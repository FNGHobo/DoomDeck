"""Compatibility warnings: things that will probably go wrong even though the command
itself is valid, such as Doom II maps on a Doom IWAD, or a ZScript mod on a port that
cannot run it.

Everything here returns plain strings; launch.check turns them into warnings. Nothing
touches Qt. The rules are deliberately cautious: a warning must be something a person
would want to know, never a guess that cries wolf on a working setup.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import wadinfo

# Words in an engine's name or file name that tell which family of source port it is.
_ZDOOM_WORDS = ("uzdoom", "gzdoom", "lzdoom", "qzdoom", "zdoom", "zandronum", "skulltag")
_BOOM_WORDS = ("dsda", "prboom", "woof", "nugget", "eternity", "odamex", "doomretro", "cherry")
_VANILLA_WORDS = ("chocolate", "crispy")

# IWADs by file name. "doom2" style IWADs start on MAP01; "doom" style on E1M1.
_DOOM2_IWADS = ("doom2", "tnt", "plutonia", "freedoom2", "hacx", "doom2f", "nerve", "blasphem")
_DOOM1_IWADS = ("doom", "doom1", "doomu", "freedoom1", "chex", "chex3", "rekkr")
_OTHER_GAMES = ("heretic", "hexen", "strife", "hexdd", "harmony")


def engine_family(name: str, path: str = "") -> str:
    """"zdoom", "boom", "vanilla" or "unknown" from an engine's name and program path."""
    text = f"{name} {Path(path).name}".lower()
    for family, words in (("zdoom", _ZDOOM_WORDS), ("boom", _BOOM_WORDS), ("vanilla", _VANILLA_WORDS)):
        if any(word in text for word in words):
            return family
    return "unknown"


def iwad_kind(iwad_name: str) -> str:
    """"doom2", "doom", "other" (Heretic, Hexen...) or "" for an IWAD we cannot place."""
    stem = Path(iwad_name).stem.lower()
    if any(stem.startswith(g) for g in _OTHER_GAMES):
        return "other"
    if stem in _DOOM2_IWADS or stem.startswith("freedoom2") or stem.startswith("hacx"):
        return "doom2"
    if stem in _DOOM1_IWADS or stem.startswith("freedoom1") or stem.startswith("chex"):
        return "doom"
    return ""


def map_style(map_name: str) -> str:
    """"doom2" for MAP01 style names, "doom" for E1M1 style, "" for anything else."""
    if re.fullmatch(r"MAP\d\d", map_name.upper()):
        return "doom2"
    if re.fullmatch(r"E\dM\d", map_name.upper()):
        return "doom"
    return ""


def check(
    *, engine_name: str, engine_path: str, iwad_name: str, files: list[str], warp: str = ""
) -> list[str]:
    """Warnings for one combination of engine, IWAD and files (existing files only)."""
    out: list[str] = []
    existing = [f for f in files if Path(f).expanduser().is_file()]
    family = engine_family(engine_name, engine_path)
    kind = iwad_kind(iwad_name)

    owners: dict[str, list[str]] = {}
    styles: set[str] = set()
    needs_zdoom: dict[str, list[str]] = {}
    for path in existing:
        name = Path(path).name
        maps = [m for m, _t in wadinfo.list_maps(path)]
        for map_name in maps:
            owners.setdefault(map_name, []).append(name)
            styles.add(map_style(map_name))
        for feature in wadinfo.file_features(path):
            # A PK3 itself is only a problem for the vanilla-style ports; Boom-family
            # ports differ in what they accept, so only ZDoom-only data is flagged there.
            if feature in wadinfo.ZDOOM_ONLY_FEATURES or (feature == "pk3" and family == "vanilla"):
                needs_zdoom.setdefault(name, []).append(feature)

    styles.discard("")
    if kind == "other":
        if styles:
            out.append(
                f"{iwad_name} is not a Doom IWAD; maps made for Doom will not load on it."
            )
    elif kind and styles and styles != {kind}:
        wanted = "Doom II" if "doom2" in styles else "Doom"
        have = "Doom II" if kind == "doom2" else "Doom"
        if len(styles) == 1:
            out.append(f"These maps are for {wanted} but the IWAD is {iwad_name} ({have}).")

    clashes = {m: names for m, names in owners.items() if len(set(names)) > 1}
    if clashes:
        shown = sorted(clashes, key=wadinfo._map_sort_key)
        first = shown[0]
        more = f" and {len(shown) - 1} more" if len(shown) > 1 else ""
        out.append(
            f"{' and '.join(sorted(set(clashes[first])))} both contain {first}{more}; "
            "the file loaded last replaces the other's maps."
        )

    if warp and owners and warp.upper() not in {m.upper() for m in owners}:
        out.append(f"Start map {warp} is not in any of the selected files.")

    if family in ("boom", "vanilla"):
        for name, features in needs_zdoom.items():
            pretty = ", ".join(sorted(set(f.upper() if f != "pk3" else "PK3" for f in features)))
            out.append(f"{name} uses {pretty}, which {engine_name} may not support (it needs a ZDoom-family engine).")
    return out
