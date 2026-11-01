"""T10 — ``kaskadctl`` of the groups and the exit policy (SPEC-stage1-exits §7.7, D-050, D-078) on the LIVE functions of
T7 (``gw.policy_ops``): ``gw groups``, ``gw policy``, ``gw policy-reset``, the route ``group:<gid>`` (``--via`` too),
``gw exit-add --group``, the buttons over one exit (``--standby``/``--restore``/``--maintenance``/``--accept-country``,
``--enable``/``--disable`` with ``--dry``/``--rev``, the quota), ``gw exit-del --dry``, ``gw exit-move``,
``gw group-del``, ``gw group-flap-standby``, ``gw exit-history``, ``gw exit-speedtest``, ``kaskadctl tz``. A destructive
command refuses without ``--yes`` off a terminal and asks with numbers on one; nothing is written by ``--dry``."""

import io
import json
import sys
import time
from unittest import mock

from kaskad import errors, paths, state, util
from kaskad.gw import cli_policy, collect, exits, model, ops, policy as P, policy_ops as pops, rev
from tests.fakes_gw import VLESS_B
from tests.test_gw_ops import CliGwCase


class T10Case(CliGwCase):
    def setUp(self):
        super().setUp()
        self.w.runner.on(["systemd-run"], lambda argv, inp: (0, "", ""))     # the rebuild of the bot's proxy (§2.9)
        self.enabled_world()
        self.uid = ops.user_add(self.w.runner, "Мама")["id"]
        self.did = ops.device_add(self.w.runner, self.uid, "Телефон", kind="phone", platform="android",
                                  access=["wg"])["device"]["id"]
        collect.tick(self.w.runner)

    def state_bytes(self):
        return self.read("/etc/kaskad/state.json")

    def group(self, name="Стриминг", role="abroad"):
        return pops.group_add(self.w.runner, name, role=role)["id"]

    def pol(self):
        return pops._base(state.load())

    def stdin(self, text):
        return mock.patch.object(sys, "stdin", io.StringIO(text))


class GroupsTest(T10Case):
    def test_groups_json_names_labels_and_the_table(self):
        gid = self.group()
        rc, doc, err = self.run_json("gw", "groups")
        self.assertEqual(rc, 0, err)
        main, other = doc["groups"]
        self.assertEqual((main["id"], main["name"], main["label"], main["role"]), ("main", "", "Основная", "abroad"))
        self.assertEqual(sorted(main["exits"]), sorted([self.vid, self.aid]))
        self.assertEqual(main["tiers"][0]["label"], "Уровень 1")
        # PLAN-order Ш3 (MCP exit_groups_list): how each tier chooses
        self.assertEqual((main["tiers"][0]["method"], main["tiers"][0]["order"], main["per_connection"]),
                         ("even", "auto", False))
        self.assertTrue(main["tg_proxy"] and main["new_exits"])
        self.assertEqual((other["id"], other["label"], other["exits"]), (gid, "Стриминг", []))
        self.assertIsInstance(doc["rev"], int)
        lines = doc["lines"]
        self.assertTrue(lines[0].startswith("Группа") and "Последний шаг" in lines[0], lines)
        row = [x for x in lines if x.startswith("Основная")][0]
        self.assertIn("зарубежная", row)
        self.assertIn("блок", row)
        self.assertIn("нет выходов", [x for x in lines if x.startswith("Стриминг")][0])
        self.assertTrue(any("Основная · Уровень 1" in x for x in lines), lines)          # each exit under the table
        self.assertEqual(lines[-1], "Управление выходами и группами — в панели или kaskadctl gw …")
        rc, out, err = self.run_cli("gw", "groups")
        self.assertEqual((rc, out.rstrip("\n").splitlines()), (0, lines))

    def test_the_tier_now_the_last_step_and_devices_without_an_exit(self):
        from kaskad.gw import live
        now = time.time()
        util.atomic_write(paths.pins_json(), json.dumps({"version": 1, "saved": int(now), "gw_all": None, "groups": {
            "main": {"pins": {}, "tier_good_since": {}, "active_tier": None, "last_step": "block"}}}), 0o600)
        doc, _age = live.read()
        doc = dict(doc or {}, ts=now, no_exit={self.did: {"step": "last_block", "group": "main", "since": int(now) - 60}})
        util.atomic_write(paths.live_json(), json.dumps(doc), 0o644)
        from kaskad import snapshot
        snapshot.forget()
        rc, doc, err = self.run_json("gw", "groups")
        self.assertEqual(rc, 0, err)
        g = doc["groups"][0]
        self.assertEqual((g["last_step"], g["no_exit"], doc["no_exit"]), ("block", 1, 1))
        self.assertIn("последний шаг с %s" % live._clock(int(now) - 60), doc["lines"][1])
        self.assertIn("Нет выхода в интернет: 1 устройство.", doc["lines"])

    def test_policy_view_one_group_and_no_raw_json(self):
        gid = self.group()
        rc, doc, err = self.run_json("gw", "policy", "--group", gid)
        self.assertEqual(rc, 0, err)
        self.assertEqual([g["id"] for g in doc["policy"]["groups"]], [gid])
        rc, out, _ = self.run_cli("gw", "policy")
        self.assertIn("Группа «Основная» — зарубежная, набор «Стандарт»", out)
        self.assertIn("Если все выходы недоступны: блок", out)
        self.assertIn("настраиваются в панели", out)
        rc, doc, _ = self.run_json("gw", "policy", "--group", "g-000000")
        self.assertEqual((rc, doc["data"].get("gone")), (errors.CONFLICT, True))
        names = [a.dest for a in self._parser("policy")._actions]
        self.assertNotIn("file", names)                                    # §7.7: no policy-set, no raw JSON

    def test_policy_view_shows_the_new_exits_tier_and_the_night_rebalance(self):
        # Т-3312 стенд У §2.4: «Новые и перенесённые ключи» и «Ночная перебалансировка» — как в панели, RU и EN
        from kaskad.i18n import use_lang
        rc, out, _ = self.run_cli("gw", "policy")
        self.assertIn("  Новые и перенесённые выходы: на последний уровень", out)
        self.assertIn("  Ночная перебалансировка: нет", out)
        rc, doc, _ = self.run_json("gw", "policy")          # MCP exit_policy_get = this JSON: both fields are there
        g0 = doc["policy"]["groups"][0]
        self.assertEqual((g0["new_exits_tier"], g0["night_rebalance"]["enabled"]), ("last", False))
        g = {"id": "g-1a2b3c", "name": "Дом", "role": "ru", "preset": "ru_home", "fail": "direct",
             "tiers": [{"id": "t1", "exits": []}, {"id": "t2", "name": "Дача", "exits": []}],
             "new_exits_tier": "last", "night_rebalance": {"enabled": True, "at": "03:30"}}
        lines = cli_policy.policy_lines({"groups": [g]})
        self.assertIn("  Новые и перенесённые ключи: каждый — своим уровнем в конце", lines)
        self.assertIn("  Ночная перебалансировка: да, в 03:30", lines)
        cases = (("first", "на первый уровень", "to the first tier"), ("t1", "в уровень «Уровень 1»", "to the tier “Level 1”"),
                 ("t2", "в уровень «Дача»", "to the tier “Дача”"),
                 ("t9", "каждый — своим уровнем в конце", "each as its own tier at the end"))    # unknown id = «last»
        for where, ru, en in cases:
            g["new_exits_tier"] = where
            self.assertIn("  Новые и перенесённые ключи: " + ru, cli_policy.policy_lines({"groups": [g]}), where)
            with use_lang("en"):
                self.assertIn("  New and moved keys: " + en, cli_policy.policy_lines({"groups": [g]}), where)
        with use_lang("en"):
            g["role"], g["new_exits_tier"], g["night_rebalance"] = "abroad", "last", {"enabled": False}
            lines = cli_policy.policy_lines({"groups": [g]})
        self.assertIn("  New and moved exits: to the last tier", lines)
        self.assertIn("  Night rebalancing: no", lines)

    def _parser(self, name):
        from kaskad import cli
        p = cli.build_parser()
        gw = p._subparsers._group_actions[0].choices["gw"]
        return gw._subparsers._group_actions[0].choices[name]


class ResetTest(T10Case):
    def test_reset_asks_dry_runs_and_writes(self):
        before = self.state_bytes()
        rc, doc, _ = self.run_json("gw", "policy-reset", "--preset", "compat")      # off a terminal, no --yes
        self.assertEqual(rc, errors.USAGE)
        self.assertIn("--yes", doc["error"])
        self.assertEqual(self.state_bytes(), before)
        rc, doc, err = self.run_json("gw", "policy-reset", "--preset", "compat", "--dry")
        self.assertEqual(rc, 0, err)
        self.assertTrue(doc["dry"])
        self.assertIn("moves", doc)
        self.assertEqual(self.state_bytes(), before)
        rev = doc["rev"]
        rc, doc, _ = self.run_json("gw", "policy-reset", "--preset", "compat", "--yes", "--rev", str(rev + 7))
        self.assertEqual(rc, errors.CONFLICT)
        self.assertEqual(self.state_bytes(), before)
        rc, doc, err = self.run_json("gw", "policy-reset", "--preset", "compat", "--yes", "--rev", str(rev))
        self.assertEqual(rc, 0, err)
        self.assertEqual(P.group(self.pol(), "main")["preset"], "compat")

    def test_on_a_terminal_numbers_and_enter_cancels(self):
        before = self.state_bytes()
        with mock.patch.object(cli_policy, "_tty", lambda: True), self.stdin("\n"):
            rc, out, err = self.run_cli("gw", "policy-reset", "--preset", "compat")
        self.assertEqual(rc, 0)
        self.assertIn("1) Включить «Как раньше (без условий)»", err)
        self.assertIn("2) Отмена", err)
        self.assertIn("Выбор [2]:", err)
        self.assertIn("Отменено", out)
        self.assertEqual(self.state_bytes(), before)
        with mock.patch.object(cli_policy, "_tty", lambda: True), self.stdin("y\n1\n"):
            rc, out, err = self.run_cli("gw", "policy-reset", "--preset", "compat")
        self.assertEqual(rc, 0, err)
        self.assertIn("Нет такого пункта: введите 1 или 2.", err)                   # no y/n: numbers only
        self.assertEqual(P.group(self.pol(), "main")["preset"], "compat")

    def test_a_preset_of_another_role_is_refused(self):
        rc, doc, _ = self.run_json("gw", "policy-reset", "--preset", "ru_home", "--yes")
        self.assertEqual(rc, errors.USAGE)


class RouteTest(T10Case):
    def test_route_through_a_group_by_its_name_and_both_forms(self):
        gid = self.group()
        rc, doc, err = self.run_json("gw", "route", "--via", "group:%s" % gid)
        self.assertEqual(rc, 0, err)
        self.assertEqual(state.load()["gateway"]["routes"]["default"]["via"], "group:%s" % gid)
        rc, out, err = self.run_cli("gw", "route", "group:%s" % gid, "--fail", "direct")
        self.assertEqual(rc, 0, err)
        self.assertIn("Стриминг", out)
        self.assertNotIn(gid, out)                                           # the label, not the id (§3.1)
        rc, doc, _ = self.run_json("gw", "route", "pool", "--via", "direct")
        self.assertEqual(rc, errors.USAGE)
        rc, out, err = self.run_cli("gw", "user", "route", self.uid, "--via", "group:%s" % gid)
        self.assertEqual(rc, 0, err)
        rc, out, err = self.run_cli("gw", "device", "route", self.did, "--via", "group:main")
        self.assertEqual(rc, 0, err)
        self.assertEqual(model.find_device(state.load(), self.did)["route"]["via"], "pool")

    def test_a_russian_or_missing_group_is_refused(self):
        ru = self.group("Дом", role="ru")
        before = self.state_bytes()
        for via in ("group:%s" % ru, "group:g-000000"):
            rc, doc, _ = self.run_json("gw", "route", "--via", via)
            self.assertEqual(rc, errors.USAGE, via)
            rc, doc, _ = self.run_json("gw", "device", "route", self.did, via)
            self.assertEqual(rc, errors.USAGE, via)
        self.assertEqual(self.state_bytes(), before)

    def test_exits_json_tells_the_group_of_the_default_route(self):
        rc, doc, err = self.run_json("gw", "exits")
        self.assertEqual(rc, 0, err)
        dr = doc["default_route"]
        self.assertEqual((dr["via"], dr["group"], dr["group_name"], dr["group_fail"], dr["fail_eff"]),
                         ("pool", "main", "Основная", "block", "block"))
        row = [r for r in doc["exits"] if r["id"] == self.aid][0]
        for k in ("health", "reasons", "group", "tier", "devices", "rtt_ms", "recovering", "unstable", "manual_why"):
            self.assertIn(k, row)
        self.assertEqual((row["group"], row["tier"]), ("main", "t1"))
        self.assertEqual(doc["groups"], [{"id": "main", "label": "Основная", "role": "abroad", "fail": "block"}])

    def _m1(self, eid, rows):
        """Rows [(ago, rtt_med, loss[, ok])] of the minute table of metrics.db for ``eid`` (ok — 6 of 6 by default)."""
        import sqlite3
        from kaskad.gw import metrics
        if not metrics.available():
            self.skipTest("no sqlite3")
        w = metrics.open_writer()                           # the base with its tables (or the collector's)
        if w is not None:
            w.close()
        now = int(time.time())
        c = sqlite3.connect(paths.metrics_db())
        for row in rows:
            ago, rtt, loss, ok = (tuple(row) + (6,))[:4]
            c.execute("INSERT OR REPLACE INTO m1 (exit, ts, rtt_med, loss, ok, n) VALUES (?,?,?,?,?,6)",
                      (eid, (now - ago) // 60 * 60, rtt, loss, ok))
        c.commit()
        c.close()

    def _exit_rows(self):
        rc, doc, err = self.run_json("gw", "exits")
        self.assertEqual(rc, 0, err)
        return dict((r["id"], r) for r in doc["exits"])

    def test_the_response_is_the_last_one_measured(self):
        """Л3 («Все выходы › Отклик» — «—» у работающего выхода, в «Выходы · час» 108 мс): HTTP-проба простаивающего
        AmneziaWG — раз в 2 мин, у последней минуты rtt_med нет. Отклик — последний измеренный за короткое окно
        (rtt_keep_s, при частоте «часто» — 10 мин); потери и дрожание — по-прежнему последней минуты; старше окна —
        нет числа, как раньше; последняя минута без ответов (выход не работает) — тоже без числа."""
        self._m1(self.aid, [(200, 104.0, 0.0), (130, 108.0, 0.0), (70, None, 0.0)])
        rows = self._exit_rows()
        self.assertEqual((rows[self.aid]["rtt_ms"], rows[self.aid]["loss_pct"]), (108.0, 0.0))   # без правки: None
        self._m1(self.vid, [(900, 50.0, 0.0), (70, None, 1.0)])                                  # 15 мин назад — старо
        rows = self._exit_rows()
        self.assertEqual((rows[self.vid]["rtt_ms"], rows[self.vid]["loss_pct"]), (None, 1.0))
        self._m1(self.aid, [(130, 108.0, 0.0), (70, None, 100.0, 0)])                       # не отвечает — «—»
        self.assertEqual(self._exit_rows()[self.aid]["rtt_ms"], None)
        self.assertEqual(pops.rtt_keep_s(None), 600)
        self.assertEqual(pops.rtt_keep_s({"checks": {"freq": {"mode": "rare"}}}), 1500)       # 12 × 120 + 60

    def test_status_json_tells_the_default_route_and_the_groups(self):
        """REV-s1-t10-2 п. 2: «Состояние» (17 → 5) reads ``default_route`` and ``groups`` of ``gw status --json`` — the
        contract of the core with the menu (the bash test feeds a fake answer)."""
        g = self.group()
        rc, doc, err = self.run_json("gw", "status")
        self.assertEqual(rc, 0, err)
        dr = doc["default_route"]
        self.assertEqual((dr["via"], dr["group"], dr["group_name"], dr["group_fail"]), ("pool", "main", "Основная", "block"))
        self.assertEqual([(x["id"], x["label"]) for x in doc["groups"]], [("main", "Основная"), (g, "Стриминг")])
        for x in doc["groups"]:
            self.assertEqual(set(x), {"id", "label", "role", "fail"})


class ExitButtonsTest(T10Case):
    def test_standby_restore_maintenance_with_dry_and_rev(self):
        before = self.state_bytes()
        rc, doc, err = self.run_json("gw", "exit", self.vid, "--standby", "--dry")
        self.assertEqual(rc, 0, err)
        self.assertTrue(doc["dry"])
        self.assertEqual(self.state_bytes(), before)
        rc, doc, _ = self.run_json("gw", "exit", self.vid, "--standby", "--rev", str(doc["rev"] + 5))
        self.assertEqual(rc, errors.CONFLICT)
        rc, out, err = self.run_cli("gw", "exit", self.vid, "--standby")
        self.assertEqual(rc, 0, err)
        self.assertIn("в запасе", out)
        self.assertEqual(model.exit_pref(state.load(), self.vid)["manual"], "standby")
        rc, out, err = self.run_cli("gw", "exits")
        self.assertIn("вручную: в запасе", out)
        self.ok("gw", "exit", self.vid, "--maintenance")
        self.assertEqual(model.exit_pref(state.load(), self.vid)["manual"], "maintenance")
        self.ok("gw", "exit", self.vid, "--restore")
        self.assertIsNone(model.exit_pref(state.load(), self.vid).get("manual"))

    def test_one_action_at_a_time_and_gone(self):
        for argv in (("--standby", "--restore"), ("--standby", "--name", "X"), ("--enable", "--quota-gb", "5"),
                     ("--enable", "--name", "X", "--dry")):
            rc, _out, _err = self.run_cli("gw", "exit", self.vid, *argv)
            self.assertEqual(rc, errors.USAGE, argv)
        rc, doc, _ = self.run_json("gw", "exit", "e-0000000000", "--standby")
        self.assertEqual((rc, doc["data"].get("gone")), (errors.CONFLICT, True))

    def test_enable_disable_dry_through_the_buttons_of_t7(self):
        before = self.state_bytes()
        rc, doc, err = self.run_json("gw", "exit", self.vid, "--disable", "--dry")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.state_bytes(), before)
        self.ok("gw", "exit", self.vid, "--disable", "--rev", str(doc["rev"]))
        self.assertFalse(model.exit_pref(state.load(), self.vid)["enabled"])
        self.ok("gw", "exit", self.vid, "--enable", "--name", "Вена")                 # the path of 3.3.5 stays
        p = model.exit_pref(state.load(), self.vid)
        self.assertEqual((p["enabled"], p["name"]), (True, "Вена"))

    def test_accept_country_without_a_new_one_is_refused(self):
        rc, doc, _ = self.run_json("gw", "exit", self.vid, "--accept-country")
        self.assertEqual(rc, errors.CONFLICT)

    def test_quota_fields_merge_and_off(self):
        self.ok("gw", "exit", self.aid, "--quota-gb", "50")
        q = model.exit_pref(state.load(), self.aid)["quota"]
        self.assertEqual((q["limit_gb"], q["period"], q["action"]), (50.0, "month", "remove"))
        self.ok("gw", "exit", self.aid, "--quota-action", "standby", "--quota-reset-day", "5")
        q = model.exit_pref(state.load(), self.aid)["quota"]
        self.assertEqual((q["limit_gb"], q["action"], q["reset_day"]), (50.0, "standby", 5))
        rc, doc, _ = self.run_json("gw", "exit", self.aid, "--quota-reset-day", "40")
        self.assertEqual((rc, doc["data"]["field"]), (errors.USAGE, "quota.reset_day"))
        self.ok("gw", "exit", self.aid, "--no-quota")
        self.assertIsNone(model.exit_pref(state.load(), self.aid).get("quota"))
        rc, doc, _ = self.run_json("gw", "exit", self.vid, "--quota-action", "push")       # no limit: the core says
        self.assertEqual(rc, errors.USAGE)

    def test_exit_del_dry_removes_nothing(self):
        before = exits.pool_lines()
        rc, doc, err = self.run_json("gw", "exit-del", self.vid, "--dry")
        self.assertEqual(rc, 0, err)
        self.assertTrue(doc["dry"])
        self.assertEqual(exits.pool_lines(), before)

    def test_exit_add_into_a_group(self):
        gid = self.group()
        with self.stdin(VLESS_B + "\n"):
            rc, doc, err = self.run_json("gw", "exit-add", "--group", gid)
        self.assertEqual(rc, 0, err)
        eid = exits.exit_id(VLESS_B)
        self.assertEqual(P.group_of(self.pol(), eid)["id"], gid)
        ru = self.group("Дом", role="ru")
        with self.stdin(VLESS_B + "\n"):
            rc, doc, _ = self.run_json("gw", "exit-add", "--group", ru)      # T15 Q4: the key is abroad already —
        self.assertEqual(rc, errors.CONFLICT)                               # one key in one file
        self.assertEqual(doc["data"]["code"] if "data" in doc else doc.get("code"), "key_in_other_role")
        self.assertEqual(P.group_of(self.pol(), eid)["id"], gid)


class MoveDeleteTest(T10Case):
    def test_exit_move_same_role_and_dry(self):
        gid = self.group()
        before = self.state_bytes()
        rc, doc, err = self.run_json("gw", "exit-move", self.vid, "--group", gid, "--dry")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.state_bytes(), before)
        # KT3-9: a move changes the path of traffic — off a terminal only with --yes (as group-del), nothing moved
        for argv in ((), ("--json",)):
            with self.stdin(""):
                rc, out, err = self.run_cli("gw", "exit-move", self.vid, "--group", gid, *argv)
            self.assertEqual(rc, errors.USAGE, out + err)
            self.assertIn("--yes", out + err)
            self.assertEqual(self.state_bytes(), before)
        with mock.patch.object(cli_policy, "_tty", lambda: True), self.stdin("2\n"):
            rc, out, err = self.run_cli("gw", "exit-move", self.vid, "--group", gid)
        self.assertEqual(rc, 0, err)
        self.assertIn("Перенести выход", err)
        self.assertIn("1) Перенести", err)
        self.assertEqual(self.state_bytes(), before)                        # «2»: nothing moved
        with mock.patch.object(cli_policy, "_tty", lambda: True), self.stdin("1\n"):
            rc, out, err = self.run_cli("gw", "exit-move", self.vid, "--group", gid)
        self.assertEqual(rc, 0, err)
        self.assertIn("перенесён в группу «Стриминг»", out)
        self.assertEqual(P.group_of(self.pol(), self.vid)["id"], gid)
        rc, out, err = self.run_cli("gw", "exit-move", self.vid, "--group", "main", "--yes")
        self.assertEqual(rc, 0, err)
        self.assertEqual(P.group_of(self.pol(), self.vid)["id"], "main")

    def test_exit_move_to_another_role_needs_the_word_and_yes(self):
        ru = self.group("Дом", role="ru")
        before = self.state_bytes()
        rc, doc, _ = self.run_json("gw", "exit-move", self.vid, "--group", ru)
        self.assertEqual(rc, errors.CONFLICT)                               # 409 confirm_role
        rc, doc, _ = self.run_json("gw", "exit-move", self.vid, "--group", ru, "--confirm-role")
        self.assertEqual(rc, errors.USAGE)                                  # off a terminal without --yes
        self.assertEqual(self.state_bytes(), before)
        calls = []

        def move(runner, eid, group, tier=None, confirm_role=False, want_rev=None, dry=False, want_moves=None):
            calls.append((eid, group, confirm_role, dry))
            return {"ok": True, "dry": dry, "rev": 1, "moves": {"total": 0}, "warnings": []}
        with mock.patch.object(pops, "exit_move", move):
            with mock.patch.object(cli_policy, "_tty", lambda: True), self.stdin("2\n"):
                rc, out, err = self.run_cli("gw", "exit-move", self.vid, "--group", ru, "--confirm-role")
            self.assertEqual(rc, 0)
            self.assertIn("Трафик группы пойдёт через домашний IP. Перенести в группу «Дом»?", err)
            self.assertIn("1) Перенести", err)
            self.assertEqual(calls, [(self.vid, ru, True, True)])         # only the dry run: «2» — nothing moved
            with mock.patch.object(cli_policy, "_tty", lambda: True), self.stdin("1\n"):
                rc, out, err = self.run_cli("gw", "exit-move", self.vid, "--group", ru, "--confirm-role")
            self.assertEqual(rc, 0, err)
            self.assertEqual(calls[-1], (self.vid, ru, True, False))
        # T15 Q4 (SPEC-groups §2.2): with the word and ``--yes`` the key goes to the Russian file — its id stays
        rc, doc, err = self.run_json("gw", "exit-move", self.vid, "--group", ru, "--confirm-role", "--yes")
        self.assertEqual(rc, 0, err)
        self.assertEqual(P.group_of(self.pol(), self.vid)["id"], ru)
        self.assertEqual(doc["role"]["to"], "ru")

    def test_group_delete_asks_and_moves_the_exits(self):
        gid = self.group()
        pops.exit_move(self.w.runner, self.vid, gid)
        rc, doc, _ = self.run_json("gw", "group-del", gid, "--exits-to", "main")
        self.assertEqual(rc, errors.USAGE)
        self.assertIsNotNone(P.group(self.pol(), gid))
        rc, doc, err = self.run_json("gw", "group-del", gid, "--exits-to", "main", "--dry")
        self.assertEqual(rc, 0, err)
        self.assertIsNotNone(P.group(self.pol(), gid))
        rc, doc, err = self.run_json("gw", "group-del", gid, "--exits-to", "main", "--yes")
        self.assertEqual(rc, 0, err)
        self.assertIsNone(P.group(self.pol(), gid))
        self.assertEqual(P.group_of(self.pol(), self.vid)["id"], "main")
        rc, doc, _ = self.run_json("gw", "group-del", "main", "--yes")
        self.assertEqual(rc, errors.USAGE)

    def test_flap_standby_of_one_group(self):
        rc, doc, err = self.run_json("gw", "group-flap-standby", "main", "off", "--dry")
        self.assertEqual(rc, 0, err)
        self.assertTrue(P.group(self.pol(), "main")["flap_standby"])
        rc, out, err = self.run_cli("gw", "group-flap-standby", "main", "off")
        self.assertEqual(rc, 0, err)
        g = P.group(self.pol(), "main")
        self.assertEqual((g["flap_standby"], g["preset"]), (False, "custom"))   # another value — «Свой» (§1.3)
        self.assertIn("остаётся в работе", out)


class HistorySpeedTzTest(T10Case):
    def test_history_answers_without_a_base(self):
        rc, doc, err = self.run_json("gw", "exit-history", self.vid, "--days", "3", "--limit", "5")
        self.assertEqual(rc, 0, err)
        self.assertEqual(doc["id"], self.vid)
        self.assertIn("items", doc)

    def test_speedtest_waits_for_its_result_and_has_a_limit(self):
        from kaskad.gw.probe import unit
        started = []
        clock = [1000.0]
        with mock.patch.object(unit, "manual", lambda r, eid, force=False: started.append((eid, force))), \
                mock.patch.object(cli_policy, "_now", lambda: clock[0]), \
                mock.patch.object(cli_policy, "_sleep", lambda s: clock.__setitem__(0, clock[0] + s)), \
                mock.patch.object(pops, "_last_metrics",
                                  lambda ids, now: ({}, {ids[0]: {"mbps": 42.0, "ts": 1004}} if now >= 1004 else {})):
            rc, doc, err = self.run_json("gw", "exit-speedtest", self.vid, "--force")
        self.assertEqual(rc, 0, err)
        self.assertEqual((started, doc["speed"]["mbps"]), ([(self.vid, True)], 42.0))
        clock[0] = 1000.0
        with mock.patch.object(unit, "manual", lambda r, eid, force=False: None), \
                mock.patch.object(cli_policy, "_now", lambda: clock[0]), \
                mock.patch.object(cli_policy, "_sleep", lambda s: clock.__setitem__(0, clock[0] + s)), \
                mock.patch.object(pops, "_last_metrics", lambda ids, now: ({}, {})):
            rc, doc, _ = self.run_json("gw", "exit-speedtest", self.vid)
        self.assertEqual((rc, doc["data"]["running"]), (errors.ERROR, True))
        self.assertLessEqual(clock[0], 1000.0 + cli_policy.SPEED_WAIT + cli_policy.SPEED_POLL)
        rc, doc, _ = self.run_json("gw", "exit-speedtest", "e-0000000000")
        self.assertEqual(rc, errors.CONFLICT)

    def test_tz_get_and_set(self):
        rc, doc, err = self.run_json("tz", "set", "UTC")
        self.assertEqual(rc, 0, err)
        self.assertEqual(doc["tz"], "UTC")
        rc, doc, err = self.run_json("tz", "get")
        self.assertEqual((rc, doc["tz"]), (0, "UTC"))
        self.assertNotIn("zones", doc)
        rc, doc, _ = self.run_json("tz", "set", "Mars/Olympus")
        self.assertEqual(rc, errors.USAGE)


# ── round 2 (REV-s1-t10, REV-s1-t10-sec) ──────────────────────────────────────

class AskWriteTest(T10Case):
    """sec №1: «1» writes what the question showed — the ``rev`` and the moves of its dry run; changed meanwhile (the
    panel sets a person's route into the group, another write changes the policy) — counted and asked again."""

    def _second_person(self):
        u2 = ops.user_add(self.w.runner, "Папа")["id"]
        ops.device_add(self.w.runner, u2, "Ноутбук", kind="laptop", platform="linux", access=["wg"])
        return u2

    def test_group_del_asks_again_when_the_panel_adds_a_person_meanwhile(self):
        g = self.group()
        ops.user_route(self.w.runner, self.uid, "group:" + g, None)
        u2 = self._second_person()
        asked = []

        def confirm(args, question, *a, **k):
            asked.append(question)
            if len(asked) == 1:
                ops.user_route(self.w.runner, u2, "group:" + g, None)     # the panel, while the question is up
            return True
        with mock.patch.object(cli_policy, "confirm", confirm), mock.patch.object(cli_policy, "_tty", lambda: True):
            rc, out, err = self.run_cli("gw", "group-del", g, "--reassign", "direct")
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(len(asked), 2)                         # the first «1» wrote nothing: the moves were others
        first, again = err.split("Пока шёл вопрос", 1)
        self.assertIn("1 устройство", first)
        self.assertIn("2 устройства", again)                     # what is written now was shown before the second «1»
        self.assertIn("маршруты или состояние выходов", again)   # REV-s1-t10-2 п. 1: the exits move the moves too
        routes = [(u.get("route") or {}).get("via") for u in model.gw(state.load())["users"]]
        self.assertEqual(routes, ["direct", "direct"])

    def test_group_del_asks_again_when_a_device_is_swapped_at_the_same_count(self):
        """REV-s1-t10-2 З-2: while the question is up one person leaves the group and another comes — the count is the
        same (1), the devices are not: the signature of ``moves`` (not just ``total``) asks again; the newcomer goes
        «напрямую» only after the second «1»."""
        g = self.group()
        ops.user_route(self.w.runner, self.uid, "group:" + g, None)
        u2 = self._second_person()
        asked, between = [], []

        def via(uid):
            return next((u.get("route") or {}).get("via") for u in model.gw(state.load())["users"] if u["id"] == uid)

        def confirm(args, question, *a, **k):
            asked.append(question)
            if len(asked) == 1:                                   # the panel, while the question is up: A out, B in
                ops.user_route(self.w.runner, self.uid, "direct", None)
                ops.user_route(self.w.runner, u2, "group:" + g, None)
            else:
                between.append(via(u2))                           # before the second «1»: B still in the group
            return True
        with mock.patch.object(cli_policy, "confirm", confirm), mock.patch.object(cli_policy, "_tty", lambda: True):
            rc, out, err = self.run_cli("gw", "group-del", g, "--reassign", "direct")
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(len(asked), 2, err)                      # the same count, other devices — asked again
        self.assertEqual(between, ["group:" + g])
        first, again = err.split("Пока шёл вопрос", 1)
        self.assertIn("1 устройство", first)
        self.assertIn("1 устройство", again)
        self.assertEqual((via(self.uid), via(u2)), ("direct", "direct"))

    def test_policy_reset_writes_with_the_rev_of_its_dry_run(self):
        other = self.group()
        seen, asked = [], []
        real = pops.reset

        def spy(runner, **kw):
            try:
                res = real(runner, **kw)
            except errors.KaskadError as e:
                seen.append((kw.get("dry"), kw.get("want_rev"), None, getattr(e, "key", None)))
                raise
            seen.append((kw.get("dry"), kw.get("want_rev"), res.get("rev"), None))
            return res

        def confirm(args, question, *a, **k):
            asked.append(question)
            if len(asked) == 1:                                   # another write: the policy changes, no device moves
                pops.group_flap_standby(self.w.runner, other, False)
            return True
        with mock.patch.object(pops, "reset", spy), mock.patch.object(cli_policy, "confirm", confirm), \
                mock.patch.object(cli_policy, "_tty", lambda: True):
            rc, out, err = self.run_cli("gw", "policy-reset", "--preset", "compat")
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(len(asked), 2)
        dry0, write0, dry1, write1 = seen
        self.assertEqual((dry0[0], dry0[1]), (True, None))
        self.assertEqual((write0[0], write0[1], write0[3]), (False, dry0[2], "policy_changed"))   # its dry run's rev
        self.assertEqual((write1[0], write1[1], write1[3]), (False, dry1[2], None))
        self.assertNotEqual(dry0[2], dry1[2])
        self.assertEqual(P.group(self.pol(), "main")["preset"], "compat")

    def test_cancel_on_the_second_question_writes_nothing_and_rounds_end(self):
        g = self.group()
        ops.user_route(self.w.runner, self.uid, "group:" + g, None)
        u2 = self._second_person()
        answers = [True, False]

        def confirm(args, question, *a, **k):
            if len(answers) == 2:
                ops.user_route(self.w.runner, u2, "group:" + g, None)
            return answers.pop(0)
        with mock.patch.object(cli_policy, "confirm", confirm), mock.patch.object(cli_policy, "_tty", lambda: True):
            rc, out, _err = self.run_cli("gw", "group-del", g, "--reassign", "direct")
        self.assertEqual(rc, 0)
        self.assertIn("Отменено", out)
        self.assertIsNotNone(P.group(self.pol(), g))
        calls = []

        def move(runner, eid, group, tier=None, confirm_role=False, want_rev=None, dry=False, want_moves=None):
            calls.append((dry, want_rev))
            if dry:
                return {"ok": True, "dry": True, "rev": 10 + len(calls), "moves": {"total": 0}, "warnings": []}
            raise pops.policy_changed(99)
        with mock.patch.object(pops, "exit_move", move), mock.patch.object(cli_policy, "_role_changes",
                                                                         lambda e, g: (True, {"role": "ru"})), \
                mock.patch.object(cli_policy, "confirm", lambda *a, **k: True), \
                mock.patch.object(cli_policy, "_tty", lambda: True):
            rc, out, err = self.run_cli("gw", "exit-move", self.vid, "--group", g, "--confirm-role")
        self.assertEqual(rc, errors.CONFLICT)                    # changed every time — at most ASK_ROUNDS questions
        self.assertEqual(calls, [(True, None), (False, 11), (True, None), (False, 13), (True, None), (False, 15)])

    def test_with_rev_given_a_changed_policy_is_not_asked_again(self):
        other = self.group()
        rev = pops.current_rev()
        n = []

        def confirm(args, question, *a, **k):
            n.append(1)
            pops.group_flap_standby(self.w.runner, other, False)
            return True
        with mock.patch.object(cli_policy, "confirm", confirm), mock.patch.object(cli_policy, "_tty", lambda: True):
            rc, out, err = self.run_cli("gw", "policy-reset", "--preset", "compat", "--rev", str(rev))
        self.assertEqual((rc, len(n)), (errors.CONFLICT, 1))


class SpeedRefusedTest(T10Case):
    """sec №2: a refusal of the unit (busy, the budget, the exit off) or a failed test — at once with its reason,
    ``running`` false; a refusal of the day's budget tells about --force."""

    def _run(self, write, *flags):
        from kaskad.gw.probe import unit
        clock = [time.time()]
        start = clock[0]
        with mock.patch.object(unit, "manual", lambda r, eid, force=False: write(eid, int(clock[0]))), \
                mock.patch.object(cli_policy, "_now", lambda: clock[0]), \
                mock.patch.object(cli_policy, "_sleep", lambda s: clock.__setitem__(0, clock[0] + s)):
            rc, doc, err = self.run_json("gw", "exit-speedtest", self.vid, *flags)
        return rc, doc, clock[0] - start

    def _db(self, why, ok=False):
        import sqlite3
        from kaskad.gw import metrics

        def write(eid, ts):
            w = metrics.open_writer()                           # the base with its tables (or the collector's)
            if w is not None:
                w.close()
            c = sqlite3.connect(paths.metrics_db())
            c.execute("INSERT OR REPLACE INTO speed VALUES (?,?,?,?,?,?,?,?,?)",
                      (eid, ts + 1, None, None, None, None, None, 1 if ok else 0, why))
            c.commit()
            c.close()
        return write

    def _results(self, why):
        from kaskad.gw.probe import run as prun

        def write(eid, ts):
            prun.commit([{"exit": eid, "kind": "speed", "ts": ts, "ok": False, "why": why, "manual": True}])
        return write

    def test_budget_of_the_day_at_once_with_the_word_force(self):
        rc, doc, waited = self._run(self._db("budget_day"))
        self.assertEqual(rc, errors.CONFLICT, doc)
        self.assertEqual((doc["data"]["running"], doc["data"]["why"]), (False, "budget_day"))
        self.assertIn("суточный лимит тестов скорости исчерпан", doc["error"])
        self.assertIn("с --force", doc["error"])
        self.assertLess(waited, cli_policy.SPEED_WAIT)

    def test_busy_from_the_results_of_the_unit(self):
        rc, doc, waited = self._run(self._results("busy"))
        self.assertEqual((rc, doc["data"]["why"], doc["data"]["running"]), (errors.LOCKED, "busy", False))
        self.assertIn("другой тест скорости ещё идёт", doc["error"])
        self.assertLess(waited, cli_policy.SPEED_WAIT)

    def test_a_failed_test_says_why(self):
        rc, doc, _w = self._run(self._db("timeout"), "--force")
        self.assertEqual((rc, doc["data"]["why"]), (errors.ERROR, "timeout"))
        self.assertIn("тест скорости не прошёл — не уложились по времени", doc["error"])
        self.assertNotIn("--force", doc["error"])


class PoisonedPolicyTest(T10Case):
    """sec №3: a policy the reader cannot take (``tiers[].exits`` a string — a hand edit) never takes the list of the
    exits or the screen «Состояние» down: ``default_route`` is null, the rows stay."""

    def test_exits_and_status_with_a_broken_policy(self):
        with state.transaction() as st:
            st["gateway"]["exit_policy"] = {"version": 1, "groups": [{"id": "main", "tiers": [{"id": "t1", "exits": "zz"}]}]}
        rc, doc, err = self.run_json("gw", "exits")
        self.assertEqual(rc, 0, err)
        self.assertIsNone(doc["default_route"])
        self.assertEqual(len(doc["exits"]), 2)
        # ``gw status`` with such a policy falls in ops.no_exits_problem (policy.place_new) — before T10, in the backlog


class EnableOnePathTest(T10Case):
    """strict №1: --enable/--disable — always the button of the panel (policy_ops.exit_enabled): ``rev`` and ``moves``
    in the answer, the bot's proxy rebuilt; with --name — the name by the path of 3.3.5, then the same button."""

    def test_disable_without_flags_goes_through_the_button(self):
        built = []
        with mock.patch.object(pops, "tg_rebuild", lambda runner, now=None: built.append(1)):
            rc, doc, err = self.run_json("gw", "exit", self.vid, "--disable")
            self.assertEqual(rc, 0, err)
            self.assertIn("rev", doc)
            self.assertIn("moves", doc)
            self.assertFalse(doc["enabled"])
            self.ok("gw", "exit", self.aid, "--disable")
        self.assertTrue(built)                                   # the indexes of the bot's proxy changed
        self.assertIn("GW policy: exit %s off" % self.vid, self.log())
        rc, doc, err = self.run_json("gw", "exit", self.vid, "--enable", "--name", "Вена")
        self.assertEqual(rc, 0, err)
        self.assertIn("rev", doc)
        p = model.exit_pref(state.load(), self.vid)
        self.assertEqual((p["enabled"], p["name"]), (True, "Вена"))
        rc, out, err = self.run_cli("gw", "exit", self.vid, "--name", "Рига")    # the name alone — the rename of T13
        self.assertEqual(rc, 0, err)
        self.assertIn("Сохранено: «Рига»", out)
        self.assertIn("Сам ключ и настройки не меняются.", out)
        self.assertEqual(model.exit_pref(state.load(), self.vid)["name"], "Рига")

    def test_name_with_the_switch_action_first_and_both_said(self):
        """REV-3371 П-2: ``--disable --name X`` — the name checked first (a refused name — nothing done), the switch,
        then the rename; the answer says both (JSON: ``rename``)."""
        before = self.state_bytes()
        rc, _out, _err = self.run_cli("gw", "exit", self.vid, "--disable", "--name", "a|b")
        self.assertEqual(rc, errors.USAGE)
        self.assertEqual(self.state_bytes(), before)                  # the switch is not done either
        rc, out, err = self.run_cli("gw", "exit", self.vid, "--disable", "--name", "Рига")
        self.assertEqual(rc, 0, err)
        p = model.exit_pref(state.load(), self.vid)
        self.assertEqual((p["enabled"], p["name"]), (False, "Рига"))
        self.assertIn("сохранено", out)
        self.assertIn("Сохранено: «Рига»", out)
        rc, doc, err = self.run_json("gw", "exit", self.vid, "--enable", "--name", "Вена")
        self.assertEqual(rc, 0, err)
        self.assertIn("rev", doc)
        self.assertEqual(doc["rename"]["name"], "Вена")
        # a refusal of the name after the switch (a race): the switch is said, then the refusal — never «saved» alone
        refused = errors.KaskadError("refused", code=errors.CONFLICT)
        with mock.patch.object(ops, "exit_set", side_effect=refused):
            rc, out, err = self.run_cli("gw", "exit", self.vid, "--disable", "--name", "Осло")
        self.assertEqual(rc, errors.CONFLICT)
        self.assertIn("сохранено", out)
        self.assertNotIn("Осло", out)
        p = model.exit_pref(state.load(), self.vid)
        self.assertEqual((p["enabled"], p["name"]), (False, "Вена"))
        # REV-3371-2 П-2: with --json — ONE document: the refusal, the switch in its «data»
        with mock.patch.object(ops, "exit_set", side_effect=errors.KaskadError("refused", code=errors.CONFLICT)):
            rc, out, err = self.run_cli("gw", "exit", self.vid, "--enable", "--name", "Осло", "--json")
        self.assertEqual(rc, errors.CONFLICT)
        lines = [ln for ln in out.splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1, out)
        doc = json.loads(lines[0])
        self.assertEqual((doc["ok"], doc["error"]), (False, "refused"))
        self.assertTrue(doc["data"]["switch"]["ok"])
        self.assertTrue(model.exit_pref(state.load(), self.vid)["enabled"])


class FlapAndNoExitTest(T10Case):
    """strict №4: P2 — ``group-flap-standby`` compares ``rev`` and says «gone» of a deleted group; P5 — the devices
    without an exit are counted by their own group."""

    def test_flap_standby_stale_rev_and_gone_group(self):
        g = self.group()
        rev = pops.current_rev()
        before = self.state_bytes()
        rc, doc, _ = self.run_json("gw", "group-flap-standby", g, "off", "--rev", str(rev - 1))
        self.assertEqual(rc, errors.CONFLICT)
        self.assertEqual(self.state_bytes(), before)
        rc, doc, _ = self.run_json("gw", "group-flap-standby", "g-000000", "off")
        self.assertEqual((rc, doc["data"].get("gone")), (errors.CONFLICT, True))
        self.assertEqual(self.state_bytes(), before)

    def test_devices_without_an_exit_by_their_group(self):
        from kaskad import snapshot
        from kaskad.gw import live
        g = self.group()
        now = time.time()
        doc, _age = live.read()
        doc = dict(doc or {}, ts=now, no_exit={self.did: {"step": "last_block", "group": "main", "since": int(now) - 60},
                                               "d-00000099": {"step": "last_block", "group": g, "since": int(now) - 30},
                                               "d-00000098": {"step": "last_block", "group": g, "since": int(now) - 20}})
        util.atomic_write(paths.live_json(), json.dumps(doc), 0o644)
        snapshot.forget()
        rc, doc, err = self.run_json("gw", "groups")
        self.assertEqual(rc, 0, err)
        by = dict((x["id"], x["no_exit"]) for x in doc["groups"])
        self.assertEqual((by["main"], by[g], doc["no_exit"]), (1, 2, 3))


class Round2WordsTest(T10Case):
    """The придирки: the same role — no question; the refusal tells --confirm-role; the hint of group-del can be
    copied; the names of the exits and of a deleted group in the summaries; a quota of nan/inf — the words of a field."""

    def test_move_to_the_same_role_with_confirm_role_asks_nothing(self):
        g = self.group()
        with mock.patch.object(cli_policy, "_tty", lambda: True), self.stdin("1\n"):     # KT3-9: the plain question
            rc, out, err = self.run_cli("gw", "exit-move", self.vid, "--group", g, "--confirm-role")
        self.assertEqual(rc, 0, err)
        self.assertNotIn("Перенести в группу", err)
        self.assertNotIn("через бывший зарубежный выход", err + out)
        self.assertEqual(P.group_of(self.pol(), self.vid)["id"], g)

    def test_the_refusal_of_another_role_tells_the_flag(self):
        ru = self.group("Дом", role="ru")
        rc, doc, _ = self.run_json("gw", "exit-move", self.vid, "--group", ru)
        self.assertEqual(rc, errors.CONFLICT)
        self.assertIn("kaskadctl gw exit-move %s --group %s --confirm-role" % (self.vid, ru), doc["error"])

    def test_the_hint_of_group_del_is_a_command(self):
        g = self.group()
        pops.exit_move(self.w.runner, self.vid, g)
        rc, doc, _ = self.run_json("gw", "group-del", g, "--exits-to", "main")
        self.assertEqual(rc, errors.USAGE)
        self.assertIn("kaskadctl gw group-del %s --exits-to main --yes" % g, doc["error"])
        self.assertNotIn("…", doc["error"])

    def test_names_not_ids_and_the_deleted_group_by_its_name(self):
        name = cli_policy._exit_name(self.vid)
        self.assertNotEqual(name, self.vid)
        out, _err = self.ok("gw", "exit", self.vid, "--standby")
        self.assertIn("Выход «%s»: в запасе" % name, out)
        self.assertNotIn(self.vid, out)
        out, _err = self.ok("gw", "exit", self.vid, "--restore")
        self.assertIn("«%s»" % name, out)
        g = self.group()
        ops.user_route(self.w.runner, self.uid, "group:" + g, None)
        out, _err = self.ok("gw", "group-del", g, "--reassign", "direct", "--yes")
        self.assertIn("Группа «Стриминг» удалена.", out)
        self.assertIn("Телефон: Стриминг", out)                  # «что изменилось» — the name of the deleted group
        self.assertNotIn(g, out)

    def test_quota_of_nan_inf_and_huge_is_a_refusal_of_the_field(self):
        before = self.state_bytes()
        for v in ("nan", "inf", "1e308", "-inf", "2000000"):
            rc, doc, _ = self.run_json("gw", "exit", self.aid, "--quota-gb=" + v)
            self.assertEqual((rc, doc["data"].get("field")), (errors.USAGE, "quota.limit_gb"), v)
            self.assertIn("гигабайт", doc["error"])
        self.assertEqual(self.state_bytes(), before)


class TofuCliTest(T10Case):
    """§7.7, §9.1: ``gw exit-preview --tofu`` shows the fingerprint and how to agree; ``gw exit-add --pin-tofu`` pins
    the one shown (the core takes it again: another — 409 ``bad_pin``, nothing added); a value that is not a SHA-256 —
    USAGE before the key is read."""

    SHA = "ab" * 32
    INSECURE = VLESS_B.replace("#Riga", "&allowInsecure=1#Riga")          # TLS without a check and no fingerprint

    def _tofu(self, asked, pinned):
        from kaskad.gw import tofu

        def check(parser, line, by="panel", runner=None):
            asked.append(by)
            return {"status": "ask", "server": "198.51.100.21:8443", "sha256": self.SHA} \
                if line in (VLESS_B, self.INSECURE) else None

        def consent(parser, line, s, by, runner=None):
            pinned.append((line == VLESS_B, s, by))
            return {"status": "pinned"}
        return mock.patch.object(tofu, "check", check), mock.patch.object(tofu, "consent", consent)

    def test_preview_shows_the_fingerprint_and_the_command(self):
        asked, pinned = [], []
        c1, c2 = self._tofu(asked, pinned)
        with c1, c2, self.stdin(self.INSECURE + "\n"):
            rc, out, err = self.run_cli("gw", "exit-preview", "--tofu")
        self.assertEqual(rc, 0, err)
        self.assertEqual((asked, pinned), (["cli"], []))
        self.assertIn("будет закреплён отпечаток " + self.SHA, out)
        self.assertIn("kaskadctl gw exit-add --pin-tofu %s" % self.SHA, out)
        with c1, c2, self.stdin(self.INSECURE + "\n"):
            self.ok("gw", "exit-preview")
        self.assertEqual(asked, ["cli"])                          # without --tofu nothing goes to the network

    def test_pin_only_the_fingerprint_shown_and_by_whom(self):
        asked, pinned = [], []
        c1, c2 = self._tofu(asked, pinned)
        before = exits.pool_lines()
        with c1, c2, self.stdin(VLESS_B + "\n"):
            rc, doc, _ = self.run_json("gw", "exit-add", "--pin-tofu", "cd" * 32)
        self.assertEqual((rc, doc["data"].get("code")), (errors.CONFLICT, "bad_pin"))
        self.assertEqual((pinned, exits.pool_lines()), ([], before))
        with c1, c2, self.stdin(VLESS_B + "\n"):
            rc, doc, err = self.run_json("gw", "exit-add", "--pin-tofu", ":".join([self.SHA.upper()[i:i + 2]
                                                                                    for i in range(0, 64, 2)]))
        self.assertEqual(rc, 0, err)
        self.assertEqual(pinned, [(True, self.SHA, "cli")])
        self.assertIn(exits.exit_id(VLESS_B), [i for i, _ in exits.line_ids()])

    def test_by_mcp_and_a_bad_value_before_the_key(self):
        got = []
        with mock.patch.object(ops, "exit_add", lambda r, t, group=None, pin_tofu=None, by="panel":
                               got.append((pin_tofu, by)) or {"ok": True, "added": [], "text": "", "results": []}), \
                self.stdin(VLESS_B + "\n"):
            self.ok("gw", "exit-add", "--pin-tofu", self.SHA, "--pin-by=mcp")
        self.assertEqual(got, [(self.SHA, "mcp")])
        read = []
        with mock.patch.object(sys, "stdin", mock.Mock(read=lambda n: read.append(n) or VLESS_B, isatty=lambda: False)):
            for bad in ("zz" * 32, "ab" * 31, "-x"):
                rc, doc, _ = self.run_json("gw", "exit-add", "--pin-tofu=" + bad)
                self.assertEqual((rc, doc["data"].get("code")), (errors.USAGE, "bad_pin"), bad)
        self.assertEqual(read, [])                               # the key never read for a bad value


class BotPoolNameTest(T10Case):
    """§7.10, проверка 16: the bot names the pool by the group «main» — «группа «Основная»», after a rename its name."""

    def test_pool_by_the_name_of_main(self):
        from kaskad.bot import gateway as bg
        from kaskad.i18n import use_lang
        g = self.group()
        self.assertEqual(bg._route_text({"via": "pool"}, state.load()), "группа «Основная»")
        self.assertEqual(bg._route_text({"via": "group:" + g}, state.load()), "группа «Стриминг»")
        pops.group_rename(self.w.runner, "main", "Европа")
        self.assertEqual(bg._route_text({"via": "pool", "fail": "direct"}, state.load()),
                         "группа «Европа», при отказе — напрямую")
        pops.group_rename(self.w.runner, "main", "")
        with use_lang("en"):
            self.assertEqual(bg._route_text({"via": "pool"}, state.load()), "group “Main”")


class PeopleRouteViewTest(T10Case):
    """REV-s1-t10 №3 (класс): the lists of people (17 → 2, agents) carry ``fail_eff`` and ``group_name`` of each route —
    the SSH menu says «при отказе — напрямую» when the GROUP goes directly, and names a group, not its id."""

    def test_user_and_device_lists_say_what_the_route_does(self):
        g = self.group()
        with state.transaction() as st:
            pol = pops._base(st)
            P.group(pol, g)["fail"] = "direct"
            st["gateway"]["exit_policy"] = pol
        ops.user_route(self.w.runner, self.uid, "group:" + g, None)
        rc, doc, err = self.run_json("gw", "user", "list")
        self.assertEqual(rc, 0, err)
        r = doc["users"][0]["route"]
        self.assertEqual((r["via"], r["fail"], r["fail_eff"], r["group_name"]), ("group:" + g, "block", "direct", "Стриминг"))
        rc, doc, err = self.run_json("gw", "device", "list")
        self.assertEqual(rc, 0, err)
        eff = doc["devices"][0]["route_effective"]
        self.assertEqual((eff["via"], eff["fail_eff"], eff["group_name"]), ("group:" + g, "direct", "Стриминг"))


class ExitDelGroupsKT3Test(T10Case):
    """KT3-1 (стенд 3.3.7.1): ``gw exit-del`` оставлял id в уровнях групп — «Выходы 20» при 14 в пуле."""

    def tiers_ids(self):
        return [e for g in P.stored(state.load())["groups"] for t in g["tiers"] for e in t["exits"]]

    def test_a_deleted_key_leaves_its_group_at_once(self):
        gid = self.group()
        self.ok("gw", "exit-move", self.vid, "--group", gid, "--yes")
        self.assertIn(self.vid, self.tiers_ids())
        self.ok("gw", "exit-del", self.vid)
        self.assertNotIn(self.vid, [eid for eid, _ in exits.line_ids()])
        self.assertNotIn(self.vid, self.tiers_ids())                     # out of «Стриминг» at once
        self.assertIn(self.aid, self.tiers_ids())                        # the others stay
        rc, doc, err = self.run_json("gw", "groups")
        self.assertEqual(rc, 0, err)
        self.assertNotIn(self.vid, json.dumps(doc))

    def test_the_collector_takes_old_ids_out(self):
        st = state.load()
        doc = P.stored(st)
        doc["groups"][0]["tiers"][0]["exits"] += ["e-00000000aa", "e-00000000bb"]   # left by 3.3.7
        st["gateway"]["exit_policy"] = doc
        rev.note(st)                                                      # counted then, as any write of 3.3.7
        state.save(st)
        before = rev.rev_of(state.load())
        collect.tick(self.w.runner)
        self.assertEqual(sorted(self.tiers_ids()), sorted([self.vid, self.aid]))
        self.assertEqual(rev.rev_of(state.load()), before + 1)
        collect.tick(self.w.runner)
        self.assertEqual(rev.rev_of(state.load()), before + 1)            # nothing more to take: no new rev
        self.assertIn("gone from the groups: e-00000000aa, e-00000000bb", self.log())

    def test_an_unreadable_pool_takes_nothing(self):
        from kaskad.gw import policy_ops
        doc = P.stored(state.load())
        with mock.patch.object(policy_ops.util, "read_text", lambda p: None):
            self.assertIsNone(policy_ops.known_ids())
        self.assertEqual(P.prune_gone(doc, None), [])

    def kept_in_tiers(self, eid="e-abcdef0123"):
        """An exit of a subscription in «main» under the id subs.json kept for it (not its id by text)."""
        st = state.load()
        doc = P.stored(st)
        doc["groups"][0]["tiers"][0]["exits"].append(eid)
        st["gateway"]["exit_policy"] = doc
        rev.note(st)
        state.save(st)
        return eid

    def test_a_broken_subs_file_takes_nothing(self):
        """REV-3371-3 З-1: subs.json битый или не читается — ид выходов подписок не известны, из групп ничего не уходит."""
        import os
        from kaskad.gw import policy_ops
        eid = self.kept_in_tiers()
        p = os.path.join(paths.egress_dir(), "subs.json")
        util.atomic_write(p, '{"ids": {', 0o600)                       # broken mid-way
        self.assertFalse(exits.kept_ids_ok())
        self.assertIsNone(policy_ops.known_ids())
        collect.tick(self.w.runner)
        self.assertIn(eid, self.tiers_ids())
        self.assertNotIn("gone from the groups", self.log())
        util.atomic_write(p, '{"ids": []}', 0o600)                      # «ids» of another kind: broken too
        self.assertIsNone(policy_ops.known_ids())
        with mock.patch.object(exits.util, "read_text",
                               lambda f, _r=util.read_text: None if f == p else _r(f)):
            exits._IDS_CACHE.clear()
            self.assertIsNone(policy_ops.known_ids())                   # there, not readable
        exits._IDS_CACHE.clear()
        util.atomic_write(p, "{}", 0o600)                               # whole again: the id no file has goes
        self.assertTrue(exits.kept_ids_ok())
        collect.tick(self.w.runner)
        self.assertNotIn(eid, self.tiers_ids())
        self.assertIn(self.vid, self.tiers_ids())

    def test_an_empty_or_locked_pool_takes_nothing(self):
        """REV-3371-3 З-1: откат install.sh пишет файл пула «: >», затем строки — под блокировкой пула. Пустой
        upstream.txt — зарубежные группы целы; занятая блокировка — из групп ничего не уходит."""
        import fcntl
        import os
        from kaskad.gw import policy_ops, tofu
        eid = self.kept_in_tiers("e-00000000aa")
        up = paths.egress_up()
        lines = util.read_text(up)
        util.atomic_write(up, "", 0o600)
        self.assertEqual(policy_ops.known_ids(), {"abroad": None, "ru": []})
        collect.tick(self.w.runner)
        self.assertEqual(sorted(self.tiers_ids()), sorted([self.vid, self.aid, eid]))
        util.atomic_write(up, lines, 0o600)
        util.ensure_run_dir(os.path.dirname(tofu.lock_path()))
        fd = os.open(tofu.lock_path(), os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)                              # install.sh holds the pool
            with mock.patch.object(policy_ops, "KNOWN_LOCK_WAIT", 0.2):
                self.assertIsNone(policy_ops.known_ids())
                collect.tick(self.w.runner)
            self.assertIn(eid, self.tiers_ids())
        finally:
            os.close(fd)
        known = policy_ops.known_ids()
        self.assertIn(self.vid, known["abroad"])
        self.assertNotIn(eid, known["abroad"])
        collect.tick(self.w.runner)
        self.assertEqual(sorted(self.tiers_ids()), sorted([self.vid, self.aid]))

    def test_the_collector_does_not_wait_for_a_busy_pool(self):
        """REV-3371-pre З-1: сборщик держит state.lock — блокировку пула он не ждёт (занята — вырезание на следующем
        такте) и пишет об этом в журнал не чаще раза в POOL_BUSY_LOG_EVERY; ``after_pool`` ждёт, как прежде."""
        import fcntl
        import os
        from kaskad.gw import policy_ops, tofu
        eid = self.kept_in_tiers("e-00000000aa")
        util.ensure_run_dir(os.path.dirname(tofu.lock_path()))
        fd = os.open(tofu.lock_path(), os.O_WRONLY | os.O_CREAT, 0o600)
        clock = [1000000.0]
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)                              # install.sh holds the pool (an import)
            with mock.patch.object(policy_ops, "KNOWN_LOCK_WAIT", 3.0), \
                    mock.patch.dict(collect._live_errors, clear=True), \
                    mock.patch.object(collect, "_now", lambda: clock[0]):
                t0 = time.monotonic()
                collect.tick(self.w.runner)
                self.assertLess(time.monotonic() - t0, 2.5)             # not the 3 s of after_pool
                clock[0] += 60
                collect.tick(self.w.runner)                             # the next tick: busy again — no new line
                self.assertEqual(self.log().count("egress_pool_locked"), 1)
                clock[0] += collect.POOL_BUSY_LOG_EVERY
                collect.tick(self.w.runner)
                self.assertEqual(self.log().count("egress_pool_locked"), 2)
                t0 = time.monotonic()
                self.assertIsNone(policy_ops.known_ids())               # after_pool still waits for the lock
                self.assertGreaterEqual(time.monotonic() - t0, 2.9)
            self.assertIn(eid, self.tiers_ids())
        finally:
            os.close(fd)
        collect.tick(self.w.runner)                                     # free again: pruned
        self.assertNotIn(eid, self.tiers_ids())

    def in_group(self, gid, eid):
        st = state.load()
        doc = P.stored(st)
        P.group(doc, gid)["tiers"][0]["exits"].append(eid)
        st["gateway"]["exit_policy"] = doc
        rev.note(st)
        state.save(st)

    def test_an_empty_foreign_pool_still_prunes_the_russian_groups(self):
        """REV-3371-pre З-2: пустой upstream.txt (удалён последний зарубежный ключ, узел только с российскими
        выходами) не отключает вырезание российских выходов: российские группы — по upstream-ru.txt, зарубежные целы."""
        from kaskad.gw import policy_ops
        ru = self.group("Дом", role="ru")
        self.in_group(ru, "e-00000000cc")                               # a Russian exit deleted meanwhile
        eid = self.kept_in_tiers("e-00000000aa")
        util.atomic_write(rev.ru_pool_path(), "", 0o600)
        util.atomic_write(paths.egress_up(), "", 0o600)
        self.assertEqual(policy_ops.known_ids(), {"abroad": None, "ru": []})
        collect.tick(self.w.runner)
        self.assertEqual(P.group(P.stored(state.load()), ru)["tiers"][0]["exits"], [])
        self.assertEqual(sorted(self.tiers_ids()), sorted([self.vid, self.aid, eid]))
        self.assertIn("gone from the groups: e-00000000cc", self.log())

    def test_prune_by_role(self):
        """``prune_gone`` по ролям: роль None — группы этой роли целы; ид любого известного файла остаётся в группе
        другой роли (перенос с --confirm-role); группа неизвестной роли — не трогается."""
        doc = {"groups": [{"id": "main", "role": "abroad", "tiers": [{"exits": ["a1", "gone"]}]},
                          {"id": "home", "role": "ru", "tiers": [{"exits": ["r1", "a1", "gone"]}]},
                          {"id": "odd", "role": "mars", "tiers": [{"exits": ["gone"]}]}]}
        gone = P.prune_gone(doc, {"abroad": None, "ru": ["r1"]})
        self.assertEqual(gone, [("a1", "home"), ("gone", "home")])
        self.assertEqual([t["exits"] for g in doc["groups"] for t in g["tiers"]], [["a1", "gone"], ["r1"], ["gone"]])
        doc["groups"][1]["tiers"][0]["exits"] = ["r1", "a1"]
        self.assertEqual(P.prune_gone(doc, {"abroad": ["a1"], "ru": ["r1"]}), [("gone", "main")])
        self.assertEqual(doc["groups"][1]["tiers"][0]["exits"], ["r1", "a1"])
        self.assertEqual(P.prune_gone(doc, ["a1"]), [("r1", "home"), ("gone", "odd")])   # a plain list: every group

    def test_an_unreadable_subs_file_is_not_cached(self):
        """REV-3371-pre П-3: subs.json не читался (права), права поправили без записи — следующий вызов читает его."""
        import os
        p = os.path.join(paths.egress_dir(), "subs.json")
        util.atomic_write(p, "{}", 0o600)
        exits._IDS_CACHE.clear()
        with mock.patch.object(exits.util, "read_text", lambda f, _r=util.read_text: None if f == p else _r(f)):
            self.assertFalse(exits.kept_ids_ok())
        self.assertTrue(exits.kept_ids_ok())                            # same inode, mtime and size
