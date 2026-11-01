#!/bin/bash
# goDNS — Copyright (c) 2026 anten-ka. All rights reserved.
#
# test_webui.sh — веб-панель: чистая логика, без root и без сети.
#
# Проверяется контракт входа (godns-web/server.py) и bash-обвязка
# (lib/webui.sh) на фикстурах во временном дереве. Сеть не нужна:
# отправка кода в Telegram подменяется, HTTP гоняется только по
# петле 127.0.0.1 на эфемерном порту.
#
# Обязательные отрицательные тесты (их отсутствие делает набор липой):
#   • код НЕ принимается после истечения TTL (300 с);
#   • код НЕ принимается на шестой попытке — даже правильный;
#   • чужой initData (битая подпись / не-админ / протухший) отвергается;
#   • запрос с посторонним Host отвергается (DNS-rebinding).
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

fail=0
ok()  { printf '  [ OK ] %s\n' "$1"; LAST_OK="$1"; }
bad() { printf '  [FAIL] %s\n' "$1"; fail=$((fail+1)); }

# Под `set -euo pipefail` любая необработанная единица убивает файл, и
# итоговый подвал не печатается вовсе: набор «падал», не назвав ни
# виновника, ни даже факт провала — на глаз это выглядело как обрыв.
# Ловушка называет строку и последнюю пройденную проверку.
LAST_OK="(ни одной)"
_died() {
    local rc=$? ln="$1"
    [ "$rc" -eq 0 ] && return 0
    printf '\n  \033[0;31m✗ test_webui.sh оборвался на строке %s (код %s)\033[0m\n' "$ln" "$rc"
    printf '    последняя пройденная проверка: %s\n\n' "$LAST_OK"
}
trap '_died "$LINENO"' ERR

# ═══════════════════════════════════════════════════════════════════════
# ЧАСТЬ 1 — server.py: вход тремя путями, отказы, страж Host
# ═══════════════════════════════════════════════════════════════════════
echo "== 1. server.py: аутентификация и стражи =="

mkdir -p "$WORK/etc/webui" "$WORK/static/sections" "$WORK/opt"
cat > "$WORK/opt/config.json" <<'EOF'
{"domain": "dns.example.org", "webui_path": "PaNeLsEcReT16ch", "doh_port": "4443", "doh_path": "abc"}
EOF
cat > "$WORK/etc/bot.env" <<'EOF'
BOT_TOKEN=123456:TEST-FIXTURE-TOKEN-abc
ALLOWED_IDS=424242, 777000
EOF
chmod 600 "$WORK/etc/bot.env"
printf 'GODNS-PANEL-INDEX-MARKER\n' > "$WORK/static/index.html"
printf '/* css-marker */\n' > "$WORK/static/theme.css"

export GODNS_ETC="$WORK/etc"
export GODNS_DIR="$WORK/opt"
export GODNS_CONFIG="$WORK/opt/config.json"
export GODNS_WEB_DIR="$WORK/web"
export GODNS_WEB_STATIC="$WORK/static"
# Словарь панели кладём в песочницу: перевод теперь часть поведения, и без
# него wt() уходит в запасной вид, а проверки сообщений сравнивают текст.
mkdir -p "$WORK/static/lang"
cp "$REPO/godns-web/static/lang/"*.json "$WORK/static/lang/" 2>/dev/null || true
export GODNS_WEB_TOKEN_FILE="$WORK/etc/webui/token"
export GODNS_WEB_BOT_ENV="$WORK/etc/bot.env"
export GODNS_PROGRAM_DIR="$REPO"

if python3 - "$REPO" <<'PY'; then ok "python-контракт входа — все проверки внутри прошли"; else bad "python-контракт входа (подробности выше)"; fi
import hashlib
import hmac
import http.cookies
import importlib.util
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

repo = sys.argv[1]
spec = importlib.util.spec_from_file_location("godns_web", repo + "/godns-web/server.py")
srv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(srv)

failures = []


def _deep_json_recurses():
    """Достижим ли RecursionError телом, которое мы вообще принимаем.

    Ветка `except RecursionError` в read_json_body нужна ровно тогда,
    когда переполнить разборщик можно телом НЕ БОЛЬШЕ нашего лимита в
    1 МБ. Проверять это фиксированной глубиной нельзя: порог зависит от
    версии Python. Первый вариант теста зашил 6000 — на macOS (3.9) это
    падало, на Ubuntu (3.12) разбиралось штатно, и набор был зелёным на
    одной машине и красным на другой.

    Ищем порог удвоением. Каждый уровень стоит два байта, поэтому предел
    поиска — половина лимита тела.
    """
    limit = 1024 * 1024 // 2
    depth = 1000
    while depth <= limit:
        try:
            json.loads("[" * depth + "]" * depth)
        except RecursionError:
            return True
        except Exception:
            return False
        depth *= 2
    return False


def check(name, cond, detail=""):
    if cond:
        print("     ok: " + name)
    else:
        failures.append(name)
        print("     FAIL: " + name + ("  [" + str(detail) + "]" if detail else ""))


BOT_TOKEN = "123456:TEST-FIXTURE-TOKEN-abc"


def make_init_data(uid, auth_date, token=BOT_TOKEN, tamper=False):
    user = json.dumps({"id": uid, "first_name": "T"})
    data = {"auth_date": str(int(auth_date)), "query_id": "AA", "user": user}
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if tamper:
        calc = ("0" if calc[0] != "0" else "1") + calc[1:]
    data["hash"] = calc
    return urllib.parse.urlencode(data)


now = time.time()

# ── подпись мини-аппа ───────────────────────────────────────────────────
check("initData админа с верной подписью принимается",
      srv.verify_telegram_init_data(make_init_data(424242, now), now=now) == "424242")
check("initData второго админа принимается",
      srv.verify_telegram_init_data(make_init_data(777000, now), now=now) == "777000")
check("ЧУЖОЙ initData: битая подпись отвергается",
      srv.verify_telegram_init_data(make_init_data(424242, now, tamper=True), now=now) is None)
check("ЧУЖОЙ initData: подпись чужого бота отвергается",
      srv.verify_telegram_init_data(make_init_data(424242, now, token="999:OTHER"), now=now) is None)
check("ЧУЖОЙ initData: валидная подпись, но НЕ админ — отвергается",
      srv.verify_telegram_init_data(make_init_data(31337, now), now=now) is None)
check("протухший initData (25 часов) отвергается",
      srv.verify_telegram_init_data(make_init_data(424242, now - 25 * 3600), now=now) is None)
check("пустой initData отвергается",
      srv.verify_telegram_init_data("", now=now) is None)

# ── 2FA: код, TTL, попытки, квоты ───────────────────────────────────────
sent = []
srv._telegram_send = lambda chat_id, text: (sent.append((chat_id, text)) or True)


def last_code():
    m = re.search(r"\b(\d{6})\b", sent[-1][1])
    return m.group(1) if m else ""


t0 = now
okk, msg = srv.tfa_request("203.0.113.7", now=t0)
check("код отправляется", okk, msg)
check("код уходит ВСЕМ админам, не первому попавшемуся",
      {c for c, _ in sent} == {"424242", "777000"}, sent)
code = last_code()
check("в сообщении есть шестизначный код", len(code) == 6)

okk, msg = srv.tfa_verify(code, now=t0 + srv.TFA_CODE_TTL + 1)
check("ОТРИЦАТЕЛЬНЫЙ: правильный код ПОСЛЕ TTL (301 с) не принимается",
      not okk and "истёк" in msg, msg)

okk, msg = srv.tfa_request("203.0.113.7", now=t0 + 40)
check("новый код после паузы выдаётся", okk, msg)
code = last_code()
okk, msg = srv.tfa_verify(code, now=t0 + 41)
check("правильный код в срок принимается", okk, msg)
okk, msg = srv.tfa_verify(code, now=t0 + 42)
check("код одноразовый: повторный ввод того же кода отвергается", not okk, msg)

okk, _ = srv.tfa_request("203.0.113.7", now=t0 + 80)
check("третий код выдан (для теста попыток)", okk)
code = last_code()
for i in range(5):
    okk, msg = srv.tfa_verify("000111", now=t0 + 81 + i)
    check("неверная попытка %d/5 отклонена" % (i + 1), not okk)
okk, msg = srv.tfa_verify(code, now=t0 + 90)
check("ОТРИЦАТЕЛЬНЫЙ: ШЕСТАЯ попытка не принимается, даже с ПРАВИЛЬНЫМ кодом",
      not okk and "попыток" in msg, msg)

okk, msg = srv.tfa_request("203.0.113.7", now=t0 + 95)
check("кулдаун: повторный запрос кода раньше 30 с отклоняется",
      not okk and "повторите" in msg, msg)

srv._TFA_REQUESTS.clear()
tq = t0 + 10000
for i in range(5):
    okk, _ = srv.tfa_request("198.51.100.2", now=tq + i * 31)
    check("квота: запрос %d/5 в час проходит" % (i + 1), okk)
okk, msg = srv.tfa_request("198.51.100.2", now=tq + 5 * 31)
check("квота: ШЕСТОЙ запрос кода за час с адреса отклоняется",
      not okk and "час" in msg, msg)
okk, _ = srv.tfa_request("192.0.2.99", now=tq + 5 * 31 + 1)
check("квота считается ПО адресу: другой адрес не заперт", okk)

# ── упавший бот не съедает квоту ────────────────────────────────────────
srv._TFA_REQUESTS.clear()
srv._telegram_send = lambda chat_id, text: False
okk, msg = srv.tfa_request("203.0.113.50", now=tq + 400)
check("бот лежит: запрос кода честно проваливается", not okk, msg)
srv._telegram_send = lambda chat_id, text: (sent.append((chat_id, text)) or True)
okk, msg = srv.tfa_request("203.0.113.50", now=tq + 401)
check("квота возвращена: следующий запрос не упирается в кулдаун", okk, msg)

# ── сессии и билеты ─────────────────────────────────────────────────────
s = srv.session_create(now=tq)
check("свежая сессия валидна", srv.session_valid(s, now=tq + 10))
check("сессия умирает после 12 часов", not srv.session_valid(s, now=tq + srv.SESSION_TTL + 1))
tk = srv.ticket_create(now=tq)
check("билет гасится один раз", srv.ticket_redeem(tk, now=tq + 5))
check("погашенный билет мёртв", not srv.ticket_redeem(tk, now=tq + 6))
tk2 = srv.ticket_create(now=tq)
check("просроченный билет (61 с) мёртв", not srv.ticket_redeem(tk2, now=tq + srv.TICKET_TTL + 1))

# ── 2FA: смена адреса не обходит лимит ──────────────────────────────────
# ГРАБЛИ: квоты считались ТОЛЬКО по client_ip. У атакующего с IPv6-подсетью
# адресов сколько угодно — каждый «новый» проходил и кулдаун, и часовую
# квоту, и админа заваливало кодами в Telegram. Плюс запись в словаре
# вычищалась лишь при ПОВТОРНОМ заходе того же адреса: одноразовые
# оставались навсегда.
_sent_real = srv._telegram_send
srv._telegram_send = lambda a, t: True
srv._TFA_REQUESTS.clear()
_passed = sum(1 for i in range(200)
              if srv.tfa_request("10.0.%d.%d" % (i // 250, i % 250),
                                 now=1000.0 + i)[0])
check("смена адреса не обходит потолок (прошло ровно столько, сколько разрешено)",
      _passed == srv.TFA_GLOBAL_PER_HOUR, "прошло %d" % _passed)
check("словарь адресов не растёт без предела",
      len(srv._TFA_REQUESTS) <= srv.TFA_IPS_MAX,
      "записей %d" % len(srv._TFA_REQUESTS))
# Спустя час всё открывается снова — потолок не должен запирать навсегда.
_later, _ = srv.tfa_request("10.9.9.9", now=1000.0 + 4000)
check("через час доступ снова открыт", _later)
check("истёкшие адреса убраны", len(srv._TFA_REQUESTS) <= 2,
      "осталось %d" % len(srv._TFA_REQUESTS))
srv._TFA_REQUESTS.clear()
srv._TFA_STATE.update({"hash": "", "expires": 0.0, "attempts": 0})
srv._telegram_send = _sent_real

# ── проверка подписи не спотыкается о неASCII ───────────────────────────
# ГРАБЛИ: hmac.compare_digest на строке с неASCII бросает TypeError, а hash
# в init_data целиком задаёт клиент. Вместо честного None вылетало
# исключение → 500 и полная трассировка в журнал на КАЖДЫЙ запрос.
check("подпись с неASCII-hash отвергается, а не падает",
      srv.verify_telegram_init_data("user=%7B%22id%22%3A1%7D&hash=%D0%9F%D0%B0") is None)
check("подпись с эмодзи-hash отвергается, а не падает",
      srv.verify_telegram_init_data("user=%7B%22id%22%3A1%7D&hash=%F0%9F%99%82") is None)

# ── отзыв доступа гасит живую сессию ────────────────────────────────────
# ГРАБЛИ: сессия хранила только СРОК, и guard_request на живой сессии
# больше ни с чем не сверялся. Админа вычёркивали из bot.env, чтобы
# отобрать доступ, а его открытая вкладка панели — а панель работает от
# root — жила ещё двенадцать часов. Отозвать можно было только
# перезапуском службы.
ENV_PATH = os.environ["GODNS_WEB_BOT_ENV"]
TOK_PATH = os.environ["GODNS_WEB_TOKEN_FILE"]
_env_before = open(ENV_PATH).read()
# Файл токена трогаем — значит обязаны вернуть и СОДЕРЖИМОЕ, и РЕЖИМ:
# ниже по набору проверяются его длина и права 0600, и первый вариант
# этих тестов уронил обе проверки, оставив после себя 0644 и 23 байта.
# Файла может ещё не быть — его создаёт часть 2 набора. Тогда наша
# задача не «вернуть содержимое», а не оставить его после себя вовсе.
try:
    _tok_before = open(TOK_PATH, "rb").read()
    _tok_mode_before = os.stat(TOK_PATH).st_mode & 0o777
except FileNotFoundError:
    _tok_before = None
    _tok_mode_before = None


def _set_admins(value):
    open(ENV_PATH, "w").write(
        "BOT_TOKEN=1234567890:FAKE-TOKEN-FOR-TESTS-XXXXXXXXXXXXXXXXXXX\n"
        "ADMIN_IDS=%s\n" % value)
    srv._GEN_CACHE["at"] = 0.0


_set_admins("424242,777")
s_id = srv.session_create(now=tq, tg_id="424242")
check("сессия мини-аппа годна", srv.session_valid(s_id, now=tq + 10))
_set_admins("777")
check("удалили админа — его вкладка погасла",
      not srv.session_valid(s_id, now=tq + 10))

_set_admins("777")
s_gen = srv.session_create(now=tq)
check("сессия из 2FA годна", srv.session_valid(s_gen, now=tq + 10))
_set_admins("777,888")
check("состав админов изменился — сессия без tg_id тоже погасла",
      not srv.session_valid(s_gen, now=tq + 10))

_set_admins("777")
s_tok = srv.session_create(now=tq)
open(TOK_PATH, "w").write("rotated-emergency-token\n")
srv._GEN_CACHE["at"] = 0.0
check("ротация аварийного токена гасит сессию",
      not srv.session_valid(s_tok, now=tq + 10))
if _tok_before is None:
    os.unlink(TOK_PATH)
    check("файла токена после теста нет, как и было",
          not os.path.exists(TOK_PATH))
else:
    open(TOK_PATH, "wb").write(_tok_before)
    os.chmod(TOK_PATH, _tok_mode_before)
    check("файл токена возвращён как был",
          open(TOK_PATH, "rb").read() == _tok_before
          and (os.stat(TOK_PATH).st_mode & 0o777) == _tok_mode_before)
srv._GEN_CACHE["at"] = 0.0

# И обратное: без изменений сессия жить обязана, иначе «отзыв» превратился
# бы в выброс хозяина на каждом чихе.
srv._GEN_CACHE["at"] = 0.0
s_live = srv.session_create(now=tq)
check("без изменений сессия живёт", srv.session_valid(s_live, now=tq + 10))
open(ENV_PATH, "w").write(_env_before)
srv._GEN_CACHE["at"] = 0.0

# ── состав админов читается как у демона ────────────────────────────────
# ГРАБЛИ: панель складывала id из ВСЕХ ключей, а botd брал только первый
# непустой. На bot.env, доставшемся от соседней сборки, человек оказывался
# админом панели (root), но не бота.
open(ENV_PATH, "w").write(
    "BOT_TOKEN=1234567890:FAKE-TOKEN-FOR-TESTS-XXXXXXXXXXXXXXXXXXX\n"
    "ADMIN_IDS=424242\n"
    "ALLOWED_IDS=999999\n")
srv._GEN_CACHE["at"] = 0.0
_ids = srv._bot_admin_ids()
check("первый непустой ключ побеждает, как у демона", _ids == {"424242"})
check("id из второго ключа админом не делает", "999999" not in _ids)
open(ENV_PATH, "w").write(_env_before)
srv._GEN_CACHE["at"] = 0.0

# ── кривое тело запроса — 400, а не 500 ─────────────────────────────────
check("глубоко вложенный JSON ломает разборщик (ветка не мёртвая)",
      _deep_json_recurses())

# ── разбор Host ─────────────────────────────────────────────────────────
check("host_name_only срезает порт", srv.host_name_only("dns.example.org:9445") == "dns.example.org")
check("host_name_only оставляет [::1]", srv.host_name_only("[::1]:8642") == "[::1]")
check("наш домен в допуске", "dns.example.org" in srv.allowed_hosts())
check("чужого домена в допуске нет", "evil.example" not in srv.allowed_hosts())

# ── ANSI-очистка ────────────────────────────────────────────────────────
check("strip_ansi снимает цвета",
      srv.strip_ansi("\x1b[0;32mOK\x1b[0m done") == "OK done\n")
check("strip_ansi оставляет последний кадр спиннера",
      srv.strip_ansi("wait |\rwait /\rготово") == "готово\n")

# ── живой страж: петлевой HTTP-сервер на эфемерном порту ────────────────
httpd = srv.LocalHTTPServer(("127.0.0.1", 0), srv.WebHandler)
port = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, daemon=True).start()
base = "http://127.0.0.1:%d" % port


def req(path, method="GET", host=None, body=None, headers=None):
    r = urllib.request.Request(base + path, method=method,
                               data=(json.dumps(body).encode() if body is not None else None))
    if host:
        r.add_header("Host", host)
    if body is not None:
        r.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, resp.read().decode("utf-8", "replace"), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), dict(e.headers)


st, body, _ = req("/", host="evil.example")
check("ОТРИЦАТЕЛЬНЫЙ: посторонний Host отвергается (DNS-rebinding), а не обслуживается",
      st == 403 and "host" in body, (st, body[:80]))
st, body, _ = req("/api/auth/request", method="POST", host="evil.example",
                  headers={"X-GoDNS-Admin": "1"}, body={})
check("посторонний Host отвергается и на маршрутах входа", st == 403, st)

st, body, _ = req("/")
check("без сессии корень отдаёт страницу входа, не панель",
      st == 200 and "прислать код" in body and "GODNS-PANEL-INDEX-MARKER" not in body, st)
st, body, _ = req("/theme.css")
check("ассет без секретов отдаётся до входа (его тянет страница входа)",
      st == 200 and "css-marker" in body, st)
st, body, _ = req("/api/ports")
check("API без входа — 401, не данные", st == 401, (st, body[:60]))

tok = srv.admin_token()
st, body, hdrs = req("/?token=" + tok)
check("токен из файла пускает на петле (SSH-туннель)",
      st == 200 and "GODNS-PANEL-INDEX-MARKER" in body, st)
# ГРАБЛИ, снятые живым прогоном: панель чистит ?token=… из адресной строки,
# и без cookie первая же перезагрузка выкидывала на форму входа. Вход по
# токену обязан выдать сессионную cookie вместе со страницей.
check("вход по токену выдаёт сессионную cookie (реглесс перезагрузки)",
      "gd_session" in (hdrs.get("Set-Cookie") or ""), hdrs.get("Set-Cookie", "")[:60])
cookie_tok = http.cookies.SimpleCookie()
cookie_tok.load(hdrs["Set-Cookie"])
st, body, _ = req("/", headers={"Cookie": "gd_session=" + cookie_tok["gd_session"].value})
check("после перезагрузки cookie от токен-входа держит сессию",
      st == 200 and "GODNS-PANEL-INDEX-MARKER" in body, st)
check("на петлевом слушателе cookie БЕЗ Secure (иначе Safari её выбросит)",
      "Secure" not in hdrs["Set-Cookie"], hdrs["Set-Cookie"])
st, body, _ = req("/?token=WRONG-TOKEN-1234567890")
check("неверный токен НЕ пускает", "GODNS-PANEL-INDEX-MARKER" not in body, st)
st, body, _ = req("/PaNeLsEcReT16ch/")
check("секретный путь на петле тоже работает (единый адрес)",
      st == 200 and "прислать код" in body, st)

# полный вход по коду через HTTP: запрос → верификация → cookie → панель
sent.clear()
srv._TFA_REQUESTS.clear()
st, body, _ = req("/api/auth/request", method="POST", body={})
check("HTTP: код запрошен", st == 200 and json.loads(body)["ok"], (st, body[:80]))
code = last_code()
st, body, hdrs = req("/api/auth/verify", method="POST", body={"code": "999999"})
check("HTTP: неверный код — 401", st == 401, st)
st, body, hdrs = req("/api/auth/verify", method="POST", body={"code": code})
check("HTTP: верный код — 200 и Set-Cookie", st == 200 and "Set-Cookie" in hdrs, (st, hdrs.get("Set-Cookie", "")[:40]))
cookie = http.cookies.SimpleCookie()
cookie.load(hdrs["Set-Cookie"])
sess = cookie["gd_session"].value
st, body, _ = req("/", headers={"Cookie": "gd_session=" + sess})
check("HTTP: сессионная cookie открывает панель",
      st == 200 and "GODNS-PANEL-INDEX-MARKER" in body, st)
st, body, _ = req("/", headers={"Cookie": "gd_session=forged-session-value"})
check("HTTP: поддельная cookie панели не открывает",
      "GODNS-PANEL-INDEX-MARKER" not in body, st)

# мини-апп через HTTP: заголовок с подписью пускает в API
init_ok = make_init_data(424242, time.time())
st, body, _ = req("/api/ports", headers={srv.MINIAPP_INITDATA_HEADER: init_ok})
check("HTTP: подпись мини-аппа в заголовке пускает в API", st == 200, (st, body[:60]))
init_bad = make_init_data(31337, time.time())
st, body, _ = req("/api/ports", headers={srv.MINIAPP_INITDATA_HEADER: init_bad})
check("HTTP: не-админ с валидной подписью в API не допущен", st == 401, st)

st, body, hdrs = req("/api/auth/miniapp", method="POST", body={"init_data": init_ok})
check("HTTP: /api/auth/miniapp даёт cookie и билет",
      st == 200 and "Set-Cookie" in hdrs and json.loads(body).get("ticket"), st)
tkt = json.loads(body)["ticket"]
st, body, hdrs = req("/?s=" + urllib.parse.quote(tkt))
check("HTTP: билет ?s= открывает панель один раз",
      st == 200 and "GODNS-PANEL-INDEX-MARKER" in body, st)
check("HTTP: погашенный билет тоже выдаёт cookie — перезагрузка не выбрасывает",
      "gd_session" in (hdrs.get("Set-Cookie") or ""), hdrs.get("Set-Cookie", "")[:60])
st, body, _ = req("/?s=" + urllib.parse.quote(tkt))
check("HTTP: погашенный билет второй раз НЕ открывает",
      "GODNS-PANEL-INDEX-MARKER" not in body, st)

st, body, _ = req("/api/ports/dot", method="POST",
                  headers={"Cookie": "gd_session=" + sess},
                  body={"enabled": False})
check("HTTP: запись без X-GoDNS-Admin отвергается (страж CSRF)", st == 403, (st, body[:60]))
st, body, _ = req("/api/ports/dot", method="POST",
                  headers={"Cookie": "gd_session=" + sess, "X-GoDNS-Admin": "1"},
                  body={"enabled": False})
check("HTTP: выключение DoT без слова-подтверждения отвергается",
      st == 400 and "ВЫКЛЮЧИТЬ" in body, (st, body[:80]))
st, body, _ = req("/api/backups/restore", method="POST",
                  headers={"Cookie": "gd_session=" + sess, "X-GoDNS-Admin": "1"},
                  body={"name": "../../etc/passwd", "confirm": "ВОССТАНОВИТЬ"})
check("HTTP: имя копии с путём отвергается", st == 400, (st, body[:80]))

# путь наружу из статики
st, body, _ = req("/..%2f..%2fetc%2fpasswd", headers={"Cookie": "gd_session=" + sess})
check("HTTP: обход каталога статики не проходит", "root:" not in body, st)

# ── свой апстрим: контракт /api/upstreams/custom/* ──────────────────────
# Мост подменяется лямбдой, как выше подменён _telegram_send: проверяется
# ГРАНИЦА — что отвергнуто до моста, что и как ушло в argv и окружение, как
# разобран ответ. Саму lib проверяет часть 2 на bash.
H = {"Cookie": "gd_session=" + sess, "X-GoDNS-Admin": "1"}
calls = []
_bridge_real = srv.bridge
_bridge_rc = [0, "  ℹ  Разрешаю имя…\nip\t1.1.1.1\nsni\tone.one.one.one\nport\t853\nok\tyes\nlatency\t 12 мс (хол. 41)\n"]


def fake_bridge(action, *args, **kw):
    calls.append((action, args, kw))
    return tuple(_bridge_rc)


srv.bridge = fake_bridge
PROBE = "/api/upstreams/custom/probe"
good = {"proto": "dot", "host": "one.one.one.one", "port": "853", "path": "", "ack_plain": False}

st, body, _ = req(PROBE, method="POST", headers={"Cookie": "gd_session=" + sess}, body=good)
check("custom/probe без X-GoDNS-Admin — 403", st == 403, st)
st, body, _ = req(PROBE, method="POST", headers={"X-GoDNS-Admin": "1"}, body=good)
check("custom/probe без сессии — 401", st == 401, st)

# 400 на КАЖДЫЙ негодный аргумент — и ни один не доходит до моста.
for label, patch in [
    ("proto=quic", {"proto": "quic"}),
    ("host с пробелом", {"host": "a b"}),
    ("host с ;", {"host": "x;rm"}),
    ("host пустой", {"host": ""}),
    ("port=\"0\"", {"port": "0"}),
    ("port=0 числом (не «по умолчанию»)", {"port": 0}),
    ("port=70000", {"port": "70000"}),
    ("port=abc", {"port": "abc"}),
    ("path без /", {"proto": "doh", "path": "dns-query"}),
    ("path с {id}", {"proto": "doh", "path": "/{id}"}),
    ("ack_plain=maybe", {"ack_plain": "maybe"}),
]:
    calls.clear()
    st, body, _ = req(PROBE, method="POST", headers=H, body=dict(good, **patch))
    check("custom/probe: %s → 400, мост не звался" % label,
          st == 400 and not calls, (st, body[:80]))

calls.clear()
st, body, _ = req(PROBE, method="POST", headers=H, body=good)
act, args, kw = calls[-1]
check("проба: argv = proto host port ack, и только они",
      act == "custom_probe" and args == ("dot", "one.one.one.one", "853", "no"), (act, args))
d = json.loads(body)["data"]
check("проба rc 0 → 200, ok:true, ip и latency разобраны из TSV",
      st == 200 and d["ok"] is True and d["ip"] == "1.1.1.1"
      and d["latency"] == "12 мс (хол. 41)", (st, d))
check("проба: TSV-строк в output нет, текст для оператора есть",
      "\t" not in d["output"] and "Разрешаю" in d["output"], d["output"])
calls.clear()
st, body, _ = req(PROBE, method="POST", headers=H,
                  body=dict(good, proto="doh", port="", path="/secret-path"))
act, args, kw = calls[-1]
check("проба: путь DoH НЕ в argv (cmdline читает любой), а в окружении GODNS_CUSTOM_PATH",
      "/secret-path" not in " ".join(args)
      and (kw.get("env") or {}).get("GODNS_CUSTOM_PATH") == "/secret-path", (args, kw))

_bridge_rc[:] = [3, "ip\t1.1.1.1\nsni\tone.one.one.one\nport\t853\nok\tno\nlatency\t  — таймаут\n"]
st, body, _ = req(PROBE, method="POST", headers=H, body=good)
d = json.loads(body)["data"]
check("проба rc 3 (не ответил) → 200 и ok:false — результат, а не HTTP-ошибка",
      st == 200 and d["ok"] is False and d["needs_ack"] is False and "таймаут" in d["latency"], (st, d))
_bridge_rc[:] = [4, "ip\t8.8.8.8\nsni\t\nport\t53\nprivate\tno\nneeds_ack\tyes\nok\t-\n"]
st, body, _ = req(PROBE, method="POST", headers=H,
                  body=dict(good, proto="plain", host="8.8.8.8", port="53"))
d = json.loads(body)["data"]
check("проба rc 4 → 200, needs_ack:true, private:false, ok:false",
      st == 200 and d["needs_ack"] is True and d["private"] is False and d["ok"] is False, (st, d))
_bridge_rc[:] = [1, "  ✗  Имя nope.invalid не разрешилось.\n"]
st, body, _ = req(PROBE, method="POST", headers=H, body=dict(good, host="nope.invalid"))
check("проба rc 1 → 502, причина в data.output",
      st == 502 and "не разрешилось" in json.loads(body)["data"]["output"], (st, body[:120]))

# Одна проба за раз: замок занят → 409, до моста не доходит.
_bridge_rc[:] = [0, "ok\tyes\nlatency\t1 мс\n"]
srv._PROBE_LOCK.acquire()
try:
    calls.clear()
    st, body, _ = req(PROBE, method="POST", headers=H, body=good)
    check("проба занята → 409 web_cu_probe_busy, мост не звался",
          st == 409 and "проба уже идёт" in body and not calls, (st, body[:80]))
finally:
    srv._PROBE_LOCK.release()
st, body, _ = req(PROBE, method="POST", headers=H, body=good)
check("замок пробы отпущен после ответа", st == 200, st)

ADD = "/api/upstreams/custom"
addb = {"proto": "dot", "host": "one.one.one.one", "port": "853", "path": "",
        "name": "Мой DoT", "enable": False, "ack_plain": False}
for label, patch in [("name из 41 знака", {"name": "x" * 41}), ("name с |", {"name": "a|b"}),
                     ("name с ;", {"name": "a;b"}), ("enable=maybe", {"enable": "maybe"}),
                     ("host с пробелом", {"host": "a b"})]:
    calls.clear()
    st, body, _ = req(ADD, method="POST", headers=H, body=dict(addb, **patch))
    check("custom add: %s → 400, мост не звался" % label, st == 400 and not calls, (st, body[:80]))
st, body, _ = req(ADD, method="POST", headers=H, body=dict(addb, name="я" * 40))
check("custom add: кириллическое имя в 40 знаков проходит", st == 200, (st, body[:80]))
srv._CATALOG_AT = 12345.0
_bridge_rc[:] = [0, "  ✓  Добавлен: Мой DoT\nid\tcustom-one-one-one-one\n"]
calls.clear()
st, body, _ = req(ADD, method="POST", headers=H, body=dict(addb, enable="no"))
act, args, kw = calls[-1]
check("custom add: argv = proto host port name enable ack; enable:\"no\" — это НЕТ",
      act == "custom_add" and args == ("dot", "one.one.one.one", "853", "Мой DoT", "no", "no"), (act, args))
d = json.loads(body)["data"]
check("custom add → 200, data.id из TSV-строки, TSV в output нет",
      st == 200 and d["id"] == "custom-one-one-one-one" and "\t" not in d["output"]
      and "Добавлен" in d["output"], (st, d))
check("после добавления кэш каталога сброшен (_CATALOG_AT == 0)", srv._CATALOG_AT == 0.0, srv._CATALOG_AT)
calls.clear()
st, body, _ = req(ADD, method="POST", headers=H,
                  body=dict(addb, proto="doh", port="", path="/secret-path", enable=True, ack_plain=True))
act, args, kw = calls[-1]
check("custom add: путь DoH только в окружении; enable/ack → yes",
      "/secret-path" not in " ".join(args)
      and (kw.get("env") or {}).get("GODNS_CUSTOM_PATH") == "/secret-path"
      and args[4:] == ("yes", "yes"), (args, kw))
srv._CATALOG_AT = 12345.0
_bridge_rc[:] = [1, "  ✗  Такой апстрим уже есть: Мой DoT\n"]
st, body, _ = req(ADD, method="POST", headers=H, body=addb)
check("custom add: отказ lib (дубль) → 502 с причиной", st == 502 and "уже есть" in body, (st, body[:120]))
check("после отказа кэш каталога тоже сброшен", srv._CATALOG_AT == 0.0, srv._CATALOG_AT)

RM = "/api/upstreams/custom/remove"
st, body, _ = req(RM, method="POST", headers=H, body={"id": "custom-x"})
check("custom remove: без слова → 400 и слово УБРАТЬ в ответе", st == 400 and "УБРАТЬ" in body, (st, body[:80]))
st, body, _ = req(RM, method="POST", headers=H, body={"id": "../x"})
check("custom remove: слово проверяется ПЕРВЫМ — негодный id без слова получает отказ про слово",
      st == 400 and "УБРАТЬ" in body, (st, body[:80]))
for bad_id in ("../x", "cloudflare", "custom-", "custom-X", "custom-a/../b", ""):
    calls.clear()
    st, body, _ = req(RM, method="POST", headers=H, body={"id": bad_id, "confirm": "REMOVE"})
    check("custom remove: id %r → 400 web_cu_bad_id, мост не звался" % bad_id,
          st == 400 and "непохоже" in body and not calls, (st, body[:80]))
with srv._MEASURE_LOCK:
    srv._MEASURE["custom-x"] = {"warm": 5}
    srv._MEASURE["cloudflare"] = {"warm": 7}
srv._CATALOG_AT = 12345.0
_bridge_rc[:] = [0, "  ✓  Удалён: Мой DoT\n"]
calls.clear()
st, body, _ = req(RM, method="POST", headers=H, body={"id": "custom-x", "confirm": "REMOVE"})
check("custom remove: слово REMOVE принимается, мост получил custom_remove custom-x",
      st == 200 and calls[-1][0] == "custom_remove" and calls[-1][1] == ("custom-x",), (st, calls))
check("после удаления замер записи забыт, чужой — нет, кэш каталога сброшен",
      "custom-x" not in srv._MEASURE and "cloudflare" in srv._MEASURE and srv._CATALOG_AT == 0.0)
with srv._MEASURE_LOCK:
    srv._MEASURE["custom-x"] = {"warm": 5}
_bridge_rc[:] = [1, "  ✗  Это единственный апстрим в рабочем наборе\n"]
st, body, _ = req(RM, method="POST", headers=H, body={"id": "custom-x", "confirm": "УБРАТЬ"})
check("custom remove: отказ lib (единственный) → 502, замер записи остаётся",
      st == 502 and "единственный" in body and "custom-x" in srv._MEASURE, (st, body[:120]))
with srv._MEASURE_LOCK:
    srv._MEASURE.clear()

# Каталог: девять полей нового моста и пять старого читаются одинаково.
_data_real = srv.bridge_data
CAT9 = ("custom-dns-example-org\tМой DoH\tnone\tсвой · DoH\tсвой\tyes\tdoh\t203.0.113.5:8443\tdns.example.org\n"
        "cloudflare\tCloudflare\tnone\tнет\tглобальный anycast\n")
srv.bridge_data = lambda action, *a, **kw: ((0, CAT9) if action == "catalog" else _data_real(action, *a, **kw))
srv._CATALOG = []
srv._CATALOG_AT = 0.0
st, body, _ = req("/api/upstreams", headers=H)
d = json.loads(body)["data"]
byid = {c["id"]: c for c in d["catalog"]}
cu = byid.get("custom-dns-example-org") or {}
check("каталог из 9 полей: custom:true, proto, addr, sni",
      st == 200 and cu.get("custom") is True and cu.get("proto") == "doh"
      and cu.get("addr") == "203.0.113.5:8443" and cu.get("sni") == "dns.example.org", (st, cu))
cf = byid.get("cloudflare") or {}
check("каталог из 5 полей (старый мост): custom:false, proto dot",
      cf.get("custom") is False and cf.get("proto") == "dot" and cf.get("addr") == "", cf)
check("custom_max читается из lib/providers.sh (PROV_CUSTOM_MAX=8)",
      d.get("custom_max") == 8, d.get("custom_max"))
srv.bridge_data = _data_real
srv.bridge = _bridge_real
srv._CATALOG = []
srv._CATALOG_AT = 0.0

httpd.shutdown()

if failures:
    print("ИТОГ: %d провал(ов): %s" % (len(failures), ", ".join(failures)))
    sys.exit(1)
sys.exit(0)
PY

# ═══════════════════════════════════════════════════════════════════════
# ЧАСТЬ 2 — lib/webui.sh: чистая bash-логика на фикстурах
# ═══════════════════════════════════════════════════════════════════════
echo ""
echo "== 2. lib/webui.sh: порты, токен, юнит, адрес =="

export GODNS_ETC="$WORK/etc"
export GODNS_DIR="$WORK/opt"
export GODNS_CONFIG="$WORK/opt/config.json"
export GODNS_WEBUI_DIR="$WORK/web"
export GODNS_WEBUI_TOKEN_DIR="$WORK/etc/webui"
export GODNS_WEBUI_TOKEN_FILE="$WORK/etc/webui/token"
export GODNS_WEBUI_UNIT_DIR="$WORK/systemd"
export XUI_DB_PATH="$WORK/xui.db"

# t/tf до source: webui.sh сам их не определяет (это делает i18n.sh,
# которому нужен bash 4 — а тесты обязаны ходить и на macOS/bash 3.2).
t()  { printf '%s' "$1"; }
tf() { local k="$1"; shift; printf '%s %s' "$k" "$*"; }

# ── Заглушки СИСТЕМНЫХ операций ────────────────────────────────────────
# ГРАБЛИ, стоившие живого бота: песочница выше закрывает ПУТИ, но не
# действия над системой. godns_bot_remove/webui_remove зовут настоящие
# `systemctl disable --now` и `userdel`. Прогон набора на РАБОЧЕМ сервере
# остановил работающего бота и удалил его пользователя — в auth.log
# осталось `userdel 'godns-bot'`, а юнит после этого падал с 217/USER.
#
# Заглушки обязаны стоять ДО source библиотек. Не глушим молча, а ПИШЕМ
# вызовы: тест продолжает проверять, что снятие пыталось отключить юниты,
# — иначе заглушка спрятала бы и настоящую поломку снятия.
SYS_CALLS="${WORK}/syscalls.log"
: > "$SYS_CALLS"
systemctl() { printf 'systemctl %s\n' "$*" >> "$SYS_CALLS"; return 0; }
userdel()   { printf 'userdel %s\n'   "$*" >> "$SYS_CALLS"; return 0; }
groupdel()  { printf 'groupdel %s\n'  "$*" >> "$SYS_CALLS"; return 0; }
deluser()   { printf 'deluser %s\n'   "$*" >> "$SYS_CALLS"; return 0; }
useradd()   { printf 'useradd %s\n'   "$*" >> "$SYS_CALLS"; return 0; }
adduser()   { printf 'adduser %s\n'   "$*" >> "$SYS_CALLS"; return 0; }
runuser()   { printf 'runuser %s\n'   "$*" >> "$SYS_CALLS"; return 0; }
ufw()       { printf 'ufw %s\n'       "$*" >> "$SYS_CALLS"; return 0; }

. "$REPO/lib/common.sh"
. "$REPO/lib/webui.sh"

# фикстура базы 3X-UI: случайный порт панели соседа
python3 - "$XUI_DB_PATH" <<'PY'
import sqlite3, sys
con = sqlite3.connect(sys.argv[1])
con.execute("create table settings (key text, value text)")
con.execute("insert into settings values ('webPort', '34567')")
con.execute("insert into settings values ('subPort', '2096')")
con.commit()
PY

if _webui_port_reserved 443; then ok "443 зарезервирован соседями всегда"; else bad "443 должен быть зарезервирован"; fi
if _webui_port_reserved 853; then ok "853 (наш DoT) зарезервирован"; else bad "853 должен быть зарезервирован"; fi
if _webui_port_reserved 4443; then ok "порт DoH из config.json зарезервирован"; else bad "4443 из конфига должен быть зарезервирован"; fi
if _webui_port_reserved 34567; then ok "случайный порт 3X-UI из x-ui.db зарезервирован (только чтение)"; else bad "34567 из x-ui.db должен быть зарезервирован"; fi
if _webui_port_reserved 2096; then ok "subPort 3X-UI из x-ui.db зарезервирован"; else bad "2096 из x-ui.db должен быть зарезервирован"; fi
if _webui_port_reserved 9445; then bad "9445 не должен быть зарезервирован"; else ok "свободный кандидат 9445 не считается занятым"; fi

p=$(webui_pick_port) || p=""
if [ -n "$p" ] && ! _webui_port_reserved "$p"; then ok "webui_pick_port выдал незарезервированный порт ($p)"; else bad "webui_pick_port: [$p]"; fi

# webui_url: собирается только при полном комплекте
config_set webui_port "9445"
config_set webui_path ""
u=$(webui_url) || u=""
if [ -z "$u" ]; then ok "webui_url пуст без webui_path"; else bad "webui_url без пути должен быть пуст: [$u]"; fi
config_set webui_path "PaNeLsEcReT16ch"
u=$(webui_url) || u=""
if [ "$u" = "https://dns.example.org:9445/PaNeLsEcReT16ch/" ]; then ok "webui_url собирает адрес с секретным путём"; else bad "webui_url: [$u]"; fi

# токен: 0600, стабилен между вызовами, не пустой
if webui_ensure_token; then ok "webui_ensure_token создал токен"; else bad "webui_ensure_token"; fi
tok1=$(cat "$GODNS_WEBUI_TOKEN_FILE")
perm=$(python3 -c "import os,sys;print(oct(os.stat(sys.argv[1]).st_mode & 0o777))" "$GODNS_WEBUI_TOKEN_FILE")
if [ "$perm" = "0o600" ]; then ok "токен лежит 0600"; else bad "права токена: $perm"; fi
webui_ensure_token || true
tok2=$(cat "$GODNS_WEBUI_TOKEN_FILE")
if [ -n "$tok1" ] && [ "$tok1" = "$tok2" ]; then ok "повторный вызов не перегенерирует токен"; else bad "токен изменился между вызовами"; fi
# Токен мог создать и server.py (token_urlsafe: есть «-» и «_»), и bash
# (random_path: A-Za-z0-9). Инвариант один: символы из TOKEN_RE сервера —
# иначе ?token=… искалечится в адресной строке.
case "$tok1" in *[!A-Za-z0-9_.~=-]*) bad "в токене посторонние символы"; ;; *) ok "токен URL-безопасен";; esac
if [ "${#tok1}" -ge 32 ]; then ok "токен достаточно длинный (${#tok1})"; else bad "токен короткий: ${#tok1}"; fi

# юнит: имя строго godns-, ExecStart указывает в наш каталог
if _webui_write_unit "/opt/prog"; then ok "_webui_write_unit записал юнит"; else bad "_webui_write_unit"; fi
unit="$WORK/systemd/godns-web.service"
if [ -s "$unit" ]; then ok "юнит на месте: godns-web.service (префикс godns-)"; else bad "нет юнита $unit"; fi
if grep -q "ExecStart=/usr/bin/python3 $WORK/web/server.py" "$unit"; then ok "ExecStart смотрит в наш каталог"; else bad "ExecStart в юните"; fi
if grep -q "GODNS_PROGRAM_DIR=/opt/prog" "$unit"; then ok "юнит несёт GODNS_PROGRAM_DIR для моста"; else bad "нет GODNS_PROGRAM_DIR в юните"; fi
if grep -qE "govless|gotelegram|x-ui|telemt|gocdn" "$unit"; then bad "в юните чужие имена"; else ok "в юните нет чужих имён"; fi

# webui_set_* — проверка аргументов (без установленного движка это
# чистая валидация: engine_is_installed=false ⇒ webui_apply — no-op)
engine_is_installed() { return 1; }
if webui_set_ports "0" "853" >/dev/null 2>&1; then bad "порт 0 не должен приниматься"; else ok "порт 0 отвергается"; fi
if webui_set_ports "70000" "853" >/dev/null 2>&1; then bad "порт 70000 не должен приниматься"; else ok "порт 70000 отвергается"; fi
if webui_set_ports "4443" "4443" >/dev/null 2>&1; then bad "совпадающие порты не должны приниматься"; else ok "совпадающие DoH/DoT отвергаются"; fi
if webui_set_dot "maybe" >/dev/null 2>&1; then bad "set_dot maybe не должен приниматься"; else ok "set_dot принимает только yes|no"; fi

# ГРАБЛИ: здесь стояла заглушка `prov_exists` с выдуманным «quad9», и
# пока функция только проверяла аргументы, этого хватало. Но панель обязана
# ПЕРЕСОБИРАТЬ канонические записи `id|proto|host|port|sni|path` — движок
# рендерит только из них. Пока она этого не делала, смена набора из панели
# не доезжала до dnsdist вовсе, а панель рапортовала «применено». Заглушка
# такую подмену пропускала: настоящих записей ей строить было не из чего.
# Поэтому теперь берём НАСТОЯЩИЕ идентификаторы каталога.
# Настоящий каталог сюда не тянем: lib/providers.sh требует bash 4.0+, а
# эти тесты обязаны идти на macOS без контейнера. Сборку записей проверяет
# test_engine.sh; здесь проверяется КОНТРАКТ панели — что она пересобирает
# записи, а не пишет один список.
# lib/engine.sh грузится и на bash 3.2 (свой guard он держит внутри), а
# engine_upstreams_store — та самая общая сборка, которую панель обязана
# звать вместо голого config_set upstream_ids.
. "$REPO/lib/engine.sh"
prov_exists() { case "$1" in cloudflare|quad9-unfiltered) return 0;; *) return 1;; esac; }
engine_upstream_record() {
    case "$1" in
        cloudflare)        printf 'cloudflare|dot|1.1.1.1|853|one.one.one.one|' ;;
        quad9-unfiltered)  printf 'quad9-unfiltered|dot|9.9.9.10|853|dns10.quad9.net|' ;;
        *) return 1 ;;
    esac
}
if webui_set_upstreams "cloudflare,quad9-unfiltered" "roundrobin" >/dev/null 2>&1; then ok "валидные апстримы принимаются"; else bad "валидные апстримы"; fi
if [ "$(config_get upstream_ids "")" = "cloudflare,quad9-unfiltered" ]; then ok "upstream_ids записаны"; else bad "upstream_ids: $(config_get upstream_ids "")"; fi
_recs=$(config_get upstreams "" | tr ';' '\n' | grep -c . || true)
if [ "${_recs:-0}" -eq 2 ]; then ok "канонические записи пересобраны панелью"; else bad "записей: ${_recs:-0} (ждали 2) — панель не пересобрала набор"; fi
if config_get upstreams "" | grep -q '|dot|'; then ok "записи в каноническом формате id|proto|host|port|sni|path"; else bad "формат записей не канонический"; fi
if webui_set_upstreams "cloudflare,evil" "roundrobin" >/dev/null 2>&1; then bad "неизвестный апстрим не должен приниматься"; else ok "неизвестный апстрим отвергается"; fi
if webui_set_upstreams "cloudflare" "wrr" >/dev/null 2>&1; then bad "политика wrr не должна приниматься"; else ok "чужая политика отвергается"; fi

# ═══════════════════════════════════════════════════════════════════════
# ЧАСТЬ 3 — синтаксис и компиляция всего нового
# ═══════════════════════════════════════════════════════════════════════
echo ""
echo "== 3. синтаксис =="
if bash -n "$REPO/lib/webui.sh" && bash -n "$REPO/godns-web/bridge.sh"; then ok "bash -n webui.sh, bridge.sh"; else bad "bash -n"; fi
if python3 -m py_compile "$REPO/godns-web/server.py"; then ok "py_compile server.py"; else bad "py_compile server.py"; fi
# node на цели не обязателен: без него синтаксис JS держит только браузер.
if command -v node >/dev/null 2>&1; then
    if node --check "$REPO/godns-web/static/sections/upstreams.js" 2>/dev/null; then ok "node --check upstreams.js"; else bad "node --check upstreams.js"; fi
fi

# мост: лёгкий путь (drifted) работает без bash4 и без движка
export GODNS_PROGRAM_DIR="$REPO"
config_set applied_fingerprint "" || true
if bash "$REPO/godns-web/bridge.sh" drifted >/dev/null 2>&1; then
    bad "drifted: без штампа должен быть rc 1 (нечему дрейфовать)"
else
    ok "мост: drifted без штампа — rc 1"
fi

# ═══════════════════════════════════════════════════════════════════════
echo
echo "── смена портов закрывает ушедший порт в ufw"
# ГРАБЛИ: панель открывала новый порт и НЕ закрывала прежний — он
# оставался разрешённым в межсетевом экране навсегда. В SSH-пути такое
# закрытие есть (doh_port_prev/dot_port_prev), в панели не было. Порт без
# слушателя сам по себе безобиден, но завтра его займёт что-то другое и
# окажется открытым наружу без чьего-либо решения.
: > "$SYS_CALLS"
mkdir -p "$WORK/ufwetc"
printf 'ENABLED=yes\n' > "$WORK/ufwetc/ufw.conf"
config_set doh_port 4443 >/dev/null; config_set dot_port 853 >/dev/null
engine_is_installed() { return 0; }
engine_apply() { return 0; }
certs_grant_read_access() { return 0; }
certs_setup_renewal_reaction() { return 0; }
godns_mark_applied() { return 0; }
# Помощники читают /etc/ufw/ufw.conf; подменяем сам факт «ufw включён»
# через заглушку grep-условия — проще подменить сами функции проверки.
godns_fw_open()  { printf 'fw_open %s\n'  "$1" >> "$SYS_CALLS"; return 0; }
godns_fw_close() { printf 'fw_close %s\n' "$1" >> "$SYS_CALLS"; return 0; }
# ГРАБЛИ: занятость порта проверялась НАСТОЯЩИМ ss по живой машине. На
# сервере с goTelegram 8443 держит его nginx, webui_set_ports возвращала
# 1, и под set -euo pipefail файл умирал ПРЯМО ЗДЕСЬ — без единой строки
# FAIL и без итогового подвала. Пять проверок ниже просто не исполнялись,
# а на машине, где 8443 случайно свободен, набор был зелёным. Тест обязан
# проверять СВОЮ логику, а не то, что сейчас слушает эта машина.
port_in_use() { return 1; }
port_owner()  { printf ''; return 0; }
webui_set_ports "8443" "8853" >/dev/null 2>&1 || true
if grep -q '^fw_close 4443$' "$SYS_CALLS" && grep -q '^fw_close 853$' "$SYS_CALLS"; then
    ok "прежние порты 4443 и 853 закрыты после применения"
else
    bad "прежние порты не закрыты: $(tr '\n' ' ' < "$SYS_CALLS")"
fi
if grep -q '^fw_open 8443$' "$SYS_CALLS"; then
    ok "новый порт открыт"
else
    bad "новый порт не открыт"
fi
# Порядок важен: закрыть раньше открытия — оставить оператора без доступа.
if [ "$(grep -n '^fw_open 8443$' "$SYS_CALLS" | cut -d: -f1)" -lt \
     "$(grep -n '^fw_close 4443$' "$SYS_CALLS" | cut -d: -f1)" ]; then
    ok "новый порт открыт РАНЬШЕ закрытия прежнего"
else
    bad "прежний порт закрыт до открытия нового"
fi

echo
echo "── свой апстрим: webui_custom_probe / _add / _remove / _list_tsv"
# lib/providers.sh требует bash 4 — сюда его не тянем (см. выше): каталог и
# запись заглушены, проверяется КОНТРАКТ функций lib/webui.sh — порядок
# шагов, коды возврата, что и когда зовётся. Заглушки ПИШУТ вызовы в
# $SYS_CALLS: «не звался» доказывается отсутствием строки, а не молчанием.
PROBE_RC=0
bench_probe_line() { printf 'bench_probe_line %s\n' "$1" >> "$SYS_CALLS"; printf '  12 мс (хол. 41)'; return "$PROBE_RC"; }
godns_resolve_host() {
    printf 'resolve %s\n' "$1" >> "$SYS_CALLS"
    case "$1" in one.one.one.one) printf '1.1.1.1\n2606:4700:4700::1111\n' ;; *) return 1 ;; esac
}
_prov_valid_path() { [[ "${1:-}" =~ ^/[A-Za-z0-9._~%/-]{0,127}$ ]]; }
PROV_CUSTOM_LAST_ID=""
prov_custom_add() {
    printf 'prov_custom_add %s|%s|%s|%s|%s|%s\n' "$1" "$2" "$3" "$4" "$5" "$6" >> "$SYS_CALLS"
    PROV_CUSTOM_LAST_ID="custom-stub"; printf 'custom-stub'; return 0
}
prov_custom_remove() { printf 'prov_custom_remove %s\n' "$1" >> "$SYS_CALLS"; [ "$1" = "custom-stub" ]; }
prov_custom_list() { printf 'custom-stub\n'; }
prov_exists() { case "$1" in cloudflare|quad9-unfiltered|custom-stub) return 0;; *) return 1;; esac; }
prov_name() { case "$1" in custom-stub) printf 'Мой DoT' ;; *) printf '%s' "$1" ;; esac; }
prov_field() {
    case "$1:$2" in
        custom-stub:custom)                       printf 'yes' ;;
        custom-stub:proto)                        printf 'dot' ;;
        custom-stub:dot_host|cloudflare:dot_host) printf 'one.one.one.one' ;;
        custom-stub:dot_port|cloudflare:dot_port) printf '853' ;;
        custom-stub:ipv4|cloudflare:ipv4)         printf '1.1.1.1' ;;
        cloudflare:filter)                        printf 'none' ;;
        *) printf '' ;;
    esac
    return 0
}
prov_bootstrap_first() { case "${2:-v4}" in v6) prov_field "$1" ipv6 ;; *) prov_field "$1" ipv4 ;; esac; }
prov_filter_text() { printf 'нет'; }
prov_region_text() { case "$1" in custom-stub) printf 'свой' ;; *) printf 'глобальный anycast' ;; esac; }
prov_menu_ids() { printf 'cloudflare\ncustom-stub\n'; }
engine_upstreams_store() { printf 'ups_store %s\n' "$1" >> "$SYS_CALLS"; printf ''; return 0; }
webui_apply() { printf 'webui_apply\n' >> "$SYS_CALLS"; return 0; }
TAB=$(printf '\t')
has_kv() { printf '%s\n' "$1" | grep -q "^${2}${TAB}${3}\$"; }
called() { grep -q "^$1" "$SYS_CALLS"; }

# отказы на границе — до единого пакета
: > "$SYS_CALLS"
if webui_custom_probe quic one.one.one.one "" "" no >/dev/null 2>&1; then bad "проба: протокол quic принят"; else ok "проба: неизвестный протокол — rc 1"; fi
if webui_custom_probe dot one.one.one.one 70000 "" no >/dev/null 2>&1; then bad "проба: порт 70000 принят"; else ok "проба: порт 70000 — rc 1"; fi
if webui_custom_probe dot 1.1.1.1 853 "" no >/dev/null 2>&1; then bad "проба: IP в SNI принят"; else ok "проба: IP вместо имени в SNI (dot) — rc 1"; fi
if webui_custom_probe dot nope.invalid 853 "" no >/dev/null 2>&1; then bad "проба: неразрешённое имя принято"; else ok "проба: имя не разрешилось — rc 1"; fi
if webui_custom_probe doh one.one.one.one "" "/{id}" no >/dev/null 2>&1; then bad "проба: путь с {id} принят"; else ok "проба: путь DoH с фигурными скобками — rc 1"; fi
if webui_custom_probe dot "a b" 853 "" no >/dev/null 2>&1; then bad "проба: хост с пробелом принят"; else ok "проба: хост с пробелом — rc 1"; fi
if called bench_probe_line; then bad "проба: отказ на границе, а bench_probe_line звался"; else ok "проба: ни один отказ не дошёл до bench_probe_line"; fi

# открытый DNS к публичному адресу: цена названа ДО пробы
: > "$SYS_CALLS"
out=$(webui_custom_probe plain 8.8.8.8 53 "" no 2>/dev/null) && rc=0 || rc=$?
if [ "$rc" -eq 4 ] && has_kv "$out" needs_ack yes && has_kv "$out" private no; then ok "проба: plain к публичному без ack — rc 4, needs_ack=yes"; else bad "проба plain/public без ack: rc=$rc out=$(printf '%s' "$out" | tr '\n\t' ';=')"; fi
if called bench_probe_line; then bad "проба: plain к публичному без ack — пакет УШЁЛ"; else ok "проба: без ack ни один пакет не ушёл"; fi
: > "$SYS_CALLS"
out=$(webui_custom_probe plain 8.8.8.8 53 "" yes 2>/dev/null) && rc=0 || rc=$?
if [ "$rc" -eq 0 ] && has_kv "$out" ok yes && has_kv "$out" private no && called 'bench_probe_line probe|plain|8.8.8.8|53||8.8.8.8$'; then ok "проба: plain к публичному с ack — идёт строкой SSH-экрана"; else bad "проба plain/public с ack: rc=$rc calls=$(tr '\n' ' ' < "$SYS_CALLS")"; fi
: > "$SYS_CALLS"
out=$(webui_custom_probe plain 10.0.0.53 "" "" no 2>/dev/null) && rc=0 || rc=$?
if [ "$rc" -eq 0 ] && has_kv "$out" private yes && has_kv "$out" port 53 && called bench_probe_line; then ok "проба: plain к приватному — без ack, порт 53 по умолчанию"; else bad "проба plain/private: rc=$rc out=$(printf '%s' "$out" | tr '\n\t' ';=')"; fi

# не ответил — rc 3: результат, а не отказ
PROBE_RC=1
out=$(webui_custom_probe dot one.one.one.one "" "" no 2>/dev/null) && rc=0 || rc=$?
if [ "$rc" -eq 3 ] && has_kv "$out" ok no; then ok "проба: не ответил — rc 3 и ok=no"; else bad "проба rc3: rc=$rc"; fi
PROBE_RC=0
: > "$SYS_CALLS"
out=$(webui_custom_probe doh one.one.one.one "" "" no 2>/dev/null) && rc=0 || rc=$?
if [ "$rc" -eq 0 ] && has_kv "$out" ok yes && has_kv "$out" ip 1.1.1.1 && has_kv "$out" sni one.one.one.one && has_kv "$out" port 443 && has_kv "$out" latency "12 мс (хол. 41)"; then ok "проба: doh — rc 0, ip/sni/port/latency в TSV, порт 443 по умолчанию"; else bad "проба doh: rc=$rc out=$(printf '%s' "$out" | tr '\n\t' ';=')"; fi
if called 'bench_probe_line probe|doh|one.one.one.one||https://one.one.one.one/dns-query|1.1.1.1$'; then ok "проба: doh без пути — /dns-query, URL без :443"; else bad "строка пробы doh: $(grep bench_probe_line "$SYS_CALLS")"; fi
: > "$SYS_CALLS"
out=$(webui_custom_probe doh one.one.one.one 8443 "secret" no 2>/dev/null) && rc=0 || rc=$?
if called 'bench_probe_line probe|doh|one.one.one.one||https://one.one.one.one:8443/secret|1.1.1.1$'; then ok "проба: doh с портом и путём без «/» — порт в URL, «/» добавлен"; else bad "строка пробы doh:8443: $(grep bench_probe_line "$SYS_CALLS")"; fi
if printf '%s' "$out" | grep -q secret; then bad "проба: путь DoH напечатан в TSV"; else ok "проба: путь DoH в машинный вывод не попадает"; fi

# запись: порядок и коды
: > "$SYS_CALLS"
out=$(webui_custom_add dot one.one.one.one 853 "" "Имя" no no 2>/dev/null) && rc=0 || rc=$?
if [ "$rc" -eq 0 ] && has_kv "$out" id custom-stub; then ok "add: rc 0, stdout id<TAB>custom-stub"; else bad "add: rc=$rc out=$out"; fi
if called 'prov_custom_add dot|1.1.1.1|853|one.one.one.one||Имя$'; then ok "add: prov_custom_add получил proto ip port sni path name"; else bad "add: $(grep prov_custom_add "$SYS_CALLS")"; fi
if called ups_store || called webui_apply; then bad "add без включения: набор тронут"; else ok "add без включения: engine_upstreams_store и webui_apply не звались"; fi
config_set upstream_ids "cloudflare" >/dev/null
: > "$SYS_CALLS"
out=$(webui_custom_add dot one.one.one.one 853 "" "Имя" yes no 2>/dev/null) && rc=0 || rc=$?
if [ "$rc" -eq 0 ] && called 'ups_store cloudflare,custom-stub$' && called webui_apply; then ok "add с включением: набор = прежний + новый, webui_apply вызван"; else bad "add enable=yes: rc=$rc calls=$(tr '\n' ' ' < "$SYS_CALLS")"; fi
: > "$SYS_CALLS"
if webui_custom_add plain 8.8.8.8 53 "" "" yes no >/dev/null 2>&1; then bad "add: plain к публичному без ack принят"; else ok "add: plain к публичному без ack — rc 1"; fi
if called prov_custom_add; then bad "add: без ack запись всё же сделана"; else ok "add: без ack записи нет"; fi
: > "$SYS_CALLS"
if webui_custom_add plain 8.8.8.8 53 "" "" no yes >/dev/null 2>&1 && called 'prov_custom_add plain|8.8.8.8|53|||$'; then ok "add: plain к публичному с ack — записан без sni и пути"; else bad "add plain с ack: $(grep prov_custom_add "$SYS_CALLS")"; fi
: > "$SYS_CALLS"
if webui_custom_add plain 10.0.0.53 "" "" "" no no >/dev/null 2>&1 && called 'prov_custom_add plain|10.0.0.53|53|||$'; then ok "add: plain к приватному — без ack, порт 53"; else bad "add plain private: $(grep prov_custom_add "$SYS_CALLS")"; fi
: > "$SYS_CALLS"
webui_custom_add doh one.one.one.one "" "/secret" "" no no >/dev/null 2>&1 || true
if called 'prov_custom_add doh|1.1.1.1|443|one.one.one.one|/secret|$'; then ok "add: doh — путь доехал до prov_custom_add параметром"; else bad "add doh: $(grep prov_custom_add "$SYS_CALLS")"; fi

# удаление: единственный не снимать, не из набора — не применять
: > "$SYS_CALLS"
if webui_custom_remove custom-nope >/dev/null 2>&1; then bad "remove: неизвестный id принят"; else ok "remove: неизвестный id — rc 1"; fi
if webui_custom_remove cloudflare >/dev/null 2>&1; then bad "remove: запись каталога принята"; else ok "remove: запись каталога (не своя) — rc 1"; fi
if called prov_custom_remove; then bad "remove: отказ, а prov_custom_remove звался"; else ok "remove: на отказе записи нет"; fi
config_set upstream_ids "custom-stub" >/dev/null
: > "$SYS_CALLS"
err=$(webui_custom_remove custom-stub 2>&1 >/dev/null) && rc=0 || rc=$?
if [ "$rc" -eq 1 ] && printf '%s' "$err" | grep -q ups_custom_last_one; then ok "remove: единственный в наборе — rc 1 с ups_custom_last_one"; else bad "remove last: rc=$rc err=$err"; fi
if called prov_custom_remove; then bad "remove: единственный в наборе, а запись снята"; else ok "remove: правило «единственный» — ДО любой записи"; fi
config_set upstream_ids "cloudflare,custom-stub" >/dev/null
: > "$SYS_CALLS"
if webui_custom_remove custom-stub >/dev/null 2>&1 && called 'prov_custom_remove custom-stub' && called 'ups_store cloudflare$' && called webui_apply; then ok "remove: в наборе — снят, набор пересобран без него, webui_apply вызван"; else bad "remove in-set: $(tr '\n' ' ' < "$SYS_CALLS")"; fi
config_set upstream_ids "cloudflare" >/dev/null
: > "$SYS_CALLS"
if webui_custom_remove custom-stub >/dev/null 2>&1 && called 'prov_custom_remove custom-stub' && ! called ups_store && ! called webui_apply; then ok "remove: не в наборе — снят, службу не трогаем"; else bad "remove not-in-set: $(tr '\n' ' ' < "$SYS_CALLS")"; fi

# список своих и каталог для панели
config_set upstream_ids "cloudflare,custom-stub" >/dev/null
out=$(webui_custom_list_tsv)
first=$(printf '%s\n' "$out" | sed -n 1p)
if [ "$first" = "set_count${TAB}2" ]; then ok "list_tsv: первой строкой set_count<TAB>2"; else bad "list_tsv первая строка: $first"; fi
if printf '%s\n' "$out" | grep -q "^custom-stub${TAB}Мой DoT${TAB}dot${TAB}1.1.1.1:853${TAB}one.one.one.one${TAB}yes\$"; then ok "list_tsv: id, name, proto, addr, sni, in_set=yes"; else bad "list_tsv: $(printf '%s' "$out" | tr '\n\t' ';=')"; fi
config_set upstream_ids "cloudflare" >/dev/null
if webui_custom_list_tsv | grep -q "${TAB}no\$"; then ok "list_tsv: не в наборе — in_set=no"; else bad "list_tsv in_set=no"; fi
out=$(webui_catalog_tsv)
badnf=$(printf '%s\n' "$out" | awk -F'\t' 'NF != 9 { n++ } END { print n + 0 }')
nrows=$(printf '%s\n' "$out" | grep -c . || true)
if [ "$badnf" -eq 0 ] && [ "$nrows" -eq 2 ]; then ok "catalog_tsv: 9 полей в каждой из $nrows строк"; else bad "catalog_tsv: строк $nrows, не по 9 полей: $badnf"; fi
if printf '%s\n' "$out" | grep -q "^custom-stub${TAB}.*${TAB}свой${TAB}yes${TAB}dot${TAB}1.1.1.1:853${TAB}one.one.one.one\$"; then ok "catalog_tsv: своя запись — custom=yes, proto, addr, sni, регион переводом"; else bad "catalog_tsv custom row: $(printf '%s\n' "$out" | grep custom-stub | tr '\t' '=')"; fi
if printf '%s\n' "$out" | grep -q "^cloudflare${TAB}.*${TAB}no${TAB}dot${TAB}1.1.1.1:853${TAB}one.one.one.one\$"; then ok "catalog_tsv: запись каталога — custom=no, proto=dot"; else bad "catalog_tsv catalog row: $(printf '%s\n' "$out" | grep cloudflare | tr '\t' '=')"; fi

echo
echo "── путь DoH не ездит в argv: мост панели и фасад бота"
# /proc/<pid>/cmdline читает любой локальный пользователь, а путь DoH чужого
# резолвера может быть чужим секретом. Четвёртый параметр lib-функции обязан
# приходить ТОЛЬКО из окружения GODNS_CUSTOM_PATH. Счётчик осмотренных файлов
# обязателен: обе границы должны найтись, иначе проверка молчала бы о
# ненаписанной. Пока фасад бота этих вызовов не содержит — это честный FAIL.
CU_SEEN=0; CU_BAD=""
for f in "$REPO/godns-web/bridge.sh" "$REPO/godns-bot/actions.sh"; do
    [ -f "$f" ] || continue
    hits=$(grep -nE 'webui_custom_(probe|add)[[:space:]]' "$f" | grep -vE '^[0-9]+:[[:space:]]*#' || true)
    [ -n "$hits" ] || continue
    CU_SEEN=$((CU_SEEN + 1))
    while IFS= read -r hit; do
        [ -n "$hit" ] || continue
        fourth=$(printf '%s\n' "$hit" | sed -E 's/.*webui_custom_(probe|add)[[:space:]]+//' | awk '{print $4}')
        if [ "$fourth" != '"${GODNS_CUSTOM_PATH:-}"' ]; then
            CU_BAD="${CU_BAD}${f##*/}:${hit%%:*} → ${fourth}; "
        fi
    done <<EOF
$hits
EOF
done
if [ "$CU_SEEN" -lt 2 ]; then
    bad "путь DoH: осмотрено файлов ${CU_SEEN} из 2 — bridge.sh и actions.sh обязаны звать webui_custom_probe/add"
elif [ -n "$CU_BAD" ]; then
    bad "путь DoH: четвёртый параметр lib-функции не GODNS_CUSTOM_PATH: ${CU_BAD}"
else
    ok "путь DoH приходит в lib только из GODNS_CUSTOM_PATH (bridge.sh, actions.sh)"
fi

echo
echo "── строки панели: ключи, слова подтверждений, ширина колонок"
# Пропущенный ключ показывается ПОЛЬЗОВАТЕЛЮ: t() возвращает сам ключ, и
# человек видит `ui_bk_col_size` вместо «размер» — ровно та беда, из-за
# которой в SSH-меню однажды печаталось `certs_unusable_pick`.
if python3 "$REPO/tools/check-webui-strings.py" >/dev/null 2>&1; then
    ok "строки панели сходятся (ключи, ru/en, слова подтверждений)"
else
    bad "строки панели: $(python3 "$REPO/tools/check-webui-strings.py" 2>&1 | tail -3 | tr '\n' ' ')"
fi

echo
echo "── server.py — строгий разбор булева в теле запроса"
# ═══════════════════════════════════════════════════════════════════════
# ГРАБЛИ: было `bool(body.get("enabled"))`, а в Python НЕПУСТАЯ СТРОКА
# истинна — то есть {"enabled": "no"} означало ДА. Клиент, пишущий скрипт
# против API, выключал бы DoT и молча его ВКЛЮЧАЛ, получая бодрое 200.
# Своя страница шлёт настоящий булев и не страдала; API обязан быть
# однозначным для всех. Непонятное значение — отказ, а не догадка.
FLAG_OUT=$(python3 - "$REPO/godns-web/server.py" <<'PYEOF'
import ast, sys
from typing import Any

src = open(sys.argv[1]).read()
ns = {"Any": Any}
found = False
# `from __future__ import annotations` — чтобы аннотация `bool | None` не
# вычислялась при определении: server.py рассчитан на python 3.10+, а
# тесты обязаны идти и на машине разработчика с более старым.
future = ast.ImportFrom(module="__future__",
                        names=[ast.alias(name="annotations", asname=None)], level=0)
for n in ast.parse(src).body:
    if isinstance(n, ast.FunctionDef) and n.name == "parse_flag":
        mod = ast.Module([future, n], [])
        ast.fix_missing_locations(mod)
        exec(compile(mod, "<s>", "exec"), ns)
        found = True
if not found:
    print("MISSING"); raise SystemExit
f = ns["parse_flag"]
cases = [(True, True), (False, False), ("yes", True), ("no", False),
         ("true", True), ("false", False), ("on", True), ("off", False),
         ("1", True), ("0", False), (1, True), (0, False),
         ("YES", True), (" No ", False),
         ("может быть", None), ("", None), (None, None), ([], None), ({}, None)]
bad = ["%r→%r(ждали %r)" % (v, f(v), w) for v, w in cases if f(v) != w]
print("OK" if not bad else "BAD " + " ".join(bad))
PYEOF
) || FLAG_OUT="BAD запуск не удался"
case "$FLAG_OUT" in
    OK)      ok "булев разбирается строго (19 случаев, «no» — это НЕТ)" ;;
    MISSING) bad "parse_flag не найдена в server.py" ;;
    *)       bad "разбор булева: ${FLAG_OUT#BAD }" ;;
esac

echo ""
if [ "$fail" -eq 0 ]; then
    echo "  ✓ test_webui.sh: всё зелёное"
    exit 0
fi
echo "  ✗ test_webui.sh: провалов: $fail"
exit 1
