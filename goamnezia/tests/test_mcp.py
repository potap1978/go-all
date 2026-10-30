# -*- coding: utf-8 -*-
"""Тесты MCP-сервера goamnezia-mcp/server.py.

Сервер запускается настоящим подпроцессом (как его запустит ssh у клиента), мост подменён
заглушкой tests/fixtures/fake_action.sh. Каждый тест — отдельная сессия: все сообщения
пишутся в stdin разом, ответы читаются из stdout после завершения. Так проверяется и то,
что в stdout нет ничего, кроме JSON-RPC (иначе разбор ответов упадёт).

Запуск: python3 -m unittest tests/test_mcp.py
"""

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(ROOT, "goamnezia-mcp", "server.py")
README = os.path.join(ROOT, "goamnezia-mcp", "README.md")
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "fake_action.sh")
ACTIONS_JSON = os.path.join(ROOT, "lib", "actions.json")
PROTOCOL = "2025-06-18"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

with open(ACTIONS_JSON, "r", encoding="utf-8") as _fh:
    ACTIONS = json.load(_fh)

# Действия с "mcp": null (доступы агентов, HTTP-транспорт) сервер публиковать не должен (контракт §5):
# всё, что сверяется с tools/list, README и гайдом, берётся из PUBLISHED, а HIDDEN проверяется отдельно.
PUBLISHED = dict((cli, adef) for cli, adef in ACTIONS["actions"].items() if adef.get("mcp"))
HIDDEN = [cli for cli, adef in ACTIONS["actions"].items() if not adef.get("mcp")]
# Параметры только для людей ("mcp": false у параметра): агенту их нет ни в схеме, ни в вызове. Белый список — явный:
# новый такой параметр добавляют сюда осознанно, а снятая метка у sub вернула бы агенту перевыпуск подписки (R-2),
# у keys — перевыпуск ключей пользователя при отзыве страницы (B3-3).
HUMAN_ONLY_PARAMS = {("page-revoke", "sub"), ("page-revoke", "keys")}


def mcp_params(adef):
    """Параметры действия, которые видит агент (без "mcp": false)."""
    return dict((p, d) for p, d in (adef.get("params") or {}).items() if (d or {}).get("mcp") is not False)


GUIDE_URI = "goamnezia://guide"
GUIDE = os.path.join(ROOT, "goamnezia-mcp", "AGENT_GUIDE.md")


def write_spec(spec):
    """Свой actions.json во временном каталоге → путь; каталог удаляет вызывающий (shutil.rmtree)."""
    tmp = tempfile.mkdtemp(prefix="goamnezia-mcp-spec-")
    path = os.path.join(tmp, "actions.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(spec, fh, ensure_ascii=False)
    return path


def init_messages(protocol=PROTOCOL):
    params = {"capabilities": {}, "clientInfo": {"name": "test_mcp", "version": "1"}}
    if protocol is not None:
        params["protocolVersion"] = protocol
    return [
        {"jsonrpc": "2.0", "id": "init", "method": "initialize", "params": params},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
    ]


def req(msg_id, method, params=None):
    message = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def call(msg_id, tool, arguments=None):
    return req(msg_id, "tools/call", {"name": tool, "arguments": arguments if arguments is not None else {}})


class Result(object):
    def __init__(self, proc, elapsed, audit, args):
        self.proc = proc
        self.elapsed = elapsed
        self.stdout = proc.stdout.decode("utf-8")
        self.stderr = proc.stderr.decode("utf-8", "replace")
        self.audit = audit
        self.args = args          # список argv моста (список списков) по порядку вызовов
        self.responses = []
        for line in self.stdout.splitlines():
            if line.strip():
                self.responses.append(json.loads(line))   # мусор в stdout → тест падает здесь
        self.by_id = {}
        for r in self.responses:
            if isinstance(r, dict) and "id" in r:
                self.by_id[r["id"]] = r


def run(messages, env=None, init=True, config=True, timeout=40, server=SERVER):
    """Прогнать сессию: список сообщений (dict или сырая строка) → Result.

    server — путь, которым запускается сервер (по умолчанию сам server.py; в тесте симлинка —
    ссылка на него, как /usr/local/bin/goamnezia-mcp на боевой машине).
    """
    tmp = tempfile.mkdtemp(prefix="goamnezia-mcp-test-")
    try:
        if config:
            with open(os.path.join(tmp, "config.json"), "w", encoding="utf-8") as fh:
                json.dump({"version": "9.9.9-test", "lang": "ru"}, fh)
        audit_path = os.path.join(tmp, "audit.log")
        args_path = os.path.join(tmp, "args.tsv")
        full_env = dict(os.environ)
        full_env.update({
            "GOAMNEZIA_DIR": tmp,
            "GOAMNEZIA_ACTION_BIN": FIXTURE,
            "GOAMNEZIA_ACTIONS_JSON": ACTIONS_JSON,
            "GOAMNEZIA_AUDIT_LOG": audit_path,
            "GOAMNEZIA_MCP_LOG": "warn",
            "FAKE_ACTION_ARGS_FILE": args_path,
        })
        for key in ("GOAMNEZIA_LANG", "GOAMNEZIA_MCP_TIMEOUT", "FAKE_ACTION_SLEEP", "FAKE_ACTION_STATUS", "FAKE_ACTION_NO_JSON"):
            full_env.pop(key, None)
        full_env.update(env or {})
        seq = (init_messages() if init else []) + list(messages)
        payload = "\n".join(m if isinstance(m, str) else json.dumps(m, ensure_ascii=False) for m in seq) + "\n"
        started = time.time()
        proc = subprocess.run([sys.executable, server], input=payload.encode("utf-8"),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=full_env, timeout=timeout)
        elapsed = time.time() - started
        audit = ""
        if os.path.exists(audit_path):
            with open(audit_path, "r", encoding="utf-8") as fh:
                audit = fh.read()
        args = []
        if os.path.exists(args_path):
            with open(args_path, "r", encoding="utf-8") as fh:
                args = [line.rstrip("\n").rstrip("\t").split("\t") for line in fh if line.strip()]
        return Result(proc, elapsed, audit, args)
    finally:
        for name in os.listdir(tmp):
            os.unlink(os.path.join(tmp, name))
        os.rmdir(tmp)


class InitializeTests(unittest.TestCase):
    def test_initialize_current_version(self):
        res = run([])
        self.assertEqual(res.proc.returncode, 0)
        init = res.by_id["init"]["result"]
        self.assertEqual(init["protocolVersion"], PROTOCOL)
        self.assertEqual(init["capabilities"]["tools"], {"listChanged": False})
        self.assertEqual(init["capabilities"]["resources"], {"subscribe": False, "listChanged": False})
        self.assertEqual(init["capabilities"]["prompts"], {"listChanged": False})
        self.assertEqual(init["serverInfo"], {"name": "goamnezia", "version": "9.9.9-test"})
        # Инструкции двуязычные и перечисляют разрушительные инструменты из actions.json
        self.assertIn("MCP-сервер", init["instructions"])
        self.assertIn("MCP server", init["instructions"])
        for cli, adef in PUBLISHED.items():
            if adef["destructive"]:
                self.assertIn(adef["mcp"], init["instructions"])
        self.assertIn("confirm", init["instructions"])
        # Инструкции отсылают к гайду, называют инструменты «с чего начать» (действия за ресурсами)
        # и подсказки-сценарии; скрытые действия в них не упоминаются
        self.assertIn(GUIDE_URI, init["instructions"])
        self.assertIn("text/markdown", init["instructions"])
        for rdef in ACTIONS["resources"]:
            if rdef.get("action"):
                self.assertIn(PUBLISHED[rdef["action"]]["mcp"], init["instructions"])
        for pdef in ACTIONS["prompts"]:
            self.assertIn(pdef["name"], init["instructions"])
        for cli in HIDDEN:
            self.assertNotIn(cli.replace("-", "_"), init["instructions"])

    def test_initialize_old_protocol_versions(self):
        # Поддерживаемую старую версию сервер повторяет (правило спецификации), незнакомую — заменяет своей
        for requested, expected in (("2025-03-26", "2025-03-26"), ("2024-11-05", "2024-11-05"),
                                    ("1.0", PROTOCOL), (None, PROTOCOL)):
            res = run(init_messages(requested) + [req(1, "ping")], init=False)
            self.assertEqual(res.by_id["init"]["result"]["protocolVersion"], expected, requested)
            self.assertEqual(res.by_id[1]["result"], {})

    def test_version_without_config_is_zero(self):
        res = run([], config=False)
        self.assertEqual(res.by_id["init"]["result"]["serverInfo"]["version"], "0.0.0")

    def test_notifications_are_silent_and_ping_works(self):
        res = run([req(1, "ping"), {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 1}},
                   {"jsonrpc": "2.0", "method": "notifications/unknown_thing"}, req(2, "ping")])
        self.assertEqual(sorted(res.by_id.keys(), key=str), sorted(["init", 1, 2], key=str))
        self.assertEqual(len(res.responses), 3)
        self.assertEqual(res.by_id[2]["result"], {})


class ToolsListTests(unittest.TestCase):
    def test_tools_list_matches_actions_json(self):
        res = run([req(1, "tools/list")])
        tools = res.by_id[1]["result"]["tools"]
        expected_names = [a["mcp"] for a in PUBLISHED.values()]
        self.assertEqual([t["name"] for t in tools], expected_names)
        by_name = dict((t["name"], t) for t in tools)
        for cli, adef in PUBLISHED.items():
            tool = by_name[adef["mcp"]]
            schema = tool["inputSchema"]
            self.assertEqual(schema["type"], "object")
            self.assertFalse(schema["additionalProperties"])
            params = mcp_params(adef)
            required = set(p for p, d in params.items() if d.get("required"))
            if adef["destructive"]:
                required.add("confirm")
                self.assertEqual(schema["properties"]["confirm"]["type"], "string")
            else:
                self.assertNotIn("confirm", schema["properties"])
            self.assertEqual(set(schema.get("required", [])), required, adef["mcp"])
            # confirm, объявленный в каталоге (users-del-bulk: число людей), — обычный параметр действия
            self.assertEqual(set(schema["properties"].keys()) - ({"confirm"} - set(params)), set(params.keys()))
            for pname, pdef in params.items():
                ptype = ACTIONS["param_types"][pdef["ref"]]
                prop = schema["properties"][pname]
                self.assertEqual(prop["type"], ptype["type"])
                for key in ("pattern", "enum", "minimum", "maximum"):
                    if key in ptype:
                        self.assertEqual(prop[key], ptype[key], (pname, key))
                if "default" in pdef:
                    self.assertEqual(prop["default"], pdef["default"])
                self.assertIn(" / ", prop["description"])       # RU / EN
            # Идемпотентность у читающего — правда по определению; у изменяющих
            # стоит False честно (DEFECTS §5.5).
            self.assertEqual(tool["annotations"], {
                "readOnlyHint": adef["readonly"], "destructiveHint": adef["destructive"],
                "idempotentHint": bool(adef["readonly"]), "openWorldHint": False})
            self.assertIn(adef["description"]["ru"], tool["description"])
            self.assertIn(adef["description"]["en"], tool["description"])

    def test_human_only_params_are_the_whitelist_and_never_reach_the_bridge(self):
        """R-2: page_revoke без sub — перевыпуск подписки агентам закрыт (sub-rotate — mcp: null). sub в вызове — -32602
        до моста, отзыв без него уходит мосту без --sub (ядро берёт off)."""
        marked = set((cli, p) for cli, adef in ACTIONS["actions"].items()
                     for p, d in (adef.get("params") or {}).items() if (d or {}).get("mcp") is False)
        self.assertEqual(marked, HUMAN_ONLY_PARAMS)
        self.assertIsNone(ACTIONS["actions"]["sub-rotate"].get("mcp"))
        res = run([req(1, "tools/list"),
                   call(2, "page_revoke", {"id": "p-abcd12", "sub": "on"}),
                   call(3, "page_revoke", {"id": "p-abcd12", "sub": "off"}),
                   call(4, "page_revoke", {"id": "p-abcd12"}),
                   call(5, "page_revoke", {"id": "p-abcd12", "keys": "on"})])
        by_name = dict((t["name"], t) for t in res.by_id[1]["result"]["tools"])
        for cli, pname in HUMAN_ONLY_PARAMS:
            tool = by_name[ACTIONS["actions"][cli]["mcp"]]
            self.assertNotIn(pname, tool["inputSchema"]["properties"], cli)
            self.assertNotIn(pname, tool["inputSchema"].get("required", []), cli)
        for i in (2, 3):
            err = res.by_id[i]["error"]
            self.assertEqual(err["code"], -32602)
            self.assertEqual(err["data"]["unknown"], ["sub"])
        self.assertEqual(res.by_id[5]["error"]["code"], -32602)         # B3-3: перевыпуск ключей агенту закрыт
        self.assertEqual(res.by_id[5]["error"]["data"]["unknown"], ["keys"])
        self.assertFalse(res.by_id[4]["result"]["isError"])
        self.assertEqual(res.args, [["--action=page-revoke", "--json", "--id=p-abcd12"]])
        with open(README, "r", encoding="utf-8") as fh:
            row = [l for l in fh.read().splitlines() if l.startswith("| `page_revoke` ")]
        self.assertEqual(len(row), 1)
        self.assertNotIn("`sub`", row[0])
        self.assertNotIn("sub=on", row[0])
        self.assertNotIn("`keys`", row[0])

    def test_hidden_actions_are_not_published(self):
        # В actions.json есть действия с "mcp": null — иначе проверять нечего
        self.assertTrue(HIDDEN)
        hidden_names = [cli.replace("-", "_") for cli in HIDDEN]
        res = run([req(1, "tools/list"), req(2, "prompts/get", {"name": "onboard_user", "arguments": {"name": "anna"}})]
                  + [call(10 + i, name, {}) for i, name in enumerate(hidden_names)])
        published = [t["name"] for t in res.by_id[1]["result"]["tools"]]
        self.assertEqual(len(published), len(PUBLISHED))
        for i, name in enumerate(hidden_names):
            self.assertNotIn(name, published)
            self.assertEqual(res.by_id[10 + i]["error"]["code"], -32602, name)   # как неизвестный инструмент
            self.assertNotIn(name, res.by_id[2]["result"]["messages"][0]["content"]["text"])
        self.assertEqual(res.args, [])            # мост ни разу не вызывался

    def test_missing_mcp_field_derives_name_but_null_hides(self):
        # Переносимость: без поля mcp имя выводится из cli-имени; null и пустая строка — скрывают
        spec = {"schema": 1, "param_types": {},
                "actions": {"foo-bar": {"readonly": True, "destructive": False, "params": {}, "description": {"ru": "р", "en": "e"}},
                            "hidden-null": {"mcp": None, "readonly": True, "destructive": False, "params": {}, "description": {"ru": "р", "en": "e"}},
                            "hidden-empty": {"mcp": "", "readonly": True, "destructive": False, "params": {}, "description": {"ru": "р", "en": "e"}}},
                "resources": [], "prompts": []}
        path = write_spec(spec)
        try:
            res = run([req(1, "tools/list"), call(2, "foo_bar"), call(3, "hidden_null"), call(4, "hidden_empty")],
                      env={"GOAMNEZIA_ACTIONS_JSON": path})
        finally:
            shutil.rmtree(os.path.dirname(path))
        self.assertEqual([t["name"] for t in res.by_id[1]["result"]["tools"]], ["foo_bar"])
        self.assertFalse(res.by_id[2]["result"]["isError"])
        self.assertEqual(res.by_id[3]["error"]["code"], -32602)
        self.assertEqual(res.by_id[4]["error"]["code"], -32602)
        self.assertEqual(res.args, [["--action=foo-bar", "--json"]])


class ToolsCallTests(unittest.TestCase):
    def test_readonly_call(self):
        res = run([call(1, "users_list")])
        result = res.by_id[1]["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"]["status"], "success")
        self.assertEqual(result["content"][0]["type"], "text")
        self.assertEqual(json.loads(result["content"][0]["text"]), result["structuredContent"])
        self.assertEqual(res.args, [["--action=user-list", "--json"]])

    def test_destructive_without_confirm_is_rejected(self):
        res = run([call(1, "user_delete", {"name": "anna"})])
        err = res.by_id[1]["error"]
        self.assertEqual(err["code"], -32602)
        self.assertIn("confirm", err["data"]["missing"])
        self.assertEqual(res.args, [])            # до моста дело не дошло
        self.assertEqual(audit_rec(res.audit)["status"], "rejected")

    def test_destructive_with_wrong_confirm_is_rejected(self):
        res = run([call(1, "user_delete", {"name": "anna", "confirm": "bob"}),
                   call(2, "user_delete", {"name": "anna", "confirm": "Anna"})])
        self.assertEqual(res.by_id[1]["error"]["code"], -32602)
        self.assertEqual(res.by_id[2]["error"]["code"], -32602)
        self.assertEqual(res.args, [])

    def test_destructive_with_confirm_succeeds(self):
        res = run([call(1, "user_delete", {"name": "anna", "confirm": "anna"}),
                   call(2, "upstream_update", {"profile": "awg2", "tag": "3.1.1", "confirm": "awg2"})])
        self.assertFalse(res.by_id[1]["result"]["isError"])
        self.assertFalse(res.by_id[2]["result"]["isError"])
        # confirm — параметр MCP, мосту он не передаётся
        self.assertEqual(res.args[0], ["--action=user-del", "--json", "--name=anna"])
        self.assertEqual(res.args[1], ["--action=upstream-update", "--json", "--profile=awg2", "--tag=3.1.1"])

    def test_bulk_delete_own_confirm_is_the_number_and_reaches_the_bridge(self):
        """users_del_bulk (просьба владельца 03.10): confirm — число людей из предпросмотра, объявлен в каталоге и уходит
        мосту — сверяет его ядро в момент удаления; без него, не числом 1–100 или числом JSON — -32602 до моста."""
        res = run([call(1, "users_del_bulk", {"state": "expired", "confirm": "3"}),
                   call(2, "users_del_bulk", {"prefix": "office"}),
                   call(3, "users_del_bulk", {"prefix": "office", "confirm": "0"}),
                   call(4, "users_del_bulk", {"prefix": "office", "confirm": "101"}),
                   call(5, "users_del_bulk", {"prefix": "office", "confirm": 3}),
                   call(6, "users_del_bulk", {"names": "anna,bob", "confirm": "2", "extra": 1}),
                   call(7, "users_del_bulk_preview", {"names": "anna,bob"})])
        self.assertFalse(res.by_id[1]["result"]["isError"])
        self.assertIn("confirm", res.by_id[2]["error"]["data"]["missing"])
        for i in (3, 4, 5, 6):
            self.assertEqual(res.by_id[i]["error"]["code"], -32602, i)
        self.assertFalse(res.by_id[7]["result"]["isError"])
        self.assertEqual(res.args, [["--action=users-del-bulk", "--json", "--confirm=3", "--state=expired"],
                                    ["--action=users-del-bulk-preview", "--json", "--names=anna,bob"]])

    def test_bulk_delete_schema_keeps_its_own_confirm(self):
        res = run([req(1, "tools/list")])
        tools = dict((t["name"], t) for t in res.by_id[1]["result"]["tools"])
        schema = tools["users_del_bulk"]["inputSchema"]
        self.assertEqual(schema["properties"]["confirm"]["pattern"], ACTIONS["param_types"]["bulk_confirm"]["pattern"])
        self.assertEqual(schema["required"].count("confirm"), 1)
        self.assertTrue(tools["users_del_bulk"]["annotations"]["destructiveHint"])
        self.assertIn("сверяет его в момент исполнения", tools["users_del_bulk"]["description"])
        self.assertTrue(tools["users_del_bulk_preview"]["annotations"]["readOnlyHint"])
        self.assertNotIn("confirm", tools["users_del_bulk_preview"]["inputSchema"]["properties"])

    def test_invalid_arguments_return_invalid_params(self):
        cases = [
            call(1, "user_show", {"name": "bad name!"}),                # регекс
            call(2, "user_show", {"name": "anna\n"}),                   # перевод строки
            call(3, "logs_tail", {"component": "kernel"}),              # enum
            call(4, "logs_tail", {"component": "bot", "lines": 5}),     # minimum
            call(5, "logs_tail", {"component": "bot", "lines": "50"}),  # тип
            call(6, "user_show", {"name": "anna", "extra": 1}),         # неизвестный параметр
            call(7, "user_show"),                                       # обязательный отсутствует
            call(8, "no_such_tool", {}),                                # неизвестный инструмент
            req(9, "tools/call", {"name": "user_show", "arguments": []}),   # arguments не объект
            call(10, "user_set_quota", {"name": "anna", "quota_gb": 200000}),  # maximum
            call(11, "profile_install", {"profile": "awg2", "port": True}),    # bool вместо integer
        ]
        res = run(cases)
        for msg_id in range(1, 12):
            self.assertIn("error", res.by_id[msg_id], msg_id)
            self.assertEqual(res.by_id[msg_id]["error"]["code"], -32602, msg_id)
        self.assertEqual(res.args, [])

    def test_mtu_below_1280_is_refused_in_words(self):
        # L-02б (живая проверка 08.10): ниже 1280 у клиентов с AllowedIPs ::/0 туннель не поднимается вовсе. Граница —
        # minimum 1280 из actions.json, отказ — её словами (below_minimum), а не общим «минимум 1280»; мост не зовётся.
        res = run([call(1, "profile_set_mtu", {"profile": "awg2", "mtu": 1279}),
                   call(2, "profile_set_mtu", {"profile": "awg2", "mtu": 1280})])
        err = res.by_id[1]["error"]
        self.assertEqual(err["code"], -32602)
        self.assertEqual(err["message"], "Ниже 1280 туннель не поднимется у клиентов с IPv6 (AllowedIPs ::/0) — "
                                         "оставьте 1280 или больше.")
        self.assertEqual(res.args, [["--action=profile-mtu", "--json", "--profile=awg2", "--mtu=1280"]])
        res = run([call(1, "profile_set_mtu", {"profile": "awg2", "mtu": 1200})], env={"GOAMNEZIA_LANG": "en"})
        self.assertIn("Below 1280 the tunnel will not come up for clients with IPv6", res.by_id[1]["error"]["message"])
        # Прочие границы — прежним общим текстом: у них своего объяснения нет.
        res = run([call(1, "logs_tail", {"component": "bot", "lines": 5})])
        self.assertIn("минимум", res.by_id[1]["error"]["message"])

    def test_parameter_mapping_underscore_to_dash(self):
        res = run([call(1, "user_set_quota", {"name": "anna", "quota_gb": 50}),
                   call(2, "logs_tail", {"component": "bot"}),
                   call(3, "logs_tail", {"component": "bot", "lines": 50}),
                   call(4, "user_add", {"name": "anna", "label": "Анна Иванова", "profiles": "awg2,awg3", "days": 30.0})])
        self.assertEqual(res.args[0], ["--action=user-quota", "--json", "--name=anna", "--quota-gb=50"])
        self.assertEqual(res.args[1], ["--action=logs-tail", "--json", "--component=bot"])  # default не подставляем
        self.assertEqual(res.args[2], ["--action=logs-tail", "--json", "--component=bot", "--lines=50"])
        self.assertEqual(res.args[3], ["--action=user-add", "--json", "--name=anna", "--label=Анна Иванова",
                                       "--profiles=awg2,awg3", "--days=30"])

    def test_empty_string_reaches_bridge_where_it_means_reset(self):
        # Пустая строка у host и ip — законный ответ «вернуть общий адрес» (так их описывает actions.json),
        # а шаблон без пустой альтернативы отвергал её ещё здесь: через MCP нельзя было вернуть Endpoint
        # на IP сервера, хотя README и гайд это обещали.
        res = run([call(1, "endpoint_set", {"host": ""}),
                   call(2, "user_set_address", {"name": "anna", "ip": ""}),
                   call(3, "user_set_address", {"name": "anna", "ip": "198.51.100.20"}),
                   call(4, "user_set_address", {"name": "anna", "ip": "198.51.100.020"}),   # ведущий ноль
                   call(5, "user_set_address", {"name": "anna"}),                            # ip не передан
                   call(6, "endpoint_set", {"host": "Bad_Host"})])
        self.assertEqual(res.args, [["--action=endpoint-set", "--json", "--host="],
                                    ["--action=user-set-address", "--json", "--name=anna", "--ip="],
                                    ["--action=user-set-address", "--json", "--name=anna", "--ip=198.51.100.20"]])
        for msg_id in (4, 5, 6):
            self.assertEqual(res.by_id[msg_id]["error"]["code"], -32602, msg_id)

    def test_user_get_key_returns_images(self):
        res = run([call(1, "user_get_key", {"name": "anna", "profile": "awg2"})])
        result = res.by_id[1]["result"]
        self.assertFalse(result["isError"])
        images = [c for c in result["content"] if c["type"] == "image"]
        self.assertEqual(len(images), 3)                       # qr_conf_png_b64 + 2 × qr_vpn_png_b64
        for image in images:
            self.assertEqual(image["mimeType"], "image/png")
            self.assertTrue(base64.b64decode(image["data"]).startswith(PNG_SIGNATURE))
        text = result["content"][0]["text"]
        self.assertEqual(result["content"][0]["type"], "text")
        self.assertNotIn(images[0]["data"], text)              # base64 не дублируется в тексте
        self.assertIn("PNG", text)
        self.assertIn("vpn://FAKE", text)
        self.assertEqual(result["structuredContent"]["qr_conf_png_b64"], images[0]["data"])
        self.assertEqual(len(result["structuredContent"]["qr_vpn_png_b64"]), 2)
        self.assertEqual(res.args, [["--action=user-key", "--json", "--name=anna", "--profile=awg2"]])

    def test_bridge_error_status_sets_is_error(self):
        res = run([call(1, "server_status")], env={"FAKE_ACTION_STATUS": "error"})
        result = res.by_id[1]["result"]
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["status"], "error")
        self.assertEqual(result["structuredContent"]["code"], "forced_error")
        self.assertEqual(audit_rec(res.audit)["status"], "error")

    def test_bridge_without_json_is_transport_error(self):
        res = run([call(1, "server_status")], env={"FAKE_ACTION_NO_JSON": "1"})
        result = res.by_id[1]["result"]
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["code"], "transport")
        self.assertEqual(result["structuredContent"]["exit_code"], 3)

    def test_missing_bridge_binary_is_transport_error(self):
        res = run([call(1, "server_status")], env={"GOAMNEZIA_ACTION_BIN": "/nonexistent/goamnezia-install.sh"})
        result = res.by_id[1]["result"]
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["code"], "transport")

    def test_bridge_timeout_is_reported_and_process_killed(self):
        res = run([call(1, "server_status"), req(2, "ping")],
                  env={"FAKE_ACTION_SLEEP": "8", "GOAMNEZIA_MCP_TIMEOUT": "1"})
        result = res.by_id[1]["result"]
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["code"], "timeout")
        self.assertEqual(res.by_id[2]["result"], {})          # сессия жива после таймаута
        self.assertLess(res.elapsed, 6, "мост не был остановлен по таймауту")

    def test_stdout_contains_only_protocol(self):
        res = run([call(1, "server_status"), call(2, "users_list")])
        self.assertNotIn("GARBAGE-LINE-FROM-BRIDGE", res.stdout)
        self.assertNotIn("spinner noise", res.stdout)
        for line in res.stdout.splitlines():
            self.assertTrue(line.startswith("{"), line)

    def test_audit_log_lines(self):
        res = run([call(1, "users_list"), call(2, "user_get_key", {"name": "anna", "profile": "awg2"})])
        lines = [l for l in res.audit.splitlines() if l.strip()]
        self.assertEqual(len(lines), 2)
        # Журнал — построчный JSON одного формата на весь продукт (CONTRACT §7.1).
        first = json.loads(lines[0])
        self.assertEqual((first["iface"], first["stage"], first["kind"]), ("mcp", "call", "call"))
        self.assertEqual((first["tool"], first["action"]), ("users_list", "user-list"))
        self.assertEqual(first["status"], "success")
        self.assertEqual(first["client"], "test_mcp/1")
        self.assertEqual(json.loads(lines[1])["params"]["name"], "anna")
        self.assertNotIn("FAKE-PRIVATE-KEY", res.audit)       # ответ моста в аудит не попадает
        self.assertNotIn("iVBOR", res.audit)


def audit_rec(audit_text, index=-1):
    """Запись журнала как объект: одна строка — один JSON (CONTRACT §7.1)."""
    lines = [l for l in audit_text.splitlines() if l.strip()]
    assert lines, "журнал пуст"
    return json.loads(lines[index])


class StdinParamTests(unittest.TestCase):
    """user_attach_key: ключ человека уходит мосту каналом, а не в argv (там его видно в ps),
    а в журнал аудита — только его длина."""

    CONF = "[Interface]\nPrivateKey = SECRETKEYSECRETKEYSECRETKEYSECRETKEYSECRE=\n\n[Peer]\nPublicKey = x\n"

    def test_key_goes_to_stdin_not_argv_or_audit(self):
        with tempfile.TemporaryDirectory() as d:
            stdin_file = os.path.join(d, "stdin.txt")
            res = run([call(1, "user_attach_key", {"name": "anna", "config": self.CONF})],
                      env={"FAKE_ACTION_STDIN_FILE": stdin_file})
            self.assertFalse(res.by_id[1]["result"]["isError"], res.by_id[1])
            self.assertEqual(res.args, [["--action=user-attach-key", "--json", "--name=anna"]])
            with open(stdin_file, "r", encoding="utf-8") as fh:
                self.assertEqual(fh.read(), self.CONF)
        self.assertNotIn("SECRETKEY", res.audit)
        self.assertIn("chars", res.audit)

    def test_debug_log_does_not_carry_the_key(self):
        # TK-6: на уровне debug в stderr шли первые 512 байт запроса — с ключом человека внутри.
        res = run([call(1, "user_attach_key", {"name": "anna", "config": self.CONF})],
                  env={"GOAMNEZIA_MCP_LOG": "debug"})
        self.assertFalse(res.by_id[1]["result"]["isError"], res.by_id[1])
        self.assertIn("tool=user_attach_key", res.stderr)
        self.assertIn("config:<%d chars>" % len(self.CONF), res.stderr)
        self.assertNotIn("SECRETKEY", res.stderr)
        self.assertNotIn("PrivateKey", res.stderr)

    def test_other_tools_get_no_stdin(self):
        with tempfile.TemporaryDirectory() as d:
            stdin_file = os.path.join(d, "stdin.txt")
            run([call(1, "users_list")], env={"FAKE_ACTION_STDIN_FILE": stdin_file})
            with open(stdin_file, "r", encoding="utf-8") as fh:
                self.assertEqual(fh.read(), "")

    def test_control_chars_other_than_newlines_are_refused(self):
        res = run([call(1, "user_attach_key", {"name": "anna", "config": "vpn://abc\x00def"}),
                   call(2, "user_attach_key", {"name": "anna", "config": "x" * 70000})])
        self.assertEqual(res.by_id[1]["error"]["code"], -32602)
        self.assertEqual(res.by_id[2]["error"]["code"], -32602)
        self.assertEqual(res.args, [])


class ResourcesTests(unittest.TestCase):
    def test_list_and_read(self):
        res = run([req(1, "resources/list"), req(2, "resources/read", {"uri": "goamnezia://users"}),
                   req(3, "resources/read", {"uri": "goamnezia://nope"}), req(4, "resources/templates/list")])
        resources = res.by_id[1]["result"]["resources"]
        self.assertEqual([r["uri"] for r in resources], [r["uri"] for r in ACTIONS["resources"]])
        # Ресурс-действие — application/json, файловый — mimeType из записи actions.json
        expected_mime = dict((r["uri"], r.get("mimeType") or "application/json") for r in ACTIONS["resources"])
        for item in resources:
            self.assertEqual(item["mimeType"], expected_mime[item["uri"]], item["uri"])
            self.assertTrue(item["name"])
        contents = res.by_id[2]["result"]["contents"]
        self.assertEqual(contents[0]["uri"], "goamnezia://users")
        self.assertEqual(contents[0]["mimeType"], "application/json")
        self.assertEqual(json.loads(contents[0]["text"])["status"], "success")
        self.assertEqual(res.args, [["--action=user-list", "--json"]])
        self.assertEqual(res.by_id[3]["error"]["code"], -32002)
        self.assertEqual(res.by_id[4]["result"], {"resourceTemplates": []})

    def test_read_bridge_error(self):
        res = run([req(1, "resources/read", {"uri": "goamnezia://status"})], env={"FAKE_ACTION_STATUS": "error"})
        self.assertEqual(res.by_id[1]["error"]["code"], -32603)
        self.assertEqual(res.by_id[1]["error"]["data"]["code"], "forced_error")

    def test_read_guide_file_resource(self):
        res = run([req(1, "resources/read", {"uri": GUIDE_URI})])
        contents = res.by_id[1]["result"]["contents"]
        self.assertEqual(len(contents), 1)
        self.assertEqual(contents[0]["uri"], GUIDE_URI)
        self.assertEqual(contents[0]["mimeType"], "text/markdown")
        text = contents[0]["text"]
        self.assertTrue(text.startswith("# "), text[:80])            # markdown с заголовком первой строкой
        with open(GUIDE, "r", encoding="utf-8") as fh:
            self.assertEqual(text, fh.read())                          # файл отдаётся целиком, как есть
        self.assertEqual(res.args, [])                                 # файл читается без моста
        rec = audit_rec(res.audit)
        self.assertEqual((rec["iface"], rec["kind"], rec["tool"], rec["action"]),
                         ("mcp", "resource", "-", "file:AGENT_GUIDE.md"))
        self.assertEqual(rec["status"], "success")

    def test_file_resource_is_found_when_started_through_symlink(self):
        # На сервере клиент запускает симлинк /usr/local/bin/goamnezia-mcp → server.py; «каталог
        # сервера» должен быть каталогом настоящего файла, иначе гайд ищется рядом с симлинком
        tmp = tempfile.mkdtemp(prefix="goamnezia-mcp-link-")
        link = os.path.join(tmp, "goamnezia-mcp")
        try:
            os.symlink(SERVER, link)
            res = run([req(1, "resources/read", {"uri": GUIDE_URI})], server=link)
        finally:
            shutil.rmtree(tmp)
        contents = res.by_id[1]["result"]["contents"]
        self.assertEqual(contents[0]["mimeType"], "text/markdown")
        self.assertTrue(contents[0]["text"].startswith("# "))

    def test_missing_file_resource_is_not_found(self):
        # Файл ресурса пропал (ошибка упаковки): ресурс в списке остаётся, чтение отвечает -32002 с путём,
        # --check называет проблему и возвращает 1
        spec = dict(ACTIONS)
        spec["resources"] = [{"uri": GUIDE_URI, "file": "NO_SUCH_GUIDE.md", "mimeType": "text/markdown", "name": "Гайд"}]
        path = write_spec(spec)
        try:
            res = run([req(1, "resources/list"), req(2, "resources/read", {"uri": GUIDE_URI})],
                      env={"GOAMNEZIA_ACTIONS_JSON": path})
            env = dict(os.environ, GOAMNEZIA_ACTION_BIN=FIXTURE, GOAMNEZIA_ACTIONS_JSON=path)
            check = subprocess.run([sys.executable, SERVER, "--check"], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        finally:
            shutil.rmtree(os.path.dirname(path))
        self.assertEqual([r["uri"] for r in res.by_id[1]["result"]["resources"]], [GUIDE_URI])
        err = res.by_id[2]["error"]
        self.assertEqual(err["code"], -32002)
        self.assertIn("NO_SUCH_GUIDE.md", err["message"])
        self.assertIn(GUIDE_URI, err["message"])
        self.assertTrue(err["data"]["path"].endswith(os.path.join("goamnezia-mcp", "NO_SUCH_GUIDE.md")))
        self.assertEqual(res.args, [])
        self.assertEqual(check.returncode, 1)
        self.assertIn("ПРОБЛЕМА", check.stderr.decode("utf-8", "replace"))
        self.assertIn("NO_SUCH_GUIDE.md", check.stderr.decode("utf-8", "replace"))


class PromptsTests(unittest.TestCase):
    def test_list_and_get(self):
        res = run([req(1, "prompts/list"), req(2, "prompts/get", {"name": "onboard_user", "arguments": {"name": "anna"}}),
                   req(3, "prompts/get", {"name": "onboard_user"}),
                   req(4, "prompts/get", {"name": "nope"}),
                   req(5, "prompts/get", {"name": "diagnose_user", "arguments": {"name": "bad name!"}}),
                   req(6, "prompts/get", {"name": "safe_core_update", "arguments": {"profile": "awg3"}})])
        prompts = res.by_id[1]["result"]["prompts"]
        self.assertEqual([p["name"] for p in prompts], [p["name"] for p in ACTIONS["prompts"]])
        for pdef, item in zip(ACTIONS["prompts"], prompts):
            self.assertEqual([(a["name"], a["required"]) for a in item["arguments"]],
                             [(a["name"], a["required"]) for a in pdef["arguments"]])
            self.assertIn(pdef["description"]["ru"], item["description"])
        got = res.by_id[2]["result"]
        self.assertEqual(got["description"], "Создать пользователя и выдать ему ключи нужных профилей")
        text = got["messages"][0]["content"]["text"]
        self.assertEqual(got["messages"][0]["role"], "user")
        self.assertIn("anna", text)
        for tool in ("profiles_list", "user_add", "user_get_key"):
            self.assertIn(tool, text)
        self.assertLess(text.index("user_add"), text.index("user_get_key"))   # порядок шагов
        self.assertIn("confirm", text)
        self.assertEqual(res.by_id[3]["error"]["code"], -32602)     # нет обязательного name
        self.assertEqual(res.by_id[4]["error"]["code"], -32602)     # неизвестная подсказка
        self.assertEqual(res.by_id[5]["error"]["code"], -32602)     # name не по регексу
        self.assertIn("upstream_rollback", res.by_id[6]["result"]["messages"][0]["content"]["text"])
        self.assertEqual(res.args, [])                              # подсказки мост не дёргают

    def test_language_switch(self):
        res = run([req(1, "prompts/get", {"name": "onboard_user", "arguments": {"name": "anna"}}),
                   call(2, "user_delete", {"name": "anna"})], env={"GOAMNEZIA_LANG": "en"})
        got = res.by_id[1]["result"]
        self.assertEqual(got["description"], "Create a user and issue keys of the needed profiles")
        self.assertIn("Steps:", got["messages"][0]["content"]["text"])
        self.assertIn("Missing required parameters", res.by_id[2]["error"]["message"])


class JsonRpcTests(unittest.TestCase):
    def test_unknown_method(self):
        res = run([req(1, "tools/explode")])
        self.assertEqual(res.by_id[1]["error"]["code"], -32601)

    def test_parse_error(self):
        res = run(['{"jsonrpc": "2.0", "id": 5, "method": ', req(2, "ping")])
        errors = [r for r in res.responses if "error" in r and r["error"]["code"] == -32700]
        self.assertEqual(len(errors), 1)
        self.assertIsNone(errors[0]["id"])
        self.assertEqual(res.by_id[2]["result"], {})              # сессия пережила битую строку

    def test_invalid_request(self):
        res = run(["42", '{"id": 7, "method": "ping"}', '{"jsonrpc": "2.0", "id": 8}',
                   '{"jsonrpc": "2.0", "id": 9, "method": "ping", "params": [1]}'])
        codes = [r["error"]["code"] for r in res.responses if "error" in r]
        self.assertEqual(codes, [-32600, -32600, -32600, -32602])

    def test_batch_is_tolerated(self):
        res = run([json.dumps([req(1, "ping"), req(2, "ping")]), "[]"])
        batch = [r for r in res.responses if isinstance(r, list)]
        self.assertEqual(len(batch), 1)
        self.assertEqual([r["id"] for r in batch[0]], [1, 2])
        self.assertTrue(any(isinstance(r, dict) and r.get("error", {}).get("code") == -32600 for r in res.responses))


class CloneAlarmTests(unittest.TestCase):
    """Тревога о клоне обязана говорить, УДАЛОСЬ ли заморозить доступ (DEFECTS §1d.1 №5).

    Не удалось — доступ жив, клон работает дальше, и час тишины оставил бы владельца с одним
    сообщением про угон, который никто не остановил.
    """

    class _Grants(object):
        def __init__(self, frozen):
            self.frozen = frozen
        def freeze(self, grant_id, ip, when):
            return self.frozen

    class _Notifier(object):
        def __init__(self):
            self.events = []
        def send(self, event, payload):
            self.events.append((event, payload))

    class _Log(object):
        def error(self, *a):
            pass
        warn = info = debug = error

    @classmethod
    def setUpClass(cls):
        import importlib.util
        spec = importlib.util.spec_from_file_location("goamnezia_mcp_server", SERVER)
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)

    def _transport(self, frozen):
        settings = types.SimpleNamespace(notify_dir="", state_dir="", allowed_origins=())
        notifier = self._Notifier()
        transport = self.mod.HttpTransport(settings, None, None, None, self._Log(),
                                           self._Grants(frozen), notifier=notifier)
        return transport, notifier

    @staticmethod
    def _grant():
        return types.SimpleNamespace(id="a-1a2b", label="Claude Code", scope="read",
                                     last_ip="203.0.113.5", last_used="2026-09-11T11:00:00Z")

    def test_event_carries_the_freeze_result(self):
        for frozen in (True, False):
            transport, notifier = self._transport(frozen)
            transport.raise_clone(self._grant(), "203.0.113.9")
            self.assertEqual(len(notifier.events), 1)
            event, payload = notifier.events[0]
            self.assertEqual(event, "agent_compromised")
            self.assertIs(payload["frozen"], frozen)
            self.assertTrue(payload["clone_at"] and payload["clone_ip"])

    def test_a_live_clone_is_not_kept_quiet_for_an_hour(self):
        self.assertLess(self.mod.HTTP_ALARM_QUIET_LIVE, self.mod.HTTP_ALARM_QUIET)
        grant = self._grant()
        for frozen, expect in ((True, 1), (False, 2)):
            transport, notifier = self._transport(frozen)
            transport.raise_clone(grant, "203.0.113.9")
            # Отматываем тишину чуть дальше короткого окна: заморозили — по-прежнему молчим,
            # не заморозили — тревожим снова, потому что доступ всё ещё впускает вора.
            transport._alarms["clone:" + grant.id] -= self.mod.HTTP_ALARM_QUIET_LIVE + 1
            transport.raise_clone(grant, "203.0.113.9")
            self.assertEqual(len(notifier.events), expect, "frozen=%s" % frozen)

    def test_failed_freeze_leaves_a_mark_for_the_grant_card(self):
        """L-08: заморозить не удалось — отметка рядом с цепочкой (ядро отдаёт её в agents_list полем
        freeze_failed); следующая тревога заморозила — отметка снята."""
        tmp = tempfile.mkdtemp(prefix="mcp-freeze-")
        try:
            grant = self._grant()
            settings = types.SimpleNamespace(notify_dir="", state_dir=tmp, allowed_origins=())
            path = os.path.join(tmp, "agents", grant.id + ".freeze_failed")
            for frozen in (False, True):
                transport = self.mod.HttpTransport(settings, None, None, None, self._Log(), self._Grants(frozen),
                                                   notifier=self._Notifier())
                transport.raise_clone(grant, "203.0.113.9")
                if frozen:
                    self.assertFalse(os.path.exists(path), "заморозили — отметки нет")
                    continue
                with open(path, encoding="utf-8") as fh:
                    mark = json.load(fh)
                self.assertEqual((mark["id"], mark["ip"]), (grant.id, "203.0.113.9"))
                self.assertTrue(mark["at"])
                self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class CliAndDocsTests(unittest.TestCase):
    def test_check_and_tools_md(self):
        env = dict(os.environ, GOAMNEZIA_ACTION_BIN=FIXTURE, GOAMNEZIA_ACTIONS_JSON=ACTIONS_JSON)
        check = subprocess.run([sys.executable, SERVER, "--check"], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(check.returncode, 0, check.stderr.decode("utf-8", "replace"))
        self.assertIn("скрыто (mcp: null): %d" % len(HIDDEN), check.stderr.decode("utf-8", "replace"))
        md = subprocess.run([sys.executable, SERVER, "--tools-md"], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(md.returncode, 0)
        table = md.stdout.decode("utf-8")
        for adef in PUBLISHED.values():
            self.assertIn("`%s`" % adef["mcp"], table)
        for cli in HIDDEN:
            self.assertNotIn("`%s`" % cli.replace("-", "_"), table)
        # Вторая таблица — ресурсы обоих видов, файловые — с именем файла и MIME из записи
        for rdef in ACTIONS["resources"]:
            self.assertIn("`%s`" % rdef["uri"], table, rdef["uri"])
            if rdef.get("file"):
                self.assertIn("`%s`" % rdef["file"], table)
                self.assertIn("`%s`" % rdef["mimeType"], table)

    def test_readme_lists_every_tool(self):
        with open(README, "r", encoding="utf-8") as fh:
            readme = fh.read()
        for adef in PUBLISHED.values():
            self.assertIn("`%s`" % adef["mcp"], readme, adef["mcp"])
        for rdef in ACTIONS["resources"]:
            self.assertIn(rdef["uri"], readme)
            if rdef.get("file"):
                self.assertIn(rdef["file"], readme)
        for pdef in ACTIONS["prompts"]:
            self.assertIn(pdef["name"], readme)

    def test_guide_mentions_every_tool(self):
        # Гайд — документация для модели: каждый публикуемый инструмент, ресурс и подсказка должны быть
        # в нём хотя бы раз, разрушительные — в разделе про confirm, коды ошибок — в таблице кодов
        with open(GUIDE, "r", encoding="utf-8") as fh:
            guide = fh.read()
        self.assertTrue(guide.startswith("# "))
        for adef in PUBLISHED.values():
            self.assertIn("`%s`" % adef["mcp"], guide, adef["mcp"])
        for cli in HIDDEN:
            self.assertNotIn("`%s`" % cli.replace("-", "_"), guide, cli)   # скрытое не преподносится как инструмент
        for rdef in ACTIONS["resources"]:
            self.assertIn("`%s`" % rdef["uri"], guide, rdef["uri"])
        for pdef in ACTIONS["prompts"]:
            self.assertIn("`%s`" % pdef["name"], guide, pdef["name"])
        confirm_section = guide[guide.index("## 5."):guide.index("## 6.")]
        self.assertIn("`confirm`", confirm_section)
        for adef in PUBLISHED.values():
            if adef["destructive"]:
                self.assertIn("`%s`" % adef["mcp"], confirm_section, adef["mcp"])
        for code in ("lock_timeout", "not_implemented", "unknown_action", "module_missing", "pull_failed",
                     "rate_limit", "unsupported_arch", "transport", "timeout", "forbidden", "port_taken"):
            self.assertIn("`%s`" % code, guide, code)
        self.assertIn("## EN summary", guide)                           # краткая EN-часть в конце

    def test_guide_catalog_matches_the_actions(self):
        """Каталог гайда (§3) — в обе стороны против actions.json: каждая строка называет опубликованный инструмент
        (переименованный или снятый ловится здесь, а не агентом на сервере), каждый опубликованный — в строке, буква
        R/W/D — как в каталоге, параметры — ровно каталожные, обязательные со «*», у разрушительных — `confirm`*.
        Список разрушительных в §5 — ровно разрушительные каталога, параметры подсказок в §6 — как в каталоге
        (находка TAIL-4: гайд отставал от каталога на 1.0.10)."""
        with open(GUIDE, "r", encoding="utf-8") as fh:
            guide = fh.read()
        by_tool = dict((adef["mcp"], adef) for adef in PUBLISHED.values())
        flag = lambda adef: "D" if adef["destructive"] else ("R" if adef["readonly"] else "W")  # noqa: E731
        section = guide[guide.index("## 3."):guide.index("## 4.")]
        rows = {}
        for line in section.splitlines():
            m = re.match(r"\| (`[a-z_]+`(?: / `[a-z_]+`)*) ([RWD]) \| (.*?) \| ", line)
            if not m:
                continue
            for tool in re.findall(r"`([a-z_]+)`", m.group(1)):
                self.assertNotIn(tool, rows, "%s: две строки в §3" % tool)
                rows[tool] = (m.group(2), m.group(3))
        self.assertEqual(sorted(set(rows) - set(by_tool)), [], "в §3 гайда — инструменты, которых нет в каталоге MCP")
        self.assertEqual(sorted(set(by_tool) - set(rows)), [], "опубликованные инструменты без строки в §3 гайда")

        def outside_parens(text):
            out, depth = [], 0
            for ch in text:
                depth += ch == "("
                if depth == 0:
                    out.append(ch)
                depth -= ch == ")" and depth > 0
            return "".join(out)

        for tool, (letter, cell) in sorted(rows.items()):
            adef = by_tool[tool]
            self.assertEqual(letter, flag(adef), "%s: в гайде %s, в каталоге %s" % (tool, letter, flag(adef)))
            want = dict((p, bool((pdef or {}).get("required"))) for p, pdef in mcp_params(adef).items())
            if adef["destructive"]:
                want.setdefault("confirm", True)
            got = dict((p, star == "*") for p, star in re.findall(r"`([a-z_][a-z0-9_]*)`(\*?)", outside_parens(cell))
                       if p not in by_tool)                                # ссылка на соседний инструмент — не параметр
            if not want:
                self.assertEqual((cell.strip(), got), ("—", {}), "%s: параметров в каталоге нет" % tool)
                continue
            self.assertEqual(got, want, "%s: параметры гайда (имя → обязателен) против каталога" % tool)
        # §5: разрушительные — ровно каталожные, ни больше ни меньше
        listed = re.search(r"^Разрушительные инструменты: (.*)$", guide, re.M)
        self.assertIsNotNone(listed, "нет строки «Разрушительные инструменты:» в §5")
        self.assertEqual(sorted(re.findall(r"`([a-z_]+)`", listed.group(1))),
                         sorted(t for t, adef in by_tool.items() if adef["destructive"]))
        # §6: подсказки — с параметрами каталога
        for pdef in ACTIONS["prompts"]:
            args = ", ".join("`%s`%s" % (a["name"], "*" if a.get("required") else "") for a in pdef.get("arguments") or [])
            self.assertIn("`%s` (%s)" % (pdef["name"], args), guide, pdef["name"])


if __name__ == "__main__":
    unittest.main()


class SlidingExpiryTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        import importlib.util
        spec = importlib.util.spec_from_file_location("goamnezia_mcp_srv_slide", SERVER)
        cls.srv = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(cls.srv)
        except SystemExit:
            pass

    """Срок доступа продлевается сам, пока им пользуются.

    Доступ, которым работают, не должен умирать по календарю: владелец не обязан
    помнить про продление, а молчаливое истечение выглядит как поломка — отказ
    тот же неотличимый, что у чужого ключа. Кончается доступ двумя путями:
    владелец убрал его сам, или им не пользовались всю выданную длительность.
    """

    def test_term_is_derived_for_old_records(self):
        # Записи, выданные до появления поля, тоже должны продлеваться: иначе
        # ровно они и страдают от молчаливого истечения.
        raw = {"created": "2026-09-01T00:00:00Z", "expires": "2026-10-01T00:00:00Z"}
        self.assertEqual(self.srv._term_days_from(raw), 30)
        self.assertEqual(self.srv._term_days_from({"created": "", "expires": ""}), 0)
        self.assertEqual(self.srv._term_days_from({"created": "2026-09-02T00:00:00Z",
                                              "expires": "2026-09-01T00:00:00Z"}), 0)

    def test_seconds_left_and_plus_days_agree(self):
        soon = self.srv._utc_plus_days(2)
        left = self.srv._seconds_left(soon)
        self.assertIsNotNone(left)
        self.assertGreater(left, 2 * 86400 - 120)
        self.assertLess(left, 2 * 86400 + 120)
        self.assertIsNone(self.srv._seconds_left("не дата"))
        self.assertIsNone(self.srv._seconds_left(""))

    def test_the_half_term_rule(self):
        # Двигаем не на каждый вызов, а когда прошла половина срока: иначе
        # запись менялась бы постоянно без пользы.
        term = 30
        fresh = self.srv._seconds_left(self.srv._utc_plus_days(29))
        worn = self.srv._seconds_left(self.srv._utc_plus_days(10))
        self.assertFalse(fresh < term * 86400 / 2, "свежий срок двигать незачем")
        self.assertTrue(worn < term * 86400 / 2, "прошла половина — пора двигать")


class ExpiryWarningBandTests(unittest.TestCase):
    """Полоса «срок на исходе» согласована с правилом продления.

    Постоянные трое суток перекрывали правило: срок продлевается сам, когда
    осталось меньше ПОЛОВИНЫ выданной длительности, то есть половина — это пол,
    ниже которого запись используемого доступа не опускается. При длительности
    шесть суток и меньше пол не выше трёх суток, живой доступ попадал в полосу на
    каждом круге, и тревога била раз в сутки вечно — про доступ, вызов которого
    обслуживался прямо в эту секунду. Текст тревоги при этом утверждает обратное:
    «близкий конец означает одно — этим доступом давно не пользовались».
    """

    @classmethod
    def setUpClass(cls):
        import importlib.util
        spec = importlib.util.spec_from_file_location("goamnezia_mcp_srv_warn", SERVER)
        cls.srv = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(cls.srv)
        except SystemExit:
            pass

    def band(self, term_days):
        return self.srv._expiry_warn_seconds(term_days)

    def test_the_band_never_reaches_the_floor_of_the_sliding_window(self):
        # Главное свойство: доступ, которым пользуются, в полосу не попадает
        # никогда — она лежит ниже половины длительности при ЛЮБОМ сроке.
        for term in list(range(1, 40)) + [90, 180, 365, 3650]:
            floor = term * 86400 / 2.0
            self.assertLess(self.band(term), floor, "длительность %d суток" % term)

    def test_the_old_fixed_band_did_reach_it_on_short_terms(self):
        # Свидетель находки: прежний постоянный порог при шести сутках и меньше
        # накрывал пол целиком, поэтому тревога и била вечно.
        old = self.srv.HTTP_EXPIRY_WARN_DAYS * 86400
        for term in (1, 2, 3, 4, 5, 6):
            self.assertGreaterEqual(old, term * 86400 / 2.0, "длительность %d суток" % term)

    def test_ordinary_terms_keep_the_calendar_threshold(self):
        # У обычных сроков поведение прежнее: предупреждаем за трое суток.
        for term in (30, 90, 365):
            self.assertEqual(self.band(term), self.srv.HTTP_EXPIRY_WARN_DAYS * 86400.0, term)

    def test_short_terms_shrink_with_the_term(self):
        self.assertEqual(self.band(6), 1.5 * 86400)
        self.assertEqual(self.band(4), 1.0 * 86400)

    def test_unknown_term_falls_back_to_the_calendar(self):
        # Старая запись, у которой длительность не вывести из created→expires:
        # сказать что-то более точное не из чего.
        self.assertEqual(self.band(0), self.srv.HTTP_EXPIRY_WARN_DAYS * 86400.0)

    def test_grant_knows_its_term_and_derives_it_for_old_records(self):
        self.assertEqual(self.srv.Grant({"term_days": 7}).term_days, 7)
        self.assertEqual(self.srv.Grant({"created": "2026-09-01T00:00:00Z",
                                         "expires": "2026-10-01T00:00:00Z"}).term_days, 30)
        self.assertEqual(self.srv.Grant({}).term_days, 0)

    # ── сама тревога, а не только счёт полосы ────────────────────────────────
    class Stub(object):
        """HttpTransport ровно в том объёме, который читает notify_expiring."""

        def __init__(self):
            self.sent = []
            self.notifier = self

        def _alarm_due(self, key, quiet):
            return True

        def send(self, event, fields):
            self.sent.append((event, fields))

    def fire(self, term_days, days_left):
        stub = self.Stub()
        grant = self.srv.Grant({"id": "a-1a2b", "label": "Claude Code", "scope": "read",
                                "term_days": term_days,
                                "expires": self.srv._utc_plus_days(days_left)})
        self.srv.HttpTransport.notify_expiring(stub, grant)
        return stub.sent

    def test_a_grant_that_is_about_to_renew_itself_raises_nothing(self):
        # Шесть суток, осталось двое: следующий же удачный вызов отодвинет срок
        # на полные шесть. Прежде тут уходила тревога — каждые сутки, вечно.
        self.assertEqual(self.fire(6, 2), [])
        self.assertEqual(self.fire(3, 1), [])

    def test_a_grant_that_really_runs_out_still_raises(self):
        # Та же длительность, но доступ молчит: до конца меньше четверти срока.
        sent = self.fire(6, 1)
        self.assertEqual([e for e, _ in sent], ["agent_expiring"], sent)
        self.assertEqual(sent[0][1]["agent"], "a-1a2b")
        sent = self.fire(30, 2)
        self.assertEqual([e for e, _ in sent], ["agent_expiring"], sent)

    def test_an_unlimited_grant_raises_nothing(self):
        stub = self.Stub()
        self.srv.HttpTransport.notify_expiring(stub, self.srv.Grant({"id": "a-1a2b"}))
        self.assertEqual(stub.sent, [])


class AuditFloodTests(unittest.TestCase):
    """Аудит безопасности 25.09.2026: любой грант (даже read) раздувал аудит — 100 КБ имени клиента из
    initialize в каждой строке и сырые аргументы в отказах. Теперь имя обрезано, у отказа — только имена
    и размеры аргументов, строка не длиннее AUDIT_LINE_MAX."""

    class _Log(object):
        def info(self, *a, **k):
            pass
        warn = warning = debug = error = info

    @classmethod
    def setUpClass(cls):
        import importlib.util
        spec = importlib.util.spec_from_file_location("goamnezia_mcp_server_audit", SERVER)
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)

    def test_clip_text_drops_control_chars_and_cuts(self):
        self.assertEqual(self.mod.clip_text("a\x00b\nc", 10), "abc")
        self.assertEqual(len(self.mod.clip_text("x" * 100000, 60)), 61)

    def test_rejected_call_logs_only_sizes_and_line_is_capped(self):
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "audit.log")
            audit = self.mod.Audit(path, self._Log())
            audit.write("call", "user_delete", "user-del", {"name": "y" * 50000, "confirm": "z" * 50000},
                        "rejected", "forbidden", 0, client="c" * 100000)
            audit.write("call", "status", "status", {"name": "q" * 50000}, "ok", "", 5, client="c")
            with open(path, encoding="utf-8") as fh:
                lines = fh.read().splitlines()
            self.assertEqual(len(lines), 2)
            for line in lines:
                self.assertLessEqual(len(line), self.mod.AUDIT_LINE_MAX)
            first = json.loads(lines[0])
            self.assertNotIn("yyyy", lines[0])
            self.assertTrue(first["params"]["name"].startswith("<str "), first["params"])
            self.assertLessEqual(len(first["client"]), 101)
            self.assertEqual(json.loads(lines[1])["params"].get("_truncated"), True)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # Тестовый ключ (base64 строки «N3-test-only-…»), не настоящий.
    HPK = "TjMtdGVzdC1vbmx5LW5vdC1hLXJlYWwta2V5LTAwMDE="

    def test_header_protection_key_value_is_masked_in_the_journal(self):
        # KEENETIC-I1-N N3: значение HeaderProtectionKey у profile-set-param ложилось в журнал MCP целиком.
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "audit.log")
            audit = self.mod.Audit(path, self._Log())
            audit.write("call", "profile_set_param", "profile-set-param",
                        {"profile": "awg3", "key": "HeaderProtectionKey", "value": self.HPK}, "ok", "", 5, client="c")
            audit.write("call", "profile_set_param", "profile-set-param",
                        {"profile": "awg3", "key": "headerprotectionkey", "value": self.HPK}, "error", "invalid_param", 5,
                        client="c")
            audit.write("call", "profile_set_param", "profile-set-param",
                        {"profile": "awg3", "key": "Jc", "value": "7"}, "ok", "", 5, client="c")
            audit.write("call", "profile_set_param", "profile-set-param",
                        {"profile": "awg3", "key": "HeaderProtectionKey", "value": ""}, "ok", "", 5, client="c")
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            self.assertNotIn(self.HPK[:12], text)
            recs = [json.loads(line)["params"] for line in text.splitlines()]
            self.assertEqual(recs[0], {"profile": "awg3", "key": "HeaderProtectionKey", "value": "***"})
            self.assertEqual(recs[1]["value"], "***", "регистр ключа не спасает: в журнал идёт и вызов, который ядро отвергнет")
            self.assertEqual(recs[2]["value"], "7", "остальные параметры — как были")
            self.assertEqual(recs[3]["value"], "", "«убрать параметр» — пусто, а не «***»")
            # Другое действие с полями key/value — как было (маска только у параметра-ключа профиля).
            self.assertEqual(self.mod.Audit.scrub({"key": "HeaderProtectionKey", "value": "x"}, "status")["value"], "x")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_header_protection_key_value_is_masked_in_the_bridge_debug_line(self):
        cmd = ["/usr/local/bin/goamnezia", "--action=profile-set-param", "--json", "--profile=awg3",
               "--key=HeaderProtectionKey", "--value=" + self.HPK]
        shown = self.mod.Bridge.loggable("profile-set-param", cmd)
        self.assertNotIn(self.HPK[:12], " ".join(shown))
        self.assertIn("--value=***", shown)
        self.assertEqual(cmd[-1], "--value=" + self.HPK, "сама команда — прежняя: ядру нужен ключ целиком")
        jc = cmd[:4] + ["--key=Jc", "--value=7"]
        self.assertEqual(self.mod.Bridge.loggable("profile-set-param", jc), jc)
