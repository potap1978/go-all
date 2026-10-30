#!/usr/bin/env bash
# tools/github_tag_selftest.sh — какую версию берут с GitHub отдельный xray для «Прокси для API Telegram»
# (egress_ensure_xray) и установка 3X-UI без выбранной версии, то есть «Pro — авто (ленивый)»
# (xui_resolve_version, шаг «2. Resolve version» в install_3xui).
#
# Зачем: GitHub API бывает отдаёт JSON одной строкой (Aeza, Debian 12, 04.10.2026). Построчный разбор
# grep '"tag_name"' | cut -d'"' -f4 печатал тогда значение первого ключа ("url"): версия непустая, запасная
# не включалась, ссылка на архив выходила мусорной. Отдельный xray не ставился (egress тихо уходил на xray
# панели), а установка по умолчанию обрывалась на скачивании 3X-UI (404).
#
# Без root и без сети: curl подменён в PATH — отдаёт заданный ответ API и редиректа, пишет в журнал каждый
# запрошенный URL, вместо архивов с GitHub кладёт собранные тут же, а на ссылку с «тегом» из нескольких частей
# отвечает 404 (curl -f → 22), как GitHub. Ветка sed проверяется с python3, который падает (exit 127).
# Каждый вызов идёт дважды: как обычно и под set -euo pipefail, как в govless.sh, — разбор версии не должен
# ронять скрипт.
#
# Запуск:  bash tools/github_tag_selftest.sh        (exit 0 = всё зелёное)
# Обвязка идёт и в bash 3.2, но lib/ goVLESS требует bash ≥ 4.2 (declare -gA): функции запускаются
# в первом подходящем bash — $BASH, bash из PATH, /opt/homebrew/bin/bash, /usr/local/bin/bash.
set -u
unset EGRESS_XVER_FALLBACK EGRESS_DIR
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
pass=0; fail=0
ok(){ printf '  \033[32mPASS\033[0m %s\n' "$1"; pass=$((pass+1)); }
no(){ printf '  \033[31mFAIL\033[0m %s\n' "$1"; [ -n "${2:-}" ] && printf '       %s\n' "$2"; fail=$((fail+1)); }

bash_ok(){ "$1" -c '[ "${BASH_VERSINFO[0]}" -gt 4 ] || { [ "${BASH_VERSINFO[0]}" -eq 4 ] && [ "${BASH_VERSINFO[1]}" -ge 2 ]; }' 2>/dev/null; }
GV_BASH=""
for b in "${BASH:-}" "$(command -v bash 2>/dev/null)" /opt/homebrew/bin/bash /usr/local/bin/bash; do
    [ -n "$b" ] && [ -x "$b" ] && bash_ok "$b" && { GV_BASH="$b"; break; }
done
[ -n "$GV_BASH" ] || { echo "нужен bash ≥ 4.2 (lib/ goVLESS: declare -gA) — не найден"; exit 2; }
command -v python3 >/dev/null 2>&1 || { echo "нужен python3 (он же нужен goVLESS)"; exit 2; }
echo "  bash для функций: $GV_BASH ($("$GV_BASH" -c 'echo "$BASH_VERSION"'))"

RE='^v[0-9]+\.[0-9]+\.[0-9]+$'
NEW="v25.12.8"   # тег xray в ответах API — нарочно не равен запасному
FB=$(sed -n 's/^EGRESS_XVER_FALLBACK="\${EGRESS_XVER_FALLBACK:-\([^}]*\)}"$/\1/p' "$ROOT_DIR/lib/egress.sh")
P3="v3.0.2"      # последний стабильный 3.x в списке релизов 3X-UI (выше него — пререлиз v3.1.0)
P2="v2.8.11"     # последний стабильный 2.x там же — нарочно не равен запасному
RD="v3.0.9"      # куда ведёт редирект github.com/…/releases/latest — нарочно не равен P3
LFB=$(sed -n 's/^XUI_LEGACY_FALLBACK="\([^"]*\)".*/\1/p' "$ROOT_DIR/lib/xui.sh")
RDR_OK="https://github.com/MHSanaei/3x-ui/releases/tag/${RD}"
mkdir -p "$TMP/bin" "$TMP/nopy" "$TMP/zip" "$TMP/tgz/x-ui"

# Поддельный curl. api.github.com: Xray-core/releases/latest — $FAKE_API (код $FAKE_API_RC);
# 3x-ui/releases?… — $FAKE_LIST, 3x-ui/releases/latest — $FAKE_XUI_LATEST (оба с кодом $FAKE_LIST_RC).
# github.com/MHSanaei/3x-ui/releases/latest с -w '%{url_effective}' печатает $FAKE_REDIRECT (и при ошибке,
# как настоящий curl) и выходит с $FAKE_REDIRECT_RC. releases/download/<тег>/<архив> — копия $FAKE_ZIP или
# $FAKE_TGZ в файл после -o; «тег» из нескольких частей GitHub не знает — 404, curl -f выходит с 22.
# Остальное — 22. Каждый URL — строкой в $FAKE_LOG.
cat > "$TMP/bin/curl" <<'MOCK'
#!/bin/sh
out="" url="" wfmt="" src=""
while [ $# -gt 0 ]; do
    case "$1" in
        -o) out="$2"; shift 2 ;;
        -w) wfmt="$2"; shift 2 ;;
        http://*|https://*) url="$1"; shift ;;
        *) shift ;;
    esac
done
printf '%s\n' "$url" >> "$FAKE_LOG"
emit() { if [ -n "$1" ] && [ -f "$1" ]; then cat "$1"; fi; }
case "$url" in
    https://api.github.com/repos/XTLS/Xray-core/releases/latest)
        emit "${FAKE_API:-}"; exit "${FAKE_API_RC:-0}" ;;
    https://api.github.com/repos/MHSanaei/3x-ui/releases/latest)
        emit "${FAKE_XUI_LATEST:-}"; exit "${FAKE_LIST_RC:-0}" ;;
    https://api.github.com/repos/MHSanaei/3x-ui/releases\?*)
        emit "${FAKE_LIST:-}"; exit "${FAKE_LIST_RC:-0}" ;;
    https://github.com/MHSanaei/3x-ui/releases/latest)
        case "$wfmt" in *url_effective*) printf '%s' "${FAKE_REDIRECT:-}" ;; esac
        exit "${FAKE_REDIRECT_RC:-0}" ;;
    https://github.com/*/releases/download/*/*/*) exit 22 ;;
    https://github.com/XTLS/Xray-core/releases/download/*/Xray-linux-*.zip) src="${FAKE_ZIP:-}" ;;
    https://github.com/MHSanaei/3x-ui/releases/download/*/x-ui-linux-*.tar.gz) src="${FAKE_TGZ:-}" ;;
    *) exit 22 ;;
esac
[ -n "$out" ] && [ -f "$src" ] && cp "$src" "$out" && exit 0
exit 22
MOCK
printf '#!/bin/sh\nexit 127\n' > "$TMP/nopy/python3"
chmod +x "$TMP/bin/curl" "$TMP/nopy/python3"
# Распаковка — не предмет проверки. Без unzip в системе egress_ensure_xray распаковывает архив python3-м,
# а в прогонах nopy он падает нарочно, — тогда даём прокладку unzip на настоящем python3.
if ! command -v unzip >/dev/null 2>&1; then
    cat > "$TMP/bin/unzip" <<SHIM
#!/bin/sh
zf="" mem="" dir="."
while [ \$# -gt 0 ]; do
    case "\$1" in -d) dir="\$2"; shift 2 ;; -*) shift ;; *) if [ -z "\$zf" ]; then zf="\$1"; else mem="\$1"; fi; shift ;; esac
done
exec "$(command -v python3)" -c 'import sys,zipfile; zipfile.ZipFile(sys.argv[1]).extract(sys.argv[2], sys.argv[3])' "\$zf" "\$mem" "\$dir"
SHIM
    chmod +x "$TMP/bin/unzip"
fi

# «Релизы»: архив xray (внутри xray, который печатает свою версию) и tarball 3X-UI.
printf '#!/bin/sh\necho "Xray 25.12.8 (selftest)"\n' > "$TMP/zip/xray"; chmod +x "$TMP/zip/xray"
ZIP="$TMP/Xray.zip"
if command -v zip >/dev/null 2>&1; then (cd "$TMP/zip" && zip -q "$ZIP" xray)
else python3 -c 'import sys,zipfile; z=zipfile.ZipFile(sys.argv[1],"w"); z.write(sys.argv[2],"xray"); z.close()' "$ZIP" "$TMP/zip/xray"; fi
printf '#!/bin/sh\necho "x-ui (selftest)"\n' > "$TMP/tgz/x-ui/x-ui"; chmod +x "$TMP/tgz/x-ui/x-ui"
TGZ="$TMP/x-ui.tar.gz"; tar -czf "$TGZ" -C "$TMP/tgz" x-ui

# Ответы API. Порядок ключей как у GitHub: "url" первым, tag_name в середине, body в конце — в body
# экранированный "tag_name" с чужим значением, его брать нельзя.
cat > "$TMP/xray.pretty.json" <<JSON
{
  "url": "https://api.github.com/repos/XTLS/Xray-core/releases/302486999",
  "assets_url": "https://api.github.com/repos/XTLS/Xray-core/releases/302486999/assets",
  "html_url": "https://github.com/XTLS/Xray-core/releases/tag/${NEW}",
  "id": 302486999,
  "author": {
    "login": "RPRX",
    "url": "https://api.github.com/users/RPRX"
  },
  "tag_name": "${NEW}",
  "target_commitish": "main",
  "name": "Xray-core ${NEW}",
  "draft": false,
  "prerelease": false,
  "assets": [
    {
      "name": "Xray-linux-64.zip",
      "browser_download_url": "https://github.com/XTLS/Xray-core/releases/download/${NEW}/Xray-linux-64.zip"
    }
  ],
  "body": "Old API clients: \"tag_name\": \"v0.0.1\" is not the tag"
}
JSON
cat > "$TMP/xui.latest.pretty.json" <<JSON
{
  "url": "https://api.github.com/repos/MHSanaei/3x-ui/releases/251390044",
  "html_url": "https://github.com/MHSanaei/3x-ui/releases/tag/${P3}",
  "id": 251390044,
  "tag_name": "${P3}",
  "name": "${P3}",
  "draft": false,
  "prerelease": false,
  "assets": [
    {
      "name": "x-ui-linux-amd64.tar.gz",
      "browser_download_url": "https://github.com/MHSanaei/3x-ui/releases/download/${P3}/x-ui-linux-amd64.tar.gz"
    }
  ],
  "body": "Old API clients: \"tag_name\": \"v0.0.1\" is not the tag"
}
JSON
# Список релизов 3X-UI: сверху пререлиз 3.x (его брать нельзя), затем стабильные 3.x и 2.x.
rel() {  # rel <id> <tag> <prerelease>
    printf '  {\n    "url": "https://api.github.com/repos/MHSanaei/3x-ui/releases/%s",\n' "$1"
    printf '    "html_url": "https://github.com/MHSanaei/3x-ui/releases/tag/%s",\n    "id": %s,\n' "$2" "$1"
    printf '    "tag_name": "%s",\n    "name": "%s",\n    "draft": false,\n    "prerelease": %s,\n' "$2" "$2" "$3"
    printf '    "body": "Old API clients: \\"tag_name\\": \\"v0.0.1\\" is not the tag"\n  }'
}
{ echo '['; rel 251400001 v3.1.0 true; echo ','; rel 251390044 "$P3" false; echo ','
  rel 251380000 "$P2" false; echo ','; rel 251370000 v2.8.10 false; echo; echo ']'; } > "$TMP/list.pretty.json"
{ echo '['; rel 251400001 v3.1.0 true; echo ','; rel 251380000 "$P2" false; echo; echo ']'; } > "$TMP/list.nostable3.json"
# Те же ответы одной строкой, без пробелов и без перевода строки в конце — как их отдаёт GitHub на Aeza.
minify(){ tr -d '\n' < "$1" | sed 's/^ *//; s/" *: */":/g; s/, *"/,"/g; s/, *{/,{/g; s/{ */{/g; s/ *}/}/g; s/\[ */[/g; s/ *]/]/g'; }
minify "$TMP/xray.pretty.json" > "$TMP/xray.min.json"
minify "$TMP/xui.latest.pretty.json" > "$TMP/xui.latest.min.json"
minify "$TMP/list.pretty.json" > "$TMP/list.min.json"
printf '<html><body><h1>502 Bad Gateway</h1></body></html>\n' > "$TMP/html.txt"
printf '{"message":"API rate limit exceeded for 203.0.113.10.","documentation_url":"https://docs.github.com/rest"}' > "$TMP/ratelimit.json"
head -c 300 "$TMP/list.min.json" > "$TMP/list.trunc.json"   # обрыв на середине (таймаут)
: > "$TMP/empty.json"

# ── обвязка: egress_ensure_xray ─────────────────────────────────────────────────────────────────────────
# $1 — файл ответа API, $2 — код выхода curl на запрос API, $3 — py | nopy (python3 падает → работает sed),
# $4 — plain | strict (strict: set -euo pipefail до подключения lib/, как в govless.sh, и прямой вызов).
# Итог: OUT — вывод; RC — что напечатано после «RC=» (пусто — скрипт умер, не дойдя до конца);
# VER — версия из ссылки на архив (между download/ и /Xray-linux-…zip); NAPI — сколько раз спросили API.
egr_run() {
    rm -rf "$TMP/egress"; : > "$TMP/curl.log"
    local p="$TMP/bin:$PATH" dl; [ "$3" = nopy ] && p="$TMP/nopy:$p"
    OUT=$(PATH="$p" FAKE_API="$1" FAKE_API_RC="$2" FAKE_ZIP="$ZIP" FAKE_LOG="$TMP/curl.log" \
          EGRESS_DIR="$TMP/egress" GT_ROOT="$ROOT_DIR" GT_TMP="$TMP" GT_MODE="$4" "$GV_BASH" -c '
        if [ "$GT_MODE" = strict ]; then set -euo pipefail; else set -uo pipefail; fi
        source "$GT_ROOT/lib/common.sh"; source "$GT_ROOT/lib/i18n.sh"; load_language ru
        source "$GT_ROOT/lib/egress.sh"
        case "$EGRESS_XRAY" in "$GT_TMP"/*) ;; *) echo "RC=96 (EGRESS_DIR не во временном каталоге)"; exit 96 ;; esac
        egress_ensure_xray; echo "RC=$?"
    ' 2>&1 </dev/null)
    RC=$(printf '%s\n' "$OUT" | sed -n 's/^RC=//p' | tail -n1)
    dl=$(grep -m1 '/releases/download/' "$TMP/curl.log"); VER=""
    case "$dl" in https://github.com/XTLS/Xray-core/releases/download/*/Xray-linux-*.zip)
        VER=${dl#https://github.com/XTLS/Xray-core/releases/download/}; VER=${VER%/Xray-linux-*.zip} ;; esac
    NAPI=$(grep -c '^https://api.github.com/repos/XTLS/Xray-core/releases/latest$' "$TMP/curl.log")
}
# egr_expect <ждём версию> <что> <файл ответа> [код curl]: python3 и sed, обычный вызов и под set -euo pipefail.
# Везде ждём ссылку на архив с этой версией и RC=0 (архив скачан, xray на месте, скрипт жив).
egr_expect() {
    local want="$1" what="$2" f="$3" rc="${4:-0}" py mode
    for py in py nopy; do for mode in plain strict; do
        egr_run "$f" "$rc" "$py" "$mode"
        if [ "$VER" = "$want" ] && [ "$RC" = "0" ]; then ok "${what} [${py} ${mode}] → ${want}"
        elif [ -z "$RC" ]; then
            no "${what} [${py} ${mode}]: скрипт умер, до «RC=» не дошёл (качалось «${VER:-ничего}»)" "$(printf '%s\n' "$OUT" | tail -n2 | tr '\n' ' ')"
        else no "${what} [${py} ${mode}]: ждали ${want}, качается «${VER:-ничего}», RC=${RC}" "$(tail -n2 "$TMP/curl.log" | tr '\n' ' ')"; fi
    done; done
}

# ── обвязка: xui_resolve_version ────────────────────────────────────────────────────────────────────────
# Состояние «сети» задаёт net: <список релизов (файл)> <код curl> <куда ведёт редирект> <код curl на редирект>.
# На api …/3x-ui/releases/latest (его спрашивал прежний разбор) отдаётся $XL.
net(){ L="$1"; LRC="$2"; RDR="$3"; RRC="$4"; }
XL="$TMP/xui.latest.min.json"
# $1 — XUI_BRANCH, $2 — py | nopy, $3 — plain | strict. Итог: V — что напечатала функция; RC — «RC=…»;
# IRC — код выхода bash; NRDR — запросов редиректа; NOLD — запросов старого api …/3x-ui/releases/latest.
xui_run() {
    : > "$TMP/curl.log"
    local p="$TMP/bin:$PATH"; [ "$2" = nopy ] && p="$TMP/nopy:$p"
    OUT=$(PATH="$p" FAKE_LIST="$L" FAKE_LIST_RC="$LRC" FAKE_XUI_LATEST="$XL" FAKE_REDIRECT="$RDR" \
          FAKE_REDIRECT_RC="$RRC" FAKE_LOG="$TMP/curl.log" GT_ROOT="$ROOT_DIR" GT_BRANCH="$1" GT_MODE="$3" "$GV_BASH" -c '
        if [ "$GT_MODE" = strict ]; then set -euo pipefail; else set -uo pipefail; fi
        source "$GT_ROOT/lib/common.sh"; source "$GT_ROOT/lib/i18n.sh"; load_language ru
        source "$GT_ROOT/lib/xui.sh"
        XUI_BRANCH="$GT_BRANCH"
        xui_resolve_version; echo "RC=$?"
    ' 2>"$TMP/err.log" </dev/null); IRC=$?
    RC=$(printf '%s\n' "$OUT" | sed -n 's/^RC=//p' | tail -n1)
    V=$(printf '%s\n' "$OUT" | grep -v '^RC=' | head -n1)
    NRDR=$(grep -c '^https://github.com/MHSanaei/3x-ui/releases/latest$' "$TMP/curl.log")
    NOLD=$(grep -c '^https://api.github.com/repos/MHSanaei/3x-ui/releases/latest$' "$TMP/curl.log")
}
# xui_expect <ветка> <ждём с python3> <ждём без python3> <что>. ERR — функция возвращает 1 и ничего не печатает.
# Под set -euo pipefail удача — дошли до «RC=0»; ошибка — bash вышел ровно с 1, то есть сработал её return 1,
# а не errexit на упавшей команде (он вышел бы с её кодом: у curl 22 или 6).
# Редирект спрашивают только для ветки new и только если API версию не дал; старый api …/releases/latest — никогда.
xui_expect() {
    local br="$1" w_py="$2" w_nopy="$3" what="$4" py mode want rdr bad
    for py in py nopy; do
        want="$w_py"; [ "$py" = nopy ] && want="$w_nopy"
        rdr=0; [ "$br" != legacy ] && [ "$want" != "$P3" ] && rdr=1
        for mode in plain strict; do
            xui_run "$br" "$py" "$mode"; bad=""
            if [ "$want" = ERR ]; then
                [ -z "$V" ] || bad="напечатала «${V}»;"
                if [ "$mode" = strict ]; then [ "$IRC" = 1 ] || bad="${bad} bash вышел с ${IRC}, ждали 1;"
                else [ "$RC" = 1 ] || bad="${bad} RC=«${RC}», ждали 1;"; fi
            else
                [ "$V" = "$want" ] || bad="версия «${V}», ждали ${want};"
                [ "$RC" = 0 ] || bad="${bad} RC=«${RC}» — до конца не дошли;"
            fi
            [ "$NRDR" = "$rdr" ] || bad="${bad} редирект спрошен ${NRDR} раз, ждали ${rdr};"
            [ "$NOLD" = 0 ] || bad="${bad} спрошен старый api …/releases/latest;"
            if [ -z "$bad" ]; then ok "${what} [${br:-пусто} ${py} ${mode}] → ${want}"
            else no "${what} [${br:-пусто} ${py} ${mode}]: ${bad}" "$(tr '\n' ' ' < "$TMP/curl.log")"; fi
        done
    done
}

echo "── 1. статика ──"
for f in lib/egress.sh lib/xui.sh; do
    "$GV_BASH" -n "$ROOT_DIR/$f" 2>/dev/null && ok "bash -n $f" || no "bash -n $f"
done
grep -q '^egress_ensure_xray() *{' "$ROOT_DIR/lib/egress.sh" && ok "есть egress_ensure_xray()" || no "нет egress_ensure_xray()"
grep -q '^xui_resolve_version() *{' "$ROOT_DIR/lib/xui.sh" && ok "есть xui_resolve_version()" || no "нет xui_resolve_version()"
awk '/^install_3xui\(\)/,/^}/' "$ROOT_DIR/lib/xui.sh" | grep -qF 'version=$(xui_resolve_version)' \
    && ok "install_3xui берёт версию у xui_resolve_version" || no "install_3xui не зовёт xui_resolve_version"
[[ "$FB" =~ $RE ]] && ok "запасная версия xray EGRESS_XVER_FALLBACK=${FB} — вида vX.Y.Z" || no "запасная версия xray не найдена или не vX.Y.Z: «${FB}»"
[ "$FB" != "$NEW" ] && ok "тег ответа (${NEW}) отличается от запасного" || no "тег ответа совпал с запасным — проверка ничего не докажет"
[[ "$LFB" =~ ^v2\.[0-9]+\.[0-9]+$ ]] && [ "$LFB" != "$P2" ] && ok "запасная 2.x XUI_LEGACY_FALLBACK=${LFB} — вида v2.X.Y и не равна ${P2}" \
    || no "запасная 2.x не найдена, не v2.X.Y или совпала с ${P2}: «${LFB}»"
# Построчный разбор tag_name в коде продукта (комментарии не в счёт: они как раз объясняют старую ошибку;
# тесты не в счёт: в них этот разбор — образец ошибки).
bad=$(cd "$ROOT_DIR" && grep -rn --include='*.sh' 'tag_name' govless.sh bootstrap.sh install.sh lib phase-a 2>/dev/null \
      | grep -vE '^[^:]+:[0-9]+:[[:space:]]*#' | grep -E "cut -d|grep (-m1 )?'\"tag_name\"'")
[ -z "$bad" ] && ok "tag_name нигде не режется grep | cut по строкам" || no "остался построчный разбор tag_name" "$bad"

echo "── 2. ответы-образцы ──"
for f in xray.min.json xui.latest.min.json list.min.json; do
    [ "$(wc -l < "$TMP/$f" | tr -d ' ')" = "0" ] && ok "${f}: одна строка без перевода строки" || no "${f} не в одну строку"
done
v=$(grep -m1 '"tag_name"' "$TMP/xray.min.json" | cut -d'"' -f4)
[ "$v" = "https://api.github.com/repos/XTLS/Xray-core/releases/302486999" ] \
    && ok "старый разбор xray на одной строке даёт «url» — ошибка воспроизводится" || no "старый разбор xray на одной строке дал «${v}»"
v=$(grep '"tag_name"' "$TMP/xui.latest.min.json" | head -1 | cut -d'"' -f4)
[ "$v" = "https://api.github.com/repos/MHSanaei/3x-ui/releases/251390044" ] \
    && ok "старый разбор 3X-UI на одной строке даёт «url» — ошибка воспроизводится" || no "старый разбор 3X-UI на одной строке дал «${v}»"
v=$(grep -m1 '"tag_name"' "$TMP/xray.pretty.json" | cut -d'"' -f4)
[ "$v" = "$NEW" ] && ok "старый разбор с переносами давал ${NEW} — потому ошибка и проявлялась не везде" || no "старый разбор с переносами дал «${v}»"
python3 -c 'import json,sys; [json.load(open(f)) for f in sys.argv[1:]]' "$TMP"/xray.*.json "$TMP"/xui.latest.*.json \
    "$TMP/list.pretty.json" "$TMP/list.min.json" "$TMP/list.nostable3.json" 2>/dev/null \
    && ok "все образцы — корректный JSON" || no "образец не разбирается как JSON"

echo "── 3. egress_ensure_xray: JSON одной строкой ──"
egr_run "$TMP/xray.min.json" 0 py plain
[ "$VER" = "$NEW" ] && ok "качается ${NEW}, а не запасная версия" || no "качается «${VER:-ничего}»" "$(tr '\n' ' ' < "$TMP/curl.log")"
[ "$NAPI" = "1" ] && ok "API спрошен один раз" || no "API спрошен ${NAPI} раз"
printf '%s' "$OUT" | grep -qF "(${NEW})" && ok "в сообщении о загрузке — ${NEW}" || no "сообщение без версии" "$(printf '%s' "$OUT" | head -2 | tr '\n' ' ')"
[ "$RC" = "0" ] && ok "egress_ensure_xray вернул 0" || no "egress_ensure_xray вернул ${RC:-?}" "$(printf '%s' "$OUT" | tail -2 | tr '\n' ' ')"
[ -x "$TMP/egress/xray" ] && [ "$("$TMP/egress/xray")" = "Xray 25.12.8 (selftest)" ] && ok "xray распакован и запускается" || no "xray не на месте"
[ ! -e "$TMP/egress/.xray.zip" ] && [ ! -e "$TMP/egress/.x" ] && ok "архив и каталог распаковки убраны" || no "остались .xray.zip или .x"
egr_expect "$NEW" "JSON одной строкой" "$TMP/xray.min.json"

echo "── 4. egress_ensure_xray: JSON с переносами (как раньше) ──"
egr_expect "$NEW" "JSON с переносами" "$TMP/xray.pretty.json"

echo "── 5. egress_ensure_xray: плохие ответы → запасная версия ──"
egr_expect "$FB" "пустой ответ" "$TMP/empty.json"
egr_expect "$FB" "мусор (HTML вместо JSON)" "$TMP/html.txt"
egr_expect "$FB" "JSON без tag_name (лимит запросов)" "$TMP/ratelimit.json"
egr_expect "$FB" "curl с ошибкой (HTTP 403 при -f), хотя что-то напечатал" "$TMP/xray.min.json" 22
egr_expect "$FB" "сети нет (curl 6), ответа нет" "$TMP/nothing" 6
for t in 'v26.3.27-rc1' 'latest' 'v26.3' '26.3.27' 'v1.2.3/../x' 'v1.2.3 '; do
    sed "s|\"tag_name\":\"$NEW\"|\"tag_name\":\"${t}\"|" "$TMP/xray.min.json" > "$TMP/bad.min.json"
    sed "s|\"tag_name\": \"$NEW\"|\"tag_name\": \"${t}\"|" "$TMP/xray.pretty.json" > "$TMP/bad.pretty.json"
    grep -qF "\"tag_name\":\"${t}\"" "$TMP/bad.min.json" && grep -qF "\"tag_name\": \"${t}\"" "$TMP/bad.pretty.json" \
        || { no "образец с tag_name «${t}» не собрался"; continue; }
    egr_expect "$FB" "tag_name «${t}», одной строкой" "$TMP/bad.min.json"
    egr_expect "$FB" "tag_name «${t}», с переносами" "$TMP/bad.pretty.json"
done
for t in 'null' '123'; do
    sed "s|\"tag_name\":\"$NEW\"|\"tag_name\":${t}|" "$TMP/xray.min.json" > "$TMP/bad.min.json"
    egr_expect "$FB" "tag_name не строка (${t})" "$TMP/bad.min.json"
done

echo "── 6. egress_ensure_xray: свой xray уже есть ──"
rm -rf "$TMP/egress"; mkdir -p "$TMP/egress"; cp "$TMP/zip/xray" "$TMP/egress/xray"; : > "$TMP/curl.log"
OUT=$(PATH="$TMP/bin:$PATH" FAKE_API="$TMP/xray.min.json" FAKE_ZIP="$ZIP" FAKE_LOG="$TMP/curl.log" EGRESS_DIR="$TMP/egress" \
      GT_ROOT="$ROOT_DIR" "$GV_BASH" -c '
    set -euo pipefail
    source "$GT_ROOT/lib/common.sh"; source "$GT_ROOT/lib/i18n.sh"; load_language ru
    source "$GT_ROOT/lib/egress.sh"; egress_ensure_xray; echo "RC=$?"' </dev/null 2>&1)
[ "$(printf '%s\n' "$OUT" | sed -n 's/^RC=//p')" = "0" ] && [ ! -s "$TMP/curl.log" ] \
    && ok "возвращает 0 и в сеть не ходит" || no "при готовом xray ходил в сеть или вернул не 0" "$(tr '\n' ' ' < "$TMP/curl.log")"

echo "── 7. xui_resolve_version: версия из API, из редиректа, запасная 2.x ──"
net "$TMP/list.min.json" 0 "$RDR_OK" 0
xui_expect new "$P3" "$RD" "список релизов одной строкой"
xui_expect "" "$P3" "$RD" "XUI_BRANCH не задан — как new, список одной строкой"
net "$TMP/list.pretty.json" 0 "$RDR_OK" 0
xui_expect new "$P3" "$RD" "список релизов с переносами"
net "$TMP/list.nostable3.json" 0 "$RDR_OK" 0
xui_expect new "$RD" "$RD" "в списке нет стабильного 3.x (только пререлиз) → редирект"
for s in "empty.json 0" "html.txt 0" "ratelimit.json 0" "list.trunc.json 28" "nothing 6" "nothing 22"; do
    set -- $s; net "$TMP/$1" "$2" "$RDR_OK" 0
    xui_expect new "$RD" "$RD" "API: ${1} (curl ${2}) → редирект"
done
net "$TMP/list.min.json" 0 "$RDR_OK" 0
xui_expect legacy "$P2" "$LFB" "legacy: список одной строкой"
net "$TMP/ratelimit.json" 0 "$RDR_OK" 0
xui_expect legacy "$LFB" "$LFB" "legacy: лимит API → запасная ${LFB}, редирект не нужен"

echo "── 8. xui_resolve_version: API не ответил и редирект плохой → return 1 ──"
RB="https://github.com/MHSanaei/3x-ui/releases"
for s in "${RB}/tag/${RD}|22" "https://github.com/MHSanaei/3x-ui/releases/latest|6" "|0" "${RB}|0" \
         "${RB}/tag/v3.0.9-rc1|0" "${RB}/tag/latest|0" "${RB}/tag/v3.0|0" "${RB}/tag/3.0.9|0" \
         "${RB}/tag/v1.2.3/../x|0" "${RB}/tag/null|0" "${RB}/tag/123|0" "${RB}/tag/|0" "${RB}/tag/v4.0.0|0" \
         "https://github.com/login?return_to=%2FMHSanaei%2F3x-ui%2Freleases%2Flatest|0"; do
    net "$TMP/ratelimit.json" 0 "${s%|*}" "${s##*|}"
    xui_expect new ERR ERR "редирект «${s%|*}» (curl ${s##*|})"
done

echo "── 9. install_3xui: шаг «2. Resolve version» как есть, со ссылкой на архив и скачиванием ──"
# Шаг вырезается из install_3xui целиком (до «3. Download and extract») вместе со строкой tarball_url —
# установщик целиком не запускается: он трогает /usr/local, systemd и /tmp.
STEP2=$(awk '/# ── 2\. Resolve version/{f=1} /# ── 3\. Download and extract/{f=0} f' "$ROOT_DIR/lib/xui.sh")
TBURL=$(grep -m1 'local tarball_url=' "$ROOT_DIR/lib/xui.sh")
if [ -n "$STEP2" ] && [ -n "$TBURL" ]; then ok "шаг 2 и строка tarball_url найдены в install_3xui"
else no "не найден шаг «2. Resolve version» или tarball_url в lib/xui.sh"; fi
{ echo 'xui_step2() {'
  echo '    local version="${1:-}" arch=amd64'
  printf '%s\n' "$STEP2" "$TBURL"
  echo '    printf "VERSION=%s\nURL=%s\n" "$version" "$tarball_url"'
  echo '    if gv_fetch "$tarball_url" "$GT_TMP/x.tgz" 30 >/dev/null 2>&1 && _is_gzip "$GT_TMP/x.tgz"; then echo DL=ok; else echo DL=fail; fi'
  echo '}'; } > "$TMP/step2.sh"
# $1 — версия на входе install_3xui ("" — «Pro — авто»), $2 — plain | strict
step_run() {
    : > "$TMP/curl.log"; rm -f "$TMP/x.tgz"
    OUT=$(PATH="$TMP/bin:$PATH" FAKE_LIST="$L" FAKE_LIST_RC="$LRC" FAKE_XUI_LATEST="$XL" FAKE_REDIRECT="$RDR" \
          FAKE_REDIRECT_RC="$RRC" FAKE_TGZ="$TGZ" FAKE_LOG="$TMP/curl.log" GT_ROOT="$ROOT_DIR" GT_TMP="$TMP" \
          GT_VER="$1" GT_MODE="$2" "$GV_BASH" -c '
        if [ "$GT_MODE" = strict ]; then set -euo pipefail; else set -uo pipefail; fi
        source "$GT_ROOT/lib/common.sh"; source "$GT_ROOT/lib/i18n.sh"; load_language ru
        source "$GT_ROOT/lib/xui.sh"; source "$GT_TMP/step2.sh"
        XUI_BRANCH=new
        xui_step2 "$GT_VER"; echo "RC=$?"
    ' 2>"$TMP/err.log" </dev/null); IRC=$?
    SV=$(printf '%s\n' "$OUT" | sed -n 's/^VERSION=//p'); SU=$(printf '%s\n' "$OUT" | sed -n 's/^URL=//p')
    SD=$(printf '%s\n' "$OUT" | sed -n 's/^DL=//p'); RC=$(printf '%s\n' "$OUT" | sed -n 's/^RC=//p')
}
# step_expect <версия на входе> <ждём версию> <что>: в обоих режимах ссылка на архив этой версии, архив скачан.
step_expect() {
    local in="$1" want="$2" what="$3" mode url="https://github.com/MHSanaei/3x-ui/releases/download/${2}/x-ui-linux-amd64.tar.gz"
    for mode in plain strict; do
        step_run "$in" "$mode"
        if [ "$SV" = "$want" ] && [ "$SU" = "$url" ] && [ "$SD" = ok ] && [ "$RC" = 0 ]; then ok "${what} [${mode}] → ${want}, архив скачан"
        else no "${what} [${mode}]: версия «${SV}», архив ${SD:-не качали}, RC=«${RC}», ждали ${want}" "$(tr '\n' ' ' < "$TMP/curl.log")"; fi
    done
}
net "$TMP/list.min.json" 0 "$RDR_OK" 0; XL="$TMP/xui.latest.min.json"
step_expect "" "$P3" "«Pro — авто», API одной строкой"
net "$TMP/list.pretty.json" 0 "$RDR_OK" 0; XL="$TMP/xui.latest.pretty.json"
step_expect "" "$P3" "«Pro — авто», API с переносами"
net "$TMP/ratelimit.json" 0 "$RDR_OK" 0; XL="$TMP/ratelimit.json"
step_expect "" "$RD" "«Pro — авто», лимит API → редирект"
net "$TMP/list.min.json" 0 "$RDR_OK" 0; XL="$TMP/xui.latest.min.json"
step_expect "v2.9.4" "v2.9.4" "версия задана — как есть"
[ "$(grep -cE 'api\.github\.com|releases/latest' "$TMP/curl.log")" = 0 ] && ok "версия задана — ни API, ни редиректа не спрашивали" \
    || no "версия задана, а сеть всё равно спрашивали" "$(tr '\n' ' ' < "$TMP/curl.log")"
net "$TMP/ratelimit.json" 0 "${RB}/tag/${RD}" 22; XL="$TMP/ratelimit.json"
for mode in plain strict; do
    step_run "" "$mode"; want_rc=1; [ "$mode" = strict ] && want_rc=""
    if [ -z "$SV" ] && [ "$RC" = "$want_rc" ] && [ "$IRC" = "$([ "$mode" = strict ] && echo 1 || echo 0)" ] \
       && grep -qF 'Не удалось определить последнюю версию 3X-UI' "$TMP/err.log"; then
        ok "версию не узнать [${mode}] → «Не удалось определить последнюю версию 3X-UI», return 1"
    else no "версию не узнать [${mode}]: версия «${SV}», RC=«${RC}», bash ${IRC}" "$(tail -n2 "$TMP/err.log" | tr '\n' ' ')"; fi
done

echo "────────────────────────────"
echo "  PASS=$pass  FAIL=$fail"
[ "$fail" = 0 ]
