"""End-to-end tests for TidalDownloadService against recorded fixtures."""

import base64
import datetime
import json
import shutil
import struct
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pyaes
import pytest
import requests
import soundfile as sf
from mutagen.wave import WAVE

import rekordbox_set_list_manager.utils.config as config_mod
from rekordbox_set_list_manager.services import (
    _tidal_audio,
    _tidal_crypto,
    _tidal_download_adapter,
    tidal_download,
)
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
    monkeypatch.delenv("SET_MANAGER_TIDAL_DOWNLOAD_WORKERS", raising=False)
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


_MPD_URLS = (
    "https://audio.tidal.com/placeholder/init.mp4",
    "https://audio.tidal.com/placeholder/seg-0.m4a",
    "https://audio.tidal.com/placeholder/seg-1.m4a",
    "https://audio.tidal.com/placeholder/seg-2.m4a",
    "https://audio.tidal.com/placeholder/seg-3.m4a",
)
_BTS_SEG_URL = "https://audio.tidal.com/placeholder/seg0.flac"


def _mpd_url_bodies():
    """``{url: body}`` mapping matching the MPD fixture's expanded plan."""
    return dict(zip(_MPD_URLS, _mpd_bodies(), strict=True))


class _FakeResp:
    """Minimal ``requests.Response`` stand-in for queued-body fakes."""

    def __init__(self, status_code=200, body=b"", headers=None):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def close(self):
        self.closed = True

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

    def get(self, url, stream=False, timeout=None, allow_redirects=True):
        self.calls.append(url)
        if self._bodies:
            return self._bodies.pop(0)
        return _FakeResp(200, b"")


class _FakeHttpByUrl:
    """HTTP stand-in keyed by exact URL — parallel fetches can't share a queue.

    Values may be ``_FakeResp`` instances (reused across retries — the fake
    response is stateless) or exceptions (raised on every GET for that URL).
    ``max_inflight`` records the deepest concurrent ``get`` overlap seen.
    """

    def __init__(self, mapping):
        self._mapping = dict(mapping)
        self.calls = []
        self.calls_lock = threading.Lock()
        self.inflight = 0
        self.max_inflight = 0

    def get(self, url, stream=False, timeout=None, allow_redirects=True):
        with self.calls_lock:
            self.calls.append(url)
            self.inflight += 1
            self.max_inflight = max(self.max_inflight, self.inflight)
        try:
            outcome = self._mapping[url]
        except KeyError:
            outcome = _FakeResp(404)
        with self.calls_lock:
            self.inflight -= 1
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, bytes):
            outcome = _FakeResp(200, outcome)
        return outcome


class _ChainedResp(_FakeResp):
    """Response whose body reports when it has been fully consumed."""

    def __init__(self, body, on_consumed):
        super().__init__(200, body)
        self._on_consumed = on_consumed

    def iter_content(self, chunk_size):
        yield from super().iter_content(chunk_size)
        self._on_consumed()


class _ReverseOrderHttp:
    """Fake http forcing strictly reversed completion order.

    The GET for ``urls[i]`` returns only after the body of ``urls[i + 1]``
    was fully consumed, so the first segment always finishes last.  A merge
    honoring completion order instead of URL position would produce the
    payload reversed — this fake is what makes the ordering assertion a real
    proof rather than a coincidence of sequential fetching.

    If the successor segment never gets requested (a sequential fetcher) the
    wait bails out after a short grace period so the fake can never deadlock
    a non-parallel pipeline.
    """

    def __init__(self, url_bodies):
        self._items = list(url_bodies)  # [(url, body)] in plan order
        self._consumed = [False] * len(self._items)
        self._cond = threading.Condition()
        self.calls = []
        self.inflight = 0
        self.max_inflight = 0

    def get(self, url, stream=False, timeout=None, allow_redirects=True):
        index = next(i for i, (u, _b) in enumerate(self._items) if u == url)
        with self._cond:
            self.calls.append(url)
            self.inflight += 1
            self.max_inflight = max(self.max_inflight, self.inflight)
            nxt = index + 1
            if nxt < len(self._items):
                started = time.monotonic()
                while not self._consumed[nxt]:
                    successor_called = self._items[nxt][0] in self.calls
                    waited = time.monotonic() - started
                    if not successor_called and waited > 0.5:
                        break  # sequential fetcher — nothing to wait for
                    if waited > 10:
                        break  # safety bound; never hang the suite
                    self._cond.wait(timeout=0.1)
        return _ChainedResp(self._items[index][1], lambda i=index: self._mark(i))

    def _mark(self, index):
        with self._cond:
            self._consumed[index] = True
            self.inflight -= 1
            self._cond.notify_all()


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
    isrc="USABC1234567",
    release_date=None,
    image_url=None,
):
    return SimpleNamespace(
        id=12345678,
        title=title,
        artist=SimpleNamespace(name=artist) if artist is not None else None,
        album=(
            SimpleNamespace(
                id=42,
                name=album,
                release_date=release_date,
                image=lambda _dim=640: image_url,
            )
            if album is not None
            else None
        ),
        track_num=track_num,
        isrc=isrc,
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


def test_download_track_mpd_fetches_all_urls_and_merges_in_plan_order(tmp_path, monkeypatch):
    bodies = _mpd_bodies()
    captured = []

    def _record_decode(payload, container, dest_part, bit_depth, **_kwargs):
        captured.append((container, payload))
        _write_real_wav(dest_part)

    monkeypatch.setattr(_tidal_audio, "decode_to_wav", _record_decode)
    session = _FakeSession(_load_fixture("playback_mpd_dash.json"), _fake_tidal_track())
    http = _FakeHttpByUrl(_mpd_url_bodies())
    service = TidalDownloadService(session, download_dir=tmp_path / "downloads", http=http)

    result = service.download_track("12345678")

    # Every URL in the plan is fetched exactly once (order is worker
    # scheduling, not contract — the *merge* order is what matters).
    assert sorted(http.calls) == sorted(_MPD_URLS)
    # Concatenation order == plan order: init + media segments.
    assert captured[0][0] == "mp4"
    assert captured[0][1] == b"".join(bodies)
    assert result.filepath.exists()


def test_download_track_parallel_fetch_overlaps_and_merges_in_url_order(tmp_path, monkeypatch):
    """Bounded worker pool: fetches overlap, completion order is reversed,
    merged payload still follows manifest URL position."""
    monkeypatch.setenv("SET_MANAGER_TIDAL_DOWNLOAD_WORKERS", "5")
    captured = []

    def _record_decode(payload, container, dest_part, bit_depth, **_kwargs):
        captured.append(payload)
        _write_real_wav(dest_part)

    monkeypatch.setattr(_tidal_audio, "decode_to_wav", _record_decode)
    session = _FakeSession(_load_fixture("playback_mpd_dash.json"), _fake_tidal_track())
    http = _ReverseOrderHttp(_mpd_url_bodies().items())
    service = TidalDownloadService(session, download_dir=tmp_path / "downloads", http=http)

    result = service.download_track("12345678")

    # All five workers held an in-flight GET at once — real overlap, and the
    # held-back segment 0 always completes last.
    assert http.max_inflight == len(_MPD_URLS)
    assert captured[0] == b"".join(_mpd_bodies())
    assert result.filepath.exists()


def test_download_track_workers_kwarg_and_env_override(tmp_path, monkeypatch):
    monkeypatch.delenv("SET_MANAGER_TIDAL_DOWNLOAD_WORKERS", raising=False)
    service, _session, _http = _make_service(
        tmp_path, "playback_bts_flac_clear.json", bodies=[_flac_payload()], workers=3
    )
    assert service._workers == 3

    monkeypatch.setenv("SET_MANAGER_TIDAL_DOWNLOAD_WORKERS", "2")
    service2, _s2, _h2 = _make_service(
        tmp_path, "playback_bts_flac_clear.json", bodies=[_flac_payload()], workers=3
    )
    assert service2._workers == 2


def test_download_track_segment_failure_cleans_every_seg_part(tmp_path):
    """A failing segment aborts its siblings and leaves no .seg/.part behind."""
    bodies = _mpd_url_bodies()
    bodies[_MPD_URLS[2]] = requests.ConnectionError("segment host unreachable")
    session = _FakeSession(_load_fixture("playback_mpd_dash.json"), _fake_tidal_track())
    http = _FakeHttpByUrl(bodies)
    service = TidalDownloadService(session, download_dir=tmp_path / "downloads", http=http)

    with pytest.raises(TidalDownloadError) as excinfo:
        service.download_track("12345678")

    assert excinfo.value.kind == DownloadErrorKind.NETWORK
    download_dir = tmp_path / "downloads"
    assert list(download_dir.rglob("*.wav")) == []
    assert list(download_dir.rglob("*.part")) == []
    assert list(download_dir.rglob("*.seg*")) == []


def test_download_track_cancel_mid_fetch_leaves_no_strays(tmp_path):
    """A cancel raised while the pool is mid-flight cleans indexed parts."""
    cancel = threading.Event()

    class _CancellingHttp(_FakeHttpByUrl):
        def get(self, url, stream=False, timeout=None, allow_redirects=True):
            resp = super().get(
                url, stream=stream, timeout=timeout, allow_redirects=allow_redirects
            )
            if len(self.calls) >= 2:
                cancel.set()
            return resp

    session = _FakeSession(_load_fixture("playback_mpd_dash.json"), _fake_tidal_track())
    http = _CancellingHttp(_mpd_url_bodies())
    service = TidalDownloadService(session, download_dir=tmp_path / "downloads", http=http)

    with pytest.raises(TidalDownloadError) as excinfo:
        service.download_track("12345678", cancel_event=cancel)

    assert excinfo.value.kind == DownloadErrorKind.CANCELLED
    download_dir = tmp_path / "downloads"
    assert list(download_dir.rglob("*.wav")) == []
    assert list(download_dir.rglob("*.part")) == []
    assert list(download_dir.rglob("*.seg*")) == []


def test_classify_local_permission_error_is_io_not_auth():
    """A filesystem PermissionError must not masquerade as a Tidal auth
    failure — only the adapter's TidalAuthError means HTTP 401/403."""
    local = tidal_download._classify_download_error(PermissionError("disk denied"))
    remote = tidal_download._classify_download_error(
        _tidal_download_adapter.TidalAuthError("refused")
    )

    assert local.kind is DownloadErrorKind.IO
    assert remote.kind is DownloadErrorKind.AUTH


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


def test_decode_via_ffmpeg_forces_wav_muxer(tmp_path, monkeypatch):
    """ffmpeg infers the output muxer from the filename extension; the
    ``.wav.part`` staging name matches nothing, so the arg list must pin
    ``-f wav`` or every MP4 decode exits with an output-format error."""
    monkeypatch.setattr(shutil, "which", lambda _name: "/fake/bin/ffmpeg")
    calls = []

    def _fake_run(cmd, check, timeout):
        calls.append(list(cmd))
        _write_real_wav(Path(cmd[-1]))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", _fake_run)

    _tidal_audio.decode_to_wav(b"mp4-payload", "mp4", tmp_path / "x.wav.part", 16)

    assert len(calls) == 1
    cmd = calls[0]
    assert cmd[cmd.index("-f") + 1] == "wav"
    assert cmd[-1].endswith(".part")


def test_download_track_ffmpeg_timeout_classifies_as_decode(tmp_path, monkeypatch):
    """``subprocess.TimeoutExpired`` is a SubprocessError, not OSError —
    without an explicit catch it would fall through to NETWORK."""
    monkeypatch.setattr(shutil, "which", lambda _name: "/fake/bin/ffmpeg")

    def _timeout_run(cmd, check, timeout):
        raise subprocess.TimeoutExpired(cmd, timeout)

    monkeypatch.setattr(subprocess, "run", _timeout_run)
    service, _session, _http = _make_service(
        tmp_path, "playback_mpd_dash.json", bodies=_mpd_bodies()
    )

    with pytest.raises(TidalDownloadError) as excinfo:
        service.download_track("12345678")

    assert excinfo.value.kind == DownloadErrorKind.DECODE


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


# ---------------------------------------------------------------------------
# Dual tagging — ID3 + RIFF LIST-INFO + cover APIC
# ---------------------------------------------------------------------------

_JPEG_BYTES = b"\xff\xd8\xff\xe0" + bytes(range(64)) + b"\xff\xd9"
_COVER_URL = "https://resources.tidal.com/images/abc/640x640.jpg"


def _find_info_block(data: bytes) -> bytes | None:
    """Extract the RIFF ``LIST``/``INFO`` payload, walking top-level chunks."""
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return None
    offset = 12
    while offset + 8 <= len(data):
        cid = data[offset : offset + 4]
        (csize,) = struct.unpack_from("<I", data, offset + 4)
        if cid == b"LIST" and data[offset + 8 : offset + 12] == b"INFO":
            return data[offset + 12 : offset + 8 + csize]
        offset += 8 + csize + (csize % 2)
    return None


def test_download_track_wav_carries_riff_info_chunk(tmp_path):
    """Rekordbox reads WAV metadata from RIFF INFO — it must be dual-written
    alongside the ID3 chunk mutagen manages."""
    track = _fake_tidal_track(release_date=datetime.date(2024, 3, 7))
    service, _session, _http = _make_service(
        tmp_path,
        "playback_bts_flac_clear.json",
        bodies=[_flac_payload()],
        tidal_track=track,
    )

    result = service.download_track("12345678")

    data = result.filepath.read_bytes()
    assert b"LIST" in data
    assert b"INFO" in data
    assert b"Test Artist" in data
    info = _find_info_block(data)
    assert info is not None
    assert b"INAM" in info
    assert b"Test Title" in info
    assert b"IART" in info
    assert b"Test Artist" in info
    assert b"IPRD" in info
    assert b"Test Album" in info
    assert b"ITRK" in info
    assert b"01" in info
    assert b"ISRC" in info
    assert b"USABC1234567" in info
    assert b"ICRD" in info
    assert b"2024" in info
    assert b"None" not in info
    # The chunk must not corrupt the WAV — it still parses end to end.
    assert WAVE(result.filepath).info.length == pytest.approx(1.0, abs=0.1)


def test_download_track_info_omits_missing_fields(tmp_path):
    """Absent source fields are omitted entirely — never written as the
    literal string ``None``."""
    track = _fake_tidal_track(album=None, isrc=None)
    service, _session, _http = _make_service(
        tmp_path,
        "playback_bts_flac_clear.json",
        bodies=[_flac_payload()],
        tidal_track=track,
    )

    result = service.download_track("12345678")

    data = result.filepath.read_bytes()
    assert b"None" not in data
    info = _find_info_block(data)
    assert info is not None
    assert b"INAM" in info
    assert b"Test Title" in info
    assert b"IART" in info
    assert b"Test Artist" in info
    for absent in (b"IPRD", b"ISRC", b"ICRD"):
        assert absent not in info


def test_download_track_embeds_cover_art_as_apic(tmp_path):
    """``track.album.image(640)`` bytes land in an APIC frame."""
    session = _FakeSession(
        _load_fixture("playback_bts_flac_clear.json"),
        _fake_tidal_track(image_url=_COVER_URL),
    )
    http = _FakeHttpByUrl({_BTS_SEG_URL: _flac_payload(), _COVER_URL: _JPEG_BYTES})
    service = TidalDownloadService(session, download_dir=tmp_path / "downloads", http=http)

    result = service.download_track("12345678")

    apic = WAVE(result.filepath).tags.getall("APIC")
    assert len(apic) == 1
    assert apic[0].data == _JPEG_BYTES


def test_download_track_cover_failure_still_succeeds(tmp_path):
    """A failed cover GET is best-effort: no APIC, download still delivers."""
    session = _FakeSession(
        _load_fixture("playback_bts_flac_clear.json"),
        _fake_tidal_track(image_url=_COVER_URL),
    )
    http = _FakeHttpByUrl({_BTS_SEG_URL: _flac_payload(), _COVER_URL: _FakeResp(500)})
    service = TidalDownloadService(session, download_dir=tmp_path / "downloads", http=http)

    result = service.download_track("12345678")

    assert result.filepath.exists()
    assert WAVE(result.filepath).tags.getall("APIC") == []


def test_download_track_cancel_during_cover_fetch_fails_cancelled(tmp_path):
    """A user cancel landing while cover art is in flight must abort the
    download — the best-effort cover path cannot swallow it."""
    cancel = threading.Event()

    class _CancellingCoverHttp(_FakeHttpByUrl):
        def get(self, url, stream=False, timeout=None, allow_redirects=True):
            if "images" in url:
                cancel.set()
            return super().get(
                url, stream=stream, timeout=timeout, allow_redirects=allow_redirects
            )

    session = _FakeSession(
        _load_fixture("playback_bts_flac_clear.json"),
        _fake_tidal_track(image_url=_COVER_URL),
    )
    http = _CancellingCoverHttp({_BTS_SEG_URL: _flac_payload(), _COVER_URL: _JPEG_BYTES})
    service = TidalDownloadService(session, download_dir=tmp_path / "downloads", http=http)

    with pytest.raises(TidalDownloadError) as excinfo:
        service.download_track("12345678", cancel_event=cancel)

    assert excinfo.value.kind == DownloadErrorKind.CANCELLED
    download_dir = tmp_path / "downloads"
    assert list(download_dir.rglob("*.wav")) == []
    assert list(download_dir.rglob("*.part")) == []


# ---------------------------------------------------------------------------
# download_many — batch aggregation over classified per-track outcomes
# ---------------------------------------------------------------------------


class _FakeMultiSession:
    """Session fake keyed by track id for batch tests.

    ``raw_map`` maps track id → playback-info payload; ``track_map`` maps
    track id → tidalapi-shaped track; ``on_request`` (optional) fires inside
    the playback-info call so tests can flip a cancel event mid-batch.
    """

    def __init__(self, raw_map, track_map, on_request=None):
        self.config = SimpleNamespace(quality=None, client_id=None, client_secret=None)
        self._raw_map = raw_map
        self._track_map = track_map
        self._on_request = on_request
        self.request_calls = []
        self.request = SimpleNamespace(request=self._request)

    def _request(self, method, path, params=None):
        self.request_calls.append((method, path, params))
        track_id = path.split("/")[1]
        if self._on_request is not None:
            self._on_request(track_id)
        return SimpleNamespace(json=lambda: self._raw_map[track_id])

    def track(self, track_id=None, with_album=False):
        return self._track_map[track_id]


def _bts_raw_for(track_id):
    """BTS playback fixture cloned with a track-specific segment URL."""
    raw = _load_fixture("playback_bts_flac_clear.json")
    manifest = json.loads(base64.b64decode(raw["manifest"]))
    manifest["urls"] = [f"https://audio.tidal.com/placeholder/seg-{track_id}.flac"]
    raw["manifest"] = base64.b64encode(json.dumps(manifest).encode()).decode()
    return raw


def _seg_url(track_id):
    return f"https://audio.tidal.com/placeholder/seg-{track_id}.flac"


def _track_map_for(track_ids):
    """Distinct titles → distinct templated paths per track id."""
    return {tid: _fake_tidal_track(title=f"Title {tid}") for tid in track_ids}


def _batch_service(tmp_path, raw_map, track_ids, http_map, on_request=None, **kwargs):
    session = _FakeMultiSession(raw_map, _track_map_for(track_ids), on_request=on_request)
    http = _FakeHttpByUrl(http_map)
    return TidalDownloadService(session, download_dir=tmp_path / "downloads", http=http, **kwargs)


def test_download_many_aggregates_classified_outcomes(tmp_path):
    """ok + preview + below-floor + ok → results carry only delivered files,
    failures carry classified reasons, nothing aborts the batch."""
    service = _batch_service(
        tmp_path,
        {
            "ok1": _bts_raw_for("ok1"),
            "prev": _load_fixture("playback_preview.json"),
            "low": _load_fixture("playback_low_quality.json"),
            "ok2": _bts_raw_for("ok2"),
        },
        ("ok1", "prev", "low", "ok2"),
        {_seg_url("ok1"): _flac_payload(), _seg_url("ok2"): _flac_payload()},
    )

    batch = service.download_many(["ok1", "prev", "low", "ok2"])

    assert [r.track_id for r in batch.results] == ["ok1", "ok2"]
    assert batch.downgrades == []
    assert [f.label for f in batch.failures] == ["prev", "low"]
    assert batch.failures[0].reason.startswith("preview:")
    assert batch.failures[1].reason.startswith("quality_cap:")
    assert batch.cancelled is False
    for result in batch.results:
        assert result.filepath.exists()


def test_download_many_min_quality_any_reports_downgrade(tmp_path):
    """A delivered-but-downgraded track lands in results AND downgrades —
    never in failures."""
    service = _batch_service(
        tmp_path,
        {
            "ok": _bts_raw_for("ok"),
            "low": _load_fixture("playback_low_quality.json"),
        },
        ("ok", "low"),
        {_seg_url("ok"): _flac_payload(), _BTS_SEG_URL: _flac_payload()},
        min_quality="any",
    )

    batch = service.download_many(["ok", "low"])

    assert [r.track_id for r in batch.results] == ["ok", "low"]
    assert [r.track_id for r in batch.downgrades] == ["low"]
    assert batch.downgrades[0].downgraded is True
    assert batch.failures == []
    assert batch.cancelled is False


def test_download_many_failure_isolates_one_track(tmp_path):
    """A transport error on one track is classified and does not abort the
    rest of the batch."""
    service = _batch_service(
        tmp_path,
        {tid: _bts_raw_for(tid) for tid in ("t1", "t2", "t3")},
        ("t1", "t2", "t3"),
        {
            _seg_url("t1"): _flac_payload(),
            _seg_url("t2"): requests.ConnectionError("host unreachable"),
            _seg_url("t3"): _flac_payload(),
        },
    )

    batch = service.download_many(["t1", "t2", "t3"])

    assert [r.track_id for r in batch.results] == ["t1", "t3"]
    assert [f.label for f in batch.failures] == ["t2"]
    assert batch.failures[0].reason.startswith("network:")
    assert batch.cancelled is False


def test_download_many_cancel_stops_between_tracks(tmp_path):
    """Cancel raised mid-batch: the in-flight track fails classified, later
    tracks are never attempted, completed results stay reported."""
    cancel = threading.Event()

    def _on_request(track_id):
        if track_id == "t2":
            cancel.set()

    service = _batch_service(
        tmp_path,
        {tid: _bts_raw_for(tid) for tid in ("t1", "t2", "t3")},
        ("t1", "t2", "t3"),
        {_seg_url(tid): _flac_payload() for tid in ("t1", "t2", "t3")},
        on_request=_on_request,
    )

    batch = service.download_many(["t1", "t2", "t3"], cancel_event=cancel)

    assert batch.cancelled is True
    assert [r.track_id for r in batch.results] == ["t1"]
    assert [f.label for f in batch.failures] == ["t2"]
    assert batch.failures[0].reason.startswith("cancelled:")
    # t3's playback-info was never requested.
    assert not any("/t3/" in path for _m, path, _p in service._session.request_calls)
    download_dir = tmp_path / "downloads"
    assert list(download_dir.rglob("*.part")) == []
    assert list(download_dir.rglob("*.seg*")) == []


def test_download_many_skipped_existing_counts_as_result(tmp_path):
    """An existing file is a delivered result, not a failure."""
    service = _batch_service(
        tmp_path,
        {"t1": _bts_raw_for("t1"), "t2": _bts_raw_for("t2")},
        ("t1", "t2"),
        {_seg_url("t2"): _flac_payload()},
    )
    download_dir = tmp_path / "downloads"
    existing = download_dir / "Test Artist" / "Test Album" / "01 - Title t1.wav"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"existing")

    batch = service.download_many(["t1", "t2"])

    assert [r.track_id for r in batch.results] == ["t1", "t2"]
    assert batch.results[0].skipped_existing is True
    assert batch.results[0].filepath == existing.resolve()
    assert batch.failures == []


def test_download_many_empty_batch(tmp_path):
    session = _FakeMultiSession({}, {})
    service = TidalDownloadService(session, download_dir=tmp_path / "downloads")

    batch = service.download_many([])

    assert batch.results == []
    assert batch.downgrades == []
    assert batch.failures == []
    assert batch.cancelled is False
