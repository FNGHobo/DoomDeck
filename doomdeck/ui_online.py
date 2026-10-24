"""The "Get WADs" tab (search and install from the Doomworld /idgames archive) and the
update-tracking actions of the Library tab.

These methods belong to MainWindow (ui_main.py), which inherits this class; they are
kept in their own file only to keep each file a readable size."""

from __future__ import annotations

from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from . import config as config_io
from . import idgames, updates
from .ui_widgets import PickFilesDialog
from .ui_workers import run_with_popup, start


def _megabytes(size: int) -> str:
    return f"{size / (1024 * 1024):.1f} MB" if size else "size unknown"


class OnlineMixin:
    """Get WADs tab, plus checking and applying updates for library projects."""

    # ---- the Get WADs tab ----------------------------------------------------------
    def _build_online_tab(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        col = QtWidgets.QVBoxLayout(page)

        row = QtWidgets.QHBoxLayout()
        self.online_search = QtWidgets.QLineEdit()
        self.online_search.setPlaceholderText("Search /idgames, or paste a Doomworld link or file id…")
        self.online_search.returnPressed.connect(self.online_find)
        self.online_kind = QtWidgets.QComboBox()
        self.online_kind.addItems(list(idgames.SEARCH_FIELDS))
        self.online_kind.setToolTip("Which part of each entry to search")
        row.addWidget(self.online_search, 1)
        row.addWidget(self.online_kind)
        col.addLayout(row)

        row = QtWidgets.QHBoxLayout()
        self.online_sort = QtWidgets.QComboBox()
        self.online_sort.addItems(list(idgames.SORTS))
        find = QtWidgets.QPushButton("Search")
        find.clicked.connect(lambda _c=False: self.online_find())
        newest = QtWidgets.QPushButton("Newest uploads")
        newest.clicked.connect(lambda _c=False: self.online_latest())
        row.addWidget(QtWidgets.QLabel("Sort by"))
        row.addWidget(self.online_sort)
        row.addStretch(1)
        row.addWidget(newest)
        row.addWidget(find)
        col.addLayout(row)

        self.online_list = QtWidgets.QListWidget()
        self.online_list.currentItemChanged.connect(self._online_selected)
        self.online_list.itemDoubleClicked.connect(lambda _i: self.online_install())
        col.addWidget(self.online_list, 1)

        self.online_info = QtWidgets.QPlainTextEdit()
        self.online_info.setReadOnly(True)
        self.online_info.setMaximumHeight(170)
        self.online_info.setPlaceholderText("Select a result to read its description.")
        col.addWidget(self.online_info)

        self.online_status = QtWidgets.QLabel(
            "Downloads go into your wads folder, sorted by type (maps go to the maps folder)."
        )
        self.online_status.setObjectName("muted")
        self.online_status.setWordWrap(True)
        col.addWidget(self.online_status)
        bar_row = QtWidgets.QHBoxLayout()
        self.online_bar = QtWidgets.QProgressBar()
        self.online_bar.setVisible(False)
        self.online_cancel = QtWidgets.QPushButton("Cancel")
        self.online_cancel.setVisible(False)
        bar_row.addWidget(self.online_bar, 1)
        bar_row.addWidget(self.online_cancel)
        col.addLayout(bar_row)

        row = QtWidgets.QHBoxLayout()
        self.online_get = QtWidgets.QPushButton("Download and install")
        self.online_get.setEnabled(False)
        self.online_get.clicked.connect(lambda _c=False: self.online_install())
        self.online_page_button = QtWidgets.QPushButton("Open page")
        self.online_page_button.setEnabled(False)
        self.online_page_button.clicked.connect(self._online_open_page)
        row.addWidget(self.online_get)
        row.addWidget(self.online_page_button)
        col.addLayout(row)
        self._online_results: list[idgames.IdgamesFile] = []
        self._online_busy = False
        return page

    def show_online_tab(self) -> None:
        """Bring the Get WADs tab to the front and focus its search box."""
        self.left_tabs.setCurrentWidget(self.online_page)
        self.online_search.setFocus()

    def _online_set_busy(self, text: str | None) -> None:
        """Show or clear the 'working' state of the tab."""
        self._online_busy = text is not None
        if text is not None:
            self.online_status.setText(text)
        self.online_get.setEnabled(not self._online_busy and self._online_current() is not None)
        self.online_bar.setVisible(self._online_busy)
        self.online_cancel.setVisible(False)
        if self._online_busy:
            self.online_bar.setRange(0, 0)

    def _online_failed(self, message: str) -> None:
        self._online_set_busy(None)
        self.online_status.setText("Failed: " + message)

    def online_find(self) -> None:
        """Search the archive (or look up a pasted link / id)."""
        text = self.online_search.text().strip()
        if not text or self._online_busy:
            return
        file_id, path = idgames.parse_page_link(text)
        if file_id or path:
            def work(_w):
                return [idgames.get(file_id or None, path)]
        else:
            kind = idgames.SEARCH_FIELDS[self.online_kind.currentText()]
            sort, direction = idgames.SORTS[self.online_sort.currentText()]

            def work(_w):
                return idgames.search(text, kind, sort, direction)
        self._online_set_busy("Searching…")
        start(self, work, self._online_results_ready, self._online_failed)

    def online_latest(self) -> None:
        if self._online_busy:
            return
        self._online_set_busy("Getting the newest uploads…")
        start(self, lambda _w: idgames.latest(40), self._online_results_ready, self._online_failed)

    def _online_results_ready(self, files) -> None:
        self._online_set_busy(None)
        self._online_results = list(files)[:200]
        self.online_list.clear()
        for found in self._online_results:
            stars = f"  ★{found.rating:.1f}" if found.rating else ""
            item = QtWidgets.QListWidgetItem(
                f"{found.title or found.filename}  -  {found.author or 'unknown'}{stars}"
            )
            item.setToolTip(found.path or found.filename)
            self.online_list.addItem(item)
        self.online_status.setText(
            f"{len(self._online_results)} result(s)." if self._online_results else "Nothing found."
        )
        if self._online_results:
            self.online_list.setCurrentRow(0)
        else:
            self.online_info.setPlainText("")

    def _online_current(self) -> idgames.IdgamesFile | None:
        row = self.online_list.currentRow()
        return self._online_results[row] if 0 <= row < len(self._online_results) else None

    def _online_selected(self, *_args) -> None:
        found = self._online_current()
        self.online_get.setEnabled(found is not None and not self._online_busy)
        self.online_page_button.setEnabled(found is not None and bool(found.url or found.id))
        if found is None:
            self.online_info.setPlainText("")
            return
        head = [found.title or found.filename]
        meta = [f"by {found.author}" if found.author else "", found.date, _megabytes(found.size),
                f"rating {found.rating:.1f} ({found.votes} votes)" if found.votes else ""]
        head.append(" · ".join(x for x in meta if x))
        if found.path:
            head.append(found.path)
        body = "\n".join(head) + "\n\n" + found.description.strip()
        if found.textfile.strip():
            body += "\n\n----\n" + found.textfile.strip()[:4000]
        self.online_info.setPlainText(body)

    def _online_open_page(self) -> None:
        found = self._online_current()
        if found is None:
            return
        url = found.url or f"https://www.doomworld.com/idgames/?id={found.id}"
        QtGui.QDesktopServices.openUrl(QtCore.QUrl(url))

    def online_install(self, overwrite: bool = False) -> None:
        """Download the selected file and unpack it into the sorted wads folder."""
        found = self._online_current()
        if found is None or self._online_busy:
            return
        settings = self.config.settings
        wads_root, mapsets = settings.wads_root, settings.mapsets_category
        downloads = config_io.APP_DIR / "downloads"

        def work(worker):
            full = found if (found.filename and found.dir) else idgames.get(found.id or None, found.path)
            folder = (Path(wads_root).expanduser() / idgames.category_for(full.dir, mapsets)
                      / full.project_name)
            if not overwrite and folder.is_dir() and any(folder.iterdir()):
                return full, None  # the window asks before replacing anything
            archive = idgames.download(full, downloads / full.filename, worker.report, worker.cancelled)
            try:
                return full, idgames.install(full, archive, wads_root, mapsets_category=mapsets)
            finally:
                archive.unlink(missing_ok=True)

        def progress(done: int, total: int) -> None:
            if total > 0:
                self.online_bar.setRange(0, total)
                self.online_bar.setValue(done)
                self.online_status.setText(f"Downloading… {_megabytes(done)} of {_megabytes(total)}")
            else:
                self.online_status.setText(f"Downloading… {_megabytes(done)}")

        self._online_set_busy(f"Downloading {found.title or found.filename}…")
        worker = start(self, work, self._online_installed, self._online_failed, progress)
        self.online_cancel.setVisible(True)
        try:
            self.online_cancel.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        self.online_cancel.clicked.connect(lambda _c=False: worker.cancel())

    def _online_installed(self, outcome) -> None:
        full, result = outcome
        self._online_set_busy(None)
        if result is None:
            answer = QtWidgets.QMessageBox.question(
                self, "Already in your library",
                f"“{full.project_name}” is already in your library.\n"
                "Download it again and replace files of the same name?",
            )
            if answer == QtWidgets.QMessageBox.StandardButton.Yes:
                self.online_install(overwrite=True)
            else:
                self.online_status.setText("Left the existing copy alone.")
            return
        key = f"{result.category}/{result.folder.name}"
        self.config.library_meta.setdefault(key, {})["source"] = idgames.source_record(full)
        self.save_config()
        self._updates.pop(key, None)
        self.refresh_library()
        self.online_status.setText(f"Installed {len(result.files)} file(s) into {result.folder}")
        answer = QtWidgets.QMessageBox.question(
            self, "Installed", f"Installed “{full.project_name}”.\nMake a preset from it now?"
        )
        if answer == QtWidgets.QMessageBox.StandardButton.Yes:
            files = self._files_of_project(key)
            if files:
                self._add_and_select(self._default_preset(result.folder.name, files))

    def _find_project(self, key: str):
        """The library Project for a "category/project" key, or None."""
        for category in self._library_all:
            for project in category.projects:
                if f"{category.name}/{project.name}" == key:
                    return project
        return None

    def _files_of_project(self, key: str) -> list[str]:
        """Pick the loadable files of a project (asking when there are several)."""
        project = self._find_project(key)
        if project is None or not project.files:
            return []
        if len(project.files) == 1:
            return [str(project.files[0].path)]
        dialog = PickFilesDialog(project.name, [(f.rel, str(f.path)) for f in project.files], self)
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return []
        return dialog.chosen()

    # ---- update tracking for library projects ----------------------------------------
    def check_for_updates(self) -> None:
        """Ask every source what is newest and mark the projects that have a newer version."""
        meta = {key: dict(value) for key, value in self.config.library_meta.items()}
        if not any(isinstance(v.get("source"), dict) for v in meta.values()):
            QtWidgets.QMessageBox.information(
                self, "Check for updates",
                "No project has an update source yet.\n\nProjects installed from Get WADs get one "
                "automatically. For others, right-click a project in the Library tab and "
                "choose “Set update source…”.",
            )
            return
        self.statusBar().showMessage("Checking for updates…")
        start(self, lambda _w: updates.check_all(meta), self._updates_checked,
              lambda message: self.statusBar().showMessage("Update check failed: " + message, 8000))

    def _updates_checked(self, results) -> None:
        self._updates = {r.key: r for r in results if r.is_update}
        self._relabel_library()
        self.statusBar().showMessage(f"{len(self._updates)} update(s) available", 8000)
        QtWidgets.QMessageBox.information(self, "Check for updates", updates.report_text(results))

    def _set_update_source(self, key: str) -> None:
        current = self.config.library_meta.get(key, {}).get("source") or {}
        shown = current.get("repo") or (f"idgames:{current['id']}" if current.get("id") else "")
        text, ok = QtWidgets.QInputDialog.getText(
            self, "Update source",
            "Where does this project come from?\n"
            "A Doomworld /idgames link or file id, or a GitHub link or owner/repository.\n"
            "What you have now is taken to be the current version.",
            text=shown,
        )
        if not ok:
            return
        text = text.strip()
        if not text:
            self._forget_update_source(key)
            return
        source = updates.parse_source(text)
        if source is None:
            QtWidgets.QMessageBox.warning(self, "Update source", "That does not look like an idgames or GitHub address.")
            return
        self.statusBar().showMessage("Contacting the source…")

        def done(record) -> None:
            self.config.library_meta.setdefault(key, {})["source"] = record
            self._updates.pop(key, None)
            self.save_config()
            self._relabel_library()
            self.statusBar().showMessage(
                f"Tracking updates for {key} ({record.get('title') or record.get('repo') or record.get('id')})", 8000
            )

        start(self, lambda _w: updates.baseline(source), done,
              lambda message: QtWidgets.QMessageBox.warning(self, "Update source", message))

    def _forget_update_source(self, key: str) -> None:
        self.config.library_meta.get(key, {}).pop("source", None)
        self._updates.pop(key, None)
        self.save_config()
        self._relabel_library()

    def _mark_up_to_date(self, key: str) -> None:
        info = self._updates.get(key)
        source = self.config.library_meta.get(key, {}).get("source")
        if info is None or not source:
            return
        source["stamp"] = info.latest
        self._updates.pop(key, None)
        self.save_config()
        self._relabel_library()

    def _update_project(self, key: str) -> None:
        """Re-download an idgames project into its own folder (GitHub ones open the releases page)."""
        source = self.config.library_meta.get(key, {}).get("source") or {}
        if source.get("kind") == "github":
            QtGui.QDesktopServices.openUrl(
                QtCore.QUrl(f"https://github.com/{source.get('repo', '')}/releases/latest")
            )
            self.statusBar().showMessage(
                "Opened the releases page. Use “Mark as up to date” after you have installed it.", 10000
            )
            return
        project = self._find_project(key)
        if project is None or source.get("kind") != "idgames":
            return
        folder = project.path
        downloads = config_io.APP_DIR / "downloads"

        def work(worker):
            full = idgames.get(source.get("id") or None, source.get("path", ""))
            archive = idgames.download(full, downloads / full.filename, worker.report, worker.cancelled)
            try:
                idgames.extract_archive(archive, folder)
            finally:
                archive.unlink(missing_ok=True)
            return full

        def done(full) -> None:
            self.config.library_meta.setdefault(key, {})["source"] = idgames.source_record(full)
            self._updates.pop(key, None)
            self.save_config()
            self.refresh_library()
            self.refresh_presets()
            self.statusBar().showMessage(f"Updated {key}", 8000)

        run_with_popup(self, f"Updating {key}…", work, done,
                       lambda message: QtWidgets.QMessageBox.warning(self, "Update", message))
