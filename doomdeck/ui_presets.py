"""Presets: the list, the Setup tab, and everything that edits a preset.

These methods belong to MainWindow (ui_main.py), which inherits this class; they are
kept in their own file only to keep each file a readable size."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from PySide6 import QtCore, QtGui, QtWidgets
from . import config as config_io
from . import importer, launch, library, osinfo, shortcuts, sharing, userdata, wadinfo
from .models import AppConfig, Preset, parse_tags, safe_name, sort_presets
from .ui_widgets import FileButtons, FileList, PATH_ROLE
from .ui_common import SKILLS, PICTURE_BOX, PICTURE_ICON, active_preset_files, format_time, format_date


class PresetsMixin:
    """Presets: the list, the Setup tab, and everything that edits a preset."""

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
            f"Add this preset to {shortcuts.where_text()}, so it starts the game directly "
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
        self.net_check = QtWidgets.QPushButton("Check server...")
        self.net_check.setToolTip(
            "Ask a Zandronum or Odamex server which files it runs, compare them with your library, and\n"
            "offer to download what is missing and use the server's files in this profile.")
        self.net_check.clicked.connect(self.check_server)
        self.net_autosync = QtWidgets.QCheckBox("On Launch, check the server and get its files (asks first)")
        grid.addWidget(self.net_check, 3, 0)
        grid.addWidget(self.net_autosync, 3, 1, 1, 5)
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
        self.net_autosync.toggled.connect(self._form_changed)
        return box

    def _update_net_enabled(self) -> None:
        """Grey out the multiplayer fields that do not apply to the chosen mode."""
        mode = self.net_mode.currentData()
        self.net_players.setEnabled(mode == "host")
        self.net_game.setEnabled(mode == "host")
        self.net_address.setEnabled(mode == "join")
        self.net_port.setEnabled(bool(mode))
        self.net_check.setEnabled(mode == "join")
        self.net_autosync.setEnabled(mode == "join")

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
            self.net_autosync.setChecked(preset.net_autosync)
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
        """Save the current preset as a shareable file that lists every file's checksum."""
        if self.current is None:
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export preset", f"{safe_name(self.current.name)}.json", "JSON (*.json)"
        )
        if not path:
            return
        engine = self.config.engine_named(self.current.engine)
        iwad = launch.resolve_iwad(self.config.settings, self.current.iwad)
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.CursorShape.WaitCursor)
        try:  # checksums of big wads take a moment the first time
            data = sharing.export_data(self.current, iwad, engine.version if engine else "")
            Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError as error:
            QtWidgets.QMessageBox.warning(self, "Export", f"Could not write the file: {error}")
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

    def _local_file_index(self) -> dict:
        """Every file in the wads folder and the extra library folders, by file name."""
        settings = self.config.settings
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.CursorShape.WaitCursor)
        try:
            return sharing.index_files([settings.wads_root, *settings.extra_folders])
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

    def _read_shared_file(self, title: str):
        """Ask for a preset file and return (preset dict, extras), or None."""
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, title, "", "JSON (*.json)")
        if not path:
            return None
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            return sharing.read_export(data)
        except (OSError, ValueError, TypeError, AttributeError) as error:
            QtWidgets.QMessageBox.warning(self, title, f"Could not read that file: {error}")
            return None

    def _shared_report(self, extras: dict, checks: list) -> tuple[str, bool]:
        """Report text for a shared preset, and whether everything matched."""
        iwad = sharing.verify_iwad(extras.get("iwad") or {}, self.config.settings.iwads_dir)
        text = sharing.report(checks, iwad)
        if extras.get("engine_version"):
            text += f"\nThe sender used engine version {extras['engine_version']}."
        clean = all(c.status == "ok" for c in checks) and (iwad is None or iwad.status == "ok")
        return text, clean

    def import_preset(self) -> None:
        """Import a preset file; files are matched to your library and checked by checksum."""
        loaded = self._read_shared_file("Import preset")
        if loaded is None:
            return
        data, extras = loaded
        try:
            if extras.get("files") or extras.get("iwad"):
                relinked = sharing.relink(data, extras, self._local_file_index())
                data = dict(data, files=relinked.files, disabled_files=relinked.disabled)
            else:
                relinked = None
            preset = AppConfig.from_dict({"presets": [data]}).presets[0]
        except (ValueError, IndexError, TypeError, AttributeError) as error:
            QtWidgets.QMessageBox.warning(self, "Import", f"Could not read that preset: {error}")
            return
        preset.name = self._unique_name(preset.name or "Imported")
        preset.picture = ""
        self._add_and_select(preset)
        if relinked is not None:
            text, clean = self._shared_report(extras, relinked.checks)
            box = QtWidgets.QMessageBox.information if clean else QtWidgets.QMessageBox.warning
            box(self, "Import", f"Imported “{preset.name}”.\n\n{text}")

    def check_shared_preset(self) -> None:
        """Compare your files with a shared preset's checksums (for multiplayer) without importing it."""
        loaded = self._read_shared_file("Check against a shared preset")
        if loaded is None:
            return
        data, extras = loaded
        if not extras.get("files"):
            QtWidgets.QMessageBox.information(
                self, "Check", "That file has no checksums (it is an older export), so it cannot be compared."
            )
            return
        checks = sharing.verify(extras["files"], self._local_file_index())
        text, clean = self._shared_report(extras, checks)
        box = QtWidgets.QMessageBox.information if clean else QtWidgets.QMessageBox.warning
        box(self, "Check", ("Everything matches.\n\n" if clean else "") + text)

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
        p.net_autosync = self.net_autosync.isChecked()
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
        self._refresh_maps_tab()
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
            f"Played {p.play_count} times · {format_time(p.play_seconds)} total · "
            f"last {format_date(p.last_played)}"
        )

    @staticmethod
    def _preset_tooltip(preset: Preset) -> str:
        """Hover text in the preset list: play stats and the first few files."""
        names = [Path(f).name for f in preset.files]
        shown = names[:10] + ([f"... and {len(names) - 10} more"] if len(names) > 10 else [])
        head = f"{len(names)} file(s) · played {preset.play_count}x · last {format_date(preset.last_played)}"
        return "\n".join([head, *shown])

    def _shortcut_icon(self, preset: Preset) -> Path | None:
        """A PNG for the shortcut's icon: the preset's picture, else DoomDeck's own icon.

        Linux uses the file as it is. Windows and macOS need a square PNG of a fixed size,
        so the picture is scaled into a 256 x 256 transparent square first.
        """
        source = Path(preset.picture) if preset.picture and Path(preset.picture).is_file() else config_io.find_icon()
        if source is None or osinfo.current() == osinfo.LINUX:
            return source
        image = QtGui.QImage(str(source))
        if image.isNull():
            return None
        image = image.scaled(256, 256, QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                             QtCore.Qt.TransformationMode.SmoothTransformation)
        square = QtGui.QImage(256, 256, QtGui.QImage.Format.Format_ARGB32)
        square.fill(QtCore.Qt.GlobalColor.transparent)
        painter = QtGui.QPainter(square)
        painter.drawImage((256 - image.width()) // 2, (256 - image.height()) // 2, image)
        painter.end()
        folder = config_io.APP_DIR / "shortcut-icons"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{safe_name(preset.name)}.png"
        return target if square.save(str(target), "PNG") else None

    def create_shortcut(self) -> None:
        """Write a menu shortcut (application menu, Start menu or Applications folder)
        that launches the current preset directly."""
        p = self.current
        if p is None:
            return
        try:
            made = shortcuts.create(
                p, config_io.PROGRAM_DIR, self._shortcut_icon(p),
                icon_dir=config_io.APP_DIR / "shortcut-icons",
            )
        except OSError as error:
            QtWidgets.QMessageBox.warning(self, "Shortcut", f"Could not write the shortcut: {error}")
            return
        QtWidgets.QMessageBox.information(
            self, "Shortcut",
            f"Added '{p.name}' to {shortcuts.where_text()}:\n{made.path}\n\n"
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
