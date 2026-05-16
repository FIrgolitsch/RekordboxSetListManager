"""Project lifecycle controller: load, save, dirty tracking."""

from __future__ import annotations

from pathlib import Path  # noqa: TC003 — used at runtime in method signatures

from PySide6.QtCore import QObject, Signal

from rekordbox_set_list_manager.models.project import Project
from rekordbox_set_list_manager.services.project_io import (  # noqa: F401 — re-exported
    ProjectIOError,
    load_project,
    save_project,
)
from rekordbox_set_list_manager.utils.config import add_recent_file
from rekordbox_set_list_manager.utils.constants import PROJECT_FILE_EXTENSION

_DEFAULT_PROJECT_NAME = "Untitled"


class ProjectController(QObject):
    """Manages the active project, save path, and dirty state.

    Emits:
        project_changed(Project | None): Fired after a new project is loaded,
            created, or restored from a snapshot.
        save_path_changed(Path | None): Fired when the save path changes.
        dirty_changed(bool): Fired when dirty state transitions.
    """

    project_changed = Signal(object)  # Project | None
    save_path_changed = Signal(object)  # Path | None
    dirty_changed = Signal(bool)

    def __init__(self, parent: QObject | None = None) -> None:
        """Initialise with no project loaded and a clean dirty state."""
        super().__init__(parent)
        self._project: Project | None = None
        self._save_path: Path | None = None
        self._dirty: bool = False

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def project(self) -> Project | None:
        """Return the currently loaded project, or None if none is open."""
        return self._project

    @property
    def save_path(self) -> Path | None:
        """Return the path the project was last saved to, or None."""
        return self._save_path

    @property
    def dirty(self) -> bool:
        """Return True if the project has unsaved changes."""
        return self._dirty

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def new(self, name: str = _DEFAULT_PROJECT_NAME) -> Project:
        """Create a fresh project and emit signals.

        Parameters
        ----------
        name : str
            Display name for the new project.  Defaults to ``"Untitled"``.

        Returns
        -------
        Project
            The newly created project.

        """
        self._project = Project(name=name)
        self._save_path = None
        self._emit_dirty(dirty=False)
        self.save_path_changed.emit(None)
        self.project_changed.emit(self._project)
        return self._project

    def load(self, path: Path) -> Project:
        """Load project from *path*.  Raises :class:`ProjectIOError` on failure.

        Parameters
        ----------
        path : Path
            Path to the ``.setmgr`` project file.

        Returns
        -------
        Project
            The loaded project.

        """
        project = load_project(path)
        self._project = project
        self._save_path = path
        self._emit_dirty(dirty=False)
        add_recent_file(str(path))
        self.save_path_changed.emit(path)
        self.project_changed.emit(project)
        return project

    def save(self, path: Path) -> None:
        """Save current project to *path*.  Raises :class:`ProjectIOError` on failure.

        Normalises the path suffix to :data:`~rekordbox_set_list_manager.utils.constants
        .PROJECT_FILE_EXTENSION`
        if needed, then writes the file, updates the save path, clears dirty, and
        records the file in the recent-files list.

        Parameters
        ----------
        path : Path
            Destination path for the saved project file.

        """
        if self._project is None:
            return
        if path.suffix.lower() != PROJECT_FILE_EXTENSION:
            path = path.with_suffix(PROJECT_FILE_EXTENSION)
        self._project.touch()
        save_project(self._project, path)
        prev = self._save_path
        self._save_path = path
        self._emit_dirty(dirty=False)
        add_recent_file(str(path))
        if path != prev:
            self.save_path_changed.emit(path)

    def mark_dirty(self) -> None:
        """Mark the project as having unsaved changes."""
        self._emit_dirty(dirty=True)

    def restore(self, project: Project) -> None:
        """Replace the active project with *project* (e.g. an undo/redo snapshot).

        Sets dirty = True and emits :attr:`project_changed` so the UI refreshes.
        The save path is not changed.

        Parameters
        ----------
        project : Project
            The project snapshot to restore.

        """
        self._project = project
        self._emit_dirty(dirty=True)
        self.project_changed.emit(project)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _emit_dirty(self, dirty: bool) -> None:
        if self._dirty != dirty:
            self._dirty = dirty
            self.dirty_changed.emit(dirty)
