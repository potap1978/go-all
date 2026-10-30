#!/usr/bin/env bash
# live_branch_switch_check.sh — проверить переезд между ветками на ЖИВОМ сервере,
# против настоящего приватного репозитория на GitHub.
#
# Отвечает на один вопрос: реально ли загрузчик переключает уже установленный
# goVLESS с одной ветки на другую, или только печатает «Updated».
#
# БЕЗОПАСНО: всё происходит во временном каталоге. /opt/govless-installer,
# /opt/govless, 3X-UI, nginx и сам VPN не трогаются, установщик не запускается.
#
#   GH_TOKEN=<токен> bash tools/live_branch_switch_check.sh            # rc1 -> main
#   GH_TOKEN=<токен> FROM=dev TO=main bash tools/live_branch_switch_check.sh
#
# Нужно: root, git, curl. GH_TOKEN — если git ещё не настроен на этот репозиторий.
set -u

G='\033[0;32m'; R='\033[0;31m'; Y='\033[1;33m'; D='\033[2m'; N='\033[0m'
FAILED=0
ok(){   printf "  ${G}PASS${N} %s\n" "$1"; }
bad(){  printf "  ${R}FAIL${N} %s\n" "$1"; FAILED=1; }
info(){ printf "  ${D}%s${N}\n" "$1"; }
hdr(){  printf "\n  ${Y}── %s${N}\n" "$1"; }

[ "$(id -u)" = "0" ] || { echo "  нужен root"; exit 2; }
command -v git >/dev/null 2>&1 || { echo "  нужен git"; exit 2; }

REPO="${GOVLESS_REPO:-https://github.com/potap1978/go-all.git}"
FROM="${FROM:-rc1}"
TO="${TO:-main}"
# Ветка с исправлением. Когда фикс окажется в main, сюда можно ставить main.
FIX_BRANCH="${FIX_BRANCH:-claude/zealous-pasteur-tifvfy}"

export GIT_TERMINAL_PROMPT=0 GIT_ASKPASS=/bin/false
GIT_AUTH=()
if [ -n "${GH_TOKEN:-}" ]; then
    GIT_AUTH=(-c "http.${REPO}.extraHeader=Authorization: Basic $(printf 'x-access-token:%s' "$GH_TOKEN" | base64 -w0)")
fi

W="$(mktemp -d)"; trap 'rm -rf "$W"' EXIT
echo ""
echo "  live branch-switch check: $FROM -> $TO"
echo "  репозиторий: $REPO"
echo "  песочница:   $W   (ничего установленного не трогается)"

# ── тянем репозиторий один раз, чтобы достать оба загрузчика ────────────────
hdr "доступ к репозиторию"
if ! timeout 180 git "${GIT_AUTH[@]}" clone -q --bare "$REPO" "$W/mirror.git" 2>"$W/clone.err"; then
    bad "не клонируется — задайте GH_TOKEN (репозиторий приватный)"
    sed 's/^/      /' "$W/clone.err" | tail -3
    echo ""; exit 1
fi
ok "репозиторий доступен"
for b in "$FROM" "$TO" "$FIX_BRANCH"; do
    git --git-dir="$W/mirror.git" rev-parse --verify -q "refs/heads/$b" >/dev/null \
        && info "ветка $b: $(git --git-dir="$W/mirror.git" show "$b:lib/common.sh" | grep -m1 '^GOVLESS_VERSION=' || echo '?')" \
        || bad "нет ветки $b"
done
[ "$FAILED" = "0" ] || { echo ""; exit 1; }

ver_from="$(git --git-dir="$W/mirror.git" show "$FROM:lib/common.sh" | grep -oE '^GOVLESS_VERSION="[^"]+"' | cut -d'"' -f2)"
ver_to="$(git --git-dir="$W/mirror.git"   show "$TO:lib/common.sh"   | grep -oE '^GOVLESS_VERSION="[^"]+"' | cut -d'"' -f2)"
if [ "$ver_from" = "$ver_to" ]; then
    info "ВНИМАНИЕ: у $FROM и $TO одинаковый GOVLESS_VERSION ($ver_from) —"
    info "проверка пойдёт по HEAD, версия ничего не различит."
fi

# Загрузчик ТОЙ ветки, на которую переезжаем: goInstaller берёт bootstrap.sh
# именно оттуда. Правим ТОЛЬКО пути — каталог-песочницу и отказ от запуска
# установщика. Git-логика, которая и проверяется, не тронута.
sandbox_loader() { # $1 = ветка-источник bootstrap.sh, $2 = куда положить
    git --git-dir="$W/mirror.git" show "$1:bootstrap.sh" \
      | sed 's|^INSTALL_DIR="/opt/govless-installer"$|INSTALL_DIR="${GOVLESS_INSTALL_DIR:-/opt/govless-installer}"|' \
      | sed 's|^\( *\)exec bash govless\.sh .*$|\1exit 0|' > "$2"
    grep -q 'exec bash govless.sh' "$2" && { bad "не удалось обезвредить запуск установщика в $2"; return 1; }
    return 0
}
hb(){ git -C "$1" rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?'; }
gv(){ grep -oE '^GOVLESS_VERSION="[^"]+"' "$1/lib/common.sh" 2>/dev/null | head -1 | cut -d'"' -f2; }

run(){ # $1 = загрузчик, $2 = каталог, $3 = ветка -> печатает вывод
    GOVLESS_BOOTSTRAP_ONLY=1 GOVLESS_INSTALL_DIR="$2" GOVLESS_REPO="$REPO" \
    GOVLESS_BRANCH="$3" GH_TOKEN="${GH_TOKEN:-}" bash "$1" </dev/null 2>&1
}

# ── 1. как ведёт себя загрузчик, который сейчас лежит в $TO ─────────────────
hdr "1. загрузчик, который сейчас в ветке $TO"
sandbox_loader "$TO" "$W/loader.current.sh" || { echo ""; exit 1; }
INST="$W/inst.current"
run "$W/loader.current.sh" "$INST" "$FROM" >/dev/null
info "поставили $FROM -> HEAD=$(hb "$INST"), версия=$(gv "$INST")"
out="$(run "$W/loader.current.sh" "$INST" "$TO")"
printf '%s' "$out" | grep -E 'Updated|Could not|✗' | sed 's/^/      /'
CURRENT_BROKEN=0
if [ "$(hb "$INST")" = "$TO" ]; then
    ok "переезд $FROM -> $TO работает (HEAD=$(hb "$INST"), версия=$(gv "$INST"))"
    info "исправление уже в ветке $TO"
else
    CURRENT_BROKEN=1
    printf "  ${R}ДИАГНОЗ${N} HEAD остался на %s (версия %s) — баг воспроизведён\n" \
        "$(hb "$INST")" "$(gv "$INST")"
    printf '%s' "$out" | grep -qF "Updated to latest $TO" && \
        info "и при этом напечатано «Updated to latest $TO» — вот эта ложь и доходит до goInstaller"
    info "refspec клона: $(git -C "$INST" config --get-all remote.origin.fetch | tr '\n' ' ')"
    info "это не провал проверки: фикс просто ещё не выложен в $TO"
fi

# ── 2. загрузчик с исправлением ────────────────────────────────────────────
hdr "2. загрузчик с исправлением (ветка $FIX_BRANCH)"
sandbox_loader "$FIX_BRANCH" "$W/loader.fixed.sh" || { echo ""; exit 1; }
INST="$W/inst.fixed"
run "$W/loader.fixed.sh" "$INST" "$FROM" >/dev/null
info "поставили $FROM -> HEAD=$(hb "$INST"), версия=$(gv "$INST")"

# Клон делаем таким, какой он у всех уже установленных серверов: однобранчевым.
git -C "$INST" config --replace-all remote.origin.fetch "+refs/heads/$FROM:refs/remotes/origin/$FROM"
git -C "$INST" update-ref -d "refs/remotes/origin/$TO" 2>/dev/null || true
info "refspec сузили до однобранчевого — как на реально установленных серверах"

run "$W/loader.fixed.sh" "$INST" "$TO" | grep -E 'Updated|Could not|✗' | sed 's/^/      /'
[ "$(hb "$INST")" = "$TO" ] && ok "$FROM -> $TO: HEAD=$TO, версия=$(gv "$INST")" \
                         || bad "$FROM -> $TO не сработал: HEAD=$(hb "$INST")"
run "$W/loader.fixed.sh" "$INST" "$FROM" | grep -E 'Updated|✗' | sed 's/^/      /'
[ "$(hb "$INST")" = "$FROM" ] && ok "$TO -> $FROM: HEAD=$FROM, версия=$(gv "$INST")" \
                           || bad "$TO -> $FROM не сработал: HEAD=$(hb "$INST")"

rs="$(git -C "$INST" config --get-all remote.origin.fetch)"
[ "$(printf '%s\n' "$rs" | grep -c .)" = "1" ] && printf '%s' "$rs" | grep -qF '+refs/heads/*:refs/remotes/origin/*' \
    && ok "refspec: $rs" || bad "refspec не расширился: $rs"

# ── 3. репозиторий недоступен + просят другую ветку → отказ ────────────────
hdr "3. репозиторий недоступен, запрошена другая ветка"
git -C "$INST" remote set-url origin "file://$W/нет-такого.git"
out="$(GOVLESS_BOOTSTRAP_ONLY=1 GOVLESS_INSTALL_DIR="$INST" GOVLESS_REPO="file://$W/нет-такого.git" \
       GOVLESS_BRANCH="$TO" bash "$W/loader.fixed.sh" </dev/null 2>&1)"; rc=$?
[ "$rc" != "0" ] && ok "ненулевой код возврата ($rc)" || bad "код 0 — запустил бы не ту ветку"
printf '%s' "$out" | grep -qF 'Updated' && bad "напечатано «Updated»" || ok "«Updated» не печатается"
[ "$(hb "$INST")" = "$FROM" ] && ok "клон не тронут (HEAD=$FROM)" || bad "HEAD изменился: $(hb "$INST")"

echo ""
if [ "$FAILED" != "0" ]; then
    printf "  ${R}ЕСТЬ ПРОБЛЕМЫ${N} — см. FAIL выше\n\n"
    exit 1
fi
printf "  ${G}ИСПРАВЛЕНИЕ РАБОТАЕТ${N} — $FROM <-> $TO переключается по-настоящему\n"
if [ "${CURRENT_BROKEN:-0}" = "1" ]; then
    printf "  ${Y}НО${N} загрузчик в ветке $TO всё ещё старый: пока фикс не выложен в $TO,\n"
    printf "     goInstaller будет брать оттуда сломанный bootstrap.sh.\n\n"
    exit 2
fi
echo ""
exit 0
