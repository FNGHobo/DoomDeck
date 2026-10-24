"""Installing and updating source ports from their GitHub releases.

An engine entry can name a GitHub repository (`Engine.update_repo`, e.g. UZDoom/UZDoom).
This module finds that repository's newest release, picks the Linux downloads out of
its assets, downloads one, unpacks it into DoomDeck's own `engines/<version>/` folder
and finds the program inside. Old versions are left alone, so an update never breaks
presets, and a bad release can be rolled back by choosing the older engine entry.

Nothing here touches Qt.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import tarfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import netio
from .models import safe_name

RELEASES_URL = "https://api.github.com/repos/{repo}/releases"
# Program names looked for inside an unpacked release, best first.
PROGRAM_NAMES = ("uzdoom", "gzdoom", "lzdoom", "qzdoom", "zdoom", "zandronum", "dsda-doom", "woof")
DEFAULT_REPO = "UZDoom/UZDoom"


@dataclass
class Asset:
    name: str
    url: str
    size: int = 0


@dataclass
class Release:
    tag: str
    name: str = ""
    url: str = ""
    published: str = ""
    prerelease: bool = False
    assets: list[Asset] = field(default_factory=list)


def parse_release(data: dict) -> Release:
    """Build a Release from GitHub's JSON for one release."""
    return Release(
        tag=str(data.get("tag_name") or ""),
        name=str(data.get("name") or data.get("tag_name") or ""),
        url=str(data.get("html_url") or ""),
        published=str(data.get("published_at") or "")[:10],
        prerelease=bool(data.get("prerelease")),
        assets=[
            Asset(str(a.get("name") or ""), str(a.get("browser_download_url") or ""), int(a.get("size") or 0))
            for a in data.get("assets", []) if isinstance(a, dict)
        ],
    )


def latest_release(repo: str, include_prereleases: bool = False,
                   fetch: Callable[[str], object] | None = None) -> Release:
    """The newest release of a repository (skipping pre-releases unless asked)."""
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo or ""):
        raise netio.NetError("the update source must look like owner/repository")
    data = (fetch or netio.get_json)(RELEASES_URL.format(repo=repo))
    if not isinstance(data, list):
        raise netio.NetError(f"{repo} did not return a list of releases")
    for entry in data:
        if isinstance(entry, dict) and not entry.get("draft") and (include_prereleases or not entry.get("prerelease")):
            return parse_release(entry)
    raise netio.NetError(f"{repo} has no releases")


_OTHER_OS = ("windows", "win32", "win64", "macos", "mac-", "osx", "darwin", ".exe", ".msi", ".dmg", ".pkg",
             ".deb", ".rpm", ".flatpak", ".snap", "src", "source", "-sha", ".sig", ".txt", ".sha256", ".asc")


def linux_assets(release: Release) -> list[Asset]:
    """The assets that look like a Linux download, best guess first.

    An AppImage is the easiest to run, then a tar/zip that says Linux. Anything that
    looks like Windows, macOS, a package for another distro, source code or a checksum
    is left out. The caller shows the list so a person can still pick by hand.
    """
    def score(asset: Asset) -> int:
        low = asset.name.lower()
        if any(word in low for word in _OTHER_OS) and "appimage" not in low:
            return -1
        points = 0
        if low.endswith(".appimage"):
            points += 10
        elif low.endswith((".tar.gz", ".tar.xz", ".tar.bz2", ".tgz", ".zip")):
            points += 5
        else:
            return -1
        if "linux" in low:
            points += 3
        if any(w in low for w in ("x86_64", "amd64", "x64")):
            points += 2
        if any(w in low for w in ("aarch64", "arm64", "armv")):
            points -= 6
        return points

    scored = [(score(a), a) for a in release.assets]
    return [a for s, a in sorted(scored, key=lambda t: -t[0]) if s > 0]


def version_key(tag: str) -> tuple:
    """Sortable form of a version tag: "v4.14.3" -> (4, 14, 3); non-numbers sort last."""
    parts = re.findall(r"\d+", tag)
    return tuple(int(p) for p in parts) if parts else (-1,)


def is_newer(installed: str, latest: str) -> bool:
    """True when `latest` is a later version than `installed` (unknown installed counts as older)."""
    if not latest:
        return False
    if not installed:
        return True
    if installed.strip().lower().lstrip("v") == latest.strip().lower().lstrip("v"):
        return False
    return version_key(latest) > version_key(installed)


def find_program(folder: str | Path, names: tuple[str, ...] = PROGRAM_NAMES) -> Path | None:
    """Find the engine program in an unpacked release: a file with a known name, or the
    only executable, or the only AppImage."""
    folder = Path(folder)
    files = [p for p in folder.rglob("*") if p.is_file()]
    by_name = {p.name.lower(): p for p in files}
    for name in names:
        if name in by_name:
            return by_name[name]
    appimages = [p for p in files if p.suffix.lower() == ".appimage"]
    if len(appimages) == 1:
        return appimages[0]
    runnable = [p for p in files if os.access(p, os.X_OK) and not p.suffix and p.stat().st_size > 100_000]
    return runnable[0] if len(runnable) == 1 else None


def _safe_tar_members(archive: tarfile.TarFile, dest: Path):
    root = dest.resolve()
    for member in archive.getmembers():
        target = (dest / member.name).resolve()
        if member.issym() or member.islnk() or member.isdev():
            continue
        if root == target or root in target.parents:
            yield member


def unpack(archive_path: str | Path, dest: str | Path) -> None:
    """Unpack a tar or zip into `dest`, skipping links, devices and paths that escape it."""
    archive_path, dest = Path(archive_path), Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    name = archive_path.name.lower()
    try:
        if name.endswith(".zip"):
            with zipfile.ZipFile(archive_path) as archive:
                root = dest.resolve()
                for info in archive.infolist():
                    target = (dest / info.filename).resolve()
                    if root == target or root in target.parents:
                        archive.extract(info, dest)
                        mode = (info.external_attr >> 16) & 0o777
                        if mode and not info.is_dir():
                            target.chmod(mode | stat.S_IRUSR)
        else:
            with tarfile.open(archive_path) as archive:
                members = list(_safe_tar_members(archive, dest))
                try:
                    archive.extractall(dest, members=members, filter="data")
                except TypeError:  # Python without extraction filters
                    archive.extractall(dest, members=members)
    except (tarfile.TarError, zipfile.BadZipFile, OSError) as error:
        raise netio.NetError(f"could not unpack {archive_path.name}: {error}") from error


def install_asset(asset: Asset, tag: str, engines_root: str | Path,
                  progress: netio.Progress | None = None,
                  cancelled: Callable[[], bool] | None = None) -> Path:
    """Download an asset and return the path of the engine program to run.

    Everything goes into <engines_root>/<tag>/. An AppImage is simply made executable;
    an archive is unpacked and the program searched for inside.
    """
    folder = Path(engines_root).expanduser() / safe_name(tag or "unknown")
    folder.mkdir(parents=True, exist_ok=True)
    archive = netio.download(asset.url, folder / safe_name(asset.name), progress, cancelled=cancelled)
    if archive.name.lower().endswith(".appimage"):
        archive.chmod(archive.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
        return archive
    unpack(archive, folder)
    archive.unlink(missing_ok=True)
    program = find_program(folder)
    if program is None:
        raise netio.NetError(f"unpacked {asset.name} but could not find the engine program in {folder}")
    program.chmod(program.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
    return program


def remove_install(folder: str | Path, engines_root: str | Path) -> bool:
    """Delete one installed version, only if it really lies inside the engines folder."""
    folder, root = Path(folder).resolve(), Path(engines_root).resolve()
    if root in folder.parents and folder.is_dir():
        shutil.rmtree(folder)
        return True
    return False
