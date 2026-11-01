#!/usr/bin/env bash
# Заглушка моста `install.sh --action=<имя> --json --k=v …` для тестов веб-панели
# (tests/test_admin_http.py, tests/test_admin_features.py). Своя, отдельная от
# fake_action.sh (MCP) и fake_action_bot.sh (бот): панели нужны другие ответы
# (карточки с рукопожатиями, параметры профиля с областью, каталог бэкапов,
# заглушки not_implemented для агентов), а чужие заглушки менять нельзя.
#
# Ведёт себя как настоящий мост по контракту (docs/CONTRACT.md §0 п.3): весь «UI»
# — в stderr, в stdout мусор ДО последней строки допустим, ПОСЛЕДНЯЯ строка stdout
# — JSON со status. Мусор здесь нарочно: тест проверяет, что панель берёт
# последнюю JSON-строку, а не первую попавшуюся.
#
# Управление через окружение:
#   FAKE_ADMIN_ARGS_FILE  — дописать строку с argv (аргументы через табуляцию);
#   FAKE_ADMIN_ENV_FILE   — дописать «GOAMNEZIA_WHO GOAMNEZIA_CALLER» (кто зовёт мост);
#   FAKE_ADMIN_SLEEP      — поспать N секунд перед ответом (проверка таймаута);
#   FAKE_ADMIN_SLEEP_FILE — путь к файлу с числом секунд: если файл есть, спать
#                           столько же. Отдельно от FAKE_ADMIN_SLEEP потому, что
#                           окружение моста фиксируется при старте панели, а
#                           «задержи ответ вот сейчас» нужно уметь включать из
#                           теста на живом сервере (проверка занятого замка);
#   FAKE_ADMIN_STATUS     — success (по умолчанию) | error — вернуть status=error;
#   FAKE_ADMIN_NO_JSON=1  — завершиться без JSON (проверка ошибки транспорта);
#   FAKE_ADMIN_COUNT_FILE — дописать имя действия при каждом вызове (проверка кэша);
#   FAKE_ADMIN_PAGES      — ok (по умолчанию) | need_domain — отвечает ли page-create
#                           отказом pages_need_domain (режим без домена);
#   FAKE_ADMIN_STDIN_FILE — записать сюда то, что user-attach-key получил в stdin: ключ человека
#                           приходит ТОЛЬКО так (docs/CONTRACT.md § user-attach-key).
#
# «Привязать старый ключ» (user-attach-key): пусто в stdin — no_config; OTHER-SERVER — server_mismatch;
# SOMEONE-ELSE — key_mismatch; не vpn:// и не .conf с [Interface] и PrivateKey — config_unreadable;
# у anna ключи на сервере — already_has_key; иначе привязан.
#
# Разовый адрес сервера в ключе (--endpoint у user-key и share-create, «Ключ на другой
# адрес сервера»): допустимы адреса машины из edge-status (203.0.113.10 — общий,
# 198.51.100.20, 198.51.100.30 — «замолчал») и домен vpn.example.com. Отказы — как у
# моста, rc 2: 0.0.0.0 и 999.* — invalid_ip; 192.0.2.1 — addresses_unknown (машина не
# видит своих адресов); любой другой хост — address_not_here со списком допустимых.
# «Добавить сразу несколько» (users-add-bulk): --count человек, имена <prefix>-01… или случайные bulk_01… с
# подписями «Человек 1»…; FAKE_ADMIN_BULK=partial — сбой на третьем (success с partial и failed), room — отказ
# subnet_full до изменений.
# «Удалить сразу несколько» (users-del-bulk-preview, users-del-bulk): выбор по списку user-list — --state=disabled → bob,
# expired → old, quota → greedy; --prefix=team → team-01…03; --names — те из anna, bob, old, greedy, что названы
# (остальные — missing). У удаления --confirm должен совпасть с числом выбранных (иначе confirm_mismatch с matched);
# FAKE_ADMIN_BULK_DEL=partial — второй и дальше оставлены выключенными (partial, kept, user_del_failed).
# Совместим с bash 3.2 (macOS по умолчанию): без массивов и bash-4 конструкций.

action=""
name=""
profile=""
format=""
component=""
lines=""
agent_id=""
label=""
scope=""
port=""
container=""
apply=""
ttl=""
app=""
platform=""
endpoint=""
count_arg=""
prefix_arg=""
names_arg=""
state_arg=""
confirm_arg=""
brief=""
for a in "$@"; do
    case "$a" in
        --count=*)     count_arg="${a#--count=}" ;;
        --prefix=*)    prefix_arg="${a#--prefix=}" ;;
        --names=*)     names_arg="${a#--names=}" ;;
        --state=*)     state_arg="${a#--state=}" ;;
        --confirm=*)   confirm_arg="${a#--confirm=}" ;;
        --action=*)    action="${a#--action=}" ;;
        --name=*)      name="${a#--name=}" ;;
        --profile=*)   profile="${a#--profile=}" ;;
        --format=*)    format="${a#--format=}" ;;
        --component=*) component="${a#--component=}" ;;
        --lines=*)     lines="${a#--lines=}" ;;
        --id=*)        agent_id="${a#--id=}" ;;
        --label=*)     label="${a#--label=}" ;;
        --scope=*)     scope="${a#--scope=}" ;;
        --port=*)      port="${a#--port=}" ;;
        --container=*) container="${a#--container=}" ;;
        --apply=*)     apply="${a#--apply=}" ;;
        --ttl=*)       ttl="${a#--ttl=}" ;;
        --app=*)       app="${a#--app=}" ;;
        --platform=*)  platform="${a#--platform=}" ;;
        --endpoint=*)  endpoint="${a#--endpoint=}" ;;
        --brief=*)     brief="${a#--brief=}" ;;
    esac
done

if [ -n "${FAKE_ADMIN_ARGS_FILE:-}" ]; then
    { printf '%s\t' "$@"; printf '\n'; } >> "$FAKE_ADMIN_ARGS_FILE"
fi
if [ -n "${FAKE_ADMIN_ENV_FILE:-}" ]; then
    printf '%s %s\n' "${GOAMNEZIA_WHO:-}" "${GOAMNEZIA_CALLER:-}" >> "$FAKE_ADMIN_ENV_FILE"
fi
# Язык ответа на один вызов (L-24): «-» — переменной нет, язык установки.
if [ -n "${FAKE_ADMIN_LANG_FILE:-}" ]; then
    printf '%s %s\n' "$action" "${GOAMNEZIA_CALL_LANG:--}" >> "$FAKE_ADMIN_LANG_FILE"
fi
if [ -n "${FAKE_ADMIN_COUNT_FILE:-}" ]; then
    printf '%s\n' "$action" >> "$FAKE_ADMIN_COUNT_FILE"
fi
# Секретам панели в окружении моста делать нечего — панель обязана их вычистить.
if [ -n "${GOAMNEZIA_ADMIN_TOKEN:-}${GOAMNEZIA_TFA_SINK:-}${BOT_TOKEN:-}" ] && [ -n "${FAKE_ADMIN_ENV_FILE:-}" ]; then
    printf 'LEAK\n' >> "$FAKE_ADMIN_ENV_FILE"
fi

# Шум в stderr: обычные логи и спиннер с ANSI-цветом, как у настоящего install.sh.
echo "fake_action_admin: обработка действия '${action}'" >&2
printf '\033[32m[ok]\033[0m spinner noise\r\n' >&2

if [ -n "${FAKE_ADMIN_SLEEP:-}" ]; then
    sleep "$FAKE_ADMIN_SLEEP"
fi
if [ -n "${FAKE_ADMIN_SLEEP_FILE:-}" ] && [ -f "$FAKE_ADMIN_SLEEP_FILE" ]; then
    sleep "$(cat "$FAKE_ADMIN_SLEEP_FILE" 2>/dev/null || echo 0)"
fi

# Мусорные строки в stdout ДО JSON — в том числе «чужой» JSON без status и
# объект со status в середине вывода, который брать нельзя.
echo "GARBAGE-LINE-FROM-BRIDGE: this is not json"
echo '{"note": "intermediate object without status"}'

# Ключ человека у user-attach-key — только из stdin; остальным действиям панель stdin закрывает.
material=""
if [ "$action" = "user-attach-key" ]; then
    material=$(cat)
    if [ -n "${FAKE_ADMIN_STDIN_FILE:-}" ]; then
        printf '%s' "$material" > "$FAKE_ADMIN_STDIN_FILE"
    fi
fi

if [ "${FAKE_ADMIN_NO_JSON:-0}" = "1" ]; then
    echo "fake_action_admin: авария без JSON" >&2
    # Упавший мост печатает присланный ключ: панель обязана не переносить такой вывод в журнал
    [ -n "$material" ] && printf 'CRASH-DUMP %s\n' "$material" && printf 'CRASH-DUMP %s\n' "$material" >&2
    exit 3
fi
# Устаревший объект со status ПЕРЕД настоящим ответом: панель обязана взять
# последний, а не первый попавшийся (печатается только когда ответ будет).
echo '{"status":"error","message":"stale intermediate object","code":"must_not_be_used"}'

if [ "${FAKE_ADMIN_STATUS:-success}" = "error" ]; then
    printf '{"status":"error","message":"forced error from fixture","code":"forced_error","action":"%s"}\n' "$action"
    exit 1
fi

# Валидный PNG 1×1 (70 байт): проверяем настоящую картинку, а не «какой-то base64».
PNG='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAX+XDSwAAAABJRU5ErkJggg=='
# Рукопожатие «сейчас» — чтобы онлайн (< 180 с) считался от реального времени теста.
NOW=$(date +%s)
RECENT=$((NOW - 30))
OLD=$((NOW - 7200))

# Адреса машины для --endpoint (те же, что в key_choices у edge-status).
EDGE_HOSTS="203.0.113.10 198.51.100.20 198.51.100.30 vpn.example.com"
# Проверка разового адреса: пусто или допустимый — молча дальше; иначе отказ моста и rc 2.
endpoint_guard() {
    [ -n "$endpoint" ] || return 0
    case " $EDGE_HOSTS " in *" $endpoint "*) return 0 ;; esac
    case "$endpoint" in
        0.0.0.0|999.*)
            printf '{"status":"error","message":"Негодный адрес IPv4: %s","code":"invalid_ip","action":"%s"}\n' "$endpoint" "$action" ;;
        192.0.2.1)
            printf '{"status":"error","message":"Машина не видит своих внешних адресов: адрес %s проверить нельзя.","code":"addresses_unknown","action":"%s"}\n' "$endpoint" "$action" ;;
        *)
            printf '{"status":"error","message":"Адреса %s у этого сервера нет. Допустимо: %s","code":"address_not_here","action":"%s"}\n' "$endpoint" "$EDGE_HOSTS" "$action" ;;
    esac
    exit 2
}

case "$action" in
    status)
        # Полный status (без --brief) несёт шаги «кому что сделать» после перестановки (lines_check.reline.steps, N-3):
        # их берёт окно согласия панели через GET /api/profiles/<p>/reline-preview. Краткий — без lines_check, как раньше.
        lc=""
        [ "$brief" = on ] || lc=',"lines_check":{"no_20":false,"shared":[],"missing":[],"reline":{"profile":"awg3","from":"3.1","line":"2.0","port":39001,"title":"AmneziaWG 3.1 · порт 39001","steps":[{"kind":"page","text":"Со страницей подключения (anna): открыть страницу заново и импортировать новый ключ."}]}}'
        printf '{"status":"success","message":"ok","version":"0.1.0","installed":true,"mode":"lite","domain":"","server_ip":"203.0.113.10","endpoint_host":"","users":3,"profiles":[{"profile":"awg2","container":"amnezia-awg2","state":"running","port":443,"line":"2.0","peers":2,"image_tag":"3.1.20260828","image_digest":"sha256:0","enabled":true},{"profile":"awg3","container":"amnezia-awg3","state":"exited","port":39001,"line":"3.1","peers":1,"image_tag":"3.1.20260828","image_digest":"sha256:0","enabled":true}]%s}\n' "$lc"
        ;;
    profiles-status)
        echo '{"status":"success","message":"ok","profiles":[{"profile":"awg2","container":"amnezia-awg2","state":"running","port":443,"line":"2.0","peers":2,"image_tag":"3.1.20260828","image_digest":"sha256:0","enabled":true},{"profile":"awg3","container":"amnezia-awg3","state":"exited","port":39001,"line":"3.1","peers":1,"image_tag":"3.1.20260828","image_digest":"sha256:0","enabled":true}]}'
        ;;
    user-list)
        # Приватных ключей и psk здесь нет — как у users_list (ga_users_public_filter).
        echo '{"status":"success","message":"ok","users":[{"name":"anna","label":"Анна","created":"2026-09-01T10:00:00Z","enabled":true,"quota_bytes":0,"expires":"","traffic":{"rx":1048576,"tx":2097152,"last_rx":0,"last_tx":0,"updated":""},"profiles":["awg2","awg3"],"peers":{"awg2":{"ip":"10.8.2.2","pubkey":"PUBANNA2","created":"2026-09-01T10:00:00Z","active":true,"last_handshake":0,"rx":0,"tx":0},"awg3":{"ip":"10.8.3.2","pubkey":"PUBANNA3","created":"2026-09-01T10:00:00Z","active":true,"last_handshake":0,"rx":0,"tx":0}}},{"name":"bob","label":"Bob","created":"2026-09-01T10:00:00Z","enabled":false,"quota_bytes":0,"expires":"","traffic":{"rx":0,"tx":0,"last_rx":0,"last_tx":0,"updated":""},"profiles":["awg2"],"peers":{"awg2":{"ip":"10.8.2.3","pubkey":"PUBBOB2","created":"2026-09-01T10:00:00Z","active":false,"last_handshake":0,"rx":0,"tx":0}}},{"name":"old","label":"Old","created":"2026-01-01T10:00:00Z","enabled":true,"quota_bytes":0,"expires":"2020-01-01T00:00:00Z","traffic":{"rx":0,"tx":0,"last_rx":0,"last_tx":0,"updated":""},"profiles":[],"peers":{}},{"name":"greedy","label":"Greedy","created":"2026-01-01T10:00:00Z","enabled":true,"quota_bytes":1024,"expires":"","traffic":{"rx":1024,"tx":1024,"last_rx":0,"last_tx":0,"updated":""},"profiles":["awg2"],"peers":{"awg2":{"ip":"10.8.2.4","pubkey":"PUBGREEDY2","created":"2026-01-01T10:00:00Z","active":true,"last_handshake":0,"rx":0,"tx":0}}}]}'
        ;;
    user-show)
        if [ "$name" = "anna" ]; then
            echo '{"status":"success","message":"ok","name":"anna","key_host":"203.0.113.10","key_host_gone":false,"user":{"name":"anna","label":"Анна","created":"2026-09-01T10:00:00Z","enabled":true,"quota_bytes":53687091200,"expires":"2027-01-01T00:00:00Z","traffic":{"rx":1048576,"tx":2097152,"last_rx":0,"last_tx":0,"updated":""},"profiles":["awg2","awg3"],"peers":{"awg2":{"ip":"10.8.2.2","pubkey":"PUBANNA2","created":"2026-09-01T10:00:00Z","active":true,"last_handshake":0,"rx":0,"tx":0},"awg3":{"ip":"10.8.3.2","pubkey":"PUBANNA3","created":"2026-09-01T10:00:00Z","active":true,"last_handshake":1700000000,"rx":0,"tx":0}}}}'
        else
            printf '{"status":"error","message":"Пользователь %s не найден","code":"user_not_found","action":"user-show"}\n' "$name"
            exit 3
        fi
        ;;
    user-key)
        endpoint_guard
        # Хост в Endpoint: разовый адрес побеждает общий; порт — порт профиля.
        key_host="${endpoint:-203.0.113.10}"
        key_port=443; [ "$profile" = "awg3" ] && key_port=39001
        override=false; vpn="vpn://FAKEKEY"
        if [ -n "$endpoint" ]; then override=true; vpn="vpn://FAKEKEY-${endpoint}"; fi
        # file_name (Н-2): anna — ядро старше поля; kim — имя от ядра; junk — негодное имя (панель его не берёт).
        fn_field=""
        [ "$name" = "kim" ] && fn_field=',"file_name":"kim-2_0.conf"'
        [ "$name" = "junk" ] && fn_field=',"file_name":"../x\"y.conf"'
        # Приватный ключ — единственное место, где секрет допустим в ответе (§0.7).
        printf '{"status":"success","message":"ok","name":"%s","profile":"%s","line":"2.0","min_client":"4.8.12.7","conf":"[Interface]\\nPrivateKey = FAKE-PRIVATE-KEY-DO-NOT-LOG\\nAddress = 10.8.2.2/32\\n\\n[Peer]\\nEndpoint = %s:%s\\n","vpn":"%s","qr_conf_png_b64":"%s","qr_vpn_png_b64":["%s","%s"],"format":"%s","key_host":"%s","key_endpoint":"%s:%s","key_host_override":%s%s}\n' \
            "$name" "$profile" "$key_host" "$key_port" "$vpn" "$PNG" "$PNG" "$PNG" "$format" "$key_host" "$key_host" "$key_port" "$override" "$fn_field"
        ;;
    traffic)
        printf '{"status":"success","message":"ok","profiles":[{"profile":"awg2","peers":[{"pubkey":"PUBANNA2","endpoint":"198.51.100.7:51820","allowed_ips":"10.8.2.2/32","latest_handshake_ts":%s,"rx":10,"tx":20},{"pubkey":"PUBGREEDY2","endpoint":"198.51.100.8:51820","allowed_ips":"10.8.2.4/32","latest_handshake_ts":%s,"rx":5,"tx":5}]},{"profile":"awg3","peers":[{"pubkey":"PUBANNA3","endpoint":"","allowed_ips":"10.8.3.2/32","latest_handshake_ts":0,"rx":0,"tx":0}]}],"users":[{"name":"anna","label":"Анна","enabled":true,"quota_bytes":0,"expires":"","traffic":{"rx":1048576,"tx":2097152},"profiles":["awg2","awg3"],"peers":{"awg2":{"ip":"10.8.2.2","pubkey":"PUBANNA2","active":true},"awg3":{"ip":"10.8.3.2","pubkey":"PUBANNA3","active":true}}},{"name":"greedy","label":"Greedy","enabled":true,"quota_bytes":1024,"expires":"","traffic":{"rx":1024,"tx":1024},"profiles":["awg2"],"peers":{"awg2":{"ip":"10.8.2.4","pubkey":"PUBGREEDY2","active":true}}}]}\n' "$RECENT" "$OLD"
        ;;
    edge-status)
        # Три внешних адреса: общий принимает, второй без ключей, третий «замолчал».
        # key_choices — ровно то, что примет --endpoint: default первым, домены, IP, dark в конце.
        echo '{"status":"success","message":"ok","count":3,"domain_endpoint":false,"keys_host":"203.0.113.10","ips":[{"ip":"203.0.113.10","packets_15m":1200,"packets_1h":5400,"packets_day":98000,"users":3,"verdict":"ok"},{"ip":"198.51.100.20","packets_15m":0,"packets_1h":0,"packets_day":0,"users":0,"verdict":"unused"},{"ip":"198.51.100.30","packets_15m":0,"packets_1h":0,"packets_day":4200,"users":1,"verdict":"dark"}],"key_choices":[{"host":"203.0.113.10","kind":"ip","default":true,"verdict":"ok","users":3,"in_dns":null},{"host":"vpn.example.com","kind":"domain","default":false,"verdict":null,"users":null,"in_dns":null},{"host":"198.51.100.20","kind":"ip","default":false,"verdict":"unused","users":0,"in_dns":null},{"host":"198.51.100.30","kind":"ip","default":false,"verdict":"dark","users":1,"in_dns":null}]}'
        ;;
    logs-tail)
        printf '{"status":"success","message":"ok","component":"%s","lines":%s,"log":"line one\\nline two <b>not html</b>\\n"}\n' "$component" "${lines:-100}"
        ;;
    quality)
        echo '{"status":"success","message":"ok","quality":{"docker":true,"ip_forward":"1","network":true,"disk_free_mb":12345,"ports":[{"profile":"awg2","port":"443","free":false}]}}'
        ;;
    backup-list)
        echo '{"status":"success","message":"ok","backups":[{"name":"goamnezia-backup-20260908_120000.tar.gz","size":1024,"created":"2026-09-08T12:00:00Z"},{"name":"../evil.tar.gz","size":1,"created":""},{"name":"goamnezia-backup-20260907_120000.tar.gz","size":1024,"created":"2026-09-07T12:00:00Z"}]}'
        ;;
    backup-create)
        echo '{"status":"success","message":"Бэкап создан","file":"goamnezia-backup-20260908_130000.tar.gz","path":"/opt/goamnezia/backups/goamnezia-backup-20260908_130000.tar.gz"}'
        ;;
    profile-params)
        printf '{"status":"success","message":"ok","profile":"%s","params":{"Jc":"5","Jmin":"10","Jmax":"50","S1":"30","S2":"40","S3":"20","S4":"25","H1":"1000-2000","H2":"3000-4000","H3":"5000-6000","H4":"7000-8000","I1":"","PersistentKeepalive":"25","MTU":"1280"}}\n' "$profile"
        ;;
    profile-set-param)
        printf '{"status":"success","message":"ok","profile":"%s","reissue_required":true,"result":{"scope":"server"}}\n' "$profile"
        ;;
    import-scan)
        echo '{"status":"success","message":"ok","containers":[{"container":"amnezia-awg2","port":443,"proto":"udp","kind":"awg","profile":"awg2","managed":true,"suggested_profile":"awg2","adoptable":false,"reason":"managed","line":"2.0","subnet":"10.8.2.0/24","listen_port":443,"iface":"awg0","server_pubkey":"SRVPUB2","image":"amnezia-awg2","peers":1,"clients":[{"pubkey":"PUBANNA2","name":"Анна","ip":"10.8.2.2","created":"2026-09-01T10:00:00Z","known":true}]},{"container":"amnezia-awg","port":38000,"proto":"udp","kind":"legacy","profile":"","managed":false,"suggested_profile":"awg1","adoptable":true,"reason":"","line":"1.0","subnet":"10.8.5.0/24","listen_port":38000,"iface":"wg0","server_pubkey":"SRVPUB1","image":"amnezia-awg","peers":1,"clients":[{"pubkey":"PUBKEENETIC","name":"Keenetic дома","ip":"10.8.5.1","created":"2025-11-11T11:11:11Z","known":false}]}],"adoptable":["amnezia-awg"]}'
        ;;
    import-adopt)
        if [ "$container" = "amnezia-awg" ]; then
            echo '{"status":"success","message":"Контейнер amnezia-awg подхвачен как профиль awg1.","profile":"awg1","container":"amnezia-awg","line":"1.0","port":38000,"subnet":"10.8.5.0/24","iface":"wg0","image_tag":"latest","image_digest":"","users_added":1,"users":["Keenetic"],"peers":1,"hint":"У импортированных пользователей нет приватных ключей."}'
        elif [ "$container" = "amnezia-xray" ]; then
            printf '{"status":"error","message":"Контейнер %s не поддерживается goAmnezia.","code":"unsupported_container"}\n' "$container"
            exit 1
        else
            printf '{"status":"error","message":"Контейнер %s подхватить нельзя.","code":"not_adoptable"}\n' "$container"
            exit 1
        fi
        ;;
    import-reconcile)
        printf '{"status":"success","message":"Профиль %s: контейнер разошёлся с users.json.","profile":"%s","container":"amnezia-awg2","in_sync":false,"new":[{"pubkey":"PUBNEW","ip":"10.8.2.7","name":"Laptop"}],"gone":[],"applied":%s,"added":%s,"deactivated":0,"users":[]}\n' "$profile" "$profile" "$([ "$apply" = on ] && echo true || echo false)" "$([ "$apply" = on ] && echo 1 || echo 0)"
        ;;
    upstream-check)
        echo '{"status":"success","message":"Есть обновление.","checked":"2026-09-09T05:10:00Z","sources":{"github":"ok","dockerhub":"ok"},"client_release":{"tag":"5.0.3.0","published":"2026-09-20T10:00:00Z","prerelease":true,"url":"https://github.com/amnezia-vpn/amnezia-client/releases/tag/5.0.3.0","current":"5.0.2.1","update":true},"images":{"amneziawg-go":{"repo":"amneziavpn/amneziawg-go","pinned_tag":"3.1.20260828","latest_tag":"3.2.20260915","update":true}},"scripts":{"awg":{"tag":"5.0.3.0","checked":true,"changed":["Dockerfile","template.conf"],"unchanged":[],"missing":[]}},"placeholders":{"awg/template.conf":{"new":["CONTENT_PADDING_MAX"],"removed":[]}},"unsupported":[{"file":"awg/template.conf","name":"CONTENT_PADDING_MAX"}],"profiles":[{"profile":"awg2","container":"amnezia-awg2","image_key":"amneziawg-go","current_tag":"3.1.20260828","current_digest":"sha256:cbafc02b","available_tag":"3.2.20260915","available_digest":"sha256:51a5cfd1","update":true,"previous_tag":"","previous_digest":"","rollback_available":false},{"profile":"awg3","container":"amnezia-awg3","image_key":"amneziawg-go","current_tag":"3.2.20260915","current_digest":"sha256:51a5cfd1","available_tag":"3.2.20260915","available_digest":"sha256:51a5cfd1","update":false,"previous_tag":"3.1.20260828","previous_digest":"sha256:cbafc02b","rollback_available":true}],"updates_available":true,"hint":"Есть обновление. Оно ставится только по кнопке."}'
        ;;
    upstream-update)
        printf '{"status":"success","message":"Ядро профиля %s обновлено до 3.2.20260915.","profile":"%s","container":"amnezia-%s","image_tag":"3.2.20260915","image_digest":"sha256:51a5cfd1","previous_tag":"3.1.20260828","previous_digest":"sha256:cbafc02b","client_release":"5.0.3.0","scripts":{"tag":"5.0.3.0","changed":["Dockerfile","template.conf"],"unsupported":[{"file":"awg/template.conf","name":"CONTENT_PADDING_MAX"}]},"backup":"goamnezia_backup_20260909_051000.tar.gz","rolled_back":false}\n' "$profile" "$profile" "$profile"
        ;;
    upstream-rollback)
        printf '{"status":"success","message":"Профиль %s откачен на образ 3.1.20260828.","profile":"%s","container":"amnezia-%s","image_tag":"3.1.20260828","image_digest":"sha256:cbafc02b","rolled_back_from_tag":"3.2.20260915","scripts_restored":true}\n' "$profile" "$profile" "$profile"
        ;;
    core-update)
        printf '{"status":"success","message":"Контейнер профиля %s пересоздан на образе 3.1.20260828.","profile":"%s","container":"amnezia-%s","image_tag":"3.1.20260828","image_digest":"sha256:cbafc02b","old_tag":"3.1.20260828","old_digest":"sha256:cbafc02b","repair":true}\n' "$profile" "$profile" "$profile"
        ;;
    site-check)
        echo '{"status":"success","message":"ok","site":{"domain":"","mode":"lite","server_ip":"203.0.113.10","dns_ok":false,"https_ok":false,"nginx_active":false,"index_present":false,"cert_expires":"","cert_days_left":null,"cert_ok":false,"verdict":"not_pro","dry_run":true},"verdict":"not_pro"}'
        ;;
    change-template)
        echo '{"status":"success","message":"Шаблон установлен","template":{"template_id":"h5up_aerial","name":"Aerial","deployed":true}}'
        ;;
    miniapp-status)
        # Панель на домене в этой заглушке — «в следующей версии»: на ней держится
        # проверка кода not_implemented → HTTP 501.
        echo '{"status":"error","message":"Действие появится в следующей версии.","code":"not_implemented"}'
        exit 1
        ;;
    # ── Доступы агентов к MCP по HTTP (lib/agents.sh): список без секретов, адрес — только у add/show/rotate ──
    agent-list)
        echo '{"status":"success","message":"ok","agents":[{"id":"a-1a2b","label":"Claude Code на ноутбуке","scope":"write","enabled":true,"created":"2026-09-01T10:00:00Z","expires":"2026-12-01T10:00:00Z","port":0,"allow_ips":["203.0.113.0/24"],"last_used":"2026-09-08T09:00:00Z","last_ip":"203.0.113.5","calls":12,"expired":false,"state":"active"},{"id":"a-3c4d","label":"Только чтение","scope":"read","enabled":false,"created":"2026-09-02T10:00:00Z","expires":"","port":0,"allow_ips":[],"last_used":"","last_ip":"","calls":0,"expired":false,"state":"disabled"}],"transport":{"enabled":true,"mode":"lite_site","listen":"nginx","bind":"127.0.0.1","port":1986,"tls":false,"http_port":1986,"public_port":0,"domain":"","server_ip":"203.0.113.10","base_url":"https://203.0.113.10","via":"ip","cert_fingerprint_sha256":"AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99","nginx_location":true,"unit":"goamnezia-mcp-http.service","unit_state":"active","reachable":true,"hint":"","agents":2,"agents_active":1,"agents_with_port":0}}'
        ;;
    agent-show|agent-rotate)
        if [ "$agent_id" = "a-1a2b" ] || [ "$agent_id" = "a-3c4d" ]; then
            printf '{"status":"success","message":"ok","id":"%s","label":"Claude Code на ноутбуке","scope":"write","enabled":true,"expires":"","port":0,"allow_ips":[],"last_used":"","last_ip":"","calls":0,"url":"https://203.0.113.10/a-FAKESECRETFAKESECRETFAKESECRETFAKESECRET01/mcp","via":"ip","cert_fingerprint_sha256":"AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99","require_token":true,"token":"FAKETOKENFAKETOKENFAKETOKENFAKETOKEN012345","pin_ip":false,"pinned_ip":"","connect":"claude mcp add --transport http goamnezia https://203.0.113.10/a-FAKESECRETFAKESECRETFAKESECRETFAKESECRET01/mcp --header Authorization:Bearer-FAKETOKENFAKETOKENFAKETOKENFAKETOKEN012345","transport_enabled":true,"reachable":true,"hint":"","warning":"Адрес — это ключ."}\n' "$agent_id"
        else
            printf '{"status":"error","message":"Доступ %s не найден.","code":"agent_not_found"}\n' "$agent_id"
            exit 1
        fi
        ;;
    agent-add)
        printf '{"status":"success","message":"Доступ a-9f9f выдан","id":"a-9f9f","label":"%s","scope":"%s","enabled":true,"expires":"","port":0,"allow_ips":[],"last_used":"","last_ip":"","calls":0,"url":"https://203.0.113.10/a-NEWSECRETNEWSECRETNEWSECRETNEWSECRETNEWSEC/mcp","via":"ip","cert_fingerprint_sha256":"AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99","transport_enabled":true,"reachable":true,"hint":"","warning":"Адрес — это ключ."}\n' "$label" "${scope:-write}"
        ;;
    mcp-http-status)
        echo '{"status":"success","message":"ok","enabled":true,"mode":"lite_site","listen":"nginx","bind":"127.0.0.1","port":1986,"tls":false,"http_port":1986,"public_port":0,"domain":"","server_ip":"203.0.113.10","base_url":"https://203.0.113.10","via":"ip","cert_fingerprint_sha256":"AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99","nginx_location":true,"unit":"goamnezia-mcp-http.service","unit_state":"active","reachable":true,"hint":"","agents":2,"agents_active":1,"agents_with_port":0}'
        ;;
    mcp-http-on)
        # С портом — как в Lite за закрытым файрволом: порт свободен, но закрыт локально (closed_local → 409 у панели)
        if [ -n "$port" ]; then
            printf '{"status":"error","message":"Порт %s закрыт локальным файрволом","code":"closed_local","port":%s,"proto":"tcp","hint":"ufw allow %s/tcp"}\n' "$port" "$port" "$port"
            exit 1
        fi
        echo '{"status":"success","message":"HTTP-транспорт MCP включён.","enabled":true,"mode":"lite_site","listen":"nginx","bind":"127.0.0.1","port":1986,"tls":false,"http_port":1986,"public_port":0,"domain":"","server_ip":"203.0.113.10","base_url":"https://203.0.113.10","via":"ip","cert_fingerprint_sha256":"AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99","nginx_location":true,"unit":"goamnezia-mcp-http.service","unit_state":"active","reachable":true,"hint":"","agents":2,"agents_active":1,"agents_with_port":0}'
        ;;
    mcp-http-off)
        echo '{"status":"success","message":"HTTP-транспорт MCP выключен.","enabled":false,"mode":"lite_site","listen":"nginx","bind":"127.0.0.1","port":1986,"tls":false,"http_port":1986,"public_port":0,"domain":"","server_ip":"203.0.113.10","base_url":"https://203.0.113.10","via":"ip","cert_fingerprint_sha256":"","nginx_location":false,"unit":"goamnezia-mcp-http.service","unit_state":"missing","reachable":false,"hint":"HTTP-транспорт выключен","agents":2,"agents_active":1,"agents_with_port":0}'
        ;;
    # ── Временные ссылки на файл (lib/share.sh, docs/SHARE.md §3) ──
    share-create)
        endpoint_guard
        # Файл с разовым адресом — <имя>-<профиль>-<host>.conf, иначе как было.
        file="${name}-${profile}.conf"
        [ -n "$endpoint" ] && file="${name}-${profile}-${endpoint}.conf"
        # Секретный адрес — единственное место, где он вообще появляется.
        printf '{"status":"success","message":"ok","id":"s-4b1e","url":"https://203.0.113.10:38472/d-FAKESECRETFAKESECRETFAKESECRETFAKESECRET1/%s","path":"/d-FAKESECRETFAKESECRETFAKESECRETFAKESECRET1/%s","port":38472,"host":"203.0.113.10","via":"ip","format":"%s","file_name":"%s","endpoint":"%s","size":712,"created":"2026-09-10T18:41:07Z","expires":"2026-09-10T18:46:07Z","ttl":300,"max_downloads":0,"cert":"selfsigned","fingerprint":"3A:9F:12:B7:04:CE:88:1D:6E:52:AB:77:30:F4:9C:21:55:E0:8B:46:D2:7A:13:C9:60:EF:35:A8:74:1B:5D:92","selfsigned":true}\n' \
            "$file" "$file" "${format:-conf}" "$file" "$endpoint"
        ;;
    share-list)
        # url/path здесь НЕ должно быть: список видит любой, кто открыл
        # страницу. Поля нарочно подложены — панель обязана их выкинуть.
        echo '{"status":"success","message":"ok","shares":[{"id":"s-4b1e","name":"anna","profile":"awg2","format":"conf","file_name":"anna-awg2.conf","endpoint":"","port":38472,"host":"203.0.113.10","via":"ip","size":712,"created":"2026-09-10T18:41:07Z","expires":"2026-09-10T18:46:07Z","ttl":300,"max_downloads":0,"cert":"selfsigned","downloads":1,"seconds_left":214,"url":"https://203.0.113.10:38472/d-MUSTNOTLEAK/anna-awg2.conf","path":"/d-MUSTNOTLEAK/anna-awg2.conf"}],"count":1}'
        ;;
    share-revoke)
        printf '{"status":"success","message":"Раздача снята","id":"%s","reason":"revoked"}\n' "$agent_id"
        ;;
    share-revoke-all)
        echo '{"status":"success","message":"Все раздачи сняты","revoked":1}'
        ;;
    # ── Предполётная проверка портов (lib/preflight.sh) ──
    preflight)
        # 443/tcp занят чужим nginx (юнит + продукт), 80/tcp свободен,
        # 443/udp занят нашим профилем: доступен только Lite.
        echo '{"status":"success","message":"ok","modes":{"lite":{"available":true},"lite_site":{"available":false,"reasons":[{"port":443,"proto":"tcp","state":"busy","process":"nginx","pid":812,"unit":"nginx.service","container":"","product":"gotelegram","firewall_source":"ufw","message":"443/tcp занят: nginx (PID 812, юнит nginx.service, похоже на goTelegram)"}]},"pro":{"available":false,"reasons":[{"port":443,"proto":"tcp","state":"busy","process":"nginx","pid":812,"unit":"nginx.service","container":"","product":"gotelegram","firewall_source":"ufw","message":"443/tcp занят: nginx (PID 812, юнит nginx.service, похоже на goTelegram)"}]},"warnings":[{"code":"udp443_busy","port":443,"proto":"udp","process":"docker-proxy","pid":900,"unit":"docker.service","container":"amnezia-awg2","product":"goamnezia","message":"443/udp занят нашим профилем awg2"}],"ports":{"tcp80":{"state":"free","pid":null,"process":"","unit":"","container":"","product":"unknown","ours":false,"firewall_source":"ufw"},"tcp443":{"state":"busy","pid":812,"process":"nginx","unit":"nginx.service","container":"","product":"gotelegram","ours":false,"firewall_source":"ufw"},"udp443":{"state":"busy","pid":900,"process":"docker-proxy","unit":"docker.service","container":"amnezia-awg2","product":"goamnezia","ours":true,"firewall_source":"ufw"}},"hints":{"free_cmd":"systemctl disable --now nginx.service","open_cmd":""},"dry_run":true},"lite_site_available":false,"pro_available":false,"hint":"systemctl disable --now nginx.service"}'
        ;;
    logs-status)
        # Проценты — голые числа, как их печатает logb_status (без знака «%»),
        # и последняя строка «free» — свободное место на диске, а не занятое.
        # Имена — участки диска (area), а не источники логов из logs-tail.
        echo '{"status":"success","message":"ok","areas":[{"area":"goamnezia","used_mb":"12","budget_mb":"200","percent":"6"},{"area":"journal","used_mb":"180","budget_mb":"200","percent":"90"},{"area":"docker","used_mb":"340","budget_mb":"200","percent":"170"},{"area":"free","used_mb":"12345","budget_mb":"-","percent":"-"}]}'
        ;;
    profile-port)
        # Занятый UDP/443 — код port_taken, как у настоящего диспетчера.
        for a in "$@"; do
            case "$a" in --port=443) [ "$profile" = "awg3" ] && { echo '{"status":"error","message":"UDP/443 уже у awg2","code":"port_taken"}'; exit 1; } ;; esac
        done
        printf '{"status":"success","message":"ok: %s","profile":"%s"}\n' "$action" "$profile"
        ;;
    # ── Страница подключения и зеркало сборок (docs/CLIENTS.md §9) ──
    # Поля — по таблице действий моста: ядро пишется параллельно, заглушка —
    # договор интерфейсов с ним. Адрес вымышленный (example.com), секрет — 43
    # знака base64url, как у настоящего модуля.
    clients-list)
        echo '{"status":"success","message":"ok","checked":"2026-09-17","apps":[{"id":"amneziavpn","name":"AmneziaVPN","developer":"Amnezia","official":true,"license":"GPL-3.0","platforms":{"ios":{"links":[{"kind":"appstore","url":"https://apps.apple.com/app/id1600529900"}],"support":{"1.0":"yes","2.0":"yes","3.1":"5.0.1.5"},"note":"not in the Russian App Store"},"android":{"links":[{"kind":"googleplay","url":"https://play.google.com/store/apps/details?id=org.amnezia.vpn"}],"support":{"1.0":"yes","2.0":"yes","3.1":"5.0.1.5"},"note":""},"windows":{"links":[{"kind":"github","url":"https://github.com/amnezia-vpn/amnezia-client/releases"}],"support":{"1.0":"yes","2.0":"yes","3.1":"5.0.1.5"},"note":""}},"import":["vpn","qr-vpn"],"mirror":{"repo":"amnezia-vpn/amnezia-client","assets":{"android":"AmneziaVPN_.*\\.apk$","windows":"AmneziaVPN_.*_x64\\.exe$","macos":"AmneziaVPN_.*\\.dmg$","linux":"AmneziaVPN_.*\\.tar\\.zip$"},"license":"GPL-3.0"}},{"id":"wgtunnel","name":"WG Tunnel","developer":"Zane Schepke","official":false,"license":"MIT","platforms":{"android":{"links":[{"kind":"github","url":"https://github.com/wgtunnel/android/releases"}],"support":{"1.0":"yes","2.0":"yes","3.1":"5.6.0"},"note":""}},"import":["conf","qr-conf"],"mirror":{"repo":"wgtunnel/android","assets":{"android":"wgtunnel-.*\\.apk$"},"license":"MIT"}},{"id":"defaultvpn","name":"DefaultVPN","developer":"Amnezia","official":true,"license":"closed","platforms":{"ios":{"links":[{"kind":"appstore","url":"https://apps.apple.com/app/id6744725017"}],"support":{"1.0":"yes","2.0":"yes","3.1":"2.0.1"},"note":""}},"import":["vpn","qr-vpn"],"mirror":null}]}'
        ;;
    page-create)
        if [ "${FAKE_ADMIN_PAGES:-ok}" = "need_domain" ]; then
            echo '{"status":"error","message":"Страница подключения живёт только с сайтом на домене и настоящим сертификатом.","code":"pages_need_domain"}'
            exit 1
        fi
        case "$ttl" in
            forever|[1-9]h|[1-9][0-9]h|[1-9][0-9][0-9]h|[1-9]d|[1-9][0-9]d|[1-9][0-9][0-9]d) ;;
            *) printf '{"status":"error","message":"Недопустимый срок: %s","code":"invalid_ttl"}\n' "$ttl"; exit 1 ;;
        esac
        forever=false; expires="2026-09-18T18:41:07Z"
        [ "$ttl" = "forever" ] && { forever=true; expires=""; }
        printf '{"status":"success","message":"ok","id":"p-7f3a","url":"https://vpn.example.com/p-FAKESECRETFAKESECRETFAKESECRETFAKESECRET012/","user":"%s","created":"2026-09-17T18:41:07Z","expires":"%s","forever":%s,"profiles":["awg2","awg3"],"mirror":true}\n' \
            "$name" "$expires" "$forever"
        ;;
    page-list)
        # url/path здесь быть НЕ должно (§9: список без адреса). Поля нарочно
        # подложены — панель обязана их выкинуть, как у share-list.
        anna='{"id":"p-7f3a","user":"anna","created":"2026-09-17T18:41:07Z","expires":"2026-09-18T18:41:07Z","forever":false,"visits":3,"last_visit":"2026-09-17T19:02:11Z","profiles":["awg2","awg3"],"mirror":true,"url":"https://vpn.example.com/p-MUSTNOTLEAK/","path":"/p-MUSTNOTLEAK/"},{"id":"p-9c1d","user":"anna","created":"2026-09-10T08:00:00Z","expires":"","forever":true,"visits":0,"last_visit":"","profiles":["awg2"],"mirror":false}'
        bob='{"id":"p-0bad","user":"bob","created":"2026-09-16T12:00:00Z","expires":"2026-10-16T12:00:00Z","forever":false,"visits":1,"last_visit":"2026-09-16T12:30:00Z","profiles":["awg2"],"mirror":true}'
        case "$name" in
            anna) rows="$anna" ;;
            bob)  rows="$bob" ;;
            "")   rows="$anna,$bob" ;;
            *)    rows="" ;;
        esac
        printf '{"status":"success","message":"ok","pages":[%s]}\n' "$rows"
        ;;
    page-url)
        case "$agent_id" in
            p-7f3a|p-9c1d|p-0bad)
                printf '{"status":"success","message":"ok","id":"%s","url":"https://vpn.example.com/p-FAKESECRETFAKESECRETFAKESECRETFAKESECRET012/"}\n' "$agent_id" ;;
            *) printf '{"status":"error","message":"Страница %s не найдена.","code":"page_not_found"}\n' "$agent_id"; exit 1 ;;
        esac
        ;;
    page-revoke)
        case "$agent_id" in
            p-7f3a|p-9c1d|p-0bad) printf '{"status":"success","message":"Страница %s отозвана.","id":"%s"}\n' "$agent_id" "$agent_id" ;;
            *) printf '{"status":"error","message":"Страница %s не найдена.","code":"page_not_found"}\n' "$agent_id"; exit 1 ;;
        esac
        ;;
    page-revoke-all)
        echo '{"status":"success","message":"Страницы отозваны.","revoked":2}'
        ;;
    mirror-list)
        echo '{"status":"success","message":"ok","max_total_mb":3072,"used_mb":412,"items":[{"app":"amneziavpn","platform":"android","state":"ready","version":"5.0.1.5","size":123456789,"sha256":"0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c4b5a69788796a5b4c3d2e1f0","fetched":"2026-09-17T10:00:00Z","error":""},{"app":"amneziavpn","platform":"windows","state":"downloading","version":"5.0.1.5","size":209715200,"bytes":52428800,"sha256":"","fetched":"","error":""},{"app":"wgtunnel","platform":"android","state":"failed","version":"","size":0,"sha256":"","fetched":"","error":"mirror_unverified","message":"Для файла wgtunnel-1.2.3.apk из релиза v1.2.3 источник не отдал контрольную сумму — сборка не публикуется"}]}'
        ;;
    mirror-fetch)
        case "$app" in
            amneziavpn|amneziawg|wgtunnel)
                printf '{"status":"success","message":"Загрузка поставлена в очередь.","app":"%s","platform":"%s","state":"queued"}\n' "$app" "$platform" ;;
            defaultvpn)
                echo '{"status":"error","message":"Сборки DefaultVPN зеркалить нельзя: закрытый код.","code":"not_mirrorable"}'; exit 1 ;;
            *)
                printf '{"status":"error","message":"Приложение %s не найдено в каталоге.","code":"unknown_app"}\n' "$app"; exit 1 ;;
        esac
        ;;
    mirror-remove)
        printf '{"status":"success","message":"Копия удалена.","app":"%s","platform":"%s"}\n' "$app" "$platform"
        ;;
    user-attach-key)
        [ -n "$profile" ] || profile=awg2
        if [ -z "$material" ]; then
            echo '{"status":"error","message":"Ключ не прислан: нужна ссылка vpn://… или текст файла .conf, который вы отправляли человеку","code":"no_config"}'
            exit 2
        fi
        case "$material" in
            *OTHER-SERVER*)
                echo '{"status":"error","message":"Этот ключ от другого сервера: в нём нет ни одного профиля этого сервера","code":"server_mismatch"}'
                exit 2 ;;
            *SOMEONE-ELSE*)
                printf '{"status":"error","message":"Это не ключ %s: он не совпадает с ключом этого человека на профиле %s","code":"key_mismatch"}\n' "$name" "$profile"
                exit 2 ;;
            *vpn://*) ;;      # как ядро: ссылку ищем во всём тексте (её пересылают с подписью)
            *"[Interface]"*PrivateKey*) ;;
            *)
                echo '{"status":"error","message":"Не удалось прочитать ключ: ожидается ссылка vpn://… или текст файла .conf с [Interface] и PrivateKey","code":"config_unreadable"}'
                exit 2 ;;
        esac
        if [ "$name" = "anna" ]; then
            printf '{"status":"error","message":"Ключ anna на профиле %s уже есть на сервере — привязывать нечего","code":"already_has_key"}\n' "$profile"
            exit 2
        fi
        printf '{"status":"success","message":"Старый ключ %s на профиле %s привязан: теперь его можно показать и отправить отсюда. У человека ничего не изменилось.","name":"%s","profile":"%s","line":"2.0"}\n' \
            "$name" "$profile" "$name" "$profile"
        ;;
    users-add-bulk)
        if [ "${FAKE_ADMIN_BULK:-ok}" = "room" ]; then
            echo '{"status":"error","message":"В профиле AmneziaWG 2.0 (awg2) свободно мест: 3 из 254 — 10 человек не поместятся, никого не добавлено.","code":"subnet_full"}'
            exit 1
        fi
        n="${count_arg:-1}"
        done_n="$n"
        if [ "${FAKE_ADMIN_BULK:-ok}" = "partial" ] && [ "$n" -gt 2 ]; then done_n=2; fi
        list=""
        i=1
        while [ "$i" -le "$done_n" ]; do
            if [ -n "$prefix_arg" ]; then
                item=$(printf '{"name":"%s-%02d","label":"%s-%02d"}' "$prefix_arg" "$i" "$prefix_arg" "$i")
            else
                item=$(printf '{"name":"bulk_%02d","label":"Человек %d"}' "$i" "$i")
            fi
            list="${list:+$list,}$item"
            i=$((i + 1))
        done
        failed=null
        partial=false
        if [ "$done_n" != "$n" ]; then
            partial=true
            failed='{"name":"bulk_03","code":"keys_failed","message":"Не удалось сгенерировать ключи"}'
        fi
        printf '{"status":"success","message":"Добавлено человек: %s.","requested":%s,"count":%s,"added":[%s],"profiles":["awg3","awg2"],"prefix":"%s","partial":%s,"failed":%s}\n' \
            "$done_n" "$n" "$done_n" "$list" "$prefix_arg" "$partial" "$failed"
        ;;
    users-del-bulk-preview|users-del-bulk)
        sel=""; missing=""; mode="state"; value="$state_arg"
        known=" anna bob old greedy "   # люди из users-list этой заглушки
        if [ -n "$names_arg" ]; then
            mode="names"; value="$names_arg"
            for n in $(printf '%s' "$names_arg" | tr ',' ' '); do
                case "$known" in
                    *" $n "*) sel="${sel:+$sel }$n" ;;
                    *)        missing="${missing:+$missing,}\"$n\"" ;;
                esac
            done
        elif [ -n "$prefix_arg" ]; then
            mode="prefix"; value="$prefix_arg"
            [ "$prefix_arg" = "team" ] && sel="team-01 team-02 team-03"
        else
            case "$state_arg" in disabled) sel="bob" ;; expired) sel="old" ;; quota) sel="greedy" ;; esac
        fi
        cnt=0; list=""; names_js=""
        for n in $sel; do
            cnt=$((cnt + 1))
            list="${list:+$list,}{\"name\":\"$n\",\"label\":\"$n\"}"
            names_js="${names_js:+$names_js,}\"$n\""
        done
        if [ "$action" = "users-del-bulk-preview" ]; then
            printf '{"status":"success","message":"Под выбор попадает человек: %s.","mode":"%s","value":"%s","count":%s,"max":100,"too_many":false,"selected":[%s],"missing":[%s]}\n' \
                "$cnt" "$mode" "$value" "$cnt" "$list" "$missing"
            exit 0
        fi
        if [ -n "$missing" ]; then
            printf '{"status":"error","message":"Таких пользователей нет: %s — никого не удалено.","code":"user_not_found"}\n' "$(printf '%s' "$missing" | tr -d '"')"
            exit 3
        fi
        if [ "$cnt" = 0 ]; then
            echo '{"status":"error","message":"Никого не нашлось — удалять некого","code":"nothing_selected","matched":0}'
            exit 1
        fi
        if [ "$confirm_arg" != "$cnt" ]; then
            printf '{"status":"error","message":"Подтверждено %s, а выбрано сейчас %s: список успел измениться — никого не удалено.","code":"confirm_mismatch","matched":%s}\n' \
                "$confirm_arg" "$cnt" "$cnt"
            exit 1
        fi
        if [ "${FAKE_ADMIN_BULK_DEL:-ok}" = "partial" ] && [ "$cnt" -gt 1 ]; then
            first="${sel%% *}"; rest="${sel#* }"; stop="${rest%% *}"
            kept_js=$(printf '%s' "$rest" | sed 's/[^ ][^ ]*/"&"/g; s/ /,/g')
            printf '{"status":"success","message":"Удалено 1 из %s; на %s остановилось: контейнер не принял изменения","requested":%s,"count":1,"deleted":["%s"],"kept":[%s],"partial":true,"failed":{"name":"%s","code":"user_del_failed","message":"контейнер не принял изменения — пачка оставлена в списке выключенной, удаление можно повторить"},"mode":"%s","value":"%s"}\n' \
                "$cnt" "$stop" "$cnt" "$first" "$kept_js" "$stop" "$mode" "$value"
            exit 0
        fi
        printf '{"status":"success","message":"Удалено человек: %s.","requested":%s,"count":%s,"deleted":[%s],"kept":[],"partial":false,"failed":null,"mode":"%s","value":"%s"}\n' \
            "$cnt" "$cnt" "$cnt" "$names_js" "$mode" "$value"
        ;;
    # Короткие ссылки с 2FA через бота (договор docs/SUBSCRIPTION.md): ответы — поле actions фикстуры
    # tests/fixtures/short_links.json; итог short-decide и short-status — полем status, как в договоре.
    # FAKE_ADMIN_SHORT_DECIDE=merged — мост с занятым status: success и итог полем decision.
    short-ensure|short-list|short-replace-request|short-decide|short-status|short-pending|short-bans|short-unban)
        python3 - "$(dirname "$0")/short_links.json" "$action" "${FAKE_ADMIN_SHORT_DECIDE:-}" <<'PY'
import json
import sys
fx = json.load(open(sys.argv[1], encoding="utf-8"))
r = dict(fx["actions"][sys.argv[2]])
if sys.argv[2] == "short-decide" and sys.argv[3] == "merged":
    r = {"status": "success", "message": "ok", "decision": r["status"], "display": r["display"], "url": r["url"]}
print(json.dumps(r, ensure_ascii=False))
PY
        ;;
    # Модуль ядра AmneziaWG (KMOD_PLAN этап Б, CONTRACT §4.7a): состояние — модуль не ставился, профили на программе;
    # FAKE_ADMIN_KMOD_ENGINE=lost — перевод не удался (никто не вернулся за 3 минуты), сервер откатан.
    kmod-status)
        echo '{"status":"success","message":"ok","kmod":{"loaded":false,"version":"","patched":false,"warn":false,"state":"off","code":"","reason":"","kernel":"6.8.0-36-generic","want":"","installed":false,"installed_version":"","owner":"","label":"3.1.20260906+kaskad-k31.1","profiles":{"awg2":{"engine":"program","want":""},"awg3":{"engine":"stopped","want":""}},"progress":null,"selftest":null,"failed":null,"packages":[],"junk":"<script>"}}'
        ;;
    kmod-check)
        echo '{"status":"success","message":"ok","check":{"ok":true,"code":"","reason":"","arch":"x86_64","virt":"none","secure_boot":false,"disk_free_mb":9500,"kernel":"6.8.0-36-generic","kernel_ok":true,"headers":{"installed":false,"candidate":"6.8.0-36.36"},"module":{"loaded":false,"version":"","installed":false,"ko_version":"","owner":""},"kaskad":false,"reuse":false,"label":"3.1.20260906+kaskad-k31.1","plan":{"missing":["dkms","gcc-13"],"install":["dkms","gcc-13"],"size_mb":214,"kernel":false,"gcc":"gcc-13","headers_meta":null}}}'
        ;;
    kmod-install)
        echo '{"status":"success","message":"Модуль ядра установлен","state":"ready","version":"3.1.20260906+kaskad-k31.1","kernel":"6.8.0-36-generic","owner":"goamnezia","reused":false,"p218":false,"packages":["dkms","gcc-13"],"headers_meta_skipped":"","selftest":{"ok":true,"kernel":"6.8.0-36-generic","label":"3.1.20260906+kaskad-k31.1","at":"2026-10-08T10:00:00Z","cases":[{"id":"awg2k","profile":"awg2","line":"2.0","client":"kernel","ok":true,"detail":""}],"reason":""},"want":"program","duration_s":95}'
        ;;
    kmod-engine)
        if [ "${FAKE_ADMIN_KMOD_ENGINE:-ok}" = "lost" ]; then
            echo '{"status":"error","message":"Профиль awg2: из 3 человек на связи за 3 мин не вернулся ни один — сервер откатан на прежний движок","code":"kmod_clients_lost","engine":"kernel","want":"program","profiles":[{"profile":"awg2","from":"program","to":"kernel","restarted":true,"alive_before":3,"returned":0,"return_s":null,"note":"lost"}],"rolled_back":true,"copies":"/opt/goamnezia/state/.kmod-copies/x"}'
            exit 1
        fi
        echo '{"status":"success","message":"Движок профилей сменён","engine":"kernel","want":"kernel","profiles":[{"profile":"awg2","from":"program","to":"kernel","restarted":true,"alive_before":2,"returned":2,"return_s":17,"note":""}],"rolled_back":false,"copies":"/opt/goamnezia/state/.kmod-copies/x"}'
        ;;
    kmod-remove)
        echo '{"status":"success","message":"Модуль ядра удалён","removed":true,"kept_for_neighbour":false,"packages":["dkms","gcc-13"],"command":"apt remove dkms gcc-13"}'
        ;;
    *)
        printf '{"status":"success","message":"ok: %s","action":"%s","name":"%s","profile":"%s"}\n' "$action" "$action" "$name" "$profile"
        ;;
esac
exit 0
