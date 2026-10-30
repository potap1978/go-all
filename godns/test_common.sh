#!/bin/bash
# goDNS — Copyright (c) 2026 potap1978. All rights reserved.
#
# goDNS — regression tests for the PURE logic in lib/common.sh and lib/i18n.sh.
#
# Runs anywhere: no root, no network, no Linux, no container. Everything under
# test is string/JSON/arithmetic manipulation, or is driven by a stub (ss(),
# python3()) injected in a subshell.
#
# Deliberately bash 3.2 compatible so it can be run on the macOS dev box —
# the associative-array parts of the i18n engine are skipped there, because on
# bash 3.2 they CANNOT work (that is exactly what the guard in lib/i18n.sh is
# for, and the guard itself IS tested here).
#
# NB: this file must NOT `set -e`. Half the assertions are "this returns 1".
#
# Usage: ./test_common.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Point every writable path at a scratch dir BEFORE sourcing, so no test can
# ever touch a real /opt/godns or /etc/godns.
TEST_DIR=$(mktemp -d "${TMPDIR:-/tmp}/godns-test.XXXXXX") || {
    echo "cannot create a temp dir" >&2; exit 1; }
export GODNS_DIR="$TEST_DIR"
export GODNS_ETC="${TEST_DIR}/etc"
export GODNS_CONFIG="${TEST_DIR}/config.json"
trap 'rm -rf "$TEST_DIR"' EXIT

# shellcheck source=lib/common.sh
. "${SCRIPT_DIR}/lib/common.sh"

PASS=0
FAIL=0

ok()  { PASS=$((PASS+1)); printf "  \033[0;32m✓\033[0m %s\n" "$1"; }
bad() {
    FAIL=$((FAIL+1))
    printf "  \033[0;31m✗\033[0m %s\n       expected: %s\n       got:      %s\n" \
        "$1" "$2" "$3"
}
skip() { printf "  \033[2m⊘ %s\033[0m\n" "$1"; }
section() { printf "\n\033[1m── %s\033[0m\n" "$1"; }

# eq <label> <expected> <actual>
eq() { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1" "$2" "$3"; fi; }

# rc <label> <expected-rc> <cmd…>   — stdout and stderr are swallowed
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

# Octal permissions, portably.
#
# GNU spells it `stat -c '%a'`, BSD/macOS spells it `stat -f '%Lp'`. Order
# matters: on Linux `stat -f` means "filesystem status" and SUCCEEDS with
# unrelated output, so a `stat -f … || stat -c …` fallback never fires and the
# test then compares permissions against a filesystem dump.
file_mode() { stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1" 2>/dev/null; }

# ═══════════════════════════════════════════════════════════════════════
section "logging goes to stderr (never poison \$( ))"
# ═══════════════════════════════════════════════════════════════════════
eq "log_info    writes nothing to stdout" "" "$(log_info    'x' 2>/dev/null)"
eq "log_success writes nothing to stdout" "" "$(log_success 'x' 2>/dev/null)"
eq "log_warning writes nothing to stdout" "" "$(log_warning 'x' 2>/dev/null)"
eq "log_error   writes nothing to stdout" "" "$(log_error   'x' 2>/dev/null)"
eq "log_dim     writes nothing to stdout" "" "$(log_dim     'x' 2>/dev/null)"
eq "log_step    writes nothing to stdout" "" "$(log_step    'x' 2>/dev/null)"

# ═══════════════════════════════════════════════════════════════════════
section "run_action defuses a failing action"
# ═══════════════════════════════════════════════════════════════════════
_boom()      { return 7; }
_boom_deep() { _boom; echo "unreachable-if-errexit-bites"; return 3; }

rc "run_action false            → rc 0" 0 run_action false
rc "run_action on rc-7 function → rc 0" 0 run_action _boom
rc "run_action passes arguments" 0 run_action printf '%s' ''
# The point of run_action: errexit is disabled for the WHOLE call tree.
eq "errexit disabled several levels deep" "0" \
   "$(set -e; run_action _boom_deep >/dev/null 2>&1; echo $?)"

# ═══════════════════════════════════════════════════════════════════════
section "os_release_get"
# ═══════════════════════════════════════════════════════════════════════
OSREL_DIR="${TEST_DIR}/osrel"
mkdir -p "$OSREL_DIR"

write_osrel() { # write_osrel <name> <<'EOF' … EOF
    cat > "${OSREL_DIR}/$1"
}

write_osrel jammy <<'EOF'
PRETTY_NAME="Ubuntu 22.04.5 LTS"
NAME="Ubuntu"
VERSION_ID="22.04"
VERSION="22.04.5 LTS (Jammy Jellyfish)"
VERSION_CODENAME=jammy
ID=ubuntu
ID_LIKE=debian
UBUNTU_CODENAME=jammy
EOF

write_osrel noble <<'EOF'
PRETTY_NAME="Ubuntu 24.04.2 LTS"
NAME="Ubuntu"
VERSION_ID="24.04"
VERSION_CODENAME=noble
ID=ubuntu
ID_LIKE=debian
UBUNTU_CODENAME=noble
EOF

write_osrel lts2604 <<'EOF'
PRETTY_NAME="Ubuntu 26.04 LTS"
NAME="Ubuntu"
VERSION_ID="26.04"
VERSION_CODENAME=resolute
ID=ubuntu
ID_LIKE=debian
UBUNTU_CODENAME=resolute
EOF

write_osrel focal <<'EOF'
PRETTY_NAME="Ubuntu 20.04.6 LTS"
NAME="Ubuntu"
VERSION_ID="20.04"
VERSION_CODENAME=focal
ID=ubuntu
UBUNTU_CODENAME=focal
EOF

write_osrel bionic <<'EOF'
PRETTY_NAME="Ubuntu 18.04.6 LTS"
NAME="Ubuntu"
VERSION_ID="18.04"
VERSION_CODENAME=bionic
ID=ubuntu
UBUNTU_CODENAME=bionic
EOF

write_osrel interim2510 <<'EOF'
PRETTY_NAME="Ubuntu 25.10"
NAME="Ubuntu"
VERSION_ID="25.10"
VERSION_CODENAME=questing
ID=ubuntu
UBUNTU_CODENAME=questing
EOF

write_osrel futurelts <<'EOF'
PRETTY_NAME="Ubuntu 28.04 LTS"
NAME="Ubuntu"
VERSION_ID="28.04"
VERSION_CODENAME=xenophon
ID=ubuntu
UBUNTU_CODENAME=xenophon
EOF

write_osrel mint <<'EOF'
NAME="Linux Mint"
VERSION="21.3 (Virginia)"
ID=linuxmint
ID_LIKE="ubuntu debian"
PRETTY_NAME="Linux Mint 21.3"
VERSION_ID="21.3"
UBUNTU_CODENAME=jammy
EOF

write_osrel debian <<'EOF'
PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"
NAME="Debian GNU/Linux"
VERSION_ID="12"
VERSION_CODENAME=bookworm
ID=debian
EOF

# No ID= line at all, plus junk — the shape a truncated/overwritten
# os-release actually has in the wild.
write_osrel malformed <<'EOF'
# this file was clobbered by a broken image build
????
NAME=
VERSION_ID
EOF

write_osrel nonnumeric <<'EOF'
ID=ubuntu
VERSION_ID="jammy"
PRETTY_NAME="Ubuntu (unknown)"
EOF

write_osrel noversion <<'EOF'
ID=ubuntu
NAME="Ubuntu"
PRETTY_NAME="Ubuntu"
EOF

OS_RELEASE_FILE="${OSREL_DIR}/jammy"
eq "reads an unquoted value"  "ubuntu"              "$(os_release_get ID)"
eq "strips double quotes"     "22.04"               "$(os_release_get VERSION_ID)"
eq "keeps inner spaces"       "Ubuntu 22.04.5 LTS"  "$(os_release_get PRETTY_NAME)"
eq "absent key → empty"       ""                    "$(os_release_get NO_SUCH_KEY)"
OS_RELEASE_FILE="${OSREL_DIR}/does-not-exist"
eq "unreadable file → empty"  ""                    "$(os_release_get ID)"

# ═══════════════════════════════════════════════════════════════════════
section "os_gate — 13 fixtures (0 = supported, 1 = refuse, 2 = warn+ask)"
# ═══════════════════════════════════════════════════════════════════════
gate() { # gate <fixture> — leaves the rc for the caller
    OS_RELEASE_FILE="${OSREL_DIR}/$1"
    os_gate
}

rc "22.04 jammy      → 0" 0 gate jammy
rc "24.04 noble      → 0" 0 gate noble
rc "26.04 resolute   → 0" 0 gate lts2604
rc "20.04 focal      → 1" 1 gate focal
rc "18.04 bionic     → 1" 1 gate bionic
rc "25.10 interim    → 2" 2 gate interim2510
rc "28.04 future LTS → 2" 2 gate futurelts
rc "linuxmint        → 1" 1 gate mint
rc "debian           → 1" 1 gate debian
rc "malformed        → 1" 1 gate malformed
rc "VERSION_ID=jammy → 1" 1 gate nonnumeric
rc "no VERSION_ID    → 1" 1 gate noversion
rc "file missing     → 1" 1 gate nowhere-at-all

# The gate must also PUBLISH what it found — callers print it.
OS_RELEASE_FILE="${OSREL_DIR}/noble"
os_gate >/dev/null 2>&1
eq "sets OS_ID"         "ubuntu"              "$OS_ID"
eq "sets OS_VERSION_ID" "24.04"               "$OS_VERSION_ID"
eq "sets OS_CODENAME"   "noble"               "$OS_CODENAME"
eq "sets OS_PRETTY"     "Ubuntu 24.04.2 LTS"  "$OS_PRETTY"

# A derivative must be named in the refusal, not silently lumped in with
# "unsupported" — the operator needs to know WHY.
OS_RELEASE_FILE="${OSREL_DIR}/mint"
eq "mint refusal names the ID" "yes" \
   "$(os_gate 2>&1 >/dev/null | grep -q 'linuxmint' && echo yes || echo no)"

# os_gate never writes to stdout — a caller may capture it.
OS_RELEASE_FILE="${OSREL_DIR}/focal"
eq "os_gate stdout is empty on refusal" "" "$(os_gate 2>/dev/null)"

OS_RELEASE_FILE="${OSREL_DIR}/jammy"

# ═══════════════════════════════════════════════════════════════════════
section "json_get"
# ═══════════════════════════════════════════════════════════════════════
J="${TEST_DIR}/sample.json"
cat > "$J" <<'EOF'
{
  "language": "ru",
  "listen": { "doh": { "port": 8443, "path": "/dns-query" } },
  "empty_string": "",
  "nothing": null,
  "yes": true,
  "no": false,
  "count": 7,
  "ratio": 0.5,
  "list": [1, 2, 3],
  "obj": { "a": 1 },
  "unicode": "Российский"
}
EOF

eq "top-level string"        "ru"          "$(json_get "$J" language)"
eq "nested two deep"         "8443"        "$(json_get "$J" listen.doh.port)"
eq "nested string"           "/dns-query"  "$(json_get "$J" listen.doh.path)"
eq "unicode survives"        "Российский"  "$(json_get "$J" unicode)"
eq "number"                  "7"           "$(json_get "$J" count)"
eq "float"                   "0.5"         "$(json_get "$J" ratio)"
eq "bool true → 'true'"      "true"        "$(json_get "$J" yes)"
eq "bool false → 'false'"    "false"       "$(json_get "$J" no)"
eq "null → default"          "DEF"         "$(json_get "$J" nothing DEF)"
eq "null → empty default"    ""            "$(json_get "$J" nothing)"
eq "empty string stays empty" ""           "$(json_get "$J" empty_string DEF)"
eq "array → default"         "DEF"         "$(json_get "$J" list DEF)"
eq "object → default"        "DEF"         "$(json_get "$J" obj DEF)"
eq "missing key → default"   "DEF"         "$(json_get "$J" nope DEF)"
eq "missing nested → default" "DEF"        "$(json_get "$J" listen.dot.port DEF)"
eq "descend into a scalar → default" "DEF" "$(json_get "$J" count.deeper DEF)"
eq "missing FILE → default"  "DEF"         "$(json_get "${TEST_DIR}/no-such.json" a DEF)"

printf '%s' '{"broken": ' > "${TEST_DIR}/bad.json"
eq "malformed JSON → default" "DEF" "$(json_get "${TEST_DIR}/bad.json" broken DEF)"
printf '%s' 'not json at all' > "${TEST_DIR}/plain.txt"
eq "not JSON at all → default" "DEF" "$(json_get "${TEST_DIR}/plain.txt" a DEF)"
printf '%s' '[1,2,3]' > "${TEST_DIR}/toplist.json"
eq "top-level array → default" "DEF" "$(json_get "${TEST_DIR}/toplist.json" a DEF)"

# json_get must never fail — it is assigned under `set -e` everywhere.
rc "json_get on garbage still returns 0" 0 json_get "${TEST_DIR}/bad.json" a DEF
rc "json_get on missing file returns 0"  0 json_get "${TEST_DIR}/no-such.json" a DEF

# ═══════════════════════════════════════════════════════════════════════
section "config_get / config_set"
# ═══════════════════════════════════════════════════════════════════════
rm -f "$GODNS_CONFIG"
eq "config_get before any config → default" "ru" "$(config_get language ru)"
succeeds "config_set creates the file" config_set language en
eq "config_get reads it back"     "en"   "$(config_get language)"
succeeds "config_set overwrites"  config_set language ru
eq "value replaced, not appended" "ru"   "$(config_get language)"
succeeds "config_set second key"  config_set doh_path /q7Xz
eq "first key survives"           "ru"   "$(config_get language)"
eq "second key readable"          "/q7Xz" "$(config_get doh_path)"
eq "config.json is 0600"          "600"  "$(file_mode "$GODNS_CONFIG")"
eq "value with spaces round-trips" "Ubuntu 24.04 LTS" \
   "$(config_set note 'Ubuntu 24.04 LTS' && config_get note)"

# Лимит запросов входит в отпечаток: иначе смена значения не применялась бы
# (тот же урок, что dot_enabled).
config_set ratelimit_qps 200
fp_a=$(godns_config_fingerprint)
config_set ratelimit_qps 0
fp_b=$(godns_config_fingerprint)
if [ "$fp_a" != "$fp_b" ]; then
    ok "ratelimit_qps входит в отпечаток"
else
    bad "ratelimit_qps не в отпечатке" "разные" "$fp_a = $fp_b"
fi

# A clobbered config must not stop the installer — config_set rebuilds it.
printf '%s' 'garbage' > "$GODNS_CONFIG"
succeeds "config_set repairs a clobbered config" config_set language ru
eq "…and the value is there"      "ru"   "$(config_get language)"

# ═══════════════════════════════════════════════════════════════════════
section "valid_port"
# ═══════════════════════════════════════════════════════════════════════
succeeds "accepts 1"      valid_port 1
succeeds "accepts 53"     valid_port 53
succeeds "accepts 853"    valid_port 853
succeeds "accepts 65535"  valid_port 65535
fails    "rejects 0"      valid_port 0
fails    "rejects 65536"  valid_port 65536
fails    "rejects 99999"  valid_port 99999
fails    "rejects empty"  valid_port ""
fails    "rejects 'abc'"  valid_port abc
fails    "rejects '80a'"  valid_port 80a
fails    "rejects '-1'"   valid_port -- -1
fails    "rejects ' 80'"  valid_port " 80"
fails    "rejects '8 0'"  valid_port "8 0"
fails    "rejects '5.3'"  valid_port 5.3
# Leading zeros must be read as decimal, not octal — 080 is not an error.
succeeds "accepts '080' as decimal 80" valid_port 080

# ═══════════════════════════════════════════════════════════════════════
section "valid_domain"
# ═══════════════════════════════════════════════════════════════════════
succeeds "example.com"                valid_domain example.com
succeeds "dns.example.com"            valid_domain dns.example.com
succeeds "a.b.c.example.co.uk"        valid_domain a.b.c.example.co.uk
succeeds "hyphens inside a label"     valid_domain my-dns.example.com
succeeds "digits in a label"          valid_domain dns1.example.com
succeeds "all-digit label"            valid_domain 1.example.com
succeeds "punycode (IDN)"             valid_domain xn--d1acufc.xn--p1ai
succeeds "uppercase"                  valid_domain DNS.Example.COM
succeeds "63-char label" \
    valid_domain "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.com"
fails    "rejects empty"              valid_domain ""
fails    "rejects a bare label"       valid_domain localhost
fails    "rejects a trailing dot"     valid_domain example.com.
fails    "rejects a leading dot"      valid_domain .example.com
fails    "rejects a double dot"       valid_domain example..com
fails    "rejects a leading hyphen"   valid_domain -bad.example.com
fails    "rejects a trailing hyphen"  valid_domain bad-.example.com
fails    "rejects a space"            valid_domain "ex ample.com"
fails    "rejects an underscore"      valid_domain "bad_label.example.com"
fails    "rejects a scheme"           valid_domain "https://example.com"
fails    "rejects a port"             valid_domain "example.com:443"
fails    "rejects a path"             valid_domain "example.com/dns-query"
fails    "rejects a 64-char label" \
    valid_domain "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.com"

# ═══════════════════════════════════════════════════════════════════════
section "random_path"
# ═══════════════════════════════════════════════════════════════════════
_rp=$(random_path);   eq "default length is 12" "12" "${#_rp}"
_rp=$(random_path 24); eq "honours length 24"   "24" "${#_rp}"
_rp=$(random_path 1);  eq "honours length 1"    "1"  "${#_rp}"
_rp=$(random_path 64); eq "honours length 64"   "64" "${#_rp}"

_rp=$(random_path 32)
eq "alphanumeric only" "" "$(printf '%s' "$_rp" | tr -d 'A-Za-z0-9')"

# Uniqueness: 100 draws of 16 chars must all differ.
_uniq=$(for _i in $(seq 1 100); do random_path 16; echo; done | sort -u | wc -l | tr -d ' ')
eq "100 draws are 100 distinct values" "100" "$_uniq"

# ── REGRESSION: the SIGPIPE trap ─────────────────────────────────────────
# `head -c 512 /dev/urandom | tr -dc … | head -c N` looks obvious and is
# wrong: the second head closes the pipe, tr dies on SIGPIPE, and under
# `set -o pipefail` the whole substitution returns non-zero. The symptom is
# not a crash but a SILENT fall-through to the python3 branch — and on a box
# where python3 is somehow unavailable, an EMPTY path.
#
# We prove the primary path is the one that runs by shadowing python3 with a
# stub that emits a marker. If the marker ever shows up, the pipeline broke.
_stub_out=$( python3() { printf 'PYTHON-FALLBACK-USED'; }; random_path 24 )
eq "primary pipeline used, python3 fallback NOT hit" "24" "${#_stub_out}"
case "$_stub_out" in
    *PYTHON-FALLBACK-USED*) bad "no SIGPIPE fallback" "random chars" "$_stub_out" ;;
    *)                      ok  "no SIGPIPE fallback (marker absent)" ;;
esac

# Same, but with the exact shell options godns.sh runs under.
_strict=$( set -euo pipefail; python3() { printf 'PYTHON-FALLBACK-USED'; }; random_path 24 )
eq "survives set -euo pipefail" "24" "${#_strict}"
case "$_strict" in
    *PYTHON-FALLBACK-USED*) bad "no fallback under pipefail" "random chars" "$_strict" ;;
    *)                      ok  "no fallback under pipefail" ;;
esac
eq "rc 0 under set -euo pipefail" "0" \
   "$(set -euo pipefail; random_path 12 >/dev/null; echo $?)"

# ── Секретный путь DoH: длина — это защита, а не вкус ──────────────
# ГРАБЛИ, пойманные случайным вводом на живом сервере: проверялся
# только СОСТАВ символов, и путь из одной буквы принимался молча —
# получался адрес https://домен:4443/n. Точная сверка пути в dnsdist
# есть ЕДИНСТВЕННОЕ, что отличает наш резолвер от открытого: короткий
# путь подбирается за секунды, и резолвером начинают пользоваться
# посторонние. Генератор даёт 12 символов.
succeeds "path: 12 chars accepted"        godns_doh_path_ok "myOwnSecret12"
succeeds "path: exactly the minimum"      godns_doh_path_ok "12345678"
succeeds "path: underscores and dashes"   godns_doh_path_ok "a_b-c/d-e12"
succeeds "path: leading slash tolerated"  godns_doh_path_ok "/myOwnSecret12"
fails    "path: one character refused"    godns_doh_path_ok "n"
fails    "path: one below the minimum"    godns_doh_path_ok "1234567"
fails    "path: empty refused"            godns_doh_path_ok ""
fails    "path: cyrillic refused"         godns_doh_path_ok "путькириллицей"
fails    "path: space refused"            godns_doh_path_ok "my secret 12"
fails    "path: absurdly long refused"    godns_doh_path_ok "$(head -c 100 /dev/zero | tr '\0' 'a')"
eq "path: reason for a short one"  "short" "$(godns_doh_path_ok n || true)"
eq "path: reason for a long one"   "long"  "$(godns_doh_path_ok "$(head -c 100 /dev/zero | tr '\0' 'a')" || true)"
eq "path: reason for bad symbols"  "chars" "$(godns_doh_path_ok 'плохой путь' || true)"


# ═══════════════════════════════════════════════════════════════════════
section "port_in_use / first_free_port (ss stubbed)"
# ═══════════════════════════════════════════════════════════════════════
# Real `ss -Hlnt` output: State Recv-Q Send-Q Local:Address:Port Peer
# (the %lo scope suffix and the [::] form are exactly what a real box prints).
_ss_busy() {
    printf '%s\n' \
      'LISTEN 0 4096 127.0.0.53%lo:53 0.0.0.0:*' \
      'LISTEN 0 511  *:443 *:*' \
      'LISTEN 0 4096 [::]:853 [::]:*' \
      'LISTEN 0 128  10.0.0.7:5353 0.0.0.0:*'
}
# An ADDRESS ending in .53 — the trap a naive `grep :53` falls into.
_ss_lookalike() { printf '%s\n' 'LISTEN 0 128 10.0.0.53:5353 0.0.0.0:*'; }
# `ss` absent (macOS, minimal images): 127 + no output.
_ss_absent() { return 127; }

port_probe() { # port_probe <stub-fn> <port>
    # NB: the stub name must be captured in a variable. Writing
    # `ss() { "$1"; }` would resolve $1 to ss's OWN argument (-Hlnt) at call
    # time, not to the stub we meant. Bash scoping is dynamic, so the local
    # `_stub` is visible from inside ss() when port_in_use calls it.
    local _stub="$1" _port="$2"
    ( ss() { "$_stub"; }; port_in_use "$_port" ) >/dev/null 2>&1
}

rc "53 is busy (IPv4 with %lo scope)" 0 port_probe _ss_busy 53
rc "443 is busy (wildcard *:443)"     0 port_probe _ss_busy 443
rc "853 is busy (IPv6 [::]:853)"      0 port_probe _ss_busy 853
rc "5353 is busy"                     0 port_probe _ss_busy 5353
rc "8443 is free"                     1 port_probe _ss_busy 8443
rc "80 is free"                       1 port_probe _ss_busy 80
rc "address 10.0.0.53 ≠ port 53"      1 port_probe _ss_lookalike 53
rc "10.0.0.53:5353 → 5353 IS busy"    0 port_probe _ss_lookalike 5353
rc "no ss binary → port reads free"   1 port_probe _ss_absent 53

# first_free_port with port_in_use stubbed directly.
_busy_ports() { case "$1" in 53|443|853) return 0 ;; *) return 1 ;; esac; }
ffp() { ( port_in_use() { _busy_ports "$1"; }; first_free_port "$@" ); }

eq "picks the first free candidate" "8443" "$(ffp 53 443 8443 9443 2>/dev/null)"
eq "picks the only free candidate"  "5353" "$(ffp 853 5353 2>/dev/null)"
eq "first candidate already free"   "9999" "$(ffp 9999 53 2>/dev/null)"
eq "all busy → prints nothing"      ""     "$(ffp 53 443 853 2>/dev/null)"

rc "all busy → rc 1"     1 ffp 53 443 853
rc "no candidates → rc 1" 1 ffp

# ═══════════════════════════════════════════════════════════════════════
section "confirm / typed_confirm"
# ═══════════════════════════════════════════════════════════════════════
ask() { # ask <default> <input> — rc of confirm
    confirm "proceed?" "$1" >/dev/null 2>&1 <<< "$2"
}
rc "y            → yes"        0 ask n "y"
rc "Y            → yes"        0 ask n "Y"
rc "yes          → yes"        0 ask n "yes"
rc "YES          → yes"        0 ask n "YES"
rc "д (russian)  → yes"        0 ask n "д"
rc "да (russian) → yes"        0 ask n "да"
rc "ДА (russian) → yes"        0 ask n "ДА"
rc "n            → no"         1 ask y "n"
rc "no           → no"         1 ask y "no"
rc "nonsense     → no"         1 ask y "wat"

# Замер DoH: URL (а в нём — возможно чужой секретный путь) НЕ попадает в argv
# curl. QA поймал на живом сервере: /proc/<pid>/cmdline читает любой, и на
# секунду пробы путь был виден. Поддельный curl пишет argv и stdin в файлы.
_fk="$TEST_DIR/fakecurl"; _fkargv="$TEST_DIR/curl.argv"; _fkcfg="$TEST_DIR/curl.cfg"
cat > "$_fk" <<EOF
#!/usr/bin/env bash
case " \$* " in *" -V "*) echo "curl 8.0.0"; echo "Features: HTTP2 SSL"; exit 0 ;; esac
printf '%s\n' "\$@" > "$_fkargv"
cat > "$_fkcfg"
exit 7
EOF
chmod +x "$_fk"
printf '[{"id":"x","proto":"doh","url":"https://dns.example/qa-secret-9f1","ip":"192.0.2.1"}]' > "$TEST_DIR/cand.json"
GODNS_CURL="$_fk" GODNS_PROBES=2 GODNS_TIMEOUT=1 python3 "$SCRIPT_DIR/lib/bench.py" "$TEST_DIR/cand.json" >/dev/null 2>&1 || true
if [ -s "$_fkargv" ]; then
    if grep -q 'qa-secret-9f1\|https://' "$_fkargv"; then
        bad "bench: URL с путём попал в argv curl" "$(grep -m1 'https://' "$_fkargv")"
    else
        ok "bench: в argv curl нет ни URL, ни пути"
    fi
    grep -qx -- '-K' "$_fkargv" && ok "bench: curl читает конфиг (-K -)" || bad "bench: нет -K"
    [ "$(grep -c '^url = "https://dns.example/qa-secret-9f1' "$_fkcfg")" = "2" ] \
        && ok "bench: оба URL пришли конфигом на stdin" || bad "bench: URL в конфиге" "$(head -3 "$_fkcfg")"
    grep -q '^output = "' "$_fkcfg" && ok "bench: файлы вывода — тоже в конфиге" || bad "bench: output не в конфиге"
else
    bad "bench: поддельный curl не был вызван" "$(ls -l "$TEST_DIR" | head -5)"
fi

# Разрешение имени: адреса отсортированы, первый одинаков от вызова к вызову.
_r1=$(godns_resolve_host localhost | head -1); _r2=$(godns_resolve_host localhost | head -1)
eq "resolve: localhost → 127.0.0.1 первым"     "127.0.0.1" "$_r1"
eq "resolve: порядок стабилен между вызовами"  "$_r1" "$_r2"
_rc=0; godns_resolve_host "" >/dev/null 2>&1 || _rc=$?
eq "resolve: пустое имя — отказ" "1" "$_rc"

# Enter (пустая СТРОКА) берёт умолчание…
rc "Enter, default y → yes"  0 ask y ""
rc "Enter, default n → no"   1 ask n ""

# …а Ctrl-D — это отмена, а не Enter. Раньше эти два жеста были
# неразличимы, и на экране «Изменить порты» выход из мастера проходил все
# вопросы на умолчаниях и перезапускал службу. Отмена значит отмена — при
# ЛЮБОМ умолчании, включая «да».
_c=0; confirm "p" y >/dev/null 2>&1 </dev/null || _c=$?
eq "Ctrl-D при умолчании «да» → отмена" "1" "$_c"
_c=0; confirm "p" n >/dev/null 2>&1 </dev/null || _c=$?
eq "Ctrl-D при умолчании «нет» → отмена" "1" "$_c"
_c=0; confirm "p"   >/dev/null 2>&1 </dev/null || _c=$?
eq "Ctrl-D без умолчания → отмена" "1" "$_c"

# Строка БЕЗ завершающего перевода — это ответ, а не конец ввода: read
# отдаёт код 1, но значение в переменной есть. Приняв её за Ctrl-D, мы
# отвергали бы честное «yes» из `printf yes | godns`.
_c=0; printf 'yes' | confirm "p" n >/dev/null 2>&1 || _c=$?
eq "ответ без перевода строки — это ответ" "0" "$_c"

# …and the prompt is on stderr, so `confirm` is safe next to a $( ).
eq "confirm prompt goes to stderr" "" "$(confirm 'p' y 2>/dev/null <<< 'y')"

_c=0; typed_confirm "DELETE" "type" >/dev/null 2>&1 <<< "DELETE" || _c=$?
eq "typed_confirm exact match → 0"      "0" "$_c"
_c=0; typed_confirm "DELETE" "type" >/dev/null 2>&1 <<< "delete" || _c=$?
eq "typed_confirm wrong case → 1"       "1" "$_c"
_c=0; typed_confirm "DELETE" "type" >/dev/null 2>&1 </dev/null || _c=$?
eq "typed_confirm empty input → 1"      "1" "$_c"
# `read` strips leading/trailing IFS whitespace, so a stray space in front of
# the word is forgiven. That is the desired behaviour — an operator who typed
# the word correctly should not be told he did not.
_c=0; typed_confirm "DELETE" "type" >/dev/null 2>&1 <<< " DELETE " || _c=$?
eq "typed_confirm forgives surrounding spaces" "0" "$_c"
_c=0; typed_confirm "DELETE" "type" >/dev/null 2>&1 <<< "DEL ETE" || _c=$?
eq "typed_confirm rejects an inner space"      "1" "$_c"

# ═══════════════════════════════════════════════════════════════════════
section "temp files"
# ═══════════════════════════════════════════════════════════════════════
_tmp=$(godns_mktemp)
if [ -n "$_tmp" ] && [ -f "$_tmp" ]; then ok "godns_mktemp creates a file"; \
   else bad "godns_mktemp creates a file" "an existing path" "$_tmp"; fi
eq "temp file is 0600 (umask 077)" "600" "$(file_mode "$_tmp")"
rm -f "$_tmp"

# cleanup_temp_files works off the array in the CURRENT shell. NB: calling
# godns_mktemp inside $( ) registers the path in a SUBSHELL, so the parent
# never learns about it — capture-then-rm yourself, as above.
_t1="${TEST_DIR}/tmp-a"; _t2="${TEST_DIR}/tmp-b"
: > "$_t1"; : > "$_t2"
GODNS_TMP_FILES=("$_t1" "$_t2")
succeeds "cleanup_temp_files returns 0" cleanup_temp_files
if [ ! -e "$_t1" ] && [ ! -e "$_t2" ]; then ok "cleanup removed both files"; \
   else bad "cleanup removed both files" "gone" "still there"; fi
GODNS_TMP_FILES=()
rc "cleanup on an empty list still returns 0" 0 cleanup_temp_files

# ═══════════════════════════════════════════════════════════════════════
section "lib/logs.sh — арифметика бюджета"
# ═══════════════════════════════════════════════════════════════════════
# Заказчик просил ГАРАНТИЮ 200 МБ, а не «примерно». Гарантия состоит из двух
# половин, и обе проверяются здесь без root, без сети и без systemd:
#   • арифметика — размер ротации выводится из несжатого слоя, а число
#     поколений из остатка бюджета;
#   • сторож — считает реальные байты и сносит самое старое.
# Комментарию в шапке lib/logs.sh верить нельзя, поэтому он продублирован
# исполняемой проверкой.
#
# NB: logs.sh грузится ПОСЛЕ common.sh и ДО i18n.sh, поэтому здесь работают
# его собственные заглушки t()/tf() — они печатают сам ключ. Все сравнения
# ниже смотрят на число, а не на единицу измерения.
# shellcheck source=lib/logs.sh
. "${SCRIPT_DIR}/lib/logs.sh"

GODNS_LOG_CAP_MB=200; GODNS_LOG_LIVE_MB=150; GODNS_LOG_FILE_MB=35
GODNS_LOG_STREAMS=2;  GODNS_LOG_RATIO=10

eq "размер ротации при умолчаниях"  "35" "$(godns_logs_file_mb)"
eq "поколений при умолчаниях"        "7" "$(godns_logs_rotate_count)"
succeeds "объявленные константы укладываются в потолок" godns_logs_budget_ok

# Несжатый слой = потоки × 2 файла (живой + «.1» под delaycompress) × размер.
_plain=$(( GODNS_LOG_STREAMS * 2 * $(godns_logs_file_mb) ))
if [ "$_plain" -le "$GODNS_LOG_LIVE_MB" ]; then
    ok "несжатое ${_plain} МБ ≤ обещанных ${GODNS_LOG_LIVE_MB} МБ"
else
    bad "несжатый слой" "≤ ${GODNS_LOG_LIVE_MB}" "$_plain"
fi
_arch=$(( GODNS_LOG_STREAMS * ($(godns_logs_rotate_count) - 1) * $(godns_logs_file_mb) / GODNS_LOG_RATIO ))
if [ "$(( _plain + _arch ))" -le "$GODNS_LOG_CAP_MB" ]; then
    ok "всего ожидаемо $(( _plain + _arch )) МБ ≤ потолка ${GODNS_LOG_CAP_MB} МБ"
else
    bad "суммарный бюджет" "≤ ${GODNS_LOG_CAP_MB}" "$(( _plain + _arch ))"
fi

# Оператор поднял размер файла выше того, что вмещает несжатый слой.
# Константа обязана быть УРЕЗАНА, иначе «150 МБ несжатых» ломается молча.
GODNS_LOG_FILE_MB=500
eq "завышенный размер файла урезан по несжатому слою" "37" "$(godns_logs_file_mb)"
succeeds "после урезания бюджет всё ещё сходится" godns_logs_budget_ok
GODNS_LOG_FILE_MB=0
eq "нулевой размер файла не проходит"  "37" "$(godns_logs_file_mb)"
GODNS_LOG_FILE_MB=abc
# Мусор — это не «поднять до потолка», а «взять проектное умолчание».
eq "мусор вместо размера → умолчание 35"  "35" "$(godns_logs_file_mb)"
GODNS_LOG_FILE_MB=35

# Вырожденные настройки: потолок не больше несжатого слоя — архиву места нет.
GODNS_LOG_CAP_MB=150
eq "нет места под архив → минимум поколений" "3" "$(godns_logs_rotate_count)"
GODNS_LOG_CAP_MB=200

# ═══════════════════════════════════════════════════════════════════════
section "lib/logs.sh — измерение и форматирование"
# ═══════════════════════════════════════════════════════════════════════
LOGDIR="${TEST_DIR}/varlog"
mkdir -p "$LOGDIR"
GODNS_LOG_DIR="$LOGDIR"
GODNS_LOG_STATE="${TEST_DIR}/logbudget.state"

eq "пустой каталог — ноль байт"   "0" "$(godns_logs_bytes "$LOGDIR")"
eq "пустой каталог — ноль файлов" "0" "$(godns_logs_count "$LOGDIR")"
eq "несуществующий каталог не падает" "0" "$(godns_logs_bytes "${TEST_DIR}/nope")"

# 1000 + 2000 + 3000 байт: два .log и один .gz.
_mk() { dd if=/dev/zero of="$1" bs=1 count="$2" >/dev/null 2>&1; }
_mk "${LOGDIR}/dnsdist.log" 1000
_mk "${LOGDIR}/godns.log"   2000
_mk "${LOGDIR}/dnsdist.log.2.gz" 3000
eq "сумма по всему каталогу"      "6000" "$(godns_logs_bytes "$LOGDIR")"
eq "сумма по маске *.gz"          "3000" "$(godns_logs_bytes "$LOGDIR" '*.gz')"
eq "сумма по маске *.log"         "3000" "$(godns_logs_bytes "$LOGDIR" '*.log')"
eq "файлов всего"                    "3" "$(godns_logs_count "$LOGDIR")"
eq "файлов сжатых"                   "1" "$(godns_logs_count "$LOGDIR" '*.gz')"

# Человекочитаемый размер: смотрим на число, единица подставится i18n.
eq "0 байт"        "0"     "$(godns_logs_human 0 | awk '{print $1}')"
eq "1023 байта"    "1023"  "$(godns_logs_human 1023 | awk '{print $1}')"
eq "1.5 КБ"        "1.5"   "$(godns_logs_human 1536 | awk '{print $1}')"
eq "12.5 МБ"       "12.5"  "$(godns_logs_human 13107200 | awk '{print $1}')"
eq "2.0 ГБ"        "2.0"   "$(godns_logs_human 2147483648 | awk '{print $1}')"
eq "мусор вместо числа → 0" "0" "$(godns_logs_human "abc" | awk '{print $1}')"

# df может не ответить — тогда ПУСТО, а не «0 МБ свободно». Иначе аварийный
# режим включался бы на здоровой машине при каждом сбое df.
_free=$(godns_logs_free_mb)
case "$_free" in
    ''|*[!0-9]*) bad "свободное место — число или пусто" "цифры или пусто" "$_free" ;;
    *)           ok  "свободное место прочиталось: ${_free} МБ" ;;
esac

# ═══════════════════════════════════════════════════════════════════════
section "lib/logs.sh — конфиг ротации"
# ═══════════════════════════════════════════════════════════════════════
LR=$(godns_logs_render_logrotate)
_has() { if printf '%s\n' "$LR" | grep -q -- "$1"; then ok "конфиг: $2"; else bad "конфиг: $2" "$1" "нет"; fi; }
_hasnt() { if printf '%s\n' "$LR" | grep -q -- "$1"; then bad "конфиг: $2" "нет $1" "есть"; else ok "конфиг: $2"; fi; }

_has "^${LOGDIR}/\*\.log {"  "блок покрывает только наш каталог"
_has "size 35M"              "ротация по размеру"
_has "rotate 7"              "семь поколений"
_has "compress"              "сжатие включено"
_has "delaycompress"         "свежее поколение остаётся несжатым"
_has "copytruncate"          "copytruncate — дескриптор держит systemd"
_has "notifempty"            "notifempty"
_has "missingok"             "missingok"
_has "nocreate"              "nocreate (файл создаёт systemd)"
_has "su root"               "su задан"
_has "lastaction"            "сторож вызывается сразу после ротации"
# maxsize под `size` бессмыслен, а вместе с daily возвращает состояние и
# двойную ротацию (наш часовой таймер + штатный суточный logrotate).
_hasnt "compressoptions"     "gzip зовём с умолчаниями (лишняя директива = разбор всего файла падает)"
_hasnt "maxsize"             "maxsize не задан (решение по размеру — без состояния)"
_hasnt "daily"               "нет временного интервала — только размер"
_hasnt "/var/log/nginx"      "чужих путей в нашем конфиге нет"
_hasnt "/var/log/journal"    "журнала systemd в нашем конфиге нет"

# Скобки должны сойтись, иначе logrotate молча пропустит весь файл.
_open=$(printf '%s\n' "$LR" | grep -c '{')
_close=$(printf '%s\n' "$LR" | grep -c '}')
eq "скобки блока сошлись" "$(printf '%s' "$_open" | tr -d ' ')" "$(printf '%s' "$_close" | tr -d ' ')"

# ═══════════════════════════════════════════════════════════════════════
section "lib/logs.sh — сторож (слой 3)"
# ═══════════════════════════════════════════════════════════════════════
# Порядок сноса — не деталь реализации, а обещание:
#   живой *.log не трогаем НИКОГДА (обрезать под открытым дескриптором —
#   это дыра в логе), «.1» не жмём (он и есть несжатый слой), архивы
#   сносим от самых старых.
rm -f "${LOGDIR}"/* 2>/dev/null
_mk "${LOGDIR}/dnsdist.log"   4000    # живой
_mk "${LOGDIR}/dnsdist.log.1" 4000    # несжатый слой (delaycompress)
_mk "${LOGDIR}/dnsdist.log.3" 4000    # ротированное, но не дожатое
for _i in 2 4 5; do
    _mk "${LOGDIR}/dnsdist.log.${_i}.gz" 4000
done
# Возраст: .5.gz самый старый, .2.gz самый свежий.
touch -t 202001010000 "${LOGDIR}/dnsdist.log.5.gz"
touch -t 202002010000 "${LOGDIR}/dnsdist.log.4.gz"
touch -t 202003010000 "${LOGDIR}/dnsdist.log.2.gz"

_before=$(godns_logs_bytes "$LOGDIR")
_res=$(godns_logs_trim 9000 "$LOGDIR")
_after=$(godns_logs_bytes "$LOGDIR")

if [ "$_after" -le 9000 ]; then
    ok "сторож уложился в бюджет: ${_before} → ${_after} байт"
else
    bad "сторож уложился в бюджет" "≤ 9000" "$_after"
fi
if [ -f "${LOGDIR}/dnsdist.log" ]; then ok "живой файл не тронут"; \
   else bad "живой файл не тронут" "существует" "удалён"; fi
eq "живой файл не обрезан" "4000" "$(godns_logs_bytes "$LOGDIR" 'dnsdist.log')"
if [ -f "${LOGDIR}/dnsdist.log.1" ]; then ok "«.1» остался несжатым"; \
   else bad "«.1» остался несжатым" "есть .1" "сжат или удалён"; fi
if [ -f "${LOGDIR}/dnsdist.log.3.gz" ] || [ ! -f "${LOGDIR}/dnsdist.log.3" ]; then
    ok "недожатое поколение дожато"
else
    bad "недожатое поколение дожато" ".3.gz" ".3"
fi
if [ ! -f "${LOGDIR}/dnsdist.log.5.gz" ]; then ok "самый старый архив снесён первым"; \
   else bad "самый старый архив снесён первым" "нет .5.gz" "есть"; fi
eq "сторож отчитался «дожато удалено»" "2" "$(printf '%s' "$_res" | wc -w | tr -d ' ')"

# В бюджете — не трогаем ничего.
_res=$(godns_logs_trim 999999999 "$LOGDIR")
eq "в бюджете — ноль действий" "0 0" "$_res"

# Полный прогон: превышение → уборка. Аварийный порог свободного места
# отключаем, иначе на маленьком диске разработчика тест стал бы плавающим.
GODNS_LOG_KEEP_FREE_MB=0
GODNS_LOG_CAP_MB=1
rm -f "${LOGDIR}"/* 2>/dev/null
_mk "${LOGDIR}/dnsdist.log" 2000
for _i in 2 3 4; do _mk "${LOGDIR}/dnsdist.log.${_i}.gz" 700000; done
succeeds "godns_logs_enforce возвращает 0" godns_logs_enforce
if [ "$GODNS_LOGS_AFTER" -le $(( 1 * 1024 * 1024 )) ]; then
    ok "после уборки уложились в потолок: ${GODNS_LOGS_BEFORE} → ${GODNS_LOGS_AFTER}"
else
    bad "после уборки уложились в потолок" "≤ 1 МБ" "$GODNS_LOGS_AFTER"
fi
if [ -f "${LOGDIR}/dnsdist.log" ]; then ok "живой файл пережил enforce"; \
   else bad "живой файл пережил enforce" "существует" "удалён"; fi
if [ -s "$GODNS_LOG_STATE" ]; then ok "сторож записал отчёт о прогоне"; \
   else bad "сторож записал отчёт" "непустой файл" "пусто"; fi
GODNS_LOG_CAP_MB=200
GODNS_LOG_KEEP_FREE_MB=1024

# Ротация посреди замера обнуляет счётчик строк. Отдать отрицательное число
# нельзя: оценка «на сколько хватит» уехала бы в бесконечность.
printf 'a\nb\nc\n' > "${TEST_DIR}/rate.log"
( sleep 1; printf 'x\n' > "${TEST_DIR}/rate.log" ) &
eq "ротация во время замера → 0, а не минус" "0" \
   "$(godns_logs_measure_rate 2 "${TEST_DIR}/rate.log")"
wait 2>/dev/null
fails "замер несуществующего файла отказывает" godns_logs_measure_rate 1 "${TEST_DIR}/no-such.log"

rm -rf "$LOGDIR"
GODNS_LOG_DIR="/var/log/godns"

# ═══════════════════════════════════════════════════════════════════════
section "lib/stats.sh — окна статистики: три ловушки и размер файла"
# ═══════════════════════════════════════════════════════════════════════
# Арифметика окон живёт в lib/stats.py; здесь она гоняется через bash-API
# без root, systemd и dnsdist: срезы подаются руками (godns_stats_ingest),
# а «сейчас» подменяется крюком GODNS_STATS_NOW. Наивное вычитание срезов
# врёт трижды — перезапуск службы обнуляет счётчики, перезагрузка сервера
# оставляет дыру, файл растёт, — и каждая ловушка проверяется отдельно.
# shellcheck source=lib/stats.sh
. "${SCRIPT_DIR}/lib/stats.sh"

GODNS_STATS_DIR="${TEST_DIR}/varlib"
GODNS_STATS_FILE="${GODNS_STATS_DIR}/stats.tsv"

# Отметка окна из отчёта: full / approx / none.
_st_mark() { python3 "$GODNS_STATS_PY" report "$GODNS_STATS_FILE" --now "$1" 2>/dev/null \
    | awk -F'\t' -v w="$2" '$1 == "w" && $2 == w { print $5 }'; }

if ! command -v python3 >/dev/null 2>&1; then
    skip "python3 недоступен — статистика не проверяется"
else
    T0=1700000000

    # «Нет данных» и «ноль» — разные ответы: прочерк, а не 0.
    fails "окно без файла отказывает (rc≠0)" godns_stats_window 1h
    eq "окно без файла молчит, а не печатает 0" "" "$(godns_stats_window 1h 2>/dev/null)"
    fails "неизвестное окно отвергается" godns_stats_window 3h
    fails "мусор вместо среза отвергается" godns_stats_ingest abc AAA 100 0 0

    succeeds "первый срез принят (точка отсчёта)" godns_stats_ingest "$T0" AAA 100 10 0
    GODNS_STATS_NOW=$((T0 + 5))
    fails "после одного среза окно всё ещё «нет данных»" godns_stats_window 1m
    eq "файл срезов создан с правами 0640" "640" "$(file_mode "$GODNS_STATS_FILE")"

    succeeds "второй срез" godns_stats_ingest $((T0 + 60)) AAA 160 20 1
    GODNS_STATS_NOW=$((T0 + 65))
    eq "1m = дельта последнего среза" "60" "$(godns_stats_window 1m)"
    eq "15m = сумма дельт"            "60" "$(godns_stats_window 15m)"

    # Ловушка 1: перезапуск службы обнуляет счётчики. 160 → 40 при СМЕНЕ
    # метки старта — это 40 запросов с нуля, а не «−120».
    succeeds "срез после перезапуска службы" godns_stats_ingest $((T0 + 120)) BBB 40 5 0
    GODNS_STATS_NOW=$((T0 + 125))
    eq "перезапуск: дельта = текущее значение, не минус" "40" "$(godns_stats_window 1m)"
    eq "15m сложило обе стороны перезапуска" "100" "$(godns_stats_window 15m)"

    # Счётчик упал БЕЗ смены метки — это не объяснить перезапуском:
    # значение берётся, но срез помечается разрывом.
    succeeds "аномальный откат счётчика принят" godns_stats_ingest $((T0 + 180)) BBB 10 0 0
    _ts=$((T0 + 180))
    eq "аномалия помечена разрывом" "1" \
       "$(awk -F'\t' -v ts=$((_ts - _ts % 60)) '$1 == "M" && $2 == ts { print $7 }' "$GODNS_STATS_FILE")"
    GODNS_STATS_NOW=$((T0 + 185))
    eq "окно с аномалией внутри — приблизительное" "approx" "$(_st_mark $((T0 + 185)) 15m)"

    # Ловушка 2: перезагрузка сервера — дыра в срезах. Четыре часа тишины
    # не превращаются ни в «ноль запросов», ни в разность через дыру:
    # срез после дыры несёт флаг разрыва, окно с дырой — «≈».
    _hole=$((T0 + 180 + 4 * 3600))
    succeeds "срез после четырёхчасовой дыры" godns_stats_ingest "$_hole" CCC 90 0 0
    eq "срез после дыры помечен разрывом" "1" \
       "$(awk -F'\t' -v ts=$((_hole - _hole % 60)) '$1 == "M" && $2 == ts { print $7 }' "$GODNS_STATS_FILE")"
    GODNS_STATS_NOW=$((_hole + 5))
    eq "свежий срез после дыры виден в 1m" "90" "$(godns_stats_window 1m)"
    eq "1d с дырой внутри — приблизительное, но считает" "approx" "$(_st_mark $((_hole + 5)) 1d)"
    eq "1d = только настоящие дельты, без выдуманных нулей" "200" "$(godns_stats_window 1d)"

    # Спарклайн: дыра рисуется точкой, а не нулевым столбиком.
    _spark=$(GODNS_STATS_NOW=$((_hole + 5)) godns_stats_sparkline 30 16)
    case "$_spark" in
        *·*) ok "спарклайн показывает дыру точкой" ;;
        *)   bad "спарклайн показывает дыру точкой" "символ ·" "$_spark" ;;
    esac
    case "$_spark" in
        *█*) ok "спарклайн масштабирует максимум в полный столбик" ;;
        *)   bad "спарклайн масштабирует максимум" "символ █" "$_spark" ;;
    esac

    # Ловушка 3: файл не растёт. Неделя поминутных срезов (10080) — и
    # поминутный слой обязан свернуться в часовые корзины: ≤1441 M-строк
    # за сутки + ≤169 H-строк за неделю. Свёртка не теряет ни запроса:
    # сумма дельт в файле равна разности крайних значений счётчика.
    rm -f "$GODNS_STATS_FILE"
    python3 -c "
t0 = $T0
q = h = f = 0
for i in range(10080):
    q += (i * 37) % 120
    h += (i * 11) % 30
    f += i % 2
    print(t0 + i * 60, 'AAA', q, h, f)
" | python3 "$GODNS_STATS_PY" ingest "$GODNS_STATS_FILE"
    _m=$(awk -F'\t' '$1 == "M" { n++ } END { print n + 0 }' "$GODNS_STATS_FILE")
    _h=$(awk -F'\t' '$1 == "H" { n++ } END { print n + 0 }' "$GODNS_STATS_FILE")
    if [ "$_m" -le 1441 ]; then ok "поминутный слой ограничен сутками (${_m} строк)"; \
       else bad "поминутный слой ограничен сутками" "≤ 1441" "$_m"; fi
    if [ "$_h" -ge 24 ] && [ "$_h" -le 169 ]; then ok "часовые корзины за неделю (${_h} строк)"; \
       else bad "часовые корзины за неделю" "24…169" "$_h"; fi
    _sum=$(awk -F'\t' '$1 == "M" || $1 == "H" { s += $4 } END { print s + 0 }' "$GODNS_STATS_FILE")
    _expect=$(python3 -c "
q = 0; first = None
for i in range(10080):
    q += (i * 37) % 120
    if first is None: first = q
print(q - first)
")
    eq "свёртка не потеряла ни одного запроса" "$_expect" "$_sum"
    _lines=$(wc -l < "$GODNS_STATS_FILE" | tr -d ' ')
    if [ "$_lines" -le 1700 ]; then ok "после недели срезов в файле ${_lines} строк"; \
       else bad "размер файла фиксирован" "≤ 1700 строк" "$_lines"; fi

    # Ещё сутки поверх — размер обязан остаться тем же, а не подрасти.
    # База счётчика ВЫШЕ конца первой партии (≈599 760): иначе это было бы
    # «падение счётчика», срез пометился бы разрывом, и проверка полного
    # окна ниже мерила бы уже не непрерывную неделю.
    python3 -c "
t0 = $T0 + 10080 * 60
q, h, f = 700000, 150000, 5100
for i in range(1440):
    q += (i * 37) % 120
    h += (i * 11) % 30
    f += i % 2
    print(t0 + i * 60, 'AAA', q, h, f)
" | python3 "$GODNS_STATS_PY" ingest "$GODNS_STATS_FILE"
    eq "ещё сутки срезов не изменили размер файла" "$_lines" \
       "$(wc -l < "$GODNS_STATS_FILE" | tr -d ' ')"

    # Полные окна на непрерывной истории — без «≈».
    _now=$((T0 + 10080 * 60 + 1439 * 60 + 5))
    eq "непрерывная неделя даёт полное окно 7d" "full" "$(_st_mark "$_now" 7d)"

    # Повтор того же среза не плодит строк (идемпотентность по минуте).
    _before=$(wc -l < "$GODNS_STATS_FILE" | tr -d ' ')
    succeeds "повторный срез той же секунды принят" \
        godns_stats_ingest $((T0 + 10080 * 60 + 1439 * 60)) AAA 700000 200000 6000
    eq "повтор не изменил число строк" "$_before" \
       "$(wc -l < "$GODNS_STATS_FILE" | tr -d ' ')"

    # systemd-юниты сборщика: рендер чистый, проверяем содержимое.
    _svc=$(_stats_render_service)
    case "$_svc" in
        *"godns_stats_sample"*) ok "юнит службы зовёт godns_stats_sample" ;;
        *) bad "юнит службы зовёт godns_stats_sample" "вызов в ExecStart" "$_svc" ;;
    esac
    case "$_svc" in
        *"ConditionPathExists="*) ok "юнит службы защищён ConditionPathExists" ;;
        *) bad "юнит службы защищён ConditionPathExists" "условие" "$_svc" ;;
    esac
    _tmr=$(_stats_render_timer)
    case "$_tmr" in
        *"OnCalendar="*) ok "таймер идёт по календарной минуте" ;;
        *) bad "таймер идёт по календарной минуте" "OnCalendar" "$_tmr" ;;
    esac

    unset GODNS_STATS_NOW
fi

# ═══════════════════════════════════════════════════════════════════════
section "lib/i18n.sh — bash 4.0+ guard"
# ═══════════════════════════════════════════════════════════════════════
# This one is testable on ANY bash: on 3.2 the guard must fire, on 5.x it
# must not. Getting this wrong is not a crash — on 3.2 every I18N[key]
# collapses into index 0 and the whole UI shows one repeated phrase.
if [ "${BASH_VERSINFO[0]}" -lt 4 ]; then
    # Именно $BASH (тот, что исполняет тест, /bin/bash 3.2), а не `bash` из
    # PATH: с Homebrew там bash 5, страж честно не срабатывает, и тест
    # краснел на исправном коде.
    _guard_out=$("$BASH" -c ". ${SCRIPT_DIR}/lib/i18n.sh" 2>&1)
    _guard_rc=$?
    eq "guard exits non-zero on bash 3.2" "1" "$_guard_rc"
    case "$_guard_out" in
        *"bash 4.0"*) ok "guard message names the required version" ;;
        *) bad "guard message names the required version" "mentions bash 4.0" "$_guard_out" ;;
    esac
    case "$_guard_out" in
        *"нужен"*) ok "guard message is bilingual (ru half present)" ;;
        *) bad "guard message is bilingual (ru half present)" "russian text" "$_guard_out" ;;
    esac
    case "$_guard_out" in
        *"required"*) ok "guard message is bilingual (en half present)" ;;
        *) bad "guard message is bilingual (en half present)" "english text" "$_guard_out" ;;
    esac
else
    ok "running on bash ${BASH_VERSION} — guard must stay quiet"
fi

# ═══════════════════════════════════════════════════════════════════════
section "lib/i18n.sh — engine"
# ═══════════════════════════════════════════════════════════════════════
if [ "${BASH_VERSINFO[0]}" -lt 4 ]; then
    skip "skipped — associative arrays need bash 4.0+, this is ${BASH_VERSION}"
    skip "  run on the Ubuntu target, or: brew install bash && /opt/homebrew/bin/bash $0"
else
    # A private locale dir so the test does not depend on the real strings,
    # which other agents are still writing.
    LANGDIR="${TEST_DIR}/lang"
    mkdir -p "$LANGDIR"
    cat > "${LANGDIR}/ru.sh" <<'EOF'
I18N[back]="Назад"
I18N[cert_found]="Сертификат найден: %s"
I18N[bench_two]="%s → %s мс"
I18N[spacer]=""
EOF
    cat > "${LANGDIR}/en.sh" <<'EOF'
I18N[back]="Back"
I18N[cert_found]="Certificate found: %s"
I18N[bench_two]="%s → %s ms"
I18N[spacer]=""
EOF
    export GODNS_LANG_DIR="$LANGDIR"

    # shellcheck source=lib/i18n.sh
    . "${SCRIPT_DIR}/lib/i18n.sh"

    succeeds "load_language ru"          load_language ru
    eq "t returns the russian string"    "Назад"  "$(t back)"
    eq "get_language reports ru"         "ru"     "$(get_language)"
    succeeds "load_language en"          load_language en
    eq "t returns the english string"    "Back"   "$(t back)"
    eq "get_language reports en"         "en"     "$(get_language)"

    # THE contract: a missing key prints itself, never a blank line.
    eq "missing key prints the key"      "certs_no_such_key" "$(t certs_no_such_key)"
    eq "a key defined as empty stays empty" "" "$(t spacer)"
    eq "t with no argument → empty line" ""   "$(t)"

    eq "tf substitutes one arg"  "Certificate found: dns.example.com" \
       "$(tf cert_found dns.example.com)"
    eq "tf substitutes two args" "1.1.1.1 → 12 ms" "$(tf bench_two 1.1.1.1 12)"
    eq "tf on a missing key shows key + args" "eng_nope a b" "$(tf eng_nope a b)"
    eq "tf on a missing key with no args"     "eng_nope"     "$(tf eng_nope)"

    # Unknown language must fall back to RUSSIAN, not English.
    succeeds "load_language xx falls back" load_language xx
    eq "unknown language → ru (project default)" "Назад" "$(t back)"
    eq "…and reports ru"                         "ru"    "$(get_language)"
    load_language "" >/dev/null 2>&1
    # ГРАБЛИ: здесь сравнивался `t back` со строкой «ru» — то есть перевод
    # с кодом языка. На macOS вся секция пропускается (нужен bash 4), и
    # ошибка жила незамеченной: на цели тест валился, хотя код работает
    # правильно. Проверяем обе величины по отдельности.
    eq "empty language → ru (строка)"            "Назад" "$(t back)"
    eq "empty language → ru (код)"               "ru"    "$(get_language)"

    # No locale files at all: the engine must degrade, not die.
    _empty="${TEST_DIR}/lang-empty"; mkdir -p "$_empty"
    _out=$( GODNS_LANG_DIR="$_empty" bash -c \
        ". ${SCRIPT_DIR}/lib/i18n.sh; load_language ru; t back; echo rc=\$?" 2>/dev/null )
    case "$_out" in
        back*) ok "no locale files → t prints the key, engine survives" ;;
        *)     bad "no locale files → t prints the key" "back…" "$_out" ;;
    esac

    # detect_language: config.json first, then $GODNS_DIR/.language, then the
    # neighbours. Point the neighbour markers at scratch paths — otherwise
    # running this suite ON a server that already has goVLESS installed would
    # pick up its language and the assertions would flap.
    export GOVLESS_LANG_MARKER="${TEST_DIR}/neighbour-govless"
    export GOTELEGRAM_LANG_MARKER="${TEST_DIR}/neighbour-gotelegram"
    rm -f "$GOVLESS_LANG_MARKER" "$GOTELEGRAM_LANG_MARKER"

    rm -f "$GODNS_CONFIG" "${GODNS_DIR}/.language"
    eq "nothing saved anywhere → ru" "ru" "$(detect_language)"
    printf 'en\n' > "${GODNS_DIR}/.language"
    eq "reads the .language marker"  "en" "$(detect_language)"
    printf '  en \n' > "${GODNS_DIR}/.language"
    eq "marker is whitespace-tolerant" "en" "$(detect_language)"
    printf 'EN\n' > "${GODNS_DIR}/.language"
    eq "uppercase code is not accepted → ru" "ru" "$(detect_language)"
    printf 'english\n' > "${GODNS_DIR}/.language"
    eq "long word truncated to 'en'" "en" "$(detect_language)"

    printf 'en\n' > "${GODNS_DIR}/.language"
    config_set language ru >/dev/null 2>&1
    eq "config.json wins over the marker" "ru" "$(detect_language)"
    # A bad value in config.json is still an ANSWER — the marker is not
    # consulted — so it lands on the project default rather than on en.
    config_set language zz >/dev/null 2>&1
    eq "unknown code in config → ru (project default)" "ru" "$(detect_language)"
    rm -f "$GODNS_CONFIG"
    eq "marker used again once config is gone" "en" "$(detect_language)"

    # Neighbour inheritance — do not ask the operator twice.
    rm -f "${GODNS_DIR}/.language"
    printf 'en\n' > "$GOVLESS_LANG_MARKER"
    eq "inherits goVLESS choice"      "en" "$(detect_language)"
    rm -f "$GOVLESS_LANG_MARKER"
    printf 'en\n' > "$GOTELEGRAM_LANG_MARKER"
    eq "inherits goTelegram choice"   "en" "$(detect_language)"
    rm -f "$GOTELEGRAM_LANG_MARKER"

    # save_language writes both places and refuses nonsense.
    rm -f "$GODNS_CONFIG" "${GODNS_DIR}/.language"
    succeeds "save_language ru" save_language ru
    eq "marker written"        "ru" "$(cat "${GODNS_DIR}/.language" 2>/dev/null)"
    eq "config written"        "ru" "$(config_get language)"
    fails    "save_language rejects 'de'" save_language de
    fails    "save_language rejects empty" save_language ""
    eq "rejected value did not overwrite" "ru" "$(config_get language)"
    # ГРАБЛИ: маркер наследовал 0600 root, а бот и панель работают под
    # своими пользователями. В журнале бота стояло «cannot read
    # .language: Permission denied», и выбранный язык до него не доходил:
    # бот молча говорил на языке по умолчанию. Отказ немой — закрепляем.
    _lang_mode=$(file_mode "${GODNS_DIR}/.language") || _lang_mode=""
    eq "language marker is world-readable (bot runs as its own user)" "644" "$_lang_mode"

    # pick_language_interactive: prompt on stderr, code on stdout, ru default.
    eq "picker: 1 → ru" "ru" "$(pick_language_interactive 2>/dev/null <<< '1')"
    eq "picker: 2 → en" "en" "$(pick_language_interactive 2>/dev/null <<< '2')"
    eq "picker: 'ru' → ru" "ru" "$(pick_language_interactive 2>/dev/null <<< 'ru')"
    eq "picker: garbage → ru (project default)" "ru" \
       "$(pick_language_interactive 2>/dev/null <<< 'zzz')"
    eq "picker: empty → ru" "ru" "$(pick_language_interactive 2>/dev/null </dev/null)"

    load_language ru >/dev/null 2>&1
fi

section "config_set: параллельные писатели не теряют ключей"
# ГРАБЛИ по построению: писателей три — SSH-меню, демон бота (root) и
# панель (root), все через один config_set. Атомарный os.replace защищал
# файл от порчи, но не от потерянного обновления: двое читают один снимок,
# каждый пишет свой ключ, второй replace затирает первый. Оператор менял
# апстримы в панели, пока бот крутил путь DoH, — и пропадал либо новый
# путь, либо половина пары upstream_ids/upstreams. Теперь запись под
# fcntl.flock на файле-соседе (сам config.json заменяется, и замок на нём
# держал бы старый inode).
rm -f "$GODNS_CONFIG" "${GODNS_CONFIG}.lock"
printf '{}' > "$GODNS_CONFIG"
_cw_n=24
for _i in $(seq 1 "$_cw_n"); do
    config_set "cw_key_${_i}" "v${_i}" &
done
wait
_cw_have=$(python3 -c "
import json,sys
d=json.load(open('$GODNS_CONFIG'))
print(sum(1 for k in d if k.startswith('cw_key_')))")
eq "все ${_cw_n} параллельных ключа выжили" "$_cw_n" "$_cw_have"
_cw_valid=$(python3 -c "
import json
try:
    json.load(open('$GODNS_CONFIG')); print('да')
except Exception: print('нет')")
eq "конфиг после гонки — валидный JSON" "да" "$_cw_valid"
rm -f "$GODNS_CONFIG" "${GODNS_CONFIG}.lock"

section "сосед на диске меняет выбор порта"
# ГРАБЛИ: «порт свободен» и «порт останется свободным» — разные
# утверждения. goTelegram занимает :443 только после настройки прокси, а
# до того порт пуст. Мастер это видел, брал 443 и обещал, что «DoH не
# отличить от HTTPS»; предупреждал при этом только про goVLESS. Через день
# соседа настраивали — и либо он не поднимался, либо резолвер отваливался
# у всех клиентов разом.
_nb_root="${TEST_DIR}/nb"
mkdir -p "$_nb_root"
GOVLESS_DIR="${_nb_root}/govless"
GOTELEGRAM_DIR="${_nb_root}/gotelegram"

rm -rf "$GOVLESS_DIR" "$GOTELEGRAM_DIR"
if godns_neighbour_present >/dev/null 2>&1; then
    eq "чистая машина: соседей нет" "нет" "нашёл: $(godns_neighbour_present)"
else
    eq "чистая машина: соседей нет" "нет" "нет"
fi

mkdir -p "$GOTELEGRAM_DIR"
eq "goTelegram виден"              "goTelegram" "$(godns_neighbour_present)"
mkdir -p "$GOVLESS_DIR"
eq "видны оба, goVLESS первым"     "goVLESS, goTelegram" "$(godns_neighbour_present)"
rm -rf "$GOTELEGRAM_DIR"
eq "остался один goVLESS"          "goVLESS" "$(godns_neighbour_present)"
rm -rf "$GOVLESS_DIR"
unset GOVLESS_DIR GOTELEGRAM_DIR

section "порты соседа не предлагаются, даже когда свободны"
# ГРАБЛИ на два уровня. (1) Мастер смотрел только «занят ли порт СЕЙЧАС»,
# а goTelegram занимает :443 своим nginx лишь после настройки прокси — на
# машине с установленным, но не настроенным соседом мастер брал 443.
# (2) Первая правка это исправила и стала предлагать 8443 — порт САЙТА
# goTelegram. Логику «числится за кем-то по построению» умела панель и не
# умел мастер; теперь она одна на обоих.
port_in_use() { return 1; }   # ничего не занято ПРЯМО СЕЙЧАС
for _p in 443 853 1984 7443 8443 9090 9091 9443 80; do
    if godns_port_reserved "$_p"; then :; else
        eq "порт $_p числится за соседом" "да" "нет"
    fi
done
eq "порты соседей отбиты (9 шт.)" "ok" "ok"
eq "4443 свободен для нас"        ""   "$(godns_port_reserved 4443 && echo занят)"
eq "8444 свободен для нас"        ""   "$(godns_port_reserved 8444 && echo занят)"
eq "выбор при соседе — не порт соседа" "4443" \
   "$(godns_first_free_port 4443 8444 9444 5443 7444)"
eq "8443 пропускается, даже если первый" "8444" \
   "$(godns_first_free_port 8443 8444)"
unset -f port_in_use

section "lib/selftest.sh — выбор транспорта"
# ГРАБЛИ: st_pick_transport выбирал DoT по условию «ST_DOT_PORT непуст», а
# он непуст ВСЕГДА — selftest_load_ctx подставляет 853 по умолчанию, ведь
# порт хранится в конфиге и при выключенном DoT. На установке по умолчанию
# (DoT выключен — это дефолт мастера) обе проверки DNSSEC били в :853, где
# никто не слушает, и печатали приговор «резолвер просто мёртв» при
# исправном резолвере: DoH в том же прогоне отвечал.
#
# Это ещё и первый тест, который вообще ИСПОЛНЯЕТ lib/selftest.sh: модуль
# на 1900 строк, который сам судит о живой системе, не был подключён ни
# одним набором.
if source "${SCRIPT_DIR}/lib/selftest.sh" >/dev/null 2>&1; then
    st_have() { [ "$1" = kdig ]; }
    ST_DOT_PORT=853; ST_DOH_PORT=443; ST_DOH_PATH=abcdefghij

    GODNS_DOT_ENABLED=no
    st_pick_transport
    eq "DoT выключен — идём по DoH"      "doh" "$ST_QTRANSPORT"

    GODNS_DOT_ENABLED=yes
    st_pick_transport
    eq "DoT включён — идём по DoT"       "dot" "$ST_QTRANSPORT"

    # Без DoH-пути и с выключенным DoT транспорта нет вовсе — это отказ,
    # а не молчаливый выбор наугад.
    GODNS_DOT_ENABLED=no; ST_DOH_PATH=""
    if st_pick_transport; then
        eq "без пути DoH транспорта нет" "отказ" "выбрал ${ST_QTRANSPORT}"
    else
        eq "без пути DoH транспорта нет" "отказ" "отказ"
    fi
    unset -f st_have
    unset GODNS_DOT_ENABLED
else
    eq "lib/selftest.sh подключается"    "да" "нет"
fi

# ═══════════════════════════════════════════════════════════════════════
printf "\n────────────────────────────────────────────────────────────\n"
if [ "$FAIL" -eq 0 ]; then
    printf "  \033[0;32m✓ all %d checks passed\033[0m\n\n" "$PASS"
    exit 0
fi
printf "  \033[0;31m✗ %d failed\033[0m, %d passed\n\n" "$FAIL" "$PASS"
exit 1
