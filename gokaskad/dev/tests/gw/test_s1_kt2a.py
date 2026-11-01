"""КТ2, находка стенда К2-4 (k3/stand/logs/T-KT2.md §3): «по соединениям», VLESS-выходы мертвы, AmneziaWG живы — у устройств
ядра (WG/AWG) около половины новых соединений без интернета. Правило ``numgen random mod N map {…}`` в gw_pre рисовалось
только полным применением (``net.spread_rules`` из ``plan()["spread"]``); смерть или возврат выхода полного применения не
вызывает — ячейки 0x200 (tproxy-in → gw-vless, живых в нём нет: block) оставались.

Теперь ``net.update_map`` (такт сборщика) приводит правило к ``spread_rules(plan)`` в той же транзакции, что gw_route:
заменяет по handle, ставит перед охраной 0x2fe или удаляет. Оракул ячеек — ``oracle_spread`` из test_s1_kt1a (по ТЗ §4.5,
не ``assign.spread_cands``) и метки пула; ещё один — «как нарисовало бы полное применение» (``net.rules``)."""

import itertools
import json
import random
import re
import time
import unittest
from unittest import mock

from kaskad import nft, sysrun, util
from kaskad.errors import KaskadError
from kaskad.gw import exits, net, peek
from kaskad.i18n import tr
from tests.gw.test_ru_invariant import oracle_may_go_direct
from tests.gw.test_s1_kt1a import RealTargetCase, oracle_spread

IP = "10.77.1.10"                                      # d-00000001, a device of the kernel (WireGuard)


def numgen_cells(case):
    """The marks of the numgen rules of the LIVE gw_pre (the fake table), one list per rule."""
    out = []
    for r in case.w.nft.table()["chains"]["gw_pre"]["rules"]:
        got = net._numgen_marks(r["expr"])
        if got is not None:
            out.append(got)
    return out


def pre_texts(case):
    return [r["text"] for r in case.w.nft.table()["chains"]["gw_pre"]["rules"]]


class SpreadHotCase(RealTargetCase):
    def setUp(self):
        super().setUp()
        self.node()
        self.per_connection(fail="block")
        net.spread_reset()
        self.marks = dict((e["id"], exits.MARK_XRAY if exits.is_xray(e) else e["mark"]) for e in self.pool)

    def full_apply(self, dec):
        """``nft.apply`` (``ops._apply``) with the decision ``dec`` — what the stand had before the exits died."""
        with mock.patch.object(exits, "decision", lambda *a, **kw: dec):
            nft.apply(self.st, self.w.runner)

    def tick(self, dec):
        """The collector's round: ``update_map`` with the decision of the tick in memory (collect.exits_tick)."""
        return net.update_map(self.w.runner, self.st, live=nft.read_live(self.w.runner), decision=dec)

    def want_cells(self, states, tiers):
        return sorted(self.marks[e] for e in oracle_spread(states, tiers))

    def assert_as_full(self, dec):
        """gw_pre after the hot change = gw_pre a full apply would draw with the same decision (order included)."""
        p = net.plan(self.st, self.w.runner, decision=dec)
        self.assertEqual(pre_texts(self), dict(net.rules(self.st, p))["gw_pre"])

    def journal(self):
        lines = []
        return lines, mock.patch.object(util, "log_action", lambda line, *a, **kw: lines.append(line))

    def runs(self, hook):
        """The runner with ``hook(argv, input)`` → a result or None (the real nft); the calls seen."""
        seen = []
        real = self.w.runner.run

        def run(argv, input=None, **kw):
            seen.append((list(argv), input))
            got = hook(argv, input)
            return got if got is not None else real(argv, input=input, **kw)
        return seen, mock.patch.object(self.w.runner, "run", run)

    def nft_f(self, seen):
        return [i for a, i in seen if a[:2] == [nft.NFT, "-f"]]


class DeadVlessTest(SpreadHotCase):
    def test_vless_dead_awg_alive_no_failed_cell_for_the_kernel(self):
        # the stand: every exit «ok» at the full apply (cells 0x200 among them); then the VLESS ones «не работает»
        ok = self.dec()
        self.full_apply(ok)
        self.assertEqual(self.w.nft.elements("gw_route")[IP], net.MARK_SPREAD)
        self.assertEqual([sorted(c) for c in numgen_cells(self)], [self.want_cells(
            {self.vid: "ok", self.bid: "ok", self.aid: "ok"}, [[self.vid, self.aid, self.bid]])])
        self.assertIn(exits.MARK_XRAY, numgen_cells(self)[0])
        dead = self.dec(states={self.vid: "off", self.bid: "off", self.aid: "ok"})
        self.assertTrue(self.tick(dead))
        cells = numgen_cells(self)
        self.assertEqual(cells, [[self.marks[self.aid]]])                  # 0 failed cells: AmneziaWG only
        self.assertEqual(self.w.nft.elements("gw_route")[IP], net.MARK_SPREAD)    # the device stays on the spread
        self.assert_as_full(dead)
        self.assertFalse(self.tick(dead))                                  # nothing more to change

    def test_the_exit_back_brings_its_cells_back(self):
        dead = self.dec(states={self.vid: "off", self.bid: "off", self.aid: "ok"})
        self.full_apply(dead)
        self.assertEqual(numgen_cells(self), [[self.marks[self.aid]]])
        back = self.dec(states={self.vid: "ok", self.bid: "off", self.aid: "ok"})
        self.assertTrue(self.tick(back))
        self.assertEqual([sorted(c) for c in numgen_cells(self)], [sorted([exits.MARK_XRAY, self.marks[self.aid]])])
        self.assert_as_full(back)

    def test_all_down_and_back_after_a_full_apply_in_between(self):
        # every exit down — the device goes to the last step and the rule goes with it; a full apply then draws no
        # numgen; the exits come back — the rule is put back before the guard (before: the guard blocked 0x2fe)
        self.full_apply(self.dec())
        down = self.dead()
        self.assertTrue(self.tick(down))
        self.assertEqual(self.w.nft.elements("gw_route")[IP], exits.MARK_BLOCK)
        self.assertEqual(numgen_cells(self), [])
        self.assert_as_full(down)
        self.full_apply(down)
        back = self.dec(states={self.vid: "off", self.bid: "off", self.aid: "ok"})
        self.assertTrue(self.tick(back))
        self.assertEqual(self.w.nft.elements("gw_route")[IP], net.MARK_SPREAD)
        self.assertEqual(numgen_cells(self), [[self.marks[self.aid]]])
        self.assert_as_full(back)

    def test_every_state_of_three_exits_over_two_tiers(self):
        # the order of the states as the collector could go through them: after each tick the cells of the kernel are
        # those of the spec (oracle_spread), never a dead leg, never 0x200 without a good xray exit, and gw_pre equals
        # a full apply
        doc = self.per_connection(exits_=[self.aid, self.vid])
        doc["groups"][0]["tiers"].append({"id": "t2", "exits": [self.bid]})
        tiers = [[self.aid, self.vid], [self.bid]]
        self.full_apply(self.dec())
        n = 0
        for sv in itertools.product(("ok", "degraded", "standby", "off", "unknown"), repeat=3):
            states = dict(zip((self.vid, self.bid, self.aid), sv))
            dec = self.dec(states=states, gw_all=exits.tag(self.aid))
            self.tick(dec)
            mark = self.w.nft.elements("gw_route").get(IP)
            cells = numgen_cells(self)
            if mark == net.MARK_SPREAD:
                n += 1
                want = self.want_cells(states, tiers)
                self.assertTrue(want, states)
                self.assertEqual([sorted(c) for c in cells], [want], states)
                good = set(self.marks[e] for e in oracle_spread(states, tiers))
                self.assertFalse([m for m in cells[0] if m not in good], states)
            else:
                self.assertEqual(cells, [], states)
            self.assert_as_full(dec)
        self.assertGreater(n, 50)


class ByDeviceTest(SpreadHotCase):
    def test_by_device_never_lists_gw_pre_nor_draws_numgen(self):
        # «по устройствам» (the mode off): the tick reads no chain and the table has no numgen — as before
        self.per_connection()["groups"][0]["per_connection"] = False
        dec = self.dec(pins={"d-00000001": self.aid})
        self.full_apply(dec)
        seen = []
        real = self.w.runner.run

        def run(argv, input=None, **kw):
            seen.append(list(argv))
            return real(argv, input=input, **kw)
        with mock.patch.object(self.w.runner, "run", run):
            self.tick(self.dec(pins={"d-00000001": self.aid},
                               states={self.vid: "off", self.bid: "off", self.aid: "ok"}))
        self.assertFalse([a for a in seen if a[:4] == [nft.NFT, "-j", "list", "chain"]])
        self.assertEqual(numgen_cells(self), [])
        self.assertEqual(self.w.nft.elements("gw_route")[IP], self.marks[self.aid])


class RefusedTest(SpreadHotCase):
    def test_a_refused_rule_lets_the_map_go_on_and_is_told_once(self):
        self.full_apply(self.dec())
        real = self.w.runner.run

        def run(argv, input=None, **kw):
            if argv[:2] == [nft.NFT, "-f"] and input and " handle " in input:
                return real([nft.NFT, "-f", nft.STDIN], input="nonsense\n", **kw)
            return real(argv, input=input, **kw)
        lines = []
        down = self.dead()
        with mock.patch.object(self.w.runner, "run", run), \
                mock.patch.object(util, "log_action", lambda line, *a, **kw: lines.append(line)):
            self.assertTrue(self.tick(down))                               # the map changed without the rule
            self.assertEqual(self.w.nft.elements("gw_route")[IP], exits.MARK_BLOCK)
            self.tick(down)
            self.tick(down)
        told = [ln for ln in lines if "по соединениям" in ln]
        self.assertEqual(len(told), 1, lines)
        self.assertEqual(len(numgen_cells(self)), 1)                       # left as it was, retried next round
        self.tick(down)
        self.assertEqual(numgen_cells(self), [])
        self.assertIsNone(net.SPREAD_FAIL["err"])

    def test_unreadable_numgen_is_rewritten_not_doubled(self):
        self.full_apply(self.dec())
        dead = self.dec(states={self.vid: "off", self.bid: "off", self.aid: "ok"})
        with mock.patch.object(net, "_numgen_marks", lambda ex: [] if net._is_mark_match(
                ex[0] if ex else None, {"meta": {"key": "mark"}}, net.MARK_SPREAD) and "map" in str(ex) else None):
            self.tick(dead)
            self.tick(dead)
        self.assertEqual(numgen_cells(self), [[self.marks[self.aid]]])     # one rule, never a second one


PRE_LIST = [nft.NFT, "-j", "list", "chain", nft.TABLE_FAMILY, nft.TABLE_NAME, "gw_pre"]


class UnreadTest(SpreadHotCase):
    """REV-s1-kt2a З-1 (а): gw_pre unreadable (the listing, its JSON, a rule without an integer handle) — one journal
    line per reason, not every tick, and a retry next tick — even when nothing else would bring the rule back."""

    def test_a_refused_listing_is_told_once_and_retried(self):
        self.full_apply(self.dec())
        lines, jr = self.journal()
        broken = {"on": True}
        seen, rn = self.runs(lambda a, i: _Res(False, "", "Error: No such file or directory")
                             if broken["on"] and a == PRE_LIST else None)
        down = self.dead()
        with rn, jr:
            self.assertTrue(self.tick(down))                   # the map goes on: the device to its last step
            self.assertEqual(self.w.nft.elements("gw_route")[IP], exits.MARK_BLOCK)
            self.tick(down)
            self.tick(down)
            told = [ln for ln in lines if tr("gw_net.log.spread_unread", error="").split("(")[0].strip() in ln]
            self.assertEqual(len(told), 1, lines)
            self.assertIn("No such file", told[0])
            self.assertEqual(len(numgen_cells(self)), 1)       # left as it was
            self.assertIsNotNone(net.SPREAD_FAIL["err"])
            broken["on"] = False
            # nothing on 0x2fe now, in the plan or the table: only the retry brings the rule in line (deleted)
            self.assertNotIn(net.MARK_SPREAD, self.w.nft.elements("gw_route").values())
            self.assertTrue(self.tick(down))
            self.assertEqual(numgen_cells(self), [])
            self.assertIsNone(net.SPREAD_FAIL["err"])
            self.assertFalse(self.tick(down))
            self.assertEqual(len([ln for ln in lines if "gw_pre" in ln]), 1, lines)

    def test_a_handle_not_an_integer_is_a_reason_of_its_own(self):
        self.full_apply(self.dec())
        lines, jr = self.journal()
        mode = {"m": "handle"}
        real = self.w.runner.run

        def run(argv, input=None, **kw):
            res = real(argv, input=input, **kw)
            if argv == PRE_LIST and mode["m"] == "handle" and res.ok:
                doc = json.loads(res.out)
                for it in doc["nftables"]:
                    if "rule" in it:
                        it["rule"]["handle"] = str(it["rule"]["handle"])
                return _Res(True, json.dumps(doc), "")
            if argv == PRE_LIST and mode["m"] == "json":
                return _Res(True, "{not json", "")
            return res
        dead = self.dec(states={self.vid: "off", self.bid: "off", self.aid: "ok"})
        with mock.patch.object(self.w.runner, "run", run), jr:
            self.tick(dead)
            self.tick(dead)
            mode["m"] = "json"
            self.tick(dead)
            self.tick(dead)
            told = [ln for ln in lines if "gw_pre" in ln]
            self.assertEqual(len(told), 2, lines)                  # one per reason
            self.assertIn("handle", told[0])
            self.assertIn("JSON", told[1])
            self.assertIn(exits.MARK_XRAY, numgen_cells(self)[0])  # not changed while unreadable
            mode["m"] = None
            self.assertTrue(self.tick(dead))
        self.assertEqual(numgen_cells(self), [[self.marks[self.aid]]])
        self.assert_as_full(dead)
        self.assertIsNone(net.SPREAD_FAIL["err"])

    def test_gw_pre_is_listed_once_per_tick(self):
        # REV-s1-kt2a П-2: the collector's live is peek.read (no rules) — spread_ok and the rule share one listing
        self.full_apply(self.dec())
        seen, rn = self.runs(lambda a, i: None)
        dead = self.dec(states={self.vid: "off", self.bid: "off", self.aid: "ok"})
        with rn:
            live = peek.read(self.w.runner)
            del seen[:]
            self.assertTrue(net.update_map(self.w.runner, self.st, live=live, decision=dead))
        self.assertEqual(len([a for a, _ in seen if a == PRE_LIST]), 1, [a for a, _ in seen])
        self.assertEqual(numgen_cells(self), [[self.marks[self.aid]]])


def _Res(ok, out, err):
    return sysrun.Result(["nft"], 0 if ok else 1, out, err)


def unrecognizable(ex):
    """``_numgen_marks`` as if the live nft -j drew the map in a form of its own: a numgen rule, its map not read."""
    got = REAL_MARKS(ex)
    return [] if got else got


REAL_MARKS = net._numgen_marks


class UnrecognizedTest(SpreadHotCase):
    """REV-s1-kt2a З-1 (б), SEC З-2: the map of the live numgen rule not recognized — rewritten once (one journal line),
    then only when the good exits change or someone else rewrote the rule; never every tick."""

    def test_rewritten_once_not_every_tick(self):
        self.full_apply(self.dec())
        lines, jr = self.journal()
        seen, rn = self.runs(lambda a, i: None)
        dead = self.dec(states={self.vid: "off", self.bid: "off", self.aid: "ok"})
        with mock.patch.object(net, "_numgen_marks", unrecognizable), rn, jr:
            got = [self.tick(dead) for _ in range(4)]
            self.assertEqual(got, [True, False, False, False])
            self.assertEqual(len([i for i in self.nft_f(seen) if " handle " in (i or "")]), 1)
            self.assert_as_full(dead)
            told = [ln for ln in lines if "не распознана" in ln or "not recognized" in ln]
            self.assertEqual(len(told), 1, lines)
            # the good exits change: rewritten (a real difference), still no second journal line
            back = self.dec()
            self.assertTrue(self.tick(back))
            self.assert_as_full(back)
            self.assertFalse(self.tick(back))
            self.assertEqual(len([ln for ln in lines if "не распознана" in ln or "not recognized" in ln]), 1)

    def test_a_full_apply_in_between_is_seen(self):
        # the tick wrote the rule for [AWG]; a full apply then drew [0x200, AWG] (another process: no memory of it);
        # the tick still wants [AWG] — the rule is not the one it saw: rewritten
        dead = self.dec(states={self.vid: "off", self.bid: "off", self.aid: "ok"})
        self.full_apply(self.dec())
        with mock.patch.object(net, "_numgen_marks", unrecognizable):
            self.assertTrue(self.tick(dead))
            self.assertFalse(self.tick(dead))
            self.full_apply(self.dec())
            self.assertIn(exits.MARK_XRAY, REAL_MARKS_OF(self)[0])
            self.assertTrue(self.tick(dead))
            self.assert_as_full(dead)
            self.assertFalse(self.tick(dead))

    def test_a_full_apply_of_the_same_cells_is_left(self):
        dead = self.dec(states={self.vid: "off", self.bid: "off", self.aid: "ok"})
        self.full_apply(self.dec())
        with mock.patch.object(net, "_numgen_marks", unrecognizable):
            self.assertTrue(self.tick(dead))
            self.full_apply(dead)                              # the same text: the same rule as nft lists it
            self.assertFalse(self.tick(dead))


def REAL_MARKS_OF(case):
    return [m for m in (REAL_MARKS(r["expr"]) for r in case.w.nft.table()["chains"]["gw_pre"]["rules"])
            if m is not None]


class ParserTest(unittest.TestCase):
    MM = {"meta": {"key": "mark"}}

    def rule(self, numgen, data):
        return [{"match": {"op": "==", "left": self.MM, "right": net.MARK_SPREAD}},
                {"mangle": {"key": self.MM, "value": {"map": {"key": {"numgen": numgen}, "data": {"set": data}}}}}]

    def test_numgen_not_a_dict_is_unrecognized_not_a_crash(self):
        # REV-s1-kt2a П-3
        for ng in (["random", 2], "random", 7, None):
            self.assertEqual(net._numgen_marks(self.rule(ng, [[0, 0x102]])), [], ng)

    def test_a_huge_mod_is_refused_by_its_size_first(self):
        # SEC-s1-kt2a П-2: no list of ``mod`` items
        t = time.time()
        with mock.patch("builtins.range", side_effect=AssertionError("range of the live mod")):
            got = net._numgen_marks(self.rule({"mode": "random", "mod": 4294967295, "offset": 0}, [[0, 0x102]]))
        self.assertEqual(got, [])
        self.assertLess(time.time() - t, 1)
        self.assertEqual(net._numgen_marks(self.rule({"mode": "random", "mod": 2, "offset": 0},
                                                     [[1, 0x200], [0, 0x102]])), [0x102, 0x200])


class TextTest(SpreadHotCase):
    def test_the_texts_tell_the_block_too(self):
        # REV-s1-kt2a П-1: the rule not updated — new connections on the previous rule OR blocked (a device moved to
        # 0x2fe with no numgen meets the guard), in both languages
        from kaskad.i18n import lookup as catalog_text
        for lang, word in (("ru", "блокируются"), ("en", "blocked")):
            for key in ("gw_net.log.spread_failed", "gw_net.log.spread_unread"):
                self.assertIn(word, catalog_text(key, lang), (lang, key))
                self.assertIn("{error}", catalog_text(key, lang))
            self.assertTrue(catalog_text("gw_net.log.spread_unrecognized", lang))

    def test_no_rule_line_when_the_map_alone_is_refused_too(self):
        self.full_apply(self.dec())
        lines, jr = self.journal()
        seen, rn = self.runs(lambda a, i: _Res(False, "", "Error: Could not process rule")
                             if a[:2] == [nft.NFT, "-f"] else None)
        with rn, jr:
            with self.assertRaises(KaskadError):
                self.tick(self.dead())
        self.assertFalse([ln for ln in lines if "по соединениям" in ln or "per connection" in ln], lines)
        self.assertIsNotNone(net.SPREAD_FAIL["err"])               # retried next tick


# ── SEC-s1-kt2a З-1: the invariant of the kernel on the fake table ─────────
FORBIDDEN = (exits.MARK_DIRECT, exits.MARK_NODE, 0, net.MARK_SPREAD, exits.MARK_BLOCK)
NUMGEN_RE = re.compile(r"numgen random mod (\d+) map \{ ([^}]*) \}")


def oracle_cells(text):
    """The cells of a numgen rule by its text (the oracle's own reading): [mark…] by their numbers."""
    m = NUMGEN_RE.search(text)
    pairs = [x.split(":") for x in m.group(2).split(",")]
    cells = dict((int(k.strip(), 0), int(v.strip(), 0)) for k, v in pairs)
    assert sorted(cells) == list(range(int(m.group(1)))), text
    return [cells[i] for i in range(len(cells))]


class KernelInvariantTest(SpreadHotCase):
    """A full apply, then ticks of random states of the exits (a full apply now and then, as the owner's changes): after
    EVERY step the live table holds
    * numgen cells only 0x200 (a good xray exit) and the marks of good AmneziaWG exits — never 0x201, 0x202, 0, 0x2fe,
      0x2ff;
    * at most one numgen rule, the guard 0x2fe → 0x2ff right after it, before the rules «Россия напрямую»;
    * 0x201 in gw_route only for a device that may go out directly by its route (``oracle_may_go_direct`` of D-136:
      «напрямую», its fail or the last step of its group «напрямую»)."""
    STATES = ("ok", "degraded", "standby", "off", "unknown")

    def check(self, states, tiers, fail, where):
        pre = pre_texts(self)
        ng = [i for i, t in enumerate(pre) if "numgen" in t]
        guard = [i for i, t in enumerate(pre) if t == net.SPREAD_GUARD_PRE]
        ru = [i for i, t in enumerate(pre) if "@%s " % net.RD in t]
        self.assertEqual(len(guard), 1, (where, pre))
        self.assertLessEqual(len(ng), 1, (where, pre))
        self.assertTrue(ru, where)
        self.assertLess(guard[0], min(ru), (where, pre))
        if ng:
            self.assertEqual(ng[0] + 1, guard[0], (where, pre))
            good = oracle_spread(states, tiers)
            ok = set(self.marks[e] for e in good)
            cells = oracle_cells(pre[ng[0]])
            self.assertFalse([c for c in cells if c in FORBIDDEN], (where, cells))
            self.assertFalse([c for c in cells if c not in ok], (where, cells, sorted(ok)))
        devs = dict((ip, d) for d in self.st["gateway"]["devices"] for ip in (d.get("ips") or {}).values())
        for ip, m in self.w.nft.elements("gw_route").items():
            self.assertNotIn(m, (exits.MARK_NODE, 0), (where, ip))
            if m == exits.MARK_DIRECT:
                self.assertTrue(oracle_may_go_direct(self.st, devs[ip]), (where, ip, fail))

    def test_full_apply_and_ticks_keep_the_kernel_inside(self):
        rnd = random.Random(2304)
        seen = {"numgen": 0, "direct": 0, "steps": 0, "pool_direct": 0}
        for run in range(6):
            fail = ("block", "direct")[run % 2]
            two = run >= 2
            doc = self.per_connection(fail=fail, exits_=[self.aid, self.vid] if two else None)
            tiers = [[self.vid, self.bid, self.aid]]
            if two:
                doc["groups"][0]["tiers"].append({"id": "t2", "exits": [self.bid]})
                tiers = [[self.aid, self.vid], [self.bid]]

            def rand():
                st = dict((e, rnd.choice(self.STATES)) for e in (self.vid, self.bid, self.aid))
                return st, self.dec(states=st, gw_all=exits.tag(self.aid))
            states, dec = rand()
            self.full_apply(dec)
            self.check(states, tiers, fail, (run, "full", states))
            for k in range(25):
                states, dec = rand()
                if rnd.random() < 0.15:
                    self.full_apply(dec)
                else:
                    self.tick(dec)
                self.check(states, tiers, fail, (run, k, states))
                seen["steps"] += 1
                seen["numgen"] += any("numgen" in t for t in pre_texts(self))
                seen["direct"] += exits.MARK_DIRECT in self.w.nft.elements("gw_route").values()
                seen["pool_direct"] += self.w.nft.elements("gw_route").get(IP) == exits.MARK_DIRECT
        self.assertGreater(seen["numgen"], 40, seen)            # not vacuous: the rule is there, and «напрямую» too
        self.assertGreater(seen["direct"], 0, seen)
        self.assertGreater(seen["pool_direct"], 0, seen)          # the last step «напрямую» of the spread device


if __name__ == "__main__":
    unittest.main()
