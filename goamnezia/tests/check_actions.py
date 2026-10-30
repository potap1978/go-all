# -*- coding: utf-8 -*-
"""Сверка lib/actions.json с диспетчером install.sh и с самим собой.

actions.json — единственное описание действий моста: из него строятся
инструменты MCP, подсказки бота и проверки. Любое расхождение с диспетчером
означает либо действие, которое MCP предлагает, а сервер не умеет (агент
получает `unknown_action`), либо наоборот — недокументированную команду.

Что проверяется:
  1. JSON разбирается, есть обязательные разделы;
  2. имена инструментов MCP уникальны (одинаковые молча затирают друг друга
     в tools/list, и один из инструментов просто исчезает);
  3. каждый параметр действия ссылается на существующий param_types;
  4. resources ссылаются на существующее действие ИЛИ на существующий файл
     рядом с сервером MCP (goamnezia-mcp/<file>, контракт §5), prompts имеют
     имена;
  5. если install.sh уже есть — каждое действие встречается веткой `case` в
     теле (_)action_dispatch (в форме `имя)` или `"имя")`, в том числе внутри
     составной метки `a|b)`) и наоборот. Пока install.sh нет — SKIP.

Запуск: python3 tests/check_actions.py [корень репозитория]
"""
import re
import sys
import json
import pathlib
import posixpath
import collections

ROOT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else '.')
ACTIONS = ROOT / 'lib' / 'actions.json'
# Каталог сервера MCP. Поле resources[].file — путь относительно каталога
# server.py, а не корня репозитория (контракт §5: сервер читает файл рядом с
# собой, через normpath(join(<каталог server.py>, file))); абсолютный путь
# сервер читает как есть с диска сервера.
MCP_DIR = 'goamnezia-mcp'

if not ACTIONS.exists():
    print("!! нет %s" % ACTIONS)
    sys.exit(2)

try:
    data = json.loads(ACTIONS.read_text(encoding='utf-8'))
except Exception as exc:                       # noqa: BLE001 — печатаем как есть
    print("!! lib/actions.json не разбирается: %s" % exc)
    sys.exit(2)

bad = 0
actions = data.get('actions') or {}
param_types = data.get('param_types') or {}
resources = data.get('resources') or []
prompts = data.get('prompts') or []

print("действий: %d   типов параметров: %d   ресурсов: %d   подсказок: %d"
      % (len(actions), len(param_types), len(resources), len(prompts)))
print()

if not actions:
    print("✗ раздел actions пуст")
    bad += 1
if not param_types:
    print("✗ раздел param_types пуст")
    bad += 1

# ── 1. уникальность имён MCP ─────────────────────────────────────────────────
# Поле mcp обязано быть у каждого действия, но "mcp": null — законное значение:
# так помечены действия, которые сервер MCP не публикует намеренно (agent-*,
# mcp-http-*, контракт §5): агент не должен уметь выдавать доступы сам себе.
# Отсутствие поля и null различаем строго: без поля server.py публикует
# инструмент под именем, выведенным из cli-имени (совместимость с actions.json
# других продуктов серии), а тесты, README и гайд считают такое действие
# скрытым — забытое поле молча разводит сервер с документацией.
mcp_names = collections.defaultdict(list)
hidden = []
for name, spec in actions.items():
    spec = spec or {}
    if 'mcp' not in spec:
        print("✗ у действия %s нет поля mcp (null, если инструмент не публикуется)" % name)
        bad += 1
        continue
    mcp = spec['mcp']
    if mcp is None:
        hidden.append(name)
        continue
    if not isinstance(mcp, str) or not mcp:
        print("✗ у действия %s поле mcp не строка и не null: %r" % (name, mcp))
        bad += 1
        continue
    mcp_names[mcp].append(name)
dups = {k: v for k, v in mcp_names.items() if len(v) > 1}
if dups:
    bad += len(dups)
    for mcp, owners in sorted(dups.items()):
        print("✗ имя MCP %s занято дважды: %s" % (mcp, ", ".join(sorted(owners))))
else:
    print("✓ имена инструментов MCP уникальны (%d), не публикуются намеренно: %d"
          % (len(mcp_names), len(hidden)))

# ── 2. параметры ссылаются на существующие param_types ───────────────────────
bad_refs = 0
for name, spec in sorted(actions.items()):
    for pname, pspec in ((spec or {}).get('params') or {}).items():
        ref = (pspec or {}).get('ref')
        if not ref:
            print("✗ %s.%s: нет ref на param_types" % (name, pname))
            bad_refs += 1
        elif ref not in param_types:
            print("✗ %s.%s: ref «%s» отсутствует в param_types" % (name, pname, ref))
            bad_refs += 1
bad += bad_refs
if not bad_refs:
    print("✓ все параметры ссылаются на существующие param_types")

# ── 3. ресурсы и подсказки ───────────────────────────────────────────────────
# Ресурс бывает двух видов: обёртка над действием (action) и статический файл
# репозитория (file, например гайд для агента). Требовать action от второго
# бессмысленно, а не проверять файл — значит отдать агенту пустой ресурс.
bad_res = 0
for res in resources:
    res = res or {}
    uri = res.get('uri', '?')
    if not res.get('uri'):
        print("✗ ресурс без uri")
        bad_res += 1
    act, fil = res.get('action'), res.get('file')
    if act and fil:
        print("✗ ресурс %s задан и действием, и файлом — выберите одно" % uri)
        bad_res += 1
    elif act:
        if act not in actions:
            print("✗ ресурс %s ссылается на несуществующее действие «%s»" % (uri, act))
            bad_res += 1
    elif fil:
        fil = str(fil)
        if posixpath.isabs(fil):
            # Абсолютный путь сервер читает с диска СЕРВЕРА: в репозитории его
            # проверить нечем, а на машине разработчика — бессмысленно.
            print("• ресурс %s указывает на абсолютный путь %s — в репозитории не проверяется" % (uri, fil))
        else:
            # Сервер MCP читает файл ресурса рядом с собой (goamnezia-mcp/,
            # контракт §5) — normpath(join(...)) как в server.py, чтобы «../x»
            # проверялся там же, где его будет искать сервер, а не в корне
            # репозитория.
            rel = posixpath.normpath(posixpath.join(MCP_DIR, fil))
            if not (ROOT / rel).is_file():
                print("✗ ресурс %s ссылается на отсутствующий файл «%s»" % (uri, rel))
                bad_res += 1
    else:
        print("✗ ресурс %s не задан ни действием, ни файлом" % uri)
        bad_res += 1
for pr in prompts:
    if not (pr or {}).get('name'):
        print("✗ подсказка без имени")
        bad_res += 1
bad += bad_res
if not bad_res:
    print("✓ ресурсы и подсказки согласованы с действиями")

# ── 4. диспетчер install.sh ──────────────────────────────────────────────────
install = ROOT / 'install.sh'
if not install.exists():
    print("• SKIP: install.sh ещё не написан — сверка с case-диспетчером пропущена")
else:
    text = install.read_text(encoding='utf-8', errors='replace')
    # Ищем ветки ТОЛЬКО внутри тела диспетчера. В install.sh десятки других
    # case (состояния контейнера, компоненты логов, пункты меню), и по всему
    # файлу выборка дала бы и ложные «лишние ветки», и ложные совпадения.
    body = text
    for fname in ('_action_dispatch_locked', 'action_dispatch'):
        m = re.search(r'^%s\(\)\s*\{(.*?)^\}' % re.escape(fname), text, re.S | re.M)
        if m:
            body = m.group(1)
            break
    else:
        print("• диспетчер (_)action_dispatch не найден — ищу ветки по всему install.sh")
    # Ветка выглядит как  имя-действия)  или  "имя-действия")  — кавычки
    # необязательны, обе формы в bash равнозначны. Составная метка
    # a|b|c) тоже разбирается: иначе действия b и c числились бы забытыми.
    in_case = set()
    for m in re.finditer(r'^\s*("?[a-z0-9][a-z0-9|"-]*"?)\s*\)', body, re.M):
        for part in m.group(1).split('|'):
            part = part.strip().strip('"')
            if part:
                in_case.add(part)
    missing = sorted(a for a in actions if a not in in_case)
    extra = sorted(c for c in in_case if c not in actions and '-' in c)
    if missing:
        bad += len(missing)
        print("✗ нет ветки в диспетчере install.sh (%d): %s" % (len(missing), ", ".join(missing)))
    else:
        print("✓ каждое действие из actions.json есть в диспетчере")
    if extra:
        bad += len(extra)
        print("✗ в диспетчере есть, а в actions.json нет (%d): %s"
              % (len(extra), ", ".join(extra)))
    else:
        print("✓ лишних веток в диспетчере нет")

# ── 5. источники логов: enum моста и список install.sh — один список ─────────
# Имя компонента отсеивает мост по param_types.component.enum, а человеку в SSH
# список называет GA_LOG_SOURCES (строка «Показать могу: …»). Разойдись они —
# и одна половина продукта обещала бы то, чего другая не умеет.
if install.exists():
    m = re.search(r'^GA_LOG_SOURCES="([^"]*)"', install.read_text(encoding='utf-8', errors='replace'), re.M)
    enum = list((param_types.get('component') or {}).get('enum') or [])
    if not m:
        print("✗ в install.sh нет GA_LOG_SOURCES — списку источников логов негде быть")
        bad += 1
    elif sorted(m.group(1).split()) != sorted(enum):
        print("✗ GA_LOG_SOURCES (%s) ≠ param_types.component.enum (%s)"
              % (m.group(1), " ".join(enum)))
        bad += 1
    else:
        print("✓ источники логов: GA_LOG_SOURCES и enum моста совпадают (%d)" % len(enum))

print()
print("ИТОГ: чисто" if not bad else "ИТОГ: НЕ ПРОШЛО — расхождений: %d" % bad)
sys.exit(1 if bad else 0)
