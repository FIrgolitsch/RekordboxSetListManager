# Installation

## Requirements

| Requirement | Minimum |
|---|---|
| macOS | 12 Monterey or later |
| Python | 3.12 or later |
| Rekordbox | 6.x or 7.x (for XML export/import) |

> **Windows / Linux** — the application is tested primarily on macOS.  It should run on any platform that supports PySide6, but is not officially supported.

---

## Install with uv (recommended)

[uv](https://docs.astral.sh/uv/) is a fast Python package manager.  If you don't have it yet:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then install Rekordbox Set List Manager:

```bash
# Clone the repository
git clone https://github.com/your-org/set_manager.git
cd set_manager

# Create a virtual environment and install all dependencies
uv sync
```

---

## Install with pip

```bash
git clone https://github.com/your-org/set_manager.git
cd set_manager
python -m pip install -e .
```

---

## First launch

```bash
# With uv
uv run set-manager

# With pip / activated venv
set-manager
# or
python -m rekordbox_set_list_manager
```

The main window opens with an empty project.  You can start adding sections immediately or [connect a streaming service](connecting-services.md) first.

---

## Updating

```bash
git pull
uv sync   # or: pip install -e .
```

---

## Uninstalling

Delete the cloned folder.  Configuration is stored in the system user-data directory:

| Platform | Config location |
|---|---|
| macOS | `~/Library/Application Support/rekordbox_set_list_manager/` |
| Linux | `~/.config/rekordbox_set_list_manager/` |
| Windows | `%APPDATA%\rekordbox_set_list_manager\` |

Remove that directory to wipe all settings.
