"""Unit tests for utils.config persistence guarantees."""

from __future__ import annotations

import json
import logging
import stat
import sys
from typing import TYPE_CHECKING

import pytest

import rekordbox_set_list_manager.utils.config as config_mod

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the config module at a tmp dir and reset the cached settings."""
    config_dir = tmp_path / "config"
    monkeypatch.setattr(config_mod, "_CONFIG_DIR", config_dir)
    monkeypatch.setattr(config_mod, "_CONFIG_PATH", config_dir / "config.json")
    monkeypatch.setattr(config_mod, "_cache", None)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


def test_set_value_writes_and_reads_back(tmp_path: Path) -> None:
    config_mod.set_value("recent_files", ["/a.setmgr"])

    assert config_mod.get("recent_files") == ["/a.setmgr"]
    on_disk = json.loads((tmp_path / "config" / "config.json").read_text(encoding="utf-8"))
    assert on_disk["recent_files"] == ["/a.setmgr"]


def test_get_returns_default_for_missing_key() -> None:
    assert config_mod.get("nope", "fallback") == "fallback"


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------


def test_write_leaves_no_tmp_file(tmp_path: Path) -> None:
    config_mod.set_value("k", "v")

    assert not (tmp_path / "config" / "config.json.tmp").exists()
    assert (tmp_path / "config" / "config.json").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits only")
def test_written_config_is_owner_only(tmp_path: Path) -> None:
    config_mod.set_value("k", "v")

    assert _mode(tmp_path / "config" / "config.json") == 0o600


# ---------------------------------------------------------------------------
# Corrupt / missing file handling
# ---------------------------------------------------------------------------


def test_missing_config_is_silent(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger=config_mod.__name__):
        assert config_mod.get("k", "d") == "d"
    assert caplog.records == []


def test_corrupt_config_warns_and_falls_back(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "config.json").write_text("{not json", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger=config_mod.__name__):
        assert config_mod.get("k", "d") == "d"

    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_empty_config_warns_and_falls_back(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "config.json").write_text("", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger=config_mod.__name__):
        assert config_mod.get("k", "d") == "d"

    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_corrupt_config_does_not_raise(tmp_path: Path) -> None:
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "config.json").write_text("garbage", encoding="utf-8")

    assert config_mod.get_recent_files() == []


# ---------------------------------------------------------------------------
# Opportunistic permission fix on load
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits only")
def test_load_chmods_loose_existing_config(tmp_path: Path) -> None:
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True)
    cfg = cfg_dir / "config.json"
    cfg.write_text('{"k": "v"}', encoding="utf-8")
    cfg.chmod(0o644)

    assert config_mod.get("k") == "v"
    assert _mode(cfg) == 0o600
