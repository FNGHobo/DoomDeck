"""Tests for the idgames client, update tracking, checksums, engine installer,
compatibility warnings, map progress and settings migration. No network is used: the
web calls are given canned answers, and downloads use file:// links."""

import io
import json
import os
import stat
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

from doomdeck import compat, engines, idgames, launch, netio, sharing, updates, wadinfo
from doomdeck.models import AppConfig, Engine, Preset

from tests.test_core import Base, make_wad


def api_answer(files):
    return {"meta": {"version": 3}, "content": {"file": files}}


SAMPLE = {
    "id": 15156, "title": "Sunlust", "dir": "levels/doom2/megawads/", "filename": "sunlust.zip",
    "size": 20_000_000, "age": 1_300_000_000, "date": "2011-03-13", "author": "Ribbiks & Dannebubinga",
    "description": "A 32 map megawad", "rating": 4.8, "votes": 100, "url": "https://x/15156",
}


class IdgamesTests(unittest.TestCase):
    def test_parse_list_single_and_none(self):
        self.assertEqual(idgames.parse_response(api_answer([SAMPLE, SAMPLE]))[0].title, "Sunlust")
        one = idgames.parse_response({"content": {"file": SAMPLE}})  # one result is not a list
        self.assertEqual(len(one), 1)
        get_style = idgames.parse_response({"content": SAMPLE})
        self.assertEqual(get_style[0].filename, "sunlust.zip")
        self.assertEqual(idgames.parse_response({"warning": {"type": "No Results"}}), [])

    def test_parse_error_raises(self):
        with self.assertRaises(idgames.IdgamesError) as ctx:
            idgames.parse_response({"error": {"type": "Query Too Small", "message": "short"}})
        self.assertIn("Query Too Small", str(ctx.exception))
        with self.assertRaises(idgames.IdgamesError):
            idgames.parse_response("nonsense")

    def test_numbers_may_arrive_as_strings(self):
        f = idgames.parse_response({"content": {"file": {**SAMPLE, "size": "123", "rating": "3.5", "id": "7"}}})[0]
        self.assertEqual((f.size, f.rating, f.id), (123, 3.5, 7))

    def test_search_builds_the_request(self):
        seen = []

        def fetch(url):
            seen.append(url)
            return api_answer([SAMPLE])

        idgames.search("sun lust", "title", "rating", "desc", fetch=fetch)
        self.assertIn("action=search", seen[0])
        self.assertIn("query=sun+lust", seen[0])
        self.assertIn("out=json", seen[0])
        with self.assertRaises(idgames.IdgamesError):
            idgames.search("ab", fetch=fetch)

    def test_network_errors_become_idgames_errors(self):
        def broken(url):
            raise netio.NetError("offline")

        with self.assertRaises(idgames.IdgamesError):
            idgames.latest(fetch=broken)

    def test_download_urls_and_names(self):
        f = idgames.parse_response(api_answer([SAMPLE]))[0]
        urls = f.download_urls()
        self.assertTrue(urls[0].endswith("/levels/doom2/megawads/sunlust.zip"))
        self.assertEqual(len(urls), len(idgames.MIRRORS))
        self.assertEqual(f.project_name, "Sunlust")
        self.assertEqual(idgames.IdgamesFile(filename="my map.zip").project_name, "my map")

    def test_categories(self):
        self.assertEqual(idgames.category_for("levels/doom2/megawads/"), "maps")
        self.assertEqual(idgames.category_for("levels/doom2/megawads/", "mapsets"), "mapsets")
        self.assertEqual(idgames.category_for("music/"), "audio")
        self.assertEqual(idgames.category_for("sounds/doom2/"), "audio")
        self.assertEqual(idgames.category_for("graphics/"), "visuals")
        self.assertEqual(idgames.category_for("utils/"), "utilities")
        self.assertEqual(idgames.category_for("combos/"), "total-conversions")
        self.assertEqual(idgames.category_for("docs/"), "needs-to-be-sorted")

    def test_parse_page_link(self):
        self.assertEqual(idgames.parse_page_link("15156"), ("15156", ""))
        self.assertEqual(idgames.parse_page_link("idgames:42"), ("42", ""))
        self.assertEqual(idgames.parse_page_link("https://x/y?id=99"), ("99", ""))
        self.assertEqual(
            idgames.parse_page_link("https://www.doomworld.com/idgames/levels/doom2/megawads/sunlust"),
            ("", "levels/doom2/megawads/sunlust"),
        )
        self.assertEqual(idgames.parse_page_link("hello"), ("", ""))

    def make_zip(self, path, names, top=""):
        with zipfile.ZipFile(path, "w") as z:
            for n in names:
                z.writestr(top + n, b"data " + n.encode())
        return path

    def test_extract_strips_a_single_top_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            z = self.make_zip(Path(tmp) / "a.zip", ["a.wad", "a.txt"], top="Pack/")
            files = idgames.extract_archive(z, Path(tmp) / "out")
            self.assertEqual(sorted(p.name for p in files), ["a.txt", "a.wad"])
            self.assertTrue((Path(tmp) / "out" / "a.wad").is_file())

    def test_extract_keeps_flat_archives_and_blocks_escapes(self):
        with tempfile.TemporaryDirectory() as tmp:
            z = Path(tmp) / "bad.zip"
            with zipfile.ZipFile(z, "w") as zf:
                zf.writestr("good.wad", b"x")
                zf.writestr("../evil.txt", b"x")
                zf.writestr("/abs.txt", b"x")
            files = idgames.extract_archive(z, Path(tmp) / "out")
            self.assertEqual([p.name for p in files], ["good.wad"])
            self.assertFalse((Path(tmp) / "evil.txt").exists())

    def test_bad_zip_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.zip"
            bad.write_bytes(b"not a zip")
            with self.assertRaises(idgames.IdgamesError):
                idgames.extract_archive(bad, Path(tmp) / "out")

    def test_install_goes_to_category_and_project_folder(self):
        f = idgames.parse_response(api_answer([SAMPLE]))[0]
        with tempfile.TemporaryDirectory() as tmp:
            z = self.make_zip(Path(tmp) / "s.zip", ["sunlust.wad", "sunlust.txt"])
            result = idgames.install(f, z, Path(tmp) / "wads", mapsets_category="maps")
            self.assertEqual(result.folder, Path(tmp) / "wads" / "maps" / "Sunlust")
            self.assertTrue((result.folder / "sunlust.wad").is_file())
            self.assertEqual(result.category, "maps")

    def test_download_tries_the_next_mirror(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = Path(tmp) / "mirror" / "levels" / "doom2" / "megawads"
            good.mkdir(parents=True)
            (good / "sunlust.zip").write_bytes(b"zipdata")
            mirrors = ("file:///does/not/exist/", Path(tmp).joinpath("mirror").as_uri() + "/")
            f = idgames.parse_response(api_answer([SAMPLE]))[0]
            seen = []
            out = idgames.download(f, Path(tmp) / "dl" / "s.zip", progress=lambda d, t: seen.append(d), mirrors=mirrors)
            self.assertEqual(out.read_bytes(), b"zipdata")
            self.assertTrue(seen)
            self.assertFalse((Path(tmp) / "dl" / "s.zip.part").exists())

    def test_download_all_mirrors_fail(self):
        f = idgames.parse_response(api_answer([SAMPLE]))[0]
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(idgames.IdgamesError):
                idgames.download(f, Path(tmp) / "x.zip", mirrors=("file:///nowhere/",))

    def test_download_can_be_cancelled(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "big.bin"
            src.write_bytes(b"x" * 600_000)
            with self.assertRaises(netio.NetError):
                netio.download(src.as_uri(), Path(tmp) / "out.bin", cancelled=lambda: True)
            self.assertFalse((Path(tmp) / "out.bin").exists())
            self.assertFalse((Path(tmp) / "out.bin.part").exists())


class UpdateTests(unittest.TestCase):
    def test_parse_source(self):
        self.assertEqual(updates.parse_source("UZDoom/UZDoom"), {"kind": "github", "repo": "UZDoom/UZDoom", "stamp": ""})
        self.assertEqual(updates.parse_source("https://github.com/a/b.git")["repo"], "a/b")
        self.assertEqual(updates.parse_source("https://github.com/a/b/releases/tag/v1")["repo"], "a/b")
        self.assertEqual(updates.parse_source("idgames:123")["id"], 123)
        self.assertEqual(updates.parse_source("levels/doom2/megawads/x.zip")["path"], "levels/doom2/megawads/x.zip")
        self.assertIsNone(updates.parse_source(""))
        self.assertIsNone(updates.parse_source("just some words"))

    def test_github_check(self):
        def fetch(url):
            return {"tag_name": "v2.0", "name": "Two", "html_url": "https://gh/rel"}

        meta = {"mods/Brutal": {"source": {"kind": "github", "repo": "a/b", "stamp": "v1.0"}},
                "mods/Same": {"source": {"kind": "github", "repo": "a/c", "stamp": "v2.0"}},
                "mods/NoSource": {"favorite": True}}
        results = updates.check_all(meta, fetch)
        self.assertEqual([r.key for r in results], ["mods/Brutal", "mods/Same"])
        self.assertTrue(results[0].is_update)
        self.assertFalse(results[1].is_update)
        text = updates.report_text(results)
        self.assertIn("mods/Brutal", text)
        self.assertNotIn("mods/Same:", text)
        self.assertIn("2 checked, 1 with updates", text)

    def test_idgames_check_uses_upload_time(self):
        def fetch(url):
            return {"content": {**SAMPLE, "age": 1_400_000_000}}

        meta = {"maps/Sunlust": {"source": {"kind": "idgames", "id": 15156, "stamp": 1_300_000_000}}}
        (info,) = updates.check_all(meta, fetch)
        self.assertTrue(info.is_update)

    def test_errors_are_reported_not_raised(self):
        def fetch(url):
            raise netio.NetError("offline")

        (info,) = updates.check_all({"a/b": {"source": {"kind": "github", "repo": "x/y"}}}, fetch)
        self.assertFalse(info.is_update)
        self.assertIn("offline", info.error)
        self.assertIn("could not check", updates.report_text([info]))

    def test_baseline_records_current_version(self):
        out = updates.baseline({"kind": "github", "repo": "a/b", "stamp": ""},
                               fetch=lambda url: {"tag_name": "v3", "name": "Three"})
        self.assertEqual(out["stamp"], "v3")

    def test_source_record_roundtrip(self):
        f = idgames.parse_response(api_answer([SAMPLE]))[0]
        rec = idgames.source_record(f)
        self.assertEqual((rec["kind"], rec["id"], rec["stamp"]), ("idgames", 15156, 1_300_000_000))


class SharingTests(Base):
    def test_export_has_checksums_and_no_personal_data(self):
        preset = Preset("P", engine="UZDoom", iwad="DOOM2.WAD", files=[self.evit, self.brutal],
                        disabled_files=[self.brutal], picture="/x.png", play_count=9,
                        maps_done=["MAP01"], map_plays={"MAP01": 2})
        data = sharing.export_data(preset, self.root / "iwads" / "DOOM2.WAD", engine_version="v4.14")
        self.assertEqual(data["doomdeck_preset"], 1)
        self.assertEqual(data["engine_version"], "v4.14")
        self.assertEqual([f["name"] for f in data["files"]], ["Evit.wad", "brutal.pk3"])
        self.assertEqual(len(data["files"][0]["sha256"]), 64)
        self.assertEqual([f["enabled"] for f in data["files"]], [True, False])
        for key in ("picture", "play_count", "maps_done", "map_plays"):
            self.assertNotIn(key, data["preset"])
        self.assertEqual(data["iwad"]["name"], "DOOM2.WAD")
        json.dumps(data)  # must be JSON-able

    def test_read_export_accepts_old_plain_files(self):
        preset, extras = sharing.read_export({"name": "Old", "files": ["/a.wad"]})
        self.assertEqual(preset["name"], "Old")
        self.assertEqual(extras["files"], [])
        preset, extras = sharing.read_export({"doomdeck_preset": 1, "preset": {"name": "N"}, "files": [{"name": "a"}]})
        self.assertEqual((preset["name"], extras["files"]), ("N", [{"name": "a"}]))

    def test_verify_ok_different_missing(self):
        entries = [sharing.describe(self.evit), sharing.describe(self.brutal), {"name": "gone.wad", "size": 5, "sha256": "ab"}]
        index = sharing.index_files([self.config.settings.wads_root])
        checks = sharing.verify(entries, index)
        self.assertEqual([c.status for c in checks], ["ok", "ok", "missing"])
        # Change one file: same name, different content.
        Path(self.brutal).write_bytes(b"changed content")
        sharing._DIGEST_CACHE.clear()
        checks = sharing.verify(entries, sharing.index_files([self.config.settings.wads_root]))
        self.assertEqual([c.status for c in checks], ["ok", "different", "missing"])
        text = sharing.report(checks)
        self.assertIn("1 of 3", text)
        self.assertIn("brutal.pk3: your copy is different", text)
        self.assertIn("gone.wad: not in your library", text)

    def test_duplicate_names_pick_the_matching_copy(self):
        other = self.root / "wads" / "other"
        other.mkdir()
        (other / "brutal.pk3").write_bytes(b"a different brutal.pk3")
        entries = [sharing.describe(self.brutal)]
        checks = sharing.verify(entries, sharing.index_files([self.config.settings.wads_root]))
        self.assertEqual((checks[0].status, checks[0].path), ("ok", self.brutal))

    def test_relink_points_at_local_copies_and_keeps_missing(self):
        preset = Preset("P", files=[self.evit, "/their/pc/gone.wad", self.brutal], disabled_files=["/their/pc/gone.wad"])
        data = sharing.export_data(preset, None)
        data["files"][1].update(size=5, sha256="00")
        # Pretend the preset came from a computer with other paths.
        data["preset"]["files"] = ["/their/Evit.wad", "/their/pc/gone.wad", "/their/brutal.pk3"]
        data["preset"]["disabled_files"] = ["/their/pc/gone.wad"]
        index = sharing.index_files([self.config.settings.wads_root])
        result = sharing.relink(data["preset"], data, index)
        self.assertEqual(result.files, [self.evit, "/their/pc/gone.wad", self.brutal])
        self.assertEqual(result.disabled, ["/their/pc/gone.wad"])
        self.assertEqual([c.status for c in result.problems], ["missing"])

    def test_old_export_relinks_by_name(self):
        data = {"name": "Old", "files": ["/their/Evit.wad"]}
        preset, extras = sharing.read_export(data)
        index = sharing.index_files([self.config.settings.wads_root])
        result = sharing.relink(preset, extras, index)
        self.assertEqual(result.files, [self.evit])

    def test_iwad_check(self):
        expected = sharing.describe(self.root / "iwads" / "DOOM2.WAD")
        check = sharing.verify_iwad(expected, self.root / "iwads")
        self.assertEqual(check.status, "ok")
        self.assertIsNone(sharing.verify_iwad({}, self.root / "iwads"))
        self.assertEqual(sharing.verify_iwad({"name": "DOOM.WAD"}, self.root / "iwads").status, "missing")


class EngineInstallTests(unittest.TestCase):
    RELEASES = [
        {"tag_name": "v5.0-rc1", "prerelease": True, "assets": []},
        {"tag_name": "v4.14.3", "name": "4.14.3", "html_url": "https://gh/r", "published_at": "2026-01-02T00:00:00Z",
         "assets": [
             {"name": "uzdoom-4.14.3-windows-x64.zip", "browser_download_url": "https://x/w.zip", "size": 1},
             {"name": "uzdoom-4.14.3-linux-x86_64.tar.gz", "browser_download_url": "https://x/l.tgz", "size": 2},
             {"name": "UZDoom-4.14.3-x86_64.AppImage", "browser_download_url": "https://x/a.AppImage", "size": 3},
             {"name": "uzdoom-4.14.3-linux-aarch64.tar.gz", "browser_download_url": "https://x/arm.tgz", "size": 4},
             {"name": "uzdoom-4.14.3.tar.gz.sha256", "browser_download_url": "https://x/s", "size": 5},
             {"name": "Source code (zip)", "browser_download_url": "https://x/src", "size": 6},
         ]},
    ]

    def test_latest_skips_prereleases(self):
        rel = engines.latest_release("UZDoom/UZDoom", fetch=lambda url: self.RELEASES)
        self.assertEqual(rel.tag, "v4.14.3")
        self.assertEqual(rel.published, "2026-01-02")
        pre = engines.latest_release("UZDoom/UZDoom", include_prereleases=True, fetch=lambda url: self.RELEASES)
        self.assertEqual(pre.tag, "v5.0-rc1")

    def test_bad_repo_names_are_rejected(self):
        with self.assertRaises(netio.NetError):
            engines.latest_release("not a repo")
        with self.assertRaises(netio.NetError):
            engines.latest_release("a/b", fetch=lambda url: {"message": "Not Found"})

    def test_linux_assets_ranked(self):
        rel = engines.parse_release(self.RELEASES[1])
        names = [a.name for a in engines.linux_assets(rel)]
        self.assertEqual(names[0], "UZDoom-4.14.3-x86_64.AppImage")
        self.assertIn("uzdoom-4.14.3-linux-x86_64.tar.gz", names)
        self.assertNotIn("uzdoom-4.14.3-windows-x64.zip", names)
        self.assertNotIn("uzdoom-4.14.3.tar.gz.sha256", names)
        self.assertLess(names.index("uzdoom-4.14.3-linux-x86_64.tar.gz"), names.index("uzdoom-4.14.3-linux-aarch64.tar.gz"))

    def test_version_compare(self):
        self.assertTrue(engines.is_newer("v4.14.2", "v4.14.3"))
        self.assertTrue(engines.is_newer("4.9", "4.10"))
        self.assertFalse(engines.is_newer("v4.14.3", "4.14.3"))
        self.assertFalse(engines.is_newer("v5.0", "v4.14.3"))
        self.assertTrue(engines.is_newer("", "v1"))
        self.assertFalse(engines.is_newer("v1", ""))

    def make_tar(self, path, members):
        with tarfile.open(path, "w:gz") as tar:
            for name, data, mode in members:
                info = tarfile.TarInfo(name)
                info.size = len(data)
                info.mode = mode
                tar.addfile(info, io.BytesIO(data))

    def test_install_from_tar(self):
        with tempfile.TemporaryDirectory() as tmp:
            tgz = Path(tmp) / "src" / "uzdoom-linux.tar.gz"
            tgz.parent.mkdir()
            self.make_tar(tgz, [("uzdoom-4.14/uzdoom", b"\x7fELF" + b"0" * 200, 0o644), ("uzdoom-4.14/uzdoom.pk3", b"pk3", 0o644),
                                ("../escape.txt", b"no", 0o644)])
            asset = engines.Asset("uzdoom-linux.tar.gz", tgz.as_uri(), 1)
            program = engines.install_asset(asset, "v4.14.3", Path(tmp) / "engines")
            self.assertEqual(program.name, "uzdoom")
            self.assertTrue(os.access(program, os.X_OK))
            self.assertFalse((Path(tmp) / "escape.txt").exists())
            self.assertEqual(program.parent.parent.name, "v4.14.3")

    def test_install_appimage_is_made_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "UZDoom.AppImage"
            src.write_bytes(b"appimage")
            program = engines.install_asset(engines.Asset("UZDoom.AppImage", src.as_uri()), "v1", Path(tmp) / "engines")
            self.assertTrue(program.stat().st_mode & stat.S_IXUSR)

    def test_install_reports_missing_program(self):
        with tempfile.TemporaryDirectory() as tmp:
            zp = Path(tmp) / "r.zip"
            with zipfile.ZipFile(zp, "w") as z:
                z.writestr("readme.txt", "hi")
            with self.assertRaises(netio.NetError):
                engines.install_asset(engines.Asset("r.zip", zp.as_uri()), "v1", Path(tmp) / "engines")

    def test_remove_install_only_inside_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "engines"
            (root / "v1").mkdir(parents=True)
            outside = Path(tmp) / "keep"
            outside.mkdir()
            self.assertFalse(engines.remove_install(outside, root))
            self.assertTrue(outside.exists())
            self.assertTrue(engines.remove_install(root / "v1", root))
            self.assertFalse((root / "v1").exists())


class CompatTests(Base):
    def test_engine_family(self):
        self.assertEqual(compat.engine_family("UZDoom", "/x/uzdoom"), "zdoom")
        self.assertEqual(compat.engine_family("My port", "/x/gzdoom"), "zdoom")
        self.assertEqual(compat.engine_family("DSDA", "/x/dsda-doom"), "boom")
        self.assertEqual(compat.engine_family("Chocolate", "/x/chocolate-doom"), "vanilla")
        self.assertEqual(compat.engine_family("Thing", "/x/thing"), "unknown")

    def test_iwad_kind(self):
        self.assertEqual(compat.iwad_kind("DOOM2.WAD"), "doom2")
        self.assertEqual(compat.iwad_kind("freedoom2.wad"), "doom2")
        self.assertEqual(compat.iwad_kind("doom.wad"), "doom")
        self.assertEqual(compat.iwad_kind("DOOMU.WAD"), "doom")
        self.assertEqual(compat.iwad_kind("heretic.wad"), "other")
        self.assertEqual(compat.iwad_kind("mystery.wad"), "")

    def run_check(self, iwad="DOOM2.WAD", files=(), engine=("UZDoom", "/x/uzdoom"), warp=""):
        return compat.check(engine_name=engine[0], engine_path=engine[1], iwad_name=iwad, files=list(files), warp=warp)

    def test_doom2_maps_on_doom_iwad_warns(self):
        warnings = self.run_check(iwad="DOOM.WAD", files=[self.evit])
        self.assertTrue(any("Doom II" in w and "DOOM.WAD" in w for w in warnings), warnings)
        self.assertEqual(self.run_check(iwad="DOOM2.WAD", files=[self.evit]), [])

    def test_doom1_maps_on_doom2_iwad_warns(self):
        e1 = self.root / "e1.wad"
        make_wad(e1, ["E1M1", "E1M2"])
        warnings = self.run_check(iwad="DOOM2.WAD", files=[str(e1)])
        self.assertTrue(any("for Doom but" in w for w in warnings), warnings)

    def test_unknown_iwad_does_not_warn(self):
        self.assertEqual(self.run_check(iwad="custom.wad", files=[self.evit]), [])

    def test_two_map_files_with_the_same_maps(self):
        other = self.root / "other.wad"
        make_wad(other, ["MAP01", "MAP02"])
        warnings = self.run_check(files=[self.evit, str(other)])
        self.assertTrue(any("both contain MAP01" in w for w in warnings), warnings)

    def test_start_map_not_in_files(self):
        self.assertTrue(any("MAP30" in w for w in self.run_check(files=[self.evit], warp="MAP30")))
        self.assertEqual(self.run_check(files=[self.evit], warp="MAP02"), [])
        self.assertEqual(self.run_check(files=[self.brutal], warp="MAP30"), [])  # no maps in the files: say nothing

    def test_zdoom_data_on_a_boom_port(self):
        udmf = self.root / "u.wad"
        make_wad(udmf, ["MAP01", "TEXTMAP", "ENDMAP"])
        zs = self.root / "mod.pk3"
        with zipfile.ZipFile(zs, "w") as z:
            z.writestr("zscript.txt", "version 4.0")
        for engine in (("DSDA-Doom", "/x/dsda-doom"),):
            warnings = self.run_check(files=[str(udmf), str(zs)], engine=engine)
            self.assertTrue(any("u.wad" in w and "UDMF" in w for w in warnings), warnings)
            self.assertTrue(any("mod.pk3" in w and "ZSCRIPT" in w for w in warnings), warnings)
        self.assertEqual(self.run_check(files=[str(udmf), str(zs)]), [])  # UZDoom is fine

    def test_pk3_on_vanilla_style_port(self):
        pk = self.root / "plain.pk3"
        with zipfile.ZipFile(pk, "w") as z:
            z.writestr("sprites/x.png", "x")
        warnings = self.run_check(files=[str(pk)], engine=("Chocolate", "/x/chocolate-doom"))
        self.assertTrue(any("PK3" in w for w in warnings), warnings)
        self.assertEqual(self.run_check(files=[str(pk)], engine=("DSDA", "/x/dsda-doom")), [])

    def test_features_detected(self):
        udmf = self.root / "u.wad"
        make_wad(udmf, ["MAP01", "TEXTMAP", "DECORATE"])
        self.assertEqual(wadinfo.file_features(udmf), frozenset({"udmf", "decorate"}))
        self.assertEqual(wadinfo.file_features(self.root / "missing.wad"), frozenset())
        self.assertEqual(wadinfo.file_features(self.evit), frozenset())

    def test_launch_check_includes_compat_warnings(self):
        preset = Preset("P", engine="UZDoom", iwad="DOOM2.WAD", files=[self.evit], warp="MAP40")
        texts = [p.text for p in launch.check(self.config, preset)]
        self.assertTrue(any("MAP40" in t for t in texts), texts)


class ProgressAndMigrationTests(Base):
    def test_map_progress_helpers(self):
        p = Preset("P")
        p.set_map_done("map02", True)
        p.set_map_done("MAP01", True)
        p.set_map_done("MAP02", True)  # ticking twice keeps one entry
        self.assertEqual(p.maps_done, ["MAP01", "MAP02"])
        self.assertTrue(p.map_is_done("map01"))
        p.set_map_done("MAP01", False)
        self.assertEqual(p.maps_done, ["MAP02"])
        p.count_map_play("map05")
        p.count_map_play("MAP05")
        p.count_map_play("")
        self.assertEqual(p.map_plays, {"MAP05": 2})

    def test_launch_counts_the_start_map(self):
        preset = Preset("P", engine="UZDoom", iwad="DOOM2.WAD", files=[self.evit], warp="MAP02")
        self.config.presets.append(preset)
        self.assertEqual(launch.start_map_for(preset), "MAP02")
        self.assertEqual(launch.start_map_for(preset, map_override="MAP10"), "MAP10")
        self.assertEqual(launch.start_map_for(preset, load_save="/s.zds"), "")
        self.assertEqual(launch.start_map_for(preset, play_demo="/d.lmp"), "")

    def test_progress_fields_survive_save_and_load(self):
        preset = Preset("P", maps_done=["MAP01"], map_plays={"MAP01": 3})
        again = AppConfig.from_dict(AppConfig(presets=[preset]).to_dict()).presets[0]
        self.assertEqual((again.maps_done, again.map_plays), (["MAP01"], {"MAP01": 3}))

    def test_global_arguments_move_into_engines(self):
        data = {
            "settings": {"global_args": "+vid_vsync 1"},
            "engines": [{"name": "A", "path": "/a", "extra_args": "-x"}, {"name": "B", "path": "/b"},
                        {"name": "C", "path": "/c", "extra_args": "+vid_vsync 1"}],
        }
        config = AppConfig.from_dict(data)
        self.assertEqual([e.extra_args for e in config.engines], ["-x +vid_vsync 1", "+vid_vsync 1", "+vid_vsync 1"])
        self.assertFalse(hasattr(config.settings, "global_args"))
        # The migrated config no longer carries the old field.
        self.assertNotIn("global_args", config.to_dict()["settings"])

    def test_engine_update_fields_round_trip(self):
        config = AppConfig(engines=[Engine("U", "/u", version="v4.14.3", update_repo="UZDoom/UZDoom")])
        again = AppConfig.from_dict(config.to_dict()).engines[0]
        self.assertEqual((again.version, again.update_repo), ("v4.14.3", "UZDoom/UZDoom"))

    def test_old_configs_without_new_fields_load(self):
        again = AppConfig.from_dict({"engines": [{"name": "E", "path": "/e"}], "presets": [{"name": "P"}]})
        self.assertEqual(again.engines[0].version, "")
        self.assertEqual(again.presets[0].maps_done, [])


if __name__ == "__main__":
    unittest.main()


class AppDirTests(unittest.TestCase):
    """Where DoomDeck keeps its own files."""

    def test_portable_next_to_run_script(self):
        from doomdeck import config

        with tempfile.TemporaryDirectory() as tmp:
            program = Path(tmp)
            (program / "run.sh").write_text("#!/bin/sh\n")
            self.assertEqual(config.resolve_app_dir({}, program), program)

    def test_installed_copy_uses_xdg_config(self):
        from doomdeck import config

        with tempfile.TemporaryDirectory() as tmp:
            program = Path(tmp) / "site-packages"
            program.mkdir()
            result = config.resolve_app_dir({"XDG_CONFIG_HOME": "/xdg"}, program)
            self.assertEqual(result, Path("/xdg/doomdeck"))

    def test_home_override_wins(self):
        from doomdeck import config

        with tempfile.TemporaryDirectory() as tmp:
            program = Path(tmp)
            (program / "run.sh").write_text("")
            self.assertEqual(config.resolve_app_dir({"DOOMDECK_HOME": "/custom"}, program), Path("/custom"))
