# -*- coding: utf-8 -*-
"""Общий снимок частых чтений панели (CPU-аудит 03.10.2026): один сбор на всех, сроки, сброс, гонки.

Мост подменён функцией (server._bridge_exec): она считает вызовы и держит каждый на своей «заслонке» — гонки
воспроизводятся барьерами, а не задержками. Ждущих чужой сбор считаем по их ожиданию на событии сбора
(_Flight.done), поэтому «трое пришли разом» — это ровно трое в одной точке, а не «наверное, успели».
Часы — server._now (подменные), файлы ядра — во временном каталоге.

Запуск: python3 -m unittest tests/test_admin_snapshot.py
"""
import datetime
import importlib.util
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(ROOT, "admin-web", "server.py")

TMP_ROOT = tempfile.mkdtemp(prefix="goamnezia-admin-snap-")
os.environ["GOAMNEZIA_DIR"] = os.path.join(TMP_ROOT, "goamnezia")
os.environ["GOAMNEZIA_ADMIN_DIR"] = os.path.join(TMP_ROOT, "admin")
os.environ["GOAMNEZIA_BOT_DIR"] = os.path.join(TMP_ROOT, "bot")
os.environ["GOAMNEZIA_LOCK_DIR"] = os.path.join(TMP_ROOT, "run")
os.environ["GOAMNEZIA_ACTION_BIN"] = os.path.join(ROOT, "tests", "fixtures", "fake_action_admin.sh")
os.environ["GOAMNEZIA_ACTIONS_JSON"] = os.path.join(ROOT, "lib", "actions.json")
os.environ["GOAMNEZIA_AUDIT_LOG"] = os.path.join(TMP_ROOT, "audit.log")
os.environ["GOAMNEZIA_ADMIN_PORT"] = "1985"
os.environ["GOAMNEZIA_TRUST_TUNNEL"] = "1"
for d in ("goamnezia", "admin", "bot", "run", os.path.join("run", "share")):
    os.makedirs(os.path.join(TMP_ROOT, d), exist_ok=True)

_spec = importlib.util.spec_from_file_location("ga_admin_server_snapshot", SERVER)
server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(server)  # type: ignore[union-attr]


def tearDownModule():
    shutil.rmtree(TMP_ROOT, ignore_errors=True)


def core_file(name):
    """Файл ядра так, как его видит этот экземпляр панели: в общем прогоне модули тестов ставят GOAMNEZIA_DIR каждый
    свой, и os.environ к началу тестов указывает на каталог последнего загруженного модуля, а не этого."""
    return str({"config.json": server.CONFIG_FILE, "users.json": server.USERS_FILE, "stats.json": server.STATS_FILE}[name])


def write_json(path, obj):
    """Атомарная замена, как у ядра: новый inode на месте прежнего файла."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh)
    os.replace(tmp, path)


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class FakeBridge:
    """Мост-функция: ответы по имени действия, счёт вызовов и заслонка на каждый вызов (по умолчанию открыта)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.calls = []
        self.gates = []          # заслонки по порядку вызовов чтения; кончились — вызов идёт сразу
        self.entered = threading.Semaphore(0)
        self.answers = {}
        self.raise_on = set()

    def __call__(self, name, argv, env, timeout, stdin_data, secret_in):
        with self.lock:
            self.calls.append((name, env.get("GOAMNEZIA_CALL_LANG", ""), tuple(argv[3:])))
            gate = self.gates.pop(0) if self.gates and name in READS else None
        # Ответ — каким он был на момент вызова (сбор «видит» состояние на своём старте), потом заслонка.
        ans = self.answers.get(name)
        if callable(ans):
            ans = ans()
        if ans is None:
            ans = {"status": "success", "message": "ok", "action": name}
        self.entered.release()
        if gate is not None and not gate.wait(20):
            raise AssertionError("заслонка не открылась")
        if name in self.raise_on:
            raise RuntimeError("мост упал")
        return dict(ans), True

    def count(self, name):
        with self.lock:
            return sum(1 for c in self.calls if c[0] == name)


READS = set(server.READ_CACHE_TTL)


class SnapshotCase(unittest.TestCase):
    def setUp(self):
        server._cache_clear()
        self.bridge = FakeBridge()
        self._exec, self._now, self._flight = server._bridge_exec, server._now, server._Flight
        server._bridge_exec = self.bridge
        self.clock = Clock()
        server._now = self.clock
        # Ждущие чужой сбор — через счётчик: ждать на событии сбора значит «пришёл и присоединился».
        self.cv = threading.Condition()
        self.waiting = 0
        case = self

        class ObservedEvent(threading.Event):
            def wait(self, timeout=None):
                with case.cv:
                    case.waiting += 1
                    case.cv.notify_all()
                return super().wait(timeout)

        class ObservedFlight(self._flight):
            def __init__(self, at):
                super().__init__(at)
                self.done = ObservedEvent()

        server._Flight = ObservedFlight
        server._CALL_CTX.lang = ""
        server._CALL_CTX.fresh = False
        server._CALL_CTX.reads = {}
        for f in ("config.json", "users.json", "stats.json"):
            p = core_file(f)
            if os.path.exists(p):
                os.unlink(p)

    def tearDown(self):
        server._bridge_exec, server._now, server._Flight = self._exec, self._now, self._flight
        server._cache_clear()

    # Чтение в своём потоке, с языком и «свежестью» этого потока (как у запроса панели).
    def reader(self, name, out, idx, lang="", fresh=False, params=None):
        def run():
            server._CALL_CTX.lang = lang
            server._CALL_CTX.fresh = fresh
            server._CALL_CTX.reads = {}
            try:
                out[idx] = server.run_action(name, params or {}, "tester")
            except Exception as exc:  # noqa: BLE001 — исключение ведущего проверяем отдельно
                out[idx] = exc
        th = threading.Thread(target=run, daemon=True)
        th.start()
        return th

    def wait_waiting(self, n):
        with self.cv:
            self.assertTrue(self.cv.wait_for(lambda: self.waiting >= n, timeout=20), "ждущих меньше %d" % n)

    def wait_entered(self, n=1):
        for _ in range(n):
            self.assertTrue(self.bridge.entered.acquire(timeout=20), "мост не вызван")


class SingleFlightTests(SnapshotCase):
    def test_three_identical_reads_share_one_bridge_call(self):
        gate = threading.Event()
        self.bridge.gates = [gate]
        out = {}
        t1 = self.reader("traffic", out, 1)
        self.wait_entered()
        t2, t3 = self.reader("traffic", out, 2), self.reader("traffic", out, 3)
        self.wait_waiting(2)
        gate.set()
        for th in (t1, t2, t3):
            th.join(20)
        self.assertEqual(self.bridge.count("traffic"), 1)
        self.assertIs(out[1], out[2])
        self.assertIs(out[1], out[3])
        self.assertEqual(server._READ_FLIGHTS, {})
        # И следующий читатель в срок снимка — без моста.
        self.assertIs(server.run_action("traffic", {}, "tester"), out[1])
        self.assertEqual(self.bridge.count("traffic"), 1)

    def test_languages_do_not_share_a_collection(self):
        gate = threading.Event()
        self.bridge.gates = [gate, gate]
        self.bridge.answers["status"] = lambda: {"status": "success", "message": "слово на языке вызова"}
        out = {}
        t1 = self.reader("status", out, 1, lang="ru")
        t2 = self.reader("status", out, 2, lang="en")
        self.wait_entered(2)
        gate.set()
        t1.join(20)
        t2.join(20)
        self.assertEqual(self.bridge.count("status"), 2)
        self.assertEqual(sorted(c[1] for c in self.bridge.calls), ["en", "ru"])
        self.assertIsNot(out[1], out[2])

    def test_params_are_part_of_the_key(self):
        server.run_action("status", {"brief": "on"}, "tester")
        server.run_action("status", {}, "tester")
        server.run_action("status", {"brief": "on"}, "tester")
        self.assertEqual(self.bridge.count("status"), 2)

    def test_failed_collection_releases_every_waiter_and_is_not_cached(self):
        gate = threading.Event()
        self.bridge.gates = [gate]
        self.bridge.answers["traffic"] = {"status": "error", "code": "transport", "message": ""}
        out = {}
        t1 = self.reader("traffic", out, 1)
        self.wait_entered()
        t2, t3 = self.reader("traffic", out, 2), self.reader("traffic", out, 3)
        self.wait_waiting(2)
        gate.set()
        for th in (t1, t2, t3):
            th.join(20)
            self.assertFalse(th.is_alive())
        for i in (1, 2, 3):
            self.assertEqual(out[i]["code"], "transport")
        self.assertEqual(server._READ_FLIGHTS, {})
        self.bridge.answers.pop("traffic")
        self.assertEqual(server.run_action("traffic", {}, "tester")["status"], "success")
        self.assertEqual(self.bridge.count("traffic"), 2)

    def test_exception_in_collection_releases_waiters(self):
        gate = threading.Event()
        self.bridge.gates = [gate]
        self.bridge.raise_on.add("traffic")
        out = {}
        t1 = self.reader("traffic", out, 1)
        self.wait_entered()
        t2 = self.reader("traffic", out, 2)
        self.wait_waiting(1)
        gate.set()
        t1.join(20)
        t2.join(20)
        self.assertIsInstance(out[1], RuntimeError)   # ведущий видит своё исключение (маршрут ответит 500)
        self.assertEqual(out[2]["code"], "transport")  # ждущий — честный отказ, а не вечное ожидание
        self.assertEqual(server._READ_FLIGHTS, {})
        self.bridge.raise_on.clear()
        self.assertEqual(server.run_action("traffic", {}, "tester")["status"], "success")

    def test_waiter_gives_up_after_the_bridge_deadline(self):
        gate = threading.Event()
        self.bridge.gates = [gate]
        grace = server.READ_FLIGHT_GRACE
        server.READ_FLIGHT_GRACE = 0.0
        try:
            out = {}
            t1 = self.reader("traffic", out, 1)
            self.wait_entered()
            done = {}

            def follower():
                server._CALL_CTX.lang = ""
                server._CALL_CTX.fresh = False
                # Ключ — как у run_action: запрос без языка живёт на снимках языка установки (PERF-08).
                key = server._snapshot_key("traffic", server.snapshot_lang(), {})
                done["r"] = server._shared_read("traffic", key, lambda: None, 0.05)
            th = threading.Thread(target=follower, daemon=True)
            th.start()
            th.join(20)
            self.assertEqual(done["r"]["code"], "timeout")
        finally:
            server.READ_FLIGHT_GRACE = grace
            gate.set()
            t1.join(20)
        self.assertEqual(server._READ_FLIGHTS, {})

    def test_flight_table_is_bounded(self):
        gate = threading.Event()
        self.bridge.gates = [gate, gate]
        cap = server._READ_FLIGHTS_MAX
        server._READ_FLIGHTS_MAX = 1
        try:
            out = {}
            t1 = self.reader("traffic", out, 1)
            t2 = self.reader("user-list", out, 2)
            self.wait_entered(2)
            self.assertLessEqual(len(server._READ_FLIGHTS), 1)   # второй — мимо таблицы, сам по себе
            gate.set()
            t1.join(20)
            t2.join(20)
        finally:
            server._READ_FLIGHTS_MAX = cap
        self.assertEqual(out[1]["status"], "success")
        self.assertEqual(out[2]["status"], "success")
        self.assertEqual(server._READ_FLIGHTS, {})

    def test_reads_are_not_serialised_behind_a_running_mutation(self):
        server._ACTION_LOCK.acquire()
        try:
            self.assertEqual(server.run_action("traffic", {}, "tester")["status"], "success")
        finally:
            server._ACTION_LOCK.release()


class FreshnessTests(SnapshotCase):
    def test_ttl_then_new_collection(self):
        server.run_action("traffic", {}, "tester")
        self.clock.t += server.READ_CACHE_TTL["traffic"] - 0.1
        server.run_action("traffic", {}, "tester")
        self.assertEqual(self.bridge.count("traffic"), 1)
        self.clock.t += 0.1
        server.run_action("traffic", {}, "tester")
        self.assertEqual(self.bridge.count("traffic"), 2)

    def test_change_by_another_process_is_seen_at_once(self):
        users = core_file("users.json")
        cfg = core_file("config.json")
        stats = core_file("stats.json")
        write_json(users, {"users": [{"name": "anna"}]})
        server.run_action("user-list", {}, "tester")
        server.run_action("user-list", {}, "tester")
        self.assertEqual(self.bridge.count("user-list"), 1)
        # Бот удалил человека: атомарная замена файла (новый inode) — следующий же запрос идёт в мост.
        write_json(users, {"users": []})
        server.run_action("user-list", {}, "tester")
        self.assertEqual(self.bridge.count("user-list"), 2)
        # Правка config.json из SSH — тоже.
        write_json(cfg, {"mode": "lite"})
        server.run_action("user-list", {}, "tester")
        self.assertEqual(self.bridge.count("user-list"), 3)
        # Таймер статистики переписал stats.json — снимок трафика несвежий, списка людей — нет.
        server.run_action("traffic", {}, "tester")
        write_json(stats, {"days": {}})
        server.run_action("traffic", {}, "tester")
        server.run_action("user-list", {}, "tester")
        self.assertEqual(self.bridge.count("traffic"), 2)
        self.assertEqual(self.bridge.count("user-list"), 3)

    def test_file_changed_during_collection_does_not_leave_a_fresh_looking_snapshot(self):
        users = core_file("users.json")
        write_json(users, {"users": []})
        self.bridge.answers["user-list"] = lambda: (write_json(users, {"users": [{"name": "new"}]}),
                                                    {"status": "success", "users": []})[1]
        server.run_action("user-list", {}, "tester")
        self.bridge.answers.pop("user-list")
        server.run_action("user-list", {}, "tester")
        self.assertEqual(self.bridge.count("user-list"), 2)

    def test_old_read_does_not_refill_the_cache_after_a_mutation(self):
        gate_old = threading.Event()
        self.bridge.gates = [gate_old]
        self.bridge.answers["traffic"] = lambda: {"status": "success", "seq": self.bridge.count("traffic")}
        out = {}
        t_old = self.reader("traffic", out, 1)
        self.wait_entered()
        # Мутация из панели, пока старое чтение идёт: всё собранное раньше — несвежее.
        self.assertEqual(server.run_action("user-rename", {"name": "anna", "label": "A"}, "tester")["status"], "success")
        # Новый читатель к старому сбору не присоединяется — свой вызов, и он кладёт свежий снимок.
        fresh = server.run_action("traffic", {}, "tester")
        self.assertEqual(self.bridge.count("traffic"), 2)
        gate_old.set()
        t_old.join(20)
        self.assertEqual(out[1]["seq"], 1)
        # Старый ответ пришёл последним, но в кэш не лёг: следующий читатель получает новый.
        self.assertIs(server.run_action("traffic", {}, "tester"), fresh)
        self.assertEqual(self.bridge.count("traffic"), 2)

    def test_manual_refresh_asks_for_a_new_collection_but_not_twice_a_second(self):
        server.run_action("traffic", {}, "tester")
        server._CALL_CTX.fresh = True
        self.clock.t += 1.0
        server.run_action("traffic", {}, "tester")
        self.assertEqual(self.bridge.count("traffic"), 1)      # снимок моложе 2 с — тот же
        self.clock.t += 2.0
        server.run_action("traffic", {}, "tester")
        self.assertEqual(self.bridge.count("traffic"), 2)
        server._CALL_CTX.fresh = False

    def test_manual_refresh_joins_a_running_collection(self):
        gate = threading.Event()
        self.bridge.gates = [gate]
        out = {}
        t1 = self.reader("traffic", out, 1)
        self.wait_entered()
        t2 = self.reader("traffic", out, 2, fresh=True)
        self.wait_waiting(1)
        gate.set()
        t1.join(20)
        t2.join(20)
        self.assertEqual(self.bridge.count("traffic"), 1)
        self.assertIs(out[1], out[2])


class StaleAndMetaTests(SnapshotCase):
    USERS = {"status": "success", "users": [{"name": "anna", "enabled": True, "peers": {}}]}

    def test_failed_refresh_keeps_the_previous_picture_marked_stale(self):
        self.bridge.answers["user-list"] = self.USERS
        d = server.users_payload("tester")
        self.assertNotIn("stale", d)
        self.assertEqual([u["name"] for u in d["users"]], ["anna"])
        first_at = d["snapshot_at"]
        # Срок вышел, новый сбор упал по таймауту — экран получает прежний список с пометкой, а не пустоту.
        self.clock.t += 120
        self.bridge.answers["user-list"] = {"status": "error", "code": "timeout", "message": ""}
        server._CALL_CTX.reads = {}
        d = server.users_payload("tester")
        self.assertEqual([u["name"] for u in d["users"]], ["anna"])
        self.assertEqual(d["stale"]["code"], "timeout")
        self.assertEqual(d["stale"]["since"], first_at)
        self.assertNotIn("error", d)

    def test_substantive_refusal_is_not_hidden_behind_old_data(self):
        self.bridge.answers["user-list"] = self.USERS
        server.users_payload("tester")
        self.clock.t += 120
        self.bridge.answers["user-list"] = {"status": "error", "code": "users_corrupt", "message": "битый users.json"}
        d = server.users_payload("tester")
        self.assertEqual(d["error"]["code"], "users_corrupt")
        self.assertEqual(d["users"], [])

    def test_stale_picture_expires(self):
        self.bridge.answers["user-list"] = self.USERS
        server.users_payload("tester")
        self.clock.t += server.READ_STALE_MAX_AGE + 1
        self.bridge.answers["user-list"] = {"status": "error", "code": "transport", "message": ""}
        d = server.users_payload("tester")
        self.assertEqual(d["error"]["code"], "transport")

    def test_snapshot_age_is_the_collection_start(self):
        d1 = server.traffic_payload("tester")
        self.clock.t += 10
        server._CALL_CTX.reads = {}
        d2 = server.traffic_payload("tester")
        self.assertEqual(self.bridge.count("traffic"), 1)
        self.assertEqual(d1["snapshot_at"], d2["snapshot_at"])
        self.assertEqual(d1["snapshot_at"], server._iso_ts(self.clock.t - 10))

    def test_share_lifetime_follows_the_clock(self):
        self.bridge.answers["share-list"] = {"status": "success", "shares": [
            {"id": "s-aaaa", "name": "anna", "seconds_left": 214, "url": "https://x/SECRET"},
            {"id": "s-bbbb", "name": "bob", "seconds_left": 10}]}
        d = server.share_payload("tester")
        self.assertEqual([s["seconds_left"] for s in d["shares"]], [214, 10])
        self.clock.t += 20
        server._CALL_CTX.reads = {}
        d = server.share_payload("tester")
        self.assertEqual(self.bridge.count("share-list"), 1)
        # Остаток — от времени сбора; истёкшая ссылка пропала, адрес ссылки в списке так и не появился.
        self.assertEqual([(s["id"], s["seconds_left"]) for s in d["shares"]], [("s-aaaa", 194)])
        self.assertNotIn("SECRET", json.dumps(d))

    def test_answer_with_secret_fields_is_never_kept(self):
        self.bridge.answers["traffic"] = {"status": "success", "profiles": [{"profile": "awg2", "peers": [
            {"pubkey": "PUB", "privkey": "MUSTNOTSTAY"}]}], "users": []}
        server.run_action("traffic", {}, "tester")
        server.run_action("traffic", {}, "tester")
        self.assertEqual(self.bridge.count("traffic"), 2)
        self.assertNotIn("MUSTNOTSTAY", repr(server._READ_CACHE) + repr(server._READ_LAST_GOOD))

    def test_secret_actions_are_never_shared(self):
        self.assertNotIn("user-key", server.READ_CACHE_TTL)
        for _ in range(2):
            server.run_action("user-key", {"name": "anna", "profile": "awg2", "format": "conf"}, "tester")
        self.assertEqual(self.bridge.count("user-key"), 2)
        self.assertFalse(any(k.startswith("user-key") for k in server._READ_CACHE))


class PayloadMetaTests(SnapshotCase):
    def test_overview_uses_the_brief_status_when_the_core_knows_it(self):
        self.assertEqual(server.status_brief_params(), {"brief": "on"})
        self.bridge.answers["status"] = {"status": "success", "mode": "lite", "profiles": []}
        d = server.overview_payload("tester")
        self.assertIn("snapshot_at", d)
        argv = [c[2] for c in self.bridge.calls if c[0] == "status"]
        self.assertEqual(argv, [("--brief=on",)])

    def test_services_health_is_cached_like_status(self):
        calls = []
        orig = server.service_state
        server.service_state = lambda unit: calls.append(unit) or "active"
        orig_exists = server.unit_exists
        server.unit_exists = lambda unit: True
        try:
            server._SERVICES_CACHE.update({"at": 0.0, "data": {}, "mode": None})
            server.services_health("pro")
            n = len(calls)
            self.clock.t += server.SERVICES_TTL - 1
            server.services_health("pro")
            self.assertEqual(len(calls), n)
            server.services_health("lite")       # другой режим — другой набор служб
            self.assertGreater(len(calls), n)
        finally:
            server.service_state = orig
            server.unit_exists = orig_exists


STATUS = {"status": "success", "message": "ok", "mode": "lite", "users": "2", "server_ip": "203.0.113.10",
          "profiles": [{"profile": "awg2", "container": "amnezia-awg2", "state": "running", "port": 443, "line": "2.0",
                        "peers": 2}]}
EDGE = {"status": "success", "message": "ok", "count": 1, "domain_endpoint": False, "dns": None,
        "ips": [{"ip": "203.0.113.10", "verdict": "ok"}]}


def traffic_answer(clock, dumped=True, ago=(100, 900)):
    """traffic: anna рукопожатием ago[0] с назад, bob — ago[1]; dumped=False — awg show не ответил, и ядро отдало
    пустой список пиров профиля."""
    peers = [{"pubkey": "PUB-A", "latest_handshake_ts": int(clock.t) - ago[0]},
             {"pubkey": "PUB-B", "latest_handshake_ts": int(clock.t) - ago[1]}] if dumped else []
    return {"status": "success", "message": "ok", "profiles": [{"profile": "awg2", "peers": peers}],
            "users": [{"name": "anna", "enabled": True, "peers": {"awg2": {"pubkey": "PUB-A"}}, "traffic": {"rx": 10, "tx": 20}},
                      {"name": "bob", "enabled": True, "peers": {"awg2": {"pubkey": "PUB-B"}}, "traffic": {"rx": 1, "tx": 2}}]}


class ColdOverviewTests(SnapshotCase):
    """PERF-03 (живая проверка на 168: холодные /api/overview, /api/settings, /api/traffic по 3–5 с, плитка «Пользователи»
    несколько секунд «0 на связи»): Обзор ждёт только состояние ядра (и то — не дольше, чем нет годного снимка), рукопожатия
    и адреса сервера собираются в фоне, «на связи» без прочитанных рукопожатий — None, а не ноль; «Настройки» свои
    подсказки не ждут."""

    def gated(self, name, answer):
        """Ответ моста на name держится, пока тест не откроет заслонку (и при уборке — всегда)."""
        ev = threading.Event()
        self.bridge.answers[name] = lambda: (ev.wait(20), answer() if callable(answer) else answer)[1]
        self.addCleanup(ev.set)
        return ev

    def call(self, fn, *args, lang="", fresh=False):
        """fn(*args) в своём потоке, как запрос панели (язык интерфейса и «Обновить» — этого запроса): → (поток, ящик
        с ответом)."""
        box = {}

        def run():
            server._CALL_CTX.lang = lang
            server._CALL_CTX.fresh = fresh
            server._CALL_CTX.reads = {}
            box["r"] = fn(*args)
        th = threading.Thread(target=run, daemon=True)
        th.start()
        return th, box

    def ask(self, fn, *args, lang="", fresh=False):
        """Запрос, который не ждёт ничего долгого (ответы моста без заслонок): ответ."""
        th, box = self.call(fn, *args, lang=lang, fresh=fresh)
        self.done(th, f"{getattr(fn, '__name__', fn)} ждал")
        return box["r"]

    def settle(self):
        """Фоновые сборы закончились."""
        deadline = time.monotonic() + 20
        while server._READ_FLIGHTS and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(server._READ_FLIGHTS, {})

    def done(self, th, what):
        th.join(5)
        self.assertFalse(th.is_alive(), what)

    def test_cold_overview_waits_only_for_the_core_status(self):
        self.bridge.answers["status"] = STATUS
        tr_gate = self.gated("traffic", lambda: traffic_answer(self.clock))
        edge_gate = self.gated("edge-status", EDGE)
        th, box = self.call(server.overview_payload, "tester")
        self.done(th, "Обзор ждал рукопожатия и адреса сервера")
        d = box["r"]
        self.assertTrue(d["ok_status"])
        self.assertEqual(d["users_total"], 2)
        self.assertIsNone(d["users_online"])                  # не знаем — не ноль
        self.assertIsNone(d["traffic_total"])
        self.assertEqual(sorted(d["pending"]), ["edge-status", "traffic"])
        self.assertEqual(d["tunnel_host"], "203.0.113.10")    # до ответа сторожа — адрес сервера
        tr_gate.set()
        edge_gate.set()
        self.settle()
        th, box = self.call(server.overview_payload, "tester")
        self.done(th, "второй Обзор")
        d = box["r"]
        self.assertEqual(d["users_online"], 1)
        self.assertEqual(d["traffic_total"], {"rx": 11, "tx": 22})
        self.assertEqual(d["pending"], [])
        self.assertEqual((self.bridge.count("traffic"), self.bridge.count("edge-status")), (1, 1))   # сбор — один, фоновый

    def overview_with(self, status, traffic):
        server._cache_clear()
        self.bridge.answers.update({"status": status, "edge-status": EDGE, "traffic": traffic})
        server.read_snapshot("traffic", "tester")
        server.run_action("edge-status", {}, "tester")
        server._CALL_CTX.reads = {}
        return server.overview_payload("tester")

    def test_unread_handshakes_are_not_zero_online(self):
        # awg show не ответил (ядро прячет сбой в пустой список пиров), а в профиле есть люди — «на связи» не знаем.
        d = self.overview_with(STATUS, lambda: traffic_answer(self.clock, dumped=False))
        self.assertIsNone(d["users_online"])
        self.assertEqual(d["traffic_total"], {"rx": 11, "tx": 22})   # учёт людей — из users.json, он прочитан
        # Docker не ответил — тоже «не знаем».
        st = dict(STATUS, profiles=[dict(STATUS["profiles"][0], state="docker_unreachable")])
        self.assertIsNone(self.overview_with(st, lambda: traffic_answer(self.clock))["users_online"])
        # Рукопожатия прочитаны, но давние — честный ноль.
        d = self.overview_with(STATUS, lambda: traffic_answer(self.clock, ago=(600, 900)))
        self.assertEqual(d["users_online"], 0)
        # Профиль остановлен — его пустой список никого не прячет.
        st = dict(STATUS, profiles=[dict(STATUS["profiles"][0], state="exited")])
        self.assertEqual(self.overview_with(st, lambda: traffic_answer(self.clock, dumped=False))["users_online"], 0)

    def test_stale_handshakes_after_a_failed_collection_are_not_zero_online(self):
        # Новый сбор упал по таймауту: Обзор не ждёт — «собирается» (pending); а с GOAMNEZIA_READ_SWR=0 (ждёт, как в
        # 1.0.9) — прежняя картина (stale) даёт учёт, но не «на связи»: рукопожатиям в ней больше 45 с (CR-2).
        self.overview_with(STATUS, lambda: traffic_answer(self.clock))
        self.clock.t += 120
        self.bridge.answers["traffic"] = {"status": "error", "code": "timeout", "message": ""}
        server._CALL_CTX.reads = {}
        d = server.overview_payload("tester")
        self.assertEqual((d["users_online"], d["pending"]), (None, ["traffic"]))
        self.settle()
        os.environ["GOAMNEZIA_READ_SWR"] = "0"
        self.addCleanup(os.environ.pop, "GOAMNEZIA_READ_SWR", None)
        self.clock.t += server.READ_CACHE_TTL["status"]
        server._CALL_CTX.reads = {}
        d = server.overview_payload("tester")
        self.assertEqual(d["stale"]["code"], "timeout")
        self.assertEqual(d["traffic_total"], {"rx": 11, "tx": 22})
        self.assertIsNone(d["users_online"])

    def test_old_core_status_is_served_at_once_and_refreshed_in_background(self):
        self.bridge.answers.update({"status": STATUS, "edge-status": EDGE, "traffic": lambda: traffic_answer(self.clock)})
        server.overview_payload("tester")
        self.settle()
        self.clock.t += server.READ_CACHE_TTL["status"] + 10      # срок снимка вышел, READ_SWR_MAX_AGE — нет
        st_gate = self.gated("status", STATUS)
        th, box = self.call(server.overview_payload, "tester")
        self.done(th, "Обзор ждал новый status при снимке моложе 10 минут")
        d = box["r"]
        self.assertTrue(d["ok_status"])
        self.assertIn("status", d["swr"]["reads"])
        self.assertEqual(d["swr"]["age"], server.READ_CACHE_TTL["status"] + 10)
        st_gate.set()
        self.settle()
        self.assertEqual(self.bridge.count("status"), 2)
        # Снимок старше 10 минут — ждём свежий, как раньше.
        self.clock.t += server.READ_SWR_MAX_AGE["status"] + 1
        st_gate = self.gated("status", STATUS)
        th, box = self.call(server.overview_payload, "tester")
        th.join(0.5)
        self.assertTrue(th.is_alive(), "состояние ядра старше 10 минут — Обзор ждёт свежее")
        st_gate.set()
        self.done(th, "Обзор после сбора status")

    def test_settings_do_not_wait_for_their_hints(self):
        gates = [self.gated("status", STATUS), self.gated("edge-status", EDGE)]
        th, box = self.call(server.settings_payload, "tester")
        self.done(th, "«Настройки» ждали status и адреса сервера")
        d = box["r"]
        self.assertEqual(sorted(d["pending"]), ["edge-status", "status"])
        self.assertIsNone(d["server_ip_change"])
        self.assertFalse(d["endpoint_host_gone"])
        for g in gates:
            g.set()
        self.settle()
        th, box = self.call(server.settings_payload, "tester")
        self.done(th, "вторые «Настройки»")
        self.assertEqual(box["r"]["pending"], [])
        self.assertEqual((self.bridge.count("status"), self.bridge.count("edge-status")), (1, 1))

    def test_overview_traffic_request_does_not_wait_but_the_traffic_page_does(self):
        gate = self.gated("traffic", lambda: traffic_answer(self.clock))
        th, box = self.call(server.traffic_payload, "tester", False)
        self.done(th, "/api/traffic?wait=0 ждал сбор")
        self.assertTrue(box["r"]["pending"])
        self.assertEqual(box["r"]["users"], [])
        th2, box2 = self.call(server.traffic_payload, "tester")
        th2.join(0.3)
        self.assertTrue(th2.is_alive(), "раздел «Трафик» ждёт свежий сбор")
        gate.set()
        self.done(th2, "раздел «Трафик»")
        self.assertEqual(box2["r"]["online"], 1)
        self.assertEqual(self.bridge.count("traffic"), 1)       # раздел присоединился к фоновому сбору

    def test_swr_switch_off_overview_waits_as_in_1_0_9(self):
        os.environ["GOAMNEZIA_READ_SWR"] = "0"
        self.addCleanup(os.environ.pop, "GOAMNEZIA_READ_SWR", None)
        self.bridge.answers.update({"status": STATUS, "edge-status": EDGE})
        gate = self.gated("traffic", lambda: traffic_answer(self.clock))
        th, box = self.call(server.overview_payload, "tester")
        th.join(0.5)
        self.assertTrue(th.is_alive(), "GOAMNEZIA_READ_SWR=0 — Обзор ждёт рукопожатия, как в 1.0.9")
        gate.set()
        self.done(th, "Обзор после сбора")
        self.assertEqual((box["r"]["users_online"], box["r"]["pending"]), (1, []))

    def test_services_health_old_picture_is_served_while_refreshing(self):
        calls = []
        gate = threading.Event()
        self.addCleanup(gate.set)

        def state(unit):
            calls.append(unit)
            if len(calls) > 3:
                gate.wait(20)                    # второй сбор висит
            return "active"
        orig_state, orig_exists = server.service_state, server.unit_exists
        server.service_state = state
        server.unit_exists = lambda unit: False
        try:
            server._SERVICES_CACHE.update({"at": 0.0, "data": {}, "mode": None, "refreshing": False})
            first = server.services_health("lite")
            self.assertEqual(len(calls), 3)
            self.clock.t += server.SERVICES_TTL + 1
            th, box = self.call(server.services_health, "lite")
            self.done(th, "Обзор ждал systemctl при картине моложе 10 минут")
            self.assertEqual(box["r"], first)
            th, box = self.call(server.services_health, "lite")   # сбор уже идёт — второго нет
            self.done(th, "повторный запрос")
            gate.set()
            deadline = time.monotonic() + 20
            while server._SERVICES_CACHE["refreshing"] and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(len(calls), 6)
            self.assertEqual(server._SERVICES_CACHE["at"], self.clock.t)
        finally:
            gate.set()
            deadline = time.monotonic() + 20
            while server._SERVICES_CACHE["refreshing"] and time.monotonic() < deadline:
                time.sleep(0.01)
            server.service_state, server.unit_exists = orig_state, orig_exists

    def test_overview_after_idle_keeps_the_traffic_tally_but_not_online(self):
        # PERF-05 (168: после простоя больше 45 с Обзор писал «—» и в трафике): сумма — из снимка до 10 минут с возрастом
        # (счётчики только растут), «на связи» — только по свежим рукопожатиям (CR-2), а пока — pending и новый сбор.
        self.overview_with(STATUS, lambda: traffic_answer(self.clock))
        self.settle()
        self.clock.t += 120
        server._CALL_CTX.reads = {}
        d = server.overview_payload("tester")
        self.assertEqual(d["traffic_total"], {"rx": 11, "tx": 22})
        self.assertEqual(d["traffic_total_age"], 120)
        self.assertIsNone(d["users_online"])
        self.assertIn("traffic", d["pending"])
        self.settle()                                           # новый сбор — в фоне
        server._CALL_CTX.reads = {}
        d = server.overview_payload("tester")
        self.assertEqual((d["users_online"], d["traffic_total_age"], d["pending"]), (1, 0, []))
        # Старше 10 минут — суммы нет, как раньше: только pending.
        self.clock.t += server.TRAFFIC_TOTALS_MAX_AGE + 1
        server._CALL_CTX.reads = {}
        d = server.overview_payload("tester")
        self.assertEqual((d["traffic_total"], d["users_online"]), (None, None))
        self.assertIn("traffic", d["pending"])

    def test_overview_traffic_request_after_idle_gives_the_tally_without_online(self):
        # /api/traffic?wait=0 после простоя: учёт (сутки, месяц, часы) — из снимка до 10 минут, «на связи» — None у всех.
        self.bridge.answers["traffic"] = lambda: traffic_answer(self.clock)
        server.read_snapshot("traffic", "tester")
        self.clock.t += 120
        server._CALL_CTX.reads = {}
        d = server.traffic_payload("tester", False)
        self.assertNotIn("pending", d)
        self.assertIsNone(d["online"])
        self.assertEqual([u["online"] for u in d["users"]], [None, None])
        self.assertEqual([p["online"] for p in d["profiles"]], [None])
        self.settle()
        server._CALL_CTX.reads = {}
        d = server.traffic_payload("tester", False)
        self.assertEqual(d["online"], 1)

    # ── PERF-08 (168, 05.10: /api/overview по curl через 4 мин после рестарта — «холодная» форма: traffic_total null,
    # pending [traffic, edge-status], без мутаций; страница в это время шла на тёплых снимках) ─────────────────────────
    def warm_page(self, lang="ru"):
        """Страница на языке lang открыла Обзор и дождалась фоновых сборов: status, traffic и edge-status — тёплые.
        → сколько раз мост собирал каждое."""
        self.bridge.answers.update({"status": STATUS, "edge-status": EDGE, "traffic": lambda: traffic_answer(self.clock)})
        self.ask(server.overview_payload, "tester", lang=lang)
        self.settle()
        d = self.ask(server.overview_payload, "tester", lang=lang)
        self.assertEqual((d["users_online"], d["pending"]), (1, []))
        return {n: self.bridge.count(n) for n in ("status", "traffic", "edge-status")}

    def test_request_without_language_shares_the_page_snapshots(self):
        # curl и скрипты проверки заголовок языка не шлют: раньше у них были свои снимки «действие|», холодные после
        # рестарта, хотя страница на языке установки их уже собрала, — Обзор по curl отвечал «холодной» формой и всё
        # собиралось второй раз.
        before = self.warm_page("ru")
        d = self.ask(server.overview_payload, "tester")                 # без языка, язык установки — ru
        self.assertEqual(d["traffic_total"], {"rx": 11, "tx": 22})
        self.assertEqual((d["users_online"], d["pending"]), (1, []))
        self.assertIn("edge", d)
        self.settle()
        self.assertEqual({n: self.bridge.count(n) for n in before}, before)   # ни одного нового сбора
        # Язык установки — английский: запрос без языка живёт на английских снимках, и мост отвечает по-английски явно.
        write_json(core_file("config.json"), {"lang": "en"})
        server._cache_clear()
        self.bridge.calls.clear()
        self.ask(server.overview_payload, "tester")
        self.settle()
        self.assertEqual({c[1] for c in self.bridge.calls}, {"en"})
        n = len(self.bridge.calls)
        d = self.ask(server.overview_payload, "tester", lang="en")
        self.assertEqual((d["users_online"], d["pending"]), (1, []))
        self.settle()
        self.assertEqual(len(self.bridge.calls), n)                    # английская страница — на тех же снимках
        self.ask(server.overview_payload, "tester", lang="ru")           # русская — на своих
        self.settle()
        self.assertEqual({c[1] for c in self.bridge.calls[n:]}, {"ru"})

    def test_manual_refresh_keeps_the_overview_picture(self):
        # «Обновить» (X-GoAmnezia-Fresh) у чтений без ожидания: раньше прежний снимок такому запросу не годился, и Обзор
        # отвечал той же «холодной» формой — без трафика, «на связи» и адресов сервера, — пока не кончится новый сбор.
        # Теперь — прежний снимок, новый сбор в фоне и pending: страница переспросит через пару секунд.
        before = self.warm_page("ru")
        self.clock.t += 20                                               # снимки старше 2 с
        d = self.ask(server.overview_payload, "tester", lang="ru", fresh=True)
        self.assertEqual(d["traffic_total"], {"rx": 11, "tx": 22})
        self.assertEqual(d["traffic_total_age"], 20)
        self.assertEqual(d["users_online"], 1)                           # рукопожатиям 20 с — годны (CR-2)
        self.assertIn("edge", d)
        self.assertEqual(sorted(d["pending"]), ["edge-status", "traffic"])
        self.settle()
        self.assertEqual(self.bridge.count("status"), before["status"] + 1)        # состояние ядра «Обновить» ждёт
        self.assertEqual(self.bridge.count("traffic"), before["traffic"] + 1)      # трафик и адреса — новым сбором в фоне
        self.assertEqual(self.bridge.count("edge-status"), before["edge-status"] + 1)
        # Учёт для Обзора (/api/traffic?wait=0) — тоже прежним снимком, а не «собирается».
        self.clock.t += 20
        t = self.ask(server.traffic_payload, "tester", False, lang="ru", fresh=True)
        self.assertNotIn("pending", t)
        self.assertEqual(t["online"], 1)
        self.settle()
        # Снимок моложе 2 с «Обновить» не пересобирает.
        n = self.bridge.count("traffic")
        self.clock.t += 1
        self.ask(server.overview_payload, "tester", lang="ru", fresh=True)
        self.settle()
        self.assertEqual(self.bridge.count("traffic"), n)

    def test_overview_traffic_tile_comes_with_the_overview(self):
        # PNL-07 (168: первый кадр — «Трафик за всё время», через 0,35 с — «за 30 дней»): плитка «Трафик» — в ответе
        # Обзора (traffic_kpi), правилом страницы: 30 суток учёта, иначе месяц, иначе всё накопленное; из того же снимка,
        # что и /api/traffic.
        def answer(**extra):
            return lambda: dict(traffic_answer(self.clock), **extra)
        days = [{"date": (datetime.date(2026, 9, 1) + datetime.timedelta(days=i)).isoformat(), "rx": 1, "tx": 2}
                for i in range(35)]
        d = self.overview_with(STATUS, answer(days=days, month={"rx": 500, "tx": 700}))
        k = d["traffic_kpi"]
        self.assertEqual((k["period"], k["rx"], k["tx"]), ("30d", 30, 60))
        self.assertEqual([x["date"] for x in k["days"]], [x["date"] for x in days[-14:]])
        self.assertEqual(k["at"], server._iso_ts(self.clock.t))
        server._CALL_CTX.reads = {}
        t = server.traffic_payload("tester", False)                     # тот же снимок — та же сумма
        self.assertEqual((sum(x["rx"] for x in t["days"][-30:]), sum(x["tx"] for x in t["days"][-30:])), (30, 60))
        d = self.overview_with(STATUS, answer(days=[], month={"rx": 500, "tx": 700}))
        self.assertEqual({k: d["traffic_kpi"][k] for k in ("period", "rx", "tx", "days")},
                         {"period": "month", "rx": 500, "tx": 700, "days": []})
        d = self.overview_with(STATUS, answer())
        self.assertEqual(d["traffic_kpi"], {"period": "all", "rx": 11, "tx": 22, "days": [], "at": server._iso_ts(self.clock.t)})
        # Учёт ещё собирается — плитки нет, как и суммы.
        server._cache_clear()
        gate = self.gated("traffic", lambda: traffic_answer(self.clock))
        d = self.ask(server.overview_payload, "tester")
        self.assertIsNone(d["traffic_kpi"])
        self.assertIsNone(d["traffic_total"])
        gate.set()
        self.settle()


class BackgroundPriorityTests(SnapshotCase):
    """PERF-04 (живая проверка на 168: холодный Обзор в браузере 4,2–4,5 с до первого кадра): фоновые сборы (прежний снимок
    с новым сбором, wait=False) идут через nice и ionice — передний status получает ядро первым; сам мост запускается
    по-настоящему, подменён только Popen."""

    def setUp(self):
        super().setUp()
        server._bridge_exec = self._exec
        self.argvs = []
        case = self

        class Proc:
            returncode = 0
            stdout = stderr = None

            def __init__(self, argv, **kw):
                case.argvs.append(list(argv))

            def communicate(self, input=None, timeout=None):
                return b'{"status": "success", "message": "ok", "action": "x"}\n', b""
        self._popen = mock.patch.object(server.subprocess, "Popen", Proc)
        self._popen.start()
        self.addCleanup(self._popen.stop)
        self.nice = shutil.which("nice")
        self.ionice = shutil.which("ionice")
        if not self.nice:
            self.skipTest("нет nice")

    def settle(self):
        deadline = time.monotonic() + 20
        while server._READ_FLIGHTS and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(server._READ_FLIGHTS, {})

    def action_of(self, argv):
        return next(a.split("=", 1)[1] for a in argv if a.startswith("--action="))

    def assertNiced(self, argv, level):
        self.assertEqual(argv[:3], [self.nice, "-n", level], argv)
        if self.ionice:
            self.assertEqual(argv[3:8], [self.ionice, "-c", "2", "-n", "7"], argv)
        self.assertEqual(argv[len(server.bg_prefix(self.action_of(argv)))], server.ACTION_BIN)

    def test_background_collections_yield_the_core_to_the_foreground(self):
        server.run_action("status", {"brief": "on"}, "tester")            # переднее чтение — как есть
        self.assertEqual(self.argvs[-1][0], server.ACTION_BIN)
        for name, level in (("traffic", "10"), ("edge-status", "19"), ("status", "15")):
            self.assertEqual(server.run_action(name, {}, "tester", wait=False)["code"], "pending")
            self.settle()
            self.assertEqual(self.action_of(self.argvs[-1]), name)
            self.assertNiced(self.argvs[-1], level)
        # Прежний снимок с фоновым сбором (swr) — тоже фон.
        self.clock.t += server.READ_CACHE_TTL["traffic"] + 1
        server.run_action("traffic", {}, "tester", swr=server.READ_SWR_MAX_AGE["traffic"])
        self.settle()
        self.assertNiced(self.argvs[-1], "10")

    def test_cold_overview_asks_the_core_status_first_and_the_rest_in_the_background(self):
        with mock.patch.object(server, "services_health", return_value={}):
            server.overview_payload("tester")
        self.settle()
        names = [self.action_of(a) for a in self.argvs]
        self.assertEqual(names[0], "status")
        self.assertEqual(self.argvs[0][0], server.ACTION_BIN)            # status Обзора — с обычным приоритетом
        self.assertEqual(sorted(names[1:]), ["edge-status", "traffic"])
        for argv in self.argvs[1:]:
            self.assertEqual(argv[0], self.nice)

    def test_switch_off_keeps_the_background_at_normal_priority(self):
        os.environ["GOAMNEZIA_READ_BG_NICE"] = "0"
        self.addCleanup(os.environ.pop, "GOAMNEZIA_READ_BG_NICE", None)
        server.run_action("traffic", {}, "tester", wait=False)
        self.settle()
        self.assertEqual(self.argvs[-1][0], server.ACTION_BIN)


if __name__ == "__main__":
    unittest.main()
