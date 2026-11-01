# -*- coding: utf-8 -*-
"""Сверка всех обращений к словарю в SSH-меню с самими словарями.

Прогон живых экранов ловит только то, что успело отрисоваться при текущем
состоянии сервера: ветку «профиль не установлен» на рабочем сервере не увидеть
никогда. Статическая сверка от состояния не зависит и покрывает все ветки сразу.

t() при промахе печатает сам ключ — пользователь видит `menu_protocols` вместо
названия пункта. Ровно это и ищем.

Что проверяется:
  1. дубликаты ключей внутри языка (ядро словаря против фрагментов и фрагменты
     между собой) — победитель зависел бы от порядка source, то есть от ФС;
  2. паритет наборов ключей ru и en — иначе половина меню на другом языке;
  3. каждый ключ, запрошенный кодом, есть в обоих языках;
  4. число подстановок (%s/%d) в строке одинаково во всех языках — иначе tf()
     на одном языке подставит аргументы, а на другом напечатает их мимо текста;
  5. число подстановок в строке словаря совпадает с числом аргументов у вызова
     tf. Это printf: лишний аргумент печатается ХВОСТОМ после текста, а
     недостающий превращается в пустоту — «Удалить профиль ?» вместо имени.
     Ни то, ни другое ничего не ломает, поэтому доехало бы до человека молча.
  6. каждый файл словаря грузится настоящим bash без единой строки в stderr, и
     все ключи, которые файл определяет, после загрузки на месте: разбор
     регуляркой не видит строку, которую bash режет по кавычке (T1, 25.09.2026).

Аргументы вызова считаются разбором строки, а не делением по пробелам: внутри
живут $( ), ${ }, кавычки и обратные слэши, и `tf ask_del "$(profile_name "$id")"`
— это ОДИН аргумент, а не три. Строку, которую разбор прочитать не смог, считаем
ошибкой: непонятый вызов — это ровно та дыра, в которой расхождение и спрячется.

Модули, у которых фрагмента словаря ещё нет (пишутся в других спринтах),
попадают в раздел «ожидается»: требовать перевод от файла, которого автор ещё
не написал, бессмысленно. Как только появляется lib/lang/ru.d/<модуль>.sh,
ключи этого модуля начинают проверяться жёстко. GOAMNEZIA_I18N_STRICT=1 делает
жёсткой и эту часть — так гоняем перед релизом.

Запуск: python3 tests/check_i18n_ssh.py [корень репозитория]
"""
import re
import os
import sys
import pathlib
import collections
import subprocess

ROOT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else '.')
STRICT = os.environ.get('GOAMNEZIA_I18N_STRICT', '') == '1'

# Модули, чьи строки живут в ядре словаря или в фрагменте, который уже обязан
# существовать. Для них «ключа нет» — всегда ошибка.
CORE_MODULES = {'common', 'i18n', 'deps', 'logbudget', 'backup', 'selftest',
                'install', 'bootstrap'}

# Ключи, которых в словаре быть НЕ должно: selftest намеренно спрашивает
# несуществующий ключ, чтобы убедиться, что t() печатает его имя.
DELIBERATE_MISS = re.compile(r'^no_such_key')

# ── ключи, которые код запрашивает ───────────────────────────────────────────
# Две формы вызова: подстановка "$(t key)" и голая команда `t key` в начале
# строки или после ; && || ( {. Вторая нужна, потому что tf иногда зовут ради
# самого печатающего эффекта, без захвата вывода.
CALL_SUBST = re.compile(r'\$\((?:t|tf)\s+([A-Za-z_][A-Za-z_0-9]*)')
CALL_BARE = re.compile(r'(?:^|[;&|({]|\bthen\b|\bdo\b|\belse\b)\s*(?:t|tf)\s+'
                       r'([A-Za-z_][A-Za-z_0-9]*)')

# Вызовы tf для счёта аргументов: та же пара форм, но без t — у него аргументов
# не бывает по определению. Первая группа непуста, если это подстановка $(tf …).
CALL_TF = re.compile(r'(?:(\$\()|(?:^|[;&|(){}]|\bthen\b|\bdo\b|\belse\b)\s*)'
                     r'tf\s+([A-Za-z_][A-Za-z_0-9]*)')

used = collections.defaultdict(set)          # key -> {"файл:строка"}
key_module = {}                              # key -> модуль первого вхождения


# ── разбор аргументов вызова tf ──────────────────────────────────────────────
# Своё деление на слова, потому что shlex по bash не работает: ему неизвестны ни
# $( ), ни ${ }, и `"$(ga_users_name "$id")"` он развалил бы на куски. Нам нужно
# ровно одно число — сколько СЛОВ идёт после ключа до конца вызова, — поэтому
# содержимое подстановок мы не разбираем, а перепрыгиваем целиком.
_SEP = ' \t'
_ENDERS = ';&|<>'


def _skip_dquote(text, i):
    """i — на открывающей двойной кавычке; вернуть позицию ЗА закрывающей."""
    i += 1
    while i < len(text):
        c = text[i]
        if c == '\\':
            i += 2
        elif c == '"':
            return i + 1
        elif c == '$' and text[i + 1:i + 2] == '(':
            i = _skip_subst(text, i + 1)
            if i is None:
                return None
        elif c == '`':
            j = text.find('`', i + 1)
            if j < 0:
                return None
            i = j + 1
        else:
            i += 1
    return None


def _skip_subst(text, i):
    """i — на '(' конструкции $( или обычной подоболочки; вернуть позицию ЗА ')'."""
    i += 1
    while i < len(text):
        c = text[i]
        if c == '\\':
            i += 2
        elif c == "'":
            j = text.find("'", i + 1)
            if j < 0:
                return None
            i = j + 1
        elif c == '"':
            i = _skip_dquote(text, i)
            if i is None:
                return None
        elif c == '(':
            i = _skip_subst(text, i)
            if i is None:
                return None
        elif c == ')':
            return i + 1
        else:
            i += 1
    return None


def _skip_brace(text, i):
    """i — на '{' конструкции ${ ; вернуть позицию ЗА парной '}'."""
    i += 1
    depth = 1
    while i < len(text):
        c = text[i]
        if c == '\\':
            i += 2
            continue
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return None


def count_args(text, start):
    """Сколько аргументов у вызова, начиная с позиции сразу за ключом.

    None — строку разобрать не удалось (незакрытая кавычка или скобка).
    """
    i, words, in_word = start, 0, False
    while i < len(text):
        c = text[i]
        if c in _SEP:
            in_word = False
            i += 1
            continue
        # Конец вызова: закрывающая скобка подстановки, разделитель команд,
        # перенаправление или начало комментария на месте нового слова.
        if c == ')' or c in _ENDERS or (c == '#' and not in_word):
            return words
        if not in_word:
            words += 1
            in_word = True
        if c == '\\':
            i += 2
        elif c == "'":
            j = text.find("'", i + 1)
            if j < 0:
                return None
            i = j + 1
        elif c == '"':
            i = _skip_dquote(text, i)
            if i is None:
                return None
        elif c == '$' and text[i + 1:i + 2] == '(':
            i = _skip_subst(text, i + 1)
            if i is None:
                return None
        elif c == '$' and text[i + 1:i + 2] == '{':
            i = _skip_brace(text, i + 1)
            if i is None:
                return None
        elif c == '`':
            j = text.find('`', i + 1)
            if j < 0:
                return None
            i = j + 1
        else:
            i += 1
    return words


def logical_lines(text):
    """Строки с уже склеенными переносами «\\» — вызов tf бывает и в две строки."""
    buf, start = '', 0
    for i, line in enumerate(text.splitlines(), 1):
        if not buf:
            start = i
        if line.endswith('\\'):
            buf += line[:-1]
            continue
        yield start, buf + line
        buf = ''
    if buf:
        yield start, buf


def n_subst(fmt):
    """Сколько подстановок съест printf: %% — это просто знак процента."""
    n, i = 0, 0
    while i < len(fmt):
        if fmt[i] != '%':
            i += 1
            continue
        if fmt[i + 1:i + 2] == '%':
            i += 2
            continue
        n += 1
        i += 2
    return n

SRC = [ROOT / 'install.sh', ROOT / 'bootstrap.sh'] + sorted((ROOT / 'lib').glob('*.sh'))
tf_calls = []       # (файл, строка, ключ, число аргументов или None, сам вызов)
for f in SRC:
    if not f.exists():
        continue
    module = f.stem
    text = f.read_text(encoding='utf-8', errors='replace')
    for i, line in enumerate(text.splitlines(), 1):
        # Комментарий — не вызов: в шапке lib/i18n.sh примеры использования
        # ссылаются на ключи, которых в словаре нет и быть не должно.
        if line.lstrip().startswith('#'):
            continue
        for rx in (CALL_SUBST, CALL_BARE):
            for m in rx.finditer(line):
                key = m.group(1)
                if DELIBERATE_MISS.match(key):
                    continue
                used[key].add("%s:%d" % (f.name, i))
                key_module.setdefault(key, module)
    # Аргументы считаем по СКЛЕЕННЫМ строкам: вызов с переносом «\» иначе
    # выглядел бы обрубком и дал бы ложное расхождение.
    for i, line in logical_lines(text):
        if line.lstrip().startswith('#'):
            continue
        for m in CALL_TF.finditer(line):
            key = m.group(2)
            if DELIBERATE_MISS.match(key):
                continue
            tf_calls.append((f.name, i, key, count_args(line, m.end()), line.strip()))

# ── ключи, которые словари определяют ────────────────────────────────────────
DEF = re.compile(r'^\s*I18N\[([A-Za-z_][A-Za-z_0-9]*)\]=')
# Значение строкой в кавычках на одной строке — так написан весь словарь.
# Не подошедшее под этот вид в подсчёт подстановок не берём (см. ниже unread).
DEF_VAL = re.compile(r'^\s*I18N\[([A-Za-z_][A-Za-z_0-9]*)\]="(.*)"\s*$')
LANG_DIR = ROOT / 'lib' / 'lang'

langs = {}          # lang -> {key: [файлы]}
fragments = {}      # lang -> [имена фрагментов]
values = {}         # lang -> {key: строка формата или None, если прочитать не вышло}
if LANG_DIR.is_dir():
    for core in sorted(LANG_DIR.glob('*.sh')):
        lang = core.stem
        files = [core] + sorted((LANG_DIR / (lang + '.d')).glob('*.sh'))
        table = collections.defaultdict(list)
        vals = {}
        for lf in files:
            for line in lf.read_text(encoding='utf-8').splitlines():
                m = DEF.match(line)
                if m:
                    table[m.group(1)].append(lf.name)
                    mv = DEF_VAL.match(line)
                    vals[m.group(1)] = mv.group(2) if mv else None
        langs[lang] = table
        values[lang] = vals
        fragments[lang] = [p.name for p in files[1:]]

if not langs:
    print("!! словари не найдены в lib/lang/")
    sys.exit(2)

print("обращений к словарю: %d   языков: %s" % (len(used), ', '.join(sorted(langs))))
for name in sorted(langs):
    frag = fragments.get(name) or []
    print("  %s: %d ключей (ядро + фрагментов: %d%s)"
          % (name, len(langs[name]), len(frag),
             (' — ' + ', '.join(frag)) if frag else ''))
print()

bad = 0
pending = collections.defaultdict(list)

# ── 1. дубликаты внутри языка ────────────────────────────────────────────────
for name in sorted(langs):
    dups = {k: v for k, v in langs[name].items() if len(v) > 1}
    if dups:
        bad += len(dups)
        print("✗ %s: ключ объявлен дважды (%d):" % (name, len(dups)))
        for k in sorted(dups)[:20]:
            print("    %-32s ← %s" % (k, ', '.join(dups[k])))
    else:
        print("✓ %s: дублей ключей нет" % name)

# ── 2. паритет наборов ключей ────────────────────────────────────────────────
allk = set()
for table in langs.values():
    allk |= set(table)
for name in sorted(langs):
    gap = sorted(allk - set(langs[name]))
    if gap:
        bad += len(gap)
        print("✗ в %s нет %d ключей, которые есть в других языках:" % (name, len(gap)))
        print("    " + ", ".join(gap[:14]) + (" …" if len(gap) > 14 else ""))
    else:
        print("✓ %s: набор ключей совпадает с другими языками" % name)

# ── 3. все запрошенные ключи на месте ────────────────────────────────────────
def is_pending(key):
    """Ключ модуля, у которого фрагмента словаря пока нет вовсе."""
    if STRICT:
        return False
    mod = key_module.get(key, '')
    if mod in CORE_MODULES:
        return False
    return not (LANG_DIR / 'ru.d' / (mod + '.sh')).exists() \
        and not (LANG_DIR / 'en.d' / (mod + '.sh')).exists()

for name in sorted(langs):
    keys = set(langs[name])
    miss = sorted(k for k in used if k not in keys)
    hard = [k for k in miss if not is_pending(k)]
    soft = [k for k in miss if is_pending(k)]
    for k in soft:
        pending[key_module.get(k, '?')].append(k)
    if hard:
        bad += len(hard)
        print("✗ нет в %s (%d):" % (name, len(hard)))
        for k in hard:
            print("    %-32s ← %s" % (k, sorted(used[k])[0]))
    else:
        print("✓ %s: все запрошенные ключи на месте (%d)" % (name, len(used) - len(soft)))

# ── 4. число подстановок одинаково во всех языках ────────────────────────────
REF = 'ru' if 'ru' in values else sorted(values)[0]
diff = []
for name in sorted(values):
    if name == REF:
        continue
    for key, fmt in sorted(values[name].items()):
        ref = values[REF].get(key)
        if fmt is None or ref is None:
            continue
        if n_subst(fmt) != n_subst(ref):
            diff.append((key, name, n_subst(ref), n_subst(fmt)))
if diff:
    bad += len(diff)
    print("✗ число подстановок в языках разное (%d):" % len(diff))
    for key, name, a, b in diff[:20]:
        print("    %-32s %s: %d, %s: %d" % (key, REF, a, name, b))
else:
    print("✓ число подстановок одинаково во всех языках")

# ── 5. число подстановок в строке ↔ число аргументов вызова tf ───────────────
# printf не ругается ни на лишний аргумент (он печатается хвостом), ни на
# недостающий (на его месте пустота) — расхождение молча доезжает до человека.
mismatch, unread = [], []
for fname, lineno, key, nargs, src in tf_calls:
    fmt = values[REF].get(key)
    if fmt is None:              # ключа ещё нет (фрагмент не написан) — §3 это уже сказал
        continue
    if nargs is None:
        unread.append((fname, lineno, key, src))
        continue
    if n_subst(fmt) != nargs:
        mismatch.append((fname, lineno, key, n_subst(fmt), nargs, src))
if mismatch:
    bad += len(mismatch)
    print("✗ аргументы tf не сходятся со строкой словаря (%d):" % len(mismatch))
    for fname, lineno, key, want, got, src in mismatch[:20]:
        print("    %s:%d %s — подстановок %d, аргументов %d" % (fname, lineno, key, want, got))
        print("        %s" % src[:150])
if unread:
    bad += len(unread)
    print("✗ вызов tf разобрать не удалось (%d) — упростите строку или поправьте разбор здесь:" % len(unread))
    for fname, lineno, key, src in unread[:20]:
        print("    %s:%d %s: %s" % (fname, lineno, key, src[:150]))
if not mismatch and not unread:
    print("✓ аргументы всех вызовов tf сходятся со строкой словаря (%d)" % len(tf_calls))

# ── 6. каждый файл словаря грузится в bash, и все его ключи на месте ─────────
# Разбор выше читает словари регуляркой и не видит строку, которую bash режет по
# кавычке: частичная замена строки с \" оставила в en.d/menu.sh хвост после
# закрывающей кавычки — bash печатал «not a valid identifier» и «command not
# found» на КАЖДОМ запуске меню на английском, а ключ пропадал (находка T1,
# 25.09.2026, была и в выпущенной 0.4.2). Здесь файл грузит настоящий bash:
# stderr обязан быть пустым, а каждый ключ, который файл определяет, — загружен.
def _find_bash4():
    import shutil
    for cand in (os.environ.get('BASH5'), '/opt/homebrew/bin/bash', '/usr/local/bin/bash', shutil.which('bash')):
        if not cand or not os.path.exists(cand):
            continue
        try:
            ver = subprocess.run([cand, '-c', 'echo "${BASH_VERSINFO[0]}"'], capture_output=True,
                                 text=True, timeout=10).stdout.strip()
        except Exception:
            continue
        if ver.isdigit() and int(ver) >= 4:
            return cand
    return None


BASH4 = _find_bash4()
if BASH4 is None:
    print("– загрузка словарей в bash пропущена: нет bash 4+ (задайте BASH5)")
else:
    LOAD = ('declare -A I18N; source "$1" || exit 3; '
            'for k in "${!I18N[@]}"; do printf "%s\\n" "$k"; done')
    # stderr здесь обязан быть пустым — поэтому без LANG и LC_* машины: CI с локалью, которой в образе
    # нет, получал бы от каждого bash «setlocale: cannot change locale». На Linux — C.UTF-8.
    BASH_ENV = {k: v for k, v in os.environ.items() if k != 'LANG' and not k.startswith('LC_')}
    if sys.platform.startswith('linux'):
        BASH_ENV['LANG'] = 'C.UTF-8'
    broken = []
    nfiles = 0
    for lang in sorted(langs):
        for lf in [LANG_DIR / (lang + '.sh')] + sorted((LANG_DIR / (lang + '.d')).glob('*.sh')):
            nfiles += 1
            r = subprocess.run([BASH4, '-c', LOAD, '_', str(lf)], capture_output=True, timeout=60, env=BASH_ENV)
            err = r.stderr.decode('utf-8', 'replace').strip()
            if r.returncode != 0 or err:
                broken.append((lf, 'rc=%d: %s' % (r.returncode, (err.splitlines() or [''])[0][:160])))
                continue
            loaded = set(r.stdout.decode('utf-8', 'replace').split())
            want = [m.group(1) for m in (DEF.match(x) for x in lf.read_text(encoding='utf-8').splitlines()) if m]
            lost = [k for k in want if k not in loaded]
            if lost:
                broken.append((lf, 'после загрузки нет ключей: ' + ', '.join(lost[:6])))
    if broken:
        bad += len(broken)
        print("✗ словарь не грузится в bash чисто (%d):" % len(broken))
        for lf, why in broken[:20]:
            print("    %s — %s" % (lf.relative_to(ROOT), why))
    else:
        print("✓ все файлы словарей (%d) грузятся в bash без ошибок, ключи на месте" % nfiles)

if pending:
    print()
    print("• ожидается (фрагмент словаря модуля ещё не написан):")
    for mod in sorted(pending):
        keys = sorted(set(pending[mod]))
        print("    lib/lang/{ru,en}.d/%s.sh — %d ключей: %s%s"
              % (mod, len(keys), ", ".join(keys[:6]), " …" if len(keys) > 6 else ""))
    print("    (GOAMNEZIA_I18N_STRICT=1 делает это ошибкой — так гоняем перед релизом)")

print()
print("ИТОГ: чисто" if not bad else "ИТОГ: НЕ ПРОШЛО — дыр в словарях: %d" % bad)
sys.exit(1 if bad else 0)
