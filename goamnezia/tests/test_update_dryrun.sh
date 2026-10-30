#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
# goAmnezia pro — эксплуатационные находки OPS-01…05 вхолостую (GOAMNEZIA_DRY_RUN=1, без Docker и root):
# запись служебных файлов при кончившемся месте, прерванная и упавшая миграция, откат неудачного обновления
# (код, данные, .install-source), цепочки обновления со старых выпусков, изоляция подхваченного контейнера
# сразу после старта, место на диске в status и в шапке меню.
#
# Запуск: ${BASH5:-/opt/homebrew/bin/bash} tests/test_update_dryrun.sh
# Нужны: bash ≥ 5, jq, python3, git (цепочки берут прежние выпуски из истории репозитория; без истории —
# пропуск с причиной).
#
# Каждый сервер — свой каталог в $TMP: GOAMNEZIA_DIR, замок, журналы, очередь уведомлений, юниты systemd.
# systemctl машины подменён (пишет вызов в журнал и отвечает отказом), docker не нужен (сухой прогон).
# ══════════════════════════════════════════════════════════════════════════════
set -uo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
BASH5="${BASH5:-$BASH}"
TMP=$(mktemp -d "${TMPDIR:-/tmp}/goamnezia-update.XXXXXX") || exit 2
DONE=""
OUT="$TMP/stdout.log"
ERR="$TMP/stderr.log"
trap 'if [ -z "$DONE" ]; then printf "\n!! аварийный выход, последний stderr:\n"; tail -n 20 "$ERR" 2>/dev/null; fi; chmod -R u+w "$TMP" 2>/dev/null; rm -rf "$TMP"' EXIT

export GOAMNEZIA_DRY_RUN=1
export GOAMNEZIA_SERVER_IP="203.0.113.10"
export GOAMNEZIA_LANG=ru
unset DOCKER_HOST DOCKER_CONTEXT GOAMNEZIA_BRANCH GOAMNEZIA_REF GOAMNEZIA_EXPECT_VERSION GOAMNEZIA_PAT \
      GOAMNEZIA_LOCAL_TREE GOAMNEZIA_BOOTSTRAP_CHECK GOAMNEZIA_AFTER_ROLLBACK
export GOAMNEZIA_DOCKER_SOCK="$TMP/nosuch.sock"
# systemctl машины прогону не нужен (как в tests/test_actions_dryrun.sh, L1-20).
HOSTBIN="$TMP/hostbin"; mkdir -p "$HOSTBIN"
printf '#!/bin/sh\nprintf "%%s\\n" "$*" >>"%s"\nexit 1\n' "$TMP/host-systemctl.calls" >"$HOSTBIN/systemctl"
chmod +x "$HOSTBIN/systemctl"
# Место на диске (TAIL-12): набор задаёт фикстуру GOAMNEZIA_DF_FIXTURE (tests/run_static.sh), и нынешний код меряет
# по ней, а прежние выпуски в цепочках её не знают и спросили бы df машины — на маленьком диске CI им «мало места».
# Им отвечает этот df: фикстура задана — её вывод, нет — настоящий df.
REAL_DF=$(command -v df || echo /bin/df)
printf '#!/bin/sh\nif [ -n "${GOAMNEZIA_DF_FIXTURE:-}" ] && [ -r "$GOAMNEZIA_DF_FIXTURE" ]; then cat "$GOAMNEZIA_DF_FIXTURE"; exit 0; fi\nexec "%s" "$@"\n' \
    "$REAL_DF" >"$HOSTBIN/df"
chmod +x "$HOSTBIN/df"
export PATH="$HOSTBIN:$PATH"
REAL_PY=$(command -v python3 || true)

pass=0; fail=0; skip=0; rc=0; last=""; nlines=0
ok()   { pass=$((pass + 1)); printf '  ✓ %s\n' "$1"; }
bad()  { fail=$((fail + 1)); printf '  ✗ %s\n' "$1"; [ -n "${2:-}" ] && printf '      %s\n' "$2" | head -c 1200; return 0; }
skipped() { skip=$((skip + 1)); printf '  – %s (пропущено: %s)\n' "$1" "$2"; }
check() { if ( eval "$2" ) >/dev/null 2>&1; then ok "$1"; else bad "$1" "${3:-}"; fi; }
section() { printf '\n── %s\n' "$1"; }
errs() { tail -n 12 "$ERR" 2>/dev/null; }
jget() { jq -r "$1" <<<"$last" 2>/dev/null; }
perm() { stat -c %a "$1" 2>/dev/null || stat -f %Lp "$1" 2>/dev/null; }
sha() { { sha256sum "$1" 2>/dev/null || shasum -a 256 "$1"; } | awk '{print $1}'; }

# stand <имя> — чистый «сервер»: всё своё во временном каталоге, код по умолчанию — из репозитория.
stand() {
    S="$TMP/$1"
    rm -rf "$S"; mkdir -p "$S"
    export GOAMNEZIA_DIR="$S/ga" GOAMNEZIA_LOCK_DIR="$S/run" GOAMNEZIA_LOG_FILE="$S/goamnezia.log" \
           GOAMNEZIA_AUDIT_LOG="$S/audit.log" GOAMNEZIA_NOTIFY_DIR="$S/run/notify" GOAMNEZIA_BUILD_ROOT="$S/build" \
           GOAMNEZIA_SYSTEMD_DIR="$S/systemd" GOAMNEZIA_SECRET_TMPDIR="$S/shm" \
           BOT_DIR="$S/bot" ADMIN_DIR="$S/admin" MCP_DIR="$S/mcp" \
           NGINX_SITE_CONF="$S/etc-nginx/sites-available/goamnezia" NGINX_SITE_LINK="$S/etc-nginx/sites-enabled/goamnezia"
    mkdir -p "$GOAMNEZIA_DIR" "$GOAMNEZIA_LOCK_DIR" "$GOAMNEZIA_SYSTEMD_DIR" "$GOAMNEZIA_SECRET_TMPDIR"
    CFG="$GOAMNEZIA_DIR/config.json"
    APP="$ROOT/install.sh"
    # Закреплённые образы по умолчанию — $GOAMNEZIA_DIR/lib/upstream, как на сервере; код отдельно от данных —
    # задать явно (UPSTREAM_DIR=<код>/lib/upstream).
    unset UPSTREAM_DIR
}
# seed_config <версия> — config.json «после мастера», как у tests/test_actions_dryrun.sh.
seed_config() {
    cat >"$CFG" <<EOF
{"schema": 1, "version": "$1", "installed": true, "mode": "lite", "lang": "ru", "domain": "",
 "server_ip": "203.0.113.10", "endpoint_host": "", "client_dns": ["1.1.1.1", "1.0.0.1"], "profiles": {}}
EOF
    chmod 600 "$CFG"
    printf 'ru\n' >"$GOAMNEZIA_DIR/.language"
}
# act <действие> [--k=v …] — мост кодом $APP, как бот: новый процесс, stdout — ровно одна строка JSON.
act() {
    local action="$1"; shift
    # Подоболочка — ради строки «Killed: 9» от bash, когда проверка обрывает процесс нарочно.
    ( "$BASH5" "$APP" --action="$action" --json "$@" >"$OUT" 2>"$ERR" </dev/null; exit $? ) 2>/dev/null; rc=$?
    last=$(tail -n 1 "$OUT")
    nlines=$(wc -l <"$OUT" | tr -d ' ')
    return 0
}
expect() {
    local what="$1" status="$2" code="${3:-}"
    if [ "$nlines" = 1 ] && jq -e . <<<"$last" >/dev/null 2>&1 && [ "$(jget .status)" = "$status" ] \
       && { [ -z "$code" ] || [ "$(jget .code)" = "$code" ]; }; then
        ok "$what"
    else
        bad "$what" "rc=$rc lines=$nlines last=${last:0:300} | $(errs | tr '\n' ' ' | head -c 400)"
    fi
}
# leftovers <каталог> — времянки атомарной записи (<файл>.XXXXXX от mktemp), оставшиеся после отказа.
leftovers() {
    local f
    for f in "$1"/* "$1"/.[!.]*; do
        [ -f "$f" ] || continue
        [[ "${f##*/}" =~ ^.+\.[A-Za-z0-9]{6}$ ]] && printf '%s\n' "$f"
    done
    return 0
}

GA_VER=$(sed -n 's/^GOAMNEZIA_VERSION="\([^"]*\)".*/\1/p' "$ROOT/lib/common.sh" | head -1)

section "окружение"
printf '  bash: %s (%s), версия кода %s\n' "$BASH5" "$("$BASH5" -c 'echo $BASH_VERSION')" "$GA_VER"
check "jq и python3 на месте" 'command -v jq && [ -n "$REAL_PY" ]'

# ══════════════════════════════════════════════════════════════════════════════
# OPS-04. Запись при кончившемся месте: ошибка выходит наружу, прежний файл цел
# ══════════════════════════════════════════════════════════════════════════════
# Кончившееся место изображает подставной python3: он отказывает на fsync-и-rename атомарного писателя
# (_ga_commit_file зовёт `python3 - <времянка> <цель>`) — ровно так ENOSPC отложенной записи доходит до продукта.
# Всё остальное подставной отдаёт настоящему python3.
section "OPS-04: запись служебных файлов при кончившемся месте"
stand ops04
seed_config "$GA_VER"
ENOSPC="$S/enospc"; mkdir -p "$ENOSPC"
cat >"$ENOSPC/python3" <<EOS
#!/bin/sh
[ "\${1:-}" = "-" ] && { echo "No space left on device" >&2; exit 1; }
exec "$REAL_PY" "\$@"
EOS
chmod +x "$ENOSPC/python3"
# Миграция этого кода уже прошла: первое пишущее действие ставит отметку с отпечатком кода.
act lang-set --lang=ru
cfg_before=$(cat "$CFG")
PATH="$ENOSPC:$PATH" act lang-set --lang=en
expect "OPS-04 lang-set при ENOSPC → error lang_write_failed, а не success" error lang_write_failed
check "OPS-04 в ответе названо, что не записалось (config.json)" '[[ "$(jget .message)" == *config.json* ]]' "$last"
check "OPS-04 config.json и .language — прежние (ru), времянок не осталось" \
    '[ "$(cat "$CFG")" = "$cfg_before" ] && [ "$(cat "$GOAMNEZIA_DIR/.language")" = ru ] && [ -z "$(leftovers "$GOAMNEZIA_DIR")" ]' \
    "lang=$(jq -r .lang "$CFG") marker=$(cat "$GOAMNEZIA_DIR/.language") left=$(leftovers "$GOAMNEZIA_DIR" | tr '\n' ' ')"
act lang-set --lang=en
expect "OPS-04 место есть — lang-set success" success
check "OPS-04 язык записан и в config.json, и в .language (0644)" \
    '[ "$(jq -r .lang "$CFG")" = en ] && [ "$(cat "$GOAMNEZIA_DIR/.language")" = en ] && [ "$(perm "$GOAMNEZIA_DIR/.language")" = 644 ]' \
    "lang=$(jq -r .lang "$CFG") marker=$(cat "$GOAMNEZIA_DIR/.language")"
act lang-set --lang=ru
# Отметка миграции при ENOSPC не обнуляется: код «сменился» (UP-7) — шаги повторяются, а отметка не
# записывается и остаётся прежней, человеку сказано почему; следующий вызов повторит.
printf 'stale\n' >"$GOAMNEZIA_DIR/.migrated_$GA_VER"
PATH="$ENOSPC:$PATH" act lang-set --lang=ru
check "OPS-04 отметка миграции при ENOSPC не обнулилась (осталась прежней), причина — в stderr" \
    '[ "$(cat "$GOAMNEZIA_DIR/.migrated_$GA_VER")" = stale ] && grep -qF "Не удалось записать $GOAMNEZIA_DIR/.migrated_$GA_VER" "$ERR" && [ -z "$(leftovers "$GOAMNEZIA_DIR")" ]' \
    "marker=$(cat "$GOAMNEZIA_DIR/.migrated_$GA_VER") $(errs)"
act lang-set --lang=ru
check "OPS-04 место вернулось — отметка переписана отпечатком кода" \
    '[[ "$(cat "$GOAMNEZIA_DIR/.migrated_$GA_VER")" =~ ^[0-9a-f]{64}$ ]]' "$(cat "$GOAMNEZIA_DIR/.migrated_$GA_VER")"
# ga_write_file напрямую: отказ фиксации — код write_failed, цель прежняя; успех — ровно то содержимое.
wf_out=$("$BASH5" -c '
    set -u; GOAMNEZIA_DIR="$1"; source "$2/lib/common.sh"; source "$2/lib/i18n.sh"; load_language ru
    printf "old\n" >"$GOAMNEZIA_DIR/wf"
    _ga_commit_file() { return 1; }
    ga_write_file "$GOAMNEZIA_DIR/wf" "new" 644; echo "rc=$? code=$GA_ERR_CODE content=$(cat "$GOAMNEZIA_DIR/wf")"
    unset -f _ga_commit_file; source "$2/lib/common.sh"
    ga_write_file "$GOAMNEZIA_DIR/wf" "новое
значение" 640; echo "rc=$? mode=$(stat -c %a "$GOAMNEZIA_DIR/wf" 2>/dev/null || stat -f %Lp "$GOAMNEZIA_DIR/wf")"
    cat "$GOAMNEZIA_DIR/wf"' _ "$GOAMNEZIA_DIR" "$ROOT" 2>&1)
check "OPS-04 ga_write_file: отказ фиксации → rc 1, write_failed, прежнее содержимое цело" \
    'grep -qx "rc=1 code=write_failed content=old" <<<"$wf_out"' "$wf_out"
check "OPS-04 ga_write_file: успех — ровно то содержимое, права как просили, времянок нет" \
    'grep -qx "rc=0 mode=640" <<<"$wf_out" && [ "$(tail -n 2 <<<"$wf_out")" = "новое
значение" ] && [ -z "$(leftovers "$GOAMNEZIA_DIR")" ]' "$wf_out"
naked_marks() {
    grep -nF -e 'touch "$marker"' -e '>"$marker"' -e '>"$GOAMNEZIA_DIR/.changelog_pending"' \
        -e 'touch "$GOAMNEZIA_DIR/.preupgrade_' -e '>"$stamp"' "$ROOT/install.sh"
    grep -nF -e 'echo "$lang" > ' "$ROOT/lib/i18n.sh"
}
check "OPS-04 в миграции и языке нет больше голых записей отметок (printf >отметка, touch, echo >.language)" \
    '[ -z "$(naked_marks)" ]' "$(naked_marks)"

# ══════════════════════════════════════════════════════════════════════════════
# OPS-03. Прерванная и упавшая миграция доделывается следующим вызовом
# ══════════════════════════════════════════════════════════════════════════════
# Код — копия репозитория с двумя шагами миграции после _migrate_state_conf (так же их вставляет живая проверка
# update-rollback): «сорваться» по флагу — убить весь процесс (обрыв SSH, перезагрузка) или упасть на set -u
# (ошибка в новом коде) — и «сторож», отмечающий, что шаги ПОСЛЕ точки обрыва выполнились.
# inject_steps <install.sh> — вставка шагов; флаги — в $GA_T_FLAGS.
inject_steps() {
    python3 - "$1" <<'PY'
import sys
p = sys.argv[1]
s = open(p, encoding='utf-8').read()
anchor = "    _migrate_profile_line\n    _migrate_state_conf\n"
tail = '\nmain "$@"\n'
assert s.count(anchor) == 1 and s.endswith(tail), 'якорь шагов миграции или последняя строка install.sh не найдены'
s = s.replace(anchor, anchor + "    _ga_t_step_break\n    _ga_t_step_after\n")
s = s[:-len(tail)] + r'''
_ga_t_step_break() {
    if [ -f "$GA_T_FLAGS/kill" ]; then rm -f "$GA_T_FLAGS/kill"; kill -9 "$$" "$BASHPID"; fi
    if [ -f "$GA_T_FLAGS/crash" ]; then echo "ga-test: the migration step crashes now" >&2; set -u; : "${_ga_t_unset_var}"; fi
    return 0
}
_ga_t_step_after() { touch "$GA_T_FLAGS/after_ran"; }
''' + tail
open(p, 'w', encoding='utf-8').write(s)
PY
}
# code_copy <каталог> — код продукта отдельно от данных (как стенд): install.sh, lib/, каталог шаблонов.
code_copy() {
    mkdir -p "$1"
    cp "$ROOT/install.sh" "$1/" && cp -R "$ROOT/lib" "$1/lib" && ln -s "$ROOT/templates_catalog.json" "$1/templates_catalog.json"
}
# mig_reset <версия> — состояние «от прежней версии», без отметок прошлой миграции.
mig_reset() {
    jq --arg v "$1" '.version = $v' "$CFG" >"$CFG.t" && mv "$CFG.t" "$CFG" && chmod 600 "$CFG"
    rm -f "$GOAMNEZIA_DIR"/.migrated_* "$GOAMNEZIA_DIR"/.migrating_* "$GOAMNEZIA_DIR"/.preupgrade_* "$GOAMNEZIA_DIR/.changelog_pending"
    rm -f "$GA_T_FLAGS"/*
}
nbk() { ls "$GOAMNEZIA_DIR/backups"/*.tar.gz 2>/dev/null | wc -l | tr -d ' '; }

section "OPS-03: прерванная миграция доделывается"
stand ops03
seed_config "$GA_VER"
export GA_T_FLAGS="$S/flags"; mkdir -p "$GA_T_FLAGS"
code_copy "$S/code"; inject_steps "$S/code/install.sh"; APP="$S/code/install.sh"; export UPSTREAM_DIR="$S/code/lib/upstream"
act profile-install --profile=awg2 --port=443
act user-add --name=anna
expect "OPS-03 стенд: профиль и человек заведены кодом с вставленными шагами" success
mig_reset 0.4.6
touch "$GA_T_FLAGS/kill"
act lang-set --lang=ru
bk1=$(nbk)
check "OPS-03 миграция оборвана посреди шагов (процесс убит)" '[ "$rc" = 137 ] && [ ! -f "$GA_T_FLAGS/after_ran" ]' "rc=$rc $(errs)"
check "OPS-03 после обрыва: версия прежняя, отметки и «Что нового» нет, отметка начала с путём снимка" \
    '[ "$(jq -r .version "$CFG")" = 0.4.6 ] && [ ! -e "$GOAMNEZIA_DIR/.migrated_$GA_VER" ] && [ ! -e "$GOAMNEZIA_DIR/.changelog_pending" ] && grep -qx "from=0.4.6" "$GOAMNEZIA_DIR/.migrating_$GA_VER" && [ -f "$(sed -n "s/^archive=//p" "$GOAMNEZIA_DIR/.migrating_$GA_VER")" ]' \
    "version=$(jq -r .version "$CFG") wip=$(tr '\n' ' ' <"$GOAMNEZIA_DIR/.migrating_$GA_VER" 2>/dev/null) $(ls -a "$GOAMNEZIA_DIR" | tr '\n' ' ')"
act lang-set --lang=ru
expect "OPS-03 следующий вызов (как тик сторожа) отвечает как обычно" success
check "OPS-03 следующий вызов доделал миграцию: шаги после точки обрыва выполнены, сказано человеку" \
    '[ -f "$GA_T_FLAGS/after_ran" ] && grep -qF "Доделываю прерванное обновление 0.4.6 → $GA_VER" "$ERR"' "$(errs)"
check "OPS-03 версия, отметка с отпечатком и «Что нового» (0.4.6) — только теперь; отметки начала нет" \
    '[ "$(jq -r .version "$CFG")" = "$GA_VER" ] && [[ "$(cat "$GOAMNEZIA_DIR/.migrated_$GA_VER")" =~ ^[0-9a-f]{64}$ ]] && [ "$(cat "$GOAMNEZIA_DIR/.changelog_pending")" = 0.4.6 ] && [ ! -e "$GOAMNEZIA_DIR/.migrating_$GA_VER" ]' \
    "version=$(jq -r .version "$CFG") pending=$(cat "$GOAMNEZIA_DIR/.changelog_pending" 2>/dev/null)"
check "OPS-03 снимок второй раз не снимался" '[ "$(nbk)" = "$bk1" ]' "было $bk1, стало $(nbk)"

# Упавший шаг (ошибка нового кода): процесс жив, действие отвечает, миграция не отмечена сделанной.
mig_reset 0.4.6
touch "$GA_T_FLAGS/crash"
act lang-set --lang=ru
expect "OPS-03 шаг миграции упал (set -u) — мост всё равно ответил JSON" success
check "OPS-03 упавшая миграция не отмечена: версия прежняя, ни отметки, ни «Что нового», сказано «не закончена»" \
    '[ "$(jq -r .version "$CFG")" = 0.4.6 ] && [ ! -e "$GOAMNEZIA_DIR/.migrated_$GA_VER" ] && [ ! -e "$GOAMNEZIA_DIR/.changelog_pending" ] && [ -f "$GOAMNEZIA_DIR/.migrating_$GA_VER" ] && [ ! -f "$GA_T_FLAGS/after_ran" ] && grep -qF "Миграция на $GA_VER не закончена" "$ERR"' \
    "version=$(jq -r .version "$CFG") $(errs)"
rm -f "$GA_T_FLAGS/crash"
act lang-set --lang=ru
check "OPS-03 ошибку исправили — следующий вызов доделывает: шаги, версия, отметка, «Что нового»" \
    '[ -f "$GA_T_FLAGS/after_ran" ] && [ "$(jq -r .version "$CFG")" = "$GA_VER" ] && [ -f "$GOAMNEZIA_DIR/.migrated_$GA_VER" ] && [ "$(cat "$GOAMNEZIA_DIR/.changelog_pending")" = 0.4.6 ] && [ ! -e "$GOAMNEZIA_DIR/.migrating_$GA_VER" ]' \
    "version=$(jq -r .version "$CFG") $(errs)"
# Шаг, вышедший раньше времени с кодом 0 (exit в новом коде), — тоже незаконченная миграция.
exit_fn=$(awk '/^_ga_migrate_run\(\)/,/^}/' "$ROOT/install.sh")
check "OPS-03 конец шагов подтверждается строкой из подоболочки (exit 0 посреди шагов — отказ)" \
    'grep -q "steps-done" <<<"$exit_fn" && grep -q "_ga_migrate_steps" <<<"$exit_fn"'
check "OPS-03 версия в config.json пишется только после шагов (в шагах её нет)" \
    '! awk "/^_ga_migrate_steps\\(\\)/,/^}/" "$ROOT/install.sh" | grep -q "version"'

# INST-03 (чистая установка на сервере 168, 04.10.2026): мастер прежних версий писал желаемый режим в config.json ДО
# установки сайта, и установку прервали раньше сайта. Загрузчик (новый код той же версии — пересборка vhost, UP-7)
# собирал vhost сайта, которого нет: на сервере nginx -t отказывал, и человек читал «Конфиг сайта новой версии не
# применился — сайт работает на прежнем». «Сайт есть» теперь — по факту (наш vhost на диске), а не по режиму.
section "INST-03: режим с сайтом в config.json, а сайта на сервере нет — пересборки vhost нет и ложной строки нет"
stand inst03
seed_config "$GA_VER"
jq '.mode = "pro" | .domain = "example.com"' "$CFG" >"$CFG.tmp" && mv "$CFG.tmp" "$CFG"; chmod 600 "$CFG"
export GOAMNEZIA_NGINX_DIR="$S/nginx"
VH="$GOAMNEZIA_NGINX_DIR/sites-available/goamnezia"
act lang-set --lang=ru
# Код на диске сменился при той же версии (загрузчик поверх, как на 168) — шаги, зависящие от кода, ещё раз.
printf 'stale\n' >"$GOAMNEZIA_DIR/.migrated_$GA_VER"
act lang-set --lang=ru
expect "INST-03 действие ответило как обычно" success
check "INST-03 шаги миграции прошли (отметка с отпечатком кода переписана)" \
    '[[ "$(cat "$GOAMNEZIA_DIR/.migrated_$GA_VER")" =~ ^[0-9a-f]{64}$ ]]' "$(errs)"
check "INST-03 сайта не было — vhost не появился и nginx не трогался" \
    '[ ! -e "$VH" ] && ! grep -qF "DRY: nginx -t" "$ERR"' "$(ls -laR "$GOAMNEZIA_NGINX_DIR" 2>&1 | head -n 8) $(errs)"
check "INST-03 нет ложного «Конфиг сайта новой версии не применился — сайт работает на прежнем»" \
    '! grep -qF "не применился" "$ERR" && ! grep -qF "работает на прежнем" "$ERR"' "$(errs)"
# Контроль: сайт ставился (vhost на диске) — пересборка под новую версию идёт, как и раньше (защиты новой версии
# доходят до поставленных серверов).
mkdir -p "${VH%/*}"; printf 'server { server_name old.example; }\n' >"$VH"
printf 'stale\n' >"$GOAMNEZIA_DIR/.migrated_$GA_VER"
act lang-set --lang=ru
check "INST-03 контроль: vhost был — пересобран под домен из config.json" \
    'grep -q "server_name example.com" "$VH" && ! grep -q "old.example" "$VH"' "$(head -n 12 "$VH" 2>&1) $(errs)"
unset GOAMNEZIA_NGINX_DIR

# ══════════════════════════════════════════════════════════════════════════════
# OPS-02. Неудачное обновление откатывается: прежний код, данные из снимка preupgrade, .install-source.
#         Цепочки обновления со старых выпусков: 0.4.4, 0.4.5, 0.4.6 → текущий код
# ══════════════════════════════════════════════════════════════════════════════
# Прежние выпуски — из истории репозитория (git archive), разложены как у ИХ загрузчика (FILES и FILES_OPTIONAL
# их bootstrap.sh). Обновление — НАШИМ загрузчиком из каталога (GOAMNEZIA_LOCAL_TREE) с проверкой запуска новой
# версии (GOAMNEZIA_BOOTSTRAP_CHECK=1), как «Обновить goAmnezia»: снимок прежнего кода, раскладка через rename,
# .install-source. Миграцию проводит первый пишущий вызов нового кода — как тик сторожа или бот.
OLD_RELEASES="0.4.4:5415bb5 0.4.5:695bf6c 0.4.6:f6cd5d5"
SHA_OLD=1111111111111111111111111111111111111111
SHA_NEW=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
HAVE_GIT=1
for _rel in $OLD_RELEASES; do
    git -C "$ROOT" cat-file -e "${_rel#*:}^{commit}" 2>/dev/null || HAVE_GIT=0
done
# Исторические выпуски содержат $label и сами не запускаются с jq 1.6. Их код не правим:
# цепочки проверяются отдельно с jq >= 1.7, новый код — со штатным jq Ubuntu 22.04.
HAVE_LEGACY_JQ=1
jq -n --arg label test '$label' >/dev/null 2>&1 || HAVE_LEGACY_JQ=0

# old_tree <коммит> <каталог> — дерево прежнего выпуска.
old_tree() {
    mkdir -p "$2" && git -C "$ROOT" archive --format=tar "$1" 2>/dev/null | tar -x -C "$2" 2>/dev/null
    [ -f "$2/install.sh" ] && [ -f "$2/bootstrap.sh" ]
}
# lay_listed <дерево> <каталог> — раскладка, как у загрузчика ЭТОГО дерева: только его FILES и FILES_OPTIONAL.
lay_listed() {
    python3 - "$1" "$2" <<'PY'
import os, re, shutil, sys
src, dst = sys.argv[1], sys.argv[2]
text = open(os.path.join(src, 'bootstrap.sh'), encoding='utf-8').read()
for name in ('FILES', 'FILES_OPTIONAL'):
    m = re.search(r'^%s=\(\s*(.*?)^\)' % name, text, re.S | re.M)
    for line in m.group(1).splitlines():
        for rel in re.findall(r'"([^"]+)"', line.split('#', 1)[0]):
            s = os.path.join(src, rel)
            if os.path.isfile(s):
                d = os.path.join(dst, rel)
                os.makedirs(os.path.dirname(d), exist_ok=True)
                shutil.copy2(s, d)
                if rel.endswith(('.sh', '.py')):
                    os.chmod(d, 0o755)
PY
}
# new_bootstrap <дерево> [ПЕРЕМЕННАЯ=значение …] — наш загрузчик ставит дерево из каталога.
# Повтор — ровно на код 139 при пустом выводе: так падает сам bash из homebrew на macOS (setlocale через
# CoreFoundation под нагрузкой, подробно — tests/test_bootstrap_archive.py); загрузчик к этому мигу ещё ничего
# не тронул — он всегда начинает с вывода рамки.
new_bootstrap() {
    local tree="$1" n
    shift
    for n in 1 2 3 4; do
        ( env GOAMNEZIA_LOCAL_TREE="$tree" GOAMNEZIA_BOOTSTRAP_CHECK=1 GOAMNEZIA_BRANCH=dev GOAMNEZIA_REF="$SHA_NEW" "$@" \
            "$BASH5" "$ROOT/bootstrap.sh" >"$OUT" 2>"$ERR" </dev/null; exit $? ) 2>/dev/null; rc=$?
        [ "$rc" = 139 ] && [ ! -s "$OUT" ] || break
    done
}
# code_sums <каталог> — отпечатки всех файлов кода (без данных): «код ровно прежний».
code_sums() {
    ( cd "$1" && find install.sh lib goamnezia-bot goamnezia-mcp admin-web templates_catalog.json -type f 2>/dev/null \
        | LC_ALL=C sort | while IFS= read -r f; do printf '%s %s\n' "$(sha "$f")" "$f"; done )
}
state_sums() {
    ( cd "$1/state" 2>/dev/null && find . -type f 2>/dev/null | LC_ALL=C sort \
        | while IFS= read -r f; do printf '%s %s\n' "$(sha "$f")" "$f"; done )
}
unit_sums() {
    ( cd "$GOAMNEZIA_SYSTEMD_DIR" 2>/dev/null && for f in goamnezia-*; do [ -f "$f" ] && printf '%s %s\n' "$(sha "$f")" "$f"; done )
}
peers_keys() { jq -S '[.users[] | {name, peers}]' "$GOAMNEZIA_DIR/users.json" 2>/dev/null; }
prev_kv() { sed -n "s/^$2=//p" "$GOAMNEZIA_DIR/.prev_code/$1" 2>/dev/null | head -n 1; }
# no_tty <команда…> — без управляющего терминала: меню не заберёт терминал человека, гоняющего прогон руками.
no_tty() { python3 -c 'import os, sys
os.setsid()
os.execvp(sys.argv[1], sys.argv[1:])' "$@"; }
# old_install <версия> <коммит> — сервер на прежнем выпуске: профиль awg2:443 и двое людей — его же мостом.
old_install() {
    old_tree "$2" "$S/old" || return 1
    lay_listed "$S/old" "$GOAMNEZIA_DIR"
    seed_config "$1"
    printf 'branch=dev\nrepo=potap1978/go-all\nref=%s\n' "$SHA_OLD" >"$GOAMNEZIA_DIR/.install-source"
    APP="$GOAMNEZIA_DIR/install.sh"
    act profile-install --profile=awg2 --port=443; [ "$(jget .status)" = success ] || return 1
    act user-add --name=anna;                      [ "$(jget .status)" = success ] || return 1
    act user-add --name=boris;                     [ "$(jget .status)" = success ] || return 1
    return 0
}
# candidate <каталог> <версия> — кандидат в новые версии: файлы репозитория, в lib/common.sh — эта версия.
candidate() {
    mkdir -p "$1"
    lay_listed "$ROOT" "$1"
    python3 - "$1/lib/common.sh" "$2" <<'PY'
import re, sys
p, v = sys.argv[1], sys.argv[2]
s = open(p, encoding='utf-8').read()
s, n = re.subn(r'^GOAMNEZIA_VERSION="[^"]*"', 'GOAMNEZIA_VERSION="%s"' % v, s, count=1, flags=re.M)
assert n == 1
open(p, 'w', encoding='utf-8').write(s)
PY
}
# inject_status_crash <install.sh> — status кандидата падает (set -u) при флаге status_crash: «новый код не
# отвечает» после удавшейся миграции.
inject_status_crash() {
    python3 - "$1" <<'PY'
import sys
p = sys.argv[1]
s = open(p, encoding='utf-8').read()
tail = '\nmain "$@"\n'
assert s.endswith(tail)
s = s[:-len(tail)] + r'''
ga_export_endpoint_host_gone() {
    if [ -f "$GA_T_FLAGS/status_crash" ]; then echo "ga-test: status crashes now" >&2; set -u; : "${_ga_t_unset_status}"; fi
    return 1
}
''' + tail
open(p, 'w', encoding='utf-8').write(s)
PY
}
# rb_state [nounits] — всё, что откат обязан вернуть: код, config.json и users.json (по смыслу), state/,
# .install-source и (без nounits) юниты systemd.
rb_state() {
    printf '%s\n--\n%s\n--\n%s\n--\n%s\n--\n%s\n' "$(code_sums "$GOAMNEZIA_DIR")" "$(jq -S . "$CFG")" \
        "$(jq -S . "$GOAMNEZIA_DIR/users.json")" "$(state_sums "$GOAMNEZIA_DIR")" \
        "$(cat "$GOAMNEZIA_DIR/.install-source")"
    [ "${1:-}" = nounits ] || printf -- '--\n%s\n' "$(unit_sums)"
}
upd_traces() {
    local f
    for f in "$GOAMNEZIA_DIR"/.prev_code "$GOAMNEZIA_DIR"/.migrating_* "$GOAMNEZIA_DIR"/.migrated_*rbtest \
             "$GOAMNEZIA_DIR"/.preupgrade_*rbtest*; do
        [ -e "$f" ] && printf '%s\n' "${f##*/}"
    done
    return 0
}

if [ "$HAVE_GIT" != 1 ] || [ "$HAVE_LEGACY_JQ" != 1 ]; then
    section "OPS-02: цепочки обновления и откат"
    if [ "$HAVE_GIT" != 1 ]; then
        skipped "цепочки 0.4.4/0.4.5/0.4.6 → $GA_VER и откат обновления" "нет истории репозитория (git archive прежних выпусков)"
    else
        skipped "цепочки 0.4.4/0.4.5/0.4.6 → $GA_VER и откат обновления" "прежним выпускам нужен jq >= 1.7; повторите этот набор с ним"
    fi
else
section "OPS-02: цепочки обновления 0.4.4 → $GA_VER, 0.4.5 → $GA_VER, 0.4.6 → $GA_VER"
for _rel in $OLD_RELEASES; do
    v="${_rel%%:*}"
    stand "chain-$v"
    if ! old_install "$v" "${_rel#*:}"; then
        bad "цепочка $v: прежний выпуск не встал" "$last $(errs)"
        continue
    fi
    keys0=$(peers_keys); srv0=$(sha "$GOAMNEZIA_DIR/state/amnezia-awg2/wireguard_server_private_key.key")
    old_install_sum=$(sha "$GOAMNEZIA_DIR/install.sh")
    new_bootstrap "$ROOT"
    check "$v → $GA_VER: наш загрузчик разложил новую версию, прежняя — рядом (.prev_code: from $v, to $GA_VER)" \
        '[ "$rc" = 0 ] && [ "$(prev_kv meta from)" = "$v" ] && [ "$(prev_kv meta to)" = "$GA_VER" ] && [ "$(prev_kv meta from_ref)" = "$SHA_OLD" ] && grep -qx "old install.sh" "$GOAMNEZIA_DIR/.prev_code/manifest" && grep -qx "new lib/update.sh" "$GOAMNEZIA_DIR/.prev_code/manifest" && grep -qF "Прежняя версия сохранена рядом для отката: $v" "$OUT" && [ "$("$BASH5" "$GOAMNEZIA_DIR/install.sh" --version)" = "goAmnezia pro $GA_VER" ]' \
        "rc=$rc $(tail -n 6 "$OUT") $(cat "$GOAMNEZIA_DIR/.prev_code/meta" 2>/dev/null)"
    check "$v → $GA_VER: прежний код в снимке — байт в байт" \
        '[ "$(sha "$GOAMNEZIA_DIR/.prev_code/files/install.sh")" = "$old_install_sum" ]'
    act lang-set --lang=ru
    expect "$v → $GA_VER: первый пишущий вызов нового кода (как тик сторожа) — success" success
    check "$v → $GA_VER: миграция прошла — версия, отметка с отпечатком, «Что нового» с $v, снимок preupgrade" \
        '[ "$(jq -r .version "$CFG")" = "$GA_VER" ] && [[ "$(cat "$GOAMNEZIA_DIR/.migrated_$GA_VER")" =~ ^[0-9a-f]{64}$ ]] && [ "$(cat "$GOAMNEZIA_DIR/.changelog_pending")" = "$v" ] && [ -f "$(head -n 1 "$GOAMNEZIA_DIR/.preupgrade_${GA_VER}_done")" ]' \
        "version=$(jq -r .version "$CFG") $(errs)"
    check "$v → $GA_VER: обновление завершено — копии прежнего кода и отметок неудачи нет" \
        '[ ! -e "$GOAMNEZIA_DIR/.prev_code" ] && [ ! -e "$GOAMNEZIA_DIR/.update_failed.json" ] && [ ! -e "$GOAMNEZIA_DIR/.migrating_$GA_VER" ]'
    check "$v → $GA_VER: ключи пользователей и сервера не тронуты" \
        '[ "$(peers_keys)" = "$keys0" ] && [ "$(sha "$GOAMNEZIA_DIR/state/amnezia-awg2/wireguard_server_private_key.key")" = "$srv0" ]'
    check "$v → $GA_VER: .install-source — ветка dev и новый коммит" \
        'grep -qx "branch=dev" "$GOAMNEZIA_DIR/.install-source" && grep -qx "ref=$SHA_NEW" "$GOAMNEZIA_DIR/.install-source"'
    check "$v → $GA_VER: юниты таймеров — от новой версии" \
        '[ -f "$GOAMNEZIA_SYSTEMD_DIR/goamnezia-watch.timer" ] && grep -q "install.sh --action=watch-tick" "$GOAMNEZIA_SYSTEMD_DIR/goamnezia-watch.service"'
    act status
    check "$v → $GA_VER: status — success, версия $GA_VER, update_rollback=null, людей 2" \
        '[ "$(jget .status)" = success ] && [ "$(jget .version)" = "$GA_VER" ] && [ "$(jget .update_rollback)" = null ] && [ "$(jget .users)" = 2 ]' "$last"
done

# Отметку неудачного обновления показывает код этой версии и новее (откат, скажем, с будущей 0.5.2 на эту).
# Стенд — последний из цепочек: на нём уже текущий код.
section "OPS-02: отметка неудачного обновления — в status (update_rollback) и в шапке меню"
notice_at() {
    jq -n --arg at "$1" --arg v "$GA_VER" '{at: $at, stage: "migration", reason: "перенос данных на новую версию оборвался с ошибкой",
        version: $v, failed_version: "9.9.9", code_restored: true, data_restored: true, archive: "/x.tar.gz"}' \
        >"$GOAMNEZIA_DIR/.update_failed.json"
}
notice_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
act status
check "status.update_rollback — отметка целиком (что не встало, этап, что вернулось)" \
    '[ "$(jget .update_rollback.failed_version)" = 9.9.9 ] && [ "$(jget .update_rollback.stage)" = migration ] && [ "$(jget .update_rollback.code_restored)" = true ]' "$last"
( no_tty "$BASH5" "$GOAMNEZIA_DIR/install.sh" </dev/null >"$OUT" 2>"$ERR"; exit $? ) 2>/dev/null
check "шапка главного меню: «Обновление до 9.9.9 не удалось (дата): причина — работает прежняя версия ${GA_VER}»" \
    'tr "\n" " " <"$OUT" | tr -s " " | grep -qF "Обновление до 9.9.9 не удалось ($(date -u +%d.%m.%Y)): перенос данных на новую версию оборвался с ошибкой — работает прежняя версия $GA_VER"' \
    "$(grep -F "9.9.9" "$OUT" "$ERR" | head -n 3)"
notice_at "2020-01-01T00:00:00Z"
( no_tty "$BASH5" "$GOAMNEZIA_DIR/install.sh" </dev/null >"$OUT" 2>"$ERR"; exit $? ) 2>/dev/null
check "отметке больше недели — в шапке её нет (в status остаётся)" '! grep -qF "9.9.9" "$OUT" "$ERR"'
rm -f "$GOAMNEZIA_DIR/.update_failed.json"

# Процесс нового кода ждал замка, а соседний (тик, бот) за это время откатил обновление: на диске уже прежний
# код. Мигрировать данные кодом из памяти нельзя — отказ и признак «отдать вызов коду с диска».
section "OPS-02: код на диске сменился, пока процесс ждал замка — без миграции, вызов уходит коду с диска"
stand rb-moved
seed_config 0.4.6
code_copy "$S/code"; export UPSTREAM_DIR="$S/code/lib/upstream"
sed '$d' "$S/code/install.sh" >"$S/code/install_nomain.sh"
moved=$("$BASH5" -c '
    set -uo pipefail
    source "$1/install_nomain.sh"
    sed -i.bak "s/^GOAMNEZIA_VERSION=\"[^\"]*\"/GOAMNEZIA_VERSION=\"9.9.9\"/" "$1/lib/common.sh"
    auto_migrate_state; echo "rc=$? moved=${GA_CODE_MOVED:-0}"' _ "$S/code" 2>/dev/null </dev/null | tail -n 1)
check "отказ (rc 1, GA_CODE_MOVED=1), версия в config.json прежняя, снимка и отметок нет" \
    '[ "$moved" = "rc=1 moved=1" ] && [ "$(jq -r .version "$CFG")" = 0.4.6 ] && [ "$(nbk)" = 0 ] && [ -z "$(upd_traces)" ] && [ ! -e "$GOAMNEZIA_DIR/.migrated_$GA_VER" ]' \
    "$moved version=$(jq -r .version "$CFG")"
unset UPSTREAM_DIR

section "OPS-02: миграция упала → откат кода, данных, юнитов и .install-source (мост, как бот или тик)"
stand rb-bridge
export GA_T_FLAGS="$S/flags"; mkdir -p "$GA_T_FLAGS"
if ! old_install 0.4.6 f6cd5d5; then
    bad "откат: 0.4.6 не встал" "$last $(errs)"
else
    before=$(rb_state)
    CAND="${GA_VER}-rbtest"
    candidate "$S/cand" "$CAND"; inject_steps "$S/cand/install.sh"
    touch "$GA_T_FLAGS/crash"
    new_bootstrap "$S/cand"
    check "кандидат $CAND разложен, 0.4.6 — рядом" '[ "$rc" = 0 ] && [ "$(prev_kv meta from)" = 0.4.6 ] && [ "$(prev_kv meta to)" = "$CAND" ]' \
        "rc=$rc $(tail -n 5 "$OUT")"
    act lang-set --lang=ru
    check "миграция кандидата упала на внедрённом шаге, шаги после него не шли" \
        'grep -qF "ga-test: the migration step crashes now" "$ERR" && [ ! -f "$GA_T_FLAGS/after_ran" ]' "$(errs)"
    expect "тот же вызов после отката ответил прежний код: success" success
    check "человеку сказано: что не встало, что вернули, какая версия работает" \
        'grep -qF "Обновление до $CAND не удалось" "$ERR" && grep -qF "Возвращаю прежнюю версию 0.4.6" "$ERR" && grep -qF "Настройки, ключи и пользователи — как до обновления" "$ERR" && grep -qF "Работает прежняя версия 0.4.6" "$ERR"' "$(errs)"
    check "код, config.json, users.json, state/, .install-source и юниты — ровно как до обновления" \
        '[ "$(rb_state)" = "$before" ]' "$(diff <(printf '%s\n' "$before") <(rb_state) | head -n 12)"
    check "на диске прежний код: install.sh --version → 0.4.6" \
        '[[ "$("$BASH5" "$GOAMNEZIA_DIR/install.sh" --version)" == *" 0.4.6" ]]'
    check "следов попытки нет: копии кода, отметок начала, снимка и миграции кандидата" '[ -z "$(upd_traces)" ]' "$(upd_traces)"
    check "отметка для status и шапки: версии, этап migration, код и данные вернулись, путь копии" \
        'jq -e --arg c "$CAND" ".version == \"0.4.6\" and .failed_version == \$c and .stage == \"migration\" and .code_restored == true and .data_restored == true" "$GOAMNEZIA_DIR/.update_failed.json" && [ -f "$(jq -r .archive "$GOAMNEZIA_DIR/.update_failed.json")" ]' \
        "$(cat "$GOAMNEZIA_DIR/.update_failed.json" 2>/dev/null)"
    ntf=$(cat "$GOAMNEZIA_NOTIFY_DIR"/*.json 2>/dev/null | jq -c 'select(.event == "update_rolled_back")' 2>/dev/null | tail -n 1)
    check "боту — событие update_rolled_back, фраза первым полем" \
        '[ "$(jq -r "keys_unsorted[2]" <<<"$ntf")" = message ] && [[ "$(jq -r .message <<<"$ntf")" == *"Возвращена прежняя версия 0.4.6"* ]] && [ "$(jq -r .failed_version <<<"$ntf")" = "$CAND" ]' "$ntf"
    check "в журнале действий — update-rollback" 'grep -qF "update-rollback" "$GOAMNEZIA_AUDIT_LOG"'
    act lang-set --lang=ru
    check "следующий вызов (тик) — прежний код, без миграции и без ошибок" \
        '[ "$(jget .status)" = success ] && ! grep -qF "Миграция состояния" "$ERR" && [ "$(rb_state)" = "$before" ]' "$last $(errs)"
fi

section "OPS-02: миграция упала в меню сразу после загрузчика → откат и меню прежней версии"
stand rb-menu
export GA_T_FLAGS="$S/flags"; mkdir -p "$GA_T_FLAGS"
if ! old_install 0.4.5 695bf6c; then
    bad "откат из меню: 0.4.5 не встал" "$last $(errs)"
else
    before_nounits=$(rb_state nounits)
    CAND="${GA_VER}-rbtest"
    candidate "$S/cand" "$CAND"; inject_steps "$S/cand/install.sh"
    touch "$GA_T_FLAGS/crash"
    new_bootstrap "$S/cand"
    # Как после загрузчика: install.sh без аргументов — меню; терминала нет, конец ввода — выход.
    ( no_tty "$BASH5" "$GOAMNEZIA_DIR/install.sh" </dev/null >"$OUT" 2>"$ERR"; exit $? ) 2>/dev/null; rc=$?
    check "меню нового кода: миграция упала, откат сказан, прежняя версия запущена" \
        'grep -qF "Обновление до $CAND не удалось" "$ERR" && grep -qF "Работает прежняя версия 0.4.5" "$ERR" && grep -qF "Запускаю прежнюю версию 0.4.5" "$ERR"' \
        "$(tail -n 12 "$ERR")"
    # Заставка и шапка меню 0.4.5 писали название с заглавной («goAmnezia Pro»).
    check "дальше — меню прежней версии 0.4.5, выход по концу ввода без ошибки" \
        '[ "$rc" = 0 ] && grep -qiF "goAmnezia pro v0.4.5  — " "$OUT"' "rc=$rc $(tail -n 6 "$OUT")"
    # Юниты тут не сверяем: меню прежней версии при запуске само ставит свои таймеры (_timers_apply).
    check "код, config.json, users.json, state/ и .install-source — как до обновления" \
        '[ "$(rb_state nounits)" = "$before_nounits" ]' \
        "$(diff <(printf '%s\n' "$before_nounits") <(rb_state nounits) | head -n 12)"
fi

section "OPS-02: новый код не отвечает на status после миграции → откат (этап check)"
stand rb-check
export GA_T_FLAGS="$S/flags"; mkdir -p "$GA_T_FLAGS"
if ! old_install 0.4.6 f6cd5d5; then
    bad "откат по проверке: 0.4.6 не встал" "$last $(errs)"
else
    before=$(rb_state)
    CAND="${GA_VER}-rbtest"
    candidate "$S/cand" "$CAND"; inject_status_crash "$S/cand/install.sh"
    touch "$GA_T_FLAGS/status_crash"
    new_bootstrap "$S/cand"
    act lang-set --lang=ru
    expect "status кандидата упал — откат, тот же вызов ответил прежний код" success
    check "отметка: этап check, причина «не ответила на проверку состояния», данные вернулись" \
        'jq -e ".stage == \"check\" and .data_restored == true and .code_restored == true and (.reason | test(\"проверку состояния\"))" "$GOAMNEZIA_DIR/.update_failed.json"' \
        "$(cat "$GOAMNEZIA_DIR/.update_failed.json" 2>/dev/null) $(errs)"
    check "код, данные, .install-source и юниты — как до обновления" \
        '[ "$(rb_state)" = "$before" ]' "$(diff <(printf '%s\n' "$before") <(rb_state) | head -n 12)"
fi

section "OPS-02: новая версия не запускается — загрузчик обновление не начинает"
stand rb-start
if ! old_install 0.4.6 f6cd5d5; then
    bad "проверка запуска: 0.4.6 не встал" "$last $(errs)"
else
    before=$(rb_state)
    candidate "$S/cand1" "${GA_VER}-broken"
    printf '\nga_t_broken() {\n' >>"$S/cand1/lib/users.sh"
    new_bootstrap "$S/cand1"
    check "синтаксическая ошибка в модуле: отказ до раскладки, названа причина, прежнее не тронуто" \
        '[ "$rc" != 0 ] && grep -qF "Новая версия не запускается:" "$OUT" && grep -qF "users.sh" "$OUT" && grep -qF "Обновление не начато" "$OUT" && [ "$(rb_state)" = "$before" ] && [ ! -e "$GOAMNEZIA_DIR/.prev_code" ]' \
        "rc=$rc $(tail -n 4 "$OUT")"
    candidate "$S/cand2" "${GA_VER}-broken2"
    python3 - "$S/cand2/lib/common.sh" <<'PY'
import sys
p = sys.argv[1]
s = open(p, encoding='utf-8').read()
s = s.replace('\nGOAMNEZIA_VERSION=', '\n: "${_ga_t_unset_at_start}"\nGOAMNEZIA_VERSION=', 1)
open(p, 'w', encoding='utf-8').write(s)
PY
    new_bootstrap "$S/cand2"
    check "install.sh --version новой версии падает (set -u при загрузке модулей): отказ, прежнее не тронуто" \
        '[ "$rc" != 0 ] && grep -qF "Новая версия не запускается:" "$OUT" && grep -qF "Обновление не начато" "$OUT" && [ "$(rb_state)" = "$before" ] && [ ! -e "$GOAMNEZIA_DIR/.prev_code" ]' \
        "rc=$rc $(tail -n 4 "$OUT")"
    # Незаконченное обновление: кандидат разложен, но новый код ещё не запускался (миграции не было), и
    # загрузчик запускают снова — копия прежнего кода остаётся та же (0.4.6), а не промежуточная.
    candidate "$S/cand3" "${GA_VER}-mid"
    new_bootstrap "$S/cand3"
    new_bootstrap "$ROOT"
    check "повторный загрузчик до миграции: копия прежней версии — та же 0.4.6, а не промежуточная" \
        '[ "$rc" = 0 ] && grep -qF "Сохранена копия версии до незаконченного обновления: 0.4.6" "$OUT" && [ "$(prev_kv meta from)" = 0.4.6 ] && [ "$(prev_kv meta to)" = "$GA_VER" ] && [ "$(sha "$GOAMNEZIA_DIR/.prev_code/files/install.sh")" = "$(sha "$S/old/install.sh")" ]' \
        "rc=$rc $(tail -n 5 "$OUT") $(cat "$GOAMNEZIA_DIR/.prev_code/meta" 2>/dev/null)"
    act lang-set --lang=ru
    check "затем миграция 0.4.6 → $GA_VER проходит и копия убирается" \
        '[ "$(jget .status)" = success ] && [ "$(jq -r .version "$CFG")" = "$GA_VER" ] && [ ! -e "$GOAMNEZIA_DIR/.prev_code" ] && [ "$(cat "$GOAMNEZIA_DIR/.changelog_pending")" = 0.4.6 ]' \
        "$last $(errs)"
fi

section "OPS-02: места на новые файлы не хватило — раскладка не начата"
if [ "$(id -u)" = 0 ]; then
    skipped "каталог только для чтения изображает нехватку места" "прогон от root: права каталога root не остановят"
else
    stand rb-space
    if ! old_install 0.4.6 f6cd5d5; then
        bad "раскладка: 0.4.6 не встал" "$last $(errs)"
    else
        before=$(rb_state)
        chmod 555 "$GOAMNEZIA_DIR/lib"
        new_bootstrap "$ROOT"
        chmod 755 "$GOAMNEZIA_DIR/lib"
        ga_new=$(find "$GOAMNEZIA_DIR" -name '*.ga-new' 2>/dev/null)
        check "файлы не легли: отказ, прежний код цел, копия и времянки убраны" \
            '[ "$rc" != 0 ] && grep -qF "Файлы новой версии не легли" "$OUT" && grep -qF "Обновление не начато" "$OUT" && [ "$(rb_state)" = "$before" ] && [ ! -e "$GOAMNEZIA_DIR/.prev_code" ] && [ -z "$ga_new" ]' \
            "rc=$rc $(tail -n 4 "$OUT") $ga_new"
    fi
fi
fi   # HAVE_GIT

# ══════════════════════════════════════════════════════════════════════════════
# OPS-05. Подхваченный контейнер после старта — сразу с правилом изоляции, не дожидаясь тика сторожа
# ══════════════════════════════════════════════════════════════════════════════
# Стенд: профиль awg1 подхвачен у приложения (контейнер amnezia-awg, wg0, imported — его start.sh мы не трогаем) и
# свой awg2. Контейнеры «работают» (каталоги-фикстуры), события Docker — из файла: старт amnezia-awg, чужого nginx
# и amnezia-awg2. Служба (install.sh --isolation-watch) сначала подписывается на события, потом догоняет уже
# работающие контейнеры, потом ставит правило каждому стартовавшему контейнеру профиля.
section "OPS-05: изоляция подхваченного контейнера сразу после его старта"
stand ops05
seed_config "$GA_VER"
export UPSTREAM_DIR="$ROOT/lib/upstream" GA_T_FLAGS="$S/flags"; mkdir -p "$GA_T_FLAGS"
jq '.profiles = {
      awg1: {installed_at: "2026-01-01T00:00:00Z", container: "amnezia-awg", iface: "wg0", imported: true,
             port: 34567, subnet: "10.8.1.0/24", enabled: true, line: "1.0"},
      awg2: {installed_at: "2026-01-01T00:00:00Z", container: "amnezia-awg2", port: 443, subnet: "10.8.2.0/24",
             enabled: true, line: "2.0"}}' "$CFG" >"$CFG.t" && mv "$CFG.t" "$CFG" && chmod 600 "$CFG"
export GOAMNEZIA_CONTAINER_FS_DIR="$S/cfs"
mkdir -p "$S/cfs/amnezia-awg" "$S/cfs/amnezia-awg2"
printf 'amnezia-awg\nnginx\namnezia-awg2\n' >"$S/events"
iso_rule() { printf "DRY: docker exec -i %s sh -c 'iptables -C FORWARD -i %s -o %s -j DROP 2>/dev/null || iptables -I FORWARD 1 -i %s -o %s -j DROP'" "$1" "$2" "$2" "$2" "$2"; }
iso_watch() {
    ( GOAMNEZIA_DOCKER_EVENTS_FIXTURE="$S/events" "$BASH5" "$APP" --isolation-watch </dev/null >"$OUT" 2>"$ERR"; exit $? ) 2>/dev/null
    rc=$?
}
iso_watch
n_awg=$(grep -cF "$(iso_rule amnezia-awg wg0)" "$ERR")
n_awg2=$(grep -cF "$(iso_rule amnezia-awg2 awg0)" "$ERR")
check "OPS-05 служба: подхваченному amnezia-awg (wg0) — правило при догоняющем проходе и по событию старта" \
    '[ "$rc" = 0 ] && [ "$n_awg" = 2 ]' "rc=$rc n=$n_awg $(grep -F "DRY: docker" "$ERR" | head -n 8)"
check "OPS-05 своему amnezia-awg2 (awg0) — так же; чужому контейнеру (nginx) — ничего" \
    '[ "$n_awg2" = 2 ] && ! grep -qF "docker exec -i nginx" "$ERR"' "n=$n_awg2 $(grep -F "DRY: docker exec" "$ERR" | head -n 6)"
check "OPS-05 сначала подписка на события, потом догоняющий проход (контейнер между ними не потеряется)" \
    '[ "$(grep -nF "DRY: docker events" "$ERR" | head -n 1 | cut -d: -f1)" -lt "$(grep -nF "DRY: docker exec" "$ERR" | head -n 1 | cut -d: -f1)" ]' \
    "$(grep -nF "DRY: docker" "$ERR" | head -n 4)"
check "OPS-05 start.sh подхваченного не трогается: в контейнер — только команды правил (изоляция и MSS)" \
    '[ "$(grep -F "amnezia-awg " "$ERR" | grep -vF "iptables -C FORWARD -i wg0 -o wg0 -j DROP" | grep -vF "docker inspect" | grep -vF "TCPMSS" | wc -l | tr -d " ")" = 0 ]' \
    "$(grep -F "amnezia-awg " "$ERR" | head -n 4)"
# Этап Б (08.10): подгонка TCP MSS контейнеру приложения — командой со старта, как изоляция (его start.sh не трогаем).
check "OPS-05 подхваченному amnezia-awg — правило MSS командой при догоняющем проходе и по событию старта" \
    '[ "$(grep -F "DRY: docker exec -i amnezia-awg sh -c " "$ERR" | grep -cF "TCPMSS")" = 2 ]' \
    "$(grep -F "amnezia-awg " "$ERR" | head -n 6)"
jq '.client_isolation = false' "$CFG" >"$CFG.t" && mv "$CFG.t" "$CFG" && chmod 600 "$CFG"
iso_watch
check "OPS-05 client_isolation=false — правил не ставит" '[ "$rc" = 0 ] && ! grep -qF "iptables -I FORWARD 1" "$ERR"' \
    "$(grep -F "DRY: docker exec" "$ERR" | head -n 3)"
jq 'del(.client_isolation)' "$CFG" >"$CFG.t" && mv "$CFG.t" "$CFG" && chmod 600 "$CFG"
# Юнит службы — вместе с таймерами продукта (timers_install): миграция 0.4.6 → текущая ставит его.
act lang-set --lang=ru
mig_reset 0.4.6
act lang-set --lang=ru
unit="$GOAMNEZIA_SYSTEMD_DIR/goamnezia-isolation.service"
check "OPS-05 юнит goamnezia-isolation.service: ExecStart --isolation-watch, после Docker и вслед за ним, Restart=on-failure" \
    'grep -qx "ExecStart=/bin/bash $GOAMNEZIA_DIR/install.sh --isolation-watch" "$unit" && grep -qx "After=docker.service" "$unit" && grep -qx "PartOf=docker.service" "$unit" && grep -qx "WantedBy=multi-user.target docker.service" "$unit" && grep -qx "Restart=on-failure" "$unit" && grep -qx "Type=simple" "$unit"' \
    "$(cat "$unit" 2>/dev/null)"
# enable и restart при записи юнита проверяет tests/test_timers_dryrun.sh (_timers_apply глушит вывод timers_install).
check "OPS-05 после смены кода (миграция) служба перезапускается — держит в памяти прежний код" \
    'grep -qF "DRY: systemctl try-restart goamnezia-isolation.service" "$ERR"' "$(grep -F "isolation" "$ERR" | head -n 5)"
check "OPS-05 правило ставит не только сторож: тик по-прежнему держит его (ga_amn_isolation_apply в amnezia_watch_tick)" \
    'grep -q "ga_amn_isolation_apply \"\$p\" quiet" "$ROOT/lib/amnezia.sh"'
unset GOAMNEZIA_CONTAINER_FS_DIR UPSTREAM_DIR

# ══════════════════════════════════════════════════════════════════════════════
# OPS-01. Место на диске: поле disk в status и строка в шапке главного меню SSH
# ══════════════════════════════════════════════════════════════════════════════
# df подменён: раздел 20000 МБ, свободно — сколько задано в DF_FREE, занято — остальное (Use% — с округлением
# вверх, как у настоящего df). Фикстура места прогона (GOAMNEZIA_DF_FIXTURE, TAIL-12) на эти вызовы снята.
section "OPS-01: место на диске — status.disk и шапка меню"
stand ops01
seed_config "$GA_VER"
DFBIN="$S/dfbin"; mkdir -p "$DFBIN"
cat >"$DFBIN/df" <<'EOS'
#!/bin/sh
free=${DF_FREE:-15000}
used=$((20000 - free))
pct=$(( (used * 100 + 19999) / 20000 ))
printf 'Filesystem 1M-blocks Used Available Capacity Mounted on\n/dev/vda1 20000 %s %s %s%% /\n' "$used" "$free" "$pct"
EOS
chmod +x "$DFBIN/df"
act lang-set --lang=ru
for _case in 15000:ok 800:low 100:critical; do
    free="${_case%%:*}"; want="${_case#*:}"
    DF_FREE="$free" GOAMNEZIA_DF_FIXTURE='' PATH="$DFBIN:$PATH" act status
    check "OPS-01 status.disk при $free МБ свободных из 20000: level=$want, занято %, свободно, пороги" \
        '[ "$(jget .disk.level)" = "$want" ] && [ "$(jget .disk.free_mb)" = "$free" ] && [ "$(jget .disk.total_mb)" = 20000 ] && [ "$(jget .disk.used_pct)" = "$(( ((20000 - free) * 100 + 19999) / 20000 ))" ] && [ "$(jget .disk.low_mb)" = 1024 ] && [ "$(jget .disk.critical_mb)" = 512 ]' \
        "$(jget .disk)"
done
# menu_header <свободно МБ> — меню без терминала (конец ввода — выход), его вывод одной строкой.
menu_header() {
    ( DF_FREE="$1" GOAMNEZIA_DF_FIXTURE='' PATH="$DFBIN:$PATH" no_tty "$BASH5" "$APP" </dev/null >"$OUT" 2>"$ERR"; exit $? ) 2>/dev/null
    tr '\n' ' ' <"$OUT" | tr -s ' '
}
hdr=$(menu_header 100)
check "OPS-01 шапка меню, диск почти полон: «Диск почти полон: занято 100%, свободно 100 МБ»" \
    'grep -qF "Диск почти полон: занято 100%, свободно 100 МБ" <<<"$hdr"' "$(grep -F "Диск" "$OUT" | head -n 3)"
hdr=$(menu_header 800)
check "OPS-01 шапка при 800 МБ свободных (порог «Диагностики» — 1024): «Диск заполнен на 96%, свободно 800 МБ»" \
    'grep -qF "Диск заполнен на 96%, свободно 800 МБ" <<<"$hdr"' "$(grep -F "Диск" "$OUT" | head -n 3)"
hdr=$(menu_header 15000)
check "OPS-01 места достаточно — строки о диске в шапке нет" '! grep -qF "Диск " <<<"$hdr"' "$(grep -F "Диск" "$OUT" | head -n 3)"
check "OPS-01 пороги — те же, что у «Диагностики» (1024 МБ — образ Amnezia) и плановой копии (512 МБ)" \
    'grep -q "^GA_DISK_LOW_MB=1024$" "$ROOT/lib/common.sh" && grep -q "^GA_AMN_MIN_DISK_MB=1024$" "$ROOT/lib/amnezia.sh" && grep -q "^GA_DISK_CRITICAL_MB=512$" "$ROOT/lib/common.sh" && grep -q "^GA_BACKUP_MIN_DISK_MB=512$" "$ROOT/lib/backup.sh" && grep -q "if \[ \"\$v\" -lt 1024 \]" "$ROOT/install.sh"'

# TAIL-19: о заполняющемся диске бот узнаёт уведомлением — событие disk_level в очереди, но только при СМЕНЕ
# уровня (сторож, watch-tick раз в 2 минуты): ok → low → critical и обратно в ok, без повтора на каждом тике;
# первый тик на здоровом диске молчит. Фикстура места прогона (GOAMNEZIA_DF_FIXTURE, TAIL-12) на эти вызовы снята.
section "TAIL-19: событие disk_level — только при смене уровня"
disk_events() { cat "$GOAMNEZIA_NOTIFY_DIR"/*.json 2>/dev/null | jq -r 'select(.event == "disk_level") | "\(.prev)>\(.level):\(.free_mb)"' 2>/dev/null | paste -sd' ' -; }
rm -f "$GOAMNEZIA_LOCK_DIR/disk_level" "$GOAMNEZIA_NOTIFY_DIR"/*.json 2>/dev/null
ticks=""
for free in 15000 15000 800 800 100 100 15000 15000; do
    DF_FREE="$free" GOAMNEZIA_DF_FIXTURE='' PATH="$DFBIN:$PATH" act watch-tick
    ticks="$ticks $(jget '.disk | "\(.level)/\(.notified)"')"
done
check "TAIL-19 восемь тиков (15000, 15000, 800, 800, 100, 100, 15000, 15000 МБ): событий ровно три — ok>low, low>critical, critical>ok" \
    '[ "$(disk_events)" = "ok>low:800 low>critical:100 critical>ok:15000" ]' "events=[$(disk_events)] ticks=[$ticks] $(errs)"
check "TAIL-19 ответ watch-tick несёт блок disk: уровень и было ли событие на этом тике" \
    '[ "$ticks" = " ok/false ok/false low/true low/false critical/true critical/false ok/true ok/false" ]' "ticks=[$ticks]"
check "TAIL-19 событие — с тем, что нужно боту: сколько свободно и занято, раздел и пороги" \
    '[ "$(cat "$GOAMNEZIA_NOTIFY_DIR"/*.json | jq -r "select(.event == \"disk_level\" and .level == \"critical\") | [.free_mb, .total_mb, .used_pct, .low_mb, .critical_mb, (.path != \"\")] | map(tostring) | join(\" \")")" = "100 20000 100 1024 512 true" ]' \
    "$(cat "$GOAMNEZIA_NOTIFY_DIR"/*.json 2>/dev/null | head -c 600)"
# После перезагрузки (памяти в /run нет) неснятая тревога напоминает о себе один раз
rm -f "$GOAMNEZIA_LOCK_DIR/disk_level" "$GOAMNEZIA_NOTIFY_DIR"/*.json 2>/dev/null
DF_FREE=800 GOAMNEZIA_DF_FIXTURE='' PATH="$DFBIN:$PATH" act watch-tick
DF_FREE=800 GOAMNEZIA_DF_FIXTURE='' PATH="$DFBIN:$PATH" act watch-tick
check "TAIL-19 памяти нет, а места мало — одно событие ok>low, следующий тик молчит" \
    '[ "$(disk_events)" = "ok>low:800" ]' "events=[$(disk_events)]"

# ══════════════════════════════════════════════════════════════════════════════
DONE=1
printf '\n══ ИТОГ: пройдено %d, провалено %d, пропущено %d\n' "$pass" "$fail" "$skip"
[ "$fail" -eq 0 ]
