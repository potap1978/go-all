#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
# goAmnezia pro — сквозной прогон lib/upstream.sh без сети и без Docker
# (GOAMNEZIA_DRY_RUN=1, фикстуры GitHub / Docker Hub в tests/fixtures/upstream*).
#
# Запуск: ${BASH5:-/opt/homebrew/bin/bash} tests/test_upstream_dryrun.sh
# Нужны: bash ≥ 5, jq, python3. Сеть не трогается: ga_up_fetch читает
# сохранённые ответы из GOAMNEZIA_UPSTREAM_FIXTURE_DIR.
#
# Что проверяем (PLAN §6.8, CONTRACT §4.9):
#   патч start.sh воспроизводит наш awg/start.sh из start.sh.upstream байт в байт →
#   без фикстур (нет сети) → код unreachable, stdout пуст → фикстура «есть
#   обновление»: первый не-draft релиз 5.0.3.0 (draft пропущен), новейший
#   версионный тег образа 3.2.20260915 (latest — только справка), изменившиеся
#   скрипты awg, новый плейсхолдер CONTENT_PADDING_MAX помечен «не поддержан»,
#   профили awg2/awg3 с update=true → частичная недоступность (только GitHub /
#   только Docker Hub) → warning, не ошибка → upstream_update awg2: бэкап, previous/,
#   скрипты 5.0.3.0 в вендоренном каталоге, start.sh пересобран патчем, pull по
#   новому дайджесту, контейнер пересоздан из state/ с прежним портом, ключи
#   сервера и пользователей не изменились, config.json previous, images.json →
#   повтор → already_current → rollback: прежний образ и скрипты, previous
#   снят → повтор → no_previous → явный тег со второй страницы Hub (отдельный
#   запрос) → неизвестный тег → tag_not_found → провал пересоздания → откат
#   образа и скриптов, config без изменений → upstream_tick: last_check.json,
#   событие upstream_available один раз → core-update как ремонт →
#   фикстура «всё актуально» → секретов нет ни в stdout, ни в stderr →
#   паритет словаря.
# Ничего вне $TMP не пишем. Захват — через run (stdout → файл), не $(…).
# ══════════════════════════════════════════════════════════════════════════════
set -uo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
TMP=$(mktemp -d "${TMPDIR:-/tmp}/goamnezia-upstream.XXXXXX") || exit 2
DONE=""
ERR="$TMP/stderr.log"
OUT="$TMP/stdout.log"
ALL_ERR="$TMP/all-stderr.log"
trap 'if [ -z "$DONE" ]; then printf "\n!! аварийный выход, последний stderr:\n"; tail -n 20 "$ERR" 2>/dev/null; fi; rm -rf "$TMP"' EXIT

export GOAMNEZIA_DRY_RUN=1
export GOAMNEZIA_DIR="$TMP/goamnezia"
export GOAMNEZIA_LOCK_DIR="$TMP/run"
export GOAMNEZIA_LOG_FILE="$TMP/goamnezia.log"
export GOAMNEZIA_AUDIT_LOG="$TMP/audit.log"
export GOAMNEZIA_NOTIFY_DIR="$TMP/run/notify"
export GOAMNEZIA_BUILD_ROOT="$TMP/build"
export GOAMNEZIA_SERVER_IP="203.0.113.10"
export UPSTREAM_DIR="$TMP/upstream"
export LIB_DIR="$ROOT/lib"
unset GOAMNEZIA_UPSTREAM_FIXTURE_DIR
mkdir -p "$GOAMNEZIA_DIR" "$GOAMNEZIA_LOCK_DIR" "$UPSTREAM_DIR"
cp -R "$ROOT/lib/upstream/." "$UPSTREAM_DIR/"
FX="$ROOT/tests/fixtures/upstream"
FX_CUR="$ROOT/tests/fixtures/upstream_current"

# Временные файлы с ключами — в каталоге стенда (L1-19). Без переменной продукт кладёт их в /dev/shm,
# а он есть только на Linux: там они жили бы на машине прогона, и оборванный прогон оставлял бы ключи.
export GOAMNEZIA_SECRET_TMPDIR="$TMP/shm"; mkdir -p "$GOAMNEZIA_SECRET_TMPDIR"

pass=0; fail=0; skip=0; rc=0; out=""
ok()   { pass=$((pass + 1)); printf '  ✓ %s\n' "$1"; }
bad()  { fail=$((fail + 1)); printf '  ✗ %s\n' "$1"; [ -n "${2:-}" ] && printf '      %s\n' "$2" | head -c 700; return 0; }
skipped() { skip=$((skip + 1)); printf '  – %s (пропущено: %s)\n' "$1" "$2"; }
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
    cat "$ERR" >>"$ALL_ERR"
    return 0
}
errs() { cat "$ERR" 2>/dev/null; }
jget() { jq -r "$1" <<<"$out" 2>/dev/null; }
sha() { if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'; else shasum -a 256 "$1" | awk '{print $1}'; fi; }

# ── Окружение ────────────────────────────────────────────────────────────────
section "окружение"
# shellcheck disable=SC1091
source "$ROOT/lib/common.sh"
# shellcheck disable=SC1091
source "$ROOT/lib/i18n.sh"
load_language ru >/dev/null 2>&1
for _m in docker amnezia users export backup upstream; do
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
run amnezia_profile_install awg3 40001 default
check "профиль awg3 установлен (dry)" '[ "$rc" = 0 ]' "$(errs)"
run users_add anna "Анна"
check "пользователь anna создан в awg2 и awg3" '[ "$rc" = 0 ] && [ "$(jq -r ".users[0].peers | keys | join(\",\")" "$USERS_FILE")" = "awg2,awg3" ]' "$(errs)"
SRV_PRIV=$(cat "$STATE_DIR/amnezia-awg2/wireguard_server_private_key.key")
ANNA_PRIV=$(jq -r '.users[0].peers.awg2.privkey' "$USERS_FILE")
CONF_SHA_BEFORE=$(sha "$STATE_DIR/amnezia-awg2/awg0.conf")

# ── 1. Патч start.sh воспроизводит наш файл ─────────────────────────────────
section "патч start.sh (идемпотентные iptables)"
run ga_up_patch_start_sh "$ROOT/lib/upstream/awg/start.sh.upstream"
check "ga_up_patch_start_sh(start.sh.upstream) == lib/upstream/awg/start.sh байт в байт" \
    '[ "$rc" = 0 ] && cmp -s "$OUT" "$ROOT/lib/upstream/awg/start.sh"' "$(diff "$OUT" "$ROOT/lib/upstream/awg/start.sh" | head -5)"
check "в патче каждое -A предварено -C … 2>/dev/null ||" \
    '! grep -E "^[[:space:]]*iptables( -t nat)? -A" "$OUT" | grep -qv "2>/dev/null ||"'

# ── 2. Без сети ──────────────────────────────────────────────────────────────
section "сеть недоступна (dry-run без фикстур)"
run upstream_check
check "upstream_check → rc≠0, код unreachable, stdout пуст" '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = unreachable ] && [ -z "$out" ]' "rc=$rc code=$GA_ERR_CODE out=$out"
check "stderr: совет, DRY curl без реального вызова" 'grep -q "DRY: curl" "$ERR" && grep -q "GitHub" "$ERR"' "$(errs)"
run ga_up_profiles_current
check "ga_up_profiles_current без сети: два профиля с текущим тегом 3.1.20260828, rollback_available=false" \
    'is_json "$out" && [ "$(jget "length")" = 2 ] && [ "$(jget ".[0].current_tag")" = 3.1.20260828 ] && [ "$(jget "[.[].rollback_available] | any")" = false ]' "$out"
run upstream_update awg2
check "upstream_update без сети → unreachable, ничего не изменено" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = unreachable ] && [ "$(jq -r .profiles.awg2.image_tag "$CONFIG_FILE")" = 3.1.20260828 ]' "rc=$rc code=$GA_ERR_CODE"
run upstream_summary
check "upstream_summary до первой суточной проверки — null (L-07)" '[ "$rc" = 0 ] && [ "$out" = null ]' "rc=$rc out=$out"

# ── 3. Есть обновление ───────────────────────────────────────────────────────
section "upstream_check: фикстура «есть обновление»"
export GOAMNEZIA_UPSTREAM_FIXTURE_DIR="$FX"
run upstream_check
check "upstream_check: rc=0, stdout — один JSON" '[ "$rc" = 0 ] && is_json "$out" && [ "$(wc -l <"$OUT" | tr -d " ")" = 1 ]' "rc=$rc $(errs | tail -3)"
check "sources: github=ok, dockerhub=ok; updates_available=true; hint про кнопку" \
    '[ "$(jget .sources.github)" = ok ] && [ "$(jget .sources.dockerhub)" = ok ] && [ "$(jget .updates_available)" = true ] && grep -q "upstream-update" <<<"$(jget .hint)"' "$out"
check "релиз клиента: первый не-draft = 5.0.3.0 (draft 5.0.4.0 пропущен), current 5.0.2.1, update=true, prerelease=true" \
    '[ "$(jget .client_release.tag)" = 5.0.3.0 ] && [ "$(jget .client_release.current)" = 5.0.2.1 ] && [ "$(jget .client_release.update)" = true ] && [ "$(jget .client_release.prerelease)" = true ]' "$(jget .client_release)"
check "образ amneziawg-go: pinned 3.1.20260828 → latest_tag 3.2.20260915 (не «latest»), дайджесты sha256, update=true, alias latest указывает на новый" \
    '[ "$(jget ".images[\"amneziawg-go\"].pinned_tag")" = 3.1.20260828 ] && [ "$(jget ".images[\"amneziawg-go\"].latest_tag")" = 3.2.20260915 ] && [[ "$(jget ".images[\"amneziawg-go\"].latest_digest")" =~ ^sha256:[0-9a-f]{64}$ ]] && [ "$(jget ".images[\"amneziawg-go\"].update")" = true ] && [ "$(jget ".images[\"amneziawg-go\"].alias_latest_digest")" = "$(jget ".images[\"amneziawg-go\"].latest_digest")" ]' "$(jget ".images")"
check "образ amnezia-wg не проверяется — им не пользуется ни один профиль" '[ "$(jget ".images | has(\"amnezia-wg\")")" = false ]' "$(jget ".images|keys")"
check "скрипты awg по тегу 5.0.3.0: changed = Dockerfile, configure_container.sh, template.conf; start.sh и run_container.sh unchanged; missing пуст" \
    '[ "$(jget ".scripts.awg.tag")" = 5.0.3.0 ] && [ "$(jget ".scripts.awg.changed | join(\",\")")" = "Dockerfile,configure_container.sh,template.conf" ] && [ "$(jget ".scripts.awg.unchanged | join(\",\")")" = "run_container.sh,start.sh" ] && [ "$(jget ".scripts.awg.missing|length")" = 0 ]' "$(jget .scripts)"
check "в отчёте только каталог awg — другого upstream у продукта нет" '[ "$(jget ".scripts|keys|join(\",\")")" = awg ]' "$(jget .scripts)"
check "плейсхолдеры: новый CONTENT_PADDING_MAX в awg/template.conf и awg/configure_container.sh, удалённых нет" \
    '[ "$(jget ".placeholders[\"awg/template.conf\"].new | join(\",\")")" = CONTENT_PADDING_MAX ] && [ "$(jget ".placeholders[\"awg/configure_container.sh\"].new | join(\",\")")" = CONTENT_PADDING_MAX ] && [ "$(jget ".placeholders[\"awg/template.conf\"].removed|length")" = 0 ]' "$(jget .placeholders)"
check "unsupported: две записи {file, name} с CONTENT_PADDING_MAX; предупреждение «не поддержан» в stderr" \
    '[ "$(jget ".unsupported|length")" = 2 ] && [ "$(jget "[.unsupported[].name] | unique | join(\",\")")" = CONTENT_PADDING_MAX ] && grep -q "CONTENT_PADDING_MAX" "$ERR"' "$(jget .unsupported)"
check "профили: awg2 и awg3 — current 3.1.20260828 → available 3.2.20260915, update=true, rollback_available=false" \
    '[ "$(jget ".profiles|length")" = 2 ] && [ "$(jget "[.profiles[] | .update] | all")" = true ] && [ "$(jget "[.profiles[] | .available_tag] | unique | join(\",\")")" = 3.2.20260915 ] && [ "$(jget "[.profiles[] | .rollback_available] | any")" = false ]' "$(jget .profiles)"
check "проектный lib/upstream не тронут (репозиторий — только чтение)" \
    '[ "$(jq -r .client_release "$ROOT/lib/upstream/images.json")" = 5.0.2.1 ] && ! grep -q CONTENT_PADDING_MAX "$ROOT/lib/upstream/awg/template.conf"'

# ── 4. Частичная недоступность ───────────────────────────────────────────────
section "недоступен один источник"
mkdir -p "$TMP/fx_hub_only" "$TMP/fx_gh_only/raw"
cp "$FX"/hub_*.json "$TMP/fx_hub_only/"
cp "$FX/releases.json" "$TMP/fx_gh_only/"; cp -R "$FX/raw/." "$TMP/fx_gh_only/raw/"
GOAMNEZIA_UPSTREAM_FIXTURE_DIR="$TMP/fx_hub_only" run upstream_check
check "только Docker Hub: success, sources.github=unreachable, warning, client_release=null, образы сравнены" \
    '[ "$rc" = 0 ] && [ "$(jget .sources.github)" = unreachable ] && [ "$(jget .sources.dockerhub)" = ok ] && [ -n "$(jget .warning)" ] && [ "$(jget .client_release)" = null ] && [ "$(jget ".images[\"amneziawg-go\"].update")" = true ]' "rc=$rc $(jget "{sources, warning}")"
GOAMNEZIA_UPSTREAM_FIXTURE_DIR="$TMP/fx_gh_only" run upstream_check
check "только GitHub: success, sources.dockerhub=unreachable, релиз и скрипты сравнены, у профилей update=false" \
    '[ "$rc" = 0 ] && [ "$(jget .sources.dockerhub)" = unreachable ] && [ "$(jget .client_release.tag)" = 5.0.3.0 ] && [ "$(jget ".scripts.awg.changed|length")" = 3 ] && [ "$(jget "[.profiles[] | .update] | any")" = false ] && [ "$(jget ".images[\"amneziawg-go\"].reachable")" = false ]' "rc=$rc $(jget "{sources, images}")"
GOAMNEZIA_UPSTREAM_FIXTURE_DIR="$TMP/fx_gh_only" run upstream_update awg2
check "upstream_update без Docker Hub → unreachable (образ обязателен)" '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = unreachable ]' "rc=$rc code=$GA_ERR_CODE"

# ── 5. Обновление awg2 ───────────────────────────────────────────────────────
section "upstream_update awg2: бэкап → скрипты → образ → пересоздание из state/"
NEW_DIGEST=$(jq -r '.results[] | select(.name == "3.2.20260915") | .digest' "$FX/hub_amneziawg-go_tags.json")
run upstream_update awg2
check "upstream_update awg2: rc=0, один JSON в stdout" '[ "$rc" = 0 ] && is_json "$out" && [ "$(wc -l <"$OUT" | tr -d " ")" = 1 ]' "rc=$rc code=$GA_ERR_CODE $(errs | tail -4)"
check "ответ: image_tag 3.2.20260915, image_digest новый, previous_tag 3.1.20260828, client_release 5.0.3.0, scripts.changed 3, unsupported 2, backup указан, rolled_back=false" \
    '[ "$(jget .image_tag)" = 3.2.20260915 ] && [ "$(jget .image_digest)" = "$NEW_DIGEST" ] && [ "$(jget .previous_tag)" = 3.1.20260828 ] && [ "$(jget .client_release)" = 5.0.3.0 ] && [ "$(jget ".scripts.changed|length")" = 3 ] && [ "$(jget ".scripts.unsupported|length")" = 2 ] && [[ "$(jget .backup)" == goamnezia_backup_*.tar.gz ]] && [ "$(jget .rolled_back)" = false ]' "$out"
check "порядок в stderr: бэкап → скрипты → DRY pull по новому дайджесту → пересоздание (rm, run с тем же портом 40000, cp конфига/ключей, awg show)" \
    'grep -q "DRY: docker pull amneziavpn/amneziawg-go@$NEW_DIGEST" "$ERR" && grep -q "DRY: docker stop amnezia-awg2 && docker rm -fv amnezia-awg2" "$ERR" && grep -q -- "-p 40000:40000/udp" "$ERR" && grep -q "docker cp <stdin> amnezia-awg2:/opt/amnezia/awg/wireguard_server_private_key.key" "$ERR" && grep -q "awg show awg0" "$ERR"' "$(grep DRY "$ERR" | head -8)"
check "бэкап создан в backups/ (0600) до пересоздания" '[ -f "$BACKUP_DIR/$(jget .backup)" ] && [ "$(perm "$BACKUP_DIR/$(jget .backup)")" = 600 ]'
check "config.json: awg2 image_tag/digest новые, previous{image_tag 3.1.20260828, digest, client_release 5.0.2.1}, core_updated_at; awg3 не тронут" \
    '[ "$(jq -r .profiles.awg2.image_tag "$CONFIG_FILE")" = 3.2.20260915 ] && [ "$(jq -r .profiles.awg2.image_digest "$CONFIG_FILE")" = "$NEW_DIGEST" ] && [ "$(jq -r .profiles.awg2.previous.image_tag "$CONFIG_FILE")" = 3.1.20260828 ] && [ "$(jq -r .profiles.awg2.previous.client_release "$CONFIG_FILE")" = 5.0.2.1 ] && [ -n "$(jq -r .profiles.awg2.core_updated_at "$CONFIG_FILE")" ] && [ "$(jq -r .profiles.awg3.image_tag "$CONFIG_FILE")" = 3.1.20260828 ]' "$(jq -c .profiles.awg2 "$CONFIG_FILE")"
check "ключи не тронуты: приватный ключ сервера в state/, ключ Анны в users.json, awg0.conf в state/ тот же" \
    '[ "$(cat "$STATE_DIR/amnezia-awg2/wireguard_server_private_key.key")" = "$SRV_PRIV" ] && [ "$(jq -r ".users[0].peers.awg2.privkey" "$USERS_FILE")" = "$ANNA_PRIV" ] && [ "$(sha "$STATE_DIR/amnezia-awg2/awg0.conf")" = "$CONF_SHA_BEFORE" ]'
check "images.json (рабочий): amneziawg-go tag 3.2.20260915, digest новый, amd64_digest, checked сегодня, client_release 5.0.3.0" \
    '[ "$(jq -r ".[\"amneziawg-go\"].tag" "$UPSTREAM_DIR/images.json")" = 3.2.20260915 ] && [ "$(jq -r ".[\"amneziawg-go\"].digest" "$UPSTREAM_DIR/images.json")" = "$NEW_DIGEST" ] && [[ "$(jq -r ".[\"amneziawg-go\"].amd64_digest" "$UPSTREAM_DIR/images.json")" =~ ^sha256: ]] && [ "$(jq -r .client_release "$UPSTREAM_DIR/images.json")" = 5.0.3.0 ]' "$(jq -c . "$UPSTREAM_DIR/images.json")"
check "скрипты: awg/5.0.3.0/ — скачанные файлы как есть; вендоренный template.conf с новым плейсхолдером; Dockerfile новый" \
    '[ -f "$UPSTREAM_DIR/awg/5.0.3.0/template.conf" ] && grep -q CONTENT_PADDING_MAX "$UPSTREAM_DIR/awg/template.conf" && grep -q "dumb-init iptables" "$UPSTREAM_DIR/awg/Dockerfile"'
check "start.sh пересобран патчем из нового upstream (совпал с прежним: upstream start.sh не менялся), start.sh.upstream дословный" \
    'cmp -s "$UPSTREAM_DIR/awg/start.sh" "$ROOT/lib/upstream/awg/start.sh" && cmp -s "$UPSTREAM_DIR/awg/start.sh.upstream" "$FX/raw/5.0.3.0/awg/start.sh"'
check "previous/: прежние файлы (template.conf без нового плейсхолдера) и previous.json с client_release 5.0.2.1" \
    '[ -f "$UPSTREAM_DIR/awg/previous/template.conf" ] && ! grep -q CONTENT_PADDING_MAX "$UPSTREAM_DIR/awg/previous/template.conf" && [ "$(jq -r .client_release "$UPSTREAM_DIR/awg/previous/previous.json")" = 5.0.2.1 ]'
check "аудит: upstream-update awg2 3.1.20260828->3.2.20260915" 'audit_has upstream-update "awg2 3.1.20260828->3.2.20260915"' "$(tail -2 "$GOAMNEZIA_AUDIT_LOG")"
run upstream_update awg2
check "повтор upstream_update awg2 → already_current" '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = already_current ]' "rc=$rc code=$GA_ERR_CODE"
run upstream_check
check "upstream_check после обновления: awg2 update=false, rollback_available=true, previous_tag 3.1.20260828; awg3 всё ещё update=true; скрипты по 5.0.3.0 уже совпадают (checked=false)" \
    '[ "$(jget ".profiles[] | select(.profile == \"awg2\") | .update")" = false ] && [ "$(jget ".profiles[] | select(.profile == \"awg2\") | .rollback_available")" = true ] && [ "$(jget ".profiles[] | select(.profile == \"awg2\") | .previous_tag")" = 3.1.20260828 ] && [ "$(jget ".profiles[] | select(.profile == \"awg3\") | .update")" = true ] && [ "$(jget .scripts.awg.checked)" = false ] && [ "$(jget .client_release.update)" = false ]' "$(jget "{profiles, scripts, client_release}")"

# ── 6. Откат ─────────────────────────────────────────────────────────────────
section "upstream_rollback awg2"
OLD_DIGEST="sha256:cbafc02b8373a83f428272db6d8001b37bc02e6211cbd8c0cb4e2e3759b12b72"
# Дефект 1a.9: образ тянем ДО подмены вендоренных скриптов. Обычный отказ
# загрузки (нет сети, исчерпан предел Docker Hub) не имеет права оставить
# скрипты старыми, канонический start.sh удалённым, а images.json — с
# описаниями новой версии: следующая установка собрала бы контейнер старыми
# скриптами под новый образ.
STATE_START="$STATE_DIR/amnezia-awg2/start.sh"
printf '#!/bin/sh\n# state start.sh\n' >"$STATE_START"
docker_pull_ref() { ga_err pull_failed "stend: нет сети"; return 1; }
run upstream_rollback awg2
check "откат при отказе загрузки образа: rc≠0, вендоренные скрипты остались новыми, state/start.sh на месте, images.json не тронут" \
    '[ "$rc" != 0 ] && grep -q CONTENT_PADDING_MAX "$UPSTREAM_DIR/awg/template.conf" && [ -f "$STATE_START" ] && [ "$(jq -r .client_release "$UPSTREAM_DIR/images.json")" = 5.0.3.0 ] && [ "$(jq -r .profiles.awg2.image_tag "$CONFIG_FILE")" = 3.2.20260915 ]' \
    "rc=$rc code=$GA_ERR_CODE $(errs | tail -3)"
# shellcheck disable=SC1091
source "$ROOT/lib/docker.sh"
run upstream_rollback awg2
check "upstream_rollback awg2: rc=0; image_tag 3.1.20260828, rolled_back_from_tag 3.2.20260915, scripts_restored=true" \
    '[ "$rc" = 0 ] && is_json "$out" && [ "$(jget .image_tag)" = 3.1.20260828 ] && [ "$(jget .rolled_back_from_tag)" = 3.2.20260915 ] && [ "$(jget .scripts_restored)" = true ]' "rc=$rc $(errs | tail -3)"
check "DRY pull прежнего дайджеста и пересоздание" 'grep -q "DRY: docker pull amneziavpn/amneziawg-go@$OLD_DIGEST" "$ERR" && grep -q "DRY: docker stop amnezia-awg2" "$ERR"'
check "config.json: awg2 назад на 3.1.20260828, previous=null; images.json tag 3.1.20260828, client_release 5.0.2.1" \
    '[ "$(jq -r .profiles.awg2.image_tag "$CONFIG_FILE")" = 3.1.20260828 ] && [ "$(jq -r .profiles.awg2.image_digest "$CONFIG_FILE")" = "$OLD_DIGEST" ] && [ "$(jq -c .profiles.awg2.previous "$CONFIG_FILE")" = null ] && [ "$(jq -r ".[\"amneziawg-go\"].tag" "$UPSTREAM_DIR/images.json")" = 3.1.20260828 ] && [ "$(jq -r .client_release "$UPSTREAM_DIR/images.json")" = 5.0.2.1 ]' "$(jq -c "{p: .profiles.awg2.previous, t: .profiles.awg2.image_tag}" "$CONFIG_FILE")"
check "скрипты возвращены: template.conf без нового плейсхолдера, Dockerfile прежний, start.sh наш" \
    '! grep -q CONTENT_PADDING_MAX "$UPSTREAM_DIR/awg/template.conf" && cmp -s "$UPSTREAM_DIR/awg/Dockerfile" "$ROOT/lib/upstream/awg/Dockerfile" && cmp -s "$UPSTREAM_DIR/awg/start.sh" "$ROOT/lib/upstream/awg/start.sh"'
check "ключи после отката прежние" '[ "$(cat "$STATE_DIR/amnezia-awg2/wireguard_server_private_key.key")" = "$SRV_PRIV" ] && [ "$(jq -r ".users[0].peers.awg2.privkey" "$USERS_FILE")" = "$ANNA_PRIV" ]'
# Регрессия стенда: у профиля, подхваченного из приложения (import-adopt),
# «предыдущий» образ — latest без дайджеста. Откат такого профиля записывал
# пустой дайджест в images.json и распинывал образ для ВСЕХ будущих установок:
# profile-install начинал тянуть :latest по тегу.
PINNED_TAG=$(jq -r '."amneziawg-go".tag' "$UPSTREAM_DIR/images.json")
PINNED_DIG=$(jq -r '."amneziawg-go".digest' "$UPSTREAM_DIR/images.json")
ga_config_merge "$(jq -cn '{profiles: {awg2: {previous: {image_tag: "latest", image_digest: "", client_release: "5.0.2.1"}}}}')" >/dev/null
run upstream_rollback awg2
check "откат на образ приложения (latest без дайджеста): rc=0" '[ "$rc" = 0 ]' "rc=$rc $(errs | tail -3)"
check "images.json остался закреплённым по дайджесту (откат не распинал образ)" \
    '[ "$(jq -r ".[\"amneziawg-go\"].tag" "$UPSTREAM_DIR/images.json")" = "$PINNED_TAG" ] && [ "$(jq -r ".[\"amneziawg-go\"].digest" "$UPSTREAM_DIR/images.json")" = "$PINNED_DIG" ]' \
    "$(jq -c ".[\"amneziawg-go\"]" "$UPSTREAM_DIR/images.json")"

# Возвращаем профиль на закреплённый образ: дальше идут проверки core-update,
# которым нужен исходный тег и дайджест.
ga_config_merge "$(jq -cn --arg t "$PINNED_TAG" --arg d "$PINNED_DIG" \
    '{profiles: {awg2: {image_tag: $t, image_digest: $d}}}')" >/dev/null

run upstream_rollback awg2
check "повторный откат → no_previous" '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = no_previous ]' "rc=$rc code=$GA_ERR_CODE"
run upstream_rollback awg1
check "откат неустановленного профиля → profile_not_installed" '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = profile_not_installed ]' "rc=$rc code=$GA_ERR_CODE"

# ── 7. Явный тег ─────────────────────────────────────────────────────────────
section "upstream_update с явным тегом"
run upstream_update awg3 3.0.20260710
check "тег 3.0.20260710 не на первой странице списка → отдельный запрос /tags/<тег>; awg3 → 3.0.20260710, previous 3.1.20260828" \
    '[ "$rc" = 0 ] && [ "$(jget .image_tag)" = 3.0.20260710 ] && [ "$(jget .previous_tag)" = 3.1.20260828 ] && [ "$(jq -r .profiles.awg3.image_tag "$CONFIG_FILE")" = 3.0.20260710 ]' "rc=$rc code=$GA_ERR_CODE $(errs | tail -3)"
run upstream_update awg3 9.9.9
check "неизвестный тег → tag_not_found" '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = tag_not_found ]' "rc=$rc code=$GA_ERR_CODE"
run upstream_update awg3 "bad tag"
check "тег с пробелом → bad_arg" '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = bad_arg ]' "rc=$rc code=$GA_ERR_CODE"
run upstream_rollback awg3
check "откат awg3 после явного тега → 3.1.20260828" '[ "$rc" = 0 ] && [ "$(jq -r .profiles.awg3.image_tag "$CONFIG_FILE")" = 3.1.20260828 ]' "rc=$rc code=$GA_ERR_CODE"

# ── 8. Провал пересоздания → откат ───────────────────────────────────────────
section "провал проверки после пересоздания → откат образа и скриптов"
# Возвращаем images.json к исходному: явный тег для awg3 переставил закреплённый
# образ, а сценарий провала должен начинаться «как с сервера до обновления».
cp "$ROOT/lib/upstream/images.json" "$UPSTREAM_DIR/images.json"
# Подставной docker run: роняем ровно столько первых вызовов, сколько сказано в
# GA_STUB_RUN_FAILS, остальные считаем удачными. Счётчик GA_STUB_RUNS показывает,
# ПЫТАЛСЯ ли модуль поднять контейнер второй раз, то есть был ли откат: провалом
# кончается первый вызов, а удачный откат в dry-run добавляет ещё два (сам откат
# и пересоздание из state/, где контейнера «нет»). Поэтому сравниваем с единицей,
# а не с точным числом: важно «откат был / отката не было».
GA_STUB_RUNS=0; GA_STUB_RUN_FAILS=1
docker_run_awg() {
    GA_STUB_RUNS=$((GA_STUB_RUNS + 1))
    if [ "$GA_STUB_RUN_FAILS" -gt 0 ]; then
        GA_STUB_RUN_FAILS=$((GA_STUB_RUN_FAILS - 1))
        ga_err run_failed "stend: docker run failed"
        return 1
    fi
    ga_dry_echo "docker run (stend: откат поднялся) $1 $2 $3"
    return 0
}
run upstream_update awg3
check "docker run падает → rc≠0, код update_failed, сообщение об откате, прежний образ поднят заново" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = update_failed ] && grep -q "откачены" "$ERR" && [ "$GA_STUB_RUNS" -gt 1 ]' "rc=$rc code=$GA_ERR_CODE runs=$GA_STUB_RUNS $(errs | tail -3)"
check "сообщение об откате напечатано один раз, а не дважды" \
    '[ "$(grep -c "откачены" "$ERR")" = 1 ]' "$(grep -n "откачены" "$ERR")"
check "config.json awg3 не изменился (3.1.20260828, previous нет); скрипты вернулись; images.json прежний" \
    '[ "$(jq -r .profiles.awg3.image_tag "$CONFIG_FILE")" = 3.1.20260828 ] && [ "$(jq -c ".profiles.awg3.previous // null" "$CONFIG_FILE")" = null ] && ! grep -q CONTENT_PADDING_MAX "$UPSTREAM_DIR/awg/template.conf" && [ "$(jq -r ".[\"amneziawg-go\"].tag" "$UPSTREAM_DIR/images.json")" = 3.1.20260828 ]' "$(jq -c .profiles.awg3 "$CONFIG_FILE")"

# Дефект 1a.8: у подхваченного из приложения профиля дайджест образа пуст по
# самому договору импорта (CONTRACT §3.1). Прежнее условие отката спрашивало
# ТОЛЬКО дайджест — образ назад не возвращался, а сообщение об откате всё равно
# печаталось: контейнер снесён, а человек уверен, что всё вернули.
ga_config_merge "$(jq -cn '{profiles: {awg3: {image_digest: "", image_tag: "latest", imported: true}}}')" >/dev/null
GA_STUB_RUNS=0; GA_STUB_RUN_FAILS=1
run upstream_update awg3
check "подхваченный профиль (дайджест пуст, тег latest): откат идёт ПО ТЕГУ — контейнер поднимали снова, код update_failed" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = update_failed ] && [ "$GA_STUB_RUNS" -gt 1 ] && grep -q "откачены" "$ERR"' "rc=$rc code=$GA_ERR_CODE runs=$GA_STUB_RUNS $(errs | tail -3)"
check "прежний образ возвращён по ссылке repo:latest (не по пустому дайджесту)" \
    'grep -q "amneziavpn/amneziawg-go:latest" "$ERR"' "$(grep -n "amneziawg-go" "$ERR" | tail -3)"

# Ни дайджеста, ни тега — вернуть образ не по чему. Об откате в этом случае не
# говорим вовсе: код другой, и в сообщении назван контейнер, который лежит.
ga_config_merge "$(jq -cn '{profiles: {awg3: {image_digest: "", image_tag: ""}}}')" >/dev/null
GA_STUB_RUNS=0; GA_STUB_RUN_FAILS=1
run upstream_update awg3
check "нечем откатывать → код update_failed_no_rollback, второго docker run не было, «откачены» не печатается" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = update_failed_no_rollback ] && [ "$GA_STUB_RUNS" = 1 ] && ! grep -q "откачены" "$ERR"' "rc=$rc code=$GA_ERR_CODE runs=$GA_STUB_RUNS $(errs | tail -3)"
check "сообщение называет контейнер и способ поднять его (core-update / backup-restore)" \
    'grep -q "amnezia-awg3" "$ERR" && grep -q "core-update" "$ERR"' "$(errs | tail -3)"
# core-update — тот же договор кодов. Прежний образ есть (тег) — откат поднимает контейнер: update_failed;
# вернуть не по чему — update_failed_no_rollback с именем контейнера, а не «откат сделан».
ga_config_merge "$(jq -cn '{profiles: {awg3: {image_digest: "", image_tag: "latest"}}}')" >/dev/null
GA_STUB_RUNS=0; GA_STUB_RUN_FAILS=1
run upstream_core_update awg3
check "core-update: docker run упал, прежний образ поднят — код update_failed, второй docker run был" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = update_failed ] && [ "$GA_STUB_RUNS" -gt 1 ]' "rc=$rc code=$GA_ERR_CODE runs=$GA_STUB_RUNS $(errs | tail -3)"
ga_config_merge "$(jq -cn '{profiles: {awg3: {image_digest: "", image_tag: ""}}}')" >/dev/null
GA_STUB_RUNS=0; GA_STUB_RUN_FAILS=1
run upstream_core_update awg3
check "core-update: вернуть не по чему — update_failed_no_rollback, контейнер назван, второго docker run нет" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = update_failed_no_rollback ] && [ "$GA_STUB_RUNS" = 1 ] && grep -q "amnezia-awg3" "$ERR"' "rc=$rc code=$GA_ERR_CODE runs=$GA_STUB_RUNS $(errs | tail -3)"
# Второй круг, №5: отказ ДО сноса контейнера (сборка) — прежний контейнер цел: update_failed, отката и
# пересоздания нет, docker stop не звался.
ga_config_merge "$(jq -cn '{profiles: {awg3: {image_digest: "", image_tag: ""}}}')" >/dev/null
_bd_src=$(declare -f docker_build)
docker_build() { ga_err build_failed "stend: сборка упала"; return 1; }
GA_STUB_RUNS=0; GA_STUB_RUN_FAILS=0
run upstream_core_update awg3
check "core-update: сборка упала до сноса — update_failed, контейнер не сносили, docker run не звался" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = update_failed ] && [ "$GA_STUB_RUNS" = 0 ] && ! grep -q "DRY: docker stop amnezia-awg3" "$ERR"' "rc=$rc code=$GA_ERR_CODE runs=$GA_STUB_RUNS $(errs | tail -3)"
eval "$_bd_src"
# Демон Docker не отвечает — отказ ДО первой работы (CONTRACT §4.3, дефект 1a.10)
ga_config_merge "$(jq -cn --arg t "$(jq -r ".[\"amneziawg-go\"].tag" "$UPSTREAM_DIR/images.json")" --arg d "$(jq -r ".[\"amneziawg-go\"].digest" "$UPSTREAM_DIR/images.json")" '{profiles: {awg3: {image_tag: $t, image_digest: $d}}}')" >/dev/null
BK_BEFORE=$(ls "$BACKUP_DIR" 2>/dev/null | wc -l | tr -d ' ')
docker_container_state() { echo "$GA_DOCKER_STATE_UNREACHABLE"; }
GA_STUB_RUNS=0; GA_STUB_RUN_FAILS=0
run upstream_update awg3
check "upstream_update при недоступном демоне: код docker_unreachable, бэкапа нет, docker run не звался" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = docker_unreachable ] && [ "$(ls "$BACKUP_DIR" | wc -l | tr -d " ")" = "$BK_BEFORE" ] && [ "$GA_STUB_RUNS" = 0 ]' "rc=$rc code=$GA_ERR_CODE runs=$GA_STUB_RUNS $(errs | tail -3)"
run upstream_core_update awg3
check "core-update при недоступном демоне: тот же код до бэкапа и до загрузки образа" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = docker_unreachable ] && [ "$(ls "$BACKUP_DIR" | wc -l | tr -d " ")" = "$BK_BEFORE" ] && ! grep -q "DRY: docker pull" "$ERR"' "rc=$rc code=$GA_ERR_CODE $(errs | tail -3)"
run upstream_rollback awg3
check "rollback при недоступном демоне: тот же код, скрипты не подменены" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = docker_unreachable ] && ! grep -q CONTENT_PADDING_MAX "$UPSTREAM_DIR/awg/template.conf"' "rc=$rc code=$GA_ERR_CODE $(errs | tail -3)"
# shellcheck disable=SC1091
source "$ROOT/lib/docker.sh"

# SR-30: в новом релизе поменялся start.sh, а образ не загрузился (нет сети, предел Docker Hub). Раньше
# state/start.sh удалялся ДО загрузки и не возвращался — профиль оставался без своего start.sh (и строк goWARP).
FX_SH="$TMP/fx_startsh"; mkdir -p "$FX_SH"; cp -R "$FX/." "$FX_SH/"
printf '\n# новая строка upstream start.sh\n' >>"$FX_SH/raw/5.0.3.0/awg/start.sh"
SR30_START="$STATE_DIR/amnezia-awg3/start.sh"
printf '#!/bin/sh\n# state start.sh со строкой goWARP\n' >"$SR30_START"
sr30_start_sha=$(sha "$SR30_START")
docker_pull_ref() { ga_err pull_failed "stend: нет сети"; return 1; }
GOAMNEZIA_UPSTREAM_FIXTURE_DIR="$FX_SH" run upstream_update awg3
check "SR-30 новый start.sh, загрузка образа не удалась: state/start.sh на месте и прежний, скрипты возвращены" \
    '[ "$rc" != 0 ] && [ -f "$SR30_START" ] && [ "$(sha "$SR30_START")" = "$sr30_start_sha" ] && [ ! -e "$SR30_START.pre-update" ] && ! grep -q CONTENT_PADDING_MAX "$UPSTREAM_DIR/awg/template.conf"' \
    "rc=$rc code=$GA_ERR_CODE $(ls "$STATE_DIR/amnezia-awg3") $(errs | tail -3)"
# shellcheck disable=SC1091
source "$ROOT/lib/docker.sh"

# ── 9. Таймер ────────────────────────────────────────────────────────────────
section "upstream_tick: last_check.json и событие боту один раз"
run upstream_tick
check "upstream_tick: rc=0, updates_available=true, notified=true, reachable=true" \
    '[ "$rc" = 0 ] && [ "$(jget .updates_available)" = true ] && [ "$(jget .notified)" = true ] && [ "$(jget .reachable)" = true ]' "rc=$rc $out"
check "память: upstream/last_check.json (0600, JSON с profiles) и notified.json" \
    '[ -f "$GOAMNEZIA_DIR/upstream/last_check.json" ] && [ "$(perm "$GOAMNEZIA_DIR/upstream/last_check.json")" = 600 ] && jq -e ".profiles|length == 2" "$GOAMNEZIA_DIR/upstream/last_check.json" >/dev/null && [ -f "$GOAMNEZIA_DIR/upstream/notified.json" ]'
NTF=$(ls "$GOAMNEZIA_NOTIFY_DIR"/*.json 2>/dev/null | head -n 1)
check "событие upstream_available в очереди бота: релиз 5.0.2.1 → 5.0.3.0, профили с available_tag 3.2.20260915, unsupported" \
    '[ -n "$NTF" ] && [ "$(jq -r .event "$NTF")" = upstream_available ] && [ "$(jq -r .client_release_latest "$NTF")" = 5.0.3.0 ] && [ "$(jq -r ".profiles[0].available_tag" "$NTF")" = 3.2.20260915 ] && [ "$(jq -r ".unsupported[0]" "$NTF")" = CONTENT_PADDING_MAX ]' "$(cat "$NTF" 2>/dev/null)"
rm -f "$GOAMNEZIA_NOTIFY_DIR"/*.json
run upstream_tick
check "повторный тик с той же новизной: notified=false, событий нет" '[ "$(jget .notified)" = false ] && [ -z "$(ls "$GOAMNEZIA_NOTIFY_DIR"/*.json 2>/dev/null)" ]' "$out"
run upstream_last_check
check "upstream_last_check отдаёт сохранённый JSON" 'is_json "$out" && [ "$(jget .updates_available)" = true ]'
# L-07: итог суточной проверки для status — без сети, из памяти таймера
checked_ok=$(jq -r .checked "$GOAMNEZIA_DIR/upstream/last_check.json")
run upstream_summary
check "upstream_summary: checked = время удачной проверки, attempted, reachable=true, updates_available, релиз 5.0.2.1 → 5.0.3.0, профили с available_tag, unsupported" \
    'is_json "$out" && [ "$(jget .checked)" = "$checked_ok" ] && [ -n "$(jget .attempted)" ] && [ "$(jget .reachable)" = true ] && [ "$(jget .updates_available)" = true ] && [ "$(jget .client_release.current)" = 5.0.2.1 ] && [ "$(jget .client_release.latest)" = 5.0.3.0 ] && [ "$(jget .client_release.update)" = true ] && [ "$(jget ".profiles|length")" = 2 ] && [ "$(jget ".profiles[0].available_tag")" = 3.2.20260915 ] && [ "$(jget ".unsupported|join(\",\")")" = CONTENT_PADDING_MAX ] && [ -n "$(jget .hint)" ]' "$out"
check "память попытки: upstream/last_tick.json (0600), reachable=true" \
    '[ "$(perm "$GOAMNEZIA_DIR/upstream/last_tick.json")" = 600 ] && [ "$(jq -r .reachable "$GOAMNEZIA_DIR/upstream/last_tick.json")" = true ]'
GOAMNEZIA_UPSTREAM_FIXTURE_DIR="$TMP/nowhere" run upstream_tick
check "тик без сети: reachable=false, rc=0 (таймер не падает), событий нет" '[ "$rc" = 0 ] && [ "$(jget .reachable)" = false ] && [ -z "$(ls "$GOAMNEZIA_NOTIFY_DIR"/*.json 2>/dev/null)" ]' "$out"
run upstream_summary
check "после тика без сети: reachable=false, а итог и время — прежней удачной проверки" \
    '[ "$(jget .reachable)" = false ] && [ "$(jget .checked)" = "$checked_ok" ] && [ "$(jget .updates_available)" = true ]' "$out"

# ── 10. core-update как ремонт ───────────────────────────────────────────────
section "upstream_core_update (core-update)"
run upstream_core_update awg2
check "core-update на закреплённом образе: rc=0, repair=true, тег прежний, previous не записан" \
    '[ "$rc" = 0 ] && [ "$(jget .repair)" = true ] && [ "$(jget .image_tag)" = 3.1.20260828 ] && [ "$(jq -c ".profiles.awg2.previous // null" "$CONFIG_FILE")" = null ]' "rc=$rc $out"
check "DRY pull по дайджесту из images.json и пересоздание" 'grep -q "DRY: docker pull amneziavpn/amneziawg-go@$OLD_DIGEST" "$ERR" && grep -q "DRY: docker rm\|docker rm -fv amnezia-awg2" "$ERR"'
# SR-30: профиль уже на образе новее закреплённого (его обновил upstream-update, а самообновление продукта
# вернуло images.json к закрепу релиза) — ремонт чинит на нём, а не откатывает молча назад.
ga_config_merge "$(jq -cn --arg d "$NEW_DIGEST" '{profiles: {awg2: {image_tag: "3.2.20260915", image_digest: $d}}}')" >/dev/null
run upstream_core_update awg2
sr30_flat=$(tr '\n' ' ' <"$ERR" | tr -s ' ')
check "SR-30 ремонт профиля на образе новее закрепа: rc=0, образ прежний 3.2.20260915, repair=true, сказано словами" \
    '[ "$rc" = 0 ] && [ "$(jq -r .profiles.awg2.image_tag "$CONFIG_FILE")" = 3.2.20260915 ] && [ "$(jq -r .profiles.awg2.image_digest "$CONFIG_FILE")" = "$NEW_DIGEST" ] && [ "$(jget .repair)" = true ] && grep -q "новее закреплённого" <<<"$sr30_flat" && ! grep -q "DRY: docker pull amneziavpn/amneziawg-go@$OLD_DIGEST" "$ERR"' \
    "rc=$rc $out $(errs | tail -3)"
ga_config_merge "$(jq -cn --arg d "$OLD_DIGEST" '{profiles: {awg2: {image_tag: "3.1.20260828", image_digest: $d}}}')" >/dev/null
# SR-31: бэкап перед обновлением не удался — причина названа, а не одно «бэкап не удался» на любую.
_sr31_cb=$(declare -f create_backup)
create_backup() { ga_err low_disk "stend: мало места на диске"; return 1; }
run upstream_core_update awg2
check "SR-31 бэкап перед ремонтом не удался: backup_failed, в сообщении — причина модуля" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = backup_failed ] && grep -qF "stend: мало места на диске" <<<"$GA_ERR_MSG"' "rc=$rc code=$GA_ERR_CODE msg=$GA_ERR_MSG"
eval "$_sr31_cb"

# ── 11. Всё актуально ────────────────────────────────────────────────────────
section "фикстура «всё актуально»"
GOAMNEZIA_UPSTREAM_FIXTURE_DIR="$FX_CUR" run upstream_check
check "updates_available=false, hint «всё актуально», скрипты не качались (checked=false)" \
    '[ "$rc" = 0 ] && [ "$(jget .updates_available)" = false ] && [ "$(jget .client_release.update)" = false ] && [ "$(jget .scripts.awg.checked)" = false ] && [ "$(jget "[.profiles[] | .update] | any")" = false ]' "$(jget "{updates_available, client_release, scripts}")"
# LC-21: вышло только приложение Amnezia (релиз новее, скрипты сервера те же, образы актуальны) — раньше
# «Есть обновление… по профилю (upstream-update --profile=<профиль>)», хотя обновлять на сервере нечего.
FX_CLI="$TMP/fx_client_only"; mkdir -p "$FX_CLI/raw/5.0.3.9/awg"
cp "$FX_CUR"/hub_*.json "$FX_CLI/"
for _f in Dockerfile configure_container.sh run_container.sh template.conf; do cp "$UPSTREAM_DIR/awg/$_f" "$FX_CLI/raw/5.0.3.9/awg/$_f"; done
cp "$UPSTREAM_DIR/awg/start.sh.upstream" "$FX_CLI/raw/5.0.3.9/awg/start.sh"
printf '[{"tag_name":"5.0.3.9","draft":false,"prerelease":false,"published_at":"2026-09-26T10:00:00Z","html_url":"https://github.com/amnezia-vpn/amnezia-client/releases/tag/5.0.3.9"}]\n' >"$FX_CLI/releases.json"
GOAMNEZIA_UPSTREAM_FIXTURE_DIR="$FX_CLI" run upstream_check
check "LC-21 вышло только приложение: updates_available=false, подсказка «для сервера обновлений нет, обновите приложение», без команды моста" \
    '[ "$rc" = 0 ] && [ "$(jget .updates_available)" = false ] && [ "$(jget .client_release.update)" = true ] && [ "$(jget .hint)" = "$(tf upstream_hint_client_only 5.0.3.9)" ] && ! grep -q "upstream-update" <<<"$(jget .hint)"' \
    "$(jget "{updates_available, hint, scripts}")"

# ── 11a. Тег релиза из ответа сервиса ────────────────────────────────────────
section "тег релиза не проверялся и уходил в путь и в адрес (дефект 1a.19)"
check "ga_up_tag_safe: обычный тег годится, «..», «.», слэш, пробел и длинный — нет" \
    'ga_up_tag_safe 5.0.3.0 && ! ga_up_tag_safe .. && ! ga_up_tag_safe . && ! ga_up_tag_safe "a/b" && ! ga_up_tag_safe "a b" && ! ga_up_tag_safe "$(printf "%065d" 0)"'
mkdir -p "$TMP/fx_badtag"
cp "$FX"/hub_*.json "$TMP/fx_badtag/"
jq -c '[.[] | if .draft == true then . else .tag_name = "../../pwned" end]' "$FX/releases.json" >"$TMP/fx_badtag/releases.json"
GOAMNEZIA_UPSTREAM_FIXTURE_DIR="$TMP/fx_badtag" run upstream_check
check "upstream_check: предупреждение про негодный тег, скрипты по нему не качаются, каталога «pwned» не появилось" \
    '[ "$rc" = 0 ] && grep -q "pwned" "$ERR" && [ "$(jget ".scripts | length")" = 0 ] && [ ! -e "$TMP/pwned" ]' "rc=$rc $(jget .scripts) $(errs | tail -2)"
GOAMNEZIA_UPSTREAM_FIXTURE_DIR="$TMP/fx_badtag" run upstream_update awg2
check "upstream_update: образ обновлён, шаг скриптов пропущен, каталог НАД вендоренным не тронут" \
    '[ "$rc" = 0 ] && [ "$(jget .image_tag)" = 3.2.20260915 ] && [ "$(jget .scripts.tag)" = "" ] && [ ! -e "$TMP/pwned" ] && [ ! -f "$UPSTREAM_DIR/Dockerfile" ]' "rc=$rc $out"

# ── 12. Секреты и словарь ────────────────────────────────────────────────────
section "секреты и словарь"
check "приватные ключи (сервера и Анны) не встречаются ни в stdout, ни в stderr всего прогона" \
    '! grep -qF "$SRV_PRIV" "$ALL_ERR" && ! grep -qF "$ANNA_PRIV" "$ALL_ERR" && ! grep -qF "$SRV_PRIV" "$OUT"'
check "все DRY-команды docker — без реального docker (нет строки «docker: command not found»)" '! grep -q "command not found" "$ALL_ERR"'
ru=$(grep -o '^I18N\[[a-z_0-9]*\]' "$ROOT/lib/lang/ru.d/upstream.sh" | sort); en=$(grep -o '^I18N\[[a-z_0-9]*\]' "$ROOT/lib/lang/en.d/upstream.sh" | sort)
check "словарь upstream: ru и en совпадают по набору ключей ($(wc -l <<<"$ru" | tr -d ' '))" '[ "$ru" = "$en" ] && [ -n "$ru" ]'
used=$(grep -oE '\$\((t|tf) upstream_[a-z_0-9]+' "$ROOT/lib/upstream.sh" | sed 's/.* //' | sort -u)
miss=""; for k in $used; do grep -q "^I18N\[$k\]" "$ROOT/lib/lang/ru.d/upstream.sh" || miss="$miss $k"; done
check "все ключи t/tf модуля есть в словаре" '[ -z "$miss" ]' "нет: $miss"

# ── Итог ─────────────────────────────────────────────────────────────────────
# ── Битый config.json: причина названа своим именем ─────────────────────────
# Прежде код config_corrupt терялся в подстановке команды, сборка ответа падала,
# и человеку печаталось «источники обновлений недоступны» при живых GitHub и
# Docker Hub: владельца отправляли чинить сеть вместо настроек.
section "битый config.json не выдаётся за недоступность источников"
cp "$CONFIG_FILE" "$CONFIG_FILE.ok"
head -c 35 "$CONFIG_FILE.ok" > "$CONFIG_FILE"
run upstream_check
check "upstream_check на битом конфиге: код про конфиг, а не про сеть" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" != check_failed ] && [ "$GA_ERR_CODE" != unreachable ]' \
    "rc=$rc code=$GA_ERR_CODE"
cp "$CONFIG_FILE.ok" "$CONFIG_FILE"
run upstream_check
check "после починки конфига проверка снова работает" '[ "$rc" = 0 ]' "rc=$rc code=$GA_ERR_CODE"

# ── SR-28: откат ядра сорвался уже после сноса контейнера ─────────────────────
# Совет был «восстановите из бэкапа» — самое разрушительное действие продукта, хотя хватает копии
# состояния. Теперь: профиль поднимается из неё (rollback_failed со словами «поднят заново»), а не вышло —
# rollback_failed_no_container и совет «Поднять заново» (profile-repair). Сеть, бэкап и сам откат — заглушки.
section "SR-28: откат ядра сорвался после сноса контейнера"
sr28() {
    (
        rel=$(jq -r .client_release "$UPSTREAM_DIR/images.json")
        ga_config_merge "$(jq -cn --arg r "$rel" '{profiles: {awg2: {previous: {image_tag: "3.1.20260828", image_digest: "", client_release: $r}}}}')" >/dev/null 2>&1
        ga_up_backup() { printf 'stub.tar.gz'; }
        docker_pull_ref() { return 0; }
        amnezia_core_update() { GA_AMN_CORE_RM_DONE=1; ga_err run_failed "stend: docker run failed"; return 1; }
        if [ "$1" = up ]; then amnezia_state_restore() { return 0; }; ga_amn_wait_up() { return 0; }
        else amnezia_state_restore() { return 1; }; fi
        upstream_rollback awg2 >/dev/null 2>&1
        printf '%s|%s' "$GA_ERR_CODE" "$GA_ERR_MSG"
    )
}
r=$(sr28 up)
check "SR-28 контейнер снесён, копия состояния поднялась: rollback_failed «поднят заново»" \
    '[ "${r%%|*}" = rollback_failed ] && [ "${r#*|}" = "$(tf upstream_err_rollback_restored awg2)" ]' "$r"
r=$(sr28 down)
check "SR-28 и копия не поднялась: rollback_failed_no_container, совет — «Поднять заново», не бэкап" \
    '[ "${r%%|*}" = rollback_failed_no_container ] && [ "${r#*|}" = "$(tf upstream_err_rollback_down awg2 amnezia-awg2)" ]' "$r"
ga_config_merge '{"profiles":{"awg2":{"previous":null}}}' >/dev/null 2>&1


# Регрессия живого порядка: state_sync приносит старый start.sh прямо перед
# пересозданием. Простой dry-run пропускает sync и раньше не замечал потерю
# нового upstream. Эмулируем только контейнер, реальный updater/core идут целиком.
start_script_case() {
    (
        mode="$1"; case_dir="$TMP/start-script-$mode"; mkdir -p "$case_dir"
        cp -R "$GOAMNEZIA_DIR" "$case_dir/ga"
        cp -R "$ROOT/lib/upstream" "$case_dir/upstream"
        export GOAMNEZIA_DIR="$case_dir/ga" UPSTREAM_DIR="$case_dir/upstream"
        export STATE_DIR="$GOAMNEZIA_DIR/state" CONFIG_FILE="$GOAMNEZIA_DIR/config.json"
        export USERS_FILE="$GOAMNEZIA_DIR/users.json" BACKUP_DIR="$GOAMNEZIA_DIR/backups"
        export GOAMNEZIA_UPSTREAM_FIXTURE_DIR="$FX_SH"
        ga_config_merge "$(jq -cn --arg d "$OLD_DIGEST" '{profiles:{awg2:{image_tag:"3.1.20260828",image_digest:$d,previous:null}}}')" >/dev/null
        live="$case_dir/live-start.sh"
        printf '#!/bin/sh\n# original goWARP hook\n' >"$live"
        amnezia_state_sync() {
            cp "$live" "$STATE_DIR/amnezia-awg2/start.sh"
            # Настоящий sync меняет весь каталог; побочный файл внутри тоже пропадёт.
            rm -f "$STATE_DIR/amnezia-awg2/start.sh.pre-update"
        }
        amnezia_state_restore() { cp "$STATE_DIR/amnezia-awg2/start.sh" "$live"; }
        docker_container_state() { echo running; }
        ga_amn_docker_build() { return 0; }
        docker_rm() { return 0; }
        docker_run_awg() { return 0; }
        ga_up_backup() { printf 'fixture-backup.tar.gz'; }
        ga_amn_wait_up() {
            if [ "$mode" = rollback ] && [ ! -f "$case_dir/failed-once" ]; then
                touch "$case_dir/failed-once"; return 1
            fi
            return 0
        }
        ga_capture result upstream_update awg2 >/dev/null 2>&1; status=$?
        if [ "$mode" = rollback ]; then
            [ "$status" != 0 ] && [ "$GA_ERR_CODE" = update_failed ] \
                && grep -q 'original goWARP hook' "$live" \
                && ! grep -q 'новая строка upstream' "$live"
        else
            [ "$status" = 0 ] && grep -q 'новая строка upstream' "$live" \
                && ! grep -q 'original goWARP hook' "$live"
        fi
    )
}
check "новый upstream start.sh доходит до контейнера после state_sync" 'start_script_case update'
check "автооткат после неудачного старта возвращает точный старый start.sh с goWARP" 'start_script_case rollback'

DONE=1
printf '\n══ ИТОГ: пройдено %d, провалено %d, пропущено %d\n' "$pass" "$fail" "$skip"
[ "$fail" -eq 0 ]
