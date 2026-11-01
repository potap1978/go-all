"""В1 (владелец 28.10 23:37 «Тоже да»; IT-1 строка 109): «Удалить группу вместе с ключами» — в CLI и MCP, как в панели
(решение 8а: ``policy_ops.group_delete(exits="drop")``, сухой прогон, 409 ``ru_last`` без него).

CLI: ``kaskadctl gw group-del <gid> --with-keys`` — сухой прогон (``--dry``) и вопрос на терминале называют ключи, что
уйдут с сервера; ``--yes`` — сразу. Без флага — как было: 409 ``ru_last``, но текст подсказывает ``--with-keys``.
MCP: ``group_delete`` с ``with_keys`` (флаг с потерей данных: ``confirm_with_keys``), не вместе с ``exits_to``; подсказки
CLI агенту — именами параметров. Каждый тест падает на коде до В1: там флага ``--with-keys`` и поля ``with_keys`` нет."""

import contextlib
import io
import json
import os
from unittest import mock

from kaskad import cli, errors
from kaskad.gw import cli_policy, policy_ops
from kaskad.i18n import tr
from kaskad.mcp import server as mcp
from tests.gw.test_it1_group_drop import DropCase
from tests.gw.test_s1_ru_pool_ops import HOME
from tests.test_mcp import McpCase, call


class CliDropCase(DropCase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                rc = cli.main(["gw", "group-del"] + list(argv))
            except SystemExit as e:
                rc = e.code
        return rc, out.getvalue(), err.getvalue()

    def run_json(self, *argv):
        rc, out, err = self.run_cli(*(argv + ("--json",)))
        return rc, json.loads(out), err


class CliWithKeysTest(CliDropCase):
    def test_without_the_flag_ru_last_tells_with_keys(self):
        """Как было: последняя российская группа с ключами — 409 ``ru_last``, ничего не тронуто; текст — с подсказкой."""
        rc, doc, err = self.run_json(HOME, "--reassign", "direct", "--yes")
        self.assertEqual(rc, errors.CONFLICT, err)
        self.assertEqual(sorted(doc["data"]["exits"]), sorted(self.keys))
        self.assertIn(tr("gw_policy_ops.ru_last"), doc["error"])
        self.assertIn("повторите с --with-keys", doc["error"])
        self.untouched()

    def test_dry_names_the_keys_and_writes_nothing(self):
        calls = len(self.w.script.calls)
        rc, out, err = self.run_cli(HOME, "--with-keys", "--reassign", "direct", "--dry")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("Удалятся ключи группы: Router, Dacha.", out)
        self.assertIn("Ничего не изменено (--dry).", out)
        rc, doc, err = self.run_json(HOME, "--with-keys", "--reassign", "direct", "--dry")
        self.assertEqual(rc, 0, err)
        self.assertEqual((doc["dry"], sorted(doc["drop"])), (True, sorted(self.keys)))
        self.assertEqual(len(self.w.script.calls), calls)            # no script: nothing written
        self.untouched()

    def test_off_a_terminal_only_with_yes(self):
        rc, doc, _ = self.run_json(HOME, "--with-keys", "--reassign", "direct")
        self.assertEqual(rc, errors.USAGE)
        self.assertIn("--with-keys --yes", doc["error"])               # how to say yes — with the flag
        self.untouched()

    def test_yes_drops_the_group_with_its_keys(self):
        rc, out, err = self.run_cli(HOME, "--with-keys", "--reassign", "direct", "--yes")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("Группа «Дом» удалена вместе со своими ключами: Router, Dacha.", out)
        self.assertEqual(self.ru(), [])
        self.assertNotIn(HOME, self.groups())
        self.assertEqual(self.ru_via(), "direct")

    def test_the_question_names_the_keys(self):
        asked = []

        def confirm(args, question, yes, *a, **k):
            asked.append((question, yes))
            return True
        with mock.patch.object(cli_policy, "confirm", confirm), mock.patch.object(cli_policy, "_tty", lambda: True):
            rc, out, err = self.run_cli(HOME, "--with-keys", "--reassign", "direct")
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(asked, [("Удалить группу «Дом» вместе с её ключами? Ключи уйдут с сервера: Router, Dacha.",
                                  "Удалить вместе с ключами")])
        self.assertNotIn(HOME, self.groups())

    def test_people_without_reassign_are_told_how(self):
        rc, doc, _ = self.run_json(HOME, "--with-keys", "--yes")
        self.assertEqual(rc, errors.CONFLICT)
        self.assertEqual(doc["data"]["refs"]["users"], [self.uid])
        self.assertIn("--reassign direct", doc["error"])
        self.untouched()

    def test_with_keys_and_exits_to_together_refused(self):
        rc, doc, _ = self.run_json(HOME, "--with-keys", "--exits-to", "main", "--reassign", "direct", "--yes")
        self.assertEqual(rc, errors.USAGE)
        self.assertEqual(doc["error"], tr("gw_cli.gdel.keys_or_move"))
        self.untouched()

    def test_with_keys_and_subs_to_together_refused(self):
        """REV-3312 П-2: с --with-keys подписки не переносятся (ядро: 409 need «subs» при любом subs_to) — --subs-to
        не принимается молча, а отказ USAGE и до записи, и в сухом прогоне."""
        for extra in (("--dry",), ("--yes",)):
            rc, doc, _ = self.run_json(HOME, "--with-keys", "--subs-to", "main", "--reassign", "direct", *extra)
            self.assertEqual(rc, errors.USAGE, extra)
            self.assertEqual(doc["error"], tr("gw_cli.gdel.keys_no_subs"), extra)
        self.assertIn(HOME, self.groups())
        self.untouched()

    def test_a_group_abroad_is_never_dropped(self):
        gid = policy_ops.group_add(self.w.runner, "Стриминг", role="abroad")["id"]
        rc, doc, _ = self.run_json(gid, "--with-keys", "--yes")
        self.assertEqual(rc, errors.USAGE)
        self.assertEqual(doc["error"], tr("gw_cli.gdel.keys_ru_only"))
        self.assertIn(gid, self.groups())
        self.untouched()


class McpWithKeysTest(McpCase):
    def test_argv_and_the_danger_confirm(self):
        s = self.run_session([
            call(1, "group_delete", {"group": "g-1a2b3c", "with_keys": True, "reassign": "direct", "dry": True,
                                     "confirm": "g-1a2b3c"}),
            call(2, "group_delete", {"group": "g-1a2b3c", "with_keys": True, "reassign": "direct", "dry": True,
                                     "confirm": "g-1a2b3c", "confirm_with_keys": "with_keys"}),
            call(3, "group_delete", {"group": "g-1a2b3c", "with_keys": True, "exits_to": "main",
                                     "confirm": "g-1a2b3c", "confirm_with_keys": "with_keys"}),
            call(4, "group_delete", {"group": "g-1a2b3c", "with_keys": True, "reassign": "direct",
                                     "confirm": "g-1a2b3c", "confirm_with_keys": "with_keys"}),
            call(5, "group_delete", {"group": "g-1a2b3c", "with_keys": True, "subs_to": "main", "reassign": "direct",
                                     "confirm": "g-1a2b3c", "confirm_with_keys": "with_keys"}),
        ])
        self.assertEqual(s.error(1)["code"], -32602)                 # data lost: confirm_with_keys is a must
        self.assertEqual(s.error(3)["code"], -32602)                 # not together with exits_to
        self.assertEqual(s.error(5)["code"], -32602)                 # nor with subs_to (REV-3312 П-2)
        self.assertEqual([c["argv"] for c in s.calls], [
            ["gw", "group-del", "g-1a2b3c", "--with-keys", "--reassign=direct", "--dry", "--yes", "--json"],
            ["gw", "group-del", "g-1a2b3c", "--with-keys", "--reassign=direct", "--yes", "--json"]])

    def test_the_cli_hints_are_said_for_the_agent(self):
        for lang in ("ru", "en"):
            cat = mcp.load_catalog(os.path.join(os.path.dirname(mcp.__file__), "actions.json"), lang,
                                   mcp.Log("error", io.StringIO()))
            tool = cat.tools["group_delete"]
            for key in ("gw_cli.gdel.ru_last_hint", "gw_cli.gdel.keys_reassign", "gw_cli.gdel.keys_ru_only"):
                text = tool.agent_words(tr(key, lang))
                self.assertNotRegex(text, r"--(with-keys|reassign|exits-to|dry)\b", (lang, key, text))
            self.assertIn('with_keys=true', tool.agent_words(tr("gw_cli.gdel.ru_last_hint", lang)))
            self.assertIn('confirm_with_keys="with_keys"', tool.agent_words(tr("gw_cli.gdel.ru_last_hint", lang)))
            self.assertIn('reassign="direct"', tool.agent_words(tr("gw_cli.gdel.keys_reassign", lang)))
            self.assertEqual(mcp.parser_problems(cat), [])

