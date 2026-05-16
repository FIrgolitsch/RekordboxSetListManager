"""On-disk cache for the Rekordbox collection to avoid slow re-loads.

The cache stores all tracks as a JSON byte-stream in the platformdirs
user-cache directory.  Validity is checked against the modification time of
the Rekordbox master.db file so the cache is automatically invalidated
whenever Rekordbox writes a new library snapshot.
"""

from __future__ import annotations

import contextlib
import json
import logging
from pathlib import Path

import platformdirs
from pydantic import TypeAdapter

from rekordbox_set_list_manager.models.track import Track

_CACHE_DIR = Path(platformdirs.user_cache_dir("rekordbox_set_list_manager"))
_CACHE_FILE = _CACHE_DIR / "collection.json"
_META_FILE = _CACHE_DIR / "collection_meta.json"

_ADAPTER: TypeAdapter[list[Track]] = TypeAdapter(list[Track])

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def is_valid(db_path: Path) -> bool:
    """Return ``True`` if the cached collection is up to date with *db_path*.

    Parameters
    ----------
    db_path : Path
        Path to the Rekordbox SQLite database whose mtime is checked.

    Returns
    -------
    bool
        ``True`` if the cache exists and its recorded mtime matches *db_path*.

    """
    if not _CACHE_FILE.exists() or not _META_FILE.exists():
        return False
    try:
        meta: dict = json.loads(_META_FILE.read_text(encoding="utf-8"))
        cached_mtime: float = float(meta.get("db_mtime", 0.0))
        current_mtime: float = db_path.stat().st_mtime
        return abs(current_mtime - cached_mtime) < 1.0
    except Exception:  # noqa: BLE001
        return False


def load() -> list[Track] | None:
    """Load tracks from the cache.  Returns ``None`` if missing or corrupt.

    Returns
    -------
    list[Track] | None
        The cached tracks, or ``None`` if the cache file is absent or invalid.

    """
    if not _CACHE_FILE.exists():
        return None
    try:
        return _ADAPTER.validate_json(_CACHE_FILE.read_bytes())
    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to read collection cache: %s", exc)
        return None


def load_if_valid(db_path: Path) -> list[Track] | None:
    """Return cached tracks if the cache is valid for *db_path*, else ``None``.

    Parameters
    ----------
    db_path : Path
        Path to the Rekordbox SQLite database to validate the cache against.

    Returns
    -------
    list[Track] | None
        The cached track list if valid, or ``None`` if the cache is stale or absent.

    """
    if not is_valid(db_path):
        return None
    return load()


def save(tracks: list[Track], db_path: Path) -> None:
    """Persist *tracks* and the current mtime of *db_path* to disk.

    Parameters
    ----------
    tracks : list[Track]
        The collection tracks to serialise and cache.
    db_path : Path
        Path to the Rekordbox database; its mtime is stored for cache validation.

    """
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        _CACHE_FILE.write_bytes(_ADAPTER.dump_json(tracks))
        mtime = db_path.stat().st_mtime
        _META_FILE.write_text(
            json.dumps({"db_path": str(db_path), "db_mtime": mtime}),
            encoding="utf-8",
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to save collection cache: %s", exc)


def invalidate() -> None:
    """Delete the cache files (called when the user explicitly forces a reload)."""
    for f in (_CACHE_FILE, _META_FILE):
        with contextlib.suppress(Exception):
            f.unlink(missing_ok=True)
