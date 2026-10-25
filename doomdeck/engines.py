"""Installing and updating source ports from their GitHub releases.

An engine entry can name a GitHub repository (`Engine.update_repo`, e.g. UZDoom/UZDoom).
This module finds that repository's newest release, picks the downloads for this system
(Linux, Windows or macOS) out of its assets, downloads one, unpacks it into DoomDeck's own `engines/<version>/` folder
and finds the program inside. Old versions are left alone, so an update never breaks
presets, and a bad release can be rolled back by choosing the older engine entry.

Nothing here touches Qt.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import netio, osinfo
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


# Words in an asset's name that say which system it is for. Compared as whole words
# (split at anything that is not a letter or digit), so "darwin" does not look like "win".
_SYSTEM_WORDS = {
    osinfo.LINUX: {"linux", "appimage"},
    osinfo.WINDOWS: {"windows", "win", "win32", "win64", "w64", "msvc", "mingw"},
    osinfo.MACOS: {"macos", "mac", "macosx", "osx", "darwin", "apple", "dmg"},
}
# Never a build to run: source, checksums, signatures, other packaging.
_NOT_A_BUILD = ("src", "source", "sha", "sha256", "sig", "asc", "txt", "deb", "rpm", "flatpak", "snap",
                "msi", "pkg", "zsync", "debug", "symbols", "pdb", "headers")
_ARCHIVES = (".tar.gz", ".tar.xz", ".tar.bz2", ".tgz", ".zip")


def _words(name: str) -> set[str]:
    return {w for w in re.split(r"[^a-z0-9]+", name.lower()) if w}


def assets_for(release: Release, system: str | None = None, cpu: str | None = None) -> list[Asset]:
    """The assets that look like a download for a system, best guess first.

    Linux: an AppImage first, then a tar/zip that says Linux. Windows: a zip that says
    Windows (installers are left out, they cannot be unpacked into DoomDeck's folder).
    macOS: a .dmg, or a zip/tar that says macOS. Anything that names another system, is
    for another processor, or is source code, a checksum or a package for another distro
    is left out. A build that names no system at all is kept only for Linux, where a
    plain tar.gz is the usual case. The caller shows the list so a person can still pick
    by hand.
    """
    system = system or osinfo.current()
    cpu = osinfo.machine(cpu)
    mine = _SYSTEM_WORDS[system]
    others = set().union(*(w for key, w in _SYSTEM_WORDS.items() if key != system))

    def score(asset: Asset) -> int:
        low = asset.name.lower()
        words = _words(low)
        if words & others - mine or words & set(_NOT_A_BUILD) or low.endswith(".exe"):
            return -1
        points = 0
        if low.endswith(".appimage"):
            if system != osinfo.LINUX:
                return -1
            points += 10
        elif low.endswith(".dmg"):
            if system != osinfo.MACOS:
                return -1
            points += 10
        elif low.endswith(_ARCHIVES):
            points += 5
        else:
            return -1
        if words & mine:
            points += 3
        elif system != osinfo.LINUX:
            return -1  # on Windows and macOS an archive must say it is for that system
        built_for = ""
        if {"amd64", "x64"} & words or ("x86" in words and "64" in words):
            built_for = "x86_64"
        elif {"aarch64", "arm64", "armv8"} & words:
            built_for = "arm64"
        elif {"armv7", "armhf", "armv6", "i386", "i686"} & words:
            return -1
        if built_for == cpu:
            points += 2
        elif built_for:
            # An ARM Mac or Windows PC can run Intel builds (Rosetta / x64 emulation); nothing
            # else can run a build made for the other kind of processor.
            if cpu == "arm64" and system in (osinfo.MACOS, osinfo.WINDOWS):
                points -= 3
            else:
                return -1
        return points

    scored = [(score(a), a) for a in release.assets]
    return [a for s, a in sorted(scored, key=lambda t: -t[0]) if s > 0]


def linux_assets(release: Release) -> list[Asset]:
    """Kept for older callers: the Linux downloads of a release."""
    return assets_for(release, osinfo.LINUX)


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


def find_program(folder: str | Path, names: tuple[str, ...] = PROGRAM_NAMES,
                 system: str | None = None) -> Path | None:
    """Find the engine program in an unpacked release.

    A file with a known name wins ("uzdoom", or "uzdoom.exe" on Windows). On macOS an
    ``.app`` is the program (the .app folder itself is returned). Otherwise the only
    AppImage, or the only large executable, is taken.
    """
    system = system or osinfo.current()
    folder = Path(folder)
    if system == osinfo.MACOS:
        apps = sorted(p for p in folder.rglob("*.app") if p.is_dir()
                      and not any(part.lower().endswith(".app") for part in p.relative_to(folder).parts[:-1]))
        known = [a for a in apps if a.stem.lower() in names]
        if known or len(apps) == 1:
            return (known or apps)[0]
    files = [p for p in folder.rglob("*") if p.is_file()]
    by_name = {p.name.lower(): p for p in files}
    for name in osinfo.program_names(names, system) if system == osinfo.WINDOWS else names:
        if name in by_name:
            return by_name[name]
    appimages = [p for p in files if p.suffix.lower() == ".appimage"]
    if len(appimages) == 1:
        return appimages[0]
    if system == osinfo.WINDOWS:
        exes = [p for p in files if p.suffix.lower() == ".exe" and p.stat().st_size > 1_000_000
                and not p.name.lower().startswith(("unins", "vc_redist", "crash"))]
        return exes[0] if len(exes) == 1 else None
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


def _unzip_with_ditto(archive_path: Path, dest: Path) -> bool:
    """On macOS, unzip an archive holding an .app with Apple's `ditto`, which keeps the
    symbolic links and permissions an app bundle needs (Python's zipfile drops links).
    Returns True when that was done; anywhere else, or if ditto fails, False."""
    if osinfo.current() != osinfo.MACOS or shutil.which("ditto") is None:
        return False
    try:
        with zipfile.ZipFile(archive_path) as archive:
            if not any(".app/" in info.filename for info in archive.infolist()):
                return False
            root = dest.resolve()
            if any(root not in (dest / info.filename).resolve().parents for info in archive.infolist()
                   if not info.filename.endswith("/")):
                return False  # a path that escapes the folder: let the careful unpacker refuse it
        result = subprocess.run(["ditto", "-x", "-k", str(archive_path), str(dest)],
                                capture_output=True, timeout=600, check=False)
        return result.returncode == 0
    except (zipfile.BadZipFile, OSError, subprocess.SubprocessError):
        return False


def unpack(archive_path: str | Path, dest: str | Path) -> None:
    """Unpack a tar or zip into `dest`, skipping links, devices and paths that escape it."""
    archive_path, dest = Path(archive_path), Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    name = archive_path.name.lower()
    try:
        if name.endswith(".zip") and _unzip_with_ditto(archive_path, dest):
            return
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


def unpack_dmg(dmg_path: str | Path, dest: str | Path) -> None:
    """Copy the ``.app`` bundle(s) out of a macOS disk image into `dest` (macOS only).

    The image is mounted read-only without opening a Finder window, copied from, and
    always unmounted again, even when the copy fails.
    """
    if osinfo.current() != osinfo.MACOS:
        raise netio.NetError("a .dmg disk image can only be opened on macOS")
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    mount = Path(tempfile.mkdtemp(prefix="doomdeck-dmg-"))
    attached = False
    try:
        subprocess.run(
            ["hdiutil", "attach", "-nobrowse", "-readonly", "-noverify", "-noautoopen",
             "-mountpoint", str(mount), str(dmg_path)],
            check=True, capture_output=True, timeout=300,
        )
        attached = True
        apps = sorted(mount.glob("*.app"))
        if not apps:
            raise netio.NetError(f"{Path(dmg_path).name} does not contain an app")
        for app in apps:
            shutil.copytree(app, dest / app.name, symlinks=True, dirs_exist_ok=True)
    except (subprocess.SubprocessError, OSError, shutil.Error) as error:
        raise netio.NetError(f"could not open {Path(dmg_path).name}: {error}") from error
    finally:
        if attached:
            subprocess.run(["hdiutil", "detach", "-force", str(mount)], capture_output=True, timeout=120, check=False)
        shutil.rmtree(mount, ignore_errors=True)


def install_asset(asset: Asset, tag: str, engines_root: str | Path,
                  progress: netio.Progress | None = None,
                  cancelled: Callable[[], bool] | None = None) -> Path:
    """Download an asset and return the path of the engine program to run.

    Everything goes into <engines_root>/<tag>/. An AppImage is simply made executable;
    a macOS disk image has its .app copied out; an archive is unpacked and the program
    searched for inside. On macOS the returned path is the ``.app`` folder.
    """
    folder = Path(engines_root).expanduser() / safe_name(tag or "unknown")
    folder.mkdir(parents=True, exist_ok=True)
    archive = netio.download(asset.url, folder / safe_name(asset.name), progress, cancelled=cancelled)
    low = archive.name.lower()
    if low.endswith(".appimage"):
        osinfo.make_executable(archive)
        return archive
    if low.endswith(".dmg"):
        unpack_dmg(archive, folder)
    else:
        unpack(archive, folder)
    archive.unlink(missing_ok=True)
    program = find_program(folder)
    if program is None:
        raise netio.NetError(f"unpacked {asset.name} but could not find the engine program in {folder}")
    osinfo.make_executable(osinfo.resolve_program(program))
    return program


def remove_install(folder: str | Path, engines_root: str | Path) -> bool:
    """Delete one installed version, only if it really lies inside the engines folder."""
    folder, root = Path(folder).resolve(), Path(engines_root).resolve()
    if root in folder.parents and folder.is_dir():
        shutil.rmtree(folder)
        return True
    return False
