#!/usr/bin/env python3
"""goDNS — verify lib/lang/ru.sh and lib/lang/en.sh define the same I18N keys.

Why this exists
---------------
A key defined in one locale but not the other renders at runtime as its own
raw name — the operator sees ``certs_delete_warn_short`` where a sentence
should be. Nothing crashes, nothing is logged; it just ships.

Three classes of defect are caught here:

1. key present in one file, missing in the other;
2. the same key defined TWICE inside one file — bash keeps the last
   assignment silently, so the translation you edited is not the one shown;
3. printf placeholders that differ between locales — ``tf()`` would then
   render the wrong number of arguments in one language only.

Usage:  tools/check-str-parity.py [lang_dir]
Exit:   0 = in parity (or: no locale files exist yet), 1 = a real mismatch.

Stdlib only. Runs on python3.8+ (Ubuntu 22.04 ships 3.10).
"""

import re
import json
import pathlib
import sys
from pathlib import Path

# I18N[key]="value"   — the only form the loader understands.
KEY_RE = re.compile(r"^\s*I18N\[([A-Za-z_][A-Za-z0-9_]*)\]=")

# %s %d %-10s %.2f … but NOT %% (a literal percent sign).
SPEC_RE = re.compile(r"%(?!%)[-+ #0-9.*]*[sdiufeEgGxXoc]")

LOCALES = ("ru", "en")


def parse(path):
    """Return (ordered key list, {key: (line_no, raw_line)})."""
    order = []
    where = {}
    text = path.read_text(encoding="utf-8", errors="replace")
    for lineno, line in enumerate(text.splitlines(), 1):
        m = KEY_RE.match(line)
        if not m:
            continue
        key = m.group(1)
        order.append(key)
        # Keep the FIRST sighting for the duplicate report, so the message
        # points at the definition that is being silently overridden.
        where.setdefault(key, (lineno, line))
    return order, where


def count_specs(line):
    """Number of printf placeholders on a definition line."""
    # Strip the `I18N[key]=` prefix so a key containing digits cannot
    # accidentally be counted.
    value = line.split("=", 1)[1] if "=" in line else line
    return len(SPEC_RE.findall(value))


def main():
    if len(sys.argv) > 1:
        lang_dir = Path(sys.argv[1])
    else:
        lang_dir = Path(__file__).resolve().parent.parent / "lib" / "lang"

    files = {code: lang_dir / (code + ".sh") for code in LOCALES}
    present = [c for c in LOCALES if files[c].is_file()]

    # Degrade, do not crash: during early development the locale files may
    # not exist yet. Nothing to compare is not the same thing as a mismatch.
    if not present:
        print("ℹ no locale files in {} yet — nothing to compare".format(lang_dir))
        print("  (expected: {})".format(", ".join(str(files[c]) for c in LOCALES)))
        return 0

    if len(present) != len(LOCALES):
        missing = [c for c in LOCALES if c not in present]
        for code in missing:
            print("✗ missing locale file: {}".format(files[code]))
        print("  ({} exists — a half-translated UI is worse than none)".format(
            ", ".join(str(files[c]) for c in present)))
        return 1

    rc = 0
    order = {}
    where = {}
    for code in LOCALES:
        order[code], where[code] = parse(files[code])

    if not any(order[c] for c in LOCALES):
        print("✗ locale files exist but define no I18N[...] keys at all")
        print("  checked: {}".format(", ".join(str(files[c]) for c in LOCALES)))
        return 1

    # ── duplicates inside one file (last assignment wins, silently) ──────
    for code in LOCALES:
        seen = {}
        dupes = []
        for key in order[code]:
            seen[key] = seen.get(key, 0) + 1
            if seen[key] == 2:
                dupes.append(key)
        if dupes:
            rc = 1
            print("✗ {}.sh defines {} key(s) twice (bash keeps the LAST one):".format(
                code, len(dupes)))
            for key in sorted(dupes):
                print("    {}  (first at line {})".format(key, where[code][key][0]))

    sets = {c: set(order[c]) for c in LOCALES}

    # ── parity ───────────────────────────────────────────────────────────
    missing_in_en = sets["ru"] - sets["en"]
    missing_in_ru = sets["en"] - sets["ru"]

    if missing_in_en:
        rc = 1
        print("✗ missing in en.sh ({}):".format(len(missing_in_en)))
        for key in sorted(missing_in_en):
            print("    {}  (ru.sh line {})".format(key, where["ru"][key][0]))
    if missing_in_ru:
        rc = 1
        print("✗ missing in ru.sh ({}):".format(len(missing_in_ru)))
        for key in sorted(missing_in_ru):
            print("    {}  (en.sh line {})".format(key, where["en"][key][0]))

    # ── printf arity must agree across locales ───────────────────────────
    both = sets["ru"] & sets["en"]
    for key in sorted(both):
        n_ru = count_specs(where["ru"][key][1])
        n_en = count_specs(where["en"][key][1])
        if n_ru != n_en:
            rc = 1
            print("✗ {}: {} printf arg(s) in ru.sh (line {}), "
                  "{} in en.sh (line {})".format(
                      key, n_ru, where["ru"][key][0],
                      n_en, where["en"][key][0]))

    # ── the bot's own dictionaries ───────────────────────────────────────
    # These are hand-written JSON, outside the .i18n-keys -> build-lang
    # pipeline, so nothing guarded them: a key added to ru.json and
    # forgotten in en.json would show an English user a raw key, and a
    # Russian phrase left in en.json would show them Russian. Both are the
    # exact failure this checker exists to prevent.
    rc = check_bot_dicts(rc)

    if rc == 0:
        print("✓ locales in parity — {} keys in each of ru.sh / en.sh".format(
            len(both)))
    return rc


def check_bot_dicts(rc):
    root = pathlib.Path(__file__).resolve().parent.parent / "godns-bot" / "lang"
    ru_p, en_p = root / "ru.json", root / "en.json"
    if not ru_p.exists() or not en_p.exists():
        # Counting what was inspected: a guard that finds no files must say
        # so, not pass in silence.
        print("✗ bot dictionaries not found under {}".format(root))
        return 1
    ru = json.loads(ru_p.read_text(encoding="utf-8"))
    en = json.loads(en_p.read_text(encoding="utf-8"))
    only_ru, only_en = set(ru) - set(en), set(en) - set(ru)
    if only_ru:
        rc = 1
        print("✗ missing in bot en.json ({}): {}".format(
            len(only_ru), ", ".join(sorted(only_ru)[:8])))
    if only_en:
        rc = 1
        print("✗ missing in bot ru.json ({}): {}".format(
            len(only_en), ", ".join(sorted(only_en)[:8])))
    cyr = [k for k, v in en.items()
           if isinstance(v, str) and any("\u0410" <= c <= "\u044f" for c in v)]
    if cyr:
        rc = 1
        print("✗ Russian text left in bot en.json ({}): {}".format(
            len(cyr), ", ".join(sorted(cyr)[:8])))
    # The bot substitutes with str.format and {0}-style placeholders, not
    # printf. Counting %s here compared 0 against 0 on every key and was
    # green without checking anything. tf() swallows a format error and
    # returns the raw template, so a mismatch shows the user a literal
    # "{0}" or silently drops the value — exactly what this must catch.
    ph = re.compile(r"\{(\d+)\}")
    checked = 0
    for key in sorted(set(ru) & set(en)):
        if not isinstance(ru[key], str) or not isinstance(en[key], str):
            continue
        checked += 1
        s_ru = set(ph.findall(ru[key]))
        s_en = set(ph.findall(en[key]))
        if s_ru != s_en:
            rc = 1
            print("✗ bot {}: placeholders {{{}}} in ru.json, {{{}}} in en.json".format(
                key, ",".join(sorted(s_ru)) or "-", ",".join(sorted(s_en)) or "-"))
    if checked == 0:
        rc = 1
        print("✗ bot dictionaries: no string values inspected")
    if rc == 0:
        print("✓ bot dictionaries in parity — {} keys in each of "
              "ru.json / en.json, {} placeholder set(s) compared".format(
                  len(ru), checked))
    return rc


if __name__ == "__main__":
    sys.exit(main())
