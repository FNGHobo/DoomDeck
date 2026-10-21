"""The Settings dialog: engines, default data folders, and global options.

The dialog edits a deep copy of the config and only writes the changes back when the
user clicks OK, so Cancel always leaves everything untouched.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path

from PySide6 import QtCore, QtWidgets

from . import library
from .models import AppConfig, Engine
from .ui_widgets import FileButtons, FileList


# Settings field that says "give each preset its own subfolder", per folder kind.
SEPARATE_FIELD = {
    "config": "separate_config",
    "save": "separate_saves",
    "demo": "separate_demos",
    "screenshot": "separate_screenshots",
}


def _path_row(line_edit: QtWidgets.QLineEdit, pick_folder: bool, filter_text: str = "") -> QtWidgets.QWidget:
    """A line edit with a Browse… button next to it."""
    box = QtWidgets.QWidget()
    row = QtWidgets.QHBoxLayout(box)
    row.setContentsMargins(0, 0, 0, 0)
    row.addWidget(line_edit, 1)
    button = QtWidgets.QPushButton("Browse…")

    def browse() -> None:
        start = line_edit.text() or str(Path.home())
        if pick_folder:
            chosen = QtWidgets.QFileDialog.getExistingDirectory(box, "Choose folder", start)
        else:
            chosen, _ = QtWidgets.QFileDialog.getOpenFileName(box, "Choose file", start, filter_text)
        if chosen:
            line_edit.setText(chosen)

    button.clicked.connect(browse)
    row.addWidget(button)
    return box


class SettingsDialog(QtWidgets.QDialog):
    """Edit engines, default folders and global options."""

    def __init__(self, config: AppConfig, parent=None):
        super().__init__(parent)
        self.setWindowTitle("DoomDeck settings")
        self.resize(760, 560)
        self.config = copy.deepcopy(config)
        # Names engines had when the dialog opened, to update presets after a rename.
        self._original_names = [e.name for e in self.config.engines]
        self._loading = False
        # Folder names hidden from the library: a working copy that is written back
        # on OK. Names of folders that are not currently on disk are kept as they are.
        self._hidden = set(self.config.settings.hidden_categories)
        self._filling_folder_list = False

        layout = QtWidgets.QVBoxLayout(self)
        self.tabs = QtWidgets.QTabWidget()
        self._tab_pages = {}
        for key, title, build in (
            ("engines", "Engines", self._build_engines_tab),
            ("folders", "Folders", self._build_folders_tab),
            ("library", "Library", self._build_library_tab),
            ("general", "General", self._build_general_tab),
        ):
            self._tab_pages[key] = self.tabs.addTab(build(), title)
        layout.addWidget(self.tabs)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._fill_engine_list()
        self._load_folders()

    def show_tab(self, key: str) -> None:
        """Open the dialog on one of its tabs: engines, folders, library or general."""
        if key in self._tab_pages:
            self.tabs.setCurrentIndex(self._tab_pages[key])

    # ---- engines tab -----------------------------------------------------------
    def _build_engines_tab(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(page)

        left = QtWidgets.QVBoxLayout()
        self.engine_list = QtWidgets.QListWidget()
        self.engine_list.currentRowChanged.connect(self._engine_selected)
        left.addWidget(self.engine_list)
        add = QtWidgets.QPushButton("Add engine")
        add.clicked.connect(self._add_engine)
        remove = QtWidgets.QPushButton("Remove engine")
        remove.clicked.connect(self._remove_engine)
        left.addWidget(add)
        left.addWidget(remove)
        row.addLayout(left, 1)

        form = QtWidgets.QFormLayout()
        self.engine_name = QtWidgets.QLineEdit()
        self.engine_path = QtWidgets.QLineEdit()
        self.engine_args = QtWidgets.QLineEdit()
        self.engine_config_flag = QtWidgets.QLineEdit()
        self.engine_save_flag = QtWidgets.QLineEdit()
        self.engine_shot_flag = QtWidgets.QLineEdit()
        self.engine_status = QtWidgets.QLabel()
        self.engine_status.setWordWrap(True)
        form.addRow("Name", self.engine_name)
        form.addRow("Program", _path_row(self.engine_path, False))
        form.addRow("", self.engine_status)
        form.addRow("Extra arguments", self.engine_args)
        form.addRow("Config switch", self.engine_config_flag)
        form.addRow("Save folder switch", self.engine_save_flag)
        form.addRow("Screenshot switch", self.engine_shot_flag)
        hint = QtWidgets.QLabel(
            "Switches default to the ZDoom family (UZDoom, GZDoom, Zandronum). "
            "Clear a switch to stop passing it to this engine."
        )
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        form.addRow("", hint)
        holder = QtWidgets.QWidget()
        holder.setLayout(form)
        row.addWidget(holder, 3)

        for edit in (
            self.engine_name, self.engine_path, self.engine_args,
            self.engine_config_flag, self.engine_save_flag, self.engine_shot_flag,
        ):
            edit.textEdited.connect(self._engine_edited)
        return page

    def _fill_engine_list(self) -> None:
        self.engine_list.clear()
        for engine in self.config.engines:
            self.engine_list.addItem(engine.name)
        if self.config.engines:
            self.engine_list.setCurrentRow(0)
        else:
            self._engine_selected(-1)

    def _current_engine(self) -> Engine | None:
        row = self.engine_list.currentRow()
        return self.config.engines[row] if 0 <= row < len(self.config.engines) else None

    def _engine_selected(self, _row: int) -> None:
        engine = self._current_engine()
        self._loading = True
        for edit in (
            self.engine_name, self.engine_path, self.engine_args,
            self.engine_config_flag, self.engine_save_flag, self.engine_shot_flag,
        ):
            edit.setEnabled(engine is not None)
        self.engine_name.setText(engine.name if engine else "")
        self.engine_path.setText(engine.path if engine else "")
        self.engine_args.setText(engine.extra_args if engine else "")
        self.engine_config_flag.setText(engine.config_flag if engine else "")
        self.engine_save_flag.setText(engine.save_flag if engine else "")
        self.engine_shot_flag.setText(engine.screenshot_flag if engine else "")
        self._loading = False
        self._update_engine_status()

    def _engine_edited(self, _text: str) -> None:
        """Copy the form fields into the selected engine as the user types."""
        engine = self._current_engine()
        if engine is None or self._loading:
            return
        engine.name = self.engine_name.text().strip() or engine.name
        engine.path = self.engine_path.text().strip()
        engine.extra_args = self.engine_args.text()
        engine.config_flag = self.engine_config_flag.text().strip()
        engine.save_flag = self.engine_save_flag.text().strip()
        engine.screenshot_flag = self.engine_shot_flag.text().strip()
        item = self.engine_list.currentItem()
        if item is not None:
            item.setText(engine.name)
        self._update_engine_status()

    def _update_engine_status(self) -> None:
        """Show whether the engine program exists and can be run."""
        engine = self._current_engine()
        if engine is None:
            self.engine_status.setText("")
            return
        exe = Path(engine.path).expanduser()
        if not engine.path:
            text, style = "No program chosen.", "error"
        elif not exe.is_file():
            text, style = "File not found.", "error"
        elif not os.access(exe, os.X_OK):
            text, style = "Found, but not executable (run chmod +x on it).", "error"
        else:
            text, style = "Found and executable.", "muted"
        self.engine_status.setText(text)
        self.engine_status.setObjectName(style)
        self.engine_status.style().unpolish(self.engine_status)
        self.engine_status.style().polish(self.engine_status)

    def _add_engine(self) -> None:
        names = {e.name for e in self.config.engines}
        name, number = "New engine", 2
        while name in names:
            name, number = f"New engine {number}", number + 1
        self.config.engines.append(Engine(name=name, path=""))
        self._original_names.append(name)  # a brand new engine has nothing to rename
        self._fill_engine_list()
        self.engine_list.setCurrentRow(len(self.config.engines) - 1)

    def _remove_engine(self) -> None:
        row = self.engine_list.currentRow()
        if 0 <= row < len(self.config.engines):
            del self.config.engines[row]
            del self._original_names[row]
            self._fill_engine_list()

    # ---- folders tab -----------------------------------------------------------
    def _build_folders_tab(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(page)
        self.wads_root = QtWidgets.QLineEdit()
        self.iwads_dir = QtWidgets.QLineEdit()
        self.data_root = QtWidgets.QLineEdit()
        self.mapsets_category = QtWidgets.QLineEdit()
        self.mapsets_category.setPlaceholderText("maps")
        form.addRow("Wads folder (sorted library)", _path_row(self.wads_root, True))
        form.addRow("Mapsets folder name (inside it)", self.mapsets_category)
        form.addRow("IWAD folder", _path_row(self.iwads_dir, True))
        form.addRow("Data folder (parent of the four below)", _path_row(self.data_root, True))

        self.folder_edits = {}
        self.separate_boxes = {}
        grid = QtWidgets.QGridLayout()
        grid.addWidget(QtWidgets.QLabel("Folder name"), 0, 1)
        grid.addWidget(QtWidgets.QLabel("Own subfolder per preset"), 0, 2)
        for row, (key, label) in enumerate(
            (("config", "Engine config"), ("save", "Saves"), ("demo", "Demos"),
             ("screenshot", "Screenshots")), start=1
        ):
            grid.addWidget(QtWidgets.QLabel(label), row, 0)
            edit = QtWidgets.QLineEdit()
            box = QtWidgets.QCheckBox()
            self.folder_edits[key] = edit
            self.separate_boxes[key] = box
            grid.addWidget(edit, row, 1)
            grid.addWidget(box, row, 2)
            edit.textChanged.connect(self._update_example)
            box.toggled.connect(self._update_example)
        holder = QtWidgets.QWidget()
        holder.setLayout(grid)
        form.addRow("Default data folders", holder)

        self.seed_config = QtWidgets.QLineEdit()
        form.addRow("Starter config (optional)", _path_row(self.seed_config, False, "Config (*.ini);;All files (*)"))
        seed_hint = QtWidgets.QLabel(
            "Copied into a preset's config folder the first time it launches, so new "
            "presets keep your key bindings and video settings."
        )
        seed_hint.setObjectName("muted")
        seed_hint.setWordWrap(True)
        form.addRow("", seed_hint)

        self.example = QtWidgets.QLabel()
        self.example.setObjectName("muted")
        self.example.setWordWrap(True)
        form.addRow("Example", self.example)
        for edit in (self.data_root,):
            edit.textChanged.connect(self._update_example)
        return page

    def _load_folders(self) -> None:
        s = self.config.settings
        self.wads_root.setText(s.wads_root)
        self.iwads_dir.setText(s.iwads_dir)
        self.data_root.setText(s.data_root)
        self.mapsets_category.setText(s.mapsets_category)
        self.seed_config.setText(s.seed_config)
        for key in self.folder_edits:
            self.folder_edits[key].setText(getattr(s, f"{key}_folder"))
            self.separate_boxes[key].setChecked(getattr(s, SEPARATE_FIELD[key]))
        self.global_args.setText(s.global_args)
        self.global_wrapper.setText(s.global_wrapper)
        self.close_on_launch.setChecked(s.close_on_launch)
        self.autoload.set_files(s.autoload_files)
        self.extra_list.clear()
        self.extra_list.addItems(s.extra_folders)
        self._fill_folder_list()
        self._update_example()

    def _store_folders(self) -> None:
        s = self.config.settings
        s.wads_root = self.wads_root.text().strip()
        s.iwads_dir = self.iwads_dir.text().strip()
        s.data_root = self.data_root.text().strip()
        s.mapsets_category = self.mapsets_category.text().strip() or "maps"
        s.seed_config = self.seed_config.text().strip()
        for key in self.folder_edits:
            setattr(s, f"{key}_folder", self.folder_edits[key].text().strip() or key.capitalize())
            setattr(s, SEPARATE_FIELD[key], self.separate_boxes[key].isChecked())
        s.global_args = self.global_args.text()
        s.global_wrapper = self.global_wrapper.text().strip()
        s.close_on_launch = self.close_on_launch.isChecked()
        s.extra_folders = [self.extra_list.item(i).text() for i in range(self.extra_list.count())]
        s.hidden_categories = sorted(self._hidden)
        s.autoload_files = self.autoload.paths()

    def _update_example(self, *_args) -> None:
        """Show where a preset called 'Example' would keep its files."""
        if not hasattr(self, "example") or not self.folder_edits:
            return
        self._store_folders_preview()

    # Made-up location used only in the example text, so it shows the pattern and not
    # anyone's real folders.
    EXAMPLE_ROOT = Path("/games/Doomdeck")

    def _store_folders_preview(self) -> None:
        """Show the folder pattern with a generic root and <preset name> for a preset.

        The folder names and "own subfolder" ticks are read from the form, so the
        example follows what is typed; the real settings are never touched.
        """
        root = self.EXAMPLE_ROOT
        lines = []
        for label, key in (("Saves", "save"), ("Screenshots", "screenshot"),
                           ("Demos", "demo"), ("Config", "config")):
            path = root / (self.folder_edits[key].text().strip() or key.capitalize())
            if self.separate_boxes[key].isChecked():
                path = path / "<preset name>"
            lines.append(f"{label}: {path}")
        self.example.setText("\n".join(lines))

    # ---- library tab -----------------------------------------------------------
    def _build_library_tab(self) -> QtWidgets.QWidget:
        """Choose which folders the Library tab shows, and add folders from elsewhere."""
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)

        layout.addWidget(QtWidgets.QLabel("Folders inside the wads folder - untick one to hide it:"))
        self.folder_list = QtWidgets.QListWidget()
        self.folder_list.itemChanged.connect(self._folder_ticked)
        layout.addWidget(self.folder_list, 3)
        hide_hint = QtWidgets.QLabel(
            "Hiding only removes a folder from the library lists. Nothing is moved or "
            "deleted, and presets that already use its files keep working."
        )
        hide_hint.setObjectName("muted")
        hide_hint.setWordWrap(True)
        layout.addWidget(hide_hint)

        layout.addWidget(QtWidgets.QLabel("Extra folders - shown in the library as if they were inside the wads folder:"))
        self.extra_list = QtWidgets.QListWidget()
        layout.addWidget(self.extra_list, 2)
        row = QtWidgets.QHBoxLayout()
        add = QtWidgets.QPushButton("Add folder…")
        add.clicked.connect(self._add_extra_folder)
        remove = QtWidgets.QPushButton("Remove")
        remove.clicked.connect(self._remove_extra_folder)
        row.addWidget(add)
        row.addWidget(remove)
        row.addStretch(1)
        layout.addLayout(row)
        extra_hint = QtWidgets.QLabel(
            "A folder that contains sub-folders appears as a category of projects (like "
            "'maps'); a folder that holds wad/pk3 files itself appears as one project. "
            "Removing an extra folder here does not touch it on disk."
        )
        extra_hint.setObjectName("muted")
        extra_hint.setWordWrap(True)
        layout.addWidget(extra_hint)

        # The list of folders follows the wads folder box on the Folders tab.
        self.wads_root.textChanged.connect(self._fill_folder_list)
        return page

    def _fill_folder_list(self, *_args) -> None:
        """List the folders found in the wads folder, ticked unless they are hidden."""
        self._filling_folder_list = True
        self.folder_list.clear()
        for name in library.top_level_names(self.wads_root.text().strip()):
            item = QtWidgets.QListWidgetItem(name)
            item.setFlags(item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                QtCore.Qt.CheckState.Unchecked if name in self._hidden
                else QtCore.Qt.CheckState.Checked
            )
            self.folder_list.addItem(item)
        self._filling_folder_list = False

    def _folder_ticked(self, item: QtWidgets.QListWidgetItem) -> None:
        if self._filling_folder_list:
            return
        if item.checkState() == QtCore.Qt.CheckState.Checked:
            self._hidden.discard(item.text())
        else:
            self._hidden.add(item.text())

    def _add_extra_folder(self) -> None:
        start = self.wads_root.text().strip() or str(Path.home())
        chosen = QtWidgets.QFileDialog.getExistingDirectory(self, "Add a folder to the library", start)
        if not chosen:
            return
        existing = [self.extra_list.item(i).text() for i in range(self.extra_list.count())]
        if chosen not in existing:
            self.extra_list.addItem(chosen)

    def _remove_extra_folder(self) -> None:
        row = self.extra_list.currentRow()
        if row >= 0:
            self.extra_list.takeItem(row)

    # ---- general tab -----------------------------------------------------------
    def _build_general_tab(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        form = QtWidgets.QFormLayout()
        self.global_wrapper = QtWidgets.QLineEdit()
        self.global_wrapper.setPlaceholderText("e.g. gamemoderun  or  mangohud")
        self.global_args = QtWidgets.QLineEdit()
        self.global_args.setPlaceholderText("added to every launch")
        form.addRow("Wrapper program", self.global_wrapper)
        form.addRow("Extra arguments", self.global_args)
        self.close_on_launch = QtWidgets.QCheckBox("Close DoomDeck when the game launches")
        self.close_on_launch.setToolTip(
            "The game keeps running after DoomDeck quits. Play time is not tracked "
            "in this mode (the play count and last-played date still are)."
        )
        form.addRow("", self.close_on_launch)
        layout.addLayout(form)
        layout.addWidget(QtWidgets.QLabel("Autoload files (loaded first, on every launch):"))
        self.autoload = FileList(checkable=False)
        layout.addWidget(self.autoload, 1)
        layout.addWidget(FileButtons(self.autoload))
        return page

    # ---- finishing -------------------------------------------------------------
    def _accept(self) -> None:
        self._store_folders()
        names = [e.name for e in self.config.engines]
        if len(set(names)) != len(names):
            QtWidgets.QMessageBox.warning(self, "Engine names", "Each engine needs a unique name.")
            return
        self.accept()

    def result_config(self, original: AppConfig) -> AppConfig:
        """Return `original` updated with this dialog's choices (call after accept).

        Engines that were renamed get their presets updated to the new name, and
        presets pointing at a deleted engine are left as they are (the launcher
        warns about them) rather than being silently changed.
        """
        renames = {
            old: engine.name
            for old, engine in zip(self._original_names, self.config.engines)
            if old != engine.name
        }
        original.settings = self.config.settings
        original.engines = self.config.engines
        for preset in original.presets:
            if preset.engine in renames:
                preset.engine = renames[preset.engine]
        return original
