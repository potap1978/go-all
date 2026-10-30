# -*- coding: utf-8 -*-
"""Сверка списка файлов bootstrap.sh с содержимым репозитория.

bootstrap.sh качает продукт по ЯВНОМУ списку путей. Забытый в списке файл —
самая неприятная поломка серии: установка проходит «успешно», а модуль на
сервере отсутствует, и это выясняется на живом сервере при первом обращении к
пункту меню. Обратную сторону тоже проверяем: запись о файле, которого в
репозитории нет, приводит к 404 при установке.

Что проверяется:
  1. в FILES и FILES_OPTIONAL нет дублей и пересечений;
  2. КАЖДЫЙ файл репозитория из каталогов продукта перечислен в одном из
     списков (жёсткая ошибка — именно она ловит забытый модуль);
  3. каждая запись списков существует в репозитории. Пока продукт пишется по
     спринтам, ненаписанные файлы попадают в раздел «ожидается» и ошибкой не
     считаются; GOAMNEZIA_BOOTSTRAP_STRICT=1 делает и это ошибкой — так гоняем
     перед релизом.

Исключения (в списки не входят и в проверку не попадают): docs/, tests/,
любые *.md, всё, что начинается с точки (.git, .gitignore, .shellcheckrc),
сам bootstrap.sh (он и есть загрузчик), кэши Python и venv, а также каталог
lib/upstream/xray/ — он лежит в репозитории только провенансом чужих скриптов
и на сервер не едет (см. REPO_ONLY_DIRS ниже). Из правила «*.md не
возим» есть обратное исключение: файлы, на которые ссылается resources[].file в
lib/actions.json, MCP-сервер читает на сервере рядом с собой — они обязаны быть
в FILES под путём goamnezia-mcp/<file> (контракт §5).

Запуск: python3 tests/check_bootstrap_files.py [корень репозитория]
"""
import re
import os
import sys
import pathlib
import posixpath

ROOT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else '.').resolve()
STRICT = os.environ.get('GOAMNEZIA_BOOTSTRAP_STRICT', '') == '1'
BOOTSTRAP = ROOT / 'bootstrap.sh'

if not BOOTSTRAP.exists():
    print("!! нет %s" % BOOTSTRAP)
    sys.exit(2)

text = BOOTSTRAP.read_text(encoding='utf-8')


def read_array(name):
    """Достать из bootstrap.sh массив вида NAME=( "a" "b" )."""
    m = re.search(r'^%s=\(\s*(.*?)^\)' % re.escape(name), text, re.S | re.M)
    if not m:
        return []
    body = m.group(1)
    out = []
    for line in body.splitlines():
        line = line.split('#', 1)[0].strip()
        if not line:
            continue
        out.extend(re.findall(r'"([^"]+)"', line))
    return out


files = read_array('FILES')
optional = read_array('FILES_OPTIONAL')

if not files:
    print("!! не удалось разобрать FILES=() в bootstrap.sh")
    sys.exit(2)

print("FILES: %d   FILES_OPTIONAL: %d" % (len(files), len(optional)))
print()

bad = 0

# ── 1. дубли и пересечения ───────────────────────────────────────────────────
def dups(seq):
    seen, out = set(), []
    for x in seq:
        if x in seen:
            out.append(x)
        seen.add(x)
    return out


for label, seq in (('FILES', files), ('FILES_OPTIONAL', optional)):
    d = dups(seq)
    if d:
        bad += len(d)
        print("✗ дубли в %s: %s" % (label, ", ".join(sorted(set(d)))))
both = sorted(set(files) & set(optional))
if both:
    bad += len(both)
    print("✗ файл есть и в FILES, и в FILES_OPTIONAL: %s" % ", ".join(both))
if not dups(files) and not dups(optional) and not both:
    print("✓ дублей и пересечений в списках нет")

listed = set(files) | set(optional)

# ── 2. каждый файл репозитория перечислен ────────────────────────────────────
# Каталоги продукта: их содержимое едет на сервер целиком.
PRODUCT_DIRS = ['lib', 'goamnezia-bot', 'goamnezia-mcp', 'admin-web']
SKIP_NAMES = {'bootstrap.sh'}
SKIP_DIR_PARTS = {'__pycache__', 'venv', '.venv', 'node_modules'}
# Каталоги, которые живут ТОЛЬКО в репозитории и на сервер не возятся.
# lib/upstream/xray/ — официальные скрипты контейнера amnezia-xray, скачанные из
# репозитория Amnezia. Ядра Xray в продукте нет (решение владельца 09.09.2026),
# каталог сохранён как провенанс и справка (lib/upstream/README.md), поэтому в
# FILES его быть не должно — и «забытым» он тоже не считается.
REPO_ONLY_DIRS = ('lib/upstream/xray/',)


def interesting(rel):
    parts = rel.split('/')
    if any(p.startswith('.') for p in parts):
        return False
    if any(p in SKIP_DIR_PARTS for p in parts):
        return False
    if rel.endswith('.md'):
        return False
    if parts[0] in ('docs', 'tests'):
        return False
    if rel in SKIP_NAMES:
        return False
    if rel.startswith(REPO_ONLY_DIRS):
        return False
    return True


# Файлы-ресурсы из lib/actions.json (resources[].file) — исключение из правила
# «*.md не возим»: MCP-сервер читает их на СЕРВЕРЕ, значит bootstrap обязан их
# привезти, иначе ресурс у агента отвечает ошибкой -32002.
#
# Поле file — путь ОТНОСИТЕЛЬНО КАТАЛОГА СЕРВЕРА, а не корня репозитория
# (контракт §5: сервер читает файл рядом с собой; server.py берёт
# normpath(join(<каталог server.py>, file))). Поэтому "AGENT_GUIDE.md" из
# actions.json — это goamnezia-mcp/AGENT_GUIDE.md в репозитории, и именно такую
# запись ищем в bootstrap.sh. Абсолютный путь сервер читает как есть с диска
# сервера — такой файл bootstrap привезти не может, его только называем.
MCP_DIR = 'goamnezia-mcp'


def resource_repo_path(fil):
    """resources[].file → путь от корня репозитория, каким его разрешит server.py;
    None для абсолютного пути (лежит вне репозитория)."""
    fil = str(fil)
    if posixpath.isabs(fil):
        return None
    return posixpath.normpath(posixpath.join(MCP_DIR, fil))


RESOURCE_FILES = set()
RESOURCE_ABS = set()
_actions = ROOT / 'lib' / 'actions.json'
if _actions.exists():
    import json
    try:
        _d = json.loads(_actions.read_text(encoding='utf-8'))
        for _r in (_d.get('resources') or []):
            if (_r or {}).get('file'):
                _rel = resource_repo_path(_r['file'])
                if _rel is None:
                    RESOURCE_ABS.add(str(_r['file']))
                else:
                    RESOURCE_FILES.add(_rel)
    except Exception:                              # noqa: BLE001 — сверку actions.json делает свой тест
        pass

# Ресурс должен стоять именно в FILES (обязательных): сам server.py обязателен,
# и если файл его ресурса окажется среди необязательных, 404 на нём пройдёт
# строкой «пропущено», установка завершится «успешно», а ресурс у агента будет
# отвечать ошибкой — ровно та поломка, ради которой существует эта проверка.
missing_resources = sorted(f for f in RESOURCE_FILES if f not in files)
if missing_resources:
    bad += len(missing_resources)
    for f in missing_resources:
        where = ("стоит в FILES_OPTIONAL, а нужен в FILES" if f in optional
                 else "не возится bootstrap.sh")
        print("✗ ресурс из actions.json %s: %s" % (where, f))
elif RESOURCE_FILES:
    print("✓ файлы-ресурсы из actions.json перечислены в FILES bootstrap.sh (%d)" % len(RESOURCE_FILES))
if RESOURCE_ABS:
    print("• ресурсы с абсолютным путём (сервер читает их с диска сервера, bootstrap не возит): %s"
          % ", ".join(sorted(RESOURCE_ABS)))

on_disk = []
# файлы в корне репозитория (без рекурсии — там лежат только install.sh,
# bootstrap.sh, каталоги и документация)
for p in sorted(ROOT.iterdir()):
    if p.is_file():
        rel = p.name
        if interesting(rel):
            on_disk.append(rel)
for d in PRODUCT_DIRS:
    base = ROOT / d
    if not base.is_dir():
        continue
    for p in sorted(base.rglob('*')):
        if not p.is_file():
            continue
        rel = p.relative_to(ROOT).as_posix()
        if interesting(rel):
            on_disk.append(rel)

forgotten = sorted(f for f in on_disk if f not in listed)
if forgotten:
    bad += len(forgotten)
    print("✗ файл есть в репозитории, но не перечислен в bootstrap.sh (%d):" % len(forgotten))
    for f in forgotten:
        print("    %s" % f)
else:
    print("✓ все файлы продукта (%d) перечислены в bootstrap.sh" % len(on_disk))

# ── 3. каждая запись существует ──────────────────────────────────────────────
missing = [f for f in files if not (ROOT / f).exists()]
missing_opt = [f for f in optional if not (ROOT / f).exists()]

if STRICT:
    # Строгий режим — про ОБЯЗАТЕЛЬНЫЕ файлы: их отсутствие = 404 и стоп
    # установки. FILES_OPTIONAL — файлы, которых может не быть в ветке (бот,
    # панель), они и в строгом режиме «ожидаются», а не считаются ошибкой:
    # bootstrap их пропускает.
    if missing:
        bad += len(missing)
        print("✗ перечислено в FILES bootstrap.sh, но отсутствует (%d):" % len(missing))
        for f in missing:
            print("    %s" % f)
    else:
        print("✓ каждая обязательная запись FILES существует в репозитории")
    if missing_opt:
        print("• ожидается (необязательные, спринты впереди — %d): %s" % (len(missing_opt), ", ".join(missing_opt)))
else:
    if missing:
        print("• ожидается (обязательные, ещё не написаны — %d):" % len(missing))
        for f in missing:
            print("    %s" % f)
    if missing_opt:
        print("• ожидается (необязательные, спринты впереди — %d): %s%s"
              % (len(missing_opt), ", ".join(missing_opt[:8]),
                 " …" if len(missing_opt) > 8 else ""))
    if not missing and not missing_opt:
        print("✓ каждая запись списков существует в репозитории")
    else:
        print("  (GOAMNEZIA_BOOTSTRAP_STRICT=1 делает это ошибкой — так гоняем перед релизом)")

print()
print("ИТОГ: чисто" if not bad else "ИТОГ: НЕ ПРОШЛО — расхождений: %d" % bad)
sys.exit(1 if bad else 0)
