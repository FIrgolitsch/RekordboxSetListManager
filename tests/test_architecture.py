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
