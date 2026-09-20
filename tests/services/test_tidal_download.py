"""End-to-end tests for TidalDownloadService against recorded fixtures."""

import base64
import json
import shutil
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pyaes
import pytest
import requests
import soundfile as sf
from mutagen.wave import WAVE

import rekordbox_set_list_manager.utils.config as config_mod
from rekordbox_set_list_manager.services import _tidal_audio, _tidal_crypto
from rekordbox_set_list_manager.services.tidal_download import (
    DownloadErrorKind,
    TidalDownloadError,
    TidalDownloadService,
)

_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "tidal"


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path, monkeypatch):
    """Redirect config to a per-test dir and seed dummy client credentials.

    ``TidalDownloadService.__init__`` applies the client credentials
    defensively, so the isolated config seeds a fake pair; ambient
    environment variables are dropped so the config values win.
    """
    config_dir = tmp_path / "config"
    monkeypatch.setattr(config_mod, "_CONFIG_DIR", config_dir)
    monkeypatch.setattr(config_mod, "_CONFIG_PATH", config_dir / "config.json")
    monkeypatch.setattr(config_mod, "_cache", None)
    monkeypatch.delenv("SET_MANAGER_TIDAL_CLIENT_ID", raising=False)
    monkeypatch.delenv("SET_MANAGER_TIDAL_CLIENT_SECRET", raising=False)
    config_mod.set_value("tidal_client_id", "test-client-id")
    config_mod.set_value("tidal_client_secret", "test-client-secret")


def _load_fixture(name):
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


def _flac_payload():
    return (_FIXTURES / "payload_sine.flac").read_bytes()


def _mpd_bodies():
    """Init segment (ftyp at byte 4) plus four media segment bodies."""
    return [
        b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x00isomiso2",
        b"MEDIA-SEGMENT-0",
        b"MEDIA-SEGMENT-1",
        b"MEDIA-SEGMENT-2",
        b"MEDIA-SEGMENT-3",
    ]


class _FakeResp:
    """Minimal ``requests.Response`` stand-in for queued-body fakes."""

    def __init__(self, status_code=200, body=b"", headers=None):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}

    def iter_content(self, chunk_size):
        for index in range(0, len(self._body), chunk_size):
            yield self._body[index : index + chunk_size]

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")


class _FakeHttp:
    """HTTP stand-in returning one queued ``_FakeResp`` per GET."""

    def __init__(self, bodies=()):
        self._bodies = [_FakeResp(200, body=b) for b in bodies]
        self.calls = []

    def get(self, url, stream=False, timeout=None):
        self.calls.append(url)
        if self._bodies:
            return self._bodies.pop(0)
        return _FakeResp(200, b"")


class _FakeSession:
    """tidalapi Session stand-in serving a fixture playback-info payload."""

    def __init__(self, raw, tidal_track=None):
        self.config = SimpleNamespace(quality=None, client_id=None, client_secret=None)
        self._raw = raw
        self._tidal_track = tidal_track
        self.request_calls = []
        self.request = SimpleNamespace(request=self._request)

    def _request(self, method, path, params=None):
        self.request_calls.append((method, path, params))
        return SimpleNamespace(json=lambda: self._raw)

    def track(self, track_id=None, with_album=False):
        return self._tidal_track


def _fake_tidal_track(
    title="Test Title",
    artist="Test Artist",
    album="Test Album",
    track_num=1,
    duration=1,
):
    return SimpleNamespace(
        id=12345678,
        title=title,
        artist=SimpleNamespace(name=artist),
        album=SimpleNamespace(
            id=42,
            name=album,
            release_date=None,
            image=lambda _dim=640: None,
        ),
        track_num=track_num,
        isrc="USABC1234567",
        bpm=None,
        duration=duration,
        stream_start_date=None,
    )


def _make_service(
    tmp_path,
    fixture_name,
    bodies=(),
    tidal_track=None,
    **service_kwargs,
):
    """Build a service wired to fakes; returns (service, session, http)."""
    session = _FakeSession(
        _load_fixture(fixture_name),
        tidal_track if tidal_track is not None else _fake_tidal_track(),
    )
    http = _FakeHttp(bodies)
    service_kwargs.setdefault("download_dir", tmp_path / "downloads")
    service = TidalDownloadService(session, http=http, **service_kwargs)
    return service, session, http


def _write_real_wav(dest_part, seconds=1.0):
    """soundfile-generated WAV used by patched decode fakes."""
    frames = int(44100 * seconds)
    t = np.linspace(0.0, seconds, frames, endpoint=False)
    sf.write(str(dest_part), np.stack([t, t], axis=1), 44100, format="WAV", subtype="PCM_16")


# ---------------------------------------------------------------------------
# Happy path — clear BTS FLAC → tagged WAV
# ---------------------------------------------------------------------------


def test_download_track_clear_bts_end_to_end(tmp_path):
    service, _session, http = _make_service(
        tmp_path, "playback_bts_flac_clear.json", bodies=[_flac_payload()]
    )
    progress = []

    result = service.download_track("12345678", progress_cb=progress.append)

    download_dir = (tmp_path / "downloads").resolve()
    assert result.filepath.suffix == ".wav"
    assert result.filepath.is_relative_to(download_dir)
    assert result.filepath == download_dir / "Test Artist" / "Test Album" / "01 - Test Title.wav"
    head = result.filepath.open("rb").read(12)
    assert head[:4] == b"RIFF"
    assert head[8:12] == b"WAVE"
    tags = WAVE(result.filepath).tags
    assert tags is not None
    assert "TIT2" in tags
    assert "TPE1" in tags
    assert "TXXX:TIDAL_QUALITY" in tags
    assert tags["TXXX:TIDAL_QUALITY"].text == ["LOSSLESS"]
    assert result.served_quality == "LOSSLESS"
    assert result.requested_quality == "HI_RES_LOSSLESS"
    assert result.downgraded is False
    assert result.duration_s == pytest.approx(1.0)
    assert result.skipped_existing is False
    assert WAVE(result.filepath).info.length == pytest.approx(1.0, abs=0.1)
    assert len(http.calls) == 1
    assert progress == sorted(progress)
    assert progress[-1] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Preview + quality floor — rejected before any segment GET
# ---------------------------------------------------------------------------


def test_download_track_preview_rejected_before_segment_gets(tmp_path):
    service, _session, http = _make_service(tmp_path, "playback_preview.json")

    with pytest.raises(TidalDownloadError) as excinfo:
        service.download_track("12345678")

    assert excinfo.value.kind == DownloadErrorKind.PREVIEW
    assert "FULL_REQUIRES_HIGHER_ACCESS_TIER" in str(excinfo.value)
    assert http.calls == []


def test_download_track_below_floor_rejected_before_segment_gets(tmp_path):
    service, _session, http = _make_service(tmp_path, "playback_low_quality.json")

    with pytest.raises(TidalDownloadError) as excinfo:
        service.download_track("12345678")

    assert excinfo.value.kind == DownloadErrorKind.QUALITY_CAP
    assert http.calls == []


def test_download_track_missing_quality_rejected(tmp_path):
    raw = _load_fixture("playback_bts_flac_clear.json")
    raw.pop("audioQuality")
    session = _FakeSession(raw, _fake_tidal_track())
    http = _FakeHttp([_flac_payload()])
    service = TidalDownloadService(session, download_dir=tmp_path / "downloads", http=http)

    with pytest.raises(TidalDownloadError) as excinfo:
        service.download_track("12345678")

    assert excinfo.value.kind == DownloadErrorKind.QUALITY_CAP
    assert http.calls == []


def test_download_track_min_quality_any_delivers_downgraded(tmp_path):
    service, _session, _http = _make_service(
        tmp_path,
        "playback_low_quality.json",
        bodies=[_flac_payload()],
        min_quality="any",
    )

    result = service.download_track("12345678")

    assert result.downgraded is True
    assert result.served_quality == "HIGH"
    assert result.filepath.exists()


# ---------------------------------------------------------------------------
# Skip-if-existing — the templated path is the dedup key
# ---------------------------------------------------------------------------


def test_download_track_skips_existing_without_new_segment_gets(tmp_path):
    service, _session, http = _make_service(
        tmp_path, "playback_bts_flac_clear.json", bodies=[_flac_payload()]
    )
    first = service.download_track("12345678")
    calls_after_first = len(http.calls)

    second = service.download_track("12345678")

    assert second.skipped_existing is True
    assert second.filepath == first.filepath
    assert len(http.calls) == calls_after_first


# ---------------------------------------------------------------------------
# MPD manifest — init segment first, media order preserved
# ---------------------------------------------------------------------------


def test_download_track_mpd_fetches_urls_in_plan_order(tmp_path, monkeypatch):
    bodies = _mpd_bodies()
    captured = []

    def _record_decode(payload, container, dest_part, bit_depth, **_kwargs):
        captured.append((container, payload))
        _write_real_wav(dest_part)

    monkeypatch.setattr(_tidal_audio, "decode_to_wav", _record_decode)
    service, _session, http = _make_service(tmp_path, "playback_mpd_dash.json", bodies=bodies)

    result = service.download_track("12345678")

    assert len(http.calls) == 5
    assert http.calls[0].endswith("init.mp4")
    assert [u.rsplit("-", 1)[-1] for u in http.calls[1:]] == [
        "0.m4a",
        "1.m4a",
        "2.m4a",
        "3.m4a",
    ]
    # Concatenation order == plan order: init + media segments.
    assert captured[0][0] == "mp4"
    assert captured[0][1] == b"".join(bodies)
    assert result.filepath.exists()


# ---------------------------------------------------------------------------
# Validation + cancellation
# ---------------------------------------------------------------------------


def test_download_track_bad_payload_magic_leaves_no_partial_files(tmp_path):
    service, _session, _http = _make_service(
        tmp_path, "playback_bts_flac_clear.json", bodies=[b"NOT-AUDIO-GARBAGE"]
    )

    with pytest.raises(TidalDownloadError) as excinfo:
        service.download_track("12345678")

    assert excinfo.value.kind == DownloadErrorKind.VALIDATION
    download_dir = tmp_path / "downloads"
    assert list(download_dir.rglob("*.wav")) == []
    assert list(download_dir.rglob("*.part")) == []
    assert list(download_dir.rglob("*.seg*")) == []


def test_download_track_pre_cancelled_leaves_nothing(tmp_path):
    service, _session, _http = _make_service(
        tmp_path, "playback_bts_flac_clear.json", bodies=[_flac_payload()]
    )
    cancel = threading.Event()
    cancel.set()

    with pytest.raises(TidalDownloadError) as excinfo:
        service.download_track("12345678", cancel_event=cancel)

    assert excinfo.value.kind == DownloadErrorKind.CANCELLED
    download_dir = tmp_path / "downloads"
    assert not download_dir.exists() or list(download_dir.rglob("*")) == []


# ---------------------------------------------------------------------------
# Config keys + template sanitization
# ---------------------------------------------------------------------------


def test_download_track_respects_config_keys(tmp_path):
    download_dir = tmp_path / "from-config"
    config_mod.set_value("download_dir", str(download_dir))
    config_mod.set_value("download_path_template", "{artist} - {title}")
    config_mod.set_value("tidal_download_min_quality", "any")
    session = _FakeSession(_load_fixture("playback_low_quality.json"), _fake_tidal_track())
    http = _FakeHttp([_flac_payload()])
    service = TidalDownloadService(session, http=http)

    result = service.download_track("12345678")

    assert result.filepath == download_dir.resolve() / "Test Artist - Test Title.wav"
    assert result.downgraded is True


def test_render_path_sanitizes_unsafe_characters(tmp_path):
    download_dir = tmp_path / "downloads"
    service, _session, _http = _make_service(
        tmp_path,
        "playback_bts_flac_clear.json",
        bodies=[_flac_payload()],
        tidal_track=_fake_tidal_track(artist="AC/DC", title="Back: In Black?"),
    )

    result = service.download_track("12345678")

    assert "AC_DC" in result.filepath.parts
    assert result.filepath.name == "01 - Back_ In Black_.wav"
    assert result.filepath.is_relative_to(download_dir.resolve())


def test_render_path_traversal_title_stays_inside_root(tmp_path):
    download_dir = tmp_path / "downloads"
    service, _session, _http = _make_service(
        tmp_path,
        "playback_bts_flac_clear.json",
        bodies=[_flac_payload()],
        tidal_track=_fake_tidal_track(title="../../etc/evil"),
    )

    result = service.download_track("12345678")

    assert result.filepath.is_relative_to(download_dir.resolve())
    assert result.filepath.exists()


def test_render_path_unknown_placeholder_falls_back_to_default(tmp_path):
    download_dir = tmp_path / "downloads"
    service, _session, _http = _make_service(
        tmp_path,
        "playback_bts_flac_clear.json",
        bodies=[_flac_payload()],
        template="{artist}/{bogus}/{title}",
    )

    result = service.download_track("12345678")

    assert result.filepath == (
        download_dir.resolve() / "Test Artist" / "Test Album" / "01 - Test Title.wav"
    )


# ---------------------------------------------------------------------------
# MP4 container — ffmpeg route
# ---------------------------------------------------------------------------


def test_download_track_mp4_without_ffmpeg_raises_decode(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    service, _session, _http = _make_service(
        tmp_path, "playback_mpd_dash.json", bodies=_mpd_bodies()
    )

    with pytest.raises(TidalDownloadError) as excinfo:
        service.download_track("12345678")

    assert excinfo.value.kind == DownloadErrorKind.DECODE
    assert "install ffmpeg or restore soundfile" in str(excinfo.value)
    assert list((tmp_path / "downloads").rglob("*.wav")) == []


def test_download_track_mp4_with_ffmpeg_decodes_end_to_end(tmp_path, monkeypatch):
    """Mocked ffmpeg writes a real WAV so the mp4 route is proven end-to-end."""
    monkeypatch.setattr(shutil, "which", lambda _name: "/fake/bin/ffmpeg")

    def _fake_run(cmd, check, timeout):
        _write_real_wav(Path(cmd[-1]))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", _fake_run)
    service, _session, _http = _make_service(
        tmp_path, "playback_mpd_dash.json", bodies=_mpd_bodies()
    )

    result = service.download_track("12345678")

    assert result.filepath.exists()
    head = result.filepath.open("rb").read(12)
    assert head[:4] == b"RIFF"
    assert head[8:12] == b"WAVE"
    tags = WAVE(result.filepath).tags
    assert tags is not None
    assert "TXXX:TIDAL_QUALITY" in tags


# ---------------------------------------------------------------------------
# Encrypted manifests — real decrypt through the pipeline
# ---------------------------------------------------------------------------


def _encrypted_payload():
    return (_FIXTURES / "payload_encrypted.bin").read_bytes()


def test_download_track_encrypted_bts_end_to_end(tmp_path):
    """``encryptionType: OLD`` + ``keyId`` → decrypt → decode → tagged WAV."""
    service, _session, http = _make_service(
        tmp_path, "playback_bts_flac_encrypted.json", bodies=[_encrypted_payload()]
    )

    result = service.download_track("12345678")

    assert result.filepath.suffix == ".wav"
    assert result.filepath.is_relative_to((tmp_path / "downloads").resolve())
    head = result.filepath.open("rb").read(12)
    assert head[:4] == b"RIFF"
    assert head[8:12] == b"WAVE"
    tags = WAVE(result.filepath).tags
    assert tags is not None
    assert tags["TXXX:TIDAL_QUALITY"].text == ["LOSSLESS"]
    assert result.served_quality == "LOSSLESS"
    assert result.downgraded is False
    assert WAVE(result.filepath).info.length == pytest.approx(1.0, abs=0.1)
    assert len(http.calls) == 1


def test_download_track_encrypted_corrupt_payload_fails_classified(tmp_path):
    """CTR is unauthenticated: ciphertext corrupted inside the magic-byte
    region decrypts to garbage that the payload-magic check rejects —
    nothing reaches the library."""
    corrupted = bytearray(_encrypted_payload())
    corrupted[0] ^= 0xFF
    service, _session, _http = _make_service(
        tmp_path, "playback_bts_flac_encrypted.json", bodies=[bytes(corrupted)]
    )

    with pytest.raises(TidalDownloadError) as excinfo:
        service.download_track("12345678")

    assert excinfo.value.kind in (DownloadErrorKind.DECRYPT, DownloadErrorKind.VALIDATION)
    download_dir = tmp_path / "downloads"
    assert list(download_dir.rglob("*.wav")) == []
    assert list(download_dir.rglob("*.part")) == []
    assert list(download_dir.rglob("*.seg*")) == []


def test_download_track_wrong_key_fails_validation(tmp_path):
    """A well-formed ``keyId`` wrapping the wrong key decrypts cleanly but
    produces non-FLAC bytes — rejected as VALIDATION, never a corrupt file."""
    raw = _load_fixture("playback_bts_flac_encrypted.json")
    manifest = json.loads(base64.b64decode(raw["manifest"]))
    wrong_key = bytes(reversed(bytes.fromhex("00112233445566778899aabbccddeeff")))
    encrypter = pyaes.Encrypter(
        pyaes.AESModeOfOperationCBC(_tidal_crypto._MASTER_KEY, iv=bytes(16))
    )
    token_ct = encrypter.feed(wrong_key + bytes.fromhex("0a1b2c3d4e5f6078")) + encrypter.feed()
    manifest["keyId"] = base64.b64encode(bytes(16) + token_ct).decode()
    raw["manifest"] = base64.b64encode(json.dumps(manifest).encode()).decode()
    session = _FakeSession(raw, _fake_tidal_track())
    http = _FakeHttp([_encrypted_payload()])
    service = TidalDownloadService(session, download_dir=tmp_path / "downloads", http=http)

    with pytest.raises(TidalDownloadError) as excinfo:
        service.download_track("12345678")

    assert excinfo.value.kind == DownloadErrorKind.VALIDATION
    download_dir = tmp_path / "downloads"
    assert list(download_dir.rglob("*.wav")) == []
    assert list(download_dir.rglob("*.part")) == []


def test_download_track_malformed_keyid_fails_decrypt(tmp_path):
    """A ``keyId`` that is not iv + whole ciphertext blocks fails as DECRYPT."""
    raw = _load_fixture("playback_bts_flac_clear.json")
    manifest = json.loads(base64.b64decode(raw["manifest"]))
    manifest["encryptionType"] = "OLD"
    manifest["keyId"] = base64.b64encode(b"fixture-key-id-payload-32-bytes!!").decode()
    raw["manifest"] = base64.b64encode(json.dumps(manifest).encode()).decode()
    session = _FakeSession(raw, _fake_tidal_track())
    http = _FakeHttp([_flac_payload()])
    service = TidalDownloadService(session, download_dir=tmp_path / "downloads", http=http)

    with pytest.raises(TidalDownloadError) as excinfo:
        service.download_track("12345678")

    assert excinfo.value.kind == DownloadErrorKind.DECRYPT
    download_dir = tmp_path / "downloads"
    assert list(download_dir.rglob("*.wav")) == []
    assert list(download_dir.rglob("*.part")) == []
    assert list(download_dir.rglob("*.seg*")) == []
