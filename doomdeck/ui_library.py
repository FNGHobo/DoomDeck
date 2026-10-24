"""Library and Mapsets tabs: the sorted wads tree and the tick-box map set list.

These methods belong to MainWindow (ui_main.py), which inherits this class; they are
kept in their own file only to keep each file a readable size."""

from __future__ import annotations

from pathlib import Path
from PySide6 import QtCore, QtGui, QtWidgets
from . import library, wadinfo
from .ui_widgets import PickFilesDialog, PATH_ROLE
from .ui_common import add_action


class LibraryMixin:
    """Library and Mapsets tabs: the sorted wads tree and the tick-box map set list."""

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
        label = f"{label}  {'★' * rating}" if rating else label
        return f"{label}  ⬆" if key in self._updates else label

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
            self.right_tabs.setCurrentWidget(self.setup_page)

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
        source = meta.get("source") or {}
        add_action(menu, "Change update source…" if source else "Set update source…",
                   lambda: self._set_update_source(key))
        if key in self._updates:
            add_action(menu, "Update now" if source.get("kind") == "idgames" else "Open releases page",
                       lambda: self._update_project(key))
            add_action(menu, "Mark as up to date", lambda: self._mark_up_to_date(key))
        if source:
            add_action(menu, "Forget update source", lambda: self._forget_update_source(key))
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
        self._relabel_library()

    def _relabel_library(self) -> None:
        """Refresh every project's label (favorite, stars, update marker) without rebuilding the tree."""
        for c in range(self.tree.topLevelItemCount()):
            cat_item = self.tree.topLevelItem(c)
            for p in range(cat_item.childCount()):
                project = self._library[c].projects[p]
                key = f"{self._library[c].name}/{project.name}"
                cat_item.child(p).setText(0, self._project_label(key, project.name))
        self._filter_library()

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
        self.right_tabs.setCurrentWidget(self.setup_page)
        self._clear_mapset_ticks()
