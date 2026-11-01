# -*- coding: utf-8 -*-
"""Жизнь сессии по-настоящему: настоящий HTTP, настоящие обработчики.

Сроки берутся из окружения, поэтому ждать пятнадцать минут не нужно — поднимаем
экземпляр с сессией на 6 секунд и окном на 4. Отправку в Telegram подменяем и
перехватываем код: гонять живые сообщения владельцу ради теста незачем, а
проверить надо именно то, что код доходит и продлевает.
"""
import http.cookiejar, importlib.util, json, os, re, sys, threading, time
import urllib.request, urllib.error

PORT = int(os.getenv("TEST_PORT", "19840"))
os.environ["GOTELEGRAM_SESSION_TTL"] = "6"
os.environ["GOTELEGRAM_SESSION_GRACE"] = "4"
os.environ["GOTELEGRAM_ADMIN_PORT"] = str(PORT)
# Стенд ходит на 127.0.0.1, то есть выглядит как вход по туннелю, а тот пускают
# без второго фактора. Здесь это выключаем: проверяем ИМЕННО жизнь сессии, и без
# выключателя все двадцать проверок мерили бы пустоту.
os.environ["GOTELEGRAM_TRUST_TUNNEL"] = "0"

PANEL = os.getenv("GT_PANEL", "/opt/gotelegram-admin/server.py")
spec = importlib.util.spec_from_file_location("srv", PANEL)
S = importlib.util.module_from_spec(spec)
spec.loader.exec_module(S)

CAPTURED = {"code": "", "html": False, "text": ""}
def fake_send(chat_id, text, html_mode=False):
    CAPTURED["text"] = text
    CAPTURED["html"] = html_mode
    m = re.search(r"(\d{6})", text)
    CAPTURED["code"] = m.group(1) if m else ""
    return True, ""
S._telegram_send = fake_send

httpd = S.ThreadingHTTPServer(("127.0.0.1", PORT), S.AdminHandler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
time.sleep(0.4)

B = f"http://127.0.0.1:{PORT}"
jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

def call(path, body=None, method=None):
    req = urllib.request.Request(
        B + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", "X-GoTelegram-Admin": "1"},
        method=method or ("POST" if body is not None else "GET"))
    try:
        with opener.open(req, timeout=15) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="ignore")
        try:
            return e.code, json.loads(raw or "{}")
        except Exception:
            return e.code, {"raw": raw[:120]}

R = []
def chk(name, got, want):
    good = got == want
    R.append(good)
    print(f"  {'✓' if good else '✗'} {name:52} {got!r}" + ("" if good else f"  ← ждали {want!r}"))

def state():
    return call("/api/session")[1].get("data", {}).get("state")

print(f"сессия {S.SESSION_TTL}с, окно продления {S.SESSION_GRACE}с\n")

# ── 1. вход по токену ────────────────────────────────────────────────────────
tok = S.admin_token()
req = urllib.request.Request(f"{B}/?token={tok}", headers={"X-GoTelegram-Admin": "1"})
with opener.open(req, timeout=15) as r:
    chk("вход по токену отдаёт панель", r.status, 200)
chk("cookie сессии выдана", any(c.name == S.SESSION_COOKIE for c in jar), True)
chk("состояние сразу после входа", state(), "ok")
chk("данные отдаются", call("/api/overview")[0], 200)

# ── 2. истечение → окно продления ────────────────────────────────────────────
time.sleep(S.SESSION_TTL + 0.6)
chk("после 15 минут состояние", state(), "renew")
code, body = call("/api/overview")
chk("данные в окне продления НЕ отдаются", code, 401)
chk("отказ объясняет причину", body.get("state"), "renew")
chk("отказ сообщает остаток секунд", body.get("seconds", 0) > 0, True)

# ── 3. продление кодом ───────────────────────────────────────────────────────
c, b = call("/api/auth/request", {})
chk("код запрошен", b.get("ok"), True)
chk("код ушёл моноширинным (копируется касанием)", "<code>" in CAPTURED["text"], True)
chk("разметка включена", CAPTURED["html"], True)
c, b = call("/api/auth/verify", {"code": CAPTURED["code"]})
chk("код принят", b.get("ok"), True)
chk("состояние после продления", state(), "ok")
chk("данные снова отдаются", call("/api/overview")[0], 200)

# ── 4. упущенное окно ────────────────────────────────────────────────────────
time.sleep(S.SESSION_TTL + S.SESSION_GRACE + 0.8)
chk("окно упущено — состояние", state(), "dead")
code, body = call("/api/overview")
chk("данные не отдаются", code, 401)
chk("причина — истекла, а не «нужен токен»", body.get("state"), "expired")

# ── 5. неверный код и подбор ────────────────────────────────────────────────
req = urllib.request.Request(f"{B}/?token={tok}", headers={"X-GoTelegram-Admin": "1"})
with opener.open(req, timeout=15) as r:
    pass
time.sleep(S.SESSION_TTL + 0.6)
call("/api/auth/request", {})
for i in range(S.TFA_MAX_ATTEMPTS):
    call("/api/auth/verify", {"code": "000000"})
c, b = call("/api/auth/verify", {"code": CAPTURED["code"]})
chk("после исчерпания попыток верный код отвергнут", b.get("ok"), False)

# ── 6. корзины лимита не пересекаются ───────────────────────────────────────
chk("корзина продления отдельная", S._tfa_bucket("127.0.0.1", False) != S.TFA_BUCKET_RENEW, True)
chk("лимит продления выше публичного",
    S._tfa_hour_limit(S.TFA_BUCKET_RENEW) > S._tfa_hour_limit(S.TFA_BUCKET_PUBLIC), True)

httpd.shutdown()
print("\n" + "─" * 78)
print(f"  проверок: {len(R)}   прошло: {sum(R)}   не прошло: {len(R)-sum(R)}")
sys.exit(0 if all(R) else 1)
