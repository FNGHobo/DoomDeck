"""Listing the files a preset has produced: saves, demos and screenshots."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

SAVE_EXTENSIONS = {".zds", ".dsg", ".sav"}
DEMO_EXTENSIONS = {".lmp", ".cld"}
SHOT_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".pcx"}


@dataclass
class UserFile:
    """One save/demo/screenshot with the data the tabs display."""

    path: Path
    modified: float  # unix time
    size: int


def list_files(folder: Path, extensions: set[str]) -> list[UserFile]:
    """All files with the given extensions under folder (recursive), newest first.

    A missing folder gives an empty list.
    """
    results: list[UserFile] = []
    try:
        for path in folder.rglob("*"):
            if path.is_file() and path.suffix.lower() in extensions:
                stat = path.stat()
                results.append(UserFile(path, stat.st_mtime, stat.st_size))
    except OSError:
        return []
    return sorted(results, key=lambda f: f.modified, reverse=True)


def list_saves(folder: Path) -> list[UserFile]:
    return list_files(folder, SAVE_EXTENSIONS)


def list_demos(folder: Path) -> list[UserFile]:
    return list_files(folder, DEMO_EXTENSIONS)


def list_screenshots(folder: Path) -> list[UserFile]:
    return list_files(folder, SHOT_EXTENSIONS)


def latest_save(folder: Path) -> UserFile | None:
    """The most recently written save in a folder, or None (used by "Continue")."""
    saves = list_saves(folder)
    return saves[0] if saves else None
