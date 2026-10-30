# -*- coding: utf-8 -*-
"""Тесты HTTP-транспорта MCP-сервера (goamnezia-mcp/server.py --http, CONTRACT §5.1).

Сервер поднимается настоящим подпроцессом на свободных портах петли с временным
config.json и agents.json, мост подменён заглушкой tests/fixtures/fake_action.sh.
Адрес агента подставляется заголовком X-Real-IP: с петли сервер верит ему, как
заголовку nginx, — так проверяются allow_ips и бан по IP без второго интерфейса.
Каждый отказный запрос идёт со своего адреса, чтобы неудачи разных тестов не
складывались в один бан (20 за 10 минут).

Что проверяется: верный секрет → tools/list по уровню (read без user_get_key и
user_add, write без разрушительных, admin — всё); вызов вне уровня → -32003;
выключенный / истёкший / чужой IP / неверный секрет → одинаковый 404 с телом nginx;
чужой Origin → 403; 21 неверный запрос → бан (и верный секрет с этого IP → 404);
правка agents.json на диске видна следующим запросом; last_used/last_ip/calls растут;
GET → 405; путь вне формы → 404; персональный порт принимает только свой доступ;
пакеты и уведомления; лимит тела; TLS-слушатель; аудит с agent=<id>; SIGTERM.

Второй фактор (CONTRACT §5.1): доступ с require_token без заголовка Authorization и
с чужим токеном получает тот же 404, что неизвестный путь (401 подтвердил бы, что
путь существует), с верным — 200; доступ, выданный до второго фактора
(require_token отсутствует), продолжает работать по одному адресу; привязка к
адресу закрепляет первый и отвергает второй, снятие возвращает доступ; о первом
использовании, о вызове с нового адреса и о запросе с чужим токеном событие
ложится в очередь бота.

Одноразовый ключ (mode: rolling, docs/MCP_ROTATION.md §1 в редакции §11): цепочка вращается,
следующий ключ приезжает заголовком X-Ga-Next-Key; ключ из истории выданных — клон, доступ
замораживается и владельцу уходит карточка с двумя временами и двумя адресами; ключ, которого
сервер не выдавал, — обычный мусор, доступ он НЕ гасит; повтор с прежним ключом и тем же номером
отдаёт сохранённый ответ и не выполняет действие заново; ДВА ЗАПРОСА ОДНИМ КЛЮЧОМ РАЗОМ (§11.8)
доступ не замораживают, получают один и тот же новый ключ, а повтор такого запроса не выполняет
действие дважды, тогда как тот же ключ, посланный ПОЗЖЕ ответа, по-прежнему клон; промах мимо
пути цепочку не трогает;
успешный запрос обнуляет счётчик неудач; состояние цепочки лежит вне /run и переживает его
удаление; перевыпуск начинает новую цепочку и снимает заморозку; static работает по-прежнему.

Запуск: python3 -m unittest tests/test_mcp_http.py
"""

import hashlib
import http.client
import json
import os
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(ROOT, "goamnezia-mcp", "server.py")
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "fake_action.sh")
ACTIONS_JSON = os.path.join(ROOT, "lib", "actions.json")
PROTOCOL = "2025-06-18"
NGINX_404 = (b"<html>\r\n<head><title>404 Not Found</title></head>\r\n<body>\r\n"
             b"<center><h1>404 Not Found</h1></center>\r\n<hr><center>nginx</center>\r\n"
             b"</body>\r\n</html>\r\n")

with open(ACTIONS_JSON, "r", encoding="utf-8") as _fh:
    ACTIONS = json.load(_fh)
# Пределы берём из самого сервера, а не переписываем числами: разъехавшись, тест
# начал бы проверять не то, что работает.
_SRC = open(SERVER, "r", encoding="utf-8").read()


def _const(name):
    import re as _re
    return float(_re.search(r"^%s = ([0-9.]+)" % name, _SRC, _re.M).group(1))


HTTP_BAN_FAILURES = int(_const("HTTP_BAN_FAILURES"))
HTTP_RATE_CALLS = int(_const("HTTP_RATE_CALLS"))
HTTP_RATE_RETRY_AFTER = int(_const("HTTP_RATE_RETRY_AFTER"))
PUBLISHED = dict((cli, adef) for cli, adef in ACTIONS["actions"].items() if adef.get("mcp"))
READ_NAMES = sorted(a["mcp"] for a in PUBLISHED.values() if a.get("readonly") and not a.get("secret"))
WRITE_NAMES = sorted(a["mcp"] for a in PUBLISHED.values() if not a.get("destructive"))
ALL_NAMES = sorted(a["mcp"] for a in PUBLISHED.values())

# Секреты — 43 символа base64url, как выдаёт lib/agents.sh; здесь они намеренно узнаваемые.
SECRETS = {
    "a-read": "R" * 43, "a-write": "W" * 43, "a-admin": "A" * 43, "a-off": "D" * 43,
    "a-exp": "E" * 43, "a-ip": "I" * 43, "a-port": "P" * 43, "a-cnt": "C" * 43,
    "a-tok": "S" * 43, "a-pin": "K" * 43, "a-ntf": "F" * 43,
}
UNKNOWN_SECRET = "X" * 43
# Токены второго фактора — того же вида (32 байта base64url), но передаются заголовком.
TOKENS = {"a-tok": "t" * 43, "a-pin": "q" * 43, "a-ntf": "n" * 43}
WRONG_TOKEN = "z" * 43
# Идентификаторы доступов — как выдаёт lib/agents.sh: a-<4–8 hex>; ключи словаря — лишь имена в тесте.
IDS = {"a-read": "a-0ead", "a-write": "a-0f1e", "a-admin": "a-ad01", "a-off": "a-0ff0",
       "a-exp": "a-e0e0", "a-ip": "a-1a1a", "a-port": "a-b0b0", "a-cnt": "a-c0c0",
       "a-tok": "a-70c0", "a-pin": "a-91a0", "a-ntf": "a-4e40"}

TMP = None
PROC = None
PORT = 0
PORT_GRANT = 0
AGENTS_JSON = ""
AUDIT_LOG = ""
BRIDGE_ENV_LOG = ""
NOTIFY_DIR = ""
STDERR = None
_IP_COUNTER = [0]


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def wait_port(port, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
            return True
        except OSError:
            time.sleep(0.05)
    return False


def grants_doc():
    return {"schema": 1, "agents": [
        {"id": IDS["a-read"], "label": "Наблюдатель", "secret": SECRETS["a-read"], "scope": "read", "enabled": True,
         "created": "2026-09-08T10:00:00Z", "expires": "", "port": 0, "allow_ips": [], "last_used": "", "last_ip": "", "calls": 0},
        {"id": IDS["a-write"], "label": "Claude Code", "secret": SECRETS["a-write"], "scope": "write", "enabled": True,
         "created": "2026-09-08T10:00:00Z", "expires": "", "port": 0, "allow_ips": [], "last_used": "", "last_ip": "", "calls": 0},
        {"id": IDS["a-admin"], "label": "Админ", "secret": SECRETS["a-admin"], "scope": "admin", "enabled": True,
         "created": "2026-09-08T10:00:00Z", "expires": "2099-01-01T00:00:00Z", "port": 0, "allow_ips": [], "last_used": "", "last_ip": "", "calls": 0},
        {"id": IDS["a-off"], "label": "Выключен", "secret": SECRETS["a-off"], "scope": "admin", "enabled": False,
         "created": "2026-09-08T10:00:00Z", "expires": "", "port": 0, "allow_ips": [], "last_used": "", "last_ip": "", "calls": 0},
        {"id": IDS["a-exp"], "label": "Истёк", "secret": SECRETS["a-exp"], "scope": "admin", "enabled": True,
         "created": "2026-01-01T10:00:00Z", "expires": "2026-02-01T00:00:00Z", "port": 0, "allow_ips": [], "last_used": "", "last_ip": "", "calls": 0},
        {"id": IDS["a-ip"], "label": "Только из офиса", "secret": SECRETS["a-ip"], "scope": "write", "enabled": True,
         "created": "2026-09-08T10:00:00Z", "expires": "", "port": 0, "allow_ips": ["203.0.113.0/24", "198.51.100.77"],
         "last_used": "", "last_ip": "", "calls": 0},
        {"id": IDS["a-port"], "label": "Свой порт", "secret": SECRETS["a-port"], "scope": "write", "enabled": True,
         "created": "2026-09-08T10:00:00Z", "expires": "", "port": PORT_GRANT, "allow_ips": [], "last_used": "", "last_ip": "", "calls": 0},
        {"id": IDS["a-cnt"], "label": "Счётчик", "secret": SECRETS["a-cnt"], "scope": "read", "enabled": True,
         "created": "2026-09-08T10:00:00Z", "expires": "", "port": 0, "allow_ips": [], "last_used": "", "last_ip": "", "calls": 0},
        # Доступы со вторым фактором: адрес плюс токен в заголовке Authorization.
        {"id": IDS["a-tok"], "label": "С токеном", "secret": SECRETS["a-tok"], "token": TOKENS["a-tok"],
         "require_token": True, "scope": "write", "enabled": True, "created": "2026-09-08T10:00:00Z",
         "expires": "", "port": 0, "allow_ips": [], "pin_ip": False, "pinned_ip": "", "known_ips": [],
         "last_used": "", "last_ip": "", "calls": 0},
        {"id": IDS["a-pin"], "label": "С привязкой", "secret": SECRETS["a-pin"], "token": TOKENS["a-pin"],
         "require_token": True, "scope": "read", "enabled": True, "created": "2026-09-08T10:00:00Z",
         "expires": "", "port": 0, "allow_ips": [], "pin_ip": True, "pinned_ip": "", "known_ips": [],
         "last_used": "", "last_ip": "", "calls": 0},
        {"id": IDS["a-ntf"], "label": "Для уведомлений", "secret": SECRETS["a-ntf"], "token": TOKENS["a-ntf"],
         "require_token": True, "scope": "read", "enabled": True, "created": "2026-09-08T10:00:00Z",
         "expires": "", "port": 0, "allow_ips": [], "pin_ip": False, "pinned_ip": "", "known_ips": [],
         "last_used": "", "last_ip": "", "calls": 0},
    ]}


def write_agents(doc):
    """Как lib/agents.sh: tmp рядом + rename — сервер видит новый inode/mtime следующим запросом."""
    tmp = AGENTS_JSON + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, AGENTS_JSON)


def read_agents():
    with open(AGENTS_JSON, "r", encoding="utf-8") as fh:
        return json.load(fh)


def start_server(port, extra_args, env_extra=None):
    env = dict(os.environ)
    env.update({
        "GOAMNEZIA_DIR": TMP,
        "GOAMNEZIA_ACTION_BIN": FIXTURE,
        "GOAMNEZIA_ACTIONS_JSON": ACTIONS_JSON,
        "GOAMNEZIA_AUDIT_LOG": AUDIT_LOG,
        "GOAMNEZIA_LOCK_DIR": os.path.join(TMP, "run"),
        "GOAMNEZIA_NOTIFY_DIR": NOTIFY_DIR,
        "GOAMNEZIA_MCP_LOG": "debug",
        "FAKE_ACTION_ENV_FILE": BRIDGE_ENV_LOG,
    })
    for key in ("GOAMNEZIA_LANG", "GOAMNEZIA_MCP_TIMEOUT", "FAKE_ACTION_SLEEP", "FAKE_ACTION_STATUS", "FAKE_ACTION_NO_JSON",
                "GOAMNEZIA_AGENTS_JSON", "GOAMNEZIA_LOCK_FILE"):
        env.pop(key, None)
    env.update(env_extra or {})
    log = open(os.path.join(TMP, "stderr-%d.log" % port), "wb")
    proc = subprocess.Popen([sys.executable, SERVER, "--http", "--bind", "127.0.0.1", "--port", str(port)] + extra_args,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log, env=env)
    return proc, log


def setUpModule():
    global TMP, PROC, PORT, PORT_GRANT, AGENTS_JSON, AUDIT_LOG, STDERR, NOTIFY_DIR, BRIDGE_ENV_LOG
    TMP = tempfile.mkdtemp(prefix="goamnezia-mcp-http-")
    os.makedirs(os.path.join(TMP, "run"))
    PORT = free_port()
    PORT_GRANT = free_port()
    AGENTS_JSON = os.path.join(TMP, "agents.json")
    AUDIT_LOG = os.path.join(TMP, "audit.log")
    # Окружение, с которым сервер зовёт мост: настоящий мост пишет из него аудит,
    # и только так видно, чьим именем ложится мутация агента.
    BRIDGE_ENV_LOG = os.path.join(TMP, "bridge-env.log")
    NOTIFY_DIR = os.path.join(TMP, "run", "notify")
    with open(os.path.join(TMP, "config.json"), "w", encoding="utf-8") as fh:
        json.dump({"version": "9.9.9-test", "lang": "ru",
                   "mcp": {"http_enabled": True, "http_port": PORT, "allowed_origins": ["https://trusted.example"]}}, fh)
    write_agents(grants_doc())
    PROC, STDERR = start_server(PORT, ["--extra-bind", "127.0.0.1", "--extra-ports", "auto"])
    if not wait_port(PORT) or not wait_port(PORT_GRANT):
        PROC.kill()
        STDERR.close()
        with open(STDERR.name, "rb") as fh:
            raise RuntimeError("HTTP-сервер не поднялся:\n" + fh.read().decode("utf-8", "replace"))


def tearDownModule():
    global PROC
    if PROC is not None and PROC.poll() is None:
        PROC.send_signal(signal.SIGTERM)
        try:
            PROC.wait(timeout=10)
        except subprocess.TimeoutExpired:
            PROC.kill()
    if STDERR is not None:
        STDERR.close()
    shutil.rmtree(TMP, ignore_errors=True)


def next_ip():
    _IP_COUNTER[0] += 1
    return "198.51.100.%d" % (1 + _IP_COUNTER[0] % 250) if _IP_COUNTER[0] < 250 else "192.0.2.%d" % (_IP_COUNTER[0] % 250)


def raw_request(method, path, body=None, headers=None, port=None, https=False):
    """Один HTTP-запрос → (status, headers, body). https — через доверяющий всему контекст (тест TLS)."""
    port = port or PORT
    if https:
        conn = http.client.HTTPSConnection("127.0.0.1", port, timeout=30, context=ssl._create_unverified_context())
    else:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        data = resp.read()
        return resp.status, dict((k.lower(), v) for k, v in resp.getheaders()), data
    finally:
        conn.close()


def rpc(secret, message, ip=None, headers=None, port=None, https=False, token=None):
    """POST JSON-RPC на адрес доступа; ip → X-Real-IP (с петли сервер ему верит, как nginx);
    token → Authorization: Bearer <token> (второй фактор)."""
    body = message if isinstance(message, (bytes, str)) else json.dumps(message, ensure_ascii=False)
    if isinstance(body, str):
        body = body.encode("utf-8")
    hdrs = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL, "User-Agent": "test-http-client/1"}
    hdrs["X-Real-IP"] = ip or next_ip()
    if token is not None:
        hdrs["Authorization"] = "Bearer " + token
    hdrs.update(headers or {})
    return raw_request("POST", "/a-%s/mcp" % secret, body, hdrs, port=port, https=https)


def notify_events(event=None, agent=None):
    """Очередь уведомлений (docs/BOT.md §6) как список объектов: файлы читаем, но не
    удаляем — в проде их забирает бот, а тесту важно только содержимое."""
    out = []
    try:
        names = sorted(os.listdir(NOTIFY_DIR))
    except OSError:
        return out
    for name in names:
        if not name.endswith(".json") or name.startswith(".tmp."):
            continue
        try:
            with open(os.path.join(NOTIFY_DIR, name), "r", encoding="utf-8") as fh:
                obj = json.loads(fh.read().strip().splitlines()[0])
        except (OSError, ValueError, IndexError):
            continue
        if event is not None and obj.get("event") != event:
            continue
        if agent is not None and obj.get("agent") != agent:
            continue
        out.append(obj)
    return out


# ── Одноразовый ключ: свои доступы на каждый тест ───────────────────────────
# Часть проверок замораживает доступ навсегда, поэтому общий на всех доступ не
# годится: порядок тестов внутри модуля не гарантирован, и один тест ронял бы
# соседние. Каждый заводит свой и убирает за собой.
_ROLL = [0]


def key_hash(key):
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def new_nonce(tag="n"):
    _ROLL[0] += 1
    return ("%s%08d" % (tag, _ROLL[0])).ljust(12, "0")


class Rolling(object):
    """Доступ с одноразовым ключом: id, адрес, токен и текущий ключ цепочки."""

    def __init__(self, scope="write", **extra):
        _ROLL[0] += 1
        seq = _ROLL[0]
        self.id = "a-%04x" % (0xd000 + seq)
        self.secret = ("s%03d" % seq).ljust(43, "x")
        self.token = ("t%03d" % seq).ljust(43, "y")
        self.key = ("k%03d" % seq).ljust(43, "z")
        self.first = self.key
        rec = {"id": self.id, "label": "Одноразовый %d" % seq, "secret": self.secret, "token": self.token,
               "require_token": True, "mode": "rolling", "key_hash": key_hash(self.key), "compromised": False,
               "scope": scope, "enabled": True, "created": "2026-09-11T10:00:00Z", "expires": "",
               "port": 0, "allow_ips": [], "pin_ip": False, "pinned_ip": "", "known_ips": [],
               "last_used": "", "last_ip": "", "calls": 0}
        rec.update(extra)
        # Переданные извне id/секрет/токен — это и есть поля объекта: иначе drop() не нашёл
        # бы запись, а post() ходил бы по чужому адресу.
        self.id, self.secret, self.token = rec["id"], rec["secret"], rec["token"]
        doc = read_agents()
        doc["agents"].append(rec)
        write_agents(doc)
        try:
            os.unlink(self.state_path())
        except OSError:
            pass

    def state_path(self):
        return os.path.join(TMP, "state", "agents", self.id + ".key")

    def record(self):
        return dict((r["id"], r) for r in read_agents()["agents"]).get(self.id, {})

    def patch(self, **fields):
        doc = read_agents()
        for rec in doc["agents"]:
            if rec["id"] == self.id:
                rec.update(fields)
        write_agents(doc)

    def drop(self):
        doc = read_agents()
        doc["agents"] = [r for r in doc["agents"] if r["id"] != self.id]
        write_agents(doc)
        try:
            os.unlink(self.state_path())
        except OSError:
            pass

    def post(self, message, key=None, nonce=None, ip=None, token=None, follow=True):
        """Один запрос цепочки → (status, headers, body). follow — принять выданный ключ."""
        headers = {"X-Ga-Key": self.key if key is None else key,
                   "X-Ga-Nonce": new_nonce() if nonce is None else nonce}
        answer = rpc(self.secret, message, ip=ip, headers=headers,
                     token=self.token if token is None else token)
        issued = answer[1].get("x-ga-next-key")
        if follow and issued:
            self.key = issued
        return answer


def audit_lines(agent_id):
    """Строки аудита этого доступа: ими проверяется, что действие НЕ выполнено дважды."""
    try:
        with open(AUDIT_LOG, "r", encoding="utf-8") as fh:
            return [line for line in fh if ("agent=%s" % agent_id) in line]
    except OSError:
        return []


def req(msg_id, method, params=None):
    message = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def call(msg_id, tool, arguments=None):
    return req(msg_id, "tools/call", {"name": tool, "arguments": arguments if arguments is not None else {}})


def tool_names(secret, ip=None, port=None):
    status, _, body = rpc(secret, req(1, "tools/list"), ip=ip, port=port)
    assert status == 200, (status, body[:200])
    return sorted(t["name"] for t in json.loads(body)["result"]["tools"])


class ScopeTests(unittest.TestCase):
    def test_tools_list_by_scope(self):
        self.assertEqual(tool_names(SECRETS["a-read"]), READ_NAMES)
        self.assertEqual(tool_names(SECRETS["a-write"]), WRITE_NAMES)
        self.assertEqual(tool_names(SECRETS["a-admin"]), ALL_NAMES)
        # read: только чтение и без секретов — ни выдачи ключей, ни создания пользователя
        self.assertNotIn("user_get_key", READ_NAMES)
        self.assertNotIn("user_add", READ_NAMES)
        self.assertIn("users_list", READ_NAMES)
        # write: выдача ключей есть, разрушительного нет
        self.assertIn("user_get_key", WRITE_NAMES)
        self.assertIn("user_add", WRITE_NAMES)
        self.assertNotIn("user_delete", WRITE_NAMES)
        self.assertIn("user_delete", ALL_NAMES)
        # «Удалить сразу несколько» — уровень user_delete (admin); предпросмотр — чтение без секретов, есть и у read
        self.assertNotIn("users_del_bulk", WRITE_NAMES)
        self.assertIn("users_del_bulk", ALL_NAMES)
        self.assertIn("users_del_bulk_preview", READ_NAMES)

    def test_call_outside_scope_is_forbidden(self):
        # read не может создать пользователя и не получит ключ; write не удалит пользователя даже с confirm
        for secret, tool, args in ((SECRETS["a-read"], "user_add", {"name": "anna"}),
                                   (SECRETS["a-read"], "user_get_key", {"name": "anna", "profile": "awg2"}),
                                   (SECRETS["a-write"], "user_delete", {"name": "anna", "confirm": "anna"})):
            status, _, body = rpc(secret, call(1, tool, args))
            self.assertEqual(status, 200, body[:200])
            err = json.loads(body)["error"]
            self.assertEqual(err["code"], -32003, (tool, err))
            self.assertIn("forbidden", err["message"])
            self.assertEqual(err["data"]["tool"], tool)
        # admin — можно
        status, _, body = rpc(SECRETS["a-admin"], call(2, "user_delete", {"name": "anna", "confirm": "anna"}))
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(body)["result"]["isError"])
        # отклонённая попытка — в аудите со статусом rejected и меткой доступа
        with open(AUDIT_LOG, "r", encoding="utf-8") as fh:
            audit = fh.read()
        # Журнал — построчный JSON одного формата на весь продукт (CONTRACT §7.1).
        recs = [json.loads(l) for l in audit.splitlines() if l.strip()]
        self.assertTrue([r for r in recs
                         if (r.get("tool"), r.get("action")) == ("user_add", "user-add")], audit)
        self.assertTrue([r for r in recs
                         if (r.get("status"), r.get("code")) == ("rejected", "forbidden")], audit)
        self.assertIn("agent=%s" % IDS["a-read"], audit)

    def test_read_scope_call_works_and_audits_agent(self):
        ip = next_ip()
        status, headers, body = rpc(SECRETS["a-write"], call(7, "users_list"), ip=ip)
        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "application/json")
        result = json.loads(body)["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"]["status"], "success")
        with open(AUDIT_LOG, "r", encoding="utf-8") as fh:
            lines = [l for l in fh.read().splitlines() if l.strip()]
        recs = [json.loads(l) for l in lines]
        lines = [r for r in recs
                 if r.get("tool") == "users_list" and r.get("agent") == IDS["a-write"]]
        self.assertTrue(lines, "нет строки аудита с agent=<id write>")
        last = lines[-1]
        self.assertIn("ip=%s" % ip, str(last.get("origin")))
        self.assertEqual(last.get("client"), "test-http-client/1")
        self.assertNotIn("from=", str(last.get("origin")))
        self.assertNotIn(SECRETS["a-write"], json.dumps(last, ensure_ascii=False))

    def test_bridge_is_told_which_agent_acts(self):
        """1c.15: мутация агента обязана ложиться в аудит моста как `mcp:<a-xxxx>`.

        Сервер выставлял мосту только GOAMNEZIA_CALLER, а действующее лицо модули берут из
        GOAMNEZIA_WHO — и без него правка агента значилась в журнале именем `ssh`, то есть
        приписывалась владельцу, сидящему за терминалом.
        """
        status, _, body = rpc(SECRETS["a-write"], call(11, "user_add", {"name": "actor"}), ip=next_ip())
        self.assertEqual(status, 200, body[:200])
        with open(BRIDGE_ENV_LOG, encoding="utf-8") as fh:
            rows = [l.split() for l in fh.read().splitlines() if l.startswith("user-add ")]
        self.assertTrue(rows, "мост не вызывался")
        self.assertEqual(rows[-1][1], "mcp:%s" % IDS["a-write"])
        self.assertNotEqual(rows[-1][1], "ssh")
        self.assertEqual(rows[-1][2], "mcp")

    def test_prompts_filtered_by_scope(self):
        def prompts(secret):
            status, _, body = rpc(secret, req(1, "prompts/list"))
            self.assertEqual(status, 200)
            return sorted(p["name"] for p in json.loads(body)["result"]["prompts"])
        self.assertEqual(prompts(SECRETS["a-admin"]), sorted(p["name"] for p in ACTIONS["prompts"]))
        self.assertEqual(prompts(SECRETS["a-read"]), ["diagnose_user"])        # onboard_user зовёт user_add
        self.assertNotIn("safe_core_update", prompts(SECRETS["a-write"]))     # шаг upstream_update разрушительный
        status, _, body = rpc(SECRETS["a-read"], req(2, "prompts/get", {"name": "onboard_user", "arguments": {"name": "anna"}}))
        self.assertEqual(json.loads(body)["error"]["code"], -32003)

    def test_initialize_and_instructions_by_scope(self):
        status, headers, body = rpc(SECRETS["a-read"], req("init", "initialize", {
            "protocolVersion": PROTOCOL, "capabilities": {}, "clientInfo": {"name": "http-test", "version": "1"}}))
        self.assertEqual(status, 200)
        self.assertNotIn("mcp-session-id", headers)             # сервер без состояния
        init = json.loads(body)["result"]
        self.assertEqual(init["protocolVersion"], PROTOCOL)
        self.assertIn("read", init["instructions"])
        self.assertNotIn("user_delete", init["instructions"])   # разрушительное read не показывается
        status, _, body = rpc(SECRETS["a-admin"], req("init", "initialize", {"protocolVersion": PROTOCOL, "capabilities": {}}))
        self.assertIn("user_delete", json.loads(body)["result"]["instructions"])


class RefusalTests(unittest.TestCase):
    def assert_nginx_404(self, status, headers, body):
        self.assertEqual(status, 404)
        self.assertEqual(body, NGINX_404)
        self.assertEqual(headers["content-type"], "text/html")
        self.assertEqual(headers["server"], "nginx")

    def test_refusals_are_identical_404(self):
        listing = req(1, "tools/list")
        answers = [
            rpc(UNKNOWN_SECRET, listing),                                   # неизвестный секрет
            rpc(SECRETS["a-off"], listing),                                  # выключен
            rpc(SECRETS["a-exp"], listing),                                  # истёк
            rpc(SECRETS["a-ip"], listing, ip="198.51.100.9"),               # чужой IP
            rpc("short", listing),                                           # путь не по форме
            raw_request("POST", "/a-%s/other" % SECRETS["a-write"], b"{}", {"X-Real-IP": next_ip()}),
            raw_request("POST", "/", b"{}", {"X-Real-IP": next_ip()}),
        ]
        for status, headers, body in answers:
            self.assert_nginx_404(status, headers, body)
        # allow_ips: адрес из подсети и точный адрес проходят
        self.assertEqual(rpc(SECRETS["a-ip"], listing, ip="203.0.113.7")[0], 200)
        self.assertEqual(rpc(SECRETS["a-ip"], listing, ip="198.51.100.77")[0], 200)

    def test_broken_allow_ips_lock_everyone_and_say_why(self):
        """1c.8: в белом списке уцелело ноль годных записей — «пусто» обязано значить
        «никого», а не «кого угодно». `203.0.113.10/0` выглядит как «только мой адрес»,
        а разбирается как «любой»; `010.0.0.1` python не читает вовсе, и раньше агент
        оказывался заперт своим же списком без единого слова в журнале."""
        secret = "B" * 43
        doc = read_agents()
        doc["agents"].append(
            {"id": "a-bad0", "label": "Кривой список", "secret": secret, "token": "", "require_token": False,
             "scope": "read", "enabled": True, "created": "2026-09-08T10:00:00Z", "expires": "", "port": 0,
             "allow_ips": ["010.0.0.1", "203.0.113.10/0"], "pin_ip": False, "pinned_ip": "", "known_ips": [],
             "mode": "static", "key_hash": "", "compromised": False,
             "last_used": "", "last_ip": "", "calls": 0})
        write_agents(doc)
        try:
            listing = req(1, "tools/list")
            # ни «свой» адрес из записи с /0, ни какой-либо другой доступ не открывают
            self.assertEqual(rpc(secret, listing, ip="203.0.113.10")[0], 404)
            self.assertEqual(rpc(secret, listing, ip="192.0.2.61")[0], 404)
            self.assertEqual(rpc(secret, listing, ip="198.51.100.61")[0], 404)
        finally:
            doc = read_agents()
            doc["agents"] = [r for r in doc["agents"] if r["id"] != "a-bad0"]
            write_agents(doc)
        STDERR.flush()
        with open(STDERR.name, "rb") as fh:
            log = fh.read().decode("utf-8", "replace")
        self.assertIn("a-bad0", log)                       # причина названа, а не молчаливый 404
        self.assertIn("010.0.0.1", log)
        self.assertIn("203.0.113.10/0", log)

    def test_get_is_405_and_other_methods(self):
        status, headers, body = raw_request("GET", "/a-%s/mcp" % SECRETS["a-write"], headers={"X-Real-IP": next_ip()})
        self.assertEqual(status, 405)
        self.assertEqual(headers["allow"], "POST")
        # Чужой путь верной формы — тот же 404, что у сайта: 405 выдавал сканеру, что здесь MCP (аудит 25.09.2026)
        status, headers, body = raw_request("GET", "/a-%s/mcp" % UNKNOWN_SECRET, headers={"X-Real-IP": next_ip()})
        self.assert_nginx_404(status, headers, body)
        status, headers, body = raw_request("GET", "/robots.txt", headers={"X-Real-IP": next_ip()})
        self.assert_nginx_404(status, headers, body)
        status, _, _ = raw_request("DELETE", "/a-%s/mcp" % SECRETS["a-write"], headers={"X-Real-IP": next_ip()})
        self.assertEqual(status, 405)

    def test_foreign_origin_is_403(self):
        listing = req(1, "tools/list")
        status, _, body = rpc(SECRETS["a-write"], listing, headers={"Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        self.assertIn("origin", json.loads(body)["error"])
        # Origin с тем же хостом, что Host, и Origin из mcp.allowed_origins — проходят
        self.assertEqual(rpc(SECRETS["a-write"], listing, headers={"Origin": "http://127.0.0.1:%d" % PORT})[0], 200)
        self.assertEqual(rpc(SECRETS["a-write"], listing, headers={"Origin": "https://127.0.0.1", "Host": "127.0.0.1"})[0], 200)
        self.assertEqual(rpc(SECRETS["a-write"], listing, headers={"Origin": "https://trusted.example"})[0], 200)
        self.assertEqual(rpc(SECRETS["a-write"], listing, headers={"Origin": "null"})[0], 403)
        # неизвестный секрет с чужим Origin — всё равно 404: существование пути не подтверждаем
        self.assertEqual(rpc(UNKNOWN_SECRET, listing, headers={"Origin": "https://evil.example"})[0], 404)

    def test_ban_after_twenty_failures(self):
        ip = "192.0.2.199"
        for _ in range(20):
            self.assertEqual(rpc(UNKNOWN_SECRET, req(1, "ping"), ip=ip)[0], 404)
        # 21-й запрос — уже с ВЕРНЫМ секретом — получает тот же 404: адрес забанен
        status, headers, body = rpc(SECRETS["a-admin"], req(1, "ping"), ip=ip)
        self.assertEqual(status, 404)
        self.assertEqual(body, NGINX_404)
        # другой адрес тем же секретом работает — бан по IP, а не по доступу
        self.assertEqual(rpc(SECRETS["a-admin"], req(1, "ping"), ip="192.0.2.200")[0], 200)

    def test_body_limits_and_bad_json(self):
        status, _, body = rpc(SECRETS["a-write"], b"{not json", ip=next_ip())
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"]["code"], -32700)
        status, _, _ = raw_request("POST", "/a-%s/mcp" % SECRETS["a-write"], b"{}",
                                   {"X-Real-IP": next_ip(), "Content-Length": str(2 * 1024 * 1024)})
        self.assertEqual(status, 413)
        status, _, body = rpc(SECRETS["a-write"], req(1, "ping"), headers={"MCP-Protocol-Version": "1999-01-01"})
        self.assertEqual(status, 400)


class LiveReloadAndAccountingTests(unittest.TestCase):
    def test_agents_json_edit_is_seen_by_next_request(self):
        listing = req(1, "tools/list")
        self.assertEqual(rpc(SECRETS["a-write"], listing)[0], 200)
        doc = read_agents()
        for rec in doc["agents"]:
            if rec["id"] == IDS["a-write"]:
                rec["enabled"] = False
        write_agents(doc)
        try:
            self.assertEqual(rpc(SECRETS["a-write"], listing)[0], 404)       # отзыв мгновенный
            doc = read_agents()
            for rec in doc["agents"]:
                if rec["id"] == IDS["a-write"]:
                    rec["enabled"] = True
                    rec["scope"] = "read"                                     # и смена уровня — тоже
            write_agents(doc)
            self.assertEqual(tool_names(SECRETS["a-write"]), READ_NAMES)
            # перевыпуск: старый секрет умирает, новый работает
            new_secret = "N" * 43
            doc = read_agents()
            for rec in doc["agents"]:
                if rec["id"] == IDS["a-write"]:
                    rec["secret"] = new_secret
                    rec["scope"] = "write"
            write_agents(doc)
            self.assertEqual(rpc(SECRETS["a-write"], listing)[0], 404)
            self.assertEqual(tool_names(new_secret), WRITE_NAMES)
        finally:
            doc = read_agents()
            for rec in doc["agents"]:
                if rec["id"] == IDS["a-write"]:
                    rec["enabled"] = True
                    rec["scope"] = "write"
                    rec["secret"] = SECRETS["a-write"]
            write_agents(doc)
        self.assertEqual(rpc(SECRETS["a-write"], listing)[0], 200)

    def test_last_used_last_ip_calls_grow(self):
        before = dict((r["id"], r) for r in read_agents()["agents"])[IDS["a-cnt"]]
        self.assertEqual(before["calls"], 0)
        self.assertEqual(rpc(SECRETS["a-cnt"], req(1, "ping"), ip="203.0.113.50")[0], 200)
        self.assertEqual(rpc(SECRETS["a-cnt"], req(2, "tools/list"), ip="203.0.113.51")[0], 200)
        self.assertEqual(rpc(SECRETS["a-cnt"], call(3, "users_list"), ip="203.0.113.52")[0], 200)
        after = dict((r["id"], r) for r in read_agents()["agents"])[IDS["a-cnt"]]
        self.assertEqual(after["calls"], 3)
        self.assertEqual(after["last_ip"], "203.0.113.52")
        self.assertRegex(after["last_used"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertEqual(after["secret"], SECRETS["a-cnt"])                     # запись не потеряла полей
        self.assertEqual(oct(os.stat(AGENTS_JSON).st_mode & 0o777), "0o600")   # права после перезаписи
        # отказ статистику не трогает
        self.assertEqual(rpc(SECRETS["a-off"], req(1, "ping"))[0], 404)
        self.assertEqual(dict((r["id"], r) for r in read_agents()["agents"])[IDS["a-off"]]["calls"], 0)


class ListenerTests(unittest.TestCase):
    def test_personal_port_accepts_only_its_grant(self):
        listing = req(1, "tools/list")
        # на общем порту доступ с портом принимается (mcp.port_exclusive не включён)
        self.assertEqual(rpc(SECRETS["a-port"], listing)[0], 200)
        # на своём порту — принимается, чужой доступ на нём — 404 как неизвестный
        self.assertEqual(rpc(SECRETS["a-port"], listing, port=PORT_GRANT)[0], 200)
        status, headers, body = rpc(SECRETS["a-write"], listing, port=PORT_GRANT)
        self.assertEqual(status, 404)
        self.assertEqual(body, NGINX_404)
        self.assertEqual(rpc(SECRETS["a-admin"], listing, port=PORT_GRANT)[0], 404)

    def test_batch_and_notifications(self):
        # Пакет по HTTP отвергается целиком: лимит частоты и поворот ключа считаются на запрос, и пакет
        # обходил их (аудит безопасности 25.09.2026). Ни один элемент не выполняется.
        status, _, body = rpc(SECRETS["a-write"], [req(1, "ping"), req(2, "ping")])
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"]["code"], -32600)
        self.assertIn("batch", json.loads(body)["error"]["message"])
        status, _, body = rpc(SECRETS["a-write"], {"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertEqual(status, 202)
        self.assertEqual(body, b"")
        # неизвестный метод — обычная ошибка JSON-RPC при HTTP 200
        status, _, body = rpc(SECRETS["a-write"], req(3, "tools/explode"))
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["error"]["code"], -32601)

    def test_keep_alive_connection_serves_several_requests(self):
        conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=30)
        try:
            for i in range(3):
                conn.request("POST", "/a-%s/mcp" % SECRETS["a-write"], body=json.dumps(req(i, "ping")),
                             headers={"Content-Type": "application/json", "X-Real-IP": "203.0.113.90"})
                resp = conn.getresponse()
                self.assertEqual(resp.status, 200)
                self.assertEqual(json.loads(resp.read())["id"], i)
        finally:
            conn.close()

    def test_secret_never_appears_in_server_log(self):
        STDERR.flush()
        with open(STDERR.name, "rb") as fh:
            log = fh.read().decode("utf-8", "replace")
        self.assertIn("слушаю 127.0.0.1:%d" % PORT, log)
        for secret in SECRETS.values():
            self.assertNotIn(secret, log)
        self.assertNotIn(UNKNOWN_SECRET, log)
        self.assertIn("/a-***", log)


class SecondFactorTests(unittest.TestCase):
    """Токен в заголовке Authorization (CONTRACT §5.1).

    Смысл второго фактора: адрес утекает буднично (снимок экрана, пересылка, история
    браузера, журнал прокси, конфиг агента на чужой машине), заголовок — нет. Поэтому
    верный адрес без верного токена обязан выглядеть как несуществующий путь.
    """

    def assert_nginx_404(self, answer):
        status, headers, body = answer
        self.assertEqual(status, 404)
        self.assertEqual(body, NGINX_404)
        self.assertEqual(headers["content-type"], "text/html")
        self.assertEqual(headers["server"], "nginx")

    def test_url_alone_is_not_enough(self):
        listing = req(1, "tools/list")
        # Ровно тот же 404, что у неизвестного пути: 401 подтвердил бы, что путь существует
        self.assert_nginx_404(rpc(SECRETS["a-tok"], listing))                       # без заголовка
        self.assert_nginx_404(rpc(SECRETS["a-tok"], listing, token=WRONG_TOKEN))    # чужой токен
        self.assert_nginx_404(rpc(SECRETS["a-tok"], listing, token=""))             # пустой Bearer
        self.assert_nginx_404(rpc(SECRETS["a-tok"], listing, headers={"Authorization": "Basic " + TOKENS["a-tok"]}))
        self.assert_nginx_404(rpc(SECRETS["a-tok"], listing, token=TOKENS["a-pin"]))  # токен другого доступа
        # Верный токен — обычная работа, уровень доступа тот же
        status, _, body = rpc(SECRETS["a-tok"], listing, token=TOKENS["a-tok"])
        self.assertEqual(status, 200, body[:200])
        self.assertEqual(sorted(t["name"] for t in json.loads(body)["result"]["tools"]), WRITE_NAMES)
        # Схема Bearer регистронезависима (RFC 7235) — клиенты пишут по-разному
        self.assertEqual(rpc(SECRETS["a-tok"], listing, headers={"Authorization": "bearer " + TOKENS["a-tok"]})[0], 200)

    def test_legacy_grant_without_require_token_still_works(self):
        """Доступ, выданный до второго фактора: require_token в записи нет вовсе."""
        record = dict((r["id"], r) for r in read_agents()["agents"])[IDS["a-write"]]
        self.assertNotIn("require_token", record)
        self.assertNotIn("token", record)
        self.assertEqual(rpc(SECRETS["a-write"], req(1, "tools/list"))[0], 200)
        # И не ломается, если клиент всё же прислал какой-то заголовок
        self.assertEqual(rpc(SECRETS["a-write"], req(2, "ping"), token=WRONG_TOKEN)[0], 200)

    def test_require_token_without_token_in_record_is_refused(self):
        """Испорченная запись (require_token без токена) не должна тихо понижаться до пути."""
        doc = read_agents()
        for rec in doc["agents"]:
            if rec["id"] == IDS["a-tok"]:
                rec["token"] = ""
        write_agents(doc)
        try:
            self.assert_nginx_404(rpc(SECRETS["a-tok"], req(1, "ping"), token=TOKENS["a-tok"]))
            self.assert_nginx_404(rpc(SECRETS["a-tok"], req(2, "ping")))
        finally:
            doc = read_agents()
            for rec in doc["agents"]:
                if rec["id"] == IDS["a-tok"]:
                    rec["token"] = TOKENS["a-tok"]
            write_agents(doc)
        self.assertEqual(rpc(SECRETS["a-tok"], req(3, "ping"), token=TOKENS["a-tok"])[0], 200)

    def test_expired_grant_is_404_even_with_token(self):
        doc = read_agents()
        for rec in doc["agents"]:
            if rec["id"] == IDS["a-tok"]:
                rec["expires"] = "2026-02-01T00:00:00Z"
        write_agents(doc)
        try:
            self.assert_nginx_404(rpc(SECRETS["a-tok"], req(1, "ping"), token=TOKENS["a-tok"]))
        finally:
            doc = read_agents()
            for rec in doc["agents"]:
                if rec["id"] == IDS["a-tok"]:
                    rec["expires"] = ""
            write_agents(doc)
        self.assertEqual(rpc(SECRETS["a-tok"], req(2, "ping"), token=TOKENS["a-tok"])[0], 200)

    def test_secret_and_token_never_appear_in_logs(self):
        rpc(SECRETS["a-tok"], req(1, "ping"), token=TOKENS["a-tok"])
        STDERR.flush()
        with open(STDERR.name, "rb") as fh:
            log = fh.read().decode("utf-8", "replace")
        for value in list(TOKENS.values()) + [WRONG_TOKEN]:
            self.assertNotIn(value, log)
        with open(AUDIT_LOG, "r", encoding="utf-8") as fh:
            audit = fh.read()
        for value in list(TOKENS.values()) + list(SECRETS.values()):
            self.assertNotIn(value, audit)


class PinnedAddressTests(unittest.TestCase):
    """pin_ip: доступ запоминает адрес первого использования (CONTRACT §5.1).

    Отдельная страховка на случай, когда утекли и адрес, и токен: угонщику нужен
    ещё и тот же выходной IP.
    """

    def setUp(self):
        # Каждый тест начинает с «привязка включена, адрес ещё не закреплён»
        doc = read_agents()
        for rec in doc["agents"]:
            if rec["id"] == IDS["a-pin"]:
                rec["pin_ip"] = True
                rec["pinned_ip"] = ""
                rec["known_ips"] = []
        write_agents(doc)

    def test_first_address_is_pinned_and_others_are_404(self):
        first, other = "203.0.113.201", "203.0.113.202"
        self.assertEqual(rpc(SECRETS["a-pin"], req(1, "ping"), ip=first, token=TOKENS["a-pin"])[0], 200)
        record = dict((r["id"], r) for r in read_agents()["agents"])[IDS["a-pin"]]
        self.assertEqual(record["pinned_ip"], first)          # закрепился ровно первый
        self.assertEqual(rpc(SECRETS["a-pin"], req(2, "ping"), ip=first, token=TOKENS["a-pin"])[0], 200)
        status, _, body = rpc(SECRETS["a-pin"], req(3, "ping"), ip=other, token=TOKENS["a-pin"])
        self.assertEqual(status, 404)                          # верный адрес и верный токен — мало
        self.assertEqual(body, NGINX_404)

    def test_unpinning_returns_access(self):
        first, other = "203.0.113.211", "203.0.113.212"
        self.assertEqual(rpc(SECRETS["a-pin"], req(1, "ping"), ip=first, token=TOKENS["a-pin"])[0], 200)
        self.assertEqual(rpc(SECRETS["a-pin"], req(2, "ping"), ip=other, token=TOKENS["a-pin"])[0], 404)
        # Снятие привязки — как это делает agents_pin <id> off
        doc = read_agents()
        for rec in doc["agents"]:
            if rec["id"] == IDS["a-pin"]:
                rec["pin_ip"] = False
                rec["pinned_ip"] = ""
        write_agents(doc)
        self.assertEqual(rpc(SECRETS["a-pin"], req(3, "ping"), ip=other, token=TOKENS["a-pin"])[0], 200)
        # Повторное включение закрепляет уже новый адрес
        doc = read_agents()
        for rec in doc["agents"]:
            if rec["id"] == IDS["a-pin"]:
                rec["pin_ip"] = True
                rec["pinned_ip"] = ""
        write_agents(doc)
        self.assertEqual(rpc(SECRETS["a-pin"], req(4, "ping"), ip=other, token=TOKENS["a-pin"])[0], 200)
        record = dict((r["id"], r) for r in read_agents()["agents"])[IDS["a-pin"]]
        self.assertEqual(record["pinned_ip"], other)


class NotifyTests(unittest.TestCase):
    """Очередь событий владельцу (docs/BOT.md §6): угон должен быть заметен."""

    def test_first_use_new_ip_and_token_alarm(self):
        agent = IDS["a-ntf"]
        self.assertEqual(notify_events(agent=agent), [])
        first, second = "203.0.113.221", "203.0.113.222"
        self.assertEqual(rpc(SECRETS["a-ntf"], req(1, "ping"), ip=first, token=TOKENS["a-ntf"])[0], 200)
        events = notify_events("agent_first_use", agent)
        self.assertEqual(len(events), 1, notify_events(agent=agent))
        self.assertEqual(events[0]["ip"], first)
        self.assertEqual(events[0]["label"], "Для уведомлений")
        self.assertRegex(events[0]["ts"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        # Тот же адрес второй раз — молча: иначе уведомления перестали бы читать
        self.assertEqual(rpc(SECRETS["a-ntf"], req(2, "ping"), ip=first, token=TOKENS["a-ntf"])[0], 200)
        self.assertEqual(len(notify_events("agent_first_use", agent)), 1)
        self.assertEqual(notify_events("agent_new_ip", agent), [])
        # Новый адрес — событие
        self.assertEqual(rpc(SECRETS["a-ntf"], req(3, "ping"), ip=second, token=TOKENS["a-ntf"])[0], 200)
        events = notify_events("agent_new_ip", agent)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["ip"], second)
        # Верный путь с чужим токеном — тревога «адрес утёк»
        self.assertEqual(rpc(SECRETS["a-ntf"], req(4, "ping"), ip=second, token=WRONG_TOKEN)[0], 404)
        alarms = notify_events("agent_token_failed", agent)
        self.assertEqual(len(alarms), 1)
        self.assertEqual(alarms[0]["ip"], second)
        # Повторная попытка не размножает тревогу (не чаще раза в час на доступ)
        self.assertEqual(rpc(SECRETS["a-ntf"], req(5, "ping"), ip=second, token=WRONG_TOKEN)[0], 404)
        self.assertEqual(len(notify_events("agent_token_failed", agent)), 1)
        # Ни адреса, ни токена в событиях нет: они уходят в чат
        for ev in notify_events(agent=agent):
            text = json.dumps(ev, ensure_ascii=False)
            self.assertNotIn(SECRETS["a-ntf"], text)
            self.assertNotIn(TOKENS["a-ntf"], text)


class RollingKeyTests(unittest.TestCase):
    """Одноразовый ключ: вращение, клон, мусор, потерянный ответ (MCP_ROTATION §1 и §11).

    Смысл всего раздела в одной строке: скопированный секрет обезвредить нельзя, но раздвоенную
    цепочку видно. Поэтому проверяется не «пустит или нет», а РАЗЛИЧЕНИЕ: ключ из истории — клон
    и заморозка; ключ, которого сервер не выдавал, — обычный отказ, доступ продолжает работать.
    """

    def assert_nginx_404(self, answer):
        status, headers, body = answer
        self.assertEqual(status, 404)
        self.assertEqual(body, NGINX_404)
        self.assertEqual(headers["content-type"], "text/html")

    def test_chain_rotates_and_next_key_comes_in_header(self):
        grant = Rolling()
        self.addCleanup(grant.drop)
        seen = [grant.key]
        for i in range(4):
            status, headers, body = grant.post(req(i, "tools/list"))
            self.assertEqual(status, 200, body[:200])
            issued = headers.get("x-ga-next-key")
            self.assertIsNotNone(issued, "следующий ключ обязан приезжать заголовком X-Ga-Next-Key")
            self.assertRegex(issued, r"^[A-Za-z0-9_-]{43}$")
            self.assertNotIn(issued, seen, "каждый запрос обязан выдавать НОВЫЙ ключ")
            # Ключ едет ЗАГОЛОВКОМ, а не в теле: тело клиенты MCP пишут в свои журналы целиком
            self.assertNotIn(issued.encode("utf-8"), body)
            seen.append(issued)
        # Цепочка живёт отдельным файлом на доступ, и это НЕ agents.json
        self.assertTrue(os.path.isfile(grant.state_path()))
        self.assertEqual(oct(os.stat(grant.state_path()).st_mode & 0o777), "0o600")
        # Реестр от вращения не двигается: в нём по-прежнему отпечаток ПЕРВОГО ключа (§11.6)
        self.assertEqual(grant.record().get("key_hash"), key_hash(grant.first))
        # И ни один выданный ключ в реестре не лежит — там только отпечаток начала цепочки
        registry = json.dumps(grant.record(), ensure_ascii=False)
        for issued in seen:
            self.assertNotIn(issued, registry)
        # Ключи не должны попадать ни в журнал сервера, ни в аудит: журналы уезжают в бэкапы
        STDERR.flush()
        with open(STDERR.name, "rb") as fh:
            log = fh.read().decode("utf-8", "replace")
        audit = "".join(audit_lines(grant.id))
        for issued in seen:
            self.assertNotIn(issued, log)
            self.assertNotIn(issued, audit)

    def test_missing_key_headers_are_404_without_freezing(self):
        grant = Rolling()
        self.addCleanup(grant.drop)
        # Обращение как к static-доступу: адрес и токен верные, ключа нет вовсе
        self.assert_nginx_404(rpc(grant.secret, req(1, "ping"), token=grant.token))
        # Номер запроса тоже обязателен: без него повтор нельзя отличить от нового запроса
        self.assert_nginx_404(grant.post(req(2, "ping"), nonce="", follow=False))
        self.assertFalse(grant.record().get("compromised"))
        self.assertEqual(grant.post(req(3, "ping"))[0], 200)

    def test_garbage_key_does_not_kill_the_grant(self):
        """Самая важная строка §11.1: чужой мусор не должен быть кнопкой «выключить агента»."""
        grant = Rolling()
        self.addCleanup(grant.drop)
        self.assertEqual(grant.post(req(1, "ping"))[0], 200)
        for junk in ("q" * 43, "Z" * 43, "-" * 43):
            self.assert_nginx_404(grant.post(req(2, "ping"), key=junk, follow=False))
            self.assertFalse(grant.record().get("compromised"),
                             "ключ, которого сервер не выдавал, — не доказательство клона")
        # Цепочка на месте: честный агент продолжает работать своим ключом
        self.assertEqual(grant.post(req(3, "ping"))[0], 200)

    def test_key_from_history_is_a_clone_and_freezes_the_grant(self):
        """Вор молчал, честный агент работал: вор приходит со старым ключом (§1, первый случай)."""
        grant = Rolling()
        self.addCleanup(grant.drop)
        stolen = grant.first
        for i in range(4):
            self.assertEqual(grant.post(req(i, "ping"))[0], 200)
        honest = grant.key
        self.assert_nginx_404(grant.post(req(9, "ping"), key=stolen, follow=False))
        record = grant.record()
        self.assertTrue(record.get("compromised"), "ключ из истории обязан заморозить доступ")
        self.assertTrue(record.get("compromised_at"))
        self.assertTrue(record.get("compromised_ip"))
        # Заморозка, а НЕ удаление (§11.3): запись на месте, владельцу есть что решать
        self.assertIn(grant.id, [r["id"] for r in read_agents()["agents"]])
        # Замороженный доступ не впускает больше никого — в том числе честного агента
        self.assert_nginx_404(grant.post(req(10, "ping"), key=honest, follow=False))
        # Владельцу ушла карточка: два времени и два адреса, без секретов
        events = notify_events(event="agent_compromised", agent=grant.id)
        self.assertEqual(len(events), 1, events)
        event = events[0]
        self.assertTrue(event.get("clone_at") and event.get("clone_ip"))
        self.assertTrue(event.get("last_used") and event.get("last_ip"))
        text = json.dumps(event, ensure_ascii=False)
        for secret in (grant.secret, grant.token, stolen, honest):
            self.assertNotIn(secret, text)

    def test_clone_first_honest_agent_second(self):
        """Вор пришёл первым: один запрос он успевает, второй ход раскрывает его (§1, случай два)."""
        grant = Rolling()
        self.addCleanup(grant.drop)
        stolen = grant.first
        status, headers, _ = grant.post(req(1, "ping"), key=stolen)
        self.assertEqual(status, 200)
        self.assertIsNotNone(headers.get("x-ga-next-key"))
        # Честный агент приходит со своим (тем же) ключом, но со своим номером запроса —
        # это уже не дословный повтор, а второй запрос сожжённым ключом.
        self.assert_nginx_404(grant.post(req(2, "ping"), key=stolen, nonce=new_nonce("h"), follow=False))
        self.assertTrue(grant.record().get("compromised"))
        self.assertEqual(len(notify_events(event="agent_compromised", agent=grant.id)), 1)

    def _at_once(self, grant, messages, key, nonces, ip=None):
        """Отправить несколько запросов ОДНИМ ключом, не дожидаясь ответов → список ответов.

        Соединения открываются заранее, поэтому запросы уходят подряд за микросекунды, а между
        ними у сервера замок на доступ, запись agents.json и fsync состояния цепочки: второй
        заведомо ПРИХОДИТ раньше, чем уходит ответ на первый. Ровно так ведёт себя клиент MCP
        сразу после initialize — и ровно это раньше объявлялось клоном гарантированно.
        """
        conns = []
        for _ in messages:
            conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=30)
            conn.connect()
            conns.append(conn)
        time.sleep(0.05)          # дать серверу принять оба соединения и встать на чтение
        ip = ip or next_ip()
        for conn, message, nonce in zip(conns, messages, nonces):
            conn.request("POST", "/a-%s/mcp" % grant.secret,
                         body=json.dumps(message, ensure_ascii=False).encode("utf-8"),
                         headers={"Content-Type": "application/json", "MCP-Protocol-Version": PROTOCOL,
                                  "User-Agent": "test-http-client/1", "X-Real-IP": ip,
                                  "Authorization": "Bearer " + grant.token,
                                  "X-Ga-Key": key, "X-Ga-Nonce": nonce})
        answers = []
        for conn in conns:
            resp = conn.getresponse()
            answers.append((resp.status, dict((k.lower(), v) for k, v in resp.getheaders()), resp.read()))
            conn.close()
        return answers

    def test_two_requests_at_once_are_not_a_clone(self):
        """Главная проверка §11.8: честный клиент, пославший два запроса разом, не клон.

        Оба запроса несут ОДИН ключ (нового второй ещё не видел) и свои номера. Это та же картина
        на проводе, что у копии доступа, и различает их единственный признак: второй пришёл
        раньше, чем ушёл ответ с новым ключом, — значит следующего ключа он знать не мог.
        """
        grant = Rolling()
        self.addCleanup(grant.drop)
        self.assertEqual(grant.post(req(1, "ping"))[0], 200)
        key = grant.key
        answers = self._at_once(grant, [call(2, "users_list"), call(3, "users_list")],
                                key, [new_nonce("pa"), new_nonce("pb")])
        for status, headers, body in answers:
            self.assertEqual(status, 200, body[:300])
            self.assertIsNotNone(headers.get("x-ga-next-key"), "одновременный запрос тоже обязан вести цепочку")
        issued = set(h.get("x-ga-next-key") for _, h, _ in answers)
        self.assertEqual(len(issued), 1, "оба ответа обязаны нести ОДИН и тот же следующий ключ")
        # Главное: доступ жив, тревоги о краже владельцу не ушло
        self.assertFalse(grant.record().get("compromised"), "два запроса разом — не копия доступа")
        self.assertEqual(notify_events(event="agent_compromised", agent=grant.id), [])
        # Цепочка сдвинулась ровно на один шаг, и работа продолжается выданным ключом
        grant.key = issued.pop()
        self.assertEqual(grant.post(req(4, "ping"))[0], 200)
        self.assertFalse(grant.record().get("compromised"))
        # А тот же ключ, посланный ПОЗЖЕ — уже после полученного ответа, — по-прежнему клон:
        # различается не «сколько запросов», а «мог ли отправитель знать новый ключ».
        self.assert_nginx_404(grant.post(req(5, "ping"), key=key, nonce=new_nonce("late"), follow=False))
        self.assertTrue(grant.record().get("compromised"), "запоздавший запрос сожжённым ключом — клон")

    def test_repeat_of_a_simultaneous_request_does_not_run_the_action_twice(self):
        """Ответ на одновременный запрос тоже сохраняется: повтор отдаёт его и НЕ выполняет заново."""
        grant = Rolling(scope="admin")
        self.addCleanup(grant.drop)
        self.assertEqual(grant.post(req(1, "ping"))[0], 200)
        key = grant.key
        first, second = new_nonce("q1"), new_nonce("q2")
        destructive = call(3, "user_delete", {"name": "anna", "confirm": "anna"})
        before = len(audit_lines(grant.id))
        answers = self._at_once(grant, [call(2, "users_list"), destructive], key, [first, second])
        for status, _, body in answers:
            self.assertEqual(status, 200, body[:300])
        self.assertEqual(len(audit_lines(grant.id)), before + 2, "оба запроса обязаны выполниться по разу")
        # Дословный повтор второго: тот же ответ, тот же следующий ключ, действие НЕ повторено
        again = grant.post(destructive, key=key, nonce=second, follow=False)
        self.assertEqual(again[0], answers[1][0])
        self.assertEqual(again[2], answers[1][2], "повтор обязан вернуть тот же самый ответ")
        self.assertEqual(again[1].get("x-ga-next-key"), answers[1][1].get("x-ga-next-key"))
        self.assertEqual(len(audit_lines(grant.id)), before + 2,
                         "разрушительное действие не должно выполняться второй раз (§11.4 и §11.8)")
        self.assertFalse(grant.record().get("compromised"))

    def test_lost_answer_is_replayed_and_the_action_is_not_repeated(self):
        """Ответ не доехал, агент повторил дословно: тот же ответ, тот же ключ, действие один раз."""
        grant = Rolling(scope="admin")
        self.addCleanup(grant.drop)
        nonce = new_nonce("lost")
        message = call(1, "user_delete", {"name": "anna", "confirm": "anna"})
        before = len(audit_lines(grant.id))
        status, headers, body = grant.post(message, nonce=nonce, follow=False)
        self.assertEqual(status, 200, body[:300])
        issued = headers.get("x-ga-next-key")
        self.assertEqual(len(audit_lines(grant.id)), before + 1)
        # Дословный повтор той же попытки
        again = grant.post(message, nonce=nonce, follow=False)
        self.assertEqual(again[0], status)
        self.assertEqual(again[2], body, "повтор обязан вернуть тот же самый ответ")
        self.assertEqual(again[1].get("x-ga-next-key"), issued, "и тот же самый следующий ключ")
        self.assertEqual(len(audit_lines(grant.id)), before + 1,
                         "разрушительное действие не должно выполняться второй раз (§11.4)")
        # Цепочка не сдвинулась: выданный ключ по-прежнему текущий
        grant.key = issued
        self.assertEqual(grant.post(req(2, "ping"))[0], 200)
        self.assertFalse(grant.record().get("compromised"))

    def test_path_miss_does_not_burn_the_chain(self):
        """Промах мимо пути и мимо срока цепочку не трогает (§11.5)."""
        grant = Rolling()
        self.addCleanup(grant.drop)
        self.assertEqual(grant.post(req(1, "ping"))[0], 200)
        current = grant.key
        self.assert_nginx_404(rpc(UNKNOWN_SECRET, req(2, "ping"), headers={"X-Ga-Key": current,
                                                                          "X-Ga-Nonce": new_nonce()},
                                  token=grant.token))
        self.assert_nginx_404(raw_request("POST", "/a-%s/nope" % grant.secret, b"{}",
                                          {"X-Real-IP": next_ip()}))
        self.assertEqual(grant.key, current)
        self.assertEqual(grant.post(req(3, "ping"))[0], 200)
        self.assertFalse(grant.record().get("compromised"))

    def test_rejected_after_the_key_check_does_not_rotate_the_chain(self):
        """Ключ сошёлся, но запрос отвергнут транспортом — цепочка обязана остаться на месте.

        Иначе чужой Origin или слишком большое тело жгли бы ключ, ничего не выполнив, и агент
        оставался бы позади сервера — то есть со следующего запроса выглядел бы мусором.
        """
        grant = Rolling()
        self.addCleanup(grant.drop)
        current = grant.key
        # чужой Origin: ключ уже проверен, но запрос не выполняется
        answer = rpc(grant.secret, req(2, "ping"), token=grant.token,
                     headers={"X-Ga-Key": current, "X-Ga-Nonce": new_nonce(),
                              "Origin": "https://evil.example", "Host": "127.0.0.1"})
        self.assertEqual(answer[0], 403)
        self.assertIsNone(answer[1].get("x-ga-next-key"))
        # слишком большое тело — то же самое
        answer = rpc(grant.secret, req(3, "ping"), token=grant.token,
                     headers={"X-Ga-Key": current, "X-Ga-Nonce": new_nonce(),
                              "Content-Length": str(2 * 1024 * 1024)})
        self.assertEqual(answer[0], 413)
        self.assertIsNone(answer[1].get("x-ga-next-key"))
        # цепочка на прежнем ключе
        self.assertEqual(grant.post(req(4, "ping"), key=current)[0], 200)
        self.assertFalse(grant.record().get("compromised"))

    def test_success_resets_the_failure_counter(self):
        """Успешная проверка ключа обнуляет счётчик неудач адреса (§11.5)."""
        grant = Rolling()
        self.addCleanup(grant.drop)
        ip = "192.0.2.77"
        for i in range(19):
            self.assert_nginx_404(grant.post(req(i, "ping"), key="q" * 43, ip=ip, follow=False))
        self.assertEqual(grant.post(req(100, "ping"), ip=ip)[0], 200)
        # Без обнуления следующие девятнадцать промахов добили бы до бана (20 за 10 минут)
        for i in range(19):
            self.assert_nginx_404(grant.post(req(i, "ping"), key="q" * 43, ip=ip, follow=False))
        self.assertEqual(grant.post(req(101, "ping"), ip=ip)[0], 200)
        self.assertFalse(grant.record().get("compromised"))

    def test_chain_state_lives_outside_run_and_survives_its_removal(self):
        """Состояние цепочки в постоянном каталоге (§11.6): /run чистится при перезагрузке."""
        grant = Rolling()
        self.addCleanup(grant.drop)
        self.assertEqual(grant.post(req(1, "ping"))[0], 200)
        self.assertTrue(grant.state_path().startswith(os.path.join(TMP, "state")))
        run_dir = os.path.join(TMP, "run")
        shutil.rmtree(run_dir, ignore_errors=True)
        os.makedirs(NOTIFY_DIR, exist_ok=True)
        self.assertTrue(os.path.isfile(grant.state_path()), "цепочка не должна жить в /run")
        self.assertEqual(grant.post(req(2, "ping"))[0], 200)

    def test_reissue_starts_a_new_chain_and_lifts_the_freeze(self):
        """«Это был я, перевыдать»: новый первый ключ, снятая заморозка, мёртвая старая цепочка."""
        grant = Rolling()
        self.addCleanup(grant.drop)
        stolen = grant.first
        self.assertEqual(grant.post(req(1, "ping"))[0], 200)
        self.assertEqual(grant.post(req(2, "ping"))[0], 200)
        old_key = grant.key
        self.assert_nginx_404(grant.post(req(3, "ping"), key=stolen, follow=False))
        self.assertTrue(grant.record().get("compromised"))
        # Ровно то, что делает agents_rotate: новый секрет, новый токен, новый первый ключ,
        # снятая заморозка. Файл состояния прежней цепочки может остаться — сервер обязан
        # отбросить его сам по несовпадению origin.
        grant.secret = "n" + grant.secret[1:]
        grant.token = "m" + grant.token[1:]
        # Новый первый ключ — заведомо не из прежней цепочки (TAIL-18). Прежде он получался из old_key
        # заменой первого знака на «w», а old_key выдал сервер, он случайный — и в одном прогоне из 64
        # сам начинался с «w»: новый первый ключ совпадал со старым, после запроса 4 становился
        # «предыдущим» новой цепочки, и запрос 6 со старым ключом сервер честно объявлял клоном.
        grant.key = grant.first = ("r%03d" % _ROLL[0]).ljust(43, "e")
        self.assertNotIn(grant.key, (stolen, old_key))
        grant.patch(secret=grant.secret, token=grant.token, key_hash=key_hash(grant.key),
                    compromised=False, compromised_at="", compromised_ip="")
        self.assertEqual(grant.post(req(4, "ping"))[0], 200)
        # Ключи прежней цепочки теперь просто неизвестны — и это НЕ повод объявлять клона
        self.assert_nginx_404(grant.post(req(5, "ping"), key=stolen, follow=False))
        self.assert_nginx_404(grant.post(req(6, "ping"), key=old_key, follow=False))
        self.assertFalse(grant.record().get("compromised"))

    def test_broken_rolling_record_is_refused(self):
        """rolling без отпечатка первого ключа — испорченная запись, а не доступ по одному пути."""
        grant = Rolling()
        self.addCleanup(grant.drop)
        grant.patch(key_hash="")
        self.assert_nginx_404(grant.post(req(1, "ping"), follow=False))
        self.assert_nginx_404(rpc(grant.secret, req(2, "ping"), token=grant.token))

    def test_static_grant_keeps_working_and_gets_no_next_key(self):
        """Совместимость: выданное раньше не должно ни сломаться, ни начать вращаться."""
        status, headers, _ = rpc(SECRETS["a-write"], req(1, "tools/list"))
        self.assertEqual(status, 200)
        self.assertIsNone(headers.get("x-ga-next-key"))
        # И лишние заголовки static-доступ не смущают: ключа у него нет, цепочки тоже
        status, headers, _ = rpc(SECRETS["a-write"], req(2, "ping"),
                                 headers={"X-Ga-Key": "q" * 43, "X-Ga-Nonce": new_nonce()})
        self.assertEqual(status, 200)
        self.assertIsNone(headers.get("x-ga-next-key"))


class LogsNarrowingTests(unittest.TestCase):
    """Уровень read через журналы был шире, чем объявлено (DEFECTS §1d.2).

    `logs_tail` помечен readonly и потому целиком попадал в read, а внутри у него два
    компонента, которые «наблюдателю» не принадлежат: `system` — весь журнал машины от root,
    `mcp` — ВЕСЬ аудит-лог (действия владельца, бота, панели и всех остальных агентов), хотя
    руководство обещает агенту «аудит твоих же вызовов».
    """

    def _logs(self, secret, component, token=None):
        status, _, body = rpc(secret, call(1, "logs_tail", {"component": component}), token=token)
        return status, json.loads(body)

    def test_system_journal_is_admin_only(self):
        status, answer = self._logs(SECRETS["a-read"], "system")
        self.assertEqual(status, 200)
        self.assertEqual(answer["error"]["code"], -32003)
        self.assertEqual(answer["error"]["data"]["component"], "system")
        # write — тоже не наблюдатель машины
        _, answer = self._logs(SECRETS["a-write"], "system")
        self.assertEqual(answer["error"]["code"], -32003)
        # admin — можно
        status, answer = self._logs(SECRETS["a-admin"], "system")
        self.assertEqual(status, 200)
        self.assertIn("result", answer)
        self.assertIn("line one", answer["result"]["structuredContent"]["log"])

    def test_audit_shows_only_this_grants_own_lines(self):
        status, answer = self._logs(SECRETS["a-read"], "mcp")
        self.assertEqual(status, 200)
        data = answer["result"]["structuredContent"]
        self.assertEqual(data["narrowed_to_agent"], IDS["a-read"])
        # В журнале четыре строки: две чужого агента, одна бота, одна… ничья. Своих у
        # наблюдателя нет вовсе — и это честный пустой ответ, а не чужой аудит.
        self.assertEqual(data["lines_total"], 4)
        self.assertEqual(data["lines_shown"], 0)
        self.assertEqual(data["log"], "")
        self.assertNotIn("bot:42", data["log"])
        self.assertNotIn("a-0002", data["log"])
        self.assertIn(IDS["a-read"], data["message"])

    def test_own_lines_survive_the_filter(self):
        # Доступ с id, который встречается в журнале, видит ровно свои строки
        grant = Rolling(scope="read", id="a-0001", secret=("q" * 43), token=("w" * 43))
        self.addCleanup(grant.drop)
        status, _, body = grant.post(call(1, "logs_tail", {"component": "mcp"}))
        self.assertEqual(status, 200)
        data = json.loads(body)["result"]["structuredContent"]
        self.assertEqual(data["lines_shown"], 2)
        self.assertEqual(data["lines_total"], 4)
        self.assertEqual(data["log"].count("agent=a-0001"), 2)
        self.assertNotIn("a-0002", data["log"])


class BanAfterFreezeTests(unittest.TestCase):
    """После заморозки честный агент банил свой же адрес (DEFECTS §1d.2).

    Замороженный доступ отвечает отказом на каждый вызов, агент об этом не знает и звонит
    дальше: двадцать отказов набираются за минуты, и его собственный адрес уходит в бан на
    десять минут. Перевыпуск бан не снимал — ответ владельца «это был я» не действовал.
    """

    def test_state_refusals_do_not_feed_the_ban_counter(self):
        ip = "203.0.113.201"
        grant = Rolling(scope="read", compromised=True)
        self.addCleanup(grant.drop)
        for _ in range(HTTP_BAN_FAILURES + 5):
            status, _, body = grant.post(req(1, "ping"), ip=ip, follow=False)
            self.assertEqual(status, 404)
            self.assertEqual(body, NGINX_404)
        # Тот же адрес по ЖИВОМУ доступу работает: бан не сработал
        status, _, _ = rpc(SECRETS["a-read"], req(1, "ping"), ip=ip)
        self.assertEqual(status, 200)

    def test_expired_and_disabled_do_not_ban_either(self):
        ip = "203.0.113.202"
        for secret in (SECRETS["a-exp"], SECRETS["a-off"]):
            for _ in range(HTTP_BAN_FAILURES // 2 + 3):
                status, _, _ = rpc(secret, req(1, "ping"), ip=ip)
                self.assertEqual(status, 404)
        status, _, _ = rpc(SECRETS["a-read"], req(1, "ping"), ip=ip)
        self.assertEqual(status, 200)

    def test_expiry_is_announced_instead_of_silence(self):
        rpc(SECRETS["a-exp"], req(1, "ping"), ip="203.0.113.203")
        events = notify_events("agent_expired", IDS["a-exp"])
        self.assertTrue(events, "истёкший доступ просто замолчал")
        self.assertEqual(events[0]["expires"], "2026-02-01T00:00:00Z")

    def test_reissue_lifts_the_ban_on_the_grants_own_addresses(self):
        ip = "203.0.113.204"
        grant = Rolling(scope="read")
        self.addCleanup(grant.drop)
        grant.patch(known_ips=[ip], last_ip=ip)
        # Адрес в бане: перебор чужих путей с него (это по-прежнему считается)
        for _ in range(HTTP_BAN_FAILURES + 1):
            rpc(UNKNOWN_SECRET, req(1, "ping"), ip=ip)
        status, _, _ = rpc(SECRETS["a-read"], req(1, "ping"), ip=ip)
        self.assertEqual(status, 404, "бан не сработал — проверка бессмысленна")
        # Владелец отвечает «это был я»: перевыпуск (новый секрет, новая цепочка)
        grant.secret = "u" * 43
        grant.key = "v" * 43
        grant.patch(secret=grant.secret, key_hash=key_hash(grant.key), compromised=False)
        status, _, body = grant.post(req(1, "ping"), ip=ip, follow=False)
        self.assertEqual(status, 200, body[:200])
        # И бан снят целиком: с того же адреса работает любой доступ
        status, _, _ = rpc(SECRETS["a-read"], req(1, "ping"), ip=ip)
        self.assertEqual(status, 200)


class RateLimitTests(unittest.TestCase):
    """Предела частоты не было вовсе (DEFECTS §1d.2): счётчик считал только неудачи."""

    def test_successful_calls_are_capped_per_grant_with_an_event(self):
        grant = Rolling(scope="read", mode="static", key_hash="")
        self.addCleanup(grant.drop)
        ip = "203.0.113.205"
        last = None
        for _ in range(HTTP_RATE_CALLS + 1):
            last = rpc(grant.secret, req(1, "ping"), ip=ip, token=grant.token)
            if last[0] != 200:
                break
        status, headers, body = last
        # Честный 429 с Retry-After, а не 404: запрос уже доказал знание ключа, и
        # молчаливый 404 отправил бы агента перевыпускать доступ впустую.
        self.assertEqual(status, 429, body[:200])
        self.assertEqual(headers.get("retry-after"), str(HTTP_RATE_RETRY_AFTER))
        self.assertIn("rate limit", json.loads(body)["error"]["message"])
        events = notify_events("agent_rate_limited", grant.id)
        self.assertTrue(events, "о всплеске владельцу не сказали")
        self.assertGreaterEqual(events[0]["calls"], HTTP_RATE_CALLS)
        # Предел — на доступ, а не на адрес: сосед с того же IP работает
        status, _, _ = rpc(SECRETS["a-read"], req(1, "ping"), ip=ip)
        self.assertEqual(status, 200)


    def test_bad_rolling_key_does_not_spend_the_agents_slots(self):
        # Аудит 25.09.2026: слот частоты списывался после пути и токена, но до одноразового
        # ключа — тот, кому утекли только путь и токен, держал настоящего агента в 429.
        # Неверные ключи — с разных адресов, каждый ниже порога бана.
        grant = Rolling(scope="read")
        self.addCleanup(grant.drop)
        per_ip = HTTP_BAN_FAILURES - 1
        sent, n = 0, 0
        while sent < HTTP_RATE_CALLS + 5:
            n += 1
            for _ in range(min(per_ip, HTTP_RATE_CALLS + 5 - sent)):
                status, _, _ = grant.post(req(1, "ping"), key="w" * 43, ip="198.51.100.%d" % n, follow=False)
                self.assertEqual(status, 404)
                sent += 1
        status, _, body = grant.post(req(1, "ping"), ip="203.0.113.250")
        self.assertEqual(status, 200, body[:200])


class TlsAndStartupTests(unittest.TestCase):
    def test_public_bind_without_tls_is_refused(self):
        port = free_port()
        proc, log = start_server(port, ["--bind", "0.0.0.0"])
        # --bind перебивает: последний ключ побеждает, сервер должен отказаться без --tls
        rc = proc.wait(timeout=20)
        log.close()
        self.assertEqual(rc, 2)
        with open(log.name, "rb") as fh:
            self.assertIn("--tls", fh.read().decode("utf-8", "replace"))

    def test_tls_listener(self):
        openssl = shutil.which("openssl")
        if not openssl:
            self.skipTest("нет openssl")
        crt = os.path.join(TMP, "tls.crt")
        key = os.path.join(TMP, "tls.key")
        cfg = os.path.join(TMP, "tls.cnf")
        with open(cfg, "w", encoding="utf-8") as fh:
            fh.write("[req]\ndistinguished_name = dn\nx509_extensions = v3\nprompt = no\n[dn]\nCN = 127.0.0.1\n"
                     "[v3]\nsubjectAltName = IP:127.0.0.1\n")
        gen = subprocess.run([openssl, "req", "-x509", "-new", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256",
                              "-nodes", "-days", "2", "-keyout", key, "-out", crt, "-config", cfg],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if gen.returncode != 0:
            self.skipTest("openssl не выпустил тестовый сертификат: %s" % gen.stderr.decode("utf-8", "replace")[-200:])
        port = free_port()
        proc, log = start_server(port, ["--tls", "%s,%s" % (crt, key), "--extra-ports", "none"])
        try:
            self.assertTrue(wait_port(port), "TLS-слушатель не поднялся")
            status, headers, body = rpc(SECRETS["a-admin"], req(1, "tools/list"), port=port, https=True)
            self.assertEqual(status, 200)
            self.assertEqual(sorted(t["name"] for t in json.loads(body)["result"]["tools"]), ALL_NAMES)
            status, _, body = rpc(UNKNOWN_SECRET, req(1, "ping"), port=port, https=True)
            self.assertEqual(status, 404)
            self.assertEqual(body, NGINX_404)
            # открытый текст на TLS-порт — соединение рвётся, сервер живёт
            with self.assertRaises(Exception):
                raw_request("POST", "/", b"{}", {}, port=port)
            self.assertEqual(rpc(SECRETS["a-admin"], req(2, "ping"), port=port, https=True)[0], 200)
        finally:
            proc.send_signal(signal.SIGTERM)
            rc = proc.wait(timeout=10)
            log.close()
        self.assertEqual(rc, 0)

    def test_check_reports_http_state(self):
        env = dict(os.environ, GOAMNEZIA_DIR=TMP, GOAMNEZIA_ACTION_BIN=FIXTURE, GOAMNEZIA_ACTIONS_JSON=ACTIONS_JSON)
        check = subprocess.run([sys.executable, SERVER, "--check"], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(check.returncode, 0, check.stderr.decode("utf-8", "replace"))
        text = check.stderr.decode("utf-8", "replace")
        self.assertIn("http         : включён", text)
        self.assertIn("доступов 11", text)
        self.assertIn("с портом 1", text)
        self.assertIn("без токена 8", text)
        self.assertIn("с привязкой к адресу 1", text)
        self.assertIn("усильте их agent-harden", text)
        for secret in SECRETS.values():
            self.assertNotIn(secret, text)


# ── Переходник goamnezia-connect: привязка кодом и плавающий путь (MCP_ROTATION §2, §3, §7) ──
import importlib.util as _ilu

_spec = _ilu.spec_from_file_location("ga_connect_for_tests", os.path.join(os.path.dirname(SERVER), "connect.py"))
CONNECT = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(CONNECT)

ENROLL_CODE = "GA-TEST1-TEST2-TEST3-TEST4"
EXPIRED_CODE = "GA-OLDOL-DOLDO-LDOLD-OLDOL"
BOUND_SEED = "M" * 43
BOUND_TOKEN = "b" * 43
BOUND_KEY0 = "k" * 43
CONNECT_IDS = {"pending": "a-c0de", "expired": "a-dead", "bound": "a-b0d0", "fenced": "a-fe7c", "lapsed": "a-1a75",
               "pinned": "a-91b7"}
FENCED_CODE = "GA-FENCE-DFENC-EDFEN-CEDFE"
LAPSED_CODE = "GA-LAPSE-DLAPS-EDLAP-SEDLA"
FENCED_IP = "198.18.5.7"
PINNED_CODE = "GA-PINNE-DPINN-EDPIN-NEDPI"


def connect_grants():
    base = {"scope": "read", "enabled": True, "created": "2026-09-13T10:00:00Z", "expires": "", "port": 0,
            "allow_ips": [], "last_used": "", "last_ip": "", "calls": 0, "transport": "connect"}
    return [
        dict(base, id=CONNECT_IDS["pending"], label="Cursor", secret="G" * 43,
             enroll_path=CONNECT.enroll_path(ENROLL_CODE), enroll_expires="2099-01-01T00:00:00Z"),
        dict(base, id=CONNECT_IDS["expired"], label="Old", secret="H" * 43,
             enroll_path=CONNECT.enroll_path(EXPIRED_CODE), enroll_expires="2020-01-01T00:00:00Z"),
        dict(base, id=CONNECT_IDS["bound"], label="Bound", secret="J" * 43, rot_seed=BOUND_SEED,
             token=BOUND_TOKEN, require_token=True, mode="rolling", key_storage="file",
             key_hash=hashlib.sha256(BOUND_KEY0.encode()).hexdigest()),
        # Белый список адресов действует и на привязку: код — замена секрета пути, не пропуск
        dict(base, id=CONNECT_IDS["fenced"], label="Fenced", secret=("fenced" * 8)[:43], allow_ips=[FENCED_IP],
             enroll_path=CONNECT.enroll_path(FENCED_CODE), enroll_expires="2099-01-01T00:00:00Z"),
        # Привязка к адресу осталась от прежней машины (перевыпуск после переезда агента)
        dict(base, id=CONNECT_IDS["pinned"], label="Pinned", secret=("pinned" * 8)[:43], pin_ip=True,
             pinned_ip="203.0.113.1", enroll_path=CONNECT.enroll_path(PINNED_CODE),
             enroll_expires="2099-01-01T00:00:00Z"),
        # Срок самого доступа вышел, а код ещё свежий (перевыпуск после истечения)
        dict(base, id=CONNECT_IDS["lapsed"], label="Lapsed", secret=("lapsed" * 8)[:43],
             expires="2020-01-01T00:00:00Z",
             enroll_path=CONNECT.enroll_path(LAPSED_CODE), enroll_expires="2099-01-01T00:00:00Z"),
    ]


def float_post(rot_seed, slot, token, key, nonce, method="tools/list", ip=None):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method}).encode()
    return raw_request("POST", "/r-%s/mcp" % CONNECT.float_path(rot_seed, slot), body=body,
                       headers={"Content-Type": "application/json", "Authorization": "Bearer " + token,
                                "X-Ga-Key": key, "X-Ga-Nonce": nonce, "X-Real-IP": ip or next_ip()})


class ConnectTransportTests(unittest.TestCase):
    def setUp(self):
        doc = grants_doc()
        doc["agents"] += connect_grants()
        write_agents(doc)

    def tearDown(self):
        write_agents(grants_doc())

    def test_enroll_hands_out_secrets_once_and_the_floating_path_opens(self):
        body = json.dumps({"key_storage": "tpm", "client": "test"}).encode()
        path = "/a-%s/enroll" % CONNECT.enroll_path(ENROLL_CODE)
        status, headers, data = raw_request("POST", path, body=body,
                                            headers={"Content-Type": "application/json", "X-Real-IP": next_ip()})
        self.assertEqual(status, 200, data)
        reply = json.loads(data)
        self.assertEqual(reply["grant"], CONNECT_IDS["pending"])
        for field in ("rot_seed", "token", "key"):
            self.assertRegex(reply[field], r"^[A-Za-z0-9_-]{43}$")
        self.assertIn("no-store", headers.get("cache-control", ""))
        rec = [a for a in read_agents()["agents"] if a["id"] == CONNECT_IDS["pending"]][0]
        self.assertEqual((rec["rot_seed"], rec["token"], rec["key_storage"], rec["mode"]),
                         (reply["rot_seed"], reply["token"], "tpm", "rolling"))
        self.assertEqual(rec["key_hash"], hashlib.sha256(reply["key"].encode()).hexdigest())
        self.assertNotIn("enroll_path", rec, "код обязан сгореть")
        self.assertTrue(notify_events("agent_enrolled", agent=CONNECT_IDS["pending"]))
        slot = int(time.time() // 60)
        status, headers, data = float_post(reply["rot_seed"], slot, reply["token"], reply["key"], "nonce-first-01")
        self.assertEqual(status, 200, data)
        self.assertRegex(headers.get("x-ga-next-key", ""), r"^[A-Za-z0-9_-]{43}$")
        status, _, _ = raw_request("POST", path, body=body, headers={"X-Real-IP": next_ip()})
        self.assertEqual(status, 404, "второй раз тем же кодом — отказ")

    def test_floating_window_is_three_slots_and_a_miss_does_not_burn_the_chain(self):
        slot = int(time.time() // 60)
        status, headers, _ = float_post(BOUND_SEED, slot - 1, BOUND_TOKEN, BOUND_KEY0, "nonce-prev-001")
        self.assertEqual(status, 200)
        k1 = headers["x-ga-next-key"]
        status, headers, _ = float_post(BOUND_SEED, slot + 1, BOUND_TOKEN, k1, "nonce-next-001")
        self.assertEqual(status, 200)
        k2 = headers["x-ga-next-key"]
        status, _, _ = float_post(BOUND_SEED, slot + 3, BOUND_TOKEN, k2, "nonce-far-0001")
        self.assertEqual(status, 404, "отрезок за окном")
        status, headers, _ = float_post(BOUND_SEED, slot, BOUND_TOKEN, k2, "nonce-cur-0001")
        self.assertEqual(status, 200, "промах мимо пути цепочку не сжёг")
        rec = [a for a in read_agents()["agents"] if a["id"] == CONNECT_IDS["bound"]][0]
        self.assertFalse(rec.get("compromised"))

    def test_fixed_path_does_not_open_a_connector_grant(self):
        status, _, _ = raw_request("POST", "/a-%s/mcp" % ("J" * 43),
                                   body=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode(),
                                   headers={"Content-Type": "application/json", "Authorization": "Bearer " + BOUND_TOKEN,
                                            "X-Ga-Key": BOUND_KEY0, "X-Ga-Nonce": "nonce-fixed-01", "X-Real-IP": next_ip()})
        self.assertEqual(status, 404)

    def test_enroll_obeys_the_ip_allowlist_and_a_refusal_keeps_the_code(self):
        path = "/a-%s/enroll" % CONNECT.enroll_path(FENCED_CODE)
        status, _, _ = raw_request("POST", path, body=b"{}",
                                   headers={"Content-Type": "application/json", "X-Real-IP": "198.18.9.9"})
        self.assertEqual(status, 404, "привязка с адреса вне белого списка")
        rec = [a for a in read_agents()["agents"] if a["id"] == CONNECT_IDS["fenced"]][0]
        self.assertIn("enroll_path", rec, "отказ по адресу не сжигает код владельца")
        self.assertFalse(rec.get("rot_seed"))
        status, _, data = raw_request("POST", path, body=b"{}",
                                      headers={"Content-Type": "application/json", "X-Real-IP": FENCED_IP})
        self.assertEqual(status, 200, data)

    def test_enroll_repins_a_pinned_grant_to_the_enrolling_machine(self):
        ip = "198.18.7.7"
        status, _, data = raw_request("POST", "/a-%s/enroll" % CONNECT.enroll_path(PINNED_CODE), body=b"{}",
                                      headers={"Content-Type": "application/json", "X-Real-IP": ip})
        self.assertEqual(status, 200, data)
        rec = [a for a in read_agents()["agents"] if a["id"] == CONNECT_IDS["pinned"]][0]
        self.assertEqual(rec["pinned_ip"], ip, "привязка к адресу — за машиной, которая привязалась")
        reply = json.loads(data)
        slot = int(time.time() // 60)
        status, _, _ = float_post(reply["rot_seed"], slot, reply["token"], reply["key"], "nonce-pinned-01", ip=ip)
        self.assertEqual(status, 200, "новая машина работает, а не упирается в прежний адрес")

    def test_enroll_is_refused_for_a_grant_whose_own_term_expired(self):
        status, _, _ = raw_request("POST", "/a-%s/enroll" % CONNECT.enroll_path(LAPSED_CODE), body=b"{}",
                                   headers={"Content-Type": "application/json", "X-Real-IP": next_ip()})
        self.assertEqual(status, 404)
        rec = [a for a in read_agents()["agents"] if a["id"] == CONNECT_IDS["lapsed"]][0]
        self.assertIn("enroll_path", rec)
        self.assertFalse(rec.get("token"))

    def test_expired_code_is_refused_and_the_record_stays_pending(self):
        status, _, _ = raw_request("POST", "/a-%s/enroll" % CONNECT.enroll_path(EXPIRED_CODE), body=b"{}",
                                   headers={"Content-Type": "application/json", "X-Real-IP": next_ip()})
        self.assertEqual(status, 404)
        rec = [a for a in read_agents()["agents"] if a["id"] == CONNECT_IDS["expired"]][0]
        self.assertIn("enroll_path", rec)
        self.assertNotIn("rot_seed", rec)

    def test_connector_and_server_agree_end_to_end(self):
        work = tempfile.mkdtemp(prefix="ga-connect-e2e-")
        try:
            real = sys.stdout
            sys.stdout = open(os.devnull, "w")
            try:
                rc = CONNECT.main(["enroll", ENROLL_CODE, "--server", "http://127.0.0.1:%d" % PORT,
                                   "--storage", "file", "--dir", work])
            finally:
                sys.stdout.close()
                sys.stdout = real
            self.assertEqual(rc, 0)
            cfg, _ = CONNECT.load_cfg(work, CONNECT_IDS["pending"])
            conn = CONNECT.Connector(cfg, CONNECT.make_store("file", work, cfg["grant"]), CONNECT.Http(cfg["server"]))
            import io as _io
            stdin = _io.BytesIO(b"".join(json.dumps(m).encode() + b"\n" for m in (
                {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                 "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})))
            stdout = _io.BytesIO()
            conn.run(stdin, stdout)
            out = [json.loads(l) for l in stdout.getvalue().decode().splitlines() if l.strip()]
            self.assertEqual([m.get("id") for m in out], [1, 2], out)
            self.assertIn("tools", out[1].get("result", {}), out)
        finally:
            shutil.rmtree(work, ignore_errors=True)


class ClockWatchTests(unittest.TestCase):
    def test_only_a_jump_between_checks_counts(self):
        spec = _ilu.spec_from_file_location("ga_server_for_clock", SERVER)
        mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)
        wall, mono = [1000.0], [50.0]
        watch = mod.ClockWatch(wall=lambda: wall[0], mono=lambda: mono[0])
        wall[0] += 10; mono[0] += 10
        self.assertEqual(watch.check(), 0)
        wall[0] += 130; mono[0] += 10
        self.assertEqual(watch.check(), 120, "перевод часов на две минуты вперёд")
        wall[0] += 10; mono[0] += 10
        self.assertEqual(watch.check(), 0, "после прыжка точка отсчёта новая")
        for _ in range(200):                 # медленная подстройка NTP — не прыжок
            wall[0] += 10.5; mono[0] += 10
            self.assertEqual(watch.check(), 0)


if __name__ == "__main__":
    unittest.main()
