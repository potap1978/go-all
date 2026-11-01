#!/usr/bin/env bash
# Certificates for tests/site (OpenSSL 3.4+: x509 -not_before/-not_after). Not secrets: test keys only.
# A «Let's Encrypt» CA R11, a «staging» CA, and leaves with fixed dates (the tests patch the clock).
set -eu
cd "$(dirname "$0")"
O=${OPENSSL:-openssl}
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
ca() {   # name CN O
    $O genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out "$T/$1.key" 2>/dev/null
    $O req -x509 -new -key "$T/$1.key" -subj "/C=US/O=$3/CN=$2" -not_before 20240101000000Z \
        -not_after 20300101000000Z -out "$T/$1.crt" -addext basicConstraints=critical,CA:TRUE 2>/dev/null
}
leaf() { # file ca names from to
    $O genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out "$1.key" 2>/dev/null
    $O req -new -key "$1.key" -subj "/CN=${3%%,*}" -out "$T/$1.csr" 2>/dev/null
    san=$(echo "$3" | sed 's/[^,]*/DNS:&/g')
    printf 'subjectAltName=%s\nbasicConstraints=CA:FALSE\nextendedKeyUsage=serverAuth\n' "$san" > "$T/$1.ext"
    $O x509 -req -in "$T/$1.csr" -CA "$T/$2.crt" -CAkey "$T/$2.key" -set_serial 0x$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n') \
        -not_before "$4" -not_after "$5" -extfile "$T/$1.ext" -out "$T/$1.leaf" 2>/dev/null
    cat "$T/$1.leaf" "$T/$2.crt" > "$1.pem"
}
ca r11 R11 "Let's Encrypt"
ca stg "(STAGING) Ersatz Edamame E1" "(STAGING) Let's Encrypt"
leaf le r11 vpn.example.com 20261001100000Z 20261230100000Z
leaf le2 r11 vpn.example.com 20261201100000Z 20270301100000Z
leaf staging stg vpn.example.com 20261001100000Z 20261230100000Z
leaf other r11 other.example.org 20261001100000Z 20261230100000Z
leaf idn r11 xn--b1awf.xn--e1afmkfd.xn--p1ai 20261001100000Z 20261230100000Z
