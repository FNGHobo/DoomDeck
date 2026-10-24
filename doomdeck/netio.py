"""Small HTTP helpers shared by the idgames browser, update checks and engine installer.

Only the standard library is used. Every function raises NetError (with a readable
message) instead of urllib's many exception types, so callers have one thing to catch.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from . import __version__

USER_AGENT = f"DoomDeck/{__version__} (+https://github.com/FNGHobo/DoomDeck)"
Progress = Callable[[int, int], None]  # bytes done, total (0 when unknown)


class NetError(Exception):
    """A download or web request failed; the message says why in plain words."""


def _open(url: str, timeout: float):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    try:
        return urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        raise NetError(f"{url} answered {error.code} {error.reason}") from error
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError, ValueError) as error:
        reason = getattr(error, "reason", error)
        raise NetError(f"could not reach {url}: {reason}") from error


def get_bytes(url: str, timeout: float = 25, limit: int = 64 * 1024 * 1024) -> bytes:
    """Fetch a URL into memory (at most `limit` bytes, to keep a wrong link harmless)."""
    with _open(url, timeout) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise NetError(f"{url} is bigger than {limit // (1024 * 1024)} MB")
    return data


def get_json(url: str, timeout: float = 25):
    """Fetch a URL and decode it as JSON."""
    raw = get_bytes(url, timeout)
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except ValueError as error:
        raise NetError(f"{url} did not return valid JSON") from error


def download(url: str, dest: str | Path, progress: Progress | None = None,
             timeout: float = 30, cancelled: Callable[[], bool] | None = None) -> Path:
    """Stream a URL to `dest` (written to a .part file first, then renamed).

    `progress(done, total)` is called as data arrives; `cancelled()` returning True stops
    the download and removes the partial file.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    try:
        with _open(url, timeout) as response, open(part, "wb") as out:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
                if cancelled and cancelled():
                    raise NetError("download cancelled")
        part.replace(dest)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return dest
