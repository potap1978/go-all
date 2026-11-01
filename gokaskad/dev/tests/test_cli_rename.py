"""T13 N4 (SPEC-stage1-rename §4.2, §4.3): ``kaskadctl … rename`` — exit codes (0, 2 the check, 3 a duplicate), --json,
a leading «-» through «=»; the MCP tools of the renames map to these commands."""

import json
import os

from kaskad import errors, state
from kaskad.bot import aliases
from kaskad.gw import exitsubs, model, ops
from kaskad.mcp import server as mcp_server
from tests.fakes_nft import save_state
from tests.test_gw_ops import F443, CliGwCase


class CliRenameTest(CliGwCase):
    def setUp(self):
        super().setUp()
        self.enabled_world()
        ops.inbound_install(self.w.runner, "wg")
        self.uid = ops.user_add(self.w.runner, "Мама")["id"]
        self.u2 = ops.user_add(self.w.runner, "Дача")["id"]
        self.did = ops.device_add(self.w.runner, self.uid, "Телефон", access=("wg",))["device"]["id"]
        self.d2 = ops.device_add(self.w.runner, self.uid, "Ноутбук")["device"]["id"]

    def test_user_codes_and_json(self):
        rc, doc, _ = self.run_json("gw", "user", "rename", self.uid, "--name=-Оля-")
        self.assertEqual(rc, 0)
        self.assertEqual((doc["ok"], doc["id"], doc["old"], doc["name"], doc["what"]),
                         (True, self.uid, "Мама", "-Оля-", "user"))
        self.assertIn("note", doc)
        rc, doc, _ = self.run_json("gw", "user", "rename", self.uid, "--name=дача")
        self.assertEqual((rc, doc["ok"], doc["code"]), (errors.CONFLICT, False, errors.CONFLICT))
        rc, doc, _ = self.run_json("gw", "user", "rename", self.uid, "--name=" + "x" * 41)
        self.assertEqual((rc, doc["data"]["reason"]), (errors.USAGE, "long"))
        rc, _out, _err = self.run_cli("gw", "user", "rename", self.uid, "--name=a\nb")
        self.assertEqual(rc, errors.USAGE)
        rc, doc, _ = self.run_json("gw", "user", "rename", "u-00000000", "--name=x")
        self.assertEqual(rc, errors.ERROR)
        self.assertIn("RENAME user %s" % self.uid, self.log())
        self.assertIn("(via=cli)", self.log())

    def test_device_duplicate_and_the_flag(self):
        rc, doc, _ = self.run_json("gw", "device", "rename", self.did, "--name=ноутбук")
        self.assertEqual((rc, doc["data"]["need"], doc["data"]["duplicates"]),
                         (errors.CONFLICT, "confirm_duplicate", [self.d2]))
        rc, doc, _ = self.run_json("gw", "device", "rename", self.did, "--name=ноутбук", "--allow-duplicate",
                                   "--via=ssh")
        self.assertEqual((rc, doc["duplicates"]), (0, [self.d2]))
        self.assertIn("(via=ssh)", self.log())
        rc, _out, err = self.run_cli("gw", "device", "rename", "НОУТБУК", "--name=Ноут")   # ambiguous: refused
        self.assertEqual(rc, errors.ERROR, err)

    def test_text_output_and_the_note(self):
        out, _ = self.ok("gw", "device", "rename", self.did, "--name=Телефон Оли")
        self.assertIn("«Телефон Оли»", out)
        self.assertIn("второй туннель", out)

    def test_exit_inbound_sub(self):
        rc, doc, _ = self.run_json("gw", "exit", self.vid, "--name=Вена")
        self.assertEqual((rc, doc["name"], doc["what"]), (0, "Вена", "exit"))
        rc, doc, _ = self.run_json("gw", "exit", self.vid, "--name=")
        self.assertEqual((rc, doc["name"]), (0, ""))
        rc, doc, _ = self.run_json("gw", "exit", self.vid, "--name=" + "x" * 41)
        self.assertEqual(rc, errors.USAGE)
        rc, doc, _ = self.run_json("gw", "inbound", "label", "wg", "--name=для роутеров")
        self.assertEqual((rc, doc["name"]), (0, "для роутеров"))
        self.assertEqual(model.inbound(state.load(), "wg")["label"], "для роутеров")
        exitsubs.save({"schema": 1, "next": 2, "subs": [{"id": "s-aaaaaa", "n": 1, "url": "https://sub.example.com/a",
                                                          "keys": [], "auto": True}]})
        rc, doc, _ = self.run_json("gw", "exit-sub", "rename", "1", "--name=Моя")
        self.assertEqual((rc, doc["shown"]), (0, "Моя"))
        rc, doc, _ = self.run_json("gw", "exit-sub", "rename", "s-aaaaaa", "--name=")
        self.assertEqual((rc, doc["shown"]), (0, "sub.example.com"))


class CliForwardServerTest(CliGwCase):
    def setUp(self):
        super().setUp()
        save_state([F443])

    def test_fwd_and_server(self):
        rc, doc, _ = self.run_json("fwd", "rename", "tcp", "443", "--name=Сайт")
        self.assertEqual((rc, doc["id"], doc["name"]), (0, "tcp:443", "Сайт"))
        rc, doc, _ = self.run_json("fwd", "rename", "tcp", "444", "--name=x")
        self.assertEqual(rc, errors.ERROR)
        rc, doc, _ = self.run_json("server", "rename", "203.0.113.5", "--name=Вена")
        self.assertEqual((rc, doc["shown"]), (0, "Вена"))
        aliases.update("203.0.113.6", name="Рига")
        rc, doc, _ = self.run_json("server", "rename", "203.0.113.5", "--name=рига")
        self.assertEqual((rc, doc["data"]["need"]), (errors.CONFLICT, "confirm_duplicate"))
        rc, doc, _ = self.run_json("server", "rename", "203.0.113.5", "--name=a\\b")
        self.assertEqual(rc, errors.USAGE)


class McpRenameToolsTest(CliGwCase):
    """The tools of the renames: the catalog maps them to the commands above, «--via=mcp» for the journal."""

    def catalog(self):
        with open(os.path.join(os.path.dirname(mcp_server.__file__), "actions.json"), encoding="utf-8") as f:
            return json.load(f)

    def test_tools_and_types(self):
        d = self.catalog()
        want = {"person_rename": ["gw", "user", "rename"], "device_rename": ["gw", "device", "rename"],
                "inbound_label": ["gw", "inbound", "label"], "exit_sub_rename": ["gw", "exit-sub", "rename"],
                "forward_rename": ["fwd", "rename"], "server_rename": ["server", "rename"],
                "exit_rename": ["gw", "exit"]}
        for name, cmd in want.items():
            a = d["actions"][name]
            self.assertEqual(a["cmd"], cmd, name)
            self.assertFalse(a["readonly"] or a["destructive"], name)
            self.assertIn("--via=mcp", a.get("args") or [], name)
            self.assertEqual(a["params"]["allow_duplicate"]["flag"], "--allow-duplicate", name)
        import re
        for t in ("person_name", "device_name", "exit_name", "label", "new_name"):
            pat = re.compile(d["param_types"][t]["pattern"])
            self.assertIsNone(pat.match("x" * 41), t)
            self.assertIsNone(pat.match("a|b"), t)
            self.assertIsNotNone(pat.match("x" * 40), t)
        self.assertIsNotNone(re.match(d["param_types"]["new_name"]["pattern"], "-Вена"))


if __name__ == "__main__":
    import unittest
    unittest.main()
