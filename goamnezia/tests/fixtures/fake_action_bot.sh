#!/usr/bin/env bash
# Заглушка моста `install.sh --action=<имя> --json --k=v …` для тестов Telegram-бота
# (tests/test_bot_features.py). Своя, отдельная от fake_action.sh MCP-тестов: у бота
# другой набор нужных ответов (карточка пользователя, статус с профилями, список
# бэкапов, параметры профиля), и менять чужую заглушку ради них нельзя.
#
# Ведёт себя как настоящий мост по контракту (docs/CONTRACT.md §0 п.3): весь «UI» — в
# stderr, в stdout мусор ДО последней строки допустим, а ПОСЛЕДНЯЯ строка stdout — JSON
# со status. Мусор здесь нарочно: тест проверяет, что бот берёт последнюю JSON-строку.
#
# Управление через окружение:
#   FAKE_BOT_ARGS_FILE   — дописать строку с argv (аргументы через табуляцию);
#   FAKE_BOT_ENV_FILE    — дописать значение GOAMNEZIA_WHO (кто зовёт мост);
#   FAKE_BOT_SLEEP       — поспать N секунд перед ответом (проверка таймаута);
#   FAKE_BOT_STATUS      — success (по умолчанию) | error — вернуть status=error;
#   FAKE_BOT_NO_JSON=1   — завершиться без JSON (проверка ошибки транспорта);
#   FAKE_BOT_INSTALLED   — true (по умолчанию) | false — поле installed в status;
#   FAKE_BOT_MODE        — lite (по умолчанию) | lite_site | pro — режим в status и site-check;
#   FAKE_BOT_MINIAPP     — off (по умолчанию) | on — включена ли панель на домене;
#   FAKE_BOT_SHARES      — 1 (по умолчанию) | 0 — сколько активных раздач в share-list;
#   FAKE_BOT_PREFLIGHT   — free (по умолчанию) | busy — заняты ли 80/443;
#   FAKE_BOT_PAGES       — some (по умолчанию) | empty — есть ли страницы подключения в page-list;
#   FAKE_BOT_MIRROR      — some (по умолчанию) | empty — есть ли копии сборок в mirror-list;
#   FAKE_BOT_EDGE        — single (по умолчанию) | multi — адреса машины в edge-status
#                          (key_choices, keys_host) и status.key_hosts: один адрес или три IP + домен;
#   FAKE_BOT_KEY_HOST    — адрес в ключах пользователя (user-show, key_host в корне ответа, как у
#                          install.sh); пусто — поля нет;
#   FAKE_BOT_KEY_HOST_GONE=1 — закреплённый адрес пользователя пропал (key_host_gone в корне);
#   FAKE_BOT_KEY_REFUSE  — код отказа user-key/share-create на непустой --endpoint (address_not_here…).
#   FAKE_BOT_ENDPOINT_GONE=1 — в status вписанный в ключи адрес 198.51.100.7 пропал с сервера
#                          (endpoint_host, endpoint_host_gone: true — L-02);
#   FAKE_BOT_BOB_ENABLED — false (по умолчанию) | true — включён ли bob в user-show (A3-5: временная ссылка выключенному);
#   FAKE_BOT_USER_IP     — свой адрес сервера у anna (user.endpoint_ip в user-show; L-10 user-set-address);
#   FAKE_BOT_PIN_SAME=1  — user-set-address: адрес в самом ключе не изменился (resend_required: false);
#   FAKE_BOT_STDIN_FILE  — записать сюда то, что user-attach-key получил в stdin (ключ идёт ТОЛЬКО так);
#   FAKE_BOT_STATE_DIR   — каталог состояния: удачный user-attach-key кладёт туда метку, и user-show
#                          после неё отдаёт пир с has_key: true (карточка после привязки — с выдачей);
#                          там же счётчик name-suggest — варианты имени идут по кругу;
#   FAKE_BOT_NAME_SUGGEST — bridge (по умолчанию) | okdata | unknown | noexclude | repeat — ответ
#                          name-suggest в обычной форме моста (id и label в корне), в форме договора
#                          {"ok":true,"data":{…}}, отказ ядра, которое действия ещё не знает
#                          (unknown_action), ядро без --exclude (на него bad_arg) или ядро, которое
#                          всегда предлагает одно и то же (foggy_london, --exclude не слушает).
#                          Без repeat вариант, совпавший с --exclude, пропускается.
#   FAKE_BOT_LAZY        — pro (по умолчанию) | pro31 | pro_old | semi_pro | nolines | blocked | app | partial |
#                          dnsunknown | awg2busy | cf | unknown — ответ lazy-check по договору 0.6.0 с «Уточнениями» и
#                          «По итогам ядра» (docs/MODES_047.md): всё свободно, на 443 — 2.0 (udp443_line); на 443 —
#                          3.1 (2.0 подхвачена на своём порту); ядро без udp443_line; UDP 443 занят; обе линии на
#                          своих портах (no_line_for_443); домен не сюда и TCP 443 чужой (плюс код, которого бот не
#                          знает); TCP 443 держит контейнер приложения; часть A-записей чужие; DNS не ответил; место
#                          2.0 занято контейнером приложения с людьми; домен за прокси Cloudflare; ядро без действия.
#                          by — длинная формулировка предполётной проверки, как у ядра. xn--e1afmkfd.xn--p1ai
#                          показывается как «пример.рф» (domain_display).
#   FAKE_BOT_LAZY_SETUP  — ok (по умолчанию) | semi | nolines | rare | refused | midway | error | unknown — ответ
#                          lazy-setup: Pro с 2.0 на UDP 443 и ключами (по --keys, не больше пяти); Semi-Pro без UDP 443,
#                          без панели на домене и с ключом без страницы; Semi-Pro при линиях на своих портах; все
#                          редкие warnings; отказ проверки с plan и reasons; срыв по ходу (adopt_failed, plan pro);
#                          отказ без плана; ядро без действия.
#   FAKE_BOT_CERT_FAIL   — dns | net | limit | limit_until | nocause — change-mode с доменом: отказ Let's Encrypt
#                          (cert_failed) с причиной cert_cause (limit_until — ещё и срок cert_retry_after), nocause —
#                          без причины, как у ядра старше 1.0.6 (L-26);
#   FAKE_BOT_PRO_LINE    — ok (по умолчанию) | adopted | adopted_noname | error | unknown — ответ pro-line: линия
#                          встала; отказ adopted_keyless, пока нет --adopted-confirm=awg2 (подхваченный профиль
#                          назван полем profile); то же без поля (подтверждается выбранный --profile); отказ
#                          ядра; ядро без действия.
#   FAKE_BOT_BULK        — ok (по умолчанию) | partial | room | unknown — ответ users-add-bulk: все --count
#                          добавлены (имена bulk_01… с подписями «Человек 1»…; с --prefix=office — office-01…,
#                          подпись — имя, как у ядра); сбой на третьем — success с partial и failed; отказ
#                          subnet_full до изменений; ядро без действия.
#   FAKE_BOT_BULK_DEL    — ok (по умолчанию) | partial | mismatch — users-del-bulk-preview и users-del-bulk:
#                          --state=disabled → bob, expired → никого, quota → 150 (больше, чем за раз); --prefix=team →
#                          team-01…03, другой — никого. Удаление сверяет --confirm с числом выбранных; partial — удалён
#                          первый, остальные оставлены выключенными (user_del_failed); mismatch — список «изменился»
#                          (confirm_mismatch, matched на одного больше).
#
# Ключ из приложения Amnezia (has_key: false, docs/CONTRACT.md § user-attach-key): у proba на awg2
# ключ из приложения, awg3 свой; у olga ключ из приложения на единственном профиле awg2. По таким
# пирам user-key и share-create отвечают no_private_key, page-create у olga — тоже, у proba —
# страницей с imported_profiles. user-attach-key решает по присланному: пусто — no_config; в тексте
# OTHER-SERVER — server_mismatch; SOMEONE-ELSE — key_mismatch; не vpn:// и не .conf с [Interface] и
# PrivateKey — config_unreadable; пир с ключом (anna) — already_has_key; иначе привязан.
# Совместим с bash 3.2 (macOS по умолчанию): без массивов и bash-4 конструкций.

action=""
name=""
profile=""
format=""
agent_id=""
label=""
scope=""
port=""
container=""
apply=""
mode_arg=""
domain_arg=""
template=""
lang=""
state=""
ttl=""
app=""
platform=""
endpoint=""
exclude=""
ip_arg=""
days_arg=""
keys_arg=""
adopted_arg=""
count_arg=""
prefix_arg=""
confirm_arg=""
sub_arg=""
for a in "$@"; do
    case "$a" in
        --action=*)  action="${a#--action=}" ;;
        --name=*)    name="${a#--name=}" ;;
        --profile=*) profile="${a#--profile=}" ;;
        --format=*)  format="${a#--format=}" ;;
        --id=*)      agent_id="${a#--id=}" ;;
        --sub=*)     sub_arg="${a#--sub=}" ;;
        --label=*)   label="${a#--label=}" ;;
        --scope=*)   scope="${a#--scope=}" ;;
        --port=*)    port="${a#--port=}" ;;
        --container=*) container="${a#--container=}" ;;
        --apply=*)   apply="${a#--apply=}" ;;
        --mode=*)    mode_arg="${a#--mode=}" ;;
        --domain=*)  domain_arg="${a#--domain=}" ;;
        --template=*) template="${a#--template=}" ;;
        --lang=*)    lang="${a#--lang=}" ;;
        --state=*)   state="${a#--state=}" ;;
        --ttl=*)     ttl="${a#--ttl=}" ;;
        --app=*)     app="${a#--app=}" ;;
        --platform=*) platform="${a#--platform=}" ;;
        --endpoint=*) endpoint="${a#--endpoint=}" ;;
        --exclude=*) exclude="${a#--exclude=}" ;;
        --ip=*)      ip_arg="${a#--ip=}" ;;
        --days=*)    days_arg="${a#--days=}" ;;
        --keys=*)    keys_arg="${a#--keys=}" ;;
        --adopted-confirm=*) adopted_arg="${a#--adopted-confirm=}" ;;
        --count=*)   count_arg="${a#--count=}" ;;
        --prefix=*)  prefix_arg="${a#--prefix=}" ;;
        --confirm=*) confirm_arg="${a#--confirm=}" ;;
    esac
done

if [ -n "${FAKE_BOT_ARGS_FILE:-}" ]; then
    { printf '%s\t' "$@"; printf '\n'; } >> "$FAKE_BOT_ARGS_FILE"
fi
if [ -n "${FAKE_BOT_ENV_FILE:-}" ]; then
    printf '%s\n' "${GOAMNEZIA_WHO:-}" >> "$FAKE_BOT_ENV_FILE"
fi

# Шум в stderr: обычные логи и спиннер с ANSI-цветом, как у настоящего install.sh.
echo "fake_action_bot: обработка действия '${action}'" >&2
printf '\033[32m[ok]\033[0m spinner noise\r\n' >&2

if [ -n "${FAKE_BOT_SLEEP:-}" ]; then
    sleep "$FAKE_BOT_SLEEP"
fi

# Мусорные строки в stdout ДО JSON — в том числе «чужой» JSON без status,
# который бот брать не должен, и объект со status в середине вывода.
echo "GARBAGE-LINE-FROM-BRIDGE: this is not json"
echo '{"note": "intermediate object without status"}'

# Ключ человека у user-attach-key — только из stdin (в argv его видно в ps). Остальным действиям
# бот stdin закрывает, так что читать его им незачем.
material=""
if [ "$action" = "user-attach-key" ]; then
    material=$(cat)
    if [ -n "${FAKE_BOT_STDIN_FILE:-}" ]; then
        printf '%s' "$material" > "$FAKE_BOT_STDIN_FILE"
    fi
fi

if [ "${FAKE_BOT_NO_JSON:-0}" = "1" ]; then
    echo "fake_action_bot: авария без JSON" >&2
    # Упавший мост печатает присланный ключ: бот обязан не переносить такой вывод в свой лог
    [ -n "$material" ] && printf 'CRASH-DUMP %s\n' "$material" && printf 'CRASH-DUMP %s\n' "$material" >&2
    exit 3
fi

if [ "${FAKE_BOT_STATUS:-success}" = "error" ]; then
    printf '{"status":"error","message":"forced error from fixture","code":"forced_error","action":"%s"}\n' "$action"
    exit 1
fi

# Валидный PNG 1×1 (70 байт): проверяем настоящую картинку, а не «какой-то base64».
PNG='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAX+XDSwAAAABJRU5ErkJggg=='
INSTALLED="${FAKE_BOT_INSTALLED:-true}"
MODE="${FAKE_BOT_MODE:-lite}"
# Домен есть только там, где он бывает по-настоящему: в Pro (lite_site живёт на
# IP с самоподписанным сертификатом, lite — вообще без сайта).
DOMAIN=""
[ "$MODE" = "pro" ] && DOMAIN="vpn.example.com"
# Блок site в status: null в lite, объект в режимах с сайтом (как у настоящего моста)
SITE='null'
if [ "$MODE" = "pro" ]; then
    SITE='{"url":"https://vpn.example.com/","template_id":"h5up_aerial","template_source":"html5up","listen":"public","cert":"letsencrypt","cert_expires":"2026-12-01T00:00:00Z","cert_days_left":82,"cert_fingerprint_sha256":""}'
elif [ "$MODE" = "lite_site" ]; then
    SITE='{"url":"https://203.0.113.10/","template_id":"h5up_aerial","template_source":"html5up","listen":"public","cert":"selfsigned","cert_expires":"","cert_days_left":null,"cert_fingerprint_sha256":"3A:9F:12:B7:04:CE:88:1D:6E:52:AB:77:30:F4:9C:21:55:E0:8B:46:D2:7A:13:C9:60:EF:35:A8:74:1B:5D:92"}'
fi

# Адреса машины для «Ключ на другой адрес» (контракт моста: edge-status key_choices/keys_host,
# status.key_hosts, --endpoint у user-key и share-create). multi — три IP (третий замолчал) и домен.
EDGE="${FAKE_BOT_EDGE:-single}"
KEY_HOSTS=""
ENDPOINT_OK="203.0.113.10"
if [ "$EDGE" = "multi" ]; then
    KEY_HOSTS=',"key_hosts":["203.0.113.10","vpn.example.com","203.0.113.11","203.0.113.12"]'
    ENDPOINT_OK="203.0.113.10 vpn.example.com 203.0.113.11 203.0.113.12"
fi
# Отказ на --endpoint, как у ядра: rc 2, status=error, code; пусто — адрес принят
endpoint_refusal() {
    [ -n "$endpoint" ] || return 1
    if [ -n "${FAKE_BOT_KEY_REFUSE:-}" ]; then
        printf '{"status":"error","message":"Адреса %s у сервера нет. Допустимые: %s","code":"%s"}\n' "$endpoint" "$ENDPOINT_OK" "$FAKE_BOT_KEY_REFUSE"
        return 0
    fi
    case " $ENDPOINT_OK " in
        *" $endpoint "*) return 1 ;;
    esac
    printf '{"status":"error","message":"Адреса %s у сервера нет. Допустимые: %s","code":"address_not_here"}\n' "$endpoint" "$ENDPOINT_OK"
    return 0
}
USER_EXTRA=""
[ -n "${FAKE_BOT_KEY_HOST:-}" ] && USER_EXTRA=",\"key_host\":\"${FAKE_BOT_KEY_HOST}\""
[ "${FAKE_BOT_KEY_HOST_GONE:-0}" = "1" ] && USER_EXTRA="${USER_EXTRA},\"key_host_gone\":true"

# Ключ из приложения Amnezia: привязан ли уже (метка удачного user-attach-key в FAKE_BOT_STATE_DIR)
attached() {
    [ -n "${FAKE_BOT_STATE_DIR:-}" ] && [ -f "${FAKE_BOT_STATE_DIR}/attached-$1-$2" ]
}
# has_key пира: false только у ключа из приложения, пока его не привязали
has_key_of() {
    case "$1/$2" in
        proba/awg2|olga/awg2)
            if attached "$1" "$2"; then printf 'true'; else printf 'false'; fi ;;
        *) printf 'true' ;;
    esac
}
# Выдача по ключу из приложения — отказ словами ядра (lib/lang/ru.d/export.sh, export_err_no_privkey)
no_private_key_refusal() {
    [ "$(has_key_of "$name" "$profile")" = "false" ] || return 1
    printf '{"status":"error","message":"Ключ %s на профиле %s выдан в приложении Amnezia и хранится только там — отсюда его не показать и не отправить. У человека он работает. Привяжите старый ключ (ссылка vpn:// или .conf, которые вы ему отправляли) или перевыпустите — тогда старый перестанет подключаться.","code":"no_private_key"}\n' "$name" "$profile"
    return 0
}

case "$action" in
    status)
        eh=""; eh_gone=false; eh_eff="${DOMAIN:-203.0.113.10}"
        if [ "${FAKE_BOT_ENDPOINT_GONE:-0}" = "1" ]; then
            eh="198.51.100.7"; eh_gone=true; eh_eff="198.51.100.7"
        fi
        printf '{"status":"success","message":"ok","version":"0.1.0","installed":%s,"mode":"%s","domain":"%s","server_ip":"203.0.113.10","endpoint_host":"'"$eh"'","endpoint_host_gone":'"$eh_gone"',"endpoint_effective":"%s","endpoint_auto":"%s","users":3,"site":%s,"profiles":[{"profile":"awg2","container":"amnezia-awg2","state":"running","port":443,"line":"2.0","peers":2,"image_tag":"3.1.20260828","image_digest":"sha256:0","enabled":true},{"profile":"awg3","container":"amnezia-awg3","state":"exited","port":39001,"line":"3.1","peers":1,"image_tag":"3.1.20260828","image_digest":"sha256:0","enabled":true}]%s}\n' \
            "$INSTALLED" "$MODE" "$DOMAIN" "$eh_eff" "${DOMAIN:-203.0.113.10}" "$SITE" "$KEY_HOSTS"
        ;;
    profiles-status)
        echo '{"status":"success","message":"ok","profiles":[{"profile":"awg2","container":"amnezia-awg2","state":"running","port":443,"line":"2.0","peers":2,"image_tag":"3.1.20260828","image_digest":"sha256:0","enabled":true}]}'
        ;;
    user-list)
        echo '{"status":"success","message":"ok","users":[{"name":"anna","label":"Анна","created":"2026-09-01T10:00:00Z","enabled":true,"quota_bytes":0,"expires":"","traffic":{"rx":1048576,"tx":2097152,"last_rx":0,"last_tx":0,"updated":""},"profiles":["awg2","awg3"],"peers":{"awg2":{"ip":"10.8.2.2","pubkey":"PUBANNA2","created":"2026-09-01T10:00:00Z","active":true,"last_handshake":0,"rx":0,"tx":0},"awg3":{"ip":"10.8.3.2","pubkey":"PUBANNA3","created":"2026-09-01T10:00:00Z","active":true,"last_handshake":0,"rx":0,"tx":0}}},{"name":"bob","label":"Bob","created":"2026-09-01T10:00:00Z","enabled":false,"quota_bytes":0,"expires":"","traffic":{"rx":0,"tx":0,"last_rx":0,"last_tx":0,"updated":""},"profiles":["awg2"],"peers":{"awg2":{"ip":"10.8.2.3","pubkey":"PUBBOB2","created":"2026-09-01T10:00:00Z","active":false,"last_handshake":0,"rx":0,"tx":0}}},{"name":"old","label":"Old","created":"2026-01-01T10:00:00Z","enabled":true,"quota_bytes":0,"expires":"2020-01-01T00:00:00Z","traffic":{"rx":0,"tx":0,"last_rx":0,"last_tx":0,"updated":""},"profiles":[],"peers":{}},{"name":"greedy","label":"Greedy","created":"2026-01-01T10:00:00Z","enabled":true,"quota_bytes":1024,"expires":"","traffic":{"rx":1024,"tx":1024,"last_rx":0,"last_tx":0,"updated":""},"profiles":[],"peers":{}}]}'
        ;;
    user-show)
        if [ "$name" = "anna" ]; then
            printf '{"status":"success","message":"ok","name":"anna","user":{"name":"anna","label":"Анна","endpoint_ip":"'"${FAKE_BOT_USER_IP:-}"'","created":"2026-09-01T10:00:00Z","enabled":true,"quota_bytes":53687091200,"expires":"2027-01-01T00:00:00Z","traffic":{"rx":1048576,"tx":2097152,"last_rx":0,"last_tx":0,"updated":""},"profiles":["awg2","awg3"],"peers":{"awg2":{"ip":"10.8.2.2","pubkey":"PUBANNA2","created":"2026-09-01T10:00:00Z","active":true,"last_handshake":0,"rx":0,"tx":0},"awg3":{"ip":"10.8.3.2","pubkey":"PUBANNA3","created":"2026-09-01T10:00:00Z","active":true,"last_handshake":1700000000,"rx":0,"tx":0}}}%s}\n' "$USER_EXTRA"
        elif [ "$name" = "bob" ]; then
            # Ровно один профиль: на нём проверяется пропуск лишнего экрана выбора
            printf '{"status":"success","message":"ok","name":"bob","user":{"name":"bob","label":"Bob","created":"2026-09-01T10:00:00Z","enabled":'"${FAKE_BOT_BOB_ENABLED:-false}"',"quota_bytes":0,"expires":"","traffic":{"rx":0,"tx":0,"last_rx":0,"last_tx":0,"updated":""},"profiles":["awg2"],"peers":{"awg2":{"ip":"10.8.2.3","pubkey":"PUBBOB2","created":"2026-09-01T10:00:00Z","active":false,"last_handshake":0,"rx":0,"tx":0}}}%s}\n' "$USER_EXTRA"
        elif [ "$name" = "proba" ]; then
            # Подхвачен из приложения Amnezia: на awg2 ключ из приложения (пока не привязан), awg3 свой
            printf '{"status":"success","message":"ok","name":"proba","user":{"name":"proba","label":"Proba","created":"2026-09-01T10:00:00Z","enabled":true,"imported":true,"quota_bytes":0,"expires":"","traffic":{"rx":0,"tx":0,"last_rx":0,"last_tx":0,"updated":""},"profiles":["awg2","awg3"],"peers":{"awg2":{"ip":"10.8.2.9","pubkey":"PUBPROBA2","created":"2026-09-01T10:00:00Z","active":true,"last_handshake":0,"rx":0,"tx":0,"has_key":%s},"awg3":{"ip":"10.8.3.9","pubkey":"PUBPROBA3","created":"2026-09-01T10:00:00Z","active":true,"last_handshake":0,"rx":0,"tx":0,"has_key":true}}}%s}\n' "$(has_key_of proba awg2)" "$USER_EXTRA"
        elif [ "$name" = "olga" ]; then
            # Единственный профиль — ключ из приложения: выдавать отсюда нечего, пока его не привязали
            printf '{"status":"success","message":"ok","name":"olga","user":{"name":"olga","label":"Olga","created":"2026-09-01T10:00:00Z","enabled":true,"imported":true,"quota_bytes":0,"expires":"","traffic":{"rx":0,"tx":0,"last_rx":0,"last_tx":0,"updated":""},"profiles":["awg2"],"peers":{"awg2":{"ip":"10.8.2.10","pubkey":"PUBOLGA2","created":"2026-09-01T10:00:00Z","active":true,"last_handshake":0,"rx":0,"tx":0,"has_key":%s}}}%s}\n' "$(has_key_of olga awg2)" "$USER_EXTRA"
        else
            printf '{"status":"error","message":"Пользователь %s не найден","code":"user_not_found","action":"user-show"}\n' "$name"
            exit 3
        fi
        ;;
    user-key)
        # Заглушка not_implemented: у настоящего моста такого кода больше нет, но
        # бот обязан уметь его показать — тест держится на этом формате-ловушке.
        if [ "$format" = "later" ]; then
            echo '{"status":"error","message":"Действие появится в следующей версии.","code":"not_implemented"}'
            exit 1
        fi
        if no_private_key_refusal; then
            exit 3
        fi
        if endpoint_refusal; then
            exit 2
        fi
        key_host="${endpoint:-203.0.113.10}"
        override=false
        [ -n "$endpoint" ] && override=true
        # Приватный ключ — единственное место, где секрет допустим в ответе (§0.7).
        printf '{"status":"success","message":"ok","name":"%s","profile":"%s","line":"2.0","min_client":"4.8.12.7","conf":"[Interface]\\nPrivateKey = FAKE-PRIVATE-KEY\\nAddress = 10.8.2.2/32\\n\\n[Peer]\\nEndpoint = %s:443\\n","vpn":"vpn://FAKEKEY","qr_conf_png_b64":"%s","qr_vpn_png_b64":["%s","%s","not-a-png"],"format":"%s","key_host":"%s","key_endpoint":"%s:443","key_host_override":%s%s}\n' \
            "$name" "$profile" "$key_host" "$PNG" "$PNG" "$PNG" "$format" "$key_host" "$key_host" "$override" \
            "$([ "${FAKE_BOT_OLD_KEY:-0}" = 1 ] || printf ',"file_name":"%s-2_0.conf"' "$name")"
        ;;
    traffic)
        echo '{"status":"success","message":"ok","profiles":[{"profile":"awg2","peers":[{"pubkey":"PUBANNA2","endpoint":"198.51.100.7:51820","allowed_ips":"10.8.2.2/32","latest_handshake_ts":1,"rx":10,"tx":20}]}],"users":[{"name":"anna","label":"Анна","enabled":true,"quota_bytes":0,"expires":"","traffic":{"rx":10,"tx":20},"profiles":["awg2"],"peers":{"awg2":{"ip":"10.8.2.2","pubkey":"PUBANNA2","active":true}}}]}'
        ;;
    logs-tail)
        echo '{"status":"success","message":"ok","component":"awg2","lines":60,"log":"line one\nline two <b>not html</b>\n"}'
        ;;
    quality)
        echo '{"status":"success","message":"ok","quality":{"docker":true,"ip_forward":"1","network":true,"disk_free_mb":12345,"ports":[{"profile":"awg2","port":"443","free":false}]}}'
        ;;
    backup-list)
        echo '{"status":"success","message":"ok","backups":[{"name":"goamnezia-backup-20260908_120000.tar.gz","size":1024,"created":"2026-09-08T12:00:00Z"},{"name":"../evil.tar.gz","size":1,"created":""},{"name":"goamnezia-backup-20260907_120000.tar.gz","size":1024,"created":"2026-09-07T12:00:00Z"}]}'
        ;;
    profile-params)
        printf '{"status":"success","message":"ok","profile":"%s","params":{"Jc":"5","Jmin":"10","Jmax":"50","S1":"30","S2":"40","S3":"20","S4":"25","H1":"1000-2000","H2":"3000-4000","H3":"5000-6000","H4":"7000-8000","I1":"","PersistentKeepalive":"25","MTU":"1280"}}\n' "$profile"
        ;;
    profile-set-param)
        printf '{"status":"success","message":"ok","profile":"%s","reissue_required":true,"result":{"scope":"server"}}\n' "$profile"
        ;;
    import-scan)
        # Как lib/import.sh: наш amnezia-awg2 (managed), legacy amnezia-awg приложения
        # (можно подхватить в awg1) и amnezia-xray приложения — его goAmnezia не
        # поддерживает (продукт только про AmneziaWG).
        echo '{"status":"success","message":"ok","containers":[{"container":"amnezia-awg2","port":443,"proto":"udp","kind":"awg","profile":"awg2","managed":true,"suggested_profile":"awg2","adoptable":false,"reason":"managed","line":"2.0","subnet":"10.8.2.0/24","listen_port":443,"iface":"awg0","server_pubkey":"SRVPUB2","image":"amnezia-awg2","peers":1,"clients":[{"pubkey":"PUBANNA2","name":"Анна","ip":"10.8.2.2","created":"2026-09-01T10:00:00Z","known":true}]},{"container":"amnezia-awg","port":38000,"proto":"udp","kind":"legacy","profile":"","managed":false,"suggested_profile":"awg1","adoptable":true,"reason":"","line":"1.0","subnet":"10.8.5.0/24","listen_port":38000,"iface":"wg0","server_pubkey":"SRVPUB1","image":"amnezia-awg","peers":1,"clients":[{"pubkey":"PUBKEENETIC","name":"Keenetic дома","ip":"10.8.5.1","created":"2025-11-11T11:11:11Z","known":false}]},{"container":"amnezia-xray","port":443,"proto":"tcp","kind":"unsupported","profile":"","managed":false,"suggested_profile":"","adoptable":false,"reason":"unsupported_container","line":"","subnet":"","listen_port":0,"iface":"awg0","server_pubkey":"","image":"","peers":0,"clients":[]}],"adoptable":["amnezia-awg"]}'
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
        if [ "$apply" = "on" ]; then
            printf '{"status":"success","message":"Профиль %s: изменения подхвачены.","profile":"%s","container":"amnezia-awg2","in_sync":false,"new":[{"pubkey":"PUBNEW","ip":"10.8.2.7","name":"Laptop"}],"gone":[],"applied":true,"added":1,"deactivated":0,"users":["Laptop"]}\n' "$profile" "$profile"
        else
            printf '{"status":"success","message":"Профиль %s: контейнер разошёлся с users.json.","profile":"%s","container":"amnezia-awg2","in_sync":false,"new":[{"pubkey":"PUBNEW","ip":"10.8.2.7","name":"Laptop"}],"gone":[{"name":"bob","pubkey":"PUBBOB"}],"applied":false,"added":0,"deactivated":0,"users":[]}\n' "$profile" "$profile"
        fi
        ;;
    upstream-check)
        # Есть обновление: awg2 → 3.2.…, awg3 уже обновлён и умеет откат; новый параметр не поддержан.
        if [ "${FAKE_BOT_UPSTREAM:-}" = "unreachable" ]; then
            echo '{"status":"error","message":"GitHub и Docker Hub недоступны с этого сервера.","code":"unreachable","profiles":[{"profile":"awg2","container":"amnezia-awg2","image_key":"amneziawg-go","current_tag":"3.1.20260828","current_digest":"sha256:cbafc02b","available_tag":"","available_digest":"","update":false,"previous_tag":"","previous_digest":"","rollback_available":false},{"profile":"awg3","container":"amnezia-awg3","image_key":"amneziawg-go","current_tag":"3.2.20260915","current_digest":"sha256:51a5cfd1","available_tag":"","available_digest":"","update":false,"previous_tag":"3.1.20260828","previous_digest":"sha256:cbafc02b","rollback_available":true}],"updates_available":false}'
            exit 1
        fi
        echo '{"status":"success","message":"Есть обновление. Оно ставится только по кнопке: upstream-update --profile=<профиль>","checked":"2026-09-09T05:10:00Z","sources":{"github":"ok","dockerhub":"ok"},"client_release":{"tag":"5.0.3.0","published":"2026-09-20T10:00:00Z","prerelease":true,"url":"https://github.com/amnezia-vpn/amnezia-client/releases/tag/5.0.3.0","current":"5.0.2.1","update":true},"images":{"amneziawg-go":{"repo":"amneziavpn/amneziawg-go","pinned_tag":"3.1.20260828","latest_tag":"3.2.20260915","update":true}},"scripts":{"awg":{"tag":"5.0.3.0","checked":true,"changed":["Dockerfile","template.conf"],"unchanged":[],"missing":[]}},"placeholders":{"awg/template.conf":{"new":["CONTENT_PADDING_MAX"],"removed":[]}},"unsupported":[{"file":"awg/template.conf","name":"CONTENT_PADDING_MAX"}],"profiles":[{"profile":"awg2","container":"amnezia-awg2","image_key":"amneziawg-go","current_tag":"3.1.20260828","current_digest":"sha256:cbafc02b","available_tag":"3.2.20260915","available_digest":"sha256:51a5cfd1","update":true,"previous_tag":"","previous_digest":"","rollback_available":false},{"profile":"awg3","container":"amnezia-awg3","image_key":"amneziawg-go","current_tag":"3.2.20260915","current_digest":"sha256:51a5cfd1","available_tag":"3.2.20260915","available_digest":"sha256:51a5cfd1","update":false,"previous_tag":"3.1.20260828","previous_digest":"sha256:cbafc02b","rollback_available":true}],"updates_available":true,"hint":"Есть обновление. Оно ставится только по кнопке: upstream-update --profile=<профиль>"}'
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
    # ── Доступы агентов к MCP по HTTP (lib/agents.sh): секрет — 43 символа, как у настоящего модуля ──
    agent-list)
        echo '{"status":"success","message":"ok","agents":[{"id":"a-1a2b","label":"Claude Code на ноутбуке","scope":"write","enabled":true,"created":"2026-09-01T10:00:00Z","expires":"2026-12-01T10:00:00Z","port":0,"allow_ips":["203.0.113.0/24"],"last_used":"2026-09-08T09:00:00Z","last_ip":"203.0.113.5","calls":12,"expired":false,"state":"active"},{"id":"a-3c4d","label":"Только чтение","scope":"read","enabled":false,"created":"2026-09-02T10:00:00Z","expires":"","port":0,"allow_ips":[],"last_used":"","last_ip":"","calls":0,"expired":false,"state":"disabled"}],"transport":{"enabled":true,"mode":"lite_site","listen":"nginx","bind":"127.0.0.1","port":1986,"tls":false,"http_port":1986,"public_port":0,"domain":"","server_ip":"203.0.113.10","base_url":"https://203.0.113.10","via":"ip","cert_fingerprint_sha256":"AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99","nginx_location":true,"unit":"goamnezia-mcp-http.service","unit_state":"active","reachable":true,"hint":"","agents":2,"agents_active":1,"agents_with_port":0}}'
        ;;
    agent-show|agent-rotate)
        if [ "$agent_id" = "a-1a2b" ] || [ "$agent_id" = "a-3c4d" ]; then
            printf '{"status":"success","message":"ok","id":"%s","label":"Claude Code на ноутбуке","scope":"write","enabled":true,"expires":"","port":0,"allow_ips":[],"last_used":"","last_ip":"","calls":0,"url":"https://203.0.113.10/a-FAKESECRETFAKESECRETFAKESECRETFAKESECRET01/mcp","via":"ip","cert_fingerprint_sha256":"AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99","require_token":true,"token":"FAKETOKENFAKETOKENFAKETOKENFAKETOKEN012345","pin_ip":false,"pinned_ip":"","connect":"claude mcp add --transport http goamnezia https://203.0.113.10/a-FAKESECRETFAKESECRETFAKESECRETFAKESECRET01/mcp --header Authorization:Bearer-FAKETOKENFAKETOKENFAKETOKENFAKETOKEN012345","transport_enabled":true,"reachable":true,"hint":"Подключение: claude mcp add --transport http goamnezia https://203.0.113.10/a-FAKESECRETFAKESECRETFAKESECRETFAKESECRET01/mcp","warning":"Адрес — это ключ."}\n' "$agent_id"
        else
            printf '{"status":"error","message":"Доступ %s не найден.","code":"agent_not_found"}\n' "$agent_id"
            exit 1
        fi
        ;;
    # Продлить доступ (lib/agents.sh agents_extend): {id, expires, old_expires, days}; 0 — снять срок
    agent-extend)
        case "$agent_id" in
            a-1a2b|a-3c4d) ;;
            *) printf '{"status":"error","message":"Доступ %s не найден.","code":"agent_not_found"}\n' "$agent_id"; exit 1 ;;
        esac
        ext_exp="2026-12-31T10:00:00Z"
        [ "$days_arg" = "0" ] && ext_exp=""
        printf '{"status":"success","message":"Срок доступа %s продлён.","id":"%s","expires":"%s","old_expires":"2026-12-01T10:00:00Z","days":%s}\n' \
            "$agent_id" "$agent_id" "$ext_exp" "${days_arg:-0}"
        ;;
    # Свой адрес сервера за человеком (lib/users.sh users_set_endpoint_ip): адрес — из адресов машины
    # (FAKE_BOT_EDGE), общий 203.0.113.10 пишется пусто; FAKE_BOT_PIN_SAME=1 — ключ не изменился.
    user-set-address)
        case "$name" in
            anna|bob|proba|olga) ;;
            *) printf '{"status":"error","message":"Пользователь %s не найден","code":"user_not_found"}\n' "$name"; exit 3 ;;
        esac
        machine_ips="203.0.113.10"
        [ "$EDGE" = "multi" ] && machine_ips="203.0.113.10 203.0.113.11 203.0.113.12"
        if [ -n "$ip_arg" ]; then
            case " $machine_ips " in
                *" $ip_arg "*) ;;
                *) printf '{"status":"error","message":"Адреса %s на этом сервере нет — ключ с ним не соединится. Адреса сервера: %s","code":"address_not_here"}\n' "$ip_arg" "$machine_ips"
                   exit 2 ;;
            esac
        fi
        pinned="$ip_arg"
        [ "$ip_arg" = "203.0.113.10" ] && pinned=""
        pin_resend=true
        [ "${FAKE_BOT_PIN_SAME:-0}" = "1" ] && pin_resend=false
        printf '{"status":"success","message":"Адрес задан.","name":"%s","endpoint_ip":"%s","previous_endpoint_ip":"%s","endpoint":"%s","resend_required":%s,"domain_endpoint":false}\n' \
            "$name" "$pinned" "${FAKE_BOT_USER_IP:-}" "${pinned:-203.0.113.10}" "$pin_resend"
        ;;
    agent-add)
        printf '{"status":"success","message":"Доступ a-9f9f выдан","id":"a-9f9f","label":"%s","scope":"%s","enabled":true,"expires":"","port":0,"allow_ips":[],"last_used":"","last_ip":"","calls":0,"url":"https://203.0.113.10/a-NEWSECRETNEWSECRETNEWSECRETNEWSECRETNEWSEC/mcp","via":"ip","cert_fingerprint_sha256":"AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99","transport_enabled":true,"reachable":true,"hint":"","warning":"Адрес — это ключ."}\n' "$label" "${scope:-write}"
        ;;
    mcp-http-status)
        # FAKE_BOT_MCP_MODE=lite — как в Lite без сайта: транспорт выключен, порта нет
        if [ "${FAKE_BOT_MCP_MODE:-}" = "lite" ]; then
            echo '{"status":"success","message":"ok","enabled":false,"mode":"lite","listen":"none","bind":"","port":0,"tls":false,"http_port":1986,"public_port":0,"domain":"","server_ip":"203.0.113.10","base_url":"","via":"","cert_fingerprint_sha256":"","nginx_location":false,"unit":"goamnezia-mcp-http.service","unit_state":"missing","reachable":false,"hint":"HTTP-транспорт выключен","agents":0,"agents_active":0,"agents_with_port":0}'
        else
            echo '{"status":"success","message":"ok","enabled":true,"mode":"lite_site","listen":"nginx","bind":"127.0.0.1","port":1986,"tls":false,"http_port":1986,"public_port":0,"domain":"","server_ip":"203.0.113.10","base_url":"https://203.0.113.10","via":"ip","cert_fingerprint_sha256":"AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99","nginx_location":true,"unit":"goamnezia-mcp-http.service","unit_state":"active","reachable":true,"hint":"","agents":2,"agents_active":1,"agents_with_port":0}'
        fi
        ;;
    mcp-http-on)
        if [ -n "$port" ]; then
            printf '{"status":"success","message":"HTTP-транспорт MCP включён.","enabled":true,"mode":"lite","listen":"public_port","bind":"0.0.0.0","port":%s,"tls":true,"http_port":1986,"public_port":%s,"domain":"","server_ip":"203.0.113.10","base_url":"https://203.0.113.10:%s","via":"port","cert_fingerprint_sha256":"AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99","nginx_location":false,"unit":"goamnezia-mcp-http.service","unit_state":"active","reachable":true,"hint":"Порт открыт в локальном файрволе","agents":0,"agents_active":0,"agents_with_port":0}\n' "$port" "$port" "$port"
        elif [ "${FAKE_BOT_MCP_MODE:-}" = "lite" ]; then
            echo '{"status":"error","message":"В Lite без сайта HTTP-транспорту негде жить","code":"needs_site"}'
            exit 1
        else
            echo '{"status":"success","message":"HTTP-транспорт MCP включён.","enabled":true,"mode":"lite_site","listen":"nginx","bind":"127.0.0.1","port":1986,"tls":false,"http_port":1986,"public_port":0,"domain":"","server_ip":"203.0.113.10","base_url":"https://203.0.113.10","via":"ip","cert_fingerprint_sha256":"AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99","nginx_location":true,"unit":"goamnezia-mcp-http.service","unit_state":"active","reachable":true,"hint":"","agents":2,"agents_active":1,"agents_with_port":0}'
        fi
        ;;
    mcp-http-off)
        echo '{"status":"success","message":"HTTP-транспорт MCP выключен.","enabled":false,"mode":"lite_site","listen":"nginx","bind":"127.0.0.1","port":1986,"tls":false,"http_port":1986,"public_port":0,"domain":"","server_ip":"203.0.113.10","base_url":"https://203.0.113.10","via":"ip","cert_fingerprint_sha256":"","nginx_location":false,"unit":"goamnezia-mcp-http.service","unit_state":"missing","reachable":false,"hint":"HTTP-транспорт выключен","agents":2,"agents_active":1,"agents_with_port":0}'
        ;;
    # ── Временная ссылка на файл (lib/share.sh): адрес с секретом, как в docs/SHARE.md §3 ──
    # Адрес и секрет вымышленные: 203.0.113.10 — документационная сеть RFC 5737.
    share-create)
        if [ "$format" != "conf" ] && [ "$format" != "vpn" ]; then
            printf '{"status":"error","message":"Формат %s не поддержан.","code":"invalid_format"}\n' "$format"
            exit 1
        fi
        if no_private_key_refusal; then
            exit 3
        fi
        if endpoint_refusal; then
            exit 2
        fi
        # Разовый адрес сервера — в имени файла и в поле endpoint (пусто = как обычно)
        fbase="${name}-${profile}"
        [ -n "$endpoint" ] && fbase="${fbase}-${endpoint}"
        if [ "$MODE" = "pro" ]; then
            printf '{"status":"success","message":"ok","id":"s-4b1e","url":"https://vpn.example.com:38472/d-FAKESECRETFAKESECRETFAKESECRETFAKE/%s-%s.%s","path":"/d-FAKESECRETFAKESECRETFAKESECRETFAKE/%s-%s.%s","port":38472,"host":"vpn.example.com","via":"domain","format":"%s","file_name":"%s.%s","size":712,"created":"2026-09-10T18:41:07Z","expires":"2026-09-10T18:46:07Z","ttl":300,"max_downloads":0,"cert":"letsencrypt","fingerprint":"","selfsigned":false,"endpoint":"%s"}\n' \
                "$name" "$profile" "$format" "$name" "$profile" "$format" "$format" "$fbase" "$format" "$endpoint"
        else
            printf '{"status":"success","message":"ok","id":"s-4b1e","url":"https://203.0.113.10:38472/d-FAKESECRETFAKESECRETFAKESECRETFAKE/%s-%s.%s","path":"/d-FAKESECRETFAKESECRETFAKESECRETFAKE/%s-%s.%s","port":38472,"host":"203.0.113.10","via":"ip","format":"%s","file_name":"%s.%s","size":712,"created":"2026-09-10T18:41:07Z","expires":"2026-09-10T18:46:07Z","ttl":300,"max_downloads":0,"cert":"selfsigned","fingerprint":"3A:9F:12:B7:04:CE:88:1D:6E:52:AB:77:30:F4:9C:21:55:E0:8B:46:D2:7A:13:C9:60:EF:35:A8:74:1B:5D:92","selfsigned":true,"endpoint":"%s"}\n' \
                "$name" "$profile" "$format" "$name" "$profile" "$format" "$format" "$fbase" "$format" "$endpoint"
        fi
        ;;
    share-list)
        if [ "${FAKE_BOT_SHARES:-1}" = "0" ]; then
            echo '{"status":"success","message":"ok","shares":[],"count":0}'
        else
            # Секретной части пути в списке нет — так и у настоящего share_list
            echo '{"status":"success","message":"ok","shares":[{"id":"s-4b1e","name":"anna","profile":"awg2","format":"conf","file_name":"anna-awg2.conf","port":38472,"host":"203.0.113.10","via":"ip","size":712,"created":"2026-09-10T18:41:07Z","expires":"2026-09-10T18:46:07Z","ttl":300,"max_downloads":0,"cert":"selfsigned","downloads":1,"seconds_left":214},{"id":"s-7c2d","name":"bob","profile":"awg2","format":"vpn","file_name":"bob-awg2.vpn.txt","port":41003,"host":"203.0.113.10","via":"ip","size":320,"created":"2026-09-10T18:42:00Z","expires":"2026-09-10T18:47:00Z","ttl":300,"max_downloads":0,"cert":"selfsigned","downloads":0,"seconds_left":30},{"id":"s-99","name":"bad","profile":"awg2","format":"conf","file_name":"x","port":1,"host":"","via":"ip","size":0,"created":"","expires":"","ttl":0,"max_downloads":0,"cert":"","downloads":0,"seconds_left":0}],"count":3}'
        fi
        ;;
    share-revoke)
        printf '{"status":"success","message":"Раздача %s снята.","id":"%s","revoked":1}\n' "$agent_id" "$agent_id"
        ;;
    share-revoke-all)
        echo '{"status":"success","message":"Все раздачи сняты.","revoked":2}'
        ;;
    # ── Режим, сайт, шаблон, панель на домене (lib/website.sh, lib/preflight.sh) ──
    change-mode)
        if [ "$mode_arg" = "pro" ] && [ -z "$domain_arg" ] && [ "$MODE" != "pro" ]; then
            echo '{"status":"error","message":"Домен не задан.","code":"no_domain"}'
            exit 1
        fi
        if [ -n "${FAKE_BOT_CERT_FAIL:-}" ] && [ -n "$domain_arg" ]; then
            case "$FAKE_BOT_CERT_FAIL" in
                nocause)     cause_json='' ;;
                limit_until) cause_json=',"cert_cause":"limit","cert_retry_after":"2026-09-27 10:00"' ;;
                *)           cause_json=',"cert_cause":"'"$FAKE_BOT_CERT_FAIL"'","cert_retry_after":""' ;;
            esac
            printf '{"status":"error","message":"Не удалось выпустить сертификат для %s. СЛОВА-ЯДРА","code":"cert_failed"%s}\n' \
                "$domain_arg" "$cause_json"
            exit 1
        fi
        printf '{"status":"success","message":"Режим переключён: %s.","mode":"%s","previous_mode":"%s","domain":"%s","template":null,"site":null}\n' \
            "$mode_arg" "$mode_arg" "$MODE" "${domain_arg:-$DOMAIN}"
        ;;
    change-template)
        if [ -z "$template" ]; then
            echo '{"status":"error","message":"Шаблон не найден.","code":"unknown_template"}'
            exit 1
        fi
        printf '{"status":"success","message":"Шаблон сайта: %s.","template":{"template_id":"%s","template_source":"html5up"}}\n' "$template" "$template"
        ;;
    site-check)
        if [ "$MODE" = "lite" ]; then
            echo '{"status":"success","message":"ok","verdict":"not_pro","site":{"domain":"","mode":"lite","server_ip":"203.0.113.10","url":"","dns_ip":"","dns_ok":false,"listen":"public","http_code":0,"https_code":0,"https_ok":false,"nginx_active":false,"index_present":false,"cert":"","cert_fingerprint_sha256":"","cert_expires":"","cert_days_left":null,"cert_ok":false,"verdict":"not_pro","dry_run":false,"checked_at":"2026-09-10T18:00:00Z"}}'
        else
            printf '{"status":"success","message":"ok","verdict":"ok","site":{"domain":"%s","mode":"%s","server_ip":"203.0.113.10","url":"https://203.0.113.10/","dns_ip":"203.0.113.10","dns_ok":true,"listen":"public","http_code":301,"https_code":200,"https_ok":true,"nginx_active":true,"index_present":true,"cert":"selfsigned","cert_fingerprint_sha256":"3A:9F:12:B7:04:CE","cert_expires":"2027-01-01","cert_days_left":300,"cert_ok":true,"verdict":"ok","dry_run":false,"checked_at":"2026-09-10T18:00:00Z"}}\n' "$DOMAIN" "$MODE"
        fi
        ;;
    preflight)
        if [ "${FAKE_BOT_PREFLIGHT:-free}" = "busy" ]; then
            echo '{"status":"success","message":"ok","modes":{"lite":{"available":true},"lite_site":{"available":false,"reasons":[{"port":443,"proto":"tcp","state":"busy","process":"nginx","pid":812,"unit":"nginx.service","container":"","product":"gotelegram","firewall_source":"ufw","message":"TCP/443 занят: nginx (PID 812, юнит nginx.service) — похоже, это goTelegram Pro"}]},"pro":{"available":false,"reasons":[{"port":443,"proto":"tcp","state":"busy","process":"nginx","pid":812,"unit":"nginx.service","container":"","product":"gotelegram","firewall_source":"ufw","message":"TCP/443 занят: nginx (PID 812, юнит nginx.service)"}]},"warnings":[{"code":"udp443_busy","port":443,"proto":"udp","process":"xray","pid":1700,"unit":"x-ui.service","container":"","product":"xui","message":"UDP/443 занят: xray (PID 1700) — профиль по умолчанию получит случайный порт"}],"hints":{"free_cmd":"systemctl disable --now nginx.service","open_cmd":""}},"lite_site_available":false,"pro_available":false,"hint":"systemctl disable --now nginx.service"}'
        else
            echo '{"status":"success","message":"ok","modes":{"lite":{"available":true},"lite_site":{"available":true},"pro":{"available":true},"warnings":[],"hints":{"free_cmd":"","open_cmd":""}},"lite_site_available":true,"pro_available":true,"hint":""}'
        fi
        ;;
    site-alt-svc)
        applied=false
        [ "$MODE" != "lite" ] && applied=true
        on=false
        [ "$state" = "on" ] && on=true
        printf '{"status":"success","message":"Заголовок Alt-Svc: %s.","alt_svc":%s,"applied":%s,"mode":"%s"}\n' "$state" "$on" "$applied" "$MODE"
        ;;
    miniapp-status)
        on=false
        [ "${FAKE_BOT_MINIAPP:-off}" = "on" ] && on=true
        printf '{"status":"success","message":"ok","enabled":%s,"mode":"%s","domain":"%s","panel_port":1985,"site":null}\n' "$on" "$MODE" "$DOMAIN"
        ;;
    miniapp-on|miniapp-rotate)
        if [ "$MODE" != "pro" ]; then
            echo '{"status":"error","message":"Панель на домене доступна только в Pro.","code":"not_pro"}'
            exit 1
        fi
        printf '{"status":"success","message":"Панель на домене открыта.","enabled":true,"changed":true,"path":"app-FAKEPATH0001","url":"https://vpn.example.com/app-FAKEPATH0001/","hint":"Вход по коду из бота."}\n'
        ;;
    miniapp-off)
        printf '{"status":"success","message":"Панель на домене закрыта.","enabled":false,"mode":"%s"}\n' "$MODE"
        ;;
    lang-set)
        printf '{"status":"success","message":"Язык интерфейсов: %s.","lang":"%s"}\n' "$lang" "$lang"
        ;;
    # ── Страница подключения и зеркало сборок (docs/CLIENTS.md §9) ──
    # Поля — ровно по таблице действий моста; ядро пишется параллельно, и эта
    # заглушка — договор интерфейсов с ним. Адрес вымышленный (example.com),
    # секрет — 43 знака base64url, как у настоящего модуля. Страница живёт
    # только с доменом и настоящим сертификатом: в lite и lite_site (IP и
    # самоподписанный) действие отвечает pages_need_domain.
    clients-list)
        echo '{"status":"success","message":"ok","checked":"2026-09-17","apps":[{"id":"amneziavpn","name":"AmneziaVPN","developer":"Amnezia","official":true,"license":"GPL-3.0","platforms":{"ios":{"links":[{"kind":"appstore","url":"https://apps.apple.com/app/id1600529900"}],"support":{"1.0":"yes","2.0":"yes","3.1":"5.0.1.5"},"note":"not in the Russian App Store"},"android":{"links":[{"kind":"googleplay","url":"https://play.google.com/store/apps/details?id=org.amnezia.vpn"},{"kind":"github","url":"https://github.com/amnezia-vpn/amnezia-client/releases"}],"support":{"1.0":"yes","2.0":"yes","3.1":"5.0.1.5"},"note":""},"windows":{"links":[{"kind":"github","url":"https://github.com/amnezia-vpn/amnezia-client/releases"}],"support":{"1.0":"yes","2.0":"yes","3.1":"5.0.1.5"},"note":""},"macos":{"links":[{"kind":"github","url":"https://github.com/amnezia-vpn/amnezia-client/releases"}],"support":{"1.0":"yes","2.0":"yes","3.1":"5.0.1.5"},"note":""},"linux":{"links":[{"kind":"github","url":"https://github.com/amnezia-vpn/amnezia-client/releases"}],"support":{"1.0":"yes","2.0":"yes","3.1":"5.0.1.5"},"note":""}},"import":["vpn","qr-vpn"],"mirror":{"repo":"amnezia-vpn/amnezia-client","assets":{"android":"AmneziaVPN_.*\\.apk$","windows":"AmneziaVPN_.*_x64\\.exe$","macos":"AmneziaVPN_.*\\.dmg$","linux":"AmneziaVPN_.*\\.tar\\.zip$"},"license":"GPL-3.0"}},{"id":"amneziawg","name":"AmneziaWG","developer":"Amnezia","official":true,"license":"MIT","platforms":{"ios":{"links":[{"kind":"appstore","url":"https://apps.apple.com/app/id6478942365"}],"support":{"1.0":"yes","2.0":"yes","3.1":"3.1.4"},"note":""},"android":{"links":[{"kind":"googleplay","url":"https://play.google.com/store/apps/details?id=org.amnezia.awg"},{"kind":"github","url":"https://github.com/amnezia-vpn/amneziawg-android/releases"}],"support":{"1.0":"yes","2.0":"yes","3.1":"3.1.20260814"},"note":""},"windows":{"links":[{"kind":"github","url":"https://github.com/amnezia-vpn/amneziawg-windows-client/releases"}],"support":{"1.0":"yes","2.0":"yes","3.1":"3.1.0"},"note":""}},"import":["conf","qr-conf"],"mirror":{"repo":"amnezia-vpn/amneziawg-android","assets":{"android":"amneziawg-android-.*\\.apk$","windows":"amneziawg-.*\\.msi$"},"license":"MIT"}},{"id":"wgtunnel","name":"WG Tunnel","developer":"Zane Schepke","official":false,"license":"MIT","platforms":{"android":{"links":[{"kind":"github","url":"https://github.com/wgtunnel/android/releases"},{"kind":"izzy","url":"https://apt.izzysoft.de/fdroid/index/apk/com.zaneschepke.wireguardautotunnel"}],"support":{"1.0":"yes","2.0":"yes","3.1":"5.6.0"},"note":""}},"import":["conf","qr-conf"],"mirror":{"repo":"wgtunnel/android","assets":{"android":"wgtunnel-.*\\.apk$"},"license":"MIT"}},{"id":"defaultvpn","name":"DefaultVPN","developer":"Amnezia","official":true,"license":"closed","platforms":{"ios":{"links":[{"kind":"appstore","url":"https://apps.apple.com/app/id6744725017"}],"support":{"1.0":"yes","2.0":"yes","3.1":"2.0.1"},"note":""}},"import":["vpn","qr-vpn"],"mirror":null}]}'
        ;;
    page-create)
        if [ "$MODE" != "pro" ]; then
            echo '{"status":"error","message":"Страница подключения живёт только с сайтом на домене и настоящим сертификатом.","code":"pages_need_domain"}'
            exit 1
        fi
        case "$ttl" in
            forever|[1-9]h|[1-9][0-9]h|[1-9][0-9][0-9]h|[1-9]d|[1-9][0-9]d|[1-9][0-9][0-9]d) ;;
            *) printf '{"status":"error","message":"Недопустимый срок: %s","code":"invalid_ttl"}\n' "$ttl"; exit 1 ;;
        esac
        case "$name" in
            anna|bob|proba|olga) ;;
            *) printf '{"status":"error","message":"Пользователь %s не найден","code":"user_not_found"}\n' "$name"
               exit 1 ;;
        esac
        # bob выключен (user-show): страница не откроется — как у настоящего pages_create
        if [ "$name" = "bob" ]; then
            echo '{"status":"error","message":"Пользователь bob выключен — страница не откроется; включите его (user-enable) и повторите","code":"user_disabled"}'
            exit 1
        fi
        # У olga единственный ключ — из приложения Amnezia: страницу без единого ключа ядро не создаёт
        if [ "$name" = "olga" ] && [ "$(has_key_of olga awg2)" = "false" ]; then
            echo '{"status":"error","message":"Ключи olga (профили: awg2) выданы в приложении Amnezia ещё до подхвата, и на сервере их нет — страница не показала бы человеку ни одного ключа. Привяжите старый ключ в карточке человека или перевыпустите ключ — тогда старый сразу перестанет подключаться","code":"no_private_key"}'
            exit 3
        fi
        imported='[]'
        [ "$(has_key_of "$name" awg2)" = "false" ] && imported='["awg2"]'
        forever=false; expires="2026-09-18T18:41:07Z"
        [ "$ttl" = "forever" ] && { forever=true; expires=""; }
        printf '{"status":"success","message":"ok","id":"p-7f3a","url":"https://vpn.example.com/p-FAKESECRETFAKESECRETFAKESECRETFAKESECRET012/","user":"%s","created":"2026-09-17T18:41:07Z","expires":"%s","forever":%s,"profiles":["awg2","awg3"],"mirror":true,"imported_profiles":%s}\n' \
            "$name" "$expires" "$forever" "$imported"
        ;;
    page-list)
        # Без адреса — так и у настоящего page-list (§9). Запись с мусорным id
        # интерфейс обязан отбросить, как s-99 у раздач.
        if [ "${FAKE_BOT_PAGES:-some}" = "empty" ]; then
            echo '{"status":"success","message":"ok","pages":[]}'
            exit 0
        fi
        anna='{"id":"p-7f3a","user":"anna","created":"2026-09-17T18:41:07Z","expires":"2026-09-18T18:41:07Z","forever":false,"visits":3,"last_visit":"2026-09-17T19:02:11Z","profiles":["awg2","awg3"],"mirror":true,"subscription":"created"},{"id":"p-9c1d","user":"anna","created":"2026-09-10T08:00:00Z","expires":"","forever":true,"visits":'"${FAKE_BOT_P9_VISITS:-0}"',"last_visit":"","profiles":["awg2"],"mirror":false}'
        bob='{"id":"p-0bad","user":"bob","created":"2026-09-16T12:00:00Z","expires":"2026-10-16T12:00:00Z","forever":false,"visits":1,"last_visit":"2026-09-16T12:30:00Z","profiles":["awg2"],"mirror":true},{"id":"BAD id!","user":"bob","created":"","expires":"","forever":false,"visits":0,"last_visit":"","profiles":[],"mirror":false}'
        # Страница человека с ключом из приложения на awg2: там пояснение вместо ключа
        proba='{"id":"p-5a5a","user":"proba","created":"2026-09-20T09:00:00Z","expires":"2026-09-27T09:00:00Z","forever":false,"visits":0,"last_visit":"","profiles":["awg2","awg3"],"mirror":true}'
        # У olga остался только ключ из приложения, а страница создана раньше (UI-4): живая, с одним
        # пояснением — её должно быть видно и можно отозвать, хоть новую такую и не создать
        # FAKE_BOT_OLGA_PAGE_SUB=exists — у страницы olga снимок подписки (П-3: sub-list не ответил — по снимку).
        olga='{"id":"p-0a1b","user":"olga","created":"2026-09-19T09:00:00Z","expires":"","forever":true,"visits":2,"last_visit":"2026-09-24T21:00:00Z","profiles":["awg2"],"mirror":false'"${FAKE_BOT_OLGA_PAGE_SUB:+,\"subscription\":\"${FAKE_BOT_OLGA_PAGE_SUB}\"}"'}'
        case "$name" in
            anna)  rows="$anna" ;;
            bob)   rows="$bob" ;;
            proba) rows="$proba" ;;
            olga)  rows="$olga" ;;
            "")    rows="$anna,$bob,$proba,$olga" ;;
            *)     rows="" ;;
        esac
        printf '{"status":"success","message":"ok","pages":[%s]}\n' "$rows"
        ;;
    page-url)
        case "$agent_id" in
            p-7f3a|p-9c1d|p-0bad|p-0a1b)
                printf '{"status":"success","message":"ok","id":"%s","url":"https://vpn.example.com/p-FAKESECRETFAKESECRETFAKESECRETFAKESECRET012/"}\n' "$agent_id" ;;
            *) printf '{"status":"error","message":"Страница %s не найдена.","code":"page_not_found"}\n' "$agent_id"; exit 1 ;;
        esac
        ;;
    page-revoke)
        case "$agent_id" in
            # Подписка есть только у anna (как в sub-list; FAKE_BOT_SUBS=empty — ни у кого): sub_present — была ли она
            # при отзыве с sub=on (R-4); нет — sub_rotated: false без ошибки.
            # sub=off — sub_present: null (подписку не спрашивали, RR-2).
            p-7f3a|p-9c1d|p-0bad|p-0a1b) rot=false pres=null
                [ "$sub_arg" = on ] && pres=false
                if [ "$sub_arg" = on ] && [ "${FAKE_BOT_SUBS:-some}" != empty ] && { [ "$agent_id" = p-7f3a ] || [ "$agent_id" = p-9c1d ]; }; then
                    rot=true pres=true
                fi
                # keys=on (B3-3) — ключи на профилях страницы: у p-7f3a — awg2 и awg3
                kr=null kf=null
                [ "$keys_arg" = on ] && { kr='["awg2","awg3"]'; kf='[]'; }
                printf '{"status":"success","message":"Страница %s отозвана.","id":"%s","sub_rotated":%s,"sub_present":%s,"keys_reissued":%s,"keys_failed":%s}\n' "$agent_id" "$agent_id" "$rot" "$pres" "$kr" "$kf" ;;
            *) printf '{"status":"error","message":"Страница %s не найдена.","code":"page_not_found"}\n' "$agent_id"; exit 1 ;;
        esac
        ;;
    page-revoke-all)
        echo '{"status":"success","message":"Страницы отозваны.","revoked":2}'
        ;;
    # Подписка (lib/subs.sh, docs/SUBSCRIPTION.md): sub-list без адресов; адреса — только в ответах sub-create,
    # sub-show и sub-rotate. Живёт только с доменом и настоящим сертификатом: вне pro — subs_need_domain.
    # FAKE_BOT_SUBS — some (по умолчанию) | empty — есть ли подписка у anna в sub-list.
    # FAKE_BOT_SUB_CLIENTS=1 — ответы sub-create, sub-show и sub-rotate с выбором по клиенту: фикстура
    # tests/fixtures/sub_clients.json одной строкой (поле clients); без переменной — прежний ответ без clients.
    # FAKE_BOT_SUB_SHORT=1 — вместе с ним: в ответе short_available: true (короткие ссылки есть — бот настроен).
    sub-list)
        # FAKE_BOT_SUBS=fail — sub-list не ответил (П-3).
        if [ "${FAKE_BOT_SUBS:-some}" = "fail" ]; then
            echo '{"status":"error","message":"sub-list недоступен","code":"internal"}'
            exit 1
        fi
        ready=false; [ "$MODE" = "pro" ] && ready=true
        anna='{"name":"anna","label":"Анна","created":"2026-10-03T10:00:00Z","rotated":"","generation":1,"fetches":3,"last_fetch":"2026-10-03T12:00:00Z","last_suffix":"mihomo.yaml","last_client":"clash-verge","lines":[{"line":"3.1","profile":"awg3","has_key":true,"active":true,"token":"awg31"},{"line":"2.0","profile":"awg2","has_key":true,"active":true,"token":"awg20"}],"disabled":false}'
        rows=""
        if [ "${FAKE_BOT_SUBS:-some}" != "empty" ]; then
            case "$name" in anna|"") rows="$anna" ;; esac
        fi
        printf '{"status":"success","message":"ok","subs":[%s],"count":%s,"domain_ready":%s,"update_interval":12}\n' \
            "$rows" "$([ -n "$rows" ] && echo 1 || echo 0)" "$ready"
        ;;
    sub-create|sub-show|sub-rotate)
        if [ "$MODE" != "pro" ]; then
            echo '{"status":"error","message":"Подписка работает только на сайте с доменом и доверенным сертификатом.","code":"subs_need_domain"}'
            exit 2
        fi
        case "$name" in
            anna|bob|proba) ;;
            olga) echo '{"status":"error","message":"Ключи olga (профили: awg2) выданы в приложении Amnezia ещё до подхвата, и на сервере их нет — подписке нечего отдавать.","code":"no_private_key"}'
                  exit 3 ;;
            *) printf '{"status":"error","message":"Пользователь %s не найден","code":"user_not_found"}\n' "$name"; exit 3 ;;
        esac
        if [ "${FAKE_BOT_SUB_CLIENTS:-0}" = 1 ] && [ "${FAKE_BOT_SUB_SHORT:-0}" = 1 ]; then
            python3 -c 'import json, sys; r = json.load(open(sys.argv[1], encoding="utf-8")); r["short_available"] = True; print(json.dumps(r, ensure_ascii=False))' \
                "$(dirname "$0")/sub_clients.json"
            exit 0
        fi
        if [ "${FAKE_BOT_SUB_CLIENTS:-0}" = 1 ]; then
            tr -d '\n' < "$(dirname "$0")/sub_clients.json"
            echo
            exit 0
        fi
        secret="FAKESUBSECRETFAKESUBSECRETFAKESUBSECRET0123"
        [ "$action" = "sub-rotate" ] && secret="ROTATEDSECRETROTATEDSECRETROTATEDSECRET0123"
        new=false; [ "$action" = "sub-create" ] && new=true
        base="https://vpn.example.com/s-$secret/"
        printf '{"status":"success","message":"ok","name":"%s","label":"Анна","created":"2026-10-03T10:00:00Z","rotated":"","generation":1,"fetches":0,"last_fetch":"","last_suffix":"","last_client":"","lines":[{"line":"3.1","profile":"awg3","has_key":true,"active":true,"token":"awg31"},{"line":"2.0","profile":"awg2","has_key":true,"active":true,"token":"awg20"}],"disabled":false,"base_url":"%s","urls":[{"suffix":"mihomo.yaml","format":"mihomo","line":"","url":"%smihomo.yaml"},{"suffix":"awg31.conf","format":"conf","line":"3.1","url":"%sawg31.conf"},{"suffix":"awg20.conf","format":"conf","line":"2.0","url":"%sawg20.conf"},{"suffix":"awg31.vpn","format":"vpn","line":"3.1","url":"%sawg31.vpn"},{"suffix":"awg20.vpn","format":"vpn","line":"2.0","url":"%sawg20.vpn"},{"suffix":"all.vpn","format":"vpn","line":"","url":"%sall.vpn"}],"new":%s,"paused":false,"update_interval":12}\n' \
            "$name" "$base" "$base" "$base" "$base" "$base" "$base" "$base" "$new"
        ;;
    sub-revoke)
        case "$name" in
            anna|bob|proba) printf '{"status":"success","message":"ok","name":"%s","revoked":true}\n' "$name" ;;
            *) printf '{"status":"error","message":"У пользователя %s нет подписки","code":"sub_not_found"}\n' "$name"; exit 3 ;;
        esac
        ;;
    # Короткие ссылки с 2FA через бота (договор docs/SUBSCRIPTION.md): ответы — поле actions фикстуры
    # tests/fixtures/short_links.json. FAKE_BOT_SHORT=need_bot — short-ensure и short-list отвечают short_need_bot;
    # FAKE_BOT_SHORT_DECIDE — итог short-decide: ok (по умолчанию; форма договора — итог полем status, с новым кодом) |
    # wrong | denied | swapped (с новым кодом) | expired | frozen | merged (status success моста, итог полем decision) |
    # already (решено раньше в панели: decision ok, already: true, by panel) | error (отказ моста, как у ядра:
    # short_request_not_found со словами ядра); FAKE_BOT_SHORT_BANS — some (по умолчанию) | empty.
    # FAKE_BOT_SHORT_FAIL — ответ моста целиком (JSON отказа) у действия FAKE_BOT_SHORT_FAIL_ACTION (по умолчанию
    # short-replace-request): отказы ядра словами и кодом, как их отдаёт мост (SL-UX-03).
    short-ensure|short-list|short-replace-request|short-decide|short-status|short-pending|short-bans|short-unban)
        if [ "${FAKE_BOT_SHORT:-}" = need_bot ] && { [ "$action" = short-ensure ] || [ "$action" = short-list ]; }; then
            echo '{"status":"error","message":"Короткие ссылки работают только с 2FA через Telegram-бота.","code":"short_need_bot"}'
            exit 2
        fi
        if [ -n "${FAKE_BOT_SHORT_FAIL:-}" ] && [ "$action" = "${FAKE_BOT_SHORT_FAIL_ACTION:-short-replace-request}" ]; then
            printf '%s\n' "$FAKE_BOT_SHORT_FAIL"
            exit 2
        fi
        python3 - "$(dirname "$0")/short_links.json" "$action" "${FAKE_BOT_SHORT_DECIDE:-ok}" "${FAKE_BOT_SHORT_BANS:-some}" <<'PY'
import json
import sys
fx = json.load(open(sys.argv[1], encoding="utf-8"))
action, decide, bans = sys.argv[2], sys.argv[3], sys.argv[4]
r = dict(fx["actions"][action])
if action == "short-decide":
    if decide == "error":
        r = {"status": "error", "code": "short_request_not_found",
             "message": "Запрос подтверждения не найден: его не было или он закрыт больше часа назад"}
    elif decide == "merged":
        r = {"status": "success", "message": "ok", "decision": "ok", "display": r["display"], "url": r["url"]}
    elif decide == "already":
        r = {"status": "success", "message": "ok", "decision": "ok", "already": True, "by": "panel"}
    else:
        r["status"] = decide
        if decide not in ("ok", "swapped"):
            r.pop("display", None)
            r.pop("url", None)
if action == "short-bans" and bans == "empty":
    r["bans"] = []
print(json.dumps(r, ensure_ascii=False))
PY
        [ "$action" = short-decide ] && [ "${FAKE_BOT_SHORT_DECIDE:-ok}" = error ] && exit 3
        ;;
    mirror-list)
        # Три состояния сразу: готовая копия, идущая загрузка (bytes из общего
        # size — интерфейсы считают проценты), сорвавшаяся с кодом ошибки.
        if [ "${FAKE_BOT_MIRROR:-some}" = "empty" ]; then
            echo '{"status":"success","message":"ok","max_total_mb":3072,"used_mb":0,"items":[]}'
            exit 0
        fi
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
    # ── Привязать старый ключ (docs/CONTRACT.md § user-attach-key): ключ пришёл в stdin ──
    user-attach-key)
        case "$name" in
            anna|bob|proba|olga) ;;
            *) printf '{"status":"error","message":"Пользователь %s не найден","code":"user_not_found"}\n' "$name"
               exit 3 ;;
        esac
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
        if [ "$(has_key_of "$name" "$profile")" = "true" ]; then
            printf '{"status":"error","message":"Ключ %s на профиле %s уже есть на сервере — привязывать нечего","code":"already_has_key"}\n' "$name" "$profile"
            exit 2
        fi
        if [ -n "${FAKE_BOT_STATE_DIR:-}" ]; then
            : > "${FAKE_BOT_STATE_DIR}/attached-${name}-${profile}"
        fi
        printf '{"status":"success","message":"Старый ключ %s на профиле %s привязан: теперь его можно показать и отправить отсюда. У человека ничего не изменилось.","name":"%s","profile":"%s","line":"2.0"}\n' \
            "$name" "$profile" "$name" "$profile"
        ;;
    # ── Адреса машины (lib/edgeips.sh): вердикты сторожа и варианты адреса для ключа ──
    # key_choices — ровно то, что примет --endpoint: обычный первым, потом домены, потом IP,
    # «замолчавший» в конце; у домена verdict/users/in_dns — null, у машины с одним адресом verdict — null.
    edge-status)
        if [ "$EDGE" = "multi" ]; then
            echo '{"status":"success","message":"ok","count":3,"domain_endpoint":false,"ips":[{"ip":"203.0.113.10","verdict":"ok","users":2,"packets_15m":1200,"packets_1h":5000,"packets_day":90000,"silent_since":""},{"ip":"203.0.113.11","verdict":"ok","users":0,"packets_15m":300,"packets_1h":900,"packets_day":20000,"silent_since":""},{"ip":"203.0.113.12","verdict":"dark","users":1,"packets_15m":0,"packets_1h":0,"packets_day":4000,"silent_since":"2026-09-25T08:00:00Z"}],"keys_host":"203.0.113.10","key_choices":[{"host":"203.0.113.10","kind":"ip","default":true,"verdict":"ok","users":2,"in_dns":null},{"host":"vpn.example.com","kind":"domain","default":false,"verdict":null,"users":null,"in_dns":null},{"host":"203.0.113.11","kind":"ip","default":false,"verdict":"ok","users":0,"in_dns":null},{"host":"203.0.113.12","kind":"ip","default":false,"verdict":"dark","users":1,"in_dns":null}]}'
        else
            echo '{"status":"success","message":"ok","count":1,"domain_endpoint":false,"ips":[{"ip":"203.0.113.10","verdict":null,"users":3,"packets_15m":0,"packets_1h":0,"packets_day":0,"silent_since":""}],"keys_host":"203.0.113.10","key_choices":[{"host":"203.0.113.10","kind":"ip","default":true,"verdict":null,"users":null,"in_dns":null}]}'
        fi
        ;;
    # «🎲 Случайное имя» (docs/MODES_047.md «Имена ключей»): «прилагательное + столица», подпись — на
    # языке --lang, id латиницей. Варианты по кругу, счётчик в FAKE_BOT_STATE_DIR (без него — первый).
    name-suggest)
        sug_mode="${FAKE_BOT_NAME_SUGGEST:-bridge}"
        if [ "$sug_mode" = "unknown" ]; then
            printf '{"status":"error","message":"Неизвестное действие: %s","code":"unknown_action"}\n' "$action"
            exit 2
        fi
        if [ "$sug_mode" = "noexclude" ] && [ -n "$exclude" ]; then
            echo '{"status":"error","message":"У действия name-suggest нет параметра exclude","code":"bad_arg"}'
            exit 2
        fi
        n=0
        if [ -n "${FAKE_BOT_STATE_DIR:-}" ]; then
            n=$(cat "${FAKE_BOT_STATE_DIR}/name-suggest-n" 2>/dev/null || echo 0)
            echo $((n + 1)) > "${FAKE_BOT_STATE_DIR}/name-suggest-n"
        fi
        [ "$sug_mode" = "repeat" ] && n=0
        case $((n % 3)) in
            0) sug_id="foggy_london"; sug_ru="Туманный Лондон"; sug_en="Foggy London" ;;
            1) sug_id="sunny_manila"; sug_ru="Солнечная Манила"; sug_en="Sunny Manila" ;;
            *) sug_id="snowy_ottawa"; sug_ru="Снежная Оттава"; sug_en="Snowy Ottawa" ;;
        esac
        if [ "$sug_mode" != "repeat" ] && [ "$sug_id" = "$exclude" ]; then
            # как ядро: исключённый вариант не предлагается — следующий по кругу
            case "$sug_id" in
                foggy_london) sug_id="sunny_manila"; sug_ru="Солнечная Манила"; sug_en="Sunny Manila" ;;
                sunny_manila) sug_id="snowy_ottawa"; sug_ru="Снежная Оттава"; sug_en="Snowy Ottawa" ;;
                *) sug_id="foggy_london"; sug_ru="Туманный Лондон"; sug_en="Foggy London" ;;
            esac
        fi
        sug_label="$sug_ru"
        [ "$lang" = "en" ] && sug_label="$sug_en"
        if [ "${FAKE_BOT_NAME_SUGGEST:-bridge}" = "okdata" ]; then
            printf '{"ok":true,"data":{"id":"%s","label":"%s"}}\n' "$sug_id" "$sug_label"
        else
            printf '{"status":"success","message":"ok","id":"%s","label":"%s"}\n' "$sug_id" "$sug_label"
        fi
        ;;
    # Ленивый Pro (docs/MODES_047.md, «Договор 0.6.0»): проверка без изменений и весь сценарий одним действием
    lazy-check)
        lz="${FAKE_BOT_LAZY:-pro}"
        if [ "$lz" = "unknown" ]; then
            printf '{"status":"error","message":"Неизвестное действие: %s","code":"unknown_action"}\n' "$action"
            exit 2
        fi
        disp="$domain_arg"
        [ "$domain_arg" = "xn--e1afmkfd.xn--p1ai" ] && disp="пример.рф"
        # by — формулировка предполётной проверки целиком, как у ядра (длинная); у свободного порта — null
        here=true; a='["203.0.113.10"]'; cf=false; plan=pro; reasons='[]'; line443=',"udp443_line":"2.0"'
        p80='{"free":true,"by":null}'; p443='{"free":true,"by":null}'; u443='{"free":true,"by":null}'; apps='[]'
        case "$lz" in
            pro31)
                line443=',"udp443_line":"3.1"'
                apps='[{"name":"amnezia-awg2","line":"2.0","port":51820},{"name":"amnezia-xray","line":"","port":8443}]' ;;
            pro_old)
                line443='' ;;
            semi_pro)
                u443='{"free":false,"by":"xray (PID 1700, юнит x-ui.service) — похоже, это 3x-ui"}'; plan=semi_pro
                reasons='["udp443_busy"]'; line443=',"udp443_line":null'
                apps='[{"name":"amnezia-awg","line":"1.5","port":38000}]' ;;
            nolines)
                plan=semi_pro; reasons='["no_line_for_443"]'; line443=',"udp443_line":null' ;;
            blocked)
                here=false; a='["198.51.100.50"]'; plan=blocked; reasons='["dns_elsewhere","tcp443_busy","mystery_code"]'
                p443='{"free":false,"by":"nginx (PID 812, юнит nginx.service) — похоже, это goTelegram Pro"}'; line443='' ;;
            app)
                plan=blocked; reasons='["tcp443_app"]'; line443=''
                p443='{"free":false,"by":"docker-proxy (PID 2211, контейнер amnezia-xray)"}' ;;
            partial)
                here=false; a='["198.51.100.50","203.0.113.10"]'; plan=blocked; reasons='["dns_partial"]'; line443='' ;;
            dnsunknown)
                here=false; a='[]'; plan=blocked; reasons='["dns_unknown"]'; line443='' ;;
            awg2busy)
                plan=blocked; reasons='["awg2_container_busy"]'; line443=''
                apps='[{"name":"amnezia-awg2","line":"2.0","port":51820}]' ;;
            cf)
                here=false; a='["104.21.0.1","172.67.0.1"]'; cf=true; reasons='["cloudflare_proxied"]' ;;
        esac
        printf '{"status":"success","message":"ok","domain":"%s","domain_display":"%s","points_here":%s,"a":%s,"aaaa":[],"server_ips":["203.0.113.10"],"cloudflare_proxied":%s,"ports":{"tcp80":%s,"tcp443":%s,"udp443":%s},"app_containers":%s,"plan":"%s","reasons":%s%s}\n' \
            "$domain_arg" "$disp" "$here" "$a" "$cf" "$p80" "$p443" "$u443" "$apps" "$plan" "$reasons" "$line443"
        ;;
    lazy-setup)
        ls_mode="${FAKE_BOT_LAZY_SETUP:-ok}"
        if [ "$ls_mode" = "unknown" ]; then
            printf '{"status":"error","message":"Неизвестное действие: %s","code":"unknown_action"}\n' "$action"
            exit 2
        fi
        if [ "$ls_mode" = "error" ]; then
            echo '{"status":"error","message":"TCP/443 занят: nginx (PID 812) — Ленивый Pro не ставится.","code":"lazy_blocked"}'
            exit 1
        fi
        # Отказ проверки до изменений — с планом и причинами, как у ядра (docs/MODES_047.md «По итогам ядра»)
        if [ "$ls_mode" = "refused" ]; then
            printf '{"status":"error","message":"Домен %s указывает не сюда: 198.51.100.50. Адреса этого сервера: 203.0.113.10. Поправьте A-запись у регистратора.","code":"dns_not_here","plan":"blocked","reasons":["dns_elsewhere","tcp443_busy","mystery_code"]}\n' "$domain_arg"
            exit 1
        fi
        # Сорвалось по ходу при выполнимом плане: план и причины тоже есть, но «не встанет» — не про это
        if [ "$ls_mode" = "midway" ]; then
            echo '{"status":"error","message":"Контейнер приложения amnezia-awg2 подхватить не вышло (import_failed).","code":"adopt_failed","plan":"pro","reasons":["cloudflare_proxied"]}'
            exit 1
        fi
        n="${keys_arg:-5}"
        keys=""; i=0
        for k in foggy_london:Туманный-Лондон sunny_manila:Солнечная-Манила snowy_ottawa:Снежная-Оттава green_dublin:Зелёный-Дублин sunny_athens:Солнечные-Афины; do
            [ "$i" -lt "$n" ] || break
            i=$((i + 1))
            kid="${k%%:*}"; klabel="${k#*:}"
            page="\"https://$domain_arg/p-000$i/\""
            # Semi-Pro: у последнего ключа страницы нет (договор: keys[].page — адрес или null)
            [ "$ls_mode" = "semi" ] && [ "$i" -eq "$n" ] && page=null
            keys="${keys:+$keys,}{\"name\":\"$kid\",\"label\":\"$(printf '%s' "$klabel" | tr '-' ' ')\",\"page\":$page}"
        done
        if [ "$ls_mode" = "semi" ]; then
            printf '{"status":"success","message":"Ленивый Pro: UDP 443 занят — режим Semi-Pro.","mode":"lite_site","domain":"%s","udp443":null,"profiles":[{"id":"awg3","line":"3.1","port":38211},{"id":"awg2","line":"2.0","port":41022}],"site":{"template":"h5up_aerial"},"panel_on_domain":false,"keys":[%s],"warnings":["udp443_busy","panel_off"]}\n' "$domain_arg" "$keys"
        elif [ "$ls_mode" = "nolines" ]; then
            # «Lite → Ленивый Pro»: обе линии уже стояли на своих портах — Semi-Pro, UDP 443 свободен
            printf '{"status":"success","message":"Ленивый Pro готов.","mode":"lite_site","domain":"%s","udp443":null,"profiles":[{"id":"awg3","line":"3.1","port":39001},{"id":"awg2","line":"2.0","port":5388}],"site":{"template":"h5up_aerial"},"panel_on_domain":true,"keys":[%s],"warnings":["no_line_for_443"]}\n' "$domain_arg" "$keys"
        elif [ "$ls_mode" = "rare" ]; then
            printf '{"status":"success","message":"Ленивый Pro готов.","mode":"pro","domain":"%s","udp443":{"profile":"awg2","line":"2.0"},"profiles":[{"id":"awg2","line":"2.0","port":443}],"site":{"template":""},"panel_on_domain":true,"keys":[%s],"warnings":["adopt_failed","firewall_open_failed","line_skipped","profile_failed","site_failed","timers_failed"]}\n' "$domain_arg" "$keys"
        else
            printf '{"status":"success","message":"Ленивый Pro готов: %s.","mode":"pro","domain":"%s","udp443":{"profile":"awg2","line":"2.0"},"profiles":[{"id":"awg3","line":"3.1","port":38211},{"id":"awg2","line":"2.0","port":443}],"site":{"template":"h5up_aerial"},"panel_on_domain":true,"keys":[%s],"warnings":["template_fallback","adopted","mystery_warning"]}\n' "$domain_arg" "$domain_arg" "$keys"
        fi
        ;;
    # Линия на UDP 443 в Pro (договор 0.6.0): выбранная — на 443, прежняя (у заглушки awg2) — на случайный порт
    pro-line)
        pl="${FAKE_BOT_PRO_LINE:-ok}"
        if [ "$pl" = "unknown" ]; then
            printf '{"status":"error","message":"Неизвестное действие: %s","code":"unknown_action"}\n' "$action"
            exit 2
        fi
        if [ "$pl" = "error" ]; then
            echo '{"status":"error","message":"Порт 443/udp занят: xray (PID 1700).","code":"port_busy"}'
            exit 2
        fi
        if [ "$pl" = "adopted" ] && [ "$adopted_arg" != "awg2" ]; then
            echo '{"status":"error","message":"Профиль awg2 подхвачен у приложения Amnezia, и у людей на нём (2) ключи выдало приложение — их приватных ключей на сервере нет. Порт не менялся.","code":"adopted_keyless","profile":"awg2"}'
            exit 2
        fi
        if [ "$pl" = "adopted_noname" ] && [ "$adopted_arg" != "$profile" ]; then
            printf '{"status":"error","message":"Профиль %s подхвачен у приложения Amnezia. Порт не менялся.","code":"adopted_keyless"}\n' "$profile"
            exit 2
        fi
        # Ответ — по уточнениям договора 28.09: {mode, udp443:{profile,line}, profiles:[{id,line,port}]}
        case "$profile" in awg3) pl_line=3.1 ;; awg1) pl_line=1.5 ;; *) pl_line=2.0 ;; esac
        if [ "$profile" = "awg2" ]; then
            profs='[{"id":"awg3","line":"3.1","port":39001},{"id":"awg2","line":"2.0","port":443}]'
        else
            profs="[{\"id\":\"$profile\",\"line\":\"$pl_line\",\"port\":443},{\"id\":\"awg2\",\"line\":\"2.0\",\"port\":38211}]"
        fi
        printf '{"status":"success","message":"На UDP 443 — %s.","mode":"pro","udp443":{"profile":"%s","line":"%s"},"profiles":%s}\n' \
            "$profile" "$profile" "$pl_line" "$profs"
        ;;
    users-add-bulk)
        bk="${FAKE_BOT_BULK:-ok}"
        if [ "$bk" = "unknown" ]; then
            printf '{"status":"error","message":"Неизвестное действие: %s","code":"unknown_action"}\n' "$action"
            exit 2
        fi
        if [ "$bk" = "room" ]; then
            echo '{"status":"error","message":"В профиле AmneziaWG 2.0 (awg2) свободно мест: 3 из 254 — 10 человек не поместятся, никого не добавлено.","code":"subnet_full"}'
            exit 1
        fi
        n="${count_arg:-1}"
        done_n="$n"
        if [ "$bk" = "partial" ] && [ "$n" -gt 2 ]; then done_n=2; fi
        list=""
        i=1
        while [ "$i" -le "$done_n" ]; do
            if [ -n "$prefix_arg" ]; then
                list="${list:+$list,}$(printf '{"name":"%s-%02d","label":"%s-%02d"}' "$prefix_arg" "$i" "$prefix_arg" "$i")"
            else
                list="${list:+$list,}$(printf '{"name":"bulk_%02d","label":"Человек %d"}' "$i" "$i")"
            fi
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
        sel=""; many=0; mode="state"; value="$state"
        if [ -n "$prefix_arg" ]; then
            mode="prefix"; value="$prefix_arg"
            [ "$prefix_arg" = "team" ] && sel="team-01 team-02 team-03"
        else
            case "$state" in disabled) sel="bob" ;; quota) many=150 ;; esac
        fi
        cnt=0; list=""; names_js=""
        for n in $sel; do
            cnt=$((cnt + 1))
            list="${list:+$list,}{\"name\":\"$n\",\"label\":\"$n\"}"
            names_js="${names_js:+$names_js,}\"$n\""
        done
        [ "$many" -gt 0 ] && cnt=$many
        if [ "$action" = "users-del-bulk-preview" ]; then
            too_many=false; [ "$cnt" -gt 100 ] && too_many=true
            printf '{"status":"success","message":"Под выбор попадает человек: %s.","mode":"%s","value":"%s","count":%s,"max":100,"too_many":%s,"selected":[%s],"missing":[]}\n' \
                "$cnt" "$mode" "$value" "$cnt" "$too_many" "$list"
            exit 0
        fi
        matched="$cnt"
        [ "${FAKE_BOT_BULK_DEL:-ok}" = "mismatch" ] && matched=$((cnt + 1))
        if [ "$confirm_arg" != "$matched" ]; then
            printf '{"status":"error","message":"Подтверждено %s, а выбрано сейчас %s: список успел измениться — никого не удалено.","code":"confirm_mismatch","matched":%s}\n' \
                "$confirm_arg" "$matched" "$matched"
            exit 1
        fi
        if [ "${FAKE_BOT_BULK_DEL:-ok}" = "partial" ] && [ "$cnt" -gt 1 ]; then
            first="${sel%% *}"; rest="${sel#* }"; stop="${rest%% *}"
            kept_js=$(printf '%s' "$rest" | sed 's/[^ ][^ ]*/"&"/g; s/ /,/g')
            printf '{"status":"success","message":"Удалено 1 из %s","requested":%s,"count":1,"deleted":["%s"],"kept":[%s],"partial":true,"failed":{"name":"%s","code":"user_del_failed","message":"контейнер не принял изменения — пачка оставлена в списке выключенной"},"mode":"%s","value":"%s"}\n' \
                "$cnt" "$cnt" "$first" "$kept_js" "$stop" "$mode" "$value"
            exit 0
        fi
        printf '{"status":"success","message":"Удалено человек: %s.","requested":%s,"count":%s,"deleted":[%s],"kept":[],"partial":false,"failed":null,"mode":"%s","value":"%s"}\n' \
            "$cnt" "$cnt" "$cnt" "$names_js" "$mode" "$value"
        ;;
    *)
        printf '{"status":"success","message":"ok: %s","action":"%s","name":"%s","profile":"%s"}\n' "$action" "$action" "$name" "$profile"
        ;;
esac
exit 0
