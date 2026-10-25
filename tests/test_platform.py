"""Windows and macOS support, tested from any computer.

Nothing here can start a Windows or macOS program: the platform code takes the system
as an argument (or is patched to report another system), and the tests check the files
and command lines it would produce. What cannot be checked this way (PowerShell making a
real .lnk, hdiutil mounting a real disk image, a real engine starting) is covered by the
continuous-integration run on real Windows and macOS machines, see packaging/.
"""

from __future__ import annotations

import plistlib
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from doomdeck import config as config_io
from doomdeck import engines, importer, launch, netio, osinfo, shortcuts
from doomdeck.models import AppConfig, Engine, Preset, Settings

# A real, valid 1x1 PNG (so the icon builders have something to wrap).
PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360f8cfc000000301010018dd8db00000000049454e44ae426082"
)


def png_of(width: int, height: int) -> bytes:
    """A PNG header claiming a size (enough for the icon builders, which only read it)."""
    return (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", width, height)
            + b"\x08\x06\x00\x00\x00" + b"\x00" * 4 + b"\x00" * 20)


def release(*names: str) -> engines.Release:
    return engines.Release(tag="v1", assets=[engines.Asset(n, f"https://x/{n}", 1) for n in names])


class SystemDetectionTests(unittest.TestCase):
    def test_current_follows_sys_platform(self):
        for platform, expected in (("linux", "linux"), ("win32", "windows"), ("darwin", "macos"),
                                   ("freebsd13", "linux")):
            with mock.patch.object(osinfo.sys, "platform", platform):
                self.assertEqual(osinfo.current(), expected)

    def test_machine_names(self):
        self.assertEqual(osinfo.machine("AMD64"), "x86_64")
        self.assertEqual(osinfo.machine("aarch64"), "arm64")
        self.assertEqual(osinfo.machine("arm64"), "arm64")

    def test_per_user_folders(self):
        home = Path("/home/u")
        self.assertEqual(osinfo.app_dir_base("linux", {}, home), home / ".config" / "doomdeck")
        self.assertEqual(osinfo.app_dir_base("linux", {"XDG_CONFIG_HOME": "/x"}, home), Path("/x/doomdeck"))
        self.assertEqual(osinfo.app_dir_base("windows", {"APPDATA": "/r"}, home), Path("/r/DoomDeck"))
        self.assertEqual(osinfo.app_dir_base("windows", {}, home), home / "AppData" / "Roaming" / "DoomDeck")
        self.assertEqual(osinfo.app_dir_base("macos", {}, home), home / "Library" / "Application Support" / "DoomDeck")

    def test_default_locations(self):
        self.assertEqual(osinfo.default_doom_home("linux"), "/media/4TBSSD/Games/doom")
        self.assertEqual(osinfo.default_doom_home("windows"), "~/Games/Doom")
        self.assertEqual(osinfo.default_engine_path("~/Games/Doom", "windows"), "~/Games/Doom/UZDoom/uzdoom.exe")
        self.assertEqual(osinfo.default_engine_path("~/Games/Doom", "macos"), "~/Games/Doom/UZDoom/UZDoom.app")
        self.assertEqual(osinfo.launcher_script("windows"), "run.bat")
        self.assertEqual(osinfo.launcher_script("macos"), "run.command")
        self.assertEqual(osinfo.launcher_script("linux"), "run.sh")

    def test_portable_mode_is_recognised_for_every_run_script(self):
        for marker in ("run.sh", "run.bat", "run.command", "doomdeck.json"):
            with tempfile.TemporaryDirectory() as tmp:
                (Path(tmp) / marker).write_text("")
                self.assertEqual(config_io.resolve_app_dir({}, Path(tmp)), Path(tmp), marker)
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(osinfo, "current", return_value="macos"):
            with mock.patch.object(Path, "home", return_value=Path("/Users/me")):
                self.assertEqual(config_io.resolve_app_dir({}, Path(tmp)),
                                 Path("/Users/me/Library/Application Support/DoomDeck"))


class ArgumentTests(unittest.TestCase):
    def test_windows_backslashes_survive(self):
        self.assertEqual(
            osinfo.split_args(r'-file "C:\My Docs\a.wad" C:\Doom\b.pk3 +map MAP01', "windows"),
            ["-file", r"C:\My Docs\a.wad", r"C:\Doom\b.pk3", "+map", "MAP01"],
        )

    def test_shell_style_on_linux_and_mac(self):
        for system in ("linux", "macos"):
            self.assertEqual(osinfo.split_args(r'-file "/a b/c.wad" x\ y', system), ["-file", "/a b/c.wad", "x y"])

    def test_unbalanced_quote_never_raises(self):
        for system in ("linux", "windows", "macos"):
            self.assertEqual(osinfo.split_args('-file "oops', system), ["-file", '"oops'])

    def test_continuation_lines(self):
        self.assertEqual(osinfo.split_args("a \\\n b", "linux"), ["a", "b"])
        self.assertEqual(osinfo.split_args("a ^\n b", "windows"), ["a", "b"])

    def test_quoting_round_trips(self):
        parts = [r"C:\Program Files\UZDoom\uzdoom.exe", "-file", "my map.wad", "+map", "MAP01", "plain"]
        for system in ("windows", "linux", "macos"):
            line = osinfo.join_command(parts, system)
            self.assertEqual(osinfo.split_args(line, system), parts, system)
        self.assertEqual(osinfo.quote_arg("a", "windows"), "a")
        self.assertEqual(osinfo.quote_arg("", "windows"), '""')

    def test_detached_start_options(self):
        self.assertEqual(osinfo.popen_options(False, "windows"), {})
        self.assertEqual(osinfo.popen_options(True, "linux"), {"start_new_session": True})
        self.assertEqual(osinfo.popen_options(True, "macos"), {"start_new_session": True})
        flags = osinfo.popen_options(True, "windows")["creationflags"]
        self.assertTrue(flags & 0x00000008 and flags & 0x00000200)  # DETACHED_PROCESS, NEW_PROCESS_GROUP

    def test_importer_reads_a_windows_command_line(self):
        with mock.patch.object(osinfo, "current", return_value="windows"):
            parsed = importer.parse_command(
                r'"C:\Games\UZDoom\uzdoom.exe" -iwad "C:\Doom\DOOM2.WAD" -file C:\Doom\a.wad -skill 3 +map MAP05')
        self.assertEqual(parsed.engine, r"C:\Games\UZDoom\uzdoom.exe")
        self.assertEqual(parsed.iwad, r"C:\Doom\DOOM2.WAD")
        self.assertEqual(parsed.files, [r"C:\Doom\a.wad"])
        self.assertEqual((parsed.skill, parsed.warp), (3, "MAP05"))


class EngineProgramTests(unittest.TestCase):
    def make_app(self, root: Path, executable: str = "uzdoom", plist_name: str | None = "uzdoom") -> Path:
        app = root / "UZDoom.app"
        (app / "Contents" / "MacOS").mkdir(parents=True)
        (app / "Contents" / "MacOS" / executable).write_bytes(b"x" * 10)
        (app / "Contents" / "MacOS" / executable).chmod(0o755)
        if plist_name:
            (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps({"CFBundleExecutable": plist_name}))
        return app

    def test_mac_app_resolves_to_the_program_inside(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_app(Path(tmp))
            self.assertEqual(osinfo.resolve_program(app, "macos"), app / "Contents" / "MacOS" / "uzdoom")
            self.assertEqual(osinfo.program_problem(app, "macos"), "")
            # Without an Info.plist the program is found by the app's name, or as the only file.
            other = self.make_app(Path(tmp) / "b", executable="Renamed", plist_name=None)
            self.assertEqual(osinfo.resolve_program(other, "macos").name, "Renamed")
            # Other systems do not look inside a folder that happens to be called .app.
            self.assertEqual(osinfo.resolve_program(app, "linux"), app)

    def test_problems_are_described_per_system(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plain = root / "uzdoom"
            plain.write_bytes(b"x")
            plain.chmod(0o644)
            self.assertTrue(osinfo.program_problem(root / "nothing", "linux").startswith("not found"))
            self.assertIn("chmod +x", osinfo.program_problem(plain, "linux"))
            self.assertIn("chmod +x", osinfo.program_problem(plain, "macos"))
            plain.chmod(0o755)
            self.assertEqual(osinfo.program_problem(plain, "linux"), "")
            # Windows has no executable bit: the file name decides.
            self.assertIn(".exe", osinfo.program_problem(plain, "windows"))
            exe = root / "uzdoom.exe"
            exe.write_bytes(b"x")
            exe.chmod(0o644)
            self.assertEqual(osinfo.program_problem(exe, "windows"), "")

    def test_launch_check_uses_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = self.make_app(root)
            iwad = root / "doom2.wad"
            iwad.write_bytes(b"IWAD" + b"\0" * 8)
            config = AppConfig(settings=Settings(iwads_dir=str(root)))
            config.engines.append(Engine(name="UZDoom", path=str(app)))
            preset = Preset(name="P", engine="UZDoom", iwad="doom2.wad")
            with mock.patch.object(osinfo, "current", return_value="macos"):
                self.assertEqual([p for p in launch.check(config, preset) if p.level == "error"], [])
                cmd = launch.build_command(config, preset)
        self.assertEqual(cmd[0], str(app / "Contents" / "MacOS" / "uzdoom"))

    def test_engine_ini_folders(self):
        home, docs = Path("/home/u"), Path("/home/u/Documents")
        win = osinfo.engine_ini_folders(["uzdoom"], "windows", {"APPDATA": "/r"}, home, docs)
        self.assertIn((docs / "My Games" / "UZDoom", False), win)
        self.assertIn((Path("/r/UZDoom"), False), win)
        mac = osinfo.engine_ini_folders(["uzdoom"], "macos", {}, home)
        self.assertEqual(mac[0], (home / "Library" / "Preferences", True))
        lin = osinfo.engine_ini_folders(["uzdoom"], "linux", {}, home)
        self.assertEqual(lin, [(home / ".config" / "uzdoom", False)])

    def test_shared_preferences_folder_only_gives_engine_inis(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefs = Path(tmp) / "Library" / "Preferences"
            prefs.mkdir(parents=True)
            for name in ("uzdoom.ini", "com.apple.dock.ini", "gzdoom.ini"):
                (prefs / name).write_text("")
            engine = Engine(name="UZDoom", path=str(Path(tmp) / "elsewhere" / "uzdoom"))
            with mock.patch.object(osinfo, "current", return_value="macos"), \
                    mock.patch.object(Path, "home", return_value=Path(tmp)):
                found = sorted(p.name for p in launch.engine_config_files(engine))
        self.assertEqual(found, ["gzdoom.ini", "uzdoom.ini"])


class DownloadChoiceTests(unittest.TestCase):
    # Asset names as UZDoom's release workflow produces them (checked in .github/workflows/release_core.yml).
    UZDOOM = ("Windows-UZDoom-1.2.3-x86_64.zip", "MacOS-UZDoom-1.2.3-ARM64.dmg",
              "Linux-UZDoom-1.2.3-x86_64.AppImage", "Linux-UZDoom-1.2.3-x86_64.AppImage.zsync",
              "Source code (zip)", "Source code (tar.gz)")

    def names(self, system, cpu, *assets):
        return [a.name for a in engines.assets_for(release(*(assets or self.UZDOOM)), system, cpu)]

    def test_each_system_gets_its_own_build(self):
        self.assertEqual(self.names("windows", "x86_64"), ["Windows-UZDoom-1.2.3-x86_64.zip"])
        self.assertEqual(self.names("macos", "arm64"), ["MacOS-UZDoom-1.2.3-ARM64.dmg"])
        self.assertEqual(self.names("linux", "x86_64"), ["Linux-UZDoom-1.2.3-x86_64.AppImage"])

    def test_nothing_is_offered_that_cannot_run(self):
        self.assertEqual(self.names("macos", "x86_64"), [])  # an Intel Mac cannot run an ARM-only build
        self.assertEqual(self.names("linux", "arm64"), [])

    def test_windows_on_arm_can_use_the_x64_build(self):
        self.assertEqual(self.names("windows", "arm64"), ["Windows-UZDoom-1.2.3-x86_64.zip"])

    def test_darwin_is_not_mistaken_for_windows(self):
        names = self.names("windows", "x86_64", "gzdoom-darwin-x64.zip", "gzdoom-4-14-3-windows.zip")
        self.assertEqual(names, ["gzdoom-4-14-3-windows.zip"])

    def test_installers_and_source_are_left_out(self):
        names = self.names("windows", "x86_64", "setup.exe", "uzdoom.msi", "uzdoom-src.zip", "uzdoom-win64.zip")
        self.assertEqual(names, ["uzdoom-win64.zip"])

    def test_a_plain_archive_is_only_guessed_for_linux(self):
        self.assertEqual(self.names("linux", "x86_64", "engine.tar.gz"), ["engine.tar.gz"])
        self.assertEqual(self.names("windows", "x86_64", "engine.tar.gz"), [])


class FindProgramTests(unittest.TestCase):
    def test_windows_exe(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "bin").mkdir()
            (root / "bin" / "uzdoom.exe").write_bytes(b"x")
            (root / "bin" / "sndfile.dll").write_bytes(b"x")
            self.assertEqual(engines.find_program(root, system="windows"), root / "bin" / "uzdoom.exe")

    def test_windows_only_big_exe_when_the_name_is_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "weird.exe").write_bytes(b"x" * 2_000_000)
            (root / "unins000.exe").write_bytes(b"x" * 2_000_000)
            self.assertEqual(engines.find_program(root, system="windows"), root / "weird.exe")

    def test_mac_app_is_the_program(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "UZDoom.app" / "Contents" / "MacOS").mkdir(parents=True)
            (root / "UZDoom.app" / "Contents" / "MacOS" / "uzdoom").write_bytes(b"x")
            (root / "UZDoom.app" / "Contents" / "Frameworks" / "Helper.app").mkdir(parents=True)
            self.assertEqual(engines.find_program(root, system="macos"), root / "UZDoom.app")

    def test_dmg_is_unpacked_through_hdiutil(self):
        calls = []

        def fake_run(args, **kwargs):
            calls.append(args[:2])
            if args[:2] == ["hdiutil", "attach"]:
                mount = Path(args[args.index("-mountpoint") + 1])
                (mount / "UZDoom.app" / "Contents" / "MacOS").mkdir(parents=True)
                (mount / "UZDoom.app" / "Contents" / "MacOS" / "uzdoom").write_bytes(b"x")
            return mock.Mock(returncode=0)

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(osinfo, "current", return_value="macos"), \
                mock.patch.object(engines.subprocess, "run", fake_run):
            dmg = Path(tmp) / "x.dmg"
            dmg.write_bytes(b"dmg")
            dest = Path(tmp) / "out"
            engines.unpack_dmg(dmg, dest)
            self.assertTrue((dest / "UZDoom.app" / "Contents" / "MacOS" / "uzdoom").is_file())
        self.assertEqual(calls, [["hdiutil", "attach"], ["hdiutil", "detach"]])  # always unmounted

    def test_dmg_is_refused_elsewhere(self):
        with mock.patch.object(osinfo, "current", return_value="linux"):
            with self.assertRaises(netio.NetError):
                engines.unpack_dmg("x.dmg", "out")

    def test_install_asset_returns_the_app_on_macos(self):
        def fake_download(url, dest, progress=None, cancelled=None):
            Path(dest).write_bytes(b"dmg")
            return Path(dest)

        def fake_run(args, **kwargs):
            if args[:2] == ["hdiutil", "attach"]:
                mount = Path(args[args.index("-mountpoint") + 1])
                (mount / "UZDoom.app" / "Contents" / "MacOS").mkdir(parents=True)
                (mount / "UZDoom.app" / "Contents" / "MacOS" / "uzdoom").write_bytes(b"x")
            return mock.Mock(returncode=0)

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(osinfo, "current", return_value="macos"), \
                mock.patch.object(engines.netio, "download", fake_download), \
                mock.patch.object(engines.subprocess, "run", fake_run):
            program = engines.install_asset(engines.Asset("MacOS-UZDoom-1-ARM64.dmg", "u"), "v1", tmp)
            self.assertEqual(program.name, "UZDoom.app")
            self.assertTrue(osinfo.resolve_program(program, "macos").is_file())


class ShortcutTests(unittest.TestCase):
    def test_icon_builders(self):
        ico = shortcuts.build_ico(png_of(256, 256))
        self.assertEqual(struct.unpack("<HHH", ico[:6]), (0, 1, 1))
        width, height, _c, _r, planes, bpp, size, offset = struct.unpack("<BBBBHHII", ico[6:22])
        self.assertEqual((width, height, planes, bpp, offset), (0, 0, 1, 32, 22))  # 0 means 256
        self.assertEqual(size, len(ico) - 22)
        self.assertIsNone(shortcuts.build_ico(png_of(512, 512)))  # an .ico cannot hold this
        self.assertIsNone(shortcuts.build_ico(b"not a png"))
        icns = shortcuts.build_icns(png_of(256, 256))
        self.assertEqual(icns[:4], b"icns")
        self.assertEqual(struct.unpack(">I", icns[4:8])[0], len(icns))
        self.assertEqual(icns[8:12], b"ic08")
        self.assertIsNone(shortcuts.build_icns(png_of(200, 200)))
        self.assertIsNone(shortcuts.build_icns(png_of(256, 128)))

    def test_real_png_size_is_read(self):
        self.assertEqual(shortcuts.png_size(PNG_1X1), (1, 1))

    def test_linux_desktop_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            program = Path(tmp) / "prog"
            program.mkdir()
            (program / "run.sh").write_text("#!/bin/sh\n")
            made = shortcuts.create(Preset(name="My Game"), program, Path("/i/icon.png"), "linux", Path(tmp) / "apps")
            text = made.path.read_text()
        self.assertEqual(made.where, "application menu")
        self.assertIn(f'Exec="{program}/run.sh" --launch "My Game"', text)
        self.assertIn("Icon=/i/icon.png", text)

    def test_macos_app_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            program = Path(tmp) / "prog"
            program.mkdir()
            (program / "run.command").write_text("#!/bin/bash\n")
            png = Path(tmp) / "icon.png"
            png.write_bytes(png_of(256, 256))
            made = shortcuts.create(Preset(name="Doom: Ultimate"), program, png, "macos", Path(tmp) / "apps")
            app = made.path
            self.assertEqual(app.suffix, ".app")
            self.assertNotIn(":", app.name)
            script = app / "Contents" / "MacOS" / "launch"
            self.assertTrue(script.stat().st_mode & 0o100)  # executable
            self.assertIn(f"exec {program}/run.command --launch 'Doom: Ultimate'", script.read_text())
            info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
            self.assertEqual(info["CFBundleExecutable"], "launch")
            self.assertEqual(info["CFBundleIconFile"], "icon")
            self.assertTrue((app / "Contents" / "Resources" / "icon.icns").is_file())
            # A picture of the wrong size just means no custom icon, not a failed shortcut.
            png.write_bytes(png_of(300, 300))
            again = shortcuts.create(Preset(name="Plain"), program, png, "macos", Path(tmp) / "apps")
            self.assertNotIn("CFBundleIconFile", plistlib.loads((again.path / "Contents" / "Info.plist").read_bytes()))

    def test_windows_shortcut_goes_through_powershell(self):
        seen = {}

        def fake_runner(args, env=None, **kwargs):
            seen["args"], seen["env"] = args, env
            Path(env["DD_LNK"]).write_bytes(b"lnk")
            return mock.Mock(returncode=0)

        with tempfile.TemporaryDirectory() as tmp:
            program = Path(tmp) / "prog"
            (program / "doomdeck").mkdir(parents=True)
            (program / "doomdeck" / "__main__.py").write_text("")
            png = Path(tmp) / "icon.png"
            png.write_bytes(png_of(256, 256))
            made = shortcuts.create(Preset(name="My Game"), program, png, "windows", Path(tmp) / "menu",
                                    Path(tmp) / "icons", fake_runner)
            env = seen["env"]
            self.assertEqual(made.path.name, "My Game.lnk")
            self.assertEqual(made.where, "Start menu")
            self.assertEqual(seen["args"][0], "powershell")
            self.assertEqual(env["DD_DIR"], str(program))
            self.assertTrue(env["DD_ARGS"].endswith('--launch "My Game"'))
            self.assertIn("-m doomdeck", env["DD_ARGS"])
            self.assertTrue(Path(env["DD_ICON"]).is_file())

    def test_windows_falls_back_to_a_cmd_file(self):
        def failing_runner(args, **kwargs):
            raise OSError("no powershell")

        with tempfile.TemporaryDirectory() as tmp:
            program = Path(tmp) / "prog"
            (program / "doomdeck").mkdir(parents=True)
            (program / "doomdeck" / "__main__.py").write_text("")
            made = shortcuts.create(Preset(name="Fallback"), program, None, "windows", Path(tmp) / "menu",
                                    Path(tmp) / "icons", failing_runner)
            text = made.path.read_text()
        self.assertEqual(made.path.suffix, ".cmd")
        self.assertIn("--launch Fallback", text)

    def test_windows_prefers_the_private_venv(self):
        with tempfile.TemporaryDirectory() as tmp:
            program = Path(tmp)
            (program / ".venv" / "Scripts").mkdir(parents=True)
            (program / ".venv" / "Scripts" / "pythonw.exe").write_text("")
            command, workdir = shortcuts.program_command(program, "windows")
        self.assertEqual(command, [str(program / ".venv" / "Scripts" / "pythonw.exe"), "-m", "doomdeck"])
        self.assertEqual(workdir, program)

    def test_where_text(self):
        self.assertEqual(shortcuts.where_text("windows"), "your Start menu")
        self.assertEqual(shortcuts.where_text("macos"), "your Applications folder")


class RunScriptTests(unittest.TestCase):
    ROOT = Path(__file__).resolve().parent.parent

    def test_scripts_exist_and_start_the_package(self):
        for name in ("run.sh", "run.bat", "run.command"):
            text = (self.ROOT / name).read_text()
            self.assertIn("doomdeck", text, name)
            self.assertIn("PySide6", text, name)

    def test_windows_script_uses_crlf_safe_syntax(self):
        text = (self.ROOT / "run.bat").read_text()
        self.assertTrue(text.startswith("@echo off"))
        self.assertIn("pythonw", text)  # no console box for the window
        self.assertIn("%*", text)  # command line options are passed on

    def test_mac_script_is_a_bash_script(self):
        self.assertTrue((self.ROOT / "run.command").read_text().startswith("#!/usr/bin/env bash"))


if __name__ == "__main__":
    unittest.main()
