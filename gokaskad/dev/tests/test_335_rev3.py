"""3.3.5, REV-335-3 (k3/stand/logs/REV-335-3.md, the decisions of the coordinator 15.10):
№1 «Маршруты перешли на …» — only the truth: the result of the handover is read AFTER install.sh (the mark
   egress_handover:<n>); the handover failed or the other copy may not take the routes (off for the gateway, a problem
   in the pool) — the line stays, «Маршруты не перешли — удаление отменено, повторите» (RU+EN);
№2 a subscription moves the routes only after its new pool is in place;
№3 «работать будет только один» — only AmneziaWG: two lines of one VLESS account both work;
№4 the recheck of AmneziaWG not symmetric: a good check replaces a failed one up to 10 s, a failed one a good one only
   within P − 2 s;
№5 a step of the wall clock does not make every VLESS probe «old» (the monotonic clock);
№6 a broken live-feed.json goes aside, the feed starts anew, the history of live.json stays;
№7 _forget drops «said», «said» of an update from 3.3.4 comes from the events of live.json;
№8 «маршруты перейдут на него» — only when there are routes."""

import json
import os
import random
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from kaskad import paths, state
from kaskad.errors import ConflictError, LockedError
from kaskad.gw import collect, exits, live, model, ops
from kaskad.i18n import use_lang
from tests import jsparts
from tests.fakes_gw import VLESS_A, scenario_state, write_pool
from tests.test_335_flap import FlapCase
from tests.test_335_rev2 import TwoCopiesCase, read
from tests.test_335_samekey import BY_IP, SameKeyCase
from tests.test_gw_exits import conf_of, vpn_of
from tests.test_gw_exitsubs import GA_URL

FAILED_RU = "Маршруты не перешли — удаление отменено, повторите."
FAILED_EN = "The routes did not move — the key is not deleted, try again."


class RoutedCase(TwoCopiesCase):
    """Both copies of one key; the routes of the domain copy (the default, a person, a device) — as HandoverTest of
    test_335_rev2, without running its tests again."""

    def setUp(self):
        super().setUp()
        st, _vid, _aid = scenario_state()
        write_pool([self.ip, self.dom])
        g = st["gateway"]
        g["routes"]["default"] = {"via": "exit:" + self.old[1], "fail": "block"}
        g["users"][0]["route"] = {"via": "exit:" + self.old[1], "fail": "direct"}
        g["devices"][0]["route"] = {"via": "exit:" + self.old[1], "fail": "block"}
        g["devices"][2]["route"] = {"via": "exit:" + self.old[0], "fail": "direct"}
        state.save(st)

    def routes(self):
        g = model.peek(state.load())
        return (g["routes"]["default"], g["users"][0]["route"], g["devices"][0]["route"], g["devices"][2]["route"])


class TruthOfTheHandoverTest(RoutedCase):
    """№1: the answer of a removal says what install.sh did, not what was counted before it."""

    def vias(self):
        return [r["via"] for r in self.routes()[:3]]

    def test_the_handover_fails_the_line_stays_and_it_is_said(self):
        for lang, text in (("ru", FAILED_RU), ("en", FAILED_EN)):
            with mock.patch.object(ops, "hand_over", side_effect=LockedError("state locked")), use_lang(lang):
                with self.assertRaises(LockedError) as cm:
                    ops.exit_del(self.w.runner, self.old[1])
            self.assertEqual(str(cm.exception), text)
            self.assertEqual(self.ids(), self.old)                             # not removed
            self.assertEqual(self.vias(), ["exit:" + self.old[1]] * 3)         # nothing moved, nothing claimed

    def test_the_cli_says_it_and_keeps_the_key(self):
        with mock.patch.object(ops, "hand_over", side_effect=LockedError("state locked")), use_lang("ru"):
            rc, out, err = self.run_cli("gw", "exit-del", self.old[1])
        self.assertEqual(rc, 6)                                                 # «повторите»: the lock, as busy
        self.assertIn(FAILED_RU, out + err)
        self.assertNotIn("Маршруты перешли", out + err)
        self.assertEqual(self.ids(), self.old)

    def test_the_core_command_codes(self):
        """install.sh tells by the code: 0 moved (or nothing to move), 3 the other copy may not take them, 6 failed, 2
        — a core without the command (older than 3.3.5): removed as before."""
        with mock.patch.object(ops, "hand_over", side_effect=LockedError("busy")), use_lang("ru"):
            rc, _out, err = self.run_cli("gw", "exit-handover", "2")
        self.assertEqual(rc, 6)
        self.assertIn(FAILED_RU, err)
        st = state.load()
        model.set_exit(st, self.old[0], enabled=False)
        state.save(st)
        with use_lang("ru"):
            rc, out, err = self.run_cli("gw", "exit-handover", "2", "--json")
        self.assertEqual(rc, 3)
        self.assertIn("Маршруты не перешли: «Аня IP» (тот же ключ) выключен для шлюза", out + err)
        self.assertEqual(self.vias(), ["exit:" + self.old[1]] * 3)

    def test_moved_is_what_install_sh_says(self):
        """An install.sh that moved nothing (no mark egress_handover) — no «перешли», whatever the routes were; the
        mark — its number."""
        with mock.patch.object(type(self.w.script), "handover", lambda s, n, h: ""), use_lang("ru"):
            res = ops.exit_del(self.w.runner, self.old[1])
        self.assertEqual((res["moved"], res.get("text")), (0, None))
        write_pool([self.ip, self.dom])
        with mock.patch.object(type(self.w.script), "handover", lambda s, n, h: "@K3:egress_handover:2\n"), \
                use_lang("en"):
            res = ops.exit_del(self.w.runner, self.old[1])
        self.assertEqual((res["moved"], res["text"]), (2, "The routes moved to “Аня IP” — the same key."))


class UnfitTwinTest(RoutedCase):
    """№8: the routes never go to a copy off for the gateway or broken in the pool — the removal is refused."""

    def test_off_for_the_gateway(self):
        st = state.load()
        model.set_exit(st, self.old[0], enabled=False)
        state.save(st)
        with use_lang("ru"):
            dry = ops.exit_handover(2, dry=True)
            with self.assertRaises(ConflictError) as cm:
                ops.exit_del(self.w.runner, self.old[1])
        self.assertTrue(dry["unfit"])
        self.assertIn("маршруты на него не перейдут, удаление будет отменено", dry["text"])
        self.assertEqual(str(cm.exception), "Маршруты не перешли: «Аня IP» (тот же ключ) выключен для шлюза или не "
                                            "может работать — удаление отменено. Переведите маршруты на другой выход "
                                            "и удалите снова.")
        self.assertEqual(self.ids(), self.old)
        self.assertEqual(self.routes()[0]["via"], "exit:" + self.old[1])

    def test_a_problem_in_the_pool(self):
        st = state.load()
        rows = [dict(e) for e in exits.pool(None)]
        self.assertIsNone(ops.twin_unfit(st, self.old[0], rows))
        rows[0].update(kind="bad", problem="too far")
        self.assertEqual(ops.twin_unfit(st, self.old[0], rows), "broken")
        self.assertEqual(ops.twin_unfit(st, "e-0000000000", rows), "broken")
        model.set_exit(st, self.old[0], enabled=False)
        self.assertEqual(ops.twin_unfit(st, self.old[0], [dict(e) for e in exits.pool(None)]), "off")
        with mock.patch.object(exits, "pool", lambda *a, **k: rows):
            self.assertEqual(ops.hand_over({self.old[1]: self.old[0]}), 0)
        self.assertEqual(self.routes()[0]["via"], "exit:" + self.old[1])

    def test_no_routes_nothing_said(self):
        st = state.load()
        g = model.peek(st)
        g["routes"]["default"] = {"via": "pool", "fail": "block"}
        g["users"][0]["route"] = {"via": "pool", "fail": "direct"}
        g["devices"][0]["route"] = {"via": "pool", "fail": "block"}
        state.save(st)
        with use_lang("ru"):
            self.assertEqual(ops.exit_handover(2, dry=True)["text"], "")
            res = ops.exit_del(self.w.runner, self.old[1])
        self.assertEqual((res["moved"], res.get("text")), (0, None))


class SubscriptionAfterTheReplaceTest(RoutedCase):
    """№2: a subscription that drops one copy moves its routes only once the new pool is in place."""

    def drop_the_domain_copy(self):
        self.feed.bodies[GA_URL] = vpn_of(conf_of("2.0", 3, endpoint=BY_IP), "Аня IP")

    def test_the_replace_fails_the_routes_stay(self):
        self.drop_the_domain_copy()
        self.w.script.replace_rc = 1
        try:
            self.refresh()
        except Exception:                               # noqa: BLE001 — the refresh fails in its own way
            pass
        self.assertEqual(self.ids(), self.old)
        self.assertEqual(set(r["via"] for r in self.routes()[:3]), {"exit:" + self.old[1]})

    def test_the_replace_done_the_routes_move_after_it(self):
        self.drop_the_domain_copy()
        seen = []
        real = ops.hand_over

        def spy(moves, runner=None, unfit=None):
            seen.append((list(exits.line_ids()), runner is not None))
            return real(moves, runner=runner, unfit=unfit)

        with mock.patch.object(ops, "hand_over", spy):
            r = self.refresh()["results"][0]
        self.assertEqual(r["removed"], 1, r["text"])
        self.assertEqual(seen, [([(self.old[0], 1)], True)])               # the new pool already, applied at once
        self.assertEqual(set(x["via"] for x in self.routes()), {"exit:" + self.old[0]})

    def test_an_unfit_copy_gets_nothing_and_it_is_said(self):
        st = state.load()
        model.set_exit(st, self.old[0], enabled=False)
        state.save(st)
        self.drop_the_domain_copy()
        with use_lang("ru"):
            r = self.refresh()["results"][0]
        self.assertEqual(r["removed"], 1, r["text"])
        self.assertEqual(set(x["via"] for x in self.routes()[:3]), {"exit:" + self.old[1]})
        self.assertTrue(any("не перешли на «Аня IP»" in w for w in r["warnings"]), r["warnings"])


class VlessTwoLinesTest(SameKeyCase):
    """№3: two lines of one VLESS account both work — not «работать будет только один»."""

    def test_the_words(self):
        b = VLESS_A.split("#")[0] + "#Other%20name"
        st, _v, _a = scenario_state()
        write_pool([VLESS_A, b])
        with use_lang("ru"):
            ru = exits.same_peer_problems(st, exits.pool(None))
        with use_lang("en"):
            en = exits.same_peer_problems(st, exits.pool(None))
        self.assertEqual(len(ru), 1)
        self.assertIn("Это одна учётка VLESS — работают обе строки, но вторая лишняя", ru[0])
        self.assertNotIn("только один", ru[0])
        self.assertIn("It is one VLESS account — both lines work", en[0])
        self.assertNotIn("Only one", en[0])
        write_pool([VLESS_A, b, VLESS_A.split("#")[0] + "#Third"])
        with use_lang("ru"):
            self.assertIn("работают все строки", exits.same_peer_problems(st, exits.pool(None))[0])
        write_pool([self.line(conf_of("2.0", 3, endpoint=BY_IP), "Аня"),
                    self.line(conf_of("2.0", 3, endpoint="vpn.example.com:51820"), "Аня домен")])
        with use_lang("ru"):
            self.assertIn("Работать будет только один", exits.same_peer_problems(st, exits.pool(None))[0])

    def test_the_import_of_install_sh(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "install.sh"), encoding="utf-8") as f:
            sh = f.read()
        start = sh.index("\negress_import_now() {")
        body = sh[start:sh.index("\n}\n", start)]
        self.assertIn('vless://*) same_v=$((same_v+1)) ;;', body)          # stage 1 (REV-s1-t11 П3): by the scheme
        self.assertIn('awg://*) same=$((same+1)) ;;', body)
        self.assertIn('tf EGRESS_IMPORT_SAME_VLESS "$same_v"', body)
        for lang in ("ru", "en"):
            with open(os.path.join(root, "lib/kaskad/i18n/sh/egress.%s.sh" % lang), encoding="utf-8") as f:
                words = f.read()
            line = [x for x in words.splitlines() if x.startswith("M_%s_EGRESS_IMPORT_SAME_VLESS=" % lang)][0]
            self.assertNotIn("one connection" if lang == "en" else "одно подключение", line)


class RecheckSymmetryTest(unittest.TestCase):
    """№4: the model of the reviewer (scratchpad rev3/sim_loss.py, the real exits.merge; the timing of collect.loop):
    a working AmneziaWG exit, a share of lost probes; a slow node (3.5 s of a round) — no more false «упал» than before
    P − 2 s; a whole failure — told within about a minute."""

    OK = {"alive": True, "detail": "x"}
    BAD = {"alive": False, "detail": "x"}

    def run_day(self, loss, overhead, seed, hours):
        rnd = random.Random(seed)
        snap, _, _ = exits.merge({"exits": {}}, {"e": self.OK}, ["e"], now=1000.0)
        t, drops, end = 1000.0, 0, 1000.0 + hours * 3600
        while t < end:
            ok = rnd.random() >= loss
            t_merge = t + overhead / 2 + (0.1 if ok else 4.0)
            snap, ch, _ = exits.merge(snap, {"e": self.OK if ok else self.BAD}, ["e"], now=t_merge)
            drops += sum(1 for c in ch if c[2] is False)
            rec = snap["exits"]["e"]
            t = t_merge + overhead / 2 + (5 if rec.get("alive") and int(rec.get("fails") or 0) > 0 else 10)
        return drops

    def drops(self, slack, loss, overhead):
        with mock.patch.object(exits, "CHECK_SLACK", slack):
            return sum(self.run_day(loss, overhead, seed, 6) for seed in range(3))

    def test_a_slow_node_no_more_false_drops_than_before(self):
        for loss in (0.15, 0.2):
            now, before = self.drops(2, loss, 3.5), self.drops(0, loss, 3.5)    # CHECK_SLACK 0 — the rule before
            self.assertLessEqual(now, before, (loss, now, before))
        self.whole_failure_in_about_a_minute()

    def whole_failure_in_about_a_minute(self):
        for overhead in (0.5, 2.0, 3.5):
            snap = {"exits": {}}
            t = 1000.0
            while t < 1300:
                snap, _, _ = exits.merge(snap, {"e": self.OK}, ["e"], now=t)
                t += 10 + overhead
            start = t
            while True:
                t_merge = t + overhead / 2 + 4
                snap, ch, _ = exits.merge(snap, {"e": self.BAD}, ["e"], now=t_merge)
                if any(c[2] is False for c in ch):
                    break
                self.assertLess(t_merge - start, 120)
                t = t_merge + overhead / 2 + 5
            self.assertLessEqual(t_merge - start, 60, overhead)

    def test_a_good_recheck_9_s_later_replaces_the_failed_check(self):
        snap = {"exits": {}}
        for i in range(12):
            snap, _, _ = exits.merge(snap, {"e": self.OK}, ["e"], now=1000 + 10 * i)
        snap, _, _ = exits.merge(snap, {"e": self.BAD}, ["e"], now=1120)
        snap, _, _ = exits.merge(snap, {"e": self.OK}, ["e"], now=1129)          # ≥ P − 2, < P: replaces
        self.assertEqual(snap["exits"]["e"]["tries"][-1], [1120, 1])
        self.assertEqual(sum(1 for x in snap["exits"]["e"]["tries"] if not x[1]), 0)
        snap, _, _ = exits.merge(snap, {"e": self.BAD}, ["e"], now=1138)         # a failed one 9 s later: its own
        self.assertEqual(snap["exits"]["e"]["tries"][-2:], [[1120, 1], [1138, 0]])
        snap, _, _ = exits.merge(snap, {"e": self.BAD}, ["e"], now=1145)         # 7 s: within P − 2 — replaces
        self.assertEqual(snap["exits"]["e"]["tries"][-2:], [[1120, 1], [1138, 0]])


class MonotonicClockTest(unittest.TestCase):
    """№5: the wall clock jumps forward (a paused VM, NTP) — the same probe of xray is not «old» at once."""

    def test_a_step_of_the_wall_clock(self):
        p = {"alive": True, "detail": "x", "try_at": 500}
        snap, _, _ = exits.merge({"exits": {}}, {"e": p}, ["e"], now=1000.0, mono=100.0)
        snap, ch, _ = exits.merge(snap, {"e": p}, ["e"], now=1000.0 + 300, mono=105.0)     # +300 s wall, +5 s real
        self.assertEqual(ch, [])
        self.assertTrue(snap["exits"]["e"]["alive"])
        snap, ch, _ = exits.merge(snap, {"e": p}, ["e"], now=1000.0 + 360, mono=161.0)     # a real minute: silence
        self.assertEqual(ch, [("e", True, False)])
        self.assertEqual(snap["exits"]["e"]["detail_code"], "detail.xray_stale")

    def test_the_window_keeps_it(self):
        self.assertIn("try_mono", exits.WINDOW_KEYS)                      # in /run with the window, not on the disk


class FeedFileTest(FlapCase):
    """№6, №7: the feed of the exits across a restart and an update."""

    def setUp(self):
        super().setUp()
        for _ in range(12):
            self.round()

    def down(self):
        self.w.leg(2, probe=False)
        for _ in range(40):
            if self.mine(self.round(5), self.aid):
                return
        self.fail("never down")

    def up(self):
        self.w.leg(2)
        for _ in range(2):
            self.round()

    def kinds(self):
        return [e["kind"] for e in self.events()]

    def held_dead(self):
        self.down()
        self.up()
        self.down()
        self.assertEqual(self.kinds(), ["exit_dead", "exit_alive"])           # «упал» held back

    def test_a_broken_feed_goes_aside_the_history_stays(self):
        self.held_dead()
        feed = paths.P("/run/kaskad/live-feed.json")
        with open(feed, "w", encoding="utf-8") as f:
            json.dump({"v": 1, "exits": [{"id": self.aid}]}, f)
        with mock.patch.object(collect, "LIVE", live.Live()):
            self.round()
            self.assertEqual(self.kinds(), ["exit_dead", "exit_alive", "exit_dead"])     # «said» from the disk
        self.assertTrue(os.path.exists(feed + ".bad"))
        self.assertIn("live-feed.json испорчен", self.log())
        with open(feed, encoding="utf-8") as f:
            self.assertIsInstance(json.load(f)["exits"], dict)                         # written anew

    def test_an_update_from_334_takes_said_from_the_events(self):
        """3.3.4 had neither the feed file nor exits-told.json: what was told last comes from the events of live.json."""
        self.held_dead()
        for f in (paths.P("/run/kaskad/live-feed.json"), paths.P("/etc/kaskad/gw/exits-told.json")):
            os.remove(f)
        with mock.patch.object(collect, "LIVE", live.Live()):
            self.round()
            self.assertEqual(self.kinds(), ["exit_dead", "exit_alive", "exit_dead"])

    def test_forget_drops_said(self):
        L = live.Live()
        L.said = {"e-1111111111": (100, "exit_dead"), "e-2222222222": (100, "exit_alive")}
        L._forget(["e-2222222222"], 200)
        self.assertEqual(list(L.said), ["e-2222222222"])
        self.assertTrue(L._told_dirty)
        L._save_feed()
        with open(paths.P("/etc/kaskad/gw/exits-told.json"), encoding="utf-8") as f:
            self.assertEqual(list(json.load(f)), ["e-2222222222"])


@unittest.skipUnless(shutil.which("node"), "нет node: функции app.js не запустить")
class PanelTest(RoutedCase):
    """№3, №8 in the panel: the words of VLESS; «маршруты перейдут на него» only with routes and a fit copy."""

    def node(self, script):
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
            f.write(script)
            path = f.name
        try:
            res = subprocess.run(["node", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        finally:
            os.unlink(path)
        self.assertEqual(res.returncode, 0, res.stderr.decode("utf-8", "replace")[-2000:])
        return json.loads(res.stdout.decode("utf-8").strip().splitlines()[-1])

    def warn(self, people, enabled=True):
        from tests.test_panel_js_contract import app_scope
        rows = json.loads(json.dumps(ops.exits_view(self.w.runner)))
        rows[0]["enabled"] = enabled
        js = read("app.js")
        scope = "\n".join([jsparts.const(js, "exitNameOf"), jsparts.const(js, "exitTwinFit"),
                           jsparts.function(js, "exitPinned"), jsparts.const(js, "exAbRows"), jsparts.function(js, "exitDelWarn")])
        return self.node(app_scope() + """
const rows = app.normExits({ exits: %s }).rows;
const t = (key, vars) => key + (vars ? " " + JSON.stringify(vars) : "");
const tn = (key, n, vars) => key + " " + n + (vars ? " " + JSON.stringify(vars) : "");
const R = { exits: { loaded: true, data: { rows: rows } }, people: { loaded: true, data: %s }, gw: { data: null } };
const exitOwn = (id) => /^e-[0-9a-f]{10}$/.test(String(id));
%s
console.log(JSON.stringify(exitDelWarn(rows[1])));
""" % (json.dumps(rows, ensure_ascii=False), json.dumps(people, ensure_ascii=False), scope))

    def test_the_question_of_removal(self):
        via = {"via": "exit:" + self.old[1], "fail": "block"}
        routed = {"users": [], "devices": [{"id": "d1", "name": "Телефон", "route": via}]}
        self.assertIn('ex.delTwin {"name":"Аня IP"}', self.warn(routed))
        self.assertNotIn("ex.delTwin", self.warn({"users": [], "devices": []}))          # no routes: nothing to move
        w = self.warn(routed, enabled=False)
        self.assertIn('ex.delTwinUnfit {"name":"Аня IP"}', w)
        self.assertIn("ex.delPinned", w)

    def test_the_words(self):
        js = read("app.js")
        self.assertIn('t(isXrayExit(r) && (r.proto || "vless") === "vless" ? "ex.samePeerVless" : "ex.samePeer", { name: exitNameOf(r.samePeer)', js)
        for name in ("i18n-ru.js", "i18n-en.js"):
            words = read(name)
            for key in ("ex.samePeerVless", "ex.delTwinUnfit"):
                self.assertIn('"%s":' % key, words, (name, key))
        self.assertIn("Это одна учётка VLESS — работают обе строки", read("i18n-ru.js"))
        self.assertIn("It is one VLESS account — both lines work", read("i18n-en.js"))


if __name__ == "__main__":
    unittest.main()
