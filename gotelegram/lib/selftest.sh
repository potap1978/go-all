#!/usr/bin/env bash
# lib/selftest.sh — автопроверка функций БЕЗ сервера (bash install.sh --selftest).
# Цель: ловить регрессии до выката. Ничего не устанавливает и не меняет систему:
# все проверки — на временных копиях в /tmp, сетевые/системные вызовы застабены.

# Счётчики — в файле: часть проверок идёт в подоболочках ( ... ), где переменные не переживают.
ST_TALLY="${ST_TALLY:-}"
_st_add()  { [ -n "$ST_TALLY" ] && printf '%s\n' "$1" >> "$ST_TALLY" 2>/dev/null; return 0; }
_st_count() { local n=0; [ -f "$ST_TALLY" ] && n=$(grep -c "^$1\$" "$ST_TALLY" 2>/dev/null || true); [[ "$n" =~ ^[0-9]+$ ]] || n=0; printf '%s' "$n"; }
_st_ok()   { _st_add PASS; printf '  \033[32m✓\033[0m %s\n' "$1"; }
_st_bad()  { _st_add FAIL; printf '  \033[31m✗\033[0m %s \033[2m(ждали: %s, получили: %s)\033[0m\n' "$1" "$2" "$3"; }
_st_skip() { _st_add SKIP; printf '  \033[2m• пропуск: %s (%s)\033[0m\n' "$1" "$2"; }
_st_eq()   { [ "$2" = "$3" ] && _st_ok "$1" || _st_bad "$1" "$3" "$2"; }
_st_rc()   { local d="$1"; shift; if "$@" >/dev/null 2>&1; then _st_ok "$d"; else _st_bad "$d" "rc=0" "rc=$?"; fi; }
_st_nrc()  { local d="$1"; shift; if "$@" >/dev/null 2>&1; then _st_bad "$d" "rc≠0" "rc=0"; else _st_ok "$d"; fi; }
# Проверка JSON фильтром jq: греп по имени поля подтверждал лишь то, что функция
# что-то напечатала — имена полей есть в её же printf-строке всегда, даже в мусоре.
_st_json() { local d="$1" j="$2" q="$3"
    command -v jq >/dev/null 2>&1 || { _st_skip "$d" "нет jq"; return 0; }
    if jq -e "$q" <<<"$j" >/dev/null 2>&1; then _st_ok "$d"; else _st_bad "$d" "$q" "$j"; fi
}
_st_head() { printf '\n\033[1m%s\033[0m\n' "$1"; }

selftest_run() {
    local tmp; tmp=$(mktemp -d /tmp/gt-selftest.XXXXXX) || return 1
    ST_TALLY="$tmp/tally"; : > "$ST_TALLY"
    trap 'rm -rf "$tmp" 2>/dev/null' RETURN
    printf '\n\033[1;37m  goTelegram Pro — автопроверка (--selftest)\033[0m\n'
    printf '\033[2m  Ничего не устанавливается и не меняется; всё в %s\033[0m\n' "$tmp"

    # ── 1. Синтаксис всех скриптов ─────────────────────────────────────────
    _st_head "1. Синтаксис"
    local f bad=0
    for f in "${SCRIPT_DIR:-.}"/install.sh "${SCRIPT_DIR:-.}"/bootstrap.sh "${LIB_DIR:-./lib}"/*.sh "${LIB_DIR:-./lib}"/lang/*.sh; do
        [ -f "$f" ] || continue
        bash -n "$f" 2>/dev/null || { _st_bad "bash -n $(basename "$f")" "ok" "syntax error"; bad=1; }
    done
    [ "$bad" = 0 ] && _st_ok "bash -n: все скрипты чисты"

    # ── 2. i18n: симметрия ru/en и отсутствие сирот ─────────────────────────
    _st_head "2. i18n"
    if [ -f "${LIB_DIR:-./lib}/lang/ru.sh" ] && [ -f "${LIB_DIR:-./lib}/lang/en.sh" ]; then
        local ru en diff
        ru=$(grep -oE '^I18N\[[a-z0-9_]+\]' "${LIB_DIR:-./lib}/lang/ru.sh" | sort -u)
        en=$(grep -oE '^I18N\[[a-z0-9_]+\]' "${LIB_DIR:-./lib}/lang/en.sh" | sort -u)
        diff=$(comm -3 <(printf '%s\n' "$ru") <(printf '%s\n' "$en") | tr -d '\t' | head -5)
        [ -z "$diff" ] && _st_ok "ключи ru/en симметричны ($(printf '%s\n' "$ru"|wc -l) шт.)" \
            || _st_bad "ключи ru/en симметричны" "нет расхождений" "$(printf '%s' "$diff"|tr '\n' ' ')"
    else _st_skip "i18n" "нет lang-файлов"; fi

    # ── 3. Версии ядра telemt ──────────────────────────────────────────────
    _st_head "3. Выбор версии ядра (последняя = рекомендованная)"
    if type telemt_target_version >/dev/null 2>&1; then
        ( TELEMT_PINNED_VERSION="3.4.18"
          get_latest_telemt_version() { echo "9.9.9"; }
          read_config_or_default() { echo ""; }
          _st_eq "пустой pref → последняя"      "$(TELEMT_VERSION_PREF=''            telemt_target_version)" "9.9.9"
          _st_eq "recommended → последняя"       "$(TELEMT_VERSION_PREF=recommended   telemt_target_version)" "9.9.9"
          _st_eq "pinned → стабильный пин"       "$(TELEMT_VERSION_PREF=pinned        telemt_target_version)" "3.4.18"
          _st_eq "конкретный тег → он же"        "$(TELEMT_VERSION_PREF=3.4.15        telemt_target_version)" "3.4.15"
          get_latest_telemt_version() { echo ""; }
          _st_eq "API недоступен → фолбэк на пин" "$(TELEMT_VERSION_PREF=latest       telemt_target_version)" "3.4.18" )
    else _st_skip "версии ядра" "telemt.sh не загружен"; fi

    # ── 4. Метрики: окна потерь и трафика ──────────────────────────────────
    _st_head "4. Метрики качества"
    if type metrics_loss_window >/dev/null 2>&1; then
        ( local now; now=$(date +%s)
          MET_LOSS_LOG="$tmp/loss.log"; MET_CF_LOG="$tmp/cf.log"
          printf '%s 10 9\n' "$((now-20))"  > "$MET_LOSS_LOG"    # 10% потерь
          printf '%s 10 5\n' "$((now-200))" >> "$MET_LOSS_LOG"   # +50% в окне 5м
          _st_eq "потери 1м = 10%"  "$(metrics_loss_window 60)"  "10"
          _st_eq "потери 5м = 30%"  "$(metrics_loss_window 300)" "30"
          : > "$MET_LOSS_LOG"
          _st_eq "нет данных → '-'" "$(metrics_loss_window 60)"  "-"
          printf '%s 1048576\n' "$((now-20))" > "$MET_CF_LOG"
          _st_eq "CF-трафик 1м = 1.0МБ" "$(metrics_cf_window 60)" "1.0МБ"
          _st_eq "байты: 0"    "$(_met_bytes_human 0)"          "0Б"
          _st_eq "байты: 3ГБ"  "$(_met_bytes_human 3221225472)" "3.0ГБ" )
        if type metrics_quality_json >/dev/null 2>&1; then
            local qj; qj=$( MET_LOSS_LOG="$tmp/x"; MET_CF_LOG="$tmp/y"; MET_CF_IFACE=nope
                            config_get() { echo off; }
                            metrics_quality_json 2>/dev/null )
            # Логи пустые, поэтому потери обязаны быть «-», а не 0: ноль означал бы,
            # что панель и бот рисуют «потерь нет» там, где данных вообще не было.
            _st_json "quality_json: валидный JSON, без данных loss='-'" "$qj" \
                '.status=="ok" and .loss_1m=="-" and (.traffic|type=="number") and (.loss_max|type=="number") and (.cf_1m|type=="string")'
        fi
    else _st_skip "метрики" "metrics.sh не загружен"; fi

    # ── 5. SYN-фикс: генерация правил (dry-run, без применения) ────────────
    _st_head "5. SYN-фикс (генерация правил)"
    if type _mf_write_rules >/dev/null 2>&1; then
        ( MEKOFIX_DIR="$tmp/mf"; MEKOFIX_RULES="$tmp/mf/rules.sh"; mkdir -p "$tmp/mf"
          local v b
          for v in v3 v2; do for b in nft iptables; do
              _mf_write_rules "$v" "$b" "443,8443"
              if bash -n "$MEKOFIX_RULES" 2>/dev/null; then _st_ok "правила $v/$b — валидный bash"
              else _st_bad "правила $v/$b" "валидный bash" "syntax error"; fi
          done; done
          _mf_write_rules v3 nft 443
          grep -q 'ssh_guard' "$MEKOFIX_RULES" && _st_ok "SSH-guard присутствует" || _st_bad "SSH-guard" "есть" "нет"
          grep -q 'reject with tcp reset' "$MEKOFIX_RULES" && _st_ok "REJECT+RST (не DROP)" || _st_bad "REJECT+RST" "есть" "нет"
          grep -q '54/minute' "$MEKOFIX_RULES" && _st_ok "лимит 54/мин (=1 SYN/1.1с)" || _st_bad "лимит" "54/minute" "нет"
          _mf_write_rules v3 iptables 443
          grep -q 'u32' "$MEKOFIX_RULES" && _st_ok "V3/iptables использует u32-отпечаток" || _st_bad "u32" "есть" "нет" )
        # Функция всегда завершается echo, то есть rc=0 при любом ответе; значим
        # сам номер — мусор вместо порта попадёт в правило «SSH разрешить первым».
        local sp; sp=$(mekofix_ssh_port 2>/dev/null)
        if [[ "$sp" =~ ^[0-9]+$ ]] && [ "$sp" -ge 1 ] && [ "$sp" -le 65535 ]; then _st_ok "ssh_port — номер порта ($sp)"
        else _st_bad "ssh_port" "число 1..65535" "$sp"; fi
        local sj; sj=$(mekofix_status_json 2>/dev/null)
        _st_json "status_json валиден" "$sj" '(.active|type=="boolean") and (.version|type=="string") and (.unit|type=="string")'
    else _st_skip "SYN-фикс" "mekofix.sh не загружен"; fi

    # ── 6. V4 (zapret2): dry-run генерация ────────────────────────────────
    _st_head "6. V4 zapret2 (экспериментальный)"
    if type v4_status_json >/dev/null 2>&1; then
        local vj; vj=$(V4_ROOT="$tmp/v4" v4_status_json 2>/dev/null)
        _st_json "v4_status_json валиден и помечен экспериментальным" "$vj" \
            '.experimental==true and (.installed|type=="boolean") and (.port|type=="number")'
        # V4_ROOT на временную копию: без него проверка шла в живую систему и на
        # сервере, где владелец штатно выбрал режим V4, давала ложный провал.
        ( V4_ROOT="$tmp/v4"; _st_nrc "v4 не активен на пустом V4_ROOT" v4_is_active )
    else _st_skip "V4" "mekofix_v4.sh не загружен"; fi

    # ── 7. Зависимости ────────────────────────────────────────────────────
    _st_head "7. Проверка зависимостей"
    if type deps_report_json >/dev/null 2>&1; then
        local dj; dj=$(DEPS_QUIET=1 deps_report_json core 2>/dev/null)
        # Сверяем связность полей, а не наличие имени "ok": ok=false с пустым
        # missing — это «чего-то не хватает, а чего, не скажем» в боте и панели.
        _st_json "deps_report_json валиден" "$dj" \
            '(.ok|type=="boolean") and (.missing|type=="array") and (.ok or ((.missing|length)>0))'
        # Функция сама всегда return 0, поэтому проверяем её ответ: по нему решается
        # вопрос про PQ-обмен (OpenSSL ≥ 3.5), и пустая строка тихо даст «нет».
        local ov; ov=$(deps_openssl_version 2>/dev/null)
        if command -v openssl >/dev/null 2>&1; then
            [[ "$ov" =~ ^[0-9]+(\.[0-9]+)*$ ]] && _st_ok "deps_openssl_version → версия ($ov)" \
                || _st_bad "deps_openssl_version" "напр. 3.0.13" "$ov"
        else _st_eq "без openssl → пустая версия" "$ov" ""; fi
    else _st_skip "зависимости" "deps.sh не загружен"; fi

    # ── 8. Миграция конфига: ключи сохраняются ────────────────────────────
    _st_head "8. Миграция конфига (сохранность ключей)"
    if type write_normalized_gotelegram_config >/dev/null 2>&1 && command -v jq >/dev/null 2>&1; then
        ( GOTELEGRAM_CONFIG="$tmp/config.json"
          cat > "$GOTELEGRAM_CONFIG" <<'JEOF'
{"version":"2.7.24","mode":"lite","port":443,"secret":"AABBCCDDEEFF00112233445566778899","mask_host":"google.com","language":"ru","installed_at":"2026-01-01T00:00:00+00:00","telemt_version":"3.4.18","warp_egress":"on","warp_key":"1111-2222-3333-4444","mekofix":"v3","preset_id":"4"}
JEOF
          get_language() { echo ru; }
          write_normalized_gotelegram_config lite 443 "AABBCCDDEEFF00112233445566778899" google.com "" "" "" 2>/dev/null
          _st_eq "secret сохранён"        "$(jq -r .secret "$GOTELEGRAM_CONFIG")"        "AABBCCDDEEFF00112233445566778899"
          _st_eq "telemt_version сохранён" "$(jq -r .telemt_version "$GOTELEGRAM_CONFIG")" "3.4.18"
          _st_eq "warp_egress сохранён"   "$(jq -r .warp_egress "$GOTELEGRAM_CONFIG")"   "on"
          _st_eq "warp_key сохранён"      "$(jq -r .warp_key "$GOTELEGRAM_CONFIG")"      "1111-2222-3333-4444"
          _st_eq "mekofix сохранён"       "$(jq -r .mekofix "$GOTELEGRAM_CONFIG")"       "v3"
          _st_eq "preset_id сохранён"     "$(jq -r .preset_id "$GOTELEGRAM_CONFIG")"     "4"
          _st_eq "версия обновлена"       "$(jq -r .version "$GOTELEGRAM_CONFIG")"       "${GOTELEGRAM_VERSION}" )
    else _st_skip "миграция конфига" "нет write_normalized/jq"; fi

    # ── 9. Пресеты анти-DPI ───────────────────────────────────────────────
    _st_head "9. Пресеты анти-DPI"
    if type preset_params >/dev/null 2>&1; then
        local p4; p4=$(preset_params 4 2>/dev/null)
        printf '%s' "$p4" | grep -q 'bbr' && _st_ok "пресет 4 (MEKO style) = bbr" || _st_bad "пресет 4" "bbr" "$p4"
        _st_eq "дефолтный пресет = 4" "${DIAG_DEFAULT_PRESET:-}" "4"
        if type diagnose_preset_cc >/dev/null 2>&1; then
            ( config_get() { echo ""; }
              _st_eq "чистая установка (PREF=4) → bbr" "$(TELEMT_PRESET_PREF=4 diagnose_preset_cc)" "bbr"
              _st_eq "апгрейд (PREF пуст) → cubic"     "$(TELEMT_PRESET_PREF='' diagnose_preset_cc)" "cubic" )
        fi
    else _st_skip "пресеты" "diagnose.sh не загружен"; fi

    # ── 10. WARP-egress против middle-proxy ───────────────────────────────
    # Эта комбинация кладёт прокси целиком: Cloudflare не пропускает порт 8888,
    # telemt не поднимает ни одного ME-writer'а, все клиенты получают
    # «Telegram handshake timeout». Снаружи выглядит как «ключи перестали работать».
    _st_head "10. WARP-egress и middle-proxy"
    local _mp=0 _we=0 _rt=0
    grep -qE '^[[:space:]]*use_middle_proxy[[:space:]]*=[[:space:]]*true' "${TELEMT_CONFIG:-/etc/telemt/config.toml}" 2>/dev/null && _mp=1
    [ "$(config_get warp_egress 2>/dev/null)" = "on" ] && _we=1
    ip rule list 2>/dev/null | grep -q 'lookup 7788' && _rt=1
    if [ "$_mp" = "1" ] && { [ "$_we" = "1" ] || [ "$_rt" = "1" ]; }; then
        # Связка сама по себе не поломка: у части серверов порт ME через Cloudflare проходит.
        # Валим проверку только если он реально не проходит — тогда прокси сейчас лежит.
        if ! type _warp_reach_via_warp >/dev/null 2>&1; then
            _st_skip "порт middle-proxy через WARP" "warp_egress.sh не загружен"
        elif ! ip addr show "${WARP_IFACE:-gtwarp}" >/dev/null 2>&1; then
            _st_skip "порт middle-proxy через WARP" "интерфейс не поднят"
        elif _warp_reach_via_warp; then
            _st_ok "порт middle-proxy (${WARP_ME_PORT:-8888}) проходит через WARP"
        else
            _st_bad "порт middle-proxy через WARP" "проходит" "не проходит — telemt без связи с Telegram"
        fi
    else
        _st_ok "WARP-egress не конфликтует с middle-proxy"
    fi

    # ── итог ──────────────────────────────────────────────────────────────
    local p f k; p=$(_st_count PASS); f=$(_st_count FAIL); k=$(_st_count SKIP)
    printf '\n\033[1m  Итог: \033[32m%s пройдено\033[0m' "$p"
    [ "$f" -gt 0 ] && printf ', \033[31m%s провалено\033[0m' "$f"
    [ "$k" -gt 0 ] && printf ', \033[2m%s пропущено\033[0m' "$k"
    printf '\n\n'
    [ "$f" -eq 0 ]
}
