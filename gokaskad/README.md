# Kaskad PRO v2.2

**Каскадный менеджер VPN / Proxy с Telegram-ботом, мониторингом, GeoIP и live ping.**

> Канал: [YouTube @antenkaru](https://www.youtube.com/@antenkaru)

**Не разбираетесь в серверах и терминале?** → [ИНСТРУКЦИЯ_ДЛЯ_ПОЛЬЗОВАТЕЛЯ.md](ИНСТРУКЦИЯ_ДЛЯ_ПОЛЬЗОВАТЕЛЯ.md) — пошагово простыми словами.

---

## Возможности

| Функция | Описание |
|---|---|
| **Каскад UDP** | AmneziaWG, WireGuard — одна команда |
| **Каскад TCP** | VLESS, XRay, VMess, Reality, Trojan |
| **Каскад MTProto** | Telegram-прокси |
| **Custom Rule** | Разные порты входа/выхода, SSH, RDP и т.д. |
| **GeoIP + Ping-test** | При добавлении IP: GeoIP (страна/ISP) + 3 пинга |
| **Live Ping** | Пинг с ASCII-графикой (обновление 1 сек) |
| **Telegram Bot** | Полное управление через кнопки (Inline и Reply-клавиатура) |
| **Прокси для API Telegram** | Бот ходит в Telegram через пул ключей VLESS и AmneziaWG 1.0–3.1 (`vpn://` и `.conf` приложения Amnezia и goAmnezia pro) с резервом |
| **Ping в боте** | 1 раз / 10 раз (среднее) / 60 секунд (непрерывно) |
| **Мониторинг** | Автопинг с порогом и частотой уведомлений (10с/60с/5м/15м) |
| **Имена/примечания** | Имена IP серверов + примечания, отображаются везде |
| **BBR Turbo** | Автоматическое включение Google BBR |
| **Безопасность** | Валидация IP/портов, маркировка правил, изоляция iptables |

---

## Установка

Подключитесь к VPS (Ubuntu/Debian/CentOS/Fedora) под `root`.

### Вариант A: репозиторий публичный

```bash
curl -sL https://raw.githubusercontent.com/potap1978/go-all/main/install.sh -o install.sh && chmod +x install.sh && ./install.sh
```

### Вариант B: репозиторий приватный (с GitHub PAT)

```bash
curl -sL -H "Authorization: token YOUR_GITHUB_PAT" https://raw.githubusercontent.com/potap1978/go-all/main/install.sh -o install.sh && chmod +x install.sh && ./install.sh
```

Далее скрипт установит зависимости, включит IP forwarding/BBR и создаст глобальную команду `gokaskad`.

> **2.13.2 — исправление безопасности: всем, у кого 2.13, — обновиться.** Исправлена проверка адреса ключа в меню
> «Прокси для API Telegram»: ключ, у которого адрес или порт сервера не разобран, не добавляется; имена ключей
> показываются без служебных символов терминала. После обновления перезагрузите сервер (`reboot`).
> EN: security fix — the key address check in the «Telegram API proxy» menu; update and then reboot the server.

> Тот же PAT нужен пункту «Прокси для API Telegram» для ядра AmneziaWG (`bin/` репозитория приватный).
> Ключам AmneziaWG 3.x (их по умолчанию выдаёт goAmnezia pro) нужно ядро 3.1 — Kaskad 2.13 сам меняет
> на него прежнее ядро; пока нового нет, ключи 1.x/2.0 работают на старом, а меню говорит, чего не хватает.
> Скрипт ищет ключ сам: меню 8 → 6, `GITHUB_PAT` в `/etc/kaskad/config`, переменная `GH_TOKEN`,
> ключ goInstaller (`/opt/go-installer/token`); если нигде нет — спросит при первом добавлении AWG-ключа.
> Клон репозитория с `bin/` в `/opt/kaskad-pro` (или рядом со скриптом) избавляет от сети вовсе.

```bash
gokaskad
```

---

## Документация

Полная документация: **`DOCS.md`**

---

## Быстрый старт

### 1. Создать каскад (пример WireGuard)

```
Меню → 1
IP: 45.10.20.30
Порт: 51820
Подтвердить: y
```

В клиенте замените Endpoint на IP этого сервера.

### 2. Настроить Telegram-бота

```
Меню → 8 → 1  →  Вставить токен от @BotFather
Меню → 8 → 2  →  Отправить сообщение боту, нажать Enter
Меню → 8 → 4  →  Запустить бота
```

В Telegram: `/start` — появится меню с кнопками.

### 3. Включить мониторинг

```
Меню → 7 → 1  →  Выбрать сервер → Интервал → Порог (мс)
Меню → 7 → 3  →  Запустить службу
```

Алерты приходят в Telegram, когда пинг превышает порог.

---

## Структура меню

```
 1) AmneziaWG / WireGuard (UDP)
 2) VLESS / XRay (TCP)
 3) TProxy / MTProto (TCP)
 4) Кастомное правило
 5) Активные правила
 6) Ping сервера (live)
 7) Мониторинг
 8) Telegram Bot
 9) Удалить правило
10) Сбросить правила
11) Обновить скрипт
12) PROMO
13) Инструкция (7 страниц)
14) Имена серверов
 0) Выход
```

---

## Telegram Bot — кнопки

```
🔀 AWG/WireGuard (UDP)     — добавить UDP каскад
🔀 VLESS/XRay (TCP)        — добавить TCP каскад
🔀 MTProto/TProxy (TCP)    — добавить TCP каскад
🛠 Custom Rule             — произвольное правило
📋 Правила                 — список активных правил
🏓 Ping                    — пинг (1x / 10x / 60 сек)
📊 Мониторинг              — добавить/удалить/список мониторов
❌ Удалить правило          — удалить конкретный каскад
🗑 Сбросить всё            — полная очистка (с подтверждением)
⌨️ Reply-клавиатура         — переключение типа меню
```

---

## Файлы и пути

| Путь | Назначение |
|---|---|
| `/usr/local/bin/gokaskad` | Глобальная команда запуска |
| `/etc/kaskad/config` | Токен бота и Chat ID |
| `/etc/kaskad/monitors/` | Конфигурации мониторов |
| `/etc/kaskad/aliases` | Имена/примечания/GeoIP для IP |
| `/var/log/kaskad.log` | Лог всех действий |

---

## Системные службы

```bash
# Telegram-бот
systemctl status kaskad-bot
journalctl -u kaskad-bot -f

# Мониторинг
systemctl status kaskad-monitor
journalctl -u kaskad-monitor -f
```

---

## Лицензия

Для частного использования. Распространение запрещено.
