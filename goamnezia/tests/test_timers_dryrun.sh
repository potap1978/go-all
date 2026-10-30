#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
# goAmnezia pro — таймеры systemd, сторож и статистика без сервера
# (GOAMNEZIA_DRY_RUN=1, юниты в GOAMNEZIA_SYSTEMD_DIR, systemctl — «DRY: …»).
#
# Запуск: ${BASH5:-/opt/homebrew/bin/bash} tests/test_timers_dryrun.sh
#
# Что проверяем (CONTRACT §1, §4.11, §4.12):
#   timers_install пишет 4 пары service/timer с ожидаемыми OnBootSec /
#   OnUnitActiveSec / OnCalendar и ExecStart через мост --action, daemon-reload
#   и enable --now в DRY; повтор — без изменений; timers_status; timers_remove →
#   stats_tick на подложенном `awg show dump`: дельты по профилю и пользователю,
#   обнуление счётчиков при рестарте, month/today, обрезка до 30 суток →
#   сторож: exited → docker start и событие container_restarted, интерфейс
#   не отвечает → docker restart, дедупликация уведомлений, контейнера нет →
#   container_missing → тики через настоящий install.sh --action: quota-tick,
#   watch-tick, stats-tick, upstream-tick отвечают одной JSON-строкой; traffic
#   несёт today/month/days; quality — timers →
#   правила logrotate в песочнице: чужой конфиг nginx уходит ИЗ каталога (а не
#   переименовывается в нём), хвост .goamnezia-disabled от прошлых версий
#   подбирается, снятие возвращает штатный файл на место →
#   рассогласование версий Docker (регрессия стенда 10.09.2026, подставной
#   docker из tests/fixtures/fake_docker.sh): версия API демона определяется по
#   сокету и по тексту ошибки и закрепляется, профиль не становится missing,
#   сторож не шлёт «отсутствует» и шлёт одно событие docker_unreachable на тик,
#   тик квот отдаёт ошибку вместо success с нулями, quality называет
#   рассогласование, при совпадении версий поведение прежнее; предел очереди
#   уведомлений → старый демон (DK-02): пакет обновлён, служба нет — версии и
#   сборки бинаря и демона, запасной путь по API; сторож при людях на связи ждёт
#   и говорит один раз, без людей перезапускает Docker и поднимает профили
#   (события до и после, журнал действий), шесть часов между попытками, занятый
#   замок и dpkg — пропуск, сбой — событие с ручной командой; мост docker-restart
#   и поле status.docker_stale.
# ══════════════════════════════════════════════════════════════════════════════
set -uo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
INSTALL="$ROOT/install.sh"
BASH5="${BASH5:-$BASH}"
TMP=$(mktemp -d "${TMPDIR:-/tmp}/goamnezia-timers.XXXXXX") || exit 2
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
unset GOAMNEZIA_UPSTREAM_FIXTURE_DIR GOAMNEZIA_AWG_SHOW_FIXTURE
mkdir -p "$GOAMNEZIA_DIR" "$GOAMNEZIA_LOCK_DIR" "$UPSTREAM_DIR" "$GOAMNEZIA_CONTAINER_FS_DIR"
cp -R "$ROOT/lib/upstream/." "$UPSTREAM_DIR/"
# DOCKER_HOST (rootless docker, dind в CI) продукт ставит выше GOAMNEZIA_DOCKER_SOCK — и проверки
# «демон не отвечает» ниже спрашивали бы настоящий демон машины.
unset DOCKER_HOST DOCKER_CONTEXT

# Временные файлы с ключами — в каталоге стенда (L1-19). Без переменной продукт кладёт их в /dev/shm,
# а он есть только на Linux: там они жили бы на машине прогона, и оборванный прогон оставлял бы ключи.
export GOAMNEZIA_SECRET_TMPDIR="$TMP/shm"; mkdir -p "$GOAMNEZIA_SECRET_TMPDIR"

pass=0; fail=0; skip=0; rc=0; out=""
ok()   { pass=$((pass + 1)); printf '  ✓ %s\n' "$1"; }
bad()  { fail=$((fail + 1)); printf '  ✗ %s\n' "$1"; [ -n "${2:-}" ] && printf '      %s\n' "$2" | head -c 700; return 0; }
check() { if eval "$2" >/dev/null 2>&1; then ok "$1"; else bad "$1" "${3:-}"; fi; }
# audit_has <действие> [начало details] [who] — есть ли запись в журнале аудита.
# Журнал — один объект JSON на строку (CONTRACT §7.1); прежние проверки искали
# поля через табуляцию и после смены формата молча не находили ничего.
audit_has() {
    jq -R -e -n --arg a "$1" --arg d "${2:-}" --arg w "${3:-}" \
        '[inputs | fromjson? // empty | select(.action == $a and ($w == "" or .who == $w)
          and ((.details // "") | startswith($d)))] | length > 0' "$GOAMNEZIA_AUDIT_LOG" >/dev/null 2>&1
}
section() { printf '\n── %s\n' "$1"; }
is_json() { jq -e . <<<"$1" >/dev/null 2>&1; }
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
jget() { jq -r "$1" <<<"$out" 2>/dev/null; }
# act — настоящий install.sh --action процессом, как бот и таймер
last=""; nlines=0
act() {
    local action="$1"; shift
    "$BASH5" "$INSTALL" --action="$action" --json "$@" >"$OUT" 2>"$ERR"; rc=$?
    last=$(tail -n 1 "$OUT"); nlines=$(wc -l <"$OUT" | tr -d ' ')
    return 0
}
lget() { jq -r "$1" <<<"$last" 2>/dev/null; }

section "окружение"
# shellcheck disable=SC1091
source "$ROOT/lib/common.sh"
# shellcheck disable=SC1091
source "$ROOT/lib/i18n.sh"
load_language ru >/dev/null 2>&1
for _m in docker amnezia users export backup timers stats upstream; do
    # shellcheck disable=SC1090
    source "$ROOT/lib/$_m.sh"
done
mkdir -p "$STATE_DIR"; chmod 700 "$STATE_DIR"
# Версия в фикстуре — ТЕКУЩАЯ из lib/common.sh, а не литерал: со старым номером
# каждый подъём версии включал бы миграцию, а она делает предмиграционный бэкап —
# лишний архив ломал проверки списка бэкапов.
GA_VER=$(sed -n 's/^GOAMNEZIA_VERSION="\([^"]*\)".*/\1/p' "$ROOT/lib/common.sh" | head -1)
cat >"$CONFIG_FILE" <<EOF
{"schema": 1, "version": "$GA_VER", "installed": true, "mode": "lite", "lang": "ru", "domain": "",
 "server_ip": "203.0.113.10", "endpoint_host": "", "client_dns": ["1.1.1.1", "1.0.0.1"], "profiles": {}}
EOF
chmod 600 "$CONFIG_FILE"
printf '{"schema": 1, "users": []}\n' >"$USERS_FILE"; chmod 600 "$USERS_FILE"
run amnezia_profile_install awg2 40000 default
check "профиль awg2 установлен (dry)" '[ "$rc" = 0 ]' "$(errs)"
run users_add anna "Анна"
run users_add bob "Боб"
check "пользователи anna и bob" '[ "$(jq -r ".users|length" "$USERS_FILE")" = 2 ]'
ANNA_PUB=$(jq -r '.users[0].peers.awg2.pubkey' "$USERS_FILE"); BOB_PUB=$(jq -r '.users[1].peers.awg2.pubkey' "$USERS_FILE")

# ── 1. Юниты ─────────────────────────────────────────────────────────────────
section "timers_install: юниты и таймеры"
run timers_install
check "timers_install: rc=0, changed = quota,watch,stats,upstream,cert; enabled все пять" \
    '[ "$rc" = 0 ] && [ "$(jget ".changed | join(\",\")")" = "quota,watch,stats,upstream,cert" ] && [ "$(jget ".enabled|length")" = 5 ]' "rc=$rc $out $(errs | tail -3)"
SD="$GOAMNEZIA_SYSTEMD_DIR"
# Десять юнитов таймеров и служба изоляции (OPS-05, tests/test_update_dryrun.sh).
check "11 файлов в GOAMNEZIA_SYSTEMD_DIR (10 таймеров и служба изоляции), права 644" \
    '[ "$(ls "$SD" | wc -l | tr -d " ")" = 11 ] && [ -f "$SD/goamnezia-quota.timer" ] && [ -f "$SD/goamnezia-upstream.service" ] && [ -f "$SD/goamnezia-isolation.service" ] && [ "$(perm "$SD/goamnezia-quota.timer")" = 644 ]' "$(ls "$SD")"
check "goamnezia-quota.timer: OnBootSec=3min, OnUnitActiveSec=5min, Unit=goamnezia-quota.service, WantedBy=timers.target" \
    'grep -q "^OnBootSec=3min$" "$SD/goamnezia-quota.timer" && grep -q "^OnUnitActiveSec=5min$" "$SD/goamnezia-quota.timer" && grep -q "^Unit=goamnezia-quota.service$" "$SD/goamnezia-quota.timer" && grep -q "^WantedBy=timers.target$" "$SD/goamnezia-quota.timer"' "$(cat "$SD/goamnezia-quota.timer")"
check "goamnezia-watch.timer: OnUnitActiveSec=2min; goamnezia-stats.timer: 10min" \
    'grep -q "^OnUnitActiveSec=2min$" "$SD/goamnezia-watch.timer" && grep -q "^OnUnitActiveSec=10min$" "$SD/goamnezia-stats.timer"'
check "goamnezia-upstream.timer: OnCalendar=*-*-* 05:10:00, Persistent=true, RandomizedDelaySec=1h, без OnUnitActiveSec" \
    'grep -q "^OnCalendar=\*-\*-\* 05:10:00$" "$SD/goamnezia-upstream.timer" && grep -q "^Persistent=true$" "$SD/goamnezia-upstream.timer" && grep -q "^RandomizedDelaySec=1h$" "$SD/goamnezia-upstream.timer" && ! grep -q OnUnitActiveSec "$SD/goamnezia-upstream.timer"' "$(cat "$SD/goamnezia-upstream.timer")"
check "goamnezia-quota.service: oneshot, ExecStart=/bin/bash <GOAMNEZIA_DIR>/install.sh --action=quota-tick --json, GOAMNEZIA_WHO=timer, After=docker.service" \
    'grep -q "^Type=oneshot$" "$SD/goamnezia-quota.service" && grep -q "^ExecStart=/bin/bash $GOAMNEZIA_DIR/install.sh --action=quota-tick --json$" "$SD/goamnezia-quota.service" && grep -q "^Environment=GOAMNEZIA_WHO=timer$" "$SD/goamnezia-quota.service" && grep -q "^After=docker.service" "$SD/goamnezia-quota.service"' "$(cat "$SD/goamnezia-quota.service")"
# SR-38: у oneshot таймаута по умолчанию нет — зависший docker держал бы общий замок бесконечно.
check "SR-38 у юнитов тиков есть потолок прогона (TimeoutStartSec): квоты 10 мин, сторож 20 мин" \
    'grep -q "^TimeoutStartSec=10min$" "$SD/goamnezia-quota.service" && grep -q "^TimeoutStartSec=20min$" "$SD/goamnezia-watch.service"' "$(cat "$SD/goamnezia-watch.service")"
check "остальные service зовут watch-tick, stats-tick, upstream-tick, cert-tick; описания из словаря" \
    'grep -q -- "--action=watch-tick --json" "$SD/goamnezia-watch.service" && grep -q -- "--action=stats-tick --json" "$SD/goamnezia-stats.service" && grep -q -- "--action=upstream-tick --json" "$SD/goamnezia-upstream.service" && grep -q -- "--action=cert-tick --json" "$SD/goamnezia-cert.service" && grep -q "^Description=goAmnezia pro" "$SD/goamnezia-watch.timer"'
# §1d.2 №11: сторож срока сертификата — суточный таймер, которого не было вовсе.
check "goamnezia-cert.timer: суточный OnCalendar=*-*-* 04:40:00, Persistent=true, без OnUnitActiveSec" \
    'grep -q "^OnCalendar=\*-\*-\* 04:40:00$" "$SD/goamnezia-cert.timer" && grep -q "^Persistent=true$" "$SD/goamnezia-cert.timer" && ! grep -q OnUnitActiveSec "$SD/goamnezia-cert.timer"' "$(cat "$SD/goamnezia-cert.timer" 2>/dev/null)"
check "DRY: systemctl daemon-reload и enable --now для пяти таймеров; set_backup_schedule/logb на стенде не зовутся" \
    'grep -q "DRY: systemctl daemon-reload" "$ERR" && [ "$(grep -c "DRY: systemctl enable --now goamnezia-.*\.timer" "$ERR")" = 5 ] && ! grep -q "backup_schedule\|logrotate" "$ERR"' "$(errs)"
# OPS-05: служба изоляции (tests/test_update_dryrun.sh) — тем же проходом: включена и запущена заново.
check "служба изоляции goamnezia-isolation.service: enable и restart тем же проходом" \
    'grep -q "DRY: systemctl enable goamnezia-isolation.service" "$ERR" && grep -q "DRY: systemctl restart goamnezia-isolation.service" "$ERR" && grep -q -- "--isolation-watch" "$SD/goamnezia-isolation.service"' "$(errs)"
run timers_install
check "повторный timers_install: changed=[] (идемпотентно), файлы не переписаны" '[ "$rc" = 0 ] && [ "$(jget ".changed|length")" = 0 ]' "$out"
run timers_status
check "timers_status: 9 записей — свои пять плюс чужие четыре, которые продукт только смотрит" \
    '[ "$rc" = 0 ] && [ "$(jget length)" = 9 ] && [ "$(jget "[.[] | select(.name == \"quota\" or .name == \"watch\" or .name == \"stats\" or .name == \"upstream\" or .name == \"cert\") | .installed] | all")" = true ] && [ "$(jget "[.[].state] | unique | join(\",\")")" = dry ] && [ "$(jget ".[] | select(.name == \"watch\") | .action")" = watch-tick ] && [ -n "$(jget ".[] | select(.name == \"backup\") | .timer")" ]' "$out"
run timers_remove
check "timers_remove: файлов нет, DRY disable --now × 5 таймеров и служба изоляции" '[ "$rc" = 0 ] && [ -z "$(ls "$SD" 2>/dev/null)" ] && [ "$(grep -c "DRY: systemctl disable --now goamnezia-.*\.timer" "$ERR")" = 5 ] && grep -q "DRY: systemctl disable --now goamnezia-isolation.service" "$ERR"' "$(ls "$SD") $(errs)"
run timers_install
check "timers_install после удаления снова пишет 11 файлов" '[ "$(ls "$SD" | wc -l | tr -d " ")" = 11 ]'
GOAMNEZIA_SYSTEMD_DIR="" run timers_install
check "без GOAMNEZIA_SYSTEMD_DIR в dry-run — только «DRY: write …», на диск ничего" '[ "$rc" = 0 ] && grep -q "DRY: write /etc/systemd/system/goamnezia-quota.timer" "$ERR"' "$(errs)"
# Дефект 1a.5: _timers_write_unit кладёт причину в GA_ERR_CODE, а её брали через
# подстановку команды — код оставался в дочернем процессе, и наружу доходил
# голый rc. Агент моста не отличал «нет прав на каталог юнитов» от любой другой
# беды. Каталог юнитов под обычным файлом — mkdir -p не пройдёт и у root.
printf 'не каталог\n' >"$TMP/not-a-dir"
GOAMNEZIA_SYSTEMD_DIR="$TMP/not-a-dir/systemd" run timers_install
check "юнит не записался: rc≠0 И код unit_failed доехал до вызывающего (не потерян в подоболочке)" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = unit_failed ] && [ "$GA_LAST_ERROR_CODE" = unit_failed ]' "rc=$rc code=$GA_ERR_CODE last=$GA_LAST_ERROR_CODE $(errs | tail -2)"
check "сообщение называет путь, который не удалось записать" \
    'grep -q "not-a-dir/systemd/goamnezia-quota.service" "$ERR"' "$(errs | tail -2)"

# ── 2. Статистика ────────────────────────────────────────────────────────────
section "stats_tick: дельты по профилю и пользователям"
export GOAMNEZIA_STATS_TODAY="2026-09-09"
dump() { # anna_rx anna_tx bob_rx bob_tx
    printf 'PRIV\tPUB\t40000\toff\n%s\tPSK\t198.51.100.7:1234\t10.8.2.1/32\t1757400000\t%s\t%s\t25\n%s\tPSK\t(none)\t10.8.2.2/32\t0\t%s\t%s\t25\n' \
        "$ANNA_PUB" "$1" "$2" "$BOB_PUB" "$3" "$4" >"$TMP/dump.txt"
}
dump 1000 2000 300 400
export GOAMNEZIA_AWG_SHOW_FIXTURE="$TMP/dump.txt"
run stats_tick
check "первый тик: today awg2 rx 1300 tx 2400 (сумма пиров), anna 1000/2000, bob 300/400; stats.json 0600" \
    '[ "$rc" = 0 ] && [ "$(jget .day)" = 2026-09-09 ] && [ "$(jget ".profiles[0].rx")" = 1300 ] && [ "$(jget ".profiles[0].tx")" = 2400 ] && [ "$(jq -r ".days[\"2026-09-09\"].users.anna.rx" "$GOAMNEZIA_DIR/stats.json")" = 1000 ] && [ "$(jq -r ".days[\"2026-09-09\"].users.bob.tx" "$GOAMNEZIA_DIR/stats.json")" = 400 ] && [ "$(perm "$GOAMNEZIA_DIR/stats.json")" = 600 ]' "rc=$rc $out $(errs | tail -2)"
dump 1500 2000 300 900
run stats_tick
check "второй тик: дельты (anna +500 rx, bob +500 tx) → профиль 1800/2900" \
    '[ "$(jget ".profiles[0].rx")" = 1800 ] && [ "$(jget ".profiles[0].tx")" = 2900 ] && [ "$(jq -r ".days[\"2026-09-09\"].users.anna.rx" "$GOAMNEZIA_DIR/stats.json")" = 1500 ]' "$out"
dump 100 50 300 900
run stats_tick
check "рестарт контейнера (показание меньше прошлого): дельта = новое показание → anna rx 1600, tx 2050" \
    '[ "$(jq -r ".days[\"2026-09-09\"].users.anna.rx" "$GOAMNEZIA_DIR/stats.json")" = 1600 ] && [ "$(jq -r ".days[\"2026-09-09\"].users.anna.tx" "$GOAMNEZIA_DIR/stats.json")" = 2050 ]' "$(jq -c ".days" "$GOAMNEZIA_DIR/stats.json")"
GOAMNEZIA_STATS_TODAY="2026-09-10" dump 200 100 300 900
GOAMNEZIA_STATS_TODAY="2026-09-10" run stats_tick
check "новый день: свой ящик (anna +100/+50), вчерашний не тронут" \
    '[ "$(jq -r ".days[\"2026-09-10\"].users.anna.rx" "$GOAMNEZIA_DIR/stats.json")" = 100 ] && [ "$(jq -r ".days[\"2026-09-09\"].users.anna.rx" "$GOAMNEZIA_DIR/stats.json")" = 1600 ]'
GOAMNEZIA_STATS_TODAY="2026-09-10" run stats_summary
check "stats_summary: today 2026-09-10 (rx 100), month с 2026-09-01 = сумма двух дней (rx 1900+100=… by_user anna 1700), days 2" \
    '[ "$rc" = 0 ] && [ "$(jget .today.date)" = 2026-09-10 ] && [ "$(jget .today.rx)" = 100 ] && [ "$(jget .month.from)" = 2026-09-01 ] && [ "$(jget .month.by_user.anna.rx)" = 1700 ] && [ "$(jget ".days|length")" = 2 ] && [ "$(jget ".days[0].date")" = 2026-09-09 ]' "$out"
# обрезка: подкладываем 40 старых дней
jq -c '.days += ([range(1;41)] | map({key: ("2026-07-" + (if . < 10 then "0" else "" end) + (.|tostring)), value: {rx: 1, tx: 1, profiles: {}, users: {}}}) | map(select(.key | test("2026-07-(0[1-9]|[12][0-9]|3[01])$"))) | from_entries)' "$GOAMNEZIA_DIR/stats.json" >"$TMP/s.json" && mv "$TMP/s.json" "$GOAMNEZIA_DIR/stats.json"
GOAMNEZIA_STATS_TODAY="2026-09-10" run stats_tick
check "обрезка: хранится не больше 30 суток, самые старые ушли, сегодняшний остался" \
    '[ "$(jq -r ".days|length" "$GOAMNEZIA_DIR/stats.json")" = 30 ] && jq -e ".days[\"2026-09-10\"]" "$GOAMNEZIA_DIR/stats.json" >/dev/null && ! jq -e ".days[\"2026-07-01\"]" "$GOAMNEZIA_DIR/stats.json" >/dev/null 2>&1' "$(jq -r ".days|keys|.[0]" "$GOAMNEZIA_DIR/stats.json")"
# 1a.15. Чистка last. Комментарий обещал её с самого начала, а выражение
# трогало только метку времени и сутки: ключи копились при каждом перевыпуске
# пира и не удалялись никогда. Чистим ТОЛЬКО опрошенные профили — иначе у
# профиля с лежащим контейнером пропала бы база, и на ближайшем тике весь
# накопительный счётчик засчитался бы как трафик за сутки.
jq -c '.last["awg2/STALEPEER"] = {rx: 7, tx: 7} | .last["awg3/SLEEPING"] = {rx: 9, tx: 9}' \
    "$GOAMNEZIA_DIR/stats.json" >"$TMP/s.json" && mv "$TMP/s.json" "$GOAMNEZIA_DIR/stats.json"
GOAMNEZIA_STATS_TODAY="2026-09-10" run stats_tick
check "чистка last: ключ выбывшего пира awg2 удалён, живые anna и bob остались" \
    '! jq -e ".last[\"awg2/STALEPEER\"]" "$GOAMNEZIA_DIR/stats.json" >/dev/null 2>&1 && jq -e ".last[\"awg2/$ANNA_PUB\"]" "$GOAMNEZIA_DIR/stats.json" >/dev/null && jq -e ".last[\"awg2/$BOB_PUB\"]" "$GOAMNEZIA_DIR/stats.json" >/dev/null' \
    "$(jq -c '.last|keys' "$GOAMNEZIA_DIR/stats.json")"
check "у НЕопрошенного профиля база показаний сохранена (иначе дельта соврёт вдвое)" \
    'jq -e ".last[\"awg3/SLEEPING\"]" "$GOAMNEZIA_DIR/stats.json" >/dev/null' "$(jq -c '.last|keys' "$GOAMNEZIA_DIR/stats.json")"

# 1a.16. Битый stats.json: тот же узор, что признан дефектом для config.json и
# users.json. Раньше предупреждение уходило в журнал таймера, а ближайший тик
# через десять минут переписывал файл скелетом — месяц статистики исчезал, и
# копии битого файла не оставалось.
stats_keep=$(cat "$GOAMNEZIA_DIR/stats.json")
printf 'garbage' >"$GOAMNEZIA_DIR/stats.json"
run stats_tick
check "битый stats.json: тик отказывает кодом stats_corrupt" \
    '[ "$rc" != 0 ] && [ "$GA_LAST_ERROR_CODE" = stats_corrupt ] && [ "$GA_ERR_CODE" = stats_corrupt ]' "rc=$rc code=$GA_LAST_ERROR_CODE $(errs)"
check "причина уходит наружу текстом (мост читает GA_ERR_MSG)" \
    '[ -n "$GA_ERR_MSG" ] && grep -qF "stats.json" <<<"$GA_ERR_MSG"' "msg=$GA_ERR_MSG"
check "битый stats.json не затёрт скелетом" '[ "$(cat "$GOAMNEZIA_DIR/stats.json")" = garbage ]' "$(head -c 80 "$GOAMNEZIA_DIR/stats.json")"
check "копия битого файла спасена в backups/ с правами 600" \
    '[ -n "$(ls "$BACKUP_DIR"/stats.json.broken-* 2>/dev/null)" ] && [ "$(perm "$(ls "$BACKUP_DIR"/stats.json.broken-* | head -1)")" = 600 ]' "$(ls "$BACKUP_DIR" 2>/dev/null)"
check "отказ называет файл и спасённую копию" 'grep -q "stats.json.broken-" "$ERR"' "$(errs)"
run stats_summary
check "сводка на битом файле тоже отказывает, а не говорит «трафика не было»" \
    '[ "$rc" != 0 ] && [ -z "$out" ]' "rc=$rc out=$(head -c 80 <<<"$out")"
printf '%s' "$stats_keep" >"$GOAMNEZIA_DIR/stats.json"
GOAMNEZIA_STATS_TODAY="2026-09-10" run stats_tick
check "файл починили — тик снова работает" '[ "$rc" = 0 ]' "rc=$rc $(errs)"

section "HM-1: почасовые корзины (карта активности 7×24)"
# Час — UTC, ключ «YYYY-MM-DDTHH»; rx/tx — те же дельты тика, что в сутках; active — максимум за час числа людей
# «на связи» (рукопожатие моложе 180 с — окно «●» интерфейсов). Время тика задаёт GOAMNEZIA_STATS_NOW.
HM_T=1790488800   # 2026-09-27T06:00:00Z
dump_hs() { # anna_rx anna_tx bob_rx bob_tx anna_hs bob_hs
    printf 'PRIV\tPUB\t40000\toff\n%s\tPSK\t198.51.100.7:1234\t10.8.2.1/32\t%s\t%s\t%s\t25\n%s\tPSK\t(none)\t10.8.2.2/32\t%s\t%s\t%s\t25\n' \
        "$ANNA_PUB" "$5" "$1" "$2" "$BOB_PUB" "$6" "$3" "$4" >"$TMP/dump.txt"
}
hours_of() { jq -c -S --arg h "$1" '.hours[$h] // null' "$GOAMNEZIA_DIR/stats.json"; }
rm -f "$GOAMNEZIA_DIR/stats.json"
export GOAMNEZIA_STATS_TODAY="2026-09-27"
dump_hs 1000 2000 300 400 $((HM_T + 500)) 0
GOAMNEZIA_STATS_NOW=$((HM_T + 600)) run stats_tick
check "HM-1 первый тик 06:10: корзина 06 — rx 1300, tx 2400, на связи 1 (anna; у bob рукопожатия нет)" \
    '[ "$rc" = 0 ] && [ "$(hours_of 2026-09-27T06)" = "{\"active\":1,\"rx\":1300,\"tx\":2400}" ]' "rc=$rc $(hours_of 2026-09-27T06) $(errs | tail -2)"
dump_hs 1500 2000 300 900 $((HM_T + 1100)) $((HM_T + 1150))
GOAMNEZIA_STATS_NOW=$((HM_T + 1200)) run stats_tick
check "HM-1 второй тик 06:20: дельты +500/+500 в тот же час, на связи уже двое" \
    '[ "$(hours_of 2026-09-27T06)" = "{\"active\":2,\"rx\":1800,\"tx\":2900}" ]' "$(hours_of 2026-09-27T06)"
GOAMNEZIA_STATS_NOW=$((HM_T + 3000)) run stats_tick
check "HM-1 тик 06:50 без рукопожатий: active остаётся максимумом часа (2), трафика нет — rx/tx прежние" \
    '[ "$(hours_of 2026-09-27T06)" = "{\"active\":2,\"rx\":1800,\"tx\":2900}" ]' "$(hours_of 2026-09-27T06)"
dump_hs 1600 2100 300 900 $((HM_T + 3630)) $((HM_T + 1150))
GOAMNEZIA_STATS_NOW=$((HM_T + 3660)) run stats_tick
check "HM-1 переход через час (07:01): новая корзина 07 — rx 100, tx 100, на связи 1; корзина 06 не тронута" \
    '[ "$(hours_of 2026-09-27T07)" = "{\"active\":1,\"rx\":100,\"tx\":100}" ] && [ "$(hours_of 2026-09-27T06)" = "{\"active\":2,\"rx\":1800,\"tx\":2900}" ] && [ "$(jq -r .days[\"2026-09-27\"].rx "$GOAMNEZIA_DIR/stats.json")" = 1900 ]' \
    "$(jq -c .hours "$GOAMNEZIA_DIR/stats.json")"
# Обрезка: 193 часа назад — за краем 192 часов, уходит; 170 и 100 часов назад — остаются в файле.
hm_key() { jq -rn --argjson t "$1" '$t | strftime("%Y-%m-%dT%H")'; }
HM_NOW=$((HM_T + 3660))
HM_OLD=$(hm_key $((HM_NOW - 193 * 3600))); HM_170=$(hm_key $((HM_NOW - 170 * 3600))); HM_100=$(hm_key $((HM_NOW - 100 * 3600)))
jq -c --arg o "$HM_OLD" --arg a "$HM_170" --arg b "$HM_100" \
    '.hours[$o] = {rx: 1, tx: 1, active: 1} | .hours[$a] = {rx: 2, tx: 2, active: 2} | .hours[$b] = {rx: 3, tx: 3, active: 3}' \
    "$GOAMNEZIA_DIR/stats.json" >"$TMP/s.json" && mv "$TMP/s.json" "$GOAMNEZIA_DIR/stats.json"
GOAMNEZIA_STATS_NOW=$HM_NOW run stats_tick
check "HM-1 обрезка тем же проходом: старше 192 часов ушло, 170 и 100 часов назад — на месте; файл 0600" \
    '[ "$(hours_of "$HM_OLD")" = null ] && [ "$(hours_of "$HM_170")" != null ] && [ "$(hours_of "$HM_100")" != null ] && [ "$(perm "$GOAMNEZIA_DIR/stats.json")" = 600 ] && [ "$(jq -r .schema "$GOAMNEZIA_DIR/stats.json")" = 1 ]' \
    "$(jq -c '.hours|keys' "$GOAMNEZIA_DIR/stats.json")"
GOAMNEZIA_STATS_NOW=$HM_NOW run stats_summary
check "HM-1 stats_summary.hours: последние 168 часов по возрастанию, {hour: …:00Z, rx, tx, active}; 170 часов назад — за окном" \
    '[ "$rc" = 0 ] && [ "$(jget ".hours|length")" = 3 ] && [ "$(jget ".hours[0].hour")" = "${HM_100}:00Z" ] && [ "$(jget ".hours[1]|to_entries|sort_by(.key)|from_entries|tojson")" = "{\"active\":2,\"hour\":\"2026-09-27T06:00Z\",\"rx\":1800,\"tx\":2900}" ] && [ "$(jget ".hours[2].hour")" = 2026-09-27T07:00Z ]' \
    "$(printf '%s' "$out" | head -c 600)"
# Файл прежней версии без hours читается как есть, а первый тик добавляет ключ, не трогая остального.
printf '%s' '{"schema":1,"updated":"","last":{},"days":{"2026-09-26":{"rx":5,"tx":7,"profiles":{},"users":{}}}}' >"$GOAMNEZIA_DIR/stats.json"
chmod 600 "$GOAMNEZIA_DIR/stats.json"
GOAMNEZIA_STATS_NOW=$HM_NOW run stats_summary
check "HM-1 старый stats.json без hours: сводка успешна, hours — пустой список, сутки на месте" \
    '[ "$rc" = 0 ] && [ "$(jget ".hours|tostring")" = "[]" ] && [ "$(jget ".days[0].rx")" = 5 ]' "rc=$rc $out"
GOAMNEZIA_STATS_NOW=$HM_NOW run stats_tick
check "HM-1 старый stats.json: тик проходит, появляется корзина текущего часа, прежние сутки не тронуты, schema 1" \
    '[ "$rc" = 0 ] && [ "$(hours_of 2026-09-27T07)" != null ] && [ "$(jq -r .days[\"2026-09-26\"].tx "$GOAMNEZIA_DIR/stats.json")" = 7 ] && [ "$(jq -r .schema "$GOAMNEZIA_DIR/stats.json")" = 1 ]' \
    "rc=$rc $(errs | tail -2)"
unset GOAMNEZIA_AWG_SHOW_FIXTURE GOAMNEZIA_STATS_TODAY

# ── 3. Сторож ────────────────────────────────────────────────────────────────
section "amnezia_watch_tick: сторож контейнеров"
run amnezia_watch_tick
check "контейнера нет (dry без фикстуры): state missing, ok=false, событие container_missing" \
    '[ "$rc" = 0 ] && [ "$(jget ".profiles[0].state")" = missing ] && [ "$(jget ".profiles[0].ok")" = false ] && [ "$(jget .events)" = 1 ] && [ "$(jq -r .event "$(ls "$GOAMNEZIA_NOTIFY_DIR"/*.json | head -1)")" = container_missing ]' "$out $(errs)"
rm -f "$GOAMNEZIA_NOTIFY_DIR"/*.json
run amnezia_watch_tick
check "повтор через минуту: то же событие не дублируется (events=0)" '[ "$(jget .events)" = 0 ] && [ -z "$(ls "$GOAMNEZIA_NOTIFY_DIR"/*.json 2>/dev/null)" ]' "$out"
mkdir -p "$GOAMNEZIA_CONTAINER_FS_DIR/amnezia-awg2/opt/amnezia/awg"
run amnezia_watch_tick
check "контейнер работает (фикстура): action none, событий нет, память сторожа сброшена" '[ "$(jget ".profiles[0].state")" = running ] && [ "$(jget ".profiles[0].action")" = none ] && [ "$(jget .events)" = 0 ]' "$out"
printf 'exited\n' >"$GOAMNEZIA_CONTAINER_FS_DIR/amnezia-awg2/.state"
run amnezia_watch_tick
check "контейнер остановлен: DRY docker start, action=started, ok=true, событие container_restarted{action started, ok true}" \
    '[ "$(jget ".profiles[0].state")" = exited ] && [ "$(jget ".profiles[0].action")" = started ] && [ "$(jget ".profiles[0].ok")" = true ] && grep -q "DRY: docker start amnezia-awg2" "$ERR" && [ "$(jq -r .event "$(ls "$GOAMNEZIA_NOTIFY_DIR"/*.json | head -1)")" = container_restarted ] && [ "$(jq -r .action "$(ls "$GOAMNEZIA_NOTIFY_DIR"/*.json | head -1)")" = started ]' "$out $(errs)"
rm -f "$GOAMNEZIA_NOTIFY_DIR"/*.json
check "после docker start фикстура снова running (файл .state снят)" '[ ! -f "$GOAMNEZIA_CONTAINER_FS_DIR/amnezia-awg2/.state" ]'
touch "$GOAMNEZIA_CONTAINER_FS_DIR/amnezia-awg2/.iface_down"
run amnezia_watch_tick
check "интерфейс не отвечает при работающем контейнере: DRY docker restart, action=restarted, событие" \
    '[ "$(jget ".profiles[0].action")" = restarted ] && grep -q "DRY: docker restart amnezia-awg2" "$ERR" && [ "$(jq -r .action "$(ls "$GOAMNEZIA_NOTIFY_DIR"/*.json | head -1)")" = restarted ]' "$out $(errs)"
rm -f "$GOAMNEZIA_CONTAINER_FS_DIR/amnezia-awg2/.iface_down" "$GOAMNEZIA_NOTIFY_DIR"/*.json
check "аудит сторожа: container_restarted awg2" 'audit_has container_restarted "awg2 " watch' "$(grep watch "$GOAMNEZIA_AUDIT_LOG")"
ga_config_merge '{"profiles":{"awg2":{"enabled":false}}}' >/dev/null 2>&1
run amnezia_watch_tick
check "выключенный профиль сторож не трогает" '[ "$(jget ".profiles|length")" = 0 ]' "$out"
ga_config_merge '{"profiles":{"awg2":{"enabled":true}}}' >/dev/null 2>&1

# ── 4. Тики через настоящий install.sh ───────────────────────────────────────
section "тики через install.sh --action (как из юнитов)"
act quota-tick
check "quota-tick: одна JSON-строка, success, tick=quota, users=2" '[ "$nlines" = 1 ] && is_json "$last" && [ "$(lget .status)" = success ] && [ "$(lget .tick)" = quota ] && [ "$(lget .users)" = 2 ]' "$last $(errs | tail -2)"
act watch-tick
check "watch-tick: success, profiles[0].state=running (фикстура), events=0" '[ "$nlines" = 1 ] && [ "$(lget .status)" = success ] && [ "$(lget ".profiles[0].state")" = running ]' "$last"
act stats-tick
check "stats-tick: success, day и total" '[ "$nlines" = 1 ] && [ "$(lget .status)" = success ] && [ -n "$(lget .day)" ] && [ "$(lget ".total|type")" = object ]' "$last"
act upstream-tick
check "upstream-tick без сети: success, reachable=false (таймер не падает)" '[ "$nlines" = 1 ] && [ "$(lget .status)" = success ] && [ "$(lget .reachable)" = false ]' "$last"
GOAMNEZIA_UPSTREAM_FIXTURE_DIR="$ROOT/tests/fixtures/upstream" act upstream-tick
check "upstream-tick с фикстурой: updates_available=true, notified=true, событие upstream_available в очереди" \
    '[ "$(lget .updates_available)" = true ] && [ "$(lget .notified)" = true ] && [ "$(jq -r .event "$(ls "$GOAMNEZIA_NOTIFY_DIR"/*.json | head -1)")" = upstream_available ]' "$last"
act traffic
check "traffic: today{date, rx, tx, by_profile, by_user}, month{from}, days[]" \
    '[ "$(lget .status)" = success ] && [ "$(lget ".today|type")" = object ] && [ -n "$(lget .today.date)" ] && [ "$(lget ".month|type")" = object ] && [ "$(lget ".days|type")" = array ]' "$last"
act quality
check "quality.timers: девять таймеров, свои пять стоят, чужие четыре помечены owned=false" '[ "$(lget ".quality.timers|length")" = 9 ] && [ "$(lget "[.quality.timers[] | select(.owned) | .installed] | all")" = true ] && [ "$(lget "[.quality.timers[] | select(.owned)] | length")" = 5 ] && [ "$(lget "[.quality.timers[] | select(.name == \"backup\" or .name == \"cert-renew\") | .owned] | any")" = false ]' "$(jq -c '[.quality.timers[] | {name, installed, owned}]' <<<"$last" 2>/dev/null)"

# ── 4a. Таймер при занятом замке (WRP-03, жалоба 29.09.2026) ─────────────────
# Во время установки goamnezia-backup и goamnezia-cert ждали общий замок 30 с, получали lock_timeout и оставляли
# юнит в failed. Теперь таймер ждёт GOAMNEZIA_TIMER_LOCK_WAIT (по умолчанию 120 с) и, не дождавшись, отвечает
# «занято — пропущено, следующий запуск по таймеру» с кодом 0. Настоящего flock на macOS нет — подставной
# (FAKE_FLOCK_BUSY=1: замок держит другой процесс) проверяет саму ветку отказа замка.
section "WRP-03 таймер при занятом замке: пропуск с кодом 0, а не failed"
FLOCKBIN="$TMP/flockbin"; mkdir -p "$FLOCKBIN"
printf '#!/bin/sh\n[ "${FAKE_FLOCK_BUSY:-0}" = 1 ] && exit 1\nexit 0\n' >"$FLOCKBIN/flock"; chmod +x "$FLOCKBIN/flock"
_path_saved="$PATH"; export PATH="$FLOCKBIN:$PATH"
export GOAMNEZIA_TIMER_LOCK_WAIT=1 FAKE_FLOCK_BUSY=1
_bl_before=$(cat "$(ga_backup_last_file)" 2>/dev/null || echo absent)
rm -f "$GOAMNEZIA_NOTIFY_DIR"/*.json 2>/dev/null
run backup_tick
check "ночная копия при занятом замке: rc=0, JSON skipped=busy, строка «занято — пропущено, следующий запуск по таймеру»" \
    '[ "$rc" = 0 ] && [ "$(jq -r .skipped <<<"$out")" = busy ] && [ "$(jq -r .ok <<<"$out")" = true ] && grep -q "goamnezia-backup: занято — пропущено, следующий запуск по таймеру" "$ERR" && ! grep -q "дольше 30 секунд" "$ERR"' \
    "rc=$rc out=$out $(errs | tail -3)"
check "ночная копия при занятом замке: прошлый итог не переписан отказом, события боту нет" \
    '[ "$(cat "$(ga_backup_last_file)" 2>/dev/null || echo absent)" = "$_bl_before" ] && ! ls "$GOAMNEZIA_NOTIFY_DIR"/*.json >/dev/null 2>&1' \
    "$(cat "$(ga_backup_last_file)" 2>/dev/null) $(ls "$GOAMNEZIA_NOTIFY_DIR" 2>/dev/null)"
export GOAMNEZIA_WHO=timer
act cert-tick
check "cert-tick из юнита (GOAMNEZIA_WHO=timer) при занятом замке: код 0, одна JSON-строка success, code=busy, skipped" \
    '[ "$rc" = 0 ] && [ "$nlines" = 1 ] && [ "$(lget .status)" = success ] && [ "$(lget .code)" = busy ] && [ "$(lget .skipped)" = true ] && [ "$(lget .tick)" = cert-tick ] && grep -q "cert-tick: занято — пропущено, следующий запуск по таймеру" "$ERR"' \
    "rc=$rc last=$last $(errs | tail -3)"
check "cert-tick: попытка миграции таймера при занятом замке не пишет в журнал ошибку" '! grep -q "дольше 30 секунд" "$ERR"' "$(errs | head -5)"
act watch-tick
check "watch-tick из юнита при занятом замке — тоже пропуск с кодом 0" '[ "$rc" = 0 ] && [ "$(lget .code)" = busy ]' "rc=$rc $last"
unset GOAMNEZIA_WHO
act cert-tick
check "не таймер (бот, панель, MCP) при занятом замке — прежний lock_timeout, код 75" \
    '[ "$rc" = 75 ] && [ "$(lget .status)" = error ] && [ "$(lget .code)" = lock_timeout ]' "rc=$rc $last"
export FAKE_FLOCK_BUSY=0 GOAMNEZIA_WHO=timer
act cert-tick
check "замок свободен — таймер работает как прежде (success без skipped)" '[ "$rc" = 0 ] && [ "$(lget .status)" = success ] && [ "$(lget .skipped)" != true ]' "rc=$rc $last $(errs | tail -2)"
# Бюджет ожидания замка у тика — один на миграцию и само действие (живая проверка 03.10.2026: тик при занятом замке
# висел ~150 с — 30 с миграция и ещё 120 действие). Этот подставной flock пишет, сколько его просили ждать (-w), и при
# занятом замке ждёт по-настоящему, но не дольше 2 с.
FLOCKBIN2="$TMP/flockbin2"; mkdir -p "$FLOCKBIN2"
cat >"$FLOCKBIN2/flock" <<'FL'
#!/bin/sh
[ "$1" = -w ] || exit 0
printf '%s\n' "$2" >>"$FAKE_FLOCK_LOG"
[ "${FAKE_FLOCK_BUSY:-0}" = 1 ] || exit 0
n="$2"; [ "$n" -gt 2 ] && n=2
sleep "$n"
exit 1
FL
chmod +x "$FLOCKBIN2/flock"
export PATH="$FLOCKBIN2:$_path_saved" FAKE_FLOCK_LOG="$TMP/flock.log" FAKE_FLOCK_BUSY=1 GOAMNEZIA_TIMER_LOCK_WAIT=2
: >"$FAKE_FLOCK_LOG"
act cert-tick
_waits=$(paste -sd+ - <"$FAKE_FLOCK_LOG"); _sum=$(( ${_waits:-0} ))
check "тик при занятом замке: миграция и действие ждут из одного бюджета (2 с), а не 30 + 2; пропуск с кодом 0" \
    '[ "$rc" = 0 ] && [ "$(lget .code)" = busy ] && [ "$(grep -c . "$FAKE_FLOCK_LOG")" -ge 2 ] && [ "$_sum" -le 2 ]' \
    "rc=$rc ждали: [$_waits] $last"
export FAKE_FLOCK_BUSY=0
: >"$FAKE_FLOCK_LOG"
act cert-tick
# Остаток считается от срока в целых секундах: под нагрузкой полного прогона сама миграция может перешагнуть границу
# секунды — тогда действию остаётся 1 из 2 (мигало 04.10 при 53 заданиях). Смысл проверки — «не ждали сверх бюджета и
# не урезали его ожиданием замка»: остаток не меньше бюджета без одной секунды.
check "замок свободен — миграция берёт его сразу, действию остаётся весь бюджет" \
    '[ "$rc" = 0 ] && [ "$(lget .status)" = success ] && [ "$(lget .skipped)" != true ] && [ "$(sed -n 2p "$FAKE_FLOCK_LOG")" -ge 1 ] && [ "$(sed -n 2p "$FAKE_FLOCK_LOG")" -le 2 ]' \
    "rc=$rc ждали: [$(paste -sd, - <"$FAKE_FLOCK_LOG")] $last"
unset GOAMNEZIA_WHO FAKE_FLOCK_BUSY GOAMNEZIA_TIMER_LOCK_WAIT FAKE_FLOCK_LOG
export PATH="$_path_saved"
run ga_timer_lock_wait
check "ожидание таймера по умолчанию — 120 с" '[ "$out" = 120 ]' "out=$out"
_w=$(GA_TIMER_LOCK_DEADLINE=$(( $(date +%s) + 30 )) ga_timer_lock_wait)
check "срок тика через 30 с — ждать не больше остатка (а не 120)" '[ "$_w" -ge 29 ] && [ "$_w" -le 30 ]' "w=$_w"
_w=$(GA_TIMER_LOCK_DEADLINE=$(( $(date +%s) - 5 )) ga_timer_lock_wait)
check "срок тика вышел — 0: одна попытка без ожидания" '[ "$_w" = 0 ]' "w=$_w"

# ── 5. Рассогласование версий Docker (регрессия стенда 10.09.2026) ───────────
# Пакет docker.io обновился автообновлением, демон не перезапускался: клиент
# просит API 1.52, демон понимает 1.43, каждая команда падает. Контейнеры при
# этом работают. Проверяем ровно то, что тогда сломалось: статус профиля,
# сторож, тик квот и диагностику. Подставной docker ведёт себя как на стенде.
section "рассогласование версий Docker: закрепление API и docker_unreachable"
FAKE_BIN="$TMP/bin"
mkdir -p "$FAKE_BIN"
cp "$ROOT/tests/fixtures/fake_docker.sh" "$FAKE_BIN/docker"
# Та же фикстура под именем dockerd — бинарь демона на диске (DK-02): версия пакета, как у клиента. Без неё на машине с
# настоящим Docker нашёлся бы настоящий dockerd, и вердикт «служба старая» зависел бы от машины прогона.
cp "$ROOT/tests/fixtures/fake_docker.sh" "$FAKE_BIN/dockerd"
chmod +x "$FAKE_BIN/docker" "$FAKE_BIN/dockerd"
# systemctl машины этому прогону не нужен: сторож при старой службе перезапускает Docker сам (DK-02). Подставной пишет
# вызов и отвечает успехом; отказ — FAKE_SYSTEMCTL_FAIL=1.
SCTL_DK="$TMP/systemctl-docker.calls"; : >"$SCTL_DK"
printf '#!/bin/sh\nprintf "%%s\\n" "$*" >>"%s"\n[ "${FAKE_SYSTEMCTL_FAIL:-0}" = 1 ] && { echo "Failed to restart docker.service: Unit docker.service failed." >&2; exit 1; }\nexit 0\n' \
    "$SCTL_DK" >"$FAKE_BIN/systemctl"
chmod +x "$FAKE_BIN/systemctl"
PATH="$FAKE_BIN:$PATH"; export PATH
# Сокет подсовываем несуществующий: на машине разработчика может быть запущен
# настоящий Docker, и тест обязан от него не зависеть.
export GOAMNEZIA_DOCKER_SOCK="$TMP/nosuch.sock"
# Ответ демона на GET /version — дословно со стенда (демон 24.0.7, API 1.43).
cat >"$TMP/docker_version.json" <<'EOJSON'
{"Platform":{"Name":""},"Version":"24.0.7","ApiVersion":"1.43","MinAPIVersion":"1.12","Os":"linux","Arch":"amd64"}
EOJSON
# Сохранённый `awg show awg0 dump` для тика квот через подставной docker.
dump 4000 5000 600 700
export FAKE_DOCKER_AWG_DUMP="$TMP/dump.txt"
# Кэш версии живёт на процесс — между сценариями сбрасываем, как при новом запуске.
docker_reset() {
    GA_DOCKER_API_PINNED=""; GA_DOCKER_API_DAEMON=""; GA_DOCKER_API_CLIENT=""
    GA_DOCKER_API_SOURCE=""; GA_DOCKER_VERSION_RAW=""; GA_DOCKER_VERSION_TRIED=""
    # «Старый ли демон» (DK-02) — тоже на процесс: бинарь, вердикт, перезапуск в сухом прогоне; память сторожа о нём.
    # Кэш ответа `dockerd --version` (LOCK_DIR/dockerd-version) — тоже: на сервере ответ меняется только с самим
    # файлом, а подставной dockerd отвечает по FAKE_DOCKER_MODE, не меняясь.
    GA_DOCKER_BIN_TRIED=""; GA_DOCKER_STALE=""; GA_DOCKER_DRY_RESTARTED=""
    unset DOCKER_API_VERSION
    rm -f "$GOAMNEZIA_NOTIFY_DIR"/*.json "$(ga_amn_watch_state_file)" "$(ga_amn_dsw_file)" "$GOAMNEZIA_LOCK_DIR/dockerd-version" 2>/dev/null
}
# Дальше — настоящие вызовы docker (подставного), поэтому dry-run выключаем.
GOAMNEZIA_DRY_RUN=0

# 5.1 Версия демона через сокет (ответ /version)
docker_reset
export GOAMNEZIA_DOCKER_VERSION_JSON="$TMP/docker_version.json"
export FAKE_DOCKER_MODE=mismatch
docker_api_pin
check "версия API демона взята из /version и закреплена: DOCKER_API_VERSION=1.43, клиент 1.52" \
    '[ "${DOCKER_API_VERSION:-}" = 1.43 ] && [ "$GA_DOCKER_API_DAEMON" = 1.43 ] && [ "$GA_DOCKER_API_CLIENT" = 1.52 ] && [ "$GA_DOCKER_API_SOURCE" = socket ]' \
    "pin=${DOCKER_API_VERSION:-} daemon=$GA_DOCKER_API_DAEMON client=$GA_DOCKER_API_CLIENT src=$GA_DOCKER_API_SOURCE"
docker_api_pin
check "повторный вызов ничего не переопределяет (кэш на процесс)" '[ "${DOCKER_API_VERSION:-}" = 1.43 ]'

# 5.1a За терминалом: подоболочка $(…) закрепляет версию сама и родителю не передаёт — строка о рассогласовании
# повторялась (обновление 1.0.2 → 1.0.6 у владельца, 29.09). Теперь — один раз за 5 минут; у журнала отметка своя.
rm -f "$GOAMNEZIA_LOCK_DIR"/docker-api-warned*
tty_run() {
    python3 - "$1" <<'PY'
import os, subprocess, sys
m, s = os.openpty()
p = subprocess.Popen(["bash", "-c", sys.argv[1]], stdin=subprocess.DEVNULL, stdout=s, stderr=s, close_fds=True)
os.close(s)
out = b""
while True:
    try:
        chunk = os.read(m, 4096)
    except OSError:
        break
    if not chunk:
        break
    out += chunk
p.wait()
sys.stdout.write(out.decode("utf-8", "replace"))
PY
}
tty_out=$(GOAMNEZIA_DRY_RUN=0 ROOT="$ROOT" tty_run 'source "$ROOT/lib/common.sh"; source "$ROOT/lib/i18n.sh"; load_language ru >/dev/null 2>&1; source "$ROOT/lib/docker.sh"; unset DOCKER_API_VERSION; v=$(docker_api_pin; echo x); docker_api_pin; echo "pin=$DOCKER_API_VERSION"')
check "за терминалом: подоболочка и родитель определяют версию, строка о рассогласовании — одна" \
    '[ "$(grep -c "служба осталась старой" <<<"$tty_out")" = 1 ] && grep -q "pin=1.43" <<<"$tty_out"' "$tty_out"
# DK-02: человеку — словами и без пугающих чисел: что будет и где кнопка; версии API и «systemctl restart docker» —
# только журналу.
# Терминал переносит строку по ширине окна — сверяем текст, склеив переносы.
tty_flat=$(tr '\n\r' '  ' <<<"$tty_out" | tr -s ' ')
check "DK-02 за терминалом — понятная строка: перезапущу сам, когда никто не подключён; сейчас — «Обслуживание → Перезапустить Docker», без версий API" \
    'grep -q "goAmnezia перезапустит её сама, когда к VPN никто не будет подключён" <<<"$tty_flat" && grep -q "Обслуживание → Перезапустить Docker" <<<"$tty_flat" && ! grep -q "просит API\|systemctl restart docker" <<<"$tty_flat"' "$tty_flat"
check "отметка терминала не глушит журнал: у таймера своя, и она ещё не выставлена" \
    '[ -f "$GOAMNEZIA_LOCK_DIR/docker-api-warned-tty" ] && _ga_docker_warn_due journal 21600 && ! _ga_docker_warn_due journal 21600' \
    "$(ls "$GOAMNEZIA_LOCK_DIR" 2>&1)"
rm -f "$GOAMNEZIA_LOCK_DIR/docker-api-warned"
jr_out=$(GOAMNEZIA_DRY_RUN=0 "$BASH5" -c 'source "$1/lib/common.sh"; source "$1/lib/i18n.sh"; load_language ru >/dev/null 2>&1; source "$1/lib/docker.sh"; unset DOCKER_API_VERSION; docker_api_pin' _ "$ROOT" 2>&1 </dev/null)
check "DK-02 в журнал (не терминал) — строка с версиями API: клиент 1.52, демон 1.43, закреплено 1.43" \
    'grep -q "клиент просит API 1.52, демон понимает максимум 1.43" <<<"$jr_out" && grep -q "Закреплено 1.43" <<<"$jr_out"' "$jr_out"

# 5.2 Запасной путь: сокета нет — версию берём из текста отказа
docker_reset
unset GOAMNEZIA_DOCKER_VERSION_JSON
docker_api_pin
check "без сокета версия демона вычитана из «Maximum supported API version is 1.43»" \
    '[ "${DOCKER_API_VERSION:-}" = 1.43 ] && [ "$GA_DOCKER_API_SOURCE" = error ]' \
    "pin=${DOCKER_API_VERSION:-} src=$GA_DOCKER_API_SOURCE"

# 5.3 Заданную снаружи версию не трогаем
docker_reset
DOCKER_API_VERSION=1.41 docker_api_pin
check "DOCKER_API_VERSION из окружения уважаем (source=env)" '[ "$GA_DOCKER_API_SOURCE" = env ]' "src=$GA_DOCKER_API_SOURCE"

# 5.4 Главное: при рассогласовании профиль НЕ становится missing
docker_reset
export GOAMNEZIA_DOCKER_VERSION_JSON="$TMP/docker_version.json"
# Анна на связи — рукопожатие минуту назад: старую службу (пакет 29.1.3, демон 24.0.7) сторож не перезапускает, а
# ждёт и говорит об этом одним событием (DK-02). Счётчики — те же, на них ниже считает тик квот.
dump_hs 4000 5000 600 700 "$(( $(date +%s) - 60 ))" 0
run docker_container_state amnezia-awg2
check "состояние контейнера при рассогласовании версий: running (а не missing)" '[ "$out" = running ]' "out=$out $(errs)"
run amnezia_profile_status awg2
check "amnezia_profile_status: state=running, контейнер жив" '[ "$(jget .state)" = running ]' "$out"
: >"$SCTL_DK"
run amnezia_watch_tick
check "сторож при рассогласовании: state=running, action=none; служба старая, но Анна на связи — ждём (docker_stale=waiting), Docker не тронут" \
    '[ "$(jget ".profiles[0].state")" = running ] && [ "$(jget ".profiles[0].action")" = none ] && [ "$(jget .docker_unreachable)" = false ] && [ "$(jget .docker_stale)" = waiting ] && ! grep -q "restart docker" "$SCTL_DK"' "$out $(errs | tail -3) $(cat "$SCTL_DK")"
check "сторож при рассогласовании: событие одно — docker_stale с версиями (пакет 29.1.3, служба 24.0.7, на связи 1), а не container_missing" \
    '[ "$(jget .events)" = 1 ] && [ "$(ls "$GOAMNEZIA_NOTIFY_DIR"/*.json | wc -l | tr -d " ")" = 1 ] && f=$(ls "$GOAMNEZIA_NOTIFY_DIR"/*.json) && [ "$(jq -r .event "$f")" = docker_stale ] && [ "$(jq -r .binary_version "$f")" = 29.1.3 ] && [ "$(jq -r .daemon_version "$f")" = 24.0.7 ] && [ "$(jq -r .online "$f")" = 1 ]' \
    "$out $(cat "$GOAMNEZIA_NOTIFY_DIR"/*.json 2>/dev/null)"
check "сторож не звал восстанавливать из бэкапа" '! grep -q "отсутствует" "$ERR"' "$(errs | tail -3)"
rm -f "$GOAMNEZIA_NOTIFY_DIR"/*.json
run amnezia_watch_tick
check "DK-02 следующий тик, Анна всё ещё на связи: снова ждём, события нет (одно на обнаружение), Docker не тронут" \
    '[ "$(jget .docker_stale)" = waiting ] && [ "$(jget .events)" = 0 ] && [ -z "$(ls "$GOAMNEZIA_NOTIFY_DIR"/*.json 2>/dev/null)" ] && ! grep -q "restart docker" "$SCTL_DK"' "$out $(cat "$SCTL_DK")"
run users_quota_tick
check "тик квот при рассогласовании считает трафик (anna 4000/5000)" \
    '[ "$rc" = 0 ] && [ "$(jq -r ".users[0].peers.awg2.rx" "$USERS_FILE")" -gt 0 ] && [ "$(jq -r ".users[0].peers.awg2.last_rx" "$USERS_FILE")" = 4000 ]' \
    "rc=$rc $(jq -c '.users[0].peers.awg2' "$USERS_FILE") $(errs | tail -2)"

# 5.5 Демон не отвечает вовсе: honest «состояние неизвестно»
docker_reset
unset GOAMNEZIA_DOCKER_VERSION_JSON
export FAKE_DOCKER_MODE=down
run docker_container_state amnezia-awg2
check "демон не отвечает: docker_unreachable (а не missing)" '[ "$out" = docker_unreachable ]' "out=$out"
run amnezia_profile_status awg2
check "amnezia_profile_status: state=docker_unreachable" '[ "$(jget .state)" = docker_unreachable ]' "$out"
run amnezia_watch_tick
check "сторож: docker_unreachable=true, action=none, ровно одно событие на весь тик" \
    '[ "$(jget .docker_unreachable)" = true ] && [ "$(jget ".profiles[0].state")" = docker_unreachable ] && [ "$(jget ".profiles[0].action")" = none ] && [ "$(jget .events)" = 1 ] && [ "$(ls "$GOAMNEZIA_NOTIFY_DIR"/*.json | wc -l | tr -d " ")" = 1 ]' "$out $(errs | tail -3)"
check "событие сторожа — docker_unreachable, а не container_missing" \
    '[ "$(jq -r .event "$(ls "$GOAMNEZIA_NOTIFY_DIR"/*.json | head -1)")" = docker_unreachable ] && ! grep -q "отсутствует" "$ERR"' \
    "$(cat "$GOAMNEZIA_NOTIFY_DIR"/*.json 2>/dev/null) $(errs | tail -3)"
check "аудит сторожа: watch docker_unreachable" 'audit_has docker_unreachable "" watch' "$(grep docker_unreachable "$GOAMNEZIA_AUDIT_LOG" | tail -2)"
run amnezia_watch_tick
check "повтор тика: событие не дублируется (дедупликация по типу), в очереди по-прежнему одно" \
    '[ "$(jget .events)" = 0 ] && [ "$(ls "$GOAMNEZIA_NOTIFY_DIR"/*.json | wc -l | tr -d " ")" = 1 ]' "$out"
run users_quota_tick
check "тик квот при недоступном демоне: ошибка docker_unreachable, а не success с нулями" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = docker_unreachable ]' "rc=$rc code=$GA_ERR_CODE $(errs | tail -2)"
run docker_diag_json
check "диагностика: reachable=false" '[ "$(jget .reachable)" = false ] && [ "$(jget .installed)" = true ]' "$out"

# 5.6 Диагностика называет рассогласование и подсказывает рестарт
docker_reset
export GOAMNEZIA_DOCKER_VERSION_JSON="$TMP/docker_version.json"
export FAKE_DOCKER_MODE=mismatch
run docker_diag_json
check "docker_diag_json: клиент 29.1.3/1.52, демон 24.0.7/1.43, mismatch=true, restart_required=true, закреплено 1.43" \
    '[ "$(jget .client_version)" = 29.1.3 ] && [ "$(jget .client_api)" = 1.52 ] && [ "$(jget .daemon_version)" = 24.0.7 ] && [ "$(jget .daemon_api)" = 1.43 ] && [ "$(jget .mismatch)" = true ] && [ "$(jget .restart_required)" = true ] && [ "$(jget .api_pinned)" = 1.43 ] && [ "$(jget .reachable)" = true ]' "$out"
# quality зовём как бот — отдельным процессом install.sh. Диагностика по
# контракту смотрит на docker по-настоящему даже в dry-run (--probe), поэтому
# рассогласование она видит и здесь.
GOAMNEZIA_DRY_RUN=1 act quality
check "quality через install.sh: docker=true (демон отвечает после закрепления), docker_info называет рассогласование" \
    '[ "$(lget .status)" = success ] && [ "$(lget .quality.docker)" = true ] && [ "$(lget .quality.docker_info.mismatch)" = true ] && [ "$(lget .quality.docker_info.client_api)" = 1.52 ] && [ "$(lget .quality.docker_info.daemon_api)" = 1.43 ] && [ "$(lget .quality.docker_info.api_pinned)" = 1.43 ] && [ "$(lget .quality.docker_info.restart_required)" = true ]' "$(lget .quality.docker_info) $(errs | tail -3)"
# DK-02: та же картина глазами «старая ли служба»: бинарь на диске (dockerd --version) — 29.1.3, демон — 24.0.7.
check "DK-02 quality: docker_info.stale=true, причина version, бинарь 29.1.3 со сборкой, с какого времени (stale_since)" \
    '[ "$(lget .quality.docker_info.stale)" = true ] && [ "$(lget .quality.docker_info.stale_reason)" = version ] && [ "$(lget .quality.docker_info.binary_version)" = 29.1.3 ] && [ "$(lget .quality.docker_info.binary_build)" = 29.1.3-0ubuntu3~24.04.2 ] && [[ "$(lget .quality.docker_info.stale_since)" =~ ^20[0-9]{2}-[0-9]{2}-[0-9]{2}T ]]' \
    "$(lget .quality.docker_info) $(errs | tail -3)"

# 5.7 Нормальный случай: версии совпадают — поведение прежнее
docker_reset
unset GOAMNEZIA_DOCKER_VERSION_JSON
export FAKE_DOCKER_MODE=ok
run docker_container_state amnezia-awg2
check "версии совпадают: контейнер running, закреплять нечего (клиент и так работает)" \
    '[ "$out" = running ] && [ -z "${DOCKER_API_VERSION:-}" ]' "out=$out pin=${DOCKER_API_VERSION:-}"
docker_reset
export GOAMNEZIA_DOCKER_VERSION_JSON="$TMP/docker_version.json"
docker_api_pin
run docker_container_state amnezia-awg2
check "версии совпадают и сокет отвечает: закрепили ту же 1.43, поведение прежнее" \
    '[ "${DOCKER_API_VERSION:-}" = 1.43 ] && [ "$out" = running ]' "out=$out pin=${DOCKER_API_VERSION:-}"
check "при совпадении версий предупреждения о рассогласовании нет" '! grep -q "docker_warn_api_mismatch\|просит API" "$ERR"' "$(errs | tail -2)"
run docker_diag_json
check "версии совпадают: mismatch=false, reachable=true" '[ "$(jget .mismatch)" = false ] && [ "$(jget .reachable)" = true ]' "$out"
check "DK-02 версии совпадают: stale=false, бинарь назван, а «с какого времени старая» — пусто" \
    '[ "$(jget .stale)" = false ] && [ "$(jget .binary_version)" = 24.0.7 ] && [ "$(jget .stale_since)" = "" ] && [ "$(jget .restart_required)" = false ]' "$out"
run amnezia_watch_tick
check "версии совпадают: сторож молчит (events=0, docker_unreachable=false)" \
    '[ "$(jget .events)" = 0 ] && [ "$(jget .docker_unreachable)" = false ]' "$out"

# 5.8 Предел очереди уведомлений: читателя может не быть вовсе
rm -f "$GOAMNEZIA_NOTIFY_DIR"/*.json
for i in $(seq 1 12); do ga_notify test_event "$(jq -cn --arg i "$i" '{n: $i}')" >/dev/null 2>&1; done
check "без предела очередь растёт как есть (12 файлов)" '[ "$(ls "$GOAMNEZIA_NOTIFY_DIR"/*.json | wc -l | tr -d " ")" = 12 ]' "$(ls "$GOAMNEZIA_NOTIFY_DIR" | wc -l)"
GOAMNEZIA_NOTIFY_MAX=5 ga_notify test_event '{"n":"13"}' >/dev/null 2>&1
check "предел GOAMNEZIA_NOTIFY_MAX=5: в очереди 5 файлов, самые старые выброшены, свежее осталось" \
    '[ "$(ls "$GOAMNEZIA_NOTIFY_DIR"/*.json | wc -l | tr -d " ")" = 5 ] && [ "$(jq -r .n "$(ls "$GOAMNEZIA_NOTIFY_DIR"/*.json | tail -1)")" = 13 ]' \
    "$(ls "$GOAMNEZIA_NOTIFY_DIR")"

# ── 5a. Бюджет логов (охота 11.09.2026) ──────────────────────────────────────
# 1a.12 — голый вызов docker без закрепления версии API. При рассогласовании
#         путь к журналу оказывался пустым, а комментарий тут же трактовал это
#         как «драйвера нет»: журналы подхваченных контейнеров выпадали из
#         бюджета и росли без предела — ровно то, ради чего модуль написан.
# 1a.13 — запасной путь подсчёта размера был недостижим (код конвейера — код
#         awk, а он нулевой и на пустом вводе), и наружу уходила пустая строка.
section "бюджет логов: версия API docker и подсчёт размера"
if [ -f "$ROOT/lib/logbudget.sh" ]; then
    # shellcheck disable=SC1091
    source "$ROOT/lib/logbudget.sh"

    # 1a.13. Размер каталога: три случая источника, наружу всегда число.
    # pipefail снимаем нарочно: сторож стартует из юнита как `/bin/bash -c …`,
    # без набора флагов install.sh. Именно там код конвейера — это код awk, а
    # он нулевой и на пустом вводе, и запасной путь не выполнялся никогда.
    set +o pipefail
    mkdir -p "$TMP/logb/dir"; printf 'x%.0s' $(seq 1 100) >"$TMP/logb/dir/a.log"
    printf '#!/bin/bash\nprintf "%%s\\t%%s\\n" 4096 "$1"\n' >"$FAKE_BIN/du"
    printf '#!/bin/bash\nprintf "10\\n20\\n30\\n"\n' >"$FAKE_BIN/find"
    chmod +x "$FAKE_BIN/du" "$FAKE_BIN/find"
    run _logb_bytes "$TMP/logb/dir"
    check "du отвечает — берём его число (4096)" '[ "$out" = 4096 ]' "out=[$out]"
    printf '#!/bin/bash\nexit 1\n' >"$FAKE_BIN/du"; chmod +x "$FAKE_BIN/du"
    run _logb_bytes "$TMP/logb/dir"
    check "du не умеет -b (busybox, BSD) — запасной путь считает сам: 10+20+30=60" \
        '[ "$out" = 60 ]' "out=[$out]"
    printf '#!/bin/bash\nexit 1\n' >"$FAKE_BIN/find"; chmod +x "$FAKE_BIN/find"
    run _logb_bytes "$TMP/logb/dir"
    check "не ответил никто — ноль, а не пустая строка (иначе сравнение с бюджетом падает)" \
        '[ "$out" = 0 ]' "out=[$out]"
    run _logb_bytes "$TMP/logb/nosuch"
    check "каталога нет — ноль" '[ "$out" = 0 ]' "out=[$out]"
    rm -f "$FAKE_BIN/du" "$FAKE_BIN/find"
    run _logb_bytes "$TMP/logb/dir" 'a.log*'
    check "с маской тоже число" '[[ "$out" =~ ^[0-9]+$ ]]' "out=[$out]"
    run logb_status
    check "logb_status: в каждой строке ровно четыре поля, «занято» — число" \
        '[ "$(awk "NF != 4" <<<"$out" | wc -l | tr -d " ")" = 0 ] && [ "$(awk "\$1 == \"journal\" {print \$2}" <<<"$out")" -ge 0 ]' "$out"
    set -o pipefail

    # 1a.12. Версия API демона. Подставной docker в режиме mismatch отвечает
    # только закреплённому клиенту — ровно как живой демон на стенде.
    mkdir -p "$TMP/logb/docker"
    printf 'json log\n' >"$TMP/logb/docker/c1-json.log"
    printf 'rotated\n' >"$TMP/logb/docker/c1-json.log.1"
    export FAKE_DOCKER_LOGPATH="$TMP/logb/docker/c1-json.log"
    docker_reset
    export GOAMNEZIA_DOCKER_VERSION_JSON="$TMP/docker_version.json"
    export FAKE_DOCKER_MODE=mismatch
    run _logb_docker_files
    check "рассогласование версий: журналы контейнера всё равно найдены (живой + ротированный)" \
        '[ "$(wc -l <<<"$out" | tr -d " ")" = 2 ] && grep -q "c1-json.log.1" <<<"$out"' "out=[$out] $(errs | tail -2)"
    check "версия API закреплена самим модулем бюджета" '[ "${DOCKER_API_VERSION:-}" = 1.43 ]' "pin=${DOCKER_API_VERSION:-}"
    run _logb_docker_bytes
    check "байты журналов посчитаны (не ноль)" '[ "$out" -gt 0 ]' "out=[$out]"

    # Демон не отвечает вовсе: это не «драйвера нет», и молчать здесь нельзя.
    docker_reset
    unset GOAMNEZIA_DOCKER_VERSION_JSON
    export FAKE_DOCKER_MODE=down
    run _logb_docker_files
    check "демон лежит: файлов нет и об этом сказано вслух, а не молча" \
        '[ -z "$out" ] && grep -qF "$(tf logb_docker_unreachable amnezia-awg2)" "$ERR"' "out=[$out] $(errs | tail -2)"
    # L3-14 / TAIL-11: демон жив, а контейнера профиля просто нет — это не «Docker не ответил»
    docker_reset
    export FAKE_DOCKER_MODE=ok FAKE_DOCKER_MISSING="amnezia-awg2"
    run _logb_docker_files
    check "TAIL-11 контейнера нет, демон жив: файлов нет, и «Docker не ответил» не сказано" \
        '[ -z "$out" ] && ! grep -qF "$(tf logb_docker_unreachable amnezia-awg2)" "$ERR"' "out=[$out] $(errs | tail -2)"
    unset FAKE_DOCKER_MISSING

    # Юнит сторожа обязан подключать docker.sh — иначе закреплять версию нечем.
    check "юнит бюджета грузит lib/docker.sh (без него docker_api_pin недоступен)" \
        'grep -q "lib/docker.sh && source .*lib/logbudget.sh" "$ROOT/lib/logbudget.sh"'
    # ── Ротация: чужой конфиг уходит ИЗ каталога logrotate (стенд, 11.09.2026) ─
    # logrotate читает /etc/logrotate.d ЦЕЛИКОМ, и «выключенный» переименованием
    # nginx.goamnezia-disabled продолжал разбираться: duplicate log entry, весь
    # прогон обрывался с ошибкой, logrotate.service висел в failed — то есть
    # продукт ломал ротацию журналов ВСЕМУ серверу, а не только себе.
    # Проверяем по факту файлов в каталоге, а не по тексту кода.
    LR="$TMP/logrotate.d"; NGX="$TMP/varlog/nginx"; STASH="$STATE_DIR/logrotate-foreign"
    LOGB_LOGROTATE_DIR="$LR"; LOGB_NGINX_LOG_DIR="$NGX"; LOGB_UNIT_DIR="$TMP/logb-units"
    mkdir -p "$LR" "$NGX" "$LOGB_UNIT_DIR"
    SCTL_LOG="$TMP/systemctl.calls"; : >"$SCTL_LOG"
    printf '#!/bin/bash\necho "$*" >>"%s"\nexit 0\n' "$SCTL_LOG" >"$FAKE_BIN/systemctl"; chmod +x "$FAKE_BIN/systemctl"
    LR_UNIT="$LOGB_UNIT_DIR/goamnezia-logrotate.service"
    # unit_confs_exist — каждый конфиг из ExecStart юнита лежит на диске: logrotate
    # с именем отсутствующего файла выходит с ошибкой, и юнит висит в failed.
    unit_confs_exist() {
        local c
        for c in $(sed -n 's|^ExecStart=/usr/sbin/logrotate ||p' "$LR_UNIT"); do [ -f "$c" ] || return 1; done
        grep -q '^ExecStart=/usr/sbin/logrotate ' "$LR_UNIT"
    }

    # ── Сервер без nginx («Только VPN»): поймано на живом стенде 24.09.2026 ──
    # goamnezia-nginx пишется только при каталоге логов nginx, а юнит называл его
    # всегда — logrotate выходил с ошибкой каждый час, юнит висел в failed.
    LOGB_NGINX_LOG_DIR="$TMP/varlog/no-nginx"
    run logb_apply_logrotate
    check "без nginx: в юните только лежащие на диске конфиги (иначе logrotate падает каждый час)" \
        '[ "$rc" = 0 ] && [ ! -e "$LR/goamnezia-nginx" ] && unit_confs_exist && ! grep -q goamnezia-nginx "$LR_UNIT"' \
        "rc=$rc $(grep ExecStart "$LR_UNIT" 2>&1)"
    check "переписанный юнит снимает прежний провал (reset-failed), а не ждёт следующего часа" \
        'grep -q "reset-failed goamnezia-logrotate.service" "$SCTL_LOG"' "$(cat "$SCTL_LOG")"
    : >"$SCTL_LOG"
    run logb_apply_logrotate
    check "повторный прогон без изменений юнит не трогает и провал не сбрасывает" \
        '! grep -q "reset-failed" "$SCTL_LOG"' "$(cat "$SCTL_LOG")"
    # SR-37: logrotate не стоит (нет /etc/logrotate.d) — наш конфиг не ложится, и юнит с ним падал бы каждый час.
    LOGB_LOGROTATE_DIR="$TMP/no-logrotate.d"
    : >"$SCTL_LOG"
    run logb_apply_logrotate
    check "SR-37 без каталога logrotate: юнит и таймер ротации сняты, а не оставлены падать каждый час" \
        '[ "$rc" = 0 ] && [ ! -e "$LR_UNIT" ] && [ ! -e "$LOGB_UNIT_DIR/goamnezia-logrotate.timer" ] && grep -q "disable --now goamnezia-logrotate.timer" "$SCTL_LOG"' \
        "rc=$rc $(ls "$LOGB_UNIT_DIR") $(cat "$SCTL_LOG")"
    LOGB_LOGROTATE_DIR="$LR"
    run logb_apply_logrotate
    check "SR-37 каталог logrotate есть — юнит снова на месте" '[ "$rc" = 0 ] && unit_confs_exist' "rc=$rc"
    LOGB_NGINX_LOG_DIR="$NGX"

    # Чужие файлы каталога: дефолт дистрибутива для nginx и посторонний конфиг.
    printf '%s/*.log {\n    daily\n    rotate 14\n}\n' "$NGX" >"$LR/nginx"
    printf '/var/log/syslog {\n    weekly\n}\n' >"$LR/rsyslog"
    NGINX_ORIG=$(cat "$LR/nginx")

    run logb_apply_logrotate
    check "применение настроек: наш конфиг nginx на месте, чужой дефолт уведён из каталога" \
        '[ "$rc" = 0 ] && [ -f "$LR/goamnezia-nginx" ] && [ ! -e "$LR/nginx" ]' "rc=$rc $(ls "$LR" 2>&1) $(errs | tail -3)"
    check "в каталоге logrotate НЕ остаётся файла с нашим хвостом (иначе duplicate log entry)" \
        '[ "$(find "$LR" -name "*.goamnezia-disabled" 2>/dev/null | wc -l | tr -d " ")" = 0 ]' "$(ls "$LR" 2>&1)"
    check "один и тот же лог описан ровно ОДНИМ файлом каталога — нашим" \
        '[ "$(grep -lF "$NGX/*.log" "$LR"/* 2>/dev/null | wc -l | tr -d " ")" = 1 ] && grep -qF "$NGX/*.log" "$LR/goamnezia-nginx"' \
        "$(grep -lF "$NGX/*.log" "$LR"/* 2>&1)"
    check "штатный конфиг цел и лежит в состоянии продукта, откуда его вернут" \
        '[ -f "$STASH/nginx" ] && [ "$(cat "$STASH/nginx")" = "$NGINX_ORIG" ]' "$(ls "$STASH" 2>&1)"
    check "посторонний конфиг каталога не тронут" \
        '[ -f "$LR/rsyslog" ] && grep -q weekly "$LR/rsyslog"' "$(ls "$LR" 2>&1)"
    check "nginx появился: его конфиг дописан в юнит, и все конфиги юнита на месте" \
        'grep -q "goamnezia-nginx" "$LR_UNIT" && unit_confs_exist' "$(grep ExecStart "$LR_UNIT" 2>&1)"

    # Хвост от прошлых версий: на уже установленном сервере файл лежит как
    # nginx.goamnezia-disabled, а наш конфиг СОВПАДАЕТ — то есть «изменений нет».
    # Починка обязана доехать и туда, иначе она бесполезна ровно там, где сломано.
    mv -f "$STASH/nginx" "$LR/nginx.goamnezia-disabled"
    run logb_apply_logrotate
    check "хвост .goamnezia-disabled от прошлых версий подобран, хотя конфиг не менялся" \
        '[ ! -e "$LR/nginx.goamnezia-disabled" ] && [ -f "$STASH/nginx" ] && [ "$(cat "$STASH/nginx")" = "$NGINX_ORIG" ]' \
        "$(ls "$LR" "$STASH" 2>&1)"

    # Снос установки: чужой файл возвращается на место, наши правила уходят.
    run logb_remove_logrotate
    check "снятие: штатный конфиг возвращён в каталог целым, наши правила удалены" \
        '[ "$rc" = 0 ] && [ -f "$LR/nginx" ] && [ "$(cat "$LR/nginx")" = "$NGINX_ORIG" ] && [ ! -e "$LR/goamnezia-nginx" ] && [ ! -e "$LR/goamnezia" ] && [ ! -e "$STASH/nginx" ]' \
        "rc=$rc $(ls "$LR" "$STASH" 2>&1)"
    run logb_remove_logrotate
    check "повторное снятие ничего не ломает (идемпотентно)" \
        '[ "$rc" = 0 ] && [ "$(cat "$LR/nginx")" = "$NGINX_ORIG" ]' "rc=$rc $(ls "$LR" 2>&1)"
    rm -f "$FAKE_BIN/systemctl"

    # ── SEC-C2: поток записей в живой журнал действий не стирает его архивы ──────
    # Раньше в счёт бюджета goamnezia* шёл и живой goamnezia-audit.log, и поток записей (сотни мегабайт за
    # час) сносил сжатые архивы того же журнала — журнал безопасности вычищался потоком.
    SECV="$TMP/logb/secvar"; mkdir -p "$SECV"
    printf 'a%.0s' $(seq 1 3000) >"$SECV/goamnezia-audit.log"
    printf 'b%.0s' $(seq 1 100) >"$SECV/goamnezia-audit.log.2.gz"; touch -t 202601010000 "$SECV/goamnezia-audit.log.2.gz"
    printf 'c%.0s' $(seq 1 100) >"$SECV/goamnezia.log.3.gz"; touch -t 202601020000 "$SECV/goamnezia.log.3.gz"
    run _logb_trim_dir "$SECV" 'goamnezia*' 1000
    check "SEC-C2 живой журнал больше бюджета, архивов мало: архивы журнала действий НЕ удалены" \
        '[ -f "$SECV/goamnezia-audit.log.2.gz" ] && [ -f "$SECV/goamnezia.log.3.gz" ] && [ -f "$SECV/goamnezia-audit.log" ]' "$(ls -la "$SECV")"
    printf 'd%.0s' $(seq 1 400) >"$SECV/goamnezia.log.4.gz"; touch -t 202512010000 "$SECV/goamnezia.log.4.gz"
    printf 'e%.0s' $(seq 1 100) >"$SECV/goamnezia-audit.log.5.gz"; touch -t 202511010000 "$SECV/goamnezia-audit.log.5.gz"
    run _logb_trim_dir "$SECV" 'goamnezia*' 1000
    check "SEC-C2 архивов сверх их доли: первыми уходят архивы рабочего журнала, архивы журнала действий остаются" \
        '[ ! -e "$SECV/goamnezia.log.4.gz" ] && [ -f "$SECV/goamnezia-audit.log.5.gz" ] && [ -f "$SECV/goamnezia-audit.log.2.gz" ]' "$(ls -la "$SECV")"
    run _logb_bytes "$SECV" 'goamnezia*'
    check "SEC-C2 размер своих файлов в общем каталоге считается и без find -printf (BSD, busybox)" '[ "$out" -ge 3000 ]' "out=[$out]"
    rm -rf "$SECV"
    # Пути модуля нарочно остаются в песочнице и после блока: случайный поздний
    # вызов не должен уметь дотянуться до настоящего /etc машины разработчика.

    unset FAKE_DOCKER_LOGPATH
    docker_reset
    export FAKE_DOCKER_MODE=ok
else
    skip=$((skip + 1)); printf '  – бюджет логов (пропущено: нет lib/logbudget.sh)\n'
fi

# Возвращаем прогон в dry-run и убираем подставной docker.
GOAMNEZIA_DRY_RUN=1
unset FAKE_DOCKER_MODE FAKE_DOCKER_AWG_DUMP GOAMNEZIA_DOCKER_VERSION_JSON GOAMNEZIA_DOCKER_SOCK DOCKER_API_VERSION
rm -f "$FAKE_BIN/docker" "$FAKE_BIN/dockerd" "$GOAMNEZIA_NOTIFY_DIR"/*.json
docker_reset

# ── 5b. Старый демон: сторож перезапускает Docker сам (DK-02, решение владельца 04.10.2026) ──
# Ubuntu обновила пакет docker.io, а службу не перезапустила: на сервере владельца с 01.10 по 04.10 работал демон 24.0.7
# при пакете 29.1.3. Продукт это переживал (закрепление API), но обновление безопасности не действовало, а владелец
# видел предупреждение на каждом шагу. Теперь сторож перезапускает службу сам, когда к VPN никто не подключён, а
# человек — кнопкой. Сухой прогон: демон — фикстура ответа /version (до и после перезапуска), бинарь на диске —
# GOAMNEZIA_DOCKERD_VERSION, рукопожатия — GOAMNEZIA_AWG_SHOW_FIXTURE; сам демон не трогается («DRY: systemctl restart
# docker»).
section "DK-02 старый демон: распознавание, сторож, перезапуск и мост docker-restart"
printf '%s\n' '{"Version":"24.0.7","ApiVersion":"1.43","MinAPIVersion":"1.12","GitCommit":"24.0.7-0ubuntu4","Os":"linux"}' >"$TMP/dk_old.json"
printf '%s\n' '{"Version":"29.1.3","ApiVersion":"1.52","MinAPIVersion":"1.44","GitCommit":"29.1.3-0ubuntu3~24.04.2","Os":"linux"}' >"$TMP/dk_new.json"
DK_PKG="Docker version 29.1.3, build 29.1.3-0ubuntu3~24.04.2"
export GOAMNEZIA_DOCKER_VERSION_JSON="$TMP/dk_old.json" GOAMNEZIA_DOCKER_VERSION_JSON_AFTER="$TMP/dk_new.json"
export GOAMNEZIA_DOCKERD_VERSION="$DK_PKG" GOAMNEZIA_DOCKERD_SINCE=1791037502
export GOAMNEZIA_AWG_SHOW_FIXTURE="$TMP/dump.txt" GA_AMN_WATCH_RECHECK_SECONDS=0
# dk_hs <на связи Анна: секунд назад|0> — рукопожатия профиля: Анна — столько секунд назад (0 — не было), Боб — никогда.
dk_hs() { local a=0; [ "${1:-0}" != 0 ] && a=$(( $(date +%s) - $1 )); dump_hs 100 100 100 100 "$a" 0; }
dk_ev() { ls "$GOAMNEZIA_NOTIFY_DIR"/*.json 2>/dev/null | while read -r f; do jq -r .event "$f"; done | paste -sd, -; }
dk_last() { jq -c . "$(ls "$GOAMNEZIA_NOTIFY_DIR"/*.json 2>/dev/null | tail -1)" 2>/dev/null; }
dk_clean() { docker_reset; rm -f "$GOAMNEZIA_NOTIFY_DIR"/*.json; }

# Распознавание: версии бинаря и демона, сборки, запасной путь по API
dk_clean
run docker_stale_probe
check "DK-02 пакет 29.1.3 на диске, работает демон 24.0.7: служба старая (reason=version), версии и сборки названы" \
    '[ "$rc" = 0 ] && [ "${GA_DOCKER_STALE_REASON:-}" = version ] && [ "${GA_DOCKER_BIN_VER:-}" = 29.1.3 ] && [ "${GA_DOCKER_BIN_BUILD:-}" = 29.1.3-0ubuntu3~24.04.2 ] && [ "${GA_DOCKER_DAEMON_VER:-}" = 24.0.7 ] && [ "${GA_DOCKER_DAEMON_BUILD:-}" = 24.0.7-0ubuntu4 ]' \
    "rc=$rc reason=${GA_DOCKER_STALE_REASON:-} bin=${GA_DOCKER_BIN_VER:-}/${GA_DOCKER_BIN_BUILD:-} daemon=${GA_DOCKER_DAEMON_VER:-}/${GA_DOCKER_DAEMON_BUILD:-}"
run docker_stale_json
check "DK-02 docker_stale_json: reason, binary_version, daemon_version, since — время замены бинаря (ISO)" \
    '[ "$(jget .reason)" = version ] && [ "$(jget .binary_version)" = 29.1.3 ] && [ "$(jget .daemon_version)" = 24.0.7 ] && [ "$(jget .since)" = 2026-10-03T14:25:02Z ]' "$out"
dk_clean
GOAMNEZIA_DOCKERD_VERSION="Docker version 24.0.7, build 24.0.7-0ubuntu4" run docker_stale_probe
check "DK-02 бинарь и демон одной версии и сборки — служба свежая, ничего не делать" '[ "$rc" = 1 ] && [ -z "${GA_DOCKER_STALE_REASON:-}" ]' "rc=$rc reason=${GA_DOCKER_STALE_REASON:-}"
dk_clean
GOAMNEZIA_DOCKERD_VERSION="Docker version 24.0.7, build 24.0.7-0ubuntu4.1" run docker_stale_probe
check "DK-02 та же версия, другая сборка (исправление безопасности без смены версии) — служба старая, reason=build" \
    '[ "$rc" = 0 ] && [ "${GA_DOCKER_STALE_REASON:-}" = build ]' "rc=$rc reason=${GA_DOCKER_STALE_REASON:-}"
dk_clean
GOAMNEZIA_DOCKERD_VERSION="Docker version 24.0.7, build 24.0.7-0ubuntu4.1" run docker_stale_json
check "DK-02 одна версия, разные сборки — человеку сборки (binary_label/daemon_label), а не «24.0.7 → 24.0.7»" \
    '[ "$(jget .binary_label)" = 24.0.7-0ubuntu4.1 ] && [ "$(jget .daemon_label)" = 24.0.7-0ubuntu4 ]' "$out"
dk_clean
run docker_stale_json
check "DK-02 разные версии — человеку версии (binary_label 29.1.3, daemon_label 24.0.7)" \
    '[ "$(jget .binary_label)" = 29.1.3 ] && [ "$(jget .daemon_label)" = 24.0.7 ]' "$out"
dk_clean
GOAMNEZIA_DOCKERD_VERSION="Docker version 24.0.7, build 41ca978" GOAMNEZIA_DOCKER_VERSION_JSON="$TMP/docker_version.json" run docker_stale_probe
check "DK-02 у демона сборки нет (не узнать) — сравниваем только версии: одинаковые — служба свежая" '[ "$rc" = 1 ]' "rc=$rc reason=${GA_DOCKER_STALE_REASON:-}"
dk_clean
GOAMNEZIA_DOCKERD_VERSION="не версия" run docker_stale_probe
check "DK-02 бинарь не узнать, рассогласования API не видно (сухой прогон) — не «старая»: без доказательств не перезапускаем" \
    '[ "$rc" = 1 ]' "rc=$rc reason=${GA_DOCKER_STALE_REASON:-}"
dk_clean
GA_DOCKER_API_PINNED=1 GA_DOCKER_API_CLIENT=1.52 GA_DOCKER_API_DAEMON=1.43 GOAMNEZIA_DOCKERD_VERSION="не версия" run docker_stale_probe
check "DK-02 бинарь не узнать, но клиент просит API 1.52 при демоне 1.43 — служба старая по рассогласованию API (reason=api)" \
    '[ "$rc" = 0 ] && [ "${GA_DOCKER_STALE_REASON:-}" = api ]' "rc=$rc reason=${GA_DOCKER_STALE_REASON:-}"
dk_clean
GA_DOCKER_API_PINNED=1 GA_DOCKER_API_CLIENT=1.52 GA_DOCKER_API_DAEMON=1.43 GOAMNEZIA_DOCKERD_VERSION="Docker version 24.0.7, build 24.0.7-0ubuntu4" run docker_stale_probe
check "DK-02 рассогласование API, а бинарь и демон одной версии (клиент из другого пакета) — перезапуск не вылечит, не «старая»" \
    '[ "$rc" = 1 ]' "rc=$rc reason=${GA_DOCKER_STALE_REASON:-}"

# Профилей нет (установка ещё идёт, все сняты) — Docker на машине служит не нам
dk_clean; dk_hs 0
printf '{"schema": 1, "installed": true, "mode": "lite", "profiles": {}}\n' >"$TMP/dk_noprof.json"
_cfg_dk="$CONFIG_FILE"; CONFIG_FILE="$TMP/dk_noprof.json"
run ga_amn_docker_stale_watch
CONFIG_FILE="$_cfg_dk"
check "DK-02 профилей нет — сторож Docker не трогает (none), событий нет; кнопка остаётся" \
    '[ "$rc" = 0 ] && [ "$out" = none ] && ! grep -q "systemctl restart docker" "$ERR" && [ -z "$(dk_ev)" ]' "rc=$rc out=$out $(dk_ev)"

# Кто на связи: рукопожатие моложе 5 минут — у всех профилей, подхваченных (1.x: wg и wg0) тоже
dk_hs 290; run ga_amn_vpn_online
check "DK-02 рукопожатие 4 мин 50 с назад — человек на связи (1)" '[ "$rc" = 0 ] && [ "$out" = 1 ]' "rc=$rc out=$out"
dk_hs 310; run ga_amn_vpn_online
check "DK-02 рукопожатие 5 мин 10 с назад — на связи никого (0)" '[ "$rc" = 0 ] && [ "$out" = 0 ]' "rc=$rc out=$out"
ga_config_merge '{"profiles":{"awg2":{"iface":"wg0","imported":true}}}' >/dev/null 2>&1
GOAMNEZIA_AWG_SHOW_FIXTURE="" run ga_amn_vpn_online
check "DK-02 подхваченный контейнер AmneziaWG 1.x: рукопожатия спрашиваются у wg show wg0 — его люди тоже считаются" \
    '[ "$rc" = 0 ] && grep -q "DRY: docker exec -i amnezia-awg2 wg show wg0 dump" "$ERR"' "rc=$rc out=$out $(errs | tail -3)"
jq '.profiles.awg2 |= del(.iface, .imported)' "$CONFIG_FILE" >"$CONFIG_FILE.tmp" && mv "$CONFIG_FILE.tmp" "$CONFIG_FILE" && chmod 600 "$CONFIG_FILE"

# Сторож: кто-то подключён — ждать и сказать один раз
dk_clean; dk_hs 60
run amnezia_watch_tick
check "DK-02 сторож: служба старая, Анна на связи — ждёт (docker_stale=waiting), Docker не тронут" \
    '[ "$rc" = 0 ] && [ "$(jget .docker_stale)" = waiting ] && ! grep -q "systemctl restart docker" "$ERR"' "$out $(errs | tail -3)"
check "DK-02 боту — одно событие docker_stale с версиями и числом подключённых" \
    '[ "$(dk_ev)" = docker_stale ] && [ "$(jget .events)" = 1 ] && [ "$(dk_last | jq -r .binary_version)" = 29.1.3 ] && [ "$(dk_last | jq -r .daemon_version)" = 24.0.7 ] && [ "$(dk_last | jq -r .online)" = 1 ] && [ "$(dk_last | jq -r .since)" = 2026-10-03T14:25:02Z ]' \
    "$(dk_ev) $(dk_last)"
check "DK-02 журнал действий: watch docker_stale" 'audit_has docker_stale "daemon=24.0.7 binary=29.1.3" watch' "$(grep docker_stale "$GOAMNEZIA_AUDIT_LOG" | tail -2)"
rm -f "$GOAMNEZIA_NOTIFY_DIR"/*.json
run amnezia_watch_tick
check "DK-02 следующий тик, пользователи всё ещё на связи: ждём молча (событие — одно на обнаружение), Docker не тронут" \
    '[ "$(jget .docker_stale)" = waiting ] && [ "$(jget .events)" = 0 ] && [ -z "$(dk_ev)" ] && ! grep -q "systemctl restart docker" "$ERR"' "$out $(dk_ev)"
GA_DOCKER_STALE=""; GA_DOCKER_BIN_TRIED=""
GOAMNEZIA_DOCKERD_VERSION="Docker version 29.1.4, build 29.1.4-0ubuntu1" run amnezia_watch_tick
check "DK-02 пакет обновили ещё раз (новое обнаружение), Анна на связи: снова одно сообщение — уже о 29.1.4" \
    '[ "$(jget .docker_stale)" = waiting ] && [ "$(dk_ev)" = docker_stale ] && [ "$(dk_last | jq -r .binary_version)" = 29.1.4 ] && ! grep -q "systemctl restart docker" "$ERR"' \
    "$out $(dk_ev) $(dk_last)"
dk_clean; dk_hs 600

# Никого: перезапуск, подъём профилей, итог
run amnezia_watch_tick
check "DK-02 никого нет (рукопожатие 10 минут назад): сторож перезапустил Docker сам — docker_stale=restarted, «DRY: systemctl restart docker»" \
    '[ "$rc" = 0 ] && [ "$(jget .docker_stale)" = restarted ] && grep -q "DRY: systemctl restart docker" "$ERR"' "$out $(errs | tail -5)"
check "DK-02 после перезапуска — профиль тем же путём, что после перезапуска профиля: интерфейс, изоляция клиентов" \
    'sed -n "/DRY: systemctl restart docker/,\$p" "$ERR" | grep -q "DRY: docker exec -i amnezia-awg2 awg show awg0" && sed -n "/DRY: systemctl restart docker/,\$p" "$ERR" | grep -q "iptables -C FORWARD -i awg0 -o awg0 -j DROP"' \
    "$(sed -n "/systemctl restart docker/,\$p" "$ERR" | head -12)"
check "DK-02 боту — два события: docker_restarting (перед перезапуском), затем docker_restarted (ok, длительность, служба теперь 29.1.3, профиль awg2)" \
    '[ "$(dk_ev)" = docker_restarting,docker_restarted ] && [ "$(jget .events)" = 2 ] && [ "$(dk_last | jq -r .ok)" = true ] && [ "$(dk_last | jq -r .daemon_after)" = 29.1.3 ] && [ "$(dk_last | jq -r .daemon_before)" = 24.0.7 ] && [ "$(dk_last | jq -r .daemon_after_label)" = 29.1.3 ] && [ "$(dk_last | jq -r ".restarted|join(\",\")")" = awg2 ] && [ "$(dk_last | jq -r ".duration_s|type")" = number ]' \
    "$(dk_ev) $(dk_last)"
check "DK-02 журнал действий: watch docker-restart by=watch ok=true с длительностью и версиями" \
    'audit_has docker-restart "by=watch ok=true duration=" watch && grep -q "daemon=24.0.7->29.1.3" "$GOAMNEZIA_AUDIT_LOG"' "$(grep docker-restart "$GOAMNEZIA_AUDIT_LOG" | tail -2)"
check "DK-02 попытка в памяти сторожа (правило 6 ч): attempt_at, attempt_by=watch, attempt_ok=true" \
    '[ "$(jq -r .attempt_by "$(ga_amn_dsw_file)")" = watch ] && [ "$(jq -r .attempt_ok "$(ga_amn_dsw_file)")" = true ] && [[ "$(jq -r .attempt_at "$(ga_amn_dsw_file)")" =~ ^[0-9]+$ ]]' \
    "$(cat "$(ga_amn_dsw_file)" 2>&1)"
rm -f "$GOAMNEZIA_NOTIFY_DIR"/*.json
run amnezia_watch_tick
check "DK-02 следующий тик: демон уже тот, что на диске — служба свежая (docker_stale=none), событий нет" \
    '[ "$(jget .docker_stale)" = none ] && [ "$(jget .events)" = 0 ] && [ -z "$(dk_ev)" ]' "$out $(dk_ev)"

# Шесть часов между попытками
GA_DOCKER_DRY_RESTARTED=""; GA_DOCKER_STALE=""; GA_DOCKER_VERSION_TRIED=""; GA_DOCKER_VERSION_RAW=""
rm -f "$GOAMNEZIA_NOTIFY_DIR"/*.json
run amnezia_watch_tick
check "DK-02 служба снова старая через минуту после попытки: 6 ч не прошли — не перезапускает (backoff), Docker не тронут, событий нет" \
    '[ "$(jget .docker_stale)" = backoff ] && ! grep -q "systemctl restart docker" "$ERR" && [ "$(jget .events)" = 0 ]' "$out $(dk_ev)"
_ga_amn_dsw_put "{\"attempt_at\": $(( $(date +%s) - 21600 + 120 ))}"
GA_DOCKER_STALE=""; run amnezia_watch_tick
check "DK-02 5 ч 58 мин после попытки — всё ещё ждёт" '[ "$(jget .docker_stale)" = backoff ] && ! grep -q "systemctl restart docker" "$ERR"' "$out"
rm -f "$GOAMNEZIA_NOTIFY_DIR"/*.json
GA_DOCKER_STALE=""; GA_DOCKER_BIN_TRIED=""
GOAMNEZIA_DOCKERD_VERSION="Docker version 29.1.4, build 29.1.4-0ubuntu1" run amnezia_watch_tick
check "DK-02 пакет обновили ещё раз, а 6 ч не прошли: одно сообщение с retry_after; в журнале — про шесть часов, а не «подключены люди»" \
    '[ "$(jget .docker_stale)" = backoff ] && [ "$(dk_ev)" = docker_stale ] && [ -n "$(dk_last | jq -r ".retry_after // empty")" ] && grep -q "меньше 6 ч назад" "$ERR" && ! grep -q "подключены люди" "$ERR" && ! grep -q "systemctl restart docker" "$ERR"' \
    "$out $(dk_last) $(errs | tail -3)"
GA_DOCKER_STALE=""; GA_DOCKER_BIN_TRIED=""
_ga_amn_dsw_put "{\"attempt_at\": $(( $(date +%s) - 21600 - 60 ))}"
GA_DOCKER_STALE=""; GA_DOCKER_DRY_RESTARTED=""; run amnezia_watch_tick
check "DK-02 6 ч прошли — перезапускает снова" '[ "$(jget .docker_stale)" = restarted ] && grep -q "DRY: systemctl restart docker" "$ERR"' "$out $(errs | tail -3)"

# Одна версия, другая сборка (так было на test1 04.10): в итоге — сборки, а не «служба теперь 24.0.7» о той же 24.0.7
dk_clean; dk_hs 0
printf '%s\n' '{"Version":"24.0.7","ApiVersion":"1.43","MinAPIVersion":"1.12","GitCommit":"24.0.7-0ubuntu4.1","Os":"linux"}' >"$TMP/dk_new_b.json"
GOAMNEZIA_DOCKERD_VERSION="Docker version 24.0.7, build 24.0.7-0ubuntu4.1" GOAMNEZIA_DOCKER_VERSION_JSON_AFTER="$TMP/dk_new_b.json" run amnezia_watch_tick
check "DK-02 сменилась одна сборка: перезапуск, в итоге сборки (было 24.0.7-0ubuntu4, стало 24.0.7-0ubuntu4.1), в журнале действий так же" \
    '[ "$(jget .docker_stale)" = restarted ] && [ "$(dk_last | jq -r .daemon_before_label)" = 24.0.7-0ubuntu4 ] && [ "$(dk_last | jq -r .daemon_after_label)" = 24.0.7-0ubuntu4.1 ] && grep -q "daemon=24.0.7-0ubuntu4->24.0.7-0ubuntu4.1" "$GOAMNEZIA_AUDIT_LOG"' \
    "$out $(dk_last) $(grep docker-restart "$GOAMNEZIA_AUDIT_LOG" | tail -1)"

# Замок занят (идёт установка или обновление) и apt/dpkg за работой — пропуск без попытки
dk_clean; dk_hs 0
_path_dk="$PATH"; export PATH="$FLOCKBIN:$PATH" FAKE_FLOCK_BUSY=1
run amnezia_watch_tick
check "DK-02 замок продукта занят: пропуск (docker_stale=busy), Docker не тронут, попытка не записана, событий нет" \
    '[ "$rc" = 0 ] && [ "$(jget .docker_stale)" = busy ] && ! grep -q "systemctl restart docker" "$ERR" && [ -z "$(jq -r ".attempt_at // empty" "$(ga_amn_dsw_file)" 2>/dev/null)" ] && [ "$(jget .events)" = 0 ]' \
    "$out $(errs | tail -3) $(cat "$(ga_amn_dsw_file)" 2>/dev/null)"
export PATH="$_path_dk"; unset FAKE_FLOCK_BUSY
GA_DOCKER_STALE=""; GOAMNEZIA_DPKG_BUSY=1 run amnezia_watch_tick
check "DK-02 apt/dpkg за работой (могут ставить docker.io): пропуск, Docker не тронут" \
    '[ "$(jget .docker_stale)" = busy ] && ! grep -q "systemctl restart docker" "$ERR"' "$out"

# Сбой перезапуска: событие с причиной и ручной командой, повтор — не раньше чем через 6 ч
dk_clean; dk_hs 0
GOAMNEZIA_DOCKER_VERSION_JSON_AFTER="$TMP/nosuch-after.json" GOAMNEZIA_DOCKER_RESTART_WAIT=1 run amnezia_watch_tick
check "DK-02 демон после перезапуска не ответил: docker_stale=failed, docker_restarted ok=false, код docker_restart_timeout, ручная команда" \
    '[ "$rc" = 0 ] && [ "$(jget .docker_stale)" = failed ] && [ "$(dk_ev)" = docker_restarting,docker_restarted ] && [ "$(dk_last | jq -r .ok)" = false ] && [ "$(dk_last | jq -r .code)" = docker_restart_timeout ] && [ "$(dk_last | jq -r .command)" = "systemctl restart docker" ] && [ "$(dk_last | jq -r .retry_hours)" = 6 ] && [ -n "$(dk_last | jq -r .reason)" ]' \
    "$out $(dk_ev) $(dk_last)"
check "DK-02 сбой — в журнал действий (ok=false с кодом) и в память (attempt_ok=false)" \
    'audit_has docker-restart "by=watch ok=false" watch && grep -q "code=docker_restart_timeout" "$GOAMNEZIA_AUDIT_LOG" && [ "$(jq -r .attempt_ok "$(ga_amn_dsw_file)")" = false ]' \
    "$(grep docker-restart "$GOAMNEZIA_AUDIT_LOG" | tail -1) $(cat "$(ga_amn_dsw_file)" 2>/dev/null)"
rm -f "$GOAMNEZIA_NOTIFY_DIR"/*.json
GA_DOCKER_DRY_RESTARTED=""; GA_DOCKER_STALE=""; GA_DOCKER_VERSION_TRIED=""; GA_DOCKER_VERSION_RAW=""
run amnezia_watch_tick
check "DK-02 после сбоя следующий тик: не повторяет (backoff) и не шлёт второе событие о том же" \
    '[ "$(jget .docker_stale)" = backoff ] && ! grep -q "systemctl restart docker" "$ERR" && [ -z "$(dk_ev)" ]' "$out $(dk_ev)"
dk_clean; dk_hs 0
GOAMNEZIA_DOCKER_VERSION_JSON_AFTER="" run amnezia_watch_tick
check "DK-02 перезапущен, а служба всё та же: failed, код docker_still_stale" \
    '[ "$(jget .docker_stale)" = failed ] && [ "$(dk_last | jq -r .code)" = docker_still_stale ]' "$out $(dk_last)"

# Мост docker-restart (бот, панель, MCP): тот же путь, итог — нажавшему
dk_clean; dk_hs 60
act status
check "DK-02 status.docker_stale: версии, причина, since; прошлой попытки нет" \
    '[ "$(lget .status)" = success ] && [ "$(lget .docker_stale.reason)" = version ] && [ "$(lget .docker_stale.binary_version)" = 29.1.3 ] && [ "$(lget .docker_stale.daemon_version)" = 24.0.7 ] && [ "$(lget .docker_stale.since)" = 2026-10-03T14:25:02Z ] && [ "$(lget .docker_stale.last_attempt)" = null ]' \
    "$(lget .docker_stale)"
act status --brief=on
check "DK-02 status brief (Обзор панели) тоже несёт docker_stale" '[ "$(lget .docker_stale.reason)" = version ]' "$(lget .docker_stale)"
GOAMNEZIA_WHO="panel:tester" act docker-restart
check "DK-02 мост docker-restart при людях на связи — сразу (это решение человека): success, DRY systemctl, профиль поднят, служба 29.1.3" \
    '[ "$rc" = 0 ] && [ "$nlines" = 1 ] && [ "$(lget .status)" = success ] && grep -q "DRY: systemctl restart docker" "$ERR" && [ "$(lget ".restarted|join(\",\")")" = awg2 ] && [ "$(lget .daemon_after)" = 29.1.3 ] && [ "$(lget .daemon_before)" = 24.0.7 ] && [ "$(lget .stale)" = false ] && [ "$(lget ".duration_s|type")" = number ] && [ -n "$(lget .message)" ]' \
    "rc=$rc $last $(errs | tail -3)"
check "DK-02 ручной перезапуск: событий боту нет (итог получил нажавший), журнал действий — кто нажал, by=manual" \
    '[ -z "$(dk_ev)" ] && audit_has docker-restart "by=manual ok=true" "panel:tester"' "$(dk_ev) $(grep docker-restart "$GOAMNEZIA_AUDIT_LOG" | tail -1)"
act status
check "DK-02 после ручной попытки status знает о ней: last_attempt.by=manual, ok=true, retry_after через 6 ч" \
    '[ "$(lget .docker_stale.last_attempt.by)" = manual ] && [ "$(lget .docker_stale.last_attempt.ok)" = true ] && [ -n "$(lget .docker_stale.retry_after)" ]' "$(lget .docker_stale)"
dk_hs 0
run amnezia_watch_tick
check "DK-02 ручная попытка тоже считается: 6 ч сторож сам не перезапускает" '[ "$(jget .docker_stale)" = backoff ]' "$out"
dk_clean
GOAMNEZIA_DOCKER_VERSION_JSON_AFTER="$TMP/nosuch-after.json" GOAMNEZIA_DOCKER_RESTART_WAIT=1 act docker-restart
check "DK-02 мост: демон не ответил — error, code=docker_restart_timeout, ручная команда в command, слова в message" \
    '[ "$rc" != 0 ] && [ "$nlines" = 1 ] && [ "$(lget .status)" = error ] && [ "$(lget .code)" = docker_restart_timeout ] && [ "$(lget .command)" = "systemctl restart docker" ] && [ -n "$(lget .message)" ]' \
    "rc=$rc $last"
dk_clean
printf 'missing\n' >"$GOAMNEZIA_CONTAINER_FS_DIR/amnezia-awg2/.state"
act docker-restart
check "DK-02 мост: Docker перезапущен, а контейнера профиля нет — error restart_failed, в failed — awg2 с кодом container_missing и словами" \
    '[ "$(lget .status)" = error ] && [ "$(lget .code)" = restart_failed ] && [ "$(lget ".failed[0].profile")" = awg2 ] && [ "$(lget ".failed[0].code")" = container_missing ] && [ -n "$(lget ".failed[0].message")" ] && [ "$(lget ".restarted|length")" = 0 ] && grep -q "awg2" <<<"$(lget .message)"' \
    "$last"
rm -f "$GOAMNEZIA_CONTAINER_FS_DIR/amnezia-awg2/.state"
dk_clean
GOAMNEZIA_DOCKER_VERSION_JSON="$TMP/dk_new.json" act status
check "DK-02 служба свежая — поле status.docker_stale есть и равно null" \
    '[ "$(lget .status)" = success ] && [ "$(lget "has(\"docker_stale\")")" = true ] && [ "$(lget .docker_stale)" = null ]' "$(lget .docker_stale)"
check "DK-02 действие docker-restart в каталоге: разрушительное, MCP docker_restart, без параметров" \
    '[ "$(jq -r ".actions[\"docker-restart\"].destructive" "$ROOT/lib/actions.json")" = true ] && [ "$(jq -r ".actions[\"docker-restart\"].mcp" "$ROOT/lib/actions.json")" = docker_restart ] && [ "$(jq -r ".actions[\"docker-restart\"].params|length" "$ROOT/lib/actions.json")" = 0 ]'
unset GOAMNEZIA_DOCKER_VERSION_JSON GOAMNEZIA_DOCKER_VERSION_JSON_AFTER GOAMNEZIA_DOCKERD_VERSION GOAMNEZIA_DOCKERD_SINCE GOAMNEZIA_AWG_SHOW_FIXTURE GA_AMN_WATCH_RECHECK_SECONDS
dk_clean

# ── 6. Словарь ───────────────────────────────────────────────────────────────
section "словари timers/stats"
for m in timers stats; do
    ru=$(grep -o '^I18N\[[a-z_0-9]*\]' "$ROOT/lib/lang/ru.d/$m.sh" | sort); en=$(grep -o '^I18N\[[a-z_0-9]*\]' "$ROOT/lib/lang/en.d/$m.sh" | sort)
    check "словарь $m: ru и en совпадают" '[ "$ru" = "$en" ] && [ -n "$ru" ]'
done

DONE=1
printf '\n══ ИТОГ: пройдено %d, провалено %d, пропущено %d\n' "$pass" "$fail" "$skip"
[ "$fail" -eq 0 ]
