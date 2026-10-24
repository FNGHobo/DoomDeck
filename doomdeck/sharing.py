"""Sharing presets: an export that carries file checksums, and checking a shared preset
against the files on this computer.

For multiplayer (and for "does my copy match yours?") names are not enough: two wads
called the same can differ, and a mismatch desyncs a game. The export therefore lists
every file with its size and SHA-256. The importer finds each file in the library by
name, compares checksums, and reports each one as matching, different or missing.

Nothing here touches Qt.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .models import Preset

FORMAT_VERSION = 1
_DIGEST_CACHE: dict[tuple[str, int, int], str] = {}


def file_digest(path: str | Path) -> str:
    """SHA-256 of a file as hex ("" if it cannot be read). Cached until the file changes."""
    path = Path(path)
    try:
        stat = path.stat()
        key = (str(path), stat.st_mtime_ns, stat.st_size)
        if key in _DIGEST_CACHE:
            return _DIGEST_CACHE[key]
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return ""
    _DIGEST_CACHE[key] = digest.hexdigest()
    return _DIGEST_CACHE[key]


def describe(path: str | Path) -> dict:
    """{"name", "size", "sha256"} for a file; size and checksum are empty if it is missing."""
    path = Path(path).expanduser()
    try:
        size = path.stat().st_size
    except OSError:
        return {"name": path.name, "size": 0, "sha256": ""}
    return {"name": path.name, "size": size, "sha256": file_digest(path)}


def export_data(preset: Preset, iwad_path: str | Path | None, engine_version: str = "") -> dict:
    """The JSON-ready export of a preset: its settings plus a checksum list.

    Picture and personal play statistics are left out, since they mean nothing elsewhere.
    """
    data = asdict(preset)
    for personal in ("picture", "last_played", "play_count", "play_seconds", "map_plays", "maps_done"):
        data.pop(personal, None)
    return {
        "doomdeck_preset": FORMAT_VERSION,
        "preset": data,
        "engine_version": engine_version,
        "iwad": describe(iwad_path) if iwad_path else {},
        "files": [describe(f) | {"enabled": f not in preset.disabled_files} for f in preset.files],
    }


def read_export(data: dict) -> tuple[dict, dict]:
    """Split an export into (preset dict, extras). Plain old exports (no checksums) work too."""
    if isinstance(data, dict) and "doomdeck_preset" in data and isinstance(data.get("preset"), dict):
        extras = {k: data.get(k) for k in ("engine_version", "iwad", "files")}
        return data["preset"], extras
    return data, {"engine_version": "", "iwad": {}, "files": []}


# ---- checking against the local library -------------------------------------------------------


@dataclass
class FileCheck:
    name: str
    status: str  # "ok", "different" or "missing"
    expected_size: int = 0
    expected_sha256: str = ""
    path: str = ""  # best local file found (for "different", the one that did not match)
    found_sha256: str = ""

    @property
    def symbol(self) -> str:
        return {"ok": "✔", "different": "≠", "missing": "✖"}[self.status]


def index_files(roots: list[str | Path]) -> dict[str, list[Path]]:
    """Map lower-case file name -> every file with that name under the given folders."""
    index: dict[str, list[Path]] = {}
    for root in roots:
        root = Path(root).expanduser()
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.is_file():
                index.setdefault(path.name.lower(), []).append(path)
    return index


def verify(entries: list[dict], index: dict[str, list[Path]]) -> list[FileCheck]:
    """Compare a shared file list with what is in the local library.

    A file matches when some local file of the same name has the same size and checksum.
    Entries without a checksum (an old export) match on name and size alone.
    """
    results = []
    for entry in entries:
        name = str(entry.get("name", ""))
        size, want = int(entry.get("size") or 0), str(entry.get("sha256") or "")
        candidates = index.get(name.lower(), [])
        if not candidates:
            results.append(FileCheck(name, "missing", size, want))
            continue
        chosen = None
        for path in candidates:
            try:
                same_size = not size or path.stat().st_size == size
            except OSError:
                continue
            if same_size and (not want or file_digest(path) == want):
                chosen = path
                break
        if chosen is not None:
            results.append(FileCheck(name, "ok", size, want, str(chosen), want))
        else:
            first = candidates[0]
            results.append(FileCheck(name, "different", size, want, str(first), file_digest(first)))
    return results


def verify_iwad(expected: dict, iwad_dir: str | Path) -> FileCheck | None:
    """Check the shared preset's IWAD against the local IWAD folder (None if not listed)."""
    if not expected or not expected.get("name"):
        return None
    index = index_files([iwad_dir])
    return verify([expected], index)[0]


@dataclass
class Relink:
    files: list[str]
    disabled: list[str]
    checks: list[FileCheck] = field(default_factory=list)

    @property
    def problems(self) -> list[FileCheck]:
        return [c for c in self.checks if c.status != "ok"]


def relink(preset_data: dict, extras: dict, index: dict[str, list[Path]]) -> Relink:
    """Point a shared preset's file list at the local copies of its files.

    Files that match (or differ) are replaced by the local path so the preset launches
    here; missing files keep their original path so the load order is preserved and the
    launcher's own check flags them.
    """
    shared = extras.get("files") or []
    original = list(preset_data.get("files", []))
    entries = shared or [{"name": Path(p).name, "size": 0, "sha256": ""} for p in original]
    checks = verify(entries, index)
    files, disabled = [], []
    off = set(preset_data.get("disabled_files", []))
    for i, check in enumerate(checks):
        old = original[i] if i < len(original) else check.name
        new = check.path or old
        files.append(new)
        enabled = shared[i].get("enabled", old not in off) if i < len(shared) else old not in off
        if not enabled:
            disabled.append(new)
    return Relink(files, disabled, checks)


def report(checks: list[FileCheck], iwad: FileCheck | None = None) -> str:
    """Readable summary of a verification (one line per problem)."""
    lines = []
    if iwad is not None:
        lines.append(f"IWAD {iwad.name}: " + {"ok": "matches", "different": "is a different version", "missing": "not found"}[iwad.status])
    ok = sum(1 for c in checks if c.status == "ok")
    lines.append(f"{ok} of {len(checks)} file(s) match.")
    for c in checks:
        if c.status == "missing":
            lines.append(f"  {c.symbol} {c.name}: not in your library")
        elif c.status == "different":
            lines.append(f"  {c.symbol} {c.name}: your copy is different from the shared one")
    return "\n".join(lines)
