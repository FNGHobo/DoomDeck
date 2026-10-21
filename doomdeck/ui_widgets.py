"""Reusable widgets: a reorderable, checkable file list and a file-picker dialog."""

from __future__ import annotations

from pathlib import Path

from PySide6 import QtCore, QtWidgets

from .library import LOADABLE

PATH_ROLE = QtCore.Qt.ItemDataRole.UserRole  # item data holding the full file path
FILE_FILTER = "Doom files (" + " ".join(f"*{e}" for e in sorted(LOADABLE)) + ");;All files (*)"


class FileList(QtWidgets.QListWidget):
    """A list of file paths with tick boxes, drag-to-reorder and file-manager drops.

    Items show the file name; the full path is the tooltip and stored in PATH_ROLE.
    Emits `changed` whenever order, ticks, or contents change (not while populating).
    """

    changed = QtCore.Signal()

    def __init__(self, parent=None, checkable: bool = True):
        super().__init__(parent)
        self._checkable = checkable
        self._quiet = False
        self.setDragDropMode(QtWidgets.QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(QtCore.Qt.DropAction.MoveAction)
        self.setAcceptDrops(True)
        self.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.itemChanged.connect(self._item_changed)

    # ---- content -------------------------------------------------------------
    def set_files(self, paths: list[str], disabled: list[str] | None = None) -> None:
        """Replace the list contents without emitting `changed`."""
        disabled_set = set(disabled or [])
        self._quiet = True
        self.clear()
        for path in paths:
            self._add_item(path, path not in disabled_set)
        self._quiet = False

    def add_files(self, paths: list[str]) -> None:
        """Append files that are not already listed, then emit `changed` once."""
        existing = set(self.paths())
        self._quiet = True
        for path in paths:
            if path not in existing:
                self._add_item(path, True)
                existing.add(path)
        self._quiet = False
        self.changed.emit()

    def _add_item(self, path: str, enabled: bool) -> None:
        item = QtWidgets.QListWidgetItem(Path(path).name)
        item.setData(PATH_ROLE, path)
        item.setToolTip(path)
        flags = item.flags() | QtCore.Qt.ItemFlag.ItemIsDragEnabled
        if self._checkable:
            flags |= QtCore.Qt.ItemFlag.ItemIsUserCheckable
            item.setCheckState(
                QtCore.Qt.CheckState.Checked if enabled else QtCore.Qt.CheckState.Unchecked
            )
        item.setFlags(flags)
        if not Path(path).is_file():
            item.setText(Path(path).name + "  (missing)")
        self.addItem(item)

    def paths(self) -> list[str]:
        """All file paths in list order."""
        return [self.item(i).data(PATH_ROLE) for i in range(self.count())]

    def disabled_paths(self) -> list[str]:
        """Paths whose tick box is off."""
        if not self._checkable:
            return []
        return [
            self.item(i).data(PATH_ROLE)
            for i in range(self.count())
            if self.item(i).checkState() != QtCore.Qt.CheckState.Checked
        ]

    # ---- actions used by the buttons ----------------------------------------
    def move_selected(self, delta: int) -> None:
        """Move the selected rows up (-1) or down (+1) by one."""
        rows = sorted(self.row(i) for i in self.selectedItems())
        if not rows:
            return
        if delta < 0 and rows[0] == 0 or delta > 0 and rows[-1] == self.count() - 1:
            return
        self._quiet = True
        for row in (rows if delta < 0 else reversed(rows)):
            item = self.takeItem(row)
            self.insertItem(row + delta, item)
            item.setSelected(True)
        self._quiet = False
        self.changed.emit()

    def remove_selected(self) -> None:
        for item in self.selectedItems():
            self.takeItem(self.row(item))
        self.changed.emit()

    def browse_and_add(self) -> None:
        """Let the user pick files with a dialog and append them."""
        names, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self, "Add files", "", FILE_FILTER
        )
        if names:
            self.add_files(names)

    # ---- signals and drag/drop ----------------------------------------------
    def _item_changed(self, _item) -> None:
        if not self._quiet:
            self.changed.emit()

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            # Files dragged in from a file manager: add the ones that are loadable.
            dropped = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
            self.add_files([p for p in dropped if Path(p).suffix.lower() in LOADABLE])
            event.acceptProposedAction()
        else:
            super().dropEvent(event)
            self.changed.emit()


class FileButtons(QtWidgets.QWidget):
    """Add / Up / Down / Remove buttons wired to a FileList."""

    def __init__(self, files: FileList, parent=None):
        super().__init__(parent)
        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        for text, slot in (
            ("Add files…", files.browse_and_add),
            ("Up", lambda: files.move_selected(-1)),
            ("Down", lambda: files.move_selected(1)),
            ("Remove", files.remove_selected),
        ):
            button = QtWidgets.QPushButton(text)
            button.clicked.connect(slot)
            row.addWidget(button)
        row.addStretch(1)


class PickFilesDialog(QtWidgets.QDialog):
    """Ask which of a project's files to use (e.g. LegenDoom ships three versions)."""

    def __init__(self, title: str, files: list[tuple[str, str]], parent=None):
        """files is a list of (label, full path); the first one starts ticked."""
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(520, 360)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel("This project has several files. Tick the ones to load:"))
        self.list = QtWidgets.QListWidget()
        for index, (label, path) in enumerate(files):
            item = QtWidgets.QListWidgetItem(label)
            item.setData(PATH_ROLE, path)
            item.setFlags(item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                QtCore.Qt.CheckState.Checked if index == 0 else QtCore.Qt.CheckState.Unchecked
            )
            self.list.addItem(item)
        layout.addWidget(self.list)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def chosen(self) -> list[str]:
        return [
            self.list.item(i).data(PATH_ROLE)
            for i in range(self.list.count())
            if self.list.item(i).checkState() == QtCore.Qt.CheckState.Checked
        ]
