"""Asking a multiplayer server which files it is running.

Two kinds of server answer a "launcher query" over UDP without anyone joining:

* **Zandronum** servers (default port 10666). The request and reply are Huffman-coded
  (see huffman.py). The reply lists the server's name, map, IWAD, players and every PWAD
  in load order, with the MD5 of each file when the server is new enough to send them.
* **Odamex** servers (default port 10666). Plain little-endian UDP. The reply lists the
  files in load order with MD5s, and the addresses the server offers downloads from.

UZDoom and GZDoom games cannot be asked: their host only compares files when someone
connects, and has no query. For those, DoomDeck's shared-preset files are the way to
find out what a game needs.

Both formats were written from the servers' own source code (Zandronum
src/sv_master.cpp and Odamex server/src/sv_sqpold.cpp) and its launcher documentation.
Nothing here touches Qt, so it is tested with built packets; whether a particular live
server answers in the same way can only be tried against a live server.
"""

from __future__ import annotations

import socket
import struct
import time
from dataclasses import dataclass, field
from typing import Callable

from . import huffman

DEFAULT_PORT = 10666

# Zandronum launcher protocol.
ZAN_CHALLENGE = 199  # what a launcher sends
ZAN_REPLY = 5660023  # SERVER_LAUNCHER_CHALLENGE
ZAN_IGNORING = 5660024  # asked again too soon (the server allows one query per ~10 s)
ZAN_BANNED = 5660025
ZAN_SEGMENTED = 5660032

SQF_NAME = 0x00000001
SQF_URL = 0x00000002
SQF_EMAIL = 0x00000004
SQF_MAPNAME = 0x00000008
SQF_MAXCLIENTS = 0x00000010
SQF_MAXPLAYERS = 0x00000020
SQF_PWADS = 0x00000040
SQF_GAMETYPE = 0x00000080
SQF_GAMENAME = 0x00000100
SQF_IWAD = 0x00000200
SQF_FORCEPASSWORD = 0x00000400
SQF_FORCEJOINPASSWORD = 0x00000800
SQF_GAMESKILL = 0x00001000
SQF_BOTSKILL = 0x00002000
SQF_DMFLAGS = 0x00004000
SQF_LIMITS = 0x00010000
SQF_TEAMDAMAGE = 0x00020000
SQF_TEAMSCORES = 0x00040000
SQF_NUMPLAYERS = 0x00080000
SQF_PLAYERDATA = 0x00100000
SQF_TEAMINFO_NUMBER = 0x00200000
SQF_TEAMINFO_NAME = 0x00400000
SQF_TEAMINFO_COLOR = 0x00800000
SQF_TEAMINFO_SCORE = 0x01000000
SQF_TESTING_SERVER = 0x02000000
SQF_DATA_MD5SUM = 0x04000000
SQF_ALL_DMFLAGS = 0x08000000
SQF_SECURITY_SETTINGS = 0x10000000
SQF_OPTIONAL_WADS = 0x20000000
SQF_DEH = 0x40000000
SQF_EXTENDED_INFO = 0x80000000

SQF2_PWAD_HASHES = 0x00000001
SQF2_COUNTRY = 0x00000002
SQF2_GAMEMODE_NAME = 0x00000004
SQF2_GAMEMODE_SHORTNAME = 0x00000008
SQF2_VOICECHAT = 0x00000010

# What DoomDeck asks for (nothing about players: fewer bytes, one packet).
ZAN_FLAGS = (SQF_NAME | SQF_URL | SQF_MAPNAME | SQF_MAXCLIENTS | SQF_MAXPLAYERS | SQF_PWADS | SQF_GAMETYPE
             | SQF_GAMENAME | SQF_IWAD | SQF_FORCEPASSWORD | SQF_FORCEJOINPASSWORD | SQF_NUMPLAYERS
             | SQF_OPTIONAL_WADS | SQF_DEH | SQF_EXTENDED_INFO)
ZAN_FLAGS2 = SQF2_PWAD_HASHES | SQF2_GAMEMODE_NAME

ZAN_GAME_MODES = {
    0: "Cooperative", 1: "Survival", 2: "Invasion", 3: "Deathmatch", 4: "Team deathmatch", 5: "Duel",
    6: "Terminator", 7: "Last man standing", 8: "Team last man standing", 9: "Possession",
    10: "Team possession", 11: "Team game", 12: "Capture the flag", 13: "One-flag CTF", 14: "Skulltag",
    15: "Domination",
}

# Odamex's older query ("LAUNCHER_CHALLENGE") and its reply marker (MSG_CHALLENGE).
ODA_CHALLENGE = 777123
ODA_REPLY = 5560020

PROTOCOLS = ("zandronum", "odamex")


class QueryError(Exception):
    """The server could not be asked or its answer could not be read. The text is for people."""

    def __init__(self, message: str, retry_after: float = 0.0):
        super().__init__(message)
        self.retry_after = retry_after


@dataclass
class ServerFile:
    """One file the server has loaded."""

    name: str
    md5: str = ""
    optional: bool = False


@dataclass
class ServerInfo:
    """What a server said about itself."""

    protocol: str
    address: str
    port: int
    name: str = ""
    version: str = ""
    map_name: str = ""
    iwad: str = ""
    game: str = ""
    game_mode: str = ""
    players: int = 0
    max_clients: int = 0
    max_players: int = 0
    password: bool = False
    website: str = ""
    download_urls: list[str] = field(default_factory=list)
    files: list[ServerFile] = field(default_factory=list)
    patches: list[str] = field(default_factory=list)
    ping_ms: int = 0

    @property
    def required_files(self) -> list[ServerFile]:
        return [f for f in self.files if not f.optional]


class _Reader:
    """Reads little-endian values from a packet; a short packet raises QueryError."""

    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def _take(self, count: int) -> bytes:
        if self.pos + count > len(self.data):
            raise QueryError("The server's reply was cut short.")
        chunk = self.data[self.pos:self.pos + count]
        self.pos += count
        return chunk

    def byte(self) -> int:
        return self._take(1)[0]

    def short(self) -> int:
        return struct.unpack("<h", self._take(2))[0]

    def long(self) -> int:
        return struct.unpack("<i", self._take(4))[0]

    def ulong(self) -> int:
        return struct.unpack("<I", self._take(4))[0]

    def float(self) -> float:
        return struct.unpack("<f", self._take(4))[0]

    def string(self) -> str:
        end = self.data.find(b"\0", self.pos)
        if end < 0:
            raise QueryError("The server's reply was cut short.")
        raw = self.data[self.pos:end]
        self.pos = end + 1
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("latin-1")

    def left(self) -> int:
        return len(self.data) - self.pos


# ---- Zandronum -------------------------------------------------------------------------------


def zandronum_request(stamp: int, flags: int = ZAN_FLAGS, flags2: int = ZAN_FLAGS2) -> bytes:
    """The (compressed) packet that asks a Zandronum server for its details."""
    body = struct.pack("<III", ZAN_CHALLENGE, flags & 0xFFFFFFFF, stamp & 0xFFFFFFFF)
    if flags & SQF_EXTENDED_INFO:
        body += struct.pack("<I", flags2 & 0xFFFFFFFF)
    return huffman.encode(body)


def parse_zandronum(packet: bytes, address: str, port: int, stamp: int | None = None) -> ServerInfo:
    """Read a Zandronum server's reply (still compressed, as it came off the network)."""
    data = huffman.decode(packet)
    reader = _Reader(data)
    header = reader.ulong()
    if header == ZAN_IGNORING:
        raise QueryError("The server limits how often it can be asked. Try again in about 10 seconds.",
                         retry_after=10.0)
    if header == ZAN_BANNED:
        raise QueryError("This computer is banned from the server, so it will not tell us anything.")
    if header == ZAN_SEGMENTED:
        raise QueryError("The server sent its answer in pieces, which DoomDeck did not ask for.")
    if header != ZAN_REPLY:
        raise QueryError("That is not a Zandronum server's reply.")
    echoed = reader.ulong()
    if stamp is not None and echoed != stamp & 0xFFFFFFFF:
        raise QueryError("The reply was for a different question.")
    info = ServerInfo(protocol="zandronum", address=address, port=port)
    info.version = reader.string()
    flags = reader.ulong()
    names: list[str] = []
    optional: list[int] = []
    flags2 = 0

    if flags & SQF_NAME:
        info.name = reader.string()
    if flags & SQF_URL:
        info.website = reader.string()
    if flags & SQF_EMAIL:
        reader.string()
    if flags & SQF_MAPNAME:
        info.map_name = reader.string()
    if flags & SQF_MAXCLIENTS:
        info.max_clients = reader.byte()
    if flags & SQF_MAXPLAYERS:
        info.max_players = reader.byte()
    if flags & SQF_PWADS:
        names = [reader.string() for _ in range(reader.byte())]
    if flags & SQF_GAMETYPE:
        mode = reader.byte()
        reader.byte()  # instagib
        reader.byte()  # buckshot
        info.game_mode = ZAN_GAME_MODES.get(mode, "")
    if flags & SQF_GAMENAME:
        info.game = reader.string()
    if flags & SQF_IWAD:
        info.iwad = reader.string()
    if flags & SQF_FORCEPASSWORD:
        info.password = bool(reader.byte())
    if flags & SQF_FORCEJOINPASSWORD:
        info.password = bool(reader.byte()) or info.password
    if flags & SQF_GAMESKILL:
        reader.byte()
    if flags & SQF_BOTSKILL:
        reader.byte()
    if flags & SQF_DMFLAGS:
        reader.long()
        reader.long()
        reader.long()
    if flags & SQF_LIMITS:
        reader.short()  # frag limit
        time_limit = reader.short()
        if time_limit > 0:
            reader.short()  # time left
        reader.short()  # duel limit
        reader.short()  # point limit
        reader.short()  # win limit
    if flags & SQF_TEAMDAMAGE:
        reader.float()
    if flags & SQF_TEAMSCORES:
        reader.short()
        reader.short()
    if flags & SQF_NUMPLAYERS:
        info.players = reader.byte()
    unsupported = flags & (SQF_PLAYERDATA | SQF_TEAMINFO_NUMBER | SQF_TEAMINFO_NAME | SQF_TEAMINFO_COLOR
                           | SQF_TEAMINFO_SCORE)
    if not unsupported:
        if flags & SQF_TESTING_SERVER:
            reader.byte()
            reader.string()
        if flags & SQF_DATA_MD5SUM:
            reader.string()
        if flags & SQF_ALL_DMFLAGS:
            for _ in range(reader.byte()):
                reader.long()
        if flags & SQF_SECURITY_SETTINGS:
            reader.byte()
        if flags & SQF_OPTIONAL_WADS:
            optional = [reader.byte() for _ in range(reader.byte())]
        if flags & SQF_DEH:
            info.patches = [reader.string() for _ in range(reader.byte())]
        if flags & SQF_EXTENDED_INFO:
            flags2 = reader.ulong()
    hashes: list[str] = []
    if flags2 & SQF2_PWAD_HASHES:
        hashes = [reader.string() for _ in range(reader.byte())]
    if flags2 & SQF2_COUNTRY:
        reader._take(3)
    if flags2 & SQF2_GAMEMODE_NAME:
        mode_name = reader.string()
        info.game_mode = mode_name or info.game_mode
    info.files = [
        ServerFile(name, hashes[i].lower() if i < len(hashes) else "", i in optional)
        for i, name in enumerate(names)
    ]
    return info


# ---- Odamex ----------------------------------------------------------------------------------


def odamex_request() -> bytes:
    """The packet that asks an Odamex server for its details (its older launcher query)."""
    return struct.pack("<i", ODA_CHALLENGE)


def parse_odamex(packet: bytes, address: str, port: int) -> ServerInfo:
    """Read an Odamex server's reply to the older launcher query."""
    reader = _Reader(packet)
    if reader.long() != ODA_REPLY:
        raise QueryError("That is not an Odamex server's reply.")
    reader.long()  # connection token (a key follows only if the request carried one; ours does not)
    info = ServerInfo(protocol="odamex", address=address, port=port)
    info.name = reader.string()
    info.players = reader.byte()
    info.max_clients = reader.byte()
    info.map_name = reader.string()
    count = reader.byte()
    names = [reader.string() for _ in range(count)]
    reader.byte()  # deathmatch
    reader.byte()  # skill
    reader.byte()  # team deathmatch
    reader.byte()  # capture the flag
    for _ in range(info.players):  # one record per player: name, frags, ping, team
        reader.string()
        reader.short()
        reader.long()
        reader.byte()
    hashes = [reader.string() for _ in range(count)]
    sites = reader.string()
    info.download_urls = [u for u in sites.replace(",", " ").split() if u.lower().startswith(("http://", "https://"))]
    info.files = [ServerFile(n, hashes[i].lower()) for i, n in enumerate(names)]
    return info


# ---- the network -----------------------------------------------------------------------------

Exchange = Callable[[bytes], bytes]


def udp_exchange(host: str, port: int, timeout: float = 2.0, tries: int = 3) -> Exchange:
    """A function that sends one packet to host:port and returns the first reply.

    Raises QueryError after `tries` silent attempts (each waits `timeout` seconds).
    """
    def exchange(packet: bytes) -> bytes:
        try:
            family, _t, _p, _c, target = socket.getaddrinfo(host, port, type=socket.SOCK_DGRAM)[0]
        except (socket.gaierror, OSError) as error:
            raise QueryError(f"Could not find “{host}”: {error}") from error
        with socket.socket(family, socket.SOCK_DGRAM) as sock:
            sock.settimeout(timeout)
            for _ in range(tries):
                try:
                    sock.sendto(packet, target)
                    while True:
                        data, source = sock.recvfrom(65535)
                        if source[0] == target[0]:
                            return data
                except socket.timeout:
                    continue
                except OSError as error:
                    raise QueryError(f"Could not reach {host}:{port}: {error}") from error
        raise QueryError(f"{host}:{port} did not answer. The server may be offline, use a different port, "
                         "or run a game that cannot be asked (UZDoom and GZDoom hosts cannot).")
    return exchange


def split_address(text: str, default_port: int = DEFAULT_PORT) -> tuple[str, int]:
    """("host", port) from "host", "host:port", "1.2.3.4:5" or "[::1]:5"."""
    text = text.strip()
    if not text:
        raise QueryError("No server address was given.")
    if text.startswith("["):
        host, _bracket, rest = text[1:].partition("]")
        if rest.startswith(":") and rest[1:].isdigit():
            return host, int(rest[1:])
        return host, default_port
    if text.count(":") == 1:
        host, _colon, port = text.partition(":")
        if port.isdigit() and 0 < int(port) < 65536:
            return host, int(port)
        raise QueryError(f"“{port}” is not a port number.")
    return text, default_port


def query(address: str, protocol: str = "", default_port: int = DEFAULT_PORT,
          exchange_for: Callable[[str, int], Exchange] | None = None,
          clock: Callable[[], float] = time.monotonic) -> ServerInfo:
    """Ask a server what it is running.

    `protocol` is "zandronum" or "odamex"; empty tries Zandronum, then Odamex. Raises
    QueryError with a message fit to show.
    """
    host, port = split_address(address, default_port)
    if protocol and protocol not in PROTOCOLS:
        raise QueryError(f"DoomDeck cannot ask a {protocol} server (it can ask Zandronum and Odamex servers).")
    exchange = (exchange_for or udp_exchange)(host, port)
    errors: list[QueryError] = []
    for kind in ([protocol] if protocol else list(PROTOCOLS)):
        started = clock()
        try:
            if kind == "zandronum":
                stamp = int(started * 1000) & 0xFFFFFFFF
                reply = exchange(zandronum_request(stamp))
                info = parse_zandronum(reply, host, port, stamp)
            else:
                info = parse_odamex(exchange(odamex_request()), host, port)
        except QueryError as error:
            errors.append(error)
            if error.retry_after:  # it answered, but asked us to wait: do not try other kinds
                raise
            continue
        info.ping_ms = int((clock() - started) * 1000)
        return info
    raise errors[0]
