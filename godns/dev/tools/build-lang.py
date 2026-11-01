#!/usr/bin/env python3
"""Собирает lib/lang/{ru,en}.sh из .i18n-keys/*.tsv.

Источник истины — TSV: одна строка = ключ + русский + английский. Так
расхождение ru/en по составу ключей структурно невозможно: они рождаются
из одной и той же строки. check-str-parity.py всё равно проверяет результат —
на случай, если кто-то поправит .sh руками.
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / ".i18n-keys"
DST = ROOT / "lib" / "lang"

# Строки ВЕБ-ПАНЕЛИ живут в отдельных файлах и собираются в JSON для
# страницы, а не в таблицу оболочки: их читает браузер, а не bash.
# Источник истины при этом тот же — TSV, где русский и английский стоят в
# ОДНОЙ строке, поэтому разойтись по составу ключей они не могут.
WEB_PREFIX = "webui-ui"
WEB_DST = ROOT / "godns-web" / "static" / "lang"

HEADER = """#!/bin/bash
# goDNS — Copyright (c) 2026 anten-ka. All rights reserved.
#
# ЭТОТ ФАЙЛ СГЕНЕРИРОВАН. Руками не править.
# Источник: .i18n-keys/*.tsv  →  python3 tools/build-lang.py
# shellcheck disable=SC2034,SC2148
"""


def esc(value: str) -> str:
    # Значение попадает в I18N[k]="..." — экранируем то, что раскрыл бы bash.
    for a, b in (("\\", "\\\\"), ('"', '\\"'), ("$", "\\$"), ("`", "\\`")):
        value = value.replace(a, b)
    return value


def main() -> int:
    if not SRC.is_dir():
        print(f"нет каталога {SRC}", file=sys.stderr)
        return 1

    rows: dict[str, tuple[str, str, str]] = {}
    web_rows: dict[str, tuple[str, str, str]] = {}
    dupes: list[str] = []
    bad: list[str] = []

    for tsv in sorted(SRC.glob("*.tsv")):
        for n, line in enumerate(tsv.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) != 3:
                bad.append(f"{tsv.name}:{n}: полей {len(parts)}, нужно 3")
                continue
            key, ru, en = (p.strip() for p in parts)
            if not key:
                bad.append(f"{tsv.name}:{n}: пустой ключ")
                continue
            target = web_rows if tsv.name.startswith(WEB_PREFIX) else rows
            if key in web_rows or key in rows:
                dupes.append(f"{key}: повтор в {tsv.name}")
                continue
            target[key] = (tsv.name, ru, en)

    if bad:
        for b in bad:
            print(f"ОШИБКА {b}", file=sys.stderr)
        return 1
    if dupes:
        for d in dupes:
            print(f"ОШИБКА дубль ключа {d}", file=sys.stderr)
        return 1

    DST.mkdir(parents=True, exist_ok=True)
    for lang, idx in (("ru", 1), ("en", 2)):
        out = [HEADER, ""]
        current = None
        for key in sorted(rows, key=lambda k: (rows[k][0], k)):
            origin = rows[key][0]
            if origin != current:
                current = origin
                out.append(f"\n# ── {origin.removesuffix('.tsv')} " + "─" * max(0, 52 - len(origin)))
            out.append(f'I18N[{key}]="{esc(rows[key][idx])}"')
        (DST / f"{lang}.sh").write_text("\n".join(out) + "\n", encoding="utf-8")

    if web_rows:
        WEB_DST.mkdir(parents=True, exist_ok=True)
        for lang, idx in (("ru", 1), ("en", 2)):
            data = {k: web_rows[k][idx] for k in sorted(web_rows)}
            (WEB_DST / f"{lang}.json").write_text(
                json.dumps(data, ensure_ascii=False, indent=1,
                           sort_keys=True) + "\n", encoding="utf-8")

    print(f"собрано {len(rows)} ключей → lib/lang/ru.sh, lib/lang/en.sh")
    if web_rows:
        print(f"собрано {len(web_rows)} ключей панели → "
              f"godns-web/static/lang/ru.json, en.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
