#!/usr/bin/env bash
# lib/selftest.sh — автопроверка функций БЕЗ сервера (bash install.sh --selftest).
# Цель: ловить регрессии до выката. Ничего не устанавливает и не меняет систему:
# все проверки — на временных копиях в /tmp, сетевые/системные вызовы застабены.

# Счётчики — в файле: часть проверок идёт в подоболочках ( ... ), где переменные не переживают.
ST_TALLY="${ST_TALLY:-}"
_st_add()  { [ -n "$ST_TALLY" ] && printf '%s\n' "$1" >> "$ST_TALLY" 2>/dev/null; return 0; }
_st_count() { local n=0; [ -f "$ST_TALLY" ] && n=$(grep -c "^$1\$" "$ST_TALLY" 2>/dev/null || true); [[ "$n" =~ ^[0-9]+$ ]] || n=0; printf '%s' "$n"; }
_st_ok()   { _st_add PASS; printf '  \033[32m✓\033[0m %s\n' "$1"; }
_st_bad()  { _st_add FAIL; printf '  \033[31m✗\033[0m %s \033[2m(ждали: %s, получили: %s)\033[0m\n' "$1" "$2" "$3"; }
_st_skip() { _st_add SKIP; printf '  \033[2m• пропуск: %s (%s)\033[0m\n' "$1" "$2"; }
# Предупреждение: на живом сервере что-то не так, но это не регрессия кода.
_st_warn() { _st_add WARN; printf '  \033[33m!\033[0m %s\n' "$1"; }
_st_eq()   { [ "$2" = "$3" ] && _st_ok "$1" || _st_bad "$1" "$3" "$2"; }
_st_rc()   { local d="$1"; shift; if "$@" >/dev/null 2>&1; then _st_ok "$d"; else _st_bad "$d" "rc=0" "rc=$?"; fi; }
_st_nrc()  { local d="$1"; shift; if "$@" >/dev/null 2>&1; then _st_bad "$d" "rc≠0" "rc=0"; else _st_ok "$d"; fi; }
# Проверка JSON фильтром jq: греп по имени поля подтверждал лишь то, что функция
# что-то напечатала — имена полей есть в её же printf-строке всегда, даже в мусоре.
_st_json() { local d="$1" j="$2" q="$3"
    command -v jq >/dev/null 2>&1 || { _st_skip "$d" "нет jq"; return 0; }
    if jq -e "$q" <<<"$j" >/dev/null 2>&1; then _st_ok "$d"; else _st_bad "$d" "$q" "$j"; fi
}
_st_head() { printf '\n\033[1m%s\033[0m\n' "$1"; }
# Файл байт в байт равен ожидаемому тексту (переводы строк — как есть).
_st_file() { local d="$1" f="$2" want="$3" got
    if [ -f "$f" ] && [ "$(cat "$f"; printf x)" = "${want}x" ]; then _st_ok "$d"
    else got=$(cat "$f" 2>/dev/null | tr '\n' '|'); _st_bad "$d" "$(printf '%s' "$want" | tr '\n' '|')" "$got"; fi
}

# Раздел 12: dns_resolver_tune на временных файлах. Замер подменён таблицей
# _ST_LOSS («адрес=потерь …», остальные — 0); каждый вызов замера пишется в calls.
_st_dns_tests() {
    local base="$1/dns"; mkdir -p "$base" || return 0
    ( LOG_FILE="$base/gt.log"; : > "$LOG_FILE"
      local d="$base" M="$DNS_TUNE_MARK" calls="$base/calls" f exp o
      _dns_have_dig() { return 0; }
      _dns_ns_losses() { printf '%s\n' "$1" >> "$calls"; local kv
          for kv in ${_ST_LOSS:-}; do [ "${kv%%=*}" = "$1" ] && { echo "${kv#*=}"; return 0; }; done
          echo 0; }
      _run() { : > "$calls"; DNS_RESOLV_CONF="$1" dns_resolver_tune 2>"$base/err"; }
      # Сообщение (по ключу i18n) есть / нет в выводе последнего запуска.
      _said()  { grep -qF -- "$(tf "${@:2}")" "$base/err" && _st_ok "$1" || _st_bad "$1" "есть: $(tf "${@:2}")" "$(tr '\n' '|' < "$base/err")"; }
      _nsaid() { grep -qF -- "$(tf "${@:2}")" "$base/err" && _st_bad "$1" "нет: $(tf "${@:2}")" "есть" || _st_ok "$1"; }
      local FB="$DNS_TUNE_FB_MARK"
      _calls() { sort -u "$calls" | tr '\n' ' ' | sed 's/ $//'; }
      _no_orig() { [ ! -e "$1.gotelegram.orig" ] && _st_ok "$2: копии нет" || _st_bad "$2: копии нет" "нет" "есть"; }

      # Проверки адресов: это то, что уходит в dig.
      local a okv=1 okl=1
      for a in 1.1.1.1 2606:4700:4700::1111 fe80::1%eth0 ::ffff:1.2.3.4; do
          _dns_ns_valid "$a" || { okv=0; _st_bad "годный адрес" "$a годен" "отвергнут"; }; done
      for a in 256.1.1.1 01.1.1.1 -h +short dns.google '1.1.1.1;x' '$(id)' 1::2::3 12345::1 '' 'fe80::1%a b'; do
          _dns_ns_valid "$a" && { okv=0; _st_bad "негодный адрес" "$a отвергнут" "годен"; }; done
      [ "$okv" = 1 ] && _st_ok "в dig уходят только IPv4 и IPv6 (с %iface); -h, +short, имена, \$(…) — нет"
      for a in 127.0.0.53 127.1 ::1 0:0:0:0:0:0:0:1 ::ffff:127.0.0.1 0.0.0.0 ::; do
          _dns_ns_is_local "$a" || { okl=0; _st_bad "локальный адрес" "$a локальный" "нет"; }; done
      for a in 1.1.1.1 ::1000 10::1 2606:4700:4700::1111; do
          _dns_ns_is_local "$a" && { okl=0; _st_bad "не локальный адрес" "$a не локальный" "локальный"; }; done
      [ "$okl" = 1 ] && _st_ok "локальный кэш распознаётся (127.0.0.0/8, ::1, 0.0.0.0, ::)"

      # Ссылка (systemd-resolved и др.): ни файла, ни цели не трогаем.
      printf 'nameserver 8.8.8.8\nnameserver 1.1.1.1\n' > "$d/real"; ln -s real "$d/link"
      _ST_LOSS="8.8.8.8=3" _run "$d/link" && _st_ok "симлинк: rc 0" || _st_bad "симлинк: rc" 0 "≠0"
      { [ -L "$d/link" ] && [ "$(cat "$d/real")" = $'nameserver 8.8.8.8\nnameserver 1.1.1.1' ] && [ ! -s "$calls" ]; } \
          && _st_ok "симлинк: ссылка и цель не тронуты, замера нет" || _st_bad "симлинк" "не тронут" "изменён"
      _no_orig "$d/link" "симлинк"

      # Локальный кэш и ::1 рядом с внешним сервером — файл целиком не трогаем.
      printf 'nameserver 127.0.0.53\noptions edns0 trust-ad\nsearch .\n' > "$d/stub"
      _run "$d/stub"
      _st_file "127.0.0.53: файл не тронут" "$d/stub" $'nameserver 127.0.0.53\noptions edns0 trust-ad\nsearch .\n'
      _st_eq "127.0.0.53: замера нет" "$(_calls)" ""
      printf 'nameserver 8.8.8.8\nnameserver ::1\n' > "$d/v6lo"
      _ST_LOSS="8.8.8.8=3" _run "$d/v6lo"
      _st_file "::1 рядом с 8.8.8.8: файл не тронут" "$d/v6lo" $'nameserver 8.8.8.8\nnameserver ::1\n'

      # Менеджер DNS сам перепишет файл — не спорим с ним.
      printf '# Generated by NetworkManager\nnameserver 8.8.8.8\nnameserver 1.1.1.1\n' > "$d/nm"
      _ST_LOSS="8.8.8.8=3" _run "$d/nm"
      _st_file "NetworkManager: файл не тронут" "$d/nm" $'# Generated by NetworkManager\nnameserver 8.8.8.8\nnameserver 1.1.1.1\n'
      _no_orig "$d/nm" "NetworkManager"

      # Случай владельца: файл SolusVM, 8.8.8.8 теряет, 1.1.1.1 — нет.
      o=$'# Generated by SolusVM\nnameserver 8.8.8.8\nnameserver 1.1.1.1\n'
      f="$d/solus"; printf '%s' "$o" > "$f"; chmod 640 "$f"
      local ino0 ino1; ino0=$(ls -i "$f" | awk '{print $1}')
      _ST_LOSS="8.8.8.8=3" _run "$f" && _st_ok "SolusVM: rc 0" || _st_bad "SolusVM: rc" 0 "≠0"
      exp=$'# Generated by SolusVM\nnameserver 1.1.1.1\nnameserver 8.8.8.8\noptions timeout:1 attempts:2\n'"$M"$'\n'
      _st_file "SolusVM: 1.1.1.1 первым, options timeout:1 attempts:2, заголовок на месте" "$f" "$exp"
      _st_file "SolusVM: копия .gotelegram.orig = исходник" "$f.gotelegram.orig" "$o"
      ino1=$(ls -i "$f" | awk '{print $1}')
      _st_eq "SolusVM: запись на месте (тот же inode)" "$ino1" "$ino0"
      _st_eq "SolusVM: права сохранены" "$(ls -l "$f" | cut -c1-10)" "-rw-r-----"
      _st_eq "SolusVM: замерены оба, запасные не нужны" "$(_calls)" "1.1.1.1 8.8.8.8"
      _st_eq "SolusVM: временных файлов не осталось" "$(ls -A "$d" | grep -c '^\.gotelegram-resolv')" "0"
      grep -q 'nameserver: 8.8.8.8, 1.1.1.1 -> 1.1.1.1, 8.8.8.8' "$LOG_FILE" \
          && _st_ok "SolusVM: в журнале порядок до и после" || _st_bad "журнал" "порядок до/после" "$(tail -1 "$LOG_FILE")"
      _said "SolusVM: после записи — «поставлен ниже»" dns_tune_slow 8.8.8.8 3
      # Повторный запуск при тех же замерах — байт в байт то же, копия та же.
      cp -p "$f" "$d/solus.1"
      _ST_LOSS="8.8.8.8=3" _run "$f"
      cmp -s "$f" "$d/solus.1" && _st_ok "повтор: файл байт в байт тот же" || _st_bad "повтор" "без изменений" "$(tr '\n' '|' < "$f")"
      _nsaid "повтор: нет «поставлен ниже»" dns_tune_slow 8.8.8.8 3
      _said  "повтор: «уже стоит ниже»" dns_tune_slow_kept 8.8.8.8 3
      _said  "повтор: «без изменений»" dns_tune_ok "$f"
      _st_file "повтор: копия не изменилась" "$f.gotelegram.orig" "$o"
      _st_eq "повтор: маркер ровно один" "$(grep -c "^# goTelegram Pro dns_tune:" "$f")" "1"
      # Хостер переписал файл заново — настройка применяется снова, копия — прежняя.
      printf '# Generated by SolusVM\nnameserver 8.8.8.8\nnameserver 8.8.4.4\n' > "$f"
      _ST_LOSS="8.8.8.8=3" _run "$f"
      _st_file "хостер переписал файл: настройка снова, 8.8.4.4 первым" "$f" \
          $'# Generated by SolusVM\nnameserver 8.8.4.4\nnameserver 8.8.8.8\noptions timeout:1 attempts:2\n'"$M"$'\n'
      _st_file "хостер переписал файл: копия — самый первый исходник" "$f.gotelegram.orig" "$o"
      # Файл, который владелец поправил руками, — не трогаем вовсе (и маркер не дописываем).
      o=$'# Generated by SolusVM\nnameserver 1.1.1.1\nnameserver 8.8.8.8\noptions timeout:1 attempts:2\n'
      printf '%s' "$o" > "$d/manual"; _run "$d/manual"
      _st_file "ручная правка владельца: без изменений" "$d/manual" "$o"
      _no_orig "$d/manual" "ручная правка"

      # options: все строки — в одну на месте первой; последнее значение действует.
      printf 'nameserver 1.1.1.1\noptions rotate timeout:5 attempts:1\nsearch example.com\noptions ndots:2 attempts:4\n' > "$d/opt"
      _run "$d/opt"
      _st_file "options слиты: rotate, timeout:1, attempts:4 (последнее), ndots:2; search на месте" "$d/opt" \
          $'nameserver 1.1.1.1\noptions rotate timeout:1 attempts:4 ndots:2\nsearch example.com\n'"$M"$'\n'
      printf 'nameserver 1.1.1.1\nnameserver 8.8.8.8\noptions edns0 attempts:1 timeout:3 edns0\n' > "$d/opt2"
      _run "$d/opt2"
      _st_file "options: attempts:1 → 2, timeout:3 → 1, без дублей" "$d/opt2" \
          $'nameserver 1.1.1.1\nnameserver 8.8.8.8\noptions edns0 attempts:2 timeout:1\n'"$M"$'\n'

      # Один сервер, сбоит — запасной (первый из 1.1.1.1, 8.8.8.8, 9.9.9.9, которого
      # нет в файле и который ответил 8 из 8) ставится НИЖЕ него, с меткой отдельной
      # строкой: внутренние имена хостера остаются за его сервером (NXDOMAIN от
      # публичного glibc не переспрашивает). Запасной не замеряется и не переставляется.
      printf 'nameserver 10.0.0.1\n' > "$d/one"
      _ST_LOSS="10.0.0.1=4" _run "$d/one"
      exp=$'nameserver 10.0.0.1\n'"$FB"$'\nnameserver 1.1.1.1\noptions timeout:1 attempts:2\n'"$M"$'\n'
      _st_file "один сбоящий: запасной 1.1.1.1 ниже него, с меткой; сервер не удалён" "$d/one" "$exp"
      _st_eq "один сервер: запасные замерены вместе с ним" "$(_calls)" "1.1.1.1 10.0.0.1 8.8.8.8 9.9.9.9"
      _said "один сбоящий: после записи — «добавлен запасной»" dns_tune_fallback 10.0.0.1 4 1.1.1.1
      _ST_LOSS="10.0.0.1=4" _run "$d/one"
      _st_file "запасной, повтор при тех же замерах: байт в байт" "$d/one" "$exp"
      _st_eq "запасной, повтор: замера нет (один сервер + запасной)" "$(_calls)" ""
      _ST_LOSS="10.0.0.1=0" _run "$d/one"
      _st_file "запасной, повтор: сервер выздоровел — без изменений" "$d/one" "$exp"
      _ST_LOSS="1.1.1.1=3 10.0.0.1=4" _run "$d/one"
      _st_file "запасной, повтор: сбоят оба — без изменений" "$d/one" "$exp"
      printf 'nameserver 1.1.1.1\n' > "$d/one2"
      _ST_LOSS="1.1.1.1=3 8.8.8.8=1" _run "$d/one2"
      _st_file "один сбоящий 1.1.1.1, 8.8.8.8 ответил 7/8 → запасной 9.9.9.9 ниже" "$d/one2" \
          $'nameserver 1.1.1.1\n'"$FB"$'\nnameserver 9.9.9.9\noptions timeout:1 attempts:2\n'"$M"$'\n'
      printf 'nameserver 10.0.0.1\n' > "$d/one3"
      _ST_LOSS="10.0.0.1=4 1.1.1.1=8 8.8.8.8=1 9.9.9.9=2" _run "$d/one3"
      _st_file "один сбоящий, запасные не ответили 8/8 → ничего не добавлено" "$d/one3" \
          $'nameserver 10.0.0.1\noptions timeout:1 attempts:2\n'"$M"$'\n'
      # Метка без строки запасного (её убрали руками) — убирается; одна метка без
      # nameserver за ней не делает следующий сервер запасным.
      printf 'nameserver 1.1.1.1\nnameserver 8.8.8.8\n%s\noptions timeout:1 attempts:2\n%s\n' "$FB" "$M" > "$d/orph"
      _run "$d/orph"
      _st_file "метка запасного без сервера — убрана" "$d/orph" $'nameserver 1.1.1.1\nnameserver 8.8.8.8\noptions timeout:1 attempts:2\n'"$M"$'\n'
      # Второй сервер дописан после запасного — запасной снова последний.
      printf 'nameserver 10.0.0.1\n%s\nnameserver 1.1.1.1\nnameserver 9.9.9.9\n' "$FB" > "$d/fbmid"
      _ST_LOSS="1.1.1.1=8" _run "$d/fbmid"
      _st_file "запасной всегда после исходных серверов и не замеряется" "$d/fbmid" \
          $'nameserver 10.0.0.1\nnameserver 9.9.9.9\n'"$FB"$'\nnameserver 1.1.1.1\noptions timeout:1 attempts:2\n'"$M"$'\n'
      _st_eq "запасной не замеряется" "$(_calls)" "10.0.0.1 9.9.9.9"
      # Строку хостера убрали, остался только наш запасной — он обычный сервер, метка убрана.
      printf '%s\nnameserver 1.1.1.1\n' "$FB" > "$d/fbonly"
      _run "$d/fbonly"
      _st_file "остался только запасной: обычный сервер, метка убрана, options заданы" "$d/fbonly" \
          $'nameserver 1.1.1.1\noptions timeout:1 attempts:2\n'"$M"$'\n'
      _nsaid "остался только запасной: нет «нет nameserver»" dns_tune_skip_nons "$d/fbonly"
      printf 'nameserver 10.0.0.1' > "$d/one4"                 # без перевода строки в конце
      _run "$d/one4"
      _st_file "один здоровый: только options" "$d/one4" $'nameserver 10.0.0.1\noptions timeout:1 attempts:2\n'"$M"$'\n'

      # Все сбоят — порядок прежний. Сбоящие — вниз в прежнем взаимном порядке, гнёзда — прежние.
      printf 'nameserver 8.8.8.8\nnameserver 1.1.1.1\n' > "$d/allbad"
      _ST_LOSS="8.8.8.8=5 1.1.1.1=3" _run "$d/allbad"
      _st_file "все сбоят: порядок прежний, options заданы" "$d/allbad" \
          $'nameserver 8.8.8.8\nnameserver 1.1.1.1\noptions timeout:1 attempts:2\n'"$M"$'\n'
      printf '# hoster\nnameserver 9.9.9.9\n# keep\nnameserver 8.8.8.8\nsearch x\nnameserver 1.1.1.1\n' > "$d/three"
      _ST_LOSS="9.9.9.9=5 8.8.8.8=2" _run "$d/three"
      _st_file "три сервера: здоровый вверх, сбоящие в прежнем взаимном порядке, комментарии на местах" "$d/three" \
          $'# hoster\nnameserver 1.1.1.1\n# keep\nnameserver 9.9.9.9\nsearch x\nnameserver 8.8.8.8\noptions timeout:1 attempts:2\n'"$M"$'\n'
      printf 'nameserver 8.8.8.8\nnameserver 1.1.1.1\n' > "$d/oneloss"
      _ST_LOSS="8.8.8.8=1" _run "$d/oneloss"
      _st_file "1 потеря из 8 — не сбой, порядок прежний" "$d/oneloss" \
          $'nameserver 8.8.8.8\nnameserver 1.1.1.1\noptions timeout:1 attempts:2\n'"$M"$'\n'

      # Нет nameserver — нечего настраивать.
      printf 'search example.com\noptions ndots:2\n' > "$d/nons"; _run "$d/nons"
      _st_file "нет nameserver: файл не тронут" "$d/nons" $'search example.com\noptions ndots:2\n'
      _no_orig "$d/nons" "нет nameserver"

      # Невалидные адреса в dig не уходят и остаются на своих местах дословно.
      printf '%s\n' 'nameserver 8.8.8.8' 'nameserver -h' 'nameserver dns.google' 'nameserver $(touch pwn)' \
          'nameserver +short' 'nameserver 1.1.1.1  # main' > "$d/bad"
      _ST_LOSS="8.8.8.8=3" _run "$d/bad"
      _st_eq "невалидные адреса не замеряются" "$(_calls)" "1.1.1.1 8.8.8.8"
      exp=$'nameserver 1.1.1.1  # main\nnameserver -h\nnameserver dns.google\nnameserver $(touch pwn)\nnameserver +short\nnameserver 8.8.8.8\noptions timeout:1 attempts:2\n'"$M"$'\n'
      _st_file "невалидные строки остались дословно на своих местах" "$d/bad" "$exp"
      printf 'nameserver 127.1\nnameserver 8.8.8.8\n' > "$d/odd"; _run "$d/odd"
      _st_file "нестандартная запись IPv4 (127.1): файл не тронут" "$d/odd" $'nameserver 127.1\nnameserver 8.8.8.8\n'

      # Выключатель — файл рядом: <resolv.conf>.gotelegram.off (config.json не нужен).
      o=$'# Generated by SolusVM\nnameserver 8.8.8.8\nnameserver 1.1.1.1\n'
      printf '%s' "$o" > "$d/off"; : > "$d/off.gotelegram.off"
      _ST_LOSS="8.8.8.8=3" _run "$d/off"
      _st_file "выключатель .gotelegram.off: файл не тронут" "$d/off" "$o"
      _st_eq "выключатель: замера нет" "$(_calls)" ""
      _no_orig "$d/off" "выключатель"
      printf '%s' "$o" > "$d/offl"; ln -s /nonexistent/gt-off "$d/offl.gotelegram.off"
      _ST_LOSS="8.8.8.8=3" _run "$d/offl"
      _st_file "выключатель — висячая ссылка: файл не тронут" "$d/offl" "$o"
      printf '%s' "$o" > "$d/offl2"; ln -s "$d/off" "$d/offl2.gotelegram.off"
      _ST_LOSS="8.8.8.8=3" _run "$d/offl2"
      _st_file "выключатель — ссылка на файл: файл не тронут" "$d/offl2" "$o"
      rm -f "$d/off.gotelegram.off"; _ST_LOSS="8.8.8.8=3" _run "$d/off"
      _st_file "выключатель снят (rm) — настройка применяется" "$d/off" \
          $'# Generated by SolusVM\nnameserver 1.1.1.1\nnameserver 8.8.8.8\noptions timeout:1 attempts:2\n'"$M"$'\n'

      # Внутренний адрес хостера в файле — порядок не трогаем, сбоящий — только в журнал.
      printf 'nameserver 10.0.0.2\nnameserver 8.8.8.8\n' > "$d/priv"
      _ST_LOSS="10.0.0.2=3" _run "$d/priv"
      _st_file "внутренний 10.0.0.2 сбоит: порядок прежний, options заданы" "$d/priv" \
          $'nameserver 10.0.0.2\nnameserver 8.8.8.8\noptions timeout:1 attempts:2\n'"$M"$'\n'
      _said "внутренний адрес: предупреждение есть" dns_tune_slow_private 10.0.0.2 3 10.0.0.2
      local okp=1
      for a in 10.1.2.3 172.16.0.1 172.31.255.1 192.168.0.1 100.64.0.1 100.127.255.255 100.100.2.136 169.254.169.254 fd00::1 FC00::1 fe80::1%eth0 feb0::1 \
               ::ffff:10.0.0.1 0:0:0:0:0:ffff:10.0.0.1 ::ffff:a00:1 ::FFFF:C0A8:1; do
          _dns_ns_is_private "$a" || { okp=0; _st_bad "внутренний адрес" "$a внутренний" "нет"; }; done
      for a in 1.1.1.1 172.32.0.1 100.63.255.255 100.128.0.1 192.169.0.1 fc::1 fec0::1 2606:4700:4700::1111 ::ffff:808:808; do
          _dns_ns_is_private "$a" && { okp=0; _st_bad "внешний адрес" "$a внешний" "внутренний"; }; done
      [ "$okp" = 1 ] && _st_ok "внутренние адреса: 10/8, 172.16/12, 192.168/16, 100.64/10, 169.254/16, fc00::/7, fe80::/10 (с границами и IPv4-mapped)"
      # Внутренний первым, сбоящий публичный и так ниже — «уже стоит ниже», без «порядок не меняю».
      printf 'nameserver 10.0.0.2\nnameserver 8.8.8.8\n' > "$d/priv2"
      _ST_LOSS="8.8.8.8=3" _run "$d/priv2"
      _said  "внутренний первым, сбоящий ниже: «уже стоит ниже»" dns_tune_slow_kept 8.8.8.8 3
      _nsaid "внутренний первым, сбоящий ниже: нет «порядок не меняю»" dns_tune_slow_private 8.8.8.8 3 10.0.0.2
      # options rotate: запасной стал бы равноправным первым — не добавляем, предупреждаем.
      printf 'nameserver 10.0.0.2\noptions rotate\n' > "$d/rot"
      _ST_LOSS="10.0.0.2=3" _run "$d/rot"
      _st_file "options rotate: запасной не добавлен" "$d/rot" $'nameserver 10.0.0.2\noptions rotate timeout:1 attempts:2\n'"$M"$'\n'
      _said "options rotate: предупреждение есть" dns_tune_no_fallback_rotate 10.0.0.2 3
      _st_eq "options rotate: кандидаты в запасные не замеряются" "$(_calls)" "10.0.0.2"

      # Адрес — до пробела или таба, как у glibc: «1.1.1.1#main», «8.8.8.8;x» — не серверы.
      printf 'nameserver 8.8.8.8\nnameserver 1.1.1.1#main\n' > "$d/hash"
      _ST_LOSS="8.8.8.8=3" _run "$d/hash"
      _st_file "«1.1.1.1#main» не сервер: 8.8.8.8 один, запасной ниже, строка на месте" "$d/hash" \
          $'nameserver 8.8.8.8\n'"$FB"$'\nnameserver 1.1.1.1\nnameserver 1.1.1.1#main\noptions timeout:1 attempts:2\n'"$M"$'\n'
      grep -qF '#main' "$calls" && _st_bad "«1.1.1.1#main» не замеряется" "нет" "есть" || _st_ok "«1.1.1.1#main» не замеряется"
      printf 'nameserver 10.0.0.1\nnameserver 8.8.8.8;x\n' > "$d/semi"
      _ST_LOSS="10.0.0.1=4" _run "$d/semi"
      _st_file "«8.8.8.8;x» не сервер: у 10.0.0.1 появился запасной" "$d/semi" \
          $'nameserver 10.0.0.1\n'"$FB"$'\nnameserver 1.1.1.1\nnameserver 8.8.8.8;x\noptions timeout:1 attempts:2\n'"$M"$'\n'

      # Строки из файла на экран — без \ и управляющих символов (log_* — это echo -e).
      printf 'nameserver 1.1.1.1\noptions ndots:3 \\033[31mRED\\c\n' > "$d/esc"
      _run "$d/esc"
      grep -q 'RED' "$base/err" && ! grep -qF '\' "$base/err" && ! grep -q $'\033\\[31m' "$base/err" \
          && _st_ok "options из файла выводятся без \\ и управляющих символов" \
          || _st_bad "вывод options" "без \\ и ESC" "$(tr '\n' '|' < "$base/err")"
      _st_file "в сам файл options идут дословно" "$d/esc" \
          $'nameserver 1.1.1.1\noptions ndots:3 \\033[31mRED\\c timeout:1 attempts:2\n'"$M"$'\n'
      printf 'nameserver 1.1.1.1\noptions ndots:3 \033[31mRED\001X\n' > "$d/esc2"     # настоящие ESC и SOH
      _run "$d/esc2"
      grep -qF '[31mREDX' "$base/err" && _st_ok "настоящие управляющие символы из options на экран не идут" \
          || _st_bad "вывод options с ESC/SOH" "[31mREDX" "$(tr '\n' '|' < "$base/err" | cat -v)"
      grep -qF $'\033[31mRED\001X' "$d/esc2" && _st_ok "в файл options с управляющими символами — дословно" \
          || _st_bad "файл options с ESC/SOH" "дословно" "$(cat -v "$d/esc2" | tr '\n' '|')"
      printf 'nameserver 127.0.0.1\033[31mX\\c\n' > "$d/esc3"
      _run "$d/esc3"
      grep -qF '127.0.0.1[31mXc' "$base/err" && _st_ok "адрес в сообщении о пропуске — без \\ и управляющих символов" \
          || _st_bad "адрес в сообщении о пропуске" "127.0.0.1[31mXc" "$(tr '\n' '|' < "$base/err" | cat -v)"

      # Нет dig: порядок не трогаем, options применяем.
      printf '%s' "$o" > "$d/nodig"
      ( _dns_have_dig() { return 1; }
        _ST_LOSS="8.8.8.8=3" _run "$d/nodig"
        _st_file "нет dig: порядок прежний, options заданы" "$d/nodig" "${o}options timeout:1 attempts:2"$'\n'"$M"$'\n'
        _st_eq "нет dig: замера нет" "$(_calls)" "" )

      # Без права записи — исходник цел, rc 0 (под root chmod запись не запрещает).
      printf '%s' "$o" > "$d/ro"; chmod 444 "$d/ro"
      if [ "$(id -u)" != 0 ]; then
          _ST_LOSS="8.8.8.8=3" _run "$d/ro" && _st_ok "без права записи: rc 0" || _st_bad "без права записи: rc" 0 "≠0"
          _st_file "без права записи: исходник цел" "$d/ro" "$o"
          _no_orig "$d/ro" "без права записи"
      else _st_skip "без права записи" "под root chmod 444 запись не запрещает"; fi
      chmod 644 "$d/ro" 2>/dev/null

      # Сбой самой записи (обрыв на полуслове) — прежнее содержимое возвращается.
      printf '%s' "$o" > "$d/wfail"
      ( _dns_write_in_place() { if [ ! -e "$d/wfail.flag" ]; then : > "$d/wfail.flag"; printf 'nameser' > "$2"; return 1; fi; cat "$1" > "$2"; }
        _ST_LOSS="8.8.8.8=3" _run "$d/wfail" && _st_ok "сбой записи: rc 0" || _st_bad "сбой записи: rc" 0 "≠0"
        _st_file "сбой записи: прежнее содержимое возвращено" "$d/wfail" "$o"
        _nsaid "сбой записи: «поставлен ниже» не выводится" dns_tune_slow 8.8.8.8 3
        _said  "сбой записи: «прежнее содержимое возвращено»" dns_tune_restored "$d/wfail" )

      # Разные серверы замеряются параллельно: 3 × 1 с укладываются в ~1 с.
      printf 'nameserver 8.8.8.8\nnameserver 1.1.1.1\nnameserver 9.9.9.9\n' > "$d/par"
      ( _dns_ns_losses() { sleep 1; echo 0; }
        local s0=$SECONDS; _run "$d/par"
        [ $((SECONDS - s0)) -le 2 ] && _st_ok "замер параллельный (3 сервера × 1 с ≤ 2 с)" \
            || _st_bad "замер параллельный" "≤ 2 с" "$((SECONDS - s0)) с" )

      # stdout чист (бот ждёт JSON от --action, миграция идёт и оттуда); сбой внутри
      # (даже ошибка set -u) не роняет вызывающего — всё в подоболочке, выход 0.
      printf 'nameserver 8.8.8.8\nnameserver 1.1.1.1\n' > "$d/quiet"
      _st_eq "stdout пуст" "$(_ST_LOSS="8.8.8.8=3" DNS_RESOLV_CONF="$d/quiet" dns_resolver_tune 2>/dev/null)" ""
      _st_eq "внутренняя ошибка не роняет вызывающего (rc 0)" \
          "$( set -u; _dns_resolver_tune_run() { echo "$_st_no_such_var"; }; dns_resolver_tune 2>/dev/null; echo "rc=$?" )" "rc=0"

      # Маркер-дубли (руками) сводятся к одному.
      printf 'nameserver 1.1.1.1\nnameserver 8.8.8.8\noptions timeout:1 attempts:2\n%s\n%s\n' "$M" "$M" > "$d/dup"
      _run "$d/dup"
      _st_eq "маркер-дубли сведены к одному" "$(grep -c "^# goTelegram Pro dns_tune:" "$d/dup")" "1"
    )

    # Где вызывается: сразу после ensure_deps в установке Lite и Pro (до загрузок)
    # и в миграции (обновление). Строки — из тел функций, как их видит bash.
    local fn
    for fn in install_lite_mode install_pro_mode; do
        _st_eq "$fn: dns_resolver_tune сразу после ensure_deps" \
            "$(declare -f "$fn" 2>/dev/null | sed 's/^[[:space:]]*//; s/;$//' | grep -x -A1 ensure_deps | sed -n 2p)" "dns_resolver_tune"
    done
    declare -f _auto_migrate_legacy_state_locked 2>/dev/null | sed 's/^[[:space:]]*//; s/;$//' | grep -qx dns_resolver_tune \
        && _st_ok "миграция вызывает dns_resolver_tune" || _st_bad "миграция" "вызывает dns_resolver_tune" "нет вызова"

    # Сам замер: аргументы dig, учёт ответа, ~/.digrc не мешает.
    ( local args="$base/digargs"; : > "$args"
      dig() { printf '%s|%s\n' "$HOME" "$*" >> "$args"
              printf ';; ->>HEADER<<- opcode: QUERY, status: %s, id: 1\n' "${_ST_DIG_STATUS:-NOERROR}"
              return "${_ST_DIG_RC:-0}"; }
      _st_eq "_dns_ns_losses: все ответили → 0" "$(_dns_ns_losses 1.1.1.1)" "0"
      _st_eq "_dns_ns_losses: 8 запросов" "$(wc -l < "$args" | tr -d ' ')" "8"
      _st_eq "_dns_ns_losses: аргументы dig" "$(head -1 "$args")" "/nonexistent|+time=1 +tries=1 @1.1.1.1 . NS"
      _st_eq "SERVFAIL — потеря" "$(_ST_DIG_STATUS=SERVFAIL _dns_ns_losses 1.1.1.1)" "8"
      _st_eq "код dig ≠ 0 — потеря" "$(_ST_DIG_RC=9 _dns_ns_losses 1.1.1.1)" "8"
      : > "$args"
      _st_nrc "_dns_ns_losses -h: отказ" _dns_ns_losses -h
      _st_eq "_dns_ns_losses -h: dig не вызван" "$(wc -l < "$args" | tr -d ' ')" "0" )
    return 0
}

# Раздел 13: get_server_ip без сети. ip и curl подменены функциями (фоновые
# задания их наследуют), кэш и временные файлы — во временном каталоге.
# _ST_SRC — src маршрута (пусто — маршрута нет). Ответы сервисов — таблица
# _ST_IP_ANS «хост=ответ» или «хост=код:ответ» (без кода — HTTP 200): «hang» —
# висит дольше бюджета (pid пишется в pids), нет записи — сетевой отказ.
# Каждый вызов curl пишется в calls.
_st_ip_tests() {
    local base="$1/ip"
    mkdir -p "$base/tmpd" "$base/bin" 2>/dev/null \
        || { _st_bad "раздел 13: временный каталог" "создан" "нет ($base)"; return 0; }
    ( local calls="$base/calls" pidf="$base/pids" out="" rc=0 t0 dt now a ok c
      SERVER_IP_CACHE="$base/run/server_ip"; SERVER_IP_TTL=3600; SERVER_IP_BUDGET=3
      unset SERVER_IP_FAIL_BACKOFF
      TMPDIR="$base/tmpd"; _ST_SRC=""; _ST_IP_ANS=""
      ip() { [ -n "$_ST_SRC" ] || return 2
             printf '1.1.1.1 via 10.0.0.1 dev eth0 src %s uid 0 \n    cache \n' "$_ST_SRC"; }
      curl() { local w="" url="" host ans="" kv ph='%{http_code}' code=200
          while [ $# -gt 0 ]; do
              case "$1" in -w) w="$2"; shift ;; --max-time) shift ;; http://*|https://*) url="$1" ;; esac; shift
          done
          host="${url#*://}"; host="${host%%/*}"
          printf '%s\n' "$host" >> "$calls"
          for kv in $_ST_IP_ANS; do [ "${kv%%=*}" = "$host" ] && ans="${kv#*=}"; done
          case "$ans" in
              hang) printf '%s\n' "$BASHPID" >> "$pidf"; exec sleep 5 ;;
              "")   [ -n "$w" ] && printf '%b' "${w//"$ph"/000}"; return 7 ;;
              *)    [[ "$ans" =~ ^([0-9]{3}):(.*)$ ]] && { code="${BASH_REMATCH[1]}"; ans="${BASH_REMATCH[2]}"; }
                    printf '%s\n' "$ans"; [ -n "$w" ] && printf '%b' "${w//"$ph"/$code}"; return 0 ;;
          esac; }
      _gsi() { : > "$calls"; : > "$pidf"; t0=$SECONDS
               out=$(get_server_ip 2>/dev/null); rc=$?; dt=$((SECONDS - t0)); }
      _ncalls() { wc -l < "$calls" | tr -d ' '; }
      _put() { mkdir -p "${SERVER_IP_CACHE%/*}"; printf '%s\n' "$1" > "$SERVER_IP_CACHE"; }
      _cached() { local ts="" v=""; { read -r ts v; } 2>/dev/null < "$SERVER_IP_CACHE"
                  [[ "$ts" =~ ^[0-9]+$ ]] && printf '%s' "$v"; }
      # Хвосты после вызова: живые «сервисы» (pid) и временные файлы; «нет/» — чисто.
      _tails() { local p left=""
          while read -r p; do kill -0 "$p" 2>/dev/null && left+="$p "; done < "$pidf"
          printf '%s/%s' "${left:-нет}" "$(ls -A "$TMPDIR" | tr '\n' ' ')"; }
      _hascache() { [ -e "$SERVER_IP_CACHE" ] && echo есть || echo нет; }
      _hasfail() { [ -e "$SERVER_IP_CACHE.fail" ] && echo есть || echo нет; }
      printf -v now '%(%s)T' -1
      ANS3="api.ipify.org=198.51.100.20 icanhazip.com=198.51.100.20 ifconfig.me=198.51.100.20"

      # Что считается публичным адресом
      ok=1
      for a in 1.1.1.1 8.8.8.8 203.0.113.7 11.0.0.1 100.63.255.255 100.128.0.1 169.253.1.1 \
               172.15.255.255 172.32.0.1 191.255.255.255 192.0.1.0 192.0.2.1 192.167.1.1 \
               198.17.255.255 198.20.0.0 223.255.255.255; do
          _ip_is_public4 "$a" || { ok=0; _st_bad "публичный адрес" "$a публичный" "нет"; }; done
      for a in 0.1.2.3 10.0.0.1 100.64.0.1 100.127.255.255 127.0.0.1 169.254.1.1 172.16.0.1 \
               172.31.255.255 192.0.0.0 192.0.0.2 192.0.0.255 192.168.0.1 198.18.0.0 198.18.0.1 \
               198.19.255.255 224.0.0.1 255.255.255.255 256.1.1.1 01.2.3.4 1.2.3 \
               '1.2.3.4 ' ' 1.2.3.4' '1.2.3.4.5' '' '$(id)' '1.2.3.4;id' $'1.2.3.4\n5.6.7.8'; do
          _ip_is_public4 "$a" && { ok=0; _st_bad "не публичный адрес" "$a отвергнут" "принят"; }; done
      [ "$ok" = 1 ] && _st_ok "публичный IPv4: без 0/8, 10/8, 100.64/10, 127/8, 169.254/16, 172.16/12, 192.0.0/24, 192.168/16, 198.18/15, 224/3 и кривых записей"

      # 1. Публичный src маршрута — сразу он, без сети и без кэша
      _ST_SRC=203.0.113.10; _ST_IP_ANS="$ANS3"; _gsi
      _st_eq "публичный src: он сам, curl не вызван, кэш не пишется" "$out/$rc/$(_ncalls)/$(_hascache)" "203.0.113.10/0/0/нет"

      # 2. За NAT, свежий кэш — кэш, без сети
      _ST_SRC=10.0.0.5; _put "$((now - 60)) 198.51.100.7"; _gsi
      _st_eq "NAT + свежий кэш: адрес из кэша, curl не вызван" "$out/$rc/$(_ncalls)" "198.51.100.7/0/0"
      out=$(IFS=: _server_ip_cache_get); rc=$?
      _st_eq "кэш читается и при чужом IFS вызывающего" "$out/$rc" "198.51.100.7/0"

      # 3. За NAT, кэша нет — ответ сервиса, он же в кэше; рядом с кэшем и во
      #    временном каталоге ничего лишнего, зависших запросов нет
      rm -rf "${SERVER_IP_CACHE%/*}"; _gsi
      _st_eq "NAT без кэша: ответ сервиса записан в кэш, без хвостов" \
          "$out/$rc/$(_cached)/$(ls -A "${SERVER_IP_CACHE%/*}" 2>/dev/null | tr '\n' ' ')/$(_tails)" \
          "198.51.100.20/0/198.51.100.20/server_ip /нет/"

      # 4. Устаревший кэш, сервисы молчат — устаревший адрес, rc 0
      _put "$((now - 7200)) 198.51.100.8"; _ST_IP_ANS=""; _gsi
      _st_eq "устаревший кэш + отказ: старый адрес, rc 0, сервисы спрошены" "$out/$rc/$(_ncalls)" "198.51.100.8/0/3"
      _put "$((now + 100000)) 198.51.100.9"; _gsi
      _st_eq "кэш «из будущего» не свежий: сервисы спрошены, при отказе — он" "$out/$rc/$(_ncalls)" "198.51.100.9/0/3"
      _put "$((now - 7200)) 198.51.100.8"; _ST_IP_ANS="$ANS3"; _gsi
      c=""; { read -r c _; } 2>/dev/null < "$SERVER_IP_CACHE"
      _st_eq "устаревший кэш + ответ сервиса: новый адрес, кэш перезаписан свежим" \
          "$out/$rc/$(_cached)/$([[ "$c" =~ ^[0-9]+$ ]] && [ "$c" -ge "$now" ] && echo свежий || echo "ts=$c")" \
          "198.51.100.20/0/198.51.100.20/свежий"
      _ST_IP_ANS=""

      # 5. Ни кэша, ни ответа — прежний контракт (на нём стоят вызывающие)
      rm -rf "${SERVER_IP_CACHE%/*}"; _gsi
      _st_eq "нет кэша + отказ: 0.0.0.0, rc 1 (прежний контракт)" "$out/$rc/$(_hascache)" "0.0.0.0/1/нет"

      # 6. Приватный ответ сервиса не принимается
      _ST_IP_ANS="api.ipify.org=10.1.2.3 icanhazip.com=192.168.1.1 ifconfig.me=198.51.100.30"; _gsi
      _st_eq "приватные ответы отброшены, взят публичный" "$out/$rc" "198.51.100.30/0"
      rm -rf "${SERVER_IP_CACHE%/*}"
      _ST_IP_ANS="api.ipify.org=10.1.2.3 icanhazip.com=100.64.0.1 ifconfig.me=127.0.0.1"; _gsi
      _st_eq "только приватные ответы: 0.0.0.0, rc 1, в кэш не попали" "$out/$rc/$(_hascache)" "0.0.0.0/1/нет"

      # 6б. Ответ не HTTP 200 не принимается, даже с годным адресом в теле
      _ST_IP_ANS="api.ipify.org=503:198.51.100.50 icanhazip.com=404:198.51.100.51 ifconfig.me=302:198.51.100.52"; _gsi
      _st_eq "ответы не 200 с годным адресом: не приняты, 0.0.0.0, rc 1, в кэш не попали" "$out/$rc/$(_hascache)" "0.0.0.0/1/нет"
      _ST_IP_ANS="api.ipify.org=503:198.51.100.50 icanhazip.com=500:198.51.100.51 ifconfig.me=200:198.51.100.53"; _gsi
      _st_eq "ответы 503 и 500 отброшены, взят ответ 200" "$out/$rc" "198.51.100.53/0"

      # 7. Мусор в кэше не принимается и заменяется ответом сервиса
      _ST_IP_ANS="$ANS3"; ok=1
      for c in "123 \$(id)" "$((now - 60)) 10.0.0.9" "$((now - 60)) 198.51.100.7 x" "$((now - 60)) 198.51.100.07" "0$((now - 60)) 198.51.100.7"; do
          _put "$c"; _gsi
          [ "$out/$rc" = "198.51.100.20/0" ] && [ "$(_cached)" = 198.51.100.20 ] \
              || { ok=0; _st_bad "мусор в кэше" "198.51.100.20/0, кэш перезаписан" "$out/$rc ($c)"; }
      done
      [ "$ok" = 1 ] && _st_ok "мусор в кэше (\$(…), приватный адрес, лишнее поле, ведущие нули) отброшен и перезаписан"

      # 8. Нет команды ip — ветка NAT (PATH без ip, функция-подмена снята)
      for c in mkdir mktemp mv rm sleep cat ls wc tr grep head; do ln -sf "$(command -v "$c")" "$base/bin/$c"; done
      _put "$((now - 60)) 198.51.100.7"
      ( unset -f ip; PATH="$base/bin"; _ST_SRC=203.0.113.10; _gsi
        _st_eq "нет ip + свежий кэш: адрес из кэша, curl не вызван" "$out/$rc/$(_ncalls)" "198.51.100.7/0/0"
        rm -f "$SERVER_IP_CACHE"; _gsi
        _st_eq "нет ip, кэша нет: ответ сервиса" "$out/$rc" "198.51.100.20/0" )
      _ST_SRC=""; _put "$((now - 60)) 198.51.100.7"; _gsi
      _st_eq "нет маршрута + свежий кэш: адрес из кэша" "$out/$rc/$(_ncalls)" "198.51.100.7/0/0"

      # 8б. Пауза после отказа — только по просьбе вызывающего: шапка меню зовёт
      #     с SERVER_IP_FAIL_BACKOFF=300, остальные без неё спрашивают каждый раз.
      _ST_SRC=10.0.0.5; rm -rf "${SERVER_IP_CACHE%/*}"; _ST_IP_ANS=""
      SERVER_IP_FAIL_BACKOFF=300 _gsi; a="$out/$rc/$(_ncalls)/$(ls -A "${SERVER_IP_CACHE%/*}" 2>/dev/null | tr '\n' ' ')"
      SERVER_IP_FAIL_BACKOFF=300 _gsi
      _st_eq "шапка, отказ дважды: первый спросил и оставил отметку, второй curl не зовёт" \
          "$a | $out/$rc/$(_ncalls)" "0.0.0.0/1/3/server_ip.fail  | 0.0.0.0/1/0"
      _gsi
      _st_eq "без паузы (ссылки, установка): при свежей отметке сервисы спрошены" "$out/$rc/$(_ncalls)" "0.0.0.0/1/3"
      _put "$((now - 7200)) 198.51.100.8"; SERVER_IP_FAIL_BACKOFF=300 _gsi
      _st_eq "шапка, свежая отметка + устаревший кэш: старый адрес сразу" "$out/$rc/$(_ncalls)" "198.51.100.8/0/0"
      ok=1
      for c in "$((now - 400))" "$((now + 1000))" "$((now - 10)) x" "0$((now - 10))" "abc"; do
          printf '%s\n' "$c" > "$SERVER_IP_CACHE.fail"; SERVER_IP_FAIL_BACKOFF=300 _gsi
          [ "$(_ncalls)" = 3 ] || { ok=0; _st_bad "шапка, отметка «$c»" "сервисы спрошены (3)" "$(_ncalls)"; }
      done
      for c in 0 abc ""; do
          printf '%s\n' "$((now - 10))" > "$SERVER_IP_CACHE.fail"; SERVER_IP_FAIL_BACKOFF="$c" _gsi
          [ "$(_ncalls)" = 3 ] || { ok=0; _st_bad "пауза «$c»" "сервисы спрошены (3)" "$(_ncalls)"; }
      done
      [ "$ok" = 1 ] && _st_ok "отметка старше паузы, из будущего или кривая, пауза 0 или не число — сервисы спрошены"
      _ST_IP_ANS="$ANS3"; _gsi
      _st_eq "успех снимает отметку отказа" "$out/$rc/$(_hasfail)" "198.51.100.20/0/нет"

      # 9. Параллельно: два сервиса висят (дольше бюджета), третий отвечает сразу.
      #    Порог щедрый: ≤ 2 с по SECONDS при бюджете 3 с (по очереди было бы 10 с).
      _ST_SRC=10.0.0.5; rm -rf "${SERVER_IP_CACHE%/*}"
      _ST_IP_ANS="api.ipify.org=hang icanhazip.com=hang ifconfig.me=198.51.100.40"; _gsi
      _st_eq "два сервиса висят: ответ третьего раньше бюджета, зависшие погашены" \
          "$out/$rc/$([ "$dt" -le 2 ] && echo быстро || echo "${dt} с")/$(_tails)" "198.51.100.40/0/быстро/нет/"

      # 10. Все висят — функция укладывается в бюджет + 1 с
      rm -rf "${SERVER_IP_CACHE%/*}"; SERVER_IP_BUDGET=1
      _ST_IP_ANS="api.ipify.org=hang icanhazip.com=hang ifconfig.me=hang"; _gsi
      _st_eq "все висят: 0.0.0.0, rc 1 за бюджет + 1 с (1+1), зависшие погашены" \
          "$out/$rc/$([ "$dt" -le 3 ] && echo вовремя || echo "${dt} с")/$(_tails)" "0.0.0.0/1/вовремя/нет/"
      SERVER_IP_BUDGET=3 )

    # Шапка меню: «|| echo» внутри $(get_server_ip …) давал две строки
    local _mm; _mm=$(declare -f show_main_menu 2>/dev/null)
    if [ -n "$_mm" ]; then
        local _bad; _bad=$(grep -E 'get_server_ip[^)]*\|\|[[:space:]]*echo' <<<"$_mm" | head -1)
        [ -z "$_bad" ] && _st_ok "шапка меню: IP одной строкой (нет «get_server_ip … || echo»)" \
            || _st_bad "шапка меню: get_server_ip … || echo" "нет" "$(printf '%s' "$_bad" | tr -s ' ')"
        grep -qE 'SERVER_IP_FAIL_BACKOFF=[1-9][0-9]* get_server_ip' <<<"$_mm" \
            && _st_ok "шапка меню: пауза после отказа (SERVER_IP_FAIL_BACKOFF=… get_server_ip)" \
            || _st_bad "шапка меню: пауза после отказа" "SERVER_IP_FAIL_BACKOFF=… get_server_ip" "нет"
    else _st_skip "шапка меню" "нет show_main_menu"; fi
    return 0
}

# Раздел 14: версии TLS сайта. config.json и конфиг nginx — временные файлы.
# Генератор целиком не зовётся (он пишет в /etc/nginx); вместо этого стражи по
# его тексту и переключатель с подменёнными генератором, nginx и systemctl.
_st_tls_tests() {
    local base="$1/tls"; mkdir -p "$base" || return 0
    ( LOG_FILE="$base/gt.log"; GOTELEGRAM_CONFIG="$base/config.json"
      NGINX_SITE_CONF="$base/site"; NGINX_SITE_LINK="$base/site-enabled"
      # shellcheck disable=SC2034  # читают gt_with_config_lock и _nginx_tls_set_locked
      GT_CONFIG_LOCK="$base/lock"
      # shellcheck disable=SC2034
      WEB_FRONT_STREAM_CONF="$base/stream"
      _v() { printf '%s' "$1" > "$GOTELEGRAM_CONFIG"; nginx_tls_protocols 2>/dev/null; }
      _st_eq "tls_protocols: нет ключа / TLSv1.2 / инъекция" \
          "$(_v '{}')|$(_v '{"tls_protocols":"TLSv1.2"}')|$(_v '{"tls_protocols":"TLSv1.2; include /etc/passwd"}')" \
          "TLSv1.2 TLSv1.3|TLSv1.2|TLSv1.2 TLSv1.3"
      _st_eq "tls_protocols: пробелы по краям / перевод строки внутри / массив" \
          "$(_v '{"tls_protocols":" TLSv1.2 "}')|$(_v '{"tls_protocols":"TLSv1.2\nTLSv1.3"}')|$(_v '{"tls_protocols":["TLSv1.2"]}')" \
          "TLSv1.2|TLSv1.2 TLSv1.3|TLSv1.2 TLSv1.3"
      printf '{}' > "$GOTELEGRAM_CONFIG"; ln -sf "$NGINX_SITE_CONF" "$NGINX_SITE_LINK"
      printf '    ssl_protocols TLSv1.2;\n    ssl_protocols TLSv1.2 TLSv1.3;\n' > "$NGINX_SITE_CONF"
      local r1 r2 k1 k2
      nginx_tls_adopt_manual 2>/dev/null; r1=$?; k1=$(jq -r '.tls_protocols // "-"' "$GOTELEGRAM_CONFIG")
      printf '    ssl_protocols TLSv1.2;\n    ssl_protocols TLSv1.2; # вручную\n' > "$NGINX_SITE_CONF"
      nginx_tls_adopt_manual 2>/dev/null; r2=$?; k2=$(jq -r '.tls_protocols // "-"' "$GOTELEGRAM_CONFIG")
      _st_eq "ручной TLSv1.2 в nginx: смесь не берём, везде 1.2 — в config.json" \
          "$r1 $k1 | $r2 $k2" "1 - | 0 TLSv1.2"
      # Детектор: 1 — «везде ровно TLSv1.2», 0 — нет
      # shellcheck disable=SC2059
      _d() { printf "$1" > "$NGINX_SITE_CONF"; if _nginx_tls_live_all TLSv1.2; then printf 1; else printf 0; fi; }
      _st_eq "детектор: CRLF, табы, закомм. 1.3 | CRLF-смесь, 1.3 после другой директивы, значение на след. строке" \
          "$(_d '    ssl_protocols TLSv1.2;\r\n    ssl_protocols TLSv1.2;\r\n')$(_d '\tssl_protocols\tTLSv1.2;\n\t\tssl_protocols  \t TLSv1.2\t;\t\n')$(_d '    #ssl_protocols TLSv1.2 TLSv1.3;\n    ssl_protocols TLSv1.2;\n')|$(_d '    ssl_protocols TLSv1.2;\r\n    ssl_protocols TLSv1.2 TLSv1.3;\r\n')$(_d '    ssl_protocols TLSv1.2;\n    ssl_session_tickets off; ssl_protocols TLSv1.2 TLSv1.3;\n')$(_d '    ssl_protocols\n        TLSv1.2 TLSv1.3;\n    ssl_protocols TLSv1.2;\n')" \
          "111|000"
      # Текущее значение зовут на каждой отрисовке меню — без предупреждений и журнала
      printf '{"tls_protocols":"TLSv1.3"}' > "$GOTELEGRAM_CONFIG"; : > "$LOG_FILE"
      _st_eq "текущее при мусоре в ключе: умолчание, ни stderr, ни журнала" \
          "$(nginx_tls_current 2>&1)|$(wc -c < "$LOG_FILE" | tr -d ' ')" "TLSv1.2 TLSv1.3|0"
      # Переключатель: меню и CLI → литерал ключа; остальное — отказ
      _st_eq "меню 1/2, CLI 1.2+1.3/1.2 → ключ" \
          "$(nginx_tls_menu_value 1)|$(nginx_tls_menu_value 2)|$(nginx_tls_cli_value 1.2+1.3)|$(nginx_tls_cli_value 1.2)" \
          "TLSv1.2 TLSv1.3|TLSv1.2|TLSv1.2 TLSv1.3|TLSv1.2"
      local j okj=1
      for j in '' 0 1 3 1.3 TLSv1.2 'TLSv1.2 TLSv1.3' ' 1.2' '1.2 ' '1.2;' '1.2+1.3;x' '1.2|g' $'1.2\n1.3'; do
          nginx_tls_cli_value "$j" >/dev/null && { okj=0; _st_bad "CLI: отказ" "«$j» — отказ" "принято"; }
      done
      for j in '' 0 3 10 '1 ' 1.2; do
          nginx_tls_menu_value "$j" >/dev/null && { okj=0; _st_bad "меню: отказ" "«$j» — отказ" "принято"; }
      done
      [ "$okj" = 1 ] && _st_ok "CLI и меню: мусор, литералы ключа, пробелы, «;», «|» — отказ"
      # Переключатель целиком: генератор, nginx и systemctl подменены
      _NGT=0
      # shellcheck disable=SC2329  # подмены зовёт nginx_tls_set
      generate_nginx_config() { printf '    ssl_certificate x;\n    ssl_protocols %s;\n    ssl_protocols %s;\n' \
          "$(nginx_tls_protocols)" "$(nginx_tls_protocols)" > "$NGINX_SITE_CONF"; }
      # shellcheck disable=SC2329
      nginx() { return "$_NGT"; }
      # systemctl: журнал вызовов в файле (переживает $(...)), коды reload и is-active
      _SRL=0; _SIA=0; : > "$base/sys"
      # shellcheck disable=SC2329
      systemctl() { echo "$1" >> "$base/sys"; case "$1" in reload) return "$_SRL" ;; is-active) return "$_SIA" ;; esac; return 0; }
      printf '{"mode":"pro","domain":"d.example","users":[1]}' > "$GOTELEGRAM_CONFIG"
      printf '    ssl_certificate x;\n    ssl_protocols TLSv1.2 TLSv1.3;\n' > "$NGINX_SITE_CONF"
      local s1 s2 s3 s4 k
      nginx_tls_set "TLSv1.2" 2>/dev/null; s1=$?
      k=$(jq -r '.tls_protocols' "$GOTELEGRAM_CONFIG")$(grep -c 'ssl_protocols TLSv1.2;' "$NGINX_SITE_CONF")
      nginx_tls_set "TLSv1.2" 2>/dev/null; s2=$?
      # откат возвращает config.json побайтно, вместе с updated_at
      jq '.updated_at = "t0"' "$GOTELEGRAM_CONFIG" > "$GOTELEGRAM_CONFIG.t" && mv "$GOTELEGRAM_CONFIG.t" "$GOTELEGRAM_CONFIG"
      local c0; c0=$(cat "$GOTELEGRAM_CONFIG")
      _NGT=1; nginx_tls_set "TLSv1.2 TLSv1.3" 2>/dev/null; s3=$?; _NGT=0
      [ "$(cat "$GOTELEGRAM_CONFIG")" = "$c0" ] || s3="$s3/config.json:$(jq -c '[.tls_protocols,.updated_at]' "$GOTELEGRAM_CONFIG")"
      k="$k|$(jq -r '.tls_protocols' "$GOTELEGRAM_CONFIG")$(grep -c 'ssl_protocols TLSv1.2;' "$NGINX_SITE_CONF")"
      nginx_tls_set "TLSv1.2; x" 2>/dev/null; s4=$?
      _st_eq "переключатель: 1.2 → 0, повтор → 9, nginx -t упал → 6 и откат побайтно, мусор → 4" \
          "$s1 $s2 $s3 $s4 $k $(jq -c '.users' "$GOTELEGRAM_CONFIG")" "0 9 6 4 TLSv1.22|TLSv1.22 [1]"
      # --action=tls-protocols: только без --value= — чтение без замка и миграции
      _st_eq "tls-protocols: без --value читает, с --value= и другие действия — нет" \
          "$(_gt_tls_action_reads --action=tls-protocols --json; echo $?)$(_gt_tls_action_reads --action=tls-protocols --value=1.2 --json; echo $?)$(_gt_tls_action_reads --action=web-off; echo $?)" "011"
      local c1 o o1 o2 o3
      c1=$(cat "$GOTELEGRAM_CONFIG" "$NGINX_SITE_CONF"); : > "$base/sys"
      o=$(bot_action_dispatch --action=tls-protocols --value=1.3 --json 2>/dev/null)
      _st_eq "--value=1.3 --json: одна строка error/bad_value, файлы не тронуты, systemctl не звали" \
          "$(printf '%s\n' "$o" | wc -l | tr -d ' ')|$(printf '%s' "$o" | jq -r '.status + "/" + .code' 2>/dev/null)|$([ "$(cat "$GOTELEGRAM_CONFIG" "$NGINX_SITE_CONF")" = "$c1" ] && echo same)|$(wc -c < "$base/sys" | tr -d ' ')" \
          "1|error/bad_value|same|0"
      o1=$(bot_action_dispatch --action=tls-protocols --value=1.2+1.3 --json 2>/dev/null)
      o2=$(bot_action_dispatch --action=tls-protocols --value=1.2 --json 2>/dev/null)
      o3=$(bot_action_dispatch --action=tls-protocols --value=1.2 --json 2>/dev/null)
      _st_eq "--value=1.2+1.3, 1.2, снова 1.2 (--json): changed true, true, false" \
          "$(printf '%s\n%s\n%s\n' "$o1" "$o2" "$o3" | jq -r '[.status, .value, .changed] | join(" ")' 2>/dev/null | tr '\n' '|')$(grep -c 'ssl_protocols TLSv1.2;' "$NGINX_SITE_CONF")" \
          "ok 1.2+1.3 true|ok 1.2 true|ok 1.2 false|2"
      # reload не прошёл: nginx жив — откат без restart; стоял — start
      c1=$(cat "$GOTELEGRAM_CONFIG" "$NGINX_SITE_CONF"); : > "$base/sys"; _SRL=1
      o=$(bot_action_dispatch --action=tls-protocols --value=1.2+1.3 --json 2>/dev/null)
      _st_eq "reload упал, nginx жив: nginx_reload, откат побайтно, restart и start не звали" \
          "$(printf '%s' "$o" | jq -r '.status + "/" + .code' 2>/dev/null)|$([ "$(cat "$GOTELEGRAM_CONFIG" "$NGINX_SITE_CONF")" = "$c1" ] && echo same)|$(grep -cE '^(restart|start)$' "$base/sys")" \
          "error/nginx_reload|same|0"
      : > "$base/sys"; _SIA=3
      o=$(bot_action_dispatch --action=tls-protocols --value=1.2+1.3 --json 2>/dev/null); _SRL=0; _SIA=0
      _st_eq "reload упал, nginx стоял: start, применено" \
          "$(printf '%s' "$o" | jq -r '.status + "/" + .changed' 2>/dev/null)|$(tr '\n' ',' < "$base/sys")" "ok/true|reload,is-active,start,"
      # откат не удался — отдельный код, строка в журнал, снимок оставлен
      : > "$LOG_FILE"
      # shellcheck disable=SC2329  # подмену зовёт nginx_tls_set
      o=$(_nginx_tls_cfg_restore() { return 1; }; _NGT=1 TMPDIR="$base"
          bot_action_dispatch --action=tls-protocols --value=1.2 --json 2>/dev/null)
      _st_eq "сбой и откат не удался: rollback_failed, журнал, снимок оставлен" \
          "$(printf '%s' "$o" | jq -r '.status + "/" + .code' 2>/dev/null)|$(grep -c 'ROLLBACK FAILED' "$LOG_FILE")|$(find "$base" -maxdepth 1 -name 'gt-tls.*' | wc -l | tr -d ' ')" \
          "error/rollback_failed|1|1"
      # ручной 1.2 без ключа и явный выбор «только 1.2»: ключ дописан, nginx не трогали
      jq 'del(.tls_protocols)' "$GOTELEGRAM_CONFIG" > "$GOTELEGRAM_CONFIG.t" && mv "$GOTELEGRAM_CONFIG.t" "$GOTELEGRAM_CONFIG"
      printf '    ssl_certificate x;\n    ssl_protocols TLSv1.2;\n' > "$NGINX_SITE_CONF"; : > "$base/sys"
      nginx_tls_set "TLSv1.2" 2>/dev/null; s1=$?
      _st_eq "ручной 1.2 без ключа, выбор 1.2: 9, ключ TLSv1.2, nginx не трогали" \
          "$s1 $(jq -r '.tls_protocols' "$GOTELEGRAM_CONFIG") $(grep -c ssl_protocols "$NGINX_SITE_CONF") $(wc -c < "$base/sys" | tr -d ' ')" "9 TLSv1.2 1 0"
      printf '{"mode":"lite"}' > "$GOTELEGRAM_CONFIG"
      nginx_tls_set "TLSv1.2" 2>/dev/null; s1=$?
      _st_eq "переключатель в Lite: 2 (не используется), config.json не тронут" \
          "$s1 $(cat "$GOTELEGRAM_CONFIG")" '2 {"mode":"lite"}' )
    local _gf _g _la _lc
    _gf=$(declare -f generate_nginx_config 2>/dev/null)
    _g=$(printf '%s\n' "$_gf" | grep -E 'ssl_protocols[[:space:]]' | grep -v 'TLS_PROTOCOLS_PLACEHOLDER' | head -2)
    [ -z "$_g" ] && _st_ok "generate_nginx_config: ssl_protocols только из настройки" \
        || _st_bad "generate_nginx_config: ssl_protocols" "TLS_PROTOCOLS_PLACEHOLDER" "$(printf '%s' "$_g" | tr -s ' ' | tr '\n' ' ')"
    # shellcheck disable=SC2016
    printf '%s\n' "$_gf" | grep -qF 's|TLS_PROTOCOLS_PLACEHOLDER|${tls_protocols}|g' \
        && _st_ok "generate_nginx_config: TLS_PROTOCOLS_PLACEHOLDER подставляется" \
        || _st_bad "generate_nginx_config: подстановка" "s|TLS_PROTOCOLS_PLACEHOLDER|\${tls_protocols}|g" "нет"
    # Ручную фиксацию надо прочесть до перезаписи файла, иначе её нечем прочесть
    _la=$(printf '%s\n' "$_gf" | grep -n 'nginx_tls_adopt_manual' | head -1 | cut -d: -f1)
    # shellcheck disable=SC2016
    _lc=$(printf '%s\n' "$_gf" | grep -nF 'cat > "$NGINX_SITE_CONF"' | head -1 | cut -d: -f1)
    [ -n "$_la" ] && [ -n "$_lc" ] && [ "$_la" -lt "$_lc" ] \
        && _st_ok "generate_nginx_config: nginx_tls_adopt_manual до перезаписи конфига" \
        || _st_bad "generate_nginx_config: adopt до cat >" "строка adopt < строки cat" "${_la:-нет} / ${_lc:-нет}"
}

# Раздел 15: каталог ACME и имя WEB. Всё во временном каталоге (ACME_WEBROOT,
# файлы nginx, журнал, TMPDIR); nginx, systemctl, certbot, timeout и curl
# подменены. curl-подмена играет nginx: отдаёт файл из acme-challenge с кодом
# _ST_HTTP (000 — нет ответа, wrong — чужое тело). certbot-подмена: исход
# n-го вызова — _ST_CB<n> (ok — выпуск, perm — unauthorized 403, acct — нет
# аккаунта); вызовы пишутся в cb.
_st_acme_tests() {
    local base="$1/acme"; mkdir -p "$base/tmp" || return 0
    ( LOG_FILE="$base/gt.log"; ACME_WEBROOT="$base/www/certbot"; TMPDIR="$base/tmp"
      # shellcheck disable=SC2034  # читает web_cert_expand (пауза перед повтором пробы)
      ACME_PROBE_RETRY_DELAY=0
      local w="$ACME_WEBROOT" r
      _m() { local x; read -r x _ <<<"$(_acme_stat "$1")"; printf '%s' "$x"; }
      _ms() { printf '%s %s %s %s' "$(_m "$base/www")" "$(_m "$w")" "$(_m "$w/.well-known")" "$(_m "$w/.well-known/acme-challenge")"; }
      # Подготовка webroot
      ( umask 077; mkdir -p "$w/.well-known/acme-challenge" ); chmod 750 "$base/www"
      acme_webroot_prepare; r=$?
      _st_eq "webroot 700 (umask 077), родитель 750 → 755, родителю только o+x" "$r $(_ms)" "0 751 755 755 755"
      rm -rf "$base/www"; ( umask 077; acme_webroot_prepare ); r=$?
      _st_eq "webroot нет, umask 077 → создан 755 вместе с родителем" "$r $(_ms)" "0 755 755 755 755"
      chmod 777 "$w"; chmod 700 "$w/.well-known" "$w/.well-known/acme-challenge"
      acme_webroot_prepare; r=$?
      _st_eq "webroot 777 (запись всем) → 755, подкаталоги после этого тоже" "$r $(_ms)" "0 755 755 755 755"
      rm -rf "$w/.well-known"; mkdir -m 700 "$base/other"; ln -s "$base/other" "$w/.well-known"; : > "$LOG_FILE"
      acme_webroot_prepare; r=$?
      _st_eq "симлинк .well-known: rc 1, по нему не пошли, цель 700, запись в журнал" \
          "$r $([ -L "$w/.well-known" ] && echo link) $(_m "$base/other") $(grep -c 'left as is' "$LOG_FILE")" "1 link 700 1"
      rm -f "$w/.well-known"; mkdir -p "$w/.well-known/acme-challenge"; chmod 700 "$w/.well-known" "$w/.well-known/acme-challenge"
      # чужой владелец только у certbot-каталога (родитель — наш): без root
      # подменяем stat, а не id — иначе недоверенным стал бы уже родитель
      eval "$(declare -f _acme_stat | sed '1s/^_acme_stat/_st_orig_stat/')"
      # shellcheck disable=SC2329  # подмену зовёт acme_webroot_prepare
      ( _acme_stat() { local o; o=$(_st_orig_stat "$@") || return 1
                       [ "$1" = "$w" ] && o="${o%% *} 65534"; printf '%s\n' "$o"; }
        acme_webroot_prepare ); r=$?
      _st_eq "чужой владелец каталога certbot (родитель наш): подкаталоги не трогаем" \
          "$r $(_m "$w") $(_m "$w/.well-known") $(_m "$w/.well-known/acme-challenge")" "1 755 700 700"
      # Миграция (install.sh): не тронули, но nginx проходит — без предупреждения
      _mig() { acme_webroot_prepare || acme_webroot_web_ok || echo warn; }
      # shellcheck disable=SC2329
      id() { if [ "${1:-}" = -u ] && [ "${2:-}" = www-data ]; then echo 33; else command id "$@"; fi; }
      chmod 755 "$w/.well-known" "$w/.well-known/acme-challenge"
      # shellcheck disable=SC2329
      r=$( _acme_stat() { local o; o=$(_st_orig_stat "$@") || return 1
                          [ "$1" = "$w" ] && o="${o%% *} 33"; printf '%s\n' "$o"; }; _mig )
      _st_eq "миграция: владелец certbot www-data, 755 — без предупреждения" "$r" ""
      chmod 700 "$w"
      # родитель чужой; в подмене не $base — его затеняет local acme_webroot_prepare
      # shellcheck disable=SC2329
      r=$( _acme_stat() { local o; o=$(_st_orig_stat "$@") || return 1
                          [ "$1" = "${w%/*}" ] && o="${o%% *} 33"; printf '%s\n' "$o"; }; _mig )
      _st_eq "миграция: certbot root 700 (родитель чужой, не чиним) — предупреждение" "$r $(_m "$w")" "warn 700"
      chmod 755 "$w"; mv "$w" "$base/real"; ln -s "$base/real" "$w"
      _st_eq "миграция: certbot — симлинк — предупреждение" "$(_mig)" "warn"
      rm -f "$w"; mv "$base/real" "$w"; unset -f id
      rm -rf "$base/www" "$base/other"; acme_webroot_prepare
      _st_eq "проходимость для nginx: 700 — нет, 755 — да" \
          "$(chmod 700 "$w"; acme_web_enters "$w" && echo y || echo n)$(chmod 755 "$w"; acme_web_enters "$w" && echo y || echo n)" "ny"
      # Самопроверка до certbot
      # shellcheck disable=SC2329  # подмену зовёт acme_local_probe
      curl() {
          local a u=""; for a in "$@"; do case "$a" in http://*) u="$a" ;; esac; done
          printf '%s\n' "$@" > "$base/curl_args"; echo >> "$base/curl_n"
          local code="${_ST_HTTP:-200}"
          # очередь кодов: первый вызов — первая строка и т. д.
          if [ -s "$base/http_seq" ]; then
              code=$(head -n 1 "$base/http_seq"); tail -n +2 "$base/http_seq" > "$base/http_seq.n"; mv "$base/http_seq.n" "$base/http_seq"
          fi
          case "$code" in
              000)   printf '\n000'; return 7 ;;
              200)   cat "$ACME_WEBROOT/.well-known/acme-challenge/${u##*/}" 2>/dev/null; printf '\n200' ;;
              wrong) printf 'index'; printf '\n200' ;;
              *)     printf '<html>\n</html>'; printf '\n%s' "$code" ;;
          esac
      }
      local h c pr=""
      for h in 200 403 404 wrong 301 000; do
          _ST_HTTP=$h acme_local_probe www.d.example; pr+="$h:$? "
      done
      _st_eq "самопроверка: 200 своё → 0, 403 → 3, 404/чужое/301 → 4, нет ответа → 5" \
          "$pr" "200:0 403:3 404:4 wrong:4 301:4 000:5 "
      _st_eq "самопроверка: файлов в acme-challenge не осталось" \
          "$(find "$w/.well-known/acme-challenge" -type f | wc -l | tr -d ' ')" "0"
      c=$(tr '\n' ' ' < "$base/curl_args")
      case " $c" in *" --resolve www.d.example:80:127.0.0.1 "*" --noproxy * "*|*" --noproxy * "*" --resolve www.d.example:80:127.0.0.1 "*) _st_ok "самопроверка: 127.0.0.1:80 по --resolve, мимо прокси" ;;
                    *) _st_bad "самопроверка: аргументы curl" "--resolve www.d.example:80:127.0.0.1 и --noproxy *" "$c" ;; esac
      _st_eq "самопроверка: curl -q первым (без ~/.curlrc)" "$(head -n 1 "$base/curl_args")" "-q"
      case "$(tail -n 1 "$base/curl_args")" in http://www.d.example/.well-known/acme-challenge/gtprobe-*) _st_ok "самопроверка: URL — файл в acme-challenge" ;;
          *) _st_bad "самопроверка: URL" "http://www.d.example/.well-known/acme-challenge/gtprobe-…" "$(tail -n 1 "$base/curl_args")" ;; esac
      rm -f "$base/curl_args"; acme_local_probe 'a b;x'; r=$?
      _st_eq "самопроверка: не имя → 2, curl не звали" "$r $([ -e "$base/curl_args" ] && echo called)" "2 "
      mv "$w/.well-known" "$base/wk"; ln -s "$base/wk" "$w/.well-known"; acme_local_probe www.d.example; r=$?
      rm -f "$w/.well-known"; mv "$base/wk" "$w/.well-known"
      _st_eq "самопроверка: симлинк в цепочке → 2 (файл не кладём)" "$r" "2"
      # Причина отказа certbot
      local f="$base/out"
      _k() { printf '%b' "$1" > "$f"; certbot_fail_kind "$f"; }
      _st_eq "certbot: unauthorized/connection/firewall/dns/rateLimited/исходящая сеть/прочее" \
          "$(_k '  Domain: www.d.example\n  Type:   unauthorized\n  Detail: 192.0.2.1: Invalid response from http://www.d.example/.well-known/acme-challenge/x: 403\n')|$(_k '  Type:   connection\n  Detail: 192.0.2.1: Fetching http://www.d.example/.well-known/acme-challenge/x: Connection refused\n')|$(_k 'Detail: Fetching\n   http://www.d.example/.well-known/acme-challenge/x:\n   Timeout during connect (likely firewall problem)\n')|$(_k '  Type:   dns\n  Detail: DNS problem: NXDOMAIN looking up A for www.d.example\n')|$(_k 'Error creating new order :: too many certificates (5) already issued for this exact set of identifiers in the last 168h0m0s, retry after 2026-11-02 10:00:00 UTC: see https://letsencrypt.org/docs/rate-limits/\n')|$(_k "Failed to establish a new connection: [Errno 101] Network is unreachable\n")|$(_k 'You should register before running non-interactive\n')" \
          "perm|port80|port80|dns|ratelimit|other|other"
      _st_eq "certbot: тексты 403/порт 80/DNS — свои фразы" \
          "$(_k '  Type:   unauthorized\n' >/dev/null; certbot_fail_text "$f" 1 www.d.example)|$(_k '  Type:   connection\n' >/dev/null; certbot_fail_text "$f" 1 www.d.example)|$(_k 'DNS problem: NXDOMAIN\n' >/dev/null; certbot_fail_text "$f" 1 www.d.example)" \
          "$(tf web_cert_perm www.d.example "$ACME_WEBROOT")|$(tf web_cert_port80 www.d.example)|$(tf web_cert_dns www.d.example)"
      _st_eq "certbot: лимит — когда повторить (retry after / час / неделя)" \
          "$(_k 'rateLimited: too many certificates, retry after 2026-11-02T10:00:00Z: see\n' >/dev/null; certbot_fail_text "$f" 1 h)|$(_k 'too many failed authorizations (5) for "h" in the last 1h0m0s\n' >/dev/null; certbot_fail_text "$f" 1 h)|$(_k 'urn:ietf:params:acme:error:rateLimited\n' >/dev/null; certbot_fail_text "$f" 1 h)" \
          "$(tf web_cert_ratelimit "$(tf web_cert_wait_until 2026-11-02T10:00:00Z)")|$(tf web_cert_ratelimit "$(t web_cert_wait_hour)")|$(tf web_cert_ratelimit "$(t web_cert_wait_week)")"
      local long; long=$(printf 'x%.0s' $(seq 1 300))
      _st_eq "certbot: прочее — общая фраза + одна строка; таймаут 100 с" \
          "$(_k 'Saving debug log to /var/log/letsencrypt/letsencrypt.log\nAn unexpected error occurred:\nThe server experienced an internal error :: boom\nAsk for help or search for solutions at https://community.letsencrypt.org. See the logfile /var/log/letsencrypt/letsencrypt.log\n' >/dev/null; certbot_fail_text "$f" 1 h)|$(certbot_fail_text "$f" 124 h)" \
          "$(tf web_host_cert h) certbot: The server experienced an internal error :: boom|$(tf web_host_cert h) certbot: timeout 100 s"
      _st_eq "certbot: шум (Ask for help, logfile) не попадает в строку; после «An unexpected error occurred:» — сама ошибка" \
          "$(_k 'Saving debug log to /var/log/letsencrypt/letsencrypt.log\nAnother instance of Certbot is already running.\nAsk for help or search for solutions at https://community.letsencrypt.org. See the logfile /var/log/letsencrypt/letsencrypt.log or re-run Certbot with -v for more details.\n' >/dev/null; certbot_fail_text "$f" 1 h)|$(_k 'An unexpected error occurred:\nThe client lacks sufficient authorization :: Account is not valid\nAsk for help or search for solutions at https://community.letsencrypt.org.\n' >/dev/null; certbot_fail_text "$f" 1 h)" \
          "$(tf web_host_cert h) certbot: Another instance of Certbot is already running.|$(tf web_host_cert h) certbot: The client lacks sufficient authorization :: Account is not valid"
      printf 'Error: bad\033[31m \\033[0m\ttab %s\n' "$long" > "$f"; c=$(certbot_short_err "$f")
      # длина в байтах (200 + «…» = 203): ${#c} при локали C тоже считает байты
      _st_eq "certbot: строка без управляющих символов и «\\», ≤ 200 символов" \
          "$(printf '%s' "$c" | LC_ALL=C grep -c "$(printf '[\001-\037\\\\]')")|$([ "$(printf '%s' "$c" | wc -c | tr -d ' ')" -le 203 ] && echo short)" "0|short"
      # web_cert_expand целиком
      NGINX_SITE_CONF="$base/site"; NGINX_SITE_LINK="$base/site-enabled"
      # shellcheck disable=SC2034  # читает web_cert_expand (снимок)
      WEB_FRONT_STREAM_CONF="$base/stream"
      # shellcheck disable=SC2329  # подмены зовёт web_cert_expand
      generate_nginx_config() { printf 'server_name %s %s;\n' "$1" "${WEB_HOST_PENDING:-}" > "$NGINX_SITE_CONF"; }
      # shellcheck disable=SC2329
      nginx() { return "${_ST_NGT:-0}"; }
      # shellcheck disable=SC2329
      systemctl() { echo "$*" >> "$base/sys"; [ "$1" = reload ] && return "${_ST_SRL:-0}"; return 0; }
      # shellcheck disable=SC2329
      timeout() { shift; "$@"; }
      # shellcheck disable=SC2329
      _web_cert_covers() { [ -f "$base/covered" ]; }
      # shellcheck disable=SC2329
      certbot() {
          local n; n=$(( $(wc -l < "$base/cb" 2>/dev/null || echo 0) + 1 )); echo "$*" >> "$base/cb"
          case "$(eval "printf '%s' \"\${_ST_CB$n:-acct}\"")" in
              ok)   : > "$base/covered"; echo "Successfully received certificate."; return 0 ;;
              perm) printf '  Type:   unauthorized\n  Detail: Invalid response from http://x/: 403\n'; return 1 ;;
              hang) : > "$base/cb_started"; sleep 1; return 1 ;;
              *)    echo "You should register before running non-interactive"; return 1 ;;
          esac
      }
      _reset() { printf 'ORIG\n' > "$NGINX_SITE_CONF"; ln -sf "$NGINX_SITE_CONF" "$NGINX_SITE_LINK"
                 rm -f "$base/covered" "$base/cb" "$base/stream" "$base/cb_started" "$base/curl_n" "$base/http_seq"
                 : > "$base/sys"; : > "$LOG_FILE"; }
      _cnt() { [ -f "$1" ] && wc -l < "$1" | tr -d ' ' || echo 0; }
      _left() { find "$TMPDIR" -maxdepth 1 \( -name 'gt-certbot.*' -o -name 'gt-webcert.*' \) | wc -l | tr -d ' '; }
      local e
      _reset; _ST_NGT=1 web_cert_expand d.example www.d.example; r=$?
      e=$WEB_CERT_ERR
      _st_eq "expand: nginx -t упал → своя фраза, certbot нет, reload нет, конфиг как был" \
          "$r|$e|$(_cnt "$base/cb")|$(grep -c reload "$base/sys")|$(cat "$NGINX_SITE_CONF")|$(_left)" \
          "1|$(tf web_cert_nginx www.d.example)|0|0|ORIG|0"
      _reset; _ST_SRL=1 web_cert_expand d.example www.d.example; r=$?
      _st_eq "expand: reload упал → своя фраза, certbot нет, конфиг как был" \
          "$r|$WEB_CERT_ERR|$(_cnt "$base/cb")|$(cat "$NGINX_SITE_CONF")" "1|$(tf web_cert_reload www.d.example)|0|ORIG"
      _reset; _ST_HTTP=403 web_cert_expand d.example www.d.example; r=$?
      _st_eq "expand: самопроверка 403 → своя фраза, certbot нет, nginx возвращён (2 reload)" \
          "$r|$WEB_CERT_ERR|$(_cnt "$base/cb")|$(grep -c '^reload nginx$' "$base/sys")|$(cat "$NGINX_SITE_CONF")" \
          "1|$(tf web_cert_probe_403 www.d.example "$ACME_WEBROOT")|0|2|ORIG"
      _reset; _ST_HTTP=000 web_cert_expand d.example www.d.example; r=$?
      _st_eq "expand: нет ответа на 80 → своя фраза (проба дважды)" "$r|$WEB_CERT_ERR|$(_cnt "$base/curl_n")" "1|$(t web_cert_probe_none)|2"
      _reset; printf '404\n200\n' > "$base/http_seq"; _ST_CB1=ok web_cert_expand d.example www.d.example; r=$?
      _st_eq "expand: проба 404 сразу после reload, 200 со второй → certbot, выпуск" \
          "$r|$(_cnt "$base/curl_n")|$(_cnt "$base/cb")|$WEB_CERT_ERR" "0|2|1|"
      _reset; TMPDIR=/nonexistent web_cert_expand d.example www.d.example; r=$?
      _st_eq "expand: снимок не сохранить (TMPDIR нет) → своя фраза, certbot нет" \
          "$r|$WEB_CERT_ERR|$(_cnt "$base/cb")|$(cat "$NGINX_SITE_CONF")" "1|$(tf web_cert_snap /nonexistent)|0|ORIG"
      _reset; _ST_CB1=perm web_cert_expand d.example www.d.example; r=$?
      _st_eq "expand: certbot unauthorized → фраза о правах, второго захода нет, конфиг как был, временных нет" \
          "$r|$WEB_CERT_ERR|$(_cnt "$base/cb")|$(cat "$NGINX_SITE_CONF")|$(_left)" \
          "1|$(tf web_cert_perm www.d.example "$ACME_WEBROOT")|1|ORIG|0"
      _reset; _ST_CB1=acct _ST_CB2=ok web_cert_expand d.example www.d.example; r=$?
      _st_eq "expand: нет аккаунта → второй заход без почты, выпуск, имя осталось на :80" \
          "$r|$(_cnt "$base/cb")|$(tail -n 1 "$base/cb" | grep -c -- '--register-unsafely-without-email')|$(cat "$NGINX_SITE_CONF")|$(_left)" \
          "0|2|1|server_name d.example www.d.example;|0"
      _reset; : > "$base/covered"; web_cert_expand d.example www.d.example; r=$?
      _st_eq "expand: имя уже в сертификате → 0, ничего не трогаем" "$r|$(_cnt "$base/cb")|$(wc -c < "$base/sys" | tr -d ' ')|$(cat "$NGINX_SITE_CONF")" "0|0|0|ORIG"
      # Прервали посреди certbot: временных нет, файлы nginx вернулись, чужая
      # ловушка вызывающего сработала после уборки, а после обычного вызова — на месте
      local pid
      _reset; ( trap 'echo outer > "$base/outer"; exit 7' TERM; _ST_CB1=hang web_cert_expand d.example www.d.example ) & pid=$!
      for _ in $(seq 1 50); do [ -f "$base/cb_started" ] && break; sleep 0.1; done
      kill -TERM "$pid" 2>/dev/null; wait "$pid"; r=$?
      _st_eq "expand: SIGTERM посреди certbot → временных нет, конфиг как был, ловушка вызывающего сработала" \
          "$r|$(_left)|$(cat "$NGINX_SITE_CONF")|$(cat "$base/outer" 2>/dev/null)" "7|0|ORIG|outer"
      _reset; ( _ST_CB1=hang web_cert_expand d.example www.d.example ) & pid=$!
      for _ in $(seq 1 50); do [ -f "$base/cb_started" ] && break; sleep 0.1; done
      kill -TERM "$pid" 2>/dev/null; wait "$pid"; r=$?
      _st_eq "expand: SIGTERM без ловушек → завершение по сигналу (143), временных нет" "$r|$(_left)" "143|0"
      # Вызывающий перехватил TERM без выхода: ловушка вернула файлы и убрала снимок,
      # шаг идёт дальше, и обычный откат не должен удалить файлы без копии
      _reset; ( trap 'x=1' TERM; _ST_CB1=hang web_cert_expand d.example www.d.example
                printf '%s' "$WEB_CERT_ERR" > "$base/err" ) & pid=$!
      for _ in $(seq 1 50); do [ -f "$base/cb_started" ] && break; sleep 0.1; done
      kill -TERM "$pid" 2>/dev/null; wait "$pid"; r=$?
      _st_eq "expand: вызывающий глушит TERM (trap 'x=1') → файлы nginx на месте и не пустые, причина есть, временных нет" \
          "$r|$(cat "$NGINX_SITE_CONF" 2>/dev/null || echo NONE)|$([ -L "$NGINX_SITE_LINK" ] && echo link)|$([ -s "$base/err" ] && echo err)|$(_left)" \
          "0|ORIG|link|err|0"
      # restore без снимка: живые файлы не трогает, rc 1
      _reset; _nginx_tls_snap restore "$base/nosnap" "$NGINX_SITE_CONF" "$NGINX_SITE_LINK"; r=$?
      _st_eq "снимок nginx: каталога нет → rc 1, файлы не тронуты" \
          "$r|$(cat "$NGINX_SITE_CONF" 2>/dev/null || echo NONE)|$([ -L "$NGINX_SITE_LINK" ] && echo link)" "1|ORIG|link"
      # TERM сразу после удаления снимка: ловушка не должна «восстанавливать» из
      # пустоты (restore сначала удаляет файл) — файлы nginx на месте
      local x out2=""
      for x in ok perm; do
          _reset
          # shellcheck disable=SC2329  # подмену зовёт web_cert_expand
          ( rm() { command rm "$@"; case "$*" in *gt-webcert.*) kill -TERM "$BASHPID" ;; esac; }
            eval "_ST_CB1=$x web_cert_expand d.example www.d.example" ) & pid=$!
          wait "$pid"; out2+="$?:$(cat "$NGINX_SITE_CONF" 2>/dev/null || echo NONE)|"
      done
      _st_eq "expand: TERM сразу после удаления снимка → файлы nginx на месте (выпуск и откат)" \
          "$out2" "143:server_name d.example www.d.example;|143:ORIG|"
      _reset; r=$( trap 'echo outer' TERM; _ST_CB1=perm web_cert_expand d.example www.d.example; trap -p TERM INT HUP )
      _st_eq "expand: прежние ловушки вызывающего после вызова — как были" "$r" "trap -- 'echo outer' SIGTERM"
      eval "$(declare -f _nginx_tls_snap | sed '1s/^_nginx_tls_snap/_st_orig_snap/')"
      # shellcheck disable=SC2329
      _nginx_tls_snap() { [ "$1" = restore ] && return 1; _st_orig_snap "$@"; }
      _reset; _ST_CB1=perm web_cert_expand d.example www.d.example; r=$?
      _st_eq "expand: откат не удался → об этом в той же строке, снимок оставлен, журнал" \
          "$r|$(case "$WEB_CERT_ERR" in "$(tf web_cert_perm www.d.example "$ACME_WEBROOT") "*gt-webcert.*) echo ok ;; *) echo "$WEB_CERT_ERR" ;; esac)|$(find "$TMPDIR" -maxdepth 1 -name 'gt-webcert.*' | wc -l | tr -d ' ')|$(grep -c 'ROLLBACK FAILED' "$LOG_FILE")" \
          "1|ok|1|1"
      # Причина доходит до бота и панели: --action=web-host берёт последнюю строку
      GOTELEGRAM_CONFIG="$base/config.json"
      # shellcheck disable=SC2034  # читает bot_action_dispatch (замок)
      GT_CONFIG_LOCK="$base/lock"
      printf '{"mode":"pro","domain":"d.example"}' > "$GOTELEGRAM_CONFIG"
      # shellcheck disable=SC2329
      web_set_host() { log_warning "x"; log_error "$(tf web_cert_port80 "$1")"; return 1; }
      # локаль C, как у служб бота и панели без LANG: значок «✗» — многобайтовый
      local o; o=$(LC_ALL=C bot_action_dispatch --action=web-host --host=www.d.example --json 2>/dev/null)
      _st_eq "web-host --json: причина отказа — в message, code=web_host" \
          "$(printf '%s' "$o" | jq -r '.status + "|" + .code + "|" + .message' 2>/dev/null)" "error|web_host|$(tf web_cert_port80 www.d.example)" )
    # Стражи по тексту: web_set_host берёт причину из web_cert_expand и сам nginx
    # не перегенерирует (откат — снимком внутри web_cert_expand)
    local _ws; _ws=$(declare -f web_set_host 2>/dev/null)
    if printf '%s\n' "$_ws" | grep -q 'WEB_CERT_ERR' && ! printf '%s\n' "$_ws" | grep -q 'generate_nginx_config'; then
        _st_ok "web_set_host: причина из WEB_CERT_ERR, без своей перегенерации nginx"
    else _st_bad "web_set_host" "WEB_CERT_ERR, без generate_nginx_config" "иначе"; fi
    # миграция предупреждает, только если nginx не проходит каталоги (как _mig выше)
    if declare -f _auto_migrate_legacy_state_locked 2>/dev/null | grep -q 'acme_webroot_prepare || acme_webroot_web_ok || log_warning'; then
        _st_ok "миграция: acme_webroot_left — только если nginx не проходит каталоги"
    else _st_bad "миграция: acme_webroot_prepare || acme_webroot_web_ok || log_warning" "есть" "нет"; fi
    # Живой сервер: каталоги ACME проходимы для nginx (www-data). Ничего не меняет.
    local _aw="${ACME_WEBROOT:-/var/www/certbot}" _ad _bad=""
    if [ -d "$_aw" ]; then
        for _ad in "$(dirname -- "$_aw")" "$_aw" "$_aw/.well-known" "$_aw/.well-known/acme-challenge"; do
            [ -e "$_ad" ] || continue
            acme_web_enters "$_ad" || _bad+="$_ad ($(_acme_stat "$_ad" -L | cut -d' ' -f1)) "
        done
        [ -z "$_bad" ] && _st_ok "каталоги ACME доступны nginx (www-data)" \
            || _st_warn "каталоги ACME недоступны nginx (www-data): ${_bad}— Let's Encrypt получит 403, продление сертификата не пройдёт; исправит обновление (миграция) или chmod 755"
    else _st_skip "каталоги ACME на сервере" "нет $_aw"; fi
}

selftest_run() {
    local tmp; tmp=$(mktemp -d /tmp/gt-selftest.XXXXXX) || return 1
    ST_TALLY="$tmp/tally"; : > "$ST_TALLY"
    trap 'rm -rf "$tmp" 2>/dev/null' RETURN
    printf '\n\033[1;37m  goTelegram Pro — автопроверка (--selftest)\033[0m\n'
    printf '\033[2m  Ничего не устанавливается и не меняется; всё в %s\033[0m\n' "$tmp"

    # ── 1. Синтаксис всех скриптов ─────────────────────────────────────────
    _st_head "1. Синтаксис"
    local f bad=0
    for f in "${SCRIPT_DIR:-.}"/install.sh "${SCRIPT_DIR:-.}"/bootstrap.sh "${LIB_DIR:-./lib}"/*.sh "${LIB_DIR:-./lib}"/lang/*.sh; do
        [ -f "$f" ] || continue
        bash -n "$f" 2>/dev/null || { _st_bad "bash -n $(basename "$f")" "ok" "syntax error"; bad=1; }
    done
    [ "$bad" = 0 ] && _st_ok "bash -n: все скрипты чисты"

    # ── 2. i18n: симметрия ru/en и отсутствие сирот ─────────────────────────
    _st_head "2. i18n"
    if [ -f "${LIB_DIR:-./lib}/lang/ru.sh" ] && [ -f "${LIB_DIR:-./lib}/lang/en.sh" ]; then
        local ru en diff
        ru=$(grep -oE '^I18N\[[a-z0-9_]+\]' "${LIB_DIR:-./lib}/lang/ru.sh" | sort -u)
        en=$(grep -oE '^I18N\[[a-z0-9_]+\]' "${LIB_DIR:-./lib}/lang/en.sh" | sort -u)
        diff=$(comm -3 <(printf '%s\n' "$ru") <(printf '%s\n' "$en") | tr -d '\t' | head -5)
        [ -z "$diff" ] && _st_ok "ключи ru/en симметричны ($(printf '%s\n' "$ru"|wc -l) шт.)" \
            || _st_bad "ключи ru/en симметричны" "нет расхождений" "$(printf '%s' "$diff"|tr '\n' ' ')"
    else _st_skip "i18n" "нет lang-файлов"; fi

    # ── 3. Версии ядра telemt ──────────────────────────────────────────────
    _st_head "3. Выбор версии ядра (последняя = рекомендованная)"
    if type telemt_target_version >/dev/null 2>&1; then
        ( TELEMT_PINNED_VERSION="3.4.18"
          get_latest_telemt_version() { echo "9.9.9"; }
          read_config_or_default() { echo ""; }
          _st_eq "пустой pref → последняя"      "$(TELEMT_VERSION_PREF=''            telemt_target_version)" "9.9.9"
          _st_eq "recommended → последняя"       "$(TELEMT_VERSION_PREF=recommended   telemt_target_version)" "9.9.9"
          _st_eq "pinned → стабильный пин"       "$(TELEMT_VERSION_PREF=pinned        telemt_target_version)" "3.4.18"
          _st_eq "конкретный тег → он же"        "$(TELEMT_VERSION_PREF=3.4.15        telemt_target_version)" "3.4.15"
          get_latest_telemt_version() { echo ""; }
          _st_eq "API недоступен → фолбэк на пин" "$(TELEMT_VERSION_PREF=latest       telemt_target_version)" "3.4.18" )
    else _st_skip "версии ядра" "telemt.sh не загружен"; fi

    # ── 4. Метрики: окна потерь и трафика ──────────────────────────────────
    _st_head "4. Метрики качества"
    if type metrics_loss_window >/dev/null 2>&1; then
        ( local now; now=$(date +%s)
          MET_LOSS_LOG="$tmp/loss.log"; MET_CF_LOG="$tmp/cf.log"
          printf '%s 10 9\n' "$((now-20))"  > "$MET_LOSS_LOG"    # 10% потерь
          printf '%s 10 5\n' "$((now-200))" >> "$MET_LOSS_LOG"   # +50% в окне 5м
          _st_eq "потери 1м = 10%"  "$(metrics_loss_window 60)"  "10"
          _st_eq "потери 5м = 30%"  "$(metrics_loss_window 300)" "30"
          : > "$MET_LOSS_LOG"
          _st_eq "нет данных → '-'" "$(metrics_loss_window 60)"  "-"
          printf '%s 1048576\n' "$((now-20))" > "$MET_CF_LOG"
          _st_eq "CF-трафик 1м = 1.0МБ" "$(metrics_cf_window 60)" "1.0МБ"
          _st_eq "байты: 0"    "$(_met_bytes_human 0)"          "0Б"
          _st_eq "байты: 3ГБ"  "$(_met_bytes_human 3221225472)" "3.0ГБ" )
        if type metrics_quality_json >/dev/null 2>&1; then
            local qj; qj=$( MET_LOSS_LOG="$tmp/x"; MET_CF_LOG="$tmp/y"; MET_CF_IFACE=nope
                            config_get() { echo off; }
                            metrics_quality_json 2>/dev/null )
            # Логи пустые, поэтому потери обязаны быть «-», а не 0: ноль означал бы,
            # что панель и бот рисуют «потерь нет» там, где данных вообще не было.
            _st_json "quality_json: валидный JSON, без данных loss='-'" "$qj" \
                '.status=="ok" and .loss_1m=="-" and (.traffic|type=="number") and (.loss_max|type=="number") and (.cf_1m|type=="string")'
        fi
    else _st_skip "метрики" "metrics.sh не загружен"; fi

    # ── 5. SYN-фикс: генерация правил (dry-run, без применения) ────────────
    _st_head "5. SYN-фикс (генерация правил)"
    if type _mf_write_rules >/dev/null 2>&1; then
        ( MEKOFIX_DIR="$tmp/mf"; MEKOFIX_RULES="$tmp/mf/rules.sh"; mkdir -p "$tmp/mf"
          local v b
          for v in v3 v2; do for b in nft iptables; do
              _mf_write_rules "$v" "$b" "443,8443"
              if bash -n "$MEKOFIX_RULES" 2>/dev/null; then _st_ok "правила $v/$b — валидный bash"
              else _st_bad "правила $v/$b" "валидный bash" "syntax error"; fi
          done; done
          _mf_write_rules v3 nft 443
          grep -q 'ssh_guard' "$MEKOFIX_RULES" && _st_ok "SSH-guard присутствует" || _st_bad "SSH-guard" "есть" "нет"
          grep -q 'reject with tcp reset' "$MEKOFIX_RULES" && _st_ok "REJECT+RST (не DROP)" || _st_bad "REJECT+RST" "есть" "нет"
          grep -q '54/minute' "$MEKOFIX_RULES" && _st_ok "лимит 54/мин (=1 SYN/1.1с)" || _st_bad "лимит" "54/minute" "нет"
          _mf_write_rules v3 iptables 443
          grep -q 'u32' "$MEKOFIX_RULES" && _st_ok "V3/iptables использует u32-отпечаток" || _st_bad "u32" "есть" "нет" )
        # Функция всегда завершается echo, то есть rc=0 при любом ответе; значим
        # сам номер — мусор вместо порта попадёт в правило «SSH разрешить первым».
        local sp; sp=$(mekofix_ssh_port 2>/dev/null)
        if [[ "$sp" =~ ^[0-9]+$ ]] && [ "$sp" -ge 1 ] && [ "$sp" -le 65535 ]; then _st_ok "ssh_port — номер порта ($sp)"
        else _st_bad "ssh_port" "число 1..65535" "$sp"; fi
        local sj; sj=$(mekofix_status_json 2>/dev/null)
        _st_json "status_json валиден" "$sj" '(.active|type=="boolean") and (.version|type=="string") and (.unit|type=="string")'
    else _st_skip "SYN-фикс" "mekofix.sh не загружен"; fi

    # ── 6. V4 (zapret2): dry-run генерация ────────────────────────────────
    _st_head "6. V4 zapret2 (экспериментальный)"
    if type v4_status_json >/dev/null 2>&1; then
        local vj; vj=$(V4_ROOT="$tmp/v4" v4_status_json 2>/dev/null)
        _st_json "v4_status_json валиден и помечен экспериментальным" "$vj" \
            '.experimental==true and (.installed|type=="boolean") and (.port|type=="number")'
        # V4_ROOT на временную копию: без него проверка шла в живую систему и на
        # сервере, где владелец штатно выбрал режим V4, давала ложный провал.
        ( V4_ROOT="$tmp/v4"; _st_nrc "v4 не активен на пустом V4_ROOT" v4_is_active )
    else _st_skip "V4" "mekofix_v4.sh не загружен"; fi

    # ── 7. Зависимости ────────────────────────────────────────────────────
    _st_head "7. Проверка зависимостей"
    if type deps_report_json >/dev/null 2>&1; then
        local dj; dj=$(DEPS_QUIET=1 deps_report_json core 2>/dev/null)
        # Сверяем связность полей, а не наличие имени "ok": ok=false с пустым
        # missing — это «чего-то не хватает, а чего, не скажем» в боте и панели.
        _st_json "deps_report_json валиден" "$dj" \
            '(.ok|type=="boolean") and (.missing|type=="array") and (.ok or ((.missing|length)>0))'
        # Функция сама всегда return 0, поэтому проверяем её ответ: по нему решается
        # вопрос про PQ-обмен (OpenSSL ≥ 3.5), и пустая строка тихо даст «нет».
        local ov; ov=$(deps_openssl_version 2>/dev/null)
        if command -v openssl >/dev/null 2>&1; then
            [[ "$ov" =~ ^[0-9]+(\.[0-9]+)*$ ]] && _st_ok "deps_openssl_version → версия ($ov)" \
                || _st_bad "deps_openssl_version" "напр. 3.0.13" "$ov"
        else _st_eq "без openssl → пустая версия" "$ov" ""; fi
    else _st_skip "зависимости" "deps.sh не загружен"; fi

    # ── 8. Миграция конфига: ключи сохраняются ────────────────────────────
    _st_head "8. Миграция конфига (сохранность ключей)"
    if type write_normalized_gotelegram_config >/dev/null 2>&1 && command -v jq >/dev/null 2>&1; then
        ( GOTELEGRAM_CONFIG="$tmp/config.json"
          cat > "$GOTELEGRAM_CONFIG" <<'JEOF'
{"version":"2.7.24","mode":"lite","port":443,"secret":"AABBCCDDEEFF00112233445566778899","mask_host":"google.com","language":"ru","installed_at":"2026-01-01T00:00:00+00:00","telemt_version":"3.4.18","warp_egress":"on","warp_key":"1111-2222-3333-4444","mekofix":"v3","preset_id":"4","tls_protocols":"TLSv1.2"}
JEOF
          get_language() { echo ru; }
          write_normalized_gotelegram_config lite 443 "AABBCCDDEEFF00112233445566778899" google.com "" "" "" 2>/dev/null
          _st_eq "secret сохранён"        "$(jq -r .secret "$GOTELEGRAM_CONFIG")"        "AABBCCDDEEFF00112233445566778899"
          _st_eq "telemt_version сохранён" "$(jq -r .telemt_version "$GOTELEGRAM_CONFIG")" "3.4.18"
          _st_eq "warp_egress сохранён"   "$(jq -r .warp_egress "$GOTELEGRAM_CONFIG")"   "on"
          _st_eq "warp_key сохранён"      "$(jq -r .warp_key "$GOTELEGRAM_CONFIG")"      "1111-2222-3333-4444"
          _st_eq "mekofix сохранён"       "$(jq -r .mekofix "$GOTELEGRAM_CONFIG")"       "v3"
          _st_eq "preset_id сохранён"     "$(jq -r .preset_id "$GOTELEGRAM_CONFIG")"     "4"
          _st_eq "tls_protocols сохранён" "$(jq -r .tls_protocols "$GOTELEGRAM_CONFIG")" "TLSv1.2"
          _st_eq "версия обновлена"       "$(jq -r .version "$GOTELEGRAM_CONFIG")"       "${GOTELEGRAM_VERSION}" )
    else _st_skip "миграция конфига" "нет write_normalized/jq"; fi

    # ── 9. Пресеты анти-DPI ───────────────────────────────────────────────
    _st_head "9. Пресеты анти-DPI"
    if type preset_params >/dev/null 2>&1; then
        local p4; p4=$(preset_params 4 2>/dev/null)
        printf '%s' "$p4" | grep -q 'bbr' && _st_ok "пресет 4 (MEKO style) = bbr" || _st_bad "пресет 4" "bbr" "$p4"
        _st_eq "дефолтный пресет = 4" "${DIAG_DEFAULT_PRESET:-}" "4"
        if type diagnose_preset_cc >/dev/null 2>&1; then
            ( config_get() { echo ""; }
              _st_eq "чистая установка (PREF=4) → bbr" "$(TELEMT_PRESET_PREF=4 diagnose_preset_cc)" "bbr"
              _st_eq "апгрейд (PREF пуст) → cubic"     "$(TELEMT_PRESET_PREF='' diagnose_preset_cc)" "cubic" )
        fi
    else _st_skip "пресеты" "diagnose.sh не загружен"; fi

    # ── 10. WARP-egress против middle-proxy ───────────────────────────────
    # Эта комбинация кладёт прокси целиком: Cloudflare не пропускает порт 8888,
    # telemt не поднимает ни одного ME-writer'а, все клиенты получают
    # «Telegram handshake timeout». Снаружи выглядит как «ключи перестали работать».
    _st_head "10. WARP-egress и middle-proxy"
    local _mp=0 _we=0 _rt=0
    grep -qE '^[[:space:]]*use_middle_proxy[[:space:]]*=[[:space:]]*true' "${TELEMT_CONFIG:-/etc/telemt/config.toml}" 2>/dev/null && _mp=1
    [ "$(config_get warp_egress 2>/dev/null)" = "on" ] && _we=1
    ip rule list 2>/dev/null | grep -q 'lookup 7788' && _rt=1
    if [ "$_mp" = "1" ] && { [ "$_we" = "1" ] || [ "$_rt" = "1" ]; }; then
        # Связка сама по себе не поломка: у части серверов порт ME через Cloudflare проходит.
        # Валим проверку только если он реально не проходит — тогда прокси сейчас лежит.
        if ! type _warp_reach_via_warp >/dev/null 2>&1; then
            _st_skip "порт middle-proxy через WARP" "warp_egress.sh не загружен"
        elif ! ip addr show "${WARP_IFACE:-gtwarp}" >/dev/null 2>&1; then
            _st_skip "порт middle-proxy через WARP" "интерфейс не поднят"
        elif _warp_reach_via_warp; then
            _st_ok "порт middle-proxy (${WARP_ME_PORT:-8888}) проходит через WARP"
        else
            _st_bad "порт middle-proxy через WARP" "проходит" "не проходит — telemt без связи с Telegram"
        fi
    else
        _st_ok "WARP-egress не конфликтует с middle-proxy"
    fi

    # ── 11. Самопроверка сайта без DNS ─────────────────────────────────────
    # 29.10.2026: резолвер хостера терял запросы, меню показывало «✗ Сайт» при
    # живом сайте. Проверка идёт на 127.0.0.1:443 с SNI домена (--resolve).
    _st_head "11. Самопроверка сайта без DNS"
    if type site_local_ok >/dev/null 2>&1; then
        ( unset SITE_PROBE_TIMEOUT
          _st_args="$tmp/site_curl_args"
          curl() { printf '%s\n' "$@" > "$_st_args"; return "${_ST_CURL_RC:-0}"; }
          _st_rc "site_local_ok: curl ответил → сайт жив" site_local_ok example.com
          local _a; _a=$(tr '\n' ' ' < "$_st_args" 2>/dev/null)
          case " $_a" in *" --resolve example.com:443:127.0.0.1 "*) _st_ok "curl идёт на 127.0.0.1:443 (--resolve)" ;;
                         *) _st_bad "curl идёт на 127.0.0.1:443" "--resolve example.com:443:127.0.0.1" "$_a" ;; esac
          case " $_a" in *" --max-time 5 "*) _st_ok "таймаут по умолчанию 5 с" ;;
                         *) _st_bad "таймаут по умолчанию" "--max-time 5" "$_a" ;; esac
          case " $_a" in *" --noproxy * "*) _st_ok "мимо https_proxy (--noproxy '*')" ;;
                         *) _st_bad "мимо https_proxy" "--noproxy *" "$_a" ;; esac
          _st_eq "последний аргумент — URL сайта" "$(tail -n 1 "$_st_args" 2>/dev/null)" "https://example.com/"
          _ST_CURL_RC=28 _st_nrc "отказ curl → сайт не отвечает" site_local_ok example.com
          rm -f "$_st_args"
          _st_nrc "пустой домен → отказ" site_local_ok ""
          [ -e "$_st_args" ] && _st_bad "пустой домен: curl не вызывается" "без вызова" "вызван" \
                             || _st_ok "пустой домен: curl не вызывается" )
    else _st_skip "site_local_ok" "нет функции — website.sh не загружен"; fi
    # Страж: самопроверка сайта через DNS не должна вернуться.
    # Не в счёт: обход DNS (--resolve, --connect-to), комментарии, подсказки в echo/printf/log_*.
    local _skip_re='(--resolve|--connect-to|(^|[[:space:]])#.*curl|(echo|printf|log_[a-z_]+)[^;|&]*curl)'
    # (а) шапка меню: проверка сайта — только через site_local_ok, без своего curl
    #     (ловит и откат через переменную: url="https://$domain"; curl "$url").
    local _mm; _mm=$(declare -f show_main_menu 2>/dev/null)
    if [ -n "$_mm" ]; then
        grep -q 'site_local_ok' <<<"$_mm" && _st_ok "шапка меню проверяет сайт через site_local_ok" \
            || _st_bad "шапка меню: проверка сайта" "site_local_ok" "нет вызова"
        local _mc; _mc=$(grep -E '(^|[^[:alnum:]_])curl[[:space:]]' <<<"$_mm" \
            | grep -vE -e "$_skip_re" -e '127\.0\.0\.1|localhost' | head -2)
        [ -z "$_mc" ] && _st_ok "шапка меню не зовёт curl наружу" \
            || _st_bad "шапка меню: curl наружу" "нет" "$(printf '%s' "$_mc" | tr -s ' ' | cut -c1-160)"
    else _st_skip "шапка меню" "нет show_main_menu"; fi
    # (б) install.sh и lib/*.sh: curl на https://$домен без --resolve; строки с «\» в конце склеиваем.
    local _f _hit _dnsprobe=""
    for _f in "${SCRIPT_DIR:-.}"/install.sh "${LIB_DIR:-./lib}"/*.sh; do
        [ -f "$_f" ] || continue
        case "$_f" in */selftest.sh) continue ;; esac
        _hit=$(awk -v F="$(basename "$_f")" '{ if (sub(/\\$/, "")) { b = b $0; next } print F ": " b $0; b = "" }' "$_f" \
            | grep -E 'curl[^#]*https://"?\$[({]?[A-Za-z_ ]*([Dd][Oo][Mm]|[Hh][Oo][Ss][Tt])' \
            | grep -vE -e "$_skip_re" | head -3)
        [ -n "$_hit" ] && _dnsprobe+="${_hit}"$'\n'
    done
    [ -z "$_dnsprobe" ] && _st_ok "нет curl на https://\$домен без --resolve" \
        || _st_bad "curl на https://\$домен без --resolve" "нет таких" "$(printf '%s' "$_dnsprobe" | tr -s ' ' | cut -c1-160)"

    # ── 12. DNS сервера: dns_resolver_tune ─────────────────────────────────
    # 29.10.2026: в файле хостера 8.8.8.8 стоял первым и терял ~2 запроса из 5,
    # glibc ждал 5 с. Все проверки — на временных resolv.conf (DNS_RESOLV_CONF),
    # замер подменён (_dns_ns_losses); системный /etc/resolv.conf не читается.
    _st_head "12. DNS сервера (resolv.conf)"
    if type dns_resolver_tune >/dev/null 2>&1; then
        _st_dns_tests "$tmp"
    else _st_skip "dns_resolver_tune" "нет функции — install.sh не загружен"; fi

    # ── 13. IP сервера: get_server_ip ──────────────────────────────────────
    # 29.10.2026: шапка меню (раз в 30 с) ждала IP до 15 с: три сервиса по
    # очереди по имени, без кэша, при сбойном DNS. Сеть подменена.
    _st_head "13. IP сервера (get_server_ip)"
    if type get_server_ip >/dev/null 2>&1; then
        _st_ip_tests "$tmp"
    else _st_skip "get_server_ip" "нет функции — common.sh не загружен"; fi

    # ── 14. Версии TLS сайта: tls_protocols ────────────────────────────────
    # 29.10.2026: обновление перегенерировало nginx и вернуло TLSv1.3 поверх
    # ручного «ssl_protocols TLSv1.2;» — провайдер владельца снова резал 443.
    _st_head "14. Версии TLS сайта (nginx ssl_protocols)"
    if type nginx_tls_protocols >/dev/null 2>&1 && command -v jq >/dev/null 2>&1; then
        _st_tls_tests "$tmp"
    else _st_skip "tls_protocols" "нет website.sh или jq"; fi

    # ── 15. Каталог ACME и имя WEB: webroot, самопроверка, причины certbot ──
    # 30.10.2026: mkdir -p /var/www/certbot при umask 077 давал 700, nginx
    # отвечал 403 — «Отдельное имя WEB» и продление падали одной общей фразой.
    _st_head "15. Каталог ACME и сертификат имени WEB"
    if type acme_webroot_prepare >/dev/null 2>&1 && type web_cert_expand >/dev/null 2>&1 && command -v jq >/dev/null 2>&1; then
        _st_acme_tests "$tmp"
    else _st_skip "ACME" "нет website.sh, install.sh или jq"; fi

    # ── итог ──────────────────────────────────────────────────────────────
    local p f k w; p=$(_st_count PASS); f=$(_st_count FAIL); k=$(_st_count SKIP); w=$(_st_count WARN)
    printf '\n\033[1m  Итог: \033[32m%s пройдено\033[0m' "$p"
    [ "$f" -gt 0 ] && printf ', \033[31m%s провалено\033[0m' "$f"
    [ "$w" -gt 0 ] && printf ', \033[33m%s предупреждений\033[0m' "$w"
    [ "$k" -gt 0 ] && printf ', \033[2m%s пропущено\033[0m' "$k"
    printf '\n\n'
    [ "$f" -eq 0 ]
}
