"""Per-preset tabs: saves, demos, screenshots, picture, info and output.

These methods belong to MainWindow (ui_main.py), which inherits this class; they are
kept in their own file only to keep each file a readable size."""

from __future__ import annotations

import json
import time
from pathlib import Path
from PySide6 import QtCore, QtGui, QtWidgets
from . import config as config_io
from . import launch, userdata, wadinfo
from .models import safe_name
from .ui_widgets import PATH_ROLE
from .ui_common import PICTURE_MAX, PICTURE_BOX, add_action, active_preset_files, format_date


class UserDataMixin:
    """Per-preset tabs: saves, demos, screenshots, picture, info and output."""

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

    def _dirs(self) -> launch.DataDirs | None:
        return launch.data_dirs(self.config.settings, self.current) if self.current else None

    def _refresh_user_tabs(self) -> None:
        """Reload whichever of the file tabs is visible (cheap, so no caching)."""
        dirs = self._dirs()
        self._refresh_maps_tab()
        self.saves_list.clear()
        self.demos_list.clear()
        self.shots_list.clear()
        if dirs is None:
            return
        for entry in userdata.list_saves(dirs.saves):
            self._add_user_item(self.saves_list, entry)
        for entry in userdata.list_demos(dirs.demos):
            self._add_user_item(self.demos_list, entry)
        if self.right_tabs.currentWidget() is self.shots_page:  # thumbnails only when visible
            for entry in userdata.list_screenshots(dirs.screenshots)[:80]:
                thumb = self._thumbnail(entry.path)
                if thumb is None:
                    continue  # unreadable image: leave it out rather than show a blank icon
                item = QtWidgets.QListWidgetItem(QtGui.QIcon(thumb), entry.path.name)
                item.setData(PATH_ROLE, str(entry.path))
                self.shots_list.addItem(item)

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
        item = QtWidgets.QListWidgetItem(f"{entry.path.name}    {format_date(entry.modified)}")
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
