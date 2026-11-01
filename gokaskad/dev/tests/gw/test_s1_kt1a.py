"""КТ1, находки стенда (k3/stand/logs/T-KT1.md §9–10): the routing of xray and of the kernel when the exits fail.

* Н7 — «по соединениям» (§4.5): a VLESS / SOCKS5 user on the pool went to ``fail-block`` with the exits alive (the
  render knew no ``gw-vless`` answer of ``exits.target``);
* S26 — «по соединениям» with every exit down: the devices stayed on the spread (the kernel — numgen over dead legs
  or the block mark, xray — ``gw-vless`` falling back to block) while the panel said «при отказе — напрямую»: now the
  group's last step, as for a pinned device (``direct`` — directly; ``block`` — the block of the last step, Russia
  stays direct for ru_direct);
* Н6 — every exit down (``gw-all`` → block): the names of Russia do not resolve — accepted as Р-Г9
  (SPEC-stage1-exit-groups §5: DNS of the node silent, a line in the group card; вариант В — этап 2). The change of
  round 1 (the direct group by the node's resolver) is withdrawn: it was by domain, not by person (REV-s1-kt1a С2,
  -sec С-1); ``RuDnsRg9Test`` keeps the DNS silent.

These tests use the REAL ``exits.target`` (tests/gw/s1_xray.py patches it with T5's sketch — that is how Н7 hid)."""

import json
import unittest

from kaskad.gw import dns, exits, net, xray
from kaskad.gw import policy as P
from kaskad.rules import apply as rules_apply
from tests import realbin
from tests.gw.s1_xray import S1Case, chain_of, decision, fake_target, rules_of



def oracle_spread(states, tiers):
    """The exits a new connection of «по соединениям» may take (SPEC-stage1-exits §4.5), written from the spec — not
    ``assign.spread_cands`` (REV-s1-kt1a-2 З-2): the first tier with an «ok» exit — its «ok» ones; else the first with
    «degraded»; else the first «in reserve»; an «unknown» or «off» exit — never."""
    for klass in ("ok", "degraded", "standby"):
        for tier in tiers:
            got = [e for e in tier if states.get(e) == klass]
            if got:
                return got
    return []

class RealTargetCase(S1Case):
    def setUp(self):
        super().setUp()
        for p in list(self._t5):
            if getattr(p, "new", None) is fake_target:
                p.stop()
                self._t5.remove(p)

    def per_connection(self, fail="block", exits_=None):
        doc = P.default_doc("compat")
        main = doc["groups"][0]
        main["per_connection"] = True
        main["fail"] = fail
        main["tiers"][0]["exits"] = list(exits_ if exits_ is not None else (self.vid, self.bid, self.aid))
        self.st["gateway"]["exit_policy"] = doc
        return doc

    def dec(self, **kw):
        kw.setdefault("pins", {})
        d = super().dec(**kw)
        d["rows"] = dict((e["id"], exits._row(e)) for e in self.pool)       # as exits.decision has them
        return d

    def dead(self, **kw):
        return self.dec(states={self.vid: "off", self.bid: "off", self.aid: "off"}, gw_all="block", **kw)


class PerConnectionTest(RealTargetCase):
    def test_pool_user_goes_to_gw_vless_with_live_exits(self):
        # Н7: the chain of a pool user of SOCKS5 was fail-block (no internet) while every exit was alive
        self.node()
        self.per_connection()
        dec = self.dec()
        t = exits.target(self.st, self.dev("d-00000001"), dec)
        self.assertEqual((t["mark"], t["xray"]), (exits.MARK_SPREAD, {"balancerTag": exits.VLESS_BALANCER}))
        cfg = self.render(dec)
        tel = chain_of(cfg, "telefon-0001")
        self.assertEqual([(r.get("balancerTag"), r.get("outboundTag")) for r in tel], [("gw-vless", None)])
        bal = dict((b["tag"], b) for b in cfg["routing"]["balancers"])
        self.assertEqual(sorted(bal["gw-vless"]["selector"]), sorted([exits.tag(self.vid), exits.tag(self.bid)]))
        self.assertNotIn(xray.FAIL_BLOCK, json.dumps([r for r in cfg["routing"]["rules"]
                                                      if "telefon-0001" in (r.get("user") or [])]))
        # «exit:E» of a device keeps its exit in the mode (only the pool spreads)
        self.assertEqual(chain_of(cfg, "noutbuk-0003")[0]["outboundTag"], exits.tag(self.vid))

    def test_awg_only_main_goes_to_gw_all(self):
        # no xray exit in «Основная»: no gw-vless at all — gw-all (the collector's best of «Основная»), not fail-block
        self.node()
        doc = self.per_connection(exits_=[self.aid])
        doc["groups"].append(P.default_group("g-00aa11", "abroad", "compat"))   # the VLESS exits elsewhere
        doc["groups"][1]["tiers"][0]["exits"] = [self.vid, self.bid]
        self.assertEqual(P.exits_of(P.group(P.load(self.st, self.pool), P.MAIN)), [self.aid])
        cfg = self.render(self.dec())
        self.assertEqual(chain_of(cfg, "telefon-0001")[0].get("balancerTag"), "gw-all")

    def test_the_mode_goes_live_without_a_restart(self):
        # T8 turns the mode on from the panel: the chains and the balancers change through the API
        self.node()
        before = self.render(self.dec(pins={"d-00000001": self.vid}))
        self.per_connection()
        after = self.render(self.dec())
        plan = xray.hot_plan(before, after, xray.hot_tags(self.st, self.sec))
        self.assertIsNotNone(plan)


class PerConnectionDownTest(RealTargetCase):
    def test_fail_direct_goes_directly_kernel_and_xray(self):
        # S26: «по соединениям» + fail direct, every exit down — shown «напрямую», no connection at all before
        self.node()
        self.per_connection(fail="direct")
        dec = self.dead()
        for did in ("d-00000001",):
            t = exits.target(self.st, self.dev(did), dec)
            self.assertEqual((t["step"], t["mark"], t["xray"]), ("last_direct", exits.MARK_DIRECT,
                                                                 {"outboundTag": "direct"}))
        self.assertEqual(exits.marks(self.st, dec)["10.77.1.10"], exits.MARK_DIRECT)
        self.assertEqual(chain_of(self.render(dec), "telefon-0001")[0]["outboundTag"], "direct")
        now = exits.now_of(self.st, self.dev("d-00000001"), None, decision=dec)
        self.assertEqual(now["target"], "direct")
        self.assertEqual(exits.fail_of(self.st, exits.model.route_of(self.st, self.dev("d-00000001")),
                                       {"policy": P.load(self.st)}), "direct")

    def test_fail_block_is_the_last_step_and_russia_stays(self):
        # S23 in the mode: the block of the last step (no restart), the ru_direct device keeps Russia (gw_ruonly)
        self.node()
        self.per_connection(fail="block")
        dec = self.dead()
        t = exits.target(self.st, self.dev("d-00000001"), dec)
        self.assertEqual((t["step"], t["mark"], t["xray"]), ("last_block", exits.MARK_BLOCK,
                                                             {"outboundTag": xray.FAIL_BLOCK}))
        cfg = self.render(dec)
        self.assertEqual(chain_of(cfg, "telefon-0001")[0]["outboundTag"], xray.FAIL_BLOCK)
        self.assertTrue([r for r in rules_of(cfg, prefix="ls-") if "telefon-0001" in r["user"]
                         and r.get("outboundTag") == "direct"])
        tg = exits.targets(self.st, dec)
        self.assertIn("10.77.1.10", net.ruonly_of(tg, ["10.77.1.10"]))

    def test_the_step_of_the_collector_and_of_a_reader(self):
        # the collector's tick has ``steps``; a reader of pins.json has the last step with no pins, or the states only
        self.node()
        self.per_connection(fail="direct")
        dev = self.dev("d-00000001")
        tick = self.dead(steps={"d-00000001": "last_direct"})
        self.assertEqual(exits.target(self.st, dev, tick)["step"], "last_direct")
        live = self.dec(steps={"d-00000001": "exit"})
        self.assertEqual(exits.target(self.st, dev, live)["mark"], exits.MARK_SPREAD)
        pinned = self.dec(states={self.vid: "ok", self.bid: "ok", self.aid: "ok"})
        pinned["groups"]["main"]["last_step"] = "direct"                 # pins.json: the last step written
        self.assertEqual(exits.target(self.st, dev, pinned)["step"], "last_direct")
        unknown = self.dec(states={self.vid: "unknown", self.bid: "off", self.aid: "off"})
        self.assertEqual(exits.target(self.st, dev, unknown)["step"], "last_direct")
        standby = self.dec(states={self.vid: "standby", self.bid: "off", self.aid: "off"})
        self.assertEqual(exits.target(self.st, dev, standby)["mark"], exits.MARK_SPREAD)


class PerConnectionTierTest(RealTargetCase):
    """REV-s1-kt1a С1: gw-vless only with an xray exit a new connection may take now in the kernel's tier."""

    def chain(self, cfg):
        return [(r.get("balancerTag"), r.get("outboundTag")) for r in chain_of(cfg, "telefon-0001")]

    def test_awg_alive_xray_dead_goes_to_gw_all(self):
        # «Основная» of VLESS + AmneziaWG, VLESS cut by DPI: the group lives on AWG — the users of xray too (gw-all, the
        # collector's best of «Основная»), not gw-vless of the dead legs falling back to block
        self.node()
        self.per_connection(fail="direct")
        dec = self.dec(states={self.vid: "off", self.bid: "off", self.aid: "ok"}, gw_all=exits.tag(self.aid))
        t = exits.target(self.st, self.dev("d-00000001"), dec)
        self.assertEqual(t["mark"], exits.MARK_SPREAD)                   # the group is alive: the spread
        cfg = self.render(dec)
        self.assertEqual(self.chain(cfg), [("gw-all", None)])
        self.assertEqual(net.spread_marks(self.st, dec, self.pool), [self.aid_mark()])
        bal = dict((b["tag"], b) for b in cfg["routing"]["balancers"])
        self.assertNotIn(xray.FAIL_BLOCK, json.dumps(chain_of(cfg, "telefon-0001")))
        self.assertEqual(bal["gw-all"]["selector"], [exits.ABROAD_PREFIX])

    def test_xray_only_in_the_reserve_tier_stays_with_the_kernel(self):
        # the active tier has AmneziaWG «ok» only, VLESS «ok» lies in tier 2: the kernel spreads over tier 1 — xray too
        # (gw-all), never over the reserve tier while the kernel is on the active one
        self.node()
        doc = self.per_connection(exits_=[self.aid])
        doc["groups"][0]["tiers"].append({"id": "t2", "exits": [self.vid, self.bid]})
        dec = self.dec(gw_all=exits.tag(self.aid))
        self.assertEqual(net.spread_marks(self.st, dec, self.pool), [self.aid_mark()])
        cfg = self.render(dec)
        self.assertEqual(self.chain(cfg), [("gw-all", None)])
        # the active tier falls: both on tier 2 — the kernel 0x200, xray gw-vless of the VLESS exits there
        dec = self.dec(states={self.vid: "ok", self.bid: "degraded", self.aid: "off"})
        self.assertEqual(net.spread_marks(self.st, dec, self.pool), [exits.MARK_XRAY])
        cfg = self.render(dec)
        self.assertEqual(self.chain(cfg), [("gw-vless", None)])
        bal = dict((b["tag"], b) for b in cfg["routing"]["balancers"])
        self.assertEqual(bal["gw-vless"]["selector"], [exits.tag(self.vid)])      # the best class only (as the kernel)

    def test_xray_and_kernel_always_on_one_tier(self):
        # every state of three exits over two tiers: gw-vless for the users of xray exactly when the kernel spreads over
        # an xray exit (0x200), and then over the same exits
        import itertools
        self.node()
        doc = self.per_connection(exits_=[self.aid, self.vid])
        doc["groups"][0]["tiers"].append({"id": "t2", "exits": [self.bid]})
        xray_ids = {exits.tag(self.vid): self.vid, exits.tag(self.bid): self.bid}
        for sv in itertools.product(("ok", "degraded", "standby", "off", "unknown"), repeat=3):
            states = dict(zip((self.vid, self.bid, self.aid), sv))
            dec = self.dec(states=states, gw_all=exits.tag(self.aid))
            if exits.target(self.st, self.dev("d-00000001"), dec)["mark"] != exits.MARK_SPREAD:
                continue
            cands = set(oracle_spread(states, [[self.aid, self.vid], [self.bid]]))   # not spread_cands (З-2)
            cfg = self.render(dec)
            got = self.chain(cfg)
            want_x = sorted(e for e in cands if e in xray_ids.values())
            if want_x:
                self.assertEqual(got, [("gw-vless", None)], states)
                bal = dict((b["tag"], b) for b in cfg["routing"]["balancers"])
                self.assertEqual(sorted(xray_ids[x] for x in bal["gw-vless"]["selector"]), want_x, states)
                self.assertIn(exits.MARK_XRAY, net.spread_marks(self.st, dec, self.pool), states)
            else:
                self.assertEqual(got, [("gw-all", None)], states)
                self.assertNotIn(exits.MARK_XRAY, net.spread_marks(self.st, dec, self.pool), states)

    def aid_mark(self):
        return next(e["mark"] for e in self.pool if e["id"] == self.aid)

    def test_now_of_names_the_candidates_of_the_kernel_and_xray(self):
        # REV-s1-kt1a-2 П-1: «Куда пойдёт трафик?» (now_of) takes the candidates from assign.spread_cands — the one
        # answer of the kernel (net.spread_marks) and xray (gw-vless), not a copy of _klass/_cands
        from unittest import mock
        from kaskad.gw import assign
        self.node()
        doc = self.per_connection(exits_=[self.aid, self.vid])
        doc["groups"][0]["tiers"].append({"id": "t2", "exits": [self.bid]})
        dec = self.dec(states={self.aid: "unknown", self.vid: "ok", self.bid: "ok"})
        now = exits.now_of(self.st, self.dev("d-00000001"), None, decision=dec)
        self.assertEqual((now["target"], now["balancer"], now["exits"]), ("pool", "any", [self.vid]))
        with mock.patch.object(assign, "spread_cands", lambda states, grp: ["x-sentinel"]):
            now = exits.now_of(self.st, self.dev("d-00000001"), None, decision=dec)
        self.assertEqual(now["exits"], ["x-sentinel"])

    def test_unknown_gets_no_new_connection_kernel_and_xray(self):
        # REV-s1-kt1a-2 З-2 (and «было до правки» of the kernel): §4.5 — an «unknown» exit gets no new connection, even
        # one last seen «ok» (``shown``); «unknown» + «ok» in tier 2 — tier 2, both in the kernel and in xray
        self.node()
        doc = self.per_connection(exits_=[self.aid, self.vid])
        doc["groups"][0]["tiers"].append({"id": "t2", "exits": [self.bid]})
        for unk in ("unknown", {"state": "unknown", "shown": "ok"}):
            dec = self.dec(states={self.aid: unk, self.vid: "ok", self.bid: "off"})
            self.assertEqual(net.spread_marks(self.st, dec, self.pool), [exits.MARK_XRAY], unk)   # not the AWG mark
            dec = self.dec(states={self.aid: unk, self.vid: unk, self.bid: "ok"})
            self.assertEqual(net.spread_marks(self.st, dec, self.pool), [exits.MARK_XRAY], unk)   # tier 2
            cfg = self.render(dec)
            self.assertEqual(self.chain(cfg), [("gw-vless", None)], unk)
            bal = dict((b["tag"], b) for b in cfg["routing"]["balancers"])
            self.assertEqual(bal["gw-vless"]["selector"], [exits.tag(self.bid)], unk)
            dec = self.dec(states={self.aid: "ok", self.vid: unk, self.bid: "ok"})
            self.assertEqual(net.spread_marks(self.st, dec, self.pool), [self.aid_mark()], unk)   # tier 1, AWG only


class SpreadDownSeenTest(RealTargetCase):
    def test_unknown_counts_as_it_was_for_a_reader(self):
        # REV-s1-kt1a П2: a reader of pins.json (no ``steps``, no last step written) — an «unknown» exit last seen «ok»
        # keeps the group on the spread (``_klass(seen=True)``: what the group stands on), a bare «unknown» does not
        self.node()
        self.per_connection(fail="direct")
        dev = self.dev("d-00000001")
        was_ok = self.dec(states={self.vid: {"state": "unknown", "shown": "ok"}, self.bid: "off", self.aid: "off"})
        self.assertEqual(exits.target(self.st, dev, was_ok)["mark"], exits.MARK_SPREAD)
        was_off = self.dec(states={self.vid: {"state": "unknown", "shown": "off"}, self.bid: "off", self.aid: "off"})
        self.assertEqual(exits.target(self.st, dev, was_off)["step"], "last_direct")


class RuDnsRg9Test(RealTargetCase):
    """Р-Г9 (SPEC-stage1-exit-groups §5, строка карточки): «Основная» down — DNS of the node silent; the names of
    Russia do not resolve by the node (вариант В — этап 2). The КТ1 Н6 change is withdrawn (REV-s1-kt1a С2, -sec С-1)."""

    def direct_dns(self, cfg):
        return [s for s in cfg["dns"]["servers"] if isinstance(s, dict) and s.get("address") == "localhost"
                and any(not str(d).startswith("full:") for d in s.get("domains") or [])]

    def test_no_name_of_the_lists_by_the_node_with_the_consent(self):
        self.node()
        self.assertIsNone(rules_apply._why(self.st))                         # the consent ⚠РФ and the lists
        for dec in (self.dead(), self.dec()):
            cfg = self.render(dec)
            self.assertEqual(self.direct_dns(cfg), [])
            self.assertNotIn("ext:", json.dumps(cfg["dns"]))
            self.assertEqual(cfg["dns"]["servers"][-2:], ["1.1.1.1", "1.0.0.1"])
            self.assertEqual(rules_of(cfg, inboundTag=["gw-dns-int"])[0].get("balancerTag"), "gw-all")
        self.assertEqual(self.render(self.dead())["dns"], self.render(self.dec())["dns"])   # no restart when they fall

    def test_dnsmasq_has_no_direct_names(self):
        self.node()
        text = dns.conf_text(self.st, True, False, True, rules_apply.dnsmasq_lines(self.st))
        self.assertFalse([ln for ln in text.splitlines() if ln.startswith("server=/")])
        self.assertNotIn("strict-order", text.splitlines())

    @unittest.skipUnless(realbin.xray_path(), "SKIP нет xray: bash tools/fetch_test_bins.sh")
    def test_the_real_xray_takes_it(self):
        self.node()
        for dec in (self.dead(), self.dec(states={self.vid: "off", self.bid: "off", self.aid: "ok"})):
            self.per_connection(fail="block")
            ok, text = realbin.xray_check(self.render(dec))
            self.assertTrue(ok, text)


if __name__ == "__main__":
    unittest.main()
