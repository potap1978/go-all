# Kaskad PRO — документация (v2.2)

## Что такое Kaskad PRO

Kaskad PRO — bash-скрипт для Linux, который создаёт **каскадный “мост”** (DNAT + MASQUERADE) между клиентом и вашим зарубежным VPN/Proxy сервером.

Схема:

- **Клиент** → **RU VPS (Kaskad PRO)** → **Foreign VPN/Proxy** → Интернет

Скрипт:

- Добавляет правила `iptables` (NAT + FORWARD)
- Включает `net.ipv4.ip_forward=1`
- Включает BBR (fq + bbr)
- Сохраняет правила (netfilter-persistent/iptables-persistent где доступно)

---

## Требования

- **VPS с Linux** (Ubuntu/Debian/CentOS/Fedora)
- Доступ **root**
- Исходящий доступ в интернет (для установки пакетов и GeoIP)
- Пакеты (скрипт установит автоматически): `iptables`, `curl`, `jq`, `qrencode` (+ persistent для Debian/Ubuntu)

---

## Установка

### Вариант A: репозиторий публичный

```bash
curl -sL https://raw.githubusercontent.com/potap1978/go-all/main/install.sh -o install.sh && chmod +x install.sh && ./install.sh
```

### Вариант B: репозиторий приватный (GitHub PAT)

```bash
curl -sL -H "Authorization: token YOUR_GITHUB_PAT" https://raw.githubusercontent.com/potap1978/go-all/main/install.sh -o install.sh && chmod +x install.sh && ./install.sh
```

### Повторный запуск меню

```bash
gokaskad
```

### Поверх бесплатного Kaskad

Бесплатный Kaskad из инструкции (github.com/potap1978/kaskad) ставит ту же
команду `gokaskad` и такие же правила, только без меток `kaskad:`. Kaskad PRO
при запуске заменяет команду своей и забирает эти правила себе: каскады
продолжают работать и появляются в меню. Чужие DNAT, у которых нет пары
FORWARD к той же цели, он не трогает. Команду установки из бесплатной
инструкции после этого не запускайте: она вернёт бесплатную версию на место PRO.

---

## Где взять ключи/токены

### 1) Telegram Bot Token (где взять токен бота)

Нужен для запуска Telegram-бота.

1. В Telegram откройте **`@BotFather`**
2. Отправьте команду: `/newbot`
3. Введите имя бота (любое)
4. Введите username бота (должен оканчиваться на `bot`)
5. Скопируйте **token**, который выдаст BotFather (формат `123456789:AA...`)

### 2) Telegram Chat ID (как привязать доступ)

В Kaskad PRO управление ограничено **по Chat ID**.

Вариант авто:

- В меню: `8) Telegram Bot → 2) Chat ID (авто)`
- Перед этим отправьте своему боту любое сообщение (например `/start`)

Вариант вручную:

- В меню: `8) Telegram Bot → 3) Chat ID (вручную)`
- Вставьте chat id (число)

### 3) GitHub PAT (для приватного репозитория)

Нужен только если репозиторий **private**, чтобы скачивать `install.sh` по `raw.githubusercontent.com`.

Рекомендуемый вариант: **fine-grained PAT** с доступом только к репозиторию.

1. GitHub → Settings → Developer settings → **Personal access tokens** → **Fine-grained tokens**
2. Create token
3. Repository access: **Only select repositories** → выберите `kaskad-pro`
4. Permissions: **Contents = Read-only**
5. Сгенерируйте и сохраните токен

Использование: в команде установки или обновления через `curl -H "Authorization: token ..."`

---

## Основные функции (терминальное меню)

Ниже — что делает каждый пункт меню и когда использовать.

### 1) AmneziaWG / WireGuard (UDP)

Создаёт каскад для UDP-сервисов (AWG/WireGuard/OpenVPN-UDP).

- Вы вводите `IP` и `порт`
- Создаётся правило: `udp :порт (RU VPS) → IP:порт (Foreign)`

На клиенте (AWG/WireGuard) меняете endpoint на IP вашего RU VPS.

### 2) VLESS / XRay (TCP)

Каскад для TCP-прокси (VLESS/VMess/Trojan/Reality и т.д.).

Логика та же:

- `tcp :порт (RU VPS) → IP:порт (Foreign)`

### 3) TProxy / MTProto (TCP)

Каскад для MTProto/TProxy (обычно для Telegram).

### 4) Custom Rule (TCP/UDP, разные порты)

Позволяет пробрасывать:

- разные вход/выход порты
- любой TCP/UDP сервис

Пример: клиент подключается к `RU_VPS:2222`, а трафик уходит на `FOREIGN:22` (SSH).

### 5) Правила

Показывает активные DNAT правила (включая IP каскада) и информацию о целевом сервере:

- имя
- примечание
- GeoIP (страна/ISP), если определено

### 6) Ping (live)

Live ping выбранного целевого IP:

- обновление каждые 1 сек
- ASCII-бар + цветовая шкала
- статистика min/max/avg и потери

### 7) Мониторинг (авто)

Мониторинг целевых серверов:

- Интервал проверок: **10 сек / 1 мин / 5 мин**
- Порог задержки: в ms
- Частота уведомлений (не чаще): **10 сек / 60 сек / 5 мин / 15 мин**
- Если задержка выше порога или timeout — отправляет alert в Telegram

Служба `kaskad-monitor`:

- запускается автоматически, если есть хотя бы один монитор
- останавливается автоматически, если мониторов нет

### 8) Telegram Bot

Настройка токена/Chat ID и управление службой `kaskad-bot`.

**Прокси для API Telegram** (меню 8 → 7). Бот ходит в api.telegram.org через пул ключей: VLESS
(`vless://`, подписка) и AmneziaWG — строка `vpn://` или текст `.conf` из приложения Amnezia или
goAmnezia pro, линии 1.0, 1.5, 2.0 и 3.1. Линию Kaskad определяет сам, по правилам приложения:
любое поле 3.x (HeaderProtectionKey, тайминги, RandomTrailers/DisableCookies) — 3.1; S3/S4 или
диапазон в H — 2.0; I1–I5 — 1.5. Значения 3.x передаются в AmneziaWG как есть, диапазоны тоже
(`PersistentKeepalive = 25-35`); только переключатели true/false приводятся к on/off, а
`PersistentKeepalive = off` — к 0 (так их понимает awg 3.1).

Ключам 3.x нужно ядро AmneziaWG 3.1 (`bin/`: amneziawg-go v3.1.20260828 + awg 3.1.20260812, см.
`bin/VERSIONS.txt`). На сервере со старым ядром Kaskad меняет его при добавлении ключа 3.x (с диска
или по сети с ключом GitHub; новое готовится рядом и встаёт на место, только когда проверено) и
пробует снова при открытии «Прокси для API Telegram» — диск каждый раз, сеть не чаще раза в час.
Пока нового нет, старое остаётся, и ключи 1.x/2.0 работают. Ключ 3.x, добавленный Kaskad 2.12 и раньше, потерял
поля 3.x — меню помечает его, его нужно удалить и добавить заново. MTU туннеля — 1200.
Откат на 2.12 (`/usr/local/bin/gokaskad.bak`): ключи 1.x/2.0 работают и на новом ядре, а ключи,
добавленные как 3.1, перестают подключаться — 2.12 их не понимает.

### 9) Удалить правило

Удаляет 1 DNAT правило и связанные INPUT/FORWARD правила.

### 10) Сбросить всё

Удаляет **только** правила, созданные Kaskad (по маркеру `kaskad`).

### 11) Обновить скрипт

Скачивает свежий `install.sh` из репозитория и заменяет `/usr/local/bin/gokaskad`.

Если репозиторий приватный — обновление нужно выполнять с PAT (или настроить отдельный update-token внутри скрипта/бота).

### 12) PROMO

Партнёрские хостинги и промокоды (плюс QR).

### 13) Инструкция

Встроенная краткая инструкция (страницы).

### 14) Имена серверов

Редактор имён и примечаний для целевых IP.

Данные хранятся в `/etc/kaskad/aliases`.

---

## Telegram Bot (как пользоваться)

### Запуск

1. Создайте токен через `@BotFather`
2. На сервере:

```bash
gokaskad
```

Меню:

- `8) Telegram Bot → 1) Токен бота` — вставьте token
- `8) Telegram Bot → 2) Chat ID (авто)` — отправьте боту `/start`, затем Enter в терминале
- `8) Telegram Bot → 4) Запустить`

В Telegram:

- Отправьте боту `/start`

### Режимы меню

- **Inline** — кнопки под сообщением (по умолчанию)
- **Reply keyboard** — большие кнопки внизу экрана

Переключение:

- В inline: кнопка `⌨️ Reply-клавиатура`
- В reply: команда `/inline`

### Добавление сервера через бота

Поток:

1. Нажимаете протокол (AWG/VLESS/MTProto/Custom)
2. Вводите IP
3. Бот делает:
   - GeoIP (страна/ISP)
   - ping 3 раза (1 сек)
4. Просит имя
5. Просит примечание
6. Просит порт(ы)
7. Создаёт правило

### Ping в боте

- 1 раз — мгновенно
- 10 раз — показывает список и среднее
- 60 сек — обновляет прогресс каждые 10 секунд, затем итог

### Мониторинг в боте

1. `📊 Монитор` → `➕ Добавить` → выбрать сервер
2. Выбрать интервал (10с/1мин/5мин)
3. Ввести порог (мс)
4. Выбрать частоту уведомлений (10с/60с/5мин/15мин)

---

## Где хранятся данные

| Путь | Назначение |
|---|---|
| `/usr/local/bin/gokaskad` | основной скрипт/команда |
| `/etc/kaskad/config` | BOT_TOKEN, BOT_CHAT_ID, MENU_STYLE |
| `/etc/kaskad/aliases` | IP → имя/примечание/GeoIP |
| `/etc/kaskad/monitors/*.conf` | мониторы IP |
| `/var/log/kaskad.log` | лог действий |

---

## Службы systemd

```bash
systemctl status kaskad-bot
journalctl -u kaskad-bot -f

systemctl status kaskad-monitor
journalctl -u kaskad-monitor -f
```

---

## Troubleshooting

### Бот не отвечает

- Проверьте, что задан `BOT_TOKEN` и `BOT_CHAT_ID`
- Проверьте службу:

```bash
systemctl status kaskad-bot
journalctl -u kaskad-bot -f
```

### Raw GitHub не скачивается

Если репозиторий приватный — нужен PAT:

```bash
curl -sL -H "Authorization: token YOUR_GITHUB_PAT" https://raw.githubusercontent.com/potap1978/go-all/main/install.sh
```

### Мониторинг не запускается

Мониторинг запускается автоматически, если есть конфиги в `/etc/kaskad/monitors/`.
Проверьте:

```bash
ls -la /etc/kaskad/monitors/
systemctl status kaskad-monitor
```

---

## Лицензия

Для частного использования. Распространение запрещено.

