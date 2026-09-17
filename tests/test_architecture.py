"""Enforce the layering contract ``models`` <- ``services``/``controllers`` <- ``gui``.

An AST walk catches every import form — module-level, ``from X import Y``,
``if TYPE_CHECKING:`` blocks, and function bodies — including the forms that
module-level-only lint rules deliberately do not flag.
"""

import ast
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

_SRC_ROOT = Path(__file__).resolve().parent.parent / "src"
_PKG = "rekordbox_set_list_manager"

_MODELS_BANNED = (
    "PySide6",
    f"{_PKG}.gui",
    f"{_PKG}.services",
    f"{_PKG}.controllers",
)
_SERVICES_BANNED = (
    "PySide6",
    f"{_PKG}.gui",
    f"{_PKG}.controllers",
)


def _package_of(path: Path, src_root: Path) -> str:
    """Dotted package containing *path*, or ``""`` when outside *src_root*."""
    try:
        parts = list(path.resolve().relative_to(src_root).with_suffix("").parts)
    except ValueError:
        return ""
    parts.pop()  # drop the module name (__init__.py *is* the package)
    return ".".join(parts)


def _resolve(node: ast.ImportFrom, package: str) -> str:
    """Absolute dotted module referenced by an ``ImportFrom`` node."""
    if node.level == 0 or not package:
        return node.module or ""
    parts = package.split(".")
    base = parts[: len(parts) - node.level + 1]
    if node.module:
        base += node.module.split(".")
    return ".".join(base)


def _top_two(name: str) -> str:
    return ".".join(name.split(".")[:2])


def _iter_imports(path: Path, src_root: Path = _SRC_ROOT) -> Iterator[str]:
    """Yield every module imported by *path*, normalized to its top two segments."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package = _package_of(path, src_root)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield _top_two(alias.name)
        elif isinstance(node, ast.ImportFrom):
            resolved = _resolve(node, package)
            if not resolved:
                continue
            yield _top_two(resolved)
            for alias in node.names:
                # `from pkg import submod` can itself name a submodule.
                if alias.name != "*":
                    yield _top_two(f"{resolved}.{alias.name}")


def _violations(directory: Path, banned: tuple[str, ...]) -> list[str]:
    """Banned imports found under *directory*, formatted for assertion output."""
    return [
        f"{path.relative_to(_SRC_ROOT)} imports {name}"
        for path in sorted(directory.rglob("*.py"))
        for name in _iter_imports(path)
        if any(name == b or name.startswith(f"{b}.") for b in banned)
    ]


def test_models_stay_qt_and_upper_layer_free() -> None:
    assert _violations(_SRC_ROOT / _PKG / "models", _MODELS_BANNED) == []


def test_services_stay_qt_and_gui_free() -> None:
    assert _violations(_SRC_ROOT / _PKG / "services", _SERVICES_BANNED) == []


def test_collector_catches_blind_spot_imports(tmp_path) -> None:
    """Imports inside TYPE_CHECKING blocks and function bodies are collected."""
    probe = tmp_path / "probe.py"
    probe.write_text(
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    import PySide6\n"
        "def f():\n"
        "    from rekordbox_set_list_manager.gui import main_window\n",
        encoding="utf-8",
    )
    names = set(_iter_imports(probe))
    assert "PySide6" in names
    assert "rekordbox_set_list_manager.gui" in names


def test_relative_imports_resolve_against_owning_package(tmp_path) -> None:
    """``from .. import x`` resolves against the module's package, not the cwd."""
    root = tmp_path / "src"
    mod = root / "pkg" / "sub" / "mod.py"
    mod.parent.mkdir(parents=True)
    mod.write_text("from .. import gui\nfrom . import sibling\n", encoding="utf-8")
    names = set(_iter_imports(mod, root))
    assert "pkg.gui" in names
    assert "pkg.sub" in names


# ---------------------------------------------------------------------------
# Mutation-discipline enforcement
#
# Every model mutation must reach the project through
# ``EditController.apply_mutation`` so undo/redo and dirty tracking stay
# consistent.  The collector walks ``src/`` and flags mutation-shaped
# constructs outside a small per-module allowlist.  It cannot see through
# ``getattr``/``setattr`` indirection or renamed receivers — a documented
# heuristic limit, backstopped by the planted-probe tests below.
#
# ``Project.touch`` is deliberately absent from the ban list: the pre-save
# ``updated_at`` bookkeeping stamp in ``project_controller.save`` must never
# produce an undo step, and it is neither a banned method nor a model-field
# write, so no special-casing is needed.
# ---------------------------------------------------------------------------

_PKG_ROOT = _SRC_ROOT / _PKG

_MUTATION_ALLOWLIST = (
    ("models",),
    ("controllers", "edit_controller.py"),
    ("controllers", "track_match.py"),
    ("gui", "widgets", "dialogs", "theme_dialog.py"),
)

_BANNED_MUTATORS = frozenset(
    {
        "add_track",
        "add_section",
        "remove_section",
        "remove_track",
        "move_section",
        "move_track",
        "apply_theme",
        "add_theme",
        "remove_theme",
    }
)

_FUNNEL_RECEIVERS = frozenset({"_edit", "_edit_ctrl", "edit_ctrl"})

_MODEL_RECEIVERS = frozenset(
    {
        "proj",
        "project",
        "section",
        "sec",
        "track",
        "theme",
        "target",
        "_project",
    }
)

_MODEL_FIELDS = frozenset(
    {
        "name",
        "names",
        "color",
        "section_type",
        "track_ids",
        "transition_notes",
        "themes",
        "tracks",
        "sections",
        "match_status",
        "match_strategy",
        "match_score",
        "filepath",
        "bpm",
        "key",
        "rekordbox_id",
        "duration",
        "isrc",
        "source",
        "spotify_id",
        "tidal_id",
        "spotify_playlist_id",
        "tidal_playlist_id",
        "updated_at",
        "section_color_map",
        "title",
        "artist",
    }
)

_LIST_MUTATORS = frozenset({"append", "insert", "remove", "pop", "clear", "extend"})

_PRIVATE_INTERNALS = frozenset({"_stack", "_emit"})

_FUNNEL_CANARIES = frozenset({"push_snapshot", "notify_changed"})


def _allowlisted(path: Path) -> bool:
    """True when *path* sits under a module exempt from the mutation ban."""
    try:
        parts = path.resolve().relative_to(_PKG_ROOT).parts
    except ValueError:
        return False
    return any(parts[: len(item)] == item for item in _MUTATION_ALLOWLIST)


def _mutation_violations(path: Path, label: str | None = None) -> list[str]:
    """Mutation-ban violations in one non-allowlisted module, sorted by line."""
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits: list[str] = []
    label = label or path.name
    return [f"{label}:{hit}" for hit in sorted(hits)]


def test_all_mutations_route_through_the_funnel() -> None:
    violations = [
        hit
        for path in sorted(_PKG_ROOT.rglob("*.py"))
        if not _allowlisted(path)
        for hit in _mutation_violations(path, label=str(path.relative_to(_SRC_ROOT)))
    ]
    assert violations == [], "\n".join(violations)


def test_mutation_collector_flags_bypasses(tmp_path) -> None:
    """Every unsanctioned mutation shape is reported, none is missed."""
    probe = tmp_path / "bad.py"
    probe.write_text(
        "class Widget:\n"
        "    def plain(self, project, t, tid, section, x):\n"
        "        project.add_track(t)\n"
        "        project.name = 'x'\n"
        "        self._edit_ctrl._stack.push(x)\n"
        "        notify_changed()\n"
        "        section.track_ids.append(tid)\n"
        "\n"
        "    def helpers(self, track, local, other):\n"
        "        apply_track_match(track, local)\n"
        "        other._push(track)\n"
        "        self._edit_ctrl._emit()\n"
        "\n"
        "    def orphan(self, proj, t):\n"
        "        def _helper(p):\n"
        "            p.add_track(t)\n"
        "        _helper(proj)\n"
        "\n"
        "    def sneaky(self):\n"
        "        def _fn(proj):\n"
        "            self._edit_ctrl._stack.push('x')\n"
        "        self._edit_ctrl.apply_mutation(_fn)\n",
        encoding="utf-8",
    )
    hits = _mutation_violations(probe)
    expected = [
        "banned mutator 'add_track'",
        "model field write 'name'",
        "private internals '_stack'",
        "escape hatch 'notify_changed'",
        "mutating call 'append' on field 'track_ids'",
        "bare 'apply_track_match'",
        "private '_push' on non-self receiver",
        "private internals '_emit'",
        "banned mutator 'add_track'",
        "private internals '_stack'",
    ]
    descriptions = sorted(hit.split(": ", 1)[1] for hit in hits)
    assert descriptions == sorted(expected), "\n".join(hits)


def test_mutation_collector_allows_sanctioned_shapes(tmp_path) -> None:
    """Funnel-arg closures, EditController delegation, own ``_push``, ``touch`` pass."""
    probe = tmp_path / "good.py"
    probe.write_text(
        "class Widget:\n"
        "    def funneled(self, t, sid, track):\n"
        "        def _fn(proj):\n"
        "            proj.add_track(t)\n"
        "            proj.name = 'x'\n"
        "            proj.tracks[t.id] = t\n"
        "\n"
        "        self._edit_ctrl.apply_mutation(_fn)\n"
        "        self._edit_ctrl.apply_mutation(lambda proj: proj.add_track(track))\n"
        "        self._edit.add_track(track, sid)\n"
        "        self._push('playlist', [])\n"
        "        self._project.touch()\n",
        encoding="utf-8",
    )
    assert _mutation_violations(probe) == []
