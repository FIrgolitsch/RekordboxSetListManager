# Projects

---

## File format

Projects are saved as **`.setmgr`** files.  A `.setmgr` file is a plain-text JSON document (pretty-printed with 2-space indentation).  You can open it in any text editor.

The file contains:
- A format version string.
- The full project: all sections, all tracks (with metadata and match results), all transition notes, and all section name themes.

Because it is JSON, `.setmgr` files are safe to commit to git and diff normally.

---

## Saving

| Action | Shortcut | Description |
|---|---|---|
| Save | `Ctrl+S` | Write to the current file.  Shows a Save dialog if the project has never been saved. |
| Save As… | `Ctrl+Shift+S` | Write to a new file, which becomes the current file. |

Rekordbox Set List Manager warns before overwriting an existing file when using Save As.

---

## Opening

| Action | Shortcut | Description |
|---|---|---|
| Open… | `Ctrl+O` | Browse for a `.setmgr` file. |
| Open Recent | — | **File → Open Recent** lists the last 10 opened files. |

If the project has unsaved changes, Rekordbox Set List Manager prompts you to save before opening another file.

---

## Autosave

Rekordbox Set List Manager autosaves the current project **every 30 seconds** if there are unsaved changes.  The autosave is written to a temporary file in the system cache directory:

| Platform | Autosave location |
|---|---|
| macOS | `~/Library/Caches/rekordbox_set_list_manager/` |
| Linux | `~/.cache/rekordbox_set_list_manager/` |
| Windows | `%LOCALAPPDATA%\rekordbox_set_list_manager\` |

The autosave file name is derived from the project file name.  If the application crashes:

1. Reopen the project normally with **File → Open…**
2. Rekordbox Set List Manager detects the autosave and asks whether you want to restore it.

Once the project is saved manually, the autosave file is deleted.

---

## New project

**File → New** (`Ctrl+N`) creates a blank project.  If there are unsaved changes, you are prompted to save first.

The new project starts with no sections and no tracks.

---

## Project settings

A project stores:
- **Name** — set when you first save the file (taken from the filename).
- **Sections** — ordered list of sections (each with name, type, colour, and tracks).
- **Section name themes** — saved presets for bulk-renaming sections.

There is no separate "Project Settings" dialog — all settings are implicit in the saved structure.
