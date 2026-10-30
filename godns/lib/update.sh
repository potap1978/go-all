#!/bin/bash
# goDNS — Copyright (c) 2026 potap1978. All rights reserved.
#
# goDNS — обновление поверх: определение, бэкап, миграция схемы, откат.
#
# ── Что именно должно пережить обновление ───────────────────────────────
# Секретный путь DoH и оба порта. Их смена — это не «неудобство»: клиенты
# настроены руками, по одному, и новый адрес им никто не разошлёт. Смена
# пути отваливает ВСЕХ разом и без сообщения — у клиента просто перестаёт
# работать интернет. Поэтому значения читаются в глобальные переменные ДО
# любой записи и сверяются ПОСЛЕ (godns_preserve_existing/_restore_preserved).
#
# ── Как install.sh участвует ────────────────────────────────────────────
#   install.sh  →  godns_update_prepare   (ДО подмены /opt/godns-installer:
#                                          копия снимается СТАРЫМ кодом,
#                                          пока он ещё на диске)
#   godns.sh    →  godns_update_finish    (при каждом старте; на совпадающих
#                                          версиях — молчаливый no-op)
#
# prepare необязателен: кто обновляется руками (git pull в каталог
# программы), prepare не вызовет вовсе. Поэтому гарантию «перед обновлением
# всегда есть копия» держит finish — он сам делает копию, если её нет.
#
# ── Версионирование схемы конфига ───────────────────────────────────────
# Два независимых числа, и это не дублирование:
#   GODNS_VERSION — версия ПРОГРАММЫ. Меняется каждый релиз.
#   GODNS_SCHEMA  — версия ФОРМАТА config.json. Меняется только когда
#                   ключ переименован, удалён или сменил смысл.
# Схема хранится в самом config.json (ключ `schema`). Миграция крутится
# ступенями: godns_migrate_step_<n> переводит схему n → n+1.
#
# Требует: lib/common.sh, lib/backup.sh (и, желательно, lib/i18n.sh).
# Ассоциативных массивов нет: файл обязан работать на bash 3.2.

if ! declare -F t >/dev/null 2>&1; then
    t()  { printf '%s' "$1"; }
    tf() {
        local _k="$1"; shift
        printf '%s' "$_k"
        if [ "$#" -gt 0 ]; then printf ' [%s]' "$*"; fi
        return 0
    }
fi

# Версия, С КОТОРОЙ обновляемся. Пусто — не обновление.
# Приём из goTelegram: миграции пишутся как
#     if godns_migrated_from_before "0.4.0"; then … fi
# — то есть гейт по версии, а не по факту существования файла.
GODNS_MIGRATED_FROM=""

# «Отчёт об обновлении был напечатан». Флаг нужен вызывающему: сразу после
# godns_update_finish главное меню делает clear, и «обновлено с X до Y,
# настройки сохранены» мелькнуло бы и исчезло — то самое сообщение, ради
# которого вся эта машинерия и написана. Пауза живёт в godns.sh: читать
# stdin ради оформления — не дело библиотеки, и тесты за это платят
# зависанием на `read`.
GODNS_UPDATE_SHOWN="no"

# Файл-указатель на копию, снятую перед обновлением. Живёт в нашем
# каталоге состояния, а не в /tmp: откат может понадобиться после
# перезагрузки.
_upd_marker_file() { printf '%s' "${GODNS_ETC}/.pre-update-backup"; }

# ═══════════════════════════════════════════════════════════════════════
# ЧТО ЗНАЧИТ «УЖЕ УСТАНОВЛЕНО»
# ═══════════════════════════════════════════════════════════════════════
# ГРАБЛИ, унаследованные из goTelegram (install.sh:1035) и адаптированные:
# там признаком «уже стоит» брали каталог, и человек на ЧИСТОЙ машине
# получал вопрос «продолжить обновление?». Гейтом сделали config.json —
# у них он пишется в самом конце установки.
#
# У НАС ПРОСТО КОПИРОВАТЬ ЭТОТ ГЕЙТ НЕЛЬЗЯ. config.json здесь создаёт уже
# save_language при первом запуске меню: язык выбирают ДО того, как
# что-нибудь установлено, и файл `{"language":"ru"}` появляется на
# абсолютно чистой машине. Гейт по существованию файла означал бы отчёт об
# обновлении и лишний архив у того, кто просто открыл меню.
#
# Признак настоящей установки — след мастера: выбранный домен (шаг 1) или
# штамп успешного применения. Языка мало.
godns_state_present() {
    [ -f "$GODNS_CONFIG" ] || return 1
    local v=""
    v=$(config_get applied_fingerprint "") || v=""
    [ -n "$v" ] && return 0
    v=$(config_get godns_version "") || v=""
    [ -n "$v" ] && return 0
    v=$(config_get domain "") || v=""
    [ -n "$v" ] || return 1
    # Домен есть, а штампа применения и версии нет. Так выглядят ДВА разных
    # состояния: установка старше привычки писать версию (движок стоит) и
    # ПРЕРВАННЫЙ мастер — домен и сертификат записаны шагом 1, до
    # развёртывания дело не дошло. ГРАБЛИ со стенда (0.32.0): во втором
    # случае следующий запуск печатал «Обновление 0.0.0 → …», снимал лишнюю
    # копию, показывал «что нового» за все версии и — главное — прятал пункт
    # «Быстрая установка»: довести установку из меню было нечем. Различаем
    # по движку: не установлен — это не установка, а недоделанный первый
    # шаг. Без загруженного движка (мост панели до engine_select) ответ
    # прежний — «стоит»: ошибочное «стоит» безопаснее ошибочного «чисто».
    if [ -n "${GODNS_ENGINE:-}" ] && declare -F engine_is_installed >/dev/null 2>&1; then
        if ! engine_is_installed 2>/dev/null; then return 1; fi
    fi
    return 0
}

# Какая версия стоит СЕЙЧАС. Источник ровно один — config.json, куда версия
# попадает в момент успешного применения (godns_mark_applied).
#
# Читать версию со СТАРОГО lib/common.sh на диске соблазнительно и вредно:
# godns_update_finish запускается уже ПОСЛЕ подмены каталога программы, и
# там этот файл содержит НОВУЮ версию. Такой «запасной источник» отвечал бы
# «стоит 0.2.0» на установке, где стоит 0.0.9, — то есть тихо отменял бы
# миграцию ровно тем, кому она нужна. Один честный источник лучше двух, из
# которых один врёт в половине вызовов.
#
# Конфиг без версии — установка старше самой привычки её писать. Возвращаем
# 0.0.0, чтобы гейты миграций сработали, а не молча пропустились.
godns_installed_version() {
    local v=""
    v=$(config_get godns_version "") || v=""
    [ -n "$v" ] || v="0.0.0"
    printf '%s' "$v"
    return 0
}

# fresh | same | upgrade | downgrade  — только в stdout, без логов.
godns_update_state() {
    if ! godns_state_present; then
        printf 'fresh'
        return 0
    fi
    local old new="${GODNS_VERSION:-0.0.0}"
    old=$(godns_installed_version) || old="0.0.0"
    if [ "$old" = "$new" ]; then
        printf 'same'
        return 0
    fi
    if godns_version_lt "$old" "$new"; then
        printf 'upgrade'
        return 0
    fi
    printf 'downgrade'
    return 0
}

# rc0, если обновляемся с версии СТАРШЕ указанной.
# Шаблон гейта миграции:  godns_migrated_from_before "0.4.0" && …
godns_migrated_from_before() {
    local want="${1:-}"
    [ -n "${GODNS_MIGRATED_FROM:-}" ] || return 1
    [ -n "$want" ] || return 1
    godns_version_lt "$GODNS_MIGRATED_FROM" "$want"
}

# ═══════════════════════════════════════════════════════════════════════
# СОХРАНИТЬ ЖИВЫЕ ЗНАЧЕНИЯ
# ═══════════════════════════════════════════════════════════════════════
# Результат — ТОЛЬКО в глобальных переменных. В goTelegram эта же функция
# однажды вызывалась через $(…), исполнялась в подоболочке, присваивания
# гибли вместе с ней, и «сохранение» молча не сохраняло ничего.
GODNS_KEPT_DOH_PATH=""
GODNS_KEPT_DOH_PORT=""
GODNS_KEPT_DOT_PORT=""
GODNS_KEPT_DOMAIN=""

godns_preserve_existing() {
    GODNS_KEPT_DOH_PATH=$(config_get doh_path "") || GODNS_KEPT_DOH_PATH=""
    GODNS_KEPT_DOH_PORT=$(config_get doh_port "") || GODNS_KEPT_DOH_PORT=""
    GODNS_KEPT_DOT_PORT=$(config_get dot_port "") || GODNS_KEPT_DOT_PORT=""
    GODNS_KEPT_DOMAIN=$(config_get domain "")     || GODNS_KEPT_DOMAIN=""
    return 0
}

# Сверка ПОСЛЕ миграции. Не «на всякий случай»: миграция пишет в тот же
# файл, и ошибка в ней стоила бы всех клиентов сразу. Восстанавливаем и
# говорим вслух — молчаливое исправление скрыло бы поломку миграции.
godns_restore_preserved() {
    local now fixed=0
    now=$(config_get doh_path "") || now=""
    if [ -n "$GODNS_KEPT_DOH_PATH" ] && [ "$now" != "$GODNS_KEPT_DOH_PATH" ]; then
        config_set doh_path "$GODNS_KEPT_DOH_PATH" || true
        fixed=1
    fi
    now=$(config_get doh_port "") || now=""
    if [ -n "$GODNS_KEPT_DOH_PORT" ] && [ "$now" != "$GODNS_KEPT_DOH_PORT" ]; then
        config_set doh_port "$GODNS_KEPT_DOH_PORT" || true
        fixed=1
    fi
    now=$(config_get dot_port "") || now=""
    if [ -n "$GODNS_KEPT_DOT_PORT" ] && [ "$now" != "$GODNS_KEPT_DOT_PORT" ]; then
        config_set dot_port "$GODNS_KEPT_DOT_PORT" || true
        fixed=1
    fi
    now=$(config_get domain "") || now=""
    if [ -n "$GODNS_KEPT_DOMAIN" ] && [ "$now" != "$GODNS_KEPT_DOMAIN" ]; then
        config_set domain "$GODNS_KEPT_DOMAIN" || true
        fixed=1
    fi
    if [ "$fixed" -eq 1 ]; then
        log_warning "$(t upd_preserved_restored)"
        return 0
    fi
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
# СХЕМА КОНФИГА
# ═══════════════════════════════════════════════════════════════════════
godns_config_schema() {
    local s=""
    s=$(config_get schema "") || s=""
    case "$s" in ''|*[!0-9]*) s="0" ;; esac
    printf '%s' "$s"
    return 0
}

# Ступень 0 → 1: первое версионирование формата.
#   secret_path → doh_path,  doh_domain → domain
# Старые ключи НЕ удаляем: lib/selftest.sh читает их как запасной вариант,
# и удаление сделало бы откат на предыдущую версию хуже, чем он есть.
godns_migrate_step_0() {
    local v=""
    v=$(config_get doh_path "") || v=""
    if [ -z "$v" ]; then
        v=$(config_get secret_path "") || v=""
        if [ -n "$v" ]; then
            config_set doh_path "${v#/}" || return 1
            log_dim "$(tf upd_moved_key "secret_path" "doh_path")"
        fi
    fi
    v=$(config_get domain "") || v=""
    if [ -z "$v" ]; then
        v=$(config_get doh_domain "") || v=""
        if [ -n "$v" ]; then
            config_set domain "$v" || return 1
            log_dim "$(tf upd_moved_key "doh_domain" "domain")"
        fi
    fi
    if [ -z "$(config_get engine "")" ]; then
        config_set engine "dnsdist" || return 1
    fi
    return 0
}

# Крутим ступенями до текущей схемы. Ступень n переводит n → n+1.
godns_config_migrate() {
    local have want="${GODNS_SCHEMA:-1}" step
    have=$(godns_config_schema) || have="0"

    if [ "$have" -gt "$want" ] 2>/dev/null; then
        # Конфиг из будущего. Миграции идут только вперёд, и «понизить»
        # схему нечем. Трогать такой файл опаснее, чем оставить как есть.
        log_warning "$(tf upd_schema_future "$have" "$want")"
        return 1
    fi
    if [ "$have" -eq "$want" ] 2>/dev/null; then
        return 0
    fi

    step="$have"
    while [ "$step" -lt "$want" ] 2>/dev/null; do
        if declare -F "godns_migrate_step_${step}" >/dev/null 2>&1; then
            if ! "godns_migrate_step_${step}"; then
                log_error "$(tf upd_schema_step_failed "$step" "$((step + 1))")"
                return 1
            fi
        fi
        step=$((step + 1))
    done

    config_set schema "$want" || return 1
    log_dim "$(tf upd_schema_done "$have" "$want")"
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
# НОРМАЛИЗАЦИЯ — НА ЛЮБОМ ОБНОВЛЕНИИ, БЕЗ ВЕРСИОННОГО ГЕЙТА
# ═══════════════════════════════════════════════════════════════════════
# ГРАБЛИ (goTelegram install.sh:1014): нормализацию однажды спрятали за
# `_ver_lt "$MIGRATED_FROM" "2.7.4"` — и гейт отсёк ровно всех живых
# пользователей, потому что все они были новее. Приводить состояние к
# текущей модели надо на КАЖДОМ обновлении; гейт по версии — только для
# правок, осмысленных ровно для одной старой версии.
godns_migrate_normalize() {
    # Отпечаток применённого хешируется по фиксированному набору полей.
    # Стоит набору поменяться в новой версии — и панель у всех, кто
    # обновился, показывает «есть непринятые изменения», хотя никто ничего
    # не менял. Пере-штампуем, но ТОЛЬКО если служба реально работает:
    # иначе мы бы «подтвердили» конфигурацию, которая ни разу не
    # применялась, и спрятали бы настоящее расхождение.
    if declare -F engine_is_running >/dev/null 2>&1 \
       && declare -F godns_mark_applied >/dev/null 2>&1; then
        if engine_is_running && godns_config_drifted; then
            godns_mark_applied || true
            log_dim "$(t upd_fingerprint_refreshed)"
        fi
    fi
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
# ПЕРЕД ОБНОВЛЕНИЕМ  (зовёт install.sh, пока старый код ещё на диске)
# ═══════════════════════════════════════════════════════════════════════
godns_update_prepare() {
    local state old new="${GODNS_VERSION:-?}"
    state=$(godns_update_state) || state="fresh"

    if [ "$state" = "fresh" ]; then
        log_dim "$(t upd_fresh)"
        return 0
    fi

    old=$(godns_installed_version) || old="?"
    if [ "$state" = "same" ]; then
        # Переустановка той же версии — не обновление. Отдельная копия на
        # каждый повторный запуск установщика засорила бы хранилище и
        # вытеснила бы полезные архивы.
        log_dim "$(tf upd_same "$new")"
        return 0
    fi

    if [ "$state" = "downgrade" ]; then
        log_warning "$(tf upd_downgrade "$old" "$new")"
    else
        log_info "$(tf upd_detected "$old" "$new")"
    fi

    local arc=""
    arc=$(godns_backup_create "pre-update-${old}") || arc=""
    if [ -z "$arc" ]; then
        # Нет копии — нет обновления. В goTelegram провал tar однажды
        # проглатывался, и конфиг переписывался без возможности отката.
        log_error "$(t upd_backup_failed)"
        return 1
    fi

    mkdir -p "$GODNS_ETC" 2>/dev/null || true
    chmod 0700 "$GODNS_ETC" 2>/dev/null || true
    printf '%s\n' "$arc" > "$(_upd_marker_file)" 2>/dev/null || true
    chmod 0600 "$(_upd_marker_file)" 2>/dev/null || true

    config_set previous_version "$old" || true
    godns_backup_prune >/dev/null 2>&1 || true

    log_success "$(tf upd_backup_ok "$arc")"
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
# ПОСЛЕ ОБНОВЛЕНИЯ  (зовёт godns.sh при каждом старте)
# ═══════════════════════════════════════════════════════════════════════
# Конфиг движка перерисовать НОВЫМ кодом — на каждом обновлении, без гейта
# по версии. Рендер меняется почти в каждом выпуске, а служба крутила бы
# старый файл, пока оператор что-нибудь не поменяет: так исправление
# 0.32.0 (поштучный лимит вместо бана) не доехало бы ни до одного из тех,
# у кого роутеры сидели под баном. Только если конфигурация уже
# применялась (есть applied_fingerprint): на недонастроенной установке
# применять нечего. Неудача — не повод рвать обновление: старый конфиг
# остаётся на месте (dnsdist_apply меняет файл только после проверки).
godns_update_reapply() {
    GODNS_REAPPLY_DONE="no"
    declare -F engine_is_installed >/dev/null 2>&1 || return 0
    declare -F engine_apply        >/dev/null 2>&1 || return 0
    local fp=""
    fp=$(config_get applied_fingerprint "") || fp=""
    [ -n "$fp" ] || return 0
    engine_is_installed || return 0
    # Нечего перерисовывать — не перезапускаем: обновление ради README не
    # должно рвать соединения клиентов (урок «ничего не изменилось — не
    # повод применять»). Сравниваются строки Lua, без заголовка с датой.
    if declare -F engine_config_is_current >/dev/null 2>&1 && engine_config_is_current; then
        if declare -F godns_mark_applied >/dev/null 2>&1; then
            godns_mark_applied || true
        fi
        log_dim "$(t upd_reapply_same)"
        return 0
    fi
    GODNS_REAPPLY_DONE="yes"
    log_info "$(t upd_reapply)"
    if engine_apply; then
        if declare -F godns_mark_applied >/dev/null 2>&1; then
            godns_mark_applied || true
        fi
        log_success "$(t upd_reapply_ok)"
        return 0
    fi
    # Провал — дрейф настоящий: служба осталась на старом конфиге. Штамп к
    # этому моменту уже пересчитан нормализацией (она идёт раньше и обязана
    # идти раньше восстановления портов), поэтому метку ставим явно:
    # значение, которое ни с одним отпечатком не совпадёт. «Настройки DNS»
    # увидят непринятые изменения и применят при первом же действии.
    config_set applied_fingerprint "reapply-failed-${GODNS_VERSION:-?}" || true
    log_error "$(t upd_reapply_failed)"
    return 0
}

godns_update_finish() {
    local state old new="${GODNS_VERSION:-?}"
    state=$(godns_update_state) || state="fresh"

    # Молчаливый no-op — это ТРЕБОВАНИЕ, а не оптимизация. В goTelegram
    # чистая установка той же версии выходила из миграции, не поставив
    # маркер, и человек при каждом заходе видел «Миграция состояния…»,
    # вопрос «продолжить обновление?» и лишний архив на диске.
    case "$state" in
        fresh|same) return 0 ;;
    esac

    old=$(godns_installed_version) || old="0.0.0"
    GODNS_MIGRATED_FROM="$old"

    # Флаг поднимаем ЗДЕСЬ, а не после отчёта: пауза нужна и тогда, когда
    # обновление сорвалось на середине. Иначе сообщение об ошибке стёр бы
    # clear в show_dashboard — то есть громче всего молчали бы как раз в
    # том случае, ради которого всё и написано.
    GODNS_UPDATE_SHOWN="yes"

    print_header "$(t upd_title)"
    if [ "$state" = "downgrade" ]; then
        log_warning "$(tf upd_downgrade "$old" "$new")"
    else
        log_info "$(tf upd_detected "$old" "$new")"
    fi

    # Гарантия «перед обновлением всегда есть копия». prepare зовёт только
    # install.sh; тот, кто обновился руками, до сюда доходит без копии.
    local arc=""
    arc=$(_upd_recorded_backup) || arc=""
    if [ -z "$arc" ]; then
        arc=$(godns_backup_create "pre-update-${old}") || arc=""
        if [ -z "$arc" ]; then
            log_error "$(t upd_backup_failed)"
            log_warning "$(t upd_aborted)"
            return 1
        fi
        mkdir -p "$GODNS_ETC" 2>/dev/null || true
        printf '%s\n' "$arc" > "$(_upd_marker_file)" 2>/dev/null || true
        chmod 0600 "$(_upd_marker_file)" 2>/dev/null || true
        log_success "$(tf upd_backup_ok "$arc")"
    else
        log_dim "$(tf upd_backup_reused "$arc")"
    fi

    # Значения, потеря которых отваливает всех клиентов, — в глобальные
    # переменные ДО первой записи.
    godns_preserve_existing

    if ! godns_config_migrate; then
        log_error "$(t upd_migrate_failed)"
        log_warning "$(tf upd_migrate_hint "$arc")"
        return 1
    fi
    godns_migrate_normalize
    godns_restore_preserved

    config_set godns_version "$new" || true
    config_set previous_version "$old" || true

    godns_update_reapply

    godns_update_report "$old" "$new"
    if [ "$state" = "upgrade" ]; then
        godns_update_changelog
    fi

    # Обновилось ≠ работает. Молчать об упавшей службе нельзя: оператор
    # уйдёт в уверенности, что всё поднялось, а клиенты уже отвалились.
    #
    # Но ПЕРЕД предложением отката — одна попытка применить конфигурацию
    # НОВЫМ кодом. Обновление часто ставят именно потому, что старый рендер
    # был сломан (Unbound на 127.0.0.1:53 ронял службу после зелёной
    # проверки — починено в 0.17.2); откат в этой ситуации — то же самое
    # лекарство, от которого уже плохо: старая версия несёт тот же баг.
    # Не помогло применение — тогда честно предлагаем откат, как раньше.
    if declare -F engine_is_installed >/dev/null 2>&1 \
       && declare -F engine_is_running >/dev/null 2>&1; then
        if engine_is_installed && ! engine_is_running; then
            log_warning "$(t upd_service_down_retry)"
            # Перерисовка выше уже пробовала применить новым кодом и не
            # смогла — вторая попытка даст ту же ошибку и второй рестарт;
            # сразу к откату (ревью 0.32.0).
            if [ "${GODNS_REAPPLY_DONE:-no}" != "yes" ] \
               && declare -F engine_apply >/dev/null 2>&1; then
                engine_apply || true
            fi
            if engine_is_running; then
                log_success "$(t upd_service_down_fixed)"
            else
                log_error "$(t upd_service_down)"
                godns_rollback_offer "$arc"
                return 1
            fi
        fi
    fi

    # Маркер снимаем: он выполнил роль (провёл откат, если бы служба не
    # встала). ГРАБЛИ: он жил до следующего обновления, и install.sh взводил
    # по нему «перенос доделается при первом запуске» при переустановке ТОЙ
    # ЖЕ версии — переноса нет, finish на same молчит. Плюс маркер уезжал
    # в бекап и оживал на чужой машине.
    rm -f "$(_upd_marker_file)" 2>/dev/null || true

    godns_backup_prune >/dev/null 2>&1 || true
    return 0
}

# Путь к записанной копии — только если файл действительно на месте.
_upd_recorded_backup() {
    local f p=""
    f=$(_upd_marker_file)
    [ -r "$f" ] || { printf ''; return 0; }
    p=$(head -n1 "$f" 2>/dev/null) || p=""
    p="${p%%$'\n'*}"
    if [ -n "$p" ] && [ -f "$p" ]; then
        printf '%s' "$p"
        return 0
    fi
    printf ''
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
# ОТЧЁТ ОПЕРАТОРУ
# ═══════════════════════════════════════════════════════════════════════
# Обновление, о котором не отчитались, читается как «что-то произошло».
# Показываем именно то, из-за чего человек волнуется: путь и порты целы.
godns_update_report() {
    local old="${1:-?}" new="${2:-?}"
    local dom path doh dot

    dom=$(config_get domain "")   || dom=""
    path=$(config_get doh_path "") || path=""
    doh=$(config_get doh_port "") || doh=""
    dot=$(config_get dot_port "") || dot=""

    echo ""
    echo -e "  ${GREEN}${BOLD}$(tf upd_done "$old" "$new")${NC}"
    print_rule
    kv_row "$(t upd_kept_domain)" "${WHITE}${dom:-—}${NC}"                      15
    kv_row "$(t upd_kept_ports)"  "${WHITE}DoH ${doh:-—} · DoT ${dot:-—}${NC}"   15
    kv_row "$(t upd_kept_path)"   "${WHITE}/${path:-—}${NC}"                     15
    if declare -F godns_doh_url >/dev/null 2>&1; then
        local url; url=$(godns_doh_url) || url=""
        if [ -n "$url" ]; then kv_row "$(t upd_kept_url)" "${WHITE}${url}${NC}" 15; fi
    fi
    print_rule
    echo -e "  ${DIM}$(t upd_clients_note)${NC}"
    echo ""
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
# ЧТО НОВОГО
# ═══════════════════════════════════════════════════════════════════════
# Гейт по версии здесь на своём месте — в отличие от нормализации, где он
# однажды отсёк всех живых пользователей. «Что нового» по определению
# относительно ТОЙ версии, с которой человек обновляется: строки про 0.1.0
# не нужны тому, кто идёт с 0.1.0 на 0.2.0.
#
# Новый релиз — новый блок сверху, гейт на СВОЮ версию. Ничего удалять не
# надо: тот, кто перепрыгнул три версии, увидит все три блока.
godns_update_changelog() {
    local shown=0

    echo ""
    echo -e "  ${BOLD}${WHITE}$(t upd_changes_title)${NC}"
    if godns_migrated_from_before "1.0.1"; then
        echo -e "  ${DIM}1.0.1${NC}"
        echo -e "    • $(t upd_ch_101_ratelimit)"
        echo -e "    • $(t upd_ch_101_brand)"
        echo -e "    • $(t upd_ch_101_main)"
        shown=$((shown + 1))
    fi
    if godns_migrated_from_before "0.5.0"; then
        echo -e "  ${DIM}0.5.0${NC}"
        echo -e "    • $(t upd_ch_050_stats)"
        shown=$((shown + 1))
    fi
    if godns_migrated_from_before "0.1.0"; then
        echo -e "  ${DIM}0.1.0${NC}"
        echo -e "    • $(t upd_ch_010_backup)"
        echo -e "    • $(t upd_ch_010_update)"
        shown=$((shown + 1))
    fi
    if [ "$shown" -eq 0 ]; then
        echo -e "  ${DIM}$(t upd_changes_none)${NC}"
    fi
    echo ""
    return 0
}

# ═══════════════════════════════════════════════════════════════════════
# ОТКАТ
# ═══════════════════════════════════════════════════════════════════════
# Честная формулировка: копия возвращает НАСТРОЙКИ, а не программу.
# Обещать «откат версии» тем, у кого на диске уже новый код, — обман;
# для возврата самой версии есть переустановка с GODNS_REF.
godns_rollback_offer() {
    local arc="${1:-}" prev=""
    [ -n "$arc" ] || arc=$(_upd_recorded_backup) || arc=""
    if [ -z "$arc" ] || [ ! -f "$arc" ]; then
        log_warning "$(t upd_no_rollback)"
        return 1
    fi

    prev=$(config_get previous_version "") || prev=""
    [ -n "$prev" ] || prev="?"

    echo ""
    log_warning "$(tf upd_rollback_intro "$prev")"
    log_dim "$(tf upd_rollback_what "$arc")"
    log_dim "$(t upd_rollback_code)"
    log_dim "$(tf upd_rollback_code_2 "$prev")"
    echo ""

    if ! confirm "$(t upd_rollback_ask)" y; then
        log_info "$(t upd_rollback_declined)"
        return 0
    fi

    if godns_backup_restore "$arc" "" yes; then
        log_success "$(t upd_rollback_done)"
        return 0
    fi
    log_error "$(t upd_rollback_failed)"
    return 1
}
