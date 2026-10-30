#!/usr/bin/env python3
# goDNS — Copyright (c) 2026 potap1978. All rights reserved.
"""
goDNS — экран живого наблюдения: разбор вывода консоли dnsdist в таблицу.

Зачем отдельный файл, а не awk в bash
-------------------------------------
Две причины, обе из граблей проекта.

1. `printf '%-12s'` считает БАЙТЫ. Кириллическая подпись — два байта на
   букву, и колонки разъезжаются ровно на свою длину. `str.ljust` в python3
   считает символы, поэтому вся вёрстка таблицы живёт здесь, а bash только
   печатает готовые строки.
2. Колонки `grepq` — фиксированной ширины, но поле, которое НЕ ВЛЕЗЛО,
   сдвигает всё правее себя. Проверено на живом dnsdist 2.1.1:
   имя `nonexistent-zzz-qqq.example.` (28 символов) в колонке шириной 25
   сдвигает `Type` с позиции 114 на 117. Разбор по пробелам такую строку
   разбирает неверно (у запросов колонка Server ПУСТАЯ — токены съезжают),
   а разбор по жёстким смещениям ломается на переполнении. Нужен обход
   колонок с переносом сдвига — в bash это нечитаемо.

Что на входе (stdin), секциями
------------------------------
    @@GODNS labels     ключ=значение — подписи (i18n) и цвета из bash
    @@GODNS servers    сырой вывод showServers()
    @@GODNS stats      сырой вывод dumpStats()
    @@GODNS feed       сырой вывод grepq("", N)

Что на выходе (stdout): готовые строки экрана, каждая уже с цветом и с
`ESC[K` на конце (гасит хвост предыдущего кадра — так перерисовка не мигает).

Что здесь НЕ делается
---------------------
Никаких сетевых запросов и никаких вызовов dnsdist: этот файл только
форматирует уже полученный текст. Так его можно прогнать на фикстурах без
сервера — чем и занимается test_engine.sh.

Только stdlib. python3.8+ (на Ubuntu 22.04 штатный 3.10).
"""

import os
import re
import sys
import time

ESC_K = "\033[K"

# ── Коды ответа dnsdist → короткая метка ────────────────────────────────
# dnsdist печатает RCode словами и дописывает число ответов:
# «No Error. 2 answers», «Non-Existent domain» (строчная «d» — так в 2.1.1).
# Сверяем по префиксу в нижнем регистре, чтобы правка регистра в следующей
# версии не превращала таблицу в кашу.
RCODE_MAP = (
    ("no error", "NOERROR"),
    ("format error", "FORMERR"),
    ("server failure", "SERVFAIL"),
    ("non-existent", "NXDOMAIN"),
    ("not implemented", "NOTIMP"),
    ("query refused", "REFUSED"),
    ("refused", "REFUSED"),
)

# Так dnsdist называет ответ, который отдал сам, не спрашивая апстрим.
CACHE_MARKERS = ("cache", "0.0.0.0:0", "[::]:0")


# ═══════════════════════════════════════════════════════════════════════
# РАЗБОР ТАБЛИЦ dnsdist
# ═══════════════════════════════════════════════════════════════════════
def header_offsets(line):
    """[(имя_колонки, позиция), …] по строке заголовка."""
    return [(m.group(0), m.start()) for m in re.finditer(r"\S+", line)]


def split_fixed(line, starts):
    """Разрезать строку по стартам колонок, ПЕРЕНОСЯ сдвиг от переполнения.

    `grepq` печатает boost::format'ом вида `%-7.1f %-47s %-12s …` — то есть
    ровно один пробел между полями. Поэтому ширина поля i равна
    starts[i+1] - starts[i] - 1. Если поле шире (длинное имя), оно съедает
    разделитель и двигает вправо ВСЁ остальное; тянемся до ближайшего
    пробела и продолжаем с новой позиции. Ширины при этом не меняются —
    сдвиг живёт только в курсоре.
    """
    out = []
    pos = 0
    n = len(starts)
    for i in range(n):
        if i == n - 1:
            out.append(line[pos:].strip())
            break
        width = starts[i + 1] - starts[i] - 1
        if width < 1:
            width = 1
        end = pos + width
        if end < len(line) and not line[end].isspace():
            while end < len(line) and not line[end].isspace():
                end += 1
        out.append(line[pos:end].strip())
        pos = min(end + 1, len(line))
    while len(out) < n:
        out.append("")
    return out


def parse_servers(text):
    """showServers() → [{name, address, state, queries, lat, outstanding, drops}].

    Здесь разбор ПО ПРОБЕЛАМ, а не по смещениям, и это не небрежность:
    числовые колонки showServers выровнены ПО ПРАВОМУ краю (заголовок `Qps`
    на позиции 80, значение `0.0` — на 84), так что смещения заголовка полю
    не соответствуют. Зато пустых колонок в середине нет — имя и адрес
    без пробелов, — поэтому токенизация здесь точна, а там неточна.
    """
    rows = []
    lines = [l for l in text.splitlines() if l.strip()]
    head = None
    for line in lines:
        toks = line.split()
        if head is None:
            if "Name" in toks and "Address" in toks and "State" in toks:
                head = toks
            continue
        # Итоговая строка «All …» — без имени; токены в ней означают другое.
        if not toks or toks[0] == "All":
            continue
        idx = {name: i for i, name in enumerate(head)}

        def col(name, default=""):
            i = idx.get(name)
            if i is None or i >= len(toks):
                return default
            return toks[i]

        # ВАЖНО ДЛЯ goDNS: у нас ВСЕ апстримы DoT/DoH, то есть TCP. Колонка
        # `Lat` считает только UDP-ответы и на живом goDNS всегда «-»,
        # а число лежит в колонке `TCP`. Проверено на dnsdist 2.1.1.
        lat = col("Lat", "-")
        if lat in ("", "-"):
            lat = col("TCP", "-")
        rows.append({
            "name": col("Name"),
            "address": col("Address"),
            "state": col("State"),
            "queries": col("Queries", "0"),
            "drops": col("Drops", "0"),
            "lat": lat,
            "outstanding": col("Outstanding", "0"),
        })
    return rows


def parse_stats(text):
    """dumpStats() → {имя: строка-значение}.

    Вывод — две колонки пар «имя<TAB>значение» в строке. Регулярка берёт
    пары где угодно в строке и потому не зависит от того, сколько колонок
    решила напечатать конкретная версия.
    """
    out = {}
    for name, value in re.findall(r"([a-z][a-z0-9-]{2,})\s+(-?\d+(?:\.\d+)?)", text):
        out.setdefault(name, value)
    return out


def parse_feed(text):
    """grepq("", N) → [запись], где запись — разобранная строка кольца.

    Возвращает список словарей в порядке от старых к новым (так их и
    печатает dnsdist). kind = 'q' (запрос) или 'r' (ответ).
    """
    lines = [l for l in text.splitlines() if l.strip()]
    starts = None
    names = None
    out = []
    for line in lines:
        if starts is None:
            cols = header_offsets(line)
            got = [c[0] for c in cols]
            if "Time" in got and "Client" in got and "Rcode" in got:
                names = got
                starts = [c[1] for c in cols]
            continue
        vals = split_fixed(line, starts)
        rec = dict(zip(names, vals))
        try:
            ago = abs(float(rec.get("Time", "0") or 0))
        except ValueError:
            continue
        rcode = (rec.get("Rcode") or "").strip()
        kind = "q" if rcode.lower().startswith("question") else "r"
        out.append({
            "kind": kind,
            "ago": ago,
            "client": rec.get("Client", ""),
            "proto": rec.get("Protocol", "") or "",
            "server": rec.get("Server", "") or "",
            "id": rec.get("ID", ""),
            "name": rec.get("Name", ""),
            "qtype": rec.get("Type", ""),
            "lat": rec.get("Lat.", "") or "",
            "rcode": rcode,
        })
    return out


# ═══════════════════════════════════════════════════════════════════════
# СКЛЕЙКА ЗАПРОС + ОТВЕТ В ОДИН ОБМЕН
# ═══════════════════════════════════════════════════════════════════════
def pair_exchanges(records):
    """Из двух колец dnsdist собрать по одной строке на обмен.

    Кольцо запросов и кольцо ответов — разные, и `grepq` печатает их
    вперемешку: каждый запрос даёт ДВЕ строки. Оператору нужна одна:
    «пришло от кого, за что, ушло к кому, за сколько, чем кончилось».

    Ключ склейки — клиент:порт + id + имя + тип. Ответ без своего запроса
    (запрос уже вытеснен из кольца) остаётся отдельной записью — это
    честнее, чем прятать его.
    """
    pending = {}
    order = []
    for r in records:
        key = (r["client"], r["id"], r["name"], r["qtype"])
        if r["kind"] == "q":
            item = {
                "ago": r["ago"], "client": r["client"], "proto": r["proto"],
                "name": r["name"], "qtype": r["qtype"],
                "server": "", "lat": "", "rcode": "", "answered": False,
            }
            pending.setdefault(key, []).append(item)
            order.append(item)
        else:
            item = None
            queue = pending.get(key)
            if queue:
                item = queue.pop(0)
                if not queue:
                    pending.pop(key, None)
            if item is None:
                item = {
                    "ago": r["ago"], "client": r["client"], "proto": r["proto"],
                    "name": r["name"], "qtype": r["qtype"],
                    "server": "", "lat": "", "rcode": "", "answered": False,
                }
                order.append(item)
            item["server"] = r["server"]
            item["lat"] = r["lat"]
            item["rcode"] = r["rcode"]
            item["answered"] = True
            # Протокол ответа — это протокол ДО АПСТРИМА (DoT/DoH), а у
            # запроса — протокол клиента. Показываем клиентский.
            if not item["proto"]:
                item["proto"] = r["proto"]
    return order


# ═══════════════════════════════════════════════════════════════════════
# ФОРМАТИРОВАНИЕ
# ═══════════════════════════════════════════════════════════════════════
def cut(value, width):
    """Обрезать по СИМВОЛАМ, пометив обрезку. Пустая ширина — пустая строка."""
    if width <= 0:
        return ""
    value = value or ""
    if len(value) <= width:
        return value
    if width == 1:
        return "…"
    return value[: width - 1] + "…"


def pad(value, width, right=False):
    value = cut(value, width)
    if right:
        return value.rjust(width)
    return value.ljust(width)


def short_rcode(text):
    low = (text or "").lower()
    for prefix, short in RCODE_MAP:
        if low.startswith(prefix):
            # «No Error. 0 answers» — это NOERROR без записей, то есть
            # NODATA. Разница важна: клиент видит «пусто», а не «нет домена».
            if short == "NOERROR" and re.search(r"\b0 answers\b", low):
                return "NODATA"
            return short
    return (text or "").split(".")[0][:8]


def fmt_ms(value):
    try:
        return "%.1f" % float(value)
    except (TypeError, ValueError):
        return "—"


def human_int(value):
    """1234567 → «1 234 567». Неразрывный пробел не берём: ширина считается."""
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        return str(value or "0")
    out = ""
    s = str(abs(n))
    while len(s) > 3:
        out = " " + s[-3:] + out
        s = s[:-3]
    return ("-" if n < 0 else "") + s + out


# ═══════════════════════════════════════════════════════════════════════
# ВХОД
# ═══════════════════════════════════════════════════════════════════════
def read_sections(stream):
    sections = {}
    current = None
    for line in stream.read().splitlines():
        if line.startswith("@@GODNS "):
            current = line[len("@@GODNS "):].strip()
            sections[current] = []
            continue
        if current is not None:
            sections[current].append(line)
    return {k: "\n".join(v) for k, v in sections.items()}


def parse_labels(text):
    out = {}
    for line in text.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v
    return out


def main():
    width = 78
    rows = 10
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--width" and i + 1 < len(args):
            try:
                width = max(60, min(200, int(args[i + 1])))
            except ValueError:
                pass
        if a == "--rows" and i + 1 < len(args):
            try:
                rows = max(3, min(60, int(args[i + 1])))
            except ValueError:
                pass

    sections = read_sections(sys.stdin)
    L = parse_labels(sections.get("labels", ""))

    def lbl(key, default=""):
        return L.get(key, default)

    C_DIM = lbl("c_dim")
    C_NC = lbl("c_nc")
    C_WHITE = lbl("c_white")
    C_GREEN = lbl("c_green")
    C_RED = lbl("c_red")
    C_YELLOW = lbl("c_yellow")
    C_CYAN = lbl("c_cyan")
    C_BOLD = lbl("c_bold")

    servers = parse_servers(sections.get("servers", ""))
    stats = parse_stats(sections.get("stats", ""))
    feed = pair_exchanges(parse_feed(sections.get("feed", "")))

    # Адрес апстрима → имя из конфигурации. Кольцо помнит АДРЕС, а оператор
    # выбирал апстримы по именам — без карты таблица говорит на другом языке.
    by_addr = {s["address"]: s["name"] for s in servers if s["address"]}

    out = []

    def emit(text=""):
        out.append(text + ESC_K)

    # ── шапка ───────────────────────────────────────────────────────────
    # Подсказка «как выйти» живёт ВНИЗУ экрана, а не в заголовке: в 80
    # колонок заголовок вместе с ней не влезает, а обрезанная подсказка
    # про выход — худшее, что можно обрезать.
    title = cut(lbl("title", "goDNS Pro"), width - 14)
    clock = time.strftime("%H:%M:%S")
    gap = width - 2 - len(title) - len(clock)
    if gap < 1:
        gap = 1
    emit("  %s%s%s%s%s%s%s" % (C_BOLD + C_WHITE, title, C_NC,
                               " " * gap, C_DIM, clock, C_NC))
    emit("  %s%s%s" % (C_DIM, "─" * (width - 2), C_NC))

    # ── счётчики ────────────────────────────────────────────────────────
    hits = int(float(stats.get("cache-hits", 0) or 0))
    misses = int(float(stats.get("cache-misses", 0) or 0))
    total_cache = hits + misses
    rate = ("%d%%" % (hits * 100 // total_cache)) if total_cache else "—"
    line = "  %s%s%s %s   %s%s%s %s   %s%s%s %s/%s (%s)" % (
        C_DIM, lbl("st_queries", "queries"), C_NC,
        C_WHITE + human_int(stats.get("queries", 0)) + C_NC,
        C_DIM, lbl("st_responses", "responses"), C_NC,
        C_WHITE + human_int(stats.get("responses", 0)) + C_NC,
        C_DIM, lbl("st_cache", "cache"), C_NC,
        C_GREEN + human_int(hits) + C_NC, human_int(misses), rate,
    )
    emit(line)

    timeouts = int(float(stats.get("downstream-timeouts", 0) or 0))
    servfail = int(float(stats.get("servfail-responses", 0) or 0))
    nxd = int(float(stats.get("frontend-nxdomain", 0) or 0))
    tcol = C_RED if timeouts else C_DIM
    scol = C_YELLOW if servfail else C_DIM
    emit("  %s%s%s %s%s%s   %s%s%s %s%s%s   %s%s%s %s" % (
        C_DIM, lbl("st_timeouts", "timeouts"), C_NC, tcol, human_int(timeouts), C_NC,
        C_DIM, lbl("st_servfail", "servfail"), C_NC, scol, human_int(servfail), C_NC,
        C_DIM, lbl("st_nxdomain", "nxdomain"), C_NC, human_int(nxd),
    ))
    emit()

    # ── апстримы ────────────────────────────────────────────────────────
    emit("  %s%s%s" % (C_BOLD, lbl("sec_upstreams", "upstreams"), C_NC))
    w_name, w_state, w_q, w_lat, w_out = 12, 6, 8, 9, 8
    fixed = 2 + w_name + w_state + w_q + w_lat + w_out + 5
    # Потолок ровно на «[2606:4700:4700::1111]:853» — самый длинный адрес,
    # который вообще может тут оказаться. Без потолка вся ширина широкого
    # терминала уходит в пустую колонку адреса.
    w_addr = max(14, min(26, width - fixed))
    emit("  %s%s %s %s %s %s %s%s" % (
        C_DIM,
        pad(lbl("sv_name", "name"), w_name),
        pad(lbl("sv_addr", "address"), w_addr),
        pad(lbl("sv_state", "state"), w_state),
        pad(lbl("sv_queries", "queries"), w_q, True),
        pad(lbl("sv_lat", "latency"), w_lat, True),
        pad(lbl("sv_out", "in flight"), w_out, True),
        C_NC,
    ))
    if not servers:
        emit("    %s%s%s" % (C_DIM, lbl("no_servers", "—"), C_NC))
    for s in servers:
        up = s["state"].lower().startswith("up")
        scolor = C_GREEN if up else C_RED
        lat = s["lat"]
        lat_txt = "—" if lat in ("", "-") else "%s %s" % (lat, lbl("ms", "ms"))
        emit("  %s %s %s%s%s %s %s %s" % (
            pad(s["name"], w_name),
            pad(s["address"], w_addr),
            scolor, pad(s["state"], w_state), C_NC,
            pad(human_int(s["queries"]), w_q, True),
            pad(lat_txt, w_lat, True),
            pad(human_int(s["outstanding"]), w_out, True),
        ))
    emit()

    # ── лента обменов ───────────────────────────────────────────────────
    emit("  %s%s%s" % (C_BOLD, lbl("sec_feed", "queries"), C_NC))
    # Протокол клиента (DoT/DoH/DoUDP) — колонка полезная, но в 80 колонок
    # она отбирает ширину у имени, а имя важнее. Показываем от 88 колонок.
    show_proto = width >= 88
    w_time, w_client, w_type, w_up, w_ms, w_rc = 8, 15, 5, 10, 6, 8
    w_proto = 6 if show_proto else 0
    # 7 колонок → 6 разделителей; с протоколом 8 колонок → 7.
    used = 2 + w_time + w_client + w_type + w_up + w_ms + w_rc + 6
    if show_proto:
        used += w_proto + 1
    w_name = max(12, width - used)

    head = "  %s%s %s" % (C_DIM, pad(lbl("q_time", "time"), w_time),
                          pad(lbl("q_client", "client"), w_client))
    if show_proto:
        head += " " + pad(lbl("q_proto", "proto"), w_proto)
    head += " %s %s %s %s %s%s" % (
        pad(lbl("q_name", "name"), w_name),
        pad(lbl("q_type", "type"), w_type),
        pad(lbl("q_up", "upstream"), w_up),
        pad(lbl("q_ms", "ms"), w_ms, True),
        pad(lbl("q_rcode", "answer"), w_rc),
        C_NC,
    )
    emit(head)

    now = time.time()
    tail = feed[-rows:] if rows < len(feed) else feed
    if not tail:
        emit("    %s%s%s" % (C_DIM, lbl("feed_empty", "—"), C_NC))
    for ex in tail:
        stamp = time.strftime("%H:%M:%S", time.localtime(now - ex["ago"]))
        client = (ex["client"] or "").rsplit(":", 1)[0]
        server = ex["server"] or ""
        low = server.lower()
        if not ex["answered"]:
            # Ответа в кольце нет. Совсем свежий запрос ещё может быть
            # в полёте — назвать это «нет ответа» было бы враньём.
            up = lbl("inflight", "…") if ex["ago"] < 2.0 else lbl("noanswer", "no answer")
            upcolor = C_DIM if ex["ago"] < 2.0 else C_RED
            ms_txt, rc_txt, rc_color = "—", "—", C_DIM
        elif any(m in low for m in CACHE_MARKERS) or not server:
            up = lbl("cache", "cache")
            upcolor = C_CYAN
            ms_txt = fmt_ms(ex["lat"])
            rc_txt = short_rcode(ex["rcode"])
            rc_color = C_NC
        else:
            up = by_addr.get(server, server)
            upcolor = C_NC
            ms_txt = fmt_ms(ex["lat"])
            rc_txt = short_rcode(ex["rcode"])
            rc_color = C_NC
        if rc_txt in ("SERVFAIL", "REFUSED"):
            rc_color = C_RED
        elif rc_txt in ("NXDOMAIN", "NODATA"):
            rc_color = C_YELLOW

        row = "  %s%s%s %s" % (C_DIM, stamp, C_NC, pad(client, w_client))
        if show_proto:
            row += " " + pad(ex["proto"], w_proto)
        row += " %s %s %s%s%s %s %s%s%s" % (
            pad(ex["name"].rstrip("."), w_name),
            pad(ex["qtype"], w_type),
            upcolor, pad(up, w_up), C_NC,
            pad(ms_txt, w_ms, True),
            rc_color, pad(rc_txt, w_rc), C_NC,
        )
        emit(row)

    notes = [lbl("note", ""), lbl("note2", ""), lbl("note3", "")]
    if any(notes):
        emit()
    for note in notes:
        if note:
            emit("  %s%s%s" % (C_DIM, cut(note, width - 2), C_NC))
    hint = lbl("hint", "")
    if hint:
        emit("  %s%s%s" % (C_DIM, cut(hint, width - 2), C_NC))

    sys.stdout.write("\n".join(out) + "\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        os._exit(0)
