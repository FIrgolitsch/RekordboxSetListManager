"""Tests for the spotipy adapter (private _get wrapper + secure cache handler)."""

import stat
import sys

import pytest

from rekordbox_set_list_manager.services._spotify_adapter import (
    SecureCacheFileHandler,
    fetch_playlist_items,
)


class _FakeSp:
    """Fake spotipy client recording ``_get`` calls and replaying pages."""

    def __init__(self, pages):
        self._pages = list(pages)
        self.calls = []

    def _get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self._pages.pop(0)


def _track(track_id="id1", name="Song", artists=("Artist",)):
    return {
        "id": track_id,
        "name": name,
        "type": "track",
        "duration_ms": 240000,
        "artists": [{"name": a} for a in artists],
        "external_ids": {"isrc": "USABC1234567"},
    }


# ---------------------------------------------------------------------------
# fetch_playlist_items — request shape
# ---------------------------------------------------------------------------


def test_fetch_calls_get_with_exact_url_and_kwargs():
    sp = _FakeSp([{"items": [{"item": _track()}], "next": None}])

    fetch_playlist_items(sp, "abc123")

    assert sp.calls == [
        ("playlists/abc123/items", {"limit": 100, "offset": 0, "market": "from_token"})
    ]


def test_fetch_paginates_via_next():
    sp = _FakeSp(
        [
            {"items": [{"item": _track("a")}], "next": "page2"},
            {"items": [{"item": _track("b")}], "next": None},
        ]
    )

    tracks, skipped = fetch_playlist_items(sp, "pl")

    assert [c[1]["offset"] for c in sp.calls] == [0, 100]
    assert [t["id"] for t in tracks] == ["a", "b"]
    assert skipped == []


def test_fetch_returns_empty_on_empty_first_page():
    sp = _FakeSp([{"items": [], "next": None}])

    tracks, skipped = fetch_playlist_items(sp, "pl")

    assert tracks == []
    assert skipped == []
    assert len(sp.calls) == 1


# ---------------------------------------------------------------------------
# fetch_playlist_items — skip handling
# ---------------------------------------------------------------------------


def test_fetch_skips_null_and_episode_items():
    sp = _FakeSp(
        [
            {
                "items": [
                    None,
                    {"item": None},
                    {"item": {"id": "ep1", "name": "Podcast Ep", "type": "episode"}},
                    {"item": _track("t1")},
                ],
                "next": None,
            }
        ]
    )

    tracks, skipped = fetch_playlist_items(sp, "pl")

    assert [t["id"] for t in tracks] == ["t1"]
    assert len(skipped) == 3
    assert all("not a track" in s.reason for s in skipped)
    # Episode items keep their name as the label
    assert skipped[2].label == "Podcast Ep"


def test_fetch_skips_nameless_tracks():
    nameless = _track("noid", name="")
    sp = _FakeSp([{"items": [{"item": nameless}, {"item": _track("ok")}], "next": None}])

    tracks, skipped = fetch_playlist_items(sp, "pl")

    assert [t["id"] for t in tracks] == ["ok"]
    assert len(skipped) == 1
    assert skipped[0].label == "noid"
    assert "no track name" in skipped[0].reason


def test_fetch_supports_legacy_track_key():
    sp = _FakeSp([{"items": [{"track": _track("legacy")}], "next": None}])

    tracks, skipped = fetch_playlist_items(sp, "pl")

    assert [t["id"] for t in tracks] == ["legacy"]
    assert skipped == []


# ---------------------------------------------------------------------------
# SecureCacheFileHandler
# ---------------------------------------------------------------------------


def _mode(path):
    return stat.S_IMODE(path.stat().st_mode)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file permissions")
def test_save_token_chmods_cache_to_owner_only(tmp_path):
    cache = tmp_path / ".spotify_cache"
    handler = SecureCacheFileHandler(cache_path=str(cache))

    handler.save_token_to_cache({"access_token": "tok", "expires_at": 0})

    assert _mode(cache) == 0o600


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file permissions")
def test_get_cached_token_chmods_preexisting_file(tmp_path):
    cache = tmp_path / ".spotify_cache"
    cache.write_text('{"access_token": "tok"}')
    cache.chmod(0o644)
    handler = SecureCacheFileHandler(cache_path=str(cache))

    token = handler.get_cached_token()

    assert token == {"access_token": "tok"}
    assert _mode(cache) == 0o600


def test_get_cached_token_returns_none_without_file(tmp_path):
    handler = SecureCacheFileHandler(cache_path=str(tmp_path / ".spotify_cache"))

    assert handler.get_cached_token() is None
