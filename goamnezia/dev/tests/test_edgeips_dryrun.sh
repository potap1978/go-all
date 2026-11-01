#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
# goAmnezia pro — несколько адресов сервера без сервера (lib/edgeips.sh, DEFECTS §2.3)
#
# Запуск: ${BASH5:-/opt/homebrew/bin/bash} tests/test_edgeips_dryrun.sh
#
# Подставные ip, iptables и iptables-save из $TMP/bin. Что проверяем:
#   адреса без частных, link-local и CGNAT → порты включённых профилей → правила:
#   цепочка, ровно один переход, ровно одно правило ESTABLISHED на пару «адрес, порт»;
#   повтор ничего не меняет; копии правил и перехода, правила прежнего вида, пропавший
#   адрес и сменённый порт снимаются; занятый замок xtables — отказ без добавлений;
#   в сухом режиме — только DRY-строки → счётчики пакетов по парам → снимок и срез
#   истории не чаще раза в сутки → вердикт: dark только при ключах на адресе, живом
#   соседе и адресе, обычно занятом в это время суток (край дня, суббота после будней, пользователь
#   «через день» и один день истории — не тревога; блокировка с ночи видна утром); quiet,
#   quiet-all, unused; снятый адрес не
#   считается соседом; снятое правило одного порта не выглядит обнулением; доменный
#   Endpoint не приписывает пользователей адресам → тик: одна тревога на переход,
#   напоминание не чаще раза в сутки, «снова принимает», молчаливое снятие у адреса
#   без ключей → пользователи по адресам.
# ══════════════════════════════════════════════════════════════════════════════
set -uo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
TMP=$(mktemp -d "${TMPDIR:-/tmp}/goamnezia-edgeips.XXXXXX") || exit 2
DONE=""
ERR="$TMP/stderr.log"
OUT="$TMP/stdout.log"
trap 'if [ -z "$DONE" ]; then printf "\n!! аварийный выход, последний stderr:\n"; tail -n 20 "$ERR" 2>/dev/null; fi; rm -rf "$TMP"' EXIT

export GOAMNEZIA_DRY_RUN=1
export GOAMNEZIA_DIR="$TMP/goamnezia"
export GOAMNEZIA_LOCK_DIR="$TMP/run"
export GOAMNEZIA_LOG_FILE="$TMP/goamnezia.log"
export GOAMNEZIA_AUDIT_LOG="$TMP/audit.log"
export GOAMNEZIA_NOTIFY_DIR="$TMP/run/notify"
export GOAMNEZIA_BUILD_ROOT="$TMP/build"
export GOAMNEZIA_SERVER_IP="203.0.113.10"
export GOAMNEZIA_SYSTEMD_DIR="$TMP/systemd"
export UPSTREAM_DIR="$TMP/upstream"
export LIB_DIR="$ROOT/lib"
export GOAMNEZIA_CONTAINER_FS_DIR="$TMP/cfs"
unset GOAMNEZIA_UPSTREAM_FIXTURE_DIR GOAMNEZIA_AWG_SHOW_FIXTURE GOAMNEZIA_STATE_DIR
mkdir -p "$GOAMNEZIA_DIR" "$GOAMNEZIA_LOCK_DIR" "$UPSTREAM_DIR" "$GOAMNEZIA_CONTAINER_FS_DIR"
cp -R "$ROOT/lib/upstream/." "$UPSTREAM_DIR/"

# Временные файлы с ключами — в каталоге стенда (L1-19). Без переменной продукт кладёт их в /dev/shm,
# а он есть только на Linux: там они жили бы на машине прогона, и оборванный прогон оставлял бы ключи.
export GOAMNEZIA_SECRET_TMPDIR="$TMP/shm"; mkdir -p "$GOAMNEZIA_SECRET_TMPDIR"

pass=0; fail=0; skip=0; rc=0; out=""
ok()   { pass=$((pass + 1)); printf '  ✓ %s\n' "$1"; }
bad()  { fail=$((fail + 1)); printf '  ✗ %s\n' "$1"; [ -n "${2:-}" ] && printf '      %s\n' "$2" | head -c 900; return 0; }
skipped() { skip=$((skip + 1)); printf '  – %s (пропущено: %s)\n' "$1" "$2"; }
check() { if eval "$2" >/dev/null 2>&1; then ok "$1"; else bad "$1" "${3:-}"; fi; }
section() { printf '\n── %s\n' "$1"; }
# Права восьмерично: сначала GNU (-c), потом BSD (-f). Не наоборот: у GNU «-f» — сведения
# о файловой системе, он печатал их перед правами, и на Linux краснела каждая проверка 600/700.
perm() { stat -c %a "$1" 2>/dev/null || stat -f %Lp "$1" 2>/dev/null; }
run() {
    GA_ERR_CODE=""; GA_ERR_MSG=""; GA_LAST_ERROR_CODE=""
    "$@" >"$OUT" 2>"$ERR"; rc=$?
    out=$(<"$OUT")
    return 0
}
errs() { cat "$ERR" 2>/dev/null; }

# ── Подставные ip / iptables / iptables-save ─────────────────────────────────
export FAKE_IPT_DIR="$TMP/ipt"
FAKE_BIN="$TMP/bin"
mkdir -p "$FAKE_IPT_DIR" "$FAKE_BIN"
cat >"$FAKE_BIN/ip" <<'EOF'
#!/bin/bash
cat "${FAKE_IPT_DIR:?}/addrs"
EOF
cat >"$FAKE_BIN/iptables" <<'EOF'
#!/bin/bash
S="${FAKE_IPT_DIR:?}"
printf '%s\n' "$*" >>"$S/calls"
[ -f "$S/locked" ] && exit 4
[ "${1:-}" = "-w" ] && shift 2
[ "${1:-}" = "-t" ] && shift 2
case "${1:-}" in
    -S) if [ "${2:-}" = PREROUTING ]; then [ -f "$S/jumps" ] && cat "$S/jumps"; exit 0; fi
        [ -f "$S/chain" ] || exit 1
        [ -f "$S/rules" ] && cat "$S/rules"; exit 0 ;;
    -N) [ -f "$S/chain" ] && exit 1; touch "$S/chain" ;;
    -I) [ "${2:-}" = PREROUTING ] && echo "-A PREROUTING -j ${5:-}" >>"$S/jumps" ;;
    -A) shift; printf -- '-A %s\n' "$*" >>"$S/rules" ;;
    -D) shift
        if [ "${1:-}" = PREROUTING ]; then file="$S/jumps"; line="-A $*"; else file="$S/rules"; line="-A $*"; fi
        [ -f "$file" ] && awk -v l="$line" '!d && $0 == l { d = 1; next } { print }' "$file" >"$file.tmp" && mv "$file.tmp" "$file" ;;
    *) exit 0 ;;
esac
EOF
cat >"$FAKE_BIN/iptables-save" <<'EOF'
#!/bin/bash
S="${FAKE_IPT_DIR:?}"
echo "*mangle"
echo "[900:90000] -A PREROUTING -j GOAMNEZIA_EDGE"
echo "[5:500] -A OTHER_CHAIN -d 203.0.113.10/32 -p udp -m udp --dport 40000 -m conntrack --ctstate ESTABLISHED"
[ -f "$S/counters" ] && cat "$S/counters"
echo "COMMIT"
EOF
chmod +x "$FAKE_BIN/ip" "$FAKE_BIN/iptables" "$FAKE_BIN/iptables-save"
export PATH="$FAKE_BIN:$PATH"

A="203.0.113.10"; B="198.51.100.20"; C="192.0.2.77"
RULE_A="-A GOAMNEZIA_EDGE -d $A/32 -p udp -m udp --dport 40000 -m conntrack --ctstate ESTABLISHED"
RULE_B="-A GOAMNEZIA_EDGE -d $B/32 -p udp -m udp --dport 40000 -m conntrack --ctstate ESTABLISHED"
set_addrs() {
    : >"$FAKE_IPT_DIR/addrs"
    local a
    for a in "$@"; do
        printf '2: eth0    inet %s scope global eth0\\       valid_lft forever preferred_lft forever\n' "$a" >>"$FAKE_IPT_DIR/addrs"
    done
}
set_counters() {   # set_counters <пакеты A:40000> <пакеты B:40000>
    printf '[%s:%s] %s\n[%s:%s] %s\n' "$1" "$(( $1 * 90 ))" "$RULE_A" "$2" "$(( $2 * 90 ))" "$RULE_B" >"$FAKE_IPT_DIR/counters"
}
mutations() { grep -cE -- "-t mangle -(A|D|N|I) " "$FAKE_IPT_DIR/calls" 2>/dev/null; }

section "окружение"
# shellcheck disable=SC1091
source "$ROOT/lib/common.sh"
# shellcheck disable=SC1091
source "$ROOT/lib/i18n.sh"
load_language ru >/dev/null 2>&1
for _m in docker amnezia users export edgeips website; do
    # shellcheck disable=SC1090
    source "$ROOT/lib/$_m.sh"
done
# DNS на стенде — только подставной (GOAMNEZIA_DNS_STUB); без него «не знаем», как без сети
unset GOAMNEZIA_DNS_STUB
# На адресах сценариев по одному пользователю; порог «не меньше двух» проверяется отдельно, в конце раздела 4.
GA_EDGE_MIN_USERS=1
mkdir -p "$STATE_DIR"; chmod 700 "$STATE_DIR"
GA_VER=$(sed -n 's/^GOAMNEZIA_VERSION="\([^"]*\)".*/\1/p' "$ROOT/lib/common.sh" | head -1)
cat >"$CONFIG_FILE" <<EOF
{"schema": 1, "version": "$GA_VER", "installed": true, "mode": "lite", "lang": "ru", "domain": "",
 "server_ip": "$A", "endpoint_host": "", "client_dns": ["1.1.1.1", "1.0.0.1"], "profiles": {}}
EOF
chmod 600 "$CONFIG_FILE"
printf '{"schema": 1, "users": []}\n' >"$USERS_FILE"; chmod 600 "$USERS_FILE"
run amnezia_profile_install awg2 40000 default
check "профиль awg2 на порту 40000 установлен (dry)" '[ "$rc" = 0 ]' "$(errs)"
run users_add anna "Анна"
run users_add bob "Боб"
set_user() {   # set_user <имя> <jq-правка>
    jq --arg n "$1" ".users |= map(if .name == \$n then $2 else . end)" "$USERS_FILE" >"$USERS_FILE.tmp" \
        && mv "$USERS_FILE.tmp" "$USERS_FILE" && chmod 600 "$USERS_FILE"
}
set_user bob ".endpoint_ip = \"$B\""
check "пользователи: anna на общем адресе, bob на втором" \
    '[ "$(jq -r ".users|length" "$USERS_FILE")" = 2 ] && [ "$(jq -r ".users[] | select(.name==\"bob\") | .endpoint_ip" "$USERS_FILE")" = "$B" ]'

# ── 1. Адреса и порты ────────────────────────────────────────────────────────
section "ga_edge_list / ga_edge_ports"
printf '2: eth0    inet %s/24 brd 203.0.113.255 scope global eth0\n2: eth0    inet %s/32 scope global eth0\n3: eth1    inet 10.0.0.5/24 scope global eth1\n4: wg0    inet 100.72.1.1/10 scope global wg0\n5: eth2    inet 169.254.3.3/16 scope global eth2\n6: br0    inet 192.168.7.1/24 scope global br0\n' \
    "$A" "$B" >"$FAKE_IPT_DIR/addrs"
run ga_edge_list
check "адреса: только глобальные, без частных, CGNAT и link-local" '[ "$out" = "$(printf "%s\n%s" "$B" "$A")" ]' "$out"
run ga_edge_ports
check "порты включённых профилей: 40000" '[ "$out" = 40000 ]' "$out"
ga_config_merge '{"profiles":{"awg2":{"enabled":false}}}' >/dev/null 2>&1
run ga_edge_ports
check "выключенный профиль портов не даёт" '[ -z "$out" ]' "$out"
ga_config_merge '{"profiles":{"awg2":{"enabled":true}}}' >/dev/null 2>&1

# ── 2. Правила ───────────────────────────────────────────────────────────────
section "ga_edge_ensure_rules"
set_addrs "$A/24" "$B/32"
: >"$FAKE_IPT_DIR/calls"
GOAMNEZIA_DRY_RUN=1 run ga_edge_ensure_rules
check "сухой режим: DRY-строки с ESTABLISHED и ожиданием замка, изменений нет" \
    '[ "$rc" = 0 ] && grep -qF -- "DRY: iptables -w 5 -t mangle ${RULE_A}" "$ERR" && [ "$(mutations)" = 0 ] && [ ! -f "$FAKE_IPT_DIR/chain" ]' \
    "$(errs | head -6) | $(cat "$FAKE_IPT_DIR/calls")"
GOAMNEZIA_DRY_RUN=0 run ga_edge_ensure_rules
check "первый проход: цепочка, один переход, по правилу ESTABLISHED на адрес" \
    '[ "$rc" = 0 ] && [ -f "$FAKE_IPT_DIR/chain" ] && [ "$(grep -c . "$FAKE_IPT_DIR/jumps")" = 1 ] && [ "$(grep -c . "$FAKE_IPT_DIR/rules")" = 2 ] && grep -qxF -- "$RULE_A" "$FAKE_IPT_DIR/rules" && grep -qxF -- "$RULE_B" "$FAKE_IPT_DIR/rules"' \
    "$(cat "$FAKE_IPT_DIR/rules" "$FAKE_IPT_DIR/jumps" 2>/dev/null) | $(errs)"
check "все вызовы iptables ждут замок (-w 5)" '! grep -vq "^-w 5 " "$FAKE_IPT_DIR/calls"' "$(grep -v "^-w 5 " "$FAKE_IPT_DIR/calls")"
: >"$FAKE_IPT_DIR/calls"
GOAMNEZIA_DRY_RUN=0 run ga_edge_ensure_rules
check "повтор ничего не добавляет и не снимает" '[ "$rc" = 0 ] && [ "$(mutations)" = 0 ]' "$(cat "$FAKE_IPT_DIR/calls")"
printf '%s\n' "$RULE_A" "-A GOAMNEZIA_EDGE -d $A/32 -p udp -m udp --dport 40000" >>"$FAKE_IPT_DIR/rules"
echo "-A PREROUTING -j GOAMNEZIA_EDGE" >>"$FAKE_IPT_DIR/jumps"
GOAMNEZIA_DRY_RUN=0 run ga_edge_ensure_rules
check "копия правила, правило прежнего вида (без ESTABLISHED) и лишний переход сняты" \
    '[ "$rc" = 0 ] && [ "$(grep -c . "$FAKE_IPT_DIR/rules")" = 2 ] && [ "$(grep -cxF -- "$RULE_A" "$FAKE_IPT_DIR/rules")" = 1 ] && [ "$(grep -c . "$FAKE_IPT_DIR/jumps")" = 1 ]' \
    "$(cat "$FAKE_IPT_DIR/rules" "$FAKE_IPT_DIR/jumps")"
set_addrs "$A/24"
GOAMNEZIA_DRY_RUN=0 run ga_edge_ensure_rules
check "адрес пропал с машины — его правило снято" \
    '[ "$rc" = 0 ] && ! grep -qF -- "-d $B/32" "$FAKE_IPT_DIR/rules" && grep -qxF -- "$RULE_A" "$FAKE_IPT_DIR/rules"' "$(cat "$FAKE_IPT_DIR/rules")"
ga_config_merge '{"profiles":{"awg2":{"port":40001}}}' >/dev/null 2>&1
GOAMNEZIA_DRY_RUN=0 run ga_edge_ensure_rules
check "порт профиля сменился — правило старого порта снято, нового добавлено" \
    '[ "$rc" = 0 ] && grep -qF -- "--dport 40001 -m conntrack --ctstate ESTABLISHED" "$FAKE_IPT_DIR/rules" && ! grep -qF -- "--dport 40000" "$FAKE_IPT_DIR/rules"' "$(cat "$FAKE_IPT_DIR/rules")"
ga_config_merge '{"profiles":{"awg2":{"port":40000}}}' >/dev/null 2>&1
set_addrs "$A/24" "$B/32"
GOAMNEZIA_DRY_RUN=0 run ga_edge_ensure_rules
touch "$FAKE_IPT_DIR/locked"; : >"$FAKE_IPT_DIR/calls"; cp "$FAKE_IPT_DIR/rules" "$TMP/rules.before"
GOAMNEZIA_DRY_RUN=0 run ga_edge_ensure_rules
rm -f "$FAKE_IPT_DIR/locked"
check "замок xtables занят (iptables -S не ответил) — отказ, ни одного добавления" \
    '[ "$rc" != 0 ] && [ "$(mutations)" = 0 ] && cmp -s "$TMP/rules.before" "$FAKE_IPT_DIR/rules"' "rc=$rc $(cat "$FAKE_IPT_DIR/calls")"

# ── 3. Счётчики, снимок, срез истории ────────────────────────────────────────
section "ga_edge_counters / ga_edge_sample / срез"
printf '[120:15000] %s\n[30:4000] -A GOAMNEZIA_EDGE -d %s/32 -p udp -m udp --dport 51820 -m conntrack --ctstate ESTABLISHED\n[7:900] %s\n[500:60000] -A GOAMNEZIA_EDGE -d %s/32 -p udp -m udp --dport 40000\n' \
    "$RULE_A" "$A" "$RULE_B" "$A" >"$FAKE_IPT_DIR/counters"
run ga_edge_counters
check "счётчики пакетов и байтов по парам «адрес, порт»; без ESTABLISHED, чужая цепочка и переход не считаются" \
    '[ "$out" = "$(printf "%s\t40000\t7\t900\n%s\t40000\t120\t15000\n%s\t51820\t30\t4000" "$B" "$A" "$A")" ]' "$out"
HIST=$(ga_edge_history)
rm -f "$HIST"
GOAMNEZIA_DRY_RUN=0 run ga_edge_sample
check "снимок: заголовок timestamp,ip,port,packets,bytes, строка на пару, права 600" \
    '[ "$rc" = 0 ] && [ "$(head -1 "$HIST")" = "timestamp,ip,port,packets,bytes" ] && [ "$(grep -c ",$A,40000,120,15000$" "$HIST")" = 1 ] && [ "$(grep -c ",$A,51820,30,4000$" "$HIST")" = 1 ] && [ "$(perm "$HIST")" = 600 ]' \
    "$(cat "$HIST" 2>/dev/null) | $(errs)"
printf 'timestamp,ip,packets,bytes\n1,%s,1,1\n' "$A" >"$HIST"
GOAMNEZIA_DRY_RUN=0 run ga_edge_sample
check "история чужого формата начинается заново, а не читается вкривь" \
    '[ "$(head -1 "$HIST")" = "timestamp,ip,port,packets,bytes" ] && ! grep -q "^1," "$HIST"' "$(head -3 "$HIST")"
# История версии без байтов (0.4.2 и раньше) — не сбрасывается: шапка переписывается, строки остаются.
old_ts=$(( $(date -u +%s) - 600 ))
printf 'timestamp,ip,port,packets\n%s,%s,40000,5\n' "$old_ts" "$A" >"$HIST"
GOAMNEZIA_DRY_RUN=0 run ga_edge_sample
check "история без байтов (прежняя версия) сохраняется: новая шапка, старые строки на месте, новые — с байтами" \
    '[ "$(head -1 "$HIST")" = "timestamp,ip,port,packets,bytes" ] && grep -q "^$old_ts,$A,40000,5$" "$HIST" && grep -q ",$A,40000,120,15000$" "$HIST"' "$(head -4 "$HIST")"
# UP-3: первая строка с байтами после строк без них — не прирост: счётчик байтов правила копился с его
# создания (правила переживают обновление), и «трафик по адресам» сутки показывал десятки ГБ.
up3_ts=$(( $(date -u +%s) - 480 ))
printf 'timestamp,ip,port,packets,bytes\n%s,%s,40000,100\n%s,%s,40000,200\n%s,%s,40000,300,50000000000\n%s,%s,40000,400,50000009000\n' \
    "$up3_ts" "$A" "$((up3_ts + 120))" "$A" "$((up3_ts + 240))" "$A" "$((up3_ts + 360))" "$A" >"$HIST"
run ga_edge_status_json
up3_b15=$(jq -r --arg ip "$A" '.ips[] | select(.ip == $ip) | .bytes_15m' <<<"$out" 2>/dev/null)
up3_bday=$(jq -r --arg ip "$A" '.ips[] | select(.ip == $ip) | .bytes_day' <<<"$out" 2>/dev/null)
up3_p15=$(jq -r --arg ip "$A" '.ips[] | select(.ip == $ip) | .packets_15m' <<<"$out" 2>/dev/null)
check "UP-3 история v1→v2: накопленный счётчик байтов не засчитан одним тиком (15 мин и сутки — 9000), пакеты — как были" \
    '[ "$up3_b15" = 9000 ] && [ "$up3_bday" = 9000 ] && [ "$up3_p15" = 300 ]' "b15=$up3_b15 bday=$up3_bday p15=$up3_p15 $out"
now=$(date -u +%s); cutoff=$(( now - GA_EDGE_RETENTION_DAYS * 86400 ))
printf 'timestamp,ip,port,packets\n%s,%s,40000,1\n%s,%s,40000,2\n' "$(( cutoff - 3600 ))" "$A" "$now" "$A" >"$HIST"
run _ga_edge_prune "$HIST"
check "хвост старше срока меньше чем на сутки — файл не переписывается" '[ "$(grep -c . "$HIST")" = 3 ]' "$(cat "$HIST")"
printf 'timestamp,ip,port,packets\n%s,%s,40000,1\n%s,%s,40000,2\n' "$(( cutoff - 90000 ))" "$A" "$now" "$A" >"$HIST"
run _ga_edge_prune "$HIST"
check "хвост старше срока больше чем на сутки — срезан" '[ "$(grep -c . "$HIST")" = 2 ] && [ "$(perm "$HIST")" = 600 ]' "$(cat "$HIST")"

# ── 4. Вердикт ───────────────────────────────────────────────────────────────
section "ga_edge_report"
# gen_history <сценарий> — восемь суток истории с шагом 2 минуты, последний тик — 2 минуты назад.
# Печатает: накопительные пакеты A:40000 и B:40000 на последнем тике и начало тишины A.
gen_history() {
    python3 - "$HIST" "$1" "$A" "$B" <<'PY'
import sys, time
path, scenario, A, B = sys.argv[1:5]
step, per_day = 120, 720
now = int(time.time())
total = 8 * per_day
def ts(k):                       # k — тиков от конца (0 — последний)
    t = now - (k + 1) * step
    if scenario == "gap" and k >= 2:
        t -= 36000               # десять часов без наблюдения между k=2 и k=1
    return t
def live_a(k):
    if scenario in ("dark", "port2", "quiet_all", "gap"):
        return k >= 10
    if scenario == "long_block":
        return k >= 900
    if scenario == "quiet_usual":
        return 10 <= k < 70
    if scenario == "oneday":
        return 10 <= k < 70 or per_day <= k <= per_day + 20
    if scenario == "never_live":
        return False
    if scenario in ("short_all", "short_one"):
        return k >= 3            # замолчал 6 минут назад — короче порога тишины
    return True
def live_b(k):
    if scenario == "short_all":
        return k >= 12           # замолчал 24 минуты назад
    return k >= 10 if scenario == "quiet_all" else True
a = b = 0
s_a = 0
with open(path, "w") as fh:
    fh.write("timestamp,ip,port,packets\n")
    for k in range(total - 1, -1, -1):
        t = ts(k)
        if live_a(k):
            a += 30
            s_a = 0
        elif s_a == 0:
            s_a = t
        if live_b(k):
            b += 30
        fh.write("%d,%s,40000,%d\n%d,%s,40000,%d\n" % (t, A, a, t, B, b))
        if scenario == "port2" and k > 0:
            fh.write("%d,%s,51820,99999\n" % (t, A))
print(a, b, s_a)
PY
}
field_of() { awk -F'\t' -v ip="$1" -v n="$2" '$1 == ip { print $n }' <<<"$out"; }
read -r LAST_A LAST_B S_A < <(gen_history dark)
run ga_edge_report
check "A молчит 20 минут, B жив, в прошлые дни A в это время принимал, на A смотрит ключ → dark; B → ok" \
    '[ "$(field_of "$A" 4)" = dark ] && [ "$(field_of "$A" 5)" = 1 ] && [ "$(field_of "$B" 4)" = ok ] && [ "$(field_of "$B" 5)" = 1 ]' "$out"
check "у тёмного адреса названо начало тишины" '[ "$(field_of "$A" 6)" = "$S_A" ]' "$out | ждали $S_A"
check "окна: у молчащего A за 15 минут 0 пакетов, у B за 15 минут меньше, чем за час, а за час меньше, чем за сутки" \
    '[ "$(field_of "$A" 8)" = 0 ] && [ "$(field_of "$B" 8)" -gt 0 ] && [ "$(field_of "$B" 9)" -gt "$(field_of "$B" 8)" ] && [ "$(field_of "$B" 3)" -gt "$(field_of "$B" 9)" ]' "$out"
run ga_edge_status_json
check "edge-status: count=2, у A вердикт dark и silent_since в ISO, пакеты по окнам числами" \
    '[ "$(jq -r .count <<<"$out")" = 2 ] && [ "$(jq -r --arg ip "$A" ".ips[] | select(.ip == \$ip) | .verdict" <<<"$out")" = dark ] && [[ "$(jq -r --arg ip "$A" ".ips[] | select(.ip == \$ip) | .silent_since" <<<"$out")" =~ ^20[0-9]{2}-[0-9]{2}-[0-9]{2}T ]] && [ "$(jq -r --arg ip "$B" ".ips[] | select(.ip == \$ip) | .packets_1h | type" <<<"$out")" = number ]' "$out"
# PUI-25 (SSH): команда туннеля к панели — на адрес, куда реально приходят; основной A молчит — берём B.
run ga_edge_reach_ip "$A"
check "PUI-25 основной адрес без входящих (dark) → адрес для SSH-туннеля — B (вердикт ok)" '[ "$out" = "$B" ]' "out=$out"
run ga_edge_reach_ip "$B"
check "PUI-25 основной адрес принимает (ok) → он и остаётся" '[ "$out" = "$B" ]' "out=$out"
read -r LAST_A LAST_B S_A < <(gen_history alllive)
run ga_edge_reach_ip "$A"
check "PUI-25 оба адреса принимают — основной A, без подмены" '[ "$out" = "$A" ]' "out=$out"
# SR-37: битый users.json — «не знаем, кто где», а не «людей нет»: отказ с кодом, а не нули и «успешно».
cp "$USERS_FILE" "$TMP/users.sr37"; printf '{"schema": 1, "users": [' >"$USERS_FILE"
run ga_edge_status_json
check "SR-37 битый users.json: edge-status — отказ users_corrupt, а не count с нулями людей" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = users_corrupt ] && [ -z "$out" ]' "rc=$rc code=$GA_ERR_CODE out=${out:0:200}"
cat "$TMP/users.sr37" >"$USERS_FILE"
read -r LAST_A LAST_B S_A < <(gen_history quiet_usual)
run ga_edge_report
check "в прошлые дни A в это время молчал → quiet, не тревога" '[ "$(field_of "$A" 4)" = quiet ]' "$out"
read -r LAST_A LAST_B S_A < <(gen_history oneday)
run ga_edge_report
check "A принимал в это время только вчера из семи дней — совпадение, а не привычка → quiet" '[ "$(field_of "$A" 4)" = quiet ]' "$out"
read -r LAST_A LAST_B S_A < <(gen_history long_block)
run ga_edge_report
check "блокировка тянется 30 часов — вердикт остаётся dark (дни внутри тишины не в счёт)" \
    '[ "$(field_of "$A" 4)" = dark ] && [ "$(field_of "$A" 6)" = "$S_A" ]' "$out | ждали $S_A"
read -r LAST_A LAST_B S_A < <(gen_history quiet_all)
run ga_edge_report
check "молчат оба адреса → quiet-all у обоих" '[ "$(field_of "$A" 4)" = quiet-all ] && [ "$(field_of "$B" 4)" = quiet-all ]' "$out"
read -r LAST_A LAST_B S_A < <(gen_history dark)
set_addrs "$A/24"
run ga_edge_report
check "снятый с машины адрес не строка отчёта и не «живой сосед» → A quiet-all" \
    '[ "$(field_of "$A" 4)" = quiet-all ] && ! grep -qF "$B" <<<"$out"' "$out"
set_addrs "$A/24" "$B/32"
read -r LAST_A LAST_B S_A < <(gen_history port2)
run ga_edge_report
check "правило второго порта A снято в последний тик — это не прирост, A по-прежнему dark" '[ "$(field_of "$A" 4)" = dark ]' "$out"
read -r LAST_A LAST_B S_A < <(gen_history gap)
run ga_edge_report
check "10 часов без наблюдения посреди тишины — тишина считается только после разрыва → ok" '[ "$(field_of "$A" 4)" = ok ]' "$out"
read -r LAST_A LAST_B S_A < <(gen_history never_live)
run ga_edge_report
check "адрес ни разу не принимал — quiet, а не ok (ложному «снова принимает» не родиться)" '[ "$(field_of "$A" 4)" = quiet ]' "$out"
# NC6-10: за 15 минут у обоих ноль — вердикт один, «тихо у всех», как бы давно каждый ни замолчал; раньше A
# (тишина 6 минут) оставался «принимает» строкой выше «тихо у всех адресов» у B
read -r LAST_A LAST_B S_A < <(gen_history short_all)
run ga_edge_report
check "NC6-10 молчат оба (A 6 минут, B 24 минуты) → quiet-all у обоих, без «принимает» у A" \
    '[ "$(field_of "$A" 4)" = quiet-all ] && [ "$(field_of "$B" 4)" = quiet-all ]' "$out"
# PUI-25 при такой тишине: «ok» нет ни у кого — адрес для SSH-туннеля тот, что принял больше за последний час (A)
run ga_edge_reach_ip "$B"
check "NC6-10 тихо у всех: адрес для SSH-туннеля — принявший больше за час (A), а не основной B" '[ "$out" = "$A" ]' "out=$out"
read -r LAST_A LAST_B S_A < <(gen_history short_one)
run ga_edge_report
check "NC6-10 A замолчал 6 минут назад, B принимает → у A короткая тишина по-прежнему ok, у B ok" \
    '[ "$(field_of "$A" 4)" = ok ] && [ "$(field_of "$B" 4)" = ok ]' "$out"
read -r LAST_A LAST_B S_A < <(gen_history dark)
set_user anna '.enabled = false'
run ga_edge_report
check "на A не смотрит ни один ключ включённого пользователя → unused" '[ "$(field_of "$A" 4)" = unused ] && [ "$(field_of "$A" 5)" = 0 ]' "$out"
set_user anna '.enabled = true'
ga_config_merge '{"endpoint_host":"vpn.example.com"}' >/dev/null 2>&1
run ga_edge_report
check "Endpoint — домен: адрес выбирает DNS, как в goTelegram, — пользователи на каждом адресе (2 и 2), A dark, B ok" \
    '[ "$(field_of "$A" 5)" = 2 ] && [ "$(field_of "$B" 5)" = 2 ] && [ "$(field_of "$A" 4)" = dark ] && [ "$(field_of "$B" 4)" = ok ]' "$out"
# Несколько A-записей (0.4.3): доменные пользователи приходят только на адреса ИЗ A-записей. Адрес,
# которого нет в DNS, простаивает честно — «unused», а не ложная тревога «замолчал».
export GOAMNEZIA_DNS_STUB="$A $B"
run ga_edge_report
check "A-записи домена — оба адреса: пользователи на каждом (2 и 2), как раньше" \
    '[ "$(field_of "$A" 5)" = 2 ] && [ "$(field_of "$B" 5)" = 2 ] && [ "$(field_of "$A" 4)" = dark ]' "$out"
export GOAMNEZIA_DNS_STUB="$B"
run ga_edge_report
check "в A-записях только B: на A доменных пользователей нет → unused, а не dark; на B — 2, ok" \
    '[ "$(field_of "$A" 5)" = 0 ] && [ "$(field_of "$A" 4)" = unused ] && [ "$(field_of "$B" 5)" = 2 ] && [ "$(field_of "$B" 4)" = ok ]' "$out"
run ga_edge_status_json
check "edge-status: dns.state ok, у A in_dns=false, у B in_dns=true" \
    'jq -e ".dns.state == \"ok\" and (.ips[] | select(.ip == \"$A\") | .in_dns) == false and (.ips[] | select(.ip == \"$B\") | .in_dns) == true" <<<"$out"' "$out"
export GOAMNEZIA_DNS_STUB="$B 192.0.2.200"
run ga_edge_status_json
check "чужой адрес в A-записях: dns.state partial, foreign = [192.0.2.200]" \
    'jq -e ".dns.state == \"partial\" and .dns.foreign == [\"192.0.2.200\"]" <<<"$out"' "$out"
export GOAMNEZIA_DNS_STUB=""
run ga_edge_status_json
check "A-записей нет: dns.state none, in_dns=false у обоих адресов" \
    'jq -e ".dns.state == \"none\" and ([.ips[].in_dns] | all(. == false))" <<<"$out"' "$out"
check "A-записей нет: доменных пользователей нет ни на одном адресе — оба unused, не dark" \
    'jq -e "[.ips[] | .users] == [0, 0] and ([.ips[].verdict] | all(. == \"unused\"))" <<<"$out"' "$out"
export GOAMNEZIA_DNS_STUB="192.0.2.200"
run ga_edge_status_json
check "домен ведёт только на чужой адрес: state elsewhere, доменных пользователей здесь нет" \
    'jq -e ".dns.state == \"elsewhere\" and ([.ips[] | .users] | add) == 0" <<<"$out"' "$out"
unset GOAMNEZIA_DNS_STUB
run ga_edge_status_json
check "DNS не спросить (стенд без подставного ответа): dns.state unknown, in_dns=null — пользователи по-старому на каждом" \
    'jq -e ".dns.state == \"unknown\" and ([.ips[].in_dns] | all(. == null))" <<<"$out"' "$out"
ga_config_merge "{\"endpoint_host\":\"$B\"}" >/dev/null 2>&1
run ga_edge_report
check "Endpoint — второй адрес: anna и bob на B (2), A unused" '[ "$(field_of "$B" 5)" = 2 ] && [ "$(field_of "$A" 4)" = unused ]' "$out"
ga_config_merge '{"endpoint_host":""}' >/dev/null 2>&1

# Время суток. gen_days <последний тик> <дней> <python: живой ли A> [то же для B] — история от полуночи
# самого старого дня (d = 0) до последнего тика (сегодня, d = дней − 1); m — минута суток UTC. Печатает
# счётчики A и B и начало последней тишины A.
DAY0=$(( $(date -u +%s) / 86400 * 86400 ))
at_min() { printf '%s' $(( DAY0 + $1 * 60 )); }
gen_days() {
    python3 - "$HIST" "$1" "$2" "$3" "${4:-True}" "$A" "$B" "$DAY0" <<'PY'
import sys
path, last, days, expr_a, expr_b, A, B, day0 = sys.argv[1:9]
last, days, day0 = int(last), int(days), int(day0)
start = day0 - (days - 1) * 86400
a = b = s_a = 0
with open(path, "w") as fh:
    fh.write("timestamp,ip,port,packets\n")
    t = start
    while t <= last:
        d, m = (t - start) // 86400, (t - start) % 86400 // 60
        if eval(expr_a, {"d": d, "m": m}):
            a += 30
            s_a = 0
        elif s_a == 0:
            s_a = t
        if eval(expr_b, {"d": d, "m": m}):
            b += 30
        fh.write("%d,%s,40000,%d\n%d,%s,40000,%d\n" % (t, A, a, t, B, b))
        t += 120
print(a, b, s_a)
PY
}
# scene <минута суток последнего тика> <дней> <живой ли A> — история и отчёт на её последнем тике. Отчёт
# строится здесь, в основной оболочке: из $( ) переменная $out наружу не вышла бы, и проверка читала бы
# отчёт прошлого сценария.
scene() {
    read -r _ _ S_A < <(gen_days "$(at_min "$1")" "$2" "$3")
    GA_EDGE_NOW="$(at_min "$1")" run ga_edge_report
}
scene 1340 8 "540 <= m < 1320"
check "A каждый день 09:00–22:00: 22:20 — конец обычного дня, а не блокировка → quiet" \
    '[ "$(field_of "$A" 4)" = quiet ]' "$out"
scene 1280 8 "540 <= m < (1260 if d == 7 else 1320)"
check "сегодня ушли на час раньше обычного (21:00 вместо 22:00): 21:20 → quiet" \
    '[ "$(field_of "$A" 4)" = quiet ]' "$out"
scene 550 8 "d < 7 and 540 <= m < 1310"
check "блокировка с ночи: 09:10 — пользователи обычно только приходят → ещё quiet" \
    '[ "$(field_of "$A" 4)" = quiet ]' "$out"
scene 660 8 "d < 7 and 540 <= m < 1310"
check "блокировка с ночи: 11:00 — в это время адрес обычно занят → dark, «молчит с» — вчерашний вечер" \
    '[ "$(field_of "$A" 4)" = dark ] && [ "$(field_of "$A" 6)" = "$S_A" ]' "$out | ждали $S_A"
scene 630 8 "d in (2, 3, 4, 5, 6) and 540 <= m < 1080"
check "только будни, сегодня суббота 10:30 (в прошлую субботу тоже тихо) → quiet" \
    '[ "$(field_of "$A" 4)" = quiet ]' "$out"
scene 630 10 "d in (2, 3, 4, 5, 6) and 540 <= m < 1080"
check "только будни, блокировка с вечера пятницы: понедельник 10:30 → dark" \
    '[ "$(field_of "$A" 4)" = dark ]' "$out"
scene 720 8 "d % 2 == 0 and 540 <= m < 1320"
check "пользователь через день, сегодня день пропуска → quiet (4 дня из 7 — не привычка)" \
    '[ "$(field_of "$A" 4)" = quiet ]' "$out"
scene 724 2 "(d == 0 and 540 <= m < 1320) or (d == 1 and 540 <= m < 700)"
check "история — один прошлый день: одного совпадения мало → quiet" \
    '[ "$(field_of "$A" 4)" = quiet ]' "$out"
scene 724 3 "(d < 2 and 540 <= m < 1320) or (d == 2 and 540 <= m < 660)"
check "история — два прошлых дня, оба в это время заняты → dark" \
    '[ "$(field_of "$A" 4)" = dark ]' "$out"
scene 680 16 "540 <= m < 1320 and d != 8 and not (d == 15 and m >= 660)"
check "праздник ровно неделю назад, сегодня с 11:00 блокировка: две недели назад было занято → dark" \
    '[ "$(field_of "$A" 4)" = dark ]' "$out"
scene 630 16 "d % 7 in (3, 4, 5, 6, 0) and 540 <= m < 1080"
check "только будни, суббота третьей недели: тихо и неделю, и две недели назад → quiet" \
    '[ "$(field_of "$A" 4)" = quiet ]' "$out"
scene 660 17 "d % 7 in (1, 2, 3, 4, 5) and 540 <= m < 1080 and d not in (8, 16) and not (d == 15 and m >= 660)"
check "только будни, праздник в прошлый понедельник, блокировка с понедельника: во вторник → dark (занято неделю и две назад)" \
    '[ "$(field_of "$A" 4)" = dark ]' "$out"
GA_EDGE_MIN_USERS=2
scene 660 8 "d < 7 and 540 <= m < 1310"
check "порог по умолчанию: на адресе один пользователь — отгул от блокировки не отличить → quiet" \
    '[ "$(field_of "$A" 4)" = quiet ] && [ "$(field_of "$A" 5)" = 1 ]' "$out"
run users_add carol "Карина"
GA_EDGE_NOW="$(at_min 660)" run ga_edge_report
check "второй пользователь на том же адресе → та же тишина уже dark" \
    '[ "$(field_of "$A" 4)" = dark ] && [ "$(field_of "$A" 5)" = 2 ]' "$out"
run users_del carol
GA_EDGE_MIN_USERS=1

# ── 5. Тик и тревоги ─────────────────────────────────────────────────────────
section "ga_edge_tick: переходы, сутки, гистерезис, память"
export GOAMNEZIA_DRY_RUN=0
ALERTS=$(ga_edge_alerts_file)
rm -f "$GOAMNEZIA_NOTIFY_DIR"/*.json "$ALERTS" 2>/dev/null
events() { cat "$GOAMNEZIA_NOTIFY_DIR"/*.json 2>/dev/null | jq -sc --arg e "$1" '[.[] | select(.event == $e)]'; }
mem() { jq -r --arg ip "$1" --arg k "$2" '.[$ip][$k] | tostring' "$ALERTS" 2>/dev/null; }
age_notified() {
    jq --arg ip "$A" --argjson n "$(( $(date -u +%s) - 90000 ))" '.[$ip].notified = $n' "$ALERTS" >"$TMP/alerts.json" \
        && mv "$TMP/alerts.json" "$ALERTS"
}
# Тики — через подставные часы модуля, раз в две минуты: в прогоне они иначе шли бы в одну секунду
# и сливались в один тик. fresh <сценарий> — новая история и отсчёт тиков заново.
TICK_BASE=0; TICK_N=0
fresh() { read -r LAST_A LAST_B S_A < <(gen_history "$1"); TICK_BASE=$(date -u +%s); TICK_N=0; }
tick() { TICK_N=$((TICK_N + 1)); GA_EDGE_NOW=$(( TICK_BASE + TICK_N * 120 )) run ga_edge_tick; }
fresh dark
set_counters "$LAST_A" "$(( LAST_B + 30 ))"
tick
ev=$(events edge_ip_dark)
check "тёмный адрес → одно событие edge_ip_dark: адрес, 1 пользователь, «молчит с» = начало тишины" \
    '[ "$out" = 1 ] && [ "$(jq length <<<"$ev")" = 1 ] && [ "$(jq -r ".[0].ip" <<<"$ev")" = "$A" ] && [ "$(jq -r ".[0].users" <<<"$ev")" = 1 ] && [ "$(jq -r ".[0].since" <<<"$ev")" = "$(_ga_edge_iso "$S_A")" ] && [ "$(jq -r ".[0].reminder // false" <<<"$ev")" = false ]' \
    "out=$out ev=$ev $(errs | tail -3)"
check "память тревог — в state/, права 600, эпизод объявлен" \
    '[ "$(perm "$ALERTS")" = 600 ] && [ "$(mem "$A" state)" = dark ] && [ "$(mem "$A" announced)" = true ]' "$(cat "$ALERTS")"
set_counters "$LAST_A" "$(( LAST_B + 60 ))"
tick
check "следующий тик: адрес всё ещё тёмный — тревога не повторяется" '[ "$out" = 0 ] && [ "$(jq length <<<"$(events edge_ip_dark)")" = 1 ]' "out=$out"
age_notified
set_counters "$LAST_A" "$(( LAST_B + 90 ))"
tick
check "сутки спустя адрес всё ещё тёмный → одно напоминание (reminder=true) с тем же «молчит с»" \
    '[ "$out" = 1 ] && [ "$(jq -r "[.[] | select(.reminder == true)] | length" <<<"$(events edge_ip_dark)")" = 1 ] && [ "$(jq -r "[.[] | select(.reminder == true)][0].since" <<<"$(events edge_ip_dark)")" = "$(_ga_edge_iso "$S_A")" ]' \
    "out=$out $(events edge_ip_dark)"
set_counters "$(( LAST_A + 30 ))" "$(( LAST_B + 120 ))"
tick
check "один живой тик — ещё не возврат: «снова принимает» не отправлено, адрес помнится тёмным" \
    '[ "$out" = 0 ] && [ "$(jq length <<<"$(events edge_ip_back)")" = 0 ] && [ "$(mem "$A" state)" = dark ]' "out=$out $(cat "$ALERTS")"
set_counters "$(( LAST_A + 60 ))" "$(( LAST_B + 150 ))"; tick
set_counters "$(( LAST_A + 90 ))" "$(( LAST_B + 180 ))"; tick
back=$(events edge_ip_back)
check "три живых тика подряд → одно событие edge_ip_back" \
    '[ "$out" = 1 ] && [ "$(jq length <<<"$back")" = 1 ] && [ "$(jq -r ".[0].ip" <<<"$back")" = "$A" ] && [ "$(mem "$A" state)" = ok ]' "out=$out back=$back"
rm -f "$GOAMNEZIA_NOTIFY_DIR"/*.json
fresh dark
set_counters "$LAST_A" "$(( LAST_B + 30 ))"
tick
check "снова тёмный меньше чем через сутки после тревоги → без сообщения, эпизод записан необъявленным" \
    '[ "$out" = 0 ] && [ "$(mem "$A" state)" = dark ] && [ "$(mem "$A" announced)" = false ]' "out=$out $(cat "$ALERTS")"
age_notified
set_counters "$LAST_A" "$(( LAST_B + 60 ))"
tick
check "необъявленный эпизод тянется дольше суток после прошлой тревоги → объявляется (reminder=false)" \
    '[ "$out" = 1 ] && [ "$(jq -r ".[0].reminder // false" <<<"$(events edge_ip_dark)")" = false ] && [ "$(mem "$A" announced)" = true ]' "out=$out $(events edge_ip_dark)"
rm -f "$GOAMNEZIA_NOTIFY_DIR"/*.json; : >"$ALERTS"
fresh dark
set_counters "$LAST_A" "$(( LAST_B + 30 ))"; tick
set_counters "$LAST_A" "$(( LAST_B + 60 ))"; tick
check "пустой файл памяти: одна тревога, на следующем тике — ни одной (память восстановилась)" \
    '[ "$out" = 0 ] && [ "$(jq length <<<"$(events edge_ip_dark)")" = 1 ] && [ -s "$ALERTS" ]' "out=$out $(cat "$ALERTS")"
if [ "$(id -u)" != 0 ]; then
    rm -f "$GOAMNEZIA_NOTIFY_DIR"/*.json "$ALERTS"
    fresh dark
    set_counters "$LAST_A" "$(( LAST_B + 30 ))"
    chmod 500 "$(dirname "$ALERTS")"
    tick
    chmod 700 "$(dirname "$ALERTS")"
    check "память тревог не записалась → тревога не отправлена (иначе шла бы каждый тик)" \
        '[ "$out" = 0 ] && [ "$(jq length <<<"$(events edge_ip_dark)")" = 0 ]' "out=$out"
else
    skipped "память тревог не записалась" "под root каталог только на чтение не мешает записи"
fi
_ga_edge_alert_set "$A" dark "$(date -u +%s)" "$S_A" true
_ga_edge_alert_set "$C" dark "$(date -u +%s)" 1 true
set_user anna '.enabled = false'
set_counters "$LAST_A" "$(( LAST_B + 60 ))"
tick
check "с тёмного адреса всех убрали (unused) → тревога снимается молча; память о снятом с машины адресе стёрта" \
    '[ "$out" = 0 ] && [ "$(jq length <<<"$(events edge_ip_back)")" = 0 ] && [ "$(mem "$A" state)" = ok ] && [ "$(jq -r --arg ip "$C" "has(\$ip)" "$ALERTS")" = false ]' \
    "out=$out $(cat "$ALERTS")"
set_user anna '.enabled = true'
set_addrs "$A/24"
: >"$FAKE_IPT_DIR/calls"
tick
check "один адрес у машины — сторожить нечего: 0 событий, iptables не звался" \
    '[ "$out" = 0 ] && [ ! -s "$FAKE_IPT_DIR/calls" ]' "out=$out $(cat "$FAKE_IPT_DIR/calls")"
set_addrs "$A/24" "$B/32"
export GOAMNEZIA_DRY_RUN=1

# ── 6. Пользователи по адресам ───────────────────────────────────────────────
section "ga_edge_users_on / ga_edge_users_map"
run ga_edge_users_on "$A"
check "на общем адресе — пользователи без endpoint_ip: 1" '[ "$out" = 1 ]' "$out"
run ga_edge_users_on "$B"
check "на втором адресе — пользователь с endpoint_ip: 1" '[ "$out" = 1 ]' "$out"
run ga_edge_users_on "$C"
check "на чужом адресе — 0" '[ "$out" = 0 ]' "$out"
ga_config_merge '{"endpoint_host":"vpn.example.com"}' >/dev/null 2>&1
run ga_edge_users_map
check "доменный Endpoint: все — строкой «?» (отчёт считает их на каждом адресе)" \
    '[ "$out" = "$(printf "?\t2")" ]' "$out"
ga_config_merge '{"endpoint_host":""}' >/dev/null 2>&1
# Домен сервера по умолчанию (режим Pro): свой адрес пользователя его
# побеждает, остальные — строкой «?», как при вписанном домене.
cfg_before_domain=$(cat "$CONFIG_FILE")
ga_config_merge '{"mode":"pro","domain":"vpn.example.com","site":{"cert":"letsencrypt"}}' >/dev/null 2>&1
run ga_edge_users_map
check "домен по умолчанию: пользователь со своим адресом — на нём, остальные — «?»" \
    'grep -qxF "$(printf "%s\t1" "$B")" <<<"$out" && grep -qxF "$(printf "?\t1")" <<<"$out"' "$out"
# Свой адрес и доменные складываются: раньше доменные ЗАТИРАЛИ счёт адреса, вписанного в ключ
export GOAMNEZIA_DNS_STUB="$A $B"
run ga_edge_report
check "свой адрес bob (B) и доменная anna: на B — 2, на A — 1" '[ "$(field_of "$B" 5)" = 2 ] && [ "$(field_of "$A" 5)" = 1 ]' "$out"
unset GOAMNEZIA_DNS_STUB
printf '%s\n' "$cfg_before_domain" >"$CONFIG_FILE" && chmod 600 "$CONFIG_FILE"
ga_config_merge '{"profiles":{"awg2":{"enabled":false}}}' >/dev/null 2>&1
run ga_edge_users_map
check "профиль выключен: его пользователи ни на один адрес не смотрят (порт не принимает)" '[ -z "$out" ]' "$out"
ga_config_merge '{"profiles":{"awg2":{"enabled":true}}}' >/dev/null 2>&1
set_user anna '.peers.awg2.active = false'
run ga_edge_users_on "$A"
check "пир снят из конфига (active=false) — пользователь на адрес не смотрит" '[ "$out" = 0 ]' "$out"
set_user anna '.peers.awg2.active = true'

DONE=1
printf '\n══ ИТОГ: пройдено %d, провалено %d, пропущено %d\n' "$pass" "$fail" "$skip"
[ "$fail" -eq 0 ]
