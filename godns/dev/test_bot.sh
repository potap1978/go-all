#!/bin/bash
# goDNS — Copyright (c) 2026 anten-ka. All rights reserved.
#
# goDNS — тесты Telegram-бота: чистая логика, без root и сети.
#
# Что покрыто и почему:
#   • bot.env: разбор и атомарная запись — значения с `$` и `=` обязаны
#     выживать (файл никогда не source-ится: bcrypt-подобное значение под
#     set -u уронило бы скрипт — этими граблями отличился .govless_credentials);
#   • токен: формат BotFather, отрицательные случаи;
#   • демон botd.py: привязка админа одноразовым кодом (неверный код,
#     закрытое окно, ОДНОразовость), отказ исполнять действия без
#     привязанного админа, валидация аргументов, снятие ANSI;
#   • контракт бот↔демон↔фасад: каждое действие бота есть у демона,
#     каждое действие демона есть в диспетчере actions.sh;
#   • свои апстримы: allowlist аргументов и окружения демона (путь DoH —
#     только окружением GODNS_CUSTOM_PATH, порт 70000 отвергается, а не
#     прижимается), разбор адреса в боте, регэкспы границ тождественны
#     регэкспам панели;
#   • юниты: имена только godns-*, бот без root, токена в юнитах нет;
#   • установка файлов идемпотентна, снятие не оставляет юнитов.
#
# NB: файл не под `set -e` — половина утверждений «это возвращает 1».

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

TEST_DIR=$(mktemp -d "${TMPDIR:-/tmp}/godns-bot-test.XXXXXX") || {
    echo "cannot create a temp dir" >&2; exit 1; }
trap 'rm -rf "$TEST_DIR"' EXIT

# Все записываемые пути — в песочницу ДО source, чтобы ни один тест не мог
# коснуться настоящих /opt/godns, /etc/godns или /etc/systemd.
export GODNS_DIR="${TEST_DIR}/opt"
export GODNS_ETC="${TEST_DIR}/etc"
export GODNS_CONFIG="${TEST_DIR}/opt/config.json"
export GODNS_INSTALLER_DIR="${SCRIPT_DIR}"
export GODNS_BOT_UNIT_DIR="${TEST_DIR}/units"
export GODNS_BOTD_SOCK="${TEST_DIR}/botd.sock"
mkdir -p "$GODNS_DIR" "$GODNS_ETC" "$GODNS_BOT_UNIT_DIR"

# ── Заглушки СИСТЕМНЫХ операций ────────────────────────────────────────
# ГРАБЛИ, стоившие живого бота: песочница выше закрывает ПУТИ, но не
# действия над системой. godns_bot_remove/webui_remove зовут настоящие
# `systemctl disable --now` и `userdel`. Прогон набора на РАБОЧЕМ сервере
# остановил работающего бота и удалил его пользователя — в auth.log
# осталось `userdel 'godns-bot'`, а юнит после этого падал с 217/USER.
#
# Заглушки обязаны стоять ДО source библиотек. Не глушим молча, а ПИШЕМ
# вызовы: тест продолжает проверять, что снятие пыталось отключить юниты,
# — иначе заглушка спрятала бы и настоящую поломку снятия.
SYS_CALLS="${TEST_DIR}/syscalls.log"
: > "$SYS_CALLS"
systemctl() { printf 'systemctl %s\n' "$*" >> "$SYS_CALLS"; return 0; }
userdel()   { printf 'userdel %s\n'   "$*" >> "$SYS_CALLS"; return 0; }
groupdel()  { printf 'groupdel %s\n'  "$*" >> "$SYS_CALLS"; return 0; }
deluser()   { printf 'deluser %s\n'   "$*" >> "$SYS_CALLS"; return 0; }
useradd()   { printf 'useradd %s\n'   "$*" >> "$SYS_CALLS"; return 0; }
adduser()   { printf 'adduser %s\n'   "$*" >> "$SYS_CALLS"; return 0; }
runuser()   { printf 'runuser %s\n'   "$*" >> "$SYS_CALLS"; return 0; }

# Заглушки i18n: тесты не поднимают таблицу строк (bash 3.2 её и не может).
t()  { printf '%s' "$1"; }
tf() { local _k="$1"; shift; printf '%s %s' "$_k" "$*"; }

# shellcheck source=lib/common.sh
. "${SCRIPT_DIR}/lib/common.sh"
# shellcheck source=lib/bot.sh
. "${SCRIPT_DIR}/lib/bot.sh"

PASS=0
FAIL=0

ok()  { PASS=$((PASS+1)); printf "  \033[0;32m✓\033[0m %s\n" "$1"; }
bad() {
    FAIL=$((FAIL+1))
    printf "  \033[0;31m✗\033[0m %s\n       expected: %s\n       got:      %s\n" \
        "$1" "$2" "$3"
}
section() { printf "\n\033[1m── %s\033[0m\n" "$1"; }

eq() { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1" "$2" "$3"; fi; }

rc() {
    local label="$1" want="$2" got=0
    shift 2
    "$@" >/dev/null 2>&1
    got=$?
    if [ "$got" = "$want" ]; then ok "$label"; else bad "$label" "rc $want" "rc $got"; fi
}
succeeds() { local l="$1"; shift; rc "$l" 0 "$@"; }
fails()    {
    local label="$1" got=0
    shift
    "$@" >/dev/null 2>&1
    got=$?
    if [ "$got" -ne 0 ]; then ok "$label"; else bad "$label" "non-zero rc" "rc 0"; fi
}

file_mode() { stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1" 2>/dev/null; }

PYBOT="${SCRIPT_DIR}/godns-bot"

# ═══════════════════════════════════════════════════════════════════════
section "bot.env — разбор и запись (никогда не source)"
# ═══════════════════════════════════════════════════════════════════════
eq "env_get на отсутствующем файле → умолчание" \
    "def" "$(godns_bot_env_get NOPE def)"

succeeds "env_set пишет ключ" godns_bot_env_set BOT_TOKEN "12345:abc"
eq "env_get возвращает записанное" "12345:abc" "$(godns_bot_env_get BOT_TOKEN)"
eq "права на bot.env — 600" "600" "$(file_mode "$GODNS_BOT_ENV_FILE")"

# Значение с `$` — как bcrypt-пароль в .govless_credentials: выжить обязано.
succeeds "env_set: значение с \$ принимается" \
    godns_bot_env_set TRICKY 'x$2a$10$QqZz'
eq "значение с \$ не раскрывается шелом" 'x$2a$10$QqZz' "$(godns_bot_env_get TRICKY)"

succeeds "env_set: значение с =" godns_bot_env_set EQ "a=b=c"
eq "значение с = целиком" "a=b=c" "$(godns_bot_env_get EQ)"

# Чужие ключи и комментарии выживают при обновлении.
echo "# comment survives" >> "$GODNS_BOT_ENV_FILE"
echo "WEBUI_KEEP=yes" >> "$GODNS_BOT_ENV_FILE"
succeeds "env_set поверх" godns_bot_env_set BOT_TOKEN "67890:xyz"
eq "обновление не плодит дублей" "1" \
    "$(grep -c '^BOT_TOKEN=' "$GODNS_BOT_ENV_FILE")"
eq "комментарий выжил" "1" "$(grep -c '^# comment survives' "$GODNS_BOT_ENV_FILE")"
eq "чужой ключ выжил" "yes" "$(godns_bot_env_get WEBUI_KEEP)"
eq "новое значение читается" "67890:xyz" "$(godns_bot_env_get BOT_TOKEN)"

# env_init идемпотентен: существующий токен не затирает.
succeeds "env_init" godns_bot_env_init
succeeds "env_init повторно" godns_bot_env_init
eq "env_init не тронул токен" "67890:xyz" "$(godns_bot_env_get BOT_TOKEN)"

if grep -nE '(^|[[:space:]])(source|\.)[[:space:]]+.*(bot\.env|BOT_ENV_FILE)' \
    "${SCRIPT_DIR}/lib/bot.sh" >/dev/null 2>&1; then
    bad "bot.env не source-ится в lib/bot.sh" "нет source" "source найден"
else
    ok "bot.env не source-ится в lib/bot.sh"
fi

# ═══════════════════════════════════════════════════════════════════════
section "токен BotFather — формат"
# Токены здесь ЗАВЕДОМО поддельные. Настоящий токен в репозитории
# считается скомпрометированным и подлежит отзыву у BotFather, а не
# удалению строки: он остаётся в истории git навсегда.
# ═══════════════════════════════════════════════════════════════════════
succeeds "валидный токен принят" \
    godns_bot_token_valid "1234567890:AAFakeBBBBccccDDDDeeeeFFFFgggg12345678"
fails "пустой токен отвергнут"        godns_bot_token_valid ""
fails "токен без двоеточия отвергнут" godns_bot_token_valid "1234567890AAFakeTokenForTestsOnly_0123456789xyz"
fails "короткий секрет отвергнут"     godns_bot_token_valid "1234567890:AAshort"
fails "пробел внутри отвергнут"       godns_bot_token_valid "1234567890:AAFakeToken ForTestsOnly_0123456789"
fails "кириллица отвергнута"          godns_bot_token_valid "1234567890:AAFakeTokenForTestsOnlyггггг12345678"
fails "мусор с URL отвергнут"         godns_bot_token_valid "https://t.me/godnstest_bot"

# ═══════════════════════════════════════════════════════════════════════
section "код привязки"
# ═══════════════════════════════════════════════════════════════════════
CODE1=$(godns_bot_gen_code) || CODE1=""
CODE2=$(godns_bot_gen_code) || CODE2=""
if [ "${#CODE1}" -ge 8 ]; then ok "код не короче 8 знаков"; else bad "код не короче 8 знаков" ">=8" "${#CODE1}"; fi
case "$CODE1" in
    *[!A-Za-z0-9]*) bad "код только из A-Za-z0-9" "буквы-цифры" "$CODE1" ;;
    *) ok "код только из A-Za-z0-9" ;;
esac
if [ "$CODE1" != "$CODE2" ]; then ok "коды не повторяются"; else bad "коды не повторяются" "разные" "одинаковые"; fi

# claim_code_show требует существующий bot.env (иначе бот не установлен).
succeeds "claim_code_show при наличии bot.env" godns_bot_claim_code_show
CLAIM_NOW=$(godns_bot_env_get ADMIN_CLAIM_TOKEN "")
if [ "${#CLAIM_NOW}" -ge 8 ]; then ok "код записан в bot.env"; else bad "код записан в bot.env" ">=8 знаков" "'$CLAIM_NOW'"; fi

# TTL: рядом с кодом обязан лечь срок годности в будущем.
CLAIM_EXP=$(godns_bot_env_get ADMIN_CLAIM_EXPIRES "0")
NOW_TS=$(date +%s)
case "$CLAIM_EXP" in ''|*[!0-9]*) CLAIM_EXP=0 ;; esac
if [ "$CLAIM_EXP" -gt "$NOW_TS" ] && [ "$CLAIM_EXP" -le $((NOW_TS + 3600)) ]; then
    ok "срок кода в будущем и разумен (TTL)"
else
    bad "срок кода в будущем и разумен (TTL)" "now < exp <= now+1h" "$CLAIM_EXP (now=$NOW_TS)"
fi
case "$(godns_bot_fmt_time "$NOW_TS")" in
    [0-9][0-9]:[0-9][0-9]) ok "fmt_time даёт ЧЧ:ММ на этой ОС" ;;
    *) bad "fmt_time даёт ЧЧ:ММ на этой ОС" "HH:MM" "$(godns_bot_fmt_time "$NOW_TS")" ;;
esac

# Отрицательный: без bot.env — отказ, а не пустой код.
_saved_env="$GODNS_BOT_ENV_FILE"
GODNS_BOT_ENV_FILE="${TEST_DIR}/nope/bot.env"
fails "claim_code_show без bot.env → отказ" godns_bot_claim_code_show
GODNS_BOT_ENV_FILE="$_saved_env"

# ═══════════════════════════════════════════════════════════════════════
section "systemd-юниты — рендер"
# ═══════════════════════════════════════════════════════════════════════
BOT_UNIT_TXT=$(godns_bot_render_bot_unit)
BOTD_UNIT_TXT=$(godns_bot_render_botd_unit)

case "$BOT_UNIT_TXT" in
    *"User=godns-bot"*) ok "бот работает НЕ от root (User=godns-bot)" ;;
    *) bad "бот работает НЕ от root (User=godns-bot)" "User=godns-bot" "нет" ;;
esac
case "$BOT_UNIT_TXT" in
    *"EnvironmentFile=${GODNS_BOT_ENV_FILE}"*) ok "токен приходит через EnvironmentFile" ;;
    *) bad "токен приходит через EnvironmentFile" "EnvironmentFile=..." "нет" ;;
esac
case "$BOT_UNIT_TXT" in
    *"NoNewPrivileges=yes"*) ok "у бота NoNewPrivileges" ;;
    *) bad "у бота NoNewPrivileges" "NoNewPrivileges=yes" "нет" ;;
esac
case "$BOT_UNIT_TXT" in
    *"SyslogIdentifier=godns-bot"*) ok "журнал бота под своим именем" ;;
    *) bad "журнал бота под своим именем" "SyslogIdentifier=godns-bot" "нет" ;;
esac
case "$BOTD_UNIT_TXT" in
    *"botd.py"*) ok "демон запускает botd.py" ;;
    *) bad "демон запускает botd.py" "botd.py в ExecStart" "нет" ;;
esac
case "$BOTD_UNIT_TXT" in
    *"GODNS_BOT_ACTIONS="*) ok "демону указан фасад actions.sh" ;;
    *) bad "демону указан фасад actions.sh" "GODNS_BOT_ACTIONS=" "нет" ;;
esac

# Токен уже лежит в bot.env — в юнитах его быть не должно.
TOK_NOW=$(godns_bot_env_get BOT_TOKEN "")
case "$BOT_UNIT_TXT$BOTD_UNIT_TXT" in
    *"$TOK_NOW"*) bad "токен не попадает в юниты" "нет токена" "токен в юните" ;;
    *) ok "токен не попадает в юниты" ;;
esac

# Ничего чужого и всё своё — с префиксом godns-.
case "$BOT_UNIT_TXT$BOTD_UNIT_TXT" in
    *govless*|*gotelegram*|*x-ui*|*telemt*|*gocdn*)
        bad "в юнитах нет чужих имён" "чисто" "чужое имя" ;;
    *) ok "в юнитах нет чужих имён" ;;
esac

# ═══════════════════════════════════════════════════════════════════════
section "установка файлов — идемпотентность"
# ═══════════════════════════════════════════════════════════════════════
succeeds "install_files (первый раз)"  godns_bot_install_files
succeeds "install_files (повторно)"    godns_bot_install_files
for f in bot.py botd.py i18n.py lang/ru.json lang/en.json; do
    if [ -f "${GODNS_BOT_APP}/$f" ]; then ok "скопирован $f"; else bad "скопирован $f" "есть" "нет"; fi
done
if [ -d "$GODNS_BOT_STATE_DIR" ]; then ok "каталог состояния создан"; else bad "каталог состояния создан" "есть" "нет"; fi

# Отрицательный: источник без bot.py — отказ.
_saved_src="$GODNS_BOT_SRC"
GODNS_BOT_SRC="${TEST_DIR}/empty-src"
mkdir -p "$GODNS_BOT_SRC"
fails "install_files без исходников → отказ" godns_bot_install_files
GODNS_BOT_SRC="$_saved_src"

# ═══════════════════════════════════════════════════════════════════════
section "запись юнитов и снятие"
# ═══════════════════════════════════════════════════════════════════════
succeeds "write_units" godns_bot_write_units
if [ -s "${GODNS_BOT_UNIT_DIR}/godns-bot.service" ] \
    && [ -s "${GODNS_BOT_UNIT_DIR}/godns-botd.service" ]; then
    ok "оба юнита записаны и непустые"
else
    bad "оба юнита записаны и непустые" "2 файла" "$(ls "$GODNS_BOT_UNIT_DIR" 2>/dev/null | tr '\n' ' ')"
fi
succeeds "godns_bot_installed видит установку" godns_bot_installed

# Снятие: подтверждения кормим («снять» = y, «удалить bot.env» = y).
printf 'y\ny\n' | godns_bot_remove >/dev/null 2>&1
REMOVE_RC=$?
eq "remove завершился rc 0" "0" "$REMOVE_RC"
LEFT=$(ls "$GODNS_BOT_UNIT_DIR" 2>/dev/null | grep -c 'godns-bot')
eq "юнитов после снятия не осталось" "0" "$LEFT"
if [ ! -d "$GODNS_BOT_HOME" ]; then ok "каталог бота удалён"; else bad "каталог бота удалён" "нет каталога" "есть"; fi
if [ ! -f "$GODNS_BOT_ENV_FILE" ]; then ok "bot.env удалён по согласию"; else bad "bot.env удалён по согласию" "нет файла" "есть"; fi

# Повторное снятие на чистой машине — тихий no-op, не падение.
rc "remove повторно → rc 0 (нечего снимать)" 0 godns_bot_remove
fails "godns_bot_installed после снятия → нет" godns_bot_installed

# Отказ оператора: ничего не удаляется.
succeeds "write_units снова" godns_bot_write_units
printf 'n\n' | godns_bot_remove >/dev/null 2>&1
if [ -s "${GODNS_BOT_UNIT_DIR}/godns-bot.service" ]; then
    ok "отказ в confirm ничего не удалил"
else
    bad "отказ в confirm ничего не удалил" "юнит на месте" "удалён"
fi
printf 'y\nn\n' | godns_bot_remove >/dev/null 2>&1
if [ ! -s "${GODNS_BOT_UNIT_DIR}/godns-bot.service" ]; then
    ok "снятие с сохранением bot.env убрало юниты"
else
    bad "снятие с сохранением bot.env убрало юниты" "юнитов нет" "юнит остался"
fi

# ═══════════════════════════════════════════════════════════════════════
section "botd.py — привязка админа одноразовым кодом"
# ═══════════════════════════════════════════════════════════════════════
BOTD_ENV="${TEST_DIR}/claim.env"
BOTD_AUDIT="${TEST_DIR}/audit.log"

py_claim() {   # <env-файл> <python-выражение с claim/env-функциями>
    GODNS_BOT_ENV="$1" GODNS_BOT_AUDIT="$BOTD_AUDIT" \
    python3 -c "
import sys
sys.path.insert(0, '${PYBOT}')
import botd
$2
"
}

# Импорт без побочных эффектов: ни сокета, ни файлов.
succeeds "import botd — без побочных эффектов" \
    py_claim "$BOTD_ENV" "pass"
if [ ! -e "$GODNS_BOTD_SOCK" ]; then ok "сокет при импорте не создаётся"; else bad "сокет при импорте не создаётся" "нет сокета" "есть"; fi

printf 'BOT_TOKEN=tt\nADMIN_IDS=\nADMIN_CLAIM_TOKEN=\n' > "$BOTD_ENV"
eq "claim при пустом коде в env → closed" "closed" \
    "$(py_claim "$BOTD_ENV" "print(botd.claim_admin('whatever', 111)['reason'])")"

printf 'BOT_TOKEN=tt\nADMIN_IDS=\nADMIN_CLAIM_TOKEN=SeCrEt123\nADMIN_CLAIM_EXPIRES=9999999999\n' > "$BOTD_ENV"
eq "неверный код → wrong_code" "wrong_code" \
    "$(py_claim "$BOTD_ENV" "print(botd.claim_admin('nope', 111)['reason'])")"
eq "неверный код не привязал никого" "ADMIN_IDS=" \
    "$(grep '^ADMIN_IDS=' "$BOTD_ENV")"
eq "кривой tg_id → bad_id" "bad_id" \
    "$(py_claim "$BOTD_ENV" "print(botd.claim_admin('SeCrEt123', 0)['reason'])")"

eq "верный код → ok + список админов" "True [111]" \
    "$(py_claim "$BOTD_ENV" "r = botd.claim_admin('SeCrEt123', 111); print(r['ok'], r['admins'])")"
eq "код погашен после успеха" "ADMIN_CLAIM_TOKEN=" \
    "$(grep '^ADMIN_CLAIM_TOKEN=' "$BOTD_ENV")"
eq "срок кода погашен после успеха" "ADMIN_CLAIM_EXPIRES=" \
    "$(grep '^ADMIN_CLAIM_EXPIRES=' "$BOTD_ENV")"
eq "админ записан в env" "ADMIN_IDS=111" \
    "$(grep '^ADMIN_IDS=' "$BOTD_ENV")"

# ОДНОразовость: тот же код второй раз не работает — окно закрыто.
eq "тот же код повторно → closed" "closed" \
    "$(py_claim "$BOTD_ENV" "print(botd.claim_admin('SeCrEt123', 222)['reason'])")"

# ═══════════════════════════════════════════════════════════════════════
section "botd.py — исторические имена ключа админов"
# ═══════════════════════════════════════════════════════════════════════
# ГРАБЛИ, пойманные на живом сервере: ключ переименовали с ALLOWED_IDS на
# ADMIN_IDS, env_write бережно сохранил незнакомую строку, а читатель её
# не искал. Демон отвечал admins: [] при заполненном файле — бот жив,
# отвечает и НЕ СЛУШАЕТСЯ владельца. Отказ немой, поэтому закреплён здесь.
LEG_ENV="${TEST_DIR}/legacy.env"

printf 'BOT_TOKEN=tt\nALLOWED_IDS=424242\n' > "$LEG_ENV"
eq "старый ALLOWED_IDS подхватывается" "[424242]" \
    "$(py_claim "$LEG_ENV" "print(botd.admins_from_env(botd.env_read()))")"

printf 'BOT_TOKEN=tt\nADMIN_IDS=777\nALLOWED_IDS=424242\n' > "$LEG_ENV"
eq "нынешний ADMIN_IDS главнее старого" "[777]" \
    "$(py_claim "$LEG_ENV" "print(botd.admins_from_env(botd.env_read()))")"

printf 'BOT_TOKEN=tt\nADMIN_IDS=\nALLOWED_IDS=\n' > "$LEG_ENV"
eq "пустые оба → админов нет" "[]" \
    "$(py_claim "$LEG_ENV" "print(botd.admins_from_env(botd.env_read()))")"

printf 'BOT_TOKEN=tt\nALLOWED_IDS=не-число\n' > "$LEG_ENV"
eq "мусор вместо ID → админов нет" "[]" \
    "$(py_claim "$LEG_ENV" "print(botd.admins_from_env(botd.env_read()))")"

# ═══════════════════════════════════════════════════════════════════════
section "botd.py — TTL кода привязки"
# ═══════════════════════════════════════════════════════════════════════
# Истёкший код — отдельная причина: «выпусти новый», а не «ты опечатался».
printf 'BOT_TOKEN=tt\nADMIN_IDS=\nADMIN_CLAIM_TOKEN=OldCode99\nADMIN_CLAIM_EXPIRES=1000\n' > "$BOTD_ENV"
eq "истёкший код → expired (даже верный)" "expired" \
    "$(py_claim "$BOTD_ENV" "print(botd.claim_admin('OldCode99', 111)['reason'])")"
eq "истёкший код гасится в env" "ADMIN_CLAIM_TOKEN=" \
    "$(grep '^ADMIN_CLAIM_TOKEN=' "$BOTD_ENV")"

# Fail-closed: код без срока = истёкший, а не вечный.
printf 'BOT_TOKEN=tt\nADMIN_IDS=\nADMIN_CLAIM_TOKEN=NoTtl1234\n' > "$BOTD_ENV"
eq "код без ADMIN_CLAIM_EXPIRES → expired" "expired" \
    "$(py_claim "$BOTD_ENV" "print(botd.claim_admin('NoTtl1234', 111)['reason'])")"

# claim_is_open согласован с TTL (его показывает ping как claim_open).
printf 'ADMIN_CLAIM_TOKEN=Live12345\nADMIN_CLAIM_EXPIRES=9999999999\n' > "$BOTD_ENV"
eq "claim_is_open: живой код → True" "True" \
    "$(py_claim "$BOTD_ENV" "print(botd.claim_is_open(botd.env_read()))")"
printf 'ADMIN_CLAIM_TOKEN=Dead12345\nADMIN_CLAIM_EXPIRES=1000\n' > "$BOTD_ENV"
eq "claim_is_open: истёкший код → False" "False" \
    "$(py_claim "$BOTD_ENV" "print(botd.claim_is_open(botd.env_read()))")"

# ═══════════════════════════════════════════════════════════════════════
section "botd.py — тормоз перебора кодов"
# ═══════════════════════════════════════════════════════════════════════
# Один процесс — одна серия: состояние счётчика живёт в памяти демона.
RL_ENV="${TEST_DIR}/ratelimit.env"
printf 'BOT_TOKEN=tt\nADMIN_IDS=\nADMIN_CLAIM_TOKEN=RightCode1\nADMIN_CLAIM_EXPIRES=9999999999\n' > "$RL_ENV"
RL_OUT=$(GODNS_BOT_ENV="$RL_ENV" GODNS_BOT_AUDIT="$BOTD_AUDIT" python3 -c "
import sys
sys.path.insert(0, '${PYBOT}')
import botd
# 5 промахов подряд взводят замок…
for i in range(botd.CLAIM_FAIL_LIMIT):
    r = botd.claim_admin('miss%d' % i, 111)
    assert r['reason'] == 'wrong_code', r
# …и дальше отлуп получает даже ВЕРНЫЙ код.
r = botd.claim_admin('RightCode1', 111)
assert r['reason'] == 'rate_limited', r
# Замок истёк (симулируем время) — верный код проходит и сбрасывает счётчик.
botd._CLAIM_STATE['until'] = 0.0
r = botd.claim_admin('RightCode1', 111)
assert r['ok'] and r['admins'] == [111], r
assert botd._CLAIM_STATE['fails'] == 0
print('ratelimit-ok')
") || RL_OUT="fail"
eq "5 промахов → замок; замок держит верный код; успех сбрасывает" \
    "ratelimit-ok" "$RL_OUT"
# Промахи видны оператору: каждый — строка в аудите.
RL_FAILS=$(grep -c '"event": "claim_failed"' "$BOTD_AUDIT" 2>/dev/null)
if [ "${RL_FAILS:-0}" -ge 6 ]; then
    ok "неудачные попытки пишутся в аудит (${RL_FAILS})"
else
    bad "неудачные попытки пишутся в аудит" ">=6 записей" "${RL_FAILS:-0}"
fi

# ═══════════════════════════════════════════════════════════════════════
section "botd.py — маскировка секретов в выводе"
# ═══════════════════════════════════════════════════════════════════════
# Ключ консоли dnsdist и секретный путь DoH не должны уезжать в Telegram:
# история переписки хранится на чужих серверах вечно.
SCRUB_CFG="${TEST_DIR}/scrub-config.json"
printf '{"console_key":"rRJNkSecretKey0=","doh_path":"AbCdSecret12"}' > "$SCRUB_CFG"

py_scrub() {   # <python-выражение>
    GODNS_CONFIG="$SCRUB_CFG" python3 -c "
import sys
sys.path.insert(0, '${PYBOT}')
import botd
$1
"
}

eq "setKey('…') маскируется" "setKey('***')" \
    "$(py_scrub "print(botd.scrub_secrets(\"setKey('rRJNkSecretKey0=')\"))")"
eq "setKey(\"…\") маскируется" "setKey('***')" \
    "$(py_scrub "print(botd.scrub_secrets('setKey(\"whatever\")'))")"
eq "значение ключа консоли маскируется и вне setKey" "err: *** rejected" \
    "$(py_scrub "print(botd.scrub_secrets('err: rRJNkSecretKey0= rejected'))")"
eq "путь DoH маскируется при mask_path" "GET /*** 404" \
    "$(py_scrub "print(botd.scrub_secrets('GET /AbCdSecret12 404', mask_path=True))")"
eq "без mask_path путь остаётся (это адрес для клиентов)" "url /AbCdSecret12" \
    "$(py_scrub "print(botd.scrub_secrets('url /AbCdSecret12'))")"
eq "показ конфига — в списке маскировки пути" "True" \
    "$(py_scrub "print('svc_showconf' in botd.MASK_PATH_ACTIONS)")"

# ═══════════════════════════════════════════════════════════════════════
section "botd.py — без админа действия не исполняются"
# ═══════════════════════════════════════════════════════════════════════
STUB_ACTIONS="${TEST_DIR}/stub-actions.sh"
# Заглушка печатает окружение ОТДЕЛЬНО от args: так утечка пути DoH в
# argv (см. секцию «свои апстримы») была бы видна, а не спрятана.
cat > "$STUB_ACTIONS" <<'STUB'
#!/bin/bash
echo "stub-ran action=$1 args=$* env=${GODNS_CUSTOM_PATH-unset}"
exit 0
STUB
chmod +x "$STUB_ACTIONS"

py_rpc() {   # <env-файл> <json-запрос>  → печатает ответ JSON
    GODNS_BOT_ENV="$1" GODNS_BOT_AUDIT="$BOTD_AUDIT" \
    GODNS_BOT_ACTIONS="$STUB_ACTIONS" \
    python3 -c "
import asyncio, json, sys
sys.path.insert(0, '${PYBOT}')
import botd
resp = asyncio.run(botd.handle_request(json.loads('''$2''')))
print(json.dumps(resp, ensure_ascii=False))
"
}

NOADMIN_ENV="${TEST_DIR}/noadmin.env"
printf 'BOT_TOKEN=tt\nADMIN_IDS=\nADMIN_CLAIM_TOKEN=\n' > "$NOADMIN_ENV"
OUT=$(py_rpc "$NOADMIN_ENV" '{"jsonrpc":"2.0","id":1,"method":"action","params":{"name":"panel","tg_id":999}}')
case "$OUT" in
    *'"code": 403'*) ok "без привязанного админа — отказ 403" ;;
    *) bad "без привязанного админа — отказ 403" "403" "$OUT" ;;
esac

ADMIN_ENV="${TEST_DIR}/admin.env"
printf 'BOT_TOKEN=tt\nADMIN_IDS=111\nADMIN_CLAIM_TOKEN=\n' > "$ADMIN_ENV"
OUT=$(py_rpc "$ADMIN_ENV" '{"jsonrpc":"2.0","id":2,"method":"action","params":{"name":"panel","tg_id":111}}')
case "$OUT" in
    *stub-ran*) ok "админу действие исполняется (через фасад)" ;;
    *) bad "админу действие исполняется (через фасад)" "stub-ran" "$OUT" ;;
esac
# Чужой id, даже «соседний» — отказ.
OUT=$(py_rpc "$ADMIN_ENV" '{"jsonrpc":"2.0","id":3,"method":"action","params":{"name":"panel","tg_id":112}}')
case "$OUT" in
    *'"code": 403'*) ok "не-админу — отказ 403" ;;
    *) bad "не-админу — отказ 403" "403" "$OUT" ;;
esac
# Неизвестное действие — ошибка валидации, не запуск.
OUT=$(py_rpc "$ADMIN_ENV" '{"jsonrpc":"2.0","id":4,"method":"action","params":{"name":"rm_rf_slash","tg_id":111}}')
case "$OUT" in
    *'"code": -32602'*) ok "неизвестное действие отвергнуто" ;;
    *) bad "неизвестное действие отвергнуто" "-32602" "$OUT" ;;
esac
# Кривой аргумент — тоже.
OUT=$(py_rpc "$ADMIN_ENV" '{"jsonrpc":"2.0","id":5,"method":"action","params":{"name":"policy_set","args":["evil"],"tg_id":111}}')
case "$OUT" in
    *'"code": -32602'*) ok "кривой аргумент policy_set отвергнут" ;;
    *) bad "кривой аргумент policy_set отвергнут" "-32602" "$OUT" ;;
esac
# Неизвестный метод.
OUT=$(py_rpc "$ADMIN_ENV" '{"jsonrpc":"2.0","id":6,"method":"shutdown","params":{}}')
case "$OUT" in
    *'"code": -32601'*) ok "неизвестный метод отвергнут" ;;
    *) bad "неизвестный метод отвергнут" "-32601" "$OUT" ;;
esac
# svc_logs: аргумент прижимается к границам.
OUT=$(py_rpc "$ADMIN_ENV" '{"jsonrpc":"2.0","id":7,"method":"action","params":{"name":"svc_logs","args":["99999"],"tg_id":111}}')
case "$OUT" in
    *"args=svc_logs 200"*) ok "svc_logs: 99999 прижат к 200" ;;
    *) bad "svc_logs: 99999 прижат к 200" "args=svc_logs 200" "$OUT" ;;
esac

# ping отвечает и без админов (бот показывает «демон жив»).
OUT=$(py_rpc "$NOADMIN_ENV" '{"jsonrpc":"2.0","id":8,"method":"ping","params":{}}')
case "$OUT" in
    *'"pong": true'*) ok "ping отвечает без авторизации" ;;
    *) bad "ping отвечает без авторизации" "pong" "$OUT" ;;
esac

# ═══════════════════════════════════════════════════════════════════════
section "botd.py — свои апстримы: allowlist аргументов и окружения"
# ═══════════════════════════════════════════════════════════════════════
# Путь DoH своего апстрима может быть чужим секретом (второй goDNS как
# апстрим), а argv читает любой локальный пользователь через
# /proc/<pid>/cmdline. Поэтому путь едет ТОЛЬКО окружением
# GODNS_CUSTOM_PATH и только для двух действий из ACTION_ENV; всё прочее
# в env — отказ -32602, а не молчаливый пропуск. Порт вне диапазона —
# тоже отказ: _int_range прижал бы 70000 к 65535, а это другой сервер.
cu_rpc() {   # <json params> → ответ демона (от админа 111)
    py_rpc "$ADMIN_ENV" "{\"jsonrpc\":\"2.0\",\"id\":9,\"method\":\"action\",\"params\":$1}"
}
cu_rejected() {   # <метка> <json params>: ждём -32602
    local out; out=$(cu_rpc "$2")
    case "$out" in
        *'"code": -32602'*) ok "$1" ;;
        *) bad "$1" "-32602" "$out" ;;
    esac
}
cu_ran() {   # <метка> <json params> <подстрока в ответе>
    local out; out=$(cu_rpc "$2")
    case "$out" in
        *"$3"*) ok "$1" ;;
        *) bad "$1" "$3" "$out" ;;
    esac
}

cu_ran "custom_probe: путь DoH едет окружением, в argv его нет" \
    '{"name":"custom_probe","args":["dot","one.one.one.one","853","no"],"tg_id":111,"env":{"GODNS_CUSTOM_PATH":"/dns-query"}}' \
    "args=custom_probe dot one.one.one.one 853 no env=/dns-query"
cu_ran "custom_probe без env → окружение не задано" \
    '{"name":"custom_probe","args":["dot","one.one.one.one","","no"],"tg_id":111}' \
    "args=custom_probe dot one.one.one.one  no env=unset"
cu_rejected "custom_probe: протокол quic" \
    '{"name":"custom_probe","args":["quic","one.one.one.one","853","no"],"tg_id":111}'
cu_rejected "custom_probe: host с «;»" \
    '{"name":"custom_probe","args":["dot","a;b","853","no"],"tg_id":111}'
cu_rejected "custom_probe: host пустой" \
    '{"name":"custom_probe","args":["dot","","853","no"],"tg_id":111}'
cu_rejected "custom_probe: host с пробелом" \
    '{"name":"custom_probe","args":["dot","a b","853","no"],"tg_id":111}'
# `$` в регэкспе Python прощает завершающий перевод строки — match() его
# пропустил бы; демон обязан сверять fullmatch.
cu_rejected "custom_probe: host с переводом строки в хвосте" \
    '{"name":"custom_probe","args":["dot","dns.google\\n","853","no"],"tg_id":111}'
cu_rejected "custom_probe: порт 70000 — отвергнут, не прижат" \
    '{"name":"custom_probe","args":["dot","one.one.one.one","70000","no"],"tg_id":111}'
cu_rejected "custom_probe: порт 0" \
    '{"name":"custom_probe","args":["dot","one.one.one.one","0","no"],"tg_id":111}'
cu_rejected "custom_probe: порт abc" \
    '{"name":"custom_probe","args":["dot","one.one.one.one","abc","no"],"tg_id":111}'
cu_rejected "custom_probe: порт с ведущим нулём" \
    '{"name":"custom_probe","args":["dot","one.one.one.one","0853","no"],"tg_id":111}'
cu_rejected "custom_probe: ack=maybe" \
    '{"name":"custom_probe","args":["dot","one.one.one.one","853","maybe"],"tg_id":111}'
cu_rejected "custom_probe: лишний пятый аргумент" \
    '{"name":"custom_probe","args":["dot","one.one.one.one","853","no","x"],"tg_id":111}'
cu_rejected "custom_probe: три аргумента вместо четырёх" \
    '{"name":"custom_probe","args":["dot","one.one.one.one","853"],"tg_id":111}'
cu_rejected "env: путь с шаблоном /{id}" \
    '{"name":"custom_probe","args":["doh","dns.google","443","no"],"tg_id":111,"env":{"GODNS_CUSTOM_PATH":"/{id}"}}'
cu_rejected "env: путь без ведущего «/»" \
    '{"name":"custom_probe","args":["doh","dns.google","443","no"],"tg_id":111,"env":{"GODNS_CUSTOM_PATH":"dns-query"}}'
cu_rejected "env: чужой ключ PATH" \
    '{"name":"custom_probe","args":["dot","dns.google","853","no"],"tg_id":111,"env":{"PATH":"/tmp"}}'
cu_rejected "env: чужой ключ рядом с разрешённым" \
    '{"name":"custom_probe","args":["dot","dns.google","853","no"],"tg_id":111,"env":{"GODNS_CUSTOM_PATH":"/dns-query","GODNS_BOT_LIB":"/tmp"}}'
cu_rejected "env: не объект (строка)" \
    '{"name":"custom_probe","args":["dot","dns.google","853","no"],"tg_id":111,"env":"/dns-query"}'
cu_rejected "env: не объект (список)" \
    '{"name":"custom_probe","args":["dot","dns.google","853","no"],"tg_id":111,"env":["GODNS_CUSTOM_PATH"]}'
cu_rejected "env: значение не строка" \
    '{"name":"custom_probe","args":["dot","dns.google","853","no"],"tg_id":111,"env":{"GODNS_CUSTOM_PATH":1}}'
cu_rejected "env у custom_remove — окружение ему не положено" \
    '{"name":"custom_remove","args":["custom-x"],"tg_id":111,"env":{"GODNS_CUSTOM_PATH":"/dns-query"}}'
cu_rejected "env у panel — тоже" \
    '{"name":"panel","tg_id":111,"env":{"GODNS_CUSTOM_PATH":"/x"}}'

cu_ran "custom_add: полный argv, путь окружением" \
    '{"name":"custom_add","args":["doh","dns.example.org","8443","My DoH","yes","no"],"tg_id":111,"env":{"GODNS_CUSTOM_PATH":"/secret-path"}}' \
    "args=custom_add doh dns.example.org 8443 My DoH yes no env=/secret-path"
# Кириллица — \u-эскейпами: так тест не зависит от локали, в которой
# python3 читает свой аргумент -c.
NAME40=$(printf '\\u044f%.0s' $(seq 1 40))
NAME41=$(printf '\\u044f%.0s' $(seq 1 41))
cu_ran "custom_add: кириллическое имя в 40 знаков проходит" \
    "{\"name\":\"custom_add\",\"args\":[\"dot\",\"one.one.one.one\",\"853\",\"${NAME40}\",\"no\",\"no\"],\"tg_id\":111}" \
    "stub-ran action=custom_add"
cu_rejected "custom_add: имя из 41 знака" \
    "{\"name\":\"custom_add\",\"args\":[\"dot\",\"one.one.one.one\",\"853\",\"${NAME41}\",\"no\",\"no\"],\"tg_id\":111}"
cu_rejected "custom_add: имя с «|»" \
    '{"name":"custom_add","args":["dot","one.one.one.one","853","a|b","no","no"],"tg_id":111}'
cu_rejected "custom_add: имя с «;»" \
    '{"name":"custom_add","args":["dot","one.one.one.one","853","a;b","no","no"],"tg_id":111}'
cu_rejected "custom_add: enable=да (строгий yes/no)" \
    '{"name":"custom_add","args":["dot","one.one.one.one","853","x","\u0434\u0430","no"],"tg_id":111}'
cu_rejected "custom_add: пять аргументов вместо шести" \
    '{"name":"custom_add","args":["dot","one.one.one.one","853","x","no"],"tg_id":111}'

cu_rejected "custom_remove: id из каталога (cloudflare)" \
    '{"name":"custom_remove","args":["cloudflare"],"tg_id":111}'
cu_rejected "custom_remove: custom- без slug" \
    '{"name":"custom_remove","args":["custom-"],"tg_id":111}'
cu_rejected "custom_remove: обход пути custom-x/../y" \
    '{"name":"custom_remove","args":["custom-x/../y"],"tg_id":111}'
cu_rejected "custom_remove: заглавные буквы в id" \
    '{"name":"custom_remove","args":["custom-X"],"tg_id":111}'
cu_rejected "custom_remove: без аргумента" \
    '{"name":"custom_remove","tg_id":111}'
cu_ran "custom_remove: custom-dns-google-2 исполняется" \
    '{"name":"custom_remove","args":["custom-dns-google-2"],"tg_id":111}' \
    "args=custom_remove custom-dns-google-2 env=unset"
cu_ran "custom_list: без аргументов исполняется" \
    '{"name":"custom_list","tg_id":111}' "stub-ran action=custom_list"
cu_rejected "custom_list: аргумент не положен" \
    '{"name":"custom_list","args":["x"],"tg_id":111}'

# Аудит: ИМЕНА ключей окружения — да, значение пути — никогда.
if grep '"action": "custom_add"' "$BOTD_AUDIT" 2>/dev/null \
    | grep -q '"env_keys": \["GODNS_CUSTOM_PATH"\]'; then
    ok "аудит custom_add: env_keys с именем ключа"
else
    bad "аудит custom_add: env_keys с именем ключа" \
        '"env_keys": ["GODNS_CUSTOM_PATH"]' \
        "$(grep '"action": "custom_add"' "$BOTD_AUDIT" 2>/dev/null | tail -1)"
fi
if grep -qE 'secret-path|dns-query' "$BOTD_AUDIT" 2>/dev/null; then
    bad "аудит не содержит путь DoH" "ни /secret-path, ни /dns-query" \
        "$(grep -m1 -E 'secret-path|dns-query' "$BOTD_AUDIT")"
else
    ok "аудит не содержит путь DoH"
fi

# ═══════════════════════════════════════════════════════════════════════
section "bot.py — адрес мини-приложения не теряется при отказе"
# ═══════════════════════════════════════════════════════════════════════
# ГРАБЛИ, пойманные живым нажатием кнопки в Telegram: на старте бот
# спрашивал адрес панели от имени user_id=0. Нулевой идентификатор
# админом не бывает, демон отвечает 403 — и эта НЕУДАЧА клалась в кэш на
# те же пять минут, что и удача. Итог: первые пять минут после каждого
# перезапуска кнопка «Мини-приложение» пропадала из меню совсем.
WEBAPP_OUT=$(python3 - "$PYBOT" <<'PYEOF'
import ast, asyncio, sys, pathlib, time

src = pathlib.Path(sys.argv[1], "bot.py").read_text()
tree = ast.parse(src)

class RpcError(Exception): pass
class RpcTransportError(Exception): pass

ns = {"time": time, "RpcError": RpcError, "RpcTransportError": RpcTransportError}
want_fn = {"_webapp_url"}
want_var = {"_WEBAPP_NEVER", "_WEBAPP_CACHE", "WEBAPP_TTL_OK", "WEBAPP_TTL_FAIL"}
got = set()
for n in tree.body:
    if isinstance(n, ast.Assign):
        for t in n.targets:
            if getattr(t, "id", "") in want_var:
                exec(compile(ast.Module([n], []), "<b>", "exec"), ns)
                got.add(t.id)
    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in want_fn:
        mod = ast.Module([ast.ImportFrom(module="__future__",
              names=[ast.alias(name="annotations", asname=None)], level=0), n], [])
        ast.fix_missing_locations(mod)
        exec(compile(mod, "<b>", "exec"), ns)
        got.add(n.name)

missing = (want_fn | want_var) - got
if missing:
    print("MISSING " + ",".join(sorted(missing))); raise SystemExit

GOOD = "https://example.test:9445/secretpath/"
mode = {"fail": False}

async def fetch_panel(uid):
    if mode["fail"]:
        raise RpcError("caller is not an admin")
    return {"webui_url": GOOD}

ns["fetch_panel"] = fetch_panel
webapp_url = ns["_webapp_url"]
cache = ns["_WEBAPP_CACHE"]
bad = []

async def run():
    # 1. Первый удачный запрос запоминается.
    u = await webapp_url(1)
    if u != GOOD: bad.append("удачный запрос дал %r" % u)
    # 2. Отказ НЕ затирает прежний адрес.
    mode["fail"] = True
    cache["ts"] = ns["_WEBAPP_NEVER"]   # срок вышел — пойдёт новый запрос
    u = await webapp_url(0)
    if u != GOOD: bad.append("отказ затёр адрес: %r" % u)
    # 3. Пока адреса нет вовсе, отказ не запирает надолго.
    cache.update(url="", ts=ns["_WEBAPP_NEVER"])
    await webapp_url(0)
    if ns["WEBAPP_TTL_FAIL"] >= ns["WEBAPP_TTL_OK"]:
        bad.append("срок неудачи не короче срока удачи")
    # 4. После отказа удача подхватывается сразу.
    mode["fail"] = False
    cache["ts"] = ns["_WEBAPP_NEVER"]
    u = await webapp_url(1)
    if u != GOOD: bad.append("после отказа удача не подхватилась: %r" % u)

asyncio.run(run())

# 5. Стартовый запрос не должен идти от нулевого идентификатора.
#    Комментарии не считаем: в них этот вызов упомянут как «было так».
code_only = "\n".join(
    l for l in src.splitlines() if not l.lstrip().startswith("#"))
if "_webapp_url(0)" in code_only.replace(" ", ""):
    bad.append("на старте адрес спрашивается от user_id=0")

# 6. Отметка «никогда не спрашивали» не должна быть нулём: time.monotonic()
#    отсчитывается от неопределённого начала и на macOS близок к нулю.
if '"ts": 0.0' in src or "'ts': 0.0" in src:
    bad.append("отметка кэша — ноль, а monotonic() может быть около нуля")

print("OK" if not bad else "BAD " + " | ".join(bad))
PYEOF
) || WEBAPP_OUT="BAD запуск не удался"

case "$WEBAPP_OUT" in
    OK)       ok "отказ не затирает адрес мини-приложения, старт не спрашивает от нуля" ;;
    MISSING*) bad "части кэша адреса не найдены" "_webapp_url + кэш" "${WEBAPP_OUT#MISSING }" ;;
    *)        bad "кэш адреса мини-приложения" "адрес переживает отказ" "${WEBAPP_OUT#BAD }" ;;
esac

# ═══════════════════════════════════════════════════════════════════════
section "bot.py — нарезка длинных ответов под предел Telegram"
# ═══════════════════════════════════════════════════════════════════════
# Предел 4096 считается по ОТПРАВЛЯЕМОМУ сообщению, а отправляем мы
# `<pre>` + html-экранирование: «&» становится «&amp;» (рост впятеро),
# «<» — «&lt;» (вчетверо). Нарезка мерила сырой текст, и на обычном
# выводе (3478 на проводе при пределе 3500) запас казался достаточным.
# Но имя DNS-запроса задаёт КЛИЕНТ, dnsdist пишет его в лог, оператор
# смотрит лог из чата — текст с этими символами приходит снаружи. На
# 4000 угловых скобок сообщение раздувалось до 17511, и Telegram
# отвергал его целиком: вместо журнала оператор видел пустоту.
#
# bot.py вне venv не импортируется (нужен python-telegram-bot), поэтому
# достаём НАСТОЯЩИЕ функции разбором дерева — не копию.
CHUNK_OUT=$(python3 - "$PYBOT" <<'PYEOF'
import ast, html, sys, pathlib

src = pathlib.Path(sys.argv[1], "bot.py").read_text()
tree = ast.parse(src)
want = {"_esc", "_wire_len", "_chunks"}  # _wire_len нужен самому _chunks
ns = {"html": html, "List": list}
picked = set()
for node in tree.body:
    if isinstance(node, ast.Assign) and any(
            getattr(t, "id", "") == "_WIRE_OVERHEAD" for t in node.targets):
        exec(compile(ast.Module([node], []), "<bot>", "exec"), ns)
    if isinstance(node, ast.FunctionDef) and node.name in want:
        exec(compile(ast.Module([node], []), "<bot>", "exec"), ns)
        picked.add(node.name)

missing = want - picked
if missing:
    print("MISSING " + ",".join(sorted(missing)))
    raise SystemExit

chunks = ns["_chunks"]

# ГРАБЛИ в самой проверке: сначала длина мерилась функцией _wire_len,
# взятой из ТОГО ЖЕ bot.py. Сломай её — и проверка сломается вместе с
# ней, оставшись зелёной. Меряем независимо, по правилу Telegram:
# на провод уходит `<pre>` + html-экранирование.
def wire(part: str) -> int:
    return len("<pre>" + html.escape(part, quote=False) + "</pre>")

LIMIT = 4096
bad = []

cases = {
    "пусто": "",
    "обычный текст": "строка журнала\n" * 400,
    "4000 угловых скобок": "<" * 4000,
    "4000 амперсандов": "&" * 4000,
    "смесь": ("<a & b>\n" * 900),
    "одна строка 9000 без переносов": "x" * 9000,
    "строка из скобок 9000": ">" * 9000,
}
for label, text in cases.items():
    parts = chunks(text)
    worst = max((wire(p) for p in parts), default=0)
    if worst > LIMIT:
        bad.append("%s: %d > %d" % (label, worst, LIMIT))
    # Нарезка обязана сохранять СОДЕРЖИМОЕ. Сверяем без переносов:
    # длинная строка режется на части, и склейка через "\n" добавила бы
    # символы, которых в исходнике не было. А вот потерю символов такая
    # сверка ловит по-прежнему.
    if "".join(parts).replace("\n", "") != text.replace("\n", ""):
        bad.append("%s: содержимое потеряно" % label)

print("OK" if not bad else "BAD " + " | ".join(bad))
PYEOF
) || CHUNK_OUT="BAD запуск не удался"

case "$CHUNK_OUT" in
    OK)      ok "нарезка держит предел Telegram на всех случаях" ;;
    MISSING*) bad "функции нарезки не найдены в bot.py" "_esc/_wire_len/_chunks" "${CHUNK_OUT#MISSING }" ;;
    *)       bad "нарезка превышает предел" "все части ≤ 4096" "${CHUNK_OUT#BAD }" ;;
esac

# ═══════════════════════════════════════════════════════════════════════
section "bot.py — свой апстрим: разбор адреса, имени и списка"
# ═══════════════════════════════════════════════════════════════════════
# Правила те же, что у SSH-экрана (godns.sh custom_upstream_add), но
# функция — чистая, и достаём её из дерева, как _chunks: bot.py вне venv
# не импортируется. Пробел внутри адреса — отказ, а не склейка.
CU_PARSE_OUT=$(python3 - "$PYBOT" <<'PYEOF'
import ast, re, sys, pathlib
from typing import Optional, Tuple

src = pathlib.Path(sys.argv[1], "bot.py").read_text()
tree = ast.parse(src)
want_fn = {"_cu_port_ok", "_cu_parse_address", "_cu_valid_name", "_cu_parse_list"}
want_var = {"_CU_HOST_RE", "_CU_PATH_RE", "_CU_NAME_RE", "_CU_ID_RE", "CU_DEFAULT_PORT"}
ns = {"re": re, "Optional": Optional, "Tuple": Tuple}
got = set()
for n in tree.body:
    if isinstance(n, ast.Assign):
        for t in n.targets:
            if getattr(t, "id", "") in want_var:
                exec(compile(ast.Module([n], []), "<b>", "exec"), ns)
                got.add(t.id)
    if isinstance(n, ast.FunctionDef) and n.name in want_fn:
        exec(compile(ast.Module([n], []), "<b>", "exec"), ns)
        got.add(n.name)
missing = (want_fn | want_var) - got
if missing:
    print("MISSING " + ",".join(sorted(missing))); raise SystemExit

parse, valid_name, parse_list = ns["_cu_parse_address"], ns["_cu_valid_name"], ns["_cu_parse_list"]
bad = []

cases = [
    (("doh", "https://dns.example.org:8443/secret"), ("dns.example.org", "8443", "/secret")),
    (("doh", "http://dns.google/dns-query"), ("dns.google", "", "/dns-query")),
    (("doh", "dns.google"), ("dns.google", "", "")),          # путь подставит lib
    (("doh", " https://dns.google/dns-query "), ("dns.google", "", "/dns-query")),
    (("dot", "[2001:db8::1]:853"), ("2001:db8::1", "853", "")),
    (("dot", "[2001:db8::1]"), ("2001:db8::1", "", "")),
    (("dot", "2001:db8::1"), ("2001:db8::1", "", "")),      # голый v6
    (("dot", "one.one.one.one:853"), ("one.one.one.one", "853", "")),
    (("dot", "https://dns.quad9.net"), ("dns.quad9.net", "", "")),
    (("plain", "10.0.0.53"), ("10.0.0.53", "", "")),
    (("plain", "10.0.0.53:5353"), ("10.0.0.53", "5353", "")),
    (("dot", "host with space"), None),
    (("dot", ""), None),
    (("dot", "   "), None),
    (("dot", "a|b"), None),
    (("dot", "host:70000"), None),
    (("dot", "host:0"), None),
    (("dot", "host:abc"), None),
    (("dot", "[2001:db8::1]x"), None),
    (("dot", "[2001:db8::1"), None),
    (("dot", "dns.google/path"), None),                      # путь только у doh
    (("doh", "dns.google/{id}"), None),                      # шаблон — не путь
    (("dot", "-bad.example"), None),
    (("dot", "a_b.example"), None),                          # регэксп границы демона
]
for args, want in cases:
    got_v = parse(*args)
    if got_v != want:
        bad.append("parse%r → %r, ждали %r" % (args, got_v, want))

for name, want in [("Мой DoH", True), ("a" * 40, True), ("a" * 41, False),
                   ("a|b", False), ("a;b", False), ("", False), ("tab\tx", False),
                   ("nl\nx", False)]:
    if valid_name(name) != want:
        bad.append("valid_name(%r) → %r" % (name, not want))

listing = ("max\t8\nset_count\t2\n"
           "custom-dns-example-org\tМой DoH\tdoh\t203.0.113.5:8443\tdns.example.org\tyes\n"
           "custom-lan\tLAN\tplain\t10.0.0.53:53\t\tno\n"
           "Разрешаю имя… (строка для человека)\n")
info = parse_list(listing)
if info["max"] != 8 or info["set_count"] != 2:
    bad.append("служебные пары списка: %r" % {k: info[k] for k in ("max", "set_count")})
ids = [it["id"] for it in info["items"]]
if ids != ["custom-dns-example-org", "custom-lan"]:
    bad.append("записи списка: %r" % ids)
elif not (info["items"][0]["in_set"] and info["items"][0]["proto"] == "doh"
          and info["items"][1]["sni"] == "" and not info["items"][1]["in_set"]):
    bad.append("поля записей разобраны неверно: %r" % info["items"])
if any("path" in it for it in info["items"]):
    bad.append("в записи списка есть путь DoH — его там быть не должно")
if parse_list("")["items"] or parse_list("garbage\n")["max"] != 8:
    bad.append("пустой/мусорный вывод списка")

print("OK" if not bad else "BAD " + " | ".join(bad))
PYEOF
) || CU_PARSE_OUT="BAD запуск не удался"
case "$CU_PARSE_OUT" in
    OK)       ok "адрес, имя и список своего апстрима разбираются по правилам SSH-экрана" ;;
    MISSING*) bad "функции разбора не найдены в bot.py" "_cu_parse_address и соседи" "${CU_PARSE_OUT#MISSING }" ;;
    *)        bad "разбор своего апстрима" "все случаи" "${CU_PARSE_OUT#BAD }" ;;
esac

# ═══════════════════════════════════════════════════════════════════════
section "botd.py — снятие ANSI из отчётов"
# ═══════════════════════════════════════════════════════════════════════
eq "настоящий ESC снят" "hello" \
    "$(py_claim "$BOTD_ENV" "print(botd.strip_ansi('\x1b[0;32mhello\x1b[0m'))")"
eq "литеральный \\033 снят" "dim text" \
    "$(py_claim "$BOTD_ENV" "print(botd.strip_ansi(r'\033[2mdim text\033[0m'))")"
eq "\\r-перерисовка: остаётся последняя версия строки" "done" \
    "$(py_claim "$BOTD_ENV" "print(botd.strip_ansi('spinner 1\rspinner 2\rdone'))")"

# ═══════════════════════════════════════════════════════════════════════
section "контракт: бот ⊆ демон ⊆ фасад"
# ═══════════════════════════════════════════════════════════════════════
section "код привязки с неASCII не роняет root-демон"
# ГРАБЛИ: hmac.compare_digest на строке с неASCII бросает TypeError, а код
# приходит из Telegram. Любой ПОСТОРОННИЙ мог прислать боту «/start Пароль»
# в открытое окно привязки и уронить задачу в root-демоне: клиент не
# получал ответа вовсе, бот показывал «демон недоступен», и попытка шла
# мимо счётчика перебора. В соседней ветке (action) TypeError ловился
# осознанно — в claim забыли.
CLAIM_OUT=$(python3 - "$PYBOT" <<'PYEOF'
import hmac, pathlib, re, sys

src = pathlib.Path(sys.argv[1], "botd.py").read_text()
# Сравнение обязано идти по БАЙТАМ: проверяем сам код, а не поведение
# (botd вне venv не импортируется).
m = re.search(r"if not code or not hmac\.compare_digest\((.*?)\):", src, re.S)
if not m:
    print("NO-COMPARE"); sys.exit(1)
if ".encode(" not in m.group(1):
    print("STRING-COMPARE"); sys.exit(1)

# И контрольный опыт: так, как написано теперь, неASCII не роняет.
for bad in ("Пароль", "\U0001f642", "abc"):
    hmac.compare_digest(bad.encode("utf-8", "ignore"),
                        "expected".encode("utf-8", "ignore"))
print("claim-ok")
PYEOF
) || CLAIM_OUT="fail"
if [ "$CLAIM_OUT" = "claim-ok" ]; then
    ok "код привязки сравнивается по байтам — неASCII не роняет демон"
else
    bad "код привязки: $CLAIM_OUT"
fi

section "медиа-контракт: TGPHOTO/TGFILE принимают только каталог бота"
# ГРАБЛИ по построению: маркеры едут через ТОТ ЖЕ канал, что и журнал
# dnsdist, а в журнале бывают строки, пришедшие от посторонних клиентов
# (имена DNS-запросов). Строка «TGFILE:/etc/passwd» в журнале не должна
# заставить бота отправить /etc/passwd в Telegram. bot.py вне venv не
# импортируется — достаём настоящую функцию разбором текста.
MEDIA_OUT=$(python3 - "$PYBOT" <<'PYEOF'
import os, sys, pathlib, tempfile, re

src = pathlib.Path(sys.argv[1], "bot.py").read_text()
m = re.search(r"def _extract_media.*?\n(?=\nasync def _send_media)", src, re.S)
if not m:
    print("NO-FUNC"); sys.exit(1)

with tempfile.TemporaryDirectory() as td:
    share = os.path.join(td, "share")
    os.makedirs(share)
    ours = os.path.join(share, "qr.png")
    open(ours, "wb").write(b"x")
    os.environ["GODNS_BOT_STATE"] = td
    ns = {"os": os}
    exec(m.group(0), ns)
    f = ns["_extract_media"]

    media, text = f("head\nTGPHOTO:%s\ntail" % ours)
    assert media == [("photo", os.path.realpath(ours))], media
    assert text == "head\ntail", text

    media, text = f("TGFILE:/etc/passwd\nok")
    assert media == [] and text == "ok", (media, text)

    media, _ = f("TGFILE:%s/../../etc/passwd" % share)
    assert media == [], media

    media, _ = f("TGPHOTO:%s" % share)
    assert media == [], media

    media, _ = f("case from dnsdist log: TGPHOTO:/etc/shadow suffix")
    assert media == [], media
print("media-ok")
PYEOF
) || MEDIA_OUT="fail"
if [ "$MEDIA_OUT" = "media-ok" ]; then
    ok "чужие пути и обход ../ отброшены, свой каталог работает"
else
    bad "медиа-контракт" "$MEDIA_OUT"
fi

CONTRACT_OUT=$(python3 - "$PYBOT" "$SCRIPT_DIR" <<'PY'
# Причину провала печатаем и выходим НУЛЁМ: при sys.exit(1) оболочка
# подставляла слово «fail», и понять, что именно сломано, было нельзя.
import ast, re, sys
pybot, root = sys.argv[1], sys.argv[2]

def toplevel_assign(path, name):
    tree = ast.parse(open(path, encoding="utf-8").read())
    for node in tree.body:
        # ACTION_SPECS объявлен с аннотацией типа — это AnnAssign, и обход
        # только по Assign его молча пропускал (найдено этим же тестом).
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return node.value
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == name:
                return node.value
    return None

# ACTIONS демона: имена ключей (значения — функции, literal_eval не возьмёт).
actions_node = toplevel_assign(f"{pybot}/botd.py", "ACTIONS")
daemon = {ast.literal_eval(k) for k in actions_node.keys}

# ACTION_SPECS бота: ключ -> (имя действия демона, args, timeout).
specs_node = toplevel_assign(f"{pybot}/bot.py", "ACTION_SPECS")
bot_specs = {}
for k, v in zip(specs_node.keys, specs_node.values):
    key = ast.literal_eval(k)
    val = ast.literal_eval(v)
    bot_specs[key] = val[0]

missing = sorted(set(bot_specs.values()) - daemon)
if missing:
    print("BOT->DAEMON MISSING:", ",".join(missing)); sys.exit(0)

# Диспетчер actions.sh: метки case.
labels = set()
for line in open(f"{root}/godns-bot/actions.sh", encoding="utf-8"):
    m = re.match(r'^\s{8}([a-z_]+)\)\s+bot_act_', line)
    if m:
        labels.add(m.group(1))
missing = sorted(daemon - labels)
if missing:
    print("DAEMON->ACTIONS MISSING:", ",".join(missing)); sys.exit(0)

# Каждая кнопка меню бота ведёт на существующее действие/меню/подтверждение.
src = open(f"{pybot}/bot.py", encoding="utf-8").read()
menus_node = toplevel_assign(f"{pybot}/bot.py", "MENUS")
menus = ast.literal_eval(menus_node)
confirm_node = toplevel_assign(f"{pybot}/bot.py", "CONFIRM_WARN_KEY")
confirms = set(ast.literal_eval(confirm_node))
typed_node = toplevel_assign(f"{pybot}/bot.py", "TYPED_WORDS")
typed = set(ast.literal_eval(typed_node))
known_menus = set(menus) | {"m:main", "m:policy", "m:lang", "m:test"}
bad = []
for title, items, hint in menus.values():
    for label, cb in items:
        if cb.startswith("r:") and cb[2:] not in bot_specs: bad.append(cb)
        elif cb.startswith("c:") and cb[2:] not in confirms: bad.append(cb)
        elif cb.startswith("t:") and cb[2:] not in typed: bad.append(cb)
        elif cb.startswith("m:") and cb not in known_menus: bad.append(cb)
if bad:
    print("MENU DANGLING:", ",".join(bad)); sys.exit(0)
# У каждого подтверждения есть текст и спека.
for key in confirms | typed:
    if key not in bot_specs:
        print("CONFIRM WITHOUT SPEC:", key); sys.exit(0)

# ГРАБЛИ: проверялось только направление «кнопка → действие», и две
# написанные, переведённые, работающие возможности оказались без кнопки:
# backup_schedule_status (узнать текущее расписание копий) и ports_info.
# Оператор мог ЗАДАТЬ расписание, но не мог УЗНАТЬ его. Это тот же класс,
# что ловит check-unreachable в оболочке: оплаченная работа, до которой
# нельзя дотянуться. Проверяем и обратное направление.
reachable = set()
for title, items, hint in menus.values():
    for label, cb in items:
        if cb[:2] in ("r:", "c:", "t:"):
            reachable.add(cb[2:])
# Экраны, которые собирают кнопки в коде, а не в MENUS: их callbacks
# берём из исходника напрямую.
import re as _re
src = open(f"{pybot}/bot.py", encoding="utf-8").read()
for m in _re.finditer(r'callback_data="([rct]):([a-z_0-9]+)"', src):
    reachable.add(m.group(2))
for m in _re.finditer(r'\("([a-z_0-9]+)_btn",\s*"[rct]:([a-z_0-9]+)"\)', src):
    reachable.add(m.group(2))
# Действия потока «свой апстрим»: их кнопки строятся в коде (render_ups_menu
# и экраны шагов), а callback-и cu:* не имеют вида r:/c:/t: — регэкспы выше
# их не видят. Набор объявлен явно, CUSTOM_FLOW_ACTIONS, и каждое имя из
# него обязано быть у бота, у демона и меткой диспетчера фасада: иначе это
# та же «оплаченная работа без кнопки», только спрятанная за исключением.
flow_node = toplevel_assign(f"{pybot}/bot.py", "CUSTOM_FLOW_ACTIONS")
flow = set(ast.literal_eval(flow_node)) if flow_node is not None else set()
if not flow:
    print("CUSTOM_FLOW_ACTIONS MISSING OR EMPTY"); sys.exit(0)
for key in sorted(flow):
    if key not in bot_specs:
        print("FLOW ACTION WITHOUT SPEC:", key); sys.exit(0)
    if key not in daemon:
        print("FLOW ACTION WITHOUT DAEMON:", key); sys.exit(0)
    if key not in labels:
        print("FLOW ACTION WITHOUT DISPATCH LABEL:", key); sys.exit(0)
reachable |= flow
orphans = sorted(k for k in bot_specs if k not in reachable)
if orphans:
    print("ACTION WITHOUT BUTTON:", ",".join(orphans)); sys.exit(0)
# Кнопки cu:* — на существующий обработчик: каждый литерал callback_data
# «cu:…» из исходника обязан быть строкой в теле cu_on_callback (точной или
# префиксом, как «cu:proto:»). Мёртвая кнопка потока — тот же класс, что
# «MENU DANGLING» выше, просто вне MENUS.
tree = ast.parse(src)
handler = next((n for n in tree.body
                if isinstance(n, ast.AsyncFunctionDef) and n.name == "cu_on_callback"), None)
if handler is None:
    print("CU HANDLER MISSING"); sys.exit(0)
handled = {n.value for n in ast.walk(handler)
           if isinstance(n, ast.Constant) and isinstance(n.value, str)
           and n.value.startswith("cu:")}
# Префиксная ветка («cu:proto:» → startswith) закрывает все cu:proto:<p>.
prefixes = [h for h in handled if h.endswith(":")]
dead = sorted(cb for cb in set(_re.findall(r'callback_data="(cu:[a-z_:]+)"', src))
              if cb not in handled and not any(cb.startswith(p) for p in prefixes))
if dead:
    print("CU BUTTON WITHOUT HANDLER:", ",".join(dead)); sys.exit(0)
print("contract-ok")
PY
) || CONTRACT_OUT="fail"
eq "все кнопки бота разрешаются в действия демона и фасада" \
    "contract-ok" "$CONTRACT_OUT"

# ═══════════════════════════════════════════════════════════════════════
section "свой апстрим — стражи потока и регэкспы границ"
# ═══════════════════════════════════════════════════════════════════════
CU_GUARD_OUT=$(python3 - "$PYBOT" "$SCRIPT_DIR" <<'PYEOF'
import ast, pathlib, re, sys
pybot, root = sys.argv[1], sys.argv[2]
src = pathlib.Path(pybot, "bot.py").read_text()
tree = ast.parse(src)
bad = []

def fn(name):
    return next((n for n in tree.body
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and n.name == name), None)

# 1. Задачи потока снимают _INFLIGHT в finally (грабли launch_action:
#    отметка, застрявшая навсегда, лечилась только перезапуском бота).
for name in ("cu_run_probe", "cu_run_add", "run_and_show"):
    f = fn(name)
    if f is None:
        bad.append(name + " не найдена"); continue
    if not any(isinstance(n, ast.Try) and n.finalbody
               and "_INFLIGHT" in ast.dump(ast.Module(n.finalbody, []))
               and "discard" in ast.dump(ast.Module(n.finalbody, []))
               for n in ast.walk(f)):
        bad.append(name + ": _INFLIGHT не снимается в finally")

# 2. Черновик живёт по тем же правилам, что типизированное слово.
sweep = fn("_sweep_confirms")
if sweep is None or "_CU_DRAFT" not in ast.dump(sweep):
    bad.append("_sweep_confirms не чистит _CU_DRAFT")
start = fn("cmd_start")
if start is None or "_CU_DRAFT" not in ast.dump(start):
    bad.append("cmd_start не сбрасывает _CU_DRAFT")
code_only = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
if not re.search(r'if not data\.startswith\("cu:"\):\s*\n\s*_CU_DRAFT\.pop\(user_id, None\)', code_only):
    bad.append("on_callback не гасит черновик на навигации вне cu:*")

# 3. Путь DoH — только окружением: у вызовов rpc_action("custom_probe"/
#    "custom_add") обязан быть env с GODNS_CUSTOM_PATH, а draft["path"] не
#    должен попасть в args. ГРАБЛИ первой версии этого стража: он смотрел
#    во второй позиционный аргумент вызова, а там стоит ИМЯ `args` —
#    список собирается строкой выше. Мутация «путь в args» проходила
#    зелёной. Поэтому смотрим присваивания args внутри самой функции.
for fname, action in (("cu_run_probe", "custom_probe"), ("cu_run_add", "custom_add")):
    f = fn(fname)
    if f is None:
        bad.append(fname + " не найдена"); continue
    calls = [n for n in ast.walk(f)
             if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "rpc_action"]
    if not any(c.args and isinstance(c.args[0], ast.Constant) and c.args[0].value == action
               for c in calls):
        bad.append("%s: нет вызова rpc_action(%r)" % (fname, action)); continue
    for c in calls:
        env_kw = next((k for k in c.keywords if k.arg == "env"), None)
        if env_kw is None or "GODNS_CUSTOM_PATH" not in ast.dump(env_kw.value):
            bad.append("%s: rpc_action(%r) без env={GODNS_CUSTOM_PATH: …}" % (fname, action))
        if len(c.args) > 1 and "'path'" in ast.dump(c.args[1]):
            bad.append("%s: путь попал в args вызова" % fname)
    for n in ast.walk(f):
        if (isinstance(n, ast.Assign)
                and any(getattr(t, "id", "") == "args" for t in n.targets)
                and "'path'" in ast.dump(n.value)):
            bad.append("%s: путь попал в args" % fname)

# 4. Регэкспы границ тождественны регэкспам панели — таблицей, а не
#    «нормализацией»: демон разрешает пустой порт/путь/имя (внешние
#    ^(…)?$ и {0,40} против {1,40}), всё остальное — побайтно одно.
TABLE = {
    "CU_HOST_RE": (r"^[A-Za-z0-9:][A-Za-z0-9.:-]{0,252}$",
                   r"^[A-Za-z0-9:][A-Za-z0-9.:-]{0,252}$"),
    "CU_PORT_RE": (r"^([1-9][0-9]{0,4})?$",
                   r"^[1-9][0-9]{0,4}$"),
    "CU_PATH_RE": (r"^(/[A-Za-z0-9._~%/-]{0,127})?$",
                   r"^/[A-Za-z0-9._~%/-]{0,127}$"),
    "CU_NAME_RE": (r"^[^\x00-\x1f\x7f|;]{0,40}$",
                   r"^[^\x00-\x1f\x7f|;]{1,40}$"),
    "CU_ID_RE":   (r"^custom-[a-z0-9]([a-z0-9-]{0,38}[a-z0-9])?$",
                   r"^custom-[a-z0-9]([a-z0-9-]{0,38}[a-z0-9])?$"),
}

def regexes(path):
    out = {}
    try:
        t = ast.parse(pathlib.Path(path).read_text())
    except (OSError, SyntaxError):
        return out
    for n in t.body:
        if (isinstance(n, ast.Assign) and len(n.targets) == 1
                and isinstance(n.targets[0], ast.Name)
                and isinstance(n.value, ast.Call) and n.value.args
                and isinstance(n.value.args[0], ast.Constant)):
            out[n.targets[0].id] = n.value.args[0].value
    return out

daemon = regexes(pathlib.Path(pybot, "botd.py"))
for key, (want_d, _) in TABLE.items():
    if daemon.get(key) != want_d:
        bad.append("botd.py %s = %r, таблица ждёт %r" % (key, daemon.get(key), want_d))
print("GUARDS " + ("OK" if not bad else "BAD " + " | ".join(bad)))

# Панель — отдельной строкой: до слияния панели её регэкспов может не быть,
# и это должно читаться как «панель не слита», а не как поломка демона.
srv = regexes(pathlib.Path(root, "godns-web", "server.py"))
pbad = []
for key, (_, want_s) in TABLE.items():
    if key not in srv:
        pbad.append("%s отсутствует" % key)
    elif srv[key] != want_s:
        pbad.append("%s = %r, таблица ждёт %r" % (key, srv[key], want_s))
print("PANEL " + ("OK" if not pbad else "BAD " + " | ".join(pbad)))
PYEOF
) || CU_GUARD_OUT="GUARDS BAD запуск не удался"
case "$CU_GUARD_OUT" in
    *"GUARDS OK"*) ok "поток cu:*: _INFLIGHT в finally, TTL черновика, путь только окружением, регэкспы демона по таблице" ;;
    *) bad "стражи потока cu:*" "GUARDS OK" "$(printf '%s' "$CU_GUARD_OUT" | grep '^GUARDS')" ;;
esac
case "$CU_GUARD_OUT" in
    *"PANEL OK"*) ok "регэкспы границ панели (server.py) совпадают с таблицей и демоном" ;;
    *) bad "регэкспы границ панели (server.py)" "PANEL OK — CU_*_RE как в таблице" \
           "$(printf '%s' "$CU_GUARD_OUT" | grep '^PANEL')" ;;
esac

# ═══════════════════════════════════════════════════════════════════════
section "локали бота — ru/en в паритете"
# ═══════════════════════════════════════════════════════════════════════
LANG_OUT=$(python3 - "$PYBOT" <<'PY'
import json, re, sys
pybot = sys.argv[1]
ru = json.load(open(f"{pybot}/lang/ru.json", encoding="utf-8"))
en = json.load(open(f"{pybot}/lang/en.json", encoding="utf-8"))
if set(ru) != set(en):
    diff = set(ru) ^ set(en)
    print("KEYS DIFFER:", ",".join(sorted(diff))); sys.exit(1)
for key in ru:
    n_ru = len(re.findall(r"\{\d\}", ru[key]))
    n_en = len(re.findall(r"\{\d\}", en[key]))
    if n_ru != n_en:
        print("PLACEHOLDERS DIFFER:", key); sys.exit(1)
    if not ru[key] or not en[key]:
        print("EMPTY:", key); sys.exit(1)
print(f"parity-ok {len(ru)}")
PY
) || LANG_OUT="fail"
case "$LANG_OUT" in
    parity-ok*) ok "ru.json/en.json: одинаковые ключи и подстановки (${LANG_OUT#parity-ok })" ;;
    *) bad "ru.json/en.json в паритете" "parity-ok" "$LANG_OUT" ;;
esac

# ═══════════════════════════════════════════════════════════════════════
printf "\n"
if [ "$FAIL" -eq 0 ]; then
    printf "  \033[0;32m✓ все %d проверок прошли\033[0m\n\n" "$PASS"
    exit 0
fi
printf "  \033[0;31m✗ %d из %d проверок упали\033[0m\n\n" "$FAIL" "$((PASS+FAIL))"
exit 1
