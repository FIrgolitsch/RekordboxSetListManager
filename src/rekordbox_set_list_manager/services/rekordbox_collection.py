"""Unified protocol + factory for loading Rekordbox track collections."""

from __future__ import annotations

from pathlib import Path  # noqa: TC003 — used at runtime in _XmlLoader.__init__
from typing import TYPE_CHECKING, Protocol

from rekordbox_set_list_manager.services.rekordbox_db import RekordboxDbError, RekordboxDbService
from rekordbox_set_list_manager.services.rekordbox_xml import RekordboxXmlError, RekordboxXmlService

if TYPE_CHECKING:
    from rekordbox_set_list_manager.models.track import Track


class CollectionLoaderError(Exception):
    """Raised when any :class:`CollectionLoader` fails to load tracks."""


class CollectionLoader(Protocol):
    """Protocol satisfied by any Rekordbox collection source."""

    @property
    def source_name(self) -> str:
        """Short human-readable label (e.g. filename or "Rekordbox DB")."""
        ...

    def load(self) -> list[Track]:
        """Load and return all tracks.

        Raises:
            CollectionLoaderError: on any loading failure.
        """
        ...


# ---------------------------------------------------------------------------
# Private implementations
# ---------------------------------------------------------------------------


class _XmlLoader:
    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def source_name(self) -> str:
        return self._path.name

    def load(self) -> list[Track]:
        try:
            return RekordboxXmlService().import_collection(self._path)
        except RekordboxXmlError as exc:
            raise CollectionLoaderError(str(exc)) from exc


class _DbLoader:
    @property
    def source_name(self) -> str:
        return "Rekordbox DB"

    def load(self) -> list[Track]:
        try:
            return RekordboxDbService().get_collection()
        except RekordboxDbError as exc:
            raise CollectionLoaderError(str(exc)) from exc


# ---------------------------------------------------------------------------
# Public factory functions
# ---------------------------------------------------------------------------


def xml_loader(path: Path) -> CollectionLoader:
    """Return a loader that reads tracks from a Rekordbox XML export at *path*."""
    return _XmlLoader(path)


def db_loader() -> CollectionLoader:
    """Return a loader that reads tracks from the local Rekordbox database."""
    return _DbLoader()
