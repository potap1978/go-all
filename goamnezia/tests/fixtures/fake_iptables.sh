#!/usr/bin/env bash
# Поддельный iptables для проверок порядка и идемпотентности правил контейнера (SEC-NET-1): таблица — файл
# $FAKE_IPT_DIR/<таблица>, по строке на правило в том виде, как их печатает `iptables -S` («-A ЦЕПОЧКА …»).
# Понимает то, чем пользуются start.sh и команда изоляции: -t, -C, -A, -I [N], -D, -S. Код 1 у -C и -D —
# «такого правила нет», как у настоящего. Под именем ip6tables — свои таблицы ($FAKE_IPT_DIR/v6); под именем
# iptables-save (ip6tables-save) — все таблицы в виде iptables-save: «*таблица», правила, COMMIT (подгонка MSS).
set -u
dir="${FAKE_IPT_DIR:?FAKE_IPT_DIR не задан}"
case "${0##*/}" in ip6tables*) dir="$dir/v6"; mkdir -p "$dir" ;; esac
case "${0##*/}" in
    *-save)
        for t in filter nat mangle raw; do
            [ -f "$dir/$t" ] || continue
            printf '*%s\n' "$t"; cat "$dir/$t"; printf 'COMMIT\n'
        done
        exit 0 ;;
esac
table=filter
if [ "${1:-}" = "-t" ]; then table="$2"; shift 2; fi
op="${1:-}"; chain="${2:-}"
[ "$#" -ge 2 ] && shift 2 || shift "$#"
f="$dir/$table"
touch "$f"
if [ "$op" = "-S" ]; then
    if [ -n "$chain" ]; then grep -E -- "^-A $chain( |\$)" "$f"; else cat "$f"; fi
    exit 0
fi
pos=""
if [ "$op" = "-I" ] && [[ "${1:-}" =~ ^[0-9]+$ ]]; then pos="$1"; shift; fi
rule="-A $chain $*"
case "$op" in
    -C) grep -qxF -- "$rule" "$f" ;;
    -A) printf '%s\n' "$rule" >>"$f" ;;
    -I)
        # Позиция считается среди правил ЭТОЙ цепочки (1 — первое), как у настоящего iptables.
        awk -v c="-A $chain" -v r="$rule" -v n="${pos:-1}" '
            index($0, c " ") == 1 || $0 == c { k++; if (k == n && !done) { print r; done = 1 } }
            { print }
            END { if (!done) print r }' "$f" >"$f.tmp" && mv "$f.tmp" "$f" ;;
    -D)
        grep -qxF -- "$rule" "$f" || exit 1
        awk -v r="$rule" '$0 == r && !done { done = 1; next } { print }' "$f" >"$f.tmp" && mv "$f.tmp" "$f" ;;
    *) echo "fake iptables: не умею $op" >&2; exit 2 ;;
esac
