#!/usr/bin/env python3
"""Строки веб-панели: ключи, подстановки и слова подтверждений.

Тот же класс проверок, что check-str-usage.py для оболочки. Без него
пропущенный ключ показывается ПОЛЬЗОВАТЕЛЮ: `t()` возвращает сам ключ, и
человек видит `ui_bk_col_size` вместо «размер». Ровно та же беда, из-за
которой в SSH-меню однажды печаталось `certs_unusable_pick`.

Проверяется:
  1. каждый t('ключ') в static/** определён в словаре;
  2. каждый {{ключ}} в index.html и на странице входа определён;
  3. ru.json и en.json имеют ОДИН и тот же набор ключей;
  4. слова подтверждения в панели совпадают с CONFIRM_WORDS в server.py —
     иначе кнопка просит одно, а сервер ждёт другое, и подтвердить
     опасное действие нельзя вовсе;
  5. английская подпись не длиннее русской там, где строка стоит в
     колонке фиксированной ширины (ключи *_col_*).
"""
import ast
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
WEB = ROOT / "godns-web"
STATIC = WEB / "static"
LANG = STATIC / "lang"

T_CALL = re.compile(r"\bt\(\s*'([a-z0-9_]+)'")
# Сервер печатает свои строки через wt("ключ") — их тоже надо засчитать,
# иначе они выглядят «определены, но не используются», и настоящий мусор
# в этом шуме не разглядеть.
WT_CALL = re.compile(r'\bwt\(\s*"([a-z0-9_]+)"')
PLACEHOLDER = re.compile(r"\{\{([a-z0-9_]+)\}\}")


def main() -> int:
    bad: list[str] = []

    try:
        ru = json.loads((LANG / "ru.json").read_text(encoding="utf-8"))
        en = json.loads((LANG / "en.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"✗ словарь панели не прочитан: {exc}", file=sys.stderr)
        return 1

    # 3. Наборы ключей совпадают.
    only_ru = sorted(set(ru) - set(en))
    only_en = sorted(set(en) - set(ru))
    for k in only_ru:
        bad.append(f"ключ есть только в ru: {k}")
    for k in only_en:
        bad.append(f"ключ есть только в en: {k}")

    # 1. t('ключ') в статике.
    used: set[str] = set()
    for js in sorted(STATIC.rglob("*.js")):
        # Сам i18n.js исключаем: в его пояснении стоит образец t('key'),
        # и без исключения проверка ругалась бы на собственную документацию.
        if js.parent.name == "lang" or js.name == "i18n.js":
            continue
        for m in T_CALL.finditer(js.read_text(encoding="utf-8")):
            used.add(m.group(1))
            if m.group(1) not in ru:
                bad.append(f"{js.relative_to(ROOT)}: нет ключа {m.group(1)}")

    # 2. {{ключ}} в шаблонах, которые подставляет сервер.
    server_src = (WEB / "server.py").read_text(encoding="utf-8")
    for m in WT_CALL.finditer(server_src):
        used.add(m.group(1))
        if m.group(1) not in ru:
            bad.append(f"server.py: нет ключа {m.group(1)}")
    index_src = (STATIC / "index.html").read_text(encoding="utf-8")
    for where, text in (("index.html", index_src), ("server.py", server_src)):
        for m in PLACEHOLDER.finditer(text):
            key = m.group(1)
            if key == "__lang__":
                continue
            used.add(key)
            if key not in ru:
                bad.append(f"{where}: нет ключа {key} для подстановки")

    # 4. Слова подтверждения совпадают со списком сервера.
    words: dict[str, tuple[str, ...]] = {}
    for node in ast.parse(server_src).body:
        if isinstance(node, ast.Assign) and any(
                getattr(t_, "id", "") == "CONFIRM_WORDS" for t_ in node.targets):
            words = {k: tuple(v) for k, v in ast.literal_eval(node.value).items()}
    if not words:
        bad.append("server.py: CONFIRM_WORDS не найден")
    else:
        accepted = {w for pair in words.values() for w in pair}
        for key in sorted(k for k in ru if "_word_" in k):
            for lang, table in (("ru", ru), ("en", en)):
                if table[key] not in accepted:
                    bad.append(
                        f"{key} [{lang}] = {table[key]!r} — сервер такого слова "
                        f"не принимает")

    # 5. Английская подпись колонки не длиннее русской.
    for key in sorted(k for k in ru if "_col_" in k):
        if len(en[key]) > len(ru[key]):
            bad.append(f"{key}: en «{en[key]}» длиннее ru «{ru[key]}» — "
                       f"колонка фиксированной ширины разъедется")

    # 6. Никто не затеняет импортированную t().
    #
    # ГРАБЛИ, пойманные только запуском: в shell.js стояла локальная
    # `const t = state.section.title…`, и вызов t('ui_loading') двумя
    # строками выше падал в «Cannot access t before initialization» —
    # панель не рисовала НИЧЕГО. А в ui.js параметром `t` называлась
    # отметка времени, и t('ui_unit_sec') оказывался вызовом числа.
    # Синтаксис при этом безупречен, node --check молчит.
    SHADOW = re.compile(r"\b(?:const|let|var)\s+t\s*=|\(\s*t\s*[,)]\s*=>"
                        r"|function\s*\(\s*t\s*[,)]")
    for js in sorted(STATIC.rglob("*.js")):
        if js.parent.name == "lang" or js.name == "i18n.js":
            continue
        src = js.read_text(encoding="utf-8")
        if "i18n.js" not in src:
            continue
        for n, line in enumerate(src.splitlines(), 1):
            if SHADOW.search(line):
                bad.append(f"{js.relative_to(ROOT)}:{n}: имя `t` затеняет "
                           f"функцию строк — вызов t('ключ') сломается")

    unused = sorted(set(ru) - used)
    if bad:
        for b in bad:
            print(f"    {b}", file=sys.stderr)
        print(f"✗ строки панели: {len(bad)} проблем(ы)", file=sys.stderr)
        return 1
    print(f"✓ строки панели: {len(used)} использованных ключей из {len(ru)} "
          f"определённых, ru/en в паритете")
    if unused:
        print(f"ℹ определены, но не использованы ({len(unused)}): "
              + ", ".join(unused[:12]) + ("…" if len(unused) > 12 else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
