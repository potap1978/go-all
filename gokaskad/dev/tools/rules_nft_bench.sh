#!/usr/bin/env bash
# rules_nft_bench.sh — замер на настоящем узле перед выбором вида набора gw_ru (WP-R, docs/k3/SPEC-wave3.md §2).
#
# Что меряет (по три вида таблицы: baseline — без большого набора, concat — «ipv4_addr . inet_proto» с интервалами,
# plain — «ipv4_addr» с интервалами и auto-merge; в каждом ~15 тыс. диапазонов России):
#   1. загрузку диапазонов одной транзакцией nft -f (кусками по 5000 элементов, как делает Kaskad);
#   2. «путь dnsmasq»: N отдельных «nft add element ip kaskad_bench gw_ru_dns { 198.18.x.y }» — libnftables перед
#      каждым добавлением наполняет свой кэш; по исследованию он выгружает элементы всех НЕсоставных интервальных
#      наборов во всех таблицах, и при plain каждый ответ DNS дорожает на 0,1–0,3 с;
#   3. чтение таблицы «nft -j list table» (так читает таблицу сторож/сборщик Kaskad) и «nft -j -t list table».
# Таблица ip kaskad и трафик не трогаются: всё в отдельной таблице ip kaskad_bench (цепочка без хука), в конце
# она удаляется (и при Ctrl+C).
#
# Запуск (root):   bash tools/rules_nft_bench.sh                 диапазоны из /var/lib/kaskad/rules/current/ru4.txt,
#                                                                иначе скачать direct.txt RoscomVPN (jsDelivr), иначе
#                                                                синтетические 15 000
#                  bash tools/rules_nft_bench.sh --adds 100      сколько добавлений мерить (по умолчанию 50)
#                  bash tools/rules_nft_bench.sh --ranges FILE   свой файл (строки a.b.c.d/n или a.b.c.d-e.f.g.h)
#                  bash tools/rules_nft_bench.sh --synthetic 30000
# Решение (ИТОГ в конце): вид годится, если его загрузка nft -f укладывается в 30 с (таймаут загрузки Kaskad) и
# «путь dnsmasq» не дороже baseline больше чем на 5 мс; из годных — concat (по умолчанию), иначе plain
# (RU_FORM = "plain" в lib/kaskad/gw/net.py). Время nft -c показывается: если сухой прогон диапазонов дольше 2 с,
# их нельзя прогонять через nft -c — Kaskad и не прогоняет (стенд 05.10.2026, Ubuntu 24.04: concat nft -c 99,7 с,
# nft -f 0,67 с, add 4,9 мс; plain nft -f 0,16 с, add 53 мс).
set -u

if [ "$(id -u)" != "0" ]; then
    echo "Нужен root (nft)." >&2
    exit 1
fi
command -v nft >/dev/null 2>&1 || { echo "Нет nft (пакет nftables)." >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "Нет python3." >&2; exit 1; }

exec python3 - "$@" <<'PY'
import ipaddress
import os
import statistics
import subprocess
import sys
import time
import urllib.request

TABLE = "ip kaskad_bench"
CHUNK = 5000
LOAD_LIMIT = 30          # s: the timeout of Kaskad's own load of the ranges (lib/kaskad/gw/net.py RU_LOAD_TIMEOUT)
CHECK_LIMIT = 2          # s: a dry run (nft -c) of the ranges longer than this — they must never be dry-run
STORE = "/var/lib/kaskad/rules/current/ru4.txt"
DIRECT_TXT = "https://cdn.jsdelivr.net/gh/hydraponique/roscomvpn-geoip/release/text/direct.txt"
SPECS = {"concat": "type ipv4_addr . inet_proto; flags interval;",
         "plain": "type ipv4_addr; flags interval; auto-merge;"}

args = sys.argv[1:]


def opt(name, default=None):
    if name in args:
        i = args.index(name)
        if i + 1 < len(args):
            return args[i + 1]
    return default


def nft(argv, script=None):
    t = time.monotonic()
    p = subprocess.run(["nft"] + argv, input=script, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       universal_newlines=True)
    return p.returncode, time.monotonic() - t, (p.stdout or ""), (p.stderr or "").strip()


def merge(nets):
    spans = sorted((int(n.network_address), int(n.broadcast_address)) for n in nets if n.version == 4)
    out = []
    for a, b in spans:
        if out and a <= out[-1][1] + 1:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def text_of(a, b):
    size = b - a + 1
    ip = ipaddress.IPv4Address
    if size & (size - 1) == 0 and a % size == 0:
        return "%s/%d" % (ip(a), 32 - (size.bit_length() - 1))
    return "%s-%s" % (ip(a), ip(b))


def ranges():
    path = opt("--ranges")
    syn = opt("--synthetic")
    if syn:
        n = int(syn)
        base = int(ipaddress.IPv4Address("5.0.0.0"))
        return ["%s/24" % ipaddress.IPv4Address(base + (i * 2 << 8)) for i in range(n)], "синтетические %d" % n
    for p in ([path] if path else [STORE]):
        if p and os.path.isfile(p):
            with open(p) as f:
                return [ln.strip() for ln in f if ln.strip()], p
    try:
        with urllib.request.urlopen(DIRECT_TXT, timeout=30) as r:
            text = r.read().decode("ascii", "replace")
        nets = []
        for ln in text.split():
            try:
                nets.append(ipaddress.ip_network(ln.strip(), strict=False))
            except ValueError:
                pass
        return [text_of(a, b) for a, b in merge(nets)], "direct.txt RoscomVPN (%d CIDR)" % len(nets)
    except Exception as e:                          # noqa: BLE001
        print("direct.txt не скачался (%s) — беру синтетические 15000" % e)
        base = int(ipaddress.IPv4Address("5.0.0.0"))
        return ["%s/24" % ipaddress.IPv4Address(base + (i * 2 << 8)) for i in range(15000)], "синтетические 15000"


def table_script(form):
    lines = ["table %s" % TABLE, "delete table %s" % TABLE, "table %s {" % TABLE,
             "\tset gw_nets { type ipv4_addr; flags interval; elements = { 10.77.0.0/16 } }",
             "\tset gw_priv { type ipv4_addr; flags interval; elements = { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16 } }",
             "\tset gw_rd { type ipv4_addr; elements = { 10.77.1.10 } }",
             "\tset gw_ru_dns { type ipv4_addr; flags timeout; timeout 6h; }",
             "\tset gw_force { type ipv4_addr; flags timeout; timeout 6h; }"]
    rule = None
    if form != "baseline":
        lines.append("\tset gw_ru { %s }" % SPECS[form])
        match = "ip daddr . meta l4proto @gw_ru" if form == "concat" else "ip daddr @gw_ru"
        rule = "ip saddr @gw_rd %s ip daddr != @gw_force meta mark != 0x2ff meta mark set 0x201" % match
    lines.append("\tchain bench {")                  # no hook: never sees a packet
    if rule:
        lines.append("\t\t" + rule)
    lines.append("\t\tip saddr @gw_rd ip daddr @gw_ru_dns ip daddr != @gw_force meta mark != 0x2ff meta mark set 0x201")
    lines.append("\t}")
    lines.append("}")
    return "\n".join(lines) + "\n"


def elements_script(form, rs):
    els = [("%s . 0-255" % r) if form == "concat" else r for r in rs]
    return "".join("add element %s gw_ru { %s }\n" % (TABLE, ", ".join(els[i:i + CHUNK]))
                   for i in range(0, len(els), CHUNK))


def mem_available():
    try:
        with open("/proc/meminfo") as f:
            for ln in f:
                if ln.startswith("MemAvailable:"):
                    return int(ln.split()[1])
    except OSError:
        pass
    return None


def ms(x):
    return "%.1f" % (x * 1000.0)


def run(form, rs, adds):
    out = {"form": form}
    rc, _, _, err = nft(["-f", "-"], table_script(form))
    if rc != 0:
        out["error"] = "таблица не принята: %s" % err[:300]
        return out
    mem0 = mem_available()
    if form != "baseline":
        script = elements_script(form, rs)
        rc, dt, _, err = nft(["-c", "-f", "-"], script)
        out["check"] = dt
        if rc != 0:
            out["error"] = "диапазоны не приняты (nft -c): %s" % err[:300]
            return out
        rc, dt, _, err = nft(["-f", "-"], script)
        out["load"] = dt
        if rc != 0:
            out["error"] = "диапазоны не загрузились: %s" % err[:300]
            return out
    mem1 = mem_available()
    if mem0 is not None and mem1 is not None:
        out["mem_kb"] = mem0 - mem1
    times = []
    for i in range(adds):
        a = ipaddress.IPv4Address(int(ipaddress.IPv4Address("198.18.0.1")) + i)
        rc, dt, _, err = nft(["add", "element"] + TABLE.split() + ["gw_ru_dns", "{ %s }" % a])
        if rc != 0:
            out["error"] = "add element не прошёл: %s" % err[:200]
            return out
        times.append(dt)
    times.sort()
    out["add_median"] = statistics.median(times)
    out["add_p90"] = times[int(len(times) * 0.9) - 1] if len(times) >= 10 else times[-1]
    lists = []
    for _ in range(3):
        rc, dt, text, err = nft(["-j", "list", "table"] + TABLE.split())
        lists.append(dt)
        out["json_kb"] = len(text) // 1024
    out["list"] = statistics.median(lists)
    rc, dt, _, _ = nft(["-j", "-t", "list", "table"] + TABLE.split())
    out["list_terse"] = dt if rc == 0 else None
    return out


def main():
    adds = int(opt("--adds", "50"))
    rs, where = ranges()
    rc, _, ver, _ = nft(["--version"])
    kernel = os.uname().release
    print("nft: %s, ядро %s" % (ver.strip(), kernel))
    print("диапазонов: %d (%s), добавлений «как dnsmasq»: %d" % (len(rs), where, adds))
    res = []
    try:
        for form in ("baseline", "concat", "plain"):
            print("… %s" % form, flush=True)
            res.append(run(form, rs, adds))
    finally:
        nft(["delete", "table"] + TABLE.split())
    print("")
    print("%-9s %10s %10s %12s %12s %10s %10s %8s %8s" % ("вид", "nft -c, мс", "загрузка", "add медиана",
                                                          "add p90, мс", "list -j", "list -t", "JSON КБ", "память"))
    for r in res:
        if r.get("error"):
            print("%-9s ОШИБКА: %s" % (r["form"], r["error"]))
            continue
        print("%-9s %10s %10s %12s %12s %10s %10s %8s %8s" % (
            r["form"], ms(r["check"]) if "check" in r else "—", ms(r["load"]) if "load" in r else "—",
            ms(r["add_median"]), ms(r["add_p90"]), ms(r["list"]),
            ms(r["list_terse"]) if r.get("list_terse") is not None else "—", r.get("json_kb", "—"),
            ("%d КБ" % r["mem_kb"]) if r.get("mem_kb") is not None else "—"))
    by = dict((r["form"], r) for r in res)
    base = by.get("baseline", {})
    print("")
    verdict = {}
    for form in ("concat", "plain"):
        r = by.get(form, {})
        if r.get("error") or "add_median" not in r:
            verdict[form] = None
            print("%s: не годится — %s" % (form, r.get("error") or "нет замера"))
            continue
        dns = r["add_median"] - base.get("add_median", 0)
        load_ok = r.get("load", 0) <= LOAD_LIMIT
        verdict[form] = {"dns": dns, "load_ok": load_ok}
        print("%s: загрузка nft -f %s мс (%s), цена на ответ DNS %+.1f мс, nft -c %s мс%s, чтение list -j %s мс" % (
            form, ms(r.get("load", 0)), "укладывается в %d с Kaskad" % LOAD_LIMIT if load_ok else
            "НЕ укладывается в %d с Kaskad" % LOAD_LIMIT, dns * 1000.0, ms(r.get("check", 0)),
            " — сухой прогон диапазонов недопустим (Kaskad его и не делает)" if r.get("check", 0) > CHECK_LIMIT
            else "", ms(r["list"])))
    good = [f for f in ("concat", "plain") if verdict.get(f) and verdict[f]["load_ok"] and verdict[f]["dns"] <= 0.005]
    print("")
    if good:
        print("ИТОГ: RU_FORM = \"%s\"%s." % (good[0], " (по умолчанию)" if good[0] == "concat" else
                                             " — поставить в lib/kaskad/gw/net.py"))
        if len(good) == 2:
            print("      plain тоже годится здесь (кэш libnftables этой версии не дорожает) — concat остаётся.")
    else:
        usable = [f for f in ("concat", "plain") if verdict.get(f) and verdict[f]["load_ok"]]
        if not usable:
            print("ИТОГ: ни один вид не загружается за %d с — правила «Россия напрямую» по сетям здесь не включать "
                  "(шлюз работает без них)." % LOAD_LIMIT)
        else:
            best = min(usable, key=lambda f: verdict[f]["dns"])
            print("ИТОГ: каждый ответ dnsmasq дорожает; дешевле %s (%+.1f мс) — RU_FORM = \"%s\", следить за "
                  "нагрузкой dnsmasq." % (best, verdict[best]["dns"] * 1000.0, best))
    print("Kaskad грузит диапазоны отдельной транзакцией nft -f (таймаут %d с) и никогда не прогоняет их через "
          "nft -c; изменения устройств и сторож их не трогают." % LOAD_LIMIT)


main()
PY
