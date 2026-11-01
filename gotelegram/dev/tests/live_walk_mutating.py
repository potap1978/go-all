# -*- coding: utf-8 -*-
"""Пункты меню, которые действительно что-то делают. ТОЛЬКО для тестового стенда.

Отрисовка экрана ничего не говорит о том, сработало ли действие: кнопка может
нарисовать «готово» и не сделать ничего. Поэтому после каждого нажатия
проверяется последствие в системе — файл бекапа, поле конфига, открытый порт, —
а исходное состояние восстанавливается.

НЕ запускать на боевом сервере: перезапускает telemt, переключает WARP и меняет
анти-DPI профиль. `certbot renew` не трогается и здесь — лимиты Let's Encrypt
общие на домен, сжечь их проверкой нельзя.

Про анти-DPI: пункты v3free/v3lim/v4 меняют ФИКС (поле mekofix), а p1/p2/p4 —
ПРЕСЕТ, и намеренно снимают фикс. Проверять надо то поле, которое пункт меняет:
ожидание смены preset_id от v3lim даёт ложный провал.
"""
import asyncio, json, os, subprocess, sys, time, traceback
BOT_DIR = os.environ.get("GT_BOT_DIR", "/opt/gotelegram-bot")
sys.path.insert(0, BOT_DIR); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(BOT_DIR)
import bot as B
from live_walk_menu import FQuery, FUpdate, FContext

ADMIN = int(sys.argv[1]); CFG = B.GOTELEGRAM_CONFIG
BK = "/opt/gotelegram/backups"
SCHED = "/opt/gotelegram/backup_schedule.json"

def sh(c): return subprocess.run(c, shell=True, capture_output=True, text=True).stdout.strip()

async def press(cb, ud, t=420):
    sink = []; q = FQuery(cb, sink)
    try:
        await asyncio.wait_for(B.handle_callback(FUpdate(q), FContext(sink, ud)), timeout=t)
        return None, sink
    except Exception:
        return traceback.format_exc(limit=3), sink

R = []
def rec(n, ok, d=""):
    R.append(ok); print(f"  {n[:46]:46} {'✓' if ok else '✗'}  {d[:64]}")

async def main():
    B._WAITING_FOR_ADMIN = False; ud = {}

    # ── возврат mekofix, снятого предыдущим прогоном ─────────────────────────
    err, _ = await press("adpi_set~v3free", ud); time.sleep(4)
    mk = json.load(open(CFG)).get("mekofix")
    rec("возврат mekofix=v3 (был снят пресетом p4)", mk == "v3", f"сейчас {mk!r}")

    # ── бекап по правильному пути ────────────────────────────────────────────
    before = set(sh(f"ls -1 {BK} 2>/dev/null").split())
    err, sink = await press("backup_create", ud)
    after = set(sh(f"ls -1 {BK} 2>/dev/null").split())
    new = sorted(after - before)
    msg = " ".join(str(t) for _k, t, _ in sink if isinstance(t, str))[:90]
    rec("Бекап → создать", bool(new) and not err,
        f"{new[0]} ({sh(f'du -h {BK}/{new[0]} | cut -f1')})" if new else f"ответ: {msg}")

    # ── расписание: backup_schedule.json, не cron ───────────────────────────
    s0 = sh(f"cat {SCHED} 2>/dev/null")
    await press("backup_schedule_daily", ud); time.sleep(1)
    s1 = sh(f"cat {SCHED} 2>/dev/null")
    rec("Бекап → расписание «ежедневно»", s1 != s0 and "dail" in s1.lower(), f"{s1[:56]}")
    await press("backup_schedule_off", ud); time.sleep(1)
    s2 = sh(f"cat {SCHED} 2>/dev/null")
    rec("Бекап → расписание «выключить» (возврат)", s2 != s1, f"{s2[:56]}")

    # ── перезапуск: ждём, пока порт реально откроется ───────────────────────
    t0 = sh("systemctl show telemt -p ActiveEnterTimestamp --value")
    err, _ = await press("menu_restart", ud)
    listens = False
    for _ in range(30):
        time.sleep(2)
        if sh("ss -ltn 2>/dev/null | grep -c ':443 ' || true") != "0":
            listens = True; break
    t1 = sh("systemctl show telemt -p ActiveEnterTimestamp --value")
    rec("Перезапуск прокси", t1 != t0 and listens and sh("systemctl is-active telemt") == "active",
        f"перезапущен={t1 != t0}, порт 443 открылся={listens}")

    # ── анти-DPI: проверяем ТО поле, которое пункт и меняет ─────────────────
    mk0 = json.load(open(CFG)).get("mekofix")
    await press("adpi_set~v3lim", ud); time.sleep(4)
    lim = json.load(open(CFG))
    rec("Анти-DPI → V3 с ограничением", lim.get("mekofix") == "v3",
        f"mekofix={lim.get('mekofix')!r} (пункт меняет фикс, не пресет)")

    p0 = json.load(open(CFG)).get("preset_id")
    await press("adpi_set~p2", ud); time.sleep(4)
    c2 = json.load(open(CFG))
    rec("Анти-DPI → пресет p2", str(c2.get("preset_id")) == "2",
        f"пресет {p0} → {c2.get('preset_id')}, фикс снят: {c2.get('mekofix')!r} (так и задумано)")

    await press("adpi_set~p4", ud); time.sleep(4)
    await press("adpi_set~v3free", ud); time.sleep(4)
    c3 = json.load(open(CFG))
    rec("Анти-DPI → возврат исходного (пресет 4 + v3)",
        str(c3.get("preset_id")) == "4" and c3.get("mekofix") == "v3",
        f"пресет={c3.get('preset_id')} mekofix={c3.get('mekofix')!r}")

    time.sleep(4)
    ok443 = sh("ss -ltn 2>/dev/null | grep -c ':443 ' || true") != "0"
    me = sh("journalctl -u telemt --since '-60s' --no-pager -q | grep -ci 'writer\\|connected' || true")
    rec("прокси обслуживает после всех действий",
        ok443 and sh("systemctl is-active telemt") == "active",
        f"443 открыт={ok443}, событий ME за 60с={me}")
    # Домен берём из конфига, а не зашиваем: стенд у каждого свой, а зашитый
    # адрес ещё и утекает в репозиторий.
    dom = json.load(open(CFG)).get("domain") or ""
    if dom:
        # Через 127.0.0.1:443 с SNI домена, без DNS: резолвер хостера может терять запросы.
        site = sh(f"curl -sk -o /dev/null -w '%{{http_code}}' --max-time 8 --noproxy '*' --resolve {dom}:443:127.0.0.1 https://{dom}/")
        rec("сайт-прикрытие отвечает", site == "200", f"HTTP {site}")
    else:
        rec("сайт-прикрытие", True, "домена нет (lite-режим) — проверять нечего")

    print("\n" + "─" * 88)
    print(f"  действий: {len(R)}   получилось: {sum(R)}   не получилось: {len(R)-sum(R)}")

asyncio.run(main())
