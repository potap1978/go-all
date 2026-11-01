#!/bin/bash
# goDNS — Copyright (c) 2026 anten-ka. All rights reserved.
#
# goDNS — резервные копии состояния: создание, список, восстановление,
# уборка и расписание.
#
# ── Что считается состоянием ────────────────────────────────────────────
#   /opt/godns/config.json     — ВСЁ: домен, пути сертификата, порты,
#                                СЕКРЕТНЫЙ путь DoH, политика, апстримы,
#                                ключ консоли dnsdist, отпечаток applied
#   /opt/godns/.language       — выбранный язык
#   /etc/godns/                — каталог состояния (может быть пуст)
#   /etc/dnsdist/dnsdist.conf  — СГЕНЕРИРОВАННЫЙ конфиг движка
#
# Сертификаты в архив НЕ кладутся. Они принадлежат goVLESS или goTelegram,
# goDNS их только читает — а копия чужого приватного ключа в нашем архиве
# была бы вторым местом, где он лежит, и первым, про которое забудут.
#
# ── Почему конфиг движка не восстанавливается, а перегенерируется ───────
# dnsdist.conf — это Lua, который исполняется службой. Архив может приехать
# с чужого сервера («вот мой бекап, разверни»), а разворачиваем мы его от
# root. Поэтому dnsdist.conf кладётся в архив ТОЛЬКО для диагностики, а при
# восстановлении рождается заново из config.json через engine_apply. Тот же
# урок, что в goTelegram: код и исполняемое приезжают из дистрибутива,
# бекап — канал данных, а не кода.
#
# ── Права ───────────────────────────────────────────────────────────────
# Внутри архива — секретный путь DoH и ключ консоли dnsdist. Каталог 0700,
# файлы 0600, владелец root. Это ровно та же защита, что у оригиналов
# (/opt/godns/config.json — 0600 root). Шифрование добавляет ровно одно:
# защиту архива ПОСЛЕ того, как он уехал с сервера (scp, облако, почта).
# Поэтому оно есть, но по умолчанию выключено — см. godns_backup_create.
#
# Требует: lib/common.sh (и, желательно, lib/i18n.sh) до себя.
# Ассоциативных массивов здесь нет намеренно: файл обязан работать на
# bash 3.2, чтобы test_backup.sh шёл на macOS без контейнера.

# ── i18n: мягкая подстраховка (как в lib/engine.sh) ─────────────────────
if ! declare -F t >/dev/null 2>&1; then
    t()  { printf '%s' "$1"; }
    tf() {
        local _k="$1"; shift
        printf '%s' "$_k"
        if [ "$#" -gt 0 ]; then printf ' [%s]' "$*"; fi
        return 0
    }
fi

# ── Пути и константы ────────────────────────────────────────────────────
GODNS_BACKUP_DIR="${GODNS_BACKUP_DIR:-/root/godns-backups}"
# Держим 10, а не 30 как у goTelegram: там в архиве живая статистика и
# ключи пользователей, у нас — один config.json, который меняется, только
# когда оператор что-то настроил.
GODNS_BACKUP_KEEP="${GODNS_BACKUP_KEEP:-10}"
# Имя каталога ВНУТРИ архива. Фиксированное намеренно: только так работает
# `tar -xzOf архив godns-backup/manifest.json` — чтение манифеста без
# распаковки всего архива.
GODNS_BACKUP_MEMBER="godns-backup"
GODNS_BACKUP_FORMAT="1"
GODNS_BACKUP_TIMER="godns-backup"

# ═══════════════════════════════════════════════════════════════════════
# МЕЛОЧИ
# ═══════════════════════════════════════════════════════════════════════

# Конфиг движка. Определён в lib/engines/dnsdist.sh, но backup.sh должен
# работать и когда движок не загружен (install.sh при обновлении).
_bk_engine_conf() { printf '%s' "${DNSDIST_CONF:-/etc/dnsdist/dnsdist.conf}"; }

# sha256sum есть на Ubuntu, но не на macOS; shasum — наоборот. openssl —
# везде. Тот же приём, что в godns_config_fingerprint.
_bk_sha256() {
    local f="${1:-}" out=""
    [ -f "$f" ] || return 1
    if command -v sha256sum >/dev/null 2>&1; then
        out=$(sha256sum "$f" 2>/dev/null) || out=""
        printf '%s' "${out%% *}"
        return 0
    fi
    if command -v shasum >/dev/null 2>&1; then
        out=$(shasum -a 256 "$f" 2>/dev/null) || out=""
        printf '%s' "${out%% *}"
        return 0
    fi
    out=$(openssl dgst -sha256 "$f" 2>/dev/null) || out=""
    printf '%s' "${out##*= }"
    return 0
}

# `date -Iseconds` есть не всюду в одинаковом виде — формат задаём явно.
_bk_iso()   { date +%Y-%m-%dT%H:%M:%S%z 2>/dev/null || printf '?'; }
_bk_stamp() { date +%Y%m%d-%H%M%S 2>/dev/null || printf '00000000-000000'; }

# Человекочитаемый размер файла.
_bk_size() {
    local f="${1:-}" s=""
    [ -f "$f" ] || { printf '?'; return 0; }
    s=$(du -h "$f" 2>/dev/null | cut -f1) || s=""
    s="${s%%$'\n'*}"
    printf '%s' "${s:-?}"
    return 0
}

# Метка в имени файла: только то, что не превратит имя в сюрприз.
#
# `tr -s` обязателен. Без него метка «первая проверка» (кириллица — по два
# байта на букву) превращалась в godns-0.1.0-…-------------------------,
# потому что каждый негодный БАЙТ становился отдельным дефисом. Метку
# целиком хранит манифест; в имени файла она лишь подсказка глазу.
_bk_slug() {
    local s="${1:-}"
    s=$(printf '%s' "$s" | LC_ALL=C tr -c 'A-Za-z0-9._-' '-' 2>/dev/null \
        | LC_ALL=C tr -s '-' 2>/dev/null) || s=""
    s="${s%%$'\n'*}"
    s=$(printf '%s' "$s" | cut -c1-24) || s=""
    # Схлопываем подряд идущие точки и обрезаем разделители по краям, пока
    # что-то обрезается. Без этого «../../etc/evil» превращалось в
    # «..-..-etc-evil»: траверсала тут нет (слэши уже заменены), но точки
    # в имени архива пугают того, кто выбирает, что разворачивать.
    s=$(printf '%s' "$s" | LC_ALL=C tr -s '.' 2>/dev/null) || s=""
    while : ; do
        case "$s" in
            [-.]*) s="${s#?}" ;;
            *[-.]) s="${s%?}" ;;
            *) break ;;
        esac
    done
    # Метка без единой буквы или цифры («..», «-», «.-.») даёт имя файла,
    # по которому потом ничего не выбрать, а «..» в имени ещё и пугает.
    # Такую метку считаем отсутствующей.
    case "$s" in
        *[A-Za-z0-9]*) : ;;
        *) s="" ;;
    esac
    printf '%s' "$s"
    return 0
}

# Каталог для копий: существует, 0700, наш.
_bk_ensure_dir() {
    local d="${1:-$GODNS_BACKUP_DIR}"
    mkdir -p "$d" 2>/dev/null || { log_error "$(tf bk_dir_failed "$d")"; return 1; }
    chmod 0700 "$d" 2>/dev/null || true
    return 0
}

# Можно ли писать в каталог состояния. Заменяет require_root: пути
# переопределяемы (тесты), и «нет прав» надо проверять по факту, а не по uid.
_bk_state_writable() {
    local d
    d=$(dirname "$GODNS_CONFIG")
    mkdir -p "$d" 2>/dev/null || true
    if [ -w "$d" ]; then return 0; fi
    return 1
}

# ── Сравнение версий ────────────────────────────────────────────────────
# godns_version_lt A B → rc0, если A СТРОГО меньше B.
# Приём взят из goTelegram (install.sh:864): sort -V делает всю работу и
# не путается на 0.9.0 против 0.10.0. Запасной разбор нужен ровно на
# случай урезанного sort — без него «не смог сравнить» молча означало бы
# «не старее», то есть тихо пропущенную миграцию.
godns_version_lt() {
    local a="${1:-0}" b="${2:-0}" first=""
    [ "$a" = "$b" ] && return 1
    first=$(printf '%s\n%s\n' "$a" "$b" | sort -V 2>/dev/null) || first=""
    first="${first%%$'\n'*}"
    if [ -n "$first" ]; then
        if [ "$first" = "$a" ]; then return 0; fi
        return 1
    fi
    _bk_ver_lt_fallback "$a" "$b"
}

# Почленное числовое сравнение. Нечисловой хвост (rc1, beta) отбрасываем:
# для гейтов миграции важен только номер выпуска.
_bk_ver_lt_fallback() {
    local a="${1:-0}" b="${2:-0}" i=0 pa pb na nb
    local IFS_SAVE="$IFS"
    IFS='.'
    # shellcheck disable=SC2206
    pa=($a)
    # shellcheck disable=SC2206
    pb=($b)
    IFS="$IFS_SAVE"
    while [ "$i" -lt 4 ]; do
        na="${pa[$i]:-0}"; nb="${pb[$i]:-0}"
        na="${na%%[!0-9]*}"; nb="${nb%%[!0-9]*}"
        [ -n "$na" ] || na=0
        [ -n "$nb" ] || nb=0
        if [ "$na" -lt "$nb" ]; then return 0; fi
        if [ "$na" -gt "$nb" ]; then return 1; fi
        i=$((i + 1))
    done
    return 1
}

# ── Манифест ────────────────────────────────────────────────────────────
# JSON пишем python3, не jq: jq нет на Ubuntu 22.04 standard и на всех
# minimal-образах. Секретов в манифесте НЕТ намеренно — ни пути DoH, ни
# ключа консоли: манифест читают, чтобы решить, разворачивать ли архив.
_bk_manifest_write() {   # <файл> <ключ> <значение> …
    local out="$1"; shift
    python3 - "$out" "$@" <<'PY' || return 1
import json, os, sys, tempfile
out = sys.argv[1]
pairs = sys.argv[2:]
data = {}
for i in range(0, len(pairs) - 1, 2):
    data[pairs[i]] = pairs[i + 1]
d = os.path.dirname(out) or "."
fd, tmp = tempfile.mkstemp(dir=d)
with os.fdopen(fd, "w", encoding="utf-8") as fh:
    json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
os.chmod(tmp, 0o600)
os.replace(tmp, out)
PY
    return 0
}

# Одно поле манифеста ПРЯМО ИЗ АРХИВА, без полной распаковки.
godns_backup_manifest_field() {   # <архив> <ключ> [умолчание]
    local arc="${1:-}" key="${2:-}" def="${3:-}" tmp="" val=""
    case "$arc" in
        *.enc) printf '%s' "$def"; return 0 ;;   # зашифрован — читать нечем
    esac
    [ -f "$arc" ] || { printf '%s' "$def"; return 0; }
    tmp=$(godns_mktemp) || { printf '%s' "$def"; return 0; }
    tar -xzOf "$arc" "${GODNS_BACKUP_MEMBER}/manifest.json" > "$tmp" 2>/dev/null || true
    if [ ! -s "$tmp" ]; then
        rm -f "$tmp" 2>/dev/null || true
        printf '%s' "$def"
        return 0
    fi
    val=$(json_get "$tmp" "$key" "$def") || val="$def"
    rm -f "$tmp" 2>/dev/null || true
    printf '%s' "$val"
    return 0
}

# ── Опись состояния ─────────────────────────────────────────────────────
# Единственный источник правды о том, что такое «состояние goDNS».
# Печатает построчно:  <путь на диске> TAB <путь внутри архива>
# Только то, что реально существует.
#
# Из /opt/godns берём ДВА НАЗВАННЫХ файла, а не каталог целиком. Там же
# живёт бухгалтерия, которая настройками не является и восстановлению
# только вредит: `logrotate.state` — отметка «этот журнал уже провернули»,
# и старая отметка из архива заставит logrotate решить, что ротация была.
# Новый файл в /opt/godns — повод осознанно решить, состояние он или нет,
# а не попасть в архив по умолчанию. /etc/godns, наоборот, целиком наш.
godns_backup_sources() {
    local conf; conf=$(_bk_engine_conf)
    [ -f "$GODNS_CONFIG" ]            && printf '%s\t%s\n' "$GODNS_CONFIG" "state/config.json"
    [ -f "${GODNS_DIR}/.language" ]   && printf '%s\t%s\n' "${GODNS_DIR}/.language" "state/language"
    [ -d "$GODNS_ETC" ]               && printf '%s\t%s\n' "$GODNS_ETC" "state/etc-godns"
    [ -f "$conf" ]                    && printf '%s\t%s\n' "$conf" "engine/dnsdist.conf"
    [ -n "${LE_HOOK_FILE:-}" ] && [ -f "${LE_HOOK_FILE:-}" ] \
        && printf '%s\t%s\n' "$LE_HOOK_FILE" "hooks/50-godns.sh"
    return 0
}

# Отпечаток состояния — чтобы таймер не плодил десять одинаковых архивов.
godns_backup_state_hash() {
    local line src dst tmp="" h=""
    tmp=$(godns_mktemp) || { printf ''; return 0; }
    : > "$tmp"
    while IFS=$'\t' read -r src dst; do
        [ -n "$src" ] || continue
        printf '%s\n' "$dst" >> "$tmp"
        if [ -f "$src" ]; then
            cat "$src" >> "$tmp" 2>/dev/null || true
        elif [ -d "$src" ]; then
            find "$src" -type f 2>/dev/null | sort | while IFS= read -r f; do
                printf '%s\n' "$f"
                cat "$f" 2>/dev/null || true
            done >> "$tmp"
        fi
    done <<EOF
$(godns_backup_sources)
EOF
    h=$(_bk_sha256 "$tmp") || h=""
    rm -f "$tmp" 2>/dev/null || true
    printf '%s' "$h"
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
# СОЗДАНИЕ
# ═══════════════════════════════════════════════════════════════════════
# godns_backup_create [метка] [пароль] [only-if-changed]
#
# stdout — ПУТЬ к созданному архиву (и только он). Всё остальное в stderr.
#
# Тело — подоболочка "( … )": umask 077 действует внутри и не протекает
# наружу. Приём из goTelegram; там он спас .env с токеном бота, здесь —
# секретный путь DoH и ключ консоли dnsdist.
godns_backup_create() (
    umask 077

    local label="${1:-}" password="${2:-}" only_changed="${3:-}"
    local out_dir="$GODNS_BACKUP_DIR"

    _bk_ensure_dir "$out_dir" || return 1

    local n=0
    n=$(godns_backup_sources | grep -c . 2>/dev/null) || n=0
    n="${n//[^0-9]/}"; [ -n "$n" ] || n=0
    if [ "$n" -eq 0 ]; then
        log_warning "$(t bk_nothing_to_save)"
        return 1
    fi

    local state_hash=""
    state_hash=$(godns_backup_state_hash) || state_hash=""

    # Таймер зовёт с only-if-changed: конфиг меняется, только когда оператор
    # что-то настроил, а глубина хранения конечна — десять одинаковых копий
    # вытеснили бы единственную полезную.
    if [ "$only_changed" = "only-if-changed" ] && [ -n "$state_hash" ]; then
        local newest="" prev=""
        newest=$(godns_backup_files | head -n1) || newest=""
        if [ -n "$newest" ]; then
            prev=$(godns_backup_manifest_field "$newest" state_sha256 "") || prev=""
            if [ "$prev" = "$state_hash" ]; then
                log_dim "$(t bk_unchanged_skip)"
                printf '%s' "$newest"
                return 0
            fi
        fi
    fi

    # Приватная песочница со случайным именем: фиксированный /tmp/<имя>
    # позволял бы локальному пользователю заранее подложить симлинк и
    # подставить root'у чужой путь.
    local stage=""
    stage=$(mktemp -d "${TMPDIR:-/tmp}/godns-backup.XXXXXXXX") || {
        log_error "$(t bk_tmp_failed)"; return 1; }
    chmod 0700 "$stage" 2>/dev/null || true

    local root="${stage}/${GODNS_BACKUP_MEMBER}"
    mkdir -p "$root" || { rm -rf "$stage"; log_error "$(t bk_tmp_failed)"; return 1; }

    # ── собираем ────────────────────────────────────────────────────────
    log_info "$(t bk_collecting)"
    local line src dst files=""
    while IFS=$'\t' read -r src dst; do
        [ -n "$src" ] || continue
        mkdir -p "${root}/$(dirname "$dst")" 2>/dev/null || true
        if [ -d "$src" ]; then
            mkdir -p "${root}/${dst}" 2>/dev/null || true
            cp -a "$src/." "${root}/${dst}/" 2>/dev/null || true
        else
            cp -a "$src" "${root}/${dst}" 2>/dev/null || true
        fi
        files="${files}${dst} "
    done <<EOF
$(godns_backup_sources)
EOF

    # ── манифест ────────────────────────────────────────────────────────
    local eng_ver="" os_pretty="" host=""
    # Без `command -v` строка «bash: dnsdist: command not found» уезжала бы
    # в манифест как версия движка: 2>&1 ловит и сообщение шелла тоже.
    if command -v dnsdist >/dev/null 2>&1; then
        eng_ver=$(dnsdist --version 2>&1 | head -n1) || eng_ver=""
        eng_ver="${eng_ver%%$'\n'*}"
    fi
    os_pretty="${OS_PRETTY:-}"
    if [ -z "$os_pretty" ] && declare -F os_release_get >/dev/null 2>&1; then
        os_pretty=$(os_release_get PRETTY_NAME) || os_pretty=""
    fi
    host=$(hostname 2>/dev/null) || host=""

    # ВНИМАНИЕ: doh_path и console_key в манифест НЕ кладём. Манифест —
    # то, что читают, чтобы РЕШИТЬ, разворачивать ли архив; секретам там
    # делать нечего, они и так внутри, в config.json.
    #
    # Пары складываем в массив, а не пишем в столбик через `\`: имена полей
    # `godns_version` и `engine_version` в начале строки неотличимы от
    # ВЫЗОВА функции с таким именем, и tools/check-callgraph.py справедливо
    # ругался на несуществующие функции. `mf+=(…)` снимает двусмысленность.
    local -a mf=()
    mf+=(backup_format       "$GODNS_BACKUP_FORMAT")
    mf+=(godns_version       "${GODNS_VERSION:-?}")
    mf+=(schema              "$(config_get schema "${GODNS_SCHEMA:-1}")")
    mf+=(created             "$(_bk_iso)")
    mf+=(label               "${label:-manual}")
    mf+=(hostname            "${host:-?}")
    mf+=(os                  "${os_pretty:-?}")
    mf+=(engine              "$(config_get engine dnsdist)")
    mf+=(engine_version      "${eng_ver:-?}")
    mf+=(domain              "$(config_get domain "")")
    mf+=(doh_port            "$(config_get doh_port "")")
    mf+=(dot_port            "$(config_get dot_port "")")
    mf+=(policy              "$(config_get policy "")")
    # В манифест — только ИДЕНТИФИКАТОРЫ, не записи движка. Запись несёт
    # адрес, имя и путь DoH; у своего апстрима путь может быть чужим
    # секретом (второй goDNS как апстрим). Манифест читают, чтобы РЕШИТЬ,
    # разворачивать ли архив, — секретам там не место; сами записи и так
    # внутри, в config.json.
    mf+=(upstreams           "$(config_get upstream_ids "")")
    mf+=(applied_fingerprint "$(config_get applied_fingerprint "")")
    mf+=(state_sha256        "${state_hash:-?}")
    mf+=(files               "$files")
    if ! _bk_manifest_write "${root}/manifest.json" "${mf[@]}"; then
        rm -rf "$stage"
        log_error "$(t bk_manifest_failed)"
        return 1
    fi

    # ── имя ─────────────────────────────────────────────────────────────
    local slug="" name="" suffix=0
    slug=$(_bk_slug "$label") || slug=""
    while : ; do
        name="godns-${GODNS_VERSION:-0}-$(_bk_stamp)"
        [ -n "$slug" ] && name="${name}-${slug}"
        [ "$suffix" -gt 0 ] && name="${name}-${suffix}"
        if [ ! -e "${out_dir}/${name}.tar.gz" ] && [ ! -e "${out_dir}/${name}.tar.gz.enc" ]; then
            break
        fi
        suffix=$((suffix + 1))
        [ "$suffix" -gt 50 ] && break
    done

    # ── упаковка ────────────────────────────────────────────────────────
    local tar_file="${stage}/${name}.tar.gz"
    if ! tar czf "$tar_file" -C "$stage" "$GODNS_BACKUP_MEMBER" 2>/dev/null; then
        rm -rf "$stage"
        log_error "$(t bk_archive_failed)"
        return 1
    fi
    # `-s`: tar умеет «успешно» создать пустой архив при нехватке места.
    # Без этой проверки уверенность «откат возможен» была бы ложной.
    if [ ! -s "$tar_file" ]; then
        rm -rf "$stage"
        log_error "$(t bk_archive_empty)"
        return 1
    fi

    local final=""
    if [ -n "$password" ]; then
        final="${out_dir}/${name}.tar.gz.enc"
        # Пароль — через ФАЙЛ (0600 благодаря umask 077), не через argv:
        # `-pass pass:…` читается любым локальным пользователем в
        # /proc/<pid>/cmdline всё время шифрования.
        local pass_file="${stage}/.pass"
        printf '%s' "$password" > "$pass_file" || { rm -rf "$stage"; return 1; }
        if ! openssl enc -aes-256-cbc -salt -pbkdf2 \
                -in "$tar_file" -out "$final" -pass "file:${pass_file}" 2>/dev/null; then
            rm -f "$final" 2>/dev/null || true
            rm -rf "$stage"
            log_error "$(t bk_encrypt_failed)"
            return 1
        fi
        # Незашифрованный tar и файл пароля не держим ни секунды дольше нужного.
        rm -f "$tar_file" "$pass_file" 2>/dev/null || true
        log_success "$(t bk_encrypted)"
    else
        final="${out_dir}/${name}.tar.gz"
        # /tmp и каталог копий часто на разных ФС: mv здесь = копирование,
        # и при ENOSPC файл окажется обрезанным. Без сверки суммы «успех»
        # был бы фиктивным.
        local src_sum="" dst_sum=""
        src_sum=$(_bk_sha256 "$tar_file") || src_sum=""
        if ! mv "$tar_file" "$final" 2>/dev/null; then
            rm -f "$final" 2>/dev/null || true
            rm -rf "$stage"
            log_error "$(t bk_archive_failed)"
            return 1
        fi
        dst_sum=$(_bk_sha256 "$final") || dst_sum=""
        if [ -n "$src_sum" ] && [ "$src_sum" != "$dst_sum" ]; then
            rm -f "$final" 2>/dev/null || true
            rm -rf "$stage"
            log_error "$(t bk_archive_truncated)"
            return 1
        fi
    fi

    chmod 0600 "$final" 2>/dev/null || true

    # Контрольная сумма рядом — сверяется ДО распаковки при восстановлении.
    local sum=""
    sum=$(_bk_sha256 "$final") || sum=""
    if [ -n "$sum" ]; then
        printf '%s  %s\n' "$sum" "$(basename "$final")" > "${final}.sha256" 2>/dev/null || true
        chmod 0600 "${final}.sha256" 2>/dev/null || true
    fi

    rm -rf "$stage"

    log_success "$(tf bk_created "$final" "$(_bk_size "$final")")"
    printf '%s\n' "$final"
    return 0
)

# ═══════════════════════════════════════════════════════════════════════
# СПИСОК
# ═══════════════════════════════════════════════════════════════════════
# Пути к архивам, СВЕЖИЕ СНАЧАЛА. Сортируем по mtime, а не по имени:
# в имени первым идёт версия, и лексически godns-0.10.0-… оказалось бы
# «старше» godns-0.9.0-…. find -printf и stat -c — GNU-only, поэтому
# python3, который в проекте и так обязателен.
godns_backup_files() {
    local dir="${1:-$GODNS_BACKUP_DIR}"
    [ -d "$dir" ] || return 0
    python3 - "$dir" <<'PY' 2>/dev/null || return 0
import os, sys
d = sys.argv[1]
items = []
try:
    names = os.listdir(d)
except OSError:
    names = []
for name in names:
    if not (name.endswith(".tar.gz") or name.endswith(".tar.gz.enc")):
        continue
    p = os.path.join(d, name)
    if not os.path.isfile(p):
        continue
    try:
        items.append((os.path.getmtime(p), p))
    except OSError:
        continue
items.sort(key=lambda x: (-x[0], x[1]))
for _, p in items:
    sys.stdout.write(p + "\n")
PY
    return 0
}

godns_backup_list() {
    local dir="$GODNS_BACKUP_DIR"
    local files="" f i=1 ver date_str dom size enc

    files=$(godns_backup_files "$dir") || files=""
    if [ -z "$files" ]; then
        log_info "$(tf bk_none "$dir")"
        return 1
    fi

    print_header "$(t bk_list_title)"
    echo -e "  ${DIM}$(t bk_list_dir): ${dir}${NC}"
    echo ""
    while IFS= read -r f; do
        [ -n "$f" ] || continue
        ver=$(godns_backup_manifest_field "$f" godns_version "?")  || ver="?"
        date_str=$(godns_backup_manifest_field "$f" created "?")   || date_str="?"
        dom=$(godns_backup_manifest_field "$f" domain "")          || dom=""
        size=$(_bk_size "$f") || size="?"
        enc=""
        case "$f" in *.enc) enc=" ${YELLOW}🔒${NC}" ;; esac
        printf '  %b%2d)%b %s%b  %bv%s · %s · %s%b\n' \
            "$CYAN" "$i" "$NC" "$(basename "$f")" "$enc" \
            "$DIM" "$ver" "$date_str" "$size" "$NC"
        [ -n "$dom" ] && echo -e "      ${DIM}${dom}${NC}"
        i=$((i + 1))
    done <<EOF
$files
EOF
    echo ""
    echo -e "  ${DIM}$(tf bk_keep_note "$GODNS_BACKUP_KEEP")${NC}"
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
# УБОРКА
# ═══════════════════════════════════════════════════════════════════════
godns_backup_prune() {
    local keep="${1:-$GODNS_BACKUP_KEEP}"
    case "$keep" in ''|*[!0-9]*) keep="$GODNS_BACKUP_KEEP" ;; esac
    [ "$keep" -ge 1 ] 2>/dev/null || keep=1

    local files="" f n=0 removed=0
    files=$(godns_backup_files) || files=""
    [ -n "$files" ] || { log_info "$(tf bk_none "$GODNS_BACKUP_DIR")"; return 0; }

    while IFS= read -r f; do
        [ -n "$f" ] || continue
        n=$((n + 1))
        [ "$n" -le "$keep" ] && continue
        rm -f "$f" "${f}.sha256" 2>/dev/null || continue
        removed=$((removed + 1))
        log_dim "$(tf bk_pruned_one "$(basename "$f")")"
    done <<EOF
$files
EOF

    if [ "$removed" -eq 0 ]; then
        log_info "$(tf bk_prune_nothing "$n" "$keep")"
        return 0
    fi
    log_success "$(tf bk_pruned "$removed" "$keep")"
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
# ВОССТАНОВЛЕНИЕ
# ═══════════════════════════════════════════════════════════════════════
# godns_backup_restore <файл> [пароль] [yes]
#
# Третий аргумент "yes" снимает подтверждения — им пользуются тест и
# автоматический откат после неудачного обновления. Из меню он не
# передаётся никогда.
godns_backup_restore() {
    local arc="${1:-}" password="${2:-}" assume_yes="${3:-}"

    if [ -z "$arc" ] || [ ! -f "$arc" ]; then
        log_error "$(tf bk_not_found "${arc:-?}")"
        return 1
    fi
    if ! _bk_state_writable; then
        log_error "$(tf bk_need_write "$(dirname "$GODNS_CONFIG")")"
        return 1
    fi

    # ── целостность ─────────────────────────────────────────────────────
    if [ -f "${arc}.sha256" ]; then
        local want got
        want=$(grep -oE '[0-9a-fA-F]{64}' "${arc}.sha256" 2>/dev/null | head -n1) || want=""
        want=$(printf '%s' "$want" | LC_ALL=C tr 'A-F' 'a-f') || want=""
        got=$(_bk_sha256 "$arc") || got=""
        got=$(printf '%s' "$got" | LC_ALL=C tr 'A-F' 'a-f') || got=""
        if [ -n "$want" ] && [ "$want" != "$got" ]; then
            log_error "$(t bk_sha_mismatch)"
            log_dim "$(tf bk_sha_detail "$want" "${got:-—}")"
            return 1
        fi
        [ -n "$want" ] && log_dim "$(t bk_sha_ok)"
    fi

    local work=""
    work=$(mktemp -d "${TMPDIR:-/tmp}/godns-restore.XXXXXXXX") || {
        log_error "$(t bk_tmp_failed)"; return 1; }
    chmod 0700 "$work" 2>/dev/null || true

    # ── расшифровка ─────────────────────────────────────────────────────
    local tar_file="$arc" made_plain="no"
    case "$arc" in
        *.enc)
            if [ -z "$password" ]; then
                echo -ne "  $(t bk_enter_pass): " >&2
                read -rs password || password=""
                echo "" >&2
            fi
            tar_file="${work}/plain.tar.gz"
            local pass_file="${work}/.pass"
            printf '%s' "$password" > "$pass_file" 2>/dev/null || { rm -rf "$work"; return 1; }
            chmod 0600 "$pass_file" 2>/dev/null || true
            if ! openssl enc -aes-256-cbc -d -pbkdf2 \
                    -in "$arc" -out "$tar_file" -pass "file:${pass_file}" 2>/dev/null; then
                rm -f "$pass_file" 2>/dev/null || true
                rm -rf "$work"
                log_error "$(t bk_bad_pass)"
                return 1
            fi
            rm -f "$pass_file" 2>/dev/null || true
            made_plain="yes"
            ;;
    esac

    # ── распаковка ──────────────────────────────────────────────────────
    # --no-same-owner / --no-same-permissions: архив может быть ЧУЖИМ
    # («вот мой бекап, разверни»), а распаковываем мы его от root. Без этих
    # флагов чужой архив внёс бы в систему файлы с чужим владельцем и, при
    # желании автора, с setuid-root.
    mkdir -p "${work}/x" 2>/dev/null || true
    if ! tar xzf "$tar_file" -C "${work}/x" --no-same-owner --no-same-permissions 2>/dev/null; then
        rm -rf "$work"
        log_error "$(t bk_extract_failed)"
        return 1
    fi

    local base="${work}/x/${GODNS_BACKUP_MEMBER}"
    [ -d "$base" ] || base="${work}/x"

    # Совместимость: архив, собранный руками из абсолютных путей.
    local cfg_in="${base}/state/config.json"
    if [ ! -f "$cfg_in" ] && [ -f "${work}/x/opt/godns/config.json" ]; then
        cfg_in="${work}/x/opt/godns/config.json"
    fi
    if [ ! -f "$cfg_in" ]; then
        rm -rf "$work"
        log_error "$(t bk_no_config_inside)"
        return 1
    fi
    # config.json из архива обязан быть валидным JSON-объектом: иначе
    # восстановление положит на место файл, из-за которого не заведётся ни
    # один config_get.
    #
    # ГРАБЛИ: раньше тут стояло `[ -z "$(json_get … domain __none__)" ]`.
    # json_get печатает УМОЛЧАНИЕ при любой ошибке разбора, то есть битый
    # файл давал НЕПУСТУЮ строку и проходил проверку — обрезанный на
    # середине config.json затирал живой с зелёной галкой. А валидный ранний
    # архив с domain="" (копия до шага 1 мастера), наоборот, отвергался как
    # «нечитаемый». Проверка обязана спрашивать про сам JSON, а не про ключ.
    if ! python3 -c 'import json,sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
sys.exit(0 if isinstance(d, dict) else 1)' "$cfg_in" 2>/dev/null; then
        rm -rf "$work"
        log_error "$(t bk_config_unreadable)"
        return 1
    fi

    # ── что внутри ──────────────────────────────────────────────────────
    local m_ver="?" m_date="?" m_dom="" m_doh="" m_dot="" m_host="?"
    if [ -f "${base}/manifest.json" ]; then
        m_ver=$(json_get "${base}/manifest.json" godns_version "?")  || m_ver="?"
        m_date=$(json_get "${base}/manifest.json" created "?")       || m_date="?"
        m_dom=$(json_get "${base}/manifest.json" domain "")          || m_dom=""
        m_doh=$(json_get "${base}/manifest.json" doh_port "")        || m_doh=""
        m_dot=$(json_get "${base}/manifest.json" dot_port "")        || m_dot=""
        m_host=$(json_get "${base}/manifest.json" hostname "?")      || m_host="?"
    fi

    print_header "$(t bk_restore_title)"
    # Отступы здесь были забиты пробелами под русские подписи: по-русски
    # колонка гуляла на три знака, по-английски — на два в другую сторону.
    kv_row "$(t bk_f_file)"    "${WHITE}$(basename "$arc")${NC}"                              9
    kv_row "$(t bk_f_version)" "${WHITE}${m_ver}${NC}   ${DIM}$(t bk_f_date): ${m_date}${NC}" 9
    kv_row "$(t bk_f_host)"    "${DIM}${m_host}${NC}"                                         9
    kv_row "$(t bk_f_domain)"  "${WHITE}${m_dom:-—}${NC}"                                     9
    kv_row "$(t bk_f_ports)"   "${WHITE}DoH ${m_doh:-—} · DoT ${m_dot:-—}${NC}"               9
    echo ""
    log_success "$(t bk_certs_untouched)"

    # Откат назад опаснее отката вперёд: конфиг, написанный НОВОЙ версией,
    # может содержать ключи, которых старая не понимает, а миграции идут
    # только вперёд — обратной дороги в схеме не предусмотрено.
    if [ "$m_ver" != "?" ] && godns_version_lt "${GODNS_VERSION:-0}" "$m_ver"; then
        echo ""
        log_warning "$(tf bk_newer_warn "$m_ver" "${GODNS_VERSION:-?}")"
        log_dim "$(t bk_newer_hint)"
    fi

    echo ""
    if [ "$assume_yes" != "yes" ]; then
        if ! confirm "$(t bk_restore_ask)" n; then
            rm -rf "$work"
            log_info "$(t startup_cancelled)"
            return 0
        fi
    fi

    # ── страховка: копия ТЕКУЩЕГО состояния ─────────────────────────────
    # Восстановление — тоже перезапись. Без этой копии «восстановил не тот
    # архив» было бы необратимым.
    local safety=""
    if [ -f "$GODNS_CONFIG" ]; then
        safety=$(godns_backup_create "pre-restore") || safety=""
        if [ -n "$safety" ]; then
            log_dim "$(tf bk_safety_made "$safety")"
        else
            log_warning "$(t bk_safety_failed)"
            if [ "$assume_yes" != "yes" ]; then
                confirm "$(t bk_safety_continue)" n || { rm -rf "$work"; return 1; }
            fi
        fi
    fi

    # ── укладываем ──────────────────────────────────────────────────────
    mkdir -p "$(dirname "$GODNS_CONFIG")" 2>/dev/null || true
    if ! cp -f "$cfg_in" "$GODNS_CONFIG" 2>/dev/null; then
        rm -rf "$work"
        log_error "$(tf bk_write_failed "$GODNS_CONFIG")"
        return 1
    fi
    chmod 0600 "$GODNS_CONFIG" 2>/dev/null || true
    log_success "$(t bk_restored_config)"

    if [ -f "${base}/state/language" ]; then
        cp -f "${base}/state/language" "${GODNS_DIR}/.language" 2>/dev/null || true
        log_dim "$(t bk_restored_lang)"
    fi

    if [ -d "${base}/state/etc-godns" ]; then
        mkdir -p "$GODNS_ETC" 2>/dev/null || true
        cp -a "${base}/state/etc-godns/." "$GODNS_ETC/" 2>/dev/null || true
        chmod 0700 "$GODNS_ETC" 2>/dev/null || true
        log_dim "$(t bk_restored_etc)"
    fi

    # Копия помнит, что бот и панель были включены, и возвращает их ключи
    # в /etc/godns — но САМИ юниты на этой машине могут быть не
    # установлены (архив приехал с другой). Молча оставлять оператора без
    # панели нельзя: config говорит «включено», а служба не стоит.
    if [ "$(config_get webui_enabled "no")" = "yes" ] \
       && ! webui_installed 2>/dev/null; then
        log_warning "$(t bk_restore_webui_absent)"
    fi
    # Признак «бот был» — bot_app_fingerprint: отдельного флага установки
    # бот в конфиг не пишет, а отпечаток кладёт при каждой сборке.
    if [ -n "$(config_get bot_app_fingerprint "")" ] \
       && ! godns_bot_installed 2>/dev/null; then
        log_warning "$(t bk_restore_bot_absent)"
    fi

    # Конфиг движка НЕ ставим на место. Это Lua, который исполнит служба;
    # архив мог приехать с чужой машины. Кладём рядом как справку и
    # перегенерируем настоящий из восстановленного config.json.
    if [ -f "${base}/engine/dnsdist.conf" ]; then
        local ref="$(_bk_engine_conf).from-backup"
        mkdir -p "$(dirname "$ref")" 2>/dev/null || true
        if cp -f "${base}/engine/dnsdist.conf" "$ref" 2>/dev/null; then
            chmod 0600 "$ref" 2>/dev/null || true
            log_dim "$(tf bk_engine_conf_kept "$ref")"
        fi
    fi

    rm -rf "$work"
    [ "$made_plain" = "yes" ] && rm -f "$tar_file" 2>/dev/null

    # ── схема ───────────────────────────────────────────────────────────
    # ГРАБЛИ: миграция звалась с ПУСТЫМ GODNS_MIGRATED_FROM, поэтому
    # ступени, закрытые гейтом `godns_migrated_from_before "X"`, не
    # выполнялись. А ниже godns_mark_applied штамповал в конфиг версию
    # КОДА, а не архива, — и следующий запуск видел «та же версия»,
    # молчаливый no-op, и гейтованные ступени не исполнялись уже НИКОГДА.
    # Развёрнутая копия от 0.13.0 под кодом 0.14.1 навсегда оставалась
    # немигрированной, а конфиг при этом утверждал, что он от 0.14.1.
    #
    # Версию архива знает манифест — он прочитан выше как m_ver.
    if declare -F godns_config_migrate >/dev/null 2>&1; then
        if [ "$m_ver" != "?" ] && [ -n "$m_ver" ]; then
            GODNS_MIGRATED_FROM="$m_ver"
            export GODNS_MIGRATED_FROM
        fi
        godns_config_migrate || log_warning "$(t bk_migrate_failed)"
    fi

    # ── сертификат: пути из архива могут указывать в никуда ─────────────
    # Ровно тот же класс, из-за которого goTelegram не восстанавливает код
    # из архива: чужой архив несёт чужую правду о системе. Здесь она
    # безобидна, но молча приводит к службе, которая не поднимется.
    local cert key
    cert=$(config_get cert_path "") || cert=""
    key=$(config_get key_path "")  || key=""
    if [ -z "$cert" ] || [ ! -r "$cert" ] || [ -z "$key" ] || [ ! -r "$key" ]; then
        log_warning "$(tf bk_cert_missing_now "${cert:-—}")"
        log_dim "$(t bk_cert_missing_hint)"
        log_dim "$(t bk_cert_missing_hint_2)"
        log_success "$(t bk_restore_partial)"
        return 0
    fi

    # ── применяем ───────────────────────────────────────────────────────
    if ! declare -F engine_apply >/dev/null 2>&1; then
        log_success "$(t bk_restore_done_noapply)"
        return 0
    fi
    if engine_apply; then
        declare -F godns_mark_applied >/dev/null 2>&1 && godns_mark_applied
        # Хук/страж перевыпуска сертификата из архива НЕ восстанавливается
        # (исполняемое из архива на место не кладём) — переоформляем заново
        # из дистрибутива: после восстановления на чистой машине его нет.
        if declare -F certs_setup_renewal_reaction >/dev/null 2>&1; then
            certs_setup_renewal_reaction \
                "$(config_get domain "")" "$(config_get cert_path "")" \
                || log_warning "$(t deploy_hook_failed)"
        fi
        log_success "$(t bk_restore_done)"
        if declare -F godns_doh_url >/dev/null 2>&1; then
            log_dim "$(tf bk_restore_doh "$(godns_doh_url)")"
        fi
        return 0
    fi
    log_error "$(t bk_restore_apply_failed)"
    [ -n "$safety" ] && log_dim "$(tf bk_safety_hint "$safety")"
    return 1
}

# ═══════════════════════════════════════════════════════════════════════
# РАСПИСАНИЕ
# ═══════════════════════════════════════════════════════════════════════
godns_backup_calendar() {
    case "${1:-off}" in
        off)     printf '' ;;
        daily)   printf '*-*-* 04:20:00' ;;
        weekly)  printf 'Sun 04:20:00' ;;
        monthly) printf '*-*-01 04:20:00' ;;
        *) return 1 ;;
    esac
    return 0
}

# Каталог юнитов — переменной, а не константой: только так тест может
# доказать, что ExecStart отрендерился правильно. Юнит, который молча
# ничего не делает, неотличим от юнита, который работает: таймер срабатывает,
# служба «успешно» завершается, копий нет.
GODNS_SYSTEMD_DIR="${GODNS_SYSTEMD_DIR:-/etc/systemd/system}"

# Рендер юнитов отделён от systemctl намеренно: собственно текст юнита —
# чистая логика, и её надо проверять на фикстуре, без root и без systemd.
# _bk_render_units <файл службы> <файл таймера> <OnCalendar>
_bk_render_units() {
    local svc="${1:-}" tmr="${2:-}" calendar="${3:-}"
    local inst="${GODNS_INSTALLER_DIR:-/opt/godns-installer}"

    mkdir -p "$(dirname "$svc")" 2>/dev/null || true

    # only-if-changed: конфиг меняется, только когда оператор что-то
    # настроил. Без него ежедневный таймер за десять дней вытеснил бы
    # единственную полезную копию десятью одинаковыми.
    #
    # Своего [Install] у службы нет намеренно: её запускает таймер, и
    # `systemctl enable` на ней означал бы ещё один запуск при загрузке.
    cat > "$svc" <<SVC
[Unit]
Description=goDNS Pro scheduled backup
After=network-online.target

[Service]
Type=oneshot
ExecStart=/bin/bash -c 'set -u; . ${inst}/lib/common.sh; . ${inst}/lib/i18n.sh; . ${inst}/lib/backup.sh; load_language "\$(detect_language)" >/dev/null 2>&1 || true; godns_backup_create auto "" only-if-changed >/dev/null; godns_backup_prune'
SVC

    cat > "$tmr" <<TMR
[Unit]
Description=goDNS Pro scheduled backup

[Timer]
OnCalendar=${calendar}
Persistent=true
RandomizedDelaySec=15m
Unit=${GODNS_BACKUP_TIMER}.service

[Install]
WantedBy=timers.target
TMR

    # Проверяем РЕЗУЛЬТАТ, а не код возврата: при нехватке места на диске
    # `cat >` завершается успешно, а файл остаётся обрезанным. Ровно тот же
    # приём, что с `[ -s ]` после tar в godns_backup_create.
    if [ ! -s "$svc" ] || [ ! -s "$tmr" ]; then
        rm -f "$svc" "$tmr" 2>/dev/null || true
        return 1
    fi
    return 0
}

godns_backup_schedule_set() {
    local freq="${1:-off}" calendar=""
    calendar=$(godns_backup_calendar "$freq") || {
        log_error "$(tf bk_sched_bad "$freq")"; return 1; }

    if ! command -v systemctl >/dev/null 2>&1; then
        log_warning "$(t bk_sched_no_systemd)"
        return 1
    fi

    local svc="${GODNS_SYSTEMD_DIR}/${GODNS_BACKUP_TIMER}.service"
    local tmr="${GODNS_SYSTEMD_DIR}/${GODNS_BACKUP_TIMER}.timer"

    if [ "$freq" = "off" ]; then
        systemctl disable --now "${GODNS_BACKUP_TIMER}.timer" >/dev/null 2>&1 || true
        rm -f "$tmr" "$svc" 2>/dev/null || true
        systemctl daemon-reload >/dev/null 2>&1 || true
        config_set backup_schedule "off" || true
        log_success "$(t bk_sched_off)"
        return 0
    fi

    _bk_ensure_dir "$GODNS_BACKUP_DIR" || return 1

    if ! _bk_render_units "$svc" "$tmr" "$calendar"; then
        log_error "$(t bk_sched_write_failed)"
        return 1
    fi

    systemctl daemon-reload >/dev/null 2>&1 || true
    if ! systemctl enable --now "${GODNS_BACKUP_TIMER}.timer" >/dev/null 2>&1; then
        log_error "$(t bk_sched_enable_failed)"
        return 1
    fi
    config_set backup_schedule "$freq" || true
    log_success "$(tf bk_sched_on "$freq" "$calendar")"
    return 0
}

godns_backup_schedule_status() {
    local freq
    freq=$(config_get backup_schedule "off") || freq="off"
    echo -e "  $(t bk_sched_current): ${WHITE}$(godns_backup_schedule_label "$freq")${NC}"
    if command -v systemctl >/dev/null 2>&1; then
        # `--no-pager` не убирает подсказку «Pass --all to see loaded but
        # inactive timers, too.» — она попадала в отчёт оператору как
        # непонятный служебный шум. Берём только строки самого таймера.
        #
        # И не только её: при нуле таймеров systemd всё равно печатает
        # английскую шапку `NEXT LEFT LAST PASSED UNIT ACTIVATES` — на
        # русском экране это выглядело как вылезший наружу служебный вывод.
        # Шапку узнаём по слову UNIT в строке заголовка.
        local rows=""
        rows=$(systemctl list-timers "${GODNS_BACKUP_TIMER}.timer" --no-pager 2>/dev/null \
            | grep -vE '^\s*$|Pass --all|^[0-9]+ timers listed|(^|[[:space:]])UNIT[[:space:]]+ACTIVATES') || rows=""
        if [ -n "$rows" ]; then
            printf '%s\n' "$rows"
        else
            echo -e "  ${DIM}$(t bk_sched_none)${NC}"
        fi
    fi
    return 0
}

# Значение конфига — машинное слово (off/daily/weekly/monthly). На экран оно
# попадало как есть: «Сейчас: off» посреди русского текста.
godns_backup_schedule_label() {
    case "$1" in
        daily)   t bk_sched_daily ;;
        weekly)  t bk_sched_weekly ;;
        monthly) t bk_sched_monthly ;;
        *)       t bk_sched_val_off ;;
    esac
}

# ═══════════════════════════════════════════════════════════════════════
# ИНТЕРАКТИВНЫЕ ОБЁРТКИ
# ═══════════════════════════════════════════════════════════════════════
godns_backup_interactive() {
    print_header "$(t bk_create_title)"
    echo -e "  ${DIM}$(t bk_create_note)${NC}"
    echo -e "  ${DIM}$(t bk_create_note_2)${NC}"
    echo ""

    local password="" p2=""
    # По умолчанию НЕТ: на сервере архив лежит в /root с правами 0600 —
    # ровно как оригиналы. Шифрование нужно тому, кто увезёт архив с
    # сервера, и спрашивать об этом надо честно, а не подразумевать.
    if confirm "$(t bk_encrypt_ask)" n; then
        echo -ne "  $(t bk_enter_pass): " >&2
        read -rs password || password=""
        echo "" >&2
        echo -ne "  $(t bk_repeat_pass): " >&2
        read -rs p2 || p2=""
        echo "" >&2
        if [ "$password" != "$p2" ]; then
            log_error "$(t bk_pass_mismatch)"
            return 1
        fi
        if [ "${#password}" -lt 8 ]; then
            log_error "$(t bk_pass_short)"
            return 1
        fi
        log_warning "$(t bk_pass_lost_warn)"
    fi

    local label=""
    echo -ne "  $(t bk_label_ask) " >&2
    godns_read_line || { echo "" >&2; return 1; }
    label="$GODNS_REPLY"

    local out=""
    out=$(godns_backup_create "$label" "$password") || out=""
    [ -n "$out" ] || return 1
    # ГРАБЛИ: прореживание внутри создания молчало (`>/dev/null 2>&1`), и
    # копии сверх лимита исчезали без единого слова — оператор насчитывал
    # одиннадцать архивов, создавал двенадцатый и получал десять, не зная,
    # что два старейших только что удалены навсегда. Отдельный пункт
    # «Проредить» при этом честно называет каждый удалённый. Создание
    # обязано говорить то же самое: тихая потеря копии — худшее, что может
    # сделать система резервного копирования.
    godns_backup_prune || true
    return 0
}

godns_backup_restore_interactive() {
    godns_backup_list || return 1

    local choice="" files="" f i=1 pick=""
    echo -ne "  $(t bk_pick_ask) " >&2
    read -r choice || { echo ""; return 0; }
    [ -z "$choice" ] && return 0
    [ "$choice" = "0" ] && return 0

    files=$(godns_backup_files) || files=""
    case "$choice" in
        ''|*[!0-9]*)
            if [ -f "$choice" ]; then pick="$choice"; fi
            ;;
        *)
            while IFS= read -r f; do
                [ -n "$f" ] || continue
                if [ "$i" -eq "$choice" ]; then pick="$f"; break; fi
                i=$((i + 1))
            done <<EOF
$files
EOF
            ;;
    esac

    if [ -z "$pick" ]; then
        log_warning "$(t bk_not_in_list)"
        return 1
    fi
    godns_backup_restore "$pick"
}
