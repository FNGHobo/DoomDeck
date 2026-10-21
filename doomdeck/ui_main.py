"""The DoomDeck main window.

Layout:
    left   : tabs "Presets" (saved setups) and "Library" (the sorted wads tree)
    right  : tabs Setup / Saves / Demos / Screenshots / Info / Output for the
             selected preset
    bottom : the command that will run, plus the Launch button

All state lives in `self.config` (see models.py); every edit is written to the JSON
file immediately, so nothing is lost if the program or the engine crashes.
"""

from __future__ import annotations

import copy
import json
import subprocess
import time
from dataclasses import asdict
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from . import config as config_io
from . import importer, launch, library, userdata, wadinfo
from .models import AppConfig, Preset, parse_tags, safe_name, sort_presets
from .ui_settings import SettingsDialog
from .ui_widgets import FILE_FILTER, FileButtons, FileList, PickFilesDialog, PATH_ROLE

SKILLS = ["Engine default", "1 - I'm too young to die", "2 - Hey, not too rough",
          "3 - Hurt me plenty", "4 - Ultra-violence", "5 - Nightmare!"]
SORTS = ["Name", "Last played", "Most played", "Times launched", "Favorites first"]
# Preset picture sizes: stored copy limit, Setup tab box, preset list icon.
PICTURE_MAX = QtCore.QSize(640, 400)
PICTURE_BOX = QtCore.QSize(160, 100)
PICTURE_ICON = QtCore.QSize(64, 40)


def add_action(menu: QtWidgets.QMenu, text: str, slot) -> QtGui.QAction:
    """Add a menu entry that calls slot() with no arguments.

    triggered() sends a 'checked' flag; swallowing it here means lambdas with default
    arguments are never handed that flag by mistake.
    """
    action = QtGui.QAction(text, menu)
    action.triggered.connect(lambda _checked=False: slot())
    menu.addAction(action)
    return action


def _format_time(seconds: int) -> str:
    """12345 -> '3h 25m' (for play time labels)."""
    hours, rest = divmod(int(seconds), 3600)
    return f"{hours}h {rest // 60}m" if hours else f"{rest // 60}m"


def _format_date(stamp: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(stamp)) if stamp else "never"


class MainWindow(QtWidgets.QMainWindow):
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

    # ---- left side: presets and library ---------------------------------------
    def _build_left(self) -> QtWidgets.QWidget:
        tabs = QtWidgets.QTabWidget()

        # Presets tab
        presets = QtWidgets.QWidget()
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
        tabs.addTab(self._build_mapsets_tab(), "Mapsets")

        # Library tab
        lib = QtWidgets.QWidget()
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
        top.addWidget(self.lib_search, 1)
        top.addWidget(folders)
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
        self.left_tabs = tabs
        return tabs

    # ---- right side: the preset editor ------------------------------------------
    def _build_right(self) -> QtWidgets.QWidget:
        self.right_tabs = QtWidgets.QTabWidget()
        self.right_tabs.addTab(self._build_setup_tab(), "Setup")
        self.right_tabs.addTab(self._build_saves_tab(), "Saves")
        self.right_tabs.addTab(self._build_demos_tab(), "Demos")
        self.right_tabs.addTab(self._build_shots_tab(), "Screenshots")
        self.right_tabs.addTab(self._build_info_tab(), "Info")
        self.right_tabs.addTab(self._build_output_tab(), "Output")
        self.right_tabs.currentChanged.connect(lambda _i: self._refresh_user_tabs())
        return self.right_tabs

    def _build_setup_tab(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        col = QtWidgets.QVBoxLayout(page)

        head = QtWidgets.QHBoxLayout()
        self.picture_label = QtWidgets.QLabel("No picture")
        self.picture_label.setFixedSize(PICTURE_BOX)
        self.picture_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.picture_label.setObjectName("muted")
        self.picture_label.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        head.addWidget(self.picture_label)
        side = QtWidgets.QVBoxLayout()
        self.stats_label = QtWidgets.QLabel()
        self.stats_label.setObjectName("muted")
        side.addWidget(self.stats_label)
        buttons = QtWidgets.QHBoxLayout()
        set_picture = QtWidgets.QPushButton("Set picture...")
        set_picture.clicked.connect(lambda _c=False: self.choose_picture())
        self.remove_picture_button = QtWidgets.QPushButton("Remove picture")
        self.remove_picture_button.clicked.connect(lambda _c=False: self.clear_picture())
        buttons.addWidget(set_picture)
        buttons.addWidget(self.remove_picture_button)
        shortcut = QtWidgets.QPushButton("Desktop shortcut")
        shortcut.setToolTip(
            "Add this preset to your application menu, so it starts the game directly "
            "without opening DoomDeck."
        )
        shortcut.clicked.connect(lambda _c=False: self.create_shortcut())
        buttons.addWidget(shortcut)
        buttons.addStretch(1)
        side.addLayout(buttons)
        side.addStretch(1)
        head.addLayout(side, 1)
        col.addLayout(head)

        form = QtWidgets.QFormLayout()
        self.engine_combo = QtWidgets.QComboBox()
        self.iwad_combo = QtWidgets.QComboBox()
        self.skill_combo = QtWidgets.QComboBox()
        self.skill_combo.addItems(SKILLS)
        self.map_combo = QtWidgets.QComboBox()
        self.map_combo.setEditable(True)
        self.map_combo.lineEdit().setPlaceholderText("start normally")
        self.wrapper_edit = QtWidgets.QLineEdit()
        self.wrapper_edit.setPlaceholderText("empty = use the global wrapper")
        self.args_edit = QtWidgets.QLineEdit()
        self.args_edit.setPlaceholderText("extra engine arguments for this preset")
        self.tags_edit = QtWidgets.QLineEdit()
        self.tags_edit.setPlaceholderText("labels separated by commas, e.g. co-op, slaughter")
        self.tags_edit.editingFinished.connect(self._tags_edited)
        form.addRow("Engine", self.engine_combo)
        form.addRow("IWAD", self.iwad_combo)
        form.addRow("Skill", self.skill_combo)

        # Engine config (.ini): pick one of the preset's configs or make a new one.
        self.config_combo = QtWidgets.QComboBox()
        self.config_combo.setToolTip(
            "Which engine config file this preset uses (key bindings, video settings...)."
        )
        new_config = QtWidgets.QPushButton("New…")
        new_config.setToolTip("Create another config for this preset")
        new_config.clicked.connect(self.new_config)
        open_config = QtWidgets.QPushButton("Open folder")
        open_config.clicked.connect(lambda: self._open_dir("config"))
        config_row = QtWidgets.QWidget()
        config_layout = QtWidgets.QHBoxLayout(config_row)
        config_layout.setContentsMargins(0, 0, 0, 0)
        config_layout.addWidget(self.config_combo, 1)
        config_layout.addWidget(new_config)
        import_config = QtWidgets.QPushButton("Import...")
        import_config.setToolTip(
            "Copy an existing engine config into this preset: the engine's own config, "
            "another preset's config, or any .ini file."
        )
        import_config.clicked.connect(lambda _c=False: self.import_config())
        config_layout.addWidget(import_config)
        config_layout.addWidget(open_config)
        form.addRow("Config", config_row)

        form.addRow("Start map", self.map_combo)
        form.addRow("Wrapper", self.wrapper_edit)
        form.addRow("Extra arguments", self.args_edit)
        form.addRow("Tags", self.tags_edit)
        col.addLayout(form)

        toggles = QtWidgets.QGridLayout()
        self.checks = {}
        for index, (key, text) in enumerate((
            ("favorite", "Favorite"), ("use_autoload", "Use autoload files"),
            ("record_demo", "Record demo"), ("no_monsters", "No monsters"),
            ("fast", "Fast monsters"), ("respawn", "Respawn monsters"),
            ("no_sound", "No sound"), ("no_music", "No music"),
        )):
            box = QtWidgets.QCheckBox(text)
            box.toggled.connect(self._form_changed)
            self.checks[key] = box
            toggles.addWidget(box, index // 4, index % 4)
        col.addLayout(toggles)

        res = QtWidgets.QHBoxLayout()
        self.width_spin = QtWidgets.QSpinBox()
        self.height_spin = QtWidgets.QSpinBox()
        for spin in (self.width_spin, self.height_spin):
            spin.setRange(0, 16000)
            spin.setSpecialValueText("auto")
            spin.valueChanged.connect(self._form_changed)
        res.addWidget(QtWidgets.QLabel("Resolution"))
        res.addWidget(self.width_spin)
        res.addWidget(QtWidgets.QLabel("x"))
        res.addWidget(self.height_spin)
        res.addStretch(1)
        col.addLayout(res)

        col.addWidget(self._build_multiplayer_box())

        col.addWidget(QtWidgets.QLabel("Load order (tick to enable, drag to reorder, drop files here):"))
        self.file_list = FileList()
        self.file_list.changed.connect(self._files_changed)
        col.addWidget(self.file_list, 1)
        file_row = QtWidgets.QHBoxLayout()
        file_row.addWidget(FileButtons(self.file_list))
        find_button = QtWidgets.QPushButton("Find missing files")
        find_button.setToolTip(
            "Look for files that moved (for example when sorting the wads folder) "
            "and point the preset at their new location."
        )
        find_button.clicked.connect(lambda _c=False: self.find_missing_files())
        file_row.addWidget(find_button)
        sort_button = QtWidgets.QPushButton("Auto-sort")
        sort_button.setToolTip(
            "Order the files by kind: audio and visual resources first, then mods, then "
            "maps (files keep their relative order within a kind)."
        )
        sort_button.clicked.connect(lambda _c=False: self.auto_sort_files())
        file_row.addWidget(sort_button)
        file_row.addStretch(1)
        col.addLayout(file_row)

        self.notes_edit = QtWidgets.QPlainTextEdit()
        self.notes_edit.setPlaceholderText("Notes about this preset…")
        self.notes_edit.setMaximumHeight(60)
        self.notes_edit.textChanged.connect(self._form_changed)
        col.addWidget(self.notes_edit)

        self.problem_label = QtWidgets.QLabel()
        self.problem_label.setWordWrap(True)
        col.addWidget(self.problem_label)

        for combo in (self.engine_combo, self.iwad_combo, self.skill_combo, self.config_combo):
            combo.currentIndexChanged.connect(self._form_changed)
        # Connected after _form_changed on purpose: it runs once the preset has the new
        # engine, because the default config's file name depends on the engine.
        self.engine_combo.currentIndexChanged.connect(self._engine_changed)
        self.map_combo.editTextChanged.connect(self._form_changed)
        self.wrapper_edit.textEdited.connect(self._form_changed)
        self.args_edit.textEdited.connect(self._form_changed)
        return page

    def _build_multiplayer_box(self) -> QtWidgets.QWidget:
        """Host or join a network game (ZDoom-family switches); off by default."""
        box = QtWidgets.QGroupBox("Multiplayer")
        grid = QtWidgets.QGridLayout(box)

        self.net_mode = QtWidgets.QComboBox()
        self.net_mode.addItem("Off (single player)", "")
        self.net_mode.addItem("Host a game", "host")
        self.net_mode.addItem("Join a game", "join")
        self.net_players = QtWidgets.QSpinBox()
        self.net_players.setRange(2, 8)
        self.net_game = QtWidgets.QComboBox()
        self.net_game.addItem("Co-op", "")
        self.net_game.addItem("Deathmatch", "deathmatch")
        self.net_game.addItem("Alt deathmatch", "altdeath")
        self.net_address = QtWidgets.QLineEdit()
        self.net_address.setPlaceholderText("the host's IP address or name")
        self.net_port = QtWidgets.QSpinBox()
        self.net_port.setRange(0, 65535)
        self.net_port.setSpecialValueText("default")

        grid.addWidget(QtWidgets.QLabel("Mode"), 0, 0)
        grid.addWidget(self.net_mode, 0, 1)
        grid.addWidget(QtWidgets.QLabel("Players"), 0, 2)
        grid.addWidget(self.net_players, 0, 3)
        grid.addWidget(QtWidgets.QLabel("Game"), 0, 4)
        grid.addWidget(self.net_game, 0, 5)
        grid.addWidget(QtWidgets.QLabel("Address"), 1, 0)
        grid.addWidget(self.net_address, 1, 1, 1, 3)
        grid.addWidget(QtWidgets.QLabel("Port"), 1, 4)
        grid.addWidget(self.net_port, 1, 5)
        hint = QtWidgets.QLabel(
            "Everyone needs the same engine version, IWAD, and files in the same load "
            "order (use Copy on the command bar to share them). The host chooses the "
            "players, game type, skill and map; the default port is 5029 (UDP)."
        )
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        grid.addWidget(hint, 2, 0, 1, 6)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(5, 1)

        for combo in (self.net_mode, self.net_game):
            combo.currentIndexChanged.connect(self._form_changed)
        for spin in (self.net_players, self.net_port):
            spin.valueChanged.connect(self._form_changed)
        self.net_address.textEdited.connect(self._form_changed)
        return box

    def _update_net_enabled(self) -> None:
        """Grey out the multiplayer fields that do not apply to the chosen mode."""
        mode = self.net_mode.currentData()
        self.net_players.setEnabled(mode == "host")
        self.net_game.setEnabled(mode == "host")
        self.net_address.setEnabled(mode == "join")
        self.net_port.setEnabled(bool(mode))

    def _build_user_list_tab(self, buttons: list[tuple[str, object]]):
        """A list plus a row of buttons, shared by the Saves and Demos tabs."""
        page = QtWidgets.QWidget()
        col = QtWidgets.QVBoxLayout(page)
        listing = QtWidgets.QListWidget()
        col.addWidget(listing, 1)
        row = QtWidgets.QHBoxLayout()
        for text, slot in buttons:
            button = QtWidgets.QPushButton(text)
            button.clicked.connect(slot)
            row.addWidget(button)
        row.addStretch(1)
        col.addLayout(row)
        return page, listing

    def _build_saves_tab(self) -> QtWidgets.QWidget:
        page, self.saves_list = self._build_user_list_tab([
            ("Load selected save", self._load_selected_save),
            ("Open folder", lambda: self._open_dir("saves")),
            ("Delete", lambda: self._delete_selected(self.saves_list)),
        ])
        self.saves_list.itemDoubleClicked.connect(lambda _i: self._load_selected_save())
        return page

    def _build_demos_tab(self) -> QtWidgets.QWidget:
        page, self.demos_list = self._build_user_list_tab([
            ("Play selected demo", self._play_selected_demo),
            ("Open folder", lambda: self._open_dir("demos")),
            ("Delete", lambda: self._delete_selected(self.demos_list)),
        ])
        self.demos_list.itemDoubleClicked.connect(lambda _i: self._play_selected_demo())
        return page

    def _build_shots_tab(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        col = QtWidgets.QVBoxLayout(page)
        self.shots_list = QtWidgets.QListWidget()
        self.shots_list.setViewMode(QtWidgets.QListView.ViewMode.IconMode)
        self.shots_list.setIconSize(QtCore.QSize(180, 112))
        self.shots_list.setResizeMode(QtWidgets.QListView.ResizeMode.Adjust)
        self.shots_list.setMovement(QtWidgets.QListView.Movement.Static)
        self.shots_list.setWordWrap(True)
        self.shots_list.itemDoubleClicked.connect(
            lambda item: QtGui.QDesktopServices.openUrl(
                QtCore.QUrl.fromLocalFile(item.data(PATH_ROLE))
            )
        )
        self.shots_list.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.shots_list.customContextMenuRequested.connect(self._shot_menu)
        col.addWidget(self.shots_list, 1)
        row = QtWidgets.QHBoxLayout()
        open_button = QtWidgets.QPushButton("Open folder")
        open_button.clicked.connect(lambda: self._open_dir("screenshots"))
        row.addWidget(open_button)
        use_button = QtWidgets.QPushButton("Use as preset picture")
        use_button.clicked.connect(lambda _c=False: self._use_selected_shot())
        row.addWidget(use_button)
        row.addStretch(1)
        col.addLayout(row)
        return page

    def _build_info_tab(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        col = QtWidgets.QVBoxLayout(page)
        self.info_combo = QtWidgets.QComboBox()
        self.info_combo.currentIndexChanged.connect(self._show_info)
        self.info_text = QtWidgets.QPlainTextEdit()
        self.info_text.setReadOnly(True)
        col.addWidget(self.info_combo)
        col.addWidget(self.info_text, 1)
        return page

    def _build_output_tab(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        col = QtWidgets.QVBoxLayout(page)
        self.output_text = QtWidgets.QPlainTextEdit()
        self.output_text.setReadOnly(True)
        self.output_text.setPlaceholderText("Engine output of the last launch appears here.")
        col.addWidget(self.output_text, 1)
        return page

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
            "Quit DoomDeck as soon as the game starts (also found in Settings → General)."
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
        """Keep the launch-bar tick box and the Settings option in step, and save."""
        self.config.settings.close_on_launch = checked
        self.save_config()

    def check_all_presets(self) -> None:
        """Report which presets have problems (missing files, engine, IWAD...)."""
        found = launch.audit(self.config)
        if not found:
            QtWidgets.QMessageBox.information(
                self, "Check all presets", f"All {len(self.config.presets)} preset(s) look fine."
            )
            return
        lines = []
        for preset, problems in found:
            lines.append(preset.name)
            lines += [f"    {'✖' if x.level == 'error' else '⚠'} {x.text}" for x in problems]
        QtWidgets.QMessageBox.warning(
            self, "Check all presets",
            f"{len(found)} of {len(self.config.presets)} preset(s) have problems:\n\n" + "\n".join(lines),
        )

    def focus_search(self) -> None:
        """Ctrl+F: jump to the search box of the visible left tab."""
        box = {0: self.preset_search, 2: self.lib_search}.get(self.left_tabs.currentIndex())
        if box is not None:
            box.setFocus()
            box.selectAll()

    def _build_menu(self) -> None:
        menu = self.menuBar().addMenu("&File")
        preset_menu = self.menuBar().addMenu("&Preset")
        for text, slot, shortcut in (
            ("&New…", self.new_preset, "Ctrl+N"),
            ("&Duplicate", self.duplicate_preset, "Ctrl+D"),
            ("&Rename…", self.rename_preset, "F2"),
            ("&Continue last save", self.continue_last_save, "Ctrl+Shift+Return"),
            ("Find &missing files", self.find_missing_files, ""),
            ("&Auto-sort load order", self.auto_sort_files, ""),
            ("Create desktop &shortcut", self.create_shortcut, ""),
            ("Check &all presets…", self.check_all_presets, ""),
        ):
            action = QtGui.QAction(text, self)
            if shortcut:
                action.setShortcut(QtGui.QKeySequence(shortcut))
            action.triggered.connect(lambda _checked=False, f=slot: f())
            preset_menu.addAction(action)
        for text, slot, shortcut in (
            ("&Quick play…", self.quick_play, "Ctrl+P"),
            ("&Settings…", self.open_settings, "Ctrl+,"),
            ("&Rescan library", self.refresh_library, "F5"),
            ("E&xit", self.close, "Ctrl+Q"),
        ):
            action = QtGui.QAction(text, self)
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

    # ===================================================================== presets
    def _refresh_tag_filter(self) -> None:
        """Rebuild the tag filter from the tags in use, keeping the current choice."""
        chosen = self.tag_filter.currentData() or ""
        tags: dict[str, str] = {}
        for preset in self.config.presets:
            for tag in preset.tags:
                tags.setdefault(tag.lower(), tag)
        self.tag_filter.blockSignals(True)
        self.tag_filter.clear()
        self.tag_filter.addItem("All tags", "")
        for key in sorted(tags):
            self.tag_filter.addItem(tags[key], tags[key])
        self.tag_filter.setCurrentIndex(max(0, self.tag_filter.findData(chosen)))
        self.tag_filter.blockSignals(False)

    def _tags_edited(self) -> None:
        """Save the tags typed on the Setup tab (when the field is left)."""
        if self._loading or self.current is None:
            return
        tags = parse_tags(self.tags_edit.text())
        if tags != self.current.tags:
            self.current.tags = tags
            self.save_config()
            self.refresh_presets()
        self.tags_edit.setText(", ".join(tags))

    def refresh_presets(self, *_args) -> None:
        """Rebuild the preset list from the config, applying search, sort and filter."""
        self._refresh_tag_filter()
        query = self.preset_search.text().strip().lower()
        tag = self.tag_filter.currentData() or ""
        presets = [
            p for p in self.config.presets
            if (not query or p.matches(query))
            and (not self.favorites_only.isChecked() or p.favorite)
            and (not tag or p.has_tag(tag))
        ]
        presets = sort_presets(presets, self.preset_sort.currentText(), self.sort_reverse.isChecked())
        self.preset_list.blockSignals(True)
        self.preset_list.clear()
        for preset in presets:
            problems = launch.check(self.config, preset)
            level = launch.worst_level(problems)
            marker = {"error": "✖ ", "warning": "⚠ "}.get(level, "")
            item = QtWidgets.QListWidgetItem(("★ " if preset.favorite else "") + marker + preset.name)
            item.setData(PATH_ROLE, preset.name)
            tooltip = self._preset_tooltip(preset)
            if problems:
                tooltip += "\n\n" + "\n".join(x.text for x in problems)
            item.setToolTip(tooltip)
            icon = self._picture_pixmap(preset.picture, PICTURE_ICON)
            if icon is not None:
                item.setIcon(QtGui.QIcon(icon))
            self.preset_list.addItem(item)
            if self.current is not None and preset.name == self.current.name:
                self.preset_list.setCurrentItem(item)
        self.preset_list.blockSignals(False)

    def _preset_item_changed(self, item, _previous) -> None:
        self._select_preset(self.config.preset_named(item.data(PATH_ROLE)) if item else None)

    def _select_preset(self, preset: Preset | None) -> None:
        """Make `preset` the one being edited and fill the widgets from it."""
        self.current = preset
        self.config.last_preset = preset.name if preset else ""
        self._loading = True
        enabled = preset is not None
        self.right_tabs.setEnabled(enabled)
        self.launch_button.setEnabled(enabled or self._process is not None)

        # Engine / IWAD choices come from the config and the IWAD folder.
        self.engine_combo.clear()
        self.engine_combo.addItems([e.name for e in self.config.engines])
        self.iwad_combo.clear()
        self._iwads = library.scan_iwads(self.config.settings.iwads_dir)
        self.iwad_combo.addItems([p.name for p in self._iwads])

        if preset:
            self._set_combo(self.engine_combo, preset.engine)
            self._fill_config_combo(preset)
            self._set_combo(self.iwad_combo, preset.iwad)
            self.skill_combo.setCurrentIndex(max(0, min(5, preset.skill)))
            self._fill_map_combo(preset)
            self.wrapper_edit.setText(preset.wrapper)
            self.args_edit.setText(preset.extra_args)
            self.tags_edit.setText(", ".join(preset.tags))
            for key, box in self.checks.items():
                box.setChecked(bool(getattr(preset, key)))
            self.width_spin.setValue(preset.width)
            self.height_spin.setValue(preset.height)
            self.net_mode.setCurrentIndex(max(0, self.net_mode.findData(preset.net_mode)))
            self.net_players.setValue(preset.net_players)
            self.net_game.setCurrentIndex(max(0, self.net_game.findData(preset.net_game)))
            self.net_address.setText(preset.net_address)
            self.net_port.setValue(preset.net_port)
            self.file_list.set_files(preset.files, preset.disabled_files)
            self.notes_edit.setPlainText(preset.notes)
        else:
            self.config_combo.clear()
            self.tags_edit.setText("")
            self.file_list.set_files([])
            self.notes_edit.setPlainText("")
        self._loading = False
        self._update_derived()
        self._fill_info_combo()
        self._refresh_user_tabs()
        self._highlight_current_preset()
        self.save_config()

    def _highlight_current_preset(self) -> None:
        """Make the preset list show the current preset as selected (without re-selecting)."""
        self.preset_list.blockSignals(True)
        self.preset_list.setCurrentItem(None)
        for row in range(self.preset_list.count()):
            item = self.preset_list.item(row)
            if self.current is not None and item.data(PATH_ROLE) == self.current.name:
                self.preset_list.setCurrentItem(item)
                break
        self.preset_list.blockSignals(False)

    @staticmethod
    def _set_combo(combo: QtWidgets.QComboBox, text: str) -> None:
        """Select `text` in a combo, adding it (marked missing) if it is not listed."""
        index = combo.findText(text)
        if index < 0 and text:
            combo.addItem(text)
            index = combo.count() - 1
        combo.setCurrentIndex(index)

    def _fill_config_combo(self, preset: Preset) -> None:
        """List the configs the preset can use: the default first, then every .ini found.

        Files from the shared config folder are marked "(shared)". A config the preset
        names but that no longer exists stays in the list, marked "(missing)", so the
        choice is not silently changed. Call with _loading set (or via
        _refresh_config_combo), because it changes the combo's selection.
        """
        settings = self.config.settings
        engine = self.config.engine_named(preset.engine)
        default_name = launch.default_config_name(engine) if engine else "engine.ini"
        own_folder = launch.data_dirs(settings, preset).config
        chosen = "" if preset.config == default_name else preset.config

        self.config_combo.blockSignals(True)
        self.config_combo.clear()
        self.config_combo.addItem(f"Default ({default_name})", "")
        for path in launch.list_configs(settings, preset):
            if path.name == default_name:
                continue  # already offered as "Default"
            label = path.name if path.parent == own_folder else f"{path.name}  (shared)"
            self.config_combo.addItem(label, path.name)
        index = self.config_combo.findData(chosen)
        if index < 0:
            self.config_combo.addItem(f"{chosen}  (missing)", chosen)
            index = self.config_combo.count() - 1
        self.config_combo.setCurrentIndex(index)
        self.config_combo.blockSignals(False)

    def _refresh_config_combo(self) -> None:
        """Rebuild the config dropdown for the current preset without saving anything."""
        if self.current is None:
            return
        self._loading = True
        self._fill_config_combo(self.current)
        self._loading = False

    def _engine_changed(self, *_args) -> None:
        """The default config's file name depends on the engine, so relabel the list."""
        if not self._loading:
            self._refresh_config_combo()

    def import_config(self) -> None:
        """Copy an existing engine config (own, another preset's, or any file) into this preset."""
        preset = self.current
        if preset is None:
            return
        settings = self.config.settings
        engine = self.config.engine_named(preset.engine)
        sources: list[tuple[str, Path | None]] = [("Browse for an .ini file...", None)]
        if engine is not None:
            for path in launch.engine_config_files(engine):
                sources.append((f"The engine's own config: {path}", path))
        for other, path in launch.other_preset_configs(settings, self.config.presets, preset):
            sources.append((f"From preset {other.name}: {path.name}", path))
        labels = [label for label, _ in sources]
        choice, ok = QtWidgets.QInputDialog.getItem(
            self, "Import config", "Import a config from:", labels, 0, False
        )
        if not ok:
            return
        source = sources[labels.index(choice)][1]
        if source is None:
            start = Path.home() / ".config"
            path, _ = QtWidgets.QFileDialog.getOpenFileName(
                self, "Choose an engine config", str(start if start.is_dir() else Path.home()),
                "Config files (*.ini *.cfg);;All files (*)",
            )
            if not path:
                return
            source = Path(path)
        name, ok = QtWidgets.QInputDialog.getText(
            self, "Import config", "Save it in this preset as:",
            text=launch.unique_config_name(settings, preset, source.name),
        )
        if not ok or not name.strip():
            return
        try:
            made = launch.create_config(settings, preset, name, source)
        except FileExistsError:
            QtWidgets.QMessageBox.warning(self, "Import config", "A config with that name already exists.")
            return
        except (ValueError, OSError) as error:
            QtWidgets.QMessageBox.warning(self, "Import config", f"Could not import it: {error}")
            return
        preset.config = made.name
        self._refresh_config_combo()
        self._update_derived()
        self.save_config()
        self.statusBar().showMessage(f"Imported {source.name} as {made.name}", 6000)

    def new_config(self) -> None:
        """Create another engine config for the current preset and switch to it."""
        preset = self.current
        if preset is None:
            return
        settings = self.config.settings
        name, ok = QtWidgets.QInputDialog.getText(
            self, "New config", "Name for the new config file:"
        )
        if not ok or not name.strip():
            return
        # What the new file starts from: nothing, the starter config, or a copy of one
        # of the existing configs (keeps key bindings and video settings).
        sources: list[tuple[str, Path | None]] = [("Blank (the engine starts from its defaults)", None)]
        starter = Path(settings.seed_config.strip()).expanduser() if settings.seed_config.strip() else None
        if starter is not None and starter.is_file():
            sources.append((f"Starter config ({starter.name})", starter))
        for path in launch.list_configs(settings, preset):
            sources.append((f"Copy of {path.name}", path))
        labels = [label for label, _ in sources]
        choice, ok = QtWidgets.QInputDialog.getItem(
            self, "New config", "Start the new config from:", labels, 0, False
        )
        if not ok:
            return
        try:
            made = launch.create_config(settings, preset, name, sources[labels.index(choice)][1])
        except FileExistsError:
            QtWidgets.QMessageBox.warning(self, "New config", "A config with that name already exists.")
            return
        except (ValueError, OSError) as error:
            QtWidgets.QMessageBox.warning(self, "New config", f"Could not create it: {error}")
            return
        preset.config = made.name
        self._refresh_config_combo()
        self._update_derived()
        self.save_config()

    def _fill_map_combo(self, preset: Preset) -> None:
        """List the maps found in the preset's files; the field stays freely editable."""
        self.map_combo.clear()
        seen: list[str] = []
        for path in active_preset_files(self.config, preset):
            for name, title in wadinfo.list_maps(path):
                if name not in seen:
                    seen.append(name)
                    self.map_combo.addItem(f"{name} - {title}" if title else name, name)
        self.map_combo.setEditText(preset.warp)

    def _unique_name(self, base: str) -> str:
        names = {p.name for p in self.config.presets}
        name, number = base, 2
        while name in names:
            name, number = f"{base} {number}", number + 1
        return name

    def _default_preset(self, name: str, files: list[str]) -> Preset:
        """A new preset using the first engine and a guessed IWAD."""
        engine = self.config.engines[0].name if self.config.engines else ""
        iwad = wadinfo.guess_iwad(files, self._iwads_now()) or (
            self._iwads_now()[0].name if self._iwads_now() else ""
        )
        return Preset(name=self._unique_name(name), engine=engine, iwad=iwad, files=list(files))

    def _iwads_now(self) -> list[Path]:
        return library.scan_iwads(self.config.settings.iwads_dir)

    def new_preset(self) -> None:
        name, ok = QtWidgets.QInputDialog.getText(self, "New preset", "Preset name:")
        if ok and name.strip():
            self._add_and_select(self._default_preset(name.strip(), []))

    def import_command(self) -> None:
        """Make a preset from a pasted command line (DoomRunner, a script, a shortcut)."""
        text, ok = QtWidgets.QInputDialog.getMultiLineText(
            self, "New preset from a command line",
            "Paste a Doom command line, for example\n"
            "uzdoom -iwad doom2.wad -file mod.pk3 map.wad -skill 4 +map MAP01",
        )
        if not ok or not text.strip():
            return
        parsed = importer.parse_command(text)
        if not parsed.files and not parsed.iwad:
            QtWidgets.QMessageBox.warning(
                self, "Import", "No -iwad or -file was found in that text."
            )
            return
        default = Path(parsed.files[0]).stem if parsed.files else Path(parsed.iwad).stem
        name, ok = QtWidgets.QInputDialog.getText(self, "New preset", "Preset name:", text=default)
        if not ok or not name.strip():
            return
        settings = self.config.settings
        iwad_names = [p.name for p in self._iwads_now()]
        preset = importer.build_preset(
            parsed, self._unique_name(name.strip()), self.config, iwad_names
        )
        # Files may have moved since the command was written: look for them by name.
        moved = library.find_moved(preset.files, settings.wads_root, settings.extra_folders)
        preset.files = [moved.get(f, f) for f in preset.files]
        if not preset.iwad:
            preset.iwad = wadinfo.guess_iwad(preset.files, self._iwads_now()) or (
                iwad_names[0] if iwad_names else ""
            )
        self._add_and_select(preset)
        missing = [f for f in preset.files if not Path(f).expanduser().is_file()]
        if missing:
            QtWidgets.QMessageBox.information(
                self, "Import",
                f"{len(missing)} file(s) could not be found and are marked (missing) in the load order.",
            )

    # ---- dropping files on the window -------------------------------------------
    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        """Files or folders dropped on the window become a new preset."""
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        files = importer.expand_dropped(paths)
        if not files:
            return
        event.acceptProposedAction()
        settings = self.config.settings
        files = library.sort_load_order(files, settings.wads_root, settings.extra_folders)
        name, ok = QtWidgets.QInputDialog.getText(
            self, "New preset from dropped files",
            f"{len(files)} file(s) dropped. Preset name:", text=Path(files[0]).stem,
        )
        if ok and name.strip():
            self._add_and_select(self._default_preset(name.strip(), files))

    def _add_and_select(self, preset: Preset) -> None:
        self.config.presets.append(preset)
        self.current = preset
        self.refresh_presets()
        self._select_preset(preset)
        self.left_tabs.setCurrentIndex(0)

    def duplicate_preset(self) -> None:
        if self.current is None:
            return
        clone = copy.deepcopy(self.current)
        clone.name = self._unique_name(self.current.name + " copy")
        clone.last_played, clone.play_count, clone.play_seconds = 0.0, 0, 0
        clone.picture = ""
        if self.current.picture:  # the copy gets its own file so removing one keeps the other
            clone.picture = self._store_picture(self.current.picture, clone.name)
        self._add_and_select(clone)

    def rename_preset(self) -> None:
        if self.current is None:
            return
        name, ok = QtWidgets.QInputDialog.getText(
            self, "Rename preset", "New name:", text=self.current.name
        )
        name = name.strip()
        if ok and name and name != self.current.name:
            if self.config.preset_named(name):
                QtWidgets.QMessageBox.warning(self, "Rename", "A preset with that name exists.")
                return
            QtWidgets.QMessageBox.information(
                self, "Rename",
                "Saves, screenshots and demos live in a folder named after the preset.\n"
                "Rename those folders by hand too if you want to keep using them.",
            )
            self.current.name = name
            self.config.last_preset = name
            self.refresh_presets()
            self._refresh_config_combo()  # the preset's own config folder is named after it
            self._update_derived()
            self.save_config()

    def delete_preset(self) -> None:
        if self.current is None:
            return
        answer = QtWidgets.QMessageBox.question(
            self, "Delete preset",
            f"Delete preset '{self.current.name}'?\nIts saves and screenshots are not deleted.",
        )
        if answer == QtWidgets.QMessageBox.StandardButton.Yes:
            self._delete_picture_file(self.current.picture)
            self.config.presets.remove(self.current)
            self.current = None
            self.refresh_presets()
            self._select_preset(self.config.presets[0] if self.config.presets else None)

    def export_preset(self) -> None:
        if self.current is None:
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export preset", f"{safe_name(self.current.name)}.json", "JSON (*.json)"
        )
        if path:
            try:
                data = asdict(self.current)
                data["picture"] = ""  # a path on this computer means nothing elsewhere
                Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")
            except OSError as error:
                QtWidgets.QMessageBox.warning(self, "Export", f"Could not write the file: {error}")

    def import_preset(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Import preset", "", "JSON (*.json)")
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            preset = AppConfig.from_dict({"presets": [data]}).presets[0]
        except (OSError, ValueError, IndexError, TypeError, AttributeError) as error:
            QtWidgets.QMessageBox.warning(self, "Import", f"Could not read that preset: {error}")
            return
        preset.name = self._unique_name(preset.name or "Imported")
        preset.picture = ""
        self._add_and_select(preset)

    # ================================================================== form → data
    def _form_changed(self, *_args) -> None:
        """Copy widget values into the current preset and save."""
        if self._loading or self.current is None:
            return
        p = self.current
        p.engine = self.engine_combo.currentText()
        p.iwad = self.iwad_combo.currentText()
        p.skill = self.skill_combo.currentIndex()
        p.config = self.config_combo.currentData() or ""  # "" = the default config
        # The map combo shows "MAP01 - Title"; the preset stores only the map name.
        text = self.map_combo.currentText().strip()
        index = self.map_combo.findText(text)
        p.warp = self.map_combo.itemData(index) if index >= 0 else text
        p.wrapper = self.wrapper_edit.text().strip()
        p.extra_args = self.args_edit.text()
        for key, box in self.checks.items():
            setattr(p, key, box.isChecked())
        p.width, p.height = self.width_spin.value(), self.height_spin.value()
        p.net_mode = self.net_mode.currentData() or ""
        p.net_players = self.net_players.value()
        p.net_game = self.net_game.currentData() or ""
        p.net_address = self.net_address.text().strip()
        p.net_port = self.net_port.value()
        p.notes = self.notes_edit.toPlainText()
        self.refresh_presets()
        self._update_derived()
        self.save_config()

    def _files_changed(self) -> None:
        if self._loading or self.current is None:
            return
        self.current.files = self.file_list.paths()
        self.current.disabled_files = self.file_list.disabled_paths()
        self._loading = True
        self._fill_map_combo(self.current)
        self._loading = False
        self._update_derived()
        self._fill_info_combo()
        self.save_config()

    def _update_derived(self) -> None:
        """Refresh the command preview, warnings and play stats.

        The Info tab is not refreshed here: it reads files from disk, so it is only
        rebuilt when the preset or its file list changes (see _fill_info_combo).
        """
        self._update_net_enabled()
        p = self.current
        self._update_picture()
        if p is None:
            self.command_edit.setText("")
            self.problem_label.setText("")
            self.stats_label.setText("")
            self.continue_button.setEnabled(False)
            return
        problems = launch.check(self.config, p)
        lines = [f"{'✖' if x.level == 'error' else '⚠'} {x.text}" for x in problems]
        self.problem_label.setText("\n".join(lines))
        self.problem_label.setObjectName("error" if any(x.level == "error" for x in problems) else "warning")
        self.problem_label.style().unpolish(self.problem_label)
        self.problem_label.style().polish(self.problem_label)
        try:
            self.command_edit.setText(launch.format_command(launch.build_command(self.config, p)))
        except ValueError:
            self.command_edit.setText("")
        no_errors = not any(x.level == "error" for x in problems)
        if self._process is None:
            self.launch_button.setEnabled(no_errors)
        dirs = launch.data_dirs(self.config.settings, p)
        self.continue_button.setEnabled(
            self._process is None and no_errors and userdata.latest_save(dirs.saves) is not None
        )
        self.stats_label.setText(
            f"Played {p.play_count} times · {_format_time(p.play_seconds)} total · "
            f"last {_format_date(p.last_played)}"
        )

    # =================================================================== library
    def refresh_library(self) -> None:
        settings = self.config.settings
        self._library_all = library.scan_library(settings.wads_root, settings.extra_folders)
        self._library = library.apply_hidden(self._library_all, settings.hidden_categories)
        self.tree.clear()
        for category in self._library:
            cat_item = QtWidgets.QTreeWidgetItem([f"{category.name}  ({len(category.projects)})"])
            cat_item.setData(0, PATH_ROLE, None)
            self.tree.addTopLevelItem(cat_item)
            for project in category.projects:
                key = f"{category.name}/{project.name}"
                item = QtWidgets.QTreeWidgetItem([self._project_label(key, project.name)])
                item.setData(0, PATH_ROLE, ("project", category.name, project.name))
                item.setToolTip(0, str(project.path))
                cat_item.addChild(item)
                for lib_file in project.files:
                    child = QtWidgets.QTreeWidgetItem([lib_file.rel])
                    child.setData(0, PATH_ROLE, ("file", str(lib_file.path)))
                    child.setToolTip(0, str(lib_file.path))
                    item.addChild(child)
        total = sum(len(c.projects) for c in self._library)
        self.statusBar().showMessage(f"Library: {total} projects", 4000)
        self._filter_library()
        self.refresh_mapsets()

    def _project_label(self, key: str, name: str) -> str:
        meta = self.config.library_meta.get(key, {})
        label = ("♥ " if meta.get("favorite") else "") + name
        rating = int(meta.get("rating", 0) or 0)
        return f"{label}  {'★' * rating}" if rating else label

    def _filter_library(self, *_args) -> None:
        """Hide tree entries that do not match the search box / favorites filter."""
        query = self.lib_search.text().strip().lower()
        only_fav = self.lib_favorites.isChecked()
        for c in range(self.tree.topLevelItemCount()):
            cat_item = self.tree.topLevelItem(c)
            cat_name = self._library[c].name
            visible_children = 0
            for p in range(cat_item.childCount()):
                project = self._library[c].projects[p]
                meta = self.config.library_meta.get(f"{cat_name}/{project.name}", {})
                match = (not query or query in project.name.lower()
                         or any(query in f.rel.lower() for f in project.files))
                if only_fav and not meta.get("favorite"):
                    match = False
                cat_item.child(p).setHidden(not match)
                visible_children += match
            cat_item.setHidden(visible_children == 0)
            if query:
                cat_item.setExpanded(visible_children > 0)

    @staticmethod
    def _info_pane() -> QtWidgets.QPlainTextEdit:
        """A small read-only text box that shows a project's readme or file list."""
        pane = QtWidgets.QPlainTextEdit()
        pane.setReadOnly(True)
        pane.setMaximumHeight(150)
        pane.setPlaceholderText("Select a project to see its readme or files.")
        return pane

    def _library_selected(self, current, _previous) -> None:
        """Show the readme / file list of the highlighted library row."""
        data = current.data(0, PATH_ROLE) if current is not None else None
        if not data:
            self.lib_info.setPlainText("")
            return
        if data[0] == "file":
            path = Path(data[1])
            self.lib_info.setPlainText(wadinfo.project_info(path.parent, [str(path)]))
            return
        found = self._selected_project()
        if found is None:
            self.lib_info.setPlainText("")
            return
        project = found[1]
        self.lib_info.setPlainText(
            wadinfo.project_info(project.path, [str(f.path) for f in project.files])
        )

    def _selected_project(self):
        """The (category name, Project) for the selected tree row, or None."""
        item = self.tree.currentItem()
        while item is not None:
            data = item.data(0, PATH_ROLE)
            if data and data[0] == "project":
                for category in self._library:
                    if category.name == data[1]:
                        for project in category.projects:
                            if project.name == data[2]:
                                return category.name, project
            item = item.parent()
        return None

    def _library_double_click(self, item, _column) -> None:
        data = item.data(0, PATH_ROLE)
        if data and data[0] in ("project", "file"):
            self._library_add_selected()

    def _choose_project_files(self, project: library.Project, category: str) -> list[str] | None:
        """Return the file paths to use from a project, asking when there are several."""
        if not project.files:
            QtWidgets.QMessageBox.information(self, "Library", "That folder has no loadable files.")
            return None
        data = self.tree.currentItem().data(0, PATH_ROLE)
        if data and data[0] == "file":  # a single file was picked directly
            return [data[1]]
        if len(project.files) == 1:
            return [str(project.files[0].path)]
        dialog = PickFilesDialog(
            project.name, [(f.rel, str(f.path)) for f in project.files], self
        )
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return None
        return dialog.chosen() or None

    def _library_add_selected(self) -> None:
        found = self._selected_project()
        if found is None:
            return
        if self.current is None:
            self._library_new_from_selected()
            return
        files = self._choose_project_files(found[1], found[0])
        if files:
            self.file_list.add_files(files)
            self.right_tabs.setCurrentIndex(0)

    def _library_new_from_selected(self) -> None:
        found = self._selected_project()
        if found is None:
            return
        files = self._choose_project_files(found[1], found[0])
        if files:
            self._add_and_select(self._default_preset(found[1].name, files))

    def _library_menu(self, pos) -> None:
        clicked = self.tree.itemAt(pos)
        if clicked is not None and clicked.parent() is None:
            self._category_menu(clicked, pos)  # a folder row, not a project
            return
        found = self._selected_project()
        if found is None:
            return
        category, project = found
        key = f"{category}/{project.name}"
        meta = self.config.library_meta.get(key, {})  # read only: nothing is saved by opening the menu
        menu = QtWidgets.QMenu(self)
        add_action(menu, "Add to current preset", self._library_add_selected)
        add_action(menu, "New preset from project", self._library_new_from_selected)
        menu.addSeparator()
        add_action(
            menu,
            "Remove from favorites" if meta.get("favorite") else "Add to favorites",
            lambda: self._set_meta(key, "favorite", not meta.get("favorite")),
        )
        rating_menu = menu.addMenu("Rating")
        for stars in range(0, 6):
            add_action(
                rating_menu,
                "No rating" if stars == 0 else "★" * stars,
                lambda s=stars: self._set_meta(key, "rating", s),
            )
        menu.addSeparator()
        add_action(
            menu,
            "Open folder",
            lambda: QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(project.path))),
        )
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _category_menu(self, item: QtWidgets.QTreeWidgetItem, pos) -> None:
        """Right-click menu for a top-level folder row: hide it, or manage folders."""
        name = self._library[self.tree.indexOfTopLevelItem(item)].name
        menu = QtWidgets.QMenu(self)
        if name != library.LOOSE_NAME:  # that row is a group of folders, not one folder
            add_action(menu, f"Hide “{name}” from the library", lambda: self._hide_folder(name))
        add_action(menu, "Manage library folders…", lambda: self.open_settings("library"))
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _hide_folder(self, name: str) -> None:
        """Add a folder to the hidden list (it can be shown again in Settings → Library)."""
        hidden = self.config.settings.hidden_categories
        if name not in hidden:
            hidden.append(name)
        self.save_config()
        self.refresh_library()
        self.statusBar().showMessage(
            f"Hid “{name}”. Show it again under Settings → Library.", 8000
        )

    def _set_meta(self, key: str, field: str, value) -> None:
        """Store a favorite/rating for a project and update the tree in place.

        The tree is not rebuilt, so the folders you have open stay open.
        """
        self.config.library_meta.setdefault(key, {})[field] = value
        self.save_config()
        for c in range(self.tree.topLevelItemCount()):
            cat_item = self.tree.topLevelItem(c)
            for p in range(cat_item.childCount()):
                project = self._library[c].projects[p]
                if f"{self._library[c].name}/{project.name}" == key:
                    cat_item.child(p).setText(0, self._project_label(key, project.name))
        self._filter_library()

    # ==================================================================== mapsets
    def _build_mapsets_tab(self) -> QtWidgets.QWidget:
        """A tick-box picker for map sets: tick every file the maps need, make a preset.

        Many map sets need several files (the maps plus a texture or resource pack,
        sometimes from another folder), so this is built around ticking several files
        at once. Ticking a project ticks all its files. Files are loaded in the order
        they were ticked, so tick resources first.
        """
        page = QtWidgets.QWidget()
        col = QtWidgets.QVBoxLayout(page)
        top = QtWidgets.QHBoxLayout()
        self.map_search = QtWidgets.QLineEdit()
        self.map_search.setPlaceholderText("Search map sets…")
        self.map_search.textChanged.connect(self._filter_mapsets)
        self.map_scope = QtWidgets.QComboBox()
        self.map_scope.addItems(["Map sets only", "Everything"])
        self.map_scope.setToolTip(
            "Everything also lists mods, music and resource packs, so a map set's "
            "requirements can be ticked from other folders."
        )
        self.map_scope.currentIndexChanged.connect(self.refresh_mapsets)
        top.addWidget(self.map_search, 1)
        top.addWidget(self.map_scope)
        col.addLayout(top)

        self.mapset_tree = QtWidgets.QTreeWidget()
        self.mapset_tree.setHeaderHidden(True)
        self.mapset_tree.itemChanged.connect(self._mapset_item_changed)
        self.mapset_tree.currentItemChanged.connect(self._mapset_selected)
        col.addWidget(self.mapset_tree, 1)

        self.mapset_info = QtWidgets.QLabel()
        self.mapset_info.setObjectName("muted")
        self.mapset_info.setWordWrap(True)
        col.addWidget(self.mapset_info)
        self.mapset_text = self._info_pane()
        col.addWidget(self.mapset_text)
        self.mapset_count = QtWidgets.QLabel()
        col.addWidget(self.mapset_count)

        row = QtWidgets.QHBoxLayout()
        new = QtWidgets.QPushButton("New preset from ticked")
        new.clicked.connect(self._mapset_new_preset)
        add = QtWidgets.QPushButton("Add ticked to preset")
        add.clicked.connect(self._mapset_add_to_preset)
        clear = QtWidgets.QPushButton("Clear")
        clear.clicked.connect(self._mapset_clear)
        for button in (new, add, clear):
            row.addWidget(button)
        col.addLayout(row)
        self._mapset_selected(None, None)
        self._update_mapset_count()
        return page

    @staticmethod
    def _tick_item(text: str, tristate: bool = True) -> QtWidgets.QTreeWidgetItem:
        """A tree row with a tick box. Rows with children follow their children's boxes."""
        item = QtWidgets.QTreeWidgetItem([text])
        flags = item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable
        if tristate:
            flags |= QtCore.Qt.ItemFlag.ItemIsAutoTristate
        item.setFlags(flags)
        item.setCheckState(0, QtCore.Qt.CheckState.Unchecked)
        return item

    def refresh_mapsets(self, *_args) -> None:
        """Rebuild the Mapsets tree from the scanned library, keeping the ticked files."""
        keep = set(self._checked_mapset_files())
        wanted = self.config.settings.mapsets_category
        everything = self.map_scope.currentIndex() == 1
        tree = self.mapset_tree
        self._mapset_owner = {}
        self._mapset_building = True
        tree.clear()
        # "Everything" respects the hidden folders; "Map sets only" always shows the maps folder.
        for category in (self._library if everything else self._library_all):
            if not everything and category.name != wanted:
                continue
            if everything:
                holder = self._tick_item(f"{category.name}  ({len(category.projects)})")
                tree.addTopLevelItem(holder)
            for project in category.projects:
                if not project.files:
                    continue  # nothing to tick
                project_item = self._tick_item(project.name)
                project_item.setData(0, PATH_ROLE, ("project", category.name, project.name))
                project_item.setToolTip(0, str(project.path))
                if everything:
                    holder.addChild(project_item)
                else:
                    tree.addTopLevelItem(project_item)
                for lib_file in project.files:
                    path = str(lib_file.path)
                    file_item = self._tick_item(lib_file.rel, tristate=False)
                    file_item.setData(0, PATH_ROLE, ("file", path))
                    file_item.setToolTip(0, path)
                    project_item.addChild(file_item)
                    file_item.setCheckState(
                        0, QtCore.Qt.CheckState.Checked if path in keep
                        else QtCore.Qt.CheckState.Unchecked
                    )
                    self._mapset_owner[path] = (category.name, project.name)
        self._mapset_building = False
        self._mapset_order = [p for p in self._mapset_order if p in keep and p in self._mapset_owner]
        self._filter_mapsets()
        self._update_mapset_count()

    def _mapset_file_items(self) -> list[tuple[QtWidgets.QTreeWidgetItem, str]]:
        """Every file row in the tree, top to bottom, with its path."""
        found: list[tuple[QtWidgets.QTreeWidgetItem, str]] = []

        def walk(parent: QtWidgets.QTreeWidgetItem) -> None:
            for i in range(parent.childCount()):
                child = parent.child(i)
                data = child.data(0, PATH_ROLE)
                if data and data[0] == "file":
                    found.append((child, data[1]))
                else:
                    walk(child)

        walk(self.mapset_tree.invisibleRootItem())
        return found

    def _checked_mapset_files(self) -> list[str]:
        """Ticked files in the order they were ticked (ties fall back to tree order)."""
        ticked = [
            path for item, path in self._mapset_file_items()
            if item.checkState(0) == QtCore.Qt.CheckState.Checked
        ]
        ordered = [p for p in self._mapset_order if p in ticked]
        return ordered + [p for p in ticked if p not in ordered]

    def _mapset_item_changed(self, item: QtWidgets.QTreeWidgetItem, _column: int) -> None:
        """Remember the order files were ticked in, and update the counter."""
        if self._mapset_building:
            return
        data = item.data(0, PATH_ROLE)
        if data and data[0] == "file":
            path = data[1]
            if item.checkState(0) == QtCore.Qt.CheckState.Checked:
                if path not in self._mapset_order:
                    self._mapset_order.append(path)
            elif path in self._mapset_order:
                self._mapset_order.remove(path)
        self._update_mapset_count()

    def _update_mapset_count(self) -> None:
        count = len(self._checked_mapset_files())
        self.mapset_count.setText(
            "Nothing ticked yet." if count == 0
            else f"{count} file{'s' if count != 1 else ''} ticked (loaded in the order ticked)"
        )

    def _filter_mapsets(self, *_args) -> None:
        """Hide rows that do not match the search box; ticks on hidden rows are kept."""
        query = self.map_search.text().strip().lower()
        root = self.mapset_tree.invisibleRootItem()
        for i in range(root.childCount()):
            self._apply_mapset_filter(root.child(i), query, False)

    def _apply_mapset_filter(self, item: QtWidgets.QTreeWidgetItem, query: str, parent_matches: bool) -> bool:
        """Show/hide one row and its children; returns whether the row stays visible.

        A row whose own name matches shows everything below it.
        """
        matches = parent_matches or not query or query in item.text(0).lower()
        if item.childCount() == 0:
            item.setHidden(not matches)
            return matches
        any_child = False
        for i in range(item.childCount()):
            if self._apply_mapset_filter(item.child(i), query, matches):
                any_child = True
        item.setHidden(not any_child)
        if query:
            item.setExpanded(any_child)
        return any_child

    def _mapset_selected(self, current, _previous) -> None:
        """Describe the highlighted row: how many maps its file(s) contain."""
        hint = "Tick every file the maps need. Ticking a project ticks all of its files."
        data = current.data(0, PATH_ROLE) if current is not None else None
        if not data:
            self.mapset_info.setText(hint)
            self.mapset_text.setPlainText("")
            return
        if data[0] == "file":
            paths = [data[1]]
            folder = Path(data[1]).parent
        else:
            paths = [
                str(f.path)
                for category in self._library_all if category.name == data[1]
                for project in category.projects if project.name == data[2]
                for f in project.files
            ]
            folder = next(
                (project.path
                 for category in self._library_all if category.name == data[1]
                 for project in category.projects if project.name == data[2]),
                Path(paths[0]).parent if paths else Path("."),
            )
        self.mapset_text.setPlainText(wadinfo.project_info(folder, paths))
        names: list[str] = []
        for path in paths:
            names += [m for m, _title in wadinfo.list_maps(path) if m not in names]
        if not names:
            self.mapset_info.setText("No maps found here (a resource, music or mod file).")
        elif len(names) == 1:
            self.mapset_info.setText(f"1 map: {names[0]}")
        else:
            self.mapset_info.setText(f"{len(names)} maps: {names[0]} to {names[-1]}")

    def _clear_mapset_ticks(self) -> None:
        self._mapset_building = True
        for item, _path in self._mapset_file_items():
            item.setCheckState(0, QtCore.Qt.CheckState.Unchecked)
        self._mapset_building = False
        self._mapset_order = []
        self._update_mapset_count()

    def _mapset_clear(self) -> None:
        self._clear_mapset_ticks()

    def _mapset_new_preset(self) -> None:
        files = self._checked_mapset_files()
        if not files:
            QtWidgets.QMessageBox.information(self, "Mapsets", "Tick at least one file first.")
            return
        # Name it after the first ticked map set (not a resource pack it depends on).
        wanted = self.config.settings.mapsets_category
        owners = [self._mapset_owner[f] for f in files if f in self._mapset_owner]
        suggestion = next((o[1] for o in owners if o[0] == wanted), owners[0][1] if owners else "New preset")
        name, ok = QtWidgets.QInputDialog.getText(self, "New preset", "Preset name:", text=suggestion)
        if ok and name.strip():
            self._add_and_select(self._default_preset(name.strip(), files))
            self._clear_mapset_ticks()

    def _mapset_add_to_preset(self) -> None:
        files = self._checked_mapset_files()
        if not files:
            QtWidgets.QMessageBox.information(self, "Mapsets", "Tick at least one file first.")
            return
        if self.current is None:
            self._mapset_new_preset()
            return
        self.file_list.add_files(files)
        self.right_tabs.setCurrentIndex(0)
        self._clear_mapset_ticks()

    # ==================================================== saves, demos, screenshots
    def _dirs(self) -> launch.DataDirs | None:
        return launch.data_dirs(self.config.settings, self.current) if self.current else None

    def _refresh_user_tabs(self) -> None:
        """Reload whichever of the file tabs is visible (cheap, so no caching)."""
        dirs = self._dirs()
        self.saves_list.clear()
        self.demos_list.clear()
        self.shots_list.clear()
        if dirs is None:
            return
        for entry in userdata.list_saves(dirs.saves):
            self._add_user_item(self.saves_list, entry)
        for entry in userdata.list_demos(dirs.demos):
            self._add_user_item(self.demos_list, entry)
        if self.right_tabs.currentIndex() == 3:  # thumbnails only when visible
            for entry in userdata.list_screenshots(dirs.screenshots)[:80]:
                thumb = self._thumbnail(entry.path)
                if thumb is None:
                    continue  # unreadable image: leave it out rather than show a blank icon
                item = QtWidgets.QListWidgetItem(QtGui.QIcon(thumb), entry.path.name)
                item.setData(PATH_ROLE, str(entry.path))
                self.shots_list.addItem(item)

    # ============================================================ shortcuts, relinking
    @staticmethod
    def _preset_tooltip(preset: Preset) -> str:
        """Hover text in the preset list: play stats and the first few files."""
        names = [Path(f).name for f in preset.files]
        shown = names[:10] + ([f"... and {len(names) - 10} more"] if len(names) > 10 else [])
        head = f"{len(names)} file(s) · played {preset.play_count}x · last {_format_date(preset.last_played)}"
        return "\n".join([head, *shown])

    def create_shortcut(self) -> None:
        """Write an application-menu entry that launches the current preset directly."""
        p = self.current
        if p is None:
            return
        icon = p.picture if p.picture and Path(p.picture).is_file() else str(config_io.APP_DIR / "doomdeck.png")
        try:
            target = launch.write_desktop_entry(
                p, config_io.APP_DIR / "run.sh", icon, config_io.APP_DIR
            )
        except OSError as error:
            QtWidgets.QMessageBox.warning(self, "Shortcut", f"Could not write the shortcut: {error}")
            return
        QtWidgets.QMessageBox.information(
            self, "Shortcut",
            f"Added '{p.name}' to your application menu:\n{target}\n\n"
            "It starts the game directly, without opening DoomDeck. Run this again after "
            "renaming the preset or changing its picture.",
        )

    def auto_sort_files(self) -> None:
        """Put the current preset's files in resources -> mods -> maps order."""
        p = self.current
        if p is None:
            return
        settings = self.config.settings
        ordered = library.sort_load_order(p.files, settings.wads_root, settings.extra_folders)
        if ordered == p.files:
            self.statusBar().showMessage("The load order is already sorted.", 5000)
            return
        answer = QtWidgets.QMessageBox.question(
            self, "Auto-sort",
            "Sort the load order into audio, visuals, utilities, mods, then maps?\n"
            "Files of the same kind keep their current order.",
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        self.file_list.set_files(ordered, p.disabled_files)
        self._files_changed()
        self.statusBar().showMessage("Load order sorted.", 5000)

    def find_missing_files(self) -> None:
        """Re-point missing files of the current preset to where they now live."""
        p = self.current
        if p is None:
            return
        settings = self.config.settings
        missing = [f for f in p.files if not Path(f).expanduser().is_file()]
        if not missing:
            QtWidgets.QMessageBox.information(self, "Find missing files", "Every file in this preset was found.")
            return
        moved = library.find_moved(p.files, settings.wads_root, settings.extra_folders)
        if not moved:
            QtWidgets.QMessageBox.information(
                self, "Find missing files",
                f"{len(missing)} file(s) are missing and none of them could be found in the library.",
            )
            return
        new_files: list[str] = []
        for path in p.files:
            path = moved.get(path, path)
            if path not in new_files:
                new_files.append(path)
        self.file_list.set_files(new_files, [moved.get(f, f) for f in p.disabled_files])
        self._files_changed()
        left = len(missing) - len(moved)
        QtWidgets.QMessageBox.information(
            self, "Find missing files",
            f"Fixed {len(moved)} file(s)." + (f"\n{left} could not be found." if left else ""),
        )

    # ================================================================ preset picture
    @property
    def _picture_dir(self) -> Path:
        return config_io.APP_DIR / "pictures"

    def _picture_pixmap(self, path: str, size: QtCore.QSize) -> QtGui.QPixmap | None:
        """The stored picture scaled to fit `size`, or None if unset/unreadable.

        Cached by file, modified time and size; presets are redrawn often.
        """
        if not path:
            return None
        try:
            stamp = Path(path).stat().st_mtime_ns
        except OSError:
            return None
        key = (path, stamp, size.width(), size.height())
        if key not in self._picture_cache:
            reader = QtGui.QImageReader(path)
            reader.setAutoTransform(True)
            native = reader.size()
            if native.isValid():
                native.scale(size, QtCore.Qt.AspectRatioMode.KeepAspectRatio)
                reader.setScaledSize(native)
            image = reader.read()
            self._picture_cache[key] = None if image.isNull() else QtGui.QPixmap.fromImage(image)
        return self._picture_cache[key]

    def _store_picture(self, source: str, preset_name: str) -> str:
        """Save a downscaled PNG copy of `source` and return its path ("" on failure)."""
        reader = QtGui.QImageReader(source)
        reader.setAutoTransform(True)
        size = reader.size()
        if size.isValid() and (size.width() > PICTURE_MAX.width() or size.height() > PICTURE_MAX.height()):
            size.scale(PICTURE_MAX, QtCore.Qt.AspectRatioMode.KeepAspectRatio)
            reader.setScaledSize(size)  # only ever shrinks
        image = reader.read()
        if image.isNull():
            return ""
        try:
            self._picture_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            return ""
        target = self._picture_dir / f"{safe_name(preset_name)}-{time.time_ns()}.png"
        return str(target) if image.save(str(target), "PNG") else ""

    def _delete_picture_file(self, path: str) -> None:
        """Delete a stored picture, but only if it is one of DoomDeck's own copies."""
        if not path:
            return
        try:
            target = Path(path).resolve()
            if target.parent == self._picture_dir.resolve() and target.is_file():
                target.unlink()
        except OSError:
            pass

    def set_picture(self, source: str) -> None:
        """Use the image file `source` as the current preset's picture."""
        if self.current is None:
            return
        stored = self._store_picture(source, self.current.name)
        if not stored:
            QtWidgets.QMessageBox.warning(self, "Picture", f"Could not read that image:\n{source}")
            return
        self._delete_picture_file(self.current.picture)
        self.current.picture = stored
        self.refresh_presets()
        self._update_picture()
        self.save_config()

    def choose_picture(self) -> None:
        if self.current is None:
            return
        dirs = self._dirs()
        start = str(dirs.screenshots) if dirs and dirs.screenshots.is_dir() else ""
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Choose a picture", start,
            "Images (*.png *.jpg *.jpeg *.bmp *.webp);;All files (*)",
        )
        if path:
            self.set_picture(path)

    def clear_picture(self) -> None:
        if self.current is None or not self.current.picture:
            return
        self._delete_picture_file(self.current.picture)
        self.current.picture = ""
        self.refresh_presets()
        self._update_picture()
        self.save_config()

    def _update_picture(self) -> None:
        """Show the current preset's picture (or the placeholder) on the Setup tab."""
        p = self.current
        pixmap = self._picture_pixmap(p.picture, PICTURE_BOX) if p else None
        if pixmap is None:
            self.picture_label.setPixmap(QtGui.QPixmap())
            self.picture_label.setText("No picture")
        else:
            self.picture_label.setPixmap(pixmap)
        self.remove_picture_button.setEnabled(pixmap is not None)

    def _use_selected_shot(self) -> None:
        item = self.shots_list.currentItem()
        if item is None:
            QtWidgets.QMessageBox.information(self, "Picture", "Select a screenshot first.")
            return
        self.set_picture(item.data(PATH_ROLE))

    def _shot_menu(self, pos) -> None:
        item = self.shots_list.itemAt(pos)
        if item is None:
            return
        menu = QtWidgets.QMenu(self)
        add_action(menu, "Use as preset picture", lambda: self.set_picture(item.data(PATH_ROLE)))
        menu.exec(self.shots_list.viewport().mapToGlobal(pos))

    @staticmethod
    def _thumbnail(path: Path) -> QtGui.QPixmap | None:
        """Load an image already shrunk to thumbnail size.

        Screenshots can be 4K; keeping 80 full-size pixmaps in icons would use
        gigabytes of memory, so the decoder is asked for the small version directly.
        """
        reader = QtGui.QImageReader(str(path))
        reader.setAutoTransform(True)
        size = reader.size()
        if size.isValid():
            size.scale(360, 224, QtCore.Qt.AspectRatioMode.KeepAspectRatio)
            reader.setScaledSize(size)
        image = reader.read()
        return None if image.isNull() else QtGui.QPixmap.fromImage(image)

    @staticmethod
    def _add_user_item(listing: QtWidgets.QListWidget, entry: userdata.UserFile) -> None:
        item = QtWidgets.QListWidgetItem(f"{entry.path.name}    {_format_date(entry.modified)}")
        item.setData(PATH_ROLE, str(entry.path))
        listing.addItem(item)

    def _open_dir(self, which: str) -> None:
        dirs = self._dirs()
        if dirs is None:
            return
        folder = getattr(dirs, which)
        folder.mkdir(parents=True, exist_ok=True)
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(folder)))

    def _delete_selected(self, listing: QtWidgets.QListWidget) -> None:
        item = listing.currentItem()
        if item is None:
            return
        path = Path(item.data(PATH_ROLE))
        answer = QtWidgets.QMessageBox.question(self, "Delete", f"Delete {path.name}?")
        if answer == QtWidgets.QMessageBox.StandardButton.Yes:
            try:
                path.unlink()
            except OSError as error:
                QtWidgets.QMessageBox.warning(self, "Delete", str(error))
            self._refresh_user_tabs()

    def _load_selected_save(self) -> None:
        item = self.saves_list.currentItem()
        if item is not None:
            self.launch_current(load_save=item.data(PATH_ROLE))

    def _play_selected_demo(self) -> None:
        item = self.demos_list.currentItem()
        if item is not None:
            self.launch_current(play_demo=item.data(PATH_ROLE))

    # ====================================================================== info
    def _fill_info_combo(self) -> None:
        """List the readme files of the projects used by the current preset."""
        if self.current is None:
            return
        root = Path(self.config.settings.wads_root).expanduser()
        # Group the active files by project folder, in load order.
        groups: dict[Path, list[str]] = {}
        for path in active_preset_files(self.config, self.current):
            folder = Path(path).parent
            try:
                parts = Path(path).relative_to(root).parts
                folder = root.joinpath(*parts[:2]) if len(parts) > 2 else folder
            except ValueError:
                pass  # file lives outside the wads folder: use its own folder
            groups.setdefault(folder, []).append(path)

        # Per project: a readme file in the folder, else readmes inside its archives,
        # else a summary of its files.
        entries: list[tuple[str, dict]] = []
        for folder, files in groups.items():
            readme = wadinfo.find_readme(folder)
            if readme:
                entries.append((folder.name, {"kind": "file", "path": str(readme)}))
                continue
            inside = []
            for path in files:
                member = wadinfo.find_archive_readme(path)
                if member:
                    inside.append((Path(path).name, path, member))
            for archive_name, path, member in inside:
                label = f"{folder.name} - {archive_name}: {member}"
                entries.append((label, {"kind": "archive", "path": path, "member": member}))
            if not inside:
                entries.append(
                    (f"{folder.name} (file details)", {"kind": "summary", "files": files})
                )

        self.info_combo.blockSignals(True)
        self.info_combo.clear()
        for label, data in entries:
            self.info_combo.addItem(label, json.dumps(data))
        self.info_combo.blockSignals(False)
        self._show_info()

    def _show_info(self, *_args) -> None:
        raw = self.info_combo.currentData()
        if not raw:
            self.info_text.setPlainText("No files in this preset yet.")
            return
        data = json.loads(raw)
        if data["kind"] == "file":
            text = wadinfo.read_text_file(data["path"])
        elif data["kind"] == "archive":
            text = wadinfo.read_archive_text(data["path"], data["member"])
        else:
            text = (
                "No readme found for this project, so here are its files:\n\n"
                + wadinfo.file_summary(data["files"])
            )
        self.info_text.setPlainText(text)

    # =================================================================== launching
    def launch_current(self, *_args, load_save: str | None = None, play_demo: str | None = None) -> None:
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
            self.right_tabs.setCurrentIndex(5)  # show Output so the failure is visible

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
            self.close_check.blockSignals(True)
            self.close_check.setChecked(self.config.settings.close_on_launch)
            self.close_check.blockSignals(False)
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


def active_preset_files(config: AppConfig, preset: Preset) -> list[str]:
    """Existing, enabled files of a preset (autoload files excluded), for map/info lookups."""
    disabled = set(preset.disabled_files)
    return [f for f in preset.files if f not in disabled and Path(f).is_file()]
