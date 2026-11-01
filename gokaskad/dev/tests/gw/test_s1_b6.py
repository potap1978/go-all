"""B6 (the batch before КТ1): REV-s1-b5 — the panel reads ``gen`` as ``state.load`` (З1, D-135); the old config sending
Russia directly is closed against what RAN, not only against the disk (З2); xray that did not stop is said so and its
file stays (П1); closing without gw_xports and a fresh consent (П2); one text, a wider reason (П3); no deferred restart
when the new config takes direct rules away (П4)."""

import json
import os
from unittest import mock

from kaskad import paths, secrets, snapshot, state, util
from kaskad.errors import KaskadError
from kaskad.gw import exits, model, net, xray
from kaskad.gw import sys as gsys
from kaskad.i18n import tr
from kaskad.rules import rfgate
from tests.gw.test_s1_b5 import CONF, REFUSED, RuClosedCase, direct_users
from tests.helpers import RootCase
from tests.test_state_gen import FILE_333

UNIT = "kaskad-gw-xray.service"


class PanelGenTest(RootCase):
    """REV-s1-b5 З1: a file of 3.3.x without ``gen`` — the scheme of the panel, the rules page and the bot read it as the
    core does (``state.load``: an UPDATED node, «Россия напрямую» of 3.3.x by default), never «всё через выход»."""

    def test_the_readers_past_load_see_an_updated_node(self):
        from kaskad.bot import data as bot_data
        from kaskad.panel import scheme
        from kaskad.rules import panel as rules_panel
        util.atomic_write(paths.state(), json.dumps(FILE_333), 0o600)
        core = state.load()
        self.assertEqual(model.rules_conf(core)["default"], "ru_direct")         # 3.3.x: the default of the node
        for name, st in (("scheme", scheme.read_state()), ("rules", rules_panel._state()),
                         ("bot", bot_data.state_doc())):
            self.assertEqual(st.get("gen"), 1, name)
            self.assertFalse(state.born_new(st), name)
            self.assertEqual(model.rules_conf(st), model.rules_conf(core), name)
            self.assertEqual((rfgate.mode(st), rfgate.allowed(st)), (rfgate.mode(core), rfgate.allowed(core)), name)
        self.assertEqual(scheme.default_profile(scheme.read_state()), "ru_direct")
        self.assertNotIn("gen", snapshot.read_json(paths.state()))               # the cached object is not changed

    def test_a_present_field_is_kept_as_it_is(self):
        self.assertEqual(state.as_loaded({"gen": 2}), {"gen": 2})
        self.assertEqual(state.as_loaded({"gen": "1"}), {"gen": "1"})              # broken: a NEW node, as after load
        self.assertTrue(state.born_new(state.as_loaded({"gen": "1"})))
        doc = {"schema": 1}
        self.assertEqual(state.as_loaded(doc), {"schema": 1, "gen": 1})
        self.assertEqual(doc, {"schema": 1})
        self.assertIsNone(state.as_loaded(None))


class ConsentFormTest(RootCase):
    """REV-s1-b5 П2: what ``grant`` writes passes the check of the form at once, in the same object (not only after a
    save and a read) — for every way of the consent and the organisations as given."""

    def test_a_fresh_consent_is_a_consent(self):
        for via in sorted(rfgate.VIAS):
            st = {"gen": 2, "gateway": {"rules": {"default": "ru_direct"}}}
            got = rfgate.grant(st, via, "admin", "ru", ("meta", "fbk"), log=[])
            self.assertIs(rfgate._consent(st), got, via)
            self.assertIsInstance(got["orgs"], list)
            self.assertTrue(rfgate.allowed(st), via)
        st = {"gen": 2, "gateway": {"rules": {"default": "ru_direct"}}}
        rfgate.grant(st, "nonsense", None, "en", [], log=[])
        self.assertEqual(st["gateway"]["rf_consent"]["via"], "cli")
        self.assertTrue(rfgate.allowed(st))


class CloseRuB6Case(RuClosedCase):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(xray, "KILL_WAIT_S", 0.0, create=True)        # the fake is synchronous: no wait after kill
        p.start()
        self.addCleanup(p.stop)

    def without_consent(self):
        self.socks_world()
        with state.transaction() as st:
            st["gateway"].pop("rf_consent")                                    # a hand edit: no consent
        return state.load(), secrets.load()


class AfterTheFileTest(CloseRuB6Case):
    def test_a_failure_between_the_file_and_the_restart_closes(self):
        # REV-s1-b5 З2: the new config is on the disk, the restart never came (enable / daemon-reload failed) — the old
        # process with the direct rules runs; before B6 «nothing on the disk sends Russia directly» kept it
        new, sec = self.without_consent()
        with mock.patch.object(xray, "hot_apply", return_value=False), \
                mock.patch.object(gsys, "ensure_unit", side_effect=KaskadError("daemon-reload failed")):
            with self.assertRaises(KaskadError) as e:
                xray.apply(new, sec, self.w.runner, exits.pool(self.w.runner))
        self.assertGreater((e.exception.data or {}).get("ru_closed", 0), 0)
        self.assertFalse(xray.status(self.w.runner)["active"])
        self.assertFalse(os.path.exists(self.path(CONF)))
        self.ok("gw", "apply")                                                  # the next apply runs the new one
        self.assertTrue(xray.status(self.w.runner)["active"])
        self.assertEqual(direct_users(json.loads(self.read(CONF))), [])

    def test_a_change_the_running_xray_took_by_the_api_is_not_closed(self):
        new, sec = self.without_consent()
        with mock.patch.object(gsys, "ensure_unit", side_effect=KaskadError("daemon-reload failed")):
            with self.assertRaises(KaskadError) as e:
                xray.apply(new, sec, self.w.runner, exits.pool(self.w.runner))
        self.assertNotIn("ru_closed", e.exception.data or {})                    # the routing went in by the API
        self.assertTrue(xray.status(self.w.runner)["active"])


class StopsTest(CloseRuB6Case):
    def test_disable_failing_still_stops(self):
        # REV-s1-b5 П1: «disable --now» refused — the unit is stopped all the same, then the file goes
        new, sec = self.without_consent()
        self.w.sysd.fail["disable"] = "Failed to disable unit: Access denied\n"
        self.w.xray.fail = REFUSED
        with self.assertRaises(KaskadError) as e:
            xray.apply(new, sec, self.w.runner, exits.pool(self.w.runner))
        self.assertGreater(e.exception.data.get("ru_closed", 0), 0)
        self.assertFalse(xray.status(self.w.runner)["active"])
        self.assertFalse(os.path.exists(self.path(CONF)))

    def test_kill_is_the_last_step(self):
        new, sec = self.without_consent()
        self.w.sysd.fail.update({"disable": "Access denied\n", "stop": "Access denied\n"})
        self.w.xray.fail = REFUSED
        with self.assertRaises(KaskadError) as e:
            xray.apply(new, sec, self.w.runner, exits.pool(self.w.runner))
        self.assertGreater(e.exception.data.get("ru_closed", 0), 0)
        self.assertIn(["systemctl", "kill", "--signal=SIGKILL", UNIT], self.w.sysd.calls)
        self.assertFalse(xray.status(self.w.runner)["active"])

    def test_xray_alive_keeps_its_file_and_says_so(self):
        self.socks_world()
        self.w.sysd.fail.update({"disable": "Access denied\n", "stop": "Access denied\n"})
        self.w.sysd.units[UNIT]["unkillable"] = True
        self.w.xray.fail = REFUSED
        rc, out, err = self.run_cli("rules", "profile", "user", "Мама", "all_exit")
        self.assertEqual(rc, 0, err)
        self.assertTrue(xray.status(self.w.runner)["active"])
        # B7 (REV-s1-b6-sec Н-1): the file goes BEFORE the stop (no restart picks it up), the mark keeps what may run
        self.assertFalse(os.path.exists(self.path(CONF)))
        self.assertTrue(xray.pending())
        self.assertIn("kaskadctl gw apply", err)
        self.assertIn("kill", err)
        self.assertNotIn(tr("gw_ops.warn.xray_ru_closed", err="").strip(), err)
        self.assertNotIn("Работает прежний конфиг", err)
        self.assertNotIn("xray шлюза остановлен", err)
        del self.w.sysd.fail["stop"]                                             # the next call closes it
        with self.assertRaises(KaskadError) as e:
            xray.apply(state.load(), secrets.load(), self.w.runner, exits.pool(self.w.runner))
        self.assertGreater(e.exception.data.get("ru_closed", 0), 0)
        self.assertFalse(xray.status(self.w.runner)["active"])
        self.assertFalse(os.path.exists(self.path(CONF)))


class NoXportsTest(CloseRuB6Case):
    def test_closing_without_gw_xports_says_one_thing(self):
        # REV-s1-b5 П2, П3: no gw_xports — the new config is not put in place; the old one sent Russia directly: xray
        # is stopped, and «not updated … catches up on its own» is not said next to «stopped»
        new, sec = self.without_consent()
        with mock.patch.object(net, "xports_add", return_value=False):
            res = xray.apply(new, sec, self.w.runner, exits.pool(self.w.runner))
        self.assertTrue(res.get("xports_missing"))
        self.assertGreater(res.get("ru_closed", 0), 0)
        self.assertFalse(xray.status(self.w.runner)["active"])
        self.assertFalse(os.path.exists(self.path(CONF)))
        self.assertIn(tr("gw_xray.warn.ru_closed_xports"), res["warnings"])
        self.assertNotIn(tr("gw_xray.warn.no_xports"), res["warnings"])
        self.assertNotIn(tr("gw_xray.warn.ru_closed"), res["warnings"])

    def test_without_gw_xports_and_nothing_to_close_the_old_runs(self):
        st = self.socks_world()
        new = json.loads(json.dumps(st))
        new["gateway"]["rules"]["block_torrent"] = True
        with mock.patch.object(net, "xports_add", return_value=False):
            res = xray.apply(new, secrets.load(), self.w.runner, exits.pool(self.w.runner))
        self.assertIn(tr("gw_xray.warn.no_xports"), res["warnings"])
        self.assertNotIn("ru_closed", res)
        self.assertTrue(xray.status(self.w.runner)["active"])


class NotDeferredTest(CloseRuB6Case):
    def test_taking_direct_rules_away_restarts_at_once(self):
        # REV-s1-b5 П4: a restart the storm would defer is done now when the new config takes direct rules away
        new, sec = self.without_consent()
        with mock.patch.object(xray, "hot_plan", return_value=None):
            res = xray.apply(new, sec, self.w.runner, exits.pool(self.w.runner), defer_restart=True)
        self.assertFalse(res.get("deferred"))
        self.assertTrue(res["restarted"])
        self.assertEqual(direct_users(json.loads(self.read(CONF))), [])

    def test_another_change_is_still_deferred(self):
        st = self.socks_world()
        new = json.loads(json.dumps(st))
        new["gateway"]["rules"]["block_torrent"] = True
        before = self.read(CONF)
        with mock.patch.object(xray, "hot_plan", return_value=None):
            res = xray.apply(new, secrets.load(), self.w.runner, exits.pool(self.w.runner), defer_restart=True)
        self.assertTrue(res.get("deferred"))
        self.assertEqual(self.read(CONF), before)


if __name__ == "__main__":
    import unittest
    unittest.main()


# ── REV-s1-b4: the fail step as it ACTS in the panel, MCP, the bot (З-1, П-1, «было до правки») ──

from kaskad import i18n                                              # noqa: E402
from kaskad.bot import gateway as bot_gw                             # noqa: E402
from kaskad.gw import cli as gw_cli                                  # noqa: E402
from kaskad.gw import policy as P                                    # noqa: E402
from kaskad.panel import scheme                                      # noqa: E402
from tests.fakes_gw import VLESS_B, write_pool                       # noqa: E402
from tests.panel.panelcase import PanelCase                          # noqa: E402

GID = "g-123456"


def _with_groups(st, main_fail="block", home_fail="direct"):
    """«Основная» with ``main_fail`` and a foreign group «Дом» (GID) with ``home_fail`` — in the stored policy of ``st``."""
    pol = P.load(st)
    pol["groups"] = [g for g in pol["groups"] if g.get("id") != GID]
    P.group(pol, P.MAIN)["fail"] = main_fail
    home = P.default_group(GID, "abroad", "compat", name="Дом")
    home["fail"] = home_fail
    pol["groups"].append(home)
    st["gateway"]["exit_policy"] = pol
    return st


class ActsTextTest(RootCase):
    """З-1: the scheme says the fail step that ACTS (the default of the node, the route of a device) — «при отказе —
    напрямую» when the group of the route sends directly, even with its own «block»; a group by its name."""

    def setUp(self):
        super().setUp()
        cm = i18n.use_lang("ru")
        cm.__enter__()
        self.addCleanup(cm.__exit__, None, None, None)

    def test_eff_text_and_the_default_say_the_group(self):
        st = _with_groups({"gateway": {}}, main_fail="direct")
        direct = tr("panel.scheme.failDirect")
        eff = scheme._eff_text(st, {"via": "pool", "fail": "block", "from": "default"}, [])
        self.assertIn(direct, eff)
        eff = scheme._eff_text(st, {"via": "group:" + GID, "fail": "block", "from": "device"}, [])
        self.assertIn(direct, eff)
        self.assertIn("«Дом»", eff)
        self.assertNotIn(GID, eff)
        st = _with_groups({"gateway": {}}, main_fail="block", home_fail="block")
        self.assertNotIn(direct, scheme._eff_text(st, {"via": "pool", "fail": "block", "from": "default"}, []))
        # «было → стало» of an edit names the policy of the route itself
        st = _with_groups({"gateway": {}}, main_fail="direct")
        self.assertNotIn(direct, scheme._route_text(st, {"via": "pool", "fail": "block"}, []))

    def test_the_bot_names_the_group(self):
        st = _with_groups({"gateway": {}}, main_fail="block", home_fail="direct")
        s = bot_gw._route_text({"via": "group:" + GID, "fail": "block"}, st)
        self.assertIn("«Дом»", s)
        self.assertNotIn(GID, s)
        self.assertIn(tr("bot.gw.route_fail_direct"), s)
        with i18n.use_lang("en"):
            self.assertIn("“Дом”", bot_gw._route_text({"via": "group:" + GID, "fail": "block"}, st))
        s = bot_gw._route_text({"via": "group:g-654321", "fail": "block"}, st)       # a group gone: its id
        self.assertIn("g-654321", s)


class FailWithoutDecisionTest(RootCase):
    """П-1: without a decision (the CLI, the bot, the copy) a new exit not yet placed is in the group of its hint, as the
    collector places it — the answer equals the decision's."""

    def test_an_unplaced_exit_takes_the_group_of_its_hint(self):
        write_pool([VLESS_B])
        eid = exits.exit_id(VLESS_B)
        st = _with_groups({"gateway": {"exits": {eid: {"group": GID}}}}, main_fail="block", home_fail="direct")
        self.assertIsNone(P.group_of(P.load(st), eid))                        # not placed in the stored policy
        route = {"via": "exit:" + eid, "fail": "block"}
        want = exits.fail_of(st, route, {"policy": P.load(st, [eid])})        # as the decision of the collector
        self.assertEqual(want, "direct")
        self.assertEqual(exits.fail_of(st, route), want)
        self.assertIn("при отказе — напрямую", gw_cli._route_text(route, st=st))
        self.assertIn(tr("bot.gw.route_fail_direct"), bot_gw._route_text(route, st))
        self.assertEqual(exits.fail_of(st, route, pool=[]), "block")          # the copy of a node without the key


class PanelFailEffTest(PanelCase):
    """З-1: /api/gw/people and MCP ``scheme`` carry ``fail_eff`` (as it acts) and the name of a group; ``via_fails`` —
    the targets whose group sends directly (the panel warns for a route it is about to set)."""

    def setUp(self):
        super().setUp()
        self.gateway()
        with state.transaction() as st:
            _with_groups(st, main_fail="direct", home_fail="direct")
            st["gateway"]["users"][0]["route"] = {"via": "group:" + GID, "fail": "block"}
        snapshot.forget()

    def test_people_say_what_acts(self):
        doc = self.ok("GET", "/api/gw/people")
        u = doc["users"][0]
        self.assertEqual((u["route"]["fail"], u["route"]["fail_eff"], u["route"]["group_name"]),
                         ("block", "direct", "Дом"))
        self.assertEqual(doc["routes"]["default"]["fail_eff"], "direct")
        self.assertEqual(doc["via_fails"].get("pool"), "direct")
        self.assertEqual(doc["via_fails"].get("group:" + GID), "direct")
        ex = self.ok("GET", "/api/gw/exits")
        self.assertEqual(ex["default_route"]["fail_eff"], "direct")
        with state.transaction() as st:
            P.group(st["gateway"]["exit_policy"], P.MAIN)["fail"] = "block"
        snapshot.forget()
        doc = self.ok("GET", "/api/gw/people")
        self.assertEqual(doc["routes"]["default"]["fail_eff"], "block")
        self.assertNotIn("pool", doc["via_fails"])

    def test_mcp_scheme_says_what_acts(self):
        doc = gw_cli.scheme_doc(self.w.runner)
        self.assertEqual(doc["default_route"]["fail_eff"], "direct")
        p = doc["people"][0]
        self.assertEqual((p["route"]["fail_eff"], p["route"]["group_name"]), ("direct", "Дом"))
        with open(os.path.join(os.path.dirname(gw_cli.__file__), "..", "mcp", "AGENT_GUIDE.md"), encoding="utf-8") as f:
            guide = f.read()
        self.assertIn("fail_eff", guide)


# ── REV-s1-b4 П-2: the clock in the SSH menu — the advice once, no item 17 ──

import re                                                            # noqa: E402
import shutil                                                        # noqa: E402
import subprocess                                                    # noqa: E402
import unittest                                                      # noqa: E402

from kaskad import summary                                           # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@unittest.skipUnless(shutil.which("bash"), "нет bash")
class ClockMenuTest(unittest.TestCase):
    def loop(self):
        """The loop of «Поиск неполадок» (21) over the problems, from install.sh."""
        with open(os.path.join(ROOT, "install.sh"), encoding="utf-8") as f:
            sh = f.read()
        body = sh[sh.index("\nk3_check_screen() {"):]
        body = body[:body.index("\n}\n")]
        m = re.search(r"\n(\s*for \(\(i = 0; i < n; i\+\+\)\); do\n.*?\n\s*done)\n", body, re.S)
        return m.group(1)

    def run_loop(self, probs):
        stubs = ('k3_pg_msg() { printf "%s\\n" "$1"; }\nk3_pg_gap() { :; }\n'
                 'tf() { printf "Пункт %s:" "$2"; }\nWHITE= NC= RED=\n')
        arrays = "K3_HP_ITEM=(%s)\nK3_HP_DETAIL=(%s)\nK3_HP_ADVICE=(%s)\nn=%d\nitems=\n" % (
            " ".join("'%s'" % p[0] for p in probs), " ".join("'%s'" % p[1] for p in probs),
            " ".join("'%s'" % p[2] for p in probs), len(probs))
        src = stubs + arrays + self.loop() + '\nprintf "ITEMS=%s\\n" "$items"\n'
        return subprocess.run(["bash", "-c", src], stdout=subprocess.PIPE, universal_newlines=True, check=True).stdout

    def test_the_clock_says_its_advice_once_without_an_item(self):
        with i18n.use_lang("ru"):
            p = summary.problem("gw_clock", tr("gw_live.clock_unsynced"), "live")
            other = summary.problem("gw_service", "xray не работает", "live")
        self.assertEqual(p["item"], 0)
        out = self.run_loop([(p["item"], p["detail"], p["advice"]), (other["item"], other["detail"], other["advice"])])
        self.assertEqual(out.count("timedatectl set-ntp true"), 1, out)
        self.assertNotIn("Пункт 0", out)
        self.assertNotIn("Пункт 17: " + p["advice"], out)
        self.assertIn("  " + p["advice"], out)
        self.assertIn("Пункт 17: " + other["advice"], out)
        self.assertIn("ITEMS=17\n", out)                                      # 0 is never offered to go to


# ── REV-s1-b4 П-3: the good news of a key is a note — exit add/del and a subscription; the port in tofu_snap ──

from kaskad.gw import exitsubs, ops                                  # noqa: E402
from tests.fakes_gw import VLESS_A                                   # noqa: E402
from tests.test_334_tofu import TofuCase                             # noqa: E402
from tests.test_334_xray import key as tofu_key                      # noqa: E402
from tests.test_335_rename import RenameCase, b64, key as sub_key    # noqa: E402
from tests.test_gw_exitsubs import IP_URL                            # noqa: E402


def _marks(*nos):
    return "".join("@K3:egress_warn:vless_key:%d:xray_ext\n@K3:egress_warn:vless_key:%d:xray_legacy\n" % (n, n)
                   for n in nos)


class NotesTest(RenameCase):
    def good(self, n):
        return tr("gw_ops.script_warn.vless_key.xray_ext", n=str(n))

    def test_exit_add_and_del_carry_notes_not_warnings(self):
        write_pool([VLESS_A])
        real = self.w.script.egress_add

        def add(text):
            rc, out, err = real(text)
            return rc, out + _marks(2), err
        self.w.script.egress_add = add
        res = ops.exit_add(self.w.runner, VLESS_B)
        self.assertEqual(res["notes"], [self.good(2)])
        self.assertNotIn(self.good(2), res["warnings"])
        self.assertTrue(res["warnings"])                                      # the real warning stays one
        self.w.script.del_rebuild = (0, _marks(1))
        res = ops.exit_del(self.w.runner, exits.exit_id(VLESS_B))
        self.assertEqual(res["notes"], [self.good(1)])
        self.assertNotIn(self.good(1), res.get("warnings") or [])

    def test_a_subscription_says_the_news_of_its_keys(self):
        write_pool([VLESS_A])
        real = self.w.script.egress_replace

        def replace(want, text):
            rc, out, err = real(want, text)
            return rc, out + _marks(1, 2), err                               # 1 — the key of the node, not the sub's
        self.w.script.egress_replace = replace
        res = self.add(IP_URL, b64([sub_key("10GB left")]))
        self.assertEqual(res["notes"], [self.good(2)])
        self.assertEqual(res["result"]["notes"], [self.good(2)])
        self.assertNotIn(self.good(2), res["warnings"])


class TofuPortTest(TofuCase):
    def test_a_port_with_spaces_is_asked_at_that_port(self):
        # REV-s1-b4 П-3 (m25): the port by ``_netloc_port``, never ``urlparse().port`` (3.12+ refuses « 8443»)
        line = tofu_key(1, "&allowInsecure=1").replace(":8443?", ": 8443?")
        asked = []

        def fetch(h, p, sni, timeout, alpn):
            asked.append((h, p, sni))
            return "cd" * 32
        out = self.p.tofu_snap([line], first=True, fetch=fetch)
        self.assertEqual(asked, [("198.51.100.21", 8443, "a.example.com")])
        self.assertEqual([s for s, _ in out], ["pinned"])


class PortOfSpacesTest(TofuCase):
    def test_the_core_and_the_parser_read_a_port_alike(self):
        # REV-s1-b4 П-4: «h: » is no port in both (it was 443 in the core)
        for netloc in ("u@198.51.100.7: ", "u@198.51.100.7:", "u@198.51.100.7", "u@198.51.100.7: 443",
                       "u@198.51.100.7:443 ", "u@[2001:db8::1]: ", "u@[2001:db8::1]:", "u@198.51.100.7:0",
                       "u@198.51.100.7:65536", "u@198.51.100.7:４４３"):
            self.assertEqual(exits._netloc_port(netloc), self.p._netloc_port(netloc), netloc)
        self.assertIsNone(exits._netloc_port("u@198.51.100.7: "))
        self.assertEqual(exits._netloc_port("u@198.51.100.7:"), 443)
