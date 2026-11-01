# goTelegram Pro — техническая документация для ИИ-агентов

> ⚠️ **Документ исторический, актуален до версии 2.7.0.**
> Текущее состояние продукта описано в `AUDIT.md`. Здесь встречаются утверждения,
> которые больше не верны (число анти-DPI режимов, отдельное меню реаниматора,
> привязка SYN-фикса к пресету). При расхождении верить коду и `AUDIT.md`.


**Версия:** 2.6.0
**Репозиторий:** `anten-ka/gotelegram_pro`
**Активная ветка:** `main` — основной код и продакшен. `beta` и `alpha` существуют для следующих сборок.
**Целевая ОС:** Ubuntu 20.04+, Debian 11+

Этот документ описывает устройство проекта с максимальным количеством нюансов — все ловушки, на которые мы уже наступали, все причины «почему именно так», формат всех внешних интерфейсов, и checklist действий для типовых задач. Цель: чтобы новый агент мог продолжить работу без повторения ошибок и без регрессий.

---

## 0. Быстрый старт для нового ИИ

Если ты агент, который впервые открыл репозиторий:

1. Не трогай токены, ключи VPS и BotFather-токены в истории диалога; не печатай их в ответах и не клади в файлы.
2. Основной код устанавливается из `main`; `bootstrap.sh` по умолчанию скачивает `main`, если не задан `GOTELEGRAM_BRANCH`.
3. Рабочая модель веток: `alpha` для ранних экспериментов, `beta` для проверочных сборок, `main` для продакшена.
4. Главный код установки — `install.sh` + `lib/*.sh`. Не исправляй Telegram-бот или web-admin в отрыве от bash-слоя: все три интерфейса должны показывать одно и то же состояние.
5. Главный конфиг пользователя — `/opt/gotelegram/config.json`; главный runtime-конфиг ядра — `/etc/telemt/config.toml`.
6. `telemt` использует TOML v3, не совместимый со старым `mtg`. Не возвращай старые секции `[security]`, `[[users]]`, `bind_to`.
7. Любая bash-функция, которую вызывают через `$()`, обязана писать UI и логи в stderr (`>&2`), а в stdout возвращать только значение.
8. Перед пушем запускай проверки из раздела 20. Для документации минимум: bash syntax, unit tests, py_compile, node check, `git diff --check`.
9. Если меняешь поведение, обновляй `DOCS_HUMAN.md`, `DOCS_AI.md`, changelog и при необходимости `GOTELEGRAM_VERSION`.
10. Если меняешь бэкапы, сохраняй обратную совместимость restore для старых архивов.

---

## 1. Общая картина

goTelegram Pro — это менеджер MTProxy для Telegram, собранный вокруг Rust-ядра **telemt** (порт mtproto-proxy с поддержкой fake-TLS, v3.3.x). Проект даёт три вещи:

1. **CLI-меню на bash** (`install.sh` + `lib/*.sh`) — установка, настройка, обновление, бекап, перезапуск, смена режима, управление сайтом-маскировкой, выбор шаблона. Единая точка входа `gotelegram` (symlink → `/opt/gotelegram/install.sh`).
2. **Сайт-маскировку** — поднимает рядом nginx с настоящим сайтом на настоящем домене (Let's Encrypt) из каталога 1801 HTML-шаблонов (html5up / startbootstrap / ThemeWagon / dawidolko). В stealth-режиме telemt слушает 443, маскировочный трафик проксирует на локальный nginx (127.0.0.1:8443) через `dns_overrides`.
3. **Telegram-бота** (`gotelegram-bot/bot.py`, python-telegram-bot v21+) — управление прокси со смартфона: статус/ссылка/QR/перезапуск/бекап/смена режима/смена домена/смена шаблона/переключение языка per-user (RU/EN).

Архитектура stealth-режима:
```
Клиент Telegram  ──┐
                   ├─► anten-ka.com:443 (telemt, 0.0.0.0:443)
Обычный браузер ───┘                 │
                                     │  dns_overrides:
                                     ▼  "anten-ka.com:8443:127.0.0.1"
                            127.0.0.1:8443 (nginx)
                                     │
                                     ▼
                          /var/www/gotelegram-site/ (HTML шаблон)
```

Lite-режим (без домена):
```
Клиент Telegram ─► IP:443 (telemt) ─► Telegram DC
Fake-TLS эмулируется (tls_emulation=true), mask-трафик падает никуда
tls_domain = google.com (или любой из QUICK_DOMAINS)
```

---

## 2. Репозиторий и ветки

```
anten-ka/gotelegram_pro
├── main          ← продакшен и дефолтная установка
├── beta          ← следующая публичная сборка для проверки пользователями
└── alpha         ← ранние личные сборки владельца
```

**Правило коммитов:** новые рискованные изменения сначала идут в `alpha`, затем после ручной проверки в `beta`. В `main` попадает только код, который можно считать установочным по умолчанию. Перед любым рискованным переносом или force-update создавай rollback tag/release для текущего состояния целевой ветки.

**Инструменты для коммитов:**
- Основной путь — обычный `git commit` + `git push` в нужную ветку после локальной проверки.
- Если окружение не может пушить напрямую, используй GitHub REST API с PAT из переменной окружения `GOTELEGRAM_PAT`.
- Workflow через GitHub API: `POST git/blobs` для каждого файла → `POST git/trees` (с `base_tree` от текущего HEAD) → `POST git/commits` (parents=[текущий HEAD]) → `PATCH git/refs/heads/<main|beta|alpha>` (sha=новый commit) → при необходимости `POST /releases`.
- **Важно про `base_tree`:** при частичном обновлении (1-2 файла) ОБЯЗАТЕЛЬНО передавать `base_tree` — иначе дерево получится только из переданных файлов, и все остальные файлы пропадут из коммита. `base_tree` можно опускать только при коммите с полным набором файлов.

---

## 3. Карта файлов

```
install.sh                     главная точка входа, CLI-меню из 5 разделов
install_gotelegram_bot.sh      legacy-установщик бота (функционал продублирован в install.sh)
bootstrap.sh                   установщик приватного репо через raw.githubusercontent.com с PAT
templates_catalog.json         каталог 33 467 шаблонов, 18 категорий, 5 источников (~14MB)
DOCS_HUMAN.md                  документация для пользователя (этот каталог)
DOCS_AI.md                     этот файл
README.md                      короткая входная точка проекта

lib/common.sh                  цвета, log_*, confirm, select_option, _valid_ip, config_get,
                               save_gotelegram_config, get_server_ip, run_with_spinner,
                               version, GOTELEGRAM_VERSION, пути
lib/telemt.sh                  download_telemt, install_telemt_full, start/stop/restart_telemt,
                               update_telemt, remove_telemt, telemt_status, telemt_logs,
                               install_telemt_service (systemd юнит)
lib/telemt_config.sh           generate_telemt_toml (TOML v3), generate_proxy_link,
                               get_config_value, validate_telemt_config, QUICK_DOMAINS[],
                               select_quick_domain, select_port, show_proxy_info[_pro]
lib/templates_catalog.sh       download_template, select_category, select_template,
                               show_template_preview, поддержка 5 источников
lib/website.sh                 nginx + certbot + деплой шаблона
lib/backup.sh                  create_backup, restore_backup, list_backups
lib/i18n.sh                    t(), tf(), switch_language(), загрузка lang/ru.sh и lang/en.sh
lib/lang/ru.sh                 328 i18n ключей на русском
lib/lang/en.sh                 328 i18n ключей на английском
lib/stats.sh                   телеметрия (опциональная)
lib/edgeips.sh                 учёт трафика по каждому внешнему адресу + сторож
                               запасного входа по SSH (раз в 5 мин из цикла stats)

gotelegram-bot/bot.py          Telegram-бот (python-telegram-bot v21+)
gotelegram-bot/config.example.env
gotelegram-bot/requirements.txt
gotelegram-bot/README.md
gotelegram-bot/lang/*.json     i18n для бота с per-user persistence

admin-web/server.py            backend локальной админки на Python stdlib
admin-web/static/index.html    shell интерфейса админки
admin-web/static/app.js        frontend логика, API calls, графики, таблицы
admin-web/static/styles.css    responsive light/dark UI
```

**Где что лежит на VPS после установки:**
| Что | Путь |
| --- | --- |
| Репо-скрипты | `/opt/gotelegram/` |
| Symlink запуска | `/usr/local/bin/gotelegram` → `/opt/gotelegram/install.sh` |
| Конфиг goTelegram Pro (JSON) | `/opt/gotelegram/config.json` |
| Конфиг telemt | `/etc/telemt/config.toml` |
| Бинарник telemt | `/usr/local/bin/telemt` |
| Systemd юнит telemt | `/etc/systemd/system/telemt.service` |
| Bot | `/opt/gotelegram-bot/` |
| Systemd юнит бота | `/etc/systemd/system/gotelegram-bot.service` |
| Сайт | `/var/www/gotelegram-site/` |
| nginx конфиг | `/etc/nginx/sites-available/gotelegram` |
| nginx enabled | `/etc/nginx/sites-enabled/gotelegram` |
| Бекапы | `/opt/gotelegram/backups/` |
| Лог goTelegram Pro | `/var/log/gotelegram.log` |
| Логи telemt | `journalctl -u telemt` |
| Логи бота | `journalctl -u gotelegram-bot` |

---

## 4. Формат telemt v3 TOML (КРИТИЧЕСКИ ВАЖНО)

telemt v3.3.39 использует собственный TOML-формат, **несовместимый с mtg и mtproto-proxy**. Попытка скормить ему старый формат (`[security]`, `[[users]]`, `[listen] bind_to`) приводит к тому, что telemt молча игнорирует секции и запускается с дефолтами → клиенты отваливаются с ошибкой SNI.

### Минимальный рабочий конфиг для Lite-режима

```toml
[server]
port = 443
listen_addr_ipv4 = "0.0.0.0"

[censorship]
tls_domain = "google.com"
mask = true
mask_port = 443
tls_emulation = true    # true → telemt сам эмулирует TLS handshake от имени tls_domain

[access.users]
main = "HEX_SECRET_32"  # 16 байт = 32 hex символа, БЕЗ префикса ee
```

### Минимальный рабочий конфиг для Pro-режима (stealth)

```toml
[server]
port = 443
listen_addr_ipv4 = "0.0.0.0"

[censorship]
tls_domain = "anten-ka.com"
mask = true
mask_port = 8443
tls_emulation = false   # false → mask-трафик идёт на mask_port, nginx имеет свой Let's Encrypt cert

[access.users]
main = "HEX_SECRET_32"

[network]
dns_overrides = ["anten-ka.com:8443:127.0.0.1"]
# формат host:port:ip — именно три поля через двоеточие
```

### Рабочий Pro-конфиг под прямой ТСПУ (2.6.x — КРИТИЧЕСКИ)

С 2.6.0 `generate_telemt_toml` в Pro добавляет анти-ТСПУ параметры (telemt 3.4.15):
`client_mss = "tspu"` (MSS=92), `tls_emulation=false`, `mask_shape_hardening=true`,
бакеты `512/4096`, `above_cap_blur 64`, `mask_timing_normalization 180..320ms`,
`server_hello_delay_min_ms=20`, `[censorship.tls_fetch] profiles=[...]`. Это РАБОЧИЕ
значения (как на эталоне 203.0.113.20), не трогать без причины.

⚠️ **КРИТИЧЕСКИ: `client_mss="tspu"` (MSS=92) требует congestion control `cubic`.**
С `bbr` реальные Telegram-клиенты ловят `Telegram handshake timeout` (а fake-TLS
probe при этом ПРОХОДИТ — не верь probe!). С 2.6.3 это форсит `apply_network_sysctl()`
(`cubic` + `fq_codel`; sed глушит `bbr` в `/etc/sysctl.conf`, т.к. он перебивает
drop-in'ы в `/etc/sysctl.d/`). См. §10 баг #29.

### Поля по секциям (полный набор, который telemt принимает в дефолтном gen-config)

- `[general]` — modes, telemetry, links (опционально, можно не трогать).
- `[general.modes]` — включение/отключение режимов прокси.
- `[general.telemetry]` — отправка статистики (по умолчанию выключено).
- `[general.links]` — формат публикуемых ссылок.
- `[server]` — `port`, `listen_addr_ipv4`, `listen_addr_ipv6`.
- `[server.api]` — HTTP-админка (не используем).
- `[server.conntrack_control]` — ограничения на conntrack.
- `[timeouts]` — таймауты handshake, idle и т.д.
- `[network]` — `ipv4`, `ipv6`, `prefer`, `stun_servers`, `dns_overrides` (мы пишем `ipv4 = true`, `ipv6 = false`, `prefer = 4`).
- `[censorship]` — `tls_domain`, `mask`, `mask_port`, `tls_emulation`, `unknown_sni_action` (значения: `Drop`, `Proxy`).
- `[censorship.tls_fetch]` — параметры получения реального cert от tls_domain (используется только если `tls_emulation=false` И нет dns_overrides).
- `[access]` — rate limits, доступ.
- `[access.users]` — таблица `name = "secret"` (секрет — 32 hex).
- `[access.user_max_unique_ips]` — опциональная таблица `name = число`, ограничивает одновременные уникальные IP на ключ; в goTelegram Pro значение `0` в UI удаляет строку и означает безлимит.

### Почему `unknown_sni_action = Drop` нас кусал

В Pro-режиме при старом конфиге с `tls_domain = anten-ka.com` telemt дропает всех клиентов, которые приходят с другим SNI. Если потом перегенерировать конфиг в Lite (`tls_domain = google.com`), но НЕ перезапустить telemt полноценно (`systemctl start` no-op на живом сервисе), то у telemt в памяти остаётся старое `anten-ka.com`, и клиенты с SNI=google.com дропаются. Симптом пользователя: «ключ Lite не работает, telemt живой». Фикс — в `start_telemt()`, см. раздел 10, баг #23.

### Fake-TLS секрет (ee-формат)

Что присылается клиенту в ссылке `tg://proxy?...`:

```
секрет_в_ссылке = "ee" + <hex_secret_32chars> + <hex(tls_domain)>
```

Пример для secret=`2de6920b1e17ccd440933ba0600f578f6` (длинное — 32 hex) и domain=`google.com`:
- hex(google.com) = `676f6f676c652e636f6d`
- итог: `ee2de6920b1e17ccd440933ba0600f578f676f6f676c652e636f6d`

В конфиге telemt (`[access.users] main = ...`) секрет хранится **без** префикса `ee` и **без** hex-домена. telemt сам склеивает при handshake. Функция `generate_proxy_link()` в `lib/telemt_config.sh` делает эту конкатенацию при формировании ссылки.

### Systemd юнит

Генерируется функцией `install_telemt_service()` в `lib/telemt.sh`:
```ini
[Unit]
Description=goTelegram Pro MTProxy (telemt engine)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
ExecStart=/usr/local/bin/telemt run /etc/telemt/config.toml
Restart=always
RestartSec=5
LimitNOFILE=65535
NoNewPrivileges=true
ProtectSystem=full
ProtectHome=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

- `ProtectSystem=full` (НЕ `strict`!) — telemt пишет cache-файлы, при `strict` он падает.
- `User=root` — telemt нужен для bind на 443. Можно заменить на `telemt` + `AmbientCapabilities=CAP_NET_BIND_SERVICE`, но это усложнение ради эстетики.

---

## 5. Жизненный цикл установки (Lite)

Функция `install_lite_mode` в `install.sh`. Порядок:

1. `select_quick_domain` (lib/telemt_config.sh) — интерактивный выбор `tls_domain` из списка QUICK_DOMAINS[] (20 доменов: google.com, microsoft.com, cloudflare.com, apple.com, amazon.com, github.com …). Функция **ОБЯЗАТЕЛЬНО** выводит UI через `>&2`, потому что она вызывается через `$()` — см. раздел 10, баг #14.
2. `select_port` — порт (443 по умолчанию, проверяется занятость через `check_port`).
3. Генерация секрета: `openssl rand -hex 16` → 32 hex символа.
4. `install_telemt_full` → `download_telemt` (скачивает бинарник из telemt/telemt GitHub Releases с фильтром `telemt-x86_64-linux-gnu.tar.gz`) → `install_telemt_service` → `enable_telemt`.
5. `generate_telemt_toml "$secret" "$port" "lite" "$domain" "$port"` → пишет `/etc/telemt/config.toml`.
6. `save_gotelegram_config` → пишет `/opt/gotelegram/config.json` с полями `mode=lite`, `domain=""`, `mask_host=$domain`, `secret`, `port`.
7. `start_telemt` → **restart** если уже active (см. баг #23), иначе `start`. Проверяет `systemctl is-active` через 2 сек.
8. `show_proxy_info` — выводит IP/port/secret/tls_domain и `tg://proxy?...` ссылку, QR через qrencode если есть.

## 6. Жизненный цикл установки (Pro / stealth)

Функция `install_pro_mode` в `install.sh`. Добавляет к Lite-флоу:

1. Запрос домена (`read_user_input domain`), `validate_domain` — проверка формата.
2. `dig +short $domain` → проверка DNS = IP VPS. Если не совпало — предупреждение, но продолжаем (пользователь может знать лучше).
3. `apt install nginx certbot python3-certbot-nginx` (если ещё нет).
4. `interactive_template_selection` → `select_category` → `select_template` → `show_template_preview` → возвращает `tpl_id`. Все UI через `>&2` — иначе ID замусоривается.
5. `download_template "$tpl_id"` → клонирует репо шаблона в temp, проверяет `index.html`, копирует в `/var/www/gotelegram-site/`. Для StartBootstrap проверяет `dist/index.html` — см. баг #21.
6. Запись временного nginx-конфига на порту 80 (для challenge), reload nginx.
7. `certbot --nginx -d $domain` → получает Let's Encrypt сертификат.
8. Переписывание nginx-конфига: listen `127.0.0.1:8443 ssl`, root `/var/www/gotelegram-site`, используем cert от certbot.
9. Генерация `telemt/config.toml` в режиме `pro` с `tls_emulation=false`, `mask_port=8443`, `dns_overrides = ["$domain:8443:127.0.0.1"]`.
10. `save_gotelegram_config mode=pro domain=$domain ...`.
11. `start_telemt` (restart-safe) → `show_proxy_info_pro` выводит ссылку через доменное имя.

---

## 7. Правила subshell-capture (железно)

Это **самое частое место ошибок** в проекте. Любая функция, которая вызывается через `$(...)` для получения значения, должна писать ВСЕ логи и UI в stderr (`>&2`). Единственный разрешённый вывод в stdout — это финальный `echo "$result"` с возвращаемым значением.

Что обязательно `>&2`:
- **Все** `log_info/success/warning/error/step/dim` в `lib/common.sh` (это уже сделано).
- `confirm`, `select_option` в `lib/common.sh` (сделано).
- `select_quick_domain`, `select_port` в `lib/telemt_config.sh` (сделано).
- `select_category`, `select_template`, `show_template_preview` в `lib/templates_catalog.sh` (сделано, баг #18 был о том, что 4 строки в show_template_preview выводили без >&2).
- Любой `echo`/`printf` внутри `download_template` при ошибочном пути — см. баг #22 (`ls -la` без `>&2` замусоривал вывод).
- Любая новая интерактивная функция, которую добавляешь.

Чек-лист когда пишешь новую функцию:
1. Она вызывается через `$()`? → все echo/printf/log_* через `>&2`, кроме финального.
2. Возвращает строку через echo → именно **одна** финальная строка, без лишних переводов строки.
3. Ошибочный путь (early return) → НЕ писать в stdout, только в stderr, `return 1`.

---

## 8. SCRIPT_DIR и symlink

`install.sh` запускается через symlink `/usr/local/bin/gotelegram → /opt/gotelegram/install.sh`. Если наивно использовать `dirname "${BASH_SOURCE[0]}"`, получим `/usr/local/bin`, а там нет каталога `lib/`. Правильный паттерн:

```bash
SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(dirname "$SCRIPT_PATH")"
```

`readlink -f` резолвит symlink до реального файла → `SCRIPT_DIR=/opt/gotelegram`, и `source "$SCRIPT_DIR/lib/common.sh"` работает. Это зафиксировано в баге #19.

---

## 9. Каталог шаблонов

Файл `templates_catalog.json`, ~14MB, 33 467 записей в 18 категориях. Формат — категории со вложенными шаблонами:

```json
{ "categories": [ { "id": "business", "name": "Бизнес и корпоративные", "icon": "briefcase",
    "templates": [ { "id": "h5up_aerial", "name": "Aerial", "source": "html5up",
        "description": "Корпоративный сайт", "repo_url": "https://github.com/zce/html5up",
        "sparse_path": "aerial", "preview_url": "https://html5up.net/aerial/",
        "license": "CC-BY-3.0", "stars": 120 } ] } ] }
```

`license` и `stars` необязательны (есть у записей, прошедших проверку 03.09.2026). `preview_url` пустой, если превью мертво.

Источники (`download_template` в `lib/templates_catalog.sh`):

| Источник | Префикс id | Метод |
| --- | --- | --- |
| html5up, learning-zone, dawidolko | `h5up_*`, `lz_*`, `da_*` | sparse checkout папки `sparse_path` из большого репо (`git -C … sparse-checkout set`) |
| startbootstrap, themewagon | `sb_*`, `th_*` | `git clone --depth 1` отдельного репо |
| github | `gh_*` | то же: свободные репозитории со всего GitHub (MIT/Apache/BSD/CC0/CC BY), найденные и проверенные клоном 03.09.2026 |

Где страница: `_tpl_pick_index_dir` — `dist/` → `build/` → корень → `html/ theme/ live/ public_html/ site/ _site/` → самый неглубокий `index.html`, минуя `documentation/ docs/ src/ public/ node_modules/ test*/ example*/`; второй проход разрешает `examples/` и `docs/`; `home.html`/`main.html` без `index.html` дублируется в `index.html`. Копирование — `cp -r "$dir/."` (точечные файлы не теряются).

Готовность страницы — `_tpl_page_ok`: отказ с кодом `app_shell` при `%PUBLIC_URL%`, `src="/src/*.jsx"` или тексте < 300 символов без картинок (оболочки CRA/Vite дают белый экран); `broken_assets`, если из ≥2 локальных css/js нет ни одного; `no_index` — страницы нет; `download_failed` — клон пустой (ошибка git показывается: stderr сохраняется в `_TPL_GIT_ERR`). Код причины пишется в `$output_dir/.last_error` (download_template зовут через `$(…)`, переменная из подоболочки не доходит); `bot_action_change_template` отдаёт его боту в `code`, бот показывает причину по ключам `tpl_err_*`.

Деплой (`deploy_template_to_nginx` в `lib/website.sh`) после копирования вызывает `strip_template_service_files`: из корня и первого уровня удаляются README*/LICENSE*/*.md/package*.json/gulpfile*/webpack*/.github/.gitignore/.editorconfig/.DS_Store и т.п. — иначе `https://домен/Readme.txt` отдавал «Template Name … Author …» (след для маскировки, было у 996 шаблонов из 1457). Точечные файлы nginx не отдаёт (`location ~ /\. { deny all; }`), `.gotelegram_template_id` → 403.

SSH-выбор (`select_template`): страницы по 25, `n`/`p`, слово — поиск по названию/id, `r` — случайный, `0` — назад; неудачная загрузка возвращает к списку той же категории (`interactive_template_selection`, до 5 попыток). Бот листает по `PRO_TEMPLATES_PER_PAGE` = 24, ключи шаблонов — sha1[:12] в `context` (callback ≤ 64 байт).

Аудит 03.09.2026: из прежних 1801 оставлено 1457 (убраны админ-панели, пустые SPA-оболочки, Next.js-исходники, дубли lz_* = da_*, заглушки, битая вёрстка), 43 мёртвых превью очищены; 9747 новых из GitHub добавлены после клона и проверки страницы, ~81 тысяча кандидатов из очереди поиска не обработаны (конвейер отбирал ~95 в минуту). Дубли по `repo_url`+`sparse_path` при слиянии отбрасываются. ColorlibHQ как источник не использовать — WordPress-темы (PHP).

---

## 10. История багов (не наступать на те же грабли)

Все решены в текущем HEAD (`main`). Перечисление для того, чтобы новый агент не «чинил» то, что уже починено, и понимал контекст.

1. `telemt.sh grep` — URL формат `telemt-x86_64-linux-gnu.tar.gz`, arch перед `linux`. Нужен `grep -iE "$arch_pattern"` + цепочка фильтров (`grep -i linux`, `grep -v sha256`, `grep gnu`, `head -1`).
2. `bot.py safe_edit_message` — обёртка для `query.edit_message_text`, ловит `BadRequest: message is not modified`.
3. `bot.py QR cleanup` — `os.remove(qr_file)` в `finally` блоке.
4. `common.sh _valid_ip` — проверка октетов 0-255, раньше пропускал 999.
5. `common.sh os-release injection` — блокирует `;`, backticks, `$(`, `${` при парсинге `/etc/os-release`.
6. `common.sh config_get` — return codes: `0=ok`, `2=файл отсутствует`, `3=невалидный JSON`.
7. `common.sh run_with_spinner` — stderr в temp-файл, показ первых 3 строк при ошибке.
8. `install.sh` — проверка пустого домена **перед** `validate_domain` (валидатор падал на пустой строке).
9. `install.sh mode_choice` — санитизация `${mode_choice:-}` чтобы `set -u` не ронял скрипт.
10. `backup.sh` — инициализация `final_file`/`tar_file`/`backup_file` перед ветвлением (иначе unset var).
11. `bot.py XSS` — `html.escape(preview_url, quote=True)` с двойными кавычками.
12. `common.sh` — версия была устаревшая, обновлена (сейчас 2.4.1).
13. `install.sh` — добавлен пункт 12 меню (Telegram-бот) с подменю установка/статус/логи/настройки/удаление.
14. **КРИТИЧЕСКИЙ subshell capture** — интерактивные функции (`select_quick_domain`, `select_port`, `select_category`, `select_template`, `show_template_preview`) выводили UI в stdout. Вызов через `$()` → меню уходило в переменную. Фикс: весь UI через `>&2`.
15. **КРИТИЧЕСКИЙ download_telemt extract** — `find -newer` не находил извлечённый файл (таймстамп архива старше). Фикс: извлечение в отдельную директорию `$extract_dir` + проверка размера файла + fallback через `file` для определения ELF.
16. `common.sh log_*` — все `log_info/success/warning/error/step/dim` выводят в stderr (`>&2`).
17. `common.sh confirm/select_option` — UI через `>&2`.
18. **КРИТИЧЕСКИЙ show_template_preview stdout leak** — 4 строки (блок «Спасибо авторам» + разделители) выводили без `>&2`. Цепочка `interactive_template_selection → select_template → show_template_preview` → мусор подмешивался к `tpl_id` → `download_template` получал мусорный ID → «шаблон не содержит index.html». Фикс: `>&2` ко всем 4 echo.
19. **symlink SCRIPT_DIR** — `readlink -f "${BASH_SOURCE[0]}"` для резолва symlink `/usr/local/bin/gotelegram`.
20. **bootstrap.sh** — скачивание приватного репо через `raw.githubusercontent.com/.../bootstrap.sh?token=...`, создание symlink, запуск install.sh.
21. **КРИТИЧЕСКИЙ StartBootstrap dist/** — sb_* шаблоны хранят production в `dist/`. Фикс: клонируем в `$sb_tmp`, проверяем `dist/index.html`, копируем `dist/*` в `$clone_dir`, + universal fallback через `find`.
22. **templates_catalog.sh ls stdout leak** — `ls -la` в блоке ошибки `download_template` шёл в stdout. Фикс: `>&2`.
23. **КРИТИЧЕСКИЙ start_telemt stale config (v2.4.1)** — `systemctl start` это no-op для уже активного сервиса. После переустановки Lite поверх Pro конфиг на диске менялся, но telemt держал в памяти старый `tls_domain=anten-ka.com` и дропал клиентов с SNI=google.com. Фикс: `start_telemt` теперь делает `restart` если сервис уже активен. См. `lib/telemt.sh:189-213`.
24. **КРИТИЧЕСКИЙ safe_edit_message TypeError (v2.4.2)** — в iter2 аудите субагент нашёл: `cb_pro_confirm` в success-пути вызывал `safe_edit_message(query, text, ..., disable_web_page_preview=True)`, но сигнатура обёртки этот kwarg не принимала. Runtime TypeError прямо в хэппи-пути смены шаблона из бота. Фикс: добавлен `disable_web_page_preview: Optional[bool] = None` + условный форвард через `**kwargs`.
25. **template_id field name mismatch (v2.4.2)** — `save_gotelegram_config` всегда писал ключ `template_id`, но `bot.py` читал `config.get('template')` и писал `config['template']` в `handle_text_message`. Результат: статус шаблона в боте никогда не отображался, а поле в JSON появлялось фантомно и не использовалось. Фикс: везде только `template_id`. Исторические чтения совместимы через `config.get("template_id") or config.get("template")` для legacy конфигов.
26. **jq 1.5 compat в bot_update_config_field (v2.4.2)** — использовалось `jq '... | .updated_at = (now | todate)'`, но фильтр `now|todate` только с jq 1.6. На Debian 10 jq 1.5 падал с syntax error, config не обновлялся. Фикс: генерим timestamp через `date -Iseconds` в shell и передаём через `jq --arg t`.
27. **Отсутствующая валидация tpl_id/domain (v2.4.2)** — `tpl_id` из `callback_data` и `domain` из текстового ввода передавались напрямую в subprocess как `--template=$x`. Defense-in-depth: в bot.py добавлены `_TPL_ID_RE = ^[A-Za-z0-9_-]{1,64}$` и `_DOMAIN_RE`, в install.sh — `validate_domain` перед `generate_telemt_toml`. Малформный ввод отвергается early.
28. **КРИТИЧЕСКИЙ race в bot_action_dispatch (v2.4.3)** — iter3-тест с 3 параллельными `change-lite-domain` дал `{"status":"error","code":"no_secret"}` на одном из вызовов. Причина: `bot_update_config_field` делает `jq ... > tmp && mv tmp config.json`; когда параллельный процесс заходил в `get_config_value secret` в момент между `>` и `mv`, он видел пустой/частичный файл. `asyncio.Lock` в боте ловил только внутри-процессные гонки, но не CLI-level. Фикс: `flock -w 30 9 < /var/lock/gotelegram-bot-action.lock` вокруг всего диспатчера. Новый error code: `lock_timeout` (`EX_TEMPFAIL`=75).
29. **КРИТИЧЕСКИЙ `bbr` + `client_mss=tspu` ломает MTProto-handshake (v2.6.3).** После миграции 2.5→2.6.1 реальные Telegram-клиенты ловили `Telegram handshake timeout` (fake-TLS probe ПРОХОДИЛ). Две сессии обвиняли значения маскировки и `client_mss`. Реальная причина — congestion control `bbr` в связке с `client_mss="tspu"` (MSS=92): крошечный MSS + пейсинг BBR не доводят handshake на части провайдеров. Эталон 203.0.113.20 работал на `cubic`; тест 203.0.113.30 дефолтил в `bbr` (прописан в `/etc/sysctl.conf`, перебивает drop-in'ы). Фикс: `apply_network_sysctl()` форсит `cubic`+`fq_codel`+буферы 4MB и sed'ит `bbr` из `/etc/sysctl.conf`; вызывается из `auto_migrate_legacy_state` и из install lite/pro. Конфиг telemt при этом = исходный 2.6.1 (он был правильный). Ловушка: fake-TLS probe этот баг НЕ ловит (только TLS-уровень) — проверять реальным клиентом. Промежуточный 2.6.2 был ошибочным (развернул значения конфига под присланный шаблон вместо эталона) и заменён 2.6.3.

---

## 11. Telegram-бот

Файл `gotelegram-bot/bot.py`, python-telegram-bot v21+, systemd сервис `gotelegram-bot.service`.

Ключевые моменты:
- **Admin ID** — бот отвечает только пользователю с `ADMIN_TG_ID` из `.env`. Всё остальное игнорируется.
- **safe_edit_message** — обёртка над `query.edit_message_text` + `query.edit_message_caption`, глотает `BadRequest: message is not modified`. Начиная с v2.4.2 принимает опциональный `disable_web_page_preview` — не забудь прокинуть его если показываешь ссылку-превью (раньше вызов с этим kwarg ловил TypeError в runtime). Используй её всегда вместо прямого вызова.
- **Language per-user** — файл `/opt/gotelegram-bot/user_langs.json` хранит `{user_id: "ru"/"en"}`. При каждом сообщении бот читает язык пользователя и подставляет строку через `t(user_id, key)` / `tf(user_id, key, ...)`.
- **QR-коды** — генерятся в `/tmp/gotelegram_qr_*.png`, отправляются как `InputFile`, удаляются в `finally`.
- **Шаблон preview в HTML** — URL экранируется через `html.escape(url, quote=True)` (баг #11).
- **Системные действия (v2.4.2+)** — бот реально умеет менять шаблон и домен маскировки. Хелпер `run_bot_action(action, **kwargs)` вызывает `subprocess.run(["/opt/gotelegram/install.sh", "--action=X", "--json", ...])` и парсит JSON. Два коллбэка: `cb_pro_confirm` (change-template) и `cb_lite_domain` (change-lite-domain). Оба обёрнуты в `async with _BOT_ACTION_LOCK` (глобальный `asyncio.Lock()`) — сериализуют параллельные callback'и внутри процесса бота. Входы валидируются ДО subprocess: `_TPL_ID_RE = r"^[A-Za-z0-9_-]{1,64}$"`, `_DOMAIN_RE` (RFC-like). Малформный ввод отвергается с понятным сообщением, не доходя до shell.
- **Поле `template_id` в config.json** — канонический ключ для текущего шаблона. Раньше (до v2.4.2) бот читал `config['template']` и писал в него же в `handle_text_message`, но `save_gotelegram_config` всегда писал `template_id` → поле статуса никогда не отображалось. Используй только `template_id`.
- **Устанавливается** из меню `install.sh → 4) Telegram-бот → Установить`. Пользователь вводит BotFather token + свой Telegram ID, `.env` пишется в `/opt/gotelegram-bot/.env`.
- **Обновляется автоматически (v2.5.0+)** при повторном bootstrap/update: `auto_update_bot_if_possible` сравнивает новые файлы в `$SCRIPT_DIR/gotelegram-bot/` с установленными `/opt/gotelegram-bot/{bot.py,i18n.py,requirements.txt,lang/*.json}`. Если есть отличия и сервис уже установлен, вызывается `bot_install >/dev/null`, `.env` сохраняется, зависимости обновляются, systemd unit переписывается и `gotelegram-bot` перезапускается. Это закрывает сценарий 2.4.x → 2.5.0, где bootstrap обновлял `/opt/gotelegram/gotelegram-bot/`, но рабочий сервис продолжал запускать старый `/opt/gotelegram-bot/bot.py`.

### 11.1 Non-interactive action bridge (install.sh ↔ bot)

Бот общается с CLI через жёсткий JSON-протокол. Единая точка входа — `bot_action_dispatch` в `install.sh`.

```bash
/opt/gotelegram/install.sh --action=<name> [--template=ID|--domain=HOST] --json
```

**Доступные action'ы:**
- `change-template --template=<tpl_id>` — только в Pro режиме. Скачивает шаблон, деплоит в nginx, обновляет `config.json.template_id`.
- `change-lite-domain --domain=<host>` — только в Lite режиме. Регенерит telemt TOML с новым `tls_domain`, валидирует, рестартит telemt, обновляет `config.json.{domain,mask_host}`.

**Формат ответа (stdout, последняя строка):**
```json
{"status":"success","message":"...","<extra>":"..."}
{"status":"error","message":"...","code":"<machine_code>"}
```

Коды ошибок: `missing_arg`, `invalid_domain`, `wrong_mode`, `unknown_template`, `download_failed`, `deploy_failed`, `no_secret`, `gen_failed`, `validate_failed`, `restart_failed`, `unknown_action`, `lock_timeout`.

**Сериализация (v2.4.3+):** `bot_action_dispatch` оборачивает вызов в `flock -w 30 9 < /var/lock/gotelegram-bot-action.lock`. Это защищает от гонок при:
1. Одновременных callback'ах внутри бота (asyncio.Lock уже ловит это, но flock — defense-in-depth).
2. Параллельных CLI-вызовах (бот + ручной SSH, или два бот-процесса — теоретически).

Если таймаут лока (30 с), диспетчер возвращает `exit 75` (`EX_TEMPFAIL`) и JSON `{"status":"error","code":"lock_timeout"}`. `flock` идёт из `util-linux` — добавлен в `critical` зависимости в `ensure_deps`.

**История:** до v2.4.2 коллбэки были stub'ами («сделай через CLI»). В v2.4.2 подключили реальные action'ы. В iter3-тестировании на VPS обнаружилась гонка: `bot_update_config_field` делает `jq ... > tmp && mv tmp config.json`, и параллельный процесс мог прочитать `config.json` в промежутке, увидев пустоту → ошибка `no_secret`. v2.4.3 починил flock'ом.

---

## 12. i18n (bash CLI)

`lib/i18n.sh` экспортирует функции:

```bash
t "key"              # вернуть строку на текущем языке
tf "key" arg1 arg2   # t + printf-интерполяция
switch_language ru|en
```

Под капотом — ассоциативный массив `I18N`, ключи в `lib/lang/ru.sh` и `lib/lang/en.sh`. ~328 ключей.

Добавление нового ключа:
1. Придумай стабильный key (snake_case, без пробелов): `menu_install`, `error_port_busy`.
2. Добавь строку в `lib/lang/ru.sh`: `I18N[menu_install]="Установить / обновить"`.
3. Добавь строку в `lib/lang/en.sh`: `I18N[menu_install]="Install / update"`.
4. В коде вызови `t menu_install`.
5. Если есть интерполяция — используй `%s`/`%d`: `I18N[info_port]="Порт %s свободен"` + `tf info_port "$port"`.

Выбор языка сохраняется в `$GOTELEGRAM_CONFIG` → `config.json` → ключ `lang`. Первый запуск — интерактивный выбор.

---

## 13. Бекапы

`lib/backup.sh`. Собирает в `.tar.gz`:
- `/etc/telemt/config.toml`
- `/opt/gotelegram/config.json`
- `/opt/gotelegram/disabled_users.json`
- `/var/www/gotelegram-site/` (если есть)
- `/opt/gotelegram/custom_templates/`, `/opt/gotelegram/templates_catalog.json`
- `/opt/gotelegram/stats_history.csv`, `/opt/gotelegram/user_stats_history.csv`, `/opt/gotelegram/shared-443.json`
- `/opt/gotelegram-bot/.env`, bot i18n/lang
- `/opt/gotelegram-admin/server.py`, `/opt/gotelegram-admin/static/`
- `/etc/letsencrypt/live/<domain>/` + `/etc/letsencrypt/archive/<domain>/` + `/etc/letsencrypt/renewal/<domain>.conf` (если Pro)
- `/etc/nginx/sites-available/gotelegram` (если есть)

Складывает в `/opt/gotelegram/backups/gotelegram_backup_YYYYMMDD_HHMMSS.tar.gz`.

`restore_backup <file> [password] [yes]` разворачивает архив обратно, перезапускает telemt, nginx, bot и admin. Третий аргумент `yes` нужен для неинтерактивного restore из web-admin/бота; перед ним они создают свежий safety backup. Restore понимает legacy-архивы раннего бота `backup_*.tar.gz`, где внутри лежали только `opt/gotelegram/config.json` и `etc/telemt/config.toml`.

Расписания бекапов: `set_backup_schedule off|daily|weekly|monthly` пишет `/etc/systemd/system/gotelegram-backup.{service,timer}` и `/opt/gotelegram/backup_schedule.json`. OnCalendar: daily `*-*-* 03:20:00`, weekly `Sun 03:20:00`, monthly `*-*-01 03:20:00`. Автоматическая чистка держит 30 последних `.tar.gz*` архивов.

### 13.1 Local Web Admin (v2.5.0)

Локальная web-админка находится в `admin-web/` и устанавливается в `/opt/gotelegram-admin`:

- backend: `admin-web/server.py`, Python stdlib only, без pip/npm dependencies;
- frontend: `admin-web/static/`, vanilla JS/CSS, SVG-график без CDN;
- systemd service: `gotelegram-admin`;
- bind: `127.0.0.1:1984`, доступ только через SSH tunnel;
- токена нет: после SSH tunnel открывается `http://127.0.0.1:1984/`;
- write-запросы дополнительно требуют `X-GoTelegram-Admin: 1`, фронтенд добавляет его автоматически.
- язык панели читается из `config.json.language`, затем из `/opt/gotelegram/.language`, fallback `en`; `POST /api/settings/language` сохраняет RU/EN в общий конфиг, marker file и bot `.env`; `gotelegram-bot/i18n.py` использует тот же источник как default до per-user override;
- UI построен вкладками (`dashboard`, `traffic`, `keys`, `backups`, `logs`, `settings`), есть иконки меню, полезный overview-блок по реальным TCP/UDP-слушателям 443, light/dark theme в `localStorage` и promo-modal раз в 24 часа через `localStorage`;
- `/api/overview` отдаёт `stats_status`, `admin_bind`, `site_status`, `port_443` и `backup_schedule`; `/api/site/check` проверяет `https://config.domain/` через `127.0.0.1:443` (`curl --noproxy '*' --resolve домен:443:127.0.0.1`, SNI — домен, без DNS сервера; `--max-time 5`) и считает OK только HTTP 200; `/api/stats?range=15m|1h|24h|month` отдаёт выбранное окно и `summary_rows`; `/api/users/<name>/traffic?range=15m|1h|24h|month` отдаёт per-user историю по `telemt total_octets`; `/api/users/<name>/qr` отдаёт PNG QR для Telegram proxy link через `qrencode`; `POST /api/users/<name>/max-ips` пишет `[access.user_max_unique_ips]` (`0` удаляет строку и означает безлимит); `/api/stats/collect` делает разовый сбор, `/api/stats/repair` устанавливает/перезапускает `gotelegram-stats`.
- самопроверка сайта с сервера не ходит через DNS: у части хостеров первый резолвер в `/etc/resolv.conf` теряет запросы, glibc ждёт 5 с и проверка падала при живом сайте (29.10.2026). 443 слушается на `0.0.0.0` во всех вариантах Pro (ядро, nginx stream разводки WEB, общий 443 с 3x-ui), поэтому `127.0.0.1:443` с SNI домена — та же цепочка внутри сервера (443 → ядро/nginx → сайт). DNS, сеть хостера и внешний фаервол эта проверка не видит: DNS сверяют отдельные плашки в шапке (`edgeips_dns_warn`, AAAA). `--noproxy '*'` обязателен: при `https_proxy` в окружении curl идёт в прокси и `--resolve` не действует. В SSH-меню индикатор «Сайт» в шапке (`show_main_menu`) зовёт `site_local_ok <домен>` (`lib/website.sh`; успех — любой HTTP-ответ, таймаут `SITE_PROBE_TIMEOUT`, 5 с); страж в `--selftest` (раздел 11): `show_main_menu` зовёт `site_local_ok` и не зовёт curl наружу; в `install.sh`/`lib/*.sh` нет `curl https://$домен` без `--resolve`/`--connect-to`.

Функции: overview, проверка сайта на HTTP 200, service status/restart, чтение/запись `[access.users]`, enable/disable ключей через `/api/users/<name>/enabled`, per-user лимит одновременных уникальных IP через `[access.user_max_unique_ips]`, генерация proxy links и QR, общий traffic history из `/opt/gotelegram/stats_history.csv`, per-user traffic history из `/opt/gotelegram/user_stats_history.csv` с периодами 15m/1h/24h/month, current stats из `/run/gotelegram/stats_current.json`, список/создание backup, `POST /api/backups/schedule`, `POST /api/backups/restore` (background restore job, только basename из backup dir), структурированные journal logs (`service`, `ok`, `exit_code`, `line_count`, `text`).

Traffic retention: обе CSV-истории хранятся максимум 365 дней. Последние 31 день остаются с поминутным разрешением, более старые точки автоматически уплотняются до одной последней cumulative-точки в час. Чистка/уплотнение запускается не чаще одного раза в час, а per-user сбор пишет данные не чаще одного раза в минуту, даже если systemd-loop вызывает `stats_collect` каждую секунду. Параметры можно переопределить переменными `STATS_RETENTION_DAYS`, `STATS_MINUTE_RETENTION_DAYS`, `STATS_CLEANUP_INTERVAL`.

### 13.1.1 Shared TCP/443 with 3x-ui/Xray

`lib/shared443.sh` добавляет управляемую схему shared-443 через nginx stream `ssl_preread`:

- публичный вход: `0.0.0.0:443` принадлежит nginx stream dispatcher;
- default backend: `127.0.0.1:7443` (`telemt`), а `general.links.public_port` остаётся `443`;
- сайт остаётся за telemt через `dns_overrides` на `127.0.0.1:8443`;
- Xray/3x-ui должен быть перенесён в панели на внутренний target, например `127.0.0.1:9443`, после чего `shared443_enable <domain> <xray-sni> 127.0.0.1:9443` пишет `/opt/gotelegram/shared-443.json` и `/etc/nginx/stream-conf.d/gotelegram-shared443.conf`.

Автоматически переписывать SQLite/JSON 3x-ui нельзя: панель может перегенерировать Xray config и потерять ручные правки. Поэтому goTelegram Pro показывает конфликт прямого bind на `443`, даёт маршрут и включает dispatcher только на уровне собственных конфигов/nginx.

Отключённые ключи хранятся в `/opt/gotelegram/disabled_users.json`: active keys остаются в `/etc/telemt/config.toml` под `[access.users]`, disabled keys удаляются из active block и могут быть возвращены обратно без потери secret. `main` защищён от удаления и отключения. Лимит уникальных IP сохраняется в `[access.user_max_unique_ips]` и не теряется при disable/enable; при удалении ключа строка лимита удаляется. Операции с ключами в web-admin и Telegram-боте берут общий file lock `/run/gotelegram/admin-users.lock`, TOML пишется через temp+replace и quoted keys (`"a.b"`), а telemt restart для add/delete/enable/disable/IP-limit ставится через `systemctl --no-block restart`, чтобы switch в UI не зависал на `wait_tcp_port`.

`install_admin_web` вызывается при установке Telegram-бота. `auto_install_admin_web_if_possible` подхватывает админку после bootstrap/update, если Python уже установлен и файлы отличаются. `auto_update_bot_if_possible` аналогично подхватывает уже установленный Telegram-бот и обновляет рабочий `/opt/gotelegram-bot` без запроса токена. При установке админки скрипт пытается установить/перезапустить `gotelegram-stats`; если это не удалось, оператор может нажать Restart collector в Traffic. Backup v1.6 сохраняет `admin_web/server.py`, `admin_web/static/`, `disabled_users.json`, `stats_history.csv`, `user_stats_history.csv`, `backup_schedule.json` и `shared-443.json`, restore возвращает их, удаляет legacy `admin_web/token` и пробует перезапустить `gotelegram-admin`.

### 13.2 Upgrade migration (v2.5.0)

`install.sh` вызывает `auto_migrate_legacy_state` перед интерактивным меню и перед non-interactive `--action=...` для бота. В интерактивном запуске после миграции также вызываются `auto_update_bot_if_possible` и `auto_install_admin_web_if_possible`, чтобы повторный bootstrap обновлял не только исходники в `/opt/gotelegram`, но и реально запущенные сервисы `/opt/gotelegram-bot` и `/opt/gotelegram/admin-web`. Цель — комфортно обновляться даже с кривой старой логики без переустановки прокси:

- перед изменениями создаётся `/opt/gotelegram/backups/preupgrade_2.5.0_*.tar.gz`;
- из старого `/etc/telemt/config.toml` вытаскиваются все строки `[access.users]`, а не только `main`;
- если `main` отсутствует, он добавляется с первым найденным секретом, чтобы старые ссылки не потерялись;
- сохраняются порт, `tls_domain`, `mask_port`, режим Lite/Pro, домен, язык, `stats_enabled`;
- старый telemt TOML нормализуется под v2.5.0 с `[server.api]` и metrics, затем в него возвращается полный users block;
- если сайт уже развёрнут, но template id неизвестен или старый `config.json` врёт, в `/var/www/gotelegram-site/.gotelegram_template_id` пишется `deployed_site`;
- `config.json` переписывается в актуальный формат, но с сохранением `installed_at` и пользовательских настроек;
- если telemt был активен и TOML был изменён, сервис перезапускается один раз.

Инвариант: миграция должна быть идемпотентной. Маркер `/opt/gotelegram/.migrated_2.5.0` предотвращает повторную нормализацию без причины.

---

## 14. Checklist: как обновить один файл и запушить

1. Прочитай текущее состояние файла: `Read` для локальной версии + `git_get_contents` (raw.githubusercontent.com) если нужно убедиться что на GitHub то же самое.
2. Применяй правки через `Edit` в локальном каталоге `/sessions/.../gotelegram-v2/`.
3. Напиши `C:\Temp\push_<описание>.py`:
   ```python
   import os, base64, json, urllib.request, ssl
   TOKEN = os.environ["GOTELEGRAM_PAT"]
   REPO = "anten-ka/gotelegram_pro"
   BRANCH = "main"  # или beta/alpha для соответствующей ветки
   API = f"https://api.github.com/repos/{REPO}"
   headers = {
       "Authorization": f"Bearer {TOKEN}",
       "Accept": "application/vnd.github+json",
       "X-GitHub-Api-Version": "2022-11-28",
       "User-Agent": "gotelegram-push",
   }
   def req(method, path, body=None):
       data = json.dumps(body).encode() if body else None
       r = urllib.request.Request(API + path, data=data, headers={**headers, "Content-Type": "application/json"}, method=method)
       return json.loads(urllib.request.urlopen(r).read())

   # 1. current ref
   ref = req("GET", f"/git/refs/heads/{BRANCH}")
   parent_sha = ref["object"]["sha"]
   commit = req("GET", f"/git/commits/{parent_sha}")
   base_tree = commit["tree"]["sha"]

   # 2. blobs
   files = {
       "lib/common.sh": open("C:/.../gotelegram-v2/lib/common.sh","rb").read(),
       "DOCS_AI.md":    open("C:/.../gotelegram-v2/DOCS_AI.md","rb").read(),
   }
   tree_items = []
   for path, content in files.items():
       blob = req("POST", "/git/blobs", {"content": base64.b64encode(content).decode(), "encoding": "base64"})
       tree_items.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})

   # 3. tree (С base_tree — обязательно при частичном апдейте)
   tree = req("POST", "/git/trees", {"base_tree": base_tree, "tree": tree_items})

   # 4. commit
   new_commit = req("POST", "/git/commits", {
       "message": "v2.4.1: docs + start_telemt restart-safe",
       "tree": tree["sha"],
       "parents": [parent_sha],
   })

   # 5. patch ref
   req("PATCH", f"/git/refs/heads/{BRANCH}", {"sha": new_commit["sha"], "force": False})
   print("pushed:", new_commit["sha"])
   ```
4. Запускай через Desktop Commander `start_process` с `shell="cmd.exe"`:
   ```
   cmd /c "python C:\Temp\push_docs.py"
   ```
   НЕ через PowerShell — там stdout не захватывается в нашем окружении.
5. Проверь результат: `GET /commits/<sha>` или открой ветку в браузере вручную.

**Частые грабли:**
- Забыл `base_tree` → все остальные файлы исчезли из коммита. ВСЕГДА передавай `base_tree` кроме случая «чистый коммит со всеми файлами».
- `cp1251` в cmd ломает юникод → пиши в файл через Python с `encoding='utf-8'`, не выводи кириллицу в stdout.
- GitHub API кеширует raw-ответы по path → при проверке обновления используй `?ref=<commit_sha>`, не ветку.

---

## 15. Checklist: тестирование на VPS

VPS: адрес и доступ — у владельца, в репозитории не хранятся.

**Путь из Linux sandbox:** `ssh`/`sshpass` нет, `pip install paramiko` падает (прокси 403). Идём через Windows Python, где paramiko уже установлен.

**Хелперы:**
- `C:\Temp\ssh_cmd.py` — однократная команда, читает из `C:\Temp\ssh_input.txt`, пишет в `C:\Temp\ssh_output.txt`.
- `C:\Temp\ssh_a1.py` / `ssh_a2.py` / `ssh_a3.py` — для параллельных агентов (разные output-файлы).

**Базовый шаблон ssh_cmd.py:**
```python
import sys, paramiko
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(HOST, username="root", password=PASSWORD, timeout=20)  # значения из окружения
cmd = open("C:/Temp/ssh_input.txt", "r", encoding="utf-8").read()
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=60)
out = stdout.read().decode(errors="replace")
err = stderr.read().decode(errors="replace")
open("C:/Temp/ssh_output.txt", "w", encoding="utf-8").write(f"STDOUT:\n{out}\nSTDERR:\n{err}")
ssh.close()
```

**Заливка одного файла через SFTP:**
```python
sftp = ssh.open_sftp()
sftp.put("C:/.../lib/telemt.sh", "/opt/gotelegram/lib/telemt.sh")
sftp.close()
```

**CRLF:** файлы из GitHub API приходят с `\r\n` → ОБЯЗАТЕЛЬНО `sed -i 's/\r$//' /opt/gotelegram/install.sh /opt/gotelegram/lib/*.sh` перед запуском, иначе bash падает с `bad interpreter`.

**chmod:** `chmod +x /opt/gotelegram/install.sh /opt/gotelegram/install_gotelegram_bot.sh` — GitHub раздаёт как 644.

**Быстрая проверка live-состояния:**
```bash
systemctl is-active telemt nginx gotelegram-bot
telemt --version
cat /etc/telemt/config.toml | grep tls_domain
journalctl -u telemt --no-pager -n 30
curl -sk -o /dev/null -w "%{http_code}\n" https://127.0.0.1:8443
```

**Проверка что Lite-ключ реально работает через Fake-TLS:**
```python
import ssl, socket
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
with socket.create_connection(("203.0.113.10", 443), timeout=5) as s:
    with ctx.wrap_socket(s, server_hostname="google.com") as ss:
        print(ss.version(), ss.getpeercert(True)[:40])
```
Ожидаемо: `TLSv1.3`, сертификат с CN=`*.google.com` (его telemt эмулирует через tls_fetch).

---

## 16. Меню (install.sh)

```
Главный экран:
 1) submenu_proxy        (установка, статус, ссылка, share, restart, logs, change mode)
 2) submenu_stats        (show_traffic_stats, toggle_stats, install_stats_collector)
 3) submenu_manage       (backup, restore, update_telemt, website/SSL, remove, language)
 4) menu_bot             (install/start/stop/logs/change token/remove bot, installs admin web)
 5) submenu_about        (version info, promo)
 0) exit

Раздел Proxy:
 1) menu_install         (Lite/Pro выбор, domain, template, certbot)
 2) menu_status          (telemt_status + show_proxy_info)
 3) menu_link            (ссылка + QR)
 4) menu_share           (текст «для друзей»)
 5) menu_restart         (restart_telemt)
 6) menu_logs            (telemt_logs 40)
 7) menu_change_mode     (lite↔pro, смена шаблона, смена домена маскировки)

Раздел Management:
 1) interactive_backup   (create_backup)
 2) interactive_restore  (select_backup + restore_backup)
 3) update_telemt        (check_telemt_update → download → restart)
 4) menu_website         (nginx restart, certbot renew)
 5) menu_remove          (только прокси / только бот / всё)
 6) menu_language        (switch_language ru|en)
```

Диспатчер в `install.sh` (`bot_action_dispatch`) принимает `--action=` для автоматизации из бота. Полный контракт описан в разделе 11.1.

---

## 17. Changelog

- **2.10.4 (2026-08-22) — лимит в 1 уникальный IP запрещён.** Телефон при переходе Wi-Fi ↔ LTE меняет адрес, старые соединения ещё живы, и клиент отбивается собственным лимитом. Замер на боевом: 19 эпизодов за полтора часа, медиана 20 с без связи, максимум 374 с. Единицу больше нельзя выставить ни в одном из трёх интерфейсов: `_users_set_ip_limit_locked` (SSH), `normalize_max_unique_ips` в `admin-web/server.py` и в `bot.py`, плюс клиентская проверка в `app.js` (HTML-полем «0 либо >= 2» не выразить). Ноль по-прежнему означает «нет персонального лимита». Уже проставленные единицы поднимает миграция `gt_migrate_bump_ip_limit_one` — awk строго внутри секции `[access.user_max_unique_ips]`, потому что «до конца файла» задело бы `"x" = 1` в соседних таблицах (проверено макетом). Версия поднята до 2.10.4 намеренно: гейт миграции сравнивает версию с маркером, и без бампа авто-подъём не запустился бы на серверах, уже стоящих на 2.10.3. Ключи `usr_limit_one_warn` / `user_iplimit_one_warn` удалены — предупреждать больше не о чем. Заодно починен вечно красный `test_installer_auto_updates_existing_bot_files`: он требовал буквального соседства трёх вызовов, между которыми давно вставили `auto_update_telemt_if_possible`; теперь проверяется порядок, и контрольным прогоном подтверждено, что тест ловит его нарушение.
- **2.10.3-hotfix (2026-08-22) — обновление ставило ПРЕДЫДУЩУЮ версию.** `bootstrap.sh` качал файлы по URL ВЕТКИ на `raw.githubusercontent.com`, у которого кэш пять минут (`cache-control: max-age=300`). Обновление сразу после пуша молча получало старые файлы: на диске прежний код, а в шапке прежняя версия. Поймано вживую при выкладке 2.10.3 — первый прогон оставил 2.10.2. **Важно:** ни `Cache-Control: no-cache`, ни `?nocache=<штамп>` этот кэш НЕ пробивают — проверено замером (прогрели кэш, запушили правку, через 17 с все три варианта отдавали старое, а Contents API и raw по SHA — свежее). Решение: один запрос к `api.github.com/repos/.../commits/<branch>` за SHA головы, дальше `raw.githubusercontent.com/<repo>/<SHA>/...` — такие URL неизменяемы, кэш на них всегда корректен. Бонус — атомарность: все файлы из одного коммита, пуш в середине обновления не смешает версии. Нет SHA (лимит/сеть) — откат на URL ветки.
- **2.10.8–2.10.10 (2026-08-23) — исправление записи про 2.10.3: Cloudflare 8888 ПРОПУСКАЕТ, WARP с middle-proxy РАБОТАЕТ.** Утверждение «Cloudflare не пропускает 8888» пришло из разбора одного сервера, было обобщено на всех и ни разу не проверено; на нём стояли запрет в 2.10.4 и гейт «по карте портов» в 2.10.7. Замеры: `curl --interface gtwarp http://portquiz.net:8888/` отдаёт полный HTTP-ответ (то же на 80/443/8443); скачивание 3 МБ через туннель — 3 000 000 байт за 335 мс без обрывов. **Проверка на живом трафике (203.0.113.20, 23.08.2026 13:07):** WARP включён, владелец смотрит видео — за 2 минуты через туннель пришло ~115 МБ, ВСЕ соединения к Telegram имеют адрес источника `172.16.0.2` (интерфейс gtwarp), `All ME servers failed` = 0, пул поднял все 12 групп ДЦ, клиентов на :443 — 21. Асимметрия подтверждает замысел: в туннель уходит 0,2–0,7 МБ за тик, клиентам отдаётся 20–29 МБ напрямую с реального IP. **Вторая опровергнутая посылка:** признак «TCP-соединение установилось» через WARP не значит ничего — Cloudflare принимает рукопожатие у себя на краю, и закрытый порт `:9999`, и несуществующие адреса `198.51.100.77`/`203.0.113.9` «соединяются» за 0,007 с. На этом признаке была построена `_warp_reach_via_warp`, поэтому она ВСЕГДА возвращала «достижимо», и сторожевое спасение не срабатывало ни разу с 2.10.5. Заменено на признак ОТВЕТА: живой узел на мусорный кадр закрывает соединение за доли секунды, чёрная дыра молчит до таймаута (`_warp_endpoint_responds`). **Что осталось непонятным:** причина исходной аварии 2.10.3 (13 часов простоя, 4838 отказов) неизвестна — прежнее объяснение ложно, нового доказанного нет. **Побочно:** при WARP наблюдается повышенный фон `ME init incomplete: no live writers` (~9/мин против ~2/час без WARP) — на работу не влияет, 115 МБ видео проходят, но стоит держать в поле зрения. **Урок:** дважды логика строилась на признаке, который не проверяли контролем на заведомо мёртвой цели. Один `curl` к адресу из TEST-NET ловит такое сразу.

- **2.10.3 (2026-08-22) — WARP-egress убивал прокси при `use_middle_proxy = true`.** Найдено на боевом сервере: 13 часов простоя, 4838 отказов клиентам за час, 26 пользователей. Причина тогда была определена как «Cloudflare WARP не пропускает порт 8888» — **это объяснение ОПРОВЕРГНУТО замером 23.08.2026, см. запись про 2.10.8–2.10.10 выше.** Симптом описан верно (ни одного живого ME-writer'а, клиенты получают `Telegram handshake timeout`), механизм — нет. Снаружи выглядит как «после обновления ключи перестали работать», хотя секреты побайтово те же. **Почему не сработала защита:** pre-flight и health-check щупали `:443`, который через WARP жив, поэтому счётчик провалов не накапливался и fail-open (`n >= 10`) не наступал никогда; юнит падал с exit 1, а девять `ip rule` оставались активными, и оба юнита были `enabled` — после ребута сервер ломал себя заново. **Сделано:** `_warp_middle_proxy_on()` + `_warp_probe_port()` (8888/443); решение в `_warp_reach_via_warp` по `%{time_connect}`, а не по коду возврата curl (на не-HTTP порту успешный connect тоже даёт rc=28); гейт в `_warp_enable_core` и `_warp_apply_key_locked` (rc=3, `code=middle_proxy`); проверка middle-proxy в `ExecStart`, откат при неудаче и `ExecStopPost` у `gotelegram-warp-egress`; автоспасение тиком вотчдога и `gt_migrate_rescue_warp_middleproxy` в миграции; проверка №10 в `--selftest`. Проверено на боевом включая перезагрузку: сервер вылечивает себя за ~30 с без человека. **Заодно:** сэмплер потерь брал два адреса, которых вообще нет в карте middle-proxy Telegram, и мерил их на :443 — одна деградировавшая подсеть DC5 (Сингапур, 2 эндпоинта на группу) давала «25% потерь» при здоровом прокси, и панель на этом основании советовала включить WARP, то есть указывала на ту самую мину. Теперь эндпоинты берутся из `/var/lib/telemt/cache/proxy-config-v4.txt`, меряются только группы, к которым прокси реально подключён, и счёт идёт по группам (100 очков на группу), а не по попыткам. **Ещё:** `mekofix_status_json` отдавал невалидный JSON (`systemctl is-enabled` на отсутствующем юните печатает `not-found` И падает — срабатывали обе половины `||`); QR не показывался никогда, потому что `qrencode` помечен `opt`, а `deps_install_missing` не вызывается из установщика ни разу — теперь `show_qr` ставит пакет сам и объясняет, если не вышло; ключ/ссылка/QR переехали под список ключей; при `user_max_unique_ips = 1` — предупреждение (замер на боевом: медиана 20 с без связи, максимум 374 с при смене Wi-Fi ↔ LTE).
- **dev/main (2026-06-11) — warmup-таймер в подборе.** После переключения пресета `diagnose_rated`/`diagnose_manual` показывают обратный отсчёт `DIAG_WARMUP_SECS=15`с с аннотацией («telemt прогревается — поднимает пул соединений к Telegram, проверять можно ТОЛЬКО после отсчёта»). Причина: после рестарта telemt есть ~10-15с прогрева ME-пула; пользователь проверял в это окно и думал, что режим не применился/не работает (подтверждено на 203.0.113.20 — telemt релеил, но клиент тестил в warmup). `_diag_warmup_wait`.
- **2.7.0 (dev, 2026-06-10) — версия + ченджлог при обновлении.** `GOTELEGRAM_VERSION` 2.6.3→**2.7.0**: существующие 2.6.3-установки теперь ре-мигрируют (версия не совпадает с маркером) → telemt-конфиг перегенерится исправленным `generate_telemt_toml` → битые конфиги (`bucket=0` от старого turbo-пресета) **самоизлечиваются**. При обновлении `auto_migrate` выставляет `MIGRATED_FROM`, а `main()` после баннера показывает `show_upgrade_changelog` («что нового»: дисклеймер, ленивый, меню 1/2/3, подбор с оценкой 1-5, фиксы) + Enter. Показывается один раз (привязано к факту миграции). `write_normalized_gotelegram_config` теперь переносит и `preset_ratings` (оценки переживают ре-миграцию). i18n `upd_*` RU/EN. 2.5.0→2.7.0 мигрирует полностью (ключи/режим/домен сохраняются), 2.6.3→2.7.0 теперь тоже (раньше пропускалось по совпадению версии).
- **dev (2026-06-10) — подбор переделан на пользовательскую оценку 1-5.** Хрупкий авто-замер трафика (ломал рабочий прокси, применяя невалидные/нерабочие пресеты) заменён на `diagnose_rated`: применяет каждый режим → юзер подключает Telegram и ставит 1-5 (1=не работает, 3=плохо, 5=отлично). Оценка 5 → «остаться [s] или продолжить». После всех → таблица оценок → выбор финала (Enter=лучший по оценке) с показом его оценки. Оценки персистятся в `config.json` (`preset_ratings`), тест перепроходим, прошлые оценки показываются, на `q` — возврат прежнего режима. **Лестница сужена до 6 пресетов, проверенных вживую через VPN** (все cubic, floor 1024 кроме эталона; bbr убран — был худшим и ломался под ТСПУ). Меню «Подбор»: 1) Тест с оценкой · 2) Ручной · 3) Таблица · 4) Мои оценки. `post_install_autotune` теперь предлагает тест с оценкой. Урок: автоподбор параметров маскировки для хрупкого MTProto-конфига не работает — оценку даёт пользователь.
- **dev (2026-06-10) — фикс критического бага подбора (telemt падал на turbo-пресете).** Пресеты «без шейпинга» (#1 turbo/#2 fast: bucket=0, timing=0) генерировали невалидный конфиг — telemt: `censorship.mask_shape_bucket_floor_bytes must be > 0` → не стартовал, подбор падал мгновенно («вышел в меню без процесса») и ОСТАВЛЯЛ telemt лежащим. Фиксы: (A) `generate_telemt_toml` пишет `mask_shape_hardening=false` / `mask_timing_normalization_enabled=false` при нулевых bucket/timing, значения держит валидными (512/4096). (B) `diagnose_auto` при прерывании/неудаче ВОССТАНАВЛИВАЕТ прежний рабочий конфиг (`apply_preset orig|дефолт`) — прокси не остаётся лежать. (C) `measure_window` — ранний выход, если трафик не пошёл (не ждёт полное окно). (D) `/dev/tty`-guard через реальную попытку открыть (убран варнинг «No such device»). Проверено на .136: telemt стартует на всех пресетах, подбор без клиента корректно выходит и возвращает дефолт, telemt остаётся UP.
- **dev (2026-06-10) — авто-подбор после установки во ВСЕХ режимах.** `post_install_autotune` в конце Lite/Pro/Lazy → `diagnose_auto` (подбор пресета + congestion control ядра под сеть пользователя). Можно пропустить (`s`) и запустить позже из меню. `diagnose_auto` получил защиту: если на первом пресете нет сигнала (0 байт / 0 таймаутов / трафик не пошёл) — клиент не подключён → ранний выход (rc=2) с подсказкой подключить Telegram и запустить подбор из меню (не молотит всю лестницу впустую). Если трафик есть, но все пресеты режутся → rc=1 с советом сменить домен. i18n autotune_* (RU/EN).
- **dev (2026-06-10) — UX-унификация с goVLESS.** (1) Дисклеймер-gate `show_disclaimer --gate` на первой установке (кэш `$GOTELEGRAM_DIR/.disclaimer-accepted`; в «О программе» — инфо без gate). (2) Первый запуск (прокси не установлен) → сразу выбор режима установки, без дашборда. (3) Ленивый режим (`GOTELEGRAM_LAZY=1`): вводишь только домен → авто (случайный шаблон `pick_random_template_id`, 5 ключей `main`+`key2..key5`, Let's Encrypt без email, авто-подтверждение). (4) Меню установки: `1) Ленивый (рекоменд.)` → `2) Pro` → `3) Lite`, Enter=1=ленивый. (5) Полный фикс stdin→`/dev/tty` в `main()` и `bootstrap.sh` (надёжно закрывает класс #1/#3, а не только confirm). (6) В конце не-ленивой Pro-установки — предложение запустить подбор режима. i18n RU/EN (дисклеймер 26 ключей + lazy). Файлы: lib/common.sh, lib/templates_catalog.sh, lib/lang/{ru,en}.sh, install.sh, bootstrap.sh.
- **dev (2026-06-10) — багфиксы по фидбэку.** (1) `confirm()` (`lib/common.sh`) читает ответ из `/dev/tty`; при отсутствии терминала/ввода -> «No». Закрывает «`n` всё равно ставит» и «смена режима (7→2) без согласия»: корень — при bootstrap (`exec bash`)/пайпе `read` получал EOF→пусто, а `[Y/n]` трактовал пустоту как Yes. Проверено pty: `n`→abort, `y`/Enter→proceed; не-tty→abort. (2) admin-web (`server.py`) берёт версию из `config.json`, а не из `VERSION="2.5.0"` (панель :1984 показывала 2.5.0 при 2.6.3); константа → 2.6.3.
- **dev (2026-06-10, не в main) — «Режим подбора» (anti-DPI preset diagnostics).** Новый `lib/diagnose.sh`: лестница из 16 пресетов «скорость↔надёжность» (главный рычаг `client_mss` строкой: off/decimal/`tspu`/`2in8`/`extreme-low`; плюс cc cubic/bbr/reno, timing, бакеты, `server_hello_delay_min_ms`, опц. `tls_emulation`). Авто-режим: крупный шаг сверху до первого рабочего + уточнение вверх, смарт-старт от сохранённого пресета; измерение через telemt API `:9091` `total_octets` (дельта за окно) + греп `journalctl` на `Telegram handshake timeout`, порог 10 МБ/60с. Ручной режим — выбор по номеру с таблицей. `generate_telemt_toml` стал preset-aware (читает `preset_id` из `config.json`; без него — прежний эталон .42). `apply_network_sysctl` берёт cc из пресета (default cubic). `preset_id` переносится в `write_normalized_gotelegram_config` (переживает миграцию). Подключено: пункт меню «6) Подбор режима» + `lib/diagnose.sh` в `bootstrap.sh`. Статус: код в ветке `dev`, синтаксис/функц.тесты пройдены, на VPS ещё НЕ обкатано. TODO: бот-обёртка, замер точного MSS для `2in8`/`extreme-low`, i18n пункта меню.
- **2.6.3 (2026-06-10)** — ПРАВИЛЬНЫЙ фикс (заменяет ошибочный 2.6.2). Корень handshake-timeout под ТСПУ оказался НЕ в значениях маскировки, а в congestion control: `bbr` + `client_mss="tspu"` (MSS=92) рвёт MTProto-handshake реальных клиентов на части провайдеров (fake-TLS probe при этом проходит). Эталон 203.0.113.20 работает на `cubic`, тест 203.0.113.30 дефолтил в `bbr`. Новая `apply_network_sysctl()` форсит `cubic`+`fq_codel`+буферы 4MB, глушит `bbr` в `/etc/sysctl.conf` (sed) и кладёт drop-in `/etc/sysctl.d/99-zz-gotelegram.conf`; вызывается из миграции и из install lite/pro. Значения `generate_telemt_toml` ВЕРНУЛИ к 2.6.1 (они были правильные). Версия → 2.6.3, чтобы 2.6.1/2.6.2-установки пере-мигрировали. Миграция сохраняет все `[access.users]`. Проверено E2E на ОБОИХ серверах: чистый 2.5.0 → `bootstrap @ main` → тот же ключ, конфиг 2.6.1 + cubic, 0 timeouts, под ТСПУ без VPN — работает. Полный стек (CLI-меню, web-панель `gotelegram-admin:1984`, stats) проверен.
- **2.6.2 (2026-06-09) — ОШИБОЧНЫЙ, заменён 2.6.3.** Развернул `generate_telemt_toml` под присланный «конфиг от парней» (`tls_emulation=true`, бакеты `1024/131072`, тайминг `2..18ms`, `bbr`/`fq`). На чистой миграции не заработало: значения НЕ соответствуют реально рабочему эталону 203.0.113.20 (там исходные 2.6.1 значения + cubic). Ветка `hotfix/2.6.2` удалена. Урок: сверяться с ЖИВЫМ рабочим сервером, а не со скинутым шаблоном.
- **2.6.1 (2026-06-09)** — hotfix: `generate_telemt_toml` больше не рандомизирует `mask_shape_bucket_floor/cap_bytes` и `mask_timing_normalization_floor/ceiling_ms` — все четыре теперь фиксированные (512/4096/180/320 как у эталона на 203.0.113.20). Random per-installation jitter иногда выкидывал `bucket_floor = 1024` + `timing_normalization 225..350ms`, такая комбинация ломала реальный Telegram-клиент (fake-TLS handshake проходил, но MTProto-поток не шёл; Telegram показывал "mtproto: подключён", но без пинга). Зафиксировано на чистой установке через `bootstrap.sh @ main` 9 июня 2026 на 203.0.113.30.
- **2.6.0 (2026-06-09)** — отлаженный под прямой ТСПУ конфиг маскировки. Главное: переход на upstream telemt 3.4.15 (с поддержкой `client_mss = "tspu"`) + перепиленный `generate_telemt_toml` под реально работающие параметры. Что в новом TOML: `[server]` получил `client_mss = "tspu"` (anti-ТСПУ MSS clamping, MSS=92/segment_multiplier=16) и `max_connections = 5000`. `[general.links]` получил `public_host = $domain` в pro-режиме (ссылки теперь на домен, не на IP). `[censorship]` обзавёлся явным `mask_host = "127.0.0.1"`, блоком `mask_shape_hardening + buckets`, `mask_timing_normalization 180..320ms` и `server_hello_delay_min_ms = 20` (только min, без max). `[censorship.tls_fetch]` только `profiles = ["modern_firefox_like", "compat_tls12"]` — без доп.параметров. `[access] user_max_unique_ips_global_each = 13`. Per-installation jitter сохранился только в безопасных полях: `mask_shape_bucket_floor/cap` (random из 384/512/640/768/1024 × 4) и `mask_timing_normalization_floor/ceiling` (random 150-309ms). Что **умышленно НЕ** добавлено (ломало handshake у реальных Telegram-клиентов в наших A/B-тестах 9 июня): `mask_shape_above_cap_blur = true` (флаг режет MTProto reply-flight в 3.4.15), `server_hello_delay_max_ms`, `tls_new_session_tickets`, `use_middle_proxy = false`. Добавлен `auto_update_telemt_if_possible` в `main()` для авто-обновления ядра при повторном bootstrap. `auto_migrate_legacy_state` уже умеет перегенерировать `/etc/telemt/config.toml` при apparent_version != 2.6.0 — для существующих установок миграция произойдёт прозрачно при первом запуске `bootstrap.sh` под новой версией: telemt обновится с 3.4.11/3.4.12/3.4.13 до 3.4.15 (или latest upstream), config перегенерируется с новыми параметрами, users из старого конфига сохранятся.
- **2.5.0 (2026-04-24)** — крупный maintenance pass в `main`: единая версия `2.5.0` в runtime и документации; удалён дефолтный PAT из `bootstrap.sh` (токен теперь только через `GOTELEGRAM_PAT`); повторный bootstrap/update автоматически обновляет уже установленный `/opt/gotelegram-bot` и перезапускает `gotelegram-bot`, сохраняя `.env`; `generate_telemt_toml` добавляет `[server.api]` на `127.0.0.1:9091` и metrics на `127.0.0.1:9090`, что нужно для управления пользователями и статистики; Telegram-бот получил меню `🔑 Keys` для `[access.users]` (добавить/отключить/включить/удалить/показать ссылку/QR/runtime info/IP-limit); добавлена локальная web-админка goTelegram Pro `gotelegram-admin` на `127.0.0.1:1984` с SSH-tunnel инструкцией в боте без отдельного web-admin токена, вкладочной UI-навигацией, иконками, блоком реальных TCP/UDP-слушателей 443, promo-modal раз в 24 часа, i18n от языка установки, ручным переключателем RU/EN, site check на HTTP 200, structured journal logs, light/dark theme, адаптивом, быстрыми switch-переключателями ключей, настройкой `[access.user_max_unique_ips]`, QR-кодами, traffic history 15m/1h/24h/month с переключением график/строки, per-user traffic history из `telemt total_octets` и stats collector restart endpoint; исправлено чтение traffic CSV в боте (header больше не ломает parsing); бот сам делает `stats_collect` перед показом статистики; `iptables` добавлен в optional deps и stats collector пытается установить его; CLI-смена шаблона теперь обновляет `config.json.template_id`, чтобы бот не показывал первый установленный шаблон; backup/restore версии `1.6` сохраняет bot `.env`, bot lang files, disabled user keys, backup schedule, web-admin server/static, custom templates, templates catalog, stats history, user stats history, shared-443 config и полноценную структуру Let's Encrypt (`live/archive/renewal`) для переезда на новый сервер; добавлен безопасный детект 3x-ui/Xray на 443 и управляемый nginx stream shared-443 dispatcher.
- **2.4.6 (2026-04-10)** — universal `apt_lock_wait` helper: ожидание dpkg/apt lock при unattended-upgrades, исправляет установку nginx/certbot/python на свежих VPS.
- **2.4.3 (2026-04-10)** — iter3-фикс: `bot_action_dispatch` оборачивается во `flock -w 30` на `/var/lock/gotelegram-bot-action.lock`. Обнаружена гонка: параллельные `change-lite-domain` получали `"no secret in config"`, потому что один процесс читал `config.json`, пока другой делал `jq ... > tmp && mv`. `util-linux` (содержит `flock`) добавлен в `critical` deps, `check_deps_present` и маппинги `apt_pkg_for_cmd`/`dnf_pkg_for_cmd`.
- **2.4.2 (2026-04-10)** — реализация non-interactive `bot_action_*` в install.sh (change-template + change-lite-domain с JSON-ответом). bot.py подключает `run_bot_action()` и делает реальную работу вместо stub'ов. Критфиксы: (a) `safe_edit_message` принимает `disable_web_page_preview` (иначе TypeError в success-пути cb_pro_confirm); (b) чтение/запись `config['template_id']` вместо `config['template']` (save_gotelegram_config всегда писал `template_id`, бот смотрел не туда); (c) `bot_update_config_field` использует shell `date -Iseconds` вместо `jq now|todate` (jq 1.5 совместимость для Debian 10); (d) `asyncio.Lock _BOT_ACTION_LOCK` сериализует callback'и в процессе бота; (e) валидация `_TPL_ID_RE`/`_DOMAIN_RE` до subprocess. Полный аудит и автоустановка зависимостей: `ensure_deps` разделяет critical/optional, дедуплицирует пакеты, re-verify после install; `apt_pkg_for_cmd` и `dnf_pkg_for_cmd` мапят команды на пакеты (dig→dnsutils/bind-utils, xxd→xxd/vim-common, flock→util-linux). `check_deps_present` — быстрый чек без `apt-get update`.
- **2.4.1 (2026-04-10)** — баг #23: `start_telemt` делает `restart` если сервис активен (иначе stale in-memory config после переустановки Lite поверх Pro). Полная документация проекта — `DOCS_HUMAN.md` и `DOCS_AI.md` (этот файл).
- **2.4.0** — i18n EN/RU для CLI (328 ключей) и бота (99 ключей JSON с per-user persistence). Возможность загрузить свой шаблон сайта из произвольного git-репозитория (валидация URL, таймауты, лимит размера клона).
- **2.3.x** — каталог шаблонов расширен до 1801, 4 источника, 18 категорий. Поддержка StartBootstrap `dist/` структуры.
- **2.2.1** — критические фиксы `$()` capture (все UI через `>&2`), StartBootstrap dist, symlink SCRIPT_DIR через `readlink -f`, XSS в HTML-превью бота, OS-release injection.
- **2.2** — переход с mtg на telemt v3, новый TOML-формат конфига, stealth-архитектура.

---

## 18. Быстрый справочник: «хочу сделать X»

**Добавить новый пункт меню:**
1. `install.sh`: добавь `menu_new_thing()`, впиши в диспатчер `case` в главном цикле.
2. Добавь i18n ключи `menu_new_thing` в ru.sh и en.sh.
3. Если функция интерактивная + возвращает значение → ВСЁ UI через `>&2`.

**Добавить новый домен в QUICK_DOMAINS:**
- `lib/telemt_config.sh` → массив `QUICK_DOMAINS=(...)`. Убедись что домен не заблокирован ни в одной из популярных стран и действительно отвечает на 443 с валидным сертификатом (telemt при `tls_emulation=true` вынимает реальный cert через tls_fetch).

**Сменить версию:**
- `lib/common.sh:6` → `GOTELEGRAM_VERSION="X.Y.Z"`.
- `DOCS_HUMAN.md` / `DOCS_AI.md` → раздел Changelog + версия в шапке.
- Тэг (опционально): `PATCH /git/refs/tags/vX.Y.Z` на новый commit sha.

**Добавить новый шаблон в каталог:**
- Найди git-репо со статическим HTML (index.html в корне или в `dist/`).
- Придумай id: `<source_prefix>_<name>` (например `h5up_future`, `sb_portfolio_x`).
- Добавь запись в `templates_catalog.json` с `id`, `name`, `source`, `repo`, `category`, `preview`.
- Убедись что `download_template` знает префикс источника (`case "$tpl_id" in h5up_*) ... sb_*) ... esac`).

**Отладить «ключ не работает»:**
1. `systemctl is-active telemt` → живой?
2. `cat /etc/telemt/config.toml` → какой `tls_domain`? Какой `port`? Какой `secret`?
3. `journalctl -u telemt --no-pager -n 50` → есть `unknown_sni_action=Drop`? `port in use`? `failed to bind`?
4. Сравни `tls_domain` в конфиге и hex-домен в конце ссылки клиента (`hex(domain) === суффикс секрета после ee+32hex`).
5. Если telemt жив но дропает — **restart** (не start). Это баг #23.
6. Если порт занят — `ss -ltnp | grep :443` → убей конкурента.
7. Если Pro и не открывается сайт в браузере — `curl -k https://127.0.0.1:8443` (nginx жив?), `dig +short $domain` (DNS правильный?).

---

## 19. Где НЕ копаться

- `install_gotelegram_bot.sh` — legacy, функционал дублирован в install.sh пункте 12. Можно удалить после того как убедимся что никто им не пользуется.
- `lib/stats.sh` — опциональная телеметрия, не критичная для работы.
- `ColorlibHQ` в каталоге — wordpress-темы, отброшены. Не возвращать.
- Старый формат конфига mtg (`[security]`, `[[users]]`, `bind_to`) — telemt v3 его игнорирует. Не пытаться «починить» совместимость.

---

## 20. Контрольные точки и инварианты

Перед любым пушем в `main`/`beta`/`alpha`:
1. `bash -n install.sh lib/*.sh` — синтаксис bash ОК.
2. Все новые `$()`-вызываемые функции пишут UI через `>&2`.
3. Все пути к lib/ идут через `$SCRIPT_DIR/lib/...`, а `SCRIPT_DIR` — через `readlink -f`.
4. `GOTELEGRAM_VERSION` обновлена если изменения меняют поведение.
5. Changelog в `DOCS_HUMAN.md` и `DOCS_AI.md` дополнен.
6. Если бекап-формат изменился — прописать в `restore_backup` обратную совместимость.
7. `generate_telemt_toml` не роняет telemt v3 (проверить `telemt run --check config.toml`).
8. **`client_mss="tspu"` (MSS=92) требует `cubic`, НЕ `bbr`** — иначе MTProto-handshake реальных клиентов рвётся под ТСПУ (fake-TLS probe не ловит). `apply_network_sysctl()` это форсит и sed'ит `/etc/sysctl.conf` (он перебивает drop-in'ы в `/etc/sysctl.d/`).
9. **`preset_id` («режим подбора») — канонический ключ в `config.json`, переносится при миграции.** `write_normalized_gotelegram_config` обязан сохранять `preset_id` (как `template_source`/`stats_enabled`), иначе апгрейд сбросит подобранный режим обратно на дефолт (класс бага #25). `generate_telemt_toml` — единый источник правды: он читает `preset_id` и подставляет параметры пресета; `apply_preset` только пишет `preset_id` + зовёт генератор + cc + restart.

После пуша:
1. Подождать пока нужная ветка обновится (GitHub API мгновенно, raw кеш ~30 сек).
2. На VPS: повторить bootstrap из целевой ветки с `GOTELEGRAM_PAT` и при необходимости `GOTELEGRAM_BRANCH=beta|alpha|main`; скрипт сам скачает файлы, обновит установленный бот/админку и покажет меню.
3. `telemt_status` → running. `journalctl -u telemt` → нет ошибок. Ссылка открывается в Telegram-клиенте.

---

**Если в чём-то сомневаешься — открой `CLAUDE.md` в корне `MT-proxy/`. Там суммированы все ранее пройденные грабли и рабочие паттерны под Windows + Desktop Commander + paramiko.**

## WEB-прокси (Telegram Desktop 7.1+) — как устроено

**Предел профилей ядра.** `[web.limits] max_profiles` у telemt по умолчанию 32, а профиль `[[web.vhosts.profiles]]` создаётся на каждый ключ — с 33-м ключом ядро не стартует («WEB profiles exceed web.limits.max_profiles»). Генератор пишет `max_profiles = 4096` (ядро принимает до 65536, 0 — ошибка) перед `[[web.vhosts]]`; `telemt_web_sync_profiles` (SSH), `_ensure_web_limits` (бот, панель) дописывают блок в конфиг, собранный до 2.10.46, при первой записи ключей; миграция делает то же при обновлении и перезапускает ядро. Есть и `max_vhosts` — у нас один vhost.

Тип прокси `WEB` появился в Telegram Desktop 7.1.0 (22.08.2026): MTProxy-потоки идут
внутри обычного HTTPS с доменом сервера через скрытый WebView. Серверная часть — в
ядре telemt с 3.5.5 (`docs/WEB/WEB_PROXY.ru.md` в репозитории telemt); ничего
внешнего не ставим.

Цепочка на сервере (Pro): клиент → telemt :443 (fake-TLS не сошёлся → mask с PROXY
protocol v2, `mask_proxy_protocol = 2`) → nginx 127.0.0.1:8443 (TLS-терминация,
`proxy_protocol` + realip, весь vhost `proxy_pass` в telemt) → telemt WEB-listener
127.0.0.1:18080 (`[[server.listeners]] transport = "web"`) → для обычных запросов
decoy `http_upstream` → nginx 127.0.0.1:8444 (прежний сайт-маскировка и мини-апп).

Ловушка ядра: `listen_addr_ipv4` превращается в listener только при ПУСТОМ
`[[server.listeners]]`. С 2.10.50 `generate_telemt_toml` пишет главный listener
`0.0.0.0:443` явно ВСЕГДА (при WEB к нему добавляется web), а `listen_addr_ipv4`
не пишет вовсе — иначе с web-listener'ом прокси перестал бы слушать 443.

**Только IPv4.** При пустом `[[server.listeners]]` ядро добавляет к IPv4 ещё и
`[::]` (`listen_addr_ipv6` по умолчанию `"::"`): 3.4.x его слушает, 3.5.x
пропускает лишь из-за `[network] ipv6 = false` по умолчанию (`maestro/listeners/plan.rs`).
Прокси работает строго по IPv4: явный listener `0.0.0.0` + `[network] ipv4 = true,
ipv6 = false, prefer = 4`. AAAA-запись у домена уводит IPv6-клиентов в пустоту —
`domain_aaaa_records` (lib/common.sh) показывает предупреждение в шапке меню и при
установке Pro. `shared443_rewrite_telemt_bind` переносит явный listener (не web) на
loopback, а не только строку `listen_addr_ipv4`.

Один ключ — две ссылки: WEB-рукопожатие привязано к пользователю и режиму секрета
профиля (`MtprotoModePolicy::Web`), от `[general.modes]` не зависит. На каждый ключ
из `[access.users]` пишется `[[web.vhosts.profiles]]` с `secret_mode = "dd"`, ссылка
`tg://webproxy?server=ДОМЕН&secret=dd<секрет>`. Профили пересобираются в КАЖДОМ
писателе ключей — `replace_telemt_users_block` (bash), `write_telemt_users` (панель),
`save_telemt_users` (бот): профиль на удалённого пользователя делает конфиг
невалидным перед перезапуском telemt.

Где что: `lib/telemt_config.sh` (helpers `telemt_web_*`, `generate_web_link`,
`show_web_link`), `lib/website.sh` (шаблон nginx с WEB-блоком и decoy),
`install.sh` (`web_enable/web_disable/web_set_carrier/web_status_json`, действия
`web-status|web-on|web-off|web-carrier --carrier=`, меню «Управление → 10»),
панель `/api/web` GET/POST + карточка на «Настройках», бот `menu_web`. Самопроверка
bridge (`web_bridge_check`) считает capability как клиент
(HMAC-SHA256, ключ = байты `dd`+секрет, контекст `tdesktop-web-proxy-bridge-v1\n`+host)
и кэшируется на 5 минут — у ядра есть лимит неиспользованных bootstrap на адрес.

WEB-трафик идёт по mask-пробросу ядра, у которого свои лимиты для чужих
соединений (5 с простоя, 60 с жизни, 5 МиБ). Long poll на 25 с рвался на пятой
секунде — доказано реальной сессией по протоколу. При WEB генератор пишет
`mask_relay_idle_timeout_ms = 70000`, `mask_relay_timeout_ms = 86400000`,
`mask_relay_max_bytes = 0` (с 2.10.50: 0 = без лимита в 3.5.5 и 3.5.14; лимит
считается на направление, и по исчерпании ядро соединение НЕ закрывает, а
замолкает — `masking/copy.rs`; прежние 64 МиБ давали «WEB висит минуту»).

**С 2.10.50 WEB живёт на отдельном имени (`config.json` `web_host`).** Причина —
пер-IP троттлинг рукопожатий ядра (`proxy/handshake/auth_probe.rs`, пороги зашиты
константами в 3.5.5–3.5.14): каждое соединение, не прошедшее FakeTLS на главном
listener'е, пишется сбоем адреса (`auth_probe_record_failure_in`, в т.ч. чужой SNI);
с 4-го сбоя подряд окно 25 мс·2^(n−4) до 1 с, в окне даже верное ee уходит в
BadClient/mask (`tls_handshake.rs` в начале `handle_tls_handshake_impl`); сброс —
только успехом с того же IP. WEB-клиенты (каждое TCP — сбой) за одним NAT душили
ee-клиентов. Топология с именем:

```text
клиент :443 → nginx stream (ssl_preread, /etc/nginx/stream-conf.d/gotelegram-web-front.conf,
             proxy_protocol on)
   SNI = web_host → nginx 127.0.0.1:8445 (TLS, server_name web_host) → telemt WEB 18080
                    → decoy 8444 (сайт)
   иначе          → telemt 127.0.0.1:7443 (proxy_protocol = true,
                    [server] proxy_protocol_trusted_cidrs = ["127.0.0.1/32"])
                    → FakeTLS (ee) или mask → nginx 127.0.0.1:8443 (сайт основного домена)
```

Факт режима — конфиг ядра: `web_front_in_core` = есть `transport = "web"` И
`proxy_protocol = true`; по нему решают nginx (`generate_nginx_config` пишет/убирает
stream-файл), статистика сайта, ожидание старта ядра (`wait_telemt_ready` берёт порт
главного listener'а, не `[server] port`). Порядок «кто держит 443» соблюдает
`web_front_sync_before_restart` (website.sh), его зовут `start_telemt`/`restart_telemt`
— через них идут все смены режима. В `_web_apply_locked` перезагрузка nginx при
переходе на разводку пропускается: nginx не займёт 443, пока его держит ядро, а
запасной `restart nginx` уронил бы сайт. За nginx у ядра выключены `client_mss` и
встроенный synlimit (на loopback они вредны; все пресеты и так `mss off`,
`synlimit off`); внешний MEKO V3/V4 работает на порту 443 как прежде. nginx:
`listen … so_keepalive=15s:15s:4` в stream-сервере — обязательно: ядро ставит клиентским сокетам
keepalive 15 с (`timeouts.client_keepalive`, `transport/socket.rs` `configure_client_socket`) и
TCP_USER_TIMEOUT 90 с; без keepalive у nginx простаивающие соединения телефонов умирали в NAT
(найдено 12.10.2026: «работало 5 минут, потом не отправить, выкл/вкл помогает»).
`worker_connections 16384`, `worker_rlimit_nofile 65535` (`web_front_tune_nginx_limits`,
бекап `nginx.conf.gotelegram.orig`) — каждое соединение прокси занимает два слота.

Имя задаёт `web_set_host` / `--action=web-host --host=`: DNS A → этот сервер
(публичный IP или адрес интерфейса), сертификат `web_cert_expand`: снимок nginx →
nginx с новым именем (`nginx -t`, reload) → самопроверка webroot → `certbot certonly
--webroot --cert-name <домен> -d <домен> -d <имя> --expand` (порт 80 слушает оба
имени); любой отказ — откат nginx и причина отдельной фразой (`WEB_CERT_ERR`, см.
раздел «Сертификат и ACME»), затем `_web_apply_locked`. Без имени `web_blocker` → `need_web_host`; установки,
где WEB включили раньше, работают по-старому (`web-status` → `legacy: true`) и
показывают предупреждение. capability WEB-ссылки — HMAC от имени хоста, поэтому
ссылки (bash/бот/панель) берут имя из `[[web.vhosts]] host` конфига ядра.
Совместно с shared-443 нельзя (`need_no_shared443`): оба держат stream-сервер на 443.

Имя WEB — только поддомен домена (`web_host_is_sub`: `*.<домен>`, для домена
второго уровня это третий уровень, `www.<домен>`); иначе отказ, в JSON
`code=web_host_not_sub`. После обновления `main()` (сразу после
`show_upgrade_changelog`, только с tty и только при `web_legacy_active`) зовёт
`web_upgrade_offer_host`: объясняет раздел ключей по именам, `confirm` «Продолжаем?»,
имя по умолчанию `www.<домен>`, показывает «на N из M адресов» и зовёт `web_set_host`
(вне замка миграции — `web_set_host` берёт `gt_with_config_lock` сам); DNS не готов —
говорит, какие A-записи завести, WEB остаётся как был.

**DNS по числу адресов сервера.** `edgeips_dns_missing <имя> [публичный IP]`
(lib/edgeips.sh): печатает адреса сервера (`edgeips_server_ips` = `edgeips_list`,
пусто — переданный IP или `get_server_ip`), которых нет в A-записях имени; коды
0 — проверено, 2 — A-записей нет (печатает все адреса), 3 — нет dig, 4 — DNS не
ответил. `dig +short +time=2 +tries=1`, без кэша, поэтому зовётся только там, где
смотрит человек: шапка SSH (домен и имя WEB, рядом с AAAA), меню WEB, `web_set_host`
(не блокирующее предупреждение после проверки DNS), `edgeips-status`, `web-status`,
`web-host`. В сборщик не встроено. Текст плашки — `edgeips_dns_warn` (молчит при
кодах 3/4). JSON-контракт:
- `edgeips-status --json`: у каждого `ips[]` `dns_domain` (bool) и `dns_web`
  (bool, `null` без `web_host`); сверху `web_host` и
  `dns:{checked, domain_missing:[ip], web_missing:[ip]}`; при `checked=false`
  (нет домена/dig, DNS молчит) списки пустые, а `dns_*` по адресам — `true`.
- `web-status --json`: `server_ips` (число) и `dns_web_missing` ([ip], пусто без имени).
- `web-host --json` при успехе: `dns_missing` — непокрытые адреса через пробел.

Carrier один, без `carriers` (auto-negotiation): со списком кандидатов ядро
пробовало бы websocket первым, а выбранный владельцем carrier был бы лишь
последним запасным — переключатель в меню ничего бы не значил. Ссылки везде
(bash `web_link_for_secret`, панель `web_link`, бот `get_web_link_for_secret`)
даются только при `transport = "web"` в конфиге ядра — флаг мог остаться от
ядра без WEB. После смены ядра `web_reconcile_after_core_change`
(из `auto_update_telemt_if_possible`) либо дособорает WEB, либо выключает его
с предупреждением; в статусе есть `configured` (флаг) отдельно от `enabled`
(факт), и при «настроен, но не активен» бот и панель показывают «Выключить».

Anti-probing: decoy-блок nginx тоже с `access_log off` — ядро отправляет в decoy
и запросы с живой capability (неканоничная форма, исчерпан лимит bootstrap), и
capability попадала бы в лог. `proxy_set_header Host` статический: с чужим Host
(IP-литерал без SNI) ядро отвечало своим `not found`, а не сайтом.

Ограничения: только Pro с сертификатом; ядро ≥ 3.5.5 (`pinned` 3.4.18 не подходит);
`public_addr` vhost — главный IP по маршруту по умолчанию, одна vhost на все адреса
домена; прямые обращения к 127.0.0.1:8443 без PROXY-заголовка после включения не
работают (все проверки идут через 443). Обход меню бота НЕ нажимает
`web_on/web_off/web_carrier` (см. FORBIDDEN в tests/live_walk_menu.py).

## DNS сервера: `dns_resolver_tune` (2.10.54)

**Зачем.** 29.10.2026 на сервере владельца `/etc/resolv.conf` хостера (`# Generated by SolusVM`,
`nameserver 8.8.8.8`, `nameserver 1.1.1.1`): 8.8.8.8 терял ~2 запроса из 5, glibc ждёт ответа
`timeout` (по умолчанию 5 с) и только потом идёт к следующему серверу — загрузки, certbot и всё по
именам тормозило и падало. Ручная правка (1.1.1.1 первым, `options timeout:1 attempts:2`) дала
20/20. Функция делает то же сама; SolusVM/cloud-init могут переписать файл — обновление применит
настройку снова.

**Где и когда.** `install.sh`, рядом с `apply_network_sysctl`. Вызовы: `install_lite_mode` и
`install_pro_mode` сразу после `ensure_deps` (dig уже стоит; ядро, шаблоны и certbot качаются с
новой настройкой) и `auto_migrate_legacy_state` рядом с `apply_network_sysctl` (раз на версию —
маркер `.migrated_<версия>`). Путь — `DNS_RESOLV_CONF` (по умолчанию `/etc/resolv.conf`, для
тестов). Выключатель — файл `${DNS_RESOLV_CONF}.gotelegram.off` (на сервере
`/etc/resolv.conf.gotelegram.off`; любой тип, проверка `-e`/`-L`). Он рядом с самим файлом, а не в
`config.json`: переживает обновление, переустановку и удаление (`config.json` удаление стирает), и
config.json с секретами для выключения трогать не нужно. Статическая проверка в `--selftest`
(раздел 12): вызов стоит сразу после `ensure_deps` в Lite и Pro и есть в
`_auto_migrate_legacy_state_locked`.

**Не трогаем** (выход 0, причина — в журнал): ссылка или не обычный файл; комментарий менеджера
(`_dns_manager_of`: NetworkManager, resolvconf, systemd-resolved, dhcpcd, connman, netconfig) — он
перепишет файл сам; любой nameserver на loopback, `0.0.0.0` или `::` (`_dns_ns_is_local`) —
это локальный кэш: `timeout:1` к нему вреден, а порядок за ним ведёт он сам; IPv4 в нестандартной
записи (`127.1`, `0x7f.1`, `0177.0.0.1` — glibc читает IPv4 через `inet_aton` и понимает их, мы
нет: за такой записью может прятаться loopback); нет ни одного nameserver с годным адресом;
есть файл-выключатель.

**Разбор — как у glibc `res_init`.** Ключевое слово только с начала строки и за ним пробел или таб;
строки с отступом, комментарии, `search`/`domain`/`sortlist` и прочее — дословно на своих местах.
Адрес nameserver — до первого пробела или таба (glibc 2.26+: `strpbrk(" \t\n")`, затем
`inet_aton_exact`/`inet_pton`): `nameserver 1.1.1.1#main` и `nameserver 8.8.8.8;x` для glibc
негодны — у нас тоже: не замеряются, не считаются серверами, стоят дословно на месте. Строки
nameserver переносятся дословно (с хвостами после пробела), меняется только их взаимный порядок в
прежних гнёздах. Невалидный адрес
(`_dns_ns_valid`: каноничный IPv4 или IPv6, у IPv6 можно `%iface`) в dig не уходит и стоит на своём
месте. Сервер не удаляется никогда. glibc читает только первые 3 nameserver (`MAXNS`): сбоящий,
ушедший на 4-е место, фактически выключается, но остаётся в файле.

**options.** Все строки `options` сливаются в одну на месте первой (нет ни одной — в конец). Как в
`res_setoptions` (токены по порядку, позднее перекрывает раннее): у `ключ:значение` действует
последнее значение, флаги (`rotate`, `edns0`, `trust-ad`…) — без дублей, в порядке первого
появления. `timeout:1` — всегда; `attempts` — не меньше 2 (больше — оставляем).

**Замер.** `_dns_ns_losses <ns>` печатает потери из 8: восемь последовательных
`dig +time=1 +tries=1 @ns . NS`, ответ засчитан только при коде 0 и `status: NOERROR`;
`HOME=/nonexistent`, чтобы `~/.digrc` (например `+short`) не спрятал строку статуса (`dig -r` в
BIND 9.10 нет). Разные серверы — параллельно, `wait` по PID (голый `wait` ждал бы и чужие фоновые
задачи); всего до ~9 с (мёртвый сервер — 8 × 1 с). Нет dig (`_dns_have_dig`) — порядок не трогаем,
`options` применяем. Отдельные функции — швы для `--selftest`.

**Порядок.** Сбоящий — от 2 потерь из 8 (одна — шум). Сбоящие уходят ниже здоровых в **прежнем
взаимном порядке** (без сортировки по потерям: шумные 3 и 4 менялись бы местами на каждом
обновлении); здоровые тоже сохраняют порядок. Порядок не трогаем:
- сбоят все — предупреждение (обычно это сеть сервера, а не серверы);
- в файле есть внутренний адрес хостера (`_dns_ns_is_private`: 10/8, 172.16/12, 192.168/16,
  100.64/10 — Alibaba 100.100.2.136, 169.254/16, fc00::/7, fe80::/10; IPv4-mapped в любой записи:
  `::ffff:a.b.c.d`, `0:0:0:0:0:ffff:a.b.c.d`, hex `::ffff:a00:1`) — о сбоящем только предупреждение
  (`dns_tune_slow_private`; если сбоящий и так ниже здоровых — `dns_tune_slow_kept`) и строка в журнал. Причина: glibc идёт к следующему серверу только
  при таймауте, SERVFAIL, NOTIMP или REFUSED; NXDOMAIN — окончательный ответ (`res_send.c`).
  Публичный сервер, поставленный выше приватного, на внутренние имена хостера (`ec2.internal`,
  зеркала, метаданные) отвечает NXDOMAIN — и они пропадают совсем.

**Запасной.** Сервер один — его место не меняется; если он сбоит, **ниже** него добавляется
запасной: первый из `1.1.1.1 8.8.8.8 9.9.9.9`, которого нет в файле и который ответил 8 из 8
(замеряются вместе с основным, параллельно). Перед строкой запасного — метка отдельной строкой
`# goTelegram Pro dns_tune fallback: …` (`DNS_TUNE_FB_MARK`). Не хвостом строки nameserver: парсер
BIND (`lib/irs/resconf.c`, им читают resolv.conf dig/host/nslookup) на «extra junk» после адреса
отказывается читать файл — сломались бы наш замер и dig-проверки продукта. При разборе годный
nameserver сразу за меткой — запасной (`fbns`): не замеряется, не переставляется и при сборке всегда
стоит сразу после последнего исходного сервера (вместе с меткой). Один сервер с запасным не
замеряется вовсе (менять нечего), поэтому повтор при любых замерах файл не меняет (правило 9). Метка
без nameserver за ней (строку убрали руками) — убирается. Остался только запасной (строку хостера
убрали) — он обычный сервер (`kind` → `ns`), метка убирается. Внутренний адрес запасному не мешает:
он ниже, внутренние имена отвечает свой сервер, — **кроме `options rotate`**: glibc (`RES_ROTATE`)
начинает каждый следующий запрос со следующего сервера, запасной стал бы равноправным первым, и
часть запросов к внутренним именам получала бы NXDOMAIN. Поэтому при `rotate` запасной не
добавляется вовсе (кандидаты не замеряются), только предупреждение `dns_tune_no_fallback_rotate`.
Разбор `options` для этого стоит до замера.

**Сообщения.** О перестановке («поставлен ниже») и о запасном — только после успешной записи
(копятся в `pend`). Файл не меняется — «потерял N из 8, уже стоит ниже» и «без изменений». Строки
из файла (`options`, адреса в сообщениях о пропуске) перед `log_*` (это `echo -e`) проходят
`_dns_safe`: без обратной косой черты и управляющих символов; в сам файл идут дословно.

**Запись.** Только если тело файла (без маркера) изменилось; маркер — одна ASCII-строка
`# goTelegram Pro dns_tune: …` в конце, дубли сводятся к одной. Копия
`${conf}.gotelegram.orig` — один раз (`cp -p`, сверка), не перезаписывается; не сохранилась — файл
не меняем. Перед записью: `-w` (immutable, read-only → предупреждение) и файл не изменился за время
замера. tmp — `mktemp` в том же каталоге (заодно проверка места на той же ФС), сверка `cmp`. Замена —
**на месте** (`cat tmp > conf`, `_dns_write_in_place`), не `mv`: сохраняются inode, права, владелец
и контекст SELinux, и работает файл, примонтированный в контейнер (rename там даёт EBUSY). После
замены — сверка; сбой → возвращаем снимок, сделанный до замера, и предупреждаем. chattr не трогаем.
Всё — в подоболочке с `trap EXIT` на временные файлы; функция всегда возвращает 0 и установку не
роняет (даже `set -u`-ошибка остаётся в подоболочке).

**Журнал.** `log_info`/`log_warning` (ключи `dns_tune_*` в `lib/lang`), в `/var/log/gotelegram.log`
строки `dns_tune:` — потери по серверам, порядок до и после, `options` до и после, добавленный
запасной. Удаление продукта файл не трогает (как и sysctl). Откат: `touch
/etc/resolv.conf.gotelegram.off`, затем `diff /etc/resolv.conf.gotelegram.orig /etc/resolv.conf`;
отличаются только порядок, `options` и наши строки — `cat .orig > /etc/resolv.conf`; серверы хостера
с тех пор другие — старый файл не копировать, а убрать наши строки (метки, запасной, `options`) и
вернуть порядок руками. Проверки — `--selftest`, раздел 12
(временные файлы, замер подменён).

## IP сервера: `get_server_ip` (2.10.55)

**Зачем.** 29.10.2026 у владельца первый резолвер в `/etc/resolv.conf` терял ~40% запросов, glibc
ждёт 5 с перед следующим сервером. Шапка SSH-меню (`show_main_menu`, перерисовка раз в 30 с по
`read -t 30`) каждый раз звала `get_server_ip`, а та по очереди спрашивала api.ipify.org,
icanhazip.com и ifconfig.me (`curl --max-time 5`, по имени, без кэша): шапка ждала до 15 с и
показывала «0.0.0.0». Вдобавок `ip=$(get_server_ip 2>/dev/null || echo "N/A")` при отказе давал две
строки — «0.0.0.0» и «N/A»; теперь `ip=$(get_server_ip 2>/dev/null) || ip="N/A"`.

**Порядок источников** (`lib/common.sh`): 1) src маршрута `ip -4 route get 1.1.1.1`
(`_server_route_src4`); публичный — вернуть сразу, без сети и кэша. WARP-egress маршрут к 1.1.1.1
не меняет: в таблицу 7788 уходят только `WARP_TG_SUBNETS`. 2) За NAT (src приватный, нет `ip` или
маршрута) — свежий кэш `SERVER_IP_CACHE` (`/run/gotelegram/server_ip`, tmpfs), моложе
`SERVER_IP_TTL` (3600 с). 3) Внешние сервисы параллельно (`_server_ip_external`): каждый
`curl -s -4 --max-time SERVER_IP_BUDGET` (3 с) — сам фоновый процесс, `-w '\ngt-http:%{http_code}\n'`
— метка конца ответа (файл без метки ещё пишется). Опрос файлов раз в 0,1 с, не дольше бюджета + 1 с;
первый годный ответ (HTTP 200, тело — ровно публичный IPv4) побеждает, остальные curl гасятся и
дожидаются, временный каталог удаляется. Опрос, а не `wait -n`: `wait -n` есть только с bash 4.3, а
на пробе с настоящим curl один прогон из нескольких ждал таймер (3 с вместо 2; причина не
установлена); опрос от таблицы заданий bash не зависит. Тело `_server_ip_external` — подоболочка с
ловушками EXIT/INT/TERM/HUP (`_server_ip_ext_cleanup`): Ctrl+C или обрыв SSH посреди опроса гасят
curl (фоновые задания SIGINT игнорируют) и удаляют каталог, затем сигнал повторяется себе — меню
прерывается, как раньше. Успех пишется в кэш и снимает отметку отказа; отказ пишет отметку (ниже).
4) Устаревший кэш любого возраста — rc 0. 5) «0.0.0.0», rc 1 — контракт для
`telemt_web_public_addr` и `edgeips_server_ips`.

**Пауза после отказа.** После каждого неудачного опроса сервисов `get_server_ip` пишет рядом с
кэшем `<SERVER_IP_CACHE>.fail` — одна строка «epoch», запись атомарная, как у кэша
(`_server_ip_file_put`); успех её удаляет. Пропускает опрос при свежей отметке только тот, кто
попросил: `SERVER_IP_FAIL_BACKOFF=<с> get_server_ip` (`_server_ip_fail_fresh`). Так зовёт одна шапка
меню (`show_main_menu`, 300 с): за NAT без ответа сервисов перерисовка раз в 30 с иначе ждала бы 3 с
каждый раз, а теперь — раз в 5 минут, в остальное время сразу устаревший кэш или «N/A». Ссылки tg://,
установка и сверки DNS зовут без переменной и спрашивают каждый раз: краткий сбой сети не должен
давать им «0.0.0.0». Отметка из будущего, кривая или пауза не числом — паузы нет.

**Кэш.** Одна строка «epoch ip»: время внутри файла (без `stat -c`, переносимо). Читается обычный
файл или ссылка на него (`-f` идёт по ссылке; содержимое всё равно проверяется строго), строка
принимается целиком (IFS — свой, не вызывающего): epoch — цифры без ведущего нуля, адрес —
канонический публичный IPv4 (`_ip_is_public4`: не 0/8, 10/8, 100.64/10, 127/8, 169.254/16,
172.16/12, 192.0.0/24 — src на IPv6-only VPS с CLAT/464XLAT, 192.168/16, 198.18/15 — fake-ip в
TUN-режиме mihomo/sing-box, 224/3), третьего поля нет; иначе кэша нет. Время из будущего — не
свежий. Запись: `mktemp` в том же каталоге (права 600) и `mv -f` поверх (ссылку заменяет, а не
пишет через неё). Сбросить: `rm /run/gotelegram/server_ip /run/gotelegram/server_ip.fail` (или
перезагрузка). Бот (`bot.py`) и панель (`server.py`, `public_ip`) узнают IP сами и этот кэш не
читают. Проверки — `--selftest`, раздел 13 (`ip` и `curl` подменены функциями, кэш и временные
файлы — во временном каталоге).

## Загрузка файлов: `bootstrap.sh` (2.10.56)

**Зачем.** 29.10.2026 `templates_catalog.json` (13,6 МБ) шёл с raw.githubusercontent из приватного
репозитория со скоростью ~80 КБ/с — три минуты без строки на экране; установщик трижды прервали
как «зависший». Через `bootstrap.sh` идут и установка, и обновление — место одно.

**Как качает** (`download_file`, параметры — `CURL_DL_OPTS`): `--compressed` (каталог по сети
~1,4 МБ; ключ ставится, только если в `curl -V` есть libz), `--connect-timeout 15`, обрыв при
скорости ниже 1 КБ/с дольше 30 с (`--speed-limit 1024 --speed-time 30`), `--max-time` 600 с на
файл, для каталога 1800 с (без gzip 13,6 МБ на медленном канале в 600 с не влезают, а повтор
начинается с нуля). Каждый вызов curl начинается с `-q`: ~/.curlrc root с verbose/trace вывел бы
заголовок с токеном.
До трёх попыток с паузой 3 и 6 с — и при ошибке curl, и при плохом коде; 401/403/404 не
повторяются. Успех — код curl 0, HTTP 200 и непустой файл. Перед каталогом — строка, что файл
большой; полоса прогресса curl — только если stdout и stderr — терминал. Строки повтора и ошибок —
в stderr. Запрос к API за SHA ветки — тоже с таймаутами (10/30 с) и после проверок root и токена.

**Атомарность.** Каждый файл качается в `<файл>.part` рядом с рабочим; на место (`mv -f`) все файлы
встают только когда скачались все. Первый сбой загрузки останавливает её; ловушки EXIT/INT/TERM/HUP
удаляют все `.part`, установленные файлы не меняются. Сообщение зависит от того, была ли установка
(`KEPT_MSG`: «установленные файлы не изменены» / «ничего не установлено»); Ctrl+C — одна строка
«Прервано, …» в stderr; при ошибке записи (curl 23, `DL_FAIL_RC`) подсказки про токен и сеть нет.
Перестановка не прерывается сигналами (`trap '' INT TERM HUP`), но если `mv` всё же откажет на
середине, часть файлов уже новая — скрипт так и пишет и просит запустить установку ещё раз.
Перед `exec install.sh` ловушки снимаются.

**Токен.** Не в argv (`ps -eo args` видит любой пользователь): `-H @<(auth_header)` — curl 7.55+
читает заголовок из канала подстановки, printf — встроенная команда bash. Не `-H @-`: при
`curl … | bash` stdin — сам скрипт. Команда установки из README сама передаёт токен внешнему curl
аргументом — это вне `bootstrap.sh`.

Проверки — `tests/test_bootstrap.py` (локальный стенд в процессе теста; окно застоя 2 с вместо
30, остальное — как в продукте).

## Версии TLS сайта: `tls_protocols`

**Зачем.** 29.10.2026 на сервере владельца (WEB на отдельном имени: nginx stream 443 → сайт 8443 и
WEB 8445) домашний провайдер периодически резал весь 443 на ~7 минут, пока nginx предлагал TLSv1.3.
С ручным `ssl_protocols TLSv1.2;` окон не было 5 ч 46 мин; обновление 2.10.54 перегенерировало nginx,
вернуло `TLSv1.2 TLSv1.3` — и через минуту окно.

**Как устроено** (`lib/website.sh`). Ключ `tls_protocols` в config.json: ровно `TLSv1.2` или
`TLSv1.2 TLSv1.3`; нет ключа — `TLSv1.2 TLSv1.3` (умолчание не менялось). Любое другое значение —
умолчание и предупреждение (stderr + `/var/log/gotelegram.log`); пробелы по краям значения
отбрасываются (`"TLSv1.2 "` — это 1.2, не мусор). `nginx_tls_protocols` печатает
только один из двух литералов — сырое значение в nginx не попадает никогда. Все `ssl_protocols` в
`generate_nginx_config` — через `TLS_PROTOCOLS_PLACEHOLDER`, так что значение доходит и до смены
имени WEB, перевыпуска сертификата, мини-аппа, обновления и действий бота/панели (`--action`): все
они перегенерируют nginx этой функцией.

**Ручная фиксация.** `nginx_tls_adopt_manual` зовут `generate_nginx_config` и
`generate_nginx_temp_config` до перезаписи файла: если ключа нет, а в действующем конфиге
(`sites-enabled/gotelegram` — по симлинку или обычный файл после `sed -i`; если его нет —
`sites-available/gotelegram`) все незакомментированные директивы `ssl_protocols` — ровно
`ssl_protocols TLSv1.2;` на своей строке, в config.json пишется `tls_protocols=TLSv1.2` и одна
строка в лог. Детектор (`_nginx_tls_live_all`) считает и директиву после другой на той же строке
(`ssl_session_tickets off; ssl_protocols TLSv1.2 TLSv1.3;` — это смесь, не фиксация); CRLF и табы
допустимы. Смесь, 1.2+1.3, ни одной строки, нет файла — ничего. Не вышло записать config.json (нет
файла, не JSON) — на этот раз всё равно 1.2.

Генератор свои строки не помечает: config.json без ключа (старый бекап, ручная правка) рядом с
помеченным «только 1.2» вернул бы 1.3, а ручная фиксация — главное, что надо сохранить. Цена:
сгенерированный «только 1.2» без ключа читается как ручная фиксация, поэтому **удаление ключа
1.3 не возвращает** — следующая перегенерация снова запишет `TLSv1.2`. Вернуть 1.3 — только
переключателем (ниже): он пишет явное `"TLSv1.2 TLSv1.3"`.

**Переключатель (2.10.58, правки 2.10.59).** SSH: «Прокси» → 10 «TLS сайта и WEB» (текущее значение в подписи),
варианты «1.2 + 1.3» и «только 1.2», 0/EOF — назад. Безголово:
`--action=tls-protocols [--json]` — текущее (`value` `1.2`|`1.2+1.3`, `tls_protocols`,
`applicable`, `blocker` `mode`|`no_site`|пусто; замок не берётся, миграция не запускается);
`--action=tls-protocols --value=1.2|1.2+1.3 [--json]` — применить (`changed` — менялся ли nginx).
Все значения в JSON — строки, как у соседних действий (`bot_emit_json`): `changed` и `applicable`
приходят как `"true"`/`"false"`, не как JSON-логические. Иное значение — `error`,
`code=bad_value`, ничего не меняется. `nginx_tls_set` (под `gt_with_config_lock`): ключ →
`generate_nginx_config <domain> 8443` → все `ssl_protocols` равны выбранному → `nginx -t` →
`reload`; telemt не трогается. **restart не делается никогда**: при WEB на отдельном имени 443
держит nginx stream, restart оборвал бы все соединения. `reload` не прошёл, а nginx жив
(`systemctl is-active`) — откат, rc 7; nginx стоял — `systemctl start`. В nginx уже так — rc 9,
nginx не трогается; ключ дописывается ровно выбранным, если его не было или он другой (ручной
`TLSv1.2` без ключа + выбор «только 1.2» → ключ `TLSv1.2`). Сбой — снимок `sites-available`,
`sites-enabled` (симлинк или файл) и разводки 443 возвращается как был, в config.json — прежние
`tls_protocols` и `updated_at` (остальные поля не трогаются); при rc 7 nginx снова перечитывает
прежний конфиг. Не удался сам откат — rc 8, строка `ROLLBACK FAILED` в журнале с прежними
значениями ключа и путём к оставленному снимку файлов nginx. Коды ошибок `--json`: `not_used`
(Lite), `no_site` (нет домена/SSL-конфига), `config_write`, `nginx_test` (конфиг не собрался или
не прошёл проверку), `nginx_reload`, `rollback_failed`.

**Инвариант.** `write_normalized_gotelegram_config` (белый список ключей при обновлении) обязан
переносить `tls_protocols`, иначе обновление снова вернёт 1.3. Проверки — `--selftest`, разделы 8 и
14 (стражи по тексту `generate_nginx_config`: нет `ssl_protocols` мимо настройки, подстановка
`TLS_PROTOCOLS_PLACEHOLDER` есть, `nginx_tls_adopt_manual` стоит до первого `cat > "$NGINX_SITE_CONF"`;
переключатель с подменёнными генератором, nginx и systemctl). Бот и панель кнопки пока не имеют —
только `--action`.

**Как откатить.** После отката на версию без ключа (до 2.10.57) фиксацию надо снова поставить
вручную: `ssl_protocols TLSv1.2;` в `sites-available/gotelegram`, `nginx -t && systemctl reload nginx`.

## Сертификат и ACME: webroot и причины отказа (2.10.60)

**Зачем.** «Отдельное имя WEB» на части серверов падало общей фразой `web_host_cert`. Причина
чаще всего — `/var/www/certbot`, созданный `mkdir -p` при umask 077/027 (700/750): nginx (www-data)
отвечает 403 на файл проверки, Let's Encrypt пишет `unauthorized`, человек видит «проверьте
A-запись и порт 80». К тому же nginx с новым именем перегенерировался уже после отказа certbot.

**Каталог** (`lib/website.sh`, `ACME_WEBROOT` из `lib/common.sh`). `acme_webroot_prepare`:
`/var/www/certbot`, `.well-known`, `.well-known/acme-challenge` — 755 (нет — `mkdir -m 755`,
другой режим — `chmod 755`). Родителю `/var/www` — только недостающий `o+x`, больше ничего.
Симлинки не трогаются и не проходятся; каталог меняется, только если его родитель принадлежит
root (или нам) и закрыт на запись для группы и остальных (`_acme_trusted`, GNU и BSD `stat`) —
иначе в него могли бы подложить симлинк между проверкой и `chmod`. Не вышло — запись в журнал,
rc 1, дальше работа идёт как раньше. Зовут: `web_cert_expand`, `obtain_ssl_certificate` (до
проверки `live/`, то есть и при уже выданном сертификате), `generate_nginx_temp_config`,
`renew_ssl_certificate` и миграция при обновлении Pro. Чистую установку покрывают
`generate_nginx_temp_config` и `obtain_ssl_certificate`: миграция на ней выходит раньше (версия в
config.json уже текущая). Предупреждение миграции `acme_webroot_left` — только если каталоги не
тронуты и nginx их не проходит (`acme_webroot_web_ok`: родитель и существующие каталоги
проходимы для www-data — `o+x` или владелец www-data с `u+x`; симлинк — «не проходит»).

**Отступление: кнопка «Продлить SSL» в боте** (`cb_ssl_renew`, `gotelegram-bot/bot.py`) зовёт
`certbot renew` сама, без `acme_webroot_prepare` (отдельного `--action` для продления нет). Каталог
к этому моменту уже исправлен миграцией при обновлении на 2.10.60; если права снова испортили
вручную, продление с кнопки упадёт (Let's Encrypt получит 403) — `--selftest` об этом предупреждает.

**`web_cert_expand`** (install.sh). Имя уже в сертификате — rc 0 без действий. Иначе: подготовка
каталога → снимок файлов nginx (`_nginx_tls_snap`: сайт, симлинк `sites-enabled`, разводка 443) →
`generate_nginx_config` с `WEB_HOST_PENDING` и `nginx -t` (отказ — `web_cert_nginx`; снимок не
сохранился — `web_cert_snap` с каталогом `TMPDIR`) → reload
(отказ — `web_cert_reload`) → `acme_local_probe` → `_web_certbot_expand`. Любой отказ: снимок
возвращается (если перечитывали — ещё `nginx -t && reload`), certbot после отказа nginx или
самопроверки не зовётся, rc 1, текст — в `WEB_CERT_ERR`; `web_set_host` печатает его вместо
общей фразы (её оставили для случая без текста). Не удался сам откат — `ROLLBACK FAILED` в журнале,
снимок остаётся, к фразе добавляется `web_cert_rollback` с путём.

**Временные файлы при прерывании.** Снимок (`gt-webcert.*`), вывод certbot (`gt-certbot.*`) и файл
самопроверки живут под ловушкой INT/TERM/HUP (`_gt_tmp_trap` / `_gt_tmp_untrap`,
`lib/website.sh`): уборка (для снимка — сначала вернуть файлы nginx; не вернулись — снимок
остаётся), затем прежние ловушки вызывающего (`trap -p` до шага) возвращаются и сигнал
повторяется себе — его обрабатывают они или умолчание. nginx в ловушке не перечитывается.
`_nginx_tls_snap restore` сначала удаляет файл, поэтому ловушка снимается до удаления снимка, а без
каталога снимка ничего не делает. SIGKILL
(таймаут subprocess бота или панели) ловушки не ловят.

**Самопроверка** `acme_local_probe <имя>`: случайный файл `gtprobe-…` (тело — его имя, 644) в
`acme-challenge`, `curl --noproxy '*' --resolve <имя>:80:127.0.0.1` (таймаут
`ACME_PROBE_TIMEOUT`, 5 с), файл удаляется сразу. rc: 0 — 200 и своё тело; 3 — 403
(`web_cert_probe_403`, права); 4 — 404, другой код или чужое тело (`web_cert_probe_other`, имя
обслуживает не наш блок или 80 занят); 5 — нет ответа (`web_cert_probe_none`); 2 — проверить нельзя
(нет curl, имя не проходит `validate_domain`, цепочка каталогов не доверена) — certbot идёт как
раньше. На 4 и 5 `web_cert_expand` повторяет пробу один раз через `ACME_PROBE_RETRY_DELAY` (2 с):
reload не ждёт старых воркеров, первый ответ мог дать прежний конфиг.

**certbot.** Вывод — во временный файл (`mktemp`, 600, удаляется), `timeout 100`. Второй заход
с `--register-unsafely-without-email` — только если причина не распознана (нет аккаунта), чтобы не
жечь лимит неудачных проверок. `certbot_fail_kind`: `ratelimit` > `dns` > `port80` (Type:
connection, таймаут, отказ соединения) > `perm` (Type: unauthorized, Invalid response) > `other`.
Фразы: `web_cert_port80`, `web_cert_perm`, `web_cert_dns`, `web_cert_ratelimit` + срок (`retry
after <дата>` из вывода → `web_cert_wait_until`; failed authorizations → `web_cert_wait_hour`;
иначе `web_cert_wait_week`). `other` — `web_host_cert` и `certbot: <одна строка>`
(`certbot_short_err`: первая `Detail:`, иначе последняя строка с ошибкой, иначе последняя непустая —
без служебного шума certbot (Ask for help, logfile, Saving debug log); вместо заголовка «An
unexpected error occurred:» — следующая непустая строка; без управляющих символов и `\`, до 200
символов; rc 124 — `timeout 100 s`).

**Бот и панель.** `--action=web-host` берёт последнюю строку вывода `web_set_host` и отдаёт её в
`message` (`bot_emit_json`); бот экранирует `html.escape`, панель показывает уведомление через
`textContent`, а в журнал событий пишет `innerHTML` с `escapeHtml` (`admin-web/static/app.js`). Очистка строки
снимает ANSI и значок `log_*` явно: прежний класс `[^A-Za-zА-Яа-яЁё]` при локали C (у служб бота и
панели `LANG` может не быть) резал «✗» по байтам и оставлял мусор перед текстом.

**selftest.** Предупреждение (`!`, не провал), если `/var/www/certbot` или его существующие
подкаталоги непроходимы для www-data (нет `o+x`, и владелец не www-data с `u+x`).

Проверки — `--selftest`, раздел 15 (подготовка при 700/750/777 и umask 077, симлинк и чужой
владелец не трогаются; самопроверка с подменённым curl на 200/403/404/чужое тело/301/000;
разбор вывода certbot и тексты, шум и «An unexpected error occurred:»; `web_cert_expand` с
подменами nginx, certbot и systemctl — отказ `nginx -t`, reload, самопроверки, повтор пробы после
404, снимок не сохранить, unauthorized без второго захода, откат не удался, SIGTERM посреди certbot
(временных нет, ловушки вызывающего целы); миграция — www-data 755 молчит, root 700 и симлинк
предупреждают; `--action=web-host --json` при `LC_ALL=C` передаёт причину).

**Как откатить.** `git revert` коммита 2.10.60. Права 755 на `/var/www/certbot` и `o+x` на
`/var/www` останутся — это обычные права webroot, их возвращать не нужно.
