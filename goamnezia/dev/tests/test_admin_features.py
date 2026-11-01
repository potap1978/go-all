# -*- coding: utf-8 -*-
"""Юнит-тесты веб-панели admin-web/server.py — без сети и без поднятого сервера.

Модуль импортируется по пути с окружением, указывающим во временные каталоги
(панель читает пути на уровне модуля), поэтому /opt не трогается даже при
запуске на сервере. Проверяются чистые функции: разбор ответа моста, argv и
валидация параметров по lib/actions.json, сессии и TTL с подменой часов
(server._now), корзины лимитов кода, подпись WebApp на известном векторе,
доверие петле по заголовкам, маскирование секретов, пометки пользователей.

Запуск: python3 -m unittest tests/test_admin_features.py
"""
import base64
import contextlib
import hashlib
import hmac
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(ROOT, "admin-web", "server.py")
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "fake_action_admin.sh")
ACTIONS_JSON = os.path.join(ROOT, "lib", "actions.json")

TMP_ROOT = tempfile.mkdtemp(prefix="goamnezia-admin-unit-")
os.environ["GOAMNEZIA_DIR"] = os.path.join(TMP_ROOT, "goamnezia")
os.environ["GOAMNEZIA_ADMIN_DIR"] = os.path.join(TMP_ROOT, "admin")
os.environ["GOAMNEZIA_BOT_DIR"] = os.path.join(TMP_ROOT, "bot")
os.environ["GOAMNEZIA_ACTION_BIN"] = FIXTURE
os.environ["GOAMNEZIA_ACTIONS_JSON"] = ACTIONS_JSON
os.environ["GOAMNEZIA_AUDIT_LOG"] = os.path.join(TMP_ROOT, "audit.log")
os.environ["GOAMNEZIA_ADMIN_PORT"] = "1985"
os.environ["GOAMNEZIA_SESSION_TTL"] = "900"
os.environ["GOAMNEZIA_SESSION_GRACE"] = "60"
os.environ["GOAMNEZIA_TRUST_TUNNEL"] = "1"
os.environ.pop("GOAMNEZIA_TFA_SINK", None)
os.environ.pop("GOAMNEZIA_ADMIN_TOKEN", None)
os.makedirs(os.environ["GOAMNEZIA_DIR"])
os.makedirs(os.environ["GOAMNEZIA_ADMIN_DIR"])
os.makedirs(os.environ["GOAMNEZIA_BOT_DIR"])

_spec = importlib.util.spec_from_file_location("ga_admin_server", SERVER)
server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(server)  # type: ignore[union-attr]

BOT_TOKEN = "123456789:AAFakeTokenForUnitTestsOnly_0123456789"
ADMIN_ID = "424242"


def tearDownModule():
    shutil.rmtree(TMP_ROOT, ignore_errors=True)


def read_text(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def write_env(text):
    # Путь — из уже импортированного модуля, а не из os.environ: при общем
    # discover соседний test_bot_features переставляет GOAMNEZIA_BOT_DIR под себя.
    with open(str(server.BOT_DIR / ".env"), "w", encoding="utf-8") as fh:
        fh.write(text)


class Clock:
    """Подменные часы: сессии и корзины считают время через server._now."""

    def __init__(self, start=1_000_000.0):
        self.t = start

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class ClockTestCase(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self._orig = server._now
        server._now = self.clock
        with server._SESSION_LOCK:
            server._SESSIONS.clear()
            server._TICKETS.clear()
        with server._TFA_LOCK:
            server._TFA_CODES.clear()
            server._TFA_REQUESTS.clear()

    def tearDown(self):
        server._now = self._orig


class BridgeParseTests(unittest.TestCase):
    def test_last_json_line_with_status_wins(self):
        out = ('noise\n{"note": "no status"}\n{"status":"error","code":"stale"}\n'
               '{"status":"success","message":"a"}\n')
        self.assertEqual(server.parse_bridge_output(out)["message"], "a")

    def test_trailing_garbage_is_skipped(self):
        out = '{"status":"success","x":1}\nnot json at all\n{broken'
        self.assertEqual(server.parse_bridge_output(out)["x"], 1)

    def test_no_json_gives_none(self):
        self.assertIsNone(server.parse_bridge_output("just text\n"))
        self.assertIsNone(server.parse_bridge_output(""))
        self.assertIsNone(server.parse_bridge_output('{"no_status": 1}'))

    def test_argv_dashes_and_empty_value(self):
        argv = server.build_argv("user-add", {"name": "anna", "quota_gb": "5", "label": ""})
        self.assertEqual(argv[0], FIXTURE)
        self.assertEqual(argv[1:3], ["--action=user-add", "--json"])
        self.assertIn("--quota-gb=5", argv)
        self.assertIn("--label=", argv)  # пустое передаётся, это законное значение


class ValidateParamsTests(unittest.TestCase):
    def test_required_missing(self):
        with self.assertRaises(server.ActionError) as ctx:
            server.validate_params("user-show", {})
        self.assertEqual(ctx.exception.code, "missing_arg")

    def test_unknown_action(self):
        with self.assertRaises(server.ActionError) as ctx:
            server.validate_params("rm-rf", {})
        self.assertEqual(ctx.exception.code, "unknown_action")
        self.assertEqual(ctx.exception.status, 404)

    def test_pattern_enum_and_range(self):
        for params in ({"name": "../x"}, {"name": "a" * 33}, {"name": "имя"}):
            with self.assertRaises(server.ActionError) as ctx:
                server.validate_params("user-show", params)
            self.assertEqual(ctx.exception.code, "bad_arg")
        with self.assertRaises(server.ActionError):
            server.validate_params("profile-restart", {"profile": "wg9"})
        with self.assertRaises(server.ActionError):
            server.validate_params("profile-mtu", {"profile": "awg2", "mtu": "9000"})
        with self.assertRaises(server.ActionError):
            server.validate_params("profile-mtu", {"profile": "awg2", "mtu": "12a"})
        out = server.validate_params("profile-mtu", {"profile": "awg2", "mtu": 1300})
        self.assertEqual(out, {"profile": "awg2", "mtu": "1300"})

    def test_mtu_below_1280_is_refused_in_words(self):
        # L-02б (живая проверка 08.10): ниже 1280 у клиентов с AllowedIPs ::/0 туннель не поднимается вовсе. Граница —
        # minimum 1280 в actions.json, отказ — её словами (below_minimum), а не общим «меньше 1280».
        for mtu in ("1279", "1200", 1100):
            with self.assertRaises(server.ActionError) as ctx:
                server.validate_params("profile-mtu", {"profile": "awg2", "mtu": mtu})
            self.assertEqual(ctx.exception.code, "bad_arg")
            self.assertIn("Ниже 1280 туннель не поднимется у клиентов с IPv6 (AllowedIPs ::/0)", str(ctx.exception))
        self.assertEqual(server.validate_params("profile-mtu", {"profile": "awg2", "mtu": "1280"})["mtu"], "1280")

    def test_empty_value_is_allowed_and_kept(self):
        # --value= у profile-set-param значит «убрать параметр» (CONTRACT §4.7).
        out = server.validate_params("profile-set-param", {"profile": "awg2", "key": "I1", "value": ""})
        self.assertEqual(out["value"], "")
        out = server.validate_params("endpoint-set", {"host": ""})
        self.assertEqual(out, {"host": ""})

    def test_empty_profile_list_is_refused_not_understood_as_all(self):
        """Снятые галочки — «никому», а не «всем» (DEFECTS §1.4).

        Пустой список профилей приходит ровно оттуда, где человек снял все
        галочки. Передать его дальше — молча завести ключи во всех линейках,
        которых он не просил, и он о них не узнает. Отказ на стороне панели
        стоит до моста: без ожидания замка и без запуска bash.
        """
        with self.assertRaises(server.ActionError) as ctx:
            server.validate_params("user-add", {"name": "anna", "profiles": ""})
        self.assertEqual(ctx.exception.code, "no_profiles_picked")
        self.assertEqual(ctx.exception.status, 400)
        self.assertTrue(str(ctx.exception).strip())
        # Пробелы вместо списка — тоже отказ (шаблон профилей их не пропускает).
        with self.assertRaises(server.ActionError):
            server.validate_params("user-add", {"name": "anna", "profiles": "   "})
        # Отсутствующий параметр — прежний смысл «во все установленные».
        self.assertEqual(server.validate_params("user-add", {"name": "anna"}),
                         {"name": "anna"})
        self.assertEqual(server.validate_params("user-add", {"name": "anna", "profiles": "awg2,awg3"})["profiles"],
                         "awg2,awg3")

    def test_refusal_of_empty_profiles_is_bilingual(self):
        for lang in ("ru", "en"):
            self.assertTrue(server.PANEL_STRINGS[lang]["no_profiles_picked"].strip(), lang)

    def test_extra_param_rejected_and_newline_rejected(self):
        with self.assertRaises(server.ActionError):
            server.validate_params("user-show", {"name": "anna", "evil": "1"})
        with self.assertRaises(server.ActionError):
            server.validate_params("user-rename", {"name": "anna", "label": "x\ny"})

    def test_confirm_required_only_for_destructive(self):
        self.assertEqual(server.confirm_required("user-del"), "name")
        self.assertEqual(server.confirm_required("profile-remove"), "profile")
        self.assertIsNone(server.confirm_required("user-add"))
        self.assertIsNone(server.confirm_required("status"))
        # Своё подтверждение (users-del-bulk: число людей) — параметр моста, сверяет его ядро
        self.assertEqual(server.confirm_required("users-del-bulk"), "confirm")
        self.assertTrue(server.action_own_confirm("users-del-bulk"))
        self.assertFalse(server.action_own_confirm("user-del"))
        self.assertIsNone(server.confirm_required("users-del-bulk-preview"))
        for code, status in (("invalid_selection", 400), ("confirm_mismatch", 409), ("nothing_selected", 409),
                             ("too_many", 409), ("user_del_failed", 409), ("interrupted", 409)):
            self.assertEqual(server.bridge_http_status({"status": "error", "code": code}), status, code)
        self.assertGreaterEqual(server.BRIDGE_TIMEOUT_BY_ACTION["users-del-bulk"], 900)

    def test_run_action_without_confirm_refused_before_bridge(self):
        with self.assertRaises(server.ActionError) as ctx:
            server.run_action("user-del", {"name": "anna"}, "test")
        self.assertEqual(ctx.exception.code, "confirm_required")
        with self.assertRaises(server.ActionError):
            server.run_action("user-del", {"name": "anna"}, "test", confirm="bob")

    def test_sprint4_codes_map_to_http(self):
        """unreachable — источники обновлений недоступны (503, повторить позже);
        конфликты состояния импорта и обновлений — 409; неизвестный тег/контейнер — 404."""
        for code, status in (("unreachable", 503), ("already_current", 409), ("no_previous", 409),
                             ("tag_not_found", 404), ("already_managed", 409), ("not_adoptable", 409),
                             ("container_not_found", 404), ("invalid_container", 400), ("container_missing", 409)):
            self.assertEqual(server.bridge_http_status({"status": "error", "code": code}), status, code)
        self.assertGreaterEqual(server.BRIDGE_TIMEOUT_BY_ACTION["upstream-rollback"], 600)
        self.assertGreaterEqual(server.BRIDGE_TIMEOUT_BY_ACTION["core-update"], 600)

    def test_all_actions_are_listed_for_frontend(self):
        with open(ACTIONS_JSON, encoding="utf-8") as fh:
            spec = json.load(fh)
        payload = server.actions_payload()
        self.assertEqual(set(payload["actions"]), set(spec["actions"]))
        self.assertIn("pattern", payload["param_types"]["name"])
        self.assertTrue(payload["actions"]["user-del"]["destructive"])
        self.assertTrue(payload["actions"]["user-key"]["secret"])
        self.assertEqual(server.param_enum("profile"), spec["param_types"]["profile"]["enum"])

    def test_param_keys_follow_line_rules(self):
        k15 = server.param_keys_for_line("1.5")
        k20 = server.param_keys_for_line("2.0")
        k31 = server.param_keys_for_line("3.1")
        self.assertIn("I1", k15)
        self.assertNotIn("S3", k15)
        self.assertIn("S3", k20)
        self.assertNotIn("HeaderProtectionKey", k20)
        self.assertIn("HeaderProtectionKey", k31)
        self.assertEqual(k31[-1], "MTU")


class SessionTests(ClockTestCase):
    def test_session_lifecycle_ok_renew_dead(self):
        tok = server.session_create("code")
        self.assertEqual(server.session_state(tok), ("ok", 900))
        self.assertEqual(server.session_who(tok), "code")
        self.clock.advance(899)
        self.assertEqual(server.session_state(tok)[0], "ok")
        self.clock.advance(1)
        st, left = server.session_state(tok)
        self.assertEqual(st, "renew")
        self.assertEqual(left, 60)
        self.assertTrue(server.session_renewable(tok))
        self.assertFalse(server.session_valid(tok))
        self.clock.advance(59)
        self.assertEqual(server.session_state(tok)[0], "renew")
        self.clock.advance(1)
        self.assertEqual(server.session_state(tok), ("dead", 0))
        # GC убрал запись только после окна продления
        with server._SESSION_LOCK:
            self.assertNotIn(tok, server._SESSIONS)

    def test_unknown_and_empty_tokens_are_dead(self):
        self.assertEqual(server.session_state(""), ("dead", 0))
        self.assertEqual(server.session_state("nope"), ("dead", 0))

    def test_session_drop(self):
        tok = server.session_create("tunnel")
        server.session_drop(tok)
        self.assertEqual(server.session_state(tok)[0], "dead")

    def test_ticket_is_single_use_and_short_lived(self):
        tk = server.ticket_create()
        self.assertTrue(server.ticket_redeem(tk))
        self.assertFalse(server.ticket_redeem(tk))  # второй раз — нет
        tk2 = server.ticket_create()
        self.clock.advance(server.TICKET_TTL + 1)
        self.assertFalse(server.ticket_redeem(tk2))
        self.assertFalse(server.ticket_redeem(""))


class TfaBucketTests(ClockTestCase):
    def setUp(self):
        super().setUp()
        write_env("BOT_TOKEN=%s\nALLOWED_IDS=%s\n" % (BOT_TOKEN, ADMIN_ID))
        self.sent = []
        self._orig_deliver = server._deliver_code
        server._deliver_code = lambda admins, text, code: (self.sent.append(code) or True, [])
        # Ответ «chat not found» называет бота (@имя из getMe) — без подмены это настоящий запрос к
        # api.telegram.org с подставным токеном: проверкам сеть не нужна, а без сети (CI, фильтр) он
        # ждал бы тайм-аута. Так же, как в AuthAnswerTests.
        self._orig_uname = server._bot_username
        server._bot_username = lambda: ""

    def tearDown(self):
        server._deliver_code = self._orig_deliver
        server._bot_username = self._orig_uname
        super().tearDown()

    def test_bucket_selection_and_limits(self):
        # Публичный запрос с известным адресом (X-Real-IP своего nginx) — своя корзина; без адреса —
        # запасная общая «net» со строгим лимитом.
        self.assertEqual(server._tfa_bucket("203.0.113.5", public=True), "net:203.0.113.5")
        self.assertEqual(server._tfa_bucket("127.0.0.1", public=True), "net")
        self.assertEqual(server._tfa_bucket("?", public=True), "net")
        # IPv6 — сетью /64 (одному клиенту её выдают целиком), IPv4 внутри IPv6 — как IPv4.
        self.assertEqual(server._tfa_bucket("2001:db8:1:2::5", public=True), "net:2001:db8:1:2::/64")
        self.assertEqual(server._tfa_bucket("2001:db8:1:2:ffff::9", public=True), "net:2001:db8:1:2::/64")
        self.assertEqual(server._tfa_bucket("::ffff:203.0.113.5", public=True), "net:203.0.113.5")
        self.assertEqual(server._tfa_bucket("203.0.113.5", public=False), "203.0.113.5")
        self.assertEqual(server._tfa_hour_limit("net"), server.TFA_REQUESTS_PER_HOUR)
        self.assertEqual(server._tfa_hour_limit("net:203.0.113.5"), server.TFA_REQUESTS_PER_HOUR)
        self.assertEqual(server._tfa_hour_limit("renew"), server.TFA_RENEW_PER_HOUR)
        self.assertEqual(server._tfa_hour_limit("127.0.0.1"), server.TFA_REQUESTS_PER_HOUR_SHARED)
        self.assertEqual(server._tfa_hour_limit("203.0.113.5"), server.TFA_REQUESTS_PER_HOUR)

    def test_crooked_code_spends_an_attempt_not_the_server(self):
        """Код с одиноким суррогатом (кривой JSON с улицы, продолжение UI-5): encode в _tfa_hash падал,
        и открытый маршрут /api/auth/verify отвечал 500. Теперь это неверный код и списанная попытка."""
        ok, _msg, _why = server.tfa_request("203.0.113.5", public=True)
        self.assertTrue(ok)
        ok, msg = server.tfa_verify("\ud800", "203.0.113.5", public=True)
        self.assertFalse(ok)
        self.assertIn(str(server.TFA_MAX_ATTEMPTS - 1), msg)
        # Хеш настоящего кода не изменился: он по-прежнему подходит.
        ok, _msg = server.tfa_verify(self.sent[-1], "203.0.113.5", public=True)
        self.assertTrue(ok)

    def test_public_bucket_five_per_hour_with_cooldown(self):
        for i in range(server.TFA_REQUESTS_PER_HOUR):
            ok, msg, _why = server.tfa_request("127.0.0.1", public=True)
            self.assertTrue(ok, msg)
            ok, msg, _why = server.tfa_request("127.0.0.1", public=True)
            self.assertFalse(ok)
            self.assertIn("повторите", msg)  # кулдаун 30 с
            self.clock.advance(server.TFA_REQUEST_COOLDOWN)
        ok, msg, _why = server.tfa_request("127.0.0.1", public=True)
        self.assertFalse(ok)
        self.assertIn("много", msg)
        # Публичная долбёжка не запирает туннель: другая корзина.
        ok, _msg, _why = server.tfa_request("127.0.0.1", public=False)
        self.assertTrue(ok)
        # …и продление тоже отдельно.
        self.clock.advance(server.TFA_REQUEST_COOLDOWN)
        ok, _msg, _why = server.tfa_request("127.0.0.1", public=True, renew=True)
        self.assertTrue(ok)
        # Через час квота публичной корзины возвращается.
        self.clock.advance(3600)
        ok, _msg, _why = server.tfa_request("127.0.0.1", public=True)
        self.assertTrue(ok)

    def test_verify_one_shot_attempts_and_expiry(self):
        ok, _msg, _why = server.tfa_request("127.0.0.1", public=True)
        self.assertTrue(ok)
        code = self.sent[-1]
        self.assertRegex(code, r"^[0-9]{6}$")
        for _ in range(server.TFA_MAX_ATTEMPTS - 1):
            ok, msg = server.tfa_verify("000000" if code != "000000" else "111111", "127.0.0.1", public=True)
            self.assertFalse(ok)
        ok, _ = server.tfa_verify(code, "127.0.0.1", public=True)
        self.assertTrue(ok)
        ok, msg = server.tfa_verify(code, "127.0.0.1", public=True)  # одноразовый
        self.assertFalse(ok)
        self.clock.advance(server.TFA_REQUEST_COOLDOWN)
        ok, _msg, _why = server.tfa_request("127.0.0.1", public=True)
        code = self.sent[-1]
        self.clock.advance(server.TFA_CODE_TTL + 1)
        ok, msg = server.tfa_verify(code, "127.0.0.1", public=True)
        self.assertFalse(ok)
        self.assertIn("истёк", msg)

    def test_too_many_attempts_burns_code(self):
        server.tfa_request("127.0.0.1", public=True)
        code = self.sent[-1]
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(server.TFA_MAX_ATTEMPTS):
            server.tfa_verify(wrong, "127.0.0.1", public=True)
        ok, msg = server.tfa_verify(code, "127.0.0.1", public=True)
        self.assertFalse(ok)

    # ── SEC-P2 (аудит 25.09.2026): корзина публичного входа — на адрес, а не одна на весь интернет ──
    ADMIN_IP = "203.0.113.7"
    STRANGER_IP = "198.51.100.66"

    def _wrong(self, code):
        return "000000" if code != "000000" else "111111"

    def test_stranger_cannot_lock_the_admin_out(self):
        """Посторонний, знающий домен, выжигает только свою корзину: ни квота, ни кулдаун, ни код
        администратора от его запросов и догадок не страдают."""
        ok, _m, _w = server.tfa_request(self.ADMIN_IP, public=True)
        self.assertTrue(ok)
        admin_code = self.sent[-1]
        # Чужой запрос не затирает код администратора, а чужие догадки его не выжигают.
        for _ in range(server.TFA_REQUESTS_PER_HOUR):
            ok, msg, _w = server.tfa_request(self.STRANGER_IP, public=True)
            self.assertTrue(ok, msg)
            for _ in range(server.TFA_MAX_ATTEMPTS + 1):
                self.assertFalse(server.tfa_verify(self._wrong(admin_code), self.STRANGER_IP, public=True)[0])
            self.clock.advance(server.TFA_REQUEST_COOLDOWN)
        ok, msg, why = server.tfa_request(self.STRANGER_IP, public=True)
        self.assertEqual((ok, why), (False, "too_often"))
        self.assertIn("через час", msg)
        # Чужой адрес не проверяет и чужой код: код администратора годится только с его адреса.
        self.assertFalse(server.tfa_verify(admin_code, self.STRANGER_IP, public=True)[0])
        self.assertTrue(server.tfa_verify(admin_code, self.ADMIN_IP, public=True)[0])
        # Квота и кулдаун администратора — свои: новый код сразу.
        ok, msg, _w = server.tfa_request(self.ADMIN_IP, public=True)
        self.assertTrue(ok, msg)

    def test_external_ceiling_bounds_messages_and_memory(self):
        """Страховка на все внешние корзины: сто адресов не дают пятьсот сообщений в час, а память
        держит только последний час. Туннель и продление под неё не попадают."""
        soft, cap = server.TFA_REQUESTS_PER_HOUR_EXTERNAL, server.TFA_REQUESTS_PER_HOUR_EXTERNAL_MAX
        self.assertLess(soft, cap)
        self.assertLessEqual(cap, 60, "каждый код — сообщение админу и пять догадок: страховка не шире 60 в час")
        # Каждый адрес здесь — впервые за час: первый код часа выдаётся и сверх общего счёта, но не сверх страховки.
        for i in range(cap):
            ok, msg, _w = server.tfa_request("198.51.100.%d" % (i + 1), public=True)
            self.assertTrue(ok, msg)
        ok, msg, why = server.tfa_request("198.51.100.200", public=True)
        self.assertEqual((ok, why), (False, "too_often"))
        self.assertEqual(msg.extra.get("reason"), "net_limit")
        self.assertIn("паролю", msg)      # говорит, чем войти сейчас
        with server._TFA_LOCK:
            self.assertLessEqual(len(server._TFA_REQUESTS), cap)
            self.assertLessEqual(len(server._TFA_CODES), cap)
        # Потолок — про интернет: свой туннель и продление живой сессии работают.
        self.assertTrue(server.tfa_request("127.0.0.1", public=False)[0])
        self.assertTrue(server.tfa_request("127.0.0.1", public=True, renew=True, session_token="tok")[0])
        # Через час — снова можно, а корзины прошлого часа выметены.
        self.clock.advance(3600)
        self.assertTrue(server.tfa_request("198.51.100.201", public=True)[0])
        with server._TFA_LOCK:
            self.assertEqual(set(server._TFA_REQUESTS), {"net:198.51.100.201"})
            self.assertEqual(set(server._TFA_CODES), {"net:198.51.100.201"})

    def test_four_busy_addresses_do_not_lock_out_a_new_one(self):
        """SEC46-06: четыре адреса по пять кодов выбирали общий потолок 20 — и вход кодом вставал у всех на час.
        Теперь сверх общего счёта новый адрес (администратор) свой первый код часа получает и входит; повторный
        код сверх счёта — отказ net_limit (своя квота при этом не выбрана), а через час — снова как обычно."""
        per_ip, soft = server.TFA_REQUESTS_PER_HOUR, server.TFA_REQUESTS_PER_HOUR_EXTERNAL
        busy = ["198.51.100.%d" % (i + 1) for i in range(soft // per_ip)]
        for _ in range(per_ip):
            for ip in busy:
                ok, msg, _w = server.tfa_request(ip, public=True)
                self.assertTrue(ok, msg)
            self.clock.advance(server.TFA_REQUEST_COOLDOWN)
        # Общий счёт выбран. Администратор с нового адреса: код приходит и принимается.
        ok, msg, _w = server.tfa_request(self.ADMIN_IP, public=True)
        self.assertTrue(ok, msg)
        self.assertTrue(server.tfa_verify(self.sent[-1], self.ADMIN_IP, public=True)[0])
        # Второй код ему же сверх общего счёта — нет: net_limit, «чем войти сейчас», а не hour_limit.
        self.clock.advance(server.TFA_REQUEST_COOLDOWN)
        ok, msg, why = server.tfa_request(self.ADMIN_IP, public=True)
        self.assertEqual((ok, why, msg.extra.get("reason")), (False, "too_often", "net_limit"))
        # Порядок проверок прежний: сперва кулдаун и квота своей корзины, потом общий счёт.
        ok, msg, why = server.tfa_request(busy[0], public=True)
        self.assertEqual((ok, why, msg.extra.get("reason")), (False, "too_often", "hour_limit"))
        ok, msg, why = server.tfa_request("203.0.113.99", public=True)
        self.assertTrue(ok, msg)
        ok, msg, why = server.tfa_request("203.0.113.99", public=True)
        self.assertEqual((ok, why, msg.extra.get("reason")), (False, "too_often", "cooldown"))
        # Через час счёт снова общий и обычный: повторный код адресу — как прежде.
        self.clock.advance(3600)
        self.assertTrue(server.tfa_request(self.ADMIN_IP, public=True)[0])
        self.clock.advance(server.TFA_REQUEST_COOLDOWN)
        self.assertTrue(server.tfa_request(self.ADMIN_IP, public=True)[0])

    def test_ipv6_neighbours_share_one_bucket(self):
        self.assertTrue(server.tfa_request("2001:db8:1:2::5", public=True)[0])
        ok, msg, why = server.tfa_request("2001:db8:1:2::6", public=True)
        self.assertEqual((ok, why), (False, "too_often"))
        self.assertIn("повторите", msg)
        self.assertTrue(server.tfa_request("2001:db8:1:3::6", public=True)[0])   # соседняя /64 — своя

    def test_renew_code_belongs_to_the_session_that_asked(self):
        """Код продления принимается только из той вкладки, что его просила, — и после окна
        продления тоже (код живёт 5 минут), а без cookie или с чужой не принимается."""
        self.assertTrue(server.tfa_request(self.ADMIN_IP, public=True, renew=True, session_token="tok-A")[0])
        code = self.sent[-1]
        self.assertFalse(server.tfa_verify(code, self.ADMIN_IP, public=True)[0])
        self.assertFalse(server.tfa_verify(code, self.ADMIN_IP, public=True, session_token="tok-B")[0])
        self.assertTrue(server.tfa_verify(code, self.ADMIN_IP, public=True, session_token="tok-A")[0])

    def test_no_admins_no_code(self):
        write_env("BOT_TOKEN=%s\nALLOWED_IDS=\n" % BOT_TOKEN)
        ok, msg, why = server.tfa_request("127.0.0.1", public=True)
        self.assertFalse(ok)
        self.assertIn("SSH", msg)
        # Причина названа кодом, и 429 «слишком часто» тут ни при чём.
        self.assertEqual(why, "tfa_off")
        self.assertEqual(server.TFA_HTTP_STATUS[why], 503)
        self.assertFalse(server.tfa_available())
        write_env("export BOT_TOKEN='%s'\n  ALLOWED_IDS = %s , 7\n" % (BOT_TOKEN, ADMIN_ID))
        self.assertTrue(server.tfa_available())
        self.assertEqual(server._bot_admin_ids(), {ADMIN_ID, "7"})

    def test_failed_delivery_refunds_quota(self):
        server._deliver_code = lambda admins, text, code: (False, ["chat not found"])
        ok, msg, why = server.tfa_request("127.0.0.1", public=True)
        self.assertFalse(ok)
        self.assertIn("Start", msg)
        # «Администратор не нажимал Start» — состояние, а не перебор: 409.
        self.assertEqual(why, "chat_not_started")
        self.assertEqual(server.TFA_HTTP_STATUS[why], 409)
        with server._TFA_LOCK:
            self.assertEqual(server._TFA_REQUESTS.get("net"), [])
            self.assertNotIn("net", server._TFA_CODES)


class TfaSinkTests(ClockTestCase):
    def test_sink_writes_code_to_file(self):
        write_env("BOT_TOKEN=%s\nALLOWED_IDS=%s\n" % (BOT_TOKEN, ADMIN_ID))
        sink = os.path.join(TMP_ROOT, "sink.txt")
        orig = server.TFA_SINK
        server.TFA_SINK = sink
        try:
            ok, _msg, _why = server.tfa_request("127.0.0.1", public=False)
            self.assertTrue(ok)
            with open(sink, encoding="utf-8") as fh:
                code = fh.read().strip().splitlines()[-1]
            self.assertRegex(code, r"^[0-9]{6}$")
            self.assertTrue(server.tfa_verify(code, "127.0.0.1")[0])
        finally:
            server.TFA_SINK = orig


class BotTokenLeakTests(ClockTestCase):
    """SEC46-03: токен бота не уходит ни в ответ открытого запроса кода, ни в журнал.

    Комментарий после токена в .env бот (python-dotenv) отбрасывает, а панель брала строку целиком:
    адрес https://api.telegram.org/bot<токен   # …>/sendMessage ломался, и текст InvalidURL с ПОЛНЫМ
    токеном уходил неавторизованному, запросившему код входа."""

    SECRET_PART = BOT_TOKEN.split(":", 1)[1]

    def setUp(self):
        super().setUp()
        self._orig_urlopen = server.urllib.request.urlopen
        self._orig_uname = server._bot_username
        server._bot_username = lambda: ""
        self.urls = []

    def tearDown(self):
        server.urllib.request.urlopen = self._orig_urlopen
        server._bot_username = self._orig_uname
        super().tearDown()

    def test_env_values_are_read_like_dotenv(self):
        for line in ("BOT_TOKEN=%s   # от BotFather" % BOT_TOKEN,
                     'BOT_TOKEN="%s"  # от BotFather' % BOT_TOKEN,
                     "export BOT_TOKEN='%s'\t# c" % BOT_TOKEN,
                     "  BOT_TOKEN = %s" % BOT_TOKEN):
            write_env(line + "\nALLOWED_IDS=%s , 7   # админы\nOWNER_ID=7 # владелец\n" % ADMIN_ID)
            self.assertEqual(server._bot_token_from_env(), BOT_TOKEN, line)
            self.assertEqual(server._bot_admin_ids(), {ADMIN_ID, "7"}, line)
            self.assertEqual(server.bot_admins_view()["owner"], "7", line)
            self.assertTrue(server.tfa_available(), line)

    def test_a_repeated_key_takes_the_last_line(self):
        """SEC50-03: повтор переменной в .env — побеждает последняя строка, как у бота и systemd. Панель брала
        первую: при двух строках ALLOWED_IDS код входа уходил не тем, с кем работает бот."""
        for text, ids, owner in (
                ("ALLOWED_IDS=111\nALLOWED_IDS=%s,7\nOWNER_ID=111\nOWNER_ID=7\n" % ADMIN_ID, {ADMIN_ID, "7"}, "7"),
                ("ALLOWED_IDS=111\nexport ALLOWED_IDS=\"%s\" # c\n" % ADMIN_ID, {ADMIN_ID}, ADMIN_ID),
                ("  ALLOWED_IDS = %s\n# ALLOWED_IDS=999\n" % ADMIN_ID, {ADMIN_ID}, ADMIN_ID),
                ("ALLOWED_IDS=%s\nALLOWED_IDS=\n" % ADMIN_ID, set(), "")):
            write_env("BOT_TOKEN=bad\nBOT_TOKEN=%s\n" % BOT_TOKEN + text)
            self.assertEqual(server._bot_token_from_env(), BOT_TOKEN, text)
            self.assertEqual(server._bot_admin_ids(), ids, text)
            self.assertEqual(server.bot_admins_view()["owner"], owner, text)

    def test_malformed_token_counts_as_absent(self):
        # Не той формы, что проверяет установщик (TOKEN_RE), — в адрес запроса не идёт вовсе.
        for bad in (BOT_TOKEN + "#x", BOT_TOKEN + " extra", "not-a-token", "123:short", '"' + BOT_TOKEN):
            write_env("BOT_TOKEN=%s\nALLOWED_IDS=%s\n" % (bad, ADMIN_ID))
            self.assertEqual(server._bot_token_from_env(), "", bad)
            self.assertFalse(server.tfa_available(), bad)
            ok, msg, why = server.tfa_request("127.0.0.1", public=True)
            self.assertEqual((ok, why), (False, "tfa_off"), bad)

    def test_send_failure_never_carries_the_token(self):
        write_env("BOT_TOKEN=%s   # от BotFather\nALLOWED_IDS=%s\n" % (BOT_TOKEN, ADMIN_ID))

        def urlopen(req, *args, **kwargs):
            url = getattr(req, "full_url", str(req))
            self.urls.append(url)
            # Ровно так urllib описывает негодный адрес: путь запроса целиком, с токеном.
            raise ValueError("URL can't contain control characters. %r (found at least ' ')"
                             % url.split("api.telegram.org", 1)[-1])
        server.urllib.request.urlopen = urlopen
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            ok, msg, why = server.tfa_request("203.0.113.50", public=True)
        self.assertEqual((ok, why), (False, "send_failed"))
        # Комментарий в адрес больше не попадает: токен в URL — ровно тот, что выдал BotFather.
        self.assertTrue(self.urls)
        self.assertTrue(all(u.endswith("/bot%s/sendMessage" % BOT_TOKEN) for u in self.urls), self.urls)
        for text in (msg.texts["ru"], msg.texts["en"], str(msg), err.getvalue()):
            self.assertNotIn(self.SECRET_PART, text)
        self.assertIn("<token>", msg.texts["ru"])
        self.assertIn("<token>", err.getvalue())

    def test_telegram_description_and_reasons_are_redacted(self):
        write_env("BOT_TOKEN=%s\nALLOWED_IDS=%s\n" % (BOT_TOKEN, ADMIN_ID))
        # Причина от кого угодно (сток, будущий код) с токеном внутри — всё равно без токена.
        orig_deliver = server._deliver_code
        server._deliver_code = lambda admins, text, code: (False, ["oops /bot%s/getMe" % BOT_TOKEN])
        try:
            ok, msg, why = server.tfa_request("127.0.0.1", public=False)
        finally:
            server._deliver_code = orig_deliver
        self.assertEqual(why, "send_failed")
        for lang in ("ru", "en"):
            self.assertNotIn(self.SECRET_PART, msg.texts[lang])
        self.assertEqual(server._redact_token("x 987654321:%s y" % ("Z" * 35)), "x <token> y")


class AuthAnswerTests(ClockTestCase):
    """Ответы входа с машинным кодом и текстом на обоих языках (решение владельца В4, 26.09.2026).

    У каждого отказа маршрутов входа, кода, пароля и стражи сессии — `code`, `texts{ru, en}` (не копия
    друг друга) и `error` на языке панели, как прежде. Логика, ограничители и статусы HTTP — прежние:
    это проверяют соседние наборы (TfaBucketTests, PasswordStoreTests, HTTP-тесты входа).
    """

    KEYS = ("code_off_no_admins", "code_off_no_token", "code_cooldown", "code_hour_limit", "code_net_limit",
            "code_chat_not_started", "your_bot", "code_send_failed", "code_send_failed_why", "code_expired",
            "code_too_many", "code_wrong", "sig_invalid", "sig_expired", "sig_not_admin", "sig_no_admins",
            "sig_no_token", "pwd_blocked", "pwd_none", "pwd_wrong", "pwd_bad_current",
            "pwd_short", "pwd_bad_chars", "pwd_write_failed", "sess_renew", "sess_expired", "auth_required")

    def setUp(self):
        super().setUp()
        write_env("BOT_TOKEN=%s\nALLOWED_IDS=%s\n" % (BOT_TOKEN, ADMIN_ID))
        self.sent = []
        self._orig_deliver = server._deliver_code
        self._orig_uname = server._bot_username
        server._deliver_code = lambda admins, text, code: (self.sent.append(code) or True, [])
        server._bot_username = lambda: ""
        with server._PWD_LOCK:
            server._PWD_ATTEMPTS.clear()
        self.pwd_file = str(server.PASSWORD_FILE)
        if os.path.exists(self.pwd_file):
            os.remove(self.pwd_file)

    def tearDown(self):
        server._deliver_code = self._orig_deliver
        server._bot_username = self._orig_uname
        if os.path.exists(self.pwd_file):
            os.remove(self.pwd_file)
        super().tearDown()

    def assertAuthText(self, msg, code, **extra):
        """AuthText: код, оба языка (разные, без незаполненных {…}), строка — на языке панели (здесь ru)."""
        self.assertIsInstance(msg, server.AuthText, msg)
        self.assertEqual(msg.code, code)
        self.assertEqual(set(msg.texts), {"ru", "en"})
        self.assertTrue(msg.texts["ru"] and msg.texts["en"])
        self.assertNotEqual(msg.texts["ru"], msg.texts["en"])
        for lang in ("ru", "en"):
            self.assertNotRegex(msg.texts[lang], r"\{[a-z]+\}", lang)
        self.assertEqual(str(msg), msg.texts["ru"])
        for k, v in extra.items():
            self.assertEqual(msg.extra.get(k), v, k)

    def assertAuthPayload(self, payload, code, **extra):
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["code"], code)
        self.assertEqual(set(payload["texts"]), {"ru", "en"})
        self.assertTrue(payload["texts"]["ru"] and payload["texts"]["en"])
        self.assertNotEqual(payload["texts"]["ru"], payload["texts"]["en"])
        self.assertEqual(payload["error"], payload["texts"]["ru"])
        for k, v in extra.items():
            self.assertEqual(payload.get(k), v, k)

    def test_texts_are_paired_and_translated(self):
        for key in self.KEYS:
            ru, en = server.LOGIN_STRINGS["ru"][key], server.LOGIN_STRINGS["en"][key]
            self.assertTrue(ru and en, key)
            self.assertNotEqual(ru, en, key)
            self.assertEqual(re.findall(r"\{[a-z]+\}", ru), re.findall(r"\{[a-z]+\}", en), key)

    def test_code_request_refusals(self):
        write_env("BOT_TOKEN=%s\nALLOWED_IDS=\n" % BOT_TOKEN)
        ok, msg, why = server.tfa_request("127.0.0.1", public=True)
        self.assertEqual((ok, why), (False, "tfa_off"))
        self.assertAuthText(msg, "tfa_off", reason="no_admins")
        write_env("ALLOWED_IDS=%s\n" % ADMIN_ID)
        ok, msg, why = server.tfa_request("127.0.0.1", public=True)
        self.assertAuthText(msg, "tfa_off", reason="no_token")
        write_env("BOT_TOKEN=%s\nALLOWED_IDS=%s\n" % (BOT_TOKEN, ADMIN_ID))
        # Кулдаун: секунды — числом и в обоих текстах.
        self.assertTrue(server.tfa_request("203.0.113.40", public=True)[0])
        self.clock.advance(10)
        ok, msg, why = server.tfa_request("203.0.113.40", public=True)
        self.assertEqual(why, "too_often")
        self.assertAuthText(msg, "too_often", reason="cooldown", seconds=server.TFA_REQUEST_COOLDOWN - 10)
        for lang in ("ru", "en"):
            self.assertIn(str(server.TFA_REQUEST_COOLDOWN - 10), msg.texts[lang])
        # Часовой предел своей корзины.
        for _ in range(server.TFA_REQUESTS_PER_HOUR - 1):
            self.clock.advance(server.TFA_REQUEST_COOLDOWN)
            self.assertTrue(server.tfa_request("203.0.113.40", public=True)[0])
        self.clock.advance(server.TFA_REQUEST_COOLDOWN)
        ok, msg, why = server.tfa_request("203.0.113.40", public=True)
        self.assertAuthText(msg, "too_often", reason="hour_limit")
        # Общий счёт всех внешних корзин (SEC46-06): сверх него повторный код адресу — net_limit…
        for i in range(server.TFA_REQUESTS_PER_HOUR_EXTERNAL):
            server.tfa_request("198.51.100.%d" % (i + 1), public=True)
        self.clock.advance(server.TFA_REQUEST_COOLDOWN)
        ok, msg, why = server.tfa_request("198.51.100.1", public=True)
        self.assertAuthText(msg, "too_often", reason="net_limit")
        # …а сверх страховки — и первый код часа.
        for i in range(server.TFA_REQUESTS_PER_HOUR_EXTERNAL_MAX):
            server.tfa_request("192.0.2.%d" % (i + 1), public=True)
        ok, msg, why = server.tfa_request("198.51.100.250", public=True)
        self.assertAuthText(msg, "too_often", reason="net_limit")

    def test_code_delivery_refusals(self):
        server._deliver_code = lambda admins, text, code: (False, ["Bad Request: chat not found"])
        ok, msg, why = server.tfa_request("127.0.0.1", public=False)
        self.assertEqual(why, "chat_not_started")
        self.assertAuthText(msg, "chat_not_started")
        self.assertIn(server.LOGIN_STRINGS["en"]["your_bot"], msg.texts["en"])     # без имени бота — «your bot»
        server._bot_username = lambda: "goamnezia_test_bot"
        ok, msg, why = server.tfa_request("127.0.0.1", public=False)
        for lang in ("ru", "en"):
            self.assertIn("@goamnezia_test_bot", msg.texts[lang])
        server._deliver_code = lambda admins, text, code: (False, ["Forbidden: something else"])
        ok, msg, why = server.tfa_request("127.0.0.1", public=False)
        self.assertEqual(why, "send_failed")
        self.assertAuthText(msg, "send_failed")
        for lang in ("ru", "en"):
            self.assertIn("Forbidden: something else", msg.texts[lang])       # слова Telegram — как есть
        server._deliver_code = lambda admins, text, code: (False, [])
        ok, msg, why = server.tfa_request("127.0.0.1", public=False)
        self.assertAuthText(msg, "send_failed")

    def test_code_verify_refusals(self):
        ok, msg = server.tfa_verify("123456", "203.0.113.41", public=True)
        self.assertFalse(ok)
        self.assertAuthText(msg, "code_expired")
        self.assertTrue(server.tfa_request("203.0.113.41", public=True)[0])
        wrong = "000000" if self.sent[-1] != "000000" else "111111"
        ok, msg = server.tfa_verify(wrong, "203.0.113.41", public=True)
        self.assertAuthText(msg, "code_wrong", left=server.TFA_MAX_ATTEMPTS - 1)
        for _ in range(server.TFA_MAX_ATTEMPTS - 1):
            server.tfa_verify(wrong, "203.0.113.41", public=True)
        ok, msg = server.tfa_verify(self.sent[-1], "203.0.113.41", public=True)
        self.assertAuthText(msg, "code_too_many_attempts")

    def test_error_text_follows_the_panel_language(self):
        saved = server.read_language
        server.read_language = lambda: "en"
        try:
            ok, msg = server.tfa_verify("123456", "203.0.113.42", public=True)
            self.assertEqual(str(msg), msg.texts["en"])
        finally:
            server.read_language = saved

    def _handler(self, method, path="/", body=None, headers=None, ip="203.0.113.43"):
        raw = json.dumps(body or {}).encode("utf-8")
        h = server.AdminHandler.__new__(server.AdminHandler)
        h.command = method
        h.headers = dict({"Host": "127.0.0.1:1985", "X-GoAmnezia-Admin": "1", "Content-Length": str(len(raw)),
                          "X-Forwarded-Host": "example.test"}, **(headers or {}))
        h.rfile = io.BytesIO(raw)
        h.client_address = (ip, 40000)
        h._who = "tester"
        h._issue_session = None
        h.sent = []
        h.send_json = lambda payload, status=200, extra_headers=None: h.sent.append((status, payload))
        h.send_login_page = lambda: h.sent.append((200, {"login_page": True}))
        return h

    def test_password_route_refusals(self):
        with contextlib.redirect_stderr(io.StringIO()):
            h = self._handler("POST", body={"login": "admin", "password": "x"})
            server.AdminHandler.route_password_login(h)
            self.assertEqual(h.sent[0][0], 409)
            self.assertAuthPayload(h.sent[0][1], "password_unset")
            server.password_set("admin", "right-Sample-Pass-1")
            h = self._handler("POST", body={"login": "admin", "password": "wrong-Sample-Pass-1"})
            server.AdminHandler.route_password_login(h)
            self.assertEqual(h.sent[0][0], 401)
            self.assertAuthPayload(h.sent[0][1], "bad_credentials")
            for _ in range(server.PWD_MAX_ATTEMPTS):
                server.password_attempt_failed(server._pwd_bucket("203.0.113.44"))
            h = self._handler("POST", body={"login": "admin", "password": "x"}, ip="203.0.113.44")
            server.AdminHandler.route_password_login(h)
            self.assertEqual(h.sent[0][0], 429)
            self.assertAuthPayload(h.sent[0][1], "too_many_attempts")
            self.assertGreater(h.sent[0][1]["seconds"], 0)
            # Смена пароля: неверный текущий, короткий новый, новый с одиноким суррогатом, запись не удалась.
            h = self._handler("POST", body={"current": "nope-Sample-Pass-9", "new": "brand-New-Sample-Pass-2"})
            server.AdminHandler.route_password_change(h)
            self.assertEqual(h.sent[0][0], 401)
            self.assertAuthPayload(h.sent[0][1], "bad_credentials")
            self.assertNotEqual(h.sent[0][1]["texts"]["ru"], server.LOGIN_STRINGS["ru"]["pwd_wrong"])
            h = self._handler("POST", body={"current": "right-Sample-Pass-1", "new": "short"})
            server.AdminHandler.route_password_change(h)
            self.assertEqual(h.sent[0][0], 400)
            self.assertAuthPayload(h.sent[0][1], "bad_password")
            h = self._handler("POST", body={"current": "right-Sample-Pass-1", "new": "\ud800" * 13})
            server.AdminHandler.route_password_change(h)
            self.assertEqual(h.sent[0][0], 400)
            self.assertAuthPayload(h.sent[0][1], "bad_password")
            self.assertEqual(h.sent[0][1]["texts"]["en"], server.LOGIN_STRINGS["en"]["pwd_bad_chars"])
            real_fsync = os.fsync

            def boom(fd):
                raise OSError(28, "no space left on device")
            os.fsync = boom
            try:
                h = self._handler("POST", body={"current": "right-Sample-Pass-1", "new": "brand-New-Sample-Pass-2"})
                server.AdminHandler.route_password_change(h)
            finally:
                os.fsync = real_fsync
            self.assertEqual(h.sent[0][0], 500)
            self.assertAuthPayload(h.sent[0][1], "password_write_failed")
            self.assertIn("no space left", h.sent[0][1]["texts"]["en"])
            tmp = self.pwd_file + ".tmp"
            if os.path.exists(tmp):
                os.remove(tmp)
        self.assertTrue(server.password_verify("admin", "right-Sample-Pass-1"), "пароль не сменился")

    def _guard(self, cookie=""):
        h = self._handler("GET", headers={"Cookie": "ga_session=%s" % cookie} if cookie else None, ip="127.0.0.1")
        self.assertFalse(server.AdminHandler.guard_request(h, urllib.parse.urlparse("/api/users")))
        return h.sent[0]

    def test_guard_refusals(self):
        status, payload = self._guard()
        self.assertEqual((status, payload["state"]), (401, "none"))
        self.assertAuthPayload(payload, "auth_required")
        status, payload = self._guard("no-such-session")
        self.assertEqual((status, payload["state"]), (401, "expired"))
        self.assertAuthPayload(payload, "session_expired")
        tok = server.session_create("code")
        self.clock.advance(server.SESSION_TTL + 1)
        status, payload = self._guard(tok)
        self.assertEqual((status, payload["state"]), (401, "renew"))
        self.assertAuthPayload(payload, "session_renew")
        self.assertGreater(payload["seconds"], 0)

    def test_miniapp_and_verify_routes(self):
        h = self._handler("POST", body={"init_data": "a=1&hash=" + "0" * 64})
        server.AdminHandler.route_post_api(h, urllib.parse.urlparse("/api/auth/miniapp"))
        self.assertEqual(h.sent[0][0], 401)
        self.assertAuthPayload(h.sent[0][1], "signature_invalid")
        with contextlib.redirect_stderr(io.StringIO()):
            h = self._handler("POST", body={"code": "123456"}, headers={"X-Real-IP": "203.0.113.45"}, ip="127.0.0.1")
            server.AdminHandler.route_post_api(h, urllib.parse.urlparse("/api/auth/verify"))
        self.assertEqual(h.sent[0][0], 401)
        self.assertAuthPayload(h.sent[0][1], "code_expired")
        # /api/auth/request: прежний code, статус по TFA_HTTP_STATUS и тексты рядом.
        write_env("BOT_TOKEN=%s\nALLOWED_IDS=\n" % BOT_TOKEN)
        h = self._handler("POST", headers={"X-Real-IP": "203.0.113.46"}, ip="127.0.0.1")
        server.AdminHandler.route_post_api(h, urllib.parse.urlparse("/api/auth/request"))
        self.assertEqual(h.sent[0][0], server.TFA_HTTP_STATUS["tfa_off"])
        self.assertAuthPayload(h.sent[0][1], "tfa_off", reason="no_admins")

    def _miniapp(self, init_data):
        h = self._handler("POST", body={"init_data": init_data})
        with contextlib.redirect_stderr(io.StringIO()):
            server.AdminHandler.route_post_api(h, urllib.parse.urlparse("/api/auth/miniapp"))
        return h.sent[0]

    def test_miniapp_refusals_say_why(self):
        """L-03: отказ входа из мини-аппа различает причину машинным кодом и словами на обоих языках, статус тот
        же 401; подпись не подошла — прежний signature_invalid. Логика входа не меняется: админ входит."""
        user = json.dumps({"id": int(ADMIN_ID), "first_name": "Admin"}, separators=(",", ":"))
        good = sign_init_data(BOT_TOKEN, {"auth_date": str(int(self.clock())), "user": user})
        write_env("ALLOWED_IDS=%s\n" % ADMIN_ID)                                  # токена нет
        status, payload = self._miniapp(good)
        self.assertEqual(status, 401)
        self.assertAuthPayload(payload, "miniapp_not_configured", reason="no_token")
        write_env("BOT_TOKEN=%s\nALLOWED_IDS=\n" % BOT_TOKEN)                     # админов нет
        self.assertAuthPayload(self._miniapp(good)[1], "miniapp_not_configured", reason="no_admins")
        write_env("BOT_TOKEN=%s\nALLOWED_IDS=515151\n" % BOT_TOKEN)               # человек — не из админов
        self.assertAuthPayload(self._miniapp(good)[1], "miniapp_not_admin", reason="not_admin")
        write_env("BOT_TOKEN=%s\nALLOWED_IDS=%s\n" % (BOT_TOKEN, ADMIN_ID))
        self.assertAuthPayload(self._miniapp(good.replace("Admin", "Evil"))[1], "signature_invalid",
                               reason="bad_signature")
        self.clock.advance(server.MINIAPP_MAX_AGE_SECONDS + 5)
        self.assertAuthPayload(self._miniapp(good)[1], "miniapp_expired", reason="expired")
        fresh = sign_init_data(BOT_TOKEN, {"auth_date": str(int(self.clock())), "user": user})
        self.assertEqual(server.verify_telegram_init_data(fresh), ADMIN_ID, "админ со свежей подписью входит, как раньше")


def sign_init_data(token, fields):
    check = "\n".join("%s=%s" % (k, v) for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields = dict(fields)
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(fields)


class WebAppSignatureTests(ClockTestCase):
    def test_known_vector_accepts_admin_and_rejects_others(self):
        user = json.dumps({"id": int(ADMIN_ID), "first_name": "Admin"}, separators=(",", ":"))
        fields = {"auth_date": str(int(self.clock())), "query_id": "AAH", "user": user}
        init_data = sign_init_data(BOT_TOKEN, fields)
        # Известный вектор: подпись, посчитанная по алгоритму Telegram (HMAC "WebAppData").
        expected_hash = urllib.parse.parse_qs(init_data)["hash"][0]
        self.assertEqual(len(expected_hash), 64)
        self.assertEqual(server.verify_telegram_init_data(init_data, token=BOT_TOKEN, admins={ADMIN_ID}), ADMIN_ID)
        # не админ
        self.assertIsNone(server.verify_telegram_init_data(init_data, token=BOT_TOKEN, admins={"1"}))
        # нет админов вовсе — не пускаем никого
        self.assertIsNone(server.verify_telegram_init_data(init_data, token=BOT_TOKEN, admins=set()))
        # чужой токен
        self.assertIsNone(server.verify_telegram_init_data(init_data, token=BOT_TOKEN + "x", admins={ADMIN_ID}))
        # подделанное поле
        tampered = init_data.replace("Admin", "Evil")
        self.assertIsNone(server.verify_telegram_init_data(tampered, token=BOT_TOKEN, admins={ADMIN_ID}))
        # протухшая подпись
        self.clock.advance(server.MINIAPP_MAX_AGE_SECONDS + 5)
        self.assertIsNone(server.verify_telegram_init_data(init_data, token=BOT_TOKEN, admins={ADMIN_ID}))

    def test_check_names_the_reason(self):
        """telegram_init_data_check — та же проверка, что verify_telegram_init_data, плюс причина отказа (L-03)."""
        user = json.dumps({"id": int(ADMIN_ID)}, separators=(",", ":"))
        good = sign_init_data(BOT_TOKEN, {"auth_date": str(int(self.clock())), "user": user})
        check = server.telegram_init_data_check
        self.assertEqual(check(good, token=BOT_TOKEN, admins={ADMIN_ID}), (ADMIN_ID, ""))
        self.assertEqual(check(good, token="", admins={ADMIN_ID}), (None, "no_token"))
        self.assertEqual(check(good, token=BOT_TOKEN, admins=set()), (None, "no_admins"))
        self.assertEqual(check(good, token=BOT_TOKEN, admins={"1"}), (None, "not_admin"))
        self.assertEqual(check(good, token=BOT_TOKEN + "x", admins={ADMIN_ID}), (None, "bad_signature"))
        self.assertEqual(check("", token=BOT_TOKEN, admins={ADMIN_ID}), (None, "bad_signature"))
        # подпись не верна — про администраторов ни слова, даже когда их нет
        self.assertEqual(check(good + "x", token=BOT_TOKEN, admins=set()), (None, "bad_signature"))
        self.clock.advance(server.MINIAPP_MAX_AGE_SECONDS + 5)
        self.assertEqual(check(good, token=BOT_TOKEN, admins={ADMIN_ID}), (None, "expired"))
        self.assertEqual(set(server.MINIAPP_DENY), {"bad_signature", "expired", "not_admin", "no_admins", "no_token"})

    def test_missing_hash_or_token(self):
        self.assertIsNone(server.verify_telegram_init_data("user=%7B%7D&auth_date=1", token=BOT_TOKEN, admins={ADMIN_ID}))
        self.assertIsNone(server.verify_telegram_init_data("", token=BOT_TOKEN, admins={ADMIN_ID}))
        self.assertIsNone(server.verify_telegram_init_data("hash=abc", token="", admins={ADMIN_ID}))

    def test_crooked_signature_is_refused_not_crashing(self):
        """Кривой ввод (продолжение UI-5): «é»/«й» в подписи — TypeError у compare_digest на str, одинокий
        суррогат — UnicodeEncodeError у encode; оба роняли вход мини-аппа и ЛЮБОЙ маршрут с заголовком
        X-Telegram-Init-Data в 500. Теперь это просто неподошедшая подпись."""
        for init_data in ("a=1&hash=й", "a=1&hash=\xe9", "a=\ud800&hash=" + "0" * 64, "hash=\ud800"):
            self.assertIsNone(server.verify_telegram_init_data(init_data, token=BOT_TOKEN, admins={ADMIN_ID}), init_data)
        # Настоящая подпись по-прежнему принимается (байтовое сравнение ничего не сломало).
        user = json.dumps({"id": int(ADMIN_ID)}, separators=(",", ":"))
        good = sign_init_data(BOT_TOKEN, {"auth_date": str(int(self.clock())), "user": user})
        self.assertEqual(server.verify_telegram_init_data(good, token=BOT_TOKEN, admins={ADMIN_ID}), ADMIN_ID)

    def test_env_file_is_the_default_source(self):
        write_env("BOT_TOKEN=%s\nALLOWED_IDS=%s\n" % (BOT_TOKEN, ADMIN_ID))
        user = json.dumps({"id": int(ADMIN_ID)}, separators=(",", ":"))
        init_data = sign_init_data(BOT_TOKEN, {"auth_date": str(int(self.clock())), "user": user})
        self.assertEqual(server.verify_telegram_init_data(init_data), ADMIN_ID)


class LocalConsoleTests(unittest.TestCase):
    def test_loopback_without_proxy_headers(self):
        self.assertTrue(server.local_console({}, "127.0.0.1"))
        self.assertTrue(server.local_console({"Host": "127.0.0.1:1985"}, "::1"))
        self.assertTrue(server.local_console({}, "::ffff:127.0.0.1"))

    def test_any_proxy_header_breaks_trust(self):
        for h in server.PROXY_HEADERS:
            self.assertFalse(server.local_console({h: "x"}, "127.0.0.1"), h)
        self.assertEqual(len(server.PROXY_HEADERS), 4)

    def test_non_loopback_peer_is_not_console(self):
        self.assertFalse(server.local_console({}, "203.0.113.9"))
        self.assertFalse(server.local_console({}, ""))

    def test_public_request_by_forwarded_host_only(self):
        self.assertTrue(server.public_request({"X-Forwarded-Host": "example.test"}))
        self.assertFalse(server.public_request({"X-Real-IP": "203.0.113.9"}))
        self.assertFalse(server.public_request({}))

    def test_host_allowlist(self):
        for raw in ("127.0.0.1:1985", "localhost", "LOCALHOST:1985", "[::1]:1985"):
            self.assertIn(server.host_name_only(raw), server.ALLOWED_HOSTS, raw)
        for raw in ("evil.example", "127.0.0.1.evil.example:80", "example.com:1985"):
            self.assertNotIn(server.host_name_only(raw), server.ALLOWED_HOSTS, raw)


class ClientAddressTests(unittest.TestCase):
    """1c.11: корзину перебора выбирает не тот, кто в неё стучится.

    То же правило, что у MCP-сервера: X-Real-IP ставит свой nginx директивой
    proxy_set_header, и доказательством заголовок служит только на петлевом слушателе,
    за которым этот nginx и стоит.
    """

    def _ip(self, peer, headers=None):
        handler = server.AdminHandler.__new__(server.AdminHandler)
        handler.client_address = (peer, 40000) if peer else ()
        handler.headers = headers or {}
        return server.AdminHandler.client_ip(handler)

    def test_header_is_trusted_only_behind_own_nginx(self):
        for peer in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
            self.assertEqual(self._ip(peer, {"X-Real-IP": "203.0.113.9"}), "203.0.113.9", peer)
        # панель, поднятая не на петле (GOAMNEZIA_ADMIN_HOST): заголовок целиком клиентский
        self.assertEqual(self._ip("198.51.100.4", {"X-Real-IP": "203.0.113.9"}), "198.51.100.4")
        self.assertEqual(self._ip("198.51.100.4", {}), "198.51.100.4")

    def test_caller_cannot_pick_his_own_bucket(self):
        """Новый адрес в заголовке на каждый запрос давал бы чистую корзину перебора,
        а названный чужой адрес — запирал бы чужого блокировкой за свои неудачи."""
        buckets = set(server._pwd_bucket(self._ip("198.51.100.4", {"X-Real-IP": "203.0.113.%d" % n}))
                      for n in range(1, 6))
        self.assertEqual(buckets, {"198.51.100.4"})

    def test_junk_in_the_header_is_not_an_address(self):
        self.assertEqual(self._ip("127.0.0.1", {"X-Real-IP": "не адрес"}), "127.0.0.1")
        self.assertEqual(self._ip("127.0.0.1", {"X-Real-IP": "010.0.0.1"}), "127.0.0.1")
        self.assertEqual(self._ip("", {}), "?")


class MaskingTests(unittest.TestCase):
    def test_mask_secrets_recursive(self):
        obj = {"name": "anna", "peers": {"awg2": {"privkey": "P", "psk": "S", "pubkey": "OK"}},
               "list": [{"token": "T"}], "BOT_TOKEN": "B"}
        masked = server.mask_secrets(obj)
        self.assertEqual(masked["peers"]["awg2"]["privkey"], "***")
        self.assertEqual(masked["peers"]["awg2"]["psk"], "***")
        self.assertEqual(masked["peers"]["awg2"]["pubkey"], "OK")
        self.assertEqual(masked["list"][0]["token"], "***")
        self.assertEqual(masked["BOT_TOKEN"], "***")
        self.assertNotIn("P", json.dumps(masked))

    def test_public_config_is_a_whitelist(self):
        cfg = {"mode": "pro", "domain": "example.test", "lang": "en", "profiles": {
            "awg2": {"line": "2.0", "port": 443, "subnet": "10.8.2.0/24", "params": {"MTU": "1280", "H1": "1-2"},
                     "server_pubkey": "PUB", "secret_field": "nope"}},
            "site": {"cert": "letsencrypt"},
            "panel": {"miniapp_path": "app-0123456789ab", "port": 1985}, "mystery": {"privkey": "X"}}
        pub = server.public_config(cfg)
        self.assertNotIn("mystery", pub)
        self.assertEqual(pub["profiles"]["awg2"]["mtu"], "1280")
        self.assertNotIn("params", pub["profiles"]["awg2"])
        self.assertNotIn("server_pubkey", pub["profiles"]["awg2"])
        self.assertEqual(pub["lang"], "en")
        self.assertEqual(server.public_panel_url(cfg), "https://example.test/app-0123456789ab/")
        cfg["panel"]["miniapp_path"] = "a-badprefix"
        self.assertEqual(server.public_panel_url(cfg), "")
        cfg["panel"]["miniapp_path"] = "ok_path1"
        cfg["domain"] = "Bad Domain"
        self.assertEqual(server.public_panel_url(cfg), "")

    def test_panel_url_is_empty_where_the_panel_cannot_exist(self):
        """Путь в конфиге — ещё не адрес (DEFECTS §1.8).

        Ушли из Pro в Lite: vhost'а нет, а путь и домен в конфиге остались.
        Прежний расчёт отдавал ссылку, и экран настроек писал «опубликована»
        над адресом, который ведёт в чужой 404.
        """
        cfg = {"mode": "pro", "domain": "example.test", "site": {"cert": "letsencrypt"},
               "panel": {"miniapp_path": "app-0123456789ab"}}
        self.assertEqual(server.public_panel_url(cfg), "https://example.test/app-0123456789ab/")
        self.assertEqual(server.public_panel_url(dict(cfg, mode="lite")), "")
        # Самоподписанный сертификат — браузер мини-апп не откроет, адреса нет.
        self.assertEqual(server.public_panel_url(dict(cfg, mode="lite_site", site={"cert": "selfsigned"})), "")
        # Lite + сайт на домене с Let's Encrypt — панель работает, адрес есть.
        self.assertEqual(server.public_panel_url(dict(cfg, mode="lite_site")),
                         "https://example.test/app-0123456789ab/")

    def test_login_page_says_why_a_way_in_is_unavailable(self):
        """Причина написана ТЕКСТОМ, а не подсказкой при наведении.

        Панель открывают мини-аппом с телефона, где наведения нет вовсе, а по
        погашенной вкладке и нажать нельзя: человек видел три вкладки, из них
        две мёртвые, и ни слова о том, чего не хватает.
        """
        html = server.render_login_page("ru").decode()
        # 0.5.0 (макет pageLogin): способ, которого нет, не рисуется мёртвой вкладкой — на его месте строка словами.
        self.assertIn("const V=TFA_OK&&PWD_SET?1:(TFA_OK?2:(PWD_SET?3:4));", html)
        self.assertIn("if(V===3)more.push(h('p',{class:'gx-p gx-p--center',text:tr('lg_code_later')}));", html)
        self.assertIn("btn(tr('lg_howto_link'),'ghost',()=>{howto=true;render();},{icon:'circle-question'})", html)
        self.assertIn("h('div',{class:'gx-empty__t',text:tr('lg_noway_t')})", html)
        # Тексты — в обоих словарях и в самой странице (переключатель языка берёт их оттуда же).
        for key in ("lg_code_later", "lg_howto_link", "lg_howto_t", "lg_noway_t", "lg_noway_x"):
            for lang in ("ru", "en"):
                self.assertIn(server.LOGIN_STRINGS[lang][key], html, (lang, key))

    def test_tunnel_command_shows_the_ip_only_to_a_local_visitor(self):
        """Адрес сервера — не для страницы, открытой из интернета.

        Снаружи секретный путь может найти кто угодно, а сервер может стоять за
        чужим фронтом: IP там — единственное, чего чужой не знает (DEFECTS §4.4).
        """
        local = server.render_login_page("ru", public=False).decode()
        public = server.render_login_page("ru", public=True).decode()
        self.assertIn("SERVER_IP", public, "снаружи — заглушка, а не адрес")
        ip = str((server.read_config() or {}).get("server_ip") or "")
        if ip:
            self.assertIn("root@%s" % ip, local, "своему — настоящий адрес")
            self.assertNotIn(ip, public)

    def test_long_secret_path_is_still_an_address(self):
        """512-битный путь (90 знаков) — законный адрес панели.

        Предел длины стоял в трёх местах, и после удлинения пути в ядре панель
        отвечала «панели на домене нет», будучи открытой ровно по этому адресу.
        """
        long_path = "app-" + ("aZ09_-" * 15)[:86]
        cfg = {"mode": "pro", "domain": "example.test", "site": {"cert": "letsencrypt"},
               "panel": {"miniapp_path": long_path}}
        self.assertEqual(server.public_panel_url(cfg), "https://example.test/%s/" % long_path)
        self.assertEqual(server.public_panel_url(dict(cfg, panel={"miniapp_path": "app-" + "a" * 200})), "")

    def test_audit_line_has_no_key_material(self):
        server.audit("tester", "user-key", {"name": "anna", "profile": "awg2", "privkey": "SECRET"}, "success")
        with open(str(server.AUDIT_LOG), encoding="utf-8") as fh:
            line = fh.read().strip().splitlines()[-1]
        self.assertNotIn("SECRET", line)
        rec = json.loads(line)
        self.assertEqual(rec["iface"], "panel")
        self.assertEqual(rec["params"]["privkey"], "***")


class UserMarksTests(unittest.TestCase):
    def test_states_and_marks(self):
        now = 1_800_000_000.0
        self.assertEqual(server.user_state({"enabled": False}, now), "off")
        self.assertEqual(server.user_state({"enabled": True, "expires": "2020-01-01T00:00:00Z"}, now), "exp")
        self.assertEqual(server.user_state({"enabled": True, "quota_bytes": 10, "traffic": {"rx": 5, "tx": 5}}, now), "lim")
        self.assertEqual(server.user_state({"enabled": True, "quota_bytes": 0, "traffic": {"rx": 5, "tx": 5}}, now), "on")
        self.assertEqual(server.USER_MARKS["on"], "●")
        self.assertEqual(server.USER_MARKS["off"], "○")
        self.assertEqual(server.USER_MARKS["exp"], "⏳")
        self.assertEqual(server.USER_MARKS["lim"], "▮")

    def test_decorate_user_online_and_no_secrets(self):
        now = 1_800_000_000.0
        rec = {"name": "anna", "enabled": True, "peers": {"awg2": {"pubkey": "P1", "privkey": "LEAK", "psk": "LEAK2", "ip": "10.8.2.2"},
                                                            "awg3": {"pubkey": "P2", "ip": "10.8.3.2"}}}
        hs = {"P1": {"ts": int(now) - 30, "rx": 1, "tx": 2, "endpoint": "", "profile": "awg2"},
              "P2": {"ts": int(now) - 7200, "rx": 0, "tx": 0, "endpoint": "", "profile": "awg3"}}
        d = server.decorate_user(rec, hs, now)
        self.assertTrue(d["online"])
        self.assertTrue(d["peers"]["awg2"]["online"])
        self.assertFalse(d["peers"]["awg3"]["online"])
        self.assertEqual(d["mark"], "●")
        self.assertNotIn("LEAK", json.dumps(d))

    def test_decorate_user_marks_the_peer_without_a_private_key(self):
        """has_key у каждого пира — по нему панель решает, предлагать ли выдачу.

        У пира из контейнера Amnezia (импорт установки или подхват после потери
        настроек) приватной части ключа на сервере нет: ни .conf, ни vpn://, ни
        QR по нему не собрать. Мост даёт признак сам; панель приводит его к bool,
        и отсутствие поля (ядро постарше) значит «ключ есть» — прежнее поведение,
        а не молчаливое прятанье выдачи у всех подряд.
        """
        now = 1_800_000_000.0
        rec = {"name": "proba", "enabled": True, "imported": True,
               "peers": {"awg2": {"pubkey": "P1", "ip": "10.8.2.2", "has_key": False},
                         "awg3": {"pubkey": "P2", "ip": "10.8.3.2", "has_key": True},
                         "awg1": {"pubkey": "P3", "ip": "10.8.1.2"}}}
        d = server.decorate_user(rec, {}, now)
        self.assertIs(d["peers"]["awg2"]["has_key"], False)
        self.assertIs(d["peers"]["awg3"]["has_key"], True)
        self.assertIs(d["peers"]["awg1"]["has_key"], True)
        self.assertTrue(d["imported"])

    def test_decorate_user_puts_the_line_of_the_profile_on_the_peer(self):
        """AWG31-01: место профиля — не линия (amnezia-awg2 из приложения держит 3.1, 2.0 — в awg3); у пира — линия его
        профиля из config.json. Линию от ядра (когда оно начнёт её слать) не перебиваем; без lines — как раньше."""
        now = 1_800_000_000.0
        rec = {"name": "anna", "enabled": True, "peers": {"awg2": {"pubkey": "P2", "ip": "10.8.2.2"},
                                                            "awg3": {"pubkey": "P3", "ip": "10.8.3.2"},
                                                            "awg1": {"pubkey": "P1", "ip": "10.8.1.2", "line": "1.0"}}}
        d = server.decorate_user(rec, {}, now, {"awg2": "3.1", "awg3": "2.0", "awg1": "1.5"})
        self.assertEqual({p: v.get("line") for p, v in d["peers"].items()}, {"awg2": "3.1", "awg3": "2.0", "awg1": "1.0"})
        self.assertNotIn("line", server.decorate_user(rec, {}, now)["peers"]["awg2"])

    def test_parse_iso(self):
        self.assertIsNone(server.parse_iso(""))
        self.assertIsNone(server.parse_iso("garbage"))
        self.assertAlmostEqual(server.parse_iso("1970-01-01T00:01:00Z"), 60.0)


class SubnetOverlapPayloadTests(unittest.TestCase):
    """SUBNET-01: /api/profiles несёт пересечения подсетей от ядра (profiles-status.subnet_overlaps) белым списком —
    с кем, fixed (старому контейнеру на wg0 подсеть не сменить) и выход move {profile, subnet, keyless}; у профиля —
    subnet_fixed. Мусор и чужие поля не проходят."""

    OVL = [{"profile": "awg1", "subnet": "10.8.1.0/24", "with": ["awg2"], "fixed": True,
            "move": {"profile": "awg2", "subnet": "10.8.2.0/24", "keyless": 2}},
           {"profile": "awg2", "subnet": "10.8.1.0/24", "with": ["awg1", "evil"], "fixed": False,
            "move": {"profile": "awg2", "subnet": "10.8.2.0/24", "keyless": True, "cmd": "rm -rf /"}, "x": 1}]

    def test_overlaps_are_whitelisted(self):
        out = server.subnet_overlaps_payload(self.OVL + ["junk", {"profile": "awg9"}, {"profile": "awg3", "move": "x"}])
        self.assertEqual(out[0], {"profile": "awg1", "subnet": "10.8.1.0/24", "with": ["awg2"], "fixed": True,
                                  "move": {"profile": "awg2", "subnet": "10.8.2.0/24", "keyless": 2}})
        self.assertEqual(out[1]["with"], ["awg1"])
        self.assertEqual(out[1]["move"], {"profile": "awg2", "subnet": "10.8.2.0/24", "keyless": 0})
        self.assertNotIn("x", out[1])
        self.assertEqual(out[2], {"profile": "awg3", "subnet": "", "with": [], "fixed": False, "move": None})
        self.assertEqual(len(out), 3)
        self.assertEqual(server.subnet_overlaps_payload(None), [])

    def test_profiles_payload_passes_overlaps_and_the_fixed_subnet(self):
        snap = {"status": "success", "profiles": [
            {"profile": "awg1", "container": "amnezia-awg", "state": "running", "port": 51820, "line": "1.0", "peers": 1,
             "subnet_fixed": True},
            {"profile": "awg2", "container": "amnezia-awg2", "state": "running", "port": 38123, "line": "2.0", "peers": 2,
             "subnet_fixed": False}], "subnet_overlaps": self.OVL}
        orig = server.read_snapshot
        server.read_snapshot = lambda *a, **kw: snap
        try:
            d = server.profiles_payload("panel")
        finally:
            server.read_snapshot = orig
        fixed = {p["profile"]: p["subnet_fixed"] for p in d["installed"]}
        self.assertEqual(fixed, {"awg1": True, "awg2": False})
        self.assertEqual([e["profile"] for e in d["subnet_overlaps"]], ["awg1", "awg2"])
        self.assertEqual(d["subnet_overlaps"][0]["move"]["subnet"], "10.8.2.0/24")

    # DUP-LINE-01: подхваченная у приложения 3.1 в awg2 (46567) и своя 3.1 в awg3 (6714), 2.0 нет.
    DUP_LC = {"no_20": True, "shared": [{"line": "3.1", "profiles": ["awg2", "awg3", "<x>"]}, {"line": "9.9", "profiles": []}],
              "missing": ["2.0", "x"], "reline": {"profile": "awg3", "from": "3.1", "line": "2.0", "port": 6714,
                                                  "title": "AmneziaWG 3.1 (awg3 · UDP 6714)"}, "text": "слова ядра"}

    def test_lines_check_and_free_lines_are_whitelisted(self):
        lc = server.lines_check_payload(self.DUP_LC)
        self.assertEqual(lc, {"no_20": True, "shared": [{"line": "3.1", "profiles": ["awg2", "awg3"]}], "missing": ["2.0"],
                              "reline": {"profile": "awg3", "from": "3.1", "line": "2.0", "port": 6714,
                                         "title": "AmneziaWG 3.1 (awg3 · UDP 6714)"}})
        bad = server.lines_check_payload({"no_20": "yes", "reline": {"profile": "awg9", "line": "2.0"}})
        self.assertEqual(bad, {"no_20": False, "shared": [], "missing": [], "reline": None})
        self.assertEqual(server.lines_check_payload(None), {"no_20": False, "shared": [], "missing": [], "reline": None})
        odd = server.lines_check_payload({"reline": {"profile": "awg3", "line": "3.1", "port": True, "title": "<b>x</b>"}})
        self.assertEqual(odd["reline"], {"profile": "awg3", "from": "", "line": "3.1", "port": 0, "title": ""})
        self.assertEqual(server.free_lines_payload({"awg3": "2.0", "awg1": "1.5", "awg9": "2.0", "awg2": "<b>"}), {"awg3": "2.0", "awg1": "1.5"})
        self.assertEqual(server.free_lines_payload([]), {})

    def test_profiles_and_overview_carry_the_lines(self):
        """/api/profiles: line_shared, imported, title у профилей; lines_check и free_lines — из краткого status (в
        profiles-status их нет). /api/overview — то же из своего status."""
        prof = {"status": "success", "profiles": [
            {"profile": "awg2", "state": "running", "port": 46567, "line": "3.1", "line_shared": True, "imported": True,
             "title": "AmneziaWG 3.1 (awg2 · UDP 46567)"},
            {"profile": "awg3", "state": "running", "port": 6714, "line": "3.1", "line_shared": True, "imported": False,
             "title": "AmneziaWG 3.1 (awg3 · UDP 6714)"}]}
        st = dict(prof, lines_check=self.DUP_LC, free_lines={"awg1": "1.5"},
                  dupline_fix={"profile": "awg3", "from": "3.1", "line": "2.0", "port": 6714, "title": "AmneziaWG 3.1 · порт 6714",
                               "users": 3, "text": "слова ядра", "detected_at": "2026-10-15T12:00:00Z"})
        seen = []

        def fake(name, who, params=None, **kw):
            seen.append(name)
            return st if name == "status" else prof
        orig = server.read_snapshot
        server.read_snapshot = fake
        try:
            d = server.profiles_payload("panel")
        finally:
            server.read_snapshot = orig
        by = {p["profile"]: p for p in d["installed"]}
        self.assertEqual((by["awg2"]["line_shared"], by["awg2"]["imported"], by["awg2"]["title"]), (True, True, "AmneziaWG 3.1 (awg2 · UDP 46567)"))
        self.assertEqual((by["awg3"]["line_shared"], by["awg3"]["imported"]), (True, False))
        self.assertEqual(d["lines_check"]["reline"]["profile"], "awg3")
        self.assertTrue(d["lines_check"]["no_20"])
        self.assertEqual(d["free_lines"], {"awg1": "1.5"})
        self.assertEqual(seen, ["profiles-status", "status"])
        # Ядро старше полей: line_shared None (панель решит сама), картина линий пустая.
        old = {"status": "success", "profiles": [{"profile": "awg2", "state": "running", "port": 1, "line": "2.0"}]}
        server.read_snapshot = lambda *a, **kw: old
        try:
            d = server.profiles_payload("panel")
        finally:
            server.read_snapshot = orig
        self.assertIsNone(d["installed"][0]["line_shared"])
        self.assertEqual((d["lines_check"]["no_20"], d["lines_check"]["reline"], d["free_lines"]), (False, None, {}))
        # Обзор (и мини-апп): те же поля из своего status.
        def run(action, params=None, who="panel", **kw):
            return st if action == "status" else {"status": "success", "users": []}
        saved = server.run_action
        server.run_action = run
        try:
            o = server.overview_payload("tester")
        finally:
            server.run_action = saved
        self.assertTrue(o["lines_check"]["no_20"])
        self.assertEqual(o["lines_check"]["reline"]["line"], "2.0")
        self.assertEqual(o["free_lines"], {"awg1": "1.5"})
        self.assertEqual({p["profile"]: p["line_shared"] for p in o["profiles"]}, {"awg2": True, "awg3": True})
        self.assertTrue(next(p for p in o["profiles"] if p["profile"] == "awg2")["imported"])
        # Сообщение после обновления — белым списком, без text ядра (панель говорит своими словами).
        self.assertEqual(o["dupline_fix"], {"profile": "awg3", "from": "3.1", "line": "2.0", "port": 6714,
                                            "title": "AmneziaWG 3.1 · порт 6714", "users": 3, "detected_at": "2026-10-15T12:00:00Z"})
        self.assertIsNone(server.dupline_fix_payload(None))
        self.assertIsNone(server.dupline_fix_payload({"profile": "awg9", "line": "2.0"}))
        self.assertIsNone(server.dupline_fix_payload({"profile": "awg3", "line": "<b>"}))
        self.assertEqual(server.dupline_fix_payload({"profile": "awg3", "line": "2.0", "users": -1, "port": "x", "title": "<script>",
                                                     "detected_at": "<x>"}),
                         {"profile": "awg3", "from": "", "line": "2.0", "port": 0, "title": "", "users": 0, "detected_at": ""})

    # Кому что сделать после «Переставить на 2.0» — шаги ядра до неё (ga_amn_reline_preview), S-1 проверки 15.10.
    PRE_STEPS = [{"kind": "subscription", "text": "С подпиской (vera): обновится сама в течение 12 часов или по «Обновить» в приложении."},
                 {"kind": "page", "text": "Со страницей подключения (boris): открыть страницу заново и импортировать новый ключ."},
                 {"kind": "key", "text": "Выдавали ключ или файл (anna): прежний больше не подключится — выдайте ключ vpn:// или файл .conf заново."}]

    def test_reline_steps_pass_the_whitelist(self):
        """S-1: окно согласия панели берёт шаги из lines_check.reline.steps и dupline_fix.steps — server.py их пропускает
        (kind из набора, text — строка с пределом длины, без управляющих знаков); нет поля у ядра — ключа steps нет
        (не «никому ничего делать»), а не пустой список."""
        junk = [{"kind": "page", "text": "x" * 5000}, {"kind": "<script>", "text": "чужое"}, {"kind": "key", "text": 7},
                "строка", {"kind": "key", "text": "a\x00b"}, {"kind": "users", "text": "  "}]
        lc = server.lines_check_payload(dict(self.DUP_LC, reline=dict(self.DUP_LC["reline"], steps=self.PRE_STEPS + junk)))
        self.assertEqual(lc["reline"]["steps"], self.PRE_STEPS)
        self.assertNotIn("steps", server.lines_check_payload(self.DUP_LC)["reline"])
        self.assertEqual(server.lines_check_payload(dict(self.DUP_LC, reline=dict(self.DUP_LC["reline"], steps=[])))["reline"]["steps"], [])
        dfx = server.dupline_fix_payload({"profile": "awg3", "from": "3.1", "line": "2.0", "port": 6714, "users": 3,
                                          "steps": self.PRE_STEPS + [{"kind": "subscription", "text": "y"}] * 20})
        self.assertEqual(dfx["steps"][:3], self.PRE_STEPS)
        self.assertLessEqual(len(dfx["steps"]), server.RELINE_STEPS_MAX)
        self.assertNotIn("steps", server.dupline_fix_payload({"profile": "awg3", "line": "2.0", "steps": "не список"}))

    def test_reline_preview_reads_the_full_status(self):
        """S-1, N-3: краткий status (Обзор, «Протоколы») шагов не несёт — окно согласия просит их отдельно
        (/api/profiles/<профиль>/reline-preview): полный status, шаги этого профиля из lines_check.reline или dupline_fix."""
        st = {"status": "success", "lines_check": dict(self.DUP_LC, reline=dict(self.DUP_LC["reline"], steps=self.PRE_STEPS)),
              "dupline_fix": None}
        seen = []

        def fake(name, who, params=None, **kw):
            seen.append((name, dict(params or {})))
            return st
        orig = server.read_snapshot
        server.read_snapshot = fake
        try:
            d = server.reline_preview_payload("panel", "awg3")
            other = server.reline_preview_payload("panel", "awg2")
            st2 = {"status": "success", "lines_check": {"reline": None},
                   "dupline_fix": {"profile": "awg3", "line": "2.0", "port": 6714, "steps": self.PRE_STEPS[:1]}}
            server.read_snapshot = lambda *a, **kw: st2
            d2 = server.reline_preview_payload("panel", "awg3")
            server.read_snapshot = lambda *a, **kw: {"status": "error", "code": "timeout", "message": "x"}
            bad = server.reline_preview_payload("panel", "awg3")
        finally:
            server.read_snapshot = orig
        self.assertEqual(d, {"profile": "awg3", "steps": self.PRE_STEPS})
        self.assertEqual(seen[0], ("status", {}), "полный status, не brief")
        self.assertEqual(other, {"profile": "awg2", "steps": None})
        self.assertEqual(d2["steps"], self.PRE_STEPS[:1])
        self.assertIsNone(bad["steps"])


class PeerLineTests(unittest.TestCase):
    """AWG31-01 (жалоба 05.10: ключ из вкладки «3.1» — 2.0, из вкладки «2.0» — 3.1): карточка и список людей несут у
    каждого пира линию его профиля из config.json (её же ядро вписывает в ключ, ga_profile_line). Панель подписывала
    вкладки по месту профиля: awg2 — «2.0», awg3 — «3.1», а подхваченный из приложения amnezia-awg2 держит 3.1."""

    SWAPPED = {"profiles": {"awg2": {"line": "3.1", "container": "amnezia-awg2"},
                            "awg3": {"line": "2.0", "container": "amnezia-awg3"},
                            "awg1": {"container": "amnezia-awg1"}}}
    USER = {"name": "anna", "enabled": True, "peers": {"awg2": {"pubkey": "P2", "ip": "10.8.2.2"},
                                                       "awg3": {"pubkey": "P3", "ip": "10.8.3.2"}}}

    def test_profile_lines_take_the_line_field_only(self):
        self.assertEqual(server.profile_lines(self.SWAPPED), {"awg2": "3.1", "awg3": "2.0"})
        self.assertEqual(server.profile_lines({"profiles": {"awg2": {"line": "9.9"}, "awg3": "junk", "awg1": {"line": 2}}}), {})
        self.assertEqual(server.profile_lines({"profiles": []}), {})
        self.assertEqual(server.profile_lines({}), {})

    def test_card_and_list_carry_the_line_of_the_profile(self):
        user = self.USER

        def fake_run(action, params=None, who="panel", **kw):
            if action == "user-show":
                return {"status": "success", "user": json.loads(json.dumps(user))}
            return {"status": "success"}

        def fake_snapshot(name, who, params=None, swr=0.0, **kw):
            if name == "user-list":
                return {"status": "success", "users": [json.loads(json.dumps(user))]}
            return {"status": "error", "code": "transport", "message": "нет"}

        saved = (server.run_action, server.read_snapshot, server.read_prefetch, server.read_config)
        server.run_action, server.read_snapshot = fake_run, fake_snapshot
        server.read_prefetch = lambda *a, **k: None
        server.read_config = lambda: json.loads(json.dumps(self.SWAPPED))
        try:
            status, payload = server.user_card_payload("tester", "anna")
            listed = server.users_payload("tester")
        finally:
            server.run_action, server.read_snapshot, server.read_prefetch, server.read_config = saved
        self.assertEqual(status, 200)
        lines = lambda u: {p: v.get("line") for p, v in u["peers"].items()}  # noqa: E731
        self.assertEqual(lines(payload["data"]["user"]), {"awg2": "3.1", "awg3": "2.0"})
        self.assertEqual(lines(listed["users"][0]), {"awg2": "3.1", "awg3": "2.0"})


class RunActionTests(unittest.TestCase):
    """Мост на заглушке: последняя JSON-строка, окружение без секретов, кэш и аудит."""

    def setUp(self):
        server._cache_clear()
        self.args_file = os.path.join(TMP_ROOT, "args-%d.txt" % id(self))
        self.env_file = os.path.join(TMP_ROOT, "env-%d.txt" % id(self))
        os.environ["FAKE_ADMIN_ARGS_FILE"] = self.args_file
        os.environ["FAKE_ADMIN_ENV_FILE"] = self.env_file
        os.environ["GOAMNEZIA_ADMIN_TOKEN"] = "must-not-leak-into-bridge-env"

    def tearDown(self):
        os.environ.pop("FAKE_ADMIN_ARGS_FILE", None)
        os.environ.pop("FAKE_ADMIN_ENV_FILE", None)
        os.environ.pop("GOAMNEZIA_ADMIN_TOKEN", None)

    def test_status_parsed_cached_and_env_clean(self):
        r1 = server.run_action("status", {}, "tester")
        self.assertEqual(r1["status"], "success")
        self.assertEqual(r1["mode"], "lite")
        r2 = server.run_action("status", {}, "tester")
        self.assertIs(r1, r2)  # 5 с кэша: второй вызов не идёт в мост
        with open(self.args_file, encoding="utf-8") as fh:
            self.assertEqual(len(fh.read().strip().splitlines()), 1)
        with open(self.env_file, encoding="utf-8") as fh:
            env = fh.read()
        self.assertIn("panel:tester panel", env)
        self.assertNotIn("LEAK", env)

    def test_mutation_clears_cache_and_audits(self):
        server.run_action("status", {}, "tester")
        r = server.run_action("user-rename", {"name": "anna", "label": "Анна"}, "tester")
        self.assertEqual(r["status"], "success")
        with open(self.args_file, encoding="utf-8") as fh:
            lines = fh.read().strip().splitlines()
        self.assertIn("--label=Анна", lines[-1])
        server.run_action("status", {}, "tester")
        with open(self.args_file, encoding="utf-8") as fh:
            self.assertEqual(len(fh.read().strip().splitlines()), 3)
        with open(str(server.AUDIT_LOG), encoding="utf-8") as fh:
            rec = json.loads(fh.read().strip().splitlines()[-1])
        self.assertEqual(rec["action"], "user-rename")
        self.assertEqual(rec["who"], "tester")

    def test_transport_and_timeout_codes(self):
        os.environ["FAKE_ADMIN_NO_JSON"] = "1"
        try:
            r = server.run_action("user-list", {}, "tester")
        finally:
            os.environ.pop("FAKE_ADMIN_NO_JSON", None)
        self.assertEqual(r["code"], "transport")
        os.environ["FAKE_ADMIN_SLEEP"] = "3"
        try:
            r = server.run_action("user-list", {}, "tester", timeout=1)
        finally:
            os.environ.pop("FAKE_ADMIN_SLEEP", None)
        self.assertEqual(r["code"], "timeout")
        self.assertEqual(server.bridge_http_status(r), 504)

    def test_crooked_confirm_is_a_refusal_not_a_crash(self):
        """compare_digest на str не принимает не-ASCII: подтверждение «аня» или с суррогатом бросало
        TypeError, и неверное подтверждение отвечало 500 (продолжение UI-5). Теперь — confirm_required."""
        for bad in ("аня", "\ud800", "anná"):
            with self.assertRaises(server.ActionError) as ctx:
                server.run_action("user-del", {"name": "anna"}, "tester", confirm=bad)
            self.assertEqual((ctx.exception.code, ctx.exception.status), ("confirm_required", 400), bad)

    def test_destructive_with_confirm_reaches_bridge(self):
        r = server.run_action("user-del", {"name": "anna"}, "tester", confirm="anna")
        self.assertEqual(r["status"], "success")
        self.assertEqual(server.bridge_http_status({"status": "error", "code": "not_implemented"}), 501)
        self.assertEqual(server.bridge_http_status({"status": "error", "code": "lock_timeout"}), 409)
        # closed_local / needs_site — конфликт состояния сервера (docs/PREFLIGHT.md §6, PANEL.md §5)
        self.assertEqual(server.bridge_http_status({"status": "error", "code": "closed_local"}), 409)
        self.assertEqual(server.bridge_http_status({"status": "error", "code": "needs_site"}), 409)
        self.assertEqual(server.bridge_http_status({"status": "error", "code": "agent_not_found"}), 404)
        self.assertEqual(server.bridge_http_status({"status": "error", "code": "invalid_scope"}), 400)


class ActionLockTests(unittest.TestCase):
    """Замок мутаций берётся с таймаутом, а не молча навсегда.

    Разбор критика: второй администратор, нажавший кнопку во время установки
    профиля, ждал молча до пятнадцати минут и получал «сервер не отвечает».
    Теперь ждём несколько секунд и отвечаем кодом lock_timeout (409) с именем
    действия, которое держит замок.
    """

    def setUp(self):
        server._cache_clear()
        self._wait = server.ACTION_LOCK_WAIT
        server.ACTION_LOCK_WAIT = 0.3

    def tearDown(self):
        server.ACTION_LOCK_WAIT = self._wait
        server._ACTION_BUSY["name"] = ""
        server._ACTION_BUSY["started"] = 0.0

    def test_busy_lock_answers_at_once_and_names_the_action(self):
        server._ACTION_LOCK.acquire()
        server._ACTION_BUSY["name"] = "profile-install"
        server._ACTION_BUSY["started"] = server._now() - 42
        try:
            started = time.monotonic()
            r = server.run_action("user-rename", {"name": "anna", "label": "Анна"}, "tester")
            waited = time.monotonic() - started
        finally:
            server._ACTION_LOCK.release()
        self.assertEqual(r["code"], "lock_timeout")
        self.assertEqual(server.bridge_http_status(r), 409)
        self.assertEqual(r["busy_action"], "profile-install")
        self.assertGreaterEqual(r["busy_seconds"], 42)
        self.assertIn("profile-install", r["message"])
        # Ответ пришёл по таймауту замка, а не по таймауту моста в 300 секунд.
        self.assertLess(waited, 5)

    def test_reading_is_not_blocked_by_a_running_mutation(self):
        server._ACTION_LOCK.acquire()
        server._ACTION_BUSY["name"] = "profile-install"
        try:
            r = server.run_action("user-list", {}, "tester")
        finally:
            server._ACTION_LOCK.release()
        self.assertEqual(r["status"], "success")

    def test_lock_is_released_after_a_normal_call(self):
        server.run_action("user-rename", {"name": "anna", "label": "Анна"}, "tester")
        self.assertTrue(server._ACTION_LOCK.acquire(timeout=0.1))
        server._ACTION_LOCK.release()
        self.assertEqual(server._ACTION_BUSY["name"], "")


class SharePreflightLogsPayloadTests(unittest.TestCase):
    """Данные новых блоков панели: раздачи, проверка портов, бюджет логов."""

    def setUp(self):
        server._cache_clear()

    def test_share_list_never_carries_the_secret_address(self):
        d = server.share_payload("tester")
        self.assertTrue(d["available"])
        self.assertEqual(d["count"], 1)
        item = d["shares"][0]
        self.assertEqual(item["id"], "s-4b1e")
        self.assertEqual(item["seconds_left"], 214)
        # Заглушка нарочно кладёт url и path в список — панель обязана их выкинуть.
        self.assertNotIn("url", item)
        self.assertNotIn("path", item)
        self.assertNotIn("MUSTNOTLEAK", json.dumps(d, ensure_ascii=False))

    def test_share_create_returns_the_address_to_the_one_who_asked(self):
        r = server.run_action("share-create", {"name": "anna", "profile": "awg2", "format": "conf"}, "tester")
        self.assertTrue(server.bridge_ok(r))
        self.assertTrue(r["url"].startswith("https://"))
        # …и в аудит адрес не попадает: там только параметры вызова.
        with open(str(server.AUDIT_LOG), encoding="utf-8") as fh:
            rec = json.loads(fh.read().strip().splitlines()[-1])
        self.assertEqual(rec["action"], "share-create")
        self.assertNotIn("FAKESECRET", json.dumps(rec, ensure_ascii=False))

    def test_preflight_gives_modes_ports_and_hint(self):
        d = server.preflight_payload("tester")
        self.assertTrue(d["available"])
        self.assertTrue(d["modes"]["lite"]["available"])
        self.assertFalse(d["pro_available"])
        self.assertFalse(d["lite_site_available"])
        self.assertEqual(d["modes"]["ports"]["tcp443"]["product"], "gotelegram")
        self.assertIn("nginx", d["hint"])

    def test_logs_status_gives_used_and_budget(self):
        d = server.logs_status_payload("tester")
        self.assertTrue(d["available"])
        names = [c["area"] for c in d["areas"]]
        self.assertIn("goamnezia", names)
        self.assertIn("free", names)
        self.assertEqual(d["areas"][1]["percent"], "90")

    def test_logs_status_names_areas_not_components(self):
        """Участки диска и источники логов — разные списки и разные слова.

        Пока и то, и другое звалось «components», из пяти имён этой таблицы в
        выпадающий список логов годилось одно (DEFECTS §3.9).
        """
        d = server.logs_status_payload("tester")
        self.assertNotIn("components", d)
        for row in d["areas"]:
            self.assertIn("area", row)
            self.assertNotIn("component", row)
        # Источники логов панель берёт из enum моста — это другой список.
        sources = set(server.param_enum("component"))
        self.assertFalse(sources & {c["area"] for c in d["areas"]} - {"nginx"},
                         "совпасть может только nginx — и то случайно")


class PasswordStoreTests(ClockTestCase):
    """Хранение и проверка пароля: на диске только хеш, корзина попыток скользит.

    Часы подменены (ClockTestCase), поэтому окно блокировки проверяется без
    настоящих пятнадцати минут ожидания.
    """

    PASSWORD = "panel-Sample-Pass-42"

    def setUp(self):
        super().setUp()
        with server._PWD_LOCK:
            server._PWD_ATTEMPTS.clear()
        self.file = str(server.PASSWORD_FILE)
        if os.path.exists(self.file):
            os.remove(self.file)

    def tearDown(self):
        if os.path.exists(self.file):
            os.remove(self.file)
        super().tearDown()

    def test_password_is_never_stored_in_plain_text(self):
        self.assertFalse(server.password_configured())
        server.password_set("admin", self.PASSWORD)
        raw = read_text(self.file)
        self.assertNotIn(self.PASSWORD, raw)
        rec = json.loads(raw)
        self.assertEqual(rec["login"], "admin")
        self.assertIn(rec["algo"], ("scrypt", "pbkdf2"))
        self.assertTrue(rec["salt"] and rec["hash"])
        self.assertNotEqual(rec["hash"], rec["salt"])
        # 0600 — файл рядом с токеном и по тем же правилам.
        self.assertEqual(os.stat(self.file).st_mode & 0o777, 0o600)
        self.assertTrue(server.password_configured())
        self.assertEqual(server.password_login(), "admin")

    def test_right_and_wrong_credentials(self):
        server.password_set("admin", self.PASSWORD)
        self.assertTrue(server.password_verify("admin", self.PASSWORD))
        self.assertFalse(server.password_verify("admin", self.PASSWORD + "x"))
        self.assertFalse(server.password_verify("root", self.PASSWORD))  # чужой логин
        self.assertFalse(server.password_verify("", ""))
        self.assertFalse(server.password_verify("admin", ""))

    def test_salt_is_new_on_every_set(self):
        server.password_set("admin", self.PASSWORD)
        first = json.loads(read_text(self.file))
        server.password_set("admin", self.PASSWORD)
        second = json.loads(read_text(self.file))
        self.assertNotEqual(first["salt"], second["salt"])
        self.assertNotEqual(first["hash"], second["hash"])  # одинаковый пароль — разные хеши
        self.assertTrue(server.password_verify("admin", self.PASSWORD))

    def test_new_password_replaces_the_old_one(self):
        server.password_set("admin", self.PASSWORD)
        server.password_set("admin", "another-Sample-Pass-7")
        self.assertFalse(server.password_verify("admin", self.PASSWORD))
        self.assertTrue(server.password_verify("admin", "another-Sample-Pass-7"))

    def test_password_write_waits_for_the_disk(self):
        """1c.12: пароль ложится на диск подтверждённо — fsync файла И его каталога.

        Переименование атомарно по отношению к другим процессам, но не к пропаже питания:
        без fsync файл переживает отключение пустым, а на сервере без бота пароль —
        единственный вход с домена.
        """
        real_fsync = os.fsync
        synced = []

        def spy(fd):
            synced.append(os.fstat(fd).st_ino)
            return real_fsync(fd)

        os.fsync = spy
        try:
            server.password_set("admin", self.PASSWORD)
        finally:
            os.fsync = real_fsync
        self.assertEqual(len(synced), 2, "нужен fsync и временного файла, и каталога")
        self.assertIn(os.stat(self.file).st_ino, synced)                    # сам файл (rename инод хранит)
        self.assertIn(os.stat(os.path.dirname(self.file)).st_ino, synced)   # его каталог

        def boom(fd):
            raise OSError(28, "no space left on device")

        os.fsync = boom
        try:
            with self.assertRaises(server.ActionError):
                server.password_set("admin", "another-Sample-Pass-7")
        finally:
            os.fsync = real_fsync
        # Не легло — это отказ, а не тихое «сохранено»: прежний пароль остаётся рабочим.
        self.assertTrue(server.password_verify("admin", self.PASSWORD))
        tmp = self.file + ".tmp"
        if os.path.exists(tmp):
            os.remove(tmp)

    def test_short_password_and_bad_login_refused(self):
        with self.assertRaises(server.ActionError):
            server.password_set("admin", "short")
        with self.assertRaises(server.ActionError):
            server.password_set("не логин", self.PASSWORD)
        self.assertFalse(os.path.exists(self.file))

    def test_unencodable_password_is_a_refusal(self):
        """Одинокий суррогат в новом пароле (кривой JSON) — отказ bad_password, а не 500 из KDF."""
        with self.assertRaises(server.ActionError) as ctx:
            server.password_set("admin", "\ud800" * 13)
        self.assertEqual(ctx.exception.code, "bad_password")
        self.assertFalse(os.path.exists(self.file))
        # А при проверке такой пароль просто не подходит.
        server.password_set("admin", self.PASSWORD)
        self.assertFalse(server.password_verify("admin", "\ud800"))

    def test_peeking_does_not_grow_the_table(self):
        """Подсмотр корзины (password_blocked) идёт на каждый вход — и не заводит пустых корзин: без
        пароля на сервере до чистки в password_attempt_begin дело не доходит, и записи копились бы."""
        for i in range(50):
            self.assertEqual(server.password_blocked(server._pwd_bucket("198.51.100.%d" % i)), (False, 0))
        with server._PWD_LOCK:
            self.assertEqual(server._PWD_ATTEMPTS, {})

    def test_broken_file_means_no_password(self):
        with open(self.file, "w", encoding="utf-8") as fh:
            fh.write("{ this is not json")
        self.assertFalse(server.password_configured())
        with open(self.file, "w", encoding="utf-8") as fh:
            json.dump({"login": "admin", "algo": "plain", "salt": "00", "hash": "00"}, fh)
        self.assertFalse(server.password_configured())
        self.assertFalse(server.password_verify("admin", self.PASSWORD))

    def test_five_attempts_then_temporary_block(self):
        bucket = server._pwd_bucket("203.0.113.9")
        self.assertEqual(server.password_blocked(bucket), (False, 0))
        for expected_left in (4, 3, 2, 1, 0):
            self.assertEqual(server.password_attempt_failed(bucket), expected_left)
        blocked, wait = server.password_blocked(bucket)
        self.assertTrue(blocked)
        self.assertGreater(wait, 0)
        # Соседний адрес чужим перебором не заперт.
        self.assertEqual(server.password_blocked(server._pwd_bucket("203.0.113.10")), (False, 0))
        # Окно скользящее: спустя пятнадцать минут первая попытка выпадает.
        self.clock.advance(server.PWD_WINDOW + 1)
        self.assertEqual(server.password_blocked(bucket), (False, 0))

    def test_success_clears_the_bucket(self):
        bucket = server._pwd_bucket("203.0.113.11")
        for _ in range(4):
            server.password_attempt_failed(bucket)
        server.password_attempts_clear(bucket)
        self.assertEqual(server.password_blocked(bucket), (False, 0))
        self.assertEqual(server.password_attempt_failed(bucket), server.PWD_MAX_ATTEMPTS - 1)

    # ── SEC-P1 (аудит 25.09.2026): «заперт ли адрес» и «записать попытку» — один шаг ──
    @staticmethod
    def _all_at_once(n, fn):
        """n потоков стартуют разом (барьер) — так приходит пачка параллельных запросов."""
        barrier = threading.Barrier(n)
        out, lock = [], threading.Lock()

        def run():
            barrier.wait()
            r = fn()
            with lock:
                out.append(r)
        threads = [threading.Thread(target=run) for _ in range(n)]
        for th in threads:
            th.start()
        for th in threads:
            th.join(30)
        return out

    def _route(self, method, body, ip):
        """Настоящий маршрут входа на подставном обработчике: адрес — из сокета (не петля)."""
        raw = json.dumps(body).encode("utf-8")
        handler = server.AdminHandler.__new__(server.AdminHandler)
        handler.command = "POST"
        handler.headers = {"X-GoAmnezia-Admin": "1", "Content-Length": str(len(raw)), "X-Forwarded-Host": "example.test"}
        handler.rfile = io.BytesIO(raw)
        handler.client_address = (ip, 40000)
        handler._who = "tester"
        sent = []
        handler.send_json = lambda payload, status=200, extra_headers=None: sent.append((status, payload.get("code")))
        getattr(server.AdminHandler, method)(handler)
        return sent[0]

    def test_parallel_attempts_reserve_exactly_five_places(self):
        bucket = server._pwd_bucket("203.0.113.12")
        got = self._all_at_once(20, lambda: server.password_attempt_begin(bucket))
        allowed = [r for r in got if r[0]]
        self.assertEqual(len(allowed), server.PWD_MAX_ATTEMPTS)
        self.assertEqual(sorted(r[2] for r in allowed), list(range(server.PWD_MAX_ATTEMPTS)))
        self.assertTrue(all(r[1] > 0 for r in got if not r[0]), "отказ называет, сколько ждать")
        self.assertEqual(server.password_blocked(bucket)[0], True)

    def test_parallel_wrong_logins_get_five_tries_not_more(self):
        """Прежде все десять запросов проходили «заперт ли?» раньше, чем первая неудача ложилась в
        корзину (проверка пароля идёт 0.35 с), — и получали десять догадок вместо пяти."""
        server.password_set("admin", self.PASSWORD)
        ip = "203.0.113.13"
        with contextlib.redirect_stderr(io.StringIO()):
            got = self._all_at_once(10, lambda: self._route(
                "route_password_login", {"login": "admin", "password": "wrong-Sample-Pass-0"}, ip))
            self.assertEqual(got.count((401, "bad_credentials")), server.PWD_MAX_ATTEMPTS, got)
            self.assertEqual(got.count((429, "too_many_attempts")), 10 - server.PWD_MAX_ATTEMPTS, got)
            # Предел выбран — и верный пароль до конца окна не проходит, а соседний адрес свободен.
            self.assertEqual(self._route("route_password_login", {"login": "admin", "password": self.PASSWORD}, ip),
                             (429, "too_many_attempts"))
            self.assertEqual(self._route("route_password_login",
                                         {"login": "admin", "password": self.PASSWORD}, "203.0.113.14")[0], 200)

    def test_parallel_wrong_current_password_gets_five_tries_not_more(self):
        server.password_set("admin", self.PASSWORD)
        ip = "203.0.113.15"
        with contextlib.redirect_stderr(io.StringIO()):
            got = self._all_at_once(10, lambda: self._route(
                "route_password_change", {"current": "wrong-Sample-Pass-0", "new": "brand-New-Sample-Pass-2"}, ip))
        self.assertEqual(got.count((401, "bad_credentials")), server.PWD_MAX_ATTEMPTS, got)
        self.assertEqual(got.count((429, "too_many_attempts")), 10 - server.PWD_MAX_ATTEMPTS, got)
        self.assertTrue(server.password_verify("admin", self.PASSWORD), "пароль не сменился")

    def test_ipv6_neighbours_share_one_password_bucket(self):
        self.assertEqual(server._pwd_bucket("2001:db8:1:2::5"), server._pwd_bucket("2001:db8:1:2::77"))
        self.assertNotEqual(server._pwd_bucket("2001:db8:1:2::5"), server._pwd_bucket("2001:db8:1:3::5"))
        self.assertEqual(server._pwd_bucket("127.0.0.1"), "127.0.0.1")   # туннель — как было
        self.assertEqual(server._pwd_bucket(""), "?")

    def test_answer_time_does_not_depend_on_what_failed(self):
        """Верный логин с неверным паролем и вовсе неизвестный логин обязаны
        отвечать одинаково: разница во времени сама называет верный логин."""
        server.password_set("admin", self.PASSWORD)
        samples = []
        for login in ("admin", "nobody", "admin", "nobody"):
            started = time.monotonic()
            self.assertFalse(server.password_check(login, "wrong-Sample-Pass-1"))
            samples.append(time.monotonic() - started)
        known = (samples[0] + samples[2]) / 2
        unknown = (samples[1] + samples[3]) / 2
        self.assertGreaterEqual(min(samples), server.PASSWORD_MIN_RESPONSE - 0.02)
        self.assertLess(abs(known - unknown), 0.1, "время ответа выдаёт существующий логин")


class PasswordCliTests(unittest.TestCase):
    """`server.py --set-password` — то, чем пользуется install_admin.sh.

    KDF живёт в одном месте, и установщик обязан попадать в те же файлы и
    правила, что и сама панель; пароль уходит потоком, а не аргументом.
    """

    def setUp(self):
        self.file = str(server.PASSWORD_FILE)
        if os.path.exists(self.file):
            os.remove(self.file)

    def tearDown(self):
        if os.path.exists(self.file):
            os.remove(self.file)

    def _run(self, args, stdin=""):
        env = dict(os.environ)
        env["GOAMNEZIA_ADMIN_DIR"] = str(server.ADMIN_DIR)
        return subprocess.run([sys.executable, SERVER] + args, input=stdin, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def test_status_and_set(self):
        out = self._run(["--password-status"])
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout)["set"], False)
        out = self._run(["--set-password", "--login=admin"], stdin="cli-Sample-Pass-9\n")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout)["login"], "admin")
        # Пароля нет ни в выводе, ни в файле.
        self.assertNotIn("cli-Sample-Pass-9", out.stdout + out.stderr)
        self.assertNotIn("cli-Sample-Pass-9", read_text(self.file))
        status = json.loads(self._run(["--password-status"]).stdout)
        self.assertTrue(status["set"])
        self.assertEqual(status["login"], "admin")
        self.assertTrue(server.password_verify("admin", "cli-Sample-Pass-9"))

    def test_short_password_exits_with_error(self):
        out = self._run(["--set-password"], stdin="short\n")
        self.assertEqual(out.returncode, 2)
        self.assertFalse(os.path.exists(self.file))


class TrafficPayloadTests(unittest.TestCase):
    """Карточка профиля в «Трафике»: при учёте stats.json — месяц и сегодня, а не живые счётчики интерфейса,
    которые обнуляются при перезапуске контейнера и спорили с таблицей пользователей."""

    PEERS = [{"profile": "awg2", "peers": [{"pubkey": "K1", "latest_handshake_ts": 0, "rx": 7, "tx": 9},
                                           {"pubkey": "K2", "latest_handshake_ts": 0, "rx": 1, "tx": 1}]},
             {"profile": "awg3", "peers": []}]

    def _payload(self, bridge):
        saved = server.run_action
        server.run_action = lambda action, params, who, **kw: bridge
        try:
            return {p["profile"]: p for p in server.traffic_payload("test")["profiles"]}
        finally:
            server.run_action = saved

    def test_month_and_today_come_from_stats(self):
        got = self._payload({"status": "success", "profiles": self.PEERS, "users": [],
                             "month": {"from": "2026-09-01", "by_profile": {"awg2": {"rx": 5000, "tx": 9000}}},
                             "today": {"date": "2026-09-13", "by_profile": {"awg2": {"rx": 40, "tx": "60"}}}})
        self.assertEqual((got["awg2"]["period"], got["awg2"]["rx"], got["awg2"]["tx"]), ("month", 5000, 9000))
        self.assertEqual((got["awg2"]["today_rx"], got["awg2"]["today_tx"]), (40, 60))
        # профиль без записи в учёте — нули за месяц, а не живые счётчики под подписью «за месяц»
        self.assertEqual((got["awg3"]["period"], got["awg3"]["rx"], got["awg3"]["today_tx"]), ("month", 0, 0))
        self.assertEqual(got["awg2"]["peers"], 2)

    def test_without_stats_live_counters_are_labelled(self):
        got = self._payload({"status": "success", "profiles": self.PEERS, "users": [], "month": None, "today": None})
        self.assertEqual((got["awg2"]["period"], got["awg2"]["rx"], got["awg2"]["tx"]), ("live", 8, 10))
        self.assertNotIn("today_rx", got["awg2"])

    def test_hourly_history_reaches_panel_without_unrelated_fields(self):
        saved = server.run_action
        hour = {"hour": "2026-09-27T07:00Z", "rx": 1024, "tx": 512, "active": 3}
        try:
            server.run_action = lambda *a, **kw: {"status": "success", "hours": [
                dict(hour, unrelated="must not escape"), None, {"hour": "broken"}]}
            self.assertEqual(server.traffic_payload("test")["hours"], [hour])
            for missing in (None, {}, "invalid"):
                server.run_action = lambda *a, **kw: {"status": "success", "hours": missing}
                self.assertEqual(server.traffic_payload("test")["hours"], [])
        finally:
            server.run_action = saved


class LoginPageTests(unittest.TestCase):
    """Страница входа: что показано, зависит от того, чем реально можно войти."""

    def setUp(self):
        self.file = str(server.PASSWORD_FILE)
        if os.path.exists(self.file):
            os.remove(self.file)

    def tearDown(self):
        if os.path.exists(self.file):
            os.remove(self.file)
        write_env("")

    def test_without_bot_only_password_is_offered(self):
        write_env("")  # бота нет: про код не говорим ни слова
        server.password_set("admin", "page-Sample-Pass-3")
        html = server.render_login_page("ru").decode("utf-8")
        self.assertIn("const TFA_OK=false;", html)
        self.assertIn("const PWD_SET=true;", html)
        # Блок кода в разметке скрыт и показывается только по TFA_OK.
        self.assertIn('<div id="tfa" style="display:none">', html)
        self.assertIn('id="pass"', html)
        self.assertIn("/api/auth/password", html)

    def test_the_emergency_key_comes_only_from_the_ssh_menu(self):
        """SEC50-08 (решение владельца 28.09): «на домене — только пароль или код из бота; аварийный ключ — только
        формировать в SSH полной строкой». Страница входа не называет ни файла ключа, ни ?token= — только пункт
        SSH-меню, срок в час и то, что ключ сгорает и с домена не принимается."""
        for lang, marks in (("ru", ("Настройки → Веб-панель, админы бота, ИИ-агенты → Панель и мини-апп → Аварийный ключ",
                                    "живёт час и сгорает после входа", "с домена не принимается")),
                            ("en", ("Settings → Web panel, bot admins, AI agents → Panel and mini app → Emergency key",
                                    "lives an hour and burns after sign-in", "never accepted from the domain"))):
            text = server.LOGIN_STRINGS[lang]["lg_way_tok_s"]
            for mark in marks:
                self.assertIn(mark, text, lang)
            for gone in ("?token", "{f}", "/opt/"):
                self.assertNotIn(gone, text, lang)
        write_env("")
        html = server.render_login_page("ru").decode("utf-8")
        self.assertIn("statRow(tr('lg_way_tok'),tr('lg_way_tok_s'),'')", html)
        self.assertNotIn("TOKEN_FILE", html)
        self.assertNotIn(str(server.ADMIN_TOKEN_FILE), html)
        self.assertIn('const NOTICE="";', html)
        self.assertIn('const NOTICE="token_net";', server.render_login_page("ru", notice="token_net").decode("utf-8"))
        self.assertIn('const NOTICE="token_bad";', server.render_login_page("en", notice="token_bad").decode("utf-8"))
        self.assertIn('const NOTICE="";', server.render_login_page("ru", notice="<script>").decode("utf-8"))

    def test_with_bot_both_ways_are_offered(self):
        write_env("BOT_TOKEN=%s\nALLOWED_IDS=%s\n" % (BOT_TOKEN, ADMIN_ID))
        server.password_set("admin", "page-Sample-Pass-3")
        html = server.render_login_page("en").decode("utf-8")
        self.assertIn("const TFA_OK=true;", html)
        self.assertIn("const PWD_SET=true;", html)
        self.assertIn("/api/auth/request", html)
        self.assertIn("/api/auth/password", html)

    def test_without_password_the_page_tells_how_to_set_it(self):
        write_env("")
        html = server.render_login_page("ru").decode("utf-8")
        self.assertIn("const PWD_SET=false;", html)
        self.assertIn("--reset-password", html)
        self.assertIn("ssh -L", html)  # аварийный путь остаётся на месте

    def test_login_name_is_never_shown_on_the_page(self):
        """Имя учётной записи — половина пары к паролю, и страница его не называет.

        Форма подставляла логин значением поля: всякий, кто открыл страницу из
        интернета, получал половину пары даром и подбирал одно значение вместо
        двух. Проверяем не только атрибут, а всю страницу целиком — имя не должно
        просочиться ни подсказкой поля, ни комментарием разметки, ни значением в
        JSON строк (DEFECTS §1.9).
        """
        write_env("")
        server.password_set("pelageya42", "page-Sample-Pass-3")
        for lang in ("ru", "en"):
            html = server.render_login_page(lang).decode("utf-8")
            self.assertNotIn("pelageya42", html, lang)
            self.assertNotIn('value="admin"', html, lang)
            # Подстановки не осталось вовсе — иначе имя вернулось бы правкой рядом.
            self.assertNotIn("__PWD_LOGIN__", html, lang)
        # Умолчание тоже не подсказываем: пароля нет — и логина на странице нет.
        os.remove(self.file)
        self.assertNotIn('value="%s"' % server.PASSWORD_LOGIN_DEFAULT,
                         server.render_login_page("ru").decode("utf-8"))

    def test_wrong_login_answer_does_not_tell_which_half_failed(self):
        """Ответ на неудачный вход одинаков для чужого логина и чужого пароля."""
        for lang in ("ru", "en"):
            wrong = server.LOGIN_STRINGS[lang]["pwd_wrong"]
            self.assertNotIn(server.PASSWORD_LOGIN_DEFAULT, wrong)
            for key in ("pwd_hint", "pwd_login", "pwd_empty", "pwd_unset"):
                self.assertNotIn(server.PASSWORD_LOGIN_DEFAULT,
                                 server.LOGIN_STRINGS[lang][key], "%s/%s" % (lang, key))

    def test_login_strings_are_paired(self):
        self.assertEqual(set(server.LOGIN_STRINGS["ru"]), set(server.LOGIN_STRINGS["en"]))
        self.assertNotIn("__T_", server.render_login_page("ru").decode("utf-8"))

    def test_panel_strings_are_paired(self):
        """Строки самого сервера (отказ «занято») — тоже на двух языках."""
        self.assertEqual(set(server.PANEL_STRINGS["ru"]), set(server.PANEL_STRINGS["en"]))
        for lang in ("ru", "en"):
            self.assertIn("{a}", server.PANEL_STRINGS[lang]["busy"])

    def test_login_page_is_theme_aware(self):
        """Три экрана входа — одна тема.

        Страница входа была жёстко тёмной, панель — по выбору человека, окно
        входа поверх панели — всегда светлым. Теперь у всех трёх одни и те же
        переменные и системная тема как умолчание.
        """
        html = server.render_login_page("ru").decode("utf-8")
        self.assertIn("prefers-color-scheme: dark", html)
        self.assertIn('data-theme="dark"', html)
        self.assertIn("goamnezia-theme", html)  # тот же ключ, что у панели
        self.assertIn("var(--bg)", html)
        # Ни одного «вечного» цвета фона в теле страницы.
        self.assertNotIn("body{background:#", html)


class FrontendI18nTests(unittest.TestCase):
    """Словари app.js: RU и EN в паритете, а разметка не зовёт несуществующих ключей.

    Проверка текстовая, без node: нужен не разбор JS, а два множества ключей.
    Ключ i18n — это `имя:` в начале элемента словаря, поэтому regexp привязан
    к началу строки или к позиции после запятой.
    """

    STATIC = os.path.join(ROOT, "admin-web", "static")

    @classmethod
    def setUpClass(cls):
        cls.app = read_text(os.path.join(cls.STATIC, "app.js"))
        cls.html = read_text(os.path.join(cls.STATIC, "index.html"))

    def dict_keys(self, lang):
        head = "    %s: {" % lang
        # Отсчёт от конца заголовка: иначе в ключи попадает сам «ru»/«en».
        start = self.app.index(head) + len(head)
        end = self.app.index("\n    },", start)
        body = self.app[start:end]
        return set(re.findall(r"(?:^\s+|,\s*)([A-Za-z][A-Za-z0-9_]*):\s", body, re.M))

    def test_ru_and_en_have_the_same_keys(self):
        ru, en = self.dict_keys("ru"), self.dict_keys("en")
        self.assertGreater(len(ru), 300)
        self.assertEqual(ru - en, set(), "нет перевода на английский")
        self.assertEqual(en - ru, set(), "нет строки по-русски")

    def test_every_markup_key_exists_in_both_dictionaries(self):
        ru, en = self.dict_keys("ru"), self.dict_keys("en")
        used = set(re.findall(r'data-i18n(?:-placeholder|-title|-aria-label)?="([A-Za-z0-9_]+)"', self.html))
        # 0.5.0: разделы рисует app.js из словаря, в разметке остались только ключи оболочки (заголовок, «Загрузка…»).
        self.assertTrue(used)
        self.assertEqual(used - ru, set())
        self.assertEqual(used - en, set())

    def test_add_user_always_sends_the_profile_list(self):
        """Форма не умалчивает о пустом наборе профилей (DEFECTS §1.4).

        Раньше поле уходило только непустым (`if (profiles) params.profiles`), и
        ядро понимало молчание как «во все установленные». Теперь форма сама
        отказывает со словами, а поле кладётся в запрос всегда — чтобы ядро
        видело выбор человека, а не его отсутствие.
        """
        self.assertNotIn("if (profiles) params.profiles", self.app)
        self.assertIn('if (!profiles) { profErr.hidden = false; msg.textContent = t("noProfilesPicked"); return; }', self.app)
        self.assertIn("const params = { name, profiles,", self.app)
        for lang in ("ru", "en"):
            self.assertIn("noProfilesPicked", self.dict_keys(lang), lang)

    def test_new_blocks_have_their_strings(self):
        """Раздача, проверка портов, бюджет логов, плашка действия и онбординг."""
        ru = self.dict_keys("ru")
        for key in ("shareCreate", "shareIsKey", "shareRevokeAll", "shareNone", "shareLifetime",
                    "pfCheck", "pfPorts", "pfAdvice", "pfNotChecked",
                    "colUsed", "colBudget", "logsPickHint", "logsBudgetHint",
                    "busyTitle", "busyHint", "errLockBusy",
                    "startStepProfile", "noUsersHint", "noTrafficUsers", "dangerZone"):
            self.assertIn(key, ru, key)


class PagesPayloadTests(unittest.TestCase):
    """Страница подключения и зеркало (docs/CLIENTS.md §9–§10) в server.py: списки
    без адреса и белым списком, QR к адресу рисует сама панель, отказ ядра
    pages_need_domain — конфликт состояния (409), мусор отбивается до моста."""

    SECRET = "FAKESECRETFAKESECRETFAKESECRETFAKESECRET012"

    def test_pages_list_is_whitelisted_and_filtered(self):
        d = server.pages_payload("t", "anna")
        self.assertTrue(d["available"])
        self.assertEqual(sorted(p["id"] for p in d["pages"]), ["p-7f3a", "p-9c1d"])
        for p in d["pages"]:
            self.assertNotIn("url", p)
            self.assertNotIn("path", p)
            self.assertEqual(set(p) - set(server.PAGE_LIST_FIELDS), set())
        self.assertEqual(server.pages_payload("t", "")["count"], 3)
        self.assertNotIn("MUSTNOTLEAK", json.dumps(server.pages_payload("t", "")))

    def test_pages_list_keeps_subscription_state(self):
        # RR-1: subscription (кто завёл подписку для страницы) доходит до панели — по нему галочка «Перевыпустить и
        # подписку» включена у страницы, которая подписку завела (B2-2/A2-4); чужое значение — null, адрес не уходит.
        rows = [{"id": "p-%d" % i, "user": "anna", "subscription": v, "url": "https://h/p/" + self.SECRET}
                for i, v in enumerate(("created", "exists", "off", None, "junk", {"x": 1}))]
        rows.append({"id": "p-old", "user": "anna"})  # старое ядро — поля нет
        saved = server.run_action
        server.run_action = lambda *a, **kw: {"status": "success", "pages": rows}
        try:
            d = server.pages_payload("t", "anna")
        finally:
            server.run_action = saved
        got = {p["id"]: p.get("subscription", "absent") for p in d["pages"]}
        self.assertEqual(got, {"p-0": "created", "p-1": "exists", "p-2": "off", "p-3": None, "p-4": None, "p-5": None,
                               "p-old": "absent"})
        self.assertNotIn(self.SECRET, json.dumps(d))

    def test_clients_catalog_names_mirrorable_platforms_only(self):
        d = server.clients_payload("t")
        apps = {a["id"]: a for a in d["apps"]}
        self.assertEqual(apps["amneziavpn"]["mirror_platforms"], ["android", "windows", "macos", "linux"])
        self.assertEqual(apps["defaultvpn"]["mirror_platforms"], [])
        self.assertFalse(apps["wgtunnel"]["official"])
        self.assertNotIn("assets", json.dumps(d))

    def test_mirror_list_keeps_progress_fields(self):
        d = server.mirror_payload("t")
        self.assertEqual(d["used_mb"], 412)
        self.assertEqual(d["max_total_mb"], 3072)
        downloading = next(i for i in d["items"] if i["state"] == "downloading")
        self.assertEqual(downloading["bytes"], 52428800)
        self.assertEqual(downloading["size"], 209715200)
        for i in d["items"]:
            self.assertEqual(set(i) - set(server.MIRROR_ITEM_FIELDS), set())

    def test_qr_is_attached_only_to_answers_with_an_address(self):
        r = server.attach_page_qr("page-create", {"status": "success", "id": "p-7f3a",
                                                  "url": "https://vpn.example.com/p-%s/" % self.SECRET})
        self.assertIn("qr_png_b64", r)
        if shutil.which("qrencode"):
            self.assertEqual(base64.b64decode(r["qr_png_b64"])[:8], b"\x89PNG\r\n\x1a\n")
        else:
            self.assertEqual(r["qr_png_b64"], "")
        # Не адрес страницы — ничего не дорисовываем; отказ — тоже.
        self.assertNotIn("qr_png_b64", server.attach_page_qr("user-key", {"status": "success", "url": "x"}))
        self.assertNotIn("qr_png_b64", server.attach_page_qr("page-url", {"status": "error", "code": "page_not_found"}))
        self.assertEqual(server.qr_png_b64(""), "")
        # Подписка: QR — главного адреса (профиль mihomo), только у ответов с адресами и только на https
        base = "https://vpn.example.com/s-%s/" % ("S" * 43)
        sub = {"status": "success", "urls": [{"format": "conf", "line": "2.0", "url": base + "awg20.conf"},
                                             {"format": "mihomo", "line": "", "url": base + "mihomo.yaml"}]}
        for act in ("sub-create", "sub-show", "sub-rotate"):
            self.assertIn("qr_png_b64", server.attach_page_qr(act, dict(sub)), act)
        self.assertNotIn("qr_png_b64", server.attach_page_qr("sub-list", dict(sub)))
        self.assertNotIn("qr_png_b64", server.attach_page_qr("sub-show", {"status": "error", "code": "subs_need_domain"}))
        self.assertNotIn("qr_png_b64", server.attach_page_qr("sub-show", {"status": "success", "urls": [
            {"format": "mihomo", "url": "javascript:x"}]}))

    def test_sub_clients_pass_through_with_qr_of_the_address_and_the_app_link(self):
        """Выбор по клиенту (поле clients ответов sub-*, фикстура tests/fixtures/sub_clients.json): идёт в панель
        целиком, как urls; к пункту с qr "url" — QR адреса (только https), с qr "deeplink" — QR ссылки «в приложение», с
        qr_alt "url" — второй код адреса (qr_alt_png_b64); qr "key" панель рисует путём карточки, сервер его не трогает;
        javascript: — без QR; один текст у нескольких пунктов — один qrencode; ответ sub-show не кешируется."""
        with open(os.path.join(ROOT, "tests", "fixtures", "sub_clients.json"), encoding="utf-8") as fh:
            fx = json.load(fh)
        fx["clients"].append({"id": "junk", "group": "once", "items": [
            {"line": "", "url": "https://vpn.example.com/s-x/all.vpn", "deeplink": "javascript://%0Aalert(1)", "qr": "deeplink"},
            {"line": "", "url": "http://vpn.example.com/s-x/all.vpn", "qr": "url"}, "garbage"]})
        made = []

        def fake_qr(text):
            made.append(text)
            return "UE5H"
        saved = server.qr_png_b64
        server.qr_png_b64 = fake_qr
        try:
            r = server.attach_page_qr("sub-show", json.loads(json.dumps(fx)))
            server.attach_page_qr("sub-list", {"status": "success", "clients": json.loads(json.dumps(fx["clients"]))})
        finally:
            server.qr_png_b64 = saved
        base = "https://vpn.example.com/s-FAKESUBSECRETFAKESUBSECRETFAKESUBSECRET0123/"
        # Один раз на адрес all.yaml (FlClash и Clash Meta), на ссылку incy и на её адрес all.txt (второй код); у Clash на
        # компьютере, роутеров и AWG Manager qr "" — QR нет; sub-list QR не получает
        self.assertEqual(sorted(made), sorted([base + "all.yaml", "incy://add/" + base + "all.txt", base + "all.txt"]))
        with_qr = sorted({(c["id"], it.get("line", "")) for c in r["clients"] for it in c["items"]
                          if isinstance(it, dict) and "qr_png_b64" in it})
        self.assertEqual(with_qr, [("clash-meta", ""), ("flclash", ""), ("incy", "")])
        with_alt = sorted(c["id"] for c in r["clients"] for it in c["items"] if isinstance(it, dict) and "qr_alt_png_b64" in it)
        self.assertEqual(with_alt, ["incy"])
        # Остальное — байт в байт как от ядра
        for c in r["clients"]:
            for it in c["items"]:
                if isinstance(it, dict):
                    it.pop("qr_png_b64", None)
                    it.pop("qr_alt_png_b64", None)
        self.assertEqual(r["clients"], fx["clients"])
        self.assertNotIn("sub-show", server.READ_CACHE_TTL)
        self.assertNotIn("sub-create", server.READ_CACHE_TTL)
        if shutil.which("qrencode"):
            r = server.attach_page_qr("sub-show", json.loads(json.dumps(fx)))
            incy = r["clients"][4]["items"][0]
            for field in ("qr_png_b64", "qr_alt_png_b64"):
                self.assertEqual(base64.b64decode(incy[field])[:8], b"\x89PNG\r\n\x1a\n", field)
            self.assertNotEqual(incy["qr_png_b64"], incy["qr_alt_png_b64"], "ссылка «в приложение» и адрес — разные коды")

    def test_need_domain_is_a_state_conflict(self):
        os.environ["FAKE_ADMIN_PAGES"] = "need_domain"
        try:
            r = server.run_action("page-create", {"name": "anna", "ttl": "1h"}, "t", use_cache=False)
        finally:
            os.environ.pop("FAKE_ADMIN_PAGES", None)
        self.assertEqual(r.get("code"), "pages_need_domain")
        self.assertEqual(server.bridge_http_status(r), 409)
        for code, status in (("page_not_found", 404), ("invalid_ttl", 400), ("unknown_app", 404),
                             ("not_mirrorable", 409), ("mirror_busy", 409), ("mirror_unverified", 409)):
            self.assertEqual(server.bridge_http_status({"status": "error", "code": code}), status, code)

    def test_bad_ttl_app_and_platform_never_reach_the_bridge(self):
        for name, params in (("page-create", {"name": "anna", "ttl": "3w"}),
                             ("page-url", {"id": "../etc"}),
                             ("mirror-fetch", {"app": "amneziavpn", "platform": "amiga"}),
                             ("mirror-fetch", {"app": "A B", "platform": "android"})):
            with self.assertRaises(server.ActionError, msg=name):
                server.validate_params(name, params)
        self.assertEqual(server.validate_params("page-create", {"name": "anna", "ttl": "forever"}),
                         {"name": "anna", "ttl": "forever"})


class FrontendPagesTests(unittest.TestCase):
    """Страница подключения и зеркало в app.js: адрес только по нажатию, срок
    проверяется до моста, отзыв и удаление копии спрашивают, строки — в словарях."""

    STATIC = os.path.join(ROOT, "admin-web", "static")

    @classmethod
    def setUpClass(cls):
        cls.app = read_text(os.path.join(cls.STATIC, "app.js"))
        cls.html = read_text(os.path.join(cls.STATIC, "index.html"))

    def _cut(self, start, end):
        b = self.app[self.app.index(start):]
        return b[:b.index(end)]

    def dict_body(self, lang):
        head = "    %s: {" % lang
        start = self.app.index(head) + len(head)
        return self.app[start:self.app.index("\n    },", start)]

    def test_address_is_requested_only_by_the_button(self):
        self.assertEqual(self.app.count('action("page-url"'), 1)
        pages = self._cut("function renderUserPages(user, box, d)", "function showPageUrl(r, user, dlg)")
        self.assertIn('action("page-url"', pages)
        # Список рисуется из /api/pages — там адреса нет по построению.
        self.assertIn('api(`/api/pages?name=', self.app)

    def test_create_checks_the_lifetime_before_the_bridge_and_handles_need_domain(self):
        block = self._cut("async function openPageCreate(user)", "// ── Новый пользователь (шторка)")
        self.assertLess(block.index("if (!validPageTtl(v)) throw new Error(t(\"pageInvalidTtl\"));"), block.index('action("page-create"'))
        self.assertIn('err.code === "pages_need_domain" ? t("pagesNeedDomain")', block)
        # Бессрочной — громче (красным, а не жёлтым).
        show = self._cut("function showPageUrl(r, user, dlg)", "async function openPageCreate(user)")
        self.assertIn('r.forever ? U.al("danger", null, t("pageForeverWarn")) : U.al("warning", null, t("pageIsKey"))', show)
        self.assertIn("qr_png_b64", show)

    def test_revoke_and_mirror_remove_ask_before_the_bridge(self):
        pages = self._cut("function renderUserPages(user, box, d)", "function showPageUrl(r, user, dlg)")
        self.assertIn("confirmDialog(", pages)
        self.assertLess(pages.index("if (!ok) { renderUserCard(user); return; }"), pages.index('action("page-revoke", args, { confirm: p.id })'))
        block = self._cut("async function sgMirrorRemove(i, name) {", "// ── Вход в панель")
        self.assertIn("confirmDialog(", block)
        self.assertLess(block.index("if (!ok) return;"), block.index('runAction("mirror-remove"'))
        # Действие разрушительное: confirm — первый параметр (приложение), как требует сервер.
        self.assertIn("{ confirm: i.app,", block)

    def test_mirror_card_is_in_the_settings_and_polls_while_downloading(self):
        """0.5.0: вкладка «Копии приложений» настроек — список, «Скачать на сервер», удаление; пока идёт загрузка,
        список обновляется сам, и только на этой вкладке."""
        self.assertIn('const SG_TABS = ["look", "mode", "clients", "mirror", "access", "lang", "about"];', self.app)
        tab = self._cut("function sgTabData(force) {", "async function sgPreflight() {")
        self.assertIn('} else if (sg.tab === "mirror") {', tab)
        self.assertIn("sgLoadMirror();", tab)
        rows = self._cut("function sgMirrorRows() {", "async function sgMirrorFetch() {")
        self.assertIn('U.btn(t("sgMrFetch"), "primary", () => sgMirrorFetch()', rows)
        # Ушли со страницы или вкладки — таймер снимается, а не перевзводится по устаревшему списку.
        timer = self._cut("function sgSyncMirrorTimer() {", "function sgMirrorRows() {")
        self.assertIn('i.state === "downloading" || i.state === "queued"', timer)
        self.assertIn('if (!busy || state.page !== "settings" || sg.tab !== "mirror") return;', timer)
        self.assertIn('if (state.page !== "settings" || sg.tab !== "mirror") { clearInterval(sg.mrTimer); sg.mrTimer = null; return; }', timer)
        self.assertNotIn("sgSyncMirrorTimer();", timer)
        # У сорвавшейся загрузки — фраза ядра, код в скобках.
        self.assertIn("s: i.message ? `${i.message} (${i.error || \"?\"})` : `(${i.error || \"?\"})`", rows)

    def test_disabled_user_gets_no_page_button(self):
        """0.5.0 (макет): у выключенного кнопка страницы остаётся, но окно говорит, почему страница не откроется, и
        предлагает включить человека; создать страницу у выключенного панель не пытается."""
        create = self._cut("async function openPageCreate(user)", "// ── Новый пользователь (шторка)")
        self.assertLess(create.index("if (user.enabled === false) {"), create.index('action("page-create"'))
        self.assertIn('title: t("udPageDisabledT")', create)
        self.assertIn('err.code === "user_disabled" ? t("pageUserDisabled")', self.app)
        for lang in ("ru", "en"):
            self.assertIn("pageUserDisabled:", self.dict_body(lang), lang)

    def test_module_error_codes_map_to_honest_statuses(self):
        for code, status in (("invalid_argument", 400), ("invalid_page_id", 400), ("invalid_profiles", 400),
                             ("invalid_platform", 400), ("peer_not_found", 404), ("unit_failed", 503), ("no_curl", 503)):
            self.assertEqual(server.bridge_http_status({"status": "error", "code": code}), status, code)

    def test_subscription_choice_by_app_fits_a_phone_and_speaks_both_languages(self):
        """Выбор по клиенту: уже 560 px (мини-апп) приложения — строкой с переносом над выбранным, без прокрутки вбок;
        решает ширина окна (контейнерный запрос), а не экрана. Подпись блока — «Подписка», без «(Clash, роутеры)»."""
        css = read_text(os.path.join(self.STATIC, "styles.css"))
        self.assertIn(".gx-subcw { container-type: inline-size; min-width: 0; }", css)
        block = css[css.index("@container (max-width: 559.98px) {\n  .gx-subc {"):]
        block = block[:block.index("\n}\n")]
        self.assertIn(".gx-subc { grid-template-columns: minmax(0, 1fr);", block)
        self.assertIn(".gx-subc__list { display: flex; flex-wrap: wrap;", block)
        self.assertNotIn("overflow-x", block)
        for lang in ("ru", "en"):
            body = self.dict_body(lang)
            for key in ("subPickApp", "subGrpAuto", "subGrpOnce", "subRecommended", "subAutoEvery", "subOnce", "subHowTo",
                        "subLinePick", "subAllLines", "subWays", "subFormat", "subQr", "subQrForScanner", "subQrForCamera",
                        "subQrForBoth", "subNoQrDesktop", "subNoQrRouter", "subNoQrPaste", "subNoQrApp", "subCopyUrl",
                        "subCopyKey", "subCopyConf", "subCopyCmd", "subGuide2", "subCopiedUrl", "subCopiedKey",
                        "subCopiedConf", "subCopiedCmd", "subCopyManual", "subOpenAsk", "subOpenIn", "subQrUrl",
                        "subQrCamera", "subQrScanner", "subQrKeyVpn", "subQrKeyConf", "subQrNone", "subQrNoKey"):
                self.assertIn("%s:" % key, body, "%s/%s" % (lang, key))
            # «Короткая ссылка» — третьей кнопкой, только с 2FA через бота (short_available): слова на обоих языках
            for key in ("subShort", "subShortSub", "subShort2faB", "subShort2faT", "subShortHow", "subShortSwap", "subSwapNum",
                        "subSwapLeft", "subSwapOk", "uTabShort", "ushPendT", "ushPendD", "ushBansT", "ushNotMe", "ushSwap"):
                self.assertIn("%s:" % key, body, "%s/%s" % (lang, key))
        # Кнопку решает short_available ответа и каталог ядра, а не short_url пункта (его ядро может не заполнить)
        self.assertNotIn("short_url", self._cut("function subOutput(c, it, r, user, st) {", "function showSubClients("))
        self.assertIn('const shortAvailable = (r) => !!(r && r.short_available === true) && hasAction("short-ensure");', self.app)
        self.assertIn('subShort2faB: "Короткие ссылки работают с 2FA через Telegram-бота.",', self.app)
        self.assertIn('subShort2faB: "Short links work with 2FA via the Telegram bot.",', self.app)
        # Три способа — равными кнопками на одной подложке (как образец владельца), без прокрутки вбок
        self.assertIn(".gx-subtrio { display: grid; grid-auto-flow: column; grid-auto-columns: minmax(0, 1fr);", css)
        self.assertIn('.gx-subtri[aria-pressed="true"] { background: var(--segment); box-shadow: var(--segment-shadow); }', css)
        self.assertIn('ucSub: "Подписка",', self.app)
        self.assertIn('ucSub: "Subscription",', self.app)
        # QR ключа линии — тот же путь, что карточка: fetchKey и общий keyQrParts, своего кодировщика QR в панели нет
        qr = self._cut("function subItemQr(c, it, r, user, st) {", "function subOutput(")
        self.assertIn("fetchKey(user.name, pid)", qr)
        self.assertIn("keyQrParts(pic2, nav, chunks, kind, st)", qr)
        self.assertIn("keyQrParts(pic, nav, chunks, kind, c);", self.app)
        # «Скопировать» с copy "key" берёт ключ линии тем же fetchKey, в формате key_format
        pane = self._cut("function subClientPane(app, r, user, st, redraw) {", "function showSubClients(")
        self.assertIn("const key = await fetchKey(user.name, pid);", pane)
        self.assertIn('value = String((kind === "conf" ? key.conf : key.vpn) || "") || value;', pane)

    def test_both_dictionaries_carry_the_new_strings(self):
        keys = ("pageCreate", "pagesTitle", "pageTtlLabel", "pageTtlForever", "pageTtlCustom", "pageInvalidTtl",
                "pageIsKey", "pageForeverWarn", "pagesNeedDomain", "pageShowUrl", "pageRevoke", "pageRevokeText",
                "mirrorTitle", "mirrorFetch", "mirrorUsage", "mirrorRemoveText", "mirrorPickApp", "mirrorNoApps",
                "mirrorStateDownloading", "platformAndroidTv", "platformRouter")
        for lang in ("ru", "en"):
            body = self.dict_body(lang)
            for key in keys:
                self.assertIn("%s:" % key, body, "%s/%s" % (lang, key))


class FrontendAgentScopeTests(unittest.TestCase):
    """Уровень доступа агента в панели: предвыбор read и честное пояснение (DEFECTS §1d.1 №2).

    Предвыбор write отдавал приватные ключи всех клиентов рукой человека, который об этом
    нигде не прочитал: ни одна строка формы про выдачу ключей не говорила.
    """

    STATIC = os.path.join(ROOT, "admin-web", "static")

    @classmethod
    def setUpClass(cls):
        cls.app = read_text(os.path.join(cls.STATIC, "app.js"))

    def test_read_is_preselected_and_write_is_not(self):
        block = self.app[self.app.index("function openAddAgent() {"):]
        block = block[:block.index("// ── Действия над доступом ──")]
        self.assertIn('const f = { label: "", scope: "read", days: "30", ips: "", pin: false, mode: "static", port: "" };', block)
        self.assertNotIn('scope: "write", days', block)
        # «Только чтение» — первым пунктом, «Чтение и запись» — после него.
        self.assertLess(block.index('["read", "agLvRead", "agLvReadD"]'), block.index('["write", "agLvWrite", "agLvWriteD"]'))

    def test_the_form_says_out_loud_what_each_level_gives(self):
        """0.5.0 (макет): у каждого уровня в окне выдачи — своё пояснение под названием, а не одна строка."""
        block = self.app[self.app.index("function openAddAgent() {"):]
        block = block[:block.index("// ── Действия над доступом ──")]
        self.assertIn(".map(([v, tt, dd]) => ({ title: t(tt), desc: t(dd), on: f.scope === v,", block)
        for lang, words in (("ru", ("Выдаёт ключи любых клиентов", "сменой режима отрежет и себя", "Ключей клиентов не видит")),
                            ("en", ("Issues any client key", "cuts itself off too", "Cannot see client keys"))):
            head = "    %s: {" % lang
            start = self.app.index(head) + len(head)
            body = self.app[start:self.app.index("\n    },", start)]
            for word in words:
                self.assertIn(word, body, "%s: %s" % (lang, word))

    def test_switching_to_pro_warns_that_the_vpn_port_stays(self):
        """Панель молчала про порт вовсе (DEFECTS §1d.1 №10): на 443 встают сайт и панель."""
        for lang, word in (("ru", "Порт VPN не переезжает"),
                           ("en", "The VPN port does not move")):
            head = "    %s: {" % lang
            start = self.app.index(head) + len(head)
            body = self.app[start:self.app.index("\n    },", start)]
            self.assertIn(word, body, lang)
        # 0.5.0: это строка шага «Что изменится» окна смены режима (оба режима с сайтом — одно окно), а не забытая
        # строка словаря; в «Сайт и VPN на 443» — ещё и что сделать: «Занять UDP/443» в «Протоколах».
        block = self.app[self.app.index("function sgOpenWizard(target, line = \"\") {"):]
        block = block[:block.index("function sgAfterMode() {")]
        self.assertIn('t("sgWzPortStays")', block)
        self.assertIn('w.target === "pro" ? t("sgWzPortTake") : t("sgWzPortStaysS")', block)


class FrontendSiteModeTests(unittest.TestCase):
    """Третий режим и лёгкий переход между двумя режимами с сайтом (DEFECTS §1d.2).

    Панель знала две кнопки, и режим `lite_site` из неё был недостижим, хотя
    предполётная проверка на той же странице показывала его доступным. А переход
    между двумя режимами с сайтом шёл тяжёлым путём: панель всегда слала домен, и
    мост звал certbot вместо одной записи в config.json.
    """

    STATIC = os.path.join(ROOT, "admin-web", "static")

    @classmethod
    def setUpClass(cls):
        cls.app = read_text(os.path.join(cls.STATIC, "app.js"))
        cls.html = read_text(os.path.join(cls.STATIC, "index.html"))

    def test_all_three_modes_have_a_button_and_a_name(self):
        # 0.5.0: режимы — карточками в «Настройки → Режим и сайт», у каждого не текущего — «Перейти».
        self.assertIn('const SG_MODES = ["pro", "lite_site", "lite"];', self.app)
        cards = self.app[self.app.index("function sgModeCards() {"):]
        cards = cards[:cards.index("function sgPortRow(key, label) {")]
        # 0.6.0 (F-1): первым — сценарий «Ленивый Pro», за ним три режима по порядку договора.
        self.assertIn("return [sgLazyCard(), ...SG_MODES.map((m) => {", cards)
        self.assertIn('(m === "lite" ? sgToLite() : sgOpenWizard(m))', cards)
        # 0.6.0 (F-2): имя режима — заголовок карточки, подпись из таблицы договора — строкой под ним.
        self.assertIn("title: modeShort(m), sub: modeCap(m)", cards)
        # Название режима одно на все экраны и берётся из словаря, а не из тернарника
        self.assertIn('if (mode === "lite_site") return t("modeLiteSite");', self.app)
        for lang in ("ru", "en"):
            head = "    %s: {" % lang
            start = self.app.index(head) + len(head)
            body = self.app[start:self.app.index("\n    },", start)]
            self.assertIn("modeLiteSite:", body, lang)
            self.assertIn("switchLiteSite:", body, lang)
        # Обзор и настройки больше не сводят три режима к двум
        self.assertNotIn('o.mode === "pro" ? t("modePro") : t("modeLite")', self.app)
        self.assertNotIn('c.mode === "pro" ? t("modePro") : t("modeLite")', self.app)

    def test_switch_between_two_site_modes_goes_the_light_way(self):
        block = self.app[self.app.index("function sgOpenWizard(target, line = \"\") {"):]
        block = block[:block.index("function sgAfterMode() {")]
        # Домен спрашивается ТОЛЬКО из режима без сайта: иначе мост уйдёт тяжёлым
        # путём website_enable_pro (certbot + пересборка vhost) ради переноса порта.
        self.assertIn('const light = cur === "pro" || cur === "lite_site";', block)
        self.assertIn("step: light ? 2 : 1,", block)
        self.assertIn("if (!w.light && !w.nodomain && w.domain) params.domain = w.domain;", block)
        # Лёгкий путь говорит об этом словами: сайт, домен, сертификат и панель остаются как есть.
        self.assertIn('{ m: "info", t: t("sgWzLightKeep"), s: t("sgWzLightKeepS") }', block)

    def test_agent_transport_port_comes_from_the_low_range(self):
        block = self.app[self.app.index("function agTrPort() {"):]
        block = block[:block.index("// ── Список доступов ──")]
        # Отменённый диапазон остался только в объяснении «как было», не в коде
        code = "\n".join(l for l in block.splitlines() if not l.strip().startswith("//"))
        self.assertNotIn("30000", code)
        self.assertIn("1024 + Math.floor(Math.random() * 8976)", code)


class EndpointDefaultTests(unittest.TestCase):
    """Endpoint по умолчанию — в режиме Pro домен сервера, как в ядре (ga_export_pro_domain); в остальных
    режимах IP."""

    def test_default_follows_the_core_rule(self):
        base = {"mode": "pro", "domain": "vpn.example.com", "server_ip": "203.0.113.10",
                "site": {"cert": "letsencrypt"}}
        self.assertEqual(server.endpoint_auto(dict(base)), "vpn.example.com")
        pub = server.public_config(dict(base))
        self.assertEqual((pub["endpoint_auto"], pub["endpoint_effective"]), ("vpn.example.com", "vpn.example.com"))
        self.assertEqual(server.endpoint_auto(dict(base, site={})), "vpn.example.com")   # старая установка без site.cert
        for patch in ({"mode": "lite"}, {"mode": "lite_site"}, {"site": {"cert": "selfsigned"}}, {"domain": ""},
                      {"domain": "203.0.113.10"}, {"domain": "Bad Domain"}):
            self.assertEqual(server.endpoint_auto(dict(base, **patch)), "203.0.113.10", patch)
        pinned = server.public_config(dict(base, endpoint_host="203.0.113.10"))
        self.assertEqual((pinned["endpoint_host"], pinned["endpoint_effective"]), ("203.0.113.10", "203.0.113.10"))

    def test_settings_screen_names_the_default_and_pins_the_ip(self):
        static = os.path.join(ROOT, "admin-web", "static")
        app = read_text(os.path.join(static, "app.js"))
        html = read_text(os.path.join(static, "index.html"))
        # 0.5.0: «IP сервера» — пункт выбора в «Адресах для клиентов»; сохранение вписывает сам адрес.
        self.assertIn('{ title: t("sgEpIp"), desc: tf("sgEpIpDesc", { ip: c.server_ip }), on: x.sel === "ip"', app)
        block = app[app.index("async function sgEpSave() {"):]
        block = block[:block.index("// ── Копии приложений")]
        self.assertIn('const host = x.sel === "default" ? "" : (x.sel === "ip" ? (c.server_ip || "") : x.host.trim().toLowerCase());', block)
        self.assertIn('runAction("endpoint-set", { host }', block)
        self.assertNotIn('c.endpoint_host || `${t("serverIp")} (${c.server_ip || "—"})`', app)
        for lang in ("ru", "en"):
            head = "    %s: {" % lang
            start = app.index(head) + len(head)
            body = app[start:app.index("\n    },", start)]
            for key in ("pinEndpointIp:", "endpointByDefault:"):
                self.assertIn(key, body, (lang, key))


class BotAdminsViewTests(unittest.TestCase):
    """Панель про админов бота молчала вовсе (DEFECTS §1d.2 «управление админами»).

    Управляет списком по-прежнему бот — .env его файл, и правило «владелец первый»
    живёт там же. Панель обязана хотя бы ПОКАЗЫВАТЬ, кто может управлять сервером
    из Telegram: в SSH это видно, в панели не было ни строки.
    """

    def setUp(self):
        self._saved = (server.BOT_DIR / ".env").read_text(encoding="utf-8") \
            if (server.BOT_DIR / ".env").exists() else None

    def tearDown(self):
        if self._saved is None:
            try:
                (server.BOT_DIR / ".env").unlink()
            except OSError:
                pass
        else:
            write_env(self._saved)

    def test_order_is_kept_and_the_first_id_is_the_owner(self):
        write_env("BOT_TOKEN=123456:abc\nALLOWED_IDS=777, 111 222\n")
        view = server.bot_admins_view()
        # Порядок записи, а не по возрастанию: сортировка подменила бы владельца соседом
        self.assertEqual(view["ids"], ["777", "111", "222"])
        self.assertEqual(view["owner"], "777")
        self.assertEqual(view["managed_by"], "bot")

    def test_owner_id_from_env_wins_over_the_list_order(self):
        # Владельца назначают в SSH-меню (OWNER_ID) — панель показывает того же, что и бот
        write_env("BOT_TOKEN=123456:abc\nALLOWED_IDS=777,111\nOWNER_ID=111\n")
        view = server.bot_admins_view()
        self.assertEqual(view["ids"], ["777", "111"])
        self.assertEqual(view["owner"], "111")

    def test_empty_list_is_said_out_loud_not_hidden(self):
        write_env("BOT_TOKEN=123456:abc\n")
        view = server.bot_admins_view()
        self.assertEqual(view["ids"], [])
        self.assertEqual(view["owner"], "")

    def test_settings_payload_carries_it_and_the_page_renders_it(self):
        write_env("BOT_TOKEN=123456:abc\nALLOWED_IDS=777,111\n")
        self.assertEqual(server.settings_payload("test")["bot_admins"]["owner"], "777")
        app = read_text(os.path.join(ROOT, "admin-web", "static", "app.js"))
        html = read_text(os.path.join(ROOT, "admin-web", "static", "index.html"))
        # 0.5.0: «Вход в панель → Администраторы бота» — список id, владелец первым и подписан.
        adm = app[app.index("function sgAdmSec() {"):]
        adm = adm[:adm.index("function sgAccessRows() {")]
        self.assertIn("const s = state.settings || {}, ba = s.bot_admins || {};", adm)
        self.assertIn('k: id === ba.owner ? t("sgAdmOwner") : t("sgAdmAdmin")', adm)
        for lang in ("ru", "en"):
            head = "    %s: {" % lang
            start = app.index(head) + len(head)
            body = app[start:app.index("\n    },", start)]
            self.assertIn("botAdminsOwner:", body, lang)
            self.assertIn("botAdminsHint:", body, lang)


class FrontendConfirmParityTests(unittest.TestCase):
    """Вопросы, которые были только в SSH (DEFECTS §1d.2, восемь мест).

    Проверяем не текст, а то, что вызов моста стоит ПОСЛЕ выхода по «не согласился».
    """

    STATIC = os.path.join(ROOT, "admin-web", "static")

    @classmethod
    def setUpClass(cls):
        cls.app = read_text(os.path.join(cls.STATIC, "app.js"))

    def _block(self, start, end):
        b = self.app[self.app.index(start):]
        return b[:b.index(end)]

    def test_restart_port_and_alt_svc_ask_before_the_bridge(self):
        for start, end, call in (
                ("async function prRestart(pid) {", "async function prRepair(pid) {", 'action("profile-restart"'),
                ("async function restartAllProfiles() {", "// ── Установка", 'action("profiles-restart"'),
                ("async function restartProfile(pid, line) {", "async function repairProfile(pid, line) {",
                 'runAction("profile-restart"'),
                ("async function sgAltSvc(on) {", "function sgCheckBlocks(r) {", 'runAction("site-alt-svc"')):
            block = self._block(start, end)
            self.assertIn("confirmDialog(", block, start)
            self.assertIn("if (!ok) return;", block, start)
            self.assertLess(block.index("if (!ok) return;"), block.index(call), start)
        # Порт (0.5.0): окно само и есть вопрос — мост зовётся только из «Сменить порт» в нём, а предупреждение,
        # что ключи придётся забрать заново, стоит в окне до кнопки.
        for start, end in (("function openPortDialog(pid) {", "function openTake443(pid) {"),
                           ("function openTake443(pid) {", "function openMtuDialog(pid) {")):
            block = self._block(start, end)
            self.assertLess(block.index("onOk: async () => {"), block.index('action("profile-port"'), start)
        self.assertIn('U.al("warning", null, tf("prPortWarn", { n: prPeople(p) }))', self._block("function openPortDialog(pid) {", "function openTake443(pid) {"))

    def test_profile_restarts_are_destructive_with_confirm(self):
        """SEC50-06: перезапуск профилей рвёт связь всем подключённым — разрушительное действие: панель шлёт
        confirm только после диалога, а сервер без него мост не зовёт (как у прочих разрушительных)."""
        self.assertEqual(server.confirm_required("profile-restart"), "profile")
        self.assertEqual(server.confirm_required("profiles-restart"), "")
        calls = []
        orig = server.subprocess.Popen
        server.subprocess.Popen = lambda *a, **kw: calls.append(a) or orig(*a, **kw)
        try:
            for name, params, confirm in (("profile-restart", {"profile": "awg2"}, None),
                                          ("profile-restart", {"profile": "awg2"}, "awg3"),
                                          ("profiles-restart", {}, None),
                                          ("profiles-restart", {}, "profile-restart")):
                with self.assertRaises(server.ActionError) as ctx:
                    server.run_action(name, params, "test", confirm=confirm, use_cache=False)
                self.assertEqual(ctx.exception.code, "confirm_required", (name, confirm))
        finally:
            server.subprocess.Popen = orig
        self.assertEqual(calls, [], "без верного confirm мост не зовётся")

    def test_agent_toggle_asks_in_both_directions(self):
        """Выключить и включить доступ — оба с вопросом: мост зовётся только из кнопки окна (onOk)."""
        for start, end, title, call in (("async function agDisable(a) {", "async function agEnable(a) {", 'tf("agDisableT"', 'action("agent-disable"'),
                                        ("async function agEnable(a) {", "async function agHarden(a) {", 'tf("agEnableT"', 'action("agent-enable"')):
            block = self._block(start, end)
            self.assertIn("formDialog({ title: " + title, block, start)
            self.assertLess(block.index("onOk: async () => {"), block.index(call), start)


class FrontendTelegramColorsTests(unittest.TestCase):
    """SEC50-07: цвета Telegram (themeParams) идут в CSS-переменные только вида #rrggbb — тот же фильтр, что в
    index.html и на странице входа. Раньше tgReceive и applyLook брали строку как есть: «red;background:url(…)»
    или var(…) меняли вид панели чужими правилами. Текстом — и поведение фильтра в node, если он есть."""

    STATIC = os.path.join(ROOT, "admin-web", "static")

    @classmethod
    def setUpClass(cls):
        cls.app = read_text(os.path.join(cls.STATIC, "app.js"))

    def _block(self, start, end):
        b = self.app[self.app.index(start):]
        return b[:b.index(end)]

    def test_every_entry_point_is_filtered(self):
        self.assertIn("const TG_HEX = /^#[0-9a-f]{6}$/i;", self.app)
        self.assertIn('/^#([0-9a-f]{6})$/i', read_text(os.path.join(self.STATIC, "index.html")))
        self.assertIn('/^#([0-9a-f]{6})$/i', read_text(os.path.join(ROOT, "admin-web", "server.py")))
        # sessionStorage при загрузке и событие theme_changed — через tgColors
        self.assertIn("let tgParams = tgColors((", self.app)
        receive = self._block("function tgReceive(eventType, eventData) {", "if (IS_TG) {")
        self.assertIn("const p = tgColors(eventData && eventData.theme_params);", receive)
        self.assertLess(receive.index("tgColors("), receive.index("tgParams = p;"))
        # applyLook: в каждую переменную --tg-* и в акцент — только через tgHex
        look = self._block("function applyLook() {", "function cssHex(color) {")
        tg_lines = [l.strip() for l in look.splitlines() if 'setProperty("--tg-' in l]
        self.assertEqual(len(tg_lines), 4, tg_lines)
        for line in tg_lines:
            self.assertNotIn("tgParams.", line.replace("tgHex(tgParams.", ""), line)
        self.assertIn("accent = tgHex(tgParams.button_color, accent);", look)
        self.assertIn("accentFg = tgHex(tgParams.button_text_color, accentFg);", look)
        self.assertNotRegex(look, r"accent(Fg)? = tgParams\.")

    def test_the_filter_keeps_only_hex_colors(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("нет node")
        src = self._block("const TG_HEX = ", "let tgParams = ")
        script = src + """
const cases = [
  [{bg_color: "#112233", text_color: "red;background:url(https://x/y)", hint_color: "var(--a)", button_color: "#ABCDEF",
    button_text_color: "#12345", secondary_bg_color: " #112233", extra_color: "#000000"}],
  [null], ["#112233"], [{bg_color: 0x112233}],
];
console.log(JSON.stringify(cases.map((c) => tgColors(c[0]))));
console.log(JSON.stringify([tgHex("#a1B2c3", "d"), tgHex("#a1b2c3;", "d"), tgHex(undefined, "d")]));
"""
        p = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        got, hx = [json.loads(line) for line in p.stdout.splitlines()]
        self.assertEqual(got, [{"bg_color": "#112233", "button_color": "#ABCDEF"}, None, None, {}])
        self.assertEqual(hx, ["#a1B2c3", "d", "d"])


class FrontendUiTests(unittest.TestCase):
    """Разметка и стили: то, что чинилось по разбору критика, обязано остаться.

    Проверяем не «красиво», а факты, которые ломались: подложка меню, размер
    полей на телефоне, отдельный ряд для необратимого, контрастные цвета.
    """

    STATIC = os.path.join(ROOT, "admin-web", "static")

    @classmethod
    def setUpClass(cls):
        cls.app = read_text(os.path.join(cls.STATIC, "app.js"))
        cls.html = read_text(os.path.join(cls.STATIC, "index.html"))
        cls.css = read_text(os.path.join(cls.STATIC, "styles.css"))

    def test_mobile_menu_can_be_closed(self):
        """Лист «Ещё» на телефоне закрывается всем привычным: тап по подложке, Esc, переход в раздел (0.5.0)."""
        self.assertIn(".gx-more-bd {", self.css)
        self.assertIn('el("div", { class: "gx-more-bd", onclick: () => closeMore() })', self.app)
        self.assertIn('pushLayer({ kind: "more"', self.app)
        self.assertIn("if (closeTopLayer()) ev.preventDefault();", self.app)  # Escape — верхний слой
        # Переход в раздел: кнопка с data-goto → setPage, и он же закрывает «Ещё». Клик мимо кнопок разделов — не
        # переход (ACC5-2; сами клики — tests/test_panel_dom.py).
        self.assertIn("if (go) setPage(go.dataset.goto);", self.app)
        start = self.app.index("function setPage(page, push = true) {")
        self.assertIn("closeMore();", self.app[start:self.app.index("\n  }\n", start)])

    def test_mobile_fields_are_not_zoomed_by_ios(self):
        """iOS увеличивает страницу на фокусе в поле мельче 16px; цели касания на телефоне — от 44 px."""
        phone = self.css[self.css.index("7. Телефон"):]
        self.assertIn('[data-hero][data-device="phone"] input, [data-hero][data-device="phone"] textarea, '
                      '[data-hero][data-device="phone"] select { font-size: 16px; }', phone)
        self.assertIn("{ min-height: 44px; min-width: 44px; flex-shrink: 0; }", phone)
        self.assertIn('[data-hero][data-device="phone"] .gx-input { height: 44px; font-size: 16px; }', phone)
        # Поле кода из Telegram (окно продления) — тоже 16px.
        self.assertIn(".gx-otp > input { position: absolute; inset: 0; width: 100%; opacity: 0; cursor: text; font-size: 16px;",
                      self.css)

    def test_qr_follows_the_theme(self):
        """QR (владелец 04.10: белая карточка на тёмной панели): блок — плитка в токенах темы, светлая только плашка
        самого кода (--qr-paper, модули --qr-ink); токены — смесь с акцентом под @supports color-mix, без неё — чёрное на
        белом. Код рисуется SVG из модулей той же картинки (qrArt) — и в карточке пользователя, и в окнах с адресом
        (страница, подписка, агент); сами модули — tests/test_panel_dom.py, QrModulesTests."""
        def rule(sel):
            body = self.css[self.css.index(sel + " {"):]
            return body[:body.index("}")]
        for sel in (".gx-qrcard", ".gx-qrbox", ".gx-qrseg", ".gx-qrseg__thumb", ".gx-qrseg > button", ".gx-qrcard__pic",
                    ".gx-qrnav", ".gx-qrcard__at", ".gx-qrart"):
            self.assertNotRegex(rule(sel), r"#[0-9a-fA-F]{3,6}\b|rgba?\(", sel)
        self.assertIn("background: var(--tile-bg); color: var(--foreground);", rule(".gx-qrcard"))
        self.assertIn("background: var(--tile-bg); color: var(--foreground);", rule(".gx-qrbox"))
        self.assertIn("background: var(--qr-paper);", rule(".gx-qrart"))
        self.assertIn(".gx-qrart__paper { fill: var(--qr-paper); }", self.css)
        self.assertIn(".gx-qrart__ink { fill: var(--qr-ink); }", self.css)
        self.assertIn("mix-blend-mode: multiply;", rule(".gx-qrart > img"))
        self.assertIn("[data-hero] { --qr-paper: #fff; --qr-ink: #18181b; }", self.css)
        sup = self.css[self.css.index("@supports (color: color-mix(in oklab, #000 50%, #fff)) {"):]
        sup = sup[:sup.index("\n}\n")]
        self.assertIn("--qr-paper: color-mix(in oklab, var(--accent) 6%, var(--white));", sup)
        self.assertIn("--qr-ink: color-mix(in oklab, var(--accent) 22%, oklch(.18 .006 286));", sup)
        self.assertIn('[data-hero][data-theme="dark"] { --qr-paper: color-mix(in oklab, var(--accent) 10%, oklch(.92 0 0)); }', sup)
        # Картинка qrencode сама по себе больше не выводится: карточка и окна — через qrArt.
        self.assertIn("function qrModules(img) {", self.app)
        self.assertIn("qrArt(`data:image/png;base64,${chunks[part]}`, alt,", self.app)
        self.assertIn('box.append(qrArt(src, cap || "QR"));', self.app)
        self.assertNotIn("img.src = `data:image/png;base64,${chunks", self.app)

    def test_destructive_actions_live_in_their_own_row(self):
        card = self.app[self.app.index("function prCard(m) {"):]
        card = card[:card.index("function prSkel() {")]
        self.assertIn('el("div", { class: "gx-pdanger" }, el("span", { text: t("prDanger") })', card)
        # …и не остались в общем ряду настроек.
        actions_row = card[card.index('el("div", { class: "gx-pbtns" }'):card.index('el("div", { class: "gx-pdanger" }')]
        self.assertNotIn("openRegenDialog(", actions_row)
        self.assertNotIn("removeProfile(", actions_row)

    def test_profile_card_buttons_keep_their_places(self):
        """0.5.0 (макет Panel-core): кнопки профиля — в постоянном порядке, как в макете: перезапуск (или подъём из
        копии), порт, UDP/443 (у профиля на 443 её нет), MTU, подсеть; необратимое — полосой внизу карточки, и
        строки подсетки ставят её на одну высоту в соседних карточках."""
        card = self.app[self.app.index("function prCard(m) {"):]
        card = card[:card.index("function prSkel() {")]
        calls = re.findall(r"\((?:ev, b)?\) => (prRestart|prRepair|openPortDialog|openTake443|openMtuDialog|openSubnetDialog|openRegenDialog|removeProfile|openInstallDialog)\(", card)
        self.assertEqual(calls, ["prRepair", "prRestart", "openPortDialog", "openTake443", "openMtuDialog", "openSubnetDialog",
                                 "openRegenDialog", "removeProfile", "openInstallDialog"])
        self.assertIn('String(p.port) !== "443" ? U.btn(t("prTake443")', card)
        self.assertLess(card.index('el("div", { class: "gx-pkv" })'), card.index('el("div", { class: "gx-pcard__acts" })'))
        self.assertLess(card.index('el("div", { class: "gx-pbtns" }'), card.index('el("div", { class: "gx-pdanger" }'))
        rule = self.css[self.css.index(".gx-pcard {"):]
        rule = rule[:rule.index("}")]
        self.assertIn("grid-row: span 5; display: grid; grid-template-rows: subgrid;", rule)
        self.assertIn(".gx-pcard__acts { align-self: end;", self.css)

    def test_card_rows_line_up_with_the_header(self):
        """Ряды карточек — сеткой auto-fit (колонки не уже --row-min, на узком экране — одна), содержимое страницы
        не шире 1600 px и по центру; ни одна карточка не распирает ряд (min-width: 0)."""
        row = self.css[self.css.index(".gx-row {"):]
        row = row[:row.index("}")]
        self.assertIn("repeat(auto-fit, minmax(min(100%, var(--row-min, 420px)), 1fr))", row)
        self.assertIn(".gx-row > * { min-width: 0; }", self.css)
        page = self.css[self.css.index(".gx-page {"):]
        self.assertIn("max-width: 1600px; margin: 0 auto;", page[:page.index("}")])

    def test_traffic_cards_say_which_period(self):
        """«По профилям» в «Трафике» подписывает период: сегодня (24 часа), текущий месяц учёта stats.json (7 и 30
        дней) или «с запуска контейнера» — у ядра без модуля статистики живые счётчики обнуляются перезапуском."""
        self.assertIn('const live = !profs.length || profs.some((p) => p.period !== "month");', self.app)
        self.assertIn('const profPeriod = live ? t("trPSinceStart") : period;', self.app)
        self.assertIn('const period = tv.range === 0 ? t("trPToday") : (monthWord || t("trPAll"));', self.app)
        for key in ("trPToday", "trPSinceStart", "trPAll", "trByProf", "trMonthNote"):
            self.assertEqual(self.app.count(key + ":"), 2, key)

    def test_tab_switch_shows_a_loader_until_data_arrives(self):
        """Переход между вкладками — со значком загрузки: до ответа ядра страница не видна, и пустой или прежний
        экран («Пользователей пока нет» при живых пользователях) не мелькает. Фоновое обновление страницу не
        прячет — маленькая вертушка у времени обновления."""
        main = self.html[self.html.index('<main class="gx-main" id="main">'):]
        head = main[:main.index('data-page="overview"')]
        self.assertIn('id="pageLoader"', head)
        self.assertIn('<span data-i18n="loading">', head)
        start = self.app.index("function setPage(page, push = true) {")
        body = self.app[start:self.app.index("\n  }\n", start)]
        # Переделанный раздел рисует свой скелет до ответа ядра, старый прячется за «Загрузка…».
        self.assertIn("if (NEW_PAGES.has(next)) { setPageLoading(false); rerenderPage(); }", body)
        self.assertIn("else setPageLoading(true);", body)
        self.assertIn("function overviewSkeleton() {", self.app)
        # Само обновление раздела — runRefresh (refreshPage только не даёт двум обновлениям раздела идти разом).
        start = self.app.index("async function runRefresh(page, opts) {")
        refresh = self.app[start:self.app.index("\n  }\n", start)]
        self.assertIn("if (page === state.page) {\n        setPageLoading(false);", refresh)
        self.assertIn("} finally {", refresh)

    def test_users_list_is_not_called_empty_before_it_loads(self):
        """«Пользователей пока нет» и «Список · 0» — только после удачной загрузки; отказ ядра не затирает список."""
        start = self.app.index("async function loadUsers(opts) {")
        load_users = self.app[start:self.app.index("\n  }\n", start)]
        self.assertIn("state.usersLoaded = true;", load_users)
        self.assertLess(load_users.index("if (data.error)"), load_users.index("state.users = data.users || [];"))
        start = self.app.index("function renderUsersList() {")
        render = self.app[start:self.app.index("\n  }\n", start)]
        # До первой удачной загрузки — скелет, а не «пользователей нет»; счётчики у вкладки и отбора — тоже после.
        self.assertLess(render.index("if (!state.usersLoaded) {"), render.index('t("uEmptyT")'))
        self.assertIn('n: state.usersLoaded ? (state.users || []).length : ""', self.app)
        self.assertIn("if (state.usersLoaded) b.append(el(\"span\", { class: \"gx-seg__n\"", self.app)

    def test_progress_plate_is_in_the_section_header(self):
        """Плашка идущего действия — над содержимым раздела, на любой странице (0.5.0: первой строкой в <main>)."""
        main = self.html[self.html.index('<main class="gx-main" id="main">'):]
        self.assertLess(main.index('id="progress"'), main.index('data-page="overview"'))
        self.assertIn('el("span", { class: "gx-busy__c", id: "progressClock" })', self.app)
        self.assertIn("startProgress(name)", self.app)

    def test_one_class_is_described_by_one_rule(self):
        """Строка состояния «Обновлений Amnezia», «Импорта» и «Трафика» (.gx-ptile) жила под тем же именем, что
        плитка-значок профиля на «Обзоре», и каскад сливал два правила: строка брала 36×36 у значка — серый
        квадратик, а текст узкой колонкой вылезал за низ карточки (снимок владельца, 1.0.7). Одиночный класс
        описан на верхнем уровне стилей одним правилом; общее у нескольких классов — правилом через запятую,
        уточнения — модификатором или в @media."""
        # Комментарии — пустыми строками того же числа, чтобы номера строк в сообщении совпали с файлом.
        css = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), self.css, flags=re.S)
        seen, dups, depth = {}, [], 0
        for m in re.finditer(r"([^{}]*)\{|\}", css):
            if m.group(0) == "}":
                depth = max(0, depth - 1)
                continue
            sel = m.group(1).strip()
            if depth == 0 and re.fullmatch(r"\.[\w-]+", sel):
                line = css.count("\n", 0, m.start(1) + m.group(1).index(sel)) + 1
                if sel in seen:
                    dups.append("%s (строки %d и %d)" % (sel, seen[sel], line))
                seen.setdefault(sel, line)
            depth += 1
        self.assertEqual(dups, [], "один класс описан двумя правилами — каскад сольёт их")
        # Строка состояния тянется во всю ширину карточки: ни размеров, ни flex-основы ей не задано.
        tile = self.css[self.css.index(".gx-ptile {") + len(".gx-ptile {"):]
        props = {d.split(":")[0].strip() for d in tile[:tile.index("}")].split(";") if ":" in d}
        self.assertEqual(props & {"width", "height", "max-width", "flex", "flex-basis"}, set())
        self.assertIn(".gx-profrow__ic { width: 36px; height: 36px;", self.css)
        self.assertIn('class: `gx-profrow__ic${!p || unk ? " gx-profrow__ic--off" : ""}`', self.app)
        self.assertEqual(re.findall(r'class: [`"]gx-ptile\b[^`"]*[`"], html: ic\(', self.app), [])

    def test_contrast_colours_are_used_for_text_on_tints(self):
        """Метка «на связи» и предупреждения брали цвет своей же заливки: текст на мягкой подложке — *-soft-foreground."""
        self.assertIn(".gx-chip--success { background: var(--success-soft); color: var(--success-soft-foreground); }", self.css)
        self.assertIn(".gx-chip--warning { background: var(--warning-soft); color: var(--warning-soft-foreground); }", self.css)
        self.assertIn(".gx-al--warning { background: var(--warning-soft); color: var(--warning-soft-foreground); }", self.css)
        self.assertIn("--success-soft-foreground: color-mix(in oklab, var(--success) 55%, var(--foreground) 45%);", self.css)
        self.assertIn('[data-hero][data-theme="dark"] {', self.css)

    def test_no_inline_colours_left_in_the_login_overlay(self):
        """Окно входа рисовалось инлайновыми стилями и было всегда светлым: теперь это окно панели на токенах темы."""
        gate = self.app[self.app.index("function showAuthGate(expired) {"):]
        gate = gate[:gate.index("\n  }\n")]
        self.assertIn("openDialog({ stack: true, alert: true, noClose: true", gate)
        self.assertNotIn("style", gate)
        self.assertNotIn('setAttribute("style"', self.app)


class FrontendImportedKeyTests(unittest.TestCase):
    """Импортированный пир в карточке панели: выдачи нет, перевыпуск есть.

    У пира, пришедшего из контейнера Amnezia, приватной части ключа на сервере
    нет (`has_key: false`), и все шесть кнопок выдачи ответили бы одним и тем же
    `no_private_key`. Панель предлагала их молча, и человек узнавал правду из
    ошибки — четырежды подряд. Проверяем не «красиво», а четыре факта: правда
    стоит выше кнопок; у такого пира кнопок выдачи нет вовсе; у обычного они на
    месте; перевыпуск спрашивает ДО моста, идёт снять-потом-выдать и держит
    окно, если снятие прошло, а выдача упала.

    Проверка текстовая, без node, — как у соседних Frontend*Tests.
    """

    STATIC = os.path.join(ROOT, "admin-web", "static")
    ISSUE_KEYS = ("ucDl", "ucCopyVpn", "ucText", "ucShare", "ucPage")

    @staticmethod
    def _cut(text, start, end):
        b = text[text.index(start):]
        return b[:b.index(end)]

    @classmethod
    def setUpClass(cls):
        cls.app = read_text(os.path.join(cls.STATIC, "app.js"))
        cls.card = cls._cut(cls.app, "function renderUserCard(user) {", "function renderPeerPane(")
        cls.pane = cls._cut(cls.app, "function renderPeerPane(", "async function fetchKey(")
        cls.reissue = cls._cut(cls.app, "async function reissuePeer(user, pid) {", "async function removePeer(")
        cls.body = cls._cut(cls.app, "function drawCardBody(user, pid) {", "const installedProfiles")

    def dict_body(self, lang):
        head = "    %s: {" % lang
        start = self.app.index(head) + len(head)
        return self.app[start:self.app.index("\n    },", start)]

    def test_card_says_the_truth_above_the_buttons(self):
        """0.5.0: правда про ключ из приложения — во вкладке профиля первой строкой, до кнопки «Привязать старый
        ключ», и в шапке карточки («из приложения Amnezia»), если такой ключ есть хоть на одном профиле."""
        self.assertIn("const userImported = (u) => Object.values(u.peers || {}).some((p) => p && p.has_key === false);", self.app)
        self.assertIn('if (userImported(user)) sub.append(document.createTextNode(` · ${t("uFromApp")}`));', self.card)
        imported = self._cut(self.pane, "if (!hasKey) {", "return { tiles, blocks, qr: null };")
        self.assertLess(imported.index('U.al("accent", tf("ucImpB"'), imported.index('U.btn(t("attachKey")'))

    def test_disabled_key_is_flagged_and_download_shows_errors(self):
        """Ключ выключенного пользователя мост помечает disabled — панель говорит об этом у QR, копирования и
        скачивания. Скачивание .conf идёт запросом ключа с showError, а не простой ссылкой, которая сохраняла JSON
        с ошибкой под именем .conf."""
        self.assertGreaterEqual(self.pane.count("key.disabled === true"), 3)
        self.assertIn('t("keyDisabledWarn")', self.pane)
        self.assertNotIn("href: confHref", self.pane)
        dl = self._cut(self.pane, "const downloadConf = async () => {", "\n    };")
        # Запрос ключа — с выбранным адресом сервера (пустой — как обычно), см. FrontendKeyAddressTests.
        self.assertIn("fetchKey(name, pid, want)", dl)
        # Ошибка — словами: keyError показывает no_private_key окном, остальное — тем же showError.
        self.assertIn("keyError(err)", dl)
        self.assertIn('if (!err || err.code !== "no_private_key") { showError(err); return; }', self.pane)

    def test_imported_peer_is_offered_no_key_at_all(self):
        self.assertIn("const hasKey = peer.has_key !== false;", self.pane)
        row = self._cut(self.pane, "const issueRow = hasKey ? [", "] : [];")
        for key in self.ISSUE_KEYS:
            self.assertIn('t("%s")' % key, row, key)
        # …и ни одной кнопки выдачи не осталось за пределами этой ветки: иначе
        # скачивание .conf или временная ссылка так и предлагались бы.
        outside = self.pane.replace(row, "")
        for key in self.ISSUE_KEYS:
            self.assertNotIn('t("%s")' % key, outside, key)
        # На месте ряда — объяснение, а не пустота; QR у такого пира не запрашивается вовсе.
        imported = self._cut(self.pane, "if (!hasKey) {", "return { tiles, blocks, qr: null };")
        self.assertIn('blocks.push(U.al("accent", tf("ucImpB"', imported)
        self.assertLess(self.pane.index("return { tiles, blocks, qr: null };"), self.pane.index("const showQr = async (kind) => {"))

    def test_an_ordinary_peer_keeps_every_key_button(self):
        """Обратная сторона той же ветки: поля нет или оно true — выдача на месте.

        Сравнение именно с false: ядро постарше признака не шлёт, и прятать у
        него выдачу было бы враньём в другую сторону.
        """
        self.assertIn("peer.has_key !== false", self.pane)
        self.assertNotIn("if (peer.has_key)", self.pane)
        self.assertNotIn("peer.has_key === true", self.pane)
        self.assertIn("const issueRow = hasKey ? [", self.pane)
        # И карточка без импортированных пиров строки про импорт не рисует.
        self.assertIn("if (userImported(user)) sub.append(", self.card)

    def test_reissue_asks_first_and_calls_one_action(self):
        """Вопрос — до моста, и мост зовётся ОДНИМ действием.

        Пара «снять, потом выдать» оставляла окно: закрытая вкладка между двумя
        вызовами отнимала у человека доступ на профиль. Ядро делает подмену под
        общим замком (user-reissue-peer) и само возвращает прежний ключ.
        """
        self.assertIn('U.btn(tf("ucReissueN", { v }), "gdanger", () => reissuePeer(user, pid))', self.body)
        self.assertIn("typedConfirm(", self.reissue)
        ask = self.reissue.index("if (!ok) { renderUserCard(user); return; }")
        call = self.reissue.index('action("user-reissue-peer"')
        self.assertLess(ask, call, "вопрос задаётся до моста")
        self.assertNotIn('runAction("user-del-peer"', self.reissue)
        self.assertNotIn('runAction("user-add-peer"', self.reissue)
        # Разрушительное — тем же цветом, что сосед «Убрать профиль», в полосе «Необратимое».
        self.assertIn('U.btn(tf("ucRemovePeerN", { v }), "gdanger", () => removePeer(user, pid))', self.body)
        self.assertLess(self.body.index('t("ucDanger")'), self.body.index('tf("ucReissueN"'))

    def test_failed_reissue_holds_a_window_and_names_the_way_back(self):
        tail = self.reissue[self.reissue.index('action("user-reissue-peer"'):]
        self.assertIn("noticeDialog(", tail)
        self.assertIn('t("reissueKeyFailedTitle")', tail)
        self.assertIn('tf("reissueKeyFailed"', tail)
        self.assertIn('a: t("addProfile")', tail)
        # Окно, а не тост на шесть секунд: сообщение держится, пока его не закроют.
        notice = self._cut(self.app, "function noticeDialog({", "// Разрушительное: кнопка активна")
        self.assertIn('t("ok")', notice)
        self.assertNotIn("toast(", notice)
        # Мёртвый ключ не остаётся в кэше вкладки — ни на обычный адрес, ни на разовые (`имя/профиль/адрес`).
        self.assertIn("const dead = `${name}/${pid}/`;", self.reissue)
        self.assertIn("if (k.startsWith(dead)) delete state.keyCache[k];", self.reissue)

    def test_both_dictionaries_carry_the_new_strings(self):
        """Ни одной строки для человека мимо словаря — и сразу на двух языках."""
        keys = ("importedNote", "importedNoteMany", "importedNoKeys", "reissueKey", "reissueKeyTitle",
                "reissueKeyText", "reissueKeyDone", "reissueKeyFailedTitle", "reissueKeyFailed")
        for lang in ("ru", "en"):
            body = self.dict_body(lang)
            for key in keys:
                self.assertIn("%s:" % key, body, "%s/%s" % (lang, key))
        ru, en = self.dict_body("ru"), self.dict_body("en")
        # Каноничные слова общего договора (docs/CONTRACT.md, 0.4.5), те же в боте и SSH-меню: где
        # ключ, что у человека всё работает, почему отсюда его не показать, и оба выхода.
        self.assertIn("выдан в приложении Amnezia ещё до подхвата", ru)
        self.assertIn("На сервере только его открытая часть", ru)
        self.assertIn("У пользователя всё работает, но показать или отправить этот ключ отсюда нельзя", ru)
        self.assertIn("«Привязать старый ключ»: вставьте ту ссылку vpn:// или файл .conf", ru)
        self.assertIn("Доступ вернёт кнопка", ru)
        self.assertIn("was issued in the Amnezia app before the takeover", en)
        self.assertIn("The server holds only its public part", en)
        self.assertIn("“Attach the old key”", en)
        self.assertIn("brings access back", en)
        # Владелец читает эти строки сам: без «приватной части» и прочего жаргона.
        for key in ("importedNote", "importedNoteMany", "importedNoKeys"):
            line = re.search(r"\b%s: \"([^\"]*)\"" % key, ru).group(1)
            self.assertNotIn("приватн", line, key)


class AttachKeyServerTests(unittest.TestCase):
    """«Привязать старый ключ» на стороне сервера панели (user-attach-key, docs/CONTRACT.md).

    Ключ человека (ссылка vpn:// или текст .conf) приходит полем `config` тела POST и уходит мосту
    ТОЛЬКО в stdin: не в argv (его видно в ps), не в аудит, не в журнал панели. Каталог ядра помечает
    такой параметр "stdin": true; панель держит имя и сама (STDIN_PARAM_FALLBACK), поэтому тесты идут на
    копии каталога — с пометкой и без неё — и не зависят от того, что уже объявило ядро.
    """

    KEY = "vpn://OLD-KEY-OF-PROBA-DO-NOT-LOG"
    ATTACH = {"mcp": "user_attach_key", "readonly": False, "destructive": False,
              "params": {"name": {"ref": "name", "required": True},
                         "profile": {"ref": "profile", "required": False},
                         "config": {"ref": "key_material", "required": True, "stdin": True}}}

    def setUp(self):
        server._cache_clear()
        self.tmp = tempfile.mkdtemp(prefix="attach-", dir=TMP_ROOT)
        self.args_file = os.path.join(self.tmp, "args.txt")
        self.stdin_file = os.path.join(self.tmp, "stdin.txt")
        for k, v in (("FAKE_ADMIN_ARGS_FILE", self.args_file), ("FAKE_ADMIN_STDIN_FILE", self.stdin_file)):
            os.environ[k] = v
            self.addCleanup(os.environ.pop, k, None)
        self._use_spec(flagged=True)

    def _use_spec(self, flagged=True, present=True):
        """Копия каталога ядра: user-attach-key с пометкой stdin, без неё или вовсе без действия."""
        spec = json.loads(json.dumps(server.load_actions()))
        spec["actions"].pop("user-attach-key", None)
        if present:
            adef = json.loads(json.dumps(self.ATTACH))
            if not flagged:
                adef["params"]["config"].pop("stdin")
            spec["actions"]["user-attach-key"] = adef
        saved = getattr(self, "_saved_load", None) or server.load_actions
        self._saved_load = saved
        server.load_actions = lambda: spec
        self.addCleanup(setattr, server, "load_actions", saved)

    def _argv(self):
        if not os.path.exists(self.args_file):
            return ""
        with open(self.args_file, encoding="utf-8") as fh:
            return fh.read()

    def _post(self, name, body):
        """POST /api/action/<name> через настоящий route_post_api; ответ — (HTTP-статус, тело)."""
        raw = json.dumps(body).encode("utf-8")
        handler = server.AdminHandler.__new__(server.AdminHandler)
        handler.command = "POST"
        handler.headers = {"X-GoAmnezia-Admin": "1", "Content-Length": str(len(raw))}
        handler.rfile = io.BytesIO(raw)
        handler._who = "tester"
        sent = []
        handler.send_json = lambda payload, status=200, extra_headers=None: sent.append((status, payload))
        server.AdminHandler.route_post_api(handler, urllib.parse.urlparse("/api/action/%s" % name))
        self.assertEqual(len(sent), 1)
        return sent[0]

    def test_the_key_param_comes_from_the_catalog_or_the_fallback(self):
        self.assertEqual(server.stdin_param("user-attach-key"), "config")
        self._use_spec(flagged=False)          # пометку забыли — имя держит панель
        self.assertEqual(server.stdin_param("user-attach-key"), "config")
        for other in ("user-key", "share-create", "user-rename"):
            self.assertEqual(server.stdin_param(other), "", other)

    def test_the_key_never_becomes_an_argument(self):
        for flagged in (True, False):
            self._use_spec(flagged=flagged)
            out = server.validate_params("user-attach-key", {"name": "proba", "profile": "awg2"})
            self.assertEqual(out, {"name": "proba", "profile": "awg2"}, flagged)
            # профиль необязателен — ядро найдёт его само по ключу
            self.assertEqual(server.validate_params("user-attach-key", {"name": "proba"}), {"name": "proba"})
            # ключ среди параметров — «лишний параметр», а не --config=… в командной строке
            with self.assertRaises(server.ActionError) as ctx:
                server.validate_params("user-attach-key", {"name": "proba", "config": self.KEY})
            self.assertEqual(ctx.exception.code, "bad_arg")

    def test_run_action_sends_the_key_by_stdin_only(self):
        r = server.run_action("user-attach-key", {"name": "proba", "profile": "awg2"}, "tester", stdin_data=self.KEY)
        self.assertTrue(server.bridge_ok(r), r)
        self.assertIn("привязан", r["message"])
        with open(self.stdin_file, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), self.KEY)
        argv = self._argv()
        self.assertIn("--action=user-attach-key\t--json\t--name=proba\t--profile=awg2\t", argv)
        self.assertNotIn("OLD-KEY", argv, "ключ в argv виден в ps")
        self.assertNotIn("--config", argv)
        with open(str(server.AUDIT_LOG), encoding="utf-8") as fh:
            rec = json.loads(fh.read().strip().splitlines()[-1])
        self.assertEqual(rec["action"], "user-attach-key")
        self.assertEqual(rec["params"], {"name": "proba", "profile": "awg2"})
        self.assertNotIn("OLD-KEY", json.dumps(rec, ensure_ascii=False))

    def test_stdin_is_refused_for_other_actions(self):
        with self.assertRaises(server.ActionError) as ctx:
            server.run_action("user-rename", {"name": "anna", "label": "A"}, "tester", stdin_data="vpn://X")
        self.assertEqual(ctx.exception.code, "bad_arg")
        self.assertEqual(self._argv(), "", "мост даже не запускается")

    def test_route_takes_the_key_out_of_the_body(self):
        status, payload = self._post("user-attach-key", {"name": "proba", "profile": "awg2", "config": self.KEY})
        self.assertEqual(status, 200, payload)
        self.assertIn("привязан", payload["data"]["message"])
        with open(self.stdin_file, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), self.KEY)
        self.assertNotIn("OLD-KEY", self._argv())
        # без пометки в каталоге — то же самое: поле уходит в stdin по запасному имени
        self._use_spec(flagged=False)
        status, _ = self._post("user-attach-key", {"name": "proba", "config": "vpn://SECOND"})
        self.assertEqual(status, 200)
        self.assertNotIn("SECOND", self._argv())

    def test_route_refusals_and_core_words(self):
        # ключ не того вида — слова ядра и 4xx
        for key, code, status in (("просто текст", "config_unreadable", 400), ("", "no_config", 400),
                                  ("vpn://SOMEONE-ELSE", "key_mismatch", 409), ("vpn://OTHER-SERVER", "server_mismatch", 409)):
            got, payload = self._post("user-attach-key", {"name": "proba", "profile": "awg2", "config": key})
            self.assertEqual((got, payload.get("code")), (status, code), key)
            self.assertTrue(payload.get("error"), "слова ядра — в error: %s" % key)
        got, payload = self._post("user-attach-key", {"name": "anna", "profile": "awg2", "config": self.KEY})
        self.assertEqual((got, payload["code"]), (409, "already_has_key"))
        # до моста: больше 64 КБ, не строка, нулевой байт
        calls = self._argv().count("--action=user-attach-key")
        for bad, code in (("vpn://" + "A" * (64 * 1024), "config_unreadable"), (123, "bad_arg"),
                          ("vpn://A\x00B", "config_unreadable")):
            got, payload = self._post("user-attach-key", {"name": "proba", "config": bad})
            self.assertEqual((got, payload.get("code")), (400, code), repr(bad)[:40])
        self.assertEqual(self._argv().count("--action=user-attach-key"), calls, "отказ формы — без запуска bash")
        # у другого действия поле config — лишний параметр, а не аргумент
        got, payload = self._post("user-rename", {"name": "anna", "label": "A", "config": self.KEY})
        self.assertEqual((got, payload.get("code")), (400, "bad_arg"))
        self.assertNotIn("OLD-KEY", self._argv())

    def test_new_codes_map_to_honest_statuses(self):
        for code, status in (("no_config", 400), ("config_unreadable", 400), ("server_mismatch", 409),
                             ("key_mismatch", 409), ("already_has_key", 409), ("foreign_container", 409),
                             ("no_private_key", 409), ("peer_not_found", 404), ("user_not_found", 404)):
            self.assertEqual(server.bridge_http_status({"status": "error", "code": code}), status, code)


class ShortLinksServerTests(unittest.TestCase):
    """Короткие ссылки с 2FA через бота на стороне сервера панели (договор — docs/SUBSCRIPTION.md, фикстура
    tests/fixtures/short_links.json). Ответы short-* несут код — секрет того же порядка, что адрес подписки: только общим
    маршрутом, без общего снимка и Cache-Control: no-store, в аудит — без кода; who у запроса замены и решения ставит
    сервер («panel»), а не страница; итог short-decide и short-status полем status (форма договора) — ответ, а не отказ.
    Каталог ядра с short-* — копия настоящего плюс действия фикстуры: ядро делает другой исполнитель."""

    def setUp(self):
        server._cache_clear()
        self.tmp = tempfile.mkdtemp(prefix="short-", dir=TMP_ROOT)
        self.args_file = os.path.join(self.tmp, "args.txt")
        os.environ["FAKE_ADMIN_ARGS_FILE"] = self.args_file
        self.addCleanup(os.environ.pop, "FAKE_ADMIN_ARGS_FILE", None)
        self.addCleanup(os.environ.pop, "FAKE_ADMIN_SHORT_DECIDE", None)
        with open(os.path.join(ROOT, "tests", "fixtures", "short_links.json"), encoding="utf-8") as fh:
            self.fx = json.load(fh)
        spec = json.loads(json.dumps(server.load_actions()))
        spec["actions"].update(self.fx["catalog"]["actions"])
        spec.setdefault("param_types", {}).update(self.fx["catalog"]["param_types"])
        saved = server.load_actions
        server.load_actions = lambda: spec
        self.addCleanup(setattr, server, "load_actions", saved)
        self.spec = spec

    def _argv(self):
        if not os.path.exists(self.args_file):
            return ""
        with open(self.args_file, encoding="utf-8") as fh:
            return fh.read()

    def _post(self, name, body):
        raw = json.dumps(body).encode("utf-8")
        handler = server.AdminHandler.__new__(server.AdminHandler)
        handler.command = "POST"
        handler.headers = {"X-GoAmnezia-Admin": "1", "Content-Length": str(len(raw))}
        handler.rfile = io.BytesIO(raw)
        handler._who = "tester"
        sent = []
        handler.send_json = lambda payload, status=200, extra_headers=None: sent.append((status, payload))
        server.AdminHandler.route_post_api(handler, urllib.parse.urlparse("/api/action/%s" % name))
        self.assertEqual(len(sent), 1)
        return sent[0]

    def test_who_is_set_by_the_server(self):
        self.assertEqual(server.short_params("short-decide", {"request": "r1", "answer": "47", "who": "bot:1"}),
                         {"request": "r1", "answer": "47", "who": "panel"})
        self.assertEqual(server.short_params("short-replace-request", {"name": "anna", "suffix": "all.yaml"}),
                         {"name": "anna", "suffix": "all.yaml", "who": "panel"})
        self.assertEqual(server.short_params("short-ensure", {"name": "anna", "suffix": "all.yaml"}),
                         {"name": "anna", "suffix": "all.yaml"})
        # Ядро без who у действия — параметра нет вовсе (иначе «лишний параметр»)
        self.spec["actions"]["short-decide"] = dict(self.spec["actions"]["short-decide"],
                                                    params={"request": {"ref": "short_request", "required": True},
                                                            "answer": {"ref": "short_answer", "required": True}})
        self.assertEqual(server.short_params("short-decide", {"request": "r1", "answer": "47", "who": "page"}),
                         {"request": "r1", "answer": "47"})

    def test_routes_pass_the_contract_through(self):
        status, payload = self._post("short-ensure", {"name": "anna", "suffix": "all.yaml"})
        self.assertEqual((status, payload["data"]["display"]), (200, "NV43-9BKX"))
        status, payload = self._post("short-replace-request", {"name": "anna", "suffix": "all.yaml", "who": "bot:9"})
        self.assertEqual((status, payload["data"]["number"]), (200, 47))
        argv = self._argv()
        self.assertIn("--action=short-ensure\t--json\t--name=anna\t--suffix=all.yaml\t", argv)
        self.assertIn("--action=short-replace-request\t--json\t--name=anna\t--suffix=all.yaml\t--who=panel\t", argv)
        self.assertNotIn("bot:9", argv, "who страницы не принимается")
        # Итог полем status (форма договора) — ответ 200, а не отказ моста
        status, payload = self._post("short-decide", {"request": "rqLogin01", "answer": "47"})
        self.assertEqual((status, payload["ok"], payload["data"]["status"]), (200, True, "ok"))
        self.assertIn("--action=short-decide\t--json\t--request=rqLogin01\t--answer=47\t--who=panel\t", self._argv())
        status, payload = self._post("short-status", {"request": "rqLogin01"})
        self.assertEqual((status, payload["data"]["status"]), (200, "pending"))
        os.environ["FAKE_ADMIN_SHORT_DECIDE"] = "merged"
        status, payload = self._post("short-decide", {"request": "rqLogin01", "answer": "swap"})
        self.assertEqual((status, payload["data"]["decision"]), (200, "ok"))
        for name in ("short-pending", "short-bans"):
            status, payload = self._post(name, {})
            self.assertEqual(status, 200, name)
        # Ответ с ошибкой моста по-прежнему отказ, а ok у других действий — не «итог»
        self.assertFalse(server.short_outcome_answer("short-decide", {"status": "error", "code": "short_request_gone"}))
        self.assertFalse(server.short_outcome_answer("sub-show", {"status": "ok"}))
        self.assertTrue(server.short_outcome_answer("short-status", {"status": "frozen"}))
        # Негодный ответ — до моста
        calls = self._argv().count("short-decide")
        status, payload = self._post("short-decide", {"request": "bad id", "answer": "47"})
        self.assertEqual(status, 400)
        self.assertEqual(self._argv().count("short-decide"), calls)

    def _audit_last(self):
        with open(str(server.AUDIT_LOG), encoding="utf-8") as fh:
            return json.loads(fh.read().strip().splitlines()[-1])

    def test_audit_line_has_name_suffix_and_outcome_only(self):
        """SL-LOG-01 и SL-AUD-01 (живая проверка на test1, ec2bb71): в строке аудита панели у действий с секретом в
        ответе — только имя, суффикс и итог. Поле code удачного short-ensure и short-list — сам короткий код: в аудит оно
        не идёт; нажатое администратором число у short-decide — «***» (deny и swap — как есть); отказ — словом моста."""
        status, _ = self._post("short-ensure", {"name": "anna", "suffix": "awg20.conf"})
        self.assertEqual(status, 200)
        rec = self._audit_last()
        self.assertEqual((rec["action"], rec["params"], rec["status"], rec["code"]),
                         ("short-ensure", {"name": "anna", "suffix": "awg20.conf"}, "success", ""))
        self._post("short-list", {"name": "anna"})
        rec = self._audit_last()
        self.assertEqual((rec["action"], rec["params"], rec["code"]), ("short-list", {"name": "anna"}, ""))
        os.environ["FAKE_ADMIN_SHORT_DECIDE"] = "merged"         # как у ядра: status success, итог — decision
        self._post("short-decide", {"request": "rqLogin01", "answer": "47"})
        rec = self._audit_last()
        self.assertEqual((rec["action"], rec["params"], rec["code"]),
                         ("short-decide", {"request": "rqLogin01", "answer": "***", "who": "panel"}, "ok"))
        for answer in ("deny", "swap"):
            self._post("short-decide", {"request": "rqLogin01", "answer": answer})
            self.assertEqual(self._audit_last()["params"]["answer"], answer)
        os.environ["FAKE_ADMIN_STATUS"] = "error"
        self.addCleanup(os.environ.pop, "FAKE_ADMIN_STATUS", None)
        self._post("short-ensure", {"name": "anna", "suffix": "all.yaml"})
        rec = self._audit_last()
        self.assertEqual((rec["status"], rec["code"]), ("error", "forced_error"))
        with open(str(server.AUDIT_LOG), encoding="utf-8") as fh:
            audit_text = fh.read()
        for secret in ("NV439BKX", "NV43-9BKX", "H3TW7RQZ", "H3T-W7RQZ", "Q8RT-W2KM", "https://", '"answer": "47"'):
            self.assertNotIn(secret, audit_text)
        # Слово отказа — только латиница: что-то иное в code (сам код при сбое формы ответа) — «error»
        self.assertEqual(server.audit_code("short-ensure", {"status": "error", "code": "NV439BKX"}), "error")
        self.assertEqual(server.audit_code("short-ensure", {"status": "success", "code": "NV439BKX"}), "")
        self.assertEqual(server.audit_code("user-rename", {"status": "error", "code": "user_not_found"}), "user_not_found")

    def test_codes_are_not_cached_and_not_in_the_audit(self):
        for name in server.SHORT_ACTIONS:
            self.assertNotIn(name, server.READ_CACHE_TTL, name)
        self.assertEqual(server.audit_params("short-unban", {"target": "K7Q2MXPA"}), {"target": "***"})
        self.assertEqual(server.audit_params("short-unban", {"target": "203.0.113.7"}), {"target": "203.0.113.7"})
        self.assertEqual(server.audit_params("short-unban", {"target": "2001:db8::/64"}), {"target": "2001:db8::/64"})
        self.assertEqual(server.audit_params("user-rename", {"target": "x"}), {"target": "x"})
        status, _ = self._post("short-unban", {"target": "K7Q2MXPA"})
        self.assertEqual(status, 200)
        self.assertIn("--target=K7Q2MXPA", self._argv(), "мосту — цель целиком")
        with open(str(server.AUDIT_LOG), encoding="utf-8") as fh:
            audit_text = fh.read()
        rec = json.loads(audit_text.strip().splitlines()[-1])
        self.assertEqual((rec["action"], rec["params"]), ("short-unban", {"target": "***"}))
        for secret in ("K7Q2MXPA", "NV43-9BKX", "NV439BKX"):
            self.assertNotIn(secret, audit_text)

    def test_crashed_bridge_output_with_the_key_stays_out_of_the_journal(self):
        os.environ["FAKE_ADMIN_NO_JSON"] = "1"
        self.addCleanup(os.environ.pop, "FAKE_ADMIN_NO_JSON", None)
        buf = io.StringIO()
        saved = sys.stderr
        sys.stderr = buf
        try:
            r = server.run_action("user-attach-key", {"name": "proba"}, "tester", stdin_data="vpn://LEAKED-IN-A-CRASH")
        finally:
            sys.stderr = saved
        self.assertEqual(r["code"], "transport")
        self.assertIn("user-attach-key", buf.getvalue())
        self.assertNotIn("LEAKED-IN-A-CRASH", buf.getvalue())

    def test_panel_strings_for_refusals_are_paired(self):
        for key in ("config_too_big", "config_not_text", "config_not_string", "stdin_refused"):
            self.assertTrue(server.PANEL_STRINGS["ru"][key] and server.PANEL_STRINGS["en"][key], key)
        self.assertEqual(server.STDIN_MAX_BYTES, 64 * 1024)

    def test_lone_surrogate_is_a_refusal_not_a_crash(self):
        """UI-5: одинокий суррогат из JSON не кодируется в UTF-8 — это кривой ввод (400 config_unreadable),
        а не 500 с трассировкой в журнале; мост не запускается. То же у обычных параметров — bad_arg."""
        calls = self._argv().count("--action=user-attach-key")
        got, payload = self._post("user-attach-key", {"name": "proba", "profile": "awg2", "config": "vpn://A\ud800B"})
        self.assertEqual((got, payload.get("code")), (400, "config_unreadable"))
        self.assertEqual(payload.get("error"), server.panel_strings()["config_not_text"])
        self.assertEqual(self._argv().count("--action=user-attach-key"), calls, "мост не запускался")
        got, payload = self._post("user-rename", {"name": "anna", "label": "A\udc00"})
        self.assertEqual((got, payload.get("code")), (400, "bad_arg"))

    def test_refusals_before_the_bridge_speak_the_panel_language(self):
        """UI-8: два отказа до моста брали только русский текст — теперь из PANEL_STRINGS на языке панели."""
        saved = server.read_language
        server.read_language = lambda: "en"
        try:
            with self.assertRaises(server.ActionError) as ctx:
                server.stdin_payload(123)
            self.assertEqual(str(ctx.exception), server.PANEL_STRINGS["en"]["config_not_string"])
            with self.assertRaises(server.ActionError) as ctx:
                server.run_action("user-rename", {"name": "anna", "label": "A"}, "tester", stdin_data="vpn://X")
            self.assertEqual(str(ctx.exception), "Action user-rename takes no input data.")
        finally:
            server.read_language = saved


class SecretProfileParamAuditTests(unittest.TestCase):
    """KEENETIC-I1-N N3: значение HeaderProtectionKey у profile-set-param ложилось в журнал панели целиком. Теперь в
    журнале «***», мосту — ключ целиком (ядру он нужен). Ключ — тестовый (base64 строки «N3-test-only-…»)."""

    HPK = "TjMtdGVzdC1vbmx5LW5vdC1hLXJlYWwta2V5LTAwMDE="

    def setUp(self):
        server._cache_clear()
        self.tmp = tempfile.mkdtemp(prefix="hpk-", dir=TMP_ROOT)
        self.args_file = os.path.join(self.tmp, "args.txt")
        os.environ["FAKE_ADMIN_ARGS_FILE"] = self.args_file
        self.addCleanup(os.environ.pop, "FAKE_ADMIN_ARGS_FILE", None)

    _argv = ShortLinksServerTests._argv
    _post = ShortLinksServerTests._post

    def test_audit_params_hides_the_key(self):
        hpk = {"profile": "awg3", "key": "HeaderProtectionKey", "value": self.HPK}
        self.assertEqual(server.audit_params("profile-set-param", hpk), dict(hpk, value="***"))
        self.assertEqual(server.audit_params("profile-set-param", dict(hpk, key="headerprotectionkey"))["value"], "***")
        self.assertEqual(server.audit_params("profile-set-param", dict(hpk, value=""))["value"], "",
                         "«убрать параметр» — пусто, а не «***»")
        jc = {"profile": "awg3", "key": "Jc", "value": "7"}
        self.assertEqual(server.audit_params("profile-set-param", jc), jc)
        self.assertEqual(hpk["value"], self.HPK, "вход не меняется — мосту уходит целиком")

    def test_journal_has_no_key_and_bridge_gets_it_whole(self):
        status, _ = self._post("profile-set-param", {"profile": "awg3", "key": "HeaderProtectionKey", "value": self.HPK,
                                                     "confirm": "awg3"})
        self.assertEqual(status, 200)
        self.assertIn("--value=" + self.HPK, self._argv(), "мосту — ключ целиком")
        with open(str(server.AUDIT_LOG), encoding="utf-8") as fh:
            audit_text = fh.read()
        self.assertNotIn(self.HPK[:12], audit_text)
        rec = json.loads(audit_text.strip().splitlines()[-1])
        self.assertEqual((rec["action"], rec["params"].get("key"), rec["params"].get("value")),
                         ("profile-set-param", "HeaderProtectionKey", "***"))


class ShortPendingLocalTests(unittest.TestCase):
    """PERF-02 (живая проверка на 168: мост short-pending — около 1,1 с процессора на 1 vCPU, а вкладка «Короткие ссылки»
    перечитывает ждущие, пока открыта): панель читает их без моста — модулем ядра lib/shortlinks.py в своём процессе, под
    его замком. Ответ — как у моста (python3 lib/shortlinks.py pending), только поля договора: ни кода ссылки, ни верного
    числа, ни ключа адреса. Просроченный запрос модуль закрывает, как мост (событие short_decided — боту). Модуля нет —
    мостом, как раньше."""

    CODE = "NV439BKX"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="short-local-", dir=TMP_ROOT)
        self.gdir = os.path.join(self.tmp, "goamnezia")
        self.state_dir = os.path.join(self.gdir, "subs", ".short")
        os.makedirs(self.state_dir)
        self.run_dir = os.path.join(self.tmp, "run")
        os.makedirs(self.run_dir)
        for k, v in (("GOAMNEZIA_LOCK_DIR", self.run_dir),):
            saved = os.environ.get(k)
            os.environ[k] = v
            self.addCleanup(lambda k=k, saved=saved: os.environ.pop(k) if saved is None else os.environ.__setitem__(k, saved))
        os.environ.pop("GOAMNEZIA_SUBS_DIR", None)
        os.environ.pop("GOAMNEZIA_SHORT_CONFIRM_DIR", None)
        for name, value in (("GOAMNEZIA_DIR", server.Path(self.gdir)),
                            ("SHORT_LINKS_PY", server.Path(ROOT, "lib", "shortlinks.py"))):
            saved = getattr(server, name)
            setattr(server, name, value)
            self.addCleanup(setattr, server, name, saved)
        server._SHORT_LINKS.update(stamp=None, links=None)
        self.addCleanup(server._SHORT_LINKS.update, stamp=None, links=None)
        self.args_file = os.path.join(self.tmp, "args.txt")
        os.environ["FAKE_ADMIN_ARGS_FILE"] = self.args_file
        self.addCleanup(os.environ.pop, "FAKE_ADMIN_ARGS_FILE", None)
        with open(os.path.join(ROOT, "tests", "fixtures", "short_links.json"), encoding="utf-8") as fh:
            fx = json.load(fh)
        spec = json.loads(json.dumps(server.load_actions()))
        spec["actions"].update(fx["catalog"]["actions"])
        saved_load = server.load_actions
        server.load_actions = lambda: spec
        self.addCleanup(setattr, server, "load_actions", saved_load)
        now = int(time.time())
        req = lambda **kw: dict({"kind": "login", "name": "anna", "label": "Анна", "suffix": "all.yaml",  # noqa: E731
                                 "code": self.CODE, "addr": "109.111.12.0", "ip": "109.111.12.34",
                                 "device": "iPhone · Safari", "geo": "Москва, Россия", "who": "", "number": 47,
                                 "options": [47, 12, 85, 33, 61, 90, 28, 74, 19, 56], "created": now - 30,
                                 "expires": now + 270, "status": "pending", "by": "", "decided": 0}, **kw)
        self.state = {"schema": 1, "rev": 3, "graveyard": {}, "addrs": {}, "access": {}, "misses": {}, "alarm_at": 0,
                      "codes": {self.CODE: {"rq": [now - 30]}},
                      "requests": {"rqLive0001": req(),
                                   "rqSwap0002": req(kind="replace", who="bot:424242", ip="", device="", geo="",
                                                     created=now - 10),
                                   "rqOld00003": req(created=now - 400, expires=now - 100),
                                   "rqDone0004": req(status="ok", by="panel")}}
        self._write_state(self.gdir)

    def _write_state(self, gdir):
        path = os.path.join(gdir, "subs", ".short", "state.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.state, fh)

    def _post(self):
        handler = server.AdminHandler.__new__(server.AdminHandler)
        handler.command = "POST"
        handler.headers = {"X-GoAmnezia-Admin": "1", "Content-Length": "2"}
        handler.rfile = io.BytesIO(b"{}")
        handler._who = "tester"
        sent = []
        handler.send_json = lambda payload, status=200, extra_headers=None: sent.append((status, payload))
        server.AdminHandler.route_post_api(handler, urllib.parse.urlparse("/api/action/short-pending"))
        self.assertEqual(len(sent), 1)
        return sent[0]

    def _bridge_calls(self):
        if not os.path.exists(self.args_file):
            return ""
        with open(self.args_file, encoding="utf-8") as fh:
            return fh.read()

    def test_pending_is_read_in_process_without_secrets(self):
        status, payload = self._post()
        self.assertEqual(status, 200)
        self.assertNotIn("short-pending", self._bridge_calls(), "мост не запускался")
        reqs = payload["data"]["requests"]
        self.assertEqual([q["request"] for q in reqs], ["rqLive0001", "rqSwap0002"], "ждущие, по времени; просроченный и решённый — нет")
        login = reqs[0]
        self.assertEqual((login["kind"], login["name"], login["label"], login["suffix"], login["ip"], login["geo"]),
                         ("login", "anna", "Анна", "all.yaml", "109.111.12.34", "Москва, Россия"))
        self.assertEqual(sorted(login["options"]), sorted([47, 12, 85, 33, 61, 90, 28, 74, 19, 56]))
        self.assertGreater(login["left"], 200)
        for q in reqs:
            self.assertLessEqual(set(q), set(server.SHORT_PENDING_FIELDS))
        text = json.dumps(payload, ensure_ascii=False)
        for secret in (self.CODE, "NV43-9BKX", '"number"', '"code"', '"addr"', "109.111.12.0\""):
            self.assertNotIn(secret, text)
        # Просроченный закрыт, как мостом: в состоянии — expired, боту — short_decided
        with open(os.path.join(self.state_dir, "state.json"), encoding="utf-8") as fh:
            st = json.load(fh)
        self.assertEqual(st["requests"]["rqOld00003"]["status"], "expired")
        events = []
        qdir = os.path.join(self.run_dir, "short-confirm")
        for f in sorted(os.listdir(qdir)):
            with open(os.path.join(qdir, f), encoding="utf-8") as fh:
                events.append(json.loads(fh.read()))
        self.assertEqual([(e.get("event"), e.get("request"), e.get("status")) for e in events],
                         [("short_decided", "rqOld00003", "expired")])
        self.assertEqual(os.stat(os.path.join(self.state_dir, "state.json")).st_mode & 0o777, 0o600)

    def test_answer_is_the_same_as_the_bridge_module_gives(self):
        """Тот же ответ, что у python3 lib/shortlinks.py pending (его зовёт мост), на копии того же состояния."""
        status, payload = self._post()
        twin = os.path.join(self.tmp, "twin")
        self._write_state(twin)
        env = dict(os.environ, GOAMNEZIA_DIR=twin, GOAMNEZIA_LOCK_DIR=os.path.join(self.tmp, "run-twin"))
        out = subprocess.run([sys.executable, os.path.join(ROOT, "lib", "shortlinks.py"), "pending"], env=env,
                             capture_output=True, text=True, timeout=60)
        bridge = json.loads(out.stdout)["requests"]
        strip = lambda qs: [{k: v for k, v in q.items() if k != "left"} for q in qs]  # noqa: E731 — секунды идут
        self.assertEqual(strip(payload["data"]["requests"]), strip(bridge))

    def test_without_the_module_the_bridge_answers(self):
        server.SHORT_LINKS_PY = server.Path(self.tmp, "no-such-shortlinks.py")
        status, payload = self._post()
        self.assertEqual(status, 200)
        self.assertIn("--action=short-pending", self._bridge_calls(), "модуля нет — мостом, как раньше")


class StaticVersionTests(unittest.TestCase):
    """PNL-04: адрес app.js и styles.css в странице — версия продукта и начало sha256 файла. После обновления (новый файл
    или новая версия продукта) адрес другой — браузер берёт новую статику, хотя прежнюю держит в кеше год."""

    def setUp(self):
        self.static = tempfile.mkdtemp(prefix="static-", dir=TMP_ROOT)
        for name, text in (("app.js", "console.log('сборка 1');\n" * 100), ("styles.css", "body{color:red}\n" * 100),
                           ("index.html", '<link rel="stylesheet" href="./styles.css?v=0.5.0">'
                                          '<script defer src="./app.js?v=0.5.0"></script>')):
            with open(os.path.join(self.static, name), "w", encoding="utf-8") as fh:
                fh.write(text)
        saved = server.STATIC_DIR
        server.STATIC_DIR = server.Path(self.static)
        self.addCleanup(setattr, server, "STATIC_DIR", saved)
        saved_v = server.detect_version
        self.version = "1.0.10"
        server.detect_version = lambda config=None: self.version
        self.addCleanup(setattr, server, "detect_version", saved_v)

    def page(self):
        with open(os.path.join(self.static, "index.html"), encoding="utf-8") as fh:
            return server.versioned_assets(fh.read())

    def test_new_build_or_version_gets_a_new_address(self):
        import hashlib
        with open(os.path.join(self.static, "app.js"), "rb") as fh:
            sha = hashlib.sha256(fh.read()).hexdigest()[:10]
        v1 = server.asset_version("app.js")
        self.assertEqual(v1, "1.0.10-" + sha)
        self.assertIn('src="./app.js?v=%s"' % v1, self.page())
        self.assertIn('href="./styles.css?v=1.0.10-', self.page())
        # Обновление: новый app.js — новый адрес (отпечаток файла сменился — перечитан)
        path = os.path.join(self.static, "app.js")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("console.log('сборка 2');\n" * 100)
        st = os.stat(path)
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
        v2 = server.asset_version("app.js")
        self.assertNotEqual(v2, v1)
        self.assertIn('src="./app.js?v=%s"' % v2, self.page())
        self.assertNotIn(v1, self.page())
        # Новая версия продукта при том же файле — тоже новый адрес
        self.version = "1.0.11"
        self.assertTrue(server.asset_version("app.js").startswith("1.0.11-"))
        self.assertIn("./styles.css?v=1.0.11-", self.page())

    def test_accept_encoding(self):
        for header, ok in (("gzip", True), ("gzip, deflate, br", True), ("br;q=1.0, gzip;q=0.8", True), ("*", True),
                           ("gzip;q=0", False), ("identity", False), ("", False), (None, False), ("deflate, *;q=0", False),
                           ("gzip;q=x", False)):
            self.assertEqual(server.accepts_gzip(header), ok, header)


class FrontendAttachKeyTests(unittest.TestCase):
    """«Привязать старый ключ» и ключ из приложения Amnezia во фронтенде (текстом, без node).

    Факты: кнопка — только у пира без ключа на сервере и первой, перед перевыпуском; окно — поле для
    ссылки или текста .conf и выбор файла (FileReader, ≤ 64 КБ), ключ уходит одним действием ядра и
    нигде не оседает; переспрос перевыпуска у такого ключа предупреждает; выдача, ответившая
    no_private_key, говорит правду окном; страница подключения оговаривает профиль без ключа.
    """

    STATIC = os.path.join(ROOT, "admin-web", "static")

    @staticmethod
    def _cut(text, start, end):
        b = text[text.index(start):]
        return b[:b.index(end)]

    @classmethod
    def setUpClass(cls):
        cls.app = read_text(os.path.join(cls.STATIC, "app.js"))
        cls.css = read_text(os.path.join(cls.STATIC, "styles.css"))
        cls.pane = cls._cut(cls.app, "function renderPeerPane(", "async function fetchKey(")
        cls.attach = cls._cut(cls.app, "function openAttachKey(user, pid, reload) {", "// ── Страница подключения")
        cls.share = cls._cut(cls.app, "async function openShare(", "// ── Протоколы")

    def dict_body(self, lang):
        head = "    %s: {" % lang
        start = self.app.index(head) + len(head)
        return self.app[start:self.app.index("\n    },", start)]

    def entry(self, lang, key):
        m = re.search(r"\b%s: \"([^\"]*)\"" % key, self.dict_body(lang))
        self.assertIsNotNone(m, "%s/%s" % (lang, key))
        return m.group(1)

    def test_both_dictionaries_carry_the_strings(self):
        keys = ("attachKey", "attachKeyPrompt", "attachKeyField", "attachKeyFile", "attachKeyPlaceholder",
                "attachKeyGo", "attachKeyChecking", "attachKeyEmpty", "attachKeyTooBig", "attachKeyReadFailed",
                "attachKeyDone", "reissueImportedWarn", "importedNoteMany", "importedTitle", "pageImportedNote",
                "act_user_attach_key")
        for lang in ("ru", "en"):
            for key in keys:
                self.assertTrue(self.entry(lang, key), "%s/%s" % (lang, key))
        self.assertEqual(self.entry("ru", "attachKey"), "Привязать старый ключ")
        self.assertEqual(self.entry("en", "attachKey"), "Attach the old key")
        # каноничные слова договора
        self.assertIn("Проверю, что ключ именно его и этого сервера. У пользователя ничего не изменится.",
                      self.entry("ru", "attachKeyPrompt"))
        self.assertIn("после перевыпуска старый ключ у пользователя сразу перестанет подключаться",
                      self.entry("ru", "reissueImportedWarn"))
        self.assertIn("лучше «Привязать старый ключ» — тогда у пользователя ничего не сломается",
                      self.entry("ru", "reissueImportedWarn"))
        self.assertIn("“Attach the old key” is better — nothing breaks for the user", self.entry("en", "reissueImportedWarn"))
        for key in ("{p}",):
            self.assertIn(key, self.entry("ru", "attachKeyPrompt"))
            self.assertIn(key, self.entry("en", "attachKeyPrompt"))
        for key in ("attachKeyPrompt", "reissueImportedWarn", "pageImportedNote", "importHint", "importAdoptText"):
            self.assertNotIn("приватн", self.entry("ru", key), key)

    def test_button_only_for_a_key_from_the_app_and_first(self):
        """«Привязать старый ключ» — только у пира без ключа на сервере, главной кнопкой вкладки; перевыпуск — ниже,
        в полосе «Необратимое» (вкладка профиля рисуется раньше неё)."""
        imported = self._cut(self.pane, "if (!hasKey) {", "return { tiles, blocks, qr: null };")
        self.assertIn('U.btn(t("attachKey"), "primary", () => openAttachKey(user, pid, reload), { icon: "key", md: true, full: true })', imported)
        self.assertEqual(self.app.count('U.btn(t("attachKey")'), 1)
        body = self._cut(self.app, "function drawCardBody(user, pid) {", "const installedProfiles")
        self.assertLess(body.index("renderPeerPane(user, pid, peer)"), body.index('tf("ucReissueN"'))
        peer_pane = self._cut(self.app, "function renderPeerPane(", "// ── Привязать старый ключ")
        self.assertNotIn('"dsoft"', peer_pane)

    def test_card_redraw_keeps_the_open_profile_tab(self):
        """UI-6: после привязки ключа, окна no_private_key и «Отмены» карточка перерисовывается на той же
        вкладке профиля, а не на первой — вкладка запоминается при каждом показе."""
        card = self._cut(self.app, "function renderUserCard(user) {", "function cardUsage(user) {")
        self.assertIn("const cardTab = { name: \"\", pid: \"\" };", self.app)
        self.assertIn("cardTab.pid = pid;", card)
        self.assertIn("show(cardTab.name === name && pids.includes(cardTab.pid) ? cardTab.pid : pids[0]);", card)
        opener = self._cut(self.app, "async function openUserCard(name, keepTab = false) {", "function renderUserCard(user) {")
        self.assertIn('cardTab.pid = "";', opener)     # новая карточка — с первой вкладки

    def test_pasted_text_over_the_limit_is_not_called_a_file(self):
        """UI-7: вставленный текст длиннее 64 КБ — своя строка, а не «выберите файл .conf»."""
        self.assertIn('fail(t("attachKeyTextTooBig"))', self.attach)
        self.assertIn('fail(t("attachKeyTooBig"))', self.attach)          # файл — по-прежнему про файл
        for lang in ("ru", "en"):
            self.assertTrue(self.entry(lang, "attachKeyTextTooBig"))
        self.assertTrue(self.entry("ru", "attachKeyTextTooBig").startswith("Текст больше 64 КБ"))
        self.assertTrue(self.entry("en", "attachKeyTextTooBig").startswith("The text is larger than 64 KB"))

    def test_pages_stay_visible_without_a_key(self):
        """UI-4 (панель): список страниц — при любых ключах (у выключенного человека и у того, у кого
        остались только ключи из приложения, страницы живут, и отозвать их больше негде); «Создать» — только
        с ключом, а пустой список без ключей не рисуется."""
        card = self._cut(self.app, "function drawCardBody(user, pid) {", "const installedProfiles")
        self.assertIn("const hasKeys = Object.values(peers).some((p) => p && p.has_key !== false);", card)
        self.assertIn("if (pagesAvailable()) {", card)
        self.assertIn("loadUserPages(user, pagesBox, { quietWhenEmpty: !hasKeys })", card)
        # «Создать» — в шапке списка, только когда модуль страниц есть (кнопка «Страница подключения» — во вкладке).
        self.assertIn('if (d.available !== false) head.append(U.btn(t("ucCreate")', self.app)
        loader = self._cut(self.app, "async function loadUserPages(user, box, opts = {}) {", "function renderUserPages(")
        self.assertIn("if (quiet && (d.available === false || !(d.pages || []).length)) { box.innerHTML = \"\"; box.hidden = true; return; }", loader)

    def test_dialog_reads_the_file_here_and_sends_one_action(self):
        a = self.attach
        self.assertIn("const ATTACH_MAX_BYTES = 64 * 1024;", self.app)
        self.assertIn("f.size > ATTACH_MAX_BYTES", a)
        self.assertIn("new FileReader()", a)
        self.assertIn("reader.readAsText(f)", a)
        self.assertIn('U.input({ tag: "textarea"', a)
        self.assertIn('type: "file"', a)
        self.assertIn('action("user-attach-key", { name, profile: pid, config })', a)
        # удача и «уже на сервере» — карточка заново, где у профиля уже ряд выдачи
        self.assertIn('err.code === "already_has_key"', a)
        self.assertIn("await reload();", a)
        # ключ нигде не оседает: ни в хранилище браузера, ни в кэше вкладки, ни в консоли
        for leak in ("localStorage", "sessionStorage", "keyCache", "console."):
            self.assertNotIn(leak, a, leak)
        self.assertIn('area.value = "";', a)
        # ошибка — словами ядра в самом окне: поправить и прислать снова
        self.assertIn("fail((err && err.message) || String(err));", a)
        self.assertEqual(server.STDIN_MAX_BYTES, 64 * 1024, "предел панели и окна — один")

    def test_reissue_of_a_key_from_the_app_warns_first(self):
        reissue = self._cut(self.app, "async function reissuePeer(user, pid) {", "async function removePeer(")
        self.assertIn('warn: hasKey ? "" : t("reissueImportedWarn"),', reissue)
        confirm = self._cut(self.app, "function typedConfirm({", "async function copyText(value, o = {}) {")
        self.assertIn('warn ? U.al("warning", null, warn) : null,', confirm)

    def test_issue_errors_explain_the_app_key(self):
        for fn in ("const showQr = async", "const copyVpn = async", "const downloadConf = async"):
            block = self._cut(self.pane, fn, "\n    };")
            self.assertIn("keyError(err)", block, fn)
        self.assertIn('title: t("importedTitle")', self.pane)
        self.assertIn('err.code === "no_private_key" ? (err.message || tf("importedNote", { p: prof }))', self.share)
        # A3-5: выключенному — слова окна «Пользователь выключен», остальное — слова ядра
        self.assertIn(': (err.code === "user_disabled" ? tf("udShareDisabledD", { n: userName(user) }) : err.message)', self.share)

    def test_connection_page_names_the_profile_without_a_key(self):
        create = self._cut(self.app, "async function openPageCreate(user) {", "// ── Новый пользователь (шторка)")
        self.assertIn("...pageImportedNotes(importedOf(user)),", create)
        url = self._cut(self.app, "function showPageUrl(r, user, dlg) {", "// Создание: срок")
        self.assertIn("Array.isArray(r.imported_profiles)", url)
        self.assertIn("blocks.push(...pageImportedNotes(fromApp));", url)
        pages = self._cut(self.app, "function renderUserPages(user, box, d) {", "// Окно с адресом")
        self.assertIn("pageImportedNotes(importedOf(user, onPages))", pages)

    def test_the_key_field_is_not_zoomed_on_phones(self):
        """Поле ключа — своим классом без размера шрифта: .mono (12.5px) перебил бы 16px телефона."""
        rule = self.css[self.css.index(".key-material {"):]
        rule = rule[:rule.index("}")]
        self.assertIn("monospace", rule)
        self.assertNotIn("font-size", rule)
        self.assertIn('area.classList.add("key-material");', self.attach)
        # Поле ключа — обычное поле панели: на телефоне ему достаётся 16 px общим правилом.
        self.assertIn('[data-hero][data-device="phone"] input, [data-hero][data-device="phone"] textarea', self.css)


class FrontendLiveReviewTests(unittest.TestCase):
    """Разбор живой панели на сервере A (25.09.2026, агент panel-ui): то, что было видно глазами.

    Проверка текстовая, как у соседних Frontend*Tests: факты, которые ломались, — горизонтальная
    прокрутка Обзора на телефоне, невидимые полоски бюджета, тема, приклеенная к системе первого
    визита, окно «Срок» с нулём, служебные имена вместо слов.
    """

    STATIC = os.path.join(ROOT, "admin-web", "static")

    @classmethod
    def setUpClass(cls):
        cls.app = read_text(os.path.join(cls.STATIC, "app.js"))
        cls.html = read_text(os.path.join(cls.STATIC, "index.html"))
        cls.css = read_text(os.path.join(cls.STATIC, "styles.css"))

    def _cut(self, start, end):
        b = self.app[self.app.index(start):]
        return b[:b.index(end)]

    def dict_body(self, lang):
        head = "    %s: {" % lang
        start = self.app.index(head) + len(head)
        return self.app[start:self.app.index("\n    },", start)]

    def test_address_table_scrolls_inside_its_card(self):
        """Таблица адресов не распирает Обзор: сеткой, пока помещается в карточку, уже — строками-карточками
        (решает ширина карточки — контейнерный запрос), а не прокруткой всей страницы вбок."""
        edge = self._cut("function overviewEdge(o) {", "function overviewProfiles(o) {")
        self.assertIn('el("div", { class: `gx-edgewrap gx-edgewrap--${minW}` }, table, ptable)', edge)
        self.assertIn(".gx-edgewrap { container-type: inline-size; min-width: 0; }", self.css)
        self.assertIn("@container (max-width: 639.98px) { .gx-edgewrap--640px > .gx-edge { display: none; }", self.css)
        self.assertIn(".gx-edge { display: grid; min-width: 0; overflow-x: auto; }", self.css)

    def test_budget_bar_is_a_block_with_width(self):
        # 0.5.0: место под логи рисуется общим «метром» (U.meters): дорожка — блок с высотой, заливка — по ширине.
        bar = self.css[self.css.index(".gx-meter__bar {"):]
        bar = bar[:bar.index("}")]
        self.assertIn("height: 6px;", bar)
        fill = self.css[self.css.index(".gx-meter__bar > div {"):]
        fill = fill[:fill.index("}")]
        self.assertIn("height: 100%;", fill)
        self.assertIn("fill.style.width = `${pct}%`;", self.app)

    def test_theme_follows_the_system_until_chosen(self):
        """Пока вид не выбран, тема — «Система» и следует устройству; кнопка темы в шапке — явный выбор."""
        self.assertIn('const LOOK_DEFAULTS = { theme: "system",', self.app)
        self.assertIn('matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { if (look.theme === "system")',
                      self.app)
        self.assertIn('onclick: () => setLook({ theme: dark ? "light" : "dark" }) }', self.app)
        self.assertIn('var theme = pick(ap.theme, ["light", "dark", "system"], "system");', self.html)

    def test_expiry_dialog_starts_from_the_days_left(self):
        """В поле — сколько дней осталось, а не 0 («бессрочно»); срок вышел или его нет — поле пустое."""
        expiry = self._cut("function openExpiry(user) {", "function openRename(user) {")
        expiry = expiry[:expiry.index('runAction("user-expiry"')]
        self.assertNotIn('value: "0"', expiry)
        self.assertIn('const cur = !user.expires ? "" : (Number.isFinite(left) && left > 0 ? String(left) : "");', expiry)
        self.assertIn("let val = cur;", expiry)
        self.assertIn("U.input({ value: val,", expiry)
        self.assertIn('title: tf("udExpT", { n: userName(user) })', expiry)

    def test_tunnel_command_never_uses_the_browser_port(self):
        self.assertNotIn("location.port", self.app)
        self.assertIn('const panelPort = () => String(((state.settings || {}).bind || "127.0.0.1:1985").split(":").pop() || "1985");',
                      self.app)
        self.assertIn("const tunnelCmd = () => { const port = panelPort(); return `ssh -L ${port}:127.0.0.1:${port} root@${tunnelTarget().host}`; };",
                      self.app)

    def test_create_buttons_hold_until_the_answer(self):
        """PUI-29: «Создать» гаснет до ответа ядра — второе нажатие не уходит вторым запросом. То же у «Выдать
        доступ» агенту: второй agent-add выдавал второй доступ, и окно его адреса закрывало окно первого."""
        add = self._cut("async function openAddUser() {", "// ── Протоколы")
        self.assertIn("if (createBtn.disabled) return;", add)          # и Enter в поле, мимо кнопки
        self.assertLess(add.index("if (createBtn.disabled) return;"), add.index("createBtn.disabled = true;"))
        self.assertLess(add.index("createBtn.disabled = true;"), add.index("await runAction("))
        self.assertIn('} finally { createBtn.disabled = false; createBtn.textContent = t("unCreate"); }', add)
        self.assertIn("createBtn]);", add)
        # «Выдать доступ» (0.5.0): окно formDialog гасит кнопку до ответа ядра и молча игнорирует повторное нажатие.
        block = self._cut("function openAddAgent() {", "// ── Действия над доступом ──")
        self.assertIn('busyLabel: t("agGranting")', block)
        self.assertIn('return (await action("agent-add", params)) || true;', block)
        form = self._cut("function formDialog(o) {", "function quickChips(")
        self.assertIn("if (ok.disabled) return;", form)
        self.assertLess(form.index("ok.disabled = true;"), form.index("await o.onOk(d)"))

    def test_share_cancel_returns_to_the_same_profile_tab(self):
        """UI-6: окно временной ссылки — поверх карточки, «Отмена» просто закрывает окно, и карточка остаётся на той
        же вкладке профиля; новая карточка (из списка) по-прежнему открывается с первой вкладки."""
        share = self._cut("async function openShare(", "// ── Страница подключения")
        self.assertNotIn("renderUserCard(", share)
        self.assertNotIn("cardTab.pid", share)
        opener = self._cut("async function openUserCard(name, keepTab = false) {", "function renderUserCard(user) {")
        self.assertIn('if (!keepTab || cardTab.name !== name) cardTab.pid = "";', opener)
        self.assertIn("const open = () => openUserCard(u.name);", self.app)      # из списка — без keepTab

    def test_services_and_actions_are_named_in_words(self):
        for key in ("svcMissing", "svcUnconfigured", "svcBot", "svcPanel", "act_user_add", "act_change_mode",
                    "logSrcAudit", "whoTunnel", "siteVerdict_ok", "diagTimersOk", "templatePickHint", "byteUnits"):
            for lang in ("ru", "en"):
                self.assertIn("%s:" % key, self.dict_body(lang), "%s/%s" % (lang, key))
        self.assertIn('tf("busyTitle", { a: actionTitle(progressName) })', self.app)
        self.assertIn('tf("errLockBusy", { a: actionTitle(busy) })', self.app)
        self.assertIn('"Б КБ МБ ГБ ТБ"', self.dict_body("ru"))

    def test_destructive_button_stays_last_in_the_user_card(self):
        """Разрушительное — последним: полоса «Необратимое» — последний блок карточки, «Удалить» — последним в ней."""
        body = self._cut("function drawCardBody(user, pid) {", "const installedProfiles")
        tail = body[body.rindex("body.append("):]
        self.assertTrue(tail.startswith("body.append(danger);"), tail[:60])
        self.assertLess(body.index('tf("ucRemovePeerN"'), body.index('danger.append(U.btn(t("ucDelete"), "dsoft"'))

    def test_profiles_are_listed_in_product_order(self):
        self.assertIn('const PROFILE_ORDER = ["awg3", "awg2", "awg1"];', self.app)
        self.assertIn("Object.keys(peers).sort(byProfileOrder)", self.app)

    def test_panel_on_domain_buttons_follow_the_state(self):
        """0.5.0: «Вход в панель → Панель на домене» — пять состояний; «Открыть» — только когда её можно включить,
        «Сменить путь» и «Закрыть» — только у работающей; негде (нет сайта, домена, настоящего сертификата) —
        словами, что сделать, а записанный путь можно стереть."""
        st = self._cut("function sgPanelState() {", "function sgPanelSec() {")
        self.assertIn('if (s.panel_url) return "on";', st)
        self.assertIn('return { no_site: "nosite", no_domain: "nodomain", untrusted_cert: "untrusted" }[m.reason] || "nosite";', st)
        self.assertIn('if (!sgSiteMode()) return "nosite";', st)
        sec = self._cut("function sgPanelSec() {", "async function sgPanelOpen() {")
        on = sec[sec.index('if (ps === "on") {'):sec.index('} else if (ps === "off") {')]
        self.assertIn("sgPanelRotate()", on)
        self.assertIn("sgPanelClose()", on)
        self.assertNotIn("sgPanelOpen()", on)
        off = sec[sec.index('} else if (ps === "off") {'):sec.index('} else if (ps === "nosite") {')]
        self.assertIn("sgPanelOpen()", off)
        self.assertNotIn("sgPanelRotate()", off)
        self.assertNotIn("sgPanelOpen()", sec[sec.index('} else if (ps === "nosite") {'):])
        # «Бот ждёт первого админа» — по состоянию службы из ответа «Настроек», а не только с Обзора (PUI-11).
        self.assertIn('const botState = s.bot_state || ((state.overview && state.overview.services) || {})["goamnezia-bot"];',
                      self._cut("function sgHowSec() {", "function sgPwdSec() {"))

    def test_checks_answer_in_words_not_raw_json(self):
        # 0.5.0: «Проверка сайта снаружи» — вердикт словами, строки HTTPS/HTTP/сертификат/A-записи, сырой ответ —
        # под «Подробностями»; диагностика — строками с отметкой («Логи → Диагностика»).
        site = self._cut("function sgCheckBlocks(r) {", "function sgCheckSec() {")
        self.assertIn('U.al("success", t("sgScOkB")', site)
        self.assertIn('out.push(U.kv(rows), U.details(t("sgScDet"), JSON.stringify(s, null, 2)));', site)
        self.assertNotIn("box.textContent = JSON.stringify", self.app)

    def test_phone_layout_rules(self):
        """Телефон: кнопки переносят подпись и не ниже 44 px, пары «ключ — значение» — в две строки, у кнопок
        шапки-значков есть подписи для чтения с экрана."""
        phone = self.css[self.css.index("7. Телефон"):]
        self.assertIn('[data-hero][data-device="phone"] .ga-btn { white-space: normal; height: auto; min-height: var(--btn-h);', phone)
        self.assertIn('.gx-kv--stack .gx-kv__row, [data-device="phone"] .gx-kv__row { grid-template-columns: minmax(0, 1fr) auto; }',
                      self.css)
        self.assertIn('"aria-label": t("shSearchAria"), html: ic("magnifier", 18)', self.app)
        self.assertIn('id: "refreshBtn", "aria-label": t("refresh")', self.app)


class FrontendPhoneLayoutTests(unittest.TestCase):
    """Телефон и мини-апп (25.09.2026, замечание владельца «кнопки на разной ширине»).

    Кнопки рядов — сеткой из двух равных колонок, одинокая последняя — на весь ряд; таблицы с шапкой —
    строками-карточками с подписями из шапки; «1 пир / 3 пира / 5 пиров»; цвета шапки Telegram — по теме.
    """

    STATIC = os.path.join(ROOT, "admin-web", "static")

    @classmethod
    def setUpClass(cls):
        cls.app = read_text(os.path.join(cls.STATIC, "app.js"))
        cls.html = read_text(os.path.join(cls.STATIC, "index.html"))
        css = read_text(os.path.join(cls.STATIC, "styles.css"))
        cls.phone = css[css.index("7. Телефон"):]
        cls.css = css

    def test_button_rows_are_equal_columns(self):
        """0.5.0: ряды кнопок — как в макете, переносом (flex-wrap); на телефоне подпись кнопки переносится,
        а не обрезается, и цель не ниже 44 px."""
        row = self.css[self.css.index(".gx-btns__row {"):]
        self.assertIn("flex-wrap: wrap;", row[:row.index("}")])
        self.assertIn('[data-hero][data-device="phone"] .ga-btn { white-space: normal; height: auto; min-height: var(--btn-h);',
                      self.phone)
        self.assertIn('[data-hero][data-device="phone"] .ga-btn--sm { min-height: var(--btn-h-sm); }', self.phone)
        self.assertIn('[data-hero][data-device="phone"]     { --pad: 16px; --gap: 14px; --card-p: 16px; --btn-h: 44px; --btn-h-sm: 44px; }',
                      self.css)

    def test_tables_stack_into_labelled_rows(self):
        """Таблицы на телефоне — строками «подпись … значение» с подписями из шапки (U.table), таблица адресов —
        так же, когда карточка уже её сетки."""
        table = self.app[self.app.index("    table(head, rows, o = {}) {"):]
        table = table[:table.index("\n    },\n")]
        self.assertIn("if (isPhone() && !o.keepGrid) {", table)
        self.assertIn('if (j !== 0 && !btnsOnly && H[j].t) cell.append(el("span", { class: "gx-ptbl__k", text: H[j].t }));', table)
        self.assertIn(".gx-ptbl__k {", self.css)
        self.assertIn(".gx-edgewrap > .gx-ptbl { display: none; }", self.css)

    def test_peers_count_has_plural_forms(self):
        # «Ключей», как «Ключей выдано» в SSH и боте, — не «пиров» (T16 агента texts).
        self.assertIn('peers: "ключ|ключа|ключей"', self.app)
        self.assertIn('peers: "key|keys"', self.app)
        self.assertIn("const tn = (key, n) => {", self.app)
        # «9 человек», «3 человека», «1 person» — число внутри строки словаря.
        self.assertIn("const tp = (key, n, vars) => {", self.app)
        self.assertIn('peopleN: "{n} пользователь|{n} пользователя|{n} пользователей"', self.app)
        self.assertIn('tp("peopleN", Number(p.peers) || 0)', self.app)
        self.assertNotIn('t("peers")', self.app)

    def test_error_toast_is_readable_in_both_themes(self):
        """Тост — на подложке слоя (var(--overlay)) в обеих темах; ошибка — значком на мягкой красной подложке."""
        toast = self.css[self.css.index(".gx-toast {"):]
        self.assertIn("background: var(--overlay);", toast[:toast.index("}")])
        self.assertIn(".gx-toast__ic--danger { background: var(--danger-soft); color: var(--danger-soft-foreground); }", self.css)
        self.assertIn('kind === "danger" ? 6000 : 3200', self.app)

    def test_miniapp_follows_telegram_theme_and_colors(self):
        """Мини-апп: цвета из themeParams (адрес #tgWebAppThemeParams и событие theme_changed), тема «Telegram» по
        умолчанию на этом устройстве, шапка и фон окна Telegram — цветом панели."""
        self.assertIn('kv[0] === "tgWebAppThemeParams"', self.html)
        self.assertIn('["light", "dark", "system", "telegram"], "telegram")', self.html)
        self.assertIn("function tgSyncColors() {", self.app)
        self.assertIn('tgPostEvent("web_app_set_header_color", { color });', self.app)
        self.assertIn('if (eventType !== "theme_changed") return;', self.app)
        self.assertIn('tgPostEvent("web_app_request_theme");', self.app)
        setter = self.app[self.app.index("function applyLook() {"):]
        self.assertIn("tgSyncColors();", setter[:setter.index("\n  }\n")])

    def test_address_table_marks_a_record_like_ssh(self):
        for lang, word in (("ru", 'edgeDns: "A-запись"'), ("en", 'edgeDns: "A record"')):
            self.assertIn(word, self.app, lang)
        self.assertIn('edgeDnsYes: "✓", edgeDnsNo: "✗"', self.app)



class KeyEndpointServerTests(unittest.TestCase):
    """«Ключ на другой адрес сервера» на стороне сервера панели: форма --endpoint, коды отказов, аудит.

    Разрешённый список хостов знает только мост (address_not_here); панель проверяет форму, чтобы мусор
    не запускал bash, и кладёт параметр в argv только непустым. Определение типа key_endpoint из
    lib/actions.json главнее запасного — запасное держит панель на время, пока ядро его не объявило.
    """

    def setUp(self):
        server._cache_clear()

    def test_endpoint_is_accepted_by_user_key_and_share_create_only(self):
        for action in ("user-key", "share-create"):
            out = server.validate_params(action, {"name": "anna", "profile": "awg2", "endpoint": "198.51.100.20"})
            self.assertEqual(out["endpoint"], "198.51.100.20", action)
            out = server.validate_params(action, {"name": "anna", "profile": "awg2", "endpoint": "vpn.example.com"})
            self.assertEqual(out["endpoint"], "vpn.example.com", action)
            # Нет параметра — нет и аргумента: вызов ядра байт в байт прежний.
            self.assertNotIn("endpoint", server.validate_params(action, {"name": "anna", "profile": "awg2"}))
        argv = server.build_argv("user-key", server.validate_params(
            "user-key", {"name": "anna", "profile": "awg2", "format": "all", "endpoint": "198.51.100.20"}))
        self.assertIn("--endpoint=198.51.100.20", argv)
        # Чужим действиям параметр не положен — «лишний параметр», как раньше.
        with self.assertRaises(server.ActionError) as ctx:
            server.validate_params("user-show", {"name": "anna", "endpoint": "198.51.100.20"})
        self.assertEqual(ctx.exception.code, "bad_arg")

    def test_garbage_endpoint_is_refused_before_the_bridge(self):
        for bad in ("1.2.3.4;reboot", "a b", '"x"', "host/../x", "http://x", "имя.рф", "x" * 254, "a\nb"):
            with self.assertRaises(server.ActionError, msg=bad) as ctx:
                server.validate_params("user-key", {"name": "anna", "profile": "awg2", "endpoint": bad})
            self.assertEqual(ctx.exception.code, "bad_arg", bad)
            self.assertEqual(ctx.exception.status, 400, bad)

    def _spec_call(self, mutate, params):
        """validate_params на копии каталога ядра, изменённой mutate: так тест не зависит от того,
        объявило ли ядро key_endpoint к моменту запуска."""
        spec = json.loads(json.dumps(server.load_actions()))
        mutate(spec)
        saved = server.load_actions
        server.load_actions = lambda: spec
        try:
            return server.validate_params("user-key", dict({"name": "anna", "profile": "awg2"}, **params))
        finally:
            server.load_actions = saved

    def test_the_core_definition_wins_over_the_fallback(self):
        def strict(spec):
            spec["actions"]["user-key"]["params"]["endpoint"] = {"ref": "key_endpoint", "required": False}
            # Строже запасной формы (только строчные) — значит, работает именно определение ядра.
            spec["param_types"]["key_endpoint"] = {"type": "string", "pattern": "^[a-z0-9.-]{1,253}$"}

        def old_core(spec):
            # Каталог ядра постарше: ни параметра, ни типа.
            for action in ("user-key", "share-create"):
                spec["actions"][action]["params"].pop("endpoint", None)
            spec["param_types"].pop("key_endpoint", None)

        def param_without_type(spec):
            spec["actions"]["user-key"]["params"]["endpoint"] = {"ref": "key_endpoint", "required": False}
            spec["param_types"].pop("key_endpoint", None)

        self.assertEqual(self._spec_call(strict, {"endpoint": "vpn.example.com"})["endpoint"], "vpn.example.com")
        with self.assertRaises(server.ActionError):
            self._spec_call(strict, {"endpoint": "VPN.EXAMPLE.COM"})
        # Старое ядро: запасная форма пропускает хост (регистр — не её забота) и отбивает мусор.
        for mutate in (old_core, param_without_type):
            self.assertEqual(self._spec_call(mutate, {"endpoint": "VPN.EXAMPLE.COM"})["endpoint"], "VPN.EXAMPLE.COM")
            self.assertEqual(self._spec_call(mutate, {"endpoint": "198.51.100.20"})["endpoint"], "198.51.100.20")
            for bad in ("1.2.3.4;reboot", "a b", "x" * 254):
                with self.assertRaises(server.ActionError, msg=bad):
                    self._spec_call(mutate, {"endpoint": bad})

    def test_refusals_of_the_core_map_to_4xx(self):
        for code, status in (("invalid_ip", 400), ("address_not_here", 400), ("addresses_unknown", 409),
                             ("address_gone", 409)):
            self.assertEqual(server.bridge_http_status({"status": "error", "code": code}), status, code)

    def test_audit_keeps_the_address_and_never_the_key(self):
        r = server.run_action("user-key", {"name": "anna", "profile": "awg2", "format": "all",
                                           "endpoint": "198.51.100.20"}, "tester")
        self.assertTrue(server.bridge_ok(r))
        self.assertEqual(r["key_host"], "198.51.100.20")
        self.assertIs(r["key_host_override"], True)
        self.assertIn("Endpoint = 198.51.100.20:443", r["conf"])
        with open(str(server.AUDIT_LOG), encoding="utf-8") as fh:
            rec = json.loads(fh.read().strip().splitlines()[-1])
        self.assertEqual(rec["action"], "user-key")
        self.assertEqual(rec["params"]["endpoint"], "198.51.100.20")
        blob = json.dumps(rec, ensure_ascii=False)
        self.assertNotIn("FAKE-PRIVATE-KEY", blob)
        self.assertNotIn("PrivateKey", blob)


class KeyChoicesPayloadTests(unittest.TestCase):
    """Карточка пользователя несёт адрес «как обычно» (user-show) и варианты разового адреса (edge-status)."""

    def setUp(self):
        server._cache_clear()

    def test_card_carries_key_host_and_choices_from_the_bridge(self):
        status, payload = server.user_card_payload("tester", "anna")
        self.assertEqual(status, 200)
        user = payload["data"]["user"]
        self.assertEqual(user["key_host"], "203.0.113.10")
        self.assertIs(user["key_host_gone"], False)
        self.assertEqual(user["keys_host"], "203.0.113.10")
        hosts = [c["host"] for c in user["key_choices"]]
        self.assertEqual(hosts, ["203.0.113.10", "vpn.example.com", "198.51.100.20", "198.51.100.30"])
        first, domain, dark = user["key_choices"][0], user["key_choices"][1], user["key_choices"][3]
        self.assertIs(first["default"], True)
        self.assertEqual((domain["kind"], domain["verdict"], domain["users"]), ("domain", None, None))
        self.assertEqual((dark["kind"], dark["verdict"], dark["users"]), ("ip", "dark", 1))

    def test_edge_status_is_read_through_the_half_minute_cache(self):
        count = os.path.join(TMP_ROOT, "count-%d.txt" % id(self))
        os.environ["FAKE_ADMIN_COUNT_FILE"] = count
        try:
            server.user_card_payload("tester", "anna")
            server.user_card_payload("tester", "anna")
        finally:
            os.environ.pop("FAKE_ADMIN_COUNT_FILE", None)
        with open(count, encoding="utf-8") as fh:
            calls = fh.read().splitlines()
        self.assertEqual(calls.count("edge-status"), 1)
        self.assertEqual(calls.count("user-show"), 2)

    def test_choices_are_whitelisted(self):
        edge = {"status": "success", "key_choices": [
            "junk", {"host": 'evil"host'}, {"host": "a b"}, {"host": ""},
            {"host": "203.0.113.10", "kind": "ip", "default": True, "verdict": "ok", "users": 3, "in_dns": True,
             "secret": "must-not-pass"},
            {"host": "203.0.113.10", "kind": "ip"},  # повтор
            {"host": "vpn.example.com", "kind": "domain", "verdict": "banana", "users": True, "in_dns": "yes"},
        ]}
        got = server.key_choices_payload(edge)
        self.assertEqual([c["host"] for c in got], ["203.0.113.10", "vpn.example.com"])
        self.assertEqual(got[0], {"host": "203.0.113.10", "kind": "ip", "default": True, "verdict": "ok",
                                  "users": 3, "in_dns": True})
        self.assertEqual((got[1]["verdict"], got[1]["users"], got[1]["in_dns"], got[1]["default"]),
                         (None, None, None, False))
        self.assertEqual(server.key_choices_payload({"status": "error", "code": "transport"}), [])
        self.assertEqual(server.key_choices_payload({"status": "success"}), [])

    def test_fields_inside_user_and_a_broken_edge_status(self):
        """Ядро может положить key_host в user{}; сбой edge-status карточку не роняет — вариантов просто нет."""
        def fake(action, params=None, who="panel", **kw):
            if action == "user-show":
                return {"status": "success", "user": {"name": "anna", "peers": {},
                                                      "key_host": "198.51.100.99", "key_host_gone": True}}
            if action == "edge-status":
                raise server.ActionError("unknown_action", "нет такого действия", 404)
            return {"status": "success"}
        saved = server.run_action
        server.run_action = fake
        try:
            status, payload = server.user_card_payload("tester", "anna")
        finally:
            server.run_action = saved
        self.assertEqual(status, 200)
        user = payload["data"]["user"]
        self.assertEqual(user["key_host"], "198.51.100.99")
        self.assertIs(user["key_host_gone"], True)
        self.assertEqual(user["key_choices"], [])
        self.assertEqual(user["keys_host"], "")


class OverviewEdgePayloadTests(unittest.TestCase):
    """Обзор: A-записи домена из ключей доходят до панели (критик 24.09, п.1), адрес в команде туннеля — тот,
    на который реально приходят (PUI-25), состояние бота — в ответе «Настроек» (PUI-11)."""

    STATUS = {"status": "success", "mode": "pro", "server_ip": "203.0.113.207", "users": "2", "profiles": []}
    MULTI = {"status": "success", "count": 3, "domain_endpoint": True,
             "dns": {"domain": "vpn.example.com", "state": "partial", "a": ["198.51.100.50", "192.0.2.9"],
                     "foreign": ["192.0.2.9"], "cloudflare": False, "junk": "x" * 10},
             "ips": [{"ip": "203.0.113.207", "verdict": "unused", "users": 0},
                     {"ip": "198.51.100.50", "verdict": "ok", "users": 2, "in_dns": True},
                     {"ip": "198.51.100.84", "verdict": "ok", "users": 1, "in_dns": False}]}
    SINGLE = {"status": "success", "count": 1, "domain_endpoint": True,
              "dns": {"domain": "vpn.example.com", "state": "elsewhere", "a": ["192.0.2.9"], "foreign": ["192.0.2.9"]},
              "ips": [{"ip": "203.0.113.207", "verdict": "ok", "users": 2}]}

    def _overview(self, edge):
        def fake(action, params=None, who="panel", **kw):
            return {"edge-status": edge, "status": self.STATUS}.get(action, {"status": "success", "users": []})
        saved = server.run_action
        server.run_action = fake
        try:
            return server.overview_payload("tester")
        finally:
            server.run_action = saved

    def test_dns_reaches_the_overview_whitelisted(self):
        o = self._overview(self.MULTI)
        self.assertEqual(o["edge"]["dns"], {"domain": "vpn.example.com", "state": "partial",
                                            "a": ["198.51.100.50", "192.0.2.9"], "foreign": ["192.0.2.9"],
                                            "cloudflare": False})
        self.assertEqual(len(o["edge"]["ips"]), 3)

    def test_single_address_carries_its_row_and_the_warning(self):
        """Адрес один: беда с A-записями доходит до Обзора, как в SSH-шапке (критик 24.09, п.1), и строка самого
        адреса — тоже: SSH показывает её всегда (адрес и A-запись). Нынешний интерфейс таблицу от одного адреса
        не рисует (renderEdge), данные — для любого."""
        o = self._overview(self.SINGLE)
        self.assertEqual(o["edge"]["dns"]["state"], "elsewhere")
        self.assertEqual([r["ip"] for r in o["edge"]["ips"]], ["203.0.113.207"])
        quiet = dict(self.SINGLE, dns={"domain": "vpn.example.com", "state": "ok", "a": ["203.0.113.207"], "foreign": []})
        o = self._overview(quiet)
        self.assertEqual((o["edge"]["count"], len(o["edge"]["ips"]), o["edge"]["dns"]["state"]), (1, 1, "ok"))
        # Сторож не ответил — блока нет вовсе; беда с DNS без строк адресов (адресов не знаем) — есть.
        self.assertNotIn("edge", self._overview({"status": "error", "code": "transport"}))
        no_rows = dict(self.SINGLE, count=0, ips=[])
        self.assertEqual(self._overview(no_rows)["edge"]["ips"], [])
        # Интерфейс 0.5.0 рисует и один адрес — строкой с A-записью, как SSH; беда с DNS — строками под таблицей.
        app = read_text(os.path.join(ROOT, "admin-web", "static", "app.js"))
        edge = app[app.index("function overviewEdge(o) {"):app.index("function overviewProfiles(o) {")]
        self.assertIn("if (!ips.length && o.server_ip) ips = [{ ip: o.server_ip, verdict: null, in_dns: null }];", edge)
        self.assertIn('notes.push(["danger", "circle-exclamation", tf("edgeDnsElsewhere"', edge)

    def test_address_rows_are_whitelisted(self):
        """Строки адресов уходили в браузер как есть; теперь — только известные поля и формы (edge_ips_payload)."""
        edge = {"status": "success", "count": 4, "ips": [
            {"ip": "198.51.100.50", "verdict": "ok", "users": 2, "bytes_15m": 1048576, "bytes_1h": 2.5e6,
             "bytes_day": -5, "packets_day": True, "in_dns": "yes", "silent_since": "<script>", "junk": "x"},
            {"ip": "<b>not an ip</b>", "verdict": "ok"},
            {"ip": "2001:DB8::1", "verdict": ["ok"], "in_dns": False, "silent_since": "2026-09-25T10:00:00Z"},
            "garbage"]}
        rows = server.edge_ips_payload(edge)
        self.assertEqual([r["ip"] for r in rows], ["198.51.100.50", "2001:db8::1"])
        first = rows[0]
        self.assertEqual(set(first), {"ip", "verdict", "in_dns", "silent_since"} | set(server.EDGE_IP_COUNTERS))
        self.assertEqual((first["verdict"], first["users"], first["bytes_15m"], first["bytes_1h"]), ("ok", 2, 1048576, 2500000))
        self.assertEqual((first["bytes_day"], first["packets_day"], first["in_dns"], first["silent_since"]), (None, None, None, ""))
        self.assertEqual((rows[1]["verdict"], rows[1]["in_dns"], rows[1]["silent_since"]), (None, False, "2026-09-25T10:00:00Z"))
        # Вердикт и состояние не того типа — не 500 (нехешируемое в `in set`), а null.
        self.assertIsNone(server.edge_dns_payload({"state": ["ok"], "a": []}))
        self.assertEqual(server.key_choices_payload({"status": "success", "key_choices": [
            {"host": "198.51.100.50", "verdict": {"x": 1}}]})[0]["verdict"], None)

    def test_unknown_or_malformed_dns_is_dropped(self):
        self.assertIsNone(server.edge_dns_payload({"state": "weird", "a": []}))
        self.assertIsNone(server.edge_dns_payload("oops"))
        got = server.edge_dns_payload({"domain": "bad domain;", "state": "none", "a": ["1.2.3.4", "<b>", 5],
                                       "cloudflare": "yes"})
        self.assertEqual(got, {"domain": "", "state": "none", "a": ["1.2.3.4"], "foreign": [], "cloudflare": False})

    def test_tunnel_goes_to_an_address_that_receives(self):
        """Сервер A: основной адрес только исходящий — в команде туннеля адрес, на который приходят клиенты."""
        self.assertEqual(self._overview(self.MULTI)["tunnel_host"], "198.51.100.50")
        self.assertEqual(server.tunnel_host(self.MULTI, "203.0.113.207"), "198.51.100.50")
        # Основной принимает — он и остаётся; адрес один или сторож молчит — server_ip как было.
        ok_main = dict(self.MULTI, ips=[dict(self.MULTI["ips"][0], verdict="ok")] + self.MULTI["ips"][1:])
        self.assertEqual(server.tunnel_host(ok_main, "203.0.113.207"), "203.0.113.207")
        self.assertEqual(server.tunnel_host(self.SINGLE, "203.0.113.207"), "203.0.113.207")
        self.assertEqual(server.tunnel_host({"status": "error", "code": "transport"}, "203.0.113.207"), "203.0.113.207")
        quiet = dict(self.MULTI, ips=[dict(e, verdict="quiet") for e in self.MULTI["ips"]])
        self.assertEqual(server.tunnel_host(quiet, "203.0.113.207"), "203.0.113.207")
        # IPv6 основного адреса в конфиге записан иначе, чем у сторожа, — это тот же адрес, и он принимает.
        v6 = {"status": "success", "count": 2, "ips": [{"ip": "2001:db8::1", "verdict": "ok"},
                                                       {"ip": "198.51.100.84", "verdict": "ok"}]}
        self.assertEqual(server.tunnel_host(v6, "2001:DB8:0::1"), "2001:DB8:0::1")

    def test_settings_carry_tunnel_host_and_bot_state(self):
        def fake(action, params=None, who="panel", **kw):
            if action == "edge-status":
                raise server.ActionError("unknown_action", "нет такого действия", 404)
            return {"status": "success"}
        saved = server.run_action
        server.run_action = fake
        try:
            s = server.settings_payload("tester")
        finally:
            server.run_action = saved
        self.assertIn("tunnel_host", s)                 # сбой сторожа «Настройки» не роняет
        self.assertIn(s["bot_state"], ("active", "inactive", "failed", "unknown", "not_installed"))
        self.assertEqual((s["endpoint_host_gone"], s["server_ip_change"]), (False, None))

    # Данные для тревог Обзора и «Настроек» (L-01, L-06, L-07): из status моста, белым списком.
    STATUS_ALERTS = dict(STATUS, endpoint_host_gone=True,
                         server_ip_change={"old": "203.0.113.10", "new": "198.51.100.7", "at": "2026-09-27T10:00:00Z",
                                           "endpoint_host": "203.0.113.10", "endpoint_host_action": "moved",
                                           "junk": "<b>x</b>"},
                         upstream={"checked": "2026-09-27T05:10:03Z", "attempted": "2026-09-27T05:10:03Z",
                                   "reachable": True, "updates_available": True, "hint": "Есть обновление",
                                   "client_release": {"current": "5.0.2.1", "latest": "5.0.3.0", "update": True},
                                   "profiles": [{"profile": "awg2", "current_tag": "3.1.20260828",
                                                 "available_tag": "3.2.20260915", "update": True, "junk": 1},
                                                {"profile": "<b>", "current_tag": "x"}],
                                   "unsupported": ["CONTENT_PADDING_MAX", "<script>"], "junk": {"a": 1}})

    def test_overview_carries_endpoint_gone_ip_change_and_updates(self):
        saved = self.STATUS
        self.STATUS = self.STATUS_ALERTS
        try:
            o = self._overview(self.SINGLE)
        finally:
            self.STATUS = saved
        self.assertIs(o["endpoint_host_gone"], True)
        self.assertEqual(o["server_ip_change"], {"old": "203.0.113.10", "new": "198.51.100.7", "at": "2026-09-27T10:00:00Z",
                                                 "endpoint_host": "203.0.113.10", "endpoint_host_action": "moved"})
        up = o["upstream"]
        self.assertEqual((up["checked"], up["reachable"], up["updates_available"]), ("2026-09-27T05:10:03Z", True, True))
        self.assertEqual(up["client_release"], {"current": "5.0.2.1", "latest": "5.0.3.0", "update": True})
        self.assertEqual(up["profiles"], [{"profile": "awg2", "current_tag": "3.1.20260828",
                                           "available_tag": "3.2.20260915", "update": True}])
        self.assertEqual(up["unsupported"], ["CONTENT_PADDING_MAX"])
        self.assertNotIn("junk", json.dumps(o))
        # Ядро старше (полей нет) или переездов и проверок не было — False и null, а не ошибка
        plain = self._overview(self.SINGLE)
        self.assertEqual((plain["endpoint_host_gone"], plain["server_ip_change"], plain["upstream"]), (False, None, None))
        # Испорченные значения — null, а не чужие слова на экране
        self.assertIsNone(server.server_ip_change_payload({"old": "x", "new": "not-an-ip"}))
        self.assertEqual(server.server_ip_change_payload({"new": "198.51.100.7", "at": "вчера", "endpoint_host_action": "boom"}),
                         {"old": "", "new": "198.51.100.7", "at": "", "endpoint_host": "", "endpoint_host_action": ""})
        self.assertIsNone(server.upstream_summary_payload("oops"))
        # R-1 (A-16): ключ подсказки доходит до панели — без него Обзор показал бы фразу ядра на языке проверки
        for hk in ("update", "current", "client_only", "unreachable"):
            self.assertEqual(server.upstream_summary_payload({"hint": "Для сервера всё свежее", "hint_key": hk})["hint_key"], hk)
        # Чужое слово или его нет — пусто, панель выберет ключ сама
        self.assertEqual(server.upstream_summary_payload({"hint_key": "<b>x</b>"})["hint_key"], "")
        self.assertEqual(server.upstream_summary_payload({"hint": "x"})["hint_key"], "")

    def test_settings_carry_endpoint_gone_from_status(self):
        def fake(action, params=None, who="panel", **kw):
            return self.STATUS_ALERTS if action == "status" else {"status": "success"}
        saved = server.run_action
        server.run_action = fake
        try:
            s = server.settings_payload("tester")
        finally:
            server.run_action = saved
        self.assertIs(s["endpoint_host_gone"], True)
        self.assertEqual(s["server_ip_change"]["new"], "198.51.100.7")


class FrontendKeyAddressTests(unittest.TestCase):
    """Выбор «Адрес сервера в ключе» в карточке пользователя (renderPeerPane) — текстовая проверка, как у соседей.

    Факты: выбор стоит над рядом кнопок и только когда есть ключ и есть из чего выбрать; первый вариант —
    «как обычно» с пустым значением; домен помечен, у IP — слово сторожа, у «замолчавшего» — ⚠ и
    предупреждение; смена адреса убирает показанный ключ; все шесть кнопок выдачи берут выбранный адрес;
    кэш ключей различает адреса и чистится при перевыпуске целиком; раздача передаёт адрес и называет его.
    """

    STATIC = os.path.join(ROOT, "admin-web", "static")
    KEYS = ("keyAddr", "keyAddrUsual", "keyAddrDomain", "keyAddrHint", "keyAddrDark", "keyAddrGone", "keyAt",
            "shareAddr")

    @staticmethod
    def _cut(text, start, end):
        b = text[text.index(start):]
        return b[:b.index(end)]

    @classmethod
    def setUpClass(cls):
        cls.app = read_text(os.path.join(cls.STATIC, "app.js"))
        cls.css = read_text(os.path.join(cls.STATIC, "styles.css"))
        cls.pane = cls._cut(cls.app, "function renderPeerPane(", "async function fetchKey(")
        cls.picker = cls._cut(cls.pane, "if (altChoices.length) {", "// Выключенному ключ выдаётся")

    def dict_body(self, lang):
        head = "    %s: {" % lang
        start = self.app.index(head) + len(head)
        return self.app[start:self.app.index("\n    },", start)]

    def test_both_dictionaries_carry_the_strings(self):
        for lang in ("ru", "en"):
            body = self.dict_body(lang)
            for key in self.KEYS:
                self.assertIn("%s:" % key, body, "%s/%s" % (lang, key))
        ru, en = self.dict_body("ru"), self.dict_body("en")
        for word in ('keyAddr: "Адрес сервера в ключе"', 'keyAddrUsual: "как обычно · {h}"',
                     'keyAddrDomain: "{h} · домен"', 'keyAt: "Адрес в ключе: {h}"',
                     'shareAddr: "Адрес сервера в файле: {h}"',
                     'keyAddrHint: "Ключ тот же — меняется только адрес сервера внутри; за пользователем не сохраняется."',
                     'keyAddrDark: "адрес молчит — ключ может не подключиться"'):
            self.assertIn(word, ru)
        for word in ('keyAddr: "Server address in the key"', 'keyAddrUsual: "as usual · {h}"',
                     'keyAddrDomain: "{h} · domain"', 'keyAt: "Address in the key: {h}"',
                     'shareAddr: "Server address in the file: {h}"'):
            self.assertIn(word, en)

    def test_picker_shows_only_with_a_key_and_a_real_choice(self):
        self.assertIn("const altChoices = choices.filter((ch) => !sameHost(ch.host, keyHost));", self.pane)
        self.assertIn("String(user.key_host || user.keys_host", self.pane)
        # Только у пира с ключом (ветка ключа из приложения вернулась раньше) и над рядом кнопок выдачи.
        self.assertLess(self.pane.index("return { tiles, blocks, qr: null };"), self.pane.index("if (altChoices.length) {"))
        self.assertLess(self.pane.index("if (altChoices.length) {"), self.pane.index("const issueRow = hasKey ? ["))
        # Подписанный выбор: label for=id, «как обычно» первым с пустым значением, подсказка — под выбранный адрес.
        self.assertIn('el("label", { class: "gx-field key-addr", for: selId }, el("span", { class: "gx-field__l", text: t("keyAddr") })', self.picker)
        self.assertIn('U.select([{ v: "", l:', self.picker)
        self.assertIn('tf("keyAddrUsual", { h: keyHost })', self.picker)
        self.assertLess(self.picker.index('{ v: "", l:'), self.picker.index("altChoices.map("))
        self.assertIn("const syncHint = () => { const [h, bad] = hintFor(ep);", self.picker)

    def test_options_name_domains_and_verdicts(self):
        self.assertIn('if (ch.kind === "domain") return tf("keyAddrDomain", { h: ch.host });', self.picker)
        self.assertIn("edgeWord(ch.verdict)", self.picker)
        self.assertIn('ch.verdict === "dark" ? "⚠ " : ""', self.picker)
        # «Замолчавший» не спрятан: в варианты идут все altChoices, фильтра по вердикту нет; выбран — красная подсказка.
        self.assertNotIn('ch.verdict !== "dark"', self.pane)
        self.assertIn('if (ch.verdict === "dark") return [t("ucHDark"), true];', self.pane)
        self.assertIn("return [tf(\"ucHUsual\", { h: keyHost || \"—\" }), isDark(keyHost)];", self.pane)
        # Слова — те же, что в таблице «Адреса сервера»: одно отображение на обоих.
        self.assertIn("const edgeWord = (v) =>", self.app)
        edge = self._cut(self.app, "function overviewEdge(o) {", "function overviewProfiles(o) {")
        self.assertIn("U.chip(edgeWord(e.verdict), edgeTone(e.verdict))", edge)

    def test_changing_the_address_clears_the_shown_key(self):
        self.assertIn('addrSel.addEventListener("change", () => { ep = addrSel.value; c.ep = ep; c.qrPart = 0; keyBox.innerHTML = ""; syncHint(); drawKey(); });',
                      self.picker)
        # Ответ, пришедший после смены адреса, в окно не рисуется.
        show = self._cut(self.pane, "const showQr = async (kind) => {", "\n    };")
        self.assertIn("const want = ep;", show)
        self.assertIn("if (want !== ep) return;", show)

    def test_every_key_button_uses_the_chosen_address(self):
        show = self._cut(self.pane, "const showQr = async (kind) => {", "\n    };")
        self.assertIn("fetchKey(name, pid, want)", show)
        self.assertIn('tf("keyAt", { h: key.key_host || want })', show)
        copy = self._cut(self.pane, "const copyVpn = async () => {", "\n    };")
        self.assertIn("fetchKey(name, pid, ep)", copy)
        dl = self._cut(self.pane, "const downloadConf = async () => {", "\n    };")
        # Н-2: имя — file_name ядра (≤15 знаков, адрес в имя не входит); ядро старше поля — прежнее, с адресом
        self.assertIn("download: confName(key, host)", dl)
        cn = self._cut(self.pane, "const confName = (key, host) =>", ";\n")
        self.assertIn("key.file_name", cn)
        self.assertIn("host ? `${name}-${pid}-${host}.conf` : `${name}-${pid}.conf`", cn)
        row = self._cut(self.pane, "const issueRow = hasKey ? [", "] : [];")
        self.assertIn("openShare(name, pid, ep)", row)
        # Ни одного запроса ключа мимо выбранного адреса.
        self.assertNotIn("fetchKey(name, pid)", self.pane)

    def test_key_cache_tells_addresses_apart(self):
        fetch = self._cut(self.app, "async function fetchKey(", "\n  }\n")
        self.assertIn('async function fetchKey(name, pid, ep = "") {', fetch)
        self.assertIn("const k = `${name}/${pid}/${ep}`;", fetch)
        self.assertIn('const q = ep ? `&endpoint=${encodeURIComponent(ep)}` : "";', fetch)
        self.assertIn("&format=all${q}", fetch)

    def test_share_passes_the_address_and_names_it(self):
        share = self._cut(self.app, "async function openShare(", "\n  }\n")
        self.assertIn('async function openShare(name, profile, endpoint = "") {', share)
        self.assertIn("...(endpoint ? { endpoint } : {})", share)
        self.assertIn("showShareUrl(r, endpoint, dlg);", share)
        self.assertIn('tf("shareAddr", { h: endpoint })', share)
        url = self._cut(self.app, "function showShareUrl(r", "\n  }\n")
        self.assertIn('String((r && r.endpoint) || endpoint || "")', url)
        self.assertIn('el("span", { class: "gx-al__text", text: tf("shareAddr", { h: host }) })', url)
        self.assertIn("host ? el(", url)

    def test_select_width_on_desktop_only(self):
        self.assertIn(".key-addr select { max-width: 520px; }", self.css)


class OverviewDiskAndRollbackPayloadTests(unittest.TestCase):
    """OPS-01 (панель): status.disk и status.update_rollback доходят до Обзора (им же пользуется мини-апп) — белым
    списком; мусор от моста — None, а не 500 и не поле как есть."""

    DISK = {"path": "/opt/goamnezia", "total_mb": 20000, "used_mb": 19400, "free_mb": 600, "used_pct": 97,
            "level": "low", "low_mb": 1024, "critical_mb": 512}
    RB = {"at": "2026-09-28T10:00:00Z", "stage": "migration", "reason": "перенос данных оборвался",
          "version": "0.5.0", "failed_version": "0.5.1", "code_restored": True, "data_restored": True,
          "archive": "/opt/goamnezia/backups/preupgrade_0.5.1_20260928-100000.tar.gz"}

    def _overview(self, status):
        def fake(action, params=None, who="panel", **kw):
            return {"status": status}.get(action, {"status": "success", "users": []})
        saved = server.run_action
        server.run_action = fake
        try:
            return server.overview_payload("tester")
        finally:
            server.run_action = saved

    def test_fields_reach_the_overview(self):
        o = self._overview({"status": "success", "mode": "lite", "profiles": [], "disk": dict(self.DISK, junk="x"),
                            "update_rollback": dict(self.RB, junk=["x"])})
        self.assertEqual(o["disk"], self.DISK)
        self.assertEqual(o["update_rollback"], self.RB)
        o = self._overview({"status": "success", "mode": "lite", "profiles": [], "disk": None, "update_rollback": None})
        self.assertIsNone(o["disk"])
        self.assertIsNone(o["update_rollback"])
        o = self._overview({"status": "error", "code": "transport"})
        self.assertIsNone(o["disk"])
        self.assertIsNone(o["update_rollback"])

    def test_disk_is_whitelisted(self):
        self.assertIsNone(server.disk_payload("low"))
        self.assertIsNone(server.disk_payload(dict(self.DISK, level="full")))
        self.assertIsNone(server.disk_payload(dict(self.DISK, level=["low"])))
        self.assertIsNone(server.disk_payload(dict(self.DISK, free_mb="600")))
        self.assertIsNone(server.disk_payload(dict(self.DISK, free_mb=True)))
        self.assertIsNone(server.disk_payload(dict(self.DISK, used_pct=-1)))
        self.assertEqual(server.disk_payload(dict(self.DISK, path="/opt/<x>\n"))["path"], "")

    def test_rollback_is_whitelisted(self):
        self.assertIsNone(server.update_rollback_payload([]))
        self.assertIsNone(server.update_rollback_payload(dict(self.RB, failed_version="<b>1</b>")))
        bad = server.update_rollback_payload(dict(self.RB, at="вчера", stage="rm -rf", version="x y", code_restored="yes",
                                                  data_restored=1, archive="a\nb", reason=["x"]))
        self.assertEqual(bad, {"at": "", "stage": "", "reason": "", "failed_version": "0.5.1", "version": "",
                               "code_restored": False, "data_restored": False, "archive": ""})
        for stage in ("snapshot", "record", "migration", "check"):
            self.assertEqual(server.update_rollback_payload(dict(self.RB, stage=stage))["stage"], stage)


class OverviewDockerStalePayloadTests(unittest.TestCase):
    """DK-02 (панель): status.docker_stale — пакет Docker обновлён, а служба старая — доходит до Обзора (им же пользуется
    мини-апп) белым списком; «Перезапустить Docker» — разрушительное: без confirm = имя действия мост не зовётся."""

    DKS = {"reason": "version", "binary_version": "29.1.3", "binary_build": "29.1.3-0ubuntu3~24.04.2",
           "daemon_version": "24.0.7", "daemon_build": "24.0.7-0ubuntu4", "client_api": "1.52", "daemon_api": "1.43",
           "since": "2026-10-01T06:12:00Z", "retry_after": "2026-10-04T18:00:00Z",
           "binary_label": "29.1.3", "daemon_label": "24.0.7",
           "last_attempt": {"at": "2026-10-04T12:00:00Z", "ok": False, "by": "watch", "code": "docker_restart_timeout"}}

    def _overview(self, status):
        def fake(action, params=None, who="panel", **kw):
            return {"status": status}.get(action, {"status": "success", "users": []})
        saved = server.run_action
        server.run_action = fake
        try:
            return server.overview_payload("tester")
        finally:
            server.run_action = saved

    def test_field_reaches_the_overview_whitelisted(self):
        o = self._overview({"status": "success", "mode": "lite", "profiles": [], "docker_stale": dict(self.DKS, junk="x")})
        self.assertEqual(o["docker_stale"], self.DKS)
        for value in (None, "stale", {"reason": "rm -rf"}, {"reason": ["version"]}):
            o = self._overview({"status": "success", "mode": "lite", "profiles": [], "docker_stale": value})
            self.assertIsNone(o["docker_stale"], value)
        self.assertIsNone(self._overview({"status": "error", "code": "transport"})["docker_stale"])
        bad = server.docker_stale_payload(dict(self.DKS, binary_version="<b>1</b>", since="вчера", daemon_build="a b",
                                               last_attempt={"at": "x", "ok": "no"}))
        self.assertEqual((bad["binary_version"], bad["since"], bad["daemon_build"], bad["last_attempt"]), ("", "", "", None))
        odd = server.docker_stale_payload(dict(self.DKS, last_attempt={"at": "2026-10-04T12:00:00Z", "ok": "yes", "by": "root", "code": "<x>"}))
        self.assertEqual(odd["last_attempt"], {"at": "2026-10-04T12:00:00Z", "ok": None, "by": "", "code": ""})

    def test_docker_restart_is_destructive_and_waits_long_enough(self):
        self.assertEqual(server.confirm_required("docker-restart"), "")
        calls = []
        orig = server.subprocess.Popen
        server.subprocess.Popen = lambda *a, **kw: calls.append(a) or orig(*a, **kw)
        try:
            for confirm in (None, "profiles-restart", "Docker-Restart"):
                with self.assertRaises(server.ActionError) as ctx:
                    server.run_action("docker-restart", {}, "test", confirm=confirm, use_cache=False)
                self.assertEqual(ctx.exception.code, "confirm_required", confirm)
        finally:
            server.subprocess.Popen = orig
        self.assertEqual(calls, [], "без верного confirm мост не зовётся")
        # Служба Docker и затем каждый профиль с соседями и goWARP — дольше обычных 300 с
        self.assertGreaterEqual(server.BRIDGE_TIMEOUT_BY_ACTION["docker-restart"], 600)


class OverviewKmodPayloadTests(unittest.TestCase):
    """KMOD_PLAN, этап А (панель): status.kmod — модуль ядра AmneziaWG на сервере без набора исправлений при профиле
    3.1 — доходит до Обзора (и мини-аппа) белым списком; на Обзоре — строка-предупреждение, после смены параметра,
    применённой перезапуском профиля (restarted), — «переподключатся сами, обычно за 15–25 секунд»."""

    KM = {"loaded": True, "version": "3.1.20260812", "patched": False, "warn": True}
    # С этапа Б (1.0.13) в белом списке ещё state, code и reason; у ядра этапа А их нет — пусто.
    NO_B = {"state": "", "code": "", "reason": ""}

    def _overview(self, status):
        def fake(action, params=None, who="panel", **kw):
            return {"status": status}.get(action, {"status": "success", "users": []})
        saved = server.run_action
        server.run_action = fake
        try:
            return server.overview_payload("tester")
        finally:
            server.run_action = saved

    def test_field_reaches_the_overview_whitelisted(self):
        o = self._overview({"status": "success", "mode": "lite", "profiles": [], "kmod": dict(self.KM, junk="<x>")})
        self.assertEqual(o["kmod"], dict(self.KM, **self.NO_B))
        ok = server.kmod_payload({"loaded": True, "version": "3.1.20260906+kaskad-k31.1", "patched": True, "warn": False})
        self.assertEqual(ok, {"loaded": True, "version": "3.1.20260906+kaskad-k31.1", "patched": True, "warn": False, **self.NO_B})
        for value in (None, "warn", {"warn": True}, {"loaded": "yes", "warn": True}):
            self.assertIsNone(self._overview({"status": "success", "mode": "lite", "profiles": [], "kmod": value})["kmod"], value)
        self.assertIsNone(self._overview({"status": "error", "code": "transport"})["kmod"])
        odd = server.kmod_payload({"loaded": True, "version": "<b>3.1</b>", "patched": "yes", "warn": 1})
        self.assertEqual(odd, {"loaded": True, "version": "", "patched": False, "warn": False, **self.NO_B})

    def test_stage_b_fallback_and_failed_reach_the_overview(self):
        """KMOD_PLAN этап Б: выбран модуль, а профили работают программой (fallback), или установка на этом ядре не
        удалась (failed) — Обзор и мини-апп показывают ту же строку, что шапка SSH-меню: state, code и reason словами."""
        reason = "нет модуля для ядра 6.8.0-146-generic — профили работают программой; пересобрать? (Обслуживание → Ускорение AmneziaWG → 3)"
        km = dict(self.KM, warn=False, state="fallback", code="no_module_for_kernel", reason=reason, profiles={"awg2": {}})
        o = self._overview({"status": "success", "mode": "lite", "profiles": [], "kmod": km})
        self.assertEqual(o["kmod"], dict(self.KM, warn=False, state="fallback", code="no_module_for_kernel", reason=reason))
        bad = server.kmod_payload(dict(self.KM, state="panic", code="<b>", reason="a\nb\x00c"))
        self.assertEqual((bad["state"], bad["code"], bad["reason"]), ("", "", "a b c"))
        with open(os.path.join(ROOT, "admin-web", "static", "app.js"), encoding="utf-8") as f:
            js = f.read()
        self.assertIn('else if (km && (km.state === "fallback" || km.state === "failed") && km.reason) {', js)

    def test_overview_and_param_toast_speak_words(self):
        with open(os.path.join(ROOT, "admin-web", "static", "app.js"), encoding="utf-8") as f:
            js = f.read()
        self.assertIn('if (km && km.warn) card("warning"', js)
        # С 1.0.13 итог смены параметра — paramResult (слова ядра и steps, PARAM-11); ответ прежнего ядра — тост, как раньше.
        self.assertIn('d.restarted ? t("prParamDoneRestart")', js)
        for key in ("ovAlKmodT", "ovAlKmodD", "prParamDoneRestart"):
            self.assertEqual(len(re.findall(r"\n\s+%s: \"" % key, js)), 2, key)   # ru и en
        # «15–25» — в prParamDoneRestart на обоих языках (текст «Исправить S4», S4-FIX, тоже его называет — это другие ключи).
        self.assertEqual(len(re.findall(r'prParamDoneRestart: "[^"]*15–25', js)), 2)
        # KMOD-02: подхват с тегом I1–I5 не всех реализаций — тост-предупреждение словами ядра (ipacket_warnings).
        self.assertIn("r.ipacket_warnings", js)
        self.assertIn('ovr || ipw ? "warning" : undefined', js)


class KmodPanelTests(unittest.TestCase):
    """Ускорение AmneziaWG — модуль ядра по выбору человека (KMOD_PLAN этап Б, CONTRACT §4.7a) в сервере панели:
    kmod-status белым списком, движок профилей в /api/profiles, действие в фоне — цепочка шагов моста, как пункт SSH-меню
    (enable: kmod-install, затем kmod-engine kernel; program; rebuild с возвратом на модуль; remove), с confirm = действие и
    своим confirm каталога у разрушительных шагов; второе действие, пока идёт первое, — отказ; итог — белым списком."""

    LABEL = "3.1.20260906+kaskad-k31.1"
    KS = {"loaded": True, "version": LABEL, "patched": True, "warn": False, "state": "on", "code": "", "reason": "",
          "kernel": "6.8.0-36-generic", "want": "kernel", "installed": True, "installed_version": LABEL, "owner": "goamnezia",
          "label": LABEL, "profiles": {"awg2": {"engine": "kernel", "want": "kernel"}, "awg3": {"engine": "stopped", "want": ""}},
          "progress": None, "selftest": {"ok": True, "kernel": "6.8.0-36-generic", "label": LABEL, "at": "2026-10-08T10:00:00Z",
                                         "reason": "", "cases": [{"profile": "awg2"}]},
          "failed": None, "packages": ["dkms", "gcc-13"]}
    ROWS = [{"profile": "awg2", "from": "program", "to": "kernel", "restarted": True, "alive_before": 3, "returned": 0,
             "return_s": None, "note": "lost"}]

    def setUp(self):
        with server._KMOD_JOB_LOCK:
            server._KMOD_JOB.clear()
        self.calls = []

    def tearDown(self):
        job = server._KMOD_JOB.get("job")
        if job and job.get("thread"):
            job["thread"].join(10)
        with server._KMOD_JOB_LOCK:
            server._KMOD_JOB.clear()

    def _bridge(self, answers, gate=None):
        """Мост — ответы по действию; вызовы (действие, параметры, confirm, язык) — в self.calls."""
        def fake(action, params=None, who="panel", timeout=None, confirm=None, use_cache=True, **kw):
            self.calls.append((action, dict(params or {}), confirm, server.call_lang()))
            if gate is not None:
                gate.wait(10)
            r = answers.get(action, {"status": "success", "message": "ok"})
            return r(params) if callable(r) else r
        saved = server.run_action
        server.run_action = fake
        self.addCleanup(setattr, server, "run_action", saved)

    def _start(self, op, confirm="same", headers_meta=None, lang="", wait=True):
        server._CALL_CTX.lang = lang
        try:
            status, payload = server.kmod_job_start(op, "tester", op if confirm == "same" else confirm, headers_meta)
        finally:
            server._CALL_CTX.lang = ""
        job = server._KMOD_JOB.get("job")
        if wait and job and job.get("thread"):
            job["thread"].join(10)
        return status, payload

    def test_status_is_whitelisted_and_needs_stage_b(self):
        raw = dict(self.KS, junk="<script>", reason="x\ny", owner="evil", want="both",
                   profiles={"awg2": {"engine": "kernel", "want": "kernel", "x": 1}, "awg9": {"engine": "kernel"}, "awg3": "bad"},
                   progress={"action": "install", "step": "build", "text": "Собираю\x07 модуль", "at": "2026-10-08T10:00:00Z",
                             "started": "nope", "pid": 4242},
                   failed={"kernel": "6.8.0-36-generic", "step": "build", "code": "kmod_build_failed", "reason": "не собрался",
                           "log": "/var/lib/dkms/amneziawg/3.1.20260906/build/make.log", "at": "2026-10-08T10:00:00Z"},
                   packages=["dkms", "rm -rf /", "gcc-13"])
        p = server.kmod_status_payload(raw)
        self.assertNotIn("junk", p)
        self.assertEqual(p["reason"], "x y")
        self.assertEqual((p["owner"], p["want"]), ("", ""))
        self.assertEqual(p["profiles"], {"awg2": {"engine": "kernel", "want": "kernel"}})
        self.assertEqual(p["progress"], {"action": "install", "step": "build", "text": "Собираю модуль", "at": "2026-10-08T10:00:00Z",
                                         "started": ""})
        self.assertEqual(p["failed"]["log"], "/var/lib/dkms/amneziawg/3.1.20260906/build/make.log")
        self.assertEqual(p["packages"], ["dkms", "gcc-13"])
        self.assertNotIn("cases", p["selftest"], "случаи самопроверки — только в итоге установки")
        # Ядро этапа А (без state) — карточки нет; испорченная запись — тоже
        self.assertIsNone(server.kmod_status_payload({"loaded": True, "version": "3.1.20260812", "patched": False, "warn": True}))
        self.assertIsNone(server.kmod_status_payload(None))
        # GET /api/kmod: состояние и действие панели; отказ моста — словами, без карточки
        self._bridge({"kmod-status": {"status": "success", "kmod": self.KS}})
        d = server.kmod_panel_payload("tester")
        self.assertEqual((d["kmod"]["state"], d["error"], d["job"]), ("on", None, None))
        self._bridge({"kmod-status": {"status": "error", "code": "transport", "message": ""}})
        d = server.kmod_panel_payload("tester")
        self.assertEqual((d["kmod"], d["error"]["code"]), (None, "transport"))

    def test_profiles_carry_the_engine(self):
        def fake(action, params=None, who="panel", **kw):
            return {"status": "success", "profiles": [
                {"profile": "awg2", "state": "running", "line": "2.0", "engine": "kernel", "engine_want": "kernel"},
                {"profile": "awg3", "state": "running", "line": "3.1", "engine": "<b>", "engine_want": "both"}]}
        saved = server.run_action
        server.run_action = fake
        try:
            d = server.profiles_payload("tester")
        finally:
            server.run_action = saved
        by = {p["profile"]: p for p in d["installed"]}
        self.assertEqual((by["awg2"]["engine"], by["awg2"]["engine_want"]), ("kernel", "kernel"))
        self.assertEqual((by["awg3"]["engine"], by["awg3"]["engine_want"]), ("", ""))

    def test_enable_installs_then_moves_profiles_with_the_catalog_confirm(self):
        self._bridge({"kmod-install": {"status": "success", "message": "Модуль ядра установлен", "state": "ready", "version": self.LABEL,
                                       "kernel": "6.8.0-36-generic", "owner": "goamnezia", "reused": False, "packages": ["dkms"],
                                       "headers_meta_skipped": "linux-headers-generic", "want": "program",
                                       "selftest": {"ok": True, "cases": [{"profile": "awg2", "line": "2.0", "client": "kernel", "ok": True,
                                                                           "detail": ""}, {"profile": "x", "line": "2.0"}]}},
                      "kmod-engine": {"status": "success", "message": "Движок профилей сменён", "engine": "kernel", "want": "kernel",
                                      "rolled_back": False, "copies": "/opt/x",
                                      "profiles": [dict(self.ROWS[0], returned=3, return_s=17, note="")]}})
        status, payload = self._start("enable", headers_meta="on", lang="en")
        self.assertEqual(status, 200)
        self.assertEqual(payload["data"]["job"]["op"], "enable")
        self.assertEqual(self.calls, [("kmod-install", {"rebuild": "off", "headers_meta": "on"}, None, "en"),
                                      ("kmod-engine", {"engine": "kernel"}, "kernel", "en")])
        job = server.kmod_job_payload()
        self.assertTrue(job["done"])
        self.assertEqual([s["action"] for s in job["steps"]], ["kmod-install", "kmod-engine"])
        inst, eng = job["steps"]
        self.assertEqual(inst["headers_meta_skipped"], "linux-headers-generic")
        self.assertEqual([c["profile"] for c in inst["selftest"]["cases"]], ["awg2"])
        self.assertNotIn("copies", eng)
        self.assertEqual(eng["profiles"][0]["return_s"], 17)
        self.assertTrue(eng["ok"])

    def test_failed_install_stops_and_keeps_the_words(self):
        self._bridge({"kmod-install": {"status": "error", "code": "kmod_build_failed", "message": "Модуль не собрался под ядро 6.8.0-36-generic"}})
        self._start("enable")
        self.assertEqual([c[0] for c in self.calls], ["kmod-install"], "без модуля профили не переводятся")
        step, = server.kmod_job_payload()["steps"]
        self.assertEqual((step["ok"], step["code"], step["message"]), (False, "kmod_build_failed", "Модуль не собрался под ядро 6.8.0-36-generic"))

    def test_program_rebuild_and_remove_chains(self):
        self._bridge({})
        self._start("program")
        self.assertEqual(self.calls, [("kmod-engine", {"engine": "program"}, "program", "")])
        # Пересборка: выбран модуль, а профили не на нём — затем обратно на модуль (как SSH «3) Пересобрать»)
        self.calls.clear()
        self._bridge({"kmod-install": {"status": "success", "state": "fallback", "want": "kernel"}})
        self._start("rebuild")
        self.assertEqual(self.calls, [("kmod-install", {"rebuild": "on", "headers_meta": "off"}, None, ""),
                                      ("kmod-engine", {"engine": "kernel"}, "kernel", "")])
        self.calls.clear()
        self._bridge({"kmod-install": {"status": "success", "state": "ready", "want": "program"}})
        self._start("rebuild")
        self.assertEqual([c[0] for c in self.calls], ["kmod-install"], "выбрана программа — профили остаются на ней")
        self.calls.clear()
        self._bridge({"kmod-remove": {"status": "success", "removed": True, "kept_for_neighbour": False,
                                      "packages": ["dkms", "gcc-13"], "command": "apt remove dkms gcc-13; reboot"}})
        self._start("remove")
        self.assertEqual(self.calls, [("kmod-remove", {}, "kmod-remove", "")])
        step, = server.kmod_job_payload()["steps"]
        self.assertEqual(step["command"], "apt remove dkms gcc-13", "команда — только apt remove и имена пакетов")

    def test_rollback_is_reported_whitelisted(self):
        rows = self.ROWS + [{"profile": "awg9", "note": "lost"}, {"profile": "awg3", "note": "<x>", "alive_before": -5, "from": "x"}]
        self._bridge({"kmod-engine": {"status": "error", "code": "kmod_clients_lost", "message": "Профиль awg2: никто\nне вернулся",
                                      "engine": "kernel", "want": "program", "rolled_back": True, "profiles": rows}})
        self._start("program")
        step, = server.kmod_job_payload()["steps"]
        self.assertEqual((step["ok"], step["code"], step["rolled_back"]), (False, "kmod_clients_lost", True))
        self.assertEqual(step["message"], "Профиль awg2: никто не вернулся")
        self.assertEqual([r["profile"] for r in step["profiles"]], ["awg2", "awg3"])
        self.assertEqual(step["profiles"][1], {"profile": "awg3", "from": "", "to": "", "restarted": False, "alive_before": 0,
                                               "returned": 0, "return_s": None, "note": ""})

    def test_refusals_before_the_bridge(self):
        self._bridge({})
        status, payload = self._start("format-disk")
        self.assertEqual((status, payload["code"]), (404, "unknown_action"))
        for confirm in (None, "", "remove", "enable "):
            status, payload = self._start("enable", confirm=confirm)
            self.assertEqual((status, payload["code"]), (400, "confirm_required"), confirm)
        status, payload = self._start("enable", headers_meta="yes")
        self.assertEqual((status, payload["code"]), (400, "bad_arg"))
        self.assertEqual(self.calls, [], "без согласия мост не зовётся")
        # Второе действие, пока идёт первое, — отказ словами; первое доходит до конца
        gate = threading.Event()
        self._bridge({}, gate=gate)
        status, _ = self._start("program", wait=False)
        self.assertEqual(status, 200)
        status, payload = self._start("remove", wait=False)
        self.assertEqual((status, payload["code"]), (409, "kmod_busy"))
        self.assertIn("модуля ядра", payload["error"])
        running = server.kmod_job_payload()
        self.assertEqual((running["op"], running["done"]), ("program", False))
        gate.set()
        server._KMOD_JOB["job"]["thread"].join(10)
        self.assertTrue(server.kmod_job_payload()["done"])
        self.assertEqual([c[0] for c in self.calls], ["kmod-engine"])

    def test_result_is_kept_half_an_hour(self):
        clock = Clock()
        saved = server._now
        server._now = clock
        self.addCleanup(setattr, server, "_now", saved)
        self._bridge({})
        self._start("program")
        self.assertIsNotNone(server.kmod_job_payload())
        clock.advance(server.KMOD_JOB_KEEP + 1)
        self.assertIsNone(server.kmod_job_payload())

    def test_long_actions_wait_long_enough_and_the_status_snapshot_follows_the_files(self):
        t = server.BRIDGE_TIMEOUT_BY_ACTION
        self.assertGreaterEqual(t["kmod-install"], 1800)
        self.assertGreaterEqual(t["kmod-engine"], 900)
        self.assertGreaterEqual(t["kmod-remove"], 900)
        self.assertGreaterEqual(t["kmod-check"], 120)
        self.assertIn("kmod-status", server.READ_CACHE_TTL)
        tmp = tempfile.mkdtemp(prefix="ga-kmod-sig-")
        self.addCleanup(shutil.rmtree, tmp, True)
        saved = server.KMOD_FILES
        server.KMOD_FILES = tuple(os.path.join(tmp, n) for n in ("kmod.json", "kmod-progress.json", "kmod-engines.json"))
        self.addCleanup(setattr, server, "KMOD_FILES", saved)
        before = server._snapshot_sig("kmod-status")
        with open(server.KMOD_FILES[1], "w", encoding="utf-8") as fh:
            fh.write('{"action":"install","step":"build"}')
        self.assertNotEqual(server._snapshot_sig("kmod-status"), before, "новый шаг хода — новый снимок")


class PacketRiskPayloadTests(unittest.TestCase):
    """S4-FIX (MTU-01): status.profiles[].packet_risk — пакет данных при стандартном MTU 1280 больше 1400 из-за большого
    S4 — доходит до «Обзора» (и мини-аппа) и до карточек профилей белым списком, целыми числами; у профиля в
    /api/profiles — ещё S4 из config.json для окна MTU. Мусор и чужие поля не проходят."""

    RISK = {"packet": 1502, "budget": 1400, "s4": 142, "safe_s4": 40, "mtu": 1280, "keyless": 1}

    def test_whitelist(self):
        self.assertEqual(server.packet_risk_payload(dict(self.RISK, cmd="rm -rf /")), self.RISK)
        for value in (None, "risk", 1502, {}, dict(self.RISK, s4="142"), dict(self.RISK, keyless=True),
                      dict(self.RISK, packet=-1), {k: v for k, v in self.RISK.items() if k != "safe_s4"}):
            self.assertIsNone(server.packet_risk_payload(value), value)

    def test_overview_and_profiles_carry_the_risk(self):
        st = {"status": "success", "mode": "lite", "profiles": [
            {"profile": "awg2", "container": "amnezia-awg2", "state": "running", "port": 443, "line": "2.0", "peers": 3,
             "packet_risk": dict(self.RISK, junk=1)},
            {"profile": "awg3", "container": "amnezia-awg3", "state": "running", "port": 4000, "line": "3.1", "peers": 1,
             "packet_risk": None}]}

        def fake(action, params=None, who="panel", **kw):
            return {"status": st}.get(action, {"status": "success", "users": []})
        saved_run, saved_snap = server.run_action, server.read_snapshot
        server.run_action = fake
        server.read_snapshot = lambda *a, **kw: dict(st, status="success") if a and a[0] == "profiles-status" else fake(*a, **kw)
        try:
            o = server.overview_payload("tester")
            d = server.profiles_payload("panel")
        finally:
            server.run_action, server.read_snapshot = saved_run, saved_snap
        by = {p["profile"]: p for p in o["profiles"]}
        self.assertEqual(by["awg2"]["packet_risk"], self.RISK)
        self.assertIsNone(by["awg3"]["packet_risk"])
        inst = {p["profile"]: p for p in d["installed"]}
        self.assertEqual(inst["awg2"]["packet_risk"], self.RISK)
        self.assertIsNone(inst["awg3"]["packet_risk"])

    def test_public_profile_carries_s4_for_the_mtu_window(self):
        self.assertEqual(server.public_profile("awg2", {"params": {"S4": "142", "MTU": "1280"}})["s4"], "142")
        self.assertEqual(server.public_profile("awg1", {"params": {"MTU": "1280"}})["s4"], "")
        self.assertEqual(server.public_profile("awg2", {"params": {"S4": "<b>"}})["s4"], "")
        self.assertEqual(server.public_profile("awg2", {})["s4"], "")

    def test_words_in_both_languages(self):
        with open(os.path.join(ROOT, "admin-web", "static", "app.js"), encoding="utf-8") as f:
            js = f.read()
        for key in ("ovAlS4T", "ovAlS4D", "prS4B", "prS4T", "prS4Fix", "prS4FixT", "prS4FixD", "prS4Keyless", "prS4KeylessGo",
                    "prS4DoneT", "prMtuStd", "prMtuKeep", "prMtuWarnPath", "prMtuLow"):
            self.assertEqual(len(re.findall(r"\n\s+%s: \"" % key, js)), 2, key)   # ru и en
        self.assertIn('action("profile-fix-s4", params, { confirm: pid', js)
        self.assertIn("1280 — стандарт, работает везде.", js)


class InstallAdminPasswordNoteTests(unittest.TestCase):
    """NC-14: установщик панели печатает пароль один раз — и сразу говорит, что нужен он только для входа с домена, а
    по SSH-туннелю не нужен. Строка — только когда панель туннелю доверяет (как server.py: окружение, затем
    config.json panel.trust_tunnel, по умолчанию да): без доверия и по туннелю спрашивают код или пароль."""

    SRC = os.path.join(ROOT, "admin-web", "install_admin.sh")

    @classmethod
    def setUpClass(cls):
        cls.src = read_text(cls.SRC)

    def test_words_in_both_languages_right_after_the_password(self):
        self.assertIn('I18N_RU[pwd_tunnel]="Он нужен только для входа с домена; через туннель пароль не нужен."', self.src)
        self.assertIn('I18N_EN[pwd_tunnel]="It is needed only to sign in from a domain; through the tunnel no password is needed."',
                      self.src)
        body = self.src[self.src.index("ga_print_credentials() {"):]
        body = body[:body.index("\n}\n")]
        self.assertLess(body.index("$(t pwd_once)"), body.index("if ga_tunnel_trusted; then"))
        self.assertIn('if ga_tunnel_trusted; then echo -e "  ${YELLOW}$(t pwd_tunnel)${NC}"; fi', body)

    @unittest.skipUnless(shutil.which("bash") and shutil.which("jq"), "нет bash или jq")
    def test_the_note_follows_the_tunnel_trust(self):
        fn = re.search(r"^ga_tunnel_trusted\(\) \{\n.*?^\}\n", self.src, re.M | re.S).group(0)
        cases = [("", None, True), ("", {"panel": {"port": 1985}}, True), ("", {"panel": {"trust_tunnel": True}}, True),
                 ("", {"panel": {"trust_tunnel": False}}, False), ("0", {"panel": {"trust_tunnel": True}}, False),
                 ("1", {"panel": {"trust_tunnel": False}}, True)]
        for env_value, cfg, trusted in cases:
            with self.subTest(env=env_value, cfg=cfg), tempfile.TemporaryDirectory() as tmp:
                if cfg is not None:
                    with open(os.path.join(tmp, "config.json"), "w", encoding="utf-8") as fh:
                        json.dump(cfg, fh)
                env = {k: v for k, v in os.environ.items() if k != "GOAMNEZIA_TRUST_TUNNEL"}
                env["GOAMNEZIA_DIR"] = tmp
                if env_value:
                    env["GOAMNEZIA_TRUST_TUNNEL"] = env_value
                rc = subprocess.run(["bash", "-c", fn + "\nga_tunnel_trusted"], env=env, timeout=30).returncode
                self.assertEqual(rc == 0, trusted)


class EmergencyKeyTests(unittest.TestCase):
    """SEC50-08 (решение владельца 28.09): аварийный ключ — одна строка 0600 и рядом token.meta 0600 со сроком; его
    выдаёт SSH-меню на час. Принимается только по SSH-туннелю, только с meta и в срок, и сгорает при входе. С домена
    — отказ словами. Без meta (старый постоянный ключ) и из окружения — не принимается."""

    KEY = "Emergency-" + "0a1b2c3d" * 6

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ga-key-", dir=TMP_ROOT)
        self.saved = (server.ADMIN_TOKEN_FILE, server.ADMIN_TOKEN_META, server.TRUST_LOCAL_CONSOLE)
        server.ADMIN_TOKEN_FILE = type(self.saved[0])(os.path.join(self.dir, "token"))
        server.ADMIN_TOKEN_META = type(self.saved[0])(os.path.join(self.dir, "token.meta"))
        server._TOKEN_USED.clear()

    def tearDown(self):
        server.ADMIN_TOKEN_FILE, server.ADMIN_TOKEN_META, server.TRUST_LOCAL_CONSOLE = self.saved
        os.environ.pop("GOAMNEZIA_ADMIN_TOKEN", None)
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, key=None, ttl=3600, created_ago=0, meta=True, mode=0o600, meta_text=None):
        now = int(time.time())
        items = [(str(server.ADMIN_TOKEN_FILE), (key or self.KEY) + "\n")]
        if meta:
            items.append((str(server.ADMIN_TOKEN_META),
                          meta_text if meta_text is not None else json.dumps({"created": now - created_ago, "expires": now - created_ago + ttl})))
        for path, text in items:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.chmod(path, mode)

    def handler(self, path, token="", header=False, public=False, ip="127.0.0.1"):
        h = server.AdminHandler.__new__(server.AdminHandler)
        h.command = "GET"
        h.headers = {"Host": "127.0.0.1:1985"}
        if public:
            h.headers["X-Forwarded-Host"] = "example.test"
        if token and header:
            h.headers["X-GoAmnezia-Token"] = token
        h.client_address = (ip, 40000)
        h._who = ""
        h._issue_session = None
        h.sent = []
        h.send_json = lambda payload, status=200, extra_headers=None: h.sent.append((status, payload))
        h.send_login_page = lambda notice="": h.sent.append((200, {"login_page": True, "notice": notice}))
        url = path + ("?token=%s" % urllib.parse.quote(token) if token and not header else "")
        return h, urllib.parse.urlparse(url)

    def guard(self, *a, **kw):
        h, parsed = self.handler(*a, **kw)
        with contextlib.redirect_stderr(io.StringIO()):
            ok = server.AdminHandler.guard_request(h, parsed)
        return ok, h

    def test_state_needs_meta_in_time_and_private_files(self):
        self.assertEqual(server.emergency_token_state(), ("", "no_token"))
        self.write(meta=False)
        self.assertEqual(server.emergency_token_state(), ("", "no_meta"))
        self.write()
        self.assertEqual(server.emergency_token_state(), (self.KEY, ""))
        self.write(ttl=3600, created_ago=3601)
        self.assertEqual(server.emergency_token_state(), ("", "expired"))
        # meta на неделю — это снова постоянный ключ; meta из будущего и мусор — тоже не ключ.
        self.write(ttl=7 * 86400)
        self.assertEqual(server.emergency_token_state(), ("", "bad_meta"))
        self.write(ttl=3600, created_ago=-3600)
        self.assertEqual(server.emergency_token_state(), ("", "bad_meta"))
        for text in ("{not json", "[]", '{"created": "1", "expires": 9999999999}', '{"created": true, "expires": 9999999999}'):
            with self.subTest(meta=text):
                self.write(meta_text=text)
                self.assertEqual(server.emergency_token_state(), ("", "bad_meta"))
        # Файл, открытый группе или всем, — не ключ: его мог прочесть кто угодно.
        self.write(mode=0o644)
        self.assertEqual(server.emergency_token_state()[0], "")
        self.write(key="short")
        self.assertEqual(server.emergency_token_state(), ("", "no_token"))

    def test_tunnel_key_signs_in_once_and_burns(self):
        for trust in (True, False):
            with self.subTest(trust=trust):
                server.TRUST_LOCAL_CONSOLE = trust
                key = "%s-%s" % (self.KEY, int(trust))
                self.write(key=key)
                ok, h = self.guard("/", token=key)
                self.assertTrue(ok)
                self.assertEqual(h._who, "token")
                self.assertTrue(h._issue_session, "сессия после входа — по прежним правилам")
                self.assertFalse(os.path.exists(str(server.ADMIN_TOKEN_FILE)), "ключ сгорел")
                self.assertFalse(os.path.exists(str(server.ADMIN_TOKEN_META)))
                ok, h = self.guard("/api/users", token=key, header=True)
                self.assertEqual(ok, trust, "сгоревший ключ второй раз не пускает; доверенный туннель пускает и так")
                if not trust:
                    self.assertEqual(h.sent[0][0], 401)
                    self.assertEqual(h.sent[0][1]["code"], "token_invalid")

    def test_a_key_that_could_not_be_erased_still_burns(self):
        """Файл не стёрся (диск, права) — принятый ключ второй раз не входит всё равно: отпечаток помнит панель."""
        self.write()
        saved = server.emergency_token_burn
        server.emergency_token_burn = lambda: None
        try:
            self.assertEqual(server.emergency_token_redeem(self.KEY), (True, ""))
            self.assertEqual(server.emergency_token_redeem(self.KEY), (False, "used"))
        finally:
            server.emergency_token_burn = saved

    def test_domain_refuses_even_a_fresh_key_and_keeps_it(self):
        self.write()
        for path, header in (("/", False), ("/api/users", True)):
            with self.subTest(path=path):
                ok, h = self.guard(path, token=self.KEY, header=header, public=True)
                self.assertFalse(ok)
                if header:
                    status, payload = h.sent[0]
                    self.assertEqual((status, payload["code"]), (401, "token_tunnel_only"))
                    self.assertEqual(payload["texts"]["ru"], server.LOGIN_STRINGS["ru"]["tok_net"])
                    self.assertIn("SSH", payload["texts"]["en"])
                else:
                    self.assertEqual(h.sent[0][1], {"login_page": True, "notice": "token_net"})
        self.assertEqual(server.emergency_token_state(), (self.KEY, ""), "отказ с домена ключ не жжёт")
        # Не петля и без заголовков прокси (панель выставили наружу мимо nginx) — тоже не туннель.
        ok, h = self.guard("/", token=self.KEY, ip="203.0.113.9")
        self.assertEqual((ok, h.sent[0][1].get("notice")), (False, "token_net"))

    def test_expired_legacy_wrong_and_env_keys_are_refused_over_an_untrusted_tunnel(self):
        server.TRUST_LOCAL_CONSOLE = False
        cases = (("expired", dict(ttl=3600, created_ago=7200), self.KEY), ("no meta", dict(meta=False), self.KEY),
                 ("wrong", {}, "Wrong-Key-" + "9" * 40))
        for label, kw, presented in cases:
            with self.subTest(case=label):
                self.write(**kw)
                ok, h = self.guard("/", token=presented)
                self.assertFalse(ok)
                self.assertEqual(h.sent[0][1], {"login_page": True, "notice": "token_bad"})
                self.assertTrue(os.path.exists(str(server.ADMIN_TOKEN_FILE)), "непринятый ключ не трогаем")
        # Постоянного ключа из окружения больше нет.
        shutil.rmtree(self.dir)
        os.makedirs(self.dir)
        os.environ["GOAMNEZIA_ADMIN_TOKEN"] = self.KEY
        ok, h = self.guard("/api/users", token=self.KEY, header=True)
        self.assertFalse(ok)
        self.assertEqual(h.sent[0][1]["code"], "token_invalid")

    def test_one_key_opens_one_session_even_in_parallel(self):
        self.write()
        results = []
        barrier = threading.Barrier(8)

        def go():
            barrier.wait()
            results.append(server.emergency_token_redeem(self.KEY)[0])
        threads = [threading.Thread(target=go) for _ in range(8)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        self.assertEqual(results.count(True), 1)

    def test_login_strings_speak_both_languages(self):
        for key in ("lg_tok_net_b", "lg_tok_net_t", "lg_tok_bad_b", "lg_tok_bad_t", "tok_net", "tok_bad"):
            self.assertTrue(server.LOGIN_STRINGS["ru"][key] and server.LOGIN_STRINGS["en"][key], key)
        self.assertIn("только через SSH-туннель", server.LOGIN_STRINGS["ru"]["lg_tok_net_b"])
        self.assertIn("only over an SSH tunnel", server.LOGIN_STRINGS["en"]["lg_tok_net_b"])


@unittest.skipUnless(shutil.which("node"), "нет node — страницу входа в игрушечном DOM не проверить")
class LoginNoticeDomTests(unittest.TestCase):
    """SEC50-08 на самой странице входа (игрушечный DOM tests/fixtures/panel_dom.js под node): отказ аварийному
    ключу — первой строкой страницы, словами, на языке страницы; без отказа строки нет."""

    SCRIPT = r"""
const { boot } = require(process.argv[2]);
(async () => {
  const P = await boot({ root: process.argv[3], hash: "" });
  const n = P.$("#note");
  console.log(JSON.stringify({ hidden: n.hidden, text: n.textContent.replace(/\s+/g, " ").trim(), errors: P.errors }));
  P.close();
})().catch((e) => console.log(JSON.stringify({ fatal: String(e && e.stack || e) })));
"""

    def note(self, notice, lang):
        write_env("")
        html = server.render_login_page(lang, public=True, notice=notice).decode("utf-8")
        with tempfile.TemporaryDirectory(dir=TMP_ROOT) as tmp:
            static = os.path.join(tmp, "admin-web", "static")
            os.makedirs(static)
            os.makedirs(os.path.join(tmp, "lib"))
            shutil.copy(ACTIONS_JSON, os.path.join(tmp, "lib", "actions.json"))
            with open(os.path.join(static, "index.html"), "w", encoding="utf-8") as fh:
                fh.write(html)
            with open(os.path.join(static, "app.js"), "w", encoding="utf-8") as fh:
                fh.write("")
            script = os.path.join(tmp, "note.js")
            with open(script, "w", encoding="utf-8") as fh:
                fh.write(self.SCRIPT)
            out = subprocess.run([shutil.which("node"), script, os.path.join(ROOT, "tests", "fixtures", "panel_dom.js"), tmp],
                                 capture_output=True, text=True, timeout=60).stdout
        rec = json.loads([ln for ln in out.splitlines() if ln.startswith("{")][-1])
        self.assertNotIn("fatal", rec, rec.get("fatal"))
        self.assertEqual(rec["errors"], [])
        return rec

    def test_refusals_are_the_first_line(self):
        for notice, lang in (("token_net", "ru"), ("token_net", "en"), ("token_bad", "ru"), ("token_bad", "en")):
            with self.subTest(notice=notice, lang=lang):
                rec = self.note(notice, lang)
                key = "lg_tok_net" if notice == "token_net" else "lg_tok_bad"
                self.assertFalse(rec["hidden"])
                self.assertEqual(rec["text"], "%s %s" % (server.LOGIN_STRINGS[lang][key + "_b"], server.LOGIN_STRINGS[lang][key + "_t"]))
        self.assertTrue(self.note("", "ru")["hidden"])


class InstallAdminEmergencyKeyTests(unittest.TestCase):
    """SEC50-08: установщик панели ключ не заводит и не печатает — одна строка, где взять аварийный вход в SSH;
    старый постоянный ключ (файл без token.meta) при обновлении убирает, свежий ключ SSH-меню (с meta) не трогает."""

    SRC = os.path.join(ROOT, "admin-web", "install_admin.sh")

    @classmethod
    def setUpClass(cls):
        cls.src = read_text(cls.SRC)

    def test_no_permanent_key_is_created_or_printed(self):
        for gone in ("openssl rand -hex 32", "secrets.token_hex", "?token=", "cat ${TOKEN_FILE}", "token_created"):
            self.assertNotIn(gone, self.src)
        self.assertIn('I18N_RU[done_token]="Аварийный вход — «Настройки → Веб-панель, админы бота, ИИ-агенты → Панель и мини-апп → '
                      'Аварийный ключ» в SSH"', self.src)
        self.assertIn('I18N_EN[done_token]="Emergency sign-in — «Settings → Web panel, bot admins, AI agents → Panel and mini app → '
                      'Emergency key» in SSH"', self.src)

    @unittest.skipUnless(shutil.which("bash"), "нет bash")
    def test_legacy_key_is_removed_and_a_fresh_one_kept(self):
        block = self.src[self.src.index('TOKEN_FILE="$ADMIN_DIR/token"'):]
        block = block[:block.index("\nfi\n") + 4]
        for with_meta, survives in ((False, False), (True, True)):
            with self.subTest(meta=with_meta), tempfile.TemporaryDirectory() as tmp:
                with open(os.path.join(tmp, "token"), "w") as fh:
                    fh.write("x" * 64)
                if with_meta:
                    with open(os.path.join(tmp, "token.meta"), "w") as fh:
                        fh.write('{"created": 1, "expires": 2}')
                script = 'log_info() { :; }\nt() { :; }\nADMIN_DIR="$1"\n' + block
                subprocess.run(["bash", "-c", script, "x", tmp], check=True, timeout=30)
                self.assertEqual(os.path.exists(os.path.join(tmp, "token")), survives)


class Modes060RoutesTests(unittest.TestCase):
    """0.6.0 (docs/MODES_047.md, «Договор 0.6.0»): lazy-check, lazy-setup и pro-line идут общим маршрутом /api/action/…,
    как прочие действия каталога; сервер добавляет им только время ожидания (BRIDGE_TIMEOUT_BY_ACTION). Каталог ядра
    этих действий может ещё не знать — тест подставляет свой, с полями договора, поверх настоящего."""

    NEW = {
        "lazy-check": {"readonly": True, "destructive": False, "params": {"domain": {"ref": "domain", "required": True}}},
        "lazy-setup": {"readonly": False, "destructive": True,
                       "params": {"domain": {"ref": "domain", "required": True}, "keys": {"ref": "lazy_keys", "required": False}}},
        "pro-line": {"readonly": False, "destructive": True,
                     "params": {"profile": {"ref": "profile", "required": True},
                                "adopted_confirm": {"ref": "adopted_confirm", "required": False}}},
    }

    def setUp(self):
        with open(ACTIONS_JSON, encoding="utf-8") as fh:
            spec = json.load(fh)
        for name, adef in self.NEW.items():
            spec["actions"].setdefault(name, dict(adef, description={"ru": name, "en": name}))
        spec["param_types"].setdefault("lazy_keys", {"type": "integer", "minimum": 1, "maximum": 20})
        self.catalog = os.path.join(TMP_ROOT, "actions-060-%d.json" % id(self))
        with open(self.catalog, "w", encoding="utf-8") as fh:
            json.dump(spec, fh, ensure_ascii=False)
        self.saved = server.ACTIONS_JSON
        server.ACTIONS_JSON = type(self.saved)(self.catalog)
        server._ACTIONS_CACHE["spec"] = None
        self.args_file = os.path.join(TMP_ROOT, "args-060-%d.txt" % id(self))
        os.environ["FAKE_ADMIN_ARGS_FILE"] = self.args_file

    def tearDown(self):
        server.ACTIONS_JSON = self.saved
        server._ACTIONS_CACHE["spec"] = None
        os.environ.pop("FAKE_ADMIN_ARGS_FILE", None)

    def last_argv(self):
        with open(self.args_file, encoding="utf-8") as fh:
            return fh.read().strip().splitlines()[-1].split("\t")

    def test_core_refusals_are_not_server_errors(self):
        """Отказы lib/modes.sh — неверный запрос или состояние сервера, а не 500 (отказ проверки несёт plan и reasons)."""
        for code, status in (("invalid_keys", 400), ("domain_is_ip", 400), ("idn_failed", 400), ("dns_not_here", 409),
                             ("port_busy", 409), ("app_container_busy", 409), ("awg2_container_busy", 409),
                             ("adopt_failed", 409), ("mode_not_pro", 409), ("adopted_keyless", 409)):
            self.assertEqual(server.bridge_http_status({"status": "error", "code": code}), status, code)

    def test_long_scenario_waits_long_enough(self):
        self.assertGreaterEqual(server.BRIDGE_TIMEOUT_BY_ACTION["lazy-setup"], 1800)
        self.assertGreaterEqual(server.BRIDGE_TIMEOUT_BY_ACTION["lazy-check"], 60)
        self.assertLessEqual(server.BRIDGE_TIMEOUT_BY_ACTION["lazy-check"], 300, "проверка — чтение, ждать её полчаса незачем")
        self.assertGreaterEqual(server.BRIDGE_TIMEOUT_BY_ACTION["pro-line"], 600)

    def test_lazy_check_is_a_plain_read(self):
        r = server.run_action("lazy-check", {"domain": "vpn.example.com"}, "tester")
        self.assertEqual(r["status"], "success")
        self.assertEqual(self.last_argv()[:3], ["--action=lazy-check", "--json", "--domain=vpn.example.com"])
        # Домен — как его ввёл человек (domain_input ядра: https://, путь, кириллица — ядро чистит само; панель всё равно
        # шлёт punycode). Пробел, обратная косая и управляющие символы — отказ до моста.
        self.assertEqual(server.validate_params("lazy-check", {"domain": "пример.рф"}), {"domain": "пример.рф"})
        for bad in ("vpn example.com", "vpn\\example.com", "vpn.example.com\x07"):
            with self.subTest(bad=bad), self.assertRaises(server.ActionError):
                server.validate_params("lazy-check", {"domain": bad})

    def test_lazy_setup_takes_the_domain_as_confirm(self):
        for confirm in (None, "other.example.com"):
            with self.subTest(confirm=confirm), self.assertRaises(server.ActionError) as ctx:
                server.run_action("lazy-setup", {"domain": "vpn.example.com", "keys": "7"}, "tester", confirm=confirm)
            self.assertEqual(ctx.exception.code, "confirm_required")
        with self.assertRaises(server.ActionError):
            server.validate_params("lazy-setup", {"domain": "vpn.example.com", "keys": "21"})
        r = server.run_action("lazy-setup", {"domain": "vpn.example.com", "keys": "7"}, "tester", confirm="vpn.example.com")
        self.assertEqual(r["status"], "success")
        self.assertIn("--keys=7", self.last_argv())

    def test_pro_line_passes_the_adopted_confirmation(self):
        """F-3: confirm = профиль; согласие на подхваченный профиль уходит мосту --adopted-confirm, а чужое имя — отказ до моста."""
        with self.assertRaises(server.ActionError):
            server.run_action("pro-line", {"profile": "awg2"}, "tester")
        with self.assertRaises(server.ActionError):
            server.validate_params("pro-line", {"profile": "awg2", "adopted_confirm": "awg9"})
        server.run_action("pro-line", {"profile": "awg2", "adopted_confirm": "awg2"}, "tester", confirm="awg2")
        argv = self.last_argv()
        self.assertIn("--profile=awg2", argv)
        self.assertIn("--adopted-confirm=awg2", argv)


if __name__ == "__main__":
    unittest.main()
