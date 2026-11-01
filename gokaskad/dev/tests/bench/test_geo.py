"""The country of the node kept (the coordinator 19.10, ``bench.geo``): written by the collector (the geo pass of the
exits) and by the check (the delays), read by the channel, the price of the schedule (B13, C2) and the planner of the
repeat; a copy of another machine comes without it, as the tariff."""

import os
from unittest import mock

from kaskad import paths, state
from kaskad.bench import geo, machine, suite
from kaskad.gw import collect
from tests.helpers import RootCase

MID_RAW = "0123456789abcdef0123456789abcdef"
HERE = machine.mid_of(MID_RAW)
OTHER = machine.mid_of("f" * 32)
NOW = 1760000000


class GeoCase(RootCase):
    def setUp(self):
        super().setUp()
        self.write("/etc/machine-id", MID_RAW + "\n", 0o444)

    def save(self, st):
        os.makedirs(paths.etc(), 0o700, exist_ok=True)
        state.save(st)


class StoredTest(GeoCase):
    def test_the_collector_keeps_the_country_of_the_geo_pass(self):
        st = {}
        self.assertTrue(collect._geo_kept(st, {"direct_loc": "ru", "locs": {}}, NOW))
        self.assertEqual(st["gateway"]["hostmon"]["geo"],
                         {"country": "RU", "ru": True, "src": "exits_geo", "ts": NOW, "mid": HERE})
        self.assertFalse(collect._geo_kept(st, {"direct_loc": "RU"}, NOW + 60))        # the same: no write
        self.assertTrue(collect._geo_kept(st, {"direct_loc": "RU"}, NOW + geo.REFRESH_S))
        self.assertTrue(collect._geo_kept(st, {"direct_loc": "DE"}, NOW + geo.REFRESH_S + 1))
        self.assertEqual(geo.country(st, now=NOW + geo.REFRESH_S + 1)["country"], "DE")
        for bad in ({"direct_loc": None}, {"direct_loc": "R"}, {"direct_loc": "ру"}, {"direct_loc": "R1"}, {}, None,
                    {"direct_loc": "XX"}, {"direct_loc": "xx"}, {"direct_loc": "T1"}):
            self.assertFalse(collect._geo_kept(st, bad, NOW + geo.REFRESH_S * 3), bad)
        self.assertEqual(geo.country(st, now=NOW + geo.REFRESH_S * 3)["country"], "DE")   # «XX» never over it

    def test_unknown_of_cloudflare_is_no_country(self):
        """REV-ns-join П-4: ``loc=XX`` («unknown») is never kept, never read, and the live geo pass with it is no
        country either — the setting decides."""
        st = {}
        self.assertFalse(geo.put(st, "XX", "exits_geo", NOW))
        self.assertNotIn("gateway", st)
        st = {"gateway": {"hostmon": {"geo": {"country": "XX", "ru": False, "src": "exits_geo", "ts": NOW,
                                              "mid": HERE}, "bench": {"ru": "yes"}}}}
        self.assertIsNone(geo.stored(st, now=NOW))
        self.assertEqual(geo.country(st, now=NOW)["src"], "setting")
        never = lambda h, p: self.fail("no connection: the setting says")              # noqa: E731
        self.assertTrue(suite.node_ru(st, last_pass={"direct_loc": "XX"}, ms=never))

    def test_the_order_of_the_sources(self):
        st = {}
        self.assertEqual(geo.country(st, now=NOW), {"ru": None, "country": None, "src": None, "ts": None})
        geo.put(st, None, "rtt", NOW, ru=True)
        self.assertEqual(geo.country(st, now=NOW)["src"], "rtt")
        self.assertIsNone(geo.country(st, now=NOW + geo.RTT_KEEP_S + 1)["ru"])         # the delays of a month
        geo.put(st, None, "rtt", NOW, ru=False)
        self.assertEqual(geo.stored(st, now=NOW)["ru"], False)
        self.assertIsNone(geo.country(st, now=NOW)["ru"])     # SEC-ns-join З-2: a kept «abroad» is never read
        st["gateway"]["hostmon"]["bench"] = {"ru": "yes"}
        self.assertEqual((geo.country(st, now=NOW)["ru"], geo.country(st, now=NOW)["src"]), (True, "setting"))
        geo.put(st, "NL", "exits_geo", NOW)
        self.assertEqual(geo.country(st, now=NOW), {"ru": False, "country": "NL", "src": "exits_geo", "ts": NOW})
        # the delays never over the geo pass of the exits
        self.assertFalse(geo.put(st, None, "rtt", NOW + geo.REFRESH_S * 2, ru=True))
        self.assertEqual(geo.country(st, now=NOW)["src"], "exits_geo")

    def test_the_geo_pass_is_kept_90_days(self):
        """SEC-ns-join П-1: the country of the geo pass of the exits is read 90 days, then the setting and the delays;
        the delays may then be written over it."""
        st = {}
        geo.put(st, "DE", "exits_geo", NOW)
        self.assertEqual(geo.GEO_KEEP_S, 90 * 86400)
        self.assertEqual(geo.country(st, now=NOW + geo.GEO_KEEP_S)["country"], "DE")
        late = NOW + geo.GEO_KEEP_S + 1
        self.assertEqual(geo.country(st, now=late), {"ru": None, "country": None, "src": None, "ts": None})
        st["gateway"]["hostmon"]["bench"] = {"ru": "yes"}
        self.assertEqual((geo.country(st, now=late)["ru"], geo.country(st, now=late)["src"]), (True, "setting"))
        del st["gateway"]["hostmon"]["bench"]
        self.assertTrue(geo.put(st, None, "rtt", late, ru=True))
        self.assertEqual(geo.country(st, now=late)["src"], "rtt")

    def test_a_record_that_is_not_right_is_none(self):
        good = {"country": "RU", "ru": True, "src": "exits_geo", "ts": NOW, "mid": HERE}
        for k, v in (("country", "Ru"), ("country", "RUS"), ("country", "XX"), ("ru", False), ("ru", 1), ("src", "setting"),
                     ("src", "panel"), ("ts", "1"), ("ts", True), ("ts", NOW + 2 * 86400), ("mid", OTHER),
                     ("mid", None), ("country", None)):
            st = {"gateway": {"hostmon": {"geo": dict(good, **{k: v})}}}
            self.assertIsNone(geo.stored(st, now=NOW), (k, v))
        self.assertIsNotNone(geo.stored({"gateway": {"hostmon": {"geo": good}}}, now=NOW))
        self.assertIsNone(geo.stored({"gateway": {"hostmon": {"geo": "RU"}}}, now=NOW))
        self.assertIsNone(geo.stored({"gateway": []}, now=NOW))
        with self.assertRaises(ValueError):
            geo.put({}, "R", "exits_geo", NOW)
        with self.assertRaises(ValueError):
            geo.put({}, None, "setting", NOW)
        with self.assertRaises(ValueError):
            geo.put({}, None, "rtt", NOW, ru=None)

    def test_a_cloned_disk_reads_no_country(self):
        st = {}
        geo.put(st, "RU", "exits_geo", NOW)
        self.assertIsNone(geo.stored(st, here=OTHER, now=NOW))
        self.assertIsNone(geo.country(st, here=OTHER, now=NOW)["ru"])

    def test_the_check_keeps_the_delays_through_the_collector(self):
        """SEC-ns-join П-3: the check never takes the lock of state.json — its answer goes to its own file, the
        collector carries it under its lock (``take_rtt``) and removes the file."""
        self.save(state.empty())
        never = mock.patch.object(state, "transaction", side_effect=AssertionError("no lock from the check"))
        with never, mock.patch.object(state, "locked", side_effect=AssertionError("no lock from the check")):
            self.assertTrue(geo.remember_rtt(True, now=NOW))
        self.assertTrue(os.path.exists(geo.rtt_path()))
        self.assertIsNone(geo.stored(state.load(), now=NOW))                           # not yet: the collector
        st = state.load()
        self.assertTrue(collect._rtt_kept(st, NOW + 30))
        self.assertFalse(os.path.exists(geo.rtt_path()))
        self.assertFalse(os.path.exists(geo.rtt_path() + ".take"))
        self.assertEqual(geo.stored(st, now=NOW)["src"], "rtt")
        self.assertEqual(geo.stored(st, now=NOW)["ts"], NOW)
        self.save(st)
        self.assertFalse(geo.remember_rtt(True, now=NOW + 60))                         # the same: no file
        self.assertFalse(os.path.exists(geo.rtt_path()))
        self.assertFalse(collect._rtt_kept(st, NOW + 90))                              # none: nothing
        self.assertFalse(geo.remember_rtt(None, now=NOW))
        with mock.patch.object(geo, "rtt_path", side_effect=OSError("disk")):
            self.assertFalse(geo.remember_rtt(False, now=NOW + 120))                   # never raises

    def test_the_collector_checks_the_file_of_the_check(self):
        import json
        self.save(state.empty())
        good = {"ru": True, "ts": NOW, "mid": HERE}
        bads = (dict(good, ru=1), dict(good, ts="1"), dict(good, ts=NOW - geo.RTT_KEEP_S - 100),
                dict(good, ts=NOW + 2 * 86400), dict(good, mid=OTHER), dict(good, mid=None), [good], "x")
        for bad in bads:
            os.makedirs(os.path.dirname(geo.rtt_path()), 0o700, exist_ok=True)
            with open(geo.rtt_path(), "w") as f:
                f.write(json.dumps(bad) if bad != "x" else "{not json")
            st = state.load()
            self.assertFalse(geo.take_rtt(st, NOW), bad)
            self.assertIsNone(geo.stored(st, now=NOW), bad)
            self.assertFalse(os.path.exists(geo.rtt_path()), bad)                       # dropped
        with open(geo.rtt_path(), "w") as f:
            f.write(json.dumps(dict(good, pad="x" * geo.RTT_FILE_MAX)))
        self.assertFalse(geo.take_rtt(state.load(), NOW))                              # too long
        os.symlink(self.path("/etc/machine-id"), geo.rtt_path())
        self.assertFalse(geo.take_rtt(state.load(), NOW))                              # a link is never followed
        self.assertFalse(os.path.exists(geo.rtt_path()))
        self.assertTrue(os.path.exists(self.path("/etc/machine-id")))

    def test_the_channel_reads_it_and_keeps_the_delays(self):
        st = {}
        geo.put(st, "RU", "exits_geo", int(__import__("time").time()))
        kept = []
        never = lambda h, p: self.fail("no connection when the country is kept")        # noqa: E731
        self.assertTrue(suite.node_ru(st, last_pass={}, ms=never, keep=kept.append))
        self.assertEqual(kept, [])
        # the setting over the delays kept; the delays of the check kept by ``keep``
        st = {}
        geo.put(st, None, "rtt", int(__import__("time").time()), ru=True)
        self.assertTrue(suite.node_ru(st, last_pass={}, ms=lambda h, p: None))           # no answer: the kept one
        ms = lambda h, p: 5.0 if h == "1.1.1.1" else 30.0                                # noqa: E731
        self.assertFalse(suite.node_ru(st, last_pass={}, ms=ms, keep=kept.append))       # an answer now: abroad
        self.assertEqual(kept, [False])
        st["gateway"]["hostmon"]["bench"] = {"ru": "yes"}
        self.assertTrue(suite.node_ru(st, last_pass={}, ms=ms))


class RestoreTest(GeoCase):
    def test_the_copy_never_brings_the_country(self):
        """SEC-ns-join З-1: the record of the copy never comes, whatever its machine-id (the same one — two servers
        of one template); the record of this machine in the state as it is stays, or none."""
        mine = {"country": "RU", "ru": True, "src": "exits_geo", "ts": NOW, "mid": HERE}
        copy_de = {"country": "DE", "ru": False, "src": "exits_geo", "ts": NOW, "mid": HERE}
        for other in (copy_de, dict(copy_de, mid=OTHER), dict(copy_de, mid=None), "DE"):
            g = {"hostmon": {"geo": other, "bench": {"ru": "yes"}}}
            self.assertTrue(geo.restore(g, {}, now=NOW))
            self.assertEqual(g["hostmon"], {"bench": {"ru": "yes"}})                 # the setting is the owner's
            g = {"hostmon": {"geo": other}}
            geo.restore(g, {"gateway": {"hostmon": {"geo": dict(mine)}}}, now=NOW)
            self.assertEqual(g["hostmon"]["geo"], mine)                                # this machine's stays
        g = {}
        self.assertFalse(geo.restore(g, {"gateway": {"hostmon": {"geo": dict(mine)}}}, now=NOW))
        self.assertEqual(g, {"hostmon": {"geo": mine}})
        g = {"hostmon": {}}
        geo.restore(g, {"gateway": {"hostmon": {"geo": dict(mine, mid=OTHER)}}}, now=NOW)  # a cloned disk here
        self.assertEqual(g, {"hostmon": {}})

    def test_the_setting_of_the_owner_decides_after_the_restore(self):
        """SEC-ns-join З-1 (опыт test_q2_copy_same_mid_keeps_foreign_country): a copy «DE» with the same machine-id,
        the owner says «узел в России: да» — after the restore the node is in Russia, no upload abroad."""
        from kaskad.bench import channel
        g = {"hostmon": {"geo": {"country": "DE", "ru": False, "src": "exits_geo", "ts": NOW, "mid": HERE},
                         "bench": {"ru": "yes"}}}
        geo.restore(g, {})
        st = {"gateway": g}
        never = lambda h, p: self.fail("the setting says")                             # noqa: E731
        self.assertIs(suite.node_ru(st, last_pass={}, ms=never), True)
        env = channel.Env(ru=True, manual=False, up_url=None, up_cap_mb=None, iperf=False, have_iperf=False,
                          skip=("8b", "9"), targets={"http": {"down": {}, "up": {"abroad": [
                              channel.http_target({"id": "cf", "url": "https://up.example.com/u"})]}},
                              "iperf3": {}})
        self.assertEqual(channel._plan(env, "net_up")[2], "no_upload_ru")


class PriceTest(GeoCase):
    def test_the_price_reads_the_country_kept(self):
        from kaskad import host_sched as host                  # the price of the schedule: one source (B14)
        st = {}
        self.assertFalse(host.node_ru(st))
        geo.put(st, "RU", "exits_geo", int(__import__("time").time()))
        self.assertTrue(host.node_ru(st))
        st = {"gateway": {"hostmon": {"bench": {"ru": "yes"}}}}
        self.assertTrue(host.node_ru(st))
        self.assertFalse(host.ru_point(st))
        st["gateway"]["hostmon"]["bench"]["ru_point"] = {"url": "https://up.example.com/u", "full": 500}
        self.assertEqual(host.ru_point(st), {"full": 500})
        st["gateway"]["hostmon"]["bench"]["ru_point"] = {"url": "https://up.example.com/u"}
        self.assertIs(host.ru_point(st), True)
        st["gateway"]["hostmon"]["bench"]["ru_point"] = {"url": "http://up.example.com/u"}
        self.assertFalse(host.ru_point(st))                                            # not a target of the channel
