"""Tests for the Tidal download adapter (creds, rate gate, fetch, playback-info)."""

import json
import threading
from itertools import pairwise
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
    """HTTP stand-in returning queued responses or raising queued errors."""

    def __init__(self, outcomes=()):
        self._outcomes = list(outcomes)
        self.calls = []
        self.redirects = []

    def get(self, url, stream=False, timeout=None, allow_redirects=True):
        self.calls.append((url, stream, timeout))
        self.redirects.append(allow_redirects)
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


def test_apply_client_credentials_falls_back_to_embedded_pair():
    """No configured pair keeps the session's embedded credentials untouched."""
    session = _FakeSession()
    session.config.client_id = "embedded-id"
    session.config.client_secret = "embedded-secret"  # noqa: S105

    fingerprint = adapter.apply_client_credentials(session)

    assert session.config.client_id == "embedded-id"
    assert session.config.client_secret == "embedded-secret"  # noqa: S105
    assert fingerprint == adapter.client_fingerprint("embedded-id")


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


def test_rate_gate_backoff_caps_retry_after_hint():
    """A huge server hint is clamped — the sleep runs under the global gate
    lock, so an uncapped Retry-After would stall every worker."""
    clock = _FakeClock()
    gate = adapter.RateGate(
        interval=0.25, sleep=clock.sleep, monotonic=clock.monotonic, rng=lambda: 0.0
    )

    delay = gate.backoff(0, hint=3600.0)

    assert delay == 300.0
    assert clock.sleeps == [300.0]


def test_rate_gate_backoff_grows_exponentially():
    clock = _FakeClock()
    gate = adapter.RateGate(
        interval=0.25, sleep=clock.sleep, monotonic=clock.monotonic, rng=lambda: 0.0
    )

    gate.backoff(0)
    gate.backoff(1)
    gate.backoff(2)

    assert clock.sleeps == [0.25, 0.5, 1.0]


class _BarrierClock:
    """Fake clock proving concurrent ``acquire`` calls never overlap sleeps.

    ``sleep`` rendezvous on a 2-party barrier: when a second caller enters
    ``sleep`` while another is still inside (no gate lock), the barrier
    completes and ``overlapped`` flips.  A lock-serialized gate leaves every
    barrier alone until it times out, so the flag stays ``False``.

    ``monotonic`` records every read — a serialized gate produces contiguous
    ``[elapsed, release]`` pairs per acquire, so ``reads[1::2]`` are the
    release stamps and must be spaced >= interval.
    """

    def __init__(self, start=100.0):
        self.now = start
        self.reads = []
        self.sleeps = []
        self.overlapped = False
        self._barrier = threading.Barrier(2)

    def monotonic(self):
        self.reads.append(self.now)
        return self.now

    def sleep(self, seconds):
        try:
            self._barrier.wait(timeout=0.05)
        except threading.BrokenBarrierError:
            pass
        else:
            self.overlapped = True
        self.sleeps.append(seconds)
        self.now += seconds


def test_rate_gate_serializes_concurrent_acquires():
    """The gate's critical section is lock-guarded: releases stay globally
    spaced even with several workers calling acquire() at the same time."""
    clock = _BarrierClock()
    gate = adapter.RateGate(
        interval=0.25, sleep=clock.sleep, monotonic=clock.monotonic, rng=lambda: 0.0
    )
    gate._last_release = clock.monotonic()  # every caller starts "due"
    clock.reads.clear()

    threads = [threading.Thread(target=gate.acquire) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive()

    assert clock.overlapped is False
    assert len(clock.reads) == 2 * len(threads)
    release_stamps = clock.reads[1::2]
    assert release_stamps == sorted(release_stamps)
    for earlier, later in pairwise(release_stamps):
        assert later - earlier >= 0.25


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


def test_fetch_bytes_401_raises_auth_error():
    http = _FakeHttp([_FakeResp(401)])

    # TidalAuthError subclasses PermissionError so existing callers still
    # catch it — but the distinct type keeps local PermissionError
    # (filesystem denials) out of the AUTH classification.
    with pytest.raises(adapter.TidalAuthError):
        adapter.fetch_bytes(_SEGMENT_URL, http=http, gate=_fake_gate())
    assert len(http.calls) == 1


def test_fetch_bytes_403_raises_auth_error():
    http = _FakeHttp([_FakeResp(403)])

    with pytest.raises(adapter.TidalAuthError):
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


def test_fetch_bytes_closes_streamed_responses_on_every_exit():
    """A streamed body holds its connection until closed — retry and error
    paths must release it or the pool starves under 429/5xx churn."""
    rate_limited, server_error, refused = _FakeResp(429), _FakeResp(500), _FakeResp(401)
    http = _FakeHttp([rate_limited, server_error, refused])

    with pytest.raises(PermissionError):
        adapter.fetch_bytes(_SEGMENT_URL, http=http, gate=_fake_gate())

    assert rate_limited.closed
    assert server_error.closed
    assert refused.closed


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


def test_fetch_bytes_refuses_redirect_off_allowlist():
    """requests follows redirects to ANY host — a manifest URL on
    ``*.tidal.com`` redirecting externally would defeat the allowlist, so
    redirects are disabled and a 3xx is a fetch failure."""
    http = _FakeHttp([_FakeResp(302, headers={"Location": "https://evil.example.com/seg"})])

    with pytest.raises(ValueError, match="Refusing redirect"):
        adapter.fetch_bytes(_SEGMENT_URL, http=http, gate=_fake_gate())

    assert len(http.calls) == 1
    assert http.redirects == [False]


def test_fetch_bytes_non_retriable_4xx_fails_without_retry():
    """A 404/410 is permanent — burning all five retry attempts and the
    gate-backed sleeps would only delay an inevitable failure."""
    http = _FakeHttp([_FakeResp(404)] * 5)

    with pytest.raises(ValueError, match="non-retriable"):
        adapter.fetch_bytes(_SEGMENT_URL, http=http, gate=_fake_gate())

    assert len(http.calls) == 1


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


def test_fetch_bytes_cancel_checked_before_each_retry():
    """A cancel raised between attempts must stop the retry loop — a worker
    draining after a sibling failure or a user cancel should not burn the
    remaining attempts."""
    cancel = threading.Event()

    class _CancellingHttp(_FakeHttp):
        def get(self, url, stream=False, timeout=None, allow_redirects=True):
            resp = super().get(url, stream=stream, timeout=timeout, allow_redirects=allow_redirects)
            cancel.set()
            return resp

    http = _CancellingHttp([_FakeResp(500), _FakeResp(500), _FakeResp(500)])

    with pytest.raises(InterruptedError):
        adapter.fetch_bytes(_SEGMENT_URL, http=http, gate=_fake_gate(), cancel=cancel)
    assert len(http.calls) == 1


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


def test_fetch_cover_propagates_cancel():
    """A pending cancel aborts the cover fetch — the best-effort failure
    handling must not swallow ``InterruptedError``."""
    cancel = threading.Event()
    cancel.set()
    http = _FakeHttp([_FakeResp(200, b"img")])

    with pytest.raises(InterruptedError):
        adapter.fetch_cover(
            "https://resources.tidal.com/images/cover.jpg",
            http=http,
            gate=_fake_gate(),
            cancel=cancel,
        )
