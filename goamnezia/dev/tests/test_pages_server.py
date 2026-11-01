# -*- coding: utf-8 -*-
"""Служба страниц подключения (lib/pages_server.py) без сервера: очередь к мосту и данные страницы.

Сквозной прогон tests/test_pages_dryrun.sh поднимает службу с настоящим install.sh, но вызовы моста там
быстрые и по одному — гонок и всплесков он не видит. Здесь мост подменён скриптом, который спит заданное
время и пишет в журнал начало и конец каждого вызова. По журналу видно то, что обещает находка SEC-G1
(ревью безопасности 25.09.2026: 60 параллельных GET давали 120 запусков install.sh от root):
  • одновременных вызовов моста на всю службу — не больше BRIDGE_PARALLEL, лишним — BridgeBusy (503);
  • одинаковые запросы ждут один вызов, а вызовы по одному человеку идут строго по одному;
  • в кеш ложится только ответ, за время которого users.json и config.json не менялись;
  • «занят» и сбой запуска моста в кеш не ложатся.
Плюс SEC-G3 (страница 0.4.0–0.4.2 без метки записи и тёзка), SEC-G2 (в данных HTML нет самих ключей) и
вкладка Apple TV (по User-Agent не выбирается никогда).
"""
import base64
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
import pages_server as ps  # noqa: E402

# Подставной мост: спит FAKE_SLEEP секунд, пишет «start|end время действие имя профиль» в журнал и
# отвечает, как install.sh --action=user-show / user-key --json. FAKE_TOUCH — файл, который мост
# «меняет» посреди вызова (как админ, выключивший человека, пока страница грузилась).
FAKE_BRIDGE = r'''#!/usr/bin/env python3
import json, os, sys, time
args = dict(a[2:].split("=", 1) for a in sys.argv[1:] if a.startswith("--") and "=" in a)
action, name, profile = args.get("action", ""), args.get("name", ""), args.get("profile", "")
d = os.environ["FAKE_DIR"]
def log(what):
    fd = os.open(os.path.join(d, "calls.log"), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    os.write(fd, ("%s %.6f %s %s %s\n" % (what, time.time(), action, name, profile)).encode())
    os.close(fd)
log("start")
try:
    sleep = float(open(os.path.join(d, "sleep")).read().strip() or 0)
except OSError:
    sleep = 0.0
half = sleep / 2
time.sleep(half)
touch = os.path.join(d, "touch")
if os.path.exists(touch):
    target = open(touch).read().strip()
    with open(target, "a") as fh:
        fh.write(" ")
    os.unlink(touch)
time.sleep(sleep - half)
fail = os.path.join(d, "fail")
if os.path.exists(fail):
    log("end")
    print("not json")
    sys.exit(1)
if action == "user-show":
    out = {"status": "success", "user": {"name": name, "label": name, "created": "2026-09-20T10:00:00Z",
           "enabled": True, "profiles": ["awg2"], "peers": {"awg2": {"active": True, "has_key": True}}}}
else:
    out = {"status": "success", "conf": "[Interface]\nPrivateKey = FAKE-%s-%s\n" % (name, profile),
           "vpn": "vpn://FAKE-%s-%s" % (name, profile), "line": "2.0", "qr_conf_png_b64": "",
           "qr_vpn_png_b64": [], "min_client": "4.8.12.7"}
log("end")
print(json.dumps(out))
'''


class NullLog(object):
    def __init__(self):
        self.lines = []
        self.lock = threading.Lock()

    def _add(self, level, text):
        with self.lock:
            self.lines.append((level, text))

    def info(self, text):
        self._add("info", text)

    def warn(self, text):
        self._add("warn", text)

    def error(self, text):
        self._add("error", text)


class BridgeTestBase(unittest.TestCase):
    """Каталог продукта с users.json/config.json и подставной мост в нём."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ga-pages-unit.")
        self.gdir = os.path.join(self.tmp, "goamnezia")
        self.fake = os.path.join(self.tmp, "fake")
        os.makedirs(self.gdir)
        os.makedirs(self.fake)
        for name in ("users.json", "config.json"):
            with open(os.path.join(self.gdir, name), "w") as fh:
                fh.write("{}")
        self.bin = os.path.join(self.tmp, "bridge")
        with open(self.bin, "w") as fh:
            fh.write(FAKE_BRIDGE)
        os.chmod(self.bin, stat.S_IRWXU)
        self._env = os.environ.get("FAKE_DIR")
        os.environ["FAKE_DIR"] = self.fake
        # Пороги службы — на время проверки: секунды вместо минут, чтобы прогон шёл быстро
        self._saved = {k: getattr(ps, k) for k in ("BRIDGE_PARALLEL", "BRIDGE_QUEUE_WAIT", "USER_WAIT", "BRIDGE_TIMEOUT")}
        self.log = NullLog()

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(ps, k, v)
        if self._env is None:
            os.environ.pop("FAKE_DIR", None)
        else:
            os.environ["FAKE_DIR"] = self._env
        shutil.rmtree(self.tmp, ignore_errors=True)

    def bridge(self, parallel=3, queue_wait=5.0, user_wait=30.0):
        ps.BRIDGE_PARALLEL = parallel
        ps.BRIDGE_QUEUE_WAIT = queue_wait
        ps.USER_WAIT = user_wait
        ps.BRIDGE_TIMEOUT = 30.0
        return ps.Bridge(self.bin, self.gdir, self.log)

    def set_sleep(self, seconds):
        with open(os.path.join(self.fake, "sleep"), "w") as fh:
            fh.write(str(seconds))

    def calls(self):
        """→ [(начало, конец, действие, имя, профиль)] по журналу подставного моста."""
        try:
            with open(os.path.join(self.fake, "calls.log")) as fh:
                lines = fh.read().splitlines()
        except OSError:
            return []
        starts, out = {}, []
        for line in lines:
            parts = line.split(" ")
            what, ts, rest = parts[0], float(parts[1]), tuple(parts[2:])
            if what == "start":
                starts.setdefault(rest, []).append(ts)
            else:
                out.append((starts[rest].pop(0), ts) + rest)
        return out

    @staticmethod
    def max_overlap(intervals):
        events = sorted([(s, 1) for s, e in intervals] + [(e, -1) for s, e in intervals], key=lambda x: (x[0], x[1]))
        cur = best = 0
        for _, delta in events:
            cur += delta
            best = max(best, cur)
        return best

    @staticmethod
    def run_threads(targets):
        """Запустить все вызовы разом (барьер) и собрать результаты или исключения."""
        results = [None] * len(targets)
        barrier = threading.Barrier(len(targets))

        def worker(i, fn):
            barrier.wait()
            try:
                results[i] = ("ok", fn())
            except Exception as exc:          # noqa: BLE001 — проверка сама разбирает, что пришло
                results[i] = ("exc", exc)

        threads = [threading.Thread(target=worker, args=(i, fn)) for i, fn in enumerate(targets)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        return results


class BridgeCapTests(BridgeTestBase):
    """SEC-G1: общий потолок вызовов моста и быстрый отказ лишним."""

    def test_no_more_than_bridge_parallel_calls_at_once(self):
        br = self.bridge(parallel=3, queue_wait=10.0)
        self.set_sleep(0.4)
        res = self.run_threads([lambda n=n: br.user_show("u%d" % n) for n in range(9)])
        self.assertTrue(all(r[0] == "ok" and r[1][0] is not None for r in res), res)
        calls = self.calls()
        self.assertEqual(len(calls), 9)
        self.assertLessEqual(self.max_overlap([(c[0], c[1]) for c in calls]), 3)

    def test_excess_request_gets_busy_quickly_and_no_process_is_started(self):
        br = self.bridge(parallel=2, queue_wait=0.3)
        self.set_sleep(1.5)
        t0 = time.monotonic()
        res = self.run_threads([lambda n=n: br.user_show("u%d" % n) for n in range(5)])
        elapsed = time.monotonic() - t0
        busy = [r for r in res if r[0] == "exc" and isinstance(r[1], ps.BridgeBusy)]
        ok = [r for r in res if r[0] == "ok"]
        self.assertEqual(len(ok), 2, res)
        self.assertEqual(len(busy), 3, res)
        # Процессов ровно столько, сколько мест: отказанным мост не запускался вовсе
        self.assertEqual(len(self.calls()), 2)
        self.assertLess(elapsed, 5.0)

    def test_busy_is_not_cached(self):
        br = self.bridge(parallel=1, queue_wait=0.2)
        self.set_sleep(1.0)
        res = self.run_threads([lambda: br.user_show("anna"), lambda: br.user_key("bob", "awg2")])
        kinds = sorted(r[0] for r in res)
        self.assertEqual(kinds, ["exc", "ok"], res)
        self.set_sleep(0)
        # Кому не хватило места, тот при следующем запросе получает ответ, а не «занят» из кеша
        show, code = br.user_show("anna")
        key, kcode = br.user_key("bob", "awg2")
        self.assertIsNotNone(show, code)
        self.assertIsNotNone(key, kcode)

    def test_transport_failure_is_not_cached(self):
        br = self.bridge()
        open(os.path.join(self.fake, "fail"), "w").close()
        self.assertEqual(br.user_show("anna"), (None, "transport"))
        os.unlink(os.path.join(self.fake, "fail"))
        # Сбой запуска моста — не ответ о человеке: следующий запрос идёт в мост снова, а не минуту
        # отвечает 404 из кеша
        show, code = br.user_show("anna")
        self.assertIsNotNone(show, code)
        self.assertEqual(len(self.calls()), 2)


class BridgeSingleFlightTests(BridgeTestBase):
    """SEC-G1: одинаковые запросы — один вызов; по одному человеку — строго по одному."""

    def test_identical_requests_share_one_call(self):
        br = self.bridge()
        self.set_sleep(0.5)
        res = self.run_threads([lambda: br.user_show("anna") for _ in range(20)])
        self.assertTrue(all(r[0] == "ok" and r[1][0]["user"]["name"] == "anna" for r in res), res)
        self.assertEqual(len(self.calls()), 1)

    def test_calls_for_one_person_never_overlap(self):
        br = self.bridge(parallel=3)
        self.set_sleep(0.3)
        targets = [lambda: br.user_show("anna"), lambda: br.user_key("anna", "awg1"),
                   lambda: br.user_key("anna", "awg2"), lambda: br.user_key("anna", "awg3")] * 3
        res = self.run_threads(targets)
        self.assertTrue(all(r[0] == "ok" for r in res), res)
        calls = self.calls()
        # Четыре разных ключа кеша — четыре вызова (повторы ждут своих), и ни один не наложился на другой
        self.assertEqual(len(calls), 4)
        self.assertEqual(self.max_overlap([(c[0], c[1]) for c in calls if c[3] == "anna"]), 1)

    def test_different_people_run_in_parallel(self):
        br = self.bridge(parallel=3)
        self.set_sleep(0.6)
        res = self.run_threads([lambda: br.user_show("anna"), lambda: br.user_show("bob"), lambda: br.user_show("eve")])
        self.assertTrue(all(r[0] == "ok" for r in res), res)
        self.assertEqual(self.max_overlap([(c[0], c[1]) for c in self.calls()]), 3)

    def test_waiters_of_a_busy_leader_get_busy_too(self):
        br = self.bridge(parallel=1, queue_wait=0.2)
        self.set_sleep(1.2)
        # Место занято вызовом по bob; три одинаковых запроса по anna ждут одного ведущего, ведущему места
        # не хватает — «занят» получают все трое, а не пробуют мост по очереди
        blocker = threading.Thread(target=lambda: br.user_show("bob"))
        blocker.start()
        time.sleep(0.2)
        res = self.run_threads([lambda: br.user_show("anna") for _ in range(3)])
        blocker.join(10)
        self.assertTrue(all(r[0] == "exc" and isinstance(r[1], ps.BridgeBusy) for r in res), res)
        self.assertEqual([c[3] for c in self.calls()], ["bob"])


class BridgeCacheStampTests(BridgeTestBase):
    """SEC-G1: в кеш — только ответ, за время которого users.json и config.json не менялись."""

    def test_answer_is_cached_when_files_did_not_change(self):
        br = self.bridge()
        br.user_key("anna", "awg2")
        br.user_key("anna", "awg2")
        self.assertEqual(len(self.calls()), 1)

    def test_answer_is_not_cached_when_users_json_changed_during_the_call(self):
        br = self.bridge()
        self.set_sleep(0.4)
        with open(os.path.join(self.fake, "touch"), "w") as fh:
            fh.write(os.path.join(self.gdir, "users.json"))
        first, _ = br.user_key("anna", "awg2")
        self.assertIsNotNone(first)
        # Ключ, выданный до правки (например, до «выключить»), не должен прожить в кеше минуту
        br.user_key("anna", "awg2")
        self.assertEqual(len(self.calls()), 2)

    def test_answer_is_not_cached_when_config_json_changed_during_the_call(self):
        br = self.bridge()
        self.set_sleep(0.4)
        with open(os.path.join(self.fake, "touch"), "w") as fh:
            fh.write(os.path.join(self.gdir, "config.json"))
        br.user_show("anna")
        br.user_show("anna")
        self.assertEqual(len(self.calls()), 2)

    def test_change_after_the_call_drops_the_cache(self):
        br = self.bridge()
        br.user_show("anna")
        time.sleep(0.01)
        with open(os.path.join(self.gdir, "users.json"), "w") as fh:
            fh.write('{"users": []}')
        br.user_show("anna")
        self.assertEqual(len(self.calls()), 2)


class FakePage(object):
    def __init__(self, meta):
        self.meta = meta
        self.id = meta.get("id", "p-abcd")
        self.user = meta.get("user", "anna")
        self.profiles = meta.get("profiles", ["awg2"])
        self.mirror = True
        self.forever = False
        self.expires = meta.get("expires", "")


class LegacyNamesakeTests(unittest.TestCase):
    """SEC-G3: страница 0.4.0–0.4.2 без user_created и тёзка, заведённый после неё."""

    def test_owner_older_than_page_is_accepted(self):
        page = FakePage({"created": "2026-09-18T10:00:00Z"})
        self.assertFalse(ps.App.namesake_of_legacy(page, {"created": "2026-09-01T08:00:00Z"}))

    def test_same_second_is_accepted(self):
        page = FakePage({"created": "2026-09-18T10:00:00Z"})
        self.assertFalse(ps.App.namesake_of_legacy(page, {"created": "2026-09-18T10:00:00Z"}))

    def test_record_younger_than_page_is_a_namesake(self):
        page = FakePage({"created": "2026-09-18T10:00:00Z"})
        self.assertTrue(ps.App.namesake_of_legacy(page, {"created": "2026-09-24T12:00:00Z"}))

    def test_missing_dates_keep_the_old_decision(self):
        self.assertFalse(ps.App.namesake_of_legacy(FakePage({}), {"created": "2026-09-24T12:00:00Z"}))
        self.assertFalse(ps.App.namesake_of_legacy(FakePage({"created": "2026-09-18T10:00:00Z"}), {}))
        self.assertFalse(ps.App.namesake_of_legacy(FakePage({"created": "мусор"}), {"created": "2026-09-24T12:00:00Z"}))


class AppTestBase(BridgeTestBase):
    def app(self):
        pages = os.path.join(self.gdir, "pages")
        mirror = os.path.join(self.gdir, "mirror")
        os.makedirs(pages)
        os.makedirs(mirror)
        opts = {"dir": self.gdir, "audit": "", "clients": os.path.join(ROOT, "lib", "clients.json"),
                "action_bin": self.bin, "pages_dir": pages, "mirror_dir": mirror}
        ps.BRIDGE_TIMEOUT = 30.0
        return ps.App(opts, self.log)


class UserCardTests(AppTestBase):
    """SEC-G3 целиком: карточка тёзки для страницы без метки — отказ (404), для владельца — карточка."""

    def test_namesake_is_refused_and_logged_without_secret(self):
        app = self.app()
        page = FakePage({"id": "p-1a2b", "user": "anna", "created": "2026-09-19T10:00:00Z"})
        page.secret = "S" * 43
        self.assertIsNone(app.user_card(page))
        self.assertTrue(any("заведён позже страницы" in text for _, text in self.log.lines), self.log.lines)
        self.assertFalse(any(page.secret in text for _, text in self.log.lines))

    def test_owner_gets_the_card(self):
        app = self.app()
        page = FakePage({"id": "p-1a2b", "user": "anna", "created": "2026-09-21T10:00:00Z"})
        self.assertEqual(app.user_card(page)["name"], "anna")

    def test_marked_page_uses_the_mark(self):
        app = self.app()
        page = FakePage({"id": "p-1a2b", "user": "anna", "created": "2026-09-21T10:00:00Z",
                         "user_created": "2026-09-01T00:00:00Z"})
        self.assertIsNone(app.user_card(page))


class PageDataTests(AppTestBase):
    """SEC-G2: данные HTML-страницы без самих ключей; ответ keys.json — с ними."""

    def test_html_data_carries_no_keys_and_keys_json_does(self):
        app = self.app()
        page = FakePage({"id": "p-1a2b", "user": "anna", "created": "2026-09-21T10:00:00Z", "profiles": ["awg2"]})
        kind, view = app.page_view(page)
        self.assertEqual(kind, "ok")
        label, profiles, user = view
        data = app.page_data(page, label, profiles, "android", user)
        html = ps.render_html("ru", page, label, data)
        self.assertNotIn("PrivateKey", html)
        self.assertNotIn("vpn://FAKE", html)
        self.assertNotIn('href="key/', html)
        self.assertTrue(all("conf" not in p and "vpn" not in p for p in data["profiles"]), data["profiles"])
        keys = app.keys_data(page)
        self.assertEqual(sorted(keys["keys"]), ["awg2"])
        self.assertIn("PrivateKey = FAKE-anna-awg2", keys["keys"]["awg2"]["conf"])
        self.assertEqual(keys["keys"]["awg2"]["vpn"], "vpn://FAKE-anna-awg2")
        # NC-16: рекомендуемое на телефонах — в данных страницы (ключ линейки 2.0 AmneziaWG понимает)
        self.assertEqual(data["recommend"], {"ios": "amneziawg", "android": "amneziawg"})


class BusyHttpTests(AppTestBase):
    """SEC-G1 на уровне HTTP: «мост занят» — 503 с Retry-After (страница обновится сама), а не 404
    и не двухминутное ожидание; чужой секрет по-прежнему получает 404 nginx."""

    SECRET = "Q" * 43

    def setUp(self):
        super().setUp()
        self.application = self.app()
        pdir = os.path.join(self.gdir, "pages", "p-1a2b")
        os.makedirs(pdir)
        with open(os.path.join(pdir, "meta.json"), "w") as fh:
            json.dump({"id": "p-1a2b", "user": "anna", "created": "2026-09-21T10:00:00Z", "profiles": ["awg2"],
                       "mirror": True, "forever": True, "expires": "", "expires_epoch": 0}, fh)
        with open(os.path.join(pdir, "secret"), "w") as fh:
            fh.write(self.SECRET)

        def busy(*_args):
            raise ps.BridgeBusy()
        self.application.bridge.user_show = busy
        self.httpd = ps.PagesHttpServer(("127.0.0.1", 0), ps.build_handler(self.application), self.log)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05})
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(5)
        super().tearDown()

    def get(self, path):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("GET", path)
        resp = conn.getresponse()
        body = resp.read()
        headers = {k.lower(): v for k, v in resp.getheaders()}
        conn.close()
        return resp.status, headers, body

    def test_page_answers_503_with_retry_after_and_self_refresh(self):
        status, headers, body = self.get("/p-%s/" % self.SECRET)
        self.assertEqual(status, 503)
        self.assertEqual(headers.get("retry-after"), str(ps.BUSY_RETRY_AFTER))
        self.assertIn("no-store", headers.get("cache-control", ""))
        text = body.decode("utf-8")
        self.assertIn('http-equiv="refresh" content="%d"' % ps.BUSY_RETRY_AFTER, text)
        self.assertIn("Сервер занят", text)

    def test_keys_json_answers_503_json(self):
        import http.client
        nonce = self.application.reveal.issue("p-1a2b")
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("POST", "/p-%s/keys.json" % self.SECRET, body=json.dumps({"nonce": nonce}),
                     headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        body = resp.read()
        headers = {k.lower(): v for k, v in resp.getheaders()}
        conn.close()
        self.assertEqual(resp.status, 503)
        self.assertEqual(headers.get("retry-after"), str(ps.BUSY_RETRY_AFTER))
        self.assertEqual(json.loads(body.decode("utf-8"))["error"], "busy")
        # Мост занят — одноразовое значение вернулось: повтор по Retry-After получит ключи (SEC50-05)
        self.assertTrue(self.application.reveal.take("p-1a2b", nonce))

    def test_wrong_secret_is_still_the_nginx_404(self):
        status, headers, body = self.get("/p-%s/" % ("Z" * 43))
        self.assertEqual(status, 404)
        self.assertEqual(body, ps.NGINX_404)
        self.assertNotIn("retry-after", headers)
        self.assertNotIn("content-security-policy", headers)


class RevealNoncesTests(unittest.TestCase):
    """SEC50-05: одноразовое значение для ключей — одной странице, один раз, не дольше REVEAL_TTL."""

    def test_one_page_one_time(self):
        clock = [1000.0]
        nonces = ps.RevealNonces(ttl=60, limit=3, now=lambda: clock[0])
        n = nonces.issue("p-1a2b")
        self.assertRegex(n, r"^[A-Za-z0-9_-]{32}$")
        self.assertFalse(nonces.take("p-ffff", n), "значение чужой страницы")
        self.assertTrue(nonces.take("p-1a2b", n))
        self.assertFalse(nonces.take("p-1a2b", n), "второй раз — нет")
        nonces.give_back("p-1a2b", n)                        # мост был занят — значение вернули
        self.assertTrue(nonces.take("p-1a2b", n))
        for junk in ("", None, 5, "x" * 32 + "!", "short"):
            self.assertFalse(nonces.take("p-1a2b", junk), junk)
        old = nonces.issue("p-1a2b")
        clock[0] += 61
        self.assertFalse(nonces.take("p-1a2b", old), "истекло")
        first = nonces.issue("p-1a2b")
        for _ in range(3):
            nonces.issue("p-1a2b")
        self.assertFalse(nonces.take("p-1a2b", first), "потолок таблицы — самые давние уходят")
        self.assertNotEqual(nonces.issue("p-1a2b"), nonces.issue("p-1a2b"))


class KeyGrantsTests(unittest.TestCase):
    """SEC50-10: токен на файл и QR ключа — одной странице и не дольше GRANT_TTL."""

    def test_one_page_and_expiry(self):
        clock = [1000.0]
        grants = ps.KeyGrants(ttl=60, limit=3, now=lambda: clock[0])
        g = grants.issue("p-1a2b")
        self.assertRegex(g, r"^[A-Za-z0-9_-]{32}$")
        self.assertTrue(grants.valid("p-1a2b", g))
        self.assertTrue(grants.valid("p-1a2b", g), "токен не одноразовый: файл и QR просят не по разу")
        self.assertFalse(grants.valid("p-ffff", g), "токен чужой страницы")
        for junk in ("", None, 5, "x" * 32 + "!", "short"):
            self.assertFalse(grants.valid("p-1a2b", junk), junk)
        clock[0] += 61
        self.assertFalse(grants.valid("p-1a2b", g), "истёк")
        first = grants.issue("p-1a2b")
        for _ in range(3):
            grants.issue("p-1a2b")
        self.assertFalse(grants.valid("p-1a2b", first), "потолок таблицы — самые давние уходят")


class RevealHttpTests(AppTestBase):
    """SEC50-05: ключи — только POST keys.json с одноразовым значением, выданным при отрисовке страницы; app.js
    шлёт его после действия человека. Сканер ссылок с JavaScript отрисовывал страницу, получал conf и vpn://, и
    визит засчитывался. GET keys.json больше ключей не отдаёт, повтор значения — тоже; визит — только при выдаче."""

    SECRET = "R" * 43
    OTHER = "S" * 43

    def setUp(self):
        super().setUp()
        self.application = self.app()
        self.pdirs = {}
        for pid, secret in (("p-1a2b", self.SECRET), ("p-3c4d", self.OTHER)):
            pdir = os.path.join(self.gdir, "pages", pid)
            os.makedirs(pdir)
            with open(os.path.join(pdir, "meta.json"), "w") as fh:
                json.dump({"id": pid, "user": "anna", "created": "2026-09-21T10:00:00Z", "profiles": ["awg2"],
                           "mirror": True, "forever": True, "expires": "", "expires_epoch": 0}, fh)
            with open(os.path.join(pdir, "secret"), "w") as fh:
                fh.write(secret)
            self.pdirs[pid] = pdir
        self.httpd = ps.PagesHttpServer(("127.0.0.1", 0), ps.build_handler(self.application), self.log)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05})
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(5)
        super().tearDown()

    def request(self, method, path, body=None, agent="Mozilla/5.0 (X11; Linux x86_64)"):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"User-Agent": agent}
        if body is not None:
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
        data = resp.read()
        headers = {k.lower(): v for k, v in resp.getheaders()}
        conn.close()
        return resp.status, headers, data

    def nonce_of(self, secret):
        status, _headers, body = self.request("GET", "/p-%s/" % secret)
        self.assertEqual(status, 200)
        html = body.decode("utf-8")
        blob = html.split('<script type="application/json" id="ga-data">', 1)[1].split("</script>", 1)[0]
        data = json.loads(blob)
        self.assertNotIn("PrivateKey", html)
        self.assertNotIn("vpn://FAKE", html)
        return data["reveal"]

    def visits(self, pid="p-1a2b"):
        try:
            with open(os.path.join(self.pdirs[pid], "state.json")) as fh:
                return json.load(fh).get("visits", 0)
        except OSError:
            return 0

    def post_keys(self, secret, nonce, **kw):
        return self.request("POST", "/p-%s/keys.json" % secret, json.dumps({"nonce": nonce}), **kw)

    def test_keys_only_by_post_with_the_page_nonce_and_only_once(self):
        nonce = self.nonce_of(self.SECRET)
        self.assertRegex(nonce, r"^[A-Za-z0-9_-]{32}$")
        self.assertNotEqual(nonce, self.nonce_of(self.SECRET), "у каждой отрисовки своё значение")
        self.assertEqual(self.visits(), 0, "отрисовка — не визит")
        status, headers, body = self.request("GET", "/p-%s/keys.json" % self.SECRET)
        self.assertEqual((status, headers.get("allow")), (405, "POST"))
        self.assertNotIn(b"PrivateKey", body)
        status, _h, _b = self.request("HEAD", "/p-%s/keys.json" % self.SECRET)
        self.assertEqual(status, 405)
        for bad in (json.dumps({}), "not json", json.dumps({"nonce": "x" * 32}), json.dumps(["x"])):
            status, _h, body = self.request("POST", "/p-%s/keys.json" % self.SECRET, bad)
            self.assertEqual(status, 409, bad)
            self.assertNotIn(b"PrivateKey", body)
        status, _h, body = self.post_keys(self.OTHER, nonce)
        self.assertEqual(status, 409, "значение другой страницы")
        status, _h, body = self.post_keys(self.SECRET, nonce, agent="TelegramBot (like TwitterBot)")
        self.assertEqual((status, body), (404, ps.NGINX_404), "роботу превью — никогда")
        self.assertEqual(self.visits(), 0)
        status, headers, body = self.post_keys(self.SECRET, nonce)
        self.assertEqual(status, 200)
        self.assertIn("no-store", headers.get("cache-control", ""))
        keys = json.loads(body.decode("utf-8"))["keys"]
        self.assertIn("PrivateKey = FAKE-anna-awg2", keys["awg2"]["conf"])
        self.assertEqual(self.visits(), 1, "визит — при выдаче ключей")
        status, _h, body = self.post_keys(self.SECRET, nonce)
        self.assertEqual(status, 409, "повтор значения")
        self.assertNotIn(b"PrivateKey", body)
        self.assertEqual(self.visits(), 1)
        self.assertEqual(self.post_keys(self.SECRET, self.nonce_of(self.SECRET))[0], 200)
        self.assertEqual(self.visits(), 2)

    def calls(self):
        try:
            with open(os.path.join(self.fake, "calls.log")) as fh:
                return fh.read().count("start")
        except OSError:
            return 0

    def test_key_file_and_qr_need_the_grant_from_the_keys_answer(self):
        """SEC50-10: key/<профиль>.conf и qr/key/… отдавались любому GET — сканер с адресом страницы брал файл по
        предсказуемому имени мимо POST. Теперь — только с токеном ?g= из ответа POST keys.json; без него — 404 до моста."""
        status, _h, body = self.post_keys(self.SECRET, self.nonce_of(self.SECRET))
        self.assertEqual(status, 200)
        grant = json.loads(body.decode("utf-8"))["grant"]
        self.assertRegex(grant, r"^[A-Za-z0-9_-]{32}$")
        other = json.loads(self.post_keys(self.OTHER, self.nonce_of(self.OTHER))[2].decode("utf-8"))["grant"]
        before = self.calls()
        for query in ("", "?g=", "?g=" + "A" * 32, "?g=" + other, "?x=" + grant, "?g=" + grant + "x"):
            for path in ("key/awg2.conf", "qr/key/awg2/conf.png", "qr/key/awg2/vpn.png", "qr/key/awg2/vpn-2.png"):
                status, headers, body = self.request("GET", "/p-%s/%s%s" % (self.SECRET, path, query))
                self.assertEqual((status, body), (404, ps.NGINX_404), (path, query))
            self.assertEqual(self.request("HEAD", "/p-%s/key/awg2.conf%s" % (self.SECRET, query))[0], 404, query)
        self.assertEqual(self.calls(), before, "без токена служба к мосту не ходит")
        status, headers, body = self.request("GET", "/p-%s/key/awg2.conf?g=%s" % (self.SECRET, grant))
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers.get("content-disposition", ""))
        # B3-6: имя файла — как в SSH и у временной ссылки (ga_key_file_base), а не anna-awg2.conf
        self.assertEqual(headers.get("content-disposition"), 'attachment; filename="anna-2_0.conf"')
        self.assertIn(b"PrivateKey = FAKE-anna-awg2", body)
        self.assertEqual(self.request("GET", "/p-%s/key/awg2.conf?lang=en&g=%s" % (self.SECRET, grant))[0], 200)
        self.assertEqual(self.request("GET", "/p-%s/key/awg2.conf?g=%s" % (self.OTHER, grant))[0], 404,
                         "токен одной страницы не открывает другую")

    def test_an_open_page_renews_its_grant_without_a_visit(self):
        self.post_keys(self.SECRET, self.nonce_of(self.SECRET))
        visits = self.visits()
        grant = self.application.grants.issue("p-1a2b")
        status, _h, body = self.request("POST", "/p-%s/keys.json" % self.SECRET, json.dumps({"grant": grant}))
        self.assertEqual(status, 200)
        fresh = json.loads(body.decode("utf-8"))
        self.assertEqual(sorted(fresh), ["grant"], "продление — без ключей")
        self.assertNotEqual(fresh["grant"], grant)
        self.assertTrue(self.application.grants.valid("p-1a2b", fresh["grant"]))
        self.assertEqual(self.visits(), visits, "продление — не визит")
        for bad in ("A" * 32, self.application.grants.issue("p-3c4d")):
            status, _h, body = self.request("POST", "/p-%s/keys.json" % self.SECRET, json.dumps({"grant": bad}))
            self.assertEqual(status, 409, bad)
            self.assertNotIn(b"PrivateKey", body)

    def test_a_gone_page_stays_the_nginx_404(self):
        nonce = self.nonce_of(self.SECRET)
        self.application.bridge.user_show = lambda *_a: (None, "not_found")
        for value in (nonce, "junk"):
            status, _h, body = self.post_keys(self.SECRET, value)
            self.assertEqual((status, body), (404, ps.NGINX_404), value)
        self.assertEqual(self.visits(), 0)

    def test_the_script_asks_for_keys_only_after_a_human_action(self):
        js = ps.APP_JS
        self.assertIn("method: 'POST'", js)
        self.assertIn("nonce: String(data.reveal", js)
        self.assertIn("keysState = 'hidden'", js)
        self.assertIn("e.isTrusted === false", js)
        tail = js[js.rindex("render();"):]
        self.assertNotIn("fetchKeys()", tail, "после отрисовки ключи сами не запрашиваются")
        self.assertNotIn("revealKeys()", tail)
        self.assertIn("document.addEventListener('click', onHuman, true)", tail)
        self.assertIn("document.addEventListener('keydown', onHuman, true)", tail)
        # ACC5-4: кнопка «Показать ключ» и пункт телевизора — через onHuman (проверка isTrusted), а не revealKeys
        # напрямую: element.click() из скрипта открывал ключи и засчитывал визит
        import re as _re
        self.assertIsNone(_re.search(r"on\([^;]*,\s*revealKeys\)", js), "revealKeys — только из onHuman")
        self.assertIn("t('reveal'), 'key', true, {}, true), onHuman)", js)
        self.assertIn("if (it.reveal) { on(el, onHuman); }", js)
        human = js[js.index("function onHuman(e) {"):]
        human = human[:human.index("}")]
        self.assertIn("e.isTrusted === false", human)
        # SEC50-10: адреса файла и QR ключа — только через keyUrl (с токеном ?g=), ссылка на файл берёт свежий токен
        for m in _re.finditer(r"'(?:qr/)?key/' \+ pr\.id", js):
            around = js[max(0, m.start() - 12):m.start()]
            self.assertTrue(around.endswith("keyUrl(") or around.endswith("keyfile: ") or around.endswith("}), "),
                            js[max(0, m.start() - 60):m.end() + 30])
        self.assertIn("function keyUrl(path) { return path + '?g=' + encodeURIComponent(grant); }", js)
        self.assertIn("setGrant(js.grant)", js)
        self.assertIn("body: JSON.stringify({grant: grant})", js)
        for lang in ("ru", "en"):
            self.assertTrue(ps.UI[lang]["reveal"])
        self.assertEqual(ps.UI["ru"]["reveal"], "Показать ключ")


class KeyFileNameTests(AppTestBase):
    """B3-6, RR-3: страница называет файл ключа так же, как SSH и временная ссылка (ga_key_file_base в
    lib/amnezia.sh; равенство с ним проверяет tests/test_share_dryrun.sh): <имя>-<линия>, линия читаемо и без точки
    в середине имени; имя длиннее 10 знаков — начало и три знака отпечатка, имя туннеля не длиннее 15 знаков."""

    def test_rule(self):
        kb = ps.key_file_base
        self.assertEqual(kb("anna", "awg2", "2.0"), "anna-2_0")
        self.assertEqual(kb("anna", "awg3", "3.1"), "anna-3_1")
        self.assertEqual(kb("anna", "awg2", ""), "anna-awg2", "линию не узнать — имя места")
        self.assertEqual(kb("", "awg2", "2.0"), "key-2_0")
        self.assertEqual(kb("abcdefghij", "awg2", "2.0"), "abcdefghij-2_0", "10 знаков — без отпечатка")
        one, two = kb("verylongusername1", "awg2", "2.0"), kb("verylongusername2", "awg2", "2.0")
        self.assertRegex(one, r"^verylon[0-9a-f]{3}-2_0$")
        self.assertRegex(two, r"^verylon[0-9a-f]{3}-2_0$")
        self.assertNotEqual(one, two, "RR-3: общие первые 10 знаков — разные файлы")
        dup = kb("abcdefghij", "awg3", "2.0", True)
        self.assertRegex(dup, r"^abcdef[0-9a-f]{3}-2_0-3$")
        self.assertEqual(len(dup), 15)
        self.assertEqual(kb("anna", "awg2", "2.0", True), "anna-2_0-2")
        for name in ("anna", "verylongusername1", "abcdefghij", "Ан-на_1", ""):
            for prof, line, d in (("awg2", "2.0", False), ("awg3", "3.1", True), ("awg1", "1.5", False)):
                self.assertLessEqual(len(kb(name, prof, line, d)), 15, (name, prof))
                self.assertNotIn(".", kb(name, prof, line, d))

    def test_two_profiles_of_one_line_get_the_place_number(self):
        app = self.app()
        with open(os.path.join(self.gdir, "config.json"), "w") as fh:
            json.dump({"profiles": {"awg2": {"line": "2.0"}, "awg3": {"line": "2.0"}, "awg1": {"line": "1.5"}}}, fh)
        self.assertEqual(app.key_file_name("anna", "awg2", "2.0"), "anna-2_0-2.conf")
        self.assertEqual(app.key_file_name("anna", "awg3", "2.0"), "anna-2_0-3.conf")
        self.assertEqual(app.key_file_name("anna", "awg1", "1.5"), "anna-1_5.conf")


class PageRenderTests(unittest.TestCase):
    """0.5.0, макет handoff/iteration3/Client.dc.html: вариант без JavaScript, «приостановлен», «Сервер занят» и превью
    собраны теми же классами, что рисует app.js; ключей и ссылок на key/… в них нет никогда (SEC-G2)."""

    NOW = 1790380800                       # 2026-09-26T00:00:00Z

    def data(self, **over):
        data = {"lang": "ru", "user": "anna", "label": "Мама", "id": "p-1a2b", "expires": "2026-10-24T15:40:00Z",
                "forever": False, "device": "android", "now": self.NOW,
                "access": {"used": 13207024435, "quota": 53687091200, "expires": "2026-11-01T00:00:00Z"},
                "platforms": list(ps.PLATFORMS), "lines": ["2.0"],
                "profiles": [{"id": "awg2", "line": "2.0", "qr_conf": True, "qr_vpn": 1, "min_client": "", "seen": 0}],
                "apps": [], "mirror_enabled": True, "qr_links": False, "checked": "2026-09-25", "domain": "vpn.example.ru"}
        data.update(over)
        return data

    @staticmethod
    def noscript(html):
        return html.split("<noscript>", 1)[1].split("</noscript>", 1)[0]

    def test_dictionary_is_the_same_in_both_languages_and_reaches_app_js(self):
        self.assertEqual(set(ps.UI["ru"]), set(ps.UI["en"]))
        self.assertEqual(list(ps.PLATFORM_NAMES["ru"]), list(ps.PLATFORMS))
        self.assertEqual(list(ps.PLATFORM_NAMES["en"]), list(ps.PLATFORMS))
        js = ps.APP_JS_BYTES.decode("utf-8")
        for mark in ("__UI__", "__PLATFORMS__", "__ICONS__"):
            self.assertNotIn(mark, js)
        self.assertIn(json.dumps(ps.UI, ensure_ascii=False), js)

    def test_no_script_shell_is_header_status_and_one_note_without_keys(self):
        html = ps.render_html("ru", FakePage({}), "Мама", self.data())
        shell = self.noscript(html)
        self.assertIn('<h1 class="pg-title">Подключение: Мама</h1>', shell)
        # TAIL-21: без JavaScript пояс посетителя неизвестен — UTC с подписью, момент в <time datetime> и «через …»
        self.assertIn('Страница открывается до <time datetime="2026-10-24T15:40:00Z">24.10.2026 15:40 UTC</time> '
                      '(через 28 дней) — добавьте ключ заранее, VPN будет работать и после', shell)
        self.assertIn('<div class="pg-status__sub">доступ до 01.11.2026 · осталось 36 дней</div>', shell)
        self.assertIn('<span class="pg-status__num">12,3 <span>из 50 ГБ</span></span>', shell)
        self.assertEqual(shell.count('class="ga-card pg-ns"'), 1)
        self.assertIn('href="?lang=en"', shell)
        self.assertNotIn('href="key/', html)
        self.assertNotIn("PrivateKey", html)
        self.assertIn("Сведения о приложениях проверены 25.09.2026", shell)

    def test_english_shell_and_short_page_lifetime(self):
        data = self.data(lang="en", access={"used": 0, "quota": 0, "expires": "2026-10-01T00:00:00Z"})
        shell = self.noscript(ps.render_html("en", FakePage({}), "Mom", data))
        self.assertIn('This page opens until <time datetime="2026-10-24T15:40:00Z">24.10.2026 15:40 UTC</time> '
                      '(in 28 days) — add the key in advance<', shell)
        self.assertIn("access until 01.10.2026 · 5 days left · no limit", shell)
        self.assertIn('<span class="pg-status__num">0 <span>MB</span></span>', shell)
        self.assertNotRegex(shell.replace("Mom", ""), "[А-Яа-яЁё]")

    def test_relative_time_is_rounded_down_in_both_languages(self):
        """TAIL-21: «через …» без JavaScript — минуты, часы с минутами до 10 ч, часы до двух суток, дальше дни; всё вниз."""
        cases = ((0, "через 1 мин", "in 1 min"), (59, "через 1 мин", "in 1 min"), (61, "через 1 мин", "in 1 min"),
                 (3599, "через 59 мин", "in 59 min"), (3600, "через 1 ч", "in 1 h"), (3659, "через 1 ч", "in 1 h"),
                 (8100, "через 2 ч 15 мин", "in 2 h 15 min"), (35999, "через 9 ч 59 мин", "in 9 h 59 min"),
                 (36000, "через 10 ч", "in 10 h"), (86399, "через 23 ч", "in 23 h"), (172799, "через 47 ч", "in 47 h"),
                 (172800, "через 2 дня", "in 2 days"), (5 * 86400 + 86399, "через 5 дней", "in 5 days"),
                 (21 * 86400, "через 21 день", "in 21 days"))
        for seconds, ru, en in cases:
            self.assertEqual(ps.rel_text("ru", seconds), ru, seconds)
            self.assertEqual(ps.rel_text("en", seconds), en, seconds)

    def test_page_lifetime_moment_and_relative_time_without_script(self):
        """TAIL-21: момент срока — в <time datetime> (UTC, как у app.js), «через …» от часов сервера; срок уже прошёл —
        без «через»; бессрочная — без <time>."""
        html = ps.render_html("ru", FakePage({}), "Мама", self.data(expires="2026-09-26T02:15:00Z"))
        self.assertIn('<time datetime="2026-09-26T02:15:00Z">26.09.2026 02:15 UTC</time> (через 2 ч 15 мин) — ', html)
        html = ps.render_html("ru", FakePage({}), "Мама", self.data(expires="2026-09-25T23:00:00Z"))
        self.assertIn('<time datetime="2026-09-25T23:00:00Z">25.09.2026 23:00 UTC</time> — ', html)
        self.assertNotIn("(через", html)
        shell = self.noscript(ps.render_html("ru", FakePage({}), "Мама", self.data(forever=True)))
        self.assertIn('<span>Страница бессрочная</span>', shell)
        self.assertNotIn("<time", shell)
        # app.js пишет то же время в поясе посетителя (Intl.DateTimeFormat) и кладёт момент в <time datetime>
        js = ps.APP_JS
        self.assertIn("new Intl.DateTimeFormat(lang, {timeZoneName: 'short'}).formatToParts(d)", js)
        self.assertIn("h('time', {datetime: p[3], text: p[1]})", js)

    def test_imported_only_page_explains_the_key_and_has_no_script_note(self):
        data = self.data(profiles=[{"id": "awg2", "line": "2.0", "imported": True, "seen": None}], lines=[])
        shell = self.noscript(ps.render_html("ru", FakePage({}), "Иван", data))
        self.assertIn('<b class="pg-key__title">Профиль 2.0</b>', shell)
        self.assertIn('<p class="pg-key__explain">Ключ для AmneziaWG 2.0 у вас уже есть: его выдали раньше', shell)
        self.assertNotIn("pg-ns", shell)
        self.assertNotIn("добавьте ключ заранее", shell)
        self.assertNotIn("pg-seen", shell)

    def test_suspended_has_three_reasons_and_no_keys(self):
        cases = {"quota": ({"reason": "quota", "used": 53687091200, "quota": 53687091200}, "Трафик закончился",
                           "Использовано 50 из 50 ГБ. Доступ включится, когда администратор увеличит лимит."),
                 "expired": ({"reason": "expired", "expires": "2026-09-20T12:00:00Z"}, "Срок доступа закончился 20.09.2026",
                             "Ключи перестали работать в этот день."),
                 "admin": ({"reason": "admin"}, "Доступ выключен администратором", "Ключи сейчас не работают.")}
        for reason, (off, title, what) in cases.items():
            html = ps.render_html("ru", FakePage({}), "Мама", {"lang": "ru", "user": "anna", "label": "Мама", "id": "p-1a2b",
                                                                "now": self.NOW, "suspended": off, "domain": "vpn.example.ru"})
            shell = self.noscript(html)
            self.assertIn('<h2 class="pg-sus__title">%s</h2>' % title, shell, reason)
            self.assertIn(what, shell, reason)
            self.assertIn("Ссылку выдал администратор сервера vpn.example.ru.", shell)
            self.assertIn("Ключи скрыты, пока доступ приостановлен.", shell)
            self.assertEqual("лимит считается за всё время" in shell, reason == "quota")
            self.assertNotIn("pg-kbtn", shell)
            self.assertNotIn('href="key/', html)

    def test_busy_page_is_static_and_translated(self):
        html = ps.render_busy("en", "vpn.example.ru")
        self.assertIn('<meta http-equiv="refresh" content="%d">' % ps.BUSY_RETRY_AFTER, html)
        self.assertIn("<h2>Server busy</h2>", html)
        self.assertIn("This link was issued by the administrator of vpn.example.ru.", html)
        self.assertNotIn("<script", html)
        self.assertNotRegex(html.split("</style>", 1)[1], "[А-Яа-яЁё]")

    def test_preview_shell_carries_nothing_about_the_person(self):
        html = ps.render_preview("ru")
        self.assertIn('property="og:description" content="Личная ссылка.', html)
        self.assertNotIn("<script", html)

    def test_query_lang_accepts_only_ru_and_en(self):
        self.assertEqual(ps.query_lang("/p-x/?lang=en"), "en")
        self.assertEqual(ps.query_lang("/p-x/?a=1&lang=ru"), "ru")
        self.assertEqual(ps.query_lang("/p-x/?lang=de"), "")
        self.assertEqual(ps.query_lang("/p-x/?lang=en%00"), "")
        self.assertEqual(ps.query_lang("/p-x/"), "")

    def test_seen_chip_and_days(self):
        self.assertEqual(ps.seen_info("ru", 0, self.NOW), ("ещё не подключался", False))
        self.assertEqual(ps.seen_info("ru", self.NOW - 120, self.NOW), ("подключался 2 мин назад", True))
        self.assertEqual(ps.seen_info("en", self.NOW - 7200, self.NOW), ("connected 2 h ago", False))
        self.assertIsNone(ps.seen_info("ru", None, self.NOW))
        self.assertEqual([ps.days_text("ru", n) for n in (1, 3, 11, 22, 36)], ["1 день", "3 дня", "11 дней", "22 дня", "36 дней"])
        self.assertEqual([ps.days_text("en", n) for n in (1, 5)], ["1 day", "5 days"])

    def test_qr_plate_follows_the_theme(self):
        """QR (владелец 04.10: белая карточка на тёмной теме): блок — плитка в токенах темы, светлая только плашка кода
        (--qr-paper, светлый оттенок акцента; цвета — hex, без oklch и color-mix: телевизоры), картинка ложится на неё
        через multiply, листание частей — кнопки в токенах. Код не инвертируется: модули остаются чёрными."""
        css = ps.PAGE_CSS

        def rule(sel):
            start = css.index("\n" + sel + "{") + 1
            return css[start:css.index("}", start)]
        dark_at = css.index("@media (prefers-color-scheme:dark)")
        self.assertIn("--qr-paper:#f2f8fb;", css[:dark_at])
        self.assertIn(";--qr-paper:#d1dce0}}", css[dark_at:css.index("}}", dark_at) + 2])
        for sel in (".pg-qr", ".pg-qr__img", ".pg-page", ".tv__qr"):
            self.assertNotRegex(rule(sel), r"#fff\b|#18181b|#f1f1f3", sel)
        self.assertIn("background:var(--tile-bg);color:var(--foreground)", rule(".pg-qr"))
        self.assertIn("padding:10px;border-radius:var(--r-field);background:var(--qr-paper);isolation:isolate", rule(".pg-qr__img"))
        self.assertIn("background:var(--qr-paper);isolation:isolate", rule(".tv__qr"))
        self.assertIn("mix-blend-mode:multiply", rule(".pg-qr__img img"))
        self.assertIn("mix-blend-mode:multiply", rule(".tv__qr-img img"))
        self.assertIn("background:var(--default);color:var(--default-foreground)", rule(".pg-page"))
        self.assertNotRegex(css, r"oklch\(|color-mix\(")


class VisitRaceTests(unittest.TestCase):
    """Визит — это keys.json (SEC-G2), а служба многопоточная: два запроса одной страницы по разные стороны
    перечитывания каталога держат РАЗНЫЕ объекты Page. Счёт визитов не теряется, state.json не бьётся,
    и запись одного файла из нескольких потоков не падает на общем временном файле."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ga-pages-visit.")
        self.pdir = os.path.join(self.tmp, "p-1a2b")
        os.makedirs(self.pdir)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_visits_from_two_page_objects_are_all_counted(self):
        meta = {"id": "p-1a2b", "user": "anna", "profiles": ["awg2"]}
        pages = [ps.Page(self.pdir, meta, "S" * 43), ps.Page(self.pdir, meta, "S" * 43)]
        log = NullLog()
        audit = ps.Audit("", log)
        rounds = 25
        results = BridgeTestBase.run_threads([lambda p=p: [p.visit(audit, log) for _ in range(rounds)]
                                              for p in pages * 4])
        self.assertTrue(all(r[0] == "ok" for r in results), results)
        with open(os.path.join(self.pdir, "state.json")) as fh:
            state = json.load(fh)
        self.assertEqual(state["visits"], rounds * 8)
        self.assertFalse([t for lvl, t in log.lines if lvl != "info"], log.lines)
        self.assertEqual(sorted(os.listdir(self.pdir)), ["state.json"])

    def test_parallel_atomic_writes_of_one_file_never_fail(self):
        path = os.path.join(self.pdir, "request")
        results = BridgeTestBase.run_threads([lambda n=n: [ps.write_json_atomic(path, {"n": n, "i": i})
                                                           for i in range(40)] for n in range(8)])
        self.assertTrue(all(r[0] == "ok" for r in results), results)
        with open(path) as fh:
            self.assertIn(json.load(fh)["n"], range(8))
        self.assertEqual(sorted(os.listdir(self.pdir)), ["request"])


class DeviceTests(unittest.TestCase):
    """Вкладка Apple TV выбирается только нажатием: браузера на Apple TV нет."""

    def test_tvos_is_a_tab_but_never_detected(self):
        self.assertIn("tvos", ps.PLATFORMS)
        agents = ["", "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)",
                  "AppleCoreMedia/1.0.0.21J354 (Apple TV; U; CPU OS 17_0 like Mac OS X; ru_ru)",
                  "Mozilla/5.0 (Linux; Android 11; SHIELD Android TV)", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                  "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0)", "Mozilla/5.0 (X11; Linux x86_64)", "tvOS AppleTV"]
        for agent in agents:
            device = ps.detect_device(agent)
            self.assertIn(device, ps.PLATFORMS, agent)
            self.assertNotEqual(device, "tvos", agent)

    def test_catalog_platforms_match_the_page_tabs(self):
        with open(os.path.join(ROOT, "lib", "clients.json"), encoding="utf-8") as fh:
            catalog = json.load(fh)
        self.assertEqual(tuple(catalog["platforms"]), ps.PLATFORMS)

    def test_platform_hint_reaches_the_page_only_where_it_is_set(self):
        # Своя подсказка платформы (incy на Apple TV — «TV relay») уходит странице; у остальных платформ
        # её нет, и страница берёт общую import.hint приложения
        catalog = ps.Catalog(os.path.join(ROOT, "lib", "clients.json"), NullLog())
        apps = {a["id"]: a for a in catalog.for_lines(["2.0", "3.1"], lambda app, platform: {})}
        self.assertIn("TV relay", apps["incy"]["platforms"]["tvos"]["hint"]["ru"])
        self.assertNotIn("hint", apps["incy"]["platforms"]["android"])
        self.assertIn("tvos", apps["wgconnect"]["platforms"])
        self.assertFalse(apps["wgconnect"]["official"])
        # Официальные — первыми и на вкладке Apple TV: у WG Connect и incy пометка «неофициальное»
        self.assertNotIn("mirror", apps["wgconnect"]["platforms"]["tvos"])


class RecommendAndHintTests(unittest.TestCase):
    """NC-16: приложение по умолчанию на телефоне — то, что есть в российских магазинах (выбор сверяется с пометками
    ссылок каталога, а не выдуман), и фраза «На Android …» общей подсказки — только на вкладках Android."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, "lib", "clients.json"), encoding="utf-8") as fh:
            cls.raw = {a["id"]: a for a in json.load(fh)["apps"]}
        catalog = ps.Catalog(os.path.join(ROOT, "lib", "clients.json"), NullLog())
        cls.apps = catalog.for_lines(["2.0", "3.1"], lambda app, platform: {})

    def notes(self, app, platform, kind):
        links = self.raw[app]["platforms"].get(platform, {}).get("links") or []
        return [str((link.get("note") or {}).get("ru") or "") for link in links if link.get("kind") == kind]

    def test_iphone_pick_is_marked_available_in_the_russian_app_store(self):
        pick = ps.RECOMMENDED["ios"]
        self.assertTrue(any("российском App Store" in n and "Скрыто" not in n for n in self.notes(pick, "ios", "appstore")),
                        self.notes(pick, "ios", "appstore"))
        hidden = sorted(a for a in self.raw if any("Скрыто в российском" in n for n in self.notes(a, "ios", "appstore")))
        self.assertEqual(hidden, ["amneziavpn"])
        self.assertNotIn(pick, hidden)

    def test_android_pick_follows_the_owner_rule_when_the_catalog_has_no_marks(self):
        # Пометок о российском Google Play нет ни у AmneziaWG, ни у AmneziaVPN — тогда AmneziaWG (правило владельца).
        # Появится пометка в каталоге — этот тест напомнит пересмотреть выбор.
        for app in ("amneziawg", "amneziavpn"):
            self.assertFalse(any("российск" in n.lower() for n in self.notes(app, "android", "googleplay")), app)
        self.assertEqual(ps.RECOMMENDED["android"], "amneziawg")
        self.assertEqual(set(ps.RECOMMENDED), {"ios", "android"})

    def test_page_recommends_only_what_is_on_the_tab_and_takes_the_keys(self):
        self.assertEqual(ps.recommend_for(self.apps, ["2.0", "3.1"]), {"ios": "amneziawg", "android": "amneziawg"})
        broken = json.loads(json.dumps(self.apps))
        for app in broken:
            if app["id"] == "amneziawg":
                app["platforms"]["ios"]["support"]["3.1"] = "no"
        self.assertEqual(ps.recommend_for(broken, ["2.0", "3.1"]), {"android": "amneziawg"})
        self.assertEqual(ps.recommend_for([a for a in self.apps if a["id"] != "amneziawg"], ["3.1"]), {})

    def test_android_sentence_of_the_hint_stays_on_android_tabs(self):
        apps = {a["id"]: a for a in self.apps}
        general = self.raw["amneziavpn"]["import"]["hint"]
        self.assertIn("На Android", general["ru"])
        self.assertIn("On Android", general["en"])
        for platform in ("ios", "windows", "macos", "linux"):
            hint = apps["amneziavpn"]["platforms"][platform]["hint"]
            self.assertNotIn("Android", hint["ru"], platform)
            self.assertNotIn("Android", hint["en"], platform)
            self.assertTrue(hint["ru"].startswith("vpn:// — скопировать"), hint["ru"])
        for platform in ("android", "android_tv"):
            self.assertNotIn("hint", apps["amneziavpn"]["platforms"][platform])      # общая подсказка — целиком
        self.assertEqual(ps.scoped_hint(general, "android_tv"), general)
        self.assertEqual(ps.scoped_hint(None, "ios"), {})


class NextStepsWordsTests(unittest.TestCase):
    """NC-17: «Дальше — в …» после «Скачать .conf» и «Скопировать». Шаги — строками словаря (оба языка), подписи кнопок
    — те, что в самих приложениях (сверено с исходниками 28.09.2026; поменялись там — меняются здесь осознанно)."""

    def test_recipes_use_only_dictionary_strings(self):
        used = set(re.findall(r"'(nx_[a-z_]+)'", ps.APP_JS))
        self.assertTrue({"nx_am_open", "nx_am_paste", "nx_am_file", "nx_am_on", "nx_wg_ios_save", "nx_wg_ios_add",
                         "nx_wg_and_add", "nx_wg_win_add", "nx_wg_mac_add"} <= used, used)
        for lang in ("ru", "en"):
            for key in used | {"nx_title", "nx_file", "nx_file_d", "nx_add", "nx_copied", "nx_paste", "qr_second", "pick_line"}:
                self.assertIn(key, ps.UI[lang], (lang, key))

    def test_button_labels_of_the_apps(self):
        ru = ps.UI["ru"]
        # AmneziaVPN (amnezia-client, ru_RU): вкладка «+», «Приступим», «Вставить» → «Продолжить», «Файл с настройками
        # подключения», «Подключиться» → «Подключено»
        self.assertIn("«+»", ru["nx_am_open"])
        self.assertIn("«Приступим»", ru["nx_am_open_d"])
        self.assertEqual(ru["nx_am_paste"], "«Вставить» → «Продолжить»")
        self.assertIn("«Вставьте ключ»", ru["nx_am_paste_d"])
        self.assertEqual(ru["nx_am_file"], "«Файл с настройками подключения»")
        self.assertEqual((ru["nx_am_on"], ru["nx_am_on_d"]), ("«Подключиться»", "Статус станет «Подключено»"))
        # AmneziaWG: iOS «Создать из файла или архива», Android «Импорт из файла или архива», Windows «Импорт туннелей из
        # файла» → «Подключить», macOS «Импорт туннелей из файла…»
        self.assertIn("«Создать из файла или архива»", ru["nx_wg_ios_add"])
        self.assertIn("«Импорт из файла или архива»", ru["nx_wg_and_add"])
        self.assertIn("«Импорт туннелей из файла»", ru["nx_wg_win_add"])
        self.assertEqual(ru["nx_wg_win_on"], "«Подключить»")
        self.assertIn("«Импорт туннелей из файла…»", ru["nx_wg_mac_add"])
        self.assertEqual(ru["qr_second"], "QR — для второго устройства")
        self.assertEqual(ru["pick_line"] % ("3.1", "2.0"), "Берите ключ 3.1; не подключился — 2.0.")


# ══════════════════════════════════════════════════════════════════════════════
#  Подписки: /s-<секрет>/<суффикс> (lib/subs.sh, docs/SUBSCRIPTION.md)
# ══════════════════════════════════════════════════════════════════════════════
import awgkeys  # noqa: E402 — тот же модуль форматов, что у службы

# Подставной мост подписок: карточки и ключи — из world.json стенда (тест правит его на ходу: выключить, удалить,
# заменить человека тёзкой). Каждый вызов — строка в calls.log.
SUB_BRIDGE = r'''#!/usr/bin/env python3
import json, os, sys
args = dict(a[2:].split("=", 1) for a in sys.argv[1:] if a.startswith("--") and "=" in a)
d = os.environ["FAKE_DIR"]
with open(os.path.join(d, "calls.log"), "a") as fh:
    fh.write("%s %s %s\n" % (args.get("action"), args.get("name", ""), args.get("profile", "")))
world = json.load(open(os.path.join(d, "world.json")))
action, name, profile = args.get("action"), args.get("name", ""), args.get("profile", "")
if action == "user-show":
    user = world["users"].get(name)
    print(json.dumps({"status": "success", "user": user} if user else {"status": "error", "code": "user_not_found"}))
elif action == "user-key":
    key = world["keys"].get(name, {}).get(profile)
    print(json.dumps(dict(key, status="success") if key else {"status": "error", "code": "no_private_key"}))
else:
    print(json.dumps({"status": "error", "code": "unknown_action"}))
'''

SUB_HOST = "vpn.example.com"
SUB_PRIV = {"awg3": "aGFzaC1hd2czLWNsaWVudC1rZXktMDEyMzQ1Njc4OTA=", "awg2": "aGFzaC1hd2cyLWNsaWVudC1rZXktMDEyMzQ1Njc4OTA=",
            "awg1": "aGFzaC1hd2cxLWNsaWVudC1rZXktMDEyMzQ1Njc4OTA="}
SUB_LINES = {"awg3": "3.1", "awg2": "2.0", "awg1": "1.5"}
SUB_PORTS = {"awg3": 1320, "awg2": 443, "awg1": 51820}


def sub_params(line):
    """Параметры линии без случайности — как в tests/test_awgkeys.py (I1 — универсальный с постоянным ID, KEENETIC-I1)."""
    p = {k: "" for k in awgkeys.PARAM_KEYS}
    i1 = "<b 0x1a2b" + awgkeys.I1_DNS_ANSWER + ">"
    p.update({"Jc": "4", "Jmin": "10", "Jmax": "50", "MTU": "1360", "PersistentKeepalive": "25"})
    if line == "3.1":
        p.update({"S1": "12", "S2": "12", "S3": "12", "S4": "12", "H1": "1", "H2": "2", "H3": "3", "H4": "4",
                  "I1": i1, "HeaderProtectionKey": "ERERERERERERERERERERERERERERERERERERERERERE=",
                  "RekeyAfterTime": "100-120", "RekeyTimeout": "3-7", "RejectAfterTime": "150-180",
                  "KeepaliveTimeout": "5-15", "MaxHandshakeAttempts": "15-20", "RandomTrailers": "on",
                  "DisableCookies": "on", "PersistentKeepalive": "25-35"})
    elif line == "2.0":
        p.update({"S1": "40", "S2": "70", "S3": "20", "S4": "30", "H1": "1000000-1002000", "H2": "2000000-2003000",
                  "H3": "3000000-3004000", "H4": "4000000-4005000", "I1": i1})
    else:
        p.update({"S1": "40", "S2": "70", "H1": "1000001", "H2": "1000002", "H3": "1000003", "H4": "1000004",
                  "I1": i1})
    return p


def sub_conf(profile):
    line = SUB_LINES[profile]
    return awgkeys.render_conf(line, {
        "params": sub_params(line), "client": {"ip": "10.8.%d.5" % {"awg3": 3, "awg2": 2, "awg1": 1}[profile],
                                               "privkey": SUB_PRIV[profile]},
        "server": {"pubkey": "c2VydmVyLXB1YmxpYy1rZXktYXdnLTAxMjM0NTY3ODk=", "endpoint_host": SUB_HOST,
                   "port": SUB_PORTS[profile]},
        "dns": ["1.1.1.1", "1.0.0.1"], "mtu": "1360"})


def sub_world(profiles=("awg3", "awg2", "awg1"), enabled=True, created="2026-09-20T10:00:00Z"):
    peers = {p: {"active": enabled, "has_key": True, "ip": "10.8.0.5"} for p in profiles}
    user = {"name": "anna", "label": "Анна", "created": created, "enabled": enabled, "quota_bytes": 53687091200,
            "expires": "2026-11-01T00:00:00Z", "traffic": {"rx": 1000, "tx": 250000},
            "profiles": sorted(profiles), "peers": peers}
    keys = {p: {"conf": sub_conf(p), "vpn": "vpn://FAKE-anna-%s" % p, "line": SUB_LINES[p], "disabled": not enabled}
            for p in profiles}
    return {"users": {"anna": user}, "keys": {"anna": keys}}


class SubHttpTests(BridgeTestBase):
    """Служба подписок по HTTP с подставным мостом: тела по суффиксам, заголовки клиентов подписок, 404 там, где
    ключей быть не должно, бан, перевыпуск, учёт заборов без адреса и без секрета в журнале."""

    SECRET = "S" * 21 + "u" * 22

    def setUp(self):
        super().setUp()
        with open(self.bin, "w") as fh:
            fh.write(SUB_BRIDGE)
        self.world(sub_world())
        self.subs = os.path.join(self.gdir, "subs")
        self.sdir = os.path.join(self.subs, "anna")
        os.makedirs(self.sdir)
        self.write_sub(self.SECRET)
        self.audit = os.path.join(self.tmp, "audit.log")
        opts = {"dir": self.gdir, "audit": self.audit, "clients": os.path.join(ROOT, "lib", "clients.json"),
                "action_bin": self.bin, "pages_dir": os.path.join(self.gdir, "pages"),
                "mirror_dir": os.path.join(self.gdir, "mirror"), "subs_dir": self.subs}
        ps.BRIDGE_TIMEOUT = 30.0
        self.application = ps.App(opts, self.log)
        self.httpd = ps.PagesHttpServer(("127.0.0.1", 0), ps.build_handler(self.application), self.log)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05})
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(5)
        super().tearDown()

    def world(self, data):
        with open(os.path.join(self.fake, "world.json"), "w") as fh:
            json.dump(data, fh, ensure_ascii=False)
        # Мост кеширует ответы по mtime users.json — «правка» стенда должна сбросить кеш, как на сервере
        with open(os.path.join(self.gdir, "users.json"), "a") as fh:
            fh.write(" ")

    def write_sub(self, secret, user_created="2026-09-20T10:00:00Z"):
        with open(os.path.join(self.sdir, "meta.json"), "w") as fh:
            json.dump({"user": "anna", "user_created": user_created, "created": "2026-10-03T10:00:00Z",
                       "rotated": "", "generation": 1}, fh)
        with open(os.path.join(self.sdir, "secret"), "w") as fh:
            fh.write(secret)

    def get(self, suffix, secret=None, method="GET", agent="FlClash/v0.8.80 clash-verge", ip=None):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        headers = {"User-Agent": agent}
        if ip:
            headers["X-Real-IP"] = ip
        conn.request(method, "/s-%s/%s" % (secret or self.SECRET, suffix), headers=headers)
        resp = conn.getresponse()
        body = resp.read()
        hdrs = {k.lower(): v for k, v in resp.getheaders()}
        conn.close()
        return resp.status, hdrs, body

    def state(self):
        try:
            with open(os.path.join(self.sdir, "state.json")) as fh:
                return json.load(fh)
        except OSError:
            return {}

    def test_mihomo_profile_has_every_line_and_the_subscription_headers(self):
        status, hdrs, body = self.get("mihomo.yaml")
        self.assertEqual(status, 200, body[:300])
        self.assertEqual(hdrs["content-type"], "text/yaml; charset=utf-8")
        text = body.decode("utf-8")
        self.assertEqual(text.count("  - name: "), 5)            # три прокси, группа выбора и «Auto»
        self.assertIn('      - "Auto"', text)                     # приложение выбирает линию само (fallback)
        self.assertIn("    type: fallback", text)
        for line in ("3.1", "2.0", "1.5"):
            self.assertIn('  - name: "Анна · AmneziaWG %s"' % line, text)
            self.assertIn('      - "Анна · AmneziaWG %s"' % line, text)
        self.assertEqual(text.count("version: 3"), 1)
        self.assertLess(text.index("AmneziaWG 3.1"), text.index("AmneziaWG 2.0"))
        self.assertLess(text.index("AmneziaWG 2.0"), text.index("AmneziaWG 1.5"))
        for p in ("awg3", "awg2", "awg1"):                       # ключ — тот же, что в .conf линии
            self.assertIn('private-key: "%s"' % SUB_PRIV[p], text)
            self.assertIn("port: %d" % SUB_PORTS[p], text)
        self.assertIn('  - "MATCH,goAmnezia"', text)
        self.assertIn('server: "%s"' % SUB_HOST, text)
        self.assertIn("mtu: 1360", text)
        # Заголовки клиентов подписок: имя профиля, интервал, трафик и срок
        self.assertEqual(hdrs["content-disposition"],
                         "attachment; filename=\"anna.yaml\"; filename*=UTF-8''%D0%90%D0%BD%D0%BD%D0%B0.yaml")
        self.assertEqual(hdrs["profile-title"], "base64:" + base64.b64encode("Анна".encode()).decode())
        self.assertEqual(hdrs["profile-update-interval"], "12")
        self.assertEqual(hdrs["subscription-userinfo"],
                         "upload=1000; download=250000; total=53687091200; expire=%d"
                         % ps.parse_iso("2026-11-01T00:00:00Z"))
        for key, value in (("cache-control", "no-store"), ("x-robots-tag", "noindex"), ("referrer-policy", "no-referrer"),
                           ("x-content-type-options", "nosniff"), ("server", "nginx"), ("content-security-policy", "sandbox")):
            self.assertIn(value, hdrs.get(key, ""), (key, hdrs))

    def test_conf_and_vpn_by_line_with_the_owner_synonyms(self):
        for suffix, profile in (("awg31.conf", "awg3"), ("awg20.conf", "awg2"), ("awg15.conf", "awg1"),
                                ("awgv3.conf", "awg3"), ("awgv2.conf", "awg2"), ("awgv15.conf", "awg1")):
            status, hdrs, body = self.get(suffix)
            self.assertEqual(status, 200, suffix)
            self.assertEqual(body.decode("utf-8"), sub_conf(profile), suffix)    # то же, что user-key --format=conf
            # Н-2: имя файла — key_file_base, как у страницы и user-key (anna-2_0.conf), а не anna-awg20.conf
            self.assertEqual(hdrs["content-disposition"], 'attachment; filename="anna-%s.conf"' % SUB_LINES[profile].replace(".", "_"))
            self.assertEqual(hdrs["content-type"], "text/plain; charset=utf-8")
            self.assertEqual(hdrs["profile-title"],
                             "base64:" + base64.b64encode(("Анна · AmneziaWG %s" % SUB_LINES[profile]).encode()).decode())
        status, hdrs, body = self.get("awg20.vpn")
        self.assertEqual((status, body), (200, b"vpn://FAKE-anna-awg2\n"))
        self.assertEqual(hdrs["content-disposition"], 'inline; filename="anna-2_0.txt"')
        self.assertEqual(self.get("awgv2.vpn")[2], b"vpn://FAKE-anna-awg2\n")
        status, hdrs, body = self.get("all.vpn")
        self.assertEqual((status, body), (200, b"vpn://FAKE-anna-awg3\nvpn://FAKE-anna-awg2\nvpn://FAKE-anna-awg1\n"))
        # all.vpn и all.txt — под разными именами (критик B, круг 4): иначе второй сохранённый — «(1)»
        self.assertEqual(hdrs["content-disposition"], 'inline; filename="anna-all-vpn.txt"')
        status, hdrs, _ = self.get("all.txt")
        self.assertEqual((status, hdrs["content-disposition"]), (200, 'inline; filename="anna-all.txt"'))
        # Линии 1.0 у человека нет — 404, а не чужая линия
        self.assertEqual(self.get("awg10.conf")[0], 404)
        self.assertEqual(self.get("awg10.vpn")[0], 404)

    def test_all_yaml_is_the_mihomo_profile_under_its_new_name(self):
        # Каталог форматов: <какие линии>.<формат>; mihomo.yaml уже выдан людям — тот же ответ по обоим именам
        old_status, old_hdrs, old_body = self.get("mihomo.yaml")
        status, hdrs, body = self.get("all.yaml")
        self.assertEqual((status, body), (old_status, old_body))
        self.assertEqual(status, 200)
        for key in ("content-type", "content-disposition", "profile-title", "profile-update-interval",
                    "subscription-userinfo", "content-security-policy"):
            self.assertEqual(hdrs[key], old_hdrs[key], key)

    def test_all_txt_is_the_incy_subscription_of_every_line(self):
        # docs.incy.cc, «Multiple AmneziaWG servers in one subscription»: по строке amneziawg://<base64url .conf>#<имя>
        status, hdrs, body = self.get("all.txt", agent="INCY/2.1 (iPhone; iOS 18.0)")
        self.assertEqual(status, 200, body[:300])
        for _ in range(40):                       # служба пишет учёт забора сразу после ответа
            if self.state().get("last_client") == "incy":
                break
            time.sleep(0.05)
        self.assertEqual((self.state().get("last_client"), self.state().get("last_suffix")), ("incy", "all.txt"))
        rows = body.decode("utf-8").split("\n")
        self.assertEqual(rows[-1], "")
        self.assertEqual(len(rows), 4)
        for row, profile in zip(rows, ("awg3", "awg2", "awg1")):        # 3.1 → 2.0 → 1.5, как mihomo.yaml
            m = re.match(r"^amneziawg://([A-Za-z0-9_-]+)#(\S+)$", row)
            self.assertIsNotNone(m, row[:60])
            data = m.group(1)
            conf = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8")
            self.assertEqual(conf, sub_conf(profile).rstrip("\n") + "\n")                 # ровно .conf линии
            self.assertEqual(self.get(ps.LINE_TOKENS[SUB_LINES[profile]] + ".conf")[2].decode("utf-8"), conf)
            self.assertEqual(m.group(2), "AmneziaWG%20" + SUB_LINES[profile])
        self.assertEqual(hdrs["content-type"], "text/plain; charset=utf-8")
        self.assertEqual(hdrs["profile-title"], "base64:" + base64.b64encode("Анна".encode()).decode())
        self.assertEqual(hdrs["profile-update-interval"], "12")
        self.assertIn("subscription-userinfo", hdrs)
        self.assertIn("sandbox", hdrs["content-security-policy"])
        # Выключенная линия и ключ из приложения Amnezia — не отдаются, как в mihomo.yaml
        world = sub_world()
        world["users"]["anna"]["peers"]["awg2"]["active"] = False
        world["users"]["anna"]["peers"]["awg1"]["has_key"] = False
        self.world(world)
        rows = self.get("all.txt")[2].decode("utf-8").strip().split("\n")
        self.assertEqual([r.rsplit("#", 1)[1] for r in rows], ["AmneziaWG%203.1"])

    def test_suffix_selects_the_line_and_skips_a_key_from_the_app(self):
        world = sub_world(profiles=("awg3", "awg2"))
        # awg2 тоже 3.1 (подхвачен у приложения на 3.1), а у awg3 ключ из приложения — линию 3.1 отдаёт awg2
        world["keys"]["anna"]["awg2"]["line"] = "3.1"
        world["users"]["anna"]["peers"]["awg3"]["has_key"] = False
        self.world(world)
        status, _, body = self.get("awg31.conf")
        self.assertEqual(status, 200)
        self.assertIn(SUB_PRIV["awg2"], body.decode())
        self.assertEqual(self.get("awg20.conf")[0], 404)
        text = self.get("mihomo.yaml")[2].decode()
        self.assertEqual(text.count("  - name: "), 2)

    def test_swapped_places_keep_each_line_with_its_own_key(self):
        """AWG31-01: подхваченный из приложения amnezia-awg2 держит 3.1, а 2.0 встала в awg3 (LZ-02). Суффикс awgNN,
        строка all.txt («#AmneziaWG <линия>») и прокси all.yaml («… · AmneziaWG <линия>») — по линии ключа (поле line
        ответа user-key), а не по месту профиля: в «3.1» — ключ awg2 с полями 3.x, в «2.0» — ключ awg3 без них."""
        import urllib.parse
        world = sub_world(profiles=("awg3", "awg2"))
        for p, line in (("awg2", "3.1"), ("awg3", "2.0")):
            conf = awgkeys.render_conf(line, {
                "params": sub_params(line), "client": {"ip": "10.8.0.5", "privkey": SUB_PRIV[p]},
                "server": {"pubkey": "c2VydmVyLXB1YmxpYy1rZXktYXdnLTAxMjM0NTY3ODk=", "endpoint_host": SUB_HOST,
                           "port": SUB_PORTS[p]}, "dns": ["1.1.1.1", "1.0.0.1"], "mtu": "1360"})
            world["keys"]["anna"][p].update({"line": line, "conf": conf})
        self.world(world)
        self.assertIn(SUB_PRIV["awg2"], self.get("awg31.conf")[2].decode())
        self.assertIn(SUB_PRIV["awg3"], self.get("awg20.conf")[2].decode())
        got = {}
        for row in self.get("all.txt")[2].decode("utf-8").strip().split("\n"):
            link, name = row.rsplit("#", 1)
            body = link[len("amneziawg://"):]
            got[urllib.parse.unquote(name)] = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)).decode()
        self.assertEqual(sorted(got), ["AmneziaWG 2.0", "AmneziaWG 3.1"])
        self.assertIn(SUB_PRIV["awg2"], got["AmneziaWG 3.1"])
        self.assertIn("HeaderProtectionKey", got["AmneziaWG 3.1"])
        self.assertIn(SUB_PRIV["awg3"], got["AmneziaWG 2.0"])
        self.assertNotIn("HeaderProtectionKey", got["AmneziaWG 2.0"])
        text = self.get("all.yaml")[2].decode()
        p31 = text[text.index('  - name: "Анна · AmneziaWG 3.1"'):text.index('  - name: "Анна · AmneziaWG 2.0"')]
        self.assertIn(SUB_PRIV["awg2"], p31)
        self.assertIn("version: 3", p31)
        self.assertNotIn(SUB_PRIV["awg3"], p31)

    def test_two_profiles_of_one_line_names_are_unique_and_carry_the_port(self):
        """DUP-LINE-01: на сервере две 3.1 — подхваченная у приложения в awg2 и своя в awg3 (у пользователя 15.10).
        all.txt, all.yaml и all.vpn отдают ВСЕ ключи человека (находка R2 стенда: ключ на второй 3.1 молча выпадал), имена
        уникальны — линия, которую держат два профиля, названа с портом (без имени места, решение владельца 15.10):
        «AmneziaWG 3.1 · порт 1320»; mihomo/Clash файл принимает. Линия без повтора — прежнее имя (переименованный
        прокси сбросил бы выбор человека в группе). Одиночный суффикс awg31.* — один ключ: awg3 (порядок мест)."""
        import urllib.parse
        with open(os.path.join(self.gdir, "config.json"), "w") as fh:
            json.dump({"profiles": {
                "awg2": {"line": "3.1", "port": 46567, "installed_at": "2026-10-01T00:00:00Z", "imported": True},
                "awg3": {"line": "3.1", "port": SUB_PORTS["awg3"], "installed_at": "2026-10-02T00:00:00Z"},
                "awg1": {"line": "1.5", "port": SUB_PORTS["awg1"], "installed_at": "2026-10-02T00:00:00Z"}}}, fh)
        world = sub_world(profiles=("awg3", "awg2", "awg1"))
        world["keys"]["anna"]["awg2"]["line"] = "3.1"                   # оба — 3.1, ключ awg2 тоже на сервере
        self.world(world)
        rows = self.get("all.txt")[2].decode("utf-8").strip().split("\n")
        names = [urllib.parse.unquote(r.rsplit("#", 1)[1]) for r in rows]
        self.assertEqual(names, ["AmneziaWG 3.1 · порт 1320", "AmneziaWG 3.1 · порт 46567 · из приложения", "AmneziaWG 1.5"])
        self.assertEqual(len(set(names)), len(names))
        status, _, body = self.get("all.yaml")
        self.assertEqual(status, 200, body[:300])
        text = body.decode()
        proxies = re.findall(r'^  - name: "(.*)"$', text.split("proxy-groups:")[0], re.M)
        self.assertEqual(proxies, ["Анна · AmneziaWG 3.1 · порт 1320", "Анна · AmneziaWG 3.1 · порт 46567 · из приложения",
                                   "Анна · AmneziaWG 1.5"])
        self.assertEqual(len(set(proxies)), len(proxies))
        self.assertIn(SUB_PRIV["awg3"], text)
        self.assertIn(SUB_PRIV["awg2"], text)
        vpn = self.get("all.vpn")[2].decode().strip().split("\n")
        self.assertEqual(vpn, ["vpn://FAKE-anna-awg3", "vpn://FAKE-anna-awg2", "vpn://FAKE-anna-awg1"])
        # Одиночный суффикс — один ключ на линию: awg3 (порядок мест awg3 → awg2 → awg1, как ga_subs_lines_json)
        self.assertIn(SUB_PRIV["awg3"], self.get("awg31.conf")[2].decode())
        self.assertNotIn(SUB_PRIV["awg2"], self.get("awg31.conf")[2].decode())
        # Ключ awg3 только в приложении — 3.1 в all.* одна (awg2), имя — с его местом и портом; awg31.* — awg2
        world["users"]["anna"]["peers"]["awg3"]["has_key"] = False
        self.world(world)
        rows = self.get("all.txt")[2].decode("utf-8").strip().split("\n")
        self.assertEqual([urllib.parse.unquote(r.rsplit("#", 1)[1]) for r in rows],
                         ["AmneziaWG 3.1 · порт 46567 · из приложения", "AmneziaWG 1.5"])
        self.assertIn(SUB_PRIV["awg2"], self.get("awg31.conf")[2].decode())

    def test_refusals_are_the_nginx_404(self):
        for suffix in ("awg2.conf", "awg30.conf", "mihomo.yml", "all.conf", "AWG20.conf", "incy.txt", "x"):
            status, hdrs, body = self.get(suffix)
            self.assertEqual((status, body), (404, ps.NGINX_404), suffix)
            self.assertNotIn("profile-title", hdrs)
        # Роботу превью — 404, и это не забор
        status, _, body = self.get("mihomo.yaml", agent="TelegramBot (like TwitterBot)")
        self.assertEqual((status, body), (404, ps.NGINX_404))
        self.assertEqual(self.get("awg20.conf", agent="WhatsApp/2.23.20.0")[0], 404)
        self.assertEqual(self.state(), {})
        self.assertEqual(self.get("mihomo.yaml", method="POST")[0], 404)
        # Выключенный — 404 (у роутера остаётся прежний конфиг), включили — снова 200 без перенастройки
        self.world(sub_world(enabled=False))
        self.assertEqual(self.get("mihomo.yaml")[0], 404)
        self.assertEqual(self.get("awg20.conf")[0], 404)
        self.world(sub_world())
        self.assertEqual(self.get("awg20.conf")[0], 200)
        # Тёзка, заведённый после удаления владельца, — 404; удалённый — 404
        self.world(sub_world(created="2026-10-02T10:00:00Z"))
        self.assertEqual(self.get("awg20.conf")[0], 404)
        self.world({"users": {}, "keys": {}})
        self.assertEqual(self.get("awg20.conf")[0], 404)
        self.assertFalse(any(self.SECRET in text for _, text in self.log.lines))

    def test_rotation_and_revocation_act_at_once(self):
        self.assertEqual(self.get("awg20.conf")[0], 200)
        self.assertEqual(self.get("all.txt")[0], 200)
        fresh = "N" * 43
        with open(os.path.join(self.sdir, "secret"), "w") as fh:
            fh.write(fresh)
        self.assertEqual(self.get("awg20.conf")[0], 404)          # прежний адрес — сразу, без ожидания перечитывания
        self.assertEqual(self.get("all.txt")[0], 404)
        time.sleep(ps.SUBS_RESCAN + 0.1)
        self.assertEqual(self.get("awg20.conf", secret=fresh)[0], 200)
        self.assertEqual(self.get("all.txt", secret=fresh)[0], 200)
        self.assertEqual(self.get("awg20.conf")[0], 404)
        os.unlink(os.path.join(self.sdir, "secret"))
        self.assertEqual(self.get("awg20.conf", secret=fresh)[0], 404)
        self.assertEqual(self.get("all.txt", secret=fresh)[0], 404)          # отзыв — и подписке incy

    def state_after(self, fetches):
        """state.json пишется после ответа — ждём, пока счёт дойдёт до fetches (до 3 с)."""
        import time
        st = {}
        for _ in range(60):
            st = self.state()
            if st.get("fetches") == fetches:
                return st
            time.sleep(0.05)
        return st

    def test_auto_fetches_count_only_self_updating_subscriptions(self):
        """R-1 (проверка 15.10): «обновится сама» — только забор всех линий сразу приложением. Просмотр в браузере,
        curl и ключ одной линии (после смены линии он 404) счёт auto_fetches не растят; fetches — растят все."""
        self.get("all.txt", agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Safari/604.1")
        self.get("all.yaml", agent="curl/8.5.0")
        self.get("awg31.vpn", agent="incy/1.2")
        self.get("all.txt", agent="TelegramBot (like TwitterBot)")         # робот превью — 404, не забор
        st = self.state_after(3)
        self.assertEqual((st["fetches"], st["auto_fetches"]), (3, 0), st)
        self.get("all.yaml", agent="FlClash/v0.8.80 clash-verge")
        self.get("all.txt", agent="incy/1.2")
        st = self.state_after(5)
        self.assertEqual((st["fetches"], st["auto_fetches"]), (5, 2), st)
        # all.vpn — разовый импорт пула (goVLESS, Kaskad, goTelegram; SUBSCRIPTION §2), сам не обновляется (N-1).
        self.get("all.vpn", agent="Python-urllib/3.12")
        st = self.state_after(6)
        self.assertEqual((st["fetches"], st["auto_fetches"]), (6, 2), st)

    def test_auto_fetches_seeded_from_state_of_older_service(self):
        """У state.json прежней службы auto_fetches нет — начало по последнему забору: all.* приложением — 1."""
        with open(os.path.join(self.sdir, "state.json"), "w") as fh:
            json.dump({"fetches": 7, "last_suffix": "all.yaml", "last_client": "clash-verge"}, fh)
        self.get("awg20.conf", agent="curl/8.5.0")
        st = self.state_after(8)
        self.assertEqual((st["fetches"], st.get("auto_fetches")), (8, 1), st)
        with open(os.path.join(self.sdir, "state.json"), "w") as fh:
            json.dump({"fetches": 7, "last_suffix": "awg31.vpn", "last_client": "incy"}, fh)
        self.get("awg20.conf", agent="curl/8.5.0")
        st = self.state_after(8)
        self.assertEqual((st["fetches"], st.get("auto_fetches")), (8, 0), st)
        with open(os.path.join(self.sdir, "state.json"), "w") as fh:
            json.dump({"fetches": 7, "last_suffix": "all.vpn", "last_client": "other"}, fh)
        self.get("awg20.conf", agent="curl/8.5.0")
        st = self.state_after(8)
        self.assertEqual((st["fetches"], st.get("auto_fetches")), (8, 0), st)

    def test_fetches_are_counted_without_address_and_audited_without_secret(self):
        self.get("mihomo.yaml", ip="198.51.100.7")
        self.get("awg20.conf", agent="curl/8.5.0")
        self.get("awg20.conf", method="HEAD")
        st = self.state_after(2)
        self.assertEqual((st["fetches"], st["last_suffix"], st["last_client"]), (2, "awg20.conf", "curl"))
        self.assertRegex(st["last_fetch"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertNotIn("198.51.100.7", json.dumps(st))
        self.assertEqual(oct(os.stat(os.path.join(self.sdir, "state.json")).st_mode & 0o777), "0o600")
        with open(self.audit) as fh:
            audit = [json.loads(line) for line in fh]
        fetch = [a for a in audit if a["action"] == "sub-fetch"]
        self.assertEqual(len(fetch), 1)                            # раз в час на подписку
        self.assertIn("user=anna", fetch[0]["details"])
        self.assertNotIn(self.SECRET, json.dumps(audit))

    def test_misses_ban_the_address_even_for_the_right_secret(self):
        for i in range(ps.BAN_FAILURES):
            self.assertEqual(self.get("mihomo.yaml", secret="Z%042d" % i, ip="203.0.113.77")[0], 404)
        self.assertEqual(self.get("mihomo.yaml", ip="203.0.113.77")[0], 404)
        self.assertEqual(self.get("mihomo.yaml", ip="203.0.113.78")[0], 200)

    def test_busy_bridge_is_503_with_retry_after(self):
        def busy(*_args):
            raise ps.BridgeBusy()
        self.application.bridge.user_show = busy
        status, hdrs, _ = self.get("mihomo.yaml")
        self.assertEqual((status, hdrs.get("retry-after")), (503, str(ps.BUSY_RETRY_AFTER)))

    def test_update_interval_from_config(self):
        with open(os.path.join(self.gdir, "config.json"), "w") as fh:
            json.dump({"subs": {"update_interval": 6}}, fh)
        self.assertEqual(self.get("awg20.conf")[1]["profile-update-interval"], "6")
        with open(os.path.join(self.gdir, "config.json"), "w") as fh:
            json.dump({"subs": {"update_interval": 1000}}, fh)
        self.assertEqual(self.get("awg20.conf")[1]["profile-update-interval"], "12")


class PageSubBase(BridgeTestBase):
    """Стенд вкладки «Подписка»: страница p-1a2b и подписка anna с тремя линиями, служба по HTTP на петле."""

    PAGE = "P" * 43
    SUB = "S" * 21 + "u" * 22

    def setUp(self):
        super().setUp()
        with open(self.bin, "w") as fh:
            fh.write(SUB_BRIDGE)
        self.world(sub_world())
        self.config(subs_location=True)
        pdir = os.path.join(self.gdir, "pages", "p-1a2b")
        os.makedirs(pdir)
        with open(os.path.join(pdir, "meta.json"), "w") as fh:
            json.dump({"id": "p-1a2b", "user": "anna", "user_created": "2026-09-20T10:00:00Z",
                       "created": "2026-10-04T10:00:00Z", "profiles": ["awg3", "awg2", "awg1"], "mirror": True,
                       "forever": True, "expires": "", "expires_epoch": 0}, fh)
        with open(os.path.join(pdir, "secret"), "w") as fh:
            fh.write(self.PAGE)
        self.subs = os.path.join(self.gdir, "subs")
        self.sdir = os.path.join(self.subs, "anna")
        os.makedirs(self.sdir)
        self.write_sub(self.SUB)
        opts = {"dir": self.gdir, "audit": "", "clients": os.path.join(ROOT, "lib", "clients.json"),
                "action_bin": self.bin, "pages_dir": os.path.join(self.gdir, "pages"),
                "mirror_dir": os.path.join(self.gdir, "mirror"), "subs_dir": self.subs}
        ps.BRIDGE_TIMEOUT = 30.0
        self.application = ps.App(opts, self.log)
        self.httpd = ps.PagesHttpServer(("127.0.0.1", 0), ps.build_handler(self.application), self.log)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05})
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(5)
        super().tearDown()

    def world(self, data):
        with open(os.path.join(self.fake, "world.json"), "w") as fh:
            json.dump(data, fh, ensure_ascii=False)
        with open(os.path.join(self.gdir, "users.json"), "a") as fh:
            fh.write(" ")

    def config(self, subs_location=True, domain=SUB_HOST):
        with open(os.path.join(self.gdir, "config.json"), "w") as fh:
            json.dump({"lang": "ru", "domain": domain, "site": {"subs_location": subs_location},
                       "profiles": {p: {"line": l} for p, l in SUB_LINES.items()}}, fh)

    def write_sub(self, secret, user_created="2026-09-20T10:00:00Z"):
        with open(os.path.join(self.sdir, "meta.json"), "w") as fh:
            json.dump({"user": "anna", "user_created": user_created, "created": "2026-10-04T10:00:00Z",
                       "rotated": "", "generation": 1}, fh)
        with open(os.path.join(self.sdir, "secret"), "w") as fh:
            fh.write(secret)

    def request(self, method, path, body=None, agent="Mozilla/5.0 (Linux; Android 14)"):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        headers = {"User-Agent": agent}
        if body is not None:
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
        data = resp.read()
        hdrs = {k.lower(): v for k, v in resp.getheaders()}
        conn.close()
        return resp.status, hdrs, data

    def page(self):
        status, _h, body = self.request("GET", "/p-%s/" % self.PAGE)
        self.assertEqual(status, 200, body[:300])
        html = body.decode("utf-8")
        blob = html.split('<script type="application/json" id="ga-data">', 1)[1].split("</script>", 1)[0]
        return html, json.loads(blob)

    def post_sub(self, nonce, **kw):
        return self.request("POST", "/p-%s/sub.json" % self.PAGE, json.dumps({"nonce": nonce}), **kw)


class PageSubTabTests(PageSubBase):
    """Вкладка «Подписка» страницы подключения (решение владельца 04.10.2026): в данных страницы — включена ли подписка,
    её линии и приложения с подпиской, но не адрес; адрес — POST sub.json со своим одноразовым значением отрисовки
    (тот же порядок, что у ключей, SEC50-05), роботу превью — 404; перевыпуск виден сразу, отзыв — «не включили»."""

    def test_page_data_carries_the_tab_but_never_the_address(self):
        html, data = self.page()
        self.assertNotIn(self.SUB, html)
        self.assertNotIn("/s-", html)
        # short — третья кнопка «Короткая ссылка»: только когда бот настроен (2FA через Telegram-бота), здесь его нет
        self.assertEqual(data["sub"], {"on": True, "hours": 12, "lines": ["1.5", "2.0", "3.1"], "short": False,
                                       "was": False})
        self.assertNotIn("reveal_short", data)
        self.assertRegex(data["reveal_sub"], r"^[A-Za-z0-9_-]{32}$")
        self.assertNotEqual(data["reveal_sub"], data["reveal"])
        static = {a["id"] for a in data["apps"]}
        subs = {a["id"]: sorted(a["platforms"]) for a in data["sub_apps"]}
        self.assertEqual(subs, {"flclash": ["android", "linux", "macos", "windows"], "clash-meta": ["android"],
                                "clash-verge-rev": ["linux", "macos", "windows"],
                                "clash-party": ["linux", "macos", "windows"], "keenetic": ["router"],
                                "awgm": ["router"],
                                "amneziavpn": ["android", "ios", "linux", "macos", "windows"], "defaultvpn": ["ios"],
                                "amneziawg": ["android", "ios", "macos", "windows"],
                                "wgtunnel": ["android", "android_tv"],
                                "incy": ["android", "android_tv", "ios", "macos", "tvos"]})
        self.assertFalse(static & {"flclash", "clash-meta", "clash-verge-rev", "clash-party", "awgm"},
                         "приложения только для подписки — не на вкладке «Статический ключ»")
        self.assertTrue({"keenetic", "amneziavpn", "defaultvpn"} <= static)
        self.assertEqual(data["sub_recommend"], {"ios": "incy", "android": "flclash", "android_tv": "incy",
                                                 "tvos": "incy", "windows": "clash-verge-rev",
                                                 "macos": "clash-verge-rev", "linux": "clash-verge-rev",
                                                 "router": "keenetic"})
        keenetic = next(a for a in data["sub_apps"] if a["id"] == "keenetic")["sub"]
        self.assertEqual((keenetic["format"], keenetic["once"], keenetic["lines"]), ("conf", True, ["2.0", "1.5", "1.0"]))
        self.assertEqual((keenetic["copy"], keenetic["qr"]), ("command", ""), "роутеру — команда, QR не нужен")
        verge = next(a for a in data["sub_apps"] if a["id"] == "clash-verge-rev")["sub"]
        self.assertTrue(verge["deeplink"].endswith("&url={url}"), "Clash Verge Rev берёт всё после «url=» — url последним")
        # Ключ vpn:// по ссылке — разово, все линии в порядке продукта; самообновляемое (mihomo) — первым в списке
        # «Скопировать» у них кладёт сам ключ, QR — QR ключа; путь «куда вставить» — из каталога
        for app_id in ("amneziavpn", "defaultvpn"):
            sub = next(a for a in data["sub_apps"] if a["id"] == app_id)["sub"]
            self.assertEqual((sub["format"], sub["once"], sub["lines"]), ("vpn", True, ["3.1", "2.0", "1.5", "1.0"]))
            self.assertEqual((sub["copy"], sub["qr"], sub["qr_alt"]), ("key", "key", ""))
            self.assertIn(next(a for a in data["sub_apps"] if a["id"] == app_id)["name"], sub["hint"]["ru"])
            self.assertTrue(sub["hint"]["en"])
            self.assertIn("«Вставить» → «Продолжить»", sub["paste"]["ru"])
        formats = [a["sub"]["format"] for a in data["sub_apps"]]
        self.assertEqual(formats, sorted(formats, key=ps.SUB_FORMAT_ORDER.get), formats)
        # incy — ссылки amneziawg:// всех линий (формат txt): два QR — ссылка incy://add/… и голый адрес
        incy = next(a for a in data["sub_apps"] if a["id"] == "incy")["sub"]
        self.assertEqual((incy["format"], incy["deeplink"], incy["update_hours"], incy["once"], incy["qr"],
                          incy["qr_alt"], incy["copy"]), ("txt", "incy://add/{url}", 0, False, "deeplink", "url", "url"))
        # AWG Manager — три формата с переключателем «Формат»: all.txt (сам), ключ vpn:// и текст .conf по линиям
        awgm = next(a for a in data["sub_apps"] if a["id"] == "awgm")
        self.assertEqual([(v["format"], v["variant"]["ru"], v["once"], v["copy"], v["qr"]) for v in awgm["variants"]],
                         [("txt", "Все линии .txt", False, "url", ""), ("vpn", "Ключ vpn://", True, "key", ""),
                          ("conf", "Файл .conf", True, "key", "")])
        self.assertEqual(awgm["sub"], awgm["variants"][0])
        self.assertEqual(awgm["variants"][1]["line_note"]["ru"], "Для 3.1 — режим Kernel, 2.0 работает в любом")
        self.assertNotIn("variants", incy, "у одноформатных вариантов нет")

    def test_address_only_by_post_with_its_own_nonce_and_only_once(self):
        _html, data = self.page()
        status, hdrs, body = self.request("GET", "/p-%s/sub.json" % self.PAGE)
        self.assertEqual((status, hdrs.get("allow")), (405, "POST"))
        for bad in ("x" * 32, data["reveal"], ""):
            status, _h, body = self.post_sub(bad)
            self.assertEqual(status, 409, bad)
            self.assertNotIn(self.SUB.encode(), body)
        status, _h, body = self.post_sub(data["reveal_sub"], agent="TelegramBot (like TwitterBot)")
        self.assertEqual((status, body), (404, ps.NGINX_404), "роботу превью — никогда")
        status, hdrs, body = self.post_sub(data["reveal_sub"])
        self.assertEqual(status, 200, body)
        self.assertIn("no-store", hdrs.get("cache-control", ""))
        answer = json.loads(body.decode("utf-8"))
        base = "https://%s/s-%s/" % (SUB_HOST, self.SUB)
        self.assertEqual(answer["sub"]["mihomo"], base + "all.yaml", "новое имя профиля mihomo")
        self.assertEqual(answer["sub"]["conf"], {"1.5": base + "awg15.conf", "2.0": base + "awg20.conf",
                                                 "3.1": base + "awg31.conf"})
        # Ключ vpn:// по ссылке (AmneziaVPN, DefaultVPN): адреса .vpn линий человека — и по ним служба отдаёт сам ключ
        # (страница забирает путь адреса с того же источника по «Скопировать»)
        self.assertEqual(answer["sub"]["vpn"], {"1.5": base + "awg15.vpn", "2.0": base + "awg20.vpn",
                                                "3.1": base + "awg31.vpn"})
        self.assertEqual(answer["sub"]["txt"], base + "all.txt")
        status, _h, body = self.request("GET", answer["sub"]["txt"][len("https://%s" % SUB_HOST):])
        self.assertEqual((status, body.decode("utf-8").count("amneziawg://")), (200, 3))
        iphone = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"
        for line, profile in (("3.1", "awg3"), ("2.0", "awg2"), ("1.5", "awg1")):
            path = answer["sub"]["vpn"][line][len("https://%s" % SUB_HOST):]
            status, hdrs, body = self.request("GET", path, agent=iphone)
            self.assertEqual((status, body), (200, ("vpn://FAKE-anna-%s\n" % profile).encode()), line)
            self.assertEqual(hdrs["content-type"], "text/plain; charset=utf-8")
        self.assertEqual((answer["sub"]["hours"], answer["sub"]["title"]), (12, "Анна"))
        self.assertRegex(answer["grant"], r"^[A-Za-z0-9_-]{32}$")
        self.assertEqual(self.post_sub(data["reveal_sub"])[0], 409, "повтор значения")
        # Значение подписки ключей не открывает — и наоборот (проверено выше)
        status, _h, body = self.request("POST", "/p-%s/keys.json" % self.PAGE, json.dumps({"nonce": data["reveal_sub"]}))
        self.assertEqual(status, 409)
        self.assertFalse([t for _l, t in self.log.lines if self.SUB in t], "адрес подписки — не в журнал")

    def test_rotation_shows_the_new_link_and_revocation_says_not_enabled(self):
        _html, data = self.page()
        self.write_sub("N" * 43)
        answer = json.loads(self.post_sub(data["reveal_sub"])[2].decode("utf-8"))
        self.assertIn("/s-%s/" % ("N" * 43), answer["sub"]["mihomo"], "перевыпуск — сразу новый адрес")
        nonce = self.application.reveal.issue("p-1a2b" + ps.SUB_NONCE)
        shutil.rmtree(self.sdir)
        status, _h, body = self.post_sub(nonce)
        self.assertEqual((status, json.loads(body.decode("utf-8"))), (200, {"sub": None}))
        _html, data = self.page()
        self.assertFalse(data["sub"]["on"])
        self.assertNotIn("reveal_sub", data, "без подписки значения для неё нет")

    def test_paused_route_namesake_and_suspended(self):
        self.config(subs_location=False)               # сайт ушёл с домена: адрес бы не открылся
        self.assertFalse(self.page()[1]["sub"]["on"])
        self.config(subs_location=True)
        self.write_sub(self.SUB, user_created="2026-01-01T00:00:00Z")   # подписка прежнего владельца имени
        self.assertFalse(self.page()[1]["sub"]["on"])
        self.write_sub(self.SUB)
        _html, data = self.page()
        self.world(sub_world(enabled=False))
        answer = json.loads(self.post_sub(data["reveal_sub"])[2].decode("utf-8"))
        self.assertEqual(sorted(answer), ["suspended"])
        self.assertNotIn(self.SUB, json.dumps(answer))

    def test_only_3_1_hides_the_router_and_the_qr_needs_the_grant(self):
        self.world(sub_world(profiles=("awg3",)))
        _html, data = self.page()
        self.assertEqual(data["sub"]["lines"], ["3.1"])
        self.assertNotIn("keenetic", {a["id"] for a in data["sub_apps"]}, "KeeneticOS 3.1 не умеет")
        self.assertNotIn("router", data["sub_recommend"])
        for query in ("", "?g=" + "A" * 32):
            self.assertEqual(self.request("GET", "/p-%s/qr/sub/mihomo.png%s" % (self.PAGE, query))[0], 404, query)
        grant = json.loads(self.post_sub(data["reveal_sub"])[2].decode("utf-8"))["grant"]
        status, hdrs, body = self.request("GET", "/p-%s/qr/sub/mihomo.png?g=%s" % (self.PAGE, grant))
        if self.application.qr.available():
            self.assertEqual((status, hdrs["content-type"]), (200, "image/png"))
            self.assertTrue(body.startswith(b"\x89PNG"))
        else:
            self.assertEqual(status, 404)
        self.assertEqual(self.request("GET", "/p-%s/qr/sub/awg20.png?g=%s" % (self.PAGE, grant))[0], 404,
                         "линии 2.0 у человека нет")

    def test_incy_has_two_qr_camera_link_and_bare_address_both_need_the_grant(self):
        # Сканер внутри incy схему incy:// не берёт, обычная камера телефона — берёт; голый адрес https сканер incy
        # принимает (живая проверка 04.10.2026) — поэтому QR два
        _html, data = self.page()
        for what in ("incy", "txt"):
            self.assertEqual(self.request("GET", "/p-%s/qr/sub/%s.png" % (self.PAGE, what))[0], 404, what)
        answer = json.loads(self.post_sub(data["reveal_sub"])[2].decode("utf-8"))
        link = self.application.incy_link(answer["sub"]["txt"])
        self.assertEqual(link, "incy://add/" + answer["sub"]["txt"], "адрес — как есть, как в примере incy")
        for what, text in (("incy", link), ("txt", answer["sub"]["txt"])):
            status, hdrs, body = self.request("GET", "/p-%s/qr/sub/%s.png?g=%s" % (self.PAGE, what, answer["grant"]))
            if self.application.qr.available():
                self.assertEqual((status, hdrs["content-type"]), (200, "image/png"), what)
                self.assertEqual(body, self.application.qr.png(text), what)
            else:
                self.assertEqual(status, 404, what)

    def test_open_in_app_links_and_incy_qr_pair(self):
        js = ps.APP_JS
        link = js[js.index("function subDeeplink(v, url) {"):js.index("var SUB_PHONES")]
        self.assertIn("if (tpl === 'incy://add/{url}') { return 'incy://add/' + url; }", link, "адрес как есть")
        self.assertIn("if (tpl === 'wg://{host_path}') { return 'wg://' + url.replace(/^https:\\/\\//, ''); }", link)
        self.assertIn("if (!tpl || !safeUrl(url)) { return ''; }", link)
        self.assertIn("{line: '', suffix: v.format === 'mihomo' ? 'all.yaml' : 'all.txt'}", js,
                      "в скрытом адресе — новые имена all.yaml и all.txt")
        qr = js[js.index("function subQrPanel(a, v, it) {"):js.index("function subWhat(v) {")]
        self.assertIn("keyUrl('qr/sub/incy.png'), t('sub_qr_cam', name)", qr)
        self.assertIn("keyUrl('qr/sub/txt.png'), t('sub_qr_scan', name)", qr)
        self.assertIn("vpnQrPanel(pr)", qr, "QR ключа vpn:// — панель со страницами, как на «Статическом ключе»")
        self.assertIn("keyUrl('qr/key/' + pr.id + '/conf.png')", qr)
        ru = ps.UI["ru"]
        self.assertEqual(ru["sub_qr_cam"] % "incy", "Камерой телефона: наведите обычную камеру → «Открыть в incy»")
        self.assertEqual(ru["sub_qr_scan"] % "incy", "Сканером в incy: «Добавить» → значок QR → наведите на этот код")
        self.assertEqual(ru["sub_open_q"], "Приложение уже стоит на этом устройстве?")

    def test_the_script_asks_for_the_address_only_by_a_press(self):
        js = ps.APP_JS
        self.assertIn("fetch('sub.json', {method: 'POST'", js)
        self.assertIn("nonce: String(data.reveal_sub", js)
        # Адрес просит только subReady, а её зовут лишь нажатия ряда и «Открыть в …» — с проверкой isTrusted (ACC5-4)
        self.assertEqual(js.count("fetchSub();"), 1)
        self.assertIn("if (subState === 'hidden') { subState = 'loading'; fetchSub(); }", js)
        for fn in ("function subQrPress(e) {", "function subCopyPress(e) {", "function subShortPress(e) {"):
            body = js[js.index(fn):]
            body = body[:body.index("\n  }\n")]
            self.assertIn("isTrusted === false) ", body, fn)
        self.assertEqual(js.count("subReady()"), 5,
                         "сама функция и четыре вызова: subValue («Скопировать»), QR, «Открыть в …», «Короткая ссылка»")
        tail = js[js.rindex("render();"):]
        self.assertNotIn("fetchSub()", tail, "после отрисовки адрес сам не запрашивается")
        self.assertIn("window.localStorage.getItem('ga_tab')", js)
        self.assertIn("window.localStorage.setItem('ga_tab', tab)", js)
        self.assertIn("var tab = 'static';", js, "по умолчанию — «Статический ключ»")

    def test_iphone_gets_the_vpn_key_by_link_and_no_address_in_html(self):
        # На iPhone и iPad вкладка «Подписка» есть (ключ vpn:// по ссылке в AmneziaVPN и DefaultVPN), адреса в HTML нет
        iphone = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15"
        status, _h, body = self.request("GET", "/p-%s/" % self.PAGE, agent=iphone)
        self.assertEqual(status, 200)
        html = body.decode("utf-8")
        data = json.loads(html.split('<script type="application/json" id="ga-data">', 1)[1].split("</script>", 1)[0])
        self.assertEqual(data["device"], "ios")
        self.assertNotIn(self.SUB, html)
        self.assertNotIn("/s-", html)
        self.assertNotRegex(html, r"awg[0-9]{2}\.vpn")
        on_ios = [a["id"] for a in data["sub_apps"] if "ios" in a["platforms"]]
        self.assertEqual(on_ios, ["incy", "amneziavpn", "defaultvpn", "amneziawg"],
                         "обновляется сам — первым, разовое — после")
        self.assertEqual(data["sub_recommend"]["ios"], "incy")
        defaultvpn = next(a for a in data["sub_apps"] if a["id"] == "defaultvpn")["platforms"]["ios"]
        self.assertIn("российского App Store", defaultvpn["links"][0]["note"]["ru"], "пометка каталога — на месте")
        self.assertRegex(data["reveal_sub"], r"^[A-Za-z0-9_-]{32}$")

    def test_tab_devices_match_and_tvs_get_only_what_needs_no_paste(self):
        # Устройства вкладки в app.js — те же, что SUB_PLATFORMS службы: все устройства «Статического ключа», с
        # телевизорами (решение владельца 04.10.2026); на телевизоре — incy и WG Tunnel, ключ из буфера там не вставить
        self.assertEqual(ps.SUB_PLATFORMS, ps.PLATFORMS)
        catalog = ps.Catalog(os.path.join(ROOT, "lib", "clients.json"), NullLog())
        apps = catalog.for_subs(["2.0", "3.1"])
        self.assertEqual([a["id"] for a in apps if "android_tv" in a["platforms"]], ["incy", "wgtunnel"])
        self.assertEqual([a["id"] for a in apps if "tvos" in a["platforms"]], ["incy"])
        js_plats = re.search(r"var SUB_PLATS = \[([^\]]*)\];", ps.APP_JS).group(1)
        self.assertEqual(tuple(re.findall(r"'([a-z_]+)'", js_plats)), ps.SUB_PLATFORMS)
        self.assertEqual([p for p in ps.PLATFORMS if p in ps.SUB_PLATFORMS], list(ps.SUB_PLATFORMS),
                         "порядок — как у вкладок «Статического ключа»")
        # Те же устройства — у клиентов в ответе моста (lib/subs.sh, GA_SUBS_PLATFORMS)
        with open(os.path.join(ROOT, "lib", "subs.sh"), encoding="utf-8") as fh:
            m = re.search(r"^GA_SUBS_PLATFORMS='(\[[^']*\])'$", fh.read(), re.M)
        self.assertEqual(tuple(json.loads(m.group(1))), ps.SUB_PLATFORMS)
        # «рекомендуем» — поле subscription.recommended каталога: только устройства вкладки, на устройстве — одно
        with open(os.path.join(ROOT, "lib", "clients.json"), encoding="utf-8") as fh:
            apps = json.load(fh)["apps"]
        subs = [s for a in apps for s in (a["subscription"] if isinstance(a.get("subscription"), list)
                                          else [a.get("subscription") or {}])]
        rec = [p for s in subs for p in s.get("recommended") or []]
        self.assertTrue(set(rec) <= set(ps.SUB_PLATFORMS), rec)
        self.assertEqual(len(rec), len(set(rec)), rec)
        self.assertEqual(sorted(rec), sorted(ps.SUB_PLATFORMS), "у каждого устройства вкладки — своё «рекомендуем»")

    def test_copy_takes_what_is_pasted_and_qr_is_for_phones(self):
        js = ps.APP_JS
        # «Скопировать» — то, что вставляют: ключ — свежим GET своего адреса .vpn или .conf с той же страницы
        key = js[js.index("function subKey(url, fmt) {"):js.index("function subValue(v, it) {")]
        self.assertIn("fetch(path, {credentials: 'same-origin', cache: 'no-store'})", key)
        self.assertIn("new RegExp('^/s-[A-Za-z0-9_-]{43}/awg[0-9]{2}\\\\.' + fmt + '$')", key, "только путь своего адреса")
        value = js[js.index("function subValue(v, it) {"):js.index("function subPress(mode, wait, what) {")]
        self.assertIn("return v.copy === 'key' ? subKey(url, v.format) : v.copy === 'command' ? subCommand(v, url) : url;",
                      value)
        self.assertIn("new window.ClipboardItem({'text/plain': blob})", js, "Safari: запись в буфер — в самом нажатии")
        # QR — только на телефонах, у incy — везде; причина неактивной кнопки — словами владельца
        self.assertIn("var SUB_PHONES = {ios: 1, android: 1}, SUB_QR_ANY = {incy: 1};", js)
        ru = ps.UI["ru"]
        self.assertEqual((ru["sub_noqr_pc"], ru["sub_noqr_router"], ru["sub_noqr_tv"], ru["sub_noqr_app"]),
                         ("на компьютере не нужен", "роутеру не нужен", "на телевизоре сканировать нечем", "сканера нет"))
        self.assertEqual(ru["sub_pasted"] % ("ключ vpn://", "вставьте в AmneziaVPN: «+» → «Вставить» → «Продолжить»"),
                         "Скопировано: ключ vpn://. Теперь вставьте в AmneziaVPN: «+» → «Вставить» → «Продолжить».")
        self.assertEqual(ru["sub_guide"], "Выберите, как удобнее: QR — навести камеру, «Скопировать» — вставить в "
                                          "приложение.")
        # Третья кнопка — только когда в ответе sub.json есть short (короткая ссылка — отдельной задачей)
        self.assertIn("short ? subTri('short', ", js)
        self.assertIn("{cls: 'pg-trio' + (short ? '' : ' pg-trio--2')", js)
        # Честная пометка — словами владельца (строка под заголовком «Ключ vpn://»)
        self.assertEqual(ru["sub_vpn_once"] % "AmneziaVPN",
                         "Разово по ссылке: AmneziaVPN сам по ссылке не обновляется — такой функции в приложении нет. "
                         "Ссылка постоянная и всегда отдаёт текущий ключ: сменится ключ или порт — откройте её снова и "
                         "добавьте ключ заново.")
        for gone in ("sub_show", "sub_hide", "sub_vpn_qr", "sub_ios_title"):
            self.assertNotIn(gone, ru, "«Показать ссылку», «Спрятать» и отсылка к QR «Статического ключа» — в прошлом")
        # На узком экране ряд остаётся рядом, мелкие подписи прячутся
        self.assertIn("@media (max-width:640px){.pg-tri{min-height:76px;padding:8px 4px}.pg-tri small{display:none}",
                      ps.PAGE_CSS)


class PageShortTests(PageSubBase):
    """Короткие ссылки на странице и маршрут /<КОД> (lib/shortlinks.py, docs/SUBSCRIPTION.md §11): работают только с 2FA
    через Telegram-бота — здесь бот настроен (.env с токеном и администратором). Кнопка «Короткая ссылка» —
    POST short.json со своим одноразовым значением (каждый ответ даёт следующее), ↻ — POST short-replace.json и опрос
    short-status.json; поле short в sub.json — в форме адресов и только уже созданные коды; страница подтверждения
    /<КОД> — со своим CSP; доверенный забор — то же, что полный адрес."""

    ENV = ("GOAMNEZIA_BOT_DIR", "GOAMNEZIA_LOCK_DIR", "GOAMNEZIA_NOTIFY_DIR", "GOAMNEZIA_SHORT_CONFIRM_DIR")

    def setUp(self):
        self._saved_env = {k: os.environ.get(k) for k in self.ENV}
        self.bot_root = tempfile.mkdtemp(prefix="ga-short-bot.")
        self.bot = os.path.join(self.bot_root, "bot")
        os.makedirs(self.bot)
        with open(os.path.join(self.bot, ".env"), "w") as fh:
            fh.write("BOT_TOKEN=1:x\nALLOWED_IDS=111\n")
        os.environ["GOAMNEZIA_BOT_DIR"] = self.bot
        os.environ["GOAMNEZIA_LOCK_DIR"] = os.path.join(self.bot_root, "run")
        os.environ.pop("GOAMNEZIA_NOTIFY_DIR", None)
        os.environ.pop("GOAMNEZIA_SHORT_CONFIRM_DIR", None)
        super().setUp()
        self.short = self.application.short

    def tearDown(self):
        super().tearDown()
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.bot_root, True)

    def req(self, method, path, body=None, headers=None, agent="Mozilla/5.0 (Linux; Android 14)"):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        hdrs = {"User-Agent": agent}
        hdrs.update(headers or {})
        if body is not None:
            hdrs["Content-Type"] = "application/json"
        conn.request(method, path, body=body, headers=hdrs)
        resp = conn.getresponse()
        data = resp.read()
        out = {k.lower(): v for k, v in resp.getheaders()}
        conn.close()
        return resp.status, out, data

    def post_short(self, what, nonce, suffix, **kw):
        status, _h, body = self.req("POST", "/p-%s/%s" % (self.PAGE, what), json.dumps({"nonce": nonce, "suffix": suffix}),
                                    **kw)
        return status, json.loads(body.decode("utf-8")) if body.startswith(b"{") else body

    def events(self):
        d = os.path.join(self.bot_root, "run", "short-confirm")
        try:
            names = sorted(n for n in os.listdir(d) if n.endswith(".json"))
        except OSError:
            return []
        out = []
        for n in names:
            with open(os.path.join(d, n), encoding="utf-8") as fh:
                out.append(json.loads(fh.read()))
        return out

    def test_page_data_has_the_button_and_its_own_nonce_but_no_codes(self):
        code = self.short.ensure("anna", "all.yaml")
        html, data = self.page()
        self.assertTrue(data["sub"]["short"])
        self.assertRegex(data["reveal_short"], r"^[A-Za-z0-9_-]{32}$")
        self.assertNotIn(data["reveal_short"], (data["reveal"], data["reveal_sub"]))
        self.assertNotIn(code["code"], html)
        self.assertNotIn(code["display"], html)

    def test_short_json_once_per_nonce_hands_the_next_and_checks_the_line(self):
        _html, data = self.page()
        status, js = self.post_short("short.json", data["reveal_short"], "awg20.conf")
        self.assertEqual(status, 200, js)
        self.assertEqual(sorted(js["short"]), ["created", "display", "suffix", "url"])
        self.assertEqual(js["short"]["url"], "https://%s/%s" % (SUB_HOST, js["short"]["display"]))
        self.assertEqual(self.short.codes_of("anna")["awg20.conf"]["display"], js["short"]["display"])
        self.assertEqual(self.post_short("short.json", data["reveal_short"], "awg20.conf"), (409, {"error": "reveal"}))
        status, again = self.post_short("short.json", js["next"], "awg20.conf")
        self.assertEqual((status, again["short"]["display"]), (200, js["short"]["display"]), "второй раз — тот же код")
        status, bad = self.post_short("short.json", again["next"], "awg10.conf")
        self.assertEqual((status, bad["error"]), (409, "bad_suffix"), "линии 1.0 у anna нет")
        status, bad = self.post_short("short.json", bad["next"], "mihomo.yaml")
        self.assertEqual((status, bad["error"]), (409, "bad_suffix"), "синонимы — без коротких ссылок")
        status, _b = self.post_short("short.json", bad["next"], "all.yaml", agent="TelegramBot (like TwitterBot)")
        self.assertEqual(status, 404, "роботу превью — 404")
        # Бот сняли, пока страница была открыта: отказ short_need_bot (и next, чтобы нажать снова)
        os.unlink(os.path.join(self.bot, ".env"))
        status, off = self.post_short("short.json", bad["next"], "all.yaml")
        self.assertEqual((status, off["error"]), (409, "short_need_bot"))

    def test_sub_json_short_is_in_the_address_form_and_only_created(self):
        _html, data = self.page()
        status, _h, body = self.post_sub(data["reveal_sub"])
        self.assertEqual(json.loads(body.decode("utf-8"))["sub"]["short"], {}, "кодов ещё нет — пусто")
        y = self.short.ensure("anna", "all.yaml")
        c = self.short.ensure("anna", "awg20.conf")
        self.short.ensure("anna", "all.vpn")                       # на странице его нет
        _html, data = self.page()
        status, _h, body = self.post_sub(data["reveal_sub"])
        short = json.loads(body.decode("utf-8"))["sub"]["short"]
        self.assertEqual(short, {"mihomo": y["url"], "conf": {"2.0": c["url"]}})
        os.unlink(os.path.join(self.bot, ".env"))
        _html, data = self.page()
        self.assertFalse(data["sub"]["short"])
        self.assertNotIn("reveal_short", data)
        status, _h, body = self.post_sub(data["reveal_sub"])
        self.assertNotIn("short", json.loads(body.decode("utf-8"))["sub"], "без бота поля short нет")

    def test_replace_from_the_page_number_status_and_the_new_code(self):
        old = self.short.ensure("anna", "all.yaml")
        _html, data = self.page()
        status, rep = self.post_short("short-replace.json", data["reveal_short"], "all.yaml",
                                      headers={"X-Real-IP": "198.51.100.7"},
                                      agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Version/17.0 Safari/604.1")
        self.assertEqual(status, 200, rep)
        self.assertTrue(10 <= rep["number"] <= 99)
        ev = self.events()[-1]
        self.assertEqual((ev["event"], ev["kind"], ev["who"], ev["ip"], ev["device"]),
                         ("short_confirm", "replace", "page", "198.51.100.7", "iPhone · Safari"))
        self.assertNotIn("number", ev)
        status, again = self.post_short("short-replace.json", rep["next"], "all.yaml")
        self.assertEqual((status, again["error"]), (409, "short_pending"))
        self.assertTrue(0 < again["left"] <= 300)
        path = "/p-%s/short-status.json?r=%s" % (self.PAGE, rep["request"])
        status, _h, body = self.req("GET", path)
        self.assertEqual((status, json.loads(body.decode("utf-8"))["decision"]), (200, "pending"))
        res = self.short.decide(rep["request"], str(rep["number"]), "bot:123456789")
        status, _h, body = self.req("GET", path)
        js = json.loads(body.decode("utf-8"))
        self.assertEqual((js["decision"], js["display"], js["url"]), ("ok", res["display"], res["url"]))
        self.assertNotEqual(res["display"], old["display"])
        # Чужой и неизвестный запрос — 404; вход по ссылке (не замена) со страницы не опрашивается
        login = self.short.visit(res["display"], "198.51.100.9", browser=True)
        for rid in (login["request"], "A" * 16, "short"):
            status, _h, body = self.req("GET", "/p-%s/short-status.json?r=%s" % (self.PAGE, rid))
            self.assertEqual((status, body), (404, ps.NGINX_404), rid)

    def test_code_route_confirm_page_status_and_trusted_fetch(self):
        code = self.short.ensure("anna", "all.yaml")
        app_hdr = {"X-Real-IP": "198.51.100.1", "Accept": "*/*"}
        status, _h, body = self.req("GET", "/" + code["display"], headers=app_hdr, agent="clash-verge/v2.4.2")
        self.assertEqual((status, body), (404, ps.NGINX_404), "приложению без доверия — 404")
        html_hdr = {"X-Real-IP": "198.51.100.1", "Accept": "text/html,*/*;q=0.8"}
        status, hdrs, body = self.req("GET", "/" + code["display"].lower(), headers=html_hdr)
        self.assertEqual(status, 200)
        self.assertEqual(hdrs["content-security-policy"], ps.SHORT_CSP)
        html = body.decode("utf-8")
        rid = re.search(r'data-r="([A-Za-z0-9_-]{16})"', html).group(1)
        number = int(re.search(r'<b class="pg-sc__num">(\d+)</b>', html).group(1))
        self.assertIn("Назовите администратору число", html)
        self.assertNotIn(code["code"], html.replace(code["display"].lower(), ""))
        status, _h, body = self.req("GET", "/%s?r=%s" % (code["display"], rid), headers={"Accept": "application/json"})
        poll = json.loads(body.decode("utf-8"))
        self.assertEqual(sorted(poll), ["decision", "left"], "в опросе — ни кода, ни числа")
        self.assertTrue(poll["decision"] == "pending" and 290 < poll["left"] <= 300, poll)
        self.short.decide(rid, str(number), "bot:Иван")
        status, _h, full = self.request("GET", "/s-%s/all.yaml" % self.SUB, agent="clash-verge/v2.4.2")
        self.assertEqual(status, 200)
        status, hdrs, body = self.req("GET", "/" + code["display"], headers=app_hdr, agent="clash-verge/v2.4.2")
        self.assertEqual((status, body), (200, full), "доверенный забор — то же, что полный адрес")
        self.assertIn("profile-title", hdrs)
        self.assertEqual(self.short._load()["access"]["%s 198.51.100.1" % code["code"]]["s"], "trust")
        spaced = "/" + code["display"].lower().replace("-", "%20")
        status, _h, body = self.req("GET", spaced, headers=app_hdr, agent="clash-verge/v2.4.2")
        self.assertEqual((status, body), (200, full), "строчными и с пробелом (%20) вместо тире — тот же код")
        # Обрамление копирования из мессенджера и разметки — тот же код (набор как у shortlinks.FRAME и у nginx)
        for lead, tail in (("", "."), ("", ","), ("", ";"), ("", ":"), ("", "!"), ("", "%3F"), ("", ")"), ("", "%5D"),
                           ("", "%22"), ("", "'"), ("", "%C2%BB"), ("", "%E2%80%A6"), ("", "%E2%80%9D"), ("", "%09"),
                           ("", "%E2%80%99"), ("", "%E2%80%93"), ("", "%E2%80%94"), ("", "%C2%A0"), ("", "%20"),
                           ("", ")."), ("", "..."), ("", ".%20"), ("", "./"), ("", "%C2%BB."), ("", "*"), ("", ">"),
                           ("", "+"), ("", "~"), ("", "%E2%80%A2"), ("(", ")"), ("%5B", "%5D"), ("%C2%AB", "%C2%BB"),
                           ("%22", "%22"), ("%E2%80%9C", "%E2%80%9D"), ("*", "*"), ("_", "_"), ("%60", "%60"),
                           ("%3C", "%3E"), (":", ""), (".", ""), ("..", "."), ("%E2%80%A2%20", ""), ("%E2%80%9E", "%E2%80%9C")):
            status, _h, body = self.req("GET", "/" + lead + code["display"] + tail, headers=app_hdr,
                                        agent="clash-verge/v2.4.2")
            self.assertEqual((status, body), (200, full), "обрамление %r…%r — тот же код" % (lead, tail))
        self.assertEqual(ps.SHORT_FRAME, ps.shortlinks.FRAME)
        # Без бота — 404 и доверенному
        os.unlink(os.path.join(self.bot, ".env"))
        status, _h, body = self.req("GET", "/" + code["display"], headers=app_hdr, agent="clash-verge/v2.4.2")
        self.assertEqual((status, body), (404, ps.NGINX_404))

    # ── SHORT-03 (владелец 05.10): после «Вход подтверждён» — полная ссылка подписки этому устройству ─────────────
    ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0 Mobile Safari/604.1"
    ANDROID = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/129.0 Mobile Safari/537.36"
    CODES = {"all.yaml": ("NV439BKX", "NV43-9BKX"), "all.txt": ("KT72QP4W", "KT72-QP4W"),
             "awg20.vpn": ("RZ85MD3H", "RZ85-MD3H"), "awg20.conf": ("GW64TC9P", "GW64-TC9P"),
             "awg31.conf": ("HB57XN2K", "HB57-XN2K")}

    def confirm_page(self, suffix, ip, agent=IPHONE, decide=True, decided=None):
        """Код пары (без случайности: CODES) и вход по нему с адреса ip → (показ, номер запроса, одноразовое значение,
        HTML страницы ожидания, заголовки); decide — администратор нажал верное число (decided — когда, для окна)."""
        from unittest import mock
        code, display = self.CODES[suffix]
        with mock.patch.object(ps.shortlinks, "new_code", lambda: code), \
                mock.patch.object(ps.shortlinks, "display_of", lambda c: display):
            self.assertEqual(self.short.ensure("anna", suffix)["display"], display)
        status, hdrs, body = self.req("GET", "/" + display, headers={"X-Real-IP": ip, "Accept": self.ACCEPT}, agent=agent)
        html = body.decode("utf-8")
        self.assertEqual(status, 200, html[:300])
        rid = re.search(r'data-r="([A-Za-z0-9_-]{16})"', html).group(1)
        nonce = re.search(r'data-n="([A-Za-z0-9_-]{32})"', html).group(1)
        if decide:
            number = re.search(r'<b class="pg-sc__num">(\d+)</b>', html).group(1)
            self.assertEqual(self.short.decide(rid, number, "bot:Иван", now=decided)["decision"], "ok")
        return display, rid, nonce, html, hdrs

    def post_full(self, display, rid, nonce, ip, agent=IPHONE, lang=""):
        status, hdrs, body = self.req("POST", "/%s%s" % (display, "?lang=" + lang if lang else ""),
                                      json.dumps({"request": rid, "nonce": nonce}),
                                      headers={"X-Real-IP": ip, "Accept": "application/json"}, agent=agent)
        return status, hdrs, json.loads(body.decode("utf-8")) if body.startswith(b"{") else body

    def no_store(self, hdrs, what):
        self.assertEqual((hdrs.get("cache-control"), hdrs.get("pragma")),
                         ("no-store, no-cache, must-revalidate, private", "no-cache"), what)

    def no_secrets(self, text, what):
        for bad in ("/s-", self.SUB, "PrivateKey", "vpn://", "amneziawg://", "proxies:"):
            self.assertNotIn(bad, text, what)

    def test_full_link_only_after_ok_by_request_and_one_time_nonce(self):
        audit = os.path.join(self.bot_root, "audit.log")
        self.application.audit.path = audit
        lines = len(self.log.lines)
        display, rid, nonce, html, hdrs = self.confirm_page("all.txt", "198.51.100.21", decide=False)
        # До подтверждения: ни /s-, ни секрета — ни в HTML, ни в опросе; одноразовое значение — в разметке, кнопка
        # «Показать полную ссылку» скрыта (её открывает скрипт), «Показать содержимое» — по нажатию, без самообновления
        self.no_store(hdrs, "страница ожидания")
        self.no_secrets(html, "страница ожидания")
        self.assertIn('<button type="button" class="pg-btn pg-btn--inverse" id="sc-reveal" hidden>', html)
        self.assertIn('<a class="pg-sc__more" id="sc-content" href="/%s?go=%s">Показать содержимое</a>' % (display, rid), html)
        self.assertNotIn("?go=%s\"></noscript>" % rid, html, "автоперехода нет и без JavaScript")
        self.assertNotIn("location.replace", html)
        status, hdrs, body = self.req("GET", "/%s?r=%s" % (display, rid), headers={"Accept": "application/json"})
        self.assertEqual((status, sorted(json.loads(body.decode("utf-8")))), (200, ["decision", "left"]))
        self.no_secrets(body.decode("utf-8"), "опрос до решения")
        self.assertEqual(self.post_full(display, rid, nonce, "198.51.100.21")[:3:2], (404, ps.NGINX_404),
                         "до «Вход подтверждён» — ничего")
        number = re.search(r'<b class="pg-sc__num">(\d+)</b>', html).group(1)
        self.short.decide(rid, number, "bot:Иван")
        status, _h, body = self.req("GET", "/%s?r=%s" % (display, rid), headers={"Accept": "application/json"})
        poll = json.loads(body.decode("utf-8"))
        self.assertTrue(poll["decision"] == "ok" and 880 < poll["full_left"] <= 900, poll)
        self.no_secrets(body.decode("utf-8"), "опрос после решения")
        # Чужой, выдуманный номер — 404 без баллов; номер верный, значение чужое — 409 reveal без следующего
        for mark in ("A" * 16, "short", ""):
            status, _h, out = self.post_full(display, mark, nonce, "203.0.113.30")
            self.assertEqual((status, out), (404, ps.NGINX_404), mark)
        self.assertNotIn("203.0.113.30", self.short._load()["addrs"], "ни баллов, ни бана")
        self.assertEqual(self.post_full(display, rid, "N" * 32, "198.51.100.21")[::2], (409, {"error": "reveal"}))
        # Номер этого входа и значение этой страницы — полная ссылка; телефон уже в другой сети — всё равно ей
        url = "https://%s/s-%s/all.txt" % (SUB_HOST, self.SUB)
        status, hdrs, full = self.post_full(display, rid, nonce, "2001:db8:21::7")
        self.assertEqual(status, 200, full)
        self.no_store(hdrs, "полная ссылка")
        self.assertEqual((full["kind"], full["suffix"], full["url"]), ("link", "all.txt", url))
        self.assertEqual(full["apps"], [{"id": "incy", "name": "incy", "name_en": "", "href": "incy://add/" + url}])
        self.assertEqual(full["commands"], [])
        self.assertRegex(full["next"], r"^[A-Za-z0-9_-]{32}$")
        self.assertIn('<div class="pg-addr" id="sc-url">%s</div>' % url, full["html"])
        self.assertIn('<a class="pg-btn" href="incy://add/%s">' % url, full["html"])
        self.assertIn("<span>Открыть в incy</span>", full["html"])
        if self.application.qr.available():
            self.assertTrue(base64.b64decode(full["qr"]).startswith(b"\x89PNG"))
            self.assertIn('<img src="data:image/png;base64,%s"' % full["qr"], full["html"])
        # Одноразовое: повтор — 409; следующее из ответа — снова ссылка
        self.assertEqual(self.post_full(display, rid, nonce, "198.51.100.21")[::2], (409, {"error": "reveal"}))
        status, _h, again = self.post_full(display, rid, full["next"], "198.51.100.21")
        self.assertEqual((status, again["url"]), (200, url))
        # В журнале службы — ни кода, ни секрета; в аудите — имя и суффикс
        logged = " ".join(text for _level, text in self.log.lines[lines:])
        for bad in ("KT72", "kt72", self.SUB, rid, nonce):
            self.assertNotIn(bad, logged)
        self.assertIn("полная ссылка выдана странице подтверждения (all.txt)", logged)
        with open(audit, encoding="utf-8") as fh:
            rows = [json.loads(line) for line in fh]
        self.assertEqual([(r["action"], r["details"]) for r in rows if r["action"] == "short-full"],
                         [("short-full", "user=anna suffix=all.txt")] * 2)
        self.assertNotIn(self.SUB, json.dumps(rows))

    def test_full_link_for_each_kind_of_suffix(self):
        base = "https://%s/s-%s/" % (SUB_HOST, self.SUB)
        quote = lambda s: urllib.parse.quote(s, safe="-_.!~*'()")      # noqa: E731 — как encodeURIComponent
        # all.yaml на Android — клиенты mihomo с этой платформы и их deeplink (подпись, интервал, адрес — целиком)
        display, rid, nonce, _html, _h = self.confirm_page("all.yaml", "198.51.100.31", agent=self.ANDROID)
        status, _h, full = self.post_full(display, rid, nonce, "198.51.100.31", agent=self.ANDROID)
        self.assertEqual((status, full["kind"], full["url"]), (200, "link", base + "all.yaml"))
        self.assertEqual([(a["id"], a["href"]) for a in full["apps"]], [
            ("flclash", "flclash://install-config?url=" + quote(base + "all.yaml")),
            ("clash-meta", "clashmeta://install-config?name=%s&update-interval=720&url=%s"
             % (quote("Анна"), quote(base + "all.yaml")))])
        self.assertIn("<span>Открыть в FlClash</span>", full["html"])
        self.assertIn('href="clashmeta://install-config?name=%s&amp;update-interval=720&amp;url=' % quote("Анна"),
                      full["html"], "амперсанды в атрибуте — экранированы")
        # awgNN.vpn — сам ключ vpn:// (AmneziaVPN по ссылке не обновляется), адреса нет
        display, rid, nonce, _html, _h = self.confirm_page("awg20.vpn", "198.51.100.32")
        status, _h, full = self.post_full(display, rid, nonce, "198.51.100.32")
        self.assertEqual((status, full["kind"], full["key"], full["key_qr"]), (200, "key", "vpn://FAKE-anna-awg2", []))
        self.assertNotIn("url", full)
        self.assertNotIn("/s-", full["html"])
        self.assertIn('<div class="pg-addr" id="sc-key">vpn://FAKE-anna-awg2</div>', full["html"])
        self.assertIn("<span>Скопировать ключ</span>", full["html"])
        # .conf — адрес (для роутера и WG Tunnel), «Скачать файл» — тот же короткий адрес с меткой, команда Keenetic
        display, rid, nonce, _html, _h = self.confirm_page("awg20.conf", "198.51.100.33", agent=self.ANDROID)
        status, _h, full = self.post_full(display, rid, nonce, "198.51.100.33", agent=self.ANDROID)
        self.assertEqual((status, full["kind"], full["url"]), (200, "file", base + "awg20.conf"))
        self.assertEqual(full["commands"], [{"id": "keenetic", "name": "Keenetic / Netcraze", "name_en": "",
                                             "text": "interface Wireguard9 wireguard import " + base + "awg20.conf"}])
        self.assertEqual([(a["id"], a["href"]) for a in full["apps"]],
                         [("wgtunnel", "wg://%s/s-%s/awg20.conf" % (SUB_HOST, self.SUB))])
        self.assertIn('<a class="pg-mini" href="/%s?go=%s">' % (display, rid), full["html"])
        self.assertIn("<span>Скачать файл</span>", full["html"])
        self.assertIn('<div class="pg-addr" id="sc-cmd-1">interface Wireguard9 wireguard import %sawg20.conf</div>'
                      % base, full["html"])
        # У Keenetic нет линии 3.1 — команды нет
        display, rid, nonce, _html, _h = self.confirm_page("awg31.conf", "198.51.100.34")
        status, _h, full = self.post_full(display, rid, nonce, "198.51.100.34")
        self.assertEqual((status, full["kind"], full["commands"]), (200, "file", []))

    def test_full_link_window_15_minutes_then_a_new_check(self):
        # Окно выдачи вышло: POST — 409 expired, перезагрузка (?r=) — «Нужна новая проверка», без запроса в бот
        display, rid, nonce, _html, _h = self.confirm_page("all.yaml", "198.51.100.41",
                                                           decided=time.time() - ps.shortlinks.FULL_TTL - 5)
        events = len(self.events())
        self.assertEqual(self.post_full(display, rid, nonce, "198.51.100.41")[::2], (409, {"error": "expired"}))
        status, hdrs, body = self.req("GET", "/%s?r=%s" % (display, rid),
                                      headers={"X-Real-IP": "198.51.100.41", "Accept": self.ACCEPT})
        page = body.decode("utf-8")
        self.assertEqual((status, re.findall(r'<div data-sc="([a-z]+)">', page)), (200, ["moved"]))
        self.assertNotIn("data-n=", page)
        self.no_store(hdrs, "«Нужна новая проверка»")
        self.assertEqual(len(self.events()), events)
        status, _h, body = self.req("GET", "/%s?r=%s" % (display, rid), headers={"Accept": "application/json"})
        self.assertEqual(json.loads(body.decode("utf-8"))["full_left"], 0)

    def test_reload_in_the_window_and_the_address_of_the_page(self):
        # Перезагрузка итога (?r=) в окне: с адреса, начинавшего вход, — новое одноразовое значение; с другого (адрес
        # страницы открыли на другом устройстве или в другой сети) — без него: полной ссылки такая отрисовка не даст
        display, rid, nonce, _html, _h = self.confirm_page("all.txt", "198.51.100.51")
        here = {"X-Real-IP": "198.51.100.51", "Accept": self.ACCEPT}
        status, hdrs, body = self.req("GET", "/%s?r=%s&lang=ru" % (display, rid), headers=here)
        page = body.decode("utf-8")
        self.assertEqual(re.findall(r'<div data-sc="([a-z]+)">', page), ["ok"])
        fresh = re.search(r'data-n="([A-Za-z0-9_-]{32})"', page).group(1)
        self.assertNotEqual(fresh, nonce)
        self.no_secrets(page, "итог")
        self.no_store(hdrs, "итог")
        status, _h, body = self.req("GET", "/%s?r=%s" % (display, rid),
                                    headers={"X-Real-IP": "203.0.113.52", "Accept": self.ACCEPT})
        self.assertEqual(re.findall(r'<div data-sc="([a-z]+)">', body.decode("utf-8")), ["ok"])
        self.assertNotIn("data-n=", body.decode("utf-8"))
        self.assertEqual(self.post_full(display, rid, fresh, "203.0.113.52")[0], 200, "значение — этой отрисовки")
        # Английский: итог и блок полной ссылки
        status, _h, body = self.req("GET", "/%s?r=%s&lang=en" % (display, rid), headers=here)
        page = body.decode("utf-8")
        self.assertIn('<span class="pg-sc__sub">Add the full link to the app — it works from any network without '
                      'confirmation.</span>', page)
        self.assertIn("<span>Show the full link</span>", page)
        nonce_en = re.search(r'data-n="([A-Za-z0-9_-]{32})"', page).group(1)
        status, _h, full = self.post_full(display, rid, nonce_en, "198.51.100.51", lang="en")
        self.assertIn('<span class="pg-sc__cap">Full subscription link</span>', full["html"])
        self.assertIn("<span>Open in incy</span>", full["html"])

    def test_show_contents_by_press_and_the_app_gets_what_it_got(self):
        # «Показать содержимое» — ?go=<запрос> по нажатию: открытый адрес получает то же, что приложение (браузеру
        # all.yaml — текстом), это первый забор; другой адрес — «Нужна новая проверка» без запроса в бот (SHORT-02)
        display, rid, _nonce, _html, _h = self.confirm_page("all.yaml", "198.51.100.61")
        events = len(self.events())
        status, hdrs, body = self.req("GET", "/%s?go=%s" % (display, rid),
                                      headers={"X-Real-IP": "198.51.100.62", "Accept": self.ACCEPT})
        self.assertEqual((status, re.findall(r'<div data-sc="([a-z]+)">', body.decode("utf-8"))), (200, ["moved"]))
        self.no_store(hdrs, "«Нужна новая проверка»")
        self.assertEqual(len(self.events()), events, "в бот ничего нового")
        _s, full_hdrs, full = self.request("GET", "/s-%s/all.yaml" % self.SUB, agent="clash-verge/v2.4.2")
        status, hdrs, body = self.req("GET", "/%s?go=%s" % (display, rid),
                                      headers={"X-Real-IP": "198.51.100.61", "Accept": self.ACCEPT})
        self.assertEqual((status, body), (200, full))
        self.assertEqual(hdrs["content-type"], "text/plain; charset=utf-8")
        self.assertEqual(hdrs["content-disposition"], "inline" + full_hdrs["content-disposition"][len("attachment"):])
        self.no_store(hdrs, "содержимое ссылки")
        self.assertEqual(self.short._load()["access"]["NV439BKX 198.51.100.61"]["s"], "trust")
        status, hdrs, body = self.req("GET", "/" + display, headers={"X-Real-IP": "198.51.100.61", "Accept": "*/*"},
                                      agent="clash-verge/v2.4.2")
        self.assertEqual((status, body), (200, full))
        for h in ("content-type", "content-disposition", "cache-control", "profile-title", "subscription-userinfo"):
            self.assertEqual(hdrs[h], full_hdrs[h], h)

    def test_code_shown_as_s_dash_typed_lowercase_is_the_code(self):
        # Показ «S-Y0-CPF10», набранный строчными, — /s-y0-cpf10: начало как у адреса подписки (/s-<секрет>/…), но
        # это тот же код, а не промах по подписке с началом пути в журнале (раньше — 404 примерно у 1 % кодов)
        from unittest import mock
        with mock.patch.object(ps.shortlinks, "new_code", lambda: "SY0CPF10"), \
                mock.patch.object(ps.shortlinks, "display_of", lambda code: "S-Y0-CPF10"):
            code = self.short.ensure("anna", "all.yaml")
        self.assertEqual(code["display"], "S-Y0-CPF10")
        lines = len(self.log.lines)
        for path in ("/s-y0-cpf10", "/s-y0-cpf10/", "/S-Y0-CPF10"):
            status, _h, body = self.req("GET", path, headers={"X-Real-IP": "198.51.100.3", "Accept": "text/html"})
            self.assertEqual(status, 200, path)
            self.assertIn("Назовите администратору число", body.decode("utf-8"), path)
        self.assertFalse([t for _l, t in self.log.lines[lines:] if "s-y0" in t.lower()], "ни начала кода в журнале")
        status, _h, body = self.req("POST", "/s-y0-cpf10", headers={"X-Real-IP": "198.51.100.3"})
        self.assertEqual((status, body), (404, ps.NGINX_404), "POST — 404, как и раньше")

    def test_other_methods_previews_and_address_header(self):
        code = self.short.ensure("anna", "all.yaml")
        for method in ("POST", "PUT", "DELETE"):
            status, _h, body = self.req(method, "/" + code["display"], headers={"X-Real-IP": "198.51.100.5"})
            self.assertEqual((status, body), (404, ps.NGINX_404), method)
        status, _h, body = self.req("GET", "/ZZZZ2222", headers={"X-Real-IP": "198.51.100.5", "Accept": "text/html"},
                                    agent="TelegramBot (like TwitterBot)")
        self.assertEqual((status, body), (404, ps.NGINX_404))
        self.assertNotIn("198.51.100.5", self.short._load()["addrs"], "ни метод, ни робот превью баллов не дают")
        status, _h, _b = self.req("GET", "/ZZZZ2222", headers={"X-Real-IP": "198.51.100.5", "Accept": "text/html"})
        self.assertEqual(self.short._load()["addrs"]["198.51.100.5"]["p"], 1, "адрес — из X-Real-IP с петли")
        # Слова сайта, даты и номера — не коды: обычный 404, ни балла, ни строки в журнале службы
        lines = len(self.log.lines)
        for word in ("download", "wp-admin", "phpmyadmin", "contacts", "Database", "12345678", "2024-10-01",
                     "20241001", "2024%2010%2001", "1234567A"):
            status, _h, body = self.req("GET", "/" + word, headers={"X-Real-IP": "198.51.100.8", "Accept": "text/html"})
            self.assertEqual((status, body), (404, ps.NGINX_404), word)
        self.assertNotIn("198.51.100.8", self.short._load()["addrs"])
        self.assertEqual(self.log.lines[lines:], [], "и ни строки в журнале")
        handler = ps.build_handler(self.application)
        for peer, header, want in ((("127.0.0.1", 1), "203.0.113.9", "203.0.113.9"),
                                   (("203.0.113.1", 1), "198.51.100.9", "203.0.113.1"),
                                   (("::1", 1), "198.51.100.9", "::1"),
                                   (("127.0.0.1", 1), "not-an-ip", "127.0.0.1")):
            fake = type("F", (), {"client_address": peer, "headers": {"X-Real-IP": header}})()
            self.assertEqual(handler.short_ip(fake), want, peer)

    def test_confirm_page_variants_and_script(self):
        html = ps.render_short("ru", {"state": "wait", "request": "R" * 16, "number": 42, "left": 299}, "/NV43-9BKX")
        self.assertEqual(re.findall(r'<div data-sc="([a-z]+)">', html), ["pending"], "виден только итог состояния")
        spaced = ps.render_short("ru", {"state": "wait", "request": "R" * 16, "number": 42, "left": 299}, "/nv43 9bkx")
        self.assertIn('content="5;url=/nv43%209bkx?r=' + "R" * 16 + '"', spaced, "пробел в пути — снова %20")
        self.assertIn('<span id="sc-left">4:59</span>', html)
        self.assertIn('<meta http-equiv="refresh" content="5;url=/NV43-9BKX?r=' + "R" * 16 + '">', html)
        js = re.search(r"<script>(.*?)</script>", html, re.S).group(1)
        self.assertEqual(js, ps.SHORT_JS)
        digest = base64.b64encode(__import__("hashlib").sha256(js.encode("utf-8")).digest()).decode("ascii")
        self.assertIn("'sha256-%s'" % digest, ps.SHORT_CSP)
        for bad in ("=>", "let ", "const ", "`", "fetch("):
            self.assertNotIn(bad, ps.SHORT_JS, "ES5 и XMLHttpRequest: браузеры телевизоров")
        frozen = ps.render_short("ru", {"state": "frozen", "days": 30}, "/X")
        self.assertIn('<span data-days="1" hidden>на сутки</span><span data-days="30">на месяц</span>', frozen)
        self.assertNotIn("http-equiv", frozen, "итог — без самообновления")
        wrong = ps.render_short("en", {"state": "wrong", "strikes": 2}, "/X")
        self.assertIn('<span class="sc-strikes">2</span> of 3 in a row', wrong)
        # PAGE-COS-02: «Новый запрос» есть в трёх итогах (wrong, expired, moved) — это скрытый шаблон состояний: видна и
        # фокусируется не больше одной ссылки, остальные — внутри итога с hidden (display:none, вне дерева доступности)
        for st in ("wait", "ok", "wrong", "frozen", "denied", "swapped", "expired", "busy", "limit", "moved"):
            page = ps.render_short("ru", {"state": st, "request": "R" * 16, "number": 42, "left": 299}, "/NV43-9BKX")
            parts = re.findall(r'<div data-sc="([a-z]+)"( hidden)?>(.*?)</div>', page, re.S)
            shown = [name for name, hid, inner in parts if "Новый запрос" in inner and not hid]
            self.assertEqual(page.count("Новый запрос"), sum(inner.count("Новый запрос") for _n, _h, inner in parts))
            self.assertEqual(shown, [st] if st in ("wrong", "expired", "moved") else [], st)
        self.assertIn("el.setAttribute('hidden', '')", ps.SHORT_JS, "смена итога прячет прежний вместе с его ссылкой")
        self.assertEqual(ps.mask_path("code 400, message Bad request ('GET /NV43-9BKX?r=abc HTTP/1.1')"),
                         "code 400, message Bad request ('GET /*** HTTP/1.1')")
        self.assertEqual(ps.mask_path("code 400, message Bad request ('GET /NV43-9BKX?go=abc HTTP/1.1')"),
                         "code 400, message Bad request ('GET /*** HTTP/1.1')", "метка перехода — тоже под маской")

    def test_confirm_page_ok_part_and_full_block_in_both_languages(self):
        # SHORT-03: итог «Вход подтверждён» — две строки (полная ссылка из любой сети; телевизору и роутеру можно
        # короткую), «Показать полную ссылку» (скрыта: её открывает скрипт), загрузка и отказы — скрытыми, место для блока
        # полной ссылки, «Показать содержимое» — тот же короткий адрес с меткой ?go=<запрос> (путь — как его набрали),
        # без JavaScript — честная строка. Автоперехода нет ни со скриптом, ни без. Оба языка
        rid, nonce = "Rq_-" * 4, "N0n_" * 8
        for lang, said, short, reveal, content, nojs in (
                ("ru", "Добавьте в приложение полную ссылку — она работает из любой сети без подтверждения.",
                 "Телевизору и роутеру можно оставить короткую: добавьте её в течение 15 минут — дальше из этой сети она "
                 "продлевается с каждым обновлением.", "Показать полную ссылку", "Показать содержимое",
                 "Полную ссылку страница показывает с включённым JavaScript."),
                ("en", "Add the full link to the app — it works from any network without confirmation.",
                 "A TV or a router can keep the short one: add it within 15 minutes — after that it renews from this "
                 "network with every update.", "Show the full link", "Show the contents",
                 "The page shows the full link with JavaScript on.")):
            ui = ps.UI[lang]
            self.assertEqual((ui["sc_ok"], ui["sc_ok_short"], ui["sc_reveal"], ui["sc_content"], ui["sc_full_nojs"]),
                             (said, short, reveal, content, nojs))
            ok = ps.render_short(lang, {"state": "ok", "request": rid, "nonce": nonce}, "/nv43 9bkx")
            self.assertIn('data-r="%s" data-left="0" data-n="%s">' % (rid, nonce), ok)
            self.assertIn('<div data-sc="ok"><b class="pg-sc__big">%s</b><span class="pg-sc__sub">%s</span>'
                          '<span class="pg-sc__sub">%s</span><button type="button" class="pg-btn pg-btn--inverse" '
                          'id="sc-reveal" hidden>' % (ui["sc_ok_t"], said, short), ok)
            self.assertIn('<span>%s</span></button>' % reveal, ok)
            for kind, text in (("loading", ui["sc_full_loading"]), ("busy", ui["sc_full_busy"]),
                               ("failed", ui["sc_full_failed"])):
                self.assertRegex(ok, r'<div class="pg-kn[^"]*" data-full="%s" role="status" hidden>.*?<span '
                                     r'class="pg-kn__t">%s</span></div>' % (kind, re.escape(text)))
            self.assertIn('<div class="pg-sc__full" id="sc-full"></div><a class="pg-sc__more" id="sc-content" '
                          'href="/nv43%%209bkx?go=%s">%s</a><noscript><span class="pg-sc__sub">%s</span></noscript></div>'
                          % (rid, content, nojs), ok)
            self.assertNotIn("http-equiv", ok, "итог без самообновления: автоперехода нет и без JavaScript")
            pending = ps.render_short(lang, {"state": "wait", "request": rid, "number": 42, "left": 299,
                                             "nonce": nonce}, "/NV43-9BKX")
            self.assertIn('data-n="%s"' % nonce, pending)
            self.assertIn('content="5;url=/NV43-9BKX?r=%s"' % rid, pending)
            for page in (ok, pending):
                self.assertNotIn("/s-", page)
            for st in ("moved", "busy", "limit", "frozen"):
                page = ps.render_short(lang, {"state": st, "nonce": nonce}, "/NV43-9BKX")
                self.assertNotIn('id="sc-reveal"', page, st)
                self.assertNotIn("data-n=", page, "без номера запроса — без значения")
            self.assertNotIn("data-n=", ps.render_short(lang, {"state": "ok", "request": rid, "nonce": "<x>"}, "/X"))
        # Блок полной ссылки (ответ POST /<КОД>) — экранирован, на двух языках
        url = "https://vpn.example.com/s-" + "S" * 43 + "/all.yaml"
        full = {"kind": "link", "url": url, "qr": "", "key_qr": [],
                "apps": [{"id": "x", "name": "Clash <b>", "name_en": "", "href": "clash://install-config?a=1&b=\"2\""}],
                "commands": [{"id": "k", "name": "Keenetic / Netcraze", "name_en": "", "text": "import " + url}]}
        for lang, cap, copy, open_in, cmd in (("ru", "Полная ссылка подписки", "Скопировать", "Открыть в Clash &lt;b&gt;",
                                               "Команда для роутера Keenetic / Netcraze"),
                                              ("en", "Full subscription link", "Copy", "Open in Clash &lt;b&gt;",
                                               "Command for the Keenetic / Netcraze router")):
            html = ps.render_short_full(lang, full, "/NV43-9BKX", rid)
            self.assertIn('<span class="pg-sc__cap">%s</span><div class="pg-addr" id="sc-url">%s</div>' % (cap, url), html)
            self.assertIn('data-copy="sc-url" data-done="%s"' % ps.UI[lang]["sc_copied"], html)
            self.assertIn("<span>%s</span></button>" % copy, html)
            self.assertIn('<a class="pg-btn" href="clash://install-config?a=1&amp;b=&quot;2&quot;">', html)
            self.assertIn("<span>%s</span></a>" % open_in, html)
            self.assertIn('<span class="pg-sc__cap">%s</span><div class="pg-addr" id="sc-cmd-1">import %s</div>'
                          % (cmd, url), html)
            self.assertNotIn("<img", html, "QR нет — нет и картинки")
        png = base64.b64encode(b"\x89PNG\r\n\x1a\nxx").decode("ascii")
        key = ps.render_short_full("ru", {"kind": "key", "key": "vpn://K<ey>", "key_qr": [png, png], "apps": [],
                                          "commands": []}, "/NV43-9BKX", rid)
        self.assertIn('<div class="pg-addr" id="sc-key">vpn://K&lt;ey&gt;</div>', key)
        self.assertEqual(key.count('<img src="data:image/png;base64,%s"' % png), 2)
        self.assertIn("QR ключа — 2 из 2", key)
        self.assertNotIn("sc-url", key)
        conf = ps.render_short_full("en", dict(full, kind="file", apps=[], commands=[]), "/nv43 9bkx", rid)
        self.assertIn('<a class="pg-mini" href="/nv43%%209bkx?go=%s">' % rid, conf)
        self.assertIn("<span>Download the file</span>", conf)
        # deeplink — как subDeeplink в app.js
        self.assertEqual(ps.sub_deeplink("incy://add/{url}", url, "Анна", 720), "incy://add/" + url)
        self.assertEqual(ps.sub_deeplink("wg://{host_path}", url, "", 0), "wg://" + url[len("https://"):])
        self.assertEqual(ps.sub_deeplink("clashmeta://install-config?name={name}&update-interval={minutes}&url={url}",
                                         url, "Анна Б.", 720),
                         "clashmeta://install-config?name=%D0%90%D0%BD%D0%BD%D0%B0%20%D0%91.&update-interval=720&url="
                         + urllib.parse.quote(url, safe="-_.!~*'()"))
        for tpl in ("javascript:{url}", "x://other?{url}", ""):
            self.assertEqual(ps.sub_deeplink(tpl, url, "", 0), "", tpl)
        self.assertEqual(ps.sub_deeplink("incy://add/{url}", "http://x/y", "", 0), "")
        self.assertEqual((ps.png_b64(png), ps.png_b64("bm90IGEgcG5n"), ps.png_b64("@@")), (png, "", ""))
        # Скрипт: автоперехода нет; полная ссылка — только по нажатию человека; значение — в sessionStorage, не в адресе
        js = ps.SHORT_JS
        self.assertNotIn("location.replace", js)
        self.assertNotIn("location.href =", js)
        self.assertIn("if ((e && e.isTrusted === false) || asking || !nonce || !full) { return; }", js)
        self.assertIn("x.send(JSON.stringify({request: rid, nonce: nonce}));", js)
        self.assertIn("window.sessionStorage.setItem(slot, value)", js)
        self.assertIn("img-src 'self' data:;", ps.SHORT_CSP)

    def test_script_and_words_of_the_tab(self):
        js = ps.APP_JS
        for fn in ("function shortSwapPress(e) {", "function subShortPress(e) {"):
            body = js[js.index(fn):]
            self.assertIn("isTrusted === false)", body[:body.index("\n  }\n")], fn)
        post = js[js.index("function shortPost(path, body) {"):js.index("function shortClock(s)")]
        self.assertIn("body.nonce = String(data.reveal_short || '');", post)
        self.assertIn("if (js.next) { data.reveal_short = js.next; }", post)
        self.assertIn("shortPost('short.json', {suffix: it.suffix})", js)
        self.assertIn("shortPost('short-replace.json', {suffix: it.suffix})", js)
        self.assertIn("fetch('short-status.json?r=' + encodeURIComponent(rep.request)", js)
        self.assertNotRegex(ps.PAGE_CSS, r"color-mix\(")
        ru, en = ps.UI["ru"], ps.UI["en"]
        self.assertEqual(set(ru), set(en))
        self.assertEqual((ru["sub_short_sub"], ru["sub_short_sub_tv"]), ("набрать руками, 2FA в Telegram",
                                                                         "пультом, 2FA в Telegram"))
        self.assertEqual(ru["short_2fa_t"], "Короткие ссылки работают с 2FA через Telegram-бота.")
        self.assertIn("Короткие ссылки работают с 2FA через Telegram-бота", ru["sc_2fa"])
        self.assertEqual(ru["short_how"], "Тире и регистр не важны, буква O читается как 0, I и L — как 1.")


def node_bin():
    """node ≥ 18 или None (тогда проверки скрипта страницы подтверждения пропускаются, как у test_panel_dom)."""
    node = shutil.which("node")
    if not node:
        return None
    try:
        out = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=20).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.match(r"v(\d+)\.", out)
    return node if m and int(m.group(1)) >= 18 else None


NODE = node_bin()

# Скрипт страницы подтверждения (SHORT_JS) в игрушечном DOM: элементы — из настоящей разметки render_short, часы,
# таймеры и XMLHttpRequest — подменные (ответы опроса и POST — из списков теста), location.replace/assign и
# history.replaceState только записываются, sessionStorage — словарь (или бросает, как в закрытом режиме браузера).
# Шаги — миллисекунды (подвинуть часы) или нажатия: click — человека (isTrusted), fake — из скрипта (isTrusted: false).
# Вход — JSON на stdin, выход — JSON.
SHORT_JS_HARNESS = r"""
'use strict';
const fs = require('fs'), vm = require('vm');
const inp = JSON.parse(fs.readFileSync(0, 'utf8'));
const html = inp.html;
function El(attrs) { this.attrs = Object.assign({}, attrs); this.textContent = ''; this.ls = {}; this.html = null; }
El.prototype.getAttribute = function (k) { return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null; };
El.prototype.setAttribute = function (k, v) { this.attrs[k] = String(v); };
El.prototype.removeAttribute = function (k) { delete this.attrs[k]; };
El.prototype.addEventListener = function (ev, fn) { (this.ls[ev] = this.ls[ev] || []).push(fn); };
El.prototype.querySelectorAll = function () { return []; };
Object.defineProperty(El.prototype, 'innerHTML', {get: function () { return this.html || ''; }, set: function (v) { this.html = String(v); }});
function attrsOf(s) { const out = {}; s.replace(/([a-z-]+)(?:="([^"]*)")?/g, function (_m, k, v) { out[k] = v === undefined ? '' : v; return ''; }); return out; }
const box = new El(attrsOf(/<section ([^>]*)>/.exec(html)[1]));
const parts = [], notes = [], byId = {sc: box};
html.replace(/<div (data-sc="[a-z]+"(?: hidden)?)>/g, function (_m, a) { parts.push(new El(attrsOf(a))); return ''; });
html.replace(/<div ([^>]*data-full="[a-z]+"[^>]*)>/g, function (_m, a) { notes.push(new El(attrsOf(a))); return ''; });
html.replace(/<(?:span|a|button|div) ([^>]*\bid="(sc-[a-z]+)"[^>]*)>/g, function (_m, a, id) { byId[id] = new El(attrsOf(a)); return ''; });
box.querySelectorAll = function (sel) { return sel === '[data-sc]' ? parts : sel === '[data-full]' ? notes : []; };
let now = 1000000, seq = 0, timers = [];
const moved = [], states = [], xhr = [];
function setT(fn, ms, every) { seq += 1; timers.push({id: seq, at: now + (ms || 0), fn: fn, every: every ? ms : 0}); return seq; }
function clearT(id) { timers = timers.filter(function (t) { return t.id !== id; }); }
function advance(ms) {
  const end = now + ms;
  for (;;) {
    timers.sort(function (a, b) { return a.at - b.at || a.id - b.id; });
    const t = timers[0];
    if (!t || t.at > end) { break; }
    now = t.at;
    if (t.every) { t.at += t.every; } else { timers.shift(); }
    t.fn();
  }
  now = end;
}
const polls = (inp.polls || []).slice(), posts = (inp.posts || []).slice();
function XHR() { this.readyState = 0; this.status = 0; this.responseText = ''; this.hdr = {}; }
XHR.prototype.open = function (m, u) { this.method = m; this.url = u; };
XHR.prototype.setRequestHeader = function (k, v) { this.hdr[k] = v; };
XHR.prototype.send = function (body) {
  const self = this, queue = self.method === 'POST' ? posts : polls;
  const r = queue.length ? queue.shift() : {status: 200, body: {decision: 'pending', left: 200}};
  xhr.push({method: self.method, url: self.url, body: body === undefined ? null : body, hdr: self.hdr});
  setT(function () {
    self.readyState = 4; self.status = r.status; self.responseText = JSON.stringify(r.body);
    self.onreadystatechange();
  }, 50, false);
};
const store = inp.storage || {};
const loc = {pathname: inp.path, replace: function (u) { moved.push(u); }, assign: function (u) { moved.push(u); }};
const win = {location: loc, history: {replaceState: function (s, t, u) { states.push(u); }}};
Object.defineProperty(win, 'sessionStorage', {get: function () {
  if (inp.storageThrows) { throw new Error('SecurityError'); }
  return {getItem: function (k) { return Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null; },
          setItem: function (k, v) { store[k] = String(v); }, removeItem: function (k) { delete store[k]; }};
}});
vm.runInNewContext(inp.script, {
  document: {getElementById: function (id) { return byId[id] || null; },
             documentElement: {getAttribute: function (k) { return k === 'lang' ? 'ru' : null; }}},
  window: win, XMLHttpRequest: XHR,
  setTimeout: function (f, ms) { return setT(f, ms, false); }, setInterval: function (f, ms) { return setT(f, ms, true); },
  clearTimeout: clearT, clearInterval: clearT, Date: {now: function () { return now; }}
});
const shown = function (el) { return !!el && el.getAttribute('hidden') === null; };
const snap = function () {
  return {visible: parts.filter(shown).map(function (p) { return p.getAttribute('data-sc'); }),
          notes: notes.filter(shown).map(function (n) { return n.getAttribute('data-full'); }),
          reveal: shown(byId['sc-reveal']), content: shown(byId['sc-content']),
          full: byId['sc-full'] ? byId['sc-full'].html : null,
          posts: xhr.filter(function (x) { return x.method === 'POST'; }).length};
};
const out = {};
(inp.steps || []).forEach(function (step, i) {
  if (typeof step === 'number') { advance(step); }
  else { (byId['sc-reveal'].ls.click || []).forEach(function (fn) { fn({isTrusted: step !== 'fake', currentTarget: byId['sc-reveal']}); }); }
  out['t' + i] = snap();
});
out.moved = moved; out.states = states; out.xhr = xhr; out.storage = store;
process.stdout.write(JSON.stringify(out));
"""


@unittest.skipUnless(NODE, "нет node ≥ 18 — скрипт страницы подтверждения не проверен")
class ShortConfirmScriptTests(unittest.TestCase):
    """SHORT-03: скрипт страницы подтверждения (SHORT_JS) под node. После «Вход подтверждён» — никакого перехода: адрес
    вкладки — ?r=<запрос> (history.replaceState), кнопка «Показать полную ссылку» открыта; полная ссылка — только по
    нажатию человека, POST /<КОД> {request, nonce}; одноразовое значение — из разметки (data-n) или sessionStorage,
    следующее из ответа — туда же; без значения, после окна (409 expired) и на 404 — «Нужна новая проверка»."""

    RID = "Rq_-" * 4
    NONCE = "N0n_" * 8
    PATH = "/NV43-9BKX"

    def run_js(self, view, polls=(), posts=(), steps=(), storage=None, throws=False):
        html = ps.render_short("ru", view, self.PATH)
        script = re.search(r"<script>(.*?)</script>", html, re.S).group(1)
        inp = {"html": html, "script": script, "path": self.PATH, "polls": list(polls), "posts": list(posts),
               "steps": list(steps), "storage": storage or {}, "storageThrows": throws}
        res = subprocess.run([NODE, "-e", SHORT_JS_HARNESS], input=json.dumps(inp), capture_output=True, text=True,
                             timeout=60)
        self.assertEqual(res.returncode, 0, res.stderr[-2000:])
        return json.loads(res.stdout)

    def pending(self, nonce=True):
        view = {"state": "wait", "request": self.RID, "number": 42, "left": 299}
        if nonce:
            view["nonce"] = self.NONCE
        return view

    def test_ok_never_goes_anywhere_and_the_full_link_comes_only_by_a_press(self):
        posted = {"status": 200, "body": {"kind": "link", "html": "<div>FULL</div>", "next": "X" * 32}}
        out = self.run_js(self.pending(), polls=[{"status": 200, "body": {"decision": "pending", "left": 290}},
                                                 {"status": 200, "body": {"decision": "ok", "full_left": 899}}],
                          posts=[posted], steps=[4500, 60000, "fake", 1000, "click", 10, 100])
        self.assertEqual(out["moved"], [], "сама никуда не переходит — ни сразу, ни через минуту")
        self.assertEqual(out["states"], [self.PATH + "?r=" + self.RID + "&lang=ru"], "перезагрузка — тот же итог")
        self.assertEqual(out["t0"], {"visible": ["ok"], "notes": [], "reveal": True, "content": True, "full": None,
                                     "posts": 0})
        self.assertEqual(out["t2"]["posts"], 0, "нажатие из скрипта (isTrusted: false) ничего не просит")
        self.assertEqual((out["t4"]["notes"], out["t4"]["posts"]), (["loading"], 1))
        self.assertEqual(out["t6"], {"visible": ["ok"], "notes": [], "reveal": False, "content": True,
                                     "full": "<div>FULL</div>", "posts": 1})
        post = [x for x in out["xhr"] if x["method"] == "POST"][0]
        self.assertEqual((post["url"], json.loads(post["body"]), post["hdr"]["Content-Type"]),
                         (self.PATH + "?lang=ru", {"request": self.RID, "nonce": self.NONCE}, "application/json"))
        self.assertEqual(out["storage"], {"ga-sc-n:" + self.RID: "X" * 32}, "следующее значение — вкладке, не в адрес")

    def test_nonce_from_the_tab_or_a_new_check(self):
        ok = {"state": "ok", "request": self.RID, "full_left": 600}
        posted = {"status": 200, "body": {"kind": "file", "html": "<a>F</a>", "next": "Y" * 32}}
        out = self.run_js(ok, posts=[posted], steps=[0, "click", 100], storage={"ga-sc-n:" + self.RID: self.NONCE})
        self.assertEqual((out["t0"]["reveal"], json.loads(out["xhr"][0]["body"])["nonce"]), (True, self.NONCE),
                         "перезагрузка из другой сети: значение — из sessionStorage вкладки")
        self.assertEqual((out["t2"]["full"], out["t2"]["content"]), ("<a>F</a>", False), ".conf — «Скачать файл» в блоке")
        out = self.run_js(ok, steps=[0, "click", 100])
        self.assertEqual((out["t0"]["visible"], out["t0"]["reveal"], out["t2"]["posts"]), (["moved"], False, 0),
                         "значения нет (другое устройство, чужая вкладка) — «Нужна новая проверка»")
        out = self.run_js(dict(ok, nonce=self.NONCE), posts=[posted], steps=[0, "click", 100], throws=True)
        self.assertEqual((out["t0"]["reveal"], out["t2"]["full"]), (True, "<a>F</a>"), "хранилище закрыто — в памяти")

    def test_expired_busy_and_refusals(self):
        ok = {"state": "ok", "request": self.RID, "nonce": self.NONCE}
        out = self.run_js(ok, posts=[{"status": 409, "body": {"error": "expired"}}], steps=[0, "click", 100])
        self.assertEqual((out["t2"]["visible"], out["storage"]), (["moved"], {}), "окно вышло — «Нужна новая проверка»")
        out = self.run_js(ok, posts=[{"status": 404, "body": {}}], steps=[0, "click", 100])
        self.assertEqual(out["t2"]["visible"], ["moved"])
        busy = {"status": 503, "body": {"error": "busy", "next": "Z" * 32}}
        posted = {"status": 200, "body": {"kind": "link", "html": "<i>L</i>", "next": "W" * 32}}
        out = self.run_js(ok, posts=[busy, posted], steps=[0, "click", 100, "click", 100])
        self.assertEqual(out["t2"]["notes"], ["busy"])
        self.assertEqual([json.loads(x["body"])["nonce"] for x in out["xhr"]], [self.NONCE, "Z" * 32])
        self.assertEqual(out["t4"]["full"], "<i>L</i>")
        out = self.run_js(ok, posts=[{"status": 409, "body": {"error": "reveal"}}], steps=[0, "click", 100, "click", 100])
        self.assertEqual((out["t2"]["notes"], out["t4"]["posts"], out["storage"]), (["failed"], 1, {}),
                         "значение не годится — «обновите страницу», второй раз не просит")
        for view in ({"state": "moved"}, {"state": "busy", "left": 100}, {"state": "frozen", "days": 1}):
            out = self.run_js(view, steps=[30000])
            self.assertEqual((out["moved"], out["xhr"], out["states"]), ([], [], []), view)


class MirrorRequestTests(unittest.TestCase):
    """MIRROR-01: повторная заявка на ту же сборку, пока она в очереди или загружается, — «уже в работе» (already:
    in_progress): вторую загрузку служба не ставит, заявку и её время не трогает; готовая копия — already: ready."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ga-mirror-req.")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.mirror = ps.Mirror(os.path.join(self.tmp, "mirror"),
                                ps.Catalog(os.path.join(ROOT, "lib", "clients.json"), NullLog()), NullLog())

    def test_repeat_while_queued_or_downloading_is_already_in_progress(self):
        st, err = self.mirror.request("flclash", "android", "p-one")
        self.assertEqual((st["state"], err, st.get("already")), ("queued", "", None), "первая заявка принята")
        req = os.path.join(self.tmp, "mirror", "flclash", "android", "request")
        with open(req) as fh:
            first = fh.read()
        stamp = os.stat(req).st_mtime_ns
        time.sleep(0.02)
        st, err = self.mirror.request("flclash", "android", "p-two")
        self.assertEqual((st["state"], err, st.get("already")), ("queued", "", "in_progress"))
        with open(req) as fh:
            self.assertEqual((fh.read(), os.stat(req).st_mtime_ns), (first, stamp), "заявка и её время не тронуты")
        # Загружается (живой замок рабочего, заявку он уже снял) — тоже «уже в работе», новой заявки нет
        d = os.path.dirname(req)
        os.unlink(req)
        os.makedirs(os.path.join(d, ".lock"))
        with open(os.path.join(d, ".lock", "pid"), "w") as fh:
            fh.write("%d\n" % os.getpid())
        ps.write_json_atomic(os.path.join(d, "state.json"), {"state": "downloading", "updated": ps.utc_now(),
                                                             "bytes": 1024, "total": 4096})
        st, err = self.mirror.request("flclash", "android", "p-three")
        self.assertEqual((st["state"], err, st.get("already")), ("downloading", "", "in_progress"))
        self.assertFalse(os.path.exists(req), "вторая загрузка не поставлена")
        # Готовая копия — заявка не нужна
        with open(os.path.join(d, "FlClash-0.8.99-android-arm64-v8a.apk"), "w") as fh:
            fh.write("apk")
        ps.write_json_atomic(os.path.join(d, "meta.json"), {"version": "v0.8.99", "file": "FlClash-0.8.99-android-arm64-v8a.apk",
                                                            "size": 3})
        st, err = self.mirror.request("flclash", "android", "p-four")
        self.assertEqual((st["state"], err, st.get("already")), ("ready", "", "ready"))
        self.assertFalse(os.path.exists(req))


class SubCatalogTests(unittest.TestCase):
    """Catalog.for_subs: разовое по ссылке (conf и vpn) — только линии человека, которые приложение на этой платформе не
    помечает «no»; subscription.platforms сужает платформы (AmneziaVPN и AmneziaWG — без телевизоров); список вариантов
    (AWG Manager) — переключатель «Формат»; самообновляемое (mihomo, затем txt) — первым."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ga-subs-catalog.")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def catalog(self, apps):
        path = os.path.join(self.tmp, "clients.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"apps": apps, "checked": "2026-10-04"}, fh)
        return ps.Catalog(path, NullLog())

    @staticmethod
    def app(app_id, fmt, platforms, lines=None):
        sub = {"format": fmt, "hint": {"ru": "x", "en": "x"}}
        if lines:
            sub.update(once=True, lines=lines)
        return {"id": app_id, "name": app_id, "developer": "d", "official": True, "subscription": sub,
                "platforms": {p: {"links": [], "support": dict(zip(("1.0", "1.5", "2.0", "3.1"), s))}
                              for p, s in platforms.items()}}

    def test_vpn_takes_only_lines_the_platform_does_not_mark_no(self):
        every = ("3.1", "2.0", "1.5", "1.0")
        apps = [self.app("vpn-app", "vpn", {"ios": ("yes", "yes", "yes", "no"), "android": ("yes",) * 4,
                                            "android_tv": ("yes",) * 4, "tvos": ("yes",) * 4}, list(every)),
                # 3.1 роутер «берёт», но в разовый импорт он её не принимает (lines), а 2.0 помечает «no»
                self.app("router", "conf", {"router": ("yes", "yes", "no", "yes")}, ["2.0", "1.5", "1.0"]),
                self.app("mihomo-app", "mihomo", {"windows": ("yes",) * 4}),
                self.app("odd", "yaml", {"windows": ("yes",) * 4}),
                self.app("txt-app", "txt", {"ios": ("yes",) * 4, "android": ("yes",) * 4, "macos": ("yes",) * 4}),
                # .conf по ссылке — и телефонному приложению (AmneziaWG, WG Tunnel): QR ключа, «Скопировать» — адрес
                self.app("conf-phone", "conf", {"android": ("yes",) * 4}, list(every))]
        apps[-2]["subscription"]["platforms"] = ["ios", "android"]
        cat = self.catalog(apps)
        got = {a["id"]: a for a in cat.for_subs(["3.1"])}
        self.assertEqual(sorted(got["vpn-app"]["platforms"]), ["android", "android_tv", "tvos"], "iPhone помечает 3.1 «no»")
        self.assertEqual((got["vpn-app"]["sub"]["format"], got["vpn-app"]["sub"]["lines"]), ("vpn", list(every)))
        self.assertEqual((got["vpn-app"]["sub"]["copy"], got["vpn-app"]["sub"]["qr"]), ("url", ""), "нет полей — адрес, QR нет")
        self.assertNotIn("router", got)
        self.assertNotIn("odd", got, "незнакомый формат — не на вкладке")
        self.assertEqual(sorted(got["txt-app"]["platforms"]), ["android", "ios"], "subscription.platforms: без macOS")
        got = {a["id"]: a for a in cat.for_subs(["2.0", "3.1"])}
        self.assertEqual(sorted(got["vpn-app"]["platforms"]), ["android", "android_tv", "ios", "tvos"])
        self.assertNotIn("router", got, "conf так же: единственная общая линия 2.0 помечена «no»")
        got = {a["id"]: a for a in cat.for_subs(["1.5", "2.0"])}
        self.assertEqual(sorted(got["router"]["platforms"]), ["router"])
        self.assertEqual(sorted(got["conf-phone"]["platforms"]), ["android"])
        self.assertEqual([a["id"] for a in cat.for_subs(["1.5", "2.0"])],
                         ["mihomo-app", "txt-app", "vpn-app", "router", "conf-phone"],
                         "самообновляемое — первым (mihomo, txt), разовое — после, внутри — порядок каталога")

    def test_variants_make_one_app_with_a_format_switch(self):
        app = self.app("multi", "txt", {"router": ("yes",) * 4})
        app["subscription"] = [dict(app["subscription"], variant={"ru": "Все линии .txt", "en": "All lines .txt"}),
                               {"format": "vpn", "once": True, "lines": ["3.1", "2.0"], "copy": "key",
                                "variant": {"ru": "Ключ vpn://", "en": "vpn:// key"}},
                               {"format": "nope"}]
        got = self.catalog([app]).for_subs(["3.1"])
        self.assertEqual([a["id"] for a in got], ["multi"])
        self.assertEqual([(v["format"], v["variant"]["ru"], v["copy"]) for v in got[0]["variants"]],
                         [("txt", "Все линии .txt", "url"), ("vpn", "Ключ vpn://", "key")], "незнакомый формат — мимо")
        self.assertEqual(got[0]["sub"], got[0]["variants"][0])

    def test_sub_apps_carry_the_mirror_like_the_static_tab(self):
        # Копия сборки с GitHub на нашем сервере — и на вкладке «Подписка», тем же состоянием, что у «Статического
        # ключа»: FlClash, Clash Meta, Clash Verge Rev, Clash Party, AmneziaVPN, AmneziaWG, WG Tunnel. У сборок только из
        # магазина (incy, DefaultVPN), на iPhone и без состояния копии (прежний вызов) — поля нет
        cat = ps.Catalog(os.path.join(ROOT, "lib", "clients.json"), NullLog())
        asked = []
        apps = {a["id"]: a for a in cat.for_subs(["2.0", "3.1"], lambda app, platform: asked.append((app, platform)) or
                                                 {"state": "none", "app": app})}
        want = {"flclash": ("android", "windows", "macos", "linux"), "clash-meta": ("android",),
                "clash-verge-rev": ("windows", "macos", "linux"), "clash-party": ("windows", "macos", "linux"),
                "amneziavpn": ("android", "windows", "macos", "linux"), "amneziawg": ("android", "windows"),
                "wgtunnel": ("android",)}
        for app_id, plats in want.items():
            got = sorted(p for p, item in apps[app_id]["platforms"].items() if "mirror" in item)
            self.assertEqual(got, sorted(plats), app_id)
            self.assertEqual(apps[app_id]["platforms"][plats[0]]["mirror"], {"state": "none", "app": app_id})
        for app_id in ("incy", "defaultvpn", "awgm", "keenetic"):
            self.assertFalse([p for p, item in apps[app_id]["platforms"].items() if "mirror" in item], app_id)
        self.assertNotIn("mirror", apps["amneziavpn"]["platforms"]["ios"])
        self.assertEqual(sorted(set(a for a, _p in asked)), sorted(want))
        self.assertFalse([a for a in cat.for_subs(["2.0", "3.1"]) for item in a["platforms"].values() if "mirror" in item])

    def test_real_catalog_has_amneziavpn_and_defaultvpn_by_link(self):
        cat = ps.Catalog(os.path.join(ROOT, "lib", "clients.json"), NullLog())
        apps = {a["id"]: a for a in cat.for_subs(["2.0", "3.1"])}
        self.assertEqual(sorted(apps["amneziavpn"]["platforms"]), ["android", "ios", "linux", "macos", "windows"])
        self.assertEqual(sorted(apps["defaultvpn"]["platforms"]), ["ios"])
        for app_id in ("amneziavpn", "defaultvpn"):
            self.assertEqual(apps[app_id]["sub"]["format"], "vpn")
            self.assertTrue(apps[app_id]["sub"]["once"])
        self.assertEqual(sorted(apps["incy"]["platforms"]), ["android", "android_tv", "ios", "macos", "tvos"],
                         "incy — на всех своих платформах (решение владельца 04.10.2026)")
        self.assertEqual(ps.sub_recommend_for(list(apps.values()))["ios"], "incy")
        # AmneziaWG и WG Tunnel — .conf по ссылке и QR самого ключа; Clash Meta — без QR (сканера нет)
        self.assertEqual((apps["amneziawg"]["sub"]["qr"], apps["wgtunnel"]["sub"]["qr"]), ("key", "key"))
        self.assertEqual(apps["wgtunnel"]["sub"]["deeplink"], "wg://{host_path}")
        self.assertEqual([apps[a]["sub"]["qr"] for a in ("flclash", "clash-meta", "clash-verge-rev", "clash-party")],
                         ["url", "", "", ""])
        self.assertEqual((apps["keenetic"]["sub"]["recommended"], apps["flclash"]["sub"]["recommended"],
                          apps["incy"]["sub"]["recommended"]), (["router"], ["android"], ["ios", "android_tv", "tvos"]))
        # «куда вставить» есть у каждого клиента вкладки, на двух языках
        for app in apps.values():
            for v in app.get("variants") or [app["sub"]]:
                self.assertTrue(v["paste"].get("ru") and v["paste"].get("en"), (app["id"], v["format"]))


class SubHelpersTests(unittest.TestCase):
    def test_fetch_of_rotated_address_does_not_touch_new_counters(self):
        """A3-2: забор прежнего адреса, закончившийся после перевыпуска (ядро уже обнулило счёт), state.json не пишет."""
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        old, new = "A" * 43, "B" * 43
        with open(os.path.join(d, "secret"), "w") as fh:
            fh.write(old + "\n")
        sub = ps.Sub(d, {"user": "anna", "generation": 1}, old)

        class _Null(object):
            def write(self, *a):
                pass

            def warn(self, *a):
                pass
        sub.fetched("all.txt", "incy", _Null(), _Null())
        with open(os.path.join(d, "state.json")) as fh:
            self.assertEqual(json.load(fh)["auto_fetches"], 1)
        with open(os.path.join(d, "secret"), "w") as fh:
            fh.write(new + "\n")
        with open(os.path.join(d, "state.json"), "w") as fh:
            json.dump({"fetches": 0, "auto_fetches": 0}, fh)
        sub.fetched("all.txt", "incy", _Null(), _Null())
        with open(os.path.join(d, "state.json")) as fh:
            self.assertEqual(json.load(fh), {"fetches": 0, "auto_fetches": 0})
        # Н-5: перевыпуск под замком заборов (state.lock, как _ga_subs_with_state_lock ядра) — забор ждёт его и
        # не пишет счёт прежнего адреса поверх обнулённого, даже если начался до смены секрета.
        import fcntl
        import threading
        sub2 = ps.Sub(d, {"user": "anna", "generation": 2}, new)
        fd = os.open(os.path.join(d, "state.lock"), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        t = threading.Thread(target=sub2.fetched, args=("all.yaml", "flclash", _Null(), _Null()))
        t.start()
        t.join(0.3)
        self.assertTrue(t.is_alive(), "забор не ждал замка перевыпуска")
        with open(os.path.join(d, "secret"), "w") as fh:
            fh.write(old + "\n")
        with open(os.path.join(d, "state.json"), "w") as fh:
            json.dump({"fetches": 0, "auto_fetches": 0}, fh)
        os.close(fd)
        t.join(5)
        self.assertFalse(t.is_alive())
        with open(os.path.join(d, "state.json")) as fh:
            self.assertEqual(json.load(fh), {"fetches": 0, "auto_fetches": 0})
        # П-5-2: state.lock, оставшийся 0644 (создан ядром до П-5), при взятии замка снова 0600.
        os.chmod(os.path.join(d, "state.lock"), 0o644)
        sub2.fetched("all.yaml", "flclash", _Null(), _Null())
        self.assertEqual(os.stat(os.path.join(d, "state.lock")).st_mode & 0o777, 0o600)

    def test_suffix_whitelist_matches_nginx(self):
        """Белый список службы — тот же, что в локации nginx (lib/website.sh, _ga_site_subs_block)."""
        with open(os.path.join(ROOT, "lib", "website.sh"), encoding="utf-8") as fh:
            src = fh.read()
        m = re.search(r'location ~ "\^/s-\[A-Za-z0-9_-\]\{43\}/(.+?)\$" \{', src)
        self.assertIsNotNone(m)
        rx = re.compile("^" + m.group(1) + "$")
        for suffix in ps.SUB_SUFFIXES:
            self.assertTrue(rx.match(suffix), suffix)
        for bad in ("awg2.conf", "awg30.conf", "mihomo.yml", "all.conf", "awgv20.conf", "awgv31.vpn", "incy.txt", ""):
            self.assertFalse(rx.match(bad), bad)
        self.assertTrue(rx.match("all.txt"), "подписка incy проходит nginx")
        self.assertEqual(ps.SUB_SUFFIXES["all.txt"], ("txt", ""))
        # all.yaml — новое имя профиля mihomo, mihomo.yaml — прежнее (уже выдано): оба проходят и дают одно и то же
        self.assertTrue(rx.match("all.yaml") and rx.match("mihomo.yaml"))
        self.assertEqual(ps.SUB_SUFFIXES["all.yaml"], ps.SUB_SUFFIXES["mihomo.yaml"])
        self.assertEqual(len(ps.SUB_SUFFIXES), 18)

    def test_client_family_and_mask(self):
        for agent, family in (("clash-verge/v2.4.2", "clash-verge"), ("FlClash/v0.8.80 clash-verge", "clash-verge"),
                              ("FlClash/v0.8.80", "flclash"), ("mihomo.party/v1.8", "mihomo-party"),
                              ("ClashMetaForAndroid/2.11", "clash-meta"), ("mihomo/1.19.32", "mihomo"),
                              ("INCY/2.1 (iPhone)", "incy"), ("v2rayTun/5.24", "v2raytun"), ("curl/8.5.0", "curl"),
                              ("Wget/1.21", "wget"), ("Mozilla/5.0 (iPhone)", "browser"), ("", ""), ("x", "other")):
            self.assertEqual(ps.client_family(agent), family, agent)
        self.assertEqual(ps.mask_path("GET /s-%s/mihomo.yaml" % ("A" * 43)), "GET /s-***/mihomo.yaml")
        self.assertEqual(ps.mask_path("users-delete s-abc"), "users-delete s-abc")

    def test_disposition_and_userinfo(self):
        self.assertEqual(ps.sub_disposition("anna", 'Ан/на "x"'),
                         "attachment; filename=\"anna.yaml\"; filename*=UTF-8''%D0%90%D0%BD%D0%BD%D0%B0%20x.yaml")
        self.assertEqual(ps.sub_disposition("bob", "\x07"), "attachment; filename=\"bob.yaml\"; filename*=UTF-8''bob.yaml")
        self.assertEqual(ps.sub_userinfo({"traffic": {"rx": 5, "tx": 7}, "quota_bytes": 0, "expires": ""}),
                         "upload=5; download=7; total=0; expire=0")


if __name__ == "__main__":
    unittest.main()
