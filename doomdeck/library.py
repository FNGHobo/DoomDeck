"""Scanning the sorted wads folder and the IWAD folder.

Expected layout (what sort-wads.sh produced):

    wads/
      maps/Eviternity/Eviternity.wad
      gameplay-mods/brutal doom/brutal_doom_v21.pk3
      ...

A first-level folder is a *category* if it contains only subfolders. If it holds
loadable files directly, it is treated as a *project* with no category, so an
unsorted leftover folder still shows up instead of disappearing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# File types the engines can load. .deh/.bex are DeHackEd patches passed with -deh.
LOADABLE = {".wad", ".pk3", ".pk7", ".ipk3", ".ipk7", ".zip", ".deh", ".bex"}
PATCH_EXTENSIONS = {".deh", ".bex"}
IWAD_EXTENSIONS = {".wad", ".ipk3", ".pk3"}

# How far below a project folder to look for files, to keep scans quick.
MAX_DEPTH = 4


@dataclass
class LibraryFile:
    """A loadable file inside a project."""

    path: Path
    rel: str  # path relative to the project folder, shown in the tree


@dataclass
class Project:
    """One project folder (a map set, a mod, a total conversion...)."""

    name: str
    path: Path
    files: list[LibraryFile] = field(default_factory=list)


@dataclass
class Category:
    """A first-level folder grouping projects, such as maps or gameplay-mods."""

    name: str
    projects: list[Project] = field(default_factory=list)


def is_patch(path: str | Path) -> bool:
    """True for DeHackEd patch files, which need -deh instead of -file."""
    return Path(path).suffix.lower() in PATCH_EXTENSIONS


def _visible_dirs(folder: Path) -> list[Path]:
    """Subfolders of a folder, sorted case-insensitively, skipping hidden ones."""
    try:
        with os.scandir(folder) as scan:
            entries = [Path(e.path) for e in scan if e.is_dir(follow_symlinks=False)]
    except OSError:
        return []
    return sorted((d for d in entries if not d.name.startswith(".")), key=lambda p: p.name.lower())


def _direct_loadable(folder: Path) -> bool:
    """True if the folder itself directly contains at least one loadable file."""
    try:
        with os.scandir(folder) as entries:
            return any(e.is_file() and Path(e.name).suffix.lower() in LOADABLE for e in entries)
    except OSError:
        return False


def _collect_files(project: Path) -> list[LibraryFile]:
    """All loadable files under a project folder, down to MAX_DEPTH, sorted by path."""
    found: list[LibraryFile] = []
    base_depth = len(project.parts)
    for current, dirs, names in os.walk(project):
        here = Path(current)
        if len(here.parts) - base_depth >= MAX_DEPTH:
            dirs[:] = []
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__MACOSX"]
        for name in names:
            if Path(name).suffix.lower() in LOADABLE:
                full = here / name
                found.append(LibraryFile(path=full, rel=str(full.relative_to(project))))
    return sorted(found, key=lambda f: f.rel.lower())


LOOSE_NAME = "(no category)"  # holds first-level folders that are projects themselves


def top_level_names(root: str | Path) -> list[str]:
    """Names of the folders directly inside the wads folder (what can be hidden)."""
    return [d.name for d in _visible_dirs(Path(root).expanduser())]


def apply_hidden(categories: list[Category], hidden) -> list[Category]:
    """Drop the folders the user chose to hide from a scanned library.

    `hidden` holds folder names. A name matches a category, or a project listed
    under "(no category)" (those are first-level folders that hold files directly).
    Categories left empty by this are dropped. The input list is not modified.
    """
    hidden = set(hidden)
    shown: list[Category] = []
    for category in categories:
        if category.name == LOOSE_NAME:
            projects = [p for p in category.projects if p.name not in hidden]
            if projects:
                shown.append(Category(category.name, projects))
        elif category.name not in hidden:
            shown.append(category)
    return shown


def scan_library(root: str | Path, extra_folders=()) -> list[Category]:
    """Scan the wads folder into categories of projects of files.

    `extra_folders` are additional folders (anywhere on disk) that are treated as if
    they sat directly inside the wads folder: each becomes a category of projects, or
    a single project if it holds loadable files itself. Missing or unreadable folders
    give no entries, never an exception.
    """
    root = Path(root).expanduser()
    categories: list[Category] = []
    loose = Category(name=LOOSE_NAME)
    extras = [Path(p).expanduser() for p in extra_folders if str(p).strip()]
    for first in _visible_dirs(root) + [p for p in extras if p.is_dir()]:
        if _direct_loadable(first):
            loose.projects.append(Project(first.name, first, _collect_files(first)))
            continue
        category = Category(name=first.name)
        for project_dir in _visible_dirs(first):
            category.projects.append(
                Project(project_dir.name, project_dir, _collect_files(project_dir))
            )
        categories.append(category)
    if loose.projects:
        categories.append(loose)
    return categories


def scan_iwads(folder: str | Path) -> list[Path]:
    """List the IWAD-like files directly inside the IWAD folder, sorted by name."""
    folder = Path(folder).expanduser()
    try:
        with os.scandir(folder) as scan:
            files = [
                Path(e.path)
                for e in scan
                if e.is_file() and Path(e.name).suffix.lower() in IWAD_EXTENSIONS
            ]
    except OSError:
        return []
    return sorted(files, key=lambda p: p.name.lower())


def find_moved(files, root: str | Path, extra_folders=()) -> dict[str, str]:
    """Look for files that no longer exist at their saved path, by file name.

    Sorting the wads folder moves files around, which breaks presets made earlier.
    For every path in `files` that is missing, this searches the library (hidden
    folders included) for a file with the same name, ignoring case. When several
    match, the one whose parent folder has the same name wins, then the first by
    path. Returns {old path: new path}; files that are present or not found are left
    out.
    """
    missing = [f for f in files if not Path(f).expanduser().is_file()]
    if not missing:
        return {}
    index: dict[str, list[Path]] = {}
    for category in scan_library(root, extra_folders):
        for project in category.projects:
            for entry in project.files:
                index.setdefault(entry.path.name.lower(), []).append(entry.path)
    found: dict[str, str] = {}
    for old in missing:
        options = index.get(Path(old).name.lower())
        if not options:
            continue
        parent = Path(old).parent.name.lower()
        options = sorted(options, key=lambda p: (p.parent.name.lower() != parent, str(p).lower()))
        found[old] = str(options[0])
    return found


# Load order by kind of content: assets first, then mods, then maps, so a map's own
# content wins over the generic mods. Matched against the first-level folder name
# (lower case, exact names first, then keywords). Unknown folders count as mods.
_ORDER_EXACT = {
    "audio": 0, "visuals": 1, "utilities": 2,
    "gameplay-mods": 3, "total-conversions": 3, "maps": 4, "needs-to-be-sorted": 3,
}
_ORDER_WORDS = (
    (("audio", "music", "sound"), 0),
    (("visual", "texture", "sprite", "hud", "graphic"), 1),
    (("util", "tool"), 2),
    (("map", "level", "episode"), 4),
)
_ORDER_DEFAULT = 3


def load_rank(path: str | Path, root: str | Path, extra_folders=()) -> int:
    """Where a file belongs in the load order (0 = first), from its library folder."""
    path = Path(path).expanduser()
    name = ""
    for base in [Path(root).expanduser(), *(Path(e).expanduser() for e in extra_folders)]:
        try:
            parts = path.relative_to(base).parts
        except ValueError:
            continue
        # A wads-folder file sits at <category>/<project>/file; an extra folder is its own category.
        name = parts[0].lower() if base == Path(root).expanduser() and len(parts) > 1 else base.name.lower()
        break
    if name in _ORDER_EXACT:
        return _ORDER_EXACT[name]
    for words, rank in _ORDER_WORDS:
        if any(w in name for w in words):
            return rank
    return _ORDER_DEFAULT


def sort_load_order(files: list[str], root: str | Path, extra_folders=()) -> list[str]:
    """Sort a load order by kind (resources, mods, maps), keeping the existing order
    inside each kind, so ties never shuffle files the user placed on purpose."""
    return sorted(files, key=lambda f: load_rank(f, root, extra_folders))
