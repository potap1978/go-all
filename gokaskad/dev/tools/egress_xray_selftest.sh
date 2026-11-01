#!/usr/bin/env bash
# tools/egress_xray_selftest.sh — свой xray «Прокси для API Telegram» и шлюза закреплённой версии (3.3.4, egress_ensure_xray).
# До 3.3.4 xray брался тремя путями без проверки: releases/latest при установке, любой xray сервера (/usr/local/bin,
# x-ui), xray из PATH у шлюза; скачанный однажды больше не обновлялся. Теперь — только свой, выпуск EGRESS_XVER,
# архив и файл сверены по SHA-256; свой другой версии заменяется, только если новая версия приняла текущие настройки.
# Без root и без сети: curl и uname подменены в PATH; архив «выпуска» собирается тут же, а суммы закрепления
# (egress_xray_pin) подменяются суммами этого архива — проверяется сама логика, не GitHub.
# Запуск: bash tools/egress_xray_selftest.sh          (exit 0 = всё зелёное)
set -u
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
S="$ROOT_DIR/install.sh"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
pass=0; fail=0
ok(){ printf '  \033[32mPASS\033[0m %s\n' "$1"; pass=$((pass+1)); }
no(){ printf '  \033[31mFAIL\033[0m %s\n' "$1"; [ -n "${2:-}" ] && printf '       %s\n' "$2"; fail=$((fail+1)); }
sha(){ if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }
VER=$(sed -n 's/^EGRESS_XVER="\(v[0-9.]*\)"$/\1/p' "$S" | head -n1)
mkdir -p "$TMP/bin" "$TMP/zip" "$TMP/zip2"

# Поддельный curl: releases/download/<ver>/<архив> — копия $FAKE_ZIP (нет файла — 22); всё прочее — 22.
# Каждый URL — строкой в $FAKE_LOG.
cat > "$TMP/bin/curl" <<'MOCK'
#!/usr/bin/env bash
out="" url=""
while [ $# -gt 0 ]; do case "$1" in -o) out="$2"; shift 2 ;; http*) url="$1"; shift ;; *) shift ;; esac; done
printf '%s\n' "$url" >> "$FAKE_LOG"
case "$url" in
    https://github.com/XTLS/Xray-core/releases/download/*/Xray-linux-*.zip)
        [ -n "$out" ] && [ -f "${FAKE_ZIP:-}" ] && cp "$FAKE_ZIP" "$out" && exit 0; exit 22 ;;
esac
exit 22
MOCK
# uname -m — из $FAKE_ARCH (по умолчанию x86_64)
cat > "$TMP/bin/uname" <<'MOCK'
#!/bin/sh
[ "${1:-}" = "-m" ] && { echo "${FAKE_ARCH:-x86_64}"; exit 0; }
exec /usr/bin/uname "$@"
MOCK
# systemctl — только запись вызова; is-active — «не работает»
printf '#!/bin/sh\necho "$*" >> "$FAKE_LOG.systemctl"\ncase "$*" in *is-active*|*is-enabled*) exit 3 ;; esac\nexit 0\n' > "$TMP/bin/systemctl"
# «Чужой» xray в PATH: если его кто-то запустит — это видно в журнале
printf '#!/bin/sh\necho foreign >> "$FAKE_LOG.foreign"\necho "Xray 99.0.0 (foreign)"\n' > "$TMP/bin/xray"
chmod +x "$TMP/bin/"*

# xray «выпуска»: version — его версия; -test -c F — отказ, если в F есть REFUSE (как xray 26.3.27 на allowInsecure)
mkxray() {   # $1 — файл, $2 — версия
    cat > "$1" <<EOF
#!/bin/sh
case "\$1" in
    version) echo "Xray $2 (selftest)"; exit 0 ;;
    -test) if grep -q REFUSE_AI "\$3" 2>/dev/null; then
               echo 'Failed to start: main: failed to load config files: [x] > infra/conf: Failed to build TLS config. > common/errors: The feature "allowInsecure" has been removed and migrated to "pinnedPeerCertSha256". Please update your config(s) according to release note and documentation.'
               exit 1
           fi
           if grep -q REFUSE "\$3" 2>/dev/null; then
               echo 'Failed to start: main: failed to load config files: [x] > infra/conf: failed to build outbound config with tag x > infra/conf: invalid "flow": xtls-rprx-direct'
               exit 1
           fi
           echo "Configuration OK."; exit 0 ;;
esac
exit 0
EOF
    chmod +x "$1"
}
mkzip() {    # $1 — каталог с xray, $2 — архив
    if command -v zip >/dev/null 2>&1; then (cd "$1" && zip -q "$2" xray)
    else python3 -c 'import sys,zipfile; z=zipfile.ZipFile(sys.argv[1],"w"); z.write(sys.argv[2],"xray"); z.close()' "$2" "$1/xray"; fi
}
mkxray "$TMP/zip/xray" "26.3.27"; mkzip "$TMP/zip" "$TMP/good.zip"
ZS=$(sha "$TMP/good.zip"); BS=$(sha "$TMP/zip/xray")
mkxray "$TMP/zip2/xray" "26.3.28"; mkzip "$TMP/zip2" "$TMP/other.zip"        # другой файл внутри
mkxray "$TMP/old" "25.12.8"                                                    # «прежний» свой xray узла до 3.3.4

# run [ZIP] — egress_ensure_xray в библиотечном режиме; корень — $TMP/root. Итог: OUT, RC, NURL (адреса curl), REPL.
run() {
    : > "$TMP/curl.log"
    OUT=$(PATH="$TMP/bin:$PATH" FAKE_ZIP="${1:-}" FAKE_LOG="$TMP/curl.log" FAKE_ARCH="${ARCH:-x86_64}" \
        KASKAD_ROOT="$TMP/root" ZS="$ZS" BS="$BS" "$BASH" -c '
            source "$1" --lib >/dev/null 2>&1 || { echo "RC=97"; exit; }
            KASKAD_LOG="$KASKAD_ROOT/var/log/kaskad.log"
            [ "${FAKE_ARCH}" = x86_64 ] && egress_xray_pin() { echo "Xray-linux-64.zip $ZS $BS"; }
            egress_ensure_parser >/dev/null 2>&1
            egress_ensure_xray; echo "RC=$?"; echo "REPL=${EGRESS_XRAY_REPLACED}"
            egress_xray_bin >/dev/null && echo "BIN=$(egress_xray_bin)" || echo "BIN="
        ' _ "$S" </dev/null 2>&1)
    RC=$(printf '%s\n' "$OUT" | sed -n 's/^RC=//p' | tail -1)
    REPL=$(printf '%s\n' "$OUT" | sed -n 's/^REPL=//p' | tail -1)
    BIN=$(printf '%s\n' "$OUT" | sed -n 's/^BIN=//p' | tail -1)
    NURL=$(grep -c . "$TMP/curl.log")
}
fresh() { rm -rf "$TMP/root"; mkdir -p "$TMP/root/etc/kaskad/egress" "$TMP/root/etc/kaskad/gw" "$TMP/root/var/log" "$TMP/root/opt/kaskad/gw"; }
X="$TMP/root/etc/kaskad/egress/xray"
left_x() { ls -d "$TMP/root/etc/kaskad/egress/.x."* 2>/dev/null; }

echo "── 1. статика ──"
"$BASH" -n "$S" 2>/dev/null && ok "bash -n install.sh" || no "bash -n install.sh"
[ "$VER" = "v26.3.27" ] && ok "закреплён xray $VER" || no "EGRESS_XVER не v26.3.27: «${VER}»"
left=$(grep -v '^[[:space:]]*#' "$S" | grep -n 'Xray-core/releases/latest')
[ -z "$left" ] && ok "releases/latest нигде не спрашивается" || no "остался releases/latest" "$left"
body=$(sed -n '/^egress_xray_bin() {/,/^}/p' "$S")
printf '%s' "$body" | grep -qE '/usr/local/bin/xray|/usr/bin/xray|x-ui' && no "egress_xray_bin берёт чужой xray" "$body" \
    || ok "egress_xray_bin — только свой /etc/kaskad/egress/xray"
grep -q 'EGRESS_XVER_FALLBACK' "$S" && no "осталась запасная версия EGRESS_XVER_FALLBACK" || ok "запасной «любой версии» больше нет"
for a in x86_64 aarch64 armv7l; do
    p=$(KASKAD_ROOT="$TMP/none" "$BASH" -c 'source "$1" --lib >/dev/null 2>&1; egress_xray_pin "$2"' _ "$S" "$a")
    [[ "$p" =~ ^Xray-linux-[a-z0-9-]+\.zip\ [0-9a-f]{64}\ [0-9a-f]{64}$ ]] && ok "закрепление $a: архив и две суммы" || no "закрепление $a" "$p"
done
KASKAD_ROOT="$TMP/none" "$BASH" -c 'source "$1" --lib >/dev/null 2>&1; egress_xray_pin riscv64' _ "$S" >/dev/null \
    && no "для riscv64 нашлось закрепление" || ok "другой машины нет в закреплении"

echo "── 2. своего xray нет: закреплённый, суммы сверены ──"
fresh; run "$TMP/good.zip"
[ "$RC" = 0 ] && [ -x "$X" ] && cmp -s "$X" "$TMP/zip/xray" && ok "поставлен xray из архива выпуска (RC 0)" || no "свежая установка" "$OUT"
[ "$(head -1 "$TMP/curl.log")" = "https://github.com/XTLS/Xray-core/releases/download/$VER/Xray-linux-64.zip" ] && [ "$NURL" = 1 ] \
    && ok "одна загрузка: releases/download/$VER/Xray-linux-64.zip, API GitHub не спрашивается" || no "адреса загрузки" "$(cat "$TMP/curl.log")"
[ "$REPL" = 1 ] && ok "EGRESS_XRAY_REPLACED=1" || no "EGRESS_XRAY_REPLACED «$REPL»"
[ -z "$(left_x)" ] && ok "временный каталог убран" || no "остался .x" "$(left_x)"
grep -q "xray: .*$VER" "$TMP/root/var/log/kaskad.log" 2>/dev/null && ok "в журнале — поставлен $VER" || no "журнал" "$(cat "$TMP/root/var/log/kaskad.log" 2>/dev/null)"
[ ! -s "$TMP/curl.log.foreign" ] && ok "чужой xray из PATH не запускался" || no "запускался чужой xray"

echo "── 3. свой уже закреплённый: в сеть не ходит ──"
run "$TMP/good.zip"
[ "$RC" = 0 ] && [ "$NURL" = 0 ] && [ -z "$REPL" ] && ok "RC 0, загрузок нет, замены нет" || no "повторный вызов" "RC=$RC NURL=$NURL REPL=$REPL"

echo "── 4. сумма не совпала ──"
fresh; run "$TMP/other.zip"
[ "$RC" = 1 ] && [ ! -e "$X" ] && ok "архив с другой суммой — не ставится, RC 1" || no "чужой архив" "$OUT"
printf '%s' "$OUT" | grep -q 'sha256' && ok "сказано про sha256" || no "нет слов про sha256" "$OUT"
[ -z "$(left_x)" ] && ok "временный каталог убран" || no "остался .x" "$(left_x)"
fresh; ZS0="$ZS"; ZS=$(sha "$TMP/other.zip"); run "$TMP/other.zip"; ZS="$ZS0"
[ "$RC" = 1 ] && [ ! -e "$X" ] && ok "архив сошёлся, а файл xray в нём — нет: не ставится" || no "файл в архиве с другой суммой" "$OUT"

echo "── 5. загрузки нет ──"
fresh; run ""
[ "$RC" = 1 ] && [ -z "$BIN" ] && ok "своего нет, скачать не вышло — RC 1, xray нет (чужой из PATH не взят)" || no "без сети" "$OUT"
[ -e "$TMP/root/etc/kaskad/egress/.xray-tried" ] && ok "сбой загрузки отмечен (.xray-tried)" || no "нет отметки сбоя загрузки"
run "$TMP/good.zip"; [ "$RC" = 0 ] && [ "$NURL" = 1 ] && ok "своего нет — отметка загрузку не держит (установка качает сразу)" || no "установка упёрлась в отметку" "$OUT"

echo "── 6. машина без закрепления ──"
fresh; ARCH=riscv64 run "$TMP/good.zip"
[ "$RC" = 1 ] && [ "$NURL" = 0 ] && ok "riscv64: RC 1, без загрузки" || no "riscv64" "$OUT"
printf '%s' "$OUT" | grep -q 'riscv64' && ok "в отказе названа машина" || no "машина не названа" "$OUT"

echo "── 7. свой другой версии (узел до 3.3.4): замена с проверкой настроек ──"
fresh; cp "$TMP/old" "$X"; printf '{"ok":1}\n' > "$TMP/root/etc/kaskad/egress/config.json"; printf '{"gw":1}\n' > "$TMP/root/etc/kaskad/gw/xray.json"
run "$TMP/good.zip"
[ "$RC" = 0 ] && cmp -s "$X" "$TMP/zip/xray" && cmp -s "$X.prev" "$TMP/old" && [ "$REPL" = 1 ] \
    && ok "новая версия приняла настройки — заменён, прежний — xray.prev" || no "замена" "$OUT"
printf '%s' "$OUT" | grep -q 'xray.prev' && ok "сказано, где прежний" || no "не сказано про xray.prev" "$OUT"

echo "── 8. новая версия не приняла настройки — остаётся прежний ──"
fresh; cp "$TMP/old" "$X"; printf '{"ok":1}\n' > "$TMP/root/etc/kaskad/egress/config.json"; printf '{"REFUSE":1}\n' > "$TMP/root/etc/kaskad/gw/xray.json"
run "$TMP/good.zip"
[ "$RC" = 0 ] && cmp -s "$X" "$TMP/old" && [ ! -e "$X.prev" ] && [ -z "$REPL" ] && ok "отказ на настройках шлюза — прежний xray на месте, RC 0" || no "отказ настроек" "$OUT"
printf '%s' "$OUT" | grep -q 'неверное поле flow' && ok "причина понятная (неверное поле flow)" || no "причина" "$OUT"
printf '%s' "$OUT" | grep -q '<…>' && no "в причине «<…>»" "$OUT" || ok "без обрезков «<…>»"
run "$TMP/good.zip"
[ "$NURL" = 0 ] && cmp -s "$X" "$TMP/old" && ok "те же настройки — больше не качает" || no "повторная загрузка при тех же настройках" "NURL=$NURL"
printf '{"gw":2}\n' > "$TMP/root/etc/kaskad/gw/xray.json"; run "$TMP/good.zip"
[ "$NURL" = 1 ] && cmp -s "$X" "$TMP/zip/xray" && ok "настройки сменились — снова проверено и заменено" || no "после смены настроек" "NURL=$NURL $OUT"

echo "── 8b. в настройках шлюза — только allowInsecure, которого ядро больше не напишет ──"
fresh; cp "$TMP/old" "$X"; printf '{"REFUSE_AI":1}\n' > "$TMP/root/etc/kaskad/gw/xray.json"
run "$TMP/good.zip"
[ "$RC" = 0 ] && cmp -s "$X" "$TMP/zip/xray" && [ "$REPL" = 1 ] && ok "отказ шлюза на allowInsecure замену не держит (gw apply перепишет настройки)" || no "allowInsecure шлюза" "$OUT"
fresh; cp "$TMP/old" "$X"; printf '{"REFUSE_AI":1}\n' > "$TMP/root/etc/kaskad/egress/config.json"; run "$TMP/good.zip"
cmp -s "$X" "$TMP/old" && [ -z "$REPL" ] && ok "у бота без пула (свежих настроек не собрать) — отказ на его config.json держит замену" || no "config.json бота без пула" "$OUT"

echo "── 9. свой другой версии, загрузка не прошла ──"
fresh; cp "$TMP/old" "$X"; run ""
[ "$RC" = 0 ] && cmp -s "$X" "$TMP/old" && [ "$BIN" = "$X" ] && ok "прежний остаётся, RC 0" || no "сбой загрузки при своём" "$OUT"
run "$TMP/good.zip"
[ "$NURL" = 0 ] && ok "следующая попытка — не раньше чем через час" || no "повторная попытка сразу" "NURL=$NURL"
touch -t 202001010000 "$TMP/root/etc/kaskad/egress/.xray-tried"; run "$TMP/good.zip"
[ "$NURL" = 1 ] && cmp -s "$X" "$TMP/zip/xray" && ok "через час — заменён" || no "после часа" "NURL=$NURL"

echo "── 10. старый config.json бота с тем, чего разборщик больше не пишет ──"
# в config.json узла остался allowInsecure (его писал разборщик до 3.3.4), новая версия на нём откажет — но после замены
# пересборка запишет настройки нового разборщика: проверяются они (собраны из пула), а не старый файл
fresh; cp "$TMP/old" "$X"; printf '{"REFUSE":"allowInsecure"}\n' > "$TMP/root/etc/kaskad/egress/config.json"
printf '%s\n' 'vless://11111111-2222-3333-4444-555555555555@198.51.100.21:8443?type=tcp&security=tls&allowInsecure=1&sni=a.example.com#Bad' \
    'vless://11111111-2222-3333-4444-555555555556@198.51.100.22:8443?type=tcp&security=tls&sni=b.example.com#Good' \
    > "$TMP/root/etc/kaskad/egress/upstream.txt"
run "$TMP/good.zip"
[ "$RC" = 0 ] && cmp -s "$X" "$TMP/zip/xray" && [ "$REPL" = 1 ] && ok "проверены настройки из пула (без allowInsecure) — заменён" || no "свежие настройки бота" "$OUT"

echo "── 11. служба бота идёт за своим xray ──"
# follow <юнит> — egress_xray_follow с подделкой пересборки (FAILREB=1 — она не выходит; удачная, как настоящая, пишет
# .xray-built); FOLLOW — что сделано
E="$TMP/root/etc/kaskad/egress"
follow() {
    rm -f "$TMP/rebuild.log"
    printf '%s\n' "$1" > "$TMP/root/etc/systemd/system/kaskad-egress.service"
    PATH="$TMP/bin:$PATH" FAKE_LOG="$TMP/curl.log" KASKAD_ROOT="$TMP/root" "$BASH" -c '
        source "$1" --lib >/dev/null 2>&1 || exit 97
        KASKAD_LOG="$KASKAD_ROOT/var/log/kaskad.log"
        RLOG="$2"; egress_rebuild() { echo REBUILD >> "$RLOG"; [ -z "${FAILREB:-}" ] || return 1; _egr_sha256 "$EGRESS_XRAY" > "$EGRESS_DIR/.xray-built"; }
        egress_xray_follow' _ "$S" "$TMP/rebuild.log" </dev/null >/dev/null 2>&1
    FOLLOW=$(cat "$TMP/rebuild.log" 2>/dev/null)
}
mkdir -p "$TMP/root/etc/systemd/system"
OWN="ExecStart=$X run -c $E/config.json"
printf '{"ok":1}\n' > "$E/config.json"; rm -f "$E/.xray-built" "$E/.xray-follow-failed"
follow "$OWN"; [ "$FOLLOW" = REBUILD ] && ok "пул ещё не собирался этим xray (.xray-built нет) — пересборка" || no "нет пересборки без .xray-built"
follow "$OWN"; [ -z "$FOLLOW" ] && ok "собран этим же xray — служба не трогается" || no "лишняя пересборка"
cp "$TMP/old" "$X"; follow "$OWN"
[ "$FOLLOW" = REBUILD ] && ok "xray сменился (сумма не та, что у последней сборки) — пересборка" || no "нет пересборки после замены"
cp "$TMP/zip/xray" "$X"; FAILREB=1 follow "$OWN"
[ "$FOLLOW" = REBUILD ] && [ -s "$E/.xray-follow-failed" ] && ok "пересборка не вышла — отмечено для этого пула" || no "нет отметки неудачной пересборки"
FAILREB=1 follow "$OWN"; [ -z "$FOLLOW" ] && ok "тот же пул — не повторяется (демоны при каждом старте не пересобирают по кругу)" || no "повтор неудачной пересборки"
echo 'vless://x@198.51.100.9:443#more' >> "$E/upstream.txt"
follow "$OWN"; [ "$FOLLOW" = REBUILD ] && [ ! -e "$E/.xray-follow-failed" ] && ok "пул сменился — снова пересобирается (признак замены на диске, не потерян)" || no "после смены пула"
follow "ExecStart=/usr/local/x-ui/bin/xray-linux-amd64 run -c $E/config.json"
[ "$FOLLOW" = REBUILD ] && ok "юнит с xray от x-ui — пересборка на свой" || no "юнит с чужим xray не переписан"
printf '{"allowInsecure":true}\n' > "$E/config.json"; follow "$OWN"
[ "$FOLLOW" = REBUILD ] && ok "в настройках бота allowInsecure — пересборка" || no "allowInsecure в config.json остался"
printf '{"ok":1}\n' > "$E/config.json"; : > "$E/upstream.txt"; rm -f "$E/.xray-built"; follow "$OWN"
[ -z "$FOLLOW" ] && ok "пул пуст — ничего" || no "пересборка пустого пула"

echo "── 12. нужен ли переход (k3_post_update при каждом старте демонов — без сети) ──"
# spawn — egress_xray_upgrade_spawn с подделкой systemd-run (пишет свои аргументы); SPAWN — с чем его позвали
cat > "$TMP/bin/systemd-run" <<'MOCK'
#!/bin/sh
echo "$*" >> "$FAKE_LOG.sdrun"
MOCK
chmod +x "$TMP/bin/systemd-run"
spawn() {
    : > "$TMP/curl.log"; rm -f "$TMP/curl.log.sdrun"
    mkdir -p "$TMP/root/opt/kaskad/bin"; printf '#!/bin/sh\n' > "$TMP/root/opt/kaskad/bin/gokaskad"; chmod +x "$TMP/root/opt/kaskad/bin/gokaskad"
    PATH="$TMP/bin:$PATH" FAKE_LOG="$TMP/curl.log" KASKAD_ROOT="$TMP/root" ZS="$ZS" BS="$BS" "$BASH" -c '
        source "$1" --lib >/dev/null 2>&1 || exit 97
        egress_xray_pin() { echo "Xray-linux-64.zip $ZS $BS"; }
        egress_xray_upgrade_spawn' _ "$S" </dev/null >/dev/null 2>&1
    SPAWN=$(cat "$TMP/curl.log.sdrun" 2>/dev/null)
}
fresh; mkdir -p "$TMP/root/etc/systemd/system"; E="$TMP/root/etc/kaskad/egress"
spawn; [ -z "$SPAWN" ] && ok "ни xray, ни службы бота — не нужен" || no "переход без xray" "$SPAWN"
cp "$TMP/old" "$X"; spawn
case "$SPAWN" in *"--unit=kaskad-xray-pin --on-active=30"*"--egress-xray-upgrade"*) ok "свой xray другой суммы — переход своим юнитом через 30 с" ;; *) no "переход при старом xray" "$SPAWN" ;; esac
cp "$TMP/zip/xray" "$X"; spawn; [ -z "$SPAWN" ] && ok "свой закреплённый, службы нет — не нужен" || no "переход при закреплённом" "$SPAWN"
mkdir -p "$TMP/root/opt/kaskad/gw"; cp "$TMP/old" "$TMP/root/opt/kaskad/gw/xray"; spawn
[ -z "$SPAWN" ] && ok "копия шлюза старая, но xray шлюзу не нужен (юнит выключен) — не нужен" || no "переход по копии ненужного шлюза"
printf '#!/bin/sh\necho "$*" >> "$FAKE_LOG.systemctl"\ncase "$*" in *is-enabled*kaskad-gw-xray*) exit 0 ;; esac\nexit 0\n' > "$TMP/bin/systemctl"; spawn
[ -n "$SPAWN" ] && ok "копия шлюза — старый xray, юнит xray шлюза включён — нужен (gw apply её сменит)" || no "копия шлюза не замечена"
printf '#!/bin/sh\necho "$*" >> "$FAKE_LOG.systemctl"\ncase "$*" in *is-active*|*is-enabled*) exit 3 ;; esac\nexit 0\n' > "$TMP/bin/systemctl"
cp "$X" "$TMP/root/opt/kaskad/gw/xray"; rm -f "$X"
printf '%s\n' "ExecStart=/usr/local/bin/xray run -c x" > "$TMP/root/etc/systemd/system/kaskad-egress.service"
echo 'vless://x@198.51.100.9:443#k' > "$E/upstream.txt"; spawn
[ -n "$SPAWN" ] && ok "своего нет, служба бота с пулом на чужом xray — нужен" || no "служба на чужом xray без своего"
: > "$E/.xray-tried"; spawn
[ -z "$SPAWN" ] && ok "своего нет, загрузка не удалась меньше часа назад — не дёргаем сеть на каждом старте" || no "повтор сразу после сбоя" "$SPAWN"
touch -t 202001010000 "$E/.xray-tried"; spawn
[ -n "$SPAWN" ] && ok "через час — снова нужен" || no "после часа не нужен"
rm -f "$E/.xray-tried"
cp "$TMP/zip/xray" "$X"; spawn
[ -n "$SPAWN" ] && ok "свой есть, служба бота на чужом xray — нужен" || no "служба на чужом xray"
[ ! -s "$TMP/curl.log" ] && ok "решение — без сети" || no "решение ходило в сеть" "$(cat "$TMP/curl.log")"
grep -q '^    egress_xray_upgrade_spawn$' "$S" && ok "k3_post_update зовёт egress_xray_upgrade_spawn" || no "k3_post_update не зовёт переход"
sed -n '/^egress_rebuild() {/,/^}/p' "$S" | grep -q '_egr_sha256 "$EGRESS_XRAY" > "$EGRESS_DIR/.xray-built"' \
    && ok "egress_rebuild пишет .xray-built (без этого пул пересобирался бы на каждом старте демонов)" || no "egress_rebuild не пишет .xray-built"
grep -q 'egress_pool_locked egress_xray_follow_now_menu' "$S" && grep -q '\] || egress_xray_follow_due || {' "$S" \
    && ok "меню: самолечение по egress_xray_follow_due, пересборка под замком пула" || no "меню без follow_due или без замка"

echo "── 12b. неудача без своего xray не запирает переход ──"
menu_fail() {   # $1 — есть ли свой xray после неудачи
    PATH="$TMP/bin:$PATH" FAKE_LOG="$TMP/curl.log" KASKAD_ROOT="$TMP/root" HAVE="$1" "$BASH" -c '
        source "$1" --lib >/dev/null 2>&1 || exit 97
        egress_rebuild() { return 1; }
        [ "$HAVE" = 1 ] || rm -f "$EGRESS_XRAY"
        egress_xray_follow_now_menu' _ "$S" </dev/null >/dev/null 2>&1
}
fresh; mkdir -p "$TMP/root/etc/systemd/system"; E="$TMP/root/etc/kaskad/egress"
echo 'vless://x@198.51.100.9:443#k' > "$E/upstream.txt"
menu_fail 0; [ ! -e "$E/.xray-follow-failed" ] && ok "xray не скачался — отметки нет (переход потом не упрётся в неё)" || no "отметка без своего xray"
cp "$TMP/zip/xray" "$X"; menu_fail 1; [ -s "$E/.xray-follow-failed" ] && ok "свой xray есть, пул не собрался — отмечено" || no "нет отметки при своём xray"

echo "── 13. один вызов за раз (замок .xray.lock) ──"
if command -v flock >/dev/null 2>&1; then
    fresh; cp "$TMP/old" "$X"; : > "$TMP/curl.log"
    for i in 1 2; do
        PATH="$TMP/bin:$PATH" FAKE_ZIP="$TMP/good.zip" FAKE_LOG="$TMP/curl.log" KASKAD_ROOT="$TMP/root" ZS="$ZS" BS="$BS" "$BASH" -c '
            source "$1" --lib >/dev/null 2>&1 || exit 97
            KASKAD_LOG="$KASKAD_ROOT/var/log/kaskad.log"; egress_ensure_parser >/dev/null 2>&1
            egress_xray_pin() { echo "Xray-linux-64.zip $ZS $BS"; }
            egress_ensure_xray' _ "$S" </dev/null >/dev/null 2>&1 &
    done
    wait
    [ "$(grep -c . "$TMP/curl.log")" = 1 ] && cmp -s "$X" "$TMP/zip/xray" && [ -z "$(left_x)" ] \
        && ok "два одновременных вызова — одна загрузка, второй видит закреплённый" || no "замок" "$(cat "$TMP/curl.log")"
else
    echo "  SKIP замок: нет flock (macOS) — проверяется на Linux"
fi

echo "────────────────────────────"
echo "  PASS=$pass  FAIL=$fail"
[ "$fail" = 0 ]
