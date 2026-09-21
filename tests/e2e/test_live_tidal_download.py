"""Live Tidal download smoke test — one real track end-to-end.

Runs the production ``DownloadController`` (real ``QThread`` + worker) and
the real ``TidalDownloadService`` against the operator's own Tidal session.

Gated behind ``SET_MANAGER_LIVE_TIDAL_TEST`` — set it to a truthy value
(``1``, ``true``, ``yes``) to enable, plus ``SET_MANAGER_LIVE_TIDAL_TRACK_ID``
naming the Tidal track to fetch. Without the gate the test skips, so the
default suite never touches the network.
"""

import os
import tomllib
from pathlib import Path

import pytest

from rekordbox_set_list_manager.controllers.download_controller import DownloadController
from rekordbox_set_list_manager.services.tidal_service import TidalService
from rekordbox_set_list_manager.utils import config

_ENV_GATE = "SET_MANAGER_LIVE_TIDAL_TEST"
_ENV_TRACK_ID = "SET_MANAGER_LIVE_TIDAL_TRACK_ID"

_LIVE_ENABLED = os.environ.get(_ENV_GATE, "").strip().lower() in {"1", "true", "yes"}


def test_live_tidal_marker_registered():
    """``live_tidal`` stays registered so ``-m live_tidal`` selection is deliberate."""
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    markers = tomllib.loads(pyproject.read_text(encoding="utf-8"))["tool"]["pytest"][
        "ini_options"
    ]["markers"]
    assert any("live_tidal" in marker for marker in markers)


@pytest.mark.skipif(
    not _LIVE_ENABLED, reason=f"set {_ENV_GATE}=1 to run the live Tidal smoke test"
)
def test_live_tidal_download_end_to_end(qtbot, tmp_path, monkeypatch):
    """One real track through the real controller, thread, and Tidal service."""
    track_id = os.environ.get(_ENV_TRACK_ID, "").strip()
    if not track_id:
        pytest.skip(f"{_ENV_TRACK_ID} does not name a track to download")

    tidal = TidalService()
    if tidal.try_silent_authenticate() is None:
        pytest.skip("No cached Tidal session — sign in through the app first")

    # Aim the configured library root at the test's temp dir while keeping
    # the real controller -> worker -> service path intact.
    real_get = config.get

    def _get(key, default=None):
        return str(tmp_path) if key == "download_dir" else real_get(key, default)

    monkeypatch.setattr(config, "get", _get)

    ctrl = DownloadController(tidal_service=tidal)
    finished = []
    failed = []
    ctrl.item_finished.connect(lambda _item_id, result: finished.append(result))
    ctrl.item_failed.connect(lambda _item_id, kind, msg: failed.append((kind, msg)))
    try:
        ctrl.enqueue([(track_id, "live smoke")])
        qtbot.waitUntil(lambda: bool(finished or failed), timeout=120_000)
    finally:
        # The controller is deliberately parentless here — bounded-join the
        # worker thread so it never outlives the test.
        ctrl.shutdown()

    assert not failed, f"live download failed: {failed}"
    assert len(finished) == 1
    result = finished[0]
    assert result.track_id == track_id
    assert result.filepath.is_relative_to(tmp_path)
    assert result.filepath.suffix == ".aiff"
    assert result.filepath.exists()
    assert result.filepath.stat().st_size > 0
