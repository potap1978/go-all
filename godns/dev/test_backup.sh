#!/bin/bash
# goDNS — Copyright (c) 2026 anten-ka. All rights reserved.
#
# Тест резервных копий и обновления — на фикстурах.
#
# Работает без root, без сети и без Ubuntu: всё состояние goDNS
# (/opt/godns, /etc/godns, /etc/dnsdist) подменяется переменными на
# временный каталог. lib/backup.sh и lib/update.sh намеренно не используют
# ассоциативных массивов, поэтому тест идёт и на bash 3.2 (штатный в
# macOS), и на bash 5.
#
# Что здесь на самом деле проверяется, кроме «файл создался»:
#   • секрета (путь DoH, ключ консоли) НЕТ в манифесте — манифест читают,
#     чтобы решить, разворачивать ли архив, и он не должен быть носителем
#     секретов сам по себе;
#   • приватного ключа сертификата НЕТ в архиве — сертификат чужой;
#   • dnsdist.conf из архива НЕ ставится на место: это Lua, который
#     исполнит служба, а архив может приехать с чужой машины;
#   • после восстановления секретный путь и порты те же — их смена
#     отваливает всех клиентов разом и молча.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PASS=0; FAIL=0
G='\033[0;32m'; R='\033[0;31m'; D='\033[2m'; B='\033[1m'; N='\033[0m'

ok()   { PASS=$((PASS+1)); printf "  ${G}✓${N} %s\n" "$1"; }
bad()  { FAIL=$((FAIL+1)); printf "  ${R}✗${N} %s\n" "$1"; [ $# -gt 1 ] && printf "      ${D}%s${N}\n" "$2"; }
is()   { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1" "ждал [$3], получил [$2]"; fi; }
isnt() { if [ "$2" != "$3" ]; then ok "$1"; else bad "$1" "не ждал [$3]"; fi; }
yes_() { if "$@" >/dev/null 2>&1; then ok "$1"; else bad "$1" "ожидался успех"; fi; }
head_() { printf "\n${B}── %s${N}\n" "$1"; }

command -v python3 >/dev/null 2>&1 || { echo "нужен python3"; exit 1; }
command -v tar     >/dev/null 2>&1 || { echo "нужен tar"; exit 1; }

FIX=$(mktemp -d "${TMPDIR:-/tmp}/godns-bktest.XXXXXX") || exit 1
trap 'rm -rf "$FIX"' EXIT

# ── подменяем ВСЁ состояние на фикстуру ─────────────────────────────────
export GODNS_DIR="$FIX/opt/godns"
export GODNS_ETC="$FIX/etc/godns"
export GODNS_CONFIG="$GODNS_DIR/config.json"
export GODNS_BACKUP_DIR="$FIX/root/godns-backups"
export DNSDIST_CONF="$FIX/etc/dnsdist/dnsdist.conf"
export LE_ROOT="$FIX/etc/letsencrypt"
export LE_HOOK_FILE="$FIX/etc/letsencrypt/renewal-hooks/deploy/50-godns.sh"
export GODNS_INSTALLER_DIR="$FIX/opt/godns-installer"

SECRET="s3cretPathAbc"
CONSOLE_KEY="Y29uc29sZS1rZXktc2VjcmV0"
CERT_DIR="$FIX/etc/letsencrypt/live/dns.example.com"

mkdir -p "$GODNS_DIR" "$GODNS_ETC" "$(dirname "$DNSDIST_CONF")" \
         "$CERT_DIR" "$(dirname "$LE_HOOK_FILE")"

# Приватный ключ сертификата — чужой. Он ОБЯЗАН остаться снаружи архива.
printf -- '-----BEGIN PRIVATE KEY-----\nFAKE-PRIVATE-KEY-MATERIAL-FOR-TESTS\n-----END PRIVATE KEY-----\n' \
    > "$CERT_DIR/privkey.pem"
printf -- '-----BEGIN CERTIFICATE-----\nfixture\n-----END CERTIFICATE-----\n' \
    > "$CERT_DIR/fullchain.pem"

write_state() {   # write_state <doh_path> <doh_port> <dot_port>
    cat > "$GODNS_CONFIG" <<JSON
{
  "domain": "dns.example.com",
  "cert_path": "${CERT_DIR}/fullchain.pem",
  "key_path": "${CERT_DIR}/privkey.pem",
  "cert_source": "certs_src_govless",
  "doh_port": "$2",
  "dot_port": "$3",
  "doh_path": "$1",
  "policy": "roundrobin",
  "upstreams": "quad9|dot|9.9.9.9|853|dns.quad9.net|",
  "upstream_ids": "quad9,cloudflare",
  "console_key": "${CONSOLE_KEY}",
  "engine": "dnsdist",
  "language": "ru",
  "godns_version": "0.1.0",
  "schema": "1"
}
JSON
    chmod 600 "$GODNS_CONFIG"
    printf 'ru\n' > "$GODNS_DIR/.language"
    printf 'state-marker\n' > "$GODNS_ETC/marker.txt"
    printf 'setKey("%s")\naddDOHLocal("0.0.0.0:%s", nil, nil, {"/%s"})\n' \
        "$CONSOLE_KEY" "$2" "$1" > "$DNSDIST_CONF"
    printf '#!/bin/sh\n# godns renewal hook\n' > "$LE_HOOK_FILE"
}
write_state "$SECRET" 8443 853

# ── загружаем библиотеки ────────────────────────────────────────────────
# lib/i18n.sh здесь НЕ подключаем: его bash-4 guard делает exit 1, а внутри
# `source` это убивает всю оболочку. Ставим заглушки, как в test_certs.sh.
# shellcheck source=/dev/null
. "$SCRIPT_DIR/lib/common.sh"
t()  { printf '%s' "$1"; }
tf() { local k="$1"; shift; printf '%s(%s)' "$k" "$*"; }
# shellcheck source=/dev/null
. "$SCRIPT_DIR/lib/backup.sh"
# shellcheck source=/dev/null
. "$SCRIPT_DIR/lib/update.sh"

printf "\n${B}goDNS — резервные копии и обновление${N} ${D}(bash %s)${N}\n" "${BASH_VERSION%%(*}"

# ═══════════════════════════════════════════════════════════════════════
head_ "сравнение версий"
if godns_version_lt "0.9.0" "0.10.0";  then ok "0.9.0 < 0.10.0"; else bad "0.9.0 < 0.10.0"; fi
if godns_version_lt "0.10.0" "0.9.0";  then bad "0.10.0 < 0.9.0 принято"; else ok "0.10.0 не меньше 0.9.0"; fi
if godns_version_lt "1.2.3" "1.2.3";   then bad "равные приняты за меньшее"; else ok "равные версии не меньше"; fi
if godns_version_lt "0.0.0" "0.1.0";   then ok "0.0.0 < 0.1.0 (конфиг без версии)"; else bad "0.0.0 < 0.1.0"; fi
if godns_version_lt "2.0.0" "10.0.0";  then ok "2.0.0 < 10.0.0"; else bad "2.0.0 < 10.0.0"; fi
# Запасной разбор — на случай sort без -V. Проверяем ОТДЕЛЬНО: иначе
# на машине с GNU sort он никогда не исполнится и сгниёт незамеченным.
if _bk_ver_lt_fallback "0.9.0" "0.10.0"; then ok "запасной разбор: 0.9.0 < 0.10.0"; else bad "запасной разбор: 0.9.0 < 0.10.0"; fi
if _bk_ver_lt_fallback "1.10" "1.9";    then bad "запасной разбор: 1.10 < 1.9 принято"; else ok "запасной разбор: 1.10 не меньше 1.9"; fi
if _bk_ver_lt_fallback "1.0.0" "1.0.0"; then bad "запасной разбор: равные"; else ok "запасной разбор: равные не меньше"; fi

# ═══════════════════════════════════════════════════════════════════════
head_ "опись состояния"
SRC_N=$(godns_backup_sources | grep -c . )
is "в описи 5 объектов" "$(printf '%s' "$SRC_N" | tr -d ' ')" "5"
if godns_backup_sources | grep -q 'state/config.json'; then ok "config.json в описи"; else bad "config.json в описи"; fi
if godns_backup_sources | grep -q 'engine/dnsdist.conf'; then ok "конфиг движка в описи"; else bad "конфиг движка в описи"; fi
if godns_backup_sources | grep -q 'privkey'; then bad "приватный ключ попал в опись"; else ok "приватного ключа в описи нет"; fi

H1=$(godns_backup_state_hash)
if [ -n "$H1" ]; then ok "отпечаток состояния считается"; else bad "отпечаток состояния считается"; fi

# ═══════════════════════════════════════════════════════════════════════
head_ "имя архива"
is "метка чистится"        "$(_bk_slug 'before update 0.0.9')" "before-update-0.0.9"
is "кириллица не даёт частокол дефисов" "$(_bk_slug 'первая проверка')" ""
is "хвостовой мусор срезан" "$(_bk_slug '  ??? ')" ""

head_ "создание копии"
ARC=$(godns_backup_create "manual-check" 2>/dev/null)
if [ -n "$ARC" ] && [ -f "$ARC" ]; then ok "архив создан: $(basename "$ARC")"; else bad "архив создан" "получил [$ARC]"; fi

MODE=$(ls -l "$ARC" 2>/dev/null | cut -c1-10)
is "права архива rw------- " "$MODE" "-rw-------"
DMODE=$(ls -ld "$GODNS_BACKUP_DIR" 2>/dev/null | cut -c1-10)
is "права каталога rwx------" "$DMODE" "drwx------"

if [ -f "${ARC}.sha256" ]; then ok "рядом лежит контрольная сумма"; else bad "рядом лежит контрольная сумма"; fi
WANT=$(grep -oE '[0-9a-fA-F]{64}' "${ARC}.sha256" 2>/dev/null | head -n1)
is "сумма совпадает с файлом" "$(_bk_sha256 "$ARC")" "$WANT"

# Имя: версия и метка должны быть видны глазами, без распаковки.
case "$(basename "$ARC")" in
    godns-"$GODNS_VERSION"-*-manual-check.tar.gz) ok "в имени архива версия, дата и метка" ;;
    *) bad "в имени архива версия, дата и метка" "$(basename "$ARC")" ;;
esac

head_ "манифест"
is "версия"       "$(godns_backup_manifest_field "$ARC" godns_version "?")" "$GODNS_VERSION"
is "домен"        "$(godns_backup_manifest_field "$ARC" domain "?")"        "dns.example.com"
is "порт DoH"     "$(godns_backup_manifest_field "$ARC" doh_port "?")"      "8443"
is "схема"        "$(godns_backup_manifest_field "$ARC" schema "?")"        "1"
isnt "дата заполнена" "$(godns_backup_manifest_field "$ARC" created "?")"   "?"
is "отпечаток состояния в манифесте" "$(godns_backup_manifest_field "$ARC" state_sha256 "?")" "$H1"

# ГЛАВНОЕ: манифест читают ДО распаковки, чтобы решить, разворачивать ли
# архив. Секрету там делать нечего — он и так внутри, в config.json.
MAN=$(tar -xzOf "$ARC" godns-backup/manifest.json 2>/dev/null)
case "$MAN" in
    *"$SECRET"*)      bad "секретный путь DoH утёк в манифест" ;;
    *)                ok  "секретного пути DoH в манифесте нет" ;;
esac
case "$MAN" in
    *"$CONSOLE_KEY"*) bad "ключ консоли утёк в манифест" ;;
    *)                ok  "ключа консоли в манифесте нет" ;;
esac

head_ "чужого в архиве нет"
LIST=$(tar -tzf "$ARC" 2>/dev/null)
if printf '%s' "$LIST" | grep -q 'godns-backup/state/config.json'; then ok "config.json внутри"; else bad "config.json внутри"; fi
if printf '%s' "$LIST" | grep -q 'godns-backup/engine/dnsdist.conf'; then ok "конфиг движка внутри"; else bad "конфиг движка внутри"; fi
if printf '%s' "$LIST" | grep -qi 'privkey\|\.key$'; then bad "приватный ключ попал в архив"; else ok "приватного ключа в архиве нет"; fi
BODY=$(tar -xzOf "$ARC" 2>/dev/null)
case "$BODY" in
    *FAKE-PRIVATE-KEY-MATERIAL-FOR-TESTS*) bad "содержимое приватного ключа внутри архива" ;;
    *)                                   ok  "содержимого приватного ключа в архиве нет" ;;
esac

# ═══════════════════════════════════════════════════════════════════════
head_ "список"
if godns_backup_list >/dev/null 2>&1; then ok "список строится"; else bad "список строится"; fi
is "свежий архив первым" "$(godns_backup_files | head -n1)" "$ARC"

# ═══════════════════════════════════════════════════════════════════════
head_ "восстановление"
# Портим состояние ровно так, как это выглядит в жизни: другой секретный
# путь, другие порты, потерянный язык, снесённый каталог состояния и
# перезаписанный конфиг движка.
write_state "WRONGpath999" 9999 9998
rm -f "$GODNS_DIR/.language"
rm -rf "$GODNS_ETC"
printf 'setKey("HACKED")\n-- враждебный Lua\nos.execute("id")\n' > "$DNSDIST_CONF"
BROKEN_CONF=$(cat "$DNSDIST_CONF")

is "перед восстановлением путь испорчен" "$(config_get doh_path "")" "WRONGpath999"

if godns_backup_restore "$ARC" "" yes >/dev/null 2>&1; then
    ok "восстановление отработало"
else
    bad "восстановление отработало"
fi

is "секретный путь DoH вернулся" "$(config_get doh_path "")"  "$SECRET"
is "порт DoH вернулся"           "$(config_get doh_port "")"  "8443"
is "порт DoT вернулся"           "$(config_get dot_port "")"  "853"
is "ключ консоли вернулся"       "$(config_get console_key "")" "$CONSOLE_KEY"
is "домен вернулся"              "$(config_get domain "")"    "dns.example.com"
is "апстримы вернулись"          "$(config_get upstream_ids "")" "quad9,cloudflare"
is "язык вернулся"               "$(cat "$GODNS_DIR/.language" 2>/dev/null | tr -d '[:space:]')" "ru"
is "каталог состояния вернулся"  "$(cat "$GODNS_ETC/marker.txt" 2>/dev/null | tr -d '[:space:]')" "state-marker"

# ── восстановление мигрирует ОТ ВЕРСИИ АРХИВА ───────────────────────────
# ГРАБЛИ: godns_config_migrate звалась с пустым GODNS_MIGRATED_FROM, и
# ступени за гейтом `godns_migrated_from_before "X"` не выполнялись. А
# godns_mark_applied ниже штамповал в конфиг версию КОДА, а не архива, —
# следующий запуск видел «та же версия», молчаливый no-op, и гейтованные
# ступени не исполнялись уже НИКОГДА. Копия от старой версии, развёрнутая
# на новом коде, навсегда оставалась немигрированной, а конфиг при этом
# утверждал, что он свежий.
is "миграция знает версию архива" "${GODNS_MIGRATED_FROM:-—}" \
   "$(godns_backup_manifest_field "$ARC" godns_version "?")"
if [ -n "${GODNS_MIGRATED_FROM:-}" ] && godns_migrated_from_before "99.0.0"; then
    ok "гейт миграции сработал бы (from < 99.0.0)"
else
    bad "гейт миграции не срабатывает — ступени схемы пропущены"
fi
CFGMODE=$(ls -l "$GODNS_CONFIG" 2>/dev/null | cut -c1-10)
is "права config.json rw-------" "$CFGMODE" "-rw-------"

# ГЛАВНОЕ ПРО БЕЗОПАСНОСТЬ: конфиг движка — исполняемый службой Lua.
# Из архива он НЕ ставится на место ни при каких условиях; настоящий
# рождается заново из config.json. Рядом кладём копию для сверки.
is "конфиг движка НЕ перезаписан из архива" "$(cat "$DNSDIST_CONF")" "$BROKEN_CONF"
if [ -f "${DNSDIST_CONF}.from-backup" ]; then ok "конфиг из архива положен рядом для сверки"; else bad "конфиг из архива положен рядом"; fi
if grep -q 'os.execute' "${DNSDIST_CONF}.from-backup" 2>/dev/null; then
    bad "рядом положили не тот файл"
else
    ok "рядом лежит именно версия из архива"
fi

# Страховочная копия перед восстановлением — иначе «восстановил не тот
# архив» было бы необратимым.
if godns_backup_files | grep -q 'pre-restore'; then ok "снята страховочная копия"; else bad "снята страховочная копия"; fi

# ═══════════════════════════════════════════════════════════════════════
head_ "восстановление: отказы"
if godns_backup_restore "$FIX/nope.tar.gz" "" yes >/dev/null 2>&1; then
    bad "несуществующий архив принят"
else
    ok "несуществующий архив отклонён"
fi

mkdir -p "$FIX/alien/whatever"
printf 'hello\n' > "$FIX/alien/whatever/file.txt"
tar czf "$GODNS_BACKUP_DIR/alien-20260101-000000.tar.gz" -C "$FIX/alien" whatever 2>/dev/null
if godns_backup_restore "$GODNS_BACKUP_DIR/alien-20260101-000000.tar.gz" "" yes >/dev/null 2>&1; then
    bad "чужой архив без config.json принят"
else
    ok "чужой архив без config.json отклонён"
fi
rm -f "$GODNS_BACKUP_DIR/alien-20260101-000000.tar.gz"

CORRUPT="$FIX/corrupt.tar.gz"
cp "$ARC" "$CORRUPT"
cp "${ARC}.sha256" "${CORRUPT}.sha256"
printf 'garbage' >> "$CORRUPT"
if godns_backup_restore "$CORRUPT" "" yes >/dev/null 2>&1; then
    bad "архив с несовпавшей суммой принят"
else
    ok "архив с несовпавшей суммой отклонён"
fi

# ═══════════════════════════════════════════════════════════════════════
head_ "уборка"
write_state "$SECRET" 8443 853
i=0
while [ "$i" -lt 3 ]; do
    godns_backup_create "gen$i" >/dev/null 2>&1
    # Имя архива посекундное — без паузы второй запуск получил бы суффикс,
    # и порядок по mtime стал бы неотличим. Меняем состояние вместо сна.
    printf 'gen%s\n' "$i" > "$GODNS_ETC/marker.txt"
    i=$((i + 1))
done
BEFORE=$(godns_backup_files | grep -c . )
if [ "$(printf '%s' "$BEFORE" | tr -d ' ')" -ge 4 ]; then ok "копий накопилось: $BEFORE"; else bad "копий накопилось" "$BEFORE"; fi

NEWEST=$(godns_backup_files | head -n1)
godns_backup_prune 2 >/dev/null 2>&1
AFTER=$(godns_backup_files | grep -c . )
is "после уборки осталось 2" "$(printf '%s' "$AFTER" | tr -d ' ')" "2"
is "самая свежая копия уцелела" "$(godns_backup_files | head -n1)" "$NEWEST"
LEFTOVER=$(ls "$GODNS_BACKUP_DIR" 2>/dev/null | grep -c 'sha256')
is "осиротевших .sha256 не осталось" "$(printf '%s' "$LEFTOVER" | tr -d ' ')" "2"

# ═══════════════════════════════════════════════════════════════════════
head_ "миграция схемы конфига"
# Доисторический конфиг: схемы нет, путь лежит под старым именем.
cat > "$GODNS_CONFIG" <<'JSON'
{
  "doh_domain": "old.example.com",
  "secret_path": "oldSecretPath",
  "doh_port": "4443",
  "dot_port": "853"
}
JSON
is "схема доисторического конфига = 0" "$(godns_config_schema)" "0"
if godns_config_migrate >/dev/null 2>&1; then ok "миграция прошла"; else bad "миграция прошла"; fi
is "secret_path → doh_path" "$(config_get doh_path "")" "oldSecretPath"
is "doh_domain → domain"    "$(config_get domain "")"   "old.example.com"
is "движок проставлен"      "$(config_get engine "")"   "dnsdist"
is "схема поднялась до текущей" "$(godns_config_schema)" "$GODNS_SCHEMA"
is "старый ключ не удалён (нужен для отката)" "$(config_get secret_path "")" "oldSecretPath"
if godns_config_migrate >/dev/null 2>&1; then ok "повторная миграция — no-op"; else bad "повторная миграция — no-op"; fi

# Конфиг из будущего трогать нельзя: миграции идут только вперёд.
config_set schema "99" >/dev/null 2>&1
if godns_config_migrate >/dev/null 2>&1; then
    bad "конфиг из будущего мигрирован"
else
    ok "конфиг из будущего не тронут"
fi
is "схема из будущего осталась как была" "$(godns_config_schema)" "99"

# ═══════════════════════════════════════════════════════════════════════
head_ "определение обновления"
write_state "$SECRET" 8443 853
mv "$GODNS_CONFIG" "$FIX/config.saved"
is "нет конфига → первая установка" "$(godns_update_state)" "fresh"
# Каталог состояния на месте, а конфига нет — это ВСЁ ЕЩЁ первая установка.
# Грабли goTelegram: признаком «уже стоит» брали каталог, и человек на
# чистой машине получал разговор про обновление.
mkdir -p "$GODNS_ETC"
printf 'x\n' > "$GODNS_ETC/leftover"
is "остатки каталога не считаются установкой" "$(godns_update_state)" "fresh"
mv "$FIX/config.saved" "$GODNS_CONFIG"

config_set godns_version "$GODNS_VERSION" >/dev/null 2>&1
is "та же версия → переустановка" "$(godns_update_state)" "same"
config_set godns_version "0.0.9" >/dev/null 2>&1
is "версия старше → обновление" "$(godns_update_state)" "upgrade"
config_set godns_version "99.9.9" >/dev/null 2>&1
is "версия новее → откат назад" "$(godns_update_state)" "downgrade"

# Конфиг без версии вообще — установка старше самой привычки её писать.
python3 - "$GODNS_CONFIG" <<'PY' >/dev/null 2>&1
import json, sys
p = sys.argv[1]
d = json.load(open(p, encoding="utf-8"))
d.pop("godns_version", None)
json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
PY
is "конфиг без версии → 0.0.0" "$(godns_installed_version)" "0.0.0"
is "конфиг без версии → обновление" "$(godns_update_state)" "upgrade"

# ГЛАВНАЯ ловушка адаптации: config.json создаёт save_language при первом
# же запуске меню, ДО того как что-либо установлено. Если считать сам факт
# существования файла установкой, человек на чистой машине получит отчёт об
# обновлении и лишний архив. Языка мало — нужен след мастера.
cp "$GODNS_CONFIG" "$FIX/config.saved4"
printf '{"language":"ru"}\n' > "$GODNS_CONFIG"
if godns_state_present; then bad "конфиг с одним языком принят за установку"; else ok "конфиг с одним языком — не установка"; fi
is "только язык → первая установка" "$(godns_update_state)" "fresh"
printf '{"language":"ru","domain":"dns.example.com"}\n' > "$GODNS_CONFIG"
if godns_state_present; then ok "выбранный домен — уже установка"; else bad "выбранный домен — уже установка"; fi

# Прерванный мастер: домен и сертификат записаны шагом 1, движок не
# установлен, штампов нет. Это НЕ установка — иначе следующий запуск
# показывал «Обновление 0.0.0 → …» с лишней копией и прятал сам мастер.
_ipres=$(
    GODNS_ENGINE="dnsdist"
    engine_is_installed() { return 1; }
    if godns_state_present; then printf 'present'; else printf 'absent'; fi
    printf '|%s' "$(godns_update_state)"
)
is "домен без движка и штампов — прерванный мастер, не установка" "$_ipres" "absent|fresh"
_ipres=$(
    GODNS_ENGINE="dnsdist"
    engine_is_installed() { return 0; }
    if godns_state_present; then printf 'present'; else printf 'absent'; fi
)
is "домен и установленный движок без штампов — старая установка" "$_ipres" "present"
_ipres=$(
    unset GODNS_ENGINE
    engine_is_installed() { return 1; }
    if godns_state_present; then printf 'present'; else printf 'absent'; fi
)
is "движок не загружен (мост) — прежний ответ «стоит»" "$_ipres" "present"

# Старый lib/common.sh на диске читать НЕЛЬЗЯ: godns_update_finish работает
# уже после подмены каталога программы, и там лежит НОВАЯ версия. Такой
# «запасной источник» отменял бы миграцию ровно тем, кому она нужна.
mkdir -p "$GODNS_INSTALLER_DIR/lib"
printf 'GODNS_VERSION="9.9.9"\n' > "$GODNS_INSTALLER_DIR/lib/common.sh"
is "версия с диска игнорируется" "$(godns_installed_version)" "0.0.0"
rm -rf "$GODNS_INSTALLER_DIR"
cp "$FIX/config.saved4" "$GODNS_CONFIG"

head_ "гейт миграции по версии"
GODNS_MIGRATED_FROM="0.0.5"
if godns_migrated_from_before "0.1.0"; then ok "гейт срабатывает на старой версии"; else bad "гейт срабатывает на старой версии"; fi
if godns_migrated_from_before "0.0.1"; then bad "гейт сработал на новой версии"; else ok "гейт молчит на новой версии"; fi
GODNS_MIGRATED_FROM=""
if godns_migrated_from_before "9.9.9"; then bad "гейт сработал вне обновления"; else ok "гейт молчит вне обновления"; fi

# ═══════════════════════════════════════════════════════════════════════
head_ "порты и секретный путь переживают обновление"
write_state "$SECRET" 8443 853
godns_preserve_existing
is "запомнен путь"  "$GODNS_KEPT_DOH_PATH" "$SECRET"
is "запомнен порт"  "$GODNS_KEPT_DOH_PORT" "8443"

# Имитируем миграцию, которая по ошибке затёрла и то и другое.
config_set doh_path "brandNewPath" >/dev/null 2>&1
config_set doh_port "1234"         >/dev/null 2>&1
config_set dot_port "1235"         >/dev/null 2>&1
godns_restore_preserved >/dev/null 2>&1
is "путь возвращён"      "$(config_get doh_path "")" "$SECRET"
is "порт DoH возвращён"  "$(config_get doh_port "")" "8443"
is "порт DoT возвращён"  "$(config_get dot_port "")" "853"

# Ничего не менялось — восстанавливать нечего и молчать не о чем.
godns_preserve_existing
OUT=$(godns_restore_preserved 2>&1)
is "без расхождений сообщений нет" "$OUT" ""

# ═══════════════════════════════════════════════════════════════════════
head_ "подготовка обновления (это делает install.sh)"
write_state "$SECRET" 8443 853
rm -f "${GODNS_ETC}/.pre-update-backup"

# Первая установка: ни архива, ни маркера.
mv "$GODNS_CONFIG" "$FIX/config.saved3"
N_BEFORE=$(godns_backup_files | grep -c . )
if godns_update_prepare >/dev/null 2>&1; then ok "на чистой машине prepare не мешает"; else bad "на чистой машине prepare не мешает"; fi
is "на чистой машине архива нет" "$(godns_backup_files | grep -c . | tr -d ' ')" "$(printf '%s' "$N_BEFORE" | tr -d ' ')"
mv "$FIX/config.saved3" "$GODNS_CONFIG"

# Переустановка той же версии: тоже без архива — иначе каждый повторный
# запуск установщика вытеснял бы полезные копии своими.
config_set godns_version "$GODNS_VERSION" >/dev/null 2>&1
N_BEFORE=$(godns_backup_files | grep -c . )
godns_update_prepare >/dev/null 2>&1
is "переустановка не плодит архивы" "$(godns_backup_files | grep -c . | tr -d ' ')" "$(printf '%s' "$N_BEFORE" | tr -d ' ')"

# Настоящее обновление: архив + маркер + запись предыдущей версии.
config_set godns_version "0.0.8" >/dev/null 2>&1
N_BEFORE=$(godns_backup_files | grep -c . )
if godns_update_prepare >/dev/null 2>&1; then ok "prepare при обновлении отработал"; else bad "prepare при обновлении отработал"; fi
if [ "$(godns_backup_files | grep -c . | tr -d ' ')" -gt "$(printf '%s' "$N_BEFORE" | tr -d ' ')" ]; then
    ok "копия перед обновлением снята"
else
    bad "копия перед обновлением снята"
fi
is "предыдущая версия записана" "$(config_get previous_version "")" "0.0.8"
MARK=$(cat "${GODNS_ETC}/.pre-update-backup" 2>/dev/null)
if [ -n "$MARK" ] && [ -f "$MARK" ]; then ok "маркер указывает на существующий архив"; else bad "маркер указывает на существующий архив" "$MARK"; fi
case "$(basename "$MARK")" in
    *pre-update-0.0.8*) ok "в имени копии видно, с какой версии уходим" ;;
    *) bad "в имени копии видно, с какой версии уходим" "$(basename "$MARK")" ;;
esac
# prepare версию НЕ штампует: это делает finish, уже после миграции.
# Иначе оборванное обновление выглядело бы завершённым.
is "prepare не штампует новую версию" "$(config_get godns_version "")" "0.0.8"

# ═══════════════════════════════════════════════════════════════════════
head_ "полный цикл обновления"
write_state "$SECRET" 8443 853
config_set godns_version "0.0.9" >/dev/null 2>&1
config_set schema "0"            >/dev/null 2>&1
rm -f "${GODNS_ETC}/.pre-update-backup"
N_BEFORE=$(godns_backup_files | grep -c . )

if godns_update_finish >/dev/null 2>&1; then ok "обновление отработало"; else bad "обновление отработало"; fi
N_AFTER=$(godns_backup_files | grep -c . )
if [ "$(printf '%s' "$N_AFTER" | tr -d ' ')" -gt "$(printf '%s' "$N_BEFORE" | tr -d ' ')" ]; then
    ok "копия перед обновлением снята сама, без install.sh"
else
    bad "копия перед обновлением снята" "было $N_BEFORE, стало $N_AFTER"
fi
is "версия проштампована"       "$(config_get godns_version "")"   "$GODNS_VERSION"
is "предыдущая версия записана" "$(config_get previous_version "")" "0.0.9"
is "схема поднята"              "$(godns_config_schema)"           "$GODNS_SCHEMA"
is "секретный путь цел"         "$(config_get doh_path "")"        "$SECRET"
is "порт DoH цел"               "$(config_get doh_port "")"        "8443"
# Маркер снят ПОСЛЕ успешного обновления: он выполнил роль (провёл бы
# откат, не встань служба). ГРАБЛИ: раньше он жил до следующего
# обновления, и install.sh взводил по нему «перенос доделается» на
# переустановке той же версии; плюс маркер уезжал в бекап и оживал на
# чужой машине.
if [ -f "${GODNS_ETC}/.pre-update-backup" ]; then bad "маркер обновления не снят после успеха"; else ok "маркер обновления снят после успеха"; fi
is "поднят флаг «отчёт показан»" "$GODNS_UPDATE_SHOWN" "yes"

# Второй заход по той же версии обязан быть МОЛЧАЛИВЫМ и бесследным:
# в goTelegram именно здесь человек при каждом запуске видел «Миграция
# состояния…» и получал лишний архив на диск.
N_BEFORE=$(godns_backup_files | grep -c . )
GODNS_UPDATE_SHOWN="no"
OUT=$(godns_update_finish 2>&1)
N_AFTER=$(godns_backup_files | grep -c . )
is "повторный запуск ничего не печатает" "$OUT" ""
is "повторный запуск не плодит архивы"   "$(printf '%s' "$N_AFTER" | tr -d ' ')" "$(printf '%s' "$N_BEFORE" | tr -d ' ')"
is "повторный запуск не просит паузы"    "$GODNS_UPDATE_SHOWN" "no"

# Первая установка — тоже молчание и ноль архивов.
mv "$GODNS_CONFIG" "$FIX/config.saved2"
N_BEFORE=$(godns_backup_files | grep -c . )
OUT=$(godns_update_finish 2>&1)
N_AFTER=$(godns_backup_files | grep -c . )
is "первая установка ничего не печатает" "$OUT" ""
is "первая установка не плодит архивы"   "$(printf '%s' "$N_AFTER" | tr -d ' ')" "$(printf '%s' "$N_BEFORE" | tr -d ' ')"
mv "$FIX/config.saved2" "$GODNS_CONFIG"

# ═══════════════════════════════════════════════════════════════════════
head_ "обновление, которое не удалось"
# Схема из будущего: мигрировать нечем. Обновление обязано остановиться,
# НЕ проштамповав новую версию, — иначе повторный запуск решит, что всё
# позади, и настройки останутся в полумигрированном виде навсегда.
write_state "$SECRET" 8443 853
config_set godns_version "0.0.9" >/dev/null 2>&1
config_set schema "99"           >/dev/null 2>&1
GODNS_UPDATE_SHOWN="no"
if godns_update_finish >/dev/null 2>&1; then
    bad "провальная миграция выдана за успех"
else
    ok "провальная миграция остановила обновление"
fi
is "версия НЕ проштампована"      "$(config_get godns_version "")" "0.0.9"
is "настройки не тронуты"         "$(config_get doh_path "")"      "$SECRET"
is "пауза всё равно запрошена"    "$GODNS_UPDATE_SHOWN"            "yes"

head_ "пустой архив не выдаётся за копию"
# CLAUDE.md: «[ -s ] после каждой записи. И tar, и cat > при нехватке места
# завершаются успешно, оставляя пустой или обрезанный файл. Без проверки
# уверенность „откат возможен" ложная.» Сценария не было — мутация,
# снявшая проверку, проходила набор зелёной.
#
# Глушим tar так, как он ведёт себя на кончившемся диске: код 0, файл
# создан, размер ноль.
_real_tar_path=$(command -v tar)
tar() {
    case "$1" in
        czf) : > "$2"; return 0 ;;          # «успех» с пустым файлом
        *)   "$_real_tar_path" "$@" ;;
    esac
}
N_BEFORE=$(godns_backup_files | grep -c . )
# Логи идут в stderr, путь к архиву — в stdout. Смотрим ровно на stdout:
# «вернул путь» и есть утверждение «копия снята».
RC=0
OUT=$(godns_backup_create "empty-disk" 2>/dev/null) || RC=$?
N_AFTER=$(godns_backup_files | grep -c . )
if [ "$RC" -ne 0 ] && [ -z "$OUT" ]; then
    ok "пустой архив отвергнут"
else
    bad "пустой архив отвергнут" "rc=$RC, вернулось: ${OUT:-<пусто>}"
fi
is "пустой архив не попал в список копий" \
   "$(printf '%s' "$N_AFTER" | tr -d ' ')" "$(printf '%s' "$N_BEFORE" | tr -d ' ')"
unset -f tar
unset _real_tar_path

head_ "нет копии — нет миграции"
# Требование CLAUDE.md, до сих пор не доказанное ни одной проверкой:
# godns_update_finish обязан ОТКАЗАТЬСЯ мигрировать, если копию снять не
# удалось. Мутация «копия не снялась → продолжаем» проходила весь набор
# зелёной. Ломаем создание копии, а не каталог: так проверяется именно
# гейт, а не побочные эффекты недоступного диска.
write_state "$SECRET" 8443 853
config_set godns_version "0.0.9" >/dev/null 2>&1
config_set schema "0"            >/dev/null 2>&1
rm -f "${GODNS_ETC}/.pre-update-backup"
_real_backup_create=$(declare -f godns_backup_create)
godns_backup_create() { return 1; }
GODNS_UPDATE_SHOWN="no"
if godns_update_finish >/dev/null 2>&1; then
    bad "без копии обновление всё равно прошло"
else
    ok "без копии миграция не состоялась"
fi
is "версия НЕ проштампована без копии" "$(config_get godns_version "")" "0.0.9"
is "схема НЕ поднята без копии"        "$(config_get schema "")"        "0"
is "настройки не тронуты без копии"    "$(config_get doh_path "")"      "$SECRET"
eval "$_real_backup_create"
unset _real_backup_create

head_ "сохранение портов подключено к обновлению"
# Пара godns_preserve_existing / godns_restore_preserved проверялась только
# в изоляции: мутация, заменившая ВЫЗОВ restore внутри update_finish на «:»,
# оставалась зелёной. Это ровно тот случай goTelegram, где «сохранение не
# сохраняло ничего» — только уровнем выше, на подключении.
#
# Метод: конфиг, у которого порт и путь заданы, а миграция их затирает.
# Довезти их до конца может ТОЛЬКО restore.
write_state "$SECRET" 8443 853
config_set godns_version "0.0.9" >/dev/null 2>&1
config_set schema "0"            >/dev/null 2>&1
rm -f "${GODNS_ETC}/.pre-update-backup"
_real_normalize=$(declare -f godns_migrate_normalize)
godns_migrate_normalize() {
    # Затираем именно то, что клиенты держат в руках.
    config_set doh_path "wiped" >/dev/null 2>&1
    config_set doh_port "1"     >/dev/null 2>&1
    return 0
}
GODNS_UPDATE_SHOWN="no"
godns_update_finish >/dev/null 2>&1 || true
is "секретный путь возвращён после нормализации" "$(config_get doh_path "")" "$SECRET"
is "порт DoH возвращён после нормализации"       "$(config_get doh_port "")" "8443"
eval "$_real_normalize"
unset _real_normalize

head_ "обновление с ЛЮБОЙ прежней версии не теряет настроек"
# Заказчик просил проверять это в первую очередь: клиенты держат адрес,
# порт и секретный путь, и молчаливая их потеря при обновлении отваливает
# всех разом. Гоняем настоящий godns_update_finish с конфигами от разных
# версий и сверяем, что доехало.
for _v in 0.11.0 0.13.0 0.16.0 0.20.0; do
    _md="${FIX}/mig-${_v}"; mkdir -p "${_md}/etc"
    _old_dir="$GODNS_DIR"; _old_etc="$GODNS_ETC"; _old_cfg="$GODNS_CONFIG"
    export GODNS_DIR="$_md" GODNS_ETC="${_md}/etc" GODNS_CONFIG="${_md}/config.json"
    python3 - "$GODNS_CONFIG" "$_v" <<'MIGPY'
import json, sys
json.dump({"domain": "example.test", "doh_port": "4443",
           "doh_path": "KeepThisPath", "dot_port": "853", "dot_enabled": "yes",
           "policy": "roundrobin", "upstream_ids": "cloudflare,google",
           "upstreams": "cloudflare|dot|1.1.1.1|853|one.one.one.one|",
           "godns_version": sys.argv[2], "schema": "1"},
          open(sys.argv[1], "w"), indent=2)
MIGPY
    GODNS_UPDATE_SHOWN="no"
    (
        engine_is_running()  { return 1; }
        engine_is_installed(){ return 1; }
        godns_backup_create(){ touch "${_md}/a.tar.gz"; printf '%s' "${_md}/a.tar.gz"; }
        godns_update_finish
    ) >/dev/null 2>&1
    _got=$(python3 -c "
import json; c = json.load(open('${GODNS_CONFIG}'))
print('%s|%s|%s|%s|%s' % (c.get('godns_version'), c.get('doh_port'),
      c.get('doh_path'), c.get('dot_enabled'), c.get('upstream_ids')))")
    is "с ${_v}: версия, порт, путь, DoT и апстримы целы" \
       "${GODNS_VERSION}|4443|KeepThisPath|yes|cloudflare,google" "$_got"
    export GODNS_DIR="$_old_dir" GODNS_ETC="$_old_etc" GODNS_CONFIG="$_old_cfg"
done

head_ "служба лежит после обновления: сначала лечим новым кодом"
# ГРАБЛИ: обновление часто ставят именно потому, что старый рендер был
# сломан (Unbound на 127.0.0.1:53 ронял службу после зелёной проверки).
# Прежний finish, увидев лежащую службу, сразу предлагал ОТКАТ — то же
# самое лекарство, от которого уже плохо: старая версия несёт тот же баг.
# Правильный порядок: одна попытка engine_apply новым кодом, и только
# после провала — откат. Каждый случай в подоболочке: заглушки движка не
# должны пережить свою проверку.

# Случай 1: применение чинит службу → отката не предлагается.
_ufix=$(
    write_state "$SECRET" 8443 853 >/dev/null 2>&1
    config_set godns_version "0.0.9" >/dev/null 2>&1
    printf '%s\n' "/tmp/fake.tar.gz" > "${GODNS_ETC}/.pre-update-backup"
    _svc_state="down"
    engine_is_installed() { return 0; }
    engine_is_running()  { [ "$_svc_state" = "up" ]; }
    engine_apply()       { _svc_state="up"; return 0; }
    godns_rollback_offer() { printf 'ROLLBACK-OFFERED'; return 0; }
    GODNS_UPDATE_SHOWN=yes
    out=$(godns_update_finish 2>&1); rc=$?
    printf 'rc=%s|%s' "$rc" "$out"
)
case "$_ufix" in
    rc=0*) ok "лечение новым кодом: finish завершился успехом" ;;
    *)     bad "лечение новым кодом: finish упал" "$_ufix" ;;
esac
case "$_ufix" in
    *ROLLBACK-OFFERED*) bad "откат предложен, хотя служба поднялась" ;;
    *)                  ok "откат НЕ предлагался — служба поднята применением" ;;
esac

# Случай 2: применение не помогло → откат предлагается, finish падает.
_ubad=$(
    write_state "$SECRET" 8443 853 >/dev/null 2>&1
    config_set godns_version "0.0.9" >/dev/null 2>&1
    printf '%s\n' "/tmp/fake.tar.gz" > "${GODNS_ETC}/.pre-update-backup"
    engine_is_installed() { return 0; }
    engine_is_running()  { return 1; }
    engine_apply()       { return 1; }
    godns_rollback_offer() { printf 'ROLLBACK-OFFERED'; return 0; }
    GODNS_UPDATE_SHOWN=yes
    out=$(godns_update_finish 2>&1); rc=$?
    printf 'rc=%s|%s' "$rc" "$out"
)
case "$_ubad" in
    rc=1*|rc=[2-9]*) ok "не вылечилось: finish честно вернул провал" ;;
    *)               bad "не вылечилось, а finish зелёный" "$_ubad" ;;
esac
case "$_ubad" in
    *ROLLBACK-OFFERED*) ok "откат предложен после провала лечения" ;;
    *)                  bad "откат так и не предложен" "$_ubad" ;;
esac

# Случай 3: служба работает, конфигурация ни разу не применялась (нет
# applied_fingerprint) → engine_apply не зовётся вовсе: ни лечением, ни
# перерисовкой — применять на недонастроенной установке нечего.
_uok=$(
    write_state "$SECRET" 8443 853 >/dev/null 2>&1
    config_set godns_version "0.0.9" >/dev/null 2>&1
    engine_is_installed() { return 0; }
    engine_is_running()  { return 0; }
    engine_apply()       { printf 'APPLY-CALLED'; return 0; }
    GODNS_UPDATE_SHOWN=yes
    godns_update_finish 2>&1
)
case "$_uok" in
    *APPLY-CALLED*) bad "служба работала, конфиг не применялся — а применение дёрнули" ;;
    *)              ok "работающую неприменённую установку не трогаем" ;;
esac
config_set godns_version "$GODNS_VERSION" >/dev/null 2>&1
rm -f "${GODNS_ETC}/.pre-update-backup"

head_ "обновление перерисовывает конфиг движка новым кодом"
# ГРАБЛИ (0.32.0): finish чинил службу только когда она лежала, а живую не
# трогал — и исправление рендера (поштучный лимит вместо бана) не доезжало
# ни до кого: служба крутила старый файл, пока оператор что-нибудь не менял
# в меню. Теперь при каждом обновлении применённая конфигурация
# перерисовывается новым кодом — ровно один engine_apply, без гейта по
# версии; провал — не повод рвать обновление (старый конфиг остаётся).

# Случай 4: применялась и служба работает → ровно один engine_apply,
# штамп обновлён, откат не предлагается.
_ureap=$(
    write_state "$SECRET" 8443 853 >/dev/null 2>&1
    config_set godns_version "0.0.9" >/dev/null 2>&1
    config_set applied_fingerprint "stale-stamp-from-old-version" >/dev/null 2>&1
    engine_is_installed() { return 0; }
    engine_is_running()  { return 0; }
    engine_apply()       { printf 'APPLY-CALLED;'; return 0; }
    godns_rollback_offer() { printf 'ROLLBACK-OFFERED'; return 0; }
    GODNS_UPDATE_SHOWN=yes
    out=$(godns_update_finish 2>&1); rc=$?
    printf 'rc=%s|calls=%s|%s|stamp=%s|now=%s' "$rc" \
        "$(printf '%s' "$out" | grep -o 'APPLY-CALLED;' | wc -l | tr -d ' ')" \
        "$out" "$(config_get applied_fingerprint "")" "$(godns_config_fingerprint)"
)
case "$_ureap" in
    rc=0*) ok "перерисовка: finish завершился успехом" ;;
    *)     bad "перерисовка: finish упал" "$_ureap" ;;
esac
case "$_ureap" in
    *"calls=1|"*) ok "engine_apply вызван ровно один раз" ;;
    *)            bad "engine_apply вызван не один раз" "$_ureap" ;;
esac
case "$_ureap" in
    *ROLLBACK-OFFERED*) bad "откат предложен при успешной перерисовке" ;;
    *)                  ok "откат не предлагался" ;;
esac
_stamp=${_ureap##*stamp=}; _now=${_stamp##*now=}; _stamp=${_stamp%%|*}
is "штамп applied_fingerprint пересчитан новым кодом" "$_now" "$_stamp"

# Случай 5: перерисовка провалилась, служба жива на старом конфиге →
# обновление НЕ падает и откат не предлагается.
_ufail=$(
    write_state "$SECRET" 8443 853 >/dev/null 2>&1
    config_set godns_version "0.0.9" >/dev/null 2>&1
    config_set applied_fingerprint "stale-stamp-from-old-version" >/dev/null 2>&1
    engine_is_installed() { return 0; }
    engine_is_running()  { return 0; }
    engine_apply()       { printf 'APPLY-CALLED;'; return 1; }
    godns_rollback_offer() { printf 'ROLLBACK-OFFERED'; return 0; }
    GODNS_UPDATE_SHOWN=yes
    out=$(godns_update_finish 2>&1); rc=$?
    printf 'rc=%s|%s|stamp=%s' "$rc" "$out" "$(config_get applied_fingerprint "")"
)
case "$_ufail" in
    rc=0*) ok "провал перерисовки не рвёт обновление" ;;
    *)     bad "провал перерисовки уронил обновление" "$_ufail" ;;
esac
case "$_ufail" in
    *ROLLBACK-OFFERED*) bad "откат предложен, хотя служба работает" ;;
    *)                  ok "откат не предлагался: служба на старом конфиге жива" ;;
esac
case "$_ufail" in
    *"stamp=reapply-failed-"*) ok "после провала штамп помечен как непринятый (дрейф виден)" ;;
    *) bad "после провала дрейф спрятан" "$_ufail" ;;
esac

# Случай 7: служба ЛЕЖАЛА до обновления (нормализация штамп не трогает),
# перерисовка её подняла → штамп обязан быть пересчитан самой перерисовкой,
# иначе дашборд показывает «непринятые изменения» на только что применённой
# конфигурации (мутация ревью: убрать godns_mark_applied — всё зелёное).
_uheal=$(
    write_state "$SECRET" 8443 853 >/dev/null 2>&1
    config_set godns_version "0.0.9" >/dev/null 2>&1
    config_set applied_fingerprint "stale-stamp-from-old-version" >/dev/null 2>&1
    printf '%s\n' "/tmp/fake.tar.gz" > "${GODNS_ETC}/.pre-update-backup"
    _svc_state="down"
    engine_is_installed() { return 0; }
    engine_is_running()  { [ "$_svc_state" = "up" ]; }
    engine_apply()       { _svc_state="up"; printf 'APPLY-CALLED;'; return 0; }
    godns_rollback_offer() { printf 'ROLLBACK-OFFERED'; return 0; }
    GODNS_UPDATE_SHOWN=yes
    out=$(godns_update_finish 2>&1); rc=$?
    printf 'rc=%s|calls=%s|%s|stamp=%s|now=%s' "$rc" \
        "$(printf '%s' "$out" | grep -o 'APPLY-CALLED;' | wc -l | tr -d ' ')" \
        "$out" "$(config_get applied_fingerprint "")" "$(godns_config_fingerprint)"
)
case "$_uheal" in
    rc=0*"calls=1|"*) ok "служба лежала: перерисовка подняла её одной попыткой" ;;
    *) bad "лежащая служба: не одна попытка или провал" "$_uheal" ;;
esac
_stamp=${_uheal##*stamp=}; _now=${_stamp##*now=}; _stamp=${_stamp%%|*}
is "штамп пересчитан самой перерисовкой (нормализация лежащую службу не штампует)" "$_now" "$_stamp"
case "$_uheal" in
    *ROLLBACK-OFFERED*) bad "откат предложен, хотя служба поднята" ;;
    *)                  ok "откат не предлагался" ;;
esac

# Случай 8: служба лежит, перерисовка не помогла → engine_apply ровно ОДИН
# раз (лечение не повторяет ту же ошибку), затем откат и провал finish.
_udbl=$(
    write_state "$SECRET" 8443 853 >/dev/null 2>&1
    config_set godns_version "0.0.9" >/dev/null 2>&1
    config_set applied_fingerprint "stale-stamp-from-old-version" >/dev/null 2>&1
    printf '%s\n' "/tmp/fake.tar.gz" > "${GODNS_ETC}/.pre-update-backup"
    engine_is_installed() { return 0; }
    engine_is_running()  { return 1; }
    engine_apply()       { printf 'APPLY-CALLED;'; return 1; }
    godns_rollback_offer() { printf 'ROLLBACK-OFFERED'; return 0; }
    GODNS_UPDATE_SHOWN=yes
    out=$(godns_update_finish 2>&1); rc=$?
    printf 'rc=%s|calls=%s|%s' "$rc" \
        "$(printf '%s' "$out" | grep -o 'APPLY-CALLED;' | wc -l | tr -d ' ')" "$out"
)
case "$_udbl" in
    rc=[1-9]*"calls=1|"*ROLLBACK-OFFERED*) ok "лежащая служба и провал: одна попытка, затем откат" ;;
    *) bad "лежащая служба и провал: двойная попытка или нет отката" "$_udbl" ;;
esac

# Случай 9: конфиг движка не изменился → перерисовка штампует, но НЕ
# перезапускает: обновление ради README не рвёт соединения клиентов.
_usame=$(
    write_state "$SECRET" 8443 853 >/dev/null 2>&1
    config_set godns_version "0.0.9" >/dev/null 2>&1
    config_set applied_fingerprint "stale-stamp-from-old-version" >/dev/null 2>&1
    engine_is_installed() { return 0; }
    engine_is_running()  { return 0; }
    engine_config_is_current() { return 0; }
    engine_apply()       { printf 'APPLY-CALLED;'; return 0; }
    GODNS_UPDATE_SHOWN=yes
    out=$(godns_update_finish 2>&1); rc=$?
    printf 'rc=%s|%s|stamp=%s|now=%s' "$rc" "$out" "$(config_get applied_fingerprint "")" "$(godns_config_fingerprint)"
)
case "$_usame" in
    *APPLY-CALLED*) bad "конфиг не изменился, а службу перезапустили" ;;
    rc=0*)          ok "конфиг не изменился — без перезапуска" ;;
    *)              bad "конфиг не изменился: finish упал" "$_usame" ;;
esac
_stamp=${_usame##*stamp=}; _now=${_stamp##*now=}; _stamp=${_stamp%%|*}
is "без перезапуска штамп всё равно обновлён" "$_now" "$_stamp"

# Случай 6: движок не установлен → перерисовки нет.
_unoeng=$(
    write_state "$SECRET" 8443 853 >/dev/null 2>&1
    config_set godns_version "0.0.9" >/dev/null 2>&1
    config_set applied_fingerprint "stale-stamp-from-old-version" >/dev/null 2>&1
    engine_is_installed() { return 1; }
    engine_is_running()  { return 1; }
    engine_apply()       { printf 'APPLY-CALLED;'; return 0; }
    GODNS_UPDATE_SHOWN=yes
    godns_update_finish 2>&1
)
case "$_unoeng" in
    *APPLY-CALLED*) bad "движка нет, а перерисовку дёрнули" ;;
    *)              ok "без движка перерисовки нет" ;;
esac
config_set godns_version "$GODNS_VERSION" >/dev/null 2>&1
config_set applied_fingerprint "" >/dev/null 2>&1
rm -f "${GODNS_ETC}/.pre-update-backup"

head_ "откат"
rm -f "${GODNS_ETC}/.pre-update-backup"
if godns_rollback_offer >/dev/null 2>&1; then
    bad "откат без копии выдан за успех"
else
    ok "откат без копии честно отказывает"
fi

# Полный откат: копия есть, оператор согласился.
write_state "$SECRET" 8443 853
ROLLBACK_ARC=$(godns_backup_create "before-bad-update" 2>/dev/null)
write_state "BROKENbyUpdate" 7777 7778
if printf 'y\n' | godns_rollback_offer "$ROLLBACK_ARC" >/dev/null 2>&1; then
    ok "откат отработал"
else
    bad "откат отработал"
fi
is "настройки прежней версии вернулись" "$(config_get doh_path "")" "$SECRET"
is "порты прежней версии вернулись"     "$(config_get doh_port "")" "8443"

# Отказ от отката — не ошибка, и состояние трогать нельзя.
write_state "BROKENbyUpdate" 7777 7778
if printf 'n\n' | godns_rollback_offer "$ROLLBACK_ARC" >/dev/null 2>&1; then
    ok "отказ от отката — не ошибка"
else
    bad "отказ от отката — не ошибка"
fi
is "после отказа состояние не тронуто" "$(config_get doh_path "")" "BROKENbyUpdate"

# ═══════════════════════════════════════════════════════════════════════
head_ "восстановление, когда сертификата на этой машине нет"
# Архив, приехавший с другой машины: пути к сертификату указывают в никуда.
write_state "$SECRET" 8443 853
config_set cert_path "$FIX/absent/fullchain.pem" >/dev/null 2>&1
config_set key_path  "$FIX/absent/privkey.pem"   >/dev/null 2>&1
FOREIGN=$(godns_backup_create "foreign" 2>/dev/null)
write_state "WRONGpath999" 9999 9998
if godns_backup_restore "$FOREIGN" "" yes >/dev/null 2>&1; then
    ok "восстановление не падает без сертификата"
else
    bad "восстановление не падает без сертификата"
fi
is "настройки всё равно восстановлены" "$(config_get doh_path "")" "$SECRET"

# ═══════════════════════════════════════════════════════════════════════
head_ "шифрование архива"
if command -v openssl >/dev/null 2>&1; then
    write_state "$SECRET" 8443 853
    ENC=$(godns_backup_create "secured" "correct horse battery" 2>/dev/null)
    case "$ENC" in
        *.tar.gz.enc) ok "зашифрованный архив создан" ;;
        *) bad "зашифрованный архив создан" "получил [$ENC]" ;;
    esac
    # Секрет не должен быть виден в зашифрованном файле даже как подстрока.
    if LC_ALL=C grep -q "$SECRET" "$ENC" 2>/dev/null; then
        bad "секретный путь виден в зашифрованном архиве"
    else
        ok "секретного пути в зашифрованном архиве не видно"
    fi
    write_state "WRONGpath999" 9999 9998
    if godns_backup_restore "$ENC" "неверный пароль" yes >/dev/null 2>&1; then
        bad "неверный пароль принят"
    else
        ok "неверный пароль отклонён"
    fi
    is "после неудачи состояние не тронуто" "$(config_get doh_path "")" "WRONGpath999"
    if godns_backup_restore "$ENC" "correct horse battery" yes >/dev/null 2>&1; then
        ok "восстановление из зашифрованного архива"
    else
        bad "восстановление из зашифрованного архива"
    fi
    is "путь из зашифрованного архива вернулся" "$(config_get doh_path "")" "$SECRET"
else
    printf "  ${D}⊘ openssl не найден — шифрование не проверено${N}\n"
fi

# ═══════════════════════════════════════════════════════════════════════
head_ "расписание"
is "ежедневно"   "$(godns_backup_calendar daily)"   "*-*-* 04:20:00"
is "еженедельно" "$(godns_backup_calendar weekly)"  "Sun 04:20:00"
is "ежемесячно"  "$(godns_backup_calendar monthly)" "*-*-01 04:20:00"
is "выключено"   "$(godns_backup_calendar off)"     ""
if godns_backup_calendar quarterly >/dev/null 2>&1; then
    bad "неизвестная периодичность принята"
else
    ok "неизвестная периодичность отклонена"
fi

# Юнит, который молча ничего не делает, неотличим от рабочего: таймер
# срабатывает, служба «успешно» завершается, копий нет. Поэтому проверяем
# именно ТЕКСТ ExecStart, а не факт записи файла.
UNITS="$FIX/systemd"
GODNS_INSTALLER_DIR="$FIX/opt/godns-installer"
if _bk_render_units "$UNITS/godns-backup.service" "$UNITS/godns-backup.timer" "Sun 04:20:00"; then
    ok "юниты отрендерились"
else
    bad "юниты отрендерились"
fi
SVC_TXT=$(cat "$UNITS/godns-backup.service" 2>/dev/null)
case "$SVC_TXT" in
    *"${FIX}/opt/godns-installer/lib/backup.sh"*) ok "ExecStart ссылается на каталог программы" ;;
    *) bad "ExecStart ссылается на каталог программы" ;;
esac
case "$SVC_TXT" in
    *only-if-changed*) ok "таймер не плодит одинаковые копии" ;;
    *) bad "таймер не плодит одинаковые копии" ;;
esac
case "$SVC_TXT" in
    *godns_backup_prune*) ok "таймер сам подчищает старое" ;;
    *) bad "таймер сам подчищает старое" ;;
esac
# $(detect_language) обязан попасть в юнит ЛИТЕРАЛЬНО: раскройся он при
# записи, язык навсегда застыл бы на том, что стоял в момент настройки.
case "$SVC_TXT" in
    *'$(detect_language)'*) ok "выбор языка вычисляется при запуске, а не при записи" ;;
    *) bad "выбор языка вычисляется при запуске, а не при записи" ;;
esac
case "$SVC_TXT" in
    *'[Install]'*) bad "у oneshot-службы есть [Install]" ;;
    *) ok "у службы нет [Install] — её запускает таймер" ;;
esac
TMR_TXT=$(cat "$UNITS/godns-backup.timer" 2>/dev/null)
case "$TMR_TXT" in
    *"OnCalendar=Sun 04:20:00"*) ok "расписание попало в таймер" ;;
    *) bad "расписание попало в таймер" ;;
esac
case "$TMR_TXT" in
    *"Persistent=true"*) ok "пропущенный запуск наверстывается" ;;
    *) bad "пропущенный запуск наверстывается" ;;
esac

# ═══════════════════════════════════════════════════════════════════════
head_ "install.sh: контракт с библиотеками"
# install.sh подключает common/backup/update ИЗ СВЕЖЕРАСПАКОВАННОГО дерева,
# без certs/providers/engine и без i18n. Если какая-то из трёх начнёт
# требовать соседей, обновление сломается ровно у тех, кто обновляется, —
# и заметят это на живом сервере. Проверяем в отдельной оболочке под
# `set -euo pipefail`, как у загрузчика.
if env -u GODNS_LANG_DIR GODNS_DIR="$GODNS_DIR" GODNS_ETC="$GODNS_ETC" \
        GODNS_CONFIG="$GODNS_CONFIG" GODNS_BACKUP_DIR="$GODNS_BACKUP_DIR" \
        bash -c 'set -euo pipefail
                 . "'"$SCRIPT_DIR"'/lib/common.sh"
                 . "'"$SCRIPT_DIR"'/lib/backup.sh"
                 . "'"$SCRIPT_DIR"'/lib/update.sh"
                 godns_update_state >/dev/null
                 godns_version_lt 0.1.0 0.2.0' >/dev/null 2>&1; then
    ok "три библиотеки грузятся в одиночку под set -euo pipefail"
else
    bad "три библиотеки грузятся в одиночку под set -euo pipefail"
fi

# Порядок в install.sh: копия снимается ДО подмены каталога программы.
# Наоборот — и копию снимал бы новый код по уже новому состоянию, а старый
# lib/common.sh, из которого читается прежняя версия, был бы уже удалён.
PREP_LINE=$(grep -n 'godns_update_prepare' "$SCRIPT_DIR/install.sh" | head -n1 | cut -d: -f1)
# Подмена стала атомарной (сборка в .new и mv), поэтому якорь — сам mv,
# а не прежний `rm -rf`. Проверяем ОБА признака: копия раньше подмены и
# подмена именно через mv, а не через удаление живого каталога.
RM_LINE=$(grep -n 'mv "\$NEW_DIR" "\$INSTALL_DIR"' "$SCRIPT_DIR/install.sh" | head -n1 | cut -d: -f1)
if [ -n "$PREP_LINE" ] && [ -n "$RM_LINE" ] && [ "$PREP_LINE" -lt "$RM_LINE" ]; then
    ok "install.sh снимает копию до подмены каталога программы"
else
    bad "install.sh снимает копию до подмены каталога программы" "prepare:${PREP_LINE:-—} swap:${RM_LINE:-—}"
fi

# install.sh не имеет права трогать состояние оператора.
if grep -vE '^[[:space:]]*#' "$SCRIPT_DIR/install.sh" \
     | grep -qE 'rm -rf[^|;]*(/opt/godns[^-]|/etc/godns)'; then
    bad "install.sh удаляет состояние оператора"
else
    ok "install.sh не удаляет /opt/godns и /etc/godns"
if grep -qE '^[[:space:]]*rm -rf "\$INSTALL_DIR"[[:space:]]*$' "$SCRIPT_DIR/install.sh"; then
    bad "живой каталог программы сносится до готовности нового" "остался rm -rf \$INSTALL_DIR"
else
    ok "живой каталог не сносится до готовности нового"
fi
fi

# ═══════════════════════════════════════════════════════════════════════
head_ "инвариант: чужое не трогаем"
# Сертификаты только читаем. Ни создание, ни восстановление не имеют права
# что-либо записать в /etc/letsencrypt — это требование заказчика №1.
CERT_SUM_BEFORE=$(_bk_sha256 "$CERT_DIR/privkey.pem")
godns_backup_create "invariant" >/dev/null 2>&1
LAST=$(godns_backup_files | head -n1)
godns_backup_restore "$LAST" "" yes >/dev/null 2>&1
is "приватный ключ не изменился" "$(_bk_sha256 "$CERT_DIR/privkey.pem")" "$CERT_SUM_BEFORE"
if [ -f "$CERT_DIR/fullchain.pem" ]; then ok "сертификат на месте"; else bad "сертификат на месте"; fi

# В коде не должно быть ни одной записи в чужое хозяйство acme.sh/certbot.
if grep -vE '^[[:space:]]*#' "$SCRIPT_DIR/lib/backup.sh" "$SCRIPT_DIR/lib/update.sh" \
     | grep -qE 'install-cert|--reloadcmd|certbot (certonly|--expand)|renewal/.*\.conf'; then
    bad "в backup/update есть запись в чужое хозяйство сертификатов"
else
    ok "в backup/update нет записи в чужое хозяйство сертификатов"
fi

# Распаковка обязана идти без переноса владельца и прав: архив может быть
# чужим, а разворачиваем мы его от root.
if grep -q -- '--no-same-owner' "$SCRIPT_DIR/lib/backup.sh" \
   && grep -q -- '--no-same-permissions' "$SCRIPT_DIR/lib/backup.sh"; then
    ok "распаковка без --same-owner/--same-permissions"
else
    bad "распаковка без --same-owner/--same-permissions"
fi

# Пароль не должен уходить в argv: /proc/<pid>/cmdline читает любой.
if grep -vE '^[[:space:]]*#' "$SCRIPT_DIR/lib/backup.sh" | grep -qE '\-pass[[:space:]]+pass:'; then
    bad "пароль передаётся в argv openssl"
else
    ok "пароль передаётся файлом, не в argv"
fi

printf "\n${D}%s${N}\n" "────────────────────────────────────────────────────────────"
if [ "$FAIL" -eq 0 ]; then
    printf "  ${G}✓ все %d проверок пройдены${N}\n\n" "$PASS"; exit 0
fi
printf "  ${R}✗ провалено %d из %d${N}\n\n" "$FAIL" "$((PASS+FAIL))"; exit 1
