# -*- coding: utf-8 -*-
"""Сверка всех обращений к словарю в SSH-меню с самими словарями.

Прогон экранов ловит только то, что успело отрисоваться при текущем состоянии
сервера: ветку «прокси не установлен» на рабочем сервере не увидеть никогда.
Статическая сверка от состояния не зависит и покрывает все ветки сразу.

t() при промахе печатает сам ключ — пользователь видит `manage_backup` вместо
названия пункта. Ровно это и ищем.
"""
import re, pathlib, sys, collections

ROOT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else '.')

# ── ключи, которые код запрашивает ──────────────────────────────────────────
used = collections.defaultdict(set)          # key -> {файл:строка}
SRC = [ROOT / 'install.sh'] + sorted((ROOT / 'lib').glob('*.sh'))
CALL = re.compile(r'\$\((?:t|tf)\s+([a-zA-Z_][a-zA-Z_0-9]*)')
for f in SRC:
    if not f.exists() or f.name.startswith('lang'):
        continue
    for i, line in enumerate(f.read_text(encoding='utf-8', errors='replace').splitlines(), 1):
        # Комментарий — не вызов: в шапке lib/i18n.sh примеры использования
        # ссылаются на ключи, которых в словаре нет и быть не должно.
        if line.lstrip().startswith('#'):
            continue
        for m in CALL.finditer(line):
            used[m.group(1)].add(f"{f.name}:{i}")

# ── ключи, которые словари определяют ───────────────────────────────────────
DEF = re.compile(r'^\s*I18N\[([a-zA-Z_][a-zA-Z_0-9]*)\]=')
langs = {}
for lf in sorted((ROOT / 'lib' / 'lang').glob('*.sh')):
    langs[lf.stem] = {m.group(1) for m in
                      (DEF.match(l) for l in lf.read_text(encoding='utf-8').splitlines()) if m}

if not langs:
    print("!! словари не найдены в lib/lang/"); sys.exit(2)

print(f"обращений к словарю: {len(used)}   языков: {', '.join(sorted(langs))}")
for name, keys in sorted(langs.items()):
    print(f"  {name}: {len(keys)} ключей")
print()

bad = 0
for name, keys in sorted(langs.items()):
    miss = sorted(k for k in used if k not in keys)
    if miss:
        bad += len(miss)
        print(f"✗ нет в {name} ({len(miss)}):")
        for k in miss:
            print(f"    {k:32} ← {sorted(used[k])[0]}")
    else:
        print(f"✓ {name}: все {len(used)} запрошенных ключей на месте")

# расхождение состава словарей между языками — половина меню на другом языке
allk = set().union(*langs.values())
for name, keys in sorted(langs.items()):
    gap = sorted(allk - keys)
    if gap:
        print(f"\n⚠ в {name} нет {len(gap)} ключей, которые есть в других языках:")
        print("    " + ", ".join(gap[:14]) + (" …" if len(gap) > 14 else ""))

print()
print("ИТОГ: чисто" if not bad else f"ИТОГ: НЕ ПРОШЛО — дыр в словарях: {bad}")
sys.exit(1 if bad else 0)
