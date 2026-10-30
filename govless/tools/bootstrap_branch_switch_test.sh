#!/usr/bin/env bash
# bootstrap_branch_switch_test.sh — drive the REAL bootstrap.sh through every
# branch-switch case, against a throwaway git repo on disk.
#
# Why it exists: goInstaller lets the operator move an installed goVLESS between
# main and dev. That used to print "✓ Updated to latest dev" and keep running
# main — the clone is single-branch, so `git fetch origin dev` never created
# origin/dev, both checkouts failed, and `|| true` hid all of it.
#
# The same loader also has to take over a clone of ANOTHER repository: the free
# goVLESS Lite from the public guide clones potap1978/go-all-lite-version into
# /opt/govless-installer, and the loader used to "update" that clone from the
# Lite repo and start Lite — goInstaller installed pro and opened the Lite menu.
#
# No network, no VPS, no 3X-UI: a local file:// origin with two branches and a
# stub govless.sh that prints its own GOVLESS_VERSION. Everything lands in a
# temp dir; /opt/govless-installer is never touched.
#
# Usage:  bash tools/bootstrap_branch_switch_test.sh
# Needs:  root (bootstrap.sh refuses to run otherwise) + git. Nothing else.
set -u

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

G='\033[0;32m'; R='\033[0;31m'; Y='\033[1;33m'; D='\033[2m'; N='\033[0m'
FAILED=0
ok(){   printf "  ${G}PASS${N} %s\n" "$1"; }
bad(){  printf "  ${R}FAIL${N} %s\n" "$1"; FAILED=1; }
info(){ printf "  ${D}%s${N}\n" "$1"; }
hdr(){  printf "\n  ${Y}── %s${N}\n" "$1"; }

[ "$(id -u)" = "0" ] || { echo "  нужен root — bootstrap.sh сам себя останавливает без него"; exit 2; }
command -v git >/dev/null 2>&1 || { echo "  нужен git"; exit 2; }

W="$(mktemp -d)"; trap 'rm -rf "$W"' EXIT
ORIGIN="$W/origin.git"
INSTALL="$W/installer"
GOOD_URL="file://$ORIGIN"
DEAD_URL="file://$W/no-such-repo.git"

g(){ git -c user.name=t -c user.email=t@t -c init.defaultBranch=main "$@"; }

# ── фальшивый upstream: две ветки, у каждой своя версия ────────────────────
mk_branch(){ # $1 branch, $2 version
    mkdir -p "$W/src/lib"
    printf 'GOVLESS_VERSION="%s"\n' "$2" > "$W/src/lib/common.sh"
    cat > "$W/src/govless.sh" <<'STUB'
#!/bin/bash
. lib/common.sh
echo "RAN version=$GOVLESS_VERSION"
STUB
}
g init -q "$W/src"
mk_branch main 1.3.6
g -C "$W/src" add -A && g -C "$W/src" commit -qm main
g -C "$W/src" checkout -qb dev && mk_branch dev 1.4.0
g -C "$W/src" commit -qam dev && g -C "$W/src" checkout -q main
g clone -q --bare "$W/src" "$ORIGIN"

OUT=""; RC=0
boot(){ # $1 branch, $2 repo url
    OUT="$(GOVLESS_INSTALL_DIR="$INSTALL" GOVLESS_REPO="$2" GOVLESS_BRANCH="$1" \
        bash "$ROOT_DIR/bootstrap.sh" </dev/null 2>&1)"; RC=$?
}
head_branch(){ git -C "$INSTALL" rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?'; }
says(){ printf '%s' "$OUT" | grep -qF "$1"; }

echo ""
echo "  bootstrap branch-switch test"
echo "  commit:  $(git -C "$ROOT_DIR" rev-parse --short HEAD 2>/dev/null || echo '?') ($(git -C "$ROOT_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?'))"
echo "  sandbox: $W"

# ── 1. первая установка ────────────────────────────────────────────────────
hdr "1. установка с GOVLESS_BRANCH=main"
boot main "$GOOD_URL"
[ "$RC" = "0" ] && ok "код возврата 0" || bad "код возврата $RC"
[ "$(head_branch)" = "main" ] && ok "HEAD на main" || bad "HEAD на $(head_branch), ожидалась main"
says "RAN version=1.3.6" && ok "запущен govless.sh из main (1.3.6)" || bad "не запущен код main"

# ── 2. переезд main → dev на клоне, каким его делал СТАРЫЙ загрузчик ───────
# Каждый уже установленный сервер имеет именно такой клон — с одной веткой в
# refspec. Если бы починка была только в момент клонирования, эти серверы так и
# остались бы залипшими.
hdr "2. main → dev (клон со старым, однобранчевым refspec)"
git -C "$INSTALL" config --replace-all remote.origin.fetch '+refs/heads/main:refs/remotes/origin/main'
git -C "$INSTALL" update-ref -d refs/remotes/origin/dev 2>/dev/null || true
info "refspec до: $(git -C "$INSTALL" config --get-all remote.origin.fetch | tr '\n' ' ')"
boot dev "$GOOD_URL"
[ "$RC" = "0" ] && ok "код возврата 0" || bad "код возврата $RC"
[ "$(head_branch)" = "dev" ] && ok "HEAD на dev" || bad "HEAD на $(head_branch) — ЭТО ИСХОДНЫЙ БАГ"
says "RAN version=1.4.0" && ok "запущен код dev (1.4.0)" || bad "запущен НЕ код dev"
says "Updated to latest dev" && ok "«Updated to latest dev» — и это правда" || bad "нет строки Updated"

# ── 3. обратно на main ─────────────────────────────────────────────────────
hdr "3. dev → main"
boot main "$GOOD_URL"
[ "$(head_branch)" = "main" ] && ok "HEAD снова на main" || bad "HEAD на $(head_branch)"
says "RAN version=1.3.6" && ok "запущен код main (1.3.6)" || bad "запущен НЕ код main"

# ── 4. репозиторий недоступен, просят ДРУГУЮ ветку → отказ ────────────────
hdr "4. репозиторий недоступен + запрошена другая ветка"
git -C "$INSTALL" remote set-url origin "$DEAD_URL"
boot dev "$DEAD_URL"
[ "$RC" != "0" ] && ok "ненулевой код возврата ($RC)" || bad "код 0 — старый код молча запускал main под именем dev"
says "Updated" && bad "напечатано «Updated» при неудаче" || ok "«Updated» не печатается"
says "RAN version=" && bad "govless.sh всё-таки запущен — не та ветка" || ok "govless.sh не запущен"
[ "$(head_branch)" = "main" ] && ok "клон не тронут (HEAD на main)" || bad "HEAD изменился: $(head_branch)"

# ── 5. репозиторий недоступен, просят ТЕКУЩУЮ ветку → работаем, но честно ──
hdr "5. репозиторий недоступен + запрошена текущая ветка"
boot main "$DEAD_URL"
[ "$RC" = "0" ] && ok "код возврата 0 — работа продолжается" || bad "код возврата $RC"
says "NO update was applied" && ok "честно сказано, что обновления не было" || bad "нет предупреждения об отсутствии обновления"
says "RAN version=1.3.6" && ok "запущена локальная копия main" || bad "локальная копия не запущена"

# ── 6. несуществующая ветка → внятный отказ ────────────────────────────────
hdr "6. запрошена несуществующая ветка"
git -C "$INSTALL" remote set-url origin "$GOOD_URL"
boot no-such-branch "$GOOD_URL"
[ "$RC" != "0" ] && ok "ненулевой код возврата ($RC)" || bad "код 0 на несуществующей ветке"
says "does not exist" && ok "сказано, что ветки нет" || bad "непонятное сообщение: $(printf '%s' "$OUT" | tail -3 | tr '\n' ' ')"
[ "$(head_branch)" = "main" ] && ok "клон не тронут" || bad "HEAD изменился: $(head_branch)"

# ── 7. refspec, как его проверяет goInstaller ──────────────────────────────
hdr "7. refspec клона"
rs="$(git -C "$INSTALL" config --get-all remote.origin.fetch)"
info "refspec: $rs"
[ "$(printf '%s\n' "$rs" | grep -c .)" = "1" ] && ok "ровно одна строка" || bad "строк не одна"
printf '%s' "$rs" | grep -qF '+refs/heads/*:refs/remotes/origin/*' \
    && ok "покрывает все ветки" || bad "refspec всё ещё однобранчевый"

# ── 8. контракт с goInstaller ──────────────────────────────────────────────
# goInstaller читает ветку по HEAD клона, а версию — по строке GOVLESS_VERSION
# в НАЧАЛЕ строки lib/common.sh, и сверяет их с тем, что выбрал оператор. Оба
# признака должны говорить правду после каждого переезда.
hdr "8. HEAD и GOVLESS_VERSION после переезда (как их читает goInstaller)"
for b in dev main; do
    exp=1.3.6; [ "$b" = "dev" ] && exp=1.4.0
    boot "$b" "$GOOD_URL"
    v="$(grep -oE '^GOVLESS_VERSION="[^"]+"' "$INSTALL/lib/common.sh" 2>/dev/null | head -1 | cut -d'"' -f2)"
    if [ "$(head_branch)" = "$b" ] && [ "$v" = "$exp" ]; then
        ok "$b: HEAD=$b, GOVLESS_VERSION=$v"
    else
        bad "$b: HEAD=$(head_branch), GOVLESS_VERSION=${v:-не прочитан} — ожидалось $b / $exp"
    fi
done

# ── 9. на месте клона — чужой репозиторий: goVLESS Lite из инструкции ───────
# Бесплатный Lite клонирует potap1978/go-all-lite-version в этот же каталог.
# Загрузчик «обновлял» такой клон из репозитория Lite, печатал «Updated to
# latest main» и запускал Lite: goInstaller ставил pro, а открывалось меню Lite.
mkdir -p "$W/lite/lib"
printf 'GOVLESS_VERSION="1.3.6"\n' > "$W/lite/lib/common.sh"
printf '#!/bin/bash\necho "RAN LITE"\n' > "$W/lite/govless.sh"
g init -q "$W/lite" && g -C "$W/lite" add -A && g -C "$W/lite" commit -qm lite
g clone -q --bare "$W/lite" "$W/govless-lite-version.git"
LITE_URL="file://$W/govless-lite-version.git"
lite_clone(){ rm -rf "$INSTALL"; g clone -q --depth 1 "$LITE_URL" "$INSTALL"; }
origin_now(){ git -C "$INSTALL" config --get remote.origin.url 2>/dev/null || echo '?'; }
no_leftovers(){ ! ls -d "$INSTALL".new.* >/dev/null 2>&1; }

hdr "9. клон чужого репозитория (Lite) заменяется клоном pro"
lite_clone
boot main "$GOOD_URL"
[ "$RC" = "0" ] && ok "код возврата 0" || bad "код возврата $RC"
says "RAN LITE" && bad "запущен Lite — ЭТО ИСХОДНЫЙ БАГ" || ok "Lite не запущен"
says "RAN version=1.3.6" && ok "запущен govless.sh из pro (main)" || bad "pro не запущен"
[ "$(origin_now)" = "$GOOD_URL" ] && ok "origin клона — репозиторий pro" || bad "origin клона: $(origin_now)"
[ "$(head_branch)" = "main" ] && ok "HEAD на main" || bad "HEAD на $(head_branch)"
says "another repository" && ok "сказано, что чужой клон заменён" || bad "замена прошла молча"
says "Updated to latest" && bad "напечатано «Updated» — обновления не было, была замена" || ok "«Updated» не печатается"
no_leftovers && ok "временных каталогов не осталось" || bad "остался временный каталог клона"

# ── 10. чужой клон, а pro недоступен → клон цел, ничего не запущено ─────────
# Без ключа или без сети сервер не должен остаться без установщика, который у
# него был: на этот каталог смотрит команда govless.
hdr "10. чужой клон + репозиторий pro недоступен"
lite_clone
boot main "$DEAD_URL"
[ "$RC" != "0" ] && ok "ненулевой код возврата ($RC)" || bad "код 0 без репозитория pro"
says "RAN" && bad "что-то запущено: $(printf '%s' "$OUT" | grep RAN)" || ok "ничего не запущено"
[ "$(origin_now)" = "$LITE_URL" ] && [ -f "$INSTALL/govless.sh" ] \
    && ok "клон Lite цел — команда govless не сломана" || bad "клон Lite потерян"
says "left as it was" && ok "сказано, что каталог не тронут" || bad "не сказано, что каталог не тронут"
no_leftovers && ok "временных каталогов не осталось" || bad "остался временный каталог клона"

# ── 11. свой репозиторий другим написанием чужим не считается ──────────────
hdr "11. свой клон с иначе записанным адресом — обычное обновление"
rm -rf "$INSTALL"; boot main "$GOOD_URL"
git -C "$INSTALL" remote set-url origin "$GOOD_URL/"
boot main "$GOOD_URL"
says "another repository" && bad "свой клон принят за чужой" || ok "свой клон не заменяется"
says "Updated to latest main" && ok "обычное обновление" || bad "нет обычного обновления"
eval "$(sed -n '/^repo_id()/,/^}/p' "$ROOT_DIR/bootstrap.sh")"
want="$(repo_id https://github.com/potap1978/go-all.git)"
for u in https://github.com/potap1978/go-all https://github.com/potap1978/go-all.git/ \
         https://x-access-token:t0k@github.com/potap1978/go-all.git git@github.com:potap1978/go-all.git; do
    [ "$(repo_id "$u")" = "$want" ] && ok "свой: ${u/t0k/***}" || bad "не узнан свой: $u → $(repo_id "$u")"
done
[ "$(repo_id https://github.com/potap1978/go-all-lite-version.git)" != "$want" ] \
    && ok "potap1978/go-all-lite-version — чужой" || bad "репозиторий Lite принят за свой"

echo ""
if [ "$FAILED" = "0" ]; then
    printf "  ${G}ВСЁ ЗЕЛЕНО${N} — переезд между ветками работает в обе стороны, ложных «Updated» нет, чужой клон (Lite) заменяется\n\n"
    exit 0
fi
printf "  ${R}ЕСТЬ ПРОБЛЕМЫ${N} — см. FAIL выше\n\n"
exit 1
