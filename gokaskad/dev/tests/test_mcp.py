"""MCP-сервер kaskad-mcp (lib/kaskad/mcp, docs/k3/SPEC-mcp.md).

Сервер запускается настоящим подпроцессом (``python3 -m kaskad.mcp`` — как его запустит ssh у клиента), kaskadctl
подменён заглушкой tests/fixtures/mcp/fake_kaskadctl.py. Каждый тест — отдельная сессия: сообщения пишутся в stdin
разом, ответы читаются из stdout после завершения — так проверяется и то, что в stdout нет ничего, кроме JSON-RPC.
Сверка каталога с настоящим разборщиком kaskadctl и сессия с настоящим kaskadctl — в конце.
"""

import base64
import io
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest

from kaskad import __version__
from kaskad.mcp import server as mcp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(ROOT, "lib")
MCP_DIR = os.path.join(LIB, "kaskad", "mcp")
ACTIONS_JSON = os.path.join(MCP_DIR, "actions.json")
GUIDE = os.path.join(MCP_DIR, "AGENT_GUIDE.md")
SPEC_DOC = os.path.join(ROOT, "docs", "k3", "SPEC-mcp.md")
README = os.path.join(ROOT, "README.md")
FAKE = os.path.join(ROOT, "tests", "fixtures", "mcp", "fake_kaskadctl.py")
PROTOCOL = "2025-06-18"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 40

with open(ACTIONS_JSON, encoding="utf-8") as _f:
    ACTIONS = json.load(_f)
TOOLS = ACTIONS["actions"]
DESTRUCTIVE = sorted(n for n, a in TOOLS.items() if a.get("destructive"))
SECRET = sorted(n for n, a in TOOLS.items() if a.get("secret"))
# Единое правило каталога (3.3.7.4 Ш2а, решение координатора, SPEC-mcp «Размер каталога»): tools/list на КАЖДОМ языке
# (ru и en, REV-ns-b14 З-3) — не больше PER_TOOL × N знаков (N — число инструментов), один инструмент — не больше
# TOOL_MAX. Сводный инструмент (действие параметром) длиннее обычного, но короче суммы тех, что он заменил — его держит
# TOOL_MAX. Прежний частный предел линии оценки 76 200 (600 × 127, d4712924) отменён. Новое — в предел, иначе ужимать.
PER_TOOL = 600
TOOL_MAX = 2400


def leaves(tools=None):
    """[(имя, описание)] каждого действия: у сводного инструмента (by_action) — по действию (cmd, args, readonly,
    destructive, свои params с их обязательностью), у обычного — он сам."""
    out = []
    for name, a in (TOOLS if tools is None else tools).items():
        if not a.get("by_action"):
            out.append((name, a))
            continue
        for act, v in a["by_action"].items():
            leaf = dict((k, x) for k, x in a.items() if k not in ("by_action", "action_default"))
            req = v.get("required") or []
            leaf.update(cmd=v["cmd"], args=v.get("args") or [], readonly=bool(v.get("readonly")),
                        destructive=bool(v.get("destructive")),
                        params=dict((p, dict(a["params"][p], required=p in req)) for p in v.get("params") or []))
            out.append(("%s action=%s" % (name, act), leaf))
    return out


LEAVES = leaves()


def init_messages(protocol=PROTOCOL):
    params = {"capabilities": {}, "clientInfo": {"name": "test_mcp", "version": "1"}}
    if protocol is not None:
        params["protocolVersion"] = protocol
    return [{"jsonrpc": "2.0", "id": "init", "method": "initialize", "params": params},
            {"jsonrpc": "2.0", "method": "notifications/initialized"}]


def req(msg_id, method, params=None):
    m = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        m["params"] = params
    return m


def call(msg_id, tool, arguments=None):
    return req(msg_id, "tools/call", {"name": tool, "arguments": arguments if arguments is not None else {}})


def _read(path):
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as f:
        return f.read()


class Session(object):
    def __init__(self, proc, tmp):
        self.proc = proc
        self.stdout = proc.stdout.decode("utf-8")
        self.stderr = proc.stderr.decode("utf-8", "replace")
        self.responses = {}
        self.lines = [l for l in self.stdout.splitlines() if l.strip()]
        for line in self.lines:
            obj = json.loads(line)            # каждая строка stdout — JSON-RPC, иначе тест падает здесь
            for item in obj if isinstance(obj, list) else [obj]:
                self.responses[item.get("id")] = item
        self.calls = []
        log = os.path.join(tmp, "ctl.log")
        if os.path.exists(log):
            with open(log, encoding="utf-8") as f:
                self.calls = [json.loads(l) for l in f if l.strip()]
        self.audit_path = os.path.join(tmp, "audit.log")
        self.audit = _read(self.audit_path)
        self.kaskad_log = _read(os.path.join(tmp, "root", "var", "log", "kaskad.log"))

    def result(self, msg_id):
        r = self.responses[msg_id]
        assert "result" in r, r
        return r["result"]

    def error(self, msg_id):
        r = self.responses[msg_id]
        assert "error" in r, r
        return r["error"]

    def audit_recs(self):
        return [r for r in self.init_recs(all_=True) if r.get("kind") != "initialize"]

    def init_recs(self, all_=False):
        """Записи аудита: initialize (all_ — все); вызовы — audit_recs()."""
        recs = [json.loads(l) for l in self.audit.splitlines() if l.strip()]
        return recs if all_ else [r for r in recs if r.get("kind") == "initialize"]


class McpCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kaskad-mcp-test-")
        os.makedirs(os.path.join(self.tmp, "root"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def env(self, **extra):
        env = dict(os.environ)
        for k in list(env):
            if k.startswith(("FAKE_CTL_", "KASKAD_MCP_")):
                env.pop(k)
        env.update({"PYTHONPATH": LIB, "KASKAD_ROOT": os.path.join(self.tmp, "root"),
                    "KASKAD_MCP_CTL": "%s %s" % (shlex.quote(sys.executable), shlex.quote(FAKE)),
                    "KASKAD_MCP_AUDIT": os.path.join(self.tmp, "audit.log"),
                    "FAKE_CTL_LOG": os.path.join(self.tmp, "ctl.log"), "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8"})
        env.update(dict((k, str(v)) for k, v in extra.items()))
        return env

    def run_session(self, messages, init=True, timeout=60, args=(), **env):
        msgs = (init_messages() if init else []) + messages
        data = "".join(json.dumps(m, ensure_ascii=False) + "\n" for m in msgs).encode("utf-8")
        proc = subprocess.run([sys.executable, "-m", "kaskad.mcp"] + list(args), input=data, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, env=self.env(**env), timeout=timeout, cwd=self.tmp)
        return Session(proc, self.tmp)


# ── протокол ──────────────────────────────────────────────────────────────────────────────────────────────

class InitializeTest(McpCase):
    def test_initialize(self):
        s = self.run_session([req(2, "ping")])
        r = s.result("init")
        self.assertEqual(r["protocolVersion"], PROTOCOL)
        self.assertEqual(r["serverInfo"], {"name": "kaskad", "version": __version__})
        self.assertEqual(set(r["capabilities"]), {"tools", "resources", "prompts"})
        text = r["instructions"]
        for needle in ["kaskad://guide", "gateway_status", "confirm", "user_delete.with_devices"] + DESTRUCTIVE + SECRET:
            self.assertIn(needle, text)
        self.assertNotIn("Read-only", text)                           # один язык (KASKAD_MCP_LANG, по умолчанию ru)
        self.assertEqual(s.result(2), {})
        self.assertNotIn(None, s.responses)                           # уведомление без ответа

    def test_english(self):
        s = self.run_session([req(1, "tools/list"), req(2, "prompts/list")], KASKAD_MCP_LANG="en")
        self.assertIn("Read-only", s.result("init")["instructions"])
        tools = dict((t["name"], t) for t in s.result(1)["tools"])
        self.assertEqual(tools["version"]["description"], TOOLS["version"]["description"]["en"])

    def test_old_protocol_versions_and_unknown(self):
        for version, expect in (("2025-03-26", "2025-03-26"), ("2024-11-05", "2024-11-05"), ("1999-01-01", PROTOCOL)):
            s = self.run_session([req("i", "initialize", {"protocolVersion": version, "capabilities": {}})], init=False)
            self.assertEqual(s.result("i")["protocolVersion"], expect)


class JsonRpcTest(McpCase):
    def test_errors(self):
        msgs = [req(1, "no/such"), {"jsonrpc": "1.0", "id": 2, "method": "ping"}, {"jsonrpc": "2.0", "id": 3},
                req(4, "tools/call", ["not", "an", "object"]), req(5, "tools/call", {"name": "no_such_tool"})]
        s = self.run_session(msgs)
        self.assertEqual(s.error(1)["code"], -32601)
        self.assertEqual(s.error(2)["code"], -32600)
        self.assertEqual(s.error(3)["code"], -32600)
        self.assertEqual(s.error(4)["code"], -32602)
        self.assertEqual(s.error(5)["code"], -32602)

    def test_parse_error_and_batch(self):
        env = self.env()
        data = b"not json\n" + json.dumps([req(1, "ping"), req(2, "ping")]).encode() + b"\n"
        proc = subprocess.run([sys.executable, "-m", "kaskad.mcp"], input=data, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, env=env, timeout=30)
        lines = [json.loads(l) for l in proc.stdout.decode().splitlines()]
        self.assertEqual(lines[0]["error"]["code"], -32700)
        self.assertEqual(sorted(x["id"] for x in lines[1]), [1, 2])


class ToolsListTest(McpCase):
    def test_descriptions_have_no_internal_script_modes(self):
        """T-M6 (перепроверка f2a725a): служебные режимы программы («gokaskad --egress-ensure-parser») агенту не нужны."""
        for name, a in TOOLS.items():
            for lang in ("ru", "en"):
                self.assertNotRegex(a["description"][lang], r"(^|\s)--[a-z]", "%s/%s" % (name, lang))

    def test_list_matches_actions_json(self):
        s = self.run_session([req(1, "tools/list")])
        tools = dict((t["name"], t) for t in s.result(1)["tools"])
        self.assertEqual(set(tools), set(TOOLS))
        for name, t in tools.items():
            a = TOOLS[name]
            acts = list((a.get("by_action") or {}).values())
            # 3.3.7.4 Ш2а: только значимое и отличное от умолчаний MCP (readOnlyHint=false — умолчание, idempotentHint нет)
            self.assertEqual(t["annotations"].get("readOnlyHint", False), bool(a["readonly"]), name)
            self.assertNotIn("idempotentHint", t["annotations"], name)
            if acts:
                self.assertEqual(a["readonly"], all(v.get("readonly") for v in acts), name)
                self.assertEqual(t["inputSchema"]["properties"]["action"]["enum"], list(a["by_action"]), name)
                self.assertEqual("action" in t["inputSchema"].get("required", []), "action_default" not in a, name)
            danger_hint = bool(a["destructive"] or a.get("ask_human") or any(v.get("destructive") for v in acts))
            if a["readonly"]:       # destructiveHint значим только при readOnlyHint=false: у чтения его нет — и опасного нет
                self.assertNotIn("destructiveHint", t["annotations"], name)
                self.assertFalse(danger_hint, name)
            else:                   # у изменяющих — всегда: умолчание MCP — true, наше false осознанное
                self.assertIs(t["annotations"]["destructiveHint"], danger_hint, name)
            self.assertFalse(t["inputSchema"]["additionalProperties"])
            self.assertTrue(t["description"].startswith(a["description"]["ru"]), name)     # один язык
            self.assertEqual(t["annotations"]["openWorldHint"], bool(a.get("open_world")), name)
            props = t["inputSchema"]["properties"]
            danger = set("confirm_" + p for p, d in a["params"].items() if d.get("danger"))
            # confirm of a destructive tool is the server's; host_bench has a confirm of its own (the key of the price)
            mine = {"confirm"} if a["destructive"] or any(v.get("destructive") for v in acts) else set()
            if acts:
                mine.add("action")
            self.assertEqual(set(props) - mine - danger, set(a["params"]), name)
            if a["destructive"]:
                self.assertIn("confirm", t["inputSchema"]["required"], name)
                self.assertIn("confirm", t["description"])
                self.assertRegex(t["description"], r"[.!?…] Необратимо", name)
            elif any(v.get("destructive") for v in acts):      # confirm — только у разрушительного действия
                self.assertNotIn("confirm", t["inputSchema"].get("required", []), name)
                self.assertRegex(t["description"], r"[.!?…] action=[a-z_/]+ необратимо: нужен confirm", name)
            else:
                self.assertEqual("confirm" in props, "confirm" in a["params"], name)   # no server's confirm
            for pname, pdef in a["params"].items():
                if pdef.get("required"):
                    self.assertIn(pname, t["inputSchema"]["required"])

    def test_list_is_compact(self):
        # один язык: на двух было ~66 тыс. знаков на 93 инструмента (~710 на инструмент, находка критика); на одном —
        # ~550. Предел — весь список на каждом языке (английский длиннее, REV-ns-b14 З-3): PER_TOOL × N, TOOL_MAX выше
        for lang in ("ru", "en"):
            s = self.run_session([req(1, "tools/list")], KASKAD_MCP_LANG=lang)
            size = len(json.dumps(s.result(1), ensure_ascii=False, separators=(",", ":")))
            self.assertLessEqual(size, PER_TOOL * len(s.result(1)["tools"]), lang)
            for t in s.result(1)["tools"]:
                self.assertLessEqual(len(json.dumps(t, ensure_ascii=False, separators=(",", ":"))), TOOL_MAX,
                                     (lang, t["name"]))

    def test_host_set_says_probes_only_on_request(self):
        """REV-ns-p3a З-2: «only when the person asks» is about the probes (packets out every 30 s), not only the
        anchors; host_set (a change) does not start as host_health (a read) does."""
        for lang, ask, settings in (("ru", "только по просьбе человека", "Настройки"),
                                    ("en", "only when the person asks", "settings")):
            d = TOOLS["host_set"]["description"][lang]
            probes = d[d.index("probes"):d.index("anchors")]
            self.assertIn(ask, probes, lang)
            self.assertIn(settings, d.split(":")[0], lang)
            health = TOOLS["host_health"]["description"][lang]
            self.assertNotEqual(d.split(":")[0], health.split(":")[0], lang)

    def test_counts_are_what_the_spec_says(self):
        kinds = [("destructive" if a["destructive"] else "read" if a["readonly"] else "write") for a in TOOLS.values()]
        self.assertGreaterEqual(kinds.count("read"), 30)
        self.assertGreaterEqual(kinds.count("write"), 35)
        self.assertGreaterEqual(kinds.count("destructive"), 12)


class ToolsCallTest(McpCase):
    def test_readonly_call_and_argv(self):
        s = self.run_session([call(1, "gateway_status")])
        r = s.result(1)
        self.assertFalse(r["isError"])
        self.assertEqual(r["structuredContent"]["argv"], ["gw", "status", "--json"])
        self.assertEqual(json.loads(r["content"][0]["text"])["ok"], True)
        (c,) = s.calls
        self.assertEqual((c["argv"], c["stdin"]), (["gw", "status", "--json"], ""))

    def test_argv_mapping(self):
        s = self.run_session([
            call(1, "device_add", {"user": "Мама", "name": "Телефон мамы", "platform": "android", "kind": "phone",
                                   "access": "awg2,wg", "route": "exit:e-0123456789", "fail": "direct"}),
            call(2, "inbounds_plan", {"kinds": "wg,awg2,l2tp"}),
            call(3, "forwards_list", {"traffic": True}),
            call(4, "forwards_list", {"traffic": False}),
            call(5, "exit_rename", {"id": "e-0123456789", "name": "-Вена-"}),
            call(6, "device_issue", {"device": "d-1a2b3c4d", "inbound": "awg2", "os": "ios"}),
            call(7, "traffic_trace", {"who": "-", "target": "youtube.com"}),
            call(8, "inbound_install", {"kind": "wg", "port": 51820}),
            call(9, "gateway_detect_host", {"confirm": "gateway_detect_host"}),
            call(10, "site_dns", {"wait": 0}),
        ])
        argvs = [c["argv"] for c in s.calls]
        self.assertEqual(argvs[0], ["gw", "device", "add", "Мама", "Телефон мамы", "--kind=phone", "--platform=android",
                                    "--access=awg2,wg", "--route=exit:e-0123456789", "--fail=direct", "--json"])
        self.assertEqual(argvs[1], ["gw", "inbound", "plan", "wg", "awg2", "l2tp", "--json"])
        self.assertEqual(argvs[2], ["fwd", "list", "--traffic", "--json"])
        self.assertEqual(argvs[3], ["fwd", "list", "--json"])
        self.assertEqual(argvs[4], ["gw", "exit", "e-0123456789", "--name=-Вена-", "--via=mcp", "--json"])   # «-» в значении ключа — можно
        self.assertEqual(argvs[5], ["gw", "issue", "d-1a2b3c4d", "--inbound=awg2", "--os=ios", "--png", "--where=agent",
                                    "--json"])
        self.assertEqual(argvs[6], ["gw", "trace", "-", "youtube.com", "--json"])
        self.assertEqual(argvs[7], ["gw", "inbound", "install", "wg", "--port=51820", "--new-only", "--json"])
        self.assertEqual(argvs[8], ["gw", "host", "--detect", "--json"])
        self.assertEqual(argvs[9], ["site", "dns", "--wait=0", "--json"])

    def test_bad_arguments_never_reach_kaskadctl(self):
        bad = [("user_add", {"name": "--force"}), ("user_add", {"name": "-"}), ("user_add", {"name": "a\nb"}),
               ("user_add", {"name": ""}), ("user_add", {"name": "x" * 65}), ("user_add", {"name": 5}),
               ("user_add", {}), ("user_add", {"name": "ok", "extra": 1}), ("user_add", ["x"]),
               ("device_add", {"user": "u", "name": "d", "platform": "symbian"}),
               ("default_route_set", {"via": "exit:bad"}), ("forward_add", {"proto": "tcp", "port": 70000, "to":
                                                                             "10.0.0.1", "to_port": 1}),
               ("forward_add", {"proto": "tcp", "port": 80, "to": "10.0.0.256", "to_port": 1}),
               ("traffic_trace", {"who": "-x", "target": "ya.ru"}), ("traffic_trace", {"who": "d", "target": "a/b"}),
               ("exits_list", {"x": 1}), ("forwards_list", {"traffic": "yes"}), ("page_url", {"user": "--urls"})]
        s = self.run_session([call(i, t, a) for i, (t, a) in enumerate(bad)])
        for i, (t, a) in enumerate(bad):
            self.assertEqual(s.error(i)["code"], -32602, (t, a))
        self.assertEqual(s.calls, [])
        recs = s.audit_recs()
        self.assertEqual(len(recs), len(bad))
        self.assertTrue(all(r["status"] == "rejected" for r in recs))

    def test_destructive_confirm(self):
        s = self.run_session([
            call(1, "user_delete", {"user": "Мама"}),
            call(2, "user_delete", {"user": "Мама", "confirm": "Папа"}),
            call(3, "user_delete", {"user": "Мама", "confirm": "Мама", "with_devices": True,
                                    "confirm_with_devices": "with_devices"}),
            call(4, "gateway_disable", {"confirm": "yes"}),
            call(5, "gateway_disable", {"confirm": "gateway_disable"}),
            call(6, "forward_delete", {"proto": "tcp", "port": 443, "confirm": "tcp"}),
            call(7, "forward_delete", {"proto": "tcp", "port": 443, "confirm": "443"}),
            call(8, "gateway_purge", {"with_users": True, "confirm": "gateway_purge", "confirm_with_users": "with_users"}),
            call(9, "mtu_s4", {"kind": "awg2", "confirm": "awg2"}),
            call(10, "inbound_reinstall", {"kind": "wg", "confirm": "wg"}),
        ])
        for i in (1, 2, 4, 6):
            self.assertEqual(s.error(i)["code"], -32602, i)
        for i in (3, 5, 7, 8, 9, 10):
            self.assertFalse(s.result(i)["isError"], i)
        self.assertEqual([c["argv"] for c in s.calls], [
            ["gw", "user", "del", "Мама", "--force", "--json"], ["gw", "disable", "--json"],
            ["fwd", "del", "--proto=tcp", "--port=443", "--json"], ["gw", "purge", "--users", "--yes", "--json"],
            ["gw", "mtu", "s4", "awg2", "--yes", "--json"], ["gw", "inbound", "install", "wg", "--json"]])
        recs = s.audit_recs()
        self.assertEqual([r["status"] for r in recs][:2], ["rejected", "rejected"])
        self.assertEqual(recs[2]["params"]["confirm"], "Мама")
        self.assertIn("MCP test_mcp/1: user_delete", s.kaskad_log)    # изменение — и в журнал Kaskad
        self.assertNotIn("gateway_status", s.kaskad_log)

    def test_rejected_combined_call_logs_the_asked_action(self):
        """REV-ns-b14-2 П-1: отклонённый вызов сводного инструмента — в аудите действие, которое просили."""
        s = self.run_session([
            call(1, "host_bench_schedule", {"action": "rm", "entry": "1"}),
            call(2, "host_bench_schedule", {"action": "nope"}),
            call(3, "host_bench_schedule", {"action": ["rm"]}),
            call(4, "host_score", {"action": "show", "entry": "1"}),
        ])
        for i in (1, 2, 3, 4):
            self.assertEqual(s.error(i)["code"], -32602, i)
        self.assertEqual(s.calls, [])
        recs = s.audit_recs()
        self.assertEqual([r["status"] for r in recs], ["rejected"] * 4)
        self.assertEqual([r["command"] for r in recs], ["host bench schedule rm", "host_bench_schedule",
                                                        "host_bench_schedule", "host bench show"])

    def test_data_loss_flags_need_their_own_confirm(self):
        s = self.run_session([
            call(1, "user_delete", {"user": "Мама", "confirm": "Мама", "with_devices": True}),
            call(2, "user_delete", {"user": "Мама", "confirm": "Мама", "with_devices": True, "confirm_with_devices": "yes"}),
            call(3, "gateway_purge", {"confirm": "gateway_purge", "with_users": True}),
            call(4, "user_delete", {"user": "Мама", "confirm": "Мама", "with_devices": False}),
            call(5, "gateway_purge", {"confirm": "gateway_purge"})])
        for i in (1, 2, 3):
            self.assertEqual(s.error(i)["code"], -32602, i)
        self.assertEqual([c["argv"] for c in s.calls], [["gw", "user", "del", "Мама", "--json"],
                                                        ["gw", "purge", "--yes", "--json"]])

    def test_combinations_are_checked_before_kaskadctl(self):
        s = self.run_session([
            call(1, "site_template_set", {}), call(2, "site_template_set", {"id": "x", "random": True}),
            call(3, "site_mode", {"mode": "site", "template": "x", "random": True}),
            call(4, "site_template_set", {"random": True}), call(5, "site_mode", {"mode": "site", "random": True}),
            call(6, "site_template_set", {"id": "x", "random": False})])
        for i in (1, 2, 3):
            self.assertEqual(s.error(i)["code"], -32602, i)
        self.assertEqual([c["argv"] for c in s.calls], [["site", "template", "set", "--random", "--json"],
                                                        ["site", "mode", "site", "--random", "--via=mcp", "--json"],
                                                        ["site", "template", "set", "x", "--json"]])

    def test_a_change_is_refused_without_audit(self):
        bad = os.path.join(self.tmp, "no-dir-file")
        with open(bad, "w") as f:
            f.write("x")
        s = self.run_session([call(1, "users_list"), call(2, "user_add", {"name": "Мама"})],
                             KASKAD_MCP_AUDIT=os.path.join(bad, "audit.log"))
        self.assertFalse(s.result(1)["isError"])
        self.assertEqual(s.error(2)["code"], -32603)
        self.assertEqual([c["argv"][:3] for c in s.calls], [["gw", "user", "list"]])
        s = self.run_session([call(1, "device_issue", {"device": "d-1a2b3c4d"})],      # секреты — тоже только со следом
                             KASKAD_MCP_AUDIT=os.path.join(bad, "audit.log"))
        self.assertEqual(s.error(1)["code"], -32603)
        self.assertEqual(len(s.calls), 1)                         # прежний users_list, выдачи не было

    def test_progress_notifications(self):
        msgs = [req(1, "tools/call", {"name": "version", "arguments": {}, "_meta": {"progressToken": "p1"}})]
        s = self.run_session(msgs, FAKE_CTL_SLEEP=2.5, KASKAD_MCP_PROGRESS="1")
        notes = [json.loads(l) for l in s.lines if '"notifications/progress"' in l]
        self.assertTrue(notes, s.lines)
        self.assertEqual(notes[0]["params"]["progressToken"], "p1")
        self.assertFalse(s.result(1)["isError"])

    def test_bad_input_never_kills_the_server(self):
        env = self.env()
        data = b"[" * 200000 + b"\n" + b'{"jsonrpc":"2.0","id":2,"method":"ping","params":{"x":NaN}}\n' + \
            json.dumps(req(3, "ping")).encode() + b"\n"
        proc = subprocess.run([sys.executable, "-m", "kaskad.mcp"], input=data, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, env=env, timeout=30)
        lines = [json.loads(l) for l in proc.stdout.decode().splitlines()]
        self.assertEqual([x.get("id") for x in lines], [None, None, 3])
        self.assertEqual([x["error"]["code"] for x in lines[:2]], [-32700, -32700])
        s = self.run_session([call(1, "version")], FAKE_CTL_REPLY='{"ok": true, "x": NaN}')
        self.assertEqual(s.result(1)["structuredContent"]["code"], "transport")

    def test_a_change_is_not_killed_on_timeout(self):
        t0 = time.time()
        s = self.run_session([call(1, "user_add", {"name": "Мама"}), req(2, "ping")], FAKE_CTL_SLEEP=3,
                             KASKAD_MCP_TIMEOUT=1, FAKE_CTL_DONE=os.path.join(self.tmp, "done"))
        sc = s.result(1)["structuredContent"]
        self.assertEqual((sc["code"], sc["running"]), ("timeout", True))
        self.assertIn("pid", sc)
        self.assertEqual(s.result(2), {})
        deadline = time.time() + 15
        while not os.path.exists(os.path.join(self.tmp, "done")) and time.time() < deadline:
            time.sleep(0.2)
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "done")))          # дошла до конца, а не убита

    def test_agent_gets_parameter_names_not_cli_flags(self):
        reply = json.dumps({"ok": False, "error": "У пользователя «Мама» есть устройства (2): удалите их или повторите с --force.",
                            "code": 2})
        s = self.run_session([call(1, "user_delete", {"user": "Мама", "confirm": "Мама"})], FAKE_CTL_REPLY=reply,
                             FAKE_CTL_RC=2)
        e = s.result(1)["structuredContent"]["error"]
        self.assertIn('with_devices=true и confirm_with_devices="with_devices"', e)
        self.assertNotIn("--", e)
        reply = json.dumps({"ok": False, "error": "Порт 443/tcp занят: nginx. Добавить всё равно — с --force.", "code": 3})
        s = self.run_session([call(1, "forward_add", {"proto": "tcp", "port": 443, "to": "10.0.0.2", "to_port": 443})],
                             FAKE_CTL_REPLY=reply, FAKE_CTL_RC=3)
        self.assertIn("с force=true", s.result(1)["structuredContent"]["error"])
        reply = json.dumps({"ok": False, "error": "Вход wg уже установлен. Переустановка — без --new-only (порт…).", "code": 3})
        s = self.run_session([call(1, "inbound_install", {"kind": "wg"})], FAKE_CTL_REPLY=reply, FAKE_CTL_RC=3)
        self.assertIn("inbound_reinstall", s.result(1)["structuredContent"]["error"])
        self.assertNotIn("--new-only", s.result(1)["structuredContent"]["error"])

    def test_apply_noise_is_dropped_and_text_is_compact(self):
        reply = json.dumps({"ok": True, "device": {"id": "d-1"}, "warnings": ["a"],
                            "apply": {"dns": {"x": 1}, "nft": "…" * 500, "warnings": ["b", "a"]}})
        s = self.run_session([call(1, "device_add", {"user": "u", "name": "d"})], FAKE_CTL_REPLY=reply)
        r = s.result(1)
        self.assertNotIn("apply", r["structuredContent"])
        self.assertEqual(r["structuredContent"]["warnings"], ["a", "b"])
        self.assertNotIn("\n", r["content"][0]["text"])

    def test_error_reply_is_flagged(self):
        reply = json.dumps({"ok": False, "error": "Порт 443 занят: nginx", "code": 3, "data": {"port": 443}})
        s = self.run_session([call(1, "forward_add", {"proto": "tcp", "port": 443, "to": "10.0.0.2", "to_port": 443})],
                             FAKE_CTL_REPLY=reply, FAKE_CTL_RC=3)
        r = s.result(1)
        self.assertTrue(r["isError"])
        sc = r["structuredContent"]
        self.assertEqual((sc["code"], sc["code_name"], sc["exit_code"], sc["error"]), (3, "conflict", 3, "Порт 443 занят: nginx"))
        self.assertEqual(s.audit_recs()[-1]["status"], "error")
        self.assertEqual(s.audit_recs()[-1]["code"], "conflict")

    def test_check_with_problems_is_an_error_without_ok_key(self):
        s = self.run_session([call(1, "gateway_probe")], FAKE_CTL_REPLY='{"supported": false, "reason": "Debian 10"}',
                             FAKE_CTL_RC=5)
        sc = s.result(1)["structuredContent"]
        self.assertTrue(s.result(1)["isError"])
        self.assertEqual((sc["ok"], sc["code_name"]), (False, "unsupported"))

    def test_noise_before_json_and_no_json(self):
        s = self.run_session([call(1, "version")], FAKE_CTL_NOISE=1)
        self.assertFalse(s.result(1)["isError"])
        s = self.run_session([call(1, "version")], FAKE_CTL_NOJSON=1, FAKE_CTL_RC=2)
        sc = s.result(1)["structuredContent"]
        self.assertEqual((sc["code"], sc["exit_code"]), ("usage", 2))        # argparse отказал — это параметры
        self.assertTrue(s.result(1)["isError"])
        s = self.run_session([call(1, "version")], FAKE_CTL_NOJSON=1, FAKE_CTL_RC=1)
        self.assertEqual(s.result(1)["structuredContent"]["code"], "transport")

    def test_missing_kaskadctl(self):
        s = self.run_session([call(1, "version")], KASKAD_MCP_CTL=os.path.join(self.tmp, "no-such-ctl"))
        sc = s.result(1)["structuredContent"]
        self.assertEqual(sc["code"], "transport")
        self.assertIn("kaskadctl", sc["error"])

    def test_timeout_kills_the_command(self):
        t0 = time.time()
        s = self.run_session([call(1, "version"), req(2, "ping")], FAKE_CTL_SLEEP=30, KASKAD_MCP_TIMEOUT=1)
        self.assertLess(time.time() - t0, 20)
        sc = s.result(1)["structuredContent"]
        self.assertEqual((sc["code"], sc["code_name"], sc["running"]), ("timeout", "timeout", False))
        self.assertEqual(s.result(2), {})                       # сессия жива

    def test_png_fields_become_images(self):
        b64 = base64.b64encode(PNG).decode()
        reply = json.dumps({"ok": True, "name": "Телефон", "items": [
            {"format": "qr", "title": "QR", "body": ["vpn://SECRETPAYLOAD"], "png_b64": [b64, b64]},
            {"format": "link", "body": "vpn://x", "png_b64": [b64]}, {"format": "conf", "body": "[Interface]"}]})
        s = self.run_session([call(1, "device_issue", {"device": "d-1a2b3c4d"})], FAKE_CTL_REPLY=reply)
        r = s.result(1)
        images = [c for c in r["content"] if c["type"] == "image"]
        self.assertEqual(len(images), 3)
        self.assertEqual(images[0], {"type": "image", "data": b64, "mimeType": "image/png"})
        text = r["content"][0]["text"]
        self.assertNotIn(b64, text)
        self.assertIn("изображение №3", text)
        self.assertNotIn(b64, json.dumps(r["structuredContent"]))     # base64 — только блоками image
        self.assertNotIn("SECRETPAYLOAD", s.audit + s.kaskad_log)   # ответ в аудит не попадает
        self.assertEqual(s.kaskad_log, "")                           # выдача ничего не меняет — в журнал не пишется

    def test_audit_lines(self):
        s = self.run_session([call(1, "users_list"), call(2, "user_add", {"name": "Мама"})])
        recs = s.audit_recs()
        self.assertEqual([r["tool"] for r in recs], ["users_list", "user_add"])
        self.assertEqual(recs[1]["params"], {"name": "Мама"})
        self.assertEqual(recs[1]["command"], "gw user add")
        self.assertEqual(recs[1]["client"], "test_mcp/1")
        self.assertEqual(recs[1]["status"], "success")
        self.assertEqual(stat.S_IMODE(os.stat(s.audit_path).st_mode), 0o600)

    def test_initialize_is_audited_with_the_ssh_port(self):
        """3.3.3: initialize — запись аудита (по ней панель видит «ключ подключён»), с адресом и портом SSH."""
        s = self.run_session([call(1, "users_list")], SSH_CONNECTION="203.0.113.5 51234 10.0.0.1 22")
        init = s.init_recs()
        self.assertEqual(len(init), 1)
        self.assertEqual((init[0]["from"], init[0]["port"], init[0]["status"], init[0]["tool"], init[0]["client"]),
                         ("203.0.113.5", "51234", "ok", "", "test_mcp/1"))
        self.assertEqual(s.audit_recs()[0]["port"], "51234")
        s = self.run_session([call(1, "users_list")])
        self.assertEqual((s.init_recs()[-1]["from"], s.init_recs()[-1]["port"]), ("local", ""))   # один файл аудита

    def test_stdout_is_protocol_only(self):
        s = self.run_session([call(1, "version"), req(2, "tools/list")], FAKE_CTL_NOISE=1, KASKAD_MCP_LOG="debug")
        self.assertEqual(len(s.lines), 3)                       # initialize, call, tools/list — и ничего больше
        self.assertIn("kaskad-mcp", s.stderr)


class StdinKeyTest(McpCase):
    KEY = "vless://11111111-2222-3333-4444-555555555555@198.51.100.20:443?type=tcp#Вена"
    CONF = "[Interface]\nPrivateKey = cHJpdmF0ZWtleXByaXZhdGVrZXlwcml2YXRla2V5MTI=\nAddress = 10.8.1.2/32\n"

    def test_key_goes_to_stdin_never_argv_audit_or_logs(self):
        s = self.run_session([call(1, "exit_preview", {"key": self.KEY}), call(2, "exit_add", {"key": self.CONF})],
                             KASKAD_MCP_LOG="debug")
        self.assertEqual([c["argv"] for c in s.calls], [["gw", "exit-preview", "--json"],
                                                         ["gw", "exit-add", "--public-only", "--json"]])
        self.assertEqual([c["stdin"] for c in s.calls], [self.KEY, self.CONF])
        for text in (s.audit, s.kaskad_log, s.stderr):
            self.assertNotIn("11111111-2222", text)
            self.assertNotIn("cHJpdmF0ZWtle", text)
        recs = s.audit_recs()
        self.assertEqual(recs[0]["params"], {"key": "<%d chars>" % len(self.KEY)})
        self.assertEqual(recs[1]["params"], {"key": "<%d chars>" % len(self.CONF)})

    def test_rejected_key_is_logged_by_size_only(self):
        s = self.run_session([call(1, "exit_add", {"key": self.KEY + "\x00"})])
        self.assertEqual(s.error(1)["code"], -32602)
        self.assertEqual(s.calls, [])
        self.assertNotIn("11111111", s.audit)

    def test_other_tools_get_no_stdin(self):
        s = self.run_session([call(1, "exits_list")])
        self.assertEqual(s.calls[0]["stdin"], "")


class ResourcesTest(McpCase):
    def test_list_and_read(self):
        s = self.run_session([req(1, "resources/list"), req(2, "resources/read", {"uri": "kaskad://scheme"}),
                              req(3, "resources/read", {"uri": "kaskad://guide"}),
                              req(4, "resources/read", {"uri": "kaskad://nope"}), req(5, "resources/templates/list")])
        uris = [r["uri"] for r in s.result(1)["resources"]]
        self.assertEqual(uris, [r["uri"] for r in ACTIONS["resources"]])
        c = s.result(2)["contents"][0]
        self.assertEqual((c["uri"], c["mimeType"]), ("kaskad://scheme", "application/json"))
        self.assertEqual(json.loads(c["text"])["argv"], ["gw", "scheme", "--json"])
        g = s.result(3)["contents"][0]
        self.assertEqual(g["mimeType"], "text/markdown")
        with open(GUIDE, encoding="utf-8") as f:
            self.assertEqual(g["text"], f.read())
        self.assertEqual(s.error(4)["code"], -32002)
        self.assertEqual(s.result(5), {"resourceTemplates": []})

    def test_read_error_is_a_jsonrpc_error(self):
        s = self.run_session([req(1, "resources/read", {"uri": "kaskad://status"})],
                             FAKE_CTL_REPLY='{"ok": false, "error": "нет state.json", "code": 1}', FAKE_CTL_RC=1)
        e = s.error(1)
        self.assertEqual((e["code"], e["message"], e["data"]["code_name"]), (-32603, "нет state.json", "error"))

    def test_resources_never_point_at_secrets_or_changes(self):
        for r in ACTIONS["resources"]:
            if "tool" in r:
                t = TOOLS[r["tool"]]
                self.assertTrue(t["readonly"] and not t.get("secret"), r["uri"])

    def test_missing_guide_is_not_found(self):
        spec = dict(ACTIONS)
        spec["resources"] = [{"uri": "kaskad://guide", "file": "NO_GUIDE.md", "mimeType": "text/markdown"}]
        path = os.path.join(self.tmp, "actions.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(spec, f, ensure_ascii=False)
        s = self.run_session([req(1, "resources/read", {"uri": "kaskad://guide"})], KASKAD_MCP_ACTIONS=path)
        self.assertEqual(s.error(1)["code"], -32002)
        self.assertIn("NO_GUIDE.md", s.error(1)["message"])


class PromptsTest(McpCase):
    def test_list_and_get(self):
        s = self.run_session([
            req(1, "prompts/list"),
            req(2, "prompts/get", {"name": "onboard_person", "arguments": {"name": "Мама", "platform": "android"}}),
            req(3, "prompts/get", {"name": "where_traffic", "arguments": {"who": "Мама"}}),
            req(4, "prompts/get", {"name": "where_traffic", "arguments": {"who": "--x", "target": "ya.ru"}}),
            req(5, "prompts/get", {"name": "nope"}),
            req(6, "prompts/get", {"name": "add_exit", "arguments": {"junk": "ignored"}})])
        names = [p["name"] for p in s.result(1)["prompts"]]
        self.assertEqual(names, ["onboard_person", "add_exit", "where_traffic"])
        text = s.result(2)["messages"][0]["content"]["text"]
        self.assertIn("«Мама»", text)
        self.assertNotIn("(не задан", text)
        self.assertIn("platform=android", text)
        for step in ACTIONS["prompts"][0]["steps"]:
            self.assertIn(step["tool"], text)
        self.assertIn("name=<не задано — спроси человека>", text)   # device_name не передан
        for i in (3, 4, 5):
            self.assertEqual(s.error(i)["code"], -32602, i)
        self.assertNotIn("ignored", s.result(6)["messages"][0]["content"]["text"])

    def test_prompts_use_only_existing_tools(self):
        for p in ACTIONS["prompts"]:
            for step in p["steps"]:
                self.assertIn(step["tool"], TOOLS, p["name"])
            for a in p.get("arguments") or []:
                self.assertIn(a.get("ref"), ACTIONS["param_types"], (p["name"], a))


class ActionToolTest(McpCase):
    """Сводные инструменты (by_action, REV-ns-b14 З-3): действие — параметр action; у действия свои команда, параметры,
    обязательные и confirm; лишнее для действия и чужое действие до kaskadctl не доходят."""

    def test_argv_by_action(self):
        s = self.run_session([
            call(1, "host_score", {}),
            call(2, "host_score", {"action": "history", "days": 7, "skipped": True}),
            call(3, "host_score", {"action": "show", "id": "0123456789"}),
            call(4, "host_score", {"action": "price", "full": True}),
            call(5, "host_bench_schedule", {"action": "add", "time": "03:45", "mode": "quick", "days": "1-5"}),
            call(6, "host_bench_schedule", {"action": "off", "entry": "2", "code": "0123456789"}),
            call(7, "host_bench_schedule", {"action": "rm", "entry": "2", "confirm": "2"}),
            call(8, "host_tariff_set", {"port": 1000, "code": "0123456789"}),
            call(9, "host_set", {"bench_retry": "off"}),
        ])
        argvs = [c["argv"] for c in s.calls]
        self.assertEqual(argvs, [
            ["host", "score", "--json"],
            ["host", "bench", "history", "--days=7", "--skipped", "--limit=50", "--json"],
            ["host", "bench", "show", "0123456789", "--json"],
            ["host", "bench", "--full", "--dry", "--json"],
            ["host", "bench", "schedule", "add", "--time=03:45", "--mode=quick", "--days=1-5", "--by-mcp", "--json"],
            ["host", "bench", "schedule", "off", "2", "--code=0123456789", "--by-mcp", "--json"],
            ["host", "bench", "schedule", "rm", "2", "--by-mcp", "--json"],
            ["host", "tariff", "set", "--port=1000", "--code=0123456789", "--by-mcp", "--json"],
            ["host", "set", "--bench-retry=off", "--by-mcp", "--json"]])
        recs = [r for r in s.audit_recs() if r.get("tool") == "host_bench_schedule"]
        self.assertEqual([r["command"] for r in recs][-1], "host bench schedule rm")

    def test_audit_says_a_read_as_a_read(self):
        """REV-ns-b14-3 П-3: чтение в аудите — с постоянными ключами (``--dry``): предпросмотр тарифа и цена проверки
        не выглядят как запись и запуск; запись — как была (без --by-mcp)."""
        s = self.run_session([
            call(1, "host_tariff", {"action": "preview", "port": 1000}),
            call(2, "host_score", {"action": "price"}),
            call(3, "host_tariff_set", {"port": 1000, "code": "0123456789"}),
            call(4, "host_tariff", {"action": "preview", "port": "x"}),            # отклонённое — так же
        ])
        cmds = [(r["tool"], r["command"]) for r in s.audit_recs() if r.get("kind") == "call"]
        self.assertEqual(cmds, [("host_tariff", "host tariff set --dry"), ("host_score", "host bench --dry"),
                                ("host_tariff_set", "host tariff set"), ("host_tariff", "host tariff set --dry")])

    def test_old_action_set_points_to_its_tool(self):
        """REV-ns-b14-3 П-2: host_tariff(action=set) — отказ с подсказкой host_tariff_set; чужое действие — без."""
        s = self.run_session([call(1, "host_tariff", {"action": "set", "port": 1000, "code": "0123456789"}),
                              call(2, "host_tariff", {"action": "drop"})])
        self.assertIn("host_tariff_set", s.error(1)["message"])
        self.assertEqual(s.error(1)["data"]["tool"], "host_tariff_set")
        self.assertNotIn("host_tariff_", s.error(2)["message"])
        self.assertNotIn("tool", s.error(2)["data"])
        self.assertEqual(s.calls, [])

    def test_wrong_action_or_params_never_reach_kaskadctl(self):
        bad = [("host_bench_schedule", {"entry": "1"}),                               # нет action и нет по умолчанию
               ("host_bench_schedule", {"action": "drop", "entry": "1"}),
               ("host_bench_schedule", {"action": 1}),
               ("host_bench_schedule", {"action": "add", "entry": "1", "time": "03:45", "mode": "quick",
                                        "days": "all"}),
               ("host_bench_schedule", {"action": "add", "time": "03:45"}),            # mode и days обязательны у add
               ("host_bench_schedule", {"action": "on", "entry": "1", "confirm": "1"}),  # confirm — только у rm
               ("host_bench_schedule", {"action": "rm", "entry": "1"}),
               ("host_bench_schedule", {"action": "rm", "entry": "1", "confirm": "2"}),
               ("host_score", {"id": "0123456789"}),                                   # score — без id
               ("host_score", {"action": "show"}),
               ("host_tariff", {"action": "show", "port": 1000}),
               ("host_tariff", {"action": "set", "port": 1000, "code": "0123456789"}),   # set — свой инструмент
               ("host_tariff_set", {"port": 1000}),
               ("host_score", {"action": "history", "limit": 500})]
        s = self.run_session([call(i, t, a) for i, (t, a) in enumerate(bad)])
        for i, (t, a) in enumerate(bad):
            self.assertEqual(s.error(i)["code"], -32602, (t, a))
        self.assertEqual(s.calls, [])
        self.assertIn("action=add", s.error(3)["message"])
        self.assertEqual(s.error(3)["data"]["action"], "add")
        self.assertEqual(s.error(1)["data"]["enum"], ["add", "on", "off", "rm"])

    def test_list_says_the_actions(self):
        s = self.run_session([req(1, "tools/list")])
        tools = dict((t["name"], t) for t in s.result(1)["tools"])
        t = tools["host_bench_schedule"]
        self.assertEqual(t["inputSchema"]["required"], ["action"])
        self.assertIn("add (time*, mode*, days*, no_quiet, off, monthly, code)", t["description"])
        self.assertIn("action=rm необратимо: нужен confirm = значение «entry»", t["description"])
        self.assertTrue(t["annotations"]["destructiveHint"])
        self.assertFalse(t["annotations"].get("readOnlyHint", False))
        t = tools["host_score"]
        self.assertEqual(t["inputSchema"]["properties"]["action"]["default"], "score")
        self.assertTrue(t["annotations"]["readOnlyHint"])
        self.assertNotIn("confirm", t["inputSchema"]["properties"])
        t = tools["host_tariff"]                    # REV-ns-b14-2 П-3: show и preview — только чтение, set — отдельно
        self.assertIn("preview (port, traffic, clear)", t["description"])          # NS-D34: only the port and the traffic
        self.assertNotIn("set (", t["description"])
        self.assertTrue(t["annotations"]["readOnlyHint"])
        self.assertNotIn("idempotentHint", t["annotations"])                 # 3.3.7.4 Ш2а: по MCP — по умолчанию
        t = tools["host_tariff_set"]
        self.assertNotIn("readOnlyHint", t["annotations"])                   # false — умолчание MCP, не отдаём
        self.assertNotIn("idempotentHint", t["annotations"])
        self.assertEqual(t["inputSchema"]["required"], ["code"])
        self.assertFalse(tools["host_set"]["annotations"]["destructiveHint"])        # как в 3.3.7: код — на сервере

    def test_readonly_of_tools_and_actions(self):
        from kaskad.mcp.server import Catalog, Log
        cat = Catalog(ACTIONS, "ru", Log("none", io.StringIO()), MCP_DIR)
        t = cat.tools["host_bench_schedule"]
        self.assertFalse(t.readonly)
        self.assertTrue(cat.tools["host_tariff"].readonly)                  # REV-ns-b14-2 П-3
        self.assertTrue(cat.tools["host_tariff"].variant("show").readonly)
        self.assertIsNone(cat.tools["host_tariff"].variant("set"))
        self.assertEqual(cat.tools["host_score"].variant(None).name, "score")


# ── каталог против kaskadctl и правила безопасности ──────────────────────────────────────────────────────

class CatalogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = mcp.load_catalog(ACTIONS_JSON, "ru", mcp.Log("none", io.StringIO()), MCP_DIR)

    def test_every_tool_and_flag_exists_in_kaskadctl_and_every_command_is_decided(self):
        self.assertEqual(mcp.parser_problems(self.catalog), [])

    def test_parser_problems_catch_mistakes(self):
        spec = json.loads(json.dumps(ACTIONS))
        spec["actions"]["version"]["cmd"] = ["versio"]
        spec["actions"]["user_add"]["params"]["route"]["flag"] = "--routes"
        spec["actions"]["device_offers"]["params"]["platform"].pop("required")
        spec["hidden"]["no such"] = {"ru": "x", "en": "x"}
        spec["hidden"].pop("fwd flush")
        spec["actions"]["host_bench_schedule"]["by_action"]["rm"]["cmd"] = ["host", "bench", "schedule", "remove"]
        spec["actions"]["host_score"]["by_action"]["show"]["required"] = []
        cat = mcp.Catalog(spec, "ru", mcp.Log("none", io.StringIO()), MCP_DIR)
        probs = "\n".join(mcp.parser_problems(cat))
        for needle in ("versio", "--routes", "--platform обязателен", "no such", "«fwd flush»", "«version»",
                       "host_bench_schedule action=rm: нет команды kaskadctl «host bench schedule remove»",
                       "host_score action=show: id обязателен у kaskadctl"):
            self.assertIn(needle, probs)

    def test_hidden_reasons_are_bilingual(self):
        for key, why in ACTIONS["hidden"].items():
            self.assertTrue(why.get("ru") and why.get("en"), key)

    def test_destructive_tools_are_the_irreversible_ones(self):
        irreversible = re.compile(r"^(del|rm|uninstall|purge|remove|revoke|reset|rotate|exit-del)$")
        # меняют то, что уже выдано людям: старые настройки перестают подключаться, или обновляют саму программу
        breaks_issued = (["gw", "inbound", "port"], ["gw", "host"], ["site", "use"], ["gw", "disable"], ["update", "run"],
                         ["gw", "vless", "mask"], ["gw", "vless", "sync"], ["gw", "hy2", "set"],
                         ["gw", "addresses", "in"])     # REV-mip-s34 З-2: devices on the other addresses lose the way
        for name, a in LEAVES:
            last = a["cmd"][-1]
            args = a.get("args") or []
            must = bool(irreversible.match(last)) or "--yes" in args or "--clear" in args or a["cmd"] in breaks_issued
            if a["cmd"] == ["gw", "inbound", "install"] and "--new-only" not in args:
                must = True                                         # переустановка
            if "--dry-run" in args:
                must = False                                        # site_use_preview
            if a["cmd"] == ["host", "bench"]:
                must = False    # --yes — согласие с ценой проверки (трафик), не необратимость; спрашивает клиент (ask_human)
            if a["cmd"] == ["gw", "exit-move"] and "--confirm-role" not in args:
                must = False        # KT3-9: the CLI asks for any move (--yes answers it); for MCP a move inside one role
                #                     stays operational (T10 §7.8: exit_move without confirm, exit_move_role with it)
            # согласие человека, хоть и не необратимо: закрепить отпечаток сертификата (TOFU, SE §7.8) и тест скорости
            # сверх суточного лимита (T10, REV-s1-t10-sec №2) — клиент MCP спросит человека, confirm обязателен
            if a["cmd"] == ["gw", "exit-add"] and "--pin-by=mcp" in args or \
                    a["cmd"] == ["gw", "exit-speedtest"] and "--force" in args:
                must = True
            self.assertEqual(bool(a["destructive"]), must, name)
            if a["destructive"]:
                self.assertFalse(a["readonly"], name)

    def test_secrets(self):
        self.assertEqual(SECRET, ["device_issue", "page_on", "page_rotate", "page_sub_on", "page_sub_rotate",
                                  "page_sub_url", "page_url"])
        for name, a in LEAVES:
            if a["cmd"] in (["gw", "issue"], ["page", "url"], ["page", "on"], ["page", "rotate"], ["page", "sub", "url"],
                            ["page", "sub", "on"], ["page", "sub", "rotate"]):
                self.assertTrue(a.get("secret"), name)
        for key in ("panel password", "panel link", "site panel-url", "gw awg export", "page list", "mcp key add",
                    "mcp key del"):
            used = [n for n, a in LEAVES if " ".join(a["cmd"]) == key]
            if key == "page list":
                self.assertNotIn("urls", TOOLS["pages_list"]["params"])
            else:
                self.assertEqual(used, [], key)
                self.assertIn(key, ACTIONS["hidden"])

    def test_every_flag_says_what_it_does(self):
        for name, a in TOOLS.items():
            for pname, p in a["params"].items():
                t = ACTIONS["param_types"][p["ref"]]
                self.assertTrue(t["description"]["ru"] and t["description"]["en"], (name, pname))
                if t["type"] == "boolean":
                    self.assertNotIn("Включить ключ", t["description"]["ru"], (name, pname))
        for name, pname in (("user_delete", "with_devices"), ("gateway_purge", "with_users")):
            self.assertTrue(TOOLS[name]["params"][pname].get("danger"), name)

    def test_long_changes_outlive_apt(self):
        from kaskad import aptlock
        for name in ("gateway_enable", "inbound_install", "inbound_reinstall", "exit_add", "site_mode",
                     "site_template_set", "awg_engine_set", "site_cert_issue", "rules_update"):
            self.assertGreater(TOOLS[name]["timeout"], aptlock.INSTALL_WAIT + aptlock.RUN_TIMEOUT, name)

    def test_install_sh_removes_exactly_the_lines_keys_py_writes(self):
        from kaskad.mcp import keys
        with open(os.path.join(ROOT, "install.sh"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("K3_MCP_KEY_PREFIX='%s '\n" % keys.OPTIONS, text)
        self.assertIn(keys.MARK, text)

    def test_keys_only_through_stdin(self):
        for name, a in TOOLS.items():
            for pname, p in a["params"].items():
                if p.get("ref") == "exit_key":
                    self.assertTrue(p.get("stdin"), name)

    def test_positional_strings_cannot_start_with_a_dash(self):
        for name, a in TOOLS.items():
            for pname, p in a["params"].items():
                t = ACTIONS["param_types"][p["ref"]]
                if p.get("pos") and t["type"] == "string" and not t.get("enum"):
                    rx = re.compile(t["pattern"])
                    self.assertIsNone(rx.search("--force"), (name, pname))

    def test_timeouts_cover_the_core(self):
        from kaskad.gw import ops
        self.assertGreater(TOOLS["exit_add"]["timeout"], ops.EXIT_ADD_TIMEOUT)
        self.assertGreater(TOOLS["exit_delete"]["timeout"], ops.EXIT_DEL_TIMEOUT)
        from kaskad import cli_update
        self.assertGreater(TOOLS["update_run"]["timeout"], cli_update.RUN_TIMEOUT - 1)


class DocsTest(McpCase):
    def test_guide_mentions_everything(self):
        with open(GUIDE, encoding="utf-8") as f:
            guide = f.read()
        for name in TOOLS:
            self.assertIn("`%s`" % name, guide, name)
        for r in ACTIONS["resources"]:
            self.assertIn(r["uri"], guide)
        for p in ACTIONS["prompts"]:
            self.assertIn("`%s`" % p["name"], guide)
        for code in ("locked", "conflict", "unsupported", "rolled_back", "transport", "timeout"):
            self.assertIn(code, guide)

    def test_spec_lists_every_tool_and_readme_has_the_section(self):
        with open(SPEC_DOC, encoding="utf-8") as f:
            spec = f.read()
        for name in TOOLS:
            self.assertIn("`%s`" % name, spec, name)
        with open(README, encoding="utf-8") as f:
            readme = f.read()
        self.assertIn("## MCP-сервер", readme)
        self.assertIn("claude mcp add kaskad -- ssh root@", readme)
        self.assertIn("claude_desktop_config.json", readme)

    def test_check_and_tools_md(self):
        exe = os.path.join(self.tmp, "ctl")
        with open(exe, "w") as f:
            f.write("#!/bin/sh\nexit 0\n")
        os.chmod(exe, 0o755)
        env = self.env(KASKAD_MCP_CTL=exe)
        p = subprocess.run([sys.executable, "-m", "kaskad.mcp", "--check"], stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, env=env, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr.decode())
        self.assertIn("инструменты  : %d" % len(TOOLS), p.stderr.decode())
        self.assertEqual(p.stdout, b"")
        p = subprocess.run([sys.executable, "-m", "kaskad.mcp", "--check"], stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, env=self.env(KASKAD_MCP_CTL=os.path.join(self.tmp, "none")), timeout=60)
        self.assertEqual(p.returncode, 1)
        p = subprocess.run([sys.executable, "-m", "kaskad.mcp", "--tools-md"], stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, env=env, timeout=60)
        md = p.stdout.decode()
        for name in TOOLS:
            self.assertIn("| `%s` |" % name, md)
        p = subprocess.run([sys.executable, "-m", "kaskad.mcp", "--version"], stdout=subprocess.PIPE, env=env, timeout=30)
        self.assertIn(__version__, p.stdout.decode())
        p = subprocess.run([sys.executable, "-m", "kaskad.mcp", "--bogus"], stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, env=env, timeout=30)
        self.assertEqual(p.returncode, 2)


class RealKaskadctlTest(McpCase):
    """Сессия с настоящим kaskadctl этого репозитория (KASKAD_ROOT — временный каталог): каталог годится живой команде."""

    def test_people_through_the_real_core(self):
        env = dict(KASKAD_MCP_CTL="")
        s = self.run_session([
            call(1, "version"), call(2, "user_add", {"name": "Мама"}), call(3, "users_list"), call(4, "scheme"),
            call(5, "user_delete", {"user": "Мама", "confirm": "Мама"}), call(6, "users_list"),
            call(7, "user_delete", {"user": "Мама", "confirm": "Мама"}), call(8, "exit_add", {"key": "  "}),
            call(9, "forwards_list")], timeout=120, **env)
        self.assertEqual(s.result(1)["structuredContent"]["version"], __version__)
        self.assertFalse(s.result(2)["isError"], s.result(2))
        self.assertEqual([u["name"] for u in s.result(3)["structuredContent"]["users"]], ["Мама"])
        self.assertEqual([u["name"] for u in s.result(4)["structuredContent"]["people"]], ["Мама"])
        self.assertFalse(s.result(5)["isError"])
        self.assertEqual(s.result(6)["structuredContent"]["users"], [])
        r = s.result(7)
        self.assertTrue(r["isError"])
        self.assertEqual(r["structuredContent"]["code"], 1)
        self.assertIn("Пользователя Мама нет", r["structuredContent"]["error"])
        r = s.result(8)                                           # пустой ключ: отказ ядра, скрипт не зовётся
        self.assertEqual(r["structuredContent"]["code_name"], "usage")
        r = s.result(9)                                           # движок legacy: код 5
        self.assertEqual(r["structuredContent"]["code_name"], "unsupported")


if __name__ == "__main__":
    unittest.main()
