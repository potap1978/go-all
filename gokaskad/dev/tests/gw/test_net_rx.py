"""T15 Q2 (SPEC-groups §3, §13.1 «net.py», «hooks.py»): the map gw_rx of the kernel — the Russian target of a client.
A node without a Russian group draws no gw_rx (its table — that of part A: tests/fixtures/nft keep it byte for byte);
with one: the map, its two rules right after those of gw_rd, the addresses checked (inside the base, the marks of the
Russian targets only, nothing without the consent ⚠РФ), refilled in ONE transaction with gw_route and gw_rd, gw_live and
gw_ruonly with the addresses alive only for Russia; a dynamic address goes into gw_rd or gw_rx by ``ru_target`` and
leaves gw_rx at «down» (Б10)."""

from unittest import mock

from kaskad import nft, state
from kaskad.gw import exits, hooks, model, net
from tests.fakes_gw import register_dummies
from tests.gw.test_ru_target import D1, HOME, RuWorld

D1_IP, D3_IP = "10.77.1.10", "10.77.1.12"


def rx_lines():
    return ["ip daddr . meta l4proto @gw_ru ip daddr != @gw_force meta mark set ip saddr map @gw_rx",
            "ip daddr @gw_ru_dns ip daddr != @gw_force meta mark set ip saddr map @gw_rx"]


class NoRussianGroupTest(RuWorld):
    def test_no_map_no_rules(self):
        st = self.ru_state()
        p = net.plan(st, self.w.runner)
        self.assertNotIn("rx", p)
        text = "\n".join(net._table_lines(st, p))
        self.assertNotIn("gw_rx", text)
        self.assertNotIn("gw_rx", "\n".join(net.update_lines(st, None, p)))


class RxTableTest(RuWorld):
    def test_plan_text_and_apply(self):
        st = self.world()
        p = net.plan(st, self.w.runner)
        marks = set(e["mark"] for e in self.ru_rows)
        self.assertEqual(sorted(p["rx"]), [D1_IP, D3_IP])
        self.assertTrue(set(p["rx"].values()) <= marks)
        self.assertEqual(p["rd"], [])
        pre = dict(net.rules(st, p))["gw_pre"]
        i = pre.index(rx_lines()[0])
        self.assertEqual(pre[i:i + 2], rx_lines())
        self.assertEqual(pre[i - 2:i], net.ru_rules())                  # right after the rules of gw_rd
        self.assertTrue(pre[i + 2].startswith("ct mark set meta mark"))
        text = "\n".join(net._table_lines(st, p))
        self.assertIn("map gw_rx {", text)
        nft.apply(st, self.w.runner)
        got = self.w.nft.elements("gw_rx")
        self.assertEqual(sorted(got), [D1_IP, D3_IP])
        self.assertEqual(nft.verify(state.load(), nft.read_live(self.w.runner)), [])

    def test_elements_checked(self):
        st = self.world()
        p = {"rx": {D1_IP: 457, "10.99.0.1": 457, D3_IP: exits.MARK_XRAY, "x": 457, "10.77.1.11": 0x301}}
        self.assertEqual(net.rx_elements(st, p), [(D1_IP, 457)])         # outside the base, foreign marks — out
        st["gateway"].pop("rf_consent", None)
        self.assertEqual(net.rx_elements(st, p), [])                      # D-136: the last line

    def test_one_transaction_with_gw_route(self):
        st = self.world()
        nft.apply(st, self.w.runner)
        dec = self.dec(st)
        other = [i for i in self.ru_ids if i != exits.ru_target(st, model.get_device(st, D1), dec)["exit"]][0]
        dec["groups"] = dict(dec.get("groups") or {}, **{HOME: {"pins": {D1: {"exit": other, "tier": "t1",
                                                                               "why": "evict"}}}})
        calls = []
        real = self.w.runner.run

        def spy(argv, **kw):
            calls.append(kw.get("input") or "")
            return real(argv, **kw)
        with mock.patch.object(self.w.runner, "run", side_effect=spy):
            self.assertTrue(net.update_map(self.w.runner, state.load(), decision=dec))
        scripts = [c for c in calls if "gw_rx" in c]
        self.assertEqual(len(scripts), 1, calls)
        self.assertIn("flush map %s gw_route" % nft.TABLE, scripts[0])
        want = dict((e["id"], e["mark"]) for e in self.ru_rows)[other]
        self.assertEqual(self.w.nft.elements("gw_rx")[D1_IP], want)

    def test_ruonly_the_foreign_block_and_a_russian_exit(self):
        """SE §2.3: the foreign exits down (route — the last step «block»), Russia through «Дом» — alive for Russia
        alone: gw_live and gw_ruonly, the mark of the Russian exit in gw_rx."""
        st = self.world()
        dec = self.dec(st, alive=self.ru_ids)
        p = net.plan(st, self.w.runner, decision=dec)
        self.assertEqual(p["elems"][D1_IP], exits.MARK_BLOCK)
        self.assertIn(D1_IP, p["ruonly"])
        self.assertIn(D1_IP, net.live_ips(st, p["elems"], p["ruonly"]))
        st2 = self.world(fail="block")
        p2 = net.plan(st2, self.w.runner, decision=self.dec(st2, alive=()))
        self.assertNotIn(D1_IP, p2["ruonly"])                            # Russia blocked too: not alive

    def test_the_last_group_gone_drops_the_map(self):
        st = self.world()
        nft.apply(st, self.w.runner)
        st["gateway"]["exit_policy"]["groups"] = [g for g in st["gateway"]["exit_policy"]["groups"]
                                                  if g.get("id") != HOME]
        for u in st["gateway"]["users"]:
            u.pop("ru_via", None)
        state.save(st)
        lines = net.update_lines(state.load(), nft.read_live(self.w.runner))
        self.assertIn("delete map %s gw_rx" % nft.TABLE, lines)
        self.assertGreater(lines.index("delete map %s gw_rx" % nft.TABLE), lines.index("flush chain %s gw_pre" % nft.TABLE))


class HookRxTest(RuWorld):
    def ikev2(self, via=HOME):
        st = self.world(via=via, save=False)
        register_dummies(("ikev2", {"ifaces": [], "dynamic": True}))
        st["gateway"]["inbounds"]["ikev2"] = {"enabled": True, "subnet": "10.77.8.0/24"}
        st["gateway"]["devices"][0]["access"].append("ikev2")
        state.save(st)
        nft.apply(state.load(), self.w.runner)

    def test_up_into_gw_rx_down_out_of_everything(self):
        self.ikev2()
        res = hooks.hook("ikev2", "up", "telefon-0001", "10.77.8.10", self.w.runner)
        self.assertFalse(res["ru_direct"])
        self.assertIn(res["ru_mark"], set(e["mark"] for e in self.ru_rows))
        self.assertEqual(self.w.nft.elements("gw_rx").get("10.77.8.10"), res["ru_mark"])
        self.assertNotIn("10.77.8.10", self.w.nft.elements("gw_rd"))
        self.assertEqual(nft.verify(state.load(), nft.read_live(self.w.runner)), [])
        hooks.hook("ikev2", "down", "telefon-0001", "10.77.8.10", self.w.runner)
        for name in ("gw_rx", "gw_rd", "gw_live", "gw_ruonly", "gw_route"):
            self.assertNotIn("10.77.8.10", self.w.nft.elements(name), name)

    def test_direct_into_gw_rd_not_gw_rx(self):
        self.ikev2(via="direct")
        res = hooks.hook("ikev2", "up", "telefon-0001", "10.77.8.10", self.w.runner)
        self.assertTrue(res["ru_direct"])
        self.assertIn("10.77.8.10", self.w.nft.elements("gw_rd"))
        self.assertNotIn("10.77.8.10", self.w.nft.elements("gw_rx"))

    def test_a_reused_lease_leaves_gw_rx(self):
        """The address of «Мама» in gw_rx, then «up» of a device without «Россия напрямую» on the same address — out."""
        self.ikev2()
        hooks.hook("ikev2", "up", "telefon-0001", "10.77.8.10", self.w.runner)
        st = state.load()
        st["gateway"]["devices"][0]["rules"] = "all_exit"
        state.save(st)
        hooks.hook("ikev2", "up", "telefon-0001", "10.77.8.10", self.w.runner)
        self.assertNotIn("10.77.8.10", self.w.nft.elements("gw_rx"))


class VerifyRxTest(RuWorld):
    """REV-s1-q2 З-2 (порча verify_rx): ``verify`` sees a foreign entry of gw_rx, and a gw_rx left in the table with no
    Russian group (a failed apply) — the check of the node repairs both."""

    def test_a_foreign_entry_is_a_difference(self):
        st = self.world()
        nft.apply(st, self.w.runner)
        self.assertEqual(nft.verify(state.load(), nft.read_live(self.w.runner)), [])
        mine = self.w.nft.elements("gw_rx")[D1_IP]
        other = [e["mark"] for e in self.ru_rows if e["mark"] != mine][0]
        self.w.runner.run([nft.NFT, "-f", nft.STDIN], input="delete element %s gw_rx { %s }\nadd element %s gw_rx "
                          "{ %s : %s }\n" % (nft.TABLE, D1_IP, nft.TABLE, D1_IP, hex(other)))
        probs = nft.verify(state.load(), nft.read_live(self.w.runner))
        self.assertTrue([x for x in probs if "gw_rx" in x], probs)

    def test_a_map_left_without_a_russian_group(self):
        st = self.world()
        nft.apply(st, self.w.runner)
        st = state.load()
        st["gateway"]["exit_policy"]["groups"] = [g for g in st["gateway"]["exit_policy"]["groups"]
                                                  if g.get("role") != "ru"]
        for u in st["gateway"]["users"]:
            u.pop("ru_via", None)
        state.save(st)
        probs = nft.verify(state.load(), nft.read_live(self.w.runner))
        self.assertTrue([x for x in probs if "gw_rx" in x], probs)
        nft.apply(state.load(), self.w.runner)
        self.assertEqual(nft.verify(state.load(), nft.read_live(self.w.runner)), [])


class RevokedConnectionsTest(RuWorld):
    """SEC-s1-q2 Б-2: the consent ⚠РФ withdrawn (or «Россия напрямую» off, ``ru_via`` changed) — the open connections
    of the addresses that left gw_rx / gw_rd are forgotten by their old mark (gw_pre returns an established connection
    by its ct mark before gw_rd / gw_rx): by the collector's update_map and by gw apply alike."""

    def flushed(self, fn):
        calls = []

        def fake(runner, ips, mark):
            calls.append((mark, sorted(ips)))
            return len(ips)
        with mock.patch.object(net, "flush_devices", side_effect=fake):
            fn()
        return calls

    def test_the_collector(self):
        st = self.world()
        nft.apply(st, self.w.runner)
        marks = dict(self.w.nft.elements("gw_rx"))
        self.assertEqual(sorted(marks), [D1_IP, D3_IP])
        no = state.load()
        no["gateway"].pop("rf_consent", None)
        state.save(no)
        calls = self.flushed(lambda: net.update_map(self.w.runner, state.load(), decision=self.dec(state.load())))
        self.assertEqual(self.w.nft.elements("gw_rx"), {})
        got = {}
        for m, ips in calls:
            got.update((ip, m) for ip in ips)
        self.assertEqual(got, marks)

    def test_gw_apply(self):
        from kaskad.gw import ops
        st = self.world(via="direct")
        nft.apply(st, self.w.runner)
        self.assertEqual(sorted(self.w.nft.elements("gw_rd")), [D1_IP, D3_IP])
        no = state.load()
        no["gateway"].pop("rf_consent", None)
        state.save(no)
        from kaskad import secrets
        calls = self.flushed(lambda: ops._apply(state.load(), secrets.load(), self.w.runner, probe=False))
        self.assertEqual(sorted(self.w.nft.elements("gw_rd")), [])
        self.assertIn((exits.MARK_DIRECT, [D1_IP, D3_IP]), calls)

    def test_nothing_taken_nothing_flushed(self):
        st = self.world()
        nft.apply(st, self.w.runner)
        calls = self.flushed(lambda: net.update_map(self.w.runner, state.load(), decision=self.dec(state.load())))
        self.assertEqual(calls, [])

    def test_the_pure_function(self):
        before = ({"10.0.0.1", "10.0.0.2"}, {"10.0.0.3": 0x3c9, "10.0.0.4": exits.MARK_BLOCK, "10.0.0.5": 0x3c9},
                  {})
        after = ({"10.0.0.2"}, {"10.0.0.5": 0x3ca}, {"10.0.0.1": exits.MARK_DIRECT})
        self.assertEqual(net.ru_revoked(before, after), {0x3c9: {"10.0.0.3"}})      # 10.0.0.1 goes direct anyway


class HookRuSideTest(HookRxTest):
    """SEC-s1-q2 П-1 / REV-s1-q2 П-1, П-2 and the порчи of REV-s1-q2 З-2 (hook_lockwait, hook_ruopen, set_rx_fallback):
    gw_rd and gw_rx of a dynamic address in ONE transaction BEFORE the address is live; a mark that cannot be written
    keeps the address closed; another owner's mark is replaced; without the state lock — no gw_rx; no Russian group —
    no call about gw_rx."""

    def calls_of(self, *a):
        calls = []
        real = self.w.runner.run

        def spy(argv, **kw):
            calls.append(kw.get("input") or " ".join(argv))
            return real(argv, **kw)
        with mock.patch.object(self.w.runner, "run", side_effect=spy):
            res = hooks.hook(*a, self.w.runner)
        return res, calls

    def test_the_russian_side_first_and_in_one_transaction(self):
        self.ikev2()
        res, calls = self.calls_of("ikev2", "up", "telefon-0001", "10.77.8.10")
        rx = [i for i, c in enumerate(calls) if "gw_rx" in c]
        live = [i for i, c in enumerate(calls) if "gw_live" in c]
        self.assertTrue(rx and live, calls)
        self.assertLess(rx[0], live[0])                     # never live with the old owner's gw_rx
        self.assertIn("gw_rd", calls[rx[0]])               # gw_rd and gw_rx: one transaction
        self.assertEqual(self.w.nft.elements("gw_rx").get("10.77.8.10"), res["ru_mark"])

    def test_another_owner_s_mark_is_replaced(self):
        self.ikev2()
        mine = exits.ru_target(state.load(), model.get_device(state.load(), D1), self.dec(state.load()))["mark"]
        other = [e["mark"] for e in self.ru_rows if e["mark"] != mine][0]
        self.assertTrue(net.set_ru(self.w.runner, state.load(), "10.77.8.10", False, other))   # a «down» never came
        self.assertEqual(self.w.nft.elements("gw_rx").get("10.77.8.10"), other)
        res = hooks.hook("ikev2", "up", "telefon-0001", "10.77.8.10", self.w.runner)
        self.assertEqual(res["ru_mark"], mine)
        self.assertEqual(self.w.nft.elements("gw_rx").get("10.77.8.10"), mine)

    def test_a_mark_that_cannot_be_written_keeps_the_address_closed(self):
        self.ikev2()
        mine = exits.ru_target(state.load(), model.get_device(state.load(), D1), self.dec(state.load()))["mark"]
        other = [e["mark"] for e in self.ru_rows if e["mark"] != mine][0]
        net.set_ru(self.w.runner, state.load(), "10.77.8.10", False, other)
        real = self.w.runner.run

        def refuse(argv, **kw):                             # nft takes nothing about gw_rx (EPERM, a full map…)
            if "gw_rx" in (kw.get("input") or "") and "list" not in argv:
                from kaskad.sysrun import Result
                return Result(argv, 1, "", "Error: Could not process rule: Operation not permitted\n")
            return real(argv, **kw)
        with mock.patch.object(self.w.runner, "run", side_effect=refuse):
            res = hooks.hook("ikev2", "up", "telefon-0001", "10.77.8.10", self.w.runner)
        self.assertTrue(res.get("ru_failed"), res)
        self.assertNotIn("10.77.8.10", self.w.nft.elements("gw_live"))
        self.assertNotIn("10.77.8.10", self.w.nft.elements("gw_route"))
        self.assertIn("10.77.8.10", self.log())

    def test_without_the_state_lock_no_gw_rx(self):
        import contextlib
        self.ikev2()

        @contextlib.contextmanager
        def busy():
            yield False
        with mock.patch.object(hooks, "_maybe_state_lock", busy):
            res = hooks.hook("ikev2", "up", "telefon-0001", "10.77.8.10", self.w.runner)
        self.assertTrue(res.get("rd_wait"))
        self.assertNotIn("ru_mark", res)
        self.assertNotIn("10.77.8.10", self.w.nft.elements("gw_rx"))

    def test_the_foreign_last_block_and_a_russian_exit_keep_it_alive_for_russia(self):
        self.ikev2()
        foreign = [e["id"] for e in exits.pool(self.w.runner)]
        snap = exits.load_snapshot()
        for i in foreign:
            snap.setdefault("exits", {})[i] = {"alive": False, "state": "dead", "reasons": []}
        exits.save_snapshot(snap)
        res = hooks.hook("ikev2", "up", "telefon-0001", "10.77.8.10", self.w.runner)
        self.assertEqual(res["mark"], exits.MARK_BLOCK)
        self.assertIn(res.get("ru_mark"), set(e["mark"] for e in self.ru_rows))
        self.assertTrue(res["ru_only"], res)
        self.assertIn("10.77.8.10", self.w.nft.elements("gw_live"))
        self.assertIn("10.77.8.10", self.w.nft.elements("gw_ruonly"))

    def test_no_russian_group_no_call_about_gw_rx(self):
        self.ikev2(via=None)
        st = state.load()
        st["gateway"]["exit_policy"]["groups"] = [g for g in st["gateway"]["exit_policy"]["groups"]
                                                  if g.get("role") != "ru"]
        for u in st["gateway"]["users"]:
            u.pop("ru_via", None)
        state.save(st)
        nft.apply(state.load(), self.w.runner)
        for action in ("up", "down"):
            _res, calls = self.calls_of("ikev2", action, "telefon-0001", "10.77.8.10")
            self.assertFalse([c for c in calls if "gw_rx" in c], (action, calls))


class CollectorReadsRxTest(RuWorld):
    def test_gw_rx_read_only_with_a_russian_group(self):
        """The collector's round reads gw_rx (its update_map refills it) with a Russian group — and makes no extra call
        of nft on a node without one (tests/test_gw_f2.py pins that list)."""
        from kaskad.gw import collect, peek
        seen = []
        real = peek.read

        def spy(runner, sets=peek.HOT_SETS, counters=True):
            seen.append(tuple(sets))
            return real(runner, sets=sets, counters=counters)
        st = self.world()
        nft.apply(st, self.w.runner)
        with mock.patch.object(peek, "read", side_effect=spy):
            collect.tick(self.w.runner)
        self.assertIn(net.RX, seen[0])
        self.assertEqual(sorted(self.w.nft.elements("gw_rx")), [D1_IP, D3_IP])


class SetRxGuardTest(RuWorld):
    def test_no_consent_no_entry(self):
        """D-136, the last line of a hook: without the consent ⚠РФ the address never goes into gw_rx; a foreign mark —
        neither."""
        st = self.world()
        nft.apply(st, self.w.runner)
        mark = self.ru_rows[0]["mark"]
        self.assertTrue(net.set_ru(self.w.runner, st, "10.77.8.10", False, mark))
        self.assertIn("10.77.8.10", self.w.nft.elements("gw_rx"))
        no = dict(st, gateway=dict(st["gateway"]))
        no["gateway"].pop("rf_consent", None)
        net.set_ru(self.w.runner, no, "10.77.8.10", False, mark)
        self.assertNotIn("10.77.8.10", self.w.nft.elements("gw_rx"))
        net.set_ru(self.w.runner, st, "10.77.8.11", False, exits.MARK_XRAY)
        self.assertNotIn("10.77.8.11", self.w.nft.elements("gw_rx"))
