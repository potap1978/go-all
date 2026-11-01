# -*- coding: utf-8 -*-
"""Переходник goamnezia-connect против поддельного сервера с тем же протоколом.

Поддельный сервер держит одну цепочку одноразового ключа по правилам
docs/MCP_ROTATION.md §1 и §11: текущий ключ вращается, повтор той же попытки
(тот же ключ и тот же номер) отдаёт сохранённый ответ, любое иное использование
прежнего ключа — клон. Время сервера смещается по желанию проверки.
"""
import io
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
import email.utils
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "goamnezia-mcp"))
import connect  # noqa: E402


def new_secret():
    return connect.b64url(secrets.token_bytes(32))


class FakeServer(object):
    def __init__(self):
        self.skew = 0.0              # сдвиг часов сервера относительно настоящих
        self.code = "GA-ABCDE-FGHIJ-KLMNO-PQRST"
        self.code_used = False
        self.grant = "a-1f2e"
        self.rot_seed = new_secret()
        self.token = new_secret()
        self.cur = new_secret()
        self.prev = ""
        self.prev_nonce = ""
        self.saved = None            # (status, next_key, body) последнего вращения
        self.clone = False
        self.executed = []           # методы, выполненные на самом деле
        self.requests = 0
        self.storage_seen = ""
        self.lock = threading.Lock()
        self.drop_reply = False      # выполнить и повернуть цепочку, но ответ «не довезти»
        self.status_next = 0         # статус следующего вращающего ответа (400 — кривой запрос)
        self.refuse_all = False      # отказ 404 до цепочки: бан, выключенный доступ, привязка к адресу
        self.refuse_after_drop = False
        self.crash_before_rotate = False   # выполнить действие и оборвать связь, не записав цепочку
        self.null_id_next = False          # следующий ответ — ошибка без id (сервер не сохранил ответ)
        self.gateway_next = 0              # следующий вращающий ответ заменить на 502/504 от nginx
        self.gateway_crash = 0             # выполнить, не записать цепочку и ответить 502/504

    def now(self):
        return time.time() + self.skew

    def window(self):
        slot = int(self.now() // connect.SLOT_SECONDS)
        return {connect.float_path(self.rot_seed, s) for s in (slot - 1, slot, slot + 1)}


def make_handler(fake):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, status, body=b"", headers=()):
            self.send_response(status)
            self.send_header("Date", email.utils.formatdate(fake.now(), usegmt=True))
            self.send_header("Content-Length", str(len(body)))
            if body:
                self.send_header("Content-Type", "application/json")
            for k, v in headers:
                self.send_header(k, v)
            self.end_headers()
            if body:
                self.wfile.write(body)

        def do_GET(self):
            self._send(200, b"{}")

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length)
            with fake.lock:
                fake.requests += 1
                if self.path == "/a-%s/enroll" % connect.enroll_path(fake.code):
                    if fake.code_used:
                        return self._send(404)
                    fake.code_used = True
                    fake.storage_seen = json.loads(body.decode()).get("key_storage", "")
                    reply = {"grant": fake.grant, "rot_seed": fake.rot_seed, "token": fake.token,
                             "key": fake.cur, "label": "cursor", "scope": "read"}
                    return self._send(200, json.dumps(reply).encode())
                if not (self.path.startswith("/r-") and self.path.endswith("/mcp")):
                    return self._send(404)
                if self.path[3:46] not in fake.window():
                    return self._send(404)          # путь — раньше ключа, цепочка не тронута
                if fake.refuse_all:
                    return self._send(404)          # отказ до цепочки
                if self.headers.get("Authorization") != "Bearer " + fake.token or fake.clone:
                    return self._send(404)
                key = self.headers.get("X-Ga-Key") or ""
                nonce = self.headers.get("X-Ga-Nonce") or ""
                if key == fake.prev and nonce == fake.prev_nonce and fake.saved:
                    status, nxt, out = fake.saved
                    return self._send(status, out, [("X-Ga-Next-Key", nxt)])
                if key != fake.cur:
                    fake.clone = True
                    return self._send(404)
                message = json.loads(body.decode())
                if fake.gateway_crash and "id" in message:
                    code, fake.gateway_crash = fake.gateway_crash, 0
                    fake.executed.append(message.get("method"))
                    return self._send(code, b"<html>bad gateway</html>")
                if fake.crash_before_rotate and "id" in message:
                    fake.crash_before_rotate = False
                    fake.executed.append(message.get("method"))
                    self.close_connection = True
                    return None
                nxt = new_secret()
                fake.prev, fake.prev_nonce, fake.cur = fake.cur, nonce, nxt
                if "id" in message:
                    fake.executed.append(message.get("method"))
                    out = json.dumps({"jsonrpc": "2.0", "id": message["id"],
                                      "result": {"echo": message.get("method"),
                                                 "protocolVersion": "2025-03-26"}}).encode()
                    status = 200
                else:
                    fake.executed.append("notify:" + str(message.get("method")))
                    out, status = b"", 202
                if fake.null_id_next:
                    fake.null_id_next = False
                    out = json.dumps({"jsonrpc": "2.0", "id": None,
                                      "error": {"code": -32603, "message": "previous answer was not kept"}}).encode()
                if fake.status_next:
                    status, fake.status_next = fake.status_next, 0
                fake.saved = (status, nxt, out)
                if fake.gateway_next:
                    code, fake.gateway_next = fake.gateway_next, 0
                    return self._send(code, b"<html>gateway</html>")
                if fake.drop_reply:
                    fake.drop_reply = False
                    fake.refuse_all = fake.refuse_after_drop
                    self.close_connection = True
                    return None
                return self._send(status, out, [("X-Ga-Next-Key", nxt)])

    return Handler


class FailingStore(connect.FileStore):
    """Хранилище, у которого по счёту отказывает одна запись."""
    saves = 0
    fail_on = 0

    def save(self, data):
        FailingStore.saves += 1
        if FailingStore.saves == FailingStore.fail_on:
            raise connect.StoreError("disk full")
        super(FailingStore, self).save(data)


def text_prefix(key):
    return connect.say(key, *(["\x00"] * connect.MSG[key][0].count("%s"))).split("\x00")[0]


class ConnectTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeServer()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.fake))
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        self.dir = tempfile.mkdtemp(prefix="ga-connect-test-")

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def enroll(self):
        rc = connect.main(["enroll", self.fake.code, "--server", self.base, "--storage", "file", "--dir", self.dir])
        self.assertEqual(rc, 0)
        cfg, _ = connect.load_cfg(self.dir, self.fake.grant)
        return cfg

    def connector(self, cfg, wall_skew=0.0):
        clock = connect.Clock(wall=lambda: time.time() + wall_skew)
        store = connect.make_store("file", self.dir, cfg["grant"])
        return connect.Connector(cfg, store, connect.Http(cfg["server"]), clock)

    def rpc(self, connector, *messages):
        stdin = io.BytesIO(b"".join(json.dumps(m).encode() + b"\n" for m in messages))
        stdout = io.BytesIO()
        connector.run(stdin, stdout)
        return [json.loads(l) for l in stdout.getvalue().decode().splitlines() if l.strip()]

    # ── привязка ──────────────────────────────────────────────────────────────
    def test_enroll_writes_config_and_key_privately_and_burns_the_code(self):
        cfg = self.enroll()
        self.assertEqual((cfg["grant"], cfg["storage"], cfg["scope"]), (self.fake.grant, "file", "read"))
        self.assertEqual(self.fake.storage_seen, "file")
        for name in (self.fake.grant + ".json", self.fake.grant + ".key"):
            mode = os.stat(os.path.join(self.dir, name)).st_mode & 0o777
            self.assertEqual(mode, 0o600, name)
        self.assertEqual(os.stat(self.dir).st_mode & 0o777, 0o700)
        # Код одноразовый: вторая привязка тем же кодом отказывает
        rc = connect.main(["enroll", self.fake.code, "--server", self.base, "--storage", "file",
                           "--dir", tempfile.mkdtemp(prefix="ga-connect-2-")])
        self.assertEqual(rc, 3)

    def test_code_is_normalized_and_never_sent_itself(self):
        self.assertEqual(connect.enroll_path("ga-abcde fghij-klmno-pqrst"), connect.enroll_path(self.fake.code))
        self.assertNotIn(connect.normalize_code(self.fake.code), connect.enroll_path(self.fake.code))

    # ── работа ────────────────────────────────────────────────────────────────
    def test_requests_go_through_and_the_key_rotates_on_disk(self):
        cfg = self.enroll()
        first_key = self.fake.cur
        out = self.rpc(self.connector(cfg),
                       {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                       {"jsonrpc": "2.0", "method": "notifications/initialized"},
                       {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual([m["id"] for m in out], [1, 2], "на уведомление в stdout ничего не пишется")
        self.assertEqual(out[1]["result"]["echo"], "tools/list")
        stored = connect.FileStore(os.path.join(self.dir, cfg["grant"] + ".key")).load()
        self.assertEqual(stored["key"], self.fake.cur)
        self.assertNotEqual(stored["key"], first_key)
        self.assertEqual(stored["pending_nonce"], "")
        self.assertFalse(self.fake.clone)

    def test_client_clock_an_hour_off_still_works(self):
        cfg = self.enroll()
        for skew in (3600, -3600):
            out = self.rpc(self.connector(cfg, wall_skew=skew),
                           {"jsonrpc": "2.0", "id": 7, "method": "tools/list"})
            self.assertEqual(out[0]["result"]["echo"], "tools/list", skew)
        self.assertFalse(self.fake.clone)

    def test_server_clock_jump_is_healed_by_one_retry(self):
        cfg = self.enroll()
        conn = self.connector(cfg)
        self.assertEqual(self.rpc(conn, {"jsonrpc": "2.0", "id": 1, "method": "ping"})[0]["id"], 1)
        # Часы сервера прыгнули на три отрезка: старый путь промахнётся, Date из отказа
        # даст новый отрезок, и повтор той же попытки пройдёт.
        self.fake.skew = 180
        before = self.fake.requests
        out = self.rpc(conn, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual(out[0]["result"]["echo"], "tools/list")
        self.assertEqual(self.fake.requests - before, 2, "ровно один повтор")
        self.assertFalse(self.fake.clone)
        self.assertEqual(self.fake.executed.count("tools/list"), 1)

    def test_lost_reply_is_recovered_without_clone_or_double_execution(self):
        cfg = self.enroll()
        conn = self.connector(cfg)
        self.rpc(conn, {"jsonrpc": "2.0", "id": 1, "method": "ping"})
        # Сервер выполнил и повернул цепочку, а ответ до переходника «не доехал»:
        # на диске остался прежний ключ с номером этой попытки.
        key_before, executed_before = self.fake.cur, list(self.fake.executed)
        nonce = "pending-" + new_secret()[:20]
        conn.store.save({"key": key_before, "pending_nonce": nonce})
        # сервер получил запрос с этой парой и сохранил ответ
        conn.state = conn.store.load()
        status, headers, _ = conn._attempt(json.dumps({"jsonrpc": "2.0", "id": 9, "method": "danger"}).encode(),
                                           key_before, nonce)
        self.assertEqual(status, 200)
        conn.store.save({"key": key_before, "pending_nonce": nonce})     # ответ потерян
        restarted = self.connector(cfg)
        out = self.rpc(restarted, {"jsonrpc": "2.0", "id": 10, "method": "tools/list"})
        self.assertEqual(out[0]["result"]["echo"], "tools/list")
        self.assertFalse(self.fake.clone, "восстановление не должно выглядеть кражей")
        self.assertEqual(self.fake.executed.count("danger"), 1, "действие не выполнено дважды")

    def test_second_client_on_the_same_grant_is_refused_clearly(self):
        cfg = self.enroll()
        lock = connect.acquire_lock(os.path.join(self.dir, cfg["grant"] + ".lock"))
        try:
            with self.assertRaises(connect.Busy):
                connect.acquire_lock(os.path.join(self.dir, cfg["grant"] + ".lock"))
            err = io.StringIO()
            real = sys.stderr
            sys.stderr = err
            try:
                rc = connect.main(["--grant", cfg["grant"], "--dir", self.dir])
            finally:
                sys.stderr = real
            self.assertEqual(rc, 2)
            self.assertTrue("занят" in err.getvalue() or "in use" in err.getvalue(), err.getvalue())
        finally:
            os.close(lock)

    def test_refusal_becomes_a_readable_rpc_error(self):
        cfg = self.enroll()
        self.fake.clone = True
        out = self.rpc(self.connector(cfg), {"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
        self.assertEqual(out[0]["id"], 3)
        self.assertIn("404", out[0]["error"]["message"])

    def test_unreachable_server_answers_every_request_with_an_error(self):
        cfg = self.enroll()
        cfg = dict(cfg, server="http://127.0.0.1:9")    # закрытый порт
        out = self.rpc(self.connector(cfg), {"jsonrpc": "2.0", "id": 4, "method": "tools/list"})
        self.assertEqual(out[0]["id"], 4)
        self.assertIn("error", out[0])

    def test_status_prints_no_secrets(self):
        cfg = self.enroll()
        buf = io.StringIO()
        real = sys.stdout
        sys.stdout = buf
        try:
            self.assertEqual(connect.main(["status", "--grant", cfg["grant"], "--dir", self.dir]), 0)
        finally:
            sys.stdout = real
        text = buf.getvalue()
        for secret in (cfg["token"], cfg["rot_seed"], self.fake.cur):
            self.assertNotIn(secret, text)
        self.assertEqual(json.loads(text)["storage"], "file")

    def test_synced_folder_is_refused(self):
        synced = os.path.join(self.dir, "Dropbox", "keys")
        rc = connect.main(["enroll", self.fake.code, "--server", self.base, "--storage", "file", "--dir", synced])
        self.assertEqual(rc, 2)
        self.assertFalse(self.fake.code_used, "код не должен сгореть при отказе в каталоге")

    def test_plain_http_to_a_remote_host_is_refused_before_anything_is_sent(self):
        """Обычный http — только до петли (туннель): иначе токен, ключ и ответ на привязку
        шли бы по сети открытым текстом. Отказ — до запроса, и код не сгорает."""
        with self.assertRaises(ValueError):
            connect.Http("http://203.0.113.5:1986")
        for fine in ("http://127.0.0.1:1986", "http://localhost:1986", "http://[::1]:1986", "https://203.0.113.5"):
            connect.Http(fine)
        rc = connect.main(["enroll", self.fake.code, "--server", "http://203.0.113.5:1986", "--storage", "file",
                           "--dir", self.dir])
        self.assertEqual(rc, 2)
        self.assertFalse(self.fake.code_used)
        self.assertEqual(self.fake.requests, 0)

    # ── разбор критика 13.09.2026 ────────────────────────────────────────────
    def test_reply_lost_inside_a_running_connector_is_collected_before_the_next_call(self):
        """Ответ оборвался посреди сессии (таймаут, сон ноутбука, 502 прокси): следующий вызов
        сперва забирает его тем же ключом и номером, а не приходит «копией» доступа."""
        cfg = self.enroll()
        self.fake.drop_reply = True
        out = self.rpc(self.connector(cfg),
                       {"jsonrpc": "2.0", "id": 1, "method": "danger"},
                       {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual(out[0]["result"]["echo"], "danger", "потерянный ответ забран повтором той же попытки")
        self.assertEqual(out[1]["result"]["echo"], "tools/list")
        self.assertFalse(self.fake.clone, "потерянный ответ не должен выглядеть кражей")
        self.assertEqual(self.fake.executed.count("danger"), 1, "действие не выполнено дважды")

    def test_next_key_is_taken_even_from_a_400(self):
        cfg = self.enroll()
        self.fake.status_next = 400
        out = self.rpc(self.connector(cfg),
                       {"jsonrpc": "2.0", "id": 1, "method": "odd"},
                       {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual(out[-1]["result"]["echo"], "tools/list")
        self.assertFalse(self.fake.clone, "ключ из ответа 400 тоже следующий")

    def test_unreadable_key_storage_stops_the_connector_and_keeps_the_file(self):
        cfg = self.enroll()
        key_file = os.path.join(self.dir, cfg["grant"] + ".key")
        with open(key_file, "wb") as fh:
            fh.write(b"{not json")
        rc = connect.main(["--grant", cfg["grant"], "--dir", self.dir])
        self.assertEqual(rc, 2)
        with open(key_file, "rb") as fh:
            self.assertEqual(fh.read(), b"{not json", "нечитаемое хранилище нельзя затирать пустым ключом")
        self.assertEqual(self.fake.requests, 1, "после привязки к серверу никто не ходил")

    def test_enroll_while_the_same_grant_runs_is_staged_and_activates_on_next_start(self):
        """Перевыпуск при открытом клиенте MCP: прежний переходник держит замок и пишет свой ключ.
        Привязка не теряется (код уже сгорел), а откладывается до следующего запуска."""
        lock = connect.acquire_lock(os.path.join(self.dir, self.fake.grant + ".lock"))
        try:
            rc = connect.main(["enroll", self.fake.code, "--server", self.base, "--storage", "file",
                               "--dir", self.dir])
        finally:
            os.close(lock)
        self.assertEqual(rc, 0)
        for name in (".json.new", ".key.new"):
            self.assertTrue(os.path.exists(os.path.join(self.dir, self.fake.grant + name)), name)
        self.assertFalse(os.path.exists(os.path.join(self.dir, self.fake.grant + ".json")))
        connect._promote_staged(self.dir, self.fake.grant)
        cfg, _ = connect.load_cfg(self.dir, self.fake.grant)
        self.assertEqual(cfg["rot_seed"], self.fake.rot_seed)
        self.assertFalse(os.path.exists(os.path.join(self.dir, self.fake.grant + ".json.new")))
        out = self.rpc(self.connector(cfg), {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertEqual(out[0]["result"]["echo"], "tools/list")

    def test_a_running_connector_of_another_grant_does_not_block_enrollment(self):
        lock = connect.acquire_lock(os.path.join(self.dir, "a-9999.lock"))
        try:
            rc = connect.main(["enroll", self.fake.code, "--server", self.base, "--storage", "file",
                               "--dir", self.dir])
        finally:
            os.close(lock)
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.exists(os.path.join(self.dir, self.fake.grant + ".json")))

    def test_cloud_storage_and_symlinked_synced_folders_are_refused(self):
        cloud = os.path.join(self.dir, "Library", "CloudStorage", "GoogleDrive-me", "My Drive", "keys")
        rc = connect.main(["enroll", self.fake.code, "--server", self.base, "--storage", "file", "--dir", cloud])
        self.assertEqual(rc, 2)
        real = os.path.join(self.dir, "Dropbox", "keys")
        os.makedirs(real)
        link = os.path.join(self.dir, "plain-looking")
        os.symlink(real, link)
        rc = connect.main(["enroll", self.fake.code, "--server", self.base, "--storage", "file", "--dir", link])
        self.assertEqual(rc, 2)
        self.assertFalse(self.fake.code_used)

    def test_status_names_the_full_path_for_the_mcp_client(self):
        cfg = self.enroll()
        buf = io.StringIO()
        real = sys.stdout
        sys.stdout = buf
        try:
            rc = connect.main(["status", "--grant", cfg["grant"], "--dir", self.dir])
        finally:
            sys.stdout = real
        self.assertEqual(rc, 0)
        conf = json.loads(buf.getvalue())["mcp_client_config"]
        self.assertTrue(os.path.isabs(conf["command"]), conf)
        self.assertEqual(conf["args"], [os.path.abspath(connect.__file__), "--grant", cfg["grant"],
                                        "--dir", os.path.abspath(self.dir)])

    def test_recovery_refused_before_the_chain_keeps_the_pending_attempt(self):
        """Повтор оборванной попытки получил 404 до цепочки (бан, выключенный доступ, привязка к
        адресу): номер обязан остаться, иначе следующий вызов придёт прежним ключом — «копией»."""
        cfg = self.enroll()
        conn = self.connector(cfg)
        self.fake.drop_reply = True
        self.fake.refuse_after_drop = True
        out = self.rpc(conn, {"jsonrpc": "2.0", "id": 1, "method": "danger"})
        self.assertIn("error", out[0])
        self.assertTrue(conn.store.load().get("pending_nonce"), "номер оборванной попытки сохранён")
        out = self.rpc(conn, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertIn("error", out[0], "пока сервер отказывает, новых запросов нет")
        self.assertTrue(conn.store.load().get("pending_nonce"))
        self.fake.refuse_all = False
        self.fake.refuse_after_drop = False
        out = self.rpc(conn, {"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
        self.assertEqual(out[0]["result"]["echo"], "tools/list")
        self.assertFalse(self.fake.clone, "ни одного прихода «копией»")
        self.assertEqual(self.fake.executed.count("danger"), 1)

    def test_a_failed_write_never_lets_memory_run_ahead_of_disk(self):
        cfg = self.enroll()


        store = FailingStore(os.path.join(self.dir, cfg["grant"] + ".key"))
        conn = connect.Connector(cfg, store, connect.Http(cfg["server"]))
        self.rpc(conn, {"jsonrpc": "2.0", "id": 1, "method": "ping"})
        # Сбой ровно на записи нового ключа — после того как сервер уже повернул цепочку
        FailingStore.fail_on = FailingStore.saves + 2
        out = self.rpc(conn, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                       {"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
        self.assertEqual(len(out), 1, "после сбоя записи переходник останавливается")
        self.assertEqual(out[0]["result"]["echo"], "tools/list", "выполненное действие отдаётся клиенту")
        self.assertEqual(conn.state, store.load(), "память не обогнала диск")
        self.assertTrue(conn.state.get("pending_nonce"))
        out = self.rpc(self.connector(cfg), {"jsonrpc": "2.0", "id": 4, "method": "tools/list"})
        self.assertEqual(out[0]["result"]["echo"], "tools/list")
        self.assertFalse(self.fake.clone)

    def test_crash_before_the_chain_is_written_does_not_run_the_action_twice(self):
        """Служба перезапустилась посреди запроса: действие выполнено, цепочка не записана. Повтор
        идёт безвредным телом — исходное действие второй раз не выполняется, модели честно
        говорится, что исход неизвестен."""
        cfg = self.enroll()
        self.fake.crash_before_rotate = True
        out = self.rpc(self.connector(cfg), {"jsonrpc": "2.0", "id": 1, "method": "danger"},
                       {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertIn("error", out[0])
        self.assertEqual(self.fake.executed.count("danger"), 1, "действие не выполнено вторым разом")
        self.assertEqual(out[1]["result"]["echo"], "tools/list")
        self.assertFalse(self.fake.clone)

    def test_a_newer_direct_enrollment_removes_the_stale_staged_one(self):
        lock = connect.acquire_lock(os.path.join(self.dir, self.fake.grant + ".lock"))
        try:
            self.assertEqual(connect.main(["enroll", self.fake.code, "--server", self.base, "--storage", "file",
                                           "--dir", self.dir]), 0)
        finally:
            os.close(lock)
        # Доступ перевыпущен ещё раз, клиент закрыт — привязка идёт напрямую
        self.fake.code_used = False
        self.fake.rot_seed, self.fake.token = new_secret(), new_secret()
        self.assertEqual(connect.main(["enroll", self.fake.code, "--server", self.base, "--storage", "file",
                                       "--dir", self.dir]), 0)
        for name in (".json.new", ".key.new"):
            self.assertFalse(os.path.exists(os.path.join(self.dir, self.fake.grant + name)), name)
        connect._promote_staged(self.dir, self.fake.grant)
        cfg, _ = connect.load_cfg(self.dir, self.fake.grant)
        self.assertEqual(cfg["rot_seed"], self.fake.rot_seed, "устаревшая отложенная привязка не вернулась")

    def test_an_answer_without_id_is_matched_to_the_request(self):
        cfg = self.enroll()
        self.fake.null_id_next = True
        out = self.rpc(self.connector(cfg), {"jsonrpc": "2.0", "id": 7, "method": "tools/list"})
        self.assertEqual(out[0]["id"], 7)
        self.assertIn("not kept", out[0]["error"]["message"])

    def test_unreachable_server_is_not_waited_for_again_and_again(self):
        """Молча недоступный сервер (соединение не устанавливается) — после первого отказа его 30 с не
        ждут заново.

        Тайм-аут соединения подставной, в сеть проверка не ходит. Настоящий адрес без маршрута
        (10.255.255.1) вёл себя по-разному в разных сетях: песочница CI, которая отвергает исходящие
        (RST, ICMP), давала ConnectionRefusedError, а у отказа своя быстрая ветка без паузы — и проверка
        краснела на сети машины, а не на переходнике."""
        cfg = self.enroll()
        cfg = dict(cfg, server="https://10.255.255.1:9")   # http вне петли переходник не принимает
        conn = self.connector(cfg)

        class Silent(object):
            """Соединение, которое не устанавливается: connect() ждёт и сдаётся по тайм-ауту."""
            sock = None

            def connect(self):
                raise socket.timeout("timed out")

            def close(self):
                pass

        conn.http._connection = Silent
        attempts = []
        real = conn.http.request

        def counting(*args, **kwargs):
            attempts.append(args[0])
            return real(*args, **kwargs)

        conn.http.request = counting
        out = self.rpc(conn, {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
                       {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual([o["id"] for o in out], [1, 2])
        self.assertTrue(all("error" in o for o in out))
        self.assertEqual(len(attempts), 1, "после первого отказа соединения сервер не ждут заново: %s" % attempts)
        self.assertEqual(conn.store.load().get("pending_nonce"), "", "не ушедший запрос номера не оставляет")

    def test_gateway_error_after_execution_returns_the_real_answer(self):
        """502/504 от nginx после выполненного и записанного действия — тот же обрыв: повтор забирает
        сохранённый ответ, а не отдаёт модели «HTTP 502» приглашением повторить."""
        cfg = self.enroll()
        self.fake.gateway_next = 502
        out = self.rpc(self.connector(cfg), {"jsonrpc": "2.0", "id": 1, "method": "danger"},
                       {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual(out[0]["result"]["echo"], "danger")
        self.assertEqual(out[1]["result"]["echo"], "tools/list")
        self.assertEqual(self.fake.executed.count("danger"), 1)
        self.assertFalse(self.fake.clone)

    def test_gateway_error_before_the_chain_is_written_says_outcome_unknown(self):
        cfg = self.enroll()
        self.fake.gateway_crash = 504
        out = self.rpc(self.connector(cfg), {"jsonrpc": "2.0", "id": 1, "method": "danger"},
                       {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertTrue(out[0]["error"]["message"].startswith(text_prefix("outcome_lost")), out[0])
        self.assertEqual(self.fake.executed.count("danger"), 1, "действие не выполнено вторым разом")
        self.assertEqual(out[1]["result"]["echo"], "tools/list")
        self.assertFalse(self.fake.clone)

    def test_refusal_on_the_replay_is_reported_as_unknown_outcome(self):
        """Повтор оборванной попытки получил отказ (404/429): модели — «исход неизвестен», а не
        «слишком часто» или «отказано», которые читаются как «повтори»."""
        cfg = self.enroll()
        self.fake.drop_reply = True
        self.fake.refuse_after_drop = True
        out = self.rpc(self.connector(cfg), {"jsonrpc": "2.0", "id": 1, "method": "danger"})
        self.assertTrue(out[0]["error"]["message"].startswith(text_prefix("outcome_unknown")), out[0])

    def test_empty_replay_with_a_failed_write_still_answers_the_request(self):
        cfg = self.enroll()
        store = FailingStore(os.path.join(self.dir, cfg["grant"] + ".key"))
        conn = connect.Connector(cfg, store, connect.Http(cfg["server"]))
        self.fake.crash_before_rotate = True
        FailingStore.fail_on = FailingStore.saves + 2   # запись номера — да, запись ключа после повтора — нет
        out = self.rpc(conn, {"jsonrpc": "2.0", "id": 1, "method": "danger"},
                       {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual(len(out), 1, "после сбоя записи переходник останавливается")
        self.assertEqual(out[0]["id"], 1)
        self.assertTrue(out[0]["error"]["message"].startswith(text_prefix("outcome_lost")), out[0])

    def test_refused_port_during_replay_is_retried_once_without_backoff(self):
        """Прямой порт, служба перезапускается: повтор упирается в закрытый порт — ещё одна попытка
        через пару секунд возвращает настоящий ответ; мгновенный отказ паузу не включает."""
        cfg = self.enroll()
        conn = self.connector(cfg)
        saved_delay = connect.REPLAY_RETRY_DELAY
        connect.REPLAY_RETRY_DELAY = 0.1
        self.addCleanup(setattr, connect, "REPLAY_RETRY_DELAY", saved_delay)
        real = conn.http.request
        state = {"refused": False}

        def flaky(method, path, body=None, headers=None, read_timeout=None):
            if method == "POST" and body == connect.RECOVERY_BODY and not state["refused"]:
                state["refused"] = True
                raise connect.NotSent("connection refused", fast=True)
            return real(method, path, body=body, headers=headers, read_timeout=read_timeout)

        conn.http.request = flaky
        self.fake.drop_reply = True
        out = self.rpc(conn, {"jsonrpc": "2.0", "id": 1, "method": "danger"})
        self.assertTrue(state["refused"])
        self.assertEqual(out[0]["result"]["echo"], "danger")
        self.assertEqual(conn._down_until, 0.0, "мгновенный отказ паузу не включил")
        self.assertEqual(self.fake.executed.count("danger"), 1)

    def test_certificate_change_during_replay_is_reported_as_unknown_outcome(self):
        """Ответ потерян, а повтор упёрся в чужой отпечаток (сертификат перевыпущен посреди долгого
        действия): модели — «исход неизвестен», а не «сервер недоступен», которое читается как «повтори»."""
        cfg = self.enroll()
        conn = self.connector(cfg)
        real = conn.http.request

        def swapped(method, path, body=None, headers=None, read_timeout=None):
            if method == "POST" and body == connect.RECOVERY_BODY:
                raise connect.FingerprintMismatch("fingerprint changed")
            return real(method, path, body=body, headers=headers, read_timeout=read_timeout)

        conn.http.request = swapped
        self.fake.drop_reply = True
        out = self.rpc(conn, {"jsonrpc": "2.0", "id": 1, "method": "danger"})
        self.assertTrue(out[0]["error"]["message"].startswith(text_prefix("outcome_unknown")), out[0])
        self.assertEqual(self.fake.executed.count("danger"), 1)
        self.assertTrue(conn.store.load().get("pending_nonce"), "номер остаётся: следующий вызов заберёт ответ")

    def test_synced_folder_check_ignores_letter_case(self):
        rc = connect.main(["enroll", self.fake.code, "--server", self.base, "--storage", "file",
                           "--dir", os.path.join(self.dir, "dropbox", "keys")])
        self.assertEqual(rc, 2)
        self.assertFalse(self.fake.code_used)

    def test_connection_waits_briefly_and_the_answer_long(self):
        self.assertLess(connect.CONNECT_TIMEOUT, 60)
        self.assertGreater(connect.TIMEOUT, 310)
        self.assertEqual(connect.Http(self.base)._connection().timeout, connect.CONNECT_TIMEOUT)

    def test_float_path_matches_the_server_formula(self):
        seed = new_secret()
        p = connect.float_path(seed, 12345)
        self.assertRegex(p, r"^[A-Za-z0-9_-]{43}$")
        self.assertNotEqual(p, connect.float_path(seed, 12346))
        self.assertNotEqual(p, connect.float_path(new_secret(), 12345))


if __name__ == "__main__":
    unittest.main()
