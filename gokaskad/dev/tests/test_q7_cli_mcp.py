"""Q7 (SPEC-stage1-exit-groups §8.7–8.8, §12.1): ``kaskadctl rules ru-via`` — «Российские сайты» пользователя или
устройства из командной строки и MCP; ``gw exit-add --group`` российской группы; каталог MCP ``rules_ru_via``.

Российская группа — с предупреждением «российский трафик … пойдёт через ваш роутер» и вопросом цифрами
(«1) Да  2) Нет  Выбор [2]:», правило владельца: цифры, не y/n); без терминала — только с ``--yes`` (MCP передаёт его,
инструмент ``destructive``). ``inherit`` и ``direct`` не спрашивают."""

import io
import json
import os
import sys
from unittest import mock

from kaskad import i18n, state
from kaskad.gw import model
from tests.gw.test_ru_target import D1, HOME, RuWorld

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class RuViaCliTest(RuWorld):
    def setUp(self):
        super().setUp()
        self.world(via=None)
        self.uid = state.load()["gateway"]["users"][0]["id"]

    def own(self, target="user", ref=None):
        st = state.load()
        obj = model.find_user(st, ref or self.uid) if target == "user" else model.find_device(st, ref or D1)
        return obj.get("ru_via")

    def tty(self, answers):
        return mock.patch("kaskad.gw.cli_policy._tty", return_value=True), \
            mock.patch.object(sys, "stdin", io.StringIO(answers))

    def test_a_group_with_yes_writes_and_warns(self):
        rc, doc, err = self.run_json("rules", "ru-via", "user", self.uid, HOME, "--yes")
        self.assertEqual(rc, 0, err)
        self.assertEqual((doc["ru_via"], doc["changed"]), (HOME, True))
        self.assertIn("пойдёт через ваш роутер (группа «Дом»)", doc["warning"])
        self.assertIn("интернет-провайдер увидит", doc["warning"])
        self.assertEqual(self.own(), HOME)

    def test_the_text_and_the_warning_on_stderr(self):
        rc, out, err = self.run_cli("rules", "ru-via", "device", D1, HOME, "--yes")
        self.assertEqual(rc, 0, err)
        self.assertIn("Российские сайты у «Телефон»: через группу «Дом».", out)
        self.assertIn("пойдёт через ваш роутер", err)
        self.assertEqual(self.own("device"), HOME)

    def test_without_a_terminal_and_without_yes_nothing_is_written(self):
        rc, out, err = self.run_cli("rules", "ru-via", "user", self.uid, HOME)
        self.assertEqual(rc, 2, out + err)
        self.assertIn("--yes", err)
        self.assertIsNone(self.own())
        rc, _out, err = self.run_cli("rules", "ru-via", "user", self.uid, HOME, "--json")
        self.assertEqual(rc, 2, err)
        self.assertIsNone(self.own())
        rc, _out, err = self.run_cli("rules", "ru-via", "device", "Телефон", HOME)       # a name: the hint quotes it
        self.assertEqual(rc, 2, err)
        self.assertIn("подтвердите: kaskadctl rules ru-via device 'Телефон' %s --yes" % HOME, err)
        self.assertNotIn("--dry", err)                                   # ru-via has no dry run
        st = state.load()
        st["gateway"]["devices"][0]["name"] = "Мой телефон; rm"
        state.save(st)
        rc, _out, err = self.run_cli("rules", "ru-via", "device", "Мой телефон; rm", HOME)
        self.assertIn("'Мой телефон; rm'", err)

    def test_the_question_with_numbers_two_by_default(self):
        a, b = self.tty("\n")
        with a, b:
            rc, out, err = self.run_cli("rules", "ru-via", "user", self.uid, HOME)
        self.assertEqual(rc, 0)
        self.assertIn("1) Да", err)
        self.assertIn("2) Нет", err)
        self.assertIn("Выбор [2]:", err)
        self.assertIn("Отменено", out)
        self.assertIsNone(self.own())
        a, b = self.tty("y\n2\n")
        with a, b:
            rc, out, err = self.run_cli("rules", "ru-via", "user", self.uid, HOME)
        self.assertIn("Нет такого пункта: введите 1 или 2.", err)     # «y» is not an answer: numbers only
        self.assertIsNone(self.own())
        a, b = self.tty("1\n")
        with a, b:
            rc, out, err = self.run_cli("rules", "ru-via", "user", self.uid, HOME)
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.own(), HOME)

    def test_direct_and_inherit_never_ask(self):
        self.run_json("rules", "ru-via", "user", self.uid, HOME, "--yes")
        rc, doc, err = self.run_json("rules", "ru-via", "user", self.uid, "direct")
        self.assertEqual((rc, doc["ru_via"]), (0, "direct"), err)
        self.assertNotIn("warning", doc)
        rc, doc, err = self.run_json("rules", "ru-via", "user", self.uid, "inherit")
        self.assertEqual((rc, doc["ru_via"]), (0, "inherit"), err)
        self.assertIsNone(self.own())

    def test_inherit_of_a_device_whose_user_has_a_group_asks(self):
        self.run_json("rules", "ru-via", "user", self.uid, HOME, "--yes")
        self.run_json("rules", "ru-via", "device", D1, "direct")
        rc, out, err = self.run_cli("rules", "ru-via", "device", D1, "inherit")
        self.assertEqual(rc, 2, out + err)                              # through «Дом» again — asks
        self.assertEqual(self.own("device"), "direct")

    def test_bad_values_are_refused_before_any_question(self):
        for via in ("main", "g-ffffff", "pool", "group:" + HOME):
            rc, out, err = self.run_cli("rules", "ru-via", "user", self.uid, via, "--yes")
            self.assertEqual(rc, 2, via + out + err)
        self.assertIsNone(self.own())
        rc, out, err = self.run_cli("rules", "ru-via", "user", "u-ffffffff", HOME, "--yes")
        self.assertNotEqual(rc, 0)

    def test_not_acting_is_said(self):
        st = state.load()
        st["gateway"]["users"][0]["rules"] = "all_exit"
        state.save(st)
        rc, out, err = self.run_cli("rules", "ru-via", "user", self.uid, HOME, "--yes")
        self.assertEqual(rc, 0, err)
        self.assertIn("Работает только при профиле «Россия напрямую»", out)

    def test_english(self):
        with i18n.use_lang("en"):
            rc, out, err = self.run_cli("rules", "ru-via", "device", D1, HOME, "--yes")
        self.assertEqual(rc, 0, err)
        self.assertIn("Russian sites of “Телефон”: through the group “Дом”.", out)
        self.assertIn("will go through your router", err)


class ExitAddGroupCliTest(RuWorld):
    def test_the_group_reaches_the_core(self):
        self.world()
        with mock.patch("kaskad.gw.ops.exit_add", return_value={"text": "ok", "added": []}) as add, \
                mock.patch.object(sys, "stdin", io.StringIO("vless://x\n")):
            rc, out, err = self.run_cli("gw", "exit-add", "--group", HOME)
        self.assertEqual(rc, 0, err)
        self.assertEqual(add.call_args.kwargs.get("group"), HOME)


class McpCatalogTest(RuWorld):
    def catalog(self):
        with open(os.path.join(ROOT, "lib", "kaskad", "mcp", "actions.json"), encoding="utf-8") as f:
            return json.load(f)

    def test_rules_ru_via(self):
        d = self.catalog()
        a = d["actions"]["rules_ru_via"]
        self.assertEqual(a["cmd"], ["rules", "ru-via"])
        self.assertTrue(a["destructive"])
        self.assertIn("--yes", a["args"])
        self.assertEqual([k for k, v in a["params"].items() if v.get("pos")], ["target", "id", "via"])
        pat = d["param_types"][a["params"]["via"]["ref"]]["pattern"]
        import re
        for ok in ("inherit", "direct", HOME):
            self.assertRegex(ok, pat)
        for bad in ("main", "pool", "group:" + HOME, "-x", "g-0a0b0"):
            self.assertNotRegex(bad, pat)
        self.assertIn("роутер", a["description"]["ru"])

    def test_exit_add_says_the_russian_group(self):
        a = self.catalog()["actions"]["exit_add"]
        self.assertIn("внутренн", a["description"]["ru"])
        self.assertIn("domestic", a["description"]["en"])

    def test_the_guide_and_the_spec_name_it(self):
        for rel in ("lib/kaskad/mcp/AGENT_GUIDE.md", "docs/k3/SPEC-mcp.md"):
            with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
                self.assertIn("rules_ru_via", f.read(), rel)
