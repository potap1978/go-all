#!/usr/bin/env bash
# k3_test.sh — all offline checks of Kaskad 3 (no root, no network): Python unit tests,
# Python 3.8 syntax/API compatibility, payload freshness, bash syntax, the 2.x self-tests.
# Usage: bash tools/k3_test.sh [-j N] [--quick] [--layout] [--parts a,b,…] [--units "m1 m2 …"]
#   -j N     — the unit modules in N processes (tools/k3_unit_par.py, one process per module, default 12); the installer
#              test, the 2.x self-tests and the layout run beside them, each in its own stream. -j 1 — the old way:
#              «unittest discover» in one process, then every step after another.
#   --quick  — the quick set before a merge into the stage branch: Python 3.8 compatibility, bash -n, payload (~seconds).
#   --parts  — only these steps (tools/test_select.py --run passes them): unit, compat, bashn, payload, layout,
#              installer, rules_adopt, egress_parser, egress_xray; --units — only these unit modules (with «unit»).
#   --layout — the panel layout in Chrome even with --parts/--quick.
#   The layout of the panel in a real Chrome (tools/k3_layout_check.mjs): the quick set by default (390/768/820/1024/1100/1440,
#   light, RU and EN, 1394 views, ~5 min in 6 parts) when node and Chrome are here — otherwise a SKIP line with the reason
#   and «ЗЕЛЕНО, НО ВЁРСТКА НЕ ПРОВЕРЕНА» instead of «ALL GREEN»; K3_LAYOUT=full — all widths and both themes; =0 — skip.
#   The QR window of issuing (tools/k3_give_layout.mjs) runs inside the unit tests (tests/test_333q_layout.py).
#   An action opened in place surviving the live redraw (tools/k3_hold_check.mjs) — tests/test_335_hold.py.
#   K3_UNIT_TIMEOUT=S — a unit module longer than S seconds (default 1800) is stopped with its node/Chrome and fails;
#   K3_STEP_TIMEOUT=S — the same for a step running beside the unit tests (installer, self-tests, layout; default 3600).
#   K3_OLD_INSTALL=<file> — the old install.sh of «old code → new» in the installer test; by default install.sh of 3.3.3
#              (git show 6a6c410) when the clone has that commit, otherwise that check is a SKIP line.
#   R-speed-2: K3_LAYOUT_JOBS=N — the layout in N processes with a Chrome each (default 6; -j 1 — 1), one summary line;
#   K3_INSTALL_JOBS=N — the installer test in N parts (tools/k3_install_par.py; default 3; -j 1 — the script as it is);
#   K3_LAYOUT_PAGES=<groups> — only these groups of views of the layout (tools/test_select.py --run gives them, K3_PAGES);
#   the layout step adds what each group of views needs to ~/.cache/kaskad-tests/layout-map.json (tools/test_select.py).
set -u
# П3-а: a «{name}» tr()/trn() leaves unfilled fails the tests (lib/kaskad/i18n: K3_TR_STRICT); K3_TR_STRICT=0 — off.
export K3_TR_STRICT="${K3_TR_STRICT:-1}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1
FAILED=0
JOBS=12
PARTS=""
UNITS=""
FORCE_LAYOUT=0
while [ $# -gt 0 ]; do
    case "$1" in
        -j) JOBS="${2:-}"; shift ;;
        -j[0-9]*) JOBS="${1#-j}" ;;
        --quick) PARTS="compat,bashn,payload" ;;
        --parts) PARTS="${2:-}"; shift ;;
        --units) UNITS="${2:-}"; shift ;;
        --layout) FORCE_LAYOUT=1 ;;
        -h|--help) sed -n '2,24p' "$0"; exit 0 ;;
        *) echo "k3_test.sh: непонятный аргумент $1 (--help)" >&2; exit 2 ;;
    esac
    shift
done
case "$JOBS" in ''|*[!0-9]*|0) echo "k3_test.sh: -j ждёт число ≥ 1" >&2; exit 2 ;; esac
# R-speed-2: the layout in K3_LAYOUT_JOBS processes, a Chrome each (default 6 beside the unit tests; -j 1 — one, the old way)
LAYOUT_JOBS="${K3_LAYOUT_JOBS:-}"
if [ -z "$LAYOUT_JOBS" ]; then if [ "$JOBS" -gt 1 ]; then LAYOUT_JOBS=6; else LAYOUT_JOBS=1; fi; fi
case "$LAYOUT_JOBS" in ''|*[!0-9]*|0) echo "k3_test.sh: K3_LAYOUT_JOBS ждёт число ≥ 1" >&2; exit 2 ;; esac
# R-speed-2: the installer test in K3_INSTALL_JOBS parts beside each other (tools/k3_install_par.py; default 3 beside the
# unit tests, -j 1 — the script as it is)
INSTALL_JOBS="${K3_INSTALL_JOBS:-}"
if [ -z "$INSTALL_JOBS" ]; then if [ "$JOBS" -gt 1 ]; then INSTALL_JOBS=3; else INSTALL_JOBS=1; fi; fi
case "$INSTALL_JOBS" in ''|*[!0-9]*|0) echo "k3_test.sh: K3_INSTALL_JOBS ждёт число ≥ 1" >&2; exit 2 ;; esac
STEP_LIMIT="${K3_STEP_TIMEOUT:-3600}"
case "$STEP_LIMIT" in ''|*[!0-9]*|0) echo "k3_test.sh: K3_STEP_TIMEOUT ждёт число секунд ≥ 1" >&2; exit 2 ;; esac
export K3_UNIT_TIMEOUT="${K3_UNIT_TIMEOUT:-1800}"
case "$K3_UNIT_TIMEOUT" in ''|*[!0-9]*|0) echo "k3_test.sh: K3_UNIT_TIMEOUT ждёт число секунд ≥ 1" >&2; exit 2 ;; esac
# without --parts — every step (the full run)
want() { [ -z "$PARTS" ] && return 0; case ",$PARTS," in *",$1,"*) return 0 ;; esac; return 1; }
if [ "$FORCE_LAYOUT" = 1 ] && [ -n "$PARTS" ]; then PARTS="$PARTS,layout"; fi

step() { printf '\n== %s\n' "$1"; }
fail() { printf '  FAIL %s\n' "$1"; FAILED=1; }
T0=$(date +%s)
TIMES=""
took() { TIMES="$TIMES $1=$(( $(date +%s) - $2 ))s"; }

# ── the steps that may run beside the unit tests: each writes its output and its exit code into $BG/<name>.{out,rc}
BG="$(mktemp -d 2>/dev/null)" || { echo "mktemp -d не сработал" >&2; exit 2; }
BG_PIDS=""
FG_PID=""
descendants() {   # descendants <pid> — every process it started, at any depth
    local c
    for c in $(pgrep -P "$1" 2>/dev/null); do echo "$c"; descendants "$c"; done
}
kill_tree() {   # kill_tree <pid> — the process and everything it started (the list first: a parent gone, its children
                # are not found by it any more; the parent first: tools/k3_unit_par.py stops starting modules)
    local all c
    all="$(descendants "$1")"
    kill -TERM "$1" 2>/dev/null
    for c in $all; do kill -TERM "$c" 2>/dev/null; done
}
stop_all() {   # INT/TERM: the step in the foreground and the ones beside it, with their processes; the stubborn — KILL
    local p all="" n=0 alive
    for p in $FG_PID $BG_PIDS; do all="$all $p $(descendants "$p")"; kill_tree "$p"; done
    while [ "$n" -lt 50 ]; do
        alive=""; for p in $all; do kill -0 "$p" 2>/dev/null && alive="$alive $p"; done
        [ -z "$alive" ] && return
        sleep 0.1; n=$((n + 1))
    done
    for p in $alive; do kill -KILL "$p" 2>/dev/null; done
}
# a command runs through tools/k3_proc.py: in a session of its own, stopped with everything it started — Chrome in a
# group of its own too (REV-speed-4 С8), also when the shell that started it is gone (a foreign pkill, З7); SIGINT and
# SIGQUIT in it by default — a job started with «&» by a script gets them ignored, and the tests of the Ctrl-C
# protection (tests/test_gw_f2.py) would pass on broken code (REV-speed-3 С6). A function — as is (its commands use it)
PROC="$ROOT/tools/k3_proc.py"
sig_dfl() {
    if [ "$(type -t "$1")" = function ]; then "$@"; else python3 "$PROC" "$@"; fi
}
# a command «in the foreground» runs in the background with «wait»: bash holds a trap until a foreground command ends
# (the unit step — minutes), «wait» it breaks at once (REV-speed-2 З4). The command itself goes to the background, not
# a function: a function in the background is one more bash with the command line of k3_test.sh (REV-speed-4 С8)
run_fg() {
    if [ "$(type -t "$1")" = function ]; then "$@" & else python3 "$PROC" "$@" & fi
    FG_PID=$!; wait "$FG_PID"; local rc=$?; FG_PID=""; stopped_check; return "$rc"
}
# a step stopped by a signal that k3_test.sh got ignored on entry and cannot trap (started in the background by a
# script, «nohup»): tools/k3_proc.py got it and wrote $K3_STOP_FILE — the run is stopped, not red
export K3_STOP_FILE="$BG/stopped"
# KILL to k3_test.sh only (REV-speed-5 З9): the steps beside the unit tests are started by its subshells, alive after it —
# tools/k3_proc.py watches this PID and stops its step when it is gone
export K3_TEST_PID=$$
stopped_check() {
    [ -s "$K3_STOP_FILE" ] || return 0
    local name code; read -r name code < "$K3_STOP_FILE"
    stop_all; rm -rf "$BG"; echo "$WHAT: ОСТАНОВЛЕН ($name) — итога нет"; exit "${code:-130}"
}
trap 'rm -rf "$BG"' EXIT
# П3-б (REV-ns-p3a З-1): a tr() under «except Exception» swallows the raise of the strict mode — the key also goes to this
# file (lib/kaskad/i18n: K3_TR_STRICT_LOG), and a line in it fails the run below. Given from outside (tools/test_select.py)
# — that one; otherwise one of the run's own.
if [ "$K3_TR_STRICT" = 1 ]; then export K3_TR_STRICT_LOG="${K3_TR_STRICT_LOG:-$BG/tr_strict.log}"; fi
WHAT="K3 OFFLINE TESTS"; [ -n "$PARTS" ] && WHAT="K3 OFFLINE TESTS [$PARTS]"
# stopped: a line of its own — neither green nor red (REV-speed-3 З6: a foreign pkill left a log without a summary)
trap 'stop_all; rm -rf "$BG"; echo "$WHAT: ОСТАНОВЛЕН (INT) — итога нет"; exit 130' INT
trap 'stop_all; rm -rf "$BG"; echo "$WHAT: ОСТАНОВЛЕН (TERM) — итога нет"; exit 143' TERM
trap 'stop_all; rm -rf "$BG"; echo "$WHAT: ОСТАНОВЛЕН (HUP) — итога нет"; exit 129' HUP
LAYOUT_SET=quick; [ "${K3_LAYOUT:-}" = "full" ] && LAYOUT_SET=full
run_layout() {
    # prints its own lines; the exit code: 0 green, 1 failed, 77 skipped (the reason — in $BG/layout.skip)
    if [ "${K3_LAYOUT:-}" = "0" ]; then echo "SKIP — K3_LAYOUT=0" > "$BG/layout.skip"; return 77; fi
    if ! command -v node >/dev/null 2>&1; then echo "SKIP — нет node: вёрстка панели не проверена в Chrome" > "$BG/layout.skip"; return 77; fi
    local out rc pages=""
    # R-speed-2: only the groups of views tools/test_select.py picked for the change (K3_LAYOUT_PAGES → K3_PAGES)
    [ -n "${K3_LAYOUT_PAGES:-}" ] && pages="K3_PAGES=$K3_LAYOUT_PAGES"
    out="$(sig_dfl env K3_LAYOUT_JOBS="$LAYOUT_JOBS" K3_LAYOUT_MAP_OUT="$BG/layout-map.json" ${pages:+"$pages"} \
        node tools/k3_layout_check.mjs "$LAYOUT_SET" 2>&1)"; rc=$?
    # what every group of views needs — into the map tools/test_select.py picks the layout by (a union per repository)
    if [ -s "$BG/layout-map.json" ]; then python3 tools/test_select.py --merge-layout-map "$BG/layout-map.json" >/dev/null 2>&1; fi
    if [ "$rc" = 77 ]; then
        # причина — из строки SKIP набора (нет Chrome, Node без WebSocket, Chrome не стартовал)
        local why; why="$(printf '%s\n' "$out" | grep -m1 '^SKIP' | sed 's/^SKIP: */SKIP — /')"
        [ -n "$why" ] || why="SKIP — Chrome недоступен: вёрстка панели не проверена"
        echo "$why" > "$BG/layout.skip"; return 77
    fi
    printf '%s\n' "$out" | grep -E '^(FAIL|LAYOUT)' | head -40 | sed 's/^/  /'
    [ "$rc" = 0 ]
}
# the shell steps: name, title, command
SH_STEPS="rules_adopt:tools/free_rules_adopt_test.sh installer:tools/k3_install_test.sh egress_parser:tools/egress_parser_selftest.sh egress_xray:tools/egress_xray_selftest.sh"
bg_start() {   # bg_start <name> <command…> — in the background, output and code into $BG
    local name="$1"; shift
    date +%s > "$BG/$name.start"
    ( s=$(date +%s); sig_dfl "$@" > "$BG/$name.out" 2>&1; echo $? > "$BG/$name.rc"; echo $(( $(date +%s) - s )) > "$BG/$name.secs" ) &
    echo $! > "$BG/$name.pid"
    BG_PIDS="$BG_PIDS $!"
}
bg_wait() {    # bg_wait <name> — until the step wrote its code; a step gone without one or over $STEP_LIMIT — a failure
    local name="$1" pid start
    pid="$(cat "$BG/$name.pid")"; start="$(cat "$BG/$name.start")"
    while [ ! -f "$BG/$name.rc" ]; do
        if ! kill -0 "$pid" 2>/dev/null; then
            [ -f "$BG/$name.rc" ] && break
            echo "  FAIL: шаг оборвался, не записав код выхода" >> "$BG/$name.out"; echo 99 > "$BG/$name.rc"; break
        fi
        if [ $(( $(date +%s) - start )) -ge "$STEP_LIMIT" ]; then
            kill_tree "$pid"; sleep 1
            echo "  FAIL: шаг не уложился в ${STEP_LIMIT}s (K3_STEP_TIMEOUT) — остановлен" >> "$BG/$name.out"
            echo 124 > "$BG/$name.rc"; echo "$STEP_LIMIT" > "$BG/$name.secs"; break
        fi
        stopped_check
        sleep 1
    done
    stopped_check
    [ -f "$BG/$name.secs" ] || echo $(( $(date +%s) - start )) > "$BG/$name.secs"
}
# П-3 (REV-3313-final): «старый код → новый» в tools/k3_install_test.sh — по умолчанию с install.sh 3.3.3 (6a6c410,
# самый старый выпуск в ветке k3), если коммит есть в клоне; нет его — проверка пропускается (SKIP), как раньше.
# Свой файл — K3_OLD_INSTALL=<путь>. Данные 3.3.3 эта проверка не трогает — только самообновление и ветку.
if want installer && [ -z "${K3_OLD_INSTALL:-}" ] && git cat-file -e 6a6c410:install.sh 2>/dev/null \
    && git show 6a6c410:install.sh > "$BG/install-3.3.3.sh" 2>/dev/null; then
    export K3_OLD_INSTALL="$BG/install-3.3.3.sh"
fi
PAR=0; [ "$JOBS" -gt 1 ] && PAR=1
if [ "$PAR" = 1 ]; then
    want layout && bg_start layout run_layout
    for pair in $SH_STEPS; do
        name="${pair%%:*}"; t="${pair#*:}"
        [ -f "$t" ] && want "$name" || continue
        # REV-speed-2b С7: -j 1 through it too — the script as it is, its number of checks kept for the parts to match
        if [ "$name" = installer ] && [ -f tools/k3_install_par.py ]; then
            bg_start "$name" python3 tools/k3_install_par.py -j "$INSTALL_JOBS"
        else
            bg_start "$name" bash "$t"
        fi
    done
fi

if want unit; then
    step "real validators (tests/realbin.py)"
    # T-M6-qs330b №1: the configs of xray go through the real xray — the fake one asks it too. Once into the cache;
    # no network or no build for this machine — the tests that need it are SKIPPED and said so here, not green silently.
    run_fg bash tools/fetch_test_bins.sh || true
    run_fg env PYTHONPATH="$ROOT/lib:$ROOT" python3 -c 'from tests import realbin as r
x, n = r.xray_path(), r.nginx_path()
print("  xray:  " + (x if x else "SKIP — нет xray: конфиги xray проверены только подделкой (bash tools/fetch_test_bins.sh)"))
print("  nginx: " + (n if n else "SKIP — нет nginx: vhost сайта не проверен nginx -t"))
print("  wg/awg, accel-ppp, openvpn, strongSwan: SKIP — офлайн-валидатора конфигов нет (проверка — на стенде)")
from tests import test_qs333_layout as lay
m = lay.browser_missing()
print("  chrome: " + ("SKIP — раскладка мастера (tests/setup/layout_scan.mjs) не проверена, " + m if m else lay.CHROME))'
fi

if want compat; then
    step "python 3.8 compatibility"
    run_fg python3 tools/k3_py_compat.py lib tests tools/k3_text_check.py || fail "py38 compat"
fi

if want bashn; then
    step "bash syntax"
    run_fg bash -n install.sh || fail "bash -n install.sh"
fi

if [ -f tools/build_payload.py ] && want payload; then
    step "embedded payload is up to date"
    run_fg python3 tools/build_payload.py --check || fail "payload stale: run python3 tools/build_payload.py"
fi

if want unit; then
    s=$(date +%s)
    if [ "$PAR" = 1 ]; then
        step "python unit tests (tools/k3_unit_par.py -j $JOBS)"
        # shellcheck disable=SC2086 — the modules are words
        run_fg python3 tools/k3_unit_par.py -j "$JOBS" $UNITS || fail "unittest"
    elif [ -n "$UNITS" ]; then
        step "python unit tests"
        # shellcheck disable=SC2086
        run_fg env -u K3_LAYOUT PYTHONPATH="$ROOT/lib:$ROOT" python3 -m unittest -q $UNITS || fail "unittest"
    else
        step "python unit tests"
        # K3_LAYOUT is for the layout step below — the unit tests do not run the Chrome check a second time
        run_fg env -u K3_LAYOUT PYTHONPATH="$ROOT/lib:$ROOT" python3 -m unittest discover -s tests -t . -q || fail "unittest"
    fi
    took unit "$s"
fi

LAYOUT_SKIP=""
# REV-speed-2b С10: K3_LAYOUT_PAGES (test_select) or a K3_PAGES left in the environment — not the whole set: said so
LAYOUT_NARROW="${K3_LAYOUT_PAGES:-${K3_PAGES:-}}"
if want layout; then
    step "panel layout in Chrome (tools/k3_layout_check.mjs $LAYOUT_SET)"
    [ -n "$LAYOUT_NARROW" ] && echo "  вёрстка: только виды K3_PAGES=$LAYOUT_NARROW — не весь набор"
    # R-333 №8: on by default — a CSS change is checked without remembering a variable; no Chrome — said so, not green silently
    if [ "$PAR" = 1 ]; then
        bg_wait layout; cat "$BG/layout.out"; LAYOUT_RC=$(cat "$BG/layout.rc"); TIMES="$TIMES layout=$(cat "$BG/layout.secs")s"
    else
        s=$(date +%s); run_fg run_layout; LAYOUT_RC=$?; took layout "$s"
    fi
    if [ "$LAYOUT_RC" = 77 ]; then LAYOUT_SKIP="$(cat "$BG/layout.skip" 2>/dev/null)"
    elif [ "$LAYOUT_RC" != 0 ]; then fail "layout: node tools/k3_layout_check.mjs $LAYOUT_SET"; fi
    [ -n "$LAYOUT_SKIP" ] && echo "  $LAYOUT_SKIP"
fi

for pair in $SH_STEPS; do
    name="${pair%%:*}"; t="${pair#*:}"
    [ -f "$t" ] && want "$name" || continue
    step "$t"
    if [ "$PAR" = 1 ]; then
        bg_wait "$name"
        cat "$BG/$name.out"; [ "$(cat "$BG/$name.rc")" = 0 ] || fail "$t"
        TIMES="$TIMES $name=$(cat "$BG/$name.secs")s"
    elif [ "$name" = installer ] && [ -f tools/k3_install_par.py ]; then
        s=$(date +%s); run_fg python3 tools/k3_install_par.py -j "$INSTALL_JOBS" || fail "$t"; took "$name" "$s"
    else
        s=$(date +%s); run_fg bash "$t" || fail "$t"; took "$name" "$s"
    fi
done
wait

if [ "$K3_TR_STRICT" = 1 ] && [ -s "${K3_TR_STRICT_LOG:-}" ]; then
    step "tr(): unfilled «{name}» (K3_TR_STRICT_LOG — raised, but caught by an «except» on the way)"
    sort "$K3_TR_STRICT_LOG" | uniq -c | sort -rn | head -20
    fail "tr(): не заполнена подстановка «{…}» — ключи выше (ищите вызов tr() под except)"
fi

echo ""
[ -n "$LAYOUT_SKIP" ] && echo "layout: $LAYOUT_SKIP"
want layout && [ -n "$LAYOUT_NARROW" ] && [ -z "$LAYOUT_SKIP" ] && echo "layout: только виды K3_PAGES=$LAYOUT_NARROW — не весь набор"
echo "время: всего $(( $(date +%s) - T0 ))s (-j $JOBS, вёрстка $LAYOUT_JOBS, установщик $INSTALL_JOBS):$TIMES"
if [ "$FAILED" != 0 ]; then echo "$WHAT: FAILURES"; exit 1; fi
# REV-speed-2b С2: the layout wanted and not checked (no node or Chrome, Chrome did not start) — not «ALL GREEN»: a run
# before a release must see it; K3_LAYOUT=0 — skipped on purpose, green
if [ -n "$LAYOUT_SKIP" ] && [ "${K3_LAYOUT:-}" != "0" ]; then echo "$WHAT: ЗЕЛЕНО, НО ВЁРСТКА НЕ ПРОВЕРЕНА"; exit 0; fi
echo "$WHAT: ALL GREEN"; exit 0
