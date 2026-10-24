"""Knowing when a downloaded mod or wad has a newer version.

Each library project can carry a "source" in the config's library_meta: where it came
from (the idgames archive, or a GitHub repository) and a stamp saying which version is
installed. `check_all` asks the sources what is newest and reports the projects that
differ. Nothing is changed automatically; the window offers "update" and "mark as up to
date", and `python -m doomdeck --check-updates` prints the same report for scripts and
desktop notifiers.

Nothing here touches Qt.
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass
from typing import Callable

from . import idgames, netio

GITHUB_RELEASES = "https://api.github.com/repos/{repo}/releases/latest"


@dataclass
class UpdateInfo:
    key: str  # library project key, "category/project"
    kind: str  # "idgames" or "github"
    installed: str  # what is installed now (date or tag), "" if unknown
    latest: str  # what the source offers
    title: str = ""
    url: str = ""  # a page about the new version
    error: str = ""  # set instead of `latest` when the source could not be checked

    @property
    def is_update(self) -> bool:
        return not self.error and bool(self.latest) and self.latest != self.installed


def parse_source(text: str) -> dict | None:
    """Turn what the user typed into a source record, or None if it is not recognised.

    Understands GitHub links and "owner/repo", and everything idgames.parse_page_link does
    (a number, "idgames:123", a Doomworld link). The stamp is left empty; `baseline` fills it.
    """
    text = text.strip()
    if not text:
        return None
    parsed = urllib.parse.urlparse(text)
    if "github.com" in parsed.netloc:
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) >= 2:
            return {"kind": "github", "repo": f"{parts[0]}/{parts[1].removesuffix('.git')}", "stamp": ""}
        return None
    if re.fullmatch(r"[\w.-]+/[\w.-]+", text) and not text.startswith(
        ("levels/", "themes/", "sounds/", "music/", "graphics/", "utils/", "combos/", "deathmatch/")
    ):
        return {"kind": "github", "repo": text, "stamp": ""}
    file_id, path = idgames.parse_page_link(text)
    if file_id:
        return {"kind": "idgames", "id": int(file_id), "path": "", "stamp": ""}
    if path:
        return {"kind": "idgames", "id": 0, "path": path, "stamp": ""}
    return None


def latest_for(source: dict, fetch: Callable[[str], object] | None = None) -> tuple[str, str, str]:
    """Ask a source for its newest version: (stamp, title, url). Raises NetError/IdgamesError."""
    kind = source.get("kind")
    if kind == "github":
        data = (fetch or netio.get_json)(GITHUB_RELEASES.format(repo=source["repo"]))
        if not isinstance(data, dict) or not data.get("tag_name"):
            raise netio.NetError(f"{source['repo']} has no releases")
        return str(data["tag_name"]), str(data.get("name") or data["tag_name"]), str(data.get("html_url") or "")
    if kind == "idgames":
        found = idgames.get(source.get("id") or None, source.get("path", ""), fetch=fetch)
        return str(found.age or found.date), found.title, found.url
    raise netio.NetError(f"unknown update source {kind!r}")


def baseline(source: dict, fetch: Callable[[str], object] | None = None) -> dict:
    """Record the source's current newest version as the installed one.

    Used when a person attaches a source to something they already have, on the
    assumption that what they have is the current release.
    """
    stamp, title, _url = latest_for(source, fetch)
    out = dict(source)
    out["stamp"] = stamp
    if title:
        out["title"] = title
    if out.get("kind") == "idgames" and not out.get("id"):
        out["id"] = idgames.get(None, source.get("path", ""), fetch=fetch).id
    return out


def check_one(key: str, source: dict, fetch: Callable[[str], object] | None = None) -> UpdateInfo:
    installed = str(source.get("stamp") or "")
    try:
        stamp, title, url = latest_for(source, fetch)
    except (netio.NetError, idgames.IdgamesError, KeyError) as error:
        return UpdateInfo(key, str(source.get("kind", "")), installed, "", error=str(error))
    return UpdateInfo(key, str(source.get("kind", "")), installed, stamp, title=title, url=url)


def check_all(library_meta: dict[str, dict], fetch: Callable[[str], object] | None = None) -> list[UpdateInfo]:
    """Check every project that has a source; returns one entry per project (see is_update)."""
    results = []
    for key in sorted(library_meta):
        source = library_meta[key].get("source")
        if isinstance(source, dict) and source.get("kind"):
            results.append(check_one(key, source, fetch))
    return results


def report_text(results: list[UpdateInfo]) -> str:
    """Plain-text report of check_all results (for the command line and dialogs)."""
    lines = []
    for r in results:
        if r.error:
            lines.append(f"? {r.key}: could not check ({r.error})")
        elif r.is_update:
            lines.append(f"! {r.key}: new version {r.latest}" + (f" (you have {r.installed})" if r.installed else ""))
    checked = len(results)
    updates = sum(1 for r in results if r.is_update)
    lines.append(f"{checked} checked, {updates} with updates.")
    return "\n".join(lines)
