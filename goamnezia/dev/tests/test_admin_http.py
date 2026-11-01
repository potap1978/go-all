# -*- coding: utf-8 -*-
"""HTTP-тесты веб-панели: server.py настоящим подпроцессом на свободном порту петли.

Окружение — временный GOAMNEZIA_DIR, мост подменён заглушкой
tests/fixtures/fake_action_admin.sh, коды 2FA уходят в файл (GOAMNEZIA_TFA_SINK)
вместо Telegram, сроки сессии ускорены (TTL 2 с, окно продления 2 с).

Что проверяется: вход через петлю без кода отдаёт данные; любой из четырёх
прокси-заголовков → 401 и страница входа; аварийный ключ (?token=) — только по туннелю, с meta и в срок,
заводит сессию и сгорает, с домена — отказ словами (SEC50-08);
код 2FA → сессия → продление → истечение; DNS-rebinding по Host → 403; статика;
каждый API-маршрут отвечает по контракту; мутации через /api/action/<имя> с
валидацией, confirm для разрушительных и предохранителем от CSRF.

Запуск: python3 -m unittest tests/test_admin_http.py
"""
import base64
import errno
import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(ROOT, "admin-web", "server.py")
STATIC = os.path.join(ROOT, "admin-web", "static")
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "fake_action_admin.sh")
ACTIONS_JSON = os.path.join(ROOT, "lib", "actions.json")
CATALOG_TOOL = os.path.join(ROOT, "lib", "catalog_tool.py")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
BOT_TOKEN = "123456789:AAFakeTokenForHttpTestsOnly_0123456789"
ADMIN_ID = "424242"
SESSION_TTL = 2
SESSION_GRACE = 2

TMP = None
PROC = None
PORT = 0
SINK = ""
ARGS_FILE = ""
ENV_FILE = ""
LANG_FILE = ""
COUNT_FILE = ""
SLEEP_FILE = ""
STDERR_FILE = None

MINI_CATALOG = {"categories": [
    {"id": "landing", "name": "Лендинги", "icon": "rocket", "templates": [
        {"id": "tpl_alpha", "name": "Alpha", "source": "test", "description": "Первый", "repo_url": "https://example.test/a",
         "sparse_path": "", "preview_url": "https://example.test/a/", "license": "MIT"},
        {"id": "tpl_beta", "name": "Beta cafe", "source": "test", "description": "Второй", "repo_url": "https://example.test/b",
         "sparse_path": "", "preview_url": "", "license": "MIT"}]},
    {"id": "business", "name": "Бизнес", "icon": "briefcase", "templates": [
        {"id": "tpl_gamma", "name": "Gamma", "source": "test", "description": "Третий", "repo_url": "https://example.test/c",
         "sparse_path": "", "preview_url": "", "license": "MIT"}]},
]}


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def setUpModule():
    global TMP, PROC, PORT, SINK, ARGS_FILE, ENV_FILE, LANG_FILE, COUNT_FILE, SLEEP_FILE, STDERR_FILE
    TMP = tempfile.mkdtemp(prefix="goamnezia-admin-http-")
    ga_dir = os.path.join(TMP, "goamnezia")
    admin_dir = os.path.join(TMP, "admin")
    bot_dir = os.path.join(TMP, "bot")
    for d in (ga_dir, admin_dir, bot_dir):
        os.makedirs(d)
    with open(os.path.join(ga_dir, "config.json"), "w", encoding="utf-8") as fh:
        json.dump({"schema": 1, "version": "0.1.0", "installed": True, "mode": "lite", "lang": "ru", "domain": "",
                   "server_ip": "203.0.113.10", "endpoint_host": "", "client_dns": ["1.1.1.1", "1.0.0.1"],
                   "profiles": {"awg2": {"line": "2.0", "container": "amnezia-awg2", "port": 443, "subnet": "10.8.2.0/24",
                                         "iface": "awg0", "image_tag": "3.1.20260828", "preset": "default",
                                         "params": {"MTU": "1280", "H1": "1-2", "HeaderProtectionKey": "k"}, "enabled": True,
                                         "server_pubkey": "PUB"},
                                "awg3": {"line": "3.1", "container": "amnezia-awg3", "port": 39001, "subnet": "10.8.3.0/24",
                                         "iface": "awg0", "params": {"MTU": "1280"}, "enabled": True}},
                   "panel": {"port": 1985, "miniapp_path": "", "trust_tunnel": True},
                   "site": {"template_id": "tpl_alpha", "template_source": "", "listen": "public", "mcp_location": False},
                   "options": {"alt_svc": False}, "mcp": {"http_enabled": False, "http_port": 1986}}, fh)
    with open(os.path.join(bot_dir, ".env"), "w", encoding="utf-8") as fh:
        fh.write("BOT_TOKEN=%s\nALLOWED_IDS=%s\n" % (BOT_TOKEN, ADMIN_ID))
    catalog = os.path.join(TMP, "catalog.json")
    with open(catalog, "w", encoding="utf-8") as fh:
        json.dump(MINI_CATALOG, fh, ensure_ascii=False)
    SINK = os.path.join(TMP, "tfa.txt")
    ARGS_FILE = os.path.join(TMP, "args.txt")
    ENV_FILE = os.path.join(TMP, "env.txt")
    LANG_FILE = os.path.join(TMP, "lang.txt")
    COUNT_FILE = os.path.join(TMP, "count.txt")
    SLEEP_FILE = os.path.join(TMP, "sleep.txt")
    PORT = free_port()
    env = dict(os.environ)
    env.update({
        "GOAMNEZIA_DIR": ga_dir, "GOAMNEZIA_ADMIN_DIR": admin_dir, "GOAMNEZIA_ADMIN_STATIC": STATIC,
        "GOAMNEZIA_BOT_DIR": bot_dir, "GOAMNEZIA_ACTION_BIN": FIXTURE, "GOAMNEZIA_ACTIONS_JSON": ACTIONS_JSON,
        "GOAMNEZIA_CATALOG": catalog, "GOAMNEZIA_CATALOG_TOOL": CATALOG_TOOL,
        "GOAMNEZIA_AUDIT_LOG": os.path.join(TMP, "audit.log"), "GOAMNEZIA_ADMIN_PORT": str(PORT),
        "GOAMNEZIA_TFA_SINK": SINK, "GOAMNEZIA_SESSION_TTL": str(SESSION_TTL), "GOAMNEZIA_SESSION_GRACE": str(SESSION_GRACE),
        "GOAMNEZIA_TRUST_TUNNEL": "1", "FAKE_ADMIN_ARGS_FILE": ARGS_FILE, "FAKE_ADMIN_ENV_FILE": ENV_FILE,
        "FAKE_ADMIN_LANG_FILE": LANG_FILE,
        "FAKE_ADMIN_COUNT_FILE": COUNT_FILE, "PYTHONUNBUFFERED": "1",
        # Замок мутаций: ждём секунду и отвечаем «занято». Файл задержки пустой
        # (его создаёт только тест про занятый замок) — остальным он не мешает.
        "GOAMNEZIA_ADMIN_LOCK_WAIT": "1", "FAKE_ADMIN_SLEEP_FILE": SLEEP_FILE,
    })
    env.pop("GOAMNEZIA_ADMIN_TOKEN", None)
    STDERR_FILE = open(os.path.join(TMP, "server.err"), "w+", encoding="utf-8")
    PROC = subprocess.Popen([sys.executable, SERVER], env=env, stdout=subprocess.DEVNULL, stderr=STDERR_FILE)
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=2)
            c.request("GET", "/api/session", headers={"Host": "127.0.0.1:%d" % PORT})
            c.getresponse().read()
            c.close()
            return
        except OSError:
            if PROC.poll() is not None:
                break
            time.sleep(0.1)
    STDERR_FILE.seek(0)
    raise RuntimeError("панель не поднялась: %s" % STDERR_FILE.read())


def tearDownModule():
    if PROC and PROC.poll() is None:
        PROC.terminate()
        try:
            PROC.wait(timeout=5)
        except subprocess.TimeoutExpired:
            PROC.kill()
    if STDERR_FILE:
        STDERR_FILE.close()
    shutil.rmtree(TMP, ignore_errors=True)


def _unsent(exc):
    """Соединение сброшено ДО того, как запрос ушёл: сервер его не видел.

    Очередь приёма у ThreadingHTTPServer — пять соединений. Пачку одновременных сверх неё Linux
    придерживает (клиент повторяет SYN), а macOS сбрасывает сразу: ConnectionResetError на connect,
    BrokenPipeError на отправке, EINVAL у setsockopt на уже сброшенном сокете."""
    if isinstance(exc, (ConnectionResetError, BrokenPipeError, ConnectionRefusedError)):
        return True
    return isinstance(exc, OSError) and exc.errno == errno.EINVAL


def request(method, path, body=None, headers=None, cookie="", public=False, guard=True, unsent_retries=0):
    """Запрос к панели. public=True добавляет X-Forwarded-Host — так приходит nginx с домена.

    unsent_retries — сколько раз повторить запрос, который до сервера не дошёл (см. _unsent). Повтор
    честный: сброс случился на connect/send, до ответа. Обрыв при чтении ответа не повторяется никогда —
    такой запрос сервер мог уже посчитать."""
    h = {"Host": "127.0.0.1:%d" % PORT, "Accept": "application/json"}
    if guard and method != "GET":
        h["X-GoAmnezia-Admin"] = "1"
    if public:
        h["X-Forwarded-Host"] = "example.test"
    if cookie:
        h["Cookie"] = "ga_session=%s" % cookie
    if headers:
        h.update(headers)
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        h["Content-Type"] = "application/json"
    while True:
        c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=30)
        try:
            c.request(method, path, body=data, headers=h)
        except OSError as exc:
            c.close()
            if unsent_retries <= 0 or not _unsent(exc):
                raise
            unsent_retries -= 1
            time.sleep(0.05)
            continue
        r = c.getresponse()
        raw = r.read()
        c.close()
        return r, raw


def as_json(raw):
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        return {}


def cookie_value(resp):
    for k, v in resp.getheaders():
        if k.lower() == "set-cookie" and v.startswith("ga_session="):
            return v.split(";", 1)[0].split("=", 1)[1]
    return ""


def last_sink_code():
    with open(SINK, encoding="utf-8") as fh:
        return fh.read().strip().splitlines()[-1]


def login_by_code():
    """Полный публичный вход: запрос кода → код из стока → verify → cookie."""
    r, raw = request("POST", "/api/auth/request", {}, public=True)
    assert r.status == 200, raw
    code = last_sink_code()
    r, raw = request("POST", "/api/auth/verify", {"code": code}, public=True)
    assert r.status == 200, raw
    tok = cookie_value(r)
    assert tok
    return tok


class GuardTests(unittest.TestCase):
    def test_loopback_without_code_gets_data(self):
        r, raw = request("GET", "/api/session")
        self.assertEqual(r.status, 200)
        d = as_json(raw)["data"]
        self.assertEqual(d["state"], "ok")
        self.assertEqual(d["who"], "tunnel")
        self.assertTrue(d["tfa"])
        r, raw = request("GET", "/api/users")
        self.assertEqual(r.status, 200)
        self.assertGreaterEqual(len(as_json(raw)["data"]["users"]), 3)

    def test_each_proxy_header_breaks_trust(self):
        for h in ("X-Forwarded-Host", "X-Real-IP", "X-Forwarded-For", "X-Forwarded-Proto"):
            r, raw = request("GET", "/api/users", headers={h: "203.0.113.4"})
            self.assertEqual(r.status, 401, h)
            self.assertEqual(as_json(raw)["state"], "none")
            # Страница — форма входа, а не пустая панель.
            r, raw = request("GET", "/", headers={h: "203.0.113.4"})
            self.assertEqual(r.status, 200)
            self.assertIn(b"/api/auth/request", raw)
            self.assertNotIn(b"telegram.org/js", raw)  # без внешних скриптов
            # Заголовок Cache-Control/CSP на месте
            self.assertIn("Content-Security-Policy", dict(r.getheaders()))

    def test_head_is_not_a_beacon(self):
        """HEAD не должен выдавать панель среди путей домена.

        Регрессия стенда: без do_HEAD базовый обработчик отвечал 501, и
        секретный путь панели отличался от любого другого пути сайта-маскировки
        (там HEAD даёт 200/404) — готовый указатель для перебора.
        """
        for path in ("/", "/index.html", "/api/session"):
            r, raw = request("HEAD", path)
            self.assertNotEqual(r.status, 501, path)
            self.assertEqual(raw, b"", path)
            g, _ = request("GET", path)
            self.assertEqual(r.status, g.status, path)
            self.assertEqual(r.getheader("Content-Length"), g.getheader("Content-Length"), path)
        # Несуществующий путь на HEAD отвечает так же, как на GET — 404.
        r, _ = request("HEAD", "/nope-nope")
        self.assertEqual(r.status, 404)
        # Публичный вход: HEAD не должен ни обходить проверку кода, ни
        # отвечать иначе, чем GET (иначе панель снова видна перебором).
        for path in ("/", "/api/users"):
            r, raw = request("HEAD", path, public=True)
            g, _ = request("GET", path, public=True)
            self.assertEqual(r.status, g.status, path)
            self.assertEqual(raw, b"", path)
        r, _ = request("HEAD", "/api/users", public=True)
        self.assertEqual(r.status, 401)

    @staticmethod
    def _write_key(token, ttl=3600, meta=True):
        """Аварийный ключ так, как его кладёт SSH-меню: одна строка 0600 и рядом token.meta 0600 со сроком."""
        path = os.path.join(TMP, "admin", "token")
        for p, text in ((path, token + "\n"), (path + ".meta", json.dumps({"created": int(time.time()), "expires": int(time.time()) + ttl}))):
            if p.endswith(".meta") and not meta:
                continue
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
        return path

    def test_emergency_key_only_over_the_tunnel_and_burns(self):
        """SEC50-08: панель ключ не заводит; с домена ?token= не принимается даже верным — страница входа говорит
        почему; по туннелю свежий ключ входит один раз и сгорает (файлы ключа и meta удалены)."""
        key_file = os.path.join(TMP, "admin", "token")
        self.assertFalse(os.path.exists(key_file), "панель сама ключ больше не создаёт")
        token = "FreshKey-" + "a1b2c3d4" * 6
        self._write_key(token)
        r, raw = request("GET", "/?token=%s" % urllib.parse.quote(token), public=True)
        self.assertEqual(r.status, 200)
        self.assertIn(b'const NOTICE="token_net";', raw)
        self.assertFalse(cookie_value(r), "с домена ключ сессию не заводит")
        r, raw = request("GET", "/api/users", headers={"X-GoAmnezia-Token": token}, public=True)
        self.assertEqual(r.status, 401)
        self.assertEqual(as_json(raw)["code"], "token_tunnel_only")
        self.assertTrue(os.path.exists(key_file), "отказ с домена ключ не жжёт")
        # Туннель: вход по ключу — и ключа больше нет.
        r, raw = request("GET", "/?token=%s" % urllib.parse.quote(token))
        self.assertEqual(r.status, 200)
        self.assertIn(b"<title>", raw)
        tok = cookie_value(r)
        self.assertTrue(tok, "cookie сессии не выдана")
        # Туннелю /api/session всегда отвечает «tunnel»; как заведена сессия — видно, спросив с этой cookie с домена.
        r, raw = request("GET", "/api/session", cookie=tok, public=True)
        self.assertEqual(as_json(raw)["data"]["who"], "token")
        self.assertFalse(os.path.exists(key_file), "ключ сгорел")
        self.assertFalse(os.path.exists(key_file + ".meta"))
        # Сгоревший ключ туннелю, которому и так доверяют, не нужен: пускает как туннель, но не как «ключ».
        r, raw = request("GET", "/?token=%s" % urllib.parse.quote(token))
        r, raw = request("GET", "/api/session", cookie=cookie_value(r), public=True)
        self.assertEqual(as_json(raw)["data"]["who"], "tunnel")

    def test_legacy_key_without_meta_is_not_accepted(self):
        token = "LegacyKey-" + "f0e1d2c3" * 6
        key_file = self._write_key(token, meta=False)
        try:
            r, raw = request("GET", "/?token=%s" % urllib.parse.quote(token), public=True)
            self.assertIn(b'const NOTICE="token_net";', raw)
            r, raw = request("GET", "/?token=%s" % urllib.parse.quote(token))
            r, raw = request("GET", "/api/session", cookie=cookie_value(r), public=True)
            self.assertEqual(as_json(raw)["data"]["who"], "tunnel", "ключ без meta не принят — вошёл туннель")
            self.assertTrue(os.path.exists(key_file), "непринятый ключ не трогаем")
        finally:
            os.remove(key_file)

    def test_dns_rebinding_host_rejected(self):
        for host in ("evil.example", "127.0.0.1.evil.example:%d" % PORT):
            r, raw = request("GET", "/api/session", headers={"Host": host})
            self.assertEqual(r.status, 403, host)
        r, raw = request("GET", "/api/session", headers={"Host": "localhost:%d" % PORT})
        self.assertEqual(r.status, 200)

    def test_write_guard_required(self):
        r, raw = request("POST", "/api/auth/request", {}, public=True, guard=False)
        self.assertEqual(r.status, 403)
        r, raw = request("POST", "/api/action/user-rename", {"name": "anna", "label": "x"}, guard=False)
        self.assertEqual(r.status, 403)

    def test_malformed_body_is_400_not_500(self):
        h = {"Host": "127.0.0.1:%d" % PORT, "X-GoAmnezia-Admin": "1", "Content-Type": "application/json"}
        c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=10)
        c.request("POST", "/api/auth/verify", body=b"{not json", headers=h)
        r = c.getresponse()
        raw = r.read()
        c.close()
        self.assertEqual(r.status, 400)
        self.assertFalse(as_json(raw)["ok"])
        r, raw = request("POST", "/api/action/user-rename", ["list"], public=False)
        self.assertEqual(r.status, 400)

    def test_crooked_strings_are_4xx_not_500(self):
        """Продолжение UI-5: не-ASCII и одинокие суррогаты («\\ud800» в JSON) роняли маршруты в 500, а лишний
        параметр с суррогатом рвал соединение без ответа (у nginx — 502). Открытые маршруты входа — тоже."""
        cases = [
            # подтверждение не тем словом — отказ, а не TypeError у compare_digest
            (("POST", "/api/action/user-del", {"name": "anna", "confirm": "аня"}), {}, (400, "confirm_required")),
            (("POST", "/api/action/user-del", {"name": "anna", "confirm": "\ud800"}), {}, (400, "confirm_required")),
            # имя лишнего параметра возвращается в ответе — JSON с экраном \ud800, а не обрыв
            (("POST", "/api/action/user-rename", {"name": "anna", "\ud800": "x"}), {}, (400, "bad_arg")),
            # открытые маршруты входа: код и подпись мини-аппа
            (("POST", "/api/auth/verify", {"code": "\ud800"}), {"public": True, "headers": {"X-Real-IP": "198.51.100.171"}},
             (401, None)),
            (("POST", "/api/auth/miniapp", {"init_data": "a=1&hash=й"}), {"public": True}, (401, None)),
            (("POST", "/api/auth/miniapp", {"init_data": "a=\ud800&hash=" + "0" * 64}), {"public": True}, (401, None)),
        ]
        for (method, path, body), kw, (status, code) in cases:
            r, raw = request(method, path, body, **kw)
            d = as_json(raw)
            self.assertEqual(r.status, status, (path, body, raw[:200]))
            self.assertFalse(d.get("ok"), path)
            if code:
                self.assertEqual(d.get("code"), code, path)
        # Подпись мини-аппа с «é» в заголовке проверяется на КАЖДОМ маршруте: туннель её просто не замечает,
        # а с домена это отказ «нужен вход», но не 500.
        r, raw = request("GET", "/api/users", headers={"X-Telegram-Init-Data": "a=1&hash=\xe9"})
        self.assertEqual(r.status, 200)
        r, raw = request("GET", "/api/users", public=True, headers={"X-Telegram-Init-Data": "a=1&hash=\xe9"})
        self.assertEqual(r.status, 401)


class PasswordLoginTests(unittest.TestCase):
    """Вход по логину и паролю — второй равноправный способ (решение владельца).

    Корзина попыток считается ПО АДРЕСУ, поэтому каждый тест приходит со своего
    X-Real-IP из документационной сети: иначе перебор в одном тесте запирал бы
    вход в соседнем.
    """

    LOGIN = "admin"
    PASSWORD = "panel-Test-Password-1"

    @staticmethod
    def password_file():
        return os.path.join(TMP, "admin", "password.json")

    @classmethod
    def set_password(cls, password, login=None):
        """Задать пароль тем же путём, каким это делает install_admin.sh."""
        env = dict(os.environ)
        env["GOAMNEZIA_ADMIN_DIR"] = os.path.join(TMP, "admin")
        out = subprocess.run([sys.executable, SERVER, "--set-password", "--login=%s" % (login or cls.LOGIN)],
                             input=password + "\n", env=env, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
        assert out.returncode == 0, out.stderr
        return out.stdout

    def setUp(self):
        # Каждый тест начинает с известного пароля: порядок методов внутри
        # класса алфавитный, и зависеть от него тесты не должны.
        self.set_password(self.PASSWORD)

    def login(self, login, password, ip, public=True, unsent_retries=0):
        return request("POST", "/api/auth/password", {"login": login, "password": password},
                       headers={"X-Real-IP": ip}, public=public, unsent_retries=unsent_retries)

    def test_change_password_needs_the_current_one(self):
        # Меняем через панель: маршрут за стражей, поэтому идём по туннелю.
        r, raw = request("POST", "/api/auth/password/change", {"current": "not-the-Password-1", "new": "brand-New-Password-2"})
        self.assertEqual(r.status, 401, raw)
        self.assertEqual(as_json(raw)["code"], "bad_credentials")
        r, raw = request("POST", "/api/auth/password/change", {"current": self.PASSWORD, "new": "short"})
        self.assertEqual(r.status, 400, raw)
        self.assertEqual(as_json(raw)["code"], "bad_password")
        r, raw = request("POST", "/api/auth/password/change", {"current": self.PASSWORD, "new": "brand-New-Password-2"})
        self.assertEqual(r.status, 200, raw)
        self.assertEqual(as_json(raw)["data"]["login"], self.LOGIN)
        # Старый пароль больше не пускает, новый пускает.
        r, _ = self.login(self.LOGIN, self.PASSWORD, "203.0.113.21")
        self.assertEqual(r.status, 401)
        r, raw = self.login(self.LOGIN, "brand-New-Password-2", "203.0.113.22")
        self.assertEqual(r.status, 200, raw)
        self.assertTrue(cookie_value(r))

    def test_code_login_still_works_with_password_set(self):
        # Своя корзина кода (адрес, а не общий «net»), чтобы не задеть кулдаун
        # публичного входа в TwoFactorTests.
        h = {"X-Real-IP": "203.0.113.30"}
        r, raw = request("POST", "/api/auth/request", {}, headers=h)
        self.assertEqual(r.status, 200, raw)
        r, raw = request("POST", "/api/auth/verify", {"code": last_sink_code()}, headers=h)
        self.assertEqual(r.status, 200, raw)
        tok = cookie_value(r)
        self.assertTrue(tok)
        r, raw = request("GET", "/api/session", cookie=tok, public=True)
        self.assertEqual(as_json(raw)["data"]["who"], "code")

    def test_equal_answer_time_for_wrong_login_and_wrong_password(self):
        """Разное время ответа само называло бы верный логин."""
        known, unknown = [], []
        for i in range(2):
            t0 = time.monotonic()
            r, _ = self.login(self.LOGIN, "wrong-Test-Password-9", "203.0.113.%d" % (40 + i))
            known.append(time.monotonic() - t0)
            self.assertEqual(r.status, 401)
            t0 = time.monotonic()
            r, _ = self.login("nobody", "wrong-Test-Password-9", "203.0.113.%d" % (50 + i))
            unknown.append(time.monotonic() - t0)
            self.assertEqual(r.status, 401)
        avg_known = sum(known) / len(known)
        avg_unknown = sum(unknown) / len(unknown)
        self.assertLess(abs(avg_known - avg_unknown), 0.15,
                        "время ответа выдаёт, что логин существует: %.3f и %.3f" % (avg_known, avg_unknown))

    def test_first_password_over_the_tunnel(self):
        """Обновление со старой установки: пароля нет, вход по нему не работает,
        а по туннелю его можно задать, не зная текущего (его не существует)."""
        os.remove(self.password_file())
        try:
            r, raw = request("GET", "/api/session")
            self.assertFalse(as_json(raw)["data"]["password"])
            r, raw = request("GET", "/", public=True)
            self.assertIn(b"const PWD_SET=false;", raw)
            self.assertIn(b"--reset-password", raw)  # подсказка, чем задать
            r, raw = self.login(self.LOGIN, self.PASSWORD, "203.0.113.60")
            self.assertEqual(r.status, 409, raw)
            self.assertEqual(as_json(raw)["code"], "password_unset")
            # По туннелю — задаём без текущего.
            r, raw = request("POST", "/api/auth/password/change", {"new": "fresh-First-Password-3"})
            self.assertEqual(r.status, 200, raw)
            r, raw = request("GET", "/api/session")
            self.assertTrue(as_json(raw)["data"]["password"])
            r, raw = self.login(self.LOGIN, "fresh-First-Password-3", "203.0.113.61")
            self.assertEqual(r.status, 200, raw)
        finally:
            self.set_password(self.PASSWORD)

    def test_login_page_offers_both_ways(self):
        r, raw = request("GET", "/", public=True)
        self.assertEqual(r.status, 200)
        self.assertIn(b"const TFA_OK=true;", raw)
        self.assertIn(b"const PWD_SET=true;", raw)
        self.assertIn(b"/api/auth/password", raw)
        self.assertNotIn(self.PASSWORD.encode(), raw)

    def test_login_starts_a_session_that_lives_as_before(self):
        r, raw = request("GET", "/api/users", public=True)
        self.assertEqual(r.status, 401)
        r, raw = self.login(self.LOGIN, self.PASSWORD, "203.0.113.70")
        self.assertEqual(r.status, 200, raw)
        tok = cookie_value(r)
        self.assertTrue(tok)
        r, raw = request("GET", "/api/users", cookie=tok, public=True)
        self.assertEqual(r.status, 200)
        r, raw = request("GET", "/api/session", cookie=tok, public=True)
        d = as_json(raw)["data"]
        self.assertEqual(d["state"], "ok")
        self.assertEqual(d["who"], "password")
        self.assertEqual(d["ttl"], SESSION_TTL)
        self.assertTrue(d["password"])
        # Дальше сессия ведёт себя как прежде: окно продления, потом «войдите заново».
        time.sleep(SESSION_TTL + 0.3)
        r, raw = request("GET", "/api/users", cookie=tok, public=True)
        self.assertEqual(r.status, 401)
        self.assertEqual(as_json(raw)["state"], "renew")
        time.sleep(SESSION_GRACE + 0.3)
        r, raw = request("GET", "/api/users", cookie=tok, public=True)
        self.assertEqual(as_json(raw)["state"], "expired")

    def test_password_never_leaves_the_server(self):
        r, raw = self.login(self.LOGIN, self.PASSWORD, "203.0.113.80")
        self.assertEqual(r.status, 200, raw)
        r, raw = request("GET", "/api/settings")
        pwd = as_json(raw)["data"]["password"]
        self.assertTrue(pwd["set"])
        self.assertEqual(pwd["login"], self.LOGIN)
        for leak in ("hash", "salt", "algo"):
            self.assertNotIn(leak, pwd)
        body = raw.decode("utf-8")
        self.assertNotIn(self.PASSWORD, body)
        # В аудите — факт попытки, без пароля и без логина.
        with open(os.path.join(TMP, "audit.log"), encoding="utf-8") as fh:
            lines = [json.loads(x) for x in fh.read().strip().splitlines() if x.strip()]
        auth = [x for x in lines if x["action"] == "auth-password"]
        self.assertTrue(auth, "вход по паролю не попал в аудит")
        raw_audit = json.dumps(lines, ensure_ascii=False)
        self.assertNotIn(self.PASSWORD, raw_audit)
        self.assertNotIn('"%s"' % self.LOGIN, raw_audit)

    def test_wrong_password_locks_the_address(self):
        ip = "203.0.113.90"
        for _ in range(5):
            r, raw = self.login(self.LOGIN, "wrong-Test-Password-8", ip)
            self.assertEqual(r.status, 401, raw)
            self.assertEqual(as_json(raw)["code"], "bad_credentials")
        # Шестая попытка — уже блокировка, и даже верный пароль не проходит.
        r, raw = self.login(self.LOGIN, self.PASSWORD, ip)
        self.assertEqual(r.status, 429, raw)
        self.assertEqual(as_json(raw)["code"], "too_many_attempts")
        # Соседний адрес чужим перебором не заперт.
        r, raw = self.login(self.LOGIN, self.PASSWORD, "203.0.113.91")
        self.assertEqual(r.status, 200, raw)

    def test_parallel_wrong_passwords_get_five_tries_not_more(self):
        """SEC-P1: пачка одновременных неверных входов с одного адреса. Прежде все запросы
        успевали пройти «заперт ли адрес?» раньше, чем первая неудача ложилась в корзину
        (проверка пароля занимает 0.35 с), — и догадок было столько, сколько запросов."""
        import threading
        ip, n = "203.0.113.120", 9
        barrier, got, lock = threading.Barrier(n), [], threading.Lock()

        def attempt():
            barrier.wait()
            # Повторяем только соединение, на котором запрос ещё не отправлен.
            # Сбой соединения — тоже ответ: раньше исключение в потоке молча терялось, и
            # тест падал «2 != 4» без причины (очередь соединений панели была на 5).
            try:
                r, raw = self.login(self.LOGIN, "wrong-Test-Password-7", ip, unsent_retries=50)
                item = (r.status, as_json(raw).get("code"))
            except Exception as exc:  # noqa: BLE001 — нужен любой сбой, чтобы назвать его
                item = ("error", type(exc).__name__)
            with lock:
                got.append(item)
        threads = [threading.Thread(target=attempt) for _ in range(n)]
        for th in threads:
            th.start()
        for th in threads:
            th.join(60)
        self.assertEqual(got.count((401, "bad_credentials")), 5, got)
        self.assertEqual(got.count((429, "too_many_attempts")), n - 5, got)
        r, raw = self.login(self.LOGIN, self.PASSWORD, ip)
        self.assertEqual(r.status, 429, raw)

    def test_write_guard_protects_password_routes(self):
        r, _ = request("POST", "/api/auth/password", {"login": self.LOGIN, "password": self.PASSWORD},
                       headers={"X-Real-IP": "203.0.113.95"}, guard=False)
        self.assertEqual(r.status, 403)
        r, _ = request("POST", "/api/auth/password/change", {"current": self.PASSWORD, "new": "x" * 14}, guard=False)
        self.assertEqual(r.status, 403)
        # Смена пароля — маршрут за стражей: с домена без сессии его нет.
        r, _ = request("POST", "/api/auth/password/change", {"current": self.PASSWORD, "new": "x" * 14}, public=True)
        self.assertEqual(r.status, 401)


class TwoFactorTests(unittest.TestCase):
    def test_code_login_renew_and_expiry(self):
        r, raw = request("GET", "/api/users", public=True)
        self.assertEqual(r.status, 401)
        tok = login_by_code()
        r, raw = request("GET", "/api/users", cookie=tok, public=True)
        self.assertEqual(r.status, 200)
        r, raw = request("GET", "/api/session", cookie=tok, public=True)
        d = as_json(raw)["data"]
        self.assertEqual(d["state"], "ok")
        self.assertEqual(d["who"], "code")
        self.assertEqual(d["ttl"], SESSION_TTL)
        # Окно продления: данные не отдаются, /api/session говорит renew.
        time.sleep(SESSION_TTL + 0.3)
        r, raw = request("GET", "/api/users", cookie=tok, public=True)
        self.assertEqual(r.status, 401)
        self.assertEqual(as_json(raw)["state"], "renew")
        r, raw = request("GET", "/api/session", cookie=tok, public=True)
        self.assertEqual(as_json(raw)["data"]["state"], "renew")
        # Продление: код из корзины renew (публичная корзина к этому моменту в кулдауне 30 с).
        r, raw = request("POST", "/api/auth/request", {}, cookie=tok, public=True)
        self.assertEqual(r.status, 200, raw)
        code = last_sink_code()
        r, raw = request("POST", "/api/auth/verify", {"code": code}, cookie=tok, public=True)
        self.assertEqual(r.status, 200)
        tok2 = cookie_value(r)
        self.assertTrue(tok2)
        self.assertNotEqual(tok, tok2)
        r, raw = request("GET", "/api/users", cookie=tok2, public=True)
        self.assertEqual(r.status, 200)
        # Старая сессия удалена, не «висит» как продлеваемая.
        r, raw = request("GET", "/api/users", cookie=tok, public=True)
        self.assertEqual(r.status, 401)
        self.assertEqual(as_json(raw)["state"], "expired")
        # Истечение целиком: TTL + окно → expired.
        time.sleep(SESSION_TTL + SESSION_GRACE + 0.3)
        r, raw = request("GET", "/api/users", cookie=tok2, public=True)
        self.assertEqual(r.status, 401)
        self.assertEqual(as_json(raw)["state"], "expired")

    def test_wrong_code_and_cooldown(self):
        r, raw = request("POST", "/api/auth/verify", {"code": "000000"}, headers={"X-Real-IP": "198.51.100.77"})
        self.assertEqual(r.status, 401)
        r, raw = request("POST", "/api/auth/request", {}, headers={"X-Real-IP": "198.51.100.77"})
        self.assertEqual(r.status, 200, raw)
        r, raw = request("POST", "/api/auth/request", {}, headers={"X-Real-IP": "198.51.100.77"})
        # 429 значит «слишком часто» и только это: у ответа есть код причины, и
        # прочие отказы отвечают по существу (DEFECTS §3.11).
        self.assertEqual(r.status, 429)
        self.assertIn("повторите", as_json(raw)["error"])
        self.assertEqual(as_json(raw)["code"], "too_often")

    def test_public_code_bucket_is_per_address(self):
        """SEC-P2: публичный вход — корзина на адрес (X-Real-IP своего nginx). Посторонний, знающий
        домен, упирается в свой кулдаун и выжигает свои догадки, а администратор с другого адреса
        получает код сразу и входит им — чужие неверные попытки его код не трогают."""
        stranger = {"X-Real-IP": "198.51.100.150"}
        admin = {"X-Real-IP": "198.51.100.151"}
        r, raw = request("POST", "/api/auth/request", {}, public=True, headers=stranger)
        self.assertEqual(r.status, 200, raw)
        r, raw = request("POST", "/api/auth/request", {}, public=True, headers=stranger)
        self.assertEqual((r.status, as_json(raw)["code"]), (429, "too_often"))
        r, raw = request("POST", "/api/auth/request", {}, public=True, headers=admin)
        self.assertEqual(r.status, 200, raw)
        code = last_sink_code()                       # код администратора — последний в стоке
        for _ in range(6):
            r, raw = request("POST", "/api/auth/verify", {"code": "000000" if code != "000000" else "111111"},
                             public=True, headers=stranger)
            self.assertEqual(r.status, 401)
        # Со своего адреса посторонний не проверяет и чужой код.
        r, raw = request("POST", "/api/auth/verify", {"code": code}, public=True, headers=stranger)
        self.assertEqual(r.status, 401)
        r, raw = request("POST", "/api/auth/verify", {"code": code}, public=True, headers=admin)
        self.assertEqual(r.status, 200, raw)
        self.assertTrue(cookie_value(r))

    def test_logout_drops_session(self):
        tok = login_by_code_in_own_bucket("198.51.100.90")
        r, raw = request("POST", "/api/auth/logout", {}, cookie=tok, headers={"X-Real-IP": "198.51.100.90"})
        self.assertEqual(r.status, 200)
        r, raw = request("GET", "/api/users", cookie=tok, headers={"X-Real-IP": "198.51.100.90"})
        self.assertEqual(r.status, 401)

    def test_miniapp_signature_login(self):
        import hashlib
        import hmac
        user = json.dumps({"id": int(ADMIN_ID), "first_name": "A"}, separators=(",", ":"))
        fields = {"auth_date": str(int(time.time())), "user": user, "query_id": "q"}
        check = "\n".join("%s=%s" % (k, v) for k, v in sorted(fields.items()))
        secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        init_data = urllib.parse.urlencode(fields)
        # Заголовок подписи пускает к данным без сессии.
        r, raw = request("GET", "/api/users", public=True, headers={"X-Telegram-Init-Data": init_data})
        self.assertEqual(r.status, 200)
        # Явный вход → cookie SameSite=None + одноразовый билет.
        r, raw = request("POST", "/api/auth/miniapp", {"init_data": init_data}, public=True)
        self.assertEqual(r.status, 200, raw)
        d = as_json(raw)
        self.assertTrue(d["ticket"])
        sc = [v for k, v in r.getheaders() if k.lower() == "set-cookie"][0]
        self.assertIn("SameSite=None", sc)
        self.assertIn("Secure", sc)
        r, raw = request("GET", "/?s=%s" % d["ticket"], public=True)
        self.assertEqual(r.status, 200)
        self.assertIn(b"app.js", raw)
        r, raw = request("GET", "/?s=%s" % d["ticket"], public=True)  # второй раз билет мёртв
        self.assertIn(b"/api/auth/request", raw)
        # Чужая подпись — отказ.
        r, raw = request("POST", "/api/auth/miniapp", {"init_data": init_data.replace("first_name", "last_name")}, public=True)
        self.assertEqual(r.status, 401)

    def test_miniapp_session_dies_when_the_admin_is_removed(self):
        """Админа сняли из бота — его сессия мини-аппа гаснет на следующем же запросе, а не через 15 минут
        (живая проверка 03.10: снятый админ ещё 892 с ходил в панель по открытой сессии)."""
        import hashlib
        import hmac
        user = json.dumps({"id": 777, "first_name": "B"}, separators=(",", ":"))
        fields = {"auth_date": str(int(time.time())), "user": user, "query_id": "q"}
        check = "\n".join("%s=%s" % (k, v) for k, v in sorted(fields.items()))
        secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        init_data = urllib.parse.urlencode(fields)
        env_path = os.path.join(TMP, "bot", ".env")
        with open(env_path, encoding="utf-8") as fh:
            env_before = fh.read()
        try:
            with open(env_path, "w", encoding="utf-8") as fh:
                fh.write("BOT_TOKEN=%s\nALLOWED_IDS=%s,777\nOWNER_ID=%s\n" % (BOT_TOKEN, ADMIN_ID, ADMIN_ID))
            r, raw = request("POST", "/api/auth/miniapp", {"init_data": init_data}, public=True)
            self.assertEqual(r.status, 200, raw)
            cookie = [v for k, v in r.getheaders() if k.lower() == "set-cookie"][0].split(";")[0].split("=", 1)[1]
            r, raw = request("GET", "/api/users", cookie=cookie, public=True)
            self.assertEqual(r.status, 200, raw)
            # Сняли из бота: .env без 777
            with open(env_path, "w", encoding="utf-8") as fh:
                fh.write("BOT_TOKEN=%s\nALLOWED_IDS=%s\nOWNER_ID=%s\n" % (BOT_TOKEN, ADMIN_ID, ADMIN_ID))
            r, raw = request("GET", "/api/users", cookie=cookie, public=True)
            self.assertEqual(r.status, 401, raw)
            r, raw = request("GET", "/api/session", cookie=cookie, public=True)
            self.assertNotEqual(as_json(raw).get("state"), "ok", raw)
            # Вернули — старая сессия не оживает: она снята, нужен новый вход
            with open(env_path, "w", encoding="utf-8") as fh:
                fh.write("BOT_TOKEN=%s\nALLOWED_IDS=%s,777\nOWNER_ID=%s\n" % (BOT_TOKEN, ADMIN_ID, ADMIN_ID))
            r, raw = request("GET", "/api/users", cookie=cookie, public=True)
            self.assertEqual(r.status, 401, raw)
        finally:
            with open(env_path, "w", encoding="utf-8") as fh:
                fh.write(env_before)

    def test_miniapp_admins_add_one_and_many_like_the_owner(self):
        """Мини-апп «Панель» в Telegram — та же app.js с теми же правами: и владелец, и админ, которого он позвал
        (оба в ALLOWED_IDS бота), заводят людей по одному (user-add) и сразу несколько (users-add-bulk); чужой —
        отказ до моста. Мост слышит, кто нажал (GOAMNEZIA_WHO=panel:tg:<id>)."""
        import hashlib
        import hmac

        def signed(uid):
            user = json.dumps({"id": uid, "first_name": "A"}, separators=(",", ":"))
            fields = {"auth_date": str(int(time.time())), "user": user, "query_id": "q"}
            check = "\n".join("%s=%s" % (k, v) for k, v in sorted(fields.items()))
            secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
            fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
            return {"X-Telegram-Init-Data": urllib.parse.urlencode(fields)}

        env_path = os.path.join(TMP, "bot", ".env")
        with open(env_path, encoding="utf-8") as fh:
            env_before = fh.read()
        try:
            with open(env_path, "w", encoding="utf-8") as fh:
                fh.write("BOT_TOKEN=%s\nALLOWED_IDS=%s,555\nOWNER_ID=%s\n" % (BOT_TOKEN, ADMIN_ID, ADMIN_ID))
            for uid in (int(ADMIN_ID), 555):
                r, raw = request("POST", "/api/action/user-add", {"name": "tg%d" % uid, "profiles": "awg2"},
                                 public=True, headers=signed(uid))
                self.assertEqual(r.status, 200, (uid, raw))
                r, raw = request("POST", "/api/action/users-add-bulk", {"count": "2", "profiles": "awg2"},
                                 public=True, headers=signed(uid))
                self.assertEqual(r.status, 200, (uid, raw))
                self.assertEqual(as_json(raw)["data"]["count"], 2)
                with open(ENV_FILE, encoding="utf-8") as fh:
                    self.assertEqual(fh.read().strip().splitlines()[-1], "panel:tg:%d panel" % uid)
            with open(ARGS_FILE, encoding="utf-8") as fh:
                calls = fh.read().count("--action=users-add-bulk")
            r, raw = request("POST", "/api/action/users-add-bulk", {"count": "2"}, public=True, headers=signed(777))
            self.assertEqual(r.status, 401, raw)
            with open(ARGS_FILE, encoding="utf-8") as fh:
                self.assertEqual(fh.read().count("--action=users-add-bulk"), calls)
            # «Удалить сразу несколько» — те же права, что у одиночного удаления: оба админа, подтверждение — числом
            for uid in (int(ADMIN_ID), 555):
                r, raw = request("POST", "/api/action/users-del-bulk", {"state": "quota", "confirm": "1"},
                                 public=True, headers=signed(uid))
                self.assertEqual(r.status, 200, (uid, raw))
                self.assertEqual(as_json(raw)["data"]["deleted"], ["greedy"])
                with open(ENV_FILE, encoding="utf-8") as fh:
                    self.assertEqual(fh.read().strip().splitlines()[-1], "panel:tg:%d panel" % uid)
            with open(ARGS_FILE, encoding="utf-8") as fh:
                calls = fh.read().count("--action=users-del-bulk\t")
            r, raw = request("POST", "/api/action/users-del-bulk", {"state": "quota", "confirm": "1"}, public=True,
                             headers=signed(777))
            self.assertEqual(r.status, 401, raw)
            with open(ARGS_FILE, encoding="utf-8") as fh:
                self.assertEqual(fh.read().count("--action=users-del-bulk\t"), calls)
        finally:
            with open(env_path, "w", encoding="utf-8") as fh:
                fh.write(env_before)


def login_by_code_in_own_bucket(ip):
    r, raw = request("POST", "/api/auth/request", {}, headers={"X-Real-IP": ip})
    assert r.status == 200, raw
    code = last_sink_code()
    r, raw = request("POST", "/api/auth/verify", {"code": code}, headers={"X-Real-IP": ip})
    assert r.status == 200, raw
    return cookie_value(r)


class StaticTests(unittest.TestCase):
    def test_index_and_assets(self):
        r, raw = request("GET", "/")
        self.assertEqual(r.status, 200)
        self.assertIn("text/html", r.getheader("Content-Type"))
        self.assertIn(b'data-page="overview"', raw)
        for name in ("users", "profiles", "traffic", "backups", "logs", "settings", "agents"):
            self.assertIn(('data-page="%s"' % name).encode(), raw)
        self.assertIn(b"data-i18n=", raw)
        # Ни одного внешнего скрипта или стиля: панель работает и там, где telegram.org и CDN недоступны.
        self.assertNotIn(b"telegram.org/js", raw)
        self.assertNotRegex(raw, rb'<(script|link)[^>]+(src|href)="https?://')
        r, raw = request("GET", "/app.js", public=True)  # публичный ассет
        self.assertEqual(r.status, 200)
        self.assertIn("javascript", r.getheader("Content-Type"))
        self.assertIn(b"/api/action/", raw)
        self.assertIn(b"X-GoAmnezia-Admin", raw)
        r, raw = request("GET", "/styles.css", public=True)
        self.assertEqual(r.status, 200)
        self.assertIn(b'data-theme="dark"', raw)
        r, raw = request("GET", "/favicon.ico", public=True)
        self.assertIn(r.status, (200, 204))

    def test_assets_have_the_real_version_long_cache_and_gzip(self):
        """PNL-04 (живая проверка на 168, 1.0.10): index.html ссылался на app.js?v=0.5.0 и styles.css?v=0.5.0, а всё
        отдавалось с no-store — 1,15 МБ на каждое открытие. Теперь в адресе — версия продукта и начало sha256 файла; по
        такому адресу — кеш на год (immutable) и gzip, если клиент принимает; страница — no-store; без версии или со
        старой — no-store, как раньше."""
        import gzip
        import hashlib
        r, raw = request("GET", "/")
        self.assertEqual(r.getheader("Cache-Control"), "no-store")
        page = raw.decode("utf-8")
        self.assertNotIn("?v=0.5.0", page)
        for name in ("app.js", "styles.css"):
            with open(os.path.join(STATIC, name), "rb") as fh:
                body = fh.read()
            v = "0.1.0-" + hashlib.sha256(body).hexdigest()[:10]
            self.assertIn("./%s?v=%s" % (name, v), page)
            r, got = request("GET", "/%s?v=%s" % (name, v), public=True, headers={"Accept-Encoding": "gzip, deflate, br"})
            self.assertEqual(r.status, 200, name)
            self.assertEqual(r.getheader("Cache-Control"), "public, max-age=31536000, immutable", name)
            self.assertEqual((r.getheader("Content-Encoding"), r.getheader("Vary")), ("gzip", "Accept-Encoding"), name)
            self.assertEqual(int(r.getheader("Content-Length")), len(got))
            self.assertEqual(gzip.decompress(got), body)
            self.assertLess(len(got), len(body) // 2, "сжатие в разы")
            r, got = request("GET", "/%s?v=%s" % (name, v), public=True)       # клиент без gzip — как есть
            self.assertIsNone(r.getheader("Content-Encoding"))
            self.assertEqual(got, body)
            for stale in ("/%s" % name, "/%s?v=0.5.0" % name):
                r, _ = request("GET", stale, public=True)
                self.assertEqual(r.getheader("Cache-Control"), "no-store", stale)
            r, _ = request("GET", "/%s?v=%s" % (name, v), public=True, headers={"Accept-Encoding": "gzip;q=0"})
            self.assertIsNone(r.getheader("Content-Encoding"), "q=0 — gzip нельзя")
        r, got = request("GET", "/", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(r.getheader("Content-Encoding"), "gzip")
        self.assertEqual(r.getheader("Cache-Control"), "no-store")
        self.assertIn(b"./app.js?v=0.1.0-", gzip.decompress(got))
        # Страница входа тянет те же стили — тем же адресом с версией
        r, raw = request("GET", "/index.html", public=True)
        self.assertIn("./styles.css?v=0.1.0-", raw.decode("utf-8"))
        self.assertNotIn("styles.css?v=0.5.0", raw.decode("utf-8"))

    def test_unknown_static_is_404_and_no_traversal(self):
        r, raw = request("GET", "/nope.txt")
        self.assertEqual(r.status, 404)
        r, raw = request("GET", "/../server.py")
        self.assertEqual(r.status, 404)
        r, raw = request("GET", "/index.html", public=True)  # без сессии — форма входа
        self.assertIn(b"/api/auth/request", raw)


class AppearanceTests(unittest.TestCase):
    """Вид панели (шторка «Вид», 0.5.0) хранится на сервере: белый список значений, запись — только своим фронтендом."""

    def test_appearance_roundtrip_and_whitelist(self):
        path = os.path.join(TMP, "goamnezia", "appearance.json")
        r, raw = request("GET", "/api/appearance")
        self.assertEqual(r.status, 200)
        d = as_json(raw)["data"]
        self.assertIn("appearance", d)
        self.assertEqual(d["defaults"]["density"], "detailed")
        good = {"theme": "dark", "accent": "custom", "hue": 120, "tint": "warm", "density": "min", "radius": 12,
                "btn": "round", "cards": "flat", "nav": "icons", "fs": "l", "vibrant": True, "motion": False}
        # Без предохранителя от CSRF — отказ, файл не пишется.
        r, raw = request("POST", "/api/appearance", {"appearance": good}, guard=False)
        self.assertEqual(r.status, 403)
        # Незнакомое значение, поле, тема мини-аппа, радиус вне 0…16, строка вместо числа — отказ целиком.
        for bad in ({"theme": "telegram"}, {"accent": "pink"}, {"radius": 17}, {"hue": "120"}, {"x": 1},
                    {"vibrant": "yes"}):
            r, raw = request("POST", "/api/appearance", {"appearance": bad})
            self.assertEqual(r.status, 400, bad)
            self.assertEqual(as_json(raw)["code"], "bad_appearance", bad)
        self.assertFalse(os.path.exists(path))
        r, raw = request("POST", "/api/appearance", {"appearance": good})
        self.assertEqual(r.status, 200, raw)
        self.assertEqual(as_json(raw)["data"]["appearance"], good)
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o644)
        r, raw = request("GET", "/api/appearance")
        self.assertEqual(as_json(raw)["data"]["appearance"], good)
        # Частичный вид дополняется умолчаниями — служба страниц читает всегда полный словарь.
        r, raw = request("POST", "/api/appearance", {"appearance": {"accent": "mint"}})
        saved = as_json(raw)["data"]["appearance"]
        self.assertEqual(saved["accent"], "mint")
        self.assertEqual(saved["density"], "detailed")
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["appearance"]["accent"], "mint")


class ReadApiTests(unittest.TestCase):
    def test_overview(self):
        # Холодный Обзор рукопожатия не ждёт (PERF-03): пока их снимка нет — users_online null и pending, и страница
        # переспрашивает; здесь — так же.
        for _ in range(100):
            r, raw = request("GET", "/api/overview")
            self.assertEqual(r.status, 200)
            d = as_json(raw)["data"]
            if "traffic" not in d["pending"]:
                break
            self.assertIsNone(d["users_online"])
            time.sleep(0.05)
        self.assertEqual(d["mode"], "lite")
        self.assertEqual(d["server_ip"], "203.0.113.10")
        self.assertEqual(d["users_total"], 3)
        self.assertEqual(d["users_online"], 1)  # anna — свежее рукопожатие, greedy — старое
        self.assertEqual({p["profile"] for p in d["profiles"]}, {"awg2", "awg3"})
        self.assertEqual(d["profiles"][0]["subnet"], "10.8.2.0/24")
        self.assertIn("docker", d["services"])
        self.assertEqual(d["traffic_total"]["rx"], 1048576 + 1024)

    def test_users_and_marks(self):
        r, raw = request("GET", "/api/users")
        d = as_json(raw)["data"]
        by = {u["name"]: u for u in d["users"]}
        self.assertEqual(by["anna"]["mark"], "●")
        self.assertTrue(by["anna"]["online"])
        self.assertTrue(by["anna"]["peers"]["awg2"]["online"])
        self.assertFalse(by["anna"]["peers"]["awg3"]["online"])
        self.assertEqual(by["bob"]["mark"], "○")
        self.assertEqual(by["old"]["mark"], "⏳")
        self.assertEqual(by["greedy"]["mark"], "▮")
        self.assertNotIn("privkey", raw.decode("utf-8"))
        self.assertEqual(d["online"], 1)

    def test_user_card_and_not_found(self):
        r, raw = request("GET", "/api/users/anna")
        self.assertEqual(r.status, 200)
        u = as_json(raw)["data"]["user"]
        self.assertEqual(u["label"], "Анна")
        self.assertEqual(u["quota_bytes"], 53687091200)
        r, raw = request("GET", "/api/users/nobody")
        self.assertEqual(r.status, 404)
        self.assertEqual(as_json(raw)["code"], "user_not_found")
        r, raw = request("GET", "/api/users/..%2Fetc")
        self.assertEqual(r.status, 400)

    def test_peers_carry_the_private_key_flag(self):
        """has_key у каждого пира — по нему карточка решает, предлагать ли выдачу.

        У пира из контейнера Amnezia приватной части ключа на сервере нет, и
        .conf, vpn:// и QR по нему не собрать. Признак обязан доезжать до
        браузера обоими маршрутами: без него панель предложит выдачу молча и
        человек узнает правду из `no_private_key`. Заглушка поля не шлёт — как
        ядро постарше, — и это значит «ключ есть», прежнее поведение.
        """
        r, raw = request("GET", "/api/users/anna")
        u = as_json(raw)["data"]["user"]
        self.assertIs(u["peers"]["awg2"]["has_key"], True)
        self.assertIs(u["peers"]["awg3"]["has_key"], True)
        r, raw = request("GET", "/api/users")
        by = {x["name"]: x for x in as_json(raw)["data"]["users"]}
        self.assertIs(by["anna"]["peers"]["awg2"]["has_key"], True)
        self.assertIs(by["bob"]["peers"]["awg2"]["has_key"], True)

    def test_user_key_and_conf_download(self):
        r, raw = request("GET", "/api/users/anna/key?profile=awg2&format=all")
        self.assertEqual(r.status, 200)
        d = as_json(raw)["data"]
        self.assertTrue(d["conf"].startswith("[Interface]"))
        self.assertEqual(d["vpn"], "vpn://FAKEKEY")
        self.assertTrue(base64.b64decode(d["qr_conf_png_b64"]).startswith(PNG_SIGNATURE))
        self.assertEqual(len(d["qr_vpn_png_b64"]), 2)
        self.assertEqual(d["min_client"], "4.8.12.7")
        r, raw = request("GET", "/api/users/anna/key?profile=xx")
        self.assertEqual(r.status, 400)
        self.assertEqual(as_json(raw)["code"], "bad_arg")
        r, raw = request("GET", "/api/users/anna/conf?profile=awg2")
        self.assertEqual(r.status, 200)
        self.assertIn("text/plain", r.getheader("Content-Type"))
        self.assertIn('filename="anna-awg2.conf"', r.getheader("Content-Disposition"))
        self.assertIn(b"PrivateKey", raw)
        # Выдача ключа записана в аудит, но без самого ключа.
        with open(os.path.join(TMP, "audit.log"), encoding="utf-8") as fh:
            audit = fh.read()
        self.assertIn('"action": "user-key"', audit)
        self.assertNotIn("FAKE-PRIVATE-KEY", audit)

    def test_profiles_and_params(self):
        r, raw = request("GET", "/api/profiles")
        d = as_json(raw)["data"]
        self.assertEqual(d["udp443"], "awg2")
        self.assertEqual(d["available"], ["awg1", "awg2", "awg3"])
        self.assertEqual(d["not_installed"], ["awg1"])
        awg2 = [p for p in d["installed"] if p["profile"] == "awg2"][0]
        self.assertEqual(awg2["state"], "running")
        self.assertEqual(awg2["mtu"], "1280")
        self.assertEqual(awg2["subnet"], "10.8.2.0/24")
        self.assertNotIn("params", awg2)
        r, raw = request("GET", "/api/profiles/awg2/params")
        self.assertEqual(r.status, 200)
        d = as_json(raw)["data"]
        keys = [p["key"] for p in d["params"]]
        self.assertIn("S3", keys)
        self.assertNotIn("HeaderProtectionKey", keys)  # 2.0 — без 3.x-полей
        scopes = {p["key"]: p["scope"] for p in d["params"]}
        self.assertEqual(scopes["S1"], "server")
        self.assertEqual(scopes["Jc"], "client")
        self.assertEqual({p["key"]: p["value"] for p in d["params"]}["H1"], "1000-2000")
        r, raw = request("GET", "/api/profiles/zzz/params")
        self.assertEqual(r.status, 400)

    def test_traffic_backups_logs(self):
        r, raw = request("GET", "/api/traffic")
        d = as_json(raw)["data"]
        self.assertEqual(d["online"], 1)
        awg2 = [p for p in d["profiles"] if p["profile"] == "awg2"][0]
        self.assertEqual(awg2["peers"], 2)
        self.assertEqual(awg2["online"], 1)
        # фейковое ядро без модуля статистики: живые счётчики, помеченные как живые
        self.assertEqual((awg2["period"], awg2["rx"], awg2["tx"]), ("live", 15, 25))
        self.assertEqual(d["users"][0]["name"], "anna")  # свежее рукопожатие — первым
        # без модуля статистики истории нет — но поля на месте: панель рисует «данные копятся», а не падает
        self.assertEqual((d["hours"], d["days"], d["month"], d["today"]), ([], [], None, None))
        r, raw = request("GET", "/api/backups")
        d = as_json(raw)["data"]
        names = {b["name"]: b for b in d["backups"]}
        self.assertTrue(names["goamnezia-backup-20260908_120000.tar.gz"]["restorable"])
        self.assertFalse(names["../evil.tar.gz"]["restorable"])
        r, raw = request("GET", "/api/logs?component=awg2&lines=60")
        self.assertEqual(r.status, 200)
        d = as_json(raw)["data"]
        self.assertEqual(d["component"], "awg2")
        self.assertIn("line one", d["log"])
        # PNL-COS-03: смещение часов сервера от UTC — для подписи «по часам сервера, UTC+N» под журналом
        self.assertEqual(d["tz_offset"], int(time.localtime().tm_gmtoff // 60))
        r, raw = request("GET", "/api/logs?component=hacker")
        self.assertEqual(r.status, 400)
        r, raw = request("GET", "/api/logs?component=system&lines=5")
        self.assertEqual(r.status, 400)  # lines ≥ 10 по actions.json

    def test_settings_quality_actions_agents(self):
        r, raw = request("GET", "/api/settings")
        d = as_json(raw)["data"]
        self.assertEqual(d["config"]["mode"], "lite")
        self.assertEqual(d["config"]["client_dns"], ["1.1.1.1", "1.0.0.1"])
        self.assertEqual(d["panel_url"], "")
        self.assertTrue(d["tfa"])
        self.assertTrue(d["trust_tunnel"])
        self.assertIn("system", d["components"])
        self.assertEqual(d["who"], "tunnel")
        self.assertNotIn("server_pubkey", raw.decode("utf-8"))
        self.assertNotIn("HeaderProtectionKey", raw.decode("utf-8"))
        r, raw = request("GET", "/api/quality")
        self.assertTrue(as_json(raw)["data"]["quality"]["docker"])
        r, raw = request("GET", "/api/actions")
        d = as_json(raw)["data"]
        self.assertIn("user-del", d["actions"])
        self.assertTrue(d["actions"]["user-del"]["destructive"])
        self.assertIn("pattern", d["param_types"]["name"])
        r, raw = request("GET", "/api/agents")
        d = as_json(raw)["data"]
        self.assertFalse(d["stub"])
        self.assertEqual([a["id"] for a in d["agents"]], ["a-1a2b", "a-3c4d"])
        self.assertEqual(d["agents"][0]["state"], "active")
        self.assertTrue(d["transport"]["enabled"])
        self.assertEqual(d["transport"]["base_url"], "https://203.0.113.10")
        self.assertTrue(d["transport"]["cert_fingerprint_sha256"].startswith("AA:BB:"))
        # адрес-ключ в списке не появляется: секрет отдают только agent-add/show/rotate
        self.assertNotIn("FAKESECRET", raw.decode("utf-8"))
        self.assertNotIn("/a-", raw.decode("utf-8"))
        r, raw = request("GET", "/api/nope")
        self.assertEqual(r.status, 404)

    def test_agent_actions_and_secret_only_on_demand(self):
        r, raw = request("POST", "/api/action/agent-show", {"id": "a-1a2b"})
        self.assertEqual(r.status, 200, raw)
        d = as_json(raw)["data"]
        self.assertEqual(d["url"], "https://203.0.113.10/a-FAKESECRETFAKESECRETFAKESECRETFAKESECRET01/mcp")
        self.assertTrue(d["cert_fingerprint_sha256"].startswith("AA:BB:"))
        r, raw = request("POST", "/api/action/agent-show", {"id": "a-9999"})
        self.assertEqual(r.status, 404)
        self.assertEqual(as_json(raw)["code"], "agent_not_found")
        r, raw = request("POST", "/api/action/agent-show", {"id": "nope"})
        self.assertEqual(r.status, 400)  # pattern agent_id ещё в панели
        # разрушительные — только с confirm = id
        r, raw = request("POST", "/api/action/agent-rotate", {"id": "a-1a2b"})
        self.assertEqual(r.status, 400)
        self.assertEqual(as_json(raw)["code"], "confirm_required")
        r, raw = request("POST", "/api/action/agent-rotate", {"id": "a-1a2b", "confirm": "a-1a2b"})
        self.assertEqual(r.status, 200, raw)
        self.assertIn("/a-FAKESECRET", as_json(raw)["data"]["url"])
        r, raw = request("POST", "/api/action/agent-del", {"id": "a-1a2b", "confirm": "a-1a2b"})
        self.assertEqual(r.status, 200, raw)
        r, raw = request("POST", "/api/action/agent-add", {"label": "Новый агент", "scope": "read", "days": "30", "allow_ips": "203.0.113.0/24"})
        self.assertEqual(r.status, 200, raw)
        self.assertEqual(as_json(raw)["data"]["id"], "a-9f9f")
        self.assertIn("/a-NEWSECRET", as_json(raw)["data"]["url"])
        r, raw = request("POST", "/api/action/agent-add", {"label": "x", "scope": "root"})
        self.assertEqual(r.status, 400)
        # pattern ip_list ловит форму записи (октеты > 255 отсеивает уже модуль кодом invalid_ips → 400)
        r, raw = request("POST", "/api/action/agent-add", {"label": "x", "allow_ips": "office-lan"})
        self.assertEqual(r.status, 400)
        with open(ARGS_FILE, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        self.assertTrue(any("--action=agent-add\t--json\t--label=Новый агент\t--scope=read\t--days=30\t--allow-ips=203.0.113.0/24" in l for l in lines), lines[-3:])
        # аудит панели: только id, без адреса-ключа
        with open(os.path.join(TMP, "audit.log"), encoding="utf-8") as fh:
            audit = fh.read()
        self.assertIn("agent-show", audit)
        self.assertNotIn("FAKESECRET", audit)
        self.assertNotIn("NEWSECRET", audit)
        r, raw = request("POST", "/api/action/mcp-http-off", {})
        self.assertEqual(r.status, 200, raw)
        self.assertFalse(as_json(raw)["data"]["enabled"])
        r, raw = request("POST", "/api/action/mcp-http-on", {})
        self.assertEqual(r.status, 200, raw)
        self.assertTrue(as_json(raw)["data"]["enabled"])

    def test_site_templates_catalog(self):
        r, raw = request("GET", "/api/site/templates")
        d = as_json(raw)["data"]
        self.assertTrue(d["available"])
        self.assertEqual([c["id"] for c in d["categories"]], ["landing", "business"])
        self.assertEqual(d["items"], [])
        r, raw = request("GET", "/api/site/templates?cat=landing")
        d = as_json(raw)["data"]
        self.assertEqual([i["id"] for i in d["items"]], ["tpl_alpha", "tpl_beta"])
        self.assertEqual(d["pages"], 1)
        r, raw = request("GET", "/api/site/templates?q=cafe")
        d = as_json(raw)["data"]
        self.assertEqual([i["id"] for i in d["items"]], ["tpl_beta"])
        r, raw = request("GET", "/api/site/templates?cat=BAD!")
        self.assertEqual(r.status, 400)
        r, raw = request("GET", "/api/site/template")
        d = as_json(raw)["data"]
        self.assertEqual(d["template_id"], "tpl_alpha")
        self.assertEqual(d["template"]["name"], "Alpha")

    def test_status_cache_five_seconds(self):
        # Строки целиком: подстрока «status» есть и в других действиях (edge-status).
        # Снимок рукопожатий — свежим сбором раздела «Трафик»: Обзор его не ждёт (PERF-03), а здесь нужен готовый.
        request("GET", "/api/traffic")
        with open(COUNT_FILE, encoding="utf-8") as fh:
            before = fh.read().splitlines().count("status")
        request("GET", "/api/overview")
        r, raw = request("GET", "/api/overview")
        with open(COUNT_FILE, encoding="utf-8") as fh:
            after = fh.read().splitlines().count("status")
        self.assertLessEqual(after - before, 1)
        # Второй Обзор — из общего снимка, и ответ это говорит (заголовок для замеров); время снимка — в данных.
        self.assertIn("status=hit", r.getheader("X-GoAmnezia-Snapshot") or "")
        self.assertIn("traffic=hit", r.getheader("X-GoAmnezia-Snapshot") or "")
        self.assertIn("snapshot_at", as_json(raw)["data"])

    def test_overview_traffic_and_settings_do_not_wait_for_a_slow_bridge(self):
        """PERF-03: /api/traffic?wait=0 (так спрашивает Обзор) и «Настройки» медленный мост не ждут, раздел «Трафик» (без
        параметра) ждёт свежий сбор, как раньше. «Обновить» (X-GoAmnezia-Fresh) просит новый сбор и у тех, кто не ждёт:
        они отвечают прежним снимком (нет его — pending), а новый собирается в фоне (PERF-08: раньше при готовом снимке —
        pending, «холодная» форма Обзора)."""
        fresh = {"X-GoAmnezia-Fresh": "1"}
        request("GET", "/api/traffic")
        time.sleep(2.1)                                   # «Обновить» пересобирает снимок старше 2 с
        with open(SLEEP_FILE, "w", encoding="utf-8") as fh:
            fh.write("1")
        try:
            t0 = time.monotonic()
            r, raw = request("GET", "/api/traffic?wait=0", headers=fresh)
            quick = time.monotonic() - t0
            self.assertEqual(r.status, 200)
            d = as_json(raw)["data"]
            self.assertNotIn("pending", d)                # прежний снимок, а не «собирается»
            self.assertTrue(d["users"])
            t0 = time.monotonic()
            r, raw = request("GET", "/api/settings", headers=fresh)
            settings = time.monotonic() - t0
            self.assertEqual(r.status, 200)
            self.assertLessEqual(set(as_json(raw)["data"]["pending"]), {"edge-status", "status"})
            t0 = time.monotonic()
            r, raw = request("GET", "/api/traffic", headers=fresh)
            page = time.monotonic() - t0
            self.assertNotIn("pending", as_json(raw)["data"])
        finally:
            os.unlink(SLEEP_FILE)
        self.assertLess(quick, 0.6)
        self.assertLess(settings, 0.6)
        self.assertGreater(page, 0.5)                     # раздел «Трафик» дождался сбора (мост спит 1 с)

    def test_request_without_language_reads_the_install_language_snapshots(self):
        """PERF-08 (168: Обзор по curl через 4 мин после рестарта — «холодная» форма, а страница шла на тёплых снимках):
        запрос без X-GoAmnezia-Lang (curl, скрипты проверки) живёт на снимках языка установки (здесь ru) — после
        русской страницы он ничего не собирает заново и отвечает тем же."""
        ru = {"X-GoAmnezia-Lang": "ru"}
        request("GET", "/api/traffic", headers=ru)       # рукопожатия — свежим сбором, как у раздела «Трафик»
        deadline = time.monotonic() + 10
        while True:                                       # страница дождалась фоновых сборов Обзора
            r, raw = request("GET", "/api/overview", headers=ru)
            if not as_json(raw)["data"]["pending"] or time.monotonic() > deadline:
                break
            time.sleep(0.2)
        self.assertEqual(as_json(raw)["data"]["pending"], [])
        r, raw = request("GET", "/api/overview")
        self.assertEqual(r.status, 200, raw)
        d = as_json(raw)["data"]
        self.assertEqual(d["pending"], [])
        self.assertIsNotNone(d["traffic_total"])
        self.assertNotIn("miss", r.getheader("X-GoAmnezia-Snapshot") or "")


class RelinePreviewRouteTests(unittest.TestCase):
    """DUPLINE-D-NIT: GET /api/profiles/<p>/reline-preview по HTTP — окно согласия «Переставить на 2.0» берёт шаги ядра из
    полного status (краткий их не несёт, N-3): шаги этого профиля; у другого — steps null; негодное имя — 400; без
    входа — 401."""

    def test_steps_of_the_profile_from_the_full_status(self):
        r, raw = request("GET", "/api/profiles/awg3/reline-preview")
        self.assertEqual(r.status, 200, raw[:300])
        d = as_json(raw)["data"]
        self.assertEqual(d, {"profile": "awg3", "steps": [
            {"kind": "page", "text": "Со страницей подключения (anna): открыть страницу заново и импортировать новый ключ."}]})
        with open(ARGS_FILE, encoding="utf-8") as fh:
            calls = [ln for ln in fh.read().splitlines() if ln.startswith("--action=status\t")]
        self.assertTrue(any("--brief=on" not in ln for ln in calls), "шаги — из полного status, не краткого")

    def test_other_profile_has_no_steps(self):
        r, raw = request("GET", "/api/profiles/awg2/reline-preview")
        self.assertEqual(r.status, 200)
        self.assertEqual(as_json(raw)["data"], {"profile": "awg2", "steps": None})

    def test_bad_profile_and_no_login(self):
        r, raw = request("GET", "/api/profiles/AWG3%3B/reline-preview")
        self.assertIn(r.status, (400, 404))
        r, raw = request("GET", "/api/profiles/awg9/reline-preview")
        self.assertEqual(r.status, 400)
        self.assertEqual(as_json(raw)["code"], "bad_arg")
        r, raw = request("GET", "/api/profiles/awg3/reline-preview", public=True)
        self.assertEqual(r.status, 401)


class ActionApiTests(unittest.TestCase):
    def test_bulk_add_goes_to_the_bridge_and_bad_input_stops_before_it(self):
        """«Добавить сразу несколько» (users-add-bulk): число, профили, префикс и язык подписей — мосту как есть;
        число вне 1–100, пустой список профилей и кривой префикс отсекаются по каталогу ядра до моста."""
        r, raw = request("POST", "/api/action/users-add-bulk", {"count": "3", "profiles": "awg2", "prefix": "team", "lang": "ru"})
        self.assertEqual(r.status, 200, raw)
        d = as_json(raw)["data"]
        self.assertEqual([a["name"] for a in d["added"]], ["team-01", "team-02", "team-03"])
        self.assertFalse(d["partial"])
        with open(ARGS_FILE, encoding="utf-8") as fh:
            last = fh.read().strip().splitlines()[-1].split("\t")
        for want in ("--action=users-add-bulk", "--count=3", "--profiles=awg2", "--prefix=team", "--lang=ru"):
            self.assertIn(want, last)
        for body, code in (({"count": "0"}, "bad_arg"), ({"count": "101"}, "bad_arg"), ({"count": "abc"}, "bad_arg"),
                           ({"count": "3", "profiles": ""}, "no_profiles_picked"),
                           ({"count": "3", "prefix": "bad prefix"}, "bad_arg"), ({}, "missing_arg")):
            with open(ARGS_FILE, encoding="utf-8") as fh:
                calls = fh.read().count("--action=users-add-bulk")
            r, raw = request("POST", "/api/action/users-add-bulk", body)
            self.assertEqual(r.status, 400, (body, raw))
            self.assertEqual(as_json(raw)["code"], code, body)
            with open(ARGS_FILE, encoding="utf-8") as fh:
                self.assertEqual(fh.read().count("--action=users-add-bulk"), calls, body)   # мост не звался

    def test_bulk_delete_confirm_is_the_number_and_goes_to_the_bridge(self):
        """«Удалить сразу несколько» (users-del-bulk): своё подтверждение — число людей; оно уходит мосту вместе со
        способом выбора, а сверяет его ядро. Без числа, не числом 1–100 и с чужим параметром — отказ до моста;
        список успел измениться — 409 confirm_mismatch, и в ответе matched — сколько выбрано сейчас."""
        r, raw = request("POST", "/api/action/users-del-bulk-preview", {"state": "disabled"})
        self.assertEqual(r.status, 200, raw)
        d = as_json(raw)["data"]
        self.assertEqual((d["count"], [u["name"] for u in d["selected"]]), (1, ["bob"]))
        r, raw = request("POST", "/api/action/users-del-bulk", {"names": "bob,old", "confirm": "2"})
        self.assertEqual(r.status, 200, raw)
        self.assertEqual(as_json(raw)["data"]["deleted"], ["bob", "old"])
        with open(ARGS_FILE, encoding="utf-8") as fh:
            last = fh.read().strip().splitlines()[-1].split("\t")
        self.assertEqual(last[:4], ["--action=users-del-bulk", "--json", "--confirm=2", "--names=bob,old"])
        r, raw = request("POST", "/api/action/users-del-bulk", {"state": "expired", "confirm": 2})
        self.assertEqual(r.status, 409, raw)
        self.assertEqual((as_json(raw)["code"], as_json(raw)["data"]["matched"]), ("confirm_mismatch", 1))
        for body, code in (({"state": "expired"}, "confirm_required"), ({"state": "expired", "confirm": ""}, "confirm_required"),
                           ({"state": "expired", "confirm": "0"}, "bad_arg"), ({"state": "expired", "confirm": "101"}, "bad_arg"),
                           ({"state": "nobody", "confirm": "1"}, "bad_arg"), ({"names": "bad name", "confirm": "1"}, "bad_arg"),
                           ({"state": "expired", "confirm": "1", "evil": "1"}, "bad_arg")):
            with open(ARGS_FILE, encoding="utf-8") as fh:
                calls = fh.read().count("--action=users-del-bulk\t")
            r, raw = request("POST", "/api/action/users-del-bulk", body)
            self.assertEqual(r.status, 400, (body, raw))
            self.assertEqual(as_json(raw)["code"], code, body)
            with open(ARGS_FILE, encoding="utf-8") as fh:
                self.assertEqual(fh.read().count("--action=users-del-bulk\t"), calls, body)   # мост не звался

    def test_mutation_goes_to_bridge_with_who(self):
        r, raw = request("POST", "/api/action/user-rename", {"name": "anna", "label": "Анна Б"})
        self.assertEqual(r.status, 200, raw)
        d = as_json(raw)
        self.assertTrue(d["ok"])
        self.assertEqual(d["data"]["status"], "success")
        with open(ARGS_FILE, encoding="utf-8") as fh:
            lines = fh.read().strip().splitlines()
        self.assertIn("--action=user-rename", lines[-1])
        self.assertIn("--label=Анна Б", lines[-1])
        with open(ENV_FILE, encoding="utf-8") as fh:
            env = fh.read().strip().splitlines()
        self.assertEqual(env[-1], "panel:tunnel panel")
        self.assertNotIn("LEAK", "\n".join(env))

    def test_bridge_answers_in_the_viewer_language(self):
        """L-24: язык интерфейса панели (заголовок X-GoAmnezia-Lang) уходит мосту в GOAMNEZIA_CALL_LANG на этот
        вызов; без заголовка или с чужим значением — переменной нет, мост говорит на языке установки. У чтений
        с кэшем — свой кэш на язык: английский ответ не достаётся русскому интерфейсу."""
        def last():
            with open(LANG_FILE, encoding="utf-8") as fh:
                return fh.read().strip().splitlines()[-1]
        r, raw = request("POST", "/api/action/user-rename", {"name": "anna", "label": "Anna"},
                         headers={"X-GoAmnezia-Lang": "en"})
        self.assertEqual(r.status, 200, raw)
        self.assertEqual(last(), "user-rename en")
        request("POST", "/api/action/user-rename", {"name": "anna", "label": "Анна"}, headers={"X-GoAmnezia-Lang": "RU"})
        self.assertEqual(last(), "user-rename ru")
        for headers in ({}, {"X-GoAmnezia-Lang": "de"}, {"X-GoAmnezia-Lang": "en;rm -rf"}):
            request("POST", "/api/action/user-rename", {"name": "anna", "label": "Анна"}, headers=headers)
            self.assertEqual(last(), "user-rename -", headers)
        with open(LANG_FILE, encoding="utf-8") as fh:
            before = fh.read().count("status ")
        request("GET", "/api/overview", headers={"X-GoAmnezia-Lang": "en"})
        request("GET", "/api/overview", headers={"X-GoAmnezia-Lang": "ru"})
        with open(LANG_FILE, encoding="utf-8") as fh:
            calls = [l for l in fh.read().splitlines() if l.startswith("status ")][before:]
        self.assertIn("status en", calls)
        self.assertIn("status ru", calls)

    def test_validation_before_bridge(self):
        with open(ARGS_FILE, encoding="utf-8") as fh:
            before = len(fh.read().splitlines())
        r, raw = request("POST", "/api/action/user-quota", {"name": "anna", "quota_gb": "abc"})
        self.assertEqual(r.status, 400)
        self.assertEqual(as_json(raw)["code"], "bad_arg")
        r, raw = request("POST", "/api/action/user-quota", {"name": "anna"})
        self.assertEqual(r.status, 400)
        self.assertEqual(as_json(raw)["code"], "missing_arg")
        r, raw = request("POST", "/api/action/rm-rf", {})
        self.assertEqual(r.status, 404)
        self.assertEqual(as_json(raw)["code"], "unknown_action")
        with open(ARGS_FILE, encoding="utf-8") as fh:
            self.assertEqual(len(fh.read().splitlines()), before)  # мост не вызывался

    def test_destructive_requires_confirm(self):
        r, raw = request("POST", "/api/action/user-del", {"name": "anna"})
        self.assertEqual(r.status, 400)
        self.assertEqual(as_json(raw)["code"], "confirm_required")
        r, raw = request("POST", "/api/action/user-del", {"name": "anna", "confirm": "bob"})
        self.assertEqual(r.status, 400)
        r, raw = request("POST", "/api/action/user-del", {"name": "anna", "confirm": "anna"})
        self.assertEqual(r.status, 200, raw)
        r, raw = request("POST", "/api/action/profile-set-param", {"profile": "awg2", "key": "S1", "value": "40", "confirm": "awg2"})
        self.assertEqual(r.status, 200, raw)
        self.assertTrue(as_json(raw)["data"]["reissue_required"])
        r, raw = request("POST", "/api/action/profile-set-param", {"profile": "awg2", "key": "I1", "value": "", "confirm": "awg2"})
        self.assertEqual(r.status, 200, raw)
        with open(ARGS_FILE, encoding="utf-8") as fh:
            self.assertIn("--value=\t", fh.read().splitlines()[-1] + "\t")

    def test_bridge_errors_map_to_http(self):
        r, raw = request("POST", "/api/action/miniapp-status", {})
        self.assertEqual(r.status, 501)
        self.assertEqual(as_json(raw)["code"], "not_implemented")
        # Порт закрыт локальным файрволом (docs/PREFLIGHT.md §6): конфликт состояния → 409
        r, raw = request("POST", "/api/action/mcp-http-on", {"port": "40000"})
        self.assertEqual(r.status, 409)
        self.assertEqual(as_json(raw)["code"], "closed_local")
        self.assertEqual(as_json(raw)["data"]["hint"], "ufw allow 40000/tcp")
        r, raw = request("POST", "/api/action/profile-port", {"profile": "awg3", "port": "443"})
        self.assertEqual(r.status, 409)
        self.assertEqual(as_json(raw)["code"], "port_taken")
        r, raw = request("POST", "/api/action/site-check", {})
        self.assertEqual(r.status, 200)
        self.assertEqual(as_json(raw)["data"]["verdict"], "not_pro")

    def test_upstream_and_import_routes(self):
        """Спринт 4: карточка обновлений и панель импорта на ответах моста,
        обновление/откат только с confirm=profile, подхват без confirm."""
        r, raw = request("GET", "/api/upstream")
        self.assertEqual(r.status, 200, raw)
        d = as_json(raw)["data"]
        self.assertTrue(d["reachable"])
        self.assertIsNone(d["error"])
        self.assertTrue(d["check"]["updates_available"])
        self.assertEqual(d["check"]["client_release"]["tag"], "5.0.3.0")
        self.assertEqual([p["profile"] for p in d["profiles"]], ["awg2", "awg3"])
        self.assertTrue(d["profiles"][0]["update"])
        self.assertTrue(d["profiles"][1]["rollback_available"])
        self.assertEqual(d["check"]["unsupported"][0]["name"], "CONTENT_PADDING_MAX")
        r, raw = request("POST", "/api/action/upstream-update", {"profile": "awg2"})
        self.assertEqual(r.status, 400)
        self.assertEqual(as_json(raw)["code"], "confirm_required")
        r, raw = request("POST", "/api/action/upstream-update", {"profile": "awg2", "confirm": "awg2"})
        self.assertEqual(r.status, 200, raw)
        self.assertEqual(as_json(raw)["data"]["image_tag"], "3.2.20260915")
        self.assertEqual(as_json(raw)["data"]["previous_tag"], "3.1.20260828")
        r, raw = request("POST", "/api/action/upstream-rollback", {"profile": "awg3", "confirm": "awg3"})
        self.assertEqual(r.status, 200, raw)
        self.assertTrue(as_json(raw)["data"]["scripts_restored"])
        r, raw = request("POST", "/api/action/core-update", {"profile": "awg2", "confirm": "awg2"})
        self.assertEqual(r.status, 200, raw)
        self.assertTrue(as_json(raw)["data"]["repair"])
        r, raw = request("GET", "/api/import")
        self.assertEqual(r.status, 200, raw)
        d = as_json(raw)["data"]
        self.assertEqual([c["container"] for c in d["containers"]], ["amnezia-awg2", "amnezia-awg"])
        self.assertEqual(d["adoptable"], ["amnezia-awg"])
        self.assertTrue(d["containers"][0]["managed"])
        self.assertEqual(d["containers"][1]["suggested_profile"], "awg1")
        self.assertNotIn("privkey", raw.decode("utf-8"))
        r, raw = request("POST", "/api/action/import-adopt", {"container": "amnezia-awg"})
        self.assertEqual(r.status, 200, raw)
        self.assertEqual(as_json(raw)["data"]["profile"], "awg1")
        self.assertEqual(as_json(raw)["data"]["users"], ["Keenetic"])
        r, raw = request("POST", "/api/action/import-adopt", {"container": "amnezia-xray"})
        self.assertEqual(r.status, 409)
        self.assertEqual(as_json(raw)["code"], "unsupported_container")
        r, raw = request("POST", "/api/action/import-adopt", {"container": "nginx-proxy"})
        self.assertEqual(r.status, 400)
        r, raw = request("POST", "/api/action/import-reconcile", {"profile": "awg2"})
        self.assertEqual(r.status, 200, raw)
        self.assertFalse(as_json(raw)["data"]["in_sync"])
        self.assertFalse(as_json(raw)["data"]["applied"])
        r, raw = request("POST", "/api/action/import-reconcile", {"profile": "awg2", "apply": "on"})
        self.assertEqual(r.status, 200, raw)
        self.assertTrue(as_json(raw)["data"]["applied"])
        with open(ARGS_FILE, encoding="utf-8") as fh:
            self.assertIn("--action=import-reconcile\t--json\t--profile=awg2\t--apply=on", fh.read())

    def test_kernel_module_card_and_background_actions(self):
        """Ускорение AmneziaWG (модуль ядра, KMOD_PLAN этап Б): GET /api/kmod — kmod-status белым списком и действие
        панели; POST /api/kmod/<действие> без confirm = действие — 400 и мост не зовётся, с ним — ответ сразу, а цепочка
        моста идёт в фоне (enable: kmod-install, затем kmod-engine --engine=kernel с confirm каталога); незнакомое —
        404, без предохранителя от CSRF — 403. Проверка хоста — POST /api/action/kmod-check (только чтение)."""
        r, raw = request("GET", "/api/kmod")
        self.assertEqual(r.status, 200, raw)
        d = as_json(raw)["data"]
        self.assertEqual((d["kmod"]["state"], d["kmod"]["kernel"], d["error"]), ("off", "6.8.0-36-generic", None))
        self.assertEqual(d["kmod"]["profiles"], {"awg2": {"engine": "program", "want": ""}, "awg3": {"engine": "stopped", "want": ""}})
        self.assertNotIn("<script>", raw.decode("utf-8"))
        r, raw = request("POST", "/api/action/kmod-check", {})
        self.assertEqual(r.status, 200, raw)
        self.assertEqual(as_json(raw)["data"]["check"]["plan"]["size_mb"], 214)
        with open(ARGS_FILE, encoding="utf-8") as fh:
            before = len(fh.read().splitlines())
        for body in ({}, {"confirm": "program"}):
            r, raw = request("POST", "/api/kmod/enable", body)
            self.assertEqual((r.status, as_json(raw)["code"]), (400, "confirm_required"), body)
        r, raw = request("POST", "/api/kmod/format", {"confirm": "format"})
        self.assertEqual((r.status, as_json(raw)["code"]), (404, "unknown_action"))
        r, raw = request("POST", "/api/kmod/enable", {"confirm": "enable"}, guard=False)
        self.assertEqual(r.status, 403)
        with open(ARGS_FILE, encoding="utf-8") as fh:
            self.assertEqual(len(fh.read().splitlines()), before, "без согласия мост не зовётся")
        r, raw = request("POST", "/api/kmod/enable", {"confirm": "enable", "headers_meta": "off"}, headers={"X-GoAmnezia-Lang": "en"})
        self.assertEqual(r.status, 200, raw)
        job = as_json(raw)["data"]["job"]
        self.assertEqual(job["op"], "enable")
        deadline = time.time() + 20
        while time.time() < deadline and not job["done"]:
            time.sleep(0.2)
            job = as_json(request("GET", "/api/kmod")[1])["data"]["job"]
        self.assertTrue(job["done"], job)
        self.assertEqual([(s["action"], s["ok"]) for s in job["steps"]], [("kmod-install", True), ("kmod-engine", True)])
        self.assertEqual(job["steps"][1]["profiles"][0]["return_s"], 17)
        with open(ARGS_FILE, encoding="utf-8") as fh:
            calls = [l for l in fh.read().splitlines()[before:] if "--action=kmod-" in l and "kmod-status" not in l]
        self.assertEqual(calls, ["--action=kmod-install\t--json\t--rebuild=off\t--headers-meta=off\t",
                                 "--action=kmod-engine\t--json\t--engine=kernel\t"])
        with open(LANG_FILE, encoding="utf-8") as fh:
            langs = fh.read()
        self.assertIn("kmod-install en", langs, "мост отвечает на языке того, кто нажал")
        self.assertIn("kmod-engine en", langs)

    def test_public_session_mutation_audited_as_code(self):
        tok = login_by_code_in_own_bucket("198.51.100.120")
        r, raw = request("POST", "/api/action/backup-create", {}, cookie=tok, headers={"X-Real-IP": "198.51.100.120"})
        self.assertEqual(r.status, 200, raw)
        self.assertEqual(as_json(raw)["data"]["file"], "goamnezia-backup-20260908_130000.tar.gz")
        with open(ENV_FILE, encoding="utf-8") as fh:
            self.assertEqual(fh.read().strip().splitlines()[-1], "panel:code panel")


class AuthAnswersTests(unittest.TestCase):
    """Решение владельца В4 (26.09.2026): у каждого отказа входа — машинный code и тот же текст на обоих языках
    (texts{ru, en}), error — на языке панели, как прежде; статусы HTTP прежние. Свои X-Real-IP из документационной
    сети — корзины кода и пароля не пересекаются с соседними тестами."""

    def check(self, r, raw, status, codes, **extra):
        d = as_json(raw)
        self.assertEqual(r.status, status, raw[:300])
        self.assertFalse(d.get("ok"))
        self.assertIn(d.get("code"), codes if isinstance(codes, tuple) else (codes,))
        texts = d.get("texts") or {}
        self.assertEqual(set(texts), {"ru", "en"}, raw[:300])
        self.assertTrue(texts["ru"] and texts["en"])
        self.assertNotEqual(texts["ru"], texts["en"])
        self.assertEqual(d.get("error"), texts["ru"])          # язык панели в config.json — ru
        for k, v in extra.items():
            self.assertEqual(d.get(k), v, k)
        return d

    def test_code_routes(self):
        ip = {"X-Real-IP": "198.51.100.181"}
        r, raw = request("POST", "/api/auth/request", {}, headers=ip)
        self.assertEqual(r.status, 200, raw)
        code = last_sink_code()
        r, raw = request("POST", "/api/auth/request", {}, headers=ip)
        d = self.check(r, raw, 429, "too_often", reason="cooldown")
        self.assertGreater(d["seconds"], 0)
        wrong = "000000" if code != "000000" else "111111"
        r, raw = request("POST", "/api/auth/verify", {"code": wrong}, headers=ip)
        self.check(r, raw, 401, "code_wrong", left=4)
        r, raw = request("POST", "/api/auth/verify", {"code": code}, headers={"X-Real-IP": "198.51.100.182"})
        self.check(r, raw, 401, "code_expired")                 # чужой адрес: своего кода нет
        r, raw = request("POST", "/api/auth/miniapp", {"init_data": "a=1&hash=" + "0" * 64}, public=True)
        self.check(r, raw, 401, "signature_invalid")

    def test_password_and_session_answers(self):
        r, raw = request("POST", "/api/auth/password", {"login": "admin", "password": "wrong-Sample-Pass-0"},
                         public=True, headers={"X-Real-IP": "198.51.100.183"})
        # Пароль к этому моменту задан или нет — зависит от порядка тестов; у обоих отказов код и тексты.
        self.check(r, raw, 401 if as_json(raw).get("code") == "bad_credentials" else 409,
                   ("bad_credentials", "password_unset"))
        r, raw = request("GET", "/api/users", public=True)
        self.check(r, raw, 401, "auth_required", state="none")
        r, raw = request("GET", "/api/users", public=True, cookie="no-such-session")
        self.check(r, raw, 401, "session_expired", state="expired")


class ShareRoutesTests(unittest.TestCase):
    """Временные ссылки на файл: список без секрета, адрес — только тому, кто попросил."""

    def test_list_has_no_secret_part_of_the_address(self):
        r, raw = request("GET", "/api/share")
        self.assertEqual(r.status, 200, raw)
        d = as_json(raw)["data"]
        self.assertTrue(d["available"])
        self.assertEqual(d["count"], 1)
        self.assertEqual(d["shares"][0]["id"], "s-4b1e")
        self.assertEqual(d["shares"][0]["seconds_left"], 214)
        # Заглушка кладёт в список url и path — наружу они уходить не должны.
        body = raw.decode("utf-8")
        self.assertNotIn("MUSTNOTLEAK", body)
        self.assertNotIn('"url"', body)
        self.assertNotIn('"path"', body)

    def test_create_returns_the_address_and_keeps_it_out_of_the_audit(self):
        r, raw = request("POST", "/api/action/share-create", {"name": "anna", "profile": "awg2", "format": "conf"})
        self.assertEqual(r.status, 200, raw)
        d = as_json(raw)["data"]
        self.assertEqual(d["id"], "s-4b1e")
        self.assertTrue(d["url"].startswith("https://203.0.113.10:38472/"))
        self.assertEqual(d["ttl"], 300)
        self.assertTrue(d["selfsigned"])
        with open(os.path.join(TMP, "audit.log"), encoding="utf-8") as fh:
            audit = fh.read()
        self.assertIn('"share-create"', audit)
        self.assertNotIn("FAKESECRET", audit)
        # Формат проверяется до моста: QR по ссылке панель не раздаёт, но
        # выдумывать свой формат нельзя и подавно.
        r, raw = request("POST", "/api/action/share-create", {"name": "anna", "profile": "awg2", "format": "gif"})
        self.assertEqual(r.status, 400)

    def test_revoke_is_destructive_and_needs_confirm(self):
        r, raw = request("POST", "/api/action/share-revoke", {"id": "s-4b1e"})
        self.assertEqual(r.status, 400)
        self.assertEqual(as_json(raw)["code"], "confirm_required")
        r, raw = request("POST", "/api/action/share-revoke", {"id": "s-4b1e", "confirm": "s-4b1e"})
        self.assertEqual(r.status, 200, raw)
        self.assertEqual(as_json(raw)["data"]["id"], "s-4b1e")
        # Идентификатор проверяется по share_id ещё в панели.
        r, raw = request("POST", "/api/action/share-revoke", {"id": "../etc", "confirm": "../etc"})
        self.assertEqual(r.status, 400)
        # У «снять все» параметров нет — confirm сверяется с именем действия.
        r, raw = request("POST", "/api/action/share-revoke-all", {})
        self.assertEqual(r.status, 400)
        r, raw = request("POST", "/api/action/share-revoke-all", {"confirm": "share-revoke-all"})
        self.assertEqual(r.status, 200, raw)


class PagesRoutesTests(unittest.TestCase):
    """Страница подключения (docs/CLIENTS.md §9–§10): список без адреса, адрес с QR —
    только тому, кто попросил, срок проверяется до моста, отзыв с confirm."""

    SECRET = "FAKESECRETFAKESECRETFAKESECRETFAKESECRET012"

    def _audit(self):
        with open(os.path.join(TMP, "audit.log"), encoding="utf-8") as fh:
            return fh.read()

    def test_list_has_no_address_and_filters_by_user(self):
        r, raw = request("GET", "/api/pages")
        self.assertEqual(r.status, 200, raw)
        d = as_json(raw)["data"]
        self.assertTrue(d["available"])
        self.assertEqual(sorted(p["id"] for p in d["pages"]), ["p-0bad", "p-7f3a", "p-9c1d"])
        self.assertEqual(d["count"], 3)
        first = next(p for p in d["pages"] if p["id"] == "p-7f3a")
        self.assertEqual(first["visits"], 3)
        self.assertEqual(first["profiles"], ["awg2", "awg3"])
        self.assertFalse(first["forever"])
        # Заглушка кладёт в список url и path — наружу они уходить не должны.
        body = raw.decode("utf-8")
        self.assertNotIn("MUSTNOTLEAK", body)
        self.assertNotIn('"url"', body)
        self.assertNotIn('"path"', body)
        r, raw = request("GET", "/api/pages?name=anna")
        self.assertEqual(sorted(p["id"] for p in as_json(raw)["data"]["pages"]), ["p-7f3a", "p-9c1d"])
        r, raw = request("GET", "/api/pages?name=bob")
        self.assertEqual([p["id"] for p in as_json(raw)["data"]["pages"]], ["p-0bad"])
        r, raw = request("GET", "/api/pages?name=..%2Fetc")
        self.assertEqual(r.status, 400)

    def test_create_returns_the_address_with_qr_and_keeps_it_out_of_the_audit(self):
        r, raw = request("POST", "/api/action/page-create", {"name": "anna", "ttl": "24h"})
        self.assertEqual(r.status, 200, raw)
        d = as_json(raw)["data"]
        self.assertEqual(d["id"], "p-7f3a")
        self.assertEqual(d["url"], "https://vpn.example.com/p-%s/" % self.SECRET)
        self.assertEqual(d["expires"], "2026-09-18T18:41:07Z")
        self.assertFalse(d["forever"])
        # QR рисует сервер панели — qrencode; без него поле пустое, а адрес всё равно есть.
        self.assertIn("qr_png_b64", d)
        if shutil.which("qrencode"):
            self.assertEqual(base64.b64decode(d["qr_png_b64"])[:8], PNG_SIGNATURE)
        else:
            self.assertEqual(d["qr_png_b64"], "")
        audit = self._audit()
        self.assertIn('"page-create"', audit)
        self.assertNotIn(self.SECRET, audit)
        r, raw = request("POST", "/api/action/page-create", {"name": "anna", "ttl": "forever"})
        self.assertEqual(r.status, 200, raw)
        self.assertTrue(as_json(raw)["data"]["forever"])
        # Срок проверяется до моста: мусор не запускает bash.
        with open(ARGS_FILE, encoding="utf-8") as fh:
            before = fh.read().count("--action=page-create")
        for bad in ("3w", "12", "0h", "forever!"):
            r, raw = request("POST", "/api/action/page-create", {"name": "anna", "ttl": bad})
            self.assertEqual(r.status, 400, bad)
        with open(ARGS_FILE, encoding="utf-8") as fh:
            self.assertEqual(fh.read().count("--action=page-create"), before)

    def test_url_again_only_by_post_and_with_a_valid_id(self):
        r, raw = request("POST", "/api/action/page-url", {"id": "p-7f3a"})
        self.assertEqual(r.status, 200, raw)
        d = as_json(raw)["data"]
        self.assertIn(self.SECRET, d["url"])
        self.assertIn("qr_png_b64", d)
        r, raw = request("POST", "/api/action/page-url", {"id": "../etc"})
        self.assertEqual(r.status, 400)
        r, raw = request("POST", "/api/action/page-url", {"id": "p-dead"})
        self.assertEqual(r.status, 404, raw)
        self.assertEqual(as_json(raw)["code"], "page_not_found")
        self.assertNotIn(self.SECRET, self._audit())

    def test_revoke_follows_the_catalog(self):
        # В каталоге ядра page-revoke не помечен разрушительным: вопрос задаёт
        # сама панель (confirmDialog в renderUserPages), сервер confirm не ждёт.
        # А page-revoke-all — admin-действие с confirm, как share-revoke-all.
        with open(ACTIONS_JSON, encoding="utf-8") as fh:
            spec = json.load(fh)["actions"]
        r, raw = request("POST", "/api/action/page-revoke", {"id": "p-7f3a"})
        if spec["page-revoke"].get("destructive"):
            self.assertEqual(r.status, 400)
            self.assertEqual(as_json(raw)["code"], "confirm_required")
            r, raw = request("POST", "/api/action/page-revoke", {"id": "p-7f3a", "confirm": "p-7f3a"})
        self.assertEqual(r.status, 200, raw)
        self.assertEqual(as_json(raw)["data"]["id"], "p-7f3a")
        r, raw = request("POST", "/api/action/page-revoke", {"id": "p-dead"})
        self.assertEqual(r.status, 404, raw)
        self.assertTrue(spec["page-revoke-all"].get("destructive"))
        r, raw = request("POST", "/api/action/page-revoke-all", {})
        self.assertEqual(r.status, 400)
        self.assertEqual(as_json(raw)["code"], "confirm_required")


class MirrorRoutesTests(unittest.TestCase):
    """Зеркало сборок (docs/CLIENTS.md §8): список с занятым местом, каталог только
    в объёме «что можно скачать», загрузка по каталогу, удаление с confirm."""

    def test_list_carries_state_version_size_and_usage(self):
        r, raw = request("GET", "/api/mirror")
        self.assertEqual(r.status, 200, raw)
        d = as_json(raw)["data"]
        self.assertTrue(d["available"])
        self.assertEqual(d["max_total_mb"], 3072)
        self.assertEqual(d["used_mb"], 412)
        states = {(i["app"], i["platform"]): i["state"] for i in d["items"]}
        self.assertEqual(states[("amneziavpn", "android")], "ready")
        self.assertEqual(states[("amneziavpn", "windows")], "downloading")
        self.assertEqual(states[("wgtunnel", "android")], "failed")
        downloading = next(i for i in d["items"] if i["state"] == "downloading")
        self.assertEqual(downloading["bytes"], 52428800)
        failed = next(i for i in d["items"] if i["state"] == "failed")
        self.assertEqual(failed["error"], "mirror_unverified")

    def test_clients_catalog_names_only_the_mirrorable_platforms(self):
        r, raw = request("GET", "/api/clients")
        self.assertEqual(r.status, 200, raw)
        d = as_json(raw)["data"]
        self.assertTrue(d["available"])
        apps = {a["id"]: a for a in d["apps"]}
        self.assertEqual(apps["amneziavpn"]["mirror_platforms"], ["android", "windows", "macos", "linux"])
        self.assertTrue(apps["amneziavpn"]["official"])
        self.assertFalse(apps["wgtunnel"]["official"])
        self.assertEqual(apps["defaultvpn"]["mirror_platforms"], [])
        self.assertEqual(apps["defaultvpn"]["platforms"], ["ios"])
        # Ссылки, лицензии и регексы сборок наружу не нужны — панели их нечем показать.
        self.assertNotIn("assets", raw.decode("utf-8"))

    def test_fetch_and_remove(self):
        r, raw = request("POST", "/api/action/mirror-fetch", {"app": "amneziavpn", "platform": "android"})
        self.assertEqual(r.status, 200, raw)
        self.assertEqual(as_json(raw)["data"]["state"], "queued")
        r, raw = request("POST", "/api/action/mirror-fetch", {"app": "amneziavpn", "platform": "amiga"})
        self.assertEqual(r.status, 400)
        r, raw = request("POST", "/api/action/mirror-fetch", {"app": "defaultvpn", "platform": "ios"})
        self.assertEqual(r.status, 409, raw)
        self.assertEqual(as_json(raw)["code"], "not_mirrorable")
        r, raw = request("POST", "/api/action/mirror-remove", {"app": "amneziavpn", "platform": "android"})
        self.assertEqual(r.status, 400)
        self.assertEqual(as_json(raw)["code"], "confirm_required")
        r, raw = request("POST", "/api/action/mirror-remove", {"app": "amneziavpn", "platform": "android", "confirm": "amneziavpn"})
        self.assertEqual(r.status, 200, raw)
        self.assertEqual(as_json(raw)["data"]["platform"], "android")


class PreflightAndLogStatusTests(unittest.TestCase):
    """Проверка портов и бюджет логов — то, что было только в SSH-меню."""

    def test_preflight_shows_modes_ports_and_advice(self):
        r, raw = request("GET", "/api/preflight")
        self.assertEqual(r.status, 200, raw)
        d = as_json(raw)["data"]
        self.assertTrue(d["available"])
        self.assertTrue(d["modes"]["lite"]["available"])
        self.assertFalse(d["lite_site_available"])
        self.assertFalse(d["pro_available"])
        self.assertEqual(d["modes"]["ports"]["tcp443"]["unit"], "nginx.service")
        self.assertEqual(d["modes"]["ports"]["tcp443"]["product"], "gotelegram")
        self.assertTrue(d["modes"]["ports"]["udp443"]["ours"])
        self.assertIn("nginx", d["hint"])
        self.assertTrue(d["modes"]["pro"]["reasons"][0]["message"])

    def test_logs_status_shows_used_and_budget(self):
        r, raw = request("GET", "/api/logs/status")
        self.assertEqual(r.status, 200, raw)
        d = as_json(raw)["data"]
        self.assertTrue(d["available"])
        rows = {c["area"]: c for c in d["areas"]}
        self.assertEqual(rows["goamnezia"]["budget_mb"], "200")
        self.assertEqual(rows["journal"]["percent"], "90")  # голое число, как печатает logb_status
        self.assertIn("free", rows)
        # Маршрут логов остался прежним и не перехвачен статусом.
        r, raw = request("GET", "/api/logs?component=system&lines=60")
        self.assertEqual(r.status, 200)


class KeyEndpointRoutesTests(unittest.TestCase):
    """«Ключ на другой адрес сервера»: ?endpoint= у /key и /conf, endpoint у share-create, варианты в карточке.

    Заглушка моста ведёт себя по контракту: допустимы адреса из edge-status и vpn.example.com; 0.0.0.0 —
    invalid_ip, 192.0.2.1 — addresses_unknown, остальное — address_not_here (rc 2).
    """

    @staticmethod
    def args_lines():
        with open(ARGS_FILE, encoding="utf-8") as fh:
            return fh.read().splitlines()

    # Фоновые сборы панели (снимки status, traffic, адресов — PERF-03/04) пишут в тот же журнал вызовов и могут
    # встать после проверяемого вызова: берём последний вызов, который не фоновое чтение (TEST-FLAKE-03).
    BG_READS = ("--action=edge-status", "--action=traffic", "--action=status", "--action=profiles-status")

    def last_action_line(self):
        lines = [l for l in self.args_lines() if not l.startswith(self.BG_READS)]
        return lines[-1] if lines else ""

    def test_key_with_endpoint_reaches_the_bridge(self):
        r, raw = request("GET", "/api/users/anna/key?profile=awg2&format=all&endpoint=198.51.100.20")
        self.assertEqual(r.status, 200, raw)
        d = as_json(raw)["data"]
        self.assertEqual(d["key_host"], "198.51.100.20")
        self.assertEqual(d["key_endpoint"], "198.51.100.20:443")
        self.assertIs(d["key_host_override"], True)
        self.assertIn("Endpoint = 198.51.100.20:443", d["conf"])
        last = self.last_action_line().rstrip("\t").split("\t")
        self.assertEqual(last[0], "--action=user-key")
        self.assertIn("--endpoint=198.51.100.20", last)

    def test_empty_or_missing_endpoint_is_not_passed(self):
        for q in ("", "&endpoint=", "&endpoint=%20"):
            r, raw = request("GET", "/api/users/anna/key?profile=awg2&format=all" + q)
            self.assertEqual(r.status, 200, (q, raw))
            self.assertIs(as_json(raw)["data"]["key_host_override"], False, q)
            # Вызов ядра байт в байт прежний: ни пустого --endpoint=, ни порядка аргументов не поменяли.
            self.assertEqual(self.last_action_line().rstrip("\t").split("\t"),
                             ["--action=user-key", "--json", "--name=anna", "--profile=awg2", "--format=all"], q)

    def test_garbage_is_refused_without_running_the_bridge(self):
        before = len(self.args_lines())
        for bad in ("1.2.3.4%3Breboot", "a%20b", "%22x%22", "http%3A%2F%2Fx", "x" * 300):
            for route in ("key", "conf"):
                r, raw = request("GET", "/api/users/anna/%s?profile=awg2&endpoint=%s" % (route, bad))
                self.assertEqual(r.status, 400, (route, bad, raw))
                self.assertEqual(as_json(raw)["code"], "bad_arg", (route, bad))
        r, raw = request("POST", "/api/action/share-create",
                         {"name": "anna", "profile": "awg2", "format": "conf", "endpoint": "1.2.3.4;reboot"})
        self.assertEqual(r.status, 400, raw)
        self.assertEqual(len(self.args_lines()), before, "мусор дошёл до моста")

    def test_refusals_of_the_core_come_back_as_4xx_with_its_words(self):
        for host, status, code, word in (("10.1.1.1", 400, "address_not_here", "Допустимо: 203.0.113.10"),
                                         ("0.0.0.0", 400, "invalid_ip", "Негодный адрес IPv4"),
                                         ("192.0.2.1", 409, "addresses_unknown", "не видит своих внешних адресов")):
            r, raw = request("GET", "/api/users/anna/key?profile=awg2&format=all&endpoint=" + host)
            d = as_json(raw)
            self.assertEqual(r.status, status, raw)
            self.assertEqual(d["code"], code)
            self.assertIn(word, d["error"])
            # /conf отвечает той же ошибкой в JSON, а не файлом с ошибкой внутри.
            r, raw = request("GET", "/api/users/anna/conf?profile=awg2&endpoint=" + host)
            self.assertEqual(r.status, status, raw)
            self.assertIn("application/json", r.getheader("Content-Type"))
            self.assertIsNone(r.getheader("Content-Disposition"))
            self.assertEqual(as_json(raw)["code"], code)

    def test_conf_download_is_named_with_the_host(self):
        r, raw = request("GET", "/api/users/anna/conf?profile=awg2&endpoint=198.51.100.20")
        self.assertEqual(r.status, 200, raw)
        self.assertIn('filename="anna-awg2-198.51.100.20.conf"', r.getheader("Content-Disposition"))
        self.assertIn(b"Endpoint = 198.51.100.20:443", raw)
        self.assertIn("--endpoint=198.51.100.20", self.last_action_line())
        r, raw = request("GET", "/api/users/anna/conf?profile=awg3&endpoint=vpn.example.com")
        self.assertEqual(r.status, 200, raw)
        self.assertIn('filename="anna-awg3-vpn.example.com.conf"', r.getheader("Content-Disposition"))
        self.assertIn(b"Endpoint = vpn.example.com:39001", raw)
        # Без адреса — прежнее имя файла.
        r, raw = request("GET", "/api/users/anna/conf?profile=awg2&endpoint=")
        self.assertIn('filename="anna-awg2.conf"', r.getheader("Content-Disposition"))

    def test_conf_download_takes_the_file_name_from_the_core(self):
        """П-1: ядро назвало файл (file_name) — /conf отдаёт под этим именем, и с разовым адресом тоже; негодное имя
        (не ^[A-Za-z0-9_-]{1,32}\\.conf$) — прежнее <имя>-<профиль>[-<хост>].conf."""
        for q in ("", "&endpoint=198.51.100.20"):
            r, raw = request("GET", "/api/users/kim/conf?profile=awg2" + q)
            self.assertEqual(r.status, 200, raw)
            self.assertEqual(r.getheader("Content-Disposition"), 'attachment; filename="kim-2_0.conf"', q)
        r, raw = request("GET", "/api/users/junk/conf?profile=awg2")
        self.assertEqual(r.status, 200, raw)
        self.assertEqual(r.getheader("Content-Disposition"), 'attachment; filename="junk-awg2.conf"')
        r, raw = request("GET", "/api/users/junk/conf?profile=awg2&endpoint=198.51.100.20")
        self.assertEqual(r.getheader("Content-Disposition"), 'attachment; filename="junk-awg2-198.51.100.20.conf"')

    def test_share_create_carries_the_address(self):
        r, raw = request("POST", "/api/action/share-create",
                         {"name": "anna", "profile": "awg2", "format": "conf", "endpoint": "vpn.example.com"})
        self.assertEqual(r.status, 200, raw)
        d = as_json(raw)["data"]
        self.assertEqual(d["file_name"], "anna-awg2-vpn.example.com.conf")
        self.assertEqual(d["endpoint"], "vpn.example.com")
        self.assertIn("--endpoint=vpn.example.com", self.last_action_line())
        r, raw = request("POST", "/api/action/share-create",
                         {"name": "anna", "profile": "awg2", "format": "conf", "endpoint": "10.1.1.1"})
        self.assertEqual(r.status, 400, raw)
        self.assertEqual(as_json(raw)["code"], "address_not_here")
        # Список раздач несёт адрес (пусто — как обычно), секретной части адреса в нём по-прежнему нет.
        r, raw = request("GET", "/api/share")
        item = as_json(raw)["data"]["shares"][0]
        self.assertEqual(item["endpoint"], "")
        self.assertNotIn("url", item)

    def test_card_carries_the_choices(self):
        r, raw = request("GET", "/api/users/anna")
        self.assertEqual(r.status, 200, raw)
        u = as_json(raw)["data"]["user"]
        self.assertEqual(u["key_host"], "203.0.113.10")
        self.assertIs(u["key_host_gone"], False)
        self.assertEqual([c["host"] for c in u["key_choices"]],
                         ["203.0.113.10", "vpn.example.com", "198.51.100.20", "198.51.100.30"])
        self.assertIs(u["key_choices"][0]["default"], True)
        self.assertEqual(u["key_choices"][1]["kind"], "domain")
        self.assertEqual(u["key_choices"][3]["verdict"], "dark")

    def test_audit_has_the_address_and_no_key(self):
        r, raw = request("GET", "/api/users/anna/key?profile=awg2&format=all&endpoint=198.51.100.30")
        self.assertEqual(r.status, 200, raw)
        with open(os.path.join(TMP, "audit.log"), encoding="utf-8") as fh:
            recs = [json.loads(line) for line in fh.read().splitlines() if line.strip()]
        rec = [x for x in recs if x.get("action") == "user-key"][-1]
        self.assertEqual(rec["params"]["endpoint"], "198.51.100.30")
        text = json.dumps(recs, ensure_ascii=False)
        self.assertNotIn("FAKE-PRIVATE-KEY", text)
        self.assertNotIn("PrivateKey", text)


class BusyLockTests(unittest.TestCase):
    """Занятый замок отвечает сразу и по делу, а не молчит до таймаута.

    Разбор критика: пока идёт установка профиля (до 15 минут), второй
    администратор нажимал кнопку и ждал молча, пока не получал «сервер не
    отвечает». Здесь мост задержан файлом задержки, вторая мутация обязана
    вернуться с 409 lock_timeout за считанные секунды и назвать, что идёт.
    """

    def setUp(self):
        with open(SLEEP_FILE, "w", encoding="utf-8") as fh:
            fh.write("4\n")

    def tearDown(self):
        if os.path.exists(SLEEP_FILE):
            os.remove(SLEEP_FILE)

    def test_second_mutation_is_refused_at_once(self):
        import threading
        first = {}

        def slow():
            first["r"], first["raw"] = request("POST", "/api/action/user-rename",
                                               {"name": "anna", "label": "Первый"})

        worker = threading.Thread(target=slow)
        worker.start()
        try:
            time.sleep(1.0)  # даём первому взять замок
            started = time.time()
            r, raw = request("POST", "/api/action/user-rename", {"name": "bob", "label": "Второй"})
            waited = time.time() - started
        finally:
            worker.join(timeout=30)
        d = as_json(raw)
        self.assertEqual(r.status, 409, raw)
        self.assertEqual(d["code"], "lock_timeout")
        self.assertEqual(d["data"]["busy_action"], "user-rename")
        self.assertIn("user-rename", d["error"])
        # Ответ пришёл по таймауту замка (1 с), а не по таймауту моста (300 с).
        self.assertLess(waited, 5, "второй администратор снова ждёт молча")
        # Первый при этом отработал нормально.
        self.assertEqual(first["r"].status, 200, first.get("raw"))

    def test_reading_still_works_while_a_mutation_runs(self):
        import threading

        def slow():
            request("POST", "/api/action/user-rename", {"name": "anna", "label": "Первый"})

        worker = threading.Thread(target=slow)
        worker.start()
        try:
            time.sleep(1.0)
            r, raw = request("GET", "/api/session")
            self.assertEqual(r.status, 200, raw)
        finally:
            worker.join(timeout=30)


if __name__ == "__main__":
    unittest.main()
