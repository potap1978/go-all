# -*- coding: utf-8 -*-
"""Сверка быстрого пути панели (lib/core_read.py) с ядром — за секунду, без bash и стенда.

lib/core_read.py отвечает на частые чтения панели (user-list, user-show, user-key, share-list, sub-list, sub-show,
page-list) тем же JSON, что мост, повторяя места ядра (MIRROR: ветки диспетчера, функции и переменные модулей bash,
обвязка CLI awgkeys.py и shortlinks.py, описания действий в lib/actions.json).
Отпечатки этих мест записаны после сверки (PLACES, RECORDED). Правка такого места без сверки модуля на сервере
безопасна — быстрый путь его действий выключается сам, и чтение идёт мостом, — но тихо возвращает панели прежние
2–3 с на карточку. Этот check ловит это сразу после правки или слияния, а не только в полном прогоне: называет
изменённые места и действия, показывает их дифф с ревизии сверки (если есть git) и что делать дальше.

Запуск: python3 tests/check_core_read.py [корень репозитория]   (в run_static.sh — среди сверок check_*.py)
"""
import difflib
import importlib.util
import os
import subprocess
import sys

UPDATE_CMD = "python3 lib/core_read.py --fingerprint --write"
PARITY_CMD = "BASH5=/opt/homebrew/bin/bash python3 -m unittest tests/test_core_read.py  (на сервере — без BASH5)"


def load(root):
    spec = importlib.util.spec_from_file_location("ga_core_read_check", os.path.join(root, "lib", "core_read.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def git(root, *args):
    try:
        p = subprocess.run(["git", "-C", root] + list(args), capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return p.stdout if p.returncode == 0 else None


def sync_revision(root, cr, files):
    """Ревизия, на которой места ядра совпадают с записанными (PLACES), — среди последних правок lib/core_read.py."""
    revs = git(root, "log", "--format=%H", "-n", "20", "--", "lib/core_read.py")
    for rev in (revs or "").split():
        places = {}
        for rel in files:
            text = git(root, "show", "%s:%s" % (rev, rel))
            if text is None:
                break
            places[rel] = cr.index_source(rel, text)
        else:
            if all(cr.place_hash(*k.split("|"), places[k.split("|")[0]].get(tuple(k.split("|")[1:]))) == v
                   for k, v in cr.PLACES.items()):
                return rev, places
    return None, None


def check(root, out=print):
    """Печатает итог через out; возвращает число действий, у которых быстрый путь сейчас выключен."""
    cr = load(root)
    core = cr.CoreRead(root, environ={})
    now = core.place_hashes()
    want = dict(cr.PLACES)
    rec_now = core.fingerprints()
    off = [a for a in cr.ACTIONS if not cr.RECORDED.get(a) or rec_now.get(a) != cr.RECORDED.get(a)]
    changed = sorted(k for k in set(now) | set(want) if now.get(k) != want.get(k))
    if not off and not changed:
        out("✓ места ядра, которые повторяет lib/core_read.py (%d), не менялись после сверки — быстрый путь панели "
            "включён для всех %d действий" % (len(now), len(cr.ACTIONS)))
        return 0
    out("✗ ядро поменялось в местах, которые повторяет lib/core_read.py: быстрый путь панели для %s выключен —"
        % ", ".join(off or cr.ACTIONS))
    out("  на сервере эти чтения пойдут мостом (верно, но снова 2–3 с на карточку)")
    for k in changed:
        item = tuple(k.split("|"))
        acts = [a for a, items in cr.MIRROR.items() if item in items]
        what = "нет места в ядре" if not now.get(k) else ("новое место, не сверено" if not want.get(k) else "изменилось")
        out("    %s %s %s — %s (%s)" % (item + (", ".join(acts), what)))
    files = sorted({k.split("|")[0] for k in changed if "|" in k})
    rev, old = (sync_revision(root, cr, sorted({rel for items in cr.MIRROR.values() for rel, _, _ in items}))
                if want else (None, None))
    if rev:
        out("  Что изменилось с ревизии сверки %s:" % rev[:10])
        for k in changed:
            rel, kind, name = k.split("|")
            a = (old.get(rel, {}).get((kind, name)) or "").split("\n")
            b = (core._places(rel) or {}).get((kind, name)) or ""
            for line in difflib.unified_diff(a, b.split("\n"), "%s:%s (сверено)" % (rel, name),
                                             "%s:%s (сейчас)" % (rel, name), lineterm="", n=1):
                out("    " + line)
    elif files:
        out("  Ревизия сверки не нашлась (нет git или отпечатки записаны вне коммита) — смотрите правки в: %s"
            % ", ".join(files))
    out("  Что делать:")
    out("    1. Меняет ли правка ответ действия — поля, их порядок, слова, отказы? Да — повторите её в lib/core_read.py")
    out("       (только правка текста отказа ответ быстрого пути не меняет: отказы он отдаёт мосту).")
    out("    2. Равенство с мостом на стенде: %s" % PARITY_CMD)
    out("    3. Обновите отпечатки: %s" % UPDATE_CMD)
    return len(off) or len(changed)


if __name__ == "__main__":
    root = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else ".")
    n = check(root)
    print()
    print("ИТОГ: чисто" if not n else "ИТОГ: НЕ ПРОШЛО — быстрый путь выключен, действий: %d" % n)
    sys.exit(1 if n else 0)
