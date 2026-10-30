#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
# goAmnezia pro — правки по итогам двух критиков 16.10 (CRITICS-FIX-1): SSH-экраны глазами пользователя.
#
# Запуск: ${BASH5:-/opt/homebrew/bin/bash} tests/test_critics_fix_dryrun.sh
# Нужны: bash ≥ 5, jq. Ничего вне $TMP не пишем, к Docker и серверам не ходим (GOAMNEZIA_DRY_RUN=1).
# Функции меню зовём напрямую (как test_dupline_dryrun.sh); список параметров, запись и «да/нет» (confirm читает
# терминал) — подставные.
#
#   A-4   «Изменить один параметр»: длинное значение (H2 21 знак) — через «…», а не молча обрезано;
#   A-5   «Параметры маскировки» — подзаголовок без вопроса «какой изменить?»;
#   A-6   правка параметра: Enter — назад без записи, «-» — убрать (подтверждение «Убрать параметр S1?»);
#   A-12  пресет: заголовок, текущий пресет, подтверждение с именем пресета и пользователями; 0 — назад;
#   A-13  подсеть и MTU: 0 — назад без «Отменено.».
#   K5-1  «Изменить один параметр»: значение, которое ядро не примет (S4 = 41, I1 с <r>), — отказ до «Записать …?»;
#   K5-3  HeaderProtectionKey — «задан (скрыт)» в списке, в вопросе и на «Параметрах маскировки», ни знака значения.
# ══════════════════════════════════════════════════════════════════════════════
set -uo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
TMP=$(mktemp -d "${TMPDIR:-/tmp}/goamnezia-critfix.XXXXXX") || exit 2
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
export UPSTREAM_DIR="$TMP/upstream"
export LIB_DIR="$ROOT/lib"
export GOAMNEZIA_SECRET_TMPDIR="$TMP/shm"
export BOT_DIR="$TMP/bot" ADMIN_DIR="$TMP/admin" MCP_DIR="$TMP/mcp"
export NGINX_SITE_CONF="$TMP/etc-nginx/sites-available/goamnezia" NGINX_SITE_LINK="$TMP/etc-nginx/sites-enabled/goamnezia"
export GOAMNEZIA_SYS_MODULE_DIR="$TMP/sysmodule"
unset DOCKER_HOST DOCKER_CONTEXT
export GOAMNEZIA_DOCKER_SOCK="$TMP/nosuch.sock"
# Контейнер профиля «есть и работает» (каталог-фикстура, как в test_params_dryrun.sh).
export GOAMNEZIA_CONTAINER_FS_DIR="$TMP/cfs"
mkdir -p "$GOAMNEZIA_DIR" "$GOAMNEZIA_LOCK_DIR" "$GOAMNEZIA_SECRET_TMPDIR" "$GOAMNEZIA_SYS_MODULE_DIR" "$TMP/cfs/amnezia-awg2"
HOSTBIN="$TMP/hostbin"; mkdir -p "$HOSTBIN"
printf '#!/bin/sh\nexit 1\n' >"$HOSTBIN/systemctl"; chmod +x "$HOSTBIN/systemctl"
export PATH="$HOSTBIN:$PATH"
BASH5="${BASH5:-bash}"
INSTALL="$ROOT/install.sh"

pass=0; fail=0; rc=0; out=""
ok()   { pass=$((pass + 1)); printf '  ✓ %s\n' "$1"; }
bad()  { fail=$((fail + 1)); printf '  ✗ %s\n' "$1"; [ -n "${2:-}" ] && printf '      %s\n' "$2" | head -c 1500; return 0; }
check() { if ( eval "$2" ) >/dev/null 2>&1; then ok "$1"; else bad "$1" "${3:-}"; fi; }
section() { printf '\n── %s\n' "$1"; }
MENU="$TMP/menu"; mkdir -p "$MENU"
src=$(<"$INSTALL"); printf '%s\n' "${src%main \"\$@\"*}" >"$MENU/install.sh"; ln -s "$ROOT/lib" "$MENU/lib"
# sh_fn '<тело>' [ввод] — функции install.sh без запуска меню; весь вывод (stdout+stderr) без цвета — в out.
sh_fn() {
    printf '%b' "${2:-}" >"$TMP/in"
    NO_COLOR=1 "$BASH5" -c 'source "$1/install.sh" >/dev/null 2>&1; eval "$2"' sh "$MENU" "$1" >"$OUT" 2>"$ERR" <"$TMP/in"; rc=$?
    out=$(cat "$OUT" "$ERR" | sed 's/\x1b\[[0-9;]*m//g')
    return 0
}

section "окружение"
GA_VER=$(sed -n 's/^GOAMNEZIA_VERSION="\([^"]*\)".*/\1/p' "$ROOT/lib/common.sh" | head -1)
cat >"$GOAMNEZIA_DIR/config.json" <<EOF
{"schema": 1, "version": "$GA_VER", "installed": true, "mode": "lite", "lang": "ru", "domain": "",
 "server_ip": "203.0.113.10", "endpoint_host": "", "client_dns": ["1.1.1.1"],
 "profiles": {"awg2": {"container": "amnezia-awg2", "installed_at": "2026-10-16T08:00:00Z", "port": 443, "line": "2.0", "preset": "quic",
   "subnet": "10.8.1.0/24", "params": {}}}, "options": {}}
EOF
chmod 600 "$GOAMNEZIA_DIR/config.json"
printf '{"schema": 1, "users": [{"name": "anna", "peers": {"awg2": {"ip": "10.8.1.2"}}}]}\n' >"$GOAMNEZIA_DIR/users.json"
chmod 600 "$GOAMNEZIA_DIR/users.json"
printf '  bash %s, jq %s\n' "$("$BASH5" -c 'echo $BASH_VERSION')" "$(jq --version 2>/dev/null)"

# Подставные: список параметров (H2 — 21 знак, как на стенде) и запись — в файл, чтобы видеть, дошло ли до ядра.
H2=1234567890-1234567899
STUB='amnezia_profile_param_list() { jq -cn --arg h "'"$H2"'" '"'"'[{key:"S1",value:"24",scope:"server"},{key:"H2",value:$h,scope:"server"},{key:"Jc",value:"4",scope:"client"}]'"'"'; }
amnezia_profile_set_param() { printf "%s=%s\n" "$2" "$3" >>"'"$TMP"'/set.log"; echo "{\"ok\":true}"; }
_profile_line() { echo 2.0; }
confirm() { local a=""; echo "  $1 [1 — да, 0 — нет]:" >&2; IFS= read -r a || true; [ "$a" = 1 ]; }
'
# A-4…A-6 проверяют ход экрана, а не правила значений: проверка ядра до вопроса (K5-1) здесь подставная — у фикстуры
# params пустые, и валидатор линейки отказал бы S1 = 30 («Jc is required»). Настоящая проверка — в секции K5-1.
STUB_OK="${STUB}"'amnezia_profile_param_check() { return 0; }
'

section "A-4: «Изменить один параметр» — длинное значение через «…»"
sh_fn "${STUB_OK}"'_profile_set_param_interactive awg2' '0\n'
check "H2 из 21 знака: на экране 19 знаков и «…», а не 20 знаков без пометки" \
    'grep -qF "${H2:0:19}…" <<<"$out" && ! grep -qF "${H2:0:20} " <<<"$out"' "$(grep -F H2 <<<"$out")"
check "короткое значение — целиком" 'grep -qE "S1 +24 " <<<"$out"' "$(grep -F S1 <<<"$out")"
sh_fn "${STUB_OK}"'_profile_set_param_interactive awg2' '2\n\n'
check "в вопросе после выбора — полное значение H2" 'grep -qF "H2 = $H2 →" <<<"$out"' "$(grep -F "H2 =" <<<"$out")"

section "A-5: «Параметры маскировки» — подзаголовок без вопроса"
sh_fn "${STUB}"'_profile_params_show awg2'
check "подзаголовок не спрашивает «какой параметр изменить?», а говорит, где менять" \
    '! grep -qF "какой параметр изменить" <<<"$out" && tr -s "\n " "  " <<<"$out" | grep -qF "Изменить — пункт «Изменить один параметр» карточки"' "$(head -6 <<<"$out")"

section "A-6: правка параметра — Enter назад, «-» убрать"
rm -f "$TMP/set.log"
sh_fn "${STUB_OK}"'_profile_set_param_interactive awg2; echo "rc=$? back=${GA_BACK:-0}"' '1\n\n'
check "Enter — назад: записи нет, «Отменено.» нет, rc 0 и отметка «назад»" \
    '[ ! -s "$TMP/set.log" ] && ! grep -qF "Отменено" <<<"$out" && grep -qF "rc=0 back=1" <<<"$out"' "$(tail -5 <<<"$out")"
check "в вопросе сказано: Enter — назад, «-» — убрать" 'grep -qF "S1 = 24 → новое значение (Enter — назад, «-» — убрать параметр)" <<<"$out"' "$(grep -F "S1 =" <<<"$out")"
sh_fn "${STUB_OK}"'_profile_set_param_interactive awg2' '1\n-\n1\n'
check "«-» — подтверждение «Убрать параметр S1?», в ядро ушло пустое значение" \
    'grep -qF "Убрать параметр S1?" <<<"$out" && ! grep -qF "S1 = -?" <<<"$out" && [ "$(cat "$TMP/set.log")" = "S1=" ]' "$(tail -6 <<<"$out"); $(cat "$TMP/set.log" 2>/dev/null)"
rm -f "$TMP/set.log"
sh_fn "${STUB_OK}"'_profile_set_param_interactive awg2' '1\n30\n1\n'
check "новое значение — «Записать S1 = 30?» и запись S1=30" \
    'grep -qF "Записать S1 = 30?" <<<"$out" && [ "$(cat "$TMP/set.log")" = "S1=30" ]' "$(tail -6 <<<"$out")"

section "K5-1: «Изменить один параметр» — значение, которое не запишется, отказ до «Записать …?»"
# Живой стенд 29.10: «Записать S4 = 41? [1 — да, 0 — нет]», а отказ — только после «1». Теперь проверка ядра
# (amnezia_profile_param_check — та же, что у записи, без записи) — сразу после ввода: ни предупреждения о
# перевыпуске, ни вопроса, ни записи. Проверка здесь настоящая; список параметров и запись — подставные.
STUB51='amnezia_profile_param_list() { jq -cn '"'"'[{key:"S4",value:"20",scope:"server"},{key:"I1",value:"<b 0x01>",scope:"client"}]'"'"'; }
amnezia_profile_set_param() { printf "%s=%s\n" "$2" "$3" >>"'"$TMP"'/set.log"; echo "{\"ok\":true}"; }
_profile_line() { echo 2.0; }
confirm() { local a=""; echo "  $1 [1 — да, 0 — нет]:" >&2; echo asked >>"'"$TMP"'/confirm.log"; IFS= read -r a || true; [ "$a" = 1 ]; }
'
rm -f "$TMP/set.log" "$TMP/confirm.log"
sh_fn "${STUB51}"'_profile_set_param_interactive awg2; echo "rc=$?"' '1\n41\n1\n'
check "S4 = 41: отказ «S4 = 41 не принят…» до вопроса — ни «Записать», ни предупреждения о перевыпуске, ни записи, rc 1" \
    'grep -qF "S4 = 41 не принят" <<<"$out" && ! grep -qF "Записать" <<<"$out" && ! grep -qF "Серверные параметры" <<<"$out" && [ ! -e "$TMP/confirm.log" ] && [ ! -s "$TMP/set.log" ] && grep -qF "rc=1" <<<"$out"' \
    "$(tail -8 <<<"$out"); set=$(cat "$TMP/set.log" 2>/dev/null)"
sh_fn "${STUB51}"'_profile_set_param_interactive awg2; echo "rc=$?"' '2\n<r 2>\n1\n'
check "I1 = <r 2>: отказ «I1 не принят: пакет начинается с <r>…» до вопроса, записи нет" \
    'grep -qF "I1 не принят: пакет начинается с <r>" <<<"$out" && ! grep -qF "Записать" <<<"$out" && [ ! -e "$TMP/confirm.log" ] && [ ! -s "$TMP/set.log" ]' \
    "$(tail -8 <<<"$out"); set=$(cat "$TMP/set.log" 2>/dev/null)"
# Годное значение проверку проходит и идёт к вопросу как прежде (настоящая проверка с полным набором 2.0 — в
# test_params §12; здесь у фикстуры набора нет, поэтому ход до вопроса — со словами проверки «да»).
sh_fn "${STUB51}"'amnezia_profile_param_check() { echo "$2=$3" >>"'"$TMP"'/check.log"; return 0; }; _profile_set_param_interactive awg2' '1\n22\n1\n'
check "S4 = 22: проверка спрошена с тем же значением, затем предупреждение, «Записать S4 = 22?» и запись" \
    '[ "$(cat "$TMP/check.log")" = "S4=22" ] && grep -qF "Записать S4 = 22?" <<<"$out" && grep -qF "Серверные параметры" <<<"$out" && [ "$(cat "$TMP/set.log")" = "S4=22" ]' \
    "$(tail -8 <<<"$out"); set=$(cat "$TMP/set.log" 2>/dev/null)"

section "K5-3: HeaderProtectionKey — «задан (скрыт)», а не начало ключа (экраны попадают в записи и скриншоты)"
HPK=SFBLdGVzdE9ubHlOb3RBUmVhbEtleUZvclNTSDAxMjM=
STUB53='amnezia_profile_param_list() { jq -cn --arg h "'"$HPK"'" '"'"'[{key:"Jc",value:"4",scope:"client"},{key:"HeaderProtectionKey",value:$h,scope:"server"},{key:"I2",value:"",scope:"client"}]'"'"'; }
amnezia_profile_set_param() { printf "%s=%s\n" "$2" "$3" >>"'"$TMP"'/set.log"; echo "{\"ok\":true}"; }
amnezia_profile_param_check() { return 0; }
_profile_line() { echo 3.1; }
confirm() { local a=""; echo "  $1 [1 — да, 0 — нет]:" >&2; IFS= read -r a || true; [ "$a" = 1 ]; }
'
sh_fn "${STUB53}"'_profile_set_param_interactive awg2' '2\n\n'
check "«Изменить один параметр»: строка HeaderProtectionKey — «задан (скрыт)», в вопросе — тоже; ни 8 знаков ключа" \
    'grep -qE "2\) HeaderProtectionKey +задан \(скрыт\)" <<<"$out" && grep -qF "HeaderProtectionKey = задан (скрыт) →" <<<"$out" && ! grep -qF "${HPK:0:8}" <<<"$out"' \
    "$(grep -F HeaderProtectionKey <<<"$out")"
check "… другие значения — как есть: Jc 4, пустой I2 — «-»" 'grep -qE "1\) Jc +4 " <<<"$out" && grep -qE "3\) I2 +- " <<<"$out"' "$(grep -E "Jc|I2" <<<"$out")"
sh_fn "${STUB53}"'_profile_params_show awg2'
check "«Параметры маскировки»: HeaderProtectionKey — «задан (скрыт)», ни 8 знаков ключа" \
    'grep -qE "HeaderProtectionKey +задан \(скрыт\)" <<<"$out" && ! grep -qF "${HPK:0:8}" <<<"$out"' "$(grep -F HeaderProtectionKey <<<"$out")"
rm -f "$TMP/set.log"
# KEENETIC-I1-N N3: новый ключ — и в «Записать …?» «задан (скрыт)» (прежде вопрос печатал введённое значение целиком).
HPK_NEW=TjMtdGVzdC1vbmx5LW5vdC1hLXJlYWwta2V5LTAwMDE=
sh_fn "${STUB53}"'_profile_set_param_interactive awg2' "2\n$HPK_NEW\n1\n"
check "новый HeaderProtectionKey: «Записать HeaderProtectionKey = задан (скрыт)?», ни 8 знаков нового ключа, в ядро ушёл целиком" \
    'grep -qF "Записать HeaderProtectionKey = задан (скрыт)?" <<<"$out" && ! grep -qF "${HPK_NEW:0:8}" <<<"$out" && [ "$(cat "$TMP/set.log")" = "HeaderProtectionKey=$HPK_NEW" ]' \
    "$(grep -F "Записать" <<<"$out"); set=$(cut -c1-24 "$TMP/set.log" 2>/dev/null)"
rm -f "$TMP/set.log"
sh_fn "${STUB53}"'_profile_set_param_interactive awg2' '2\n-\n1\n'
check "убрать HeaderProtectionKey — как прежде: «Убрать параметр HeaderProtectionKey?» и запись пустого значения" \
    'grep -qF "Убрать параметр HeaderProtectionKey?" <<<"$out" && [ "$(cat "$TMP/set.log")" = "HeaderProtectionKey=" ]' "$(tail -5 <<<"$out")"
sh_fn "GOAMNEZIA_LANG=en; load_language en >/dev/null 2>&1; ${STUB53}"'_profile_params_show awg2'
check "en: «set (hidden)»" 'grep -qE "HeaderProtectionKey +set \(hidden\)" <<<"$out" && ! grep -qF "${HPK:0:8}" <<<"$out"' "$(grep -F HeaderProtectionKey <<<"$out")"

section "A-12: пресет — заголовок, текущий, подтверждение по делу"
REGEN='amnezia_profile_regen() { echo "$2" >"'"$TMP"'/regen.log"; }; ga_step() { shift 3; "$@"; }; '
sh_fn "${STUB}${REGEN}"'_profile_regen_interactive awg2 ask' '2\n0\n'
check "заголовок «Пресет параметров» и текущий пресет отмечен «· сейчас»" \
    'grep -qF "Пресет параметров" <<<"$out" && grep -qE "2\) Под видом QUIC.*· сейчас" <<<"$out" && ! grep -qE "1\) Обычный.*сейчас" <<<"$out"' "$(head -12 <<<"$out")"
check "подтверждение называет пресет и профиль, перечислены пользователи с ключами" \
    'grep -qF "Применить пресет «Под видом QUIC» к" <<<"$out" && grep -qF "у пользователей: 1 (anna)" <<<"$out" && ! grep -qF "Перегенерировать параметры?" <<<"$out"' "$(tail -8 <<<"$out")"
check "отказ — пресет не применён" '[ ! -s "$TMP/regen.log" ]'
sh_fn "${STUB}${REGEN}"'_profile_regen_interactive awg2 ask; echo "rc=$? back=${GA_BACK:-0}"' '0\n'
check "0 — назад: без «Отменено.», без вопроса" \
    'grep -qF "rc=0 back=1" <<<"$out" && ! grep -qF "Отменено" <<<"$out" && ! grep -qF "Применить пресет" <<<"$out"' "$(tail -5 <<<"$out")"
sh_fn "${STUB}${REGEN}"'_profile_regen_interactive awg2 ask' '3\n1\n'
check "«Для роутеров» и «да» — в ядро ушёл router" '[ "$(cat "$TMP/regen.log" 2>/dev/null)" = router ]' "$(tail -5 <<<"$out")"
sh_fn "${STUB}${REGEN}"'_profile_regen_interactive awg2 default' '0\n'
check "«Перегенерировать параметры» — прежний вопрос" 'grep -qF "Перегенерировать параметры?" <<<"$out"' "$(tail -5 <<<"$out")"

section "A-13: подсеть и MTU — 0 назад без «Отменено.»"
sh_fn "${STUB}"'_profile_set_subnet_interactive awg2; echo "rc=$? back=${GA_BACK:-0}"' '0\n'
check "подсеть: 0 — назад" 'grep -qF "rc=0 back=1" <<<"$out" && ! grep -qF "Отменено" <<<"$out"' "$(tail -5 <<<"$out")"
sh_fn "${STUB}"'_profile_set_mtu_interactive awg2; echo "rc=$? back=${GA_BACK:-0}"' '0\n'
check "MTU: 0 — назад" 'grep -qF "rc=0 back=1" <<<"$out" && ! grep -qF "Отменено" <<<"$out"' "$(tail -5 <<<"$out")"
sh_fn "${STUB}"'_profile_set_mtu_interactive awg2; echo "rc=$?"' ''
check "MTU: конец ввода — по-прежнему отмена" 'grep -qF "Отменено" <<<"$out" && grep -qF "rc=1" <<<"$out"' "$(tail -5 <<<"$out")"

section "A-1: после создания пользователя — названия профилей, а не awg2"
sh_fn 'ga_users_apply_many() { return 0; }; users_add bob "" awg2'
check "«Пользователь bob создан, профили: AmneziaWG 2.0 · порт 443» без awg2" \
    'grep -qF "Пользователь bob создан, профили: AmneziaWG 2.0 · порт 443" <<<"$out" && ! grep -qE "профили:.*awg[123]" <<<"$out"' "rc=$rc $(tail -5 <<<"$out")"

section "A-2: «Текст .conf» — понятное имя, строка scp, файл стёрт после Enter"
sh_fn 'export_conf() { printf "[Interface]\nPrivateKey = x\n"; }; _user_key bob conf "" awg2; echo "left=$(find "$GOAMNEZIA_SECRET_TMPDIR" -name "*.conf" | wc -l | tr -d " ") back=${GA_BACK:-0}"' '\n'
check "файл «bob-2_0.conf» в своём каталоге, рядом строка «scp root@203.0.113.10:… .»" \
    'grep -qE "Сохранено в файл: .*/\.goamnezia-conf\.[^/]+/bob-2_0\.conf" <<<"$out" && grep -qE "scp root@203\.0\.113\.10:.*/bob-2_0\.conf \.$" <<<"$out"' "rc=$rc $(tail -8 <<<"$out")"
check "после Enter файла нет, вторая пауза вызывающего не нужна" 'grep -qF "left=0 back=1" <<<"$out"' "$(tail -3 <<<"$out")"

section "R-2: остатки прошлых показов стёрты, Ctrl+C и обрыв SSH на паузе файл не оставляют"
mkdir -p "$GOAMNEZIA_SECRET_TMPDIR/.goamnezia-conf.OLDOLDOL" "$GOAMNEZIA_SECRET_TMPDIR/.goamnezia-conf.NEWNEWNE"
echo k >"$GOAMNEZIA_SECRET_TMPDIR/.goamnezia-conf.OLDOLDOL/old-20.conf"
echo k >"$GOAMNEZIA_SECRET_TMPDIR/.goamnezia-conf.NEWNEWNE/new-20.conf"
touch -t 202601010000 "$GOAMNEZIA_SECRET_TMPDIR/.goamnezia-conf.OLDOLDOL"
sh_fn 'export_conf() { printf "[Interface]\nPrivateKey = x\n"; }; _user_key bob conf "" awg2' '\n'
check "каталог старше 10 минут стёрт, свежий соседний (другой сеанс) на месте" \
    '[ ! -e "$GOAMNEZIA_SECRET_TMPDIR/.goamnezia-conf.OLDOLDOL" ] && [ -f "$GOAMNEZIA_SECRET_TMPDIR/.goamnezia-conf.NEWNEWNE/new-20.conf" ]' \
    "$(ls -la "$GOAMNEZIA_SECRET_TMPDIR")"
rm -rf "$GOAMNEZIA_SECRET_TMPDIR/.goamnezia-conf.NEWNEWNE"
# Q-2: чужие каталоги и ссылки очистка не трогает. Чужого владельца без root не создать — «чужим» делаем свой
# каталог, подставив другой uid в id -u.
mkdir -p "$GOAMNEZIA_SECRET_TMPDIR/.goamnezia-conf.FOREIGNX" "$TMP/victimd"
echo k >"$GOAMNEZIA_SECRET_TMPDIR/.goamnezia-conf.FOREIGNX/x-20.conf"
echo VICTIM >"$TMP/victimd/v"; echo VICTIM >"$TMP/victim2"
ln -s "$TMP/victimd" "$GOAMNEZIA_SECRET_TMPDIR/.goamnezia-conf.LINKLINK"
touch -t 202601010000 "$GOAMNEZIA_SECRET_TMPDIR/.goamnezia-conf.FOREIGNX"
touch -h -t 202601010000 "$GOAMNEZIA_SECRET_TMPDIR/.goamnezia-conf.LINKLINK" 2>/dev/null
sh_fn 'id() { if [ "${1:-}" = -u ]; then echo 4242424; else command id "$@"; fi; }; _key_conf_sweep'
check "Q-2: старый каталог другого владельца очистка не трогает" \
    '[ -f "$GOAMNEZIA_SECRET_TMPDIR/.goamnezia-conf.FOREIGNX/x-20.conf" ]' "rc=$rc $(ls -la "$GOAMNEZIA_SECRET_TMPDIR")"
ln -s "$TMP/victim2" "$GOAMNEZIA_SECRET_TMPDIR/.goamnezia-conf.FOREIGNX/link-20.conf"
touch -t 202601010000 "$GOAMNEZIA_SECRET_TMPDIR/.goamnezia-conf.FOREIGNX"
sh_fn '_key_conf_sweep'
check "Q-2: свой старый каталог стёрт, по ссылке-каталогу и ссылке-файлу внутри не пошли" \
    '[ ! -e "$GOAMNEZIA_SECRET_TMPDIR/.goamnezia-conf.FOREIGNX" ] && [ "$(cat "$TMP/victimd/v")" = VICTIM ] && [ "$(cat "$TMP/victim2")" = VICTIM ]' \
    "rc=$rc $(ls -la "$GOAMNEZIA_SECRET_TMPDIR" "$TMP/victimd"; od -c "$TMP/victim2" | head -2)"
rm -rf "$GOAMNEZIA_SECRET_TMPDIR/.goamnezia-conf.LINKLINK" "$GOAMNEZIA_SECRET_TMPDIR/.goamnezia-conf.FOREIGNX"
ln -s "$TMP/victim2" "$GOAMNEZIA_SECRET_TMPDIR/l2"
sh_fn 'ga_wipe_file "$GOAMNEZIA_SECRET_TMPDIR/l2"'
check "Q-2: ga_wipe_file на ссылке убирает ссылку, файл по ней не затирает" \
    '[ ! -L "$GOAMNEZIA_SECRET_TMPDIR/l2" ] && [ "$(cat "$TMP/victim2")" = VICTIM ]' "rc=$rc $(od -c "$TMP/victim2" | head -2)"
# Q-4: поднялся через sudo — пользователю отдан только файл, каталог остаётся за root (0711). root подставной:
# id -u → 0, chown/chmod пишут, что их просили.
sh_fn 'id() { if [ "${1:-}" = -u ]; then echo 0; else return 0; fi; }; chown() { echo "CHOWN $*" >>"$GOAMNEZIA_SECRET_TMPDIR/../own.log"; }; chmod() { echo "CHMOD $*" >>"$GOAMNEZIA_SECRET_TMPDIR/../own.log"; command chmod "$@"; }; SUDO_USER=bob; export_conf() { printf "[Interface]\nPrivateKey = x\n"; }; _user_key bob conf "" awg2' '\n'
own=$(cat "$TMP/own.log" 2>/dev/null); rm -f "$TMP/own.log"
check "Q-4: chown — только файл bob-2_0.conf, каталог — chmod 711, строка «scp bob@…»" \
    'grep -qE "^CHOWN bob [^ ]*/\.goamnezia-conf\.[^/ ]+/bob-2_0\.conf$" <<<"$own" && [ "$(grep -c "^CHOWN" <<<"$own")" = 1 ] && grep -qE "^CHMOD 711 [^ ]*/\.goamnezia-conf\.[^/ ]+$" <<<"$own" && grep -qE "scp bob@203\.0\.113\.10:.*/bob-2_0\.conf \.$" <<<"$out"' \
    "rc=$rc own=[$own] $(grep -F scp <<<"$out")"
# Тест запущен фоном из неинтерактивной оболочки (параллельный прогон) — SIGINT ему достался заглушённым, а
# заглушённый на входе сигнал bash не даёт ни перехватить, ни вернуть: Ctrl+C тогда не проверить. Возвращаем
# обычную реакцию на SIGINT перед запуском (python3 — сигнал на месте, exec — тот же процесс).
INT_DFL=()
if [ -n "$(trap -p INT)" ] && command -v python3 >/dev/null 2>&1; then
    INT_DFL=(python3 -c 'import os, signal, sys; signal.signal(signal.SIGINT, signal.SIG_DFL); os.execvp(sys.argv[1], sys.argv[1:])')
fi
for sig in INT HUP; do
    # Пауза ждёт Enter, который не придёт (sleep держит ввод открытым); сигнал — когда на экране текст паузы
    # (Q-3: не по появлению файла — до паузы ещё идут подоболочки).
    # set -m: фоновой задаче без управления заданиями оболочка глушит SIGINT, и Ctrl+C не проверить.
    set -m
    NO_COLOR=1 "${INT_DFL[@]}" "$BASH5" -c 'source "$1/install.sh" >/dev/null 2>&1; export_conf() { printf "[Interface]\nPrivateKey = x\n"; }; _user_key bob conf "" awg2; echo AFTER' \
        sh "$MENU" >"$OUT" 2>"$ERR" < <(sleep 20) &
    kpid=$!
    set +m
    for _ in $(seq 1 100); do
        grep -qF "Нажмите Enter" "$OUT" 2>/dev/null && break
        sleep 0.1
    done
    seen=$(find "$GOAMNEZIA_SECRET_TMPDIR" -name "*.conf" | wc -l | tr -d " ")
    kill -s "$sig" "$kpid" 2>/dev/null
    wait "$kpid" 2>/dev/null; krc=$?
    check "$sig на паузе: файл был, после сигнала каталога нет, программа вышла (а не пошла дальше)" \
        '[ "$seen" = 1 ] && [ -z "$(find "$GOAMNEZIA_SECRET_TMPDIR" -name ".goamnezia-conf.*" 2>/dev/null)" ] && [ "$krc" -gt 128 ] && ! grep -qF AFTER "$OUT"' \
        "seen=$seen rc=$krc $(ls -la "$GOAMNEZIA_SECRET_TMPDIR")"
done

section "A-9: «Статус» — названия профилей"
sh_fn '_status_report'
check "строка профиля — «AmneziaWG 2.0» и порт, без awg2" \
    'grep -qE "AmneziaWG 2\.0 +UDP 443" <<<"$out" && ! grep -qE "awg[123]" <<<"$out"' "$(head -8 <<<"$out")"

section "A-10: «Логи» — профилей в списке нет, сказано, где их подключения"
sh_fn '_logs_screen' '0\n'
check "в списке нет AmneziaWG, есть строка «Профили AmneziaWG журнал не ведут… «Трафик и онлайн»»" \
    '! grep -qE "^ *[0-9]+\) AmneziaWG" <<<"$out" && tr -s "\n " "  " <<<"$out" | grep -qF "Профили AmneziaWG журнал не ведут: кто и когда подключался — «Трафик и онлайн»"' "$(head -12 <<<"$out")"
sh_fn '_ga_logs_tail() { ga_err logs_disabled "логи отключены"; return 1; }; _logs_screen' '1\n'
check "ошибка чтения журнала — без совета «Смотреть дальше…» следом" '! grep -qF "Смотреть дальше" <<<"$out"' "$(tail -5 <<<"$out")"

section "A-11: «Трафик и онлайн» — порядок списка пользователей и время последней связи"
NOW=$(date +%s)
printf '{"schema": 1, "users": [{"name": "anna", "peers": {"awg2": {"pubkey": "PKA"}}}, {"name": "bob", "peers": {"awg2": {"pubkey": "PKB"}}}, {"name": "carl", "peers": {"awg2": {"pubkey": "PKC"}}}]}\n' >"$GOAMNEZIA_DIR/users.json"
SHOW='amnezia_show() { printf "[{\"pubkey\":\"PKX\",\"latest_handshake_ts\":0},{\"pubkey\":\"PKC\",\"latest_handshake_ts\":'"$((NOW - 7200))"'},{\"pubkey\":\"PKB\",\"latest_handshake_ts\":'"$((NOW - 20))"'},{\"pubkey\":\"PKA\",\"latest_handshake_ts\":0}]"; }; '
sh_fn "${SHOW}"'_traffic_report'
rows=$(grep -E "^    (●|○|–) " <<<"$out")
check "строки по списку пользователей: anna, bob, carl, затем чужое подключение" \
    '[ "$(sed -E "s/^ +[^ ]+ ([^ ]+).*/\1/" <<<"$rows" | tr "\n" " ")" = "anna bob carl PKX… " ]' "$rows $(head -12 <<<"$out")"
check "колонка «Связь»: «сейчас» у bob, «2 ч назад» у carl, «–» у anna" \
    'grep -qE "bob .*сейчас$" <<<"$rows" && grep -qE "carl .*2 ч назад$" <<<"$rows" && grep -qE "anna .*–$" <<<"$rows"' "$rows"

section "A-8, A-17: профиль из приложения — сборка ядра вместо «latest», MTU вместо «не задан»"
DIG="sha256:$(printf 'a%.0s' $(seq 64))"
jq --arg d "$DIG" '.profiles.awg2 += {image_tag: "latest", image_digest: $d, imported: true}' "$GOAMNEZIA_DIR/config.json" >"$TMP/c" \
    && mv "$TMP/c" "$GOAMNEZIA_DIR/config.json" && chmod 600 "$GOAMNEZIA_DIR/config.json"
sh_fn '_profile_card awg2' '0\n'
check "дайджест не узнан — «не узнать (образ приложения Amnezia, тег latest; …)», а не голое latest" \
    'grep -qF "Версия ядра: не узнать (образ приложения Amnezia, тег latest" <<<"$out"' "$(grep -F "Версия ядра" <<<"$out")"
mkdir -p "$GOAMNEZIA_DIR/upstream"
jq -cn --arg d "$DIG" '{checked: "2026-10-16T08:00:00Z", images: {"amneziawg-go": {latest_tag: "3.1.20260828", latest_digest: "sha256:x", alias_latest_digest: $d}}}' \
    >"$GOAMNEZIA_DIR/upstream/last_check.json"
sh_fn '_profile_card awg2' '0\n'
check "дайджест совпал с latest на Docker Hub — «3.1.20260828 (образ приложения Amnezia, тег latest)»" \
    'grep -qF "Версия ядра: 3.1.20260828 (образ приложения Amnezia, тег latest)" <<<"$out"' "$(grep -F "Версия ядра" <<<"$out")"
sh_fn 'amnezia_profile_status awg2'
check "status: core_version 3.1.20260828" '[ "$(jq -r .core_version <<<"$out" 2>/dev/null)" = 3.1.20260828 ]' "$out"
sh_fn '_status_report'
check "«Статус»: «3.1.20260828 (образ приложения …)» вместо «образ latest»" \
    'grep -qF "3.1.20260828 (образ приложения Amnezia" <<<"$out" && ! grep -qF "образ latest" <<<"$out"' "$(grep -F UDP <<<"$out")"
sh_fn 'amnezia_profile_set_mtu() { :; }; _profile_set_mtu_interactive awg2' '0\n'
check "MTU не задан — «сейчас 1280 — стандарт, так в ключах»" 'grep -qF "сейчас 1280 — стандарт, так в ключах" <<<"$out"' "$(grep -F MTU <<<"$out")"

section "A-14: «Диагностика» на поставленном сервере — без выбора режимов установки"
sh_fn 'preflight_modes() { echo "{\"pro\":{\"reasons\":[]},\"warnings\":[]}"; }; preflight_render_modes() { echo "РЕЖИМЫ-УСТАНОВКИ"; }; _diagnose_screen'
check "поставленный сервер без неполадок портов — блока режимов нет" '! grep -qF "РЕЖИМЫ-УСТАНОВКИ" <<<"$out"' "$(tail -8 <<<"$out")"
sh_fn 'preflight_modes() { echo "{\"pro\":{\"reasons\":[\"x\"]},\"warnings\":[]}"; }; preflight_render_modes() { echo "РЕЖИМЫ-УСТАНОВКИ"; }; _diagnose_screen'
check "есть неполадка портов — блок на месте" 'grep -qF "РЕЖИМЫ-УСТАНОВКИ" <<<"$out"' "$(tail -8 <<<"$out")"

section "R-1 (CRITICS-FIX-2): отзыв страницы — выбор «и подписку» по подписке СЕЙЧАС, а не по снимку при создании"
# Страница отдаёт адрес подписки на лету, поле subscription — снимок при создании (у старых страниц его нет).
# pg_stub <subscription|null> <есть подписка сейчас: 1|""> [открытий] [nokeys] — запись страницы и подписка; отзыв пишет sub
# (и «keys» при keys=on, B3-3) в файл. Чьи ключи перевыпустит ядро (Н-1): awg2 страницы, с подпиской — ещё awg3 её линии;
# nokeys — ни одного ключа на сервере.
pg_stub() {
    printf '%s' 'pages_list() { jq -cn --argjson s '"'$1'"' '"'"'{pages: [{id: "p-abcd12", user: "anna", created: "2026-10-16T08:00:00Z", expires: "2026-10-17T08:00:00Z", forever: false, visits: '"${3:-0}"', last_visit: null, profiles: ["awg2"], mirror: true, subscription: $s}], count: 1}'"'"'; }
pages_revoke() { printf "%s %s%s\n" "$1" "$2" "$([ "$3" = on ] && echo " keys")" >>"'"$TMP"'/revoke.log"; echo "{}"; }
ga_subs_exists() { [ "$1" = anna ] && [ -n "'"$2"'" ]; }
_ga_subs_owner_gone() { return 1; }
ga_pages_revoke_key_profiles() { if [ -n "'"${4:-}"'" ]; then echo "{\"page\":[],\"sub\":[]}"; elif [ "$3" = on ]; then echo "{\"page\":[\"awg2\"],\"sub\":[\"awg3\"]}"; else echo "{\"page\":[\"awg2\"],\"sub\":[]}"; fi; }
_pr_name() { echo "AmneziaWG-$1"; }
confirm() { local a=""; echo "  $1 [1 — да, 0 — нет]:" >&2; IFS= read -r a || true; [ "$a" = 1 ]; }
_pause() { :; };'
}
for sv in null '"off"'; do
    rm -f "$TMP/revoke.log"
    sh_fn "$(pg_stub "$sv" 1)"'_pages_card p-abcd12 anna' '2\n1\n'
    check "subscription=$sv, подписка есть сейчас — выбор показан, без «рекомендуется»; пункт 1 — отзыв с подпиской" \
        'grep -qF "Отозвать страницу и перевыпустить подписку" <<<"$out" && ! grep -qF "рекомендуется" <<<"$out" && [ "$(cat "$TMP/revoke.log")" = "p-abcd12 on" ]' \
        "$(tail -12 <<<"$out"); $(cat "$TMP/revoke.log" 2>/dev/null)"
done
rm -f "$TMP/revoke.log"
sh_fn "$(pg_stub '"created"' 1)"'_pages_card p-abcd12 anna' '2\n2\n'
check "subscription=created и подписка есть — «рекомендуется»; пункт 2 — только страница" \
    'grep -qF "Отозвать страницу и перевыпустить подписку (рекомендуется)" <<<"$out" && [ "$(cat "$TMP/revoke.log")" = "p-abcd12 off" ]' \
    "$(tail -12 <<<"$out"); $(cat "$TMP/revoke.log" 2>/dev/null)"
rm -f "$TMP/revoke.log"
sh_fn "$(pg_stub '"created"' "")"'_pages_card p-abcd12 anna' '2\n1\n'
check "subscription=created, а подписки уже нет — выбора нет, прежний вопрос; отзыв без подписки" \
    '! grep -qF "перевыпустить подписку" <<<"$out" && grep -qF "Отозвать страницу p-abcd12?" <<<"$out" && [ "$(cat "$TMP/revoke.log")" = "p-abcd12 off" ]' \
    "$(tail -12 <<<"$out"); $(cat "$TMP/revoke.log" 2>/dev/null)"

section "B3-3: страницу открывали — число открытий и вопрос про ключи (по умолчанию нет)"
rm -f "$TMP/revoke.log"
sh_fn "$(pg_stub '"created"' "" 4)"'_pages_card p-abcd12 anna' '2\n1\n1\n'
check "открывали 4 раза: «Открытий страницы: 4», ключи у получателя продолжат работать; «да» — отзыв с keys=on" \
    'grep -qF "Открытий страницы: 4" <<<"$out" && grep -qF "продолжат работать" <<<"$out" && grep -qF "Перевыпустить и ключи пользователя на профилях страницы (AmneziaWG-awg2)?" <<<"$out" && ! grep -qF "отдаёт подписка" <<<"$out" && [ "$(cat "$TMP/revoke.log")" = "p-abcd12 off keys" ]' \
    "$(tail -12 <<<"$out"); $(cat "$TMP/revoke.log" 2>/dev/null)"
rm -f "$TMP/revoke.log"
sh_fn "$(pg_stub '"created"' 1 2)"'_pages_card p-abcd12 anna' '2\n1\n\n'
check "с подпиской и открытиями: подписку перевыпустили, на вопрос про ключи — Enter (нет) — keys не уходит" \
    'grep -qF "Открытий страницы: 2" <<<"$out" && [ "$(cat "$TMP/revoke.log")" = "p-abcd12 on" ]' \
    "$(tail -12 <<<"$out"); $(cat "$TMP/revoke.log" 2>/dev/null)"
rm -f "$TMP/revoke.log"
sh_fn "$(pg_stub '"created"' 1 2)"'_pages_card p-abcd12 anna' '2\n1\n1\n'
check "Н-1: с подпиской вопрос называет и профили линий подписки; «да» — отзыв с подпиской и ключами" \
    'grep -qF "на профилях страницы (AmneziaWG-awg2) и на профилях, которые отдаёт подписка (AmneziaWG-awg3)?" <<<"$out" && [ "$(cat "$TMP/revoke.log")" = "p-abcd12 on keys" ]' \
    "$(tail -12 <<<"$out"); $(cat "$TMP/revoke.log" 2>/dev/null)"
rm -f "$TMP/revoke.log"
sh_fn "$(pg_stub '"created"' "" 3 nokeys)"'_pages_card p-abcd12 anna' '2\n1\n'
check "Н-1: открывали, но ключей на сервере нет (из приложения) — вопроса про ключи нет" \
    '! grep -qF "Перевыпустить и ключи" <<<"$out" && [ "$(cat "$TMP/revoke.log")" = "p-abcd12 off" ]' \
    "$(tail -12 <<<"$out"); $(cat "$TMP/revoke.log" 2>/dev/null)"
rm -f "$TMP/revoke.log"
sh_fn "$(pg_stub '"created"' "")"'_pages_card p-abcd12 anna' '2\n1\n'
check "не открывали — вопроса про ключи нет" \
    '! grep -qF "Открытий страницы" <<<"$out" && ! grep -qF "ключи пользователя на профилях" <<<"$out" && [ "$(cat "$TMP/revoke.log")" = "p-abcd12 off" ]' \
    "$(tail -12 <<<"$out")"

section "Н-4: SSH-окно перевыпуска ключа — по фактам, как панель (страница, подписка до N ч, вручную)"
RF_STUB='pages_list() { echo "{\"pages\":[{\"id\":\"p-1\",\"user\":\"anna\",\"profiles\":[\"awg2\"]},{\"id\":\"p-2\",\"user\":\"anna\",\"profiles\":[\"awg3\"]}]}"; }
ga_subs_exists() { [ "$1" = anna ]; }; _ga_subs_owner_gone() { return 1; }; ga_subs_dir() { echo /nonexistent; }
ga_users_get() { echo "{\"name\":\"$1\",\"enabled\":true}"; }; ga_subs_update_hours() { echo 6; }
ga_subs_update_slow() { echo "[{\"name\":\"FlClash\",\"hours\":24}]"; }
ga_subs_lines_json() { echo "[{\"line\":\"3.1\",\"profile\":\"awg3\",\"has_key\":true},{\"line\":\"2.0\",\"profile\":\"awg2\",\"has_key\":false}]"; };'
sh_fn "$RF_STUB"'echo "A=$(_user_reissue_facts anna awg3) B=$(_user_reissue_facts anna awg2) C=$(_user_reissue_facts anna awg1)"'
check "факты: awg3 — страница и подписка, awg2 — страница без подписки (ключ из приложения), awg1 — ничего; часы из настроек и update_slow" \
    'grep -qF "A=1 1 6 [{\"name\":\"FlClash\",\"hours\":24}] B=1 0 6 [{" <<<"$out" && grep -qF " C=0 0 6 [{" <<<"$out"' "$out"
sh_fn "$RF_STUB"'_user_reissue_facts_text "1 1 6"; echo; _user_reissue_facts_text "1 1 6" done; echo; _user_reissue_facts_text "0 0 6"; echo; _user_reissue_facts_text "? 0 6"'
check "тексты: подписка «до 6 ч» и «Обновить», после — «само (до 6 ч)»; ни страницы, ни подписки — «отправить из карточки»; не знаем — условно" \
    'grep -qF "открыть её заново" <<<"$out" && grep -qF "(до 6 ч)" <<<"$out" && grep -qF "«Обновить»" <<<"$out" && grep -qF "заберёт его само (до 6 ч)" <<<"$out" && grep -qF "Страницы подключения с этим профилем и подписки у пользователя нет" <<<"$out" && grep -qF "Если у пользователя есть страница подключения" <<<"$out"' "$out"
section "A4-3/B4-4: SSH-окно перевыпуска — срок с приложениями, которые забирают подписку реже (update_slow)"
sh_fn "$RF_STUB"'_user_reissue_facts_text "1 1 12 [{\"name\":\"Box\",\"hours\":48},{\"name\":\"Cat\",\"hours\":24},{\"name\":\"Clash Meta\",\"hours\":24},{\"name\":\"Fast\",\"hours\":6}]"; echo; _user_reissue_facts_text "0 1 12 [{\"name\":\"FlClash\",\"hours\":24}]" done; echo; _user_reissue_facts_text "? ? 12 [{\"name\":\"FlClash\",\"hours\":24}]"; echo; _user_reissue_facts_text "0 1 24 [{\"name\":\"FlClash\",\"hours\":24}]"; echo; _user_reissue_facts_text "0 1 12 junk"'
check "«до 12 ч, у Box — до 48 ч, у Cat, Clash Meta — до 24 ч»; итог и «не знаем» — так же; не дольше совета и мусор — «до N ч»" \
    'grep -qF "при следующем обновлении (до 12 ч, у Box — до 48 ч, у Cat, Clash Meta — до 24 ч)" <<<"$out" && grep -qF "заберёт его само (до 12 ч, у FlClash — до 24 ч)" <<<"$out" && grep -qF "обновит само (до 12 ч, у FlClash — до 24 ч)" <<<"$out" && grep -qF "при следующем обновлении (до 24 ч)," <<<"$out" && grep -qF "при следующем обновлении (до 12 ч)," <<<"$out" && ! grep -qF "Fast" <<<"$out"' "$out"

section "A3-8: байты в SSH — как в панели (одна цифра после запятой с округлением, от 100 — целым)"
sh_fn 'for b in 1023 1536 42906 50893 102400 104857 1073741824 1610612736 1047552; do printf "%s=%s\n" "$b" "$(_human_bytes "$b")"; done'
check "1023 Б, 1,5 КБ, 41,9 КБ, 49,7 КБ, 100 КБ, 102 КБ, 1,0 ГБ, 1,5 ГБ, 1023 КБ" \
    'grep -qx "1023=1023 Б" <<<"$out" && grep -qx "1536=1,5 КБ" <<<"$out" && grep -qx "42906=41,9 КБ" <<<"$out" && grep -qx "50893=49,7 КБ" <<<"$out" && grep -qx "102400=100 КБ" <<<"$out" && grep -qx "104857=102 КБ" <<<"$out" && grep -qx "1073741824=1,0 ГБ" <<<"$out" && grep -qx "1610612736=1,5 ГБ" <<<"$out" && grep -qx "1047552=1023 КБ" <<<"$out"' "$out"

DONE=1
printf '\nИТОГ: %s прошло, %s не прошло\n' "$pass" "$fail"
[ "$fail" = 0 ]
