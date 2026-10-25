"""Checking a multiplayer server's files and fetching what is missing.

The packets are built here from the layouts in Zandronum's and Odamex's server source,
and the Huffman vectors were produced by compiling Zandronum's own codec. Nothing talks
to a real server or website; whether a live server answers exactly like this can only be
tried against a live server.
"""

from __future__ import annotations

import hashlib
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path

from doomdeck import compat, huffman, idgames, launch, modsync, netio, serverquery as sq
from doomdeck.models import AppConfig, Engine, Preset, Settings

# (data, Zandronum's compressed form) pairs made with the original C++ codec.
HUFFMAN_VECTORS = [
    ("c7000000e300000039300000", "04b8498a4e52408f04"),
    ("5a616e64726f6e756d20332e31004d792073657276657200",
     "03a39b22b5b17114526336f86a4b97706dfad8488df823350a"),
    ("0" * 60, "06922449922449922449922401"),
]


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()  # noqa: S324


def cstr(text: str) -> bytes:
    return text.encode() + b"\0"


def zan_packet(stamp=7, names=("a.wad", "b.pk3"), hashes=None, optional=(), patches=(), iwad="doom2.wad",
               header=sq.ZAN_REPLY, password=0):
    hashes = hashes if hashes is not None else [md5(n.encode()) for n in names]
    flags = sq.ZAN_FLAGS
    body = struct.pack("<II", header, stamp) + cstr("3.1-test") + struct.pack("<I", flags)
    body += cstr("Test server") + cstr("https://example.org/wads") + cstr("MAP01")
    body += bytes([16, 8, len(names)]) + b"".join(cstr(n) for n in names)
    body += bytes([3, 0, 0]) + cstr("Doom") + cstr(iwad)
    body += bytes([password, 0, 5])  # force password, force join password, players
    body += bytes([len(optional)]) + bytes(optional)
    body += bytes([len(patches)]) + b"".join(cstr(p) for p in patches)
    body += struct.pack("<I", sq.ZAN_FLAGS2)
    body += bytes([len(hashes)]) + b"".join(cstr(h) for h in hashes)
    body += cstr("Deathmatch")
    return huffman.encode(body)


def oda_packet(names=("a.wad",), hashes=None, sites="https://dl.example/wads http://x.example/", players=0):
    hashes = hashes if hashes is not None else [md5(n.encode()) for n in names]
    body = struct.pack("<ii", sq.ODA_REPLY, 99) + cstr("Oda server") + bytes([players, 16]) + cstr("MAP07")
    body += bytes([len(names)]) + b"".join(cstr(n) for n in names) + bytes([1, 3, 0, 0])
    for _ in range(players):
        body += cstr("Pat") + struct.pack("<h", 4) + struct.pack("<i", 30) + bytes([0])
    body += b"".join(cstr(h) for h in hashes) + cstr(sites)
    return body


class HuffmanTests(unittest.TestCase):
    def test_matches_the_original_codec(self):
        for plain, packed in HUFFMAN_VECTORS:
            self.assertEqual(huffman.encode(bytes.fromhex(plain)).hex(), packed)
            self.assertEqual(huffman.decode(bytes.fromhex(packed)).hex(), plain)

    def test_round_trip_and_uncompressed_fallback(self):
        for data in (b"", b"x", bytes(range(256)), b"hello world", b"\0" * 500):
            self.assertEqual(huffman.decode(huffman.encode(data)), data)
        self.assertEqual(huffman.encode(bytes(range(40)))[0], 0xFF)


class ZandronumTests(unittest.TestCase):
    def test_request_is_decodable(self):
        body = huffman.decode(sq.zandronum_request(1234))
        self.assertEqual(struct.unpack("<III", body[:12]), (sq.ZAN_CHALLENGE, sq.ZAN_FLAGS, 1234))
        self.assertEqual(len(body), 16)

    def test_reply_lists_files_in_order_with_hashes(self):
        info = sq.parse_zandronum(zan_packet(optional=[1], patches=["x.deh"]), "h", 10666, stamp=7)
        self.assertEqual([f.name for f in info.files], ["a.wad", "b.pk3"])
        self.assertEqual(info.files[0].md5, md5(b"a.wad"))
        self.assertTrue(info.files[1].optional)
        self.assertEqual([f.name for f in info.required_files], ["a.wad"])
        self.assertEqual(info.patches, ["x.deh"])
        self.assertEqual((info.name, info.map_name, info.iwad, info.players), ("Test server", "MAP01", "doom2.wad", 5))
        self.assertEqual((info.max_clients, info.max_players, info.website), (16, 8, "https://example.org/wads"))
        self.assertEqual(info.game_mode, "Deathmatch")
        self.assertEqual([f.name for f in modsync.server_entries(info)], ["a.wad", "b.pk3", "x.deh"])

    def test_password_and_errors(self):
        self.assertTrue(sq.parse_zandronum(zan_packet(password=1), "h", 1).password)
        with self.assertRaises(sq.QueryError) as caught:
            sq.parse_zandronum(huffman.encode(struct.pack("<II", sq.ZAN_IGNORING, 7)), "h", 1)
        self.assertGreater(caught.exception.retry_after, 0)
        for header in (sq.ZAN_BANNED, sq.ZAN_SEGMENTED, 42):
            with self.assertRaises(sq.QueryError):
                sq.parse_zandronum(huffman.encode(struct.pack("<II", header, 7)), "h", 1)
        with self.assertRaises(sq.QueryError):  # reply to another question
            sq.parse_zandronum(zan_packet(stamp=8), "h", 1, stamp=7)

    def test_truncated_reply_is_a_clean_error(self):
        whole = huffman.decode(zan_packet())
        for cut in (3, 12, 30, len(whole) - 4):
            with self.assertRaises(sq.QueryError):
                sq.parse_zandronum(huffman.encode(whole[:cut]), "h", 1)


class OdamexTests(unittest.TestCase):
    def test_reply(self):
        info = sq.parse_odamex(oda_packet(names=("a.wad", "b.wad"), players=2), "h", 10666)
        self.assertEqual([f.name for f in info.files], ["a.wad", "b.wad"])
        self.assertEqual(info.files[1].md5, md5(b"b.wad"))
        self.assertEqual(info.download_urls, ["https://dl.example/wads", "http://x.example/"])
        self.assertEqual((info.name, info.map_name, info.players, info.max_clients), ("Oda server", "MAP07", 2, 16))

    def test_rejects_other_packets_and_short_ones(self):
        with self.assertRaises(sq.QueryError):
            sq.parse_odamex(struct.pack("<ii", 1, 2), "h", 1)
        with self.assertRaises(sq.QueryError):
            sq.parse_odamex(oda_packet()[:20], "h", 1)

    def test_ignores_non_web_sites(self):
        info = sq.parse_odamex(oda_packet(sites="ftp://a/b file:///etc https://ok/"), "h", 1)
        self.assertEqual(info.download_urls, ["https://ok/"])


class QueryTests(unittest.TestCase):
    def test_split_address(self):
        self.assertEqual(sq.split_address("example.org"), ("example.org", 10666))
        self.assertEqual(sq.split_address("example.org:5000"), ("example.org", 5000))
        self.assertEqual(sq.split_address("[::1]:7"), ("::1", 7))

    def test_tries_zandronum_then_odamex(self):
        def exchange_for(host, port):
            def exchange(packet):
                if packet == sq.odamex_request():
                    return oda_packet()
                raise sq.QueryError("no answer")
            return exchange
        info = sq.query("srv", exchange_for=exchange_for)
        self.assertEqual(info.protocol, "odamex")

    def test_zandronum_first_and_rate_limit_stops(self):
        def good(host, port):
            return lambda packet: zan_packet(stamp=struct.unpack("<I", huffman.decode(packet)[8:12])[0])
        self.assertEqual(sq.query("srv", exchange_for=good).protocol, "zandronum")

        def limited(host, port):
            return lambda packet: huffman.encode(struct.pack("<II", sq.ZAN_IGNORING, 1))
        with self.assertRaises(sq.QueryError) as caught:
            sq.query("srv", exchange_for=limited)
        self.assertGreater(caught.exception.retry_after, 0)

    def test_unknown_protocol(self):
        with self.assertRaises(sq.QueryError):
            sq.query("srv", protocol="chocolate")


def info_with(files, urls=(), patches=()):
    return sq.ServerInfo(protocol="odamex", address="h", port=1, download_urls=list(urls),
                         files=[sq.ServerFile(*f) for f in files], patches=list(patches))


class CompareTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def write(self, rel, data):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def test_ok_different_missing_and_unsafe(self):
        good = self.write("maps/a/good.wad", b"good")
        self.write("maps/b/other.wad", b"mine")
        info = info_with([("good.wad", md5(b"good")), ("other.wad", md5(b"theirs")), ("gone.pk3", md5(b"x")),
                          ("../evil.wad", md5(b"x")), ("opt.wad", md5(b"o"), True)])
        matches = modsync.compare(info, modsync.sharing.index_files([self.root]))
        self.assertEqual([m.status for m in matches], ["ok", "different", "missing", "missing", "missing"])
        self.assertEqual(matches[0].path, str(good))
        self.assertTrue(matches[3].problem)
        self.assertEqual([m.name for m in modsync.needed(matches)], ["other.wad", "gone.pk3"])
        self.assertEqual([m.name for m in modsync.needed(matches, True)], ["other.wad", "gone.pk3", "opt.wad"])

    def test_same_name_in_two_folders_picks_the_matching_one(self):
        self.write("a/x.wad", b"old")
        right = self.write("b/x.wad", b"new")
        matches = modsync.compare(info_with([("x.wad", md5(b"new"))]), modsync.sharing.index_files([self.root]))
        self.assertEqual(matches[0].path, str(right))

    def test_no_hash_matches_on_name(self):
        self.write("x.wad", b"anything")
        matches = modsync.compare(info_with([("x.wad", "")]), modsync.sharing.index_files([self.root]))
        self.assertEqual(matches[0].status, "ok")

    def test_safe_file_name(self):
        for bad in ("", "a/b.wad", "..\\x.wad", "x.exe", "x.zip", "x", ".."):
            self.assertEqual(modsync.safe_file_name(bad), "", bad)
        self.assertEqual(modsync.safe_file_name("My Map.PK3"), "My Map.PK3")

    def test_iwad_status(self):
        (self.root / "doom2.wad").write_bytes(b"x")
        self.assertEqual(modsync.iwad_status(info_with([]), self.root), "")
        info = info_with([])
        info.iwad = "doom2.wad"
        self.assertEqual(modsync.iwad_status(info, self.root), "")
        info.iwad = "plutonia.wad"
        self.assertIn("plutonia.wad", modsync.iwad_status(info, self.root))

    def test_load_order_follows_the_server(self):
        matches = [modsync.Match("a.wad", status="ok", path="/x/a.wad"),
                   modsync.Match("b.wad", status="missing"),
                   modsync.Match("c.wad", status="ok", path="/x/c.wad", optional=True),
                   modsync.Match("d.wad", status="missing", optional=True)]
        self.assertEqual(modsync.load_order(matches), (["/x/a.wad", "/x/c.wad"], ["b.wad"]))


class FetchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.calls = []

    def downloader(self, payloads):
        def download(url, dest, progress=None, cancelled=None):
            self.calls.append(url)
            if url not in payloads:
                raise netio.NetError(f"{url} answered 404 Not Found")
            Path(dest).write_bytes(payloads[url])
            return Path(dest)
        return download

    def zip_bytes(self, files):
        path = self.root / "tmp.zip"
        with zipfile.ZipFile(path, "w") as z:
            for name, data in files.items():
                z.writestr(name, data)
        return path.read_bytes()

    def test_direct_download_is_verified_and_stored(self):
        info = info_with([("m.wad", md5(b"map"))], urls=["https://dl.example/w"])
        match = modsync.Match("m.wad", md5(b"map"))
        got = modsync.fetch_file(info, match, self.root / "wads",
                                 download=self.downloader({"https://dl.example/w/m.wad": b"map"}),
                                 search=lambda *a, **k: [])
        self.assertTrue(got.ok and got.verified)
        self.assertEqual(Path(got.path).read_bytes(), b"map")
        self.assertEqual(Path(got.path).parent, self.root / "wads" / "maps" / "m")

    def test_wrong_checksum_is_rejected(self):
        info = info_with([("m.wad", md5(b"map"))], urls=["https://dl.example/w"])
        got = modsync.fetch_file(info, modsync.Match("m.wad", md5(b"map")), self.root / "wads",
                                 download=self.downloader({"https://dl.example/w/m.wad": b"EVIL"}),
                                 search=lambda *a, **k: [])
        self.assertFalse(got.ok)
        self.assertFalse((self.root / "wads").exists() and any((self.root / "wads").rglob("*.wad")))

    def test_zip_from_the_server_site(self):
        info = info_with([("m.wad", md5(b"map"))], urls=["https://dl.example/w"])
        data = self.zip_bytes({"pack/m.wad": b"map", "pack/readme.txt": b"hi"})
        got = modsync.fetch_file(info, modsync.Match("m.wad", md5(b"map")), self.root / "wads",
                                 download=self.downloader({"https://dl.example/w/m.wad": data}),
                                 search=lambda *a, **k: [])
        self.assertTrue(got.ok)
        self.assertEqual(Path(got.path).read_bytes(), b"map")

    def test_falls_back_to_idgames_and_checks_the_version(self):
        info = info_with([("cool.wad", md5(b"v2"))])
        wrong = idgames.IdgamesFile(id=1, title="Cool", dir="levels/doom2/a-c/", filename="cool.zip")
        right = idgames.IdgamesFile(id=2, title="Cool 2", dir="levels/doom2/a-c/", filename="cool.zip")
        other = idgames.IdgamesFile(id=3, title="Other", dir="levels/", filename="cooler.zip")
        archives = {1: self.zip_bytes({"cool.wad": b"v1"}), 2: self.zip_bytes({"cool.wad": b"v2"})}

        def fake_download(entry, dest, progress=None, cancelled=None):
            Path(dest).write_bytes(archives[entry.id])
            return Path(dest)

        got = modsync.fetch_file(info, modsync.Match("cool.wad", md5(b"v2")), self.root / "wads",
                                 search=lambda q, kind="": [other, wrong, right], idgames_download=fake_download)
        self.assertTrue(got.ok, got.message)
        self.assertEqual(Path(got.path).read_bytes(), b"v2")
        self.assertIn("idgames", got.source)
        self.assertEqual(Path(got.path).parent.parent.name, "maps")

    def test_nothing_found_explains(self):
        got = modsync.fetch_file(info_with([("zz.wad", "")]), modsync.Match("zz.wad"), self.root,
                                 search=lambda *a, **k: [], download=self.downloader({}))
        self.assertFalse(got.ok)
        self.assertTrue(got.message)

    def test_fetch_missing_updates_matches_and_honours_cancel(self):
        info = info_with([("m.wad", md5(b"map"))], urls=["https://dl.example/w"])
        matches = [modsync.Match("m.wad", md5(b"map")), modsync.Match("n.wad", md5(b"n"))]
        report = modsync.fetch_missing(info, matches, self.root / "wads",
                                       download=self.downloader({"https://dl.example/w/m.wad": b"map"}),
                                       search=lambda *a, **k: [])
        self.assertEqual([r.ok for r in report.results], [True, False])
        self.assertEqual(matches[0].status, "ok")
        self.assertEqual(matches[1].status, "missing")
        self.assertEqual(len(report.failed), 1)
        again = modsync.fetch_missing(info, [modsync.Match("q.wad")], self.root, cancelled=lambda: True)
        self.assertEqual(again.results, [])

    def test_candidate_urls_quote_names_and_skip_other_schemes(self):
        info = info_with([], urls=["https://a/b/", "ftp://c/d"])
        self.assertEqual(modsync.candidate_urls(info, "my map.wad"), ["https://a/b/my%20map.wad"])


class JoinSwitchTests(unittest.TestCase):
    def preset(self, **kw):
        return Preset(name="p", net_mode="join", net_address="1.2.3.4", **kw)

    def test_engine_kind(self):
        self.assertEqual(compat.net_kind("Zandronum", "/x/zandronum"), "zandronum")
        self.assertEqual(compat.net_kind("Skulltag", ""), "zandronum")
        self.assertEqual(compat.net_kind("Odamex", "/x/odamex"), "odamex")
        self.assertEqual(compat.net_kind("UZDoom", "/x/uzdoom"), "zdoom")

    def test_connect_for_dedicated_server_engines(self):
        self.assertEqual(launch.net_args(self.preset(), "zandronum"), ["-connect", "1.2.3.4"])
        self.assertEqual(launch.net_args(self.preset(net_port=5000), "odamex"), ["-connect", "1.2.3.4:5000"])
        p = self.preset(net_port=5000)
        p.net_address = "1.2.3.4:6000"  # a port typed in the address wins; -port is never passed
        self.assertEqual(launch.net_args(p, "zandronum"), ["-connect", "1.2.3.4:6000"])
        self.assertEqual(launch.net_args(Preset(name="h", net_mode="host"), "zandronum"), [])

    def test_zdoom_family_unchanged(self):
        self.assertEqual(launch.net_args(self.preset(net_port=5000)), ["-join", "1.2.3.4", "-port", "5000"])

    def test_check_refuses_hosting_on_zandronum(self):
        config = AppConfig(settings=Settings(), engines=[Engine(name="Zandronum", path="/x/zandronum")],
                           presets=[])
        p = Preset(name="h", engine="Zandronum", net_mode="host")
        texts = [x.text for x in launch.check(config, p)]
        self.assertTrue(any("can only join" in t for t in texts))


if __name__ == "__main__":
    unittest.main()
