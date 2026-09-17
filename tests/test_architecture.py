"""Enforce the layering contract ``models`` <- ``services``/``controllers`` <- ``gui``.

An AST walk catches every import form — module-level, ``from X import Y``,
``if TYPE_CHECKING:`` blocks, and function bodies — including the forms that
module-level-only lint rules deliberately do not flag.
"""

import ast
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

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

_LIST_MUTATORS = frozenset(
    {
        "append",
        "insert",
        "remove",
        "pop",
        "clear",
        "extend",
        "sort",
        "reverse",
        "update",
        "setdefault",
        "popitem",
    }
)

_PRIVATE_INTERNALS = frozenset({"_stack", "_emit"})

_FUNNEL_CANARIES = frozenset({"push_snapshot", "notify_changed"})


def _allowlisted(path: Path) -> bool:
    """True when *path* sits under a module exempt from the mutation ban."""
    try:
        parts = path.resolve().relative_to(_PKG_ROOT).parts
    except ValueError:
        return False
    return any(parts[: len(item)] == item for item in _MUTATION_ALLOWLIST)


def _terminal_name(expr: ast.expr) -> str:
    """Outermost name of a receiver chain: ``a.b.c`` -> ``"c"``, ``x`` -> ``"x"``."""
    if isinstance(expr, ast.Attribute):
        return expr.attr
    if isinstance(expr, ast.Name):
        return expr.id
    return ""


def _scope_nodes(scope: ast.AST) -> Iterator[ast.AST]:
    """Yield nodes in *scope* without crossing nested def/lambda bodies."""
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue  # the header belongs to this scope; the body is a new one
        stack.extend(ast.iter_child_nodes(node))


def _flat_names(targets: Iterable[ast.expr]) -> Iterator[str]:
    """Names bound by assignment targets, flattening tuple/starred unpacking."""
    for target in targets:
        if isinstance(target, ast.Name):
            yield target.id
        elif isinstance(target, (ast.Tuple, ast.List)):
            yield from _flat_names(target.elts)
        elif isinstance(target, ast.Starred):
            yield from _flat_names([target.value])


def _local_callables(scope: ast.AST) -> dict[str, ast.AST]:
    """Names bound to ``def``/``lambda`` bodies directly inside *scope*."""
    local: dict[str, ast.AST] = {}
    for node in _scope_nodes(scope):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            local.setdefault(node.name, node)
        elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Lambda):
            for name in _flat_names(node.targets):
                local.setdefault(name, node.value)
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.value, ast.Lambda)
            and isinstance(node.target, ast.Name)
        ):
            local.setdefault(node.target.id, node.value)
    return local


def _funnel_args(call: ast.Call) -> list[ast.expr]:
    """Call arguments when *call* invokes ``*.apply_mutation``, else empty."""
    if not (isinstance(call.func, ast.Attribute) and call.func.attr == "apply_mutation"):
        return []
    return [*call.args, *(kw.value for kw in call.keywords)]


def _sanctioned_bodies(tree: ast.AST) -> set[int]:
    """Ids of def/lambda bodies passed to ``apply_mutation`` in the same scope.

    A local ``def``/``lambda`` is a sanctioned mutation closure iff its name
    appears as an argument to a ``*.apply_mutation(...)`` call in the same
    enclosing function; inline lambda args are sanctioned directly.
    """
    sanctioned: set[int] = set()
    scopes = [tree]
    while scopes:
        scope = scopes.pop()
        local = _local_callables(scope)
        for node in _scope_nodes(scope):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                scopes.append(node)
            elif isinstance(node, ast.Call):
                for arg in _funnel_args(node):
                    if isinstance(arg, ast.Name) and arg.id in local:
                        sanctioned.add(id(local[arg.id]))
                    elif isinstance(arg, ast.Lambda):
                        sanctioned.add(id(arg))
    return sanctioned


def _field_write(target: ast.expr) -> str | None:
    """Description when *target* stores into a model field on a model receiver."""
    if isinstance(target, ast.Attribute):
        base, field = target.value, target.attr
    elif isinstance(target, ast.Subscript) and isinstance(target.value, ast.Attribute):
        base, field = target.value.value, target.value.attr
    else:
        return None
    if field in _MODEL_FIELDS and _terminal_name(base) in _MODEL_RECEIVERS:
        return f"model field write '{field}' on '{_terminal_name(base)}'"
    return None


def _flat_targets(target: ast.expr) -> Iterator[ast.expr]:
    """Leaf targets of an assignment, flattening tuple/starred unpacking."""
    if isinstance(target, (ast.Tuple, ast.List)):
        for elt in target.elts:
            yield from _flat_targets(elt)
    elif isinstance(target, ast.Starred):
        yield from _flat_targets(target.value)
    else:
        yield target


def _call_violation(node: ast.Call) -> str | None:
    """Description when *node* is a banned or field-mutating call."""
    func = node.func
    if isinstance(func, ast.Name):
        return "bare 'apply_track_match'" if func.id == "apply_track_match" else None
    if not isinstance(func, ast.Attribute):
        return None
    if func.attr in _BANNED_MUTATORS:
        receiver = _terminal_name(func.value) or "?"
        if receiver not in _FUNNEL_RECEIVERS:
            return f"banned mutator '{func.attr}' on '{receiver}'"
    elif (
        func.attr in _LIST_MUTATORS
        and isinstance(func.value, ast.Attribute)
        and func.value.attr in _MODEL_FIELDS
        and _terminal_name(func.value.value) in _MODEL_RECEIVERS
    ):
        return f"mutating call '{func.attr}' on field '{func.value.attr}'"
    return None


def _internals_violation(node: ast.AST) -> str | None:
    """Description when *node* touches funnel internals or the deleted escape hatch.

    Checked even inside sanctioned funnel closures — private internals and
    the deleted ``push_snapshot``/``notify_changed`` protocol are banned
    anywhere in the module.
    """
    if isinstance(node, ast.Attribute):
        if node.attr in _FUNNEL_CANARIES:
            return f"escape hatch '{node.attr}'"
        if node.attr in _PRIVATE_INTERNALS:
            return f"private internals '{node.attr}'"
        if node.attr == "_push" and not (
            isinstance(node.value, ast.Name) and node.value.id == "self"
        ):
            return "private '_push' on non-self receiver"
    elif isinstance(node, ast.Name) and node.id in _FUNNEL_CANARIES:
        return f"escape hatch '{node.id}'"
    elif (
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in _FUNNEL_CANARIES
    ):
        return f"escape hatch '{node.name}'"
    return None


def _mutation_violations(path: Path, label: str | None = None) -> list[str]:
    """Mutation-ban violations in one non-allowlisted module, sorted by line."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    sanctioned = _sanctioned_bodies(tree)
    hits: list[tuple[int, str]] = []

    def visit(node: ast.AST, inside_funnel: bool) -> None:
        if id(node) in sanctioned:
            inside_funnel = True
        desc = _internals_violation(node)
        if desc is not None:
            hits.append((getattr(node, "lineno", 0), desc))
        if not inside_funnel:
            if isinstance(node, ast.Call):
                desc = _call_violation(node)
                if desc is not None:
                    hits.append((node.lineno, desc))
            elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    for leaf in _flat_targets(target):
                        desc = _field_write(leaf)
                        if desc is not None:
                            hits.append((node.lineno, desc))
            elif isinstance(node, ast.Delete):
                for target in node.targets:
                    for leaf in _flat_targets(target):
                        desc = _field_write(leaf)
                        if desc is not None:
                            hits.append((node.lineno, desc))
        for child in ast.iter_child_nodes(node):
            visit(child, inside_funnel)

    visit(tree, inside_funnel=False)
    label = label or path.name
    return [f"{label}:{lineno}: {desc}" for lineno, desc in sorted(hits)]


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
        "        section.track_ids.sort()\n"
        "        section.track_ids.reverse()\n"
        "        project.tracks.update({})\n"
        "        section.transition_notes.setdefault(str(tid), 'x')\n"
        "        section.transition_notes.popitem()\n"
        "        del section.track_ids[0]\n"
        "        del project.tracks[tid]\n"
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
        "banned mutator 'add_track' on 'project'",
        "model field write 'name' on 'project'",
        "private internals '_stack'",
        "escape hatch 'notify_changed'",
        "mutating call 'append' on field 'track_ids'",
        "mutating call 'sort' on field 'track_ids'",
        "mutating call 'reverse' on field 'track_ids'",
        "mutating call 'update' on field 'tracks'",
        "mutating call 'setdefault' on field 'transition_notes'",
        "mutating call 'popitem' on field 'transition_notes'",
        "model field write 'track_ids' on 'section'",
        "model field write 'tracks' on 'project'",
        "bare 'apply_track_match'",
        "private '_push' on non-self receiver",
        "private internals '_emit'",
        "banned mutator 'add_track' on 'p'",
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
        "        self._edit_ctrl.add_track(track, sid)\n"
        "        self._push('playlist', [])\n"
        "        self._project.touch()\n",
        encoding="utf-8",
    )
    assert _mutation_violations(probe) == []
