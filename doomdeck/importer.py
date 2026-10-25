"""Making presets out of things the user already has: a pasted command line, or files
dropped onto the window. No Qt here, so it is all unit tested.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import osinfo
from .library import LOADABLE, _collect_files
from .models import AppConfig, Preset

# Switches that mean the same as a checkbox in the preset.
_FLAGS = {
    "-nomonsters": "no_monsters",
    "-fast": "fast",
    "-respawn": "respawn",
    "-nosound": "no_sound",
    "-nomusic": "no_music",
}
# Switches whose value DoomDeck sets itself (per-preset data folders, saves, demos),
# so the pasted value is dropped instead of fighting with the preset's own.
_DROPPED_WITH_VALUE = {"-config", "-savedir", "-shotdir", "-loadgame", "-playdemo", "-record"}
_WRAPPERS = {"gamemoderun", "mangohud", "taskset", "nice", "env"}


@dataclass
class ParsedCommand:
    """What could be understood from a pasted Doom command line."""

    wrapper: str = ""
    engine: str = ""
    iwad: str = ""
    files: list[str] = field(default_factory=list)
    skill: int = 0
    warp: str = ""
    width: int = 0
    height: int = 0
    flags: dict[str, bool] = field(default_factory=dict)
    extra: list[str] = field(default_factory=list)  # anything not understood, kept as-is


def _is_switch(token: str) -> bool:
    return token.startswith("-") or token.startswith("+")


def parse_command(text: str) -> ParsedCommand:
    """Understand a command line such as
    `uzdoom -iwad doom2.wad -file a.wad b.pk3 -deh x.deh -skill 4 +map MAP05`.

    Line continuations (a trailing backslash) and quotes work like in a shell. A
    `-warp 5` becomes MAP05 and `-warp 1 2` becomes E1M2. Switches that DoomDeck
    manages itself (config, save folder...) are dropped; unknown ones go to `extra`.
    """
    tokens = osinfo.split_args(text)
    parsed = ParsedCommand()
    i = 0
    wrappers: list[str] = []
    while i < len(tokens) and not _is_switch(tokens[i]):
        if Path(tokens[i]).stem.lower() in _WRAPPERS:
            wrappers.append(tokens[i])
        else:
            parsed.engine = tokens[i]
            i += 1
            break
        i += 1
    parsed.wrapper = " ".join(wrappers)

    def values(start: int) -> tuple[list[str], int]:
        """Tokens after a switch up to the next switch, and the new position."""
        end = start
        while end < len(tokens) and not _is_switch(tokens[end]):
            end += 1
        return tokens[start:end], end

    while i < len(tokens):
        token = tokens[i]
        key = token.lower()
        if key.startswith("--"):
            key = key[1:]
        i += 1
        if key == "-iwad":
            vals, i = values(i)
            if vals:
                parsed.iwad = vals[0]
        elif key in ("-file", "-deh", "-bex"):
            vals, i = values(i)
            parsed.files += [v for v in vals if v not in parsed.files]
        elif key == "-skill":
            vals, i = values(i)
            if vals and vals[0].isdigit():
                parsed.skill = max(0, min(5, int(vals[0])))
        elif key == "+map":
            vals, i = values(i)
            if vals:
                parsed.warp = vals[0].upper()
        elif key == "-warp":
            vals, i = values(i)
            nums = [v for v in vals if v.isdigit()][:2]
            if len(nums) == 1:
                parsed.warp = f"MAP{int(nums[0]):02d}"
            elif len(nums) == 2:
                parsed.warp = f"E{int(nums[0])}M{int(nums[1])}"
        elif key in ("-width", "-height"):
            vals, i = values(i)
            if vals and vals[0].isdigit():
                setattr(parsed, key[1:], int(vals[0]))
        elif key in _FLAGS:
            parsed.flags[_FLAGS[key]] = True
        elif key in _DROPPED_WITH_VALUE:
            _vals, i = values(i)
        else:
            vals, i = values(i)
            parsed.extra += [token, *vals]
    return parsed


def build_preset(
    parsed: ParsedCommand, name: str, config: AppConfig, iwad_names: list[str]
) -> Preset:
    """Turn a parsed command into a preset, matching the engine and IWAD to known ones.

    The engine is the configured one with the same path (or the same program name);
    otherwise the first. The IWAD becomes a plain file name when it exists in the IWAD
    folder, else the path as typed.
    """
    engine = config.engines[0].name if config.engines else ""
    wanted = Path(parsed.engine).name.lower() if parsed.engine else ""
    for candidate in config.engines:
        if parsed.engine and (
            candidate.path == parsed.engine or Path(candidate.path).name.lower() == wanted
        ):
            engine = candidate.name
            break
    iwad = ""
    if parsed.iwad:
        base = Path(parsed.iwad).name
        lookup = {n.lower(): n for n in iwad_names}
        iwad = lookup.get(base.lower(), parsed.iwad if Path(parsed.iwad).is_absolute() else base)
    preset = Preset(
        name=name, engine=engine, iwad=iwad, files=list(parsed.files), skill=parsed.skill,
        warp=parsed.warp, width=parsed.width, height=parsed.height,
        wrapper=parsed.wrapper,
        extra_args=" ".join(osinfo.quote_arg(part) for part in parsed.extra),
    )
    for flag, value in parsed.flags.items():
        setattr(preset, flag, value)
    return preset


def expand_dropped(paths: list[str]) -> list[str]:
    """Files to use for dropped paths: loadable files as they are, folders expanded
    to the loadable files inside them. Duplicates are removed, order is kept."""
    found: list[str] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            candidates = [str(entry.path) for entry in _collect_files(path)]
        elif path.is_file() and path.suffix.lower() in LOADABLE:
            candidates = [str(path)]
        else:
            candidates = []
        found += [c for c in candidates if c not in found]
    return found
