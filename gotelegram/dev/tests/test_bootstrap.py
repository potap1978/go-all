"""bootstrap.sh: загрузка файлов на локальном стенде (без сети и без root).

Стенд подменяет в копии bootstrap.sh только адреса GitHub, каталог установки,
проверку root, ссылку в /usr/local/bin, установку curl/jq (стенд пакеты не
ставит) и окно застоя (2 с вместо 30 — механизм тот же). Проверяет то, что ломалось вживую 29.10.2026: молчаливое зависание на
каталоге, обрезанные и смешанные файлы после сбоя, токен в `ps`.
Нужны bash 4+ и curl; без них тест пропускается.
"""
import gzip
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "bootstrap.sh"
TOKEN = "stand-token-not-real-7f3a9c41"
SHA = "0123456789abcdef0123456789abcdef01234567"


def _bash():
    for cand in (shutil.which("bash"), "/opt/homebrew/bin/bash", "/usr/local/bin/bash", "/bin/bash"):
        if not cand or not os.path.exists(cand):
            continue
        out = subprocess.run([cand, "-c", "echo ${BASH_VERSINFO[0]}"], capture_output=True, text=True).stdout
        if out.strip().isdigit() and int(out) >= 4:
            return cand
    return None


BASH = _bash()


class Stand:
    """HTTP-стенд: /api/... — ответ API с SHA, /raw/<o>/<r>/<ref>/<path> — файл."""

    def __init__(self, root):
        self.root, self.modes, self.log, self.count = root, {}, [], {}
        self.gz_cache = {}  # сжатие 13 МБ — один раз на файл, а не на каждый запрос
        self.lock = threading.Lock()
        stand = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _send(self, code, body=b"", hdr=None):
                self.send_response(code)
                for k, v in (hdr or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                rel = self.path.split("/", 5)[5] if self.path.startswith("/raw/") else ""
                with stand.lock:
                    stand.count[rel] = n = stand.count.get(rel, 0) + 1
                    stand.log.append(dict(path=self.path, rel=rel,
                                          auth=self.headers.get("Authorization", ""),
                                          gzip="gzip" in self.headers.get("Accept-Encoding", "")))
                if self.path.startswith("/api/"):
                    return self._send(200, json.dumps({"sha": SHA}).encode())
                mode = stand.modes.get(rel, "ok")
                if mode.startswith("flaky:"):
                    mode = "stall" if n <= int(mode[6:]) else "ok"
                if mode == "e404":
                    return self._send(404, b"404: Not Found")
                if mode == "e500":
                    return self._send(500, b"err")
                data = (Path(stand.root) / rel).read_bytes()
                if mode == "empty":
                    return self._send(200, b"")
                gz = stand.log[-1]["gzip"]
                if gz:
                    key = (str(stand.root), rel)
                    if key not in stand.gz_cache:
                        stand.gz_cache[key] = gzip.compress(data, 6)
                    body = stand.gz_cache[key]
                else:
                    body = data
                hdr = {"Content-Encoding": "gzip"} if gz else {}
                if mode == "ok":
                    return self._send(200, body, hdr)
                # stall / slow: заголовки сразу, тело — 1000 байт и тишина / по 50 КБ/с
                self.send_response(200)
                for k, v in hdr.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    if mode == "stall":
                        self.wfile.write(body[:1000]); self.wfile.flush(); time.sleep(60)
                    else:
                        for i in range(0, len(body), 5120):
                            self.wfile.write(body[i:i + 5120]); self.wfile.flush(); time.sleep(0.1)
                except OSError:
                    pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.srv.daemon_threads = True
        self.srv.handle_error = lambda *a: None  # curl сам рвёт заглохшие соединения — это норма
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def reset(self, root=None, modes=None):
        with self.lock:
            self.root = root or self.root
            self.modes, self.log, self.count = modes or {}, [], {}

    def hits(self, rel):
        return sum(1 for r in self.log if r["rel"] == rel)


@unittest.skipUnless(BASH and shutil.which("curl"), "нужны bash 4+ и curl")
class BootstrapDownloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="gt-bootstrap-test-"))
        src = BOOTSTRAP.read_text(encoding="utf-8")
        cls.files = re.findall(r'"([^"]+)"', re.search(r"FILES=\(\n(.*?)\n\)", src, re.S).group(1))
        cls.repos = {v: cls._make_repo(v) for v in ("v1", "v2")}
        cls.stand = Stand(cls.repos["v1"])
        cls.inst = cls.tmp / "opt" / "gotelegram"
        (cls.tmp / "bin").mkdir()
        subs = [
            ('INSTALL_DIR="/opt/gotelegram"', f'INSTALL_DIR="{cls.inst}"'),
            ("https://raw.githubusercontent.com", f"http://127.0.0.1:{cls.stand.port}/raw"),
            ("https://api.github.com", f"http://127.0.0.1:{cls.stand.port}/api"),
            ('[ "$(id -u)" -ne 0 ]', "[ 0 -ne 0 ]"),
            ("/usr/local/bin/gotelegram", str(cls.tmp / "bin" / "gotelegram")),
            ("--speed-time 30", "--speed-time 2"),
            ("for cmd in curl jq; do", "for cmd in; do"),
        ]
        for a, b in subs:
            assert a in src, f"в bootstrap.sh нет «{a}» — обнови стенд"
            src = src.replace(a, b)
        cls.script = cls.tmp / "bootstrap.stand.sh"
        cls.script.write_text(src, encoding="utf-8")
        # ~/.curlrc root с verbose вывел бы заголовок Authorization — bootstrap.sh
        # обязан его игнорировать (curl -q). CURL_HOME curl смотрит первым.
        curl_home = cls.tmp / "curlhome"
        curl_home.mkdir()
        (curl_home / ".curlrc").write_text("verbose\n")
        cls.env = dict(os.environ, GOTELEGRAM_PAT=TOKEN, GOTELEGRAM_BRANCH="dev",
                       CURL_HOME=str(curl_home))
        # mv, который отказывает на файле из GT_MV_FAIL: сбой при перестановке
        cls.mvbin = cls.tmp / "mvbin"
        cls.mvbin.mkdir()
        real_mv = shutil.which("mv")
        (cls.mvbin / "mv").write_text(
            '#!/bin/sh\nfor a; do last="$a"; done\n'
            'case "$last" in *"/$GT_MV_FAIL") exit 1 ;; esac\n'
            f'exec {real_mv} "$@"\n')
        (cls.mvbin / "mv").chmod(0o755)

    @classmethod
    def tearDownClass(cls):
        cls.stand.srv.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def _make_repo(cls, ver):
        root = cls.tmp / f"repo_{ver}"
        for f in cls.files:
            p = root / f
            p.parent.mkdir(parents=True, exist_ok=True)
            if f == "templates_catalog.json":
                p.write_bytes((ROOT / f).read_bytes() + (b"\n" if ver == "v2" else b""))
            elif f == "install.sh":
                p.write_text(f'#!/bin/bash\necho "INSTALL_SH_STARTED {ver}"\n')
            else:
                p.write_text(f"# {f} {ver}\n" + "x" * 200 + "\n")
        return root

    def _run(self, root, modes=None, mv_fail=None):
        self.stand.reset(root, modes)
        env = dict(self.env)
        if mv_fail:
            env.update(GT_MV_FAIL=mv_fail, PATH=f"{self.mvbin}:{env['PATH']}")
        p = subprocess.run([BASH, str(self.script)], env=env, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=300)
        self.assertNotIn(TOKEN, p.stdout + p.stderr, "токен попал в вывод")
        return p

    def _snapshot(self):
        return {f: hashlib.sha256((self.inst / f).read_bytes()).hexdigest() for f in self.files}

    def _parts(self):
        return [str(p) for p in self.inst.rglob("*.part")]

    def _same_as(self, ver):
        return all((self.inst / f).read_bytes() == (self.repos[ver] / f).read_bytes() for f in self.files)

    def test_all(self):
        # Сценарии зависят друг от друга (установка → обновления), поэтому один тест по шагам.
        with self.subTest("первая установка не удалась: честное сообщение"):
            p = self._run(self.repos["v1"], {"lib/stats.sh": "e404"})
            self.assertEqual(p.returncode, 1)
            self.assertIn("Загрузка прервана, ничего не установлено", p.stderr)
            self.assertNotIn("установленные файлы не изменены", p.stderr)
            self.assertIn("Проверьте токен", p.stderr)
            self.assertFalse((self.inst / "install.sh").exists())
            self.assertEqual(self._parts(), [])

        with self.subTest("установка с нуля, без терминала"):
            p = self._run(self.repos["v1"])
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("INSTALL_SH_STARTED v1", p.stdout)
            self.assertTrue(self._same_as("v1"))
            self.assertTrue(all(r["auth"] == f"token {TOKEN}" for r in self.stand.log))
            self.assertTrue(all(r["path"].startswith(f"/raw/anten-ka/gotelegram_pro/{SHA}/")
                                for r in self.stand.log if r["rel"]))
            cat = [r for r in self.stand.log if r["rel"] == "templates_catalog.json"]
            self.assertTrue(cat[0]["gzip"], "каталог должен идти с --compressed")
            self.assertIn("каталог шаблонов сайтов, файл большой", p.stdout)
            self.assertEqual(p.stderr, "")
            self.assertNotIn("\r", p.stdout)
            self.assertNotIn("%", p.stdout)
            self.assertEqual(self._parts(), [])
        v1 = self._snapshot()

        with self.subTest("застой каталога: три попытки, старые файлы целы"):
            t0 = time.time()
            p = self._run(self.repos["v2"], {"templates_catalog.json": "stall"})
            self.assertEqual(p.returncode, 1)
            self.assertEqual(self.stand.hits("templates_catalog.json"), 3)
            self.assertLess(time.time() - t0, 60)
            self.assertEqual(p.stderr.count("повтор через"), 2)
            self.assertIn("Ошибка загрузки templates_catalog.json (таймаут", p.stderr)
            self.assertIn("установленные файлы не изменены", p.stderr)
            self.assertEqual(self._snapshot(), v1)  # и install.sh, скачанный до сбоя
            self.assertEqual(self._parts(), [])

        with self.subTest("404 в середине: без повтора, дальше не качаем"):
            p = self._run(self.repos["v2"], {"lib/stats.sh": "e404"})
            self.assertEqual(p.returncode, 1)
            self.assertEqual(self.stand.hits("lib/stats.sh"), 1)
            self.assertEqual(self.stand.hits("lib/shared443.sh"), 0)
            self.assertIn("Ошибка загрузки lib/stats.sh (HTTP 404)", p.stderr)
            self.assertEqual(self._snapshot(), v1)
            self.assertEqual(self._parts(), [])

        with self.subTest("пустой ответ 200 не принимаем"):
            p = self._run(self.repos["v2"], {"lib/i18n.sh": "empty"})
            self.assertEqual(p.returncode, 1)
            self.assertIn("пустой ответ", p.stderr)
            self.assertEqual(self._snapshot(), v1)

        with self.subTest("ошибка записи (curl 23): без подсказки про токен"):
            blocker = self.inst / "lib" / "users.sh.part"
            blocker.mkdir()  # curl не сможет открыть .part на запись
            try:
                p = self._run(self.repos["v2"])
            finally:
                blocker.rmdir()
            self.assertEqual(p.returncode, 1)
            self.assertIn("Ошибка загрузки lib/users.sh (не удалось записать файл", p.stderr)
            self.assertNotIn("Проверьте токен", p.stderr)
            self.assertIn("установленные файлы не изменены", p.stderr)
            self.assertEqual(self._snapshot(), v1)
            self.assertEqual(self._parts(), [])

        with self.subTest("mv отказал на первом файле: ничего не обновлено"):
            p = self._run(self.repos["v2"], mv_fail="install.sh")
            self.assertEqual(p.returncode, 1)
            self.assertIn("Не удалось поставить на место install.sh, установленные файлы не изменены", p.stderr)
            self.assertNotIn("часть файлов", p.stderr)
            self.assertEqual(self._snapshot(), v1)
            self.assertEqual(self._parts(), [])

        with self.subTest("обновление: первая попытка заглохла, вторая прошла"):
            p = self._run(self.repos["v2"], {"templates_catalog.json": "flaky:1"})
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual(p.stderr.count("повтор через"), 1)
            self.assertTrue(self._same_as("v2"))
            self.assertEqual(self._parts(), [])

        with self.subTest("mv отказал в середине: предупреждение, что часть уже новая"):
            p = self._run(self.repos["v1"], mv_fail="lib/stats.sh")
            self.assertEqual(p.returncode, 1)
            self.assertIn("часть файлов уже обновлена — запустите установку ещё раз", p.stderr)
            self.assertEqual((self.inst / "install.sh").read_bytes(),
                             (self.repos["v1"] / "install.sh").read_bytes())
            self.assertEqual(self._parts(), [])
        before = self._snapshot()

        with self.subTest("во время загрузки токена нет в ps; Ctrl+C чистит .part"):
            self.stand.reset(self.repos["v1"], {"templates_catalog.json": "slow"})
            pr = subprocess.Popen([BASH, str(self.script)], env=self.env, stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
            line, leak = None, False
            try:
                for _ in range(100):
                    time.sleep(0.2)
                    ps = subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True).stdout
                    leak = leak or TOKEN in ps
                    mine = [l for l in ps.splitlines()
                            if "curl" in l and str(self.inst) in l and "templates_catalog.json" in l]
                    # curl виден в ps раньше, чем создаст .part (на Linux — всегда)
                    if mine and (self.inst / "templates_catalog.json.part").exists():
                        line = mine[0]
                        break
                self.assertIsNotNone(line, "curl с каталогом не пойман в ps")
                self.assertFalse(leak, "токен виден в ps")
                self.assertIn("-H @/dev/fd/", line)
                self.assertIn("--max-time 1800", line)
                self.assertTrue(line.split("curl", 1)[1].startswith(" -q "), "curl -q — первым")
            finally:
                os.killpg(pr.pid, signal.SIGINT)  # как Ctrl+C: вся группа процессов
                out, err = pr.communicate(timeout=30)
            self.assertEqual(pr.returncode, 130)
            self.assertEqual(err.decode().count("Прервано, установленные файлы не изменены"), 1, err.decode())
            self.assertNotIn(TOKEN, out.decode() + err.decode())
            self.assertEqual(self._parts(), [])
            self.assertEqual(self._snapshot(), before)


if __name__ == "__main__":
    unittest.main()
