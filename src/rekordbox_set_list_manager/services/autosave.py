"""Periodic autosave and crash-recovery helpers."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import TYPE_CHECKING

import platformdirs

from rekordbox_set_list_manager.services.project_io import (
    ProjectIOError,
    load_project,
    save_project,
)

if TYPE_CHECKING:
    from uuid import UUID

    from rekordbox_set_list_manager.models.project import Project

_AUTOSAVE_DIR = Path(platformdirs.user_cache_dir("rekordbox_set_list_manager")) / "autosave"
CRASH_LOG = Path(platformdirs.user_cache_dir("rekordbox_set_list_manager")) / "crash.log"


def _autosave_path(project_id: UUID) -> Path:
    return _AUTOSAVE_DIR / f"{project_id}.setmgr"


def write_autosave(project: Project) -> None:
    """Write *project* to the autosave slot for its id.  Silent on error."""
    with contextlib.suppress(Exception):
        _AUTOSAVE_DIR.mkdir(parents=True, exist_ok=True)
        save_project(project, _autosave_path(project.id))


def read_autosave(project_id: UUID) -> Project | None:
    """Return the autosaved project for *project_id*, or None if absent/corrupt."""
    path = _autosave_path(project_id)
    if not path.exists():
        return None
    with contextlib.suppress(ProjectIOError, Exception):
        return load_project(path)
    return None


def autosave_mtime(project_id: UUID) -> float:
    """Return the mtime of the autosave file, or 0.0 if it does not exist."""
    path = _autosave_path(project_id)
    with contextlib.suppress(OSError):
        return path.stat().st_mtime
    return 0.0


def clear_autosave(project_id: UUID) -> None:
    """Remove the autosave file for *project_id*.  Silent on error."""
    with contextlib.suppress(OSError):
        _autosave_path(project_id).unlink(missing_ok=True)
