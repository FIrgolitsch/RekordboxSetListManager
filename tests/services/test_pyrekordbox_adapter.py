"""Tests for the pyrekordbox config adapter (RB7 -> RB6 db_path discovery)."""

import pyrekordbox.config as rb_config

from rekordbox_set_list_manager.services._pyrekordbox_adapter import (
    rekordbox_db_candidates,
)


def _stub_config(monkeypatch, *, rb7=None, rb6=None):
    """Stub the pyrekordbox config functions the adapter imports.

    *rb7* / *rb6* are either a dict the fake getter returns, or an exception
    instance the fake raises.
    """

    def _make(result):
        if isinstance(result, BaseException):

            def _raise(_prog, _app):
                raise result

            return _raise

        def _return(_prog, _app):
            return result

        return _return

    monkeypatch.setattr(rb_config, "_get_rb7_config", _make(rb7))
    monkeypatch.setattr(rb_config, "_get_rb6_config", _make(rb6))
    monkeypatch.setattr(rb_config, "get_pioneer_install_dir", lambda: "prog")
    monkeypatch.setattr(rb_config, "get_pioneer_app_dir", lambda: "app")


def test_candidates_prefers_rb7_over_rb6(tmp_path, monkeypatch):
    rb7_db = tmp_path / "rb7_master.db"
    rb6_db = tmp_path / "rb6_master.db"
    rb7_db.touch()
    rb6_db.touch()
    _stub_config(
        monkeypatch,
        rb7={"db_path": str(rb7_db)},
        rb6={"db_path": str(rb6_db)},
    )

    assert rekordbox_db_candidates() == [rb7_db, rb6_db]


def test_candidates_falls_back_to_rb6_when_rb7_raises(tmp_path, monkeypatch):
    rb6_db = tmp_path / "rb6_master.db"
    rb6_db.touch()
    _stub_config(
        monkeypatch,
        rb7=RuntimeError("no RB7 install"),
        rb6={"db_path": str(rb6_db)},
    )

    assert rekordbox_db_candidates() == [rb6_db]


def test_candidates_falls_back_to_rb6_when_rb7_has_no_db_path(tmp_path, monkeypatch):
    rb6_db = tmp_path / "rb6_master.db"
    rb6_db.touch()
    _stub_config(monkeypatch, rb7={}, rb6={"db_path": str(rb6_db)})

    assert rekordbox_db_candidates() == [rb6_db]


def test_candidates_filters_missing_files(tmp_path, monkeypatch):
    rb6_db = tmp_path / "rb6_master.db"
    rb6_db.touch()
    _stub_config(
        monkeypatch,
        rb7={"db_path": str(tmp_path / "does_not_exist.db")},
        rb6={"db_path": str(rb6_db)},
    )

    assert rekordbox_db_candidates() == [rb6_db]


def test_candidates_returns_empty_when_all_fail(monkeypatch):
    _stub_config(
        monkeypatch,
        rb7=RuntimeError("no RB7"),
        rb6=RuntimeError("no RB6"),
    )

    assert rekordbox_db_candidates() == []


def test_candidates_returns_empty_when_files_missing(tmp_path, monkeypatch):
    _stub_config(
        monkeypatch,
        rb7={"db_path": str(tmp_path / "nope7.db")},
        rb6={"db_path": str(tmp_path / "nope6.db")},
    )

    assert rekordbox_db_candidates() == []
