"""Turning a preset into a command line, checking it, and starting the engine.

Nothing here imports Qt, so it can be tested without a display and reused from the
command line (`python -m doomdeck --print-command "My preset"`).
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from . import compat
from .library import is_patch
from .models import AppConfig, Engine, Preset, Settings, safe_name


@dataclass
class DataDirs:
    """The four folders a launch uses for engine config, saves, demos and screenshots."""

    config: Path
    saves: Path
    demos: Path
    screenshots: Path

    def all(self) -> list[Path]:
        return [self.config, self.saves, self.demos, self.screenshots]


@dataclass
class Problem:
    """Something wrong with a preset. level is "error" (cannot launch) or "warning"."""

    level: str
    text: str


def _folder(settings: Settings, folder_name: str, separate: bool, preset: Preset) -> Path:
    """data_root/<folder>[/<preset name>] depending on the 'separate' switch."""
    base = Path(settings.data_root).expanduser() / folder_name
    return base / safe_name(preset.name) if separate else base


def data_dirs(settings: Settings, preset: Preset) -> DataDirs:
    """Work out the data folders for a preset (does not create anything)."""
    return DataDirs(
        config=_folder(settings, settings.config_folder, settings.separate_config, preset),
        saves=_folder(settings, settings.save_folder, settings.separate_saves, preset),
        demos=_folder(settings, settings.demo_folder, settings.separate_demos, preset),
        screenshots=_folder(
            settings, settings.screenshot_folder, settings.separate_screenshots, preset
        ),
    )


def default_config_name(engine: Engine) -> str:
    """File name of a preset's default config: one per engine, so engines with
    different config formats never overwrite each other's settings."""
    return f"{engine.slug}.ini"


def config_dirs(settings: Settings, preset: Preset) -> list[Path]:
    """Folders that engine configs are picked from.

    The preset's own config folder comes first. When every preset has its own
    folder, the shared parent folder is searched too, so a config file placed there
    can be used by several presets.
    """
    own = data_dirs(settings, preset).config
    shared = Path(settings.data_root).expanduser() / settings.config_folder
    return [own] if own == shared else [own, shared]


def list_configs(settings: Settings, preset: Preset) -> list[Path]:
    """Every .ini file the preset can choose from, preset folder first.

    A name that exists in both folders is listed once, from the preset's own folder.
    """
    found: dict[str, Path] = {}
    for folder in config_dirs(settings, preset):
        try:
            names = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".ini")
        except OSError:
            continue
        for path in names:
            found.setdefault(path.name, path)
    return sorted(found.values(), key=lambda p: p.name.lower())


def config_file(settings: Settings, preset: Preset, engine: Engine) -> Path:
    """The engine config (.ini) this preset launches with.

    A config picked in the preset is looked up in the config folders; if it has
    gone missing the path in the preset's own folder is returned (the engine then
    writes a fresh file there). With no choice made, the default is used.
    """
    own = data_dirs(settings, preset).config
    if preset.config:
        for folder in config_dirs(settings, preset):
            if (folder / preset.config).is_file():
                return folder / preset.config
        return own / preset.config
    return own / default_config_name(engine)


def create_config(settings: Settings, preset: Preset, name: str, source: Path | None = None) -> Path:
    """Create a new engine config in the preset's own config folder and return it.

    With `source` the new file starts as a copy of it (keeping key bindings and video
    settings); without, it starts empty and the engine fills in its defaults on first
    run. ".ini" is added to the name when missing. Raises FileExistsError if there is
    already a file with that name and ValueError for an empty name.
    """
    cleaned = safe_name(name.strip())
    if not name.strip():
        raise ValueError("A config needs a name.")
    if not cleaned.lower().endswith(".ini"):
        cleaned += ".ini"
    target = data_dirs(settings, preset).config / cleaned
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError(f"{cleaned} already exists")
    if source is not None:
        shutil.copy2(source, target)
    else:
        target.write_text("", encoding="utf-8")
    return target


def engine_config_files(engine: Engine) -> list[Path]:
    """Config files the engine itself keeps outside DoomDeck, so one can be imported.

    Looks for *.ini in ~/.config/<name> (XDG_CONFIG_HOME is honoured) for the engine's
    name and program name, plus the usual port names, and next to the engine program
    (portable installs). Only files that exist are returned, without duplicates.
    """
    home = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    names = {engine.slug, Path(engine.path).name.lower(), "uzdoom", "gzdoom", "zandronum", "lzdoom"}
    folders = [Path(home) / name for name in sorted(names) if name] + [Path(engine.path).expanduser().parent]
    found: list[Path] = []
    for folder in folders:
        try:
            entries = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".ini")
        except OSError:
            continue
        found += [p for p in entries if p not in found]
    return found


def other_preset_configs(
    settings: Settings, presets: list[Preset], exclude: Preset | None = None
) -> list[tuple[Preset, Path]]:
    """The config files in every other preset's own config folder (name order)."""
    result: list[tuple[Preset, Path]] = []
    for preset in sorted(presets, key=lambda p: p.name.lower()):
        if preset is exclude:
            continue
        folder = data_dirs(settings, preset).config
        try:
            files = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".ini")
        except OSError:
            continue
        result += [(preset, path) for path in files]
    return result


def unique_config_name(settings: Settings, preset: Preset, wanted: str) -> str:
    """`wanted` (with .ini) or "<name> 2.ini", "<name> 3.ini"... if it is already taken
    in the preset's own config folder."""
    stem = Path(safe_name(wanted)).stem if wanted.lower().endswith(".ini") else safe_name(wanted)
    folder = data_dirs(settings, preset).config
    name, number = f"{stem}.ini", 2
    while (folder / name).exists():
        name, number = f"{stem} {number}.ini", number + 1
    return name


def resolve_iwad(settings: Settings, iwad: str) -> Path | None:
    """Turn a preset's IWAD value (file name or absolute path) into a path, or None."""
    if not iwad:
        return None
    candidate = Path(iwad).expanduser()
    if candidate.is_absolute():
        return candidate
    return Path(settings.iwads_dir).expanduser() / iwad


def active_files(config: AppConfig, preset: Preset) -> list[str]:
    """Files to load, in order: autoload files first, then the enabled preset files."""
    disabled = set(preset.disabled_files)
    files: list[str] = []
    if preset.use_autoload:
        files += [f for f in config.settings.autoload_files if f not in files]
    files += [f for f in preset.files if f not in disabled and f not in files]
    return files


def check(config: AppConfig, preset: Preset) -> list[Problem]:
    """List everything that would stop or spoil a launch of this preset."""
    problems: list[Problem] = []
    engine = config.engine_named(preset.engine)
    if engine is None:
        problems.append(Problem("error", "No engine is selected (or it was deleted)."))
    else:
        exe = Path(engine.path).expanduser()
        if not exe.is_file():
            problems.append(Problem("error", f"Engine file not found: {exe}"))
        elif not os.access(exe, os.X_OK):
            problems.append(
                Problem("error", f"Engine is not executable (chmod +x it): {exe}")
            )
    if preset.net_mode == "join" and not preset.net_address.strip():
        problems.append(Problem("error", "Multiplayer: enter the host's address to join."))
    if engine is not None and engine.config_flag and preset.config:
        if not config_file(config.settings, preset, engine).is_file():
            problems.append(Problem(
                "warning", f"Config {preset.config} not found; the engine will create a new one."
            ))
    iwad = resolve_iwad(config.settings, preset.iwad)
    if iwad is None:
        problems.append(Problem("error", "No IWAD selected."))
    elif not iwad.is_file():
        problems.append(Problem("error", f"IWAD not found: {iwad}"))
    names: dict[str, str] = {}
    for name in active_files(config, preset):
        if not Path(name).expanduser().is_file():
            problems.append(Problem("warning", f"File missing, will be skipped: {name}"))
        key = Path(name).name.lower()
        if key in names:
            problems.append(Problem(
                "warning", f"Two files are both called {Path(name).name}; the second may override the first."
            ))
        names[key] = name
    if iwad is not None and engine is not None:
        for text in compat.check(
            engine_name=engine.name, engine_path=engine.path, iwad_name=iwad.name,
            files=active_files(config, preset), warp=preset.warp,
        ):
            problems.append(Problem("warning", text))
    if preset.wrapper.strip() or config.settings.global_wrapper.strip():
        wrapper = shlex.split(preset.wrapper.strip() or config.settings.global_wrapper.strip())
        if wrapper and shutil.which(wrapper[0]) is None:
            problems.append(Problem("warning", f"Wrapper program not found: {wrapper[0]}"))
    return problems


def audit(config: AppConfig) -> list[tuple[Preset, list[Problem]]]:
    """Every preset that has at least one problem, with its problems (errors first)."""
    result = []
    for preset in config.presets:
        problems = sorted(check(config, preset), key=lambda x: x.level != "error")
        if problems:
            result.append((preset, problems))
    return result


def worst_level(problems: list[Problem]) -> str:
    """"error", "warning" or "" for a list of problems."""
    if any(x.level == "error" for x in problems):
        return "error"
    return "warning" if problems else ""


def net_args(preset: Preset) -> list[str]:
    """The multiplayer switches for a preset (ZDoom family), or [] for single player.

    Hosting gives `-host <players>` plus the game type; joining gives `-join <address>`
    (nothing at all while the address is empty, which check() reports as an error).
    A port is passed to both. The host chooses skill, map and rules, so the joining
    side only needs to load the same IWAD and files in the same order.
    """
    args: list[str] = []
    if preset.net_mode == "host":
        args += ["-host", str(max(2, preset.net_players))]
        if preset.net_game == "deathmatch":
            args.append("-deathmatch")
        elif preset.net_game == "altdeath":
            args.append("-altdeath")
    elif preset.net_mode == "join" and preset.net_address.strip():
        args += ["-join", preset.net_address.strip()]
    else:
        return []
    if preset.net_port > 0:
        args += ["-port", str(preset.net_port)]
    return args


def _split(text: str) -> list[str]:
    """Split a user-typed argument string the way a shell would (quotes allowed)."""
    try:
        return shlex.split(text)
    except ValueError:
        # Unbalanced quote: fall back to plain whitespace so the launch still works.
        return text.split()


def build_command(
    config: AppConfig,
    preset: Preset,
    *,
    map_override: str | None = None,
    load_save: str | None = None,
    play_demo: str | None = None,
    now: float | None = None,
) -> list[str]:
    """Build the full argument list (wrapper + engine + options) for a preset.

    map_override / load_save / play_demo replace the preset's own start behaviour for
    one launch (used by the Saves and Demos tabs). Missing files are skipped, so the
    result is always something the engine can start with; check() reports them.
    Raises ValueError if the preset has no usable engine.
    """
    engine = config.engine_named(preset.engine)
    if engine is None:
        raise ValueError("preset has no engine")
    settings = config.settings
    dirs = data_dirs(settings, preset)

    cmd: list[str] = []
    cmd += _split(preset.wrapper.strip() or settings.global_wrapper.strip())
    cmd.append(str(Path(engine.path).expanduser()))
    cmd += _split(engine.extra_args)

    iwad = resolve_iwad(settings, preset.iwad)
    if iwad:
        cmd += ["-iwad", str(iwad)]

    # Data folders. An empty flag means this engine has no such switch.
    if engine.config_flag:
        cmd += [engine.config_flag, str(config_file(settings, preset, engine))]
    if engine.save_flag:
        cmd += [engine.save_flag, str(dirs.saves)]
    if engine.screenshot_flag:
        cmd += [engine.screenshot_flag, str(dirs.screenshots)]

    # Content files. DeHackEd patches use -deh; everything else goes after -file.
    present = [f for f in active_files(config, preset) if Path(f).expanduser().is_file()]
    normal = [str(Path(f).expanduser()) for f in present if not is_patch(f)]
    patches = [str(Path(f).expanduser()) for f in present if is_patch(f)]
    if normal:
        cmd += ["-file", *normal]
    for patch in patches:
        cmd += ["-deh", patch]

    if preset.skill:
        cmd += ["-skill", str(preset.skill)]
    if not play_demo:  # demos are single player
        cmd += net_args(preset)
    if play_demo:
        cmd += ["-playdemo", play_demo]
    elif load_save:
        cmd += ["-loadgame", load_save]
    else:
        start_map = map_override or preset.warp
        if start_map:
            cmd += ["+map", start_map]
        if preset.record_demo:
            stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
            cmd += ["-record", str(dirs.demos / f"{stamp}.lmp")]

    for flag, enabled in (
        ("-nomonsters", preset.no_monsters),
        ("-fast", preset.fast),
        ("-respawn", preset.respawn),
        ("-nosound", preset.no_sound),
        ("-nomusic", preset.no_music),
    ):
        if enabled:
            cmd.append(flag)
    if preset.width > 0 and preset.height > 0:
        cmd += ["-width", str(preset.width), "-height", str(preset.height)]

    cmd += _split(preset.extra_args)
    return cmd


def prepare_dirs(config: AppConfig, preset: Preset) -> None:
    """Create the preset's data folders and seed its engine config on first use.

    Seeding copies Settings.seed_config into the preset's config folder only if the
    preset has no config file for that engine yet, so later changes are never lost.
    """
    dirs = data_dirs(config.settings, preset)
    for folder in dirs.all():
        folder.mkdir(parents=True, exist_ok=True)
    engine = config.engine_named(preset.engine)
    seed = config.settings.seed_config.strip()
    if engine and seed:
        target = config_file(config.settings, preset, engine)
        source = Path(seed).expanduser()
        if source.is_file() and not target.exists():
            shutil.copy2(source, target)


def launch(
    config: AppConfig,
    preset: Preset,
    log_dir: Path,
    *,
    detach: bool = False,
    **start_options,
) -> tuple[subprocess.Popen, list[str], Path]:
    """Start the engine and return (process, command, log file path).

    Output of the engine is written to <log_dir>/<preset>.log (overwritten each run)
    so the Output tab can show what went wrong. start_options are passed to
    build_command (map_override, load_save, play_demo). With detach=True the engine
    gets its own session, so it keeps running after DoomDeck (or the terminal it was
    started from) is closed.
    """
    prepare_dirs(config, preset)
    cmd = build_command(config, preset, **start_options)
    engine = config.engine_named(preset.engine)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{safe_name(preset.name)}.log"
    with open(log_path, "wb") as log:
        process = subprocess.Popen(
            cmd,
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=detach,
            # Run from the engine's own folder so it finds its bundled files.
            cwd=str(Path(engine.path).expanduser().parent) if engine else None,
        )
    preset.count_map_play(start_map_for(preset, **{
        k: v for k, v in start_options.items() if k in ("map_override", "load_save", "play_demo")
    }))
    return process, cmd, log_path


def start_map_for(
    preset: Preset, *, map_override: str | None = None, load_save: str | None = None,
    play_demo: str | None = None,
) -> str:
    """The map a launch starts on ("" when it starts normally, loads a save or plays a demo)."""
    if play_demo or load_save:
        return ""
    return (map_override or preset.warp or "").strip()


def record_session(preset: Preset, started: float, ended: float | None = None) -> None:
    """Update a preset's play statistics after a session ended."""
    ended = ended if ended is not None else time.time()
    preset.last_played = started
    preset.play_count += 1
    preset.play_seconds += max(0, int(ended - started))


def format_command(cmd: list[str]) -> str:
    """Join a command into a copy-pasteable shell line."""
    return " ".join(shlex.quote(part) for part in cmd)


def _exec_quote(arg: str) -> str:
    """Quote one argument for a .desktop file's Exec= line (freedesktop rules).

    The argument goes in double quotes with ", `, $ and \\ backslash-escaped; the
    backslashes are then doubled again because the value is itself an escaped string,
    and a literal % is written %% so it is not read as a field code.
    """
    for char in ("\\", '"', "`", "$"):
        arg = arg.replace(char, "\\" + char)
    return '"' + arg.replace("\\", "\\\\").replace("%", "%%") + '"'


def desktop_entry_text(preset: Preset, run_script: "Path | list[str]", icon: str, workdir: Path) -> str:
    """Contents of a launcher-menu entry that starts one preset directly.

    `run_script` is the program that starts DoomDeck, or a list of arguments when it
    takes some (such as `python -m doomdeck`).
    """
    program = [str(run_script)] if isinstance(run_script, (str, Path)) else [str(a) for a in run_script]
    exec_start = " ".join(_exec_quote(part) for part in program)
    name = " ".join(preset.name.split()) or "Doom"
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={name}\n"
        "GenericName=Doom preset\n"
        f"Comment=Play {name} with DoomDeck\n"
        f"Exec={exec_start} --launch {_exec_quote(preset.name)}\n"
        f"Path={workdir}\n"
        f"Icon={icon}\n"
        "Terminal=false\n"
        "Categories=Game;\n"
    )


def desktop_entry_path(preset: Preset, directory: Path | None = None) -> Path:
    """Where a preset's launcher-menu entry is written (the user's applications folder)."""
    if directory is None:
        data_home = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        directory = Path(data_home) / "applications"
    return directory / f"doomdeck-{safe_name(preset.name).replace(' ', '-').lower()}.desktop"


def write_desktop_entry(
    preset: Preset, run_script: "Path | list[str]", icon: str, workdir: Path, directory: Path | None = None
) -> Path:
    """Write the preset's launcher-menu entry and return its path."""
    target = desktop_entry_path(preset, directory)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(desktop_entry_text(preset, run_script, icon, workdir), encoding="utf-8")
    target.chmod(0o755)
    return target
