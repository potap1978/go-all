# -*- coding: utf-8 -*-
"""Сплошная проверка меню бота: у каждой кнопки есть обработчик, у каждой
подписи есть перевод в обоих языках, каждый экран достижим с главного.

Мёртвая кнопка в Telegram ничем себя не выдаёт: нажатие просто не даёт ответа,
и понять это можно только ткнув в неё руками. Поэтому проверяем механически.
"""
import json, pathlib, re, sys

BOT = pathlib.Path(__file__).resolve().parent.parent / 'gotelegram-bot'
src = (BOT / 'bot.py').read_text(encoding='utf-8')
ru = json.loads((BOT / 'lang/ru.json').read_text(encoding='utf-8'))
en = json.loads((BOT / 'lang/en.json').read_text(encoding='utf-8'))

fail = []

# ── 1. все callback_data, которые реально рисуются на кнопках ───────────────
raw = re.findall(r'callback_data=(f?)"([^"]+)"', src)
static, dynamic = set(), set()
for isf, val in raw:
    if '{' in val:
        dynamic.add(val.split('{')[0].rstrip('~_') + ('~' if '~' in val else '_'))
    else:
        static.add(val)

# ── 2. что умеет роутер ─────────────────────────────────────────────────────
m = re.search(r'handlers = \{(.*?)\n    \}', src, re.S)
routed = set(re.findall(r'"([a-z_0-9]+)":', m.group(1))) if m else set()
# ветки вида `elif data == "x"` и `data.startswith("y")` — тоже маршруты
routed |= set(re.findall(r'data == "([a-z_0-9]+)"', src))
prefixes = set(re.findall(r'data\.startswith\("([a-z_0-9~]+)"\)', src))
# служебное, обрабатывается отдельно и кнопкой не является
SPECIAL = {"noop", "menu_main", "close_menu"}

print("── 1. кнопки без обработчика ──")
dead = sorted(x for x in static - routed - SPECIAL
              if not any(x.startswith(p) for p in prefixes))
if dead:
    for d in dead:
        print(f"   ✗ {d}")
    fail.append(f"мёртвых кнопок: {len(dead)}")
else:
    print(f"   ✓ все {len(static)} статических кнопок имеют обработчик")

print("── 2. динамические кнопки (с параметром) ──")
for d in sorted(dynamic):
    ok = any(d.startswith(p) or p.startswith(d[:-1]) for p in prefixes)
    print(f"   {'✓' if ok else '✗'} {d}")
    if not ok:
        fail.append(f"нет префикс-маршрута: {d}")

# ── 3. переводы ─────────────────────────────────────────────────────────────
print("── 3. подписи без перевода ──")
keys = set(re.findall(r'_t\(user_id, "([a-z_0-9]+)"\)', src)) | \
       set(re.findall(r'_t\(_uid\(update\), "([a-z_0-9]+)"\)', src))
missing = sorted(k for k in keys if k not in ru or k not in en)
if missing:
    for k in missing:
        where = [] if k in ru else ['ru']
        if k not in en: where.append('en')
        print(f"   ✗ {k} — нет в {'/'.join(where)}")
    fail.append(f"без перевода: {len(missing)}")
else:
    print(f"   ✓ все {len(keys)} ключей переведены в обоих языках")

# ── 4. достижимость: каждый экран-раздел выходит с главного ─────────────────
print("── 4. достижимость разделов с главного экрана ──")
mm = re.search(r'def get_main_menu\(.*?\n    return InlineKeyboardMarkup\(rows\)', src, re.S)
from_main = set(re.findall(r'callback_data="([a-z_0-9]+)"', mm.group(0))) if mm else set()
for want in ("menu_users", "sec_state", "menu_antidpi", "sec_service", "sec_settings"):
    ok = want in from_main
    print(f"   {'✓' if ok else '✗'} {want}")
    if not ok:
        fail.append(f"раздел недостижим с главного: {want}")

# ── 5. у каждого экрана-раздела есть выход назад ────────────────────────────
print("── 5. возврат из разделов ──")
for fn in ("cb_sec_state", "cb_sec_service", "cb_sec_settings", "cb_sub_site", "cb_sub_panel"):
    body = re.search(rf'async def {fn}\(.*?(?=\nasync def |\ndef )', src, re.S)
    ok = bool(body) and '_back_row' in body.group(0)
    print(f"   {'✓' if ok else '✗'} {fn}")
    if not ok:
        fail.append(f"нет возврата: {fn}")

print()
if fail:
    print("ИТОГ: НЕ ПРОШЛО — " + "; ".join(fail))
    sys.exit(1)
print("ИТОГ: всё чисто")
