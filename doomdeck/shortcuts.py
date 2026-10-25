"""Menu shortcuts that start one preset directly, on Linux, Windows and macOS.

* Linux: a freedesktop ``.desktop`` entry in ~/.local/share/applications.
* Windows: a ``.lnk`` in the Start menu (made by PowerShell, which every Windows has),
  with an ``.ico`` built from the picture; a ``.cmd`` file if PowerShell is unavailable.
* macOS: a small ``.app`` in ~/Applications/DoomDeck whose program is a shell script,
  with an ``.icns`` built from the picture.

The ico and icns formats can hold a PNG as they are, so no image library is needed
here: the caller hands in a PNG of a suitable size (the window scales one with Qt).

Nothing in this module touches Qt. Every function takes `system` and the folders to
write into, so each platform can be tested from any computer.
"""

from __future__ import annotations

import plistlib
import re
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import launch, osinfo
from .models import Preset, safe_name

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
# icns entry types that hold a PNG, by pixel size.
_ICNS_TYPES = {128: b"ic07", 256: b"ic08", 512: b"ic09", 1024: b"ic10"}


@dataclass
class Shortcut:
    """A shortcut that was written: its file and where the person will find it."""

    path: Path
    where: str


def png_size(data: bytes) -> tuple[int, int] | None:
    """(width, height) of PNG data, or None if it is not a PNG."""
    if len(data) < 24 or data[:8] != PNG_MAGIC or data[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", data[16:24])


def build_ico(png: bytes) -> bytes | None:
    """An .ico holding one PNG image (Windows Vista and later read these). None when the
    data is not a PNG or is larger than the 256 x 256 an .ico can hold."""
    size = png_size(png)
    if size is None or max(size) > 256:
        return None
    width, height = size
    header = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", width % 256, height % 256, 0, 0, 1, 32, len(png), 6 + 16)
    return header + entry + png


def build_icns(png: bytes) -> bytes | None:
    """An .icns holding one PNG image. None unless the PNG is square and 128, 256, 512
    or 1024 pixels wide."""
    size = png_size(png)
    if size is None or size[0] != size[1] or size[0] not in _ICNS_TYPES:
        return None
    chunk = _ICNS_TYPES[size[0]] + struct.pack(">I", 8 + len(png)) + png
    return b"icns" + struct.pack(">I", 8 + len(chunk)) + chunk


def program_command(program_dir: Path, system: str | None = None) -> tuple[list[str], Path]:
    """(command, working folder) that starts DoomDeck, for a shortcut to add arguments to.

    Windows: the quiet `pythonw` of the private ./.venv if there is one, else the Python
    running now, with `-m doomdeck` run from the program folder (so a shortcut shows no
    console box). Linux and macOS: the run script when it exists, else the installed
    `doomdeck` program, else this Python.
    """
    import shutil
    import sys

    system = system or osinfo.current()
    script = program_dir / osinfo.launcher_script(system)
    if system == osinfo.WINDOWS:
        venv = program_dir / ".venv" / "Scripts" / "pythonw.exe"
        if venv.is_file():
            return [str(venv), "-m", "doomdeck"], program_dir
        if (program_dir / "doomdeck" / "__main__.py").is_file():
            return [osinfo.gui_python(system=system), "-m", "doomdeck"], program_dir
        installed = shutil.which("doomdeck")
        return ([installed] if installed else [osinfo.gui_python(), "-m", "doomdeck"]), program_dir
    if script.is_file():
        return [str(script)], program_dir
    installed = shutil.which("doomdeck")
    return ([installed] if installed else [sys.executable, "-m", "doomdeck"]), program_dir


def _display_name(preset: Preset) -> str:
    return " ".join(preset.name.split()) or "Doom"


# ---- Linux ---------------------------------------------------------------------------------


def _create_linux(preset: Preset, command: list[str], icon: Path | None, workdir: Path,
                  base_dir: Path | None) -> Shortcut:
    target = launch.write_desktop_entry(preset, command, str(icon or ""), workdir, base_dir)
    return Shortcut(target, "application menu")


# ---- Windows -------------------------------------------------------------------------------

_POWERSHELL_LNK = (
    "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:DD_LNK); "
    "$s.TargetPath = $env:DD_TARGET; $s.Arguments = $env:DD_ARGS; "
    "$s.WorkingDirectory = $env:DD_DIR; $s.Description = $env:DD_DESC; "
    "if ($env:DD_ICON) { $s.IconLocation = $env:DD_ICON }; $s.Save()"
)


def windows_arguments(args: list[str]) -> str:
    """Arguments joined for a Windows shortcut's "Arguments" box (double quotes)."""
    return subprocess.list2cmdline(args)


def start_menu_folder(environ: dict | None = None, home: Path | None = None) -> Path:
    """The Start menu's per-user Programs folder."""
    import os

    environ = os.environ if environ is None else environ
    roaming = Path(environ.get("APPDATA") or (home or Path.home()) / "AppData" / "Roaming")
    return roaming / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "DoomDeck"


def _create_windows(preset: Preset, command: list[str], png: bytes | None, workdir: Path,
                    folder: Path, icon_dir: Path, runner=subprocess.run) -> Shortcut:
    name = _display_name(preset)
    folder.mkdir(parents=True, exist_ok=True)
    icon_location = ""
    ico = build_ico(png) if png else None
    if ico:
        icon_dir.mkdir(parents=True, exist_ok=True)
        icon_file = icon_dir / f"{safe_name(preset.name)}.ico"
        icon_file.write_bytes(ico)
        icon_location = str(icon_file)
    args = [*command[1:], "--launch", preset.name]
    link = folder / f"{safe_name(name)}.lnk"
    environment = {
        "DD_LNK": str(link), "DD_TARGET": command[0], "DD_ARGS": windows_arguments(args),
        "DD_DIR": str(workdir), "DD_DESC": f"Play {name} with DoomDeck", "DD_ICON": icon_location,
    }
    try:
        import os

        result = runner(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-Command", _POWERSHELL_LNK],
            env={**os.environ, **environment}, capture_output=True, timeout=60, check=False,
        )
        if result.returncode == 0 and link.is_file():
            return Shortcut(link, "Start menu")
    except (OSError, subprocess.SubprocessError):
        pass
    # PowerShell failed or is missing: a .cmd file in the same folder still shows up in search.
    fallback = folder / f"{safe_name(name)}.cmd"
    line = subprocess.list2cmdline([command[0], *args])
    fallback.write_text(f'@echo off\r\ncd /d "{workdir}"\r\nstart "" {line}\r\n', encoding="utf-8")
    return Shortcut(fallback, "Start menu")


# ---- macOS ---------------------------------------------------------------------------------


def applications_folder(home: Path | None = None) -> Path:
    """The per-user Applications folder DoomDeck's shortcut apps go in."""
    return (home or Path.home()) / "Applications" / "DoomDeck"


def mac_launch_script(command: list[str], workdir: Path, preset_name: str) -> str:
    """The shell script a shortcut app runs: start DoomDeck's launcher for one preset."""
    import shlex

    line = " ".join(shlex.quote(part) for part in [*command, "--launch", preset_name])
    return f"#!/bin/bash\ncd {shlex.quote(str(workdir))} || exit 1\nexec {line}\n"


def mac_info_plist(preset: Preset, has_icon: bool) -> bytes:
    """Info.plist for a shortcut app."""
    slug = re.sub(r"[^a-z0-9]+", "-", preset.name.lower()).strip("-") or "preset"
    info = {
        "CFBundleName": _display_name(preset)[:40],
        "CFBundleDisplayName": _display_name(preset),
        "CFBundleIdentifier": f"local.doomdeck.{slug}",
        "CFBundleExecutable": "launch",
        "CFBundlePackageType": "APPL",
        "CFBundleVersion": "1",
        "CFBundleShortVersionString": "1.0",
        "NSHighResolutionCapable": True,
    }
    if has_icon:
        info["CFBundleIconFile"] = "icon"
    return plistlib.dumps(info)


def _create_macos(preset: Preset, command: list[str], png: bytes | None, workdir: Path,
                  folder: Path) -> Shortcut:
    app = folder / f"{safe_name(_display_name(preset))}.app"
    macos = app / "Contents" / "MacOS"
    resources = app / "Contents" / "Resources"
    macos.mkdir(parents=True, exist_ok=True)
    resources.mkdir(parents=True, exist_ok=True)
    icns = build_icns(png) if png else None
    if icns:
        (resources / "icon.icns").write_bytes(icns)
    (app / "Contents" / "Info.plist").write_bytes(mac_info_plist(preset, bool(icns)))
    script = macos / "launch"
    script.write_text(mac_launch_script(command, workdir, preset.name), encoding="utf-8")
    script.chmod(0o755)
    return Shortcut(app, "Applications folder (Applications/DoomDeck)")


# ---- the one entry point -------------------------------------------------------------------


def create(preset: Preset, program_dir: Path, icon_png: Path | None = None, system: str | None = None,
           target_dir: Path | None = None, icon_dir: Path | None = None, runner=subprocess.run) -> Shortcut:
    """Write the shortcut for a preset on this system and say where it went.

    `icon_png` is a PNG file (any size on Linux; at most 256 x 256 for Windows; 128, 256 or
    512 square for macOS, otherwise the shortcut simply has no custom icon).
    `target_dir` overrides the folder written into, which is what the tests use.
    """
    system = system or osinfo.current()
    command, workdir = program_command(program_dir, system)
    if system == osinfo.LINUX:
        return _create_linux(preset, command, icon_png, workdir, target_dir)
    png = None
    if icon_png is not None:
        try:
            png = Path(icon_png).read_bytes()
        except OSError:
            png = None
    if system == osinfo.WINDOWS:
        return _create_windows(preset, command, png, workdir, target_dir or start_menu_folder(),
                               icon_dir or (program_dir / "shortcut-icons"), runner)
    return _create_macos(preset, command, png, workdir, target_dir or applications_folder())


def where_text(system: str | None = None) -> str:
    """Words for "your application menu" on this system."""
    return {osinfo.LINUX: "your application menu", osinfo.WINDOWS: "your Start menu",
            osinfo.MACOS: "your Applications folder"}[system or osinfo.current()]
