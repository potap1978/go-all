# -*- coding: utf-8 -*-
"""Юнит-тесты Telegram-бота goamnezia-bot/bot.py — без сети и без Telegram.

Чистые функции бота работают без python-telegram-bot: модуль импортируется с
заглушками, поэтому разбор ответа моста, typed-confirm, валидация, пагинация и
QR-файлы проверяются системным python3. Тесты, которым нужны клавиатуры PTB,
пропускаются (skipTest), если библиотека не установлена, — их гоняют в venv:

    /path/to/venv/bin/python -m unittest tests/test_bot_features.py

Мост подменён заглушкой tests/fixtures/fake_action_bot.sh (пути и каталоги —
через окружение, ДО импорта бота: он читает их на уровне модуля).
"""
import ast
import asyncio
import base64
import contextlib
import json
import logging
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import unittest
from unittest.mock import patch
import datetime as dt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOT_SRC = os.path.join(ROOT, "goamnezia-bot")
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "fake_action_bot.sh")
ACTIONS_JSON = os.path.join(ROOT, "lib", "actions.json")

# Каталоги бота и продукта — во временные: user_langs.json, .env, очередь
# уведомлений не должны трогать /opt даже при запуске тестов на сервере.
TMP_ROOT = tempfile.mkdtemp(prefix="goamnezia-bot-test-")
os.environ["GOAMNEZIA_DIR"] = os.path.join(TMP_ROOT, "goamnezia")
os.environ["GOAMNEZIA_BOT_DIR"] = os.path.join(TMP_ROOT, "bot")
os.environ["GOAMNEZIA_NOTIFY_DIR"] = os.path.join(TMP_ROOT, "notify")
os.environ["GOAMNEZIA_ACTION_BIN"] = FIXTURE
os.environ["ALLOWED_IDS"] = ""
os.environ.pop("BOT_TOKEN", None)
os.makedirs(os.environ["GOAMNEZIA_BOT_DIR"])
os.makedirs(os.environ["GOAMNEZIA_NOTIFY_DIR"])
if BOT_SRC not in sys.path:
    sys.path.insert(0, BOT_SRC)

import bot  # noqa: E402 — после настройки окружения
import i18n  # noqa: E402

# Подписи профилей заглушки моста (DUP-LINE-01: линия и порт, без имени места): awg2 — 2.0 на UDP 443, awg3 — 3.1
# на 39001. Экраны и уведомления без status берут их из кэша линий — его наполняет setUpModule тем же status.
FX20 = "AmneziaWG 2.0 · порт 443"
FX31 = "AmneziaWG 3.1 · порт 39001"


def _prime_lines():
    """Кэш подписей профилей — как у заглушки моста: тесты со своим status (порты 443/5388 и т. п.) его меняют."""
    bot.remember_lines(run(bot.run_bot_action("status", timeout=60)))


def setUpModule():
    _prime_lines()


PNG_1x1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAX+XDSwAAAABJRU5ErkJggg=="
)


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        # Раздача файла заводит фоновую задачу «удалить сообщение с адресом
        # через TTL». В тесте ждать пять минут нечего: гасим всё, что осталось,
        # иначе asyncio ругается «Task was destroyed but it is pending».
        pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()


def tearDownModule():
    shutil.rmtree(TMP_ROOT, ignore_errors=True)


def reset_stranger_limits():
    """Счётчики ответов незнакомцам (SEC-B3) — с нуля: тесты идут быстрее минуты, и без
    сброса чужие из соседних тестов съедали бы общий лимит того, кто проверяет ответ."""
    bot._STRANGER_HITS.clear()
    del bot._STRANGER_ALL[:]


def pairing_files():
    """Файлы привязки администратора: маркер, код, окно, кандидаты, подтверждение из SSH."""
    return (bot.ADMIN_CLAIMED_MARKER, bot.ADMIN_CLAIM_CODE_FILE, bot.ADMIN_PAIRING_UNTIL_FILE,
            bot.ADMIN_CANDIDATES_FILE, bot.ADMIN_CONFIRMED_FILE)


def open_pairing_window(seconds=600, until=None):
    """Окно привязки, как его открывает SSH: конец окна (epoch, секунды) в файле."""
    with open(bot.ADMIN_PAIRING_UNTIL_FILE, "w", encoding="utf-8") as fh:
        fh.write("%d\n" % (until if until is not None else time.time() + seconds))


@contextlib.contextmanager
def waiting_for_admin(code="48291375", window=True):
    """Бот без единого админа; code — содержимое файла кода (None — файла нет), window — окно привязки
    открыто (его открывает SSH; код принимается только в нём). После — всё как было: список админов,
    .env, OWNER_ID, файлы привязки."""
    saved_ids, saved_order = set(bot.ALLOWED_IDS), list(bot.ADMIN_ORDER)
    saved_env = {k: os.environ.get(k) for k in ("ALLOWED_IDS", "OWNER_ID")}
    env_existed = os.path.exists(bot.ENV_FILE)
    env_before = open(bot.ENV_FILE, encoding="utf-8").read() if env_existed else None
    bot.ALLOWED_IDS.clear()
    del bot.ADMIN_ORDER[:]
    bot._WAITING_FOR_ADMIN = True
    reset_stranger_limits()
    bot._CLAIM_VERIFIED.clear()
    bot._CLAIM_FAILS.clear()
    for path in pairing_files():
        if os.path.exists(path):
            os.unlink(path)
    if code is not None:
        with open(bot.ADMIN_CLAIM_CODE_FILE, "w", encoding="utf-8") as fh:
            fh.write(code + "\n")
    if window:
        open_pairing_window()
        # Бот это окно уже видел: «верные коды», которые тест кладёт в память сам, — из этого окна
        bot.pairing_sync_window()
    try:
        yield
    finally:
        bot.ALLOWED_IDS.clear()
        bot.ALLOWED_IDS.update(saved_ids)
        bot.ADMIN_ORDER[:] = saved_order
        bot._WAITING_FOR_ADMIN = len(bot.ALLOWED_IDS) == 0
        bot._CLAIM_VERIFIED.clear()
        bot._CLAIM_FAILS.clear()
        reset_stranger_limits()
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        if env_existed:
            with open(bot.ENV_FILE, "w", encoding="utf-8") as fh:
                fh.write(env_before)
        elif os.path.exists(bot.ENV_FILE):
            os.unlink(bot.ENV_FILE)
        for path in pairing_files():
            if os.path.exists(path):
                os.unlink(path)


def profiles_created(args_file, installed):
    """В какие линейки лёг ключ — по записанным вызовам моста, а не по коду ответа.

    Правило ядра: `--profiles` нет в argv вовсе → «во все установленные»; пустая
    строка → ядро отказывает, линеек ноль. Дыра 1c.7 в том и была, что ответ
    приходил успешный, а ключи тихо расходились по трём линейкам.
    """
    got = []
    with open(args_file, encoding="utf-8") as fh:
        for line in fh.read().splitlines():
            argv = line.split("\t")
            if "--action=user-add" not in argv:
                continue
            value = None
            for a in argv:
                if a.startswith("--profiles="):
                    value = a.split("=", 1)[1]
            if value is None:
                got.extend(installed)
            else:
                got.extend([p for p in value.split(",") if p])
    return sorted(set(got))


class ImportSideEffectsTests(unittest.TestCase):
    def test_import_is_side_effect_free(self):
        """Без BOT_TOKEN модуль импортируется; токен нужен только main()."""
        self.assertFalse(os.getenv("BOT_TOKEN"))
        self.assertTrue(callable(bot.main))
        self.assertEqual(bot.ACTION_BIN, FIXTURE)
        # пустой ALLOWED_IDS = никого не пускать, а не «всем можно»
        self.assertFalse(bot.is_user_allowed(123))
        self.assertTrue(bot._WAITING_FOR_ADMIN)

    def test_fixture_is_executable(self):
        self.assertTrue(os.access(FIXTURE, os.X_OK), "chmod +x tests/fixtures/fake_action_bot.sh")


class BridgeParseTests(unittest.TestCase):
    def test_last_json_line_with_status_wins(self):
        out = ('noise\n{"note": "no status"}\n{"status":"success","message":"a"}\n'
               '{"status":"error","message":"b","code":"x"}\n\ntrailing garbage\n')
        parsed = bot.parse_bridge_output(out)
        self.assertEqual(parsed["status"], "error")
        self.assertEqual(parsed["code"], "x")

    def test_no_json_returns_none(self):
        self.assertIsNone(bot.parse_bridge_output("just text\n{broken\n"))
        self.assertIsNone(bot.parse_bridge_output(""))
        self.assertIsNone(bot.parse_bridge_output('{"note": "no status"}'))

    def test_ok_shape_is_read_like_the_usual_one(self):
        """Договор name-suggest описан формой {"ok": true, "data": {…}}: у этого действия бот читает и её,
        и обычную. У остальных строка с ok без status — не ответ (отчёт зависимостей, сверка ключей):
        упавшее действие, успевшее её напечатать, остаётся ошибкой транспорта, а не «успехом»."""
        out = '{"note": 1}\n{"ok":true,"data":{"id":"foggy_london","label":"Туманный Лондон"}}\n'
        self.assertIsNone(bot.parse_bridge_output(out))
        self.assertEqual(bot.BRIDGE_OK_SHAPE_ACTIONS, ("name-suggest",))
        parsed = bot.parse_bridge_output(out, ok_shape=True)
        self.assertTrue(bot.bridge_ok(parsed))
        self.assertEqual((parsed["id"], parsed["label"]), ("foggy_london", "Туманный Лондон"))
        self.assertEqual(bot.name_suggestion(parsed), {"id": "foggy_london", "label": "Туманный Лондон"})
        refused = bot.parse_bridge_output('{"ok":false,"error":{"code":"unknown_action","message":"нет такого"}}',
                                          ok_shape=True)
        self.assertEqual((refused["status"], refused["code"]), ("error", "unknown_action"))
        self.assertEqual(bot.parse_bridge_output('{"ok":false,"error":"timeout"}', ok_shape=True)["code"], "timeout")
        # чужие объекты с ok — не ответ и здесь; последняя строка со status по-прежнему главнее
        for stray in ('{"ok":true,"profiles":"core","crit":0,"missing":[]}',
                      '{"profile":"awg2","config":"","state":"","conf":"","ok":false}'):
            self.assertIsNone(bot.parse_bridge_output(stray, ok_shape=True), stray)
        self.assertEqual(bot.parse_bridge_output('{"ok":true,"data":{}}\n{"status":"error","code":"x"}',
                                                 ok_shape=True)["code"], "x")

    def test_name_suggestion_takes_only_a_valid_id(self):
        ok = {"status": "success", "id": "sunny_manila", "label": "Солнечная Манила"}
        self.assertEqual(bot.name_suggestion(ok), {"id": "sunny_manila", "label": "Солнечная Манила"})
        self.assertEqual(bot.name_suggestion({"status": "success", "name": "snowy_ottawa"}),
                         {"id": "snowy_ottawa", "label": ""})
        self.assertEqual(bot.name_suggestion({"status": "success", "suggestion": {"id": "a_b", "label": "A\nB"}}),
                         {"id": "a_b", "label": ""}, "подпись с переводом строки не годится — без подписи")
        for bad in ({"status": "success", "id": "Туманный"}, {"status": "success", "id": "a b"},
                    {"status": "success", "id": "x" * 33}, {"status": "success"}, {"status": "error", "id": "ok"},
                    {"status": "success", "message": "ok: name-suggest", "action": "name-suggest", "name": ""}):
            self.assertIsNone(bot.name_suggestion(bad), bad)

    def test_argv_underscore_to_dash_and_empty_kept(self):
        argv = bot.build_bridge_argv("user-add", {"name": "anna", "quota_gb": 5, "days": None, "host": ""})
        self.assertEqual(argv[0], FIXTURE)
        self.assertEqual(argv[1], "--action=user-add")
        self.assertEqual(argv[2], "--json")
        self.assertIn("--quota-gb=5", argv)
        self.assertIn("--host=", argv)
        self.assertFalse(any(a.startswith("--days") for a in argv))

    def test_run_success_and_fields(self):
        r = run(bot.run_bot_action("status", timeout=20))
        self.assertTrue(bot.bridge_ok(r))
        self.assertEqual(r["mode"], "lite")
        # Порядок SSH-меню и панели (lib/common.sh PROFILE_IDS="awg3 awg2 awg1"), а не порядок ответа
        self.assertEqual([p["profile"] for p in bot.status_profiles(r)], ["awg3", "awg2"])
        self.assertEqual(bot.installed_profiles(r), ["awg3", "awg2"])
        self.assertEqual(bot.profile_info(r, "awg2")["port"], 443)

    def test_run_records_args_and_caller(self):
        args_file = os.path.join(TMP_ROOT, "args.txt")
        env_file = os.path.join(TMP_ROOT, "who.txt")
        os.environ["FAKE_BOT_ARGS_FILE"] = args_file
        os.environ["FAKE_BOT_ENV_FILE"] = env_file
        try:
            r = run(bot.run_bot_action("user-quota", timeout=20, who=42, name="anna", quota_gb=7))
        finally:
            os.environ.pop("FAKE_BOT_ARGS_FILE", None)
            os.environ.pop("FAKE_BOT_ENV_FILE", None)
        self.assertTrue(bot.bridge_ok(r))
        with open(args_file, encoding="utf-8") as fh:
            recorded = fh.read().split("\t")
        self.assertIn("--action=user-quota", recorded)
        self.assertIn("--quota-gb=7", recorded)
        self.assertIn("--name=anna", recorded)
        with open(env_file, encoding="utf-8") as fh:
            self.assertEqual(fh.read().strip(), "bot:42")

    def test_run_error_status(self):
        os.environ["FAKE_BOT_STATUS"] = "error"
        try:
            r = run(bot.run_bot_action("status", timeout=20))
        finally:
            os.environ.pop("FAKE_BOT_STATUS", None)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["code"], "forced_error")
        self.assertIn("forced_error", bot.error_text(None, r))

    def test_run_transport_error_without_json(self):
        os.environ["FAKE_BOT_NO_JSON"] = "1"
        try:
            r = run(bot.run_bot_action("status", timeout=20))
        finally:
            os.environ.pop("FAKE_BOT_NO_JSON", None)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["code"], "transport")

    def test_run_timeout(self):
        os.environ["FAKE_BOT_SLEEP"] = "3"
        try:
            r = run(bot.run_bot_action("status", timeout=1))
        finally:
            os.environ.pop("FAKE_BOT_SLEEP", None)
        self.assertEqual(r["code"], "timeout")

    def test_run_missing_binary_is_transport(self):
        old = bot.ACTION_BIN
        bot.ACTION_BIN = os.path.join(TMP_ROOT, "no-such-install.sh")
        try:
            r = run(bot.run_bot_action("status", timeout=5))
        finally:
            bot.ACTION_BIN = old
        self.assertEqual(r["code"], "transport")

    def test_not_implemented_maps_to_next_version_text(self):
        # У настоящего моста кода not_implemented больше нет, но показывать его
        # бот обязан — фикстура отвечает им на формат-ловушку "later".
        r = run(bot.run_bot_action("user-key", timeout=20, name="anna", profile="awg2", format="later"))
        self.assertEqual(r["code"], "not_implemented")
        self.assertEqual(bot.error_text(None, r), i18n.t(None, "err_not_implemented"))

    def test_upstream_and_import_texts(self):
        """Экраны спринта 4 на ответах моста: таблица обновлений с кнопками
        «Обновить/Откатить», импорт с «Подхватить»/«Сверить», сверка пиров."""
        r = run(bot.run_bot_action("upstream-check", timeout=20))
        text = bot.upstream_text(None, r)
        self.assertIn("5.0.2.1", text)
        self.assertIn("5.0.3.0", text)
        self.assertIn("3.1.20260828", text)
        self.assertIn("3.2.20260915", text)
        self.assertIn("CONTENT_PADDING_MAX", text)
        self.assertIn(i18n.t(None, "up_done_hint"), text)
        if bot.PTB_AVAILABLE:
            rows = bot.upstream_rows(None, r)
            data = [b.callback_data for row in rows for b in row]
            self.assertIn("up_upd~awg2", data)
            self.assertIn("up_rb~awg3", data)
            self.assertNotIn("up_upd~awg3", data)
            self.assertNotIn("up_rb~awg2", data)
        os.environ["FAKE_BOT_UPSTREAM"] = "unreachable"
        try:
            r = run(bot.run_bot_action("upstream-check", timeout=20))
        finally:
            os.environ.pop("FAKE_BOT_UPSTREAM", None)
        self.assertEqual(r["code"], "unreachable")
        text = bot.upstream_text(None, r)
        self.assertIn("unreachable", text)
        self.assertIn("3.1.20260828", text)                # текущие версии есть и без сети
        self.assertIn(i18n.t(None, "up_unreachable_hint"), text)
        if bot.PTB_AVAILABLE:
            data = [b.callback_data for row in bot.upstream_rows(None, r) for b in row]
            self.assertIn("up_rb~awg3", data)              # откат работает без сети
            self.assertNotIn("up_upd~awg2", data)
        self.assertEqual(bot.confirm_phrase("upstream-update", "awg2"), "UPDATE awg2")
        self.assertEqual(bot.confirm_phrase("upstream-rollback", "awg3"), "ROLLBACK awg3")

        r = run(bot.run_bot_action("import-scan", timeout=20))
        text = bot.import_scan_text(None, r)
        self.assertIn("amnezia-awg2", text)
        self.assertIn("amnezia-awg", text)
        # предложенный профиль — линией и портом контейнера, без имени места (DUP-LINE-01)
        self.assertIn("AmneziaWG 1.0 · порт 38000 · из приложения", text)
        self.assertNotIn("(awg1)", text)
        self.assertIn(i18n.t(None, "mt_import_reason_unsupported_container"), text)
        self.assertNotIn("SRVPUB", text)                   # ключи сервера в экран не идут
        if bot.PTB_AVAILABLE:
            data = [b.callback_data for row in bot.import_rows(None, r) for b in row]
            self.assertIn("imp_adopt~amnezia-awg", data)
            self.assertIn("imp_rec~awg2", data)
            self.assertNotIn("imp_adopt~amnezia-xray", data)
        self.assertTrue(bot.valid_container("amnezia-awg2"))
        self.assertFalse(bot.valid_container("nginx-proxy"))
        self.assertFalse(bot.valid_container("amnezia-AWG"))
        r = run(bot.run_bot_action("import-reconcile", timeout=20, profile="awg2", apply="off"))
        text = bot.reconcile_text(None, r)
        self.assertIn("Laptop", text)
        self.assertIn("10.8.2.7", text)
        self.assertIn("bob", text)
        self.assertNotIn(i18n.t(None, "mt_import_apply_btn"), text)
        # KEENETIC-I1-N (N2): пиры, которых сверка не подхватит, — причиной словами ядра (skipped[].message), а не
        # «добавлено 0» без слов: и при «совпадает», и в итоге подхвата. Текст из ответа экранируется.
        skip = {"key": "AbCdEfGhIjKl", "reason": "ip", "message": "Пир AbCdEfGhIjKl… пропущен: в AllowedIPs нет адреса IPv4 <x>"}
        for r in ({"profile": "awg2", "in_sync": True, "new": [], "gone": [], "skipped": [skip]},
                  {"profile": "awg2", "in_sync": False, "new": [{"name": "Laptop", "ip": "10.8.2.7"}], "gone": [],
                   "skipped": [skip], "applied": True, "added": 1, "deactivated": 0}):
            text = bot.reconcile_text(None, r)
            self.assertIn("⚠️ Пир AbCdEfGhIjKl… пропущен: в AllowedIPs нет адреса IPv4 &lt;x&gt;", text)
        text = bot.reconcile_text(None, {"profile": "awg2", "in_sync": True, "skipped": [{"message": ""}, "junk"]})
        self.assertNotIn("⚠️", text)

    # Действия договора docs/CLIENTS.md §9 (страница подключения и зеркало
    # сборок): ядро пишется параллельно, и пока их нет в actions.json, бот зовёт
    # их по таблице договора. Как только они появятся в каталоге, проверка
    # ниже станет для них обычной — читающие обязаны быть readonly, пишущие нет.
    # name-suggest (0.4.7, «🎲 Случайное имя») в каталоге с C47-1 — readonly, как
    # всё, что бот зовёт через run_bot_action.
    # 0.6.0 (docs/MODES_047.md «Договор 0.6.0»): lazy-check, lazy-setup и pro-line уже в каталоге (ядро 611cd07) —
    # сверяются наравне с остальными, без поблажек.
    # Короткие ссылки с 2FA через бота (договор docs/SUBSCRIPTION.md, 1.0.9): ядро пишется параллельно — чтения
    # (short-list, short-status, short-pending, short-bans) и записи (short-ensure создаёт код, short-replace-request,
    # short-decide, short-unban) по таблице договора, пока их нет в каталоге.
    CONTRACT_PENDING_READ = {"clients-list", "page-list", "mirror-list",
                             "short-list", "short-status", "short-pending", "short-bans"}
    CONTRACT_PENDING_WRITE = {"page-create", "page-url", "page-revoke", "page-revoke-all",
                              "mirror-fetch", "mirror-remove",
                              "short-ensure", "short-replace-request", "short-decide", "short-unban"}

    def test_modes_060_actions_are_in_the_real_catalog(self):
        """Ленивый Pro и линия на UDP 443 — действия настоящего каталога: чтение без замка, запись — под ним,
        а параметры, которые передаёт бот, каталог знает (домен, число ключей, профиль, согласие ACC-2)."""
        with open(ACTIONS_JSON, encoding="utf-8") as fh:
            actions = json.load(fh)["actions"]
        for name in ("lazy-check", "lazy-setup", "pro-line"):
            self.assertIn(name, actions)
        self.assertTrue(actions["lazy-check"]["readonly"])
        self.assertIn("lazy-check", bot.READONLY_ACTIONS)
        self.assertFalse(actions["lazy-setup"]["readonly"])
        self.assertFalse(actions["pro-line"]["readonly"])
        self.assertEqual(set(actions["lazy-setup"]["params"]), {"domain", "keys"})
        self.assertEqual(set(actions["pro-line"]["params"]), {"profile", "adopted_confirm"})
        self.assertLessEqual(bot.LAZY_KEYS, 20)

    def test_readonly_set_matches_actions_json(self):
        """Бот считает действие «без замка» только если actions.json согласен; все
        действия, которые бот зовёт, существуют в каталоге и мутации не readonly."""
        import re
        with open(ACTIONS_JSON, encoding="utf-8") as fh:
            spec = json.load(fh)
        known = set(spec["actions"])
        readonly = {name for name, a in spec["actions"].items() if a.get("readonly")}
        self.assertTrue(bot.READONLY_ACTIONS <= readonly, bot.READONLY_ACTIONS - readonly)
        with open(os.path.join(BOT_SRC, "bot.py"), encoding="utf-8") as fh:
            src = fh.read()
        called = set(re.findall(r'run_bot_action\("([a-z-]+)"', src))
        mutated = set(re.findall(r'mutate\("([a-z-]+)"', src))
        self.assertTrue(called and mutated)
        pending_read = self.CONTRACT_PENDING_READ - known
        pending_write = self.CONTRACT_PENDING_WRITE - known
        self.assertTrue((called | mutated) <= known | pending_read | pending_write,
                        (called | mutated) - known - pending_read - pending_write)
        # Мимо замка бота изменяющее действие идёт, только если ядро держит его своим замком (_action_own_lock в
        # install.sh — short-decide: state.json под flock); чтения — и так без замка.
        unlocked = set(bot.BOT_UNLOCKED_ACTIONS)
        with open(os.path.join(ROOT, "install.sh"), encoding="utf-8") as fh:
            body = re.search(r"_action_own_lock\(\) \{(.*?)\n\}", fh.read(), re.S)
        own_lock = {n for case in re.findall(r"^\s+([a-z|-]+)\) return 0", body.group(1) if body else "", re.M)
                    for n in case.split("|")}
        self.assertTrue(unlocked - readonly <= own_lock, unlocked - readonly - own_lock)
        self.assertTrue(called <= readonly | pending_read | unlocked, called - readonly - pending_read - unlocked)
        self.assertFalse(mutated & (readonly | pending_read), mutated & (readonly | pending_read))
        self.assertFalse(mutated & unlocked, "действие мимо замка бота зовётся и под ним: %s" % (mutated & unlocked))

    def test_key_caption_warns_when_the_user_is_disabled(self):
        """Мост помечает ключ выключенного пользователя (disabled: true): бот говорит, что ключ не подключится,
        как SSH-меню, а не выдаёт его молча."""
        warn = i18n.t(None, "key_disabled_warn")
        cap = bot.key_caption(None, "anna", "awg2", {"line": "2.0", "min_client": "4.8.12.7", "disabled": True})
        self.assertTrue(cap.startswith(warn), cap)
        cap_ok = bot.key_caption(None, "anna", "awg2", {"line": "2.0", "min_client": "4.8.12.7"})
        self.assertNotIn(warn, cap_ok)
        self.assertIn("4.8.12.7", cap_ok)

    def test_bridge_timeouts_are_known_actions(self):
        with open(ACTIONS_JSON, encoding="utf-8") as fh:
            spec = json.load(fh)
        for action in bot.BRIDGE_TIMEOUT_BY_ACTION:
            self.assertIn(action, spec["actions"])


class TypedConfirmTests(unittest.TestCase):
    def test_arm_check_expire(self):
        clock = {"t": 100.0}
        tc = bot.TypedConfirm(ttl=60, now=lambda: clock["t"])
        self.assertEqual(tc.check(1, "DELETE anna"), ("none", None))
        tc.arm(1, "user-del", "anna", bot.confirm_phrase("user-del", "anna"))
        self.assertEqual(tc.check(1, "delete anna")[0], "mismatch")
        self.assertIsNotNone(tc.pending(1))
        clock["t"] = 159.0
        verdict, rec = tc.check(1, "  DELETE anna \n")
        self.assertEqual(verdict, "ok")
        self.assertEqual(rec["target"], "anna")
        self.assertEqual(tc.check(1, "DELETE anna"), ("none", None))
        tc.arm(1, "profile-regen", "awg2", bot.confirm_phrase("profile-regen", "awg2"), {"preset": "quic"})
        clock["t"] = 300.0
        self.assertEqual(tc.check(1, "REGEN awg2"), ("expired", None))
        self.assertIsNone(tc.pending(1))

    def test_phrases_and_cancel(self):
        self.assertEqual(bot.confirm_phrase("profile-remove", "awg3"), "REMOVE awg3")
        self.assertEqual(bot.confirm_phrase("backup-restore", "b.tar.gz"), "RESTORE b.tar.gz")
        tc = bot.TypedConfirm(ttl=60)
        tc.arm(5, "user-del", "x", "DELETE x")
        tc.cancel(5)
        self.assertEqual(tc.check(5, "DELETE x"), ("none", None))
        self.assertEqual(bot.TYPED_CONFIRM_TTL, 60)

    def test_question_message_and_recent_acceptance(self):
        """BL-1 / RB-2 / RB3-1: у ожидания — сообщение с вопросом (на нём «Отмена»). Фраза принята — «Отмена» на ЭТОМ
        сообщении уже ничего не отменяет (10 минут); на другом вопросе — истёкшем, снятом, новом — отменяет, как прежде."""
        clock = {"t": 100.0}
        tc = bot.TypedConfirm(ttl=60, now=lambda: clock["t"])
        tc.attach(7, 7, 55)                       # ожидания нет — запоминать некуда
        tc.arm(7, "user-del", "x", "DELETE x")
        self.assertFalse(tc.accepted_recently(7, (7, 55)))
        tc.attach(7, 7, 55)
        verdict, rec = tc.check(7, "DELETE x")
        self.assertEqual((verdict, rec["msg"]), ("ok", (7, 55)))
        self.assertTrue(tc.accepted_recently(7, (7, 55)))
        self.assertFalse(tc.accepted_recently(7, (7, 56)), "чужое сообщение — не принятый вопрос")
        self.assertFalse(tc.accepted_recently(7))
        tc.arm(7, "user-del", "y", "DELETE y")       # новый вопрос на другом сообщении
        tc.attach(7, 7, 60)
        self.assertFalse(tc.accepted_recently(7, (7, 60)))
        self.assertTrue(tc.accepted_recently(7, (7, 55)), "у принятого вопроса — по-прежнему «уже принята»")
        clock["t"] = 170.0                           # Y истёк, фразу не набирали (воспроизведение RB3-1)
        self.assertIsNone(tc.pending(7))
        self.assertFalse(tc.accepted_recently(7, (7, 60)))
        tc.arm(7, "user-del", "z", "DELETE z")       # новый вопрос на том же сообщении — его «Отмена» отменяет
        tc.attach(7, 7, 55)
        self.assertFalse(tc.accepted_recently(7, (7, 55)))
        tc.cancel(7)
        self.assertTrue(tc.accepted_recently(7, (7, 55)))
        clock["t"] = 800.0
        self.assertFalse(tc.accepted_recently(7, (7, 55)))

    @unittest.skipUnless(bot.PTB_AVAILABLE, "python-telegram-bot не установлен")
    def test_note_confirm_message_only_for_the_typed_cancel(self):
        """Вопрос подтверждения узнаётся по кнопке tc_cancel~ — одно место для всех подтверждений фразой или портом."""
        saved = bot.CONFIRM
        bot.CONFIRM = bot.TypedConfirm(ttl=60)
        self.addCleanup(setattr, bot, "CONFIRM", saved)
        msg = type("M", (), {"chat_id": 9, "message_id": 77})()
        bot.CONFIRM.arm(9, "user-del", "x", "DELETE x")
        bot.note_confirm_message(9, msg, bot.InlineKeyboardMarkup([[bot.btn("Назад", "menu_users")]]))
        self.assertNotIn("msg", bot.CONFIRM.pending(9))
        bot.note_confirm_message(9, msg, bot.InlineKeyboardMarkup([[bot.btn("Отмена", "tc_cancel~menu_users")]]))
        self.assertEqual(bot.CONFIRM.pending(9)["msg"], (9, 77))


class QrDecodeTests(unittest.TestCase):
    def test_decode_png_b64(self):
        self.assertEqual(bot.decode_png_b64(base64.b64encode(PNG_1x1).decode()), PNG_1x1)
        self.assertIsNone(bot.decode_png_b64(base64.b64encode(b"not a png").decode()))
        self.assertIsNone(bot.decode_png_b64("###"))
        self.assertIsNone(bot.decode_png_b64(""))
        self.assertIsNone(bot.decode_png_b64(None))

    def test_write_qr_files_from_bridge_answer(self):
        r = run(bot.run_bot_action("user-key", timeout=20, name="anna", profile="awg2", format="all"))
        self.assertTrue(bot.bridge_ok(r))
        self.assertTrue(r["conf"].startswith("[Interface]"))
        self.assertTrue(r["vpn"].startswith("vpn://"))
        tmpdir = tempfile.mkdtemp(prefix="qr-")
        try:
            conf_png, vpn_pngs = bot.write_qr_files(r, tmpdir)
            self.assertTrue(conf_png and conf_png.endswith("conf.png"))
            # третий чанк заглушки — не PNG, он отбрасывается
            self.assertEqual([os.path.basename(p) for p in vpn_pngs], ["vpn-01.png", "vpn-02.png"])
            for path in [conf_png] + vpn_pngs:
                with open(path, "rb") as fh:
                    self.assertTrue(fh.read().startswith(bot.PNG_SIGNATURE))
                self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
        self.assertFalse(os.path.exists(tmpdir))

    def test_write_qr_files_empty_bundle(self):
        tmpdir = tempfile.mkdtemp(prefix="qr-")
        try:
            self.assertEqual(bot.write_qr_files({"qr_conf_png_b64": "", "qr_vpn_png_b64": None}, tmpdir), (None, []))
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


class ValidationTests(unittest.TestCase):
    def test_name(self):
        for ok in ("anna", "family-1", "A_b", "x" * 32):
            self.assertTrue(bot.valid_name(ok), ok)
        for bad in ("", "x" * 33, "анна", "a b", "a~b", "../x", "a;rm"):
            self.assertFalse(bot.valid_name(bad), bad)

    def test_label(self):
        self.assertTrue(bot.valid_label("Анна Иванова"))
        self.assertTrue(bot.valid_label("x" * 48))
        self.assertFalse(bot.valid_label(""))
        self.assertFalse(bot.valid_label("x" * 49))
        self.assertFalse(bot.valid_label("a\nb"))

    def test_dns_matches_the_bridge_pattern(self):
        """BOT-21: бот принимал «010.0.0.1», мост отвергал его bad_arg — после ввода."""
        with open(ACTIONS_JSON, encoding="utf-8") as fh:
            pattern = json.load(fh)["param_types"]["dns"]["pattern"]
        for sample in ("1.1.1.1", "94.140.14.14", "010.0.0.1", "1.1.1", "256.1.1.1", "8.8.8.8/32", "0.0.0.0", ""):
            self.assertEqual(bot.valid_dns(sample), bool(re.match(pattern, sample)), sample)

    def test_label_rejects_backslash_like_the_bridge(self):
        """SR-08: подпись печатается в терминале root через echo -e, и «\\033[2J» в ней —
        управляющая последовательность. Мост и модуль обратную косую черту больше не
        принимают, бот обязан отвергать её сам — иначе человек получает bad_arg моста."""
        for bad in ("a\\b", "\\033[2J", "evil\\e]0;x\\a", "\\"):
            self.assertFalse(bot.valid_label(bad), bad)
        with open(ACTIONS_JSON, encoding="utf-8") as fh:
            pattern = json.load(fh)["param_types"]["label"]["pattern"]
        for sample in ("Анна Иванова", "a\\b", "x" * 48, "tab\there", "ok/slash", "quote\"s"):
            self.assertEqual(bot.valid_label(sample), bool(re.match(pattern, sample)), sample)

    def test_port_mtu_int(self):
        self.assertTrue(bot.valid_port("443") and bot.valid_port("65535") and bot.valid_port("1"))
        self.assertFalse(bot.valid_port("0") or bot.valid_port("65536") or bot.valid_port("abc") or bot.valid_port("-1"))
        self.assertTrue(bot.valid_mtu("1280") and bot.valid_mtu("1500"))
        self.assertFalse(bot.valid_mtu("1199") or bot.valid_mtu("1501") or bot.valid_mtu("12800"))
        self.assertTrue(bot.valid_int("0", bot.MAX_QUOTA_GB) and bot.valid_int("100000", bot.MAX_QUOTA_GB))
        self.assertFalse(bot.valid_int("100001", bot.MAX_QUOTA_GB) or bot.valid_int("-1", 10) or bot.valid_int("1.5", 10))

    def test_subnet(self):
        for ok in ("10.8.2.0/24", "172.16.5.0/20", "192.168.1.0/22"):
            self.assertTrue(bot.valid_subnet(ok), ok)
        for bad in ("10.8.2.5/24", "10.8.2.0/25", "8.8.8.0/24", "172.32.0.0/24", "10.8.2.0"):
            self.assertFalse(bot.valid_subnet(bad), bad)

    def test_ip_list_is_exactly_as_strict_as_the_bridge(self):
        """Белый список адресов бот обязан проверять образцом моста, не мягче.

        Был мягче ровно на два вида опечаток: «203.0.113.5/» (косая черта есть,
        префикса нет) и «010.0.0.1» (ведущий ноль) бот принимал, а мост и
        ga_agents_ips_valid отвергали. Мастер выдачи к этому времени уже спросил
        всё до конца — и отказ прилетал на последнем шаге вместо подсказки на своём.
        """
        with open(ACTIONS_JSON, encoding="utf-8") as fh:
            bridge = re.compile(json.load(fh)["param_types"]["ip_list"]["pattern"])
        samples = ("203.0.113.5", "203.0.113.5/32", "203.0.113.5/1", "255.255.255.255",
                   "203.0.113.5,198.51.100.0/24", "0.0.0.0/8",
                   "203.0.113.5/", "203.0.113.5/0", "203.0.113.5/33", "203.0.113.5/01",
                   "010.0.0.1", "1.1.1.01", "256.0.0.1", "1.1.1", "1.1.1.1.1",
                   "203.0.113.5,", ",203.0.113.5", "203.0.113.5,,198.51.100.1",
                   "203.0.113.5, 198.51.100.1", "203.0.113.5 ", "203.0.113.5;rm -rf /",
                   "203.0.113.5/24/24", "вот.так.не.надо")
        for s in samples:
            self.assertEqual(bool(bot.valid_ip_list(s)), bool(bridge.match(s)),
                             "бот и мост расходятся на %r" % s)
        # Пустое — единственное законное расхождение: это «любые адреса», и мост
        # зовут вообще без аргумента, а не с пустым (cb_agent_add_mode).
        self.assertTrue(bot.valid_ip_list(""))
        self.assertFalse(bridge.match(""))

    def test_host_and_dns(self):
        self.assertTrue(bot.valid_host("") and bot.valid_host("vpn.example.com") and bot.valid_host("203.0.113.10"))
        self.assertFalse(bot.valid_host("VPN.EXAMPLE.COM") or bot.valid_host("-bad.com") or bot.valid_host("localhost"))
        self.assertTrue(bot.valid_ipv4("1.1.1.1") and bot.valid_ipv4("94.140.14.14"))
        self.assertFalse(bot.valid_ipv4("256.1.1.1") or bot.valid_ipv4("1.1.1") or bot.valid_ipv4("a.b.c.d"))

    def test_misc(self):
        self.assertTrue(bot.valid_profile("awg2") and not bot.valid_profile("awg9"))
        self.assertTrue(bot.valid_backup_file("goamnezia-backup-20260908_120000.tar.gz"))
        self.assertFalse(bot.valid_backup_file("../x.tar.gz") or bot.valid_backup_file("a/b") or bot.valid_backup_file(""))
        self.assertTrue(bot.valid_param_value("") and bot.valid_param_value("<b 0xc0><r 2>") and bot.valid_param_value("1000-2000"))
        self.assertFalse(bot.valid_param_value("x" * 513) or bot.valid_param_value("a;b") or bot.valid_param_value("тест"))
        self.assertTrue(bot.valid_admin_id("123456789") and not bot.valid_admin_id("0") and not bot.valid_admin_id("12a"))

    def test_param_tables(self):
        self.assertIn("S3", bot.param_keys_for_line("2.0"))
        self.assertNotIn("S3", bot.param_keys_for_line("1.0"))
        self.assertNotIn("I1", bot.param_keys_for_line("1.0"))
        self.assertIn("I1", bot.param_keys_for_line("1.5"))
        self.assertIn("HeaderProtectionKey", bot.param_keys_for_line("3.1"))
        self.assertNotIn("HeaderProtectionKey", bot.param_keys_for_line("2.0"))
        self.assertEqual(bot.param_scope("H1"), "server")
        self.assertEqual(bot.param_scope("Jc"), "client")
        with open(ACTIONS_JSON, encoding="utf-8") as fh:
            enum = set(json.load(fh)["param_types"]["param_key"]["enum"])
        self.assertEqual(set(bot.param_keys_for_line("3.1")), enum)


class UsersListTests(unittest.TestCase):
    def test_icons_follow_ssh_rules(self):
        now = dt.datetime(2026, 9, 8, tzinfo=dt.timezone.utc)
        self.assertEqual(bot.user_icon({"enabled": True}, now), "●")
        self.assertEqual(bot.user_icon({"enabled": False, "expires": "2020-01-01T00:00:00Z"}, now), "○")
        self.assertEqual(bot.user_icon({"enabled": True, "expires": "2020-01-01T00:00:00Z"}, now), "⏳")
        self.assertEqual(bot.user_icon({"enabled": True, "expires": "2030-01-01T00:00:00Z"}, now), "●")
        self.assertEqual(bot.user_icon({"enabled": True, "quota_bytes": 10, "traffic": {"rx": 5, "tx": 5}}, now), "▮")
        self.assertEqual(bot.user_icon({"enabled": True, "quota_bytes": 10, "traffic": {"rx": 4, "tx": 5}}, now), "●")
        self.assertEqual(bot.user_icon({}, now), "●")

    def test_fixture_users_cover_all_icons(self):
        r, users = run(bot.fetch_users())
        self.assertTrue(bot.bridge_ok(r))
        icons = {u["name"]: bot.user_icon(u) for u in users}
        self.assertEqual(icons, {"anna": "●", "bob": "○", "old": "⏳", "greedy": "▮"})

    def test_pagination(self):
        items = list(range(20))
        chunk, page, pages = bot.paginate(items, 0, 8)
        self.assertEqual((chunk, page, pages), (list(range(8)), 0, 3))
        chunk, page, pages = bot.paginate(items, 2, 8)
        self.assertEqual(chunk, [16, 17, 18, 19])
        chunk, page, pages = bot.paginate(items, 99, 8)
        self.assertEqual(page, 2)
        chunk, page, pages = bot.paginate(items, -5, 8)
        self.assertEqual(page, 0)
        chunk, page, pages = bot.paginate([], 3, 8)
        self.assertEqual((chunk, page, pages), ([], 0, 1))
        chunk, page, pages = bot.paginate(items, "x", 0)
        self.assertEqual((page, pages), (0, 20))

    def test_sorting_admin_first(self):
        users = bot.users_sorted([{"name": "zed"}, {"name": "admin"}, {"name": "Bob"}, {"label": "no name"}])
        self.assertEqual([u["name"] for u in users], ["admin", "Bob", "zed"])

    def test_human_bytes_and_iso(self):
        self.assertEqual(bot.human_bytes(0), "0 " + i18n.t(None, "unit_b"))
        self.assertTrue(bot.human_bytes(1536).startswith("1.5 "))
        self.assertTrue(bot.human_bytes(3 * 1024 ** 3).endswith(i18n.t(None, "unit_gb")))
        self.assertEqual(bot.human_bytes("junk"), "0 " + i18n.t(None, "unit_b"))
        self.assertEqual(bot.parse_iso("2026-09-08T12:00:00Z").year, 2026)
        self.assertIsNone(bot.parse_iso(""))
        self.assertIsNone(bot.parse_iso("yesterday"))


class NotifyTests(unittest.TestCase):
    def test_collect_reads_and_deletes(self):
        d = os.environ["GOAMNEZIA_NOTIFY_DIR"]
        with open(os.path.join(d, "20260908_1_1.json"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": "2026-09-08T12:00:00Z", "event": "quota_exceeded", "user": "anna",
                                 "label": "Анна", "quota_bytes": 1024, "used_bytes": 2048}) + "\n")
        with open(os.path.join(d, "20260908_1_2.json"), "w", encoding="utf-8") as fh:
            fh.write("not json at all\n")
        with open(os.path.join(d, ".tmp.abc"), "w", encoding="utf-8") as fh:
            fh.write("{}")
        with open(os.path.join(d, "readme.txt"), "w", encoding="utf-8") as fh:
            fh.write("x")
        events = bot.collect_notify_events(d)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "quota_exceeded")
        self.assertEqual(sorted(os.listdir(d)), [".tmp.abc", "readme.txt"])
        self.assertEqual(bot.collect_notify_events(os.path.join(d, "missing")), [])

    def test_format_events(self):
        _prime_lines()
        text = bot.format_notify_event(None, {"event": "quota_exceeded", "user": "anna", "label": "<Анна>",
                                              "quota_bytes": 1024, "used_bytes": 2048})
        self.assertIn("anna", text)
        self.assertIn("&lt;Анна&gt;", text)
        self.assertIn("2.0", text)
        text = bot.format_notify_event(None, {"event": "expired", "user": "bob", "expires": "2026-01-01T00:00:00Z"})
        self.assertIn("bob", text)
        text = bot.format_notify_event(None, {"event": "container_down", "container": "amnezia-awg2"})
        self.assertIn("amnezia-awg2", text)
        text = bot.format_notify_event(None, {"event": "container_restarted", "container": "amnezia-awg2", "profile": "awg2", "action": "started", "ok": True})
        self.assertIn("amnezia-awg2", text)
        # профиль — линией (DS2-55), контейнер — своим именем
        self.assertEqual(text, i18n.tf(None, "ntf_watch_restarted_fmt", "amnezia-awg2", FX20))
        text = bot.format_notify_event(None, {"event": "container_restarted", "container": "amnezia-awg2", "profile": "awg2", "action": "started", "ok": False})
        self.assertEqual(text, i18n.tf(None, "ntf_watch_failed_fmt", "amnezia-awg2", FX20))
        text = bot.format_notify_event(None, {"event": "container_missing", "container": "amnezia-awg3", "profile": "awg3"})
        # Проверка отстала от строки: она искала имя действия моста (core-update),
        # а сообщение с тех пор переписали на язык человека — «Поднять контейнер
        # заново» и кнопка «Починить». Сверяем со словарём и с сутью совета:
        # контейнер поднимают из копии состояния, а НЕ восстанавливают из бэкапа
        # (тот откатил бы всех, кого завели после последней копии).
        self.assertEqual(text, i18n.tf(None, "ntf_container_missing_fmt", "amnezia-awg3", FX31))
        self.assertIn("amnezia-awg3", text)
        self.assertIn("НЕ нужно", text)
        text = bot.format_notify_event(None, {"event": "upstream_available", "client_release_current": "5.0.2.1", "client_release_latest": "5.0.3.0",
                                              "profiles": [{"profile": "awg2", "current_tag": "3.1.20260828", "available_tag": "3.2.20260915", "update": True},
                                                           {"profile": "awg3", "current_tag": "3.2.20260915", "available_tag": "3.2.20260915", "update": False}]})
        self.assertIn("5.0.3.0", text)
        self.assertIn(FX20 + ": 3.1.20260828 → 3.2.20260915", text)
        self.assertNotIn("AmneziaWG 3.1", text)
        # дальше — прежние проверки на событии container_down
        text = bot.format_notify_event(None, {"event": "container_down", "container": "amnezia-awg2"})
        self.assertIn("amnezia-awg2", text)
        text = bot.format_notify_event(None, {"event": "weird", "user": "x", "extra": "<y>"})
        self.assertIn("weird", text)
        self.assertIn("&lt;y&gt;", text)

    def test_new_alarms_say_what_happened_and_what_to_do(self):
        """Семь тревог, приходивших машинным дампом, теперь говорят по-человечески.

        Строки в словаре не было ни у одной, и все они падали в общий вид
        «Событие cert_expiring: kind=letsencrypt, domain=…, days=5». Хуже всех
        было именно с сертификатом: это «через пять дней разом умрут сайт, панель
        и весь транспорт агентов», сказанное перечислением полей и без единого
        слова о том, что делать. Проверяем оба языка: тревога, понятная только
        по-русски, для владельца с английским интерфейсом — тот же дамп.
        """
        EN_UID = 909091
        self.assertTrue(i18n.set_user_lang(EN_UID, "en"))
        cases = [
            ({"event": "cert_expiring", "kind": "letsencrypt", "domain": "vpn.example.com",
              "days": 5, "expires": "2026-10-01"}, ("vpn.example.com", "5", "2026-10-01")),
            ({"event": "cert_expiring", "kind": "selfsigned", "domain": "",
              "days": 3, "expires": "2026-10-01"}, ("3", "2026-10-01")),
            ({"event": "cert_unknown", "kind": "letsencrypt", "domain": "vpn.example.com"},
             ("vpn.example.com",)),
            ({"event": "cert_unknown", "kind": "selfsigned", "domain": ""}, ()),
            ({"event": "server_ip_changed", "old": "203.0.113.10", "new": "198.51.100.7"},
             ("203.0.113.10", "198.51.100.7")),
            ({"event": "agent_expiring", "agent": "a-1a2b", "label": "Claude Code", "scope": "read",
              "expires": "2026-10-01", "days_left": 3}, ("a-1a2b", "Claude Code", "3", "2026-10-01")),
            ({"event": "agent_expired", "agent": "a-1a2b", "label": "Claude Code", "scope": "read",
              "expires": "2026-09-01", "ip": "203.0.113.5"},
             ("a-1a2b", "Claude Code", "2026-09-01", "203.0.113.5")),
            ({"event": "agent_rate_limited", "agent": "a-1a2b", "label": "Claude Code", "scope": "read",
              "ip": "203.0.113.5", "calls": 61, "window_s": 60},
             ("a-1a2b", "Claude Code", "61", "60", "203.0.113.5")),
            ({"event": "backup_failed", "code": "no_space", "message": "На диске нет места"},
             ("no_space", "На диске нет места")),
        ]
        # Куда идти — обязательная часть тревоги: место в меню или SSH.
        WHERE = {"ru": ("Настройки", "Обслуживание", "SSH-меню"),
                 "en": ("Settings", "Maintenance", "SSH menu")}
        for uid, lang in ((None, "ru"), (EN_UID, "en")):
            generic = i18n.t(uid, "ntf_generic_fmt").split("%s")[0].strip()
            for ev, facts in cases:
                text = bot.format_notify_event(uid, ev)
                where = "%s/%s" % (ev["event"], lang)
                # 1. это не общий дамп «Событие <code>…»
                self.assertNotIn(generic, text, where)
                self.assertNotIn("kind=", text, where)
                # 2. факты события в тексте, а не потеряны
                for fact in facts:
                    self.assertIn(fact, text, "%s: нет факта %r" % (where, fact))
                # 3. сказано, что делать, и хвоста printf нет
                self.assertTrue(any(w in text for w in WHERE[lang]), "%s: не сказано, куда идти" % where)
                self.assertNotIn("%s", text, where)
                self.assertGreater(len(text), 80, where)
        # Вид сертификата решает, какой совет давать: у Let's Encrypt дело обычно в
        # A-записи и 80-м порте, у запасного самоподписанного домена нет вовсе.
        le = bot.format_notify_event(None, cases[0][0])
        ss = bot.format_notify_event(None, cases[1][0])
        self.assertNotEqual(le, ss)
        self.assertIn("Let's Encrypt", le)
        # Предел частоты — не заморозка: доступ жив, перевыпускать его не надо.
        rate = bot.format_notify_event(None, cases[7][0])
        self.assertIn("ЖИВ", rate)

    def test_disk_level_alarm_speaks_like_the_main_screen(self):
        """TAIL-19: о заполняющемся диске — уведомлением (ядро шлёт disk_level только при смене уровня): low и
        critical — теми же словами, что строка главного экрана (OPS-06), с кнопкой «Диагностика»; возврат в ok —
        «снова достаточно», без кнопки. Оба языка."""
        EN_UID = 909092
        self.assertTrue(i18n.set_user_lang(EN_UID, "en"))
        base = {"event": "disk_level", "free_mb": 800, "total_mb": 20000, "used_pct": 96, "path": "/opt/goamnezia",
                "low_mb": 1024, "critical_mb": 512}
        for uid in (None, EN_UID):
            low = bot.format_notify_event(uid, dict(base, level="low", prev="ok"))
            self.assertEqual(low, "⚠️ " + i18n.tf(uid, "dash_disk_low_fmt", 800, 96))
            crit = bot.format_notify_event(uid, dict(base, level="critical", prev="low", free_mb=100, used_pct=100))
            self.assertEqual(crit, "🚨 " + i18n.tf(uid, "dash_disk_critical_fmt", 100, 100))
            ok = bot.format_notify_event(uid, dict(base, level="ok", prev="critical", free_mb=15000, used_pct=25))
            self.assertEqual(ok, i18n.tf(uid, "ntf_disk_ok_fmt", 15000, 25))
            self.assertNotIn("%", ok.replace("%)", "").replace("% ", "").replace("%.", ""))
        if bot.PTB_AVAILABLE:
            kb = bot.notify_keyboard(None, dict(base, level="critical"))
            # nt~ — экран диагностики открывается новым сообщением, тревога остаётся в чате (BOT-5)
            self.assertEqual([b.callback_data for row in kb.inline_keyboard for b in row], ["nt~mt_diag"])
            self.assertIsNone(bot.notify_keyboard(None, dict(base, level="ok")))

    def test_every_event_the_product_sends_has_its_own_text(self):
        """Ни одно событие продукта не должно приходить общим дампом.

        Имена берём из самого продукта, а не из списка в тесте: иначе следующее
        новое событие снова уедет владельцу перечислением полей, и узнают об этом
        только от него. Общий вид ntf_generic_fmt остаётся — но для чужих событий
        (старый сервер, ручная запись в очередь), а не для наших.
        """
        names = set()
        for name in os.listdir(os.path.join(ROOT, "lib")):
            if not name.endswith(".sh"):
                continue
            with open(os.path.join(ROOT, "lib", name), encoding="utf-8") as fh:
                names |= set(re.findall(r"^\s*(?:type ga_notify[^\n]*&&\s*)?ga_notify\s+([a-z][a-z_]*)",
                                        fh.read(), re.M))
        with open(os.path.join(ROOT, "goamnezia-mcp", "server.py"), encoding="utf-8") as fh:
            names |= set(re.findall(r'notifier\.send\("([a-z][a-z_]*)"', fh.read()))
        # Обрубок имени, которое склеивают в коде («agent_» + event), сам событием
        # не бывает — дописываем такие ниже руками.
        names = {n for n in names if not n.endswith("_")}
        # Имена, собираемые из переменной: сторож (lib/amnezia.sh event=…), таймер
        # лимитов (lib/users.sh reason=…) и доступы агентов ("agent_" + event).
        names |= {"container_restarted", "container_missing", "container_down",
                  "quota_exceeded", "expired", "agent_first_use", "agent_new_ip"}
        self.assertGreaterEqual(len(names), 15, names)
        generic = i18n.t(None, "ntf_generic_fmt").split("%s")[0].strip()
        for event in sorted(names):
            text = bot.format_notify_event(None, {"event": event})
            self.assertNotIn(generic, text, "событие %s приходит машинным дампом" % event)
        # А вот чужое событие общим видом — это и есть его работа.
        self.assertIn(generic, bot.format_notify_event(None, {"event": "from_the_future", "x": 1}))

    def test_clone_alarm_says_whether_the_grant_was_really_frozen(self):
        """Сервер кладёт в событие frozen; не прочитать его — значит соврать про главное.

        Заморозить не удалось — доступ ЖИВ, клон работает дальше, и владельцу нужно не
        «принять к сведению», а отозвать доступ прямо сейчас (DEFECTS §1d.1 №5).
        """
        base = {"event": "agent_compromised", "agent": "a-1a2b", "label": "Claude Code", "scope": "read",
                "ip": "203.0.113.9", "clone_ip": "203.0.113.9", "clone_at": "2026-09-11T12:00:00Z",
                "last_ip": "203.0.113.5", "last_used": "2026-09-11T11:00:00Z"}
        frozen = bot.format_notify_event(None, dict(base, frozen=True))
        live = bot.format_notify_event(None, dict(base, frozen=False))
        self.assertNotEqual(frozen, live, "тревога обязана различать «заморожен» и «не удалось»")
        self.assertEqual(frozen, i18n.tf(None, "ntf_agent_clone_fmt", "a-1a2b", "Claude Code",
                                         "2026-09-11T12:00:00Z", "203.0.113.9",
                                         "2026-09-11T11:00:00Z", "203.0.113.5"))
        self.assertEqual(live, i18n.tf(None, "ntf_agent_clone_live_fmt", "a-1a2b", "Claude Code",
                                       "2026-09-11T12:00:00Z", "203.0.113.9",
                                       "2026-09-11T11:00:00Z", "203.0.113.5"))
        # Оба текста несут факты, а «заморожен» звучит только там, где это правда
        for text in (frozen, live):
            for want in ("a-1a2b", "203.0.113.9", "203.0.113.5"):
                self.assertIn(want, text)
        self.assertIn("ЗАМОРОЖЕН", frozen)
        self.assertNotIn("ЗАМОРОЖЕН:", live)
        self.assertIn("НЕ УДАЛОСЬ", live)
        # Событие от прежней версии сервера (поля нет) читается как прежде — заморожен
        self.assertEqual(bot.format_notify_event(None, dict(base)), frozen)


    def test_enrollment_and_clock_jump_alarms_are_readable(self):
        """Привязка переходника и прыжок часов сервера — события server.py (DEFECTS §2.1).

        Чужая привязка — повод перевыпустить доступ немедленно, поэтому текст несёт адрес
        и место хранения ключа; прыжок часов называет величину со знаком, а мусор — «?».
        """
        ev = {"event": "agent_enrolled", "agent": "a-1a2b", "label": "Claude Code", "scope": "read",
              "ip": "203.0.113.7", "storage": "tpm"}
        tpm = bot.format_notify_event(None, ev)
        self.assertEqual(tpm, i18n.tf(None, "ntf_agent_enrolled_fmt", "a-1a2b", "Claude Code", "203.0.113.7",
                                      i18n.t(None, "ag_storage_tpm")))
        as_file = bot.format_notify_event(None, dict(ev, storage="file"))
        self.assertIn(i18n.t(None, "ag_storage_file"), as_file)
        self.assertNotEqual(tpm, as_file)
        # Метка доступа — ввод человека: разметку чата она не ломает
        self.assertIn("&lt;b&gt;", bot.format_notify_event(None, dict(ev, label="<b>x</b>")))
        jump = bot.format_notify_event(None, {"event": "server_clock_jump", "delta_s": 75})
        self.assertEqual(jump, i18n.tf(None, "ntf_server_clock_jump_fmt", "+75"))
        self.assertIn("-3600", bot.format_notify_event(None, {"event": "server_clock_jump", "delta_s": -3600}))
        for junk in (True, "75", None):
            self.assertEqual(bot.format_notify_event(None, {"event": "server_clock_jump", "delta_s": junk}),
                             i18n.tf(None, "ntf_server_clock_jump_fmt", "?"))

    def test_connect_grant_card_and_issue_screen(self):
        """Доступ через переходник: адреса нет, код показывается один раз, до привязки — «ждёт»."""
        pending = {"id": "a-1a2b", "label": "Claude Code", "scope": "read", "enabled": True, "mode": "connect",
                   "transport": "connect", "require_token": True, "enroll_pending": True, "key_storage": "",
                   "state": "active"}
        card = bot.agent_card_text(None, pending)
        self.assertIn(i18n.t(None, "ag_card_mode_connect"), card)
        self.assertIn(i18n.t(None, "ag_storage_pending"), card)
        self.assertIn(i18n.t(None, "ag_enroll_pending_note"), card)
        self.assertIn(i18n.t(None, "ag_token_connect_pending"), card, "до привязки ключа ещё нет — есть код")
        expired = dict(pending, enroll_expired=True)
        card = bot.agent_card_text(None, expired)
        self.assertIn(i18n.t(None, "ag_storage_code_expired"), card)
        self.assertIn(i18n.t(None, "ag_enroll_expired_note"), card)
        self.assertNotIn(i18n.t(None, "ag_enroll_pending_note"), card)
        bound = dict(pending, enroll_pending=False, key_storage="tpm")
        card = bot.agent_card_text(None, bound)
        self.assertIn(i18n.t(None, "ag_token_connect"), card)
        self.assertIn(i18n.t(None, "ag_storage_tpm"), card)
        self.assertNotIn(i18n.t(None, "ag_enroll_pending_note"), card)
        # Усиливать нечего: второй фактор — одноразовый ключ у переходника. Кнопки
        # собираются классами python-telegram-bot — без библиотеки эту часть пропускаем.
        if hasattr(bot, "InlineKeyboardButton"):
            markup = bot.agent_card_keyboard(None, dict(bound, require_token=False))
            data = [b.callback_data for row in markup.inline_keyboard for b in row]
            self.assertNotIn("ag_hard~a-1a2b", data)
            self.assertIn("ag_rot~a-1a2b", data)
        code = "GA-ABCDE-FGHJK-MNPQR-STVWX"
        issued = dict(pending, url="", token="", enroll_code=code, enroll_hint="hint-once",
                      connect="goamnezia-connect enroll %s --server https://example.com" % code,
                      connector_hint="scp root@203.0.113.10:/opt/goamnezia/goamnezia-mcp/connect.py ~/bin/goamnezia-connect")
        text = bot.agent_url_text(None, issued)
        self.assertIn("<code>%s</code>" % code, text)
        self.assertIn("hint-once", text)
        self.assertIn("goamnezia-connect enroll", text)
        self.assertIn("goamnezia-mcp/connect.py", text)
        self.assertNotIn(i18n.t(None, "ag_card_url"), text)
        self.assertNotIn(i18n.t(None, "ag_url_is_key"), text)
        self.assertIn(i18n.t(None, "ag_one_client_warn"), text)
        # Привязанный: команды нет, пояснение — курсивом, без подписи «Команда подключения»
        shown = dict(bound, url="", token="", connect="", connect_note="note-bound status --grant a-1a2b")
        text = bot.agent_url_text(None, shown)
        self.assertIn("<i>note-bound status --grant a-1a2b</i>", text)
        self.assertNotIn(i18n.t(None, "ag_url_cmd"), text)
        # Мастер выдачи принимает третий вид
        self.assertIn("connect", bot.AGENT_MODES)

class I18nTests(unittest.TestCase):
    def test_dictionaries_parity_and_lookup(self):
        ru = i18n._load_lang_file("ru")
        en = i18n._load_lang_file("en")
        self.assertTrue(ru and en)
        self.assertEqual(set(ru), set(en))
        self.assertEqual(i18n.t(None, "no_such_key_xyz"), "no_such_key_xyz")
        self.assertEqual(i18n.t(None, "no_such_key_xyz", default="d"), "d")
        self.assertEqual(i18n.t_in("en", "menu_users"), en["menu_users"])
        self.assertIn("5", i18n.tf(None, "days_fmt", 5))
        self.assertEqual(i18n.tf(None, "menu_users", 1, 2), i18n.t(None, "menu_users"))

    def test_user_lang_persisted_in_bot_dir(self):
        self.assertTrue(i18n.set_user_lang(777, "en"))
        self.assertFalse(i18n.set_user_lang(777, "xx"))
        self.assertEqual(i18n.get_user_lang(777), "en")
        # Путь — из уже импортированного модуля, а не из os.environ: при общем прогоне
        # соседний test_admin_features переставляет GOAMNEZIA_BOT_DIR под себя, и порядок
        # импорта модулей решал, чей каталог окажется в переменной (у admin такая же
        # оговорка стоит в write_env).
        path = os.path.join(str(i18n.BOT_DIR), "user_langs.json")
        with open(path, encoding="utf-8") as fh:
            # только свой ключ: обход экранов (HandlerWalkTests) тоже пишет сюда язык
            self.assertEqual(json.load(fh).get("777"), "en")
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(i18n.t(777, "menu_users"), i18n.t_in("en", "menu_users"))

    def test_dynamic_key_families_exist(self):
        ru = i18n._load_lang_file("ru")
        for key in ("pr_label_fmt", "pr_label_app_fmt", "pr_label_free_fmt", "pr_line_name_fmt"):
            self.assertIn(key, ru)
        for preset in bot.PRESETS:
            self.assertIn("preset_%s" % preset, ru)
        for cmd in bot.BOT_COMMANDS:
            self.assertIn("cmd_desc_%s" % cmd, ru)
        for key in ("unit_b", "unit_kb", "unit_mb", "unit_gb", "running", "stopped", "not_installed",
                    "state_enabled", "state_disabled", "state_expired", "state_quota"):
            self.assertIn(key, ru)


class AdminFileTests(unittest.TestCase):
    def test_allowed_ids_saved_atomically_with_0600(self):
        env_path = bot.ENV_FILE
        with open(env_path, "w", encoding="utf-8") as fh:
            fh.write("BOT_TOKEN=secret\nALLOWED_IDS=\n")
        os.chmod(env_path, 0o600)
        try:
            self.assertTrue(bot.add_admin(111))
            self.assertTrue(bot.is_user_allowed(111))
            self.assertFalse(bot._WAITING_FOR_ADMIN)
            self.assertTrue(os.path.exists(bot.ADMIN_CLAIMED_MARKER))
            self.assertFalse(bot.admin_claim_open())
            with open(env_path, encoding="utf-8") as fh:
                content = fh.read()
            self.assertIn("BOT_TOKEN=secret", content)
            self.assertIn("ALLOWED_IDS=111", content)
            self.assertEqual(stat.S_IMODE(os.stat(env_path).st_mode), 0o600)
            self.assertTrue(bot.remove_admin(111))
            self.assertFalse(bot.is_user_allowed(111))
        finally:
            bot.ALLOWED_IDS.clear()
            bot._WAITING_FOR_ADMIN = True
            for p in (env_path, bot.ADMIN_CLAIMED_MARKER):
                if os.path.exists(p):
                    os.unlink(p)


class ScreensTextTests(unittest.TestCase):
    """Текст экранов из ответов заглушки: html-экранирование и защищённый разбор."""

    def test_dashboard_and_reports(self):
        status = run(bot.get_status(fresh=True))
        text = bot.dashboard_text(None, status)
        self.assertIn("● 2.0", text)                         # линии, не служебные id (DS2-55)
        self.assertNotIn("awg2", text)
        self.assertIn("203.0.113.10", text)
        self.assertIn("●", text)
        self.assertIn("○", text)
        self.assertIn("AmneziaWG 3.1", bot.status_report_text(None, status))
        self.assertIn(":443/udp", bot.addresses_text(None, status))
        self.assertIsNone(bot.miniapp_url(status))
        self.assertEqual(bot.product_version(status), "0.1.0")

    def test_dns_trouble_is_told_with_a_single_address_too(self):
        """Критик 24.09, п. 1: при одном адресе у машины бот молчал о чужих A-записях домена из
        ключей, а SSH-шапка о них говорит. Таблицы при одном адресе нет, а беда с DNS — есть."""
        edge = {"count": 1, "ips": [{"ip": "203.0.113.10"}],
                "dns": {"state": "partial", "domain": "vpn.example.com", "foreign": ["192.0.2.50"]}}
        lines = bot.edge_table_lines(None, edge)
        self.assertTrue(any("192.0.2.50" in line and line.startswith("⚠️") for line in lines), lines)
        self.assertFalse(any(i18n.t(None, "edge_table_title") in line for line in lines), "таблицы нет")
        self.assertEqual(bot.edge_table_lines(None, {"count": 1, "ips": [{"ip": "203.0.113.10"}]}), [],
                         "одному адресу без беды сообщать нечего")
        edge["dns"] = {"state": "none", "domain": "vpn.example.com"}
        self.assertTrue(bot.edge_table_lines(None, edge), "домен без A-записей — тоже сказать")

    def test_long_secret_path_still_makes_a_button(self):
        """Путь панели несёт 512 бит и занимает 90 знаков.

        Предел длины жил в трёх местах сразу (ядро, бот, панель), и удлинение
        пути в ядре молча снимало кнопку мини-аппа: бот отвергал собственный
        адрес продукта и продолжал писать «панель включена».
        """
        long_path = "app-" + ("aZ09_-" * 15)[:86]
        status = {"mode": "pro", "domain": "example.test", "miniapp_path": long_path,
                  "site": {"cert": "letsencrypt"}}
        self.assertEqual(bot.miniapp_url(status), "https://example.test/%s/" % long_path)
        # Слишком длинное всё-таки отвергается: предел есть, просто он общий
        self.assertIsNone(bot.miniapp_url({"mode": "pro", "domain": "example.test",
                                           "miniapp_path": "app-" + "a" * 200,
                                           "site": {"cert": "letsencrypt"}}))

    def test_dashboard_on_bridge_error(self):
        text = bot.dashboard_text(None, {"status": "error", "code": "transport", "rc": "3"})
        self.assertIn("⚠️", text)

    def test_mode_label_three_modes_and_passthrough(self):
        """Три режима PLAN §4.3 — название и подпись из словаря (F-2: «Semi-Pro — VPN и сайт»); чужой
        код — как есть, пусто — прочерк."""
        names = {m: bot.mode_label(None, m) for m in ("lite", "lite_site", "pro")}
        self.assertEqual(len(set(names.values())), 3)
        for m, key in bot.MODE_NAME_KEYS.items():
            self.assertEqual(names[m], "%s — %s" % (bot._t(None, key), bot._t(None, bot.MODE_SUB_KEYS[m])))
            self.assertEqual(bot.mode_short(None, m), bot._t(None, key))
        self.assertNotIn("lite_site", names["lite_site"])          # не сырой код
        self.assertEqual(bot.mode_label(None, "weird_mode"), "weird_mode")
        self.assertEqual(bot.mode_label(None, None), "—")
        self.assertEqual(bot.mode_label(None, ""), "—")
        # дашборд и отчёт показывают название, а не код
        status = run(bot.get_status(fresh=True))
        self.assertIn(names["lite"], bot.dashboard_text(None, status))
        self.assertIn(names["lite"], bot.status_report_text(None, status))

    def test_site_summary_lines_selfsigned_and_letsencrypt(self):
        """Блок site из status: самоподписанный — отпечаток, Let's Encrypt — срок; мусор — пусто."""
        self.assertEqual(bot.site_summary_lines(None, None), [])
        self.assertEqual(bot.site_summary_lines(None, "not a dict"), [])
        fp = "AA:BB:" + ":".join(["00"] * 30)
        ss = {"url": "https://203.0.113.10/", "template_id": "h5up_aerial", "cert": "selfsigned",
              "cert_fingerprint_sha256": fp, "cert_days_left": 3650}
        text = "\n".join(bot.site_summary_lines(None, ss))
        self.assertIn("https://203.0.113.10/", text)
        self.assertIn("h5up_aerial", text)
        self.assertIn(fp, text)
        self.assertIn(bot._t(None, "site_cert_selfsigned"), text)
        le = {"url": "https://example.com/", "template_id": "", "cert": "letsencrypt",
              "cert_expires": "2027-09-08T20:09:08Z", "cert_days_left": 364}
        text = "\n".join(bot.site_summary_lines(None, le))
        self.assertIn("2027-09-08", text)
        self.assertIn("364", text)
        self.assertIn(bot._t(None, "site_template_none"), text)
        self.assertNotIn(bot._t(None, "site_fingerprint"), text)
        # без срока — «ещё не выпущен», html в url экранируется
        text = "\n".join(bot.site_summary_lines(None, {"cert": "letsencrypt", "url": "https://x/<b>"}))
        self.assertIn(bot._t(None, "site_cert_letsencrypt_none"), text)
        self.assertIn("&lt;b&gt;", text)
        # адреса: url сайта появляется в «Адресах», когда блок site есть
        status = dict(run(bot.get_status(fresh=True)))
        status["site"] = ss
        self.assertIn("https://203.0.113.10/", bot.addresses_text(None, status))

    def test_user_card_text_escapes_and_lists_profiles(self):
        r, user = run(bot._fetch_user("anna"))
        self.assertIsNotNone(user)
        status = run(bot.get_status())
        text = bot.user_card_text(None, user, status)
        self.assertIn("Анна", text)
        self.assertIn("4.8.12.9", text)      # мин. версия приложения для 2.0 (стабильная, как на странице подключения — A-15)
        self.assertIn("5.0.1.5", text)       # и для 3.1
        self.assertIn("10.8.2.2", text)
        r, user = run(bot._fetch_user("nobody"))
        self.assertIsNone(user)
        self.assertEqual(r["code"], "user_not_found")

    def test_traffic_logs_diag_import_texts(self):
        r = run(bot.run_bot_action("traffic", timeout=20))
        text = bot.traffic_text(None, r)
        self.assertIn("anna", text)
        r = run(bot.run_bot_action("logs-tail", timeout=20, component="awg2", lines=60))
        self.assertIn("<b>", r["log"])
        self.assertNotIn("<b>not html</b>", bot.esc(r["log"]))
        r = run(bot.run_bot_action("quality", timeout=20))
        text = bot.diag_text(None, r)
        self.assertIn("Docker", text)
        self.assertIn("12345", text)
        r = run(bot.run_bot_action("import-scan", timeout=20))
        text = bot.import_scan_text(None, r)
        self.assertIn("amnezia-awg", text)
        r = run(bot.run_bot_action("backup-list", timeout=20))
        names = bot._backup_names(r)
        self.assertEqual(len(names), 2)
        self.assertTrue(all(bot.valid_backup_file(n) for n in names))
        self.assertTrue(names[0] > names[1])
        r = run(bot.run_bot_action("profile-params", timeout=20, profile="awg2"))
        text = bot.params_text(None, "awg2", r["params"], "2.0")
        self.assertIn("🔒", text)
        self.assertIn("S3", text)


class KeyboardTests(unittest.TestCase):
    """Клавиатуры требуют python-telegram-bot — без него пропускаем."""

    def setUp(self):
        if not bot.PTB_AVAILABLE:
            self.skipTest("python-telegram-bot не установлен: %s" % bot.PTB_IMPORT_ERROR)

    def _all_callbacks(self, markup):
        out = []
        for row in markup.inline_keyboard:
            for b in row:
                if b.callback_data:
                    out.append(b.callback_data)
        return out

    def test_menu_rows_splits_long_labels(self):
        # Длинная подпись уезжает на свою строку, порядок кнопок сохраняется
        rows = [[bot.btn("a", "x"), bot.btn("b" * 20, "y")], [bot.btn("c", "z")]]
        out = bot._menu_rows(rows)
        self.assertEqual([[b.callback_data for b in r] for r in out], [["x"], ["y"], ["z"]])
        # Две короткие в одном ряду остаются парой
        rows = [[bot.btn("a", "x"), bot.btn("b" * 20, "y")], [bot.btn("c", "z"), bot.btn("d", "w")]]
        out = bot._menu_rows(rows)
        self.assertEqual([[b.callback_data for b in r] for r in out], [["x"], ["y"], ["z", "w"]])
        # Короткая без пары ждёт следующую короткую
        rows = [[bot.btn("a", "x"), bot.btn("b" * 20, "y")], [bot.btn("c" * 20, "z"), bot.btn("d", "w")],
                [bot.btn("e", "v"), bot.btn("f", "u")]]
        out = bot._menu_rows(rows)
        self.assertEqual([[b.callback_data for b in r] for r in out], [["x"], ["y"], ["z"], ["w"], ["v", "u"]])

    def test_users_keyboard_pages_and_lengths(self):
        users = [{"name": "u%02d" % i, "enabled": True} for i in range(20)]
        markup = bot.users_keyboard(None, users, 1)
        cbs = self._all_callbacks(markup)
        self.assertIn("u_view~u08", cbs)
        self.assertIn("menu_users~0", cbs)
        self.assertIn("menu_users~2", cbs)
        self.assertIn("u_add", cbs)
        self.assertTrue(all(len(c.encode()) <= 64 for c in cbs))
        markup = bot.users_keyboard(None, users[:3], 0)
        self.assertNotIn("noop", self._all_callbacks(markup))

    def test_user_card_keyboard_two_styles_per_profile(self):
        r, user = run(bot._fetch_user("anna"))
        markup = bot.user_card_keyboard(None, user)
        cbs = self._all_callbacks(markup)
        for p in ("awg2", "awg3"):
            for fmt in ("qrconf", "conf", "vpn", "qrvpn"):
                self.assertIn("u_key~anna~%s~%s" % (p, fmt), cbs)
        self.assertIn("u_en~anna~off", cbs)
        self.assertIn("u_del~anna", cbs)
        self.assertTrue(all(len(c.encode()) <= 64 for c in cbs))
        long_name = "x" * 32
        markup = bot.user_card_keyboard(None, {"name": long_name, "enabled": False, "peers": {"awg1": {}}})
        cbs = self._all_callbacks(markup)
        self.assertIn("u_en~%s~on" % long_name, cbs)
        self.assertTrue(all(len(c.encode()) <= 64 for c in cbs))

    def test_main_menu_sections_and_install_button(self):
        status = run(bot.get_status())
        cbs = self._all_callbacks(bot.get_main_menu(None, status))
        for want in ("menu_users", "sec_state", "sec_protocols", "sec_maint", "sec_settings", "close_menu"):
            self.assertIn(want, cbs)
        self.assertNotIn("menu_install", cbs)
        cbs = self._all_callbacks(bot.get_main_menu(None, dict(status, installed=False)))
        self.assertIn("menu_install", cbs)

    def test_profile_card_keyboard(self):
        cbs = self._all_callbacks(bot.profile_card_keyboard(None, "awg2", True))
        for want in ("pr_port~awg2", "pr_443~awg2", "pr_subnet~awg2", "pr_mtu~awg2", "pr_params~awg2",
                     "pr_preset~awg2", "pr_regen~awg2", "pr_restart~awg2", "pr_remove~awg2"):
            self.assertIn(want, cbs)
        cbs = self._all_callbacks(bot.profile_card_keyboard(None, "awg1", False))
        self.assertEqual([c for c in cbs if c.startswith("pr_")], ["pr_install~awg1"])
        # BL-3: профиль уже на UDP/443 — «Занять UDP/443» нет, «Сменить порт» остаётся (как LH-2 в SSH)
        cbs = self._all_callbacks(bot.profile_card_keyboard(None, "awg2", True, port="443"))
        self.assertNotIn("pr_443~awg2", cbs)
        self.assertIn("pr_port~awg2", cbs)
        cbs = self._all_callbacks(bot.profile_card_keyboard(None, "awg2", True, port="6714"))
        self.assertIn("pr_443~awg2", cbs)

    def test_router_resolves_prefixes_longest_first(self):
        self.assertIs(bot.resolve_handler("u_addp_do~anna~awg2"), bot.cb_user_add_profile_do)
        self.assertIs(bot.resolve_handler("u_addp~anna"), bot.cb_user_add_profile)
        self.assertIs(bot.resolve_handler("pr_443_do~awg2"), bot.cb_pr_443_do)
        self.assertIs(bot.resolve_handler("se_admin_del_ok~5"), bot.cb_se_admin_del)
        self.assertIs(bot.resolve_handler("menu_users"), bot.cb_menu_users)
        self.assertIs(bot.resolve_handler("menu_users~3"), bot.cb_menu_users)
        self.assertIsNone(bot.resolve_handler("nonsense"))

    def test_bot_commands_localized(self):
        for lang in ("ru", "en"):
            cmds = bot.bot_commands_for(lang)
            self.assertEqual([c.command for c in cmds], list(bot.BOT_COMMANDS))
            self.assertTrue(all(c.description for c in cmds))



class _Recorder:
    """Журнал вызовов Telegram-объектов: что бот отправил/отредактировал."""

    def __init__(self):
        self.calls = []

    def record(self, what, *args, **kwargs):
        self.calls.append((what, args, kwargs))

    def count(self, what):
        return sum(1 for c in self.calls if c[0] == what)


class FakeMessage:
    def __init__(self, rec, chat_id=1):
        self.rec = rec
        self.chat_id = chat_id
        self.message_id = id(self)
        self.text = ""

    async def reply_text(self, text, **kw):
        self.rec.record("reply_text", text, **kw)
        return self

    async def reply_photo(self, photo, **kw):
        self.rec.record("reply_photo", photo.read()[:8] if hasattr(photo, "read") else photo, **kw)
        return self

    async def reply_document(self, document, **kw):
        self.rec.record("reply_document", document, **kw)
        return self

    async def reply_media_group(self, media, **kw):
        self.rec.record("reply_media_group", len(media), **kw)
        return [self]

    async def edit_text(self, text, **kw):
        self.rec.record("edit_text", text, **kw)
        return self

    async def delete(self):
        self.rec.record("delete")


class FakeQuery:
    def __init__(self, rec, data, uid):
        self.rec = rec
        self.data = data
        self.message = FakeMessage(rec, uid)
        self.from_user = type("U", (), {"id": uid})()

    async def answer(self, text=None, show_alert=False, **kw):
        self.rec.record("answer", text, show_alert=show_alert)

    async def edit_message_text(self, text, **kw):
        if "reply_markup" in kw and kw["reply_markup"] is not None:
            # как Telegram: у каждой кнопки callback_data ≤ 64 байт или web_app
            for row in kw["reply_markup"].inline_keyboard:
                for b in row:
                    assert b.callback_data is None or len(b.callback_data.encode()) <= 64, b.callback_data
        self.rec.record("edit", text, **kw)

    async def delete_message(self):
        self.rec.record("delete_message")

    async def edit_message_reply_markup(self, reply_markup=None, **kw):
        self.rec.record("edit_markup", reply_markup, **kw)


class FakeUser:
    def __init__(self, uid):
        self.id = uid
        self.full_name = "Tester <x>"
        self.username = "tester"


class FakeUpdate:
    def __init__(self, uid, data=None, text=None, rec=None, chat_type="private"):
        self.rec = rec or _Recorder()
        self.effective_user = FakeUser(uid)
        # Личный чат с ботом — как у настоящего Telegram: id чата = id человека.
        # Группа — отрицательный id и type=group (бот там молчит, is_private_chat).
        chat_id = uid if chat_type == "private" else -1000000000000 - uid
        self.effective_chat = type("C", (), {"id": chat_id, "type": chat_type})()
        self.callback_query = FakeQuery(self.rec, data, uid) if data is not None else None
        self.message = None
        if text is not None:
            self.message = FakeMessage(self.rec, uid)
            self.message.text = text
        self.effective_message = self.message or (self.callback_query.message if self.callback_query else None)


class FakeContext:
    def __init__(self, args=None):
        self.user_data = {}
        self.args = args or []
        self.error = None


class _BotScreenCase(unittest.TestCase):
    """База для обходов экранов: нажать кнопку, сказать текст, снять экран.

    Своих тестов не содержит — наследники добавляют их сами. Нужен
    python-telegram-bot (клавиатуры), без него весь класс пропускается.
    """

    UID = 424242

    @classmethod
    def setUpClass(cls):
        if not bot.PTB_AVAILABLE:
            raise unittest.SkipTest("python-telegram-bot не установлен")
        bot.ALLOWED_IDS.add(cls.UID)
        bot._WAITING_FOR_ADMIN = False

    @classmethod
    def tearDownClass(cls):
        bot.ALLOWED_IDS.discard(cls.UID)
        bot._WAITING_FOR_ADMIN = len(bot.ALLOWED_IDS) == 0

    def _press(self, data, context=None):
        upd = FakeUpdate(self.UID, data=data)
        ctx = context or FakeContext()
        run(bot.handle_callback(upd, ctx))
        return upd.rec, ctx

    def _say(self, text, context):
        upd = FakeUpdate(self.UID, text=text)
        run(bot.handle_text_message(upd, context))
        return upd.rec

    def _screen(self, data, ctx=None):
        """Последний нарисованный экран: (текст, callback_data кнопок, контекст)."""
        rec, ctx = self._press(data, ctx)
        edits = [c for c in rec.calls if c[0] == "edit"]
        self.assertTrue(edits, "%s: экран не отрисован" % data)
        datas = []
        if edits[-1][2].get("reply_markup") is not None:
            datas = [b.callback_data for row in edits[-1][2]["reply_markup"].inline_keyboard for b in row]
        return str(edits[-1][1][0]), datas, ctx


class HandlerWalkTests(_BotScreenCase):
    """Обход обработчиков экранов живыми вызовами на заглушке моста.

    Telegram подменён минимальными объектами: проверяем, что каждый экран
    отрисовывается без исключений, отвечает на callback и рисует клавиатуру с
    callback_data не длиннее 64 байт.
    """

    def test_secret_param_current_is_hidden(self):
        """K5-3: окно правки HeaderProtectionKey — «Сейчас: задан (скрыт)», значения на экране нет."""
        hpk = "SFBLdGVzdE9ubHlOb3RBUmVhbEtleUZvckJvdDAxMjM="
        real = bot.run_bot_action

        async def fake(action, timeout=60, **kw):
            if action == "profile-params":
                return {"status": "success", "message": "ok", "profile": kw.get("profile"),
                        "params": {"Jc": "4", "HeaderProtectionKey": hpk}}
            return await real(action, timeout=timeout, **kw)
        with patch.object(bot, "run_bot_action", fake):
            text, _, _ = self._screen("pr_pkey~awg3~HeaderProtectionKey")
            self.assertIn("задан (скрыт)", text)
            self.assertNotIn(hpk[:8], text)
            text, _, _ = self._screen("pr_pkey~awg3~Jc")
            self.assertIn("<code>4</code>", text)

    def test_every_exact_route_renders(self):
        skip = {"close_menu", "admin_cancel", "nt_later"}
        for data in sorted(bot.EXACT_HANDLERS):
            if data in skip:
                continue
            rec, _ = self._press(data)
            # Кнопки подтверждения без взведённого flow (pr_pok, pr_subnet_ok,
            # pr_punset, ua_done) честно отвечают алертом «ожидание истекло».
            alerted = any(c[2].get("show_alert") for c in rec.calls if c[0] == "answer")
            edits = [c for c in rec.calls if c[0] == "edit"]
            self.assertTrue(edits or rec.count("reply_text") or alerted, "%s: экран не отрисован" % data)
            if edits:
                # последний экран не должен быть голым «⏳» без клавиатуры
                self.assertIsNotNone(edits[-1][2].get("reply_markup"), "%s: нет клавиатуры" % data)

    def test_prefix_routes_render(self):
        samples = [
            "menu_users~1", "u_view~anna", "u_view~nobody", "u_quota~anna", "uq_set~anna~50",
            "u_exp~anna", "ue_set~anna~30", "u_en~anna~off", "u_ren~anna", "u_addp~anna",
            "u_addp_do~anna~awg1", "u_del~anna", "tc_cancel~u_view~anna", "st_log~awg2", "st_log~bogus",
            "pr_card~awg2", "pr_card~awg1", "pr_install~awg1", "pr_inst_do~awg1~def", "pr_remove~awg2",
            "pr_port~awg2", "pr_443~awg3", "pr_443_do~awg3", "pr_subnet~awg2", "pr_mtu~awg2",
            "pr_mtu_set~awg2~1376", "pr_params~awg2", "pr_pedit~awg2", "pr_pkey~awg2~S1", "pr_pkey~awg2~Jc",
            "pr_preset~awg2", "pr_preset_do~awg2~quic", "pr_regen~awg2", "pr_restart~awg2", "pr_rl~awg3", "pr_rl~zzz",
            "mt_rs~0", "se_dns_set~cf", "se_admin_del~5", "se_admin_del_ok~5", "lang_set_en", "lang_set_ru",
            "inst_awg2~def", "noop", "totally_unknown",
            "ag_view~a-1a2b", "ag_view~a-zzzz", "ag_view~a-9999", "ag_show~a-1a2b", "ag_show~a-9999",
            "ag_rot~a-1a2b", "ag_rot_do~a-1a2b", "ag_en~a-1a2b~off", "ag_en~a-3c4d~on", "ag_en~a-1a2b~maybe",
            "ag_del~a-1a2b", "ag_http~on", "ag_http~off", "ag_http~off_do", "ag_http~rnd", "ag_http~port",
            "ag_http~bogus", "ag_scope~write", "ag_days~30",
            "up_upd~awg2", "up_rb~awg3", "up_fix~awg2", "up_fix~zzz", "up_upd~zzz",
            "pr_restart_do~awg2", "pr_port_do~awg2~3456", "pr_port_do~awg2~70000",
            "ag_en~a-1a2b~off_do", "ag_en~a-3c4d~on_do", "ag_http~on_do", "ag_aips~any",
            "se_mini~rot_do", "se_mini~off_do", "se_alt_set~on~do",
            "imp_adopt~amnezia-awg", "imp_adopt~nginx-proxy",
            "imp_adopt_do~amnezia-awg", "imp_adopt_do~amnezia-xray", "imp_rec~awg2", "imp_rec_do~awg2",
            # ключ из приложения Amnezia: привязка старого ключа, переспрос перевыпуска, выдача мимо
            "u_att~proba~awg2", "u_att~anna~awg2", "u_att~anna~zzz", "u_att~nobody~awg2", "u_reis~proba~awg2",
            "u_share~olga", "u_share~proba", "u_key~proba~awg2~conf",
        ]
        for data in samples:
            rec, _ = self._press(data)
            self.assertGreaterEqual(rec.count("answer"), 1, "%s: нет query.answer()" % data)

    def test_key_delivery_two_styles(self):
        for fmt, what in (("qrconf", "reply_photo"), ("conf", "reply_document"),
                          ("vpn", "reply_text"), ("qrvpn", "reply_media_group")):
            rec, _ = self._press("u_key~anna~awg2~%s" % fmt)
            self.assertGreaterEqual(rec.count(what), 1, "%s → %s" % (fmt, what))
        rec, _ = self._press("u_key~anna~awg2~bogus")
        self.assertTrue(any(c[2].get("show_alert") for c in rec.calls if c[0] == "answer"))
        # секрет из .conf не попадает в текст подписи — только в документ
        rec, _ = self._press("u_key~anna~awg2~conf")
        for c in rec.calls:
            if c[0] == "reply_document":
                self.assertNotIn("FAKE-PRIVATE-KEY", str(c[2].get("caption")))

    def test_user_add_flow_end_to_end(self):
        """Мастер: имя → профили → лимит → срок. Вопроса про отображаемое имя
        больше нет (идентификатор и есть имя, как в SSH-мастере)."""
        args_file = os.path.join(TMP_ROOT, "flow_args.txt")
        os.environ["FAKE_BOT_ARGS_FILE"] = args_file
        try:
            rec, ctx = self._press("u_add")
            self.assertEqual(ctx.user_data["flow"]["kind"], "user_add")
            self._say("bad name!", ctx)                       # ошибка ввода ожидание не снимает
            self.assertEqual(ctx.user_data["flow"]["data"]["step"], "name")
            rec = self._say("newbie", ctx)
            # шаг «отображаемое имя» снят: сразу галочки профилей
            self.assertEqual(ctx.user_data["flow"]["data"]["step"], "profiles")
            self.assertNotIn("label", ctx.user_data["flow"]["data"])
            self._press("ua_tog~awg3", ctx)                   # снять галочку awg3
            self.assertEqual(ctx.user_data["flow"]["data"]["profiles"], ["awg2"])
            self._press("ua_done", ctx)
            self._press("ua_q~50", ctx)
            self.assertEqual(ctx.user_data["flow"]["data"]["step"], "days")
            self._say("abc", ctx)                             # не число — ждём дальше
            self.assertIn("flow", ctx.user_data)
            self._say("90", ctx)
            self.assertNotIn("flow", ctx.user_data)
        finally:
            os.environ.pop("FAKE_BOT_ARGS_FILE", None)
        with open(args_file, encoding="utf-8") as fh:
            lines = [l for l in fh.read().splitlines() if "--action=user-add" in l]
        self.assertEqual(len(lines), 1)
        argv = lines[0].split("\t")
        self.assertIn("--name=newbie", argv)
        # label мосту не передаём вовсе — ядро само ставит label = name
        # (lib/users.sh: [ -n "$label" ] || label="$name")
        self.assertFalse([a for a in argv if a.startswith("--label")], argv)
        self.assertIn("--profiles=awg2", argv)
        self.assertIn("--quota-gb=50", argv)
        self.assertIn("--days=90", argv)
        # ключа снятого шага нет ни в одном словаре
        for lang in ("ru", "en"):
            self.assertNotIn("user_ask_label", i18n._load_lang_file(lang))

    def test_user_add_empty_profiles_creates_keys_nowhere(self):
        """1c.7: сняты все галочки — ключ не должен лечь НИ В ОДНУ линейку.

        Страж стоит на кнопке «Готово», но экраны мастер шлёт новым сообщением,
        старые клавиатуры не гаснут, и живая кнопка срока из прошлого прогона
        приходит сразу в создание, минуя «Готово». Считаем линейки, в которых
        оказались ключи, а не код ответа моста.
        """
        args_file = os.path.join(TMP_ROOT, "empty_profiles_args.txt")
        open(args_file, "w").close()
        installed = bot.installed_profiles(run(bot.get_status(fresh=True)))
        self.assertEqual(installed, ["awg3", "awg2"])          # есть что терять (порядок SSH: 3.1 → 2.0 → 1.x)
        os.environ["FAKE_BOT_ARGS_FILE"] = args_file
        try:
            rec, ctx = self._press("u_add")
            self._say("nobody", ctx)
            self._press("ua_tog~awg2", ctx)
            self._press("ua_tog~awg3", ctx)
            self.assertEqual(ctx.user_data["flow"]["data"]["profiles"], [])
            rec, ctx = self._press("ua_done", ctx)             # страж на «Готово» держит
            self.assertTrue([c for c in rec.calls if c[0] == "answer" and c[2].get("show_alert")])
            self.assertEqual(ctx.user_data["flow"]["data"]["step"], "profiles")
            # а теперь перепрыгиваем через него живыми кнопками прошлых экранов
            rec, ctx = self._press("ua_q~50", ctx)
            rec, ctx = self._press("ua_d~90", ctx)
        finally:
            os.environ.pop("FAKE_BOT_ARGS_FILE", None)
        self.assertEqual(profiles_created(args_file, installed), [])
        texts = " ".join(str(c[1][0]) for c in rec.calls if c[0] in ("edit", "reply_text"))
        self.assertIn(i18n.t(self.UID, "user_pick_one_profile"), texts)

    def test_user_add_one_ticked_creates_key_in_one_line(self):
        """Обратная сторона 1c.7: одна галочка — ровно одна линейка, не «все»."""
        args_file = os.path.join(TMP_ROOT, "one_profile_args.txt")
        open(args_file, "w").close()
        installed = bot.installed_profiles(run(bot.get_status(fresh=True)))
        os.environ["FAKE_BOT_ARGS_FILE"] = args_file
        try:
            rec, ctx = self._press("u_add")
            self._say("solo", ctx)
            self._press("ua_tog~awg3", ctx)
            self._press("ua_done", ctx)
            self._press("ua_q~50", ctx)
            self._press("ua_d~90", ctx)
        finally:
            os.environ.pop("FAKE_BOT_ARGS_FILE", None)
        self.assertEqual(profiles_created(args_file, installed), ["awg2"])

    def test_user_bulk_flow_end_to_end(self):
        """«Добавить сразу несколько» (просьба владельца 03.10): число → имена (🎲 случайные) → галочки → итог одним
        сообщением без ключей. Кнопка — в разделе пользователей рядом с одиночным добавлением; мосту — count,
        profiles, lang и без prefix (случайные имена)."""
        text, datas, _ = self._screen("menu_users")
        self.assertIn("u_addm", datas)
        self.assertLess(datas.index("u_add"), datas.index("u_addm"))
        args_file = os.path.join(TMP_ROOT, "bulk_args.txt")
        open(args_file, "w").close()
        os.environ["FAKE_BOT_ARGS_FILE"] = args_file
        try:
            rec, ctx = self._press("u_addm")
            self.assertEqual(ctx.user_data["flow"]["kind"], "user_bulk")
            self.assertEqual(ctx.user_data["flow"]["data"]["step"], "count")
            datas = [b.callback_data for row in rec.calls[-1][2]["reply_markup"].inline_keyboard for b in row]
            self.assertEqual(datas, ["ub_n~5", "ub_n~10", "ub_n~20", "ub_n~50", "ub_n~100", "menu_users"])
            for bad in ("0", "101", "десять", "-3"):
                rec = self._say(bad, ctx)                    # ошибка ввода ожидание не снимает
                self.assertEqual(ctx.user_data["flow"]["data"]["step"], "count", bad)
                self.assertIn(i18n.tf(self.UID, "user_bulk_bad_count_fmt", 100), str(rec.calls[-1][1][0]))
            rec = self._say("12", ctx)
            self.assertEqual(ctx.user_data["flow"]["data"]["step"], "names")
            self.assertEqual(ctx.user_data["flow"]["data"]["count"], 12)
            datas = [b.callback_data for row in rec.calls[-1][2]["reply_markup"].inline_keyboard for b in row]
            self.assertEqual(datas, ["ub_nm~random", "ub_nm~prefix", "ub_back", "menu_users"])
            rec, ctx = self._press("ub_nm~random", ctx)
            self.assertEqual(ctx.user_data["flow"]["data"]["step"], "profiles")
            self.assertIn(i18n.t(self.UID, "user_bulk_names_random"), str(rec.calls[-1][1][0]))
            rec, ctx = self._press("ub_tog~awg3", ctx)
            self.assertEqual(ctx.user_data["flow"]["data"]["profiles"], ["awg2"])
            datas = [b.callback_data for row in rec.calls[-1][2]["reply_markup"].inline_keyboard for b in row]
            self.assertEqual(datas[-3:], ["ub_done", "ub_names", "menu_users"])
            rec, ctx = self._press("ub_done", ctx)
            self.assertNotIn("flow", ctx.user_data)
        finally:
            os.environ.pop("FAKE_BOT_ARGS_FILE", None)
        with open(args_file, encoding="utf-8") as fh:
            lines = [l.split("\t") for l in fh.read().splitlines() if "--action=users-add-bulk" in l]
        self.assertEqual(len(lines), 1)
        self.assertIn("--count=12", lines[0])
        self.assertIn("--profiles=awg2", lines[0])
        self.assertIn("--lang=%s" % i18n.get_user_lang(self.UID), lines[0])
        self.assertFalse([a for a in lines[0] if a.startswith("--prefix")])
        done = [str(c[1][0]) for c in rec.calls if c[0] == "reply_text"][-1]
        self.assertIn(i18n.tf(self.UID, "user_bulk_done_fmt", 12, FX20), done)
        self.assertIn("Человек 1 — <code>bulk_01</code>", done)
        self.assertIn(i18n.t(self.UID, "user_bulk_where_keys"), done)
        self.assertNotIn("PrivateKey", done)

    def test_user_bulk_preset_partial_and_back(self):
        """Число кнопкой, «Назад» с выбора имён к числу, с галочек — к выбору имён; сбой на середине — итог говорит,
        на ком встало и что добавленные остаются; ни одной галочки — алерт, мост не зовётся."""
        rec, ctx = self._press("u_addm")
        rec, ctx = self._press("ub_n~5", ctx)
        self.assertEqual(ctx.user_data["flow"]["data"]["count"], 5)
        self.assertEqual(ctx.user_data["flow"]["data"]["step"], "names")
        rec, ctx = self._press("ub_back", ctx)
        self.assertEqual(ctx.user_data["flow"]["data"]["step"], "count")
        rec, ctx = self._press("ub_n~5", ctx)
        rec, ctx = self._press("ub_nm~random", ctx)
        self.assertEqual(ctx.user_data["flow"]["data"]["step"], "profiles")
        rec, ctx = self._press("ub_names", ctx)
        self.assertEqual(ctx.user_data["flow"]["data"]["step"], "names")
        rec, ctx = self._press("ub_nm~random", ctx)
        self._press("ub_tog~awg2", ctx)
        self._press("ub_tog~awg3", ctx)
        rec, ctx = self._press("ub_done", ctx)
        self.assertTrue([c for c in rec.calls if c[0] == "answer" and c[2].get("show_alert")])
        self.assertIn("flow", ctx.user_data)
        self._press("ub_tog~awg3", ctx)
        os.environ["FAKE_BOT_BULK"] = "partial"
        try:
            rec, ctx = self._press("ub_done", ctx)
        finally:
            os.environ.pop("FAKE_BOT_BULK", None)
        done = [str(c[1][0]) for c in rec.calls if c[0] == "reply_text"][-1]
        self.assertIn(i18n.tf(self.UID, "user_bulk_done_fmt", 2, FX31), done)
        self.assertIn(i18n.tf(self.UID, "user_bulk_partial_fmt", 3, "bulk_03", "Не удалось сгенерировать ключи"), done)
        # Кнопка числа без мастера (бот перезапущен, мастер истёк) — объяснение с возвратом, а не молчание
        rec, _ = self._press("ub_n~5", FakeContext())
        edits = [c for c in rec.calls if c[0] == "edit"]
        self.assertIn(i18n.t(self.UID, "user_bulk_lost"), str(edits[-1][1][0]))

    def _bridge_lines(self, args_file, action):
        with open(args_file, encoding="utf-8") as fh:
            return [l.split("\t") for l in fh.read().splitlines() if "--action=%s\t" % action in l + "\t"]

    def test_user_bulk_delete_by_state_end_to_end(self):
        """«Удалить сразу несколько» (просьба владельца 03.10): кнопка в разделе пользователей под добавлением;
        способ → список того, кого удалит ядро (users-del-bulk-preview) → фраза DELETE <число> за 60 с, не та фраза —
        ничего → users-del-bulk с --confirm=<число> → итог одним сообщением без ключей."""
        text, datas, _ = self._screen("menu_users")
        self.assertIn("u_delm", datas)
        self.assertLess(datas.index("u_addm"), datas.index("u_delm"))
        args_file = os.path.join(TMP_ROOT, "bulk_del_args.txt")
        open(args_file, "w").close()
        os.environ["FAKE_BOT_ARGS_FILE"] = args_file
        try:
            text, datas, ctx = self._screen("u_delm")
            self.assertEqual(ctx.user_data["flow"]["kind"], "user_bulk_del")
            self.assertEqual(datas, ["ud_how~prefix", "ud_how~disabled", "ud_how~expired", "ud_how~quota", "menu_users"])
            text, datas, ctx = self._screen("ud_how~disabled", ctx)
            self.assertIn(i18n.tf(self.UID, "user_bulk_del_preview_fmt", 1, i18n.t(self.UID, "user_bulk_del_what_disabled")), text)
            self.assertIn("• <code>bob</code>", text)
            self.assertIn(i18n.t(self.UID, "user_bulk_del_warn"), text)
            self.assertIn("<code>DELETE 1</code>", text)
            self.assertEqual(datas, ["tc_cancel~menu_users"])
            self.assertNotIn("flow", ctx.user_data)
            rec = self._say("DELETE 2", ctx)                         # не та фраза — ничего не удалено
            self.assertIn(i18n.tf(self.UID, "confirm_mismatch_fmt", "DELETE 1"), str(rec.calls[-1][1][0]))
            self.assertEqual(self._bridge_lines(args_file, "users-del-bulk"), [])
            rec = self._say("DELETE 1", ctx)
        finally:
            os.environ.pop("FAKE_BOT_ARGS_FILE", None)
        preview = self._bridge_lines(args_file, "users-del-bulk-preview")
        self.assertEqual(len(preview), 1)
        self.assertIn("--state=disabled", preview[0])
        lines = self._bridge_lines(args_file, "users-del-bulk")
        self.assertEqual(len(lines), 1)
        self.assertIn("--confirm=1", lines[0])
        self.assertIn("--state=disabled", lines[0])
        done = [str(c[1][0]) for c in rec.calls if c[0] == "reply_text"][-1]
        self.assertIn(i18n.tf(self.UID, "user_bulk_del_done_fmt", 1), done)
        self.assertIn("• <code>bob</code>", done)
        self.assertIsNone(bot.CONFIRM.pending(self.UID))

    def test_user_bulk_delete_by_prefix_text_and_partial(self):
        """Префикс текстом (хвостовой дефис снимается, негодный — переспрос), итог с оставленными выключенными."""
        rec, ctx = self._press("u_delm")
        text, datas, ctx = self._screen("ud_how~prefix", ctx)
        self.assertEqual(ctx.user_data["flow"]["data"]["step"], "prefix")
        self.assertEqual(datas, ["u_delm"])
        rec = self._say("bad prefix!", ctx)
        self.assertIn(i18n.t(self.UID, "user_bulk_bad_prefix"), str(rec.calls[-1][1][0]))
        self.assertEqual(ctx.user_data["flow"]["data"]["step"], "prefix")
        rec = self._say("team-", ctx)
        shown = str(rec.calls[-1][1][0])
        self.assertIn(i18n.tf(self.UID, "user_bulk_del_preview_fmt", 3, i18n.tf(self.UID, "user_bulk_del_what_prefix_fmt", "team")), shown)
        self.assertIn("<code>DELETE 3</code>", shown)
        os.environ["FAKE_BOT_BULK_DEL"] = "partial"
        try:
            rec = self._say("DELETE 3", ctx)
        finally:
            os.environ.pop("FAKE_BOT_BULK_DEL", None)
        done = [str(c[1][0]) for c in rec.calls if c[0] == "reply_text"][-1]
        self.assertIn(i18n.tf(self.UID, "user_bulk_del_done_fmt", 1), done)
        self.assertIn(i18n.tf(self.UID, "user_bulk_del_partial_fmt", 2, "team-02",
                              "контейнер не принял изменения — пачка оставлена в списке выключенной"), done)
        self.assertIn(i18n.tf(self.UID, "user_bulk_del_kept_fmt", 2), done)

    def test_user_bulk_delete_nobody_too_many_and_a_changed_list(self):
        """Никого — «удалять некого», больше 100 — «частями», фраза не взводится; список изменился между показом и
        удалением — отказ ядра с «Показать заново» тем же выбором."""
        bot.CONFIRM.cancel(self.UID)
        text, datas, _ = self._screen("ud_how~expired")
        self.assertIn(i18n.tf(self.UID, "user_bulk_del_none_fmt", i18n.t(self.UID, "user_bulk_del_what_expired")), text)
        self.assertEqual(datas, ["menu_users"])
        text, datas, _ = self._screen("ud_how~quota")
        self.assertIn(i18n.tf(self.UID, "user_bulk_del_many_fmt", i18n.t(self.UID, "user_bulk_del_what_quota"), 150, 100), text)
        self.assertIsNone(bot.CONFIRM.pending(self.UID))
        text, datas, ctx = self._screen("ud_pfx~team")
        self.assertIn("<code>DELETE 3</code>", text)
        os.environ["FAKE_BOT_BULK_DEL"] = "mismatch"
        try:
            rec = self._say("DELETE 3", ctx)
        finally:
            os.environ.pop("FAKE_BOT_BULK_DEL", None)
        last = [c for c in rec.calls if c[0] == "reply_text"][-1]
        self.assertIn("Подтверждено 3, а выбрано сейчас 4", str(last[1][0]))
        datas = [b.callback_data for row in last[2]["reply_markup"].inline_keyboard for b in row]
        self.assertEqual(datas, ["ud_pfx~team", "menu_users"])
        rec, _ = self._press("ud_how~bogus")
        self.assertTrue([c for c in rec.calls if c[0] == "answer" and c[2].get("show_alert")])

    def test_user_add_flow_step_back(self):
        """«⬅️ Назад» на каждом шаге: ответы не теряются, мастер не начинается
        сначала. На первом шаге «Назад» — выход к списку пользователей."""
        rec, ctx = self._press("u_add")
        markup = rec.calls[-1][2]["reply_markup"]
        datas = [b.callback_data for row in markup.inline_keyboard for b in row]
        # первый шаг: «🎲 Случайное имя» и «Назад» — без дубля «Отмена»
        self.assertEqual(datas, ["ua_rnd", "menu_users"])
        self._say("stepper", ctx)
        self.assertEqual(ctx.user_data["flow"]["data"]["step"], "profiles")
        self._press("ua_tog~awg3", ctx)
        self.assertEqual(ctx.user_data["flow"]["data"]["profiles"], ["awg2"])
        rec, ctx = self._press("ua_done", ctx)                # → лимит
        self.assertEqual(ctx.user_data["flow"]["data"]["step"], "quota")
        datas = [b.callback_data for row in rec.calls[-1][2]["reply_markup"].inline_keyboard for b in row]
        self.assertIn("ua_back", datas)
        rec, ctx = self._press("ua_q~50", ctx)                # → срок
        self.assertEqual(ctx.user_data["flow"]["data"]["step"], "days")
        datas = [b.callback_data for row in rec.calls[-1][2]["reply_markup"].inline_keyboard for b in row]
        self.assertIn("ua_back", datas)
        rec, ctx = self._press("ua_back", ctx)                # срок → лимит
        self.assertEqual(ctx.user_data["flow"]["data"]["step"], "quota")
        rec, ctx = self._press("ua_back", ctx)                # лимит → профили (галочка цела)
        self.assertEqual(ctx.user_data["flow"]["data"]["step"], "profiles")
        self.assertEqual(ctx.user_data["flow"]["data"]["profiles"], ["awg2"])
        rec, ctx = self._press("ua_back", ctx)                # профили → имя (имя цело)
        self.assertEqual(ctx.user_data["flow"]["data"]["step"], "name")
        self.assertEqual(ctx.user_data["flow"]["data"]["name"], "stepper")
        # мастер продолжается с того же места, а не с нуля
        self._say("stepper2", ctx)
        self.assertEqual(ctx.user_data["flow"]["data"]["step"], "profiles")
        # «Назад» без взведённого мастера — не молчание, а объяснение с кнопкой
        rec, _ = self._press("ua_back", FakeContext())
        edits = [c for c in rec.calls if c[0] == "edit"]
        self.assertIn(i18n.t(self.UID, "user_add_lost"), str(edits[-1][1][0]))
        self.assertIsNotNone(edits[-1][2].get("reply_markup"))

    def test_user_add_single_profile_skips_checkboxes(self):
        """Установлен ровно один профиль — экрана с галочками нет, а в итоге
        сказано, куда попал ключ."""
        args_file = os.path.join(TMP_ROOT, "single_args.txt")
        os.environ["FAKE_BOT_ARGS_FILE"] = args_file
        status = run(bot.get_status())
        one = dict(status, profiles=[p for p in status["profiles"] if p.get("profile") == "awg2"])

        async def only_awg2(fresh=False):
            return one

        real_status = bot.get_status
        bot.get_status = only_awg2
        try:
            rec, ctx = self._press("u_add")
            rec = self._say("solo", ctx)
            # экран галочек пропущен: сразу лимит
            self.assertEqual(ctx.user_data["flow"]["data"]["step"], "quota")
            self.assertEqual(ctx.user_data["flow"]["data"]["profiles"], ["awg2"])
            self.assertEqual(ctx.user_data["flow"]["data"]["single_profile"], "awg2")
            texts = " ".join(str(c[1][0]) for c in rec.calls if c[0] == "reply_text")
            self.assertNotIn(i18n.t(self.UID, "user_ask_profiles"), texts)
            # «Назад» с лимита ведёт к имени, а не к несуществующему экрану
            rec, ctx = self._press("ua_back", ctx)
            self.assertEqual(ctx.user_data["flow"]["data"]["step"], "name")
            self._say("solo", ctx)
            rec, ctx = self._press("ua_q~10", ctx)
            rec, ctx = self._press("ua_d~30", ctx)
            texts = " ".join(str(c[1][0]) for c in rec.calls if c[0] == "reply_text")
            self.assertIn(i18n.tf(self.UID, "user_add_single_profile_fmt", bot.profile_title(self.UID, "awg2")), texts)
        finally:
            bot.get_status = real_status
            os.environ.pop("FAKE_BOT_ARGS_FILE", None)
        with open(args_file, encoding="utf-8") as fh:
            lines = [l for l in fh.read().splitlines() if "--action=user-add" in l]
        self.assertEqual(len(lines), 1)
        self.assertIn("--profiles=awg2", lines[0].split("\t"))

    def test_del_profile_pair_exists_and_confirms(self):
        """Разбор критика, находка 34: подсказка звала к кнопке, которой не было.

        Подсказка после импорта обязана называть ту кнопку, которой дело
        делается. Раньше это была пара «Убрать профиль» + «Добавить профиль»;
        теперь у импортированного пира есть своя кнопка перевыпуска, и подсказка
        зовёт к ней — а пара остаётся тем, чем и была, и по-прежнему переспрашивает.
        """
        for lang in ("ru", "en"):
            hint = i18n.t_in(lang, "mt_import_no_privkey_hint")
            # Оба выхода: привязать старый ключ (без потерь) и перевыпустить
            for key in ("user_peer_reissue_btn", "user_peer_attach_btn"):
                label = i18n.t_in(lang, key).split(" ", 1)[1]
                self.assertIn(label, hint, "%s: подсказка не называет кнопку «%s»" % (lang, label))
        args_file = os.path.join(TMP_ROOT, "delp_args.txt")
        os.environ["FAKE_BOT_ARGS_FILE"] = args_file
        try:
            text, datas, ctx = self._screen("u_delp~anna")
            self.assertIn("u_delp_ask~anna~awg2", datas)
            self.assertIn("u_delp_ask~anna~awg3", datas)
            text, datas, ctx = self._screen("u_delp_ask~anna~awg3", ctx)
            self.assertIn(i18n.t(self.UID, "confirm_title"), text)
            self.assertIn("u_delp_do~anna~awg3", datas)
            self._press("u_delp_do~anna~awg3", ctx)
            rec, _ = self._press("u_delp_ask~anna~zzz")     # чужой профиль — алерт, не тишина
            self.assertTrue(any(c[2].get("show_alert") for c in rec.calls if c[0] == "answer"))
        finally:
            os.environ.pop("FAKE_BOT_ARGS_FILE", None)
        with open(args_file, encoding="utf-8") as fh:
            self.assertIn("--action=user-del-peer\t--json\t--name=anna\t--profile=awg3", fh.read())

    # ── Импортированный пир: has_key=false ──────────────────────────────────
    # Пользователь, подхваченный из контейнера приложения Amnezia, приходит без
    # приватной части ключа. Четыре кнопки выдачи по такому профилю отвечали
    # `no_private_key`, и правду человек узнавал, только ткнув в них.

    @staticmethod
    def _imported_user():
        """Один профиль импортирован, второй свой: признак — на пире, не на человеке."""
        return {"name": "proba", "label": "proba", "enabled": True, "imported": True,
                "quota_bytes": 0, "expires": "", "traffic": {"rx": 0, "tx": 0},
                "profiles": ["awg2", "awg3"],
                "peers": {"awg2": {"ip": "10.8.2.9", "pubkey": "PUB2", "active": True, "has_key": False},
                          "awg3": {"ip": "10.8.3.9", "pubkey": "PUB3", "active": True, "has_key": True}}}

    def _card_callbacks(self, user):
        markup = bot.user_card_keyboard(None, user)
        return [b.callback_data for row in markup.inline_keyboard for b in row]

    def test_imported_peer_offers_reissue_instead_of_key_buttons(self):
        """Кнопок выдачи на импортированном профиле нет — есть два выхода: привязать старый ключ
        (первым: без потерь) и перевыпуск; на своём профиле — наоборот."""
        user = self._imported_user()
        cbs = self._card_callbacks(user)
        for fmt in ("qrconf", "conf", "vpn", "qrvpn"):
            self.assertNotIn("u_key~proba~awg2~%s" % fmt, cbs,
                             "кнопка выдачи на импортированном профиле гарантированно ведёт в no_private_key")
            self.assertIn("u_key~proba~awg3~%s" % fmt, cbs)
        self.assertIn("u_reis~proba~awg2", cbs)
        self.assertIn("u_att~proba~awg2", cbs)
        self.assertLess(cbs.index("u_att~proba~awg2"), cbs.index("u_reis~proba~awg2"),
                        "выход без потерь стоит первым")
        self.assertNotIn("u_reis~proba~awg3", cbs, "на своём ключе перевыпуск не навязываем")
        self.assertNotIn("u_att~proba~awg3", cbs, "свой ключ на сервере уже есть — привязывать нечего")
        # Раздача по ссылке — та же выдача файла: пока есть хоть один свой ключ,
        # кнопка нужна; когда все пиры импортированы — она только обманывает.
        self.assertIn("u_share~proba", cbs)
        user["peers"]["awg3"]["has_key"] = False
        cbs = self._card_callbacks(user)
        self.assertNotIn("u_share~proba", cbs)
        self.assertEqual(sorted(c for c in cbs if c.startswith("u_reis~")),
                         ["u_reis~proba~awg2", "u_reis~proba~awg3"])
        # Старый мост поля не присылает: молчать про рабочую выдачу нельзя
        r, anna = run(bot._fetch_user("anna"))
        self.assertNotIn("has_key", anna["peers"]["awg2"])
        cbs = self._card_callbacks(anna)
        self.assertIn("u_key~anna~awg2~conf", cbs)
        self.assertFalse([c for c in cbs if c.startswith("u_reis~")])

    def test_imported_peer_is_explained_in_the_card_text(self):
        """Карточка говорит словами (правило владельца): где ключ, что у человека всё работает,
        почему отсюда его не показать, и оба выхода — привязать старый ключ или перевыпустить."""
        status = run(bot.get_status())
        note = i18n.tf(self.UID, "user_peer_imported_note_fmt", FX20)
        text = bot.user_card_text(self.UID, self._imported_user(), status)
        self.assertEqual(text.count(note), 1, "строка про импорт стоит ровно у импортированного профиля")
        self.assertNotIn(i18n.tf(self.UID, "user_peer_imported_note_fmt", "AmneziaWG 3.1"), text)
        self.assertIn("AmneziaWG 2.0", text)
        self.assertIn("AmneziaWG 3.1", text)
        self.assertTrue(_html_balanced(text), text)
        # Каноничные слова docs/CONTRACT.md: четыре смысла на обоих языках
        must = {"ru": ("выдан в приложении Amnezia", "открытая часть", "в ссылке или файле", "всё работает",
                       "отсюда нельзя", "Привязать старый ключ", "Перевыпустить ключ", "сразу перестанет подключаться"),
                "en": ("issued in the Amnezia app", "public part", "link or file", "keeps working",
                       "cannot be shown or sent from here", "Attach the old key", "Reissue the key", "stops connecting at once")}
        for lang, parts in must.items():
            body = i18n.t_in(lang, "user_peer_imported_note_fmt")
            for part in parts:
                self.assertIn(part, body, "%s: нет «%s»" % (lang, part))
            self.assertEqual(body.count("%s"), 1, lang)
        r, anna = run(bot._fetch_user("anna"))
        self.assertNotIn(i18n.tf(self.UID, "user_peer_imported_note_fmt", "AmneziaWG 2.0"), bot.user_card_text(self.UID, anna, status))

    def test_peer_reissue_asks_before_it_destroys(self):
        """Разрушительная кнопка переспрашивает отдельным экраном — как «Убрать профиль»."""
        args_file = os.path.join(TMP_ROOT, "reissue_args.txt")
        os.environ["FAKE_BOT_ARGS_FILE"] = args_file
        self.addCleanup(os.environ.pop, "FAKE_BOT_ARGS_FILE", None)
        text, datas, ctx = self._screen("u_reis~anna~awg2")
        self.assertIn(i18n.t(self.UID, "confirm_title"), text)
        self.assertIn(bot.profile_title(self.UID, "awg2"), text)
        self.assertIn("u_reis_do~anna~awg2", datas)
        self.assertIn("u_view~anna", datas, "отказаться можно, не выходя из карточки")
        # Н-4: как в панели — у anna страница с awg2 и подписка с его линией: открыть страницу заново, подписка — до 12 ч
        self.assertIn(i18n.t(self.UID, "reis_pages"), text)
        self.assertIn(i18n.tf(self.UID, "reis_sub_fmt", "до 12 ч"), text)
        self.assertEqual(self._actions(args_file), [],
                         "переспрос уже сходил к мосту: подтверждать было бы нечего")
        text, datas, ctx = self._screen("u_reis_do~anna~awg2", ctx)
        self.assertEqual(self._actions(args_file), ["user-reissue-peer"],
                         "перевыпуск — одно действие ядра: пара оставляла окно без доступа")
        with open(args_file, encoding="utf-8") as fh:
            argv = fh.read()
        self.assertIn("--action=user-reissue-peer\t--json\t--name=anna\t--profile=awg2", argv)
        # Вернулись в карточку: новый ключ забирают тут же, кнопками выдачи
        self.assertIn(i18n.tf(self.UID, "user_peer_reissued_fmt", bot.profile_title(self.UID, "awg2")), text)
        self.assertIn(i18n.tf(self.UID, "reis_done_sub_fmt", "до 12 ч"), text)
        self.assertIn("u_key~anna~awg2~conf", datas)
        # Н-4: ни страниц, ни подписки — «отправьте новый ключ»
        os.environ["FAKE_BOT_PAGES"] = "empty"
        os.environ["FAKE_BOT_SUBS"] = "empty"
        self.addCleanup(os.environ.pop, "FAKE_BOT_PAGES", None)
        self.addCleanup(os.environ.pop, "FAKE_BOT_SUBS", None)
        text, _, _ = self._screen("u_reis~anna~awg2")
        self.assertIn(i18n.t(self.UID, "reis_only_manual"), text)
        self.assertNotIn(i18n.tf(self.UID, "reis_sub_fmt", "до 12 ч"), text)
        self.assertEqual(bot.reissue_facts_text(self.UID, {"pages": None, "sub": False, "hours": 24}, False),
                         i18n.t(self.UID, "reis_manual") + " " + i18n.tf(self.UID, "reis_unknown_fmt", "до 24 ч"))
        rec, _ = self._press("u_reis~anna~zzz")         # чужой профиль — алерт, не тишина
        self.assertTrue(any(c[2].get("show_alert") for c in rec.calls if c[0] == "answer"))
        rec, _ = self._press("u_reis_do~anna~zzz")
        self.assertTrue(any(c[2].get("show_alert") for c in rec.calls if c[0] == "answer"))

    def test_reissue_windows_name_slow_apps(self):
        """A4-3/B4-4 (как панель): подписку FlClash забирает раз в сутки — «до 12 ч, у FlClash — до 24 ч»; приложения
        с одним сроком — вместе, сначала самые долгие; не дольше совета сервера и мусор — не называются."""
        slow = [{"name": "FlClash", "hours": 24}, {"name": "Box", "hours": 48}, {"name": "Cat", "hours": 24},
                {"name": "Fast", "hours": 6}, {"name": "", "hours": 72}, {"name": "Bool", "hours": True}, "x"]
        f = {"pages": 0, "sub": True, "hours": 12, "slow": slow}
        self.assertEqual(bot.sub_within(self.UID, f), "до 12 ч, у Box — до 48 ч, у FlClash, Cat — до 24 ч")
        self.assertIn("при следующем обновлении (до 12 ч, у Box — до 48 ч, у FlClash, Cat — до 24 ч)",
                      bot.reissue_facts_text(self.UID, f, False))
        self.assertIn("заберёт его само (до 12 ч, у Box — до 48 ч, у FlClash, Cat — до 24 ч)",
                      bot.reissue_facts_text(self.UID, f, True))
        self.assertEqual(bot.sub_within(self.UID, {"hours": 24, "slow": [{"name": "FlClash", "hours": 24}]}), "до 24 ч")
        self.assertEqual(bot.sub_within(self.UID, {"hours": 12}), "до 12 ч")
        self.assertEqual(bot.sub_within(None, {"hours": 12, "slow": [{"name": "FlClash", "hours": 24}]}).count("24"), 1)
        en = i18n.t_in("en", "reis_within_slow_fmt") % (12, i18n.t_in("en", "reis_within_app_fmt") % ("FlClash", 24))
        self.assertEqual(en, "within 12 h, FlClash — within 24 h")

    @staticmethod
    def _actions(args_file):
        """Действия моста по записанным argv, по порядку вызовов."""
        if not os.path.exists(args_file):
            return []
        out = []
        with open(args_file, encoding="utf-8") as fh:
            for line in fh.read().splitlines():
                for a in line.split("\t"):
                    if a.startswith("--action="):
                        out.append(a.split("=", 1)[1])
        return [a for a in out if a in ("user-reissue-peer", "user-del-peer", "user-add-peer")]

    def test_peer_reissue_says_loudly_when_the_key_was_lost(self):
        """Перевыпуск не прошёл — говорим громко и называем, чем вернуть доступ.

        Ядро при отказе возвращает прежний ключ, но если не удалось и это, человек
        остаётся без доступа на профиль — молчать нельзя ни в том, ни в другом случае.
        """
        calls = []
        err = {"status": "error", "message": "нет свободных адресов", "code": "no_free_ip"}

        async def fake_mutate(action, who=None, timeout=None, **params):
            calls.append(action)
            return dict(err) if action == "user-reissue-peer" else {"status": "success", "message": "ok"}

        saved = bot.mutate
        bot.mutate = fake_mutate
        self.addCleanup(setattr, bot, "mutate", saved)
        text, datas, ctx = self._screen("u_reis_do~anna~awg2")
        self.assertEqual(calls, ["user-reissue-peer"])
        want = i18n.tf(self.UID, "user_peer_reissue_lost_fmt",
                       bot.error_text(self.UID, err),
                       bot.esc(bot.profile_title(self.UID, "awg2")),
                       bot.esc(i18n.t(self.UID, "user_add_profile")))
        self.assertIn(want, text)
        self.assertIn(err["message"], text, "что именно сорвалось — тоже словами")
        self.assertNotIn(i18n.tf(self.UID, "user_peer_reissued_fmt", bot.profile_title(self.UID, "awg2")), text)
        # Кнопка, которой возвращают доступ, названа в тексте и стоит тут же
        self.assertIn(i18n.t(self.UID, "user_add_profile").split(" ", 1)[1], text)
        self.assertIn("u_addp~anna", datas)

    def test_typed_confirm_delete_flow(self):
        args_file = os.path.join(TMP_ROOT, "del_args.txt")
        os.environ["FAKE_BOT_ARGS_FILE"] = args_file
        try:
            rec, ctx = self._press("u_del~anna")
            self.assertIsNotNone(bot.CONFIRM.pending(self.UID))
            rec = self._say("delete anna", ctx)               # не совпало — ждём
            self.assertIsNotNone(bot.CONFIRM.pending(self.UID))
            rec = self._say("DELETE anna", ctx)
            self.assertIsNone(bot.CONFIRM.pending(self.UID))
            texts = " ".join(str(c[1][0]) for c in rec.calls if c[0] == "reply_text")
            self.assertIn("anna", texts)
            # любая кнопка снимает ожидание фразы
            self._press("pr_remove~awg2", ctx)
            self.assertIsNotNone(bot.CONFIRM.pending(self.UID))
            self._press("sec_state", ctx)
            self.assertIsNone(bot.CONFIRM.pending(self.UID))
            rec = self._say("REMOVE awg2", ctx)
            texts = " ".join(str(c[1][0]) for c in rec.calls if c[0] == "reply_text")
            self.assertEqual(texts, i18n.t(self.UID, "text_no_waiter"))
        finally:
            os.environ.pop("FAKE_BOT_ARGS_FILE", None)
        with open(args_file, encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn("--action=user-del\t--json\t--name=anna", content)
        # удаление не выполнено; до фразы — только чтение: чьи страницы перестанут работать (PROFILE-REMOVE-PAGES)
        self.assertNotIn("--action=profile-remove\t", content)
        self.assertIn("--action=profile-remove-preview\t--json\t--profile=awg2", content)

    def test_upstream_update_typed_confirm_flow(self):
        args_file = os.path.join(TMP_ROOT, "up_args.txt")
        os.environ["FAKE_BOT_ARGS_FILE"] = args_file
        try:
            rec, ctx = self._press("mt_upstream")
            edits = [c for c in rec.calls if c[0] == "edit"]
            self.assertIn("3.2.20260915", str(edits[-1][1]))
            rec, ctx = self._press("up_upd~awg2", ctx)
            self.assertIsNotNone(bot.CONFIRM.pending(self.UID))
            self.assertEqual(bot.CONFIRM.pending(self.UID)["phrase"], "443")   # порт профиля, не awg2 (D-5)
            texts = " ".join(str(c[1][0]) for c in rec.calls if c[0] == "edit")
            self.assertIn("3.2.20260915", texts)          # доступный тег из сохранённого ответа
            rec = self._say("443", ctx)
            self.assertIsNone(bot.CONFIRM.pending(self.UID))
            texts = " ".join(str(c[1][0]) for c in rec.calls if c[0] == "reply_text")
            self.assertIn("3.2.20260915", texts)
            self.assertIn("CONTENT_PADDING_MAX", texts)   # предупреждение о неподдержанном параметре
            rec, ctx = self._press("up_rb~awg3", ctx)
            phrase = bot.CONFIRM.pending(self.UID)["phrase"]
            self.assertRegex(phrase, r"^\d+$")
            rec = self._say(phrase, ctx)
            texts = " ".join(str(c[1][0]) for c in rec.calls if c[0] == "reply_text")
            self.assertIn("3.1.20260828", texts)
            # импорт: подхват legacy-контейнера и сверка с подхватом изменений
            rec, ctx = self._press("mt_import", ctx)
            rec, ctx = self._press("imp_adopt~amnezia-awg", ctx)
            texts = " ".join(str(c[1][0]) for c in rec.calls if c[0] == "edit")
            self.assertIn("AmneziaWG 1.0", texts)
            rec, ctx = self._press("imp_adopt_do~amnezia-awg", ctx)
            texts = " ".join(str(c[1][0]) for c in rec.calls if c[0] == "edit")
            self.assertIn("Keenetic", texts)
            rec, ctx = self._press("imp_rec~awg2", ctx)
            texts = " ".join(str(c[1][0]) for c in rec.calls if c[0] == "edit")
            self.assertIn("Laptop", texts)
            rec, ctx = self._press("imp_rec_do~awg2", ctx)
            texts = " ".join(str(c[1][0]) for c in rec.calls if c[0] == "edit")
            self.assertIn("Laptop", texts)
        finally:
            os.environ.pop("FAKE_BOT_ARGS_FILE", None)
        with open(args_file, encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn("--action=upstream-update\t--json\t--profile=awg2", content)
        self.assertIn("--action=upstream-rollback\t--json\t--profile=awg3", content)
        self.assertIn("--action=import-adopt\t--json\t--container=amnezia-awg", content)
        self.assertIn("--action=import-reconcile\t--json\t--profile=awg2\t--apply=on", content)

    def test_agent_screens_add_flow_delete_and_transport(self):
        """Доступы агентов: список без секрета, адрес только по кнопке, выдача
        (название → уровень → срок), удаление фразой DELETE a-…, транспорт в Lite."""
        args_file = os.path.join(TMP_ROOT, "agent_args.txt")
        os.environ["FAKE_BOT_ARGS_FILE"] = args_file
        try:
            rec, ctx = self._press("se_agents")
            text = rec.calls[-1][1][0]
            self.assertIn("a-1a2b", text)
            self.assertIn("https://203.0.113.10", text)                       # база транспорта — не секрет
            self.assertNotIn("FAKESECRET", text)                               # список без адресов-ключей
            markup = rec.calls[-1][2]["reply_markup"]
            datas = [b.callback_data for row in markup.inline_keyboard for b in row]
            self.assertIn("ag_view~a-1a2b", datas)
            self.assertIn("ag_add", datas)
            self.assertIn("ag_http~off", datas)                                # транспорт включён → кнопка выключения
            # карточка и адрес только по явной кнопке
            rec, ctx = self._press("ag_view~a-1a2b")
            text = rec.calls[-1][1][0]
            self.assertIn("203.0.113.5", text)                                 # последний IP
            self.assertNotIn("FAKESECRET", text)
            rec, ctx = self._press("ag_show~a-1a2b")
            text = rec.calls[-1][1][0]
            self.assertIn("<code>https://203.0.113.10/a-FAKESECRETFAKESECRETFAKESECRETFAKESECRET01/mcp</code>", text)
            self.assertIn("claude mcp add --transport http goamnezia", text)
            self.assertIn(i18n.t(self.UID, "ag_url_is_key"), text)
            self.assertIn("AA:BB:CC", text)                                    # отпечаток самоподписанного
            self.assertTrue(rec.calls[-1][2].get("disable_web_page_preview", True))
            # выдача: название → уровень → срок
            rec, ctx = self._press("ag_add")
            self.assertEqual(ctx.user_data["flow"]["kind"], "agent_add")
            self._say("x" * 60, ctx)                                           # длиннее 48 — ждём дальше
            self.assertEqual(ctx.user_data["flow"]["data"]["step"], "label")
            rec = self._say("Мой агент", ctx)
            self.assertEqual(ctx.user_data["flow"]["data"]["step"], "scope")
            markup = rec.calls[-1][2]["reply_markup"]
            datas = [b.callback_data for row in markup.inline_keyboard for b in row]
            self.assertEqual([d for d in datas if d.startswith("ag_scope~")], ["ag_scope~read", "ag_scope~write", "ag_scope~admin"])
            # Рядом с кнопками сказано, что даёт каждый уровень: владелец, выдающий
            # доступ из бота, иначе раздавал бы приватные ключи всех клиентов, не
            # прочитав об этом ни слова (DEFECTS §1d.1 №2).
            scope_screen = rec.calls[-1][1][0]
            self.assertIn(i18n.t(self.UID, "ag_scope_note"), scope_screen)
            self.assertIn("ВЫДАЧА приватного ключа", scope_screen)
            self._press("ag_scope~write", ctx)
            self.assertEqual(ctx.user_data["flow"]["data"]["step"], "days")
            rec, _ = self._press("ag_days~90", ctx)
            # Мастер вырос: после срока спрашивают привязку к адресу и вид
            # доступа. Проверка шла по старой длине и объявляла мастер
            # незавершённым — пример того, как отставший тест выглядит как
            # дефект продукта.
            self.assertEqual(ctx.user_data["flow"]["data"]["step"], "pin")
            rec, _ = self._press("ag_apin~off", ctx)
            # Белый список адресов теперь спрашивают и в боте (SSH и панель
            # спрашивали, а карточка поле показывала всегда пустым).
            self.assertEqual(ctx.user_data["flow"]["data"]["step"], "ips")
            self._say("300.1.1.1", ctx)                                        # не IPv4 — ждём дальше
            self.assertEqual(ctx.user_data["flow"]["data"]["step"], "ips")
            self._say("203.0.113.5, 198.51.100.0/24", ctx)
            self.assertEqual(ctx.user_data["flow"]["data"]["step"], "mode")
            self.assertEqual(ctx.user_data["flow"]["data"]["ips"], "203.0.113.5,198.51.100.0/24")
            rec, _ = self._press("ag_amode~static", ctx)
            self.assertNotIn("flow", ctx.user_data)
            texts = " ".join(str(c[1][0]) for c in rec.calls if c[0] == "reply_text")
            self.assertIn("<code>https://203.0.113.10/a-NEWSECRETNEWSECRETNEWSECRETNEWSECRETNEWSEC/mcp</code>", texts)
            self.assertIn(i18n.t(self.UID, "ag_url_is_key"), texts)
            # удаление — только фразой
            rec, ctx = self._press("ag_del~a-1a2b")
            self.assertIsNotNone(bot.CONFIRM.pending(self.UID))
            self._say("DELETE a-9999", ctx)
            self.assertIsNotNone(bot.CONFIRM.pending(self.UID))
            rec = self._say("DELETE a-1a2b", ctx)
            self.assertIsNone(bot.CONFIRM.pending(self.UID))
            texts = " ".join(str(c[1][0]) for c in rec.calls if c[0] == "reply_text")
            self.assertIn("a-1a2b", texts)
            # транспорт в Lite: порт случайный или свой
            os.environ["FAKE_BOT_MCP_MODE"] = "lite"
            try:
                rec, ctx = self._press("ag_http~on")
                markup = rec.calls[-1][2]["reply_markup"]
                datas = [b.callback_data for row in markup.inline_keyboard for b in row]
                self.assertIn("ag_http~rnd", datas)
                self.assertIn("ag_http~port", datas)
                rec, ctx = self._press("ag_http~port")
                self.assertEqual(ctx.user_data["flow"]["kind"], "agent_port")
                self._say("80", ctx)                                           # привилегированный — ждём дальше
                self.assertIn("flow", ctx.user_data)
                self._say("40000", ctx)
                self.assertNotIn("flow", ctx.user_data)
                self._press("ag_http~rnd")
            finally:
                os.environ.pop("FAKE_BOT_MCP_MODE", None)
        finally:
            os.environ.pop("FAKE_BOT_ARGS_FILE", None)
        with open(args_file, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        adds = [l for l in lines if "--action=agent-add" in l]
        self.assertEqual(len(adds), 1)
        self.assertIn("--label=Мой агент", adds[0].split("\t"))
        self.assertIn("--scope=write", adds[0].split("\t"))
        self.assertIn("--days=90", adds[0].split("\t"))
        self.assertTrue(any("--action=agent-del\t--json\t--id=a-1a2b" in l for l in lines))
        self.assertTrue(any("--action=mcp-http-on\t--json\t--port=40000" in l for l in lines))
        rnd = [l for l in lines if "--action=mcp-http-on" in l and "--port=40000" not in l]
        self.assertEqual(len(rnd), 1)
        port = int(rnd[0].split("--port=")[1].split("\t")[0])
        # Диапазон сменился по просьбе владельца: ближе к трём-пяти тысячам,
        # не выше десяти. Тот же предел, что у портов профилей.
        self.assertTrue(1024 <= port <= 9999, port)
        # секреты не попадают в argv моста и не логируются ботом — только id
        self.assertFalse(any("FAKESECRET" in l or "NEWSECRET" in l for l in lines))

    def test_agent_add_keeps_the_wizard_when_the_bridge_refuses(self):
        """Отказ моста на последнем шаге не стирает набранное.

        flow_clear стоял ДО вызова моста: занятый порт, нехватка места или спор за
        замок — и владелец шёл проходить мастер с самого начала (название, уровень,
        срок, привязка, белый список). Теперь состояние остаётся на шаге «режим»,
        и повтор — это одна кнопка.
        """
        rec, ctx = self._press("ag_add")
        self._say("Агент для отказа", ctx)
        self._press("ag_scope~read", ctx)
        self._press("ag_days~90", ctx)
        self._press("ag_apin~off", ctx)
        self._press("ag_aips~any", ctx)
        self.assertEqual(ctx.user_data["flow"]["data"]["step"], "mode")
        os.environ["FAKE_BOT_STATUS"] = "error"
        try:
            rec, _ = self._press("ag_amode~static", ctx)
        finally:
            os.environ.pop("FAKE_BOT_STATUS", None)
        shown = " ".join(str(c[1][0]) for c in rec.calls if c[0] in ("edit", "reply_text"))
        self.assertIn("❌", shown)
        self.assertEqual(ctx.user_data.get("flow", {}).get("kind"), "agent_add")
        data = ctx.user_data["flow"]["data"]
        self.assertEqual(data["step"], "mode")
        self.assertEqual(data["label"], "Агент для отказа")
        self.assertEqual(data["scope"], "read")
        self.assertEqual(data["days"], 90)
        # Кнопки режима нарисованы снова — иначе «состояние цело» ничего не даёт.
        last = [c for c in rec.calls if c[0] == "reply_text" and c[2].get("reply_markup")][-1]
        datas = [b.callback_data for row in last[2]["reply_markup"].inline_keyboard for b in row]
        self.assertIn("ag_amode~static", datas)
        # Повтор при живом мосте доводит выдачу до конца и снимает мастер.
        rec, _ = self._press("ag_amode~static", ctx)
        self.assertNotIn("flow", ctx.user_data)
        texts = " ".join(str(c[1][0]) for c in rec.calls if c[0] == "reply_text")
        self.assertIn("/mcp", texts)

    def test_text_flows_port_subnet_param_endpoint_dns(self):
        args_file = os.path.join(TMP_ROOT, "text_args.txt")
        os.environ["FAKE_BOT_ARGS_FILE"] = args_file
        try:
            rec, ctx = self._press("pr_port~awg2")
            self._say("70000", ctx)
            self.assertIn("flow", ctx.user_data)
            rec = self._say("39000", ctx)
            # Ввод номера больше не равен согласию: порт стоит в Endpoint каждого
            # выданного ключа, и SSH об этом спрашивает — теперь спрашивает и бот.
            self.assertNotIn("flow", ctx.user_data)
            datas = [b.callback_data for row in rec.calls[-1][2]["reply_markup"].inline_keyboard for b in row]
            self.assertIn("pr_port_do~awg2~39000", datas)
            self._press("pr_port_do~awg2~39000", ctx)
            rec, ctx = self._press("pr_subnet~awg3")
            self._say("8.8.8.0/24", ctx)
            self.assertIn("flow", ctx.user_data)
            self._say("10.8.7.0/24", ctx)
            self.assertEqual(ctx.user_data["flow"]["data"]["subnet"], "10.8.7.0/24")
            self._press("pr_subnet_ok", ctx)
            self.assertNotIn("flow", ctx.user_data)
            rec, ctx = self._press("pr_pkey~awg2~S1")           # серверный → подтверждение
            self._say("40", ctx)
            self.assertTrue(ctx.user_data["flow"]["data"].get("confirm"))
            self._press("pr_pok", ctx)
            self.assertNotIn("flow", ctx.user_data)
            rec, ctx = self._press("pr_pkey~awg2~Jc")           # клиентский → сразу
            self._say("7", ctx)
            self.assertNotIn("flow", ctx.user_data)
            rec, ctx = self._press("pr_pkey~awg2~I1")
            self._press("pr_punset", ctx)
            rec, ctx = self._press("se_endpoint")
            self._say("VPN.Example.COM", ctx)                   # приводится к строчным
            self.assertNotIn("flow", ctx.user_data)
            rec, ctx = self._press("se_dns_custom")
            self._say("1.1.1.1, 9.9.9.9", ctx)
            rec, ctx = self._press("u_ren~anna")
            self._say("Анна Петровна", ctx)
        finally:
            os.environ.pop("FAKE_BOT_ARGS_FILE", None)
        with open(args_file, encoding="utf-8") as fh:
            content = fh.read()
        for want in ("--action=profile-port\t--json\t--profile=awg2\t--port=39000",
                     "--action=profile-subnet\t--json\t--profile=awg3\t--subnet=10.8.7.0/24",
                     "--action=profile-set-param\t--json\t--profile=awg2\t--key=S1\t--value=40",
                     "--action=profile-set-param\t--json\t--profile=awg2\t--key=Jc\t--value=7",
                     "--action=profile-set-param\t--json\t--profile=awg2\t--key=I1\t--value=",
                     "--action=endpoint-set\t--json\t--host=vpn.example.com",
                     "--action=dns-set\t--json\t--dns1=1.1.1.1\t--dns2=9.9.9.9",
                     "--action=user-rename\t--json\t--name=anna\t--label=Анна Петровна"):
            self.assertIn(want, content, want)

    def test_commands(self):
        for cmd, args in ((bot.cmd_start, []), (bot.cmd_help, []), (bot.cmd_users, []), (bot.cmd_status, []),
                          (bot.cmd_lang, []), (bot.cmd_adduser, ["cmdbot", "5", "7"]), (bot.cmd_adduser, ["bad name"]),
                          (bot.cmd_key, ["anna", "awg2"]), (bot.cmd_key, ["anna"]), (bot.cmd_key, []),
                          (bot.cmd_invite, ["555"]), (bot.cmd_invite, ["x"])):
            upd = FakeUpdate(self.UID, text="/cmd")
            run(cmd(upd, FakeContext(args)))
            self.assertGreaterEqual(upd.rec.count("reply_text"), 1, cmd.__name__)
        bot.ALLOWED_IDS.discard(555)
        # чужой — отказ и ни одного вызова моста
        reset_stranger_limits()
        upd = FakeUpdate(999, text="/users")
        run(bot.cmd_users(upd, FakeContext()))
        self.assertEqual(upd.rec.count("reply_text"), 1)
        self.assertIn("999", str(upd.rec.calls[0][1][0]))

    def test_admin_onboarding(self):
        """Первый админ без терминала — только с одноразовым кодом с сервера и только в окне привязки:
        раньше им становился ЛЮБОЙ, кто первым написал /start в первые десять минут (оставшаяся часть
        аудита безопасности), а код годился в каждом окне после рестарта бота (SEC46-07)."""
        bot.ALLOWED_IDS.discard(self.UID)
        bot._WAITING_FOR_ADMIN = True
        marker = bot.ADMIN_CLAIMED_MARKER
        code_file = bot.ADMIN_CLAIM_CODE_FILE
        reset_stranger_limits()
        bot._CLAIM_VERIFIED.clear()
        bot._CLAIM_FAILS.clear()
        try:
            for path in pairing_files():
                if os.path.exists(path):
                    os.unlink(path)
            with open(code_file, "w", encoding="utf-8") as fh:
                fh.write("48291375\n")
            open_pairing_window()
            # /start без кода — кандидат для SSH и тот же ответ, что любому чужому вне окна (SEC50-01),
            # ни одной кнопки
            upd = FakeUpdate(self.UID, text="/start")
            run(bot.cmd_start(upd, FakeContext()))
            self.assertEqual(upd.rec.count("reply_text"), 1)
            self.assertIsNone(upd.rec.calls[0][2].get("reply_markup"))
            self.assertEqual(str(upd.rec.calls[0][1][0]), i18n.tf(self.UID, "stranger_private_bot_fmt", self.UID))
            self.assertEqual([c["id"] for c in bot.pairing_candidates()], [self.UID])
            # неверный код — отказ без кнопок
            upd = FakeUpdate(self.UID, text="/start 11112222")
            run(bot.cmd_start(upd, FakeContext(["11112222"])))
            self.assertIn(i18n.tf(self.UID, "admin_claim_bad_code", bot.esc(code_file)), str(upd.rec.calls[0][1][0]))
            self.assertIsNone(upd.rec.calls[0][2].get("reply_markup"))
            # старая кнопка «Да» без кода (от прежней версии бота) ничего не даёт
            upd = FakeUpdate(self.UID, data="admin_confirm_%d" % self.UID)
            run(bot.handle_callback(upd, FakeContext()))
            self.assertTrue(bot._WAITING_FOR_ADMIN)
            self.assertIn(i18n.t(self.UID, "admin_claim_code_first"), str(upd.rec.calls[0][1][0]))
            # верный код ссылкой t.me/<бот>?start=КОД — экран «Назначить вас?»
            upd = FakeUpdate(self.UID, text="/start 48291375")
            run(bot.cmd_start(upd, FakeContext(["48291375"])))
            self.assertEqual(upd.rec.count("reply_text"), 1)
            markup = upd.rec.calls[0][2]["reply_markup"]
            self.assertEqual(markup.inline_keyboard[0][0].callback_data, "admin_confirm_%d" % self.UID)
            # чужой нажимает кнопку — отказ
            upd = FakeUpdate(777, data="admin_confirm_%d" % self.UID)
            run(bot.handle_callback(upd, FakeContext()))
            self.assertTrue(bot._WAITING_FOR_ADMIN)
            # «Нет» — не тупик: под сообщением есть кнопка возврата к запросу
            upd = FakeUpdate(self.UID, data="admin_cancel")
            run(bot.handle_callback(upd, FakeContext()))
            edits = [c for c in upd.rec.calls if c[0] == "edit"]
            datas = [b.callback_data for row in edits[-1][2]["reply_markup"].inline_keyboard for b in row]
            self.assertEqual(datas, ["admin_again"])
            upd = FakeUpdate(self.UID, data="admin_again")
            run(bot.handle_callback(upd, FakeContext()))
            edits = [c for c in upd.rec.calls if c[0] == "edit"]
            datas = [b.callback_data for row in edits[-1][2]["reply_markup"].inline_keyboard for b in row]
            self.assertIn("admin_confirm_%d" % self.UID, datas)

            upd = FakeUpdate(self.UID, data="admin_confirm_%d" % self.UID)
            run(bot.handle_callback(upd, FakeContext()))
            self.assertFalse(bot._WAITING_FOR_ADMIN)
            self.assertTrue(bot.is_user_allowed(self.UID))
            self.assertTrue(os.path.exists(marker))
            self.assertFalse(bot.admin_claim_open())
            self.assertFalse(os.path.exists(code_file), "код больше ничего не открывает — файл убран")
            for path in (bot.ADMIN_PAIRING_UNTIL_FILE, bot.ADMIN_CANDIDATES_FILE):
                self.assertFalse(os.path.exists(path), "привязка состоялась — окно закрыто: %s" % path)
            # Второго /start не нужно: главный экран рисуется сразу и с кнопками
            replies = [c for c in upd.rec.calls if c[0] == "reply_text"]
            self.assertEqual(len(replies), 1, "главный экран после назначения не нарисован")
            datas = [b.callback_data for row in replies[0][2]["reply_markup"].inline_keyboard
                     for b in row if b.callback_data]
            self.assertIn("menu_users", datas)
            self.assertIn("sec_settings", datas)
        finally:
            bot.ALLOWED_IDS.add(self.UID)
            bot._WAITING_FOR_ADMIN = False
            bot._CLAIM_VERIFIED.clear()
            bot._CLAIM_FAILS.clear()
            for path in pairing_files():
                if os.path.exists(path):
                    os.unlink(path)

    def test_notify_admins_sends_to_each_admin(self):
        sent = []

        class FakeBot:
            async def send_message(self, chat_id, text, **kw):
                sent.append((chat_id, text))

        run(bot.notify_admins(FakeBot(), [{"event": "expired", "user": "bob", "expires": "2026-01-01T00:00:00Z"}]))
        self.assertEqual([s[0] for s in sent], sorted(bot.ALLOWED_IDS))
        self.assertIn("bob", sent[0][1])


# ════════════════════════════════════════════════════════════════════════════
# Временная ссылка на файл, режим и сайт, шаблоны, панель на домене, язык
# ════════════════════════════════════════════════════════════════════════════
SECRET_MARK = "FAKESECRETFAKESECRETFAKESECRETFAKE"


class _LogCatcher(logging.Handler):
    """Собрать всё, что бот написал в лог, — секрету там не место."""

    def __init__(self):
        logging.Handler.__init__(self)
        self.lines = []

    def emit(self, record):
        try:
            self.lines.append(record.getMessage())
        except Exception:  # noqa: BLE001 — тестовый обработчик молчит про свои беды
            pass

    def text(self):
        return "\n".join(self.lines)


class ShareLinkTests(_BotScreenCase):
    """Ссылка на файл: адрес отдельным сообщением, без превью и мимо лога."""

    def _create(self, fmt="conf"):
        catcher = _LogCatcher()
        bot.logger.addHandler(catcher)
        try:
            rec, ctx = self._press("u_shdo~anna~awg2~%s" % fmt)
        finally:
            bot.logger.removeHandler(catcher)
        return rec, ctx, catcher

    def test_user_card_has_share_button_only_with_peers(self):
        r, user = run(bot._fetch_user("anna"))
        datas = [b.callback_data for row in bot.user_card_keyboard(None, user).inline_keyboard for b in row]
        self.assertIn("u_share~anna", datas)
        # Без конфигураций раздавать нечего — кнопки нет
        datas = [b.callback_data for row in bot.user_card_keyboard(None, {"name": "empty", "peers": {}}).inline_keyboard
                 for b in row]
        self.assertNotIn("u_share~empty", datas)

    def test_single_profile_skips_the_profile_screen(self):
        # у bob один профиль: экран выбора профиля не показываем, сразу формат (bob включён — A3-5)
        os.environ["FAKE_BOT_BOB_ENABLED"] = "true"
        self.addCleanup(os.environ.pop, "FAKE_BOT_BOB_ENABLED", None)
        rec, _ = self._press("u_share~bob")
        edits = [c for c in rec.calls if c[0] == "edit"]
        datas = [b.callback_data for row in edits[-1][2]["reply_markup"].inline_keyboard for b in row]
        self.assertIn("u_shdo~bob~awg2~conf", datas)
        self.assertIn("u_shdo~bob~awg2~vpn", datas)
        # а у anna профиля два — сначала спрашиваем, из какого
        rec, _ = self._press("u_share~anna")
        edits = [c for c in rec.calls if c[0] == "edit"]
        datas = [b.callback_data for row in edits[-1][2]["reply_markup"].inline_keyboard for b in row]
        self.assertIn("u_shf~anna~awg2", datas)
        self.assertIn("u_shf~anna~awg3", datas)

    def test_link_goes_in_its_own_message_without_preview(self):
        rec, _, catcher = self._create()
        replies = [c for c in rec.calls if c[0] == "reply_text"]
        self.assertEqual(len(replies), 1, "адрес обязан ехать отдельным сообщением")
        text, kw = replies[0][1][0], replies[0][2]
        self.assertIn("<code>https://203.0.113.10:38472/", text)   # моноширинный адрес
        self.assertTrue(kw.get("disable_web_page_preview"), "превью сожгло бы одноразовую ссылку")
        self.assertIn(i18n.t(self.UID, "share_link_is_key"), text)
        self.assertIn("5", text)                                    # срок жизни, минуты
        # самоподписанный сертификат — предупреждение и отпечаток
        self.assertIn(i18n.t(self.UID, "share_selfsigned_warn"), text)
        self.assertIn("3A:9F:12", text)
        # у сообщения есть кнопка «Снять раздачу»
        datas = [b.callback_data for row in kw["reply_markup"].inline_keyboard for b in row]
        self.assertEqual(datas, ["sh_rev~s-4b1e"])
        # карточка при этом осталась и говорит, куда смотреть
        edits = [c for c in rec.calls if c[0] == "edit"]
        self.assertIn(i18n.t(self.UID, "share_ready"), str(edits[-1][1][0]))

    def test_secret_never_reaches_the_log(self):
        _, _, catcher = self._create()
        self.assertNotIn(SECRET_MARK, catcher.text())
        self.assertIn("s-4b1e", catcher.text(), "id раздачи в лог писать надо: без него не разобраться")

    def test_message_with_link_is_scheduled_for_deletion(self):
        """Сообщение с адресом бот удаляет сам, когда раздача протухла."""
        loop = asyncio.new_event_loop()
        try:
            upd = FakeUpdate(self.UID, data="u_shdo~anna~awg2~conf")
            ctx = FakeContext()
            loop.run_until_complete(bot.handle_callback(upd, ctx))
            tasks = [t for t in asyncio.all_tasks(loop) if not t.done()]
            self.assertEqual(len(tasks), 1, "задача на удаление сообщения не заведена")
            for t in tasks:
                t.cancel()
            loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
        finally:
            loop.close()

    def test_delete_later_removes_the_message(self):
        rec = _Recorder()
        msg = FakeMessage(rec)
        run(bot._share_delete_later(msg, 0.01, "s-4b1e"))
        self.assertEqual(rec.count("delete"), 1)

    def test_bad_format_is_refused_before_the_bridge(self):
        rec, _ = self._press("u_shdo~anna~awg2~qrconf")
        self.assertTrue(any(c[2].get("show_alert") for c in rec.calls if c[0] == "answer"))
        self.assertEqual(rec.count("reply_text"), 0)

    def test_list_revoke_and_revoke_all(self):
        args_file = os.path.join(TMP_ROOT, "share_args.txt")
        os.environ["FAKE_BOT_ARGS_FILE"] = args_file
        try:
            rec, ctx = self._press("sh_list")
            edits = [c for c in rec.calls if c[0] == "edit"]
            text = str(edits[-1][1][0])
            self.assertIn("s-4b1e", text)
            self.assertIn("anna-awg2.conf", text)
            self.assertNotIn(SECRET_MARK, text)        # секретной части пути в списке нет
            datas = [b.callback_data for row in edits[-1][2]["reply_markup"].inline_keyboard for b in row]
            self.assertIn("sh_rev~s-4b1e~l", datas)
            self.assertFalse([d for d in datas if d.startswith("sh_rev~s-99")])   # id не по формату отбрасываем
            self.assertIn("sh_revall", datas)
            # BOT-17: «Снять» на экране списка перерисовывает список, а не удаляет
            # сообщение-меню (адреса в нём нет — удалять там нечего)
            rec, _ = self._press("sh_rev~s-4b1e~l", ctx)
            self.assertEqual(rec.count("delete"), 0)
            edits = [c for c in rec.calls if c[0] == "edit"]
            self.assertIn(i18n.t(self.UID, "share_list_title"), str(edits[-1][1][0]))
            # кнопка под сообщением с адресом по-прежнему убирает само сообщение
            rec, _ = self._press("sh_rev~s-4b1e", ctx)
            self.assertEqual(rec.count("delete"), 1)
            rec, _ = self._press("sh_rev~s-4b1e~x", ctx)
            self.assertTrue(any(c[2].get("show_alert") for c in rec.calls if c[0] == "answer"))
            # «Снять все» спрашивает, как и в панели: уже отправленные ссылки
            # умирают все разом и восстановить их нечем.
            rec, _ = self._press("sh_revall", ctx)
            datas = [b.callback_data for row in rec.calls[-1][2]["reply_markup"].inline_keyboard for b in row]
            self.assertIn("sh_revall_do", datas)
            self._press("sh_revall_do", ctx)
            os.environ["FAKE_BOT_SHARES"] = "0"
            rec, _ = self._press("sh_list")
            edits = [c for c in rec.calls if c[0] == "edit"]
            self.assertIn(i18n.t(self.UID, "share_list_none"), str(edits[-1][1][0]))
        finally:
            os.environ.pop("FAKE_BOT_SHARES", None)
            os.environ.pop("FAKE_BOT_ARGS_FILE", None)
        with open(args_file, encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn("--action=share-revoke\t--json\t--id=s-4b1e", content)
        self.assertIn("--action=share-revoke-all", content)
        self.assertNotIn(SECRET_MARK, content)

    def test_bridge_gets_name_profile_and_format(self):
        args_file = os.path.join(TMP_ROOT, "share_create_args.txt")
        os.environ["FAKE_BOT_ARGS_FILE"] = args_file
        try:
            self._press("u_shdo~anna~awg3~vpn")
        finally:
            os.environ.pop("FAKE_BOT_ARGS_FILE", None)
        with open(args_file, encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn("--action=share-create\t--json\t--name=anna\t--profile=awg3\t--format=vpn", content)

    def test_share_limit_leads_straight_to_the_active_shares(self):
        """T19: ядро велит снять лишние раздачи в «Активных раздачах» — а они в «Обслуживании»,
        в другом разделе. На этот отказ бот ставит кнопку прямо туда, рядом с «Назад»."""
        orig = bot.run_bot_action
        self.addCleanup(setattr, bot, "run_bot_action", orig)
        words = "Одновременных раздач уже 5 из 5 — снимите лишние в списке «Активные раздачи»"

        async def patched(action, *a, **kw):
            if action == "share-create":
                return {"status": "error", "code": "share_limit", "message": words, "action": action}
            return await orig(action, *a, **kw)

        bot.run_bot_action = patched
        text, datas, _ = self._screen("u_shdo~anna~awg2~conf")
        self.assertIn(words, text)
        self.assertIn("share_limit", text)
        self.assertEqual(datas, ["sh_list", "u_view~anna"])
        self.assertIs(bot.resolve_handler("sh_list"), bot.cb_share_list)


class PagesTests(_BotScreenCase):
    """Страница подключения (docs/CLIENTS.md §10): срок кнопками, адрес отдельным
    сообщением с QR и без превью, мимо лога; в Lite — честный отказ и путь через
    ссылку на файл; список страниц, карточка, «ещё раз», отзыв с вопросом."""

    SECRET = "FAKESECRETFAKESECRETFAKESECRETFAKESECRET012"
    URL = "https://vpn.example.com/p-%s/" % SECRET

    def _mode(self, mode):
        os.environ["FAKE_BOT_MODE"] = mode
        self.addCleanup(os.environ.pop, "FAKE_BOT_MODE", None)
        bot.invalidate_status()

    @staticmethod
    def _sent(rec):
        """Сообщения с адресом: (текст, kwargs) — подпись к фото или текст."""
        out = []
        for c in rec.calls:
            if c[0] == "reply_photo":
                out.append((str(c[2].get("caption") or ""), c[2], c[1][0]))
            elif c[0] == "reply_text":
                out.append((str(c[1][0]), c[2], None))
        return out

    def _create(self, data="pg_ttl~anna~24h"):
        catcher = _LogCatcher()
        bot.logger.addHandler(catcher)
        try:
            rec, ctx = self._press(data)
        finally:
            bot.logger.removeHandler(catcher)
        return rec, ctx, catcher

    def test_card_has_page_button_with_any_profile(self):
        r, user = run(bot._fetch_user("anna"))
        datas = [b.callback_data for row in bot.user_card_keyboard(None, user).inline_keyboard for b in row]
        self.assertIn("u_page~anna", datas)
        datas = [b.callback_data for row in bot.user_card_keyboard(None, {"name": "empty", "peers": {}}).inline_keyboard
                 for b in row]
        self.assertNotIn("u_page~empty", datas)
        # UI-4: все ключи — из приложения Amnezia. Создать страницу нельзя, но уже созданные
        # (живые, с пояснением вместо ключа) видны и отзываются — вход тот же, из карточки.
        r, olga = run(bot._fetch_user("olga"))
        datas = [b.callback_data for row in bot.user_card_keyboard(None, olga).inline_keyboard for b in row]
        self.assertIn("u_page~olga", datas)

    def test_ttl_screen_offers_presets_custom_and_the_list(self):
        self._mode("pro")
        text, datas, _ = self._screen("u_page~anna")
        for ttl in bot.PAGE_TTL_PRESETS:
            self.assertIn("pg_ttl~anna~%s" % ttl, datas)
        self.assertIn("pg_ttl~anna~custom", datas)
        self.assertIn("pg_list~anna", datas)
        self.assertIn("u_view~anna", datas)
        self.assertIn(i18n.t(self.UID, "pg_intro"), text)

    def test_lite_says_the_page_needs_a_domain_and_offers_the_file_link(self):
        self._mode("lite")
        text, datas, _ = self._screen("u_page~anna")
        self.assertIn(i18n.t(self.UID, "pg_need_domain"), text)
        self.assertIn("u_share~anna", datas)
        self.assertNotIn("pg_ttl~anna~1h", datas)
        # lite_site: домен без настоящего сертификата — отказ приходит от ядра при создании
        self._mode("lite_site")
        rec, _ = self._press("pg_ttl~anna~24h")
        edits = [c for c in rec.calls if c[0] == "edit"]
        self.assertIn(i18n.t(self.UID, "pg_need_domain"), str(edits[-1][1][0]))
        datas = [b.callback_data for row in edits[-1][2]["reply_markup"].inline_keyboard for b in row]
        self.assertIn("u_share~anna", datas)
        self.assertEqual(self._sent(rec), [], "адреса нет — слать нечего")

    def test_address_goes_in_its_own_message_with_qr_and_without_preview(self):
        self._mode("pro")
        rec, _, catcher = self._create()
        sent = self._sent(rec)
        self.assertEqual(len(sent), 1, "адрес обязан ехать одним отдельным сообщением")
        text, kw, photo = sent[0]
        if shutil.which("qrencode"):
            self.assertEqual(photo, PNG_1x1[:8], "с qrencode адрес едет картинкой QR, подпись — текст сообщения")
        else:
            self.assertIsNone(photo)
            self.assertTrue(kw.get("disable_web_page_preview"), "превью засчитало бы открытие страницы")
        self.assertIn("<code>%s</code>" % self.URL, text)
        self.assertIn(i18n.t(self.UID, "pg_link_is_key"), text)
        self.assertNotIn(i18n.t(self.UID, "pg_link_forever_warn"), text)
        self.assertIn("2026-09-18 18:41:07 UTC", text)
        self.assertIn(FX31 + ", " + FX20, text)     # линиями, в порядке 3.1 → 2.0
        datas = [b.callback_data for row in kw["reply_markup"].inline_keyboard for b in row]
        self.assertEqual(datas, ["pg_rev~p-7f3a"])
        # экран остался и говорит, куда смотреть
        edits = [c for c in rec.calls if c[0] == "edit"]
        self.assertIn(i18n.t(self.UID, "pg_ready"), str(edits[-1][1][0]))
        # в лог — только id
        self.assertNotIn(self.SECRET, catcher.text())
        self.assertIn("p-7f3a", catcher.text())

    def test_status_comes_before_the_address(self):
        """«Адрес следующим сообщением» стоит ВЫШЕ адреса и по кнопке, и в ответе на текст."""
        self._mode("pro")
        rec, ctx = self._press("pg_ttl~anna~custom")
        rec = self._say("12h", ctx)
        kinds = [c[0] for c in rec.calls if c[0] in ("reply_text", "reply_photo")]
        self.assertEqual(kinds[0], "reply_text")
        first_reply = next(c for c in rec.calls if c[0] == "reply_text")
        self.assertIn(i18n.t(self.UID, "pg_ready"), str(first_reply[1][0]))
        self.assertEqual(len(kinds), 2, kinds)
        rec, _, _ = self._create()
        names = [c[0] for c in rec.calls]
        self.assertLess(names.index("edit"), max(i for i, n in enumerate(names) if n in ("reply_photo", "reply_text")))

    def test_send_failure_offers_the_address_again_not_success(self):
        """Адрес не ушёл: страница уже есть, второй раз не создаём — «ещё раз» (page-url) и «Назад», без ✅."""
        self._mode("pro")
        real = bot.send_page_link

        async def broken(message, user_id, r):
            return False
        bot.send_page_link = broken
        try:
            rec, ctx = self._press("pg_ttl~anna~24h")
            edits = [c for c in rec.calls if c[0] == "edit"]
            text = str(edits[-1][1][0])
            datas = [b.callback_data for row in edits[-1][2]["reply_markup"].inline_keyboard for b in row]
            self.assertIn(i18n.t(self.UID, "pg_send_failed"), text)
            self.assertNotIn(i18n.t(self.UID, "pg_ready"), text)
            self.assertEqual(datas, ["pg_url~p-7f3a", "u_view~anna"])
            # ответ на текст: статус ушёл сообщением и потом заменён итогом
            rec, ctx = self._press("pg_ttl~anna~custom")
            rec = self._say("3d", ctx)
            self.assertEqual(rec.calls[-1][0], "edit_text")
            self.assertIn(i18n.t(self.UID, "pg_send_failed"), str(rec.calls[-1][1][0]))
            datas = [b.callback_data for row in rec.calls[-1][2]["reply_markup"].inline_keyboard for b in row]
            self.assertIn("pg_url~p-7f3a", datas)
            # «ещё раз» из карточки — тот же итог, назад в карточку страницы
            rec, _ = self._press("pg_url~p-7f3a")
            edits = [c for c in rec.calls if c[0] == "edit"]
            self.assertIn(i18n.t(self.UID, "pg_send_failed"), str(edits[-1][1][0]))
            datas = [b.callback_data for row in edits[-1][2]["reply_markup"].inline_keyboard for b in row]
            self.assertEqual(datas, ["pg_url~p-7f3a", "pg_card~p-7f3a"])
        finally:
            bot.send_page_link = real

    def test_disabled_user_pages_stay_reachable_but_no_new_one_is_offered(self):
        """UI-4 (как в панели): страница выключенного человека живёт («доступ приостановлен»,
        после включения — снова ключи), а из карточки её было не увидеть и не отозвать."""
        r, user = run(bot._fetch_user("bob"))          # bob выключен в заглушке
        datas = [b.callback_data for row in bot.user_card_keyboard(None, user).inline_keyboard for b in row]
        self.assertIn("u_page~bob", datas)
        self.assertIn("u_share~bob", datas)
        self._mode("pro")
        text, datas, _ = self._screen("u_page~bob")
        self.assertIn(i18n.t(self.UID, "pg_user_disabled"), text)
        self.assertIn("pg_list~bob", datas, "созданные раньше страницы — список и отзыв")
        self.assertIn("u_view~bob", datas)
        self.assertFalse([d for d in datas if d.startswith("pg_ttl~")], "новую выключенному не создать")
        # Старая кнопка из прошлого экрана: ядро отвечает user_disabled — своими словами, без глагола CLI
        text, datas, _ = self._screen("pg_ttl~bob~24h")
        self.assertIn(i18n.t(self.UID, "pg_user_disabled"), text)
        self.assertNotIn("user-enable", text)
        self.assertIn("u_view~bob", datas)

    def test_forever_warns_louder(self):
        self._mode("pro")
        rec, _, _ = self._create("pg_ttl~anna~forever")
        text = self._sent(rec)[0][0]
        self.assertIn(i18n.t(self.UID, "pg_link_forever_warn"), text)
        self.assertIn(i18n.t(self.UID, "pg_link_forever"), text)
        self.assertNotIn(i18n.t(self.UID, "pg_link_is_key"), text)

    def test_custom_ttl_is_checked_before_the_bridge(self):
        self._mode("pro")
        args_file = os.path.join(TMP_ROOT, "page_args.txt")
        os.environ["FAKE_BOT_ARGS_FILE"] = args_file
        try:
            rec, ctx = self._press("pg_ttl~anna~custom")
            edits = [c for c in rec.calls if c[0] == "edit"]
            self.assertIn(i18n.t(self.UID, "pg_ask_custom"), str(edits[-1][1][0]))
            self.assertIsNotNone(bot.flow_get(ctx))
            # «12 h» с пробелом теперь срок (NC-23: по-русски «12 ч» пишут через пробел — и так, и так)
            for bad in ("0h", "1000h", "400d", "12", "3w", "forever!", "12 hh", "0 ч"):
                rec = self._say(bad, ctx)
                self.assertIn(i18n.t(self.UID, "pg_bad_ttl"), str(rec.calls[-1][1][0]), bad)
            rec = self._say("12H", ctx)   # регистр не важен, мост получает строчную
            sent = self._sent(rec)
            self.assertTrue(sent)
            # статус идёт первым, адрес — следующим сообщением
            self.assertTrue(any(self.URL in text for text, _, _ in sent), sent)
        finally:
            os.environ.pop("FAKE_BOT_ARGS_FILE", None)
        with open(args_file, encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn("--action=page-create\t--json\t--name=anna\t--ttl=12h", content)
        self.assertEqual(content.count("--action=page-create"), 1, "негодный срок не должен доходить до моста")
        self.assertIsNone(bot.flow_get(ctx))

    def test_ttl_validator_matches_the_contract(self):
        for good in ("1h", "720h", "1d", "365d", "forever", "12h", "3d"):
            self.assertTrue(bot.valid_page_ttl(good), good)
        for bad in ("0h", "721h", "366d", "1000h", "h", "1", "forever ", "1H", ""):
            self.assertFalse(bot.valid_page_ttl(bad), bad)

    def test_custom_ttl_in_russian_words(self):
        """NC-23 (критик-новичок на 0.5.0): по-русски срок — «12 ч», «3 дн», «навсегда», а не «12h / 3d /
        forever»; английская запись понимается по-прежнему, мосту уходит всегда запись договора."""
        for text, want in (("12 ч", "12h"), ("12ч", "12h"), ("12 часов", "12h"), ("1 час", "1h"), ("720 ч", "720h"),
                           ("3 дн", "3d"), ("3 дн.", "3d"), ("3 дня", "3d"), ("21 день", "21d"), ("365 дней", "365d"),
                           ("навсегда", "forever"), ("Бессрочно", "forever"),
                           ("12h", "12h"), ("12 H", "12h"), ("3d", "3d"), ("2 days", "2d"), ("forever", "forever")):
            self.assertEqual(bot.parse_page_ttl(text), want, text)
            self.assertTrue(bot.valid_page_ttl(want), want)
        for text in ("0 ч", "721 ч", "366 дн", "12", "3 нед", "навсегда!", "ч", "12 чч", "1000h", ""):
            self.assertEqual(bot.parse_page_ttl(text), "", text)
        ru, en = (i18n._load_lang_file(lang) for lang in ("ru", "en"))
        for key in ("pg_ask_custom", "pg_bad_ttl"):
            for word in ("12h", "3d", "forever"):
                self.assertNotIn(word, ru[key], "ru/%s: %s" % (key, word))
                self.assertIn(word, en[key], "en/%s — как было: %s" % (key, word))
            for word in ("12 ч", "3 дн", "навсегда"):
                self.assertIn(word, ru[key], "ru/%s: %s" % (key, word))
        # сквозь экран: «3 дн» русским словом — мосту уходит 3d
        self._mode("pro")
        args_file = os.path.join(TMP_ROOT, "page_ru_ttl_args.txt")
        open(args_file, "w").close()
        os.environ["FAKE_BOT_ARGS_FILE"] = args_file
        self.addCleanup(os.environ.pop, "FAKE_BOT_ARGS_FILE", None)
        _, ctx = self._press("pg_ttl~anna~custom")
        self._say("3 дн", ctx)
        self.assertIsNone(bot.flow_get(ctx))
        with open(args_file, encoding="utf-8") as fh:
            self.assertIn("--action=page-create\t--json\t--name=anna\t--ttl=3d", fh.read())

    def test_revoke_page_with_subscription(self):
        """B2-2/A2-4 (CRITICS-FIX-2): страница отдавала и адрес подписки — у отзыва две кнопки, первой «и перевыпустить
        подписку», если подписку завела эта страница; мосту уходит --sub=on. Без подписки — прежний вопрос."""
        self._mode("pro")
        args_file = os.path.join(TMP_ROOT, "page_revoke_sub_args.txt")
        open(args_file, "w").close()
        os.environ["FAKE_BOT_ARGS_FILE"] = args_file
        self.addCleanup(os.environ.pop, "FAKE_BOT_ARGS_FILE", None)
        rec, ctx = self._press("pg_url~p-7f3a")
        sent = self._sent(rec)
        self.assertIn(i18n.t(self.UID, "pg_link_sub_created"), sent[0][0])
        text, datas, ctx = self._screen("pg_rev~p-7f3a", ctx)
        self.assertEqual(datas[:2], ["pg_rev_dos~p-7f3a", "pg_rev_do~p-7f3a"])
        self.assertIn(i18n.t(self.UID, "pg_revoke_sub_note"), text)
        text, datas, _ = self._screen("pg_rev_dos~p-7f3a", ctx)
        self.assertIn(i18n.tf(self.UID, "pg_revoked_fmt", "p-7f3a"), text)
        self.assertIn(i18n.t(self.UID, "pg_revoked_sub"), text)
        with open(args_file, encoding="utf-8") as fh:
            self.assertIn("--action=page-revoke\t--json\t--id=p-7f3a\t--sub=on", fh.read())
        # R-1: выбор — по подписке СЕЙЧАС (sub-list), а не по снимку при создании. p-9c1d — старая страница без поля
        # subscription, а подписка у anna есть: выбор есть, «только страницу» — первой (подписку завела не она).
        text, datas, _ = self._screen("pg_rev~p-9c1d")
        self.assertEqual(datas[:2], ["pg_rev_do~p-9c1d", "pg_rev_dos~p-9c1d"])
        self.assertIn(i18n.t(self.UID, "pg_revoke_sub_note"), text)
        # У bob подписки нет — прежний вопрос
        text, datas, _ = self._screen("pg_rev~p-0bad")
        self.assertIn("pg_rev_do~p-0bad", datas)
        self.assertNotIn("pg_rev_dos~p-0bad", datas)
        self.assertNotIn(i18n.t(self.UID, "pg_revoke_sub_note"), text)
        # Подписку отозвали отдельно, а в записи страницы осталось created — выбора нет
        os.environ["FAKE_BOT_SUBS"] = "empty"
        self.addCleanup(os.environ.pop, "FAKE_BOT_SUBS", None)
        text, datas, _ = self._screen("pg_rev~p-7f3a")
        self.assertIn("pg_rev_do~p-7f3a", datas)
        self.assertNotIn("pg_rev_dos~p-7f3a", datas)
        self.assertNotIn(i18n.t(self.UID, "pg_revoke_sub_note"), text)
        # R-4: отзыв с подпиской, когда её уже нет (старое окно) — «перевыпускать нечего», не провал
        text, datas, _ = self._screen("pg_rev_dos~p-7f3a")
        self.assertIn(i18n.tf(self.UID, "pg_revoked_fmt", "p-7f3a"), text)
        self.assertIn(i18n.t(self.UID, "pg_revoked_sub_none"), text)
        self.assertNotIn(i18n.t(self.UID, "pg_revoked_sub_failed"), text)

    def test_revoke_page_offers_key_reissue_when_opened(self):
        """B3-3: страницу открывали — экран называет число открытий и честно говорит про ключи; последняя кнопка — с
        перевыпуском ключей (--keys=on, при подписке — и --sub=on); итог называет профили. Не открывали — кнопки нет."""
        self._mode("pro")
        args_file = os.path.join(TMP_ROOT, "page_revoke_keys_args.txt")
        open(args_file, "w").close()
        os.environ["FAKE_BOT_ARGS_FILE"] = args_file
        self.addCleanup(os.environ.pop, "FAKE_BOT_ARGS_FILE", None)
        text, datas, ctx = self._screen("pg_rev~p-7f3a")
        self.assertIn(i18n.tf(self.UID, "pg_revoke_keys_note_fmt", 3), text)
        self.assertEqual(datas[2], "pg_rev_dosk~p-7f3a")
        text, datas, _ = self._screen("pg_rev_dosk~p-7f3a", ctx)
        self.assertIn(i18n.t(self.UID, "pg_revoked_sub"), text)
        self.assertIn("♻️", text)
        self.assertIn("AmneziaWG", text)
        with open(args_file, encoding="utf-8") as fh:
            self.assertIn("--action=page-revoke\t--json\t--id=p-7f3a\t--sub=on\t--keys=on", fh.read())
        # bob без подписки, страницу открывали — «отозвать и перевыпустить ключи» без подписки
        text, datas, _ = self._screen("pg_rev~p-0bad")
        self.assertIn("pg_rev_dok~p-0bad", datas)
        text, _, _ = self._screen("pg_rev_dok~p-0bad")
        with open(args_file, encoding="utf-8") as fh:
            self.assertIn("--id=p-0bad\t--sub=off\t--keys=on", fh.read())
        # не открывали — ни строки, ни кнопки
        text, datas, _ = self._screen("pg_rev~p-9c1d")
        self.assertNotIn("pg_rev_dosk~p-9c1d", datas)
        self.assertNotIn("pg_rev_dok~p-9c1d", datas)
        self.assertNotIn(i18n.tf(self.UID, "pg_revoke_keys_note_fmt", 0), text)

    def test_revoke_page_names_key_profiles_of_page_and_subscription(self):
        """Н-1, Н-3: экран отзыва называет профили, чьи ключи перевыпустятся: страницы, а вместе с подпиской — и её
        линий; при подписке есть и «только страница и ключи» (--sub=off --keys=on)."""
        self._mode("pro")
        args_file = os.path.join(TMP_ROOT, "page_revoke_keys_n1_args.txt")
        open(args_file, "w").close()
        os.environ["FAKE_BOT_ARGS_FILE"] = args_file
        self.addCleanup(os.environ.pop, "FAKE_BOT_ARGS_FILE", None)
        # p-7f3a: профили страницы awg2 и awg3 — подписке добавить нечего
        rec, ctx = self._press("pg_rev~p-7f3a")
        edit = [c for c in rec.calls if c[0] == "edit"][-1]
        text = str(edit[1][0])
        rows = edit[2]["reply_markup"].inline_keyboard
        datas = [b.callback_data for row in rows for b in row]
        self.assertIn(i18n.tf(self.UID, "pg_revoke_keys_page_fmt", bot.profile_names(self.UID, ["awg2", "awg3"])), text)
        self.assertNotIn(i18n.tf(self.UID, "pg_revoke_keys_sub_fmt", "").split(":")[0], text)
        self.assertEqual(datas[2:4], ["pg_rev_dosk~p-7f3a", "pg_rev_dok~p-7f3a"])
        self.assertEqual(rows[3][0].text, i18n.t(self.UID, "pg_revoke_page_keys_btn"))
        self._screen("pg_rev_dok~p-7f3a", ctx)
        with open(args_file, encoding="utf-8") as fh:
            self.assertIn("--id=p-7f3a\t--sub=off\t--keys=on", fh.read())
        # p-9c1d: страница только на awg2, подписка отдаёт и awg3 — экран называет его отдельно
        os.environ["FAKE_BOT_P9_VISITS"] = "2"
        self.addCleanup(os.environ.pop, "FAKE_BOT_P9_VISITS", None)
        text, datas, _ = self._screen("pg_rev~p-9c1d")
        self.assertIn(i18n.tf(self.UID, "pg_revoke_keys_page_fmt", bot.profile_names(self.UID, ["awg2"])), text)
        self.assertIn(i18n.tf(self.UID, "pg_revoke_keys_sub_fmt", bot.profile_names(self.UID, ["awg3"])), text)
        self.assertIn("pg_rev_dosk~p-9c1d", datas)
        self.assertIn("pg_rev_dok~p-9c1d", datas)
        # без подписки — профилей подписки нет, кнопка прежняя
        os.environ["FAKE_BOT_SUBS"] = "empty"
        self.addCleanup(os.environ.pop, "FAKE_BOT_SUBS", None)
        text, datas, _ = self._screen("pg_rev~p-9c1d")
        self.assertNotIn(i18n.tf(self.UID, "pg_revoke_keys_sub_fmt", bot.profile_names(self.UID, ["awg3"])), text)
        self.assertNotIn("pg_rev_dosk~p-9c1d", datas)
        self.assertIn("pg_rev_dok~p-9c1d", datas)

    def test_revoke_page_keys_when_subscription_lines_unknown(self):
        """П-3: у профилей страницы ключа на сервере нет (olga, ключ из приложения), подписка есть по снимку, а sub-list
        не ответил — кнопка «с подпиской и ключами» есть, текст говорит условно и не называет «профили страницы»;
        «только страница и ключи» нет — перевыпускать там нечего."""
        self._mode("pro")
        os.environ["FAKE_BOT_SUBS"] = "fail"
        os.environ["FAKE_BOT_OLGA_PAGE_SUB"] = "exists"
        self.addCleanup(os.environ.pop, "FAKE_BOT_SUBS", None)
        self.addCleanup(os.environ.pop, "FAKE_BOT_OLGA_PAGE_SUB", None)
        text, datas, _ = self._screen("pg_rev~p-0a1b")
        self.assertIn(i18n.t(self.UID, "pg_revoke_sub_note"), text)
        self.assertIn(i18n.tf(self.UID, "pg_revoke_keys_note_fmt", 2), text)
        self.assertNotIn("профилях страницы", text)
        self.assertIn(i18n.t(self.UID, "pg_revoke_keys_sub_unknown"), text)
        self.assertIn("pg_rev_dosk~p-0a1b", datas)
        self.assertNotIn("pg_rev_dok~p-0a1b", datas)
        # подписки нет вовсе (sub-list ответил пусто) — ни строки, ни кнопок с ключами
        os.environ["FAKE_BOT_SUBS"] = "empty"
        text, datas, _ = self._screen("pg_rev~p-0a1b")
        self.assertNotIn(i18n.tf(self.UID, "pg_revoke_keys_note_fmt", 2), text)
        self.assertNotIn("pg_rev_dosk~p-0a1b", datas)
        self.assertNotIn("pg_rev_dok~p-0a1b", datas)

    def test_page_revoke_key_profiles_rule(self):
        """Н-1: правило ядра ga_pages_revoke_key_profiles — ключ на сервере, без повторов, подписка — сверх страницы."""
        user = {"peers": {"awg2": {"has_key": False}, "awg3": {"has_key": True}, "awg1": {}}}
        lines = [{"profile": "awg3", "has_key": True}, {"profile": "awg2", "has_key": False}, {"profile": "awg1", "has_key": True}]
        self.assertEqual(bot.page_revoke_key_profiles(user, ["awg2", "awg3", "awg3"], lines), (["awg3"], ["awg1"]))
        self.assertEqual(bot.page_revoke_key_profiles(user, ["awg2"], lines), ([], ["awg3", "awg1"]))
        self.assertEqual(bot.page_revoke_key_profiles(None, ["awg2"], None), (["awg2"], []))

    def test_list_card_url_again_and_revoke(self):
        self._mode("pro")
        args_file = os.path.join(TMP_ROOT, "page_list_args.txt")
        os.environ["FAKE_BOT_ARGS_FILE"] = args_file
        try:
            text, datas, ctx = self._screen("pg_list~anna")
            self.assertIn("p-7f3a", text)
            self.assertIn("p-9c1d", text)
            self.assertNotIn("p-0bad", text, "чужие страницы в списке пользователя не показываем")
            self.assertNotIn(self.SECRET, text)
            self.assertIn(i18n.t(self.UID, "never"), text)          # бессрочная
            self.assertIn("pg_card~p-7f3a", datas)
            self.assertIn("u_page~anna", datas)
            self.assertIn("u_view~anna", datas)
            text, datas, _ = self._screen("pg_card~p-7f3a", ctx)
            self.assertIn(i18n.tf(self.UID, "pg_card_visits_fmt", 3, "2026-09-17 19:02:11 UTC"), text)
            self.assertIn(FX31 + ", " + FX20, text)
            self.assertNotIn(self.SECRET, text)
            for want in ("pg_url~p-7f3a", "pg_rev~p-7f3a", "pg_list~anna"):
                self.assertIn(want, datas)
            # адрес ещё раз — отдельным сообщением, экран говорит куда смотреть
            rec, _ = self._press("pg_url~p-7f3a", ctx)
            sent = self._sent(rec)
            self.assertEqual(len(sent), 1)
            self.assertIn("<code>%s</code>" % self.URL, sent[0][0])
            self.assertIn("2026-09-18 18:41:07 UTC", sent[0][0])
            # отзыв — с вопросом, потом назад к страницам того же человека
            text, datas, _ = self._screen("pg_rev~p-7f3a", ctx)
            self.assertIn("pg_rev_do~p-7f3a", datas)
            text, datas, _ = self._screen("pg_rev_do~p-7f3a", ctx)
            self.assertIn(i18n.tf(self.UID, "pg_revoked_fmt", "p-7f3a"), text)
            self.assertIn("pg_list~anna", datas)
            # неизвестная страница — словами, id не по форме каталога — алерт до моста
            text, datas, _ = self._screen("pg_card~p-dead")
            self.assertIn(i18n.tf(self.UID, "pg_not_found_fmt", "p-dead"), text)
            for junk in ("pg_card~BAD id!", "pg_card~p-nope", "pg_url~p-zz", "pg_rev~../x"):
                rec, _ = self._press(junk)
                self.assertTrue(any(c[2].get("show_alert") for c in rec.calls if c[0] == "answer"), junk)
            # запись с мусорным id из ответа моста в кнопки не попадает
            text, datas, _ = self._screen("pg_list~bob")
            self.assertIn("pg_card~p-0bad", datas)
            self.assertFalse(any("BAD" in d for d in datas), datas)
            os.environ["FAKE_BOT_PAGES"] = "empty"
            text, datas, _ = self._screen("pg_list~anna")
            self.assertIn(i18n.t(self.UID, "pg_list_none"), text)
        finally:
            os.environ.pop("FAKE_BOT_ARGS_FILE", None)
            os.environ.pop("FAKE_BOT_PAGES", None)
        with open(args_file, encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn("--action=page-list\t--json\t--name=anna", content)
        self.assertIn("--action=page-url\t--json\t--id=p-7f3a", content)
        self.assertIn("--action=page-revoke\t--json\t--id=p-7f3a", content)
        self.assertNotIn("BAD", content)
        self.assertNotIn("p-nope", content)
        self.assertNotIn("p-zz", content)


SUB_SECRET = "FAKESUBSECRETFAKESUBSECRETFAKESUBSECRET0123"
SUB_ROTATED = "ROTATEDSECRETROTATEDSECRETROTATEDSECRET0123"


class SubsTextTests(unittest.TestCase):
    """Подписка (docs/SUBSCRIPTION.md) без python-telegram-bot: тексты экрана и сообщения с адресами, фразы."""

    def answer(self, **extra):
        base = "https://vpn.example.com/s-%s/" % SUB_SECRET
        r = {"name": "anna", "label": "Анна", "base_url": base, "update_interval": 12, "disabled": False,
             "urls": [{"suffix": s, "format": f, "line": l, "url": base + s} for s, f, l in (
                 ("mihomo.yaml", "mihomo", ""), ("awg31.conf", "conf", "3.1"), ("awg20.conf", "conf", "2.0"),
                 ("awg31.vpn", "vpn", "3.1"), ("awg20.vpn", "vpn", "2.0"), ("all.vpn", "vpn", ""))]}
        r.update(extra)
        return r

    def test_links_message_names_each_format_for_its_client(self):
        text = bot.sub_links_text(None, self.answer())
        for suffix in ("mihomo.yaml", "awg31.conf", "awg20.conf", "awg31.vpn", "awg20.vpn", "all.vpn"):
            self.assertIn("<code>https://vpn.example.com/s-%s/%s</code>" % (SUB_SECRET, suffix), text)
        self.assertLess(text.index(i18n.t(None, "sub_for_mihomo")), text.index("mihomo.yaml"))
        self.assertLess(text.index(i18n.t(None, "sub_for_conf")), text.index("awg20.conf"))
        self.assertIn("AmneziaWG 2.0: <code>", text)
        self.assertIn(i18n.t(None, "sub_all_lines") + ": <code>", text)
        self.assertIn(i18n.tf(None, "sub_update_fmt", 12), text)
        self.assertIn(i18n.t(None, "sub_is_key"), text)
        self.assertNotIn(i18n.t(None, "sub_user_off"), text)
        self.assertIn(i18n.t(None, "sub_user_off"), bot.sub_links_text(None, self.answer(disabled=True)))
        # Пауза без домена и подписка без ключей — ни одного адреса, только правда словами
        paused = bot.sub_links_text(None, self.answer(urls=[], paused=True))
        self.assertIn(i18n.t(None, "sub_paused"), paused)
        self.assertNotIn("https://", paused)
        self.assertIn(i18n.t(None, "sub_no_keys"), bot.sub_links_text(None, self.answer(urls=[])))
        # Только https и только записи-словари: мусор ответа в сообщение не попадает
        junk = self.answer(urls=[{"format": "conf", "line": "2.0", "url": "javascript:x"}, "x"])
        self.assertNotIn("javascript", bot.sub_links_text(None, junk))

    def test_state_screen(self):
        _prime_lines()
        r = {"domain_ready": True}
        self.assertIn(i18n.t(None, "sub_none"), bot.sub_state_text(None, "anna", r, None))
        self.assertNotIn(i18n.t(None, "sub_need_domain"), bot.sub_state_text(None, "anna", r, None))
        self.assertIn(i18n.t(None, "sub_need_domain"), bot.sub_state_text(None, "anna", {"domain_ready": False}, None))
        rec = {"name": "anna", "created": "2026-10-03T10:00:00Z", "rotated": "", "fetches": 3,
               "last_fetch": "2026-10-03T12:00:00Z", "last_client": "clash-verge", "disabled": True,
               "lines": [{"line": "3.1", "profile": "awg3", "has_key": True},
                         {"line": "2.0", "profile": "awg2", "has_key": False}]}
        text = bot.sub_state_text(None, "anna", {"domain_ready": False}, rec)
        self.assertIn("2026-10-03 10:00:00 UTC", text)
        self.assertIn(i18n.tf(None, "sub_fetches_fmt", 3, "2026-10-03 12:00:00 UTC (clash-verge)"), text)
        self.assertIn(i18n.tf(None, "sub_lines_fmt", FX31), text)
        # Профиль называется людям подписью, а не голым id (DUP-LINE-01)
        self.assertIn(i18n.tf(None, "sub_keyless_fmt", FX20), text)
        self.assertIn(i18n.t(None, "sub_user_off"), text)
        self.assertIn(i18n.t(None, "sub_paused"), text)
        self.assertNotIn("https://", text)
        self.assertNotIn(i18n.tf(None, "sub_prev_fetches_n_fmt", "").rstrip(": ."), text)
        # Н-1: после перевыпуска — след прежнего адреса
        rec2 = dict(rec, rotated="2026-10-17T08:00:00Z", fetches=0, last_fetch="", prev_fetches=5,
                    prev_last_fetch="2026-10-16T21:04:00Z")
        text = bot.sub_state_text(None, "anna", {"domain_ready": True}, rec2)
        self.assertIn(i18n.t(None, "sub_never"), text)
        self.assertIn(i18n.tf(None, "sub_prev_fetches_fmt", 5, "2026-10-16 21:04:00 UTC"), text)
        text = bot.sub_state_text(None, "anna", {"domain_ready": True}, dict(rec2, prev_last_fetch=""))
        self.assertIn(i18n.tf(None, "sub_prev_fetches_n_fmt", 5), text)
        rec.update({"fetches": 0, "last_fetch": ""})
        self.assertIn(i18n.t(None, "sub_never"), bot.sub_state_text(None, "anna", {"domain_ready": True}, rec))

    def test_phrases_and_readonly_actions(self):
        self.assertEqual(bot.confirm_phrase("sub-rotate", "anna"), "ROTATE anna")
        self.assertEqual(bot.confirm_phrase("sub-revoke", "anna"), "REVOKE anna")
        self.assertTrue({"sub-list", "sub-show"} <= bot.READONLY_ACTIONS)
        self.assertFalse({"sub-create", "sub-rotate", "sub-revoke"} & bot.READONLY_ACTIONS)


class SubsTests(_BotScreenCase):
    """Подписка в карточке: экран без адреса, создание — адреса отдельным сообщением без превью и мимо лога,
    «Адреса» ещё раз, перевыпуск и отзыв — только фразой; в Lite — честный отказ."""

    def _mode(self, mode):
        os.environ["FAKE_BOT_MODE"] = mode
        self.addCleanup(os.environ.pop, "FAKE_BOT_MODE", None)
        bot.invalidate_status()

    def test_card_has_the_subscription_button(self):
        r, user = run(bot._fetch_user("anna"))
        datas = [b.callback_data for row in bot.user_card_keyboard(None, user).inline_keyboard for b in row]
        self.assertIn("u_sub~anna", datas)
        self.assertLess(datas.index("u_page~anna"), datas.index("u_sub~anna"))
        self.assertLess(datas.index("u_sub~anna"), datas.index("u_del~anna"))
        datas = [b.callback_data for row in bot.user_card_keyboard(None, {"name": "empty", "peers": {}}).inline_keyboard
                 for b in row]
        self.assertNotIn("u_sub~empty", datas)

    def test_screen_with_and_without_a_subscription(self):
        self._mode("pro")
        text, datas, _ = self._screen("u_sub~anna")
        self.assertIn(i18n.tf(self.UID, "sub_fetches_fmt", 3, "2026-10-03 12:00:00 UTC (clash-verge)"), text)
        self.assertEqual(datas, ["sub_show~anna", "sub_rot~anna", "sub_rev~anna", "u_sub~anna", "u_view~anna"])
        self.assertNotIn(SUB_SECRET, text)
        text, datas, _ = self._screen("u_sub~bob")
        self.assertIn(i18n.t(self.UID, "sub_none"), text)
        self.assertEqual(datas, ["sub_new~bob", "u_sub~bob", "u_view~bob"])
        self._mode("lite")
        text, datas, _ = self._screen("u_sub~bob")
        self.assertIn(i18n.t(self.UID, "sub_need_domain"), text)
        self.assertNotIn("sub_new~bob", datas)

    def test_create_sends_addresses_separately_without_preview_and_not_to_the_log(self):
        self._mode("pro")
        catcher = _LogCatcher()
        bot.logger.addHandler(catcher)
        try:
            rec, _ = self._press("sub_new~bob")
        finally:
            bot.logger.removeHandler(catcher)
        texts = [c for c in rec.calls if c[0] == "reply_text"]
        self.assertEqual(len(texts), 1, "адреса — одним отдельным сообщением")
        self.assertIn("<code>https://vpn.example.com/s-%s/mihomo.yaml</code>" % SUB_SECRET, str(texts[0][1][0]))
        self.assertTrue(texts[0][2].get("disable_web_page_preview"), "превью унесло бы ключи на сервер Telegram")
        edits = [c for c in rec.calls if c[0] == "edit"]
        self.assertIn(i18n.t(self.UID, "sub_ready"), str(edits[-1][1][0]))
        self.assertNotIn(SUB_SECRET, catcher.text())
        self.assertIn("bob", catcher.text())

    def test_show_again_and_lite_refusal(self):
        self._mode("pro")
        rec, _ = self._press("sub_show~anna")
        texts = " ".join(str(c[1][0]) for c in rec.calls if c[0] == "reply_text")
        self.assertIn("/s-%s/awg20.conf" % SUB_SECRET, texts)
        self._mode("lite")
        text, datas, _ = self._screen("sub_show~anna")
        self.assertIn(i18n.t(self.UID, "sub_paused"), text)
        self.assertNotIn(SUB_SECRET, text)

    def test_rotate_and_revoke_only_by_the_typed_phrase(self):
        self._mode("pro")
        args_file = os.path.join(TMP_ROOT, "sub_args.txt")
        os.environ["FAKE_BOT_ARGS_FILE"] = args_file
        try:
            _, ctx = self._press("sub_rot~anna")
            self.assertEqual(bot.CONFIRM.pending(self.UID)["phrase"], "ROTATE anna")
            self._say("rotate anna", ctx)                        # не совпало — ждём
            self.assertIsNotNone(bot.CONFIRM.pending(self.UID))
            rec = self._say("ROTATE anna", ctx)
            texts = " ".join(str(c[1][0]) for c in rec.calls if c[0] == "reply_text")
            self.assertIn(i18n.t(self.UID, "sub_rotated_ok"), texts)
            self.assertIn("/s-%s/mihomo.yaml" % SUB_ROTATED, texts)
            _, ctx = self._press("sub_rev~anna")
            self.assertEqual(bot.CONFIRM.pending(self.UID)["phrase"], "REVOKE anna")
            rec = self._say("REVOKE anna", ctx)
            texts = " ".join(str(c[1][0]) for c in rec.calls if c[0] == "reply_text")
            self.assertIn(i18n.tf(self.UID, "sub_revoked_ok_fmt", "anna"), texts)
        finally:
            os.environ.pop("FAKE_BOT_ARGS_FILE", None)
        with open(args_file, encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn("--action=sub-rotate\t--json\t--name=anna", content)
        self.assertIn("--action=sub-revoke\t--json\t--name=anna", content)


# Ответ sub-show с выбором по клиенту (поле clients) — общая фикстура панели, бота и SSH.
SUB_CLIENTS_FIXTURE = os.path.join(ROOT, "tests", "fixtures", "sub_clients.json")


def sub_clients_answer(**extra):
    with open(SUB_CLIENTS_FIXTURE, encoding="utf-8") as fh:
        r = json.load(fh)
    r.update(extra)
    return r


def sub_record(r, cid):
    return next(c for c in bot.sub_clients(r) if c["id"] == cid)


class SubsClientsTextTests(unittest.TestCase):
    """Выбор по клиенту без python-telegram-bot: какие приложения и в каком порядке (записи с одним app — одно
    приложение), экраны выбора и приложения без адресов, кнопки по полям qr, qr_alt и copy, сообщение «Скопировать»;
    ответ без clients (ядро старше) — прежний путь."""

    def test_apps_go_by_group_one_app_per_app_and_junk_is_dropped(self):
        r = sub_clients_answer()
        self.assertEqual([c["id"] for c in bot.sub_clients(r)],
                         ["flclash", "clash-meta", "clash-verge-rev", "clash-party", "incy", "awgm-txt", "amneziavpn", "defaultvpn",
                          "amneziawg", "keenetic", "wgtunnel", "go-series", "awgm-vpn", "awgm-conf"])
        apps = bot.sub_apps(bot.sub_clients(r))
        self.assertEqual([a["key"] for a in apps], ["flclash", "clash-meta", "clash-verge-rev", "clash-party", "incy", "awgm",
                                                    "amneziavpn", "defaultvpn", "amneziawg", "keenetic", "wgtunnel", "go-series"])
        awgm = next(a for a in apps if a["key"] == "awgm")
        self.assertEqual(([v["id"] for v in awgm["variants"]], awgm["group"]), (["awgm-txt", "awgm-vpn", "awgm-conf"], "auto"))
        # Порядок групп — «Обновляется сам», затем «Разово», даже если ядро прислало их вперемешку
        r["clients"] = list(reversed(r["clients"]))
        self.assertEqual([c["group"] for c in bot.sub_clients(r)][:6], ["auto"] * 6)
        # id в callback_data — только по форме; группа — только auto/once; адрес — только https
        junk = sub_clients_answer(clients=[
            {"id": "../x", "group": "auto", "items": [{"url": "https://h/s-x/all.yaml"}]},
            {"id": "ok-one", "group": "later", "items": [{"url": "https://h/s-x/all.yaml"}]},
            {"id": "ok-two", "group": "once", "items": [{"url": "javascript:alert(1)"}, "x"]},
            {"id": "ok-three", "group": "once", "items": [{"url": "https://h/s-x/all.vpn"}]}, "garbage"])
        self.assertEqual([c["id"] for c in bot.sub_clients(junk)], ["ok-three"])
        # Ядро старше — поля нет: выбора нет, бот отдаёт прежнее сообщение с адресами
        old = sub_clients_answer()
        del old["clients"]
        self.assertEqual(bot.sub_clients(old), [])

    def test_choice_and_app_screens_carry_no_address(self):
        r = sub_clients_answer()
        text = bot.sub_chooser_text(None, r, bot.sub_clients(r), head="✅ x")
        for words in (i18n.t(None, "sub_pick"), i18n.t(None, "sub_grp_auto_note"), i18n.t(None, "sub_grp_once_note"), "✅ x"):
            self.assertIn(words, text)
        self.assertNotIn(i18n.t(None, "sub_user_off"), text)
        self.assertIn(i18n.t(None, "sub_user_off"), bot.sub_chooser_text(None, dict(r, disabled=True), bot.sub_clients(r)))
        once = [c for c in bot.sub_clients(r) if c["group"] == "once"]
        self.assertNotIn(i18n.t(None, "sub_grp_auto_note"), bot.sub_chooser_text(None, r, once))
        for c in bot.sub_clients(r):
            app = bot.sub_client_text(None, r, c)
            self.assertNotIn("https://", app, c["id"])
            self.assertNotIn(SUB_SECRET, app, c["id"])
            self.assertIn(i18n.t(None, "sub_ways"), app)
            self.assertIn(i18n.t(None, "sub_is_key"), app)
        self.assertNotIn(SUB_SECRET, text)
        f = bot.sub_client_text(None, r, sub_record(r, "flclash"))
        self.assertIn(i18n.tf(None, "sub_auto_every_fmt", 24), f)          # у FlClash — свои 24 часа
        self.assertIn("Android, Windows, macOS, Linux", f)
        hint = sub_record(r, "flclash")["hint"][i18n.get_user_lang(None)]
        self.assertIn(i18n.tf(None, "sub_howto_fmt", bot.esc(hint)), f)
        # 0 часов — интервал сервера (update_interval), {minutes} в подсказке — он же в минутах
        m = bot.sub_client_text(None, r, sub_record(r, "clash-meta"))
        self.assertIn(i18n.tf(None, "sub_auto_every_fmt", 12), m)
        self.assertIn("— 720", m)
        self.assertNotIn("{minutes}", m)
        self.assertIn("Apple TV", bot.sub_client_text(None, r, sub_record(r, "incy")))
        self.assertIn(i18n.tf(None, "sub_format_fmt", "Ключ vpn://"), bot.sub_client_text(None, r, sub_record(r, "awgm-vpn")))
        self.assertIn(i18n.t(None, "sub_once_note"), bot.sub_client_text(None, r, sub_record(r, "awgm-vpn")))

    def test_buttons_follow_qr_copy_and_lines_without_a_short_link(self):
        r = sub_clients_answer()
        qr, copy = i18n.t(None, "sub_btn_qr"), i18n.t(None, "sub_btn_copy")
        off = lambda why: (i18n.tf(None, "sub_btn_qr_off_fmt", i18n.t(None, why)), "noop", "")  # noqa: E731
        btns = lambda cid: bot.sub_client_buttons(None, r, sub_record(r, cid))  # noqa: E731
        self.assertEqual(btns("flclash"), [[(qr, "sbq", "0"), (copy, "sbc", "0")]])
        self.assertEqual(btns("incy"), [[(qr, "sbq", "0"), (copy, "sbc", "0")]])
        # qr "" — неактивный QR с причиной по типу приложения
        self.assertEqual(btns("clash-verge-rev"), [[off("sub_noqr_desktop"), (copy, "sbc", "0")]])
        self.assertEqual(btns("keenetic"), [[off("sub_noqr_router"), (copy, "sbc", "0")]])
        self.assertEqual(btns("go-series"), [[off("sub_noqr_paste"), (copy, "sbc", "0")]])
        self.assertEqual(btns("awgm-txt"), [[off("sub_noqr_router"), (copy, "sbc", "0")]])
        hdr = lambda line: [(i18n.tf(None, "sub_line_hdr_fmt", "AmneziaWG %s" % line), "noop", "")]  # noqa: E731
        self.assertEqual(btns("wgtunnel"), [hdr("3.1"), [off("sub_noqr_app"), (copy, "sbc", "0")],
                                            hdr("2.0"), [off("sub_noqr_app"), (copy, "sbc", "1")]])
        # QR ключа линии — кнопкой выдачи ключа карточки, в формате key_format
        self.assertEqual(btns("amneziavpn"), [hdr("3.1"), [(qr, "u_key", "awg3~qrvpn"), (copy, "sbc", "0")],
                                              hdr("2.0"), [(qr, "u_key", "awg2~qrvpn"), (copy, "sbc", "1")]])
        self.assertEqual(btns("amneziawg")[1][0], (qr, "u_key", "awg3~qrconf"))
        # Линия, чей ключ — в приложении Amnezia (has_key: false), QR ключа не получает
        r2 = sub_clients_answer(lines=[{"line": "3.1", "profile": "awg3", "has_key": True},
                                       {"line": "2.0", "profile": "awg2", "has_key": False}])
        self.assertEqual(bot.sub_client_buttons(None, r2, sub_record(r2, "amneziavpn"))[3][0], off("sub_noqr_nokey"))
        # «Короткая ссылка» — только при short_available (бот настроен): без него кнопки нет даже с short_url
        r3 = sub_clients_answer()
        next(c for c in r3["clients"] if c["id"] == "flclash")["items"][0]["short_url"] = "https://vpn.example.com/K7Q-2MXP"
        self.assertEqual(bot.sub_client_buttons(None, r3, sub_record(r3, "flclash")), [[(qr, "sbq", "0"), (copy, "sbc", "0")]])

    def test_qr_pictures_and_what_copy_gives(self):
        r = sub_clients_answer()
        base = "https://vpn.example.com/s-%s/" % SUB_SECRET
        self.assertEqual(bot.sub_item_qrs(sub_record(r, "flclash")["items"][0]), [("url", base + "all.yaml")])
        # incy: камерой телефона — ссылка «в приложение», сканером в incy — адрес (qr_alt)
        self.assertEqual(bot.sub_item_qrs(sub_record(r, "incy")["items"][0]),
                         [("deeplink", "incy://add/" + base + "all.txt"), ("alt", base + "all.txt")])
        self.assertEqual(bot.sub_item_qrs(sub_record(r, "clash-verge-rev")["items"][0]), [])
        self.assertEqual(bot.sub_item_qrs(sub_record(r, "amneziavpn")["items"][0]), [], "QR ключа — не картинкой адреса")
        bad = dict(sub_record(r, "incy")["items"][0], deeplink="javascript://%0Aalert(1)")
        self.assertEqual(bot.sub_item_qrs(bad), [])
        # «Скопировать» — по полю copy
        kinds = {cid: bot.sub_copy_kind(sub_record(r, cid)["items"][0]) for cid in
                 ("flclash", "keenetic", "amneziavpn", "amneziawg", "awgm-txt", "awgm-vpn", "awgm-conf")}
        self.assertEqual(kinds, {"flclash": "url", "keenetic": "command", "amneziavpn": "vpn", "amneziawg": "url",
                                 "awgm-txt": "url", "awgm-vpn": "vpn", "awgm-conf": "conf"})
        self.assertEqual(bot.sub_copy_kind({"command": "x", "url": "https://h"}), "command", "ядро без поля copy")
        self.assertEqual(bot.sub_copy_kind({"url": "https://h"}), "url")

    def test_copy_message_is_the_value_in_monospace(self):
        r = sub_clients_answer()
        base = "https://vpn.example.com/s-%s/" % SUB_SECRET
        f = sub_record(r, "flclash")
        text = bot.sub_value_text(None, r, f, f["items"][0])
        self.assertIn(i18n.tf(None, "sub_copy_url_fmt", "FlClash"), text)
        self.assertIn("<code>%sall.yaml</code>" % base, text)
        self.assertIn(i18n.t(None, "sub_is_key"), text)
        k = sub_record(r, "keenetic")
        text = bot.sub_value_text(None, r, k, k["items"][0])
        self.assertIn(i18n.tf(None, "sub_copy_cmd_fmt", "Keenetic / Netcraze · AmneziaWG 2.0"), text)
        self.assertIn("<code>interface Wireguard9 wireguard import %sawg20.conf</code>" % base, text)
        a = sub_record(r, "amneziavpn")
        text = bot.sub_value_text(None, r, a, a["items"][1], key={"vpn": "vpn://KEY<2>", "disabled": True})
        self.assertIn(i18n.tf(None, "sub_copy_key_fmt", "AmneziaVPN · AmneziaWG 2.0"), text)
        self.assertIn("<code>vpn://KEY&lt;2&gt;</code>", text)
        self.assertTrue(text.startswith(i18n.t(None, "key_disabled_warn")), "выключенному — предупреждение первым")
        c = sub_record(r, "awgm-conf")
        text = bot.sub_value_text(None, r, c, c["items"][0], key={"conf": "[Interface]\nPrivateKey = K\n"})
        self.assertIn(i18n.tf(None, "sub_copy_conf_fmt", "AWG Manager · AmneziaWG 3.1"), text)
        self.assertIn("<pre>[Interface]\nPrivateKey = K</pre>", text)
        # Ключа линии на сервере нет — адрес
        text = bot.sub_value_text(None, r, a, a["items"][0])
        self.assertIn("<code>%sawg31.vpn</code>" % base, text)


class SubsClientsTests(_BotScreenCase):
    """Выбор по клиенту в боте (ответ с полем clients): «Адреса подписки» — экран выбора приложения на месте экрана
    подписки; кнопка приложения — его экран на месте выбора (без адресов): формат, способы по линиям, «Назад»; значения —
    отдельными сообщениями без превью: QR картинкой, «Скопировать» — моноширинным текстом (ключ — действием user-key);
    адрес — заново через sub-show; в лог — только имя и id."""

    def setUp(self):
        os.environ["FAKE_BOT_MODE"] = "pro"
        os.environ["FAKE_BOT_SUB_CLIENTS"] = "1"
        self.addCleanup(os.environ.pop, "FAKE_BOT_MODE", None)
        self.addCleanup(os.environ.pop, "FAKE_BOT_SUB_CLIENTS", None)
        bot.invalidate_status()

    def test_show_turns_the_screen_into_the_app_choice(self):
        rec, _ = self._press("sub_show~anna")
        edits = [c for c in rec.calls if c[0] == "edit"]
        text = str(edits[-1][1][0])
        self.assertIn(i18n.t(self.UID, "sub_pick"), text)
        self.assertNotIn(SUB_SECRET, text)
        self.assertFalse([c for c in rec.calls if c[0] in ("reply_text", "reply_photo")], "адресов до выбора приложения нет")
        markup = edits[-1][2]["reply_markup"]
        datas = [b.callback_data for row in markup.inline_keyboard for b in row]
        labels = [b.text for row in markup.inline_keyboard for b in row]
        self.assertEqual(datas, ["noop", "sub_cl~anna~flclash", "sub_cl~anna~clash-meta", "sub_cl~anna~clash-verge-rev",
                                 "sub_cl~anna~clash-party", "sub_cl~anna~incy", "sub_cl~anna~awgm-txt", "noop",
                                 "sub_cl~anna~amneziavpn", "sub_cl~anna~defaultvpn", "sub_cl~anna~amneziawg",
                                 "sub_cl~anna~keenetic", "sub_cl~anna~wgtunnel", "sub_cl~anna~go-series", "u_sub~anna"])
        self.assertEqual(labels[0], i18n.t(self.UID, "sub_grp_auto"))
        self.assertEqual(labels[7], i18n.t(self.UID, "sub_grp_once"))
        self.assertIn("⭐ Clash Verge Rev", labels)
        self.assertEqual(labels.count("AWG Manager"), 1, "записи с одним app — одна кнопка")
        rows = [[b.callback_data for b in row] for row in markup.inline_keyboard]
        self.assertIn(["sub_cl~anna~go-series"], rows, "длинное имя — во всю ширину")
        self.assertIn(["sub_cl~anna~amneziavpn", "sub_cl~anna~defaultvpn"], rows)

    def test_app_screen_is_drawn_in_place_without_addresses(self):
        text, datas, _ = self._screen("sub_cl~anna~flclash")
        self.assertNotIn(SUB_SECRET, text)
        self.assertIn(i18n.t(self.UID, "sub_ways"), text)
        self.assertEqual(datas, ["sbq~anna~flclash~0", "sbc~anna~flclash~0", "sub_show~anna"])
        rec, _ = self._press("sub_cl~anna~flclash")
        self.assertFalse([c for c in rec.calls if c[0] in ("reply_text", "reply_photo")])
        # Неактивный QR — с причиной, кнопка ничего не делает
        _, datas, _ = self._screen("sub_cl~anna~keenetic")
        self.assertEqual(datas, ["noop", "sbc~anna~keenetic~0", "sub_show~anna"])
        # AmneziaVPN: по линии — заголовок, QR ключа выдачей карточки и «Скопировать»
        _, datas, _ = self._screen("sub_cl~anna~amneziavpn")
        self.assertEqual(datas, ["noop", "u_key~anna~awg3~qrvpn", "sbc~anna~amneziavpn~0",
                                 "noop", "u_key~anna~awg2~qrvpn", "sbc~anna~amneziavpn~1", "sub_show~anna"])
        self.assertIs(bot.resolve_handler("u_key~anna~awg3~qrvpn"), bot.cb_user_key)

    def test_one_app_with_formats(self):
        rec, _ = self._press("sub_cl~anna~awgm-vpn")
        edits = [c for c in rec.calls if c[0] == "edit"]
        markup = edits[-1][2]["reply_markup"]
        rows = [[(b.text, b.callback_data) for b in row] for row in markup.inline_keyboard]
        self.assertEqual(rows[:3], [[("Все линии .txt", "sub_cl~anna~awgm-txt")], [("✓ Ключ vpn://", "sub_cl~anna~awgm-vpn")],
                                    [("Файл .conf", "sub_cl~anna~awgm-conf")]])
        self.assertIn(i18n.tf(self.UID, "sub_format_fmt", "Ключ vpn://"), str(edits[-1][1][0]))
        self.assertIn([(i18n.tf(self.UID, "sub_line_hdr_fmt", "AmneziaWG 3.1"), "noop")], rows)
        self.assertIn([(i18n.tf(self.UID, "sub_btn_qr_off_fmt", i18n.t(self.UID, "sub_noqr_router")), "noop")], rows)
        self.assertEqual(rows[-1], [(i18n.t(self.UID, "btn_back"), "sub_show~anna")])

    def test_qr_and_copy_send_values_separately_and_not_to_the_log(self):
        args_file = os.path.join(TMP_ROOT, "sub_cl_args.txt")
        os.environ["FAKE_BOT_ARGS_FILE"] = args_file
        self.addCleanup(os.environ.pop, "FAKE_BOT_ARGS_FILE", None)
        catcher = _LogCatcher()
        bot.logger.addHandler(catcher)
        try:
            flclash, _ = self._press("sbq~anna~flclash~0")
            incy, _ = self._press("sbq~anna~incy~0")
            keenetic, _ = self._press("sbc~anna~keenetic~0")
            amnezia, _ = self._press("sbc~anna~amneziavpn~1")
            conf, _ = self._press("sbc~anna~awgm-conf~0")
        finally:
            bot.logger.removeHandler(catcher)
        if shutil.which("qrencode"):
            self.assertEqual([c[1][0] for c in flclash.calls if c[0] == "reply_photo"], [b"\x89PNG\r\n\x1a\n"])
            captions = [c[2].get("caption") for c in incy.calls if c[0] == "reply_photo"]
            self.assertEqual(captions, [i18n.tf(self.UID, "sub_qr_camera_fmt", "incy"), i18n.tf(self.UID, "sub_qr_scanner_fmt", "incy")])
        for rec in (flclash, incy, keenetic, amnezia, conf):
            self.assertFalse([c for c in rec.calls if c[0] == "edit"], "экран приложения остаётся")
        msg = [c for c in keenetic.calls if c[0] == "reply_text"]
        self.assertEqual(len(msg), 1)
        self.assertIn("<code>interface Wireguard9 wireguard import https://vpn.example.com/s-%s/awg20.conf</code>" % SUB_SECRET,
                      str(msg[0][1][0]))
        self.assertTrue(msg[0][2].get("disable_web_page_preview"), "превью унесло бы ключи на сервер Telegram")
        self.assertEqual(msg[0][2].get("parse_mode"), "HTML")
        # Ключ линии — действием user-key, как в карточке: vpn:// и текст .conf
        self.assertIn("<code>vpn://FAKEKEY</code>", str([c for c in amnezia.calls if c[0] == "reply_text"][0][1][0]))
        self.assertIn("<pre>[Interface]\nPrivateKey = FAKE-PRIVATE-KEY", str([c for c in conf.calls if c[0] == "reply_text"][0][1][0]))
        with open(args_file, encoding="utf-8") as fh:
            content = fh.read()
        self.assertEqual(content.count("--action=sub-show\t--json\t--name=anna"), 5, "адрес — заново через sub-show на каждое нажатие")
        self.assertIn("--action=user-key\t--json\t--name=anna\t--profile=awg2\t--format=vpn", content)
        self.assertIn("--action=user-key\t--json\t--name=anna\t--profile=awg3\t--format=conf", content)
        self.assertNotIn(SUB_SECRET, catcher.text())
        self.assertNotIn("FAKEKEY", catcher.text())
        self.assertIn("keenetic", catcher.text())

    def test_key_qr_button_is_the_card_key_delivery(self):
        rec, _ = self._press("u_key~anna~awg3~qrvpn")
        self.assertEqual(rec.count("reply_media_group"), 1, "серия QR ключа vpn:// — как из карточки")

    def test_bad_and_vanished_app(self):
        rec, _ = self._press("sub_cl~anna~../x")
        self.assertIn(("answer", (i18n.t(self.UID, "err_bad_input"),), {"show_alert": True}), rec.calls)
        rec, _ = self._press("sbc~anna~flclash~x")
        self.assertIn(("answer", (i18n.t(self.UID, "err_bad_input"),), {"show_alert": True}), rec.calls)
        for data in ("sub_cl~anna~no-such-app", "sbc~anna~flclash~7"):
            text, datas, _ = self._screen(data)
            self.assertIn(i18n.t(self.UID, "sub_client_gone"), text, data)
            self.assertIn("sub_cl~anna~flclash", datas)
            self.assertNotIn(SUB_SECRET, text)

    def test_create_and_rotate_show_the_choice_too(self):
        text, datas, _ = self._screen("sub_new~anna")
        self.assertIn(i18n.t(self.UID, "sub_ready_pick"), text)
        self.assertIn("sub_cl~anna~clash-verge-rev", datas)
        self.assertNotIn(SUB_SECRET, text)
        _, ctx = self._press("sub_rot~anna")
        rec = self._say("ROTATE anna", ctx)
        replies = [c for c in rec.calls if c[0] == "reply_text"]
        self.assertIn(i18n.t(self.UID, "sub_rotated_pick"), str(replies[-1][1][0]))
        self.assertNotIn(SUB_SECRET, " ".join(str(c[1][0]) for c in replies))
        datas = [b.callback_data for row in replies[-1][2]["reply_markup"].inline_keyboard for b in row]
        self.assertIn("sub_cl~anna~amneziawg", datas)


# ════════════════════════════════════════════════════════════════════════════
# Короткие ссылки с 2FA через Telegram-бота: запросы подтверждения, показ, замена, баны
# ════════════════════════════════════════════════════════════════════════════
# Ответы short-* и события — в форме договора ядра и интерфейсов (фикстура общая с панелью и SSH).
SHORT_FIXTURE = os.path.join(ROOT, "tests", "fixtures", "short_links.json")
with open(SHORT_FIXTURE, encoding="utf-8") as _fh:
    SHORT_FX = json.load(_fh)
# Коды и цели банов фикстуры: ни в callback_data, ни в лог бота они попадать не должны.
SHORT_SECRETS = ("NV439BKX", "NV43-9BKX", "Q8RT-W2KM", "K7Q2MXPA", "H3T-W7RQZ", "H3TW7RQZ")


def short_ev(name, **extra):
    """Событие фикстуры; срок по умолчанию — через 5 минут от сейчас (иначе обход сочтёт запрос истёкшим)."""
    ev = json.loads(json.dumps(SHORT_FX["events"][name]))
    if "expires" in ev:
        ev["expires"] = int(time.time()) + 300
    ev.update(extra)
    return ev


def put_short_event(qdir, ev, n=1):
    """Файл события, как его кладёт ядро (ga_notify): одна строка JSON, имя начинается с метки времени."""
    path = os.path.join(qdir, "20261004T120000Z_%d_%04d.json" % (os.getpid(), n))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
    return path


class FakeTgBot:
    """Бот Telegram для очереди коротких ссылок: что отправлено, что поправлено."""

    def __init__(self):
        self.sent, self.edits, self.mid = [], [], 500

    async def send_message(self, chat_id, text, **kw):
        self.mid += 1
        self.sent.append(dict(kw, chat_id=chat_id, text=text, message_id=self.mid))
        return type("M", (), {"message_id": self.mid, "chat_id": chat_id})()

    async def edit_message_text(self, text=None, chat_id=None, message_id=None, **kw):
        self.edits.append(dict(kw, chat_id=chat_id, message_id=message_id, text=text))


SHORT_ADMINS = (424242, 515151)
# Служебный код в скобках — «(short_pending)», «(<code>short_pending</code>)», «(rc=1)», «(127)»: людям его не показываем
# (SL-UX-03). Слова в скобках («(SSH, панель или агент)») — не код.
SERVICE_CODE_RE = re.compile(r"\((?:<code>)?[A-Za-z0-9_.:=-]+(?:</code>)?\)")


@contextlib.contextmanager
def short_sandbox(admins=SHORT_ADMINS):
    """Своя очередь и свой файл состояния, чистая память запросов, свои администраторы — после всё как было."""
    tmp = tempfile.mkdtemp(prefix="short-", dir=TMP_ROOT)
    qdir = os.path.join(tmp, "short-confirm")
    os.makedirs(qdir)
    saved = set(bot.ALLOWED_IDS)

    def reset():
        bot._SHORT.clear()
        bot._SHORT_LOADED["done"] = False
        bot._SHORT_LOCKS.clear()
        bot._SHORT_ADMIN_NAMES.clear()
        bot._SHORT_BOT["bot"] = None
    reset()
    bot.ALLOWED_IDS.clear()
    bot.ALLOWED_IDS.update(admins)
    try:
        with patch.object(bot, "SHORT_CONFIRM_DIR", qdir), \
                patch.object(bot, "SHORT_STATE_FILE", os.path.join(tmp, ".short_confirm.json")):
            yield qdir
    finally:
        bot.ALLOWED_IDS.clear()
        bot.ALLOWED_IDS.update(saved)
        reset()


def short_catalog_file():
    """lib/actions.json ядра с действиями short-* (каталог фикстуры) — во временный файл."""
    path = os.path.join(tempfile.mkdtemp(prefix="short-cat-", dir=TMP_ROOT), "actions.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"schema": 1, **SHORT_FX["catalog"]}, fh, ensure_ascii=False)
    return path


class ShortLinksTextTests(unittest.TestCase):
    """Без python-telegram-bot: раскладка клавиатуры запроса (вход 5 + 5 + 2, замена 5 + 5 + 1), разбор события и
    итога моста, тексты запроса и итога, who по каталогу ядра, тревога о подборе; без short_available — ни кнопки,
    ни строки о коротких ссылках."""

    def test_keyboard_is_12_buttons_5_5_2_for_login_and_11_for_replace(self):
        login = bot.short_event(short_ev("short_confirm_login"))
        rows = bot.short_confirm_rows(None, login)
        self.assertEqual([len(r) for r in rows], [5, 5, 2])
        self.assertEqual(sum(len(r) for r in rows), 12)
        nums = ["47", "12", "85", "33", "61", "90", "28", "74", "19", "56"]
        self.assertEqual([a for _, a in rows[0] + rows[1]], nums, "числа — в порядке ядра (оно их перемешало)")
        self.assertEqual([label for label, _ in rows[0] + rows[1]], nums)
        self.assertEqual(rows[2], [(i18n.t(None, "shl_btn_deny"), "deny"), (i18n.t(None, "shl_btn_swap"), "swap")])
        replace = bot.short_event(short_ev("short_confirm_replace"))
        rows = bot.short_confirm_rows(None, replace)
        self.assertEqual([len(r) for r in rows], [5, 5, 1])
        self.assertEqual(sum(len(r) for r in rows), 11)
        self.assertEqual(rows[2], [(i18n.t(None, "shl_btn_deny"), "deny")], "у замены «↻ Заменить ссылку» нет")
        self.assertEqual(i18n.t(None, "shl_btn_deny"), "Это не я")
        self.assertEqual(i18n.t(None, "shl_btn_swap"), "↻ Заменить ссылку")

    def test_event_is_cleaned_and_has_no_right_number(self):
        ev = bot.short_event(short_ev("short_confirm_login", options=[47, "12", 12, 9, 100, True, "x", 85, 33, 61, 90, 28, 74,
                                                                      19, 56, 77], extra="<junk>",
                                      device="<b>iPhone</b>", geo={"city": "Москва", "country": "Россия"}))
        self.assertEqual(ev["options"], [47, 12, 85, 33, 61, 90, 28, 74, 19, 56], "10–99 без повторов и мусора, не больше 10")
        self.assertNotIn("extra", ev)
        self.assertEqual(ev["kind"], "login")
        self.assertEqual(bot.short_geo_text(None, ev["geo"]), "Москва, Россия")
        self.assertEqual(bot.short_geo_text(None, {"ru": "Амстердам, Нидерланды", "en": "Amsterdam, Netherlands"}),
                         "Амстердам, Нидерланды")
        self.assertIsNone(bot.short_event(short_ev("short_confirm_login", request="bad~id")))
        self.assertIsNone(bot.short_event(short_ev("short_confirm_login", request="x" * 49)))
        self.assertIsNone(bot.short_event("junk"))
        # Срок — ISO UTC или epoch (секунды, миллисекунды)
        self.assertEqual(bot.short_epoch("2026-10-04T12:05:00Z"), 1791115500.0)
        self.assertEqual(bot.short_epoch(1791115500), 1791115500.0)
        self.assertEqual(bot.short_epoch("1791115500000"), 1791115500.0)
        self.assertEqual(bot.short_epoch("потом"), 0.0)
        self.assertEqual(bot.short_epoch(True), 0.0)

    def test_request_text_big_where_ip_whose_and_question(self):
        now = time.time()
        ev = bot.short_event(short_ev("short_confirm_login", expires=int(now) + 300, device="iPhone <Safari>"))
        text = bot.short_confirm_text(None, ev, now=now)
        lines = text.split("\n")
        self.assertEqual(lines[0], i18n.t(None, "shl_login_title"))
        self.assertEqual(lines[1], "<b>Москва, Россия · iPhone &lt;Safari&gt;</b>", "город и устройство — крупно")
        self.assertEqual(lines[2], i18n.tf(None, "shl_ip_fmt", "109.111.12.34") + " · Анна · all.yaml")
        self.assertEqual(lines[3], i18n.t(None, "shl_question"))
        self.assertEqual(lines[4], i18n.tf(None, "shl_left_fmt", 5))
        self.assertIn("Какое число на экране?", text)
        rep = bot.short_event(short_ev("short_confirm_replace", expires=int(now) + 300))
        text = bot.short_confirm_text(None, rep, now=now)
        self.assertTrue(text.startswith(i18n.t(None, "shl_replace_title")))
        self.assertNotIn("IP", text, "у замены из панели адреса нет — и строки нет")
        # Решённый — те же строки и итог вместо вопроса
        rec = {"ev": ev, "done": "ok", "by": "bot:424242", "by_name": "Вера <админ>"}
        text = bot.short_confirm_text(None, ev, rec)
        self.assertNotIn(i18n.t(None, "shl_question"), text)
        self.assertTrue(text.endswith("✅ Подтвердил: Вера &lt;админ&gt;"), text)

    def test_result_lines_by_status_and_who(self):
        line = lambda **rec: bot.short_result_line(None, rec)  # noqa: E731
        self.assertEqual(line(done="ok", by="panel"), "✅ Подтвердил: панель")
        self.assertEqual(line(done="ok", by="ssh"), "✅ Подтвердил: SSH")
        self.assertEqual(line(done="ok", by="bot:777"), "✅ Подтвердил: администратор 777")
        self.assertEqual(line(done="wrong", by="bot", by_name="Вера"), "❌ Отклонено: неверное число — Вера")
        self.assertEqual(line(done="denied", by="panel"), "⛔ Отклонено: «Это не я», адрес сразу закрыт — панель")
        self.assertEqual(line(done="swapped", by="panel"), "↻ Закрыто: ссылку заменили — панель")
        self.assertEqual(line(done="swapped", by="page"), "↻ Закрыто: ссылку заменили — страница подключения")
        self.assertEqual(line(done="expired"), "⌛ Время вышло")
        self.assertTrue(line(done="frozen", by="panel").startswith("🧊 Отклонено"))
        # Незнакомый итог — без служебного слова в скобках (SL-UX-03)
        self.assertEqual(line(done="later", by=""), "Закрыто — администратор")

    def test_outcome_from_the_bridge_answer(self):
        # Договор: итог — полем status; мост с занятым «success» — полем decision; отказ — пусто
        self.assertEqual(bot.short_outcome({"status": "ok"}), "ok")
        self.assertEqual(bot.short_outcome({"status": "pending"}), "pending")
        self.assertEqual(bot.short_outcome({"status": "success", "decision": "swapped"}), "swapped")
        self.assertEqual(bot.short_outcome({"status": "success", "result": "frozen"}), "frozen")
        self.assertEqual(bot.short_outcome({"status": "success"}), "")
        self.assertEqual(bot.short_outcome({"status": "error", "code": "short_request_gone"}), "")
        self.assertEqual(bot.short_outcome(None), "")

    def test_short_errors_speak_words_without_service_codes(self):
        """SL-UX-03 (настоящий бот на 168): «❌ По этой короткой ссылке уже ждут подтверждения в боте — попробуйте через 5
        мин (short_pending)». Отказ short-* — словами ядра без кода (они сами говорят, что делать); бот не настроен — своими
        словами, что делать; слов нет, они — тот же код или отказ незнакомый — «не выполнено» и «повторите…»; «занято»,
        таймаут и «моста нет» — словами бота, без кода и номера выхода."""
        pending = "По этой короткой ссылке уже ждут подтверждения в боте — попробуйте через 5 мин"
        failed, retry = i18n.t(None, "shl_err_failed"), i18n.t(None, "shl_err_retry")
        err = lambda code, message="", **kw: dict({"status": "error", "code": code, "message": message}, **kw)  # noqa: E731
        cases = [
            (err("short_pending", pending), pending),
            (err("short_limit", "По этой короткой ссылке за час уже 5 запросов — попробуйте через 12 мин"), "через 12 мин"),
            (err("short_not_found", "У anna ещё нет короткой ссылки для all.yaml — сначала покажите её"), "сначала покажите её"),
            (err("short_need_bot", "Короткие ссылки работают только с 2FA через Telegram-бота"), i18n.t(None, "shl_need_bot")),
            (err("short_need_bot"), i18n.t(None, "shl_need_bot")),
            (err("short_failed", "Короткие ссылки: действие не выполнено (rc=1)"),
             "Короткие ссылки: действие не выполнено. " + retry),
            (err("short_failed", "Действие не выполнено (short_failed)."), "Действие не выполнено. " + retry),
            (err("write_failed", "Не записал состояние (<code>write_failed</code>)"), "Не записал состояние. " + retry),
            (err("write_failed"), failed + " " + retry),
            (err("write_failed", "write_failed"), failed + " " + retry),
            (err(""), failed + " " + retry),
            (err("lock_timeout", "Другое действие уже выполняется (не дождались замка)."), i18n.t(None, "err_lock_timeout")),
            (err("timeout", timeout_s=60), i18n.tf(None, "err_timeout_fmt", 60)),
            (err("transport", rc=127), i18n.t(None, "shl_err_transport")),
        ]
        for r, want in cases:
            for text in (bot.short_error_text(None, r), bot.short_fail_text(None, r)):
                self.assertIn(want, text, r)
                self.assertIsNone(SERVICE_CODE_RE.search(text), text)
                self.assertNotIn("<code>", text)
                if "_" in r["code"]:
                    self.assertNotIn(r["code"], text)
                self.assertNotIn("127", text)
        self.assertTrue(bot.short_fail_text(None, err("short_pending", pending)).startswith("❌ "))
        self.assertTrue(bot.short_fail_text(None, err("lock_timeout")).startswith("<b>⏳ "), "«занято» — ожидание")
        self.assertEqual(bot.short_error_text(None, err("bad_target", "Не понял, что <снять>")), "Не понял, что &lt;снять&gt;")

    def test_who_is_the_admin_when_the_catalog_lets_it(self):
        cat = short_catalog_file()
        with patch.object(bot, "ACTIONS_JSON", cat):
            # Каталог фикстуры: who — перечень page | panel | ssh | bot → «bot», кто именно — в GOAMNEZIA_WHO
            self.assertEqual(bot.core_who_value("short-decide", "bot:424242", "bot"), "bot")
            self.assertTrue(bot.core_has_action("short-list"))
            self.assertFalse(bot.core_has_action("short-nope"))
        spec = dict(SHORT_FX["catalog"], param_types=dict(SHORT_FX["catalog"]["param_types"],
                                                           short_who={"type": "string", "pattern": "^(page|panel|ssh|bot)(:[0-9]{1,20})?$"}))
        with open(cat, "w", encoding="utf-8") as fh:
            json.dump(spec, fh)
        os.utime(cat, (time.time() + 5, time.time() + 5))
        with patch.object(bot, "ACTIONS_JSON", cat):
            self.assertEqual(bot.core_who_value("short-decide", "bot:424242", "bot"), "bot:424242")
            self.assertIsNone(bot.core_who_value("short-pending", "bot:1", "bot"), "у действия нет who — не передаём")
        with patch.object(bot, "ACTIONS_JSON", os.path.join(TMP_ROOT, "no-such-actions.json")):
            self.assertFalse(bot.core_has_action("short-list"), "ядро без каталога — коротких ссылок нет")

    def test_no_short_available_no_button_and_no_line(self):
        r = sub_clients_answer()
        flc = sub_record(r, "flclash")
        self.assertNotIn("sbs", [k for row in bot.sub_client_buttons(None, r, flc) for _, k, _ in row])
        self.assertNotIn(i18n.t(None, "shl_way_note"), bot.sub_client_text(None, r, flc))
        for value in (False, "true", 1, None):
            r2 = sub_clients_answer(short_available=value)
            self.assertNotIn("sbs", [k for row in bot.sub_client_buttons(None, r2, sub_record(r2, "flclash"))
                                     for _, k, _ in row], value)
        # С short_available — третьим действием своей строкой у каждого пункта
        r3 = sub_clients_answer(short_available=True)
        short = (i18n.t(None, "shl_btn_show"), "sbs", "0")
        rows = bot.sub_client_buttons(None, r3, sub_record(r3, "flclash"))
        self.assertEqual(rows[1], [short])
        self.assertEqual(i18n.t(None, "shl_btn_show"), "🔗 Короткая ссылка")
        rows = bot.sub_client_buttons(None, r3, sub_record(r3, "amneziavpn"))
        self.assertEqual([[k for _, k, _ in row] for row in rows],
                         [["noop"], ["u_key", "sbc"], ["sbs"], ["noop"], ["u_key", "sbc"], ["sbs"]])
        self.assertEqual(rows[5], [(i18n.t(None, "shl_btn_show"), "sbs", "1")])
        self.assertIn(i18n.t(None, "shl_way_note"), bot.sub_client_text(None, r3, sub_record(r3, "flclash")))
        self.assertIn("2FA через Telegram-бота", i18n.t(None, "shl_way_note"))

    def test_2fa_words_in_both_languages(self):
        self.assertIn("Короткие ссылки работают с 2FA через Telegram-бота", i18n.t_in("ru", "shl_2fa_note"))
        self.assertIn("Short links work with 2FA via the Telegram bot", i18n.t_in("en", "shl_2fa_note"))

    def test_alarm_says_how_many_and_offers_the_bans(self):
        ev = short_ev("short_alarm")
        text = bot.format_notify_event(None, ev)
        self.assertEqual(text, i18n.tf(None, "ntf_short_alarm_fmt", 137))
        self.assertIn("137", text)
        if bot.PTB_AVAILABLE:
            kb_ = bot.notify_keyboard(None, ev)
            self.assertEqual([b.callback_data for row in kb_.inline_keyboard for b in row], ["nt~shl_bans"])
            self.assertIs(bot.resolve_handler("shl_bans"), bot.cb_short_bans)

    def test_bans_lines_mask_the_code(self):
        bans = bot.short_bans(SHORT_FX["actions"]["short-bans"])
        self.assertEqual(len(bans), 2)
        ip = bot.short_ban_line(None, 1, bans[0])
        code = bot.short_ban_line(None, 2, bans[1])
        self.assertTrue(ip.startswith("1. IP 203.0.113.7 — до 2026-10-05 12:00:00 UTC"), ip)
        self.assertIn(i18n.t(None, "shl_reason_misses"), ip)
        self.assertIn("код ••••MXPA", code)
        self.assertNotIn("K7Q2MXPA", code)
        # Ступень словами (PNL-COS-03): «ступень 1» было непонятно — первый раз сутки, повторно месяц
        self.assertTrue(ip.endswith("подбор: три неверных кода подряд · бан на сутки (первый раз)"), ip)
        self.assertTrue(code.endswith("три неверных числа подряд · заморозка на месяц (повторно)"), code)
        self.assertNotIn("ступень", ip + code)
        self.assertTrue(bot.short_ban_line(None, 3, dict(bans[0], level=2)).endswith("бан на месяц (повторно)"))
        self.assertTrue(bot.short_ban_line(None, 4, dict(bans[1], level=1)).endswith("заморозка на сутки (первый раз)"))
        self.assertEqual(len(bot.short_ban_hash(bans[1])), 8)
        self.assertNotEqual(bot.short_ban_hash(bans[0]), bot.short_ban_hash(bans[1]))
        # Мусор ответа в список не идёт
        self.assertEqual(bot.short_bans({"bans": [{"kind": "ip", "target": "a b"}, {"kind": "x", "target": "1.2.3.4"}, "junk"]}), [])


@unittest.skipUnless(bot.PTB_AVAILABLE, "python-telegram-bot не установлен")
class ShortConfirmQueueTests(unittest.TestCase):
    """Очередь short-confirm: запрос — всем администраторам за секунду (отдельный цикл, раз в секунду, общий — раз в
    30 с), повтор события второй раз не шлётся; итог от ядра (short_decided) и истечение правят сообщение у всех;
    что у кого — в файле, перезапуск бота это не теряет; события коротких ссылок в общей очереди — своим путём."""

    def test_request_goes_to_every_admin_with_the_keyboard(self):
        with short_sandbox() as qdir:
            put_short_event(qdir, short_ev("short_confirm_login"))
            tg = FakeTgBot()
            self.assertEqual(run(bot.short_confirm_tick(tg)), 1)
            self.assertEqual(os.listdir(qdir), [], "файл события прочитан и удалён")
            self.assertEqual(sorted(m["chat_id"] for m in tg.sent), sorted(SHORT_ADMINS))
            for m in tg.sent:
                self.assertEqual(m["parse_mode"], "HTML")
                self.assertTrue(m["disable_web_page_preview"])
                self.assertIn(i18n.t(m["chat_id"], "shl_login_title"), m["text"])
                rows = m["reply_markup"].inline_keyboard
                self.assertEqual([len(r) for r in rows], [5, 5, 2])
                datas = [b.callback_data for row in rows for b in row]
                self.assertEqual(datas[:10], ["sc~rqLogin01~%s" % n for n in ("47", "12", "85", "33", "61", "90", "28", "74",
                                                                              "19", "56")])
                self.assertEqual(datas[10:], ["sc~rqLogin01~deny", "sc~rqLogin01~swap"])
                # В данных кнопок — только id запроса и ответ: ни кода, ни имени, ни адреса, ни суффикса
                for d in datas:
                    self.assertRegex(d, r"^sc~rqLogin01~(?:[1-9][0-9]|deny|swap)$")
                    self.assertLessEqual(len(d.encode()), 64)
                    for secret in SHORT_SECRETS + ("anna", "all.yaml", "109.111"):
                        self.assertNotIn(secret, d)
            # Что у кого — в файле 0600
            with open(bot.SHORT_STATE_FILE, encoding="utf-8") as fh:
                state = json.load(fh)
            self.assertEqual(sorted(c for c, _ in state["rqLogin01"]["msgs"]), sorted(SHORT_ADMINS))
            self.assertEqual(stat.S_IMODE(os.stat(bot.SHORT_STATE_FILE).st_mode), 0o600)
            # Повтор того же события второй раз не шлётся
            put_short_event(qdir, short_ev("short_confirm_login"), n=2)
            run(bot.short_confirm_tick(tg))
            self.assertEqual(len(tg.sent), len(SHORT_ADMINS))

    def test_replace_request_has_11_buttons(self):
        with short_sandbox() as qdir:
            put_short_event(qdir, short_ev("short_confirm_replace"))
            tg = FakeTgBot()
            run(bot.short_confirm_tick(tg))
            rows = tg.sent[0]["reply_markup"].inline_keyboard
            self.assertEqual([len(r) for r in rows], [5, 5, 1])
            self.assertEqual(rows[2][0].callback_data, "sc~rqSwap002~deny")
            self.assertIn(i18n.t(tg.sent[0]["chat_id"], "shl_replace_title"), tg.sent[0]["text"])

    def test_decided_event_edits_the_message_of_every_admin(self):
        with short_sandbox() as qdir:
            tg = FakeTgBot()
            put_short_event(qdir, short_ev("short_confirm_login"))
            run(bot.short_confirm_tick(tg))
            sent = {m["chat_id"]: m["message_id"] for m in tg.sent}
            for status, by, words in (("ok", "panel", "✅ Подтвердил: панель"), ("denied", "ssh", "⛔ Отклонено: «Это не я», адрес сразу закрыт — SSH"),
                                      ("swapped", "panel", "↻ Закрыто: ссылку заменили — панель")):
                tg.edits.clear()
                put_short_event(qdir, short_ev("short_decided", status=status, by=by), n=3)
                run(bot.short_confirm_tick(tg))
                self.assertEqual(sorted((e["chat_id"], e["message_id"]) for e in tg.edits), sorted(sent.items()), status)
                for e in tg.edits:
                    self.assertTrue(e["text"].endswith(words), e["text"])
                    self.assertIsNone(e["reply_markup"], "после решения кнопок нет")
                    self.assertNotIn(i18n.t(None, "shl_question"), e["text"])
            # Тот же итог второй раз — без лишних правок
            tg.edits.clear()
            put_short_event(qdir, short_ev("short_decided", status="swapped", by="panel"), n=4)
            run(bot.short_confirm_tick(tg))
            self.assertEqual(tg.edits, [])
            # Незнакомый запрос и мусор — ничего не правят
            put_short_event(qdir, short_ev("short_decided", request="rqOther99"), n=5)
            put_short_event(qdir, {"event": "short_decided", "request": "bad~id", "status": "ok"}, n=6)
            run(bot.short_confirm_tick(tg))
            self.assertEqual(tg.edits, [])

    def test_time_is_up_without_an_answer(self):
        with short_sandbox() as qdir:
            tg = FakeTgBot()
            now = time.time()
            put_short_event(qdir, short_ev("short_confirm_login", expires=int(now) + 300))
            run(bot.short_confirm_tick(tg, now=now))
            self.assertEqual(tg.edits, [])
            run(bot.short_confirm_tick(tg, now=now + 300 + bot.SHORT_EXPIRE_GRACE + 1))
            self.assertEqual(len(tg.edits), len(SHORT_ADMINS))
            self.assertTrue(all(e["text"].endswith("⌛ Время вышло") and e["reply_markup"] is None for e in tg.edits))
            run(bot.short_confirm_tick(tg, now=now + 400))
            self.assertEqual(len(tg.edits), len(SHORT_ADMINS), "второй раз «время вышло» не пишется")

    def test_restart_does_not_lose_the_messages(self):
        with short_sandbox() as qdir:
            tg = FakeTgBot()
            put_short_event(qdir, short_ev("short_confirm_login"))
            run(bot.short_confirm_tick(tg))
            # Перезапуск: память пуста, файл остался
            bot._SHORT.clear()
            bot._SHORT_LOADED["done"] = False
            put_short_event(qdir, short_ev("short_decided"), n=2)
            run(bot.short_confirm_tick(tg))
            self.assertEqual(len(tg.edits), len(SHORT_ADMINS))
            self.assertTrue(tg.edits[0]["text"].endswith("✅ Подтвердил: панель"))

    def test_queue_is_read_every_second_by_its_own_loop(self):
        """Запрос доходит за секунду-две: у очереди свой цикл раз в секунду, общая очередь — по-прежнему раз в 30 с."""
        self.assertEqual(bot.SHORT_CONFIRM_POLL_SECONDS, 1)
        self.assertEqual(bot.NOTIFY_POLL_SECONDS, 30)

        class App:
            bot = FakeTgBot()

        with short_sandbox() as qdir:
            async def scenario():
                task = asyncio.ensure_future(bot._short_confirm_loop(App))
                await asyncio.sleep(0.3)                     # первый обход — очередь пуста
                put_short_event(qdir, short_ev("short_confirm_login"))
                started = time.monotonic()
                while not App.bot.sent and time.monotonic() - started < 5:
                    await asyncio.sleep(0.05)
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
                return time.monotonic() - started
            took = run(scenario())
            self.assertEqual(len(App.bot.sent), len(SHORT_ADMINS))
            self.assertLess(took, 2.5, "запрос ждал дольше обхода раз в секунду")

    def test_announce_starts_the_short_loop(self):
        class Bot_:
            async def set_my_commands(self, *a, **kw):
                return True

            async def set_chat_menu_button(self, *a, **kw):
                return True

        class App:
            bot = Bot_()

        async def scenario():
            with patch.object(bot, "refresh_menu_button", lambda b: asyncio.sleep(0)):
                await bot._announce_self(App)
            task = bot._NOTIFY_TASK["short"]
            self.assertIsNotNone(task)
            self.assertFalse(task.done())
            await bot._shutdown(App)
            self.assertTrue(task.done())

        with short_sandbox(), patch.object(bot, "NOTIFY_DIR", os.path.join(TMP_ROOT, "no-notify")), \
                patch.object(bot, "PAIRING_POLL_SECONDS", 3600):
            run(scenario())

    def test_short_events_in_the_general_queue_go_their_way(self):
        """Ядро положило запрос или итог в общую очередь — бот всё равно рисует запрос с кнопками, а не «Событие …»;
        тревога о подборе — обычным уведомлением с кнопкой банов."""
        with short_sandbox():
            tg = FakeTgBot()
            self.assertTrue(run(bot.short_dispatch_event(tg, short_ev("short_confirm_login"))))
            self.assertEqual(len(tg.sent), len(SHORT_ADMINS))
            self.assertFalse(run(bot.short_dispatch_event(tg, short_ev("short_alarm"))))
            ndir = tempfile.mkdtemp(prefix="notify-", dir=TMP_ROOT)
            put_short_event(ndir, short_ev("short_alarm"))
            put_short_event(ndir, short_ev("short_confirm_replace"), n=2)

            class App:
                persistence = None

            App.bot = tg

            async def one_round():
                task = asyncio.ensure_future(bot._notify_loop(App))
                deadline = time.monotonic() + 5
                while len(tg.sent) < 3 * len(SHORT_ADMINS) and time.monotonic() < deadline:
                    await asyncio.sleep(0.05)
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            with patch.object(bot, "NOTIFY_DIR", ndir):
                run(one_round())
            alarms = [m for m in tg.sent if "137" in m["text"]]
            self.assertEqual(len(alarms), len(SHORT_ADMINS))
            self.assertTrue(alarms[0]["text"].startswith("🔔 "))
            self.assertEqual([b.callback_data for row in alarms[0]["reply_markup"].inline_keyboard for b in row], ["nt~shl_bans"])
            replaces = [m for m in tg.sent if i18n.t(m["chat_id"], "shl_replace_title") in m["text"]]
            self.assertEqual(len(replaces), len(SHORT_ADMINS))
            self.assertFalse([m for m in tg.sent if "short_confirm" in m["text"]], "запрос — не «Событие short_confirm»")


class ShortLinksBotTests(_BotScreenCase):
    """Короткие ссылки в боте: третьим действием у приложения (short_available), показ кодом и адресом отдельным
    сообщением (short-ensure), «↻ Заменить» (short-replace-request, число в чат), ответ на запрос кнопкой (short-decide,
    who — администратор, итог у всех), короткие ссылки подписки и баны со снятием (short-list, short-bans,
    short-unban). Кода нет ни в callback_data, ни в логе."""

    def setUp(self):
        os.environ["FAKE_BOT_MODE"] = "pro"
        os.environ["FAKE_BOT_SUB_CLIENTS"] = "1"
        os.environ["FAKE_BOT_SUB_SHORT"] = "1"
        self.args_file = os.path.join(tempfile.mkdtemp(prefix="short-args-", dir=TMP_ROOT), "args.txt")
        os.environ["FAKE_BOT_ARGS_FILE"] = self.args_file
        for k in ("FAKE_BOT_MODE", "FAKE_BOT_SUB_CLIENTS", "FAKE_BOT_SUB_SHORT", "FAKE_BOT_ARGS_FILE", "FAKE_BOT_SHORT",
                  "FAKE_BOT_SHORT_DECIDE", "FAKE_BOT_SHORT_BANS", "FAKE_BOT_ENV_FILE"):
            self.addCleanup(os.environ.pop, k, None)
        cat = patch.object(bot, "ACTIONS_JSON", short_catalog_file())
        cat.start()
        self.addCleanup(cat.stop)
        bot.invalidate_status()

    def args(self):
        try:
            with open(self.args_file, encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            return ""

    def test_client_screen_has_the_short_link_as_a_third_action(self):
        text, datas, _ = self._screen("sub_cl~anna~flclash")
        self.assertEqual(datas, ["sbq~anna~flclash~0", "sbc~anna~flclash~0", "sbs~anna~flclash~0", "sub_show~anna"])
        self.assertIn(i18n.t(self.UID, "shl_way_note"), text)
        _, datas, _ = self._screen("sub_cl~anna~amneziavpn")
        self.assertEqual(datas, ["noop", "u_key~anna~awg3~qrvpn", "sbc~anna~amneziavpn~0", "sbs~anna~amneziavpn~0",
                                 "noop", "u_key~anna~awg2~qrvpn", "sbc~anna~amneziavpn~1", "sbs~anna~amneziavpn~1", "sub_show~anna"])
        # Без short_available (бот не настроен) — как раньше, два способа
        os.environ["FAKE_BOT_SUB_SHORT"] = "0"
        text, datas, _ = self._screen("sub_cl~anna~flclash")
        self.assertEqual(datas, ["sbq~anna~flclash~0", "sbc~anna~flclash~0", "sub_show~anna"])
        self.assertNotIn(i18n.t(self.UID, "shl_way_note"), text)

    def test_short_link_is_sent_with_code_address_and_2fa_note(self):
        catcher = _LogCatcher()
        bot.logger.addHandler(catcher)
        try:
            rec, _ = self._press("sbs~anna~flclash~0")
        finally:
            bot.logger.removeHandler(catcher)
        self.assertIn("--action=sub-show\t--json\t--name=anna", self.args())
        self.assertIn("--action=short-ensure\t--json\t--name=anna\t--suffix=all.yaml", self.args())
        msgs = [c for c in rec.calls if c[0] == "reply_text"]
        self.assertEqual(len(msgs), 1)
        text, kw = str(msgs[0][1][0]), msgs[0][2]
        self.assertIn("<b>NV43-9BKX</b>", text)
        self.assertIn("<code>https://vpn.example.com/NV43-9BKX</code>", text)
        self.assertIn(i18n.t(self.UID, "shl_2fa_note"), text)
        self.assertIn("2FA через Telegram-бота", text)
        self.assertIn("FlClash", text)
        self.assertTrue(kw.get("disable_web_page_preview"))
        datas = [b.callback_data for row in kw["reply_markup"].inline_keyboard for b in row]
        self.assertEqual(datas, ["sbr~anna~all.yaml"])
        self.assertFalse([c for c in rec.calls if c[0] == "edit"], "экран приложения остаётся")
        for secret in SHORT_SECRETS:
            self.assertNotIn(secret, catcher.text())
        self.assertIn("all.yaml", catcher.text())

    def test_router_gets_the_command_with_the_short_link(self):
        rec, _ = self._press("sbs~anna~keenetic~0")
        text = str([c for c in rec.calls if c[0] == "reply_text"][0][1][0])
        self.assertIn("<code>interface Wireguard9 wireguard import https://vpn.example.com/NV43-9BKX</code>", text)
        self.assertNotIn("/s-", text, "полной ссылки в сообщении короткой нет")
        self.assertIn("--suffix=awg20.conf", self.args())

    def test_short_link_from_the_list_by_suffix_and_refusals(self):
        rec, _ = self._press("sbs~anna~awg20.conf")
        self.assertIn("--action=short-ensure\t--json\t--name=anna\t--suffix=awg20.conf", self.args())
        self.assertNotIn("sub-show", self.args(), "по суффиксу — без sub-show")
        self.assertIn("NV43-9BKX", str([c for c in rec.calls if c[0] == "reply_text"][0][1][0]))
        os.environ["FAKE_BOT_SHORT"] = "need_bot"
        rec, _ = self._press("sbs~anna~all.yaml")
        msg = [c for c in rec.calls if c[0] == "reply_text"][0]
        self.assertEqual(str(msg[1][0]), "❌ " + i18n.t(self.UID, "shl_need_bot"))
        self.assertEqual([b.callback_data for row in msg[2]["reply_markup"].inline_keyboard for b in row], ["u_sub~anna"])
        for data in ("sbs~anna~mihomo.yaml", "sbs~../x~all.yaml", "sbr~anna~awg1.conf", "sc~bad.id~47", "sc~rqLogin01~100",
                     "sc~rqLogin01~5", "shl_ub~x~abcdef01", "shl_ub~0~XYZ"):
            rec, _ = self._press(data)
            self.assertIn(("answer", (i18n.t(self.UID, "err_bad_input"),), {"show_alert": True}), rec.calls, data)

    def test_replace_shows_the_number_and_asks_the_core_as_the_bot(self):
        rec, _ = self._press("sbr~anna~all.yaml")
        self.assertIn("--action=short-replace-request\t--json\t--name=anna\t--suffix=all.yaml\t--who=bot", self.args())
        text = str([c for c in rec.calls if c[0] == "reply_text"][0][1][0])
        self.assertIn(i18n.tf(self.UID, "shl_replace_number_fmt", 47), text)
        self.assertIn("<b>47</b>", text)
        self.assertIn(i18n.t(self.UID, "shl_replace_how"), text)

    def _fail(self, answer, action="short-replace-request"):
        """Мост отвечает отказом answer (как его отдаёт ядро) на действие action."""
        os.environ["FAKE_BOT_SHORT_FAIL"] = json.dumps(answer, ensure_ascii=False)
        os.environ["FAKE_BOT_SHORT_FAIL_ACTION"] = action
        self.addCleanup(os.environ.pop, "FAKE_BOT_SHORT_FAIL", None)
        self.addCleanup(os.environ.pop, "FAKE_BOT_SHORT_FAIL_ACTION", None)

    def test_core_refusals_are_words_without_the_service_code(self):
        """SL-UX-03 (настоящий бот на 168): замена, пока по ссылке ждут подтверждения, — бот писал «❌ По этой короткой
        ссылке уже ждут подтверждения в боте — попробуйте через 5 мин (short_pending)». Теперь — слова ядра как есть и
        «Назад» к подписке; отказ без слов ядра — «не выполнено» и что делать; список коротких ссылок, баны и снятие бана —
        так же, без кода."""
        words = "По этой короткой ссылке уже ждут подтверждения в боте — попробуйте через 5 мин"
        self._fail({"status": "error", "message": words, "code": "short_pending"})
        rec, _ = self._press("sbr~anna~all.yaml")
        msgs = [c for c in rec.calls if c[0] == "reply_text"]
        self.assertEqual([str(m[1][0]) for m in msgs], ["❌ " + words])
        self.assertEqual([b.callback_data for row in msgs[0][2]["reply_markup"].inline_keyboard for b in row], ["u_sub~anna"])
        for answer in ({"status": "error", "message": "", "code": "short_failed"},
                       {"status": "error", "message": "Действие не выполнено (short_failed).", "code": "short_failed"}):
            self._fail(answer)
            rec, _ = self._press("sbr~anna~all.yaml")
            text = str([c for c in rec.calls if c[0] == "reply_text"][0][1][0])
            self.assertTrue(text.startswith("❌ "), text)
            self.assertIn(i18n.t(self.UID, "shl_err_retry"), text)
            self.assertNotIn("short_failed", text)
            self.assertIsNone(SERVICE_CODE_RE.search(text), text)
        for action, data in (("short-ensure", "sbs~anna~all.yaml"), ("short-list", "shl_list~anna"),
                             ("short-bans", "shl_bans~anna"), ("short-bans", "shl_bans")):
            self._fail({"status": "error", "message": "", "code": "write_failed"}, action)
            rec, _ = self._press(data)
            shown = [str(c[1][0]) for c in rec.calls if c[0] in ("edit", "reply_text")]
            self.assertTrue(shown, data)
            self.assertIn(i18n.t(self.UID, "shl_err_failed"), shown[-1], data)
            self.assertNotIn("write_failed", shown[-1], data)
            self.assertIsNone(SERVICE_CODE_RE.search(shown[-1]), shown[-1])
        self._fail({"status": "error", "message": "Не понял, что снять (bad_target)", "code": "bad_target"}, "short-unban")
        text, datas, _ = self._screen("shl_ub~1~%s~anna" % bot.short_ban_hash(SHORT_FX["actions"]["short-bans"]["bans"][1]))
        self.assertEqual(text, "❌ Не понял, что снять")
        self.assertEqual(datas, ["shl_list~anna"])

    def test_replacement_closes_the_pending_login_naming_who_replaced(self):
        """Ядро (вслед за SL-UX-03): замена от администратора не отказывает short_pending, а закрывает ждущий вход событием
        short_decided {status: swapped, by: who замены} — сообщение входа у всех администраторов правится: «↻ Закрыто:
        ссылку заменили — <кто>», кнопок нет. Из бота who замены — «bot:<id>» (каталог ядра его пропускает): закрытый вход
        называет администратора по имени."""
        for by, who in (("panel", "панель"), ("ssh", "SSH")):
            with short_sandbox():
                tg = FakeTgBot()
                self._sent_request(tg)
                put_short_event(bot.SHORT_CONFIRM_DIR, short_ev("short_decided", status="swapped", by=by), n=7)
                run(bot.short_confirm_tick(tg))
                self.assertEqual(len(tg.edits), len(SHORT_ADMINS), by)
                for e in tg.edits:
                    self.assertTrue(e["text"].endswith("↻ Закрыто: ссылку заменили — " + who), e["text"])
                    self.assertIsNone(e["reply_markup"])
                # SL-UX-06: замену входа в боте никто не просил — новой ссылки в бот нет (просившему — есть, см.
                # test_replacement_confirmed_elsewhere_or_declined_reaches_the_asker)
                self.assertEqual(len(tg.sent), len(SHORT_ADMINS), "только сами запросы, новых сообщений нет")
        with short_sandbox(), patch.object(bot, "ACTIONS_JSON", ACTIONS_JSON):
            tg = FakeTgBot()
            self._sent_request(tg)
            upd = FakeUpdate(self.UID, data="sbr~anna~all.yaml")
            upd.effective_user.full_name = "Аня"
            ctx = FakeContext()
            ctx.bot = tg
            run(bot.handle_callback(upd, ctx))
            self.assertIn("--action=short-replace-request\t--json\t--name=anna\t--suffix=all.yaml\t--who=bot:%d" % self.UID,
                          self.args(), "кто заменил — этот администратор")
            tg.edits.clear()
            put_short_event(bot.SHORT_CONFIRM_DIR, short_ev("short_decided", status="swapped", by="bot:%d" % self.UID), n=7)
            run(bot.short_confirm_tick(tg))
            login = sorted((m["chat_id"], m["message_id"]) for m in tg.sent)
            closed = [e for e in tg.edits if (e["chat_id"], e["message_id"]) in login]
            self.assertEqual(sorted((e["chat_id"], e["message_id"]) for e in closed), login, "вход закрыт у всех администраторов")
            for e in closed:
                self.assertTrue(e["text"].endswith("↻ Закрыто: ссылку заменили — Аня"), e["text"])
                self.assertIsNone(e["reply_markup"])
            # Нажатие другого администратора по закрытому входу — мост не зовётся, всплывает итог
            calls = self.args().count("short-decide")
            upd_b = FakeUpdate(515151, data="sc~rqLogin01~47")
            upd_b.callback_query.message.message_id = next(m["message_id"] for m in tg.sent if m["chat_id"] == 515151)
            ctx_b = FakeContext()
            ctx_b.bot = tg
            run(bot.handle_callback(upd_b, ctx_b))
            self.assertEqual(self.args().count("short-decide"), calls)
            self.assertIn(("answer", (i18n.tf(515151, "shl_already_fmt", "↻ Закрыто: ссылку заменили — Аня"),),
                           {"show_alert": True}), upd_b.rec.calls)

    def _sent_request(self, tg, name="short_confirm_login"):
        put_short_event(bot.SHORT_CONFIRM_DIR, short_ev(name))
        run(bot.short_confirm_tick(tg))
        mine = next(m for m in tg.sent if m["chat_id"] == self.UID)
        return mine["message_id"]

    def _press_on(self, data, message_id, tg):
        upd = FakeUpdate(self.UID, data=data)
        upd.callback_query.message.message_id = message_id
        ctx = FakeContext()
        ctx.bot = tg
        run(bot.handle_callback(upd, ctx))
        return upd.rec

    def test_pressing_a_number_decides_for_everyone(self):
        with short_sandbox():
            tg = FakeTgBot()
            mid = self._sent_request(tg)
            env_file = os.path.join(os.path.dirname(self.args_file), "who.txt")
            os.environ["FAKE_BOT_ENV_FILE"] = env_file
            rec = self._press_on("sc~rqLogin01~47", mid, tg)
            self.assertIn("--action=short-decide\t--json\t--request=rqLogin01\t--answer=47\t--who=bot", self.args())
            with open(env_file, encoding="utf-8") as fh:
                self.assertEqual(fh.read().split(), ["bot:%d" % self.UID], "кто именно решил — в GOAMNEZIA_WHO моста")
            words = "✅ Подтвердил: Tester &lt;x&gt;"
            # У другого администратора — правка его сообщения, у нажавшего — его же сообщение
            other = [e for e in tg.edits if e["chat_id"] != self.UID]
            self.assertEqual(len(other), len(SHORT_ADMINS) - 1)
            self.assertTrue(other[0]["text"].endswith(words), other[0]["text"])
            self.assertIsNone(other[0]["reply_markup"])
            mine = [c for c in rec.calls if c[0] == "edit"]
            self.assertTrue(str(mine[-1][1][0]).endswith(words))
            self.assertIsNone(mine[-1][2]["reply_markup"])
            self.assertIn(("answer", ("✅ Подтвердил: Tester <x>",), {"show_alert": False}), rec.calls)
            self.assertFalse([c for c in rec.calls if c[0] == "reply_text"], "вход — нового кода нет")
            # Второе нажатие — уже решено: мост не зовётся, всплывает итог
            calls_before = self.args().count("short-decide")
            rec2 = self._press_on("sc~rqLogin01~12", mid, tg)
            self.assertEqual(self.args().count("short-decide"), calls_before)
            self.assertIn(("answer", (i18n.tf(self.UID, "shl_already_fmt", "✅ Подтвердил: Tester <x>"),), {"show_alert": True}),
                          rec2.calls)
            # Итог от ядра о том же запросе позже — без повторной правки
            tg.edits.clear()
            put_short_event(bot.SHORT_CONFIRM_DIR, short_ev("short_decided", by="bot:%d" % self.UID), n=7)
            run(bot.short_confirm_tick(tg))
            self.assertEqual(tg.edits, [], "тот же итог — без повторной правки")

    def test_wrong_number_and_refusal_of_the_bridge(self):
        with short_sandbox():
            tg = FakeTgBot()
            mid = self._sent_request(tg)
            os.environ["FAKE_BOT_SHORT_DECIDE"] = "error"
            rec = self._press_on("sc~rqLogin01~47", mid, tg)
            alerts = [c for c in rec.calls if c[0] == "answer"]
            self.assertTrue(alerts[-1][2]["show_alert"])
            # Отказ моста — словами ядра, без служебного кода (SL-UX-03)
            self.assertEqual(alerts[-1][1][0], "Запрос подтверждения не найден: его не было или он закрыт больше часа назад")
            self.assertEqual(tg.edits, [], "отказ моста — запрос не решён, сообщения не трогаем")
            os.environ["FAKE_BOT_SHORT_DECIDE"] = "wrong"
            self._press_on("sc~rqLogin01~12", mid, tg)
            self.assertTrue(tg.edits and tg.edits[0]["text"].endswith("❌ Отклонено: неверное число — Tester &lt;x&gt;"))

    def test_number_reaches_the_bridge_while_a_long_bot_operation_holds_the_lock(self):
        """Нажатое число не ждёт долгой операции бота (установка профиля, смена режима держат _BOT_ACTION_LOCK минутами):
        short-decide уходит в мост сразу — у ядра свой замок state.json (_action_own_lock), — итог у всех и ответ
        нажавшему тоже сразу, пока операция бота ещё идёт."""
        with short_sandbox():
            tg = FakeTgBot()
            mid = self._sent_request(tg)

            async def scenario():
                lock = asyncio.Lock()
                with patch.object(bot, "_BOT_ACTION_LOCK", lock):
                    async with lock:                         # долгая операция бота держит замок
                        upd = FakeUpdate(self.UID, data="sc~rqLogin01~47")
                        upd.callback_query.message.message_id = mid
                        ctx = FakeContext()
                        ctx.bot = tg
                        try:
                            await asyncio.wait_for(bot.handle_callback(upd, ctx), timeout=10)
                        except asyncio.TimeoutError:
                            self.fail("нажатое число ждёт замок бота за долгой операцией")
                        self.assertTrue(lock.locked(), "операция бота ещё идёт")
                        return upd.rec
            rec = run(scenario())
            self.assertIn("--action=short-decide\t--json\t--request=rqLogin01\t--answer=47", self.args())
            self.assertIn(("answer", ("✅ Подтвердил: Tester <x>",), {"show_alert": False}), rec.calls)
            other = [e for e in tg.edits if e["chat_id"] != self.UID]
            self.assertEqual(len(other), len(SHORT_ADMINS) - 1, "итог у остальных администраторов — сразу")

    def test_core_already_decided_shows_who_decided_not_the_presser(self):
        """Гонку решает ядро: запрос уже решён в панели (already: true, by panel), а события short_decided бот ещё не
        видел — у всех итог «Подтвердил: панель», нажавшему — «Уже решено: …», не «Подтвердил: <он>»."""
        with short_sandbox():
            tg = FakeTgBot()
            mid = self._sent_request(tg)
            os.environ["FAKE_BOT_SHORT_DECIDE"] = "already"
            rec = self._press_on("sc~rqLogin01~47", mid, tg)
            self.assertIn("--action=short-decide\t--json\t--request=rqLogin01\t--answer=47", self.args())
            words = i18n.tf(self.UID, "shl_done_ok_fmt", i18n.t(self.UID, "shl_by_panel"))
            self.assertIn(("answer", (i18n.tf(self.UID, "shl_already_fmt", words),), {"show_alert": True}), rec.calls)
            other = [e for e in tg.edits if e["chat_id"] != self.UID]
            self.assertEqual(len(other), len(SHORT_ADMINS) - 1)
            self.assertTrue(other[0]["text"].endswith(words), other[0]["text"])
            self.assertNotIn("Tester", other[0]["text"])
            mine = [c for c in rec.calls if c[0] == "edit"]
            self.assertTrue(str(mine[-1][1][0]).endswith(words))
            self.assertIsNone(mine[-1][2]["reply_markup"])
            # Событие ядра о том же решении позже — без повторной правки
            tg.edits.clear()
            put_short_event(bot.SHORT_CONFIRM_DIR, short_ev("short_decided"), n=7)
            run(bot.short_confirm_tick(tg))
            self.assertEqual(tg.edits, [], "тот же итог — без повторной правки")

    def test_swap_and_confirmed_replacement_send_the_new_link(self):
        with short_sandbox():
            tg = FakeTgBot()
            mid = self._sent_request(tg)
            os.environ["FAKE_BOT_SHORT_DECIDE"] = "swapped"
            rec = self._press_on("sc~rqLogin01~swap", mid, tg)
            self.assertIn("--answer=swap", self.args())
            new = [c for c in rec.calls if c[0] == "reply_text"]
            self.assertEqual(len(new), 1)
            self.assertIn("<b>Q8RT-W2KM</b>", str(new[0][1][0]))
            self.assertIn(i18n.tf(self.UID, "shl_link_new_fmt", "Анна · all.yaml"), str(new[0][1][0]))
            self.assertEqual([b.callback_data for row in new[0][2]["reply_markup"].inline_keyboard for b in row],
                             ["sbr~anna~all.yaml"])
            # Замена, подтверждённая верным числом (мост: status success + decision) — новый код тоже
            os.environ["FAKE_BOT_SHORT_DECIDE"] = "merged"
            mid2 = self._sent_request(tg, "short_confirm_replace")
            rec = self._press_on("sc~rqSwap002~23", mid2, tg)
            new = [c for c in rec.calls if c[0] == "reply_text"]
            self.assertEqual(len(new), 1)
            self.assertIn("Анна · awg20.conf", str(new[0][1][0]))

    def _ask_replace(self, tg, name="Аня"):
        """Администратор A (UID) жмёт «↻ Заменить» под своей короткой ссылкой (сообщение 900); ответ с числом — новым
        сообщением 901. Затем ядро шлёт запрос замены всем администраторам. Вернёт id сообщения запроса у B."""
        upd = FakeUpdate(self.UID, data="sbr~anna~all.yaml")
        upd.effective_user.full_name = name
        upd.callback_query.message.message_id = 900
        texts = []

        async def reply_text(text, **kw):
            texts.append(text)
            upd.rec.record("reply_text", text, **kw)
            return type("M", (), {"chat_id": self.UID, "message_id": 901})()
        upd.callback_query.message.reply_text = reply_text
        ctx = FakeContext()
        ctx.bot = tg
        run(bot.handle_callback(upd, ctx))
        self.assertIn("<b>47</b>", texts[0])
        put_short_event(bot.SHORT_CONFIRM_DIR, short_ev("short_confirm_replace", request="rq7Kq2mXa", suffix="all.yaml"))
        run(bot.short_confirm_tick(tg))
        return next(m["message_id"] for m in tg.sent if m["chat_id"] == 515151)

    def _asker_view(self, tg):
        to_a = [m for m in tg.sent if m["chat_id"] == self.UID]
        edits = {e["message_id"]: e for e in tg.edits if e["chat_id"] == self.UID}
        return to_a, edits

    def test_replacement_asked_by_a_and_confirmed_by_b_goes_to_a(self):
        """SL-BOT-01 (живая проверка на test1): замену попросил A, подтвердил B — новый код получает A отдельным
        сообщением с «↻ Заменить», его прежнее сообщение с мёртвым кодом правится («заменена, новая — ниже»), сообщение с
        числом — итогом; B — только «↻ Заменено, ссылка у <A>», новой ссылки у B нет."""
        with short_sandbox():
            tg = FakeTgBot()
            b_mid = self._ask_replace(tg)
            tg.sent.clear()
            tg.edits.clear()
            os.environ["FAKE_BOT_SHORT_DECIDE"] = "merged"          # как у ядра: status success, итог — decision
            upd_b = FakeUpdate(515151, data="sc~rq7Kq2mXa~23")
            upd_b.callback_query.message.message_id = b_mid
            ctx_b = FakeContext()
            ctx_b.bot = tg
            run(bot.handle_callback(upd_b, ctx_b))
            to_a, edits = self._asker_view(tg)
            self.assertEqual(len(to_a), 1, "новый код — тому, кто просил")
            self.assertIn("<b>Q8RT-W2KM</b>", to_a[0]["text"])
            self.assertIn(i18n.tf(self.UID, "shl_link_new_fmt", "Анна · all.yaml"), to_a[0]["text"])
            self.assertEqual([b.callback_data for row in to_a[0]["reply_markup"].inline_keyboard for b in row], ["sbr~anna~all.yaml"])
            self.assertEqual(edits[900]["text"], i18n.tf(self.UID, "shl_link_replaced_fmt", "Анна · all.yaml"))
            self.assertIsNone(edits[900]["reply_markup"])
            self.assertNotIn("NV43", edits[900]["text"], "мёртвого кода на экране A больше нет")
            self.assertTrue(edits[901]["text"].endswith("✅ Подтвердил: Tester &lt;x&gt;"), edits[901]["text"])
            self.assertIn(("answer", ("↻ Заменено, ссылка у Аня",), {"show_alert": False}), upd_b.rec.calls)
            self.assertFalse([c for c in upd_b.rec.calls if c[0] == "reply_text"], "у B новой ссылки нет")
            self.assertFalse([m for m in tg.sent if m["chat_id"] == 515151])
            # Итог от ядра о том же запросе — второй раз ничего не шлёт
            put_short_event(bot.SHORT_CONFIRM_DIR, short_ev("short_decided", request="rq7Kq2mXa", by="bot:515151"), n=8)
            run(bot.short_confirm_tick(tg))
            self.assertEqual(len([m for m in tg.sent if m["chat_id"] == self.UID]), 1)

    def test_replacement_confirmed_elsewhere_or_declined_reaches_the_asker(self):
        """Замену из бота подтвердили в панели (short_decided без кода): новый код A берёт short-list; отказ — только итог
        в сообщении с числом, прежняя ссылка жива и не правится."""
        with short_sandbox():
            tg = FakeTgBot()
            self._ask_replace(tg)
            tg.sent.clear()
            tg.edits.clear()
            put_short_event(bot.SHORT_CONFIRM_DIR, short_ev("short_decided", request="rq7Kq2mXa", status="ok", by="panel"), n=5)
            run(bot.short_confirm_tick(tg))
            self.assertIn("--action=short-list\t--json\t--name=anna", self.args())
            to_a, edits = self._asker_view(tg)
            self.assertEqual(len(to_a), 1)
            self.assertIn("<b>NV43-9BKX</b>", to_a[0]["text"], "код — из short-list")
            self.assertTrue(edits[901]["text"].endswith("✅ Подтвердил: панель"), edits[901]["text"])
            self.assertIn(900, edits)
        with short_sandbox():
            tg = FakeTgBot()
            b_mid = self._ask_replace(tg)
            tg.sent.clear()
            tg.edits.clear()
            os.environ["FAKE_BOT_SHORT_DECIDE"] = "wrong"
            upd_b = FakeUpdate(515151, data="sc~rq7Kq2mXa~58")
            upd_b.callback_query.message.message_id = b_mid
            ctx_b = FakeContext()
            ctx_b.bot = tg
            run(bot.handle_callback(upd_b, ctx_b))
            to_a, edits = self._asker_view(tg)
            self.assertEqual(to_a, [], "замены не было — новой ссылки нет")
            self.assertNotIn(900, edits, "прежняя ссылка жива")
            self.assertTrue(edits[901]["text"].endswith("❌ Отклонено: неверное число — Tester &lt;x&gt;"), edits[901]["text"])

    def test_answer_keeps_what_the_admin_was_typing(self):
        """Ответ на запрос приходит посреди чего угодно: начатое (фраза ROTATE) не сбрасывается."""
        with short_sandbox():
            tg = FakeTgBot()
            mid = self._sent_request(tg)
            _, ctx = self._press("sub_rot~anna")
            upd = FakeUpdate(self.UID, data="sc~rqLogin01~47")
            upd.callback_query.message.message_id = mid
            ctx.bot = tg
            run(bot.handle_callback(upd, ctx))
            rec = self._say("ROTATE anna", ctx)
            replies = [c for c in rec.calls if c[0] == "reply_text"]
            self.assertIn(i18n.t(self.UID, "sub_rotated_pick"), str(replies[-1][1][0]))

    def test_card_list_and_bans_with_unban(self):
        text, datas, _ = self._screen("u_sub~anna")
        self.assertIn("shl_list~anna", datas)
        with patch.object(bot, "ACTIONS_JSON", os.path.join(TMP_ROOT, "no-such-actions.json")):
            _, datas, _ = self._screen("u_sub~anna")
            self.assertNotIn("shl_list~anna", datas, "ядро без коротких ссылок — кнопки нет")
        text, datas, _ = self._screen("shl_list~anna")
        self.assertIn(i18n.t(self.UID, "shl_2fa_note"), text)
        self.assertIn("• all.yaml — 2026-10-04 12:00:00 UTC", text)
        self.assertIn("• awg20.conf", text)
        self.assertEqual(datas, ["sbs~anna~all.yaml", "sbs~anna~awg20.conf", "shl_bans~anna", "u_sub~anna"])
        for secret in SHORT_SECRETS:
            self.assertNotIn(secret, text)
        text, datas, _ = self._screen("shl_bans~anna")
        self.assertIn(i18n.t(self.UID, "shl_bans_note"), text)
        self.assertIn("1. IP 203.0.113.7", text)
        self.assertIn("2. код ••••MXPA", text)
        bans = bot.short_bans(SHORT_FX["actions"]["short-bans"])
        self.assertEqual(datas, ["shl_ub~0~%s~anna" % bot.short_ban_hash(bans[0]), "shl_ub~1~%s~anna" % bot.short_ban_hash(bans[1]),
                                 "shl_list~anna"])
        for d in datas:
            self.assertNotIn("K7Q2MXPA", d, "код в кнопке не едет")
        text, datas, _ = self._screen("shl_ub~1~%s~anna" % bot.short_ban_hash(bans[1]))
        self.assertIn("--action=short-unban\t--json\t--target=K7Q2MXPA", self.args())
        self.assertIn("✅ " + i18n.tf(self.UID, "shl_unbanned_fmt", "код ••••MXPA"), text)
        # Метка не совпала (список сменился) — снятия нет, честное слово
        text, _, _ = self._screen("shl_ub~0~deadbeef~anna")
        self.assertEqual(self.args().count("short-unban"), 1)
        self.assertIn(i18n.t(self.UID, "shl_unban_gone"), text)
        # Из тревоги о подборе — тот же экран, «Назад» — на главный
        os.environ["FAKE_BOT_SHORT_BANS"] = "empty"
        text, datas, _ = self._screen("shl_bans")
        self.assertIn(i18n.t(self.UID, "shl_bans_none"), text)
        self.assertEqual(datas, ["menu_main"])


class MirrorTests(_BotScreenCase):
    """Настройки → «Копии приложений для клиентов» (docs/CLIENTS.md §8, §10): список с
    состоянием, версией, размером и занятым местом; загрузка — приложение →
    платформа, только зеркалируемые; удаление с вопросом."""

    def test_settings_has_the_mirror_item(self):
        text, datas, _ = self._screen("sec_settings")
        self.assertIn("se_mirror", datas)

    def test_list_shows_state_version_size_and_usage(self):
        text, datas, _ = self._screen("se_mirror")
        self.assertIn("amneziavpn", text)
        self.assertIn("5.0.1.5", text)
        self.assertIn(i18n.t(self.UID, "mr_state_ready"), text)
        self.assertIn("%s 25%%" % i18n.t(self.UID, "mr_state_downloading"), text)   # 50 из 200 МБ
        # У сорвавшейся — фраза ядра, код в скобках
        self.assertIn("%s: Для файла wgtunnel-1.2.3.apk из релиза v1.2.3 источник не отдал контрольную сумму — сборка не публикуется (mirror_unverified)"
                      % i18n.t(self.UID, "mr_state_failed"), text)
        self.assertIn(i18n.tf(self.UID, "mr_usage_fmt", 412, 3072), text)
        self.assertIn(i18n.t(self.UID, "mr_platform_android"), text)
        self.assertIn("mr_fetch", datas)
        self.assertIn("mr_del~amneziavpn~android", datas)
        self.assertIn("mr_del~wgtunnel~android", datas)
        self.assertIn("se_mirror", datas)        # обновить
        self.assertIn("sec_settings", datas)     # назад
        os.environ["FAKE_BOT_MIRROR"] = "empty"
        try:
            text, datas, _ = self._screen("se_mirror")
        finally:
            os.environ.pop("FAKE_BOT_MIRROR", None)
        self.assertIn(i18n.t(self.UID, "mr_none"), text)
        self.assertNotIn("mr_del~", " ".join(datas))

    def test_fetch_picks_app_then_platform_and_calls_the_bridge(self):
        args_file = os.path.join(TMP_ROOT, "mirror_args.txt")
        os.environ["FAKE_BOT_ARGS_FILE"] = args_file
        try:
            rec, ctx = self._press("mr_fetch")
            edits = [c for c in rec.calls if c[0] == "edit"]
            buttons = [b for row in edits[-1][2]["reply_markup"].inline_keyboard for b in row]
            datas = [b.callback_data for b in buttons]
            labels = [b.text for b in buttons]
            self.assertIn("mr_app~amneziavpn", datas)
            self.assertIn("mr_app~wgtunnel", datas)
            self.assertNotIn("mr_app~defaultvpn", datas, "закрытое приложение без mirror зеркалить нельзя")
            self.assertIn("WG Tunnel (%s)" % i18n.t(self.UID, "mr_unofficial"), labels)
            self.assertIn("AmneziaVPN", labels)
            text, datas, _ = self._screen("mr_app~amneziavpn", ctx)
            for p in ("android", "windows", "macos", "linux"):
                self.assertIn("mr_fetch~amneziavpn~%s" % p, datas)
            self.assertNotIn("mr_fetch~amneziavpn~ios", datas, "для iPhone копий не бывает")
            text, datas, _ = self._screen("mr_fetch~amneziavpn~android", ctx)
            self.assertIn(i18n.tf(self.UID, "mr_fetch_queued_fmt", "amneziavpn", i18n.t(self.UID, "mr_platform_android")), text)
            self.assertIn("se_mirror", datas)
            # отказ ядра показывается с кодом
            text, datas, _ = self._screen("mr_fetch~defaultvpn~ios", ctx)
            self.assertIn("not_mirrorable", text)
            # мусор в callback_data — алерт до моста
            rec, _ = self._press("mr_fetch~amneziavpn~amiga")
            self.assertTrue(any(c[2].get("show_alert") for c in rec.calls if c[0] == "answer"))
            rec, _ = self._press("mr_app~../etc")
            self.assertTrue(any(c[2].get("show_alert") for c in rec.calls if c[0] == "answer"))
        finally:
            os.environ.pop("FAKE_BOT_ARGS_FILE", None)
        with open(args_file, encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn("--action=mirror-fetch\t--json\t--app=amneziavpn\t--platform=android", content)
        self.assertNotIn("amiga", content)
        self.assertNotIn("../etc", content)

    def test_remove_asks_first(self):
        args_file = os.path.join(TMP_ROOT, "mirror_rm_args.txt")
        os.environ["FAKE_BOT_ARGS_FILE"] = args_file
        try:
            text, datas, ctx = self._screen("mr_del~amneziavpn~android")
            self.assertIn("mr_del_do~amneziavpn~android", datas)
            self.assertIn("se_mirror", datas)   # передумать можно
            text, datas, _ = self._screen("mr_del_do~amneziavpn~android", ctx)
            self.assertIn(i18n.tf(self.UID, "mr_removed_fmt", "amneziavpn", i18n.t(self.UID, "mr_platform_android")), text)
            rec, _ = self._press("mr_del~amneziavpn~amiga")
            self.assertTrue(any(c[2].get("show_alert") for c in rec.calls if c[0] == "answer"))
        finally:
            os.environ.pop("FAKE_BOT_ARGS_FILE", None)
        with open(args_file, encoding="utf-8") as fh:
            content = fh.read()
        self.assertEqual(content.count("--action=mirror-remove"), 1, "вопрос идёт до моста")
        self.assertIn("--action=mirror-remove\t--json\t--app=amneziavpn\t--platform=android", content)


class ModeSiteTests(_BotScreenCase):
    """Режим, шаблон сайта, проверка сайта, Alt-Svc, порты, панель на домене."""

    def _mode(self, mode):
        """Переключить режим заглушки и сбросить кэш status (он живёт 15 с)."""
        os.environ["FAKE_BOT_MODE"] = mode
        bot.invalidate_status()
        self.addCleanup(os.environ.pop, "FAKE_BOT_MODE", None)
        self.addCleanup(bot.invalidate_status)

    def test_mode_screen_offers_site_tools_only_where_a_site_exists(self):
        self._mode("lite")
        text, datas, _ = self._screen("se_mode")
        self.assertIn("se_mode_pick", datas)
        self.assertIn("se_pf", datas)
        self.assertNotIn("se_tpl", datas)          # в Lite сайта нет — нечего менять
        self.assertNotIn("se_check", datas)
        self.assertNotIn("se_alt", datas)
        os.environ["FAKE_BOT_MODE"] = "pro"
        bot.invalidate_status()
        text, datas, _ = self._screen("se_mode")
        for want in ("se_tpl", "se_check", "se_alt", "se_pf", "se_mode_pick"):
            self.assertIn(want, datas)
        self.assertIn("vpn.example.com", text)

    def test_endpoint_screen_names_the_domain_and_pins_the_ip(self):
        """В режиме Pro — в ключах домен; «IP сервера» вписывает адрес, а не пустое
        поле, которое снова дало бы домен (владелец 17.09.2026: файл по ссылке приходил с IP)."""
        self._mode("pro")
        text, datas, _ = self._screen("se_endpoint")
        self.assertIn("<code>vpn.example.com</code>", text)
        self.assertIn(i18n.t(self.UID, "endpoint_default"), text)
        self.assertIn("se_endpoint_ip", datas)
        self.assertNotIn("se_endpoint_auto", datas)      # поле и так пустое
        args_file = os.path.join(TMP_ROOT, "endpoint_pin_args.txt")
        os.environ["FAKE_BOT_ARGS_FILE"] = args_file
        try:
            self._press("se_endpoint_ip")
        finally:
            os.environ.pop("FAKE_BOT_ARGS_FILE", None)
        with open(args_file, encoding="utf-8") as fh:
            self.assertIn("--action=endpoint-set\t--json\t--host=203.0.113.10", fh.read())
        self._mode("lite")
        text, datas, _ = self._screen("se_endpoint")
        self.assertIn("<code>203.0.113.10</code>", text)
        self.assertNotIn("se_endpoint_ip", datas)        # в ключах и так IP

    def test_addresses_screen_takes_the_endpoint_from_the_core(self):
        """Экран «Адреса» угадывал адрес в ключах сам и называл домен, пока в каждом ключе стоял IP."""
        st = {"server_ip": "203.0.113.10", "domain": "vpn.example.com", "endpoint_host": "",
              "endpoint_effective": "203.0.113.10", "profiles": []}
        text = bot.addresses_text(self.UID, st)
        self.assertIn("%s: <code>203.0.113.10</code>" % i18n.t(self.UID, "st_addr_endpoint"), text)
        st["endpoint_effective"] = "vpn.example.com"
        text = bot.addresses_text(self.UID, st)
        self.assertIn("%s: <code>vpn.example.com</code>" % i18n.t(self.UID, "st_addr_endpoint"), text)

    def test_mode_pick_explains_each_mode_and_hides_the_current_one(self):
        self._mode("lite")
        text, datas, _ = self._screen("se_mode_pick")
        self.assertIn("se_mode_to~lite_site", datas)
        self.assertIn("se_mode_to~pro", datas)
        self.assertNotIn("se_mode_to~lite", datas)   # уже в нём
        for key in ("mode_desc_lite", "mode_desc_lite_site", "mode_desc_pro"):
            self.assertIn(i18n.t(self.UID, key), text)
        self.assertIn(i18n.t(self.UID, "se_mode_unsure"), text)

    def test_switch_to_pro_asks_domain_then_typed_phrase(self):
        args_file = os.path.join(TMP_ROOT, "mode_args.txt")
        os.environ["FAKE_BOT_ARGS_FILE"] = args_file
        self._mode("lite")
        try:
            # Линий две — сначала какая встанет на UDP 443 (F-3, сцена mode-pro-line); 2.0 — первой
            text, datas, ctx = self._screen("se_mode_to~pro")
            self.assertEqual(datas[:2], ["se_mode_pl~awg2", "se_mode_pl~awg3"])
            rec, ctx = self._press("se_mode_pl~awg2", ctx)
            self.assertEqual(ctx.user_data["flow"]["kind"], "mode_domain")
            self.assertEqual(ctx.user_data["flow"]["data"]["line"], "awg2")
            self._say("не домен", ctx)                       # ошибка ввода ожидание не снимает
            self.assertIn("flow", ctx.user_data)
            self._say("VPN.Example.COM", ctx)                # приводится к строчным
            self.assertIsNotNone(bot.CONFIRM.pending(self.UID))
            self.assertEqual(bot.CONFIRM.pending(self.UID)["phrase"], "MODE pro")
            self._say("MODE pro", ctx)
            self.assertIsNone(bot.CONFIRM.pending(self.UID))
        finally:
            os.environ.pop("FAKE_BOT_ARGS_FILE", None)
        with open(args_file, encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn("--action=change-mode\t--json\t--mode=pro\t--domain=vpn.example.com", content)
        self.assertNotIn("--action=pro-line", content, "2.0 у заглушки уже на UDP 443 — переносить нечего")

    def _mode_with_domain_reply(self, mode):
        """Из «Lite» в режим с сайтом с доменом vpn.example.com: вопрос о домене → фраза → ответ после моста."""
        _, _, ctx = self._screen("se_mode_to~%s" % mode)
        self.assertEqual(ctx.user_data["flow"]["kind"], "mode_domain")
        self._say("vpn.example.com", ctx)
        rec = self._say("MODE %s" % mode, ctx)
        replies = [str(c[1][0]) for c in rec.calls if c[0] == "reply_text"]
        self.assertTrue(replies, "ответа после моста нет")
        return replies[-1]

    def test_lets_encrypt_refusal_speaks_by_cause(self):
        """L-26: отказ Let's Encrypt при смене режима — своими словами по причине (что случилось и что сделать) и
        «режим, сайт и домен остались как были», а не слова ядра с кодом. Ядро без причины — как прежде."""
        self._mode("lite")
        self.addCleanup(os.environ.pop, "FAKE_BOT_CERT_FAIL", None)
        cases = (("dns", i18n.t(self.UID, "cert_cause_dns")), ("net", i18n.t(self.UID, "cert_cause_net")),
                 ("limit", i18n.t(self.UID, "cert_cause_limit")),
                 ("limit_until", i18n.tf(self.UID, "cert_cause_limit_until_fmt", "2026-09-27 10:00")))
        for cause, want in cases:
            os.environ["FAKE_BOT_CERT_FAIL"] = cause
            text = self._mode_with_domain_reply("lite_site")
            self.assertIn("❌ " + i18n.tf(self.UID, "cert_fail_title_fmt", "vpn.example.com"), text, cause)
            self.assertIn(want, text, cause)
            self.assertIn(i18n.t(self.UID, "cert_fail_kept"), text, cause)
            self.assertNotIn("СЛОВА-ЯДРА", text, cause)
            self.assertNotIn("cert_failed", text, cause)
        # Ядро старше (причины в ответе нет) — слова ядра и код, как было
        os.environ["FAKE_BOT_CERT_FAIL"] = "nocause"
        text = self._mode_with_domain_reply("lite_site")
        self.assertIn("СЛОВА-ЯДРА", text)
        self.assertIn("<code>cert_failed</code>", text)
        self.assertNotIn(i18n.t(self.UID, "cert_fail_kept"), text)

    def test_lets_encrypt_cause_texts_in_both_languages(self):
        """L-26: у каждой причины свой текст в обоих языках: что случилось и что сделать; срок лимита — подстановкой."""
        for lang in ("ru", "en"):
            texts = [i18n.t_in(lang, k) for k in ("cert_cause_dns", "cert_cause_net", "cert_cause_limit")]
            self.assertEqual(len(set(texts)), 3, lang)
            for t in texts + [i18n.t_in(lang, "cert_cause_limit_until_fmt")]:
                self.assertTrue(("Что сделать:" in t) if lang == "ru" else ("What to do:" in t), (lang, t))
            self.assertIn("%s", i18n.t_in(lang, "cert_cause_limit_until_fmt"))
        # Незнакомая причина или срок не по образцу — без выдумок: общий текст лимита, без причины — пусто
        self.assertEqual(bot.cert_cause_text(self.UID, {"cert_cause": "dns"}), i18n.t(self.UID, "cert_cause_dns"))
        self.assertEqual(bot.cert_cause_text(self.UID, {"cert_cause": "limit", "cert_retry_after": "<b>x</b>"}),
                         i18n.t(self.UID, "cert_cause_limit"))
        self.assertEqual(bot.cert_cause_text(self.UID, {"cert_cause": "mystery"}), "")
        self.assertEqual(bot.cert_cause_text(self.UID, None), "")

    def test_lazy_setup_says_why_the_site_is_on_the_fallback_certificate(self):
        """L-26: Ленивый Pro встал на запасном сертификате — почему Let's Encrypt отказал и что делать, а не общее
        «когда A-запись разойдётся»; отказ нового домена живого сайта — тоже своими словами."""
        r = {"status": "success", "mode": "pro", "domain": "example.com", "profiles": [], "keys": [],
             "warnings": ["cert_fallback", "panel_off"], "cert_cause": "net", "cert_retry_after": ""}
        text, _ = bot.lazy_result_view(self.UID, r, {}, "example.com")
        self.assertIn(i18n.tf(self.UID, "lazy_cert_fallback_fmt", "example.com"), text)
        self.assertIn(i18n.t(self.UID, "cert_cause_net"), text)
        self.assertIn(i18n.t(self.UID, "lazy_cert_fallback_where"), text)
        self.assertNotIn(i18n.t(self.UID, "lazy_code_cert_fallback"), text)
        self.assertIn(i18n.t(self.UID, "lazy_code_panel_off"), text)
        r.pop("cert_cause")
        text, _ = bot.lazy_result_view(self.UID, r, {}, "example.com")
        self.assertIn(i18n.t(self.UID, "lazy_code_cert_fallback"), text, "причина неизвестна — прежний текст")
        refusal = {"status": "error", "message": "СЛОВА-ЯДРА", "code": "cert_failed", "cert_cause": "limit",
                   "cert_retry_after": "2026-09-27 10:00", "plan": "pro", "reasons": []}
        text, _ = bot.lazy_refusal_view(self.UID, refusal, "new.example.com")
        self.assertIn(i18n.tf(self.UID, "cert_fail_title_fmt", "new.example.com"), text)
        self.assertIn(i18n.tf(self.UID, "cert_cause_limit_until_fmt", "2026-09-27 10:00"), text)
        self.assertNotIn("СЛОВА-ЯДРА", text)
        self.assertNotIn(i18n.t(self.UID, "cert_fail_kept"), text, "Ленивый Pro до сайта уже поставил профили")

    def _status_with_port(self, port):
        """Подменить status: важен только UDP-порт профиля, всё остальное — как у заглушки."""
        saved = bot.get_status

        async def fake(*_a, **_k):
            return {"status": "success", "mode": "lite", "domain": "vpn.example.com",
                    "server_ip": "203.0.113.10", "users": 0, "site": None,
                    "profiles": [{"profile": "awg2", "container": "amnezia-awg2", "state": "running",
                                  "port": port, "line": "2.0", "peers": 0, "enabled": True}]}
        bot.get_status = fake
        self.addCleanup(setattr, bot, "get_status", saved)

    def test_pro_puts_the_chosen_line_on_443_and_says_so_first(self):
        """F-3 (сцена mode-pro-line): при переходе в Pro линия встаёт на UDP 443 — pro-line сразу после change-mode.

        Сам change-mode UDP-порт по-прежнему не двигает (DEFECTS §1d.1 №10), но линию человек выбрал до фразы, и
        подтверждение называет её и смену порта — «порт не переносится» там больше не написано. Линия одна —
        без экрана выбора. Линия уже на 443 — pro-line не зовётся, и сделанное не предлагают.
        """
        desc = i18n.t(self.UID, "mode_desc_pro")
        self.assertNotIn("VPN и панель живут на том же 443", desc)
        self.assertIn("UDP 443, выбираете вы", desc)
        args_file = os.path.join(TMP_ROOT, "mode_pro_port_args.txt")
        os.environ["FAKE_BOT_ARGS_FILE"] = args_file
        self.addCleanup(os.environ.pop, "FAKE_BOT_ARGS_FILE", None)
        self._mode("lite")
        self._status_with_port(39000)                       # профиль не на 443 — переносить есть что
        # Сайт поднимается из «Lite»: домен спрашивается и тогда, когда он уже есть в конфиге, — прежний
        # оставляют кнопкой (как Enter в SSH); единственная линия едет дальше в callback_data
        text, datas, ctx = self._screen("se_mode_to~pro")
        self.assertIn("se_mode_keep~pro~awg2", datas)
        text, datas, ctx = self._screen("se_mode_keep~pro~awg2", ctx)
        self.assertEqual(bot.CONFIRM.pending(self.UID)["phrase"], "MODE pro")
        self.assertIn(i18n.tf(self.UID, "pl_switch_move_fmt", "AmneziaWG 2.0", "39000"), text)
        self.assertIn(i18n.t(self.UID, "pr_port_note"), text)
        self.assertNotIn(i18n.tf(self.UID, "se_mode_warn_fmt", "X", "Y").split("\n\n")[-1], text,
                         "«порт VPN не переносится» — неправда, когда линию переносят")
        rec = self._say("MODE pro", ctx)
        with open(args_file, encoding="utf-8") as fh:
            calls = [line for line in fh.read().splitlines() if "--action=change-mode" in line or "--action=pro-line" in line]
        self.assertEqual([c.split("\t")[0] for c in calls], ["--action=change-mode", "--action=pro-line"])
        self.assertIn("--action=pro-line\t--json\t--profile=awg2\t", calls[1])
        texts = " ".join(str(c[1][0]) for c in rec.calls if c[0] == "reply_text")
        self.assertIn(i18n.tf(self.UID, "pl_done_fmt", "AmneziaWG 2.0"), texts)
        self.assertNotIn(i18n.t(self.UID, "se_mode_port_hint"), texts, "линию поставили — «перенесите сами» не к месту")
        # А когда линия уже на 443 — ни pro-line, ни строки, ни кнопки: сделанное не предлагают
        self._status_with_port(443)
        open(args_file, "w").close()
        rec, ctx = self._press("se_mode_keep~pro~awg2")
        rec = self._say("MODE pro", ctx)
        with open(args_file, encoding="utf-8") as fh:
            self.assertNotIn("--action=pro-line", fh.read())
        replies = [c for c in rec.calls if c[0] == "reply_text"]
        texts = " ".join(str(c[1][0]) for c in replies)
        self.assertNotIn(i18n.t(self.UID, "se_mode_port_hint"), texts)
        markup = [c[2].get("reply_markup") for c in replies if c[2].get("reply_markup")][-1]
        datas = [b.callback_data for row in markup.inline_keyboard for b in row]
        self.assertFalse([d for d in datas if d.startswith("pr_443~")])

    def test_switch_to_lite_needs_no_domain(self):
        args_file = os.path.join(TMP_ROOT, "mode_lite_args.txt")
        os.environ["FAKE_BOT_ARGS_FILE"] = args_file
        self._mode("pro")
        try:
            rec, ctx = self._press("se_mode_to~lite")
            self.assertNotIn("flow", ctx.user_data)          # домен не спрашиваем
            self.assertEqual(bot.CONFIRM.pending(self.UID)["phrase"], "MODE lite")
            self._say("MODE lite", ctx)
        finally:
            os.environ.pop("FAKE_BOT_ARGS_FILE", None)
        with open(args_file, encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn("--action=change-mode\t--json\t--mode=lite", content)

    def test_bad_mode_is_refused(self):
        rec, _ = self._press("se_mode_to~turbo")
        self.assertTrue(any(c[2].get("show_alert") for c in rec.calls if c[0] == "answer"))

    def test_site_check_renders_verdict(self):
        self._mode("pro")
        text, datas, _ = self._screen("se_check")
        self.assertIn(i18n.t(self.UID, "site_verdict_ok"), text)
        self.assertIn("200", text)
        self.assertIn("se_check", datas)           # «Обновить» ведёт на себя же
        os.environ["FAKE_BOT_MODE"] = "lite"
        bot.invalidate_status()
        text, _, _ = self._screen("se_check")
        self.assertIn(i18n.t(self.UID, "site_verdict_not_pro"), text)

    def test_alt_svc_toggles(self):
        args_file = os.path.join(TMP_ROOT, "alt_args.txt")
        os.environ["FAKE_BOT_ARGS_FILE"] = args_file
        self._mode("pro")
        try:
            text, datas, ctx = self._screen("se_alt")
            self.assertIn("se_alt_set~on", datas)   # config.json теста пуст → выключен
            # Включение спрашивают (как в SSH), выключение — нет.
            text, datas, ctx = self._screen("se_alt_set~on", ctx)
            self.assertIn(i18n.t(self.UID, "se_alt_svc_warn"), text)
            self.assertIn("se_alt_set~on~do", datas)
            self._screen("se_alt_set~on~do", ctx)
            rec, _ = self._press("se_alt_set~maybe")
            self.assertTrue(any(c[2].get("show_alert") for c in rec.calls if c[0] == "answer"))
        finally:
            os.environ.pop("FAKE_BOT_ARGS_FILE", None)
        with open(args_file, encoding="utf-8") as fh:
            self.assertIn("--action=site-alt-svc\t--json\t--state=on", fh.read())

    def test_preflight_lists_busy_ports(self):
        os.environ["FAKE_BOT_PREFLIGHT"] = "busy"
        try:
            text, datas, _ = self._screen("se_pf")
        finally:
            os.environ.pop("FAKE_BOT_PREFLIGHT", None)
        self.assertIn("TCP/443", text)
        self.assertIn("nginx.service", text)
        self.assertIn(i18n.t(self.UID, "pf_unavailable"), text)
        self.assertIn(i18n.t(self.UID, "pf_available"), text)     # lite всё равно доступен
        self.assertIn("se_pf", datas)
        text, _, _ = self._screen("se_pf")                        # свободные порты
        self.assertNotIn("TCP/443", text)

    def test_panel_on_domain_needs_pro_and_sends_secret_separately(self):
        args_file = os.path.join(TMP_ROOT, "mini_args.txt")
        os.environ["FAKE_BOT_ARGS_FILE"] = args_file
        self._mode("lite")
        try:
            text, datas, _ = self._screen("se_panel")
            self.assertIn(i18n.t(self.UID, "se_mini_needs_pro"), text)
            self.assertNotIn("se_mini~on", datas)
            os.environ["FAKE_BOT_MODE"] = "pro"
            bot.invalidate_status()
            text, datas, _ = self._screen("se_panel")
            self.assertIn("se_mini~on", datas)
            catcher = _LogCatcher()
            bot.logger.addHandler(catcher)
            try:
                rec, _ = self._press("se_mini~on")
            finally:
                bot.logger.removeHandler(catcher)
            replies = [c for c in rec.calls if c[0] == "reply_text"]
            self.assertEqual(len(replies), 1)
            self.assertIn("<code>https://vpn.example.com/app-FAKEPATH0001/</code>", replies[0][1][0])
            self.assertTrue(replies[0][2].get("disable_web_page_preview"))
            self.assertIn(i18n.t(self.UID, "se_mini_url_is_key"), replies[0][1][0])
            self.assertNotIn("app-FAKEPATH0001", catcher.text())
            os.environ["FAKE_BOT_MINIAPP"] = "on"
            self.addCleanup(os.environ.pop, "FAKE_BOT_MINIAPP", None)
            text, datas, _ = self._screen("se_panel")
            self.assertIn("se_mini~rot", datas)
            self.assertIn("se_mini~off", datas)
            rec, _ = self._press("se_mini~rot")                   # смена пути — сперва вопрос
            datas = [b.callback_data for row in rec.calls[-1][2]["reply_markup"].inline_keyboard for b in row]
            self.assertIn("se_mini~rot_do", datas)
            rec, _ = self._press("se_mini~off")
            datas = [b.callback_data for row in rec.calls[-1][2]["reply_markup"].inline_keyboard for b in row]
            self.assertIn("se_mini~off_do", datas)
            self.assertEqual(rec.count("reply_text"), 0)          # вопрос моста ещё не звал
            rec, _ = self._press("se_mini~off_do")
            self.assertEqual(rec.count("reply_text"), 0)          # закрытие секрета не печатает
            rec, _ = self._press("se_mini~bogus")
            self.assertTrue(any(c[2].get("show_alert") for c in rec.calls if c[0] == "answer"))
        finally:
            os.environ.pop("FAKE_BOT_ARGS_FILE", None)
        with open(args_file, encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn("--action=miniapp-on", content)
        self.assertIn("--action=miniapp-off", content)


class ConfirmationParityTests(_BotScreenCase):
    """Восемь мест, где вопрос был только в SSH (DEFECTS §1d.2).

    Проверяем не текст, а поведение: кнопка сама по себе моста НЕ зовёт, а зовёт
    его только шаг согласия. Обратная заплатка в конце каждого теста не нужна —
    её роль играет проверка «до согласия аргументов нет».
    """

    def _args(self, name):
        path = os.path.join(TMP_ROOT, name)
        if os.path.exists(path):
            os.unlink(path)
        os.environ["FAKE_BOT_ARGS_FILE"] = path
        self.addCleanup(os.environ.pop, "FAKE_BOT_ARGS_FILE", None)
        return path

    @staticmethod
    def _read(path):
        if not os.path.exists(path):
            return ""
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    def test_profile_restart_asks_before_it_breaks_every_connection(self):
        args = self._args("restart_args.txt")
        text, datas, ctx = self._screen("pr_restart~awg2")
        self.assertIn("pr_restart_do~awg2", datas)
        self.assertIn(i18n.tf(self.UID, "pr_restart_warn_fmt", FX20), text)
        self.assertNotIn("--action=profile-restart", self._read(args))
        self._press("pr_restart_do~awg2", ctx)
        self.assertIn("--action=profile-restart\t--json\t--profile=awg2", self._read(args))

    def test_agent_enable_and_disable_both_ask(self):
        args = self._args("agent_toggle_args.txt")
        for data, done in (("ag_en~a-1a2b~off", "ag_en~a-1a2b~off_do"),
                           ("ag_en~a-3c4d~on", "ag_en~a-3c4d~on_do")):
            _, datas, ctx = self._screen(data)
            self.assertIn(done, datas)
        self.assertNotIn("--action=agent-", self._read(args))
        self._press("ag_en~a-1a2b~off_do")
        self._press("ag_en~a-3c4d~on_do")
        content = self._read(args)
        self.assertIn("--action=agent-disable\t--json\t--id=a-1a2b", content)
        self.assertIn("--action=agent-enable\t--json\t--id=a-3c4d", content)

    def test_transport_on_asks_in_modes_with_a_site(self):
        args = self._args("transport_args.txt")
        os.environ["FAKE_BOT_MODE"] = "pro"
        self.addCleanup(os.environ.pop, "FAKE_BOT_MODE", None)
        bot.invalidate_status()
        _, datas, ctx = self._screen("ag_http~on")
        self.assertIn("ag_http~on_do", datas)
        self.assertNotIn("--action=mcp-http-on", self._read(args))
        self._press("ag_http~on_do", ctx)
        self.assertIn("--action=mcp-http-on", self._read(args))

    def test_core_update_is_reachable_from_the_bot_and_needs_a_phrase(self):
        args = self._args("repair_args.txt")
        _, datas, ctx = self._screen("mt_upstream")
        # Ремонт предлагается у каждого профиля из ответа, а не только там, где
        # есть что обновлять: сюда приходят с уже упавшим контейнером.
        self.assertTrue([d for d in datas if d.startswith("up_fix~")], datas)
        rec, ctx = self._press("up_fix~awg2")
        self.assertIsNotNone(bot.CONFIRM.pending(self.UID))
        self.assertEqual(bot.CONFIRM.pending(self.UID)["phrase"], "443")    # порт профиля (D-5)
        self._say("REPAIR awg2", ctx)                       # имя места — не годится
        self.assertIsNotNone(bot.CONFIRM.pending(self.UID))
        self.assertNotIn("--action=core-update", self._read(args))
        self._say("443", ctx)
        self.assertIsNone(bot.CONFIRM.pending(self.UID))
        self.assertIn("--action=core-update\t--json\t--profile=awg2", self._read(args))


class AdminOwnerTests(_BotScreenCase):
    """Владелец бота: удалить его нельзя, и удалять других может только он."""

    def setUp(self):
        self._saved = (list(bot.ADMIN_ORDER), set(bot.ALLOWED_IDS), os.environ.get("OWNER_ID"))
        bot.ALLOWED_IDS.clear()
        bot.ALLOWED_IDS.update({777, self.UID})
        del bot.ADMIN_ORDER[:]
        bot.ADMIN_ORDER.extend([777, self.UID])
        # Владелец задан ЯВНО. Порядок записи доказательством не является:
        # прежние версии писали ALLOWED_IDS отсортированным по возрастанию id, и
        # на обновлённой установке «владельцем» становился приглашённый со
        # старым аккаунтом, а настоящий владелец терял управление админами.
        os.environ["OWNER_ID"] = "777"

    def tearDown(self):
        order, ids, own = self._saved
        bot.ALLOWED_IDS.clear()
        bot.ALLOWED_IDS.update(ids)
        del bot.ADMIN_ORDER[:]
        bot.ADMIN_ORDER.extend(order)
        if own is None:
            os.environ.pop("OWNER_ID", None)
        else:
            os.environ["OWNER_ID"] = own

    def test_only_owner_appoints_admins(self):
        # Приглашённый админ (self.UID) новых админов не назначает (решение владельца 27.09, L-25)
        upd = FakeUpdate(self.UID, text="/invite 555")
        run(bot.cmd_invite(upd, FakeContext(["555"])))
        self.assertNotIn(555, bot.ALLOWED_IDS)
        self.assertIn(i18n.t(self.UID, "admin_invite_owner_only"), " ".join(str(c) for c in upd.rec.calls))
        # Владелец — может
        self.addCleanup(bot.ALLOWED_IDS.discard, 556)
        upd = FakeUpdate(777, text="/invite 556")
        run(bot.cmd_invite(upd, FakeContext(["556"])))
        self.assertIn(556, bot.ALLOWED_IDS)

    def test_owner_comes_from_the_record_not_from_the_order(self):
        self.assertEqual(bot.owner_id(), 777)
        # Порядок переставлен — владелец прежний: решает запись, а не позиция.
        del bot.ADMIN_ORDER[:]
        bot.ADMIN_ORDER.extend([self.UID, 777])
        self.assertEqual(bot.owner_id(), 777)

    def test_without_the_record_nobody_is_the_owner(self):
        # Старая установка без OWNER_ID: владелец НЕ ОПРЕДЕЛЁН. Это честнее
        # догадки по порядку — иначе сосед получал бы власть над настоящим
        # владельцем и мог бы его удалить.
        os.environ.pop("OWNER_ID", None)
        self.assertIsNone(bot.owner_id())
        self.assertFalse(bot.is_owner(777))
        self.assertFalse(bot.is_owner(self.UID))

    def test_a_record_pointing_outside_the_list_is_ignored(self):
        # Запись, указывающая на человека вне списка админов, — не владелец:
        # иначе удалённый админ сохранял бы власть через оставшуюся строку.
        bot.ALLOWED_IDS.clear()
        bot.ALLOWED_IDS.update({777, self.UID})
        os.environ["OWNER_ID"] = "909090"   # заведомо вне списка (UID = 424242)
        self.assertIsNone(bot.owner_id())
        self.assertFalse(bot.is_owner(self.UID))
        # Порядок записи на это не влияет вовсе — раньше влиял, и в этом был дефект.
        del bot.ADMIN_ORDER[:]
        bot.ADMIN_ORDER.extend([self.UID])
        self.assertIsNone(bot.owner_id())

    def test_invited_admin_sees_no_delete_buttons_and_is_refused_by_hand(self):
        text, datas, _ = self._screen("se_admins")
        self.assertEqual([d for d in datas if d.startswith("se_admin_del~")], [])
        self.assertIn(i18n.t(self.UID, "admins_owner_rule"), text)
        self.assertIn(i18n.t(self.UID, "admins_owner_mark"), text)
        # callback_data приходит из чата: проверка не только в рисовании кнопок
        rec, _ = self._press("se_admin_del~777")
        self.assertTrue(any(c[2].get("show_alert") for c in rec.calls if c[0] == "answer"))
        self.assertIn(777, bot.ALLOWED_IDS)

    def test_owner_deletes_invited_but_never_himself(self):
        # Владельцем делает ЗАПИСЬ, а не перестановка порядка: порядок больше
        # ни на что не влияет, и это главная часть починки.
        os.environ["OWNER_ID"] = str(self.UID)
        text, datas, ctx = self._screen("se_admins")
        self.assertIn("se_admin_del~777", datas)
        rec, _ = self._press("se_admin_del~%d" % self.UID)
        self.assertTrue(any(c[2].get("show_alert") for c in rec.calls if c[0] == "answer"))
        self._press("se_admin_del_ok~777", ctx)
        self.assertNotIn(777, bot.ALLOWED_IDS)


class TemplateCatalogTests(_BotScreenCase):
    """Каталог шаблонов: категории и поиск вместо списка на тысячи строк."""

    CATALOG = {"categories": [
        {"id": "landing", "name": "Лендинги", "icon": "rocket", "templates": [
            {"id": "gh_owner_repo_page%02d" % i, "name": "Landing %02d" % i, "source": "github",
             "description": "Описание %d" % i, "repo_url": "https://github.com/owner/repo",
             "sparse_path": "page%02d" % i, "preview_url": "https://example.com/%02d" % i,
             "license": "MIT"} for i in range(11)]},
        {"id": "business", "name": "Бизнес", "icon": "briefcase", "templates": [
            {"id": "h5up_aerial", "name": "Aerial", "source": "html5up", "description": "Корпоративный",
             "repo_url": "https://github.com/zce/html5up", "sparse_path": "aerial",
             "preview_url": "https://html5up.net/aerial/", "license": "CC-BY-3.0"}]},
    ]}

    @classmethod
    def setUpClass(cls):
        _BotScreenCase.setUpClass()
        cls._catalog_path = os.path.join(TMP_ROOT, "templates_catalog.json")
        with open(cls._catalog_path, "w", encoding="utf-8") as fh:
            json.dump(cls.CATALOG, fh, ensure_ascii=False)
        cls._saved = (bot.CATALOG_FILE, bot.CATALOG_TOOL)
        # Помощник берём НАСТОЯЩИЙ (lib/catalog_tool.py): проверяем ту же связку,
        # что работает на сервере, а не свою выдумку.
        bot.CATALOG_FILE = cls._catalog_path
        bot.CATALOG_TOOL = os.path.join(ROOT, "lib", "catalog_tool.py")
        bot._CATALOG, bot._CATALOG_TRIED = None, False

    @classmethod
    def tearDownClass(cls):
        bot.CATALOG_FILE, bot.CATALOG_TOOL = cls._saved
        bot._CATALOG, bot._CATALOG_TRIED = None, False
        _BotScreenCase.tearDownClass()

    def test_categories_then_pages_instead_of_a_wall_of_templates(self):
        text, datas, ctx = self._screen("se_tpl")
        self.assertIn("se_tplc~landing~0", datas)
        self.assertIn("se_tplc~business~0", datas)
        self.assertIn("se_tplq", datas)
        self.assertIn("se_tplr", datas)
        # страница по восемь: 11 записей → две страницы, вперёд есть, назад нет
        text, datas, ctx = self._screen("se_tplc~landing~0", ctx)
        picks = [d for d in datas if d.startswith("se_tplp~")]
        self.assertEqual(len(picks), 8)
        self.assertIn("se_tplc~landing~1", datas)
        self.assertNotIn("se_tplc~landing~-1", datas)
        text, datas, ctx = self._screen("se_tplc~landing~1", ctx)
        self.assertEqual(len([d for d in datas if d.startswith("se_tplp~")]), 3)
        self.assertIn("se_tplc~landing~0", datas)
        # ключ кнопки короткий, а карта ключ→id живёт в user_data
        self.assertEqual(len(ctx.user_data["tpl_keys"]), 11)
        for key in ctx.user_data["tpl_keys"]:
            self.assertEqual(len(key), 12)

    def test_search_by_word_and_pick_asks_for_the_phrase(self):
        args_file = os.path.join(TMP_ROOT, "tpl_args.txt")
        os.environ["FAKE_BOT_ARGS_FILE"] = args_file
        try:
            rec, ctx = self._press("se_tplq")
            self.assertEqual(ctx.user_data["flow"]["kind"], "tpl_search")
            self._say("a", ctx)                                  # слишком коротко — ждём дальше
            self.assertIn("flow", ctx.user_data)
            rec = self._say("aerial", ctx)
            self.assertNotIn("flow", ctx.user_data)
            replies = [c for c in rec.calls if c[0] == "reply_text"]
            datas = [b.callback_data for row in replies[-1][2]["reply_markup"].inline_keyboard for b in row]
            picks = [d for d in datas if d.startswith("se_tplp~")]
            self.assertEqual(len(picks), 1)
            text, datas, ctx = self._screen(picks[0], ctx)
            self.assertIn("Aerial", text)
            self.assertIn("h5up_aerial", text)
            self.assertIn(i18n.t(self.UID, "tpl_replace_warn"), text)
            self.assertEqual(bot.CONFIRM.pending(self.UID)["phrase"], "TEMPLATE site")
            self._say("TEMPLATE site", ctx)
            self.assertIsNone(bot.CONFIRM.pending(self.UID))
        finally:
            os.environ.pop("FAKE_BOT_ARGS_FILE", None)
        with open(args_file, encoding="utf-8") as fh:
            self.assertIn("--action=change-template\t--json\t--template=h5up_aerial", fh.read())

    def test_random_template_goes_through_the_bridge(self):
        args_file = os.path.join(TMP_ROOT, "tpl_rnd_args.txt")
        os.environ["FAKE_BOT_ARGS_FILE"] = args_file
        try:
            rec, ctx = self._press("se_tplr")
            self.assertEqual(bot.CONFIRM.pending(self.UID)["phrase"], "TEMPLATE site")
            self._say("TEMPLATE site", ctx)
        finally:
            os.environ.pop("FAKE_BOT_ARGS_FILE", None)
        with open(args_file, encoding="utf-8") as fh:
            self.assertIn("--action=change-template\t--json\t--template=__random__", fh.read())

    def test_stale_key_says_so_instead_of_going_silent(self):
        rec, _ = self._press("se_tplp~0123456789ab")     # карты в свежем ctx нет
        alerts = [c for c in rec.calls if c[0] == "answer" and c[2].get("show_alert")]
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0][1][0], i18n.t(self.UID, "tpl_key_lost"))

    def test_missing_catalog_explains_itself(self):
        saved = bot.CATALOG_FILE
        bot.CATALOG_FILE = os.path.join(TMP_ROOT, "no-such-catalog.json")
        bot._CATALOG, bot._CATALOG_TRIED = None, False
        try:
            text, datas, _ = self._screen("se_tpl")
            self.assertIn(i18n.t(self.UID, "tpl_no_catalog"), text)
            self.assertIn("se_mode", datas)               # тупика нет
        finally:
            bot.CATALOG_FILE = saved
            bot._CATALOG, bot._CATALOG_TRIED = None, False


class ProductLanguageTests(_BotScreenCase):
    """Общий язык продукта меняется из бота, а не только по SSH."""

    def test_screen_offers_both_languages_of_both_kinds(self):
        datas = [b.callback_data for row in bot.lang_keyboard(self.UID, "sec_settings").inline_keyboard for b in row]
        for want in ("lang_set_ru", "lang_set_en", "lang_prod_ru", "lang_prod_en"):
            self.assertIn(want, datas)

    def test_product_language_goes_through_the_bridge(self):
        args_file = os.path.join(TMP_ROOT, "lang_args.txt")
        os.environ["FAKE_BOT_ARGS_FILE"] = args_file
        try:
            rec, _ = self._press("lang_prod_en")
            edits = [c for c in rec.calls if c[0] == "edit"]
            self.assertIn("✅", str(edits[-1][1][0]))
            rec, _ = self._press("lang_prod_klingon")
            self.assertTrue(any(c[2].get("show_alert") for c in rec.calls if c[0] == "answer"))
        finally:
            os.environ.pop("FAKE_BOT_ARGS_FILE", None)
        with open(args_file, encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn("--action=lang-set\t--json\t--lang=en", content)
        self.assertNotIn("--lang=klingon", content)

    def test_own_language_is_not_touched_by_the_product_one(self):
        before = i18n.get_user_lang(self.UID)
        self._press("lang_prod_en")
        self.assertEqual(i18n.get_user_lang(self.UID), before)

    def test_bot_default_follows_the_product_language(self):
        """BOT-11: .env от установщика держит GOAMNEZIA_LANG — язык на момент установки, и
        i18n ставил его выше config.json: смена языка «для всех» (из бота, SSH, панели)
        умолчание бота не меняла никогда. Теперь язык продукта первым, снимок — запасным."""
        cfg = os.path.join(os.environ["GOAMNEZIA_DIR"], "config.json")
        os.makedirs(os.path.dirname(cfg), exist_ok=True)
        before = open(cfg, encoding="utf-8").read() if os.path.exists(cfg) else None
        saved = os.environ.get("GOAMNEZIA_LANG")
        self.addCleanup(lambda: os.environ.pop("GOAMNEZIA_LANG", None) if saved is None
                        else os.environ.__setitem__("GOAMNEZIA_LANG", saved))

        def restore_cfg():
            if before is None:
                if os.path.exists(cfg):
                    os.unlink(cfg)
            else:
                with open(cfg, "w", encoding="utf-8") as fh:
                    fh.write(before)
        self.addCleanup(restore_cfg)
        with open(cfg, "w", encoding="utf-8") as fh:
            json.dump({"lang": "en"}, fh)
        os.environ["GOAMNEZIA_LANG"] = "ru"
        self.assertEqual(i18n.get_user_lang(None), "en", "снимок из .env не перекрывает язык продукта")
        with open(cfg, "w", encoding="utf-8") as fh:                 # смена из SSH — видна сразу
            json.dump({"lang": "ru"}, fh)
        self.assertEqual(i18n.get_user_lang(None), "ru")
        os.unlink(cfg)                                               # языка в config.json нет —
        os.environ["GOAMNEZIA_LANG"] = "en"                          # в силе переменная из .env
        self.assertEqual(i18n.get_user_lang(None), "en")
        with open(cfg, "w", encoding="utf-8") as fh:
            json.dump({}, fh)
        self.assertEqual(i18n.get_user_lang(None), "en")


class UserAddGuardSourceTests(unittest.TestCase):
    """1c.7 по исходнику — эта половина проверки идёт и без python-telegram-bot.

    Живой обход мастера со счётом линеек лежит в HandlerWalkTests и требует библиотеки;
    здесь держим то, что проверяется всегда: отказ стоит В САМОМ создании, а не только на
    кнопке «Готово», и параметр profiles уходит мосту при любом выборе.
    """

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(BOT_SRC, "bot.py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        cls.fn = next(f for f in ast.walk(tree)
                      if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
                      and f.name == "_user_add_finish")

    def test_refusal_lives_in_the_creation_step(self):
        texts = set(n.value for n in ast.walk(self.fn)
                    if isinstance(n, ast.Constant) and isinstance(n.value, str))
        self.assertIn("user_pick_one_profile", texts,
                      "страж на кнопке «Готово» мастер не закрывает: живая кнопка прошлого "
                      "прогона приходит прямо в создание")

    def test_profiles_parameter_is_always_sent(self):
        calls = [c for c in ast.walk(self.fn)
                 if isinstance(c, ast.Call) and getattr(c.func, "id", "") == "mutate"]
        self.assertEqual(len(calls), 1, "мост зовут ровно один раз")
        kw = next(k for k in calls[0].keywords if k.arg == "profiles")
        # `",".join(…) or None` — это «параметра нет вовсе», а у ядра это «во все линейки».
        self.assertNotIsInstance(kw.value, ast.BoolOp, ast.dump(kw.value))


class SilentButtonTests(unittest.TestCase):
    """Разбор критика, находка 7: девятнадцать кнопок молчали при плохом вводе.

    Проверяем механически по AST: у обработчика кнопки не может быть ветки,
    которая заканчивается голым `return`, ничего не показав человеку.
    """

    # Молчать можно нарочно: занято (экран уже нарисован), чужой без доступа, поток сообщений
    # от незнакомца (SEC-B3 — сверх нескольких ответов в минуту апдейт отбрасывается молча).
    ALLOWED_QUIET = ("_action_busy", "require_auth", "stranger_may_reply")

    def test_no_handler_returns_without_saying_anything(self):
        with open(os.path.join(BOT_SRC, "bot.py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        # show_key_addr_screen / show_key_addr_miss («Ключ на другой адрес») рисуют экран адресов
        # или ошибку в любой ветке, а miss ещё и отвечает алертом «адреса больше нет».
        # _lazy_check (Ленивый Pro) рисует «⏳ Проверяю…», а затем план или отказ моста — тоже в любой ветке.
        # short_show_error — show_error коротких ссылок: отказ словами без служебного кода (SL-UX-03).
        visible = {"safe_edit_message", "show_error", "show_bad_input", "reply_text", "render_main",
                   "show_user_card", "show_profile_card", "arm_typed_confirm", "dispatch_callback_data",
                   "reply_document", "reply_photo", "reply_media_group", "answer",
                   "show_key_addr_screen", "show_key_addr_miss", "_lazy_check", "short_show_error"}
        bad = []
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) or not fn.name.startswith("cb_"):
                continue
            for node in ast.walk(fn):
                if not isinstance(node, ast.If):
                    continue
                if not any(isinstance(s, ast.Return) and s.value is None for s in node.body):
                    continue
                shown = False
                quiet_ok = False
                for stmt in node.body:
                    for call in ast.walk(stmt):
                        if not isinstance(call, ast.Call):
                            continue
                        name = getattr(call.func, "id", None) or getattr(call.func, "attr", "")
                        if name in visible and not (name == "answer" and not call.args):
                            shown = True
                for call in ast.walk(node.test):
                    name = getattr(call.func, "id", "") if isinstance(call, ast.Call) else ""
                    if name in self.ALLOWED_QUIET:
                        quiet_ok = True
                if not shown and not quiet_ok:
                    bad.append("%s: строка %d" % (fn.name, node.lineno))
        self.assertEqual(bad, [], "молчащие ветки: %s" % ", ".join(bad))


class IconConsistencyTests(unittest.TestCase):
    """Разбор критика, находка 25: один смысл — один значок.

    Значки берём из словаря: подпись кнопки живёт там, и разнобой заводится
    тоже там. Проверка идёт по обоим языкам — иконка от языка не зависит.
    """

    # Понятие → ключи словаря, которые обязаны начинаться с ОДНОГО значка.
    CONCEPTS = {
        "выключить": ("ag_btn_disable", "ag_btn_transport_off", "user_disable_item",
                      "se_alt_svc_off", "se_mini_off"),
        "включить": ("ag_btn_enable", "ag_btn_transport_on", "user_enable_item",
                     "se_alt_svc_on", "se_mini_on"),
        # Перевыпуск разрушителен — старый ключ, адрес или параметры умирают сразу; кубик у него путал
        # с «наугад» (NC-23, критик-новичок на 0.5.0). Кубик — только у случайного выбора.
        "перевыпустить": ("ag_btn_rotate", "pr_regen", "se_mini_rotate", "user_peer_reissue_btn"),
        "наугад": ("ag_btn_port_random", "tpl_random_btn", "user_name_random", "user_name_again"),
        "перезапустить": ("mt_restart_all", "pr_restart"),
        "сверить": ("mt_import_btn_reconcile_fmt", "mt_import_reconcile_title_fmt", "se_site_check"),
        "удалить": ("ag_btn_delete", "pr_remove", "user_delete_item", "share_revoke_btn",
                    "share_revoke_one_fmt", "share_revoke_all", "pg_revoke_btn", "mr_remove_btn_fmt"),
        "подтвердить": ("btn_confirm", "btn_done", "btn_yes"),
        "ссылка-адрес": ("ag_btn_show", "user_share", "share_link_title", "share_list_btn",
                         "share_list_title", "pg_url_again_btn"),
    }
    # «Обновить экран» — только у одной кнопки: раньше 🔄 значил и обновление
    # экрана, и перезапуск контейнера, и сверку, причём первые два висели рядом.
    REFRESH_KEY = "btn_refresh"
    # Значки-двойники: на экране телефона их не различить (лупа с разным
    # наклоном, два красных запрещающих знака, квадрат и пауза), поэтому
    # каждой паре разрешён только один представитель. ♻️ и 🔄 сюда НЕ входят:
    # они и по цвету, и по форме разные, и разводят «перезапустить контейнер»
    # с «обновить экран» — это осознанное разделение, а не разнобой.
    LOOKALIKES = (("🔍", "🔎"), ("⏹", "⏸"), ("⛔", "🚫"))

    def setUp(self):
        self.langs = {}
        for name in ("ru", "en"):
            with open(os.path.join(BOT_SRC, "lang", "%s.json" % name), encoding="utf-8") as fh:
                self.langs[name] = json.load(fh)

    @staticmethod
    def _icon(value):
        first = (value or "").strip().split(" ")[0]
        return first

    def test_one_concept_one_icon(self):
        for lang, d in self.langs.items():
            for concept, keys in self.CONCEPTS.items():
                icons = {}
                for key in keys:
                    self.assertIn(key, d, "%s: нет ключа %s" % (lang, key))
                    icons.setdefault(self._icon(d[key]), []).append(key)
                self.assertEqual(len(icons), 1,
                                 "%s, «%s»: значков %d — %s" % (lang, concept, len(icons), icons))

    def test_lookalike_glyphs_do_not_coexist(self):
        for lang, d in self.langs.items():
            for pair in self.LOOKALIKES:
                used = {}
                for glyph in pair:
                    keys = sorted(k for k, v in d.items() if self._icon(v) == glyph)
                    if keys:
                        used[glyph] = keys
                self.assertLessEqual(len(used), 1,
                                     "%s: значки-двойники %s заняты одновременно — %s" % (lang, pair, used))

    def test_refresh_icon_belongs_to_refresh_only(self):
        for lang, d in self.langs.items():
            icon = self._icon(d[self.REFRESH_KEY])
            others = sorted(k for k, v in d.items()
                            if k != self.REFRESH_KEY and self._icon(v) == icon)
            self.assertEqual(others, [], "%s: значок «обновить экран» %s занят ещё и %s" % (lang, icon, others))

    def test_confirm_icon_is_not_a_button_next_to_delete(self):
        """✅ значит «подтвердить»: рядом с «Удалить» ему не место (карточка агента)."""
        if not bot.PTB_AVAILABLE:
            self.skipTest("python-telegram-bot не установлен")
        rec = {"id": "a-1a2b", "enabled": True, "state": "active"}
        for enabled in (True, False):
            markup = bot.agent_card_keyboard(None, dict(rec, enabled=enabled))
            for row in markup.inline_keyboard:
                labels = [b.text for b in row]
                if any(l.startswith("🗑") for l in labels):
                    self.assertEqual(len(row), 1, "«Удалить» стоит в одном ряду с %s" % labels)


class ErrorHandlerLogTests(unittest.TestCase):
    """Сбой обработчика логируется без объекта Update: str(update) в PTB печатает
    подпись к фото, а под фото QR страницы она содержит адрес-секрет."""

    SECRET = "FAKESECRETFAKESECRETFAKESECRETFAKESECRET012"

    def test_log_names_the_update_but_not_its_text(self):
        if not bot.PTB_AVAILABLE:
            self.skipTest("python-telegram-bot не установлен")
        secret = self.SECRET

        class ChattyUpdate(FakeUpdate):
            update_id = 4242

            def __str__(self):
                return "Update(callback_query=CallbackQuery(message=Message(caption='https://vpn.example.com/p-%s/')))" % secret

        upd = ChattyUpdate(424242, data="pg_rev~p-7f3a")
        ctx = FakeContext()
        ctx.error = RuntimeError("network is down")
        catcher = _LogCatcher()
        bot.logger.addHandler(catcher)
        try:
            run(bot.error_handler(upd, ctx))
        finally:
            bot.logger.removeHandler(catcher)
        self.assertNotIn(secret, catcher.text())
        self.assertIn("pg_rev~p-7f3a", catcher.text())
        self.assertIn("4242", catcher.text())
        self.assertEqual(upd.rec.count("answer"), 1)


class LogComponentTests(unittest.TestCase):
    """Разбор критика, находка 39: в логах были сырые идентификаторы."""

    def test_labels_are_human_and_only_installed_components_are_offered(self):
        for comp in ("awg2", "bot", "admin", "system", "nginx", "mcp"):
            label = bot.log_component_label(None, comp)
            self.assertNotEqual(label, comp, "компонент %s остался сырым идентификатором" % comp)
        self.assertEqual(bot.log_component_label(None, "zzz"), "zzz")
        status = run(bot.get_status(fresh=True))
        comps = bot.log_components_for(status)
        # Профилей нет вовсе: их контейнеры без логов (--log-driver none) — BOT-4, живая проверка на test1
        self.assertNotIn("awg2", comps)
        self.assertNotIn("awg1", comps)
        self.assertNotIn("nginx", comps)    # режим lite — сайта нет
        self.assertIn("bot", comps)


class FlowPrefixesCoverTheWizards(unittest.TestCase):
    """Кнопка, продолжающая мастер, обязана быть в FLOW_CALLBACK_PREFIXES.

    Иначе она мёртвая: маршрут сбрасывает мастер до того, как обработчик успеет
    что-то сделать, и человек получает «ожидание истекло» на единственной кнопке
    экрана. Так и было с ag_aips~ — белым списком адресов агента: из-за этого из
    бота нельзя было выдать доступ с умолчанием «любые адреса».
    """

    def test_every_wizard_route_is_listed(self):
        import re
        with open(os.path.join(ROOT, 'goamnezia-bot', 'bot.py'), encoding='utf-8') as fh:
            src = fh.read()
        m = re.search(r'FLOW_CALLBACK_PREFIXES = \((.*?)\)', src, re.S)
        self.assertIsNotNone(m, 'список префиксов не найден')
        prefixes = tuple(re.findall(r'"([^"]+)"', m.group(1)))
        missing = []
        for route, handler in re.findall(r'\("([a-z_]+~?)",\s*(cb_[a-z_]+)\)', src):
            body = re.search(r'async def %s\(.*?\n(?=async def |\Z)' % re.escape(handler), src, re.S)
            if body and 'flow_get' in body.group(0) and not route.startswith(prefixes):
                missing.append(route)
        self.assertEqual(missing, [], 'эти кнопки продолжают мастер, но сбрасывают его: %r' % (missing,))


class CallbackMutationOnceTests(_BotScreenCase):
    """Настоящий mutate/Lock, офлайн Telegram с отдачей управления на сетевой правке."""

    CASES = (("ag_rot_do~a-1a2b", "ag_rot~a-1a2b", "agent-rotate"),
             ("ag_hard_do~a-1a2b", "ag_hard~a-1a2b", "agent-harden"),
             ("u_shdo~anna~awg2~conf", "u_shf~anna~awg2", "share-create"))

    def _update(self, data):
        update = FakeUpdate(self.UID, data=data)
        update.callback_query.message.message_id = 123
        return update

    def test_double_click_and_late_duplicate_only_mutate_once(self):
        for data, screen, action in self.CASES:
            with self.subTest(action=action):
                async def scenario():
                    calls, ctx = [], FakeContext()
                    original_edit = bot.safe_edit_message

                    async def slow_edit(*args, **kwargs):
                        await asyncio.sleep(0)  # оба callback успевают пройти прежний _action_busy
                        return await original_edit(*args, **kwargs)

                    async def bridge(action_name, **kwargs):
                        if action_name == "agent-list":
                            return {"status": "success", "agents": [{"id": "a-1a2b", "mode": "static"}]}
                        calls.append(action_name)
                        await asyncio.sleep(0)
                        return {"status": "success", "id": "s-1a2b", "ttl": 300}

                    with patch.object(bot, "_BOT_ACTION_LOCK", asyncio.Lock()), \
                            patch.object(bot, "safe_edit_message", slow_edit), \
                            patch.object(bot, "run_bot_action", bridge):
                        await asyncio.gather(bot.handle_callback(self._update(data), ctx),
                                             bot.handle_callback(self._update(data), ctx))
                        await bot.handle_callback(self._update(data), ctx)
                        self.assertEqual(calls, [action], "двойной тап и поздний callback — одна операция")
                        # Намеренное повторение: заново открыть экран, потом подтвердить.
                        await bot.handle_callback(self._update(screen), ctx)
                        await bot.handle_callback(self._update(data), ctx)
                        self.assertEqual(calls, [action, action])
                run(scenario())

    def test_failure_before_bridge_allows_retry_but_lost_bridge_reply_does_not(self):
        async def scenario():
            data, _, action = self.CASES[0]
            ctx, calls = FakeContext(), []
            original_edit = bot.safe_edit_message
            fail_edit = True

            async def edit(*args, **kwargs):
                nonlocal fail_edit
                if fail_edit:
                    fail_edit = False
                    raise bot.TelegramError("offline test: edit failed before bridge")
                return await original_edit(*args, **kwargs)

            async def bridge(name, **kwargs):
                calls.append(name)
                return {"status": "error", "code": "timeout", "timeout_s": 1}

            with patch.object(bot, "_BOT_ACTION_LOCK", asyncio.Lock()), \
                    patch.object(bot, "safe_edit_message", edit), \
                    patch.object(bot, "run_bot_action", bridge):
                with self.assertRaises(bot.TelegramError):
                    await bot.handle_callback(self._update(data), ctx)
                self.assertEqual(calls, [])
                # Отказ общего замка тоже до моста: старая кнопка ещё имеет право на попытку.
                async with bot._BOT_ACTION_LOCK:
                    await bot.handle_callback(self._update(data), ctx)
                self.assertEqual(calls, [])
                await bot.handle_callback(self._update(data), ctx)
                await bot.handle_callback(self._update(data), ctx)
                self.assertEqual(calls, [action], "после timeout нельзя молча перевыпустить ключ ещё раз")
        run(scenario())


class CallbackAnsweredOnceTests(_BotScreenCase):
    """Telegram принимает ответ на нажатие ровно один раз (BOT-1).

    Второй answerCallbackQuery получает 400 «query ID is invalid». Обычный FakeQuery
    принимал сколько угодно ответов, и четыре перехода через dispatch_callback_data
    (отмена фразы, ссылка на файл при одном профиле, привязка и вкл/выкл доступа
    агента) проходили тесты, а в Telegram кончались «Внутренней ошибкой» без экрана.
    Здесь FakeQuery строгий, как настоящий.
    """

    def setUp(self):
        orig = FakeQuery.answer

        async def strict(query, text=None, show_alert=False, **kw):
            if getattr(query, "_answered", False):
                raise bot.BadRequest("Query is too old and response timeout expired or query id is invalid")
            query._answered = True
            await orig(query, text, show_alert=show_alert, **kw)

        FakeQuery.answer = strict
        self.addCleanup(setattr, FakeQuery, "answer", orig)

    def test_cancel_of_the_typed_phrase_returns_to_the_card(self):
        rec, ctx = self._press("u_del~anna")
        self.assertIsNotNone(bot.CONFIRM.pending(self.UID))
        text, datas, _ = self._screen("tc_cancel~u_view~anna", ctx)
        self.assertIsNone(bot.CONFIRM.pending(self.UID))
        self.assertIn("u_del~anna", datas)            # снова карточка, а не «внутренняя ошибка»

    def test_share_link_with_one_profile_opens_the_format_screen(self):
        os.environ["FAKE_BOT_BOB_ENABLED"] = "true"
        self.addCleanup(os.environ.pop, "FAKE_BOT_BOB_ENABLED", None)
        text, datas, _ = self._screen("u_share~bob")
        self.assertIn("u_shdo~bob~awg2~conf", datas)

    def test_share_link_for_a_disabled_user_says_so_first(self):
        """A3-5: выключенному ссылку не создаём — экран говорит это до выбора профиля и формата; отказ ядра
        user_disabled (путь «ключ на другой адрес», выключили посреди) — те же слова, без кода."""
        text, datas, _ = self._screen("u_share~bob")
        self.assertIn(i18n.t(self.UID, "share_user_disabled"), text)
        self.assertFalse(any(d.startswith(("u_shdo~", "u_shf~")) for d in datas), datas)
        self.assertIn("u_view~bob", datas)
        orig = bot.run_bot_action
        self.addCleanup(setattr, bot, "run_bot_action", orig)

        async def patched(action, *a, **kw):
            if action == "share-create":
                return {"status": "error", "code": "user_disabled", "message": "Пользователь bob выключен", "action": action}
            return await orig(action, *a, **kw)

        bot.run_bot_action = patched
        text, datas, _ = self._screen("u_shdo~bob~awg2~conf")
        self.assertIn(i18n.t(self.UID, "share_user_disabled"), text)
        self.assertNotIn("user_disabled", text)
        self.assertEqual(datas, ["u_view~bob"])

    def test_agent_pin_and_toggle_redraw_the_card(self):
        for data in ("ag_pin~a-1a2b~on", "ag_en~a-1a2b~off_do", "ag_en~a-3c4d~on_do"):
            text, datas, _ = self._screen(data)
            self.assertTrue(any(d.startswith("ag_rot~") or d.startswith("ag_del~") for d in datas), data)

    def test_no_route_answers_twice(self):
        skip = {"close_menu", "admin_cancel", "nt_later"}
        for data in sorted(bot.EXACT_HANDLERS):
            if data not in skip:
                self._press(data)
        for data in ("tc_cancel~u_view~anna", "tc_cancel~pr_card~awg2", "tc_cancel~se_mode", "tc_cancel~mt_upstream",
                     "tc_cancel~ag_view~a-1a2b", "tc_cancel~se_tpl", "tc_cancel~mt_restore", "u_share~bob",
                     "u_share~anna", "ag_pin~a-1a2b~off", "ag_en~a-1a2b~off_do", "sh_rev~s-4b1e~l",
                     # те же выборки, что в HandlerWalkTests.test_prefix_routes_render
                     "menu_users~1", "u_view~anna", "u_view~nobody", "u_quota~anna", "uq_set~anna~50",
                     "u_exp~anna", "ue_set~anna~30", "u_en~anna~off", "u_ren~anna", "u_addp~anna",
                     "u_addp_do~anna~awg1", "u_del~anna", "st_log~awg2", "st_log~bogus", "pr_card~awg2",
                     "pr_install~awg1", "pr_inst_do~awg1~def", "pr_inst_port~awg1", "pr_remove~awg2", "pr_port~awg2",
                     "pr_443~awg3", "pr_443_do~awg3", "pr_subnet~awg2", "pr_mtu~awg2", "pr_mtu_set~awg2~1376",
                     "pr_params~awg2", "pr_pedit~awg2", "pr_pkey~awg2~S1", "pr_preset~awg2", "pr_preset_do~awg2~quic",
                     "pr_regen~awg2", "pr_restart~awg2", "pr_restart_do~awg2", "pr_port_do~awg2~3456",
                     "mt_rs~0", "se_dns_set~cf", "se_admin_del~5", "lang_set_ru", "inst_awg2~def", "noop",
                     "totally_unknown", "ag_view~a-1a2b", "ag_show~a-1a2b", "ag_rot~a-1a2b", "ag_rot_do~a-1a2b",
                     "ag_del~a-1a2b", "ag_http~on", "ag_http~off", "ag_http~rnd", "ag_http~port",
                     "up_upd~awg2", "up_rb~awg3", "up_fix~awg2", "se_mini~rot_do", "se_mini~off_do",
                     "se_alt_set~on~do", "imp_adopt~amnezia-awg", "imp_adopt_do~amnezia-awg", "imp_rec~awg2",
                     "imp_rec_do~awg2", "se_mode_to~lite_site", "se_mode_nodom~lite_site", "u_reis~anna~awg2",
                     "se_mode_keep~pro",   # домена у заглушки в lite нет → переход обратно к вопросу через dispatch
                     "u_delp~anna", "u_delp_ask~anna~awg2", "u_shf~anna~awg2", "u_page~anna", "pg_list~anna",
                     "mr_fetch", "mr_app~amneziavpn",
                     "u_att~proba~awg2", "u_att~anna~awg2", "u_reis~proba~awg2", "u_share~olga", "u_share~proba",
                     "u_page~olga", "u_shdo~proba~awg2~conf", "u_key~proba~awg2~conf"):
            self._press(data)


def _html_balanced(text):
    """Теги открыты и закрыты по порядку, нет обрывка «<co» в конце — иначе Telegram
    отвечает «Can't parse entities», и экран не рисуется."""
    stack = []
    for m in re.finditer(r"<(/?)([a-z-]+)[^>]*>", text):
        if m.group(1):
            if not stack or stack[-1] != m.group(2):
                return False
            stack.pop()
        else:
            stack.append(m.group(2))
    return not stack and not re.search(r"<[^>]*$", text)


def _traffic_response(n, now=None):
    """Ответ traffic на n пользователей в трёх профилях; каждый пятый — онлайн."""
    now = now or int(dt.datetime.now(dt.timezone.utc).timestamp())
    users, profiles = [], {p: [] for p in ("awg2", "awg3", "awg1")}
    for i in range(n):
        name = "user%03d" % i + ("x" * 20 if i % 3 == 0 else "")
        peers = {}
        for p in profiles:
            key = "PK%s%03d" % (p, i) + "x" * 36
            peers[p] = {"pubkey": key, "ip": "10.8.2.%d" % (i % 250 + 1), "has_key": True}
            profiles[p].append({"pubkey": key, "latest_handshake_ts": (now - 10) if i % 5 == 0 else 0,
                                "rx": (i * 7919) % 10 ** 9, "tx": (i * 104729) % 10 ** 9})
        users.append({"name": name, "label": name, "enabled": True, "quota_bytes": 10 ** 11 if i % 2 else 0,
                      "expires": "", "traffic": {"rx": (i * 15485863) % 10 ** 10, "tx": 2},
                      "profiles": list(peers), "peers": peers})
    return {"status": "success", "profiles": [{"profile": p, "peers": v} for p, v in profiles.items()],
            "users": users, "today": {"rx": 1, "tx": 1}, "month": {"rx": 1, "tx": 1}}


class LongScreensTests(unittest.TestCase):
    """Экраны со списками не упираются в 4096 знаков и не рвут разметку (BOT-2)."""

    def test_traffic_with_hundreds_of_users_fits_and_keeps_the_totals(self):
        for n in (27, 41, 200, 500):
            text = bot.traffic_text(None, _traffic_response(n))
            self.assertLessEqual(len(text), bot.MESSAGE_SOFT_LIMIT, n)
            self.assertTrue(_html_balanced(text), n)
            # «онлайн сейчас» и итоги раньше не доходили до экрана вовсе
            self.assertIn(i18n.tf(None, "online_now_fmt", 3 * ((n + 4) // 5)), text, n)   # онлайн-пиров во всех профилях
            self.assertIn(i18n.tf(None, "st_traffic_users").split("%")[0], text, n)
            more = i18n.tf(None, "st_traffic_more_fmt", "X").split("X")[0]
            self.assertIn(more, text, n)

    def test_traffic_arrows_are_from_the_user_side_in_peer_rows_and_totals(self):
        """A2-12 / R-3: в одном сообщении ↓ — скачано пользователем (сервер отдал, tx), ↑ — отдано (rx): и в строках
        пиров, и в итогах «Сегодня / за месяц» — как в панели и SSH."""
        gib = 2 ** 30
        key = "PKanna" + "x" * 38
        r = {"status": "success",
             "profiles": [{"profile": "awg2", "peers": [{"pubkey": key, "latest_handshake_ts": 0, "rx": 1 * gib, "tx": 5 * gib}]}],
             "users": [{"name": "anna", "label": "anna", "enabled": True, "quota_bytes": 0, "expires": "",
                        "traffic": {"rx": 1 * gib, "tx": 5 * gib}, "profiles": ["awg2"],
                        "peers": {"awg2": {"pubkey": key, "ip": "10.8.2.2", "has_key": True}}}],
             "today": {"rx": 2 * gib, "tx": 7 * gib}, "month": {"rx": 3 * gib, "tx": 11 * gib}}
        text = bot.traffic_text(None, r)
        hb = lambda n: bot.human_bytes(n, None)  # noqa: E731
        row = [l for l in text.splitlines() if "<code>anna</code> ↓" in l]
        self.assertEqual(len(row), 1, text)
        self.assertIn("↓ %s ↑ %s" % (hb(5 * gib), hb(1 * gib)), row[0])
        self.assertIn(i18n.tf(None, "st_traffic_today_fmt", hb(7 * gib), hb(2 * gib), hb(11 * gib), hb(3 * gib)), text)

    def test_traffic_small_server_is_shown_whole(self):
        text = bot.traffic_text(None, _traffic_response(3))
        more = i18n.tf(None, "st_traffic_more_fmt", "X").split("X")[0]
        self.assertNotIn(more, text)
        for i in range(3):
            self.assertIn("user%03d" % i, text)

    def test_clip_lines_never_cuts_inside_a_tag(self):
        text = "\n".join(["<b>title</b>"] + ["  ○ <code>user%04d</code> ↓ 1.0 МБ ↑ 2.0 МБ" % i for i in range(400)])
        out = bot.clip_lines(text)
        self.assertLessEqual(bot.visible_len(out), bot.MESSAGE_SOFT_LIMIT)   # как считает Telegram
        self.assertTrue(_html_balanced(out))
        self.assertTrue(out.endswith("…"))
        # тег, открытый на одной строке и закрытый через несколько, не разрезается
        text = "<b>head</b>\n<pre>" + "\n".join("line %d &lt;x&gt;" % i for i in range(600)) + "</pre>\ntail"
        self.assertTrue(_html_balanced(bot.clip_lines(text)))
        # лог в <pre>, где «&lt;» раздувает сырую длину, но для Telegram он в пределе, — не режется
        log = "<b>log</b>\n<pre>" + "\n".join("&lt;&gt;&amp;" * 20 for _ in range(55)) + "</pre>"
        self.assertGreater(len(log), bot.MESSAGE_SOFT_LIMIT)
        self.assertLess(bot.visible_len(log), bot.MESSAGE_SOFT_LIMIT)
        self.assertEqual(bot.clip_lines(log), log)

    def test_import_and_upstream_screens_use_line_clipping(self):
        with open(os.path.join(BOT_SRC, "bot.py"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertNotRegex(src, r"\)\[:3900\]")
        many = {"status": "success", "containers": [
            {"container": "amnezia-awg%d" % i, "port": 30000 + i, "proto": "udp", "line": "1.5", "peers": 3,
             "managed": False, "adoptable": True, "suggested_profile": "awg1"} for i in range(200)]}
        out = bot.clip_lines(bot.import_scan_text(None, many))
        self.assertLessEqual(bot.visible_len(out), bot.MESSAGE_SOFT_LIMIT)
        self.assertTrue(_html_balanced(out))


class ModeTransitionTests(_BotScreenCase):
    """Смена режима как в SSH (_site_transition) и по замыслу моста (BOT-3, BOT-4, BOT-5).

    Домен спрашивается, только когда сайт поднимается из «Только VPN», — для обоих режимов
    с сайтом; между режимами с сайтом домен мосту не передаётся (иначе мост пересобирает
    сайт с certbot вместо смены записи); расхождение ярлыка режима и порта VPN видно в
    шапке и на экране режима, а после смены предлагается нужный перенос порта.
    """

    def _mode(self, mode):
        os.environ["FAKE_BOT_MODE"] = mode
        bot.invalidate_status()
        self.addCleanup(os.environ.pop, "FAKE_BOT_MODE", None)
        self.addCleanup(bot.invalidate_status)

    def _args(self, name):
        path = os.path.join(TMP_ROOT, name)
        if os.path.exists(path):
            os.unlink(path)
        os.environ["FAKE_BOT_ARGS_FILE"] = path
        self.addCleanup(os.environ.pop, "FAKE_BOT_ARGS_FILE", None)
        return path

    def _change_mode_argv(self, path):
        with open(path, encoding="utf-8") as fh:
            return [line for line in fh.read().splitlines() if "--action=change-mode" in line]

    def test_between_site_modes_the_domain_is_not_sent(self):
        args = self._args("mode_switch_args.txt")
        self._mode("pro")                                   # домен vpn.example.com в status
        rec, ctx = self._press("se_mode_to~lite_site")
        self.assertNotIn("flow", ctx.user_data)             # домен не спрашиваем: сайт остаётся
        self.assertEqual(bot.CONFIRM.pending(self.UID)["phrase"], "MODE lite_site")
        edits = [c for c in rec.calls if c[0] == "edit"]
        switch = i18n.tf(self.UID, "se_mode_warn_switch_fmt", "X", "Y").split("\n\n")[-1]
        self.assertIn(switch, str(edits[-1][1][0]))
        self._say("MODE lite_site", ctx)
        argv = self._change_mode_argv(args)
        self.assertEqual(len(argv), 1)
        self.assertIn("--mode=lite_site", argv[0])
        self.assertNotIn("--domain", argv[0], "с доменом мост пересобирает сайт вместо смены записи режима")

    def test_lite_to_vpn_and_site_asks_the_domain(self):
        args = self._args("mode_site_domain_args.txt")
        self._mode("lite")                                  # домена в status нет
        text, datas, ctx = self._screen("se_mode_to~lite_site")
        self.assertEqual(ctx.user_data["flow"]["kind"], "mode_domain")
        self.assertIn(i18n.t(self.UID, "se_mode_ask_domain"), text)
        self.assertIn("se_mode_nodom~lite_site", datas)
        self._say("VPN.Example.COM", ctx)
        self.assertEqual(bot.CONFIRM.pending(self.UID)["phrase"], "MODE lite_site")
        self._say("MODE lite_site", ctx)
        argv = self._change_mode_argv(args)
        self.assertTrue(argv and "--mode=lite_site\t--domain=vpn.example.com" in argv[-1], argv)

    def test_site_without_domain_can_get_one_from_the_bot(self):
        """«VPN и сайт» на запасном самоподписанном (домена нет) → «Сайт и VPN на 443»: у бота нет
        пункта «Домен», и вопрос о домене здесь — единственный способ подключить его из чата.
        «Без домена» — простая смена записи режима, без --domain."""
        args = self._args("mode_site_nodomain_switch_args.txt")
        self._mode("lite_site")                             # у заглушки lite_site — без домена
        text, datas, ctx = self._screen("se_mode_to~pro")   # линий две — сначала какая на UDP 443 (F-3)
        text, datas, ctx = self._screen("se_mode_pl~awg2", ctx)
        self.assertEqual(ctx.user_data["flow"]["kind"], "mode_domain")
        self.assertFalse([d for d in datas if d.startswith("se_mode_keep~")])   # оставлять нечего
        text, datas, ctx = self._screen("se_mode_nodom~pro~awg2", ctx)
        # 2.0 уже на 443: сайт, домен, сертификат и панель как есть, порты тоже
        switch = i18n.tf(self.UID, "se_mode_warn_pro_switch_fmt", "X", "Y").split("\n\n")[-1]
        self.assertIn(switch, text)
        self.assertIn(i18n.tf(self.UID, "pl_switch_same_fmt", "AmneziaWG 2.0"), text)
        self._say("MODE pro", ctx)
        text, datas, ctx = self._screen("se_mode_to~pro")
        text, datas, ctx = self._screen("se_mode_pl~awg2", ctx)
        self._say("vpn.example.com", ctx)
        self._say("MODE pro", ctx)
        argv = self._change_mode_argv(args)
        self.assertEqual(len(argv), 2, argv)
        self.assertNotIn("--domain", argv[0])
        self.assertIn("--mode=pro\t--domain=vpn.example.com", argv[1])

    def test_domain_left_in_the_config_is_offered_not_forced(self):
        """Из «Только VPN» домен, оставшийся в конфиге, раньше уходил молча — сменить его из бота
        было нечем. Теперь он кнопкой «Оставить», а новый можно прислать текстом (как в SSH)."""
        args = self._args("mode_keep_args.txt")
        saved = bot.get_status

        async def fake(*_a, **_k):
            return {"status": "success", "mode": "lite", "domain": "old.example.com", "server_ip": "203.0.113.10",
                    "users": 0, "site": None, "profiles": []}
        bot.get_status = fake
        self.addCleanup(setattr, bot, "get_status", saved)
        text, datas, ctx = self._screen("se_mode_to~lite_site")
        self.assertIn("se_mode_keep~lite_site", datas)
        # «Без домена» при сохранённом домене не предлагается: мост без --domain берёт домен
        # из конфига, и кнопка поднимала сайт на нём же, с certbot (SR-55)
        self.assertNotIn("se_mode_nodom~lite_site", datas)
        self._say("new.example.com", ctx)                   # свой домен текстом
        self._say("MODE lite_site", ctx)
        rec, ctx = self._press("se_mode_keep~lite_site")    # или прежний — кнопкой
        self._say("MODE lite_site", ctx)
        argv = self._change_mode_argv(args)
        self.assertTrue("--domain=new.example.com" in argv[0], argv)
        self.assertTrue("--domain=old.example.com" in argv[1], argv)
        rec, _ = self._press("se_mode_keep~lite")           # «оставить домен» бывает только у режимов с сайтом
        self.assertTrue(any(c[2].get("show_alert") for c in rec.calls if c[0] == "answer"))

    def test_no_domain_button_brings_the_site_up_self_signed(self):
        args = self._args("mode_site_nodomain_args.txt")
        self._mode("lite")
        text, datas, ctx = self._screen("se_mode_to~lite_site")
        text, datas, ctx = self._screen("se_mode_nodom~lite_site", ctx)
        self.assertEqual(bot.CONFIRM.pending(self.UID)["phrase"], "MODE lite_site")
        self._say("MODE lite_site", ctx)
        argv = self._change_mode_argv(args)
        self.assertTrue(argv and "--mode=lite_site" in argv[-1] and "--domain" not in argv[-1], argv)
        rec, _ = self._press("se_mode_nodom~lite")          # «без домена» бывает только у режимов с сайтом
        self.assertTrue(any(c[2].get("show_alert") for c in rec.calls if c[0] == "answer"))

    def test_warning_says_new_keys_may_change_address(self):
        """SR-43: домен в новых ключах — только в «Сайт и VPN на 443». Переход в него или из него
        меняет адрес в новых ключах, и «ключи не меняются» в подтверждении было правдой лишь
        для уже выданных."""
        note = i18n.t(self.UID, "se_mode_warn_keys_host")
        self.assertTrue(note and note != "se_mode_warn_keys_host")
        self.assertIn(note, bot.mode_change_warn(self.UID, "lite_site", "pro", ""))
        self.assertIn(note, bot.mode_change_warn(self.UID, "pro", "lite_site", ""))
        self.assertIn(note, bot.mode_change_warn(self.UID, "lite", "pro", ""))
        self.assertNotIn(note, bot.mode_change_warn(self.UID, "lite_site", "lite", "vpn.example.com"))
        self.assertNotIn(note, bot.mode_change_warn(self.UID, "lite", "lite_site", ""))

    def test_to_lite_does_not_advise_checking_the_site(self):
        self._args("mode_lite_hint_args.txt")
        self._mode("pro")
        rec, ctx = self._press("se_mode_to~lite")
        edits = [c for c in rec.calls if c[0] == "edit"]
        off = i18n.tf(self.UID, "se_mode_warn_off_fmt", "X", "Y").split("\n\n")[-1]
        self.assertIn(off, str(edits[-1][1][0]))
        rec = self._say("MODE lite", ctx)
        texts = " ".join(str(c[1][0]) for c in rec.calls if c[0] == "reply_text")
        self.assertNotIn(i18n.t(self.UID, "se_mode_done_hint"), texts)   # «Проверить сайт» в lite нет

    def test_dashboard_says_when_mode_and_port_disagree(self):
        st = {"status": "success", "mode": "lite_site", "server_ip": "203.0.113.10", "users": 1,
              "mode_port_mismatch": True, "profiles": []}
        self.assertIn(i18n.t(self.UID, "dash_mode_port_mismatch"), bot.dashboard_text(self.UID, st))
        st["mode_port_mismatch"] = False
        self.assertNotIn(i18n.t(self.UID, "dash_mode_port_mismatch"), bot.dashboard_text(self.UID, st))

    def test_vpn_and_site_with_a_profile_on_443_offers_to_move_it(self):
        self._mode("pro")                                   # у заглушки awg2 держит UDP/443
        rec, ctx = self._press("se_mode_to~lite_site")
        rec = self._say("MODE lite_site", ctx)
        replies = [c for c in rec.calls if c[0] == "reply_text"]
        hint = i18n.tf(self.UID, "se_mode_port_hint_site_fmt", "AmneziaWG 2.0")
        self.assertIn(hint, " ".join(str(c[1][0]) for c in replies))
        markup = [c[2].get("reply_markup") for c in replies if c[2].get("reply_markup")][-1]
        datas = [b.callback_data for row in markup.inline_keyboard for b in row]
        self.assertIn("pr_port~awg2", datas)
        # экран режима говорит о том же и даёт ту же кнопку, пока расхождение не снято
        saved = bot.get_status

        async def fake(*_a, **_k):
            return {"status": "success", "mode": "lite_site", "domain": "vpn.example.com", "users": 0,
                    "server_ip": "203.0.113.10", "site": None, "mode_port_mismatch": True,
                    "profiles": [{"profile": "awg2", "state": "running", "port": 443, "line": "2.0"}]}
        bot.get_status = fake
        self.addCleanup(setattr, bot, "get_status", saved)
        text, datas, _ = self._screen("se_mode")
        self.assertIn(hint, text)
        self.assertIn("pr_port~awg2", datas)


class BulkAddTextTests(unittest.TestCase):
    """«Добавить сразу несколько» (users-add-bulk): число текстом и итог одним сообщением — без PTB."""

    def test_count_is_a_whole_number_from_1_to_100(self):
        for good, n in (("1", 1), (" 12 ", 12), ("100", 100), ("007", 7)):
            self.assertEqual(bot.parse_bulk_count(good), n, good)
        for bad in ("0", "101", "", "12.5", "-3", "десять", "1e2", "1000"):
            self.assertIsNone(bot.parse_bulk_count(bad), bad)

    def test_a_hundred_fits_one_message_and_names_the_rest(self):
        added = [{"name": "very_long_capital_name_%03d" % i, "label": "Очень длинная подпись человека № %03d" % i}
                 for i in range(100)]
        r = {"status": "success", "requested": 100, "count": 100, "added": added, "partial": False, "failed": None}
        text = bot.bulk_done_text(None, r, ["awg3", "awg2"])
        self.assertLessEqual(len(text), bot.MESSAGE_SOFT_LIMIT)
        self.assertTrue(_html_balanced(text))
        self.assertIn(i18n.tf(None, "user_bulk_done_fmt", 100, FX31 + ", " + FX20), text)
        shown = text.count("<code>very_long_capital_name_")
        self.assertGreater(shown, 10)
        self.assertIn(i18n.tf(None, "user_bulk_more_fmt", 100 - shown), text)
        self.assertIn(i18n.t(None, "user_bulk_where_keys"), text)

    def test_partial_says_where_it_stopped_and_escapes_words(self):
        r = {"status": "success", "requested": 4, "count": 1, "added": [{"name": "a-01", "label": "a-01"}],
             "partial": True, "failed": {"name": "a-02", "code": "keys_failed", "message": "<b>сбой</b>"}}
        text = bot.bulk_done_text(None, r, ["awg2"])
        self.assertIn("• <code>a-01</code>", text)                      # подпись = имя — второй раз не пишем
        self.assertIn(i18n.tf(None, "user_bulk_partial_fmt", 3, "a-02", "&lt;b&gt;сбой&lt;/b&gt;"), text)
        self.assertTrue(_html_balanced(text))


class CoreBulkCrossTests(unittest.TestCase):
    """Ответ НАСТОЯЩЕГО ядра на users-add-bulk (install.sh в сухом прогоне) бот разбирает так, как ждёт итог:
    added [{name, label}], count, partial — заглушка моста выше отвечает так же, здесь — само ядро."""

    def test_the_bot_reads_the_core_answer(self):
        bash = _bash5()
        if bash is None:
            self.skipTest("нужен bash 4+ (задайте BASH5)")
        if not shutil.which("jq"):
            self.skipTest("нет jq — ядро без него не отвечает")
        tmp = tempfile.mkdtemp(prefix="core-bulk-", dir=TMP_ROOT)
        gdir = os.path.join(tmp, "goamnezia")
        os.makedirs(gdir)
        with open(os.path.join(gdir, "config.json"), "w", encoding="utf-8") as fh:
            json.dump({"schema": 1, "installed": True, "mode": "lite", "lang": "ru", "server_ip": "203.0.113.10",
                       "client_dns": ["1.1.1.1", "1.0.0.1"], "profiles": {}}, fh)
        upstream = os.path.join(tmp, "upstream")
        shutil.copytree(os.path.join(ROOT, "lib", "upstream"), upstream)
        env = dict(os.environ, GOAMNEZIA_DRY_RUN="1", GOAMNEZIA_DIR=gdir, GOAMNEZIA_LOCK_DIR=os.path.join(tmp, "run"),
                   GOAMNEZIA_LOG_FILE=os.path.join(tmp, "log"), GOAMNEZIA_AUDIT_LOG=os.path.join(tmp, "audit"),
                   GOAMNEZIA_SERVER_IP="203.0.113.10", BOT_DIR=os.path.join(tmp, "bot"), ADMIN_DIR=os.path.join(tmp, "admin"),
                   GOAMNEZIA_BUILD_ROOT=os.path.join(tmp, "build"), UPSTREAM_DIR=upstream,
                   GOAMNEZIA_DOCKER_SOCK=os.path.join(tmp, "nosuch.sock"), GOAMNEZIA_SECRET_TMPDIR=tmp,
                   GOAMNEZIA_CALL_LANG="en")

        def core(*args):
            p = subprocess.run([bash, os.path.join(ROOT, "install.sh")] + list(args) + ["--json"], env=env,
                               capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
            return bot.parse_bridge_output(p.stdout)

        self.assertTrue(bot.bridge_ok(core("--action=profile-install", "--profile=awg2", "--port=40002")))
        r = core("--action=users-add-bulk", "--count=3", "--profiles=awg2", "--lang=en")
        self.assertTrue(bot.bridge_ok(r), r)
        self.assertEqual((r.get("count"), r.get("requested"), r.get("partial")), (3, 3, False))
        text = bot.bulk_done_text(None, r, ["awg2"])
        for a in r["added"]:
            self.assertIn("<code>%s</code>" % a["name"], text)
            self.assertIn(a["label"], text)
        self.assertNotIn("privkey", json.dumps(r))
        # «Удалить сразу несколько» тем же ядром: предпросмотр по префиксу, удаление с числом — итог бота
        r = core("--action=users-add-bulk", "--count=3", "--profiles=awg2", "--prefix=team")
        self.assertTrue(bot.bridge_ok(r), r)
        p = core("--action=users-del-bulk-preview", "--prefix=team")
        self.assertTrue(bot.bridge_ok(p), p)
        self.assertEqual((p.get("count"), [s["name"] for s in p["selected"]]), (3, ["team-01", "team-02", "team-03"]))
        r = core("--action=users-del-bulk", "--prefix=team", "--confirm=2")
        self.assertEqual((r.get("code"), r.get("matched")), ("confirm_mismatch", 3))
        r = core("--action=users-del-bulk", "--prefix=team", "--confirm=3")
        self.assertTrue(bot.bridge_ok(r), r)
        text = bot.bulk_del_done_text(None, r)
        self.assertIn(i18n.tf(None, "user_bulk_del_done_fmt", 3), text)
        for name in ("team-01", "team-02", "team-03"):
            self.assertIn("<code>%s</code>" % name, text)
        self.assertNotIn("privkey", json.dumps(r))


class BulkDeleteTextTests(unittest.TestCase):
    """«Удалить сразу несколько» (users-del-bulk): фраза, выбор и итог одним сообщением — без PTB."""

    def test_phrase_and_selection(self):
        self.assertEqual(bot.confirm_phrase("users-del-bulk", "12"), "DELETE 12")
        for mode, value, ok in (("state", "disabled", True), ("state", "quota", True), ("state", "bogus", False),
                                ("prefix", "office", True), ("prefix", "bad prefix", False), ("names", "a,b", False)):
            self.assertEqual(bot.bulk_del_selection_ok(mode, value), ok, (mode, value))
        self.assertEqual(bot.bulk_del_what(None, "prefix", "office"), i18n.tf(None, "user_bulk_del_what_prefix_fmt", "office"))

    def test_a_hundred_fits_one_message_and_names_the_rest(self):
        deleted = ["very_long_capital_name_%03d" % i for i in range(100)]
        r = {"status": "success", "requested": 100, "count": 100, "deleted": deleted, "kept": [], "partial": False, "failed": None}
        text = bot.bulk_del_done_text(None, r)
        self.assertLessEqual(len(text), bot.MESSAGE_SOFT_LIMIT)
        self.assertTrue(_html_balanced(text))
        shown = text.count("<code>very_long_capital_name_")
        self.assertGreater(shown, 10)
        self.assertIn(i18n.tf(None, "user_bulk_more_fmt", 100 - shown), text)

    def test_partial_names_where_it_stopped_and_who_was_kept(self):
        r = {"status": "success", "requested": 12, "count": 10, "deleted": ["a-%02d" % i for i in range(1, 11)],
             "kept": ["a-11", "a-12"], "partial": True,
             "failed": {"name": "a-11", "code": "user_del_failed", "message": "<b>контейнер</b> не принял"}}
        text = bot.bulk_del_done_text(None, r)
        self.assertIn(i18n.tf(None, "user_bulk_del_partial_fmt", 2, "a-11", "&lt;b&gt;контейнер&lt;/b&gt; не принял"), text)
        self.assertIn(i18n.tf(None, "user_bulk_del_kept_fmt", 2), text)
        self.assertTrue(_html_balanced(text))


class BridgeParamNamesTests(unittest.TestCase):
    """Имена параметров в вызовах моста — те, что знает lib/actions.json.

    Проверка имён действий была и раньше; опечатка в имени параметра (`--profiel=`)
    доходила до моста и ловилась только его отказом bad_arg уже у человека на экране.
    """

    def test_every_kwarg_is_a_known_param(self):
        with open(ACTIONS_JSON, encoding="utf-8") as fh:
            actions = json.load(fh)["actions"]
        with open(os.path.join(BOT_SRC, "bot.py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        # Действие договора, которого ядро этой ветки ещё не знает, сверять не с чем: как только
        # оно появится в каталоге, его параметры проверяются наравне с остальными.
        pending = (BridgeParseTests.CONTRACT_PENDING_READ | BridgeParseTests.CONTRACT_PENDING_WRITE) - set(actions)
        bad = []
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in ("run_bot_action", "mutate", "_callback_mutate")):
                continue
            action_index = 1 if node.func.id == "_callback_mutate" else 0
            if len(node.args) <= action_index or not isinstance(node.args[action_index], ast.Constant):
                continue
            action = node.args[action_index].value
            if action in pending:
                continue
            params = (actions.get(action) or {}).get("params") or {}
            for kw in node.keywords:
                if kw.arg in (None, "timeout", "who"):
                    continue
                if kw.arg == "who_arg":
                    # --who= моста (короткие ссылки): параметр who обязан быть в каталоге
                    if "who" not in params:
                        bad.append("%d: %s --who" % (node.lineno, action))
                    continue
                if kw.arg == "stdin":
                    # Канал для секрета (ключ человека у user-attach-key): годится, только если каталог
                    # объявил у действия параметр со "stdin": true — иначе мост stdin не читает вовсе.
                    if not any(isinstance(p, dict) and p.get("stdin") for p in params.values()):
                        bad.append("%d: %s stdin" % (node.lineno, action))
                    continue
                if kw.arg not in params and kw.arg.replace("_", "-") not in params:
                    bad.append("%d: %s --%s" % (node.lineno, action, kw.arg))
        self.assertEqual(bad, [])


class ParamsScreenTests(unittest.TestCase):
    """BOT-15: мост отдаёт параметры всех линеек; у 1.5 чужие пустые ключи не показываем."""

    def test_line_1_5_does_not_list_empty_3_1_keys(self):
        # Форма ответа profile-params для awg1 (1.5) с живого сервера: ключи 3.1 и S3/S4 — пустые
        params = {"Jc": "5", "Jmin": "10", "Jmax": "50", "S1": "149", "S2": "93", "S3": "", "S4": "",
                  "H1": "958063019", "H2": "1357005604", "H3": "1071292951", "H4": "1613382609",
                  "I1": "<r 2><b 0x8580>", "I2": "", "I3": "", "I4": "", "I5": "",
                  "HeaderProtectionKey": "", "ContentPaddingAddition": "", "RekeyAfterTime": "",
                  "RekeyTimeout": "", "RejectAfterTime": "", "KeepaliveTimeout": "",
                  "MaxHandshakeAttempts": "", "RandomTrailers": "", "DisableCookies": "",
                  "PersistentKeepalive": "25", "MTU": "1280"}
        text = bot.params_text(None, "awg1", params, "1.5")
        for foreign in ("S3", "S4", "HeaderProtectionKey", "RandomTrailers", "ContentPaddingAddition"):
            self.assertNotIn("<code>%s</code>" % foreign, text)
        self.assertIn("<code>I1</code> = <code>&lt;r 2&gt;&lt;b 0x8580&gt;</code>", text)
        # заданный «чужой» ключ всё же виден: о нём человек должен знать
        params["S3"] = "20"
        self.assertIn("<code>S3</code> = <code>20</code>", bot.params_text(None, "awg1", params, "1.5"))

    def test_secret_param_is_hidden(self):
        """K5-3: HeaderProtectionKey на экране — «задан (скрыт)», а не значение (экраны попадают в записи и скриншоты);
        пустой — «—», другие значения — как есть. «Сейчас:» в окне правки — тем же param_shown."""
        hpk = "SFBLdGVzdE9ubHlOb3RBUmVhbEtleUZvckJvdDAxMjM="
        params = {"Jc": "4", "S1": "12", "HeaderProtectionKey": hpk, "I2": ""}
        text = bot.params_text(None, "awg3", params, "3.1")
        self.assertIn("<code>HeaderProtectionKey</code> = <code>задан (скрыт)</code>", text)
        self.assertNotIn(hpk[:8], text)
        self.assertIn("<code>Jc</code> = <code>4</code>", text)
        self.assertIn("<code>I2</code> = <code>—</code>", text)
        # Заданный «чужой» ключ (у 2.0 HeaderProtectionKey нет) показывается отдельной строкой — тоже скрыт.
        text = bot.params_text(None, "awg2", params, "2.0")
        self.assertIn("<code>HeaderProtectionKey</code> = <code>задан (скрыт)</code>", text)
        self.assertNotIn(hpk[:8], text)
        self.assertIn("<code>HeaderProtectionKey</code> = <code>—</code>",
                      bot.params_text(None, "awg3", dict(params, HeaderProtectionKey=""), "3.1"))
        self.assertEqual(bot.param_shown(None, "HeaderProtectionKey", hpk), "задан (скрыт)")
        self.assertEqual(bot.param_shown(None, "HeaderProtectionKey", ""), "—")
        self.assertEqual(bot.param_shown(None, "Jc", "4"), "4")
        with open(os.path.join(BOT_SRC, "lang", "en.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["pr_param_secret_set"], "set (hidden)")

    def test_secret_param_confirm_is_hidden(self):
        """KEENETIC-I1-N N4: подтверждение нового HeaderProtectionKey («Записать … = …?») печатало введённый ключ
        целиком. Теперь «задан (скрыт)»; в flow — ключ целиком (его записывает pr_pok). Без PTB: клавиатура — заглушка."""
        hpk = "TjMtdGVzdC1vbmx5LW5vdC1hLXJlYWwta2V5LTAwMDE="
        said = []

        class Target:
            async def reply_text(self, text, **kw):
                said.append(text)

        flow = {"data": {"profile": "awg3", "key": "HeaderProtectionKey"}}
        with patch.object(bot, "InlineKeyboardMarkup", lambda rows: rows, create=True), \
                patch.object(bot, "InlineKeyboardButton", lambda text, callback_data=None: (text, callback_data),
                             create=True):
            run(bot._param_confirm_or_apply(Target(), None, None, flow, hpk, edit=False))
        self.assertEqual(len(said), 1)
        self.assertIn("HeaderProtectionKey = задан (скрыт)", said[0].replace("<code>", "").replace("</code>", ""))
        self.assertNotIn(hpk[:8], said[0])
        self.assertEqual((flow["data"]["value"], flow["data"]["confirm"]), (hpk, True))


class DiagnosticsParityTests(unittest.TestCase):
    """BOT-16: диагностика бота показывает то же, что SSH: ключи сервера, таймеры, мало места."""

    def _quality(self, **over):
        q = {"docker": True, "ip_forward": "1", "network": True, "disk_free_mb": 26118, "ports": [],
             "server_keys": [{"profile": "awg2", "ok": True}, {"profile": "awg3", "ok": True}],
             "timers": [{"name": "quota", "state": "active", "last": "success"},
                        {"name": "backup", "state": "active", "last": "success"}]}
        q.update(over)
        return {"status": "success", "quality": q}

    def test_all_good_is_said_once(self):
        text = bot.diag_text(None, self._quality())
        # Профиль — линией, а не служебным id (BOT-3, живая проверка на test1)
        self.assertIn(i18n.tf(None, "diag_keys_ok_fmt", FX20), text)
        self.assertIn(i18n.t(None, "diag_timers_ok"), text)
        self.assertNotIn("⚠️", text)

    def test_problems_are_named(self):
        text = bot.diag_text(None, self._quality(
            disk_free_mb=512,
            server_keys=[{"profile": "awg2", "ok": False}],
            timers=[{"name": "quota", "state": "inactive", "last": ""},
                    {"name": "watch", "state": "missing", "last": ""},
                    {"name": "stats", "state": "active", "last": "failed"}]))
        self.assertIn("❌ " + i18n.tf(None, "diag_keys_bad_fmt", FX20), text)
        self.assertIn(i18n.tf(None, "diag_disk_low_fmt", 512), text)
        self.assertIn(i18n.tf(None, "diag_timer_off_fmt", "quota"), text)
        self.assertIn(i18n.tf(None, "diag_timer_missing_fmt", "watch"), text)
        self.assertIn(i18n.tf(None, "diag_timer_failed_fmt", "stats", "failed"), text)
        self.assertNotIn(i18n.t(None, "diag_timers_ok"), text)

    def test_old_bridge_without_the_fields_still_renders(self):
        text = bot.diag_text(None, {"status": "success", "quality": {"docker": True, "ip_forward": "1"}})
        self.assertIn("Docker", text)
        self.assertNotIn(i18n.t(None, "diag_timers_ok"), text)


class TaggedTextNeedsHtmlTests(unittest.TestCase):
    """BOT-13: строка словаря с <code>/<b> уходит только с parse_mode=HTML — иначе человек
    видит теги буквально (так было у ag_bad_ips и se_mode_bad_domain)."""

    def test_no_tagged_string_is_sent_as_plain_text(self):
        with open(os.path.join(BOT_SRC, "lang", "ru.json"), encoding="utf-8") as fh:
            ru = json.load(fh)
        with open(os.path.join(BOT_SRC, "bot.py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        bad = []
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("reply_text", "edit_text", "send_message")):
                continue
            if any(k.arg == "parse_mode" for k in node.keywords):
                continue
            for arg in list(node.args) + [k.value for k in node.keywords if k.arg in ("text", "caption")]:
                for sub in ast.walk(arg):
                    if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) and sub.func.id in ("_t", "_tf") \
                            and len(sub.args) > 1 and isinstance(sub.args[1], ast.Constant) \
                            and re.search(r"</?(b|i|code|pre|a)\b", str(ru.get(sub.args[1].value, ""))):
                        bad.append("%d:%s" % (node.lineno, sub.args[1].value))
        self.assertEqual(bad, [])


class StatusCacheRaceTests(unittest.TestCase):
    """BOT-12: status, начатый до мутации и вернувшийся после неё, не ложится в кэш."""

    def test_stale_status_is_not_cached_after_a_mutation(self):
        saved = bot.run_bot_action
        self.addCleanup(setattr, bot, "run_bot_action", saved)
        self.addCleanup(bot.invalidate_status)

        async def scenario():
            gate = asyncio.Event()

            async def slow_status(action, *a, **kw):
                if action == "status":
                    await gate.wait()
                    return {"status": "success", "mode": "lite", "profiles": [], "old": True}
                return {"status": "success"}
            bot.run_bot_action = slow_status
            bot.invalidate_status()
            reader = asyncio.ensure_future(bot.get_status())     # дашборд начал читать status
            await asyncio.sleep(0)
            await bot.mutate("profile-install", profile="awg1")  # мутация закончилась раньше
            gate.set()
            old = await reader
            self.assertTrue(old.get("old"))
            self.assertIsNone(bot._STATUS_CACHE["data"], "снимок до мутации лёг в кэш на 15 секунд")
            reader = asyncio.ensure_future(bot.get_status())     # обычный вызов кэшируется как раньше
            await asyncio.sleep(0)
            await reader
            self.assertIsNotNone(bot._STATUS_CACHE["data"])
        run(scenario())


class _FakeTgBot:
    """context.bot: записывает, какие кнопки меню чата ставит бот."""

    def __init__(self):
        self.menu = []

    async def set_chat_menu_button(self, chat_id=None, menu_button=None):
        self.menu.append((chat_id, type(menu_button).__name__))


class BotSmallFixesTests(_BotScreenCase):
    """BOT-6…BOT-9: подсказка панели без команд CLI, кнопка меню после смены панели,
    порт без ведущих нулей, /key у импортированного пира."""

    def _patch_action(self, action, answer):
        saved = bot.run_bot_action

        async def fake(_action, *a, **kw):
            if _action == action:
                return dict(answer)
            return await saved(_action, *a, **kw)
        bot.run_bot_action = fake
        self.addCleanup(setattr, bot, "run_bot_action", saved)

    def test_panel_screen_does_not_tell_to_run_cli_commands(self):
        cli = "Сайта ещё нет: сначала change-mode --mode=lite_site или --mode=pro --domain=…"
        self._patch_action("miniapp-status", {"status": "success", "enabled": False, "configured": False,
                                              "available": False, "reason": "no_site", "hint": cli, "mode": "lite"})
        text, datas, _ = self._screen("se_panel")
        self.assertNotIn("change-mode", text)
        self.assertIn(i18n.t(self.UID, "se_mini_needs_pro"), text)
        # незнакомая причина — словами ядра, но экранированными
        self._patch_action("miniapp-status", {"status": "success", "enabled": False, "configured": False,
                                              "available": False, "reason": "future_reason", "hint": "a <b> & c"})
        text, _, _ = self._screen("se_panel")
        self.assertIn("a &lt;b&gt; &amp; c", text)
        # отказ моста — ошибка, а не «панель выключена»
        self._patch_action("miniapp-status", {"status": "error", "code": "lock_timeout", "message": ""})
        text, datas, _ = self._screen("se_panel")
        self.assertIn(i18n.t(self.UID, "err_lock_timeout"), text)
        self.assertNotIn(i18n.t(self.UID, "se_panel_off"), text)
        self.assertNotIn("se_mini~on", datas)

    def test_menu_button_follows_the_panel(self):
        os.environ["FAKE_BOT_MODE"] = "pro"
        bot.invalidate_status()
        self.addCleanup(os.environ.pop, "FAKE_BOT_MODE", None)
        self.addCleanup(bot.invalidate_status)
        for data in ("se_mini~rot_do", "se_mini~off_do", "se_mini~on"):
            ctx = FakeContext()
            ctx.bot = _FakeTgBot()
            self._press(data, ctx)
            self.assertTrue(ctx.bot.menu, "%s: кнопка меню чата осталась на прежнем адресе панели" % data)
        ctx = FakeContext()
        ctx.bot = _FakeTgBot()
        self._press("se_mode_to~lite", ctx)
        self._say("MODE lite", ctx)
        self.assertTrue(ctx.bot.menu, "после смены режима кнопка меню не переставлена")

    def test_port_goes_to_the_bridge_without_leading_zeros(self):
        args = os.path.join(TMP_ROOT, "port_zero_args.txt")
        os.environ["FAKE_BOT_ARGS_FILE"] = args
        self.addCleanup(os.environ.pop, "FAKE_BOT_ARGS_FILE", None)
        rec, ctx = self._press("pr_port~awg3")
        rec = self._say("05443", ctx)
        markup = [c[2].get("reply_markup") for c in rec.calls if c[0] == "reply_text" and c[2].get("reply_markup")][-1]
        datas = [b.callback_data for row in markup.inline_keyboard for b in row]
        self.assertIn("pr_port_do~awg3~5443", datas)
        self._press("pr_port_do~awg3~05443")               # старая кнопка или присланная руками
        rec, ctx = self._press("pr_inst_port~awg1")
        rec = self._say("0443", ctx)
        markup = [c[2].get("reply_markup") for c in rec.calls if c[0] == "reply_text" and c[2].get("reply_markup")][-1]
        self.assertIn("pr_inst_do~awg1~443", [b.callback_data for row in markup.inline_keyboard for b in row])
        self._press("pr_inst_do~awg1~0443")
        with open(args, encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn("--port=5443", content)
        self.assertIn("--port=443", content)
        self.assertNotRegex(content, r"--port=0\d")

    def test_agent_reissue_confirm_does_not_survive_the_reissue(self):
        """BOT-23: после перевыпуска экран вопроса с «Подтвердить» оставался как был, и повторное
        нажатие перевыпускало ещё раз — присланный ключ умирал (у одноразового — «клон»)."""
        for data in ("ag_rot_do~a-1a2b", "ag_hard_do~a-1a2b"):
            rec, _ = self._press(data)
            edits = [c for c in rec.calls if c[0] == "edit"]
            self.assertTrue(edits, data)
            datas = [b.callback_data for row in edits[-1][2]["reply_markup"].inline_keyboard for b in row]
            self.assertNotIn(data, datas, "кнопка подтверждения пережила перевыпуск")
            self.assertIn("ag_view~a-1a2b", datas)
            self.assertIn(i18n.t(self.UID, "ag_sent"), str(edits[-1][1][0]))
            self.assertEqual(rec.count("reply_text"), 1)          # данные — отдельным сообщением

    def test_queued_action_does_not_ask_to_repeat(self):
        """BOT-22: при занятом замке действие встаёт в очередь и выполняется, а бот писал
        «Дождитесь завершения предыдущего действия и повторите» — и человек повторял."""

        class HeldLock:
            def locked(self):
                return True

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        saved = bot._BOT_ACTION_LOCK
        bot._BOT_ACTION_LOCK = HeldLock()
        self.addCleanup(setattr, bot, "_BOT_ACTION_LOCK", saved)
        args = os.path.join(TMP_ROOT, "queued_args.txt")
        os.environ["FAKE_BOT_ARGS_FILE"] = args
        self.addCleanup(os.environ.pop, "FAKE_BOT_ARGS_FILE", None)
        upd = FakeUpdate(self.UID, text="/adduser")
        run(bot.cmd_adduser(upd, FakeContext(args=["queued1"])))
        texts = [str(c[1][0]) for c in upd.rec.calls if c[0] == "reply_text"]
        self.assertIn(i18n.t(self.UID, "action_queued"), texts)
        self.assertNotIn(i18n.t(self.UID, "action_busy_body"), texts)
        with open(args, encoding="utf-8") as fh:
            self.assertIn("--name=queued1", fh.read())       # действие не отменено, оно выполнено

    def test_zero_quota_and_expiry_are_named_as_unlimited(self):
        """BOT-20: кнопка «без лимита» / «бессрочно» шлёт 0, а итог писал «0 ГБ» / «0 дней»."""
        for data, want, wrong in (("uq_set~anna~0", i18n.t(self.UID, "unlimited"), "0 " + i18n.t(self.UID, "unit_gb")),
                                  ("ue_set~anna~0", i18n.t(self.UID, "never"), i18n.days(self.UID, 0)),
                                  ("uq_set~anna~50", "50 " + i18n.t(self.UID, "unit_gb"), None),
                                  ("ue_set~anna~30", i18n.days(self.UID, 30), None)):
            text, _, _ = self._screen(data)
            self.assertIn(want, text, data)
            if wrong:
                self.assertNotIn(wrong, text, data)

    def test_long_vpn_key_goes_as_a_file_before_the_message_limit(self):
        """BOT-14: предел считался по одной ссылке (3800), а подпись с предупреждением
        добавляет ещё ~350 знаков — ссылка на 3700–3800 знаков давала «message is too long»."""
        for size, want in ((1400, "reply_text"), (3700, "reply_document")):
            self._patch_action("user-key", {"status": "success", "line": "3.1", "min_client": "5.0.1.5",
                                            "disabled": True, "vpn": "vpn://" + "A" * size})
            rec, _ = self._press("u_key~anna~awg3~vpn")
            self.assertGreaterEqual(rec.count(want), 1, size)
            for c in rec.calls:
                if c[0] == "reply_text" and "vpn://" in str(c[1][0]):
                    self.assertLessEqual(len(str(c[1][0])), 4000)

    def test_key_command_offers_reissue_for_an_imported_peer(self):
        saved = bot._fetch_user

        async def fake(name):
            return {"status": "success"}, {"name": name, "label": name, "enabled": True, "peers": {
                "awg2": {"ip": "10.8.2.9", "has_key": False}, "awg3": {"ip": "10.8.3.9", "has_key": True}}}
        bot._fetch_user = fake
        self.addCleanup(setattr, bot, "_fetch_user", saved)
        for args, want in ((["imp", "awg2"], "u_reis~imp~awg2"), (["imp"], "u_key~imp~awg3~qrconf")):
            upd = FakeUpdate(self.UID, text="/key")
            ctx = FakeContext(args=args)
            run(bot.cmd_key(upd, ctx))
            reply = [c for c in upd.rec.calls if c[0] == "reply_text"][-1]
            markup = reply[2].get("reply_markup")
            datas = [b.callback_data for row in markup.inline_keyboard for b in row]
            self.assertIn(want, datas, args)
            if want.startswith("u_reis~"):
                self.assertFalse([d for d in datas if d.startswith("u_key~")], "выдача по импортированному пиру заведомо откажет")
                # оба выхода и правда словами, а не одна кнопка перевыпуска
                self.assertIn("u_att~imp~awg2", datas)
                self.assertIn("u_view~imp", datas)
                self.assertIn(i18n.tf(self.UID, "user_peer_imported_note_fmt", FX20), str(reply[1][0]))


class LiveCheckTest1Tests(_BotScreenCase):
    """Находки живой проверки бота на test1 (03.10.2026): BOT-1…BOT-7."""

    def _patch(self, name, value):
        saved = getattr(bot, name)
        setattr(bot, name, value)
        self.addCleanup(setattr, bot, name, saved)

    def test_bot1_users_page_total_counts_everyone(self):
        """BOT-1: «Страница 1 из 2 · всего: 14», а не 8 (срез страницы), — на обеих страницах, кнопкой и /users."""
        users = [{"name": "u%02d" % i, "label": "", "profiles": ["awg2"], "traffic": {"rx": 0, "tx": 0},
                  "enabled": True, "expires": ""} for i in range(1, 15)]

        async def fetch():
            return {"status": "success"}, users
        self._patch("fetch_users", fetch)
        for data, page in (("menu_users", 1), ("menu_users~1", 2)):
            text, _, _ = self._screen(data)
            self.assertIn(i18n.tf(self.UID, "page_of_fmt", page, 2, 14), text, data)
        upd = FakeUpdate(self.UID, text="/users")
        run(bot.cmd_users(upd, FakeContext()))
        self.assertIn(i18n.tf(self.UID, "page_of_fmt", 1, 2, 14), str(upd.rec.calls[-1][1][0]))
        # Без total — по-старому длина переданного списка (вызовы других экранов не ломаются)
        self.assertIn(i18n.tf(None, "page_of_fmt", 1, 2, 3), bot.users_list_text(None, users[:3], 0, 2))

    def _as_owner(self, owner=777):
        saved = (list(bot.ADMIN_ORDER), set(bot.ALLOWED_IDS), os.environ.get("OWNER_ID"))

        def restore():
            order, ids, own = saved
            bot.ALLOWED_IDS.clear()
            bot.ALLOWED_IDS.update(ids)
            del bot.ADMIN_ORDER[:]
            bot.ADMIN_ORDER.extend(order)
            if own is None:
                os.environ.pop("OWNER_ID", None)
            else:
                os.environ["OWNER_ID"] = own
        self.addCleanup(restore)
        bot.ALLOWED_IDS.update({owner, self.UID})
        bot.ADMIN_ORDER.extend([owner])
        os.environ["OWNER_ID"] = str(owner)

    def test_bot2_invite_sets_the_panel_menu_button_for_the_new_admin(self):
        """BOT-2: /invite ставит новому админу кнопку меню «🚀 Панель» сразу (как привязка сообщением), а не после
        перезапуска бота; отказ (не тот id) кнопок не трогает."""
        self._as_owner()
        calls = []

        async def refresh(tg_bot):
            calls.append(sorted(bot.ALLOWED_IDS))
        self._patch("refresh_menu_button", refresh)
        ctx = FakeContext(["x"])
        ctx.bot = object()
        run(bot.cmd_invite(FakeUpdate(777, text="/invite x"), ctx))
        self.assertEqual(calls, [])
        ctx = FakeContext(["556"])
        ctx.bot = object()
        run(bot.cmd_invite(FakeUpdate(777, text="/invite 556"), ctx))
        self.assertEqual(len(calls), 1)
        self.assertIn(556, calls[0])

    def test_bot3_diagnostics_names_profiles_by_line(self):
        """BOT-3: «Диагностика» называет профиль линией («AmneziaWG 3.1 · UDP 1320»), а не служебным id «awg3»."""
        r = {"status": "success", "quality": {"docker": True, "ip_forward": "1", "network": True, "disk_free_mb": 5000,
             "server_keys": [{"profile": "awg3", "ok": True}, {"profile": "awg1", "ok": False}],
             "ports": [{"profile": "awg3", "port": 1320, "free": False}, {"profile": "awg1", "port": 2000, "free": True}]}}
        status = {"status": "success", "profiles": [{"profile": "awg3", "line": "3.1"}, {"profile": "awg1", "line": "1.5"}]}
        text = bot.diag_text(self.UID, r, status)
        self.assertIn("AmneziaWG 3.1 · UDP 1320", text)
        self.assertIn("AmneziaWG 1.5 · UDP 2000", text)
        self.assertIn(i18n.tf(self.UID, "diag_keys_ok_fmt", "AmneziaWG 3.1"), text)
        self.assertIn(i18n.tf(self.UID, "diag_keys_bad_fmt", "AmneziaWG 1.5"), text)
        self.assertNotIn("awg3", text)
        self.assertNotIn("awg1", text)
        text, _, _ = self._screen("mt_diag")                  # экран целиком — тоже без служебных id
        self.assertNotRegex(text, r"\bawg[123]\b")

    def test_bot4_no_profile_log_buttons_that_always_fail(self):
        """BOT-4: «Логи» без кнопок профилей — их контейнеры запущены без логов (--log-driver none), кнопка вела в
        заведомое «❌ … логи отключены»; вместо кнопок — одна строка-пояснение. Старая кнопка профиля — то же
        пояснение без ❌."""
        text, datas, _ = self._screen("st_logs")
        self.assertFalse([d for d in datas if d.startswith("st_log~awg")], datas)
        self.assertIn("st_log~bot", datas)
        self.assertIn("ℹ️ " + i18n.t(self.UID, "st_logs_awg_off"), text)
        saved = bot.run_bot_action

        async def fake(action, *a, **kw):
            if action == "logs-tail":
                return {"status": "error", "code": "logs_driver_unreadable", "message": "logs off"}
            return await saved(action, *a, **kw)
        self._patch("run_bot_action", fake)
        text, datas, _ = self._screen("st_log~awg2")
        self.assertIn(i18n.t(self.UID, "st_logs_awg_off"), text)
        self.assertNotIn("❌", text)
        self.assertEqual(datas, ["st_logs"])

    def test_bot5_notice_buttons_open_a_new_message_and_keep_the_alert(self):
        """BOT-5: кнопка под уведомлением («🩺 Диагностика» под тревогой о диске, кнопки доступа агента) открывает экран
        НОВЫМ сообщением — уведомление не правится и остаётся в чате."""
        for data, marker in (("nt~mt_diag", i18n.t(self.UID, "mt_diag_title")), ("nt~ag_view~a-1a2b", "a-1a2b")):
            rec, _ = self._press(data)
            kinds = [c[0] for c in rec.calls]
            self.assertNotIn("edit", kinds, data)                       # уведомление (сообщение кнопки) не тронуто
            self.assertIn("reply_text", kinds, data)                    # новое сообщение…
            edits = [c for c in rec.calls if c[0] == "edit_text"]
            self.assertTrue(edits, data)                                # …и экран нарисован в нём
            self.assertIn(marker, str(edits[-1][1][0]), data)
            self.assertIsNotNone(edits[-1][2].get("reply_markup"), data)
        for bad in ("nt~bogus", "nt~nt~mt_diag", "nt~"):
            rec, _ = self._press(bad)
            self.assertTrue([c for c in rec.calls if c[0] == "answer" and c[2].get("show_alert")], bad)
            self.assertNotIn("reply_text", [c[0] for c in rec.calls], bad)

    def test_bot6_card_title_names_the_person_once_without_a_label(self):
        """BOT-6: без подписи заголовок карточки «👤 bot-single», а не «👤 bot-single (bot-single)»;
        подпись, равная имени, — тоже один раз; настоящая подпись — в скобках, как раньше."""
        def title(user):
            return bot.user_card_text(self.UID, dict(user, peers={}), {}).split("\n")[0]
        for label in (None, "", "  ", "bot-single"):
            head = title({"name": "bot-single", "label": label})
            self.assertEqual(head, "<b>👤 bot-single</b>", repr(label))
            self.assertEqual(head.count("bot-single"), 1, repr(label))
        self.assertEqual(title({"name": "anna", "label": "Анна <телефон>"}), "<b>👤 anna (Анна &lt;телефон&gt;)</b>")
        # Карточка из моста-заглушки: у anna подпись есть — заголовок с ней
        _, anna = run(bot._fetch_user("anna"))
        self.assertIn("👤 anna (Анна)", bot.user_card_text(self.UID, anna, run(bot.get_status())))

    def test_bot7_parse_bulk_prefix_follows_the_core(self):
        """BOT-7: префикс имён разбирается по правилам ядра (users-add-bulk --prefix): пробелы по краям и хвостовые
        - и _ отбрасываются, остаток — латиница, цифры, _ и -, 1–28 знаков."""
        for text, want in (("office", "office"), ("  office-  ", "office"), ("a_-_", "a"), ("Team_2", "Team_2"),
                           ("x" * 28, "x" * 28), ("x" * 28 + "-", "x" * 28), ("x" * 29, None), ("---", None),
                           ("", None), ("офис", None), ("of fice", None), ("a.b", None), ("<b>", None)):
            self.assertEqual(bot.parse_bulk_prefix(text), want, repr(text))

    def test_bot7_bulk_add_names_step_random_or_prefix(self):
        """BOT-7: в массовом добавлении бота шаг «Имена: 🎲 случайные / ✏️ с префиксом», как в SSH и панели; префикс —
        текстом, неверный — пояснение без снятия ожидания; ядру уходит --prefix, итог — office-01… без подписей-дублей."""
        args_file = os.path.join(TMP_ROOT, "bulk_prefix_args.txt")
        open(args_file, "w").close()
        os.environ["FAKE_BOT_ARGS_FILE"] = args_file
        self.addCleanup(os.environ.pop, "FAKE_BOT_ARGS_FILE", None)
        rec, ctx = self._press("u_addm")
        self.assertNotIn(i18n.t(self.UID, "user_bulk_names_random"), str(rec.calls[-1][1][0]))
        text, datas, ctx = self._screen("ub_n~10", ctx)
        self.assertIn(i18n.tf(self.UID, "user_bulk_ask_names_fmt", 10), text)
        self.assertEqual(datas, ["ub_nm~random", "ub_nm~prefix", "ub_back", "menu_users"])
        rec = self._say("office", ctx)                                   # на выборе имён текст — подсказка про кнопки
        self.assertIn(i18n.t(self.UID, "user_bulk_names_use_buttons"), str(rec.calls[-1][1][0]))
        self.assertEqual(ctx.user_data["flow"]["data"]["step"], "names")
        text, datas, ctx = self._screen("ub_nm~prefix", ctx)
        self.assertEqual(ctx.user_data["flow"]["data"]["step"], "prefix")
        self.assertIn(i18n.t(self.UID, "user_bulk_ask_prefix"), text)
        self.assertEqual(datas, ["ub_names", "menu_users"])
        for bad in ("офис", "of fice", "---", "x" * 29):
            rec = self._say(bad, ctx)                                    # ошибка ввода ожидание не снимает
            self.assertIn(i18n.t(self.UID, "user_bulk_bad_prefix"), str(rec.calls[-1][1][0]), bad)
            self.assertEqual(ctx.user_data["flow"]["data"]["step"], "prefix", bad)
        rec = self._say(" office- ", ctx)
        flow = ctx.user_data["flow"]["data"]
        self.assertEqual((flow["step"], flow["prefix"]), ("profiles", "office"))
        screen = [c for c in rec.calls if c[0] == "reply_text"][-1]
        self.assertIn(i18n.tf(self.UID, "user_bulk_names_prefix_fmt", "office", "office"), str(screen[1][0]))
        datas = [b.callback_data for row in screen[2]["reply_markup"].inline_keyboard for b in row]
        self.assertEqual(datas[-3:], ["ub_done", "ub_names", "menu_users"])
        # «Назад» к выбору имён и 🎲 — префикс забыт; снова ✏️ — новый префикс
        self._press("ub_names", ctx)
        self.assertEqual(ctx.user_data["flow"]["data"]["step"], "names")
        self._press("ub_nm~random", ctx)
        self.assertNotIn("prefix", ctx.user_data["flow"]["data"])
        self._press("ub_names", ctx)
        self._press("ub_nm~prefix", ctx)
        self._say("team_", ctx)
        rec, ctx = self._press("ub_done", ctx)
        self.assertNotIn("flow", ctx.user_data)
        with open(args_file, encoding="utf-8") as fh:
            lines = [l.split("\t") for l in fh.read().splitlines() if "--action=users-add-bulk" in l]
        self.assertEqual(len(lines), 1)
        self.assertIn("--prefix=team", lines[0])
        self.assertIn("--count=10", lines[0])
        done = [str(c[1][0]) for c in rec.calls if c[0] == "reply_text"][-1]
        self.assertIn("• <code>team-01</code>", done)
        self.assertIn("• <code>team-10</code>", done)
        self.assertNotIn("team-01 —", done)
        # Кнопки шага имён без мастера (бот перезапущен) — объяснение с возвратом; негодная начинка — алерт
        for data in ("ub_nm~random", "ub_names"):
            rec, _ = self._press(data, FakeContext())
            self.assertIn(i18n.t(self.UID, "user_bulk_lost"), str([c for c in rec.calls if c[0] == "edit"][-1][1][0]), data)
        rec, ctx = self._press("u_addm")
        self._press("ub_n~5", ctx)
        rec, _ = self._press("ub_nm~bogus", ctx)
        self.assertTrue([c for c in rec.calls if c[0] == "answer" and c[2].get("show_alert")])
        self.assertEqual(ctx.user_data["flow"]["data"]["step"], "names")


class KeyOtherAddressTests(_BotScreenCase):
    """«Ключ на другой адрес сервера»: тот же ключ с другим адресом в Endpoint, разово.

    Мост — заглушка по контракту (edge-status key_choices/keys_host, status.key_hosts,
    user-show key_host/key_host_gone, --endpoint у user-key и share-create): FAKE_BOT_EDGE=multi —
    три IP (203.0.113.12 замолчал) и домен vpn.example.com, обычный адрес 203.0.113.10.
    """

    IP10, IP11, IP12 = "cb00710a", "cb00710b", "cb00710c"
    DOMAIN = "vpn.example.com"

    def setUp(self):
        # Кэши статуса и edge-status — модульные: окружение прошлого теста не должно
        # просочиться в этот (invalidate_status сбрасывает оба).
        bot.invalidate_status()
        self.addCleanup(bot.invalidate_status)
        self.args_file = os.path.join(TMP_ROOT, "keyaddr_args_%s.txt" % self._testMethodName)
        if os.path.exists(self.args_file):
            os.remove(self.args_file)
        self._env(FAKE_BOT_ARGS_FILE=self.args_file)

    def _env(self, **kv):
        for k, v in kv.items():
            os.environ[k] = v
            self.addCleanup(os.environ.pop, k, None)

    def _multi(self, **kv):
        self._env(FAKE_BOT_EDGE="multi", **kv)
        bot.invalidate_status()

    def _calls(self, action=None):
        if not os.path.exists(self.args_file):
            return []
        with open(self.args_file, encoding="utf-8") as fh:
            rows = [l.split("\t") for l in fh.read().splitlines()]
        return [r for r in rows if action is None or "--action=%s" % action in r]

    @classmethod
    def _dom_code(cls):
        import hashlib
        return "h" + hashlib.sha1(cls.DOMAIN.encode()).hexdigest()[:6]

    @staticmethod
    def _datas(markup):
        return [b.callback_data for row in markup.inline_keyboard for b in row]

    @staticmethod
    def _labels(markup):
        return [b.text for row in markup.inline_keyboard for b in row]

    def _last_edit(self, rec):
        edits = [c for c in rec.calls if c[0] == "edit"]
        self.assertTrue(edits, "экран не отрисован")
        return str(edits[-1][1][0]), edits[-1][2].get("reply_markup")

    # ── кодирование адреса ──────────────────────────────────────────────────
    def test_addr_code_ipv4_and_domain_round_trip(self):
        self.assertEqual(bot.key_addr_code("203.0.113.11"), "cb00710b")
        self.assertEqual(bot.key_addr_code("10.0.0.1"), "0a000001")
        self.assertEqual(bot.key_addr_code(self.DOMAIN), self._dom_code())
        self.assertEqual(bot.key_addr_code("VPN.Example.com"), self._dom_code(), "домен без учёта регистра")
        for junk in ("", "0.0.0.0", "999.1.1.1", "2001:db8::1", "bad host", None):
            self.assertEqual(bot.key_addr_code(junk), "", junk)
        for code in ("cb00710b", self._dom_code()):
            self.assertTrue(bot.KEY_ADDR_RE.match(code), code)
        for bad in ("CB00710B", "cb00710", "h12345", "hzzzzzz", "cb00710b0"):
            self.assertFalse(bot.KEY_ADDR_RE.match(bad), bad)
        # Обратно — пересчётом по текущему списку, а не по позиции
        self._multi()
        edge = run(bot.run_bot_action("edge-status"))
        self.assertEqual(bot.key_addr_find(edge, "cb00710b")["host"], "203.0.113.11")
        self.assertEqual(bot.key_addr_find(edge, self._dom_code())["host"], self.DOMAIN)
        self.assertIsNone(bot.key_addr_find(edge, "01020304"))
        # Мусор в key_choices (без host, IPv6, повтор) кнопкой не становится
        edge = {"key_choices": [{"host": "203.0.113.11"}, {"host": "203.0.113.11"}, {"host": "2001:db8::1"},
                                {"kind": "ip"}, "junk"]}
        self.assertEqual([c["host"] for c in bot.key_choices(edge)], ["203.0.113.11"])

    # ── кнопка в карточке ───────────────────────────────────────────────────
    def test_card_button_only_with_an_alternative_address(self):
        # Один адрес у машины — менять не на что: кнопки нет
        _, datas, _ = self._screen("u_view~anna")
        self.assertNotIn("u_kaddr~anna", datas)
        # Три адреса и домен — кнопка есть (адрес пользователя — общий keys_host из edge-status)
        self._multi()
        text, datas, _ = self._screen("u_view~anna")
        self.assertIn("u_kaddr~anna", datas)

    def test_card_decides_without_edge_status_when_status_and_user_show_say_enough(self):
        self._multi(FAKE_BOT_KEY_HOST="203.0.113.10")
        _, datas, _ = self._screen("u_view~anna")
        self.assertIn("u_kaddr~anna", datas)
        self.assertEqual(self._calls("edge-status"), [], "status.key_hosts и user-show.key_host уже всё сказали")

    def test_offer_needs_a_private_key_and_a_different_host(self):
        hosts = {"key_hosts": ["203.0.113.10", "203.0.113.11"]}
        imported = {"name": "imp", "peers": {"awg2": {"has_key": False}}}
        self.assertFalse(run(bot.user_key_addr_offered({"key_host": "203.0.113.10"}, imported, hosts)))
        self.assertNotIn("u_kaddr~imp", self._datas(bot.user_card_keyboard(None, imported, key_addr=True)))
        with_key = {"name": "anna", "enabled": True, "peers": {"awg2": {"has_key": True}}}
        self.assertTrue(run(bot.user_key_addr_offered({"key_host": "203.0.113.10"}, with_key, hosts)))
        self.assertFalse(run(bot.user_key_addr_offered({"key_host": "203.0.113.10"}, with_key,
                                                       {"key_hosts": ["203.0.113.10"]})))
        # Закреплённый адрес пользователя пропал с машины — любой адрес машины ему «другой»
        self.assertTrue(run(bot.user_key_addr_offered({"key_host": "198.51.100.9"}, with_key,
                                                      {"key_hosts": ["203.0.113.10"]})))
        self.assertIn("u_kaddr~anna", self._datas(bot.user_card_keyboard(None, with_key, key_addr=True)))
        self.assertNotIn("u_kaddr~anna", self._datas(bot.user_card_keyboard(None, with_key)))

    # ── экран адресов ───────────────────────────────────────────────────────
    def test_address_screen_lists_other_addresses_with_verdicts(self):
        self._multi(FAKE_BOT_KEY_HOST="203.0.113.10")
        rec, _ = self._press("u_kaddr~anna")
        text, markup = self._last_edit(rec)
        self.assertIn(i18n.tf(self.UID, "key_addr_title_fmt", "Анна"), text)
        self.assertIn(i18n.tf(self.UID, "key_addr_now_fmt", "203.0.113.10"), text)
        self.assertIn(i18n.t(self.UID, "key_addr_pick"), text)
        datas = self._datas(markup)
        # Нынешний адрес — не вариант; порядок моста: домен, потом IP, замолчавший в конце
        self.assertEqual(datas, ["u_kah~anna~%s" % self._dom_code(), "u_kah~anna~%s" % self.IP11,
                                 "u_kah~anna~%s" % self.IP12, "u_view~anna"])
        labels = self._labels(markup)
        self.assertEqual(labels[0], i18n.tf(self.UID, "key_addr_domain_fmt", self.DOMAIN))
        self.assertEqual(labels[1], "203.0.113.11 · %s" % i18n.t(self.UID, "edge_v_ok"))
        self.assertEqual(labels[2], "203.0.113.12 · %s" % i18n.t(self.UID, "edge_v_dark"))
        self.assertTrue(labels[2].startswith("203.0.113.12 · ⚠"), labels[2])

    def test_address_screen_for_a_pinned_user_offers_the_common_address(self):
        self._multi(FAKE_BOT_KEY_HOST="203.0.113.11")
        _, datas, _ = self._screen("u_kaddr~anna")
        self.assertIn("u_kah~anna~%s" % self.IP10, datas)
        self.assertNotIn("u_kah~anna~%s" % self.IP11, datas)

    def test_address_screen_says_when_the_pinned_address_is_gone(self):
        self._multi(FAKE_BOT_KEY_HOST="198.51.100.9", FAKE_BOT_KEY_HOST_GONE="1")
        text, datas, _ = self._screen("u_kaddr~anna")
        self.assertIn(i18n.tf(self.UID, "key_addr_now_fmt", "198.51.100.9"), text)
        self.assertIn(i18n.t(self.UID, "key_addr_gone"), text)
        for code in (self.IP10, self.IP11, self.IP12, self._dom_code()):
            self.assertIn("u_kah~anna~%s" % code, datas)

    # ── экран профиля и вида ключа ──────────────────────────────────────────
    def test_formats_screen_per_profile_and_dark_warning(self):
        self._multi(FAKE_BOT_KEY_HOST="203.0.113.10")
        text, datas, _ = self._screen("u_kah~anna~%s" % self.IP12)
        self.assertIn(i18n.tf(self.UID, "key_addr_formats_fmt", "203.0.113.12"), text)
        self.assertIn(i18n.t(self.UID, "key_addr_dark_warn"), text)
        for p in ("awg3", "awg2"):
            for fmt in ("qrconf", "conf", "vpn", "qrvpn"):
                self.assertIn("u_ka~anna~%s~%s~%s" % (p, fmt, self.IP12), datas)
            self.assertIn("u_shf~anna~%s~%s" % (p, self.IP12), datas)
        self.assertEqual(datas[-1], "u_kaddr~anna")
        text, _, _ = self._screen("u_kah~anna~%s" % self.IP11)
        self.assertNotIn(i18n.t(self.UID, "key_addr_dark_warn"), text, "адрес принимает — пугать нечем")

    # ── выдача ──────────────────────────────────────────────────────────────
    def test_key_on_another_address_passes_endpoint_names_file_and_captions(self):
        self._multi(FAKE_BOT_KEY_HOST="203.0.113.10")
        rec, _ = self._press("u_ka~anna~awg2~conf~%s" % self.IP11)
        docs = [c for c in rec.calls if c[0] == "reply_document"]
        self.assertEqual(len(docs), 1)
        # Н-2: имя файла — из ответа ядра (file_name), не длиннее 15 знаков: адрес в имя не входит, он — в подписи
        self.assertEqual(docs[0][1][0].filename, "anna-2_0.conf")
        caption = str(docs[0][2].get("caption"))
        self.assertIn(i18n.tf(self.UID, "key_caption_addr_fmt", "203.0.113.11"), caption)
        self.assertNotIn("FAKE-PRIVATE-KEY", caption)
        keys = self._calls("user-key")
        self.assertEqual(len(keys), 1)
        self.assertIn("--endpoint=203.0.113.11", keys[0])
        self.assertIn("--format=conf", keys[0])
        # Домен — тем же путём; QR и ключ приложения несут ту же строку адреса
        rec, _ = self._press("u_ka~anna~awg3~vpn~%s" % self._dom_code())
        texts = [str(c[1][0]) for c in rec.calls if c[0] == "reply_text"]
        self.assertTrue(any(i18n.tf(self.UID, "key_caption_addr_fmt", self.DOMAIN) in t for t in texts), texts)
        self.assertIn("--endpoint=%s" % self.DOMAIN, self._calls("user-key")[-1])
        rec, _ = self._press("u_ka~anna~awg2~qrconf~%s" % self.IP12)
        photos = [c for c in rec.calls if c[0] == "reply_photo"]
        self.assertIn(i18n.tf(self.UID, "key_caption_addr_fmt", "203.0.113.12"), str(photos[0][2].get("caption")))

    def test_plain_key_is_unchanged(self):
        self._multi(FAKE_BOT_KEY_HOST="203.0.113.10")
        rec, _ = self._press("u_key~anna~awg2~conf")
        docs = [c for c in rec.calls if c[0] == "reply_document"]
        self.assertEqual(docs[0][1][0].filename, "anna-2_0.conf")       # Н-2: file_name ядра
        self.assertNotIn(i18n.tf(self.UID, "key_caption_addr_fmt", "203.0.113.10"), str(docs[0][2].get("caption")))
        argv = self._calls("user-key")[0]
        self.assertFalse([a for a in argv if a.startswith("--endpoint")], "без адреса — байт в байт как было")
        self.assertEqual(self._calls("edge-status"), [], "обычная выдача адреса не ищет")

    def test_key_file_name_from_core_or_old_rule(self):
        """Н-2: имя .conf — file_name ядра; ядро старше поля — прежнее имя (с адресом — с адресом); мусор не берётся."""
        self.assertEqual(bot.key_file_stem({"file_name": "verylon2f2-2_0.conf"}, "verylongusername1", "awg2"), "verylon2f2-2_0")
        self.assertEqual(bot.key_file_stem({}, "anna", "awg2"), "anna-awg2")
        self.assertEqual(bot.key_file_stem({}, "anna", "awg2", "203.0.113.11"), "anna-awg2-203.0.113.11")
        self.assertEqual(bot.key_file_stem({"file_name": "../x.conf"}, "anna", "awg2"), "anna-awg2")
        os.environ["FAKE_BOT_OLD_KEY"] = "1"
        self.addCleanup(os.environ.pop, "FAKE_BOT_OLD_KEY", None)
        self._multi(FAKE_BOT_KEY_HOST="203.0.113.10")
        rec, _ = self._press("u_key~anna~awg2~conf")
        docs = [c for c in rec.calls if c[0] == "reply_document"]
        self.assertEqual(docs[0][1][0].filename, "anna-awg2.conf")

    def test_unknown_address_says_gone_and_reshows_the_address_screen(self):
        self._multi(FAKE_BOT_KEY_HOST="203.0.113.10")
        gone = i18n.t(self.UID, "key_addr_gone")
        for data in ("u_ka~anna~awg2~conf~01020304", "u_kah~anna~01020304", "u_shf~anna~awg2~01020304",
                     "u_shdo~anna~awg2~conf~01020304"):
            rec, _ = self._press(data)
            answers = [c for c in rec.calls if c[0] == "answer"]
            self.assertEqual(answers[0][1][0], gone, data)
            self.assertTrue(answers[0][2].get("show_alert"), data)
            text, markup = self._last_edit(rec)
            self.assertIn(i18n.t(self.UID, "key_addr_pick"), text, data)
            self.assertIn("u_kah~anna~%s" % self.IP11, self._datas(markup), data)
        self.assertEqual(self._calls("user-key") + self._calls("share-create"), [],
                         "ключ на исчезнувший адрес не собирается")

    def test_malformed_address_code_is_refused_before_the_bridge(self):
        for data in ("u_ka~anna~awg2~conf~ZZ", "u_ka~anna~awg2~conf", "u_kah~anna~CB00710B", "u_kah~anna",
                     "u_shf~anna~awg2~1234", "u_shdo~anna~awg2~conf~xyz", "u_ka~bad name~awg2~conf~cb00710b"):
            rec, _ = self._press(data)
            self.assertTrue(any(c[2].get("show_alert") for c in rec.calls if c[0] == "answer"), data)
        self.assertEqual(self._calls(), [], "мусор в callback_data до моста не доходит")

    def test_core_refusal_shows_its_own_words_on_the_address_screen(self):
        for code in ("address_not_here", "invalid_ip", "addresses_unknown"):
            self._multi(FAKE_BOT_KEY_HOST="203.0.113.10", FAKE_BOT_KEY_REFUSE=code)
            rec, _ = self._press("u_ka~anna~awg2~conf~%s" % self.IP11)
            self.assertEqual(rec.count("reply_document"), 0, code)
            text, markup = self._last_edit(rec)
            self.assertIn("Адреса 203.0.113.11 у сервера нет", text, code)
            self.assertNotIn("<code>%s</code>" % code, text, "слова ядра уже человеческие — без кода")
            self.assertIn("u_view~anna", self._datas(markup), code)

    # ── файл по ссылке с другим адресом ─────────────────────────────────────
    def test_share_link_with_another_address(self):
        self._multi(FAKE_BOT_KEY_HOST="203.0.113.10")
        text, datas, _ = self._screen("u_shf~anna~awg2~%s" % self.IP11)
        self.assertIn(i18n.tf(self.UID, "key_caption_addr_fmt", "203.0.113.11"), text)
        self.assertEqual(datas, ["u_shdo~anna~awg2~conf~%s" % self.IP11, "u_shdo~anna~awg2~vpn~%s" % self.IP11,
                                 "u_kah~anna~%s" % self.IP11])
        rec, _ = self._press("u_shdo~anna~awg2~conf~%s" % self.IP11)
        shares = self._calls("share-create")
        self.assertEqual(len(shares), 1)
        self.assertIn("--endpoint=203.0.113.11", shares[0])
        replies = [str(c[1][0]) for c in rec.calls if c[0] == "reply_text"]
        self.assertTrue(any("anna-awg2-203.0.113.11.conf" in r for r in replies), replies)
        self.assertTrue(any(i18n.tf(self.UID, "key_caption_addr_fmt", "203.0.113.11") in r for r in replies))
        # Без адреса раздача прежняя: ни --endpoint, ни строки адреса, «Назад» — к выбору профиля
        _, datas, _ = self._screen("u_shf~anna~awg2")
        self.assertEqual(datas[-1], "u_share~anna")
        self.assertIn("u_shdo~anna~awg2~conf", datas)
        rec, _ = self._press("u_shdo~anna~awg2~conf")
        self.assertFalse([a for a in self._calls("share-create")[-1] if a.startswith("--endpoint")])

    # ── лимиты и доступ ─────────────────────────────────────────────────────
    def test_callback_data_fits_64_bytes_with_a_32_char_name(self):
        long_name = "n" * 32
        saved = bot._fetch_user

        async def fake(name):
            return ({"status": "success", "key_host": "203.0.113.10"},
                    {"name": name, "label": name, "enabled": True,
                     "peers": {p: {"ip": "10.8.2.9", "has_key": True} for p in ("awg1", "awg2", "awg3")}})
        bot._fetch_user = fake
        self.addCleanup(setattr, bot, "_fetch_user", saved)
        self._multi()
        cbs = self._datas(bot.user_card_keyboard(None, run(fake(long_name))[1], key_addr=True))
        self.assertIn("u_kaddr~%s" % long_name, cbs)
        # FakeQuery сам проверяет ≤ 64 байт у каждой нарисованной кнопки — как Telegram
        screens = ["u_kaddr~%s" % long_name, "u_kah~%s~%s" % (long_name, self.IP11),
                   "u_kah~%s~%s" % (long_name, self._dom_code())]
        screens += ["u_shf~%s~%s~%s" % (long_name, p, self.IP11) for p in bot.PROFILE_IDS]
        for data in screens:
            _, datas, _ = self._screen(data)
            cbs.extend(datas)
        worst = ["u_ka~%s~%s~qrconf~%s" % (long_name, p, self.IP11) for p in bot.PROFILE_IDS]
        worst += ["u_shdo~%s~%s~conf~%s" % (long_name, p, self.IP11) for p in bot.PROFILE_IDS]
        self.assertTrue(set(worst) <= set(cbs), set(worst) - set(cbs))
        too_long = [c for c in cbs if len(c.encode("utf-8")) > 64]
        self.assertEqual(too_long, [])

    def test_router_knows_the_new_prefixes(self):
        self.assertIs(bot.resolve_handler("u_kaddr~anna"), bot.cb_user_key_addr)
        self.assertIs(bot.resolve_handler("u_kah~anna~cb00710b"), bot.cb_user_key_addr_formats)
        self.assertIs(bot.resolve_handler("u_ka~anna~awg2~conf~cb00710b"), bot.cb_user_key)
        self.assertIs(bot.resolve_handler("u_key~anna~awg2~conf"), bot.cb_user_key)

    def test_non_admin_gets_nothing(self):
        self._multi(FAKE_BOT_KEY_HOST="203.0.113.10")
        stranger = 777001
        self.assertFalse(bot.is_user_allowed(stranger))
        for data in ("u_kaddr~anna", "u_kah~anna~%s" % self.IP11, "u_ka~anna~awg2~conf~%s" % self.IP11,
                     "u_shf~anna~awg2~%s" % self.IP11, "u_shdo~anna~awg2~conf~%s" % self.IP11):
            reset_stranger_limits()      # пять нажатий подряд — больше лимита ответов чужому в минуту
            upd = FakeUpdate(stranger, data=data)
            run(bot.handle_callback(upd, FakeContext()))
            self.assertEqual([c[0] for c in upd.rec.calls], ["answer"], data)
            self.assertEqual(upd.rec.calls[0][1][0], i18n.t(stranger, "access_denied_short"), data)
        self.assertEqual(self._calls(), [], "чужому — ни одного вызова моста")


class _FakeTgFile:
    """telegram.File: скачивание документа в память."""

    def __init__(self, data):
        self.data = data

    async def download_as_bytearray(self):
        return bytearray(self.data)


class _FakeDocument:
    def __init__(self, data, file_name="proba-awg2.conf", file_size=None):
        self.data = data
        self.file_name = file_name
        self.file_size = len(data) if file_size is None else file_size
        self.fetched = 0

    async def get_file(self):
        self.fetched += 1
        return _FakeTgFile(self.data)


class AttachOldKeyTests(_BotScreenCase):
    """Ключ из приложения Amnezia (has_key: false) и «📎 Привязать старый ключ» (user-attach-key).

    Правило владельца: где бы ключ ни выдавался или показывался, бот говорит, где ключ, что у
    человека всё работает, почему отсюда его не показать, и два выхода. Привязка: ссылка vpn://
    или файл .conf уходят мосту ТОЛЬКО через stdin (argv виден в ps), мимо лога, а сообщение с
    ключом бот убирает из чата. Заглушка моста: proba — awg2 из приложения, awg3 свой; olga —
    единственный профиль из приложения; FAKE_BOT_STATE_DIR помнит удачную привязку.
    """

    KEY = "vpn://OLD-KEY-OF-PROBA-AAAA"
    CONF = "[Interface]\nPrivateKey = OLD-PRIVATE-OF-PROBA=\nAddress = 10.8.2.9/32\n\n[Peer]\nEndpoint = 203.0.113.10:443\n"

    def setUp(self):
        bot.invalidate_status()
        self.addCleanup(bot.invalidate_status)
        self.state = tempfile.mkdtemp(prefix="attach-state-", dir=TMP_ROOT)
        self.stdin_file = os.path.join(self.state, "stdin.txt")
        self.args_file = os.path.join(self.state, "args.txt")
        for k, v in (("FAKE_BOT_STATE_DIR", self.state), ("FAKE_BOT_STDIN_FILE", self.stdin_file),
                     ("FAKE_BOT_ARGS_FILE", self.args_file)):
            os.environ[k] = v
            self.addCleanup(os.environ.pop, k, None)
        self.catcher = _LogCatcher()
        bot.logger.addHandler(self.catcher)
        self.addCleanup(bot.logger.removeHandler, self.catcher)

    # ── помощники ──────────────────────────────────────────────────────────
    def _argv_lines(self, action=None):
        if not os.path.exists(self.args_file):
            return []
        with open(self.args_file, encoding="utf-8") as fh:
            rows = fh.read().splitlines()
        return [r for r in rows if action is None or "--action=%s\t" % action in r]

    def _stdin(self):
        with open(self.stdin_file, encoding="utf-8") as fh:
            return fh.read()

    @staticmethod
    def _replies(rec):
        return [(str(c[1][0]), c[2]) for c in rec.calls if c[0] == "reply_text"]

    @staticmethod
    def _datas(markup):
        return [b.callback_data for row in markup.inline_keyboard for b in row] if markup is not None else []

    def _send_file(self, doc, ctx):
        upd = FakeUpdate(self.UID)
        msg = FakeMessage(upd.rec, self.UID)
        msg.document = doc
        upd.message = upd.effective_message = msg
        run(bot.handle_file_message(upd, ctx))
        return upd.rec

    def _arm(self, name="proba", profile="awg2"):
        text, datas, ctx = self._screen("u_att~%s~%s" % (name, profile))
        return text, datas, ctx

    # ── карточка и переспрос перевыпуска ────────────────────────────────────
    def test_card_offers_both_ways_and_says_where_the_key_is(self):
        text, datas, _ = self._screen("u_view~proba")
        self.assertIn(i18n.tf(self.UID, "user_peer_imported_note_fmt", FX20), text)
        self.assertIn("u_att~proba~awg2", datas)
        self.assertIn("u_reis~proba~awg2", datas)
        self.assertIn("u_key~proba~awg3~conf", datas)
        self.assertNotIn("u_key~proba~awg2~conf", datas)

    def test_reissue_confirmation_warns_about_the_app_key(self):
        """Переспрос перевыпуска у ключа из приложения: старый сломается сразу, привязать — лучше."""
        warn = i18n.t(self.UID, "user_peer_reissue_imported_warn")
        text, datas, _ = self._screen("u_reis~proba~awg2")
        self.assertIn(warn, text)
        self.assertIn("u_reis_do~proba~awg2", datas)
        self.assertIn("u_att~proba~awg2", datas, "лучший выход — прямо на экране переспроса")
        self.assertIn("u_view~proba", datas)
        self.assertEqual(self._argv_lines("user-reissue-peer"), [], "переспрос не перевыпускает")
        # у своего ключа — прежний переспрос, без предупреждения про приложение
        text, datas, _ = self._screen("u_reis~anna~awg2")
        self.assertNotIn(warn, text)
        self.assertNotIn("u_att~anna~awg2", datas)
        for lang in ("ru", "en"):
            body = i18n.t_in(lang, "user_peer_reissue_imported_warn")
            self.assertIn(i18n.t_in(lang, "user_peer_attach_btn").split(" ", 1)[1], body, lang)

    # ── привязка: ссылка текстом ───────────────────────────────────────────
    def test_link_goes_by_stdin_not_argv_and_the_card_comes_back_with_keys(self):
        text, datas, ctx = self._arm()
        self.assertIn(i18n.tf(self.UID, "user_peer_attach_prompt_fmt", FX20), text)
        self.assertEqual(datas, ["u_view~proba"], "на экране ожидания — только «Отмена»")
        self.assertEqual(bot.flow_get(ctx)["kind"], "peer_attach")
        rec = self._say(self.KEY, ctx)
        # ключ — в stdin моста, и только там
        self.assertEqual(self._stdin(), self.KEY)
        calls = self._argv_lines("user-attach-key")
        self.assertEqual(len(calls), 1)
        self.assertIn("--name=proba\t", calls[0])
        self.assertIn("--profile=awg2\t", calls[0])
        self.assertNotIn("OLD-KEY", "\n".join(self._argv_lines()), "ключ в argv виден в ps")
        self.assertNotIn("OLD-KEY", self.catcher.text(), "ключу не место в логе бота")
        # слова ядра и карточка, где на awg2 уже работает выдача
        text, kw = self._replies(rec)[-1]
        self.assertIn("✅ Старый ключ proba на профиле awg2 привязан", text)
        datas = self._datas(kw.get("reply_markup"))
        self.assertIn("u_key~proba~awg2~conf", datas)
        self.assertNotIn("u_att~proba~awg2", datas)
        self.assertEqual(kw.get("parse_mode"), "HTML")
        # сообщение с ключом убрано из чата, ожидание снято
        self.assertEqual(rec.count("delete"), 1)
        self.assertIsNone(bot.flow_get(ctx))

    def test_key_message_leaves_the_chat_before_the_bridge_is_called(self):
        """UI-1: проверка может ждать замок до 15 минут (установка профиля), и упади бот за это
        время — сообщение, которое убиралось только после моста, осталось бы в истории чата."""
        _, _, ctx = self._arm()
        upd = FakeUpdate(self.UID, text=self.KEY)
        real = bot.mutate

        async def spy(action, *args, **kwargs):
            upd.rec.record("bridge:%s" % action)
            return await real(action, *args, **kwargs)

        bot.mutate = spy
        try:
            run(bot.handle_text_message(upd, ctx))
        finally:
            bot.mutate = real
        order = [c[0] for c in upd.rec.calls if c[0] in ("delete", "bridge:user-attach-key")]
        self.assertEqual(order, ["delete", "bridge:user-attach-key"], "сначала из чата, потом — мосту")
        self.assertIn("привязан", self._replies(upd.rec)[-1][0])

    def test_forwarded_message_with_a_caption_goes_whole(self):
        """Ключ часто отдавали пересылкой сообщения бота: подпись идёт вместе со ссылкой. Ядро ищет
        ссылку во всём тексте, бот ничего не вырезает — и ключ без ожидания тоже узнаёт по ней."""
        forwarded = "🔑 proba · awg2 · AmneziaWG 2.0\nКлюч для приложения Amnezia\n\n" + self.KEY
        self.assertTrue(bot.looks_like_key(forwarded))
        self.assertFalse(bot.looks_like_key("просто вопрос про ключи"))
        _, _, ctx = self._arm()
        rec = self._say(forwarded, ctx)
        self.assertEqual(self._stdin(), forwarded)
        self.assertIn("привязан", self._replies(rec)[-1][0])

    def test_conf_file_is_read_and_goes_by_stdin(self):
        _, _, ctx = self._arm()
        doc = _FakeDocument(b"\xef\xbb\xbf" + self.CONF.encode("utf-8"))   # BOM Блокнота Windows
        rec = self._send_file(doc, ctx)
        self.assertEqual(doc.fetched, 1)
        self.assertEqual(self._stdin(), self.CONF.strip())
        self.assertNotIn("OLD-PRIVATE", "\n".join(self._argv_lines()))
        self.assertNotIn("OLD-PRIVATE", self.catcher.text())
        self.assertIn("привязан", self._replies(rec)[-1][0])
        self.assertEqual(rec.count("delete"), 1, "файл с ключом тоже убираем из чата")

    def test_wrong_key_shows_the_core_words_and_keeps_waiting(self):
        _, _, ctx = self._arm()
        # Что прислать вместо — по коду отказа (сцены attach-unreadable, attach-key-mismatch дизайна 0.5.0)
        for sent, words, hint in (("просто текст", "Не удалось прочитать ключ", "user_peer_attach_retry_unreadable"),
                                  ("vpn://SOMEONE-ELSE", "Это не ключ proba", "user_peer_attach_retry_mismatch"),
                                  ("vpn://OTHER-SERVER", "Этот ключ от другого сервера", "user_peer_attach_retry")):
            rec = self._say(sent, ctx)
            text, kw = self._replies(rec)[-1]
            self.assertIn(words, text, sent)
            self.assertIn(i18n.t(self.UID, hint), text)
            self.assertIn("<code>", text, "код отказа виден, как у остальных ошибок моста")
            self.assertEqual(self._datas(kw.get("reply_markup")), ["u_view~proba"])
            self.assertEqual(bot.flow_get(ctx)["kind"], "peer_attach", "ошибка ввода ожидание не снимает")
            self.assertEqual(rec.count("delete"), 1, sent)
        rec = self._say(self.KEY, ctx)
        self.assertIn("привязан", self._replies(rec)[-1][0])
        self.assertEqual(len(self._argv_lines("user-attach-key")), 4)

    def test_already_attached_elsewhere_ends_the_wait_with_the_card(self):
        _, _, ctx = self._arm()
        open(os.path.join(self.state, "attached-proba-awg2"), "w").close()   # привязали из панели
        rec = self._say(self.KEY, ctx)
        text, kw = self._replies(rec)[-1]
        self.assertIn("уже есть на сервере", text)
        self.assertIn("u_key~proba~awg2~conf", self._datas(kw.get("reply_markup")))
        self.assertIsNone(bot.flow_get(ctx))

    def test_big_file_and_picture_are_refused_before_the_bridge(self):
        _, _, ctx = self._arm()
        big = _FakeDocument(b"x", file_size=bot.ATTACH_MAX_BYTES + 1)
        rec = self._send_file(big, ctx)
        self.assertEqual(big.fetched, 0, "большой файл даже не скачиваем")
        self.assertIn(i18n.tf(self.UID, "user_peer_attach_too_big_fmt", 64), self._replies(rec)[-1][0])
        self.assertEqual(rec.count("delete"), 1, "UI-1: присланное на месте ключа — из чата прочь")
        upd = FakeUpdate(self.UID)
        upd.message = upd.effective_message = FakeMessage(upd.rec, self.UID)   # картинка: без document
        run(bot.handle_file_message(upd, ctx))
        reply = self._replies(upd.rec)[-1][0]
        self.assertTrue(reply.startswith(i18n.t(self.UID, "user_peer_attach_photo")), reply)
        self.assertIn(i18n.t(self.UID, "user_peer_attach_key_removed"), reply)
        self.assertEqual(upd.rec.count("delete"), 1, "UI-1: QR ключа — тот же ключ")
        self.assertEqual(self._argv_lines("user-attach-key"), [])
        self.assertEqual(bot.flow_get(ctx)["kind"], "peer_attach")

    def test_cancel_and_timeout_end_the_wait(self):
        _, _, ctx = self._arm()
        self._press("u_view~proba", ctx)                      # «Отмена» — обычная кнопка карточки
        self.assertIsNone(bot.flow_get(ctx))
        rec = self._say(self.KEY, ctx)
        self.assertTrue(self._replies(rec)[-1][0].startswith(i18n.t(self.UID, "user_peer_attach_unexpected")))
        self.assertEqual(rec.count("delete"), 1, "UI-1: ключ «не вовремя» тоже убирается из чата")
        self.assertEqual(self._argv_lines("user-attach-key"), [], "без ожидания ключ на проверку не уходит")
        # просроченное ожидание забывается, как у остальных шагов
        _, _, ctx = self._arm()
        ctx.user_data["flow"]["since"] -= bot.FLOW_TTL + 1
        rec = self._send_file(_FakeDocument(self.CONF.encode()), ctx)
        self.assertTrue(self._replies(rec)[-1][0].startswith(i18n.t(self.UID, "user_peer_attach_unexpected")))
        self.assertEqual(rec.count("delete"), 1)
        self.assertEqual(self._argv_lines("user-attach-key"), [])
        rec = self._say("что-то ещё", FakeContext())
        self.assertEqual(self._replies(rec)[-1][0], i18n.t(self.UID, "text_no_waiter"))
        self.assertEqual(rec.count("delete"), 0, "обычный текст не трогаем")

    def test_stale_attach_button_shows_the_card(self):
        """Ключ уже на сервере или профиля нет — ждать нечего: карточка, как есть сейчас."""
        text, datas, ctx = self._screen("u_att~anna~awg2")
        self.assertIn(i18n.tf(self.UID, "user_peer_attach_not_needed_fmt", FX20), text)
        self.assertIn("u_key~anna~awg2~conf", datas)
        self.assertIsNone(bot.flow_get(ctx))
        text, datas, ctx = self._screen("u_att~bob~awg3")      # у bob профиля awg3 нет вовсе
        self.assertIn("u_view~bob", datas)
        self.assertNotIn(i18n.tf(self.UID, "user_peer_attach_prompt_fmt", "AmneziaWG 3.1"), text)
        self.assertIsNone(bot.flow_get(ctx))

    def test_non_admin_cannot_attach(self):
        stranger = 777000
        upd = FakeUpdate(stranger, data="u_att~proba~awg2")
        ctx = FakeContext()
        run(bot.handle_callback(upd, ctx))
        self.assertIsNone(bot.flow_get(ctx))
        ctx.user_data["flow"] = {"kind": "peer_attach", "since": time.monotonic(),
                                 "data": {"name": "proba", "profile": "awg2"}}
        upd = FakeUpdate(stranger, text=self.KEY)
        run(bot.handle_text_message(upd, ctx))
        upd = FakeUpdate(stranger)
        msg = FakeMessage(upd.rec, stranger)
        msg.document = _FakeDocument(self.CONF.encode())
        upd.message = upd.effective_message = msg
        run(bot.handle_file_message(upd, ctx))
        self.assertEqual(self._argv_lines("user-attach-key"), [])

    # ── мост: stdin только у user-attach-key, вывод упавшего моста — мимо лога ──
    def test_stdin_is_refused_for_other_actions(self):
        r = run(bot.run_bot_action("user-rename", stdin="vpn://X", name="anna", label="A"))
        self.assertEqual(r.get("code"), "bad_arg")
        self.assertEqual(self._argv_lines("user-rename"), [], "мост даже не запускается")

    def test_crashed_bridge_output_with_the_key_stays_out_of_the_log(self):
        os.environ["FAKE_BOT_NO_JSON"] = "1"
        self.addCleanup(os.environ.pop, "FAKE_BOT_NO_JSON", None)
        r = run(bot.run_bot_action("user-attach-key", stdin="vpn://LEAKED-IN-A-CRASH", name="proba", profile="awg2"))
        self.assertEqual(r.get("code"), "transport")
        self.assertIn("user-attach-key", self.catcher.text())
        self.assertNotIn("LEAKED-IN-A-CRASH", self.catcher.text())

    # ── выдача мимо: слова ядра целиком и два выхода, а не тупик ─────────────
    def test_stale_key_button_shows_the_core_words_and_both_ways(self):
        rec, _ = self._press("u_key~proba~awg2~conf")
        text, kw = self._replies(rec)[-1]
        self.assertIn("выдан в приложении Amnezia и хранится только там", text, "слова ядра — целиком")
        self.assertIn("(<code>no_private_key</code>)", text)
        datas = self._datas(kw.get("reply_markup"))
        self.assertIn("u_att~proba~awg2", datas)
        self.assertIn("u_reis~proba~awg2", datas)
        self.assertEqual(rec.count("reply_document"), 0)

    def test_share_picker_says_why_a_profile_is_missing(self):
        text, datas, _ = self._screen("u_share~proba")
        self.assertIn("u_shf~proba~awg3", datas)
        self.assertNotIn("u_shf~proba~awg2", datas)
        self.assertIn(i18n.tf(self.UID, "user_peer_imported_short_fmt", FX20), text)
        # все ключи — в приложении: не «ключей нет», а правда и два выхода
        text, datas, _ = self._screen("u_share~olga")
        self.assertNotIn(i18n.t(self.UID, "share_no_peers"), text)
        self.assertIn(i18n.tf(self.UID, "user_peer_imported_note_fmt", FX20), text)
        self.assertIn("u_att~olga~awg2", datas)
        self.assertIn("u_view~olga", datas)
        # раздача по кнопке, нарисованной до подхвата: слова ядра и выходы
        text, datas, _ = self._screen("u_shdo~proba~awg2~conf")
        self.assertIn("(<code>no_private_key</code>)", text)
        self.assertIn("u_att~proba~awg2", datas)

    def test_connection_page_says_which_key_is_not_on_it(self):
        os.environ["FAKE_BOT_MODE"] = "pro"
        self.addCleanup(os.environ.pop, "FAKE_BOT_MODE", None)
        bot.invalidate_status()
        note = i18n.tf(self.UID, "pg_imported_fmt", FX20)
        text, datas, _ = self._screen("u_page~proba")
        self.assertIn(note, text)
        self.assertIn("pg_ttl~proba~24h", datas)
        rec, _ = self._press("pg_ttl~proba~24h")
        sent = [str(c[2].get("caption") or "") for c in rec.calls if c[0] == "reply_photo"] + \
               [t for t, _ in self._replies(rec)]
        self.assertTrue(any(note in s for s in sent), "в сообщении со ссылкой — оговорка про awg2")
        text, _, _ = self._screen("pg_list~proba")
        self.assertIn(note, text)
        text, _, _ = self._screen("pg_card~p-5a5a")
        self.assertIn(note, text)
        # у anna ключи свои — оговорки нет
        text, _, _ = self._screen("pg_list~anna")
        self.assertNotIn(i18n.tf(self.UID, "pg_imported_fmt", "AmneziaWG 2.0"), text)
        # у olga все ключи в приложении: вместо сроков — правда и выходы; ядро отказывает тем же
        text, datas, _ = self._screen("u_page~olga")
        self.assertIn("u_att~olga~awg2", datas)
        self.assertFalse([d for d in datas if d.startswith("pg_ttl~")])
        text, datas, _ = self._screen("pg_ttl~olga~24h")
        self.assertIn("(<code>no_private_key</code>)", text)
        self.assertIn("u_att~olga~awg2", datas)

    # ── UI-1…UI-4: ключ не остаётся в чате, успех не теряется, сбой моста не сбрасывает ожидание ──
    def test_key_message_is_removed_on_every_path_past_the_bridge(self):
        """UI-1: сообщение с ключом убиралось только после проверки мостом; на путях мимо моста
        рабочий приватный ключ оставался в истории чата навсегда."""
        _, _, ctx = self._arm()

        class Broken(_FakeDocument):
            async def get_file(self):
                raise bot.TelegramError("boom")

        rec = self._send_file(Broken(self.CONF.encode("utf-8")), ctx)
        reply = self._replies(rec)[-1][0]
        self.assertTrue(reply.startswith(i18n.t(self.UID, "user_peer_attach_download_failed")), reply)
        self.assertIn(i18n.t(self.UID, "user_peer_attach_key_removed"), reply)
        self.assertEqual(rec.count("delete"), 1, "файл не скачался — но из чата его убираем")
        self.assertEqual(bot.flow_get(ctx)["kind"], "peer_attach", "ожидание остаётся: прислать ещё раз")
        # ключ посреди другого шага (переименование): убран, а шаг не сбит и мост не звали
        _, ctx = self._press("u_ren~anna")
        rec = self._say(self.KEY, ctx)
        self.assertEqual(rec.count("delete"), 1)
        self.assertEqual(bot.flow_get(ctx)["kind"], "user_rename")
        self.assertEqual(self._argv_lines("user-rename"), [], "ключ не стал подписью пользователя")
        # ключ без ожидания, а удалить не вышло — сказано прямо: удалить самому
        upd = FakeUpdate(self.UID, text=self.KEY)

        async def refuse():
            upd.rec.record("delete_refused")
            raise bot.BadRequest("Message can't be deleted")

        upd.message.delete = refuse
        run(bot.handle_text_message(upd, FakeContext()))
        self.assertIn(i18n.t(self.UID, "user_peer_attach_key_left"), self._replies(upd.rec)[-1][0])
        self.assertEqual(self._argv_lines("user-attach-key"), [])

    def test_key_sent_right_after_the_wait_expired_is_removed_too(self):
        """UI-1: ключ искали дольше десяти минут — ожидание истекло, и QR ключа или файл
        «proba.conf.txt» (Windows прячет расширение), присланные следом, оставались в чате."""
        unexpected = i18n.t(self.UID, "user_peer_attach_unexpected")
        removed = i18n.t(self.UID, "user_peer_attach_key_removed")
        # картинка (QR ключа) сразу после истёкшего ожидания
        _, _, ctx = self._arm()
        ctx.user_data["flow"]["since"] -= bot.FLOW_TTL + 1
        upd = FakeUpdate(self.UID)
        upd.message = upd.effective_message = FakeMessage(upd.rec, self.UID)
        run(bot.handle_file_message(upd, ctx))
        reply = self._replies(upd.rec)[-1][0]
        self.assertTrue(reply.startswith(unexpected), reply)
        self.assertIn(removed, reply)
        self.assertEqual(upd.rec.count("delete"), 1)
        # файл с чужим расширением сразу после истёкшего ожидания
        _, _, ctx = self._arm()
        ctx.user_data["flow"]["since"] -= bot.FLOW_TTL + 1
        doc = _FakeDocument(self.CONF.encode(), file_name="proba.conf.txt")
        rec = self._send_file(doc, ctx)
        self.assertEqual(rec.count("delete"), 1)
        self.assertEqual(doc.fetched, 0, "мимо моста — даже не скачиваем")
        self.assertIsNone(bot.flow_get(ctx))
        # без ожидания вовсе обычную картинку и файл не трогаем
        upd = FakeUpdate(self.UID)
        upd.message = upd.effective_message = FakeMessage(upd.rec, self.UID)
        run(bot.handle_file_message(upd, FakeContext()))
        self.assertEqual(upd.rec.count("delete"), 0)
        self.assertEqual(self._replies(upd.rec)[-1][0], i18n.t(self.UID, "text_no_waiter"))
        rec = self._send_file(_FakeDocument(b"%PDF-1.4", file_name="report.pdf"), FakeContext())
        self.assertEqual(rec.count("delete"), 0)
        self.assertEqual(self._argv_lines("user-attach-key"), [])

    def test_key_in_a_caption_is_removed(self):
        """Ссылка vpn:// подписью к картинке или файлу — тот же ключ в переписке."""
        for doc in (None, _FakeDocument(b"PNG", file_name="screenshot.png")):
            upd = FakeUpdate(self.UID)
            msg = FakeMessage(upd.rec, self.UID)
            msg.caption = "ключ Анны: " + self.KEY
            if doc is not None:
                msg.document = doc
            upd.message = upd.effective_message = msg
            run(bot.handle_file_message(upd, FakeContext()))
            self.assertEqual(upd.rec.count("delete"), 1, doc)
            self.assertIn(i18n.t(self.UID, "user_peer_attach_key_removed"), self._replies(upd.rec)[-1][0])
        self.assertEqual(self._argv_lines("user-attach-key"), [])

    def test_product_key_files_are_removed_even_without_a_wait(self):
        """UI-1: ключ файлом без «Привязать старый ключ» узнавался только как .conf и .vpn.txt — а
        свои же файлы раздачи и выгрузки (.vpn, QR, архив ключей) и приложения Amnezia (.vpn)
        оставались в истории. Теперь — по имени, а файл с «чужим» именем до 64 КБ — по содержимому."""
        removed = i18n.t(self.UID, "user_peer_attach_key_removed")
        for name in ("anna-awg2.vpn", "amnezia_config.vpn", "anna-awg2-qr.png", "anna-awg2-qr.zip",
                     "anna-awg2.zip", "anna-awg2-198.51.100.20.tar.gz", "Anna-AWG3.CONF"):
            doc = _FakeDocument(b"\x89PNG or archive bytes", file_name=name)
            rec = self._send_file(doc, FakeContext())
            self.assertEqual(rec.count("delete"), 1, name)
            self.assertIn(removed, self._replies(rec)[-1][0], name)
            self.assertEqual(doc.fetched, 0, "%s: узнан по имени — не качаем" % name)
        # имя ни о чём не говорит — смотрим, что внутри
        doc = _FakeDocument(("мой старый ключ: " + self.KEY).encode("utf-8"), file_name="key.txt")
        rec = self._send_file(doc, FakeContext())
        self.assertEqual((rec.count("delete"), doc.fetched), (1, 1))
        for doc in (_FakeDocument(b"just notes", file_name="notes.txt"),
                    _FakeDocument(b"x", file_name="backup.zip"),                 # архив не по шаблону раздачи
                    _FakeDocument(b"x", file_name="big.bin", file_size=bot.ATTACH_MAX_BYTES + 1)):
            rec = self._send_file(doc, FakeContext())
            self.assertEqual(rec.count("delete"), 0, doc.file_name)
            self.assertEqual(self._replies(rec)[-1][0], i18n.t(self.UID, "text_no_waiter"), doc.file_name)
        self.assertEqual(doc.fetched, 0, "больше 64 КБ — даже не качаем")
        self.assertEqual(self._argv_lines("user-attach-key"), [], "без ожидания мост не зовём")

    def _media(self, ctx, caption, **attrs):
        """Картинка (без attrs) или другое вложение (video=…, voice=…) с подписью."""
        upd = FakeUpdate(self.UID)
        msg = FakeMessage(upd.rec, self.UID)
        msg.caption = caption
        for k, v in attrs.items():
            setattr(msg, k, v)
        upd.message = upd.effective_message = msg
        run(bot.handle_file_message(upd, ctx))
        return upd.rec

    def test_key_in_a_video_caption_leaves_the_chat(self):
        """UI-1: видео, аудио и голосовое бот не слушал вовсе — ссылка vpn:// в их подписи
        оставалась в чате. Пока ждём ключ, такая подпись и есть ключ — на проверку."""
        rec = self._media(FakeContext(), "смотри " + self.KEY, video=object())
        self.assertEqual(rec.count("delete"), 1)
        self.assertTrue(self._replies(rec)[-1][0].startswith(i18n.t(self.UID, "user_peer_attach_unexpected")))
        rec = self._media(FakeContext(), "просто голосовое", voice=object())
        self.assertEqual(rec.calls, [], "без ключа в подписи — как раньше, ни ответа, ни удаления")
        self.assertEqual(self._argv_lines("user-attach-key"), [])
        _, _, ctx = self._arm()
        rec = self._media(ctx, self.KEY, video=object())
        self.assertEqual(self._stdin(), self.KEY)
        self.assertEqual(rec.count("delete"), 1)
        self.assertIn("привязан", self._replies(rec)[-1][0])

    def test_key_in_a_photo_caption_is_checked_while_waiting(self):
        """Пересланный QR ключа с подписью vpn://: подпись — сам ключ; раньше ответ «QR не читаю»
        убирал сообщение вместе с правильным ключом."""
        _, _, ctx = self._arm()
        rec = self._media(ctx, "ключ proba: " + self.KEY)
        self.assertEqual(self._stdin(), "ключ proba: " + self.KEY)
        self.assertEqual(rec.count("delete"), 1)
        self.assertIn("привязан", self._replies(rec)[-1][0])

    def test_undeleted_key_message_is_named_on_the_bridge_path(self):
        """Удалить сообщение с ключом не вышло ни до моста, ни после: ответ говорит прямо — удалите
        сами, и не утверждает, что сообщение убрано."""
        left = i18n.t(self.UID, "user_peer_attach_key_left")
        orig = bot.run_bot_action
        self.addCleanup(setattr, bot, "run_bot_action", orig)

        def say_undeletable(text, ctx):
            upd = FakeUpdate(self.UID, text=text)

            async def refuse():
                upd.rec.record("delete_refused")
                raise bot.BadRequest("Message can't be deleted")

            upd.message.delete = refuse
            run(bot.handle_text_message(upd, ctx))
            return upd.rec

        async def busy(action, *a, **kw):
            if action == "user-attach-key":
                return {"status": "error", "code": "lock_timeout", "message": "", "action": action}
            return await orig(action, *a, **kw)

        _, _, ctx = self._arm()
        bot.run_bot_action = busy
        rec = say_undeletable(self.KEY, ctx)
        bot.run_bot_action = orig
        text = self._replies(rec)[-1][0]
        self.assertEqual(rec.count("delete_refused"), 2, "до моста и после — две попытки")
        self.assertIn(left, text)
        self.assertNotIn(i18n.t(self.UID, "user_peer_attach_key_removed"), text)
        rec = say_undeletable(self.KEY, ctx)                   # ожидание осталось — теперь удача
        text = self._replies(rec)[-1][0]
        self.assertIn("привязан", text)
        self.assertIn(left, text)

    def test_success_is_said_even_when_the_card_cannot_be_read(self):
        """UI-2: привязка прошла, а следующий user-show — нет: человек видел одну «ошибку моста» и
        шёл «Перевыпустить», ломая рабочий ключ. Итог привязки обязан остаться на экране."""
        _, _, ctx = self._arm()
        orig = bot.run_bot_action
        state = {"attached": False}

        async def patched(action, *a, **kw):
            if action == "user-show" and state["attached"]:
                return {"status": "error", "code": "transport", "message": "", "action": action, "rc": "-1"}
            r = await orig(action, *a, **kw)
            if action == "user-attach-key":
                state["attached"] = bot.bridge_ok(r)
            return r

        bot.run_bot_action = patched
        self.addCleanup(setattr, bot, "run_bot_action", orig)
        rec = self._say(self.KEY, ctx)
        text = self._replies(rec)[-1][0]
        self.assertTrue(state["attached"])
        self.assertTrue(text.startswith("✅"), text)
        self.assertIn("привязан", text)
        self.assertIn(i18n.tf(self.UID, "err_transport_fmt", "-1"), text)

    def test_bridge_that_did_not_check_the_key_keeps_the_wait(self):
        """UI-3: замок занят, таймаут, мост без ответа — ключ не проверен. Сообщение убираем, но
        ожидание остаётся: прислать тот же ключ ещё раз, а не проходить всё заново."""
        orig = bot.run_bot_action
        self.addCleanup(setattr, bot, "run_bot_action", orig)
        for code in ("lock_timeout", "timeout", "transport"):
            _, _, ctx = self._arm()

            async def patched(action, *a, _code=code, **kw):
                if action == "user-attach-key":
                    return {"status": "error", "code": _code, "message": "", "action": action}
                return await orig(action, *a, **kw)

            bot.run_bot_action = patched
            rec = self._say(self.KEY, ctx)
            bot.run_bot_action = orig
            text, kw = self._replies(rec)[-1]
            self.assertIn(i18n.t(self.UID, "user_peer_attach_retry_later"), text, code)
            self.assertEqual(rec.count("delete"), 1, code)
            self.assertEqual(bot.flow_get(ctx)["kind"], "peer_attach", code)
            self.assertEqual(self._datas(kw.get("reply_markup")), ["u_view~proba"], code)
        rec = self._say(self.KEY, ctx)                         # повтор — обычная проверка
        self.assertIn("привязан", self._replies(rec)[-1][0])

    def test_pages_of_a_user_with_app_keys_only_can_be_listed_and_revoked(self):
        """UI-4: страница человека, у которого остались только ключи из приложения, открывается
        (пояснение вместо ключа), а из бота её было не увидеть и не отозвать."""
        os.environ["FAKE_BOT_MODE"] = "pro"
        self.addCleanup(os.environ.pop, "FAKE_BOT_MODE", None)
        bot.invalidate_status()
        text, datas, _ = self._screen("u_page~olga")
        self.assertIn("pg_list~olga", datas)
        self.assertIn("u_att~olga~awg2", datas)
        self.assertFalse([d for d in datas if d.startswith("pg_ttl~")], "новую такую не создаём")
        text, datas, _ = self._screen("pg_list~olga")
        self.assertIn("pg_card~p-0a1b", datas)
        text, datas, _ = self._screen("pg_card~p-0a1b")
        self.assertIn("pg_rev~p-0a1b", datas)
        rec, _ = self._press("pg_rev_do~p-0a1b")
        self.assertTrue(any("page-revoke" in l and "p-0a1b" in l for l in self._argv_lines("page-revoke")))

    def test_callback_data_fits_with_a_32_char_name(self):
        name = "n" * 32
        user = {"name": name, "peers": {p: {"ip": "10.0.0.1", "has_key": False} for p in bot.PROFILE_IDS}}
        for markup in (bot.user_card_keyboard(None, user), bot.imported_view(None, name, list(bot.PROFILE_IDS))[1]):
            for d in self._datas(markup):
                self.assertLessEqual(len(d.encode()), 64, d)
        self.assertIs(bot.resolve_handler("u_att~%s~awg1" % name), bot.cb_user_peer_attach)

    def test_both_languages_carry_the_new_strings(self):
        keys = ("user_peer_imported_note_fmt", "user_peer_imported_short_fmt", "user_peer_attach_btn",
                "user_peer_attach_prompt_fmt", "user_peer_reissue_imported_warn", "pg_imported_fmt",
                "user_peer_attached_fmt")
        for lang in ("ru", "en"):
            for key in keys:
                self.assertNotEqual(i18n.t_in(lang, key), key, "%s/%s" % (lang, key))
        self.assertEqual(i18n.t_in("ru", "user_peer_attach_btn"), "📎 Привязать старый ключ")
        self.assertEqual(i18n.t_in("en", "user_peer_attach_btn"), "📎 Attach the old key")
        # без «приватной части» и прочего жаргона — владелец читает эти строки сам
        for key in keys:
            self.assertNotIn("приватн", i18n.t_in("ru", key), key)


class MenuButtonSecretTests(unittest.TestCase):
    """Аудит безопасности 25.09.2026 (высокий): общая кнопка меню бота (без chat_id) — мини-апп с
    секретным путём панели — была видна любому, кто откроет бота. Теперь общая — всегда список команд,
    мини-апп — только персонально админам, бывшему админу кнопка сбрасывается."""

    class _Bot(object):
        def __init__(self):
            self.calls = []

        async def set_chat_menu_button(self, chat_id=None, menu_button=None):
            self.calls.append((chat_id, menu_button))

    def setUp(self):
        if not bot.PTB_AVAILABLE:
            self.skipTest("python-telegram-bot не установлен")
        self._ids = set(bot.ALLOWED_IDS)
        bot.ALLOWED_IDS.clear()
        bot.ALLOWED_IDS.update({111, 222})
        self._get_status, self._miniapp_url = bot.get_status, bot.miniapp_url

        async def fake_status():
            return {"status": "success"}
        bot.get_status = fake_status
        bot.miniapp_url = lambda status: "https://vpn.example.com/app-SECRETSECRET/"

    def tearDown(self):
        bot.get_status, bot.miniapp_url = self._get_status, self._miniapp_url
        bot.ALLOWED_IDS.clear()
        bot.ALLOWED_IDS.update(self._ids)

    def test_default_button_never_carries_the_panel_url(self):
        tg = self._Bot()
        run(bot.refresh_menu_button(tg))
        default = [b for c, b in tg.calls if c is None]
        self.assertEqual(len(default), 1)
        self.assertIsInstance(default[0], bot.MenuButtonCommands)
        per_chat = dict((c, b) for c, b in tg.calls if c is not None)
        self.assertEqual(set(per_chat), {111, 222})
        for b in per_chat.values():
            self.assertIsInstance(b, bot.MenuButtonWebApp)
            self.assertIn("app-SECRETSECRET", b.web_app.url)

    def test_removed_admin_gets_the_commands_button_back(self):
        tg = self._Bot()
        run(bot.reset_menu_button(tg, 222))
        self.assertEqual(len(tg.calls), 1)
        self.assertEqual(tg.calls[0][0], 222)
        self.assertIsInstance(tg.calls[0][1], bot.MenuButtonCommands)


class LateCallbackAnswerTests(unittest.TestCase):
    """Живая проверка 25.09.2026: на сервере с медленным доступом к Telegram нажатие приходит позже срока,
    query.answer() падает «Query is too old» — действие не должно из-за этого обрываться."""

    def test_too_old_answer_is_swallowed_other_errors_are_not(self):
        if not bot.PTB_AVAILABLE:
            self.skipTest("python-telegram-bot не установлен")
        from telegram.ext import ExtBot
        orig = bot._orig_answer_callback_query

        async def raising(self_, *a, **k):
            raise bot.BadRequest(k.get("_msg", "Query is too old and response timeout expired or query id is invalid"))
        bot._orig_answer_callback_query = raising
        try:
            fn = ExtBot.answer_callback_query
            self.assertIs(run(fn(object(), "id")), False)
            with self.assertRaises(bot.BadRequest):
                run(fn(object(), "id", _msg="Message is not modified"))
        finally:
            bot._orig_answer_callback_query = orig


# ════════════════════════════════════════════════════════════════════════════
# Безопасность: только личный чат, первый админ по коду, поток от незнакомцев
# ════════════════════════════════════════════════════════════════════════════

class PrivateChatOnlyTests(_BotScreenCase):
    """Бот слышит только личный чат с собой: в группе и в канале — ни ответа, ни экрана, ни вызова
    моста, даже если пишет или нажимает админ (остаток аудита безопасности 25.09)."""

    def setUp(self):
        self.args_file = os.path.join(TMP_ROOT, "private_only_args.txt")
        open(self.args_file, "w").close()
        os.environ["FAKE_BOT_ARGS_FILE"] = self.args_file
        self.addCleanup(os.environ.pop, "FAKE_BOT_ARGS_FILE", None)
        self.addCleanup(bot.CONFIRM.cancel, self.UID)

    def _bridge_calls(self):
        with open(self.args_file, encoding="utf-8") as fh:
            return fh.read().splitlines()

    def test_admin_in_a_group_gets_nothing(self):
        for cmd, args in ((bot.cmd_start, []), (bot.cmd_users, []), (bot.cmd_status, []), (bot.cmd_help, []),
                          (bot.cmd_lang, []), (bot.cmd_adduser, ["grp1"]), (bot.cmd_key, ["anna"]),
                          (bot.cmd_invite, ["555"])):
            for chat_type in ("group", "supergroup", "channel"):
                upd = FakeUpdate(self.UID, text="/cmd", chat_type=chat_type)
                run(cmd(upd, FakeContext(args)))
                self.assertEqual(upd.rec.calls, [], "%s в %s" % (cmd.__name__, chat_type))
        self.assertNotIn(555, bot.ALLOWED_IDS, "/invite из группы не добавляет админов")
        # кнопка под старым сообщением в группе: ни ответа, ни экрана
        for chat_type in ("group", "supergroup", "channel"):
            upd = FakeUpdate(self.UID, data="u_view~anna", chat_type=chat_type)
            run(bot.handle_callback(upd, FakeContext()))
            self.assertEqual(upd.rec.calls, [], chat_type)
        # фраза подтверждения, набранная в группе, ничего не удаляет
        _, ctx = self._press("u_del~anna")
        self.assertIsNotNone(bot.CONFIRM.pending(self.UID))
        upd = FakeUpdate(self.UID, text="DELETE anna", chat_type="group")
        run(bot.handle_text_message(upd, ctx))
        self.assertEqual(upd.rec.calls, [])
        self.assertIsNotNone(bot.CONFIRM.pending(self.UID), "фраза из группы не засчитана")
        # ключ, присланный в группу при ждущем шаге «Привязать старый ключ», мост не видит
        ctx.user_data["flow"] = {"kind": "peer_attach", "since": time.monotonic(),
                                 "data": {"name": "proba", "profile": "awg2"}}
        upd = FakeUpdate(self.UID, chat_type="group")
        msg = FakeMessage(upd.rec, self.UID)
        msg.document = _FakeDocument(b"[Interface]\nPrivateKey = X\n")
        upd.message = upd.effective_message = msg
        run(bot.handle_file_message(upd, ctx))
        upd = FakeUpdate(self.UID, text="vpn://KEY-IN-A-GROUP", chat_type="group")
        run(bot.handle_text_message(upd, ctx))
        self.assertEqual(upd.rec.calls, [])
        self.assertEqual(self._bridge_calls(), [], "из группы — ни одного вызова моста")

    def test_the_same_admin_in_private_is_served(self):
        upd = FakeUpdate(self.UID, text="/users")
        run(bot.cmd_users(upd, FakeContext()))
        self.assertEqual(upd.rec.count("reply_text"), 1)
        self.assertTrue(any("--action=user-list" in line for line in self._bridge_calls()))

    def test_first_admin_cannot_be_claimed_from_a_group(self):
        with waiting_for_admin():
            upd = FakeUpdate(self.UID, text="/start 48291375", chat_type="group")
            run(bot.cmd_start(upd, FakeContext(["48291375"])))
            upd2 = FakeUpdate(self.UID, text="48291375", chat_type="supergroup")
            run(bot.handle_text_message(upd2, FakeContext()))
            self.assertEqual(upd.rec.calls + upd2.rec.calls, [])
            self.assertNotIn(self.UID, bot._CLAIM_VERIFIED)
            self.assertTrue(bot._WAITING_FOR_ADMIN)
            self.assertEqual(bot.pairing_candidates(), [], "из группы в кандидаты не попадают")


class HandlerRegistrationTests(unittest.TestCase):
    """Фильтр «только личка» стоит и на входе PTB: команды, текст и файлы из группы и канала до
    обработчиков не доходят вовсе, а сам бот просит у Telegram только сообщения и нажатия."""

    def test_every_message_handler_accepts_only_private_chats(self):
        if not bot.PTB_AVAILABLE:
            self.skipTest("python-telegram-bot не установлен")
        from telegram import Update as TgUpdate
        from telegram.ext import Application, CommandHandler, MessageHandler
        app = Application.builder().token("123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi").build()
        bot.register_handlers(app)
        handlers = [h for group in app.handlers.values() for h in group]

        def upd(chat_type):
            chat_id = 5 if chat_type == "private" else -100500
            return TgUpdate.de_json({"update_id": 1, "message": {
                "message_id": 1, "date": 0, "text": "/start", "chat": {"id": chat_id, "type": chat_type},
                "from": {"id": 5, "is_bot": False, "first_name": "X"},
                "entities": [{"type": "bot_command", "offset": 0, "length": 6}]}}, None)

        checked = 0
        for h in handlers:
            if isinstance(h, (CommandHandler, MessageHandler)):
                checked += 1
                for chat_type in ("group", "supergroup", "channel"):
                    self.assertFalse(h.filters.check_update(upd(chat_type)), "%r пропускает %s" % (h, chat_type))
        self.assertEqual(checked, len(bot.BOT_COMMANDS) + 3,
                         "команды, текст, файлы и прочие сообщения (для окна привязки) — все под фильтром")
        start = next(h for h in handlers if isinstance(h, CommandHandler) and "start" in h.commands)
        self.assertTrue(start.filters.check_update(upd("private")))
        self.assertEqual(sorted(bot.ALLOWED_UPDATES), ["callback_query", "message"])

    def test_captioned_media_reach_the_file_handler(self):
        """UI-1: видео, аудио и голосовое с подписью не доходили ни до одного обработчика — ссылка
        vpn:// в подписи оставалась в чате без ответа. Теперь их слушает обработчик файлов."""
        if not bot.PTB_AVAILABLE:
            self.skipTest("python-telegram-bot не установлен")
        from telegram import Update as TgUpdate
        from telegram.ext import Application
        app = Application.builder().token("123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi").build()
        bot.register_handlers(app)
        handlers = [h for group in app.handlers.values() for h in group]
        media = {"video": {"file_id": "v", "file_unique_id": "v", "width": 1, "height": 1, "duration": 1},
                 "voice": {"file_id": "o", "file_unique_id": "o", "duration": 1},
                 "audio": {"file_id": "a", "file_unique_id": "a", "duration": 1}}
        for kind, obj in media.items():
            for chat_type, want in (("private", [bot.handle_file_message]), ("group", [])):
                upd = TgUpdate.de_json({"update_id": 1, "message": {
                    "message_id": 1, "date": 0, "chat": {"id": 5 if chat_type == "private" else -100500, "type": chat_type},
                    "from": {"id": 5, "is_bot": False, "first_name": "X"}, kind: obj,
                    "caption": "vpn://SECRET"}}, None)
                got = [h.callback for h in handlers if h.check_update(upd)]
                self.assertEqual(got, want, "%s в %s" % (kind, chat_type))

    def test_any_other_private_message_reaches_the_pairing_branch(self):
        """HB-10b: «напишите боту любое сообщение» — и стикер, голосовое, контакт, незнакомая команда
        доходят до бота (handle_other_message), а текст, файлы и известные команды — туда же, что и
        раньше: в группе PTB зовёт первый подошедший обработчик."""
        if not bot.PTB_AVAILABLE:
            self.skipTest("python-telegram-bot не установлен")
        from telegram import Update as TgUpdate
        from telegram.ext import Application
        app = Application.builder().token("123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi").build()
        bot.register_handlers(app)
        handlers = [h for group in app.handlers.values() for h in group]
        # CommandHandler сверяет «/команда@бот» с именем бота — ему нужен бот с username
        tg_bot = type("B", (), {"username": "my_test_bot", "defaults": None})()

        def first(extra, chat_type="private"):
            msg = {"message_id": 1, "date": 0, "chat": {"id": 5 if chat_type == "private" else -100500,
                                                        "type": chat_type},
                   "from": {"id": 5, "is_bot": False, "first_name": "X"}}
            msg.update(extra)
            upd = TgUpdate.de_json({"update_id": 1, "message": msg}, tg_bot)
            return next((h.callback for h in handlers if h.check_update(upd)), None)

        other = {
            "sticker": {"sticker": {"file_id": "s", "file_unique_id": "s", "width": 1, "height": 1,
                                    "is_animated": False, "is_video": False, "type": "regular"}},
            "voice": {"voice": {"file_id": "o", "file_unique_id": "o", "duration": 1}},
            "contact": {"contact": {"phone_number": "+100", "first_name": "C"}},
            "location": {"location": {"latitude": 1.0, "longitude": 2.0}},
            "unknown command": {"text": "/admin", "entities": [{"type": "bot_command", "offset": 0, "length": 6}]},
        }
        for kind, extra in other.items():
            self.assertIs(first(extra), bot.handle_other_message, kind)
            self.assertIsNone(first(extra, "group"), "%s из группы" % kind)
        self.assertIs(first({"text": "hello"}), bot.handle_text_message)
        self.assertIs(first({"text": "/start", "entities": [{"type": "bot_command", "offset": 0, "length": 6}]}),
                      bot.cmd_start)
        self.assertIs(first({"photo": [{"file_id": "p", "file_unique_id": "p", "width": 1, "height": 1}]}),
                      bot.handle_file_message)


class StrangerFloodTests(unittest.TestCase):
    """SEC-B3: каждому чужому сообщению бот отвечал «доступ запрещён» — поток таких сообщений
    съедал лимит Telegram, и ответы админам вставали в очередь. Теперь не больше нескольких
    ответов в минуту на человека и на всех чужих вместе; содержимое чужих сообщений — не в лог."""

    STRANGER = 555001

    def setUp(self):
        reset_stranger_limits()
        self.addCleanup(reset_stranger_limits)
        saved = (set(bot.ALLOWED_IDS), bot._WAITING_FOR_ADMIN)
        bot.ALLOWED_IDS.add(424243)
        bot._WAITING_FOR_ADMIN = False

        def restore():
            bot.ALLOWED_IDS.clear()
            bot.ALLOWED_IDS.update(saved[0])
            bot._WAITING_FOR_ADMIN = saved[1]
        self.addCleanup(restore)

    def test_limiter_counts_per_person_and_overall(self):
        t0 = 1000.0
        got = [bot.stranger_may_reply(1, now=t0 + i) for i in range(5)]
        self.assertEqual(got, [True] * bot.STRANGER_REPLY_LIMIT + [False] * (5 - bot.STRANGER_REPLY_LIMIT))
        self.assertTrue(bot.stranger_may_reply(2, now=t0 + 5), "у другого человека своя минута")
        self.assertTrue(bot.stranger_may_reply(1, now=t0 + bot.STRANGER_WINDOW + 1), "минута прошла — снова можно")
        reset_stranger_limits()
        allowed = sum(bot.stranger_may_reply(100 + i, now=t0) for i in range(200))
        self.assertEqual(allowed, bot.STRANGER_GLOBAL_LIMIT, "общий потолок на всех чужих")
        self.assertLessEqual(len(bot._STRANGER_HITS), bot.STRANGER_GLOBAL_LIMIT,
                             "отказанные не оседают в памяти пустыми записями")

    def test_memory_stays_bounded(self):
        for i in range(bot.STRANGER_TRACK_MAX * 2):
            bot.stranger_may_reply(10 ** 6 + i, now=i * 3.0)      # по двадцать новых в минуту — без отказов
        self.assertLessEqual(len(bot._STRANGER_HITS), bot.STRANGER_TRACK_MAX)

    def test_flood_of_commands_and_buttons_gets_a_few_answers_and_no_content_in_the_log(self):
        catcher = _LogCatcher()
        bot.logger.addHandler(catcher)
        self.addCleanup(bot.logger.removeHandler, catcher)
        replies = 0
        for i in range(10):
            upd = FakeUpdate(self.STRANGER, text="/users SECRET-FLOOD-%d" % i)
            run(bot.cmd_users(upd, FakeContext(["SECRET-FLOOD-%d" % i])))
            replies += upd.rec.count("reply_text")
        self.assertEqual(replies, bot.STRANGER_REPLY_LIMIT)
        self.assertNotIn("SECRET-FLOOD", catcher.text())
        self.assertEqual(catcher.text().count(str(self.STRANGER)), bot.STRANGER_REPLY_LIMIT,
                         "и строки лога о чужом — не на каждое сообщение")
        answers = 0
        for i in range(10):
            upd = FakeUpdate(self.STRANGER + 1, data="u_view~anna")
            run(bot.handle_callback(upd, FakeContext()))
            answers += upd.rec.count("answer")
        self.assertEqual(answers, bot.STRANGER_REPLY_LIMIT)
        # /start чужого — тот же счёт
        reset_stranger_limits()
        replies = 0
        for i in range(6):
            upd = FakeUpdate(self.STRANGER + 2, text="/start")
            run(bot.cmd_start(upd, FakeContext()))
            replies += upd.rec.count("reply_text")
        self.assertEqual(replies, bot.STRANGER_REPLY_LIMIT)

    def test_ptb_does_not_remember_every_stranger_forever(self):
        """PTB 21 после каждого апдейта, дошедшего до обработчика, кладёт id человека и чата в
        множества для persistence и чистит их только при её записи; persistence у бота нет — и
        поток с тысяч чужих аккаунтов оседал бы в памяти навсегда. Цикл уведомлений их забывает."""

        class App:
            persistence = None
            bot = None

        app = App()
        app._user_ids_to_be_updated_in_persistence = set(range(5000))
        app._chat_ids_to_be_updated_in_persistence = {-1, -2}
        self.assertEqual(bot.drop_persistence_marks(app), 5002)
        self.assertEqual(app._user_ids_to_be_updated_in_persistence, set())
        self.assertEqual(app._chat_ids_to_be_updated_in_persistence, set())
        # чужой persistence — её дело: не трогаем
        app.persistence = object()
        app._user_ids_to_be_updated_in_persistence = {1}
        self.assertEqual(bot.drop_persistence_marks(app), 0)
        self.assertEqual(app._user_ids_to_be_updated_in_persistence, {1})
        # цикл уведомлений чистит их на каждом обходе
        app.persistence = None
        app._user_ids_to_be_updated_in_persistence = set(range(10))

        async def one_round():
            task = asyncio.ensure_future(bot._notify_loop(app))
            await asyncio.sleep(0.05)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        run(one_round())
        self.assertEqual(app._user_ids_to_be_updated_in_persistence, set())
        if bot.PTB_AVAILABLE:
            # у настоящего Application эти множества есть — иначе чистка молча перестала бы работать
            from telegram.ext import Application
            real = Application.builder().token("123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi").build()
            self.assertIsNone(real.persistence)
            for attr in ("_user_ids_to_be_updated_in_persistence", "_chat_ids_to_be_updated_in_persistence"):
                self.assertIsInstance(getattr(real, attr, None), set, attr)


class AdminClaimCodeTests(unittest.TestCase):
    """Первый администратор — по одноразовому коду с сервера, а назначение — под замком."""

    UID = 515151

    def test_code_is_read_the_way_people_type_it(self):
        for raw in ("48291375", "4829 1375", "4829-1375", "/start 48291375", "/start@MyTestBot 4829 1375",
                    " 48291375\n", "4829 1375"):                 # неразрывный пробел с телефона
            self.assertEqual(bot.normalize_claim_code(raw), "48291375", raw)
        # Код — ровно восемь цифр: слова и чужие числа кодом не считаются (и промахом тоже)
        for raw in ("", "/start", "1234", "привет", "48291375!", "a" * 65, None, "ABCDEFGH12", "Hello bot",
                    "hello there", "adminadmin", "4829\\1375", "1234567", "123456789", "４８２９１３７５"):
            self.assertEqual(bot.normalize_claim_code(raw), "", raw)

    def test_ordinary_words_before_the_code_do_not_lock_the_owner_out(self):
        """Раньше «Hello bot» (→ hellobot) и любые 8+ латинских букв считались неверным кодом: пять
        фраз до кода — и владельцу не отвечали даже на верный код до перезапуска бота."""
        if not bot.PTB_AVAILABLE:
            self.skipTest("python-telegram-bot не установлен")
        with waiting_for_admin():
            for phrase in ("Hello bot", "hello there", "what is the code", "adminadmin", "Test test",
                           "123456789", "мой код?"):
                run(bot.handle_text_message(FakeUpdate(self.UID, text=phrase), FakeContext()))
            self.assertEqual(bot._CLAIM_FAILS.get(self.UID, 0), 0, "слова — не промахи")
            upd = FakeUpdate(self.UID, text="/start 48291375")
            run(bot.cmd_start(upd, FakeContext(["48291375"])))
            self.assertIn(self.UID, bot._CLAIM_VERIFIED, "верный код после слов — принят")

    def test_without_the_installer_code_nothing_matches(self):
        """Сам бот кода больше не заводит: окно привязки открывает SSH, а код — запасной путь
        установщика без терминала. Нет файла — не подходит ничто, и на «/start КОД» — не экран «Да»."""
        self.assertFalse(hasattr(bot, "ensure_admin_claim_code"))
        with waiting_for_admin(code=None):
            self.assertEqual(bot.admin_claim_code(), "")
            self.assertFalse(bot.claim_code_matches("00000000"), "нет кода — не подходит ничто")
            if bot.PTB_AVAILABLE:
                upd = FakeUpdate(self.UID, text="/start 00000000")
                run(bot.cmd_start(upd, FakeContext(["00000000"])))
                self.assertNotIn(self.UID, bot._CLAIM_VERIFIED)
                self.assertTrue(all(c[2].get("reply_markup") is None for c in upd.rec.calls))
            self.assertFalse(os.path.exists(bot.ADMIN_CLAIM_CODE_FILE))
        with waiting_for_admin(code="48291375"):
            self.assertTrue(bot.claim_code_matches("/start 4829 1375"))
            self.assertFalse(bot.claim_code_matches("57319284"))

    def test_code_sent_as_text_opens_the_claim_screen(self):
        if not bot.PTB_AVAILABLE:
            self.skipTest("python-telegram-bot не установлен")
        with waiting_for_admin():
            upd = FakeUpdate(self.UID, text="4829 1375")
            run(bot.handle_text_message(upd, FakeContext()))
            markup = upd.rec.calls[-1][2].get("reply_markup")
            self.assertEqual(markup.inline_keyboard[0][0].callback_data, "admin_confirm_%d" % self.UID)
            self.assertIn(self.UID, bot._CLAIM_VERIFIED)

    def test_guessing_stops_after_a_few_wrong_codes(self):
        if not bot.PTB_AVAILABLE:
            self.skipTest("python-telegram-bot не установлен")
        catcher = _LogCatcher()
        bot.logger.addHandler(catcher)
        self.addCleanup(bot.logger.removeHandler, catcher)
        with waiting_for_admin():
            for i in range(bot.CLAIM_MAX_FAILS):
                run(bot.cmd_start(FakeUpdate(self.UID, text="/start"), FakeContext(["%08d" % (70000000 + i)])))
            upd = FakeUpdate(self.UID, text="/start 48291375")
            run(bot.cmd_start(upd, FakeContext(["48291375"])))
            self.assertEqual(upd.rec.calls, [], "после пяти промахов — ни ответа, даже на верный код")
            self.assertNotIn(self.UID, bot._CLAIM_VERIFIED)
            self.assertNotIn("70000000", catcher.text(), "присланный код в лог не пишется")
            # чужой перебор не мешает тому, у кого код есть
            other = self.UID + 1
            upd = FakeUpdate(other, text="/start 48291375")
            run(bot.cmd_start(upd, FakeContext(["48291375"])))
            self.assertIn(other, bot._CLAIM_VERIFIED)

    def test_two_yes_at_once_make_one_admin(self):
        """SEC-B2: с concurrent_updates(True) два «Да» разом проходили проверку «админа ещё нет»
        оба — и админами становились оба. Теперь назначение под замком, проверка — повторно."""
        if not bot.PTB_AVAILABLE:
            self.skipTest("python-telegram-bot не установлен")
        a, b = self.UID, self.UID + 7
        orig = FakeQuery.answer

        async def answer_like_telegram(query, text=None, show_alert=False, **kw):
            await asyncio.sleep(0)            # настоящий запрос отдаёт управление циклу событий
            await orig(query, text, show_alert=show_alert, **kw)

        FakeQuery.answer = answer_like_telegram
        self.addCleanup(setattr, FakeQuery, "answer", orig)
        with waiting_for_admin():
            bot._CLAIM_VERIFIED.update({a, b})     # оба прислали верный код (код утёк)
            ua, ub = FakeUpdate(a, data="admin_confirm_%d" % a), FakeUpdate(b, data="admin_confirm_%d" % b)

            async def both():
                await asyncio.gather(bot.handle_callback(ua, FakeContext()), bot.handle_callback(ub, FakeContext()))
            run(both())
            admins = [x for x in (a, b) if x in bot.ALLOWED_IDS]
            self.assertEqual(len(admins), 1, "два «Да» разом — два админа")
            loser = ub if admins[0] == a else ua
            edits = [str(c[1][0]) for c in loser.rec.calls if c[0] == "edit"]
            self.assertIn(i18n.t(None, "admin_already_set"), edits)

    def test_late_presses_of_the_yes_button_stay_quiet(self):
        """SEC-B3 (остаток): «Да» онбординга обходит проверку доступа — после назначения старая
        кнопка и callback_data, присланная руками, получали правку сообщения на каждое нажатие.
        SEC-B2 (остаток): второй тап «Да» уже назначенного админа не затирает «✅ Вы назначены»."""
        if not bot.PTB_AVAILABLE:
            self.skipTest("python-telegram-bot не установлен")
        with waiting_for_admin():
            bot._CLAIM_VERIFIED.add(self.UID)
            upd = FakeUpdate(self.UID, data="admin_confirm_%d" % self.UID)
            run(bot.handle_callback(upd, FakeContext()))
            self.assertIn(self.UID, bot.ALLOWED_IDS)
            upd = FakeUpdate(self.UID, data="admin_confirm_%d" % self.UID)          # второй тап
            run(bot.handle_callback(upd, FakeContext()))
            self.assertEqual([c[0] for c in upd.rec.calls], ["answer"], "только ответ на нажатие, без правки")
            stranger = self.UID + 99
            for data in ("admin_confirm_%d" % stranger, "admin_confirm_xyz"):
                reset_stranger_limits()
                touched = 0
                for _ in range(10):
                    upd = FakeUpdate(stranger, data=data)
                    run(bot.handle_callback(upd, FakeContext()))
                    touched += 1 if upd.rec.calls else 0
                self.assertEqual(touched, bot.STRANGER_REPLY_LIMIT, data)

    def test_code_is_refused_outside_the_window(self):
        """SEC46-07: код жил, пока его не использовали, и каждый рестарт бота давал ему новые 10 минут.
        Теперь верный код без открытого окна (не открывали или оно истекло) ничего не даёт, а ответ —
        тот же, что любому чужому (SEC50-01), и называет путь — «Привязать по сообщению» в SSH-меню."""
        if not bot.PTB_AVAILABLE:
            self.skipTest("python-telegram-bot не установлен")
        not_set_up = i18n.tf(self.UID, "stranger_private_bot_fmt", self.UID)
        for until in (None, time.time() - 5):
            with waiting_for_admin(window=False):
                if until is not None:
                    open_pairing_window(until=until)          # окно было, но истекло
                self.assertFalse(bot.admin_claim_open())
                upd = FakeUpdate(self.UID, text="/start 48291375")
                run(bot.cmd_start(upd, FakeContext(["48291375"])))
                self.assertEqual([str(c[1][0]) for c in upd.rec.calls], [not_set_up])
                upd = FakeUpdate(self.UID, text="4829 1375")
                run(bot.handle_text_message(upd, FakeContext()))
                self.assertNotIn(self.UID, bot._CLAIM_VERIFIED, "код вне окна не принят")
                self.assertEqual(bot._CLAIM_FAILS.get(self.UID, 0), 0, "и промахом не считается")
                self.assertFalse(os.path.exists(bot.ADMIN_CANDIDATES_FILE), "вне окна кандидатов нет")
                # старая кнопка «Да» из прошлого окна — тоже ничего
                bot._CLAIM_VERIFIED.add(self.UID)
                upd = FakeUpdate(self.UID, data="admin_confirm_%d" % self.UID)
                run(bot.handle_callback(upd, FakeContext()))
                self.assertTrue(bot._WAITING_FOR_ADMIN)
                self.assertNotIn(self.UID, bot.ALLOWED_IDS)
        self.assertIn("Привязать по сообщению", i18n.t_in("ru", "stranger_private_bot_fmt"))
        self.assertIn("Bind by message", i18n.t_in("en", "stranger_private_bot_fmt"))
        # в открытом окне тот же код принимается
        with waiting_for_admin():
            upd = FakeUpdate(self.UID, text="/start 48291375")
            run(bot.cmd_start(upd, FakeContext(["48291375"])))
            self.assertIn(self.UID, bot._CLAIM_VERIFIED)

    def test_a_restart_no_longer_opens_a_window(self):
        """Окно раньше считалось от старта процесса; теперь только по файлу, который пишет SSH."""
        self.assertFalse(hasattr(bot, "ADMIN_CLAIM_WINDOW_SECONDS"))
        with waiting_for_admin(window=False):
            self.assertFalse(bot.admin_claim_open())
            self.assertEqual(bot.admin_claim_seconds_left(), 0)
            open_pairing_window(seconds=300)
            self.assertTrue(bot.admin_claim_open())
            self.assertTrue(290 <= bot.admin_claim_seconds_left() <= 300)
            # «вечное» окно (забытый или испорченный файл) — не дольше часа от записи файла
            open_pairing_window(until=time.time() + 10 ** 9)
            self.assertLessEqual(bot.pairing_until(), time.time() + bot.PAIRING_WINDOW_MAX + 1)
            for garbage in ("", "soon", "nan", "inf", "-5"):
                with open(bot.ADMIN_PAIRING_UNTIL_FILE, "w", encoding="utf-8") as fh:
                    fh.write(garbage)
                self.assertFalse(bot.pairing_window_open(), garbage)


class _PairingTgBot:
    """Telegram для цикла привязки: кому и что отправлено, кому поставлена кнопка меню чата."""

    def __init__(self):
        self.sent = []
        self.menu = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, text, kw))

    async def set_chat_menu_button(self, chat_id=None, menu_button=None):
        self.menu.append(chat_id)

    async def set_my_commands(self, *a, **kw):
        return True


class AdminPairingTests(unittest.TestCase):
    """Привязка администратора сообщением (решение владельца 27.09, docs/BOT.md «Контракт привязки»):
    SSH открывает окно, бот записывает написавших в кандидаты и отвечает «вернитесь в терминал»,
    SSH подтверждает одного — бот подхватывает его из .env без перезапуска и здоровается."""

    OWNER = 616161
    STRANGER = 626262

    def setUp(self):
        reset_stranger_limits()
        self.addCleanup(reset_stranger_limits)

    def _msg(self, uid, text="/start", first_name="Owner", username="owner_tg", chat_type="private"):
        """Личное сообщение: /start… — командой, остальное — текстом."""
        upd = FakeUpdate(uid, text=text, chat_type=chat_type)
        upd.effective_user.first_name = first_name
        upd.effective_user.username = username
        if text.startswith("/start"):
            run(bot.cmd_start(upd, FakeContext(text.split()[1:])))
        else:
            run(bot.handle_text_message(upd, FakeContext()))
        return upd

    @staticmethod
    def _replies(upd):
        return [str(c[1][0]) for c in upd.rec.calls if c[0] == "reply_text"]

    def _confirm_in_ssh(self, uid, env_ids=None):
        """Что делает SSH, подтвердив человека: .env с админами, потом файл подтверждения."""
        ids = str(uid) if env_ids is None else env_ids
        with open(bot.ENV_FILE, "w", encoding="utf-8") as fh:
            fh.write("BOT_TOKEN=secret\nALLOWED_IDS=%s\nOWNER_ID=%s\n" % (ids, ids.split(",")[0] if ids else ""))
        with open(bot.ADMIN_CONFIRMED_FILE, "w", encoding="utf-8") as fh:
            fh.write("%d\n" % uid)

    # ── окно и кандидаты ───────────────────────────────────────────────────
    def _neutral(self, uid):
        return i18n.tf(uid, "stranger_private_bot_fmt", uid)

    def test_candidate_is_recorded_only_inside_the_window(self):
        with waiting_for_admin(code=None, window=False):
            upd = self._msg(self.OWNER)
            self.assertFalse(os.path.exists(bot.ADMIN_CANDIDATES_FILE), "без окна кандидатов нет")
            self.assertEqual(self._replies(upd), [self._neutral(self.OWNER)])
            open_pairing_window()
            before = int(time.time())
            upd = self._msg(self.OWNER)
            self.assertEqual(self._replies(upd), [self._neutral(self.OWNER)], "в окне — тот же ответ (SEC50-01)")
            self.assertIsNone(upd.rec.calls[0][2].get("reply_markup"), "подтверждают в SSH, а не кнопкой")
            self.assertEqual(stat.S_IMODE(os.stat(bot.ADMIN_CANDIDATES_FILE).st_mode), 0o600)
            with open(bot.ADMIN_CANDIDATES_FILE, encoding="utf-8") as fh:
                items = json.load(fh)
            self.assertEqual(len(items), 1)
            self.assertEqual(sorted(items[0]), ["first_name", "id", "ts", "username"])
            self.assertEqual((items[0]["id"], items[0]["username"], items[0]["first_name"]),
                             (self.OWNER, "owner_tg", "Owner"))
            self.assertTrue(before <= items[0]["ts"] <= int(time.time()))
            # чужой текстом — второй кандидат; владелец ещё раз (позже) — запись та же и на том же месте
            self._msg(self.STRANGER, "hello", first_name="Mallory", username="")
            with patch.object(bot.time, "time", return_value=time.time() + 120):
                self._msg(self.OWNER, "это я", first_name="Другое имя")
            self.assertEqual(bot.pairing_candidates()[0], items[0], "номер и запись владельца не меняются")
            self.assertEqual([c["id"] for c in bot.pairing_candidates()], [self.OWNER, self.STRANGER])
            self.assertEqual(bot.pairing_candidates()[1]["username"], "")
            self.assertFalse(bot.is_user_allowed(self.OWNER), "написавший сам админом не становится")

    def test_the_same_reply_inside_and_outside_the_window(self):
        """SEC50-01: посторонний не должен по ответу понять, открыто ли окно привязки. Раньше в окне
        приходило «запрос получен, окно открыто ещё N мин», а вне окна — «доступ запрещён» или тишина."""
        ru, en = i18n.t_in("ru", "stranger_private_bot_fmt"), i18n.t_in("en", "stranger_private_bot_fmt")
        self.assertIn("Это бот частного сервера", ru)
        self.assertIn("Если вы его владелец — откройте SSH-терминал сервера, там будет запрос на привязку", ru)
        self.assertIn("This is the bot of a private server", en)
        self.assertIn("open the server's SSH terminal", en)
        for text in (ru, en):
            self.assertNotRegex(text, r"(?i)\b(?:мин|min)\b", "никакого остатка окна")
            self.assertEqual(text.count("%s"), 1, "подстановка одна — ID написавшего")
        kinds = ("/start", "/users", "hello", "48291375", "sticker", "file")

        def replies(uid, kind):
            if kind == "sticker":
                upd = FakeUpdate(uid, text="")
                run(bot.handle_other_message(upd, FakeContext()))
            elif kind == "file":
                upd = FakeUpdate(uid, text="")
                run(bot.handle_file_message(upd, FakeContext()))
            elif kind == "/users":
                upd = FakeUpdate(uid, text=kind)
                run(bot.cmd_users(upd, FakeContext()))
            else:
                return self._replies(self._msg(uid, kind))
            return self._replies(upd)

        for admins in (False, True):
            with waiting_for_admin(code=None, window=False):
                if admins:
                    bot.ALLOWED_IDS.add(self.OWNER)
                    bot._WAITING_FOR_ADMIN = False
                got = {}
                for window in (False, True):
                    if window:
                        open_pairing_window()
                    for i, kind in enumerate(kinds):
                        reset_stranger_limits()
                        uid = self.STRANGER + i + (100 if window else 0)
                        got[(window, kind)] = [r.replace(str(uid), "ID") for r in replies(uid, kind)]
                for kind in kinds:
                    self.assertEqual(got[(False, kind)], got[(True, kind)], (admins, kind))
                    self.assertEqual(got[(False, kind)], [self._neutral(self.STRANGER).replace(str(self.STRANGER), "ID")],
                                     (admins, kind))
                # в окне написавшие — кандидаты, вне окна — нет
                self.assertEqual(sorted(c["id"] for c in bot.pairing_candidates()),
                                 [self.STRANGER + i + 100 for i in range(len(kinds))])
            # поток сообщений от одного человека: ответов — под лимитом, запись — одна
            with waiting_for_admin(code="48291375"):
                got = sum(len(self._replies(self._msg(self.STRANGER, "msg %d" % i))) for i in range(8))
                self.assertEqual(got, bot.STRANGER_REPLY_LIMIT)
                self.assertEqual([c["id"] for c in bot.pairing_candidates()], [self.STRANGER])

    def test_the_first_reply_to_a_new_candidate_is_not_silenced_by_a_flood(self):
        """SEC50-01: поток чужих выбирает общий лимит ответов (30 в минуту) — владелец, которого SSH
        просит написать боту, всё равно получает ответ: у первого ответа новому кандидату свой бюджет.
        Бюджет — сам список кандидатов: вне окна и после десятого кандидата обход лимита не действует."""
        with waiting_for_admin(code=None, window=False):
            bot.ALLOWED_IDS.add(424242)
            bot._WAITING_FOR_ADMIN = False
            flood = [self._replies(self._msg(900000 + i, "spam")) for i in range(bot.STRANGER_GLOBAL_LIMIT + 5)]
            self.assertEqual(sum(1 for r in flood if r), bot.STRANGER_GLOBAL_LIMIT)
            self.assertEqual(self._replies(self._msg(self.OWNER, "hi")), [], "вне окна общий лимит держит всех")
            open_pairing_window()
            self.assertEqual(self._replies(self._msg(self.OWNER, "hi")), [self._neutral(self.OWNER)],
                             "новый кандидат — ответ мимо общего лимита")
            self.assertEqual(self._replies(self._msg(self.OWNER, "hi again")), [], "второй ответ — снова под лимитом")
            for i in range(1, bot.PAIRING_MAX_CANDIDATES):
                self.assertEqual(len(self._replies(self._msg(800000 + i, "me too"))), 1, i)
            self.assertEqual(len(bot.pairing_candidates()), bot.PAIRING_MAX_CANDIDATES)
            self.assertEqual(self._replies(self._msg(700000, "late")), [], "список заполнен — обхода нет")
            self.assertNotIn(700000, [c["id"] for c in bot.pairing_candidates()])

    def test_names_from_telegram_are_cleaned_before_the_terminal(self):
        with waiting_for_admin(code=None):
            self._msg(self.STRANGER, first_name='Evil\x1b[2J\u009b31m\u202e\\x", "id": 1} {[ ' + "я" * 100,
                      username="not a username!")
            rec = bot.pairing_candidates()[0]
            self.assertEqual(rec["id"], self.STRANGER, "имя не подменяет id в файле")
            self.assertEqual(rec["username"], "")
            self.assertLessEqual(len(rec["first_name"]), bot.PAIRING_NAME_MAX)
            for bad in ("\x1b", "\u009b", "\u202e", "\\", '"', "{", "}", "[", "]"):
                self.assertNotIn(bad, rec["first_name"])
            self.assertTrue(rec["first_name"].startswith("Evil 2J 31m x , id : 1 я"), rec["first_name"])
            # SEC50-01: невидимые символы (вся категория Cf и пустые буквы) — прочь, а не пробелом
            invisible = "\u061c\u200b\u200c\u200d\u2060\ufeff\u00ad\u202e\u2066\u3164\u115f\ufe0f\U000e0041"
            self._msg(self.STRANGER + 1, first_name="Vi" + invisible + "taly" + invisible, username="owner2")
            self.assertEqual(bot.pairing_candidates()[1]["first_name"], "Vitaly")
            for ch in map(chr, range(0x110000)):
                if unicodedata.category(ch) in ("Cc", "Cf"):
                    self.assertEqual(bot._pairing_clean("a" + ch + "b").replace(" ", ""), "ab", hex(ord(ch)))

    def test_the_first_ten_candidates_stay_in_place(self):
        """SEC50-01: записываются первые десять по времени первого сообщения, дальше — никто; запись не
        переписывается и не переезжает. Раньше новые вытесняли самых давних (и владельца), а повторное
        сообщение переносило запись в конец — номер в SSH сдвигался."""
        def user(uid, name="N"):
            return type("U", (), {"id": uid, "username": "u%d" % uid, "first_name": name})()
        with waiting_for_admin(code=None):
            got = [bot.pairing_record_candidate(user(700 + i), now=1000 + i) for i in range(15)]
            self.assertEqual(got, [bot.PAIRING_NEW] * 10 + [bot.PAIRING_FULL] * 5)
            self.assertEqual([c["id"] for c in bot.pairing_candidates()], list(range(700, 710)))
            before = os.stat(bot.ADMIN_CANDIDATES_FILE).st_mtime_ns
            os.utime(bot.ADMIN_CANDIDATES_FILE, ns=(before - 10 ** 9, before - 10 ** 9))
            before = os.stat(bot.ADMIN_CANDIDATES_FILE).st_mtime_ns
            snapshot = bot.pairing_candidates()
            for i in range(20):                       # поток от того, кто уже в списке, — и с другим именем
                self.assertEqual(bot.pairing_record_candidate(user(700, "Другое"), now=5000 + i), bot.PAIRING_LISTED)
            self.assertEqual(os.stat(bot.ADMIN_CANDIDATES_FILE).st_mtime_ns, before, "файл не переписан")
            self.assertEqual(bot.pairing_candidates(), snapshot)
            # мусор в файле не роняет запись
            with open(bot.ADMIN_CANDIDATES_FILE, "w", encoding="utf-8") as fh:
                fh.write("{not json")
            self.assertEqual(bot.pairing_candidates(), [])
            self.assertEqual(bot.pairing_record_candidate(FakeUser(self.OWNER)), bot.PAIRING_NEW)
            self.assertEqual([c["id"] for c in bot.pairing_candidates()], [self.OWNER])

    def test_any_other_message_counts_only_in_the_window(self):
        """HB-10b: стикер, голосовое, контакт от чужого в окне — кандидат и ответ; вне окна — тот же ответ,
        но не кандидат (SEC50-01); у админа такие сообщения бот по-прежнему не читает."""
        with waiting_for_admin(code=None):
            upd = FakeUpdate(self.STRANGER, text="")
            run(bot.handle_other_message(upd, FakeContext()))
            self.assertEqual([c["id"] for c in bot.pairing_candidates()], [self.STRANGER])
            self.assertEqual(self._replies(upd), [self._neutral(self.STRANGER)])
            bot.ALLOWED_IDS.add(self.OWNER)
            bot._WAITING_FOR_ADMIN = False
            upd = FakeUpdate(self.OWNER, text="")
            run(bot.handle_other_message(upd, FakeContext()))
            self.assertEqual(upd.rec.calls, [], "стикер админа — как раньше, без ответа")
            self.assertNotIn(self.OWNER, [c["id"] for c in bot.pairing_candidates()])
            os.unlink(bot.ADMIN_PAIRING_UNTIL_FILE)
            reset_stranger_limits()
            upd = FakeUpdate(self.STRANGER + 1, text="")
            run(bot.handle_other_message(upd, FakeContext()))
            self.assertEqual(self._replies(upd), [self._neutral(self.STRANGER + 1)], "вне окна — тот же ответ")
            self.assertNotIn(self.STRANGER + 1, [c["id"] for c in bot.pairing_candidates()])

    # ── одноразовый код и окно ─────────────────────────────────────────────
    def test_the_code_belongs_to_its_window(self):
        """HB-1: код привязан к окну временем записи, а не возрастом. Установщик пишет код и окно рядом
        (в любом порядке) — код принимается; код без окна лежит и ждёт окна (не удаляется, но и не
        принимается); код, записанный раньше окна больше чем на минуту, — от прошлого окна; через минуту
        после конца окна его код удаляется, а код, записанный после конца окна, — уже для следующего."""
        if not bot.PTB_AVAILABLE:
            self.skipTest("python-telegram-bot не установлен")
        tg = _PairingTgBot()
        for order in ("code_first", "window_first"):
            with waiting_for_admin(code=None, window=False):
                if order == "window_first":
                    open_pairing_window()
                with open(bot.ADMIN_CLAIM_CODE_FILE, "w", encoding="utf-8") as fh:
                    fh.write("48291375\n")
                if order == "code_first":
                    open_pairing_window()
                self.assertTrue(bot.admin_claim_open(), order)
                upd = self._msg(self.OWNER, "/start 48291375")
                self.assertIn(self.OWNER, bot._CLAIM_VERIFIED, order)
        # код без окна: запуск бота и обходы его не трогают, но и не принимают
        with waiting_for_admin(code="48291375", window=False):
            now = time.time()
            for later in (0, 120, 10 ** 5):
                run(bot.pairing_tick(tg, now=now + later))
            self.assertTrue(os.path.exists(bot.ADMIN_CLAIM_CODE_FILE), "код без окна не удаляется")
            self.assertFalse(bot.admin_claim_open())
            upd = self._msg(self.OWNER, "/start 48291375")
            self.assertEqual(self._replies(upd), [self._neutral(self.OWNER)])
            self.assertNotIn(self.OWNER, bot._CLAIM_VERIFIED)
            # окно открыли много позже — код прошлого окна в нём не годится: восемь цифр — просто сообщение
            old = time.time() - 3 * bot.PAIRING_CODE_SLACK
            os.utime(bot.ADMIN_CLAIM_CODE_FILE, (old, old))
            open_pairing_window()
            self.assertEqual(bot.admin_claim_code(), "")
            self.assertFalse(bot.admin_claim_open())
            reset_stranger_limits()
            upd = self._msg(self.OWNER, "/start 48291375")
            self.assertEqual(self._replies(upd), [self._neutral(self.OWNER)])
            self.assertNotIn(self.OWNER, bot._CLAIM_VERIFIED)
            self.assertEqual(bot._CLAIM_FAILS.get(self.OWNER, 0), 0)
        # окно закончилось: его код — через минуту прочь; код, записанный после конца окна, — остаётся
        with waiting_for_admin(code="48291375"):
            until = bot.pairing_until()
            run(bot.pairing_tick(tg, now=until + bot.PAIRING_STALE_SECONDS / 2))
            self.assertTrue(os.path.exists(bot.ADMIN_CLAIM_CODE_FILE), "минута запаса на гонку с SSH")
            run(bot.pairing_tick(tg, now=until + bot.PAIRING_STALE_SECONDS + 5))
            self.assertFalse(os.path.exists(bot.ADMIN_CLAIM_CODE_FILE))
            with open(bot.ADMIN_CLAIM_CODE_FILE, "w", encoding="utf-8") as fh:
                fh.write("57319284\n")
            after = until + 30
            os.utime(bot.ADMIN_CLAIM_CODE_FILE, (after, after))
            run(bot.pairing_tick(tg, now=until + 10 ** 4))
            self.assertTrue(os.path.exists(bot.ADMIN_CLAIM_CODE_FILE), "код следующего окна не трогаем")

    def test_eight_digits_without_a_code_are_just_a_message(self):
        """HB-5: в окне без кода (привязка через SSH) восемь цифр — обычное сообщение: кандидат и тот же
        ответ, что на любое сообщение, а не экран кода и не промах."""
        with waiting_for_admin(code=None):
            for text in ("48291375", "/start 48291375", "4829 1375"):
                reset_stranger_limits()
                upd = self._msg(self.OWNER, text)
                self.assertEqual(self._replies(upd), [self._neutral(self.OWNER)], text)
            self.assertEqual(bot._CLAIM_FAILS.get(self.OWNER, 0), 0)
            self.assertNotIn(self.OWNER, bot._CLAIM_VERIFIED)
            self.assertEqual([c["id"] for c in bot.pairing_candidates()], [self.OWNER])

    def test_a_new_window_forgives_old_misses_and_old_verifications(self):
        """HB-7: счёт промахов по коду — на одно окно: новое окно (SSH переписал файл) его обнуляет,
        и «верный код» прошлого окна в новом не действует — даже если окно сменилось посреди нажатия."""
        if not bot.PTB_AVAILABLE:
            self.skipTest("python-telegram-bot не установлен")

        def reopen():
            open_pairing_window()
            later = time.time() + 1
            os.utime(bot.ADMIN_PAIRING_UNTIL_FILE, (later, later))
            os.utime(bot.ADMIN_CLAIM_CODE_FILE, (later, later))      # установщик выдал код заново

        with waiting_for_admin():
            for i in range(bot.CLAIM_MAX_FAILS):
                self._msg(self.OWNER, "/start %08d" % (70000000 + i))
            upd = self._msg(self.OWNER, "/start 48291375")
            self.assertEqual(upd.rec.calls, [], "пять промахов — тишина")
            reopen()
            upd = self._msg(self.OWNER, "/start 48291375")
            self.assertIn(self.OWNER, bot._CLAIM_VERIFIED, "в новом окне — снова можно")
            # «Да» с кодом прошлого окна: окно сменилось, пока бот отвечал на нажатие
            orig = FakeQuery.answer

            async def answer_and_reopen(query, text=None, show_alert=False, **kw):
                reopen()
                await orig(query, text, show_alert=show_alert, **kw)

            FakeQuery.answer = answer_and_reopen
            try:
                upd = FakeUpdate(self.OWNER, data="admin_confirm_%d" % self.OWNER)
                run(bot.handle_callback(upd, FakeContext()))
            finally:
                FakeQuery.answer = orig
            self.assertNotIn(self.OWNER, bot.ALLOWED_IDS)
            edits = [str(c[1][0]) for c in upd.rec.calls if c[0] == "edit"]
            self.assertEqual(edits, [i18n.tf(self.OWNER, "admin_claim_need_code", bot.esc(bot.ADMIN_CLAIM_CODE_FILE))])

    # ── подтверждение из SSH ────────────────────────────────────────────────
    def test_confirmation_is_picked_up_without_a_restart(self):
        with waiting_for_admin(code="48291375"):
            self._msg(self.OWNER)
            self._confirm_in_ssh(self.OWNER)
            tg = _PairingTgBot()
            self.assertEqual(run(bot.pairing_tick(tg)), self.OWNER)
            self.assertTrue(bot.is_user_allowed(self.OWNER))
            self.assertTrue(bot.is_owner(self.OWNER))
            self.assertFalse(bot._WAITING_FOR_ADMIN)
            for path in (bot.ADMIN_CONFIRMED_FILE, bot.ADMIN_CANDIDATES_FILE, bot.ADMIN_PAIRING_UNTIL_FILE,
                         bot.ADMIN_CLAIM_CODE_FILE, bot.ADMIN_CONFIRMED_REJECTED_FILE):
                self.assertFalse(os.path.exists(path), path)
            # ответ SSH — файл с id: пропажа .admin_confirmed сама по себе ничего не значит (HB-4)
            with open(bot.ADMIN_PAIRING_DONE_FILE, encoding="utf-8") as fh:
                self.assertEqual(fh.read(), "%d\n" % self.OWNER)
            self.assertEqual(stat.S_IMODE(os.stat(bot.ADMIN_PAIRING_DONE_FILE).st_mode), 0o600)
            os.unlink(bot.ADMIN_PAIRING_DONE_FILE)
            self.assertEqual(tg.sent[0][0], self.OWNER)
            # время подтверждения — UTC с подписью (сцена claim-confirmed, DS2-54)
            self.assertRegex(tg.sent[0][1], "^" + re.escape(i18n.tf(self.OWNER, "admin_paired_fmt", "203.0.113.10", "HH:MM",
                                                                    self.OWNER)).replace("HH:MM", r"\d\d:\d\d") + "$")
            self.assertTrue(tg.sent[0][1].startswith("✓ "))
            self.assertEqual(tg.sent[0][2].get("parse_mode"), "HTML")
            if bot.PTB_AVAILABLE:
                self.assertEqual(len(tg.sent), 2, "после приветствия — главное меню")
                datas = [b.callback_data for row in tg.sent[1][2]["reply_markup"].inline_keyboard
                         for b in row if b.callback_data]
                self.assertIn("menu_users", datas)
                self.assertIn(self.OWNER, tg.menu, "кнопка меню чата — и новому админу")
            # следующий обход ничего не повторяет; сообщение нового админа — уже как у админа
            sent = len(tg.sent)
            self.assertIsNone(run(bot.pairing_tick(tg)))
            self.assertEqual(len(tg.sent), sent)
            upd = self._msg(self.OWNER, "просто текст")
            self.assertEqual(self._replies(upd), [i18n.t(self.OWNER, "text_no_waiter")])

    def _rejected(self):
        with open(bot.ADMIN_CONFIRMED_REJECTED_FILE, encoding="utf-8") as fh:
            text = fh.read()
        self.assertEqual(stat.S_IMODE(os.stat(bot.ADMIN_CONFIRMED_REJECTED_FILE).st_mode), 0o600)
        os.unlink(bot.ADMIN_CONFIRMED_REJECTED_FILE)
        return text

    def test_confirmation_waits_for_the_env_and_gives_up_later(self):
        """HB-4: отказ — не пропажа файла, а .admin_confirmed.rejected (что написал SSH и почему)."""
        with waiting_for_admin(code=None):
            self._msg(self.OWNER)
            self._confirm_in_ssh(self.OWNER, env_ids="")          # .env ещё без него
            tg = _PairingTgBot()
            self.assertIsNone(run(bot.pairing_tick(tg)))
            self.assertTrue(os.path.exists(bot.ADMIN_CONFIRMED_FILE), "SSH мог ещё не дописать .env — ждём")
            self.assertFalse(os.path.exists(bot.ADMIN_CONFIRMED_REJECTED_FILE))
            self.assertFalse(bot.is_user_allowed(self.OWNER))
            self.assertEqual(tg.sent, [])
            catcher = _LogCatcher()
            bot.logger.addHandler(catcher)
            self.addCleanup(bot.logger.removeHandler, catcher)
            self.assertIsNone(run(bot.pairing_tick(tg, now=time.time() + bot.PAIRING_STALE_SECONDS + 5)))
            self.assertFalse(os.path.exists(bot.ADMIN_CONFIRMED_FILE), "через минуту — отказ")
            self.assertEqual(self._rejected(), "%d\nnot_in_env\n" % self.OWNER)
            self.assertIn(str(self.OWNER), catcher.text())
            self.assertFalse(os.path.exists(bot.ADMIN_PAIRING_DONE_FILE))
            self.assertEqual(tg.sent, [])
            # мусор вместо id — отказ сразу, никого не назначает
            with open(bot.ADMIN_CONFIRMED_FILE, "w", encoding="utf-8") as fh:
                fh.write("rm -rf /\n")
            self.assertIsNone(run(bot.pairing_tick(tg)))
            self.assertFalse(os.path.exists(bot.ADMIN_CONFIRMED_FILE))
            self.assertEqual(self._rejected(), "rm -rf /\nbad_id\n")
            self.assertTrue(bot._WAITING_FOR_ADMIN)
            # пустой файл — SSH ещё пишет: ждём, а через минуту — отказ «empty»
            open(bot.ADMIN_CONFIRMED_FILE, "w").close()
            self.assertIsNone(run(bot.pairing_tick(tg)))
            self.assertTrue(os.path.exists(bot.ADMIN_CONFIRMED_FILE))
            self.assertFalse(os.path.exists(bot.ADMIN_CONFIRMED_REJECTED_FILE))
            self.assertIsNone(run(bot.pairing_tick(tg, now=time.time() + bot.PAIRING_STALE_SECONDS + 5)))
            self.assertEqual(self._rejected(), "\nempty\n")
            self.assertFalse(os.path.exists(bot.ADMIN_CONFIRMED_FILE))

    def test_env_is_read_like_dotenv(self):
        with waiting_for_admin(code=None):
            with open(bot.ENV_FILE, "w", encoding="utf-8") as fh:
                fh.write('BOT_TOKEN="x"\n# ALLOWED_IDS=999\nexport ALLOWED_IDS="111,222"\nOWNER_ID=111 # владелец\n')
            self.assertTrue(bot.reload_admins_from_env())
            self.assertEqual(bot.admin_ids_ordered(), [111, 222])
            self.assertEqual(bot.owner_id(), 111)
            os.unlink(bot.ENV_FILE)
            self.assertFalse(bot.reload_admins_from_env(), "нет файла — всё как было")
            self.assertEqual(bot.admin_ids_ordered(), [111, 222])

    def test_a_malformed_env_line_does_not_lock_the_admins_out(self):
        """HB-3: строка, испорченная правкой руками, не должна оставить бот без админов. Кавычки с
        комментарием за ними читаются как у python-dotenv; ни одного ID — прежний список остаётся."""
        with waiting_for_admin(code=None):
            for line, want in (('ALLOWED_IDS="111,222" # заметка', [111, 222]),
                               ("ALLOWED_IDS='333' # c", [333]),
                               ("ALLOWED_IDS=444 555 # два", [444, 555]),
                               ('ALLOWED_IDS="666,777', [666, 777])):     # незакрытая кавычка
                with open(bot.ENV_FILE, "w", encoding="utf-8") as fh:
                    fh.write("BOT_TOKEN=x\n%s\nOWNER_ID=%d\n" % (line, want[0]))
                self.assertTrue(bot.reload_admins_from_env(), line)
                self.assertEqual(bot.admin_ids_ordered(), want, line)
                self.assertEqual(bot.owner_id(), want[0], line)
            for line in ("ALLOWED_IDS=garbage", "ALLOWED_IDS=", 'ALLOWED_IDS=""', "BOT_TOKEN=only"):
                with open(bot.ENV_FILE, "w", encoding="utf-8") as fh:
                    fh.write("BOT_TOKEN=x\n%s\n" % line)
                self.assertFalse(bot.reload_admins_from_env(), line)
                self.assertEqual(bot.admin_ids_ordered(), [666, 777], "список прежний: %s" % line)
                self.assertEqual(bot.owner_id(), 666, "и владелец прежний: %s" % line)
                self.assertFalse(bot._WAITING_FOR_ADMIN)
            # подтверждение из SSH при испорченном .env: админы на месте, подтверждённый — не админ, потом отказ
            self._confirm_in_ssh(self.OWNER)
            with open(bot.ENV_FILE, "w", encoding="utf-8") as fh:
                fh.write("BOT_TOKEN=x\nALLOWED_IDS=oops\n")
            tg = _PairingTgBot()
            self.assertIsNone(run(bot.pairing_tick(tg)))
            self.assertEqual(bot.admin_ids_ordered(), [666, 777])
            run(bot.pairing_tick(tg, now=time.time() + bot.PAIRING_STALE_SECONDS + 5))
            self.assertEqual(self._rejected(), "%d\nnot_in_env\n" % self.OWNER)
            self.assertEqual(tg.sent, [])

    def test_a_closed_window_leaves_no_code_and_no_candidates(self):
        """SEC46-07: код не переживает своё окно; кандидаты — тоже (чужие id и имена на диске ни к чему)."""
        with waiting_for_admin(code="48291375"):
            self._msg(self.STRANGER)
            bot._CLAIM_VERIFIED.add(self.STRANGER)
            tg = _PairingTgBot()
            run(bot.pairing_tick(tg))                               # окно открыто — ничего не трогаем
            self.assertTrue(os.path.exists(bot.ADMIN_CANDIDATES_FILE) and os.path.exists(bot.ADMIN_CLAIM_CODE_FILE))
            self.assertIn(self.STRANGER, bot._CLAIM_VERIFIED)
            end = bot.pairing_until()
            run(bot.pairing_tick(tg, now=end + 1))                  # окно истекло
            self.assertNotIn(self.STRANGER, bot._CLAIM_VERIFIED, "верный код годится только в своём окне")
            self.assertTrue(os.path.exists(bot.ADMIN_CLAIM_CODE_FILE), "минута запаса на гонку с SSH")
            run(bot.pairing_tick(tg, now=end + bot.PAIRING_STALE_SECONDS + 10))
            self.assertFalse(os.path.exists(bot.ADMIN_CLAIM_CODE_FILE))
            self.assertFalse(os.path.exists(bot.ADMIN_CANDIDATES_FILE))
            self.assertTrue(os.path.exists(bot.ADMIN_PAIRING_UNTIL_FILE), "файл окна — дело SSH")
            self.assertEqual(tg.sent, [])

    def test_the_loop_runs_from_start_and_notices_quickly(self):
        """Бот замечает подтверждение сам, без перезапуска: цикл поднимается при старте и сразу смотрит.

        «Сразу» проверяется устройством цикла, а не часами (TAIL-25): пауза между обходами на время проверки —
        час, значит заметить подтверждение цикл может только первым обходом, до первой паузы. Ждём событие —
        приветствие новому админу закончено — с потолком в минуту. Прежде ждали пять секунд и только «админ в
        списке»: он появляется до приветствия, а приветствие ждёт мост (get_status); под нагрузкой полного
        прогона мост отвечал дольше, тест гасил цикл посреди приветствия, и sent оставался пустым."""
        self.assertLessEqual(bot.PAIRING_POLL_SECONDS, 2)

        class App:
            persistence = None
            bot = _PairingTgBot()

        finished = []
        real_greet = bot.pairing_greet

        async def greet(tg_bot, uid):
            try:
                await real_greet(tg_bot, uid)
            finally:
                finished.append(uid)

        def greeted():
            return bot.is_user_allowed(self.OWNER) and finished == [self.OWNER]

        async def until_greeted(task):
            deadline = time.monotonic() + 60
            while not greeted() and not task.done() and time.monotonic() < deadline:
                await asyncio.sleep(0.02)

        with waiting_for_admin(code=None), patch.object(bot, "PAIRING_POLL_SECONDS", 3600), \
                patch.object(bot, "pairing_greet", greet):
            self._msg(self.OWNER)
            self._confirm_in_ssh(self.OWNER)

            async def scenario():
                await bot._announce_self(App)
                task = bot._NOTIFY_TASK["pairing"]
                self.assertIsNotNone(task)
                await until_greeted(task)
                await bot._shutdown(App)
                self.assertTrue(task.done())

            if bot.PTB_AVAILABLE:
                run(scenario())
            else:
                async def one_round():
                    task = asyncio.ensure_future(bot._pairing_loop(App))
                    await until_greeted(task)
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
                run(one_round())
            self.assertTrue(greeted(), "за минуту первый обход не заметил подтверждение и не поздоровался")
            self.assertEqual(App.bot.sent[0][0], self.OWNER)


class NameSuggestTests(_BotScreenCase):
    """«🎲 Случайное имя» в мастере добавления (решение владельца 27.09): вариант от ядра (name-suggest)
    правит то же сообщение, под ним «✓ Взять» и «🎲 Ещё»; своё имя текстом работает как раньше;
    ядро, которое действия не знает, — кнопки нет."""

    def setUp(self):
        self.state = tempfile.mkdtemp(prefix="names-", dir=TMP_ROOT)
        self.args_file = os.path.join(self.state, "args.txt")
        for key, value in (("FAKE_BOT_STATE_DIR", self.state), ("FAKE_BOT_ARGS_FILE", self.args_file)):
            os.environ[key] = value
            self.addCleanup(os.environ.pop, key, None)
        self.addCleanup(bot._NAME_SUGGEST.__setitem__, "unsupported", False)
        lang = patch.object(bot, "get_user_lang", return_value="ru")
        lang.start()
        self.addCleanup(lang.stop)

    def _bridge(self, action):
        if not os.path.exists(self.args_file):
            return []
        with open(self.args_file, encoding="utf-8") as fh:
            lines = [line.split("\t") for line in fh.read().splitlines()]
        return [argv for argv in lines if "--action=%s" % action in argv]

    @staticmethod
    def _last_edit(rec):
        edits = [c for c in rec.calls if c[0] == "edit"]
        markup = edits[-1][2].get("reply_markup")
        buttons = [b for row in markup.inline_keyboard for b in row] if markup else []
        return str(edits[-1][1][0]), [b.callback_data for b in buttons], [b.text for b in buttons]

    def test_suggest_again_and_take_in_the_same_message(self):
        self.assertEqual([i18n.t_in("ru", k) for k in ("user_name_random", "user_name_take", "user_name_again")],
                         ["🎲 Случайное имя", "✓ Взять", "🎲 Ещё"])
        rec, ctx = self._press("u_add")
        text, datas, labels = self._last_edit(rec)
        self.assertEqual(datas, ["ua_rnd", "menu_users"])
        self.assertEqual(labels[0], i18n.t(self.UID, "user_name_random"))
        rec, ctx = self._press("ua_rnd", ctx)
        self.assertEqual(rec.count("reply_text"), 0, "вариант — в том же сообщении, а не новым")
        text, datas, labels = self._last_edit(rec)
        self.assertIn("Туманный Лондон", text)
        self.assertIn("<code>foggy_london</code>", text)
        self.assertEqual(datas, ["ua_take~foggy_london", "ua_rnd", "menu_users"])
        self.assertEqual(labels[:2], [i18n.t(self.UID, "user_name_take"), i18n.t(self.UID, "user_name_again")])
        self.assertEqual(ctx.user_data["flow"]["data"]["step"], "name", "своё имя текстом по-прежнему ждём")
        calls = self._bridge("name-suggest")
        self.assertEqual(len(calls), 1)
        self.assertIn("--lang=ru", calls[0])
        rec, ctx = self._press("ua_rnd", ctx)                     # «🎲 Ещё» — другой вариант
        text, datas, _ = self._last_edit(rec)
        self.assertIn("Солнечная Манила", text)
        self.assertNotIn("Туманный Лондон", text)
        self.assertEqual(datas[0], "ua_take~sunny_manila")
        rec, ctx = self._press("ua_take~sunny_manila", ctx)       # «✓ Взять»
        self.assertEqual(rec.count("reply_text"), 0, "дальше — в том же сообщении")
        data = ctx.user_data["flow"]["data"]
        self.assertEqual((data["name"], data["label"], data["step"]), ("sunny_manila", "Солнечная Манила", "profiles"))
        self.assertNotIn("suggest", data)
        self.assertNotIn("suggested", data)
        self._press("ua_done", ctx)
        self._press("ua_q~0", ctx)
        self._press("ua_d~0", ctx)
        argv = self._bridge("user-add")[-1]
        self.assertIn("--name=sunny_manila", argv)
        self.assertIn("--label=Солнечная Манила", argv, "подпись — отображаемое имя человека")

    def test_take_is_what_the_person_saw(self):
        """Два быстрых «🎲 Ещё» могут вернуться в другом порядке: «✓ Взять» несёт id показанного варианта
        и берёт его с его подписью, а не «последний сохранённый»."""
        rec, ctx = self._press("u_add")
        self._press("ua_rnd", ctx)                                # foggy_london
        self._press("ua_rnd", ctx)                                # sunny_manila
        rec, ctx = self._press("ua_take~foggy_london", ctx)       # нажали кнопку под первым вариантом
        data = ctx.user_data["flow"]["data"]
        self.assertEqual((data["name"], data["label"]), ("foggy_london", "Туманный Лондон"))
        # id, которого мастер не предлагал (руками собранная callback_data), не берётся
        rec, ctx = self._press("u_add")
        self._press("ua_rnd", ctx)
        rec, ctx = self._press("ua_take~admin", ctx)
        self.assertNotIn("name", ctx.user_data["flow"]["data"])
        alerts = [c[1][0] for c in rec.calls if c[0] == "answer" and c[2].get("show_alert")]
        self.assertEqual(alerts, [i18n.t(self.UID, "user_name_random_gone")])
        self.assertIn("ua_take~snowy_ottawa", self._last_edit(rec)[1], "на экране — текущий вариант")

    def test_a_typed_name_still_works_and_drops_the_suggested_label(self):
        rec, ctx = self._press("u_add")
        self._press("ua_rnd", ctx)
        self._say("bad name!", ctx)                               # ошибка ввода — ждём дальше
        self.assertEqual(ctx.user_data["flow"]["data"]["step"], "name")
        self._say("marina", ctx)
        data = ctx.user_data["flow"]["data"]
        self.assertEqual((data["name"], data["step"]), ("marina", "profiles"))
        self.assertNotIn("label", data)
        self.assertNotIn("suggest", data)
        self.assertNotIn("suggested", data)
        # взяли вариант, вернулись назад и ввели своё — подпись варианта к нему не приклеивается
        self._press("ua_back", ctx)
        self._press("ua_rnd", ctx)                                # sunny_manila
        self._press("ua_take~sunny_manila", ctx)
        self.assertEqual(ctx.user_data["flow"]["data"]["label"], "Солнечная Манила")
        self._press("ua_back", ctx)
        self._say("marina2", ctx)
        self.assertNotIn("label", ctx.user_data["flow"]["data"])
        self._press("ua_done", ctx)
        self._press("ua_q~0", ctx)
        self._press("ua_d~0", ctx)
        argv = self._bridge("user-add")[-1]
        self.assertIn("--name=marina2", argv)
        self.assertFalse([a for a in argv if a.startswith("--label")], argv)

    def test_older_core_hides_the_button(self):
        os.environ["FAKE_BOT_NAME_SUGGEST"] = "unknown"
        self.addCleanup(os.environ.pop, "FAKE_BOT_NAME_SUGGEST", None)
        rec, ctx = self._press("u_add")
        rec, ctx = self._press("ua_rnd", ctx)
        self.assertEqual([c[1][0] for c in rec.calls if c[0] == "answer"], [None], "ответ — сразу и без алерта")
        text, datas, _ = self._last_edit(rec)
        self.assertIn(i18n.t(self.UID, "user_name_random_unavailable"), text, "причина — в самом сообщении")
        self.assertIn(i18n.t(self.UID, "user_ask_name"), text)
        self.assertEqual(datas, ["menu_users"], "кнопки, которая не работает, больше нет")
        rec, ctx = self._press("u_add")                           # и в следующем мастере тоже
        self.assertEqual(self._last_edit(rec)[1], ["menu_users"])
        self._say("plain", ctx)
        self.assertEqual(ctx.user_data["flow"]["data"]["step"], "profiles", "своё имя работает")

    def test_contract_shape_and_the_interface_language(self):
        os.environ["FAKE_BOT_NAME_SUGGEST"] = "okdata"
        self.addCleanup(os.environ.pop, "FAKE_BOT_NAME_SUGGEST", None)
        rec, ctx = self._press("u_add")
        with patch.object(bot, "get_user_lang", return_value="en"):
            rec, ctx = self._press("ua_rnd", ctx)
        text, datas, _ = self._last_edit(rec)
        self.assertIn("Foggy London", text)
        self.assertIn("foggy_london", text)
        self.assertIn("ua_take~foggy_london", datas)
        self.assertIn("--lang=en", self._bridge("name-suggest")[-1])
        rec, ctx = self._press("ua_take~foggy_london", ctx)
        self.assertEqual(ctx.user_data["flow"]["data"]["label"], "Foggy London")

    def test_stale_buttons_and_bridge_errors_are_not_silent(self):
        # «✓ Взять» из прошлого мастера (варианта в этом нет) — алерт и вопрос об имени заново
        rec, ctx = self._press("u_add")
        rec, ctx = self._press("ua_take~foggy_london", ctx)
        alerts = [c[1][0] for c in rec.calls if c[0] == "answer" and c[2].get("show_alert")]
        self.assertEqual(alerts, [i18n.t(self.UID, "user_name_random_gone")])
        self.assertIn("ua_rnd", self._last_edit(rec)[1])
        self.assertNotIn("name", ctx.user_data["flow"]["data"])
        # без мастера — «ожидание истекло» и возврат к пользователям
        for data in ("ua_rnd", "ua_take~foggy_london"):
            rec, _ = self._press(data, FakeContext())
            self.assertTrue(any(c[2].get("show_alert") for c in rec.calls if c[0] == "answer"), data)
            text, datas, _ = self._last_edit(rec)
            self.assertIn(i18n.t(self.UID, "user_add_lost"), text)
            self.assertEqual(datas, ["menu_users"])
        self.assertEqual(self._bridge("name-suggest"), [], "без мастера мост не зовём")
        # сбой моста на «🎲 Ещё» — прежний вариант остаётся на экране, причина — в самом сообщении
        rec, ctx = self._press("u_add")
        self._press("ua_rnd", ctx)
        os.environ["FAKE_BOT_STATUS"] = "error"
        try:
            rec, ctx = self._press("ua_rnd", ctx)
        finally:
            os.environ.pop("FAKE_BOT_STATUS", None)
        self.assertEqual([c[1][0] for c in rec.calls if c[0] == "answer"], [None])
        text, datas, _ = self._last_edit(rec)
        self.assertIn(i18n.tf(self.UID, "user_name_random_failed_fmt", "forced_error"), text)
        self.assertIn("Туманный Лондон", text)
        self.assertIn("ua_take~foggy_london", datas)
        self.assertFalse(bot._NAME_SUGGEST["unsupported"], "обычный сбой кнопку не прячет")

    def test_buttons_of_the_name_step_do_nothing_later(self):
        """HB-2: «🎲» и «✓ Взять» остаются живыми под старым сообщением. Мастер уже дальше (имя
        отправили текстом или взяли вариант, двойной тап) — алерт «Имя уже выбрано», имя и шаг не
        меняются, мост не зовётся."""
        rec, ctx = self._press("u_add")
        self._press("ua_rnd", ctx)                                # foggy_london на экране
        self._say("stepper", ctx)                                 # а имя отправили сами
        calls = len(self._bridge("name-suggest"))
        for data in ("ua_rnd", "ua_take~foggy_london"):
            rec, ctx = self._press(data, ctx)
            self.assertEqual([c[0] for c in rec.calls], ["answer"], data)
            self.assertEqual(rec.calls[0][1][0], i18n.t(self.UID, "user_name_already_chosen"), data)
            self.assertTrue(rec.calls[0][2].get("show_alert"), data)
            data_now = ctx.user_data["flow"]["data"]
            self.assertEqual((data_now["name"], data_now["step"]), ("stepper", "profiles"), data)
            self.assertNotIn("label", data_now)
        self.assertEqual(len(self._bridge("name-suggest")), calls, "мост не звали")
        # двойной тап «✓ Взять»: второе нажатие имя не переписывает
        rec, ctx = self._press("u_add")
        self._press("ua_rnd", ctx)
        self._press("ua_take~sunny_manila", ctx)
        rec, ctx = self._press("ua_take~sunny_manila", ctx)
        self.assertEqual(rec.calls[0][1][0], i18n.t(self.UID, "user_name_already_chosen"))
        self.assertEqual(ctx.user_data["flow"]["data"]["name"], "sunny_manila")
        # «Назад» к имени — кнопки снова работают
        self._press("ua_back", ctx)
        rec, ctx = self._press("ua_rnd", ctx)
        self.assertIn("ua_rnd", self._last_edit(rec)[1])

    def test_answer_first_and_another_name_on_again(self):
        """HB-11: ответ на нажатие — сразу (до моста, таймаут ядра короткий); «🎲 Ещё» просит ядро не
        повторять показанный вариант (--exclude) и, если выпал тот же, спрашивает ещё раз; ядро без
        --exclude (bad_arg) спрашивается без него."""
        self.assertLessEqual(bot.NAME_SUGGEST_TIMEOUT, 10)
        order = []
        orig_answer, orig_action = FakeQuery.answer, bot.run_bot_action

        async def answer(query, text=None, show_alert=False, **kw):
            order.append("answer")
            await orig_answer(query, text, show_alert=show_alert, **kw)

        async def action(name, *a, **kw):
            order.append(name)
            return await orig_action(name, *a, **kw)

        FakeQuery.answer, bot.run_bot_action = answer, action
        try:
            rec, ctx = self._press("u_add")
            order.clear()
            self._press("ua_rnd", ctx)
            self.assertEqual(order[:2], ["answer", "name-suggest"], "ответ на нажатие — до моста")
        finally:
            FakeQuery.answer, bot.run_bot_action = orig_answer, orig_action
        self.assertFalse([a for a in self._bridge("name-suggest")[-1] if a.startswith("--exclude")],
                         "первый вариант — без исключений")
        rec, ctx = self._press("ua_rnd", ctx)
        self.assertIn("--exclude=foggy_london", self._bridge("name-suggest")[-1])
        self.assertIn("ua_take~sunny_manila", self._last_edit(rec)[1])
        # ядро, которое всё время предлагает одно и то же: ещё одна попытка, и не больше
        os.environ["FAKE_BOT_NAME_SUGGEST"] = "repeat"
        self.addCleanup(os.environ.pop, "FAKE_BOT_NAME_SUGGEST", None)
        rec, ctx = self._press("u_add")
        self._press("ua_rnd", ctx)
        before = len(self._bridge("name-suggest"))
        rec, ctx = self._press("ua_rnd", ctx)
        self.assertEqual(len(self._bridge("name-suggest")) - before, 2, "совпало с показанным — одна повторная попытка")
        self.assertIn("ua_take~foggy_london", self._last_edit(rec)[1])
        # ядро с name-suggest, но без --exclude: bad_arg — тот же вызов без параметра
        os.environ["FAKE_BOT_NAME_SUGGEST"] = "noexclude"
        rec, ctx = self._press("u_add")
        self._press("ua_rnd", ctx)
        before = len(self._bridge("name-suggest"))
        rec, ctx = self._press("ua_rnd", ctx)
        calls = self._bridge("name-suggest")[before:]
        self.assertEqual(len(calls), 2)
        self.assertTrue([a for a in calls[0] if a.startswith("--exclude=")], calls[0])
        self.assertFalse([a for a in calls[1] if a.startswith("--exclude")], "повтор — без --exclude")
        text, datas, _ = self._last_edit(rec)
        self.assertNotIn("❌", text)
        self.assertTrue(datas[0].startswith("ua_take~"))


# ════════════════════════════════════════════════════════════════════════════
# T22 (состояния профиля), T30 (служебные строки), 443 в «VPN и сайт», установка
# ════════════════════════════════════════════════════════════════════════════

class ProfileStateTests(unittest.TestCase):
    """T22: пропавший и перезапускающийся контейнер бот называл «не установлен» — и человек шёл
    ставить профиль заново (мост отвечал «уже установлен»). Теперь как в SSH."""

    def test_missing_and_restarting_are_named_like_ssh(self):
        self.assertEqual(bot.profile_icon("missing"), "✗")
        self.assertEqual(bot.profile_icon("restarting"), "↻")
        self.assertEqual(bot.profile_state_key("missing"), "state_missing")
        self.assertEqual(bot.profile_state_key("restarting"), "state_restarting")
        self.assertEqual(bot.profile_state_key("something_new"), "state_missing")
        status = {"status": "success", "profiles": [
            {"profile": "awg2", "state": "missing", "port": 443, "line": "2.0"},
            {"profile": "awg3", "state": "restarting", "port": 39001, "line": "3.1"}]}
        text = bot.status_report_text(None, status)
        self.assertIn(i18n.t(None, "state_missing"), text)
        self.assertIn(i18n.t(None, "state_restarting"), text)
        self.assertNotIn(i18n.t(None, "not_installed"), text, "установленный профиль — не «не установлен»")
        card = bot.profile_card_text(None, "awg2", status, {})
        self.assertIn("✗ " + i18n.t(None, "state_missing"), card)
        self.assertIn(i18n.t(None, "pr_missing_hint"), card)
        card = bot.profile_card_text(None, "awg1", status, {})
        self.assertIn(bot.PROFILE_NOT_INSTALLED_ICON + " " + i18n.t(None, "not_installed"), card)
        self.assertNotIn(i18n.t(None, "pr_missing_hint"), bot.profile_card_text(None, "awg3", status, {}))
        # легенда называет всё, что рисует бот, — как legend_profiles в SSH
        for lang in ("ru", "en"):
            legend = i18n.t_in(lang, "pr_legend")
            for icon in ("●", "↻", "○", "✗", "⚠", bot.PROFILE_NOT_INSTALLED_ICON):
                self.assertIn(icon, legend, lang)


class ProfileScreensTests(_BotScreenCase):
    """Карточка профиля: «Поднять контейнер заново» (profile-repair, как в SSH), список протоколов
    без «✗» у неустановленного, предупреждение про UDP/443 в «VPN и сайт», первая установка."""

    def setUp(self):
        self.args_file = os.path.join(TMP_ROOT, "profile_screens_args.txt")
        open(self.args_file, "w").close()
        os.environ["FAKE_BOT_ARGS_FILE"] = self.args_file
        self.addCleanup(os.environ.pop, "FAKE_BOT_ARGS_FILE", None)
        self.addCleanup(bot.invalidate_status)

    def _calls(self, action):
        with open(self.args_file, encoding="utf-8") as fh:
            return [line for line in fh.read().splitlines() if "--action=%s\t" % action in line]

    def _mode(self, mode):
        os.environ["FAKE_BOT_MODE"] = mode
        self.addCleanup(os.environ.pop, "FAKE_BOT_MODE", None)
        bot.invalidate_status()

    def test_card_brings_the_container_back_after_a_question(self):
        text, datas, _ = self._screen("pr_card~awg2")
        self.assertIn("pr_fix~awg2", datas)
        text, datas, _ = self._screen("pr_fix~awg2")
        self.assertIn("pr_fix_do~awg2", datas)
        self.assertIn("pr_card~awg2", datas)
        self.assertEqual(self._calls("profile-repair"), [], "вопрос ничего не делает")
        text, datas, _ = self._screen("pr_fix_do~awg2")
        self.assertEqual(len(self._calls("profile-repair")), 1)
        self.assertIn("--profile=awg2\t", self._calls("profile-repair")[0])
        self.assertIn("pr_card~awg2", datas)
        self.assertIs(bot.resolve_handler("pr_fix_do~awg2"), bot.cb_pr_repair_do)
        self.assertEqual(bot.BRIDGE_TIMEOUT_BY_ACTION["profile-repair"], 900)

    def test_protocols_list_marks_the_not_installed_profile_apart(self):
        rec, _ = self._press("sec_protocols")
        edit = [c for c in rec.calls if c[0] == "edit"][-1]
        labels = {b.callback_data: b.text for row in edit[2]["reply_markup"].inline_keyboard for b in row}
        self.assertTrue(labels["pr_card~awg1"].startswith(bot.PROFILE_NOT_INSTALLED_ICON), labels["pr_card~awg1"])
        self.assertTrue(labels["pr_card~awg2"].startswith("●"))

    def test_vpn_on_443_in_vpn_and_site_mode_is_warned_about(self):
        """Критик 24.09, п. 11: свой порт 443 и «Занять UDP/443» в «VPN и сайт» проходили молча, а шапка
        тут же ругалась, что режим и порт не совпадают."""
        warn = i18n.t(self.UID, "pr_443_site_mode_warn")
        self._mode("lite_site")
        text, _, _ = self._screen("pr_443~awg3")
        self.assertIn(warn, text)
        _, ctx = self._press("pr_port~awg3")
        rec = self._say("443", ctx)
        self.assertIn(warn, str([c for c in rec.calls if c[0] == "reply_text"][-1][1][0]))
        _, ctx = self._press("pr_inst_port~awg1")
        rec = self._say("0443", ctx)
        self.assertIn(warn, str([c for c in rec.calls if c[0] == "reply_text"][-1][1][0]))
        _, ctx = self._press("pr_port~awg3")
        rec = self._say("5443", ctx)
        self.assertNotIn(warn, str([c for c in rec.calls if c[0] == "reply_text"][-1][1][0]))
        # в «Только VPN» UDP/443 — обычный порт, предупреждать не о чем
        self._mode("lite")
        text, _, _ = self._screen("pr_443~awg3")
        self.assertNotIn(warn, text)

    def test_first_install_screen_leads_with_the_recommended_button(self):
        """Критик 24.09, п. 7: с главного экрана «порт по умолчанию» и «UDP/443» стояли на равных,
        и 443 казался лучшим. Первой — «Установить (рекомендуется)», объяснение — без 1024–9999."""
        rec, _ = self._press("menu_install")
        edit = [c for c in rec.calls if c[0] == "edit"][-1]
        rows = edit[2]["reply_markup"].inline_keyboard
        self.assertEqual(rows[0][0].callback_data, "inst_awg2~def")
        self.assertEqual(rows[0][0].text, i18n.t(self.UID, "install_btn_awg2"))
        self.assertNotIn("1024", str(edit[1][0]))
        self.assertIn("inst_awg2~443", [b.callback_data for row in rows for b in row], "в «Только VPN» 443 — обычный порт")
        # «VPN и сайт»: VPN на UDP/443 — признак другого режима, кнопки нет (как в карточке профиля)
        self._mode("lite_site")
        rec, _ = self._press("menu_install")
        edit = [c for c in rec.calls if c[0] == "edit"][-1]
        datas = [b.callback_data for row in edit[2]["reply_markup"].inline_keyboard for b in row]
        self.assertEqual(datas, ["inst_awg2~def", "menu_main"])


class HumanWordsTests(_BotScreenCase):
    """T30 и правило владельца «без жаргона»: на экранах — слова, а не имена файлов, сетей и ключей."""

    def test_about_names_the_guide_not_repository_files(self):
        text, _, _ = self._screen("se_about")
        for raw in ("docs/", "CONTRACT.md", "PLAN.md"):
            self.assertNotIn(raw, text)
        self.assertIn(i18n.t(self.UID, "about_docs_url"), text)

    def test_wipe_names_the_ssh_menu_items_by_their_numbers(self):
        text, _, _ = self._screen("se_wipe")
        self.assertIn(i18n.t(self.UID, "wipe_ssh_path"), text)
        self.assertNotIn(i18n.t(self.UID, "menu_settings"), text, "подписи кнопок бота с эмодзи — не путь в SSH")
        for lang in ("ru", "en"):
            path = i18n.t_in(lang, "wipe_ssh_path")
            self.assertIn("5 ", path)
            self.assertIn("7 ", path)

    def test_diagnostics_speak_words(self):
        r = run(bot.run_bot_action("quality", timeout=20))
        text = bot.diag_text(None, r)
        for raw in ("ip_forward", "amnezia-dns-net"):
            self.assertNotIn(raw, text)
        for key in ("diag_docker_ok", "diag_forward_on", "diag_net_ok"):
            self.assertIn(i18n.t(None, key), text)
        self.assertIn(i18n.tf(None, "diag_disk_fmt", 12345), text)
        bad = bot.diag_text(None, {"status": "success", "quality": {
            "docker": False, "ip_forward": "0", "network": False, "disk_free_mb": 5000,
            "docker_info": {"installed": True, "reachable": False}}})
        for key in ("diag_forward_off", "diag_net_bad"):
            self.assertIn(i18n.t(None, key), bad)
        self.assertEqual(bad.count(i18n.t(None, "diag_docker_unreachable")), 1, "«демон не отвечает» — одной строкой")

    def test_site_check_without_a_site_says_only_that(self):
        text, datas, _ = self._screen("se_check")             # заглушка по умолчанию — «Только VPN»
        self.assertIn(i18n.t(self.UID, "site_verdict_not_pro"), text)
        self.assertNotIn(i18n.t(self.UID, "site_check_nginx"), text)
        self.assertNotIn("HTTPS", text)
        self.assertIn("se_mode", datas)

    def test_no_jargon_in_russian_strings(self):
        with open(os.path.join(BOT_SRC, "lang", "ru.json"), encoding="utf-8") as fh:
            ru = json.load(fh)
        bad = sorted(k for k, v in ru.items()
                     if re.search(r"(?<![а-яё])(пир|пиры|пиров|пирам|пирами|пирах)(?![а-яё])", v, re.I)
                     or re.search(r"(?<![а-яё])мост(?![а-яё])", v, re.I) or "Endpoint" in v)
        self.assertEqual(bad, [], "человеку — «ключ», «сервер», «адрес», а не «пир», «мост», «Endpoint»")


# ════════════════════════════════════════════════════════════════════════════
# 0.5.0: бот по дизайну handoff/iteration3/Telegram.dc.html (docs/REDESIGN_050.md, строка «Бот»)
# ════════════════════════════════════════════════════════════════════════════

class _HeldLock:
    """Замок бота, занятый другой операцией: locked() — да, войти можно (очередь дошла)."""

    def locked(self):
        return True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class Busy050Tests(_BotScreenCase):
    """«Занято» — как в продукте (DS2-21/50): busy у кнопки, queued у ввода и фразы, busy-lock у
    замка SSH, панели или агента — без имени и длительности чужого действия."""

    def _bridge(self, answers, calls=None):
        """run_bot_action: действие из answers — его ответ (список — по очереди), остальное — заглушка."""
        saved = bot.run_bot_action

        async def fake(action, *a, **kw):
            if calls is not None:
                calls.append(action)
            if action in answers:
                got = answers[action]
                return dict(got.pop(0) if isinstance(got, list) else got)
            return await saved(action, *a, **kw)
        bot.run_bot_action = fake
        self.addCleanup(setattr, bot, "run_bot_action", saved)

    def _hold_lock(self):
        saved = bot._BOT_ACTION_LOCK
        bot._BOT_ACTION_LOCK = _HeldLock()
        self.addCleanup(setattr, bot, "_BOT_ACTION_LOCK", saved)

    LOCK = {"status": "error", "code": "lock_timeout", "message": "", "action": "x"}

    def test_lock_of_ssh_panel_or_agent_is_a_wait_with_retry(self):
        self._bridge({"user-disable": self.LOCK})
        text, datas, _ = self._screen("u_en~anna~off")
        self.assertIn(i18n.t(self.UID, "err_lock_timeout"), text)
        self.assertTrue(text.startswith("<b>⏳ "), "«занято» — ожидание, а не ошибка: %r" % text)
        self.assertNotIn("❌", text)
        self.assertEqual(datas, ["u_en~anna~off", "u_view~anna"], "«🔁 Повторить» — та же кнопка, ниже «Назад»")
        for lang in ("ru", "en"):
            self.assertIn("🔁", i18n.t_in(lang, "btn_retry"))   # 🔄 — только «Обновить» (находка 25)
        # прочие отказы — прежние: ❌ и только «Назад»
        self._bridge({"user-disable": {"status": "error", "code": "user_not_found", "message": "нет"}})
        text, datas, _ = self._screen("u_en~anna~off")
        self.assertTrue(text.startswith("❌ "))
        self.assertEqual(datas, ["u_view~anna"])

    def test_retry_after_lock_runs_the_same_button_once_more(self):
        """Кнопки с защитой от двойного нажатия (_once_callback): lock_timeout — мост ничего не
        сделал, и «Повторить» (та же callback_data в том же сообщении) обязан сработать."""
        calls = []
        self._bridge({"agent-rotate": [self.LOCK, {"status": "success", "id": "a-1a2b", "url": "https://x/a-S/mcp",
                                                   "token": "gat_x", "mode": "static"}]}, calls)
        ctx = FakeContext()
        upd = FakeUpdate(self.UID, data="ag_rot_do~a-1a2b")
        upd.callback_query.message.message_id = 77
        run(bot.handle_callback(upd, ctx))
        edits = [c for c in upd.rec.calls if c[0] == "edit"]
        datas = [b.callback_data for row in edits[-1][2]["reply_markup"].inline_keyboard for b in row]
        self.assertIn("ag_rot_do~a-1a2b", datas)
        upd2 = FakeUpdate(self.UID, data="ag_rot_do~a-1a2b")
        upd2.callback_query.message.message_id = 77
        run(bot.handle_callback(upd2, ctx))
        self.assertEqual(calls.count("agent-rotate"), 2, "повтор после «занято» не дошёл до моста")

    def test_no_retry_for_buttons_bound_to_typed_input(self):
        for data in bot.FLOW_BOUND_CALLBACKS:
            self.assertIsNone(bot.retry_data(type("Q", (), {"data": data})()), data)
        markup = bot.fail_markup(self.UID, self.LOCK, "pr_params~awg2", None)
        self.assertEqual([b.callback_data for row in markup.inline_keyboard for b in row], ["pr_params~awg2"])

    def test_button_during_bot_operation_is_busy_not_silent(self):
        self._hold_lock()
        for data, back in (("uq_set~anna~50", "u_view~anna"), ("ue_set~anna~30", "u_view~anna"),
                           ("pr_mtu_set~awg2~1376", "pr_card~awg2")):
            calls = []
            self._bridge({}, calls)
            text, datas, _ = self._screen(data)
            self.assertIn(i18n.t(self.UID, "action_busy_title"), text, data)
            self.assertIn(i18n.t(self.UID, "action_busy_body"), text, data)
            self.assertEqual(datas, [back], data)
            self.assertEqual([c for c in calls if c not in bot.READONLY_ACTIONS], [], "%s: мост не зовётся" % data)

    def test_typed_input_during_bot_operation_is_queued(self):
        self._hold_lock()
        args = os.path.join(TMP_ROOT, "queued050_args.txt")
        os.environ["FAKE_BOT_ARGS_FILE"] = args
        self.addCleanup(os.environ.pop, "FAKE_BOT_ARGS_FILE", None)
        for screen, typed, want in (("u_quota~anna", "50", "--quota-gb=50"), ("u_exp~anna", "30", "--days=30"),
                                    ("u_ren~anna", "Анна К", "--label=Анна К"),
                                    # 1320 — совет для проводного: без предупреждения (1400 + 80 больше 1440 — уже
                                    # вопрос «Сохранить всё равно?», а не очередь, L-02а)
                                    ("pr_mtu~awg2", "1320", "--mtu=1320")):
            _, ctx = self._press(screen)
            rec = self._say(typed, ctx)
            texts = [str(c[1][0]) for c in rec.calls if c[0] == "reply_text"]
            self.assertEqual(texts[0], i18n.t(self.UID, "action_queued"), screen)
            self.assertNotIn(i18n.t(self.UID, "action_busy_body"), "\n".join(texts), screen)
            with open(args, encoding="utf-8") as fh:
                self.assertIn(want, fh.read(), "%s: действие не отменено, оно выполнено" % screen)


# Голый служебный id профиля в тексте: не в скобках заголовка «(awg3)», не в имени контейнера
# amnezia-awg3 или файла anna-awg3.conf.
_BARE_ID = re.compile(r"(?<![(\-/\w])awg[123](?![)\w.-])")


class ProfileLines050Tests(_BotScreenCase):
    """DS2-55: в текстах бота — линии (AmneziaWG 3.1 / 2.0 / 1.5, на кнопках коротко 3.1 / 2.0 / 1.5);
    id остаются в фразах подтверждения, командах, именах контейнеров и файлов."""

    SCREENS = ("sec_protocols", "pr_card~awg2", "pr_card~awg3", "pr_card~awg1", "pr_install~awg1",
               "pr_port~awg2", "pr_443~awg3", "pr_subnet~awg2", "pr_mtu~awg2", "pr_params~awg2",
               "pr_pedit~awg2", "pr_preset~awg2", "pr_restart~awg2", "pr_fix~awg2", "st_status", "st_addr",
               "st_logs", "st_log~awg2", "u_view~anna", "u_view~proba", "u_addp~bob", "u_delp~anna",
               "u_delp_ask~anna~awg2", "u_reis~proba~awg2", "u_att~proba~awg2", "u_share~anna",
               "u_shf~anna~awg2", "u_page~proba", "mt_upstream", "mt_import", "imp_rec~awg2", "menu_main",
               "mt_restart_all_do")

    def test_screens_name_profiles_by_line(self):
        for data in self.SCREENS:
            rec, _ = self._press(data)
            edits = [c for c in rec.calls if c[0] == "edit"]
            self.assertTrue(edits, data)
            for call in edits:
                text = str(call[1][0])
                self.assertIsNone(_BARE_ID.search(text), "%s: голый id в тексте: %r" % (data, text))
                markup = call[2].get("reply_markup")
                for row in (markup.inline_keyboard if markup is not None else []):
                    for b in row:
                        self.assertIsNone(_BARE_ID.search(b.text), "%s: голый id на кнопке %r" % (data, b.text))

    def test_key_buttons_are_short_lines(self):
        _, datas, _ = self._screen("u_view~anna")
        rec, _ = self._press("u_view~anna")
        labels = [b.text for c in rec.calls if c[0] == "edit" for row in c[2]["reply_markup"].inline_keyboard for b in row]
        for want in ("3.1 · 39001 · " + i18n.t(self.UID, "key_btn_qr_conf"), "2.0 · 443 · " + i18n.t(self.UID, "key_btn_vpn")):
            self.assertIn(want, labels)
        self.assertIn("u_key~anna~awg3~qrconf", datas)          # в callback_data id остаётся

    def test_confirmation_phrase_is_the_port(self):
        """D-5: для удаления отправляют порт профиля (он в подписи «AmneziaWG 2.0 · порт 443»), имени места awg2 нет."""
        text, _, _ = self._screen("pr_remove~awg2")
        self.assertIn(i18n.tf(self.UID, "confirm_type_port_fmt", "443", bot.TYPED_CONFIRM_TTL), text)
        self.assertNotIn("awg2", text)
        self.assertIn("AmneziaWG 2.0", text)

    def test_line_of_awg1_comes_from_status(self):
        status = {"status": "success", "profiles": [{"profile": "awg1", "line": "1.0", "state": "running"}]}
        self.assertEqual(bot.profile_name(None, "awg1", status), "AmneziaWG 1.0")
        self.assertEqual(bot.profile_title(None, "awg1", status), "AmneziaWG 1.0", "порта в status нет — одна линия")
        self.assertEqual(bot.profile_short("awg1", status), "1.0")
        saved = dict(bot._KNOWN_LINES)
        self.addCleanup(lambda: (bot._KNOWN_LINES.clear(), bot._KNOWN_LINES.update(saved)))
        bot.remember_lines(status)
        self.assertEqual(bot.profile_name(None, "awg1"), "AmneziaWG 1.0", "без status — последняя увиденная линия")
        bot._KNOWN_LINES.clear()
        self.assertEqual(bot.profile_name(None, "awg1"), "AmneziaWG 1.5", "ничего не известно — линия свежей установки")
        self.assertEqual(bot.profile_name(None, "amnezia-xray"), "amnezia-xray", "не профиль — как есть")


class ProfileLinesSwappedTests(unittest.TestCase):
    """AWG31-01: место профиля — не линия. Подхваченный из приложения amnezia-awg2 держит 3.1, а 2.0 встала в awg3
    (LZ-02). Кнопки ключа («3.1 · QR .conf») и подписи профилей бот берёт из status (карточка зовёт get_status до
    клавиатуры), а без status — из последнего увиденного: обе линии на своих профилях, а не по месту."""

    STATUS = {"status": "success", "profiles": [{"profile": "awg2", "line": "3.1", "state": "running"},
                                                 {"profile": "awg3", "line": "2.0", "state": "running"}]}

    def test_buttons_and_names_follow_the_line(self):
        self.assertEqual((bot.profile_short("awg2", self.STATUS), bot.profile_short("awg3", self.STATUS)), ("3.1", "2.0"))
        self.assertEqual(bot.profile_name(None, "awg2", self.STATUS), "AmneziaWG 3.1")
        self.assertEqual(bot.profile_title(None, "awg3", self.STATUS), "AmneziaWG 2.0", "без порта и без имени места")
        saved = dict(bot._KNOWN_LINES)
        self.addCleanup(lambda: (bot._KNOWN_LINES.clear(), bot._KNOWN_LINES.update(saved)))
        bot.remember_lines(self.STATUS)
        self.assertEqual((bot.profile_short("awg2"), bot.profile_short("awg3")), ("3.1", "2.0"))
        self.assertEqual(bot.profile_from_arg("3.1", self.STATUS), "awg2")     # «/key anna 3.1» — профиль с линией 3.1


class SubnetOverlapBotTests(_BotScreenCase):
    """SUBNET-01 (жалоба владельца 06.10: «при подхвате старого протокола и попытке добавить новый почему-то
    пересекаются подсети»). Приложение Amnezia ставит свои контейнеры AmneziaWG на одну 10.8.1.0/24, подхват берёт их
    как есть: awg1 — старый amnezia-awg на wg0 (подсеть не сменить), awg2 — amnezia-awg2 приложения, у двоих ключи
    из приложения. Ядро (status.subnet_overlaps) называет выход — перенести awg2 на свободную 10.8.2.0/24. Бот: карточка
    говорит о пересечении и даёт кнопку выхода; экран подсети старого — отказ сразу и та же кнопка; перенос профиля с
    людьми приложения — фразой SUBNET awg2 (adopted_confirm, как перенос порта ACC-2)."""

    MV = {"profile": "awg2", "subnet": "10.8.2.0/24", "keyless": 2}

    def _status(self, keyless=2):
        mv = dict(self.MV, keyless=keyless)
        return {"status": "success", "mode": "lite", "server_ip": "203.0.113.10", "users": 3,
                "profiles": [{"profile": "awg1", "container": "amnezia-awg", "state": "running", "port": 51820,
                              "line": "1.0", "peers": 1, "subnet_fixed": True},
                             {"profile": "awg2", "container": "amnezia-awg2", "state": "running", "port": 38123,
                              "line": "2.0", "peers": 2, "subnet_fixed": False}],
                "subnet_overlaps": [{"profile": "awg1", "subnet": "10.8.1.0/24", "with": ["awg2"], "fixed": True, "move": mv},
                                    {"profile": "awg2", "subnet": "10.8.1.0/24", "with": ["awg1"], "fixed": False, "move": mv}]}

    def _fakes(self, keyless=2):
        self.calls = []
        st = self._status(keyless)

        async def fake_status(fresh=False):
            return json.loads(json.dumps(st))

        async def fake_mutate(action, who=None, timeout=None, **params):
            self.calls.append((action, {k: v for k, v in params.items() if v is not None}))
            if action == "profile-subnet" and keyless and params.get("adopted_confirm") != params.get("profile"):
                return {"status": "error", "code": "adopted_keyless",
                        "message": "Профиль awg2 подхвачен у приложения Amnezia, и у пользователей на нём (2) ключи выдало приложение."}
            return {"status": "success", "message": "ok"}

        for name, fake in (("get_status", fake_status), ("mutate", fake_mutate)):
            saved = getattr(bot, name)
            setattr(bot, name, fake)
            self.addCleanup(setattr, bot, name, saved)
        self.addCleanup(bot.CONFIRM.cancel, self.UID)

    def test_cards_name_the_overlap_and_the_way_out(self):
        self._fakes()
        text, datas, _ = self._screen("pr_card~awg1")
        self.assertIn(i18n.tf(self.UID, "pr_overlap_fmt", "10.8.1.0/24", "AmneziaWG 2.0 · порт 38123", "AmneziaWG 2.0 · порт 38123", "10.8.2.0/24"), text)
        self.assertIn("pr_smv~awg2~10.8.2.0/24", datas)
        self.assertEqual(datas[0], "pr_smv~awg2~10.8.2.0/24", "выход — первой кнопкой карточки")
        text, datas, _ = self._screen("pr_card~awg2")
        self.assertIn(i18n.tf(self.UID, "pr_overlap_keyless_fmt", 2), text)
        self.assertIn("pr_smv~awg2~10.8.2.0/24", datas)

    def test_old_profile_subnet_screen_refuses_at_once_and_leads_out(self):
        self._fakes()
        text, datas, ctx = self._screen("pr_subnet~awg1")
        self.assertIn(i18n.t(self.UID, "pr_subnet_fixed"), text)
        self.assertIn(i18n.tf(self.UID, "pr_subnet_fixed_way_fmt", "10.8.1.0/24", "AmneziaWG 2.0 · порт 38123", "10.8.2.0/24"), text)
        self.assertIn("pr_smv~awg2~10.8.2.0/24", datas)
        self.assertNotIn("pr_subnet_ok", datas)
        self.assertIsNone(bot.flow_get(ctx), "подсеть у старого не спрашиваем — менять ему нечего")
        self.assertEqual(self.calls, [])

    def test_way_out_with_app_people_is_the_phrase(self):
        self._fakes()
        rec, ctx = self._press("pr_smv~awg2~10.8.2.0/24")
        pend = bot.CONFIRM.pending(self.UID)
        self.assertIsNotNone(pend)
        self.assertEqual((pend["kind"], pend["target"], pend["phrase"], pend["extra"]),
                         ("profile-subnet-adopted", "awg2", "38123", {"subnet": "10.8.2.0/24"}))
        self.assertEqual(self.calls, [], "без фразы — ничего")
        self._say("38123", ctx)
        self.assertEqual(self.calls, [("profile-subnet", {"profile": "awg2", "subnet": "10.8.2.0/24", "adopted_confirm": "awg2"})])

    def test_way_out_without_app_people_is_a_button(self):
        self._fakes(keyless=0)
        text, datas, ctx = self._screen("pr_smv~awg2~10.8.2.0/24")
        self.assertIn("pr_smv_do~awg2~10.8.2.0/24", datas)
        self.assertIsNone(bot.CONFIRM.pending(self.UID))
        text, datas, _ = self._screen("pr_smv_do~awg2~10.8.2.0/24")
        self.assertEqual(self.calls, [("profile-subnet", {"profile": "awg2", "subnet": "10.8.2.0/24"})])
        self.assertIn("✅", text)

    def test_own_subnet_of_the_moving_profile_meets_the_phrase_on_app_people(self):
        self._fakes()
        text, datas, ctx = self._screen("pr_subnet~awg2")
        self.assertIn("pr_smv~awg2~10.8.2.0/24", datas, "свободная — одной кнопкой")
        self.assertEqual((bot.flow_get(ctx) or {}).get("kind"), "profile_subnet", "и своя подсеть — по-прежнему текстом")
        self._say("10.8.9.0/24", ctx)
        rec, ctx = self._press("pr_subnet_ok", ctx)
        self.assertEqual(self.calls, [("profile-subnet", {"profile": "awg2", "subnet": "10.8.9.0/24"})])
        pend = bot.CONFIRM.pending(self.UID)
        self.assertEqual((pend["kind"], pend["phrase"], pend["extra"]), ("profile-subnet-adopted", "38123", {"subnet": "10.8.9.0/24"}))
        texts = " ".join(str(c[1][0]) for c in rec.calls if c[0] == "edit")
        self.assertIn("38123", texts)
        self.assertIn(i18n.t(self.UID, "pr_subnet_adopted_note"), texts)


S4_RISK = {"packet": 1502, "budget": 1400, "s4": 142, "safe_s4": 40, "mtu": 1280, "keyless": 0}
S4_STEPS = [{"kind": "restart", "text": "Профиль перезапущен: клиенты переподключатся сами, обычно за 15–25 секунд."},
            {"kind": "subscription", "text": "С подпиской (vera): обновится сама в течение 12 часов или по «Обновить» в приложении."},
            {"kind": "key", "text": "Выдавали ключ или файл (anna): импортировать заново — выдайте ключ vpn:// или файл .conf ещё раз."}]
S4_DONE = "S4 профиля AmneziaWG 2.0 (awg2): 142 → 27, пакет данных 1502 → 1387 байт — укладывается в 1400."


def s4_status(risk=S4_RISK):
    return {"status": "success", "mode": "lite", "server_ip": "203.0.113.10", "users": 3,
            "profiles": [{"profile": "awg3", "container": "amnezia-awg3", "state": "running", "port": 4000, "line": "3.1",
                          "peers": 1, "packet_risk": None},
                         {"profile": "awg2", "container": "amnezia-awg2", "state": "running", "port": 443, "line": "2.0",
                          "peers": 3, "packet_risk": risk}]}


class S4FixWordsTests(unittest.TestCase):
    """S4-FIX (MTU-01) без python-telegram-bot: строка главного экрана, итог одним сообщением, окно MTU — то же правило,
    что у ядра (awgkeys.py mtu_warnings)."""

    UID = 424244

    def test_main_screen_line(self):
        lines, _ = bot.dashboard_problems(self.UID, s4_status(), buttons=False)
        want = "⚠️ " + i18n.tf(self.UID, "s4_risk_fmt", FX20, 142)
        self.assertIn(want, lines)
        self.assertIn("скачивание не идёт — слишком большие пакеты (S4 = 142)", want)
        self.assertIn(want, bot.dashboard_text(self.UID, s4_status()))
        for risk in (None, "risk", {}, dict(S4_RISK, s4="142"), dict(S4_RISK, keyless=True)):
            self.assertEqual(bot.dashboard_problems(self.UID, s4_status(risk), buttons=False), ([], []), risk)
        for lang in ("ru", "en"):
            self.assertIn("S4 = 142", i18n.t_in(lang, "s4_risk_fmt") % ("AmneziaWG 2.0", 142), lang)

    def test_result_is_one_message_with_who_does_what(self):
        text = bot.s4_fix_result_text(self.UID, {"status": "success", "message": S4_DONE, "steps": S4_STEPS + ["junk", {"text": ""}]})
        self.assertEqual(text, "✅ " + S4_DONE + "\n\n" + "\n".join("• " + bot.esc(s["text"]) for s in S4_STEPS))
        self.assertTrue(bot.s4_fix_result_text(self.UID, {"status": "success"}).startswith("✅ "))

    def test_mtu_warnings_follow_the_core(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("awgkeys_s4bot", os.path.join(ROOT, "lib", "awgkeys.py"))
        awg = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(awg)
        for s4 in (0, 12, 40, 142):
            for mtu in range(1280, 1501, 2):
                got = bot.mtu_warnings(self.UID, mtu, s4)
                want = awg.mtu_warnings({"S4": str(s4) if s4 else ""}, mtu)
                self.assertEqual(len(got), len(want), (mtu, s4, got, want))
        self.assertEqual(bot.mtu_warnings(self.UID, 1500, 40), [i18n.tf(self.UID, "pr_mtu_warn_path_fmt", 1620)])
        self.assertEqual(bot.mtu_warnings(self.UID, 1330, 40), [i18n.tf(self.UID, "pr_mtu_warn_path_fmt", 1450)])
        self.assertEqual(i18n.tf(self.UID, "pr_mtu_warn_path_fmt", 1450),
                         "Пакет 1450 байт: на части хостеров и сетей скачивание встанет — оставьте 1320 или меньше.")
        self.assertEqual(bot.mtu_warnings(self.UID, 1320, 40), [], "1320 при S4 ≤ 40 — совет для проводного, без слов")
        self.assertEqual(bot.mtu_warnings(self.UID, 1280, 142), [], "не выше 1280 за пакет отвечает «Исправить S4»")
        self.assertEqual(bot.MTU_PRESETS, (1280, 1320))
        # L-02б: ниже 1280 — не число из диапазона, а отказ своими словами.
        self.assertEqual([bot.valid_mtu(v) for v in ("1279", "1280", "1500", "1501")], [False, True, True, False])
        self.assertEqual([bot.mtu_too_low(v) for v in ("1279", "1200", "576", "1280", "abc", "")],
                         [True, True, True, False, False, False])


class S4FixBotTests(_BotScreenCase):
    """S4-FIX (MTU-01, решение владельца 08.10): выданный профиль 2.0 с большим S4 (status.profiles[].packet_risk). Бот:
    строка и кнопка на главном экране, предупреждение и кнопка «Исправить S4» — первой — на экране профиля, вопрос с
    тем же текстом, что в SSH и панели; люди приложения без ключа на сервере — фраза S4 awg2 (adopted_confirm, ACC-2);
    итог одним сообщением — кому что делать. Окно MTU: «1280 — стандарт…», «↩️ Вернуть 1280», предупреждения до записи."""

    SUBS_UPDATE = {"update_interval": 12, "update_slow": [{"name": "FlClash", "hours": 24}]}

    def _fakes(self, risk=S4_RISK, late_keyless=False, cfg=None, subs_update=SUBS_UPDATE, status_hook=None):
        self.calls = []
        self.status_calls = 0
        st = s4_status(risk)
        if subs_update is not None:
            st["subs_update"] = subs_update

        async def fake_status(fresh=False):
            self.status_calls += 1
            if status_hook is not None:
                await status_hook()
            return json.loads(json.dumps(st))

        async def fake_mutate(action, who=None, timeout=None, **params):
            self.calls.append((action, {k: v for k, v in params.items() if v is not None}))
            keyless = (risk or {}).get("keyless") or late_keyless
            if action == "profile-fix-s4" and keyless and params.get("adopted_confirm") != params.get("profile"):
                return {"status": "error", "code": "adopted_keyless",
                        "message": "Профиль awg2 подхвачен у приложения Amnezia, и у пользователей на нём (1) ключи выдало приложение."}
            if action == "profile-fix-s4":
                return {"status": "success", "message": S4_DONE, "s4_old": 142, "s4": 27, "steps": S4_STEPS}
            return {"status": "success", "message": "ok"}

        # R9-1: срок подписки в окне — из status.subs_update (совет сервера и update_slow), без sub-list: тот обходил все
        # подписки. Любое чтение мимо status окно не зовёт.
        self.reads = []

        async def fake_read(action, timeout=None, **params):
            self.reads.append((action, params))
            return {"status": "error", "code": "unexpected", "message": action}

        fakes = [("get_status", fake_status), ("mutate", fake_mutate), ("run_bot_action", fake_read)]
        if cfg is not None:
            fakes.append(("read_config_display", lambda: json.loads(json.dumps(cfg))))
        for name, fake in fakes:
            saved = getattr(bot, name)
            setattr(bot, name, fake)
            self.addCleanup(setattr, bot, name, saved)
        self.addCleanup(bot.CONFIRM.cancel, self.UID)

    def test_main_screen_has_the_line_and_the_button_above_sections(self):
        self._fakes()
        text, datas, _ = self._screen("menu_main")
        self.assertIn(i18n.tf(self.UID, "s4_risk_fmt", FX20, 142), text)
        self.assertIn("pr_s4~awg2", datas)
        self.assertLess(datas.index("pr_s4~awg2"), datas.index("menu_users"), "быстрое действие — над разделами")

    def test_profile_screen_warns_and_the_fix_comes_first(self):
        self._fakes()
        text, datas, _ = self._screen("pr_card~awg2")
        self.assertIn("⚠️ " + i18n.tf(self.UID, "s4_risk_fmt", FX20, 142), text)
        self.assertEqual(datas[0], "pr_s4~awg2")
        text, datas, _ = self._screen("pr_card~awg3")
        self.assertNotIn("pr_s4~awg3", datas)
        self.assertNotIn("S4 = ", text)

    def test_question_then_fix_in_one_message(self):
        self._fakes()
        text, datas, _ = self._screen("pr_s4~awg2")
        self.assertIn(i18n.tf(self.UID, "pr_s4_title_fmt", "AmneziaWG 2.0"), text)
        self.assertIn(i18n.tf(self.UID, "pr_s4_note_fmt", 1400, "до 12 ч, у FlClash — до 24 ч"), text)
        # K5-2 (S4-MARGIN): подбирается 12–24, пакет не больше 1384 — так и сказано, а не «не больше 40»
        self.assertIn("S4 станет 12–24 — при MTU 1280 пакет данных не больше 1384 байт, с запасом до 1400.", text)
        self.assertNotIn("не больше 40", text)
        # R8-2: окно говорит о подписке словами итога и окон перевыпуска, а не «в течение 12 часов»
        self.assertIn("подтянут новые настройки сами (до 12 ч, у FlClash — до 24 ч) или по «Обновить»", text)
        self.assertNotIn("в течение 12 часов", text)
        self.assertEqual(self.reads, [], "срок подписки — из status, sub-list не зовётся")
        self.assertEqual(self.status_calls, 1)
        self.assertEqual(datas, ["pr_s4_do~awg2", "pr_card~awg2"])
        self.assertEqual(self.calls, [], "вопрос — без моста")
        text, datas, _ = self._screen("pr_s4_do~awg2")
        self.assertEqual(self.calls, [("profile-fix-s4", {"profile": "awg2"})])
        self.assertEqual(text, bot.s4_fix_result_text(self.UID, {"message": S4_DONE, "steps": S4_STEPS}))
        self.assertIn("• С подпиской (vera): обновится сама в течение 12 часов", text)
        self.assertEqual(datas, ["pr_card~awg2"])

    def test_checking_shows_at_once_and_a_second_press_does_not_ask_again(self):
        # R9-1: пока идёт свежий status, окно уже говорит «Проверяю…» без кнопок; второе нажатие в это время второго
        # запроса не запускает и экран не трогает, только всплывающее «Проверяю…». После ответа замок снят — следующее нажатие работает.
        second = {}

        async def press_again():
            if "rec" in second:
                return
            upd = FakeUpdate(self.UID, data="pr_s4~awg2")
            second["rec"] = upd.rec
            await bot.handle_callback(upd, FakeContext())

        self._fakes(status_hook=press_again)
        rec, _ = self._press("pr_s4~awg2")
        edits = [c for c in rec.calls if c[0] == "edit"]
        self.assertEqual(str(edits[0][1][0]), "⏳ " + i18n.tf(self.UID, "pr_s4_checking_fmt", bot.profile_name(self.UID, "awg2")))
        self.assertIsNone(edits[0][2].get("reply_markup"))
        self.assertIn(i18n.tf(self.UID, "pr_s4_note_fmt", 1400, "до 12 ч, у FlClash — до 24 ч"), str(edits[-1][1][0]))
        self.assertEqual(self.status_calls, 1, "второе нажатие status не зовёт")
        self.assertEqual([c for c in second["rec"].calls if c[0] == "edit"], [], "второе нажатие экран не трогает")
        self.assertEqual([c[1][0] for c in second["rec"].calls if c[0] == "answer"],
                         [i18n.tf(self.UID, "pr_s4_checking_fmt", bot.profile_name(self.UID, "awg2"))], "а всплывающее «Проверяю…»")
        self.assertEqual(bot._PR_S4_OPENING, set())
        self._screen("pr_s4~awg2")
        self.assertEqual(self.status_calls, 2)
        self.assertEqual(self.reads, [])

    def test_double_press_while_telegram_answers_asks_status_once(self):
        # R9-1-A: второе нажатие приходит, пока первое ждёт ответа Telegram на answer() (concurrent_updates) — ключ
        # записан до первого await, второго status нет; после обоих нажатий замок снят.
        self._fakes()

        async def slow_answer(q, text=None, show_alert=False, **kw):
            await asyncio.sleep(0.05)  # обмен с Telegram
            q.rec.record("answer", text, show_alert=show_alert)

        saved = FakeQuery.answer
        FakeQuery.answer = slow_answer
        self.addCleanup(setattr, FakeQuery, "answer", saved)
        u1, u2 = FakeUpdate(self.UID, data="pr_s4~awg2"), FakeUpdate(self.UID, data="pr_s4~awg2")

        async def both():
            await asyncio.gather(bot.handle_callback(u1, FakeContext()), bot.handle_callback(u2, FakeContext()))

        run(both())
        self.assertEqual(self.status_calls, 1)
        self.assertEqual([c[1][0] for c in u2.rec.calls if c[0] == "answer"],
                         [i18n.tf(self.UID, "pr_s4_checking_fmt", bot.profile_name(self.UID, "awg2"))])
        self.assertEqual([c for c in u2.rec.calls if c[0] == "edit"], [], "второе нажатие экран не трогает")
        self.assertEqual(bot._PR_S4_OPENING, set())

    def test_unexpected_error_shows_error_with_back_not_checking(self):
        # R9-1-B: исключение внутри окна — экран «Внутренняя ошибка» с «Назад» к профилю, а не «Проверяю…» без кнопок;
        # замок снят, следующее нажатие работает.
        async def boom():
            raise RuntimeError("boom")

        self._fakes(status_hook=boom)
        with self.assertLogs(bot.logger, level="ERROR"):
            text, datas, _ = self._screen("pr_s4~awg2")
        self.assertEqual(text, "❌ " + i18n.t(self.UID, "internal_error"))
        self.assertEqual(datas, ["pr_card~awg2"])
        self.assertEqual(bot._PR_S4_OPENING, set())
        self._fakes()
        text, datas, _ = self._screen("pr_s4~awg2")
        self.assertEqual(datas, ["pr_s4_do~awg2", "pr_card~awg2"])

    def test_subs_update_missing_or_junk_says_up_to_12_hours(self):
        # R9-4 (бот): поля нет, null, мусор вместо чисел и списка — «до 12 ч», окно открывается, мост не зовётся.
        want = i18n.tf(self.UID, "pr_s4_note_fmt", 1400, "до 12 ч")
        for su in (None, "junk", [], {}, {"update_interval": "12", "update_slow": "FlClash"}, {"update_interval": True},
                   {"update_interval": 0, "update_slow": [None, "x", {"name": "", "hours": 24}, {"name": "A", "hours": "24"}]}):
            self._fakes(subs_update=su)
            text, datas, _ = self._screen("pr_s4~awg2")
            self.assertIn(want, text, su)
            self.assertEqual(datas, ["pr_s4_do~awg2", "pr_card~awg2"], su)
            self.assertEqual(self.reads, [], su)
        self._fakes(subs_update={"update_interval": 6, "update_slow": [{"name": "FlClash", "hours": 24}]})
        text, _, _ = self._screen("pr_s4~awg2")
        self.assertIn(i18n.tf(self.UID, "pr_s4_note_fmt", 1400, "до 6 ч, у FlClash — до 24 ч"), text)

    def test_status_error_or_timeout_shows_the_error_and_frees_the_window(self):
        # R9-4 (бот): status не ответил (ошибка, тайм-аут) — экран ошибки, а не вечное «Проверяю…»; замок снят.
        for r in ({"status": "error", "code": "timeout", "message": "timeout"},
                  {"status": "error", "code": "bridge_failed", "message": "boom"}):
            self._fakes()

            async def bad_status(fresh=False, r=r):
                self.status_calls += 1
                return r

            saved = bot.get_status
            bot.get_status = bad_status
            try:
                text, datas, _ = self._screen("pr_s4~awg2")
            finally:
                bot.get_status = saved
            self.assertNotIn("⏳", text, r)
            self.assertNotIn("pr_s4_do~awg2", datas, r)
            self.assertEqual(bot._PR_S4_OPENING, set(), r)
            self.assertEqual(self.reads, [], r)

    def test_app_people_need_the_phrase(self):
        self._fakes(risk=dict(S4_RISK, keyless=1))
        rec, ctx = self._press("pr_s4~awg2")
        pend = bot.CONFIRM.pending(self.UID)
        self.assertEqual((pend["kind"], pend["phrase"]), ("profile-fix-s4-adopted", "443"))
        texts = " ".join(str(c[1][0]) for c in rec.calls if c[0] == "edit")
        self.assertIn(i18n.tf(self.UID, "pr_s4_keyless_fmt", 1), texts)
        self.assertEqual(self.calls, [], "без фразы — ничего")
        rec = self._say("443", ctx)
        self.assertEqual(self.calls, [("profile-fix-s4", {"profile": "awg2", "adopted_confirm": "awg2"})])
        replies = [str(c[1][0]) for c in rec.calls if c[0] == "reply_text"]
        self.assertTrue(replies[-1].startswith("✅ " + S4_DONE), replies)

    def test_people_who_appeared_later_get_the_phrase_from_the_core(self):
        self._fakes(late_keyless=True)
        text, _, _ = self._screen("pr_s4_do~awg2")
        self.assertIn("443", text)
        self.assertEqual(bot.CONFIRM.pending(self.UID)["kind"], "profile-fix-s4-adopted")

    def test_no_risk_says_so(self):
        self._fakes(risk=None)
        text, datas, _ = self._screen("pr_s4~awg2")
        self.assertIn(i18n.t(self.UID, "pr_s4_none"), text)
        self.assertEqual(datas, ["pr_card~awg2"])
        self.assertEqual(self.calls, [])

    def test_mtu_screen_names_the_standard_and_warns_before_saving(self):
        self._fakes(cfg={"profiles": {"awg2": {"params": {"MTU": "1376", "S4": "40"}}}})
        text, datas, ctx = self._screen("pr_mtu~awg2")
        self.assertIn(i18n.tf(self.UID, "pr_mtu_now_fmt", "1376"), text)
        self.assertIn("1280 — стандарт, работает везде. На проводном интернете можно поднять до 1320 — будет на несколько "
                      "процентов быстрее", text)
        self.assertIn("1280–1500", text)
        self.assertEqual(datas, ["pr_mtu_set~awg2~1280", "pr_mtu_set~awg2~1320", "pr_card~awg2"])
        rec, _ = self._press("pr_mtu~awg2")
        labels = [b.text for row in [c for c in rec.calls if c[0] == "edit"][-1][2]["reply_markup"].inline_keyboard for b in row]
        self.assertEqual(labels[0], i18n.t(self.UID, "pr_mtu_std_btn"))
        # 1500 при S4 = 40 — пакет 1620: сначала слова и вопрос, мост — только после «Сохранить всё равно».
        text, datas, _ = self._screen("pr_mtu_set~awg2~1500")
        self.assertIn(bot.esc(i18n.tf(self.UID, "pr_mtu_warn_path_fmt", 1620)), text)
        self.assertEqual(datas, ["pr_mtu_ok~awg2~1500", "pr_mtu_set~awg2~1280", "pr_card~awg2"])
        self.assertEqual(self.calls, [])
        self._screen("pr_mtu_ok~awg2~1500")
        self.assertEqual(self.calls, [("profile-mtu", {"profile": "awg2", "mtu": "1500"})])
        # «Вернуть 1280» и 1320 при S4 ≤ 40 — сразу, без вопросов.
        self._screen("pr_mtu_set~awg2~1280")
        self._screen("pr_mtu_set~awg2~1320")
        self.assertEqual(self.calls[1:], [("profile-mtu", {"profile": "awg2", "mtu": "1280"}),
                                          ("profile-mtu", {"profile": "awg2", "mtu": "1320"})])
        # Текстом: 1330 — пакет 1450, вопрос; моста нет.
        _, ctx = self._press("pr_mtu~awg2")
        rec = self._say("1330", ctx)
        reply = [c for c in rec.calls if c[0] == "reply_text"][-1]
        self.assertIn(bot.esc(i18n.tf(self.UID, "pr_mtu_warn_path_fmt", 1450)), str(reply[1][0]))
        self.assertEqual([b.callback_data for row in reply[2]["reply_markup"].inline_keyboard for b in row],
                         ["pr_mtu_ok~awg2~1330", "pr_mtu_set~awg2~1280", "pr_card~awg2"])
        # Текстом: 1279 (L-02б) — отказ словами, туннель у клиентов с IPv6 не поднимется; кнопок и моста нет, окно ждёт
        # другое число.
        _, ctx = self._press("pr_mtu~awg2")
        rec = self._say("1279", ctx)
        reply = [c for c in rec.calls if c[0] == "reply_text"][-1]
        self.assertEqual(str(reply[1][0]), "❌ Ниже 1280 туннель не поднимется у клиентов с IPv6 (AllowedIPs ::/0) — "
                                           "оставьте 1280 или больше.")
        rec = self._say("1250", ctx)
        reply = [c for c in rec.calls if c[0] == "reply_text"][-1]
        self.assertIn(i18n.t(self.UID, "pr_mtu_low"), str(reply[1][0]))
        # Мусор — прежнее «от 1280 до 1500».
        rec = self._say("1600", ctx)
        reply = [c for c in rec.calls if c[0] == "reply_text"][-1]
        self.assertEqual(str(reply[1][0]), "❌ MTU должен быть числом от 1280 до 1500.")
        self.assertEqual(len(self.calls), 3)


class MainMenu050Tests(_BotScreenCase):
    """Главный экран по дизайну: строки проблем и кнопки быстрого действия над разделами (start*),
    L-02 — «адрес в ключах больше не на сервере» (status.endpoint_host_gone), «🔁 Повторить» при сбое."""

    def _env(self, **env):
        for k, v in env.items():
            os.environ[k] = v
            self.addCleanup(os.environ.pop, k, None)
        bot.invalidate_status()
        self.addCleanup(bot.invalidate_status)

    def test_endpoint_gone_line_and_button(self):
        self._env(FAKE_BOT_ENDPOINT_GONE="1")
        text, datas, _ = self._screen("menu_main")
        self.assertIn(i18n.tf(self.UID, "dash_endpoint_gone_fmt", "198.51.100.7"), text)
        self.assertIn("se_endpoint", datas)
        self.assertLess(datas.index("se_endpoint"), datas.index("menu_users"), "быстрое действие — над разделами")
        text, datas, _ = self._screen("se_endpoint")
        self.assertIn(i18n.tf(self.UID, "se_endpoint_gone_fmt", "198.51.100.7"), text)
        self.assertIn("se_endpoint_ip", datas, "«📍 IP сервера» — первый выход")
        for lang in ("ru", "en"):
            self.assertIn("198.51.100.7", i18n.t_in(lang, "dash_endpoint_gone_fmt") % "198.51.100.7")

    def test_no_endpoint_line_while_the_address_is_here(self):
        self._env(FAKE_BOT_ENDPOINT_GONE="0")
        text, datas, _ = self._screen("menu_main")
        self.assertNotIn("198.51.100.7", text)
        self.assertNotIn("se_endpoint", datas)
        text, _, _ = self._screen("se_endpoint")
        self.assertNotIn("⚠️", text)
        self.assertEqual(bot.endpoint_gone_host({"status": "success", "endpoint_host_gone": "yes"}), "",
                         "признак — только настоящий true")

    def test_stopped_and_missing_profiles_get_a_quick_action(self):
        self._env()
        text, datas, _ = self._screen("menu_main")               # у заглушки awg3 остановлен
        self.assertIn(i18n.tf(self.UID, "dash_profile_stopped_fmt", FX31), text)
        self.assertIn("pr_restart~awg3", datas)
        st = {"status": "success", "mode": "lite", "server_ip": "203.0.113.10", "users": 1,
              "profiles": [{"profile": "awg1", "state": "missing", "line": "1.5", "port": 5000}]}
        lines, rows = bot.dashboard_problems(self.UID, st)
        self.assertIn("⚠️ " + i18n.tf(self.UID, "dash_profile_missing_fmt", "AmneziaWG 1.5 · порт 5000"), lines)
        self.assertEqual([b.callback_data for r in rows for b in r], ["pr_fix~awg1"])
        st["profiles"][0]["state"] = "docker_unreachable"
        lines, rows = bot.dashboard_problems(self.UID, st)
        self.assertEqual(lines, ["⚠️ " + i18n.t(self.UID, "dash_docker_unreachable")])
        self.assertEqual(rows, [], "Docker чинится на сервере — кнопки нет")
        st["profiles"][0]["state"] = "running"
        self.assertEqual(bot.dashboard_problems(self.UID, st), ([], []), "чинить нечего — ни строк, ни кнопок")

    def test_server_did_not_answer_offers_retry(self):
        self._env(FAKE_BOT_STATUS="error")
        text, datas, _ = self._screen("menu_main")
        self.assertIn("forced error from fixture", text)
        self.assertEqual(datas[0], "menu_main", "первой — «🔁 Повторить»")
        self.assertIn("sec_settings", datas, "разделы остаются: язык и справка моста не ждут")


class Parity050Tests(_BotScreenCase):
    """L-10 (DS2-57, решение владельца): в боте — свой адрес за человеком (user-set-address), продлить
    доступ агента (agent-extend), отозвать все страницы человека (page-revoke-all)."""

    def _env(self, **env):
        for k, v in env.items():
            os.environ[k] = v
            self.addCleanup(os.environ.pop, k, None)
        bot.invalidate_status()
        self.addCleanup(bot.invalidate_status)

    def _args(self):
        path = os.path.join(TMP_ROOT, "parity050_%s.txt" % self._testMethodName)
        os.environ["FAKE_BOT_ARGS_FILE"] = path
        self.addCleanup(os.environ.pop, "FAKE_BOT_ARGS_FILE", None)
        return path

    # ── свой адрес за человеком ──
    def test_pin_is_offered_only_when_there_is_a_choice(self):
        self._env(FAKE_BOT_EDGE="single", FAKE_BOT_USER_IP="")
        _, datas, _ = self._screen("u_view~anna")
        self.assertNotIn("u_pin~anna", datas, "один адрес у машины — выбирать не из чего")
        self._env(FAKE_BOT_EDGE="multi")
        _, datas, _ = self._screen("u_view~anna")
        self.assertIn("u_pin~anna", datas)
        self._env(FAKE_BOT_EDGE="single", FAKE_BOT_USER_IP="203.0.113.11")
        _, datas, _ = self._screen("u_view~anna")
        self.assertIn("u_pin~anna", datas, "адрес уже закреплён — снять его можно только здесь")

    def test_pin_screen_lists_machine_addresses(self):
        self._env(FAKE_BOT_EDGE="multi", FAKE_BOT_USER_IP="")
        text, datas, _ = self._screen("u_pin~anna")
        self.assertIn(i18n.tf(self.UID, "user_pin_title_fmt", "anna"), text)
        self.assertIn(i18n.tf(self.UID, "user_pin_now_common_fmt", "203.0.113.10"), text)
        self.assertIn(i18n.t(self.UID, "user_pin_hint"), text)
        for ip in ("203.0.113.10", "203.0.113.11", "203.0.113.12"):
            self.assertIn("u_pin_set~anna~%s" % bot.key_addr_code(ip), datas)
        self.assertNotIn("u_pin_set~anna~-", datas, "общий и так — возвращать нечего")
        self.assertEqual(datas[-1], "u_view~anna")
        self._env(FAKE_BOT_USER_IP="203.0.113.11")
        text, datas, _ = self._screen("u_pin~anna")
        self.assertIn(i18n.tf(self.UID, "user_pin_now_own_fmt", "203.0.113.11"), text)
        self.assertIn("u_pin_set~anna~-", datas)
        self._env(FAKE_BOT_USER_IP="198.51.100.7")
        text, _, _ = self._screen("u_pin~anna")
        self.assertIn(i18n.tf(self.UID, "user_pin_now_gone_fmt", "198.51.100.7"), text)

    def test_pin_sets_and_resets_the_address(self):
        args = self._args()
        self._env(FAKE_BOT_EDGE="multi")
        text, datas, _ = self._screen("u_pin_set~anna~%s" % bot.key_addr_code("203.0.113.11"))
        self.assertIn(i18n.tf(self.UID, "user_pin_set_fmt", "anna", "203.0.113.11"), text)
        self.assertIn(i18n.t(self.UID, "user_pin_resend"), text, "ключ у человека смотрит на прежний адрес")
        self.assertEqual(datas, ["u_view~anna"])
        text, _, _ = self._screen("u_pin_set~anna~-")
        self.assertIn(i18n.tf(self.UID, "user_pin_common_fmt", "anna", "203.0.113.10"), text)
        with open(args, encoding="utf-8") as fh:
            got = fh.read()
        self.assertIn("--action=user-set-address\t--json\t--name=anna\t--ip=203.0.113.11", got)
        self.assertIn("--ip=\t", got, "вернуть общий — пустой --ip=, а не пропуск параметра")
        self._env(FAKE_BOT_PIN_SAME="1")
        text, _, _ = self._screen("u_pin_set~anna~%s" % bot.key_addr_code("203.0.113.11"))
        self.assertIn(i18n.tf(self.UID, "user_pin_same_fmt", "203.0.113.11"), text)

    def test_pin_with_a_domain_in_keys(self):
        """Домен в ключах: вписанный руками побеждает свой адрес (подействует позже), домен по умолчанию
        уступает — как в SSH (user_address_domain_note / user_address_site_domain_note)."""
        saved = bot.run_bot_action
        manual = {"value": "vpn.example.com"}

        async def fake(action, *a, **kw):
            if action == "edge-status":
                return {"status": "success", "count": 2, "domain_endpoint": True, "keys_host": "vpn.example.com",
                        "ips": [{"ip": "203.0.113.10"}, {"ip": "203.0.113.11"}]}
            if action == "status":
                r = await saved(action, *a, **kw)
                return dict(r, endpoint_host=manual["value"])
            return await saved(action, *a, **kw)
        bot.run_bot_action = fake
        self.addCleanup(setattr, bot, "run_bot_action", saved)
        self._env()
        text, _, _ = self._screen("u_pin~anna")
        self.assertIn(i18n.tf(self.UID, "user_pin_domain_fmt", "vpn.example.com"), text)
        manual["value"] = ""
        self._env()
        text, _, _ = self._screen("u_pin~anna")
        self.assertIn(i18n.tf(self.UID, "user_pin_site_domain_fmt", "vpn.example.com"), text)
        # итог: домен в ключах — адрес записан, ключ не меняется
        r = {"status": "success", "endpoint_ip": "203.0.113.11", "endpoint": "vpn.example.com",
             "resend_required": False, "domain_endpoint": True}
        text = bot.user_pin_result_text(self.UID, "anna", "203.0.113.11", r)
        self.assertIn(i18n.tf(self.UID, "user_pin_domain_result_fmt", "vpn.example.com"), text)
        self.assertNotIn(i18n.t(self.UID, "user_pin_resend"), text)

    def test_pin_refusals_and_bad_buttons(self):
        self._env(FAKE_BOT_EDGE="single")
        text, datas, _ = self._screen("u_pin_set~anna~%s" % bot.key_addr_code("203.0.113.99"))
        self.assertIn("address_not_here", text)
        self.assertIn("u_pin~anna", datas)
        for data in ("u_pin_set~anna~hdeadbe", "u_pin_set~anna~zz", "u_pin_set~Анна~-", "u_pin_set~anna"):
            rec, _ = self._press(data)
            self.assertTrue(any(c[2].get("show_alert") for c in rec.calls if c[0] == "answer"), data)
        self.assertEqual(bot.pin_code_ip(bot.key_addr_code("203.0.113.11")), "203.0.113.11")

    # ── продлить доступ агента ──
    def test_agent_card_offers_extend(self):
        _, datas, _ = self._screen("ag_view~a-1a2b")
        self.assertIn("ag_ext~a-1a2b", datas)

    def test_agent_extend_screen_and_result(self):
        args = self._args()
        text, datas, _ = self._screen("ag_ext~a-1a2b")
        self.assertIn(i18n.t(self.UID, "ag_extend_ask"), text)
        self.assertIn(i18n.t(self.UID, "ag_extend_sliding"), text)
        self.assertEqual(datas, ["ag_ext_do~a-1a2b~30", "ag_ext_do~a-1a2b~90", "ag_ext~a-1a2b~0", "ag_view~a-1a2b"])
        text, datas, _ = self._screen("ag_ext_do~a-1a2b~90")
        self.assertIn(i18n.tf(self.UID, "ag_extended_fmt", "2026-12-31"), text)
        self.assertEqual(datas, ["ag_view~a-1a2b"])
        # «бессрочно» — ослабление: сначала вопрос, потом days=0
        text, datas, _ = self._screen("ag_ext~a-1a2b~0")
        self.assertIn(i18n.t(self.UID, "ag_extend_forever_warn"), text)
        self.assertEqual(datas, ["ag_ext_do~a-1a2b~0", "ag_ext~a-1a2b"])
        text, _, _ = self._screen("ag_ext_do~a-1a2b~0")
        self.assertIn(i18n.t(self.UID, "ag_extended_forever"), text)
        with open(args, encoding="utf-8") as fh:
            got = fh.read()
        self.assertIn("--action=agent-extend\t--json\t--id=a-1a2b\t--days=90", got)
        self.assertIn("--days=0", got)
        for data in ("ag_ext_do~a-1a2b~7", "ag_ext_do~a-1a2b~-1", "ag_ext~a-1a2b~30", "ag_ext~bad"):
            rec, _ = self._press(data)
            self.assertTrue(any(c[2].get("show_alert") for c in rec.calls if c[0] == "answer"), data)

    def test_expiry_notifications_name_the_button_and_the_panel(self):
        for event in ("agent_expiring", "agent_expired"):
            markup = bot.notify_keyboard(self.UID, {"event": event, "agent": "a-1a2b"})
            self.assertEqual([b.callback_data for row in markup.inline_keyboard for b in row],
                             ["nt~ag_ext~a-1a2b", "nt~ag_view~a-1a2b"])
        for lang in ("ru", "en"):
            for key in ("ntf_agent_expiring_fmt", "ntf_agent_expired_fmt"):
                text = i18n.t_in(lang, key)
                self.assertIn(i18n.t_in(lang, "ag_btn_extend"), text, "%s/%s: кнопка бота названа" % (lang, key))
            self.assertNotIn("SSH", i18n.t_in(lang, "ag_forever_warn").split("—")[0], "не «только в SSH-меню»")

    # ── отозвать все страницы человека ──
    def test_revoke_all_pages_asks_then_revokes(self):
        args = self._args()
        _, datas, _ = self._screen("pg_list~anna")                # у anna две страницы
        self.assertIn("pg_revall~anna", datas)
        _, datas, _ = self._screen("pg_list~bob")                 # одна годная — отзыв в её карточке
        self.assertNotIn("pg_revall~bob", datas)
        text, datas, _ = self._screen("pg_revall~anna")
        self.assertIn(i18n.tf(self.UID, "pg_revoke_all_confirm_fmt", 2), text)
        self.assertEqual(datas, ["pg_revall_do~anna", "pg_list~anna"])
        text, datas, _ = self._screen("pg_revall_do~anna")
        self.assertIn(i18n.tf(self.UID, "pg_revoke_all_done_fmt", 2), text)
        self.assertEqual(datas, ["u_view~anna"])
        with open(args, encoding="utf-8") as fh:
            self.assertIn("--action=page-revoke-all\t--json\t--name=anna", fh.read())


class Texts050Tests(_BotScreenCase):
    """L-11 и формулировки дизайна: /help про личный чат (DS2-33), без восклицаний (DS2-32), ответы
    команд (DS2-23), один словарь уровней доступа агентов (DS2-52)."""

    def _cmd(self, cmd, args):
        upd = FakeUpdate(self.UID, text="/cmd")
        run(cmd(upd, FakeContext(args)))
        return [str(c[1][0]) for c in upd.rec.calls if c[0] == "reply_text"]

    def test_help_says_private_chat_only(self):
        texts = self._cmd(bot.cmd_help, [])
        self.assertIn(i18n.t(self.UID, "help_private_only"), texts[0])
        self.assertIn("/adduser", texts[0])

    def test_no_exclamations_except_the_greeting(self):
        for lang in ("ru", "en"):
            with open(os.path.join(BOT_SRC, "lang", "%s.json" % lang), encoding="utf-8") as fh:
                d = json.load(fh)
            loud = sorted(k for k, v in d.items() if "!" in v)
            self.assertEqual(loud, ["waiting_admin_title"], "%s: «!» только в приветствии «Привет, …!»" % lang)
            self.assertIn("администратор" if lang == "ru" else "administrator", d["admin_claimed_fmt"])

    def test_adduser_answers(self):
        self.assertEqual(self._cmd(bot.cmd_adduser, []), [i18n.t(self.UID, "cmd_adduser_usage")])
        for bad in (["Гость", "20"], ["guest-1", "много"], ["guest-1", "20", "30", "лишний"]):
            self.assertEqual(self._cmd(bot.cmd_adduser, bad), [i18n.t(self.UID, "cmd_adduser_bad")], bad)
        texts = self._cmd(bot.cmd_adduser, ["guest-1", "20", "30"])
        done = texts[-1]
        self.assertIn("<code>guest-1</code>", done)
        self.assertIn("20 " + i18n.t(self.UID, "unit_gb"), done)
        self.assertIn(i18n.days(self.UID, 30), done)
        texts = self._cmd(bot.cmd_adduser, ["guest-2"])
        self.assertIn(i18n.t(self.UID, "unlimited"), texts[-1])
        self.assertIn(i18n.t(self.UID, "never"), texts[-1])
        card = {"result": {"profiles": ["awg2", "awg3"], "expires": "2026-10-27T10:00:00Z"}}
        text = bot.adduser_done_text(self.UID, "guest-1", 20, 30, card)
        self.assertIn(i18n.tf(self.UID, "cmd_adduser_term_fmt", i18n.days(self.UID, 30), "2026-10-27"), text)
        self.assertIn(FX31 + ", " + FX20, text)

    def test_key_for_unknown_user_points_to_the_list(self):
        texts = self._cmd(bot.cmd_key, ["nobody"])
        self.assertEqual(texts, ["❌ " + i18n.tf(self.UID, "cmd_key_no_user_fmt", "nobody")])
        self.assertIn("/users", texts[0])

    def test_invite_answers(self):
        self.addCleanup(bot.ALLOWED_IDS.discard, 987654321)
        own = os.environ.get("OWNER_ID")
        self.addCleanup(lambda: os.environ.pop("OWNER_ID", None) if own is None else os.environ.__setitem__("OWNER_ID", own))
        os.environ["OWNER_ID"] = str(self.UID)   # приглашать может только владелец (L-25)
        texts = self._cmd(bot.cmd_invite, ["987654321"])
        # .env стенда записать можно не всегда — тогда честное «добавлены, но не записаны»
        self.assertIn(texts[0], ("✅ " + i18n.tf(self.UID, "admin_added_fmt", "987654321"),
                                 "⚠️ " + i18n.tf(self.UID, "admin_added_unsaved_fmt", "987654321")))
        texts = self._cmd(bot.cmd_invite, ["98765abc"])
        self.assertEqual(texts, ["❌ " + i18n.tf(self.UID, "admin_bad_ids_fmt", "98765abc")])

    def test_agent_levels_are_one_dictionary(self):
        text, _, _ = self._screen("ag_view~a-1a2b")
        self.assertIn("<b>%s</b>" % i18n.t(self.UID, "ag_level_write"), text)
        self.assertIn(i18n.t(self.UID, "ag_card_calls"), text)
        text, _, _ = self._screen("se_agents")
        self.assertIn(i18n.t(self.UID, "ag_level_write"), text)
        self.assertIn(i18n.t(self.UID, "ag_level_read"), text)
        for lang, want in (("ru", ("Только чтение (read)", "Чтение и запись (write)", "Полный (admin)", "Запросов всего")),
                           ("en", ("Read only (read)", "Read and write (write)", "Full (admin)", "Requests total"))):
            got = " ".join(i18n.t_in(lang, k) for k in ("ag_level_read", "ag_level_write", "ag_level_admin", "ag_card_calls"))
            for w in want:
                self.assertIn(w, got)
            self.assertIn(want[0], i18n.t_in(lang, "ag_scope_read"))
            self.assertIn(want[0], i18n.t_in(lang, "ag_scope_note"))


class Scenes050Tests(_BotScreenCase):
    """Новые сцены дизайна там, где функция в продукте есть: flow-expired, upstream-rollback-floating,
    import-done, mode-done / mode-fallback, site-check-a / -foreign / -https / -aaaa."""

    def _bridge(self, action, answer):
        saved = bot.run_bot_action

        async def fake(called, *a, **kw):
            if called == action:
                return dict(answer)
            return await saved(called, *a, **kw)
        bot.run_bot_action = fake
        self.addCleanup(setattr, bot, "run_bot_action", saved)

    def test_answer_to_an_expired_question_says_time_is_up(self):
        _, ctx = self._press("u_quota~anna")
        ctx.user_data["flow"]["since"] -= bot.FLOW_TTL + 5
        rec = self._say("50", ctx)
        self.assertEqual([str(c[1][0]) for c in rec.calls if c[0] == "reply_text"],
                         [i18n.tf(self.UID, "flow_expired_msg_fmt", bot.FLOW_TTL // 60)])
        rec = self._say("50", ctx)                               # один раз — дальше обычное «я ничего не жду»
        self.assertEqual([str(c[1][0]) for c in rec.calls if c[0] == "reply_text"], [i18n.t(self.UID, "text_no_waiter")])

    def test_rollback_to_a_floating_tag_warns(self):
        ctx = FakeContext()
        ctx.user_data["upstream"] = {"status": "success", "profiles": [
            {"profile": "awg3", "previous_tag": "4.8.11", "previous_digest": "", "rollback_available": True},
            {"profile": "awg2", "previous_tag": "4.8.10", "previous_digest": "sha256:ab", "rollback_available": True}]}
        text, _, _ = self._screen("up_rb~awg3", ctx)
        self.assertIn(i18n.tf(self.UID, "up_rollback_floating_fmt", "4.8.11"), text)
        text, _, _ = self._screen("up_rb~awg2", ctx)
        self.assertNotIn(i18n.tf(self.UID, "up_rollback_floating_fmt", "4.8.10"), text, "отпечаток есть — откат точный")

    def test_import_done_leads_to_the_people(self):
        _, datas, _ = self._screen("imp_adopt_do~amnezia-awg")
        self.assertEqual(datas, ["menu_users", "mt_import"])

    def test_switch_to_a_site_mode_offers_the_site_check(self):
        os.environ["FAKE_BOT_MODE"] = "pro"                       # между режимами с сайтом домена не спрашивают
        self.addCleanup(os.environ.pop, "FAKE_BOT_MODE", None)
        bot.invalidate_status()
        self.addCleanup(bot.invalidate_status)
        _, ctx = self._press("se_mode_to~lite_site")
        rec = self._say("MODE lite_site", ctx)
        markup = [c[2].get("reply_markup") for c in rec.calls if c[0] == "reply_text" and c[2].get("reply_markup")][-1]
        self.assertEqual([b.callback_data for row in markup.inline_keyboard for b in row][0], "se_check")

    def _site(self, verdict, **extra):
        site = {"domain": "vpn.example.ru", "mode": "pro", "server_ip": "203.0.113.10", "url": "https://vpn.example.ru/",
                "https_code": 200, "http_code": 301, "nginx_active": True, "cert": "letsencrypt",
                "cert_expires": "2026-12-12", "cert_days_left": 77, "dns_ips": [], "dns_foreign": [],
                "dns_aaaa_foreign": []}
        site.update(extra)
        self._bridge("site-check", {"status": "success", "verdict": verdict, "site": site})
        return self._screen("se_check")

    def test_site_check_names_the_bad_records(self):
        text, _, _ = self._site("dns_mismatch", dns_ips=["198.51.100.50"], dns_foreign=["198.51.100.50"])
        self.assertIn(i18n.tf(self.UID, "site_check_a_records_fmt", "vpn.example.ru", "198.51.100.50"), text)
        text, _, _ = self._site("dns_partial", dns_ips=["203.0.113.10", "198.51.100.50"], dns_foreign=["198.51.100.50"])
        self.assertIn(i18n.tf(self.UID, "site_check_a_records_fmt", "vpn.example.ru", "203.0.113.10 ✓, 198.51.100.50"), text)
        text, _, _ = self._site("ok", dns_aaaa_foreign=["2001:db8::1"])
        self.assertIn(i18n.tf(self.UID, "site_check_aaaa_fmt", "vpn.example.ru", "2001:db8::1"), text)
        text, datas, _ = self._site("ok")
        self.assertNotIn("AAAA", text)
        self.assertNotIn("se_pf", datas)
        _, datas, _ = self._site("http_error", https_code=0)
        self.assertEqual(datas[0], "se_pf", "сайт молчит по HTTPS — первым делом проверка портов")

    def test_mode_pick_shows_ports_and_availability(self):
        """L-23, сцены mode-pick / mode-pick-busy: у каждого из трёх режимов — порты и доступен ли он."""
        os.environ["FAKE_BOT_PREFLIGHT"] = "free"
        self.addCleanup(os.environ.pop, "FAKE_BOT_PREFLIGHT", None)
        text, _, _ = self._screen("se_mode_pick")
        for m in bot.MODES:
            self.assertIn(i18n.tf(self.UID, "se_mode_ports_fmt", i18n.t(self.UID, "mode_ports_%s" % m)), text)
        # три режима и сценарий «Ленивый Pro» над ними (0.6.0, F-1)
        self.assertEqual(text.count("✅ " + i18n.t(self.UID, "pf_available")), 4)
        os.environ["FAKE_BOT_PREFLIGHT"] = "busy"
        text, datas, _ = self._screen("se_mode_pick")
        self.assertEqual(text.count("✅ " + i18n.t(self.UID, "pf_available")), 1, "свободен только «Lite»")
        self.assertIn("⛔ TCP/443 занят: nginx", text, "причина — словами ядра")
        self.assertIn("se_mode_to~pro", datas)
        self._bridge("preflight", {"status": "error", "code": "transport"})
        text, _, _ = self._screen("se_mode_pick")
        self.assertNotIn("⛔", text)
        self.assertNotIn("✅", text, "проверка не ответила — без строк доступности")


class DiskOnMainScreenTests(_BotScreenCase):
    """OPS-06 (эксплуатационные проверки 0.5.1): почти полный диск — строка на главном экране и кнопка
    «🩺 Диагностика» над разделами; ok, null или ядро без поля — ни слова. status.disk — ga_disk_json ядра
    (lib/common.sh): {path, total_mb, used_mb, free_mb, used_pct, level ok|low|critical, low_mb, critical_mb}."""

    DISK = {"path": "/opt/goamnezia", "total_mb": 20000, "used_mb": 19300, "free_mb": 700, "used_pct": 97,
            "level": "low", "low_mb": 1024, "critical_mb": 512}

    def _disk(self, disk):
        """status с полем disk (остальное — заглушка моста)."""
        saved = bot.run_bot_action

        async def fake(action, *a, **kw):
            r = await saved(action, *a, **kw)
            return dict(r, disk=disk) if action == "status" and bot.bridge_ok(r) else r
        bot.run_bot_action = fake
        self.addCleanup(setattr, bot, "run_bot_action", saved)
        bot.invalidate_status()
        self.addCleanup(bot.invalidate_status)

    def _main(self):
        rec, _ = self._press("menu_main")
        edit = [c for c in rec.calls if c[0] == "edit"][-1]
        buttons = [(b.text, b.callback_data) for row in edit[2]["reply_markup"].inline_keyboard for b in row]
        return str(edit[1][0]), buttons

    def test_low_and_critical_warn_with_a_way_out(self):
        for level, key, icon, free, pct in (("low", "dash_disk_low_fmt", "⚠️ ", 700, 97),
                                            ("critical", "dash_disk_critical_fmt", "🚨 ", 300, 99)):
            self._disk(dict(self.DISK, level=level, free_mb=free, used_pct=pct))
            text, buttons = self._main()
            self.assertIn(icon + i18n.tf(self.UID, key, free, pct), text, level)
            datas = [d for _, d in buttons]
            self.assertIn((i18n.t(self.UID, "mt_diag"), "mt_diag"), buttons, level)
            self.assertLess(datas.index("mt_diag"), datas.index("menu_users"), "быстрое действие — над разделами")
            rec, _ = self._press("mt_diag")                        # кнопка ведёт туда, где диск виден
            self.assertTrue([c for c in rec.calls if c[0] == "edit"], level)
        for lang, unit in (("ru", "МБ"), ("en", "MB")):
            for key in ("dash_disk_low_fmt", "dash_disk_critical_fmt"):
                text = i18n.t_in(lang, key) % (812, 96)
                self.assertIn("812 " + unit, text, "%s/%s: сколько свободно" % (lang, key))
                self.assertRegex(text, r"96 ?%", "%s/%s: сколько занято" % (lang, key))
                self.assertIn("Logs" if lang == "en" else "Логи", text, "%s/%s: что сделать" % (lang, key))

    def test_ok_or_unknown_disk_says_nothing(self):
        for disk in (dict(self.DISK, level="ok", free_mb=5000, used_pct=40), None, "broken", {"level": "full"}):
            self._disk(disk)
            text, buttons = self._main()
            self.assertNotIn("mt_diag", [d for _, d in buttons], disk)
            for key in ("dash_disk_low_fmt", "dash_disk_critical_fmt"):
                self.assertNotIn(i18n.t(self.UID, key).split(":")[0], text, disk)
        self.assertEqual(bot.dashboard_problems(self.UID, {"status": "success", "profiles": [], "disk": dict(self.DISK, level="ok")}),
                         ([], []))


class KmodStageATests(unittest.TestCase):
    """KMOD_PLAN, этап А (бот): на сервере модуль ядра AmneziaWG без исправлений, а есть профиль 3.1
    (status.kmod.warn) — строка на главном экране; клиентский параметр на модуле ядра применён перезапуском профиля
    (restarted=true в ответе profile-set-param) — честно, сколько ждать. Без python-telegram-bot: кнопки не нужны."""

    UID = 424243
    LINE = "⚠️ "

    def test_unpatched_module_warns_on_the_main_screen(self):
        line = self.LINE + i18n.t(self.UID, "dash_kmod_unpatched")
        st = {"status": "success", "profiles": []}
        warn = {"loaded": True, "version": "3.1.20260812", "patched": False, "warn": True}
        self.assertIn(line, bot.dashboard_problems(self.UID, dict(st, kmod=warn), buttons=False)[0])
        for kmod in (None, "warn", {"loaded": True, "version": "3.1.20260906+kaskad-k31.1", "patched": True, "warn": False},
                     {"warn": "true"}, {"loaded": False, "warn": False}):
            self.assertNotIn(line, bot.dashboard_problems(self.UID, dict(st, kmod=kmod), buttons=False)[0], kmod)
        self.assertIn(line, bot.dashboard_text(self.UID, dict(st, kmod=warn, mode="lite", server_ip="203.0.113.10")))
        for lang in ("ru", "en"):
            self.assertIn("3.1", i18n.t_in(lang, "dash_kmod_unpatched"), lang)

    def test_restart_after_param_change_is_said_honestly(self):
        replies = []

        class Target:
            async def reply_text(self, text, **kw):
                replies.append(text)

        async def fake_mutate(action, who=None, timeout=None, **params):
            return dict(answer, status="success", message="ok")

        saved = (bot.mutate, bot.fail_markup)
        bot.mutate = fake_mutate
        bot.fail_markup = lambda *a, **kw: None
        self.addCleanup(setattr, bot, "mutate", saved[0])
        self.addCleanup(setattr, bot, "fail_markup", saved[1])
        restarted = i18n.t(self.UID, "pr_param_restarted")
        for answer, want in (({"reissue_required": False, "restarted": True}, True),
                             ({"reissue_required": False, "restarted": False}, False),
                             ({"reissue_required": True, "restarted": True}, False)):
            replies.clear()
            run(bot._apply_param(Target(), self.UID, "awg3", "Jc", "7", edit=False))
            self.assertEqual(restarted in replies[-1], want, answer)
        for lang in ("ru", "en"):
            self.assertIn("15–25", i18n.t_in(lang, "pr_param_restarted"), lang)

    def test_param_change_says_who_needs_a_new_config_once(self):
        # Проверяльщик 15.10 (REVIEW-CORE-1013, находка 1): с 1.0.13 message = итог + summary ядра («Старые конфиги профиля
        # больше не подключатся: подписки обновятся сами…»), а бот добавлял свою строку «выдайте пользователям ключи
        # заново» — то же второй раз и вопреки «подписки обновятся сами». Теперь — слова ядра и шаги по людям, без своей.
        replies = []

        class Target:
            async def reply_text(self, text, **kw):
                replies.append(text)

        async def fake_mutate(action, who=None, timeout=None, **params):
            return answer

        saved = (bot.mutate, bot.fail_markup)
        bot.mutate = fake_mutate
        bot.fail_markup = lambda *a, **kw: None
        self.addCleanup(setattr, bot, "mutate", saved[0])
        self.addCleanup(setattr, bot, "fail_markup", saved[1])
        summary = "Старые конфиги профиля больше не подключатся: подписки обновятся сами, выданные ключи vpn:// и .conf — выдать заново."
        steps = [{"kind": "restart", "text": "Профиль перезапущен: клиенты переподключатся сами, обычно за 15–25 секунд."},
                 {"kind": "subscription", "text": "С подпиской (vera): обновится сама в течение 12 часов или по «Обновить» в приложении."},
                 {"kind": "key", "text": "Выдавали ключ или файл (anna): импортировать заново — выдайте ключ vpn:// или файл .conf ещё раз."},
                 {"kind": "app", "text": "Ключ из приложения Amnezia (gleb): прежний больше не подключится — поделиться <конфигом>."}]
        reissue = i18n.t(self.UID, "pr_param_reissue_now")
        restarted = i18n.t(self.UID, "pr_param_restarted")
        answer = {"status": "success", "message": "Профиль awg3: параметр S1 теперь 13. " + summary, "reissue_required": True,
                  "restarted": True, "clients_update": True,
                  "result": {"summary": summary, "steps": steps + ["junk", {"text": ""}], "clients_update": True}}
        run(bot._apply_param(Target(), self.UID, "awg3", "S1", "13", edit=False))
        text = replies[-1]
        self.assertNotIn(reissue, text)
        self.assertNotIn(restarted, text, "перезапуск уже сказан шагом restart")
        self.assertEqual(text.count("Старые конфиги профиля больше не подключатся"), 1, text)
        for st in steps:
            self.assertIn("• " + bot.esc(st["text"]), text)
        self.assertIn("&lt;конфигом&gt;", text, "слова ядра экранированы для HTML")
        # Клиентский на модуле ядра: шаги без restart — строка про перезапуск остаётся.
        answer = {"status": "success", "message": "Профиль awg3: параметр Jc теперь 5.", "reissue_required": False,
                  "restarted": True, "clients_update": True, "result": {"summary": "", "steps": steps[1:2]}}
        run(bot._apply_param(Target(), self.UID, "awg3", "Jc", "5", edit=False))
        self.assertIn(restarted, replies[-1])
        self.assertIn(steps[1]["text"], replies[-1])
        # То же значение ещё раз: ядро говорит «никому ничего» (clients_update=false, шагов нет) — без «выдайте заново».
        answer = {"status": "success", "message": "Профиль awg3: параметр S1 теперь 13.", "reissue_required": True,
                  "restarted": True, "clients_update": False, "result": {"summary": "", "steps": []}}
        run(bot._apply_param(Target(), self.UID, "awg3", "S1", "13", edit=False))
        self.assertNotIn(reissue, replies[-1])
        # Ответ прежнего ядра (без clients_update, summary и steps) — как раньше: своя строка о перевыпуске.
        answer = {"status": "success", "message": "ok", "reissue_required": True, "restarted": False}
        run(bot._apply_param(Target(), self.UID, "awg3", "S1", "13", edit=False))
        self.assertIn(reissue, replies[-1])

    def test_adopt_with_partial_ipacket_tags_warns_in_words(self):
        # KMOD-02: подхват с <c> или <d> в I1–I5 — подхвачен, а слова ядра (ipacket_warnings) — строками с ⚠️.
        words = "I1 содержит <c> — его понимает только модуль ядра AmneziaWG; клиенты AmneziaVPN на телефонах с таким I1 не подключатся."
        text = bot.adopt_ipacket_warnings_text({"status": "success", "ipacket_warnings": [words]})
        self.assertEqual(text, "\n\n⚠️ " + bot.esc(words))
        self.assertIn("&lt;c&gt;", text, "теги экранированы для HTML")
        for r in ({"status": "success"}, {"ipacket_warnings": "x"}, {"ipacket_warnings": [None, " "]}):
            self.assertEqual(bot.adopt_ipacket_warnings_text(r), "", r)


# DK-02 (решение владельца 04.10.2026): пакет Docker обновлён, а служба старая — сторож перезапускает её сам, когда к VPN
# никто не подключён (события docker_stale → docker_restarting → docker_restarted), человек — кнопкой «🐳 Перезапустить
# Docker» в «Диагностике» и под уведомлением, через вопрос «VPN пропадёт на 10–30 секунд у всех».
DK_STALE_INFO = {"installed": True, "reachable": True, "client_version": "29.1.3", "client_api": "1.52",
                 "daemon_version": "24.0.7", "daemon_api": "1.43", "mismatch": True, "restart_required": True,
                 "stale": True, "stale_reason": "version", "binary_version": "29.1.3",
                 "binary_build": "29.1.3-0ubuntu3~24.04.2", "daemon_build": "24.0.7-0ubuntu4",
                 "stale_since": "2026-10-01T06:12:00Z"}


class DockerStaleEventsTests(unittest.TestCase):
    """DK-02: события сторожа о старой службе Docker — словами, с версиями и тем, что делать, на обоих языках; под
    «ждём» и под неудачей — кнопка «Перезапустить Docker» (экран — новым сообщением, через вопрос)."""

    EN_UID = 909093

    def setUp(self):
        self.assertTrue(i18n.set_user_lang(self.EN_UID, "en"))

    def test_events_say_what_happened_and_what_next(self):
        _prime_lines()
        stale = {"event": "docker_stale", "daemon_version": "24.0.7", "binary_version": "29.1.3", "client_api": "1.52",
                 "daemon_api": "1.43", "reason": "version", "since": "2026-10-01T06:12:00Z", "online": "2"}
        going = dict(stale, event="docker_restarting", online="")
        done = {"event": "docker_restarted", "ok": True, "duration_s": 9, "daemon_before": "24.0.7",
                "daemon_after": "29.1.3", "binary_version": "29.1.3", "restarted": ["awg3", "awg2"], "failed": [],
                "code": "", "reason": "", "command": "systemctl restart docker", "retry_hours": 6}
        failed = dict(done, ok=False, duration_s=93, daemon_after="", restarted=[], code="docker_restart_timeout",
                      reason="Служба Docker перезапущена, но демон не ответил за 90 с.")
        for uid, maint, every in ((None, "Обслуживание", "у всех"), (self.EN_UID, "Maintenance", "for everyone")):
            generic = i18n.t(uid, "ntf_generic_fmt").split("%s")[0].strip()
            texts = {ev["event"] + str(ev.get("ok", "")): bot.format_notify_event(uid, ev) for ev in (stale, going, done, failed)}
            for key, text in texts.items():
                self.assertNotIn(generic, text, key)
                self.assertNotIn("%s", text, key)
            self.assertEqual(texts["docker_stale"], i18n.tf(uid, "ntf_docker_stale_fmt", "29.1.3", "24.0.7"))
            self.assertIn(maint, texts["docker_stale"])
            self.assertIn("10–30", texts["docker_stale"])
            self.assertIn(every, texts["docker_stale"])
            self.assertEqual(texts["docker_restarting"], i18n.tf(uid, "ntf_docker_restarting_fmt", "29.1.3", "24.0.7"))
            self.assertEqual(texts["docker_restartedTrue"],
                             i18n.tf(uid, "ntf_docker_restarted_fmt", "9", "29.1.3",
                                     bot.profile_name(uid, "awg3") + ", " + bot.profile_name(uid, "awg2")))
            # Неудача — причина своими словами по коду, ручная команда и когда сторож попробует снова
            self.assertEqual(texts["docker_restartedFalse"],
                             i18n.tf(uid, "ntf_docker_restart_failed_fmt", i18n.t(uid, "dkr_why_timeout"), "6"))
            self.assertIn("systemctl restart docker", texts["docker_restartedFalse"])
            self.assertIn(maint, texts["docker_restartedFalse"])
        # Одна версия, разные сборки (живая проверка на test1, 04.10): подписи ядра — сборки, а не «29.1.3 … (29.1.3)»
        build = bot.format_notify_event(None, dict(stale, daemon_version="29.1.3", binary_label="29.1.3-0ubuntu3~24.04.2",
                                                   daemon_label="29.1.3-0ubuntu3~22.04.2"))
        self.assertEqual(build, i18n.tf(None, "ntf_docker_stale_fmt", "29.1.3-0ubuntu3~24.04.2", "29.1.3-0ubuntu3~22.04.2"))
        self.assertIn("29.1.3-0ubuntu3~24.04.2", bot.format_notify_event(None, dict(done, daemon_after_label="29.1.3-0ubuntu3~24.04.2")))
        # Версии API — когда бинарь на диске не узнать (старая служба по рассогласованию API)
        api_only = bot.format_notify_event(None, {"event": "docker_stale", "daemon_version": "", "binary_version": "",
                                                  "client_api": "1.52", "daemon_api": "1.43", "reason": "api"})
        self.assertEqual(api_only, i18n.tf(None, "ntf_docker_stale_fmt", "1.52", "1.43"))
        # Не поднялись профили — названы линиями; незнакомый код — слова ядра
        why = bot.docker_restart_why(None, {"code": "restart_failed", "failed": [{"profile": "awg3", "code": "container_not_up"}]})
        self.assertEqual(why, i18n.tf(None, "dkr_why_profiles_fmt", FX31))
        self.assertEqual(bot.docker_restart_why(None, {"code": "new_code", "reason": "<b>слова</b>"}), "&lt;b&gt;слова&lt;/b&gt;")

    def test_button_under_waiting_and_failure_only(self):
        if not bot.PTB_AVAILABLE:
            self.skipTest("python-telegram-bot не установлен")
        for ev in ({"event": "docker_stale"}, {"event": "docker_restarted", "ok": False}):
            kb = bot.notify_keyboard(None, ev)
            # nt~ — экран вопроса открывается новым сообщением, уведомление остаётся в чате (BOT-5)
            self.assertEqual([b.callback_data for row in kb.inline_keyboard for b in row], ["nt~mt_dkr"], ev)
        for ev in ({"event": "docker_restarting"}, {"event": "docker_restarted", "ok": True}):
            self.assertIsNone(bot.notify_keyboard(None, ev), ev)


class DockerRestartScreenTests(_BotScreenCase):
    """DK-02: «Диагностика» называет старую службу (версии и с какого дня) и ведёт к «🐳 Перезапустить Docker»; кнопка —
    вопрос с предупреждением, затем мост docker-restart и итог (за сколько секунд, какая служба, какие профили) или
    причина словами и ручная команда."""

    def _bridge(self, **answers):
        """Ответы моста по действию поверх заглушки; сам вызов в заглушку уходит — его видно в FAKE_BOT_ARGS_FILE."""
        saved = bot.run_bot_action

        async def fake(action, *a, **kw):
            r = await saved(action, *a, **kw)
            return answers[action] if action in answers else r
        bot.run_bot_action = fake
        self.addCleanup(setattr, bot, "run_bot_action", saved)

    def _args_file(self):
        path = os.path.join(TMP_ROOT, "dk02_%s.args" % self.id().rsplit(".", 1)[-1])
        if os.path.exists(path):
            os.unlink(path)
        os.environ["FAKE_BOT_ARGS_FILE"] = path
        self.addCleanup(os.environ.pop, "FAKE_BOT_ARGS_FILE", None)
        return path

    def _last_edit(self, data):
        rec, _ = self._press(data)
        edit = [c for c in rec.calls if c[0] == "edit"][-1]
        markup = edit[2].get("reply_markup")
        return str(edit[1][0]), [b.callback_data for row in (markup.inline_keyboard if markup is not None else []) for b in row]

    @staticmethod
    def _bridged(path, action):
        if not os.path.exists(path):
            return False
        with open(path, encoding="utf-8") as fh:
            return "--action=%s" % action in fh.read()

    def test_diag_names_the_old_service_and_leads_to_restart(self):
        self._bridge(quality={"status": "success", "quality": {"docker": True, "ip_forward": "1", "network": True,
                                                               "disk_free_mb": 26118, "docker_info": DK_STALE_INFO}})
        text, datas = self._last_edit("mt_diag")
        self.assertIn(i18n.tf(self.UID, "diag_docker_stale_fmt", "29.1.3", "24.0.7"), text)
        self.assertIn(i18n.tf(self.UID, "diag_docker_stale_since_fmt", "01.10.2026"), text)
        self.assertNotIn(i18n.t(self.UID, "diag_docker_mismatch"), text, "о том же — одной строкой, а не двумя")
        self.assertIn("mt_dkr", datas)
        # Служба свежая — строки нет, кнопка остаётся: Docker бывает странным и без обновления
        self._bridge(quality={"status": "success", "quality": {"docker": True, "ip_forward": "1", "network": True,
                                                               "docker_info": dict(DK_STALE_INFO, stale=False, mismatch=False)}})
        text, datas = self._last_edit("mt_diag")
        self.assertNotIn(i18n.tf(self.UID, "diag_docker_stale_fmt", "29.1.3", "24.0.7"), text)
        self.assertIn("mt_dkr", datas)

    def test_button_asks_first_and_warns_everyone(self):
        args = self._args_file()
        text, datas = self._last_edit("mt_dkr")
        self.assertIn(i18n.t(self.UID, "mt_dkr_body"), text)
        self.assertIn("10–30", text)
        self.assertEqual(datas, ["mt_dkr_do", "mt_diag"])
        self.assertFalse(self._bridged(args, "docker-restart"), "вопрос Docker не трогает")
        # Из уведомления — тот же вопрос, новым сообщением под ним
        rec, _ = self._press("nt~mt_dkr")
        replies = [c for c in rec.calls if c[0] == "reply_text"]
        self.assertTrue(replies, "экран вопроса — новым сообщением")
        self.assertFalse(self._bridged(args, "docker-restart"))

    def test_restart_runs_the_bridge_and_reports(self):
        args = self._args_file()
        self._bridge(**{"docker-restart": {"status": "success", "message": "ok", "duration_s": 9, "restarted": ["awg3", "awg2"],
                                           "failed": [], "daemon_before": "24.0.7", "daemon_after": "29.1.3", "stale": False}})
        text, datas = self._last_edit("mt_dkr_do")
        self.assertTrue(self._bridged(args, "docker-restart"))
        self.assertIn(i18n.tf(self.UID, "mt_dkr_done_fmt", 9, "29.1.3"), text)
        self.assertIn(i18n.tf(self.UID, "mt_dkr_profiles_fmt", FX31 + ", " + FX20), text)
        self.assertEqual(datas, ["mt_diag"])

    def test_failure_says_why_and_how_by_hand(self):
        for answer, why in (
                ({"status": "error", "code": "docker_restart_timeout", "message": "демон не ответил", "restarted": [], "failed": []},
                 i18n.t(self.UID, "dkr_why_timeout")),
                ({"status": "error", "code": "restart_failed", "message": "профили", "restarted": ["awg2"],
                  "failed": [{"profile": "awg3", "code": "container_not_up", "message": "x"}]},
                 i18n.tf(self.UID, "dkr_why_profiles_fmt", FX31))):
            self._bridge(**{"docker-restart": answer})
            text, datas = self._last_edit("mt_dkr_do")
            self.assertIn(i18n.tf(self.UID, "mt_dkr_failed_fmt", why), text, answer["code"])
            self.assertIn("systemctl restart docker", text)
            self.assertEqual(datas, ["mt_diag"])


KM_LABEL = "3.1.20260906+kaskad-k31.1"


def km_state(**o):
    """kmod-status.kmod (ga_kmod_status_json, CONTRACT §4.7a): по умолчанию модуль не ставился, профили на программе."""
    base = {"loaded": False, "version": "", "patched": False, "warn": False, "state": "off", "code": "", "reason": "",
            "kernel": "6.8.0-36-generic", "want": "", "installed": False, "installed_version": "", "owner": "", "label": KM_LABEL,
            "profiles": {"awg2": {"engine": "program", "want": ""}, "awg3": {"engine": "program", "want": ""}},
            "progress": None, "selftest": None, "failed": None, "packages": []}
    base.update(o)
    return base


KM_ON = km_state(loaded=True, version=KM_LABEL, patched=True, state="on", installed=True, installed_version=KM_LABEL, owner="goamnezia",
                 want="kernel", profiles={"awg2": {"engine": "kernel", "want": "kernel"}, "awg3": {"engine": "kernel", "want": "kernel"}},
                 packages=["dkms", "gcc-13"])
KM_FB_REASON = "нет модуля для ядра 6.8.0-146-generic — профили работают программой; пересобрать? (Обслуживание → Ускорение AmneziaWG → 3)"
KM_FALLBACK = km_state(state="fallback", code="no_module_for_kernel", kernel="6.8.0-146-generic", want="kernel", owner="goamnezia",
                       reason=KM_FB_REASON, profiles={"awg2": {"engine": "program", "want": "kernel"}, "awg3": {"engine": "program", "want": "kernel"}})
KM_CHECK = {"ok": True, "code": "", "reason": "", "kernel": "6.8.0-36-generic", "reuse": False, "module": {"ko_version": ""},
            "plan": {"missing": ["dkms", "gcc-13", "linux-headers-6.8.0-36-generic"], "size_mb": 214, "kernel": False,
                     "headers_meta": {"name": "linux-headers-generic", "installed": False, "kernel": True, "size_mb": 891}}}
KM_INSTALL_OK = {"status": "success", "message": "Модуль ядра установлен", "state": "ready", "version": KM_LABEL,
                 "kernel": "6.8.0-36-generic", "owner": "goamnezia", "reused": False, "packages": ["dkms"], "headers_meta_skipped": "",
                 "want": "program", "selftest": {"ok": True, "cases": [
                     {"profile": "awg3", "line": "3.1", "client": "kernel", "ok": True, "detail": ""},
                     {"profile": "awg3", "line": "3.1", "client": "program", "ok": True, "detail": ""}]}}
KM_ENGINE_OK = {"status": "success", "message": "Движок профилей сменён", "engine": "kernel", "want": "kernel", "rolled_back": False,
                "profiles": [{"profile": "awg2", "from": "program", "to": "kernel", "restarted": True, "alive_before": 2, "returned": 2,
                              "return_s": 17, "note": ""},
                             {"profile": "awg3", "from": "program", "to": "kernel", "restarted": True, "alive_before": 0, "returned": 0,
                              "return_s": None, "note": "no_clients"}]}
KM_ENGINE_LOST = {"status": "error", "code": "kmod_clients_lost", "engine": "kernel", "want": "program", "rolled_back": True,
                  "message": "Профиль awg2: из 3 человек на связи за 3 мин не вернулся ни один — сервер откатан на прежний движок",
                  "profiles": [{"profile": "awg2", "from": "program", "to": "kernel", "restarted": True, "alive_before": 3, "returned": 0,
                                "return_s": None, "note": "lost"}]}


class KmodBotScreenTests(_BotScreenCase):
    """Ускорение AmneziaWG — модуль ядра по выбору (KMOD_PLAN этап Б, CONTRACT §4.7a) в боте: «⚙️ Обслуживание → ⚡ Ускорение
    AmneziaWG» — состояние словами SSH и кнопки по нему; «Включить» — проверка хоста и согласие кнопками (метапакет, который
    тянет новое ядро, — отдельной кнопкой); остальные — вопрос; действие — цепочка моста под замком бота с ходом правкой одного
    сообщения и итогом на его месте; откат и удаление — понятными словами; движок в карточке профиля и строка на главном."""

    def setUp(self):
        self.calls = []
        self.answers = {}
        saved = bot.run_bot_action

        async def fake(action, *a, **kw):
            if action.startswith("kmod-"):
                self.calls.append((action, {k: v for k, v in kw.items() if k not in ("who", "timeout")}, kw.get("who")))
                ans = self.answers.get(action, {"status": "success", "message": "ok"})
                if callable(ans):
                    ans = await ans(kw)
                return ans
            return await saved(action, *a, **kw)
        bot.run_bot_action = fake
        self.addCleanup(setattr, bot, "run_bot_action", saved)

    def status(self, km):
        self.answers["kmod-status"] = {"status": "success", "message": "ok", "kmod": km}

    def mutations(self):
        return [(a, p) for a, p, _ in self.calls if a not in ("kmod-status", "kmod-check")]

    def test_maintenance_leads_to_the_screen_and_states_speak_ssh_words(self):
        _, datas, _ = self._screen("sec_maint")
        self.assertIn("mt_kmod", datas)
        self.status(km_state())
        text, datas, _ = self._screen("mt_kmod")
        for key in ("km_title", "km_note", "km_now_program", "km_off"):
            self.assertIn(i18n.t(self.UID, key), text, key)
        self.assertIn("<code>6.8.0-36-generic</code>", text)
        self.assertIn(FX20 + " — %s" % i18n.t(self.UID, "km_word_program"), text)
        self.assertEqual(datas, ["km_on", "mt_kmod", "sec_maint"])
        self.status(KM_ON)
        text, datas, _ = self._screen("mt_kmod")
        self.assertIn(i18n.tf(self.UID, "km_now_kernel_fmt", KM_LABEL), text)
        self.assertEqual(datas, ["km_prog", "km_rb", "km_rm", "mt_kmod", "sec_maint"])
        self.status(KM_FALLBACK)
        text, datas, _ = self._screen("mt_kmod")
        self.assertIn("⚠️ Нет модуля для ядра 6.8.0-146-generic — профили работают программой; пересобрать?", text)
        self.assertNotIn("→ 3", text, "путь SSH-меню в боте лишний — кнопка рядом")
        self.assertEqual(datas, ["km_rb", "km_prog", "km_rm", "mt_kmod", "sec_maint"])
        self.status(km_state(state="busy", code="install", progress={"action": "install", "step": "build",
                                                                      "text": "Собираю модуль под ядро 6.8.0-36-generic (DKMS, 1–3 минуты)"}))
        text, datas, _ = self._screen("mt_kmod")
        self.assertIn(i18n.tf(self.UID, "km_busy_now_fmt", "Собираю модуль под ядро 6.8.0-36-generic (DKMS, 1–3 минуты)"), text)
        self.assertEqual(datas, ["mt_kmod", "sec_maint"], "пока идёт действие — только «Обновить» и «Назад»")
        self.assertEqual(self.mutations(), [], "экран ничего не меняет")

    def test_enable_checks_the_host_and_asks_with_buttons(self):
        self.answers["kmod-check"] = {"status": "success", "message": "ok", "check": KM_CHECK}
        text, datas, _ = self._screen("km_on")
        self.assertIn(i18n.t(self.UID, "km_consent_q"), text)
        self.assertIn(i18n.tf(self.UID, "km_plan_packages_fmt", "dkms, gcc-13, linux-headers-6.8.0-36-generic", 214), text)
        self.assertIn(i18n.tf(self.UID, "km_meta_warn_fmt", "linux-headers-generic", 891), text)
        self.assertIn("15–25", text)
        self.assertEqual(datas, ["km_go~enable~off", "km_go~enable~on", "km_no"], "метапакет — отдельной кнопкой, по умолчанию нет")
        # Метапакет нового ядра не тянет — одна кнопка «Да», он ставится сам
        plain = dict(KM_CHECK, plan=dict(KM_CHECK["plan"], headers_meta=dict(KM_CHECK["plan"]["headers_meta"], kernel=False)))
        self.answers["kmod-check"] = {"status": "success", "message": "ok", "check": plain}
        text, datas, _ = self._screen("km_on")
        self.assertIn(i18n.tf(self.UID, "km_meta_auto_fmt", "linux-headers-generic"), text)
        self.assertEqual(datas, ["km_go~enable~off", "km_no"])
        # Нельзя здесь — одной фразой, ничего не ставится
        lxc = dict(KM_CHECK, ok=False, code="container",
                   reason="модуль ядра здесь нельзя: это контейнер LXC, модули ядра в нём не грузятся — оставляю программу amneziawg-go")
        self.answers["kmod-check"] = {"status": "success", "message": "ok", "check": lxc}
        text, datas, _ = self._screen("km_on")
        self.assertIn(i18n.t(self.UID, "km_not_here"), text)
        self.assertIn("Модуль ядра здесь нельзя: это контейнер LXC", text)
        self.assertEqual(datas, ["mt_kmod"])
        text, datas, _ = self._screen("km_no")
        self.assertIn(i18n.t(self.UID, "km_declined"), text)
        self.assertEqual(self.mutations(), [], "проверка и «Нет» ничего не ставят")

    def test_enable_runs_the_chain_with_progress_in_one_message(self):
        saved = bot.KMOD_PROGRESS_EVERY
        bot.KMOD_PROGRESS_EVERY = 0.02
        self.addCleanup(setattr, bot, "KMOD_PROGRESS_EVERY", saved)
        texts = iter(["Ставлю пакеты для сборки: dkms gcc-13", "Собираю модуль под ядро 6.8.0-36-generic (DKMS, 1–3 минуты)",
                      "Профиль awg2 → модуль ядра: перезапуск"])
        progress = {"text": next(texts)}

        async def status(kw):
            r = {"status": "success", "message": "ok", "kmod": km_state(state="busy", code="install", progress=dict(progress))}
            progress["text"] = next(texts, progress["text"])
            return r

        def slow(answer):
            async def go(kw):
                await asyncio.sleep(0.15)
                return answer
            return go
        self.answers["kmod-status"] = status
        self.answers["kmod-install"] = slow(KM_INSTALL_OK)
        self.answers["kmod-engine"] = slow(KM_ENGINE_OK)
        rec, _ = self._press("km_go~enable~on")
        edits = [c for c in rec.calls if c[0] == "edit"]
        self.assertGreaterEqual(len(edits), 3, "ход — правками того же сообщения")
        progress_texts = [str(e[1][0]) for e in edits[:-1]]
        self.assertTrue(all(i18n.t(self.UID, "km_busy_enable") in t for t in progress_texts))
        self.assertTrue(any("Ставлю пакеты для сборки: dkms gcc-13" in t for t in progress_texts), progress_texts)
        self.assertTrue(all(e[2].get("reply_markup") is None for e in edits[:-1]), "пока идёт — без кнопок")
        final = str(edits[-1][1][0])
        self.assertIn("✅ " + i18n.tf(self.UID, "km_engine_done_fmt", i18n.t(self.UID, "km_word_kernel")), final)
        self.assertIn("✓ " + i18n.tf(self.UID, "km_selftest_row_fmt", FX31, "3.1", i18n.t(self.UID, "km_client_kernel")), final)
        self.assertIn(i18n.tf(self.UID, "km_row_back_fmt", FX20, i18n.t(self.UID, "km_word_kernel"), 2, 2, 17), final)
        self.assertIn(i18n.tf(self.UID, "km_row_no_clients_fmt", FX31, i18n.t(self.UID, "km_word_kernel")), final)
        markup = edits[-1][2]["reply_markup"]
        self.assertEqual([b.callback_data for row in markup.inline_keyboard for b in row], ["mt_kmod", "sec_maint"])
        self.assertEqual(self.mutations(), [("kmod-install", {"rebuild": "off", "headers_meta": "on"}),
                                            ("kmod-engine", {"engine": "kernel"})])
        self.assertTrue(all(who == self.UID for a, _, who in self.calls), "в аудит — кто нажал")

    def test_rollback_and_failed_install_are_said_plainly(self):
        self.answers["kmod-install"] = KM_INSTALL_OK
        self.answers["kmod-engine"] = KM_ENGINE_LOST
        text, datas, _ = self._screen("km_go~enable~off")
        self.assertIn("⚠️ <b>%s</b>" % i18n.tf(self.UID, "km_rolled_back_fmt", i18n.t(self.UID, "km_word_kernel")), text)
        self.assertIn("из 3 человек на связи за 3 мин не вернулся ни один — сервер откатан на прежний движок", text)
        self.assertIn(i18n.tf(self.UID, "km_row_lost_fmt", FX20, 3), text)
        self.assertEqual(datas, ["mt_kmod", "sec_maint"])
        self.calls.clear()
        self.answers["kmod-install"] = {"status": "error", "code": "kmod_build_failed", "message": "Модуль не собрался под ядро 6.8.0-36-generic"}
        text, _, _ = self._screen("km_go~enable~off")
        self.assertIn("❌ <b>%s</b>" % i18n.t(self.UID, "km_fail_enable"), text)
        self.assertIn("Модуль не собрался под ядро 6.8.0-36-generic", text)
        self.assertIn(i18n.t(self.UID, "km_left_program"), text)
        self.assertEqual([a for a, _ in self.mutations()], ["kmod-install"], "без модуля профили не переводятся")

    def test_program_rebuild_and_remove_ask_first_then_run(self):
        self.status(KM_ON)
        text, datas, _ = self._screen("km_prog")
        self.assertIn(i18n.t(self.UID, "km_program_q"), text)
        self.assertIn(i18n.tf(self.UID, "km_program_warn_fmt", FX31 + ", " + FX20), text)
        self.assertEqual(datas, ["km_go~program", "mt_kmod"])
        text, datas, _ = self._screen("km_rb")
        self.assertIn(i18n.tf(self.UID, "km_rebuild_warn_fmt", "6.8.0-36-generic"), text)
        self.assertEqual(datas, ["km_go~rebuild", "mt_kmod"])
        self.status(dict(KM_ON, owner="kaskad"))
        text, datas, _ = self._screen("km_rm")
        self.assertIn(i18n.t(self.UID, "km_remove_warn"), text)
        self.assertIn(i18n.t(self.UID, "km_remove_neighbour"), text)
        self.assertEqual(datas, ["km_go~remove", "mt_kmod"])
        self.assertEqual(self.mutations(), [], "вопрос ничего не меняет")
        text, _, _ = self._screen("km_go~program")
        self.assertEqual(self.mutations(), [("kmod-engine", {"engine": "program"})])
        self.calls.clear()
        self.answers["kmod-install"] = {"status": "success", "state": "fallback", "want": "kernel", "version": KM_LABEL, "kernel": "6.8.0-146-generic"}
        self._screen("km_go~rebuild")
        self.assertEqual(self.mutations(), [("kmod-install", {"rebuild": "on", "headers_meta": "off"}), ("kmod-engine", {"engine": "kernel"})])
        self.calls.clear()
        self.answers["kmod-remove"] = {"status": "success", "removed": True, "kept_for_neighbour": False, "packages": ["dkms", "gcc-13"],
                                       "command": "apt remove dkms gcc-13"}
        text, _, _ = self._screen("km_go~remove")
        self.assertEqual(self.mutations(), [("kmod-remove", {})])
        self.assertIn("✅ " + i18n.t(self.UID, "km_removed"), text)
        self.assertIn(i18n.tf(self.UID, "km_remove_packages_fmt", "apt remove dkms gcc-13"), text)

    def test_busy_bot_and_bad_buttons_touch_nothing(self):
        for data in ("km_go~format", "km_go~program~on", "km_go~enable~maybe", "km_go~enable~on~x"):
            text, _, _ = self._screen(data)
            self.assertIn(i18n.t(self.UID, "err_bad_input"), text, data)

        async def scenario():
            with patch.object(bot, "_BOT_ACTION_LOCK", asyncio.Lock()):
                async with bot._BOT_ACTION_LOCK:
                    upd = FakeUpdate(self.UID, data="km_go~program")
                    await bot.handle_callback(upd, FakeContext())
                    return upd.rec
        rec = run(scenario())
        edit = [c for c in rec.calls if c[0] == "edit"][-1]
        self.assertIn(i18n.t(self.UID, "action_busy_title"), str(edit[1][0]))
        self.assertEqual(self.mutations(), [])

    def test_main_screen_and_profile_card_show_the_engine(self):
        st = {"status": "success", "profiles": [], "kmod": dict(KM_FALLBACK)}
        lines, rows = bot.dashboard_problems(self.UID, st)
        self.assertIn("⚠️ " + i18n.tf(self.UID, "dash_kmod_fallback_fmt",
                                       "нет модуля для ядра 6.8.0-146-generic — профили работают программой; пересобрать?"), lines)
        self.assertIn("mt_kmod", [b.callback_data for r in rows for b in r])
        failed = km_state(state="failed", code="kmod_build_failed", reason="Модуль не собрался под ядро 6.8.0-36-generic: см. журнал")
        lines, _ = bot.dashboard_problems(self.UID, {"status": "success", "profiles": [], "kmod": failed})
        self.assertIn("⚠️ " + i18n.tf(self.UID, "dash_kmod_failed_fmt", "Модуль не собрался под ядро 6.8.0-36-generic: см. журнал"), lines)
        for km in (km_state(), KM_ON, None, {"state": "fallback"}):
            self.assertEqual(bot.dashboard_problems(self.UID, {"status": "success", "profiles": [], "kmod": km})[0], [], km)
        status = {"status": "success", "profiles": [
            {"profile": "awg2", "state": "running", "port": 443, "line": "2.0", "peers": 2, "engine": "program", "engine_want": "kernel"},
            {"profile": "awg3", "state": "running", "port": 39001, "line": "3.1", "peers": 1, "engine": "kernel", "engine_want": "kernel"}]}
        card = bot.profile_card_text(self.UID, "awg2", status, {"profiles": {"awg2": {"iface": "awg0"}}})
        self.assertIn("%s: <b>%s</b>" % (i18n.t(self.UID, "pr_engine"), i18n.t(self.UID, "km_word_program")), card)
        self.assertIn(i18n.tf(self.UID, "pr_engine_pending_fmt", i18n.t(self.UID, "km_word_kernel")), card)
        card = bot.profile_card_text(self.UID, "awg3", status, {"profiles": {}})
        self.assertIn("%s: <b>%s</b>" % (i18n.t(self.UID, "pr_engine"), i18n.t(self.UID, "km_word_kernel")), card)
        self.assertNotIn(i18n.tf(self.UID, "pr_engine_pending_fmt", i18n.t(self.UID, "km_word_kernel")), card)
        # Старый контейнер приложения на wg0 модуль AmneziaWG не подхватит — строки нет, как в SSH
        legacy = bot.profile_card_text(self.UID, "awg2", status, {"profiles": {"awg2": {"iface": "wg0"}}})
        self.assertNotIn(i18n.t(self.UID, "pr_engine") + ":", legacy)


def _page_ui():
    """Словари страницы подключения (UI в lib/pages_server.py) — разбором, без импорта службы."""
    with open(os.path.join(ROOT, "lib", "pages_server.py"), encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "UI" for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError("в lib/pages_server.py нет словаря UI")


# Шаги в приложении цепочкой: «+» → «QR-код» или “Add” → “QR code”.
_APP_STEPS = re.compile(r"(?:«[^»]+»|“[^”]+”)(?: → (?:«[^»]+»|“[^”]+”))+")


class NoviceCritic050Tests(_BotScreenCase):
    """Критик-новичок на выпущенной 0.5.0 (docs/FINDINGS_TRACKER.md). NC-23: значок перевыпуска, линии
    вместо awg1/awg2/awg3 в /key, шаги импорта как на странице, «был в сети … назад» вместо рукопожатия
    (срок страницы «12 ч / 3 дн / навсегда» — PagesTests.test_custom_ttl_in_russian_words). NC-08: «без
    лимита» и «бессрочно» — кнопками, а не цифрой 0 (в SSH «0» — это «назад»); прежний «0» работает."""

    @staticmethod
    def _markup_buttons(call):
        """{подпись: callback_data} клавиатуры из записанного edit или reply_text."""
        markup = call[2].get("reply_markup")
        return {b.text: b.callback_data for row in (markup.inline_keyboard if markup else []) for b in row}

    def _args_file(self):
        path = os.path.join(TMP_ROOT, "nc050_%s.txt" % self._testMethodName)
        open(path, "w").close()
        os.environ["FAKE_BOT_ARGS_FILE"] = path
        self.addCleanup(os.environ.pop, "FAKE_BOT_ARGS_FILE", None)
        return path

    def test_no_limit_and_no_end_date_are_buttons_not_zero(self):
        for lang in ("ru", "en"):
            for key in ("user_ask_quota", "user_ask_days", "user_bad_quota_fmt", "user_bad_days_fmt"):
                self.assertNotRegex(i18n.t_in(lang, key), r"(?<![0-9])0(?![0-9])", "%s/%s: о нуле экран молчит" % (lang, key))
            self.assertIn(i18n.t_in(lang, "user_quota_unlimited_btn"), i18n.t_in(lang, "user_bad_quota_fmt"), lang)
            self.assertIn(i18n.t_in(lang, "user_expiry_never_btn"), i18n.t_in(lang, "user_bad_days_fmt"), lang)
        # карточка: «Лимит» и «Срок»
        for screen, key, zero in (("u_quota~anna", "user_quota_unlimited_btn", "uq_set~anna~0"),
                                  ("u_exp~anna", "user_expiry_never_btn", "ue_set~anna~0")):
            rec, _ = self._press(screen)
            buttons = self._markup_buttons([c for c in rec.calls if c[0] == "edit"][-1])
            self.assertEqual(buttons.get(i18n.t(self.UID, key)), zero, screen)
            self.assertFalse([label for label in buttons if label.startswith("0 ")], buttons)
        # мастер добавления: те же кнопки на шагах лимита и срока
        rec, ctx = self._press("u_add")
        self._say("nc08btn", ctx)
        rec, ctx = self._press("ua_done", ctx)
        buttons = self._markup_buttons([c for c in rec.calls if c[0] == "edit"][-1])
        self.assertEqual(buttons.get(i18n.t(self.UID, "user_quota_unlimited_btn")), "ua_q~0")
        rec, ctx = self._press("ua_q~50", ctx)
        buttons = self._markup_buttons([c for c in rec.calls if c[0] == "edit"][-1])
        self.assertEqual(buttons.get(i18n.t(self.UID, "user_expiry_never_btn")), "ua_d~0")
        self.assertFalse([label for label in buttons if label.startswith("0 ")], buttons)

    def test_zero_still_works_and_button_words_too(self):
        for lang in ("ru", "en"):
            self.assertEqual(bot.parse_quota_gb(i18n.t_in(lang, "user_quota_unlimited_btn")), 0, lang)
            self.assertEqual(bot.parse_expiry_days(i18n.t_in(lang, "user_expiry_never_btn")), 0, lang)
        for text, want in (("0", 0), ("50", 50), ("100000", 100000), ("без лимита", 0), ("«Без лимита»", 0),
                           ("Unlimited", 0)):
            self.assertEqual(bot.parse_quota_gb(text), want, text)
        for text in ("-1", "100001", "много", "50 ГБ", ""):
            self.assertIsNone(bot.parse_quota_gb(text), text)
        for text, want in (("0", 0), ("30", 30), ("3650", 3650), ("бессрочно", 0), ("навсегда", 0), ("never", 0)):
            self.assertEqual(bot.parse_expiry_days(text), want, text)
        for text in ("3651", "год", ""):
            self.assertIsNone(bot.parse_expiry_days(text), text)
        args = self._args_file()
        # карточка: прежний «0» и слова кнопки доходят до ядра нулём, чужое — понятный отказ, ожидание живо
        for screen, typed, want in (("u_quota~anna", "0", "--quota-gb=0"), ("u_quota~anna", "без лимита", "--quota-gb=0"),
                                    ("u_quota~anna", "25", "--quota-gb=25"), ("u_exp~anna", "0", "--days=0"),
                                    ("u_exp~anna", "Бессрочно", "--days=0"), ("u_exp~anna", "45", "--days=45")):
            open(args, "w").close()
            _, ctx = self._press(screen)
            self._say(typed, ctx)
            with open(args, encoding="utf-8") as fh:
                self.assertIn(want, fh.read(), "%s ← %r" % (screen, typed))
            self.assertIsNone(bot.flow_get(ctx), screen)
        for screen, key, limit in (("u_quota~anna", "user_bad_quota_fmt", bot.MAX_QUOTA_GB),
                                   ("u_exp~anna", "user_bad_days_fmt", bot.MAX_DAYS)):
            _, ctx = self._press(screen)
            rec = self._say("много", ctx)
            self.assertEqual([str(c[1][0]) for c in rec.calls if c[0] == "reply_text"], [i18n.tf(self.UID, key, limit)])
            self.assertIsNotNone(bot.flow_get(ctx), "ошибка ввода ожидание не снимает")
        # мастер: «0» на лимите и «бессрочно» на сроке
        open(args, "w").close()
        _, ctx = self._press("u_add")
        self._say("nc08text", ctx)
        self._press("ua_done", ctx)
        self._say("0", ctx)
        self.assertEqual(ctx.user_data["flow"]["data"]["step"], "days")
        self._say("бессрочно", ctx)
        self.assertNotIn("flow", ctx.user_data)
        with open(args, encoding="utf-8") as fh:
            line = [l for l in fh.read().splitlines() if "--action=user-add" in l][-1].split("\t")
        self.assertIn("--quota-gb=0", line)
        self.assertIn("--days=0", line)

    def _cmd(self, cmd, args):
        upd = FakeUpdate(self.UID, text="/cmd")
        run(cmd(upd, FakeContext(args)))
        return [c for c in upd.rec.calls if c[0] == "reply_text"]

    def test_reissue_is_marked_dangerous_not_random(self):
        """«🎲 Перевыпустить ключ»: кубик у разрушительного действия читался как «наугад»."""
        for lang in ("ru", "en"):
            for key in ("user_peer_reissue_btn", "ag_btn_rotate", "pr_regen", "se_mini_rotate"):
                self.assertTrue(i18n.t_in(lang, key).startswith("⚠️ "), "%s/%s" % (lang, key))
            for key in ("user_name_random", "user_name_again", "ag_btn_port_random", "tpl_random_btn"):
                self.assertTrue(i18n.t_in(lang, key).startswith("🎲 "), "%s/%s" % (lang, key))
        # на экране: карточка человека с ключом из приложения Amnezia
        rec, _ = self._press("u_view~proba")
        labels = [b.text for c in rec.calls if c[0] == "edit" for row in c[2]["reply_markup"].inline_keyboard for b in row]
        self.assertIn("2.0 · 443 · " + i18n.t(self.UID, "user_peer_reissue_btn"), labels)
        self.assertFalse([label for label in labels if "🎲" in label], labels)

    def test_key_command_takes_a_line(self):
        """/key anna 2.0 — профиль так, как его называют экраны; прежний /key anna awg2 тоже работает."""
        for arg, want in (("2.0", "awg2"), ("3.1", "awg3"), ("awg2", "awg2"), ("AWG3", "awg3")):
            reply = self._cmd(bot.cmd_key, ["anna", arg])[-1]
            datas = [b.callback_data for row in reply[2]["reply_markup"].inline_keyboard for b in row]
            self.assertIn("u_key~anna~%s~qrconf" % want, datas, arg)
            self.assertIn(i18n.tf(self.UID, "key_title_fmt", "anna", bot.profile_name(self.UID, want)), str(reply[1][0]))
        self.assertEqual(bot.profile_from_arg("1.0"), "awg1", "1.0 и 1.5 — одно семейство")
        self.assertEqual(bot.profile_from_arg("2"), "")
        # линия, на которой у человека ключа нет: где ключи есть, а не «профилей нет вовсе»
        texts = [str(c[1][0]) for c in self._cmd(bot.cmd_key, ["anna", "1.5"])]
        self.assertEqual(texts, ["❌ " + i18n.tf(self.UID, "cmd_key_no_profile_fmt", "anna", bot.profile_name(self.UID, "awg1"),
                                                 FX31 + ", " + FX20)])
        # ни линия, ни профиль — подсказка с линиями, без awg1/awg2/awg3
        usage = bot.key_usage_text(self.UID)
        for args in (["anna", "7.7"], ["anna", "awg9"], []):
            self.assertEqual([str(c[1][0]) for c in self._cmd(bot.cmd_key, args)], [usage], args)
        # Что набирать — линия; подсказка без порта и без awg1/awg2/awg3
        want = ", ".join("AmneziaWG %s (%s)" % (bot.profile_line(p), bot.profile_line(p)) for p in bot.PROFILE_IDS)
        self.assertIn(want, usage)
        for lang in ("ru", "en"):
            text = i18n.t_in(lang, "cmd_key_usage")
            self.assertIn("/key anna 2.0", text, lang)
            self.assertIsNone(_BARE_ID.search(text), "%s: %r" % (lang, text))
        self.assertIsNone(_BARE_ID.search(usage), usage)

    def test_import_steps_are_the_same_as_on_the_page(self):
        """«Импорт → сканировать» и «Добавить сервер → сканировать QR» против «+» → «QR-код» на странице
        подключения: шаги в подписях к ключам — те же, что там, на обоих языках."""
        ui = _page_ui()
        for lang in ("ru", "en"):
            conf = _APP_STEPS.search(ui[lang]["qr_conf_cap"]).group(0)
            vpn = _APP_STEPS.search(ui[lang]["qr_vpn_cap1"]).group(0)
            self.assertIn(conf, i18n.t_in(lang, "key_qr_conf_hint"), lang)
            for key in ("key_qr_vpn_hint", "key_qr_vpn_series_hint"):
                self.assertIn(vpn, i18n.t_in(lang, key), "%s/%s" % (lang, key))
            # файл и ключ текстом начинаются с той же кнопки приложения: «+» у AmneziaWG, «Добавить» у AmneziaVPN
            self.assertIn(conf.split(" → ")[0] + " → ", i18n.t_in(lang, "key_conf_hint"), lang)
            for key in ("key_vpn_hint", "key_vpn_file_hint"):
                self.assertIn(vpn.split(" → ")[0], i18n.t_in(lang, key), "%s/%s" % (lang, key))
            for key in ("key_qr_conf_hint", "key_qr_vpn_hint", "key_qr_vpn_series_hint", "key_vpn_hint"):
                text = i18n.t_in(lang, key).lower()
                for old in ("сканировать", "добавить сервер", "import → scan", "add server"):
                    self.assertNotIn(old, text, "%s/%s" % (lang, key))
        # на экране: подпись к QR .conf
        rec, _ = self._press("u_key~anna~awg2~qrconf")
        captions = [str(c[2].get("caption") or "") for c in rec.calls if c[0] == "reply_photo"]
        self.assertTrue(captions and i18n.t(self.UID, "key_qr_conf_hint") in captions[0], captions)

    def test_last_seen_instead_of_handshake(self):
        """«рукопожатие моложе 3 минут» — слово протокола; человеку — «был в сети … назад»."""
        text, _, _ = self._screen("u_view~anna")                 # у anna ключ на 3.1 выходил на связь давно
        self.assertIn(i18n.tf(self.UID, "peer_handshake_fmt", bot._ago_text(self.UID, int(time.time()) - 1700000000)), text)
        self.assertIn(i18n.t(self.UID, "peer_handshake_never"), text)
        for lang, want in (("ru", "был в сети"), ("en", "last seen")):
            self.assertTrue(i18n.t_in(lang, "peer_handshake_fmt").startswith(want), lang)
        with open(os.path.join(BOT_SRC, "lang", "ru.json"), encoding="utf-8") as fh:
            ru = json.load(fh)
        self.assertEqual(sorted(k for k, v in ru.items() if "рукопожат" in v.lower()), [])
        with open(os.path.join(BOT_SRC, "lang", "en.json"), encoding="utf-8") as fh:
            en = json.load(fh)
        self.assertEqual(sorted(k for k, v in en.items() if "handshake" in v.lower()), [])


# ════════════════════════════════════════════════════════════════════════════
# install_bot.sh: токен только через stdin curl (SEC-B1), первый админ — привязкой сообщением (SEC-B00)
# ════════════════════════════════════════════════════════════════════════════

INSTALL_BOT = os.path.join(BOT_SRC, "install_bot.sh")
FAKE_TOKEN = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"


def _install_sh_functions(*names):
    """Определения функций install.sh по именам — проверить кусок SSH-меню, не запуская меню целиком."""
    with open(os.path.join(ROOT, "install.sh"), encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    out = []
    for name in names:
        for i, line in enumerate(lines):
            if not line.startswith(name + "() {"):
                continue
            if line.rstrip().endswith("}") and line.count("{") == line.count("}"):
                out.append(line)
            else:
                j = i
                while lines[j] != "}":
                    j += 1
                out.extend(lines[i:j + 1])
            break
        else:
            raise AssertionError("нет функции %s в install.sh" % name)
    return "\n".join(out)

# Заглушка curl: пишет argv и stdin (туда установщик кладёт конфиг с адресом Bot API) и
# отвечает файлами replies/<номер вызова> — телом и последней строкой HTTP-кодом, как
# `curl -w '\n%{http_code}'`. Метка replies/<n>.wait — дождаться, пока тест положит ответ.
FAKE_CURL = r'''#!/bin/bash
d="$FAKE_TG_DIR"
n=$(cat "$d/count" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > "$d/count"
printf '%s\n' "$*" >> "$d/argv"
cat >> "$d/stdin"; printf '\n--\n' >> "$d/stdin"
r="$d/replies/$n"
if [ -f "$r.wait" ]; then
    for _ in $(seq 1 200); do [ -f "$r" ] && break; sleep 0.05; done
fi
[ -f "$r" ] || r="$d/replies/default"
if [ -f "$r" ]; then cat "$r"; else printf '{"ok":false}\n500'; fi
'''


def _bash5():
    """bash 4+ для установщика (declare -A, ${x,,}); на macOS — BASH5, как в run_static.sh.

    Путь — всегда абсолютный: run_static.sh экспортирует BASH5=bash (голое имя), subprocess
    находит его по PATH, а os.execve в pty-тесте — нет. Упавший execve оставлял дочерний
    процесс — копию всего прогона unittest — и тот гнал остаток тестов параллельно с
    родителем по тем же временным каталогам."""
    exe = shutil.which(os.environ.get("BASH5") or "bash")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "-c", "echo ${BASH_VERSINFO[0]}"], capture_output=True, text=True, timeout=10)
        return exe if int(out.stdout.strip() or 0) >= 4 else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _update(uid, from_id, text, chat_type="private", first_name="Owner"):
    return {"update_id": uid, "message": {
        "message_id": uid, "date": 0, "text": text,
        "chat": {"id": from_id if chat_type == "private" else -100500, "type": chat_type},
        "from": {"id": from_id, "is_bot": False, "first_name": first_name}}}


class InstallBotTests(unittest.TestCase):
    """install_bot.sh через source (функции) и через pty (интерактивный путь и окно привязки целиком):
    администратор — привязкой сообщением с подтверждением в терминале, одноразовый код — только без
    терминала и только вместе с окном (docs/BOT.md «Контракт привязки», C47-1, HB-1, HB-8, HB-10)."""

    @classmethod
    def setUpClass(cls):
        cls.bash = _bash5()
        if cls.bash is None:
            raise unittest.SkipTest("нужен bash 4+ (задайте BASH5)")

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="install-bot-", dir=TMP_ROOT)
        self.bin = os.path.join(self.dir, "bin")
        os.makedirs(self.bin)
        os.makedirs(os.path.join(self.dir, "replies"))
        with open(os.path.join(self.bin, "curl"), "w", encoding="utf-8") as fh:
            fh.write(FAKE_CURL)
        os.chmod(os.path.join(self.bin, "curl"), 0o755)
        self.botdir = os.path.join(self.dir, "botdir")

    # ── помощники ──────────────────────────────────────────────────────────
    def _reply(self, n, obj, code=200):
        # Как у Bot API — без пробелов после «:» и «,»
        with open(os.path.join(self.dir, "replies", str(n)), "w", encoding="utf-8") as fh:
            fh.write(json.dumps(obj, separators=(",", ":")) + "\n" + str(code))

    def _getme(self, n=1):
        self._reply(n, {"ok": True, "result": {"id": 1, "is_bot": True, "username": "my_test_bot"}})

    def _env(self, extra=None):
        env = {k: v for k, v in os.environ.items()
               if k not in ("BOT_TOKEN", "ALLOWED_IDS", "OWNER_ID", "GOAMNEZIA_ADMIN_CLAIM", "GOAMNEZIA_ADMIN_PAIR",
                            "GOAMNEZIA_LANG")}
        env.update({"INSTALL_BOT_SH": INSTALL_BOT, "FAKE_BIN": self.bin, "FAKE_TG_DIR": self.dir,
                    "GOAMNEZIA_BOT_DIR": self.botdir, "GOAMNEZIA_DIR": os.path.join(self.dir, "goamnezia"),
                    "TOKEN": FAKE_TOKEN})
        env.update(extra or {})
        return env

    def _run(self, script, extra=None, stdin=None):
        full = 'source "$INSTALL_BOT_SH"\nexport PATH="$FAKE_BIN:$PATH"\n' + script
        return subprocess.run([self.bash, "-c", full], env=self._env(extra), capture_output=True, text=True,
                              timeout=120, stdin=stdin if stdin is not None else subprocess.DEVNULL)

    def _read(self, name):
        path = os.path.join(self.dir, name)
        if not os.path.exists(path):
            return ""
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    def _env_file(self):
        with open(os.path.join(self.botdir, ".env"), encoding="utf-8") as fh:
            return dict(line.split("=", 1) for line in fh.read().splitlines() if "=" in line)

    # ── SEC-B1: токен — ни в одном argv ────────────────────────────────────
    def test_token_reaches_curl_only_through_stdin(self):
        self._getme()
        p = self._run('check_token "$TOKEN"; echo "rc=$?"')
        self.assertIn("my_test_bot", p.stdout)
        self.assertIn("rc=0", p.stdout)
        argv = self._read("argv")
        self.assertIn("-K -", argv)
        self.assertNotIn(FAKE_TOKEN, argv, "токен в argv виден любому локальному пользователю в /proc")
        self.assertNotIn("api.telegram.org", argv)
        self.assertIn('url = "https://api.telegram.org/bot%s/getMe"' % FAKE_TOKEN, self._read("stdin"))
        self.assertNotIn(FAKE_TOKEN, p.stdout + p.stderr, "токен не печатается")

    def test_token_check_outcomes(self):
        self._reply(1, {"ok": False, "error_code": 401, "description": "Unauthorized"}, 401)
        p = self._run('check_token "$TOKEN"; echo "rc=$?"')
        self.assertIn("rc=1", p.stdout)
        p = self._run('check_token "$TOKEN"; echo "rc=$?"')          # ответа нет — «сети нет»
        self.assertIn("rc=2", p.stdout)
        with open(os.path.join(self.dir, "replies", "3"), "w", encoding="utf-8") as fh:
            fh.write('{"ok": true, "result": {"id": 1, "username": "spaced_bot"}}\n200')   # пробелы в JSON
        p = self._run('check_token "$TOKEN"; echo "rc=$?"')
        self.assertIn("spaced_bot", p.stdout)
        # Не по образцу (кавычка поменяла бы разбор конфига curl) — curl даже не зовётся
        calls = self._read("count")
        p = self._run('check_token "123456789:ABC\\"DEFGHIJKLMNOPQRSTUVWXYZabcdefghi"; echo "rc=$?"')
        self.assertIn("rc=2", p.stdout)
        self.assertEqual(self._read("count"), calls)

    def test_no_curl_call_carries_the_api_address_in_argv(self):
        """По исходнику: адрес Bot API (а с ним и токен) — только в конфиге для `curl -K -`."""
        with open(INSTALL_BOT, encoding="utf-8") as fh:
            src = fh.read()
        for line in src.splitlines():
            code = line.split("#", 1)[0]
            if re.search(r"(^|[|;&(]|\$\()\s*curl\s", code):
                self.assertNotRegex(code, r"api\.telegram\.org|\$\{?token", line.strip())
                self.assertIn("-K -", code, line.strip())
        self.assertIn('printf \'url = "https://api.telegram.org/bot%s/%s"\\n\'', src)

    # ── одноразовый код — запасной путь установки без терминала ─────────────
    def test_code_file_is_the_one_the_bot_reads(self):
        with waiting_for_admin(code=None):
            p = self._run('c1=$(new_claim_code); c2=$(new_claim_code); echo "$c1 $c2 $(claim_code_pretty "$c1")"\n'
                          'write_claim_code "$c1" && echo written',
                          extra={"GOAMNEZIA_BOT_DIR": os.environ["GOAMNEZIA_BOT_DIR"]})
            c1, c2, pretty_a, pretty_b, written = p.stdout.split()
            self.assertRegex(c1, r"^[0-9]{8}$")
            self.assertNotEqual(c1, c2)
            self.assertEqual(pretty_a + pretty_b, c1)
            self.assertEqual(written, "written")
            self.assertEqual(stat.S_IMODE(os.stat(bot.ADMIN_CLAIM_CODE_FILE).st_mode), 0o600)
            self.assertEqual(bot.admin_claim_code(), c1, "код установщика — тот же, что ждёт бот")
            self.assertTrue(bot.claim_code_matches("/start %s %s" % (pretty_a, pretty_b)))

    def test_code_does_not_hang_when_sigpipe_is_ignored(self):
        """SIGPIPE, унаследованный игнорируемым (запуск из python, часть CI): прежний
        `tr </dev/urandom | head -c 8` на macOS читал /dev/urandom без конца — установщик
        висел на выборе администратора."""
        import signal
        old = signal.signal(signal.SIGPIPE, signal.SIG_IGN)
        try:
            p = subprocess.run([self.bash, "-c", 'source "$INSTALL_BOT_SH"\n'
                                'for i in 1 2 3; do new_claim_code; echo " rc=$?"; done'],
                               env=self._env(), capture_output=True, text=True, timeout=30,
                               stdin=subprocess.DEVNULL, restore_signals=False)
        finally:
            signal.signal(signal.SIGPIPE, old)
        codes = re.findall(r"^([0-9]+) rc=0$", p.stdout, re.M)
        self.assertEqual(len(codes), 3, p.stdout + p.stderr)
        for code in codes:
            self.assertRegex(code, r"^[0-9]{8}$")

    def _bot_file(self, name):
        return os.path.join(self.botdir, name)

    def _window_end(self):
        with open(self._bot_file(".admin_pairing_until"), encoding="utf-8") as fh:
            return int(fh.read().strip())

    def _code(self):
        with open(self._bot_file(".admin_claim_code"), encoding="utf-8") as fh:
            return fh.read().strip()

    # ── --auto и повторная установка ───────────────────────────────────────
    def test_auto_with_claim_writes_a_fresh_code_together_with_its_window(self):
        """HB-1 (сторона установщика): бот принимает код только в открытом окне привязки — установщик без
        терминала пишет окно вместе с кодом, до перезапуска бота. HB-10c: ни «10 минут после старта», ни
        «перезапустите бота» — окно по часам сервера, срок назван временем."""
        self._getme()
        before = int(time.time())
        p = self._run('AUTO=1; setup_env; echo "rc=$? pending=$CLAIM_PENDING until=$CLAIM_UNTIL"',
                      extra={"BOT_TOKEN": FAKE_TOKEN, "ALLOWED_IDS": "", "GOAMNEZIA_ADMIN_CLAIM": "1"})
        self.assertIn("rc=0 pending=1", p.stdout, p.stderr)
        env = self._env_file()
        self.assertEqual(env.get("ALLOWED_IDS"), "")
        self.assertEqual(env.get("OWNER_ID"), "", "владельца запишет бот — того, кто пришлёт код")
        self.assertEqual(stat.S_IMODE(os.stat(self._bot_file(".env")).st_mode), 0o600)
        code = self._code()
        self.assertRegex(code, r"^[0-9]{8}$")
        self.assertEqual(stat.S_IMODE(os.stat(self._bot_file(".admin_claim_code")).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self._bot_file(".admin_pairing_until")).st_mode), 0o600)
        until = self._window_end()
        self.assertTrue(before + 600 <= until <= int(time.time()) + 600, until)
        self.assertIn("until=%d" % until, p.stdout)
        self.assertIn("%s %s" % (code[:4], code[4:]), p.stderr, "код — на экране")
        self.assertIn("https://t.me/my_test_bot?start=%s" % code, p.stderr, "ссылка «Запустить» с кодом")
        self.assertIn(time.strftime("%H:%M", time.localtime(until)), p.stderr, "срок окна — временем")
        for stale in ("после запуска", "systemctl restart", "10 minutes after"):
            self.assertNotIn(stale, p.stderr, "HB-10c")
        self.assertNotIn(FAKE_TOKEN, self._read("argv") + p.stderr + p.stdout)

    def test_auto_without_admins_needs_an_explicit_way_and_the_old_marker_no_longer_blocks(self):
        p = self._run('AUTO=1; setup_env; echo "rc=$?"', extra={"BOT_TOKEN": FAKE_TOKEN, "ALLOWED_IDS": ""})
        self.assertIn("rc=1", p.stdout)
        self.assertFalse(os.path.exists(self._bot_file(".env")))
        # HB-8: маркер .admin_claimed (прежние версии, восстановление из бэкапа) путь без терминала не запирает
        os.makedirs(self.botdir)
        open(self._bot_file(".admin_claimed"), "w").close()
        self._getme()
        p = self._run('AUTO=1; setup_env; echo "rc=$? pending=$CLAIM_PENDING"',
                      extra={"BOT_TOKEN": FAKE_TOKEN, "ALLOWED_IDS": "", "GOAMNEZIA_ADMIN_CLAIM": "1"})
        self.assertIn("rc=0 pending=1", p.stdout, p.stderr)
        self.assertTrue(os.path.exists(self._bot_file(".env")))
        self.assertRegex(self._code(), r"^[0-9]{8}$")
        self.assertGreater(self._window_end(), time.time())

    def test_auto_pair_leaves_the_admin_to_ssh_and_issues_no_code(self):
        """SSH-меню ставит бота с GOAMNEZIA_ADMIN_PAIR=1 и сразу открывает окно само (--pair): коду, запасному
        пути без терминала, тут взяться неоткуда, окна до --pair — тоже."""
        os.makedirs(self.botdir)
        with open(self._bot_file(".admin_claim_code"), "w") as fh:
            fh.write("11112222\n")
        p = self._run('AUTO=1; setup_env; echo "rc=$? pending=$CLAIM_PENDING pair=$PAIR_AFTER_START"',
                      extra={"BOT_TOKEN": FAKE_TOKEN, "ALLOWED_IDS": "", "GOAMNEZIA_ADMIN_PAIR": "1"})
        self.assertIn("rc=0 pending=0 pair=0", p.stdout, p.stderr)
        self.assertEqual(self._env_file().get("ALLOWED_IDS"), "")
        for name in (".admin_claim_code", ".admin_pairing_until"):
            self.assertFalse(os.path.exists(self._bot_file(name)), name)
        self.assertEqual(self._read("argv"), "", "в Telegram ходить незачем")
        # повторная установка из меню на .env без админов — так же: окно откроет SSH
        p = self._run('AUTO=1; setup_env; echo "rc=$? pending=$CLAIM_PENDING pair=$PAIR_AFTER_START"',
                      extra={"GOAMNEZIA_ADMIN_PAIR": "1"})
        self.assertIn("rc=0 pending=0 pair=0", p.stdout, p.stderr)
        self.assertFalse(os.path.exists(self._bot_file(".admin_claim_code")))

    def test_auto_with_admins_drops_a_stale_code(self):
        os.makedirs(self.botdir)
        with open(self._bot_file(".admin_claim_code"), "w") as fh:
            fh.write("11112222\n")
        p = self._run('AUTO=1; setup_env; echo "rc=$? pending=$CLAIM_PENDING"',
                      extra={"BOT_TOKEN": FAKE_TOKEN, "ALLOWED_IDS": "555,777"})
        self.assertIn("rc=0 pending=0", p.stdout)
        env = self._env_file()
        self.assertEqual((env.get("ALLOWED_IDS"), env.get("OWNER_ID")), ("555,777", "555"))
        self.assertFalse(os.path.exists(self._bot_file(".admin_claim_code")))
        self.assertEqual(self._read("argv"), "", "с готовым списком в Telegram ходить незачем")

    def test_existing_env_without_admins_gets_a_fresh_code_and_window_each_run(self):
        """SEC46-07: код, увиденный однажды, в следующем окне не годится — на каждое окно новый код.
        Раньше повторный запуск показывал прежний код, а окно открывал бот после каждого старта."""
        os.makedirs(self.botdir)
        with open(self._bot_file(".env"), "w") as fh:
            fh.write('BOT_TOKEN="%s"\nALLOWED_IDS=\nOWNER_ID=\n' % FAKE_TOKEN)      # кавычки — правка руками
        with open(self._bot_file(".admin_claim_code"), "w") as fh:
            fh.write("48291375\n")
        self._getme(1)
        p = self._run('AUTO=1; setup_env; echo "rc=$? pending=$CLAIM_PENDING"')
        self.assertIn("rc=0 pending=1", p.stdout, p.stderr)
        first = self._code()
        self.assertNotEqual(first, "48291375", "код прошлого окна не переиспользуется")
        self.assertIn("https://t.me/my_test_bot?start=%s" % first, p.stderr)
        self.assertGreater(self._window_end(), time.time())
        p = self._run('AUTO=1; setup_env; echo "rc=$? pending=$CLAIM_PENDING"')
        self.assertIn("rc=0 pending=1", p.stdout)
        self.assertNotEqual(self._code(), first, "новый запуск — новое окно и новый код")
        # администраторы есть — код не нужен и удаляется, окна установщик не открывает
        os.unlink(self._bot_file(".admin_pairing_until"))
        with open(self._bot_file(".env"), "w") as fh:
            fh.write("BOT_TOKEN=%s\nALLOWED_IDS=555\nOWNER_ID=555\n" % FAKE_TOKEN)
        p = self._run('AUTO=1; setup_env; echo "rc=$? pending=$CLAIM_PENDING"')
        self.assertIn("rc=0 pending=0", p.stdout)
        self.assertFalse(os.path.exists(self._bot_file(".admin_claim_code")))
        self.assertFalse(os.path.exists(self._bot_file(".admin_pairing_until")))

    def test_interactive_needs_a_terminal(self):
        p = self._run('setup_env; echo "rc=$?"')
        self.assertIn("rc=1", p.stdout)
        self.assertFalse(os.path.exists(self._bot_file(".env")))

    # ── pty ────────────────────────────────────────────────────────────────
    def _pty(self, script, extra=None):
        env = self._env(dict({"TERM": "xterm"}, **(extra or {})))
        return _PtyRun(self.bash, 'source "$INSTALL_BOT_SH"\nexport PATH="$FAKE_BIN:$PATH"\n' + script, env)

    def test_interactive_run_offers_binding_by_message(self):
        """Интерактивная установка: токен → Enter («напишу боту и подтвержу здесь») → .env без админов,
        ни кода, ни очереди getUpdates: окно привязки откроется, как только бот запустится."""
        self._getme(1)
        run_ = self._pty('setup_env; echo "RESULT rc=$? pending=$CLAIM_PENDING pair=$PAIR_AFTER_START '
                         'user=$BOT_USERNAME"\n')
        try:
            run_.wait_for(r"BOT_TOKEN[^\n]*\n[^\n]*>")
            time.sleep(0.5)                                  # read -s уже снял эхо, как у человека
            run_.send(FAKE_TOKEN + "\n")
            run_.wait_for(r"@my_test_bot")
            run_.wait_for(r"Enter —[^\n]*\n[^\n]*>")
            self.assertIn("Напишу боту и подтвержу здесь, в терминале", run_.screen())
            run_.send("\n")
            run_.wait_for(r"RESULT rc=\d+ pending=\d+ pair=\d+ user=\S*")
        finally:
            run_.finish()
        self.assertIn("RESULT rc=0 pending=0 pair=1 user=my_test_bot", run_.screen())
        self.assertIn("откроется окно привязки", run_.screen())
        env = self._env_file()
        self.assertEqual((env.get("ALLOWED_IDS"), env.get("OWNER_ID")), ("", ""))
        self.assertFalse(os.path.exists(self._bot_file(".admin_claim_code")), "в терминале — без кода")
        self.assertNotIn("getUpdates", self._read("stdin"), "очередь бота установщик не читает")
        self.assertNotIn(FAKE_TOKEN, run_.out, "токен вводится без эха и не печатается")
        self.assertNotIn(FAKE_TOKEN, self._read("argv"))

    def test_existing_env_without_admins_in_a_terminal_binds_by_message(self):
        os.makedirs(self.botdir)
        with open(self._bot_file(".env"), "w") as fh:
            fh.write("BOT_TOKEN=%s\nALLOWED_IDS=\n" % FAKE_TOKEN)
        run_ = self._pty('setup_env; echo "RESULT rc=$? pending=$CLAIM_PENDING pair=$PAIR_AFTER_START"\n')
        try:
            run_.wait_for(r"RESULT rc=\d+ pending=\d+ pair=\d+")
        finally:
            run_.finish()
        self.assertIn("RESULT rc=0 pending=0 pair=1", run_.screen())
        self.assertFalse(os.path.exists(self._bot_file(".admin_claim_code")))

    # ── привязка сообщением: окно, кандидаты, «Это вы?», .env, ответ бота ───
    OWNER, STRANGER = 555000555, 111000111

    def _bot_installed(self, env_text=None):
        os.makedirs(self.botdir, exist_ok=True)
        with open(self._bot_file(".env"), "w") as fh:
            fh.write(env_text if env_text is not None else
                     "BOT_TOKEN=%s\nALLOWED_IDS=\nOWNER_ID=\nGOAMNEZIA_LANG=ru\n" % FAKE_TOKEN)
        os.chmod(self._bot_file(".env"), 0o600)

    def _candidates(self, *items):
        """Что пишет бот, пока окно открыто (bot.py pairing_record_candidate)."""
        with open(self._bot_file(".admin_candidates.json"), "w", encoding="utf-8") as fh:
            json.dump(list(items), fh, ensure_ascii=False)

    def _start_pairing(self, pre=""):
        self._bot_installed()
        self._getme(1)
        run_ = self._pty(pre + 'pair_by_message; echo "RESULT rc=$?"\n')
        run_.wait_for(r"Напишите боту @my_test_bot любое сообщение")
        run_.wait_for(r"Жду сообщение · окно открыто ещё \d+:\d\d")
        return run_

    def _pick_owner(self, run_, answer="1"):
        now = int(time.time())
        self._candidates({"id": self.STRANGER, "username": "", "first_name": "Stranger\u001b[2K\u009b1m", "ts": now},
                         {"id": self.OWNER, "username": "owner", "first_name": "Виталий", "ts": now})
        run_.wait_for(r"2\) @owner — Виталий, id %d, \d\d:\d\d" % self.OWNER)
        run_.wait_for(r"номер — это вы")
        time.sleep(0.3)
        run_.send("2\n")
        run_.wait_for(r"Написал @owner \(Виталий, id %d, %s\)\. Это вы\? 1 — да, это я · 0 — отмена: "
                      % (self.OWNER, time.strftime("%H:%M", time.localtime(now))))
        run_.send(answer + "\n")
        return now

    def test_pairing_binds_the_candidate_confirmed_in_the_terminal(self):
        os.makedirs(self.botdir)
        for stale in (".admin_confirmed", ".admin_pairing_done", ".admin_confirmed.rejected",
                      ".admin_candidates.json", ".admin_claim_code"):
            with open(self._bot_file(stale), "w") as fh:
                fh.write("stale\n")
        before = int(time.time())
        run_ = self._start_pairing()
        try:
            # окно: хвосты прошлого убраны, конец окна — через 10 минут по часам сервера, 0600; код не нужен
            until = self._window_end()
            self.assertTrue(before + 600 <= until <= int(time.time()) + 600, until)
            self.assertEqual(stat.S_IMODE(os.stat(self._bot_file(".admin_pairing_until")).st_mode), 0o600)
            for stale in (".admin_confirmed", ".admin_pairing_done", ".admin_confirmed.rejected",
                          ".admin_candidates.json", ".admin_claim_code"):
                self.assertFalse(os.path.exists(self._bot_file(stale)), stale)
            self._pick_owner(run_)
            run_.wait_for(r"Жду подтверждения от бота")
            env = self._env_file()
            self.assertEqual((env.get("ALLOWED_IDS"), env.get("OWNER_ID")), (str(self.OWNER), str(self.OWNER)))
            self.assertEqual(env.get("BOT_TOKEN"), FAKE_TOKEN, "токен в .env не тронут")
            self.assertEqual(stat.S_IMODE(os.stat(self._bot_file(".env")).st_mode), 0o600)
            with open(self._bot_file(".admin_confirmed"), encoding="utf-8") as fh:
                self.assertEqual(fh.read(), "%d\n" % self.OWNER)
            self.assertEqual(stat.S_IMODE(os.stat(self._bot_file(".admin_confirmed")).st_mode), 0o600)
            with open(self._bot_file(".admin_pairing_done"), "w") as fh:     # ответ бота «принято»
                fh.write("%d\n" % self.OWNER)
            run_.wait_for(r"RESULT rc=\d+")
        finally:
            run_.finish()
        screen = run_.screen()
        self.assertIn("RESULT rc=0", screen)
        self.assertIn("Администратор: @owner (id %d)" % self.OWNER, screen)
        self.assertIn("1) без @имени — Stranger", screen)
        self.assertNotIn("\x1b[2K", run_.out.split("Боту написали")[1], "имя из Telegram — чужой текст в терминале root")
        self.assertNotIn("\u009b", run_.out)
        for gone in (".admin_pairing_until", ".admin_candidates.json", ".admin_pairing_done"):
            self.assertFalse(os.path.exists(self._bot_file(gone)), gone)

    def test_pairing_keeps_the_admins_and_the_owner_already_in_env(self):
        """Второй администратор: прежние остаются (бот берёт список из файла как есть), владелец — прежний;
        .env читается как у бота — «export», кавычки, комментарий, повтор строки."""
        self._bot_installed()
        with open(self._bot_file(".env"), "w") as fh:
            fh.write('BOT_TOKEN=%s\nexport ALLOWED_IDS="111, 222" # руками\nOWNER_ID=111\nALLOWED_IDS=111,222\n'
                     'GOAMNEZIA_LANG=ru\n' % FAKE_TOKEN)
        p = self._run('ans=$(python3 -c "$PY_ENV_ADD_ADMIN" "$ENV_FILE" 333) && echo "ok $ans"')
        self.assertIn("ok 111,222,333", p.stdout, p.stderr)
        with open(self._bot_file(".env"), encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        self.assertEqual(lines, ["BOT_TOKEN=%s" % FAKE_TOKEN, "ALLOWED_IDS=111,222,333", "OWNER_ID=111",
                                 "GOAMNEZIA_LANG=ru"])
        self.assertEqual(stat.S_IMODE(os.stat(self._bot_file(".env")).st_mode), 0o600)
        # тот же человек ещё раз — без повтора в списке
        p = self._run('python3 -c "$PY_ENV_ADD_ADMIN" "$ENV_FILE" 222')
        self.assertEqual(p.stdout.strip(), "111,222,333")

    def test_pairing_a_second_admin_does_not_make_them_the_owner(self):
        """SEC50-02: OWNER_ID пишется, только если подтверждённый — первый администратор (список был пуст).
        Раньше при пустом OWNER_ID владельцем становился новый человек, даже если админы уже были."""
        cases = (
            # (строки .env, id, ALLOWED_IDS после, OWNER_ID после)
            ("ALLOWED_IDS=111,222\nOWNER_ID=\n", 333, "111,222,333", ""),
            ("ALLOWED_IDS=111,222\n", 333, "111,222,333", None),
            ("ALLOWED_IDS=\nOWNER_ID=\n", 333, "333", "333"),
            ("ALLOWED_IDS=\nOWNER_ID=999\n", 333, "333", "333"),          # владелец не из списка — не владелец
            ("ALLOWED_IDS=garbage\n", 333, "333", "333"),
            ("ALLOWED_IDS=111\nOWNER_ID=111\n", 333, "111,333", "111"),
        )
        for env_text, uid, want_ids, want_owner in cases:
            self._bot_installed("BOT_TOKEN=%s\n%sGOAMNEZIA_LANG=ru\n" % (FAKE_TOKEN, env_text))
            p = self._run('python3 -c "$PY_ENV_ADD_ADMIN" "$ENV_FILE" %d' % uid)
            self.assertEqual(p.stdout.strip(), want_ids, (env_text, p.stderr))
            env = self._env_file()
            self.assertEqual(env.get("OWNER_ID"), want_owner, env_text)
            self.assertEqual(env.get("BOT_TOKEN"), FAKE_TOKEN)
            p = self._run('owner_missing && echo missing || echo set')
            self.assertEqual(p.stdout.strip(), "set" if want_owner else "missing", env_text)
        # write_env: владелец снаружи, но не из списка, — первый из списка; списка нет — владельца нет
        for owner, ids, want in (("999", "111,222", "111"), ("222", "111,222", "222"), ("", "111", "111"),
                                 ("999", "", "")):
            p = self._run('write_env "$TOKEN" %s && echo ok' % (ids or '""'), extra={"OWNER_ID": owner})
            self.assertIn("ok", p.stdout, p.stderr)
            self.assertEqual(self._env_file().get("OWNER_ID"), want, (owner, ids))

    def test_pairing_a_second_admin_without_an_owner_says_so(self):
        """SEC50-02: админы есть, владельца нет — привязка второго его не назначает и говорит, где назначить."""
        run_ = self._start_pairing()
        try:
            with open(self._bot_file(".env"), "w") as fh:
                fh.write("BOT_TOKEN=%s\nALLOWED_IDS=424242\nOWNER_ID=\nGOAMNEZIA_LANG=ru\n" % FAKE_TOKEN)
            self._pick_owner(run_)
            run_.wait_for(r"Жду подтверждения от бота")
            with open(self._bot_file(".admin_pairing_done"), "w") as fh:
                fh.write("%d\n" % self.OWNER)
            run_.wait_for(r"RESULT rc=\d+")
        finally:
            run_.finish()
        screen = run_.screen()
        self.assertIn("RESULT rc=0", screen)
        self.assertIn("Владелец не назначен", screen)
        self.assertIn("Telegram-бот → Администраторы → Назначить владельца", screen)
        env = self._env_file()
        self.assertEqual((env.get("ALLOWED_IDS"), env.get("OWNER_ID")), ("424242,%d" % self.OWNER, ""))

    def test_ssh_menu_offers_the_owner_after_binding_a_second_admin(self):
        """SEC50-02: SSH-меню после привязки (install_bot.sh --pair отдельным процессом) видит, что владельца
        нет, и сразу ведёт к его выбору (_bot_owner_set); владелец есть — не спрашивает."""
        fake_inst = os.path.join(self.dir, "src", "goamnezia-bot", "install_bot.sh")
        os.makedirs(os.path.dirname(fake_inst))
        with open(fake_inst, "w", encoding="utf-8") as fh:
            fh.write('printf "BOT_TOKEN=x\\nALLOWED_IDS=%s\\nOWNER_ID=%s\\n" "$FAKE_IDS" "$FAKE_OWNER" '
                     '> "$GOAMNEZIA_BOT_DIR/.env"\nexit "${FAKE_RC:-0}"\n')
        funcs = _install_sh_functions("_bot_env_get", "_bot_owner_id", "_bot_admin_ids", "_bot_has_admins",
                                      "_bot_owner_missing", "_bot_pair_by_message")
        script = ('t() { printf "%%s" "$1"; }\ntf() { printf "%%s" "$1"; }\nlog_warning() { :; }\nlog_error() { :; }\n'
                  '_bot_owner_set() { echo OWNER_PICK; }\nSCRIPT_DIR=%s\nBOT_DIR=%s\nGOAMNEZIA_DIR=/nonexistent\n%s\n'
                  'mkdir -p "$BOT_DIR" && touch "$BOT_DIR/.env"\n_bot_pair_by_message; echo "rc=$?"\n'
                  % (shlex.quote(os.path.join(self.dir, "src")), shlex.quote(self.botdir), funcs))
        for ids, owner, rc, asked in (("111,222", "", "0", True), ("111,222", "999", "0", True),
                                      ("111,222", "111", "0", False), ("", "", "0", False), ("111,222", "", "1", False)):
            p = subprocess.run([self.bash, "-c", script], env=self._env({"FAKE_IDS": ids, "FAKE_OWNER": owner,
                                                                         "FAKE_RC": rc}),
                               capture_output=True, text=True, timeout=60)
            self.assertEqual("OWNER_PICK" in p.stdout, asked, (ids, owner, rc, p.stdout, p.stderr))
            self.assertIn("rc=%s" % rc, p.stdout)

    def test_pairing_rejected_by_the_bot_says_why(self):
        run_ = self._start_pairing()
        try:
            self._pick_owner(run_)
            run_.wait_for(r"Жду подтверждения от бота")
            with open(self._bot_file(".admin_confirmed.rejected"), "w") as fh:
                fh.write("%d\nnot_in_env\n" % self.OWNER)
            run_.wait_for(r"RESULT rc=\d+")
        finally:
            run_.finish()
        screen = run_.screen()
        self.assertIn("RESULT rc=1", screen)
        self.assertIn("Бот не принял привязку: id так и не появился в ALLOWED_IDS", screen)
        self.assertFalse(os.path.exists(self._bot_file(".admin_pairing_until")), "окно закрыто")

    def test_pairing_not_me_then_zero_cancels_and_closes_the_window(self):
        """TAIL-15: ответ на «Это вы?» — цифрой, как везде в SSH-меню. Не «1» (прежнее y, Enter) — не
        подтверждено: назад к списку, окно открыто."""
        run_ = self._start_pairing()
        try:
            self._pick_owner(run_, answer="y")
            run_.wait_for(r"Не подтверждено — жду дальше")
            self.assertTrue(os.path.exists(self._bot_file(".admin_pairing_until")), "окно ещё открыто")
            self.assertEqual(self._env_file().get("ALLOWED_IDS"), "", "буква — не подтверждение")
            run_.wait_for(r"номер — это вы")
            time.sleep(0.3)
            run_.send("0\n")
            run_.wait_for(r"RESULT rc=\d+")
        finally:
            run_.finish()
        screen = run_.screen()
        self.assertIn("RESULT rc=1", screen)
        self.assertIn("Привязка отменена, окно закрыто.", screen)
        self.assertIn("Telegram-бот → Администраторы → Привязать по сообщению", screen)
        self.assertEqual(self._env_file().get("ALLOWED_IDS"), "", "никого не подтвердили — .env не тронут")
        for gone in (".admin_pairing_until", ".admin_candidates.json", ".admin_confirmed"):
            self.assertFalse(os.path.exists(self._bot_file(gone)), gone)

    def test_pairing_zero_at_the_question_cancels_and_closes_the_window(self):
        """TAIL-15: «0 — отмена» в вопросе «Это вы?» — отмена привязки с закрытием окна, как «0» в списке."""
        run_ = self._start_pairing()
        try:
            self._pick_owner(run_, answer="0")
            run_.wait_for(r"RESULT rc=\d+")
        finally:
            run_.finish()
        screen = run_.screen()
        self.assertIn("RESULT rc=1", screen)
        self.assertIn("Привязка отменена, окно закрыто.", screen)
        self.assertEqual(self._env_file().get("ALLOWED_IDS"), "", "никого не подтвердили — .env не тронут")
        for gone in (".admin_pairing_until", ".admin_confirmed"):
            self.assertFalse(os.path.exists(self._bot_file(gone)), gone)

    def test_pairing_question_is_asked_with_digits_in_both_languages(self):
        """TAIL-15: в словаре установщика вопрос «Это вы?» — цифрами, без [y/N]."""
        with open(INSTALL_BOT, encoding="utf-8") as fh:
            src = fh.read()
        ru = re.search(r'^I18N_RU\[pair_ask\]="(.*)"$', src, re.M).group(1)
        en = re.search(r'^I18N_EN\[pair_ask\]="(.*)"$', src, re.M).group(1)
        self.assertTrue(ru.endswith("Это вы? 1 — да, это я · 0 — отмена: "), ru)
        self.assertTrue(en.endswith("Is this you? 1 — yes, it's me · 0 — cancel: "), en)
        self.assertNotIn("y/N", ru + en)

    def test_candidates_are_read_in_first_message_order_and_cleaned(self):
        """SEC50-01: SSH разбирает кандидатов JSON-парсером по времени первого сообщения — первые десять,
        номер не зависит от порядка записей в файле; управляющее и невидимое (вся категория Cf) из имён —
        прочь; одинаковые с точностью до регистра, невидимых и похожих букв имена помечены «двойниками»."""
        os.makedirs(self.botdir, exist_ok=True)
        t0 = 1790000000
        items = [{"id": 900 + i, "username": "u%d" % i, "first_name": "N%d" % i, "ts": t0 + 100 - i} for i in range(12)]
        items += [{"id": 905, "username": "again", "first_name": "Again", "ts": t0 + 500},    # тот же id — мимо
                  {"id": True, "first_name": "bool"}, {"id": "7"}, "junk", None]
        self._candidates(*items)
        p = self._run('python3 -c "$PY_CANDIDATES" "$CANDIDATES_FILE" "$PAIRING_MAX_CANDIDATES"')
        rows = [line.split("\t") for line in p.stdout.splitlines()]
        self.assertEqual([int(r[0]) for r in rows], list(range(911, 901, -1)), p.stderr)
        self.assertTrue(all(len(r) == 5 and r[4] == "0" for r in rows), rows)
        self.assertEqual(rows[0][3], time.strftime("%H:%M", time.localtime(t0 + 89)))
        # имена: невидимое и управляющее — прочь, двойники — по сути имени, а не по байтам
        self._candidates(
            {"id": 1, "username": "owner", "first_name": "Виталий", "ts": t0},
            {"id": 2, "username": "evil", "first_name": "Bиталий\u200b\u2060\ufeff\u061c",
             "ts": t0 + 1},                                              # латинская «B» и невидимый хвост
            {"id": 3, "username": "bad name", "first_name": "\u202eEvil\x1b[2J\u009b\\x", "ts": t0 + 2},
            {"id": 4, "username": "", "first_name": "\u200b\u3164\u2066", "ts": t0 + 3},
            {"id": 5, "username": "late", "first_name": "  ВИТАЛИЙ ", "ts": "потом"},
            {"id": 6, "username": "other", "first_name": "Vitaly", "ts": t0 + 4})
        p = self._run('python3 -c "$PY_CANDIDATES" "$CANDIDATES_FILE" "$PAIRING_MAX_CANDIDATES"')
        rows = [line.split("\t") for line in p.stdout.splitlines()]
        self.assertEqual([(r[0], r[1], r[2], r[4]) for r in rows], [
            ("1", "owner", "Виталий", "1"), ("2", "evil", "Bиталий", "1"), ("3", "-", "Evil 2J x", "0"),
            ("4", "-", "-", "0"), ("6", "other", "Vitaly", "0"), ("5", "late", "ВИТАЛИЙ", "1")])
        self.assertEqual(rows[-1][3], "--:--")
        for ch in p.stdout:
            self.assertFalse(unicodedata.category(ch) in ("Cc", "Cf") and ch not in "\t\n", hex(ord(ch)))
        # файл испорчен или не список — пусто
        for garbage in ("{not json", '{"id": 1}', "", "[1, 2]"):
            with open(self._bot_file(".admin_candidates.json"), "w", encoding="utf-8") as fh:
                fh.write(garbage)
            p = self._run('python3 -c "$PY_CANDIDATES" "$CANDIDATES_FILE" "$PAIRING_MAX_CANDIDATES"')
            self.assertEqual(p.stdout, "", garbage)

    def test_pairing_warns_about_several_writers_twins_and_a_full_list(self):
        """SEC50-01: кто угодно может написать боту и назваться как угодно — SSH предупреждает, когда
        написавших несколько, когда имена совпадают и когда список заполнен (новых бот не записывает)."""
        run_ = self._start_pairing()
        now = int(time.time())
        owner = {"id": self.OWNER, "username": "owner", "first_name": "Виталий", "ts": now}
        twin = {"id": self.STRANGER, "username": "owner_", "first_name": "Bиталий\u200b",
                "ts": now + 1}
        try:
            self._candidates(owner)
            run_.wait_for(r"1\) @owner — Виталий, id %d" % self.OWNER)
            run_.wait_for(r"номер — это вы")
            self.assertNotIn("⚠", run_.screen().split("Боту написали")[-1], "один написавший — без предупреждений")
            self._candidates(owner, twin)
            run_.wait_for(r"2\) @owner_ — Bиталий, id %d" % self.STRANGER)
            run_.wait_for(r"Писали несколько человек\. Выбирайте только по @username и времени; если писали только "
                          r"вы — отмените \(0\) и повторите позже: кто-то пытается перехватить привязку\.")
            run_.wait_for(r"Одинаковые имена у №1, 2 — имя подделать легко")
            self.assertNotIn("Список заполнен", run_.screen())
            self._candidates(owner, twin, *[{"id": 700 + i, "username": "u%d" % i, "first_name": "N%d" % i, "ts": now + 2 + i}
                                           for i in range(8)])
            run_.wait_for(r"10\) @u7 — N7")
            run_.wait_for(r"Список заполнен: записаны первые 10 написавших, новых бот не добавит")
            run_.wait_for(r"номер — это вы")
            time.sleep(0.3)
            run_.send("0\n")
            run_.wait_for(r"RESULT rc=\d+")
        finally:
            run_.finish()
        self.assertIn("RESULT rc=1", run_.screen())
        self.assertFalse(os.path.exists(self._bot_file(".admin_pairing_until")), "отмена закрывает окно")

    def test_pairing_ctrl_c_cancels_and_closes_the_window(self):
        run_ = self._start_pairing()
        try:
            self.assertTrue(os.path.exists(self._bot_file(".admin_pairing_until")))
            run_.send("\x03")                                   # Ctrl-C в терминале
            run_.wait_for(r"Привязка отменена")
        finally:
            status = run_.finish()
        self.assertEqual(os.WEXITSTATUS(status) if os.WIFEXITED(status) else status, 130)
        self.assertNotIn("RESULT", run_.screen(), "Ctrl-C — выход, а не продолжение")
        self.assertFalse(os.path.exists(self._bot_file(".admin_pairing_until")))

    def test_pairing_end_of_input_cancels_instead_of_spinning(self):
        self._bot_installed()
        p = self._run('pair_by_message my_test_bot; echo "RESULT rc=$?"', stdin=subprocess.DEVNULL)
        self.assertIn("RESULT rc=1", p.stdout, p.stderr)
        self.assertIn("Привязка отменена", p.stderr)
        self.assertEqual(p.stderr.count("Жду сообщение"), 1, "без терминала — строка состояния одна, не каждую секунду")
        self.assertFalse(os.path.exists(self._bot_file(".admin_pairing_until")))

    def test_pairing_window_expires(self):
        run_ = self._start_pairing(pre="PAIRING_WINDOW=3\n")
        try:
            run_.wait_for(r"RESULT rc=\d+", timeout=20)
        finally:
            run_.finish()
        self.assertIn("Окно привязки закрылось: за 10 минут никто не написал боту.", run_.screen())
        self.assertIn("RESULT rc=1", run_.screen())
        self.assertFalse(os.path.exists(self._bot_file(".admin_pairing_until")))

    def test_pairing_with_a_silent_bot_keeps_the_confirmation_for_its_start(self):
        """Бот молчит (не запущен): администратор уже в .env, подтверждение остаётся — бот подхватит его при
        старте; пропажа .admin_confirmed сама по себе ответом не считается."""
        run_ = self._start_pairing(pre="PAIRING_REPLY_WAIT=2\n")
        try:
            self._pick_owner(run_)
            run_.wait_for(r"RESULT rc=\d+", timeout=20)
        finally:
            run_.finish()
        self.assertIn("Бот не ответил за минуту", run_.screen())
        self.assertIn("RESULT rc=0", run_.screen())
        self.assertEqual(self._env_file().get("ALLOWED_IDS"), str(self.OWNER))
        self.assertTrue(os.path.exists(self._bot_file(".admin_confirmed")))
        self.assertFalse(os.path.exists(self._bot_file(".admin_pairing_until")))

    def test_pair_mode_needs_an_installed_bot(self):
        os.makedirs(self.botdir)
        p = subprocess.run([self.bash, INSTALL_BOT, "--pair", "--lang=en"], env=self._env(), capture_output=True,
                           text=True, timeout=60, stdin=subprocess.DEVNULL)
        self.assertEqual(p.returncode, 1, p.stderr)
        self.assertIn("The bot is not installed", p.stderr)
        self.assertEqual(os.listdir(self.botdir), [], "окна без бота не открываем")

    def test_installer_dictionary_is_complete(self):
        """Свой словарь установщика: оба языка с одними ключами и тем же числом подстановок, каждый ключ из
        t/tf на месте (check_i18n_ssh.py смотрит только словари SSH-меню)."""
        with open(INSTALL_BOT, encoding="utf-8") as fh:
            src = fh.read()
        ru = dict(re.findall(r'^I18N_RU\[([a-z0-9_]+)\]="(.*)"$', src, re.M))
        en = dict(re.findall(r'^I18N_EN\[([a-z0-9_]+)\]="(.*)"$', src, re.M))
        self.assertEqual(sorted(ru), sorted(en))
        for key in ru:
            self.assertEqual(len(re.findall(r"%[sd]", ru[key])), len(re.findall(r"%[sd]", en[key])), key)
        code = "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("#"))
        used = set(re.findall(r"\b(?:t|tf) ([a-z][a-z0-9_]+)", code))
        self.assertFalse(used - set(ru), used - set(ru))

    # ── кросс-проверка с ботом: файлы установщика и SSH бот понимает ──────────
    def test_installer_code_and_window_open_the_claim_path_in_the_bot(self):
        """Файлы, которые пишет установщик без терминала, делают bot.admin_claim_open() истинным (HB-1)."""
        botdir = os.environ["GOAMNEZIA_BOT_DIR"]
        env_existed = os.path.exists(bot.ENV_FILE)
        with waiting_for_admin(code=None, window=False):
            try:
                self.assertFalse(bot.admin_claim_open())
                self._getme()
                p = self._run('AUTO=1; setup_env; echo "rc=$? pending=$CLAIM_PENDING"',
                              extra={"BOT_TOKEN": FAKE_TOKEN, "ALLOWED_IDS": "", "GOAMNEZIA_ADMIN_CLAIM": "1",
                                     "GOAMNEZIA_BOT_DIR": botdir})
                self.assertIn("rc=0 pending=1", p.stdout, p.stderr)
                self.assertTrue(bot.admin_claim_open(), "код и окно установщика открывают путь по коду")
                with open(bot.ADMIN_CLAIM_CODE_FILE, encoding="utf-8") as fh:
                    code = fh.read().strip()
                self.assertTrue(bot.claim_code_matches(code))
                self.assertGreater(bot.pairing_seconds_left(), 590)
            finally:
                for path in (bot.ADMIN_CLAIM_CODE_FILE, bot.ADMIN_PAIRING_UNTIL_FILE):
                    if os.path.exists(path):
                        os.unlink(path)
                if not env_existed and os.path.exists(bot.ENV_FILE):
                    os.unlink(bot.ENV_FILE)

    def test_ssh_confirmation_files_are_accepted_by_the_bot(self):
        """Что пишет SSH, подтвердив человека (.env, потом .admin_confirmed), настоящий бот принимает:
        перечитывает админов без перезапуска и отвечает .admin_pairing_done с тем же id."""
        botdir = os.environ["GOAMNEZIA_BOT_DIR"]
        uid = 616262
        env_existed = os.path.exists(bot.ENV_FILE)
        with waiting_for_admin(code=None, window=True):
            try:
                with open(bot.ENV_FILE, "w", encoding="utf-8") as fh:
                    fh.write("BOT_TOKEN=%s\nALLOWED_IDS=\nOWNER_ID=\n" % FAKE_TOKEN)
                p = self._run('ans=$(python3 -c "$PY_ENV_ADD_ADMIN" "$ENV_FILE" %d) && write_private '
                              '"$CONFIRMED_FILE" %d && echo "ok $ans"' % (uid, uid), extra={"GOAMNEZIA_BOT_DIR": botdir})
                self.assertIn("ok %d" % uid, p.stdout, p.stderr)
                tg = _PairingTgBot()
                self.assertEqual(run(bot.pairing_tick(tg)), uid)
                self.assertTrue(bot.is_user_allowed(uid))
                self.assertTrue(bot.is_owner(uid))
                with open(bot.ADMIN_PAIRING_DONE_FILE, encoding="utf-8") as fh:
                    self.assertEqual(fh.read(), "%d\n" % uid)
            finally:
                for path in (bot.ADMIN_PAIRING_DONE_FILE, bot.ADMIN_CONFIRMED_FILE):
                    if os.path.exists(path):
                        os.unlink(path)
                if not env_existed and os.path.exists(bot.ENV_FILE):
                    os.unlink(bot.ENV_FILE)


class EnvParseParityTests(unittest.TestCase):
    """SEC50-03: .env бота читается везде одинаково — бот (_read_env_values), SSH-меню (install.sh
    _bot_env_get), установщик бота (install_bot.sh env_value) и панель (admin-web/server.py _env_value):
    «export», пробелы вокруг имени, кавычки, комментарий; повтор переменной — побеждает последняя строка,
    как у systemd. Раньше SSH и панель брали первую."""

    CASES = (
        "ALLOWED_IDS=111\nALLOWED_IDS=222\n",
        'ALLOWED_IDS=111\nexport ALLOWED_IDS="222,333" # c\n',
        "  ALLOWED_IDS = 444 # x\nALLOWED_IDS=555\n  export  ALLOWED_IDS='666'\n",
        "ALLOWED_IDS=777\n# ALLOWED_IDS=888\n",
        'ALLOWED_IDS="999\nALLOWED_IDS="1,2" tail\n',
        "ALLOWED_IDS=5\nALLOWED_IDS='3,4 # c\n",
        "ALLOWED_IDSX=1\nXALLOWED_IDS=2\nALLOWED_IDS=5 6\n",
        "ALLOWED_IDS=6\nALLOWED_IDS=\n",
        "ALLOWED_IDS=1#2 #3\n",
        "OWNER_ID=1\n",
    )

    @classmethod
    def setUpClass(cls):
        cls.bash = _bash5()
        if cls.bash is None:
            raise unittest.SkipTest("нужен bash 4+ (задайте BASH5)")

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="env-parity-", dir=TMP_ROOT)

    def _bot_values(self, text):
        path = os.path.join(self.dir, ".env")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        with patch.object(bot, "ENV_FILE", path):
            return bot._read_env_values(("ALLOWED_IDS",)) or {}

    def _bot_value(self, text):
        return self._bot_values(text).get("ALLOWED_IDS", "")

    def test_ssh_menu_and_installer_read_like_the_bot(self):
        funcs = _install_sh_functions("_bot_env_get", "_bot_admin_ids")
        for text in self.CASES:
            values = self._bot_values(text)                 # заодно пишет .env для bash
            want = values.get("ALLOWED_IDS", "")
            p = subprocess.run([self.bash, "-c", 'BOT_DIR="$1"\n%s\n_bot_env_get ALLOWED_IDS; echo "|$?"' % funcs,
                                "x", self.dir], capture_output=True, text=True, timeout=60)
            self.assertEqual(p.stdout, "%s|%d\n" % (want, 0 if "ALLOWED_IDS" in values else 1), (text, p.stderr))
            p = subprocess.run([self.bash, "-c", 'source "$INSTALL_BOT_SH"\nenv_value ALLOWED_IDS'],
                               env=dict(os.environ, INSTALL_BOT_SH=INSTALL_BOT, GOAMNEZIA_BOT_DIR=self.dir),
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(p.stdout, want, (text, p.stderr))

    def test_the_bot_itself_takes_the_last_line(self):
        self.assertEqual(self._bot_value(self.CASES[0]), "222")
        self.assertEqual(self._bot_value(self.CASES[2]), "666")
        self.assertEqual(self._bot_value(self.CASES[7]), "")

    def test_the_env_screen_hides_every_token_line(self):
        """SEC50-03: «export BOT_TOKEN=…» и отступ перед именем — тоже строка токена: экран .env её прячет."""
        funcs = _install_sh_functions("_bot_env_screen")
        with open(os.path.join(self.dir, ".env"), "w", encoding="utf-8") as fh:
            fh.write("BOT_TOKEN=%s\nexport BOT_TOKEN=%s\n  BOT_TOKEN = %s\nALLOWED_IDS=1\nBOT_TOKENS=keep\n"
                     % (FAKE_TOKEN, FAKE_TOKEN, FAKE_TOKEN))
        script = ('t() { printf "%%s" "$1"; }\ntf() { printf "%%s" "$1"; }\n_rule() { :; }\nlog_error() { :; }\n'
                  'BOT_DIR="$1"\n%s\n_bot_env_screen' % funcs)
        p = subprocess.run([self.bash, "-c", script, "x", self.dir], capture_output=True, text=True, timeout=60)
        self.assertNotIn(FAKE_TOKEN, p.stdout + p.stderr)
        self.assertIn("ALLOWED_IDS=1", p.stdout)
        self.assertIn("BOT_TOKENS=keep", p.stdout)


# flock(1) для стенда без util-linux (macOS): берёт flock(2) на унаследованном дескрипторе и выходит — замок
# остаётся за дескриптором оболочки, ровно как у настоящего flock FD.
FAKE_FLOCK = '''#!/usr/bin/env python3
import fcntl, sys, time
a = sys.argv[1:]
wait = None
if a and a[0] == "-w":
    wait = float(a[1])
    a = a[2:]
fd = int(a[0])
deadline = time.monotonic() + (wait if wait is not None else 1e9)
while True:
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        sys.exit(0)
    except BlockingIOError:
        if time.monotonic() >= deadline:
            sys.exit(1)
        time.sleep(0.02)
'''


class EnvLockTests(unittest.TestCase):
    """SEC50-04: у .env бота несколько писателей — бот, SSH-меню, установщик бота; каждый читает файл, меняет
    строку и кладёт файл обратно. Без общего замка запись одного пропадала под записью другого. Теперь все
    берут flock на .env.lock рядом с .env на всё «прочитать — изменить — записать»."""

    @classmethod
    def setUpClass(cls):
        cls.bash = _bash5()
        if cls.bash is None:
            raise unittest.SkipTest("нужен bash 4+ (задайте BASH5)")

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="env-lock-", dir=TMP_ROOT)
        self.bin = os.path.join(self.dir, "bin")
        os.makedirs(self.bin)
        flock = os.path.join(self.bin, "flock")
        with open(flock, "w", encoding="utf-8") as fh:
            fh.write(FAKE_FLOCK)
        os.chmod(flock, 0o755)
        self.env_path = os.path.join(self.dir, ".env")
        with open(self.env_path, "w", encoding="utf-8") as fh:
            fh.write("BOT_TOKEN=%s\nALLOWED_IDS=111\nOWNER_ID=111\n" % FAKE_TOKEN)

    def _read_env(self):
        with open(self.env_path, encoding="utf-8") as fh:
            return fh.read()

    def _hold(self, release_after, change=None):
        """Чужой писатель: взять замок и прочитать .env сразу, а через release_after секунд записать прочитанное
        вместе со своей строкой change (tmp + rename) и отпустить замок. Писатель без замка, успевший записать в
        это время, свою запись потерял бы — ровно так и терялись записи до SEC50-04."""
        import fcntl
        fd = os.open(self.env_path + ".lock", os.O_WRONLY | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        snapshot = self._read_env()

        def later():
            time.sleep(release_after)
            if change:
                with open(self.env_path + ".holder", "w", encoding="utf-8") as fh:
                    fh.write(snapshot + change)
                os.chmod(self.env_path + ".holder", 0o600)
                os.replace(self.env_path + ".holder", self.env_path)
            os.close(fd)
        th = threading.Thread(target=later)
        th.start()
        self.addCleanup(th.join)            # упавшая проверка не оставляет писателя дописывать после теста
        return th

    def test_the_bot_waits_for_the_lock_and_keeps_the_other_write(self):
        th = self._hold(0.5, "GOAMNEZIA_LANG=en\n")
        with patch.object(bot, "ENV_FILE", self.env_path), patch.dict(os.environ), \
                patch.object(bot, "_WAITING_FOR_ADMIN", bot._WAITING_FOR_ADMIN), \
                patch.object(bot, "ALLOWED_IDS", {111, 222}), patch.object(bot, "ADMIN_ORDER", [111, 222]):
            started = time.monotonic()
            self.assertTrue(bot._save_allowed_ids())
            self.assertGreaterEqual(time.monotonic() - started, 0.4, "запись ждала замка")
            th.join()
            text = self._read_env()
            self.assertIn("ALLOWED_IDS=111,222", text)
            self.assertIn("GOAMNEZIA_LANG=en", text, "чужая запись под замком не потерялась")
            # занят дольше ожидания — записи нет вовсе, вызывающий узнаёт «не сохранено»
            th = self._hold(1.0)
            with patch.object(bot, "ENV_LOCK_WAIT", 0.2):
                self.assertFalse(bot._save_env_value("OWNER_ID", "222"))
            th.join()
            self.assertIn("OWNER_ID=111", self._read_env())
            self.assertTrue(bot._save_env_value("OWNER_ID", "222"))
            self.assertIn("OWNER_ID=222", self._read_env())
        self.assertEqual(stat.S_IMODE(os.stat(self.env_path + ".lock").st_mode), 0o600)

    def _bash_run(self, script, extra=None):
        """bash -c: install_bot.sh при source ставит свой PATH — flock стенда возвращаем после него."""
        env = dict(os.environ, INSTALL_BOT_SH=INSTALL_BOT, GOAMNEZIA_BOT_DIR=self.dir, TOKEN=FAKE_TOKEN,
                   FAKE_BIN=self.bin, PATH=self.bin + os.pathsep + os.environ.get("PATH", ""))
        env.update(extra or {})
        script = script.replace('source "$INSTALL_BOT_SH"\n', 'source "$INSTALL_BOT_SH"\nexport PATH="$FAKE_BIN:$PATH"\n')
        started = time.monotonic()
        p = subprocess.run([self.bash, "-c", script], env=env, capture_output=True, text=True, timeout=120)
        p.took = time.monotonic() - started
        return p

    def test_the_installer_takes_the_same_lock(self):
        # привязка сообщением (python, fcntl.flock) ждёт замка и не теряет чужую запись
        th = self._hold(0.5, "GOAMNEZIA_LANG=en\n")
        p = self._bash_run('source "$INSTALL_BOT_SH"\npython3 -c "$PY_ENV_ADD_ADMIN" "$ENV_FILE" 333')
        th.join()
        self.assertEqual(p.stdout.strip(), "111,333", p.stderr)
        self.assertIn("ALLOWED_IDS=111,333", self._read_env(), "запись привязки не потерялась")
        self.assertIn("GOAMNEZIA_LANG=en", self._read_env())
        # занят дольше ожидания — отказ, .env не тронут
        th = self._hold(1.5)
        p = self._bash_run('source "$INSTALL_BOT_SH"\npython3 -c "$PY_ENV_ADD_ADMIN" "$ENV_FILE" 444; echo "rc=$?"',
                           extra={"ENV_LOCK_WAIT": "0.3"})
        th.join()
        self.assertIn("rc=1", p.stdout)
        self.assertNotIn("444", self._read_env())
        # создание .env (write_env, flock(1)) — тот же замок
        th = self._hold(1.5)
        p = self._bash_run('source "$INSTALL_BOT_SH"\nENV_LOCK_WAIT=0.3\nwrite_env "$TOKEN" 555; echo "rc=$?"')
        th.join()
        self.assertIn("rc=75", p.stdout, p.stderr)
        self.assertIn("111", self._read_env(), "не дождался замка — файл не тронут")
        th = self._hold(0.5)
        p = self._bash_run('source "$INSTALL_BOT_SH"\nwrite_env "$TOKEN" 555; echo "rc=$?"')
        th.join()
        self.assertIn("rc=0", p.stdout, p.stderr)
        self.assertGreaterEqual(p.took, 0.4, "создание .env ждало замка")
        self.assertIn("ALLOWED_IDS=555", self._read_env())

    def test_the_ssh_menu_writes_the_owner_under_the_lock(self):
        funcs = _install_sh_functions("_bot_env_get", "_bot_admin_ids", "_bot_with_env_lock", "_bot_owner_write")
        with open(self.env_path, "w", encoding="utf-8") as fh:
            fh.write("BOT_TOKEN=%s\nALLOWED_IDS=111,222\nOWNER_ID=111\n" % FAKE_TOKEN)
        th = self._hold(0.5, "GOAMNEZIA_LANG=en\n")
        p = self._bash_run('BOT_DIR="$GOAMNEZIA_BOT_DIR"\n%s\n_bot_with_env_lock _bot_owner_write 222; echo "rc=$?"'
                           % funcs)
        th.join()
        self.assertIn("rc=0", p.stdout, p.stderr)
        self.assertGreaterEqual(p.took, 0.4, "запись владельца ждала замка")
        text = self._read_env()
        self.assertTrue(text.rstrip().endswith("OWNER_ID=222"), text)
        self.assertNotIn("OWNER_ID=111", text)
        self.assertIn("GOAMNEZIA_LANG=en", text, "чужая запись под замком не потерялась")
        self.assertEqual(stat.S_IMODE(os.stat(self.env_path).st_mode), 0o600)
        # выбранного уже нет в списке (удалили из бота, пока человек выбирал) — не пишем
        p = self._bash_run('BOT_DIR="$GOAMNEZIA_BOT_DIR"\n%s\n_bot_with_env_lock _bot_owner_write 999; echo "rc=$?"'
                           % funcs)
        self.assertIn("rc=3", p.stdout, p.stderr)
        self.assertNotIn("OWNER_ID=999", self._read_env())


class _PtyRun:
    """bash -c <скрипт> в псевдотерминале: ждать вывода по образцу, «нажимать» клавиши, дождаться конца.

    Дочерний процесс — копия всего прогона unittest: не вышло exec — только выйти, иначе он продолжил бы
    гнать остальные тесты параллельно с родителем.

    Сигналы в дочернем — по умолчанию, как у входа по SSH (TEST-FLAKE-09): прогон, запущенный фоновым
    заданием неинтерактивной оболочки (`… &` — параллельные наборы), получает SIGINT и SIGQUIT игнорируемыми;
    игнор переживает exec, а bash не ставит ловушку на сигнал, игнорируемый при входе, — Ctrl-C в окне
    привязки пропадал, и тест ждал «Привязка отменена» впустую. SIGPIPE и SIGXFSZ Python держит игнорируемыми
    у себя — тоже вернуть, как делает subprocess (restore_signals)."""

    ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\r")
    DEFAULT_SIGNALS = ("SIGINT", "SIGQUIT", "SIGHUP", "SIGTERM", "SIGPIPE", "SIGXFSZ")

    def __init__(self, bash, script, env):
        import pty
        import signal
        self.pid, self.fd = pty.fork()
        if self.pid == 0:                                  # pragma: no cover — дочерний процесс
            try:
                for name in self.DEFAULT_SIGNALS:
                    if hasattr(signal, name):
                        signal.signal(getattr(signal, name), signal.SIG_DFL)
                os.execve(bash, [bash, "-c", script], env)
            finally:
                os._exit(127)
        self.out = ""
        self.status = None

    def _pump(self, wait):
        """None — за wait ничего не пришло, True — прочитано, False — конец вывода."""
        import select
        ready, _, _ = select.select([self.fd], [], [], wait)
        if not ready:
            return None
        try:
            chunk = os.read(self.fd, 4096)
        except OSError:
            return False
        if not chunk:
            return False
        self.out += chunk.decode("utf-8", "replace")
        return True

    def screen(self):
        return self.ANSI.sub("", self.out)

    def wait_for(self, pattern, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            m = re.search(pattern, self.screen())
            if m:
                return m
            if self._pump(0.2) is False:
                break
        m = re.search(pattern, self.screen())
        if m:
            return m
        raise AssertionError("не дождался %r; экран:\n%s" % (pattern, self.screen()[-3000:]))

    def send(self, text):
        os.write(self.fd, text.encode("utf-8"))

    def finish(self, timeout=20):
        """Дождаться выхода (не вышел — убить), дочитать вывод; вернуть статус waitpid."""
        deadline = time.monotonic() + timeout
        while self.status is None:
            done, status = os.waitpid(self.pid, os.WNOHANG)
            if done:
                self.status = status
                break
            if time.monotonic() > deadline:
                try:
                    os.kill(self.pid, 9)
                except OSError:
                    pass
                self.status = os.waitpid(self.pid, 0)[1]
                break
            self._pump(0.1)
        while self._pump(0.05):
            pass
        try:
            os.close(self.fd)
        except OSError:
            pass
        return self.status


class CoreNameSuggestCrossTests(unittest.TestCase):
    """Ответ НАСТОЯЩЕГО ядра на name-suggest (install.sh в сухом прогоне) бот разбирает так, как ждёт «🎲»:
    обычная форма моста, id и label в корне (C47-1). Заглушка моста в тестах выше отвечает так же — здесь
    проверка, что так отвечает и само ядро."""

    @classmethod
    def setUpClass(cls):
        cls.bash = _bash5()
        if cls.bash is None:
            raise unittest.SkipTest("нужен bash 4+ (задайте BASH5)")
        if not shutil.which("jq"):
            raise unittest.SkipTest("нет jq — ядро без него не отвечает")

    def _core(self, *args):
        tmp = tempfile.mkdtemp(prefix="core-suggest-", dir=TMP_ROOT)
        gdir = os.path.join(tmp, "goamnezia")
        os.makedirs(gdir)
        with open(os.path.join(gdir, "config.json"), "w", encoding="utf-8") as fh:
            json.dump({"schema": 1, "installed": True, "mode": "lite", "lang": "ru",
                       "server_ip": "203.0.113.10", "profiles": {}}, fh)
        with open(os.path.join(gdir, "users.json"), "w", encoding="utf-8") as fh:
            json.dump({"schema": 1, "users": [{"name": "anna", "label": "Анна", "peers": {}}]}, fh)
        env = dict(os.environ, GOAMNEZIA_DRY_RUN="1", GOAMNEZIA_DIR=gdir, GOAMNEZIA_LOCK_DIR=os.path.join(tmp, "run"),
                   GOAMNEZIA_LOG_FILE=os.path.join(tmp, "log"), GOAMNEZIA_AUDIT_LOG=os.path.join(tmp, "audit"),
                   GOAMNEZIA_SERVER_IP="203.0.113.10", BOT_DIR=os.path.join(tmp, "bot"))
        p = subprocess.run([self.bash, os.path.join(ROOT, "install.sh"), "--action=name-suggest", "--json"] + list(args),
                           env=env, capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
        return p.stdout

    def test_the_bot_reads_the_core_answer(self):
        with open(os.path.join(ROOT, "lib", "names_capitals.json"), encoding="utf-8") as fh:
            names = {n["id"]: n for n in json.load(fh)}
        for lang in ("ru", "en"):
            out = self._core("--lang=%s" % lang, "--exclude=foggy_london")
            parsed = bot.parse_bridge_output(out, ok_shape=True)
            self.assertTrue(bot.bridge_ok(parsed), out)
            got = bot.name_suggestion(parsed)
            self.assertIsNotNone(got, out)
            self.assertIn(got["id"], names)
            self.assertNotEqual(got["id"], "foggy_london", "--exclude ядро соблюдает")
            self.assertEqual(got["label"], names[got["id"]][lang], "подпись — на языке --lang")
        refused = bot.parse_bridge_output(self._core("--lang=de"), ok_shape=True)
        self.assertEqual((refused["status"], refused["code"]), ("error", "bad_arg"))


# ── 0.6.0: Ленивый Pro, Pro, Semi-Pro, Lite (docs/MODES_047.md, «Договор 0.6.0»; дизайн iteration3 F-1…F-3) ──

def contract_mode_names():
    """Таблица названий из «Договора 0.6.0» (docs/MODES_047.md): id → (RU, EN). Сверяем с самим договором,
    а не с копией в тесте: разойдутся — тест скажет, где."""
    with open(os.path.join(ROOT, "docs", "MODES_047.md"), encoding="utf-8") as fh:
        text = fh.read()
    section = text.split("## Договор 0.6.0", 1)[1].split("\n## ", 1)[0]
    table = {}
    for m in re.finditer(r"^\|\s*(?:\(сценарий\)\s*)?`?([a-z_]+)`?\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*$",
                         section, re.M):
        if m.group(1) != "id":
            table[m.group(1)] = (m.group(2), m.group(3))
    return table


def _button_texts(rec):
    """Подписи кнопок последнего нарисованного экрана."""
    edits = [c for c in rec.calls if c[0] == "edit" and c[2].get("reply_markup") is not None]
    if not edits:
        return []
    return [b.text for row in edits[-1][2]["reply_markup"].inline_keyboard for b in row]


class ModeNames060Tests(_BotScreenCase):
    """F-2: режимы называются Pro / Semi-Pro / Lite с подписью из таблицы договора — во всех сценах, RU и EN.
    Прежние названия («Только VPN», «VPN и сайт», «Сайт и VPN на 443») как имена режимов не остаются нигде."""

    EN_UID = 919191
    OLD_NAMES = ("«Только VPN»", "«VPN и сайт»", "«Сайт и VPN на 443»", "«VPN only»", "«VPN and site»",
                 "«Site and VPN on 443»", "“VPN only”", "“VPN and site”", "“Site and VPN on 443”")

    def _mode(self, mode):
        os.environ["FAKE_BOT_MODE"] = mode
        bot.invalidate_status()
        self.addCleanup(os.environ.pop, "FAKE_BOT_MODE", None)
        self.addCleanup(bot.invalidate_status)

    def test_names_are_the_contract_table(self):
        self.assertTrue(i18n.set_user_lang(self.EN_UID, "en"))
        table = contract_mode_names()
        for m in ("pro", "lite_site", "lite"):
            self.assertIn(m, table, "в договоре нет строки %s" % m)
            self.assertEqual(bot.mode_label(None, m), table[m][0], m)
            self.assertEqual(bot.mode_label(self.EN_UID, m), table[m][1], m)
            # короткое имя для кнопок — то, что стоит в таблице до тире
            self.assertEqual(bot.mode_short(None, m), table[m][0].split(" — ")[0], m)
            self.assertEqual(bot.mode_short(self.EN_UID, m), table[m][1].split(" — ")[0], m)

    def test_old_names_are_gone_from_both_dictionaries(self):
        for lang in ("ru", "en"):
            with open(os.path.join(BOT_SRC, "lang", "%s.json" % lang), encoding="utf-8") as fh:
                d = json.load(fh)
            stale = sorted(k for k, v in d.items() if any(old in v for old in self.OLD_NAMES))
            self.assertEqual(stale, [], "%s: прежние названия режимов в %s" % (lang, stale))

    def test_every_scene_names_the_mode_with_its_caption(self):
        self._mode("lite_site")
        semi = bot.mode_label(self.UID, "lite_site")
        self.assertEqual(semi, "Semi-Pro — VPN и сайт")
        status = run(bot.get_status(fresh=True))
        self.assertIn(semi, bot.dashboard_text(self.UID, status))
        self.assertIn(semi, bot.status_report_text(self.UID, status))
        self.assertIn(bot.mode_label(self.EN_UID, "lite_site"), bot.dashboard_text(self.EN_UID, status))
        for screen in ("se_mode", "se_about"):
            text, _, _ = self._screen(screen)
            self.assertIn("<b>%s</b>" % semi, text, screen)
        text, _, _ = self._screen("se_pf")
        for m in bot.MODES:
            self.assertIn("<b>%s</b>" % bot.mode_label(self.UID, m), text)
        # порядок — как в мастере по договору: Pro → Semi-Pro → Lite
        pos = [text.index(bot.mode_label(self.UID, m)) for m in ("pro", "lite_site", "lite")]
        self.assertEqual(pos, sorted(pos), "se_pf: порядок режимов")
        rec, _ = self._press("se_mode_pick")
        text = str([c for c in rec.calls if c[0] == "edit"][-1][1][0])
        for m in bot.MODES:
            self.assertIn(bot.mode_label(self.UID, m), text)
        pos = [text.index(bot.mode_label(self.UID, m)) for m in ("pro", "lite_site", "lite")]
        self.assertEqual(pos, sorted(pos), "se_mode_pick: порядок режимов")
        self.assertIn("<b>%s ✓</b>" % semi, text, "текущий режим отмечен")
        labels = _button_texts(rec)
        # кнопки — коротким именем: подпись уже стоит на экране над ними
        self.assertIn(i18n.tf(self.UID, "se_mode_to_fmt", "Pro"), labels)
        self.assertIn(i18n.tf(self.UID, "se_mode_to_fmt", "Lite"), labels)

    def test_confirmation_and_progress_name_the_mode_with_its_caption(self):
        self._mode("pro")
        text, _, ctx = self._screen("se_mode_to~lite")
        self.assertIn("<b>%s</b>" % bot.mode_label(self.UID, "lite"), text)
        rec = self._say("MODE lite", ctx)
        replies = " ".join(str(c[1][0]) for c in rec.calls if c[0] == "reply_text")
        self.assertIn(i18n.tf(self.UID, "se_mode_switching_fmt", bot.mode_label(self.UID, "lite")), replies)


def _replies(rec):
    """Ответы бота на сообщение: (текст, kwargs) по порядку — reply_text и правки статуса edit_text."""
    return [(str(c[1][0]), c[2]) for c in rec.calls if c[0] in ("reply_text", "edit_text")]


def _markup_datas(kw):
    markup = kw.get("reply_markup")
    return [b.callback_data for row in markup.inline_keyboard for b in row] if markup is not None else []


class LazyPro060Tests(_BotScreenCase):
    """F-1: Ленивый Pro в «Режим и сайт» — домен → lazy-check (план и причины) → фраза LAZY <домен> →
    lazy-setup (долгое, «занято» и очередь как у прочих) → итог и страницы подключения. Мост — заглушка
    по договору 0.6.0 (FAKE_BOT_LAZY, FAKE_BOT_LAZY_SETUP)."""

    EN_UID = 929292

    def setUp(self):
        bot.invalidate_status()
        self.addCleanup(bot.invalidate_status)
        self.args = os.path.join(TMP_ROOT, "lazy_args_%s.txt" % self._testMethodName)
        if os.path.exists(self.args):
            os.unlink(self.args)
        os.environ["FAKE_BOT_ARGS_FILE"] = self.args
        self.addCleanup(os.environ.pop, "FAKE_BOT_ARGS_FILE", None)

    def _env(self, **kw):
        for k, v in kw.items():
            os.environ[k] = v
            self.addCleanup(os.environ.pop, k, None)

    def _calls(self, action):
        if not os.path.exists(self.args):
            return []
        with open(self.args, encoding="utf-8") as fh:
            return [line for line in fh.read().splitlines() if "--action=%s\t" % action in line]

    def _plan(self, domain="vpn.example.com"):
        """Шаги 1–2: «🦥 Ленивый Pro» → домен текстом → экран плана (текст, кнопки, контекст)."""
        _, ctx = self._press("se_lazy")
        self.assertEqual(ctx.user_data["flow"]["kind"], "lazy_domain")
        rec = self._say(domain, ctx)
        replies = _replies(rec)
        self.assertEqual(replies[0][0], i18n.tf(self.UID, "lazy_checking_fmt",
                                                bot.esc(bot.lazy_domain_input(domain)[1])))
        text, kw = replies[-1]
        return text, _markup_datas(kw), ctx

    def test_mode_pick_offers_lazy_pro_first(self):
        rec, _ = self._press("se_mode_pick")
        text = str([c for c in rec.calls if c[0] == "edit"][-1][1][0])
        lazy = bot.lazy_label(self.UID)
        self.assertIn("<b>%s</b>" % lazy, text)
        self.assertLess(text.index(lazy), text.index(bot.mode_label(self.UID, "pro")), "Ленивый Pro — первым, как в мастере")
        datas = [b.callback_data for row in [c for c in rec.calls if c[0] == "edit"][-1][2]["reply_markup"].inline_keyboard
                 for b in row]
        self.assertEqual(datas[0], "se_lazy")
        self.assertIn(i18n.t(self.UID, "lazy_btn"), _button_texts(rec))
        # доступность — по TCP 80/443 (preflight Semi-Pro): занято чужим — ⛔ с причиной словами ядра
        self._env(FAKE_BOT_PREFLIGHT="busy")
        text, _, _ = self._screen("se_mode_pick")
        lazy_block = text.split(lazy, 1)[1].split("\n\n", 1)[0]
        self.assertIn("⛔ TCP/443 занят: nginx", lazy_block)

    def test_lazy_name_is_the_contract_row(self):
        self.assertTrue(i18n.set_user_lang(self.EN_UID, "en"))
        table = contract_mode_names()
        self.assertEqual(bot.lazy_label(None), table["lazy"][0])
        self.assertEqual(bot.lazy_label(self.EN_UID), table["lazy"][1])

    def test_domain_is_cleaned_without_dead_ends(self):
        for raw, want in (("https://VPN.Example.com/path/.", "vpn.example.com"), ("vpn.example.com:443", "vpn.example.com"),
                          ("vpn.example.com.", "vpn.example.com"), ("Пример.РФ", "xn--e1afmkfd.xn--p1ai"),
                          ("http://пример.рф./x?y", "xn--e1afmkfd.xn--p1ai")):
            self.assertEqual(bot.lazy_domain_input(raw)[0], want, raw)
        self.assertEqual(bot.lazy_domain_input("Пример.РФ")[1], "пример.рф", "показываем, как прислали")
        for raw in ("192.168.1.1", "vpn", "", "ex_ample.com", "a" * 300 + ".com"):
            self.assertEqual(bot.lazy_domain_input(raw)[2], "lazy_bad_domain", raw)
        self.assertEqual(bot.lazy_domain_input("пример..рф")[2], "lazy_bad_idn")

    def test_pro_plan_then_phrase_then_long_setup_and_result(self):
        _, ctx = self._press("se_lazy")
        rec = self._say("192.168.1.1", ctx)                          # IP — не домен: ждём дальше
        self.assertEqual([t for t, _ in _replies(rec)], [i18n.t(self.UID, "lazy_bad_domain")])
        self.assertEqual(ctx.user_data["flow"]["kind"], "lazy_domain")
        self.assertEqual(self._calls("lazy-check"), [])
        rec = self._say("https://VPN.Example.com/x", ctx)
        self.assertNotIn("flow", ctx.user_data)
        self.assertIn("--action=lazy-check\t--json\t--domain=vpn.example.com\t", self._calls("lazy-check")[-1])
        text, kw = _replies(rec)[-1]
        self.assertIn(i18n.tf(self.UID, "lazy_dns_ok_fmt", "203.0.113.10"), text)
        self.assertIn(i18n.tf(self.UID, "lazy_port_free_fmt", "UDP 443"), text)
        self.assertIn(i18n.tf(self.UID, "lazy_plan_pro_fmt", bot.mode_label(self.UID, "pro"), bot.LAZY_KEYS), text)
        self.assertIn(i18n.t(self.UID, "lazy_why_20"), text, "почему на 443 — 2.0 (решение владельца)")
        self.assertEqual(_markup_datas(kw), ["se_lazy_go", "se_lazy_re", "se_lazy", "se_mode_pick"])
        # шаг 3: фраза, как у прочих разрушительных действий; до неё мост не зовётся
        text, datas, ctx = self._screen("se_lazy_go", ctx)
        self.assertEqual(bot.CONFIRM.pending(self.UID)["phrase"], "LAZY vpn.example.com")
        self.assertIn(i18n.t(self.UID, "lazy_long_note"), text)
        self.assertIn(i18n.t(self.UID, "lazy_installed_note"), text, "у заглушки уже стоят профили")
        self.assertEqual(datas, ["tc_cancel~se_mode_pick"])
        self.assertEqual(self._calls("lazy-setup"), [])
        self._say("LAZY vpn.example.co", ctx)                        # не та фраза — ждём дальше
        self.assertEqual(self._calls("lazy-setup"), [])
        # шаги 4–5: долгое действие и итог
        rec = self._say("LAZY vpn.example.com", ctx)
        self.assertIsNone(bot.CONFIRM.pending(self.UID))
        self.assertIn("--action=lazy-setup\t--json\t--domain=vpn.example.com\t--keys=5\t", self._calls("lazy-setup")[-1])
        replies = _replies(rec)
        self.assertEqual(replies[0][0], "⏳ " + i18n.tf(self.UID, "lazy_running_fmt", "vpn.example.com"))
        result, kw = replies[1]
        self.assertTrue(result.startswith("✅ <b>%s</b>" % i18n.t(self.UID, "lazy_done_title")))
        self.assertIn("<b>%s</b>" % bot.mode_label(self.UID, "pro"), result)
        self.assertIn(i18n.tf(self.UID, "lazy_done_udp443_fmt", "AmneziaWG 2.0"), result)
        # у заглушки 3.1 стоял на 39001 — итог называет смену порта: старые конфиги этой линии не подключатся
        self.assertIn(i18n.tf(self.UID, "lazy_done_port_moved_fmt", "AmneziaWG 3.1", "39001", "38211"), result)
        self.assertNotIn(i18n.tf(self.UID, "lazy_done_port_moved_fmt", "AmneziaWG 2.0", "443", "443"), result)
        self.assertIn(i18n.t(self.UID, "lazy_code_template_fallback"), result)
        self.assertIn(i18n.t(self.UID, "lazy_code_adopted"), result)
        self.assertIn(i18n.tf(self.UID, "lazy_warn_unknown_fmt", "mystery_warning"), result, "незнакомый код — как есть")
        self.assertIn(i18n.tf(self.UID, "lazy_done_keys_fmt", 5), result)
        self.assertEqual(_markup_datas(kw), ["menu_users", "se_check", "se_panel", "se_mode"])
        pages, kw = replies[2]
        self.assertTrue(kw.get("disable_web_page_preview"), "превью засчитало бы открытие страницы")
        for i in range(1, 6):
            self.assertIn("<code>https://vpn.example.com/p-000%d/</code>" % i, pages)
        self.assertIn("Туманный Лондон (<code>foggy_london</code>)", pages)
        self.assertEqual(len(replies), 3)

    def test_semi_pro_plan_when_udp_443_is_taken(self):
        self._env(FAKE_BOT_LAZY="semi_pro")
        text, datas, _ = self._plan()
        # by — формулировка предполётной проверки целиком, какой бы длинной она ни была
        self.assertIn("⚠️ " + i18n.tf(self.UID, "lazy_port_busy_fmt", "UDP 443", "xray (PID 1700, юнит x-ui.service) — похоже, это 3x-ui"), text)
        self.assertIn(i18n.tf(self.UID, "lazy_plan_semi_fmt", bot.mode_label(self.UID, "lite_site"), bot.LAZY_KEYS), text)
        self.assertNotIn(i18n.t(self.UID, "lazy_code_udp443_busy"), text, "UDP 443 уже назван строкой порта")
        self.assertIn(i18n.tf(self.UID, "lazy_app_fmt", "amnezia-awg (1.5, UDP 38000)"), text)
        self.assertNotIn(i18n.t(self.UID, "lazy_why_20"), text, "на 443 ничего не встаёт — и объяснять нечего")
        self.assertEqual(datas[0], "se_lazy_go")

    def test_blocked_plan_names_the_reasons_and_offers_no_install(self):
        self._env(FAKE_BOT_LAZY="blocked")
        text, datas, ctx = self._plan()
        self.assertIn(i18n.t(self.UID, "lazy_plan_blocked"), text)
        self.assertIn(i18n.tf(self.UID, "lazy_dns_foreign_fmt", "198.51.100.50", "203.0.113.10", i18n.t(self.UID, "btn_refresh")), text)
        self.assertIn("⛔ " + i18n.tf(self.UID, "lazy_port_busy_fmt", "TCP 443", "nginx (PID 812, юнит nginx.service) — похоже, это goTelegram Pro"), text)
        self.assertIn(i18n.tf(self.UID, "lazy_reason_unknown_fmt", "mystery_code"), text)
        self.assertNotIn("dns_elsewhere", text, "известные коды уже сказаны строками фактов")
        self.assertNotIn(i18n.t(self.UID, "lazy_code_dns_elsewhere"), text, "и не повторяются своими словами")
        self.assertNotIn("se_lazy_go", datas)
        self.assertEqual(datas, ["se_lazy_re", "se_lazy", "se_mode_pick"])
        # «🔄 Обновить» — та же проверка того же домена
        self._screen("se_lazy_re", ctx)
        self.assertEqual(len(self._calls("lazy-check")), 2)
        # старая кнопка «Поставить» при плане «не ставим» — только новая проверка, фраза не взводится
        self._screen("se_lazy_go", ctx)
        self.assertEqual(len(self._calls("lazy-check")), 3)
        self.assertIsNone(bot.CONFIRM.pending(self.UID))

    def test_cloudflare_and_cyrillic_domain(self):
        self._env(FAKE_BOT_LAZY="cf")
        text, datas, ctx = self._plan("Пример.РФ")
        self.assertIn("--domain=xn--e1afmkfd.xn--p1ai", self._calls("lazy-check")[-1])
        self.assertIn("пример.рф (<code>xn--e1afmkfd.xn--p1ai</code>)", text)
        self.assertIn(i18n.t(self.UID, "lazy_dns_cf"), text)
        self.assertIn("se_lazy_go", datas)
        self._press("se_lazy_go", ctx)
        self.assertEqual(bot.CONFIRM.pending(self.UID)["phrase"], "LAZY xn--e1afmkfd.xn--p1ai", "фраза — латиницей")

    def test_stale_or_missing_check(self):
        text, datas, _ = self._screen("se_lazy_go")                  # проверки не было (бот перезапускался)
        self.assertIn(i18n.t(self.UID, "lazy_expired"), text)
        self.assertEqual(datas, ["se_lazy", "se_mode_pick"])
        self.assertEqual(self._calls("lazy-check"), [])
        _, _, ctx = self._plan()
        ctx.user_data["lazy"]["at"] -= bot.LAZY_PLAN_TTL + 5         # план устарел — сначала проверяем снова
        text, datas, _ = self._screen("se_lazy_go", ctx)
        self.assertEqual(len(self._calls("lazy-check")), 2)
        self.assertIsNone(bot.CONFIRM.pending(self.UID))
        self.assertIn("se_lazy_go", datas)

    def test_core_without_lazy_pro_says_update(self):
        self._env(FAKE_BOT_LAZY="unknown")
        text, datas, _ = self._plan()
        self.assertIn(i18n.t(self.UID, "lazy_unsupported"), text)
        self.assertEqual(datas, ["se_mode_pick"])
        os.environ["FAKE_BOT_LAZY"] = "pro"
        _, _, ctx = self._plan()
        self._press("se_lazy_go", ctx)
        self._env(FAKE_BOT_LAZY_SETUP="unknown")
        rec = self._say("LAZY vpn.example.com", ctx)
        self.assertEqual(_replies(rec)[-1][0], i18n.t(self.UID, "lazy_unsupported"))

    def test_setup_refused_by_the_core(self):
        _, _, ctx = self._plan()
        self._press("se_lazy_go", ctx)
        self._env(FAKE_BOT_LAZY_SETUP="error")
        rec = self._say("LAZY vpn.example.com", ctx)
        replies = _replies(rec)
        self.assertTrue(replies[-1][0].startswith("❌ "), replies[-1][0])
        self.assertIn("TCP/443 занят", replies[-1][0])
        self.assertEqual(len(replies), 2, "страниц нет — второго сообщения нет")

    def test_phrase_during_a_bot_operation_is_queued(self):
        _, _, ctx = self._plan()
        self._press("se_lazy_go", ctx)
        saved = bot._BOT_ACTION_LOCK
        bot._BOT_ACTION_LOCK = _HeldLock()
        self.addCleanup(setattr, bot, "_BOT_ACTION_LOCK", saved)
        rec = self._say("LAZY vpn.example.com", ctx)
        replies = _replies(rec)
        self.assertEqual(replies[0][0], i18n.t(self.UID, "action_queued"), "фраза принята — ждёт замок, не отменяется")
        self.assertEqual(len(self._calls("lazy-setup")), 1)

    def test_install_button_during_a_bot_operation_is_busy(self):
        """Кнопка «Поставить», пока бот занят (другая долгая операция): «занято», как у прочих кнопок, —
        фраза не взводится, вторая установка в очередь за первой не встаёт."""
        _, _, ctx = self._plan()
        saved = bot._BOT_ACTION_LOCK
        bot._BOT_ACTION_LOCK = _HeldLock()
        self.addCleanup(setattr, bot, "_BOT_ACTION_LOCK", saved)
        text, datas, _ = self._screen("se_lazy_go", ctx)
        self.assertIn(i18n.t(self.UID, "action_busy_body"), text)
        self.assertEqual(datas, ["se_mode_pick"])
        self.assertIsNone(bot.CONFIRM.pending(self.UID))

    def test_lock_held_by_ssh_panel_or_agent_is_a_wait(self):
        _, _, ctx = self._plan()
        self._press("se_lazy_go", ctx)
        saved = bot.run_bot_action

        async def fake(action, *a, **kw):
            if action == "lazy-setup":
                return {"status": "error", "code": "lock_timeout", "message": "", "action": action}
            return await saved(action, *a, **kw)
        bot.run_bot_action = fake
        self.addCleanup(setattr, bot, "run_bot_action", saved)
        rec = self._say("LAZY vpn.example.com", ctx)
        self.assertEqual(_replies(rec)[-1][0], "<b>⏳ %s</b>" % i18n.t(self.UID, "err_lock_timeout"))

    # Редкие warnings lazy-setup — «По итогам ядра (28.09)» в docs/MODES_047.md (каталог их не перечисляет)
    RARE_WARNINGS = ("adopt_failed", "firewall_open_failed", "line_skipped", "profile_failed", "site_failed",
                     "timers_failed")

    @staticmethod
    def _catalog_codes(action, field):
        """Коды из описания действия в НАСТОЯЩЕМ каталоге: «reasons [код…]: a, b, c» / «warnings [код…]: …»."""
        with open(ACTIONS_JSON, encoding="utf-8") as fh:
            desc = json.load(fh)["actions"][action]["description"]["ru"]
        m = re.search(r"%s \[код…\]: ([a-z0-9_, ]+)" % field, desc)
        return [c.strip() for c in m.group(1).split(",") if c.strip()] if m else []

    def test_every_catalog_and_contract_code_has_words_in_both_languages(self):
        reasons = self._catalog_codes("lazy-check", "reasons")
        warnings = self._catalog_codes("lazy-setup", "warnings")
        self.assertIn("no_line_for_443", reasons, "каталог перечисляет причины lazy-check — формат описания сменился?")
        self.assertIn("panel_off", warnings, "каталог перечисляет warnings lazy-setup — формат описания сменился?")
        for code in reasons + warnings + list(self.RARE_WARNINGS):
            self.assertIn(code, bot.LAZY_CODES)
        for code in bot.LAZY_CODES:
            for lang in ("ru", "en"):
                self.assertNotEqual(i18n.t_in(lang, "lazy_code_%s" % code), "lazy_code_%s" % code, (lang, code))

    def test_app_container_on_tcp_443_is_explained_not_broken(self):
        self._env(FAKE_BOT_LAZY="app")
        text, datas, _ = self._plan()
        self.assertIn("⛔ " + i18n.tf(self.UID, "lazy_port_busy_fmt", "TCP 443",
                                      "docker-proxy (PID 2211, контейнер amnezia-xray)"), text)
        self.assertIn(i18n.t(self.UID, "lazy_code_tcp443_app"), text)
        self.assertNotIn("se_lazy_go", datas)

    def test_partly_foreign_a_records_are_named(self):
        """Часть A-записей чужие: у ядра это points_here=false и план «не встанет» (dns_partial) — свои отмечены ✓,
        а не «A-запись ведёт не сюда» про запись, которая ведёт сюда."""
        self._env(FAKE_BOT_LAZY="partial")
        text, datas, _ = self._plan()
        self.assertIn(i18n.tf(self.UID, "lazy_dns_partial_fmt", "198.51.100.50, 203.0.113.10 ✓",
                              i18n.t(self.UID, "btn_refresh")), text)
        self.assertNotIn(i18n.t(self.UID, "lazy_code_dns_partial"), text, "из данных — и без повтора по коду")
        self.assertNotIn("se_lazy_go", datas)

    def test_semi_pro_result_without_panel_and_with_a_key_without_page(self):
        self._env(FAKE_BOT_LAZY="semi_pro", FAKE_BOT_LAZY_SETUP="semi")
        _, _, ctx = self._plan()
        self._press("se_lazy_go", ctx)
        rec = self._say("LAZY vpn.example.com", ctx)
        replies = _replies(rec)
        result, kw = replies[1]
        self.assertIn("<b>%s</b>" % bot.mode_label(self.UID, "lite_site"), result)
        self.assertIn(i18n.t(self.UID, "lazy_done_udp443_none"), result)
        self.assertIn(i18n.t(self.UID, "lazy_code_panel_off"), result)
        self.assertNotIn(i18n.t(self.UID, "lazy_code_udp443_busy"), result, "про UDP 443 итог уже сказал строкой")
        self.assertNotIn(i18n.tf(self.UID, "lazy_done_panel_fmt", i18n.t(self.UID, "se_panel")), result)
        self.assertEqual(_markup_datas(kw), ["menu_users", "se_check", "se_mode"], "панели на домене нет — и кнопки нет")
        pages = replies[2][0]
        self.assertIn("<i>%s</i>" % i18n.t(self.UID, "lazy_page_none"), pages)
        self.assertEqual(pages.count("https://vpn.example.com/p-000"), 4)

    def test_the_line_on_443_comes_from_the_check(self):
        """Какая линия встанет на UDP 443 — из lazy-check.udp443_line: 2.0 — почему она, другая — что порты с людьми
        не двигаем, поля нет — молчим; на экране плана и в подтверждении одинаково."""
        why20, line31 = i18n.t(self.UID, "lazy_why_20"), i18n.tf(self.UID, "lazy_line443_fmt", "3.1")
        for variant, want, absent in (("pro", why20, line31), ("pro31", line31, why20), ("pro_old", None, None)):
            os.environ["FAKE_BOT_LAZY"] = variant
            self.addCleanup(os.environ.pop, "FAKE_BOT_LAZY", None)
            text, _, ctx = self._plan()
            confirm, _, _ = self._screen("se_lazy_go", ctx)
            for screen in (text, confirm):
                if want:
                    self.assertIn(want, screen, variant)
                    self.assertNotIn(absent, screen, variant)
                else:
                    self.assertNotIn(why20, screen, "поля нет — какую линию, не угадываем")
                    self.assertNotIn(line31.split("3.1")[0], screen)
            self.assertNotIn("2.0 на UDP 443 и 3.1", text, "план не обещает 2.0 на 443 заранее")

    def _lite_status(self):
        """Lite, обе линии на своих портах — «Lite → Ленивый Pro» (частый случай no_line_for_443)."""
        saved = bot.get_status

        async def fake(*_a, **_k):
            return {"status": "success", "mode": "lite", "domain": "", "server_ip": "203.0.113.10", "users": 2,
                    "site": None, "profiles": [{"profile": "awg3", "state": "running", "port": 39001, "line": "3.1"},
                                               {"profile": "awg2", "state": "running", "port": 5388, "line": "2.0"}]}
        bot.get_status = fake
        self.addCleanup(setattr, bot, "get_status", saved)

    def test_no_line_for_443_is_semi_pro_not_a_busy_port(self):
        self._lite_status()
        self._env(FAKE_BOT_LAZY="nolines", FAKE_BOT_LAZY_SETUP="nolines")
        text, datas, ctx = self._plan()
        semi = bot.mode_label(self.UID, "lite_site")
        self.assertIn(i18n.tf(self.UID, "lazy_plan_semi_other_fmt", semi, bot.LAZY_KEYS), text)
        self.assertNotIn(i18n.tf(self.UID, "lazy_plan_semi_fmt", semi, bot.LAZY_KEYS), text, "UDP 443 не занят")
        words = i18n.t(self.UID, "lazy_code_no_line_for_443")
        self.assertIn(words, text)
        self.assertIn("se_lazy_go", datas)
        for lang in ("ru", "en"):                            # как потом: «Линия на UDP 443», ключи — заново
            said = i18n.t_in(lang, "lazy_code_no_line_for_443")
            self.assertIn(i18n.t_in(lang, "pl_btn").split(" ", 1)[1], said, lang)
        confirm, _, ctx = self._screen("se_lazy_go", ctx)
        self.assertIn(words, confirm)
        replies = _replies(self._say("LAZY vpn.example.com", ctx))
        result, kw = replies[1]
        self.assertIn(i18n.t(self.UID, "lazy_done_udp443_free"), result)
        self.assertNotIn(i18n.t(self.UID, "lazy_done_udp443_none"), result, "не «UDP 443 занят»")
        self.assertIn(words, result)
        moved_tail = i18n.tf(self.UID, "lazy_done_port_moved_fmt", "A", "B", "C").split("C", 1)[1]
        self.assertNotIn(moved_tail, result, "порты линий с пользователями не двигались")
        self.assertIn("se_mode_to~pro", _markup_datas(kw), "линию на UDP 443 — отсюда же, выбором при переходе в Pro")

    def test_dns_that_did_not_answer_and_a_busy_2_0_place(self):
        self._env(FAKE_BOT_LAZY="dnsunknown")
        text, datas, _ = self._plan()
        self.assertIn(i18n.tf(self.UID, "lazy_dns_unknown_fmt", i18n.t(self.UID, "btn_refresh")), text)
        self.assertNotIn(i18n.tf(self.UID, "lazy_dns_none_fmt", "203.0.113.10", i18n.t(self.UID, "btn_refresh")), text,
                         "DNS не ответил — это не «нет A-записи»")
        self.assertNotIn("se_lazy_go", datas)
        os.environ["FAKE_BOT_LAZY"] = "awg2busy"
        text, datas, _ = self._plan()
        self.assertIn(i18n.t(self.UID, "lazy_code_awg2_container_busy"), text)
        self.assertIn(i18n.t(self.UID, "lazy_plan_blocked"), text)
        self.assertNotIn("se_lazy_go", datas)

    def test_setup_refusal_shows_plan_and_reasons_like_the_check(self):
        _, _, ctx = self._plan()
        self._press("se_lazy_go", ctx)
        self._env(FAKE_BOT_LAZY_SETUP="refused")
        text, kw = _replies(self._say("LAZY vpn.example.com", ctx))[-1]
        self.assertTrue(text.startswith("❌ Домен vpn.example.com указывает не сюда"), text)
        self.assertIn("<code>dns_not_here</code>", text)
        for want in (i18n.t(self.UID, "lazy_plan_blocked"), i18n.t(self.UID, "lazy_code_dns_elsewhere"),
                     i18n.t(self.UID, "lazy_code_tcp443_busy"), i18n.tf(self.UID, "lazy_reason_unknown_fmt", "mystery_code"),
                     i18n.tf(self.UID, "lazy_blocked_hint_fmt", i18n.t(self.UID, "btn_refresh"))):
            self.assertIn(want, text)
        self.assertEqual(_markup_datas(kw), ["se_lazy_re", "se_lazy", "se_mode_pick"])
        # «🔄 Обновить» после отказа — снова проверка того же домена
        self._screen("se_lazy_re", ctx)
        self.assertEqual(len(self._calls("lazy-check")), 2)
        # сорвалось по ходу при выполнимом плане — причины есть, «не встанет» — нет
        _, _, ctx = self._plan()
        self._press("se_lazy_go", ctx)
        os.environ["FAKE_BOT_LAZY_SETUP"] = "midway"
        text, kw = _replies(self._say("LAZY vpn.example.com", ctx))[-1]
        self.assertTrue(text.startswith("❌ "), text)
        self.assertIn(i18n.t(self.UID, "lazy_code_cloudflare_proxied"), text)
        self.assertNotIn(i18n.t(self.UID, "lazy_plan_blocked"), text)
        self.assertNotIn(i18n.tf(self.UID, "lazy_plan_pro_fmt", bot.mode_label(self.UID, "pro"), bot.LAZY_KEYS), text)
        self.assertEqual(_markup_datas(kw), ["se_lazy_re", "se_lazy", "se_mode_pick"])

    def test_rare_warnings_have_words(self):
        _, _, ctx = self._plan()
        self._press("se_lazy_go", ctx)
        self._env(FAKE_BOT_LAZY_SETUP="rare")
        result = _replies(self._say("LAZY vpn.example.com", ctx))[1][0]
        for code in self.RARE_WARNINGS:
            self.assertIn(i18n.t(self.UID, "lazy_code_%s" % code), result, code)
        self.assertNotIn(i18n.tf(self.UID, "lazy_warn_unknown_fmt", "X").split("X")[0], result)

    def test_long_action_timeout(self):
        self.assertGreaterEqual(bot.BRIDGE_TIMEOUT_BY_ACTION["lazy-setup"], bot.BRIDGE_TIMEOUT_BY_ACTION["profile-install"])

    def test_not_installed_screen_points_to_lazy_pro(self):
        for lang in ("ru", "en"):
            self.assertIn(i18n.t_in(lang, "mode_name_lazy"), i18n.t_in(lang, "dash_not_installed"), lang)


class ProLine060Tests(_BotScreenCase):
    """F-3: какая линия стоит на UDP 443 в Pro — при переходе в Pro (сцена mode-pro-line) и в самом Pro
    («🎯 Линия на UDP 443»), через pro-line: фраза LINE <профиль>, подхваченный у приложения профиль — ещё и
    MOVE <профиль> (--adopted-confirm, ACC-2). Мост — заглушка по договору 0.6.0 (FAKE_BOT_PRO_LINE)."""

    def setUp(self):
        bot.invalidate_status()
        self.addCleanup(bot.invalidate_status)
        self.args = os.path.join(TMP_ROOT, "pro_line_args_%s.txt" % self._testMethodName)
        if os.path.exists(self.args):
            os.unlink(self.args)
        os.environ["FAKE_BOT_ARGS_FILE"] = self.args
        self.addCleanup(os.environ.pop, "FAKE_BOT_ARGS_FILE", None)

    def _env(self, **kw):
        for k, v in kw.items():
            os.environ[k] = v
            self.addCleanup(os.environ.pop, k, None)
        bot.invalidate_status()

    def _calls(self, action):
        if not os.path.exists(self.args):
            return []
        with open(self.args, encoding="utf-8") as fh:
            return [line for line in fh.read().splitlines() if "--action=%s\t" % action in line]

    def _status(self, mode, ports):
        """status с профилями {id: порт} (линии — по id), всё остальное — как у заглушки в этом режиме."""
        saved = bot.get_status
        lines = {"awg1": "1.5", "awg2": "2.0", "awg3": "3.1"}

        async def fake(*_a, **_k):
            return {"status": "success", "mode": mode, "domain": "vpn.example.com", "server_ip": "203.0.113.10",
                    "users": 1, "site": None, "mode_port_mismatch": mode == "pro" and "443" not in map(str, ports.values()),
                    "profiles": [{"profile": p, "state": "running", "port": port, "line": lines[p]} for p, port in ports.items()]}
        bot.get_status = fake
        self.addCleanup(setattr, bot, "get_status", saved)

    def test_switch_to_pro_asks_which_line_goes_on_443(self):
        self._env(FAKE_BOT_MODE="lite")
        rec, _ = self._press("se_mode_to~pro")
        text = str([c for c in rec.calls if c[0] == "edit"][-1][1][0])
        self.assertIn("<b>%s</b>" % i18n.t(self.UID, "pl_title"), text)
        self.assertIn(i18n.t(self.UID, "pl_ask"), text)
        self.assertIn(i18n.tf(self.UID, "pl_why_20_fmt", i18n.t(self.UID, "pl_btn")), text, "почему 2.0 — на экране")
        self.assertIn(i18n.tf(self.UID, "pl_now_fmt", "AmneziaWG 2.0"), text)
        labels = _button_texts(rec)
        self.assertEqual(labels[:2], [i18n.tf(self.UID, "pl_btn_now_fmt", "2.0"), i18n.tf(self.UID, "pl_btn_fmt", "3.1 · 39001")])
        self.assertEqual(_markup_datas([c for c in rec.calls if c[0] == "edit"][-1][2]),
                         ["se_mode_pl~awg2", "se_mode_pl~awg3", "se_mode_pick"])
        # 2.0 не на 443 — у неё «рекомендуем», и она первая
        self._status("lite_site", {"awg3": 443, "awg2": 5388})
        rec, _ = self._press("se_mode_to~pro")
        self.assertEqual(_button_texts(rec)[:2], [i18n.tf(self.UID, "pl_btn_rec_fmt", "2.0 · 5388"),
                                                  i18n.tf(self.UID, "pl_btn_now_fmt", "3.1")])

    def test_switch_to_pro_with_3_1_on_443(self):
        self._env(FAKE_BOT_MODE="lite")
        _, ctx = self._press("se_mode_to~pro")
        text, datas, ctx = self._screen("se_mode_pl~awg3", ctx)
        self.assertIn("se_mode_nodom~pro~awg3", datas, "линия едет дальше в кнопке")
        rec = self._say("vpn.example.com", ctx)
        confirm = [str(c[1][0]) for c in rec.calls if c[0] == "reply_text"][-1]
        self.assertIn(i18n.tf(self.UID, "pl_switch_move_fmt", "AmneziaWG 3.1", "39001"), confirm)
        self.assertIn(i18n.tf(self.UID, "pl_prev_fmt", "AmneziaWG 2.0"), confirm)
        self.assertEqual(self._calls("pro-line"), [])
        rec = self._say("MODE pro", ctx)
        self.assertIn("--action=change-mode\t--json\t--mode=pro\t--domain=vpn.example.com", self._calls("change-mode")[-1])
        self.assertIn("--action=pro-line\t--json\t--profile=awg3\t", self._calls("pro-line")[-1])
        result = [str(c[1][0]) for c in rec.calls if c[0] == "reply_text"][-1]
        self.assertIn(i18n.tf(self.UID, "pl_done_fmt", "AmneziaWG 3.1"), result)
        self.assertIn("AmneziaWG 3.1 · UDP 443, AmneziaWG 2.0 · UDP 38211", result, "порты — из ответа pro-line")
        self.assertIn(i18n.t(self.UID, "pr_port_note"), result)

    def test_switch_to_pro_when_the_line_fails(self):
        self._env(FAKE_BOT_MODE="lite", FAKE_BOT_PRO_LINE="error")
        _, ctx = self._press("se_mode_to~pro")
        _, _, ctx = self._screen("se_mode_pl~awg3", ctx)
        self._say("vpn.example.com", ctx)
        rec = self._say("MODE pro", ctx)
        reply = [c for c in rec.calls if c[0] == "reply_text"][-1]
        self.assertIn(i18n.t(self.UID, "pl_failed"), str(reply[1][0]))
        self.assertIn("port_busy", str(reply[1][0]))
        self.assertEqual(_markup_datas(reply[2]), ["se_check", "se_pline", "se_mode"], "режим сменился — линию выбрать снова")

    def test_in_pro_the_line_is_chosen_with_a_phrase(self):
        self._env(FAKE_BOT_MODE="pro")
        text, datas, _ = self._screen("se_mode")
        self.assertIn(i18n.tf(self.UID, "pl_now_fmt", "AmneziaWG 2.0"), text)
        self.assertEqual(datas.count("se_pline"), 1)
        text, datas, ctx = self._screen("se_pline")
        self.assertEqual(datas, ["se_pline~awg3", "se_mode"], "стоящую на 443 выбирать незачем")
        text, datas, ctx = self._screen("se_pline~awg3", ctx)
        self.assertEqual(bot.CONFIRM.pending(self.UID)["phrase"], "39001")      # порт профиля (D-5)
        self.assertIn(i18n.tf(self.UID, "pl_move_fmt", "AmneziaWG 3.1", "39001"), text)
        self.assertIn(i18n.tf(self.UID, "pl_prev_fmt", "AmneziaWG 2.0"), text)
        self.assertIn(i18n.t(self.UID, "pr_port_note"), text)
        self.assertEqual(datas, ["tc_cancel~se_mode"])
        self.assertEqual(self._calls("pro-line"), [], "до фразы мост не зовётся")
        rec = self._say("39001", ctx)
        self.assertIn("--action=pro-line\t--json\t--profile=awg3\t", self._calls("pro-line")[-1])
        self.assertNotIn("--adopted-confirm", self._calls("pro-line")[-1])
        replies = _replies(rec)
        self.assertEqual(replies[0][0], "⏳ " + i18n.tf(self.UID, "pl_moving_fmt", FX31))
        self.assertIn(i18n.tf(self.UID, "pl_done_fmt", "AmneziaWG 3.1"), replies[-1][0])
        self.assertEqual(_markup_datas(replies[-1][1]), ["se_mode"])

    def test_adopted_profile_needs_its_own_phrase(self):
        for variant, who in (("adopted", "awg2"), ("adopted_noname", "awg3")):
            self._env(FAKE_BOT_MODE="pro", FAKE_BOT_PRO_LINE=variant)
            _, _, ctx = self._screen("se_pline~awg3")
            rec = self._say("39001", ctx)
            reply = _replies(rec)[-1][0]
            self.assertIn(i18n.t(self.UID, "pl_adopted_note"), reply, variant)
            # подтверждают профиль, который назвало ядро (иначе выбранный), — его портом (D-5)
            pend = bot.CONFIRM.pending(self.UID)
            self.assertEqual(pend["target"], who, variant)
            self.assertRegex(pend["phrase"], r"^\d+$")
            self.assertIn("<code>%s</code>" % pend["phrase"], reply)
            rec = self._say(pend["phrase"], ctx)
            self.assertIn("--action=pro-line\t--json\t--profile=awg3\t--adopted-confirm=%s\t" % who,
                          self._calls("pro-line")[-1], variant)
            self.assertIn(i18n.tf(self.UID, "pl_done_fmt", "AmneziaWG 3.1"), _replies(rec)[-1][0], variant)

    def test_old_core_and_refusal(self):
        self._env(FAKE_BOT_MODE="pro", FAKE_BOT_PRO_LINE="unknown")
        _, _, ctx = self._screen("se_pline~awg3")
        text, kw = _replies(self._say("39001", ctx))[-1]
        self.assertEqual(text, i18n.tf(self.UID, "pl_unsupported_fmt", i18n.t(self.UID, "menu_protocols"),
                                       i18n.t(self.UID, "pr_take443")))
        self.assertEqual(_markup_datas(kw), ["sec_protocols", "se_mode"])
        os.environ["FAKE_BOT_PRO_LINE"] = "error"
        _, _, ctx = self._screen("se_pline~awg3")
        text, kw = _replies(self._say("39001", ctx))[-1]
        self.assertTrue(text.startswith("❌ "), text)
        self.assertEqual(_markup_datas(kw), ["se_pline", "se_mode"])

    def test_outside_pro_and_nothing_to_choose(self):
        self._env(FAKE_BOT_MODE="lite_site")
        text, datas, _ = self._screen("se_pline")
        self.assertIn(i18n.tf(self.UID, "pl_not_pro_fmt", i18n.t(self.UID, "se_mode_change"),
                              i18n.tf(self.UID, "se_mode_to_fmt", "Pro")), text)
        self.assertEqual(datas, ["se_mode_pick", "se_mode"])
        self._status("pro", {"awg2": 443})                   # одна линия, и она на 443 — выбирать нечего
        text, datas, _ = self._screen("se_mode")
        self.assertNotIn("se_pline", datas)
        text, datas, _ = self._screen("se_pline")
        self.assertIn(i18n.tf(self.UID, "pl_none_fmt", i18n.t(self.UID, "menu_protocols")), text)
        self.assertEqual(datas, ["sec_protocols", "se_mode"])

    def test_mode_and_port_disagree_with_several_lines(self):
        """Pro без линии на 443 и линий несколько: вместо отсылки в «Протоколы» — выбор линии (pro-line), и на экране
        режима, и быстрой кнопкой на главном."""
        self._status("pro", {"awg3": 4721, "awg2": 5388})
        text, datas, _ = self._screen("se_mode")
        self.assertIn(i18n.t(self.UID, "se_mode_port_hint"), text)
        self.assertEqual(datas.count("se_pline"), 1)
        rows = bot.get_main_menu(self.UID, run(bot.get_status()))
        self.assertIn("se_pline", [b.callback_data for row in rows.inline_keyboard for b in row])

    def test_phrase_during_a_bot_operation_is_queued(self):
        self._env(FAKE_BOT_MODE="pro")
        _, _, ctx = self._screen("se_pline~awg3")
        saved = bot._BOT_ACTION_LOCK
        bot._BOT_ACTION_LOCK = _HeldLock()
        self.addCleanup(setattr, bot, "_BOT_ACTION_LOCK", saved)
        replies = _replies(self._say("39001", ctx))
        self.assertEqual(replies[0][0], i18n.t(self.UID, "action_queued"))
        self.assertEqual(len(self._calls("pro-line")), 1)

    def test_contract_timeout_and_the_lazy_hint(self):
        self.assertEqual(bot.BRIDGE_TIMEOUT_BY_ACTION["pro-line"], 600)
        for lang in ("ru", "en"):
            self.assertIn(i18n.t_in(lang, "pl_btn"), i18n.t_in(lang, "lazy_why_20"), "Ленивый Pro отсылает к этой кнопке")


class CoreLazyCrossTests(unittest.TestCase):
    """Ответы НАСТОЯЩЕГО моста lazy-check и отказа lazy-setup (install.sh в сухом прогоне, стенды как у
    tests/test_lazy_dryrun.sh) бот рисует без незнакомых кодов: план — тот же, что у ядра, каждая причина — своими
    словами, by — целиком, линия на UDP 443 — только когда ядро её назвало. Заглушка моста в тестах выше отвечает по
    договору; здесь — что так отвечает и само ядро."""

    UID = 939393

    @classmethod
    def setUpClass(cls):
        # Экраны бота собираются клавиатурами python-telegram-bot — без библиотеки, как у остальных экранов, пропуск
        if not bot.PTB_AVAILABLE:
            raise unittest.SkipTest("python-telegram-bot не установлен")
        cls.bash = _bash5()
        if cls.bash is None:
            raise unittest.SkipTest("нужен bash 4+ (задайте BASH5)")
        if not shutil.which("jq"):
            raise unittest.SkipTest("нет jq — ядро без него не отвечает")
        cls.tmp = tempfile.mkdtemp(prefix="core-lazy-", dir=TMP_ROOT)
        # systemctl машины прогону не нужен: подставной отвечает отказом, как машина без systemd
        cls.hostbin = os.path.join(cls.tmp, "hostbin")
        os.makedirs(cls.hostbin)
        with open(os.path.join(cls.hostbin, "systemctl"), "w", encoding="utf-8") as fh:
            fh.write("#!/bin/sh\nexit 1\n")
        os.chmod(os.path.join(cls.hostbin, "systemctl"), 0o755)

    def _stand(self, **extra):
        """Чистый сервер во временном каталоге (как stand() в tests/test_lazy_dryrun.sh), DNS домена — сюда."""
        s = tempfile.mkdtemp(prefix="stand-", dir=self.tmp)
        for d in ("goamnezia", "run", "nginx", "site", "acme", "letsencrypt", "systemd", "shm", "build"):
            os.makedirs(os.path.join(s, d))
        with open(os.path.join(s, "goamnezia", ".language"), "w", encoding="utf-8") as fh:
            fh.write("ru\n")
        env = {k: v for k, v in os.environ.items() if not k.startswith(("GOAMNEZIA_", "FAKE_BOT_", "DOCKER_"))}
        env.update(PATH=self.hostbin + os.pathsep + os.environ.get("PATH", ""), GOAMNEZIA_DRY_RUN="1",
                   GOAMNEZIA_SERVER_IP="203.0.113.10", GOAMNEZIA_EDGE_IPS_STUB="", GOAMNEZIA_DIR=s + "/goamnezia",
                   GOAMNEZIA_LOCK_DIR=s + "/run", GOAMNEZIA_LOG_FILE=s + "/goamnezia.log",
                   GOAMNEZIA_AUDIT_LOG=s + "/audit.log", GOAMNEZIA_NOTIFY_DIR=s + "/run/notify",
                   GOAMNEZIA_BUILD_ROOT=s + "/build", GOAMNEZIA_DOCKER_SOCK=s + "/nosuch.sock",
                   GOAMNEZIA_SECRET_TMPDIR=s + "/shm", BOT_DIR=s + "/bot", ADMIN_DIR=s + "/admin", MCP_DIR=s + "/mcp",
                   NGINX_SITE_CONF=s + "/nginx/sites-available/goamnezia",
                   NGINX_SITE_LINK=s + "/nginx/sites-enabled/goamnezia", GOAMNEZIA_NGINX_DIR=s + "/nginx",
                   GOAMNEZIA_SITE_ROOT=s + "/site", GOAMNEZIA_ACME_ROOT=s + "/acme", GOAMNEZIA_LE_DIR=s + "/letsencrypt",
                   GOAMNEZIA_SYSTEMD_DIR=s + "/systemd", GOAMNEZIA_DNS_STUB="203.0.113.10",
                   GOAMNEZIA_LAZY_DNS_INTERVAL="1", GOAMNEZIA_LAZY_DNS_TIMEOUT="2")
        env.update(extra)
        return env

    def _act(self, env, action, *args):
        p = subprocess.run([self.bash, os.path.join(ROOT, "install.sh"), "--action=%s" % action, "--json"] + list(args),
                           env=env, capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
        r = bot.parse_bridge_output(p.stdout)
        self.assertIsNotNone(r, "%s: нет JSON — %s" % (action, (p.stderr or "")[-400:]))
        return r

    def _no_unknown_codes(self, text):
        for key in ("lazy_reason_unknown_fmt", "lazy_warn_unknown_fmt"):
            self.assertNotIn(i18n.tf(self.UID, key, "X").split("X")[0], text, "у ядра код, которого бот не знает")

    def test_check_screens_read_the_core_answer(self):
        fx = os.path.join(ROOT, "tests", "fixtures", "preflight")
        cases = (("pro", {}), ("blocked", {"GOAMNEZIA_DNS_STUB": "198.51.100.7"}),
                 ("blocked", {"GOAMNEZIA_DNS_STUB": ""}), ("blocked", {"GOAMNEZIA_DNS_STUB": "198.51.100.7 203.0.113.10"}),
                 ("pro", {"GOAMNEZIA_DNS_STUB": "104.16.132.229 172.67.1.2"}),
                 ("semi_pro", {"GOAMNEZIA_SS_FIXTURE_UDP": os.path.join(fx, "ss_udp_xray.txt")}),
                 ("blocked", {"GOAMNEZIA_SS_FIXTURE_TCP": os.path.join(fx, "ss_tcp_nginx.txt")}))
        for want_plan, extra in cases:
            r = self._act(self._stand(**extra), "lazy-check", "--domain=https://Example.com/path.")
            self.assertTrue(bot.bridge_ok(r), r)
            self.assertEqual(r.get("domain"), "example.com")
            text, markup, plan = bot.lazy_check_view(self.UID, r)
            self.assertEqual((plan, r.get("plan")), (want_plan, want_plan), extra)
            self._no_unknown_codes(text)
            for info in (r.get("ports") or {}).values():
                if isinstance(info, dict) and info.get("by"):
                    self.assertIn(bot.esc(info["by"]), text, "by — целиком")
            datas = [b.callback_data for row in markup.inline_keyboard for b in row]
            self.assertEqual("se_lazy_go" in datas, plan != "blocked", extra)
            line = r.get("udp443_line")
            if plan == "pro" and line:
                self.assertIn(bot.lazy_line443_note(self.UID, line), text)
            elif plan == "pro":
                self.assertNotIn(i18n.t(self.UID, "lazy_why_20"), text, "ядро не назвало линию — бот молчит")

    def test_setup_refusal_reads_the_core_answer(self):
        env = self._stand(GOAMNEZIA_DNS_STUB="198.51.100.7")
        r = self._act(env, "lazy-setup", "--domain=example.com", "--keys=1")
        self.assertEqual((r.get("status"), r.get("code"), r.get("plan")), ("error", "dns_not_here", "blocked"), r)
        text, markup = bot.lazy_refusal_view(self.UID, r)
        self.assertTrue(text.startswith("❌ "), text)
        self.assertIn(i18n.t(self.UID, "lazy_plan_blocked"), text)
        for code in r.get("reasons") or []:
            self.assertIn(i18n.t(self.UID, "lazy_code_%s" % code), text, code)
        self._no_unknown_codes(text)
        self.assertEqual([b.callback_data for row in markup.inline_keyboard for b in row],
                         ["se_lazy_re", "se_lazy", "se_mode_pick"])
        cfg = os.path.join(env["GOAMNEZIA_DIR"], "config.json")
        if os.path.exists(cfg):
            with open(cfg, encoding="utf-8") as fh:
                self.assertNotEqual(json.load(fh).get("installed"), True, "отказ — до любых изменений")

    def test_ports_held_by_us_and_keys_that_count(self):
        """LZ-05: на установленном сервере наши порты — «занят нами», а не «свободен»; план называет новых ключей
        столько, сколько ядро заведёт на самом деле: ключи прошлого запуска Ленивого Pro засчитываются."""
        fx = os.path.join(ROOT, "tests", "fixtures")
        upstream = os.path.join(self.tmp, "upstream-%d" % id(self))
        shutil.copytree(os.path.join(ROOT, "lib", "upstream"), upstream)
        env = self._stand(GOAMNEZIA_TEMPLATE_LOCAL_DIR=os.path.join(fx, "site_min"), UPSTREAM_DIR=upstream)
        r = self._act(env, "lazy-setup", "--domain=example.com", "--keys=3")
        self.assertTrue(bot.bridge_ok(r), r)
        ps = os.path.join(env["GOAMNEZIA_DIR"], "docker_ps.txt")
        with open(ps, "w", encoding="utf-8") as fh:
            fh.write("amnezia-awg2 0.0.0.0:443->443/udp, :::443->443/udp\n")
        env.update(GOAMNEZIA_SS_FIXTURE_TCP=os.path.join(fx, "preflight", "ss_tcp_nginx.txt"), GOAMNEZIA_DOCKER_PS_FIXTURE=ps)
        r = self._act(env, "lazy-check", "--domain=example.com")
        self.assertEqual((r.get("keys_have"), r.get("keys_new")), (3, 2), r)
        self.assertTrue(all(isinstance(i, dict) and i.get("ours") is True for i in (r.get("ports") or {}).values()), r.get("ports"))
        text, _, plan = bot.lazy_check_view(self.UID, r)
        self.assertEqual(plan, "pro")
        self._no_unknown_codes(text)
        for port in ("TCP 80", "TCP 443", "UDP 443"):
            self.assertIn(i18n.tf(self.UID, "lazy_port_ours_fmt", port), text)
            self.assertNotIn(i18n.tf(self.UID, "lazy_port_free_fmt", port), text)
        self.assertIn(i18n.tf(self.UID, "lazy_plan_pro_fmt", bot.mode_label(self.UID, "pro"), 2), text)
        self.assertIn(i18n.tf(self.UID, "lazy_keys_have_fmt", 3), text)
        # Подтверждение (шаг 3) берёт те же числа из запомненной проверки
        state = {"domain": "example.com", "display": "example.com", "plan": "pro", "reasons": [], "line443": "2.0",
                 "keys_new": 0, "keys_have": 5}
        text = bot.lazy_confirm_warn(self.UID, state, {"status": "error"})
        self.assertIn(i18n.tf(self.UID, "lazy_plan_pro_fmt", bot.mode_label(self.UID, "pro"), 0), text)
        self.assertIn(i18n.tf(self.UID, "lazy_keys_have_fmt", 5), text)
        # Ядро без keys_new — как раньше, сколько просим
        self.assertIn(i18n.tf(self.UID, "lazy_plan_pro_fmt", bot.mode_label(self.UID, "pro"), bot.LAZY_KEYS),
                      bot.lazy_confirm_warn(self.UID, {"domain": "example.com", "plan": "pro"}, {"status": "error"}))


# ── DUP-LINE-01 (решение владельца 15.10): две одинаковые линии, нет 2.0, «Переставить на 2.0» ────────────────────
# Сервер пользователя: amnezia-awg2 приложения Amnezia (3.1, порт 46567) подхвачен, а «Установить» awg3 ставило по месту
# ещё одну 3.1 (порт 6714) — две «AmneziaWG 3.1», ни одной 2.0. Ядро (faeea28) отдаёт lines_check и free_lines в status,
# profiles[].line_shared/imported и действие profile-reline; бот называет профили с портом, подписывает свободное место
# линией, которая встанет, предупреждает «нет 2.0» и ведёт к «Переставить … на 2.0» с фразой RELINE <профиль>.

DUP_RELINE_DONE = "AmneziaWG 3.1 (awg3 · UDP 6714) переставлен на AmneziaWG 2.0: порт 6714 и подсеть 10.8.3.0/24 прежние."
DUP_RELINE_STEPS = [{"kind": "users", "text": "Ключи на новом профиле выданы: vera."},
                    {"kind": "subscription", "text": "С подпиской (vera): обновится сама в течение 12 часов или по «Обновить» в приложении."},
                    {"kind": "key", "text": "Выдавали ключ или файл (vera): прежний больше не подключится — выдайте ключ vpn:// или файл .conf заново."}]


# Кому что сделать после перестановки — до неё (lines_check.reline.steps, ядро: ga_amn_reline_preview)
DUP_RELINE_PRE = [{"kind": "subscription", "text": "С подпиской (vera): обновится сама в течение 12 часов или по «Обновить» в приложении."},
                  {"kind": "page", "text": "Со страницей подключения (boris): открыть страницу заново и импортировать новый ключ."}]


def dup_status(reline=True, no20=True, awg3_imported=False, with_fields=True):
    """Две 3.1: awg2 подхвачен у приложения (46567), awg3 свой (6714); awg1 свободен — встанет 1.5."""
    profiles = [{"profile": "awg2", "container": "amnezia-awg2", "state": "running", "port": 46567, "line": "3.1",
                 "peers": 2, "line_shared": True, "imported": True, "title": "AmneziaWG 3.1 (awg2 · UDP 46567)"},
                {"profile": "awg3", "container": "amnezia-awg3", "state": "running", "port": 6714, "line": "3.1",
                 "peers": 1, "line_shared": True, "imported": awg3_imported, "title": "AmneziaWG 3.1 (awg3 · UDP 6714)"}]
    st = {"status": "success", "mode": "lite", "installed": True, "users": 3, "server_ip": "203.0.113.5",
          "version": "1.0.13", "profiles": profiles}
    if with_fields:
        st["lines_check"] = {"no_20": no20, "shared": [{"line": "3.1", "profiles": ["awg2", "awg3"]}], "missing": ["2.0"],
                             "reline": ({"profile": "awg3", "from": "3.1", "line": "2.0", "port": 6714,
                                         "title": "AmneziaWG 3.1 (awg3 · UDP 6714)", "steps": DUP_RELINE_PRE}
                                        if reline else None),
                             "text": "на языке ядра"}
        st["free_lines"] = {"awg1": "1.5"}
    else:
        for p in profiles:
            for k in ("line_shared", "imported", "title"):
                p.pop(k)
    return st


DUP_FIX = {"profile": "awg3", "from": "3.1", "line": "2.0", "port": 6714, "title": "AmneziaWG 3.1 (awg3 · UDP 6714)",
           "users": 2, "text": "на языке ядра", "detected_at": "2026-10-15T12:00:00Z"}


def dupfix_status(fix=None, reline=False):
    """После обновления: миграция нашла ошибку прошлых версий — status.dupline_fix (и lines_check тоже говорит «нет 2.0»)."""
    st = dup_status(reline=reline)
    st["dupline_fix"] = dict(DUP_FIX, **(fix or {}))
    return st


def free_status():
    """Подхвачена только 3.1 приложения в awg2; свободный awg3 по «Установить» станет 2.0 (free_lines)."""
    return {"status": "success", "mode": "lite", "installed": True, "users": 2, "server_ip": "203.0.113.5",
            "version": "1.0.13",
            "profiles": [{"profile": "awg2", "container": "amnezia-awg2", "state": "running", "port": 46567,
                          "line": "3.1", "peers": 2, "line_shared": False, "imported": True,
                          "title": "AmneziaWG 3.1 (awg2)"}],
            "lines_check": {"no_20": True, "shared": [], "missing": ["2.0"], "reline": None, "text": ""},
            "free_lines": {"awg3": "2.0", "awg1": "1.5"}}


def _keep_line_caches(case):
    """Кэши линий модуля — глобальные: вернуть как были после теста."""
    for name in ("_KNOWN_LINES", "_KNOWN_PORTS", "_KNOWN_IMPORTED", "_KNOWN_FREE"):
        if not isinstance(getattr(bot, name, None), dict):
            continue
        saved = dict(getattr(bot, name))
        case.addCleanup(lambda n=name, s=saved: (getattr(bot, n).clear(), getattr(bot, n).update(s)))


class DupLineWordsTests(unittest.TestCase):
    """DUP-LINE-01 без python-telegram-bot: подписи с портом, линия свободного места, строки главного экрана,
    защита от «переставить профиль приложения», итог, совет без 2.0 в подписке."""

    UID = 424245

    def setUp(self):
        _keep_line_caches(self)

    def test_profiles_are_named_by_line_and_port_without_slot(self):
        """Решение владельца 15.10: подпись — линия и порт всегда, подхваченный — «из приложения», без awg2/awg3."""
        st = dup_status()
        app, own = "AmneziaWG 3.1 · порт 46567 · из приложения", "AmneziaWG 3.1 · порт 6714"
        self.assertEqual(bot.profile_name(self.UID, "awg2", st), app)
        self.assertEqual(bot.profile_title(self.UID, "awg2", st), app)
        self.assertEqual(bot.profile_name(self.UID, "awg3", st), own)
        self.assertEqual(bot.profile_short("awg2", st, self.UID), "3.1 · 46567 · приложение")
        self.assertEqual(bot.profile_short("awg3", st, self.UID), "3.1 · 6714")
        for text in (app, own, bot.profile_short("awg2", st, self.UID)):
            self.assertNotIn("awg", text)
        self.assertEqual(i18n.t_in("en", "pr_label_fmt") % ("3.1", 6714), "AmneziaWG 3.1 · port 6714")
        self.assertEqual(i18n.t_in("en", "pr_label_app_fmt") % ("3.1", 46567), "AmneziaWG 3.1 · port 46567 · from the app")
        self.assertEqual(i18n.t_in("en", "pr_label_free_fmt") % "2.0", "AmneziaWG 2.0 — not installed")
        # Экраны без status — по последнему увиденному (кэш status обновляет remember_lines)
        bot.remember_lines(st)
        self.assertEqual(bot.profile_name(self.UID, "awg2"), app)
        self.assertEqual(bot.profile_short("awg3"), "3.1 · 6714")
        # Разные линии — порт всё равно в подписи; ядро старше полей imported/free_lines — без «из приложения»
        plain = dup_status(with_fields=False)
        plain["profiles"][1]["line"] = "2.0"
        self.assertEqual(bot.profile_name(self.UID, "awg3", plain), "AmneziaWG 2.0 · порт 6714")
        self.assertEqual(bot.profile_name(self.UID, "awg2", plain), "AmneziaWG 3.1 · порт 46567")
        # Порт неизвестен — одна линия
        self.assertEqual(bot.profile_name(self.UID, "awg2", {"status": "success", "profiles": [{"profile": "awg2", "line": "2.0"}]}),
                         "AmneziaWG 2.0")

    def test_user_card_names_the_port_once(self):
        st = dup_status()
        user = {"name": "vera", "enabled": True, "peers": {"awg3": {"ip": "10.8.3.2", "has_key": True}}}
        text = bot.user_card_text(self.UID, user, st)
        self.assertIn("<b>" + i18n.tf(self.UID, "user_peer_head_fmt", "AmneziaWG 3.1", 6714) + "</b>", text)
        self.assertEqual(text.count("6714"), 1)

    def test_free_slot_is_named_by_the_line_it_gets(self):
        st = free_status()
        free = "AmneziaWG 2.0 — не установлена"
        self.assertEqual(bot.profile_title(self.UID, "awg3", st), free)
        self.assertEqual(bot.profile_name(self.UID, "awg3", st), free)
        self.assertEqual(bot.profile_line_name(self.UID, "awg3", st), "AmneziaWG 2.0")
        bot.remember_lines(dup_status())          # awg3 был 3.1 на 6714…
        bot.remember_lines(st)                    # …и освободился: встанет 2.0
        self.assertEqual(bot.profile_name(self.UID, "awg3"), free)
        self.assertEqual(bot.profile_short("awg3"), "2.0")
        text = bot.profile_card_text(self.UID, "awg3", st, {})
        self.assertIn(free, text)

    def test_main_screen_warns_and_offers_the_move(self):
        lines, rows = bot.dashboard_problems(self.UID, dup_status(), buttons=False)
        self.assertIn("⚠️ " + i18n.t(self.UID, "lines_no20"), lines)
        self.assertEqual(i18n.t(self.UID, "lines_no20"),
                         "На сервере нет AmneziaWG 2.0: старые версии приложений и роутеры Keenetic на обычной прошивке не подключатся ни к "
                         "одному профилю (3.1 Keenetic понимает пока только в dev-прошивке).")
        self.assertIn("ℹ️ " + i18n.tf(self.UID, "lines_reline_fmt", "AmneziaWG 3.1 · порт 6714", "2.0"), lines)
        self.assertEqual(rows, [])
        self.assertIn(i18n.t(self.UID, "lines_no20"), bot.dashboard_text(self.UID, dup_status()))
        # Ядро старше поля, 2.0 есть — молчим
        self.assertEqual(bot.dashboard_problems(self.UID, dup_status(with_fields=False), buttons=False), ([], []))
        lines, _ = bot.dashboard_problems(self.UID, dup_status(reline=False, no20=False), buttons=False)
        self.assertEqual(lines, [])

    def test_app_profile_is_never_offered(self):
        self.assertEqual(bot.lines_check_of(dup_status())["reline"], {"profile": "awg3", "line": "2.0", "port": 6714})
        self.assertEqual(bot.lines_check_of(dup_status(awg3_imported=True))["reline"], {})
        for bad in ({"profile": "awg2", "line": "2.0"}, {"profile": "awg3", "line": "1.5"},
                    {"profile": "awg3", "line": "3.1"}, {"profile": "zzz", "line": "2.0"}, "junk"):
            st = dup_status()
            st["lines_check"]["reline"] = bad
            self.assertEqual(bot.lines_check_of(st)["reline"], {}, bad)
        self.assertEqual(bot.lines_check_of({"status": "error"}), {"no_20": False, "reline": {}})
        self.assertEqual(bot.confirm_phrase("profile-reline", "awg3"), "RELINE awg3")

    def test_reline_result_says_who_does_what(self):
        text = bot.reline_result_text(self.UID, {"status": "success", "summary": DUP_RELINE_DONE, "steps": DUP_RELINE_STEPS})
        self.assertEqual(text, "✅ " + DUP_RELINE_DONE + "\n\n" + "\n".join("• " + bot.esc(s["text"]) for s in DUP_RELINE_STEPS))

    def test_subscription_without_20_says_so(self):
        def sub(lines):
            return {"label": "vera", "update_interval": 12,
                    "urls": [{"format": "conf", "line": l, "url": "https://example.com/s-x/%s.conf" % i}
                             for i, l in enumerate(lines)]}
        note = i18n.t(self.UID, "sub_no20_note")
        self.assertIn(note, bot.sub_links_text(self.UID, sub(["3.1", "3.1"])))
        self.assertNotIn(note, bot.sub_links_text(self.UID, sub(["3.1", "2.0"])))
        self.assertNotIn(note, bot.sub_links_text(self.UID, sub(["3.1", "1.5"])))


class DupLineFixWordsTests(unittest.TestCase):
    """Сообщение после обновления (dupline_bug, status.dupline_fix): текст уведомления, кнопки «Исправить» и «Позже»,
    строка главного экрана вместо строк lines_check; профиль приложения не исправляется никогда."""

    UID = 424247

    def setUp(self):
        _keep_line_caches(self)

    def test_notification_without_users_does_not_promise_keys(self):
        """Пользователей на переставляемом профиле нет — уведомление не обещает «выдаст новые ключи (0)»."""
        ev = {"event": "dupline_bug", "profile": "awg3", "from": "3.1", "line": "2.0", "port": 6714, "users": 0}
        text = bot.format_notify_event(self.UID, ev)
        self.assertIn("Пользователей на нём нет.", text)
        self.assertNotIn("(0)", text)
        self.assertNotIn("выдаст новые ключи", text)

    def test_notification_says_what_and_who(self):
        ev = {"event": "dupline_bug", "profile": "awg3", "from": "3.1", "line": "2.0", "port": 6714,
              "title": "AmneziaWG 3.1 (awg3 · UDP 6714)", "users": 2, "text": "на языке ядра"}
        text = bot.format_notify_event(self.UID, ev)
        self.assertEqual(text, i18n.tf(self.UID, "ntf_dupline_fmt", "2.0", "3.1", "2.0", "AmneziaWG 3.1 · порт 6714", "2.0", 2))
        for part in ("Ошибка прошлых версий goAmnezia pro: вместо AmneziaWG 2.0 встала вторая 3.1",
                     "старые версии приложений (AmneziaVPN до 5.0.1.5, DefaultVPN) не подключатся к 3.1, а роутеры Keenetic на обычной "
                     "прошивке — только к AmneziaWG 1.5 или 1.0, если она стоит (3.1 Keenetic понимает пока только в dev-прошивке)",
                     "Исправление переставит AmneziaWG 3.1 · порт 6714 на 2.0: порт и подсеть останутся прежними.",
                     "Пользователям на нём (2) goAmnezia pro сразу выдаст новые ключи. Подписка подтянет их сама только там, где "
                     "приложение само её обновляет; страницу подключения нужно открыть заново и импортировать новый ключ, "
                     "а тем, кому ключ или файл отправляли вручную, — заново отсканировать QR-код или импортировать ключ: старый "
                     "перестанет работать.",
                     "Профиль приложения не тронем."):
            self.assertIn(part, text)
        self.assertNotIn("awg3", text)
        self.assertTrue(i18n.t_in("en", "ntf_dupline_fmt").startswith("A bug of earlier goAmnezia pro versions"))
        if bot.PTB_AVAILABLE:
            markup = bot.notify_keyboard(self.UID, ev)
            datas = [b.callback_data for row in markup.inline_keyboard for b in row]
            texts = [b.text for row in markup.inline_keyboard for b in row]
            self.assertEqual(datas, ["nt~pr_rl~awg3", "nt_later"])
            self.assertEqual(texts, [i18n.t(self.UID, "dupline_fix_btn"), i18n.t(self.UID, "dupline_later_btn")])
            self.assertIsNone(bot.notify_keyboard(self.UID, dict(ev, profile="zzz")))

    def test_main_screen_line_instead_of_lines_check(self):
        lines, _ = bot.dashboard_problems(self.UID, dupfix_status(), buttons=False)
        want = "⚠️ " + i18n.tf(self.UID, "dash_dupline_fmt", "2.0", "3.1", "AmneziaWG 3.1 · порт 6714", 2)
        self.assertEqual(lines, [want], "строки lines_check про то же не дублируются")
        self.assertIn(want, bot.dashboard_text(self.UID, dupfix_status()))
        self.assertEqual(bot.dupline_fix_of(dupfix_status()),
                         {"profile": "awg3", "line": "2.0", "from": "3.1", "port": 6714, "users": 2})
        # Профиль приложения, испорченная запись или линия уже такая — не исправляем, остаются строки lines_check
        for bad in ({"profile": "awg2"}, {"line": "1.5"}, {"line": "3.1"}, {"profile": "zzz"}):
            st = dupfix_status(bad, reline=True)
            self.assertEqual(bot.dupline_fix_of(st), {}, bad)
            lines, _ = bot.dashboard_problems(self.UID, st, buttons=False)
            self.assertIn("⚠️ " + i18n.t(self.UID, "lines_no20"), lines)
        st = dupfix_status()
        st["dupline_fix"] = None
        self.assertEqual(bot.dupline_fix_of(st), {})


class DupLineBotTests(_BotScreenCase):
    """DUP-LINE-01 на экранах: главный экран, «Протоколы», карточка профиля, согласие RELINE awg3 и итог, свободное место
    «Установить AmneziaWG 2.0», выбор линии на UDP 443 без 2.0."""

    UID = 424246

    def _fakes(self, st=None, reline_reply=None):
        self.calls = []
        cur = {"st": st or dup_status()}
        _keep_line_caches(self)

        async def fake_status(fresh=False):
            bot.remember_lines(cur["st"])
            return json.loads(json.dumps(cur["st"]))

        async def fake_mutate(action, who=None, timeout=None, **params):
            self.calls.append((action, {k: v for k, v in params.items() if v is not None}))
            if action == "profile-reline":
                if reline_reply is None:
                    # Перестановка удалась: ядро уже отвечает новой линией (awg3 — 2.0 на том же порту, переставлять нечего).
                    moved = json.loads(json.dumps(cur["st"]))
                    for p in moved["profiles"]:
                        if p["profile"] == "awg3":
                            p.update({"line": "2.0", "line_shared": False, "title": "AmneziaWG 2.0 (awg3 · UDP 6714)"})
                    moved["lines_check"] = {"no_20": False, "shared": [], "missing": [], "reline": None, "text": ""}
                    cur["st"] = moved
                return reline_reply or {"status": "success", "message": DUP_RELINE_DONE, "summary": DUP_RELINE_DONE,
                                        "profile": "awg3", "line_old": "3.1", "line": "2.0", "port": 6714,
                                        "steps": DUP_RELINE_STEPS}
            return {"status": "success", "message": "ok", "line": "2.0"}

        async def fake_users():
            # vera — на awg3 (после перестановки ключ у неё новый), masha — только профиль приложения (awg2)
            return {"status": "success"}, [{"name": "vera", "profiles": ["awg3"]}, {"name": "masha", "profiles": ["awg2"]}]

        for name, fake in (("get_status", fake_status), ("mutate", fake_mutate),
                           ("read_config_display", lambda: {"profiles": {}}), ("fetch_users", fake_users)):
            saved = getattr(bot, name)
            setattr(bot, name, fake)
            self.addCleanup(setattr, bot, name, saved)
        self.addCleanup(bot.CONFIRM.cancel, self.UID)

    @staticmethod
    def _labels(rec):
        edits = [c for c in rec.calls if c[0] == "edit"]
        return {b.callback_data: b.text for row in edits[-1][2]["reply_markup"].inline_keyboard for b in row}

    def test_main_screen_line_and_button_above_sections(self):
        self._fakes()
        text, datas, _ = self._screen("menu_main")
        self.assertIn(i18n.t(self.UID, "lines_no20"), text)
        self.assertIn("pr_rl~awg3", datas)
        self.assertLess(datas.index("pr_rl~awg3"), datas.index("menu_users"), "быстрое действие — над разделами")
        rec, _ = self._press("menu_main")
        self.assertEqual(self._labels(rec)["pr_rl~awg3"], i18n.tf(self.UID, "pr_rl_btn_fmt", "3.1 · 6714", "2.0"))
        self.assertIn("3.1 · 46567 · приложение", text, "главный экран различает две 3.1 по порту")

    def test_protocols_screen_names_with_ports_and_warns(self):
        self._fakes()
        rec, _ = self._press("sec_protocols")
        labels = self._labels(rec)
        self.assertIn("AmneziaWG 3.1 · порт 46567 · из приложения", labels["pr_card~awg2"])
        self.assertIn("AmneziaWG 3.1 · порт 6714", labels["pr_card~awg3"])
        self.assertEqual(labels["pr_card~awg3"].count("6714"), 1, "порт не дублируется")
        self.assertIn("AmneziaWG 1.5 — не установлена", labels["pr_card~awg1"])
        self.assertFalse([t for t in labels.values() if "awg" in t], labels)
        text, datas, _ = self._screen("sec_protocols")
        self.assertEqual(datas[0], "pr_rl~awg3")
        self.assertIn(i18n.t(self.UID, "lines_no20"), text)

    def test_card_of_own_profile_offers_the_move_first_app_profile_never(self):
        self._fakes()
        text, datas, _ = self._screen("pr_card~awg3")
        self.assertEqual(datas[0], "pr_rl~awg3")
        self.assertIn("AmneziaWG 3.1 · порт 6714", text)
        self.assertIn(i18n.tf(self.UID, "lines_reline_fmt", "AmneziaWG 3.1 · порт 6714", "2.0"), text)
        text, datas, _ = self._screen("pr_card~awg2")
        self.assertNotIn("pr_rl~awg2", datas)
        self.assertNotIn("pr_rl~awg3", datas)

    def test_move_asks_for_the_phrase_then_reports_who_does_what(self):
        self._fakes()
        rec, ctx = self._press("pr_rl~awg3")
        pend = bot.CONFIRM.pending(self.UID)
        # D-5/R-3: фраза — с портом профиля (его пользователь видит в подписи), а не с именем места awg3
        self.assertEqual((pend["kind"], pend["target"], pend["phrase"]), ("profile-reline", "awg3", "6714"))
        text = [str(c[1][0]) for c in rec.calls if c[0] == "edit"][-1]
        self.assertIn(i18n.tf(self.UID, "pr_rl_warn_fmt", "AmneziaWG 3.1 · порт 6714", "2.0", 6714, 1), text)
        self.assertIn("Профиль приложения Amnezia не трогаю", text)
        # у каждого пользователя его действие — до согласия (шаги ядра)
        self.assertIn(i18n.t(self.UID, "pr_rl_who") + "\n• С подпиской (vera): обновится сама в течение 12 часов или по "
                      "«Обновить» в приложении.\n• Со страницей подключения (boris): открыть страницу заново и "
                      "импортировать новый ключ.", text)
        # подтверждение — портом профиля, своими словами; у каждого пользователя его действие — до согласия
        self.assertIn(i18n.tf(self.UID, "confirm_type_port_fmt", "6714", bot.TYPED_CONFIRM_TTL), text)
        self.assertNotIn("awg3", text)
        self.assertEqual(self.calls, [], "без фразы — ничего")
        self._say("RELINE awg3", ctx)
        self.assertEqual(self.calls, [], "имя места больше не подтверждает")
        rec, ctx = self._press("pr_rl~awg3")
        rec = self._say("6714", ctx)
        # masha без ключа на awg3 — выбор «ключ остальным» отмечен по умолчанию и уходит ядру (DUPLINE-KEYS-01)
        self.assertEqual(self.calls, [("profile-reline", {"profile": "awg3", "line": "2.0", "keys_to_others": "on"})])
        replies = [str(c[1][0]) for c in rec.calls if c[0] == "reply_text"]
        self.assertIn(i18n.tf(self.UID, "pr_rl_running_fmt", "AmneziaWG 3.1 · порт 6714", "2.0"), replies[0])
        self.assertTrue(replies[-1].startswith(bot.reline_result_text(self.UID, {"message": DUP_RELINE_DONE, "steps": DUP_RELINE_STEPS})))
        self.assertIn("• Выдавали ключ или файл (vera)", replies[-1])

    def test_cancel_on_an_expired_new_question_after_an_accepted_phrase_cancels(self):
        """RB3-1: фразу X приняли, открыли новый вопрос Y и не набрали фразу — Y истёк. «Отмена» на Y — прежнее
        «Отменено» и возврат на прежний экран, а не «Фраза уже принята» без кнопок."""
        self._fakes()
        rec, ctx = self._press("pr_rl~awg3")
        self._say("6714", ctx)
        self.assertEqual(len(self.calls), 1, "фраза X принята")
        self._fakes()                                                # тот же профиль снова до перестановки
        self._press("pr_rl~awg3")                                    # вопрос Y — новое сообщение
        n_calls = len(self.calls)
        bot.CONFIRM.pending(self.UID)["until"] = 0                   # Y истёк по TTL
        rec2, _ = self._press("tc_cancel~pr_card~awg3")
        answers = [str(c[1][0]) for c in rec2.calls if c[0] == "answer"]
        self.assertIn(i18n.t(self.UID, "cancelled"), answers)
        self.assertNotIn(i18n.t(self.UID, "confirm_already_accepted"), answers)
        self.assertTrue([c for c in rec2.calls if c[0] == "edit"], "возврат на прежний экран")
        self.assertIsNone(bot.CONFIRM.pending(self.UID))
        self.assertEqual(len(self.calls), n_calls, "ничего не выполнено")

    def test_keys_to_the_others_are_chosen_before_the_phrase_and_issued_by_the_core(self):
        """DUPLINE-KEYS-01: у кого ключа на профиле нет (masha), — строкой на экране согласия и переключателем, отмечено по
        умолчанию; выдаёт ядро тем же действием (keys_to_others=on), итог — шагами ядра, без второго вопроса и без
        user-add-peer из бота. Сняли — ядру не передаётся. Старая кнопка pr_rlk~ ведёт в карточку профиля."""
        self._fakes()
        rec, ctx = self._press("pr_rl~awg3")
        text = [str(c[1][0]) for c in rec.calls if c[0] == "edit"][-1]
        self.assertIn(i18n.tf(self.UID, "pr_rl_others_on_fmt", "masha", "2.0"), text)
        labels = self._labels(rec)
        self.assertEqual(list(labels), ["pr_rl~awg3~off", "tc_cancel~pr_card~awg3"])
        self.assertEqual(labels["pr_rl~awg3~off"], i18n.t(self.UID, "pr_rl_keys_on_btn"))
        self.assertEqual(bot.CONFIRM.pending(self.UID)["extra"], {"line": "2.0", "keys": "on"})
        markups = []

        class _Api:
            async def edit_message_reply_markup(self, **kw):
                markups.append(kw)
        ctx.bot = _Api()
        rec = self._say("6714", ctx)
        self.assertEqual(self.calls, [("profile-reline", {"profile": "awg3", "line": "2.0", "keys_to_others": "on"})])
        # BL-1: фраза принята — с вопроса сняты «Выдать ключ остальным» и «Отмена»
        self.assertEqual(len(markups), 1, markups)
        self.assertIsNone(markups[0]["reply_markup"])
        self.assertEqual(markups[0]["chat_id"], self.UID)
        self.assertIsNotNone(markups[0]["message_id"])
        # RB-2: «Отмена», нажатая после принятой фразы (сообщение не поправилось), не говорит «Отменено» и никуда не ведёт
        n_calls = len(self.calls)
        upd = FakeUpdate(self.UID, data="tc_cancel~pr_card~awg3")
        upd.callback_query.message.message_id = markups[0]["message_id"]   # «Отмена» на том самом вопросе
        run(bot.handle_callback(upd, FakeContext()))
        rec2 = upd.rec
        answers = [c for c in rec2.calls if c[0] == "answer"]
        self.assertEqual(str(answers[0][1][0]), i18n.t(self.UID, "confirm_already_accepted"))
        self.assertNotIn(i18n.t(self.UID, "cancelled"), [str(c[1][0]) for c in answers])
        self.assertEqual([c for c in rec2.calls if c[0] == "edit_markup"][0][1][0], None)
        self.assertFalse([c for c in rec2.calls if c[0] == "edit"], "на прежний экран не уводит")
        self.assertEqual(len(self.calls), n_calls)
        replies = [c for c in rec.calls if c[0] == "reply_text"]
        last = str(replies[-1][1][0])
        self.assertNotIn("Выдать им ключ?", last)
        datas = [b.callback_data for row in replies[-1][2]["reply_markup"].inline_keyboard for b in row]
        self.assertNotIn("pr_rlk~awg3", datas)
        self.assertFalse([c for c in self.calls if c[0] == "user-add-peer"])
        # сняли выбор — не выдаём: строка и кнопка другие, ядру — без keys_to_others
        self._fakes()
        rec, ctx = self._press("pr_rl~awg3~off")
        text = [str(c[1][0]) for c in rec.calls if c[0] == "edit"][-1]
        self.assertIn(i18n.tf(self.UID, "pr_rl_others_off_fmt", "masha", "2.0"), text)
        self.assertEqual(self._labels(rec)["pr_rl~awg3"], i18n.t(self.UID, "pr_rl_keys_off_btn"))
        self.assertEqual(bot.CONFIRM.pending(self.UID)["extra"], {"line": "2.0", "keys": "off"})
        self._say("6714", ctx)
        self.assertEqual(self.calls, [("profile-reline", {"profile": "awg3", "line": "2.0"})])
        # ключа нет ни у кого — «всем», числом
        self.assertEqual(bot.reline_others_rows(self.UID, "awg3", "2.0", ["a", "b"], False, True)[0],
                         i18n.tf(self.UID, "pr_rl_all_on_fmt", 2, "2.0"))
        self.assertEqual(bot.reline_others_rows(self.UID, "awg3", "2.0", [], True, True), ("", []))
        # кнопка прежних версий — в карточку профиля, без выдачи
        self._fakes()
        rec, _ = self._press("pr_rlk~awg3")
        self.assertEqual(self.calls, [])
        # R-2: нажатие получает ответ — иначе у кнопки «часики» до таймаута Telegram
        self.assertIn("answer", [c[0] for c in rec.calls])
        self.assertIn("pr_rl~awg3", self._labels(rec))

    def test_core_version_of_a_20_profile_says_it_serves_20(self):
        """LH-8: у профиля 2.0 на сборке ядра 3.x рядом с «Версия ядра» — что сборка обслуживает и 2.0; у 3.1 — без этого."""
        st = {"profiles": [{"profile": "awg3", "line": "2.0", "port": 6714, "image_tag": "3.1.20260828", "state": "running"},
                           {"profile": "awg2", "line": "3.1", "port": 443, "image_tag": "3.1.20260828", "state": "running"}]}
        both = i18n.t(self.UID, "pr_core_ver_both20")
        self.assertIn("<code>3.1.20260828</code> " + both, bot.profile_card_text(self.UID, "awg3", st, {"profiles": {}}))
        self.assertNotIn(both, bot.profile_card_text(self.UID, "awg2", st, {"profiles": {}}))

    def test_keys_to_the_others_stay_offered_when_the_user_list_is_unreadable(self):
        """R-7: список пользователей не прочитался — выбор «ключ остальным» не пропадает молча: строка без имён, отмечено
        по умолчанию, ядру — keys_to_others=on (кому выдать, оно считает само); снять — тоже можно."""
        self._fakes()

        async def broken_users():
            return {"status": "error", "message": "boom", "code": "bridge"}, []

        saved = bot.fetch_users
        bot.fetch_users = broken_users
        self.addCleanup(setattr, bot, "fetch_users", saved)
        rec, ctx = self._press("pr_rl~awg3")
        text = [str(c[1][0]) for c in rec.calls if c[0] == "edit"][-1]
        self.assertIn(i18n.tf(self.UID, "pr_rl_others_unknown_on_fmt", "2.0"), text)
        self.assertEqual(self._labels(rec)["pr_rl~awg3~off"], i18n.t(self.UID, "pr_rl_keys_on_btn"))
        self.assertEqual(bot.CONFIRM.pending(self.UID)["extra"], {"line": "2.0", "keys": "on"})
        self._say("6714", ctx)
        self.assertEqual(self.calls, [("profile-reline", {"profile": "awg3", "line": "2.0", "keys_to_others": "on"})])
        self._fakes()
        bot.fetch_users = broken_users
        rec, ctx = self._press("pr_rl~awg3~off")
        text = [str(c[1][0]) for c in rec.calls if c[0] == "edit"][-1]
        self.assertIn(i18n.tf(self.UID, "pr_rl_others_unknown_off_fmt", "2.0"), text)
        self.assertEqual(bot.CONFIRM.pending(self.UID)["extra"], {"line": "2.0", "keys": "off"})

    def test_remove_names_the_connection_pages_that_stop_working(self):
        """PROFILE-REMOVE-PAGES: «Удалить» — до фразы: чьи страницы подключения перестанут работать (имена из ядра,
        profile-remove-preview); одна — в единственном числе; нет таких или ядро не ответило — строки нет. Само удаление
        прежнее: profile-remove после фразы."""
        self._fakes()
        seen = []

        def preview(pages, users, ok=True):
            async def fake(action, timeout=None, **params):
                seen.append((action, params))
                if not ok:
                    return {"status": "error", "code": "unknown_action", "message": "x"}
                return {"status": "success", "profile": params.get("profile"), "pages": pages, "page_users": users, "text": "слова ядра"}
            return fake

        saved = bot.run_bot_action
        self.addCleanup(setattr, bot, "run_bot_action", saved)
        bot.run_bot_action = preview(2, ["anna", "boris"])
        rec, ctx = self._press("pr_remove~awg3")
        text = [str(c[1][0]) for c in rec.calls if c[0] == "edit"][-1]
        self.assertIn(i18n.tf(self.UID, "pr_remove_pages_fmt", "anna, boris"), text)
        self.assertIn("перестанут работать: в них был только этот профиль", text)
        self.assertEqual(seen, [("profile-remove-preview", {"profile": "awg3"})])
        self._say("6714", ctx)
        self.assertEqual(self.calls, [("profile-remove", {"profile": "awg3"})])
        bot.run_bot_action = preview(1, ["boris"])
        text, _, _ = self._screen("pr_remove~awg3")
        self.assertIn(i18n.tf(self.UID, "pr_remove_page1_fmt", "boris"), text)
        for fake in (preview(0, []), preview(2, ["anna"], ok=False)):
            bot.run_bot_action = fake
            text, _, _ = self._screen("pr_remove~awg3")
            self.assertNotIn("Страниц", text)
        self.assertTrue(i18n.t_in("en", "pr_remove_pages_fmt").startswith("⚠️ The connection pages of"))

    def test_refusal_is_told_in_core_words(self):
        msg = "AmneziaWG 3.1 (awg3) с линией 2.0 не встал (image) — вернули прежнюю линию 3.1 на тот же порт и подсеть."
        self._fakes(reline_reply={"status": "error", "code": "reline_rolled_back", "message": msg})
        _, ctx = self._press("pr_rl~awg3")
        rec = self._say("6714", ctx)
        replies = [str(c[1][0]) for c in rec.calls if c[0] == "reply_text"]
        self.assertIn(bot.esc(msg), replies[-1])
        self.assertFalse(replies[-1].startswith("✅"))

    def test_nothing_to_move_says_so(self):
        self._fakes(st=dup_status(reline=False))
        text, datas, _ = self._screen("pr_rl~awg3")
        self.assertIn(i18n.t(self.UID, "pr_rl_none"), text)
        self.assertEqual(datas, ["pr_card~awg3"])
        self.assertIsNone(bot.CONFIRM.pending(self.UID))
        self._fakes(st=dup_status(awg3_imported=True))
        text, _, _ = self._screen("pr_rl~awg3")
        self.assertIn(i18n.t(self.UID, "pr_rl_none"), text, "профиль приложения не переставляется никогда")
        self.assertIsNone(bot.CONFIRM.pending(self.UID))

    def test_free_slot_install_names_the_line_and_sends_no_line(self):
        self._fakes(st=free_status())
        rec, _ = self._press("pr_card~awg3")
        labels = self._labels(rec)
        self.assertEqual(labels["pr_install~awg3"], i18n.tf(self.UID, "pr_install_line_fmt", "2.0"))
        self.assertEqual(labels["pr_install~awg3"], "📦 Установить AmneziaWG 2.0")
        text, _, _ = self._screen("pr_install~awg3")
        self.assertIn(i18n.tf(self.UID, "pr_install_title_fmt", "AmneziaWG 2.0"), text)
        self._screen("pr_inst_do~awg3~def")
        self.assertEqual(self.calls, [("profile-install", {"profile": "awg3", "preset": "default"})],
                         "линию выбирает ядро (недостающую), бот её не передаёт")

    def test_app_holds_awg2_with_31_the_user_scenario(self):
        """Главный сценарий: подхвачен amnezia-awg2 приложения с 3.1, своего awg3 нет. Свободный awg3 — «AmneziaWG 2.0
        (awg3)» и в «Протоколах», и на экране первой установки; «Установить» оттуда ставит профиль на awg3 (линию — ядро,
        без line), а не «2.0 в awg2», который занят. Нигде не «2.0 занята приложением» — занято только место awg2."""
        self._fakes(st=free_status())
        rec, _ = self._press("sec_protocols")
        labels = self._labels(rec)
        self.assertIn("AmneziaWG 2.0 — не установлена", labels["pr_card~awg3"])
        self.assertIn("AmneziaWG 3.1 · порт 46567 · из приложения", labels["pr_card~awg2"])
        text, datas, _ = self._screen("menu_install")
        self.assertIn(i18n.tf(self.UID, "install_body_fmt", "AmneziaWG 2.0"), text)
        self.assertEqual(datas[:2], ["inst_awg2~def~awg3", "inst_awg2~443~awg3"])
        rec, _ = self._press("menu_install")
        self.assertEqual(self._labels(rec)["inst_awg2~443~awg3"], i18n.tf(self.UID, "install_btn_443_fmt", "2.0"))
        self._screen("inst_awg2~def~awg3")
        self.assertEqual(self.calls, [("profile-install", {"profile": "awg3", "preset": "default"})])
        self.assertEqual(bot.first_install_slot({"status": "success", "profiles": []}), "awg2")
        self.assertEqual(bot.first_install_slot(dup_status()), "awg2", "на свободный awg1 встанет 1.5, а не 2.0 — как раньше")
        self.assertEqual(bot.first_install_slot({"status": "error"}), "awg2")
        for lang in ("ru", "en"):
            busy = i18n.t_in(lang, "lazy_code_awg2_container_busy")
            self.assertIn("awg2", busy)
            self.assertNotIn("2.0", busy, "занято место awg2, а не линия 2.0")

    def test_dupline_fix_button_on_main_screen_and_from_the_notice(self):
        """«Исправить» — с главного экрана и из уведомления: тот же экран согласия и фраза RELINE awg3, даже когда
        lines_check.reline пуст (о профиле знает только dupline_fix); «Позже» убирает кнопки, ничего не вызывая."""
        self._fakes(st=dupfix_status())
        text, datas, _ = self._screen("menu_main")
        self.assertIn(i18n.tf(self.UID, "dash_dupline_fmt", "2.0", "3.1", "AmneziaWG 3.1 · порт 6714", 2), text)
        self.assertNotIn(i18n.t(self.UID, "lines_no20"), text)
        self.assertIn("pr_rl~awg3", datas)
        self.assertLess(datas.index("pr_rl~awg3"), datas.index("menu_users"))
        rec, _ = self._press("menu_main")
        self.assertEqual(self._labels(rec)["pr_rl~awg3"], i18n.tf(self.UID, "dash_dupline_btn_fmt", "2.0", "3.1"))
        rec, ctx = self._press("nt~pr_rl~awg3")
        self.assertEqual(bot.CONFIRM.pending(self.UID)["phrase"], "6714")
        self.assertTrue(rec.count("reply_text"), "экран — новым сообщением, уведомление остаётся")
        self._say("6714", ctx)
        self.assertEqual(self.calls, [("profile-reline", {"profile": "awg3", "line": "2.0", "keys_to_others": "on"})])
        self.calls.clear()
        rec, _ = self._press("nt_later")
        marks = [c for c in rec.calls if c[0] == "edit_markup"]
        self.assertEqual(len(marks), 1)
        self.assertIsNone(marks[0][1][0])
        self.assertFalse([c for c in rec.calls if c[0] in ("edit", "reply_text")])
        self.assertEqual(self.calls, [])

    def test_udp443_choice_without_20_is_honest(self):
        self._fakes()
        text, markup = bot.pro_line_picker(self.UID, dup_status(), "pl_set~", "se_mode", False)
        datas = [b.callback_data for row in markup.inline_keyboard for b in row]
        self.assertNotIn(i18n.tf(self.UID, "pl_why_20_fmt", i18n.t(self.UID, "pl_btn")), text)
        self.assertIn(i18n.t(self.UID, "pl_no20"), text)
        self.assertIn("pr_rl~awg3", datas)
        # Две 3.1 на выбор — различимы по порту
        labels = {b.callback_data: b.text for row in markup.inline_keyboard for b in row}
        self.assertEqual(labels["pl_set~awg2"], i18n.tf(self.UID, "pl_btn_fmt", "3.1 · 46567 · приложение"))
        self.assertEqual(labels["pl_set~awg3"], i18n.tf(self.UID, "pl_btn_fmt", "3.1 · 6714"))
        text, markup = bot.pro_line_picker(self.UID, free_status(), "pl_set~", "se_mode", False)
        datas = [b.callback_data for row in markup.inline_keyboard for b in row]
        self.assertIn(i18n.t(self.UID, "pl_no20"), text)
        self.assertIn("pr_install~awg3", datas)
        st = dup_status(with_fields=False)
        st["profiles"][1]["line"] = "2.0"
        text, _ = bot.pro_line_picker(self.UID, st, "pl_set~", "se_mode", False)
        self.assertIn(i18n.tf(self.UID, "pl_why_20_fmt", i18n.t(self.UID, "pl_btn")), text)
        self.assertNotIn(i18n.t(self.UID, "pl_no20"), text)


class KeyAppOnlyTests(_BotScreenCase):
    """KEY-APP-02 / KEY-APP-01 в боте: у человека все ключи только в приложении Amnezia (has_key: false) — в карточке
    нет QR, .conf, vpn:// и ссылки (они ведут в no_private_key), есть «Привязать старый ключ» и «Перевыпустить» и строка,
    почему остального нет; отказ ядра на кнопке, нарисованной раньше, печатается один раз."""

    def test_all_keys_in_the_app(self):
        user = {"name": "olga", "enabled": True,
                "peers": {"awg2": {"ip": "10.8.2.5", "has_key": False}, "awg3": {"ip": "10.8.3.5", "has_key": False}}}
        datas = [b.callback_data for row in bot.user_card_keyboard(None, user, key_addr=True).inline_keyboard for b in row]
        self.assertFalse([d for d in datas if d.startswith(("u_key~", "u_share~", "u_kaddr~"))], datas)
        for p in ("awg2", "awg3"):
            self.assertIn("u_att~olga~%s" % p, datas)
            self.assertIn("u_reis~olga~%s" % p, datas)
        st = {"status": "success", "profiles": []}
        text = bot.user_card_text(None, user, st)
        for p in ("awg2", "awg3"):
            self.assertEqual(text.count(i18n.tf(None, "user_peer_imported_note_fmt", bot.profile_name(None, p, st))), 1)

    def test_refusal_is_printed_once(self):
        rec, _ = self._press("u_key~proba~awg2~conf")
        replies = [str(c[1][0]) for c in rec.calls if c[0] == "reply_text"]
        self.assertEqual(len(replies), 1, replies)
        self.assertEqual(replies[0].count("выдан в приложении Amnezia"), 1, replies[0])


if __name__ == "__main__":
    unittest.main()
