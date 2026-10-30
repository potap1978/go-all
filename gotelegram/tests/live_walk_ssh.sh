#!/usr/bin/env bash
# Прогон SSH-меню: каждый экран отрисовывается по-настоящему, вывод проверяется.
#
# t() при отсутствии перевода печатает сам ключ, поэтому дырка в словаре видна
# прямо в выводе экрана — её и ловим, вместе с ошибками bash и пустыми экранами.
# Действующие пункты (установка, удаление, рестарт, смена режима, certbot) не
# запускаются: они перечислены явно.
set +e
export TERM=dumb LANG=C.UTF-8

SRC=/tmp/gt_menu_probe.sh
sed 's/^main "\$@"$/: # обезврежено: стенд вызывает функции сам/' "${GT_ROOT:-/opt/gotelegram}"/install.sh > "$SRC"
grep -q 'обезврежено' "$SRC" || { echo "!! не удалось обезвредить main"; exit 2; }

cd "${GT_ROOT:-/opt/gotelegram}" || exit 2
# shellcheck disable=SC1090
source "$SRC" >/dev/null 2>&1

command -v show_main_menu >/dev/null || { echo "!! функции не подключились"; exit 2; }

# Экраны, которые только рисуют
SAFE=(show_main_menu submenu_proxy submenu_manage submenu_about menu_version
      menu_status menu_link menu_share menu_logs menu_bot menu_language
      menu_promo submenu_stats
      # второй круг: то, что вызывается пунктами подменю
      mode_menu menu_users warp_egress_menu miniapp_menu bot_show_logs
      interactive_restore telemt_version_change admin_web_screen)
# Что не трогаем и почему
declare -A SKIP=(
  [menu_install]="ставит прокси поверх рабочего сервера"
  [menu_remove]="удаляет установку"
  [menu_restart]="рвёт живые сессии клиентов"
  [menu_change_mode]="меняет режим lite/pro"
  [menu_website]="внутри certbot renew — лимиты Let's Encrypt, ТЗ §14"
  [interactive_backup]="создаёт бекап (проверяется отдельно)"
  [bot_install]="переустанавливает бота поверх работающего"
  [bot_remove]="удаляет бота"
  [bot_edit_config]="правит .env бота интерактивно"
)

pass=0; fail=0
printf '%-26s %s\n' "экран" "итог"
printf '%s\n' "──────────────────────────────────────────────────────────────────────────────"

for fn in "${SAFE[@]}"; do
    if ! type "$fn" >/dev/null 2>&1; then
        printf '  %-24s ✗ функции нет в install.sh\n' "$fn"; fail=$((fail+1)); continue
    fi
    out=$(printf '0\n0\n0\n' | timeout 25 bash -c "
        set +e; export TERM=dumb
        source '$SRC' >/dev/null 2>&1
        $fn
    " 2>&1)
    rc=$?
    clean=$(printf '%s' "$out" | sed -e 's/\x1b\[[0-9;?]*[a-zA-Z]//g')

    notes=()
    [ $rc -eq 124 ] && notes+=("завис (25с)")
    printf '%s' "$clean" | grep -qiE 'unbound variable|command not found|syntax error|: line [0-9]+:' \
        && notes+=("ошибка bash: $(printf '%s' "$clean" | grep -m1 -iE 'unbound variable|command not found|syntax error|: line [0-9]+:' | cut -c1-60)")
    # непереведённый ключ: t() печатает сам ключ вида menu_foo/svc_bar
    key=$(printf '%s' "$clean" | grep -oE '(^|[[:space:]])(menu|svc|net|stats|btn|msg|err|warn|opt|bot|site|ssl|warp)_[a-z0-9_]{3,}' | head -1 | tr -d ' ')
    [ -n "$key" ] && notes+=("непереведён: $key")
    [ -z "$(printf '%s' "$clean" | tr -d '[:space:]')" ] && notes+=("пустой экран")

    if [ ${#notes[@]} -eq 0 ]; then
        printf '  %-24s ✓\n' "$fn"; pass=$((pass+1))
    else
        printf '  %-24s ✗ %s\n' "$fn" "$(IFS='; '; echo "${notes[*]}")"; fail=$((fail+1))
    fi
done

for fn in "${!SKIP[@]}"; do
    type "$fn" >/dev/null 2>&1 \
      && printf '  %-24s · ПРОПУЩЕНО: %s\n' "$fn" "${SKIP[$fn]}" \
      || printf '  %-24s ✗ функции нет в install.sh\n' "$fn"
done

printf '%s\n' "──────────────────────────────────────────────────────────────────────────────"
printf '  экранов: %d   чисто: %d   проблем: %d   пропущено: %d\n' \
       $((pass+fail)) $pass $fail ${#SKIP[@]}
