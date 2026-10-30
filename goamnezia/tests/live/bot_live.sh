#!/usr/bin/env bash
# bot_live.sh — живая проверка Telegram-бота и мини-аппа на настоящем сервере через подставной Telegram.
#
#   bash tests/live/bot_live.sh root@203.0.113.10 --key ~/.ssh/key [setup|run|conn|cleanup|all] [--only=шаг,шаг]
#
# Настоящий токен и настоящий Telegram не нужны и не используются: токен выдуманный (создаётся на сервере, 0600, наружу
# не выходит), Bot API — подставной (remote/fake_tg_api.py на 127.0.0.1:18081), бот — настоящий bot.py штатного
# установщика, направленный туда drop-in'ом к goamnezia-bot.service (remote/run_bot.py). Подробно — tests/live/README.md,
# раздел «Бот и мини-апп через подставной Telegram».
#   setup   — инструменты в /root/ga-live/bot, токен, подставной API и прокси мини-аппа (временные службы), drop-in,
#             install_bot.sh --auto (как зовёт SSH-меню, GOAMNEZIA_ADMIN_PAIR=1), привязка владельца: сообщение до окна,
#             install_bot.sh --pair в псевдотерминале (unshare -n: getMe с выдуманным токеном в сеть не уходит),
#             сообщение в окне, «1» и «1».
#   run     — remote/bot_scenario.py: таблица «проверка → OK/FAIL → доказательство».
#   conn    — настоящее подключение ключа первого массово добавленного (acceptance.py --checks conn, клиент — сам сервер).
#   cleanup — свои люди (created-users.txt), бот, drop-in, временные службы, контейнеры bot-netdeps-*, /root/ga-live/bot;
#             в конце wrp-check (если есть), упавшие юниты и число людей.
# Сервер с настоящим ботом (есть /opt/goamnezia-bot/.env без нашего drop-in) setup не трогает.
set -uo pipefail

HOST="${1:-}"
[[ "$HOST" == *@* ]] || { echo "usage: $0 root@host [--key KEY] [setup|run|conn|cleanup|all]" >&2; exit 2; }
shift
KEY="$HOME/.ssh/id_ed25519"
CMD=all
STEPS=""
while [ $# -gt 0 ]; do
    case "$1" in
        --key) KEY="$2"; shift 2 ;;
        --key=*) KEY="${1#--key=}"; shift ;;
        --only=*) STEPS="${1#--only=}"; shift ;;
        setup|run|conn|cleanup|all) CMD="$1"; shift ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done
[[ "$STEPS" =~ ^[a-z_,]*$ ]] || { echo "--only: имена шагов через запятую" >&2; exit 2; }
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
R=/root/ga-live/bot

# SSH — общим каналом на прогон (ssh_link.py, TOOL-01): одно рукопожатие на сервер, дальше по готовому соединению;
# тестовые серверы под перебором паролей рвут как раз свежие рукопожатия («banner exchange», «Connection closed …
# port 22», код 255). Повтор с растущей паузой — когда ssh не дошёл до команды; обрыв посреди повторяется только у
# чтения и заливки (remote_read, upload): setup, run и cleanup меняют сервер, и вслепую их не повторить. Канал
# закрывается на выходе; acceptance.py (conn) едет по нему же (GA_LIVE_SSH_DIR).
LINK=(python3 "$HERE/ssh_link.py")
LINK_OPTS=(--key "$KEY" -o IdentitiesOnly=yes -o ConnectTimeout=30)
CTL=$("${LINK[@]}" open) || { echo "не создать каталог канала SSH" >&2; exit 1; }
export GA_LIVE_SSH_DIR="$CTL"
trap '"${LINK[@]}" close --dir "$CTL" "${LINK_OPTS[@]}" "$HOST"' EXIT

remote()        { "${LINK[@]}" run --dir "$CTL" "${LINK_OPTS[@]}" "$HOST" -- "$@"; }
remote_read()   { "${LINK[@]}" run --safe --dir "$CTL" "${LINK_OPTS[@]}" "$HOST" -- "$@"; }
# remote_script — скрипт на stdin (heredoc): весь читается заранее, каждая попытка получает его целиком.
remote_script() { "${LINK[@]}" run --stdin --dir "$CTL" "${LINK_OPTS[@]}" "$HOST" -- "$@"; }

upload() {
    local f
    remote_read "install -d -m 700 $R $R/state" || return 1
    for f in fake_tg_api.py run_bot.py tg_user.py pty_drive.py bot_pair.json miniapp_check.py miniapp_proxy.py \
             bot_crawl.py bot_scenario.py; do
        "${LINK[@]}" run --safe --stdin --dir "$CTL" "${LINK_OPTS[@]}" "$HOST" -- "umask 077; cat > $R/$f" \
            <"$HERE/remote/$f" || return 1
    done
    remote_read "chmod 700 $R/*.py && chmod 600 $R/bot_pair.json"
}

do_setup() {
    upload || return 1
    remote_script 'bash -s' <<'EOF'
set -uo pipefail
R=/root/ga-live/bot
DROPIN=/etc/systemd/system/goamnezia-bot.service.d/10-ga-live-fake-tg.conf
if [ -f /opt/goamnezia-bot/.env ] && [ ! -f "$DROPIN" ]; then
    echo "На сервере уже стоит настоящий бот (/opt/goamnezia-bot/.env без drop-in проверки) — не трогаю." >&2
    exit 1
fi
grep '^GOAMNEZIA_VERSION' /opt/goamnezia/lib/common.sh
# Выдуманный токен: ни к какому боту не относится и не покидает сервер
if [ ! -f "$R/token" ]; then
    python3 - <<'PY'
import os, secrets, string
alpha = string.ascii_letters + string.digits + "_-"
tok = "%d:%s" % (secrets.randbelow(9 * 10**9) + 10**9, "".join(secrets.choice(alpha) for _ in range(35)))
fd = os.open("/root/ga-live/bot/token", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
os.write(fd, (tok + "\n").encode())
os.close(fd)
PY
fi
systemctl is-active --quiet ga-fake-tg || systemd-run --unit=ga-fake-tg --description="ga-live fake Telegram Bot API (tests/live)" \
    --property=Restart=on-failure /usr/bin/python3 "$R/fake_tg_api.py" --token-file "$R/token" --state-dir "$R/state" --port 18081 >/dev/null
systemctl is-active --quiet ga-miniapp-proxy || systemd-run --unit=ga-miniapp-proxy --description="ga-live mini-app proxy (tests/live)" \
    /usr/bin/python3 "$R/miniapp_proxy.py" --port 18082 >/dev/null
install -d -m 755 "${DROPIN%/*}"
cat > "$DROPIN" <<'UNIT'
# ga-live (tests/live/bot_live.sh): бот ходит в подставной Bot API на петле (fake_tg_api.py), а не в api.telegram.org.
# Токен в .env — выдуманный. Убирается вместе с ботом: bot_live.sh cleanup.
[Service]
Environment=GA_FAKE_TG_API=http://127.0.0.1:18081
ExecStart=
ExecStart=/opt/goamnezia-bot/venv/bin/python /root/ga-live/bot/run_bot.py /opt/goamnezia-bot/bot.py
UNIT
chmod 644 "$DROPIN"
sleep 1
if [ ! -f /opt/goamnezia-bot/.env ]; then
    # Ровно так зовёт установщик SSH-меню («Telegram-бот → Установить», ответ «напишу боту»): токен — окружением
    BOT_TOKEN="$(cat "$R/token")" ALLOWED_IDS="" OWNER_ID="" GOAMNEZIA_ADMIN_PAIR=1 GOAMNEZIA_LANG=ru \
        GOAMNEZIA_BOT_DIR=/opt/goamnezia-bot GOAMNEZIA_DIR=/opt/goamnezia \
        bash /opt/goamnezia/goamnezia-bot/install_bot.sh --auto > "$R/install-auto.log" 2>&1
    rc=$?
    chmod 600 "$R/install-auto.log"
    echo "install_bot.sh --auto: rc=$rc"
    [ "$rc" -eq 0 ] || { sed -e 's/\x1b\[[0-9;]*m//g' "$R/install-auto.log" | tail -15; exit 1; }
fi
for i in $(seq 1 30); do
    [ "$(python3 -c 'import json,urllib.request;print(json.load(urllib.request.urlopen("http://127.0.0.1:18081/_test/state"))["polls"])' 2>/dev/null || echo 0)" -gt 0 ] && break
    sleep 1
done
echo "бот: $(systemctl is-active goamnezia-bot), опрос подставного API: $(python3 "$R/tg_user.py" state | python3 -c 'import json,sys;print(json.load(sys.stdin)["polls"])')"
if grep -q '^ALLOWED_IDS=.*900000001' /opt/goamnezia-bot/.env; then
    echo "владелец уже привязан"
    exit 0
fi
# До окна привязки: ответ чужому
python3 "$R/tg_user.py" send "/start" --as owner --wait 15 --no-buttons | head -2
# Окно — из SSH, как в меню: install_bot.sh --pair в псевдотерминале, без сети
rm -f "$R/pair.done" "$R/pair.log"
( cd "$R" && setsid nohup python3 "$R/pty_drive.py" --script "$R/bot_pair.json" --log "$R/pair.log" --done "$R/pair.done" -- \
    unshare -n bash /opt/goamnezia/goamnezia-bot/install_bot.sh --pair --lang=ru ) >/dev/null 2>&1 </dev/null &
for i in $(seq 1 60); do [ -f /opt/goamnezia-bot/.admin_pairing_until ] && break; sleep 0.5; done
sleep 1
python3 "$R/tg_user.py" send "Привет, это я" --as owner --wait 15 --no-buttons | head -2
for i in $(seq 1 90); do [ -f "$R/pair.done" ] && break; sleep 1; done
cat "$R/pair.done" 2>/dev/null || { echo "привязка не закончилась за 90 с" >&2; exit 1; }
python3 "$R/tg_user.py" show --as owner --n 2 | head -4
EOF
}

do_run() {
    remote "cd $R && python3 bot_scenario.py ${STEPS:+--only=$STEPS}"
}

do_conn() {
    local user
    user=$(remote_read "head -n1 $R/bulk-users.txt" 2>/dev/null)
    [ -n "$user" ] || { echo "нет $R/bulk-users.txt — сначала run" >&2; return 1; }
    python3 "$HERE/acceptance.py" --server "$HOST" --client "$HOST" --key "$KEY" --checks conn --user "$user"
}

do_cleanup() {
    remote_script 'bash -s' <<'EOF'
set -uo pipefail
R=/root/ga-live/bot
DROPIN=/etc/systemd/system/goamnezia-bot.service.d/10-ga-live-fake-tg.conf
if [ -f "$R/created-users.txt" ]; then
    sort -u "$R/created-users.txt" | while IFS= read -r n; do
        [ -n "$n" ] || continue
        r=$(GOAMNEZIA_WHO=live-bot-cleanup bash /opt/goamnezia/install.sh --action=user-del --name="$n" --json 2>/dev/null | tail -n1)
        echo "user-del $n: $(printf '%s' "$r" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("status"), d.get("code") or "")' 2>/dev/null)"
    done
fi
docker rm -f bot-netdeps-host bot-netdeps-nbr >/dev/null 2>&1 || true
if [ -f "$DROPIN" ]; then
    systemctl disable --now goamnezia-bot >/dev/null 2>&1 || true
    rm -f /etc/systemd/system/goamnezia-bot.service
    rm -rf /etc/systemd/system/goamnezia-bot.service.d
    rm -rf /opt/goamnezia-bot
    systemctl daemon-reload
    systemctl reset-failed goamnezia-bot >/dev/null 2>&1 || true
    echo "бот снят (служба, юнит, drop-in, /opt/goamnezia-bot с .env выдуманного токена)"
fi
for u in ga-fake-tg ga-miniapp-proxy; do systemctl stop "$u" >/dev/null 2>&1; systemctl reset-failed "$u" >/dev/null 2>&1; done
rm -rf "$R"
echo "подставной API, прокси и $R убраны"
grep '^GOAMNEZIA_VERSION' /opt/goamnezia/lib/common.sh
[ -x /root/ga-live/wrp-check.sh ] && bash /root/ga-live/wrp-check.sh 2>&1 | tail -n1
echo "упавших юнитов: $(systemctl list-units --failed --no-legend | wc -l)"
echo "людей: $(bash /opt/goamnezia/install.sh --action=user-list --json 2>/dev/null | tail -n1 | python3 -c 'import json,sys; print(len(json.load(sys.stdin).get("users") or []))')"
echo "служб бота: $(systemctl list-unit-files 'goamnezia-bot*' --no-legend | wc -l), контейнеров bot-*: $(docker ps -a --format '{{.Names}}' | grep -c '^bot-')"
EOF
}

case "$CMD" in
    setup) do_setup ;;
    run) do_run ;;
    conn) do_conn ;;
    cleanup) do_cleanup ;;
    all)
        rc=0
        do_setup || rc=1
        if [ "$rc" -eq 0 ]; then
            do_run || rc=1
            do_conn || rc=1
        fi
        do_cleanup
        exit "$rc"
        ;;
esac
