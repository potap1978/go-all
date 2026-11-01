"""Первый pin_ip: реальные HTTP-запросы на петле, синтетический мост и временный реестр."""
import concurrent.futures
import fcntl
import http.client
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("mcp_pin_server", ROOT / "goamnezia-mcp/server.py")
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
SECRET, TOKEN = "S" * 43, "T" * 43


class Quiet:
    def __getattr__(self, _):
        return lambda *args, **kwargs: None


class Recorder:
    """Журнал транспорта и реестра: уровень и текст каждой строки."""

    def __init__(self):
        self.lines = []

    def __getattr__(self, level):
        return lambda text, *args, **kwargs: self.lines.append((level, str(text)))


class Bridge:
    def __init__(self):
        self.calls = []
        self.lock = threading.Lock()

    def run(self, action, argv, **kwargs):
        with self.lock:
            self.calls.append(action)
        return {"status": "success", "message": "fixture"}


class PinRace(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ga-pin-race-")
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        self.path = self.dir / "agents.json"
        self.path.write_text(json.dumps({"agents": [{"id": "a-1234", "secret": SECRET,
            "token": TOKEN, "require_token": True, "scope": "read", "enabled": True,
            "pin_ip": True, "pinned_ip": "", "expires": "", "allow_ips": []}]}))
        settings = M.Settings({"GOAMNEZIA_DIR": str(self.dir), "GOAMNEZIA_LOCK_FILE": str(self.dir / "lock"),
                               "GOAMNEZIA_ACTIONS_JSON": str(ROOT / "lib/actions.json")},
                              str(ROOT / "goamnezia-mcp"))
        catalog = M.Catalog(json.loads((ROOT / "lib/actions.json").read_text()), "ru", Quiet())
        self.log = Recorder()
        self.store = M.GrantStore(str(self.path), settings.lock_file, self.log)
        self.bridge = Bridge()
        self.transport = M.HttpTransport(settings, catalog, self.bridge, Quiet(), self.log, self.store,
                                         notifier=Quiet())
        self.headers = {}
        self.httpd = M._http_classes()(("127.0.0.1", 0), self.transport)
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.status_tool = catalog.tools_by_cli["status"].name
        self.write_tool = catalog.tools_by_cli["user-add"].name

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(2)

    def record(self):
        return json.loads(self.path.read_text())["agents"][0]

    def rpc(self, ip, method="tools/call", params=None, token=TOKEN):
        params = {"name": self.status_tool, "arguments": {}} if params is None else params
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=5)
        try:
            conn.request("POST", "/a-%s/mcp" % SECRET, body,
                         {"Authorization": "Bearer " + token, "X-Real-IP": ip, "Content-Type": "application/json"})
            response = conn.getresponse()
            self.headers = dict(response.getheaders())
            return response.status, response.read()
        finally:
            conn.close()

    def race(self, ips):
        # Обе проверки гарантированно видят исходный пустой pin: тест не зависит от планировщика.
        original = self.store.authenticate
        barrier = threading.Barrier(2)
        def authenticate(*args, **kwargs):
            result = original(*args, **kwargs)
            barrier.wait(timeout=4)
            return result
        with mock.patch.object(self.store, "authenticate", side_effect=authenticate):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(self.rpc, ip) for ip in ips]
                return [f.result(timeout=6) for f in futures]

    def test_two_first_ips_execute_exactly_one_rpc(self):
        ips = ["203.0.113.1", "203.0.113.2"]
        replies = self.race(ips)
        self.assertEqual(sorted(status for status, _ in replies), [200, 404])
        self.assertEqual(len(self.bridge.calls), 1)
        winner = ips[next(i for i, reply in enumerate(replies) if reply[0] == 200)]
        self.assertEqual(self.record()["pinned_ip"], winner)
        self.assertEqual(self.rpc(winner)[0], 200)

    def test_two_first_requests_from_same_ip_both_execute(self):
        self.assertEqual([r[0] for r in self.race(["203.0.113.1"] * 2)], [200, 200])
        self.assertEqual(len(self.bridge.calls), 2)

    def test_auth_failure_does_not_claim_pin(self):
        self.assertEqual(self.rpc("203.0.113.1", token="bad-token")[0], 404)
        self.assertEqual(self.record()["pinned_ip"], "")
        self.assertEqual(self.rpc("203.0.113.2")[0], 200)
        self.assertEqual(self.record()["pinned_ip"], "203.0.113.2")

    def test_scope_failure_does_not_claim_pin(self):
        status, body = self.rpc("203.0.113.1", params={"name": self.write_tool, "arguments": {}})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["error"]["code"], M.FORBIDDEN)
        self.assertEqual(self.record()["pinned_ip"], "")
        self.assertFalse(self.bridge.calls)

    def test_logs_scope_failure_does_not_claim_pin(self):
        name = self.transport.catalog.tools_by_cli["logs-tail"].name
        status, body = self.rpc("203.0.113.1", params={"name": name, "arguments": {"component": "system"}})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["error"]["code"], M.FORBIDDEN)
        self.assertEqual(self.record()["pinned_ip"], "")

    def test_rate_failure_does_not_claim_pin(self):
        with mock.patch.object(self.transport.rates, "allow", return_value=(False, M.HTTP_RATE_CALLS)):
            self.assertEqual(self.rpc("203.0.113.1")[0], 429)
        self.assertEqual(self.record()["pinned_ip"], "")
        self.assertFalse(self.bridge.calls)

    def test_failed_pin_write_never_executes_rpc(self):
        with mock.patch.object(M.os, "replace", side_effect=OSError("synthetic write failure")):
            self.assertEqual(self.rpc("203.0.113.1")[0], 404)
        self.assertEqual(self.record()["pinned_ip"], "")
        self.assertFalse(self.bridge.calls)

    def test_unavailable_lock_never_executes_rpc(self):
        # Замок не открылся — первое закрепление не записать: вызов не выполняется, но это «повторите»
        # (503), а не отказ по адресу (SEC46-01).
        with mock.patch.object(self.store, "_flock", return_value=None):
            self.assertEqual(self.rpc("203.0.113.1")[0], 503)
        self.assertEqual(self.record()["pinned_ip"], "")
        self.assertFalse(self.bridge.calls)

    def test_revocation_between_auth_and_claim_never_executes_rpc(self):
        original = self.store.authenticate
        def authenticate(*args, **kwargs):
            result = original(*args, **kwargs)
            doc = json.loads(self.path.read_text())
            doc["agents"][0]["enabled"] = False
            self.path.write_text(json.dumps(doc))
            return result
        with mock.patch.object(self.store, "authenticate", side_effect=authenticate):
            self.assertEqual(self.rpc("203.0.113.1")[0], 404)
        self.assertEqual(self.record()["pinned_ip"], "")
        self.assertFalse(self.bridge.calls)

    # ── SEC46-01: config.lock держит долгое действие моста (установка профиля, восстановление) —
    #    закреплённый агент работает, первое закрепление отвечает 503, а не 404. ──────────────────
    def pin(self, ip):
        doc = json.loads(self.path.read_text())
        doc["agents"][0]["pinned_ip"] = ip
        self.path.write_text(json.dumps(doc))

    def hold_lock(self):
        """Замок берёт сам тест — как мост на время пишущего действия."""
        fd = os.open(str(self.dir / "lock"), os.O_RDWR | os.O_CREAT, 0o600)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX)
        return fd

    def pin_refusals(self, ip):
        """Строки журнала, где вызов с этого адреса записан отказом pin или незакреплённым адресом."""
        return [text for _level, text in self.log.lines
                if ip in text and ("отказ pin" in text or "не закреплён" in text)]

    def test_pinned_agent_is_served_while_action_holds_lock(self):
        ip = "203.0.113.1"
        self.pin(ip)
        with mock.patch.object(M, "HTTP_LOCK_WAIT", 0.05):
            self.hold_lock()
            self.assertEqual(self.rpc(ip, method="ping", params={})[0], 200)
            self.assertEqual(self.rpc(ip, method="tools/list", params={})[0], 200)
            for _ in range(3):
                status, body = self.rpc(ip)
                self.assertEqual(status, 200)
                self.assertNotIn("error", json.loads(body))
            self.assertEqual(len(self.bridge.calls), 3)
            # Быстрый путь привязку не ослабил: чужой адрес — тот же 404 ещё на authenticate.
            self.assertEqual(self.rpc("203.0.113.2")[0], 404)
        self.assertEqual(len(self.bridge.calls), 3)
        self.assertEqual(self.record()["pinned_ip"], ip)
        self.assertNotIn(ip, self.transport.failures._fails)
        self.assertFalse(self.transport.failures.banned(ip))
        self.assertEqual(self.pin_refusals(ip), [])

    def test_first_claim_under_held_lock_is_retryable_503(self):
        ip = "203.0.113.1"
        with mock.patch.object(M, "HTTP_LOCK_WAIT", 0.05):
            fd = self.hold_lock()
            status, body = self.rpc(ip)
            self.assertEqual(status, 503)
            self.assertEqual(self.headers.get("Retry-After"), str(M.HTTP_BUSY_RETRY_AFTER))
            self.assertEqual(json.loads(body)["error"]["code"], M.INTERNAL_ERROR)
            self.assertEqual(self.rpc(ip, method="ping", params={})[0], 503)
            self.assertEqual(self.record()["pinned_ip"], "")
            self.assertFalse(self.bridge.calls)
            # Не неудача подбора и не отказ pin: запрос доказал и путь, и токен.
            self.assertNotIn(ip, self.transport.failures._fails)
            self.assertEqual([t for _l, t in self.log.lines if "отказ pin" in t], [])
            self.assertTrue([t for _l, t in self.log.lines if "ответ 503" in t])
            fcntl.flock(fd, fcntl.LOCK_UN)
            self.assertEqual(self.rpc(ip)[0], 200)
        self.assertEqual(self.record()["pinned_ip"], ip)
        self.assertEqual(len(self.bridge.calls), 1)

    def test_race_under_lock_released_mid_way_still_has_one_winner(self):
        # Первые два запроса упираются в замок (503), после его снятия гонка двух адресов — один победитель.
        with mock.patch.object(M, "HTTP_LOCK_WAIT", 0.05):
            fd = self.hold_lock()
            self.assertEqual([self.rpc(ip)[0] for ip in ("203.0.113.1", "203.0.113.2")], [503, 503])
            fcntl.flock(fd, fcntl.LOCK_UN)
        replies = self.race(["203.0.113.1", "203.0.113.2"])
        self.assertEqual(sorted(status for status, _ in replies), [200, 404])
        self.assertEqual(len(self.bridge.calls), 1)

    def test_missing_lock_dir_after_reboot(self):
        # /run/goamnezia ещё нет: первое закрепление — 503, закреплённый адрес работает без замка.
        self.store.lock_file = str(self.dir / "run-not-yet" / "config.lock")
        self.assertEqual(self.rpc("203.0.113.1")[0], 503)
        self.assertEqual(self.record()["pinned_ip"], "")
        self.assertFalse(self.bridge.calls)
        self.pin("203.0.113.1")
        self.assertEqual(self.rpc("203.0.113.1")[0], 200)
        self.assertEqual(len(self.bridge.calls), 1)

    def test_revocation_of_pinned_grant_between_auth_and_admission(self):
        # Быстрый путь сверяет свежую запись без замка: отзыв после authenticate вызов не продолжает.
        self.pin("203.0.113.1")
        original = self.store.authenticate
        def authenticate(*args, **kwargs):
            result = original(*args, **kwargs)
            doc = json.loads(self.path.read_text())
            doc["agents"][0]["enabled"] = False
            self.path.write_text(json.dumps(doc))
            return result
        with mock.patch.object(M, "HTTP_LOCK_WAIT", 0.05):
            self.hold_lock()
            with mock.patch.object(self.store, "authenticate", side_effect=authenticate):
                self.assertEqual(self.rpc("203.0.113.1")[0], 404)
        self.assertFalse(self.bridge.calls)

    def test_pin_reset_between_auth_and_admission_claims_again(self):
        # Привязку сбросили после authenticate — это снова первое закрепление, под замком.
        self.pin("203.0.113.1")
        original = self.store.authenticate
        def authenticate(*args, **kwargs):
            result = original(*args, **kwargs)
            self.pin("")
            return result
        with mock.patch.object(self.store, "authenticate", side_effect=authenticate):
            self.assertEqual(self.rpc("203.0.113.1")[0], 200)
        self.assertEqual(self.record()["pinned_ip"], "203.0.113.1")
        self.assertEqual(len(self.bridge.calls), 1)


if __name__ == "__main__":
    unittest.main()
