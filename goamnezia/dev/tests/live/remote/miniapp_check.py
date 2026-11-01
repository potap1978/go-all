#!/usr/bin/env python3
"""miniapp_check.py — вход в мини-апп (панель на домене) подписью Telegram initData: на сервере, под root.

    python3 miniapp_check.py owner|deny|invited [--expect ok|not_admin] [--api http://127.0.0.1:18081]

Путь настоящий: https://<домен>/<секретный путь>/api/… через nginx и настоящий сертификат, но на 127.0.0.1
(`curl --resolve`), — панель видит X-Forwarded-Host и НЕ считает запрос туннелем (вход без кода только по SSH-туннелю).
initData подписывает подставной Telegram (fake_tg_api.py, /_test/initdata) тем же выдуманным токеном, что лежит в .env
бота, — как его подписывает Telegram. Ни путь, ни подпись, ни cookie не печатаются: только коды ответа, коды отказа,
флаги cookie и «кто вошёл». Итог — строки «OK/FAIL проверка — доказательство», код выхода 1 при FAIL.
"""
import argparse
import json
import subprocess
import sys
import tempfile
import os
import urllib.parse
import urllib.request

CONFIG = "/opt/goamnezia/config.json"
PEOPLE = {"owner": 900000001, "stranger": 900000002, "invited": 900000003}
RESULTS = []


def res(ok: bool, name: str, evidence: str) -> None:
    RESULTS.append(ok)
    print("%-4s %s — %s" % ("OK" if ok else "FAIL", name, evidence), flush=True)


def panel_base() -> tuple:
    cfg = json.load(open(CONFIG, encoding="utf-8"))
    domain = str(cfg.get("domain") or "")
    path = str((cfg.get("panel") or {}).get("miniapp_path") or "").strip("/")
    if not domain or not path:
        sys.exit("panel on the domain is not configured")
    return domain, "https://%s/%s" % (domain, path)


def init_data(api: str, who: str, mode: str = "good") -> str:
    q = urllib.parse.urlencode({"user": PEOPLE[who], "mode": mode})
    with urllib.request.urlopen("%s/_test/initdata?%s" % (api, q), timeout=10) as r:
        return r.read().decode()


def curl(domain: str, url: str, method: str = "GET", body=None, headers=None, cookie_jar=None):
    """(код, заголовки ответа, тело) — запрос через nginx на 127.0.0.1 с настоящей проверкой сертификата."""
    with tempfile.TemporaryDirectory() as td:
        hdr_file = os.path.join(td, "h")
        cfg = ['url = "%s"' % url, 'request = "%s"' % method, "silent", "show-error", 'dump-header = "%s"' % hdr_file,
               'resolve = "%s:443:127.0.0.1"' % domain, 'write-out = "%{http_code}"', 'output = "%s"' % os.path.join(td, "b"),
               'max-time = 120']
        for k, v in (headers or {}).items():
            cfg.append('header = "%s: %s"' % (k, v.replace('"', '\\"')))
        if cookie_jar:
            cfg.append('cookie = "%s"' % cookie_jar)
            cfg.append('cookie-jar = "%s"' % cookie_jar)
        if body is not None:
            data = json.dumps(body)
            cfg.append('header = "Content-Type: application/json"')
            cfg.append('data-binary = "@%s"' % os.path.join(td, "d"))
            with open(os.path.join(td, "d"), "w", encoding="utf-8") as fh:
                fh.write(data)
        # Конфиг — через stdin: адрес с секретным путём и подпись не попадают в argv (/proc/*/cmdline)
        p = subprocess.run(["curl", "-K", "-"], input="\n".join(cfg), capture_output=True, text=True)
        code = int((p.stdout or "0").strip() or 0)
        hdrs = open(hdr_file, encoding="latin-1").read() if os.path.exists(hdr_file) else ""
        raw = open(os.path.join(td, "b"), "rb").read() if os.path.exists(os.path.join(td, "b")) else b""
    try:
        data = json.loads(raw.decode("utf-8")) if raw else {}
    except ValueError:
        data = {"_non_json": len(raw)}
    return code, hdrs, data


def login(domain: str, base: str, api: str, who: str, mode: str = "good", jar=None):
    return curl(domain, base + "/api/auth/miniapp", "POST", {"init_data": init_data(api, who, mode)},
                {"X-GoAmnezia-Admin": "1"}, jar)


def check_deny(domain, base, api):
    cases = [("stranger", "good", "miniapp_not_admin", "чужой id (подпись верна)"),
             ("owner", "badsig", "signature_invalid", "неверная подпись (hash изменён)"),
             ("owner", "othertoken", "signature_invalid", "подпись чужим токеном"),
             ("owner", "stale", "miniapp_expired", "auth_date 25 ч назад")]
    for who, mode, want, label in cases:
        code, hdrs, data = login(domain, base, api, who, mode)
        got = data.get("code") or data.get("error_code") or ""
        res(code == 401 and got == want and "ga_session=" not in hdrs, "мини-апп: %s → 401" % label,
            "HTTP %s, code=%s, cookie=%s" % (code, got, "да" if "ga_session=" in hdrs else "нет"))
    code, hdrs, data = curl(domain, base + "/api/auth/miniapp", "POST", {"init_data": ""}, {"X-GoAmnezia-Admin": "1"})
    res(code == 401, "мини-апп: пустой initData → 401", "HTTP %s, code=%s" % (code, data.get("code")))
    code, _h, data = curl(domain, base + "/api/overview", headers={"X-GoAmnezia-Admin": "1"})
    res(code == 401, "домен без входа: /api/overview → 401 (туннельного доверия нет)", "HTTP %s, state=%s" % (code, data.get("state")))
    bad = init_data(api, "stranger")
    code, _h, data = curl(domain, base + "/api/overview", headers={"X-GoAmnezia-Admin": "1", "X-Telegram-Init-Data": bad})
    res(code == 401, "заголовок X-Telegram-Init-Data чужого → 401", "HTTP %s" % code)


def check_login(domain, base, api, who, expect):
    with tempfile.TemporaryDirectory() as td:
        jar = os.path.join(td, "jar")
        code, hdrs, data = login(domain, base, api, who, jar=jar)
        if expect == "not_admin":
            res(code == 401 and data.get("code") == "miniapp_not_admin", "мини-апп: %s (ещё не админ) → 401" % who,
                "HTTP %s, code=%s" % (code, data.get("code")))
            return
        setc = [l for l in hdrs.splitlines() if l.lower().startswith("set-cookie:") and "ga_session=" in l]
        flags = setc[0].lower() if setc else ""
        res(code == 200 and data.get("ok") is True and bool(data.get("ticket")) and "samesite=none" in flags and "secure" in flags
            and "httponly" in flags,
            "мини-апп: вход %s по initData → 200" % who,
            "HTTP %s, cookie ga_session [%s], ticket=%s" % (code, ", ".join(x.strip() for x in flags.split(";")[1:] if x.strip()),
                                                             "да" if data.get("ticket") else "нет"))
        code, _h, data = curl(domain, base + "/api/session", headers={"X-GoAmnezia-Admin": "1"}, cookie_jar=jar)
        d = data.get("data") or {}
        res(code == 200 and d.get("state") == "ok" and d.get("who") == "tg:%d" % PEOPLE[who],
            "мини-апп: /api/session по cookie", "HTTP %s, state=%s, who=%s, ttl=%s" % (code, d.get("state"), d.get("who"), d.get("ttl")))
        code, _h, data = curl(domain, base + "/api/overview", headers={"X-GoAmnezia-Admin": "1"}, cookie_jar=jar)
        res(code == 200, "мини-апп: /api/overview по cookie", "HTTP %s, версия %s, людей %s" % (
            code, (data.get("data") or data).get("version"), (data.get("data") or data).get("users")))
    code, _h, data = curl(domain, base + "/api/overview",
                          headers={"X-GoAmnezia-Admin": "1", "X-Telegram-Init-Data": init_data(api, who)})
    res(code == 200, "мини-апп: /api/overview по заголовку X-Telegram-Init-Data (без cookie)", "HTTP %s" % code)


def jar_login(domain, base, api, who, jar):
    """Вход мини-аппа с cookie в файле jar (0600) — чтобы потом проверить ту же сессию (jar-check)."""
    fd = os.open(jar, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.close(fd)
    code, _h, data = login(domain, base, api, who, jar=jar)
    res(code == 200 and data.get("ok") is True, "мини-апп: вход %s, cookie сохранена" % who, "HTTP %s" % code)


def jar_check(domain, base, jar, expect):
    """Та же сессия (cookie из jar-login) — /api/session и /api/overview; expect ok | denied."""
    code, _h, data = curl(domain, base + "/api/session", headers={"X-GoAmnezia-Admin": "1"}, cookie_jar=jar)
    d = data.get("data") or {}
    code2, _h2, data2 = curl(domain, base + "/api/overview", headers={"X-GoAmnezia-Admin": "1"}, cookie_jar=jar)
    ok = (code2 == 200) if expect == "ok" else (code2 == 401)
    res(ok, "мини-апп: прежняя сессия (cookie) — ждём %s" % ("доступ" if expect == "ok" else "отказ"),
        "/api/session state=%s who=%s seconds=%s; /api/overview HTTP %s %s" % (
            d.get("state"), d.get("who"), d.get("seconds"), code2, data2.get("code") or data2.get("state") or ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["owner", "deny", "invited", "jar-login", "jar-check"])
    ap.add_argument("--expect", default="ok", choices=["ok", "not_admin", "denied"])
    ap.add_argument("--who", default="invited")
    ap.add_argument("--jar", default="/root/ga-live/bot/state/miniapp.jar")
    ap.add_argument("--api", default="http://127.0.0.1:18081")
    a = ap.parse_args()
    domain, base = panel_base()
    if a.what == "deny":
        check_deny(domain, base, a.api)
    elif a.what == "jar-login":
        jar_login(domain, base, a.api, a.who, a.jar)
    elif a.what == "jar-check":
        jar_check(domain, base, a.jar, "ok" if a.expect == "ok" else "denied")
    else:
        check_login(domain, base, a.api, a.what, a.expect)
    sys.exit(0 if all(RESULTS) else 1)


if __name__ == "__main__":
    main()
