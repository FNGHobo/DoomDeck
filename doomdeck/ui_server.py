"""Checking a multiplayer server's files from the window, and fetching what is missing.

The Multiplayer box has a "Check server" button for a profile that joins a game, and a
tick box that does the same automatically when Launch is pressed. Both ask the server
(Zandronum and Odamex servers can be asked), compare its files with your library, offer
to download what is missing (always asking first) and put the files in the profile in
the server's load order. The logic is in modsync.py and serverquery.py; this is only the
window part.
"""

from __future__ import annotations

from typing import Callable

from PySide6 import QtWidgets

from . import compat, modsync, serverquery
from .models import Preset
from .ui_workers import run_with_popup

YES = QtWidgets.QMessageBox.StandardButton.Yes
NO_QUERY_HELP = (
    "\n\nOnly Zandronum and Odamex servers can be asked. A UZDoom or GZDoom host cannot be "
    "queried: ask them to export their profile (Share) and use “Check files against a shared preset”."
)


class ServerMixin:
    """Mixed into the main window."""

    # ---- entry points ------------------------------------------------------------------------

    def check_server(self) -> None:
        """The "Check server" button: look at the server and offer to fetch what is missing."""
        preset = self.current
        if preset is None:
            return
        if not preset.net_address.strip():
            QtWidgets.QMessageBox.information(self, "Check server", "Enter the server's address first.")
            return
        self._server_flow(preset, None)

    def _server_sync_wanted(self, preset: Preset) -> bool:
        """True when Launch should check the server first (joining, ticked, a queryable engine)."""
        if preset.net_mode != "join" or not preset.net_autosync or not preset.net_address.strip():
            return False
        engine = self.config.engine_named(preset.engine)
        return engine is not None and compat.net_kind(engine.name, engine.path) != "zdoom"

    # ---- the flow ----------------------------------------------------------------------------

    def _server_query_text(self, preset: Preset) -> str:
        address = preset.net_address.strip()
        if preset.net_port > 0 and serverquery.split_address(address, 0)[1] == 0:
            address = f"{address}:{preset.net_port}"
        return address

    def _server_protocol(self, preset: Preset) -> str:
        engine = self.config.engine_named(preset.engine)
        kind = compat.net_kind(engine.name, engine.path) if engine is not None else "zdoom"
        return kind if kind in serverquery.PROTOCOLS else ""

    def _server_flow(self, preset: Preset, proceed: Callable[[], None] | None) -> None:
        """Query, compare, then offer to download. `proceed` runs when the files are ready."""
        settings = self.config.settings
        address, protocol = self._server_query_text(preset), self._server_protocol(preset)

        def work(worker):
            info = serverquery.query(address, protocol)
            index = modsync.library_index(settings)
            return info, modsync.compare(info, index), modsync.iwad_status(info, settings.iwads_dir)

        def failed(message: str) -> None:
            QtWidgets.QMessageBox.warning(self, "Check server", message + NO_QUERY_HELP)

        run_with_popup(self, f"Asking {address}...", work,
                       lambda result: self._server_answered(preset, result, proceed), failed)

    def _server_answered(self, preset: Preset, result, proceed) -> None:
        info, matches, iwad_note = result
        text = modsync.describe(info, matches, iwad_note)
        missing = modsync.needed(matches)
        paths, absent = modsync.load_order(matches)
        if iwad_note:
            QtWidgets.QMessageBox.warning(self, "Check server", text)
            return
        if not missing:
            same = paths == [f for f in preset.files if f not in preset.disabled_files]
            if same:
                if proceed is not None:
                    proceed()
                else:
                    QtWidgets.QMessageBox.information(self, "Check server", text + "\n\nYour profile already matches.")
                return
            self._server_offer_apply(preset, info, matches, text, proceed)
            return
        size_note = f"\n\n{len(missing)} file(s) are missing or different. Download them now?"
        if QtWidgets.QMessageBox.question(self, "Check server", text + size_note) != YES:
            return
        self._server_download(preset, info, matches, proceed)

    def _server_download(self, preset: Preset, info, matches, proceed) -> None:
        wads_root = self.config.settings.wads_root

        def work(worker):
            return modsync.fetch_missing(info, matches, wads_root, worker.report, worker.cancelled)

        def done(report) -> None:
            failed = report.failed
            if failed:
                lines = "\n".join(f"✖ {r.name}: {r.message}" for r in failed[:12])
                site = f"\n\nThe server's website may have them: {info.website}" if info.website else ""
                QtWidgets.QMessageBox.warning(
                    self, "Check server",
                    f"{len(failed)} file(s) could not be downloaded:\n{lines}{site}")
                return
            got = "\n".join(f"✔ {r.name} (from {r.source})" for r in report.results[:12])
            self.statusBar().showMessage(f"Downloaded {len(report.results)} file(s) for the server.", 8000)
            self._server_offer_apply(preset, info, matches, got, proceed, downloaded=True)

        run_with_popup(self, "Downloading the server's files...", work, done)

    def _server_offer_apply(self, preset: Preset, info, matches, text, proceed, downloaded=False) -> None:
        paths, absent = modsync.load_order(matches)
        if absent:  # still something we could not get: do not touch the profile
            QtWidgets.QMessageBox.warning(self, "Check server", "Still missing: " + ", ".join(absent))
            return
        question = ("Use the server's files, in its load order, for this profile?\n"
                    "(Your current file list will be replaced.)")
        if QtWidgets.QMessageBox.question(self, "Check server", f"{text}\n\n{question}") != YES:
            return
        self.file_list.set_files(paths, [])
        iwad = modsync.iwad_name(info, self.config.settings.iwads_dir)
        if iwad:
            index = self.iwad_combo.findText(iwad)
            if index >= 0:
                self.iwad_combo.setCurrentIndex(index)
        self._files_changed()
        self.statusBar().showMessage("Profile now matches the server's files.", 8000)
        if proceed is not None:
            proceed()
