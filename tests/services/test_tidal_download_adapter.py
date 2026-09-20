"""Tests for the Tidal download adapter (creds, rate gate, fetch, playback-info)."""

import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
import tidalapi.media

import rekordbox_set_list_manager.utils.config as config_mod
from rekordbox_set_list_manager.services import _tidal_download_adapter as adapter

_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "tidal"

_SEGMENT_URL = "https://audio.tidal.com/placeholder/seg0.flac"


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path, monkeypatch):
    """Redirect config to a per-test dir and drop any ambient Tidal env."""
    config_dir = tmp_path / "config"
    monkeypatch.setattr(config_mod, "_CONFIG_DIR", config_dir)
    monkeypatch.setattr(config_mod, "_CONFIG_PATH", config_dir / "config.json")
    monkeypatch.setattr(config_mod, "_cache", None)
    monkeypatch.delenv("SET_MANAGER_TIDAL_CLIENT_ID", raising=False)
    monkeypatch.delenv("SET_MANAGER_TIDAL_CLIENT_SECRET", raising=False)


def _load_fixture(name):
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


class _FakeClock:
    """Deterministic monotonic clock whose ``sleep`` advances the time."""

    def __init__(self, start=100.0):
        self.now = start
        self.sleeps = []

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds

    def monotonic(self):
        return self.now


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
    """HTTP stand-in returning queued responses or raising queued errors."""

    def __init__(self, outcomes=()):
        self._outcomes = list(outcomes)
        self.calls = []

    def get(self, url, stream=False, timeout=None):
        self.calls.append((url, stream, timeout))
        outcome = self._outcomes.pop(0) if self._outcomes else _FakeResp(500)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _FakeSession:
    """tidalapi Session stand-in with a recording ``request.request``."""

    def __init__(self, raw=None, tracks=()):
        self.config = SimpleNamespace(quality=None, client_id=None, client_secret=None)
        self._raw = raw
        self._tracks = list(tracks)
        self.request_calls = []
        self.track_calls = []
        self.request = SimpleNamespace(request=self._request)

    def _request(self, method, path, params=None):
        self.request_calls.append((method, path, params))
        return SimpleNamespace(json=lambda: self._raw)

    def track(self, track_id=None, with_album=False):
        self.track_calls.append({"track_id": track_id, "with_album": with_album})
        return self._tracks.pop(0) if self._tracks else None


def _fake_gate():
    """Gate that never sleeps — retry-path tests stay instant."""
    return adapter.RateGate(interval=0.0, sleep=lambda _s: None, rng=lambda: 0.0)


# ---------------------------------------------------------------------------
# Credential resolution
# ---------------------------------------------------------------------------


def test_resolve_credentials_env_only(monkeypatch):
    monkeypatch.setenv("SET_MANAGER_TIDAL_CLIENT_ID", "env-id")
    monkeypatch.setenv("SET_MANAGER_TIDAL_CLIENT_SECRET", "env-secret")

    assert adapter.resolve_client_credentials() == ("env-id", "env-secret")


def test_resolve_credentials_config_only():
    config_mod.set_value("tidal_client_id", "cfg-id")
    config_mod.set_value("tidal_client_secret", "cfg-secret")

    assert adapter.resolve_client_credentials() == ("cfg-id", "cfg-secret")


def test_resolve_credentials_env_beats_config(monkeypatch):
    config_mod.set_value("tidal_client_id", "cfg-id")
    config_mod.set_value("tidal_client_secret", "cfg-secret")
    monkeypatch.setenv("SET_MANAGER_TIDAL_CLIENT_ID", "env-id")
    monkeypatch.setenv("SET_MANAGER_TIDAL_CLIENT_SECRET", "env-secret")

    assert adapter.resolve_client_credentials() == ("env-id", "env-secret")


def test_resolve_credentials_whitespace_env_counts_as_unset(monkeypatch):
    config_mod.set_value("tidal_client_id", "cfg-id")
    config_mod.set_value("tidal_client_secret", "cfg-secret")
    monkeypatch.setenv("SET_MANAGER_TIDAL_CLIENT_ID", "   ")
    monkeypatch.setenv("SET_MANAGER_TIDAL_CLIENT_SECRET", "\t\n")

    assert adapter.resolve_client_credentials() == ("cfg-id", "cfg-secret")


def test_resolve_credentials_missing_raises_naming_all_identifiers():
    with pytest.raises(adapter.TidalCredentialsError) as excinfo:
        adapter.resolve_client_credentials()

    message = str(excinfo.value)
    for identifier in (
        "SET_MANAGER_TIDAL_CLIENT_ID",
        "SET_MANAGER_TIDAL_CLIENT_SECRET",
        "tidal_client_id",
        "tidal_client_secret",
    ):
        assert identifier in message


def test_apply_client_credentials_sets_session_and_fingerprint():
    config_mod.set_value("tidal_client_id", "cfg-id")
    config_mod.set_value("tidal_client_secret", "cfg-secret")
    session = _FakeSession()

    fingerprint = adapter.apply_client_credentials(session)

    assert session.config.client_id == "cfg-id"
    assert session.config.client_secret == "cfg-secret"  # noqa: S105
    assert fingerprint == adapter.client_fingerprint("cfg-id")
    assert fingerprint == adapter.client_fingerprint("cfg-id")
    assert fingerprint != adapter.client_fingerprint("other-id")


# ---------------------------------------------------------------------------
# RateGate
# ---------------------------------------------------------------------------


def test_rate_gate_enforces_minimum_spacing():
    clock = _FakeClock()
    gate = adapter.RateGate(
        interval=0.25, sleep=clock.sleep, monotonic=clock.monotonic, rng=lambda: 0.0
    )

    gate.acquire()
    assert clock.sleeps == []

    gate.acquire()
    gate.acquire()
    assert clock.sleeps == [0.25, 0.25]


def test_rate_gate_backoff_honors_retry_after_hint():
    clock = _FakeClock()
    gate = adapter.RateGate(
        interval=0.25, sleep=clock.sleep, monotonic=clock.monotonic, rng=lambda: 0.0
    )

    delay = gate.backoff(0, hint=2.0)

    assert delay == 2.0
    assert clock.sleeps == [2.0]


def test_rate_gate_backoff_grows_exponentially():
    clock = _FakeClock()
    gate = adapter.RateGate(
        interval=0.25, sleep=clock.sleep, monotonic=clock.monotonic, rng=lambda: 0.0
    )

    gate.backoff(0)
    gate.backoff(1)
    gate.backoff(2)

    assert clock.sleeps == [0.25, 0.5, 1.0]


# ---------------------------------------------------------------------------
# fetch_bytes — URL guard, statuses, transport retries
# ---------------------------------------------------------------------------


def test_fetch_bytes_returns_body():
    http = _FakeHttp([_FakeResp(200, b"payload-bytes")])

    assert adapter.fetch_bytes(_SEGMENT_URL, http=http, gate=_fake_gate()) == b"payload-bytes"
    assert http.calls == [(_SEGMENT_URL, True, (10, 30))]


def test_fetch_bytes_retries_429_and_honors_retry_after():
    clock = _FakeClock()
    gate = adapter.RateGate(
        interval=0.0, sleep=clock.sleep, monotonic=clock.monotonic, rng=lambda: 0.0
    )
    http = _FakeHttp([_FakeResp(429, headers={"Retry-After": "2"}), _FakeResp(200, b"ok")])

    assert adapter.fetch_bytes(_SEGMENT_URL, http=http, gate=gate) == b"ok"
    assert len(http.calls) == 2
    assert clock.sleeps == [2.0]


def test_fetch_bytes_401_raises_permission_error():
    http = _FakeHttp([_FakeResp(401)])

    with pytest.raises(PermissionError):
        adapter.fetch_bytes(_SEGMENT_URL, http=http, gate=_fake_gate())
    assert len(http.calls) == 1


def test_fetch_bytes_403_raises_permission_error():
    http = _FakeHttp([_FakeResp(403)])

    with pytest.raises(PermissionError):
        adapter.fetch_bytes(_SEGMENT_URL, http=http, gate=_fake_gate())
    assert len(http.calls) == 1


def test_fetch_bytes_5xx_retries_then_timeout_error():
    http = _FakeHttp([_FakeResp(500)] * 5)

    with pytest.raises(TimeoutError):
        adapter.fetch_bytes(_SEGMENT_URL, http=http, gate=_fake_gate())
    assert len(http.calls) == 5


def test_fetch_bytes_429_exhaustion_raises_timeout_error():
    http = _FakeHttp([_FakeResp(429)] * 5)

    with pytest.raises(TimeoutError):
        adapter.fetch_bytes(_SEGMENT_URL, http=http, gate=_fake_gate())
    assert len(http.calls) == 5


@pytest.mark.parametrize(
    "url",
    [
        "http://audio.tidal.com/placeholder/seg0.flac",
        "https://evil.example.com/payload.flac",
        "https://nottidal.com/placeholder/seg0.flac",
        "ftp://audio.tidal.com/placeholder/seg0.flac",
    ],
)
def test_fetch_bytes_rejects_disallowed_urls_before_any_get(url):
    http = _FakeHttp([_FakeResp(200, b"nope")])

    with pytest.raises(ValueError, match="tidal"):
        adapter.fetch_bytes(url, http=http, gate=_fake_gate())
    assert http.calls == []


def test_fetch_bytes_retries_transport_error_then_succeeds():
    http = _FakeHttp([requests.ConnectionError("boom"), _FakeResp(200, b"ok")])

    assert adapter.fetch_bytes(_SEGMENT_URL, http=http, gate=_fake_gate()) == b"ok"
    assert len(http.calls) == 2


def test_fetch_bytes_persistent_transport_error_reraises_request_exception():
    """A persistent transport failure is NETWORK, never the RATE_LIMIT path."""
    http = _FakeHttp([requests.ConnectionError("boom")] * 5)

    with pytest.raises(requests.ConnectionError):
        adapter.fetch_bytes(_SEGMENT_URL, http=http, gate=_fake_gate())
    assert len(http.calls) == 5


def test_fetch_bytes_read_timeout_mid_body_retries():
    class _DyingResp(_FakeResp):
        def iter_content(self, chunk_size):
            yield b"partial"
            raise requests.ReadTimeout("mid-body abort")

    http = _FakeHttp([_DyingResp(200), _FakeResp(200, b"full")])

    assert adapter.fetch_bytes(_SEGMENT_URL, http=http, gate=_fake_gate()) == b"full"
    assert len(http.calls) == 2


def test_fetch_bytes_cancel_event_interrupts_mid_body():
    cancel = threading.Event()
    cancel.set()
    http = _FakeHttp([_FakeResp(200, b"chunk")])

    with pytest.raises(InterruptedError):
        adapter.fetch_bytes(_SEGMENT_URL, http=http, gate=_fake_gate(), cancel=cancel)


# ---------------------------------------------------------------------------
# fetch_track
# ---------------------------------------------------------------------------


def _tidal_track(album=None):
    return SimpleNamespace(
        id=123,
        title="Song",
        album=album,
        track_num=1,
        duration=1,
        isrc="USABC1234567",
        bpm=None,
        artist=SimpleNamespace(name="Artist"),
    )


def test_fetch_track_single_call_when_album_populated():
    album = SimpleNamespace(id=5, name="Album")
    session = _FakeSession(tracks=[_tidal_track(album=album)])

    adapter.fetch_track(session, "123", gate=_fake_gate())

    assert session.track_calls == [{"track_id": "123", "with_album": False}]


def test_fetch_track_retries_with_album_when_missing():
    album = SimpleNamespace(id=5, name="Album")
    session = _FakeSession(tracks=[_tidal_track(album=None), _tidal_track(album=album)])

    tidal_track = adapter.fetch_track(session, "123", gate=_fake_gate())

    assert session.track_calls == [
        {"track_id": "123", "with_album": False},
        {"track_id": "123", "with_album": True},
    ]
    assert tidal_track.album.id == 5


# ---------------------------------------------------------------------------
# get_playback_info — quality pin + raw fields preserved
# ---------------------------------------------------------------------------


def test_get_playback_info_pins_hi_res_lossless_and_preserves_raw():
    raw = _load_fixture("playback_bts_flac_clear.json")
    session = _FakeSession(raw=raw)

    info = adapter.get_playback_info(session, "123", gate=_fake_gate())

    assert session.config.quality == tidalapi.media.Quality.hi_res_lossless
    (method, path, params) = session.request_calls[0]
    assert method == "GET"
    assert path == "tracks/123/playbackinfopostpaywall"
    assert params["playbackmode"] == "STREAM"
    assert params["assetpresentation"] == "FULL"
    assert params["audioquality"] == tidalapi.media.Quality.hi_res_lossless
    assert info.stream.audio_quality == "LOSSLESS"
    assert info.presentation == "FULL"
    assert info.preview_reason is None
    assert info.raw["audioQuality"] == "LOSSLESS"


def test_get_playback_info_preserves_preview_fields():
    raw = _load_fixture("playback_preview.json")
    session = _FakeSession(raw=raw)

    info = adapter.get_playback_info(session, "123", gate=_fake_gate())

    assert info.presentation == "PREVIEW"
    assert info.preview_reason == "FULL_REQUIRES_HIGHER_ACCESS_TIER"


# ---------------------------------------------------------------------------
# build_segment_plan — container routing + URL order
# ---------------------------------------------------------------------------


def _stream_for(fixture_name):
    return tidalapi.media.Stream().parse(_load_fixture(fixture_name))


def test_build_segment_plan_bts_flac():
    plan = adapter.build_segment_plan(_stream_for("playback_bts_flac_clear.json"))

    assert plan.container == "flac"
    assert plan.encrypted is False
    assert plan.key_id is None
    assert plan.codecs == "FLAC"
    assert len(plan.urls) == 1


def test_build_segment_plan_mpd_prepends_init_segment():
    """The MPD init segment must lead the plan or the payload has no MP4 header."""
    plan = adapter.build_segment_plan(_stream_for("playback_mpd_dash.json"))

    assert plan.container == "mp4"
    assert plan.urls[0].endswith("init.mp4")
    assert [u.rsplit("-", 1)[-1] for u in plan.urls[1:]] == [
        "0.m4a",
        "1.m4a",
        "2.m4a",
        "3.m4a",
    ]


# ---------------------------------------------------------------------------
# fetch_cover — best effort
# ---------------------------------------------------------------------------


def test_fetch_cover_returns_none_for_missing_or_bad_url():
    gate = _fake_gate()
    http = _FakeHttp([_FakeResp(200, b"img")])

    assert adapter.fetch_cover(None, http=http, gate=gate) is None
    assert adapter.fetch_cover("https://evil.example.com/x.jpg", http=http, gate=gate) is None
    assert http.calls == []


def test_fetch_cover_returns_bytes_on_success():
    http = _FakeHttp([_FakeResp(200, b"jpeg-bytes")])

    assert (
        adapter.fetch_cover(
            "https://resources.tidal.com/images/cover.jpg", http=http, gate=_fake_gate()
        )
        == b"jpeg-bytes"
    )
