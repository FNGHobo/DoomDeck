"""Loading and saving the DoomDeck config file.

DoomDeck keeps its own files (config, logs, backups, pictures, downloaded engines) in
one folder, APP_DIR:

* run from a source folder (the folder has run.sh, run.bat or run.command): beside the
  program, so the whole tool stays in one folder and can be moved around;
* installed as a package (pacman/pip): the per-user folder of the system
  (~/.config/doomdeck on Linux, %APPDATA%\\DoomDeck on Windows,
  ~/Library/Application Support/DoomDeck on macOS), since the install location is read-only;
* DOOMDECK_HOME in the environment overrides both.

Set DOOMDECK_CONFIG to use a different config file inside that folder.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from pathlib import Path

from . import osinfo
from .models import AppConfig, Engine, DEFAULT_DOOM_HOME

PROGRAM_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILENAME = "doomdeck.json"


def resolve_app_dir(environ: dict | None = None, program_dir: Path | None = None) -> Path:
    """Where DoomDeck keeps its own files (see the module docstring)."""
    environ = os.environ if environ is None else environ
    program_dir = program_dir or PROGRAM_DIR
    if environ.get("DOOMDECK_HOME"):
        return Path(environ["DOOMDECK_HOME"]).expanduser()
    portable = any((program_dir / marker).is_file() for marker in osinfo.PORTABLE_MARKERS)
    if portable and os.access(program_dir, os.W_OK):
        return program_dir
    return osinfo.app_dir_base(environ=environ)


APP_DIR = resolve_app_dir()


def find_icon() -> Path | None:
    """The DoomDeck icon file, wherever this copy of the program keeps it."""
    for candidate in (
        APP_DIR / "doomdeck.png",
        PROGRAM_DIR / "doomdeck.png",
        Path(__file__).resolve().parent / "doomdeck.png",
        Path("/usr/share/icons/hicolor/256x256/apps/doomdeck.png"),
    ):
        if candidate.is_file():
            return candidate
    return None


def launcher_command() -> list[str]:
    """Command that starts DoomDeck, for shortcuts: this system's run script if it exists
    (run.sh, run.bat or run.command), else the installed `doomdeck` program, else this
    Python running the package."""
    script = PROGRAM_DIR / osinfo.launcher_script()
    if script.is_file():
        return [str(script)]
    installed = shutil.which("doomdeck")
    return [installed] if installed else [osinfo.gui_python(), "-m", "doomdeck"]


def default_path() -> Path:
    """Where the config file lives: DOOMDECK_CONFIG if set, else beside the program."""
    override = os.environ.get("DOOMDECK_CONFIG")
    return Path(override).expanduser() if override else APP_DIR / CONFIG_FILENAME


def default_config() -> AppConfig:
    """A first-run config with the user's UZDoom install pre-filled as an engine."""
    config = AppConfig()
    config.engines.append(
        Engine(name="UZDoom", path=osinfo.default_engine_path(DEFAULT_DOOM_HOME))
    )
    return config


def load(path: Path | None = None) -> AppConfig:
    """Read the config file, or return a first-run config if it does not exist yet.

    A corrupt file is moved aside to <name>.bad instead of being overwritten, so a
    typo in hand-edited JSON never silently destroys the user's presets.
    """
    path = path or default_path()
    if not path.exists():
        return default_config()
    try:
        return AppConfig.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        path.replace(path.with_suffix(path.suffix + ".bad"))
        return default_config()


def backup(path: Path, keep: int = 10, today: str | None = None) -> Path | None:
    """Copy the config into a "backups" folder beside it, at most once per day.

    The copy is made before the first save of the day, so it holds yesterday's
    presets: a bad edit or a deleted preset can be undone by copying a backup back.
    Only the newest `keep` backups are kept. Returns the new file, or None when
    nothing was copied (already backed up today, no config yet, or a disk error).
    """
    if not path.is_file():
        return None
    folder = path.parent / "backups"
    stamp = today or time.strftime("%Y%m%d")
    target = folder / f"{path.stem}-{stamp}{path.suffix}"
    if target.exists():
        return None
    try:
        folder.mkdir(exist_ok=True)
        shutil.copy2(path, target)
        for old in sorted(folder.glob(f"{path.stem}-*{path.suffix}"))[:-keep]:
            old.unlink(missing_ok=True)
    except OSError:
        return None
    return target


def save(config: AppConfig, path: Path | None = None) -> None:
    """Write the config atomically (temp file, then rename) so a crash cannot truncate it."""
    path = path or default_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    backup(path)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".doomdeck-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(config.to_dict(), handle, indent=2)
            handle.write("\n")
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
