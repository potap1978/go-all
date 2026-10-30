#!/usr/bin/env python3
"""goDNS — every t()/tf() key used in the shell sources must actually exist.

Why this exists
---------------
``t()`` prints the key itself when the string is missing. That is a deliberate
choice — a blank line is invisible, a raw key is not — but it means a typo
ships silently and the operator reads ``certs_delete_warn_short`` instead of a
sentence. This tool is the gate that stops it.

What it reports
---------------
ERROR (exit 1)
    a key is used in a *.sh file but defined in no locale file.

WARNING (exit 0)
    * a key is defined but never used  — dead weight, or a rename half-done;
    * a key whose text contains printf placeholders is called with plain
      ``t`` — the placeholders reach the operator as literal ``%s``;
    * a key with no placeholders is called with ``tf`` — bash printf REPEATS
      the format string once per extra argument, so the line prints twice.

Keys looked up through a variable (``$(t "$state")``) cannot be resolved
statically. Mark them so this tool stops calling them unused, with a comment
anywhere in the sources:

    # i18n-dynamic: eng_state_running eng_state_stopped

Usage:  tools/check-str-usage.py [project_root]
Exit:   0 = every used key is defined, 1 = at least one is not.

Stdlib only. Runs on python3.8+ (Ubuntu 22.04 ships 3.10).
"""

import json
import pathlib
import re
import sys
from pathlib import Path

DEF_RE = re.compile(r"^\s*I18N\[([A-Za-z_][A-Za-z0-9_]*)\]=(.*)$")

# %s %d %-10s %.2f … but NOT %% (a literal percent sign).
SPEC_RE = re.compile(r"%(?!%)[-+ #0-9.*]*[sdiufeEgGxXoc]")

# Where a command may start in shell: line start, after a separator, after
# an opening $( or backtick, or after then/else/do.
#
# Only horizontal whitespace is allowed between the context and the call —
# \s would match newlines and let `do` at end of line reach into the next
# statement, inventing usages that are not there.
_CTX = r"(?:^|[;|&(){}`!)]|\bthen\b|\belse\b|\bdo\b)"

# The trailing lookahead is load-bearing. Without it, `$(t "policy_${policy}")`
# — a key ASSEMBLED at runtime — matches as the literal key `policy_`, and the
# tool then demands a string nobody will ever define. Forbidding `$` after the
# name is not enough on its own: the regex would simply backtrack and report
# `policy` instead. Forbidding a word character too makes every partial match
# fail, so a composed key yields nothing here and is counted as dynamic below.
USE_RE = re.compile(
    _CTX + r"[ \t]*(tf?)[ \t]+[\"']?([A-Za-z_][A-Za-z0-9_]*)(?![A-Za-z0-9_$])",
    re.M,
)
# $(t "$var") — resolvable only at runtime; counted, never blamed.
DYN_RE = re.compile(_CTX + r"[ \t]*(tf?)[ \t]+[\"']?\$", re.M)
# $(t "policy_${policy}") — a fixed prefix plus a runtime suffix.
COMPOSED_RE = re.compile(_CTX + r"[ \t]*(tf?)[ \t]+[\"'][A-Za-z_][A-Za-z0-9_]*\$", re.M)

DYNAMIC_DECL_RE = re.compile(r"#\s*i18n-dynamic:\s*(.+)$")

SKIP_DIRS = {".git", ".i18n-keys", "node_modules", "dist"}


def strip_noise(text):
    """Drop whole-line comments and any line that DEFINES a key.

    Whole-line comments carry usage examples (this project documents them in
    the file headers) and would otherwise be counted as real calls. Inline
    comments are left alone on purpose: `#` inside a double-quoted string is
    an ordinary character, and stripping from the first `#` mangles real code.
    """
    out = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            out.append("")
            continue
        if "I18N[" in line:
            out.append("")
            continue
        out.append(line)
    return "\n".join(out)


def unquote(value):
    value = value.strip()
    # Trailing inline comment after a quoted value, e.g.  ="x"   # note
    for quote in ('"', "'"):
        if value.startswith(quote):
            end = value.rfind(quote)
            if end > 0:
                return value[1:end]
    return value


def collect_definitions(lang_dir):
    """{key: value} merged across every locale file present."""
    defined = {}
    files = sorted(lang_dir.glob("*.sh")) if lang_dir.is_dir() else []
    for path in files:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            m = DEF_RE.match(line)
            if m:
                # ru.sh wins over en.sh only for the placeholder count, and
                # check-str-parity.py already proves the counts agree.
                defined.setdefault(m.group(1), unquote(m.group(2)))
    return defined, files


def iter_sources(root):
    for path in sorted(root.rglob("*.sh")):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        # Locale files are definitions, not usage.
        if path.parent.name == "lang":
            continue
        # Test suites reference undefined keys on purpose, to prove the
        # fallback prints the key.
        if path.name.startswith("test_"):
            continue
        yield path


def main():
    root = Path(sys.argv[1] if len(sys.argv) > 1
                else Path(__file__).resolve().parent.parent)
    lang_dir = root / "lib" / "lang"

    defined, lang_files = collect_definitions(lang_dir)

    used = {}        # key -> {"t": set(files), "tf": set(files)}
    dynamic_hits = 0
    composed_hits = 0
    declared_dynamic = set()
    scanned = 0

    for path in iter_sources(root):
        scanned += 1
        raw = path.read_text(encoding="utf-8", errors="replace")
        rel = str(path.relative_to(root))

        for line in raw.splitlines():
            m = DYNAMIC_DECL_RE.search(line)
            if m:
                declared_dynamic.update(m.group(1).split())

        body = strip_noise(raw)
        dynamic_hits += len(DYN_RE.findall(body))
        composed_hits += len(COMPOSED_RE.findall(body))
        for func, key in USE_RE.findall(body):
            slot = used.setdefault(key, {"t": set(), "tf": set()})
            slot[func].add(rel)

    if not lang_files:
        if not used:
            print("ℹ no locale files in {} and no t()/tf() calls yet "
                  "— nothing to check".format(lang_dir))
            print("  ({} shell file(s) scanned)".format(scanned))
            return 0
        print("✗ no locale files in {}, but {} key(s) are already used:".format(
            lang_dir, len(used)))
        for key in sorted(used):
            files = sorted(used[key]["t"] | used[key]["tf"])
            print("    {}  ← {}".format(key, ", ".join(files)))
        return 1

    rc = 0

    # ── ERROR: used but never defined ────────────────────────────────────
    missing = {k: v for k, v in used.items() if k not in defined}
    if missing:
        rc = 1
        print("✗ {} key(s) used but defined in no locale file:".format(len(missing)))
        for key in sorted(missing):
            files = sorted(missing[key]["t"] | missing[key]["tf"])
            print("    {}  ← {}".format(key, ", ".join(files)))

    # ── WARNING: printf arity mismatch at the call site ──────────────────
    for key in sorted(k for k in used if k in defined):
        n = len(SPEC_RE.findall(defined[key]))
        slot = used[key]
        if n > 0 and slot["t"]:
            print("⚠ {}: text has {} printf placeholder(s) but is called with "
                  "plain t() — the operator will read a literal %s".format(key, n))
            print("    fix: tf {} <arg…>   ← {}".format(
                key, ", ".join(sorted(slot["t"]))))
        if n == 0 and slot["tf"]:
            print("⚠ {}: text has no placeholders but is called via tf() — "
                  "bash printf repeats the format for every extra argument".format(key))
            print("    ← {}".format(", ".join(sorted(slot["tf"]))))

    # ── WARNING: defined but never used ──────────────────────────────────
    unused = set(defined) - set(used) - declared_dynamic
    if unused:
        print("ℹ {} key(s) defined but never used: {}".format(
            len(unused), ", ".join(sorted(unused))))

    if rc == 0:
        print("✓ all {} used key(s) are defined "
              "({} defined, {} shell file(s) scanned, "
              "{} dynamic + {} composed lookup(s) skipped)".format(
                  len(used), len(defined), scanned, dynamic_hits, composed_hits))
    return rc




# ═══════════════════════════════════════════════════════════════════════
# СЛОВАРЬ БОТА
# ═══════════════════════════════════════════════════════════════════════
# Единственная строковая поверхность проекта без инструмента: у меню есть
# эта проверка, у панели — check-webui-strings, а у бота не было ничего.
# i18n.t() при промахе возвращает САМ КЛЮЧ, то есть опечатка доезжает до
# пользователя Telegram сырым `claim_hintt`. Паритет ru/en её не ловит:
# ключа нет ни там, ни там.
BOT_CALL = re.compile(r'\b_?tf?\(\s*[A-Za-z_][A-Za-z0-9_]*\s*,\s*'
                      r'(["\'])([a-z0-9_]+)\1')
BOT_DYNAMIC = re.compile(r'\b_?tf?\(\s*[A-Za-z_][A-Za-z0-9_]*\s*,\s*'
                         r'(?!["\'])[^)\n]')


def check_bot_strings() -> int:
    root = pathlib.Path(__file__).resolve().parent.parent
    langdir = root / "godns-bot" / "lang"
    ru = langdir / "ru.json"
    if not ru.exists():
        print("✗ словарь бота godns-bot/lang/ru.json не найден")
        return 1
    defined = set(json.loads(ru.read_text(encoding="utf-8")))
    files = sorted((root / "godns-bot").glob("*.py"))
    if not files:
        print("✗ исходников бота не найдено — проверка не состоялась")
        return 1

    used, composed, dynamic, scanned = {}, {}, 0, 0
    for f in files:
        scanned += 1
        for lineno, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            for m in BOT_CALL.finditer(line):
                tail = line[m.end():].lstrip()
                if tail.startswith("+"):
                    # Составной ключ: `_t(uid, "bk_freq_" + freq)`. Целиком
                    # его отсюда не собрать, но семейство обязано
                    # существовать: переименовали префикс — поймаем.
                    composed.setdefault(m.group(2), []).append(
                        "{}:{}".format(f.name, lineno))
                    continue
                used.setdefault(m.group(2), []).append(
                    "{}:{}".format(f.name, lineno))
            dynamic += len(BOT_DYNAMIC.findall(line))

    if not used:
        print("✗ в исходниках бота не найдено ни одного обращения к строке — "
              "шаблон поиска сломан")
        return 1

    orphan = {k: v for k, v in composed.items()
              if not any(d.startswith(k) for d in defined)}
    if orphan:
        print("✗ бот собирает ключ из префикса, под который в словаре нет "
              "ни одной строки:")
        for k in sorted(orphan):
            print("    {:<28} {}".format(k + "…", ", ".join(orphan[k][:3])))
        return 1

    missing = {k: v for k, v in used.items() if k not in defined}
    if missing:
        print("✗ бот зовёт {} ключ(а/ей), которых нет в словаре — "
              "пользователь увидит сам ключ:".format(len(missing)))
        for k in sorted(missing):
            print("    {:<28} {}".format(k, ", ".join(missing[k][:3])))
        return 1

    print("✓ бот: все {} использованных ключ(а/ей) есть в словаре "
          "({} определено, {} файл(ов), {} составных, "
          "{} динамических пропущено)".format(
              len(used), len(defined), scanned, len(composed), dynamic))
    return 0


if __name__ == "__main__":
    rc = main()
    rc |= check_bot_strings()
    sys.exit(rc)
