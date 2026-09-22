"""Live Rekordbox DB import smoke test — the real write path, end to end.

Runs the real ``RekordboxDbService`` + ``RekordboxImportController`` +
``EditController`` against a COPY of the operator's ``master.db``.

Gated behind ``SET_MANAGER_LIVE_REKORDBOX_TEST`` — set it to a truthy value
(``1``, ``true``, ``yes``) to enable, plus ``SET_MANAGER_LIVE_REKORDBOX_DB``
naming a copied ``master.db``.  Copy it (with its ``-wal``/``-shm``
siblings) while Rekordbox is closed, e.g.::

    cp ~/Library/Pioneer/rekordbox/master.db* /tmp/rb-copy/

then point ``SET_MANAGER_LIVE_REKORDBOX_DB`` at the copy — never at the
live database.  The copy must come from THIS machine's Rekordbox
installation: pyrekordbox derives the SQLCipher key locally, so a
foreign-machine copy may carry a different key lineage and fail to
unlock.  The pre-write backup of the copy lands under the real
``user_data_dir`` backups folder — acceptable, it is our own timestamped
copy of a scratch DB.

Without both gates the test skips, so the default suite never touches a
database.
"""

import os
import tomllib
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from rekordbox_set_list_manager.controllers.edit_controller import EditController
from rekordbox_set_list_manager.controllers.rekordbox_import_controller import (
    RekordboxImportController,
)
from rekordbox_set_list_manager.models.enums import MatchStatus
from rekordbox_set_list_manager.models.project import Project
from rekordbox_set_list_manager.models.track import Track
from rekordbox_set_list_manager.services.rekordbox_db import (
    RekordboxDbService,
    get_rekordbox_pid,
)
from rekordbox_set_list_manager.services.tidal_download import DownloadResult
from rekordbox_set_list_manager.utils import config

_ENV_GATE = "SET_MANAGER_LIVE_REKORDBOX_TEST"
_ENV_DB = "SET_MANAGER_LIVE_REKORDBOX_DB"

_LIVE_ENABLED = os.environ.get(_ENV_GATE, "").strip().lower() in {"1", "true", "yes"}


def test_live_rekordbox_marker_registered():
    """``live_rekordbox`` stays registered so ``-m live_rekordbox`` is deliberate."""
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    markers = tomllib.loads(pyproject.read_text(encoding="utf-8"))["tool"]["pytest"]["ini_options"][
        "markers"
    ]
    assert any("live_rekordbox" in marker for marker in markers)


@pytest.mark.live_rekordbox
@pytest.mark.skipif(
    not _LIVE_ENABLED, reason=f"set {_ENV_GATE}=1 to run the live Rekordbox import test"
)
def test_live_rekordbox_import_end_to_end(qapp, tmp_path, monkeypatch):
    """One real download through the real controller/service into the copied DB."""
    env_db = os.environ.get(_ENV_DB, "").strip()
    if not env_db:
        pytest.skip(f"{_ENV_DB} does not name a copied master.db")
    db_path = Path(env_db)
    if not db_path.exists():
        pytest.skip(f"{_ENV_DB} points at a missing file: {db_path}")

    # Operator safety: never write while Rekordbox holds the database.
    if get_rekordbox_pid():
        pytest.skip("Rekordbox is running — close it before running the live import test")

    # Aim every DB discovery + open path at the copy: the candidates seam
    # feeds get_db_path + the pre-write backup, and Rekordbox6Database is
    # wrapped so the deferred service imports open the copy's path (the
    # SQLCipher key still auto-derives locally).
    monkeypatch.setattr(
        "rekordbox_set_list_manager.services._pyrekordbox_adapter.rekordbox_db_candidates",
        lambda: [db_path],
    )
    import pyrekordbox

    real_db_cls = pyrekordbox.Rekordbox6Database
    monkeypatch.setattr(
        pyrekordbox,
        "Rekordbox6Database",
        lambda *a, **k: real_db_cls(path=db_path),
    )

    # The live run exercises the db ingest path regardless of the
    # operator's configured import mode.
    real_get = config.get

    def _get(key, default=None):
        return "db" if key == "rekordbox_import_mode" else real_get(key, default)

    monkeypatch.setattr(config, "get", _get)

    track = Track(
        title="Set Manager Live Import Test",
        artist="Set Manager",
        tidal_id="live-1",
        isrc="ZZABC0000001",
        match_status=MatchStatus.UNMATCHED,
    )
    project = Project(name="live-import")
    project.add_track(track)

    pc = MagicMock()
    pc.project = project
    pc.restore.side_effect = lambda p: setattr(pc, "project", p)
    edit_ctrl = EditController(pc)

    # Any non-empty bytes suffice — add_content only stats the file and
    # maps the .aiff suffix to its FileType.
    downloaded = tmp_path / "live.aiff"
    downloaded.write_bytes(b"live import test payload")

    service = RekordboxDbService()
    ctrl = RekordboxImportController(pc, edit_ctrl, db_service=service)

    result = ctrl.import_downloaded(
        [
            DownloadResult(
                filepath=downloaded,
                track_id="live-1",
                served_quality="LOSSLESS",
                requested_quality="HI_RES_LOSSLESS",
                downgraded=False,
                codec="flac",
                duration_s=180.0,
            )
        ]
    )

    assert result.inserted == 1
    assert result.matched == 1
    assert result.needs_reload_tag is True
    updated = project.get_track(track.id)
    assert updated.match_status == MatchStatus.MATCHED
    assert updated.rekordbox_id is not None

    # The stamped Title keeps the fresh row visible to the collection read
    # path — a fresh query by FolderPath returns the row just committed.
    found = service.find_track_by_path(str(downloaded))
    assert found is not None
    assert found.filepath == str(downloaded)
