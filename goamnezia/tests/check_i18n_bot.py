# -*- coding: utf-8 -*-
"""Сверка обращений Telegram-бота к словарю с самими словарями lang/{ru,en}.json.

t() при промахе печатает сам ключ — человек видит `menu_users` вместо названия
раздела. Ищем это заранее, по AST исходника (без импорта бота):

  1. каждый ключ из литеральных вызовов _t(…, "key") / _tf / _t_in есть в обоих
     языках;
  2. семейства ключей вида "cmd_desc_%s" % x резолвятся по кортежам строк
     модуля (BOT_COMMANDS, PRESETS, PROFILE_IDS…): все члены семейства есть;
  3. контейнеры ключей — кортежи/списки/значения словарей, где хотя бы один
     элемент является ключом словаря, обязаны состоять из ключей целиком
     (так ловятся units[idx] и {"on": "state_enabled", …});
  4. наборы ключей ru и en совпадают, и число %s в каждом ключе одинаково —
     иначе tf() на одном языке подставит аргументы, а на другом нет;
  5. ключ, который не встречается в bot.py ни литералом, ни через семейство,
     — мёртвый (ошибка: словарь растёт молча).

Запуск: python3 tests/check_i18n_bot.py [корень репозитория]
"""
import ast
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else '.').resolve()
BOT_DIR = ROOT / 'goamnezia-bot'
BOT = BOT_DIR / 'bot.py'
if not BOT.exists():
    print("!! нет %s" % BOT)
    sys.exit(2)

src = BOT.read_text(encoding='utf-8')
tree = ast.parse(src, filename=str(BOT))
langs = {}
for p in sorted((BOT_DIR / 'lang').glob('*.json')):
    try:
        langs[p.stem] = json.loads(p.read_text(encoding='utf-8'))
    except ValueError as e:
        print("!! %s: невалидный JSON: %s" % (p.name, e))
        sys.exit(2)
if len(langs) < 2:
    print("!! словари не найдены в %s" % (BOT_DIR / 'lang'))
    sys.exit(2)

allk = set()
for d in langs.values():
    allk |= set(d)

T_FUNCS = {'_t', '_tf', '_t_in'}
KEY_RE = re.compile(r'^[a-z][a-z0-9_]*$')

# ── 1. литеральные вызовы ────────────────────────────────────────────────────
# Словарь обслуживает ВЕСЬ пакет бота, а не один bot.py: помощники вроде
# склонения дней живут в i18n.py и зовут t/tf под собственными именами. Пока
# проверка смотрела в один файл, такие ключи объявлялись «мёртвыми», и честный
# способ пройти её был один — вернуть текст без склонения.
SOURCES = [(BOT, T_FUNCS), (BOT_DIR / 'i18n.py', {'t', 'tf', 't_in'})]
used = {}
families = {}
for path, funcs in SOURCES:
    if not path.exists():
        continue
    ptree = tree if path == BOT else ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    for node in ast.walk(ptree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in funcs:
            if len(node.args) >= 2:
                arg = node.args[1]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    used.setdefault(arg.value, "%s:%d" % (path.name, node.lineno))
                elif isinstance(arg, ast.BinOp) and isinstance(arg.op, ast.Mod) \
                        and isinstance(arg.left, ast.Constant) and isinstance(arg.left.value, str):
                    families.setdefault(arg.left.value, node.lineno)
for node in ast.walk(tree):
    # семейства могут собираться и вне вызова: PROFILE_TITLE_KEY = {p: "pr_%s" % p …}
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod) \
            and isinstance(node.left, ast.Constant) and isinstance(node.left.value, str):
        tpl = node.left.value
        if re.match(r'^[a-z][a-z0-9_]*_%s$', tpl):
            families.setdefault(tpl, node.lineno)

# ── 2. кортежи строк модуля для семейств ─────────────────────────────────────
tuples = {}
for node in tree.body:
    if isinstance(node, ast.Assign) and isinstance(node.value, (ast.Tuple, ast.List)) \
            and node.targets and isinstance(node.targets[0], ast.Name):
        elts = node.value.elts
        if elts and all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in elts):
            tuples[node.targets[0].id] = [e.value for e in elts]

family_keys = set()
family_notes = []
family_bad = []
for tpl in sorted(families):
    matched = None
    for tname, members in tuples.items():
        keys = [tpl % m for m in members]
        if all(k in allk for k in keys):
            matched = (tname, keys)
            break
    if matched:
        family_keys |= set(matched[1])
        family_notes.append("%s ← %s (%d)" % (tpl, matched[0], len(matched[1])))
    else:
        family_bad.append("%s (bot.py:%d)" % (tpl, families[tpl]))

# ── 3. контейнеры ключей ─────────────────────────────────────────────────────
container_bad = []
container_keys = set()
for node in ast.walk(tree):
    elts = []
    if isinstance(node, (ast.Tuple, ast.List)):
        elts = node.elts
    elif isinstance(node, ast.Dict):
        elts = node.values
    strs = [e.value for e in elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    if len(strs) < 2 or len(strs) != len(elts):
        continue
    if not any(s in allk for s in strs):
        continue
    for s in strs:
        if KEY_RE.match(s) and s not in allk:
            container_bad.append("%s (bot.py:%d)" % (s, node.lineno))
        elif s in allk:
            container_keys.add(s)

print("литеральных обращений: %d   семейств: %d   ключей из контейнеров: %d"
      % (len(used), len(families), len(container_keys)))
for n, d in sorted(langs.items()):
    print("  %s: %d ключей" % (n, len(d)))
print()

bad = 0
for n, d in sorted(langs.items()):
    miss = sorted(k for k in used if k not in d)
    if miss:
        bad += len(miss)
        print("✗ нет в %s (%d):" % (n, len(miss)))
        for k in miss:
            print("    %-32s ← %s" % (k, used[k]))
    else:
        print("✓ %s: все литеральные ключи на месте (%d)" % (n, len(used)))

if family_bad:
    bad += len(family_bad)
    print("✗ семейства ключей без полного набора в словарях: %s" % ", ".join(family_bad))
else:
    print("✓ семейства ключей: %s" % ("; ".join(family_notes) or "нет"))

if container_bad:
    bad += len(container_bad)
    print("✗ элементы контейнеров ключей, которых нет в словарях: %s" % ", ".join(container_bad))
else:
    print("✓ контейнеры ключей целиком в словарях")

# ── 4. паритет и число %s ────────────────────────────────────────────────────
for n, d in sorted(langs.items()):
    gap = sorted(allk - set(d))
    if gap:
        bad += len(gap)
        print("✗ в %s нет %d ключей, которые есть в другом языке: %s%s"
              % (n, len(gap), ", ".join(gap[:16]), " …" if len(gap) > 16 else ""))
    else:
        print("✓ %s: набор ключей совпадает с другими языками" % n)

names = sorted(langs)
ph_bad = []
for k in sorted(allk):
    counts = set(str(langs[n].get(k, '')).count('%s') for n in names if k in langs[n])
    if len(counts) > 1:
        ph_bad.append(k)
if ph_bad:
    bad += len(ph_bad)
    print("✗ разное число %%s между языками: %s" % ", ".join(ph_bad))
else:
    print("✓ число %s одинаково во всех языках")

# ── 5. мёртвые ключи ─────────────────────────────────────────────────────────
referenced = set(used) | family_keys | container_keys
literals = set(n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str))
dead = sorted(k for k in allk if k not in referenced and k not in literals)
if dead:
    bad += len(dead)
    print("✗ ключи словаря, которых bot.py не использует (%d): %s" % (len(dead), ", ".join(dead)))
else:
    print("✓ мёртвых ключей нет")

print()
print("ИТОГ: чисто" if not bad else "ИТОГ: НЕ ПРОШЛО — дыр: %d" % bad)
sys.exit(1 if bad else 0)
