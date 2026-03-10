"""Lightweight JSON config for persistent application settings."""

from __future__ import annotations

import json
from pathlib import Path

import platformdirs

_CONFIG_DIR = Path(platformdirs.user_config_dir("set_manager"))
_CONFIG_PATH = _CONFIG_DIR / "config.json"

_cache: dict | None = None


def _load() -> dict:
    global _cache
    if _cache is None:
        if _CONFIG_PATH.exists():
            try:
                _cache = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                _cache = {}
        else:
            _cache = {}
    return _cache


def _save(data: dict) -> None:
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def get(key: str, default: object = None) -> object:
    """Return config value for *key*, or *default* if not set."""
    return _load().get(key, default)


def set(key: str, value: object) -> None:  # noqa: A001
    """Persist *value* under *key*."""
    global _cache
    data = _load()
    data[key] = value
    _cache = data
    _save(data)
