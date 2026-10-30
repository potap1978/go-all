#!/bin/bash
# goDNS — Copyright (c) 2026 potap1978. All rights reserved.
#
# lib/qr.sh — QR-коды для подключения телефонов.
#
# ЗАЧЕМ. Жалобы «на роутере работает, на телефоне нет» имеют один корень:
# телефону нечего скопировать. Роутер настраивает оператор по сниппетам, а
# телефон — конечный человек, которому продиктовали домен голосом. Android
# «Частный DNS» требует ИМЯ ХОСТА (DoT, только порт 853), iPhone — профиль
# .mobileconfig, который надо как-то доставить на устройство. QR закрывает
# обе доставки: Android сканирует имя и вставляет, iPhone сканирует ссылку
# и скачивает профиль по HTTPS.
#
# qrencode — крошечный пакет (~50 КБ), есть во всех выпусках Ubuntu.
# Тот же инструмент использует goTelegram (-t UTF8 -m 2) — формат проверен
# на живых терминалах.

godns_qr_available() {
    command -v qrencode >/dev/null 2>&1
}

# Поставить qrencode. Молча и без вопросов — вопрос задаёт вызывающий
# ИНТЕРАКТИВНЫЙ контекст; из бота и панели ставим сразу: их «вопрос»
# уже задан нажатием кнопки.
godns_qr_ensure() {
    godns_qr_available && return 0
    apt_q install qrencode >/dev/null 2>&1 || true
    godns_qr_available
}

# QR в терминал. -m 2 — тихая зона: без неё сканеры путаются на тёмных
# темах. Текст приходит аргументом, а не через echo: в имени хоста и URL
# переносов не бывает, а вот случайный -e в начале строки echo бы съел.
godns_qr_term() {
    godns_qr_available || return 1
    qrencode -t UTF8 -m 2 "$1" 2>/dev/null
}

# QR в PNG для отправки в Telegram. Размер точки 6 — на экране телефона
# читается и с фото другого телефона.
godns_qr_png() {
    local text="$1" out="$2"
    godns_qr_available || return 1
    qrencode -t PNG -s 6 -m 2 -o "$out" "$text" 2>/dev/null || return 1
    [ -s "$out" ] || return 1
    return 0
}
