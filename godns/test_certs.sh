#!/bin/bash
# goDNS — Copyright (c) 2026 potap1978. All rights reserved.
#
# Тест обнаружения сертификатов на фикстурах.
#
# Работает без root, без сети и без Ubuntu: строит своё дерево
# /etc/letsencrypt, поднимает мини-CA и выпускает настоящие сертификаты.
# lib/certs.sh намеренно не использует ассоциативные массивы, поэтому тест
# идёт и на bash 3.2 (штатный в macOS), и на bash 5.
#
# Почему это важно проверять именно так: первый экран мастера — выбор
# сертификата. Если обнаружение молча ничего не находит или помечает годный
# сертификат негодным, оператор упирается в тупик на первом же шаге.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PASS=0; FAIL=0
G='\033[0;32m'; R='\033[0;31m'; D='\033[2m'; B='\033[1m'; N='\033[0m'

ok()   { PASS=$((PASS+1)); printf "  ${G}✓${N} %s\n" "$1"; }
bad()  { FAIL=$((FAIL+1)); printf "  ${R}✗${N} %s\n" "$1"; [ $# -gt 1 ] && printf "      ${D}%s${N}\n" "$2"; }
is()   { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1" "ждал [$3], получил [$2]"; fi; }
head_() { printf "\n${B}── %s${N}\n" "$1"; }

command -v openssl >/dev/null 2>&1 || { echo "нужен openssl"; exit 1; }

FIX=$(mktemp -d "${TMPDIR:-/tmp}/godns-certtest.XXXXXX") || exit 1
trap 'rm -rf "$FIX"' EXIT

LE="$FIX/etc/letsencrypt"
CA="$FIX/ca"
mkdir -p "$LE/live" "$CA" "$FIX/opt/govless" "$FIX/opt/gotelegram" "$FIX/root/cert/ip" "$FIX/etc"

# ── мини-CA: без него любой выпущенный серт самоподписан, и путь
#    «годен=yes» в тесте недостижим — то есть тест ничего не доказывает.
openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
    -keyout "$CA/ca.key" -out "$CA/ca.crt" -subj "/CN=goDNS Fixture CA" >/dev/null 2>&1

issue() {  # issue <домен> <дней> [san]
    local d="$1" days="$2" san="${3:-DNS:$1}" dir="$LE/live/$1"
    mkdir -p "$dir"
    openssl req -newkey rsa:2048 -nodes -keyout "$dir/privkey.pem" \
        -out "$CA/$d.csr" -subj "/CN=$d" >/dev/null 2>&1
    openssl x509 -req -in "$CA/$d.csr" -CA "$CA/ca.crt" -CAkey "$CA/ca.key" \
        -CAcreateserial -days "$days" -out "$CA/$d.leaf" \
        -extfile <(printf 'subjectAltName=%s\nbasicConstraints=CA:FALSE\n' "$san") >/dev/null 2>&1
    cat "$CA/$d.leaf" "$CA/ca.crt" > "$dir/fullchain.pem"
}

selfsign() {  # selfsign <домен> <дней> [san]
    local d="$1" days="$2" san="${3:-DNS:$1}" dir="$LE/live/$1"
    mkdir -p "$dir"
    openssl req -x509 -newkey rsa:2048 -nodes -days "$days" \
        -keyout "$dir/privkey.pem" -out "$dir/fullchain.pem" \
        -subj "/CN=$d" -addext "subjectAltName=$san" >/dev/null 2>&1
}

issue    vpn.example.com   60
issue    tg.example.com    45
issue    wild.example.com  50 "DNS:*.wild.example.com"
selfsign self.example.com  30
issue    soon.example.com   1

# CA-подписанный сертификат с ЧУЖИМ ключом: самый коварный случай —
# выглядит годным, но dnsdist на нём не поднимется.
mkdir -p "$LE/live/mismatch.example.com"
cp "$LE/live/vpn.example.com/fullchain.pem" "$LE/live/mismatch.example.com/fullchain.pem"
cp "$LE/live/tg.example.com/privkey.pem"    "$LE/live/mismatch.example.com/privkey.pem"

# IP-сертификат goVLESS: SAN = IP, для клиента по имени бесполезен.
openssl req -x509 -newkey rsa:2048 -nodes -days 6 \
    -keyout "$FIX/root/cert/ip/203.0.113.9.key" -out "$FIX/root/cert/ip/203.0.113.9.crt" \
    -subj "/CN=goVLESS-panel" -addext "subjectAltName=IP:203.0.113.9" >/dev/null 2>&1

printf '{"domain":"vpn.example.com","mode":"pro","language":"ru"}\n' > "$FIX/opt/govless/config.json"
printf '{"domain":"","mode":"lite","mask_host":"google.com"}\n'      > "$FIX/opt/gotelegram/config.json"
printf '[censorship]\ntls_domain = "tg.example.com"\n[server]\nport = 443\n' > "$FIX/etc/telemt.toml"

export LE_ROOT="$LE"
export GOVLESS_CONFIG_PATH="$FIX/opt/govless/config.json"
export GOTELEGRAM_CONFIG_PATH="$FIX/opt/gotelegram/config.json"
export TELEMT_CONFIG_PATH="$FIX/etc/telemt.toml"
export GOVLESS_IP_CERT_DIR="$FIX/root/cert/ip"
export XUI_DB_PATH="$FIX/absent.db"

# shellcheck source=/dev/null
. "$SCRIPT_DIR/lib/common.sh"
# lib/i18n.sh здесь НЕ подключаем: его bash-4 guard делает exit 1, а внутри
# `source` это убивает всю оболочку, а не только загрузку. Ставим заглушки.
t()  { printf '%s' "$1"; }
tf() { local k="$1"; shift; printf '%s(%s)' "$k" "$*"; }
# shellcheck source=/dev/null
. "$SCRIPT_DIR/lib/certs.sh"

printf "\n${B}goDNS — обнаружение сертификатов${N} ${D}(bash %s)${N}\n" "${BASH_VERSION%%(*}"

head_ "certs_days_left"
d=$(certs_days_left "$LE/live/vpn.example.com/fullchain.pem")
if [ "$d" -ge 58 ] 2>/dev/null && [ "$d" -le 60 ]; then ok "60-дневный → $d"; else bad "60-дневный" "получил [$d]"; fi
d=$(certs_days_left "$LE/live/soon.example.com/fullchain.pem")
if [ "$d" -le 1 ] 2>/dev/null; then ok "истекающий → $d"; else bad "истекающий" "получил [$d]"; fi
is "нечитаемый файл → ?" "$(certs_days_left "$FIX/nope.pem")" "?"

head_ "certs_san_list"
is "обычный SAN"   "$(certs_san_list "$LE/live/vpn.example.com/fullchain.pem")" "DNS:vpn.example.com"
is "wildcard SAN"  "$(certs_san_list "$LE/live/wild.example.com/fullchain.pem")" "DNS:*.wild.example.com"
is "IP SAN"        "$(certs_san_list "$FIX/root/cert/ip/203.0.113.9.crt")" "IP:203.0.113.9"

head_ "certs_covers_name"
C="$LE/live/vpn.example.com/fullchain.pem"
if certs_covers_name "$C" "vpn.example.com";   then ok "своё имя"; else bad "своё имя"; fi
if certs_covers_name "$C" "VPN.Example.COM";   then ok "регистр не важен"; else bad "регистр не важен"; fi
if certs_covers_name "$C" "other.example.com"; then bad "чужое имя приняло"; else ok "чужое имя отклонено"; fi
W="$LE/live/wild.example.com/fullchain.pem"
if certs_covers_name "$W" "dns.wild.example.com";   then ok "wildcard: одна метка"; else bad "wildcard: одна метка"; fi
if certs_covers_name "$W" "wild.example.com";       then bad "wildcard принял голый домен"; else ok "wildcard: голый домен отклонён"; fi
if certs_covers_name "$W" "a.b.wild.example.com";   then bad "wildcard принял две метки"; else ok "wildcard: две метки отклонены"; fi

head_ "certs_validate"
if certs_validate "$C" "$LE/live/vpn.example.com/privkey.pem"; then ok "пара cert+key принята"; else bad "пара cert+key принята"; fi
if certs_validate "$LE/live/mismatch.example.com/fullchain.pem" "$LE/live/mismatch.example.com/privkey.pem"; then
    bad "ЧУЖОЙ ключ принят"; else ok "чужой ключ отклонён"; fi
if certs_validate "$FIX/nope.pem" "$FIX/nope.key"; then bad "несуществующий принят"; else ok "несуществующий отклонён"; fi

head_ "certs_discover"
certs_discover >/dev/null 2>&1
n="${#CERT_CANDIDATES[@]}"
if [ "$n" -ge 6 ]; then ok "найдено записей: $n"; else bad "найдено записей" "получил $n, ждал ≥6"; fi

find_rec() {  # find_rec <подстрока источника> <номер поля>
    local want="$1" fld="$2" rec src
    for rec in "${CERT_CANDIDATES[@]}"; do
        src=$(printf '%s' "$rec" | cut -f4)
        case "$src" in *"$want"*) printf '%s' "$(printf '%s' "$rec" | cut -f"$fld")"; return 0 ;; esac
    done
    printf ''
}

is "goVLESS Pro: домен"        "$(find_rec certs_src_govless 1)" "vpn.example.com"
is "goVLESS Pro: годен"        "$(find_rec certs_src_govless 7)" "yes"
is "чужой ключ: не годен"      "$(find_rec mismatch 7)" "no"
is "чужой ключ: причина"       "$(find_rec mismatch 8)" "certs_why_key_mismatch"
is "самоподписанный: не годен" "$(find_rec self.example.com 7)" "no"
is "самоподписанный: причина"  "$(find_rec self.example.com 8)" "certs_why_self_signed"
is "IP-серт: не годен"         "$(find_rec certs_src_acme_ip 7)" "no"

# goTelegram в Lite сертификата не имеет, а mask_host — ЧУЖОЙ домен
# (google.com). Принять его за свой означало бы предложить оператору
# выпустить сертификат на чужое имя.
gt="$(find_rec certs_src_gotelegram 1)"
is "goTelegram Lite не даёт сертификата" "$gt" ""
gm=0
for rec in "${CERT_CANDIDATES[@]}"; do
    case "$(printf '%s' "$rec" | cut -f1)" in google.com) gm=1 ;; esac
done
is "mask_host не принят за свой домен" "$gm" "0"

# Каждая запись — ровно 8 полей, иначе cut в godns.sh молча съедет.
badf=0
for rec in "${CERT_CANDIDATES[@]}"; do
    f=$(printf '%s' "$rec" | awk -F'\t' '{print NF}')
    [ "$f" = "8" ] || badf=$((badf+1))
done
is "у всех записей по 8 полей" "$badf" "0"

head_ "инвариант: чужое не трогаем"
before=$(find "$LE" -type f -newer "$CA/ca.crt" 2>/dev/null | wc -l | tr -d ' ')
certs_discover >/dev/null 2>&1
after=$(find "$LE" -type f -newer "$CA/ca.crt" 2>/dev/null | wc -l | tr -d ' ')
is "обнаружение ничего не пишет в letsencrypt" "$after" "$before"
# Комментарии не в счёт: в certs.sh намеренно оставлено объяснение, почему
# этой команды там нет, и оно содержит саму команду. Ищем в КОДЕ.
if grep -vE '^[[:space:]]*#' "$SCRIPT_DIR/lib/certs.sh" \
     | grep -qE 'install-cert|--reloadcmd|--key-file|--fullchain-file'; then
    bad "в certs.sh есть запись в конфиг acme.sh"
else
    ok "в certs.sh нет ни одной записи в конфиг acme.sh"
fi

printf "\n${D}%s${N}\n" "────────────────────────────────────────────────────────────"
if [ "$FAIL" -eq 0 ]; then
    printf "  ${G}✓ все %d проверок пройдены${N}\n\n" "$PASS"; exit 0
fi
printf "  ${R}✗ провалено %d из %d${N}\n\n" "$FAIL" "$((PASS+FAIL))"; exit 1
