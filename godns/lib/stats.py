#!/usr/bin/env python3
# goDNS — Copyright (c) 2026 potap1978. All rights reserved.
"""goDNS — окна статистики запросов поверх НАКОПИТЕЛЬНЫХ счётчиков dnsdist.

Зачем это на python3, а не на bash: та же причина, что у json_get — python3
есть на всех целевых образах, а арифметика с ~1600 строками, свёрткой в
корзины и тремя ловушками на bash превращается в поле мин. Все строки для
человека живут в lib/stats.sh (t/tf); этот файл печатает только данные.

Модель. dnsdist отдаёт через консоль ТОЛЬКО накопительные счётчики
(dumpStats), окон он не считает. Окна получаются вычитанием срезов, которые
раз в минуту снимает systemd-таймер. Файл срезов — одно состояние
фиксированного размера:

    V  1                                             формат
    R  ts  svcmark  queries  cachehits  servfail     последний сырой срез
    M  минута  срезов  Δзапросы  Δкэш  Δservfail  разрыв   (≤ 24 часов)
    H  час     срезов  Δзапросы  Δкэш  Δservfail  разрыв   (≤ 7 суток)

Поминутные строки старше суток сворачиваются в часовые ПРИ ДОБАВЛЕНИИ
среза, часовые старше недели отбрасываются: ~1440 M + ~169 H строк, размер
файла постоянен навсегда (ловушка 3).

Три ловушки, из-за которых наивное вычитание врёт:
 1. Перезапуск службы обнуляет счётчики. «Текущее < предыдущего» — это
    старт с нуля, а не отрицательный трафик. Чтобы отличить перезапуск от
    аномалии, в срезе хранится ActiveEnterTimestampMonotonic службы: метка
    сменилась — счётчики законно начались заново, и дельта равна текущему
    значению. Метка та же, а счётчик упал — данные не объяснить, дельта
    берётся текущим значением, срез помечается разрывом.
 2. Перезагрузка сервера оставляет ДЫРУ между срезами. Часы без срезов —
    не «ноль запросов», а отсутствие сервера. Пауза больше GAP_SEC помечает
    срез разрывом; окно с разрывом внутри показывается как приблизительное,
    а скорость считается по ПОКРЫТОМУ времени (срезов × 60 с), не по ширине
    окна.
 3. Рост файла — см. свёртку выше.

Подкоманды:
    ingest <файл>                    срезы из stdin: ts svcmark q hits fails
    report <файл> [--now N]         все окна + спарклайны + возраст, TSV
    window <файл> <окно> [--now N]  одно число (rc 1 — данных нет)
    spark  <файл> <N> [K] [--now N] спарклайн: N знаков по K срезов
"""

import math
import os
import sys
import tempfile
import time

SLICE = 60                  # шаг срезов, секунд
GAP_SEC = 150               # пауза больше этой = разрыв (2.5 интервала)
STALE_SEC = 150             # свежих срезов нет — хвост окна дырявый
MIN_HORIZON = 24 * 3600     # поминутный слой
HOUR_HORIZON = 7 * 24 * 3600
BARS = "▁▂▃▄▅▆▇█"
GAP_CHAR = "·"

# имя, ширина, запас на неточность границы (свёртка и шаг срезов)
WINDOWS = [
    ("1m", 60, 150),
    ("15m", 900, 150),
    ("1h", 3600, 150),
    ("1d", 86400, 300),
    ("7d", 604800, 4200),
]


class State:
    def __init__(self):
        self.raw = None          # (ts, svcmark, q, h, f)
        self.mins = {}           # минута -> [n, dq, dh, df, gap]
        self.hours = {}          # час    -> [n, dq, dh, df, gap]


def _int(s, default=0):
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return default


def load(path):
    st = State()
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return st
    for line in lines:
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        tag = parts[0]
        if tag == "R" and len(parts) >= 6:
            st.raw = (_int(parts[1]), parts[2],
                      _int(parts[3]), _int(parts[4]), _int(parts[5]))
        elif tag in ("M", "H") and len(parts) >= 7:
            ts = _int(parts[1])
            row = [_int(parts[2]), _int(parts[3]), _int(parts[4]),
                   _int(parts[5]), 1 if parts[6] == "1" else 0]
            (st.mins if tag == "M" else st.hours)[ts] = row
    return st


def save(path, st):
    d = os.path.dirname(path) or "."
    os.makedirs(d, mode=0o750, exist_ok=True)
    # Заголовок по-английски: это машинный файл, и его читают рядом с
    # английскими именами полей. Всё, что видит оператор, живёт в меню и
    # панели — они переведены.
    out = ["# goDNS Pro — query statistics. Machine file, do not edit by hand.",
           "# M/H: timestamp  slices  d_queries  d_cache  d_servfail  gap",
           "V\t1"]
    if st.raw:
        ts, mark, q, h, f = st.raw
        out.append("R\t%d\t%s\t%d\t%d\t%d" % (ts, mark, q, h, f))
    for tag, table in (("H", st.hours), ("M", st.mins)):
        for ts in sorted(table):
            n, dq, dh, df, gap = table[ts]
            out.append("%s\t%d\t%d\t%d\t%d\t%d\t%d" % (tag, ts, n, dq, dh, df, gap))
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".stats.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("\n".join(out) + "\n")
        os.chmod(tmp, 0o640)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def rollup(st, now):
    """Поминутные старше суток — в часовые корзины; часовые старше недели — вон.

    Зовётся на каждом срезе, поэтому обычно переносит ноль или одну строку.
    """
    cut_min = now - MIN_HORIZON
    for ts in [t for t in st.mins if t < cut_min]:
        n, dq, dh, df, gap = st.mins.pop(ts)
        hour = ts - ts % 3600
        b = st.hours.setdefault(hour, [0, 0, 0, 0, 0])
        b[0] += n
        b[1] += dq
        b[2] += dh
        b[3] += df
        b[4] |= gap
    cut_hour = now - HOUR_HORIZON
    for ts in [t for t in st.hours if t + 3600 <= cut_hour]:
        del st.hours[ts]


def apply_sample(st, ts, mark, q, h, f):
    prev = st.raw
    st_raw_new = (ts, mark, q, h, f)
    if prev is None:
        # Первый срез — только точка отсчёта: окна для него неизвестны.
        st.raw = st_raw_new
        return
    pts, pmark, pq, ph, pf = prev
    if ts <= pts:
        # Часы ушли назад или повтор той же секунды — не портим историю.
        return
    gap = 1 if ts - pts > GAP_SEC else 0
    marks_known = mark != "-" and pmark != "-"
    if marks_known and mark != pmark:
        # Ловушка 1: служба перезапускалась (или сервер перезагружался) —
        # счётчики законно начались с нуля, дельта = текущее значение.
        dq, dh, df = q, h, f
    elif q < pq:
        # Счётчик упал, а метка старта та же (или мы её не знаем) —
        # объяснить нечем; берём текущее значение и честно помечаем разрыв.
        dq, dh, df = q, h, f
        gap = 1
    else:
        dq, dh, df = q - pq, max(0, h - ph), max(0, f - pf)
    minute = ts - ts % SLICE
    row = st.mins.setdefault(minute, [0, 0, 0, 0, 0])
    row[0] += 1
    row[1] += dq
    row[2] += dh
    row[3] += df
    row[4] |= gap
    st.raw = st_raw_new
    rollup(st, ts)


def cmd_ingest(path):
    st = load(path)
    changed = False
    for line in sys.stdin:
        parts = line.split()
        if len(parts) < 5:
            continue
        apply_sample(st, _int(parts[0]), parts[1],
                     _int(parts[2]), _int(parts[3]), _int(parts[4]))
        changed = True
    if changed:
        save(path, st)
    return 0


def human(n):
    if n < 1000:
        return str(n)
    for div, suf in ((1000 * 1000, "M"), (1000, "k")):
        if n >= div:
            v = n / div
            return ("%.1f%s" if v < 10 else "%.0f%s") % (v, suf)
    return str(n)


def window_stats(st, now):
    """[(имя, raw|None, human, метка full|approx|none, rate, cache%, fails)]"""
    all_rows = [(ts, r) for ts, r in st.mins.items()]
    all_rows += [(ts, r) for ts, r in st.hours.items()]
    oldest = min((ts for ts, _ in all_rows), default=None)
    newest_m = max(st.mins, default=None)
    res = []
    for name, width, slack in WINDOWS:
        if name == "1m":
            # «За минуту» — последний срез, а не сумма по календарной
            # границе: иначе на 59-й секунде цикла таймера окно мигало бы
            # прочерком, хотя данные секундной свежести есть.
            if newest_m is None or newest_m < now - STALE_SEC:
                res.append((name, None, "-", "none", None, None, None))
                continue
            picked = [(newest_m, st.mins[newest_m])]
        else:
            cut = now - width
            picked = [(ts, r) for ts, r in all_rows if ts >= cut]
            if not picked:
                res.append((name, None, "-", "none", None, None, None))
                continue
        n = sum(r[0] for _, r in picked)
        dq = sum(r[1] for _, r in picked)
        dh = sum(r[2] for _, r in picked)
        df = sum(r[3] for _, r in picked)
        mark = "full"
        if any(r[4] for _, r in picked):
            mark = "approx"        # ловушка 2: внутри окна есть разрыв
        if name != "1m":
            if oldest is not None and oldest > now - width + slack:
                mark = "approx"    # данные не достают до начала окна
            if newest_m is None or newest_m < now - STALE_SEC:
                mark = "approx"    # свежих срезов нет — дыра в хвосте окна
        rate = dq / (n * SLICE) if n else None
        cache = int(round(dh * 100.0 / dq)) if dq else None
        res.append((name, dq, human(dq), mark, rate, cache, df))
    return res


def buckets(st, nchars, k, now):
    """Разбивка на N корзин по K минутных срезов.

    Возвращает (end, vals, peak); None в vals — слот без данных.

    Отдельная функция, потому что тем же числам нужны ДВА представления:
    блочная строка для терминала и настоящий график в веб-панели. Считать
    их порознь нельзя — терминал и панель разошлись бы в цифрах, и
    объяснить это расхождение было бы нечем.

    Правый край — ПОСЛЕДНИЙ срез, а не текущая минута: срез текущей минуты
    ещё не снят почти всегда, и привязка к now рисовала бы вечную дыру
    справа.
    """
    if not st.mins:
        return None, [], None
    newest = max(st.mins)
    end = newest + SLICE
    span = nchars * k * SLICE
    vals = []
    for i in range(nchars):
        lo = end - span + i * k * SLICE
        hi = lo + k * SLICE
        cell = [r for ts, r in st.mins.items() if lo <= ts < hi]
        vals.append(sum(r[1] for r in cell) if cell else None)
    peak_rows = [r[1] for ts, r in st.mins.items() if end - span <= ts < end]
    peak = max(peak_rows) if peak_rows else None
    return end, vals, peak


def series_row(st, name, nchars, k, now):
    """Строка отчёта с ЧИСЛАМИ ряда — для графика в панели.

    series <имя> <шаг-секунд> <конец-epoch> <v,v,-,v…> <пик>
    Прочерк вместо значения — слот без данных, ровно как «·» в терминале.
    """
    end, vals, peak = buckets(st, nchars, k, now)
    if end is None:
        return "series\t%s\t-\t-\t-\t-" % name
    body = ",".join("-" if v is None else str(v) for v in vals)
    return "series\t%s\t%d\t%d\t%s\t%s" % (
        name, k * SLICE, end, body, peak if peak is not None else "-")


def sparkline(st, nchars, k, now):
    """N знаков по K минутных срезов; '·' — слот без данных."""
    end, vals, peak = buckets(st, nchars, k, now)
    if end is None:
        return "-", None
    top = max((v for v in vals if v), default=0)
    chars = []
    for v in vals:
        if v is None:
            chars.append(GAP_CHAR)
        elif v == 0 or top == 0:
            chars.append(BARS[0])
        else:
            chars.append(BARS[max(1, int(math.ceil(v * 7.0 / top)))])
    return "".join(chars), peak


def fmt_rate(rate):
    if rate is None:
        return "-"
    if rate >= 100:
        return "%.0f" % rate
    if rate >= 10:
        return "%.1f" % rate
    return "%.2f" % rate


def cmd_report(path, now):
    st = load(path)
    for name, dq, hum, mark, rate, cache, df in window_stats(st, now):
        print("w\t%s\t%s\t%s\t%s\t%s\t%s\t%s" % (
            name,
            dq if dq is not None else "-",
            hum, mark, fmt_rate(rate),
            cache if cache is not None else "-",
            df if df is not None else "-"))
    chars, peak = sparkline(st, 30, 2, now)
    print("spark\t%s\t%s" % (chars, peak if peak is not None else "-"))
    chars, peak = sparkline(st, 48, 30, now)
    print("spark24\t%s\t%s" % (chars, peak if peak is not None else "-"))
    print(series_row(st, "1h", 30, 2, now))
    print(series_row(st, "24h", 48, 30, now))
    newest_m = max(st.mins, default=None)
    if st.raw and st.raw[0] >= (newest_m or 0):
        print("last\t%d" % max(0, now - st.raw[0]))
    elif newest_m:
        print("last\t%d" % max(0, now - newest_m))
    else:
        print("last\t-")
    try:
        with open(path, "rb") as fh:
            data = fh.read()
        print("size\t%d\t%d" % (data.count(b"\n"), len(data)))
    except OSError:
        print("size\t0\t0")
    return 0


def cmd_window(path, name, now):
    st = load(path)
    for wname, dq, _hum, _mark, _rate, _cache, _df in window_stats(st, now):
        if wname == name:
            if dq is None:
                return 1
            print(dq)
            return 0
    return 2


def main(argv):
    if len(argv) < 3:
        print("usage: stats.py ingest|report|window|spark <file> …", file=sys.stderr)
        return 2
    cmd, path = argv[1], argv[2]
    rest = argv[3:]
    now = int(time.time())
    if "--now" in rest:
        i = rest.index("--now")
        v = rest[i + 1] if i + 1 < len(rest) else ""
        if v.strip():
            now = _int(v, now)
        del rest[i:i + 2]
    if cmd == "ingest":
        return cmd_ingest(path)
    if cmd == "report":
        return cmd_report(path, now)
    if cmd == "window":
        if not rest:
            return 2
        return cmd_window(path, rest[0], now)
    if cmd == "spark":
        if not rest:
            return 2
        n = _int(rest[0], 30)
        k = _int(rest[1], 1) if len(rest) > 1 else 1
        st = load(path)
        chars, _peak = sparkline(st, max(1, n), max(1, k), now)
        if chars == "-":
            return 1
        print(chars)
        return 0
    print("stats.py: unknown subcommand %r" % cmd, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
