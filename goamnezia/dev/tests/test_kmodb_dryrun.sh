#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
# goAmnezia pro — модуль ядра AmneziaWG, этап Б (docs/KMOD_PLAN.md): установка по согласию, перевод движка туда и
# обратно с откатом, режим «программа» при загруженном модуле, удаление, сторож, обновление; подгонка TCP MSS.
#
# Запуск: ${BASH5:-/opt/homebrew/bin/bash} tests/test_kmodb_dryrun.sh
# Нужны: bash ≥ 5, jq, python3, patch, tar; dash — для проверки start.sh на POSIX (нет — пропуск с причиной).
# Ничего вне $TMP: команды хоста (dkms, modprobe, modinfo, apt-get, apt-cache, dpkg-query, uname, mokutil,
# systemd-detect-virt, fuser, pgrep, curl, ip) — заглушки в PATH, пути хоста подменены (GOAMNEZIA_KMOD_*), Docker —
# сухой прогон с фикстурами контейнеров, часы — GA_KMOD_CLOCK_FILE.
#   Б2  проверка хоста: архитектура, LXC, нет /proc/modules, Secure Boot (mokutil и EFI), ядро < 5.4, мало места, нет
#       заголовков — одной фразой; всё хорошо — план пакетов; KMOD-05: метапакет тянет ядро — отдельно, с размером;
#   Б3  согласие: без терминала — «нет» (диалог в терминале — tests/test_kmod_pty.py);
#   Б4  ожидание apt с ходом в строках и отказ apt_locked; что поставили сами — в состоянии;
#   Б5  чужой sha256 — стоп; #218 по заголовкам (KMOD-04); набор не лёг; dkms.conf без AUTOINSTALL (KMOD-09);
#   Б6  сборка упала — программа, хвост make.log (KMOD-07); метка; хуки повторно; хук нового ядра apt не валит;
#   Б7  самопроверка провалилась — модуль выгружен, профили на программе; сценарий самопроверки без syncconf;
#   Б8  перевод и откат через 3 минуты (сдвиг часов); движок не тот — откат; людей не было — без ожидания; копии — 3;
#   Б9  блок движка в start.sh: program — amneziawg-go при загруженном модуле, kernel — awg-quick; goWARP цел в обоих
#       режимах и вставляется своим кодом на прежнее место; POSIX (dash -n);
#   Б10 status.kmod и profiles-status (engine, engine_want), «тихий откат» после перезагрузки в новое ядро — словами;
#   Б13 обновление с чужой меткой — пересборка отдельной службой; чужой модуль и упавшую пересборку не трогает;
#   Б14 удаление: сосед Kaskad — модуль не трогаем; без соседа — порядок рецепта и пакеты словами;
#   MSS  правила mangle один раз и после двух стартов, iptables-save, смена MTU, контейнер приложения, блок goWARP.
# ══════════════════════════════════════════════════════════════════════════════
set -uo pipefail
# Локаль — как в run_static.sh (TEST-FLAKE-11): запуск этого файла напрямую на macOS с пустыми LANG/LC_ALL ронял
# подстановки $(…) с local LC_* под нагрузкой (SIGSEGV в CoreFoundation). На Linux и при заданной локали — ничего.
if [ -z "${LC_ALL:-}${LANG:-}" ] && [ "$(uname -s 2>/dev/null)" = Darwin ]; then
    export LANG=C.UTF-8
fi

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
TMP=$(mktemp -d "${TMPDIR:-/tmp}/goamnezia-kmodb.XXXXXX") || exit 2
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
export GOAMNEZIA_EDGE_IPS_STUB=""
export UPSTREAM_DIR="$TMP/upstream"
export LIB_DIR="$ROOT/lib"
export GOAMNEZIA_SECRET_TMPDIR="$TMP/shm"
export BOT_DIR="$TMP/bot" ADMIN_DIR="$TMP/admin" MCP_DIR="$TMP/mcp"
export NGINX_SITE_CONF="$TMP/etc-nginx/sites-available/goamnezia" NGINX_SITE_LINK="$TMP/etc-nginx/sites-enabled/goamnezia"
# Хост стенда: /etc, /usr/src, /lib/modules, /var/lib/dkms, /proc, EFI, /sys/module — каталоги во $TMP.
export GOAMNEZIA_KMOD_ETC="$TMP/etc" GOAMNEZIA_KMOD_SRC_ROOT="$TMP/usrsrc" GOAMNEZIA_KMOD_LIBMOD="$TMP/libmod"
export GOAMNEZIA_KMOD_DKMS_TREE="$TMP/dkms" GOAMNEZIA_KMOD_PROC="$TMP/proc" GOAMNEZIA_KMOD_EFIVARS="$TMP/efivars"
export GOAMNEZIA_SYS_MODULE_DIR="$TMP/sysmodule" GOAMNEZIA_KMOD_HOOK_LOG="$TMP/kmod-hook.log"
export GOAMNEZIA_DF_FIXTURE="$TMP/df.txt"
export GA_KMOD_CLOCK_FILE="$TMP/clock"
CFS="$TMP/cfs"
export GOAMNEZIA_CONTAINER_FS_DIR="$CFS"
unset DOCKER_HOST DOCKER_CONTEXT
export GOAMNEZIA_DOCKER_SOCK="$TMP/nosuch.sock"
mkdir -p "$GOAMNEZIA_DIR" "$GOAMNEZIA_LOCK_DIR" "$GOAMNEZIA_SECRET_TMPDIR" "$GOAMNEZIA_SYS_MODULE_DIR" \
    "$CFS/amnezia-awg2" "$CFS/amnezia-awg3" "$TMP/etc" "$TMP/usrsrc" "$TMP/libmod" "$TMP/dkms" \
    "$TMP/proc/sys/kernel/random" "$TMP/efivars"
: >"$TMP/proc/modules"
printf 'Linux version 6.8.0-48-generic (buildd@lcy02-amd64-028) (x86_64-linux-gnu-gcc-13 (Ubuntu 13.2.0-23ubuntu4) 13.2.0, GNU ld (GNU Binutils for Ubuntu) 2.42) #48-Ubuntu SMP\n' >"$TMP/proc/version"
printf '7b1c7c1e-0000-4000-8000-000000000001\n' >"$TMP/proc/sys/kernel/random/boot_id"
printf 'Filesystem 1M-blocks Used Available Capacity Mounted on\n/dev/vda1 20000 10500 9500 53%% /\n' >"$TMP/df.txt"
printf '1760000000\n' >"$GA_KMOD_CLOCK_FILE"
BASH5="${BASH5:-bash}"
INSTALL="$ROOT/install.sh"

# ── Заглушки команд хоста ────────────────────────────────────────────────────
HOSTBIN="$TMP/hostbin"; mkdir -p "$HOSTBIN"
export STUB_LOG="$TMP/stub.log" STUB_DPKG="$TMP/dpkg-installed" STUB_CANDIDATES="$TMP/apt-candidates"
export STUB_DKMS_STATE="$TMP/dkms-state" STUB_FUSER_BUSY="$TMP/fuser-busy" STUB_TARBALL="$TMP/awgk.tgz"
: >"$STUB_LOG"; : >"$STUB_DPKG"
# Заголовки udp_tunnel.h: прежняя сигнатура (как у Ubuntu 24.04.1 Aeza 6.8.0-48 и -146, KMOD-04) и новая (struct sock *).
printf 'void setup_udp_tunnel_sock(struct net *net, struct socket *sock,\n\t\t\t   struct udp_tunnel_sock_cfg *sock_cfg);\n' >"$TMP/udp_old.h"
printf 'static inline void setup_udp_tunnel_sock(struct net *net, struct sock *sk,\n\t\t\tstruct udp_tunnel_sock_cfg *cfg);\n' >"$TMP/udp_new.h"
export STUB_UDP_TUNNEL_H="$TMP/udp_old.h"
printf 'linux-headers-6.8.0-48-generic\nlinux-headers-generic\ndkms\ngcc\nmake\npatch\ngcc-13\n' >"$STUB_CANDIDATES"
stub() { printf '%s\n' "$2" >"$HOSTBIN/$1"; chmod +x "$HOSTBIN/$1"; }
stub systemctl '#!/bin/sh
exit 1'
stub uname '#!/bin/sh
case "${1:-}" in -r) echo "${STUB_UNAME_R:-6.8.0-48-generic}" ;; -m) echo "${STUB_UNAME_M:-x86_64}" ;; *) echo Linux ;; esac'
stub systemd-detect-virt '#!/bin/sh
v="${STUB_VIRT:-none}"; echo "$v"; [ "$v" = none ] && exit 1; exit 0'
stub mokutil '#!/bin/sh
[ -n "${STUB_SB:-}" ] || exit 127
echo "SecureBoot $STUB_SB"'
stub fuser '#!/bin/sh
n=$(cat "$STUB_FUSER_BUSY" 2>/dev/null || echo 0)
[ "${n:-0}" -gt 0 ] 2>/dev/null || exit 1
echo $((n - 1)) >"$STUB_FUSER_BUSY"; exit 0'
stub pgrep '#!/bin/sh
exit 1'
stub dpkg-query '#!/bin/sh
p=""; for a in "$@"; do p="$a"; done
grep -qx -- "$p" "$STUB_DPKG" 2>/dev/null && { printf "install ok installed"; exit 0; }
exit 1'
stub apt-cache '#!/bin/bash
case "$1" in
  policy) c=""; grep -qx -- "$2" "$STUB_CANDIDATES" 2>/dev/null && c="1.0-1"
          printf "%s:\n  Installed: (none)\n  Candidate: %s\n" "$2" "${c:-(none)}" ;;
  show) shift; for a in "$@"; do case "$a" in -*) continue ;; esac
          printf "Package: %s\n" "$a"
          case "$a" in linux-image-*|linux-modules-*) echo "Installed-Size: 409600" ;; *) echo "Installed-Size: 20480" ;; esac
          echo; done ;;
esac'
stub apt-get '#!/bin/bash
echo "apt-get $*" >>"$STUB_LOG"
sim=0; inst=0; pk=()
for a in "$@"; do case "$a" in -s|--simulate) sim=1 ;; install) inst=1 ;; update) exit 0 ;; -*|*::*) ;; *) pk+=("$a") ;; esac; done
[ "$inst" = 1 ] || exit 0
if [ "$sim" = 1 ]; then
  for p in "${pk[@]}"; do
    echo "Inst $p (1.0-1 Ubuntu:24.04/noble [amd64])"
    case " ${STUB_META_PULLS:-} " in *" $p "*)
      echo "Inst linux-image-6.8.0-146-generic (6.8.0-146.146 Ubuntu:24.04/noble-updates [amd64])"
      echo "Inst linux-modules-6.8.0-146-generic (6.8.0-146.146 Ubuntu:24.04/noble-updates [amd64])" ;; esac
  done
  exit 0
fi
[ "${STUB_APT_FAIL:-0}" = 1 ] && { echo "E: Unable to locate package" >&2; exit 100; }
for p in "${pk[@]}"; do
  echo "$p" >>"$STUB_DPKG"
  # заголовки ядра появляются в /lib/modules/<ядро>/build только после установки пакета
  case "$p" in linux-headers-[0-9]*) k="${p#linux-headers-}"; mkdir -p "$GOAMNEZIA_KMOD_LIBMOD/$k/build/include/net"
    cp "$STUB_UDP_TUNNEL_H" "$GOAMNEZIA_KMOD_LIBMOD/$k/build/include/net/udp_tunnel.h" ;; esac
done'
stub dkms '#!/bin/bash
echo "dkms $*" >>"$STUB_LOG"
cmd="$1"; shift; v=""; k=""
while [ $# -gt 0 ]; do case "$1" in -v) v="$2"; shift ;; -k) k="$2"; shift ;; esac; shift; done
st="$STUB_DKMS_STATE"
case "$cmd" in
  status) [ -f "$st" ] || exit 0
          grep -qx added "$st" || exit 0
          if [ -n "$k" ]; then grep -qx "installed $k" "$st" && echo "amneziawg/$v, $k, x86_64: installed"
          else echo "amneziawg/$v: added"; fi ;;
  add) grep -qx added "$st" 2>/dev/null && exit 3; echo added >>"$st" ;;
  build) [ "${STUB_DKMS_BUILD:-ok}" = fail ] && exit 10; exit 0 ;;
  install) ver=$(sed -n "s/.*WIREGUARD_VERSION \"\\(.*\\)\".*/\\1/p" "$GOAMNEZIA_KMOD_SRC_ROOT/amneziawg-$v/version.h")
           mkdir -p "$GOAMNEZIA_KMOD_LIBMOD/$k/updates/dkms"
           echo "version=$ver" >"$GOAMNEZIA_KMOD_LIBMOD/$k/updates/dkms/amneziawg.ko"; echo "installed $k" >>"$st" ;;
  remove) rm -f "$st" "$GOAMNEZIA_KMOD_LIBMOD"/*/updates/dkms/amneziawg.ko ;;
esac
exit 0'
stub modinfo '#!/bin/sh
f=""; for a in "$@"; do f="$a"; done
[ -f "$f" ] && sed -n "s/^version=//p" "$f"'
stub modprobe '#!/bin/bash
echo "modprobe $*" >>"$STUB_LOG"
sm="$GOAMNEZIA_SYS_MODULE_DIR/amneziawg"
if [ "${1:-}" = -r ]; then
  [ "${STUB_MOD_INUSE:-0}" = 1 ] && { echo "modprobe: FATAL: Module amneziawg is in use." >&2; exit 1; }
  rm -rf "$sm"; exit 0
fi
f="$GOAMNEZIA_KMOD_LIBMOD/$(uname -r)/updates/dkms/amneziawg.ko"
[ -f "$f" ] || exit 1
mkdir -p "$sm"; sed -n "s/^version=//p" "$f" >"$sm/version"'
stub curl '#!/bin/sh
echo "curl $*" >>"$STUB_LOG"
[ "${STUB_CURL:-ok}" = fail ] && exit 22
o=""; while [ $# -gt 0 ]; do [ "$1" = -o ] && { o="$2"; shift; }; shift; done
# без -o (curl --version у проверки зависимостей) — только версия: копировать некуда
[ -n "$o" ] || { echo "curl 8.5.0 (x86_64-pc-linux-gnu)"; exit 0; }
cp "$STUB_TARBALL" "$o"'
stub ip '#!/bin/sh
if [ "${1:-}" = -o ] && [ "${2:-}" = link ]; then printf "%s\n" "${STUB_IP_LINKS:-1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue}"; fi
exit 0'
export PATH="$HOSTBIN:$PATH"

pass=0; fail=0; skipped=0; rc=0; out=""
ok()   { pass=$((pass + 1)); printf '  ✓ %s\n' "$1"; }
bad()  { fail=$((fail + 1)); printf '  ✗ %s\n' "$1"; [ -n "${2:-}" ] && printf '      %s\n' "$2" | head -c 1500; return 0; }
skp()  { skipped=$((skipped + 1)); printf '  – %s\n' "$1"; }
check() { if ( eval "$2" ) >/dev/null 2>&1; then ok "$1"; else bad "$1" "${3:-}"; fi; }
section() { printf '\n── %s\n' "$1"; }
run() {
    GA_ERR_CODE=""; GA_ERR_MSG=""; GA_LAST_ERROR_CODE=""
    : >"$STUB_LOG"
    "$@" >"$OUT" 2>"$ERR"; rc=$?
    out=$(<"$OUT")
    return 0
}
bridge() {   # bridge <действие> [--k=v…] — мост отдельным процессом; ответ — последняя строка
    : >"$STUB_LOG"
    "$BASH5" "$INSTALL" --action="$1" "${@:2}" --json >"$OUT" 2>"$ERR"; rc=$?
    out=$(tail -n 1 "$OUT")
}
errs() { tail -n 8 "$ERR" 2>/dev/null; }
jget() { jq -r "$1" <<<"$out" 2>/dev/null; }
clock() { head -n 1 "$GA_KMOD_CLOCK_FILE"; }
KVER=3.1.20260906
SRC="$TMP/usrsrc/amneziawg-$KVER"
KO="$TMP/libmod/6.8.0-48-generic/updates/dkms/amneziawg.ko"
hook1="$TMP/etc/kernel/postinst.d/zz-goamnezia-amneziawg"
hook2="$TMP/etc/kernel/header_postinst.d/zz-goamnezia-amneziawg"
DASH=$(command -v dash 2>/dev/null || true)

# Ответы контейнеров на `ip -d link show dev awg0`: программа (tun) и модуль ядра (amneziawg).
IPLINK_TUN=$'3: awg0: <POINTOPOINT,NOARP,UP,LOWER_UP> mtu 1280 qdisc fq_codel state UNKNOWN\n    link/none\n    tun type tun pi off vnet_hdr off persist off\nINTERFACE=awg0\ntun_flags=1'
IPLINK_KMOD=$'4: awg0: <POINTOPOINT,NOARP,UP,LOWER_UP> mtu 1280 qdisc noqueue state UNKNOWN\n    link/none\n    amneziawg addrgenmode none\nDEVTYPE=amneziawg\nINTERFACE=awg0'
PEER_A="R7PrjHHtJGilh+xUME6haQ4KA6Xwev/4ifa5JY7kiG0="
# awgshow <контейнер> <файл> <рукопожатие, секунды эпохи> — дамп `awg show awg0 dump` с одним человеком.
awgshow() {
    printf 'PRIVKEY\tSRVPUB\t40000\toff\n%s\t(none)\t198.51.100.7:51820\t10.8.2.5/32\t%s\t1000\t2000\toff\n' "$PEER_A" "$3" \
        >"$CFS/$1/$2"
}
engine_fx() { printf '%s\n' "$2" >"$CFS/$1/.iplink"; }

# ── Окружение и профили ──────────────────────────────────────────────────────
section "окружение: профили awg2 (2.0) и awg3 (3.1) в сухом прогоне"
# shellcheck disable=SC1091
source "$ROOT/lib/common.sh"
# shellcheck disable=SC1091
source "$ROOT/lib/i18n.sh"
load_language ru >/dev/null 2>&1
for _m in docker amnezia awg_params users export edgeips import kmod; do
    # shellcheck disable=SC1090
    source "$ROOT/lib/$_m.sh"
done
mkdir -p "$UPSTREAM_DIR"
cp -R "$ROOT/lib/upstream/." "$UPSTREAM_DIR/"
mkdir -p "$STATE_DIR" && chmod 700 "$STATE_DIR"
GA_VER=$(sed -n 's/^GOAMNEZIA_VERSION="\([^"]*\)".*/\1/p' "$ROOT/lib/common.sh" | head -1)
cat >"$CONFIG_FILE" <<EOF
{"schema": 1, "version": "$GA_VER", "installed": true, "mode": "lite", "lang": "ru", "domain": "",
 "server_ip": "203.0.113.10", "endpoint_host": "", "client_dns": ["1.1.1.1", "1.0.0.1"],
 "profiles": {}, "options": {"drop_icmp": false}}
EOF
chmod 600 "$CONFIG_FILE"
printf '{"schema": 1, "users": []}\n' >"$USERS_FILE"; chmod 600 "$USERS_FILE"
run amnezia_profile_install awg2 40000 default
check "profile-install awg2: rc=0" '[ "$rc" = 0 ]' "$(errs)"
run amnezia_profile_install awg3 40001
check "profile-install awg3: rc=0, линия 3.1" '[ "$rc" = 0 ] && [ "$(ga_profile_line awg3)" = 3.1 ]' "$(errs)"
check "kmod.json ещё нет — модулем не занимались, человек не выбирал" '[ ! -f "$(ga_kmod_state_file)" ] && [ -z "$(ga_kmod_want)" ]'
check "порядок перевода — awg2, затем awg3 (awg1 не стоит)" '[ "$(ga_kmod_profiles)" = "awg2 awg3" ]' "$(ga_kmod_profiles)"
check "новый профиль: start.sh из шаблона — блок движка и блок MSS по одному, перед tail -f /dev/null" \
    '[ "$(grep -cxF "$GA_KMOD_BLOCK_BEGIN" "$STATE_DIR/amnezia-awg2/start.sh")" = 1 ] && [ "$(grep -cxF "$GA_AMN_MSS_BEGIN" "$STATE_DIR/amnezia-awg2/start.sh")" = 1 ] && [ "$(tail -n 1 "$STATE_DIR/amnezia-awg2/start.sh")" = "tail -f /dev/null" ] && ! grep -qxF "$GA_KMOD_UP_LINE" "$STATE_DIR/amnezia-awg2/start.sh"' \
    "$(grep -n "goamnezia\|tail\|awg-quick up" "$STATE_DIR/amnezia-awg2/start.sh")"
check "человек не выбирал — файла engine у нового профиля нет (как раньше: awg-quick сам)" '[ ! -f "$STATE_DIR/amnezia-awg2/engine" ]'

# ── Б2: проверка хоста ───────────────────────────────────────────────────────
section "Б2: проверка хоста — одной фразой с причиной"
# hostcheck [ПЕРЕМЕННАЯ=значение…] — проверка хоста с заглушками в этом окружении (в подоболочке).
hostcheck() {
    (
        local kv
        for kv in "$@"; do export "${kv%%=*}=${kv#*=}"; done
        ga_kmod_check_json plan
    ) 2>/dev/null
}
out=$(hostcheck STUB_UNAME_M=armv7l)
check "архитектура armv7l → code=arch, причина словами" '[ "$(jget .ok)" = false ] && [ "$(jget .code)" = arch ] && grep -qF "архитектура armv7l" <<<"$(jget .reason)"' "$out"
out=$(hostcheck STUB_VIRT=lxc)
check "контейнер LXC → code=container: «это контейнер LXC … оставляю программу amneziawg-go»" \
    '[ "$(jget .code)" = container ] && [ "$(jget .reason)" = "модуль ядра здесь нельзя: это контейнер LXC, модули ядра в нём не грузятся — оставляю программу amneziawg-go" ]' "$out"
out=$(hostcheck STUB_VIRT=openvz)
check "OpenVZ → code=container, имя по-человечески" '[ "$(jget .code)" = container ] && grep -qF "OpenVZ" <<<"$(jget .reason)"' "$out"
mv "$TMP/proc/modules" "$TMP/proc/modules.off"
out=$(hostcheck)
check "нет /proc/modules → code=no_proc_modules" '[ "$(jget .code)" = no_proc_modules ]' "$out"
mv "$TMP/proc/modules.off" "$TMP/proc/modules"
out=$(hostcheck STUB_SB=enabled)
check "Secure Boot (mokutil) → code=secure_boot, что сделать" '[ "$(jget .code)" = secure_boot ] && [ "$(jget .secure_boot)" = true ] && grep -qF "выключите его" <<<"$(jget .reason)"' "$out"
printf '\x06\x00\x00\x00\x01' >"$TMP/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c"
out=$(hostcheck)
check "Secure Boot без mokutil — по переменной EFI (пятый байт 1)" '[ "$(jget .code)" = secure_boot ]' "$out"
printf '\x06\x00\x00\x00\x00' >"$TMP/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c"
out=$(hostcheck STUB_UNAME_R=4.15.0-213-generic)
check "ядро 4.15 → code=kernel_old" '[ "$(jget .code)" = kernel_old ] && grep -qF "4.15.0-213-generic" <<<"$(jget .reason)"' "$out"
printf 'Filesystem 1M-blocks Used Available Capacity Mounted on\n/dev/vda1 9000 8580 420 96%% /\n' >"$TMP/df-low.txt"
out=$(hostcheck GOAMNEZIA_DF_FIXTURE="$TMP/df-low.txt")
check "на диске 420 МБ → code=disk: «на диске свободно 420 МБ, а нужно от 600 МБ»" \
    '[ "$(jget .code)" = disk ] && grep -qF "на диске свободно 420 МБ, а нужно от 600 МБ" <<<"$(jget .reason)"' "$out"
out=$(hostcheck STUB_UNAME_R=6.8.0-48-aws STUB_CANDIDATES="$TMP/none")
check "нет кандидата linux-headers-<ядро> (ядро хостера) → code=no_headers, с ядром" \
    '[ "$(jget .code)" = no_headers ] && grep -qF "6.8.0-48-aws" <<<"$(jget .reason)"' "$out"
out=$(hostcheck)
check "всё хорошо → ok, план: dkms gcc make patch gcc-13 linux-headers-6.8.0-48-generic, размер в МБ" \
    '[ "$(jget .ok)" = true ] && [ "$(jget ".plan.missing | join(\" \")")" = "dkms gcc make patch gcc-13 linux-headers-6.8.0-48-generic" ] && [ "$(jget .plan.size_mb)" -gt 0 ] && [ "$(jget .plan.gcc)" = gcc-13 ]' "$out"
check "метапакет заголовков linux-headers-generic ядро не тянет — kernel=false (поставится сам)" \
    '[ "$(jget .plan.headers_meta.name)" = linux-headers-generic ] && [ "$(jget .plan.headers_meta.kernel)" = false ]' "$out"
out=$(hostcheck STUB_META_PULLS=linux-headers-generic)
check "KMOD-05: метапакет тянет новое ядро — kernel=true и размер (≈800 МБ, а не 200)" \
    '[ "$(jget .plan.headers_meta.kernel)" = true ] && [ "$(jget .plan.headers_meta.size_mb)" -ge 800 ]' "$out"
bridge kmod-check
check "мост kmod-check: success, check.ok, ничего не поставлено (только apt-get -s)" \
    '[ "$rc" = 0 ] && [ "$(jget .status)" = success ] && [ "$(jget .check.ok)" = true ] && ! grep -q "^apt-get .*install -y" "$STUB_LOG" && ! grep -q "^dkms \|^modprobe " "$STUB_LOG"' "rc=$rc ${out:0:300} $(cat "$STUB_LOG")"
bridge kmod-status
check "мост kmod-status: state=off, want пусто, kernel, label — модуля нет" \
    '[ "$(jget .status)" = success ] && [ "$(jget .kmod.state)" = off ] && [ "$(jget .kmod.want)" = "" ] && [ "$(jget .kmod.kernel)" = 6.8.0-48-generic ] && [ "$(jget .kmod.label)" = 3.1.20260906+kaskad-k31.1 ] && [ "$(jget .kmod.loaded)" = false ]' "${out:0:400}"

# ── Б3: согласие ─────────────────────────────────────────────────────────────
section "Б3: согласие цифрами — без терминала только «нет»"
if [ -t 0 ] || { : </dev/tty; } 2>/dev/null; then
    skp "прогон с терминалом — вопрос ждал бы ответа; диалог проверяет tests/test_kmod_pty.py"
else
    run _ga_kmod_ask12 "Выбор [1]:" 1
    check "нет терминала и ввода — ответ «нет» (rc 1), ничего не ставится" '[ "$rc" = 1 ] && [ ! -s "$STUB_LOG" ]' "rc=$rc"
fi

# ── Б4: ожидание apt ─────────────────────────────────────────────────────────
section "Б4: ожидание блокировки apt с ходом в строках"
echo 13 >"$STUB_FUSER_BUSY"
t0=$(clock)
run _ga_kmod_apt_wait 1800
check "apt занят минуту — ждём (часы сдвинулись на 65 с), строки хода, rc 0" \
    '[ "$rc" = 0 ] && [ $(( $(clock) - t0 )) -ge 60 ] && grep -qF "Пакеты сейчас ставит другой процесс" "$ERR" && grep -qF "прошло 0 мин из 30" "$ERR"' "rc=$rc $(errs)"
check "ход ожидания — и в журнале действий (kmod-install step=apt_wait)" 'grep -q "\"action\":\"kmod-install\".*step=apt_wait" "$GOAMNEZIA_AUDIT_LOG"' "$(tail -3 "$GOAMNEZIA_AUDIT_LOG")"
_ga_kmod_progress_done
echo 999 >"$STUB_FUSER_BUSY"
run _ga_kmod_apt_wait 60
check "занят дольше предела — отказ apt_locked словами" '[ "$rc" = 1 ] && [ "$GA_ERR_CODE" = apt_locked ] && grep -qF "уже 1 мин" <<<"$GA_ERR_MSG"' "rc=$rc $GA_ERR_CODE $GA_ERR_MSG"
echo 0 >"$STUB_FUSER_BUSY"
_ga_kmod_progress_done

# ── Исходники и патчи стенда ─────────────────────────────────────────────────
# Архив «тега»: src/ с dkms.conf (AUTOINSTALL и REMAKE_INITRD — как у upstream), version.h без метки и compat.h с местом
# для настоящего патча #218. Набор — его последний ханк (метка в version.h): sha256 стенда подменён (только в dry-run).
mk_tarball() {
    local d="$TMP/srcbuild/amneziawg-linux-kernel-module-$KVER/src"
    rm -rf "$TMP/srcbuild"; mkdir -p "$d/compat"
    printf 'PACKAGE_NAME="amneziawg"\nPACKAGE_VERSION="1.0.0"\nAUTOINSTALL=yes\nREMAKE_INITRD=no\nBUILT_MODULE_NAME="amneziawg"\nDEST_MODULE_LOCATION="/kernel/net"\n' >"$d/dkms.conf"
    printf '#ifndef WIREGUARD_VERSION\n#define WIREGUARD_VERSION "%s"\n#endif\n' "${1:-3.1.20260812}" >"$d/version.h"
    printf '/* compat (стенд) */\n#if 1\n\n#endif\n\n#if LINUX_VERSION_CODE < KERNEL_VERSION(7, 1, 5)\n#include <net/udp_tunnel.h>\n#define setup_udp_tunnel_sock(net, sk, sock_cfg) setup_udp_tunnel_sock(net, sk->sk_socket, sock_cfg)\n#define udp_tunnel_sock_release(sk) udp_tunnel_sock_release(sk->sk_socket)\n#endif\n\n#if LINUX_VERSION_CODE < KERNEL_VERSION(6, 18, 0)\n#define WQ_PERCPU 0\n#endif\n' >"$d/compat/compat.h"
    tar -czf "$STUB_TARBALL" -C "$TMP/srcbuild" "amneziawg-linux-kernel-module-$KVER"
    GOAMNEZIA_KMOD_SHA256=$(shasum -a 256 "$STUB_TARBALL" 2>/dev/null | awk '{print $1}')
    export GOAMNEZIA_KMOD_SHA256
}
mk_tarball
mkdir -p "$TMP/patches"
cp "$ROOT/lib/upstream/kmod/awg-kmod-218.patch" "$TMP/patches/"
{ printf 'goAmnezia стенд: последний ханк набора kaskad-k31.1 (метка)\n\n'
  sed -n '/^diff --git a\/src\/version.h/,$p' "$ROOT/lib/upstream/kmod/awg-kmod-31.patch"; } >"$TMP/patches/awg-kmod-31.patch"
export GOAMNEZIA_KMOD_PATCH_DIR="$TMP/patches"
GOAMNEZIA_KMOD_PATCH_31_SHA=$(shasum -a 256 "$TMP/patches/awg-kmod-31.patch" | awk '{print $1}')
export GOAMNEZIA_KMOD_PATCH_31_SHA
engine_fx amnezia-awg2 "$IPLINK_TUN"; engine_fx amnezia-awg3 "$IPLINK_TUN"
reset_host() {   # всё, что поставила «установка», — прочь (стенд с нуля)
    rm -rf "$SRC" "$TMP/libmod/6.8.0-48-generic" "$GOAMNEZIA_SYS_MODULE_DIR/amneziawg" "$STUB_DKMS_STATE" \
        "$TMP/etc/kernel" "$TMP/etc/modules-load.d" "$(ga_kmod_state_file)" "$STATE_DIR"/amnezia-awg*/engine \
        "$(_ga_kmod_progress_file)" "$(_ga_kmod_engines_file)"
    : >"$STUB_DPKG"
}

# ── Б5: чужой sha256 ─────────────────────────────────────────────────────────
section "Б5: архив с чужим sha256 — стоп, ничего не собрано"
export GOAMNEZIA_KMOD_SHA256_GOOD="$GOAMNEZIA_KMOD_SHA256"
export GOAMNEZIA_KMOD_SHA256=0000000000000000000000000000000000000000000000000000000000000000
bridge kmod-install
check "мост kmod-install: отказ kmod_sha_mismatch словами («собирать его не буду»)" \
    '[ "$rc" != 0 ] && [ "$(jget .code)" = kmod_sha_mismatch ] && grep -qF "собирать его не буду" <<<"$(jget .message)"' "rc=$rc ${out:0:400} $(errs)"
check "… исходников нет, dkms не звали, модуль не грузили" '[ ! -d "$SRC" ] && ! grep -q "^dkms \|^modprobe " "$STUB_LOG"' "$(cat "$STUB_LOG")"
check "… профили закреплены за программой (файл engine) — до чего-либо на хосте" \
    '[ "$(cat "$STATE_DIR/amnezia-awg2/engine")" = program ] && [ "$(cat "$STATE_DIR/amnezia-awg3/engine")" = program ] && [ "$(ga_kmod_want)" = program ]'
check "… в состоянии — провал с ядром и шагом (failed.kernel, step=source)" \
    '[ "$(jq -r .failed.kernel "$(ga_kmod_state_file)")" = 6.8.0-48-generic ] && [ "$(jq -r .failed.step "$(ga_kmod_state_file)")" = source ]'
bridge kmod-status
check "… kmod-status: state=failed, причина словами" '[ "$(jget .kmod.state)" = failed ] && grep -qF "sha256" <<<"$(jget .kmod.reason)"' "${out:0:300}"
export GOAMNEZIA_KMOD_SHA256="$GOAMNEZIA_KMOD_SHA256_GOOD"

# ── Б5: набор не лёг ─────────────────────────────────────────────────────────
section "Б5: набор исправлений не лёг — стоп, модуль без набора не ставится"
reset_host
mk_tarball "3.1.20990101"
run ga_kmod_install off off
check "rc≠0, kmod_patch_failed, причина с ядром" '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = kmod_patch_failed ] && grep -qF "6.8.0-48-generic" <<<"$GA_ERR_MSG"' "rc=$rc $GA_ERR_CODE $GA_ERR_MSG"
check "… dkms build не звали, в памяти пусто" '! grep -q "^dkms build" "$STUB_LOG" && [ ! -d "$GOAMNEZIA_SYS_MODULE_DIR/amneziawg" ]' "$(cat "$STUB_LOG")"
mk_tarball

# ── Б6: сборка упала ─────────────────────────────────────────────────────────
section "Б6: сборка упала — профили на программе, хвост make.log (KMOD-07)"
reset_host
mkdir -p "$TMP/dkms/amneziawg/$KVER/6.8.0-48-generic/x86_64/log"
printf 'make: Entering directory\nsrc/socket.c:42: error: too few arguments to function setup_udp_tunnel_sock\n' \
    >"$TMP/dkms/amneziawg/$KVER/6.8.0-48-generic/x86_64/log/make.log"
STUB_DKMS_BUILD=fail run ga_kmod_install off off
check "rc≠0, kmod_build_failed: в словах — последняя строка make.log" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = kmod_build_failed ] && grep -qF "too few arguments" <<<"$GA_ERR_MSG"' "rc=$rc $GA_ERR_CODE $GA_ERR_MSG"
check "… хвост make.log — в состоянии (failed.log), шаг build" \
    'grep -qF "too few arguments" <<<"$(jq -r .failed.log "$(ga_kmod_state_file)")" && [ "$(jq -r .failed.step "$(ga_kmod_state_file)")" = build ]'
check "… модуль не загружен, автозагрузки нет, профили — программа" \
    '[ ! -d "$GOAMNEZIA_SYS_MODULE_DIR/amneziawg" ] && [ ! -f "$TMP/etc/modules-load.d/amneziawg.conf" ] && [ "$(cat "$STATE_DIR/amnezia-awg2/engine")" = program ]'
check "… пакеты поставлены и записаны как наши (назовём при удалении)" \
    '[ "$(jq -r ".pkgs | join(\" \")" "$(ga_kmod_state_file)")" = "dkms gcc gcc-13 linux-headers-6.8.0-48-generic linux-headers-generic make patch" ]' \
    "$(jq -c .pkgs "$(ga_kmod_state_file)")"
check "… KMOD-09: в dkms.conf нет строк AUTOINSTALL и REMAKE_INITRD, PACKAGE_VERSION — тег" \
    '! grep -q "AUTOINSTALL\|REMAKE_INITRD" "$SRC/dkms.conf" && grep -qx "PACKAGE_VERSION=\"$KVER\"" "$SRC/dkms.conf" && grep -qx "PACKAGE_NAME=\"amneziawg\"" "$SRC/dkms.conf"' "$(cat "$SRC/dkms.conf")"
check "… KMOD-04: заголовки со старой сигнатурой — #218 не наложен" '! grep -q __compat_setup_udp_tunnel_sock "$SRC/compat/compat.h"'

# ── Б5–Б7: установка целиком ─────────────────────────────────────────────────
section "Б5–Б7: установка — #218 по заголовкам, набор, метка, хуки, самопроверка"
reset_host
export STUB_UDP_TUNNEL_H="$TMP/udp_new.h"
bridge kmod-install
check "мост kmod-install: success, state=ready (модуль загружен, профили на программе)" \
    '[ "$rc" = 0 ] && [ "$(jget .status)" = success ] && [ "$(jget .state)" = ready ] && [ "$(jget .version)" = 3.1.20260906+kaskad-k31.1 ] && [ "$(jget .kernel)" = 6.8.0-48-generic ]' \
    "rc=$rc ${out:0:500} $(errs)"
check "… #218 наложен: заголовки 6.8.0-48 объявляют новую сигнатуру (решение по заголовкам)" \
    '[ "$(jget .p218)" = true ] && grep -q __compat_setup_udp_tunnel_sock "$SRC/compat/compat.h"'
check "… метка в памяти и в файле модуля — 3.1.20260906+kaskad-k31.1" \
    '[ "$(cat "$GOAMNEZIA_SYS_MODULE_DIR/amneziawg/version")" = 3.1.20260906+kaskad-k31.1 ] && grep -qx "version=3.1.20260906+kaskad-k31.1" "$KO"'
check "… порядок: dkms add → build -k → install -k --force → modprobe amneziawg" \
    '[ "$(grep -E "^(dkms (add|build|install)|modprobe amneziawg)" "$STUB_LOG" | cut -d" " -f1,2 | paste -sd, -)" = "dkms add,dkms build,dkms install,modprobe amneziawg" ]' "$(cat "$STUB_LOG")"
check "… /etc/modules-load.d/amneziawg.conf, хуки в postinst.d и header_postinst.d (0755)" \
    '[ "$(cat "$TMP/etc/modules-load.d/amneziawg.conf")" = amneziawg ] && [ -x "$hook1" ] && [ -x "$hook2" ] && cmp -s "$hook1" "$hook2"'
check "… хук: «|| true» и выход 0 — apt не валит; #218 по заголовкам нового ядра; AUTOINSTALL нигде" \
    'grep -qF "|| true" "$hook1" && [ "$(tail -n 1 "$hook1")" = "exit 0" ] && grep -qF "awg-kmod-218.patch" "$hook1" && ! grep -q AUTOINSTALL "$hook1"'
check "… самопроверка прошла: случаи 2.0 и 3.1, клиент-программа и клиент-модуль" \
    '[ "$(jget .selftest.ok)" = true ] && [ "$(jget "[.selftest.cases[].id] | join(\",\")")" = "awg2p,awg2k,awg3p,awg3k" ]' "$(jget .selftest)"
check "… сценарий самопроверки: bash -n чисто, syncconf нет (живой тест #253 — никогда), конфиги 3.1 с ключом защиты" \
    '"$BASH5" -n "$GOAMNEZIA_LOCK_DIR/kmod-selftest.sh" && ! grep -q syncconf "$GOAMNEZIA_LOCK_DIR/kmod-selftest.sh" && grep -q "^HeaderProtectionKey = " "$GOAMNEZIA_LOCK_DIR/kmod-selftest.sh" && ! grep -q "^Address\|^DNS\|^MTU" "$GOAMNEZIA_LOCK_DIR/kmod-selftest.sh"' \
    "$(grep -c . "$GOAMNEZIA_LOCK_DIR/kmod-selftest.sh")"
check "… движок профилей не менялся: перезапусков нет, want=program, файлы engine=program" \
    '! grep -q "docker restart" "$ERR" && [ "$(ga_kmod_want)" = program ] && [ "$(cat "$STATE_DIR/amnezia-awg3/engine")" = program ]' "$(grep -c "docker restart" "$ERR")"
check "… ход — в журнале действий по шагам (check, packages, source, patch, build, load, selftest, done)" \
    '[ "$(grep -c "\"action\":\"kmod-install\"" "$GOAMNEZIA_AUDIT_LOG")" -ge 7 ] && grep -q "step=build" "$GOAMNEZIA_AUDIT_LOG" && grep -q "kmod-install\",\"details\":\"done" "$GOAMNEZIA_AUDIT_LOG"' \
    "$(grep kmod-install "$GOAMNEZIA_AUDIT_LOG" | tail -3)"
check "… хода после конца нет (kmod-status.progress = null)" '[ ! -f "$(_ga_kmod_progress_file)" ] && [ "$(ga_kmod_progress_json)" = null ]'
cp "$hook1" "$TMP/hook1.saved"
bridge kmod-install
check "повторная установка: модуль с меткой уже стоит — reused, без сборки; хуки те же, по одному файлу" \
    '[ "$rc" = 0 ] && [ "$(jget .reused)" = true ] && ! grep -q "^dkms build\|^apt-get .*install -y" "$STUB_LOG" && cmp -s "$hook1" "$TMP/hook1.saved" && [ "$(find "$TMP/etc/kernel" -type f | wc -l | tr -d " ")" = 2 ]' \
    "rc=$rc ${out:0:300} $(cat "$STUB_LOG")"

# ── Б6: хук нового ядра ──────────────────────────────────────────────────────
section "Б6: хук нового ядра — собирает, когда есть заголовки; не валит apt"
: >"$STUB_LOG"
sh "$hook1" 6.8.0-200-generic; hrc=$?
check "заголовков 6.8.0-200 нет — ничего не делает, выход 0" '[ "$hrc" = 0 ] && ! grep -q "dkms build" "$STUB_LOG"' "$(cat "$STUB_LOG")"
mkdir -p "$TMP/libmod/6.8.0-200-generic/build/include/net"
sh "$hook2" 6.8.0-200-generic; hrc=$?
check "заголовки есть — dkms build и install для 6.8.0-200-generic, выход 0" \
    '[ "$hrc" = 0 ] && grep -q "dkms build -m amneziawg -v $KVER -k 6.8.0-200-generic" "$STUB_LOG" && grep -q "dkms install -m amneziawg -v $KVER -k 6.8.0-200-generic --force" "$STUB_LOG"' "$(cat "$STUB_LOG")"
: >"$STUB_LOG"
STUB_DKMS_BUILD=fail sh "$hook1" 6.8.0-201-generic; hrc=$?
mkdir -p "$TMP/libmod/6.8.0-201-generic/build"
STUB_DKMS_BUILD=fail sh "$hook1" 6.8.0-201-generic; hrc=$?
check "сборка в хуке упала — выход всё равно 0 (apt не валится), журнал хука есть" '[ "$hrc" = 0 ] && [ -f "$GOAMNEZIA_KMOD_HOOK_LOG" ]'

# ── Б7: самопроверка провалилась ─────────────────────────────────────────────
section "Б7: самопроверка провалилась — модуль выгружен, профили на программе"
printf 'SELFTEST START\nCASE awg2p OK ping=2\nCASE awg2k OK ping=2\nCASE awg3p FAIL handshake=0 ping=0\nCASE awg3k OK ping=2\nSELFTEST END\n' >"$TMP/st-fail.txt"
rm -f "$(_ga_kmod_engines_file)"
GOAMNEZIA_KMOD_SELFTEST_FIXTURE="$TMP/st-fail.txt" run ga_kmod_install on off
check "rc≠0, kmod_selftest_failed; причина — профиль, линия и клиент словами" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = kmod_selftest_failed ] && grep -qF "awg3 (линия 3.1), клиент на программе" <<<"$GA_ERR_MSG"' "rc=$rc $GA_ERR_CODE $GA_ERR_MSG"
check "… модуль выгружен (modprobe -r), профили на программе, state=failed" \
    'grep -q "^modprobe -r amneziawg" "$STUB_LOG" && [ ! -d "$GOAMNEZIA_SYS_MODULE_DIR/amneziawg" ] && [ "$(_ga_kmod_status_field state)" = failed ] && [ "$(ga_kmod_want)" = program ]' \
    "$(cat "$STUB_LOG") $(_ga_kmod_status_field state)"
run ga_kmod_ready
check "перед переводом на модуль самопроверка этого ядра делается заново (модуль грузится, на этот раз прошла)" \
    '[ "$rc" = 0 ] && grep -q "^modprobe amneziawg" "$STUB_LOG" && [ "$(jq -r .selftest.ok "$(ga_kmod_state_file)")" = true ]' "rc=$rc $GA_ERR_CODE $GA_ERR_MSG"
_ga_kmod_progress_done

# ── Б8: перевод и откат ──────────────────────────────────────────────────────
section "Б8: перевод на модуль по одному, пользователи возвращаются; откат через 3 минуты (сдвиг часов)"
# Модуль готов: загружен с меткой, самопроверка этого ядра прошла.
modprobe amneziawg; ga_kmod_selftest >/dev/null 2>&1
for c in amnezia-awg2 amnezia-awg3; do
    engine_fx "$c" "$IPLINK_TUN"
    printf '%s\n' "$IPLINK_KMOD" >"$CFS/$c/.iplink.kernel"; printf '%s\n' "$IPLINK_TUN" >"$CFS/$c/.iplink.program"
    rm -f "$CFS/$c/.awgshow"*
done
T0=$(clock)
awgshow amnezia-awg2 .awgshow $((T0 - 60))          # на связи до смены
awgshow amnezia-awg2 .awgshow.kernel $((T0 + 14))  # вернулся через 14 с
awgshow amnezia-awg3 .awgshow 0                     # у awg3 никого
awgshow amnezia-awg3 .awgshow.kernel 0
rm -rf "$STATE_DIR/.kmod-copies"
KEYS0=$(cat "$USERS_FILE" "$STATE_DIR"/amnezia-awg*/wireguard_server_private_key.key | shasum | awk '{print $1}')
CONF0=$(grep -h "PrivateKey\|PublicKey\|ListenPort" "$STATE_DIR"/amnezia-awg*/awg0.conf | shasum | awk '{print $1}')
run ga_kmod_engine_set kernel
check "kmod-engine kernel: rc=0, оба профиля на модуле, want=kernel" \
    '[ "$rc" = 0 ] && [ "$(jget "[.profiles[].to] | join(\",\")")" = "kernel,kernel" ] && [ "$(ga_kmod_want)" = kernel ] && [ "$(jget .rolled_back)" = false ]' \
    "rc=$rc $GA_ERR_CODE ${out:0:500} $(errs)"
check "… по одному: сначала awg2, потом awg3 (порядок перезапусков)" \
    '[ "$(grep -o "DRY: docker restart amnezia-awg[0-9]" "$ERR" | paste -sd, -)" = "DRY: docker restart amnezia-awg2,DRY: docker restart amnezia-awg3" ]' "$(grep "docker restart" "$ERR")"
check "… awg2: 1 из 1 вернулся через 14 с; awg3: людей не было — без ожидания" \
    '[ "$(jget ".profiles[0].returned")" = 1 ] && [ "$(jget ".profiles[0].return_s")" = 14 ] && [ "$(jget ".profiles[1].note")" = no_clients ] && [ "$(clock)" = "$T0" ]' "$(jget .profiles)"
check "… файлы engine=kernel в копии, блок движка в start.sh; копия state/ 0700/0600" \
    '[ "$(cat "$STATE_DIR/amnezia-awg2/engine")" = kernel ] && grep -qxF "$GA_KMOD_BLOCK_BEGIN" "$STATE_DIR/amnezia-awg2/start.sh" && [ -d "$(jget .copies)/amnezia-awg2" ] && [ "$(stat -f %Lp "$(jget .copies)" 2>/dev/null || stat -c %a "$(jget .copies)")" = 700 ] && [ -z "$(find "$(jget .copies)" -type f ! -perm 600)" ]' \
    "$(jget .copies)"
check "… ключи не менялись: users.json и ключи сервера те же, в awg0.conf те же ключи, пиры и порт" \
    '[ "$(cat "$USERS_FILE" "$STATE_DIR"/amnezia-awg*/wireguard_server_private_key.key | shasum | awk "{print \$1}")" = "$KEYS0" ] && [ "$(grep -h "PrivateKey\|PublicKey\|ListenPort" "$STATE_DIR"/amnezia-awg*/awg0.conf | shasum | awk "{print \$1}")" = "$CONF0" ]'
check "… status: state=on, профили на модуле" '[ "$(_ga_kmod_status_field state)" = on ]' "$(ga_kmod_status_json)"
T1=$(clock)
awgshow amnezia-awg2 .awgshow.program 0           # на программе никто не вернётся
awgshow amnezia-awg3 .awgshow.program 0
run ga_kmod_engine_set program
check "kmod-engine program: никто не вернулся за 3 минуты — kmod_clients_lost, откат" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = kmod_clients_lost ] && [ "$(jget .rolled_back)" = true ] && [ $(( $(clock) - T1 )) -ge 180 ]' \
    "rc=$rc $GA_ERR_CODE clock+$(( $(clock) - T1 )) ${out:0:300}"
check "… awg2 вернулся на модуль (перезапуск дважды), awg3 не трогали, выбор — снова kernel" \
    '[ "$(grep -c "DRY: docker restart amnezia-awg2" "$ERR")" = 2 ] && ! grep -q "DRY: docker restart amnezia-awg3" "$ERR" && [ "$(cat "$STATE_DIR/amnezia-awg2/engine")" = kernel ] && [ "$(ga_kmod_want)" = kernel ]' \
    "$(grep "docker restart" "$ERR")"
check "… словами (WORD-01 — «пользователей», не «человек»): «пользователей на связи было 1, за 3 мин не вернулся ни один»" 'grep -qF "Профиль awg2: пользователей на связи было 1, за 3 мин не вернулся ни один" <<<"$GA_ERR_MSG" && ! grep -qF "человек" <<<"$GA_ERR_MSG"' "$GA_ERR_MSG"
bridge kmod-engine --engine=program
check "мост kmod-engine program (тот же откат): status=error, code и profiles в корне, rolled_back" \
    '[ "$rc" != 0 ] && [ "$(jget .status)" = error ] && [ "$(jget .code)" = kmod_clients_lost ] && [ "$(jget .rolled_back)" = true ] && [ "$(jget ".profiles[0].note")" = lost ]' "rc=$rc $(cut -c1-300 "$OUT")"
T2=$(clock)
awgshow amnezia-awg2 .awgshow $((T2 - 30))
awgshow amnezia-awg2 .awgshow.program $((T2 + 20))
run ga_kmod_engine_set program
check "kmod-engine program: вернулись — оба на программе (tun), want=program" \
    '[ "$rc" = 0 ] && [ "$(jget "[.profiles[].to] | join(\",\")")" = "program,program" ] && [ "$(ga_kmod_want)" = program ] && [ "$(jget ".profiles[0].return_s")" = 20 ]' \
    "rc=$rc $GA_ERR_CODE ${out:0:400}"
printf '%s\n' "$IPLINK_TUN" >"$CFS/amnezia-awg3/.iplink.kernel"   # модуль awg3 не подхватил
awgshow amnezia-awg2 .awgshow.kernel $(( $(clock) + 10 ))
run ga_kmod_engine_set kernel
check "kmod-engine kernel: awg3 после перезапуска не на модуле — kmod_engine_mismatch, откат обоих на программу" \
    '[ "$rc" != 0 ] && [ "$GA_ERR_CODE" = kmod_engine_mismatch ] && [ "$(cat "$STATE_DIR/amnezia-awg2/engine")" = program ] && [ "$(cat "$STATE_DIR/amnezia-awg3/engine")" = program ] && [ "$(ga_kmod_want)" = program ]' \
    "rc=$rc $GA_ERR_CODE $GA_ERR_MSG"
check "… откат в обратном порядке: awg3, потом awg2" \
    '[ "$(grep -o "DRY: docker restart amnezia-awg[0-9]" "$ERR" | paste -sd, - | sed "s/DRY: docker restart amnezia-//g")" = "awg2,awg3,awg3,awg2" ]' "$(grep "docker restart" "$ERR")"
check "копий state/ — три последние" '[ "$(find "$STATE_DIR/.kmod-copies" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d " ")" = 3 ]' \
    "$(ls "$STATE_DIR/.kmod-copies")"
printf '%s\n' "$IPLINK_KMOD" >"$CFS/amnezia-awg3/.iplink.kernel"

# ── Б9: режим «программа» в start.sh ─────────────────────────────────────────
section "Б9: start.sh — program при загруженном модуле поднимает amneziawg-go сам; kernel — awg-quick"
FAKE="$TMP/fakeroot"; FB="$TMP/fakebin"; mkdir -p "$FAKE/opt/amnezia/awg" "$FAKE/sys/class/net/eth0" "$FB"
for b in amneziawg-go awg awg-quick; do
    printf '#!/bin/sh\necho "%s $*" >>"$FAKE_CALLS"\n[ "%s $1" = "awg-quick strip" ] && cat "$2"\n[ "%s $1" = "awg setconf" ] && cat "$3" >"$FAKE_CALLS.setconf"\nexit 0\n' "$b" "$b" "$b" >"$FB/$b"
done
printf '#!/bin/sh\necho "ip $*" >>"$FAKE_CALLS"\ncase "$*" in "route show default") echo "default via 172.17.0.1 dev eth0" ;; esac\nexit 0\n' >"$FB/ip"
chmod +x "$FB"/*
printf '1500\n' >"$FAKE/sys/class/net/eth0/mtu"
ENGINE_BLOCK=$(sed -n "/^$GA_KMOD_BLOCK_BEGIN\$/,/^$GA_KMOD_BLOCK_END\$/p" "$STATE_DIR/amnezia-awg2/start.sh" | sed -e "s#/opt/amnezia#$FAKE/opt/amnezia#g" -e "s#/sys/class/net#$FAKE/sys/class/net#g")
runblock() { : >"$TMP/calls"; FAKE_CALLS="$TMP/calls" PATH="$FB:$PATH" sh -c "$ENGINE_BLOCK"; }
printf '[Interface]\nPrivateKey = K\nAddress = 10.8.2.0/24\nListenPort = 40000\nMTU = 1280\nJc = 5\n\n[Peer]\nPublicKey = P\nAllowedIPs = 10.8.2.1/32\n' \
    >"$FAKE/opt/amnezia/awg/awg0.conf"
printf 'program\n' >"$FAKE/opt/amnezia/awg/engine"; runblock
check "engine=program: ip link del, amneziawg-go awg0, setconf из strip, адрес, MTU 1280 up — и НЕ awg-quick up" \
    'grep -qx "amneziawg-go awg0" "$TMP/calls" && grep -q "^awg-quick strip " "$TMP/calls" && grep -qx "awg setconf awg0 /dev/stdin" "$TMP/calls" && grep -qx "ip address add 10.8.2.0/24 dev awg0" "$TMP/calls" && grep -qx "ip link set dev awg0 mtu 1280 up" "$TMP/calls" && ! grep -q "awg-quick up" "$TMP/calls"' \
    "$(cat "$TMP/calls")"
check "… awg setconf получил весь конфиг (маскировка — в новый интерфейс, это не syncconf)" 'grep -qx "Jc = 5" "$TMP/calls.setconf"' "$(cat "$TMP/calls.setconf" 2>/dev/null)"
sed -i.bak '/^MTU/d' "$FAKE/opt/amnezia/awg/awg0.conf"; runblock
check "… MTU в конфиге нет — как awg-quick: MTU карты по умолчанию − 80 (1420)" 'grep -qx "ip link set dev awg0 mtu 1420 up" "$TMP/calls"' "$(cat "$TMP/calls")"
printf 'kernel\n' >"$FAKE/opt/amnezia/awg/engine"; runblock
check "engine=kernel: awg-quick up, amneziawg-go сам не зовётся" 'grep -q "^awg-quick up " "$TMP/calls" && ! grep -q "^amneziawg-go" "$TMP/calls"' "$(cat "$TMP/calls")"
rm -f "$FAKE/opt/amnezia/awg/engine"; runblock
check "файла engine нет — как раньше: awg-quick up" 'grep -q "^awg-quick up " "$TMP/calls"' "$(cat "$TMP/calls")"
if [ -n "$DASH" ]; then
    check "весь start.sh своего профиля — POSIX: dash -n (goWARP проверяет его sh -n)" '"$DASH" -n "$STATE_DIR/amnezia-awg2/start.sh"'
else
    skp "dash нет — проверка POSIX пропущена"
fi

# ── Б9: goWARP в start.sh в обоих режимах ────────────────────────────────────
section "Б9: правки goWARP в start.sh целы в обоих режимах"
WBLOCK=$'# --- WARP-MANAGER BEGIN ---\n# gowarp-block 3\n(\n  flock -x 9 || exit 1\nif [ -f \x27/opt/warp/warp.conf\x27 ] && [ ! -f \x27/opt/warp/disabled\x27 ]; then\n  ip link show warp >/dev/null 2>&1 || env PATH=/opt/warp-tools/usr/bin:/usr/bin wg-quick up \x27/opt/warp/warp.conf\x27 9>&-\nfi\n) 9>/tmp/gowarp-runtime.lock\n# --- WARP-MANAGER END ---'
APPSH=$(cat "$ROOT/lib/upstream/awg/start.sh.upstream")
APPSH_W=$(printf '%s\n' "$APPSH" | WB="$WBLOCK" awk '/tail -f \/dev\/null/ && !d { print ENVIRON["WB"]; d = 1 } { print }')
# goWARP вставляет свой блок своим кодом (warp.sh awg_patch_start_sh): прочь прежний, перед первым tail -f или exit.
gowarp_patch() { sed '/^# --- WARP-MANAGER BEGIN ---$/,/^# --- WARP-MANAGER END ---$/d' | WB="$WBLOCK" awk '!i && (/tail -f \/dev\/null/ || /^[[:space:]]*exit([[:space:]]|$)/) { print ENVIRON["WB"]; i = 1 } { print } END { if (!i) print ENVIRON["WB"] }'; }
for eng in kernel program; do
    T=$(ga_kmod_start_sh_engine "$APPSH_W") && T=$(ga_amn_start_sh_mss "$T" awg0) && T=$(ga_amn_start_sh_isolation "$T" awg0 on)
    check "$eng: start.sh приложения с блоком goWARP — блок goWARP байт в байт, сразу перед tail -f; наши блоки — до него" \
        '[ "$(sed -n "/^# --- WARP-MANAGER BEGIN ---\$/,/^# --- WARP-MANAGER END ---\$/p" <<<"$T")" = "$WBLOCK" ] && [ "$(tail -n 1 <<<"$T")" = "tail -f /dev/null" ] && [ "$(grep -n "WARP-MANAGER END" <<<"$T" | cut -d: -f1)" = "$(( $(wc -l <<<"$T") - 1 ))" ] && [ "$(grep -n "goamnezia-engine END" <<<"$T" | cut -d: -f1)" -lt "$(grep -n "WARP-MANAGER BEGIN" <<<"$T" | cut -d: -f1)" ]' \
        "$(grep -n "WARP\|goamnezia\|tail" <<<"$T")"
    T2=$(gowarp_patch <<<"$T")
    check "$eng: goWARP переписывает свой блок — он встаёт на то же место, наши блоки целы и по одному" \
        '[ "$T2" = "$T" ] && [ "$(grep -cxF "$GA_KMOD_BLOCK_BEGIN" <<<"$T2")" = 1 ] && [ "$(grep -cxF "$GA_AMN_MSS_BEGIN" <<<"$T2")" = 1 ]' "$(diff <(echo "$T") <(echo "$T2") | head -8)"
    if [ -n "$DASH" ]; then
        check "$eng: sh -n (dash) после goWARP — чисто" '"$DASH" -n <(printf "%s\n" "$T2")'
    fi
done
mkdir -p "$CFS/amnezia-awg2/opt/amnezia"
printf '%s\n' "$APPSH_W" >"$CFS/amnezia-awg2/opt/amnezia/start.sh"
for eng in program kernel; do
    run ga_kmod_engine_write awg2 "$eng"
    check "ga_kmod_engine_write awg2 $eng: живой start.sh с блоком goWARP — в копии блок goWARP цел, файл engine=$eng" \
        '[ "$rc" = 0 ] && [ "$(sed -n "/^# --- WARP-MANAGER BEGIN ---\$/,/^# --- WARP-MANAGER END ---\$/p" "$STATE_DIR/amnezia-awg2/start.sh")" = "$WBLOCK" ] && [ "$(cat "$STATE_DIR/amnezia-awg2/engine")" = "$eng" ] && grep -qF "docker cp <stdin> amnezia-awg2:/opt/amnezia/awg/engine" "$ERR"' \
        "rc=$rc $(errs)"
done
rm -rf "$CFS/amnezia-awg2/opt"
printf '#!/bin/bash\necho custom\ntail -f /dev/null\n' >"$TMP/odd.sh"
check "start.sh без строки подъёма приложения — не узнан (rc 2), не правим" '! ga_kmod_start_sh_engine "$(cat "$TMP/odd.sh")" >/dev/null'

# ── Б10: видимость ───────────────────────────────────────────────────────────
section "Б10: status и profiles-status; «тихий откат» после перезагрузки в новое ядро"
run ga_kmod_engine_set kernel
bridge profiles-status
check "profiles-status: у профилей engine (фактический, живой опрос) и engine_want; kmod в ответе" \
    '[ "$(jget ".profiles[] | select(.profile == \"awg2\") | .engine")" = kernel ] && [ "$(jget ".profiles[] | select(.profile == \"awg2\") | .engine_want")" = kernel ] && [ "$(jget .kmod.state)" = on ]' "${out:0:600}"
bridge status --brief=on
check "status (brief): kmod.state=on, want=kernel, profiles.awg3.engine=kernel" \
    '[ "$(jget .kmod.state)" = on ] && [ "$(jget .kmod.want)" = kernel ] && [ "$(jget .kmod.profiles.awg3.engine)" = kernel ] && [ "$(jget .kmod.warn)" = false ]' "${out:0:600}"
# Перезагрузка в новое ядро без модуля под него: модуля в памяти нет, контейнеры ушли на программу сами.
export STUB_UNAME_R=6.8.0-146-generic
printf '7b1c7c1e-0000-4000-8000-000000000002\n' >"$TMP/proc/sys/kernel/random/boot_id"
rm -rf "$GOAMNEZIA_SYS_MODULE_DIR/amneziawg"; engine_fx amnezia-awg2 "$IPLINK_TUN"; engine_fx amnezia-awg3 "$IPLINK_TUN"
bridge status
check "status.kmod: state=fallback, code=no_module_for_kernel, причина словами с ядром" \
    '[ "$(jget .kmod.state)" = fallback ] && [ "$(jget .kmod.code)" = no_module_for_kernel ] && [ "$(jget .kmod.reason)" = "нет модуля для ядра 6.8.0-146-generic — профили работают программой; пересобрать? (Обслуживание → Ускорение AmneziaWG → 3)" ]' \
    "${out:0:600}"
bridge watch-tick
check "сторож: предупреждение один раз за загрузку — в журнал действий kmod_fallback с ядром" \
    '[ "$(jget .kmod.fallback)" = true ] && [ "$(grep -c "kmod_fallback" "$GOAMNEZIA_AUDIT_LOG")" = 1 ] && grep -q "kernel=6.8.0-146-generic" "$GOAMNEZIA_AUDIT_LOG"' "${out:0:400} $(errs)"
bridge watch-tick
check "… второй тик — без повтора" '[ "$(grep -c "kmod_fallback" "$GOAMNEZIA_AUDIT_LOG")" = 1 ]'
run ga_kmod_dash_warn
check "шапка SSH-меню: та же причина словами" 'grep -qF "нет модуля для ядра 6.8.0-146-generic" "$ERR"' "$(errs)"
unset STUB_UNAME_R
printf '7b1c7c1e-0000-4000-8000-000000000001\n' >"$TMP/proc/sys/kernel/random/boot_id"
modprobe amneziawg; engine_fx amnezia-awg2 "$IPLINK_KMOD"; engine_fx amnezia-awg3 "$IPLINK_KMOD"; rm -f "$(_ga_kmod_engines_file)"

# ── Б13: обновление с чужой меткой ───────────────────────────────────────────
section "Б13: обновление goAmnezia — модуль с меткой прежнего набора пересобирается отдельной службой"
_ga_kmod_state_edit 'del(.rebuild)'
printf 'version=3.1.20260906+kaskad-k31.0\n' >"$KO"; printf '3.1.20260906+kaskad-k31.0\n' >"$GOAMNEZIA_SYS_MODULE_DIR/amneziawg/version"
run ga_kmod_watch
check "модуль наш, метка k31.0 ≠ k31.1 — служба goamnezia-kmod-rebuild (kmod-install --rebuild=on), обновление не ждёт" \
    '[ "$(jget .rebuild)" = started ] && grep -qF "systemd-run --unit=goamnezia-kmod-rebuild" "$ERR" && grep -qF -- "--action=kmod-install --rebuild=on" "$ERR"' "${out:0:300} $(errs)"
_ga_kmod_state_edit '.rebuild = {label: $l, kernel: "6.8.0-48-generic", ok: false}' --arg l "$GA_KMOD_LABEL"
run ga_kmod_watch
check "пересборка на этом ядре уже не собралась — сам не повторяет" '[ "$(jget .rebuild)" = "" ] && ! grep -q "systemd-run" "$ERR"' "${out:0:300}"
_ga_kmod_state_edit 'del(.rebuild)'
mv "$SRC/.goamnezia" "$TMP/marker.saved"
run ga_kmod_watch
check "модуль не наш (исходники без нашей отметки — сосед) — не трогаем" '[ "$(jget .rebuild)" = "" ] && ! grep -q "systemd-run" "$ERR"' "${out:0:300}"
mv "$TMP/marker.saved" "$SRC/.goamnezia"
printf 'version=3.1.20260906+kaskad-k31.1\n' >"$KO"; printf '3.1.20260906+kaskad-k31.1\n' >"$GOAMNEZIA_SYS_MODULE_DIR/amneziawg/version"
printf '#!/bin/sh\nexit 0\n' >"$hook1"; rm -f "$STATE_DIR/amnezia-awg3/engine"
run ga_kmod_after_update
check "шаг миграции: хук — текстом этой версии, профиль без файла engine — за выбранным (kernel), без перезапуска" \
    'cmp -s "$hook1" "$TMP/hook1.saved" && [ "$(cat "$STATE_DIR/amnezia-awg3/engine")" = kernel ] && ! grep -q "docker restart" "$ERR"' "$(head -3 "$hook1")"
printf '{"profiles":{}}' >"$TMP/empty-state.json"
check "человек не выбирал (want пуст) — сторож молчит и ничего не переключает" \
    '( _ga_kmod_state() { printf "{}"; }; [ "$(ga_kmod_watch)" = null ] )'

# ── Б14: удаление ────────────────────────────────────────────────────────────
section "Б14: удаление — сосед Kaskad пользуется модулем: модуль и DKMS не трогаем"
for c in amnezia-awg2 amnezia-awg3; do awgshow "$c" .awgshow 0; awgshow "$c" .awgshow.program 0; done
mkdir -p "$TMP/etc/kaskad/gw"; printf '{"enabled": true}\n' >"$TMP/etc/kaskad/gw/awg-kmod.json"
run ga_kmod_remove
check "kept_for_neighbour=true: профили на программе, modprobe -r и dkms remove не звали, исходники на месте" \
    '[ "$rc" = 0 ] && [ "$(jget .kept_for_neighbour)" = true ] && [ "$(jget .removed)" = false ] && ! grep -q "^modprobe -r\|^dkms remove" "$STUB_LOG" && [ -d "$SRC" ] && [ -d "$GOAMNEZIA_SYS_MODULE_DIR/amneziawg" ] && [ "$(cat "$STATE_DIR/amnezia-awg2/engine")" = program ]' \
    "rc=$rc ${out:0:300} $(cat "$STUB_LOG")"
check "… своё убрано: хуки и отметка; автозагрузка осталась соседу" \
    '[ ! -e "$hook1" ] && [ ! -e "$hook2" ] && [ ! -e "$SRC/.goamnezia" ] && [ -f "$TMP/etc/modules-load.d/amneziawg.conf" ]'
rm -rf "$TMP/etc/kaskad"
STUB_IP_LINKS=$'1: lo: <LOOPBACK> mtu 65536\n7: kgw-awg0: <POINTOPOINT,NOARP,UP,LOWER_UP> mtu 1280' run ga_kmod_kaskad_uses
check "интерфейс соседа kgw-awg0 в памяти — тоже «пользуется»" '[ "$rc" = 0 ]'
section "Б14: удаление без соседа — порядок рецепта, пакеты словами"
printf 'goamnezia\n' >"$SRC/.goamnezia"; _ga_kmod_hooks_install
run ga_kmod_remove
check "removed=true: modprobe -r раньше dkms remove --all; исходники, хуки, автозагрузка — прочь" \
    '[ "$rc" = 0 ] && [ "$(jget .removed)" = true ] && [ "$(grep -n "^modprobe -r" "$STUB_LOG" | cut -d: -f1)" -lt "$(grep -n "^dkms remove -m amneziawg -v $KVER --all" "$STUB_LOG" | cut -d: -f1)" ] && [ ! -d "$SRC" ] && [ ! -e "$hook1" ] && [ ! -f "$TMP/etc/modules-load.d/amneziawg.conf" ]' \
    "rc=$rc ${out:0:300} $(cat "$STUB_LOG")"
check "… пакеты, что ставила goAmnezia, — строкой apt remove, сами не удалены" \
    '[ "$(jget .command)" = "apt remove dkms gcc gcc-13 linux-headers-6.8.0-48-generic linux-headers-generic make patch" ] && ! grep -q "apt-get .*remove" "$STUB_LOG" && grep -qF "убрать: apt remove" "$ERR"' "${out:0:400}"
check "… state: installed=false, want=program; kmod-status — off" \
    '[ "$(jq -r .installed "$(ga_kmod_state_file)")" = false ] && [ "$(ga_kmod_want)" = program ] && [ "$(_ga_kmod_status_field state)" = off ]' \
    "$(jq -c '{installed, want}' "$(ga_kmod_state_file)") $(ga_kmod_status_json | jq -c '{state, code, loaded, installed, installed_version}')"
STUB_MOD_INUSE=1 run ga_kmod_remove
check "модуля нет в памяти и исходников нет — повторное удаление проходит без modprobe -r" '[ "$rc" = 0 ] && ! grep -q "^modprobe -r" "$STUB_LOG"' "rc=$rc $(cat "$STUB_LOG")"

# ── MSS ──────────────────────────────────────────────────────────────────────
section "MSS по MTU туннеля: правила один раз, после двух стартов дублей нет, iptables-save"
IPTBIN="$TMP/iptbin"; mkdir -p "$IPTBIN"
for n in iptables ip6tables iptables-save ip6tables-save; do ln -sf "$ROOT/tests/fixtures/fake_iptables.sh" "$IPTBIN/$n"; done
printf '#!/bin/sh\n[ "$1 $2 $3 $4" = "-o link show dev" ] && echo "5: $5: <POINTOPOINT,NOARP,UP,LOWER_UP> mtu ${FAKE_MTU:-1280} qdisc noqueue state UNKNOWN"\nexit 0\n' >"$IPTBIN/ip"
chmod +x "$IPTBIN/ip"
netns() { FAKE_IPT_DIR="$1" PATH="$IPTBIN:$PATH" sh -c "$2"; }
SS=$(ga_amn_render_start_sh awg2)
RULES=$(printf '%s\n' "$SS" | sed -n '/^iptables /p; /^# --- goamnezia-mss BEGIN ---$/,/^# --- goamnezia-mss END ---$/p')
N1="$TMP/ns1"; mkdir -p "$N1"
netns "$N1" "$RULES"; netns "$N1" "$RULES"
SAVE=$(FAKE_IPT_DIR="$N1" "$IPTBIN/iptables-save")
check "iptables-save: *mangle — set-mss 1240 из awg0 и clamp-mss-to-pmtu в awg0, по одному" \
    '[ "$(grep -cxF -- "-A FORWARD -i awg0 -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss 1240" <<<"$SAVE")" = 1 ] && [ "$(grep -cxF -- "-A FORWARD -o awg0 -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu" <<<"$SAVE")" = 1 ] && grep -qx "\*mangle" <<<"$SAVE"' "$SAVE"
check "после двух стартов дублей нет ни в filter, ни в nat, ни в mangle" \
    '[ -z "$(sort "$N1/filter" | uniq -d)" ] && [ -z "$(sort "$N1/nat" | uniq -d)" ] && [ -z "$(sort "$N1/mangle" | uniq -d)" ]' "$SAVE"
check "ip6tables есть — свои правила с −60 (1220)" \
    'grep -qxF -- "-A FORWARD -i awg0 -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss 1220" "$N1/v6/mangle" && [ "$(wc -l <"$N1/v6/mangle" | tr -d " ")" = 2 ]' "$(cat "$N1/v6/mangle" 2>/dev/null)"
FAKE_MTU=1360 netns "$N1" "$(ga_amn_mss_cmd awg0)"
check "смена MTU (1360) командой в контейнере: 1240 снято, 1320 одно" \
    '! grep -q "set-mss 1240" "$N1/mangle" && [ "$(grep -c "set-mss 1320" "$N1/mangle")" = 1 ] && [ "$(grep -c "clamp-mss-to-pmtu" "$N1/mangle")" = 1 ]' "$(cat "$N1/mangle")"
check "блок MSS в start.sh: после правил iptables приложения и перед tail -f; без строк, начинающихся с iptables/tail/exit" \
    '[ "$(grep -n "goamnezia-mss BEGIN" <<<"$SS" | cut -d: -f1)" -gt "$(grep -n "^iptables " <<<"$SS" | tail -1 | cut -d: -f1)" ] && ! sed -n "/^# --- goamnezia-mss BEGIN ---\$/,/^# --- goamnezia-mss END ---\$/p" <<<"$SS" | grep -Eq "^(iptables|tail|exit)( |\$)"' \
    "$(grep -n "goamnezia\|^iptables\|tail" <<<"$SS" | tail -6)"
check "оба режима (kernel и program) — один и тот же start.sh: блок MSS вне блока движка" \
    '[ "$(grep -n "goamnezia-engine END" <<<"$SS" | cut -d: -f1)" -lt "$(grep -n "goamnezia-mss BEGIN" <<<"$SS" | cut -d: -f1)" ]'
section "MSS: уже поставленным контейнерам — при обновлении и рестарте тем же путём, что изоляция"
SD2="$STATE_DIR/amnezia-awg2"
# start.sh прежней версии: шаблон без блока MSS, с блоком goWARP перед tail -f.
printf '%s\n' "$SS" | awk '/^# --- goamnezia-mss BEGIN ---$/ {s=1} s { if (/^# --- goamnezia-mss END ---$/) s=0; next } { print }' \
    | WB="$WBLOCK" awk '/^tail -f/ { print ENVIRON["WB"] } { print }' >"$SD2/start.sh"
run ga_amn_isolation_apply awg2
check "start.sh прежней версии (без блока): блок MSS — в копию и в контейнер, правило — командой в контейнере" \
    '[ "$rc" = 0 ] && [ "$(grep -cxF "$GA_AMN_MSS_BEGIN" "$SD2/start.sh")" = 1 ] && grep -qF "docker cp <stdin> amnezia-awg2:/opt/amnezia/start.sh" "$ERR" && grep -q "TCPMSS --set-mss" "$ERR"' \
    "rc=$rc $(errs)"
check "… блок goWARP в копии цел и перед tail -f; блок MSS — перед ним" \
    '[ "$(sed -n "/^# --- WARP-MANAGER BEGIN ---\$/,/^# --- WARP-MANAGER END ---\$/p" "$SD2/start.sh")" = "$WBLOCK" ] && [ "$(tail -n 1 "$SD2/start.sh")" = "tail -f /dev/null" ] && [ "$(grep -n "goamnezia-mss END" "$SD2/start.sh" | cut -d: -f1)" -lt "$(grep -n "WARP-MANAGER BEGIN" "$SD2/start.sh" | cut -d: -f1)" ]' \
    "$(grep -n "goamnezia\|WARP\|tail" "$SD2/start.sh")"
run ga_amn_isolation_apply awg2
check "… повтор (тик сторожа): start.sh не пишется, команду MSS не шлём — правило ставит сам start.sh" \
    '[ "$rc" = 0 ] && ! grep -qF "docker cp <stdin> amnezia-awg2:/opt/amnezia/start.sh" "$ERR" && ! grep -q "TCPMSS" "$ERR"' "$(errs)"
run amnezia_profile_restart awg2
check "… рестарт профиля: start.sh уже с блоком — правило поставит он сам" '[ "$rc" = 0 ]' "rc=$rc $(errs)"
ga_config_merge '{"profiles":{"awg3":{"imported":true}}}' >/dev/null 2>&1
cp "$STATE_DIR/amnezia-awg3/start.sh" "$TMP/awg3-start.saved"
run ga_amn_isolation_apply awg3
check "контейнер приложения: start.sh не трогаем, правило MSS — командой (и так каждым тиком)" \
    '[ "$rc" = 0 ] && cmp -s "$TMP/awg3-start.saved" "$STATE_DIR/amnezia-awg3/start.sh" && grep -q "TCPMSS --set-mss" "$ERR" && ! grep -qF "docker cp <stdin> amnezia-awg3:/opt/amnezia/start.sh" "$ERR"' "$(errs)"
run ga_amn_isolation_on_start amnezia-awg3
check "… старт контейнера приложения (служба изоляции) — правило MSS сразу" '[ "$rc" = 0 ] && grep -q "TCPMSS" "$ERR"' "$(errs)"
ga_config_merge '{"profiles":{"awg3":{"imported":false}}}' >/dev/null 2>&1
run ga_amn_mtu_live awg2 1360
check "смена MTU на лету: та же команда несёт и MSS (снять прежнее значение, поставить новое)" \
    'grep -q "ip link set dev awg0 mtu 1360" "$ERR" && grep -q "TCPMSS --set-mss" "$ERR"' "$(errs)"
if [ -n "$DASH" ]; then
    check "команда MSS и блок — POSIX (dash -n)" '"$DASH" -n <(ga_amn_mss_cmd awg0) && "$DASH" -n "$SD2/start.sh"'
fi

# ── KMODB-01: экран после удаления ───────────────────────────────────────────
# Живая проверка 08.10: после «Удалить модуль» экран «Ускорение AmneziaWG» писал «Модуль ядра не ставился», хотя он
# стоял и удалён. Модуль удалён выше (Б14) — экран и kmod-status говорят «удалён».
section "KMODB-01: экран после «Удалить модуль» — «модуль удалён», а не «не ставился»"
run _ga_kmod_screen_state
check "экран: «Модуль ядра удалён … — профили работают программой amneziawg-go; пункт 1 поставит его снова», без «не ставился»" \
    'grep -qF "Модуль ядра удалён" "$OUT" && grep -qF "пункт 1 поставит его снова" "$OUT" && ! grep -qF "не ставился" "$OUT"' \
    "$(cat "$OUT")"
bridge kmod-status
check "kmod-status: state=off и removed_at — когда удалили (панели и боту — то же слово)" \
    '[ "$(jget .kmod.state)" = off ] && [[ "$(jget .kmod.removed_at)" =~ ^20[0-9]{2}-[0-9]{2}-[0-9]{2}T ]]' "${out:0:500}"
out=$( ( _ga_kmod_state() { printf '{}'; }; _ga_kmod_screen_state ) 2>&1 )
check "модулем не занимались вовсе — по-прежнему «Модуль ядра не ставился.»" 'grep -qF "Модуль ядра не ставился." <<<"$out"' "$out"

# ── KMODB-02: «✓ Профили работают» — один раз ────────────────────────────────
section "KMODB-02: «✓ Профили работают: …» — один раз в пунктах 1, 2 и 4"
reset_host
run ga_kmod_install off off
check "модуль снова поставлен (стенд): rc=0, профили на программе" '[ "$rc" = 0 ] && [ "$(ga_kmod_want)" = program ]' "rc=$rc $GA_ERR_CODE $GA_ERR_MSG"
kb_fixtures() {   # kb_fixtures <рукопожатие awg2 до> <после на модуле> <после на программе> — awg3 без людей
    local c
    for c in amnezia-awg2 amnezia-awg3; do
        printf '%s\n' "$IPLINK_KMOD" >"$CFS/$c/.iplink.kernel"; printf '%s\n' "$IPLINK_TUN" >"$CFS/$c/.iplink.program"
        rm -f "$CFS/$c/.awgshow"*
    done
    awgshow amnezia-awg2 .awgshow "$1"; awgshow amnezia-awg2 .awgshow.kernel "$2"; awgshow amnezia-awg2 .awgshow.program "$3"
    for c in .awgshow .awgshow.kernel .awgshow.program; do awgshow amnezia-awg3 "$c" 0; done
}
engine_fx amnezia-awg2 "$IPLINK_TUN"; engine_fx amnezia-awg3 "$IPLINK_TUN"
T3=$(clock); kb_fixtures $((T3 - 60)) $((T3 + 14)) $((T3 + 16))
run _ga_kmod_engine_interactive kernel
check "пункт 1 (перевод на модуль): «✓ Профили работают: модуль ядра» — ровно одна строка, строка профиля — по разу" \
    '[ "$rc" = 0 ] && [ "$(grep -c "Профили работают" "$ERR")" = 1 ] && grep -qF "✓ Профили работают: модуль ядра" "$ERR" && [ "$(grep -c "awg2: модуль ядра; вернулись 1 из 1" "$ERR")" = 1 ] && [ "$(grep -c "awg3: модуль ядра; пользователей на связи не было" "$ERR")" = 1 ]' \
    "rc=$rc $(grep -n "Профили работают\|awg[23]:" "$ERR")"
T3=$(clock); kb_fixtures $((T3 - 60)) $((T3 + 14)) $((T3 + 16))
run _ga_kmod_engine_interactive program
check "пункт 2 (вернуть программу): «✓ Профили работают: программа amneziawg-go» — ровно одна строка" \
    '[ "$rc" = 0 ] && [ "$(grep -c "Профили работают" "$ERR")" = 1 ] && grep -qF "✓ Профили работают: программа amneziawg-go" "$ERR" && [ "$(grep -c "awg2: программа amneziawg-go; вернулись 1 из 1" "$ERR")" = 1 ]' \
    "rc=$rc $(grep -n "Профили работают\|awg[23]:" "$ERR")"
_kb_confirm=$(declare -f confirm); confirm() { return 0; }
run _ga_kmod_remove_interactive
eval "$_kb_confirm"
check "пункт 4 (удалить): «профили работают» — одна строка (итог перевода), итог удаления — «Модуль ядра удалён.»" \
    '[ "$rc" = 0 ] && [ "$(grep -c -e "Профили работают" -e "профили работают" "$ERR")" = 1 ] && grep -qF "✓ Модуль ядра удалён." "$ERR"' \
    "rc=$rc $(grep -n -e "Профили работают" -e "профили работают" -e "удалён" "$ERR")"

# ── KMODB-03: «Пересобрать» загружает новый модуль ───────────────────────────
# Живая проверка 08.10: «Пересобрать» обещал перевод на программу и обратно, а только собирал на диск — в памяти
# оставался прежний модуль до перезапуска, а экран писал «собран и загружен». Теперь — как обещано, с теми же копиями
# state/ и проверкой людей, что у kmod-engine; итог словами — что в памяти.
section "KMODB-03: «Пересобрать» — профили на программу, modprobe -r, новый модуль, обратно на модуль"
reset_host
run ga_kmod_install off off
engine_fx amnezia-awg2 "$IPLINK_TUN"; engine_fx amnezia-awg3 "$IPLINK_TUN"
T4=$(clock); kb_fixtures $((T4 - 60)) $((T4 + 14)) $((T4 + 16))
run ga_kmod_engine_set kernel
check "до пересборки: модуль поставлен, профили на модуле (state=on)" '[ "$rc" = 0 ] && [ "$(_ga_kmod_status_field state)" = on ]' \
    "rc=$rc $GA_ERR_CODE $(ga_kmod_status_json | jq -c '{state, code}')"
T4=$(clock); kb_fixtures $((T4 - 30)) $((T4 + 12)) $((T4 + 10))
rm -rf "$STATE_DIR/.kmod-copies"
: >"$GOAMNEZIA_AUDIT_LOG"
run ga_kmod_install on off
check "rc=0, reload=swapped: в памяти — свежесобранный модуль с меткой набора" \
    '[ "$rc" = 0 ] && [ "$(jget .reload)" = swapped ] && [ "$(jget .loaded)" = 3.1.20260906+kaskad-k31.1 ] && [ "$(jget .on_disk)" = 3.1.20260906+kaskad-k31.1 ]' \
    "rc=$rc $GA_ERR_CODE $GA_ERR_MSG ${out:0:400}"
check "… модуль выгружен и загружен заново: dkms build → install → modprobe -r → modprobe amneziawg" \
    '[ "$(grep -E "^(dkms (build|install)|modprobe)" "$STUB_LOG" | cut -d" " -f1,2 | paste -sd, -)" = "dkms build,dkms install,modprobe -r,modprobe amneziawg" ]' \
    "$(cat "$STUB_LOG")"
check "… по журналу действий: сборка → профили на программу → выгрузка → загрузка → самопроверка → профили на модуль" \
    '[ "$(jq -R -r "fromjson? | select(.action | test(\"^kmod-\")) | .action + \" \" + (.details | split(\" \")[0])" "$GOAMNEZIA_AUDIT_LOG" | grep -E "step=(build|unload|load|selftest)|kmod-engine to=" | paste -sd, -)" = "kmod-rebuild step=build,kmod-engine to=program,kmod-rebuild step=unload,kmod-rebuild step=load,kmod-rebuild step=selftest,kmod-engine to=kernel" ]' \
    "$(jq -R -r 'fromjson? | select(.action | test("^kmod-")) | .action + " " + .details' "$GOAMNEZIA_AUDIT_LOG")"
check "… перезапуски по одному: awg2, awg3 на программу, затем awg2, awg3 на модуль; копий state/ — две (по переводу)" \
    '[ "$(grep -o "DRY: docker restart amnezia-awg[0-9]" "$ERR" | sed "s/DRY: docker restart amnezia-//" | paste -sd, -)" = "awg2,awg3,awg2,awg3" ] && [ "$(find "$STATE_DIR/.kmod-copies" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d " ")" = 2 ]' \
    "$(grep "docker restart" "$ERR") $(ls "$STATE_DIR/.kmod-copies" 2>/dev/null)"
check "… люди проверены на обоих переводах (awg2: вернулся через 10 с на программе и через 12 с на модуле)" \
    'grep -qF "awg2: программа amneziawg-go; вернулись 1 из 1, первый — через 10 с" "$ERR" && grep -qF "awg2: модуль ядра; вернулись 1 из 1, первый — через 12 с" "$ERR"' \
    "$(grep -n "awg2:" "$ERR")"
check "… итог словами — что в памяти: «пересобран и загружен заново: в памяти 3.1.20260906+kaskad-k31.1 (ядро 6.8.0-48-generic), профили снова на модуле»; «Профили работают» промежуточных переводов не печатается" \
    'grep -qF "Модуль ядра пересобран и загружен заново: в памяти 3.1.20260906+kaskad-k31.1 (ядро 6.8.0-48-generic), профили снова на модуле." "$ERR" && ! grep -q "Профили работают" "$ERR" && [ "$(_ga_kmod_status_field state)" = on ] && [ "$(ga_kmod_want)" = kernel ]' \
    "$(errs)"
run ga_kmod_engine_set program
run ga_kmod_install on off
check "профили на программе: пересборка выгружает и загружает модуль без перезапусков (reload=reloaded)" \
    '[ "$rc" = 0 ] && [ "$(jget .reload)" = reloaded ] && grep -q "^modprobe -r amneziawg" "$STUB_LOG" && ! grep -q "docker restart" "$ERR" && grep -qF "Модуль ядра пересобран и загружен заново: в памяти 3.1.20260906+kaskad-k31.1 (ядро 6.8.0-48-generic)." "$ERR"' \
    "rc=$rc ${out:0:300} $(cat "$STUB_LOG") $(errs)"
mkdir -p "$TMP/etc/kaskad/gw"; printf '{"enabled": true}\n' >"$TMP/etc/kaskad/gw/awg-kmod.json"
run ga_kmod_install on off
check "модулем пользуется сосед Kaskad: выгружать нельзя — новый на диске, в памяти прежний, сказано словами (reload=neighbour)" \
    '[ "$rc" = 0 ] && [ "$(jget .reload)" = neighbour ] && ! grep -q "^modprobe -r" "$STUB_LOG" && grep -qF "им пользуется сосед Kaskad" "$ERR" && grep -qF "Новый загрузится при следующей перезагрузке сервера." "$ERR"' \
    "rc=$rc ${out:0:300} $(cat "$STUB_LOG") $(errs)"
rm -rf "$TMP/etc/kaskad"
# Новый модуль не прошёл самопроверку посреди перевода: профили уже на программе — экран говорит это, а не «связь не
# прерывалась»; непроверенный модуль выгружен.
T5=$(clock); kb_fixtures $((T5 - 60)) $((T5 + 14)) $((T5 + 16))
run ga_kmod_engine_set kernel
T5=$(clock); kb_fixtures $((T5 - 30)) $((T5 + 12)) $((T5 + 10))
_kb_confirm=$(declare -f confirm); confirm() { return 0; }
GOAMNEZIA_KMOD_SELFTEST_FIXTURE="$TMP/st-fail.txt" run _ga_kmod_rebuild_interactive
eval "$_kb_confirm"
# Проверяльщик 15.10 (REVIEW-CORE-1013, находка 3): модуль не годится — совет «пункт 1 переведёт их снова» привёл бы к
# тому же отказу; экран говорит, что модуль не годится, и что у сервера теперь выбрана программа.
check "самопроверка нового модуля не прошла посреди пересборки: профили на программе, модуль выгружен, экран — «не годится», «пользователи переподключились к ней сами», «выбрано: программа», без «Пункт 1 переведёт»" \
    '[ "$rc" != 0 ] && grep -qF "Новый модуль не годится — включать его снова бесполезно" "$ERR" && grep -qF "пользователи переподключились к ней сами" "$ERR" && ! grep -qF "люди переподключились" "$ERR" && grep -qF "У сервера теперь выбрано: программа amneziawg-go." "$ERR" && ! grep -qF "Пункт 1 переведёт" "$ERR" && ! grep -qF "связь у пользователей не прерывалась" "$ERR" && [ ! -d "$GOAMNEZIA_SYS_MODULE_DIR/amneziawg" ] && [ "$(cat "$STATE_DIR/amnezia-awg2/engine")" = program ]' \
    "rc=$rc $(errs) $(cat "$STATE_DIR/amnezia-awg2/engine")"
# То же через мост (MCP, служба пересборки): прежде ответ — только code и message упавшего шага, о том, что профили и
# выбор сервера теперь на программе, — ни слова.
T5=$(clock); kb_fixtures $((T5 - 60)) $((T5 + 14)) $((T5 + 16))
run ga_kmod_engine_set kernel
T5=$(clock); kb_fixtures $((T5 - 30)) $((T5 + 12)) $((T5 + 10))
GOAMNEZIA_KMOD_SELFTEST_FIXTURE="$TMP/st-fail.txt" bridge kmod-install --rebuild=on
check "мост kmod-install --rebuild=on, самопроверка не прошла: error, kmod_selftest_failed; message — «не годится» и «выбрано: программа»; engine=program, want=program, swap_started=true" \
    '[ "$(jget .status)" = error ] && [ "$(jget .code)" = kmod_selftest_failed ] && grep -qF "Новый модуль не годится" <<<"$(jget .message)" && grep -qF "У сервера теперь выбрано: программа amneziawg-go." <<<"$(jget .message)" && ! grep -qF "переведёт их на модуль снова" <<<"$(jget .message)" && [ "$(jget .engine)" = program ] && [ "$(jget .want)" = program ] && [ "$(jget .swap_started)" = true ]' \
    "rc=$rc ${out:0:700} $(errs)"
# Проверяльщик 15.10 (REVIEW2-CORE-1013, R2-3): служба пересборки после обновления отвечает только в журнал юнита —
# «где профили и что выбрано» должны лечь в .failed.reason (kmod-status, шапка SSH), иначе перевод на программу молчаливый.
km_st=$(ga_kmod_status_json); km_dash=$(ga_kmod_dash_warn 2>&1)
check "R2-3/R4-1: после неудачной пересборки (самопроверка) — «где профили» в failed.left (не в самой ошибке failed.reason), kmod-status.failed.reason и .reason: слова шага, «не годится», «выбрано: программа»; state=failed; шапка SSH — те же слова" \
    'grep -qF "Новый модуль не годится" <<<"$(_ga_kmod_state_get ".failed.left")" && grep -qF "У сервера теперь выбрано: программа amneziawg-go." <<<"$(jq -r .failed.reason <<<"$km_st")" && ! grep -qF "У сервера теперь выбрано" <<<"$(_ga_kmod_state_get ".failed.reason")" && [ "$(jq -r .state <<<"$km_st")" = failed ] && grep -qF "Новый модуль не годится" <<<"$(jq -r .reason <<<"$km_st")" && grep -qF "У сервера теперь выбрано: программа" <<<"$km_dash"' \
    "$(_ga_kmod_state | jq -c .failed) $(jq -c '{state, reason}' <<<"$km_st") $km_dash"
T5=$(clock); kb_fixtures $((T5 - 60)) $((T5 + 14)) $((T5 + 16))
run ga_kmod_engine_set kernel
T5=$(clock); kb_fixtures $((T5 - 30)) $((T5 + 12)) $((T5 + 10))
STUB_MOD_INUSE=1 bridge kmod-install --rebuild=on
check "мост: модуль не выгрузился (kmod_in_use) посреди перевода — профили на программе, выбор — программа, как вернуть (пункт меню по SSH, без имён действий); engine=program" \
    '[ "$(jget .status)" = error ] && [ "$(jget .code)" = kmod_in_use ] && grep -qF "Новый модуль не загружен: профили работают программой amneziawg-go" <<<"$(jget .message)" && grep -qF "У сервера теперь выбрано: программа amneziawg-go." <<<"$(jget .message)" && grep -qF "Перевести их на модуль снова — «Включить (перевести на модуль)» в «Обслуживание → Ускорение AmneziaWG (модуль ядра)» по SSH." <<<"$(jget .message)" && ! grep -qF "kmod-engine" <<<"$(jget .message)" && [ "$(jget .engine)" = program ] && [ "$(jget .want)" = program ]' \
    "rc=$rc ${out:0:700} $(errs)"

# ── REVIEW2-CORE-1013: провал на возвращении профилей (switch) и на первом переводе (swap) ─────────────────────────
section "REVIEW2-CORE-1013: пересборка — профили не вернулись на новый модуль (switch); не ушли на программу (swap)"
# R2-2: новый модуль загружен и прошёл самопроверку, но на модуле никто не вернулся за 3 минуты — откат на программу.
# Прежде: «Новый модуль не загружен… переведёт их на модуль снова» — модуль в памяти, а совет вёл к той же потере связи.
T6=$(clock); kb_fixtures $((T6 - 60)) $((T6 + 14)) $((T6 + 16))
run ga_kmod_engine_set kernel
T6=$(clock); kb_fixtures $((T6 - 30)) 0 $((T6 + 10))     # на программе вернулся через 10 с, на новом модуле — никто
bridge kmod-install --rebuild=on
check "R2-2: мост, профили не вернулись на новый модуль (kmod_clients_lost, шаг switch): «загружен и прошёл самопроверку», «повторять сразу бесполезно», без «не загружен» и совета перевести снова; engine=program, want=program; модуль в памяти" \
    '[ "$(jget .status)" = error ] && [ "$(jget .code)" = kmod_clients_lost ] && grep -qF "Новый модуль загружен и прошёл самопроверку, но вернуть профили на него не удалось" <<<"$(jget .message)" && grep -qF "Повторять перевод сразу бесполезно" <<<"$(jget .message)" && grep -qF "У сервера теперь выбрано: программа amneziawg-go." <<<"$(jget .message)" && ! grep -qF "не загружен" <<<"$(jget .message)" && ! grep -qF "на модуль снова" <<<"$(jget .message)" && [ "$(jget .engine)" = program ] && [ "$(jget .want)" = program ] && [ -d "$GOAMNEZIA_SYS_MODULE_DIR/amneziawg" ]' \
    "rc=$rc ${out:0:900} $(errs)"
check "R2-2/R2-3: failed.step=switch, failed.reason — слова отказа и «загружен и прошёл самопроверку… выбрано: программа»" \
    '[ "$(_ga_kmod_state_get ".failed.step")" = switch ] && r22=$(ga_kmod_status_json | jq -r .failed.reason) && grep -qF "не вернулся ни один" <<<"$r22" && grep -qF "Новый модуль загружен и прошёл самопроверку" <<<"$r22" && grep -qF "У сервера теперь выбрано: программа amneziawg-go." <<<"$r22"' \
    "$(_ga_kmod_state | jq -c .failed)"
T6=$(clock); kb_fixtures $((T6 - 60)) $((T6 + 14)) $((T6 + 16))
run ga_kmod_engine_set kernel
T6=$(clock); kb_fixtures $((T6 - 30)) 0 $((T6 + 10))
_kb_confirm=$(declare -f confirm); confirm() { return 0; }
run _ga_kmod_rebuild_interactive
eval "$_kb_confirm"
check "R2-2: SSH «Пересобрать», тот же отказ на switch — «загружен и прошёл самопроверку», «повторять сразу бесполезно», без «Пункт 1 переведёт» и «не загружен»" \
    '[ "$rc" != 0 ] && grep -qF "Новый модуль загружен и прошёл самопроверку, но вернуть профили на него не удалось" "$ERR" && grep -qF "Повторять перевод сразу бесполезно" "$ERR" && ! grep -qF "Пункт 1 переведёт" "$ERR" && ! grep -qF "Новый модуль не загружен" "$ERR"' \
    "rc=$rc $(errs)"
# Слова для шага switch по разным причинам (R2-2): не люди и не движок (например, перезапуск не удался) — повторить можно.
_kb_kp=$(declare -f _ga_kmod_kernel_profiles)
_ga_kmod_kernel_profiles() { printf '%s' "${KB_ONK:-}"; }
w_restart=$(GA_KMOD_SWAP_STARTED=1 GA_KMOD_STEP=switch GA_ERR_CODE=restart_failed _ga_kmod_left_words bridge)
w_mismatch=$(GA_KMOD_SWAP_STARTED=1 GA_KMOD_STEP=switch GA_ERR_CODE=kmod_engine_mismatch _ga_kmod_left_words ssh)
w_onk=$(KB_ONK="awg2" GA_KMOD_SWAP_STARTED=1 GA_KMOD_STEP=switch GA_ERR_CODE=kmod_clients_lost _ga_kmod_left_words ssh)
w_swap=$(GA_KMOD_SWAP_STARTED=1 GA_KMOD_STEP=swap GA_ERR_CODE=kmod_clients_lost _ga_kmod_left_words ssh)
eval "$_kb_kp"
check "R2-2: switch + restart_failed — «загружен и прошёл самопроверку» и как перевести снова (пункт меню по SSH); switch + kmod_engine_mismatch — «повторять сразу бесполезно»" \
    'grep -qF "Новый модуль загружен и прошёл самопроверку" <<<"$w_restart" && grep -qF "Перевести их на модуль снова" <<<"$w_restart" && grep -qF "Повторять перевод сразу бесполезно" <<<"$w_mismatch" && ! grep -qF "Пункт 1" <<<"$w_mismatch"' \
    "restart: $w_restart | mismatch: $w_mismatch"
check "R2-2: switch, а часть профилей осталась на модуле — «на модуле — awg2» (новом), а не «на прежнем модуле»; R2-4: шаг swap без профилей на модуле — не «не годится»; R3-3: и не «пользователи переподключились к программе» — откат тоже не удался, проверьте профили" \
    'grep -qF "на модуле — awg2, остальные работают программой" <<<"$w_onk" && ! grep -qF "прежнем модуле" <<<"$w_onk" && grep -qF "перевести профили на программу amneziawg-go не удалось, и вернуть их на прежний модуль тоже не удалось" <<<"$w_swap" && grep -qF "Проверьте профили" <<<"$w_swap" && ! grep -qF "переподключились" <<<"$w_swap" && ! grep -qF "не годится" <<<"$w_swap"' \
    "onk: $w_onk | swap: $w_swap"
# R2-4: отказал уже первый перевод на программу (на программе никто не вернулся) — откат на прежний модуль. Шаг — swap,
# а не load: новый модуль даже не загружали, «не годится» было бы неправдой.
T6=$(clock); kb_fixtures $((T6 - 60)) $((T6 + 14)) $((T6 + 16))
run ga_kmod_engine_set kernel
T6=$(clock); kb_fixtures $((T6 - 30)) $((T6 + 12)) 0      # на программе никто не вернётся
bridge kmod-install --rebuild=on
check "R2-4: первый перевод на программу не удался — failed.step=swap, kmod_clients_lost; профили на прежнем модуле, без «не годится»; модуль не выгружали" \
    '[ "$(jget .status)" = error ] && [ "$(jget .code)" = kmod_clients_lost ] && [ "$(_ga_kmod_state_get ".failed.step")" = swap ] && grep -qF "профили работают на прежнем модуле ядра" <<<"$(jget .message)" && ! grep -qF "не годится" <<<"$(jget .message)" && [ "$(jget .engine)" = kernel ] && ! grep -q "^modprobe -r" "$STUB_LOG"' \
    "rc=$rc ${out:0:700} $(_ga_kmod_state | jq -c .failed) $(cat "$STUB_LOG")"

# ── REVIEW3-CORE-1013: прошлый провал после удачного перевода, шаг hooks, двойной сбой ─────────────────────────────
section "REVIEW3-CORE-1013: .failed после удачного перевода на модуль; слова шага hooks; откат не поднял профили"
# R3-1: отказ на возвращении профилей (switch) оставил .failed с «где профили»; потом пользователь сам перевёл профили на
# модуль (успех) и выбрал программу — шапка SSH не должна говорить «не включился» со старыми словами.
T7=$(clock); kb_fixtures $((T7 - 60)) $((T7 + 14)) $((T7 + 16))
run ga_kmod_engine_set kernel
T7=$(clock); kb_fixtures $((T7 - 30)) 0 $((T7 + 10))
bridge kmod-install --rebuild=on
r31_step=$(_ga_kmod_state_get ".failed.step")
T7=$(clock); kb_fixtures $((T7 - 60)) $((T7 + 14)) $((T7 + 16))
run ga_kmod_engine_set kernel
r31_rc=$rc; r31_failed=$(_ga_kmod_state | jq -c '.failed // null')
T7=$(clock); kb_fixtures $((T7 - 60)) $((T7 + 14)) $((T7 + 16))
run ga_kmod_engine_set program
km_st=$(ga_kmod_status_json); km_dash=$(ga_kmod_dash_warn 2>&1)
check "R3-1: switch не удался (failed.step=switch) → kmod-engine kernel (успех) снимает .failed → kmod-engine program: state не failed, шапка SSH без «не включился»" \
    '[ "$r31_step" = switch ] && [ "$r31_rc" = 0 ] && [ "$r31_failed" = null ] && [ "$rc" = 0 ] && [ "$(jq -r .state <<<"$km_st")" != failed ] && ! grep -qF "не включился" <<<"$km_dash" && ! grep -qF "не вернулся ни один" <<<"$km_dash"' \
    "step=$r31_step rc_k=$r31_rc failed=$r31_failed rc=$rc $(jq -c '{state, reason}' <<<"$km_st") $km_dash"
# R3-2: пересборка с переводом прошла (профили снова на новом модуле), а файлы в /etc не записались (шаг hooks).
T8=$(clock); kb_fixtures $((T8 - 60)) $((T8 + 14)) $((T8 + 16))
run ga_kmod_engine_set kernel
T8=$(clock); kb_fixtures $((T8 - 30)) $((T8 + 12)) $((T8 + 10))
_kb_ml=$(declare -f _ga_kmod_modload_install); _ga_kmod_modload_install() { return 1; }
run ga_kmod_install on off
eval "$_kb_ml"
r32_reason=$(ga_kmod_status_json | jq -r .failed.reason)
check "R3-2: шаг hooks после удачного перевода — failed.step=hooks, kmod_write_failed; в причине «новый модуль загружен…, профили переведены на него обратно», без «не загружен»" \
    '[ "$rc" != 0 ] && [ "$(_ga_kmod_state_get ".failed.step")" = hooks ] && [ "$(_ga_kmod_state_get ".failed.code")" = kmod_write_failed ] && grep -qF "Новый модуль загружен и прошёл самопроверку, профили переведены на него обратно" <<<"$r32_reason" && grep -qF "У сервера теперь выбрано: модуль ядра" <<<"$r32_reason" && ! grep -qF "не загружен" <<<"$r32_reason"' \
    "rc=$rc $(_ga_kmod_state | jq -c .failed) $(errs)"
# R4-1 (сценарий A): после сбоя hooks выбран модуль; пользователь выбирает программу (удачно) — «у сервера теперь
# выбрано: модуль ядра» и «профили переведены на него обратно» стали ложью: в kmod-status и шапке SSH — только ошибка.
T8=$(clock); kb_fixtures $((T8 - 60)) $((T8 + 14)) $((T8 + 16))
run ga_kmod_engine_set program
km_st=$(ga_kmod_status_json); km_dash=$(ga_kmod_dash_warn 2>&1)
check "R4-1 A: сбой hooks → kmod-engine program (успех): state=failed, причина — «Не удалось записать …», без «выбрано: модуль ядра» и «переведены на него обратно»; failed.left снят; шапка SSH — так же" \
    '[ "$rc" = 0 ] && [ "$(jq -r .state <<<"$km_st")" = failed ] && [ "$(jq -r .failed.step <<<"$km_st")" = hooks ] && grep -qF "Не удалось записать" <<<"$(jq -r .reason <<<"$km_st")" && ! grep -qF "У сервера теперь выбрано" <<<"$(jq -r .reason <<<"$km_st")" && ! grep -qF "переведены на него обратно" <<<"$(jq -r .failed.reason <<<"$km_st")" && [ "$(jq -r .failed.left <<<"$km_st")" = "" ] && [ "$(_ga_kmod_state_get ".failed.left")" = "" ] && grep -qF "Не удалось записать" <<<"$km_dash" && ! grep -qF "выбрано: модуль ядра" <<<"$km_dash"' \
    "rc=$rc $(jq -c "{state, reason, failed}" <<<"$km_st") | $km_dash"
_kb_kp=$(declare -f _ga_kmod_kernel_profiles)
_ga_kmod_kernel_profiles() { printf '%s' "${KB_ONK:-}"; }
w_h0=$(KB_ONK="awg2" GA_KMOD_SWAP_STARTED=0 GA_KMOD_STEP=hooks GA_KMOD_RELOAD="" GA_ERR_CODE=kmod_write_failed _ga_kmod_left_words state)
w_hn=$(KB_ONK="awg2" GA_KMOD_SWAP_STARTED=0 GA_KMOD_STEP=hooks GA_KMOD_RELOAD=neighbour GA_ERR_CODE=kmod_write_failed _ga_kmod_left_words state)
# R3-3: откат не поднял профиль (GA_KMOD_ROLLBACK_DOWN) — на switch и на swap.
w_sd=$(GA_KMOD_ROLLBACK_DOWN="awg2" GA_KMOD_SWAP_STARTED=1 GA_KMOD_STEP=switch GA_ERR_CODE=kmod_clients_lost _ga_kmod_left_words bridge)
w_wd=$(GA_KMOD_ROLLBACK_DOWN="awg2 awg3" GA_KMOD_SWAP_STARTED=1 GA_KMOD_STEP=swap GA_ERR_CODE=kmod_clients_lost _ga_kmod_left_words ssh)
eval "$_kb_kp"
check "R3-2: шаг hooks без перевода — «модуль ядра в памяти, профили работают на нём: awg2», без «не загружен»; сосед Kaskad — прежние слова «новый модуль не загружен» (так и есть)" \
    'grep -qF "Модуль ядра в памяти, профили работают на нём: awg2." <<<"$w_h0" && ! grep -qF "не загружен" <<<"$w_h0" && grep -qF "Новый модуль не загружен: профили работают на прежнем модуле ядра" <<<"$w_hn"' \
    "h0: $w_h0 | hn: $w_hn"
check "R3-3: откат не поднял профили — switch: «после отката на программу amneziawg-go не поднялись: awg2», без «пользователи переподключились»; swap: «вернуть… тоже не удалось», «Не поднялись после отката: awg2, awg3»" \
    'grep -qF "после отката на программу amneziawg-go не поднялись: awg2 — проверьте их" <<<"$w_sd" && ! grep -qF "переподключились" <<<"$w_sd" && grep -qF "и вернуть их на прежний модуль тоже не удалось" <<<"$w_wd" && grep -qF "Не поднялись после отката: awg2, awg3 — проверьте их" <<<"$w_wd" && ! grep -qF "переподключились" <<<"$w_wd" && ! grep -qF "Проверьте профили" <<<"$w_wd"' \
    "sd: $w_sd | wd: $w_wd"
# R3-3 вживую: первый перевод на программу не удался (на программе никто не вернулся), а откат awg2 на модуль не поднялся.
T9=$(clock); kb_fixtures $((T9 - 60)) $((T9 + 14)) $((T9 + 16))
run ga_kmod_engine_set kernel
T9=$(clock); kb_fixtures $((T9 - 30)) $((T9 + 12)) 0
eval "$(declare -f ga_amn_wait_up | sed '1s/ga_amn_wait_up/_kb_wait_up_orig/')"
ga_amn_wait_up() { [ "$1" = awg2 ] && [ "$(cat "$STATE_DIR/amnezia-awg2/engine" 2>/dev/null)" = kernel ] && return 1; _kb_wait_up_orig "$@"; }
run ga_kmod_install on off
eval "$(declare -f _kb_wait_up_orig | sed '1s/_kb_wait_up_orig/ga_amn_wait_up/')"
r33_down="${GA_KMOD_ROLLBACK_DOWN:-}"; r33_reason=$(ga_kmod_status_json | jq -r .failed.reason)
check "R3-3: откат перевода на программу не поднял awg2 — GA_KMOD_ROLLBACK_DOWN=awg2, failed.step=swap; в причине «Не поднялись после отката: awg2»" \
    '[ "$rc" != 0 ] && [ "$r33_down" = awg2 ] && [ "$(_ga_kmod_state_get ".failed.step")" = swap ] && grep -qF "Не поднялись после отката: awg2 — проверьте их" <<<"$r33_reason" && ! grep -qF "переподключились" <<<"$r33_reason"' \
    "rc=$rc down=$r33_down $(_ga_kmod_state | jq -c .failed) $(errs)"

# ── REVIEW4-CORE-1013: «где профили» прошлого провала — до первой смены движка; сборка и журнал не теряются ──────────
section "REVIEW4-CORE-1013: слова «где профили» после смены движка и перезагрузки; провал сборки после удачного перевода"
# R4-1 (сценарий B): пересборка упала на сборке — профили на прежнем модуле, выбран модуль. Потом выбрана программа.
T10=$(clock); kb_fixtures $((T10 - 60)) $((T10 + 14)) $((T10 + 16))
run ga_kmod_engine_set kernel
_kb_b=$(declare -f _ga_kmod_build)
_ga_kmod_build() { GA_KMOD_BUILD_LOG="make: *** хвост журнала сборки (тест)"; ga_err kmod_build_failed "сборка упала (тест)"; return 1; }
run ga_kmod_install on off
eval "$_kb_b"
km_st=$(ga_kmod_status_json)
check "R4-1 B: сбой сборки при пересборке — в состоянии ошибка отдельно (failed.reason без «где профили»), failed.left — «на прежнем модуле»; kmod-status.failed.reason — оба" \
    '[ "$rc" != 0 ] && [ "$(_ga_kmod_state_get ".failed.step")" = build ] && [ "$(_ga_kmod_state_get ".failed.reason")" = "сборка упала (тест)" ] && grep -qF "профили работают на прежнем модуле" <<<"$(_ga_kmod_state_get ".failed.left")" && grep -qF "сборка упала (тест)" <<<"$(jq -r .failed.reason <<<"$km_st")" && grep -qF "профили работают на прежнем модуле" <<<"$(jq -r .failed.reason <<<"$km_st")"' \
    "rc=$rc $(_ga_kmod_state | jq -c .failed) $(jq -c .failed <<<"$km_st")"
# После перезагрузки «что в памяти» уже не то — слова не показываем, ошибку — да.
printf '7b1c7c1e-0000-4000-8000-000000000003\n' >"$TMP/proc/sys/kernel/random/boot_id"
km_st=$(ga_kmod_status_json)
printf '7b1c7c1e-0000-4000-8000-000000000001\n' >"$TMP/proc/sys/kernel/random/boot_id"
check "R4-1: после перезагрузки kmod-status.failed — ошибка сборки без «на прежнем модуле», left пусто" \
    '[ "$(jq -r .failed.reason <<<"$km_st")" = "сборка упала (тест)" ] && [ "$(jq -r .failed.left <<<"$km_st")" = "" ]' \
    "$(jq -c .failed <<<"$km_st")"
T10=$(clock); kb_fixtures $((T10 - 60)) $((T10 + 14)) $((T10 + 16))
run ga_kmod_engine_set program
km_st=$(ga_kmod_status_json); km_dash=$(ga_kmod_dash_warn 2>&1)
check "R4-1 B: → kmod-engine program (успех): state=failed, причина — «сборка упала», без «на прежнем модуле»; журнал сборки на месте; шапка SSH без «прежнем модуле»" \
    '[ "$rc" = 0 ] && [ "$(jq -r .state <<<"$km_st")" = failed ] && [ "$(jq -r .reason <<<"$km_st")" = "сборка упала (тест)" ] && [ "$(jq -r .failed.log <<<"$km_st")" = "make: *** хвост журнала сборки (тест)" ] && grep -qF "сборка упала (тест)" <<<"$km_dash" && ! grep -qF "прежнем модуле" <<<"$km_dash"' \
    "rc=$rc $(jq -c "{state, reason, failed}" <<<"$km_st") | $km_dash"
# R4-2: удачный перевод на модуль снимает провал загрузки/самопроверки/перевода (R3-1), но не сборки: она не удалась,
# журнал нужен.
T10=$(clock); kb_fixtures $((T10 - 60)) $((T10 + 14)) $((T10 + 16))
run ga_kmod_engine_set kernel
r42_rc=$rc; r42_failed=$(_ga_kmod_state | jq -c '.failed // null')
T10=$(clock); kb_fixtures $((T10 - 60)) $((T10 + 14)) $((T10 + 16))
run ga_kmod_engine_set program
km_st=$(ga_kmod_status_json)
check "R4-2: провал сборки → kmod-engine kernel (успех) → program: .failed сборки остаётся (step=build, журнал), kmod-status — failed с ошибкой сборки" \
    '[ "$r42_rc" = 0 ] && [ "$(jq -r .step <<<"$r42_failed")" = build ] && [ "$(jq -r .log <<<"$r42_failed")" = "make: *** хвост журнала сборки (тест)" ] && [ "$rc" = 0 ] && [ "$(jq -r .state <<<"$km_st")" = failed ] && [ "$(jq -r .reason <<<"$km_st")" = "сборка упала (тест)" ] && [ "$(jq -r .failed.log <<<"$km_st")" = "make: *** хвост журнала сборки (тест)" ]' \
    "rc_k=$r42_rc failed=$r42_failed rc=$rc $(jq -c "{state, reason, failed}" <<<"$km_st")"

DONE=1
printf '\nИТОГ: %d пройдено, %d провалено, %d пропущено\n' "$pass" "$fail" "$skipped"
[ "$fail" = 0 ]
