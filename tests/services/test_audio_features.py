"""Tests for AudioFeaturesService using a mocked SpotifyService."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from set_manager.models.enums import TrackSource
from set_manager.models.track import Track
from set_manager.services.audio_features import AudioFeaturesError, AudioFeaturesService
from set_manager.services.spotify_service import SpotifyServiceError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _track(spotify_id: str | None = "sp-1", title: str = "Track") -> Track:
    return Track(title=title, artist="Artist", spotify_id=spotify_id, source=TrackSource.SPOTIFY)


def _mock_spotify(
    features_map: dict | None = None,
    raises: Exception | None = None,
) -> MagicMock:
    """Return a MagicMock SpotifyService pre-configured for get_audio_features."""
    sp = MagicMock()
    if raises is not None:
        sp.get_audio_features.side_effect = raises
    else:
        sp.get_audio_features.return_value = features_map or {}
    return sp


def _feat(
    track_id: str = "sp-1",
    energy: float = 0.8,
    danceability: float = 0.7,
    valence: float = 0.6,
) -> dict:
    return {"id": track_id, "energy": energy, "danceability": danceability, "valence": valence}


# ---------------------------------------------------------------------------
# Basic cases
# ---------------------------------------------------------------------------


def test_empty_list_returns_empty():
    svc = AudioFeaturesService(_mock_spotify())
    assert svc.fetch_features([]) == []


def test_track_without_spotify_id_returned_unchanged():
    track = _track(spotify_id=None)
    svc = AudioFeaturesService(_mock_spotify({}))
    result = svc.fetch_features([track])
    assert len(result) == 1
    assert result[0].energy is None
    assert result[0] is track  # same object — not a copy


def test_features_mapped_to_track_correctly():
    track = _track("sp-1")
    features = {"sp-1": _feat("sp-1", energy=0.9, danceability=0.75, valence=0.5)}
    svc = AudioFeaturesService(_mock_spotify(features))
    result = svc.fetch_features([track])
    t = result[0]
    assert t.energy == pytest.approx(0.9)
    assert t.danceability == pytest.approx(0.75)
    assert t.valence == pytest.approx(0.5)


def test_track_with_no_matching_feature_returned_unchanged():
    track = _track("sp-missing")
    svc = AudioFeaturesService(_mock_spotify({}))  # API returned nothing
    result = svc.fetch_features([track])
    assert result[0].energy is None


def test_model_copy_does_not_mutate_original():
    track = _track("sp-1")
    features = {"sp-1": _feat("sp-1")}
    svc = AudioFeaturesService(_mock_spotify(features))
    result = svc.fetch_features([track])
    assert track.energy is None           # original untouched
    assert result[0].energy is not None   # returned copy has feature set
    assert result[0] is not track         # different object


def test_mix_of_spotify_and_non_spotify_tracks():
    t_sp = _track("sp-1", "Spotify Track")
    t_no = _track(None, "No ID Track")
    features = {"sp-1": _feat("sp-1", energy=0.8)}
    svc = AudioFeaturesService(_mock_spotify(features))
    result = svc.fetch_features([t_sp, t_no])
    assert result[0].energy == pytest.approx(0.8)
    assert result[1].energy is None
    assert result[1] is t_no  # unchanged object


# ---------------------------------------------------------------------------
# Batching
# ---------------------------------------------------------------------------


def test_batches_at_100_ids():
    tracks = [_track(f"sp-{i}") for i in range(150)]
    sp = MagicMock()
    sp.get_audio_features.return_value = {}
    svc = AudioFeaturesService(sp)
    svc.fetch_features(tracks)
    assert sp.get_audio_features.call_count == 2
    first_ids = sp.get_audio_features.call_args_list[0][0][0]
    second_ids = sp.get_audio_features.call_args_list[1][0][0]
    assert len(first_ids) == 100
    assert len(second_ids) == 50


def test_exactly_100_tracks_uses_one_batch():
    tracks = [_track(f"sp-{i}") for i in range(100)]
    sp = MagicMock()
    sp.get_audio_features.return_value = {}
    svc = AudioFeaturesService(sp)
    svc.fetch_features(tracks)
    assert sp.get_audio_features.call_count == 1


def test_correct_ids_sent_in_each_batch():
    tracks = [_track(f"sp-{i}") for i in range(101)]
    sp = MagicMock()
    sp.get_audio_features.return_value = {}
    svc = AudioFeaturesService(sp)
    svc.fetch_features(tracks)
    first_ids = sp.get_audio_features.call_args_list[0][0][0]
    second_ids = sp.get_audio_features.call_args_list[1][0][0]
    assert first_ids == [f"sp-{i}" for i in range(100)]
    assert second_ids == ["sp-100"]


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_raises_audio_features_error_on_spotify_failure():
    track = _track("sp-1")
    sp = _mock_spotify(raises=SpotifyServiceError("API down"))
    svc = AudioFeaturesService(sp)
    with pytest.raises(AudioFeaturesError, match="Failed to fetch audio features"):
        svc.fetch_features([track])


def test_only_spotify_id_tracks_passed_to_api():
    t_sp = _track("sp-1")
    t_no = _track(None)
    sp = MagicMock()
    sp.get_audio_features.return_value = {}
    svc = AudioFeaturesService(sp)
    svc.fetch_features([t_sp, t_no])
    ids_sent = sp.get_audio_features.call_args[0][0]
    assert "sp-1" in ids_sent
    assert None not in ids_sent


def test_no_api_call_when_all_tracks_lack_spotify_id():
    tracks = [_track(None), _track(None)]
    sp = MagicMock()
    svc = AudioFeaturesService(sp)
    svc.fetch_features(tracks)
    sp.get_audio_features.assert_not_called()
