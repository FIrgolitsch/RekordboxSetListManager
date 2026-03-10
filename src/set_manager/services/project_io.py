"""Save and load .setmgr project files."""

from pathlib import Path

from pydantic import BaseModel, ValidationError

from set_manager.models.project import Project
from set_manager.utils.constants import PROJECT_FILE_EXTENSION

_FORMAT_VERSION = "1"


class ProjectIOError(Exception):
    """Raised when a project file cannot be read or written."""


class _ProjectFile(BaseModel):
    """On-disk envelope that wraps a Project with format versioning."""

    version: str = _FORMAT_VERSION
    project: Project


def save_project(project: Project, path: Path) -> None:
    """Serialise *project* and write it to *path*.

    The path should use the :data:`~set_manager.utils.constants.PROJECT_FILE_EXTENSION`
    suffix, but this is not enforced so callers can write to temp paths during saves.
    """
    envelope = _ProjectFile(project=project)
    try:
        path.write_text(envelope.model_dump_json(indent=2), encoding="utf-8")
    except OSError as exc:
        raise ProjectIOError(f"Could not write project to {path}: {exc}") from exc


def load_project(path: Path) -> Project:
    """Read a .setmgr file from *path* and deserialise it into a :class:`Project`.

    Raises :class:`ProjectIOError` if the file cannot be read or parsed.
    """
    if not path.exists():
        raise ProjectIOError(f"Project file not found: {path}")

    suffix = path.suffix.lower()
    if suffix != PROJECT_FILE_EXTENSION:
        raise ProjectIOError(
            f"Unexpected file extension '{suffix}'; expected '{PROJECT_FILE_EXTENSION}'"
        )

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ProjectIOError(f"Could not read project file {path}: {exc}") from exc

    try:
        envelope = _ProjectFile.model_validate_json(raw)
    except ValidationError as exc:
        raise ProjectIOError(f"Invalid project file {path}: {exc}") from exc

    if envelope.version != _FORMAT_VERSION:
        raise ProjectIOError(
            f"Unsupported project file version '{envelope.version}' "
            f"(expected '{_FORMAT_VERSION}')"
        )

    return envelope.project
