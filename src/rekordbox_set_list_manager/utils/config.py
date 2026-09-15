"""Lightweight JSON config for persistent application settings."""

from __future__ import annotations

import contextlib
import json
import logging
import os
from pathlib import Path

import platformdirs

log = logging.getLogger(__name__)

_CONFIG_DIR = Path(platformdirs.user_config_dir("rekordbox_set_list_manager"))
_CONFIG_PATH = _CONFIG_DIR / "config.json"

_cache: dict | None = None


def _chmod_private(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.chmod(0o600)


def _load() -> dict:
    global _cache
    if _cache is None:
        if _CONFIG_PATH.exists():
            _chmod_private(_CONFIG_PATH)
            try:
                _cache = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                log.warning("Could not parse config file %s: %s", _CONFIG_PATH, exc)
                _cache = {}
        else:
            _cache = {}
    return _cache


def _save(data: dict) -> None:
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    # Create the sibling tmp file with mode 0600 so the config never exists
    # world-readable, then atomically replace the target.
    tmp_path = _CONFIG_PATH.with_name(f"{_CONFIG_PATH.name}.tmp")
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    tmp_path.replace(_CONFIG_PATH)
    _chmod_private(_CONFIG_PATH)


def get(key: str, default: object = None) -> object:
    """Return config value for *key*, or *default* if not set.

    Parameters
    ----------
    key : str
        The config key to look up.
    default : object
        Value returned when *key* is absent.  Defaults to ``None``.

    Returns
    -------
    object
        The stored value, or *default* if the key has not been set.

    """
    return _load().get(key, default)


def set_value(key: str, value: object) -> None:
    """Persist *value* under *key*.

    Parameters
    ----------
    key : str
        The config key to write.
    value : object
        The value to store; must be JSON-serialisable.

    """
    global _cache
    data = _load()
    data[key] = value
    _cache = data
    _save(data)


# ---------------------------------------------------------------------------
# Recent files
# ---------------------------------------------------------------------------

_MAX_RECENT = 10


def get_recent_files() -> list[str]:
    """Return the list of recently opened file paths (newest first).

    Returns
    -------
    list[str]
        Absolute file paths ordered from most to least recently opened.

    """
    raw = get("recent_files")
    if isinstance(raw, list):
        return [str(p) for p in raw if isinstance(p, str)]
    return []


def add_recent_file(path: str) -> None:
    """Add *path* to the top of the recent-files list (max _MAX_RECENT).

    Parameters
    ----------
    path : str
        Absolute file path to prepend to the recent-files list.

    """
    recent = get_recent_files()
    # Remove if already present, then push to front.
    if path in recent:
        recent.remove(path)
    recent.insert(0, path)
    set_value("recent_files", recent[:_MAX_RECENT])
