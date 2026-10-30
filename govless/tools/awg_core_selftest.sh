#!/usr/bin/env bash
# awg_core_selftest.sh — проверить, что ядро AmneziaWG (amneziawg-go + awg)
# готовится БЕЗ обращения в сеть, из локальной копии goVLESS.
#
# Зачем: egress_ensure_awg ходил за бинарниками на raw.githubusercontent в
# ПРИВАТНЫЙ репозиторий potap1978/go-all. Без GH_TOKEN в окружении это 404, а в
# окружении его нет ни у кого, кто открыл меню ярлыком govless — токен живёт
# только внутри запуска bootstrap.sh. Пользователи видели «Не удалось загрузить
# ядро AmneziaWG. Задайте GitHub PAT (меню 8 → 6)» — при том что такого пункта
# меню не существует, а оба бинарника всё это время лежали в bin/ рядом.
#
# Usage:  bash tools/awg_core_selftest.sh
# Нужно:  НЕ root — всё в отдельном временном каталоге, живая установка цела.
# Работает и на живом сервере: §4 ограничивает список мест через AWG_CHECKOUT_ROOTS,
# иначе ядро нашлось бы в /opt/govless-installer и «нет нигде» было бы не сымитировать.
set -u

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

G='\033[0;32m'; R='\033[0;31m'; Y='\033[1;33m'; D='\033[2m'; N='\033[0m'
FAILED=0
ok(){   printf "  ${G}PASS${N} %s\n" "$1"; }
bad(){  printf "  ${R}FAIL${N} %s\n" "$1"; FAILED=1; }
info(){ printf "  ${D}%s${N}\n" "$1"; }
hdr(){  printf "\n  ${Y}── %s${N}\n" "$1"; }

case "$(uname -m)" in x86_64|amd64) ARCH=amd64;; aarch64|arm64) ARCH=arm64;; *) ARCH=amd64;; esac
# Не Linux (macOS разработчика): бинарники здесь не запустить, а arm64-сборки в
# bin/ нет — проверяем amd64-файлы и логику выбора, запуск бинарников пропускаем.
NO_EXEC=0
case "$(uname -s)" in Linux) ;; *) ARCH=amd64; NO_EXEC=1;; esac

echo ""
echo "  AmneziaWG core self-test"
echo "  commit: $(git -C "$ROOT_DIR" rev-parse --short HEAD 2>/dev/null || echo '?')   arch: $ARCH"

# ── 1. бинарники вообще есть в репозитории ─────────────────────────────────
hdr "1. ядро в репозитории"
for f in "bin/amneziawg-go-linux-$ARCH" "bin/awg-linux-$ARCH"; do
    [ -s "$ROOT_DIR/$f" ] && ok "$f на месте ($(wc -c < "$ROOT_DIR/$f" | tr -d " ") байт)" \
                          || bad "$f отсутствует — ядро неоткуда взять локально"
done
[ "$FAILED" = "0" ] || { echo ""; exit 1; }

# ── 2. подготовка ядра без сети ────────────────────────────────────────────
# AWG_REPO намеренно указывает в несуществующий репозиторий: если ядро всё
# равно подготовилось — значит оно пришло с диска, а не по сети.
hdr "2. подготовка ядра при заведомо нерабочей сети"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
out="$(
  EGRESS_DIR="$TMP/egress" AWG_REPO="potap1978/no-such-repo-$$" GH_TOKEN="" AWG_ARCH="$ARCH" \
  SCRIPT_DIR="$ROOT_DIR" bash -c '
    source "$1/lib/common.sh" >/dev/null 2>&1
    source "$1/lib/i18n.sh"; load_language ru
    source "$1/lib/egress.sh"
    egress_ensure_awg
  ' _ "$ROOT_DIR" 2>&1
)"; rc=$?
printf '%s\n' "$out" | sed 's/^/      /'
[ "$rc" = "0" ] && ok "egress_ensure_awg вернул 0" || bad "egress_ensure_awg вернул $rc"
printf '%s' "$out" | grep -qF 'из локальной копии' \
    && ok "ядро взято с диска, а не по сети" \
    || bad "ядро НЕ взято из локальной копии — снова пойдёт в сеть"

# ── 3. полученные бинарники рабочие ────────────────────────────────────────
hdr "3. бинарники запускаются"
GO="$TMP/egress/awg/amneziawg-go"; TOOL="$TMP/egress/awg/awg"
for b in "$GO" "$TOOL"; do
    [ -x "$b" ] && ok "$(basename "$b") на месте и исполняемый" \
                || bad "$(basename "$b") отсутствует"
done
if [ "$NO_EXEC" = "1" ]; then
    info "не Linux — запуск бинарников пропущен (проверяется на сервере)"
elif [ -x "$GO" ]; then
    v="$("$GO" --version 2>&1 | head -1)"
    case "$v" in *amneziawg-go*) ok "amneziawg-go: $v" ;; *) bad "amneziawg-go не запускается: $v" ;; esac
fi
if [ "$NO_EXEC" = "0" ] && [ -x "$TOOL" ]; then
    v="$("$TOOL" --version 2>&1 | head -1)"
    case "$v" in *amneziawg-tools*) ok "awg: $v" ;; *) bad "awg не запускается: $v" ;; esac
fi

# ── 4. сообщение об ошибке не врёт ─────────────────────────────────────────
# Раньше оно звало «задать GitHub PAT (меню 8 → 6)» — такого пункта нет ни в
# одной ветке, и ни одна строка кода не читает сохранённый PAT.
hdr "4. сообщение об ошибке, когда ядра нет нигде"
# Строки комментариев отбрасываем: комментарий, объясняющий починку, сам
# называет старый текст, и наивный grep поймал бы собственное объяснение.
if grep -rh --include='*.sh' -v '^[[:space:]]*#' lib/ | grep -qF 'меню 8 → 6'; then
    bad "в lib/ осталась отсылка к несуществующему «меню 8 → 6»"
else
    ok "несуществующего «меню 8 → 6» в подсказках нет"
fi
EMPTY="$TMP/empty"; mkdir -p "$EMPTY"
out="$(
  EGRESS_DIR="$TMP/egress2" AWG_REPO="potap1978/no-such-repo-$$" GH_TOKEN="" AWG_ARCH="$ARCH" \
  HOME="$EMPTY" SCRIPT_DIR="$EMPTY" AWG_CHECKOUT_ROOTS="$EMPTY" bash -c '
    source "$1/lib/common.sh" >/dev/null 2>&1
    source "$1/lib/i18n.sh"; load_language ru
    source "$1/lib/egress.sh"
    egress_ensure_awg
  ' _ "$ROOT_DIR" 2>&1
)"; rc=$?
[ "$rc" != "0" ] && ok "без ядра возвращает ненулевой код ($rc)" || bad "без ядра вернул 0"
printf '%s' "$out" | grep -qF 'bin/amneziawg-go-linux' \
    && ok "сообщение называет конкретные ожидаемые файлы" \
    || bad "сообщение не объясняет, чего не хватает"
info "$(printf '%s' "$out" | tail -4 | tr '\n' ' ' | sed 's/  */ /g' | cut -c1-160)"

echo ""
if [ "$FAILED" = "0" ]; then
    printf "  ${G}ВСЁ ЗЕЛЕНО${N} — ядро AmneziaWG готовится с диска, без сети и без токена\n\n"
    exit 0
fi
printf "  ${R}ЕСТЬ ПРОБЛЕМЫ${N} — см. FAIL выше\n\n"
exit 1
