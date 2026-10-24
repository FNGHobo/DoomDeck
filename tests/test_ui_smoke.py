"""Drives the window code against the Qt stand-in (tests/qtstub.py).

These tests do not prove the window looks right; they prove that DoomDeck's own window
code runs: it builds, buttons and menus lead somewhere, and the flows (get a wad,
export and import a preset, install an engine, track map progress)
change the config the way they should.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

from tests import qtstub

qtstub.install()

from doomdeck import config as config_io  # noqa: E402
from doomdeck import engines, idgames, netio, ui_main, ui_settings, updates  # noqa: E402
from doomdeck.models import Preset  # noqa: E402
from tests.test_core import Base, make_wad  # noqa: E402


def make_window(case: Base):
    """A MainWindow over the test config, with its app folder inside the temp dir."""
    app_dir = case.root / "app"
    app_dir.mkdir(exist_ok=True)
    patcher = mock.patch.object(config_io, "APP_DIR", app_dir)
    patcher.start()
    case.addCleanup(patcher.stop)
    window = ui_main.MainWindow(case.config, case.root / "doomdeck.json")
    case.addCleanup(window.__dict__.update, {})
    return window


class SmokeBase(Base):
    def setUp(self):
        super().setUp()
        qtstub.CANNED.clear()
        qtstub.LOG.clear()
        self.window = make_window(self)
        self.config.presets.append(
            Preset(name="Evit", engine="UZDoom", iwad="DOOM2.WAD", files=[self.evit])
        )
        self.window.refresh_presets()
        self.window._select_preset(self.config.presets[0])


class WindowTests(SmokeBase):
    def test_builds_with_all_tabs(self):
        w = self.window
        for name in ("presets_page", "mapsets_page", "library_page", "online_page",
                     "setup_page", "maps_page", "shots_page", "output_page"):
            self.assertIn(name, vars(w))  # the stub invents attributes, so look in __dict__
        self.assertEqual(w.preset_list.count(), 1)
        self.assertIn("uzdoom", w.command_edit.text())

    def test_library_lists_projects(self):
        w = self.window
        names = [w.tree.topLevelItem(i).text(0) for i in range(w.tree.topLevelItemCount())]
        self.assertTrue(any(n.startswith("maps") for n in names), names)

    def test_maps_tab_progress(self):
        w = self.window
        self.assertEqual(w.maps_tree.topLevelItemCount(), 3)  # MAP01, MAP02, MAP10
        from doomdeck import ui_maps

        item = w.maps_tree.topLevelItem(0)
        checked = ui_maps.QtCore.Qt.CheckState.Checked
        item.setCheckState(0, checked)
        w._map_ticked(item, 0)
        self.assertTrue(self.config.presets[0].map_is_done("MAP01"))
        self.assertIn("1 of 3", w.maps_progress.text())
        qtstub.CANNED["question"] = "Yes"
        w._clear_map_progress()
        self.assertEqual(self.config.presets[0].maps_done, [])

    def test_play_selected_map_launches_with_override(self):
        w = self.window
        w.maps_tree.setCurrentItem(w.maps_tree.topLevelItem(1))
        seen = {}

        def fake_launch(config, preset, log_dir, **options):
            seen.update(options)
            process = mock.Mock()
            process.poll.return_value = None
            return process, ["uzdoom"], Path(self.root / "x.log")

        with mock.patch("doomdeck.ui_main.launch.launch", fake_launch):
            w.play_selected_map()
        self.assertEqual(seen.get("map_override"), "MAP02")
        self.assertEqual(w.launch_button.text(), "Stop")

    def test_menus_build_and_settings_roundtrip(self):
        w = self.window
        qtstub.CANNED["dialog_exec"] = qtstub.QDialog.DialogCode.Accepted
        w.open_settings("engines")
        self.assertEqual(self.config.engines[0].name, "UZDoom")

    def test_close_on_launch_checkbox_saves(self):
        w = self.window
        w.close_check.setChecked(True)
        self.assertTrue(self.config.settings.close_on_launch)


class OnlineTests(SmokeBase):
    def fake_file(self):
        return idgames.IdgamesFile(
            id=7, title="Tiny Test", dir="levels/doom2/t/", filename="tiny.zip", size=1000,
            age=111, author="me", description="a map",
        )

    def test_search_fills_results(self):
        w = self.window
        files = [self.fake_file()]
        w.online_search.setText("tiny")
        with mock.patch.object(idgames, "search", lambda *a, **k: files):
            w.online_find()
        self.assertEqual(w.online_list.count(), 1)
        self.assertIn("Tiny Test", w.online_info.toPlainText())
        self.assertTrue(w.online_get.isEnabled())

    def test_search_by_pasted_link_uses_get(self):
        w = self.window
        w.online_search.setText("https://www.doomworld.com/idgames/levels/doom2/t/tiny")
        with mock.patch.object(idgames, "get", lambda fid, path, **k: self.fake_file()) as got:
            w.online_find()
        self.assertEqual(w.online_list.count(), 1)
        self.assertTrue(got)

    def test_install_records_source_and_offers_preset(self):
        import io
        import zipfile

        w = self.window
        w._online_results = [self.fake_file()]
        w.online_list.addItem("x")
        w.online_list.setCurrentRow(0)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("tiny/tiny.wad", b"PWAD" + b"\0" * 8)
            archive.writestr("tiny/tiny.txt", "hello")

        def fake_download(file, dest, progress=None, cancelled=None, mirrors=None):
            Path(dest).parent.mkdir(parents=True, exist_ok=True)
            Path(dest).write_bytes(buffer.getvalue())
            return Path(dest)

        with mock.patch.object(idgames, "download", fake_download):
            w.online_install()
        folder = Path(self.config.settings.wads_root) / "maps" / "Tiny Test"
        self.assertTrue((folder / "tiny.wad").is_file())
        source = self.config.library_meta["maps/Tiny Test"]["source"]
        self.assertEqual((source["kind"], source["id"], source["stamp"]), ("idgames", 7, 111))
        self.assertTrue(any(p.name == "Tiny Test" for p in self.config.presets))

    def test_install_asks_before_replacing(self):
        w = self.window
        folder = Path(self.config.settings.wads_root) / "maps" / "Tiny Test"
        folder.mkdir(parents=True)
        (folder / "mine.wad").write_bytes(b"PWAD")
        w._online_results = [self.fake_file()]
        w.online_list.addItem("x")
        w.online_list.setCurrentRow(0)
        qtstub.CANNED["question"] = "No"
        with mock.patch.object(idgames, "download", side_effect=AssertionError("must not download")):
            w.online_install()
        self.assertTrue((folder / "mine.wad").is_file())

    def test_update_tracking_flow(self):
        w = self.window
        key = "maps/Eviternity"
        qtstub.CANNED["text"] = ("idgames:7", True)
        with mock.patch.object(updates, "baseline", lambda source, fetch=None: dict(source, stamp=100, id=7, title="Evit")):
            w._set_update_source(key)
        self.assertEqual(self.config.library_meta[key]["source"]["stamp"], 100)

        newer = updates.UpdateInfo(key, "idgames", "100", "200", title="Evit")
        with mock.patch.object(updates, "check_all", lambda meta, fetch=None: [newer]):
            w.check_for_updates()
        self.assertIn(key, w._updates)
        w._mark_up_to_date(key)
        self.assertEqual(self.config.library_meta[key]["source"]["stamp"], "200")
        self.assertNotIn(key, w._updates)
        w._forget_update_source(key)
        self.assertNotIn("source", self.config.library_meta[key])

    def test_check_updates_without_sources_explains(self):
        self.window.check_for_updates()
        self.assertTrue(any("update source" in text.lower() for _k, _t, text in qtstub.LOG))


class SharingTests(SmokeBase):
    def test_export_then_import_checks_files(self):
        w = self.window
        out = self.root / "share.json"
        qtstub.CANNED["save_file"] = (str(out), "")
        w.export_preset()
        data = json.loads(out.read_text())
        self.assertEqual(data["doomdeck_preset"], 1)
        self.assertEqual(len(data["files"][0]["sha256"]), 64)

        qtstub.CANNED["open_file"] = (str(out), "")
        w.import_preset()
        self.assertEqual(len(self.config.presets), 2)
        self.assertEqual(self.config.presets[1].files, [self.evit])
        self.assertTrue(any("Imported" in text for _k, _t, text in qtstub.LOG))

    def test_import_reports_a_different_file(self):
        w = self.window
        out = self.root / "share.json"
        qtstub.CANNED["save_file"] = (str(out), "")
        w.export_preset()
        make_wad(Path(self.evit), ["MAP01"])  # now different from what was exported
        qtstub.CANNED["open_file"] = (str(out), "")
        qtstub.LOG.clear()
        w.check_shared_preset()
        kind, _title, text = qtstub.LOG[-1]
        self.assertEqual(kind, "warning")
        self.assertIn("different", text)

    def test_old_plain_export_still_imports(self):
        w = self.window
        out = self.root / "old.json"
        out.write_text(json.dumps({"name": "Old one", "engine": "UZDoom", "files": [self.evit]}))
        qtstub.CANNED["open_file"] = (str(out), "")
        w.import_preset()
        self.assertIsNotNone(self.config.preset_named("Old one"))


class SettingsTests(SmokeBase):
    def make_dialog(self):
        return ui_settings.SettingsDialog(self.config, self.window)

    def test_no_global_arguments_or_duplicate_close_option(self):
        dialog = self.make_dialog()
        self.assertNotIn("global_args", vars(dialog))
        self.assertNotIn("close_on_launch", vars(dialog))
        self.assertIn("mapsets_category", vars(dialog))

    def test_engine_fields_roundtrip(self):
        dialog = self.make_dialog()
        dialog.engine_version.type_text("4.14.0")
        dialog.engine_repo.type_text("UZDoom/UZDoom")
        engine = dialog.config.engines[0]
        self.assertEqual((engine.version, engine.update_repo), ("4.14.0", "UZDoom/UZDoom"))
        dialog.mapsets_category.setText("mapsets")
        dialog._accept()
        self.assertEqual(dialog.config.settings.mapsets_category, "mapsets")

    def test_install_latest_adds_engine_and_switches_presets(self):
        dialog = self.make_dialog()
        dialog.engine_repo.type_text("UZDoom/UZDoom")
        release = engines.Release(tag="v4.15.0", assets=[
            engines.Asset("UZDoom-4.15.0-windows.zip", "https://x/win.zip"),
            engines.Asset("UZDoom-4.15.0-linux-x86_64.tar.gz", "https://x/linux.tar.gz"),
        ])
        program = self.root / "app" / "engines" / "v4.15.0" / "uzdoom"
        program.parent.mkdir(parents=True)
        program.write_text("#!/bin/sh\n")
        with mock.patch.object(engines, "latest_release", lambda repo, **k: release), \
                mock.patch.object(engines, "install_asset", lambda *a, **k: program):
            dialog._install_engine_update()
        names = [e.name for e in dialog.config.engines]
        self.assertEqual(names, ["UZDoom", "UZDoom v4.15.0"])
        self.assertEqual(dialog.config.engines[1].version, "v4.15.0")
        result = dialog.result_config(self.config)
        self.assertEqual(result.presets[0].engine, "UZDoom v4.15.0")

    def test_check_engine_update_reports(self):
        dialog = self.make_dialog()
        dialog.engine_repo.type_text("UZDoom/UZDoom")
        dialog.engine_version.type_text("4.14.0")
        release = engines.Release(tag="4.15.0", published="2026-01-01")
        with mock.patch.object(engines, "latest_release", lambda repo, **k: release):
            dialog._check_engine_update()
        self.assertTrue(any("newer version" in text for _k, _t, text in qtstub.LOG))

    def test_update_failure_is_reported_not_raised(self):
        dialog = self.make_dialog()
        dialog.engine_repo.type_text("UZDoom/UZDoom")

        def boom(repo, **k):
            raise netio.NetError("offline")

        with mock.patch.object(engines, "latest_release", boom):
            dialog._check_engine_update()
        self.assertTrue(any("offline" in text for _k, _t, text in qtstub.LOG))


if __name__ == "__main__":
    unittest.main()
