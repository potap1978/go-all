#!/usr/bin/env bash
# tools/egress_parser_selftest.sh — парсер ключей «Прокси для API Telegram» (VLESS + AmneziaWG 1.0–3.1).
# Без root и без сети: парсер берётся из install.sh (egress_ensure_parser во временный каталог),
# ключи синтетические. Проверяет и бесшовность: строки пула, записанные парсером v6 (Kaskad 2.8–2.12,
# копия в tools/fixtures/egress_parse_v6.py), новый парсер обязан читать так же, как старый.
# Запуск: bash tools/egress_parser_selftest.sh          (exit 0 = всё зелёное)
set -u
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
S="${EGRESS_SELFTEST_SCRIPT:-$ROOT_DIR/install.sh}"
V6="$ROOT_DIR/tools/fixtures/egress_parse_v6.py"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
pass=0; fail=0
ok(){ printf '  \033[32mPASS\033[0m %s\n' "$1"; pass=$((pass+1)); }
no(){ printf '  \033[31mFAIL\033[0m %s\n' "$1"; [ -n "${2:-}" ] && printf '       %s\n' "$2"; fail=$((fail+1)); }
has(){ case "$2" in *"$1"*) return 0;; *) return 1;; esac; }
F="$TMP/fx"; mkdir -p "$F"

echo "── 1. egress_ensure_parser пишет парсер (и переписывает любой другой) ──"
EG="$TMP/egress"
ensure() { EG="$EG" bash -c 'source "$1" --lib >/dev/null 2>&1 || exit 97; EGRESS_DIR="$EG"; EGRESS_PARSE_PY="$EG/egress_parse.py"; egress_ensure_parser; echo "RC=$?"' _ "$S"; }
o=$(ensure); P="$EG/egress_parse.py"
has "RC=0" "$o" && [ -s "$P" ] && ok "парсер записан" || no "парсер не записан" "$o"
grep -q '^# PARSER_VER=9 ' "$P" && ok "PARSER_VER=9" || no "нет строки PARSER_VER=9"
python3 -m py_compile "$P" 2>/dev/null && ok "python компилирует парсер" || no "парсер не компилируется"
left(){ ls "$EG"/.parse.* >/dev/null 2>&1; }
! left && ok "временного файла не осталось" || no "остался временный файл $(ls "$EG"/.parse.* 2>/dev/null)"
cp "$V6" "$P"; ensure >/dev/null; grep -q '^# PARSER_VER=9 ' "$P" && ok "парсер v6 на диске переписан (сервер после self_update)" || no "v6 не переписан"
cp "$P" "$TMP/p.ok"; printf '# правка без нового номера\n' >> "$P"; ensure >/dev/null
cmp -s "$P" "$TMP/p.ok" && ok "та же метка PARSER_VER, другое содержимое — всё равно переписан (сравнение по содержимому)" || no "парсер с той же меткой, но другим содержимым не переписан"
! left && ok "после подмены временных файлов нет" || no "после подмены остался временный файл"
m1=$(cksum < "$P"); ensure >/dev/null; [ "$(cksum < "$P")" = "$m1" ] && ok "повторный вызов ничего не меняет" || no "повторный вызов переписал файл"
PY(){ python3 "$P" "$@"; }
V6PY(){ python3 "$V6" "$@"; }

echo "── 2. синтетические ключи ──"
python3 - "$F" <<'GEN'
import base64, json, zlib, sys, os
F = sys.argv[1]
k = lambda s: base64.b64encode(bytes((s * 7 + i) % 256 for i in range(32))).decode()
I1_DNS = "<r 2><b 0x858000010001000000000669636c6f756403636f6d0000010001c00c000100010000105a00044d583737>"
I1_QUIC = "<b 0xc30000000108><r 8><b 0x0000449e><r 1182>"
def conf(addr, priv, iface, pub, psk, ep, ka, dns="1.1.1.1, 1.0.0.1"):
    out = ["[Interface]", "Address = %s" % addr, "DNS = %s" % dns, "PrivateKey = %s" % priv]
    out += ["%s = %s" % kv for kv in iface]
    out += ["", "[Peer]", "PublicKey = %s" % pub]
    if psk: out.append("PresharedKey = %s" % psk)
    out += ["AllowedIPs = 0.0.0.0/0, ::/0", "Endpoint = %s" % ep, "PersistentKeepalive = %s" % ka]
    return "\n".join(out) + "\n"
J = [("Jc", "4"), ("Jmin", "40"), ("Jmax", "70")]
p10 = J + [("S1", "116"), ("S2", "61"), ("H1", "1234567801"), ("H2", "1234567802"), ("H3", "1234567803"), ("H4", "1234567804")]
p15 = p10 + [("I1", I1_DNS)]
p20 = J + [("S1", "44"), ("S2", "60"), ("S3", "37"), ("S4", "4"), ("H1", "1000000001-1500000001"),
           ("H2", "1500000002-2000000002"), ("H3", "2000000003-2500000003"), ("H4", "2500000004-3000000004"), ("I1", I1_DNS)]
# 3.1 как у goAmnezia pro (lib/awgkeys.py, gen_params + render_conf): S=12, H=1..4, ключ защиты заголовков, тайминги.
p31 = [("MTU", "1280"), ("Jc", "5"), ("Jmin", "10"), ("Jmax", "50"), ("S1", "12"), ("S2", "12"), ("S3", "12"), ("S4", "12"),
       ("H1", "1"), ("H2", "2"), ("H3", "3"), ("H4", "4"), ("I1", I1_QUIC), ("HeaderProtectionKey", k(50)),
       ("RekeyAfterTime", "100-120"), ("RekeyTimeout", "3-7"), ("RejectAfterTime", "150-180"), ("KeepaliveTimeout", "5-15"),
       ("MaxHandshakeAttempts", "15-20"), ("RandomTrailers", "on"), ("DisableCookies", "on")]
c10 = conf("10.13.13.2/32", k(1), p10, k(100), k(200), "198.51.100.10:51820", "25")
c15 = conf("10.13.13.3/32", k(2), p15, k(101), k(201), "198.51.100.15:41181", "25")
c20 = conf("10.13.13.4/32", k(3), p20, k(102), k(202), "198.51.100.20:34472", "25")
c31 = conf("10.8.1.5/32", k(4), p31, k(103), k(203), "198.51.100.31:47123", "25-35")
# 3.1 по шаблону приложения Amnezia (template.conf): все строки, пустые значения, плейсхолдеры DNS, ContentPaddingAddition.
app = [("Jc", "3"), ("Jmin", "10"), ("Jmax", "30"), ("S1", "12"), ("S2", "12"), ("S3", "12"), ("S4", "12"),
       ("H1", "1"), ("H2", "2"), ("H3", "3"), ("H4", "4"), ("I1", I1_DNS), ("I2", ""), ("I3", ""), ("I4", ""), ("I5", ""),
       ("HeaderProtectionKey", k(51)), ("ContentPaddingAddition", "10-100"), ("RekeyAfterTime", "100-120"),
       ("RekeyTimeout", "3-7"), ("RejectAfterTime", "150-180"), ("KeepaliveTimeout", "5-15"),
       ("MaxHandshakeAttempts", "15-20"), ("RandomTrailers", "on"), ("DisableCookies", "on")]
c31app = conf("10.8.1.6/32", k(5), app, k(104), k(204), "198.51.100.32:55424", "25-35", dns="$PRIMARY_DNS, $SECONDARY_DNS")
# 2.0 с выключенными переключателями 3.x (так их пишет шаблон приложения): линия 2.0, строк 3.x в awg быть не должно.
c20off = conf("10.13.13.7/32", k(6), p20 + [("RandomTrailers", "off"), ("DisableCookies", "off"), ("ContentPaddingAddition", "")],
              k(105), k(205), "198.51.100.27:34473", "25")

def qcompress(b): return len(b).to_bytes(4, "big") + zlib.compress(b, 8)
def vpn(conf_text, params, host, port, desc, ka, container_extra=None, last_extra=True, proto="3.1", top_extra=None):
    last = {"config": conf_text, "hostName": host, "port": port, "client_ip": "10.8.1.9", "client_priv_key": "x",
            "server_pub_key": "y", "allowed_ips": ["0.0.0.0/0", "::/0"], "persistent_keep_alive": ka, "mtu": "1280"}
    if last_extra: last.update(params)
    awg = {"port": str(port), "transport_proto": "udp", "protocol_version": proto}
    awg.update(container_extra if container_extra is not None else params)
    awg["last_config"] = json.dumps(last, separators=(",", ":"), sort_keys=True)
    top = {"containers": [{"container": "amnezia-awg2", "awg": awg}], "defaultContainer": "amnezia-awg2",
           "hostName": host, "description": desc, "dns1": "1.1.1.1", "dns2": "1.0.0.1"}
    top.update(top_extra or {})
    raw = json.dumps(top, separators=(",", ":"), sort_keys=True).encode()
    return "vpn://" + base64.urlsafe_b64encode(qcompress(raw)).decode().rstrip("=")
def w(name, text):
    with open(os.path.join(F, name), "w") as f: f.write(text)
d31 = dict((a, b) for a, b in p31 if a != "MTU")
w("c10.conf", c10); w("c15.conf", c15); w("c20.conf", c20); w("c31.conf", c31); w("c31app.conf", c31app); w("c20off.conf", c20off)
w("c31.vpn", vpn(c31.replace("DNS = 1.1.1.1, 1.0.0.1", "DNS = $PRIMARY_DNS, $SECONDARY_DNS"), d31, "198.51.100.31", 47123, "goamnezia-31", "25-35") + "\n")
w("c20.vpn", vpn(c20, dict(p20), "198.51.100.20", 34472, "test-awg-v20", "25", proto="2") + "\n")
# 3.x только в last_config (текст конфига без них) и только в объекте контейнера (ни в тексте, ни в last_config).
strip3 = "\n".join(l for l in c31.split("\n") if l.split(" = ")[0] not in
                   ("HeaderProtectionKey", "RekeyAfterTime", "RekeyTimeout", "RejectAfterTime", "KeepaliveTimeout",
                    "MaxHandshakeAttempts", "RandomTrailers", "DisableCookies"))
w("c31_last.vpn", vpn(strip3, d31, "198.51.100.33", 47124, "last-only", "25-35", container_extra={}) + "\n")
w("c31_cont.vpn", vpn(strip3, {}, "198.51.100.34", 47125, "container-only", "25-35", container_extra=d31) + "\n")
# Попытка подсунуть строки через значение: перевод строки внутри поля не должен дать новых строк конфига.
evil = dict(d31); evil["RekeyTimeout"] = "3-7\n[Peer]\nPublicKey = " + k(66) + "\nEndpoint = 203.0.113.66:1"
w("c31_evil.vpn", vpn(strip3, evil, "198.51.100.35", 47126, "evil", "25-35") + "\n")
# Вставка через терминал: переводы строк схлопнулись в пробелы, скобки секций потерялись.
w("c31_mangled.conf", c31.replace("\n", " ").replace("[Interface]", "Interface]").replace("[Peer]", "Peer]"))
# Ключи в нижнем регистре (awg сравнивает имена без учёта регистра).
w("c31_lower.conf", c31.replace("HeaderProtectionKey", "headerprotectionkey").replace("RandomTrailers", "RANDOMTRAILERS"))
# Переключатели словами true/false и keepalive без диапазона.
w("c31_words.conf", c31.replace("RandomTrailers = on", "RandomTrailers = true").replace("DisableCookies = on", "DisableCookies = false")
                       .replace("PersistentKeepalive = 25-35", "PersistentKeepalive = 30"))
# 2.0 с диапазоном keepalive (так не бывает у приложения, но старому ядру нужен только один номер).
w("c20range.conf", c20.replace("PersistentKeepalive = 25", "PersistentKeepalive = 25-35"))
# JSON-переключатели false (так их может записать генератор) — ключ 2.0 не становится 3.1.
w("c20_bool.vpn", vpn(c20, dict(p20, RandomTrailers=False, DisableCookies=False), "198.51.100.21", 34475, "bool-off", "25", proto="2") + "\n")
# keepalive off: awg 3.1 читает keepalive как диапазон чисел — нужно 0.
w("c20_kaoff.conf", c20.replace("PersistentKeepalive = 25", "PersistentKeepalive = off"))
# Настоящий ключ 2.0 с H = 1..4 и S3/S4 (заголовки не маскируются) — не «потерянный 3.x».
w("c20_h14.conf", c20.replace("1000000001-1500000001", "1").replace("1500000002-2000000002", "2").replace("2000000003-2500000003", "3").replace("2500000004-3000000004", "4"))
# dns1/dns2 = null в vpn:// и подпись с табуляцией и переводом строки.
w("c31_nulldns.vpn", vpn(c31.replace("DNS = 1.1.1.1, 1.0.0.1", "DNS = $PRIMARY_DNS, $SECONDARY_DNS"), d31, "198.51.100.36", 47127, "null-dns", "25-35", top_extra={"dns1": None, "dns2": None}) + "\n")
w("c31_tab.vpn", vpn(c31, d31, "198.51.100.37", 47128, "Tab\tName\nLine", "25-35") + "\n")
w("vless.txt", "vless://11111111-2222-3333-4444-555555555555@198.51.100.50:443?type=tcp&security=reality&pbk=abc&sid=01&sni=example.com&fp=chrome&flow=xtls-rprx-vision#nl-1\n")
GEN
[ -s "$F/c31.vpn" ] && ok "ключи сгенерированы" || { no "генератор ключей упал"; echo "  PASS=$pass  FAIL=$fail"; exit 1; }

# Значение строки «Ключ = …» из вывода awgconf.
val(){ printf '%s\n' "$2" | sed -n "s/^$1 = //p" | head -1; }
check31() {   # $1 — подпись, $2 — файл ключа, $3 — ожидаемый keepalive
    local out conf l
    out=$(PY extract < "$2"); l=$(printf '%s\n' "$out" | PY list)
    has "amnezia-3.1	udp" "$l" && ok "$1: линия 3.1" || no "$1: линия не 3.1" "$l"
    has "1	3.1	ok" "$(printf '%s\n' "$out" | PY awginfo)" && ok "$1: awginfo 1/3.1/ok" || no "$1: awginfo" "$(printf '%s\n' "$out" | PY awginfo)"
    conf=$(printf '%s\n' "$out" | PY awgconf 1)
    local miss="" kv
    for kv in "HeaderProtectionKey=$(python3 -c 'import base64;print(base64.b64encode(bytes((50*7+i)%256 for i in range(32))).decode())')" \
              "RekeyAfterTime=100-120" "RekeyTimeout=3-7" "RejectAfterTime=150-180" "KeepaliveTimeout=5-15" "MaxHandshakeAttempts=15-20" \
              "S1=12" "S4=12" "H1=1" "H4=4" "I1=<b 0xc30000000108><r 8><b 0x0000449e><r 1182>"; do
        [ "$(val "${kv%%=*}" "$conf")" = "${kv#*=}" ] || miss="$miss ${kv%%=*}"
    done
    [ -z "$miss" ] && ok "$1: поля 3.x, S, H и I1 переданы в awg как есть" || no "$1: не совпали:$miss" "$(printf '%s' "$conf" | tr '\n' '|')"
    [ "$(val PersistentKeepalive "$conf")" = "$3" ] && ok "$1: PersistentKeepalive = $3" || no "$1: keepalive" "$(val PersistentKeepalive "$conf")"
}

echo "── 3. AmneziaWG 3.1: .conf и vpn:// ──"
check31 "goAmnezia .conf" "$F/c31.conf" "25-35"
o=$(PY extract < "$F/c31.conf" | PY awgconf 1)
[ "$(val RandomTrailers "$o")/$(val DisableCookies "$o")" = "on/on" ] && ok "goAmnezia .conf: RandomTrailers/DisableCookies = on" || no "переключатели" "$o"
check31 "goAmnezia vpn://" "$F/c31.vpn" "25-35"
check31 "vpn://, 3.x только в last_config" "$F/c31_last.vpn" "25-35"
check31 "vpn://, 3.x только в объекте контейнера" "$F/c31_cont.vpn" "25-35"
check31 ".conf, схлопнутый в одну строку" "$F/c31_mangled.conf" "25-35"
check31 ".conf, ключи в другом регистре" "$F/c31_lower.conf" "25-35"
o=$(PY extract < "$F/c31_lower.conf" | PY awgconf 1)
has "HeaderProtectionKey = " "$o" && has "RandomTrailers = on" "$o" && ok "имена приведены к виду awg (HeaderProtectionKey, RandomTrailers)" || no "регистр имён" "$o"
o=$(PY extract < "$F/c31app.conf"); c=$(printf '%s\n' "$o" | PY awgconf 1)
has "amnezia-3.1" "$(printf '%s\n' "$o" | PY list)" && [ "$(val ContentPaddingAddition "$c")" = "10-100" ] && ok "шаблон приложения: 3.1, ContentPaddingAddition = 10-100" || no "шаблон приложения" "$c"
printf '%s\n' "$c" | grep -qE '^I[2-5] =' && no "пустые I2–I5 попали в конфиг" "$c" || ok "пустые I2–I5 отброшены"
printf '%s\n' "$c" | grep -q 'PRIMARY_DNS' && no "плейсхолдер DNS попал в конфиг awg" || ok "DNS в awg не передаётся"
o=$(PY extract < "$F/c31_words.conf" | PY awgconf 1)
[ "$(val RandomTrailers "$o")/$(val DisableCookies "$o")" = "on/off" ] && ok "true/false → on/off (awg понимает только on/off/0/1)" || no "true/false" "$o"
[ "$(val PersistentKeepalive "$o")" = "30" ] && ok "keepalive без диапазона — как есть" || no "keepalive 30" "$o"
o=$(PY extract < "$F/c31_evil.vpn" | PY awgconf 1)
[ "$(printf '%s\n' "$o" | grep -c '^\[Peer\]')" = "1" ] && ! printf '%s\n' "$o" | grep -q '^Endpoint = 203' && ok "перевод строки в значении не создаёт строк конфига (одна битая строка — awg её отвергнет)" || no "внедрение строк" "$(printf '%s' "$o" | tr '\n' '|')"
o=$(cat "$F/c31.conf" "$F/c31.vpn" "$F/vless.txt" "$F/c20.vpn" | PY extract)
[ "$(printf '%s\n' "$o" | grep -c .)" = "4" ] && ok "смешанный ввод: 4 ключа (.conf 3.1, vpn:// 3.1, vless, vpn:// 2.0)" || no "смешанный ввод" "$(printf '%s\n' "$o" | grep -c .)"
cfg=$(printf '%s\n' "$o" | PY config --mss 1000 2>/dev/null)
printf '%s' "$cfg" | python3 -c '
import json,sys
c=json.load(sys.stdin); o=[x for x in c["outbounds"] if x["tag"].startswith("out_")]
assert [x["protocol"] for x in o]==["freedom","freedom","freedom","vless"], [x["protocol"] for x in o]
assert [x["streamSettings"]["sockopt"].get("mark") for x in o if x["protocol"]=="freedom"]==[257,258,259]
assert c["routing"]["balancers"][0]["tag"]=="bot-bal"' 2>/dev/null && ok "конфиг xray: AWG 3.1 — freedom с меткой 256+номер, балансир" || no "конфиг xray для пула с 3.1"
printf '%s\n' "$o" | PY awglist | awk -F'\t' '$1==1 && $2=="kawg1" && $5=="10.8.1.5/32" && $6=="198.51.100.31"{f=1} END{exit !f}' && ok "awglist: kawg1, адрес и узел ключа 3.1" || no "awglist" "$(printf '%s\n' "$o" | PY awglist)"

echo "── 4. линии 1.0 / 1.5 / 2.0 (правила приложения Amnezia) ──"
for t in "c10.conf amnezia-1.0" "c15.conf amnezia-1.5" "c20.conf amnezia-2.0" "c20.vpn amnezia-2.0" "c20off.conf amnezia-2.0"; do
    l=$(PY extract < "$F/${t% *}" | PY list); has "${t#* }	udp" "$l" && ok "${t% *} → ${t#* }" || no "${t% *}: ждали ${t#* }" "$l"
done
o=$(PY extract < "$F/c20off.conf" | PY awgconf 1)
printf '%s\n' "$o" | grep -qE '^(RandomTrailers|DisableCookies|ContentPaddingAddition) =' && no "у ключа 2.0 в awg ушли строки 3.x (старое ядро отвергнет конфиг)" "$o" || ok "у ключа 2.0 строк 3.x в awg нет"
o=$(PY extract < "$F/c20range.conf" | PY awgconf 1)
[ "$(val PersistentKeepalive "$o")" = "25" ] && ok "2.0 с диапазоном keepalive → нижняя граница" || no "2.0 keepalive" "$o"

o=$(PY extract < "$F/c20_bool.vpn"); a=$(printf '%s\n' "$o" | PY awginfo); c=$(printf '%s\n' "$o" | PY awgconf 1)
[ "$a" = "1	2.0	ok" ] && ! printf '%s\n' "$c" | grep -qE '^(RandomTrailers|DisableCookies) =' && ok "JSON false у переключателей: ключ остаётся 2.0, строк 3.x нет" || no "JSON false сделал ключ 3.1" "$a"
o=$(PY extract < "$F/c20_kaoff.conf" | PY awgconf 1); [ "$(val PersistentKeepalive "$o")" = "0" ] && ok "PersistentKeepalive = off → 0 (awg 3.1 не принимает off)" || no "keepalive off" "$(val PersistentKeepalive "$o")"
a=$(PY extract < "$F/c20range.conf" | PY awginfo); b=$(PY extract < "$F/c20_h14.conf" | PY awginfo)
[ "$a" = "1	2.0	ok" ] && [ "$b" = "1	2.0	ok" ] && ok "ключи 2.0, добавленные новым парсером, не помечаются lost3x (диапазон keepalive, H = 1..4)" || no "ложный lost3x" "$a / $b"
o=$(PY extract < "$F/c31_nulldns.vpn"); [ "$(printf '%s\n' "$o" | PY awginfo)" = "1	3.1	ok" ] && ok "vpn:// с dns1/dns2 = null разбирается" || no "dns1 = null" "$o"
l=$(PY extract < "$F/c31_tab.vpn" | PY list); [ "$(printf '%s\n' "$l" | head -1 | awk -F'\t' '{print NF}')" = "4" ] && [ "$(printf '%s\n' "$l" | grep -c .)" = "1" ] && ok "табуляция и перевод строки в подписи не ломают колонки list" || no "подпись ломает list" "$l"
good=$(PY extract < "$F/c31.conf"); bad1="awg://$(printf '[]' | base64 | tr '+/' '-_' | tr -d '=')"; bad2="awg://$(printf '{"t":"awg","params":["x"]}' | base64 | tr '+/' '-_' | tr -d '=')"
o=$(printf '%s\n%s\n%s\n' "$bad1" "$bad2" "$good" | PY awginfo); rc=$?
[ "$rc" = "0" ] && [ "$o" = "3	3.1	ok" ] && ok "битые строки пула не мешают awginfo: ключ 3.1 на месте" || no "битые строки ломают awginfo" "rc=$rc [$o]"
o=$(printf '%s\n%s\n%s\n' "$bad1" "$bad2" "$good" | PY awglist); [ "$(printf '%s\n' "$o" | cut -f1,2)" = "3	kawg3" ] && ok "битые строки пула не мешают awglist" || no "awglist" "$o"

echo "── 5. бесшовность: пул, записанный парсером v6 (Kaskad 2.8–2.12) ──"
for f in c10.conf c15.conf c20.conf c20.vpn; do V6PY extract < "$F/$f"; done > "$TMP/pool_v6.txt"
cat "$F/vless.txt" >> "$TMP/pool_v6.txt"
python3 -c 'import sys;t=open(sys.argv[1]).read();print(t.replace("\n"," ").replace("[Interface]","Interface]"))' "$F/c10.conf" | V6PY extract >> "$TMP/pool_v6.txt"
n=$(grep -c . "$TMP/pool_v6.txt"); [ "$n" = "6" ] && ok "v6 записал 6 строк пула (5 AWG + VLESS)" || no "v6 записал $n строк"
same=1; for i in 1 2 3 4 6; do
    [ "$(V6PY awgconf "$i" < "$TMP/pool_v6.txt")" = "$(PY awgconf "$i" < "$TMP/pool_v6.txt")" ] || { same=0; no "awgconf #$i отличается от v6" "$(diff <(V6PY awgconf "$i" < "$TMP/pool_v6.txt") <(PY awgconf "$i" < "$TMP/pool_v6.txt") | tr '\n' '|')"; }
done; [ "$same" = 1 ] && ok "awgconf для всех строк v6 — байт в байт как у v6"
[ "$(V6PY awglist < "$TMP/pool_v6.txt")" = "$(PY awglist < "$TMP/pool_v6.txt")" ] && ok "awglist — как у v6" || no "awglist отличается"
[ "$(V6PY config --mss 1000 < "$TMP/pool_v6.txt" 2>/dev/null)" = "$(PY config --mss 1000 < "$TMP/pool_v6.txt" 2>/dev/null)" ] && ok "конфиг xray — как у v6" || no "конфиг xray отличается от v6"
[ "$(V6PY list < "$TMP/pool_v6.txt" | cut -f1,2,4)" = "$(PY list < "$TMP/pool_v6.txt" | cut -f1,2,4)" ] && ok "list: имя, узел, сеть — как у v6 (меняется только подпись линии)" || no "list отличается не только подписью"
a=$(PY awginfo < "$TMP/pool_v6.txt" | tr '\n' ' '); [ "$a" = "1	1.0	ok 2	1.5	ok 3	2.0	ok 4	2.0	ok 6	1.0	ok " ] && ok "awginfo старого пула: 1.0/1.5/2.0, без lost3x, VLESS пропущен" || no "awginfo старого пула" "$a"

echo "── 6. ключ 3.1, сохранённый парсером v6 (поля 3.x потеряны) ──"
V6PY extract < "$F/c31.conf" > "$TMP/pool_lost.txt"; V6PY extract < "$F/c31.vpn" >> "$TMP/pool_lost.txt"
a=$(PY awginfo < "$TMP/pool_lost.txt" | tr '\n' ' '); [ "$a" = "1	2.0	lost3x 2	2.0	lost3x " ] && ok "awginfo помечает оба как lost3x" || no "lost3x" "$a"
o=$(PY awgconf 1 < "$TMP/pool_lost.txt"); [ "$(val PersistentKeepalive "$o")" = "25" ] && ! has "HeaderProtectionKey" "$o" && ok "awgconf такого ключа: без 3.x, keepalive 25" || no "awgconf lost3x" "$o"
o=$(PY extract < "$F/c31.conf" | PY awginfo); [ "$o" = "1	3.1	ok" ] && ok "тот же ключ, добавленный заново, — 3.1/ok" || no "повторное добавление" "$o"

echo "────────────────────────────"
echo "  PASS=$pass  FAIL=$fail"
[ "$fail" = 0 ]
