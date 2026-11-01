"""R7 — the ⚠RU confirmation through MCP (docs/k3/SPEC-stage1-rf-mark-builtin.md §5.3, §9): ``confirm_not_rf`` of
rules_default, rules_profile and rules_consent is a PHRASE — exactly the text of ``rules.rf.consent`` in Russian or
English (NFC, spaces around trimmed); true, a paraphrase or another language's word order are refused before kaskadctl
runs — with the block itself in the message and in ``data`` (REV-s1-r67 З-2). The phrase is not in the schema; the three
tools are marked for the client to ask the human (``destructiveHint``, SEC-s1-r67 №2); ``orgs`` — the organisations
of the block shown: without it the core refuses the tick with the block (SEC-s1-r67 №1). Through the real kaskadctl:
consent ``via = mcp``, ``who = "mcp"``, no address in ``rf_consent``."""

import io
import json
import os

from kaskad.mcp import server as mcp
from tests.test_mcp import McpCase, call

RU = "Скрипт не используется на территории РФ"
EN = "The script is not used in the Russian Federation"


class PhraseTest(McpCase):
    def test_phrase_is_checked_and_only_the_flag_goes_to_kaskadctl(self):
        self.assertEqual(mcp._nfc(" и\u0306 "), "й")          # NFC before the comparison (a decomposed letter)
        s = self.run_session([
            call(1, "rules_default", {"profile": "ru_direct", "confirm_not_rf": True}),
            call(2, "rules_default", {"profile": "ru_direct", "confirm_not_rf": "yes"}),
            call(3, "rules_default", {"profile": "ru_direct", "confirm_not_rf": "скрипт не используется на территории рф"}),
            call(4, "rules_default", {"profile": "ru_direct", "confirm_not_rf": "  " + RU + " "}),
            call(5, "rules_profile", {"target": "device", "id": "d-1a2b3c4d", "profile": "ru_direct",
                                      "confirm_not_rf": EN}),
            call(6, "rules_consent", {"confirm_not_rf": RU}),
            call(7, "rules_consent", {}),
            call(8, "rules_default", {"profile": "all_exit"}),
            call(9, "rules_consent", {"confirm_not_rf": RU + "."}),
            call(10, "rules_consent", {"confirm_not_rf": RU, "orgs": "meta,fbk"}),
            call(11, "rules_consent", {"confirm_not_rf": RU, "orgs": "meta,--via=ssh"}),
            call(12, "rules_consent", {"confirm_not_rf": RU, "orgs": "Meta"}),
        ])
        for i in (1, 2, 3, 7, 9, 11, 12):
            self.assertEqual(s.error(i)["code"], -32602, i)
        block = "⚠ Данная организация признана экстремистской на территории Российской Федерации (Meta, ФБК)"
        self.assertEqual(s.error(2)["data"], {"param": "confirm_not_rf", "phrase": "rules.rf.consent", "block": block,
                                              "orgs": ["meta", "fbk"], "consent": RU})
        self.assertIn(EN, s.error(3)["message"])
        self.assertIn("«%s»" % block, s.error(3)["message"])            # §5.3: the refusal says the block itself
        self.assertIn("orgs", s.error(3)["message"])
        self.assertEqual([c["argv"] for c in s.calls], [
            ["rules", "default", "ru_direct", "--confirm-not-rf", "--via=mcp", "--json"],
            ["rules", "profile", "device", "d-1a2b3c4d", "ru_direct", "--confirm-not-rf", "--via=mcp", "--json"],
            ["rules", "consent", "--confirm-not-rf", "--via=mcp", "--json"],
            ["rules", "default", "all_exit", "--via=mcp", "--json"],
            ["rules", "consent", "--confirm-not-rf", "--orgs=meta,fbk", "--via=mcp", "--json"]])
        rejected = [r for r in s.audit_recs() if r.get("status") == "rejected"]
        self.assertEqual(len(rejected), 7)                    # every refusal is in the audit

    def test_schema_says_it_is_the_humans_phrase(self):
        for lang, phrases in (("en", (EN, RU)), ("ru", (RU, EN))):
            s = self.run_session([{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}], KASKAD_MCP_LANG=lang)
            tools = dict((t["name"], t) for t in s.result(1)["tools"])
            for name in ("rules_default", "rules_profile", "rules_consent"):
                t = tools[name]
                p = t["inputSchema"]["properties"]["confirm_not_rf"]
                self.assertEqual(p["type"], "string", name)
                self.assertIn("confirmed themselves" if lang == "en" else "подтвердил сам", p["description"])
                for x in phrases:                               # the phrase — in the refusal only (SEC-s1-r67 №2)
                    self.assertNotIn(x.lower(), json.dumps(t, ensure_ascii=False).lower(), (lang, name))
                self.assertTrue(t["annotations"]["destructiveHint"], name)   # the client asks the human
                self.assertNotIn("confirm", t["inputSchema"]["properties"], name)   # no confirm= on top of it
                self.assertEqual(t["inputSchema"]["properties"]["orgs"]["type"], "string", name)
        self.assertEqual(tools["rules_consent"]["inputSchema"]["required"], ["confirm_not_rf"])
        self.assertNotIn("confirm_not_rf", tools["rules_default"]["inputSchema"].get("required") or [])

    def test_phrase_is_nowhere_before_the_refusal(self):
        # REV-s1-rf-2 З-1: the agent sees the phrase only in data.consent of the refusal — not in the guide
        # (kaskad://guide, read before the first mutating call), not in instructions, the schemas or the resources.
        self.assertEqual(mcp.phrase_texts("rules.rf.consent"), (RU, EN))
        low = [x.casefold() for x in (RU, EN)]
        with open(os.path.join(os.path.dirname(mcp.__file__), "AGENT_GUIDE.md"), encoding="utf-8") as f:
            guide = f.read()
        self.assertIn("data.consent", guide)
        for x in low:
            self.assertNotIn(x, guide.casefold())
        for lang in ("ru", "en"):
            s = self.run_session([{"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                                  {"jsonrpc": "2.0", "id": 2, "method": "resources/list"},
                                  {"jsonrpc": "2.0", "id": 3, "method": "resources/read",
                                   "params": {"uri": "kaskad://guide"}}], KASKAD_MCP_LANG=lang)
            seen = json.dumps([s.result("init"), s.result(1), s.result(2), s.result(3)], ensure_ascii=False)
            self.assertIn("data.consent", s.result(3)["contents"][0]["text"])
            for x in low:
                self.assertNotIn(x, seen.casefold(), lang)

    def test_the_cli_hint_is_said_for_the_agent(self):
        cat = mcp.load_catalog(os.path.join(os.path.dirname(mcp.__file__), "actions.json"), "ru", mcp.Log("error", io.StringIO()))
        tool = cat.tools["rules_default"]
        text = tool.agent_words("⚠ Данная организация …\nПовторите с --confirm-not-rf (подтверждение: скрипт не "
                                "используется на территории РФ).")
        self.assertNotIn("--confirm-not-rf", text)
        self.assertIn("confirm_not_rf фразу, которую он подтвердил, а в orgs — data.orgs", text)
        self.assertEqual(mcp.parser_problems(cat), [])


class RealCoreTest(McpCase):
    def test_consent_through_the_real_core(self):
        s = self.run_session([
            call(1, "rules_default", {"profile": "ru_direct"}),
            call(2, "rules_default", {"profile": "ru_direct", "confirm_not_rf": RU}),
            call(4, "rules_default", {"profile": "ru_direct", "confirm_not_rf": RU, "orgs": "meta"}),
            call(5, "rules_default", {"profile": "ru_direct", "confirm_not_rf": RU, "orgs": "meta,fbk"}),
            call(3, "rules_status")], timeout=120, KASKAD_MCP_CTL="")
        r = s.result(1)
        self.assertTrue(r["isError"])
        doc = r["structuredContent"]
        self.assertEqual((doc["code_name"], doc["data"]["need"]), ("conflict", "rf_consent"))
        self.assertIn("Российской Федерации", doc["data"]["block"])
        self.assertIn("confirm_not_rf", doc["error"])
        self.assertNotIn("--confirm-not-rf", doc["error"])
        self.assertIn("data.orgs", doc["error"])
        self.assertEqual(doc["data"]["orgs"], ["meta", "fbk"])
        # the phrase without orgs, and orgs narrower than the block — refused with the block, nothing written
        for i in (2, 4):
            r = s.result(i)
            self.assertTrue(r["isError"], i)
            self.assertEqual(r["structuredContent"]["data"]["need"], "rf_consent", i)
        self.assertFalse(s.result(5)["isError"], s.result(5))
        rf = s.result(3)["structuredContent"]["rf"]
        self.assertEqual((rf["mode"], rf["consent"]["via"]), ("full", "mcp"))
        with open(os.path.join(self.tmp, "root", "etc", "kaskad", "state.json"), encoding="utf-8") as f:
            c = json.load(f)["gateway"]["rf_consent"]
        self.assertEqual((c["via"], c["who"]), ("mcp", "mcp"))
        self.assertNotIn("127.0.0.1", json.dumps(c))
        self.assertFalse(set(c) - {"at", "via", "who", "orgs", "registry", "lang"})
