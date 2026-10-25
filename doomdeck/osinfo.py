"""Everything that differs between Linux, Windows and macOS, in one place.

The rest of DoomDeck asks this module instead of testing `sys.platform` itself, so the
differences are easy to find, and every function takes an optional `system` argument
("linux", "windows" or "macos") so the other platforms can be unit tested from any
computer. No Qt in here.

What differs:

* where DoomDeck keeps its own files when it is installed rather than run from a folder;
* what counts as a runnable engine (a macOS ``UZDoom.app`` is a folder, a Windows
  engine is an ``.exe``, a Linux engine needs the executable bit);
* how a typed argument string is split and how a command is shown as one line (shells
  treat backslashes differently on Windows);
* how a game is started so it outlives DoomDeck;
* where the engines themselves keep their ini files.
"""

from __future__ import annotations

import os
import plistlib
import shlex
import subprocess
import sys
from pathlib import Path

LINUX, WINDOWS, MACOS = "linux", "windows", "macos"

# Files whose presence beside the program means "run from this folder" (portable mode).
PORTABLE_MARKERS = ("run.sh", "run.bat", "run.command", "doomdeck.json")
# Extensions an engine program may have on Windows.
_WINDOWS_PROGRAMS = (".exe", ".bat", ".cmd", ".com")


def current() -> str:
    """The system this copy of Python is running on."""
    if sys.platform.startswith("win"):
        return WINDOWS
    if sys.platform == "darwin":
        return MACOS
    return LINUX


def pretty(system: str | None = None) -> str:
    """A name to show people."""
    return {LINUX: "Linux", WINDOWS: "Windows", MACOS: "macOS"}[system or current()]


def machine(raw: str | None = None) -> str:
    """"arm64" or "x86_64" (or the raw value for anything else)."""
    import platform

    value = (raw if raw is not None else platform.machine()).lower()
    if value in ("arm64", "aarch64", "armv8", "armv8l"):
        return "arm64"
    if value in ("x86_64", "amd64", "x64"):
        return "x86_64"
    return value


# ---- where DoomDeck keeps its own files --------------------------------------------------


def app_dir_base(system: str | None = None, environ: dict | None = None, home: Path | None = None) -> Path:
    """The per-user folder used when DoomDeck is installed (not run from its own folder)."""
    system = system or current()
    environ = os.environ if environ is None else environ
    home = home or Path.home()
    if system == WINDOWS:
        return Path(environ.get("APPDATA") or home / "AppData" / "Roaming") / "DoomDeck"
    if system == MACOS:
        return home / "Library" / "Application Support" / "DoomDeck"
    return Path(environ.get("XDG_CONFIG_HOME") or home / ".config") / "doomdeck"


def default_doom_home(system: str | None = None) -> str:
    """Where the Doom files are assumed to live before the person says otherwise.

    Always editable in Settings. Linux keeps the original default; the other systems
    use a folder in the home directory, written with "~" so it works for any user.
    """
    if (system or current()) == LINUX:
        return "/media/4TBSSD/Games/doom"
    return "~/Games/Doom"


def default_engine_path(doom_home: str, system: str | None = None) -> str:
    """Where a first-run UZDoom is expected (the program, or the .app on macOS)."""
    system = system or current()
    if system == WINDOWS:
        return f"{doom_home}/UZDoom/uzdoom.exe"
    if system == MACOS:
        return f"{doom_home}/UZDoom/UZDoom.app"
    return f"{doom_home}/UZDoom/uzdoom"


def launcher_script(system: str | None = None) -> str:
    """Name of the script in the program folder that starts DoomDeck."""
    return {LINUX: "run.sh", WINDOWS: "run.bat", MACOS: "run.command"}[system or current()]


def gui_python(executable: str | None = None, system: str | None = None) -> str:
    """The Python to start the window with: on Windows `pythonw.exe` (no console box)."""
    executable = executable or sys.executable
    if (system or current()) == WINDOWS:
        path = Path(executable)
        if path.name.lower() == "python.exe":
            quiet = path.with_name("pythonw.exe")
            if quiet.is_file():
                return str(quiet)
    return executable


# ---- engines -------------------------------------------------------------------------------


def resolve_program(path: str | Path, system: str | None = None) -> Path:
    """The file to execute for an engine path.

    On macOS a ``Something.app`` is a folder; the program is inside it
    (``Contents/MacOS/<CFBundleExecutable>``). Anything else is returned unchanged.
    """
    path = Path(path).expanduser()
    if (system or current()) == MACOS and path.suffix.lower() == ".app" and path.is_dir():
        macos_dir = path / "Contents" / "MacOS"
        name = ""
        try:
            with open(path / "Contents" / "Info.plist", "rb") as handle:
                name = str(plistlib.load(handle).get("CFBundleExecutable") or "")
        except (OSError, plistlib.InvalidFileException, ValueError):
            pass
        for candidate in (name, path.stem, path.stem.lower()):
            if candidate and (macos_dir / candidate).is_file():
                return macos_dir / candidate
        try:
            files = sorted(p for p in macos_dir.iterdir() if p.is_file())
        except OSError:
            files = []
        if len(files) == 1:
            return files[0]
    return path


def program_problem(path: str | Path, system: str | None = None) -> str:
    """Why an engine path cannot be started, or "" if it can.

    The text starts with "not found" or "not executable" so callers can word their
    own message around it.
    """
    system = system or current()
    program = resolve_program(path, system)
    if not program.is_file():
        if system == MACOS and program.suffix.lower() == ".app":
            return "not found (this app has no program inside it)" if program.is_dir() else "not found"
        return "not found"
    if system == WINDOWS:
        if program.suffix.lower() not in _WINDOWS_PROGRAMS:
            return "not executable (Windows programs end in .exe)"
        return ""
    if not os.access(program, os.X_OK):
        hint = "run chmod +x on it" if system == LINUX else "run chmod +x on it in Terminal"
        return f"not executable ({hint})"
    return ""


def make_executable(path: str | Path) -> None:
    """Add the executable bit (a no-op where the idea does not exist)."""
    import stat

    path = Path(path)
    try:
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
    except OSError:
        pass


def program_names(base_names: tuple[str, ...], system: str | None = None) -> tuple[str, ...]:
    """Program names to look for in an unpacked engine, with ".exe" added on Windows."""
    if (system or current()) == WINDOWS:
        return tuple(f"{n}.exe" for n in base_names)
    return base_names


# ---- command lines -------------------------------------------------------------------------


def split_args(text: str, system: str | None = None) -> list[str]:
    """Split a typed argument string into arguments.

    Linux and macOS work like a shell (quotes and backslash escapes). On Windows a
    backslash is just a character (so `C:\\Doom\\a.wad` survives) and both quote kinds
    group words. Line continuations are removed first. Never raises: an unbalanced
    quote falls back to splitting on spaces.
    """
    system = system or current()
    for continuation in ("\\\r\n", "\\\n") + (("^\r\n", "^\n") if system == WINDOWS else ()):
        text = text.replace(continuation, " ")
    if system != WINDOWS:
        try:
            return shlex.split(text)
        except ValueError:
            return text.split()
    args: list[str] = []
    current_arg: list[str] = []
    quote = ""
    started = False
    for char in text:
        if quote:
            if char == quote:
                quote = ""
            else:
                current_arg.append(char)
        elif char in "\"'":
            quote, started = char, True
        elif char.isspace():
            if started:
                args.append("".join(current_arg))
                current_arg, started = [], False
        else:
            current_arg.append(char)
            started = True
    if quote:  # unbalanced: keep what was typed instead of failing
        return text.split()
    if started:
        args.append("".join(current_arg))
    return args


def quote_arg(arg: str, system: str | None = None) -> str:
    """Quote one argument so `split_args` reads it back unchanged."""
    if (system or current()) != WINDOWS:
        return shlex.quote(arg)
    if arg and not any(c.isspace() or c in "\"'" for c in arg):
        return arg
    if '"' not in arg:
        return f'"{arg}"'
    return f"'{arg}'" if "'" not in arg else arg  # both kinds inside: leave it, cannot be quoted


def join_command(cmd: list[str], system: str | None = None) -> str:
    """A command as one line a person could paste into a terminal."""
    return " ".join(quote_arg(part, system) for part in cmd)


# ---- starting a game -----------------------------------------------------------------------


def popen_options(detach: bool, system: str | None = None) -> dict:
    """Extra `subprocess.Popen` arguments so a detached game outlives DoomDeck."""
    if not detach:
        return {}
    if (system or current()) == WINDOWS:
        flags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200
        )
        return {"creationflags": flags}
    return {"start_new_session": True}


# ---- engine ini files ------------------------------------------------------------------------


def documents_folder(system: str | None = None, home: Path | None = None) -> Path:
    """The user's Documents folder (asks Windows, which may have moved it to OneDrive)."""
    home = home or Path.home()
    if (system or current()) == WINDOWS and sys.platform.startswith("win"):
        try:
            import ctypes

            buffer = ctypes.create_unicode_buffer(260)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buffer) == 0 and buffer.value:  # CSIDL_PERSONAL
                return Path(buffer.value)
        except (AttributeError, OSError):
            pass
    return home / "Documents"


def engine_ini_folders(names: list[str], system: str | None = None, environ: dict | None = None,
                       home: Path | None = None, documents: Path | None = None) -> list[tuple[Path, bool]]:
    """Folders where source ports keep their ini files, as (folder, shared) pairs.

    `shared` is true for a folder several programs use (macOS ~/Library/Preferences), so
    the caller should only take ini files named after the engine from it. `names` are
    lower-case program names ("uzdoom", "gzdoom"...). Checked against UZDoom's source:
    Linux ~/.config/<name>/<name>.ini, Windows Documents/My Games/<Name>/<name>.ini (older
    versions: %APPDATA%/<Name>), macOS ~/Library/Preferences/<name>.ini.
    """
    system = system or current()
    environ = os.environ if environ is None else environ
    home = home or Path.home()
    folders: list[tuple[Path, bool]] = []
    if system == WINDOWS:
        docs = documents or documents_folder(system, home)
        roaming = Path(environ.get("APPDATA") or home / "AppData" / "Roaming")
        for name in names:
            nice = {"uzdoom": "UZDoom", "gzdoom": "GZDoom", "lzdoom": "LZDoom", "qzdoom": "QZDoom",
                    "zandronum": "Zandronum", "zdoom": "ZDoom"}.get(name, name)
            folders += [(docs / "My Games" / nice, False), (roaming / nice, False)]
    elif system == MACOS:
        folders.append((home / "Library" / "Preferences", True))
        folders += [(home / "Library" / "Application Support" / name, False) for name in names]
    else:
        base = Path(environ.get("XDG_CONFIG_HOME") or home / ".config")
        folders += [(base / name, False) for name in names]
    return folders
