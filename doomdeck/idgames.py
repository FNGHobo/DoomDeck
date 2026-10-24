"""The Doomworld /idgames archive: searching it, downloading from a mirror, and putting
the files into the sorted wads folder.

The archive has a public web API (version 3) that answers with JSON. Searching and
listing the newest files go through `search` and `latest`; `get` gives one file in full.
Every function that talks to the network takes an optional `fetch` so the tests can
supply canned answers; the real default is netio.get_json.

Nothing here touches Qt.
"""

from __future__ import annotations

import re
import shutil
import urllib.parse
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import netio
from .models import safe_name

API_URL = "https://www.doomworld.com/idgames/api/api.php"
# Mirrors tried in order when downloading. Each holds the archive under this prefix.
MIRRORS = (
    "https://youfailit.net/pub/idgames/",
    "https://ftp.fu-berlin.de/pc/games/idgames/",
    "https://mirrors.lug.mtu.edu/idgames/",
)
SEARCH_FIELDS = {
    "Title": "title", "File name": "filename", "Author": "author",
    "Description": "description", "Text file": "textfile",
}
SORTS = {"Newest": ("date", "desc"), "Best rated": ("rating", "desc"), "Name": ("filename", "asc"),
         "Biggest": ("size", "desc")}
MAX_ARCHIVE_BYTES = 800 * 1024 * 1024  # refuse anything that would unpack to more than this


class IdgamesError(Exception):
    """The archive's API reported an error (or answered with something unusable)."""


@dataclass
class IdgamesFile:
    id: int = 0
    title: str = ""
    dir: str = ""  # e.g. "levels/doom2/megawads/" (empty for "latest" listings)
    filename: str = ""
    size: int = 0
    age: int = 0  # date added, seconds since 1970; used to notice a newer upload
    date: str = ""
    author: str = ""
    description: str = ""
    rating: float = 0.0
    votes: int = 0
    url: str = ""  # the file's page on Doomworld
    textfile: str = ""

    @property
    def path(self) -> str:
        return f"{self.dir}{self.filename}"

    @property
    def project_name(self) -> str:
        """Folder name for the project: the title, or the file name without extension."""
        return safe_name(self.title.strip() or Path(self.filename).stem)

    def download_urls(self, mirrors: tuple[str, ...] = MIRRORS) -> list[str]:
        return [m.rstrip("/") + "/" + urllib.parse.quote(self.path) for m in mirrors] if self.filename else []


def _as_int(value) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _as_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _file_from(data: dict) -> IdgamesFile:
    return IdgamesFile(
        id=_as_int(data.get("id")), title=str(data.get("title") or ""),
        dir=str(data.get("dir") or ""), filename=str(data.get("filename") or ""),
        size=_as_int(data.get("size")), age=_as_int(data.get("age")),
        date=str(data.get("date") or ""), author=str(data.get("author") or ""),
        description=str(data.get("description") or ""), rating=_as_float(data.get("rating")),
        votes=_as_int(data.get("votes")), url=str(data.get("url") or ""),
        textfile=str(data.get("textfile") or ""),
    )


def parse_response(payload) -> list[IdgamesFile]:
    """Turn an API answer (already decoded from JSON) into files.

    "No Results" style warnings give an empty list; a real error raises IdgamesError.
    The API uses a single object where a list of one would be expected, so both are handled.
    """
    if not isinstance(payload, dict):
        raise IdgamesError("the archive answered with something unexpected")
    error = payload.get("error")
    if error:
        if isinstance(error, dict):
            raise IdgamesError(f"{error.get('type', 'Error')}: {error.get('message', '')}".strip(": "))
        raise IdgamesError(str(error))
    content = payload.get("content")
    if not content:
        return []
    if isinstance(content, dict) and "file" in content:
        entries = content["file"]
    elif isinstance(content, dict) and "id" in content:
        entries = content
    elif isinstance(content, list):
        entries = content
    else:
        return []
    if isinstance(entries, dict):
        entries = [entries]
    return [_file_from(e) for e in entries if isinstance(e, dict)]


def _call(params: dict, fetch: Callable[[str], object] | None) -> list[IdgamesFile]:
    url = API_URL + "?" + urllib.parse.urlencode({**params, "out": "json"})
    try:
        payload = (fetch or netio.get_json)(url)
    except netio.NetError as error:
        raise IdgamesError(str(error)) from error
    return parse_response(payload)


def search(query: str, kind: str = "title", sort: str = "date", direction: str = "desc",
           fetch: Callable[[str], object] | None = None) -> list[IdgamesFile]:
    """Search the archive. The API needs at least three characters."""
    query = query.strip()
    if len(query) < 3:
        raise IdgamesError("type at least three characters to search")
    return _call({"action": "search", "query": query, "type": kind, "sort": sort, "dir": direction}, fetch)


def latest(limit: int = 30, fetch: Callable[[str], object] | None = None) -> list[IdgamesFile]:
    """The newest uploads (title, author, description and rating only; use `get` for more)."""
    return _call({"action": "latestfiles", "limit": max(1, min(limit, 100))}, fetch)


def get(file_id: int | str | None = None, path: str = "",
        fetch: Callable[[str], object] | None = None) -> IdgamesFile:
    """Full details of one file, by id or by its path in the archive."""
    if file_id:
        found = _call({"action": "get", "id": file_id}, fetch)
    elif path:
        found = _call({"action": "get", "file": path}, fetch)
    else:
        raise IdgamesError("need a file id or path")
    if not found:
        raise IdgamesError("that file is not in the archive")
    return found[0]


# ---- where a download belongs ------------------------------------------------------------------

# First folder under /idgames -> the sorted-library category it normally belongs to.
_CATEGORY_BY_TOP = {
    "levels": "maps", "deathmatch": "maps", "themes": "visuals", "graphics": "visuals",
    "sounds": "audio", "music": "audio", "utils": "utilities", "combos": "total-conversions",
}


def category_for(idgames_dir: str, mapsets_category: str = "maps") -> str:
    """Sorted-library folder for a file from this archive directory.

    levels/ and deathmatch/ files go to the mapsets folder; sounds and music to audio;
    graphics and themes to visuals; utils to utilities; combos to total-conversions;
    everything else to needs-to-be-sorted.
    """
    top = idgames_dir.strip("/").split("/", 1)[0].lower()
    category = _CATEGORY_BY_TOP.get(top, "needs-to-be-sorted")
    return mapsets_category if category == "maps" else category


def parse_page_link(text: str) -> tuple[str, str]:
    """Pick an id or archive path out of what a person pasted.

    Accepts a bare number, "idgames:123", or a Doomworld link such as
    https://www.doomworld.com/idgames/levels/doom2/megawads/foo or ...?id=123.
    Returns (id, path); either may be "".
    """
    text = text.strip()
    if re.fullmatch(r"(idgames:)?\d{1,7}", text):
        return text.split(":")[-1], ""
    parsed = urllib.parse.urlparse(text)
    query = urllib.parse.parse_qs(parsed.query)
    if "id" in query and query["id"][0].isdigit():
        return query["id"][0], ""
    if "doomworld.com" in parsed.netloc and "/idgames/" in parsed.path:
        rest = parsed.path.split("/idgames/", 1)[1].strip("/")
        if rest and not rest.startswith(("api", "random", "search")):
            return "", rest
    if re.match(r"(levels|themes|sounds|music|graphics|utils|combos|deathmatch)/", text):
        return "", text
    return "", ""


# ---- unpacking -------------------------------------------------------------------------------


@dataclass
class InstallResult:
    folder: Path
    files: list[Path] = field(default_factory=list)
    category: str = ""


def _safe_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    """Archive members that stay inside the destination (no ../ or absolute paths)."""
    out = []
    for info in archive.infolist():
        name = info.filename.replace("\\", "/")
        if info.is_dir() or name.startswith("/") or ".." in Path(name).parts:
            continue
        out.append(info)
    return out


def extract_archive(archive_path: str | Path, dest: str | Path) -> list[Path]:
    """Unpack a zip into `dest` and return the files written.

    A single top-level folder inside the zip is dropped (so files land directly in
    `dest`), names that would escape `dest` are skipped, and an archive that would
    unpack to an absurd size is refused.
    """
    dest = Path(dest)
    written: list[Path] = []
    try:
        with zipfile.ZipFile(archive_path) as archive:
            members = _safe_members(archive)
            if sum(m.file_size for m in members) > MAX_ARCHIVE_BYTES:
                raise IdgamesError("that archive would unpack to more than 800 MB; not extracting")
            tops = {m.filename.replace("\\", "/").split("/", 1)[0] for m in members}
            strip = len(tops) == 1 and all("/" in m.filename.replace("\\", "/") for m in members)
            for info in members:
                parts = info.filename.replace("\\", "/").split("/")
                if strip:
                    parts = parts[1:]
                target = dest.joinpath(*parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, open(target, "wb") as out:
                    shutil.copyfileobj(source, out)
                written.append(target)
    except zipfile.BadZipFile as error:
        raise IdgamesError("the download is not a valid zip file") from error
    except OSError as error:
        raise IdgamesError(f"could not unpack the download: {error}") from error
    return written


def install(file: IdgamesFile, archive_path: str | Path, wads_root: str | Path,
            category: str | None = None, mapsets_category: str = "maps") -> InstallResult:
    """Unpack a downloaded archive into <wads_root>/<category>/<project>/."""
    category = category or category_for(file.dir, mapsets_category)
    folder = Path(wads_root).expanduser() / category / file.project_name
    folder.mkdir(parents=True, exist_ok=True)
    files = extract_archive(archive_path, folder)
    return InstallResult(folder=folder, files=files, category=category)


def download(file: IdgamesFile, dest: str | Path, progress: netio.Progress | None = None,
             cancelled: Callable[[], bool] | None = None,
             mirrors: tuple[str, ...] = MIRRORS) -> Path:
    """Download an archive file from the first mirror that works."""
    errors = []
    for url in file.download_urls(mirrors):
        try:
            return netio.download(url, dest, progress, cancelled=cancelled)
        except netio.NetError as error:
            if "cancelled" in str(error):
                raise IdgamesError("download cancelled") from error
            errors.append(str(error))
    raise IdgamesError("every mirror failed: " + "; ".join(errors) if errors else "no download address")


def source_record(file: IdgamesFile) -> dict:
    """What gets remembered about a download so updates can be found later."""
    return {"kind": "idgames", "id": file.id, "path": file.path, "stamp": file.age or file.date,
            "size": file.size, "title": file.title}
