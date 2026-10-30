#!/bin/bash
# GoTelegram — Установка Telegram-бота
# Создаёт venv, ставит зависимости, настраивает systemd

set -e
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

BOT_DIR="/opt/gotelegram-bot"
SERVICE_NAME="gotelegram-bot"
GOTELEGRAM_DIR="/opt/gotelegram"
# Версия бралась из строки «v2.5.0», вписанной в трёх местах: в баннере и в
# Description юнита. Продукт давно ушёл вперёд, и systemd навсегда сохранял
# «GoTelegram v2.5.0 Telegram Bot». Читаем живое значение, как и остальной код.
GT_VER="$(grep -oE 'GOTELEGRAM_VERSION="[^"]*"' "$GOTELEGRAM_DIR/lib/common.sh" 2>/dev/null | head -1 | cut -d'"' -f2)"
[ -n "$GT_VER" ] || GT_VER="$(grep -oE 'GOTELEGRAM_VERSION="[^"]*"' "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh" 2>/dev/null | head -1 | cut -d'"' -f2)"
[ -n "$GT_VER" ] || GT_VER="?"
ADMIN_WEB_DIR="/opt/gotelegram-admin"
ADMIN_WEB_SERVICE="gotelegram-admin"
ADMIN_WEB_PORT="1984"

if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Запустите с sudo.${NC}"
    exit 1
fi

echo -e "${CYAN}╔═══════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║${NC}  ${GREEN}GoTelegram v${GT_VER} — Установка бота${NC}          ${CYAN}║${NC}"
echo -e "${CYAN}╚═══════════════════════════════════════════╝${NC}"
echo ""

# ── Python ───────────────────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo -e "${YELLOW}[*] Установка python3...${NC}"
    if command -v apt-get &>/dev/null; then
        apt-get update -qq && apt-get install -y -qq python3 python3-pip python3-venv
    elif command -v dnf &>/dev/null; then
        dnf install -y -q python3 python3-pip
    elif command -v yum &>/dev/null; then
        yum install -y -q python3 python3-pip
    fi
fi

# ── Каталог бота ─────────────────────────────────────────────────────────────
mkdir -p "$BOT_DIR"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -f "$SCRIPT_DIR/lib/common.sh" ] && source "$SCRIPT_DIR/lib/common.sh" || true
[ -f "$SCRIPT_DIR/lib/stats.sh" ] && source "$SCRIPT_DIR/lib/stats.sh" || true

if [ -f "$SCRIPT_DIR/gotelegram-bot/bot.py" ]; then
    echo -e "${GREEN}[*] Копирование файлов бота...${NC}"
    cp "$SCRIPT_DIR/gotelegram-bot/bot.py" "$BOT_DIR/"
    cp "$SCRIPT_DIR/gotelegram-bot/requirements.txt" "$BOT_DIR/"
    [ -f "$SCRIPT_DIR/gotelegram-bot/config.example.env" ] && cp "$SCRIPT_DIR/gotelegram-bot/config.example.env" "$BOT_DIR/"
    # i18n: без i18n.py и lang/*.json импорт падает, срабатывает фолбэк и
    # весь интерфейс превращается в голые ключи (menu_install, btn_back...).
    if [ -f "$SCRIPT_DIR/gotelegram-bot/i18n.py" ]; then
        cp "$SCRIPT_DIR/gotelegram-bot/i18n.py" "$BOT_DIR/"
    else
        echo -e "${RED}Не найден gotelegram-bot/i18n.py — интерфейс бота будет без переводов.${NC}"
    fi
    if [ -d "$SCRIPT_DIR/gotelegram-bot/lang" ]; then
        mkdir -p "$BOT_DIR/lang"
        cp -a "$SCRIPT_DIR/gotelegram-bot/lang/." "$BOT_DIR/lang/"
    else
        echo -e "${RED}Не найден каталог gotelegram-bot/lang — интерфейс бота будет без переводов.${NC}"
    fi
    rm -rf "$BOT_DIR/__pycache__"
else
    echo -e "${RED}Файлы бота не найдены в $SCRIPT_DIR/gotelegram-bot/${NC}"
    exit 1
fi

# Копируем каталог шаблонов.
# ВАЖНО про -ef: скрипт лежит в /opt/gotelegram, туда же и копирует, поэтому при
# запуске из установленного каталога источник и приёмник — ОДИН файл. cp на этом
# падает с кодом 1, а выше стоит `set -e` — и установка молча обрывалась ровно
# здесь: venv не создавался, systemd-юнит не писался, бот не поднимался вовсе.
# В логе оставалась одна строка «are the same file», по которой причину не видно.
# -ef сравнивает устройство и inode, то есть ловит и симлинк на тот же файл.
if [ -f "$SCRIPT_DIR/templates_catalog.json" ] \
   && ! [ "$SCRIPT_DIR/templates_catalog.json" -ef "$GOTELEGRAM_DIR/templates_catalog.json" ]; then
    mkdir -p "$GOTELEGRAM_DIR"
    cp "$SCRIPT_DIR/templates_catalog.json" "$GOTELEGRAM_DIR/"
    echo -e "${GREEN}[*] Каталог шаблонов скопирован${NC}"
fi

# ── Virtual environment ──────────────────────────────────────────────────────
# На чистой Ubuntu 24.04 python3 есть, а ensurepip — нет: venv отдельным пакетом
# python3.12-venv. Скрипт этого не проверял и падал на `python3 -m venv` с
# советом «apt install python3.12-venv», то есть переставал ставить бота на
# ЛЮБОМ свежем сервере. Ставим сами, как это давно делает bot_install() в
# install.sh: и общий python3-venv, и версионный — имя пакета зависит от версии.
if ! python3 -m venv --help >/dev/null 2>&1 || ! python3 -c 'import ensurepip' >/dev/null 2>&1; then
    echo -e "${GREEN}[*] Доустановка python3-venv...${NC}"
    if command -v apt-get >/dev/null 2>&1; then
        PY_VER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null)"
        DEBIAN_FRONTEND=noninteractive apt-get update -qq >/dev/null 2>&1 || true
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
            python3-venv python3-pip ${PY_VER:+python${PY_VER}-venv} >/dev/null 2>&1 || true
    fi
fi

# Проверяем НАЛИЧИЕ pip, а не каталога: от сорвавшейся попытки остаётся пустой
# venv/, и проверка `[ ! -d venv ]` считала его готовым — дальше скрипт падал на
# `venv/bin/pip: No such file or directory` и не мог починиться уже никогда,
# сколько ни перезапускай. Битый каталог сносим и создаём заново.
if [ ! -x "$BOT_DIR/venv/bin/pip" ]; then
    [ -d "$BOT_DIR/venv" ] && { echo -e "${YELLOW}[*] Прежнее окружение неполное — пересоздаю${NC}"; rm -rf "$BOT_DIR/venv"; }
    echo -e "${GREEN}[*] Создание виртуального окружения...${NC}"
    if ! python3 -m venv "$BOT_DIR/venv"; then
        echo -e "${RED}Не удалось создать venv. Поставьте пакет python3-venv вручную:${NC}"
        echo -e "${RED}  apt install python3-venv${NC}"
        exit 1
    fi
fi

echo -e "${GREEN}[*] Установка зависимостей...${NC}"
"$BOT_DIR/venv/bin/pip" install -r "$BOT_DIR/requirements.txt" -q

# ── Конфигурация ─────────────────────────────────────────────────────────────
if [ ! -f "$BOT_DIR/.env" ]; then
    echo ""
    echo -e "${YELLOW}Введите BOT_TOKEN от @BotFather:${NC}"
    TOKEN=""
    while [ -z "$TOKEN" ]; do
        read -r TOKEN
        TOKEN=$(echo "$TOKEN" | tr -d '[:space:]')
        [ -z "$TOKEN" ] && echo -e "${RED}Токен не может быть пустым.${NC}"
    done

    # Раньше здесь был Enter = «доступ для всех». На деле пустой ALLOWED_IDS
    # означает не «всем можно», а «первый, кто напишет /start, получит полный
    # root-контроль». Поэтому ID теперь спрашиваем явно и проверяем формат.
    echo ""
    echo -e "${YELLOW}Telegram ID администратора (узнать свой ID: @userinfobot).${NC}"
    echo -e "${CYAN}Можно несколько ID через запятую: 123456789,987654321${NC}"
    ADMIN_ID=""
    while [ -z "$ADMIN_ID" ]; do
        echo -ne "${YELLOW}ID:${NC} "
        # EOF (неинтерактивный запуск) — не крутим бесконечный цикл
        if ! read -r ADMIN_ID; then
            echo ""
            echo -e "${RED}Нет ввода: запустите установщик в терминале либо создайте${NC}"
            echo -e "${RED}$BOT_DIR/.env вручную (BOT_TOKEN=... и ALLOWED_IDS=...).${NC}"
            exit 1
        fi
        ADMIN_ID=$(echo "$ADMIN_ID" | tr -d '[:space:]')
        if [ -z "$ADMIN_ID" ]; then
            echo -e "${RED}ID обязателен: без него бот никого не пустит.${NC}"
        elif ! echo "$ADMIN_ID" | grep -Eq '^[0-9]+(,[0-9]+)*$'; then
            echo -e "${RED}Только цифры (несколько ID — через запятую).${NC}"
            ADMIN_ID=""
        fi
    done

    {
        echo "BOT_TOKEN=$TOKEN"
        echo "ALLOWED_IDS=$ADMIN_ID"
    } > "$BOT_DIR/.env"

    chmod 600 "$BOT_DIR/.env"
    echo -e "${GREEN}[*] .env создан${NC}"
else
    echo -e "${GREEN}[*] .env уже существует${NC}"
fi

# ── Systemd ──────────────────────────────────────────────────────────────────
cat > "/etc/systemd/system/${SERVICE_NAME}.service" << EOF
[Unit]
Description=GoTelegram v${GT_VER} Telegram Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=$BOT_DIR
ExecStart=$BOT_DIR/venv/bin/python $BOT_DIR/bot.py
Restart=always
RestartSec=5
Environment=PATH=$BOT_DIR/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME" 2>/dev/null || systemctl start "$SERVICE_NAME"

# ── Local Web Admin ──────────────────────────────────────────────────────────
if [ -f "$SCRIPT_DIR/admin-web/server.py" ]; then
    echo -e "${GREEN}[*] Установка локальной web-админки...${NC}"
    mkdir -p "$ADMIN_WEB_DIR/static"
    cp "$SCRIPT_DIR/admin-web/server.py" "$ADMIN_WEB_DIR/server.py"
    cp -a "$SCRIPT_DIR/admin-web/static/." "$ADMIN_WEB_DIR/static/"
    chmod 700 "$ADMIN_WEB_DIR"
    chmod 755 "$ADMIN_WEB_DIR/server.py" "$ADMIN_WEB_DIR/static"
    rm -f "$ADMIN_WEB_DIR/token" 2>/dev/null || true

    PYTHON_BIN=$(command -v python3)
    cat > "/etc/systemd/system/${ADMIN_WEB_SERVICE}.service" << EOF
[Unit]
Description=GoTelegram v2.5.0 Local Web Admin
After=network.target

[Service]
Type=simple
WorkingDirectory=$ADMIN_WEB_DIR
ExecStart=$PYTHON_BIN $ADMIN_WEB_DIR/server.py
Restart=always
RestartSec=5
Environment=GOTELEGRAM_ADMIN_HOST=127.0.0.1
Environment=GOTELEGRAM_ADMIN_PORT=$ADMIN_WEB_PORT

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable "$ADMIN_WEB_SERVICE"
    systemctl restart "$ADMIN_WEB_SERVICE" 2>/dev/null || systemctl start "$ADMIN_WEB_SERVICE"
    if type install_stats_collector &>/dev/null; then
        install_stats_collector >/dev/null 2>&1 || echo -e "${YELLOW}[!] Сборщик статистики не запущен; откройте Traffic в Web Admin и нажмите Repair.${NC}"
    fi
fi

echo ""
echo -e "${GREEN}╔═══════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║  ✅ Бот установлен и запущен!              ║${NC}"
echo -e "${GREEN}╚═══════════════════════════════════════════╝${NC}"
echo ""
echo -e "Проверка:  ${CYAN}systemctl status $SERVICE_NAME${NC}"
echo -e "Логи:      ${CYAN}journalctl -u $SERVICE_NAME -f${NC}"
echo -e "Настройки: ${CYAN}$BOT_DIR/.env${NC}"
echo ""

# Благодарности
echo -e "${CYAN}─────────────────────────────────────────────${NC}"
echo -e "💜 Спасибо авторам открытых проектов:"
echo -e "   ${CYAN}telemt${NC}           — MTProxy engine (Rust)"
echo -e "   ${CYAN}HTML5 UP${NC}         — шаблоны сайтов (CC BY 3.0)"
echo -e "   ${CYAN}learning-zone${NC}    — 150+ HTML5 шаблонов"
echo -e "   ${CYAN}Start Bootstrap${NC}  — Bootstrap шаблоны (MIT)"
echo -e "${CYAN}─────────────────────────────────────────────${NC}"
