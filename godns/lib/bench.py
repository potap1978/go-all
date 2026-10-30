#!/usr/bin/env python3
# goDNS — Copyright (c) 2026 potap1978. All rights reserved.
"""
goDNS — замер реальной задержки от сервера до зашифрованных upstream-резолверов.

Зачем не ICMP: ping идёт другим путём, другим протоколом и на другой порт;
половина публичных DoH/DoT-эндпоинтов ICMP просто дропает, а у аникастовых
провайдеров ближайший к ICMP узел может быть не тем, который ответит на 853/443.
Меряем ровно тот транспорт, которым потом будет ходить dnsdist.

Как:
  • DoT — чистый stdlib (socket + ssl), RFC 7858: каждое сообщение с 2-байтовым
    префиксом длины. Сертификат проверяется по-настоящему (chain + hostname):
    в блокируемой сети падение проверки — это САМЫЙ ценный диагностический
    сигнал, его нельзя глушить verify=off.
  • DoH — делегируем `curl --http2` как подпроцессу. http.client из stdlib умеет
    только HTTP/1.1, и это проверено нерабочим: Quad9 отвечает на HTTP/1.1 DoH
    кодом 505, Mullvad просто рвёт соединение после TLS.

Что печатаем: ДВА числа.
  cold_ms — TCP + TLS (+ преамбула HTTP/2) + первый ответ; цена первого запроса.
  warm_ms — МЕДИАНА проб 2..N по ТОМУ ЖЕ соединению; установившийся режим,
            в котором живёт демон. Меню сортируется по warm.
Показать только cold — и все провайдеры выглядят впятеро медленнее, чем есть.

Медиана, не среднее: один таймаут в 4000 мс сдвигает среднее из пяти проб
на +780 мс. Неудачные пробы выбрасываются ДО взятия медианы. Если уцелела
одна тёплая проба — печатаем её и ставим degraded=true, а не подделываем медиану.

Имя запроса одно и то же для всех проб (по умолчанию example.com): проба 2+
гарантированно попадает в кэш резолвера, и warm_ms — чистый сетевой RTT,
а не стоимость рекурсии.

Вход:  JSON-список кандидатов в argv[1] или на stdin.
   [{"id":"quad9","proto":"dot","host":"dns.quad9.net","ip":"9.9.9.9","port":853},
    {"id":"cf","proto":"doh","url":"https://cloudflare-dns.com/dns-query","ip":"1.1.1.1"}]
Выход: JSON в stdout. В stdout НИЧЕГО больше — bash его захватывает; вся
       диагностика идёт в stderr.

Python 3.10+ (Ubuntu 22.04), только stdlib + внешний curl.
"""

import base64
import json
import os
import shutil
import socket
import ssl
import struct
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor


# ── Настройки (перекрываются окружением из bench.sh) ────────────────────
def _env_int(name, default, lo, hi):
    try:
        v = int(os.environ.get(name, "") or default)
    except ValueError:
        return default
    return max(lo, min(hi, v))


def _env_float(name, default, lo, hi):
    try:
        v = float(os.environ.get(name, "") or default)
    except ValueError:
        return default
    return max(lo, min(hi, v))


PROBES = _env_int("GODNS_PROBES", 5, 2, 25)
TIMEOUT = _env_float("GODNS_TIMEOUT", 4.0, 1.0, 60.0)
PARALLEL = _env_int("GODNS_PARALLEL", 16, 1, 64)

# Потолок на ОДНОГО кандидата по стенным часам.
#
# Зачем отдельно от TIMEOUT: TIMEOUT ограничивает одну операцию, а нас топит
# сумма. У curl --connect-timeout покрывает только TCP-connect; хост, который
# принял соединение и завис в TLS-рукопожатии, съедал весь --max-time. Замер
# 13 кандидатов упирался в мёртвый dns0.eu и занимал 20 с вместо 2 с.
#
# Все пробы идут по ОДНОМУ соединению, поэтому честная стоимость — это
# рукопожатие (≤ TIMEOUT) плюс PROBES коротких запросов. TIMEOUT*2+2
# оставляет медленному, но живому резолверу вдвое больше нужного и при этом
# держит меню отзывчивым.
CAND_BUDGET = _env_float("GODNS_CAND_BUDGET", TIMEOUT * 2.0 + 2.0, 2.0, 120.0)
QNAME = os.environ.get("GODNS_QNAME") or "example.com"
SERIAL = os.environ.get("GODNS_SERIAL", "") in ("1", "yes", "true")
CURL = os.environ.get("GODNS_CURL") or "curl"

# Классы отказов. Это не косметика: оператор по ним понимает, что чинить.
#   resolve — не разрешилось имя самого эндпоинта (нет рабочего DNS на боксе)
#   timeout — TCP молчит (порт режут)
#   refused — RST на connect (порт закрыт / нет сервиса)
#   tls     — сертификат/SNI/MITM — диагностическое золото в блокируемой сети
#   reset   — соединение оборвали после установки (часто DPI)
#   http    — ответ не 200 (505 = HTTP/1.1 отвергнут, 403 = паддинг/гео, 404 = путь)
#   proto   — 200, но в теле не валидный DNS-ответ (страница-заглушка)
#   tool    — нет curl или curl без HTTP/2
#   badcand — кандидат описан неправильно
#   other   — всё остальное


def log(msg):
    """Вся диагностика — в stderr. В stdout только JSON."""
    sys.stderr.write("bench: %s\n" % msg)


def _tls_backend():
    """Чем именно мы жмём руку. Пишем в отчёт: если DoT падает по версии
    протокола, виноват может быть локальный бэкенд, а не резолвер.
    На Ubuntu 22.04/24.04 это OpenSSL 3.x с TLS 1.3; на macOS питон бывает
    собран с LibreSSL 2.8, у которого потолок — TLS 1.2, и резолверы,
    принимающие только 1.3, отвечают alert protocol version."""
    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        return {"lib": ssl.OPENSSL_VERSION, "max": str(ctx.maximum_version)}
    except Exception:                            # noqa: BLE001
        return {"lib": "unknown", "max": "unknown"}


# ── DNS wire format ─────────────────────────────────────────────────────
def dns_query(name, qtype=1, msgid=0):
    """Минимальный запрос A/IN, RD=1. header: ID | flags | QD | AN | NS | AR."""
    hdr = struct.pack(">HHHHHH", msgid & 0xFFFF, 0x0100, 1, 0, 0, 0)
    labels = []
    for part in name.split("."):
        if not part:
            continue
        enc = part.encode("idna")
        if len(enc) > 63:
            raise ValueError("label too long: %r" % part)
        labels.append(bytes([len(enc)]) + enc)
    qn = b"".join(labels) + b"\x00"
    return hdr + qn + struct.pack(">HH", qtype, 1)


def b64url(raw):
    """RFC 8484 ?dns= — паддинг ОБЯЗАН быть срезан.

    Проверено живьём: Quad9 отдаёт 403 на ?dns= с %3D на конце.
    """
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def ok_answer(msg):
    """Ответ настоящий: QR=1, RCODE=0, один вопрос, хотя бы одна запись."""
    if not msg or len(msg) < 12:
        return False
    flags, qd, an = struct.unpack(">HHH", msg[2:8])
    return bool(flags & 0x8000) and (flags & 0x000F) == 0 and qd == 1 and an >= 1


def median(values):
    v = sorted(values)
    n = len(v)
    if n == 0:
        return None
    if n % 2:
        return v[n // 2]
    return (v[n // 2 - 1] + v[n // 2]) / 2.0


def _new_result(proto):
    return {
        "proto": proto,
        "cold_ms": None,
        "warm_ms": None,
        "probes_ok": 0,
        "probes": PROBES,
        "degraded": False,
        "degraded_reason": None,
        "error": None,
        "error_class": None,
    }


def _finish(r, warm):
    """Медиана по уцелевшим тёплым пробам + честный флаг degraded."""
    if len(warm) >= 2:
        r["warm_ms"] = round(median(warm), 1)
    elif len(warm) == 1:
        # Медиану из одной точки не делаем вид, что посчитали.
        r["warm_ms"] = round(warm[0], 1)
        r["degraded"] = True
        r["degraded_reason"] = "single_warm_sample"
    return r


# ── DoT: RFC 7858, чистый stdlib ────────────────────────────────────────
def _readn(sock, n, deadline):
    buf = b""
    while len(buf) < n:
        left = deadline - time.monotonic()
        if left <= 0:
            raise socket.timeout("read deadline")
        sock.settimeout(left)
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionResetError("peer closed")
        buf += chunk
    return buf


def _peer_cn(cert):
    if not cert:
        return None
    for rdn in cert.get("subject", ()):
        for key, val in rdn:
            if key == "commonName":
                return val
    return None


def _classify_exc(exc):
    # SSLError раньше OSError — он его подкласс.
    if isinstance(exc, ssl.SSLCertVerificationError):
        return "tls"
    if isinstance(exc, ssl.SSLError):
        return "tls"
    if isinstance(exc, socket.gaierror):
        return "resolve"
    if isinstance(exc, ConnectionRefusedError):
        return "refused"
    if isinstance(exc, (ConnectionResetError, BrokenPipeError, ConnectionAbortedError)):
        return "reset"
    # socket.timeout == TimeoutError на 3.10
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "timeout"
    if isinstance(exc, OSError) and getattr(exc, "errno", None) in (101, 113, 51, 65):
        return "unreachable"
    return "other"


def probe_dot(cand):
    """TLS на 853, каждое сообщение с 2-октетным префиксом длины (RFC 7858)."""
    r = _new_result("dot")
    host = cand.get("host") or cand.get("ip")
    ip = cand.get("ip") or cand.get("host")
    if not host:
        r["error"] = "candidate has neither host nor ip"
        r["error_class"] = "badcand"
        return r
    try:
        port = int(cand.get("port") or 853)
    except (TypeError, ValueError):
        port = 853
    r["endpoint"] = "%s:%d" % (ip, port)

    ctx = ssl.create_default_context()          # проверяет цепочку И hostname
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED

    warm = []
    sock = None
    try:
        t0 = time.monotonic()
        raw = socket.create_connection((ip, port), timeout=TIMEOUT)
        raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)  # иначе Nagle +40 мс
        r["connect_ms"] = round((time.monotonic() - t0) * 1000.0, 1)
        raw.settimeout(TIMEOUT)
        sock = ctx.wrap_socket(raw, server_hostname=host)          # SNI + проверка имени
        r["handshake_ms"] = round((time.monotonic() - t0) * 1000.0, 1)
        r["tls"] = sock.version()
        r["peer_cn"] = _peer_cn(sock.getpeercert())
        for i in range(PROBES):
            # Общий потолок: иначе резолвер, отвечающий по TIMEOUT на каждую
            # пробу, растянул бы одного кандидата на PROBES*TIMEOUT.
            if time.monotonic() - t0 > CAND_BUDGET:
                r["truncated"] = True
                break
            q = dns_query(QNAME, msgid=(i + 1) & 0xFFFF)  # DoT — ID единственный коррелятор
            t = time.monotonic()
            deadline = min(t + TIMEOUT, t0 + CAND_BUDGET)
            sock.sendall(struct.pack(">H", len(q)) + q)
            body = _readn(sock, struct.unpack(">H", _readn(sock, 2, deadline))[0], deadline)
            dt = (time.monotonic() - t) * 1000.0
            if not ok_answer(body):
                if r["error"] is None:
                    r["error"] = "malformed or negative DNS answer"
                    r["error_class"] = "proto"
                continue
            r["probes_ok"] += 1
            if i == 0:
                r["cold_ms"] = round((time.monotonic() - t0) * 1000.0, 1)
            else:
                warm.append(dt)
    except Exception as exc:                     # noqa: BLE001 — любой сбой это результат
        if r["probes_ok"] == 0 or r["error"] is None:
            r["error"] = "%s: %s" % (type(exc).__name__, exc)
            r["error_class"] = _classify_exc(exc)
            # Подсказка на редкий, но сбивающий с толку случай: TLS-бэкенд
            # самого python не тянет 1.3, а резолвер принимает только 1.3.
            # Тогда «TLS» в меню — это про наш python, а не про провайдера.
            if ("PROTOCOL_VERSION" in str(exc)
                    and _tls_backend()["max"] != "TLSVersion.TLSv1_3"):
                r["error"] += " (local TLS backend %s tops out at %s)" % (
                    _tls_backend()["lib"], _tls_backend()["max"])
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:                    # noqa: BLE001
                pass

    _finish(r, warm)
    # Оценку "cold минус рукопожатие" здесь НЕ делаем: она заполнила бы
    # warm_ms и скрыла кандидата от волны перепроб в run(). Оценка — самый
    # последний рубеж, она живёт в run().
    if r["probes_ok"] == 0 and r["error"] is None:
        r["error"] = "no successful probe"
        r["error_class"] = "other"
    return r


# ── Обычный DNS: UDP на :53, чистый stdlib ──────────────────────────────
def probe_plain(cand):
    """Открытый DNS по UDP. Рукопожатия нет, поэтому cold — это просто первый
    ответ; warm — медиана остальных. Свой апстрим оператора, у каталога
    такого протокола нет."""
    r = _new_result("plain")
    ip = cand.get("ip") or cand.get("host")
    if not ip:
        r["error"] = "candidate has neither host nor ip"
        r["error_class"] = "badcand"
        return r
    try:
        port = int(cand.get("port") or 53)
    except (TypeError, ValueError):
        port = 53
    r["endpoint"] = "%s:%d" % (ip, port)

    warm = []
    sock = None
    try:
        t0 = time.monotonic()
        fam = socket.AF_INET6 if ":" in ip else socket.AF_INET
        sock = socket.socket(fam, socket.SOCK_DGRAM)
        for i in range(PROBES):
            if time.monotonic() - t0 > CAND_BUDGET:
                r["truncated"] = True
                break
            q = dns_query(QNAME, msgid=(i + 1) & 0xFFFF)
            t = time.monotonic()
            deadline = min(t + TIMEOUT, t0 + CAND_BUDGET)
            sock.sendto(q, (ip, port))
            # Читаем до ответа с НАШИМ ID: чужая датаграмма (запоздавший
            # ответ на прошлую пробу) не должна засчитаться за эту.
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise socket.timeout("read deadline")
                sock.settimeout(left)
                body, _peer = sock.recvfrom(4096)
                if len(body) >= 2 and body[:2] == q[:2]:
                    break
            dt = (time.monotonic() - t) * 1000.0
            if not ok_answer(body):
                if r["error"] is None:
                    r["error"] = "malformed or negative DNS answer"
                    r["error_class"] = "proto"
                continue
            r["probes_ok"] += 1
            if i == 0:
                r["cold_ms"] = round((time.monotonic() - t0) * 1000.0, 1)
            else:
                warm.append(dt)
    except Exception as exc:                     # noqa: BLE001 — любой сбой это результат
        if r["probes_ok"] == 0 or r["error"] is None:
            r["error"] = "%s: %s" % (type(exc).__name__, exc)
            r["error_class"] = _classify_exc(exc)
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:                    # noqa: BLE001
                pass

    _finish(r, warm)
    if r["probes_ok"] == 0 and r["error"] is None:
        r["error"] = "no successful probe"
        r["error_class"] = "other"
    return r


# ── DoH: curl --http2 ───────────────────────────────────────────────────
CURL_W = ("%{time_connect} %{time_appconnect} %{time_starttransfer} "
          "%{num_connects} %{http_code} %{http_version}\n")

# curl-коды -> наш класс. Смотрим на код возврата, а НЕ дописываем `|| echo 000`:
# curl при сбое соединения сам печатает 000 и возвращает ненулевой код.
CURL_ERR = {
    5: "resolve",    # couldn't resolve proxy
    6: "resolve",    # couldn't resolve host
    7: "refused",    # failed to connect
    28: "timeout",   # operation timed out
    35: "tls",       # SSL connect error
    51: "tls",       # peer certificate / fingerprint
    52: "reset",     # empty reply from server
    56: "reset",     # failure receiving data
    58: "tls", 59: "tls", 60: "tls", 77: "tls", 90: "tls", 91: "tls",
    16: "http2",     # HTTP/2 framing layer
    92: "http2",
}

_CURL_STATE = {"checked": False, "path": None, "http2": False, "reason": None}
_CURL_LOCK = threading.Lock()


def _curl_cfg_q(value):
    """Значение для строки curl-конфига в двойных кавычках."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def curl_capability():
    """Один раз выясняем, есть ли curl и собран ли он с HTTP/2.

    Грабли: пометить checked=True ДО того, как определение закончилось —
    и остальные 15 потоков прочитают полуготовое состояние и решат, что
    HTTP/2 нет. Поэтому флаг ставится последним, под замком.
    """
    if _CURL_STATE["checked"]:
        return _CURL_STATE
    with _CURL_LOCK:
        if _CURL_STATE["checked"]:
            return _CURL_STATE
        path = shutil.which(CURL)
        if not path:
            _CURL_STATE["reason"] = "curl not found in PATH"
            _CURL_STATE["checked"] = True
            return _CURL_STATE
        _CURL_STATE["path"] = path
        try:
            p = subprocess.run([path, "-V"], capture_output=True, timeout=10)
            text = (p.stdout or b"").decode("utf-8", "replace")
        except Exception as exc:                 # noqa: BLE001
            _CURL_STATE["reason"] = "curl -V failed: %s" % exc
            _CURL_STATE["checked"] = True
            return _CURL_STATE
        _CURL_STATE["http2"] = ("HTTP2" in text) or ("nghttp2" in text)
        if not _CURL_STATE["http2"]:
            _CURL_STATE["reason"] = "curl built without HTTP/2"
        _CURL_STATE["checked"] = True
    return _CURL_STATE


def _split_url(url):
    """-> (host, port, scheme). Без urllib.parse хватило бы, но с ним честнее."""
    try:
        from urllib.parse import urlsplit
        u = urlsplit(url)
        host = u.hostname or ""
        port = u.port or (443 if u.scheme == "https" else 80)
        return host, int(port), u.scheme
    except Exception:                            # noqa: BLE001
        return "", 443, "https"


def _doh_url(cand):
    url = cand.get("url")
    if url:
        return url
    host = cand.get("host")
    if not host:
        return None
    path = cand.get("path") or "/dns-query"
    if not path.startswith("/"):
        path = "/" + path
    port = cand.get("port")
    if port and int(port) != 443:
        return "https://%s:%d%s" % (host, int(port), path)
    return "https://%s%s" % (host, path)


def probe_doh(cand):
    """RFC 8484 поверх HTTP/2. Один процесс curl, N URL -> 1 холодная + N-1 тёплых."""
    r = _new_result("doh")
    url = _doh_url(cand)
    method = (cand.get("method") or "GET").upper()
    r["method"] = method
    if not url:
        r["error"] = "candidate has neither url nor host"
        r["error_class"] = "badcand"
        return r
    cap = curl_capability()
    if not cap["path"] or not cap["http2"]:
        r["error"] = cap["reason"] or "curl unusable"
        r["error_class"] = "tool"
        return r

    host, port, _scheme = _split_url(url)
    r["endpoint"] = "%s:%d" % (host or "?", port)
    ip = cand.get("ip")

    # Общий бюджет: curl не должен пережить меню.
    budget = CAND_BUDGET
    argv = [cap["path"], "-q", "-sS", "--http2",
            "--max-time", "%.1f" % budget,
            "--connect-timeout", "%.1f" % TIMEOUT,
            "-H", "accept: application/dns-message",
            "-w", CURL_W]
    if ip:
        # Пин на IP: не даём боксовому (возможно уже сломанному) DNS влиять
        # на замер и не меряем чужую рекурсию.
        argv += ["--resolve", "%s:%d:%s" % (host, port, ip)]

    tmpdir = tempfile.mkdtemp(prefix="godns-bench.")
    qfile = None
    outs = []
    try:
        if method == "POST":
            qfile = os.path.join(tmpdir, "q.bin")
            with open(qfile, "wb") as fh:
                fh.write(dns_query(QNAME))       # DoH: ID = 0 (RFC 8484 SHOULD)
            # `--data-binary` указывается РОВНО ОДИН РАЗ на весь список URL.
            # Грабли (замерено): повторённый --data-binary curl не «повторяет
            # для каждого URL», а СКЛЕИВАЕТ тела через «&» — пять проб по 29
            # байт превращаются в один битый запрос на 149 байт, и он уходит
            # в КАЖДОЙ пробе. Cloudflare такое молча съедает (лишние байты
            # игнорирует), а Quad9 отдаёт 403 — и меню обвиняет Quad9 в
            # гео-блокировке за нашу же кривую отправку.
            # Проверено: 1 URL -> upload=29 code=200; 2 URL с повтором ->
            # upload=59 code=403; одно --data-binary на 3 URL -> upload=29
            # в каждой пробе, num_connects 1,0,0 (то есть холодная + тёплые).
            argv += ["-H", "content-type: application/dns-message",
                     "--data-binary", "@" + qfile]

        get_url = url + ("&" if "?" in url else "?") + "dns=" + b64url(dns_query(QNAME))
        # URL — НЕ в argv. ГРАБЛИ, пойманные QA на живом сервере: у своего
        # DoH-апстрима путь может быть чужим секретом (второй goDNS как
        # апстрим), мост и демон берегут его в окружении — а замер клал
        # полный URL в аргументы curl, и на секунду пробы он был виден любому
        # локальному пользователю через /proc/<pid>/cmdline. Правило проекта
        # «секреты не передавать аргументами curl» касается и python.
        # Поэтому URL и файлы вывода уезжают конфигом через stdin (-K -):
        # curl берёт n-й output для n-го url. Экранирование по правилам
        # curl-конфига: внутри "…" значимы только \ и ".
        cfg_lines = []
        for i in range(PROBES):
            # ВАЖНО: output на КАЖДЫЙ URL. Один покрывает только первый
            # трансфер, остальные бинарные DNS-ответы вываливаются в stdout
            # и ломают разбор -w.
            out = os.path.join(tmpdir, "r%02d.bin" % i)
            outs.append(out)
            cfg_lines.append('url = "%s"' % _curl_cfg_q(url if method == "POST" else get_url))
            cfg_lines.append('output = "%s"' % _curl_cfg_q(out))
        argv += ["-K", "-"]
        cfg = ("\n".join(cfg_lines) + "\n").encode("utf-8")

        try:
            p = subprocess.run(argv, input=cfg, capture_output=True, timeout=budget + 3.0)
        except subprocess.TimeoutExpired:
            r["error"] = "curl wedged past %.0fs budget" % (budget + 3.0)
            r["error_class"] = "timeout"
            return r
        except Exception as exc:                 # noqa: BLE001
            r["error"] = "%s: %s" % (type(exc).__name__, exc)
            r["error_class"] = "tool"
            return r

        warm = []
        idx = 0
        http_codes = []
        for line in p.stdout.decode("utf-8", "replace").splitlines():
            f = line.split()
            if len(f) != 6:
                continue
            tconn, tapp, tstart, nconn, code, ver = f
            body = outs[idx] if idx < len(outs) else None
            idx += 1
            http_codes.append(code)
            if code != "200":
                continue
            # 200 ещё не ответ: бывает HTML-заглушка с кодом 200.
            raw = b""
            if body:
                try:
                    with open(body, "rb") as fh:
                        raw = fh.read(4096)
                except OSError:
                    raw = b""
            if not ok_answer(raw):
                if r["error"] is None:
                    r["error"] = "HTTP 200 but body is not a valid DNS answer"
                    r["error_class"] = "proto"
                continue
            r["probes_ok"] += 1
            r["http_version"] = ver
            try:
                f_tstart = float(tstart)
                f_tconn = float(tconn)
                f_tapp = float(tapp)
            except ValueError:
                continue
            if nconn != "0":
                # был (пере)коннект -> это холодная проба
                if r["cold_ms"] is None:
                    r["cold_ms"] = round(f_tstart * 1000.0, 1)
                    r["tls_ms"] = round((f_tapp - f_tconn) * 1000.0, 1)
            else:
                warm.append(f_tstart * 1000.0)

        _finish(r, warm)

        if r["probes_ok"] == 0:
            stderr = p.stderr.decode("utf-8", "replace").strip()
            if r["error_class"] != "proto":
                r["error"] = (stderr or "no successful probe")[:200]
                # Код 000 — это «соединения не было», а не статус: curl сам
                # его печатает при сбое и возвращает ненулевой код возврата,
                # так что как HTTP-статус его брать нельзя.
                bad = [c for c in http_codes if c not in ("200", "000")]
                if bad:
                    # Наблюдённый статус конкретнее, чем код возврата curl:
                    # сервер, отдающий 404 на все пять проб, в конце нередко
                    # шлёт GOAWAY, и curl выходит с 16 — но чинить оператору
                    # надо путь, а не «обрыв».
                    r["error_class"] = "http"
                    r["http_code"] = bad[0]
                    r["error"] = "HTTP %s" % bad[0]
                elif p.returncode in CURL_ERR:
                    r["error_class"] = CURL_ERR[p.returncode]
                else:
                    r["error_class"] = "other"
            if r["error_class"] == "http2":
                r["error_class"] = "reset"
        elif r["probes_ok"] < r["probes"] and r["error"] is None:
            # «000» — не статус, а «трансфера не было». Как HTTP-код его
            # печатать нельзя: оператор пойдёт искать несуществующий ответ
            # сервера вместо обрыва соединения.
            bad = [c for c in http_codes if c not in ("200", "000")]
            lost = len([c for c in http_codes if c == "000"]) + \
                (r["probes"] - len(http_codes))
            if bad:
                r["error"] = "HTTP %s on %d of %d probes" % (bad[0], len(bad), r["probes"])
                r["error_class"] = "http"
                r["http_code"] = bad[0]
            elif lost > 0:
                r["error"] = "%d of %d probes lost the connection" % (lost, r["probes"])
                r["error_class"] = "reset"
        return r
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ── Оркестрация ─────────────────────────────────────────────────────────
def probe_one(cand):
    proto = (cand.get("proto") or "").lower()
    if proto in ("dot", "tls", "tcp-tls"):
        return probe_dot(cand)
    if proto in ("doh", "https", "h2"):
        return probe_doh(cand)
    if proto in ("plain", "udp", "do53"):
        return probe_plain(cand)
    r = _new_result(proto or "?")
    r["error"] = "unknown proto %r (expected dot|doh|plain)" % proto
    r["error_class"] = "badcand"
    return r


def _safe_probe(cand):
    try:
        return probe_one(cand)
    except Exception as exc:                     # noqa: BLE001 — поток не должен ронять прогон
        r = _new_result((cand.get("proto") or "?"))
        r["error"] = "%s: %s" % (type(exc).__name__, exc)
        r["error_class"] = "other"
        return r


def _needs_retry(res):
    """Известный остаток: если curl споткнулся на переиспользовании потока
    HTTP/2 (`curl: (16) Error in the HTTP2 framing layer`, примерно один
    прогон из пяти), он переподключается, каждая уцелевшая проба оказывается
    холодной, и warm_ms приходит None при probes_ok 3/5. То же бывает и на
    DoT, когда сервер закрывает соединение после первого ответа.

    Такого кандидата перепробуем РОВНО один раз: волна коротких перепроб
    состоит только из тех, кто уже отвечал, — четырёхсекундных таймаутов
    в ней нет, она стоит около секунды.
    """
    return res.get("warm_ms") is None and res.get("probes_ok", 0) > 0


def _map(cands, fn):
    if SERIAL or len(cands) <= 1:
        return [fn(c) for c in cands]
    with ThreadPoolExecutor(max_workers=min(PARALLEL, max(1, len(cands)))) as ex:
        return list(ex.map(fn, cands))


def run(cands):
    out = {}
    # Выясняем возможности curl ДО запуска пула: иначе 16 потоков упрутся
    # в один и тот же замок вокруг `curl -V`.
    if any((c.get("proto") or "").lower() in ("doh", "https", "h2") for c in cands):
        cap = curl_capability()
        if not cap["path"] or not cap["http2"]:
            log(cap["reason"] or "curl unusable")
    results = _map(cands, _safe_probe)
    retry = []
    for cand, res in zip(cands, results):
        res["id"] = cand.get("id")
        out[res["id"]] = res
        if _needs_retry(res):
            retry.append(cand)

    if retry:
        log("re-probing %d candidate(s) that lost their warm samples" % len(retry))
        for cand, res2 in zip(retry, _map(retry, _safe_probe)):
            cid = cand.get("id")
            res2["id"] = cid
            res2["retried"] = True
            if res2.get("warm_ms") is not None:
                out[cid] = res2
            elif res2.get("probes_ok", 0) > out[cid].get("probes_ok", 0):
                out[cid] = res2

    # Последний рубеж: тёплой пробы так и нет, но холодная есть —
    # оцениваем RTT как cold минус рукопожатие и честно метим degraded.
    for cid, res in out.items():
        if res.get("warm_ms") is None and res.get("cold_ms") is not None:
            hs = res.get("tls_ms")
            if hs is None:
                hs = res.get("handshake_ms")
            if hs is not None and res["cold_ms"] - hs > 0:
                res["warm_ms"] = round(res["cold_ms"] - hs, 1)
                res["degraded"] = True
                res["degraded_reason"] = "estimated_from_cold"
    return out


def _load(argv):
    src = None
    for a in argv[1:]:
        if a == "--serial":
            globals()["SERIAL"] = True
        elif a == "--pretty":
            globals()["PRETTY"] = True
        elif a == "-":
            src = "-"
        elif not a.startswith("-"):
            src = a
    if src is None or src == "-":
        data = json.load(sys.stdin)
    else:
        with open(src, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    if isinstance(data, dict) and "candidates" in data:
        data = data["candidates"]
    if not isinstance(data, list):
        raise ValueError("candidates must be a JSON list")
    seen = set()
    clean = []
    for i, c in enumerate(data):
        if not isinstance(c, dict):
            continue
        cid = str(c.get("id") or c.get("host") or c.get("url") or "cand%d" % i)
        while cid in seen:
            cid += "_"
        seen.add(cid)
        c = dict(c)
        c["id"] = cid
        clean.append(c)
    return clean


PRETTY = False


def main():
    try:
        cands = _load(sys.argv)
    except Exception as exc:                     # noqa: BLE001
        log("bad candidate input: %s: %s" % (type(exc).__name__, exc))
        sys.stdout.write(json.dumps({"elapsed_s": 0, "results": {},
                                     "error": str(exc)}))
        sys.stdout.write("\n")
        return 2
    t0 = time.monotonic()
    results = run(cands)                          # СНАЧАЛА прогон…
    elapsed = round(time.monotonic() - t0, 2)     # …и только потом часы
    payload = {
        "elapsed_s": elapsed,
        "config": {"probes": PROBES, "timeout_s": TIMEOUT, "qname": QNAME,
                   "parallel": 1 if SERIAL else PARALLEL,
                   "python": "%d.%d" % sys.version_info[:2],
                   "tls_backend": _tls_backend()["lib"],
                   "tls_max": _tls_backend()["max"]},
        "results": results,
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=False,
                                indent=1 if PRETTY else None))
    sys.stdout.write("\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
