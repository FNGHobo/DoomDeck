"""Tests for the Qt-free core: library scan, WAD reading, command builder, config."""

import os
import struct
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

from doomdeck import config as cfgmod
from doomdeck import importer, launch, library, userdata, wadinfo
from doomdeck.models import AppConfig, Engine, Preset, Settings, parse_tags, sort_presets


def make_wad(path: Path, lump_names: list[str], mapinfo: str = "") -> None:
    """Write a minimal valid PWAD whose directory has the given empty lumps."""
    data = b""
    entries = []
    if mapinfo:
        blob = mapinfo.encode()
        entries.append(("MAPINFO", 12 + len(data), len(blob)))
        data += blob
    directory_at = 12 + len(data)
    directory = b"".join(
        struct.pack("<ii8s", off, size, n.encode().ljust(8, b"\0"))
        for n, off, size in entries
    ) + b"".join(struct.pack("<ii8s", 0, 0, n.encode().ljust(8, b"\0")) for n in lump_names)
    count = len(entries) + len(lump_names)
    path.write_bytes(b"PWAD" + struct.pack("<ii", count, directory_at) + data + directory)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

        # Fake engine, iwad and sorted wads tree.
        self.engine_path = self.root / "UZDoom" / "uzdoom"
        self.engine_path.parent.mkdir()
        self.engine_path.write_text("#!/bin/sh\n")
        self.engine_path.chmod(0o755)
        (self.root / "iwads").mkdir()
        (self.root / "iwads" / "DOOM2.WAD").write_bytes(b"IWAD" + b"\0" * 8)
        wads = self.root / "wads"
        (wads / "maps" / "Eviternity").mkdir(parents=True)
        make_wad(wads / "maps" / "Eviternity" / "Evit.wad", ["MAP01", "THINGS", "MAP02", "MAP10"])
        (wads / "gameplay-mods" / "brutal doom").mkdir(parents=True)
        (wads / "gameplay-mods" / "brutal doom" / "brutal.pk3").write_bytes(b"x")
        (wads / "gameplay-mods" / "brutal doom" / "fix.deh").write_text("x")
        (wads / "gameplay-mods" / "brutal doom" / "readme.txt").write_text("hello")
        (wads / "stray").mkdir()
        (wads / "stray" / "loose.wad").write_bytes(b"x")

        self.config = AppConfig(
            settings=Settings(
                wads_root=str(wads),
                iwads_dir=str(self.root / "iwads"),
                data_root=str(self.root / "data"),
            ),
            engines=[Engine("UZDoom", str(self.engine_path))],
        )
        self.evit = str(wads / "maps" / "Eviternity" / "Evit.wad")
        self.brutal = str(wads / "gameplay-mods" / "brutal doom" / "brutal.pk3")
        self.deh = str(wads / "gameplay-mods" / "brutal doom" / "fix.deh")


class LibraryTests(Base):
    def test_scan_sorted_layout(self):
        cats = {c.name: c for c in library.scan_library(self.config.settings.wads_root)}
        self.assertEqual(set(cats), {"maps", "gameplay-mods", "(no category)"})
        brutal = cats["gameplay-mods"].projects[0]
        self.assertEqual(brutal.name, "brutal doom")
        self.assertEqual([f.rel for f in brutal.files], ["brutal.pk3", "fix.deh"])
        self.assertEqual(cats["(no category)"].projects[0].name, "stray")

    def test_missing_root(self):
        self.assertEqual(library.scan_library(self.root / "nope"), [])

    def test_iwads(self):
        self.assertEqual([p.name for p in library.scan_iwads(self.config.settings.iwads_dir)], ["DOOM2.WAD"])

    def test_top_level_names(self):
        self.assertEqual(
            sorted(library.top_level_names(self.config.settings.wads_root)),
            ["gameplay-mods", "maps", "stray"],
        )
        self.assertEqual(library.top_level_names(self.root / "nope"), [])

    def test_extra_folders_join_the_library(self):
        # An extra folder with sub-folders becomes a category of projects...
        extra = self.root / "elsewhere" / "Old Stuff"
        (extra / "Cool Pack").mkdir(parents=True)
        make_wad(extra / "Cool Pack" / "cool.wad", ["MAP01"])
        # ...and one that holds files directly becomes a single project.
        single = self.root / "elsewhere" / "Solo"
        single.mkdir()
        make_wad(single / "solo.wad", ["MAP01"])
        cats = {
            c.name: c for c in library.scan_library(
                self.config.settings.wads_root, [extra, single, self.root / "missing"]
            )
        }
        self.assertEqual([p.name for p in cats["Old Stuff"].projects], ["Cool Pack"])
        self.assertEqual(
            sorted(p.name for p in cats[library.LOOSE_NAME].projects), ["Solo", "stray"]
        )
        self.assertNotIn("missing", cats)

    def test_hidden_folders_are_dropped(self):
        cats = library.scan_library(self.config.settings.wads_root)
        shown = library.apply_hidden(cats, ["gameplay-mods", "stray"])
        self.assertEqual([c.name for c in shown], ["maps"])  # stray was the only loose project
        self.assertEqual(len(cats), 3, "the scanned list itself must not change")
        self.assertEqual(
            [c.name for c in library.apply_hidden(cats, [])], [c.name for c in cats]
        )


class WadInfoTests(Base):
    def test_maps_sorted_numerically(self):
        self.assertEqual([m for m, _ in wadinfo.list_maps(self.evit)], ["MAP01", "MAP02", "MAP10"])

    def test_mapinfo_titles(self):
        p = self.root / "t.wad"
        make_wad(p, ["MAP01"], mapinfo='map MAP01 "First Steps"\n')
        self.assertEqual(wadinfo.list_maps(p), [("MAP01", "First Steps")])

    def test_map_list_cached_until_file_changes(self):
        """A second call must not re-read the file; a changed file must be re-read."""
        p = self.root / "cache.wad"
        make_wad(p, ["MAP01"])
        self.assertEqual([m for m, _ in wadinfo.list_maps(p)], ["MAP01"])
        reads = []
        original = wadinfo._read_maps
        wadinfo._read_maps = lambda path: reads.append(path) or original(path)
        self.addCleanup(setattr, wadinfo, "_read_maps", original)
        wadinfo.list_maps(p)
        self.assertEqual(reads, [], "unchanged file should be answered from the cache")
        make_wad(p, ["MAP01", "MAP02"])  # different size, so the cache entry no longer matches
        self.assertEqual([m for m, _ in wadinfo.list_maps(p)], ["MAP01", "MAP02"])
        self.assertEqual(len(reads), 1)

    def test_map_list_missing_file(self):
        self.assertEqual(wadinfo.list_maps(self.root / "nope.wad"), [])

    def test_pk3_embedded_wad_and_umapinfo(self):
        inner = self.root / "inner.wad"
        make_wad(inner, ["E1M1", "E1M2"])
        pk3 = self.root / "x.pk3"
        with zipfile.ZipFile(pk3, "w") as z:
            z.write(inner, "maps/inner.wad")
            z.writestr("umapinfo.txt", 'map E1M1 { levelname = "Hangar" }')
        self.assertEqual(wadinfo.list_maps(pk3), [("E1M1", "Hangar"), ("E1M2", "")])

    def test_garbage_is_empty(self):
        self.assertEqual(wadinfo.list_maps(self.brutal), [])
        bad = self.root / "bad.wad"
        bad.write_bytes(b"PWAD\xff\xff\xff\x7f\x00\x00\x00\x00")
        self.assertEqual(wadinfo.list_maps(bad), [])

    def test_guess_iwad(self):
        iwads = library.scan_iwads(self.config.settings.iwads_dir)
        self.assertEqual(wadinfo.guess_iwad([self.evit], iwads), "DOOM2.WAD")
        self.assertEqual(wadinfo.guess_iwad([self.brutal], iwads), "")

    def test_readme(self):
        r = wadinfo.find_readme(self.root / "wads" / "gameplay-mods" / "brutal doom")
        self.assertEqual(r.name, "readme.txt")
        self.assertEqual(wadinfo.read_text_file(r), "hello")

    def test_preset_picture_default_and_roundtrip(self):
        self.assertEqual(Preset(name="x").picture, "")
        config = AppConfig(presets=[Preset(name="x", picture="/p/x.png")])
        again = AppConfig.from_dict(config.to_dict())
        self.assertEqual(again.presets[0].picture, "/p/x.png")

    def test_archive_readme(self):
        pk3 = self.root / "doc.pk3"
        with zipfile.ZipFile(pk3, "w") as z:
            z.writestr("mapinfo.txt", "code")
            z.writestr("decorate.txt", "code")
            z.writestr("docs/README.txt", "deep readme")
            z.writestr("a/b/readme.txt", "too deep")
            z.writestr("Readme.md", "root readme café")
        self.assertEqual(wadinfo.find_archive_readme(pk3), "Readme.md")
        self.assertEqual(
            wadinfo.read_archive_text(pk3, "Readme.md"), "root readme café"
        )
        self.assertIn("could not read", wadinfo.read_archive_text(pk3, "nope.txt"))

    def test_archive_without_readme(self):
        pk3 = self.root / "code.pk3"
        with zipfile.ZipFile(pk3, "w") as z:
            z.writestr("mapinfo.txt", "code")
            z.writestr("sounds/readme.txt", "ignored name? no: one folder deep")
        # one folder deep still counts
        self.assertEqual(wadinfo.find_archive_readme(pk3), "sounds/readme.txt")
        plain = self.root / "plain.pk3"
        with zipfile.ZipFile(plain, "w") as z:
            z.writestr("mapinfo.txt", "code")
        self.assertIsNone(wadinfo.find_archive_readme(plain))
        self.assertIsNone(wadinfo.find_archive_readme(self.root / "nope.pk3"))
        self.assertIsNone(wadinfo.find_archive_readme(self.evit))  # not an archive

    def test_file_summary(self):
        text = wadinfo.file_summary([self.evit, str(self.root / "gone.wad")])
        self.assertIn("Evit.wad", text)
        self.assertIn("map", text)
        self.assertIn("(missing)", text)
        self.assertIn("No files", wadinfo.file_summary([]))
        self.assertEqual(wadinfo.human_size(512), "512 B")
        self.assertEqual(wadinfo.human_size(1536), "1.5 KB")


class LaunchTests(Base):
    def preset(self, **kw):
        base = dict(name="Test Run", engine="UZDoom", iwad="DOOM2.WAD",
                    files=[self.evit, self.brutal, self.deh])
        base.update(kw)
        p = Preset(**base)
        self.config.presets.append(p)
        return p

    def test_basic_command(self):
        p = self.preset(warp="MAP02", skill=4)
        cmd = launch.build_command(self.config, p)
        d = self.root / "data"
        self.assertEqual(cmd[0], str(self.engine_path))
        self.assertEqual(cmd[cmd.index("-iwad") + 1], str(self.root / "iwads" / "DOOM2.WAD"))
        self.assertEqual(cmd[cmd.index("-config") + 1], str(d / "Config" / "Test Run" / "uzdoom.ini"))
        self.assertEqual(cmd[cmd.index("-savedir") + 1], str(d / "Save" / "Test Run"))
        self.assertEqual(cmd[cmd.index("-shotdir") + 1], str(d / "Screenshot" / "Test Run"))
        i = cmd.index("-file")
        self.assertEqual(cmd[i + 1 : i + 3], [self.evit, self.brutal])
        self.assertEqual(cmd[cmd.index("-deh") + 1], self.deh)
        self.assertEqual(cmd[cmd.index("-skill") + 1], "4")
        self.assertEqual(cmd[cmd.index("+map") + 1], "MAP02")

    def test_shared_folders(self):
        s = self.config.settings
        s.separate_saves = False
        cmd = launch.build_command(self.config, self.preset())
        self.assertEqual(cmd[cmd.index("-savedir") + 1], str(Path(s.data_root) / "Save"))

    def test_disabled_autoload_wrapper_flags(self):
        s = self.config.settings
        s.autoload_files = [self.deh]
        s.global_wrapper = "gamemoderun"
        p = self.preset(disabled_files=[self.brutal], no_monsters=True, fast=True,
                        width=1920, height=1080, extra_args='+set "a b"')
        cmd = launch.build_command(self.config, p)
        self.assertEqual(cmd[:2], ["gamemoderun", str(self.engine_path)])
        self.assertNotIn(self.brutal, cmd)
        self.assertEqual(cmd.count(self.deh), 1)  # autoload + preset duplicate collapsed
        for flag in ("-nomonsters", "-fast"):
            self.assertIn(flag, cmd)
        self.assertEqual(cmd[cmd.index("-width") + 1 : cmd.index("-width") + 4], ["1920", "-height", "1080"])
        self.assertEqual(cmd[-2:], ["+set", "a b"])
        p.wrapper = "mangohud"
        self.assertEqual(launch.build_command(self.config, p)[0], "mangohud")

    def test_empty_flags_and_save_demo(self):
        eng = self.config.engines[0]
        eng.save_flag = ""
        p = self.preset()
        cmd = launch.build_command(self.config, p, load_save="/x/a.zds")
        self.assertNotIn("-savedir", cmd)
        self.assertEqual(cmd[cmd.index("-loadgame") + 1], "/x/a.zds")
        cmd = launch.build_command(self.config, p, play_demo="/x/d.lmp")
        self.assertIn("-playdemo", cmd)
        self.assertNotIn("+map", cmd)

    def test_record_demo_path(self):
        p = self.preset(record_demo=True)
        cmd = launch.build_command(self.config, p, now=0)
        path = Path(cmd[cmd.index("-record") + 1])
        self.assertEqual(path.parent, self.root / "data" / "Demo" / "Test Run")
        self.assertEqual(path.suffix, ".lmp")

    def test_missing_files_skipped_and_reported(self):
        p = self.preset(files=[self.evit, "/nope/missing.wad"])
        cmd = launch.build_command(self.config, p)
        self.assertNotIn("/nope/missing.wad", cmd)
        texts = [x.text for x in launch.check(self.config, p)]
        self.assertTrue(any("missing.wad" in t for t in texts))

    def test_check_errors(self):
        p = self.preset(engine="Nope", iwad="")
        levels = [x.level for x in launch.check(self.config, p)]
        self.assertEqual(levels.count("error"), 2)
        self.assertEqual(launch.check(self.config, self.preset(name="ok")), [])
        self.engine_path.chmod(0o644)
        self.assertTrue(any("executable" in x.text for x in launch.check(self.config, self.config.presets[-1])))

    def test_prepare_dirs_and_seed(self):
        seed = self.root / "seed.ini"
        seed.write_text("[Doom]\nkeys=1\n")
        self.config.settings.seed_config = str(seed)
        p = self.preset()
        launch.prepare_dirs(self.config, p)
        target = launch.config_file(self.config.settings, p, self.config.engines[0])
        self.assertEqual(target.read_text(), "[Doom]\nkeys=1\n")
        target.write_text("changed")
        launch.prepare_dirs(self.config, p)  # must not overwrite
        self.assertEqual(target.read_text(), "changed")
        for d in launch.data_dirs(self.config.settings, p).all():
            self.assertTrue(d.is_dir())

    def test_default_config_name_and_choice(self):
        s, eng = self.config.settings, self.config.engines[0]
        p = self.preset()
        own = self.root / "data" / "Config" / "Test Run"
        self.assertEqual(launch.config_file(s, p, eng), own / "uzdoom.ini")
        made = launch.create_config(s, p, "hexen keys")  # ".ini" is added
        self.assertEqual(made, own / "hexen keys.ini")
        self.assertEqual(made.read_text(), "")
        p.config = "hexen keys.ini"
        self.assertEqual(launch.config_file(s, p, eng), made)
        cmd = launch.build_command(self.config, p)
        self.assertEqual(cmd[cmd.index("-config") + 1], str(made))

    def test_create_config_copies_source_and_refuses_duplicates(self):
        s = self.config.settings
        p = self.preset()
        source = self.root / "base.ini"
        source.write_text("[Doom]\nkeys=1\n")
        made = launch.create_config(s, p, "copy.ini", source)
        self.assertEqual(made.read_text(), "[Doom]\nkeys=1\n")
        with self.assertRaises(FileExistsError):
            launch.create_config(s, p, "copy")
        with self.assertRaises(ValueError):
            launch.create_config(s, p, "   ")

    def test_shared_config_folder_is_searched(self):
        s, eng = self.config.settings, self.config.engines[0]
        shared = self.root / "data" / "Config"
        shared.mkdir(parents=True)
        (shared / "common.ini").write_text("x")
        p = self.preset(config="common.ini")
        self.assertEqual(launch.config_file(s, p, eng), shared / "common.ini")
        own = launch.create_config(s, p, "mine")
        names = [c.name for c in launch.list_configs(s, p)]
        self.assertEqual(names, ["common.ini", "mine.ini"])
        # The same name in the preset's own folder wins and is listed once.
        (own.parent / "common.ini").write_text("own")
        self.assertEqual(launch.config_file(s, p, eng), own.parent / "common.ini")
        self.assertEqual([c.name for c in launch.list_configs(s, p)].count("common.ini"), 1)

    def test_missing_chosen_config_warns(self):
        p = self.preset(config="gone.ini")
        texts = [x.text for x in launch.check(self.config, p)]
        self.assertTrue(any("gone.ini" in t for t in texts))
        self.assertTrue(all(x.level == "warning" for x in launch.check(self.config, p)))

    def test_detached_launch(self):
        self.engine_path.write_text("#!/bin/sh\necho hi\n")
        proc, _cmd, log = launch.launch(self.config, self.preset(), self.root / "logs", detach=True)
        proc.wait(5)
        self.assertIn("hi", log.read_text())

    def test_multiplayer_host(self):
        p = self.preset(net_mode="host", net_players=4, net_game="deathmatch", net_port=5030)
        cmd = launch.build_command(self.config, p)
        self.assertEqual(cmd[cmd.index("-host") + 1], "4")
        self.assertIn("-deathmatch", cmd)
        self.assertEqual(cmd[cmd.index("-port") + 1], "5030")
        self.assertNotIn("-join", cmd)
        p.net_game, p.net_port = "", 0  # co-op on the default port adds nothing extra
        cmd = launch.build_command(self.config, p)
        for flag in ("-deathmatch", "-altdeath", "-port"):
            self.assertNotIn(flag, cmd)
        p.net_players = 1  # a host needs at least two players
        self.assertEqual(launch.net_args(p), ["-host", "2"])
        p.net_game = "altdeath"
        self.assertIn("-altdeath", launch.net_args(p))

    def test_multiplayer_join(self):
        p = self.preset(net_mode="join", net_address=" 192.168.1.20 ", net_players=6,
                        net_game="deathmatch")
        cmd = launch.build_command(self.config, p)
        self.assertEqual(cmd[cmd.index("-join") + 1], "192.168.1.20")
        # The host decides players and rules, so a joiner passes neither.
        for flag in ("-host", "-deathmatch"):
            self.assertNotIn(flag, cmd)
        self.assertEqual(launch.check(self.config, p), [])

    def test_join_needs_address_and_demos_are_single_player(self):
        p = self.preset(net_mode="join")
        self.assertEqual(launch.net_args(p), [])
        errors = [x.text for x in launch.check(self.config, p) if x.level == "error"]
        self.assertTrue(any("address" in t for t in errors))
        host = self.preset(name="h", net_mode="host")
        cmd = launch.build_command(self.config, host, play_demo="/x/d.lmp")
        self.assertNotIn("-host", cmd)
        self.assertEqual(launch.net_args(self.preset(name="solo")), [])

    def test_new_defaults(self):
        s = Settings()
        self.assertTrue(s.data_root.endswith("/Doomdeck/data"))
        self.assertEqual((s.mapsets_category, s.close_on_launch), ("maps", False))

    def test_stats_and_format(self):
        p = self.preset()
        launch.record_session(p, 100.0, 160.5)
        self.assertEqual((p.play_count, p.play_seconds, p.last_played), (1, 60, 100.0))
        self.assertIn("'a b'", launch.format_command(["x", "a b"]))

    def test_launch_runs_and_logs(self):
        self.engine_path.write_text("#!/bin/sh\necho started \"$@\"\n")
        p = self.preset()
        proc, cmd, log = launch.launch(self.config, p, self.root / "logs")
        proc.wait(5)
        self.assertIn("started", log.read_text())


class ConfigTests(Base):
    def test_roundtrip_and_unknown_keys(self):
        self.config.presets.append(Preset(name="a", favorite=True, files=["x"]))
        self.config.library_meta["maps/x"] = {"rating": 4}
        path = self.root / "c.json"
        cfgmod.save(self.config, path)
        loaded = cfgmod.load(path)
        self.assertEqual(loaded, self.config)
        text = path.read_text().replace('"favorite"', '"future_field": 1, "favorite"', 1)
        path.write_text(text)
        self.assertTrue(cfgmod.load(path).presets[0].favorite)

    def test_corrupt_moved_aside(self):
        path = self.root / "c.json"
        path.write_text("{ not json")
        loaded = cfgmod.load(path)
        self.assertEqual(loaded.engines[0].name, "UZDoom")
        self.assertTrue(path.with_suffix(".json.bad").exists())

    def test_first_run_default(self):
        self.assertEqual(cfgmod.load(self.root / "none.json").engines[0].name, "UZDoom")


class UserDataTests(Base):
    def test_lists_newest_first(self):
        d = self.root / "s"
        d.mkdir()
        a, b = d / "a.zds", d / "b.zds"
        a.write_text("1"); time.sleep(0.02); b.write_text("22")
        (d / "ignore.txt").write_text("x")
        self.assertEqual([f.path.name for f in userdata.list_saves(d)], ["b.zds", "a.zds"])
        self.assertEqual(userdata.list_saves(self.root / "none"), [])

    def test_latest_save(self):
        d = self.root / "s2"
        d.mkdir()
        self.assertIsNone(userdata.latest_save(d))
        (d / "old.zds").write_text("1"); time.sleep(0.02); (d / "new.zds").write_text("2")
        self.assertEqual(userdata.latest_save(d).path.name, "new.zds")


class NewFeatureTests(Base):
    def test_find_moved(self):
        gone = "/old/place/maps/Eviternity/EVIT.WAD"
        other = str(self.root / "elsewhere" / "Evit.wad")
        found = library.find_moved(
            [gone, self.evit, "/nowhere/ghost.wad"], self.config.settings.wads_root
        )
        self.assertEqual(found, {gone: self.evit})  # present and unknown files are left out
        self.assertEqual(library.find_moved([self.evit], self.config.settings.wads_root), {})
        self.assertNotIn(other, found)

    def test_find_moved_prefers_same_parent_folder(self):
        wads = Path(self.config.settings.wads_root)
        (wads / "maps" / "Other").mkdir()
        (wads / "maps" / "Other" / "Evit.wad").write_bytes(b"x")
        found = library.find_moved(["/gone/Other/Evit.wad"], str(wads))
        self.assertEqual(found["/gone/Other/Evit.wad"], str(wads / "maps" / "Other" / "Evit.wad"))

    def test_backup_once_per_day_and_pruned(self):
        path = self.root / "doomdeck.json"
        self.assertIsNone(cfgmod.backup(path))  # nothing to back up yet
        path.write_text("{}")
        first = cfgmod.backup(path, keep=2, today="20260101")
        self.assertTrue(first.is_file())
        self.assertIsNone(cfgmod.backup(path, keep=2, today="20260101"))  # same day
        cfgmod.backup(path, keep=2, today="20260102")
        cfgmod.backup(path, keep=2, today="20260103")
        names = sorted(p.name for p in (self.root / "backups").iterdir())
        self.assertEqual(names, ["doomdeck-20260102.json", "doomdeck-20260103.json"])

    def test_save_makes_backup(self):
        path = self.root / "cfg" / "doomdeck.json"
        cfgmod.save(self.config, path)
        self.assertFalse((path.parent / "backups").exists())  # first save: nothing earlier
        cfgmod.save(self.config, path)
        self.assertEqual(len(list((path.parent / "backups").iterdir())), 1)

    def test_duplicate_file_name_warning(self):
        other = self.root / "wads" / "stray" / "Evit.wad"
        other.write_bytes(b"x")
        p = Preset(name="d", engine="UZDoom", iwad="DOOM2.WAD", files=[self.evit, str(other)])
        texts = [x.text for x in launch.check(self.config, p)]
        self.assertTrue(any("both called Evit.wad" in t for t in texts))

    def test_desktop_entry(self):
        p = Preset(name='My "odd" $pre%set')
        text = launch.desktop_entry_text(p, Path("/opt/dd/run.sh"), "/i/c.png", Path("/opt/dd"))
        self.assertIn('Exec="/opt/dd/run.sh" --launch "My \\\\"odd\\\\" \\\\$pre%%set"', text)
        self.assertIn("Icon=/i/c.png", text)
        self.assertIn("Path=/opt/dd", text)
        target = launch.write_desktop_entry(
            Preset(name="Doom Infinite"), Path("/opt/dd/run.sh"), "/i/c.png", Path("/opt/dd"),
            directory=self.root / "apps",
        )
        self.assertEqual(target.name, "doomdeck-doom-infinite.desktop")
        self.assertIn("Name=Doom Infinite", target.read_text())

    def test_load_order_sorting(self):
        root = self.config.settings.wads_root
        audio = str(Path(root) / "audio" / "Music" / "m.wad")
        visual = str(Path(root) / "visuals" / "HD" / "v.pk3")
        mod = self.brutal
        mapfile = self.evit
        outside = "/somewhere/else/x.wad"
        shuffled = [mapfile, mod, visual, audio, outside]
        self.assertEqual(
            library.sort_load_order(shuffled, root),
            [audio, visual, mod, outside, mapfile],  # unknown counts as a mod, order kept
        )
        # Ties keep the existing order.
        self.assertEqual(library.sort_load_order([self.deh, self.brutal], root), [self.deh, self.brutal])
        self.assertEqual(library.sort_load_order([], root), [])

    def test_load_rank_extra_folder_and_keywords(self):
        extra = self.root / "mymaps"
        extra.mkdir()
        self.assertEqual(library.load_rank(extra / "a.wad", self.config.settings.wads_root, [str(extra)]), 4)
        self.assertEqual(library.load_rank(Path(self.config.settings.wads_root) / "Doom Music Packs" / "p" / "a.wad", self.config.settings.wads_root), 0)

    def test_parse_tags(self):
        self.assertEqual(parse_tags(" co-op, Brutal;co-op ,, SLAUGHTER  map "), ["co-op", "Brutal", "SLAUGHTER map"])
        self.assertEqual(parse_tags(""), [])
        p = Preset(name="x", tags=["Co-op"])
        self.assertTrue(p.has_tag("co-OP"))
        self.assertTrue(p.matches("co-op"))
        self.assertFalse(p.has_tag("nope"))

    def test_sort_presets(self):
        a = Preset(name="Alpha", last_played=5, play_seconds=10, play_count=1)
        b = Preset(name="beta", last_played=9, play_seconds=1, play_count=7, favorite=True)
        c = Preset(name="Charlie", last_played=1, play_seconds=99, play_count=3)
        names = lambda mode, rev=False: [p.name for p in sort_presets([c, a, b], mode, rev)]
        self.assertEqual(names("Name"), ["Alpha", "beta", "Charlie"])
        self.assertEqual(names("Name", True), ["Charlie", "beta", "Alpha"])
        self.assertEqual(names("Last played"), ["beta", "Alpha", "Charlie"])
        self.assertEqual(names("Most played"), ["Charlie", "Alpha", "beta"])
        self.assertEqual(names("Times launched"), ["beta", "Charlie", "Alpha"])
        self.assertEqual(names("Favorites first"), ["beta", "Alpha", "Charlie"])

    def test_parse_command(self):
        cmd = (
            "gamemoderun /games/UZDoom/uzdoom -iwad /games/iwads/DOOM2.WAD \\\n"
            "  -file 'My Map.wad' mod.pk3 -deh fix.deh -skill 4 -warp 5 -nomonsters -fast "
            "-width 1920 -height 1080 -config /x.ini -savedir /s +set sv_cheats 1 -dmflags 4"
        )
        parsed = importer.parse_command(cmd)
        self.assertEqual(parsed.wrapper, "gamemoderun")
        self.assertEqual(parsed.engine, "/games/UZDoom/uzdoom")
        self.assertEqual(parsed.iwad, "/games/iwads/DOOM2.WAD")
        self.assertEqual(parsed.files, ["My Map.wad", "mod.pk3", "fix.deh"])
        self.assertEqual((parsed.skill, parsed.warp, parsed.width, parsed.height), (4, "MAP05", 1920, 1080))
        self.assertEqual(parsed.flags, {"no_monsters": True, "fast": True})
        self.assertEqual(parsed.extra, ["+set", "sv_cheats", "1", "-dmflags", "4"])

    def test_parse_command_warp_and_map(self):
        self.assertEqual(importer.parse_command("doom -warp 2 3").warp, "E2M3")
        self.assertEqual(importer.parse_command("doom +map map07").warp, "MAP07")
        self.assertEqual(importer.parse_command("").files, [])
        self.assertEqual(importer.parse_command("doom -file 'unbalanced").files, ["'unbalanced"])

    def test_build_preset_from_command(self):
        parsed = importer.parse_command("uzdoom -iwad /somewhere/doom2.wad -file a.wad -skill 3 -dmflags 4")
        preset = importer.build_preset(parsed, "Imp", self.config, ["DOOM2.WAD"])
        self.assertEqual((preset.engine, preset.iwad, preset.skill), ("UZDoom", "DOOM2.WAD", 3))
        self.assertEqual(preset.files, ["a.wad"])
        self.assertEqual(preset.extra_args, "-dmflags 4")

    def test_expand_dropped(self):
        wads = Path(self.config.settings.wads_root)
        (wads / "stray" / "notes.txt").write_text("x")
        found = importer.expand_dropped([str(wads / "gameplay-mods" / "brutal doom"), self.evit, self.evit,
                                         str(wads / "stray" / "notes.txt"), "/nope"])
        self.assertEqual(found, [self.brutal, self.deh, self.evit])

    def test_project_info(self):
        brutal_dir = Path(self.brutal).parent
        self.assertEqual(wadinfo.project_info(brutal_dir, [self.brutal]), "hello")  # folder readme
        evit_dir = Path(self.evit).parent
        text = wadinfo.project_info(evit_dir, [self.evit])  # no readme: file summary
        self.assertIn("Evit.wad", text)
        self.assertIn("3 maps", text)
        pk3 = self.root / "wads" / "stray" / "r.pk3"
        with zipfile.ZipFile(pk3, "w") as z:
            z.writestr("README.TXT", "inside")
        text = wadinfo.project_info(pk3.parent, [str(pk3)])
        self.assertTrue(text.endswith("inside"))
        self.assertIn("r.pk3: README.TXT", text)
        many = [self.evit] * 30
        self.assertIn("and 5 more", wadinfo.project_info(evit_dir, many))
        (brutal_dir / "readme.txt").write_text("x" * 7000)
        self.assertTrue(wadinfo.project_info(brutal_dir, [self.brutal]).endswith("[... cut]"))

    def test_audit_and_levels(self):
        ok = Preset(name="ok", engine="UZDoom", iwad="DOOM2.WAD", files=[self.evit])
        bad = Preset(name="bad", engine="UZDoom", iwad="NOPE.WAD", files=["/gone.wad"])
        self.config.presets += [ok, bad]
        found = launch.audit(self.config)
        self.assertEqual([p.name for p, _ in found], ["bad"])
        self.assertEqual(found[0][1][0].level, "error")  # errors listed first
        self.assertEqual(launch.worst_level(found[0][1]), "error")
        self.assertEqual(launch.worst_level(launch.check(self.config, ok)), "")
        self.assertEqual(launch.worst_level([launch.Problem("warning", "x")]), "warning")

    def test_import_config_sources(self):
        xdg = self.root / "xdg"
        (xdg / "uzdoom").mkdir(parents=True)
        (xdg / "uzdoom" / "uzdoom.ini").write_text("[Doom.ConsoleVariables]\n")
        old = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = str(xdg)
        try:
            found = launch.engine_config_files(self.config.engines[0])
        finally:
            if old is None:
                os.environ.pop("XDG_CONFIG_HOME")
            else:
                os.environ["XDG_CONFIG_HOME"] = old
        self.assertEqual(found, [xdg / "uzdoom" / "uzdoom.ini"])  # listed once

        a = Preset(name="A", engine="UZDoom")
        b = Preset(name="B", engine="UZDoom")
        self.config.presets += [a, b]
        made = launch.create_config(self.config.settings, a, "Binds")
        pairs = launch.other_preset_configs(self.config.settings, self.config.presets, exclude=b)
        self.assertEqual(pairs, [(a, made)])
        self.assertEqual(launch.other_preset_configs(self.config.settings, self.config.presets, exclude=a), [])

        # Importing copies the content and a taken name gets a number.
        made.write_text("keys")
        copy = launch.create_config(self.config.settings, b, launch.unique_config_name(self.config.settings, b, "Binds.ini"), made)
        self.assertEqual(copy.read_text(), "keys")
        self.assertEqual(launch.unique_config_name(self.config.settings, b, "Binds.ini"), "Binds 2.ini")
        self.assertEqual(launch.unique_config_name(self.config.settings, b, "Fresh"), "Fresh.ini")

    def test_preset_matches(self):
        p = Preset(name="Doom Infinite", notes="co-op night", files=["/w/gameplay-mods/Nash Gore/nashgore.pk3"])
        for query in ("", "infinite", "CO-OP", "nashgore", " nash "):
            self.assertTrue(p.matches(query), query)
        self.assertFalse(p.matches("eviternity"))

    def test_launch_without_window(self):
        from doomdeck import __main__ as entry
        self.engine_path.write_text("#!/bin/sh\nexit 0\n")
        self.config.presets.append(Preset(name="Go", engine="UZDoom", iwad="DOOM2.WAD", files=[self.evit]))
        cfg = self.root / "cfg.json"
        cfgmod.save(self.config, cfg)
        old_env, old_dir = os.environ.get("DOOMDECK_CONFIG"), cfgmod.APP_DIR
        os.environ["DOOMDECK_CONFIG"] = str(cfg)
        cfgmod.APP_DIR = self.root
        try:
            self.assertEqual(entry.launch_preset("Go"), 0)
            self.assertEqual(entry.launch_preset("Nope"), 1)
        finally:
            cfgmod.APP_DIR = old_dir
            if old_env is None:
                os.environ.pop("DOOMDECK_CONFIG")
            else:
                os.environ["DOOMDECK_CONFIG"] = old_env
        self.assertEqual(cfgmod.load(cfg).preset_named("Go").play_count, 1)


if __name__ == "__main__":
    unittest.main()
