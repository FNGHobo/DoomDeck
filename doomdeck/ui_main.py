"""The DoomDeck main window.

Layout:
    left   : tabs Presets (saved setups), Mapsets (tick-box picker), Library (the
             sorted wads tree) and Get WADs (search and install from /idgames)
    right  : tabs Setup / Maps / Saves / Demos / Screenshots / Info / Output for the
             selected preset
    bottom : the command that will run, plus the Launch button

All state lives in `self.config` (see models.py); every edit is written to the JSON
file immediately, so nothing is lost if the program or the engine crashes.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from . import config as config_io
from . import launch, library, userdata, wadinfo
from .models import AppConfig, Preset
from .ui_common import SORTS, PICTURE_ICON
from .ui_library import LibraryMixin
from .ui_maps import MapsMixin
from .ui_online import OnlineMixin
from .ui_presets import PresetsMixin
from .ui_settings import SettingsDialog
from .ui_userdata import UserDataMixin
from .ui_widgets import FILE_FILTER


class MainWindow(PresetsMixin, LibraryMixin, OnlineMixin, MapsMixin,
                 UserDataMixin, QtWidgets.QMainWindow):
    def __init__(self, config: AppConfig, config_path: Path | None = None):
        super().__init__()
        self.setWindowTitle("DoomDeck")
        self.setAcceptDrops(True)  # drop files on the window to make a preset
        self.resize(1250, 780)
        self.config = config
        self.config_path = config_path
        self.current: Preset | None = None
        self._picture_cache: dict[tuple, QtGui.QPixmap | None] = {}
        self._loading = False  # true while widgets are filled from a preset
        self._process: subprocess.Popen | None = None
        self._started = 0.0
        self._running_preset: Preset | None = None
        self._log_path: Path | None = None
        # _library_all is everything found on disk; _library is what the Library tab
        # shows (hidden folders removed). The Mapsets tab reads the full list so the
        # maps folder still works if it is hidden from the Library.
        self._library_all: list[library.Category] = []
        self._library: list[library.Category] = []
        # Mapsets tab: files in the order they were ticked, who owns each file
        # (path -> (category, project)), and a flag that mutes the tree while it is rebuilt.
        self._mapset_order: list[str] = []
        self._mapset_owner: dict[str, tuple[str, str]] = {}
        self._mapset_building = False
        # Library projects with a newer version available (filled by Check for updates).
        self._updates: dict = {}
        self.log_dir = config_io.APP_DIR / "logs"

        self._build_ui()
        self._build_menu()
        self._poll_timer = QtCore.QTimer(self)
        self._poll_timer.timeout.connect(self._poll_process)
        self._poll_timer.start(1000)

        self.refresh_library()
        self.refresh_presets()
        self._select_preset(self.config.preset_named(self.config.last_preset))
        QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Return"), self, activated=self.launch_current)
        QtGui.QShortcut(QtGui.QKeySequence("Ctrl+F"), self, activated=self.focus_search)

    # ====================================================================== build
    def _build_ui(self) -> None:
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        outer = QtWidgets.QVBoxLayout(central)

        split = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        split.addWidget(self._build_left())
        split.addWidget(self._build_right())
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)
        outer.addWidget(split, 1)
        outer.addLayout(self._build_launch_bar())
        self.setStatusBar(QtWidgets.QStatusBar())

    # ---- left side: presets, mapsets, library, downloads ---------------------------------------
    def _build_left(self) -> QtWidgets.QWidget:
        tabs = QtWidgets.QTabWidget()

        # Presets tab
        presets = self.presets_page = QtWidgets.QWidget()
        col = QtWidgets.QVBoxLayout(presets)
        top = QtWidgets.QHBoxLayout()
        self.preset_search = QtWidgets.QLineEdit()
        self.preset_search.setPlaceholderText("Search presets…")
        self.preset_search.textChanged.connect(self.refresh_presets)
        self.preset_sort = QtWidgets.QComboBox()
        self.preset_sort.addItems(SORTS)
        self.preset_sort.currentIndexChanged.connect(self.refresh_presets)
        self.sort_reverse = QtWidgets.QPushButton("⇅")
        self.sort_reverse.setCheckable(True)
        self.sort_reverse.setFixedWidth(34)
        self.sort_reverse.setToolTip("Reverse the sort order")
        self.sort_reverse.toggled.connect(self.refresh_presets)
        top.addWidget(self.preset_search, 1)
        top.addWidget(self.preset_sort)
        top.addWidget(self.sort_reverse)
        col.addLayout(top)
        filters = QtWidgets.QHBoxLayout()
        self.favorites_only = QtWidgets.QCheckBox("Favorites only")
        self.favorites_only.toggled.connect(self.refresh_presets)
        self.tag_filter = QtWidgets.QComboBox()
        self.tag_filter.addItem("All tags", "")
        self.tag_filter.setToolTip("Show only presets with this tag")
        self.tag_filter.currentIndexChanged.connect(self.refresh_presets)
        filters.addWidget(self.favorites_only)
        filters.addStretch(1)
        filters.addWidget(self.tag_filter)
        col.addLayout(filters)
        self.preset_list = QtWidgets.QListWidget()
        self.preset_list.setIconSize(PICTURE_ICON)
        self.preset_list.currentItemChanged.connect(self._preset_item_changed)
        # Double-click launches, but never stops a game that is already running.
        self.preset_list.itemDoubleClicked.connect(lambda _i: self._launch_if_idle())
        col.addWidget(self.preset_list, 1)
        row = QtWidgets.QHBoxLayout()
        for text, slot in (
            ("New", self.new_preset), ("Duplicate", self.duplicate_preset),
            ("Rename", self.rename_preset), ("Delete", self.delete_preset),
        ):
            button = QtWidgets.QPushButton(text)
            button.clicked.connect(slot)
            row.addWidget(button)
        col.addLayout(row)
        row2 = QtWidgets.QHBoxLayout()
        for text, slot in (
            ("Import…", self.import_preset), ("Export…", self.export_preset),
            ("From command…", self.import_command),
        ):
            button = QtWidgets.QPushButton(text)
            button.clicked.connect(slot)
            row2.addWidget(button)
        col.addLayout(row2)
        tabs.addTab(presets, "Presets")
        self.mapsets_page = self._build_mapsets_tab()
        tabs.addTab(self.mapsets_page, "Mapsets")

        # Library tab
        lib = self.library_page = QtWidgets.QWidget()
        col = QtWidgets.QVBoxLayout(lib)
        top = QtWidgets.QHBoxLayout()
        self.lib_search = QtWidgets.QLineEdit()
        self.lib_search.setPlaceholderText("Search the library…")
        self.lib_search.textChanged.connect(self._filter_library)
        rescan = QtWidgets.QPushButton("Rescan")
        rescan.clicked.connect(self.refresh_library)
        folders = QtWidgets.QPushButton("Folders…")
        folders.setToolTip("Choose which folders the library shows, or add folders from elsewhere")
        folders.clicked.connect(lambda: self.open_settings("library"))
        check = QtWidgets.QPushButton("Updates")
        check.setToolTip("Check the projects that have an update source for newer versions")
        check.clicked.connect(lambda _c=False: self.check_for_updates())
        top.addWidget(self.lib_search, 1)
        top.addWidget(folders)
        top.addWidget(check)
        top.addWidget(rescan)
        col.addLayout(top)
        self.lib_favorites = QtWidgets.QCheckBox("Favorites only")
        self.lib_favorites.toggled.connect(self._filter_library)
        col.addWidget(self.lib_favorites)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._library_menu)
        self.tree.itemDoubleClicked.connect(self._library_double_click)
        self.tree.currentItemChanged.connect(self._library_selected)
        col.addWidget(self.tree, 1)
        self.lib_info = self._info_pane()
        col.addWidget(self.lib_info)
        hint = QtWidgets.QLabel("Double-click a project to add it to the current preset. Right-click for more.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        col.addWidget(hint)
        row = QtWidgets.QHBoxLayout()
        add = QtWidgets.QPushButton("Add to preset")
        add.clicked.connect(self._library_add_selected)
        new = QtWidgets.QPushButton("New preset from project")
        new.clicked.connect(self._library_new_from_selected)
        row.addWidget(add)
        row.addWidget(new)
        col.addLayout(row)
        tabs.addTab(lib, "Library")
        self.online_page = self._build_online_tab()
        tabs.addTab(self.online_page, "Get WADs")
        self.left_tabs = tabs
        return tabs

    # ---- right side: the preset editor ------------------------------------------
    def _build_right(self) -> QtWidgets.QWidget:
        self.right_tabs = QtWidgets.QTabWidget()
        self.setup_page = self._build_setup_tab()
        self.maps_page = self._build_maps_tab()
        self.shots_page = self._build_shots_tab()
        self.output_page = self._build_output_tab()
        self.right_tabs.addTab(self.setup_page, "Setup")
        self.right_tabs.addTab(self.maps_page, "Maps")
        self.right_tabs.addTab(self._build_saves_tab(), "Saves")
        self.right_tabs.addTab(self._build_demos_tab(), "Demos")
        self.right_tabs.addTab(self.shots_page, "Screenshots")
        self.right_tabs.addTab(self._build_info_tab(), "Info")
        self.right_tabs.addTab(self.output_page, "Output")
        self.right_tabs.currentChanged.connect(lambda _i: self._refresh_user_tabs())
        return self.right_tabs

    def _build_launch_bar(self) -> QtWidgets.QLayout:
        bar = QtWidgets.QHBoxLayout()
        self.command_edit = QtWidgets.QLineEdit()
        self.command_edit.setReadOnly(True)
        self.command_edit.setPlaceholderText("The command that will run appears here")
        copy_button = QtWidgets.QPushButton("Copy")
        copy_button.clicked.connect(
            lambda: QtWidgets.QApplication.clipboard().setText(self.command_edit.text())
        )
        self.close_check = QtWidgets.QCheckBox("Close on launch")
        self.close_check.setToolTip(
            "Quit DoomDeck as soon as the game starts. Play time is not tracked in this mode."
        )
        self.close_check.setChecked(self.config.settings.close_on_launch)
        self.close_check.toggled.connect(self._close_on_launch_toggled)
        self.continue_button = QtWidgets.QPushButton("Continue")
        self.continue_button.setToolTip("Load this preset's newest save and keep playing.")
        self.continue_button.clicked.connect(lambda _c=False: self.continue_last_save())
        self.continue_button.setEnabled(False)
        self.launch_button = QtWidgets.QPushButton("Launch")
        self.launch_button.setObjectName("launch")
        self.launch_button.clicked.connect(self.launch_current)
        bar.addWidget(self.command_edit, 1)
        bar.addWidget(copy_button)
        bar.addWidget(self.close_check)
        bar.addWidget(self.continue_button)
        bar.addWidget(self.launch_button)
        return bar

    def _close_on_launch_toggled(self, checked: bool) -> None:
        """Remember the launch-bar tick box."""
        self.config.settings.close_on_launch = checked
        self.save_config()

    def focus_search(self) -> None:
        """Ctrl+F: jump to the search box of the visible left tab."""
        page = self.left_tabs.currentWidget()
        box = {self.presets_page: self.preset_search, self.mapsets_page: self.map_search,
               self.library_page: self.lib_search, self.online_page: self.online_search}.get(page)
        if box is not None:
            box.setFocus()
            box.selectAll()

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        preset_menu = self.menuBar().addMenu("&Preset")
        tools_menu = self.menuBar().addMenu("&Tools")
        for menu, items in (
            (preset_menu, (
                ("&New…", self.new_preset, "Ctrl+N"),
                ("&Duplicate", self.duplicate_preset, "Ctrl+D"),
                ("&Rename…", self.rename_preset, "F2"),
                ("&Continue last save", self.continue_last_save, "Ctrl+Shift+Return"),
                ("Find &missing files", self.find_missing_files, ""),
                ("&Auto-sort load order", self.auto_sort_files, ""),
                ("Create desktop &shortcut", self.create_shortcut, ""),
                ("Check &all presets…", self.check_all_presets, ""),
                ("Check files against a &shared preset…", self.check_shared_preset, ""),
            )),
            (tools_menu, (
                ("Get &WADs from idgames…", self.show_online_tab, "Ctrl+Shift+G"),
                ("Check library for &updates…", self.check_for_updates, ""),
                ("Check for a new &engine version…", lambda: self.open_settings("engines"), ""),
            )),
            (file_menu, (
                ("&Quick play…", self.quick_play, "Ctrl+P"),
                ("&Settings…", self.open_settings, "Ctrl+,"),
                ("&Rescan library", self.refresh_library, "F5"),
                ("E&xit", self.close, "Ctrl+Q"),
            )),
        ):
            for text, slot, shortcut in items:
                action = QtGui.QAction(text, self)
                if shortcut:
                    action.setShortcut(QtGui.QKeySequence(shortcut))
                action.triggered.connect(lambda _checked=False, f=slot: f())
                menu.addAction(action)

    # ================================================================== persistence
    def save_config(self) -> None:
        """Write the config file; show an error in the status bar instead of crashing."""
        try:
            config_io.save(self.config, self.config_path)
        except OSError as error:
            self.statusBar().showMessage(f"Could not save settings: {error}", 8000)

    def _iwads_now(self) -> list[Path]:
        return library.scan_iwads(self.config.settings.iwads_dir)

    # =================================================================== launching
    def launch_current(self, *_args, load_save: str | None = None, play_demo: str | None = None,
                       map_override: str | None = None) -> None:
        """Start the engine for the current preset (or stop it if already running)."""
        if self._process is not None:
            self._process.terminate()
            return
        if self.current is None:
            return
        problems = launch.check(self.config, self.current)
        errors = [x.text for x in problems if x.level == "error"]
        if errors:
            QtWidgets.QMessageBox.warning(self, "Cannot launch", "\n".join(errors))
            return
        options = {}
        if load_save:
            options["load_save"] = load_save
        if play_demo:
            options["play_demo"] = play_demo
        if map_override:
            options["map_override"] = map_override
        close = self.config.settings.close_on_launch
        try:
            # When closing afterwards the game gets its own session, so it survives us.
            process, command, log_path = launch.launch(
                self.config, self.current, self.log_dir, detach=close, **options
            )
        except (OSError, ValueError) as error:
            QtWidgets.QMessageBox.critical(self, "Launch failed", str(error))
            return
        if close:
            # We will not be around to see it end, so count the session now (no play time).
            now = time.time()
            launch.record_session(self.current, now, now)
            self.save_config()
            self.close()  # no game is tracked, so closeEvent does not ask for confirmation
            return
        self._process, self._log_path = process, log_path
        self._started = time.time()
        self._running_preset = self.current
        self.launch_button.setText("Stop")
        self.launch_button.setEnabled(True)
        self.continue_button.setEnabled(False)
        self.statusBar().showMessage("Running: " + self.current.name)
        self.output_text.setPlainText("$ " + launch.format_command(command) + "\n")
        self._refresh_maps_tab()  # the launch counted as a play of its start map

    def continue_last_save(self) -> None:
        """Start the current preset by loading its newest save."""
        if self.current is None or self._process is not None:
            return
        latest = userdata.latest_save(launch.data_dirs(self.config.settings, self.current).saves)
        if latest is None:
            QtWidgets.QMessageBox.information(self, "Continue", "This preset has no saves yet.")
            return
        self.launch_current(load_save=str(latest.path))

    def _launch_if_idle(self) -> None:
        """Launch the current preset, but do nothing if a game is already running."""
        if self._process is None:
            self.launch_current()

    def _poll_process(self) -> None:
        """Once a second: update the Output tab and notice when the engine exits."""
        if self._process is None:
            return
        self._show_log()
        code = self._process.poll()
        if code is None:
            return
        preset = self._running_preset
        if preset is not None:
            launch.record_session(preset, self._started)
        self._process = None
        self.launch_button.setText("Launch")
        self.statusBar().showMessage(f"Engine exited with code {code}", 8000)
        self._show_log()
        self.save_config()
        self.refresh_presets()
        self._update_derived()
        self._refresh_user_tabs()
        if code != 0:
            self.right_tabs.setCurrentWidget(self.output_page)  # show Output so the failure is visible

    def _show_log(self) -> None:
        if self._log_path is None:
            return
        try:
            text = self._log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        lines = text.splitlines()[-400:]  # keep the view light for chatty engines
        header = self.output_text.toPlainText().split("\n", 1)[0]
        new_text = header + "\n" + "\n".join(lines)
        if new_text == self.output_text.toPlainText():
            return  # nothing new: leave the view (and any selection or scrolling) alone
        bar = self.output_text.verticalScrollBar()
        follow = bar.value() >= bar.maximum() - 4  # only auto-scroll if already at the bottom
        self.output_text.setPlainText(new_text)
        if follow:
            bar.setValue(bar.maximum())

    def quick_play(self) -> None:
        """Pick files and play them right away using a reusable 'Quick Play' preset."""
        names, _ = QtWidgets.QFileDialog.getOpenFileNames(self, "Quick play: choose files", "", FILE_FILTER)
        if not names:
            return
        preset = self.config.preset_named("Quick Play")
        if preset is None:
            preset = self._default_preset("Quick Play", names)
            self.config.presets.append(preset)
        else:
            preset.files = list(names)
            preset.disabled_files = []
            preset.warp = ""  # a start map from the previous Quick Play may not exist now
            preset.iwad = wadinfo.guess_iwad(names, self._iwads_now()) or preset.iwad
        self.refresh_presets()
        self._select_preset(preset)
        self.launch_current()

    # ==================================================================== settings
    def open_settings(self, tab: str | None = None) -> None:
        """Open the Settings dialog, optionally on one tab (engines/folders/library/general)."""
        dialog = SettingsDialog(self.config, self)
        if tab:
            dialog.show_tab(tab)
        if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            self.config = dialog.result_config(self.config)
            self.save_config()
            self.refresh_library()
            self.refresh_presets()
            self._select_preset(self.current)

    def closeEvent(self, event) -> None:
        """Save, and warn before closing while a game is still running."""
        if self._process is not None:
            answer = QtWidgets.QMessageBox.question(
                self, "Quit", "The game is still running. Quit DoomDeck anyway?"
            )
            if answer != QtWidgets.QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self.save_config()
        event.accept()

