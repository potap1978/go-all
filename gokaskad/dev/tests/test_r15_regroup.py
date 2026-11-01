"""3.3.7.15 F14 «Разложить заново» (SPEC-R1 §1 решения 2–4, §3 F14): the night rebalance now for one group — the
request file of the panel, its take by the collector (rename to ``.work``, nothing lost — З-5), the rule of ``assign``
(only the active tier with a fit exit, the way back still waits for ``hold_min``, no ``night_on``), the refusals (no
such group, «по соединениям», the node offline / without the Russian addresses), the dry run that writes nothing and
shows the moves of the next round, the event ``group_regrouped`` of the feed (RU and EN, the bot silent)."""

import json
import os
import re
import stat
import time
import unittest
from unittest import mock

from kaskad import paths, state, util
from kaskad.gw import assign, collect, live, net, policy as P, policy_ops as pops
from tests.helpers import RootCase
from tests.panel.test_panel_policy import PolicyCase

RU = "g-0a0b0c"


def grp(gid="main", tiers=(("t1", ("e1", "e2")),), role="abroad", **kw):
    g = {"id": gid, "role": role, "tiers": [{"id": t, "exits": list(ex), "method": "even"} for t, ex in tiers],
         "night_rebalance": {"enabled": False, "at": "04:00"}, "return_to_primary": {"enabled": True, "hold_min": 10}}
    g.update(kw)
    return g


def pins_of(gid, pinned, tier="t1", good=None):
    return {"version": 1, "groups": {gid: {"pins": dict((d, {"exit": e, "tier": t, "since": 0, "why": "new"})
                                                         for d, (e, t) in pinned.items()),
                                           "active_tier": tier, "tier_good_since": good or {"t1": 0, "t2": 0}}}}


class AssignTest(unittest.TestCase):
    """The rule of ``assign`` with ``node.regroup`` (pure function)."""
    NOW = 1000000.0

    def away(self, n=20, ids=("e1", "e2")):
        """Devices each pinned to the exit that is NOT its «home» (HRW of the tier) -> ({dev: (exit, tier)}, homes)."""
        pinned, home = {}, {}
        for i in range(n):
            dev = "d-%08x" % (i + 1)
            h = assign.hrw(dev, list(ids))
            home[dev] = h
            pinned[dev] = ([e for e in ids if e != h][0], "t1")
        return pinned, home

    def run_one(self, groups, pins, node, states=None, devs=None):
        policy = {"groups": groups}
        states = states or {"e1": "ok", "e2": "ok", "e3": "ok"}
        devs = devs if devs is not None else dict((g["id"], [{"id": d} for d in (pins["groups"].get(g["id"]) or {})
                                                            .get("pins", {})]) for g in groups)
        return assign.assign(devs, states, policy, pins, {}, node, self.NOW)

    def test_regroup_moves_to_home_without_the_night(self):
        """The night off: no request — nobody moves; the request — every device to its «home» exit, why «rebalance»,
        one event ``group_regrouped`` with their number, ``night_on`` never written. Fails without F14: the request
        is not known, the devices stay away."""
        pinned, home = self.away()
        pins = pins_of("main", pinned)
        d0 = self.run_one([grp()], pins, {})
        self.assertEqual(d0["moves"], [])
        d = self.run_one([grp()], pins, {"regroup": {"main"}})
        self.assertEqual(len(d["moves"]), 20)
        self.assertEqual(set(m["why"] for m in d["moves"]), {"rebalance"})
        self.assertEqual(dict((dv, p["exit"]) for dv, p in d["groups"]["main"]["pins"].items()), home)
        ev = [e for e in d["events"] if e["kind"] == "group_regrouped"]
        self.assertEqual(ev, [{"kind": "group_regrouped", "group": "main", "role": "abroad", "moved": 20}])
        self.assertIsNone(d["groups"]["main"]["night_on"])

    def test_only_its_group(self):
        pinned, _ = self.away(5)
        other = dict(("x" + k, v) for k, v in pinned.items())
        pins = pins_of("main", pinned)
        pins["groups"]["g-111111"] = pins_of("g-111111", other)["groups"]["g-111111"]
        d = self.run_one([grp(), grp("g-111111")], pins, {"regroup": {"g-111111"}})
        self.assertEqual(set(m["group"] for m in d["moves"]), {"g-111111"})
        self.assertEqual([e["group"] for e in d["events"] if e["kind"] == "group_regrouped"], ["g-111111"])

    def test_the_way_back_still_waits_for_hold(self):
        """Devices on the second tier while the first is good for 1 min (hold 10 min): the request moves nobody back
        (the night rule is inside the active tier only)."""
        tiers = (("t1", ("e1",)), ("t2", ("e2", "e3")))
        pinned = dict(("d-%08x" % i, ("e2", "t2")) for i in range(1, 6))
        pins = pins_of("main", pinned, tier="t1", good={"t1": self.NOW - 60, "t2": 0})
        d = self.run_one([grp(tiers=tiers)], pins, {"regroup": {"main"}})
        self.assertEqual(d["moves"], [])
        self.assertEqual(set(p["exit"] for p in d["groups"]["main"]["pins"].values()), {"e2"})
        self.assertEqual([e["moved"] for e in d["events"] if e["kind"] == "group_regrouped"], [0])

    def test_frozen_and_per_connection(self):
        pinned, _ = self.away(4)
        pins = pins_of("main", pinned)
        d = self.run_one([grp()], pins, {"offline": True, "regroup": {"main"}})
        self.assertEqual(d["moves"], [])
        self.assertEqual([e for e in d["events"] if e["kind"] == "group_regrouped"],
                         [{"kind": "group_regrouped", "group": "main", "role": "abroad", "moved": 0, "frozen": "offline"}])
        # without the Russian addresses: a Russian group frozen, a foreign one goes on
        pins["groups"][RU] = pins_of(RU, dict(("r" + k, v) for k, v in pinned.items()))["groups"][RU]
        d = self.run_one([grp(), grp(RU, role="ru")], pins, {"ru_offline": True, "regroup": {"main", RU}})
        ev = dict((e["group"], e) for e in d["events"] if e["kind"] == "group_regrouped")
        self.assertEqual(ev[RU]["frozen"], "ru_offline")
        self.assertEqual(ev["main"]["moved"], 4)
        self.assertNotIn("frozen", ev["main"])
        self.assertEqual(set(m["group"] for m in d["moves"]), {"main"})
        # «по соединениям»: no pins, nothing to lay out
        d = self.run_one([grp(per_connection=True)], pins_of("main", {}), {"regroup": {"main"}},
                         devs={"main": [{"id": "d-1"}]})
        self.assertEqual(d["moves"], [])


class TakeTest(RootCase):
    """The requests on the disk (§1 решение 4, З-5)."""
    POL = {"groups": [{"id": "main"}, {"id": RU}]}

    def put(self, gid, age=0.0):
        p = os.path.join(paths.gw_regroup_dir(), gid)
        util.atomic_write(p, "{}\n", 0o600)
        if age:
            t = time.time() - age
            os.utime(p, (t, t))
        return p

    def names(self):
        return sorted(os.listdir(paths.gw_regroup_dir()))

    def test_a_request_during_the_round_is_not_lost(self):
        """A taken (``main.work``); while the round runs B and A again are written -> after the round B and the new A
        are there, the ``.work`` is gone; the next round takes both. (One shared file read-changed-written would lose
        the request of the panel that came between the read and the write.)"""
        self.put("main")
        got, taken = collect.regroup_take(self.POL, time.time())
        self.assertEqual(got, {"main"})
        self.assertEqual(self.names(), ["main.work"])
        self.put(RU)
        self.put("main")
        collect.regroup_done(taken)
        self.assertEqual(self.names(), sorted(["main", RU]))
        got, taken = collect.regroup_take(self.POL, time.time())
        self.assertEqual(got, {"main", RU})
        collect.regroup_done(taken)
        self.assertEqual(self.names(), [])

    def test_old_foreign_and_temp(self):
        self.put("main", age=collect.REGROUP_MAX_AGE + 1)               # the collector stood: dropped
        self.put("g-ffffff")                                             # no such group now: dropped
        self.put("junk")
        tmp = os.path.join(paths.gw_regroup_dir(), ".tmp.abc")
        util.atomic_write(tmp, "", 0o600)                                # a write in progress: never touched
        left = self.put(RU) + collect.WORK                               # a .work of a failed round: taken again
        os.rename(self.put(RU), left)
        got, taken = collect.regroup_take(self.POL, time.time())
        self.assertEqual(got, {RU})
        self.assertEqual(self.names(), [".tmp.abc", RU + ".work"])
        collect.regroup_done(taken)
        self.assertEqual(self.names(), [".tmp.abc"])

    def test_late_requests_are_told(self):
        """П-4: a request dropped for its age goes into ``late`` (the round tells it in the feed) — a foreign name never."""
        self.put("main", age=collect.REGROUP_MAX_AGE + 1)
        self.put("g-ffffff", age=collect.REGROUP_MAX_AGE + 1)
        late = []
        got, taken = collect.regroup_take(self.POL, time.time(), late=late)
        self.assertEqual((got, late, self.names()), (set(), ["main"], []))

    def test_no_directory(self):
        self.assertEqual(collect.regroup_take(self.POL, time.time()), (set(), []))


class RegroupCase(PolicyCase):
    def setUp(self):
        super().setUp()
        self.both_ok()
        self._mem = collect.MEM

    def tearDown(self):
        collect.MEM = self._mem
        super().tearDown()

    def tier(self):
        st = state.load()
        g = P.group(pops._base(st), "main")
        return [t for t in g["tiers"] if t.get("exits")][0]

    def away(self):
        """The device pinned to the exit of its tier that is not its «home» -> (away, home)."""
        from kaskad.gw import pins as PN
        t = self.tier()
        home = assign._choose(self.did, [e for e in t["exits"] if e in (self.vid, self.aid)], t, {})
        away = self.aid if home == self.vid else self.vid
        doc = PN.read()
        g = doc.setdefault("groups", {}).setdefault("main", {"pins": {}, "tier_good_since": {}})
        g["pins"] = {self.did: {"exit": away, "tier": t["id"], "since": time.time() - 3600, "why": "new"}}
        g["active_tier"] = t["id"]
        PN._write(paths.pins_json(), doc)
        collect.MEM = collect.Mem()                    # a new collector process: the pins of the file
        return away, home

    def dir_names(self):
        try:
            return sorted(os.listdir(paths.gw_regroup_dir()))
        except OSError:
            return []

    def regroup(self, gid="main", body=None, status=200):
        r = self.call("POST", "/api/gw/groups/%s/regroup" % gid, body if body is not None else {})
        self.assertEqual(r.status, status, r)
        return r.json()


class RegroupTickTest(RegroupCase):
    def test_request_and_one_round_move_to_home(self):
        """SPEC F14 «Тесты» п. 1–2: night off, the device away from its «home» exit. A round without the request — it
        stays; the dry run — the move, pins.json and the directory untouched; the request — the file (0600), one
        round — the device on its «home» exit, the same move the dry run showed, the ``.work`` gone, the event in the
        round, no conntrack flush for a «rebalance», ``night_on`` not written."""
        away, home = self.away()
        self.collect()
        self.assertEqual(self.pinned(), away)                         # night off: nobody moves on its own
        away, home = self.away()
        before = self.read("/var/lib/kaskad/gw/pins.json")
        dry = self.regroup(body={"dry": True})
        self.assertTrue(dry["dry"])
        self.assertTrue(dry["approx"])
        self.assertEqual([(m["device"], m["from"], m["to"], m["why"]) for m in dry["moves"]],
                         [(self.did, away, home, "rebalance")])
        self.assertEqual((dry["before"], dry["after"]), ({away: 1}, {home: 1}))
        self.assertIn("1", dry["text"])
        self.assertEqual(self.read("/var/lib/kaskad/gw/pins.json"), before)
        self.assertEqual(self.dir_names(), [])
        res = self.regroup()
        self.assertEqual((res["accepted"], res["group"], res["wait_s"]), (True, "main", pops.REGROUP_WAIT_S))
        p = os.path.join(paths.gw_regroup_dir(), "main")
        self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)
        self.regroup()                                                # a repeat — the same one file
        self.assertEqual(self.dir_names(), ["main"])
        with mock.patch.object(net, "flush_devices", wraps=net.flush_devices) as fl:
            out = self.collect()
        self.assertEqual(self.pinned(), home)
        moved = [(m["device"], m["from"], m["to"], m["why"]) for m in out["exits"]["moves"] if m["group"] == "main"]
        self.assertEqual(moved, [(m["device"], m["from"], m["to"], m["why"]) for m in dry["moves"]])
        self.assertEqual([e for e in out["exits"]["events"] if e["kind"] == "group_regrouped"],
                         [{"kind": "group_regrouped", "group": "main", "role": "abroad", "moved": 1}])
        self.assertEqual(self.dir_names(), [])
        fl.assert_not_called()
        self.assertIsNone(collect.MEM.decision["groups"]["main"]["night_on"])
        with open(paths.live_json(), encoding="utf-8") as f:
            feed = [e for e in json.load(f).get("events") or [] if e.get("kind") == "group_regrouped"]
        self.assertEqual(len(feed), 1)
        self.assertIn("1", feed[0]["text"])


class RegroupFixTest(RegroupCase):
    """REV-3315-g1 П-1, П-4."""

    def test_p1_the_preview_counts_the_rebalance_only(self):
        """П-1: ``moved`` of the dry run — the devices «Разложить заново» takes home (as the event counts); the other
        moves of the round apart (``also``)."""
        d = {"groups": {"main": {"steps": {}, "pins": {}}},
             "moves": [{"group": "main", "device": "d1", "from": "e2", "to": "e1", "why": "rebalance"},
                       {"group": "main", "device": "d2", "from": "e1", "to": "e2", "why": "failover"},
                       {"group": "g-111111", "device": "d3", "from": "e1", "to": "e2", "why": "rebalance"}]}
        with mock.patch.object(pops, "_decide", return_value=d):
            res = pops.regroup_preview(state.load(), "main")
        self.assertEqual((res["moved"], res["also"], len(res["moves"])), (1, 1, 2))

    def test_p4_a_late_request_is_told_in_the_feed(self):
        """П-4: the collector stood longer than REGROUP_MAX_AGE — the request is dropped (no sudden move later) and the
        round says so: ``group_regrouped`` with ``late``, the text «нажмите ещё раз». Without the fix — silence."""
        away, home = self.away()
        self.regroup()
        p = os.path.join(paths.gw_regroup_dir(), "main")
        t = time.time() - collect.REGROUP_MAX_AGE - 5
        os.utime(p, (t, t))
        out = self.collect()
        self.assertEqual(self.pinned(), away)
        self.assertEqual([e for e in out["exits"]["events"] if e["kind"] == "group_regrouped"],
                         [{"kind": "group_regrouped", "group": "main", "role": "abroad", "moved": 0, "late": True}])
        with open(paths.live_json(), encoding="utf-8") as f:
            feed = [e for e in json.load(f).get("events") or [] if e.get("kind") == "group_regrouped"]
        self.assertEqual(len(feed), 1)
        self.assertIn("ещё раз", feed[0]["text"])
        a = {"group": "main", "moved": 0, "late": True}
        self.assertIn("Press it again", live.event_text({"kind": "group_regrouped", "key": "gw_live.event.group_regrouped", "args": a},
                                                       "en"))


class RegroupRefusalTest(RegroupCase):
    def test_bad_ids_write_nothing(self):
        for gid in ("../x", "g-ffffff", "main/../../etc", "", None, 5):
            with self.subTest(gid=gid), self.assertRaises(pops.Refused) as c:
                pops.group_regroup(self.w.runner, gid)
            self.assertEqual((c.exception.key, c.exception.status), ("gone", 404))
        self.assertEqual(self.dir_names(), [])
        self.assertFalse(os.path.exists(os.path.join(self.root, "x")))
        self.assertEqual(self.call("POST", "/api/gw/groups/g-ffffff/regroup", {}).status, 404)
        self.assertEqual(self.call("POST", "/api/gw/groups/..%2Fx/regroup", {}).status, 404)
        doc = self.regroup(body={"dry": True, "rev": 1}, status=400)       # no other field
        self.assertEqual(doc["code"], "field")
        self.assertEqual(self.regroup(body={"dry": "yes"}, status=400)["ok"], False)

    def outage(self, kind):
        now = int(time.time())
        with open(paths.live_json(), encoding="utf-8") as f:
            doc = json.load(f)
        doc = dict(doc, v=live.V, ts=now, events=list(doc.get("events") or []) +
                   [{"kind": kind, "ts": now, "key": "gw_live.event." + kind, "args": {"since": now}}])
        util.atomic_write(paths.live_json(), json.dumps(doc), 0o600)

    def test_per_connection_and_frozen(self):
        with state.locked():
            st = state.load()
            doc = pops._base(st)
            P.group(doc, "main")["per_connection"] = True
            st["gateway"]["exit_policy"] = doc
            state.save(st)
        for body in ({"dry": True}, {}):
            self.assertEqual(self.regroup(body=body, status=409)["code"], "regroup_nothing")
        with state.locked():
            st = state.load()
            P.group(st["gateway"]["exit_policy"], "main")["per_connection"] = False
            state.save(st)
        self.outage("node_offline")
        for body in ({"dry": True}, {}):
            doc = self.regroup(body=body, status=409)
            self.assertEqual(doc["code"], "regroup_frozen")
        self.assertEqual(self.dir_names(), [])

    def test_ru_offline_freezes_only_the_russian_group(self):
        ru = self.add_group(name="Дом", role="ru")
        self.outage("node_ru_offline")
        self.assertEqual(self.regroup(ru, body={"dry": True}, status=409)["code"], "regroup_frozen_ru")
        self.assertEqual(self.regroup(ru, status=409)["code"], "regroup_frozen_ru")
        self.assertTrue(self.regroup("main")["accepted"])
        self.assertEqual(self.dir_names(), ["main"])


class TextTest(unittest.TestCase):
    def said(self, args, lang):
        return live.event_text({"kind": "group_regrouped", "key": "gw_live.event.group_regrouped", "args": args}, lang)

    def test_ru_en_numbers(self):
        for n, ru, en in ((1, "переехало 1 устройство", "1 device moved"), (2, "переехали 2 устройства", "2 devices"),
                          (5, "переехали 5 устройств", "5 devices"), (21, "переехало 21 устройство", "21 devices")):
            a = {"group": "g-111111", "group_name": "Стриминг", "moved": n}
            self.assertIn(ru, self.said(a, "ru"))
            self.assertIn("«Стриминг»", self.said(a, "ru"))
            self.assertIn(en, self.said(a, "en"))
        a = {"group": "main", "moved": 0}
        self.assertIn("уже на своих выходах", self.said(a, "ru"))
        self.assertIn("already on its own exit", self.said(a, "en"))
        for frozen in ("offline", "ru_offline"):
            a = {"group": "main", "moved": 0, "frozen": frozen}
            for lang in ("ru", "en"):
                text = self.said(a, lang)
                self.assertNotIn("{", text)
                if lang == "en":
                    self.assertIsNone(re.search("[А-Яа-яЁё]", text.replace("Main", "")), text)
        for n in (1, 2, 5, 21):
            for lang in ("ru", "en"):
                self.assertNotIn("{", pops.trn("gw_policy_ops.regroup_preview", n, lang))

    def test_the_bot_is_silent(self):
        from kaskad.bot import monitor
        self.assertFalse(monitor.push_on("group_regrouped", {}))
        self.assertIn("group_regrouped", live.FEED_KINDS)


if __name__ == "__main__":
    unittest.main()
