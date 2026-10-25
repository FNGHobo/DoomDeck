"""Compare the files a multiplayer server runs with your own, and fetch what is missing.

The server's list comes from serverquery (Zandronum and Odamex servers publish the name
and MD5 checksum of every file). This module then:

1. matches each file against the local library by name, confirming with the MD5;
2. for a missing or different file, looks for a download, trying in order
   * the download sites an Odamex server advertises (the server's own choice), then
   * the /idgames archive, searching by file name;
3. only keeps a download whose MD5 equals the server's (so a wrong version is never
   used), and unpacks it into the sorted wads folder;
4. builds the preset's file list in the server's load order.

A Zandronum server's website address is not downloaded from: it is a web page, not a
file, so it is only reported for the person to open.

Server-supplied names and addresses are untrusted: names must be plain file names,
addresses must be http(s), and nothing bigger than MAX_DOWNLOAD_BYTES is accepted.
No Qt here; every network call can be replaced for testing.
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
import urllib.parse
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import idgames, netio, sharing
from .serverquery import ServerFile, ServerInfo

MAX_DOWNLOAD_BYTES = 600 * 1024 * 1024
CONTENT_SUFFIXES = {".wad", ".pk3", ".pk7", ".pkz", ".ipk3", ".ipk7", ".deh", ".bex", ".zip", ".pke"}
MAX_IDGAMES_TRIES = 3

_md5_cache: dict[tuple[str, int, int], str] = {}


def md5_file(path: str | Path) -> str:
    """MD5 of a whole file (what Zandronum and Odamex servers publish), cached by size and time."""
    path = Path(path)
    stat = path.stat()
    key = (str(path), stat.st_size, int(stat.st_mtime))
    if key not in _md5_cache:
        digest = hashlib.md5()  # noqa: S324 - the servers' protocol uses MD5; not used for security
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        _md5_cache[key] = digest.hexdigest()
    return _md5_cache[key]


def safe_file_name(name: str) -> str:
    """The name if it is a plain file name with a Doom content extension, else ""."""
    name = name.strip()
    if not name or name != Path(name.replace("\\", "/")).name or name in (".", ".."):
        return ""
    if Path(name).suffix.lower() not in CONTENT_SUFFIXES - {".zip"}:
        return ""
    return name


# ---- comparing with the library ---------------------------------------------------------------


@dataclass
class Match:
    """One server file and what the library has for it."""

    name: str
    md5: str = ""
    optional: bool = False
    status: str = "missing"  # "ok", "different" or "missing"
    path: str = ""
    problem: str = ""  # set when the server's name cannot be used safely

    @property
    def symbol(self) -> str:
        return {"ok": "✔", "different": "≠", "missing": "✖"}[self.status]


def server_entries(info: ServerInfo) -> list[ServerFile]:
    """The server's files in load order: its wads, then its DeHackEd patches."""
    seen = {f.name.lower() for f in info.files}
    extra = [ServerFile(name=n) for n in info.patches if n.lower() not in seen]
    return [*info.files, *extra]


def compare(info: ServerInfo, index: dict[str, list[Path]]) -> list[Match]:
    """Match every file the server loads against the library (by name, then MD5)."""
    results: list[Match] = []
    for entry in server_entries(info):
        match = Match(entry.name, entry.md5.lower(), entry.optional)
        if not safe_file_name(entry.name):
            match.problem = "the server sent a file name DoomDeck will not use"
            results.append(match)
            continue
        candidates = index.get(entry.name.lower(), [])
        for path in candidates:
            try:
                same = not match.md5 or md5_file(path) == match.md5
            except OSError:
                continue
            if same:
                match.status, match.path = "ok", str(path)
                break
        else:
            if candidates:
                match.status, match.path = "different", str(candidates[0])
        results.append(match)
    return results


def needed(matches: list[Match], include_optional: bool = False) -> list[Match]:
    """The matches that must be fetched: not ok, usable, and required (or asked for)."""
    return [m for m in matches
            if m.status != "ok" and not m.problem and (include_optional or not m.optional)]


def iwad_name(info: ServerInfo, iwads_dir: str | Path) -> str:
    """The file in the IWAD folder that is the server's IWAD ("" if none, or none named)."""
    wanted = (info.iwad or "").strip().lower()
    if not wanted:
        return ""
    try:
        files = {p.name.lower(): p.name for p in Path(iwads_dir).expanduser().iterdir() if p.is_file()}
    except OSError:
        return ""
    for key in (wanted, Path(wanted).stem + ".wad"):
        if key in files:
            return files[key]
    return ""


def iwad_status(info: ServerInfo, iwads_dir: str | Path) -> str:
    """"" when the server's IWAD is in the IWAD folder (or unknown), else a sentence."""
    if not (info.iwad or "").strip() or iwad_name(info, iwads_dir):
        return ""
    return (f"The server uses the IWAD “{info.iwad}”, which is not in your IWAD folder "
            "(DoomDeck cannot download IWADs).")


def describe(info: ServerInfo, matches: list[Match], iwad_note: str = "", limit: int = 24) -> str:
    """A plain-text report of a server and how your files compare with it."""
    kind = {"zandronum": "Zandronum", "odamex": "Odamex"}.get(info.protocol, info.protocol)
    head = [f"{info.name or info.address} ({kind} {info.version}".rstrip() + ")"]
    bits = [b for b in (info.map_name, info.game_mode, f"{info.players}/{info.max_clients} players"
                        if info.max_clients else "") if b]
    if bits:
        head.append(" · ".join(bits))
    if info.password:
        head.append("This server asks for a password.")
    if iwad_note:
        head.append(iwad_note)
    lines = []
    for m in matches[:limit]:
        note = {"ok": "have it", "different": "you have a different version", "missing": "not in your library"}[m.status]
        if m.problem:
            note = m.problem
        lines.append(f"{m.symbol} {m.name}{' (optional)' if m.optional else ''} - {note}")
    if len(matches) > limit:
        lines.append(f"... and {len(matches) - limit} more")
    if not matches:
        lines.append("The server runs no extra files.")
    if info.website:
        lines.append(f"Server website: {info.website}")
    return "\n".join([*head, "", *lines])


# ---- downloading -------------------------------------------------------------------------------


@dataclass
class Fetched:
    """The result of fetching one file."""

    name: str
    ok: bool
    path: str = ""
    message: str = ""
    source: str = ""
    verified: bool = False


def _is_web_address(url: str) -> bool:
    return urllib.parse.urlparse(url).scheme in ("http", "https")


def candidate_urls(info: ServerInfo, name: str) -> list[str]:
    """Where an Odamex server says its files can be downloaded (site + file name)."""
    urls = []
    for base in info.download_urls:
        if _is_web_address(base):
            urls.append(base.rstrip("/") + "/" + urllib.parse.quote(name))
    return urls


def _find_in(files: list[Path], name: str, want: str) -> Path | None:
    """The file called `name` (any folder) among `files` whose MD5 is `want` (any if empty)."""
    for path in files:
        if path.name.lower() == name.lower() and (not want or md5_file(path) == want):
            return path
    return None


def _guarded(progress: Callable[[int, int], None] | None, cancelled: Callable[[], bool] | None):
    """Progress and cancel callables that also stop a download that grows past the limit."""
    state = {"stop": False}

    def on_progress(done: int, total: int) -> None:
        if done > MAX_DOWNLOAD_BYTES or total > MAX_DOWNLOAD_BYTES:
            state["stop"] = True
        if progress:
            progress(done, total)

    def stop() -> bool:
        return state["stop"] or bool(cancelled and cancelled())

    return on_progress, stop


def _install_direct(temp_file: Path, name: str, want: str, wads_root: Path) -> Fetched | None:
    """A downloaded file that is the wanted file itself or a zip that contains it."""
    folder = wads_root / "maps" / Path(name).stem
    if zipfile.is_zipfile(temp_file) and Path(name).suffix.lower() != ".zip":
        with tempfile.TemporaryDirectory(prefix="doomdeck-") as scratch:
            files = idgames.extract_archive(temp_file, Path(scratch) / "x")
            found = _find_in(files, name, want)
            if found is None:
                return None
            folder.mkdir(parents=True, exist_ok=True)
            idgames.extract_archive(temp_file, folder)
            return Fetched(name, True, str(folder / found.relative_to(Path(scratch) / "x")), verified=bool(want))
    if want and md5_file(temp_file) != want:
        return None
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name
    shutil.move(str(temp_file), target)
    return Fetched(name, True, str(target), verified=bool(want))


def fetch_file(info: ServerInfo, match: Match, wads_root: str | Path,
               progress: Callable[[int, int], None] | None = None,
               cancelled: Callable[[], bool] | None = None,
               download: Callable[..., Path] | None = None,
               search: Callable[..., list] | None = None,
               idgames_download: Callable[..., Path] | None = None) -> Fetched:
    """Download one missing file, verify it, and put it in the wads folder.

    The callables default to the real network code and exist so tests can replace them.
    """
    name, want = match.name, match.md5
    wads_root = Path(wads_root).expanduser()
    download = download or netio.download
    search = search or idgames.search
    idgames_download = idgames_download or idgames.download
    on_progress, stop = _guarded(progress, cancelled)
    notes: list[str] = []

    with tempfile.TemporaryDirectory(prefix="doomdeck-dl-") as scratch:
        scratch_dir = Path(scratch)
        for n, url in enumerate(candidate_urls(info, name)):
            try:
                temp = download(url, scratch_dir / f"direct{n}.bin", on_progress, cancelled=stop)
                installed = _install_direct(temp, name, want, wads_root)
            except (netio.NetError, idgames.IdgamesError, OSError, zipfile.BadZipFile) as error:
                notes.append(str(error))
                if cancelled and cancelled():
                    return Fetched(name, False, message="cancelled")
                continue
            if installed:
                installed.source = url
                return installed
            notes.append(f"{url} did not hold the right file")

        stem = Path(name).stem
        try:
            found = search(stem, kind="filename") if len(stem) >= 3 else []
        except idgames.IdgamesError as error:
            found = []
            notes.append(str(error))
        exact = [f for f in found if Path(f.filename).stem.lower() == stem.lower()]
        for entry in exact[:MAX_IDGAMES_TRIES]:
            if cancelled and cancelled():
                return Fetched(name, False, message="cancelled")
            try:
                archive = idgames_download(entry, scratch_dir / "idgames.zip", on_progress, cancelled=stop)
                with tempfile.TemporaryDirectory(prefix="doomdeck-x-") as probe:
                    contents = idgames.extract_archive(archive, Path(probe))
                    if _find_in(contents, name, want) is None:
                        notes.append(f"{entry.path} does not contain the right version")
                        continue
                result = idgames.install(entry, archive, wads_root)
            except (idgames.IdgamesError, netio.NetError, OSError) as error:
                notes.append(str(error))
                continue
            path = _find_in(result.files, name, want)
            if path is not None:
                return Fetched(name, True, str(path), source=f"/idgames {entry.path}", verified=bool(want))
    detail = notes[-1] if notes else "no download source found"
    return Fetched(name, False, message=detail)


@dataclass
class SyncReport:
    """What happened for every file during a fetch."""

    results: list[Fetched] = field(default_factory=list)

    @property
    def failed(self) -> list[Fetched]:
        return [r for r in self.results if not r.ok]


def fetch_missing(info: ServerInfo, matches: list[Match], wads_root: str | Path,
                  progress: Callable[[int, int], None] | None = None,
                  cancelled: Callable[[], bool] | None = None, include_optional: bool = False,
                  **callables) -> SyncReport:
    """Fetch every needed file; updates each Match in place when it succeeds."""
    report = SyncReport()
    for match in needed(matches, include_optional):
        if cancelled and cancelled():
            break
        result = fetch_file(info, match, wads_root, progress, cancelled, **callables)
        report.results.append(result)
        if result.ok:
            match.status, match.path = "ok", result.path
    return report


# ---- turning the result into a preset -----------------------------------------------------------


def load_order(matches: list[Match], include_optional: bool = False) -> tuple[list[str], list[str]]:
    """(paths in the server's load order, names that are still unavailable)."""
    paths, absent = [], []
    for match in matches:
        if match.optional and not include_optional and match.status != "ok":
            continue
        if match.status == "ok":
            paths.append(match.path)
        elif not match.optional:
            absent.append(match.name)
    return paths, absent


def library_index(settings) -> dict[str, list[Path]]:
    """Index of the wads folder and extra library folders by file name."""
    return sharing.index_files([settings.wads_root, *settings.extra_folders])
