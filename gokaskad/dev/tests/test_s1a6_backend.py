"""А6 (stand/logs/R-s1a6.md): the bash parts of the batch — run from install.sh itself, not from a copy.

* T-3379 З-2: ``egress_awg_route_leg`` puts the ip rule of a new AmneziaWG leg into its slot of the gateway's block
  (the pref of the reply rule + 4 + N, ``kaskad.gw.net``), not on top (``ip rule add`` without a pref — 3999, above
  «to <base>»): the collector moved it and told «Gateway routing … restored» right after ``gw exit-add --group``;
* T-3379 З-1: ``exit_failover`` against the tier the feed said (``told_tier``), not the health view of the round —
  through ``assign`` and through the real collector with its pins.json (REV-s1fin С-1);
* REV-3379 «было до»: an event of a newer version (a rollback) is said by its stored ``text``, never the raw key."""

import os
import re
import subprocess
import unittest

from tests.gw.test_collect_s1 import S1World

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def install_sh():
    with open(os.path.join(ROOT, "install.sh"), encoding="utf-8") as f:
        return f.read()


def fn(src, name):
    """The body of a function of install.sh (the only one of that name: a second would win in bash)."""
    found = re.findall(r"(?ms)^%s\(\) \{.*?^\}" % re.escape(name), src)
    assert len(found) == 1, (name, len(found))
    return found[0]


class LegRuleSlotTest(unittest.TestCase):
    BLOCK = ("0:\tfrom all lookup local\n"
             "4000:\tfrom all fwmark 0x10000/0x10000 lookup main\n"
             "4001:\tfrom all to 10.77.0.0/16 lookup main\n"
             "4002:\tfrom all fwmark 0x200 lookup 200\n"
             "4003:\tfrom all fwmark 0x300/0xff00 lookup 200\n"
             "4004:\tfrom 10.77.0.0/16 fwmark 0/0xffff lookup 199\n"
             "4005:\tfrom all fwmark 0x101 lookup 257\n"
             "32766:\tfrom all lookup main\n32767:\tfrom all lookup default\n")

    def run_leg(self, rules, mark, table):
        script = fn(install_sh(), "egress_awg_route_leg") + '''
k3_networkd_keep_foreign() { :; }
ip() { printf '%s\\n' "$*" >> "$IP_LOG"; [ "$*" = "-4 rule show" ] && printf '%s' "$RULES"; return 0; }
set -u
egress_awg_route_leg "$1" "$2" kawg9
'''
        log = os.path.join(self.tmp, "ip.log")
        r = subprocess.run(["bash", "-c", script, "leg", str(mark), str(table)],
                           env=dict(os.environ, IP_LOG=log, RULES=rules, LANG="C.UTF-8"),
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(log, encoding="utf-8") as f:
            return [l for l in f.read().splitlines() if l.startswith("rule ")]

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="k3-a6-")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_new_leg_is_born_in_its_slot(self):
        self.assertEqual(self.run_leg(self.BLOCK, 258, 258),
                         ["rule add pref 4006 fwmark 258 table 258"])
        moved = self.BLOCK.replace("4000:", "3990:").replace("4001:", "3991:")      # a block moved up by a foreign rule
        self.assertEqual(self.run_leg(moved, 0x10a, 0x10a)[-1], "rule add pref 4004 fwmark 266 table 266")

    def test_without_the_gateway_block_the_kernel_places_it(self):
        self.assertEqual(self.run_leg("0:\tfrom all lookup local\n32766:\tfrom all lookup main\n", 258, 258),
                         ["rule add fwmark 258 table 258"])

    def test_an_existing_rule_is_left_alone(self):
        self.assertEqual(self.run_leg(self.BLOCK, 257, 257), [])

    def test_the_slot_is_the_one_of_the_collector(self):
        from kaskad.gw import exits, net
        self.assertEqual((net.LEG_SLOT0, exits.AWG_MARK_BASE, net.REPLY_MARK, net.SLOTS[0]), (4, 256, 0x10000, "reply"))


if __name__ == "__main__":
    unittest.main()


class FailoverToldTierTest(unittest.TestCase):
    """T-3379 З-1: «x1 → x2» twice with no return between — the event compared the tier the group stands on by health
    (``active_tier``: back on the primary at once) instead of the tier the feed said; the return waits for the hold."""

    def setUp(self):
        from kaskad.gw import assign as A, policy as P
        self.A = A
        self.E1, self.E2 = "e-0000000001", "e-0000000002"
        d = P.default_doc("compat")
        d["groups"][0]["tiers"] = [P.default_tier("t1", [self.E1]), P.default_tier("t2", [self.E2])]
        self.doc = d

    def fo(self, dec):
        return [(e["from_tier"], e["to_tier"], e["back"], e["moved"]) for e in dec["events"]
                if e["kind"] == "exit_failover"]

    def step(self, prev, devs, e1, now):
        return self.A.assign({"main": devs}, {self.E1: e1, self.E2: "ok"}, self.doc, prev, {}, {}, now)

    def test_a_flap_of_the_primary_inside_the_hold_is_told_once(self):
        devs = [{"id": "d-%08x" % i} for i in range(3)]
        d1 = self.step(None, devs, "ok", 0)
        d2 = self.step(d1, devs, "off", 10)
        self.assertEqual(self.fo(d2), [("t1", "t2", False, 3)])
        d3 = self.step(d2, devs, "ok", 20)                       # the primary good: active_tier t1, the devices wait
        self.assertEqual((d3["groups"]["main"]["active_tier"], self.fo(d3)), ("t1", []))
        d4 = self.step(d3, devs, "off", 30)                      # it falls again: nobody moved — nothing new to say
        self.assertEqual(self.fo(d4), [])
        d5 = self.step(d4, devs, "ok", 40)
        d6 = self.step(d5, devs, "ok", 40 + 600)                 # the hold: the devices come back — told once
        self.assertEqual(self.fo(d6), [(None, "t1", True, 3)])
        d7 = self.step(d6, devs, "off", 700)                     # a new fall after the return — told again
        self.assertEqual(self.fo(d7), [("t1", "t2", False, 3)])

    def test_a_group_without_devices_hears_of_its_return(self):
        d1 = self.step(None, [], "ok", 0)
        d2 = self.step(d1, [], "off", 10)
        self.assertEqual(self.fo(d2), [("t1", "t2", False, 0)])
        d3 = self.step(d2, [], "ok", 20)
        d4 = self.step(d3, [], "off", 30)
        self.assertEqual(self.fo(d3) + self.fo(d4), [])           # the stand: a second «x1 → x2» 1.5 min later
        d5 = self.step(d4, [], "ok", 40)
        d6 = self.step(d5, [], "ok", 40 + 599)
        self.assertEqual(self.fo(d6), [])
        d7 = self.step(d6, [], "ok", 40 + 600)                   # good for the hold: «снова основной уровень»
        self.assertEqual(self.fo(d7), [(None, "t1", True, 0)])
        self.assertEqual(self.fo(self.step(d7, [], "ok", 700)), [])
        self.assertEqual(self.fo(self.step(d7, [], "off", 700)), [("t1", "t2", False, 0)])

    def test_per_connection_the_tier_is_the_route(self):
        # «по соединениям»: no pins — the traffic follows the tier at once, so every fall is a real switch, as before
        self.doc["groups"][0]["per_connection"] = True
        devs = [{"id": "d-00000001"}]
        d1 = self.step(None, devs, "ok", 0)
        d2 = self.step(d1, devs, "off", 10)
        d3 = self.step(d2, devs, "ok", 20)
        d4 = self.step(d3, devs, "off", 30)
        self.assertEqual(self.fo(d2) + self.fo(d3) + self.fo(d4), [("t1", "t2", False, 0), ("t1", "t2", False, 0)])

    def test_a_pins_file_without_the_told_tier(self):
        d1 = self.step(None, [], "ok", 0)
        old = {"version": 1, "groups": {"main": dict((k, v) for k, v in d1["groups"]["main"].items()
                                                     if k != "told_tier")}}
        self.assertEqual(self.fo(self.step(old, [], "off", 10)), [("t1", "t2", False, 0)])   # as before
        from kaskad.gw import pins
        doc = {"version": pins.VERSION, "groups": {"main": {"active_tier": "t1", "told_tier": "t2"},
                                                   "g-000001": {"active_tier": "t1"}}}
        got = pins.normalize(doc, now=100)["groups"]
        self.assertEqual((got["main"]["told_tier"], "told_tier" in got["g-000001"]), ("t2", False))


class CollectorToldTierTest(S1World):
    """REV-s1fin С-1: the same as ``FailoverToldTierTest``, through the real collector — ``tick``, pins.json, the
    feed in live.json. ``_pins_doc`` dropped ``told_tier``, so every round ``assign`` read «a file without it» and took
    ``active_tier``: a flap of the primary inside the hold was told twice, a group without devices never heard of its
    return. «Основная»: the VLESS exit on t1, the AmneziaWG one on t2; the VLESS one falls, rises, falls, rises."""

    def setUp(self):
        super().setUp()
        from kaskad import state
        from kaskad.gw import policy as P
        st = state.load()
        doc = st["gateway"].get("exit_policy") or P.default_doc("compat")
        doc["groups"][0]["tiers"] = [P.default_tier("t1", [self.vid]), P.default_tier("t2", [self.aid])]
        st["gateway"]["exit_policy"] = doc
        state.save(st)

    def fo(self):
        return [(e["args"].get("from_tier"), e["args"].get("to_tier"), e["args"].get("back"), e["args"].get("moved"))
                for e in self.events() if e["kind"] == "exit_failover"]

    def tier(self):
        from kaskad.gw import pins
        return pins.read()["groups"]["main"].get("active_tier")

    def vless(self, alive, tier):
        """The VLESS exit alive / not, rounds of 10 s until «Основная» stands on ``tier`` (the rule of the checks:
        down in about a minute, back after two good checks)."""
        self.w.curl.observatory = {"gx-" + self.vid: alive}
        for _ in range(20):
            self.round()
            if self.tier() == tier:
                return
        self.fail("never on %s" % tier)

    def flap(self):
        from kaskad.gw import pins
        for _ in range(12):                                       # 2 minutes of good checks
            self.round()
        self.vless(False, "t2")
        self.assertEqual(pins.read()["groups"]["main"].get("told_tier"), "t2")        # pins.json keeps it
        self.vless(True, "t1")                                    # the primary good: the devices wait for the hold
        self.vless(False, "t2")                                   # it falls again inside the hold
        self.vless(True, "t1")
        for _ in range(66):                                       # past hold_min (10 min) and the coalescing minute
            self.round()

    def test_a_flap_inside_the_hold_is_told_once(self):
        self.flap()
        self.assertEqual(self.fo(), [("t1", "t2", False, 1), (None, "t1", True, 1)])

    def test_a_group_without_devices_hears_of_its_return(self):
        self.ok("gw", "device", "del", self.dev()["id"])
        self.flap()
        self.assertEqual(self.fo(), [("t1", "t2", False, 0), (None, "t1", True, 0)])


class UnknownEventTextTest(unittest.TestCase):
    """REV-3379 «было до»: after a rollback a version shows an event of a newer one by its ``text`` (the words its
    writer stored), never by the raw key — the feed (``Live._said`` rewrites ``text`` every round), the bot."""

    NEW = {"id": 1, "ts": 1000, "kind": "zz_new_kind", "key": "gw_live.event.zz_new_kind", "args": {"n": 1},
           "text": "«Дом»: что-то новое случилось"}

    def test_the_stored_text_instead_of_the_raw_key(self):
        from kaskad.gw import live
        for lang in ("ru", "en"):
            self.assertEqual(live.event_text(dict(self.NEW), lang), "«Дом»: что-то новое случилось")
        self.assertEqual(live.event_text(dict(self.NEW, text=""), "ru"), "gw_live.event.zz_new_kind")   # nothing better

    def test_a_known_key_is_still_said_by_the_reader(self):
        from kaskad.gw import live
        ev = {"id": 2, "ts": 1000, "kind": "gw_part_restored", "key": "gw_live.event.routing_restored", "args": {},
              "text": "старые слова"}
        self.assertEqual(live.event_text(ev, "en"), "Gateway routing (ip rules, tables 199 and 200, kgw0) restored")

    def test_the_feed_keeps_it_round_after_round(self):
        from kaskad.gw import live
        lv = live.Live()
        lv.events = [dict(self.NEW)]
        for _ in range(2):
            self.assertEqual([e["text"] for e in lv._said()], ["«Дом»: что-то новое случилось"])

    def test_the_bot(self):
        from kaskad.bot import monitor
        self.assertEqual(monitor.event_text(dict(self.NEW)), "«Дом»: что-то новое случилось")


class BeforeTest(unittest.TestCase):
    """«Было до» of REV-3310/REV-3379: one definition of each function of install.sh (the second ``k3_term_esc`` —
    the same body, left at a merge; a different one would silently win in bash), the docstring of the bot's site
    with its real limit of callback_data."""

    def test_every_function_of_install_sh_is_defined_once(self):
        names = re.findall(r"(?m)^([A-Za-z_][A-Za-z0-9_]*)\(\) \{", install_sh())
        twice = sorted(set(n for n in names if names.count(n) > 1))
        self.assertEqual(twice, [])
        self.assertIn("k3_term_esc", names)

    def test_the_bot_site_docstring_says_the_real_limit(self):
        from kaskad.bot import site
        self.assertIn("at most %d bytes" % site._DATA_MAX, site.__doc__)
        self.assertNotIn("22 bytes", site.__doc__)
