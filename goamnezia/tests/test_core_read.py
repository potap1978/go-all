# -*- coding: utf-8 -*-
"""Частые чтения ядра внутри панели (PERF-01, часть C): lib/core_read.py против настоящего моста.

* Равенство: на стенде dry-run (два профиля, шесть человек — выключенный, из приложения, со своим адресом сервера,
  с пропавшим адресом, с подпиской и без, страница подключения, раздача, короткие ссылки) user-list, user-show,
  user-key (все форматы), share-list, sub-list, sub-show и page-list отвечают JSON, равным ответу
  `install.sh --action=… --json` поле в поле и в том же порядке, на русском и английском, в Lite, Pro, с
  вписанным доменом и IP, с DNS и MTU из конфига, с линией без поля line. Отказы (нет человека, нет ключа,
  нет подписки, подпись с !*'() в адресе Clash) модуль отдаёт мосту — None.
* Края (контроль 05.10, CR-4): истёкший человек, неактивный пир, awg1 с линиями 1.5 и 1.0, перевыпущенная и
  отозванная подписка, короткие ссылки без бота, истёкшая страница и срок строкой, истёкшая раздача — занятая и
  снятая уборщиком, язык из config.lang и config.language, lite_site с пустым site.cert, самоподписанный сайт,
  профиль из приложения без line, нет server_ip, DNS строкой, LANG=en_US.UTF-8, интервал подписки с ведущим нулём.
* Аудит: строка выдачи ключа и показа адресов подписки — та же, что пишет ядро (кроме времени).
* Замок: чтение во время записи ядра (LOCK_EX на config.lock, users.json и config.json правятся по очереди) ждёт её
  конца и видит либо всё до, либо всё после; висящая запись не держит чтение дольше lock_wait (0–5 с); ip и
  qrencode — без замка; весь ответ — не дольше срока.
* Секреты: в списке и карточке нет privkey и psk; ответы user-key и sub-show не ложатся в общий снимок панели;
  сбой быстрого пути пишет в журнал только вид ошибки.
* Открытие карточки в панели (карточка, ключ, страницы, подписка) не запускает мост ни разу — на прежнем коде
  четыре запуска (тест красный до PERF-01); GOAMNEZIA_CORE_READ=0, нет модуля, битый модуль — мостом. Прежний
  снимок рукопожатий — не старше 45 с (активный человек не гаснет), GOAMNEZIA_READ_SWR=0 — свежий сбор.
* Отпечаток зеркала: места ядра, которые повторяет модуль, не менялись после сверки; правка кода в каждом месте
  выключает быстрый путь ровно его действий, правка комментария — нет; несовпадение отпечатка — None.

Нужны bash ≥ 4 (BASH5 на macOS), jq, python3; qrencode — для форматов с QR (без него эти случаи пропускаются).
Запуск: BASH5=/opt/homebrew/bin/bash python3 -m unittest tests/test_core_read.py
"""
import ast
import fcntl
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
JQ = shutil.which("jq")
QRENCODE = shutil.which("qrencode")


def _bash5():
    for cand in (os.environ.get("BASH5"), shutil.which("bash"), "/opt/homebrew/bin/bash", "/usr/local/bin/bash",
                 "/bin/bash"):
        if not cand or not os.path.exists(cand):
            continue
        try:
            v = subprocess.run([cand, "-c", "echo ${BASH_VERSINFO[0]}"], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            continue
        if v.stdout.strip().isdigit() and int(v.stdout.strip()) >= 4:
            return cand
    return None


BASH5 = _bash5()
CAN_RUN = bool(BASH5 and JQ)
SKIP_WHY = "нужны bash ≥ 4 (BASH5) и jq"

_spec = importlib.util.spec_from_file_location("ga_core_read_test", ROOT / "lib" / "core_read.py")
core_read = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(core_read)  # type: ignore[union-attr]

# Стенд: настоящие модули ядра в dry-run, как tests/test_subs_dryrun.sh. Пишется только во временный каталог.
STAND_SH = r'''
set -uo pipefail
ROOT="$1"; T="$2"
source "$ROOT/lib/common.sh"
source "$ROOT/lib/i18n.sh"
load_language ru >/dev/null 2>&1
for _m in docker amnezia users export edgeips website preflight share pages subs mirror backup; do
    source "$ROOT/lib/$_m.sh"
done
mkdir -p "$UPSTREAM_DIR/awg" "$GOAMNEZIA_LOCK_DIR" "$GOAMNEZIA_SECRET_TMPDIR" "$STATE_DIR"
cp "$ROOT"/lib/upstream/awg/* "$UPSTREAM_DIR/awg/" 2>/dev/null
cp "$ROOT/lib/upstream/images.json" "$UPSTREAM_DIR/images.json" 2>/dev/null
GA_VER=$(sed -n 's/^GOAMNEZIA_VERSION="\([^"]*\)".*/\1/p' "$ROOT/lib/common.sh" | head -1)
cat >"$CONFIG_FILE" <<EOC
{"schema": 1, "version": "$GA_VER", "installed": true, "mode": "lite", "lang": "ru", "domain": "",
 "server_ip": "203.0.113.10", "endpoint_host": "", "client_dns": ["1.1.1.1", "1.0.0.1"],
 "profiles": {}, "panel": {"port": 1985, "miniapp_path": ""}, "site": {"template_id": "", "template_source": "", "cert": ""},
 "options": {"drop_icmp": false, "alt_svc": false}, "mcp": {"http_enabled": false, "http_port": 1986}}
EOC
chmod 600 "$CONFIG_FILE"
printf '{"schema": 1, "users": []}\n' >"$USERS_FILE"; chmod 600 "$USERS_FILE"
chmod 700 "$STATE_DIR"
fail() { echo "STAND FAIL: $*" >&2; exit 1; }
amnezia_profile_install awg2 40000 default >/dev/null 2>&1 || fail awg2
amnezia_profile_install awg3 40001 default >/dev/null 2>&1 || fail awg3
users_add anna "Анна" awg3,awg2 >/dev/null 2>&1 || fail anna
users_add bob "Bob (home)" awg2 5 10 >/dev/null 2>&1 || fail bob
users_add carol "Кэрол" awg2 >/dev/null 2>&1 || fail carol
users_add dave "Dave" awg2 >/dev/null 2>&1 || fail dave
users_add eve "Eve" awg3 >/dev/null 2>&1 || fail eve
users_add frank "Frank" awg2 >/dev/null 2>&1 || fail frank
users_set_endpoint_ip eve 198.51.100.7 >/dev/null 2>&1 || fail eve-ip
jq '.users |= map(if .name == "dave" then .peers.awg2.privkey = "" | .imported = true
                  elif .name == "frank" then .endpoint_ip = "192.0.2.99" else . end)' "$USERS_FILE" >"$T/u.json" \
    && mv -f "$T/u.json" "$USERS_FILE" && chmod 600 "$USERS_FILE"
ga_config_merge '{"mode": "pro", "domain": "example.com", "site": {"cert": "letsencrypt"}}' >/dev/null 2>&1
subs_create anna >/dev/null 2>&1 || fail sub-anna
subs_create bob >/dev/null 2>&1 || fail sub-bob
# Н-1: перевыпуск после заборов — prev_fetches и prev_last_fetch в строке подписки
printf '%s\n' '{"fetches": 3, "last_fetch": "2026-10-01T10:00:00Z", "last_suffix": "all.yaml"}' >"$(ga_subs_dir)/bob/state.json"
subs_rotate bob >/dev/null 2>&1 || fail sub-bob-rotate
subs_create carol >/dev/null 2>&1 || fail sub-carol
users_disable carol >/dev/null 2>&1 || fail carol-off
pages_create anna 24h "" "" >/dev/null 2>&1 || fail page-anna
pages_create anna forever "" "" >/dev/null 2>&1 || fail page-anna-2
pages_create bob 2d "" "" >/dev/null 2>&1 || fail page-bob
echo ready
'''


class Stand(object):
    """Стенд в каталоге tmp: окружение моста, вызовы моста и быстрого пути."""

    def __init__(self, tmp):
        self.tmp = Path(tmp)
        t = self.tmp
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith("GOAMNEZIA_") and k not in ("BASH_ENV", "ENV", "BOT_DIR", "ADMIN_DIR",
                                                                    "CONFIG_FILE", "USERS_FILE", "STATE_DIR")}
        self.env.update({
            "GOAMNEZIA_DRY_RUN": "1", "GOAMNEZIA_DIR": str(t / "goamnezia"), "GOAMNEZIA_LOCK_DIR": str(t / "run"),
            "GOAMNEZIA_LOG_FILE": str(t / "goamnezia.log"), "GOAMNEZIA_AUDIT_LOG": str(t / "audit.log"),
            "GOAMNEZIA_NOTIFY_DIR": str(t / "run" / "notify"), "GOAMNEZIA_BUILD_ROOT": str(t / "build"),
            "GOAMNEZIA_SERVER_IP": "203.0.113.10", "GOAMNEZIA_NGINX_DIR": str(t / "nginx"),
            "GOAMNEZIA_SITE_ROOT": str(t / "site"), "GOAMNEZIA_ACME_ROOT": str(t / "acme"),
            "GOAMNEZIA_LE_DIR": str(t / "letsencrypt"), "GOAMNEZIA_SYSTEMD_DIR": str(t / "systemd"),
            "GOAMNEZIA_TLS_DIR": str(t / "tls"), "GOAMNEZIA_MIRROR_DIR": str(t / "mirror"),
            "BOT_DIR": str(t / "bot"), "GOAMNEZIA_BOT_DIR": str(t / "bot"), "ADMIN_DIR": str(t / "admin"),
            "GOAMNEZIA_SHORT_CONFIRM_DIR": str(t / "run" / "short-confirm"), "UPSTREAM_DIR": str(t / "upstream"),
            "GOAMNEZIA_SECRET_TMPDIR": str(t / "shm"), "GOAMNEZIA_DOCKER_SOCK": str(t / "nosuch.sock"),
            "GOAMNEZIA_SHARE_BIND": "127.0.0.1", "GOAMNEZIA_EDGE_IPS_STUB": "203.0.113.10 198.51.100.7",
            "GOAMNEZIA_WHO": "panel:tunnel", "GOAMNEZIA_CALLER": "panel", "TMPDIR": str(t / "tmp"),
            "LC_ALL": "C", "LANG": "C",
        })
        for d in ("goamnezia", "run", "shm", "tmp", "bot", "admin", "mirror"):
            (t / d).mkdir(parents=True, exist_ok=True)
        self.gdir = t / "goamnezia"
        self.config = self.gdir / "config.json"
        self.users = self.gdir / "users.json"
        self.audit = t / "audit.log"
        self.core = core_read.CoreRead(str(ROOT), environ=self.env)
        self.bridge_calls = 0

    def build(self):
        p = subprocess.run([BASH5, "-c", STAND_SH, "stand", str(ROOT), str(self.tmp)], env=self.env,
                           capture_output=True, text=True, timeout=600)
        if "ready" not in p.stdout:
            raise RuntimeError("стенд не собрался: %s %s" % (p.stdout[-500:], p.stderr[-2000:]))

    def bridge(self, action, params=None, lang="", env=None):
        argv = [BASH5, str(ROOT / "install.sh"), "--action=%s" % action, "--json"]
        for k, v in (params or {}).items():
            argv.append("--%s=%s" % (k.replace("_", "-"), v))
        env = dict(self.env if env is None else env)
        if lang:
            env["GOAMNEZIA_CALL_LANG"] = lang
        self.bridge_calls += 1
        p = subprocess.run(argv, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)
        lines = [l for l in p.stdout.splitlines() if l.strip()]
        if not lines:
            raise AssertionError("мост %s молчит: %s" % (action, p.stderr[-800:]))
        return json.loads(lines[-1])

    def fast(self, action, params=None, lang=""):
        return self.core.answer(action, dict(params or {}), lang, "panel:tunnel")

    def write_json(self, path, obj):
        tmp = str(path) + ".t"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, sort_keys=True)
        os.replace(tmp, str(path))

    def config_merge(self, patch):
        cfg = json.loads(self.config.read_text(encoding="utf-8"))

        def merge(a, b):
            for k, v in b.items():
                if isinstance(v, dict) and isinstance(a.get(k), dict):
                    merge(a[k], v)
                else:
                    a[k] = v
        merge(cfg, patch)
        self.write_json(self.config, cfg)


STAND = None
_TMP = None


def setUpModule():
    global STAND, _TMP
    if not CAN_RUN:
        return
    _TMP = tempfile.mkdtemp(prefix="ga-core-read-")
    STAND = Stand(_TMP)
    STAND.build()
    core_read.RECORDED.update(STAND.core.fingerprints())     # сверка самого отпечатка — в MirrorTests
    # Раздача (lib/share.sh): meta, state и pid живого процесса — этого теста; одна раздача истекла бы позже.
    share = Path(STAND.env["GOAMNEZIA_LOCK_DIR"]) / "share" / "s-1a2b"
    share.mkdir(parents=True)
    now = int(time.time())
    (share / "meta.json").write_text(json.dumps({
        "id": "s-1a2b", "name": "anna", "profile": "awg2", "format": "conf", "file_name": "anna-awg2.conf",
        "port": 31234, "bind": "127.0.0.1", "created": "2026-10-05T10:00:00Z", "expires": "2026-10-05T11:00:00Z",
        "expires_epoch": now + 3000, "ttl": 3600, "max_downloads": 0, "cert": "/x/cert.pem", "key": "/x/key.pem",
        "cert_kind": "selfsigned", "fingerprint": "AB:CD", "host": "203.0.113.10", "via": "ip", "fw": "none",
        "size": 512, "endpoint": ""}), encoding="utf-8")
    (share / "state.json").write_text(json.dumps({"downloads": 2}), encoding="utf-8")
    (share / "pid").write_text(str(os.getpid()), encoding="utf-8")
    # Бот настроен — короткие ссылки доступны; у anna уже есть код all.yaml.
    (Path(STAND.env["BOT_DIR"]) / ".env").write_text("BOT_TOKEN=123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\n"
                                                     "ALLOWED_IDS=1001\n", encoding="utf-8")
    with mock.patch.dict(os.environ, STAND.env, clear=True):
        sl_spec = importlib.util.spec_from_file_location("ga_shortlinks_test", ROOT / "lib" / "shortlinks.py")
        sl = importlib.util.module_from_spec(sl_spec)
        sl_spec.loader.exec_module(sl)  # type: ignore[union-attr]
        sl.ShortLinks().ensure("anna", "all.yaml", who="panel")


def tearDownModule():
    if _TMP:
        shutil.rmtree(_TMP, ignore_errors=True)


def dumps(obj):
    return json.dumps(obj, ensure_ascii=False)


@unittest.skipUnless(CAN_RUN, SKIP_WHY)
class ParityTests(unittest.TestCase):
    """Ответ быстрого пути — байт в байт ответ моста (после разбора JSON), или None — и тогда это отказ моста."""

    def check(self, action, params=None, lang="", fallback=False, qr=False):
        if qr and not QRENCODE:
            self.skipTest("нет qrencode")
        b = STAND.bridge(action, params, lang)
        py = STAND.fast(action, params, lang)
        what = "%s %s %s" % (action, dumps(params or {}), lang or "-")
        if fallback:
            self.assertIsNone(py, what + ": такое отвечает мост")
            return b
        self.assertIsNotNone(py, what + ": быстрый путь отдал мосту (%s)" % dumps(b)[:300])
        if action == "share-list":
            # seconds_left считают от часов — между вызовами могла пройти секунда
            for x in py.get("shares", []):
                for y in b.get("shares", []):
                    if x.get("id") == y.get("id") and abs(x["seconds_left"] - y["seconds_left"]) <= 2:
                        x["seconds_left"] = y["seconds_left"]
        self.assertEqual(dumps(b), dumps(py), what)
        return b

    def test_user_list_and_cards(self):
        b = self.check("user-list")
        self.assertEqual([u["name"] for u in b["users"]], ["anna", "bob", "carol", "dave", "eve", "frank"])
        self.check("user-list", lang="en")
        for name in ("anna", "bob", "carol", "dave"):
            self.check("user-show", {"name": name})
        b = self.check("user-show", {"name": "eve"}, lang="en")
        self.assertEqual(b["key_host"], "198.51.100.7")           # свой адрес человека, он у машины есть
        b = self.check("user-show", {"name": "frank"})
        self.assertTrue(b["key_host_gone"])                      # своего адреса у машины больше нет
        self.check("user-show", {"name": "nobody"}, fallback=True)

    def test_keys_in_every_format(self):
        b = self.check("user-key", {"name": "anna", "profile": "awg2", "format": "all"}, qr=True)
        self.assertTrue(b["conf"].startswith("[Interface]") and b["vpn"].startswith("vpn://") and b["qr_vpn_png_b64"])
        self.assertEqual(b["file_name"], "anna-2_0.conf")          # Н-2: имя файла — ga_key_file_base
        self.check("user-key", {"name": "anna", "profile": "awg3", "format": "all"}, lang="en", qr=True)
        self.check("user-key", {"name": "anna", "profile": "awg2", "format": "conf"})
        self.check("user-key", {"name": "anna", "profile": "awg3", "format": "vpn"})
        self.check("user-key", {"name": "bob", "profile": "awg2", "format": "qr-conf"}, qr=True)
        self.check("user-key", {"name": "eve", "profile": "awg3", "format": "qr-vpn"}, qr=True)
        b = self.check("user-key", {"name": "carol", "profile": "awg2", "format": "conf"})
        self.assertTrue(b["disabled"])                           # выключенному — с пометкой, как у моста
        self.check("user-key", {"name": "dave", "profile": "awg2", "format": "all"}, fallback=True)   # из приложения
        self.check("user-key", {"name": "frank", "profile": "awg2", "format": "conf"}, fallback=True)  # адрес пропал
        self.check("user-key", {"name": "anna", "profile": "awg1", "format": "conf"}, fallback=True)   # не установлен
        self.check("user-key", {"name": "anna", "profile": "awg2", "format": "conf", "endpoint": "198.51.100.7"},
                   fallback=True)                                # разовый адрес проверяет мост

    def test_subscription_pages_and_shares(self):
        b = self.check("sub-show", {"name": "anna"})
        self.assertTrue(b["short_available"])
        self.assertTrue(any(i["short_url"] for c in b["clients"] for i in c["items"]))
        self.check("sub-show", {"name": "anna"}, lang="en")
        self.check("sub-show", {"name": "bob"}, fallback=True)     # «(» в подписи: @uri у jq 1.6 и 1.7 разный
        self.check("sub-show", {"name": "carol"})                 # выключенный: disabled, адреса те же
        self.check("sub-show", {"name": "eve"}, fallback=True)     # подписки нет
        for p in ({"name": "anna"}, {"name": "carol"}, {"name": "eve"}, {}):
            self.check("sub-list", p)
        # Н-1: у bob подписку перевыпустили после трёх заборов — счёт с нуля, след прежнего адреса в строке
        b = self.check("sub-list", {"name": "bob"})
        row = b["subs"][0]
        self.assertEqual((row["fetches"], row["prev_fetches"], row["prev_last_fetch"], row["generation"]),
                         (0, 3, "2026-10-01T10:00:00Z", 2))
        row = self.check("sub-list", {"name": "anna"})["subs"][0]
        self.assertEqual((row["prev_fetches"], row["prev_last_fetch"]), (0, ""))
        b = self.check("page-list", {"name": "anna"})
        self.assertEqual(b["count"], 2)
        self.check("page-list", {})
        self.check("page-list", {"name": "eve"})
        b = self.check("share-list")
        self.assertEqual(b["count"], 1)


@unittest.skipUnless(CAN_RUN, SKIP_WHY)
class ConfigVariantTests(unittest.TestCase):
    """Те же чтения при других настройках сервера: Lite, вписанный домен и IP, DNS и MTU, линия без поля line."""

    def setUp(self):
        self.saved = STAND.config.read_bytes()
        self.saved_users = STAND.users.read_bytes()

    def tearDown(self):
        STAND.write_json(STAND.config, json.loads(self.saved))
        STAND.write_json(STAND.users, json.loads(self.saved_users))

    def both(self, action, params=None, lang=""):
        b = STAND.bridge(action, params, lang)
        py = STAND.fast(action, params, lang)
        return b, py

    def same(self, action, params=None, lang="", qr=False):
        if qr and not QRENCODE:
            return
        b, py = self.both(action, params, lang)
        self.assertIsNotNone(py, "%s %s: отдал мосту" % (action, params))
        self.assertEqual(dumps(b), dumps(py), "%s %s" % (action, params))
        return b

    def test_lite_without_domain(self):
        STAND.config_merge({"mode": "lite", "domain": "", "site": {"cert": ""}})
        b = self.same("user-show", {"name": "anna"})
        self.assertEqual(b["key_host"], "203.0.113.10")
        self.same("user-show", {"name": "eve"})
        self.same("user-key", {"name": "anna", "profile": "awg2", "format": "all"}, qr=True)
        b = self.same("sub-list", {"name": "anna"})
        self.assertFalse(b["domain_ready"])
        b, py = self.both("sub-show", {"name": "anna"})
        self.assertEqual((b["status"], b.get("code"), py), ("error", "subs_need_domain", None))

    def test_pinned_domain_and_pinned_ip_dns_and_mtu(self):
        STAND.config_merge({"endpoint_host": "vpn.example.org"})
        b = self.same("user-show", {"name": "eve"})
        self.assertEqual(b["key_host"], "vpn.example.org")        # вписанный домен побеждает свой адрес человека
        self.same("user-key", {"name": "eve", "profile": "awg3", "format": "conf"})
        cfg = json.loads(STAND.config.read_text(encoding="utf-8"))
        cfg["endpoint_host"] = "198.51.100.7"
        cfg["client_dns"] = ["8.8.8.8", "bad", "9.9.9.9\n1.2.3.4", 5]
        cfg["profiles"]["awg2"]["params"]["MTU"] = "1400"
        STAND.write_json(STAND.config, cfg)
        b = self.same("user-key", {"name": "anna", "profile": "awg2", "format": "all"}, qr=True)
        if b:
            self.assertIn("MTU = 1400", b["conf"])
            self.assertIn("8.8.8.8, 9.9.9.9, 1.2.3.4", b["conf"])
        self.same("user-show", {"name": "frank"})

    def test_selfsigned_site_and_line_from_params(self):
        STAND.config_merge({"site": {"cert": "selfsigned"}})
        b = self.same("sub-list", {})
        self.assertFalse(b["domain_ready"])
        self.same("user-show", {"name": "anna"})
        cfg = json.loads(STAND.config.read_text(encoding="utf-8"))
        cfg["site"]["cert"] = "letsencrypt"
        del cfg["profiles"]["awg3"]["line"]                     # старая установка: линия — по параметрам
        STAND.write_json(STAND.config, cfg)
        self.same("user-key", {"name": "anna", "profile": "awg3", "format": "conf"})
        self.same("sub-show", {"name": "anna"})
        cfg["site"]["cert"] = ""                                # старая установка Pro: поле пустое — Let's Encrypt
        STAND.write_json(STAND.config, cfg)
        b = self.same("user-show", {"name": "anna"})
        self.assertEqual(b["key_host"], "example.com")
        self.same("sub-list", {"name": "anna"})

    def test_update_interval_with_leading_zero(self):
        # CR-5: subs.update_interval строкой с ведущим нулём — десятичное, как у службы страниц (pages_server.py
        # update_hours): «012» — 12 часов, «08» — 8. Раньше ядро считало минуты восьмерично («012» → 600 вместо 720),
        # а на «08» арифметика bash падала, и выбор приложений в sub-show пропадал.
        for raw, hours in (("012", 12), ("08", 8), ("007", 7), (24, 24)):
            STAND.config_merge({"subs": {"update_interval": raw}})
            b = self.same("sub-list", {"name": "anna"})
            self.assertEqual(b["update_interval"], hours, raw)
            b = self.same("sub-show", {"name": "anna"})
            self.assertEqual(b["update_interval"], hours, raw)
            links = [i["deeplink"] for c in b["clients"] for i in c["items"] if "update-interval=" in i["deeplink"]]
            self.assertTrue(links, "нет приложения с интервалом в адресе (%r)" % (raw,))
            for link in links:
                self.assertIn("update-interval=%d&" % (hours * 60), link)
            hints = [c["hint"]["ru"] for c in b["clients"] if c.get("hint", {}).get("ru")]
            self.assertFalse([h for h in hints if "{minutes}" in h])

    def test_update_slow_clients(self):
        # A4-3/B4-4: приложения каталога, которые обновляют подписку сами реже совета сервера (FlClash — 24 ч), —
        # в update_slow; совет сервера не реже их — список пуст. Быстрый путь равен мосту.
        for raw, slow in ((None, [{"name": "FlClash", "hours": 24}]), (8, [{"name": "FlClash", "hours": 24}]),
                          (24, []), (48, [])):
            if raw is not None:
                STAND.config_merge({"subs": {"update_interval": raw}})
            b = self.same("sub-list", {"name": "anna"})
            self.assertEqual(b["update_slow"], slow, raw)
            self.same("sub-list", {})

    def test_no_users_file_yet(self):
        os.unlink(str(STAND.users))                             # законный скелет: людей нет, это не отказ
        b = self.same("user-list")
        self.assertEqual(b["users"], [])
        self.same("sub-list", {})

    def test_fractions_go_to_the_bridge(self):
        # Дробь jq 1.6 и 1.7 печатают по-разному (4.5 / 4.50, 1400.0 / 1400): в ответе или внутри ключа она развела
        # бы модуль и мост на одном из них — такое отвечает мост.
        cfg = json.loads(STAND.config.read_text(encoding="utf-8"))
        cfg["profiles"]["awg2"]["params"]["Jc"] = 4.5
        STAND.write_json(STAND.config, cfg)
        self.assertIsNone(STAND.fast("user-key", {"name": "anna", "profile": "awg2", "format": "conf"}))
        users = json.loads(STAND.users.read_text(encoding="utf-8"))
        users["users"][0]["traffic"] = {"rx": 1.5, "tx": 0}
        STAND.write_json(STAND.users, users)
        self.assertIsNone(STAND.fast("user-list"))

    def test_broken_files_go_to_the_bridge(self):
        STAND.users.write_text('{"users": [', encoding="utf-8")
        for action, params in (("user-list", {}), ("user-show", {"name": "anna"}), ("sub-list", {"name": "anna"})):
            self.assertIsNone(STAND.fast(action, params), action)
        b = STAND.bridge("user-list")
        self.assertEqual(b["status"], "error")
        self.assertIn(b["code"], ("users_corrupt", "users_failed"))


CORE_SH = r'''
set -uo pipefail
ROOT="$1"
source "$ROOT/lib/common.sh"
source "$ROOT/lib/i18n.sh"
load_language ru >/dev/null 2>&1
for _m in docker amnezia users export edgeips website preflight share pages subs mirror backup; do
    source "$ROOT/lib/$_m.sh"
done
'''


def _same_shares(b, f):
    """seconds_left считают от часов — между вызовами могла пройти секунда."""
    for x in (f or {}).get("shares", []):
        for y in b.get("shares", []):
            if x.get("id") == y.get("id") and abs(x["seconds_left"] - y["seconds_left"]) <= 2:
                x["seconds_left"] = y["seconds_left"]


@unittest.skipUnless(CAN_RUN, SKIP_WHY)
class EdgeTests(unittest.TestCase):
    """Края, которые проверил контроль 05.10 (64 сравнения с мостом, CR-4): тот же ответ, что у моста, или None там,
    где отвечает только мост. Каждый тест правит стенд и возвращает его целиком (goamnezia/, раздачи, .env бота)."""

    def setUp(self):
        self.backup = Path(tempfile.mkdtemp(prefix="ga-edge-"))
        self.share_dir = Path(STAND.env["GOAMNEZIA_LOCK_DIR"]) / "share"
        shutil.copytree(str(STAND.gdir), str(self.backup / "goamnezia"), symlinks=True)
        shutil.copytree(str(self.share_dir), str(self.backup / "share"), symlinks=True)
        self.bot_env = Path(STAND.env["BOT_DIR"]) / ".env"
        self.bot_env_text = self.bot_env.read_bytes()

    def tearDown(self):
        shutil.rmtree(str(STAND.gdir))
        shutil.copytree(str(self.backup / "goamnezia"), str(STAND.gdir), symlinks=True)
        shutil.rmtree(str(self.share_dir))
        shutil.copytree(str(self.backup / "share"), str(self.share_dir), symlinks=True)
        self.bot_env.write_bytes(self.bot_env_text)
        shutil.rmtree(str(self.backup), ignore_errors=True)

    def same(self, action, params=None, lang="", qr=False):
        if qr and not QRENCODE:
            return None
        b = STAND.bridge(action, params, lang)
        f = STAND.fast(action, params, lang)
        self.assertIsNotNone(f, "%s %s: отдал мосту (%s)" % (action, params, dumps(b)[:300]))
        if action == "share-list":
            _same_shares(b, f)
        self.assertEqual(dumps(b), dumps(f), "%s %s" % (action, params))
        return b

    def bridge_only(self, action, params=None, lang=""):
        b = STAND.bridge(action, params, lang)
        self.assertIsNone(STAND.fast(action, params, lang), "%s %s: это отвечает мост" % (action, params))
        return b

    def core(self, body):
        p = subprocess.run([BASH5, "-c", CORE_SH + body + "\necho done\n", "core", str(ROOT)], env=STAND.env,
                           capture_output=True, text=True, timeout=600)
        self.assertIn("done", p.stdout, p.stderr[-800:])
        self.assertNotIn("FAIL", p.stdout, p.stderr[-800:])

    def edit_user(self, name, **fields):
        doc = json.loads(STAND.users.read_text(encoding="utf-8"))
        for u in doc["users"]:
            if u["name"] == name:
                u.update(fields)
        STAND.write_json(STAND.users, doc)

    def edit_config(self, fn):
        cfg = json.loads(STAND.config.read_text(encoding="utf-8"))
        fn(cfg)
        STAND.write_json(STAND.config, cfg)

    def test_expired_user(self):
        self.edit_user("anna", expires="2020-01-01T00:00:00Z")        # срок вышел, сторож ещё не выключил
        self.same("user-list")
        self.same("user-show", {"name": "anna"})
        self.same("user-key", {"name": "anna", "profile": "awg2", "format": "conf"})
        self.same("sub-list", {"name": "anna"})
        self.same("sub-show", {"name": "anna"})

    def test_inactive_peer(self):
        doc = json.loads(STAND.users.read_text(encoding="utf-8"))
        next(u for u in doc["users"] if u["name"] == "anna")["peers"]["awg2"]["active"] = False
        STAND.write_json(STAND.users, doc)
        b = self.same("user-key", {"name": "anna", "profile": "awg2", "format": "conf"})
        self.assertTrue(b["disabled"])
        self.same("user-show", {"name": "anna"})
        self.same("sub-show", {"name": "anna"})

    def test_awg1_lines_1_5_and_1_0(self):
        self.core('amnezia_profile_install awg1 40002 default >/dev/null 2>&1 || echo FAIL-awg1\n'
                  'users_add gina "Gina" awg1,awg2 >/dev/null 2>&1 || echo FAIL-gina\n'
                  'subs_create gina >/dev/null 2>&1 || echo FAIL-sub')
        b = self.same("user-key", {"name": "gina", "profile": "awg1", "format": "all"}, qr=True)
        if b:
            self.assertEqual(b["line"], "1.5")
        self.same("user-key", {"name": "gina", "profile": "awg1", "format": "conf"})
        self.same("sub-show", {"name": "gina"})
        self.same("sub-list", {"name": "gina"})
        self.edit_config(lambda c: c["profiles"]["awg1"].__setitem__("line", "1.0"))
        b = self.same("user-key", {"name": "gina", "profile": "awg1", "format": "vpn"})
        self.assertEqual(b["line"], "1.0")
        self.same("user-key", {"name": "gina", "profile": "awg1", "format": "all"}, qr=True)
        self.same("sub-show", {"name": "gina"})

    def test_rotated_and_revoked_subscription(self):
        before = STAND.bridge("sub-show", {"name": "anna"})
        self.core('subs_rotate anna >/dev/null 2>&1 || echo FAIL-rotate')
        b = self.same("sub-show", {"name": "anna"})
        self.assertNotEqual(b["base_url"], before["base_url"])        # новый секрет — новый адрес
        self.same("sub-list", {})
        self.core('subs_revoke carol >/dev/null 2>&1 || echo FAIL-revoke')
        b = self.bridge_only("sub-show", {"name": "carol"})
        self.assertEqual((b["status"], b.get("code")), ("error", "sub_not_found"))
        b = self.same("sub-list", {})
        self.assertNotIn("carol", [s["name"] for s in b["subs"]])

    def test_short_links_without_the_bot(self):
        self.bot_env.unlink()
        b = self.same("sub-show", {"name": "anna"})
        self.assertFalse(b["short_available"])
        self.assertFalse([i for c in b["clients"] for i in c["items"] if i["short_url"]])

    def test_expired_page_and_expiry_as_text(self):
        pages = STAND.gdir / "pages"
        pid = sorted(p.name for p in pages.iterdir() if p.name.startswith("p-"))[0]
        meta_path = pages / pid / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["expires_epoch"] = int(time.time()) - 10
        STAND.write_json(meta_path, meta)
        b = self.same("page-list", {})
        self.assertNotIn(pid, [p["id"] for p in b["pages"]])
        self.same("page-list", {"name": meta["user"]})
        meta["expires_epoch"] = "123"                                  # строкой — как у jq -r: «истекла»
        STAND.write_json(meta_path, meta)
        self.same("page-list", {})

    def test_expired_share_still_draining_and_swept(self):
        d = self.share_dir / "s-1a2b"
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        meta["expires_epoch"] = int(time.time()) - 5
        STAND.write_json(d / "meta.json", meta)
        (d / "busy").write_text(str(int(time.time())), encoding="utf-8")
        b = self.same("share-list")                                   # истекла, но ещё отдаёт файл — в списке
        self.assertEqual((b["count"], b["shares"][0]["seconds_left"]), (1, 0))
        # Истекла и не занята — уборщик ядра её снимет (мутация): отвечает мост. Процесс раздачи — свой sleep, его
        # уборщик и остановит.
        (d / "busy").unlink()
        sleeper = subprocess.Popen(["sleep", "60"])
        try:
            (d / "pid").write_text(str(sleeper.pid), encoding="utf-8")
            self.assertIsNone(STAND.fast("share-list"))
            b = STAND.bridge("share-list")
            self.assertEqual(b["count"], 0)
        finally:
            sleeper.kill()
            sleeper.wait()

    def test_language_from_config_lang_and_language(self):
        self.edit_config(lambda c: c.__setitem__("lang", "en"))       # язык вызова не задан — язык установки
        self.same("user-list")
        b = self.same("user-key", {"name": "anna", "profile": "awg3", "format": "vpn"})
        self.assertEqual(b["message"], STAND.bridge("user-list", lang="en")["message"])
        self.same("sub-show", {"name": "anna"})

        def language(c):
            c.pop("lang", None)
            c["language"] = "en"
        self.edit_config(language)
        self.same("user-show", {"name": "anna"})

    def test_lite_site_with_empty_cert(self):
        self.edit_config(lambda c: (c.__setitem__("mode", "lite_site"), c["site"].__setitem__("cert", "")))
        self.same("sub-show", {"name": "anna"})
        self.same("user-show", {"name": "anna"})
        self.same("user-key", {"name": "anna", "profile": "awg2", "format": "conf"})

    def test_selfsigned_site_key(self):
        self.edit_config(lambda c: c["site"].__setitem__("cert", "selfsigned"))
        b = self.same("user-key", {"name": "anna", "profile": "awg2", "format": "conf"})
        self.assertEqual(b["key_host"], "203.0.113.10")               # домен без настоящего сертификата — не в ключ
        b = self.bridge_only("sub-show", {"name": "anna"})
        self.assertEqual(b.get("code"), "subs_need_domain")

    def test_imported_profile_without_line(self):
        def imported(c):
            p = c["profiles"]["awg2"]
            p["container"] = "amnezia-awg"
            p["imported"] = True
            p.pop("line", None)
        self.edit_config(imported)
        self.same("user-key", {"name": "anna", "profile": "awg2", "format": "all"}, qr=True)
        self.same("user-key", {"name": "anna", "profile": "awg2", "format": "vpn"})
        self.same("sub-show", {"name": "anna"})
        self.same("sub-list", {})

    def test_no_server_ip_and_dns_as_text(self):
        self.edit_config(lambda c: (c.__setitem__("mode", "lite"), c.__setitem__("server_ip", None)))
        self.same("user-show", {"name": "anna"})                     # карточка адрес в сети не спрашивает
        self.bridge_only("user-key", {"name": "anna", "profile": "awg2", "format": "conf"})   # ключ — спросил бы
        self.edit_config(lambda c: (c.__setitem__("server_ip", "203.0.113.10"), c.__setitem__("client_dns", "8.8.8.8")))
        self.bridge_only("user-key", {"name": "anna", "profile": "awg2", "format": "conf"})

    def test_foreign_locale(self):
        # LANG=en_US.UTF-8 без LC_ALL: glob bash упорядочивает имена каталога по правилам локали — имена не вида
        # p-/s-<hex> модуль в такой локали сам не сортирует, отвечает мост; остальное — тот же ответ.
        env = {k: v for k, v in STAND.env.items() if k not in ("LC_ALL", "LC_COLLATE")}
        env["LANG"] = "en_US.UTF-8"
        core = core_read.CoreRead(str(ROOT), environ=env)
        for action, params in (("user-list", {}), ("user-show", {"name": "anna"}), ("page-list", {}),
                               ("share-list", {}), ("sub-list", {"name": "anna"})):
            b = STAND.bridge(action, params, env=env)
            f = core.answer(action, params, "", "panel:tunnel")
            if action == "share-list":
                _same_shares(b, f)
            self.assertEqual(dumps(b), dumps(f), action)
        b = STAND.bridge("sub-list", {}, env=env)
        self.assertEqual((b["status"], b["count"]), ("success", 3))
        self.assertIsNone(core.answer("sub-list", {}, "", "panel:tunnel"))

    def test_fingerprint_mismatch_answers_none(self):
        core = core_read.CoreRead(str(ROOT), environ=STAND.env)
        err = io.StringIO()
        with mock.patch.dict(core_read.RECORDED, {"user-show": "0" * 16}), mock.patch.object(sys, "stderr", err):
            self.assertIsNone(core.answer("user-show", {"name": "anna"}, "", "panel:tunnel"))
            self.assertIsNone(core.answer("user-show", {"name": "anna"}, "", "panel:tunnel"))
            self.assertIsNotNone(core.answer("user-list", {}, "", "panel:tunnel"))
        self.assertEqual(err.getvalue().count("tests/check_core_read.py"), 1)    # одна строка на действие

    def test_panel_without_the_fast_path(self):
        # GOAMNEZIA_CORE_READ=0, модуля нет (ядро старше 1.0.10), модуль битый — карточка открывается мостом, как
        # в 1.0.9; в журнале — только вид ошибки.
        empty = self.backup / "old-core"
        (empty / "lib").mkdir(parents=True)
        broken = self.backup / "broken-core"
        (broken / "lib").mkdir(parents=True)
        (broken / "lib" / "core_read.py").write_text("raise RuntimeError('privkey=SECRETSECRET')\n", encoding="utf-8")
        for what, extra, root in (("выключатель", {"GOAMNEZIA_CORE_READ": "0"}, ROOT), ("нет модуля", {}, empty),
                                  ("битый модуль", {}, broken)):
            with mock.patch.dict(os.environ, dict(STAND.env, **extra), clear=True):
                server = load_panel(STAND)
                server.CORE_READ_ROOT = str(root)
                launches = []
                real = server._bridge_exec

                def counting(name, *a, **kw):
                    launches.append(name)
                    return real(name, *a, **kw)

                err = io.StringIO()
                with mock.patch.object(server, "_bridge_exec", side_effect=counting), \
                        mock.patch.object(sys, "stderr", err):
                    self.assertIsNone(server.core_read(), what)
                    status, card = server.user_card_payload("tunnel", "anna")
                self.assertEqual((status, card["data"]["user"]["name"]), (200, "anna"), what)
                self.assertIn("user-show", launches, what)
                self.assertNotIn("SECRETSECRET", err.getvalue(), what)
                if root == broken:
                    self.assertIn("RuntimeError", err.getvalue())


@unittest.skipUnless(CAN_RUN, SKIP_WHY)
class AuditTests(unittest.TestCase):
    """Выдача ключа и показ адресов подписки — та же строка аудита, что пишет ядро."""

    def lines(self):
        if not STAND.audit.exists():
            return []
        return [json.loads(l) for l in STAND.audit.read_text(encoding="utf-8").splitlines() if l.strip()]

    def last_effect(self, before, action):
        new = [l for l in self.lines()[before:] if l.get("action") == action and l.get("stage") == "effect"]
        self.assertEqual(len(new), 1, "ровно одна строка %s: %s" % (action, new))
        row = dict(new[0])
        row.pop("ts")
        return row

    def test_same_audit_line_as_the_core(self):
        cases = [("user-key", {"name": "anna", "profile": "awg2", "format": "conf"}), ("sub-show", {"name": "anna"})]
        for action, params in cases:
            n = len(self.lines())
            STAND.bridge(action, params)
            want = self.last_effect(n, action)
            n = len(self.lines())
            self.assertIsNotNone(STAND.fast(action, params))
            have = self.last_effect(n, action)
            self.assertEqual(want, have)
            self.assertEqual(have["who"], "panel:tunnel")
        raw = STAND.audit.read_text(encoding="utf-8")
        users = json.loads(STAND.users.read_text(encoding="utf-8"))["users"]
        for u in users:
            for peer in u["peers"].values():
                for secret in (peer.get("privkey"), peer.get("psk")):
                    if secret:
                        self.assertNotIn(secret, raw)

    def test_fallback_writes_nothing(self):
        n = len(self.lines())
        self.assertIsNone(STAND.fast("user-key", {"name": "dave", "profile": "awg2", "format": "all"}))
        self.assertIsNone(STAND.fast("sub-show", {"name": "eve"}))
        self.assertEqual(len(self.lines()), n)


@unittest.skipUnless(CAN_RUN, SKIP_WHY)
class LockTests(unittest.TestCase):
    """Чтение — под общим замком ядра на чтение: запись ядра видна целиком или не видна."""

    def setUp(self):
        self.saved = STAND.users.read_bytes()
        self.saved_cfg = STAND.config.read_bytes()
        self.lock_file = Path(STAND.env["GOAMNEZIA_LOCK_DIR"]) / "config.lock"
        self.lock_file.touch()

    def tearDown(self):
        STAND.users.write_bytes(self.saved)
        STAND.config.write_bytes(self.saved_cfg)

    def test_read_during_core_write_sees_all_or_nothing(self):
        users = json.loads(self.saved)
        cfg = json.loads(self.saved_cfg)
        for u in users["users"]:
            if u["name"] == "anna":
                u["label"] = "Анна после правки"
        cfg["endpoint_host"] = "vpn.example.org"
        started, half = threading.Event(), threading.Event()

        def core_write():
            # Как ga_with_config_lock: LOCK_EX на всё время правки; users.json пишется прямо поверх (половина
            # файла видна читателю без замка), потом config.json.
            fd = os.open(str(self.lock_file), os.O_WRONLY | os.O_CREAT, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
            started.set()
            data = json.dumps(users, ensure_ascii=False).encode("utf-8")
            with open(STAND.users, "wb") as fh:
                fh.write(data[:len(data) // 2])
                fh.flush()
                half.set()
                time.sleep(0.3)
                fh.write(data[len(data) // 2:])
            time.sleep(0.1)
            STAND.write_json(STAND.config, cfg)
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

        w = threading.Thread(target=core_write)
        w.start()
        self.assertTrue(half.wait(5))
        t0 = time.monotonic()
        card = STAND.fast("user-show", {"name": "anna"})
        waited = time.monotonic() - t0
        w.join()
        self.assertIsNotNone(card, "половину файла чтение увидеть не должно было")
        self.assertEqual(card["user"]["label"], "Анна после правки")
        self.assertEqual(card["key_host"], "vpn.example.org")      # обе половины правки — вместе
        self.assertGreater(waited, 0.2)

    def test_stuck_writer_does_not_hold_reads(self):
        core = core_read.CoreRead(str(ROOT), environ=STAND.env, lock_wait=0.3)
        core_read.RECORDED.update(core.fingerprints())
        fd = os.open(str(self.lock_file), os.O_WRONLY | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            t0 = time.monotonic()
            out = core.answer("user-list", {}, "", "panel:tunnel")
            waited = time.monotonic() - t0
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        self.assertIsNotNone(out)                                  # файлы ядро пишет атомарно — читаем, как мост
        self.assertGreaterEqual(waited, 0.25)
        self.assertLess(waited, 2.0)

    def test_lock_wait_from_the_environment_is_bounded(self):
        # CR-6: GOAMNEZIA_READ_LOCK_WAIT — от 0 до 5 с; не число, nan и бесконечность — 1 с по умолчанию.
        for raw, want in (("0.5", 0.5), ("0", 0.0), ("-3", 0.0), ("100", 5.0), ("1e9", 5.0), ("nan", 1.0),
                          ("inf", 1.0), ("-inf", 1.0), ("abc", 1.0), ("", 1.0)):
            core = core_read.CoreRead(str(ROOT), environ=dict(STAND.env, GOAMNEZIA_READ_LOCK_WAIT=raw))
            self.assertEqual(core.lock_wait, want, raw)

    def _lock_free(self):
        """Замок ядра свободен для записи (LOCK_EX без ожидания) — значит, чтение его сейчас не держит."""
        fd = os.open(str(self.lock_file), os.O_WRONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
            return True
        finally:
            os.close(fd)

    def test_machine_addresses_and_qr_are_made_without_the_lock(self):
        # CR-7: под замком — только чтение файлов. Адреса машины (ip, до 5 с) собираются до него, qrencode (до 20 с на
        # картинку) — после: правка ядра их не ждёт.
        core = core_read.CoreRead(str(ROOT), environ=STAND.env)
        seen = []
        real_addrs, real_qr = core._edge_addrs, core._qr

        def addrs():
            seen.append(("ip", self._lock_free()))
            return real_addrs()

        def qr(text):
            seen.append(("qr", self._lock_free()))
            return real_qr(text)

        fmt = "all" if QRENCODE else "conf"
        with mock.patch.object(core, "_edge_addrs", side_effect=addrs), mock.patch.object(core, "_qr", side_effect=qr):
            card = core.answer("user-show", {"name": "eve"}, "", "panel:tunnel")
            key = core.answer("user-key", {"name": "eve", "profile": "awg3", "format": fmt}, "", "panel:tunnel")
            plain = core.answer("user-show", {"name": "anna"}, "", "panel:tunnel")
        self.assertEqual(card["key_host"], "198.51.100.7")         # свой адрес человека — у машины он есть
        self.assertEqual(key["key_host"], "198.51.100.7")
        self.assertEqual(plain["key_host"], "example.com")          # без своего адреса ip не нужен
        self.assertEqual([s for s in seen if s[0] == "ip"], [("ip", True), ("ip", True)])
        if QRENCODE:
            qrs = [ok for what, ok in seen if what == "qr"]
            self.assertGreaterEqual(len(qrs), 2)
            self.assertTrue(all(qrs), seen)

    def test_answer_has_one_deadline(self):
        # CR-7: весь ответ — не дольше срока (ANSWER_TIMEOUT; здесь 0,5 и 0,3 с). Висящий qrencode не растягивает его —
        # срок вышел, отвечает мост (None, без строки аудита); висящая правка ядра при lock_wait 5 с держит чтение
        # не дольше срока.
        slow = Path(STAND.tmp) / "slowbin"
        slow.mkdir(exist_ok=True)
        (slow / "qrencode").write_text("#!/bin/sh\nexec sleep 5\n", encoding="utf-8")
        (slow / "qrencode").chmod(0o755)
        core = core_read.CoreRead(str(ROOT), environ=dict(STAND.env, PATH="%s:%s" % (slow, STAND.env.get("PATH", ""))))
        audit = STAND.audit.read_text(encoding="utf-8") if STAND.audit.exists() else ""
        t0 = time.monotonic()
        out = core.answer("user-key", {"name": "anna", "profile": "awg2", "format": "qr-conf"}, "", "panel:tunnel",
                          timeout=0.5)
        waited = time.monotonic() - t0
        self.assertIsNone(out)
        self.assertLess(waited, 2.0)
        self.assertEqual(STAND.audit.read_text(encoding="utf-8") if STAND.audit.exists() else "", audit)
        core = core_read.CoreRead(str(ROOT), environ=STAND.env, lock_wait=5)
        fd = os.open(str(self.lock_file), os.O_WRONLY | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            t0 = time.monotonic()
            out = core.answer("user-list", {}, "", "panel:tunnel", timeout=0.3)
            waited = time.monotonic() - t0
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        self.assertIsNotNone(out)                                  # не дождались — читаем без замка, как мост
        self.assertLess(waited, 1.5)

    def test_lock_is_shared_between_readers_and_blocks_writers(self):
        # Два читателя одновременно не ждут друг друга, а запись ядра (LOCK_EX) ждёт обоих.
        with STAND.core.locked() as a:
            with STAND.core.locked() as b:
                self.assertTrue(a and b)
                fd = os.open(str(self.lock_file), os.O_WRONLY)
                try:
                    with self.assertRaises(OSError):
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                finally:
                    os.close(fd)


@unittest.skipUnless(CAN_RUN, SKIP_WHY)
class SecretTests(unittest.TestCase):
    """Секреты не уходят туда, где раньше не уходили."""

    def test_list_and_card_have_no_keys(self):
        users = json.loads(STAND.users.read_text(encoding="utf-8"))["users"]
        secrets = [p[k] for u in users for p in u["peers"].values() for k in ("privkey", "psk") if p.get(k)]
        self.assertTrue(secrets)
        for action, params in (("user-list", {}), ("user-show", {"name": "anna"}), ("sub-list", {}),
                               ("page-list", {}), ("share-list", {})):
            text = dumps(STAND.fast(action, params))
            self.assertNotIn('"privkey"', text)
            self.assertNotIn('"psk"', text)
            for s in secrets:
                self.assertNotIn(s, text, action)

    def test_crash_in_fast_path_logs_only_the_kind(self):
        with mock.patch.dict(os.environ, dict(STAND.env, GOAMNEZIA_CORE_READ_ROOT=str(ROOT)), clear=True):
            server = load_panel(STAND)
            core = server.core_read()
            self.assertIsNotNone(core, "модуль ядра не загрузился в панель")
            err = io.StringIO()
            boom = RuntimeError("privkey=SECRETSECRET")
            with mock.patch.object(type(core), "answer", side_effect=boom), \
                    mock.patch.object(server, "_bridge_exec", return_value=({"status": "success", "users": []}, True)), \
                    mock.patch.object(sys, "stderr", err):
                server._read_exec("user-list", {}, "", "tunnel", [], {}, 60, False)
            self.assertIn("RuntimeError", err.getvalue())
            self.assertNotIn("SECRETSECRET", err.getvalue())


def load_panel(stand):
    """admin-web/server.py со стендом: мост — обёртка вокруг install.sh тем bash, что умеет ядро."""
    wrapper = Path(stand.tmp) / "bridge.sh"
    if not wrapper.exists():
        wrapper.write_text('#!/bin/sh\nexec "%s" "%s" "$@"\n' % (BASH5, ROOT / "install.sh"), encoding="utf-8")
        wrapper.chmod(0o755)
    os.environ.update({"GOAMNEZIA_ACTION_BIN": str(wrapper), "GOAMNEZIA_ACTIONS_JSON": str(ROOT / "lib" / "actions.json"),
                       "GOAMNEZIA_ADMIN_DIR": str(Path(stand.tmp) / "admin"), "GOAMNEZIA_ADMIN_PORT": "1985",
                       "GOAMNEZIA_CORE_READ_ROOT": str(ROOT), "GOAMNEZIA_TRUST_TUNNEL": "1"})
    path = os.environ.get("GA_TEST_PANEL_SERVER") or str(ROOT / "admin-web" / "server.py")
    spec = importlib.util.spec_from_file_location("ga_admin_core_read_%d" % time.monotonic_ns(), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@unittest.skipUnless(CAN_RUN, SKIP_WHY)
class PanelCardTests(unittest.TestCase):
    """Открытие карточки в панели: до PERF-01 — четыре запуска моста (user-show, user-key, page-list, sub-list),
    теперь — ни одного; живые данные рядом (traffic, edge-status) — из снимка."""

    def test_card_open_does_not_start_the_bridge(self):
        with mock.patch.dict(os.environ, STAND.env, clear=True):
            server = load_panel(STAND)
            launches = []
            real = server._bridge_exec

            def counting(name, *a, **kw):
                launches.append(name)
                return real(name, *a, **kw)

            with mock.patch.object(server, "_bridge_exec", side_effect=counting):
                # Снимки живых данных — как после списка людей (их собирает список, не карточка)
                self.assertTrue(server.bridge_ok(server.read_snapshot("traffic", "tunnel")))
                self.assertTrue(server.bridge_ok(server.run_action("edge-status", {}, "tunnel")))
                launches.clear()
                t0 = time.monotonic()
                status, card = server.user_card_payload("tunnel", "anna")
                key = server.run_action("user-key", {"name": "anna", "profile": "awg2", "format": "conf"}, "tunnel")
                pages = server.pages_payload("tunnel", "anna")
                subs = server.run_action("sub-list", {"name": "anna"}, "tunnel", use_cache=False)
                elapsed = time.monotonic() - t0
            self.assertEqual(status, 200)
            self.assertEqual(card["data"]["user"]["name"], "anna")
            self.assertTrue(server.bridge_ok(key) and key["conf"].startswith("[Interface]"))
            self.assertEqual(pages["count"], 2)
            self.assertEqual(subs["count"], 1)
            self.assertEqual(launches, [], "запуски моста при открытии карточки: %s" % launches)
            self.assertLess(elapsed, 1.0)

    def test_list_serves_old_traffic_and_refreshes_in_background(self):
        with mock.patch.dict(os.environ, STAND.env, clear=True):
            server = load_panel(STAND)
            clock = [time.time()]
            calls = []
            gate = threading.Event()

            def bridge(name, argv, env, timeout, stdin_data, secret_in):
                calls.append(name)
                if name == "traffic" and len([c for c in calls if c == "traffic"]) > 1:
                    gate.wait(5)
                return {"status": "success", "message": "ok", "profiles": [], "users": [], "action": name}, True

            with mock.patch.object(server, "_now", side_effect=lambda: clock[0]), \
                    mock.patch.object(server, "_bridge_exec", side_effect=bridge):
                server.users_payload("tunnel")                    # холодный: traffic собран, edge-status — в фоне
                self.assertEqual(calls.count("traffic"), 1)
                clock[0] += server.READ_CACHE_TTL["traffic"] + 5   # снимок устарел, но не старше READ_SWR_MAX_AGE
                t0 = time.monotonic()
                out = server.users_payload("tunnel")
                self.assertLess(time.monotonic() - t0, 0.5)        # не ждёт нового сбора
                self.assertIsInstance(out["users"], list)
                deadline = time.monotonic() + 5
                while calls.count("traffic") < 2 and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertEqual(calls.count("traffic"), 2)        # новый сбор идёт в фоне, один
                server.users_payload("tunnel")
                self.assertEqual(calls.count("traffic"), 2)        # к идущему сбору — без второго
                gate.set()

    @staticmethod
    def _online_bridge(clock, calls):
        """Мост-заглушка: anna с одним пиром; traffic — рукопожатие за 100 с до сбора (активный пир WireGuard
        обновляет его раз в 120 с)."""
        peers = {"awg2": {"pubkey": "PUB", "has_key": True}}

        def bridge(name, argv, env, timeout, stdin_data, secret_in):
            calls.append(name)
            if name == "user-list":
                return {"status": "success", "message": "ok", "users": [
                    {"name": "anna", "enabled": True, "peers": peers}]}, True
            if name == "user-show":
                return {"status": "success", "message": "ok", "name": "anna", "key_host": "", "key_host_gone": False,
                        "user": {"name": "anna", "enabled": True, "peers": peers}}, True
            if name == "traffic":
                return {"status": "success", "message": "ok", "profiles": [{"profile": "awg2", "peers": [
                    {"pubkey": "PUB", "latest_handshake_ts": int(clock[0]) - 100}]}]}, True
            return {"status": "success", "message": "ok", "action": name}, True
        return bridge

    def _settle(self, calls, name, n):
        deadline = time.monotonic() + 5
        while calls.count(name) < n and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(0.05)
        self.assertEqual(calls.count(name), n, calls)

    def test_old_traffic_never_turns_an_active_person_offline(self):
        # CR-2: «в сети» — рукопожатие моложе ONLINE_WINDOW (180 с) от нынешнего времени; активный пир обновляет его
        # раз в 120 с. Прежний снимок рукопожатий отдаётся не старше READ_SWR_MAX_AGE["traffic"] (120 + он + сбор
        # < 180), старше — список и карточка ждут свежий сбор. Ответ из прежнего снимка несёт swr{since, reads}.
        with mock.patch.dict(os.environ, dict(STAND.env, GOAMNEZIA_CORE_READ="0"), clear=True):
            server = load_panel(STAND)
            swr = server.READ_SWR_MAX_AGE["traffic"]
            self.assertLess(120 + swr + 10, server.ONLINE_WINDOW)
            clock = [1_800_000_000.0]
            calls = []
            with mock.patch.object(server, "_now", side_effect=lambda: clock[0]), \
                    mock.patch.object(server, "_bridge_exec", side_effect=self._online_bridge(clock, calls)):
                a = server.users_payload("tunnel")
                self._settle(calls, "edge-status", 1)              # подогрев адресов сервера для карточки
                self.assertTrue(a["users"][0]["online"])
                self.assertNotIn("swr", a)
                clock[0] += swr - 5                                # срок снимка вышел, READ_SWR_MAX_AGE — ещё нет
                b = server.users_payload("tunnel")
                self.assertTrue(b["users"][0]["online"])
                self.assertEqual((b["swr"]["since"], b["swr"]["reads"]), (a["snapshot_at"], ["traffic"]))
                self.assertEqual(b["swr"]["age"], int(swr - 5))
                self._settle(calls, "traffic", 2)                  # новый сбор — в фоне, один
                clock[0] += server.READ_CACHE_TTL["traffic"] + 5
                status, card = server.user_card_payload("tunnel", "anna")
                self.assertEqual(status, 200)
                self.assertTrue(card["data"]["user"]["online"])
                self.assertIn("snapshot_at", card["data"])
                self.assertIn("traffic", card["data"]["swr"]["reads"])
                self._settle(calls, "traffic", 3)
                clock[0] += swr + 60                               # старше READ_SWR_MAX_AGE: ждём свежий сбор
                c = server.users_payload("tunnel")
                self.assertEqual(calls.count("traffic"), 4)
                self.assertTrue(c["users"][0]["online"])
                self.assertNotIn("swr", c)

    def test_swr_switch_off_waits_for_a_fresh_collection(self):
        # CR-8: GOAMNEZIA_READ_SWR=0 — как в 1.0.9: прежний снимок не отдаётся, адреса сервера не подогреваются,
        # список и карточка ждут свежий сбор.
        env = dict(STAND.env, GOAMNEZIA_CORE_READ="0", GOAMNEZIA_READ_SWR="0")
        with mock.patch.dict(os.environ, env, clear=True):
            server = load_panel(STAND)
            self.assertFalse(server.read_swr_on())
            clock = [1_800_000_000.0]
            calls = []
            with mock.patch.object(server, "_now", side_effect=lambda: clock[0]), \
                    mock.patch.object(server, "_bridge_exec", side_effect=self._online_bridge(clock, calls)):
                server.users_payload("tunnel")
                time.sleep(0.1)
                self.assertEqual((calls.count("traffic"), calls.count("edge-status")), (1, 0))
                clock[0] += server.READ_CACHE_TTL["traffic"] + 5
                out = server.users_payload("tunnel")
                self.assertEqual(calls.count("traffic"), 2)        # собрано до ответа, не в фоне
                self.assertNotIn("swr", out)
                status, card = server.user_card_payload("tunnel", "anna")
                self.assertEqual(calls.count("edge-status"), 1)
                self.assertNotIn("swr", card["data"])


def _py_target(text, kind, name):
    """Узел ast места Python: функция, метод «Класс.метод» или ветка «функция:строка»."""
    tree = ast.parse(text)
    fn_name, _, value = name.partition(":") if kind == "case" else (name, "", "")
    cls, _, meth = fn_name.rpartition(".")
    target = None
    for node in tree.body:
        if cls and isinstance(node, ast.ClassDef) and node.name == cls:
            target = next((i for i in node.body if isinstance(i, ast.FunctionDef) and i.name == meth), None)
        elif not cls and isinstance(node, ast.FunctionDef) and node.name == fn_name:
            target = node
    if target is not None and kind == "case":
        target = next((s for s in ast.walk(target) if isinstance(s, ast.If) and isinstance(s.test, ast.Compare)
                       and isinstance(s.test.comparators[0], ast.Constant) and s.test.comparators[0].value == value),
                      None)
    return target


def _mutate(text, rel, kind, name, comment=False):
    """Правка кода в месте ядра: строка «:» (bash), «pass» (Python), лишний ключ (каталог действий). comment=True —
    правка не кода, а комментария (bash) или docstring (Python): отпечаток её не видит. → новый текст или None."""
    lines = text.split("\n")
    if rel.endswith(".json"):
        doc = json.loads(text)
        block = doc["actions" if kind == "action" else "param_types"]
        block[name] = dict(block[name], description={"ru": "правка", "en": "edit"}) if comment \
            else dict(block[name], x_mutated=True)
        return json.dumps(doc, ensure_ascii=False, indent=2)
    if rel.endswith(".py"):
        target = _py_target(text, kind, name)
        if target is None:
            return None
        first = target.body[0]
        doc = isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str)
        if comment:
            if not doc:
                lines.insert(first.lineno - 1, " " * first.col_offset + "# правка комментария")
            else:
                lines[first.lineno - 1] = lines[first.lineno - 1].replace('"""', '"""Правка. ', 1)
            return "\n".join(lines)
        if doc and len(target.body) > 1:
            first = target.body[1]
        lines.insert(first.lineno - 1, " " * first.col_offset + "pass")
        return "\n".join(lines)
    stmt = "# правка комментария" if comment else ":"
    if kind == "var":
        for i, line in enumerate(lines):
            if line.startswith(name + "="):
                if comment:
                    lines.insert(i, "# правка комментария")
                else:
                    lines[i] = name + "=x" + line[len(name) + 1:]
                return "\n".join(lines)
        return None
    if kind == "case":
        start = lines.index("_action_dispatch_locked() {")
        for i in range(start, len(lines)):
            if lines[i] == "        %s)" % name:
                lines.insert(i + 1, "            " + stmt)
                return "\n".join(lines)
        return None
    for i, line in enumerate(lines):
        m = re.match(r"^%s\(\)\s*\{(.*)$" % re.escape(name), line)
        if m:
            if m.group(1).strip() and not comment:
                lines[i] = "%s() { :; %s" % (name, m.group(1).strip())      # функция в одну строку
            elif m.group(1).strip():
                lines.insert(i, "# правка комментария")
            else:
                lines.insert(i + 1, "    " + stmt)
            return "\n".join(lines)
    return None


class KeyFileNameTests(unittest.TestCase):
    """Н-2: имя файла ключа в ответе user-key быстрого пути — то же правило, что у страницы (key_file_base в
    lib/pages_server.py, сверено с ga_key_file_base в tests/test_share_dryrun.sh): не длиннее 15 знаков до .conf."""

    def test_same_rule_as_the_page(self):
        sys.path.insert(0, str(ROOT / "lib"))
        try:
            import pages_server as ps
        finally:
            sys.path.pop(0)
        import random
        cr = object.__new__(core_read.CoreRead)
        rnd = random.Random(20261017)
        alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
        names = ["anna", "-x", "--a", "-", "verylongusername1", "verylongusername2", "abcdefghij", "abcdefghijk"]
        names += ["".join(rnd.choice(alphabet) for _ in range(rnd.randint(1, 32))) for _ in range(300)]
        plain = {"profiles": {"awg2": {"line": "2.0"}, "awg3": {"line": "3.1"}}}
        dup = {"profiles": {"awg2": {"line": "3.1"}, "awg3": {"line": "3.1"}}}
        for n in names:
            for cfg, profile, line, d in ((plain, "awg2", "2.0", False), (plain, "awg3", "3.1", False),
                                          (dup, "awg2", "3.1", True), (dup, "awg3", "3.1", True),
                                          (plain, "awg1", "", False)):
                got = cr._key_file_base(cfg, n, profile, line)
                self.assertEqual(got, ps.key_file_base(n, profile, line, d), (n, profile, line))
                self.assertLessEqual(len(got), 15, got)
                self.assertFalse(got.startswith("-"), got)


class MirrorTests(unittest.TestCase):
    """Места ядра, которые повторяет модуль, не менялись после сверки (то же, что tests/check_core_read.py), и правка
    любого из них выключает быстрый путь ровно тех действий, что его повторяют."""

    def test_core_places_did_not_change_since_the_check(self):
        spec = importlib.util.spec_from_file_location("ga_check_core_read", ROOT / "tests" / "check_core_read.py")
        chk = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(chk)  # type: ignore[union-attr]
        lines = []
        n = chk.check(str(ROOT), out=lines.append)
        self.assertEqual(n, 0, "\n" + "\n".join(lines))

    def test_places_the_review_found_missing_are_mirrored(self):
        # CR-3 (контроль 05.10): этих мест в отпечатке не было — их правка не выключала быстрый путь.
        profile_line = {"user-key", "sub-list", "sub-show"}
        need = {
            ("lib/shortlinks.py", "case", "main:urls"): {"sub-show"},
            ("lib/awgkeys.py", "case", "_run:conf"): {"user-key"},
            ("lib/awgkeys.py", "case", "_run:vpn-encode"): {"user-key"},
            ("lib/awgkeys.py", "case", "_run:qr-chunks"): {"user-key"},
            ("lib/awgkeys.py", "case", "_run:detect-line"): profile_line,
            ("lib/awgkeys.py", "fn", "_read_params_stdin"): profile_line,
            ("lib/amnezia.sh", "fn", "ga_awgkeys"): profile_line,
            ("install.sh", "pre", "_action_dispatch_locked"): set(core_read.ACTIONS),
            ("lib/users.sh", "fn", "ga_users_name_valid"): {"user-show", "user-key", "sub-show"},
            ("lib/users.sh", "fn", "users_exists"): {"user-show", "user-key", "sub-show"},
            ("lib/actions.json", "action", "user-key"): {"user-key"},
            ("lib/actions.json", "param", "format"): {"user-key"},
        }
        for item, acts in need.items():
            have = {a for a, its in core_read.MIRROR.items() if item in its}
            self.assertLessEqual(acts, have, item)

    def test_each_mirrored_place_turns_its_fast_path_off(self):
        # CR-3: правка кода в любом месте зеркала — функция или ветка bash, начало диспетчера, переменная, обвязка CLI
        # awgkeys.py и shortlinks.py, описание действия и тип параметра в lib/actions.json — меняет отпечаток ровно тех
        # действий, что это место повторяют: их быстрый путь выключен до сверки, у остальных — нет. Правка
        # комментария или docstring отпечаток не трогает.
        tmp = tempfile.mkdtemp(prefix="ga-mirror-")
        try:
            rels = sorted({rel for items in core_read.MIRROR.values() for rel, _, _ in items})
            for rel in rels:
                dst = Path(tmp) / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(ROOT / rel), str(dst))
            core = core_read.CoreRead(tmp, environ={})
            base = core.fingerprints()
            self.assertEqual(base, core_read.CoreRead(str(ROOT), environ={}).fingerprints())
            self.assertTrue(all(base.values()))
            places = sorted({item for items in core_read.MIRROR.values() for item in items})
            for kind in ("pre", "case", "var", "fn", "action", "param"):
                self.assertTrue([p for p in places if p[1] == kind], kind)
            for rel in ("lib/awgkeys.py", "lib/shortlinks.py", "lib/actions.json"):
                self.assertTrue([p for p in places if p[0] == rel], rel)
            for item in places:
                rel, kind, name = item
                path = Path(tmp) / rel
                orig = path.read_text(encoding="utf-8")
                want = {a for a, its in core_read.MIRROR.items() if item in its}
                try:
                    for comment in (False, True):
                        new = _mutate(orig, rel, kind, name, comment=comment)
                        self.assertIsNotNone(new, "не нашли место: %s" % (item,))
                        path.write_text(new, encoding="utf-8")
                        now = core.fingerprints()
                        got = {a for a in core_read.ACTIONS if now[a] != base[a]}
                        self.assertEqual(got, set() if comment else want, "%s, комментарий: %s" % (item, comment))
                        with mock.patch.dict(core_read.RECORDED, base), mock.patch.object(sys, "stderr", io.StringIO()):
                            off = {a for a in core_read.ACTIONS if not core.mirror_ok(a)}
                        self.assertEqual(off, set() if comment else want, "%s, комментарий: %s" % (item, comment))
                finally:
                    path.write_text(orig, encoding="utf-8")
            self.assertEqual(core.fingerprints(), base)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
