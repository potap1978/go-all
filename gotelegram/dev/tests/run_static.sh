#!/usr/bin/env bash
# Проверки, которым не нужен сервер.
set -u
cd "$(dirname "$0")/.." || exit 2
fail=0
for t in tests/check_menu.py tests/check_i18n_bot.py tests/check_i18n_ssh.py \
         tests/test_bot_features.py tests/test_admin_features.py tests/test_bootstrap.py; do
    printf '\n════ %s\n' "$t"
    case "$t" in
        */check_i18n_bot.py) python3 "$t" gotelegram-bot ;;
        */check_i18n_ssh.py) python3 "$t" . ;;
        *)                   python3 "$t" ;;
    esac || fail=$((fail+1))
done
printf '\n──────────────────────────────────────────\n'
[ $fail -eq 0 ] && echo "все проверки пройдены" || echo "НЕ ПРОШЛО проверок: $fail"
exit $fail
