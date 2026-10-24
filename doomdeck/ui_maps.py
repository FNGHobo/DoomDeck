"""The Maps tab: every map in the current preset with a "finished" tick, a play count
and a button to start on that map.

These methods belong to MainWindow (ui_main.py), which inherits this class; they are
kept in their own file only to keep each file a readable size."""

from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from . import wadinfo
from .ui_common import active_preset_files

NAME_ROLE = QtCore.Qt.ItemDataRole.UserRole


class MapsMixin:
    """Per-map progress for the current preset."""

    def _build_maps_tab(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        col = QtWidgets.QVBoxLayout(page)
        self.maps_progress = QtWidgets.QLabel()
        self.maps_progress.setObjectName("muted")
        col.addWidget(self.maps_progress)
        self.maps_tree = QtWidgets.QTreeWidget()
        self.maps_tree.setColumnCount(3)
        self.maps_tree.setHeaderLabels(["Map (tick when finished)", "Title", "Played"])
        self.maps_tree.setRootIsDecorated(False)
        self.maps_tree.header().setStretchLastSection(False)
        self.maps_tree.header().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.maps_tree.itemChanged.connect(self._map_ticked)
        self.maps_tree.itemDoubleClicked.connect(lambda _i, _c: self.play_selected_map())
        col.addWidget(self.maps_tree, 1)
        hint = QtWidgets.QLabel(
            "Double-click a map to start on it. “Played” counts launches that started on that map "
            "(a normal start counts as its Start map, if one is set)."
        )
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        col.addWidget(hint)
        row = QtWidgets.QHBoxLayout()
        play = QtWidgets.QPushButton("Play this map")
        play.clicked.connect(lambda _c=False: self.play_selected_map())
        clear = QtWidgets.QPushButton("Clear progress")
        clear.clicked.connect(lambda _c=False: self._clear_map_progress())
        row.addWidget(play)
        row.addStretch(1)
        row.addWidget(clear)
        col.addLayout(row)
        return page

    def _preset_maps(self) -> list[tuple[str, str]]:
        """(map, title) for every map in the current preset's files, in first-seen order."""
        if self.current is None:
            return []
        seen: dict[str, str] = {}
        for path in active_preset_files(self.config, self.current):
            for name, title in wadinfo.list_maps(path):
                seen.setdefault(name, title)
        return list(seen.items())

    def _refresh_maps_tab(self) -> None:
        """Fill the Maps tab from the current preset (cheap: map lists are cached)."""
        tree = self.maps_tree
        tree.blockSignals(True)
        tree.clear()
        preset = self.current
        maps = self._preset_maps()
        if preset is None:
            tree.blockSignals(False)
            self._update_maps_progress(maps)
            return
        for name, title in maps:
            item = QtWidgets.QTreeWidgetItem([name, title, str(preset.map_plays.get(name.upper(), 0))])
            item.setFlags(item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                0, QtCore.Qt.CheckState.Checked if preset.map_is_done(name) else QtCore.Qt.CheckState.Unchecked
            )
            item.setData(0, NAME_ROLE, name)
            tree.addTopLevelItem(item)
        tree.resizeColumnToContents(0)
        tree.blockSignals(False)
        self._update_maps_progress(maps)

    def _update_maps_progress(self, maps: list[tuple[str, str]] | None = None) -> None:
        if self.current is None:
            self.maps_progress.setText("")
            return
        maps = self._preset_maps() if maps is None else maps
        if not maps:
            self.maps_progress.setText("This preset has no maps to list (add a map set on the Setup tab).")
            return
        done = sum(1 for name, _t in maps if self.current.map_is_done(name))
        self.maps_progress.setText(f"{done} of {len(maps)} map(s) finished.")

    def _map_ticked(self, item: QtWidgets.QTreeWidgetItem, column: int) -> None:
        if column != 0 or self.current is None:
            return
        self.current.set_map_done(
            item.data(0, NAME_ROLE), item.checkState(0) == QtCore.Qt.CheckState.Checked
        )
        self.save_config()
        self._update_maps_progress()

    def play_selected_map(self) -> None:
        """Start the preset on the highlighted map (without changing its saved start map)."""
        item = self.maps_tree.currentItem()
        if item is None or self.current is None:
            return
        if self._process is not None:
            self.statusBar().showMessage("A game is already running.", 5000)
            return
        self.launch_current(map_override=item.data(0, NAME_ROLE))

    def _clear_map_progress(self) -> None:
        if self.current is None or not (self.current.maps_done or self.current.map_plays):
            return
        answer = QtWidgets.QMessageBox.question(
            self, "Clear progress", "Forget which maps are finished and how often each was played?"
        )
        if answer == QtWidgets.QMessageBox.StandardButton.Yes:
            self.current.maps_done = []
            self.current.map_plays = {}
            self.save_config()
            self._refresh_maps_tab()
