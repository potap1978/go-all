#!/usr/bin/env bash
# tools/awg_core_selftest.sh — ядро AmneziaWG для «Прокси для API Telegram»:
# сначала диск (bin/ клона), сеть — только запасной путь и только с ключом GitHub.
# Без root и без сети: загрузка (kaskad_gh_curl) подменена — по умолчанию «сети нет», в отдельных
# проверках — файлы из поддельных веток; репозиторий несуществующий, ключей нигде нет.
# Запуск: bash tools/awg_core_selftest.sh          (exit 0 = всё зелёное)
# На машине с /usr/local/bin/{amneziawg-go,awg} секции 2–3 неприменимы (awg_*_bin найдёт их первыми).
set -u
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Другой продукт: AWG_SELFTEST_SCRIPT=<скрипт с режимом --lib>, AWG_SELFTEST_BIN=<каталог bin/>.
S="${AWG_SELFTEST_SCRIPT:-$ROOT_DIR/install.sh}"
BIN="${AWG_SELFTEST_BIN:-$ROOT_DIR/bin}"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
pass=0; fail=0
ok(){ printf '  \033[32mPASS\033[0m %s\n' "$1"; pass=$((pass+1)); }
no(){ printf '  \033[31mFAIL\033[0m %s\n' "$1"; [ -n "${2:-}" ] && printf '       %s\n' "$2"; fail=$((fail+1)); }
strip(){ sed 's/\x1b\[[0-9;]*m//g'; }
sha(){ if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -c1-16; else shasum -a 256 "$1" | cut -c1-16; fi; }
lineof(){ grep -n -m1 -F "$1" "$S" | cut -d: -f1; }
AWGD="$TMP/kaskad/egress/awg"
# Вызов кода install.sh в библиотечном режиме с подменёнными путями: /etc/kaskad не трогается,
# ключей нет, репозиторий несуществующий, архитектура amd64 (в bin/ есть оба файла).
# $1 — снипет bash; KAS_ROOTS — список каталогов для поиска bin/ (пусто = нигде).
lib() {
    KAS_TMP="$TMP" KAS_ROOTS="${KAS_ROOTS-}" \
    bash -c '
        source "$1" --lib >/dev/null 2>&1 || exit 97
        KASKAD_DIR="$KAS_TMP/kaskad"; KASKAD_CONF="$KASKAD_DIR/config"; BOT_STATE_DIR="$KASKAD_DIR/bot_state"
        EGRESS_DIR="$KASKAD_DIR/egress"; AWG_DIR="$EGRESS_DIR/awg"; AWG_GO="$AWG_DIR/amneziawg-go"; AWG_TOOL="$AWG_DIR/awg"
        mkdir -p "$KASKAD_DIR" "$BOT_STATE_DIR"
        AWG_REPO="potap1978/no-such-repo-$$"; KASKAD_ARCH="amd64"
        export KASKAD_AWG_ROOTS="$KAS_ROOTS" GOINST_TOKEN_FILE="$KAS_TMP/goinst-token"
        unset GH_TOKEN GITHUB_TOKEN GITHUB_PAT
        kaskad_gh_curl(){ return 1; }   # сети нет; проверке нужна «сеть» — она подменяет функцию сама
        eval "$2"
    ' _ "$S" "$1" </dev/null 2>&1
}
rc_of(){ printf '%s\n' "$1" | sed -n 's/^RC=//p' | tail -1; }
reset_tokens(){ rm -f "$TMP/goinst-token" "$TMP/kaskad/config" "$TMP/kaskad/bot_state/system" 2>/dev/null; }

echo "── 1. статика install.sh ──"
bash -n "$S" 2>/dev/null && ok "bash -n install.sh" || no "bash -n install.sh"
tail -3 "$S" | grep -q '^esac' && ok "sentinel esac в последних строках (нужен self_update)" || no "нет esac в конце"
grep -q '^KASKAD_VERSION="[0-9]' "$S" && ok "KASKAD_VERSION объявлена в начале строки" || no "KASKAD_VERSION не найдена"
grep -q '^    --lib) : ;;' "$S" && ok "режим --lib: install.sh подключается как библиотека" || no "нет режима --lib"
for fn in kaskad_gh_token kaskad_gh_curl egress_awg_roots egress_awg_arch egress_awg_pair_runs egress_awg_pair_is3x egress_awg_pair_ok egress_awg_runs egress_awg_is3x egress_awg_ok egress_awg_try_stage egress_awg_stage_disk egress_awg_fetch egress_awg_put egress_ensure_awg egress_awg_selfheal3 egress_pool_needs_awg3; do
    grep -q "^${fn}() *{" "$S" && ok "есть ${fn}()" || no "нет ${fn}()"
done
a=$(lineof 'egress_awg_stage_disk "$arch" "$st"'); b=$(lineof 'egress_awg_fetch "$arch" "$tok"')   # оба вызова внутри egress_ensure_awg
[ -n "$a" ] && [ -n "$b" ] && [ "$a" -lt "$b" ] && ok "порядок: диск (стр. $a) раньше сети (стр. $b)" || no "порядок «диск, потом сеть» нарушен" "copy=$a net=$b"
grep -v '^[[:space:]]*#' "$S" | grep -q 'awg-linux-amd64' && no "в коде осталось имя awg-linux-amd64 без \$arch (arm64 получит чужой бинарник)" || ok "имя awg строится из \$arch (нет awg-linux-amd64 в коде)"
grep -q 'hdr=(-H "Authorization' "$S" && no "ключ GitHub всё ещё уходит аргументом curl (-H) — виден в ps" || ok "ключ GitHub не в argv curl (конфиг-файл 0600)"
grep -Fq 'Задайте GitHub PAT (меню 8 → 6) и повторите' "$S" && no "старая подсказка «задайте PAT … и повторите» на месте" || ok "старой подсказки «…и повторите» нет"
grep -q 'update_token=$(kaskad_gh_token)' "$S" && ok "self_update ищет ключ тем же kaskad_gh_token" || no "self_update не использует kaskad_gh_token"
grep -q 'ip route replace unreachable default table "$table" metric 1000' "$S" && ok "нога AWG fail-closed: unreachable-маршрут в таблице fwmark" || no "нет unreachable-маршрута в таблице fwmark (мёртвая нога утечёт напрямую)"
a=$(lineof 'ip route replace unreachable default table "$table" metric 1000'); b=$(lineof '[ -n "$go" ] && [ -n "$tool" ] || continue')
[ -n "$a" ] && [ -n "$b" ] && [ "$a" -lt "$b" ] && ok "unreachable ставится ДО проверки ядра (без ядра нога закрыта, а не течёт)" || no "unreachable после проверки ядра: нога без ядра утечёт напрямую" "unreachable=$a core-check=$b"
grep -q 'ip link set "$iface" up mtu 1200' "$S" && ok "MTU kawg = 1200 (решение владельца 2026-10-04)" || no "MTU kawg не 1200"
grep -qF "{ grep -qE '\"protocol\": *\"vless\"' \"\$EGRESS_CFG\" 2>/dev/null && ! grep -q 'tcpKeepAliveInterval'" "$S" && ok "самолечение меню ищет tcpKeepAliveInterval только при VLESS-outbound в конфиге (пул из AWG не перезапускается)" || no "самолечение меню перезапускает пул без VLESS при каждом входе"
grep -qF '{ [ "${EGRESS_MSS:-0}" -gt 0 ] 2>/dev/null && ! grep -q '"'"'tcpMaxSeg'"'"'' "$S" && ok "самолечение меню ищет tcpMaxSeg только при включённом EGRESS_MSS" || no "самолечение меню при EGRESS_MSS=0 перезапускает egress каждый раз"
for f in amneziawg-go-linux-amd64 amneziawg-go-linux-arm64 awg-linux-amd64 awg-linux-arm64; do
    [ -s "$BIN/$f" ] && ok "bin/$f непустой" || no "bin/$f отсутствует или пуст"
done

echo "── 2. ядро с диска: репозиторий несуществующий, ключей нет ──"
reset_tokens
out=$(KAS_ROOTS="$(dirname "$BIN")" lib 'egress_ensure_awg; echo "RC=$?"'); rc=$(rc_of "$out")
[ "$rc" = "0" ] && ok "egress_ensure_awg вернул 0" || no "egress_ensure_awg вернул ${rc:-?}" "$(printf '%s' "$out" | strip | tail -4 | tr '\n' '|')"
printf '%s' "$out" | grep -qF 'из локальной копии' && ok "ядро взято с диска, а не по сети" || no "нет строки «из локальной копии»"
[ -f "$AWGD/amneziawg-go" ] && [ "$(sha "$AWGD/amneziawg-go")" = "$(sha "$BIN/amneziawg-go-linux-amd64")" ] && ok "amneziawg-go = bin/amneziawg-go-linux-amd64 (sha256)" || no "amneziawg-go не совпадает с bin/"
[ -f "$AWGD/awg" ] && [ "$(sha "$AWGD/awg")" = "$(sha "$BIN/awg-linux-amd64")" ] && ok "awg = bin/awg-linux-amd64 (sha256)" || no "awg не совпадает с bin/"
if [ "$(uname -s)" = "Linux" ] && [ "$(uname -m)" = "x86_64" ]; then
    v=$("$AWGD/amneziawg-go" --version 2>&1 | head -1); case "$v" in *amneziawg-go*) ok "запускается: $v";; *) no "amneziawg-go --version" "$v";; esac
    v=$("$AWGD/awg" --version 2>&1 | head -1); case "$v" in *amneziawg-tools*) ok "запускается: $v";; *) no "awg --version" "$v";; esac
else
    echo "  SKIP запуск бинарников (не Linux/x86_64: $(uname -s)/$(uname -m))"
fi
out=$(KAS_ROOTS="" lib 'egress_ensure_awg; echo "RC=$?"'); rc=$(rc_of "$out")
[ "$rc" = "0" ] && ! printf '%s' "$out" | grep -q 'Подготовка ядра' && ok "повторный вызов: ядро уже готово, ничего не качает" || no "повторный вызов не короткий" "rc=$rc"

echo "── 3. ядра нет нигде: честный отказ, без приглашения ввести ключ (stdin не терминал) ──"
reset_tokens; rm -rf "$AWGD"; mkdir -p "$TMP/empty"
out=$(KAS_ROOTS="$TMP/empty" lib 'egress_ensure_awg; echo "RC=$?"'); rc=$(rc_of "$out"); txt=$(printf '%s' "$out" | strip)
[ "$rc" = "1" ] && ok "egress_ensure_awg вернул 1" || no "ожидался rc=1, получен ${rc:-?}"
printf '%s' "$txt" | grep -qF 'bin/amneziawg-go-linux-amd64' && printf '%s' "$txt" | grep -qF 'bin/awg-linux-amd64' && ok "сообщение называет оба файла" || no "сообщение не называет файлы"
printf '%s' "$txt" | grep -q 'приватный' && printf '%s' "$txt" | grep -qF 'меню 8 → 6' && ok "сообщение объясняет причину (приватный) и где ключ (меню 8 → 6)" || no "нет причины/подсказки про ключ"
printf '%s' "$txt" | grep -qF "$AWGD/amneziawg-go" && ok "сообщение называет путь для ручной раскладки ($AWGD)" || no "нет пути для ручной раскладки"
printf '%s' "$txt" | grep -q 'Введите GitHub PAT' && no "приглашение ввести PAT показано без терминала" || ok "без терминала ключ не спрашивается"
[ ! -e "$AWGD/amneziawg-go" ] && [ ! -e "$AWGD/awg" ] && ok "обрывков в $AWGD не осталось" || no "остались файлы в $AWGD"

echo "── 4. kaskad_gh_token: порядок источников ──"
reset_tokens
t=$(lib 'printf "[%s]" "$(kaskad_gh_token)"'); [ "$t" = "[]" ] && ok "ключей нет → пусто" || no "ожидалось пусто" "$t"
t=$(lib 'printf "tok-goinst\n" > "$GOINST_TOKEN_FILE"; printf "[%s]" "$(kaskad_gh_token)"'); [ "$t" = "[tok-goinst]" ] && ok "4) файл goInstaller читается" || no "файл goInstaller" "$t"
t=$(lib 'GH_TOKEN=tok-env; printf "[%s]" "$(kaskad_gh_token)"'); [ "$t" = "[tok-env]" ] && ok "3) GH_TOKEN окружения важнее файла goInstaller" || no "GH_TOKEN" "$t"
t=$(lib 'printf "GITHUB_PAT=\"tok-conf\"\n" > "$KASKAD_CONF"; GH_TOKEN=tok-env; printf "[%s]" "$(kaskad_gh_token)"'); [ "$t" = "[tok-conf]" ] && ok "2) GITHUB_PAT из config важнее окружения" || no "GITHUB_PAT config" "$t"
t=$(lib 'bot_set_state system "UPDATE_TOKEN=tok-state"; GH_TOKEN=tok-env; printf "[%s]" "$(kaskad_gh_token)"'); [ "$t" = "[tok-state]" ] && ok "1) UPDATE_TOKEN (меню 8 → 6) важнее всего" || no "UPDATE_TOKEN" "$t"
reset_tokens

echo "── 5. лежит, но не запускается (чужая архитектура) → заменяется с диска ──"
if [ "$(uname -s)" = "Linux" ]; then
    rm -rf "$AWGD"; mkdir -p "$AWGD"
    printf '#!/bin/sh\nexit 1\n' > "$AWGD/amneziawg-go"; printf '#!/bin/sh\nexit 1\n' > "$AWGD/awg"; chmod 700 "$AWGD/amneziawg-go" "$AWGD/awg"
    out=$(KAS_ROOTS="$(dirname "$BIN")" lib 'egress_ensure_awg; echo "RC=$?"'); rc=$(rc_of "$out")
    [ "$rc" = "0" ] && [ "$(sha "$AWGD/awg")" = "$(sha "$BIN/awg-linux-amd64")" ] && ok "неработающее ядро заменено копией из bin/" || no "неработающее ядро не заменено" "rc=$rc"
else
    echo "  SKIP (проверка запуска только на Linux)"
fi

echo "── 6. ключу AmneziaWG 3.x нужно ядро 3.1: новое готовится рядом, рабочее не трогается ──"
# Поддельные пары: «new» знает ключ 3.1 (disable_cookies/DisableCookies — так их узнаёт egress_awg_is3x),
# «v30» — только HeaderProtectionKey (ядро 3.0), «old» — ничего из 3.x.
FK="$TMP/fake"; mkdir -p "$FK/new/bin" "$FK/v30/bin" "$FK/old/bin" "$FK/sys"
printf '#!/bin/sh\n# header_protection_key disable_cookies\nexit 0\n' > "$FK/new/bin/amneziawg-go-linux-amd64"
printf '#!/bin/sh\n# HeaderProtectionKey DisableCookies\nexit 0\n'      > "$FK/new/bin/awg-linux-amd64"
printf '#!/bin/sh\n# header_protection_key (3.0)\nexit 0\n'          > "$FK/v30/bin/amneziawg-go-linux-amd64"
printf '#!/bin/sh\n# HeaderProtectionKey (3.0)\nexit 0\n'            > "$FK/v30/bin/awg-linux-amd64"
printf '#!/bin/sh\n# go 0.2.19\nexit 0\n'                          > "$FK/old/bin/amneziawg-go-linux-amd64"
printf '#!/bin/sh\n# tools 1.0\nexit 0\n'                          > "$FK/old/bin/awg-linux-amd64"
printf '#!/bin/sh\n# system awg 1.0\nexit 0\n'                     > "$FK/sys/awg"
chmod 755 "$FK"/*/bin/* "$FK/sys/awg"
put_old(){ rm -rf "$AWGD"; mkdir -p "$AWGD"; cp "$FK/old/bin/amneziawg-go-linux-amd64" "$AWGD/amneziawg-go"; cp "$FK/old/bin/awg-linux-amd64" "$AWGD/awg"; chmod 700 "$AWGD/amneziawg-go" "$AWGD/awg"; }
is_old(){ [ "$(sha "$AWGD/amneziawg-go")" = "$(sha "$FK/old/bin/amneziawg-go-linux-amd64")" ] && [ "$(sha "$AWGD/awg")" = "$(sha "$FK/old/bin/awg-linux-amd64")" ]; }
is_new(){ [ "$(sha "$AWGD/amneziawg-go")" = "$(sha "$FK/new/bin/amneziawg-go-linux-amd64")" ] && [ "$(sha "$AWGD/awg")" = "$(sha "$FK/new/bin/awg-linux-amd64")" ]; }
no_left(){ ! ls -d "$AWGD"/.stage.* >/dev/null 2>&1; }
# Сеть без сети: kaskad_gh_curl берёт файл из $FK/<ветка>/bin (KAS_NET="main=old test=new") и отмечает вызов.
NETSTUB='kaskad_gh_curl(){ local src; echo "$2" >> "$KAS_TMP/net.calls"; case "$2" in */main/*) src="${KAS_NET_MAIN:-}";; */test/*) src="${KAS_NET_TEST:-}";; esac; [ -n "$src" ] && cp "$KAS_TMP/fake/$src/bin/${2##*/}" "$3" 2>/dev/null && [ -s "$3" ]; };'
reset_tokens
if [ -x /usr/local/bin/amneziawg-go ] || [ -x /usr/local/bin/awg ]; then
    echo "  SKIP (в /usr/local/bin есть системное ядро AmneziaWG — сценарии с пустым местом неприменимы)"
else
put_old
out=$(KAS_ROOTS="$FK/new" lib 'egress_awg_is3x && echo IS3X; egress_ensure_awg 3; echo "RC=$?"; egress_awg_is3x && echo NOW3X'); rc=$(rc_of "$out")
! printf '%s' "$out" | grep -q '^IS3X' && ok "старое ядро не считается 3.1" || no "старое ядро принято за 3.1"
[ "$rc" = "0" ] && printf '%s' "$out" | grep -q '^NOW3X' && is_new && no_left && ok "старое заменено ядром 3.1 из bin/, рабочих каталогов не осталось" || no "старое не заменено" "rc=$rc $(printf '%s' "$out" | strip | tr '\n' '|')"
printf '%s' "$out" | grep -qF 'Подготовка ядра AmneziaWG 3.1' && ok "сообщение говорит, что готовится ядро 3.1" || no "нет «Подготовка ядра AmneziaWG 3.1»"
out=$(KAS_ROOTS="" lib 'egress_ensure_awg 3; echo "RC=$?"'); [ "$(rc_of "$out")" = "0" ] && ! printf '%s' "$out" | grep -q 'Подготовка' && ok "ядро 3.1 уже стоит — повторный вызов ничего не делает" || no "повторный вызов с ядром 3.1 не короткий"

put_old
out=$(KAS_ROOTS="$FK/v30" lib 'egress_ensure_awg 3; echo "RC=$?"; egress_awg_is3x && echo NOW3X'); rc=$(rc_of "$out")
[ "$rc" = "0" ] && ! printf '%s' "$out" | grep -q '^NOW3X' && is_old && ok "ядро 3.0 (без DisableCookies) за 3.1 не принято, прежнее на месте" || no "ядро 3.0 принято за 3.1" "rc=$rc"

put_old
out=$(KAS_ROOTS="$FK/old:$FK/new" lib 'egress_ensure_awg 3; echo "RC=$?"'); rc=$(rc_of "$out")
[ "$rc" = "0" ] && is_new && ok "устаревший клон первым в списке не заслоняет свежий (пары по очереди)" || no "свежий клон не найден за устаревшим" "rc=$rc"

put_old
out=$(KAS_ROOTS="$TMP/empty" lib 'egress_ensure_awg 3; echo "RC=$?"'); rc=$(rc_of "$out"); txt=$(printf '%s' "$out" | strip)
[ "$rc" = "0" ] && is_old && no_left && ok "ядра 3.1 нигде нет → прежнее не тронуто, rc=0 (ключи 1.x/2.0 работают)" || no "прежнее ядро тронуто" "rc=$rc"
printf '%s' "$txt" | grep -qF 'нужно ядро 3.1' && printf '%s' "$txt" | grep -qF 'меню 8 → 6' && printf '%s' "$txt" | grep -qF 'git -C /opt/kaskad-pro pull' && ok "честное предупреждение: ключ 3.x не подключится; ключ GitHub или свежий клон" || no "нет предупреждения про ядро 3.1" "$(printf '%s' "$txt" | tail -4 | tr '\n' '|')"
printf '%s' "$txt" | grep -q 'Введите GitHub PAT' && no "без терминала спросили PAT" || ok "без терминала PAT не спрашивается"

put_old; rm -f "$TMP/net.calls"
out=$(KAS_ROOTS="$TMP/empty" lib "$NETSTUB"' kaskad_gh_curl(){ echo "$2" >> "$KAS_TMP/net.calls"; sleep 20; return 1; }; ( egress_ensure_awg 3 >/dev/null 2>&1 ) & p=$!; sleep 2; kill $p 2>/dev/null; wait $p 2>/dev/null; echo KILLED')
[ -s "$TMP/net.calls" ] && is_old && ok "оборванная загрузка (Ctrl-C, обрыв SSH) — рабочее ядро на месте" || no "после обрыва загрузки ядра нет на месте" "$(ls -la "$AWGD" 2>&1 | tr '\n' '|')"
out=$(KAS_ROOTS="$TMP/empty" lib 'egress_ensure_awg 3; echo "RC=$?"'); [ "$(rc_of "$out")" = "0" ] && is_old && ok "следующий запуск после обрыва: прежнее ядро цело" || no "после обрыва следующий запуск потерял ядро" "rc=$(rc_of "$out")"
mkdir -p "$AWGD/.stage.fresh1" "$AWGD/.stage.stale1" "$AWGD/.stage.stale1.fb"; touch -t 202001010000 "$AWGD/.stage.stale1" "$AWGD/.stage.stale1.fb"
out=$(KAS_ROOTS="$TMP/empty" lib 'egress_ensure_awg 3; echo "RC=$?"')
[ ! -e "$AWGD/.stage.stale1" ] && [ ! -e "$AWGD/.stage.stale1.fb" ] && [ -d "$AWGD/.stage.fresh1" ] && is_old && ok "остатки старше часа убираются, свежие (чужой идущий запуск) не трогаются" || no "уборка остатков" "$(ls -a "$AWGD" | tr '\n' ' ')"
rm -rf "$AWGD"/.stage.*

put_old; rm -f "$TMP/net.calls"
out=$(KAS_ROOTS="$TMP/empty" KAS_NET_MAIN=old KAS_NET_TEST=new lib "$NETSTUB"' egress_ensure_awg 3; echo "RC=$?"'); rc=$(rc_of "$out")
[ "$rc" = "0" ] && is_new && grep -q '/test/bin/' "$TMP/net.calls" && ok "сеть: в main старое, в test 3.1 — взято из test" || no "сеть main=old test=new" "rc=$rc"
rm -rf "$AWGD"; rm -f "$TMP/net.calls"
out=$(KAS_ROOTS="$TMP/empty" KAS_NET_MAIN=old KAS_NET_TEST=old lib "$NETSTUB"' egress_ensure_awg 3; echo "RC=$?"'); rc=$(rc_of "$out")
[ "$rc" = "0" ] && is_old && no_left && printf '%s' "$out" | strip | grep -qF 'нужно ядро 3.1' && ok "ядра нет вовсе, по сети только старое → поставлено запасным, ключ 3.x честно не подключится" || no "запасное ядро не поставлено" "rc=$rc"
put_old; rm -f "$TMP/net.calls"
out=$(KAS_ROOTS="$TMP/empty" KAS_NET_MAIN=new lib "$NETSTUB"' EGRESS_AWG_NO_NET=1 egress_ensure_awg 3; echo "RC=$?"'); rc=$(rc_of "$out")
[ "$rc" = "0" ] && is_old && [ ! -e "$TMP/net.calls" ] && ok "EGRESS_AWG_NO_NET=1 — в сеть не ходит" || no "EGRESS_AWG_NO_NET=1 всё равно пошёл в сеть"

rm -rf "$AWGD"; mkdir -p "$AWGD"; cp "$FK/old/bin/amneziawg-go-linux-amd64" "$AWGD/amneziawg-go"; chmod 700 "$AWGD/amneziawg-go"
out=$(KAS_ROOTS="$TMP/empty" lib 'awg_tool_bin(){ [ -x "$AWG_TOOL" ] && { echo "$AWG_TOOL"; return 0; }; echo "$KAS_TMP/fake/sys/awg"; }; egress_ensure_awg 3; echo "RC=$?"'); rc=$(rc_of "$out")
[ "$rc" = "0" ] && [ "$(sha "$AWGD/amneziawg-go")" = "$(sha "$FK/old/bin/amneziawg-go-linux-amd64")" ] && ok "смешанное ядро (своё amneziawg-go + системный awg): своё не удалено" || no "своя половина смешанного ядра удалена" "rc=$rc"

put_old; rm -f "$TMP/net.calls" "$AWGD/.core3-try" "$TMP/rebuilt"
SH='egress_pool_needs_awg3(){ return 0; }; egress_rebuild(){ touch "$KAS_TMP/rebuilt"; };'
out=$(KAS_ROOTS="$TMP/empty" lib "$NETSTUB $SH"' egress_awg_selfheal3; echo "RC=$?"')
[ -s "$TMP/net.calls" ] && [ -s "$AWGD/.core3-try" ] && [ ! -e "$TMP/rebuilt" ] && ok "самолечение меню: сеть попробована, отметка времени записана, без ядра 3.1 пересборки нет" || no "самолечение: первая попытка" "$(printf '%s' "$out" | strip | tr '\n' '|')"
rm -f "$TMP/net.calls"
out=$(KAS_ROOTS="$TMP/empty" lib "$NETSTUB $SH"' egress_awg_selfheal3')
[ ! -e "$TMP/net.calls" ] && printf '%s' "$out" | grep -qF 'ищу его на диске' && ok "самолечение меню: в течение часа в сеть снова не ходит (меню не виснет)" || no "самолечение меню ходит в сеть при каждом входе"
out=$(KAS_ROOTS="$TMP/empty" lib "$NETSTUB $SH"' printf "GITHUB_PAT=\"tok-new\"\n" > "$KASKAD_CONF"; egress_awg_selfheal3'); reset_tokens
[ -s "$TMP/net.calls" ] && ok "самолечение меню: новый ключ GitHub — пробует сеть сразу" || no "новый ключ GitHub не вызвал попытку"
out=$(KAS_ROOTS="$FK/new" lib "$NETSTUB $SH"' egress_awg_selfheal3')
[ -e "$TMP/rebuilt" ] && is_new && [ ! -e "$AWGD/.core3-try" ] && ok "самолечение меню: ядро 3.1 на диске → поставлено, egress пересобран, отметка снята" || no "самолечение с ядром на диске" "$(printf '%s' "$out" | strip | tr '\n' '|')"
rm -rf "$AWGD"; rm -f "$TMP/rebuilt"
out=$(KAS_ROOTS="$FK/old" lib "$NETSTUB $SH"' egress_awg_selfheal3')
[ -e "$TMP/rebuilt" ] && is_old && [ -s "$AWGD/.core3-try" ] && ok "самолечение меню: ядра не было вовсе → поставлено хотя бы старое, egress пересобран (поднимет 1.x/2.0)" || no "самолечение без ядра: нет пересборки" "$(ls -a "$AWGD" 2>&1 | tr '\n' ' ')"
fi

echo "── 7. ядро в bin/ понимает AmneziaWG 3.1 (amneziawg-go ≥ v3.1.20260828, awg ≥ 3.1.20260812) ──"
for a in amd64 arm64; do
    LC_ALL=C grep -aqF disable_cookies "$BIN/amneziawg-go-linux-$a" && ok "bin/amneziawg-go-linux-$a знает UAPI disable_cookies (3.1)" || no "bin/amneziawg-go-linux-$a — ядро до 3.1"
    LC_ALL=C grep -aqF DisableCookies "$BIN/awg-linux-$a" && ok "bin/awg-linux-$a знает DisableCookies (3.1)" || no "bin/awg-linux-$a — awg до 3.1"
    v=$(LC_ALL=C grep -aoE '[0-9]+\.[0-9]+\.[0-9]{8}(-[0-9]+)?' "$BIN/awg-linux-$a" | head -1)
    case "$v" in 3.1.*|3.[2-9].*|[4-9].*) ok "bin/awg-linux-$a: версия $v";; *) no "bin/awg-linux-$a: версия ${v:-не найдена} (нужна ≥ 3.1.20260812)";; esac
done

echo "────────────────────────────"
echo "  PASS=$pass  FAIL=$fail"
[ "$fail" = 0 ]
