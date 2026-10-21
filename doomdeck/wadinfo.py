"""Reading information out of WAD / PK3 files: which maps they contain, and readmes.

Only the small parts of a file needed are read (the WAD directory, or a few text
lumps), so scanning even a large megawad is fast. Every function swallows read
errors and returns an empty result: a broken file must never crash the launcher.
"""

from __future__ import annotations

import re
import struct
import zipfile
from io import BytesIO
from pathlib import Path
from typing import BinaryIO

# Map lump names: MAP01..MAP99 (Doom 2 style) and E1M1..E9M9 (Doom 1 style).
_MAP_NAME = re.compile(r"^(MAP\d\d|E\dM\d)$")

# Biggest embedded .wad (inside a pk3/zip) we are willing to load into memory.
_MAX_EMBEDDED_WAD = 200 * 1024 * 1024
# Biggest text lump (MAPINFO etc.) we will read.
_MAX_TEXT = 2 * 1024 * 1024

README_NAMES = ("readme", "read me", "read_me", "info", "about", "description", "credits")
README_EXTENSIONS = {".txt", ".md", ".nfo", ".diz"}


def _wad_lumps(handle: BinaryIO) -> list[tuple[str, int, int]]:
    """Return (name, offset, size) for each lump in a WAD directory; [] if not a WAD."""
    header = handle.read(12)
    if len(header) < 12 or header[:4] not in (b"IWAD", b"PWAD"):
        return []
    count, directory = struct.unpack("<ii", header[4:12])
    if count < 0 or count > 200_000 or directory < 12:
        return []
    handle.seek(directory)
    raw = handle.read(count * 16)
    lumps = []
    for i in range(len(raw) // 16):
        offset, size, name = struct.unpack("<ii8s", raw[i * 16 : i * 16 + 16])
        lumps.append((name.split(b"\0")[0].decode("ascii", "replace").upper(), offset, size))
    return lumps


def _maps_from_lumps(lumps: list[tuple[str, int, int]]) -> list[str]:
    """Map names found in a lump list, in file order, without duplicates."""
    seen: list[str] = []
    for name, _offset, _size in lumps:
        if _MAP_NAME.match(name) and name not in seen:
            seen.append(name)
    return seen


def _titles_from_text(text: str) -> dict[str, str]:
    """Pull map titles out of MAPINFO / UMAPINFO text where the format is simple.

    Handles `map MAP01 "Title"` (MAPINFO) and `map MAP01 { levelname = "Title" }`
    (UMAPINFO). Anything fancier is skipped; titles are only a nicety.
    """
    titles: dict[str, str] = {}
    for m in re.finditer(r'(?im)^\s*(?:defaultmap|map)\s+([A-Za-z0-9_]+)\s+"([^"]+)"', text):
        titles[m.group(1).upper()] = m.group(2)
    for m in re.finditer(
        r'(?is)\bmap\s+([A-Za-z0-9_]+)\s*\{(.*?)\}', text
    ):
        title = re.search(r'(?i)levelname\s*=\s*"([^"]+)"', m.group(2))
        if title:
            titles[m.group(1).upper()] = title.group(1)
    return titles


def _titles_from_wad(handle: BinaryIO, lumps: list[tuple[str, int, int]]) -> dict[str, str]:
    """Read titles from any MAPINFO/UMAPINFO lump inside a WAD."""
    titles: dict[str, str] = {}
    for name, offset, size in lumps:
        if name in ("MAPINFO", "UMAPINFO", "ZMAPINFO") and 0 < size <= _MAX_TEXT:
            handle.seek(offset)
            titles.update(_titles_from_text(handle.read(size).decode("latin-1")))
    return titles


# Results of list_maps, keyed by (path, modified time, size). Reading a big PK3 means
# opening the archive, so the window asks for the same file many times (every time
# a preset is selected or the load order changes) and must not redo that work.
_MAP_CACHE: dict[tuple[str, int, int], list[tuple[str, str]]] = {}


def list_maps(path: str | Path) -> list[tuple[str, str]]:
    """List (map name, title) for a WAD or PK3. Title is "" when unknown.

    PK3/ZIP files are searched for embedded `maps/*.wad` files plus any MAPINFO text.
    The result is empty for files that contain no maps (music packs, mods...).
    Answers are cached until the file's modified time or size changes.
    """
    path = Path(path)
    try:
        stat = path.stat()
    except OSError:
        return []
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    if key not in _MAP_CACHE:
        _MAP_CACHE[key] = _read_maps(path)
    return list(_MAP_CACHE[key])


def _read_maps(path: Path) -> list[tuple[str, str]]:
    """Open a WAD or PK3 and list its maps (the uncached work behind list_maps)."""
    names: list[str] = []
    titles: dict[str, str] = {}
    try:
        suffix = path.suffix.lower()
        if suffix == ".wad":
            with open(path, "rb") as handle:
                lumps = _wad_lumps(handle)
                names = _maps_from_lumps(lumps)
                titles = _titles_from_wad(handle, lumps)
        elif suffix in (".pk3", ".ipk3", ".pk7", ".ipk7", ".zip"):
            with zipfile.ZipFile(path) as archive:
                for info in archive.infolist():
                    low = info.filename.lower()
                    if low.endswith(".wad") and info.file_size <= _MAX_EMBEDDED_WAD:
                        # Only embedded wads in a maps/ folder are treated as levels.
                        if low.startswith("maps/") or "/maps/" in low:
                            inner = BytesIO(archive.read(info))
                            lumps = _wad_lumps(inner)
                            for n in _maps_from_lumps(lumps):
                                if n not in names:
                                    names.append(n)
                            titles.update(_titles_from_wad(inner, lumps))
                    elif Path(low).stem in ("mapinfo", "umapinfo", "zmapinfo") and (
                        info.file_size <= _MAX_TEXT
                    ):
                        titles.update(
                            _titles_from_text(archive.read(info).decode("latin-1"))
                        )
                        # MAPINFO can declare maps even when the map data lives elsewhere.
                        for n in titles:
                            if _MAP_NAME.match(n) and n not in names:
                                names.append(n)
    except (OSError, zipfile.BadZipFile, struct.error, ValueError, KeyError, RuntimeError):
        return []
    return [(n, titles.get(n, "")) for n in sorted(names, key=_map_sort_key)]


def _map_sort_key(name: str):
    """Sort MAP02 before MAP10 and E1M2 before E2M1 by comparing the numbers."""
    return [int(p) if p.isdigit() else p for p in re.findall(r"\d+|\D+", name)]


def find_readme(project: str | Path) -> Path | None:
    """Find the most likely readme/info text file for a project folder.

    Looks in the folder itself first, then one level down, preferring files whose
    name starts with a typical readme word, then any short text file.
    """
    project = Path(project)
    candidates: list[Path] = []
    try:
        for base in [project] + sorted(p for p in project.iterdir() if p.is_dir()):
            for entry in sorted(base.iterdir()):
                if entry.is_file() and entry.suffix.lower() in README_EXTENSIONS:
                    candidates.append(entry)
            if candidates:
                break
    except OSError:
        return None
    if not candidates:
        return None
    for entry in candidates:
        if entry.stem.lower().startswith(README_NAMES):
            return entry
    return candidates[0]


def read_text_file(path: str | Path, limit: int = 200_000) -> str:
    """Read a text file for display, tolerating odd encodings and huge files."""
    try:
        with open(path, "rb") as handle:
            data = handle.read(limit)
    except OSError as error:
        return f"(could not read file: {error})"
    return _decode_text(data)


def _decode_text(data: bytes) -> str:
    """Decode text of unknown encoding: UTF-8 first, then DOS code page, then latin-1."""
    for encoding in ("utf-8", "cp437"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


ARCHIVE_SUFFIXES = (".pk3", ".ipk3", ".pk7", ".ipk7", ".zip")


def find_archive_readme(path: str | Path) -> str | None:
    """Name of a readme text file inside a PK3/ZIP, or None.

    Only files in the archive root or one folder deep count, and only when the name
    starts with a typical readme word: other .txt files in a PK3 are usually game
    code (MAPINFO, DECORATE...) and would make a confusing "readme". Shallower files
    win, then alphabetical order.
    """
    path = Path(path)
    if path.suffix.lower() not in ARCHIVE_SUFFIXES:
        return None
    try:
        with zipfile.ZipFile(path) as archive:
            found: list[tuple[int, str]] = []
            for info in archive.infolist():
                if info.is_dir():
                    continue
                parts = info.filename.split("/")
                if len(parts) > 2:
                    continue
                entry = Path(parts[-1])
                if (
                    entry.suffix.lower() in README_EXTENSIONS
                    and entry.stem.lower().startswith(README_NAMES)
                ):
                    found.append((len(parts), info.filename))
    except (OSError, zipfile.BadZipFile, RuntimeError, ValueError):
        return None
    if not found:
        return None
    found.sort(key=lambda item: (item[0], item[1].lower()))
    return found[0][1]


def read_archive_text(path: str | Path, member: str, limit: int = 200_000) -> str:
    """Read one text file out of a PK3/ZIP for display."""
    try:
        with zipfile.ZipFile(path) as archive:
            with archive.open(member) as handle:
                data = handle.read(limit)
    except (OSError, zipfile.BadZipFile, KeyError, RuntimeError, ValueError) as error:
        return f"(could not read {member}: {error})"
    return _decode_text(data)


def human_size(size: int) -> str:
    """Format a byte count such as 1.4 MB."""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def file_summary(paths: list[str]) -> str:
    """Plain-text description of files: size, and how many maps each contains."""
    if not paths:
        return "No files in this preset yet."
    lines = []
    for raw in paths:
        path = Path(raw)
        try:
            size = path.stat().st_size
        except OSError:
            lines.append(f"{path.name}\n    (missing)  {path}")
            continue
        maps = list_maps(path)
        if maps:
            detail = f"{len(maps)} map{'s' if len(maps) != 1 else ''} ({maps[0][0]} to {maps[-1][0]})"
        else:
            detail = "no maps (resources or a mod)"
        lines.append(f"{path.name}\n    {human_size(size)}, {detail}\n    {path}")
    return "\n\n".join(lines)


def project_info(folder: str | Path, files: list[str], limit: int = 6000, max_files: int = 25) -> str:
    """Short description of a project for the library panes.

    The readme text file in the project folder if there is one, else a readme inside
    one of the PK3/ZIP files, else a list of the files with size and map count (the
    first `max_files` only, since every file has to be opened to count its maps).
    The text is cut at `limit` characters.
    """
    readme = find_readme(folder)
    if readme:
        text = read_text_file(readme)
    else:
        text = ""
        for path in files:
            member = find_archive_readme(path)
            if member:
                text = f"[{Path(path).name}: {member}]\n\n" + read_archive_text(path, member)
                break
        if not text:
            shown = files[:max_files]
            text = file_summary(shown)
            if len(files) > len(shown):
                text += f"\n\n... and {len(files) - len(shown)} more file(s)"
    text = text.strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "\n\n[... cut]"


def guess_iwad(files: list[str], iwads: list[Path]) -> str:
    """Guess which IWAD file name suits a set of files, or "" if unsure.

    Looks at the first file that contains maps: MAPxx maps mean Doom II, ExMy maps
    mean Doom (1). Then picks the IWAD in the list whose name matches.
    """
    kind = ""
    for path in files:
        maps = list_maps(path)
        if maps:
            kind = "doom2" if maps[0][0].startswith("MAP") else "doom"
            break
    if not kind:
        return ""
    for iwad in iwads:
        stem = iwad.stem.lower()
        if kind == "doom2" and stem == "doom2":
            return iwad.name
        if kind == "doom" and stem in ("doom", "doom1", "doomu"):
            return iwad.name
    return ""
