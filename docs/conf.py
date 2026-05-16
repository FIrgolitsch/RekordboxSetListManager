"""Sphinx configuration for Rekordbox Set List Manager documentation."""

import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# ---------------------------------------------------------------------------
# Project metadata
# ---------------------------------------------------------------------------

project = "Rekordbox Set List Manager"
author = "Rekordbox Set List contributors"
copyright = f"{datetime.now(tz=UTC).year}, {author}"  # noqa: A001

try:
    from importlib.metadata import version as _get_version

    release = _get_version("rekordbox-set-list-manager")
except Exception:  # noqa: BLE001
    release = "0.1.0"
version = ".".join(release.split(".")[:2])

# ---------------------------------------------------------------------------
# General
# ---------------------------------------------------------------------------

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "autoapi.extension",
    "myst_parser",
    "sphinx_design",
    "sphinx_copybutton",
    "notfound.extension",
]

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

source_suffix = {".rst": "restructuredtext", ".md": "markdown"}

# ---------------------------------------------------------------------------
# MyST
# ---------------------------------------------------------------------------

myst_enable_extensions = [
    "colon_fence",
    "deflist",
    "linkify",
    "tasklist",
]
myst_heading_anchors = 3

# ---------------------------------------------------------------------------
# AutoAPI
# ---------------------------------------------------------------------------

autoapi_type = "python"
autoapi_dirs = [str(ROOT / "src" / "rekordbox_set_list_manager")]
autoapi_root = "api"
autoapi_options = [
    "members",
    "undoc-members",
    "show-inheritance",
    "show-module-summary",
]
autoapi_keep_files = True
autoapi_add_toctree_entry = False  # we include api/index manually in index.rst

# ---------------------------------------------------------------------------
# Napoleon / autodoc
# ---------------------------------------------------------------------------

napoleon_numpy_docstring = True
napoleon_google_docstring = False
napoleon_use_param = True
napoleon_use_rtype = True
napoleon_use_ivar = True
napoleon_attr_annotations = True

autodoc_typehints = "description"
autodoc_member_order = "bysource"

# ---------------------------------------------------------------------------
# HTML output — pydata-sphinx-theme
# ---------------------------------------------------------------------------

html_theme = "pydata_sphinx_theme"
html_title = "Rekordbox Set List Manager"
html_static_path = ["_static"]

html_theme_options = {
    "show_toc_level": 2,
    "navigation_with_keys": True,
    "show_prev_next": True,
    "navbar_align": "left",
    "navbar_end": ["theme-switcher", "navbar-icon-links"],
    "footer_start": ["copyright"],
    "footer_end": ["sphinx-version", "theme-version"],
    "secondary_sidebar_items": {
        "**": ["page-toc"],
        "index": [],
    },
}

# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------

suppress_warnings = [
    "autoapi.python_import_resolution",
    "ref.python",
    "misc.highlighting_failure",
]

copybutton_prompt_text = r">>> |\.\.\. |\$ "
copybutton_prompt_is_regexp = True
copybutton_only_copy_prompt_lines = False

nitpicky = False
