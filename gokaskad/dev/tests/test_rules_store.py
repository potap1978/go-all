"""kaskad.rules.store (versions, atomic switch, last good) and the update of kaskad.rules.ops/source: sources and
their fallbacks, sha256, a broken download keeps the stored lists."""

import json
import os

from kaskad import errors
from kaskad.rules import apply as rapply
from kaskad.rules import ops, source, store
from tests.helpers import RootCase
from tests.rules_helpers import (FakeHttp, geoip_bytes, geoip_dat, geosite_bytes, release, ru_nets, seed,
                                 sha_text, small_limits)

QUIET = {"log": lambda text: None, "apply_live": False}


class RulesCase(RootCase):
    def setUp(self):
        super().setUp()
        self._limits = small_limits()
        self._limits.start()

    def tearDown(self):
        self._limits.stop()
        super().tearDown()


class StoreTest(RootCase):
    def test_commit_switches_atomically_and_keeps_the_last_good(self):
        a = store.commit({"ru4.txt": b"5.8.0.0/24\n"}, {"kernel": {"ranges": 1}})
        self.assertEqual(store.active().id, a.id)
        self.assertIsNone(store.load(store.PREVIOUS))
        b = store.commit({"ru4.txt": b"5.8.0.0/23\n"}, {"kernel": {"ranges": 1}})
        c = store.commit({"ru4.txt": b"5.8.0.0/22\n"}, {"kernel": {"ranges": 1}})
        self.assertEqual((store.load().id, store.load(store.PREVIOUS).id), (c.id, b.id))
        # only current and previous are kept; links are relative (the store can be moved)
        self.assertEqual(sorted(os.listdir(os.path.join(store.root(), store.VERSIONS))), sorted([b.id, c.id]))
        self.assertTrue(os.readlink(os.path.join(store.root(), "current")).startswith("versions/"))
        self.assertEqual(c.meta["files"]["ru4.txt"]["size"], 11)
        self.assertEqual(c.read("ru4.txt"), b"5.8.0.0/22\n")
        self.assertIsNone(c.file("geoip.dat"))
        # the current version damaged on disk: the last good one is used
        with open(os.path.join(c.path, "ru4.txt"), "ab") as f:
            f.write(b"junk\n")
        self.assertIsNone(store.load())
        self.assertEqual(store.active().id, b.id)
        os.unlink(os.path.join(b.path, store.META))
        self.assertIsNone(store.active())

    def test_no_half_written_version_is_ever_current(self):
        store.commit({"ru4.txt": b"5.8.0.0/24\n"}, {})
        before = store.active().id
        with self.assertRaises(errors.KaskadError):
            store.commit({"../evil": b"x"}, {})
        self.assertEqual(store.active().id, before)
        leftovers = [n for n in os.listdir(os.path.join(store.root(), store.VERSIONS)) if n.startswith(".new-")]
        self.assertEqual(leftovers, [])

    def test_one_update_at_a_time(self):
        with store.update_lock():
            with self.assertRaises(errors.LockedError):
                with store.update_lock():
                    pass


class UpdateTest(RulesCase):
    def test_first_update_from_github(self):
        http = release()
        res = ops.update(http=http, **QUIET)
        self.assertTrue(res["changed"])
        v = store.active()
        self.assertEqual(sorted(v.meta["files"]), ["domains.json", "geoip.dat", "geosite.dat", "ru4.txt"])
        self.assertEqual(v.meta["geoip"]["tag"], "202610040942")
        self.assertEqual(v.meta["geoip"]["date"], "2026-10-04T09:42:00Z")
        self.assertEqual(v.meta["geoip"]["source"], "github")
        self.assertEqual((v.meta["kernel"]["cidrs"], v.meta["kernel"]["ranges"], v.meta["kernel"]["v6"]),
                         (len(ru_nets()), 1101, 1))
        self.assertEqual(sorted(v.meta["geoip"]["codes"]), ["DIRECT", "PRIVATE", "WHITELIST"])
        self.assertEqual(v.meta["domains"]["keyword"], 2)                 # youtube, netflix
        self.assertEqual(v.meta["domains"]["regexp"], 1)
        k = rapply.kernel()
        self.assertTrue(k["ready"])
        self.assertIn("77.88.0.0/23", k["ranges"])
        self.assertEqual(store.load_status()["ok"], True)
        # the next day: the same sha256 — two small requests, nothing downloaded, nothing written
        http.calls = []
        res = ops.update(http=http, **QUIET)
        self.assertFalse(res["changed"])
        self.assertEqual(http.calls, [source.GITHUB_GEOIP + ".sha256", source.GITHUB_GEOSITE + ".sha256"])
        self.assertEqual(store.active().id, v.id)
        # --force downloads again and makes a new version
        res = ops.update(http=http, force=True, **QUIET)
        self.assertTrue(res["changed"])
        self.assertNotEqual(store.active().id, v.id)

    def test_mirror_when_github_fails_and_sha_from_the_same_source(self):
        http = release(mirror=True)
        http.files[source.GITHUB_GEOIP + ".sha256"] = "нет соединения (timed out)"
        res = ops.update(http=http, **QUIET)
        self.assertTrue(res["changed"])
        self.assertEqual(store.active().meta["geoip"]["source"], "jsdelivr")
        self.assertIsNone(store.active().meta["geoip"]["tag"])
        # GitHub answers a sha256 its file does not have: refused, the mirror is used
        data = geoip_bytes(1200)
        http2 = release(geoip=data, mirror=True)
        http2.files[source.GITHUB_GEOIP] = data
        http2.files[source.GITHUB_GEOIP + ".sha256"] = sha_text(b"something else")
        got = source.fetch_dat("geoip", http2)
        self.assertEqual(got["source"], "jsdelivr")

    def test_broken_download_keeps_the_stored_lists(self):
        seed()
        good = store.active()
        bad = geoip_bytes(1200)[:-5]                                       # cut short: does not decode
        http = release(geoip=bad)
        http.files[source.JSD_GEOIP] = bad
        http.files[source.JSD_GEOIP + ".sha256"] = sha_text(bad)
        http.files[source.GITHUB_GEOSITE + ".sha256"] = sha_text(geosite_bytes(), "release/geosite.dat")
        res = ops.update(http=http, **QUIET)
        self.assertFalse(res["changed"])
        self.assertTrue(any("geoip не скачался" in w and "не разобран" in w for w in res["warnings"]), res)
        self.assertEqual(store.active().id, good.id)
        # nothing reachable at all: an error, the stored lists stay, status.json says why
        with self.assertRaises(errors.KaskadError) as cm:
            ops.update(http=FakeHttp(), **QUIET)
        self.assertIn("не скачались", str(cm.exception))
        self.assertEqual(store.active().id, good.id)
        st = store.load_status()
        self.assertFalse(st["ok"])
        self.assertEqual(st["kept"], good.id)

    def test_too_few_networks_or_a_sudden_drop_is_refused(self):
        tiny = geoip_dat([("DIRECT", ru_nets(10))])
        with self.assertRaises(errors.KaskadError):
            ops.update(http=release(geoip=tiny, geosite=b"x" * 10), **QUIET)
        self.assertIsNone(store.active())
        seed()
        half = geoip_bytes(1050 // 2 * 2)                                   # still > MIN_DIRECT ...
        old = store.active().meta["kernel"]["cidrs"]
        small = geoip_dat([("DIRECT", ru_nets(1010))])
        self.assertLess(1012, old)
        res_big = ops.update(http=release(geoip=half), **QUIET)            # 1052 of 1102: fine
        self.assertTrue(res_big["changed"])
        store.commit({"ru4.txt": b"5.8.0.0/24\n"}, dict(store.active().meta, kernel={"from": "geoip", "cidrs": 5000,
                                                                                    "ranges": 1}))
        with self.assertRaises(errors.KaskadError) as cm:                  # 1012 < 5000 / 2: refused
            ops.update(http=release(geoip=small), **QUIET)
        self.assertIn("меньше половины", str(cm.exception))
        self.assertTrue(ops.update(http=release(geoip=small), force=True, **QUIET)["changed"])

    def test_text_fallback_only_without_any_geoip(self):
        text = "".join(n + "\n" for n in ru_nets(1200)).encode("ascii")
        http = FakeHttp({source.TEXT_SOURCES[1]["url"]: text})
        site = geosite_bytes()
        http.files[source.GITHUB_GEOSITE] = site
        http.files[source.GITHUB_GEOSITE + ".sha256"] = sha_text(site, "release/geosite.dat")
        res = ops.update(http=http, **QUIET)
        self.assertTrue(res["changed"] and res["text"])
        v = store.active()
        self.assertIsNone(v.file("geoip.dat"))                              # xray gets no geoip file
        self.assertEqual(v.meta["text"]["source"], "runetfreedom")
        self.assertEqual(v.meta["kernel"]["from"], "text")
        self.assertTrue(rapply.ready())
        # later the release is reachable again: geoip.dat replaces the text list
        res = ops.update(http=release(), **QUIET)
        self.assertTrue(res["changed"])
        self.assertEqual(store.active().meta["kernel"]["from"], "geoip")
        # with stored networks a failing geoip does not fall back to text
        http = release()
        del http.files[source.GITHUB_GEOIP + ".sha256"]
        http.files[source.TEXT_SOURCES[1]["url"]] = text
        res = ops.update(http=http, **QUIET)
        self.assertFalse(res["changed"])
        self.assertNotIn(source.TEXT_SOURCES[1]["url"], http.calls)

    def test_size_caps_and_sha_files(self):
        with self.assertRaises(source.FetchError):
            source.parse_sha(b"<html>404</html>")
        self.assertEqual(source.parse_sha(sha_text(b"x")), sha_text(b"x").split()[0].decode())
        http = release(geoip=b"\x0a\x00" * 10)                              # far too small
        with self.assertRaises(source.SourceError) as cm:
            source.fetch_dat("geoip", http)
        self.assertIn("мал", str(cm.exception))
        self.assertEqual(source.tag_of(["https://github.com/a/b/releases/download/202610040942/geoip.dat"]),
                         "202610040942")
        self.assertIsNone(source.tag_date("latest"))

    def test_status_and_meta_are_json(self):
        seed()
        doc = ops.status({"gateway": {}})
        json.dumps(doc)
        self.assertTrue(doc["ready"])
        self.assertEqual(doc["groups"]["block"], 4)                         # ads 2 + win-spy 2
        self.assertIn("RoscomVPN", doc["attribution"])
        self.assertIn("сетей России 1102", ops.summary(doc))
