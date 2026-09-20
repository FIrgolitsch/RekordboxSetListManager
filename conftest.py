"""Root pytest config — loaded before tests/conftest.py.

Lives at the repo root so it runs as part of the initial conftests, before
pytest-xdist computes the worker count. ``tests/conftest.py`` is loaded later
and handles fixtures and the offscreen Qt platform default.
"""

import os

# Cap xdist's auto worker count before xdist parses the command line.
# Unbounded workers each spin up a PySide6 QApplication, which exhausts
# memory on a typical dev machine. setdefault lets an explicit env var win.
os.environ.setdefault("PYTEST_XDIST_AUTO_NUM_WORKERS", "8")
