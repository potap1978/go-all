#!/usr/bin/env bash
# Заглушка моста `install.sh --action=<имя> --json --k=v …` для тестов MCP-сервера.
#
# Ведёт себя как настоящий мост по контракту (docs/CONTRACT.md §0 п.3): весь «UI» — в stderr,
# в stdout может быть мусор, но ПОСЛЕДНЯЯ строка stdout — JSON с полем status. Мусор в stdout
# здесь нарочно: тест проверяет, что MCP-сервер берёт последнюю JSON-строку, а не первую
# попавшуюся, и что ничего из stdout моста не просачивается в stdout MCP-сервера.
#
# Управление через окружение (чтобы тесты не зависели от продуктовой логики):
#   FAKE_ACTION_ARGS_FILE  — дописать в файл строку с argv (аргументы через табуляцию);
#   FAKE_ACTION_ENV_FILE   — дописать строку «GOAMNEZIA_WHO GOAMNEZIA_CALLER» (кто зовёт мост);
#   FAKE_ACTION_STDIN_FILE — дописать то, что пришло в stdin;
#   FAKE_ACTION_SLEEP      — поспать N секунд перед ответом (проверка таймаута);
#   FAKE_ACTION_STATUS     — success (по умолчанию) | error — вернуть status=error;
#   FAKE_ACTION_NO_JSON=1  — упасть без JSON (проверка ошибки транспорта).
# Скрипт совместим с bash 3.2 (macOS по умолчанию): без массивов и bash-4 конструкций.

action=""
name=""
for a in "$@"; do
    case "$a" in
        --action=*) action="${a#--action=}" ;;
        --name=*)   name="${a#--name=}" ;;
    esac
done

if [ -n "${FAKE_ACTION_ARGS_FILE:-}" ]; then
    { printf '%s\t' "$@"; printf '\n'; } >> "$FAKE_ACTION_ARGS_FILE"
fi
# То, что пришло каналом: секрет user-attach-key (ключ человека) мост читает из stdin, а не из argv.
if [ -n "${FAKE_ACTION_STDIN_FILE:-}" ]; then
    cat >> "$FAKE_ACTION_STDIN_FILE"
fi
# Действующее лицо и вызывающий: настоящий мост пишет их в аудит как ${GOAMNEZIA_WHO:-ssh},
# и только здесь видно, что MCP-сервер вообще их выставил.
if [ -n "${FAKE_ACTION_ENV_FILE:-}" ]; then
    printf '%s %s %s\n' "$action" "${GOAMNEZIA_WHO:-}" "${GOAMNEZIA_CALLER:-}" >> "$FAKE_ACTION_ENV_FILE"
fi

# Шум в stderr: обычные логи и «спиннер» с ANSI-цветом, как у настоящего install.sh.
echo "fake_action: обработка действия '${action}'" >&2
printf '\033[32m[ok]\033[0m spinner noise\r\n' >&2

if [ -n "${FAKE_ACTION_SLEEP:-}" ]; then
    sleep "$FAKE_ACTION_SLEEP"
fi

# Мусорная строка в stdout ДО JSON — по контракту допустимо только последней строке быть JSON.
echo "GARBAGE-LINE-FROM-BRIDGE: this is not json"

if [ "${FAKE_ACTION_NO_JSON:-0}" = "1" ]; then
    echo "fake_action: авария без JSON" >&2
    exit 3
fi

if [ "${FAKE_ACTION_STATUS:-success}" = "error" ]; then
    printf '{"status":"error","message":"forced error from fixture","code":"forced_error","action":"%s"}\n' "$action"
    exit 1
fi

# Валидный PNG 1×1 (70 байт) — чтобы проверить image-контент по-настоящему, а не «какой-то base64».
PNG='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAX+XDSwAAAABJRU5ErkJggg=='

case "$action" in
    status)
        echo '{"status":"success","message":"ok","mode":"lite","domain":"","profiles":["awg2"],"users":2,"services":{"bot":"inactive"},"version":"0.1.0"}'
        ;;
    user-list)
        echo '{"status":"success","message":"ok","users":[{"name":"anna","label":"Анна","enabled":true,"profiles":["awg2"]},{"name":"bob","label":"Bob","enabled":false,"profiles":[]}]}'
        ;;
    user-key)
        # Приватный ключ здесь — единственное место, где секрет допустим в ответе (контракт §0 п.7).
        printf '{"status":"success","message":"ok","name":"%s","profile":"awg2","line":"2.0","min_client":"4.8.12.7","conf":"[Interface]\\nPrivateKey = FAKE-PRIVATE-KEY\\n","vpn":"vpn://FAKE","qr_conf_png_b64":"%s","qr_vpn_png_b64":["%s","%s"]}\n' \
            "$name" "$PNG" "$PNG" "$PNG"
        ;;
    logs-tail)
        # Аудит-лог как настоящий: строки разных действующих лиц. По ним видно,
        # сузил ли сервер выдачу до строк ОДНОГО доступа (DEFECTS §1d.2).
        comp=""
        for a in "$@"; do
            case "$a" in --component=*) comp="${a#--component=}" ;; esac
        done
        if [ "$comp" = "mcp" ]; then
            printf '{"status":"success","message":"ok","component":"mcp","lines":100,"log":"%s"}\n' \
                "2026-09-11T10:00:00Z mcp call tool=server_status action=status args={} status=success code=- ms=5 client=x agent=a-0001 ip=198.51.100.1\\n2026-09-11T10:00:01Z mcp call tool=users_list action=user-list args={} status=success code=- ms=5 client=x agent=a-0002 ip=198.51.100.2\\n2026-09-11T10:00:02Z\\tbot:42\\tuser-add\\tname=anna\\n2026-09-11T10:00:03Z mcp call tool=server_status action=status args={} status=success code=- ms=5 client=x agent=a-0001 ip=198.51.100.1"
        else
            printf '{"status":"success","message":"ok","component":"%s","lines":100,"log":"line one\\nline two"}\n' "$comp"
        fi
        ;;
    *)
        printf '{"status":"success","message":"ok","action":"%s","name":"%s"}\n' "$action" "$name"
        ;;
esac
exit 0
