#!/usr/bin/env bash
# goDNS — меню исполняется, а не только грепается.
#
# Мутационный аудит нашёл дыру размером в файл: godns.sh (около двух тысяч
# строк) не запускал ни один тест. Перепутанные местами пункты меню — 1↔2
# в разделе «Наблюдение» — прошли ВЕСЬ набор зелёными: структурные стражи
# смотрят на форму, а не на то, что случится по нажатию.
#
# Здесь меню действительно исполняется. Проверяется два разных
# утверждения:
#
#   1) НАЖАТИЕ N ЗОВЁТ ИМЕННО ЭТО ДЕЙСТВИЕ. Таблица ниже — контракт: она
#      закреплена руками, и любая перестановка пунктов её ломает. Да, она
#      повторяет код — в этом и смысл: без второй записи перестановку
#      нечем заметить.
#
#   2) ЧТО НАПЕЧАТАНО, ТО И ПРИНИМАЕТСЯ. Это утверждение выводится из
#      самого меню, а не из таблицы: каждый номер на экране обязан иметь
#      ветку в case, и каждая ветка — строку на экране. Именно так ловится
#      «пункт 6 напечатан, но не работает» и дубль «1)», уже случавшийся.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ "${BASH_VERSINFO[0]}" -lt 4 ]; then
    echo "  test_menu.sh: нужен bash 4.0+ — сейчас ${BASH_VERSION}." >&2
    exit 0
fi

G=$'\033[0;32m'; R=$'\033[0;31m'; D=$'\033[2m'; B=$'\033[1m'; N=$'\033[0m'
PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); printf "  ${G}✓${N} %s\n" "$1"; }
bad() { FAIL=$((FAIL+1)); printf "  ${R}✗${N} %s\n" "$1"
        [ -n "${2:-}" ] && printf "      ${D}%s${N}\n" "$2"; }
head_() { printf "\n${B}── %s${N}\n" "$1"; }

SB=$(mktemp -d) || exit 1
trap 'rm -rf "$SB"' EXIT

# ── песочница по путям И по действиям над системой ──────────────────────
# CLAUDE.md: заглушки объявляются ДО подключения библиотек и не молчат, а
# ПИШУТ вызовы — иначе они спрятали бы настоящую поломку.
SYSLOG="${SB}/system-calls.log"
: > "$SYSLOG"
systemctl() { echo "systemctl $*" >> "$SYSLOG"; return 0; }
userdel()   { echo "userdel $*"   >> "$SYSLOG"; return 0; }
useradd()   { echo "useradd $*"   >> "$SYSLOG"; return 0; }
journalctl() { echo "journalctl $*" >> "$SYSLOG"; return 0; }
ufw()       { echo "ufw $*"       >> "$SYSLOG"; return 0; }
nft()       { echo "nft $*"       >> "$SYSLOG"; return 1; }
export -f systemctl userdel useradd journalctl ufw nft

export GODNS_DIR="${SB}/opt" GODNS_ETC="${SB}/etc"
export GODNS_CONFIG="${SB}/opt/config.json"
export GODNS_BACKUP_DIR="${SB}/backups"
export GODNS_BOT_UNIT_DIR="${SB}/units"
mkdir -p "$GODNS_DIR" "$GODNS_ETC" "$GODNS_BACKUP_DIR" "$GODNS_BOT_UNIT_DIR"

source "${SCRIPT_DIR}/lib/common.sh"
source "${SCRIPT_DIR}/lib/i18n.sh"
load_language >/dev/null 2>&1 || true

# godns.sh при `source` запускает main() — берём только функции меню.
eval "$(sed -n '/^_back()/,/^}/p' "${SCRIPT_DIR}/godns.sh")"
for fn in submenu_connect submenu_observe submenu_dns submenu_godns wizard_ratelimit \
          submenu_remote submenu_backup_schedule; do
    eval "$(sed -n "/^${fn}()/,/^}/p" "${SCRIPT_DIR}/godns.sh")"
done

# ── подмены, превращающие меню в наблюдаемое ────────────────────────────
ACTIONS="${SB}/actions.log"
# Пишем ВЕСЬ вызов, а не только имя: в «Расписании» все четыре пункта зовут
# одну функцию и различаются аргументом (daily/weekly/monthly/off) — без
# аргумента перестановка пунктов была бы неотличима.
run_action() { echo "$*" >> "$ACTIONS"; return 0; }
# Заголовки и состояния пунктов зовут настоящие функции движка; в песочнице
# их нет и быть не должно — меню обязано рисоваться и без работающей службы.
engine_query_log_state() { echo off; }
engine_is_installed()    { return 1; }
engine_is_running()      { return 1; }
godns_bot_installed()    { return 1; }
webui_installed()        { return 1; }
godns_backup_schedule_label() { echo "выключено"; }
prov_custom_count() { echo 0; }
# Настроенная машина: без домена «Как подключиться» честно выходит на
# первой же строке, и проверять там было бы нечего.
config_get() {
    case "$1" in
        domain)      echo "example.test" ;;
        doh_port)    echo 443 ;;
        dot_port)    echo 853 ;;
        doh_path)    echo "abcdefgh1234" ;;
        dot_enabled) echo yes ;;
        policy)      echo roundrobin ;;
        upstream_ids) echo "cloudflare" ;;
        *)           echo "" ;;
    esac
}
sleep() { :; }

# Одно нажатие: печатаем экран, жмём N, затем 0 (выход).
#
# Экран сам может звать действия при отрисовке: «Расписание» показывает
# состояние таймера через run_action, и наивный `head -1` возвращал ЕГО,
# а не выбранный пункт. Поэтому сначала снимаем базовый след «зашёл и
# сразу вышел», а потом вычитаем его из следа нажатия.
_press_raw() {
    : > "$ACTIONS"
    printf '%s' "$2" | "$1" >/dev/null 2>&1 || true
    cat "$ACTIONS" 2>/dev/null
}
press() {  # press <подменю> <клавиша> → вызванное действие с аргументами
    local base chosen
    base=$(_press_raw "$1" $'0\n')
    chosen=$(_press_raw "$1" "$2"$'\n0\n')
    # grep -vxF -f: убираем строки отрисовки, оставляя только новое.
    printf '%s\n' "$chosen" | grep -vxF -f <(printf '%s\n' "$base") 2>/dev/null | head -1
}
screen() { printf '0\n' | "$1" 2>&1 | sed 's/\x1b\[[0-9;]*m//g'; }

# ═══════════════════════════════════════════════════════════════════════
head_ "нажатие зовёт именно своё действие"
# Контракт. Ломается при любой перестановке пунктов.
while IFS='|' read -r menu key want; do
    [ -n "${menu:-}" ] || continue
    got=$(press "$menu" "$key")
    if [ "$got" = "$want" ]; then
        ok "${menu#submenu_} ${key} → ${want}"
    else
        bad "${menu#submenu_} ${key} → ${want}" "вызвалось: ${got:-<ничего>}"
    fi
done <<'TABLE'
submenu_connect|1|selftest_snip_phone_qr
submenu_connect|2|selftest_snip_apple_profile
submenu_connect|3|selftest_snip_routers
submenu_connect|4|selftest_snip_os
submenu_connect|5|selftest_snip_browsers
submenu_connect|6|selftest_snip_all
submenu_observe|1|selftest_quick
submenu_observe|2|selftest_full
submenu_observe|3|selftest_leak_audit
submenu_observe|4|selftest_external_check
submenu_observe|5|engine_live_monitor
submenu_observe|6|engine_upstream_report
submenu_observe|7|godns_stats_screen
submenu_observe|8|engine_logs_follow
submenu_observe|9|godns_logs_report
submenu_observe|10|godns_logs_cleanup_now
submenu_observe|11|logs_rate_and_source
submenu_observe|12|toggle_query_log
submenu_dns|1|reconfigure wizard_upstreams
submenu_dns|2|reconfigure wizard_policy
submenu_dns|3|measure_only
submenu_dns|4|reconfigure custom_upstream_add
submenu_dns|5|reconfigure custom_upstream_remove
submenu_dns|6|reconfigure wizard_ports
submenu_dns|8|reconfigure toggle_dot
submenu_dns|9|reconfigure wizard_cert
submenu_dns|10|cert_show_current
submenu_dns|11|cert_reinstall_hook
submenu_dns|12|reconfigure certs_issue_interactive
submenu_dns|13|reconfigure wizard_ratelimit
submenu_godns|1|engine_status
submenu_godns|2|service_restart
submenu_godns|3|engine_reload_certs
submenu_godns|4|engine_show_config
submenu_godns|5|godns_backup_interactive
submenu_godns|6|godns_backup_list
submenu_godns|7|godns_backup_restore_interactive
submenu_godns|8|prune_backups_ask
submenu_godns|9|submenu_backup_schedule
submenu_godns|10|godns_logs_apply_all
submenu_godns|11|language_menu
submenu_godns|12|submenu_about
submenu_godns|99|uninstall_godns
submenu_remote|1|godns_bot_setup_wizard
submenu_remote|2|godns_bot_status
submenu_remote|3|godns_bot_claim_code_show
submenu_remote|4|godns_bot_admins_show
submenu_remote|5|godns_bot_restart
submenu_remote|6|godns_bot_logs
submenu_remote|7|godns_bot_remove
submenu_remote|8|webui_install
submenu_remote|9|webui_show_access
submenu_remote|10|webui_restart
submenu_backup_schedule|1|godns_backup_schedule_set daily
submenu_backup_schedule|2|godns_backup_schedule_set weekly
submenu_backup_schedule|3|godns_backup_schedule_set monthly
submenu_backup_schedule|4|godns_backup_schedule_set off
TABLE

# ═══════════════════════════════════════════════════════════════════════
head_ "что напечатано, то и принимается"
# Выводится из самого меню, а не из таблицы выше: сравниваем номера на
# экране с метками case. Так ловится «пункт напечатан, но мёртв» и дубль
# номера — обе ошибки в этом меню уже случались.
for menu in submenu_connect submenu_observe submenu_dns submenu_godns \
            submenu_remote submenu_backup_schedule; do
    shown=$(screen "$menu" \
        | grep -oE '^[[:space:]]+[0-9]+\)' | tr -d ' )' | sort -u)
    branches=$(sed -n "/^${menu}()/,/^}/p" "${SCRIPT_DIR}/godns.sh" \
        | sed -n '/case "\$choice" in/,/esac/p' \
        | grep -oE '^[[:space:]]+[0-9]+\)' | tr -d ' )' | sort -u)
    if [ -z "$shown" ]; then
        bad "${menu#submenu_}: экран не напечатал ни одного номера"
        continue
    fi
    dupes=$(printf '%s\n' "$shown" | uniq -d)
    if [ -n "$dupes" ]; then
        bad "${menu#submenu_}: номер напечатан дважды" "$(echo $dupes)"
    else
        ok "${menu#submenu_}: номера на экране не повторяются ($(echo $shown | wc -w | tr -d ' ') шт)"
    fi
    dead=$(comm -23 <(printf '%s\n' "$shown") <(printf '%s\n' "$branches"))
    if [ -n "$dead" ]; then
        bad "${menu#submenu_}: напечатан, но не принимается" "$(echo $dead)"
    else
        ok "${menu#submenu_}: каждый напечатанный номер принимается"
    fi
    hidden=$(comm -13 <(printf '%s\n' "$shown") <(printf '%s\n' "$branches"))
    if [ -n "$hidden" ]; then
        bad "${menu#submenu_}: ветка есть, строки нет" "$(echo $hidden)"
    else
        ok "${menu#submenu_}: каждая ветка видна на экране"
    fi
done

# ═══════════════════════════════════════════════════════════════════════
head_ "выход и неверный ввод"
: > "$ACTIONS"; GODNS_SUBMENU_BACK="no"
submenu_observe >/dev/null 2>&1 <<< '0' || true
[ "$GODNS_SUBMENU_BACK" = "yes" ] \
    && ok "«0» помечает выход по «Назад» (одно нажатие, не два)" \
    || bad "«0» не пометил выход" "GODNS_SUBMENU_BACK=$GODNS_SUBMENU_BACK"

: > "$ACTIONS"; GODNS_SUBMENU_BACK="no"
submenu_observe </dev/null >/dev/null 2>&1 || true
[ "$GODNS_SUBMENU_BACK" = "yes" ] \
    && ok "Ctrl-D тоже помечает выход по «Назад»" \
    || bad "Ctrl-D не пометил выход"

out=$(printf 'чепуха\n0\n' | submenu_observe 2>&1 | sed 's/\x1b\[[0-9;]*m//g')
if printf '%s' "$out" | grep -q "$(t invalid_choice)"; then
    ok "неверный ввод ругается и остаётся в подменю"
else
    bad "неверный ввод не назван" "$(printf '%s' "$out" | tail -2)"
fi

# ═══════════════════════════════════════════════════════════════════════
head_ "меню не трогает систему"
# Тот же инвариант, что у остальных наборов: прогон на сервере с
# установленным goDNS однажды остановил живого бота и удалил его
# пользователя. Здесь действия заглушены run_action, но экраны рисуют
# состояние — и вот они системные команды звать МОГУТ.
if [ -s "$SYSLOG" ]; then
    bad "рисование меню позвало системные команды" "$(head -3 "$SYSLOG")"
else
    ok "рисование меню не позвало ни systemctl, ни userdel, ни ufw"
fi

head_ "wizard_ratelimit исполняется: Enter, Ctrl-D, мусор, нули, переполнение"
# Мастер лимита никем не исполнялся (мутация ревью: «Ctrl-D = Enter» и
# опечатки в проверке ввода проезжали все наборы). Гоняем его с подложенным
# движком и config_set: что записано и с каким кодом вышли.
_rl_run() {  # <stdin-текст или пусто для Ctrl-D>
    _RL=200
    engine_ratelimit_qps() { printf '%s' "$_RL"; }
    config_set() { _RL="$2"; return 0; }
    # НЕ через pipe: правая часть конвейера — подоболочка, и подложенный
    # config_set менял бы _RL только там (первая версия теста так и врала).
    if [ -n "${1:-}" ]; then wizard_ratelimit < <(printf '%b' "$1") >/dev/null 2>&1; else wizard_ratelimit </dev/null >/dev/null 2>&1; fi
    printf 'rc=%s|%s' "$?" "$_RL"
}
_w=$( _rl_run "" );               [ "$_w" = "rc=1|200" ] && ok "Ctrl-D — отмена, значение не тронуто" || bad "Ctrl-D: $_w"
_w=$( _rl_run '\n' );             [ "$_w" = "rc=0|200" ] && ok "Enter — оставить как есть" || bad "Enter: $_w"
_w=$( _rl_run '0\n' );            [ "$_w" = "rc=0|0" ]   && ok "0 — лимит выключен" || bad "0: $_w"
_w=$( _rl_run '08\n' );           [ "$_w" = "rc=0|8" ]   && ok "08 → 8 (не восьмеричное)" || bad "08: $_w"
_w=$( _rl_run 'abc\n300\n' );    [ "$_w" = "rc=0|300" ] && ok "мусор отвергнут, 300 принято" || bad "мусор→300: $_w"
_w=$( _rl_run '99999999999999999999\n250\n' ); [ "$_w" = "rc=0|250" ] && ok "двадцать знаков отвергнуты, 250 принято" || bad "переполнение: $_w"
_w=$( _rl_run '100001\n100000\n' ); [ "$_w" = "rc=0|100000" ] && ok "100001 отвергнуто, 100000 принято" || bad "потолок: $_w"

printf "\n${D}%s${N}\n" "────────────────────────────────────────────────────────────"
if [ "$FAIL" -eq 0 ]; then
    printf "  ${G}✓ все %d проверок пройдены${N}\n\n" "$PASS"; exit 0
fi
printf "  ${R}✗ провалено %d из %d${N}\n\n" "$FAIL" "$((PASS+FAIL))"; exit 1
