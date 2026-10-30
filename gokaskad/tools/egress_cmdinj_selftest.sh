#!/usr/bin/env bash
# tools/egress_cmdinj_selftest.sh — 2.13.2, безопасность: адрес и имя ключа «Прокси для API Telegram» приходят из чужой
# ссылки или подписки. Хост и порт ключа шли в текст команды внутреннего bash (egress_key_ping, экран пула) — метасимволы
# оболочки в них выполнялись бы от root. Теперь разборщик берёт только IP или домен (IDN — в punycode) и порт 1..65535,
# bash передаёт хост и порт аргументами и проверяет их сам; имена ключей уходят в терминал без управляющих символов.
# Без root и без сети. На коде до правки проверки «метка не создана» падают.
# Запуск: bash tools/egress_cmdinj_selftest.sh          (exit 0 = всё зелёное)
set -u
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
S="${EGRESS_SELFTEST_SCRIPT:-$ROOT_DIR/install.sh}"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
pass=0; fail=0
ok(){ printf '  \033[32mPASS\033[0m %s\n' "$1"; pass=$((pass+1)); }
no(){ printf '  \033[31mFAIL\033[0m %s\n' "$1"; [ -n "${2:-}" ] && printf '       %s\n' "$2"; fail=$((fail+1)); }

EG="$TMP/egress"; P="$EG/egress_parse.py"
EG="$EG" bash -c 'source "$1" --lib >/dev/null 2>&1 || exit 97; EGRESS_DIR="$EG"; EGRESS_PARSE_PY="$EG/egress_parse.py"; egress_ensure_parser' _ "$S" >/dev/null 2>&1
[ -s "$P" ] || { echo "парсер не записан"; exit 1; }
U="11111111-2222-3333-4444-555555555555"
T="?type=tcp&security=reality&pbk=8FXcm8haAt5zxGJFC6-71alFTj_LhAL5hbOx7QBE6lc&sid=80cd0a54&sni=www.microsoft.com"
vless(){ printf 'vless://%s@%s:%s%s#%s\n' "$U" "$1" "${2:-443}" "$T" "${3:-k}"; }
W="$TMP/w"; mkdir -p "$W"
# хосты проходят urlparse (без «/», нижний регистр): метка — «pwned» в текущем каталоге. Без сети и DNS: до
# метасимвола — 127.0.0.1, порт — закрытый 9 (на старом коде соединение сразу отказано, без поиска имени)
HOSTS=('$(touch pwned)' '`touch pwned`' '127.0.0.1;touch pwned;' '127.0.0.1|touch pwned' '127.0.0.1&&touch pwned'
       "127.0.0.1'\$(touch pwned)'")
PORTS=('9;touch pwned' '$(touch pwned)' '9`touch pwned`' '0' '65536' '44a')
# настоящий предел времени: timeout(1), а где его нет (macOS) — alarm perl; ping — подмена (журнал вызовов)
FN="command -v timeout >/dev/null 2>&1 || timeout() { local t=\"\$1\"; shift; perl -e 'alarm(int(shift) + 1); exec @ARGV' \"\$t\" \"\$@\"; }
ping() { echo \"ping \$*\" >> \"$W/ping.log\"; echo 'time=7.0 ms'; }"

echo "── 1. разборщик: плохой адрес не берётся ──"
bad=""; for h in "${HOSTS[@]}"; do bad+="$(vless "$h")"$'\n'; done
for p in "${PORTS[@]}"; do bad+="$(vless 198.51.100.20 "$p")"$'\n'; done
out=$(printf '%s' "$bad" | python3 "$P" extract 2>/dev/null)
[ -z "$out" ] && ok "ключи VLESS с метасимволами в хосте или порте, порт 0/65536/«44a» — не взяты" || no "взяты ключи с плохим адресом" "$out"
good="$(vless 198.51.100.20 8443)"$'\n'"$(vless 'Exit-1.Example.com')"$'\n'"$(vless '[2001:db8::1]')"$'\n'"$(vless 'пример.рф')"
out=$(printf '%s\n' "$good" | python3 "$P" extract 2>/dev/null | grep -c '^vless://')
[ "$out" = 4 ] && ok "IP, домен, IPv6 и IDN — взяты" || no "хорошие адреса не взяты: $out из 4"
out=$(printf '%s\n' "$good" | python3 "$P" config 2>/dev/null | grep -o '"address": "[^"]*"' | tr '\n' ' ')
case "$out" in *'"198.51.100.20"'*'"exit-1.example.com"'*'"2001:db8::1"'*'"xn--e1afmkfd.xn--p1ai"'*) ok "в xray — проверенный адрес (IDN — в punycode)" ;;
    *) no "адреса в конфиге xray не те" "$out" ;; esac
conf(){ printf '[Interface]\nAddress = 10.8.1.5/32\nPrivateKey = %s\nJc = 4\n\n[Peer]\nPublicKey = %s\nAllowedIPs = 0.0.0.0/0\nEndpoint = %s\n' \
    "aGVsbG8taGVsbG8taGVsbG8taGVsbG8taGVsbG8tMTI=" "d29ybGQtd29ybGQtd29ybGQtd29ybGQtd29ybGQtMTI=" "$1"; }
out=$(conf 'a;touch pwned;:51820' | python3 "$P" extract 2>/dev/null)
[ -z "$out" ] && ok "AmneziaWG с метасимволами в Endpoint — не взят" || no "взят AmneziaWG с плохим Endpoint"
out=$(conf '198.51.100.31:51820' | python3 "$P" extract 2>/dev/null)
[ -n "$out" ] && ok "AmneziaWG с обычным Endpoint — взят" || no "не взят обычный AmneziaWG"
out=$( { conf '[fe80::1%eth0]:51820'; conf $'[fe80::1%\e]52;c;QQ==\a]:51820'; } | python3 "$P" extract 2>/dev/null)
[ -z "$out" ] && ok "AmneziaWG: IPv6 с зоной «%…» в Endpoint (и ESC в хвосте) — не взят" || no "взят Endpoint IPv6 с зоной"
out=$(conf '198.51.100.31:51820' | sed 's#^Address = .*#Address = 10.8.1.5/32;touch pwned#' | python3 "$P" extract 2>/dev/null)
[ -z "$out" ] && ok "AmneziaWG: Address не адрес — не взят" || no "взят AmneziaWG с плохим Address"
out=$(printf 'vless://%s@%s%s#k\n' "$U" '2001:db8::1:443' "$T" "$U" '198.51.100.1:443:8443' "$T" | python3 "$P" extract 2>/dev/null)
[ -z "$out" ] && ok "VLESS: IPv6 без скобок и «хост:порт:порт» — не взяты (порт — как у urllib)" || no "взят ключ с неоднозначным портом" "$out"
taken=""
for a in 10.8.0.2/0 10.8.0.2/1 10.8.0.2/7 0.0.0.0/32 fd42::2/48; do
    out=$(conf '198.51.100.31:51820' | sed "s#^Address = .*#Address = $a#" | python3 "$P" extract 2>/dev/null)
    [ -z "$out" ] || taken+="$a "
done
[ -z "$taken" ] && ok "AmneziaWG: Address /0, /1, /7, 0.0.0.0, IPv6 /48 — не взяты" || no "AmneziaWG: взяты Address $taken"
good_a=""; for a in 10.8.1.5/32 10.8.1.5 10.8.0.2/24 fd42::2/64; do
    out=$(conf '198.51.100.31:51820' | sed "s#^Address = .*#Address = $a#" | python3 "$P" extract 2>/dev/null)
    good_a+="$(printf '%s\n' "$out" | python3 "$P" awglist 2>/dev/null | cut -f5) "
done
[ "$good_a" = "10.8.1.5/32 10.8.1.5/32 10.8.0.2/24 fd42::2/64 " ] && ok "AmneziaWG: Address /32, без префикса, /24, IPv6 /64 — взяты ($good_a)" \
    || no "AmneziaWG: обычные Address" "$good_a"
grep -q 'ip addr add "$addr" dev "$iface" noprefixroute' "$S" && ok "адрес ноги — noprefixroute (/24 ключа не забирает сеть)" \
    || no "адрес ноги без noprefixroute"
out=$(printf '%s\n' "$(vless '[fe80::1%25eth0]')" "$(vless '[fe80::1%25%1b]52;c;QQ==%07]')" | python3 "$P" extract 2>/dev/null)
[ -z "$out" ] && ok "VLESS: IPv6 с зоной «%25…» (и OSC в хвосте) — не взят" || no "взят VLESS с IPv6-зоной" "$out"

echo "── 2. пул, записанный до правки: list не печатает адрес, config его не берёт ──"
POOL="$W/upstream.txt"
{ for h in "${HOSTS[@]}"; do vless "$h"; done
  python3 - "$(conf '198.51.100.31:51820' | python3 "$P" extract 2>/dev/null)" <<'PY'
import base64, json, sys
l = sys.argv[1][6:]; r = json.loads(base64.urlsafe_b64decode(l + "=" * (-len(l) % 4)))
r["peer"]["endpoint"] = "-c9:51820"
print("awg://" + base64.urlsafe_b64encode(json.dumps(r).encode()).decode().rstrip("="))
PY
  vless 198.51.100.20; } > "$POOL"
out=$(python3 "$P" list < "$POOL" | cut -f2 | tr '\n' ' ')
[ "$out" = "? ? ? ? ? ? ? 198.51.100.20:443 " ] && ok "list: «?» вместо плохого адреса" || no "list печатает адрес" "$out"
out=$(python3 "$P" config < "$POOL" 2>/dev/null | grep -c '"protocol": "vless"')
[ "$out" = 1 ] && ok "config: ключи с плохим адресом пропущены" || no "config взял ключи с плохим адресом: $out"
out=$(python3 "$P" awglist < "$POOL")
[ -z "$out" ] && ok "awglist: ключа с плохим Endpoint нет (нога прежней версии снимается)" || no "awglist печатает ключ с плохим Endpoint" "$out"

echo "── 3. экран пула: list → хост:порт → egress_key_ping / awg_ping_ms ничего не выполняют ──"
rm -f "$W/pwned" "$W/ping.log"
(cd "$W" && bash -c 'source "$1" --lib >/dev/null 2>&1 || exit 97; eval "$2"
    while IFS=$'"'"'\t'"'"' read -r name host sec net; do
        hp="$host"; p="${hp##*:}"; h="${hp%:*}"
        if [[ "$sec" == amnezia* ]]; then awg_ping_ms "$h"; else egress_key_ping "$h" "$p"; fi
    done < <(python3 "$3" list < "$4")' _ "$S" "$FN" "$P" "$POOL" > "$W/out" 2>&1)
[ ! -e "$W/pwned" ] && ok "метка не создана (путь экрана пула)" || no "хост ключа выполнился как команда"
[ ! -e "$W/ping.log" ] && ok "ping не получил ключ команды из адреса" || no "ping вызван с адресом из ключа" "$(cat "$W/ping.log")"
for h in "${HOSTS[@]}" 'a
touch pwned'; do
    (cd "$W" && bash -c 'source "$1" --lib >/dev/null 2>&1 || exit 97; eval "$2"; egress_key_ping "$3" 9' _ "$S" "$FN" "$h" >/dev/null 2>&1)
done
for p in "${PORTS[@]}"; do
    (cd "$W" && bash -c 'source "$1" --lib >/dev/null 2>&1 || exit 97; eval "$2"; egress_key_ping 127.0.0.1 "$3"' _ "$S" "$FN" "$p" >/dev/null 2>&1)
done
[ ! -e "$W/pwned" ] && ok "метка не создана (egress_key_ping напрямую: хост и порт)" || no "egress_key_ping выполнил хост или порт"
rm -f "$W/ping.log"
out=$(cd "$W" && bash -c 'source "$1" --lib >/dev/null 2>&1 || exit 97; eval "$2"; awg_ping_ms -c9' _ "$S" "$FN" 2>&1)
[ "$out" = "—" ] && [ ! -e "$W/ping.log" ] && ok "awg_ping_ms «-c9» — без вызова ping" || no "awg_ping_ms передал «-c9» в ping" "$out"
out=$(cd "$W" && bash -c 'source "$1" --lib >/dev/null 2>&1 || exit 97; eval "$2"; awg_ping_ms 127.0.0.1' _ "$S" "$FN" 2>&1)
[ "$out" = 7 ] && grep -q ' 127.0.0.1$' "$W/ping.log" 2>/dev/null && ok "контроль: awg_ping_ms 127.0.0.1 — ping вызван, 7 мс" \
    || no "контроль awg_ping_ms: хороший адрес не дошёл до ping" "$out"
# контроль egress_key_ping: свой слушатель на 127.0.0.1 (останавливается по своему PID)
python3 -c 'import socket, sys, time
s = socket.socket(); s.bind(("127.0.0.1", 0)); s.listen(16); print(s.getsockname()[1], flush=True)
s.settimeout(1); end = time.time() + 30
while time.time() < end:
    try: s.accept()[0].close()
    except OSError: pass' > "$W/port" &
LPID=$!
for _ in 1 2 3 4 5 6 7 8 9 10; do [ -s "$W/port" ] && break; sleep 0.2; done
LPORT=$(head -1 "$W/port" 2>/dev/null)
out=$(bash -c 'source "$1" --lib >/dev/null 2>&1 || exit 97; eval "$2"; egress_key_ping 127.0.0.1 "$3"' _ "$S" "$FN" "$LPORT" 2>&1)
kill "$LPID" 2>/dev/null; wait "$LPID" 2>/dev/null
[[ "$out" =~ ^[0-9]+$ ]] && ok "контроль: egress_key_ping 127.0.0.1:$LPORT — замер есть ($out мс)" || no "контроль egress_key_ping: хороший адрес не замерен" "$out"
out=$(bash -c 'source "$1" --lib >/dev/null 2>&1 || exit 97; eval "$2"; egress_key_ping 127.0.0.1 9' _ "$S" "$FN" 2>&1)
[ "$out" = "—" ] && ok "контроль: закрытый порт — «—»" || no "закрытый порт" "$out"

echo "── 4. имена ключей — без управляющих символов ──"
printf '%s\n' "$(vless 198.51.100.20 443 'a%1b]52;c;SGVsbG8=%07b')" "$(vless 198.51.100.21 443 'a%1b[2J%c2%9bb')" > "$W/names"
out=$(python3 "$P" list < "$W/names" | cut -f1)
if ! printf '%s' "$out" | python3 -c 'import re, sys; sys.exit(1 if re.search(r"[\x00-\x09\x0b-\x1f\x7f-\x9f]", sys.stdin.read()) else 0)'; then
    no "в имени остались управляющие символы" "$(printf '%s' "$out" | od -c | head -3)"
else ok "ESC, BEL, OSC 52 и C1 убраны: $(printf '%s' "$out" | tr '\n' '|')"; fi

echo
echo "Итог: PASS=$pass FAIL=$fail"
[ "$fail" -eq 0 ]
