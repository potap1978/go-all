"""Stage 1, B4 — the batch of «заметное» before КТ1 (REV-s1-b2, REV-s1-b3, REV-s1-hotfix-merge).

З1 (REV-s1-b2): the fail step of a route is the route's own policy **or** the one of its group (``exits._last``) —
the CLI, the copy .kbk, trace, the scheme warnings and the bot say what the decision does, not only ``route.fail``."""

import unittest

from kaskad import backup, i18n, state, summary
from kaskad.bot import gateway as bot_gw
from kaskad.gw import cli, collect, exits, live, net, ops, policy as P
from kaskad.panel import scheme
from tests.gw import test_collect_s1 as C1, test_s1_b1 as B1, test_trace as TT

GID = "g-123456"


def _policy(main_fail="block", group_fail=None):
    """A policy: «main» with ``main_fail``, and (``group_fail`` given) a foreign group «Дом» with it."""
    groups = [P.default_group(P.MAIN, "abroad", "compat")]
    groups[0]["fail"] = main_fail
    if group_fail is not None:
        g = P.default_group(GID, "abroad", "compat", name="Дом")
        g["fail"] = group_fail
        groups.append(g)
    return {"version": P.VERSION, "groups": groups}


class FailOfTest(unittest.TestCase):
    def setUp(self):
        cm = i18n.use_lang("ru")
        cm.__enter__()
        self.addCleanup(cm.__exit__, None, None, None)

    def test_the_group_decides_as_the_decision_does(self):
        st = {"gateway": {"exit_policy": _policy("block", "direct")}}
        self.assertEqual(exits.fail_of(st, {"via": "group:" + GID, "fail": "block"}), "direct")
        self.assertEqual(exits.fail_of(st, {"via": "pool", "fail": "block"}), "block")
        self.assertEqual(exits.fail_of(st, {"via": "pool", "fail": "direct"}), "direct")
        st = {"gateway": {"exit_policy": _policy("direct")}}
        self.assertEqual(exits.fail_of(st, {"via": "pool", "fail": "block"}), "direct")       # «main» for the pool
        self.assertEqual(exits.fail_of(st, {"via": "exit:e-0000000001", "fail": "block"}), "direct")
        self.assertIsNone(exits.fail_of(st, {"via": "block", "fail": "direct"}))
        self.assertIsNone(exits.fail_of(st, {"via": "direct"}))
        self.assertIsNone(exits.fail_of(st, None))
        for broken in ("x", {"groups": "broken"}, {"groups": [1, None]}):                   # a foreign copy
            self.assertEqual(exits.fail_of({"gateway": {"exit_policy": broken}}, {"via": "pool"}), "block")

    def test_cli_says_the_fail_of_the_group(self):
        st = {"gateway": {"exit_policy": _policy("block", "direct")}}
        self.assertIn("при отказе — напрямую", cli._route_text({"via": "group:" + GID, "fail": "block"}, st=st))
        self.assertNotIn("при отказе", cli._route_text({"via": "pool", "fail": "block"}, st=st))
        st = {"gateway": {"exit_policy": _policy("direct")}}
        self.assertIn("при отказе — напрямую", cli._route_text({"via": "pool", "fail": "block"}, st=st))
        self.assertNotIn("при отказе", cli._route_text({"via": "block", "fail": "block"}, st=st))

    def test_the_copy_says_the_fail_of_the_group(self):
        gw = {"exit_policy": _policy("block", "direct")}
        names = backup._group_names(gw)
        w = backup._route_words({"via": "group:" + GID, "fail": "block"}, {}, names, gw)
        self.assertEqual(w, "через группу «Дом», при отказе — напрямую")
        self.assertEqual(backup._route_words({"via": "pool", "fail": "block"}, {}, names, gw),
                         backup._route_words({"via": "pool", "fail": "block"}, {}, names))
        gw = {"exit_policy": _policy("direct")}
        self.assertIn("при отказе — напрямую", backup._route_words({"via": "pool", "fail": "block"}, {}, {}, gw))

    def test_the_bot_says_the_fail_of_the_group(self):
        st = {"gateway": {"exit_policy": _policy("direct")}}
        self.assertIn(i18n.tr("bot.gw.route_fail_direct"), bot_gw._route_text({"via": "pool", "fail": "block"}, st))
        st = {"gateway": {"exit_policy": _policy("block")}}
        self.assertNotIn(i18n.tr("bot.gw.route_fail_direct"), bot_gw._route_text({"via": "pool", "fail": "block"}, st))

    def test_the_scheme_warning_falls_as_main(self):
        ctx = scheme._Ctx([], {})
        st = {"gateway": {"exit_policy": _policy("direct")}}
        w = scheme._exit_warnings(st, ctx, "pool", "block")
        self.assertEqual(w, [i18n.tr("panel.scheme.warnPoolDead", fall=i18n.tr("panel.scheme.direct"))])
        st = {"gateway": {"exit_policy": _policy("block")}}
        w = scheme._exit_warnings(st, ctx, "pool", "block")
        self.assertEqual(w, [i18n.tr("panel.scheme.warnPoolDead", fall=i18n.tr("panel.scheme.block"))])


class FailOfTraceTest(TT.TraceCase):
    def test_trace_says_the_fail_of_main(self):
        st = self.world()
        res = self.run_trace(st, TT.D1, "example.com")                  # pool, fail «block», «main» — block
        self.assertIn("при отказе выхода — блок", self.texts(res))
        st["gateway"]["exit_policy"] = _policy("direct")
        state.save(st)
        st = state.load()
        res = self.run_trace(st, TT.D1, "example.com")                  # «main» says «direct»: the route goes direct
        self.assertIn("при отказе выхода — напрямую", self.texts(res))
        self.assertNotIn("при отказе выхода — блок", self.texts(res))

    def test_cli_device_list_and_scheme_read_the_state(self):
        st = self.world()
        st["gateway"]["exit_policy"] = _policy("direct")
        state.save(st)
        out, _ = self.ok("gw", "device", "list")
        line = [l for l in out.splitlines() if "Телефон" in l][0]
        self.assertIn("при отказе — напрямую", line)
        out, _ = self.ok("gw", "scheme")
        self.assertIn("при отказе — напрямую", [l for l in out.splitlines() if "Телефон" in l][0])


class ClockAdviceTest(unittest.TestCase):
    """З2 (REV-s1-b2): «часы не синхронизированы» says what to do — a time service, not «the watchdog fixes it»."""

    def test_own_advice_ru_en(self):
        for lang in ("ru", "en"):
            with i18n.use_lang(lang):
                item, text = summary.advice("gw_clock")
                # B6 (REV-s1-b4 П-2): no item of the menu (none fixes the clock), the command once — in the problem
                self.assertEqual(item, 0)
                self.assertNotEqual(text, i18n.tr("summary.advice.gw"))
                for w in ("timedatectl set-ntp true", "chrony", "systemd-timesyncd"):
                    self.assertNotIn(w, text)
                    self.assertIn(w, i18n.tr("gw_live.clock_unsynced"))

    def test_log_line_says_it_once(self):
        for lang in ("ru", "en"):
            with i18n.use_lang(lang):
                t = i18n.tr("gw_collect.log.clock_unsynced")
                self.assertEqual(t.count("синхронизир" if lang == "ru" else "synchronis"), 1, t)
                self.assertIn("chrony", t)
        self.assertIn("сборщика Kaskad", i18n.tr("gw_live.event.node_online_restart", since="1", until="2",
                                                  minutes="3"))


class GoodNewsTest(unittest.TestCase):
    """П-2 (REV-s1-b2): «ключ проверен — работает со всеми параметрами» (xray_ext) is a note, not a warning — and not
    one of «ещё в пуле ключей с предупреждениями»; said in the core's words, not the script's line."""

    LINES = ["  something the script said", ops.MARK + "egress_warn:vless_key:3:xray_ext",
             "⚠ legacy", ops.MARK + "egress_warn:vless_key:2:xray_legacy"]

    def test_a_note_not_a_warning(self):
        with i18n.use_lang("ru"):
            good = i18n.tr("gw_ops.script_warn.vless_key.xray_ext", n="3")
            self.assertEqual(ops._script_notes(self.LINES), [good])
            warns = ops._script_warnings(self.LINES)
            self.assertNotIn(good, warns)
            self.assertEqual(warns, [i18n.tr("gw_ops.script_warn.vless_key.xray_legacy", n="2")])
            self.assertEqual(ops._script_warnings(self.LINES[:2], set([1])), [])    # not «ещё N с предупреждениями»
            self.assertEqual(ops._script_notes(["⚠ no marks"]), [])
        self.assertIn("xray_ext", ops.VLESS_KEY_CODES)
        self.assertIn("xray_ext", ops.GOOD_KEY_CODES)


class SetElementRuonlyTest(C1.S1World):
    """T4 П1: a fallback block with «Россия напрямую» — gw_ruonly and gw_live in one transaction, gw_ruonly first; no
    gw_ruonly in the table — a plain block, never gw_live alone (the node's services). gw_ruonly is «Россия напрямую»:
    with the consent ⚠РФ only (D-136, B7 круг 3 — the hook passes the last line too)."""

    def consented(self):
        st = state.load()
        st["gateway"]["rf_consent"] = {"at": "2026-10-17T10:00:00Z", "via": "panel", "who": "owner", "orgs": [],
                                       "lang": "ru"}
        return st

    def test_one_transaction_ruonly_first(self):
        st = self.consented()
        seen, patch = B1._nft_scripts(self)
        with patch:
            self.assertTrue(net.set_element(self.w.runner, st, B1.IP, exits.MARK_BLOCK, ruonly=True))
        live = [x for x in seen if "add element ip kaskad gw_live { %s }" % B1.IP in x]
        self.assertTrue(live)
        for x in live:
            self.assertIn("add element ip kaskad gw_ruonly { %s }" % B1.IP, x)
            self.assertLess(x.index("gw_ruonly"), x.index("gw_live"))
        self.assertIn(B1.IP, self.w.nft.elements("gw_live"))
        self.assertIn(B1.IP, self.w.nft.elements("gw_ruonly"))
        seen[:] = []
        with patch:                                     # back to an exit: out of gw_ruonly only after the new mark
            self.assertTrue(net.set_element(self.w.runner, st, B1.IP, 0x102))
        i = [n for n, x in enumerate(seen) if "gw_route" in x][0]
        j = [n for n, x in enumerate(seen) if "delete element ip kaskad gw_ruonly" in x][0]
        self.assertLess(i, j)
        self.assertNotIn(B1.IP, self.w.nft.elements("gw_ruonly"))

    def test_no_ruonly_set_a_plain_block(self):
        st = self.consented()
        self.assertIn(B1.IP, self.w.nft.elements("gw_live"))
        # a table before stage 1: nft refuses every script that names gw_ruonly (ENOENT)
        self.w.nft.fail_apply = lambda text: ("Error: No such file or directory\n" if "gw_ruonly" in text else None)
        self.addCleanup(setattr, self.w.nft, "fail_apply", None)
        self.assertFalse(net.set_element(self.w.runner, st, B1.IP, exits.MARK_BLOCK, ruonly=True))
        self.assertEqual(self.w.nft.elements("gw_route")[B1.IP], exits.MARK_BLOCK)
        self.assertNotIn(B1.IP, self.w.nft.elements("gw_live"))

    def test_without_the_consent_a_plain_block(self):
        st = state.load()
        st["gateway"].pop("rf_consent", None)
        self.assertFalse(net.set_element(self.w.runner, st, B1.IP, exits.MARK_BLOCK, ruonly=True))
        self.assertEqual(self.w.nft.elements("gw_route")[B1.IP], exits.MARK_BLOCK)
        self.assertNotIn(B1.IP, self.w.nft.elements("gw_live"))
        self.assertNotIn(B1.IP, self.w.nft.elements("gw_ruonly"))


class ClosedOutageRestartTest(C1.S1World):
    """З4 (REV-s1-b2): a restart of the collector after a CLOSED outage (``node_online`` already told) gives no events —
    ``live.outage_open`` resets the open outage on ``node_online`` (live.py)."""

    def _offline(self, n=12):
        self.w.leg(2, probe=False)
        self.w.curl.observatory = {"gx-" + self.vid: False}
        self.w.net_up = False
        for _ in range(n):
            self.round(5)

    def _online(self):
        self.w.net_up = True
        self.w.leg(2)
        self.w.curl.observatory = {"gx-" + self.vid: True}

    def test_no_second_node_online_after_a_restart(self):
        for _ in range(3):
            self.round()
        self._offline()
        self._online()
        self.round()
        self.assertIsNone(live.outage_open())
        collect.MEM.reset()                                   # a restart of the collector process
        collect.LIVE = live.Live()
        for _ in range(3):
            self.round()
        kinds = [e["kind"] for e in self.events() if e["kind"] in ("node_offline", "node_online")]
        self.assertEqual(kinds, ["node_offline", "node_online"])


class SocksOutOfBlockTest(C1.S1World):
    """З4 (REV-s1-b2, T45 З2): a SOCKS5 account leaving «block» goes live (``xray.hot_plan``) — no restart of xray is
    promised; going INTO «block» by choice restarts it (its connections)."""

    def test_out_of_block_no_restart_into_block_a_restart(self):
        import copy
        from tests.fakes_gw import register_dummies
        register_dummies(("socks5", {"proto": "tcp", "default_port": 1080, "ifaces": [], "xray_inbound": {
            "tag": "socks-in", "listen": "0.0.0.0", "port": 1080, "protocol": "socks"}}))
        st = state.load()
        st["gateway"]["inbounds"]["socks5"] = {"enabled": True, "port": 1080}
        d = st["gateway"]["devices"][0]
        d["access"] = [k for k in d.get("access") or [] if k != "vless"] + ["socks5"]
        d["route"] = {"via": "block", "fail": "block"}
        new = copy.deepcopy(st)
        new["gateway"]["devices"][0]["route"] = {"via": "pool", "fail": "block"}
        ctx = scheme._Ctx([], {})
        what = lambda a, b: [x["what"] for x in scheme.disruptions(a, b, ctx, ready=False)]
        self.assertNotIn("xray", what(st, new))                  # out of «block»: live, no restart
        self.assertIn("xray", what(new, st))                     # into «block»: a restart


class BootFileXportsTest(C1.S1World):
    """REV-s1-b3 П1: the boot file kaskad.nft draws gw_xports as the plan ∪ the guarded ports of the config of xray on
    disk — after a reboot xray starts THAT config (a removed exit whose new config xray refused: its probe-<id> is
    still there), so its port is closed to non-root from the first packet."""

    def test_the_port_of_the_config_on_disk_is_in_the_boot_file(self):
        import json
        import os
        import re
        from kaskad import nft, util
        from kaskad.gw import xray
        st = state.load()
        stale = xray.PROBE_PORT_BASE + 9
        path = xray.config_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        cfg = xray._read_config(path) or {"inbounds": []}
        cfg["inbounds"] = list(cfg.get("inbounds") or []) + [
            {"tag": "probe-e-00000000ff", "listen": "127.0.0.1", "port": stale, "protocol": "socks"},
            {"tag": "probe-e-00000000fe", "listen": "0.0.0.0", "port": stale + 1, "protocol": "socks"}]   # not ours
        with open(path, "w") as f:
            json.dump(cfg, f)
        nft.apply(st, self.w.runner)
        boot = re.search(r"set gw_xports \{[^}]*elements = \{([^}]*)\}", util.read_text(nft.paths.nft_file()))
        self.assertIsNotNone(boot)
        got = [x.strip() for x in boot.group(1).split(",")]
        self.assertIn(str(stale), got)
        self.assertIn(str(xray.POOL_SOCKS_PORT), got)
        self.assertNotIn(str(stale + 1), got)
        with open(path, "w") as f:                       # a broken config: the plan as it is, nothing breaks
            f.write("{broken")
        nft.apply(st, self.w.runner)
        boot = re.search(r"set gw_xports \{[^}]*elements = \{([^}]*)\}", util.read_text(nft.paths.nft_file()))
        self.assertNotIn(str(stale), [x.strip() for x in boot.group(1).split(",")])


class PoolChangeDuringPauseTest(C1.S1World):
    """REV-s1-b3 П2: a table of 3.3.5 (no gw_xports), the full apply failed — the pause (XPORTS_RETRY_S) began; a change
    of the pool during it is applied in full in that very round (TPROXY rules of a new VLESS exit), not ``update_map``;
    a success ends the pause; a round with the set in place never starts a pause."""

    def test_the_pool_changes_now(self):
        from unittest import mock
        from kaskad import nft
        from kaskad.errors import KaskadError
        from kaskad.gw import xray
        from tests.fakes_gw import VLESS_A, awg_line, write_pool
        from tests.gw import test_s1_b3 as B3
        B3._table_335(self)
        collect.MEM.reset()
        calls = []
        real = nft.apply

        def fail(*a, **kw):
            calls.append(1)
            raise KaskadError("nft")
        with mock.patch.object(nft, "apply", fail), mock.patch.object(xray, "apply", lambda *a, **kw: {}):
            self.round()
            self.assertEqual(len(calls), 1)                            # failed: the pause began
            self.assertIsNotNone(collect.MEM.xports_failed)
            self.round()
            self.assertEqual(len(calls), 1)                            # in the pause: the map only
        write_pool([VLESS_A])                                          # the pool changes during the pause
        with mock.patch.object(nft, "apply", lambda *a, **kw: calls.append(2) or real(*a, **kw)):
            self.round()
        self.assertEqual(calls[-1], 2)                                 # applied in full in this round
        self.assertIsNone(collect.MEM.xports_failed)                   # the success ended the pause
        self.assertIn("gw_xports", self.w.nft.table()["sets"])
        with mock.patch.object(nft, "apply", fail):                    # the set in place: a failure starts no pause
            write_pool([VLESS_A, awg_line(2)])                         # a real change of the pool again
            self.round()
            self.assertEqual(calls.count(1), 2)                        # tried in full, failed
        self.assertIsNone(collect.MEM.xports_failed)


class NoXportsWordsTest(C1.S1World):
    """REV-s1-b3 П3: «xray не обновлён: нет gw_xports» gives no advice in a circle (no «gw apply» — the person ran it),
    and «rules update» (``rules.ops.apply_system``) shows the warnings of xray as gw/ops._apply does."""

    def test_no_circle_and_shown_by_rules_update(self):
        from unittest import mock
        from kaskad import nft
        from kaskad.gw import xray
        from kaskad.rules import apply as rapply, ops as rops
        for lang in ("ru", "en"):
            with i18n.use_lang(lang):
                self.assertNotIn("gw apply", i18n.tr("gw_xray.warn.no_xports"))
        w = i18n.tr("gw_xray.warn.no_xports")
        with mock.patch.object(xray, "apply", lambda *a, **kw: {"warnings": [w], "notes": ["VLESS note"]}), \
                mock.patch.object(nft, "apply", lambda *a, **kw: {}), \
                mock.patch.object(rapply, "ensure_kernel", lambda *a, **kw: {"action": "none"}):
            res = rops.apply_system(self.w.runner)
        self.assertIn(w, res["warnings"])
        self.assertIn("VLESS note", res["warnings"])


class NotNeededStopFailedTest(C1.S1World):
    """REV-s1-b3 «было до правки»: «xray не нужен», but ``stop`` could not stop the unit — gw_xports is not shrunk (its
    ports are still open by xray; ``gsys.unit_active`` before ``xports_settle(())``)."""

    def test_the_set_stays_while_the_unit_runs(self):
        from unittest import mock
        from kaskad import secrets
        from kaskad.gw import xray
        st, sec = state.load(), secrets.load()
        xray.apply(st, sec, self.w.runner, exits.pool(self.w.runner), seq=xray.next_seq(), fp="up")
        self.assertTrue(self.w.sysd.units[xray.UNIT]["active"])
        ports = sorted(self.w.nft.elements("gw_xports"))
        self.assertIn(str(xray.POOL_SOCKS_PORT), ports)
        with mock.patch.object(xray, "needed", lambda *a: False), mock.patch.object(xray, "stop", lambda r: False):
            res = xray.apply(state.load(), sec, self.w.runner, exits.pool(self.w.runner))
        self.assertFalse(res["stopped"])
        self.assertTrue(self.w.sysd.units[xray.UNIT]["active"])
        self.assertEqual(sorted(self.w.nft.elements("gw_xports")), ports)


if __name__ == "__main__":
    unittest.main()
