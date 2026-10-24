"""Data models for DoomDeck: engines, presets, global settings and the whole config.

These are plain dataclasses with no Qt dependency, so the command builder and the
scanner can be tested (and used from the command line) without a display.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, fields, asdict
from typing import Any

# Where the user's Doom files live by default. Every one of these is editable in
# the Settings dialog; they only exist so a first launch works without setup.
DEFAULT_DOOM_HOME = "/media/4TBSSD/Games/doom"


def _from_dict(cls, data: dict[str, Any]):
    """Build a dataclass from a dict, ignoring keys the class does not know about.

    This keeps old config files loadable after fields are added or removed.
    """
    known = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in known})


def parse_tags(text: str) -> list[str]:
    """Split "co-op, Brutal; slaughter" into clean tags.

    Tags are separated by commas or semicolons, trimmed, and a tag repeated with
    different capitalisation is kept once (first spelling wins).
    """
    tags: list[str] = []
    seen: set[str] = set()
    for part in re.split(r"[,;]", text):
        tag = " ".join(part.split())
        if tag and tag.lower() not in seen:
            seen.add(tag.lower())
            tags.append(tag)
    return tags


def safe_name(name: str) -> str:
    """Turn a preset or engine name into something safe to use as a folder/file name."""
    cleaned = re.sub(r'[\\/:*?"<>|]+', "_", name).strip().strip(".")
    return cleaned or "unnamed"


@dataclass
class Engine:
    """One Doom source port the user can launch with.

    The three *_flag fields are the command-line switches this engine uses for its
    config file, save folder and screenshot folder. They default to the ZDoom-family
    spelling (UZDoom, GZDoom, Zandronum) but are editable per engine, and an empty
    value means "do not pass anything for this".
    """

    name: str
    path: str
    extra_args: str = ""
    config_flag: str = "-config"
    save_flag: str = "-savedir"
    screenshot_flag: str = "-shotdir"
    # Which release this engine is (set when DoomDeck installs it, or typed by hand) and
    # the GitHub "owner/repo" its releases come from, used to look for a newer one.
    version: str = ""
    update_repo: str = ""

    @property
    def slug(self) -> str:
        """Short lowercase name used for this engine's per-preset config file name."""
        return safe_name(self.name).lower().replace(" ", "-")


@dataclass
class Preset:
    """A saved combination: which engine, which IWAD, which files, and launch options."""

    name: str
    engine: str = ""
    iwad: str = ""  # file name inside the IWAD folder, or an absolute path
    files: list[str] = field(default_factory=list)  # absolute paths, in load order
    # Engine config file (.ini) this preset uses, by file name, picked from the
    # config folders. Empty means the default "<engine>.ini" in the preset's folder.
    config: str = ""
    warp: str = ""  # map name such as MAP01 or E1M1; empty means "start normally"
    skill: int = 0  # 0 = engine default, otherwise 1 (easiest) to 5 (nightmare)
    record_demo: bool = False
    extra_args: str = ""

    # Files in `files` that are switched off. They stay in the list (so the load
    # order is remembered) but are not passed to the engine.
    disabled_files: list[str] = field(default_factory=list)
    notes: str = ""
    # Program placed in front of the engine, e.g. "gamemoderun" or "mangohud".
    wrapper: str = ""
    use_autoload: bool = True  # include the global autoload files
    no_monsters: bool = False
    fast: bool = False
    respawn: bool = False
    no_sound: bool = False
    no_music: bool = False
    width: int = 0  # 0 = let the engine decide
    height: int = 0
    favorite: bool = False
    # Picture shown for this preset: a small PNG copy kept in DoomDeck's own
    # "pictures" folder (so deleting the original screenshot does not break it).
    picture: str = ""
    # Free-form labels used to group and filter presets ("co-op", "slaughter"...).
    tags: list[str] = field(default_factory=list)

    # Multiplayer, using the ZDoom-family switches. net_mode is "" (single player),
    # "host" or "join". Players and game type only matter when hosting, the address
    # only when joining. A port of 0 leaves the engine's default port in place.
    net_mode: str = ""
    net_players: int = 2
    net_game: str = ""  # "" = co-op, "deathmatch" or "altdeath"
    net_address: str = ""
    net_port: int = 0

    # Play statistics, updated by the launcher.
    last_played: float = 0.0  # unix time
    play_count: int = 0
    play_seconds: int = 0
    # Progress through the maps: names ticked off as finished, and how many times each
    # map was started from DoomDeck (a start map, or "Play this map" in the Maps tab).
    maps_done: list[str] = field(default_factory=list)
    map_plays: dict[str, int] = field(default_factory=dict)

    def matches(self, query: str) -> bool:
        """True if the search text appears in the name, notes or any file name."""
        query = query.strip().lower()
        if not query:
            return True
        haystack = [self.name, self.notes, *self.tags, *(f.rsplit("/", 1)[-1] for f in self.files)]
        return any(query in text.lower() for text in haystack)

    def has_tag(self, tag: str) -> bool:
        """True if the preset carries this tag (capitalisation ignored)."""
        return tag.lower() in (t.lower() for t in self.tags)

    def map_is_done(self, name: str) -> bool:
        return name.upper() in {m.upper() for m in self.maps_done}

    def set_map_done(self, name: str, done: bool) -> None:
        """Tick a map off (or back on) the finished list; the list keeps first-finished order."""
        key = name.upper()
        kept = [m for m in self.maps_done if m.upper() != key]
        self.maps_done = kept + [key] if done else kept

    def count_map_play(self, name: str) -> None:
        if name:
            key = name.upper()
            self.map_plays[key] = self.map_plays.get(key, 0) + 1


@dataclass
class Settings:
    """Global folders and behaviour that apply to every preset."""

    wads_root: str = f"{DEFAULT_DOOM_HOME}/wads"
    iwads_dir: str = f"{DEFAULT_DOOM_HOME}/iwads"
    data_root: str = f"{DEFAULT_DOOM_HOME}/Doomdeck/data"

    # Folder names under data_root. Defaults match the existing DoomRunner layout.
    config_folder: str = "Config"
    save_folder: str = "Save"
    demo_folder: str = "Demo"
    screenshot_folder: str = "Screenshot"

    # When true, each preset gets its own subfolder (named after the preset) inside
    # the matching folder above; when false, every preset shares the folder itself.
    separate_config: bool = True
    separate_saves: bool = True
    separate_demos: bool = True
    separate_screenshots: bool = True

    # Optional engine config file copied into a preset's config folder the first
    # time that preset launches, so new presets keep your keybinds and video setup.
    seed_config: str = ""

    # Files loaded before the preset's own files on every launch (HUD, music...).
    autoload_files: list[str] = field(default_factory=list)

    # Program placed in front of the engine on every launch (a preset can override).
    global_wrapper: str = ""

    # Name of the first-level wads folder that holds map sets. The Mapsets tab lists
    # the projects inside it (the other categories are mods, music and so on).
    mapsets_category: str = "maps"

    # Quit DoomDeck right after the engine has been started.
    close_on_launch: bool = False

    # Extra folders shown in the library as if they were inside the wads folder.
    extra_folders: list[str] = field(default_factory=list)

    # Names of folders hidden from the library (nothing on disk is touched).
    hidden_categories: list[str] = field(default_factory=list)


def sort_presets(presets: list[Preset], mode: str, reverse: bool = False) -> list[Preset]:
    """Sort presets for the list. Name sorts A-Z; the play-based modes put the most
    recent / most played first; `reverse` flips whichever order the mode gives."""
    if mode == "Last played":
        ordered = sorted(presets, key=lambda p: p.last_played, reverse=True)
    elif mode == "Most played":
        ordered = sorted(presets, key=lambda p: p.play_seconds, reverse=True)
    elif mode == "Times launched":
        ordered = sorted(presets, key=lambda p: p.play_count, reverse=True)
    elif mode == "Favorites first":
        ordered = sorted(presets, key=lambda p: (not p.favorite, p.name.lower()))
    else:
        ordered = sorted(presets, key=lambda p: p.name.lower())
    return ordered[::-1] if reverse else ordered


@dataclass
class AppConfig:
    """Everything that gets saved to the JSON config file."""

    settings: Settings = field(default_factory=Settings)
    engines: list[Engine] = field(default_factory=list)
    presets: list[Preset] = field(default_factory=list)
    last_preset: str = ""

    # Per-project extras for the library tree, keyed "category/project":
    # {"favorite": bool, "rating": 0-5, "note": str}.
    library_meta: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to plain JSON-compatible data."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppConfig":
        """Rebuild a config from loaded JSON, tolerating missing or extra keys."""
        settings_data = data.get("settings", {})
        engines = [_from_dict(Engine, e) for e in data.get("engines", [])]
        # Older configs had a global "extra arguments" box next to each engine's own.
        # They did the same job, so the global text moves into every engine's arguments.
        legacy = str(settings_data.get("global_args", "")).strip()
        if legacy:
            for engine in engines:
                if legacy not in engine.extra_args:
                    engine.extra_args = f"{engine.extra_args} {legacy}".strip()
        return cls(
            settings=_from_dict(Settings, settings_data),
            engines=engines,
            presets=[_from_dict(Preset, p) for p in data.get("presets", [])],
            last_preset=data.get("last_preset", ""),
            library_meta=dict(data.get("library_meta", {})),
        )

    def engine_named(self, name: str) -> Engine | None:
        """Find an engine by name, or None if it does not exist."""
        return next((e for e in self.engines if e.name == name), None)

    def preset_named(self, name: str) -> Preset | None:
        """Find a preset by name, or None if it does not exist."""
        return next((p for p in self.presets if p.name == name), None)
