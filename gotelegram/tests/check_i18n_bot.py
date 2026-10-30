# -*- coding: utf-8 -*-
"""Сверка всех обращений к словарю бота — во всех формах вызова.

Прежняя проверка искала только `_t(user_id, "…")` и пропускала `_tf`, `_t_in`
и вызовы с другим первым аргументом. Ключ, запрошенный в непокрытой форме,
на экране выглядит как голое `users_btn_export` — проверка при этом молчит.
"""
import json, pathlib, re, sys

ROOT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else '.')
src = (ROOT / 'bot.py').read_text(encoding='utf-8')
langs = {p.stem: json.loads(p.read_text(encoding='utf-8'))
         for p in sorted((ROOT / 'lang').glob('*.json'))}
if not langs:
    print("!! словари не найдены в lang/"); sys.exit(2)

# любая форма: _t(что_угодно, "key"), _tf(…, "key"), _t_in(lang, "key")
CALL = re.compile(r'\b_t(?:f|_in)?\(\s*[^,()]*(?:\([^()]*\))?[^,()]*,\s*"([a-z_0-9]+)"')
used = {}
for i, line in enumerate(src.splitlines(), 1):
    if line.lstrip().startswith('#'):
        continue
    for m in CALL.finditer(line):
        used.setdefault(m.group(1), i)

# у части вызовов есть default= — такой ключ не обязан быть в словаре
WITH_DEFAULT = set(re.findall(r'\b_t(?:f|_in)?\(\s*[^,()]*,\s*"([a-z_0-9]+)"\s*,\s*default=', src))

print(f"обращений к словарю: {len(used)}   из них с запасным текстом: {len(WITH_DEFAULT)}")
for n, d in sorted(langs.items()):
    print(f"  {n}: {len(d)} ключей")
print()

bad = 0
for n, d in sorted(langs.items()):
    miss = sorted(k for k in used if k not in d and k not in WITH_DEFAULT)
    soft = sorted(k for k in used if k not in d and k in WITH_DEFAULT)
    if miss:
        bad += len(miss)
        print(f"✗ нет в {n} ({len(miss)}):")
        for k in miss:
            print(f"    {k:30} ← bot.py:{used[k]}")
    else:
        print(f"✓ {n}: все обязательные ключи на месте")
    if soft:
        print(f"  · {n}: {len(soft)} ключей идут по запасному тексту: {', '.join(soft[:8])}"
              + (" …" if len(soft) > 8 else ""))

allk = set().union(*(set(d) for d in langs.values()))
for n, d in sorted(langs.items()):
    gap = sorted(allk - set(d))
    if gap:
        bad += len(gap)
        print(f"\n✗ в {n} нет {len(gap)} ключей, которые есть в другом языке — "
              f"часть меню останется на чужом языке:")
        print("    " + ", ".join(gap[:16]) + (" …" if len(gap) > 16 else ""))

print()
print("ИТОГ: чисто" if not bad else f"ИТОГ: НЕ ПРОШЛО — дыр: {bad}")
sys.exit(1 if bad else 0)
