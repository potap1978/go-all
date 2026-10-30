# goAmnezia pro — контракт модулей (обязателен для всех, кто пишет код)

Версия контракта: 1 (2026-09-08). Изменения контракта — только через этот файл, сначала
правка здесь, потом код. Общий план — [PLAN.md](../PLAN.md), спринты — [SPRINTS.md](../SPRINTS.md).

Дополнения при сборке ядра (2026-09-08, без смены версии): §1 — переменные стендов
(`GOAMNEZIA_SERVER_IP`, `UPSTREAM_DIR`, …); §3.1 — кто пишет `server_ip`; §4.1 — `@json:false`
в `ga_emit_json`; §4.4 — `amnezia_profile_param_list`, точные форматы ответов и коды
`amnezia_profile_set_param/set_subnet/set_mtu`, выбор свободной подсети при установке;
§4.5 — `users_alloc_ip` для /20–/24, вывод `users_renumber_profile`, `ga_set_endpoint_host` в
`users.sh`; §4.7 — правила диспетчера (пустой ≠ отсутствующий аргумент, коды модулей в ответе,
`backup-list`/`backup-restore`); §7 — сквозные прогоны без Docker.

## 0. Железные правила серии (из goTelegram, не обсуждаются)

1. `install.sh` + `lib/*.sh` подключаются через `source`, сборки нет. В шапке `install.sh`:
   `set -uo pipefail` (без `-e`), PATH задаётся явно (`/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin`),
   `SCRIPT_DIR` через `readlink -f "${BASH_SOURCE[0]}"` с fallback на `/opt/goamnezia`.
2. **Subshell-capture:** функция, вызываемая через `$()`, печатает в stdout только результат;
   всё остальное (`log_*`, спиннеры, подсказки) — в stderr. Нарушение = баг.
3. **Мост `--action=`:** последняя строка stdout — JSON `{"status":"success"|"error","message":"…","code":"…",…}`.
   Весь UI — в stderr. Действия с `readonly: true` в `lib/actions.json` замок не берут.
4. **Один замок** `/run/goamnezia/config.lock` на все интерфейсы: `ga_with_config_lock fn args…`
   (реентрантный, `GA_CONFIG_LOCK_HELD=1`), пара `fn()` / `_fn_locked()`. Таймаут 30 с → код `lock_timeout`, exit 75.
   Таймеры (`GOAMNEZIA_WHO=timer` у юнитов `goamnezia-*.service`, ночная копия `backup_tick`) ждут дольше —
   `GOAMNEZIA_TIMER_LOCK_WAIT`, по умолчанию 120 с — и, не дождавшись (замок держит установка или обновление),
   отвечают `success` с `code: busy`, `skipped: true` и строкой «<тик>: занято — пропущено, следующий запуск по
   таймеру», exit 0: юнит не падает в failed (WRP-03). Бюджет — один на тик: миграция перед действием ждёт из
   него, действию — остаток до общего срока (`GA_TIMER_LOCK_DEADLINE`), а не 30 + 120 с (WRP-06).
5. **Единый источник правды** — `/opt/goamnezia/config.json` и `/opt/goamnezia/users.json` (0600),
   запись только атомарно: `jq … > tmp` рядом с целью + `mv`, с мержем поверх старого.
   Неразбираемый файл — **отказ**, а не пустой скелет и не пустой список: читатель обязан
   отличать «файла ещё нет» от «файл сломан» и пробрасывать код наружу (`config_corrupt`,
   `users_corrupt`), писатель — не писать ничего. Перед отказом сохраняется копия битого файла
   в `backups/`, и сообщение называет её и путь восстановления: подмена молча стоила бы
   человеку всех настроек установки или всех приватных ключей.
6. **i18n с первого дня:** ни одной видимой строки в коде, только `t key` / `tf key args…`.
   Словари: `lib/lang/ru.sh`, `lib/lang/en.sh` (ядро) + фрагменты `lib/lang/ru.d/<module>.sh`,
   `lib/lang/en.d/<module>.sh` (по одному на модуль, чтобы писать параллельно). Формат строки:
   `I18N[key]="значение"`. Ключи `snake_case`, паритет RU/EN проверяется тестом.
7. **Секреты:** `0600` на конфиги и `.env`, `0700` на каталоги `state/`, `backups/`, панели;
   `mktemp` вместо предсказуемых путей; приватные ключи не попадают в логи и в ответы, кроме `user-key`.
8. **Валидация входа** регексами `[[ $x =~ $re ]]`, не `grep`; белые списки для профилей.
   Адрес, хост и MTU проверяются по **значению**, а не по виду, и ровно одной функцией на весь
   продукт: `_valid_ip`, `ga_valid_host`, `ga_valid_mtu` (`lib/common.sh`). Своя регулярка на
   месте вызова запрещена — `^([0-9]{1,3}\.){3}[0-9]{1,3}$` пропускает `999.999.999.999`,
   а две проверки одного и того же расходятся (см. §4.1 и §4.2).
9. **Неймспейс:** `goamnezia` во всём: `/opt/goamnezia*`, юниты `goamnezia-*`, `/var/log/goamnezia*.log`,
   `GOAMNEZIA_*` в окружении, функции модулей с префиксом модуля (`docker_*`, `amnezia_*`, `users_*`, `export_*`), внутренние хелперы `ga_*`.
   Контейнеры Amnezia и пути `/opt/amnezia/…` внутри них — официальные, не наши.
10. **Комментарии в коде по-русски и объясняют «почему»**; имена функций и переменных — по-английски.
11. **Подтверждения:** `confirm` читает `/dev/tty`, согласие только `y|yes|д|да` литералами. Разрушительное — предупреждение + подтверждение + предложение бэкапа.
12. **Проверки перед коммитом:** `bash tests/run_static.sh` (внутри: `bash -n` всех `.sh`, `shellcheck -S warning`, `python3 -m unittest discover -s tests`, паритет i18n, сверка `actions.json` ↔ диспетчер ↔ MCP), `install.sh --selftest`.
    Локально на macOS bash 5 лежит в `/opt/homebrew/bin/bash`; `run_static.sh` использует `${BASH5:-bash}`.

## 1. Раскладка на сервере

| Что | Путь | Права |
|---|---|---|
| код (эталон) | `/opt/goamnezia/` — `install.sh`, `lib/`, `goamnezia-bot/`, `goamnezia-mcp/`, `admin-web/`, `templates_catalog.json` | 755 / файлы 644, скрипты 755 |
| команда | `/usr/local/bin/goamnezia` → симлинк на `install.sh`; `/usr/local/bin/goamnezia-mcp` → `goamnezia-mcp/server.py` | |
| конфиг | `/opt/goamnezia/config.json` | 0600 |
| доступы агентов | `/opt/goamnezia/agents.json` (PLAN §6.10, §5.1) | 0600 |
| пользователи | `/opt/goamnezia/users.json` | 0600 |
| копия состояния контейнеров | `/opt/goamnezia/state/<container>/` (`awg0.conf`, `clientsTable`, `*.key`, `start.sh`; goWARP PRO — `warp/` и `warp-tools/usr/`, если есть) | 0700 |
| реестр прежних ID контейнеров с соседями в их сети | `/opt/goamnezia/state/.netdeps/<container>.ids` (WRP-01, lib/docker.sh «Соседи в сети контейнера»); там же `dangling.reported` — каким висящим соседям сторож уже сказал (WRP-06) | 0700 / 0600 |
| хранилище того же реестра между «Удалить всё» и новой установкой | `/var/lib/goamnezia-netdeps/<container>.ids` (`docker_netdeps_stash`; новая установка читает его в `docker_netdeps_reattach` и снимает, когда ссылок не осталось; WRP-06) | 0700 / 0600 |
| вендоренные upstream-скрипты | `/opt/goamnezia/lib/upstream/awg/{Dockerfile,configure_container.sh,run_container.sh,start.sh,template.conf}` + `images.json` | |
| бэкапы | `/opt/goamnezia/backups/` | 0700 |
| бот | `/opt/goamnezia-bot/` (`.env` 0600, `venv/`, `user_langs.json`; файлы привязки администратора `.admin_pairing_until`, `.admin_candidates.json`, `.admin_confirmed`, `.admin_pairing_done` / `.admin_confirmed.rejected`, `.admin_claim_code` — 0600, docs/BOT.md «Контракт привязки»; маркер прежних версий `.admin_claimed` никем не читается) | 0700 |
| панель | `/opt/goamnezia-admin/` (`token`, `static/`) | 0700 |
| MCP | `/opt/goamnezia-mcp/` (`token` для HTTP-транспорта, этап 2) | 0700 |
| страницы подключения | `/opt/goamnezia/pages/<id>/` (`meta.json`, `secret`, `state.json`; docs/CLIENTS.md §6, `lib/pages.sh`) | 0700 / файлы 0600 |
| зеркало сборок | `/opt/goamnezia/mirror/<app>/<platform>/` (файл сборки, `meta.json`, `LICENSE`, `SOURCE.txt`, `state.json`; `lib/mirror.sh`) — читает nginx | 0755 / файлы 0644 |
| сайт (Pro) | `/var/www/goamnezia-site/`, nginx `sites-available/goamnezia` | |
| замок | `/run/goamnezia/config.lock` | |
| логи | `/var/log/goamnezia.log`, `/var/log/goamnezia-audit.log`, `journalctl -u goamnezia-*` | |
| systemd | `goamnezia-bot.service`, `goamnezia-admin.service`, `goamnezia-mcp-http.service` (HTTP-транспорт MCP, только при `mcp.http_enabled`), `goamnezia-pages.service` (служба страниц подключения, только пока есть хоть одна страница), `goamnezia-isolation.service` (правило изоляции клиентов контейнеру профиля сразу после его старта по событиям Docker, `install.sh --isolation-watch`; ставится с таймерами — OPS-05), таймеры `goamnezia-stats`, `goamnezia-quota`, `goamnezia-backup`, `goamnezia-logbudget`, `goamnezia-watch`, `goamnezia-upstream`; фоновые загрузки зеркала — `systemd-run --unit=goamnezia-mirror-<app>-<platform>` | |
| обновление | `/opt/goamnezia/.prev_code/` (прежний код на время обновления: `files/`, `manifest`, `meta`, `install-source`, `units/`; кладёт `bootstrap.sh`, убирает удавшаяся миграция или откат), `.migrating_<ver>` (начатая миграция), `.update_failed.json` (отметка отката → `status.update_rollback`) — `lib/update.sh`, OPS-02/03 | `.prev_code` 0700, отметки 0600 |
| порты | UDP профилей; TCP 443 nginx (Pro); `127.0.0.1:1985` панель; `127.0.0.1:1986` MCP-HTTP (этап 2); `127.0.0.1:1987` служба страниц подключения (`pages.port`) | |

Переменные окружения для тестов и стендов: `GOAMNEZIA_DIR` (по умолчанию `/opt/goamnezia`),
`GOAMNEZIA_DRY_RUN=1` (docker-команды печатаются в stderr и не выполняются),
`GOAMNEZIA_ACTION_BIN` (для MCP и тестов — путь к `install.sh` или заглушке),
`GOAMNEZIA_ACTIONS_JSON` (путь к `actions.json`), `BASH5`,
`GOAMNEZIA_SERVER_IP` (адрес сервера без обращения к сети — `get_server_ip` отдаёт его как есть;
dry-run на macOS и CI без интернета иначе ждали бы три таймаута `curl` на каждый `status`),
`UPSTREAM_DIR` (копия `lib/upstream` — тесты кладут её во временный каталог, чтобы не писать в
проект), `GOAMNEZIA_BUILD_ROOT` (куда собирать контекст `docker build`; в dry-run по умолчанию
`mktemp -d`), `GOAMNEZIA_LOCK_DIR`, `GOAMNEZIA_LOG_FILE`, `GOAMNEZIA_AUDIT_LOG`,
`GOAMNEZIA_NOTIFY_DIR` (очередь событий для бота), `GOAMNEZIA_NOTIFY_MAX` (предел очереди, 200),
`GOAMNEZIA_AWGKEYS` (путь к `awgkeys.py`),
`GOAMNEZIA_AWG_SHOW_FIXTURE` (файл вместо `awg show awg0 dump`), `GOAMNEZIA_AGENTS_JSON` (путь к
`agents.json` для `lib/agents.sh` и `server.py`), `GOAMNEZIA_SYSTEMD_DIR` (куда `lib/agents.sh`,
`lib/website.sh` и `lib/timers.sh` пишут юниты в dry-run), `MCP_DIR` (рабочая копия сервера MCP в
`ExecStart` юнита), `GOAMNEZIA_UPSTREAM_FIXTURE_DIR` (сохранённые ответы GitHub/Docker Hub для
`lib/upstream.sh`: `releases.json`, `hub_<образ>_tags.json`, `hub_<образ>_tag_<тег>.json`,
`raw/<тег>/awg/<файл>`; без него dry-run отвечает `unreachable`), `GOAMNEZIA_UPSTREAM_WORK`
(память таймера обновлений, по умолчанию `$GOAMNEZIA_DIR/upstream`), `GOAMNEZIA_UPSTREAM_TIMEOUT`
(секунды на один запрос, по умолчанию 20), `GOAMNEZIA_CONTAINER_FS_DIR` («файловая система»
контейнеров на стенде: `<каталог>/<контейнер>/<путь>`; `docker_read_file` читает оттуда,
`docker_container_state` считает такой контейнер `running`, файлы `.state`, `.iface_down`, `.image`
изображают остановку, молчащий интерфейс и образ), `GOAMNEZIA_DOCKER_PS_FIXTURE` (сохранённый
`docker ps` — общая с `lib/preflight.sh`), `GOAMNEZIA_DF_FIXTURE` (сохранённый `df -Pm`: место на диске для
всех порогов через `ga_df_mb` вместо диска машины; `tests/run_static.sh` задаёт фикстуру с запасом, TAIL-12),
`GOAMNEZIA_STATS_TODAY` (день ящика статистики в тестах),
`GOAMNEZIA_PAGES_DIR` / `GOAMNEZIA_MIRROR_DIR` (каталоги страниц подключения и зеркала сборок на стенде),
`GOAMNEZIA_CLIENTS_JSON` (каталог приложений вместо `lib/clients.json`), `GOAMNEZIA_MIRROR_CURL` (программа
вместо `curl` для `lib/mirror.sh`: в dry-run без неё модуль в сеть не ходит), `GOAMNEZIA_MIRROR_API_BASE` /
`GOAMNEZIA_MIRROR_RAW_BASE` (адреса вместо `https://api.github.com/repos` и `https://raw.githubusercontent.com`),
`GOAMNEZIA_MIRROR_FOREGROUND=1` (вести загрузку зеркала в текущем процессе, а не в фоне),
`GOAMNEZIA_CORE_READ` / `GOAMNEZIA_CORE_READ_ROOT` / `GOAMNEZIA_READ_LOCK_WAIT` (быстрые чтения панели модулем
`lib/core_read.py`: выключатель, корень ядра на стенде с мостом-обёрткой, ожидание замка — docs/PANEL.md §5b),
`GOAMNEZIA_READ_SWR=0` (панель: живые данные у карточки и списка людей — только свежим сбором, без прежнего снимка).

## 2. Профили

| id | линейка | контейнер | конфиг в контейнере | iface | подсеть | порт по умолчанию | образ |
|---|---|---|---|---|---|---|---|
| `awg2` | AmneziaWG 2.0 (**по умолчанию, ставится всегда**) | `amnezia-awg2` | `/opt/amnezia/awg/awg0.conf` | `awg0` | `10.8.2.0/24` | вне `pro`: случайный 1024–9999; `pro`: **443** | `amneziavpn/amneziawg-go` по дайджесту из `lib/upstream/images.json` |
| `awg3` | AmneziaWG 3.1 | `amnezia-awg3` | то же | `awg0` | `10.8.3.0/24` | случайный 1024–9999 | тот же |
| `awg1` | AmneziaWG 1.0/1.5 | `amnezia-awg1` | то же | `awg0` | `10.8.1.0/24` | случайный 1024–9999 | тот же (запасной: `amneziavpn/amnezia-wg`, см. PLAN D2) |

**Диапазон порта — решение владельца 10.09.2026, оно же в коде** (`GA_PORT_LOW_MIN`/`GA_PORT_LOW_MAX`
в `lib/common.sh`, `_random_port` в `install.sh`, `ga_agents_random_port` в `lib/agents.sh`):
случайный порт берётся из **1024–9999**, потому что номера выше десяти тысяч у части провайдеров
и корпоративных сетей режутся целым блоком, и человек получал ключ, который у него же не
открывается. Общеизвестные порты (`ga_port_wellknown`) пропускаем. Прежний диапазон 30000–49999
(так выбирает порт само приложение Amnezia) остался **только запасным**: `GA_PORT_HIGH_MIN`/
`GA_PORT_HIGH_MAX` берутся, когда весь низкий занят, и об этом говорят вслух. Отдельно от этого
живут временные раздачи файлов (`GA_SHARE_PORT_MIN`/`MAX` в `lib/share.sh`): там высокий
диапазон выбран нарочно — случайный высокий порт на минуту не выделяется среди прочих.

Колонка «линейка» — это **умолчание свежей установки**, а не свойство идентификатора. Живая
линейка профиля хранится в `config.json` полем `line` и берётся из его фактических параметров
(§4.4, `ga_profile_line`): приложение Amnezia ставит в контейнер `amnezia-awg2` любую линейку,
и после `import-adopt` профиль `awg2` может быть 3.1. Проверка и генерация параметров идут по
«линейке-основанию» — семейству фактической (`ga_profile_line_base`: 1.0 и 1.5 — одно семейство,
2.0 и 3.1 — свои).

Адрес интерфейса сервера в контейнере — сетевой адрес подсети (`10.8.2.0/24`), как у Amnezia;
клиенты получают `.1`–`.254`. Внутри контейнера всё делается официальными скриптами из
`lib/upstream/awg/` с подстановкой `$ПЕРЕМЕННЫХ` (те же имена, что у клиента Amnezia:
`$CONTAINER_NAME`, `$AWG_SERVER_PORT`, `$AWG_SUBNET_IP`, `$WIREGUARD_SUBNET_CIDR`, `$SERVER_IP_ADDRESS`,
`$JUNK_PACKET_COUNT`, `$JUNK_PACKET_MIN_SIZE`, `$JUNK_PACKET_MAX_SIZE`, `$INIT_PACKET_JUNK_SIZE`,
`$RESPONSE_PACKET_JUNK_SIZE`, `$COOKIE_REPLY_PACKET_JUNK_SIZE`, `$TRANSPORT_PACKET_JUNK_SIZE`,
`$INIT_PACKET_MAGIC_HEADER`, `$RESPONSE_PACKET_MAGIC_HEADER`, `$UNDERLOAD_PACKET_MAGIC_HEADER`,
`$TRANSPORT_PACKET_MAGIC_HEADER`, `$SPECIAL_JUNK_1..5`, `$HEADER_PROTECTION_KEY`,
`$CONTENT_PADDING_ADDITION`, `$REKEY_AFTER_TIME`, `$REKEY_TIMEOUT`, `$REJECT_AFTER_TIME`,
`$KEEPALIVE_TIMEOUT`, `$MAX_HANDSHAKE_ATTEMPTS`, `$RANDOM_TRAILERS`, `$DISABLE_COOKIES`,
`$PERSISTENT_KEEPALIVE`, `$PRIMARY_DNS`, `$SECONDARY_DNS`, `$WIREGUARD_CLIENT_IP`,
`$WIREGUARD_CLIENT_PRIVATE_KEY`, `$WIREGUARD_SERVER_PUBLIC_KEY`, `$WIREGUARD_PSK`).
Пустые значения → строка удаляется (`sed -i '/^[^=]*= *$/d'`), как в upstream. Наша копия
`start.sh` отличается от upstream только идемпотентными правилами iptables (`-C … || -A …`).

## 3. Схемы данных

### 3.1 `config.json`

```json
{
  "schema": 1,
  "version": "0.1.0",
  "installed": true,
  "mode": "lite",
  "lang": "ru",
  "domain": "",
  "server_ip": "203.0.113.10",
  "endpoint_host": "",
  "client_dns": ["1.1.1.1", "1.0.0.1"],
  "client_isolation": true,
  "profiles": {
    "awg2": {
      "line": "2.0",
      "container": "amnezia-awg2",
      "port": 443,
      "subnet": "10.8.2.0/24",
      "iface": "awg0",
      "image": "amneziavpn/amneziawg-go",
      "image_tag": "3.1.20260828",
      "image_digest": "sha256:…",
      "preset": "default",
      "params": {"Jc": "5", "Jmin": "10", "Jmax": "50", "S1": "12", "S2": "12", "S3": "12", "S4": "12",
                 "H1": "…", "H2": "…", "H3": "…", "H4": "…", "I1": "", "I2": "", "I3": "", "I4": "", "I5": "",
                 "HeaderProtectionKey": "", "ContentPaddingAddition": "", "RekeyAfterTime": "", "RekeyTimeout": "",
                 "RejectAfterTime": "", "KeepaliveTimeout": "", "MaxHandshakeAttempts": "", "RandomTrailers": "", "DisableCookies": "",
                 "PersistentKeepalive": "25", "MTU": "1280"},
      "server_pubkey": "…",
      "installed_at": "2026-09-08T12:00:00Z",
      "enabled": true
    }
  },
  "panel": {"port": 1985, "miniapp_path": "", "trust_tunnel": true},
  "site": {"template_id": "", "template_source": "", "listen": "public", "mcp_location": false, "panel_path": ""},
  "options": {"drop_icmp": false, "alt_svc": false},
  "mcp": {"http_enabled": false, "http_port": 1986}
}
```

`endpoint_host` пустой → в `Endpoint` подставляется домен сервера в режиме Pro, где AmneziaWG стоит на UDP/443
под доменом (`mode` — `pro`, `site.cert` — не `selfsigned`; `ga_export_pro_domain`), иначе `server_ip`. В
`lite_site` VPN живёт на своём порту, и в ключах IP. Вписанный домен или IP действует в любом режиме.

`wizard` (ключа может и не быть) — установка не закончена (INST-01): ответы мастера, пока он не дошёл до конца, — `step` (на каком вопросе остановились: `domain` | `line` | `profiles` | `ports`, или `install` — ответы все, идёт установка), `mode`, `domain`, `line443` (`awg2` | `awg3`), `awg3` и `awg1` (`true` | `false`), `ports` (`{профиль: порт}`), `admin` (первый пользователь, когда заведён), `started`. Пишет только мастер SSH; снимается в конце установки. Пока запись есть, `install.sh` без аргументов открывает мастер (и при `installed: true`), шапка меню пишет «Установка не закончена». `mode` и `domain` в записи — желаемые: в корень `mode` и `domain` режима с сайтом пишет модуль сайта, когда сайт поднят (`_ga_site_set_mode`), — `status` и шапка меню всегда показывают действующий режим. Прерванная установка версий до 1.0.9 (режим с сайтом в корне, а vhost нет и `site.cert` пуст) при запуске меню переводится в такую же запись, а `mode` — в `lite`.

`client_isolation` (по умолчанию `true`, ключа может и не быть) — изоляция клиентов профиля (SEC-NET-1): в
сетевом пространстве каждого контейнера профиля — и своего, и подхваченного у приложения (у legacy — `wg0`) —
первым в `FORWARD` стоит `-i awg0 -o awg0 -j DROP`: люди одного профиля не достают через сервер до устройств
друг друга, выход в интернет и NAT не трогаются. У своих профилей правило — строкой в `start.sh` (метка
`goamnezia-client-isolation`), у подхваченных start.sh приложения не переписывается; при каждом подъёме
контейнера (`ga_amn_wait_up`), тиком сторожа и миграцией обновления правило приводится к настройке
(`ga_amn_isolation_apply`). `false` — правило не ставится, а стоящее снимается. Переключателя в интерфейсах
пока нет; `status` и карточка профиля отдают значение (`client_isolation`) только для показа.

Блок `mcp` (PLAN §6.10, §5.1): `http_enabled` — включён ли HTTP-транспорт (пишет только
`lib/agents.sh`: `mcp-http-on/off`), `http_port` — порт на петле за nginx (1986), необязательный
`public_port` — отдельный публичный TLS-порт в Lite без сайта (пишет `mcp-http-on --port=N`; 0 или
отсутствие = нет), необязательные `allowed_origins` (список `Origin`, которые сервер принимает
помимо `Host`) и `port_exclusive` (доступ с персональным портом принимается только на нём).
`site.mcp_location` — маршруты `/a-<secret>/mcp`, `/a-<отпечаток кода>/enroll` и `/r-<плавающий путь>/mcp` в vhost, одним `location ~ "^/[ar]-[A-Za-z0-9_-]{43}/"` (`website_mcp_location_on/off`);
`mcp-http-on` включает его в `pro`/`lite_site`, `mcp-http-off` снимает. **`mcp-http-off`
записывает `http_enabled=false` только если юнита действительно не осталось**: снятие
глушило свои шаги, и не снявшийся юнит превращался в спокойное «выключено» при живой
службе (DEFECTS §4.2). Не удалось снять — отказ `unit_failed` с причиной, флаг не меняется.

`mode` принимает три значения — **договор уточнён владельцем 10.09.2026, и прежний
(«`lite_site` = второй режим без домена, на самоподписанном») отменён**: `lite` — только VPN,
сайта нет; `lite_site` — домен, сайт-заглушка и панель на TCP/443 с настоящим сертификатом
Let's Encrypt, AmneziaWG на своём отдельном порту; `pro` — то же самое, но AmneziaWG стоит на
UDP/443 рядом с сайтом. Два режима с сайтом отличаются **только местом AmneziaWG**: домен,
сертификат, сайт и панель у них общие, поэтому переход между ними (`website_switch_site_mode`)
не трогает ни nginx, ни сертификат — он двигает только порт профиля и слово в `mode`.
Самоподписанный сертификат — **запасной путь** ровно для двух случаев: домена ещё нет или его
A-запись ещё не указывает на сервер; он включается в ЛЮБОМ из двух режимов с сайтом и выходит
из игры, как только домен готов. Поэтому вид сертификата **не выводится из режима**: в `site`
хранится `"cert"` (`"letsencrypt"` — домен готов, `"selfsigned"` — запасной путь, `""` — `lite`),
и спрашивают его `ga_site_cert_kind` / `ga_site_domain_ready`, а не название режима.
Самоподписанный ключ EC P-256 и сертификат лежат в `/opt/goamnezia/tls/site.{key,crt}`
(каталог 0700, файлы 0600; путь стенда — `GOAMNEZIA_TLS_DIR`), CN и SAN — `server_ip`, срок
3650 дней; отпечаток SHA-256 (`website_cert_fingerprint`, формат `AA:BB:…`) отдают `status`
(`site.cert_fingerprint_sha256`), `site-check` и `change-mode` — по нему агенты MCP и человек
доверяют серверу. Сертификат не перевыпускается, пока жив (≥ 30 дней) и содержит текущий IP
(смена IP, истечение и `website_selfsigned_renew` — перевыпуск с новым отпечатком).
Функции `lib/website.sh`: `website_selfsigned_ensure [--force]`, `website_selfsigned_expiry`,
`website_cert_fingerprint [файл]`, `website_selfsigned_renew`, `website_enable_site_selfsigned
[tpl]` (nginx → сертификат → vhost из того же шаблона `lib/nginx/goamnezia.conf.tpl` с
`server_name _`, `ssl_certificate` на наши файлы, блоки-варианты `ONLY_PRO` / `ONLY_SELFSIGNED`,
якоря панели и MCP на месте, панельная location не включается, маршрут агентов и Alt-Svc —
включаются → шаблон, случайный если сайта нет → `mode` = второй аргумент (по умолчанию
`lite_site`), `site.cert=selfsigned`), `website_disable_site` (→ `lite`: vhost убран, сайт и
сертификат остаются на диске), `website_enable_pro <domain> [tpl] [pro|lite_site]` —
**основной путь обоих режимов с сайтом**, стартует и с запасного самоподписанного (сертификат
заменяется на Let's Encrypt, сайт и шаблон не трогаются; отказ certbot возвращает тот vhost,
что работал до попытки), `website_switch_site_mode <pro|lite_site>` — переход МЕЖДУ двумя
режимами с сайтом одной записью `mode` (nginx, сертификат, домен, шаблон и панель не трогаются
вовсе; из `lite` — `needs_site`), `website_disable_pro [lite_site|lite]` — совместимое имя:
`lite_site` = тот же `website_switch_site_mode` (домен и сертификат НЕ снимаются, второму режиму
они нужны так же), `lite` — vhost убран целиком. Переходы не трогают контейнеры и ключи.

`site.panel_path` — зеркало `panel.miniapp_path`: оба поля пишет `website_panel_path_set` /
`website_panel_path_clear` (`lib/website.sh`). Панель на домене включается **сама** при включении сайта с
Let's Encrypt (`_ga_site_panel_autoenable_locked` из `website_enable_pro`, решение владельца 17.09.2026),
кроме случая `panel.miniapp_auto: false` — его пишет `website_panel_path_clear` («выключил сам»), а
`website_panel_path_set` возвращает `true`; главный экран SSH-меню показывает адрес панели строкой «Панель:»; бот читает `panel.miniapp_path` для кнопки
WebApp, панель и блок `site` в `status` — `site.panel_path` / `site.panel`. Поля `site.listen`,
`site.mcp_location`, `site.panel_path` входят в `_ga_config_skeleton` (`lib/common.sh`);
у `site.listen` единственное значение — `public` (сайт сам держит TCP/443), поле сохранено ради
стабильности ответов `status` и `site-check`.
`ga_config_set` хранит `true`/`false`/`null` булевыми и `null`, а не строками (проверка JSON без
`jq -e`, который считает `false` неуспехом).

Страницы подключения и зеркало сборок (docs/CLIENTS.md, `lib/pages.sh`, `lib/mirror.sh`; поля необязательные,
в скелет не входят — модули читают их с умолчаниями): `site.pages_location` (`true` — в vhost стоят
regex-локация `/p-<секрет>/` → `127.0.0.1:${pages.port}` и внутренняя локация `/_ga_mirror/`; ставит и снимает
только `lib/pages.sh` через `website_pages_location_on/off`: включается с первой страницей, снимается, когда
страниц не осталось), `pages.port` (порт службы `goamnezia-pages` на петле, по умолчанию 1987),
`mirror.max_file_mb` (предел одной сборки, 400) и `mirror.max_total_mb` (предел каталога зеркала, 3072).
`server_ip` записывает мастер установки (шаг «ответы в конфиг»), а `amnezia_profile_install`
дописывает его, если поле пусто (мост `--action` на конфиге без адреса); не определился — не
блокируем, `export_*` определят адрес при выдаче. `params` хранятся строками ровно так, как пойдут
в конфиг (значения `""` = параметр не задан).

Поля профиля спринта 4 (пишут `lib/upstream.sh` и `lib/import.sh`, читают все интерфейсы):
`previous` — `{image_tag, image_digest, client_release, updated_at}` предыдущего образа после
`upstream-update`/`core-update` (один шаг назад; `null` после отката), `core_updated_at`;
`imported: true`, `imported_at`, `built_image` (имя образа, собранного приложением) и
`preset: "imported"` у подхваченного контейнера; `iface` у legacy-контейнера `amnezia-awg` —
`wg0` (конфиг `/opt/amnezia/awg/wg0.conf`, утилиты `wg`), у остальных `awg0`; `image` такого
профиля — `amneziavpn/amnezia-wg`, `image_tag` — `latest`, `image_digest` — дайджест локально
вытянутого базового образа или `""`, если его не узнать.

Ленивый Pro (0.6.0, `lib/modes.sh`; поле необязательное, в скелет не входит): `lazy` — `{domain, keys: [имена], pages:
{имя: id страницы}}`. Пишет `lazy_setup`: ключи и страницы, заведённые сценарием, засчитываются при повторе — повтор после
обрыва или из «Режим и сайт» не плодит лишних людей и страниц. Больше его никто не читает.

### 3.2 `users.json`

```json
{
  "schema": 1,
  "users": [
    {
      "name": "anna",
      "label": "Анна",
      "created": "2026-09-08T12:00:00Z",
      "enabled": true,
      "quota_bytes": 0,
      "expires": "",
      "traffic": {"rx": 0, "tx": 0, "last_rx": 0, "last_tx": 0, "updated": ""},
      "peers": {
        "awg2": {"ip": "10.8.2.5", "privkey": "…", "pubkey": "…", "psk": "…", "created": "…", "active": true, "last_handshake": 0, "rx": 0, "tx": 0}
      }
    }
  ]
}
```

`name` — идентификатор `^[A-Za-z0-9_-]{1,32}$`, уникален; `label` — отображаемое имя (любые
буквы, до 48 символов). Необязательный `endpoint_ip` — свой адрес сервера в ключах пользователя
(IPv4, который есть на машине; `users_set_endpoint_ip`, мост `user-set-address`, docs/EDGEIPS.md §3.1). Действует, когда
общий Endpoint — IP или домен сервера по умолчанию; домен, вписанный в `endpoint_host`, побеждает: адрес
выбирает DNS. Адрес, пропавший с машины, при выдаче
ключа — `address_gone`; без поля ключ собирается ровно как раньше. Разовый адрес одной выдачи (`user-key` /
`share-create --endpoint`, §4.6, docs/EDGEIPS.md §3.4 «Разовый ключ на адрес») в `users.json` не пишется
вовсе: закрепить адрес за человеком на все следующие ключи — только `endpoint_ip`. В публичном виде (`users_list`/`users_show`) поле есть
всегда, строкой: пусто — общий адрес. `active` у пира = сейчас в конфиге контейнера (выключенный
пользователь: `enabled=false`, все `active=false`, записи остаются). Пользователь, подхваченный
из контейнера приложения (`import-adopt`, `import-reconcile`): `imported: true` у записи и у
пира, `privkey: ""` (ключ клиента есть только у человека и в выданной ему ссылке или файле —
приложение его не хранит, в его бэкапе его тоже нет), `psk` — из `[Peer]` конфига; `user-key`
ему отвечает `no_private_key`. Выходов два: **`user-attach-key`** — админ передаёт ссылку `vpn://`
или текст `.conf`, которые когда-то отправил человеку, модуль проверяет, что открытый ключ,
посчитанный из приватного, совпадает с пиром, и кладёт приватный в `users.json` (пир получает
`key_attached_at`; у человека не меняется ничего); или перевыпуск пира (`user-reissue-peer`) —
старый ключ перестаёт работать. `users_list`/`users_show` отдают `imported` в публичном виде, а КАЖДЫЙ ПИР —
**`has_key`** (`true`, когда приватная часть на сервере есть). Признак нужен всем трём
интерфейсам: приватный ключ наружу не отдаётся, и без него ни бот, ни панель не могли
отличить пир, по которому ключ выдаётся, от пира, по которому выдача заведомо ответит
отказом, — человеку показывали кнопки, гарантированно ведущие в ошибку. Правило: где есть
выдача ключа, там при `has_key: false` вместо неё предлагается перевыпуск и говорится, что
уже поставленный клиенту конфиг продолжает работать (SSH — `user_imported_note`,
`user_peer_reissue_item`). Трафик копится дельтами:
`rx/tx` — накопленное, `last_rx/last_tx` — последнее показание `awg show`, при уменьшении
показания (рестарт контейнера) дельта = новое значение. `clientId` в `clientsTable`
контейнера = `pubkey`, `userData.clientName` = `label`, `creationDate` = `created`. Запись
таблицы опирается на прежнюю (копия в `state/`): `userData`, записанное приложением Amnezia у
подхваченных людей (`creationDate` в его формате Qt TextDate, `allowed_ips` и прочее), остаётся
дословно; `clientName` меняется, только если подпись сменили у нас, `creationDate` из `created`
ставится лишь там, где даты не было (ACC-1).

### 3.3 `lib/upstream/images.json`

```json
{"amneziawg-go": {"repo": "amneziavpn/amneziawg-go", "tag": "3.1.20260828", "digest": "sha256:…", "checked": "2026-09-08"},
 "amnezia-wg":  {"repo": "amneziavpn/amnezia-wg",   "tag": "latest",       "digest": "sha256:…", "checked": "2026-09-08"},
 "client_release": "5.0.2.1"}
```

В репозитории файл — эталон вендоринга (сверяется `tests/test_upstream_vendoring.py`). На сервере
`$UPSTREAM_DIR/images.json` — рабочая копия: `upstream-update`/`upstream-rollback`/`core-update`
пишут в неё тег, дайджест (`amd64_digest`), `checked` и `client_release` того, что реально
работает, и `docker_pull_pinned` для новых профилей берёт уже её (§4.9).

## 4. Модули bash — функции и что они печатают

Все функции возвращают 0/≠0; JSON в stdout печатают только те, у кого это сказано. Ошибки —
`log_error` в stderr + код возврата. Никаких `exit` внутри библиотек (только в `install.sh`).

### 4.1 `lib/common.sh` (порт из goTelegram, замена имён)

Константы: `GOAMNEZIA_VERSION="0.1.0"`, `GOAMNEZIA_DIR`, `CONFIG_FILE`, `USERS_FILE`, `STATE_DIR`,
`BACKUP_DIR`, `UPSTREAM_DIR`, `LOCK_DIR=/run/goamnezia`, `LOCK_FILE`, `LOG_FILE`, `AUDIT_LOG`,
`BOT_DIR=/opt/goamnezia-bot`, `ADMIN_DIR=/opt/goamnezia-admin`, `MCP_DIR=/opt/goamnezia-mcp`,
`SITE_ROOT=/var/www/goamnezia-site`, `PROFILE_IDS="awg2 awg3 awg1"`, палитра, `log_*`,
`spinner`/`run_with_spinner`, `progress_bar`, `confirm`, `select_option`, `generate_hex n`,
`validate_domain`, `validate_name`, `validate_port`, `check_root`, `check_os`, `get_arch`
(только `x86_64`, иначе ошибка с кодом `unsupported_arch`), `get_server_ip`, `check_disk_space`,
`init_dirs`, `ga_with_config_lock`, `ga_config_get <jq-путь>` (stdout, `null` → пусто),
`ga_config_set <jq-путь> <json>`, `ga_config_merge <json>`, `ga_config_read` (stdout JSON,
код 1 + `config_corrupt` на неразбираемом файле), `ga_users_read` (stdout JSON),
`ga_users_intact` (0 — файла нет или он разбирается; иначе спасательная копия и `users_corrupt`),
`ga_users_load <переменная>` (единственное правильное чтение `users.json`),
`ga_users_write` (stdin JSON → атомарно с `fsync`), `ga_secret_tmpdir`, `ga_mktemp_secret`,
`ga_wipe_file`, `ga_wipe_dir` (временные файлы с секретами), `ga_emit_json <status> <message> [k=v…]`
(экранирование; значения, начинающиеся с `@json:`, вставляются как JSON без кавычек — в том
числе `false` и `null`; пустое или невалидное после `@json:` уходит строкой, ответ остаётся
валидным JSON), `ga_audit <who> <action> <details>`, `ga_now` (ISO-8601 UTC), `apt_install`,
`apt_lock_wait`, `apt_pkg_for_cmd`, `check_deps_present`, `show_disclaimer`.
`get_server_ip` сначала смотрит `GOAMNEZIA_SERVER_IP`, потом идёт в сеть; при неудаче печатает
`0.0.0.0` и возвращает 1.

**Битый `config.json` — отказ, а не скелет.** `ga_config_read` (stdout JSON) отличает «файла ещё
нет» от «файл не разбирается»: в первом случае отдаёт скелет и код 0, во втором — код 1 и
`config_corrupt`. `ga_config_set` и `ga_config_merge` спрашивают то же самое (`_ga_config_intact`)
ПЕРВОЙ строкой и на битом файле не пишут ничего: иначе первая же настройка кладёт пустой скелет
поверх режима, домена, портов панели и параметров всех профилей. Перед отказом делается копия
битого файла в `$BACKUP_DIR/config.json.broken-<время правки>` (0600, по одной на версию файла),
и сообщение называет и копию, и путь восстановления из бэкапа — повреждённый файл не должен
становиться могилой. Тот же договор у `users.json`: `ga_users_read` → код 1 и `users_corrupt`.

**Битый `users.json` — одно правило чтения на весь продукт.** В этом файле живут приватные
ключи всех клиентов, поэтому «файла нет» и «файл сломан» обязан различать КАЖДЫЙ читатель, а не
только писатель. Читают его ровно одним способом — `ga_users_load <переменная>`: она спрашивает
`ga_users_intact` **в текущей оболочке** (в `$( )` код отказа не переживает подоболочку) и
только потом читает. Подстановка команды вокруг `ga_users_read` и конвейер
`ga_users_read | jq …` запрещены: код возврата там теряется, «пусто» становится
«пользователей нет», и дальше либо пустой скелет уезжает в файл (исчезают ключи всех
пользователей), либо конфиг сервера собирается БЕЗ ЕДИНОГО ПИРА и применяется в контейнере
(связь теряют разом все). Отказ обязан случиться **до первой записи и до первой разрушительной
операции**: `amnezia_profile_remove` читает пользователей первым действием, до `docker rm` и до
сноса ключей профиля. `ga_users_intact` перед отказом спасает копию в
`$BACKUP_DIR/users.json.broken-<время правки>` (0600, по одной на версию файла) и говорит, где
она. Последний рубеж — сам писатель: `ga_users_write` спрашивает `ga_users_intact` первой
строкой и поверх неразбираемого файла не пишет ни из какой ветки. Целым считается файл, который
`jq` разобрал **и** отдал нулевой код: обрыв записи поверх более длинного содержимого оставляет
мусор в хвосте, и `jq` печатает разобранную часть, а отказывается только потом.

**Атомарная запись переживает не только соседний процесс, но и выключение питания.**
`_ga_write_json` заканчивается `_ga_commit_file`: `fsync` временного файла → `rename` → `fsync`
каталога — тот же порядок, что у хранилища цепочек агентов (§5.1). Без этого ядро вправе держать
и содержимое, и запись каталога в кэше, и штатным итогом внезапного выключения становится битый
`users.json`, то есть вход в правило абзацем выше. Не подтвердилось — это отказ `write_failed`, а
не успех. `python3` в системе нет — кладём обычным `mv`: гарантия слабее, но отказывать в записи
из-за отсутствия синхронизатора нельзя.

**Временные файлы с секретами — в память и с затиранием.** Через временные файлы продукта
проходят готовый клиентский конфиг с приватным ключом (`ga_capture`), приватный ключ сервера и
серверный конфиг (`docker_write_file`), PNG с QR (это тот же ключ картинкой, `export_bundle`).
Каталог даёт `ga_secret_tmpdir` — явная настройка `GOAMNEZIA_SECRET_TMPDIR`, затем `/dev/shm`,
затем `$LOCK_DIR` (на Linux это `/run`, tmpfs), и только последним средством общий каталог
временных файлов; файл создаёт `ga_mktemp_secret` (0600), убирает `ga_wipe_file` — перезаписью
нулями по фактическому размеру, а не одним `rm`, который снимает имя, а не блоки.

**Резервная копия не сохраняет порчу как исправность, а восстановление не кладёт её обратно.**
`create_backup` спрашивает `_ga_backup_sources_intact` ДО сборки архива: битый `config.json` или
`users.json` — отказ `backup_source_corrupt`, архив не создаётся, копия битого файла спасается.
Иначе ежесуточный таймер за глубину хранения выносит все здоровые архивы и оставляет тридцать
копий мусора. `metadata.json` архива не подставляет правдоподобные умолчания вместо непрочитанных
значений и несёт поле `sources: {config, users}` со значениями `ok|broken|absent`; карточка
восстановления показывает его, когда оно не `ok/ok`. `restore_backup` проверяет архив
(`_restore_archive_intact`, отказ `backup_corrupt` с именем файла) **до карточки и до вопроса
человеку**, а снимок текущего состояния снимает **до первой записи и до остановки служб**: не
удалось снять — восстановление отменяется (`backup_snapshot_failed`). Отказ копирования не
выдаётся за успех (`restore_write_failed`). Нечисловая и нулевая глубина хранения не удаляют
ничего.

**Тот же договор у статистики.** `stats.json` не разбирается — отказ `stats_corrupt` со
спасательной копией, а не молчаливый скелет. Данные там не критичные, но решение «выбросить
тридцать суток» не принимается молча.

**Адрес выдачи ключа обязан быть настоящим.** `get_server_ip` при неудаче печатает `0.0.0.0` и
возвращает 1. Код возврата выбрасывать нельзя, а `0.0.0.0` — непустая строка и сама по себе
страж не срабатывает. `ga_export_endpoint_host` отвергает `0.0.0.0` из любого источника и
проверяет значение через `ga_valid_host`; не определился — отказ `endpoint_unknown`. Иначе
человек получает файл, QR и ссылку, которые никогда не соединятся, без единого предупреждения.

**Занятость порта выясняется одним разбором на продукт.** `docker_port_free` зовёт
`ga_pf_listeners` из `lib/preflight.sh`. Разбор `ss` не привязан к номеру колонки: при
однопротокольном вызове пятое поле — это peer-адрес, а не локальный, и условие вида
`awk '{print $5}' | grep "…порт$"` не срабатывает никогда, потому что там всегда `0.0.0.0:*`.
Свой разбор в модуле — повод считать порт соседа свободным и увести у него трафик.

**Адреса проверяются по значению, а не по виду.** `_valid_ip <строка>` — IPv4 по октетам (ровно
четыре, до трёх цифр, без ведущих нулей, каждый ≤ 255); `ga_valid_host <строка>` — то, что годится
в `Endpoint`: имя узла (минимум две метки, строчные) ИЛИ IPv4 по `_valid_ip`. Собственные
регулярные выражения вида `^([0-9]{1,3}\.){3}[0-9]{1,3}$` в коде запрещены — они пропускают
`999.999.999.999`. Все места, принимающие адрес снаружи (`dns-set`, `endpoint-set`, списки
доступа), обязаны звать эти две функции. `ga_valid_mtu` — там же, договор в §4.2.

### 4.2 `lib/awg_params.sh` + `lib/awgkeys.py`

Вся логика параметров и форматов — в Python (stdlib, ≥ 3.8), bash-обёртки тонкие:

```
awg_params_generate <line> [preset]      → JSON params в stdout      (python awgkeys.py gen-params --line 2.0 --preset default)
awg_params_validate <line> <json> [write|adopt] → 0/1, сообщение в stderr (python awgkeys.py validate --ipackets …):
                                           правила тегов I1–I5, по умолчанию write — см. «Теги I1–I5» ниже
awg_keys_genkey / awg_keys_pubkey <priv> / awg_keys_genpsk   → base64 в stdout (python awgkeys.py genkey|pubkey|genpsk)
```

`lib/awgkeys.py` (подкоманды, всё через stdin/аргументы, вывод в stdout):

| подкоманда | вход | выход |
|---|---|---|
| `gen-params --line 1.0\|1.5\|2.0\|3.1 [--preset default\|quic\|router]` | — | JSON params (все ключи из §3.1, ненужные — `""`) |
| `validate --line L [--ipackets write\|adopt]` | JSON params на stdin | exit 0 / 2 + текст ошибки в stderr; `--ipackets` — правила тегов I1–I5 (по умолчанию `write`); `--engine` принимается для совместимости и ни на что не влияет |
| `ipackets [--mode write\|adopt]` | JSON params или `.conf` на stdin | только I1–I5, строки `ключ<TAB>код<TAB>тег<TAB>число` (пустое поле — `-`): exit 2 — беды (отказ), коды `syntax`, `b_hex`, `noarg`, `size`, `unknown`, `too_big`, а в `write` ещё `kernel_only` (`<c>`), `program_only` (`<d>` `<ds>` `<dz>`) и `b_first` (пакет начинается не с `<b 0x…>`, KEENETIC-I1, с 1.0.13; тег — первый тег пакета); exit 0 в `adopt` — предупреждения `kernel_only` / `program_only` / `b_first`. Ядро переводит их в слова (`ga_amn_ipacket_reason`, `ga_amn_ipacket_warnings`; ключи `amnezia_err_ipacket_<код>`, `amnezia_warn_ipacket_<код>`); `--engine` принимается и ни на что не влияет |
| `genkey` / `pubkey` / `genpsk` | `pubkey` читает приватник аргументом | base64 (X25519 на чистом Python, clamping по RFC 7748) |
| `conf --line L` | stdin JSON `{params, client:{ip, privkey, psk}, server:{pubkey, endpoint_host, port}, dns:[…], mtu}` | текст `.conf` для клиента (строки с пустыми значениями не печатать; для 1.0 не печатать `I*`, `S3/S4`, 3.x-строки; для 1.5 печатать `I*`) |
| `server-conf --line L` | stdin JSON `{params, server:{privkey, subnet, port}, peers:[{pubkey, psk, ip}]}` | текст серверного `awg0.conf` (`[Interface]` + `[Peer]`…, `I*` закомментированы `# I1 = …` как у Amnezia) |
| `vpn-encode` | stdin JSON `{container, line, params, conf_text, description, hostName, port, dns:[…], subnet}` | строка `vpn://…` (qCompress: 4 байта BE длины + zlib уровень 8, base64url без `=`) |
| `vpn-decode` | аргумент `vpn://…` или `-` (ссылка на stdin) | JSON |
| `qr-chunks` | аргумент `vpn://…` или `-` (ссылка на stdin; ядро зовёт только так — в ссылке приватный ключ, а argv виден всем в `/proc/*/cmdline`, SR-17) | JSON-массив строк для QR приложения Amnezia: сжатые байты режутся по 850, чанк = big-endian `int16 1984` + `uint8 count` + `uint8 index` + `uint32 len` + данные, base64url без `=` |
| `client-key` | stdin: ссылка `vpn://…` (и разбитая переносами строк — TK-7) или текст `.conf` клиента (BOM в начале снимается — TK-1) | JSON `{privkey, pubkey, psk, server_pubkey, ip}` (открытый ключ считается из приватного) |
| `detect-line` | stdin JSON params или текст `.conf` | `1.0`/`1.5`/`2.0`/`3.1` по правилам Amnezia (3.x-поле → 3.1; S3/S4 или дефис в H → 2.0; любой I → 1.5; иначе 1.0) |
| `packet-risk` (S4-FIX, с 1.0.12) | stdin JSON params | `{packet, budget, s4, safe_s4, mtu}` или `null`: пакет данных при стандартном MTU 1280 больше 1400 (S4 > 40) |
| `fix-s4` | stdin JSON params профиля с риском | `{S4, packet, budget}` — новый S4 12–24 (`S4_GEN_RANGE`, S4-MARGIN с 1.0.13): размеры попарно различны, класс пакетов данных не совпадает с рукопожатием (PARAM-06); риска нет — exit 2 |
| `i1-universal` (KEENETIC-I1, с 1.0.13) | stdin JSON params | I1 ровно прежнее умолчание (`LEGACY_DEFAULT_I1` после `normalize_params`) — строка универсального I1 со свежим ID (`default_i1()`); любой другой I1, пустой и quic — пустой вывод, exit 0; не JSON-объект — exit 2. Им пользуется миграция `_migrate_i1_universal` и подхват (§4.7) |
| `mtu-check --mtu N` | stdin JSON params | `{mtu, s4, packet, warnings}`: `path` (MTU выше 1280 и пакет больше 1440) |
| `limits --line L [--key K]` (с 1.0.13) | stdin JSON params; с `--key` — `{params: набор после записи, old: прежнее значение ключа}` (значения — stdin, не argv) | строки `код<TAB>ключ<TAB>значение<TAB>числа…` (пустое поле — `-`) — пределы того, что пишем мы (`limit_problems`, ниже): `corridor` (lo, hi, значение новых профилей), `timing_order` (срок, RekeyAfterTime.hi, «K = v (lo–hi); …» — что вне коридора), `junk_burst` (Jc, Jmax, Jc × Jmax, 16384), `jc_ipackets` (Jc, число I, сумма, 127), `trailers_loss` (H, S через запятую, S4, ширина, «1 из N»). С `--key` — только то, что запись этого ключа ломает или ухудшает, exit 2 (отказ); без — весь набор (подхват: предупреждения), exit 0. Слова — `ga_amn_limits_reason` / `ga_amn_limits_warnings` (ключи `amnezia_err_*` / `amnezia_warn_*`) |

Правила генерации (Amnezia 5.0.2 + наши для 1.x/2.0):
- 1.0: `Jc` 4–6, `Jmin 10`, `Jmax 50`, `S1`, `S2` случайные 15–150 с `S1+56 ≠ S2`, `H1–H4` случайные
  попарно различные из `[5, 2^31)`, без `I`, без `S3/S4`; `PersistentKeepalive 25`.
- 1.5: как 1.0 + `I1` по умолчанию — универсальный (KEENETIC-I1, с 1.0.13; `default_i1()`): один тег
  `<b 0x{ID}8580…>` — DNS-ответ icloud.com, как у приложения, а ID транзакции — 2 случайных байта (`secrets`), свой у
  каждого профиля. Прежнее умолчание приложения `<r 2><b 0x8580…>` (`LEGACY_DEFAULT_I1`) KeeneticOS 5.1 не принимает
  («invalid I1 value»: пакет должен начинаться с `<b>`), а `<b …>` первым понимают модуль ядра, amneziawg-go, Keenetic,
  GL.iNet и OpenWrt. Пресет `quic` — `I1` в виде QUIC Initial (`<b 0xc…>` + `<r …>`, не менялся; начинается с `<b`, но
  на Keenetic вживую не проверен), пресет `router` = 1.0 без `I`.
- 2.0: + `S3`, `S4` случайные 15–64/**12–24**, `H1–H4` — диапазоны `a-b` шириной 1000–5000, попарно
  непересекающиеся, `I1` как в 1.5. Размеры `148+S1`, `92+S2`, `64+S3`, `32+S4` попарно различны.
  Почему S4 ≤ 40 (MTU-01, с 1.0.12): внешний пакет данных = MTU туннеля + 80 (IPv6: 40 + UDP 8 + WireGuard 32; у IPv4 —
  60) + S4, а путь мобильных сетей и PPPoE ≈1400 байт; при MTU клиента 1280 это 1400 − 80 − 1280 = 40. Прежние 15–150
  давали пакет до 1510 байт: больше пути хоть на байт — и скачивание почти стоит (вживую 08.10: S4 = 142 на пути 1456 —
  0,0 Мбит/с). С 1.0.13 (S4-MARGIN, решение владельца 29.10) генератор и `profile-fix-s4` берут S4 из 12–24
  (`S4_GEN_RANGE`): пакет не больше 1280 + 80 + 24 = 1384, запас 16 байт до бюджета; 12 — умолчание приложения и минимум
  при защите заголовков 3.x. Ручной предел (40) и порог риска `packet_risk` (S4 > 40) — прежние, выданные профили с
  S4 ≤ 40 риском не считаются. Уже выданные генератор не трогает — их чинит `profile-fix-s4` (S4-FIX, §4.7). 3.1 сверх MTU + 80 + S4
  ничего не добавляет: `ContentPaddingAddition` и `RandomTrailers` добивают пакет только до самого большого уже
  отправленного этому пиру (amneziawg-go `device/send.go`). С 1.0.12 (PARAM-06) класс пакетов данных `S4 + 32 + 16k` не
  совпадает по размеру с рукопожатием: `S1 + 116 − S4`, `S2 + 60 − S4`, `S3 − S4` не кратны 16 (`s4_size_class_free`) —
  иначе у подхваченных профилей приложения 4.8 с широкими H этот класс терялся.
- S4, который пишем мы (`profile-set-param`, PARAM-01, с 1.0.12), — не больше 40 при любой линии с S4: отказ
  `params_invalid` словами. Валидатор набора (`validate`) большой S4 не отвергает — его разбирает и подхват чужих
  контейнеров, и правка других параметров подхваченного профиля.
- 3.1: как у Amnezia по умолчанию: `S1–S4 = 12`, `H1–H4 = 1,2,3,4`, `HeaderProtectionKey` = свежий X25519
  (base64), `RekeyAfterTime 100-120`, `RekeyTimeout 3-7`, `RejectAfterTime 150-180`, `KeepaliveTimeout 5-15`,
  `MaxHandshakeAttempts 15-20`, `RandomTrailers on`, `DisableCookies on`, `I1` по умолчанию. Два отступления от
  установщика приложения с 1.0.13 (живые проверки 08.10, `AWG3_GEN`, `AWG3_GEN_KEEPALIVE`):
  `ContentPaddingAddition 10-100` (PARAM-04: с хвостами подтверждения TCP раздувались до ≈340–750 Б, при отдаче
  5 Мбит/с скачивание было ниже на 43–45 %; с 10-100 — 194 Б, как у 2.0, пакет не растёт; константа самого приложения,
  его установщик её не ставит) и `PersistentKeepalive 25` вместо `25-35` (PARAM-07: NAT на 30 с терял запись 7–10 раз
  за 11 минут простоя, при 25 — ни разу). Выданные профили не трогаются: их значения меняет только человек.
- Пределы того, что пишем мы (с 1.0.13, `limit_problems`, живые проверки 08.10): тайминги 3.1 и `ContentPaddingAddition` —
  коридор вокруг значений приложения (`AWG3_CORRIDORS`): `RekeyAfterTime` 60–120, `RekeyTimeout` 3–10,
  `RejectAfterTime` 150–180, `KeepaliveTimeout` 5–15, `MaxHandshakeAttempts` 10–30, `ContentPaddingAddition` 0–1400 (оба
  конца «a-b»; пусто — значение WireGuard, оно в коридоре). Внутри коридоров при любом сочетании — и «старый ключ — новый
  сервер» — новый ключ сессии запрашивается раньше, чем умирает прежний: `RekeyAfterTime.hi` 120 < `RejectAfterTime.lo`
  150 − `KeepaliveTimeout.lo` 15 − `RekeyTimeout.lo` 10 = 125 (связка `timing_order` ловит это и у значений вне
  коридора). Вживую вне коридора: `RekeyTimeout 0-7` — в 16,5 раза больше пакетов, `RekeyAfterTime 9999999999` —
  `awg setconf` отказывает, профиль не поднимается, `ContentPaddingAddition 70000` — модуль держит 4464 (u16). Всплеск
  мусора `Jc × Jmax` — до 16384 байт (PARAM-08: 122 × 1280 при отдаче 1 Мбит/с — рукопожатие 6,7 с); `Jc + число
  непустых I1–I5` — до 127 (PARAM-09: клиент на программе иначе не поднимается, «Protocol error»); при `RandomTrailers`
  — суммарная ширина H1–H3 у тех, чей S ≠ S4, до 2²⁰ (PARAM-05, #226: H1 шириной 2²⁸ при S1 ≠ S4 — 6,5 % потерь).
  Где: `validate --ipackets write` проверяет весь набор (так их держит генератор: установка и перегенерация);
  `profile-set-param` — записываемый ключ: его коридор всегда, связки — только если запись их ломает или ухудшает
  (отказ `params_invalid` словами с диапазоном, `ga_amn_limits_reason`); подхват (`import-adopt`) — предупреждение
  словами в `param_warnings`, не отказ.
- Валидатор: `H` попарно различны и (для 2.0) диапазоны не пересекаются; `Jmin < Jmax ≤ 1280`; `1 ≤ Jc ≤ 128`;
  `S1 ≤ 1132`, `S2 ≤ 1188`, `S1+56 ≠ S2`; при `HeaderProtectionKey` все `S ≥ 12`; для линейки 1.0 запрещены `I*`, `S3/S4`,
  диапазоны и 3.x-поля; для 2.0 запрещены 3.x-поля. В `write` (по умолчанию) — ещё пределы того, что пишем мы
  (выше), для всего набора; в `adopt` — нет.
- Теги I1–I5 (KMOD-02, с 1.0.12; сверено по исходникам 08.10.2026): модуль ядра amneziawg v3.1.20260906 знает
  `b c t r rc rd`, программа amneziawg-go v3.1.20260828 — `b t r rc rd d ds dz` (тега `<c>` у неё нет с v0.2.19).
  `r rc rd dz` — положительное число, `c t d ds` — без числа, `b` — `0x` и чётное число шестнадцатеричных цифр; размер
  пакета — по всем тегам (`c`, `t` — 4 байта, `d`, `ds` — 0), предел 1280; незнакомый обоим тег — отказ. Раньше
  `<rc -5>` (модуль падает, upstream #237), `<rc 99999>` (`awg show` зацикливается, #228) и `<x 1>` проходили.
  I-пакеты шлёт инициатор рукопожатия — **клиент**: у сервера I1–I5 закомментированы (`# I1 = …`, как у приложения) и
  до его интерфейса не доходят. Поэтому какие теги годятся, решают реализации у клиентов (приложение AmneziaVPN на
  телефонах — amneziawg-go; роутеры и Linux — модуль ядра), а **не движок сервера**:
  - **write** — то, что пишем мы: `profile-set-param` I1–I5 (SSH, бот, панель, MCP), генератор, перегенерация
    (`profile-regen`). Только теги, понятные обеим реализациям: `b t r rc rd`; `<c>` и `<d> <ds> <dz>` — отказ словами.
    С 1.0.13 (KEENETIC-I1) каждый из I1–I5 ещё и начинается с `<b 0x…>`: иначе отказ `b_first` словами — почему
    (KeeneticOS 5.1 не принимает пакет, который начинается не с `<b>`) и как исправить (начните с `<b 0x…>`).
  - **adopt** — подхват уже работающих конфигов (`import-adopt`). Годится тег любой реализации; тег, который понимает не
    каждая (`<c>`; `<d> <ds> <dz>`), и пакет, который начинается не с `<b>` (`b_first`, например прежнее умолчание
    `<r 2><b …>`), — **не отказ, а предупреждение словами** (`ipacket_warnings` в ответе): люди,
    которые подключаются сейчас, не должны после подхвата остаться без сервиса. Те же правила — у уже записанных I в
    `profile-set-param` другого ключа (подхваченный `<c>` в I1 не мешает поменять Jc; записать `<c>` снова — нельзя).
    Привязка старого ключа (`user-attach-key`) берёт из ссылки или `.conf` только ключи, I1–I5 не переносит.
- `MTU`: значение по умолчанию `1280` — стандарт goAmnezia (решение владельца 08.10: работает везде — мобильный
  интернет, PPPoE, хостеры, IPv6 внутри туннеля; на проводном интернете человек может поднять до 1320 — пакет
  1320 + 80 + S4 40 = 1440 проходит пути от 1440, это большинство хостеров), договор — **задать можно целое 1280–1500** и
  ничего кроме. 1280 — минимум: ниже у интерфейса нет IPv6, и у клиента с `AllowedIPs ::/0` (наш стандартный конфиг)
  `awg-quick up` падает на `ip -6 route add ::/0` — туннель не поднимается вовсе (живая проверка L-02б, 08.10: 1279,
  1240, 1200 — rc 2). 1500 — MTU обычного Ethernet, выше него пакет всё равно фрагментируется по пути. Проверка задания
  одна на весь продукт — `ga_amn_mtu_settable` (`lib/amnezia.sh`: ниже `GA_AMN_MTU_MIN` = 1280 — отказ словами
  `amnezia_err_mtu_low`, прочее — `ga_valid_mtu` из `lib/common.sh`, границы `GA_MTU_MIN` / `GA_MTU_MAX`, отказ
  `amnezia_err_mtu`; код `invalid_mtu`), и её спрашивают оба пути задания: `amnezia_profile_set_mtu` и
  `amnezia_profile_set_param MTU`; бот, панель, окно SSH и MCP (`param_types.mtu` в `lib/actions.json`: `minimum` 1280,
  отказ словами `below_minimum`) проверяют то же до вызова. Сборка `.conf` в `lib/export.sh` спрашивает только
  `ga_valid_mtu` (1200–1500, не прошло — берётся умолчание): записанный раньше MTU 1200–1279 (поставлен до 1.0.12 или
  подхвачен) уходит в ключи как есть — поднять `GA_MTU_MIN` до 1280 значит поменять место, которое повторяет быстрый
  путь панели (`user-key`, `lib/core_read.py`), это отдельной правкой со сверкой. Валидатор `lib/awgkeys.py`
  проверяет `MTU` только на формат (целое): своего диапазона у него нет
  сознательно — второй диапазон означал бы, что один и тот же MTU принимается
  одним путём и отвергается другим, а кроме того тот же валидатор разбирает
  конфиги подхваченных контейнеров приложения, и отвергать чужой рабочий
  контейнер из-за нашего договора мы не вправе.

### 4.3 `lib/docker.sh`

```
docker_ensure_installed            # apt docker.io (+ containerd), enable --now; podman → ошибка docker_unsupported_runtime
docker_ensure_network              # amnezia-dns-net, bridge amn0, 172.29.172.0/24 (как upstream prepare_host.sh)
docker_pull_pinned <key>           # по lib/upstream/images.json: repo@digest; повтор 3 раза; код pull_failed / rate_limit
docker_build <image_name> <dir>    # docker build -t <image_name> <dir> (без --pull, база уже по дайджесту)
docker_container_state <name>      # stdout: running|exited|missing|docker_unreachable
docker_run_awg <name> <port> <image_name>   # официальные флаги run_container.sh + docker network connect
docker_exec <name> <cmd…>          # docker exec -i <name> sh -c …
docker_read_file <name> <path>     # stdout содержимое (через cat; бинарное — docker cp)
docker_write_file <name> <path>    # stdin → файл в контейнере (docker cp через mktemp), режим 0600
docker_append_file <name> <path>   # stdin → дописать
docker_rm <name>                   # stop + rm -fv (образ не трогаем)
docker_start <name>                # docker start (сторож); код start_failed
docker_port_free <port> <udp|tcp>  # ss -lun / -ltn; 0 = свободен
docker_pull_ref <repo@sha256:…|repo:tag>   # pull готовой ссылки, три попытки; коды pull_failed / rate_limit (docker_pull_pinned зовёт её)
docker_container_image <name>      # stdout: Config.Image контейнера (у контейнеров приложения — имя собранного образа)
docker_image_repo_digest <ref>     # stdout: sha256-дайджест из RepoDigests локального образа (пусто — собран локально или нет)
docker_ps_names_ports              # docker ps --format '{{.Names}} {{.Ports}}' (регексп приложения Amnezia)
docker_api_pin [--probe]           # один раз за процесс: версия API демона из его сокета (GET /version),
                                   # запасной путь — из текста отказа; экспорт DOCKER_API_VERSION.
                                   # Заданную снаружи версию не трогает; в dry-run молчит, кроме --probe
ga_docker <аргументы docker>       # единственная точка вызова CLI внутри модулей (перед первым — docker_api_pin)
docker_daemon_alive                # демон отвечает? GET /_ping через сокет, иначе docker version
docker_diag_json                   # stdout JSON {installed, reachable, client_version, client_api,
                                   # daemon_version, daemon_api, daemon_min_api, api_pinned, api_source,
                                   # mismatch, restart_required, stale, stale_reason, binary_version,
                                   # binary_build, daemon_build, stale_since, binary_label, daemon_label} — уходит в quality.docker_info
docker_stale_probe [--probe]       # DK-02: rc 0 — служба старая: бинарь на диске (dockerd --version, без него —
                                   # версия пакета dpkg) другой версии или сборки, чем работающий демон (GET /version:
                                   # Version, GitCommit); бинарь или демон не узнать — по рассогласованию API. Причина —
                                   # GA_DOCKER_STALE_REASON = version | build | api; версии — GA_DOCKER_BIN_*, GA_DOCKER_DAEMON_*.
                                   # Одна версия и сборка — не старая, даже при рассогласовании API. Один раз за процесс
docker_stale_json [--probe]        # stdout поле docker_stale: null | {reason, binary_version, binary_build, daemon_version,
                                   # daemon_build, client_api, daemon_api, since (когда заменён бинарь, ISO), binary_label,
                                   # daemon_label (что показать человеку: версия, при одной версии — сборка)}
docker_label <версия> <сборка> <другая версия>   # версия, а если версии совпадают — сборка («29.1.3 → 29.1.3» ни о чём)
docker_stale_reset                 # забыть всё о демоне (кэш /version, своё закрепление API, бинарь, вердикт)
docker_pin_refresh                 # долгоживущему процессу (SSH-меню): демон сменился без него — всё о демоне заново
docker_pkg_busy                    # rc 0 — сейчас работает apt/dpkg (fuser на замках dpkg, pgrep dpkg)
docker_daemon_restart              # systemctl restart docker + ожидание ответа демона (GOAMNEZIA_DOCKER_RESTART_WAIT, 90 с);
                                   # коды docker_restart_failed | docker_restart_timeout; в dry-run — только «DRY: systemctl restart docker»
```

**Старая служба Docker (DK-02).** Ubuntu обновляет пакет `docker.io` автообновлением, а службу не
перезапускает (debconf `docker.io/restart`, по умолчанию «нет»): обновление безопасности не действует,
пока службу не перезапустят. Сторож (`amnezia_watch_tick` → `ga_amn_docker_stale_watch`) перезапускает её
сам, когда сходится всё: служба старая (`docker_stale_probe`), с прошлой попытки — своей или кнопкой —
прошло ≥ 6 ч (`GA_AMN_DOCKER_RETRY`), apt/dpkg не работает, к VPN никто не подключён (у всех пиров всех
профилей с работающим контейнером, подхваченных тоже, последнее рукопожатие старше 5 минут —
`GA_AMN_ONLINE_WINDOW`; `awg show` не ответил — «не знаем», ждём) и замок продукта свободен (без ожидания).
Тогда: событие `docker_restarting` → `amnezia_docker_restart watch` (служба, затем каждый профиль тем же
путём, что после перезапуска профиля — `ga_amn_wait_up`: интерфейс, порт, изоляция, соседи, goWARP) →
событие `docker_restarted {ok, duration_s, daemon_before, daemon_after, daemon_before_label, daemon_after_label,
binary_version, restarted[], failed[], code, reason, command, retry_hours}` и запись `docker-restart` в журнал действий. Люди подключены — одно
событие `docker_stale` на обнаружение (подпись `docker_stale_sig`), дальше ждём молча. Память —
`LOCK_DIR/docker-stale.json` (`notified`, `attempt_at`, `attempt_sig`, `attempt_by`, `attempt_ok`,
`attempt_code`); перезагрузка её стирает — и сама лечит службу. Стенд: `GOAMNEZIA_DOCKERD_VERSION`
(что печатает `dockerd --version`), `GOAMNEZIA_DOCKERD_SINCE`, `GOAMNEZIA_DOCKER_VERSION_JSON_AFTER` (ответ
демона после перезапуска в dry-run; файла нет — демон не поднялся), `GOAMNEZIA_DPKG_BUSY=1`.

**`docker_unreachable` ≠ `missing`.** «Демон не отвечает» и «контейнера нет» требуют разных
действий, и смешивать их нельзя: на сервере с автообновлениями пакет `docker.io` обновляется
без перезапуска демона, клиент начинает просить API новее, чем демон понимает, и раньше все
живые контейнеры выглядели пропавшими (сторож звал восстанавливать их из бэкапа, тик лимитов
отчитывался «успех» с нулями). В состоянии `docker_unreachable`: `amnezia_profile_status`
отдаёт его как есть, сторож не «чинит» и шлёт одно событие `docker_unreachable` на весь обход,
`users_quota_tick` возвращает ошибку с кодом `docker_unreachable`, мутирующие операции
(`profile-port`, `profile-regen`, `profile-set-param`, `profile-set-subnet`, `state_sync`,
`state_restore`, `core-update`, `upstream-update`, `upstream-rollback`, `import-reconcile`)
отказываются тем же кодом до первого изменения — у обновлений это значит ДО резервной копии
и до попыток загрузки образа: минута ожидания с неверным кодом в конце лечения не подсказывает.
Тестовые крючки: `GOAMNEZIA_DOCKER_VERSION_JSON` (файл с ответом демона на
`/version`), `GOAMNEZIA_DOCKER_SOCK` (путь к сокету).
`GOAMNEZIA_DRY_RUN=1`: каждая функция печатает в stderr `DRY: <команда>` и возвращает 0
(`docker_container_state` → `missing`, `docker_read_file` → пусто). Это нужно для тестов на macOS.
Стенд с «контейнерами»: `GOAMNEZIA_CONTAINER_FS_DIR` и `GOAMNEZIA_DOCKER_PS_FIXTURE` (§1) —
`docker_read_file` читает файлы из каталога-фикстуры, `docker_container_state` отвечает `running`
(`exited` при файле `.state`), `docker_start` снимает `.state`, `docker_ps_names_ports` печатает
сохранённый `docker ps`; `ga_docker_fs_path <name> <path>` — общий помощник (пустой без фикстуры).

### 4.4 `lib/amnezia.sh`

```
amnezia_profile_install <profile> [port] [preset] [subnet]   # §6.1 плана; JSON-сводка в stdout при успехе; subnet —
                                                 #   своя подсеть (правила profile-subnet), иначе по умолчанию
                                                     #   три записи ключей сервера в state/ (priv, pub, psk) — каждая с проверкой кода (state_failed):
                                                     #   провал priv вылезет позже и громко, а незаписанные pub и psk уедут в тишину — восстановление
                                                     #   состояния отсутствующие файлы просто пропускает, и профиль поднимется без той самой пары,
                                                     #   что лежит у клиентов
amnezia_profile_remove <profile>
amnezia_profile_restart <profile>
amnezia_profile_set_port <profile> <port> [согласие] # пересоздать контейнер из state/, ключи не трогать; у профиля
                                                     # с людьми приложения Amnezia без ключа на сервере — только
                                                     # с согласием = <profile>, иначе adopted_keyless (ACC-2)
ga_profile_keyless_count <profile>                   # stdout: сколько людей профиля без ключа на сервере
amnezia_profile_status <profile>                     # stdout JSON {profile, container, state, port, line, peers, image_tag, image_digest, …,
                                                     #   subnet_fixed — подсеть не сменить: подхваченный старый контейнер на wg0 (SUBNET-01),
                                                     #   packet_risk — ga_amn_packet_risk (S4-FIX, с 1.0.12): объект или null}
ga_amn_s4_risky <S4>                                 # rc 0 — такой S4 при стандартном MTU 1280 выводит пакет данных 1280 + 80 + S4 за бюджет 1400,
                                                     #   то есть S4 > 40 (MTU-01). Арифметика bash, без python: та же формула, что packet_risk в
                                                     #   lib/awgkeys.py (сверка — tests/test_awgkeys.py). Константы GA_AMN_MTU_STANDARD=1280,
                                                     #   GA_AMN_PACKET_OVERHEAD=80, GA_AMN_PACKET_BUDGET=1400, GA_AMN_S4_SAFE_MAX=40
ga_amn_packet_risk <profile>                         # stdout {packet, budget, s4, safe_s4, mtu, keyless} или null — риск пакета данных профиля при
                                                     #   стандартном MTU (выбранный человеком MTU для проводной сети не в счёт, решение владельца 08.10);
                                                     #   keyless — люди приложения Amnezia без ключа на сервере (ga_profile_keyless_count). Только чтение
                                                     #   config.json и, когда риск есть, users.json; Docker не спрашивается
ga_amn_s4_audience <profile>                         # stdout {subscription, page, key, app}: имена людей профиля по тому, что им делать после смены S4
                                                     #   (и любого параметра — profile-set-param, PARAM-11, с 1.0.13)
                                                     #   (каждый в одном списке): app — ключ без приватной части на сервере (подхвачен у приложения);
                                                     #   subscription — подписку забирало приложение всеми линиями (state.json auto_fetches > 0: all.yaml/all.txt/mihomo.yaml, не браузер, не curl/wget, не awgNN.* и не all.vpn; R-1, N-1); page — живая страница подключения с
                                                     #   этим профилем; key — остальные с ключом на сервере
amnezia_profile_fix_s4 <profile> [согласие]          # S4-FIX (с 1.0.12): риска нет — s4_safe (rc 2; у линий без S4 — тоже s4_safe, другими словами);
                                                     #   люди приложения без ключа на сервере — только с согласием = <profile>, иначе adopted_keyless до
                                                     #   всякого изменения (ACC-2); новый S4 — awgkeys.py fix-s4 (12–24, попарно различные итоговые размеры,
                                                     #   PARAM-06); применяется _amnezia_profile_set_param_locked S4 (GA_AMN_PARAM_QUIET=1: свой журнал
                                                     #   profile-fix-s4 «<p> S4 old->new … keyless=N [adopted_confirm=<p>]»). stdout JSON {profile, line, s4_old,
                                                     #   s4, packet_old, packet, budget, restarted, engine, keyless, update_hours, subscription[], page[], key[],
                                                     #   app[], summary, steps[{kind: restart|start|subscription|page|key|app|nobody, text}]}
ga_amn_mtu_check <profile> <mtu>                     # stdout {mtu, s4, packet, warnings[]} — awgkeys.py mtu-check: что такой MTU сломает наверняка
ga_amn_mtu_warn_lines <json mtu-check>               # stdout — по строке слов на предупреждение (amnezia_mtu_warn_path)
ga_amn_mtu_settable <mtu>                            # rc 0 — такой MTU можно задать: целое 1280–1500; ниже 1280 — invalid_mtu
                                                     #   словами amnezia_err_mtu_low (туннель не поднимется у клиентов с IPv6,
                                                     #   L-02б), прочее — invalid_mtu amnezia_err_mtu (rc 2)
amnezia_profile_params <profile>                     # stdout JSON params
amnezia_profile_regen <profile> [preset]             # новые params, новый серверный конфиг, пиры перевыпустить (users_reissue_profile)
amnezia_profile_param_list <profile>                 # stdout JSON [{key, value, scope: server|client}] — все параметры ЛИНЕЙКИ-ОСНОВАНИЯ профиля (ga_profile_line_base,
                                                     #   см. ниже) с текущими значениями; один список для SSH-меню, бота и панели
amnezia_profile_set_param <profile> <key> <value>    # один параметр как в настройках приложения Amnezia. Ключ — из белого списка param_key, значение — как pattern param_value
                                                     #   (пусто = убрать). Проверка: config.json с подставленным значением → awg_params_validate по ЛИНЕЙКЕ-ОСНОВАНИЮ профиля
                                                     #   (ga_profile_line_base: так awg1 переходит между 1.0 и 1.5, добавляя/убирая I1, но S3/S4 и 3.x-поля в него не пройдут,
                                                     #   а подхваченный из приложения awg2 с 3.x-конфигом правится в 3.1), новая фактическая линейка —
                                                     #   detect-line, пишется в config.json вместе с params. Ошибки: invalid_param, invalid_param_value, params_invalid (rc 2,
                                                     #   первая причина валидатора — в сообщении).
                                                     #   серверный параметр (S1–S4, H1–H4, HeaderProtectionKey, RandomTrailers, DisableCookies): переписать конфиг, restart контейнера
                                                     #   (только он перечитывает [Interface]), state_sync, reissue_required=true
                                                     #   клиентский (Jc, Jmin, Jmax, I1–I5, MTU, PersistentKeepalive, тайминги, ContentPaddingAddition): config.json, конфиг контейнера
                                                     #   тоже переписывается (из него читают приложение и import-scan) и применяется syncconf без разрыва сессий, reissue_required=false
                                                     #   stdout JSON {profile, key, value, old_value, scope, line, reissue_required, restarted, engine,
                                                     #   clients_update, audience, update_hours, summary, steps}
                                                     #   С 1.0.13 (KEENETIC-I1-N, N4): у параметра-ключа (HeaderProtectionKey) value и old_value — «***»
                                                     #   (пустое — пусто), в строках для людей (итог, param_rolled_back, param_apply_failed, message
                                                     #   моста) — «задан (скрыт)»: ga_amn_param_shown. Журналы панели и MCP пишут value «***».
                                                     #   С 1.0.12: I1–I5 (KMOD-02) — записываемое значение по правилу write (только b t r rc rd), уже записанные —
                                                     #   по правилу adopt; отказ params_invalid словами (ga_amn_ipacket_reason). Движок профиля (ga_amn_engine,
                                                     #   один раз, только у работающего) решает применение (KMOD-01). На модуле ядра (kernel, unknown)
                                                     #   клиентский параметр, который живёт строкой в [Interface] сервера (GA_AMN_IFACE_MASK_PARAMS: Jc, Jmin, Jmax,
                                                     #   ContentPaddingAddition, Rekey*, RejectAfterTime, KeepaliveTimeout, MaxHandshakeAttempts), применяется
                                                     #   рестартом контейнера (amnezia_syncconf по пометке), restarted=true; I1–I5 (у сервера закомментированы),
                                                     #   PersistentKeepalive и MTU — без рестарта на любом движке. Серверный параметр — рестарт, как прежде,
                                                     #   restarted=true. Мост поднимает restarted в корень ответа рядом с reissue_required
                                                     #   С 1.0.12 (PARAM-01): S4 — не больше 40 (ga_amn_s4_risky), иначе params_invalid словами
                                                     #   (amnezia_err_s4_budget: «Задайте S4 до 40»); проверяется только записываемый S4 — у подхваченного
                                                     #   с большим S4 другие параметры меняются. GA_AMN_PARAM_QUIET=1 — шаг другого действия
                                                     #   (profile-fix-s4): без своей строки журнала и слов человеку
                                                     #   С 1.0.13 (живые проверки 08.10):
                                                     #   • пределы того, что пишем мы (§4.2, ga_amn_limits_reason → awgkeys.py limits --key): коридор
                                                     #     записываемого тайминга 3.1 / ContentPaddingAddition — всегда; связка таймингов, Jc × Jmax,
                                                     #     Jc + число I, хвосты 3.1 + разные S + широкие H — если запись их ломает или ухудшает; отказ
                                                     #     params_invalid словами с диапазоном (amnezia_err_corridor, _timing_order, _junk_burst,
                                                     #     _jc_ipackets, _trailers_loss), rc 2, ничего не записано;
                                                     #   • PARAM-10: применение не удалось (amnezia_write_server_conf, рестарт и ga_amn_wait_up,
                                                     #     amnezia_syncconf) — _ga_amn_param_undo: прежняя запись профиля в config.json целиком и прежний
                                                     #     awg0.conf (копия в state/ и в контейнер), работавший контейнер — перезапуск и подъём (не
                                                     #     записался конфиг, а интерфейс жив — без перезапуска; Docker поднял без опубликованного порта —
                                                     #     пересоздание из копии, как у «Перезапустить»); отказ param_rolled_back «<K> = <v> не
                                                     #     применился: <причина>. Вернул прежнее значение (<old>)…» или param_apply_failed (вернуть или
                                                     #     поднять не вышло — «поднимите контейнер … заново»), rc 1; журнал «<p> <K> failed step=<conf|up|
                                                     #     sync> code=<код> rolled_back=yes|no»;
                                                     #   • PARAM-11: ответ — ещё clients_update (значение изменилось: выданным ключам нужен новый конфиг,
                                                     #     и серверным, и клиентским параметрам), audience {subscription, page, key, app}
                                                     #     (ga_amn_s4_audience), update_hours, summary (одна фраза: серверный — «Старые конфиги профиля
                                                     #     больше не подключатся: подписки обновятся сами, выданные ключи vpn:// и .conf — выдать
                                                     #     заново»; клиентский — «Выданные ключи работают со старым значением, новое — только с новым
                                                     #     конфигом: …»; людей нет — amnezia_s4_step_nobody), steps[{kind: restart|start|subscription|
                                                     #     page|key|app|nobody, text}] (_ga_amn_param_steps: слова «Исправить S4» у серверного,
                                                     #     amnezia_param_cli_step_* у клиентского). То же значение — clients_update=false, шагов нет.
                                                     #     Людей определить не удалось — шаг unknown (amnezia_param_step_unknown: общая
                                                     #     фраза для всех), summary — общая, не amnezia_s4_step_nobody (проверяльщик 15.10)
ga_amn_limits_reason <line> <json после> <key> <old> # stdout — слова о первой беде пределов при записи ключа (пусто — бед нет); §4.2 «Пределы».
                                                     #   Код 1 — проверка сама упала (awgkeys.py, jq): слова amnezia_err_limits_check «не удалось
                                                     #   проверить значение — <причина>», profile-set-param отказывает params_failed, ничего не пишет.
                                                     #   Набор и прежнее значение — через stdin, не argv
ga_amn_limits_warnings <line> <json>                 # stdout — по строке слов на каждую беду всего набора (подхват: предупреждения)
amnezia_profile_set_subnet <profile> <cidr> [согласие] # cidr как pattern subnet (частный диапазон, /20–/24), адрес выровнен по маске (10.8.2.0/22 → invalid_subnet), нет пересечения
                                                     #   с занятым (ga_amn_taken_subnets: другие профили — записанная и фактическая подсеть, сети Docker, не подхваченные
                                                     #   контейнеры приложения; subnet_overlap, в тексте свободная подсеть), не та же подсеть (invalid_subnet); подхваченный
                                                     #   legacy-профиль на wg0 — subnet_legacy, а если его подсеть пересекается с другим профилем, в тексте выход: какой
                                                     #   профиль перенести и на какую свободную подсеть (ga_amn_subnet_legacy_msg, SUBNET-01); профиль с людьми приложения
                                                     #   без ключа на сервере — только с согласием = <profile>, иначе adopted_keyless до всякого изменения (как перенос порта, ACC-2);
                                                     #   state_sync живого контейнера → users_renumber_profile → subnet в config.json → docker rm/run с тем же портом →
                                                     #   конфиг сервера, clientsTable, start.sh (в нём подсеть в iptables) → state_restore → wait_up → state_sync
                                                     #   stdout JSON {profile, subnet, old_subnet, renumbered, peers: [{name, old_ip, ip}]}
amnezia_profile_set_mtu <profile> <mtu>              # ga_amn_mtu_settable: 1280–1500 (invalid_mtu, rc 2) → config.json params.MTU строкой → конфиг сервера (строка MTU) и
                                                     #   живой интерфейс (ip link set, без перезапуска) — с 29.09.2026 (WRP-02); контейнера нет — только копия в state/;
                                                     #   клиентские конфиги — при новых выдачах; перевыпуск не нужен; stdout пуст. Стандарт — 1280 (решение
                                                     #   владельца 08.10): его ставят все новые профили (gen-params), перегенерация оставляет выбранный
                                                     #   человеком, подхват не трогает. Предупреждения — у моста profile-mtu (ga_amn_mtu_check), не отказ
                                                     #   тот же ga_valid_mtu спрашивает и profile-set-param с ключом MTU — проверка одна на все пути (§4.2, «MTU»)
amnezia_write_server_conf <profile> [state]          # собрать server-conf из config.json + users.json и записать в контейнер + state/ (state — только в state/)
                                                     #   MTU профиля — строкой MTU в [Interface] и сразу на живой интерфейс (ga_amn_mtu_live); MTU в настройках нет —
                                                     #   строка MTU живого конфига сохраняется (чужую не стираем; убрали MTU сами через set-param "" — уходит);
                                                     #   MTU интерфейса warp goWARP PRO живёт в /opt/warp/warp.conf — его продукт только переносит
                                                     #   пиров читает ga_users_load ДО всякой записи: битый users.json — отказ users_corrupt, а не
                                                     #   конфиг без единого пира (§4.1). То же у clientstable_write, profile_status, profile_remove
                                                     #   и предупреждения о чужих пирах: не прочитали своих — молчим, а не объявляем чужим каждый свой
amnezia_syncconf <profile> [engine]                  # применить конфиг контейнера к живому интерфейсу (KMOD-01, с 1.0.12). engine — движок профиля
                                                     #   (ga_amn_engine; не передан — спрашивается: один docker exec). program — ровно команда приложения:
                                                     #   awg syncconf awg0 <(awg-quick strip …). kernel и unknown — <(awg-quick strip … | awk GA_AMN_SYNC_AWK):
                                                     #   из [Interface] только PrivateKey, ListenPort, FwMark и все [Peer] (маскировка на живом интерфейсе модуля —
                                                     #   зависание сервера с 1 vCPU или kernel panic, upstream #253, #140). Если запись конфига в этом действии
                                                     #   поменяла маскировку [Interface] (amnezia_write_server_conf / amnezia_state_restore сравнивают строки
                                                     #   ga_amn_mask_lines до и после → ga_amn_mask_mark) — на kernel/unknown вместо syncconf рестарт контейнера
                                                     #   и ga_amn_wait_up (amnezia_kmod_mask_restart: клиенты переподключатся сами, обычно за 15–25 секунд);
                                                     #   кого перезапустил последний вызов — GA_AMN_SYNC_RESTARTED, движок — GA_AMN_SYNC_ENGINE.
                                                     #   Других записей на живой интерфейс (awg set, setconf) в продукте нет: люди, массовые действия,
                                                     #   подхват и сверка, перевыпуск, «Поднять заново», goWARP — все через этот вызов
ga_amn_engine <profile>                              # stdout kernel | program | unknown — фактический движок профиля, один docker exec: ip -d link show dev awg0
ga_amn_engine_of <container> <iface>                 #   (amneziawg/wireguard → kernel, tun → program), запасом sysfs (DEVTYPE= в uevent, tun_flags); не ответил — unknown.
                                                     #   Legacy amnezia-awg (образ amnezia-wg, wg0): wg-quick там прежний WireGuard — tun или wireguard, не amneziawg.
                                                     #   Стенд (GOAMNEZIA_DRY_RUN=1): ответ — файл .iplink в GOAMNEZIA_CONTAINER_FS_DIR/<контейнер>/, нет файла — program
ga_amn_kmod_json                                     # stdout {loaded, version, patched, warn} — модуль ядра amneziawg на хосте (А3, только чтение):
                                                     #   /sys/module/amneziawg/version (GOAMNEZIA_SYS_MODULE_DIR — стенд), patched — метка +kaskad-k31 / +goamnezia-k31,
                                                     #   warn — загружен без метки, а есть профиль 3.1; ничего не ставит и не перезапускает
ga_amn_mss_cmd <iface>                               # stdout — команда sh для контейнера: подгонка TCP MSS (MSS clamping, этап Б, решение владельца 08.10).
                                                     #   mangle FORWARD по TCP SYN в обе стороны: из туннеля (-i iface) --set-mss = MTU интерфейса − 40
                                                     #   (iptables) и − 60 (ip6tables, если он есть в контейнере): маршрут такого пакета — наружу через eth0
                                                     #   с MTU 1500, по PMTU тут не выйдет; в туннель (-o iface) --clamp-mss-to-pmtu. Форма -C … || -A …,
                                                     #   прежнее значение --set-mss после смены MTU снимается; ядро MSS только уменьшает. POSIX (её же несёт
                                                     #   start.sh, а goWARP проверяет его sh -n); первая строка — комментарий со словом TCPMSS
ga_amn_start_sh_mss <text> <iface>                   # stdout start.sh с блоком «# --- goamnezia-mss BEGIN/END ---» перед блоком goWARP, строкой
                                                     #   tail -f /dev/null или первой строкой exit (там же, куда свой блок ставит goWARP), иначе в конец;
                                                     #   прежний блок — нынешним; MSS-меток или блока goWARP без пары — текст как есть
ga_amn_start_sh_ours <text> <iface> on|off           # stdout start.sh СВОЕГО профиля: строка изоляции + блок MSS + блок движка (ga_kmod_start_sh_engine,
                                                     #   §4.4a). Один путь для шаблона нового профиля (ga_amn_render_start_sh) и правки на месте
                                                     #   (ga_amn_isolation_sync_start_sh: правка копии state/ и контейнера, блок goWARP перед записью — из
                                                     #   самого контейнера, WRP-05; что правили — GA_AMN_START_SH_CHANGED=1)
ga_amn_mss_apply <profile> [quiet]                   # правила MSS в работающий контейнер (docker exec, идемпотентно): ga_amn_isolation_apply — если start.sh
                                                     #   только что получил блок (обновление: подъём, тик сторожа, миграция) или профиль подхвачен у приложения
                                                     #   (его start.sh не трогаем: правило ставят служба изоляции со старта контейнера и тик);
                                                     #   ga_amn_mtu_live — вместе со сменой MTU на лету
ga_amn_ipacket_reason <json params> [write|adopt]    # stdout — слова о первой беде I1–I5 (awgkeys.py ipackets --mode) или пусто
ga_amn_ipacket_warnings <json params>                # stdout — по строке слов на тег I1–I5, который понимает не каждая реализация (подхват), или пусто
ga_amn_i1_universal <profile>                        # KEENETIC-I1 (с 1.0.13): I1 ровно прежнее умолчание → универсальный путём profile-set-param
                                                     #   (клиентский, без рестарта); stdout — имя профиля, если заменён; всегда rc 0; пропуск —
                                                     #   строка-предупреждение и журнал profile-i1-universal-skipped (KEENETIC-I1-R)
ga_amn_i1_universal_all                              # то же по всем профилям (шаг миграции _migrate_i1_universal, под замком конфига)
amnezia_show <profile>                               # stdout JSON: [{pubkey, endpoint, allowed_ips, latest_handshake_ts, rx, tx}]
amnezia_clientstable_write <profile>                 # из users.json в официальном формате
amnezia_state_sync <profile>                         # контейнер → state/<container>/ (conf, clientsTable, *.key, start.sh, /opt/warp и /opt/warp-tools/usr goWARP, если есть)
                                                     #   конфиг интерфейса ложится в копию под awg0.conf (у legacy — из wg0.conf); ключа сервера
                                                     #   файлом у контейнера нет — берётся из PrivateKey конфига (ga_amn_state_canon, ACC-4)
                                                     #   подмена каталога — три перемещения, и КАЖДОЕ проверяется: сорвалось второе — прежняя копия
                                                     #   возвращается на место и отдаётся state_failed. Иначе единственная хостовая копия приватного
                                                     #   ключа сервера остаётся под именем с суффиксом .old, каталога состояния нет, а ответ — ноль
amnezia_state_restore <profile>                      # state/ → контейнер (конфиг уходит под именем интерфейса контейнера: awg0.conf / wg0.conf; goWARP — warp/ и
                                                     #   warp-tools/usr/, инструменты исполняемыми; файл engine — выбранный движок, §4.4a)
ga_amn_wait_up <profile>                             # сюда сходятся все пути подъёма: awg show + порт опубликован → изоляция клиентов → соседи профиля
                                                     #   (ga_amn_neighbours_up, WRP-01): контейнеры в его сети (NetworkMode=container:…) — в нынешнее пространство
                                                     #   (ссылка на нынешний ID и чужое пространство → docker restart; на прежний ID из реестра state/.netdeps
                                                     #   или его хранилища /var/lib/goamnezia-netdeps после «Удалить всё» (WRP-06) →
                                                     #   пересоздание из своего docker inspect через Docker API с NetworkMode=container:<контейнер профиля>),
                                                     #   затем сторож goWARP PRO gowarp-awg-watch --once. То же — служба изоляции по событию старта и сторож тиком
ga_amn_container_rm <profile>                        # снос контейнера профиля перед пересозданием: сначала /opt/warp и /opt/warp-tools goWARP из самого контейнера
                                                     #   в state/ (ga_amn_warp_refresh: свежий ключ goWARP, у остановленного — через docker cp), затем docker_rm
                                                     #   (он запоминает ID, если в сети контейнера живут соседи)
amnezia_scan_containers                              # stdout JSON: найденные amnezia-* контейнеры с портами (регексп Amnezia)
amnezia_core_update <profile> <repo@sha256:…|repo:tag> [tag] [digest]   # PLAN §6.6: state_sync живого → build нашего контекста с новым FROM → docker rm/run с прежним портом →
                                                     #   config.json image/image_tag/image_digest/core_updated_at → state_restore (конфиг, clientsTable, ключи, start.sh, /opt/warp) →
                                                     #   (start.sh из шаблона нового релиза получает блок WARP-MANAGER goWARP из прежнего — ga_amn_start_sh_carry_warp) →
                                                     #   wait_up (awg show + порт) → state_sync. Ключи сервера и пиров не меняются. Образ должен быть уже вытянут (docker_pull_ref).
                                                     #   stdout JSON {profile, container, image_tag, image_digest, old_tag, old_digest}; коды state_missing, build_failed, run_failed, container_not_up
amnezia_watch_tick                                   # сторож goamnezia-watch.timer: по включённым профилям — missing → событие container_missing; exited → docker start;
                                                     #   running, но awg show молчит (ga_amn_iface_alive) → docker restart; событие container_restarted {profile, container, action, ok}
                                                     #   docker_unreachable → НИЧЕГО не чиним и не зовём восстановление: одно событие docker_unreachable
                                                     #   {profiles[], api_client, api_daemon} на весь обход (память по ключу «docker»)
                                                     #   в очередь бота не чаще раза в час на одно и то же (память /run/goamnezia/watch.json)
                                                     #   соседи в сети пропавшего контейнера, которого нет ни в одном реестре (WRP-06): строка в журнал и
                                                     #   запись netdeps_dangling в журнал действий ОДИН раз на набор имён (state/.netdeps/dangling.reported);
                                                     #   сами контейнеры не трогаются; событием боту — когда у бота будет свой текст для него
                                                     #   при живом демоне — старая ли служба Docker (DK-02, ga_amn_docker_stale_watch, см. lib/docker.sh)
                                                     #   stdout JSON {checked, profiles[], events, docker_unreachable,
                                                     #   docker_stale: none|waiting|backoff|busy|restarted|failed}
amnezia_docker_restart [watch|manual]                # DK-02: служба Docker (docker_daemon_restart) и подъём профилей (ждём контейнер до
                                                     #   GOAMNEZIA_DOCKER_UP_WAIT, 30 с, иначе docker start; ga_amn_wait_up; порт без публикации —
                                                     #   пересоздание из копии); stdout JSON {ok, by, duration_s, restarted[], failed[{profile, code,
                                                     #   message}], daemon_before, daemon_after, daemon_before_label, daemon_after_label, binary_version, stale};
                                                     #   коды docker_restart_failed,
                                                     #   docker_restart_timeout, docker_still_stale, restart_failed. watch — события боту до и после;
                                                     #   попытка любого вида — в память сторожа (правило 6 ч). Под ga_with_config_lock
ga_amn_docker_stale_status                           # stdout поле status.docker_stale: docker_stale_json + last_attempt {at, ok, by, code} | null,
                                                     #   retry_after (ISO; пусто — сторож может хоть на ближайшем тике)
ga_amn_vpn_online                                    # stdout: сколько пиров на связи (рукопожатие моложе 5 мин) у профилей с работающим
                                                     #   контейнером; rc 1 — awg show не ответил
```
Интерфейс, утилита и путь конфига профиля внутри контейнера — `ga_amn_iface <profile>` (`awg0`,
у подхваченного legacy `amnezia-awg` — `wg0`), `ga_amn_tool` (`awg` | `wg`), `ga_amn_conf_path`
(`/opt/amnezia/awg/<iface>.conf`); в `state/` наша копия конфига всегда `awg0.conf`. Приводит её
`ga_amn_state_canon <каталог> <iface> [live]` — синхронизация, восстановление (и `profile-repair`), обновление
ядра и миграция 0.4.6 (`ga_amn_state_canon_all`); копию прежних версий под `wg0.conf` они принимают и
переименовывают, а сверка ключей и выдача адресов читают её через `ga_amn_state_conf` без переименования.
Восстановление из бэкапа кладёт `wg0.conf` архива прежней версии поверх живой `awg0.conf` (находка ACC-4).
`ga_amn_image_key <profile>` — ключ `images.json` по `image` профиля (`amnezia-wg` у legacy).
Порядок установки — как у клиента Amnezia (PLAN §2.3), только образ по дайджесту и `start.sh` наш.
Проверка после старта: `awg show awg0` внутри контейнера отвечает и порт слушается.
Подсеть при установке — из таблицы §2, а если она занята — первая свободная `10.8.N.0/24`
(`ga_amn_pick_subnet`). **Занятое** (`ga_amn_taken_subnets <profile> [виды]`, SUBNET-01): `net` — сети Docker
(amnezia-dns-net, мост docker0); `profile` — другие профили config.json: записанная подсеть, а у установленного ещё
и фактическая — по строке Address его копии конфига в `state/`, если она другая или записи нет (подхваченный профиль
без поля `subnet` или с устаревшим раньше свободной считал свою же сеть); `app` — контейнеры AmneziaWG приложения
Amnezia на машине, ещё не подхваченные (их подсеть займёт профиль при подхвате). Новый профиль — мастер, Ленивый Pro,
SSH, панель, бот, мост — никогда не встаёт на занятое; своя подсеть, пересекающаяся с ним, — `subnet_overlap` со
свободной подсетью в тексте.

**Пересечение подсетей профилей** (SUBNET-01). Приложение Amnezia ставит любой свой контейнер AmneziaWG — и старый
`amnezia-awg` (wg0), и `amnezia-awg2` — на одну `10.8.1.0/24` (`protocols::wireguard::defaultSubnetAddress`).
Подхват (`import-adopt`) берёт такой контейнер как есть — отказывает он только на сетях Docker; совпавшая с профилем
подсеть видна в `import-scan` (`subnet_overlap`) и в ответе подхвата. `ga_amn_subnet_overlaps` → JSON по записи на
установленный профиль, чья подсеть пересекается с другим: `{profile, subnet, with, fixed, move: {profile, subnet,
keyless} | null}`; он же — `subnet_overlaps` в `status` (без brief) и `profiles-status`. Выход (`move`): остаётся
профиль, которому подсеть не сменить (`fixed` — старый на wg0), иначе тот, у кого больше людей с ключами только из
приложения, при равенстве — поставленный раньше; остальные переносятся на свободные подсети (`ga_amn_pick_subnet`).
`ga_amn_subnet_overlap_text` — то же словами для SSH и сообщений моста.

Хелперы, общие для
модулей: `ga_cidr_parse <cidr>` (stdout `база размер префикс` целыми; код 1 — не CIDR, 2 — адрес не
выровнен), `ga_int2ip`, `ga_amn_cidr_net`, `ga_amn_conf_subnet`, `ga_amn_state_subnet <profile>`,
`ga_amn_subnet_check <profile> <cidr> [виды]`, `ga_amn_subnet_fixed <profile>`, `ga_amn_param_scope <key>`
(`server`|`client`), `ga_amn_param_keys <line>`, `ga_amn_validate_params <line> <json>`
(зовёт `awg_params_validate`, без `lib/awg_params.sh` — `awgkeys.py validate` напрямую),
`ga_amn_ensure_server_ip`.

**Откуда берётся линейка профиля.** Не из его идентификатора: контейнер `amnezia-awg2`,
поставленный приложением Amnezia, бывает какой угодно линейки, и после `import-adopt` имя
профиля о ней ничего не говорит.

```
ga_profile_line <profile>        # ФАКТИЧЕСКАЯ линейка (1.0|1.5|2.0|3.1). Источник правды — поле line в config.json;
                                 #   пусто (старая установка) → detect-line по .params; нет и параметров → ga_profile_default
ga_profile_line_base <profile>   # линейка, по которой параметры проверяются и генерируются: семейство фактической —
                                 #   1.0 и 1.5 → 1.5 (профиль ходит между ними по наличию I1), 2.0 → 2.0, 3.1 → 3.1.
                                 #   Выше семейства не поднимаемся: линейки несовместимы (§2)
ga_profile_default <p> line      # умолчание ТОЛЬКО для свежей установки (awg1 → 1.5, awg2 → 2.0, awg3 → 3.1)
ga_migrate_profile_line          # старые установки без поля line: вывести по .params и записать обратно; идемпотентна,
                                 #   зовётся из auto_migrate_state под замком конфига. stdout: строки «профиль=линейка»
```

Поле `line` пишут `amnezia_profile_install` (по `detect-line` от сгенерированных параметров) и
`import_adopt` (по конфигу внутри подхваченного контейнера); его же обновляют
`amnezia_profile_set_param` и `amnezia_profile_regen`. Спрашивают линейку через `ga_profile_line`
`amnezia_profile_status`, `amnezia_write_server_conf` и весь `lib/export.sh` (ключ обязан быть в
линейке контейнера, иначе он не соединится); через `ga_profile_line_base` —
`amnezia_profile_param_list`, `amnezia_profile_set_param`, `amnezia_profile_regen`. То же
семейство держат бот (`line_base` в `bot.py`) и панель (`LINE_FAMILY` в `admin-web/server.py`).

### 4.4a `lib/kmod.sh` — модуль ядра AmneziaWG по выбору (этап Б, docs/KMOD_PLAN.md)

**Решение владельца:** по умолчанию каждый профиль работает программой `amneziawg-go` в Docker; модуль ядра — только
по выбору человека, с согласием цифрами (SSH) или нажатием (панель, бот), и **продукт никогда не переключает сам**:
ни сторож, ни обновление, ни установка профиля. Модуль — upstream `amneziawg-linux-kernel-module` тега
`GA_KMOD_TAG` (`v3.1.20260906`, архив закреплён `GA_KMOD_SHA256_PIN`) с набором исправлений Kaskad `kaskad-k31.1`
(`lib/upstream/kmod/`, GPL-2.0, `PATCHES.md`; #218 — только если заголовки ядра объявляют новую сигнатуру
`setup_udp_tunnel_sock`, KMOD-04). Метка в памяти — `GA_KMOD_LABEL` = `3.1.20260906+kaskad-k31.1`; модуль соседа
Kaskad с меткой `+kaskad-k31`/`+goamnezia-k31` — годный, пересобирать его не нужно. Правило modprobe.d
`install amneziawg /bin/false` продукт не пишет (ломает соседа), тест #253 вживую не делается никогда.

Что на хосте:

| путь | что | чьё |
|---|---|---|
| `state/kmod.json` (0600) | `{want: kernel\|program, want_at, want_by, installed, owner, version, kernels[], pkgs[], p218, modules_load, hooks, at, selftest: {ok, kernel, label, at, cases[], reason}, failed: {kernel, step, code, reason, left, boot, log, at}, fallback: {kernel, reason, code, boot, at}, rebuild: {label, kernel, at, ok}, removed_at}` | продукт |
| `state/<контейнер>/engine`, `/opt/amnezia/awg/engine` в контейнере (0600) | `kernel` \| `program` — выбранный движок профиля | продукт |
| `start.sh` профиля | блок `# --- goamnezia-engine BEGIN/END ---` вместо строки приложения `awg-quick up` (`GA_KMOD_UP_LINE`): `engine` = `program` — `amneziawg-go awg0` + `awg setconf` + адреса, MTU, маршруты (модуль в памяти иначе подхватил бы профиль сам, KMOD-06); иначе — `awg-quick up`, как у приложения. POSIX, без строк `exit`/`tail -f` и без строк с `iptables`/`tail` в начале — goWARP и изоляция вставляют свои строки как прежде. У контейнера, подхваченного у приложения, это единственная правка его `start.sh` (иначе выбор «программа» при загруженном модуле не держится) | продукт |
| `/usr/src/amneziawg-3.1.20260906/` (+ метка `.goamnezia`), дерево DKMS | исходники с патчами; `dkms.conf` без `AUTOINSTALL` и `REMAKE_INITRD` (KMOD-09) | продукт (чужие исходники не трогаются — `kmod_foreign_src`) |
| `/etc/kernel/{postinst.d,header_postinst.d}/zz-goamnezia-amneziawg` | пересборка под новое ядро при установке его заголовков (с #218 по заголовкам), журнал `/var/log/goamnezia-kmod-hook.log`; ошибка не валит apt | продукт |
| `/etc/modules-load.d/amneziawg.conf` | загрузка при старте — только если файла не было | продукт (`modules_load=true`) |
| `/run/goamnezia/kmod-progress.json`, `kmod-engines.json` | ход долгого действия (с pid); кэш движков профилей на 10 минут в пределах загрузки | продукт |
| `state/.kmod-copies/<время>/` (0700, файлы 0600) | копия `state/` перед каждым переводом, последние 3 | продукт |

```
ga_kmod_check_json [plan]               # stdout {ok, code, reason, arch, virt, secure_boot, disk_free_mb, kernel, kernel_ok, headers {installed,
                                        #   candidate}, module {loaded, version, installed, ko_version, owner}, kaskad, reuse, label, plan}. Отказ
                                        #   одной причиной по порядку: not_apt, arch, container (LXC/OpenVZ…), no_proc_modules, secure_boot,
                                        #   kernel_old (< 5.4), disk (< 600 МБ; не при reuse), no_headers (нет кандидата linux-headers-<ядро>).
                                        #   plan (только plan): {missing[], install[] (apt-get -s), size_mb, kernel (apt потянет новое ядро), gcc,
                                        #   headers_meta {name, installed, kernel, size_mb} | null}; reuse — модуль с годной меткой уже на диске
ga_kmod_install <rebuild on|off> <headers_meta on|off>
                                        # под замком конфига: ожидание apt (GA_KMOD_APT_WAIT: мост 600 с, SSH 1800 с; строки хода раз в 30 с),
                                        #   пакеты (dkms gcc make patch gcc-NN linux-headers-<ядро>; метапакет заголовков — только если не тянет
                                        #   новое ядро или headers_meta=on, KMOD-05), исходники по sha256, патчи, dkms add/build/install, modprobe,
                                        #   метка, modules-load, хуки, самопроверка в netns одноразового контейнера (ga_kmod_selftest). Чужой
                                        #   стоковый модуль на диске — kmod_foreign; старый наш в памяти под профилями — перезагрузка модуля с
                                        #   переводом их через программу. Профили НЕ переводит (выбор — ga_kmod_engine_set); без файла engine им
                                        #   пишется выбор сервера (не выбирал — program). stdout {state, version, kernel, owner, reused, p218,
                                        #   packages, headers_meta_skipped, selftest, want, duration_s, reload, loaded, on_disk}.
                                        #   rebuild=on при модуле в памяти (KMODB-03, с 1.0.13) — новый модуль и в память, даже с той же
                                        #   меткой: профили на модуле — ga_kmod_engine_set program (копия state/, проверка людей), modprobe -r,
                                        #   загрузка, самопроверка, ga_kmod_engine_set kernel (reload=swapped); на модуле никого — выгрузка и
                                        #   загрузка без перезапусков (reloaded); модулем пользуется сосед Kaskad — только на диск, новый
                                        #   загрузится при перезагрузке (neighbour, предупреждение словами). Итог словами — что в памяти
                                        #   (kmod_rebuilt_swapped / _reloaded / _neighbour); loaded — метка в памяти, on_disk — у файла модуля
ga_kmod_engine_set <kernel|program>     # под замком: kernel — ga_kmod_ready (модуль под ядро, загружен, с меткой, свежая самопроверка), выбор
                                        #   сервера want, копия state/; по одному в порядке awg2 → awg1 → awg3 (только профили на awg0): файл
                                        #   engine и блок в start.sh, docker restart + ga_amn_wait_up, проверка ip -d link (kmod_engine_mismatch),
                                        #   до 3 минут — вернётся ли хоть один из тех, кто был на связи (рукопожатие моложе 3 минут;
                                        #   kmod_clients_lost). Сбой — откат ВСЕГО сервера на прежний движок и прежний выбор, rc 1.
                                        #   stdout {engine, want, profiles: [{profile, from, to, restarted, alive_before, returned, return_s,
                                        #   note: "" | already | no_clients | not_running:<state> | lost}], rolled_back, copies}.
                                        #   Ключи людей и сервера не меняются: тот же awg0.conf, тот же порт
ga_kmod_remove [wipe]                   # профили на программу (wipe — без перевода: «Удалить всё»), сосед Kaskad пользуется модулем — модуль
                                        #   и исходники остаются (kept_for_neighbour); иначе выгрузка (kmod_in_use), dkms remove --all, исходники
                                        #   с нашей меткой. Наши хуки и modules-load (если наши) — всегда. Пакеты не удаляются: stdout {removed,
                                        #   kept_for_neighbour, packages[], command: "apt remove …"}
ga_kmod_status_json                     # stdout {loaded, version, patched, warn (этап А), state, code, reason, kernel, want, installed,
                                        #   installed_version, owner, label, profiles {<p>: {engine, want}}, progress, selftest, failed, packages,
                                        #   removed_at}. removed_at (с 1.0.13, KMODB-01) — когда модуль удалили (kmod-remove), если с тех пор
                                        #   не ставили, иначе null: state=off у него — «удалён», а не «не ставился».
                                        #   state: off | ready | on | fallback (code: no_module_for_kernel | unpatched | not_loaded |
                                        #   profiles_on_program) | failed (code шага) | unpatched | busy (code — действие). reason — словами, с ядром
                                        #   («нет модуля для ядра 6.8.0-146 — профили работают программой; пересобрать?»). Дёшево: /sys, файлы,
                                        #   кэш движков; модуля в памяти нет — program без похода в Docker
ga_kmod_engine_now <p> [live]           # kernel | program | unknown | stopped; ga_kmod_profiles_enrich <json> [live] — engine, engine_want в profiles[]
ga_kmod_engine_write <p> <kernel|program> # файл engine (контейнер и копия) и блок в start.sh — без перезапуска; ga_kmod_engine_seed <p> — новому
                                        #   профилю выбор сервера до первого старта (ga_amn_write_start_sh)
ga_kmod_start_sh_engine <text>          # stdout start.sh с блоком движка вместо строки awg-quick up (повтор — тот же текст); rc 2 — не узнан
ga_kmod_selftest                        # stdout {ok, kernel, label, at, cases: [{id, profile, line, client, ok, detail}], reason}: на каждую
                                        #   линию профилей — интерфейс модуля и программы в netns gst0/gst1 контейнера из образа профиля
                                        #   (--privileged --network none, timeout 300), ключи и параметры профиля, рукопожатие и ping
ga_kmod_watch                           # тик сторожа (watch-tick → kmod): профилям без файла engine — выбор сервера (без перезапуска);
                                        #   выбран модуль, а работает программа — предупреждение и запись kmod_fallback в журнал действий раз на
                                        #   загрузку и ядро; выбрана программа, а профиль на модуле — строкой раз на загрузку (сменится при
                                        #   перезапуске); модуль наш, а метка не этой версии — пересборка службой goamnezia-kmod-rebuild
                                        #   (systemd-run, мост kmod-install --rebuild=on от имени таймера). stdout {want, kernel, state, seeded[],
                                        #   fallback, drift[], rebuild} | null
ga_kmod_after_update                    # шаг миграции (_migrate_kmod): хуки — текстом этой версии, файлы engine — выбору; движок не меняет
ga_kmod_screen                          # SSH «Обслуживание → 9) Ускорение AmneziaWG (модуль ядра)»: 1 Включить, 2 Вернуть программу,
                                        #   3 Пересобрать, 4 Удалить; ga_kmod_card_line <p> — строка «Движок» карточки профиля;
                                        #   ga_kmod_dash_warn — строка шапки (fallback, failed). С 1.0.13: после «Удалить» — «Модуль ядра удалён
                                        #   <когда> …» (kmod_removed_line, KMODB-01); строку по профилю и итог «✓ Профили работают: …» печатает
                                        #   только сам перевод ga_kmod_engine_set, экран — лишь отказ целиком (KMODB-02); переводы шагом
                                        #   пересборки — без своего итога (GA_KMOD_ENGINE_QUIET=1)
```

Коды отказов: `kmod_unsupported` (в `message` — причина проверки хоста одной фразой), `kmod_busy`, `apt_locked`,
`apt_install_failed`, `kmod_download_failed`, `kmod_sha_mismatch`, `kmod_unpack_failed`, `kmod_foreign_src`,
`kmod_patch_sha`, `kmod_patch_failed`, `kmod_build_failed` (хвост журнала сборки — в `failed.log`, KMOD-07),
`kmod_label`, `kmod_foreign`, `kmod_load_failed`, `kmod_in_use`, `kmod_selftest_failed`, `kmod_not_ready`,
`kmod_bad_engine`, `kmod_start_sh_missing`, `kmod_start_sh_unknown`, `kmod_engine_mismatch`, `kmod_clients_lost`,
`kmod_write_failed`, `kmod_tmp_failed`, `state_failed`, на стенде без подменённых путей хоста — `kmod_dry_unsafe`.
Неудача установки помнится в `failed` для этого ядра (state `failed`), модуль после неудачной самопроверки выгружается,
если на нём нет профилей. Стенд (`GOAMNEZIA_DRY_RUN=1`) трогает хост только через `GOAMNEZIA_KMOD_ETC`,
`GOAMNEZIA_KMOD_SRC_ROOT`, `GOAMNEZIA_KMOD_LIBMOD` (и `…_PROC`, `…_EFIVARS`, `…_DKMS_TREE`, `GOAMNEZIA_SYS_MODULE_DIR`).

**Соседи.** Kaskad на той же машине: модуль с годной меткой берётся как есть (`reuse`), его исходники и DKMS не
трогаются, при удалении модуль остаётся, если Kaskad им пользуется (`awg-kmod.json` или его интерфейсы `kgw-awg*`,
`kawg*`). goWARP: блок движка и блок MSS не задевают его блок в `start.sh` и проходят `sh -n` (`tests/test_kmodb_dryrun.sh`).

### 4.5 `lib/users.sh`

```
users_add <name> [label] [profiles_csv] [quota_gb] [days]   # пиры во всех перечисленных (по умолчанию установленных) профилях
users_del <name>
users_del_bulk_preview <names_csv> <prefix> <state>   # кого задевает выбор (ровно один из трёх непустой): stdout {mode, value,
                                 #   count, max, too_many, selected [{name, label}], missing}; только чтение, без замка
users_del_bulk <confirm> <names_csv> <prefix> <state>   # «Удалить сразу несколько» (до 100): confirm сверяется с выбранным под
                                 #   замком; порядок users_del (выключить → применить → стереть) пачками по 10; stdout {requested,
                                 #   count, deleted, kept, partial, failed, mode, value}; GA_DB_MATCHED — выбрано сейчас (у отказов
                                 #   confirm_mismatch, nothing_selected, too_many)
users_list                       # stdout JSON без приватных ключей и psk
users_show <name>                # stdout JSON без приватных ключей; у каждого пира has_key (есть ли приватная часть)
users_name_suggest [lang] [exclude]   # «🎲 Случайное имя» (docs/MODES_047.md «Имена ключей»): вариант «прилагательное +
                                 #   столица» из lib/names_capitals.json (ga_names_file; GOAMNEZIA_NAMES_JSON — стенды): stdout
                                 #   {id, label, lang} — id латиницей, label на языке lang (ru|en, пусто — LANG_CODE, язык установки);
                                 #   не предлагает id и подписи (ru и en) заведённых людей и exclude. Только чтение, без замка.
                                 #   Отказы: names_missing (набора нет или он не разбирается), names_exhausted, users_corrupt.
                                 #   Пользователи — SSH (_user_add_ask_name: пустой Enter или «?») и мост name-suggest
users_rename <name> <label>
users_enable <name> / users_disable <name>
users_set_quota <name> <gb> / users_set_expiry <name> <days>
users_set_endpoint_ip <name> <ip|"">   # свой адрес сервера в ключах (IPv4 машины; пусто — общий); stdout {name, endpoint_ip,
                                 #   previous_endpoint_ip, endpoint, resend_required, domain_endpoint}; отказы invalid_ip
                                 #   (и 0.0.0.0), address_not_here, addresses_unknown — машина своих внешних
                                 #   адресов не видит (действие user-set-address); те же три кода у разового
                                 #   --endpoint выдачи ключа (ga_export_key_host_check, §4.6)
users_add_peer <name> <profile> / users_reissue_peer <name> <profile>   # замена ключа пира ОДНИМ шагом под общим замком: адрес прежний, при
                                 #   сорвавшемся применении возвращается прежний ключ (действие user-reissue-peer)
users_del_peer <name> <profile>
users_attach_key <name> <profile|""> <ссылка vpn:// или текст .conf>
                                 # привязка старого ключа подхваченного пира (§ пользователи): ключ третьим аргументом функции,
                                 #   во внешние процессы — только stdin (awgkeys.py client-key) и окружение (jq); профиль можно
                                 #   не называть — найдётся по совпавшему пиру; stdout {name, profile, line}; отказы no_config,
                                 #   config_unreadable (rc 2), peer_not_found, key_mismatch (чужой ключ; ключ другого пользователя —
                                 #   с его именем в сообщении), server_mismatch, already_has_key; контейнер не трогается
users_alloc_ip <profile>         # stdout свободный IP подсети профиля (маска /20–/24; для /24 это .1–.254, сетевой и последний адрес не раздаются;
                                 #   переиспользует освободившиеся; занятые — из users.json и AllowedIPs хостовой копии конфига)
users_apply_profile <profile>    # переписать серверный конфиг и clientsTable из users.json, syncconf, state_sync
users_reissue_profile <profile>  # новые ключи всех пиров профиля (после profile-regen)
users_renumber_profile <profile> <cidr>   # новые IP всем пирам профиля по порядку записей users.json (.1, .2, …) в новой подсети; ключи не меняются.
                                 #   Только users.json (шаг amnezia_profile_set_subnet; вызванная отдельно, требует users_apply_profile следом).
                                 #   stdout JSON [{name, old_ip, ip}]; ошибки invalid_subnet, subnet_full
ga_set_endpoint_host <host>      # в users.sh: домен или IPv4 (invalid_host), пусто = по умолчанию (в Pro домен сервера, иначе server_ip) → config.json endpoint_host; действует на новые выдачи
users_quota_tick                 # таймер goamnezia-quota (действие quota-tick): awg show → дельты → лимиты/сроки → users_disable + уведомление боту
                                 #   (файл-очередь /run/goamnezia/notify/ через ga_notify из lib/common.sh: {ts, event, user, …})
                                 #   демон Docker недоступен → ошибка docker_unreachable, а НЕ success с нулями: молчаливо неработающие лимиты недопустимы
                                 #   применение не дошло до контейнера → quota_apply_failed: выключение откатывается (трафик остаётся посчитанным),
                                 #   событие владельцу НЕ уходит. «Лимит исчерпан, доступ закрыт» объявляем только после удавшегося применения,
                                 #   а откат нужен, чтобы следующий тик снова увидел нарушителя и повторил попытку
```
**Откат мутации доводится до контейнера.** `users_add` и `users_add_peer` при отказе применения
снимают запись из `users.json` **и переприменяют профили**: применение — цепочка из четырёх
шагов, и оборваться она могла уже после того, как конфиг с новым пиром лёг в контейнер. Иначе в
живом контейнере остаётся авторизованный пир, приватного ключа от которого не существует нигде,
а его адрес считается занятым и следующий пользователь получает следующий.
`GA_EXPECT_DROP` (глушилка предупреждения о чужих пирах) объявляется `local` в каждой функции,
которая её ставит: bash отдаёт локальную переменную и вызываемым функциям, а на выходе
восстанавливает прежнее значение сам — на любой ветке, включая ранний `return`. Глобальная
переменная после одного неудавшегося удаления делала «своими» ключи покойника до конца жизни
процесса, а в SSH-меню это один процесс на всю сессию.
Все мутации — под `ga_with_config_lock`. `users_list` — без замка. `ga_notify <event> [json]`
(`lib/common.sh`) — общая очередь событий бота: `quota_exceeded`, `expired` (users.sh),
`container_restarted`, `container_missing`, `docker_unreachable`, `docker_stale`, `docker_restarting`,
`docker_restarted` (сторож), `upstream_available` (проверка обновлений). Очередь ограничена `GOAMNEZIA_NOTIFY_MAX` файлами (по умолчанию 200,
`ga_notify_trim`): читателя может не быть вовсе, а таймеры пишут дальше — лишними выбрасываются
самые старые события.

### 4.6 `lib/export.sh`

```
export_conf <name> <profile> [ep]            # stdout текст .conf; ep — разовый хост в Endpoint (ниже), пусто — как обычно
export_vpn <name> <profile> [ep]             # stdout vpn://…; с ep подпись записи «<label> · AmneziaWG <line> · <хост>»
export_qr_png <text> <outfile>               # qrencode -o … -l L (для .conf) ; вернуть 0
export_qr_vpn_pngs <name> <profile> <outdir> [ep]   # чанки → PNG по одному на чанк, имена 01.png…; stdout список файлов
export_bundle <name> <profile> <format> [ep] # stdout JSON {conf, vpn, qr_conf_png_b64, qr_vpn_png_b64:[…], line, min_client,
                                             #   disabled, key_host, key_endpoint, key_host_override, file_name}
                                             #   file_name — имя .conf по ga_key_file_base (anna-2_0.conf, ≤15 знаков до
                                             #   .conf; с ep — то же): панель, бот и подписка называют файл так же (Н-2)
ga_export_key_hosts                          # stdout хосты, которые примет разовый ep, по одному в строке; без сети (ниже)
ga_export_key_host_check <host>              # stdout хост в нижнем регистре без точки в конце; отказы rc 2: invalid_ip,
                                             #   address_not_here (сообщение перечисляет допустимые), addresses_unknown
_ga_export_host_for <name> [ep]              # хост Endpoint этой выдачи: разовый (через проверку выше) или ga_export_endpoint_host <name>
export_key_choices_json <edge-status JSON>   # stdout JSON-массив для key_choices ответа edge-status (§4.7)
```
`min_client` — минимальная версия приложения Amnezia для линейки (1.5 → 4.8.8.3, 2.0 → 4.8.12.9, 3.1 → 5.0.1.5; стабильные выпуски, как в lib/clients.json).

**Ключ на другой адрес сервера — разовый Endpoint** (docs/EDGEIPS.md §3.4 «Разовый ключ на адрес»). Тот же
ключ — та же пара ключей и тот же порт профиля — с другим хостом внутри `Endpoint`: для человека, у которого не
открывается домен или молчит основной адрес. Произвольный хост не принимается — ключ на чужой адрес не
соединится, а выглядит настоящим. `ga_export_key_hosts` без похода в сеть печатает сначала домены —
`endpoint_host`, если там домен; домен Pro (`ga_export_pro_domain`); домен сайта `.domain` только при
`ga_site_domain_ready` (Let's Encrypt); в нижнем регистре, без точки в конце, без повторов, — затем IPv4:
`endpoint_host`, если там IP; `server_ip` из `config.json`; `ga_edge_list` (внешние IPv4 машины); без повторов,
`0.0.0.0` отброшен. `ga_export_key_host_check` отказывает (rc 2, `GA_ERR_*`) кодом `invalid_ip` — похоже на
IPv4, но адрес негодный или `0.0.0.0`, либо это вовсе не имя узла; `addresses_unknown` — спросили IP, которого
нет среди известных, а машина своих внешних адресов не видит (`ga_edge_list` нет или он пуст: NAT, нет `ip`);
`address_not_here` — такого адреса или домена у сервера нет. Разовый хост меняет хост только этого ключа и
побеждает всё: и закреплённый `endpoint_ip`, и домен, вписанный в `endpoint_host` (закреплённый адрес такому
домену уступает). Пустой `ep` — выдача байт в байт прежняя. `export_bundle` проверяет хост один раз до сборки
и отдаёт `key_host` (хост в этом ключе: разовый, иначе обычный с закреплённым адресом пользователя; пусто — не
определён), `key_endpoint` (`<key_host>:<порт профиля>`, пусто — хост или порт неизвестны) и
`key_host_override` (`true` — был разовый хост). Подпись `vpn://` (имя записи в приложении Amnezia) с разовым
хостом — `«<label> · AmneziaWG <line> · <хост>»` (`export_vpn_description_at`), без него прежняя, — две записи
одного человека в приложении различаются. **За пользователем ничего не сохраняется**: закрепить адрес на все
следующие ключи — `users_set_endpoint_ip` (§4.5, мост `user-set-address`). `export_key_choices_json` берёт
готовый JSON `edge-status` и печатает ровно то, что примет разовый хост (элементы — §4.7). Две оговорки:
сторож адресов (`lib/edgeips.sh` — таблица «Адреса сервера», тревога `dark`, число людей на адресе) считает
людей только по закреплённому `endpoint_ip`, а доменных — по A-записям, и разовые ключи ему не видны: кого
сторож должен считать на адресе, тому адрес закрепляют. Одна пара ключей — один пир: тот же ключ одновременно
с двух адресов (два устройства или две записи одного ключа на разные адреса) будет «прыгать» — у пира
WireGuard один endpoint; для одновременных устройств — отдельные пользователи.

### 4.6a `lib/preflight.sh` (PLAN §4.3.1)

```
preflight_port_owner <tcp|udp> <port>     # stdout JSON {state: "free"|"busy"|"closed_local", pid, process, unit, container, product, ours: bool}
preflight_modes                           # stdout JSON {lite: {available: true}, lite_site: {available, reasons: [..]}, pro: {available, reasons}, warnings: [..], ports: {tcp80, tcp443, udp443}}
preflight_render_modes <json>             # stderr: экран выбора режима с ✓/✗/⚠ и подсказками (для мастера и «Режим и сайт»)
preflight_open_local <port> <proto>       # ufw allow / iptables при согласии; в dry-run — DRY:; порт закрыт родным nftables
                                          #   (своя таблица) — отказ preflight_open_nft с командой nft, iptables не трогаем;
                                          #   после iptables — перепроверка (рядом nft со своим drop — тоже preflight_open_nft, SR-19)
preflight_guess_product <process> <unit> <container>   # stdout: gotelegram|govless|godns|amnezia|goamnezia|xui|unknown
```
Источники: `ss -ltnp`/`-lunp` (в dry-run — `GOAMNEZIA_SS_FIXTURE=<файл>` с сохранённым выводом),
`/proc/<pid>/cgroup` → `*.service`, `docker ps --format '{{.Names}} {{.Ports}}'` для `docker-proxy`,
`ufw status`, `iptables -S INPUT`/`nft list ruleset`. `ours=true` для `nginx` с нашим vhost
(`/etc/nginx/sites-enabled/goamnezia`) и контейнеров из `config.json` — не блокирует.
Мастер и `_mode_site_screen` зовут `preflight_modes` до показа выбора; `change-mode` на
недоступный режим → `ga_emit_json error … code=port_busy port=… proto=… process=… unit=…
container=… product=…` (или `code=closed_local`); `quality` включает `ports`.
Тест `tests/test_preflight_dryrun.sh` на фикстурах: свободно; занято чужим nginx с юнитом;
занято docker-proxy (контейнер `amnezia-awg2` — своё, не блокирует); занято `telemt`
(goTelegram — сообщение с именем продукта); UDP/443 занят — только предупреждение; ufw закрыт.

### 4.7 `install.sh`

Каркас `main()` из goTelegram: разбор `--action` до всего остального, `--selftest`, `--version`,
`--json`; `check_root`, `init_dirs`, `check_deps_present`; миграции `auto_migrate_state`
(`_ver_lt`, маркер `.migrated_<ver>`, `snapshot_preupgrade_state`; версия в `config.json` и маркер — только после
всех шагов, начатая миграция оставляет `.migrating_<ver>` и доделывается следующим вызовом — OPS-03; шаг 0.6.0
`_migrate_panel_legacy_key` (LZ-07) — старый постоянный ключ панели без `<файл ключа>.meta` удаляется, в аудит
`panel-legacy-key-removed` без ключа; идёт и в повторе шагов при той же версии; обновление,
у которого `bootstrap.sh` положил прежний код рядом в `.prev_code/`, при сбое миграции или проверки `status`
откатывается: код, данные из снимка preupgrade, юниты systemd, `.install-source`; отметка `.update_failed.json` →
поле `update_rollback` у `status`, событие боту `update_rolled_back` — `lib/update.sh`, OPS-02); авто-обновление бота/панели/MCP
из эталона (`cmp -s` → копия; бот и панель — только если уже установлены своими установщиками,
рабочая копия MCP `/opt/goamnezia-mcp` создаётся самим `install.sh`, симлинк
`/usr/local/bin/goamnezia-mcp` перенацеливается на неё); выбор языка; баннер; если не установлено или установка не
закончена (запись `.wizard`, §3.1) — мастер установки (конец ввода в нём и второй мастер — выход без меню);
цикл меню с `read -t 30` и различением таймаута/EOF.

Мастер первой установки (`install_wizard`): режим — экран `preflight_render_modes` (четыре пункта договора 0.6.0:
1 Ленивый Pro — сценарий §4.7b, 2 Pro, 3 Semi-Pro, 4 Lite; Enter — Lite; конец ввода на любом вопросе мастера — остановка без установки, данные ответы — в `.wizard` (§3.1), повторный запуск продолжает с того же вопроса (INST-01, INST-02); мастер на сервере один (`$LOCK_DIR/wizard.pid`); недоступные по портам не
выбираются, `5` повторяет проверку, `closed_local` → вопрос
«Открыть 80,443/tcp?» → `preflight_open_local`) → домен (Pro) → профили (галочки: `awg2` всегда,
`awg3`, `awg1`) → порт `awg2` (Lite: случайный, Pro: 443, можно поменять) → DNS → установка →
первый пользователь `admin` → сайт (`lite_site`/`pro`) → предложение поставить бота
(`goamnezia-bot/install_bot.sh --auto`, токен и ID через окружение; без ID — `GOAMNEZIA_ADMIN_PAIR=1` и сразу окно
привязки администратора сообщением `install_bot.sh --pair`, docs/BOT.md §4) и панель
(`admin-web/install_admin.sh --auto`, команда туннеля и место токена) → показать ключ 2.0 (QR
`.conf` в UTF8 + ссылка `vpn://`). Меню «Telegram-бот» и «Настройки → Веб-панель, админы бота, ИИ-агенты → Панель и
мини-апп»: установить/обновить,
статус, логи, перезапуск, `.env` без токена / токен только в терминал, удалить (docs/BOT.md §4,
docs/PANEL.md §4).

Диспетчер `action_dispatch`: имя действия должно быть в `lib/actions.json` (проверка через `jq`),
аргументы `--k=v` → переменные `ARG_k`, валидация по `param_types`; `readonly` → без замка;
остальные — `flock -w 30`. Ответ через `ga_emit_json`. Неизвестное действие → код `unknown_action`.

Правила диспетчера, на которых стоят бот, панель и MCP:
- **Отсутствующий и пустой аргумент — разные вещи.** `--value=` у `profile-set-param` значит
  «убрать параметр», `--host=` у `endpoint-set` — «по умолчанию» (в Pro домен сервера, иначе его IP), `--ip=` у `user-set-address` —
  «вернуть общий адрес», `--endpoint=` у `user-key` и `share-create` — «адрес как обычно» (ключ байт в байт
  тот же, что без аргумента). Шаблоны таких параметров в `actions.json` пропускают пустую строку явно
  (`^(…)?$`): MCP проверяет шаблон и на пустом значении, и без пустой альтернативы агент не мог
  вернуть Endpoint по умолчанию. `missing_arg` — только
  когда обязательный аргумент не передан вовсе; пустому подставляется `default` из `actions.json`,
  если он есть, иначе он уходит модулю как есть. Исключение — обязательный выбор из `enum` без пустой
  альтернативы и без `default`: пустое значение — `bad_arg` (SR-47).
- **Целые — десятичные, не длиннее 18 цифр.** Ведущие нули снимаются до границ и до модулей («0443» — это
  443, а не восьмеричное 291; в ответ уходит нормализованное число — BOT-8); длиннее 18 цифр — `bad_arg`
  (арифметика bash переполнилась бы). Длина и шаблон строковых параметров считаются в символах, а не в
  байтах локали процесса: службы (бот, панель, таймеры, страницы — в их юнитах `LANG` нет) зовут мост в
  локали C (SR-23). С 1.0.6 (L1-15 / TAIL-8) счёт не зависит и от наличия C.UTF-8: `ga_char_len` считает
  начальные байты UTF-8 в локали C, у шаблона «одна скобка на всё значение» (`^[…]{m,n}$` — `label`, `name`,
  `file`, `domain_input`…) верхняя граница уходит в предел длины в символах, а в ERE остаётся `{m,}`; тем же
  счётом — подписи людей и агентов в модулях (`ga_users_label_valid`, `ga_agents_label_valid`) и ячейки таблиц
  SSH (`ga_cell` режет по буквам, `ga_char_cut`). Свободный текст в ответе (подпись) —
  значением `@str:`, чтобы «@json:…» из ввода не превращалось в JSON (SR-38).
- **Язык ответа — на один вызов** (L-24, 0.5.0). Переменная окружения процесса моста `GOAMNEZIA_CALL_LANG=ru|en`
  переключает словарь (`message`, `hint`, слова отказов) только для этого вызова — каждый вызов моста отдельный
  процесс; нет её или значение не `ru`/`en` — язык установки, как раньше. `config.json`, `status.lang` (всегда
  язык установки) и SSH-меню не меняются; коды ответов от языка не зависят. Бот и панель ставят её по языку того,
  кто нажал кнопку (админ говорит с ботом по-английски на русском сервере — итоги по-английски); MCP не ставит.
  Частые чтения панели через общий снимок (docs/PANEL.md §5a) ставят её всегда: запросу без языка — язык установки
  (тот же ответ, что без переменной), чтобы он делил снимок со страницей на этом языке (PERF-08).
- **Выдача ключа — в журнал действий.** `user-key` (и SSH, и ключ первого пользователя в мастере) пишет
  строку `user-key` с именем, профилем, форматом и разовым адресом, без самого ключа (SR-03).
- **Коды ошибок модулей доходят до вызывающего.** Ветки зовут функции модулей через
  `_action_run <код-по-умолчанию> fn args…` (внутри `ga_capture`: stdout функции — в файл в
  текущей оболочке, а не `$( )`), поэтому в ответ попадают `code` и `message`, которые модуль
  положил в `GA_ERR_CODE`/`GA_ERR_MSG` (`ga_err`) или `GA_LAST_ERROR_CODE` (`ga_set_error`):
  `port_busy`, `profile_exists`, `foreign_container` (profile-install: контейнер с именем профиля
  уже есть, в config.json его нет, а в его конфиге есть `[Peer]` — установка удалила бы ключи людей;
  проверка до ключей и сборки и ещё раз после подъёма Docker), `user_not_found`, `params_invalid`, `subnet_overlap`,
  `param_rolled_back` и `param_apply_failed` (profile-set-param, с 1.0.13: значение не применилось — прежнее
  вернулось; или вернуть и поднять не вышло), `backup_not_found`… Обобщённый `*_failed` остаётся только когда модуль
  кода не назвал.
- **`user-attach-key` — секрет каналом.** Ключ человека (ссылка `vpn://` или текст `.conf`, до 64 КБ)
  мост читает только из stdin: у параметра `config` в `actions.json` стоит `stdin: true`, такой
  параметр не входит в список аргументов командной строки (`--config=…` → `bad_arg`), а терминал
  вместо канала — `no_config`. Бот, панель и MCP передают его так же; в аудит MCP — только длина.
  Ответ — JSON `users_attach_key` в корне (`name, profile, line`).
- `profile-set-param` кладёт `reissue_required` (булево) в корень ответа и весь JSON модуля в
  `result` (с 1.0.13 в корне ещё `clients_update`, а `message` дополняется фразой `result.summary` — кому нужен новый
  конфиг, §4.7a); `profile-subnet` — `result` с `peers`; `user-add` — в `result` карточку `users_show`
  (без ключей); `user-show` — `name`, `user` (карточка `users_show`), `key_host` и `key_host_gone`
  (разовый адрес, ниже); `user-key` — поля `export_bundle` в корне ответа, а не в `result` (MCP ищет QR
  именно там, §5), вместе с `key_host`, `key_endpoint`, `key_host_override`; `endpoint-set` — `endpoint_host` и `effective_host`; `user-set-address` — `endpoint_ip`,
  `previous_endpoint_ip`, `endpoint` (что теперь в ключах пользователя), `resend_required` (адрес в самом
  ключе изменился — выданный конфиг смотрит на прежний) и `domain_endpoint` (адрес, равный общему, пишется
  пусто — человек идёт за общим адресом, SR-49); `edge-status` (битый users.json — отказ `users_corrupt`, а не
  нули, SR-37) — `count`,
  `domain_endpoint`, `ips[]` (`ip`, `verdict`, `users`, `packets_15m`, `packets_1h`, `packets_day`,
  `silent_since`, `in_dns`), `keys_host` и `key_choices[]` (разовый адрес, ниже); `status` — `server_ip` из
  `config.json` (сеть — только если поле пусто), `endpoint_host`, `endpoint_effective` (что стоит в `Endpoint`
  ключей сейчас — та же функция, что их собирает), `endpoint_auto` (что встанет при пустом поле),
  `key_hosts` (разовый адрес, ниже) и `mode_port_mismatch`.
- **Ключ на другой адрес сервера — разовый Endpoint** (§4.6, docs/EDGEIPS.md §3.4 «Разовый ключ на
  адрес»). `user-key --name --profile [--format=all] [--endpoint=<хост>]` и `share-create --name --profile
  [--format=conf] [--endpoint=<хост>]`; тип параметра — `key_endpoint` (строка, тот же шаблон, что у
  `host`: домен или IPv4, пусто — «как обычно»; мусор отвергает ещё диспетчер — `bad_arg`). Тот же ключ —
  та же пара ключей и порт профиля, — меняется только хост в `Endpoint`; **за пользователем ничего не
  сохраняется** (закрепить адрес на все следующие ключи — `user-set-address`, поле `endpoint_ip`). Хост
  проверяет `ga_export_key_host_check`, отказы доходят через `_action_run` своими кодами: `invalid_ip`,
  `address_not_here` (сообщение перечисляет допустимые хосты), `addresses_unknown`. Ответ `user-key`:
  `key_host` — хост в этом ключе (разовый, иначе обычный с закреплённым адресом пользователя; пусто — не
  определён), `key_endpoint` — `<key_host>:<порт профиля>` (пусто — хост или порт неизвестны),
  `key_host_override` — `true`, когда был `--endpoint`. `share-create` проверяет хост ДО уборки и открытия
  порта; `share_create` получает его четвёртым аргументом, `ga_share_payload` — пятым; файл называется
  так же, как без адреса и как `file_name` у `user-key` (`ga_key_file_base`: `anna-2_0.<ext>`; адрес в имя не входит —
  имя туннеля ≤15 знаков, П-2), `endpoint` пишется в `meta.json`,
  приходит в ответе `share-create` (пустая строка — адрес как обычно) и в каждом элементе `share-list`, в
  аудит — ` endpoint=<хост>` (docs/SHARE.md). Варианты для экранов: `edge-status` отдаёт `keys_host` — общий
  адрес в ключах (без пользователя) — и `key_choices[]` (`export_key_choices_json`) — ровно то, что примет
  `--endpoint`: `{host, kind: "ip"|"domain", default (host == keys_host), verdict и users (вердикт сторожа и
  люди на адресе — только когда у машины два адреса и больше, иначе null), in_dns (из записи этого IP в
  `ips[]`, иначе null)}`; порядок — обычный адрес первым, затем домены, затем IP не в `dark`, `dark` — в
  конце; обычный адрес, которого нет среди допустимых, дописывается, чтобы он был в списке. `user-show`
  отдаёт `key_host` — обычный адрес в ключах этого человека (с закреплённым; без сети — адрес сервера из
  `config.json`) — и `key_host_gone` (`true` — закреплённого адреса на машине больше нет: обычная выдача
  отказывает `address_gone`, выход — разовый адрес); `status` — `key_hosts`, массив хостов, которые примет
  `--endpoint` (без сети). MCP: у `user_get_key` необязательный `endpoint`, `edge_status`, `user_show` и
  `server_status` несут поля выше; `share-create` агентам не публикуется (`mcp: null`). Выключенному пользователю
  `share-create` отказывает `user_disabled` (A3-5, 17.10; тот же код, что у `page-create`), а процесс раздачи
  (`lib/share_server.py --users <users.json>`, A4-2) на каждом GET и HEAD отвечает 404, пока пользователь выключен
  (без счёта скачивания и без бана), и снова отдаёт файл после включения, если срок не вышел (docs/SHARE.md §1).
- **`mode_port_mismatch` (`status`).** Второй и третий режим отличаются ровно одним: стоит
  AmneziaWG на UDP/443 или на своём порту. Мост порт НЕ двигает (это `profile-port`, и он рвёт
  связь всем выданным ключам), а в SSH-меню перенос предлагается отдельным вопросом, от которого
  человек вправе отказаться. Значит ярлык режима и факт могут разойтись — и тогда «Сайт и VPN на
  443» снаружи ничем не отличается от «VPN и сайт». Ярлык мы не переписываем (это выбор
  человека), но расхождение называем вслух: `true` в `status`, строка на главном экране
  (`dash_mode_port_mismatch`), предупреждение на экране «Режим и сайт» и сразу после перехода
  (`ms_mode_port_mismatch` / `ms_mode_port_missing_443`).
- `backup-list` отдаёт `backup_list_json` (`[{name, size, created}]`); `backup-restore` берёт
  `--file` как имя в `$BACKUP_DIR` и зовёт `restore_backup <путь> "" yes` — подтверждение уже дал
  вызывающий; `backup-create` возвращает `file` (имя) и `path`.
- Заглушек `not_implemented` в диспетчере больше нет; в модулях этот код тоже не остался.
- **Спринт 4 (§4.9–4.12).** `import-scan` (readonly) → `containers` (`import_scan`: полный разбор
  с `kind`, `line`, `subnet`, `peers`, `clients[]`, `adoptable`, `reason`, `suggested_profile`; без
  модуля — короткий `amnezia_scan_containers`) и `adoptable[]`; `import-adopt --container` →
  ответ `import_adopt` в корне (`profile, container, line, port, subnet, iface, image_tag,
  users_added, users[], peers, hint, snapshot` — каталог снимка контейнера до подхвата, `state_before`, `started` —
  запустился ли остановленный контейнер, `subnet_overlap` — с кем совпала подсеть и выход, тогда и `message` говорит это
  словами, SUBNET-01; подхват отмечает установку, как `profile-install`); `import-reconcile --profile [--apply=on|off]` → ответ
  `import_reconcile` в корне (`in_sync, new[], gone[], skipped[], applied, added, deactivated, users[]`),
  сообщение по исходу (`act_import_in_sync` / `act_import_diverged` / `act_import_reconciled`), а за ним — строки
  `skipped[].message` (пиры, которых не подхватить, — с причиной, KEENETIC-I1-N N2);
  `upstream-check` (readonly, не через `_action_run`: при `unreachable` ответ — ОДНА строка
  `{status: error, code: unreachable, profiles[] (текущие версии), updates_available: false}`;
  успех — JSON `upstream_check` в корне, `message` = его `hint`, рядом `hint_key` — как у `status.upstream`); `upstream-update --profile
  [--tag]`, `upstream-rollback --profile`, `core-update --profile` → JSON модуля в корне
  (`image_tag, image_digest, previous_tag …`); коды `unreachable`, `tag_not_found`,
  `already_current`, `no_previous`, `backup_failed`, `download_failed`, `scripts_failed`,
  `update_failed` (откат сделан), `update_failed_no_rollback` (откат НЕ сделан: прежний образ
  вернуть не по чему или возврат тоже не удался — контейнер не поднят, нужна рука человека),
  `rollback_failed`, `rollback_failed_no_container`, `already_managed`, `not_adoptable`,
  `unsupported_container` (контейнер приложения Amnezia, который goAmnezia не ведёт),
  `container_not_found`, `invalid_container`, `conf_read_failed`, `conf_parse_failed`,
  `subnet_overlap`, `container_missing` доходят через `_action_run`. Тики таймеров (`mcp: null`,
  зовутся юнитами `lib/timers.sh`): `quota-tick` → `{tick: quota, checked, users, disabled[]}`,
  `watch-tick` → `amnezia_watch_tick`, `stats-tick` → `stats_tick`, `upstream-tick` →
  `upstream_tick` (все — JSON модуля в корне). `traffic` дополнен `today`, `month`, `days[]` и
  `hours[]` — почасовые корзины `{hour: "YYYY-MM-DDTHH:00Z", rx, tx, active}` за последние 168 часов UTC по
  возрастанию (`active` — максимум за час числа людей «на связи», то же окно 180 с, что у «●»; пусто, пока
  сбора не было; HM-1) (`stats_summary`, без модуля — `null`/`[]`); `quality` — `timers[]` (`timers_status`, девять строк:
  свои пять и чужие четыре с `owned=false`).
- **Доступы агентов (`lib/agents.sh`, §4.8, §5.1).** `agent-list` (readonly) → `agents` (массив
  `agents_list` без секретов и без токенов, с флагами `require_token`, `weak`, `pin_ip`,
  `pinned_ip`) и `transport` (объект `agents_http_status`); `agent-add
  --label [--scope=read] [--days=30] [--allow-ips] [--port] [--pin-ip=off]`, `agent-show --id`
  (readonly), `agent-harden --id`, `agent-rotate --id` (destructive) → **карточка доступа в корне
  ответа** (`_action_emit_merged`: `id, label, scope, enabled, created, expires, port, allow_ips,
  last_used, last_ip, calls, require_token, token, pin_ip, pinned_ip, url, connect, via,
  cert_fingerprint_sha256, transport_enabled, reachable, hint, warning[, cert_hint][, weak_hint]`) —
  `url` содержит секрет, `token` — второй фактор, `connect` — готовая команда подключения с
  заголовком; действия помечены `secret: true`; `agent-pin --id --state=on|off` →
  `{id, pin_ip, pinned_ip}`; `agent-enable/disable --id` → `{id, enabled}`;
  `agent-del --id` (destructive) → `{id, deleted}`; `mcp-http-status` (readonly), `mcp-http-on
  [--port]`, `mcp-http-off` → объект статуса транспорта в корне (`enabled, mode, listen
  (nginx|public_port|grant_ports|none), bind, port, tls, http_port, public_port, domain,
  server_ip, base_url, via (domain|ip|port), cert_fingerprint_sha256, nginx_location, unit,
  unit_state (active|inactive|missing|dry), reachable, hint, agents, agents_active,
  agents_with_port, agents_weak`). Коды модуля доходят через `_action_run`: `needs_site` (lite без
  порта), `agent_not_found`, `invalid_agent_id`, `invalid_label`, `invalid_scope`, `invalid_days`,
  `invalid_ips`, `invalid_port`, `invalid_state`, `already_strong`, `port_busy`, `agents_corrupt`,
  `unit_failed`, `no_domain`, `no_server_ip`, `selfsigned_failed`, `nginx_conf_invalid`…;
  `bad_arg` для `scope`, `id`, `port`, `pin_ip`/`state` и `allow_ips` ловит ещё диспетчер по
  `param_types`. Все мутации пишут аудит именами действий только с `id=` — ни секрет, ни токен не
  попадают ни в аудит, ни в stderr.
- **Страница подключения, каталог приложений, зеркало сборок (`lib/pages.sh`, `lib/mirror.sh`,
  `lib/clients.json`, docs/CLIENTS.md §9, §4.13–4.14).** `clients-list [--platform] [--line]` (readonly) →
  `{checked, lines, platforms, apps[]}` каталога; `page-create --name --ttl [--profiles] [--mirror=on]` →
  `{id, url, user, created, expires, forever, ttl, profiles, mirror, imported_profiles, subscription}` — **`url` содержит секрет**
  (`secret: true`; `subscription` — `created` | `exists` | `off`: завела ли страница подписку, адреса подписки в ответе нет);
  `page-list [--name]` (readonly) → `{pages[], count}` без адресов, у страницы и `subscription` из её `meta.json` (у страниц,
  созданных до этого поля, — `null`); `page-url --id` →
  `{id, url}` (`secret: true`, аудит `page-url`); `page-revoke --id [--sub on|off] [--keys on|off]` → `{id, user, sub_rotated, sub_present, keys_reissued, keys_failed}` (`sub_present: null` при `sub=off`, `keys_*: null` при `keys=off`; `sub` и `keys` агентам MCP не публикуются — `"mcp": false`); `page-revoke-all [--name]`
  (destructive) → `{revoked}`; `mirror-list` (readonly) → `{max_file_mb, max_total_mb, used_mb, used_bytes,
  items[], count}`; `mirror-fetch --app --platform` → `{app, platform, state}` (`queued` на живом сервере,
  итог в dry-run); `mirror-remove --app --platform` (destructive) → `{app, platform}`. Коды модулей доходят
  через `_action_run`: `pages_need_domain`, `user_not_found`, `user_disabled`, `invalid_ttl`, `invalid_page_id`,
  `page_not_found`, `page_limit`, `invalid_profiles`, `peer_not_found`, `invalid_toggle`, `invalid_platform`,
  `invalid_line`, `catalog_missing`, `unknown_app`, `not_mirrorable`, `mirror_busy`, `mirror_no_space`,
  `mirror_too_big`, `mirror_unverified`, `mirror_fetch_failed`, `unit_failed`; `bad_arg` для `ttl`, `id`,
  `platform`, `line`, `mirror` ловит диспетчер по `param_types` (`page_id`, `ttl`, `page_profiles`, `app`,
  `platform`, `line`). Тики без своих таймеров: `watch-tick` дополняет ответ блоками `pages` (`pages_tick`:
  `{expired, active}`), `mirror` (`mirror_tick`: `{requests, started, deferred, checked, updates}`) и `disk`
  (`ga_disk_tick`, 1.0.6, TAIL-19: `{level, prev, notified}`; событие боту `disk_level` — только при смене уровня
  `ok`/`low`/`critical`, память — `/run/goamnezia/disk_level`, первый тик на здоровом диске молчит),
  `upstream-tick` — блоком `mirror` (`mirror_tick daily`); отказ этих тиков основной не роняет (`null`).
- **Предполётная проверка портов (`lib/preflight.sh`, §4.6a).** `preflight` (readonly) отдаёт
  `modes` (JSON `preflight_modes` как есть), `lite_site_available`, `pro_available`, `hint`.
  `change-mode --mode=pro|lite_site` перед включением зовёт `_action_require_ports`: недоступный
  режим → `code=port_busy` (чужой процесс) или `code=closed_local` (локальный файрвол) с полями
  `mode`, `port`, `proto`, `pid`, `process`, `unit`, `container`, `product`, `hint`, `reasons[]`;
  `lite` и `pro → lite_site` проверку не проходят (портов не требуют), `ours` не блокирует.
  `status` несёт `lang` и `ports` (`[{profile, port, proto, listening, ours, container, state}]`
  по установленным профилям, все — `udp`), `quality` — `public_ports`, `modes`, `port_warnings`,
  `netdeps_dangling` (имена контейнеров в сети пропавшего контейнера без записи в реестрах, WRP-06).
  `status --brief=on` (CPU-аудит 03.10.2026) — сводка для частых опросов (Обзор и «Настройки» панели): без
  `ports`, `mode_port_mismatch`, `site`, `key_hosts`, `endpoint_effective` и `endpoint_auto` — этих полей в ответе
  **нет** (а не `null`: «не проверяли» не выдаётся за «проверили — пусто»); остальные поля и их порядок — как у
  полного. Это примерно половина цены вызова: порты (ss, docker ps, файрвол), сайт и адреса в ключах людей меняются
  не сами по себе. Без параметра ответ прежний, поле в поле; бот и MCP зовут полный.
- **`name-suggest [--lang=ru|en] [--exclude=<id>]`** (readonly, MCP `name_suggest`; C47-1, «🎲 Случайное имя»,
  docs/MODES_047.md «Имена ключей»): `users_name_suggest` (§4.5) → обычная форма ответа, поля в корне:
  `{status, message, id, label, lang}` — `id` латиницей (`^[A-Za-z0-9_-]{1,32}$`), `label` на языке `--lang` (не
  передан — язык ответа: `GOAMNEZIA_CALL_LANG`, иначе язык установки), не совпадающие с id и подписью заведённых людей и не равные `--exclude` (вариант,
  который уже на экране). Ничего не занимает: имя забирает `user-add --name=<id> --label=<label>`. Отказы:
  `names_missing` (набора `lib/names_capitals.json` нет или он испорчен — путь назван в `message`), `names_exhausted`
  (свободных не осталось), `users_corrupt`; `bad_arg` для `lang` (не `ru`/`en`) и `exclude` (не id) ловит
  диспетчер. Зовут: бот (кнопка «🎲» — `bot.py name_suggestion` читает `id`/`label` в корне), панель
  (`/api/action/name-suggest`), агент MCP.
- **`users-add-bulk --count=1..100 [--profiles=a,b] [--prefix=<префикс>] [--quota-gb] [--days] [--lang=ru|en]`**
  (MCP `users_add_bulk`, уровень write, как у `user-add`; просьба владельца 03.10): `users_add_bulk` (§4.5) — каждого
  заводит тот же `_users_add_locked`, что `user-add`, всех под одним замком, а к контейнерам — одно применение в конце
  (`ga_users_apply_many`). Имена: `--prefix` — `<префикс>-01…NN` с номера после самого большого уже заведённого с этим
  префиксом (хвостовые `-` и `_` у префикса снимаются), подпись = имя; без префикса — разные случайные
  «прилагательное + столица» (`users_name_suggest_many`, правила `name-suggest`), подпись на языке `--lang` (не передан —
  язык ответа). Пустой `--profiles=` — `no_profiles_picked`, как у `user-add`. Отказы ДО изменений: `subnet_full` (в
  подсети профиля свободно меньше, чем людей: каждому — адрес в каждом выбранном профиле; в `message` — свободно из
  скольких), `names_exhausted` (свободных случайных имён меньше), `invalid_prefix`, `invalid_count`, `users_corrupt`;
  `bad_arg` для `count` вне 1–100 и негодного `prefix` ловит диспетчер (`param_types.bulk_count`, `name_prefix`). Ответ —
  поля в корне: `{status, message, requested, count, added: [{name, label}], profiles, prefix, partial, failed}`, ключей
  нет. Сбой на k-м человеке — `success` с `partial: true` и `failed {name, code, message}`: заведённые до него остаются и
  применены; никого не завели — отказ кодом первого сбоя. Контейнер не принял изменения — заведённые этим вызовом
  снимаются, профили переприменяются (как откат `user-add`), отказ кодом применения. Аудит — одна строка
  `users-add-bulk` (`count=<добавлено>/<просили> profiles=… prefix=… names=…`, при сбое `stopped=… code=…`, при
  откате `apply=failed rollback=done`). Таймаут у бота и панели — 900 с (на сотню — минута-две).
- **`users-del-bulk-preview (--names=a,b | --prefix=<префикс> | --state=disabled|expired|quota)`** (readonly, MCP
  `users_del_bulk_preview`, уровень read: имён и подписей, без ключей) и **`users-del-bulk --confirm=<число> (--names |
  --prefix | --state)`** (MCP `users_del_bulk`, `destructive: true`, уровень admin — как у `user-del`; просьба владельца
  03.10: сотня людей по одному на живом сервере удалялась 380 с). Выбор — РОВНО одним способом, иначе `invalid_selection`:
  `names` — имена через запятую, 1–100 (`param_types.name_list`; повтор — один раз; имени нет — `user_not_found` до
  изменений, в `message` названы); `prefix` — все `<префикс>-<число>`, как их заводит `users-add-bulk` (хвостовые `-`
  и `_` снимаются; `office-boss`, заведённого руками, префикс `office` не задевает); `state` — `disabled` (выключены
  руками: `enabled=false` и `disabled_reason` `manual` или нет — выключенных таймером лимитов выбирают `quota` и
  `expired`), `expired` (срок вышел, включён человек или нет; дата, которая не читается, истёкшей не считается),
  `quota` (лимит трафика исчерпан). Предпросмотр отвечает `{mode, value, count, max: 100, too_many, selected: [{name,
  label}], missing}` в порядке `users.json`. Удаление — `users_del_bulk` (§4.5) под одним замком. **Подтверждение —
  числом:** `--confirm` (`param_types.bulk_confirm`, 1–100) обязан совпасть с числом выбранных В МОМЕНТ ИСПОЛНЕНИЯ —
  список успел измениться, и отказ `confirm_mismatch` до изменений; `nothing_selected` (никого), `too_many` (больше 100
  — отказ целиком, а не «первые сто»: при сдвиге списка число в подтверждении осталось бы тем же, а удалялись бы другие
  люди); у этих трёх в ответе `matched` — сколько выбрано сейчас. Порядок — как у `user-del` (SR-06), пачками по 10:
  выключить пачку одной записью → одно применение к её профилям (`ga_users_apply_many`) → стереть пачку одной записью →
  снять страницы подключения пачки (SR-05; каталог страниц обходится один раз до удаления); `clientsTable` — один раз на
  профиль в конце. Временные ссылки на файл (share) не снимаются — как и у `user-del`. Ответ — поля в корне: `{status,
  message, requested, count, deleted: [..], kept: [..], partial, failed, mode, value}`, ключей нет. Контейнер не принял
  пачку — она остаётся в списке ВЫКЛЮЧЕННОЙ (`kept`, удаление можно повторить), дальше не идём: удалённые до неё —
  `success` с `partial: true` и `failed {name, code: user_del_failed, message}`; никого — отказ `user_del_failed`.
  Прерывание (SIGINT, SIGTERM, SIGHUP, SIGPIPE — Ctrl+C, остановка службы, обрыв SSH, ушедший читатель вывода) ждёт конца
  пачки: итог `partial` с `failed.code=interrupted`, остальные не тронуты; прервано до первой пачки — отказ
  `interrupted`. Аудит — одна строка `users-del-bulk` (`count=<удалено>/<выбрано> by=<names|prefix:…|state:…> names=…`,
  при сбое `kept=… stopped=… code=…`), страницы — своими `page-revoke`. Таймаут у бота и панели — 900 с.
- **`lang-set --lang=ru|en`** (в MCP не публикуется): `save_language` пишет маркер `.language` и
  `config.json lang`, `load_language` переключает словарь в этом же процессе — ответ
  `{lang}` с сообщением уже на новом языке; аудит `lang-set`. Панель и бот читают умолчание из
  `config.json .lang` / `status.lang`.
- **Режимы с сайтом (`lib/website.sh`, docs/PRO.md).** `change-mode --mode=lite|lite_site|pro
  [--domain]` — граф переходов под уточнённый договор о режимах (§3.1): **`pro` и `lite_site` —
  один и тот же сайт на домене, разница только в месте AmneziaWG**, поэтому
  `pro ↔ lite_site` без `--domain` идёт **лёгким путём** `website_switch_site_mode` (одна запись
  `mode`; nginx, сертификат, домен, шаблон и панель не трогаются вовсе — тяжёлый путь стоил бы
  перевыпуска сертификата и минуты простоя сайта ради переноса порта); `lite → pro|lite_site` и
  любой переход с явным `--domain` → `website_enable_pro <домен> "" <режим>`, а без домена —
  запасной `website_enable_site_selfsigned "" <режим>`. Запасной путь и при домене, чьи A-записи
  не ведут сюда (или DNS не ответил), — но только пока сайта на Let's Encrypt нет (из `lite`, с
  запасного самоподписанного, пропавший vhost): живой сайт при смене домена идёт через
  `website_enable_pro`, и отказ certbot откатывает на прежний домен (SR-02, SR-54);
  `* → lite` → `website_disable_site`;
  домен, переданный вместе с `lite`, сохраняется. Повтор того же режима — не ошибка, это ремонт
  vhost. Порт профиля мост не двигает: это отдельное действие `profile-port` — рвать связь всем
  без явной просьбы переход режима права не имеет. Ответ один на все
  переходы: `mode`, `previous_mode`, `domain`, `template` (`website_current_template`; в `lite` —
  `null`), блок `site` (как у `status`), `dns_fallback` (сайт встал на запасном, домен сохранён),
  `keys_host_before` и `keys_host` — адрес в новых ключах до и после перехода (разные — `message`
  говорит об этом словами: домен в ключах только в `pro`), `mode_port_mismatch` (булево: режим и порт
  VPN разошлись — `pro` без профиля на UDP/443 или `lite_site` с профилем на нём; `message` тогда говорит,
  что перенести, теми же словами, что SSH-шапка, LC-18) и `vpn_443_profile` (профиль на UDP/443 или
  пусто). `change-template` →
  `website_deploy_template` (в `lite` — `needs_site`, как у `mcp-http-on`); `site-check` →
  `website_check` без замка (в `lite` вердикт `not_pro` — это ответ, а не ошибка; `site.template_id` —
  развёрнутый шаблон, SR-48). Панель на
  домене — везде, где есть домен с ДОВЕРЕННЫМ сертификатом, то есть в обоих режимах с
  сайтом; `not_pro` означает именно его отсутствие (режим `lite` или запасной
  самоподписанный), а не название режима: `miniapp-on` генерирует путь `app-<86 знаков base64url>`
  (`ga_site_panel_path_new`, 512 бит, не начинается с `a-` — маршрут агентов) → `website_panel_path_set`; повторный
  вызов путь не меняет (`changed=false`), ответ — `path`, `url=https://<domain>/<path>/`, `hint`
  (в `actions.json` — `secret: true`); `miniapp-rotate` — новый путь, у выключенной панели →
  `panel_disabled`; `miniapp-off` → `website_panel_path_clear` в любом режиме; `miniapp-status`
  (readonly) отдаёт `enabled` (панель работает), `configured` (путь записан), `available`
  (здесь её вообще можно включить), `reason` (`no_site` | `no_domain` | `untrusted_cert`),
  `hint`, `mode`, `domain`, `panel_port` и `site{nginx_active, https_ok, cert_ok, verdict}`,
  но **не путь**. Три состояния разведены намеренно: «работает», «настроено, но не
  работает» и «здесь этого не бывает» — прежде состояние рапортовало «включено» там, где
  включение отказывало. `site-alt-svc --state=on|off` → `website_set_alt_svc`,
  ответ `alt_svc` (булево) и `applied` (`true` в `pro` и `lite_site`, в `lite` настройка только
  сохраняется). `status` в `pro` и `lite_site` несёт `site{domain, url, template_id,
  template_source, index_present, cert, cert_fingerprint_sha256, cert_expires, cert_days_left,
  panel, alt_svc, mcp_location, listen}`: `domain` заполнен в ОБОИХ режимах с сайтом, когда
  домен готов — пуст только на запасном самоподписанном пути, где сайт отдаётся по IP;
  `url` — `https://<домен>/` или `https://<IP>/`, `cert` — `letsencrypt` | `selfsigned`,
  `cert_fingerprint_sha256` — только для самоподписанного, `panel` — только факт, и он бывает
  в обоих режимах с сайтом; в `lite` — `site: null`. `site-check` на запасном пути
  проверяет HTTPS по IP с `--insecure` и HTTP по 80 (`https_code`, `http_code`), отдаёт `cert`,
  `cert_fingerprint_sha256`, `url`; вердикты те же, `dns_*` не применяются. Коды модуля (`invalid_domain`, `cert_failed`, `nginx_conf_invalid`,
  `unknown_template`, `download_failed`, `no_index`, `app_shell`, `broken_assets`, `invalid_path`,
  `config_write_failed`…) доходят до ответа через `_action_run`. Отказ Let's Encrypt (`cert_failed` после certbot,
  1.0.6, L-26) несёт ещё причину словом — `cert_cause`: `dns` (Let's Encrypt не проверил домен: A-запись не сюда или
  порт 80 закрыт снаружи), `net` (сервер не достучался до самого Let's Encrypt), `limit` (лимит выдачи) — и
  `cert_retry_after` («2026-09-27 10:00», UTC; пусто, если Let's Encrypt срока не назвал). Причину разбирает
  `_ga_site_cert_cause` по журналу certbot (`website_issue_cert` кладёт её в `GA_CERT_CAUSE`/`GA_CERT_RETRY_AFTER`);
  у прочих отказов этих полей нет. Так же — у `lazy-setup`: в отказе `cert_failed` (сайт жил на домене с настоящим
  сертификатом) и в удачном ответе рядом с предупреждением `cert_fallback`. Бот говорит по причине своими словами
  (docs/BOT.md). Аудит мутаций пишет сам модуль
  (`ga_site_audit`) именами действий моста (`change-mode`, `change-template`, `miniapp-on`,
  `miniapp-off`, `site-alt-svc`) без секретного пути.

Зависимости на старте: `check_deps_present` → при нехватке `deps_install_missing --yes core`
(`lib/deps.sh`); в `GOAMNEZIA_DRY_RUN=1` — только предупреждение. Мастер перед Docker ставит
профиль `qr` (`qrencode`).

Меню — дерево из PLAN §5.1: `show_main_menu` + `submenu_users|status|protocols|maintenance|settings|bot`.
В карточке профиля пункт «Изменить один параметр» строит список из `amnezia_profile_param_list`
(имя, текущее значение, сторона), спрашивает значение, для серверных предупреждает о перевыпуске
до `confirm`; «Подсеть» и «MTU» — свои пункты; в «Настройках» — «Endpoint в ключах»
(`ga_set_endpoint_host`); на экране ключей пользователя — «Ключ на другой адрес сервера (разово)»
(`_pick_key_address`; выбранный хост уходит третьим аргументом в `export_conf` / `export_vpn` и
четвёртым — в `share_create`). Подробно — `docs/MENU.md`.

### 4.7a Данные для экранов редизайна (0.5.0, docs/DESIGN_ITERATION3_ACCEPTANCE.md §4.1)

- `backup-last` (и `backup_last_json`) дополнительно отдаёт `schedule` — частоту ночной копии из `backup_schedule.json`:
  `off | daily | weekly | monthly` (файла нет — `off`). Для экрана «Бэкапы» панели 0.5.0.

Поля и действия, по которым панель, бот и страницы рисуют новые экраны. Все — только добавления: прежние поля
и коды не меняются.

- **L-12 `profiles-restart`** (разрушительное с 0.5.0 — SEC50-06: `destructive`, в MCP только уровень `admin` и
  `confirm` = `profiles_restart`, панель шлёт `confirm` после диалога; то же у `profile-restart`, `confirm` = профиль:
  перезапуск рвёт связь всем подключённым, и агент с уровнем `write` мог перезапускать профили в цикле; MCP
  `profiles_restart`, без параметров): перезапуск ВСЕХ установленных
  профилей по очереди в порядке меню (`awg3` → `awg2` → `awg1`), каждый — `amnezia_profile_restart`, как у
  `profile-restart`. Бот, панель, MCP и nginx не перезапускаются (их зовущий процесс не должен умереть посреди
  ответа). Ответ: `restarted` — массив профилей, которые поднялись; `failed` — массив `{profile, code, message}`
  (код и слова модуля: `docker_unreachable`, `port_unpublished`, `restart_failed`…). Успех — все поднялись;
  хоть один нет — `status=error`, `code=restart_failed` и те же два массива; профилей нет — `code=no_profiles`.
- **DK-02 `docker-restart`** (разрушительное: в MCP уровень `admin` и `confirm` = `docker_restart`, панель шлёт
  `confirm` = `docker-restart` после диалога, бот — после экрана-вопроса): служба Docker целиком
  (`amnezia_docker_restart manual`) — пакет обновлён, а служба старая, или Docker ведёт себя странно. VPN пропадает
  у всех на 10–30 секунд. Ответ — итог модуля в корне: `duration_s`, `restarted[]`, `failed[]`, `daemon_before`,
  `daemon_after`, `binary_version`, `stale`; отказ — `status=error`, `code` модуля (`docker_restart_failed`,
  `docker_restart_timeout`, `docker_still_stale`, `restart_failed`) и `command` = `systemctl restart docker`.
  Событий боту ручной перезапуск не шлёт (итог получает нажавший), но попытку помнит: сторож сам — не раньше
  чем через 6 ч. **`status.docker_stale`** (и в `brief=on`) — `ga_amn_docker_stale_status`: `null` или
  `{reason, binary_version, binary_build, daemon_version, daemon_build, client_api, daemon_api, since,
  binary_label, daemon_label, last_attempt, retry_after}`; панель: `GET /api/overview` → `docker_stale` (белым списком) — строка на «Обзоре».
- **KMOD_PLAN А3 `status.kmod`** (и в `brief=on`; с 1.0.12) — `ga_amn_kmod_json`: `{loaded, version, patched, warn}` — загружен ли
  на хосте модуль ядра `amneziawg`, его версия из `/sys/module/amneziawg/version`, есть ли метка набора исправлений
  (`+kaskad-k31…`, `+goamnezia-k31…`) и `warn` — загружен без метки, а у нас есть профиль 3.1. Только чтение, Docker не
  спрашивается. Где видно: шапка SSH-меню (`dash_kmod_unpatched`), главный экран бота (`dashboard_problems`), «Обзор»
  панели и мини-аппа (`GET /api/overview` → `kmod` белым списком). Выбор, установка и пересборка модуля — этап Б.
- **KMOD_PLAN этап Б — модуль ядра по выбору** (с 1.0.13, `lib/kmod.sh`, §4.4a). Только добавления:
  - `status.kmod` и новое действие **`kmod-status`** (readonly, MCP `kmod_status`, поле `kmod`) — `ga_kmod_status_json`:
    к полям этапа А — `state` (`off` / `ready` / `on` / `fallback` / `failed` / `unpatched` / `busy`), `code`, `reason`
    (словами, с ядром), `kernel`, `want` (`kernel` / `program` / пусто), `installed`, `installed_version`, `owner`
    (`goamnezia` / `kaskad` / `other` / пусто), `label`, `profiles` `{<p>: {engine, want}}`, `progress`
    (`{action, step, text, at, started}` | `null`), `selftest` (`{ok, kernel, label, at, reason}` | `null`), `failed`
    (`{kernel, step, code, reason, error, log, at, left}` | `null`: `error` — сама ошибка шага, `left` — «где профили и
    что выбрано» на момент сбоя, пока движок с тех пор не переводили и сервер не перезагружали, иначе пусто; `reason` —
    оба одной строкой), `packages`. В `brief=on` — тоже (дёшево).
  - `profiles-status` → `profiles[]` у каждого: `engine` (фактический, живым опросом контейнера) и `engine_want`
    (выбранный, пусто — не выбирал); корневое `kmod` — как `status.kmod`. В `status` — те же поля профилей из кэша.
  - **`kmod-check`** (readonly, без MCP) → `check` = `ga_kmod_check_json plan`: можно ли здесь (`ok`, `code`,
    `reason` одной фразой) и что поставит apt (`plan`: пакеты, размер, тянет ли новое ядро метапакет заголовков).
  - **`kmod-install`** (`--rebuild=on|off`, `--headers-meta=on|off`, обе по умолчанию `off`; без MCP, не
    разрушительное): согласие человека — сам вызов; ответ — итог `ga_kmod_install` в корне. Идёт минуты (apt,
    сборка, самопроверка) под общим замком — панель и бот зовут его в фоне и показывают `kmod-status.progress.text`.
    Отказ (1.0.13, KMODB-03): `status=error`, `code` упавшего шага и в корне `engine` (на чём профили сейчас: `kernel` |
    `program`), `want` (что выбрано у сервера), `swap_started` (пересборка успела перевести профили на программу);
    `message` — слова шага и где профили (`_ga_kmod_left_words`: на прежнем модуле; на программе и как вернуть —
    пункт «Включить (перевести на модуль)» по SSH; новый модуль не загрузился или не прошёл самопроверку — «не годится,
    включать снова бесполезно»; модуль загружен и прошёл самопроверку, а вернуть профили не удалось (шаг `switch`) — так
    и сказано, при `kmod_clients_lost`/`kmod_engine_mismatch` — «повторять сразу бесполезно»). Те же слова «где профили»
    лежат в `failed.left` в `state/kmod.json` (с загрузкой `failed.boot`; сама ошибка — `failed.reason`) и видны в
    `kmod-status` и шапке SSH: служба пересборки после обновления отвечает только в журнал юнита. Верны они до первой
    смены движка (`kmod-engine`, удачной или с откатом, снимает `failed.left`) или перезагрузки — дальше показывается
    только ошибка (R4-1). Удачный `kmod-engine --engine=kernel` снимает провал этого ядра на шагах `load`, `selftest`,
    `swap`, `switch`; провал пакетов, исходников, патча, сборки (с `failed.log`) и `hooks` остаётся до удачной установки
    (R4-2). Шаги `failed.step` пересборки с переводом: `swap` (перевод на
    программу) → `load` → `selftest` → `switch` (профили обратно на модуль).
  - **`kmod-engine --engine=kernel|program`** (без MCP, разрушительное: перезапуск профилей, люди переподключатся
    сами за 15–25 с): ответ — `{engine, want, profiles[], rolled_back, copies}` в корне; отказ — `status=error`,
    `code` (`kmod_not_ready`, `kmod_clients_lost`, `kmod_engine_mismatch`, `kmod_busy`…) и то, что успело случиться,
    с `rolled_back: true`. Панель и бот: вопрос с согласием (тексты SSH: `kmod_consent_*`), фон, `progress`.
  - **`kmod-remove`** (без MCP, разрушительное): профили на программу, модуль прочь (или оставлен соседу) →
    `{removed, kept_for_neighbour, packages[], command}`; пакеты удаляет человек командой из `command`.
  - Сторож (`watch-tick` → `kmod`) и обновление продукт не переключают; пересборка под новую метку — служба
    `goamnezia-kmod-rebuild`. Событий боту этап Б не добавляет: предупреждение «выбран модуль, а работает
    программа» — в `status.kmod` (`state=fallback`, `reason`) и строкой в шапке SSH-меню.
- **Подгонка TCP MSS** (этап Б, решение владельца 08.10): в контейнере каждого профиля — правила TCPMSS по TCP SYN
  в обе стороны (`ga_amn_mss_cmd`, §4.4); своим профилям — блоком в `start.sh` (на модуле и на программе), уже
  поставленным — тем же путём, каким обновляется `start.sh` (подъём, тик сторожа, миграция обновления), контейнерам
  приложения — командой со старта (служба изоляции) и тиком. Полей и действий не добавляет.
- **KMOD-01 `profile-set-param`** (с 1.0.12): в корне ответа рядом с `reissue_required` — `restarted` (профиль
  перезапускался: серверный параметр или маскировка на модуле ядра; клиенты переподключатся сами, обычно за 15–25 с),
  в `result` — ещё `engine` (`kernel` | `program` | `unknown`). Бот и панель говорят это словами (`pr_param_restarted`,
  `prParamDoneRestart`). PARAM-01: `S4` больше 40 — отказ `params_invalid` словами («Задайте S4 до 40»).
- **`profile-set-param` 1.0.13** (живые проверки 08.10, PARAM-03, 05, 08…11) — только добавления:
  - пределы того, что пишем мы (§4.2): тайминги 3.1 и `ContentPaddingAddition` вне коридора, связка таймингов,
    `Jc × Jmax` больше 16384, `Jc + число I1–I5` больше 127, хвосты 3.1 при разных S и широких H — отказ `params_invalid`
    словами с допустимым диапазоном («RekeyTimeout = 0-7 не принят: допустимо от 3 до 10 (у новых профилей — 3-7). …»);
    у подхваченного с такими значениями другие параметры меняются;
  - не применилось (контейнер не поднялся, syncconf не прошёл, конфиг не записался) — прежнее значение назад в
    `config.json` и `awg0.conf`, профиль поднят: `status=error`, `code=param_rolled_back` («H1 = 77 не применился:
    Контейнер amnezia-awg3 не поднял интерфейс awg0 за 20 с. Вернул прежнее значение (1) …») или `param_apply_failed`
    (вернуть или поднять не вышло — профиль лежит, «поднимите контейнер … заново»);
  - кому нужен новый конфиг: в корне `clients_update` (значение изменилось — выданные ключи несут прежнее, пока человек
    не возьмёт новый: и серверный, и клиентский параметр), в `result` — `audience` `{subscription, page, key, app}`,
    `update_hours`, `summary` и `steps[{kind, text}]` (как у `profile-fix-s4`: подписки обновятся сами за `update_hours`,
    страница — открыть снова, ключи `vpn://` и `.conf` — выдать заново, ключ из приложения — поделиться заново или
    перевыпустить; у клиентского — «прежний работает со старым значением»); `message` = итог + `summary`. Бот показывает
    `message` и `result.steps` строками «•» и своей строки «выдайте ключи заново» при таком ответе не добавляет; панель
    (и мини-апп) — окно со словами ядра: `message` и `steps` по людям, как у `profile-fix-s4` (проверяльщик 15.10);
  - проверка пределов сама упала (`awgkeys.py` limits — исключение или кривой вход) — значение не пишется:
    `status=error`, `code=params_failed`, «<K> = <v> не записан: не удалось проверить значение — <причина>…»;
- **`import-adopt` 1.0.13**: в ответе `param_warnings` — слова о значениях, которые продукт сам так не записал бы
  (тайминги 3.1 вне коридора, всплеск, Jc + I, хвосты + разные S + широкие H); подхват идёт, значения — как в контейнере.
- **`kmod-install --rebuild=on` 1.0.13 (KMODB-03)**: модуль в памяти — новый и в память (`reload`: `swapped` —
  профили через программу и обратно с копиями и проверкой людей, `reloaded` — без перезапусков, `neighbour` — им
  пользуется Kaskad, только на диск); `loaded` — метка в памяти, `on_disk` — у файла модуля. **`kmod-status` /
  `status.kmod`** — `removed_at` (KMODB-01): модуль удалён и с тех пор не ставился.
- **S4-FIX `packet_risk`** (с 1.0.12; MTU-01, решение владельца 08.10) — у каждого профиля в `status` (и в `brief=on`) и
  `profiles-status`: `{packet, budget, s4, safe_s4, mtu, keyless}` или `null` (`ga_amn_packet_risk`). Риск — пакет данных
  при стандартном MTU 1280 больше бюджета 1400: `1280 + 80 + S4 > 1400`, то есть `S4 > 40` (у 2.0, выданных до 1.0.12);
  MTU, поднятый человеком для проводной сети, не в счёт; у 3.1 (S = 12) и профилей приложения (S4 = 12) риска нет.
  `keyless` — люди приложения Amnezia без ключа на сервере (для них — согласие). Только чтение, Docker не спрашивается.
  Где видно: шапка SSH-меню (`amnezia_s4_risk` + `dash_s4_hint`), «Протоколы» (`pr_s4_mark`), карточка профиля (пункт 12
  `pr_s4_fix`), главный экран и экран профиля бота (`s4_risk_fmt`, кнопка `pr_s4~`), «Обзор» и карточка профиля панели
  и мини-аппа (`GET /api/overview` и `GET /api/profiles` → `packet_risk` белым списком); MCP — поле в `server_status` и
  `profiles_list`, действия у агента нет.
- **S4-FIX `profile-fix-s4 --profile=<p> [--adopted-confirm=<p>]`** (с 1.0.12; `mcp: null`, `destructive`) —
  `amnezia_profile_fix_s4`: новый S4 12–24 (S4-MARGIN, с 1.0.13; до того 15–40) тем же путём, что серверный параметр в `profile-set-param` (проверка набора,
  Docker до первой записи, `config.json`, `awg0.conf`, рестарт; на модуле ядра — пересоздание интерфейса), приватные
  ключи людей прежние. Ответ — поля модуля в корне (`_action_emit_merged`): `s4_old`, `s4`, `packet_old`, `packet`,
  `budget`, `restarted`, `engine`, `keyless`, `update_hours`, кому что делать — `subscription` (подписку забирали:
  обновится сама за `update_hours` или по «Обновить»), `page` (живая страница подключения: открыть снова и
  импортировать заново), `key` (выданный ключ или файл: импортировать заново), `app` (ключ из приложения Amnezia:
  поделиться заново или перевыпустить), `steps[{kind, text}]` — те же указания словами на языке вызова (L-24),
  `message` — итог словами. Отказы: `s4_safe` (риска нет или у линии нет S4), `adopted_keyless` (подхвачен у
  приложения, у людей ключи только в нём — нужен `adopted_confirm` = тот же профиль, как у переноса порта ACC-2).
- **DUP-LINE-01: линии на сервере** (с 1.0.13, решение владельца 15.10). Правило «какая линия на какое место» одно —
  `ga_lines_need` (lib/amnezia.sh): недостающая линия пары 2.0 / 3.1 встаёт на своё место (2.0 → awg2, 3.1 → awg3), а
  если оно занято — на другое свободное; его зовут и Ленивый Pro (`_ga_modes_lines_need`), и «Установить»
  (`ga_profile_auto_line`). `profile-install` — необязательный `line` (2.0 | 3.1 на awg2/awg3; 1.x — только awg1, иначе
  `invalid_line`); без него — `ga_profile_auto_line`: рядом с подхваченной 3.1 приложения в awg2 место awg3 получает 2.0.
  В `status` (и в `brief=on`): `lines_check` = {`no_20` (профили стоят, ни 2.0, ни 1.x нет), `shared` [{line, profiles}],
  `missing` (линии пары, которых нет), `reline` {profile, from, line, port, title, steps} | null (свой профиль, который
  можно переставить; профиль приложения — никогда; `steps` [{kind, text}] — у каждого пользователя его действие после
  перестановки, до неё: `ga_amn_reline_preview`, только в ответах моста и только без `brief` — один расчёт на оба поля, N-3), `install` {profile, line} | null (переставлять нечего, недостающая встанет
  на свободное место по «Установить»), `text` — словами}; `free_lines` {место: линия} — какая линия встанет на свободное
  место; `dupline_fix` — см. ниже. У профилей в `status.profiles` и `profiles-status`: `line_shared`, `imported`, `title`.
  `core_version` (A-8 критика 16.10, `ga_profile_core_version`) — сборка ядра профиля для людей: у своего профиля — тег
  образа (`3.1.20260828`); у подхваченного у приложения (тег `latest`) — версионный тег Docker Hub, если дайджест его
  базового образа совпал с дайджестом `latest` из последней удачной проверки обновлений (`last_check.json`); не узнать —
  `""` (интерфейсы пишут «не узнать», а не «latest»). Панель отдаёт поле в `profiles` белым списком.
  **Подпись профиля для людей** (`ga_profile_title`, решение владельца 15.10) — без имени места awg2/awg3 и всегда с
  портом: «AmneziaWG 3.1 · порт 6714», подхваченный — «… · из приложения», свободное место — «AmneziaWG 2.0 — не
  установлена»; место и контейнер — только в технике (мост, MCP, журналы, аудит, JSON).
- **`profile-remove-preview --profile=<p>`** (с 1.0.13; `mcp: null`, `readonly`) — что «Удалить профиль» заберёт у
  пользователей, до согласия (PROFILE-REMOVE-PAGES, `ga_amn_remove_preview`): `holders` (у скольких ключ на профиле),
  `pages` (сколько страниц подключения снимет удаление — в них был только этот профиль: из профилей страницы ключ с
  приватной частью у человека есть только на нём; правило то же, что у снятия `ga_users_pages_prune_locked`; страницы,
  которые и сейчас без ключей, не считаются), `page_users` [имена, без адресов страниц], `text` (то же словами на языке
  вызова; пусто — таких страниц нет; он же `message`), `title`. Профиль не установлен — `profile_not_installed`. SSH,
  панель и бот показывают это до набора порта; само `profile-remove` не менялось.
- **`profile-reline --profile=<p> [--line=2.0|3.1] [--keys-to-others=on|off]`** (с 1.0.13; `mcp: null`, `destructive`, `confirm` = профиль; человек
  в SSH, панели и боте подтверждает набором порта профиля, мосту уходит профиль) —
  `amnezia_profile_reline`: свой профиль, который повторяет линию другого, — на вторую линию пары. Собрано из готовых
  действий: удаление профиля (страницы подключения при этом не снимаются, `GA_AMN_KEEP_PAGES`), установка с линией по
  просьбе на прежние порт, подсеть и пресет, ключи на новом профиле всем, у кого они были (`users_add_peer`). Ответ —
  поля в корне: `line_old`, `line`, `port`, `subnet`, `title`, `users`, `failed`, `update_hours`, кому что делать —
  `subscription`, `page`, `key`, `steps[{kind, text}]`, `summary`. Отказы: `reline_adopted` (подхвачен у приложения),
  `reline_needless`, `invalid_line`, до удаления — `low_disk`, `docker_unreachable`, `params_failed` (ничего не тронуто);
  после — `reline_rolled_back` (новая линия не встала — вернули прежнюю; в `message` — кому ключи выданы и что им делать,
  кому выдать в карточке), `reline_partial` (профиль записан — с новой линией или вернувшейся прежней, — а контейнер не
  поднялся: поднять заново из копии состояния; в `message` — кому ключи выданы и кому выдать после подъёма),
  `reline_failed` (не встала и прежняя — «Установить» в «Протоколах»). Флаг `dupline_bug` снимает удача или
  `reline_partial` с новой линией. `--keys-to-others=on` (DUPLINE-KEYS-01) — после удачной перестановки тем же действием
  ключ на новой линии и остальным пользователям, у кого его на профиле не было (пользователи приложения, новые): в ответе
  `others` [имена, кому выдан] и `others_failed` [{name, code, message}] (у кого выдача не вышла), в `steps` — `others` и
  `others_failed` (на языке вызова), в журнале аудита — `others=N failed_others=M`. Без параметра (или `off`) — как
  прежде: полей `others*` нет. SSH, панель и бот спрашивают об этом до согласия и передают ответ сюда; сами ключи
  остальным больше не выдают. При отказах (`reline_rolled_back`, `reline_partial`, `reline_failed`) остальным ключ не
  выдаётся.
- **Универсальный I1** (KEENETIC-I1, с 1.0.13, решение владельца 29.10) — шаг миграции `_migrate_i1_universal`
  (в обеих ветках: смена версии и тот же код, после `_migrate_kmod`; под замком конфига) — `ga_amn_i1_universal_all`:
  у каждого установленного профиля, у которого I1 ровно прежнее умолчание (`awgkeys.py i1-universal`), ставит
  универсальный I1 со свежим ID тем же путём, что `profile-set-param` клиентского параметра
  (`_amnezia_profile_set_param_locked`, `GA_AMN_PARAM_QUIET=1`): `config.json`, строка `# I1 = …` в конфиге контейнера,
  `awg syncconf` — без рестарта, без перевыпуска ключей, без разрыва сессий. Журнал — `profile-i1-universal` «<p> I1
  legacy-default->universal», в выводе обновления — строка `mig_i1_universal`. Свой I1 и I2–I5 не трогаются, повтор
  ничего не меняет. Пропуск (rc 0, обновление не падает; повторит следующее обновление кода): Docker не отвечает,
  контейнера нет, конфиг контейнера или `users.json` не прочитались (или не разобрались пиры), в живом конфиге есть пир,
  которого нет в `users.json` этого профиля (`_ga_amn_foreign_peer_any`: перезапись конфига сняла бы человека,
  добавленного приложением, или клиента с ключом пользователя другого профиля — `taken_peer`, KEENETIC-I1-N V1; пир, с
  которым клиент не подключится — не ключ WireGuard, нет IPv4, — миграцию не останавливает, N2), смена не прошла и
  откатилась. Пропуск не молчаливый (KEENETIC-I1-R, `_ga_amn_i1u_skip`): одна строка
  `log_warning` — профиль, причина и как довести (`amnezia_i1u_skip`: заменить I1 в «Изменить один параметр» на готовое
  `<b 0x…>` — оно следующей строкой, без переноса; контейнера нет или профиль не поднялся — сначала «Поднять контейнер
  заново», Docker не отвечает — когда заработает (`amnezia_i1u_do*`, KEENETIC-I1-Q); `amnezia_i1u_skip_foreign`:
  сначала сверка с контейнером — путь в SSH, панели и боте, `amnezia_reconcile_where`), и журнал
  `profile-i1-universal-skipped` «<p> reason=<docker_unreachable|container_missing|conf_unreadable|foreign_peers|
  taken_peer|apply_failed|apply_failed_down>[ code=<код>]» — без значений I1. Сообщения неудачной смены стираются, строка о
  пропуске — после них; коды ошибок не остаются. Уже выданные ключи с `<r 2>` работают как прежде (I-пакеты шлёт
  клиент); в Keenetic ключ загружают заново. Подхват контейнера приложения (`import_adopt`) делает то же
  преобразование сразу после подхвата, сверка (`import_reconcile <p> apply`) — сразу после сверки (люди приложения
  уже свои); замена не прошла — ответ и код сверки прежние, остаётся строка-предупреждение.
- **«Ошибка прошлых версий»** (с 1.0.13, решение владельца 15.10) — шаг миграции `_migrate_dupline` (в обеих ветках:
  смена версии и тот же код) находит сервер, где своя 3.1 повторяет подхваченную 3.1 приложения, а 2.0 нет
  (`ga_amn_dupline_candidate`), ставит в `config.json` флаг `dupline_bug` {profile, detected_at, fixed_at, ssh_shown} и
  одно событие уведомления `dupline_bug` {profile, from, line, port, title, users, text}. Сама ничего не переставляет.
  `status.dupline_fix` (и в `brief=on`) — {profile, from, line, port, title, users (с ключом на профиле), text,
  detected_at, steps (как у `lines_check.reline`)}, пока флаг стоит, не исправлен и сервер в том состоянии; иначе `null`. SSH — экран один раз после
  «Что нового» («1) Исправить», «2) Позже», `ssh_shown`) и строка в шапке; панель — блок на «Обзоре»; бот — уведомление
  и строка на главном экране. «Исправить» — `profile-reline`.
- **`status.subs_update`** (с 1.0.13, R9-1 17.10; `_subs_update_json` в install.sh) — срок забора подписки без обхода
  подписок: `{update_interval, update_slow: [{name, hours}]}`, как те же поля `sub-list` (`ga_subs_update_hours`,
  `ga_subs_update_slow`: приложения каталога, которые обновляют подписку сами и реже совета сервера, — FlClash, 24 ч);
  модуля подписок нет — `null`. Только в полном `status`, в `brief=on` поля нет. Им пользуется окно «Исправить S4» в боте;
  панель берёт тот же срок из `sub-list` без имени (`subWithinNow`), шаги `profile-change` ядра — из `ga_subs_update_slow`.
- **Подписка** (с 1.0.13, DUP-LINE-01): `all.yaml`, `all.txt`, `all.vpn` — все ключи человека (раньше по ключу на
  линию, и ключ на второй 3.1 выпадал), по линии 3.1 → 2.0 → 1.5 → 1.0, внутри линии — порядок мест awg3 → awg2 → awg1;
  имена уникальны: линия двух профилей — «AmneziaWG 3.1 · порт 6714». Одиночные `awgNN.conf`/`awgNN.vpn` — один ключ на
  линию, первый с ключом по порядку мест (то же правило, что `ga_subs_lines_json` и быстрый путь `sub-show`).
- **`profile-mtu`** (с 1.0.12; решение владельца 08.10: MTU 1280 — стандарт и минимум) — ниже 1280 отказ: в мосту
  `bad_arg` (`minimum` 1280 в `actions.json`), в ядре и через `profile-set-param MTU` — `invalid_mtu` словами «Ниже 1280
  туннель не поднимется у клиентов с IPv6 (AllowedIPs ::/0) — оставьте 1280 или больше» (L-02б); MCP и панель отвечают
  теми же словами (`below_minimum`). В ответе `packet` (пакет данных MTU + 80 + S4), `warnings` — что такой MTU
  сломает наверняка: `path` (MTU выше 1280 и пакет больше 1440 — на части хостеров и сетей скачивание встанет: путь от
  хостера часто меньше 1500, у Aeza — 1456, L-02а); `warning` — те же предупреждения словами. Значение всё равно
  записывается: спрашивают «Сохранить всё равно?» SSH, бот и панель — до записи. 1320 при S4 ≤ 40 — без слов.
- **L-01 `endpoint_host_gone`** — в `status` был и раньше (вписанного в ключи IP на машине нет: ключи с ним не
  соединятся; намеренно чужой адрес — проброс — пропавшим не считается). Теперь его несут и ответы панели:
  `GET /api/overview` → `endpoint_host_gone` (bool), `GET /api/settings` → `endpoint_host_gone` (bool; `status` из
  кэша, сбой моста — `false`).
- **L-06 `status.server_ip_change`** — последний переезд сервера на другой IP или `null` (переездов не было):
  `{old, new, at, endpoint_host, endpoint_host_action}` — прежний и новый адрес, время `ga_now`
  (`2026-09-27T10:00:00Z`), что стало с вписанным в ключи адресом: `moved` (был прежний IP — переехал на новый),
  `kept` (намеренно чужой — оставлен), `dropped` (вписанного IP на машине больше нет — снят), пусто (закрепления не
  было); `endpoint_host` — тот вписанный адрес (пусто, если действия не было). Пишет сторож
  (`amnezia_server_ip_check`, раз в час из `watch-tick`, и проверка адреса при восстановлении) в `config.json`
  `.server_ip_change` — вместе с прежним событием бота `server_ip_changed`; хранится последний, стирать не нужно
  (как долго показывать — решает экран по `at`). Панель: `GET /api/overview` и `GET /api/settings` →
  `server_ip_change` (белым списком: адреса — только адреса, время — только формы `ga_now`, действие — одно из
  четырёх слов; испорчено — `null`).
- **L-07 `status.upstream`** — итог суточной проверки обновлений Amnezia без похода в сеть (из памяти таймера
  `goamnezia-upstream`) или `null` (ни одной проверки ещё не было): `{checked` — время последней УДАЧНОЙ проверки
  (пусто, если удачных не было), `attempted`, `reachable` — время и исход последней попытки (сеть недоступна —
  `reachable=false`, а итог и `checked` остаются от прежней удачной), `updates_available`, `hint` (слова ядра),
  `hint_key` (A-16 критика 16.10: какая это подсказка — `update` | `current` | `client_only` | `unreachable`, `""` у
  памяти старых ядер; панель говорит по нему своими словами на языке человека, `hint` — только запасом),
  `client_release{current, latest, update}` (или `null`), `profiles[{profile, current_tag, available_tag, update}]`,
  `unsupported[]` (имена новых параметров, «не поддержан»)`}`. Память попытки — `$GOAMNEZIA_DIR/upstream/last_tick.json`
  (0600), удачного отчёта — `last_check.json`. Полный отчёт той же проверки без сети — `upstream-check --cached=on`:
  ответ `upstream-check` + `cached: true` + `summary` (= `status.upstream`); проверок не было — `code=no_cached_check`
  (с `summary`). Панель: `GET /api/overview` → `upstream` (белым списком).
- **L-03 отказы входа из мини-аппа** (`POST /api/auth/miniapp`, 401 как раньше, логика входа прежняя): у ответа
  `code`, `reason` и `texts{ru, en}` — `signature_invalid` / `bad_signature` (подпись не подошла), `miniapp_expired` /
  `expired` (подпись верна, но устарела), `miniapp_not_admin` / `not_admin` (подпись верна, человек не из
  `ALLOWED_IDS`), `miniapp_not_configured` / `no_admins` (у бота нет администраторов) или `no_token` (в `.env` бота нет
  `BOT_TOKEN`). Пока подпись не верна, про администраторов ответ ничего не говорит (`telegram_init_data_check`).
- **L-08 «заморозить не удалось»** — `agents_list` (`agent-list`, панель «Агенты», бот) у каждого доступа:
  `freeze_failed` (bool), `freeze_failed_at` (время `ga_now` или пусто), `freeze_failed_ip` (адрес клона или пусто).
  Правда, когда сервер MCP увидел ключ из истории выданных (клон), а `agents.json` в этот миг не записался (замок
  занят, реестр испорчен, запись упала): доступ ЖИВ, клон работает, тревога `agent_compromised` с `frozen: false`
  повторяется раз в минуту. Отметку пишет `server.py` (`ChainStore.mark_freeze_failed`) рядом с цепочкой:
  `$GOAMNEZIA_STATE_DIR/agents/<id>.freeze_failed` (0600, `{id, at, ip}`); снимают удачная заморозка (тогда
  `compromised=true`, а `freeze_failed=false`), перевыпуск (`agent-rotate`), выключение (`agent-disable`) и отзыв
  (`agent-del`). Испорченная отметка пропускается.
- **L-24 язык ответа моста на один вызов** — `GOAMNEZIA_CALL_LANG=ru|en` в окружении вызова (правила диспетчера
  выше, §4.7).
- **L-09 `domain-check --domain=<домен>`** (readonly, MCP `domain_check`): DNS НОВОГО домена ДО смены режима или
  домена — ничего не пишет и не выпускает (`site-check` проверяет только уже записанный домен). Поля в корне
  (`website_domain_check` = `ga_site_dns_state`): `domain`, `state` (`ok` — все A-записи сюда, `partial` — часть
  чужая, `elsewhere` — ни одна, `none` — записей нет, `unknown` — DNS не ответил или сервер не знает своих адресов),
  `a[]`, `own[]` (адреса машины), `ours[]`, `foreign[]`, `unpublished[]` (адреса машины, которых нет в DNS),
  `cloudflare` (чужие A-записи сплошь из сетей Cloudflare — включено проксирование), `aaaa[]`, `aaaa_foreign[]` (AAAA
  не на IPv6 машины: Let's Encrypt сначала пробует IPv6); `ready` — домен годится для Let's Encrypt прямо сейчас
  (`state=ok`, без Cloudflare и без чужих AAAA); `hint` — что не так, словами SSH (`website_dns_hint`), пусто — всё
  хорошо; `message` — `hint` или «DNS домена … в порядке». Недопустимый домен — `bad_arg` (шаблон `domain`).

### 4.7b Режимы 0.6.0: Ленивый Pro и линия на UDP 443 (`lib/modes.sh`, docs/MODES_047.md)

- **Названия для людей** (словари SSH, бота и панели; id режимов не меняются — на них стоят config.json, мост, MCP и
  goInstaller): сценарий «Ленивый Pro — только домен, остальное сделаю сам»; `pro` — «Pro — сайт и VPN на 443»;
  `lite_site` — «Semi-Pro — VPN и сайт»; `lite` — «Lite — только VPN». Порядок на экранах — 1 Ленивый Pro, 2 Pro,
  3 Semi-Pro, 4 Lite: первый экран мастера (Enter — Lite, конец ввода — остановка мастера, «5» — повторить проверку портов) и «Настройки →
  Режим и сайт → Сменить режим» (нынешний режим отмечен; нынешний Pro — выбор линии на UDP 443). В Pro экран «Режим и
  сайт» показывает линию на UDP 443 и пункт 9 «Линия на UDP 443».
- **Модуль** решает только «что и в каком порядке»; каждый шаг — функция соседнего модуля (`import_adopt`,
  `amnezia_profile_install`, `amnezia_profile_set_port`, `website_enable_pro`, `users_name_suggest`, `users_add`,
  `pages_create`, `timers_install`). `ga_modes_domain_norm` чистит ввод человека: схема, путь, порт и точка в конце
  отрезаются, IP — отказ `domain_is_ip`, национальные буквы — в punycode (python3, кодек idna; не вышло —
  `idn_failed`), итог — `GA_MODES_DOMAIN` (ASCII) и `GA_MODES_DISPLAY` (как читают люди). `validate_domain`
  (`lib/common.sh`) принимает зону `xn--…` (.рф). DNS — `ga_site_dns_state`; на стенде без `GOAMNEZIA_DNS_STUB` домен
  «указывает сюда», как у `_site_dns_points_here`.
- **`lazy-check --domain=<d>`** (readonly, `mcp: null`): `{domain (punycode), domain_display, points_here (все
  A-записи сюда), a, aaaa, server_ips, cloudflare_proxied, ports: {tcp80, tcp443, udp443: {free, by}},
  app_containers: [{name, line, port}], plan, reasons, udp443_line, keys_have, keys_new}` (необязательный `--keys=1..20`,
  по умолчанию 5) — поля JSON модуля в корне, `message` — итог словами (первая причина, когда план не `pro`; за прокси
  Cloudflare — ещё «ключи ведут на IP сервера»). `ports.<порт>.ours` — порт держим мы сами (наш nginx, наш профиль): для
  сценария он свободен (`free: true`), людям — «занят нами». `keys_have` — живые ключи прошлых заходов (`config.json
  .lazy.keys`, их сценарий засчитывает), `keys_new` — сколько ключей он заведёт на самом деле: `max(0, keys − keys_have)`.
  Линии — по версии контейнера, а не по месту профиля (LZ-02): приложение Amnezia держит 3.1 и в `amnezia-awg2`; недостающая
  2.0 тогда встаёт на свободное место awg3 (`amnezia_profile_install <место> <порт> <пресет> <подсеть> <линия>`). `udp443_line` — какая линия будет на UDP 443 по итогу плана: у `pro` —
  та, что там уже стоит (наша или подхватываемый контейнер приложения — её порт с людьми не трогаем; например, 3.1 после
  `pro-line`), иначе та, что встанет (2.0, а если 2.0 уже на своём порту — 3.1); у `semi_pro` (в том числе
  `no_line_for_443`) и `blocked` — `null`. `free` — порт не держит чужой процесс (наш nginx и наши контейнеры — свободно); `by` —
  кто держит, словами предполётной проверки («nginx (PID 812, юнит nginx.service) — похоже, это goTelegram Pro»,
  «docker (контейнер amnezia-xray)»), у свободного — `null`. `app_containers` — контейнеры приложения Amnezia, не наши: `line` —
  линия AmneziaWG (1.0/1.5/2.0/3.1), у других протоколов приложения пусто; `port` — опубликованный порт. `plan`:
  `pro`; `semi_pro` — UDP 443 держит чужой процесс или обе линии уже стоят на своих портах (их порты с людьми сценарий
  не двигает); `blocked` — TCP 80/443 заняты чужим или домен смотрит не сюда. `reasons` (уточнения договора 28.09):
  `dns_none`, `dns_elsewhere`, `dns_partial`, `aaaa_foreign`, `cloudflare_proxied` (не отказ: сайт и сертификат идут
  через прокси, ключи — на IP), `tcp80_busy`, `tcp443_busy`, `tcp443_app` (TCP 443 держит контейнер приложения —
  Xray/Cloak), `udp443_busy`; сверх списка договора — `dns_unknown` (DNS не ответил), `no_line_for_443`,
  `awg2_container_busy` (место 2.0 занимает контейнер приложения с людьми, подхватить нельзя). Отказы: `missing_arg`,
  `bad_arg` (диспетчер, тип `domain_input`), `no_domain`, `invalid_domain`, `domain_is_ip`, `idn_failed`.
- **`lazy-setup --domain=<d> [--keys=1..20, по умолчанию 5]`** (запись, `destructive`, `secret`, `mcp: null`;
  первый параметр — `domain`, по нему панель берёт `confirm`): проверка — как у `lazy-check`, и `blocked` отказывает ДО
  любых изменений (`dns_not_here`, `port_busy`, `app_container_busy`, `awg2_container_busy`; в ответе отказа — ещё
  `plan` и `reasons`). Дальше без вопросов: подхват контейнеров приложения AmneziaWG (`import_adopt`, их порты не
  трогаются — ACC-2; не вышло с местом 2.0, где люди, — `adopt_failed`); закрытые локальным файрволом TCP 80/443 —
  `preflight_open_local`; ответы в config.json (на чистом сервере — режим, домен, версия; `server_ip` и DNS клиентов
  Google — если пусто); недостающие линии: 3.1, затем 2.0; на UDP 443 — подхваченный, если он уже там, иначе 2.0
  (решение владельца), а если 2.0 уже стоит на своём порту — 3.1; ни одна не может встать на 443 — Semi-Pro
  (`no_line_for_443`); сайт — `website_enable_pro <домен> "" <режим>` (случайный шаблон до трёх попыток, потом
  встроенная заглушка `builtin_stub` — `_ga_site_deploy_stub` в `lib/website.sh`; панель на домене включается сама);
  отказ Let's Encrypt на чистом сервере — запасной самоподписанный с сохранённым доменом (`cert_fallback`), не встал и
  он — Lite (`site_failed`); если сайт уже жил на домене с настоящим сертификатом — отказ кодом модуля сайта, прежний
  домен и сайт на месте; домен за прокси Cloudflare — `endpoint_host` = IP сервера (если его никто не вписывал);
  панель — службой (`_panel_ensure_local`: пароль ждёт показа в SSH, `panel.password_pending`); ключи — `N` человек со
  случайными именами (`users_name_suggest` на языке установки), у каждого обе линии 2.0 и 3.1 (подхваченный старый
  1.x с `wg0` — нет), и страница подключения на каждого сроком 30 дней (только на домене с настоящим сертификатом);
  затем `installed=true` и таймеры. Ответ: `{mode, domain, udp443: {profile, line} | null, profiles: [{id, line, port}],
  site: {template}, panel_on_domain (булево), keys: [{name, label, page}], warnings}`; `page` — полный адрес страницы
  (секрет: через окружение, не аргументом jq — SEC46-02) или `null`; адрес панели — действием `miniapp-on` по её
  правилам доступа. `warnings`: `template_fallback`, `cert_fallback`, `adopted`, `keys_partial`, `panel_off` и все коды
  `reasons`, которые сценарий обошёл (`udp443_busy`, `cloudflare_proxied`, `aaaa_foreign`, `no_line_for_443`); редкие:
  `adopt_failed`, `firewall_open_failed`, `line_skipped`, `profile_failed`, `site_failed`, `timers_failed`.
  **Повторный запуск ничего не ломает** — и после обрыва, и из «Режим и сайт» на установленном сервере: порты стоящих
  профилей не двигаются, ключи и страницы прошлого захода (`config.json .lazy`) засчитываются, живой сайт на прежнем
  домене при отказе Let's Encrypt остаётся. Аудит — `lazy-setup` (без адресов страниц). Время ожидания в клиентах —
  1800 с (`lazy-check` — 120 с).
- **`pro-line --profile=<id> [--adopted-confirm=<profile>]`** (запись, `destructive`, `mcp: null`): только в режиме
  `pro` (иначе `mode_not_pro`); выбранная линия встаёт на UDP 443 (не установлена — ставится сразу на 443), прежний
  держатель 443 — на свободный случайный порт (`ga_amn_pick_port <профиль> no443`), и если новая линия не встала —
  возвращается на 443. Профиль с людьми приложения без ключа на сервере (выбранный или прежний держатель) — только с
  `--adopted-confirm=<тот профиль>`, иначе `adopted_keyless` ДО любых изменений, имя профиля — полем `profile`. UDP 443
  держит чужой процесс — `port_busy` (`ga_amn_port_check`). Ответ: `{mode: "pro", udp443: {profile, line},
  profiles: [{id, line, port}]}`, `message` — что встало на 443 и куда уехал прежний. SSH «Режим и сайт» и переход в
  Pro зовут ту же функцию (`_site_vpn_port_apply`). Аудит — `pro-line`. Время ожидания в клиентах — 600 с.
- **Без терминала (goInstaller): `install.sh --lazy --domain=<d> [--keys=N]`.** Тот же сценарий с экраном итога; конец
  ввода — отмена, а не согласие (goinstaller-ready §10): домен не передан — вопрос, EOF — «Ленивый Pro отменён», rc 1;
  домен не сюда — повтор DNS раз в 15 с до 5 минут (`GOAMNEZIA_LAZY_DNS_INTERVAL` / `_TIMEOUT` у стендов), потом
  отмена; «Привязать бота?» — «позже». Установленный сервер не трогается: «уже установлен», rc 0 (Ленивый Pro на нём —
  пункт «Режим и сайт»). `--keys` не 1–20 — rc 2. Подхват контейнеров приложения — без вопроса.
- **SSH.** Мастер: первый экран (контейнеры приложения и «Подхватить?», OWN-1) остаётся первым; пункт «1 Ленивый Pro» —
  домен в цикле с подсказками (`0` — назад, пустой ввод и мусор — подсказка, не тупик), DNS не сюда — показать, куда
  указывает, и перепроверять раз в 15 с до 5 минут (Enter — сейчас, `0` — другой домен), проверки словами, установка
  шагами со спиннером, итог: сайт, адрес панели и пароль один раз (`_panel_password_pending_issue`), ключи со
  страницами и QR по номеру, «Привязать Telegram-бота?» — токен скрытым вводом → привязка сообщением
  (`_bot_install_interactive pair`: без экрана выбора администратора). Отказ, от которого первый экран уже отказался
  подхватывать, уважается (`GA_MODES_ADOPT=0`). «Режим и сайт → Ленивый Pro» — тот же сценарий на установленном сервере
  с подтверждением перед установкой; Enter на вопросе о домене — нынешний домен.

### 4.8 `lib/agents.sh` (PLAN §6.10, §5.1)

| Функция | Что делает / печатает | Коды |
|---|---|---|
| `agents_read` | `agents.json` в stdout (нет файла → `{"schema":1,"agents":[]}`); битый файл — ошибка, а не пустой список: следующая запись затёрла бы доступы | `agents_corrupt` |
| `agents_list` | stdout JSON-массив без `secret` и без `token`; `expired`, `state` (`active` \| `disabled` \| `expired`), `require_token`, `weak` (нет второго фактора), `pin_ip`, `pinned_ip` считаются здесь | — |
| `agents_add <label> [scope=read] [days=30] [allow_ips] [port] [pin_ip=off] [mode=static]` | секрет И токен `openssl rand -base64 32` → base64url по 43 символа, `require_token: true`, `id=a-<4–8 hex>`, запись `agents.json` 0600 атомарно (`_ga_write_json`), адрес по режиму **до записи** (в `lite` без порта — отказ); пустой `days` = 30 (`GA_AGENTS_DEFAULT_DAYS`), `0` — бессрочно с предупреждением; персональный порт → сертификат и перезапуск юнита (`force`); stdout — карточка (`ga_agents_card`: поля выше + `url`, `token`, `connect`) | `invalid_label`, `invalid_scope`, `invalid_days`, `invalid_ips`, `invalid_port`, `invalid_state`, `port_busy`, `needs_site`, `no_domain`, `no_server_ip`, `secret_failed`, `write_failed` |
| `agents_show <id>` | карточка снова (readonly, но событие аудита `agent-show`) | `invalid_agent_id`, `agent_not_found` |
| `agents_harden <id>` | выдать токен доступу без второго фактора: `token` + `require_token: true` → карточка; прежний способ (один адрес) перестаёт работать со следующего запроса | те же + `already_strong` |
| `agents_pin <id> <on\|off>` | привязка к адресу: `pin_ip`, `pinned_ip` обнуляется в обе стороны (включить и «перепривязать» — одно действие); stdout `{id, pin_ip, pinned_ip}` | те же + `invalid_state` |
| `agents_rotate <id>` | новые секрет И токен (плюс `require_token: true`) → карточка; старый адрес и старый токен умирают на следующем запросе сервера. У `rolling` — новая цепочка и снятие заморозки; **сервер замечает перевыпуск сам** и снимает бан с адресов доступа (§5.1) | те же |
| `agents_extend <id> <days>` | продлить срок, **не трогая ни секрет, ни токен**: клиента агента перенастраивать не нужно. Отсчёт от текущей даты окончания (`ga_agents_iso_extend`), у истёкшего и бессрочного — от сегодня; `days=0` снимает срок совсем и печатает `agents_warn_forever`. Пустой `days` — забытый аргумент, а не «бессрочно» (`invalid_days`, rc 2). stdout `{id, expires, old_expires, days}`, аудит `agent-extend`. Отдельное действие, а не «перевыпустить со сроком»: истечение — календарь, а не компрометация, и менять из-за него секрет значит останавливать работу агента | `invalid_agent_id`, `agent_not_found`, `invalid_days`, `write_failed` |
| `agents_enable` / `agents_disable <id>` | stdout `{id, enabled}` | те же |
| `agents_del <id>` | stdout `{id, deleted: true}`; доступ с портом → перезапуск юнита | те же |
| `agents_http_status` | stdout объект статуса (см. §4.7 `mcp-http-status`); `unit_state` из `systemctl` (`dry` в dry-run) | — |
| `agents_http_on [port]` | `pro`/`lite_site`: `website_mcp_location_on` (порт игнорируется с предупреждением); `lite`: без порта и без персональных портов → `needs_site`, с портом → `mcp.public_port`, открытие порта в локальном файрволе (`preflight_open_local`, если закрыт), `website_selfsigned_ensure`; затем `mcp.http_enabled=true`, юнит `goamnezia-mcp-http.service` в `${GOAMNEZIA_SYSTEMD_DIR:-/etc/systemd/system}` (`ExecStart=/usr/bin/env python3 $MCP_DIR/server.py --http --bind <127.0.0.1\|0.0.0.0> --port <N> [--tls site.crt,site.key \| --extra-tls …]`, `Restart=always`, `User=root`, `Environment=GOAMNEZIA_DIR/ACTION_BIN/AUDIT_LOG`), `systemctl enable --now` + `restart`; повтор — ремонт; stdout — статус | `needs_site`, `invalid_port`, `port_busy`, `unit_failed`, `config_write_failed`, коды сертификата и nginx |
| `agents_http_off` | location снимается (`website_mcp_location_off`, в `lite` — только флаг), юнит `disable --now` и удаляется, `mcp.http_enabled=false`; доступы остаются; идемпотентно | `config_write_failed` |
| `agents_http_reconcile [force]` / `_agents_unit_reconcile_locked` | при включённом транспорте пересобрать юнит под текущий режим (`ga_agents_listen_plan`: `pro`/`lite_site` → `127.0.0.1:<http_port>`; `lite` → `0.0.0.0:<public_port>` с TLS, без публичного порта, но с персональными — петля + `--extra-tls`; ничего → юнит останавливается с предупреждением, флаг остаётся); зовётся из `_ga_site_set_mode` (`lib/website.sh`) после каждой смены режима | `unit_failed` |

Все мутации — пары `fn` / `_fn_locked` под `ga_with_config_lock`; dry-run печатает `systemctl` и
`ufw`/`iptables` как `DRY: …`, юнит пишет только в заданный `GOAMNEZIA_SYSTEMD_DIR`, сертификат
создаёт по-настоящему. Словари — `lib/lang/{ru,en}.d/agents.sh` (ключи `agents_*` модуля, `ag_*`
экрана меню, `act_agent_*`/`act_mcp_http_*` моста). Проверки — `tests/test_agents_dryrun.sh`
(функции напрямую + живой `server.py --http` на выданном `agents.json`: второй фактор — адрес без
заголовка и с чужим токеном → `404`, со своим → `200`; совместимость доступа без `require_token`;
привязка к адресу и её снятие; усиление старого доступа; уведомления в очереди бота; отзыв,
перевыпуск, учёт вызовов) и раздел 13 `tests/test_actions_dryrun.sh` (через `install.sh --action`).

### 4.9 `lib/upstream.sh` (PLAN §6.8) — обновления из официального репозитория Amnezia

```
upstream_check                                   # stdout JSON: {checked, sources{github, dockerhub: ok|unreachable}, client_release{tag, current, published,
                                                 #   prerelease, url, update}, images{<ключ>: {repo, pinned_tag, pinned_digest, latest_tag, latest_digest, latest_amd64_digest,
                                                 #   latest_updated, alias_latest_digest, reachable, update}}, scripts{awg: {tag, checked, changed[], unchanged[], missing[]}},
                                                 #   placeholders{"awg/template.conf": {new[], removed[]}, …}, unsupported[{file, name}], profiles[{profile, container, image_key,
                                                 #   current_tag, current_digest, available_tag, available_digest, update, previous_tag, previous_digest, rollback_available}],
                                                 #   updates_available, hint[, warning]}. Ничего не меняет. Обе точки недоступны → код unreachable (rc 1, stdout пуст);
                                                 #   одна → success с warning. Скрипты качаются только когда тег релиза отличается от client_release.
upstream_update <profile> [tag]                  # бэкап (create_backup) → скрипты релиза в $UPSTREAM_DIR/awg/<тег>/ + previous/ + установка новых
                                                 #   (awg/start.sh пересобирается патчем ga_up_patch_start_sh из start.sh.upstream) → docker_pull_ref repo@digest →
                                                 #   amnezia_core_update → при провале откат образа (по дайджесту, а если его нет — по тегу: у подхваченного профиля
                                                 #   дайджеста нет) и скриптов, код update_failed; откат не состоялся — код update_failed_no_rollback и сообщение
                                                 #   с именем контейнера (об откате, которого не было, не говорим) → config .profiles.<p>.previous,
                                                 #   images.json (рабочий). stdout JSON {profile, container, image_tag, image_digest, previous_tag, previous_digest,
                                                 #   client_release, scripts{tag, changed[], unsupported[]}, backup, rolled_back:false}
upstream_rollback <profile>                      # previous → прежний образ (docker_pull_ref) СНАЧАЛА, затем previous/ скрипты (если менялись) → core_update → previous=null;
                                                 #   stdout {profile, container, image_tag, image_digest, rolled_back_from_tag, scripts_restored}; код no_previous
upstream_core_update <profile>                   # core-update: закреплённый образ images.json без смены скриптов (тот же дайджест = ремонт: repair=true)
upstream_tick                                    # таймер: upstream_check → $GOAMNEZIA_DIR/upstream/last_check.json; при новизне — событие upstream_available
                                                 #   один раз на пару «релиз + дайджесты» (notified.json); без сети — только лог; stdout {checked, updates_available, notified, reachable}
upstream_last_check                              # сохранённый JSON последней проверки или null
ga_up_profiles_current                           # профили с текущими версиями и rollback_available без сети (ответ unreachable в мосте)
```
Сеть — только `ga_up_fetch` (curl, connect 8 с, всего 20 с; фикстуры `GOAMNEZIA_UPSTREAM_FIXTURE_DIR`
имеют приоритет; dry-run без фикстур — `unreachable`). Тег релиза — первый не-draft из СПИСКА
релизов (`releases/latest` пропускает пре-релизы); образ — новейший версионный тег Docker Hub
(`^[0-9]+(\.[0-9]+){1,3}$`, `sort -V`), `latest` — справка; у `amnezia-wg` версионных тегов нет —
сравнивается дайджест `latest`. Новые плейсхолдеры `$…` в `template.conf`/`configure_container.sh`
→ `unsupported` («не поддержан»), обновление их не подставляет. Словарь `lib/lang/<lang>.d/upstream.sh`.

### 4.10 `lib/import.sh` (PLAN §6.5) — импорт существующей установки Amnezia

```
import_scan                                      # docker ps -a (и остановленные тоже, docker_ps_all) + регексп приложения → stdout JSON [{container, state (running|exited|
                                                 #   restarting), port, proto, kind (awg|legacy|unsupported|other), protocol, profile, managed,
                                                 #   suggested_profile (amnezia-awg1/2/3 → awg1/2/3, amnezia-awg → awg1), adoptable, reason (managed|profile_exists|no_conf|
                                                 #   unsupported_container|unknown_kind), reason_text (причина словами на языке сервера), line, subnet, subnet_overlap
                                                 #   (профили, с чьей подсетью совпадает подсеть контейнера; подхвату не мешает, SUBNET-01), listen_port, iface,
                                                 #   server_pubkey, image, peers, clients[{pubkey, name, ip, created, known}]}]
                                                 #   kind=unsupported — прочие контейнеры приложения Amnezia (protocol: xray, openvpn, cloak, shadowsocks, wireguard, ipsec,
                                                 #   ssxray, dns, tor, sftp, socks5proxy): goAmnezia работает только с AmneziaWG и их не подхватывает, но называет.
                                                 #   У остановленного порт — из его конфига (Docker портов не показывает). Демон не отвечает — отказ docker_unreachable.
                                                 #   Конфиг читается через awgkeys.py parse-conf (без секретов; и у остановленного — docker cp), имена — из clientsTable.
import_adopt <container>                         # KMOD-02 (с 1.0.12): I1–I5 конфига контейнера — по правилу adopt (ga_amn_ipacket_reason … adopt): беда
                                                 #   (<rc -5>, незнакомый тег, больше 1280 байт) → отказ params_invalid словами (import_err_ipacket) до всякого
                                                 #   изменения; тег, который понимает не каждая реализация (<c>; <d> <ds> <dz>), — подхват идёт, а в ответе
                                                 #   ipacket_warnings — слова (ga_amn_ipacket_warnings, amnezia_warn_ipacket_*), то же строкой в SSH и в
                                                 #   сообщении моста; бот и панель показывают их после подхвата. Остальные правила линейки к чужому
                                                 #   контейнеру не применяются.
                                                 #   С 1.0.13: пределы того, что пишем мы (§4.2, тайминги 3.1 вне коридора, связка таймингов, всплеск
                                                 #   Jc × Jmax, Jc + число I, хвосты 3.1 + разные S + широкие H), — тоже не отказ: в ответе param_warnings —
                                                 #   слова (ga_amn_limits_warnings, amnezia_warn_*, с «Поправьте в «Изменить один параметр» карточки профиля»»), то же строкой в
                                                 #   SSH. Значения записываются как в контейнере; править их потом — по правилам записи.
                                                 # Затем снимок контейнера как он есть (конфиг, clientsTable, ключи сервера, start.sh) в
                                                 #   $GOAMNEZIA_DIR/import-snapshots/<контейнер>-<время>/ (700/600, вне backups/; без снимка —
                                                 #   отказ snapshot_failed, ничего не изменено); затем
                                                 #   профиль в config.json (линейка по конфигу, preset imported, контейнер как есть, iface wg0 у legacy, образ приложения),
                                                 #   state/ (awg0.conf, ключ сервера 0600, clientsTable), пользователи из [Peer]+clientsTable без приватных ключей
                                                 #   (name — латинизированное clientName с суффиксом при совпадении, label — оригинал, psk из конфига, imported:true);
                                                 #   контейнер не пересоздаётся; на живом сервере — amnezia_state_sync (start.sh, /opt/warp). Подсеть на сетях Docker —
                                                 #   отказ (ga_amn_subnet_check … net); совпавшая с другим профилем — НЕ отказ (SUBNET-01: приложение ставит все свои
                                                 #   контейнеры AmneziaWG на 10.8.1.0/24, а старому на wg0 подсеть не сменить — был тупик): контейнер берётся как
                                                 #   есть, в ответе subnet_overlap — запись ga_amn_subnet_overlaps (с кем, выход), в stderr — то же словами. Порт —
                                                 #   против портов других профилей в config.json (port_busy). Остановленный контейнер
                                                 #   после записи запускается (docker start + ga_amn_wait_up; не поднялся — подхват всё равно состоялся, started=false).
                                                 #   stdout {profile, container, line, port, subnet, iface, image_tag, image_digest, users_added, users[], peers, hint,
                                                 #   snapshot, state_before, started, subnet_overlap}
import_reconcile <profile> [apply]               # пиры контейнера против users.json: new (в контейнере, не у нас) и gone (у нас active, в контейнере нет);
                                                 #   apply — новые становятся импортированными пользователями, исчезнувшие получают active=false (запись остаётся);
                                                 #   new — только те, кого подхват возьмёт; пир, которого не подхватить (PublicKey/PresharedKey не ключ,
                                                 #   ключ у пользователя другого профиля, нет IPv4 в AllowedIPs), — в skipped с причиной и строкой о пропуске
                                                 #   (её же видно в stderr) и расхождением не считается (KEENETIC-I1-N N2);
                                                 #   GA_RECONCILE_QUIET_SKIPPED=1 — строк о пропуске на stderr нет, skipped[] в ответе
                                                 #   есть (SSH «Подхватить?» показал их до вопроса, V4);
                                                 #   stdout {profile, container, in_sync, new[{pubkey, ip, name}], gone[{name, pubkey}],
                                                 #   skipped[{key — начало ключа, reason: key|taken|ip, message}], applied, added, deactivated, users[]}
```
`install.sh` в интерактивном запуске (`_import_reconcile_prompt`) сверяет все установленные
профили с живыми контейнерами и предлагает «подхватить изменения» (`import_reconcile_ask`).
Словарь `lib/lang/<lang>.d/import.sh`; `awgkeys.py parse-conf [--with-secrets]` — разбор
серверного конфига в JSON `{line, params, address, subnet, listen_port, server_pubkey,
server_privkey_present, peers[{pubkey, allowed_ips, ip, psk_present[, psk]}][, server_privkey]}`.

### 4.11 `lib/stats.sh` — статистика трафика по суткам

```
stats_tick                                       # таймер goamnezia-stats (10 мин): amnezia_show по профилям → дельты (показание меньше прошлого = рестарт → дельта = показание)
                                                 #   → $GOAMNEZIA_DIR/stats.json {schema, updated, last{"<профиль>/<pubkey>": {rx, tx}}, days{"YYYY-MM-DD": {rx, tx, profiles{}, users{}}},
                                                 #   hours{"YYYY-MM-DDTHH" (UTC): {rx, tx, active}}} — 30 суток и 192 часа, schema 1 (hours добавочный, HM-1); stdout {day, profiles[{profile, rx, tx}], total{rx, tx}, updated}
stats_summary                                    # stdout {today{date, rx, tx, by_profile, by_user}, month{from, rx, tx, by_profile, by_user}, days[{date, rx, tx}], hours[{hour, rx, tx, active}] (168 ч), updated} — в traffic
```

### 4.12 `lib/timers.sh` — таймеры systemd продукта

```
timers_install                                   # goamnezia-{quota,watch,stats,upstream}.{service,timer} в ${GOAMNEZIA_SYSTEMD_DIR:-/etc/systemd/system}: oneshot,
                                                 #   ExecStart=/bin/bash $GOAMNEZIA_DIR/install.sh --action=<x>-tick --json, Environment=GOAMNEZIA_WHO=timer;
                                                 #   quota OnBootSec=3min/OnUnitActiveSec=5min, watch 2min/2min, stats 4min/10min, upstream OnCalendar=*-*-* 05:10:00 Persistent=true
                                                 #   RandomizedDelaySec=1h. Идемпотентно: файл переписывается только при изменении, enable --now — если таймер не включён;
                                                 #   на живом сервере ещё set_backup_schedule daily (если расписание не задано) и logb_apply_all. stdout {changed[], enabled[]}
timers_remove                                    # disable --now + удаление (снос установки)
timers_status                                    # stdout [{name, timer, action, installed, state: active|inactive|missing|dry, owned}] — в quality.timers
                                                 #   ДЕВЯТЬ строк: свои пять (owned=true, их ставит этот модуль) плюс чужие backup, logbudget, logrotate,
                                                 #   cert-renew (owned=false, их ставят свои модули, мы только смотрим). У cert-renew, когда пакетный
                                                 #   certbot.timer делает продление сам, timer=certbot.timer и installed=true — своего юнита там и не должно быть.
```
Зовётся из мастера (после `installed=true`), из миграции и при каждом интерактивном запуске
(`_timers_apply`). В dry-run юниты пишутся в `GOAMNEZIA_SYSTEMD_DIR`, `systemctl` — `DRY: …`.

### 4.13 `lib/pages.sh` + `lib/pages_server.py` + `lib/clients.json` — страница подключения (docs/CLIENTS.md)

```
pages_create <name> <ttl> [profiles] [mirror]    # только сайт на домене с доверенным сертификатом (ga_site_domain_ready, иначе pages_need_domain, rc 2);
                                                 #   ttl ^([1-9][0-9]{0,2}h|[1-9][0-9]{0,2}d|forever)$ (часы ≤ 720, дни ≤ 365 → invalid_ttl); profiles — подмножество
                                                 #   профилей с пиром пользователя (пусто — все; invalid_profiles / peer_not_found); mirror on|off (invalid_toggle);
                                                 #   все выбранные профили без приватного ключа (has_key false — ключ из приложения Amnezia) → no_private_key
                                                 #   (rc 3), часть — log_warning и их список в imported_profiles;
                                                 #   выключенный пользователь → user_disabled; ≥ 200 страниц → page_limit. Каталог pages/<id>/ (0700) занимается голым
                                                 #   mkdir, secret (0600, 43 base64url) и meta.json (0600, без секрета) — атомарно; затем служба goamnezia-pages
                                                 #   (юнит в ${GOAMNEZIA_SYSTEMD_DIR:-/etc/systemd/system}, enable --now, restart при смене текста) и
                                                 #   website_pages_location_on (site.pages_location, vhost, reload). stdout {id, url, user, created, expires, forever, ttl,
                                                 #   profiles, mirror, imported_profiles}; url = https://<domain>/p-<секрет>/ — секрет; аудит page-create только с id
pages_list [name]                                # stdout {pages: [{id, user, created, expires, forever, ttl, profiles, mirror, visits, last_visit}], count} — без адресов;
                                                 #   readonly: истёкшие не показывает и не снимает (уборка только под замком — pages_tick)
pages_url <id>                                   # stdout {id, url} с секретом; аудит page-url; invalid_page_id (rc 2), page_not_found (rc 3, в том числе истёкшая)
pages_revoke <id> [sub on|off] [keys on|off]     # stdout {id, user, sub_rotated, sub_present, keys_reissued, keys_failed}; sub=on — и перевыпуск подписки (_subs_rotate_locked); keys=on — и ключей на профилях страницы (_users_reissue_peer_locked), с sub=on — и на профилях линий подписки (ga_pages_revoke_key_profiles); секрет стирается первым; последняя страница снимает локацию и службу; аудит page-revoke
pages_revoke_all [name]                          # stdout {revoked}
pages_tick                                       # stdout {expired, active} — истёкшие сняты (аудит page-expire), без страниц обвязка снимается; зовёт watch-tick
pages_reconcile                                  # обвязка по факту: есть страницы и домен готов → служба и локация включены, нет страниц → сняты;
                                                 #   зовётся из каждого pages_tick (_pages_reconcile_locked)
_pages_mode_off_locked                           # сайт ушёл с домена (_website_disable_site_locked / _website_disable_pro_locked lite): все страницы
                                                 #   отозваны (аудит page-revoke reason=mode), служба и флаг сняты
clients_list [platform] [line]                   # stdout {checked, lines, platforms, apps[]} из lib/clients.json; support "no" по линейке снимает платформу;
                                                 #   invalid_platform / invalid_line / catalog_missing / catalog_corrupt
```
Служба `lib/pages_server.py` (stdlib, Python ≥ 3.8, `127.0.0.1:${pages.port}`): маршруты под `/p-<секрет>/` —
`` (страница), `app.js`, `key/<profile>.conf`, `qr/key/<profile>/conf.png`, `qr/key/<profile>/vpn.png` и `vpn-<n>.png`
(чанки QR приложения Amnezia), `qr/link/<n>.png` (только ссылки каталога по номеру), `qr/mirror/<app>/<platform>.png`,
`mirror/<app>/<platform>` (`X-Accel-Redirect: /_ga_mirror/<app>/<platform>/<файл>` + `Content-Disposition`),
`mirror/<app>/<platform>/state` (GET, JSON), `mirror/<app>/<platform>/request` (POST → файл `request` в паре, JSON;
`404 not_mirrorable`; та же сборка уже в очереди или загружается — 200 `already: in_progress` без второй загрузки,
готова — `already: ready`; у страницы с `mirror=off` маршрутов зеркала нет — 404). Ключи — у моста `user-show` + `user-key
--format=all` (кеш ≤ 60 с, сброс по mtime `users.json`/`config.json`; без `qrencode` — `conf` и `vpn` по отдельности;
пир с `has_key: false` — справка `{id, line, imported: true, seen}` без вызова `user-key`, ответ `user-key`
`no_private_key` — то же; файл и QR такого профиля — 404, страница из одних справок — 200, а не 404);
выключенный пользователь → страница 200 «Доступ приостановлен» (причина `quota`/`expired`/`admin` и что сделать), без
ключей: `keys.json` — `{suspended}`, файлы и QR ключей — 404;
удалённый пользователь, истёкшая или отозванная страница, чужой секрет, чужой путь → 404 байт в байт
как у nginx и без защитных заголовков службы (только `Server`, `Date`, `Content-Type`, `Content-Length`); промахи по
адресу клиента (заголовки `X-Real-IP`/`X-Forwarded-For` — только с петли) → бан 10/5 мин → 15 мин, таблицы промахов и
банов ограничены и чистятся; путь запроса в журнал не пишется. Копия зеркала для страницы `ready`, пока на диске есть
`meta.json` и файл (провал сверки — поле `error`); `downloading` без живого замка — `failed`/`stale`. Заголовки: `Content-Security-Policy: default-src 'none'; img-src 'self'; style-src 'self' 'unsafe-inline';
script-src 'self'; connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'`, `X-Robots-Tag:
noindex, nofollow`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store`, `Server: nginx`. Визиты — `state.json`
страницы (`visits`, `last_visit`, без адреса), аудит `page-visit` раз в час на страницу (без адреса), `mirror-request`
на заявку; JSON данных внутри `<script>` экранирует `<`, `>`, `&` как `<`, `>`, `&`; путь запроса в
журнал службы не пишется (только длина и первые три символа), остальные строки проходят через маску `p-***`.

### 4.14 `lib/mirror.sh` — зеркало сборок приложений (docs/CLIENTS.md §8)

```
mirror_fetch <app> <platform>                    # только пары с mirror в каталоге (unknown_app, not_mirrorable; ios → invalid_argument); пара под замком → mirror_busy;
                                                 #   state.json queued, старт в .fetches, .last_fetch; загрузка в фоне (systemd-run --unit=goamnezia-mirror-<app>-<platform>,
                                                 #   иначе nohup с worker.pid; dry-run и GOAMNEZIA_MIRROR_FOREGROUND=1 — в этом процессе). stdout {app, platform, state}
ga_mirror_worker <app> <platform>                # releases/latest → asset по regex (jq test) → digest sha256 из API обязателен (иначе mirror_unverified, без загрузки) →
                                                 #   пределы mirror.max_file_mb (mirror_too_big), mirror.max_total_mb и свободное место ≥ размер + 1 ГиБ (mirror_no_space) →
                                                 #   curl -fsSL --proto '=https' --max-filesize <размер> во .<файл>.part рядом → размер (mirror_fetch_failed) и sha256 (mirror_unverified) →
                                                 #   LICENSE с тега (LICENSE, LICENSE.md, LICENSE.txt, COPYING; иначе имя лицензии и ссылка), SOURCE.txt → атомарный mv,
                                                 #   meta.json {app, platform, version, file, size, sha256, fetched, source_url, download_url, repo, license},
                                                 #   прежняя версия удаляется после; state.json ready; аудит mirror-fetch … start / ok / error=<код>
mirror_remove <app> <platform>                   # stdout {app, platform}; под замком → mirror_busy; аудит mirror-remove
mirror_list                                      # stdout {max_file_mb, max_total_mb, used_mb, used_bytes, items: [{app, platform, state, version, file, size, sha256,
                                                 #   fetched, error, message, bytes, updated, busy, source_url, published}], count}
ga_mirror_published_file / ga_mirror_stale       # «копия готова» = meta.json + файл на диске (что бы ни лежало в state.json); «брошена» = downloading (или queued
ga_mirror_normalize                              #   без заявки старше 120 с) без живого замка .lock/ → failed/stale; normalize пишет факт в state.json (зовёт тик)
mirror_tick [daily]                              # сначала ga_mirror_normalize у каждой пары; заявки request со страниц: готовая или качающаяся пара — заявка снимается
                                                 #   (опубликованной возвращается ready); пара, которую качали < 10 мин назад, или
                                                 #   ≥ 6 стартов за час (.fetches) — deferred (state queued, deferred: true), иначе _mirror_fetch_locked; daily — у каждой
                                                 #   опубликованной пары сверяется tag_name релиза и новая версия подтягивается. stdout {requests, started, deferred, checked, updates}
bash lib/mirror.sh --worker <app> <platform>     # запуск файлом для фона: common.sh + i18n, ga_mirror_worker, без моста и без общего замка
```

## 5. MCP-сервер `goamnezia-mcp/server.py` (stdlib, Python ≥ 3.8)

- Транспорт stdio: строки JSON-RPC 2.0 через stdin/stdout (одно сообщение на строку, UTF-8). **В stdout — только протокол**, логи в stderr (`GOAMNEZIA_MCP_LOG=debug`).
- `initialize` → `{protocolVersion: "2025-06-18", capabilities: {tools: {listChanged: false}, resources: {subscribe: false, listChanged: false}, prompts: {listChanged: false}}, serverInfo: {name: "goamnezia", version: <из config.json или "0.0.0">}, instructions: <краткое RU/EN описание, что это и как безопасно пользоваться>}`. Поддерживать также запросы с `protocolVersion` `2025-03-26` и `2024-11-05` (отвечать своей версией).
- `notifications/initialized` — принять молча. `ping` → `{}`.
- `tools/list` — из `actions.json`: `name` = поле `mcp`, `description` = RU + EN через ` / `, `inputSchema` — JSON Schema объекта из `params` (тип, `pattern`/`enum`/`minimum`/`maximum`, `required`, `default`), для `destructive: true` добавить обязательный `confirm` (string, описание: «повторите значение первого параметра»), `annotations: {readOnlyHint, destructiveHint, idempotentHint: false, openWorldHint: false}`.
- `tools/call` → проверить схему (обязательные, регексы, enum, границы) → для destructive сверить `confirm` с первым параметром → выполнить `[$GOAMNEZIA_ACTION_BIN, "--action=<cli-name>", "--json", "--k=v"…]` с таймаутом 300 с → последняя JSON-строка stdout → результат `{content: [{type: "text", text: <JSON pretty>}], structuredContent: <объект>, isError: status != "success"}`. Для `user_get_key` дополнительно `{type: "image", data: <base64 PNG>, mimeType: "image/png"}` на каждый QR из ответа (`qr_conf_png_b64`, `qr_vpn_png_b64[]`). Ошибка запуска → `isError: true`, текст с кодом `transport`.
- `resources/list` / `resources/read` — из `resources` в `actions.json` (`mimeType: application/json`); `resources/templates/list` → пусто.
- `prompts/list` / `prompts/get` — из `prompts`; текст подсказки на языке `GOAMNEZIA_LANG` (ru по умолчанию), перечисляет, какие инструменты вызвать и в каком порядке.
- Неизвестный метод → JSON-RPC error `-32601`; невалидный JSON → `-32700`; ошибка схемы → `-32602`.
- Аудит: каждая `tools/call` → строка в `$AUDIT_LOG` (`GOAMNEZIA_AUDIT_LOG`, по умолчанию `/var/log/goamnezia-audit.log`): время, `mcp`, инструмент, параметры без секретов, статус.
- **Документация в двух слоях.** Для человека — `goamnezia-mcp/README.md` (что это, подключение из
  Claude Code / Claude Desktop / Cursor, модель безопасности, таблица инструментов, проверка через
  официальный MCP Inspector `npx @modelcontextprotocol/inspector`). Для агента — всё доступно по
  протоколу: (1) `instructions` в ответе `initialize` — 10–15 строк: что за сервер, с чего начать
  (`server_status`, `profiles_list`), правило `confirm`, что ключи выдаёт только `user_get_key`,
  где искать полный гайд; (2) описания и схемы инструментов, подсказки-сценарии; (3) ресурс
  `goamnezia://guide` (`mimeType: text/markdown`) с полным гайдом для агента — файл
  `goamnezia-mcp/AGENT_GUIDE.md` (RU + краткая EN-часть): каталог инструментов с «когда
  применять», порядок операций для типовых задач, коды ошибок моста и что с ними делать, примеры
  вызовов, ограничения (линейки несовместимы, серверные параметры требуют перевыпуска ключей,
  UDP/443 у одного профиля) и договор о режимах в редакции владельца от 10.09.2026 (`lite_site` и
  `pro` — один и тот же сайт на домене, разница только в месте AmneziaWG; самоподписанный
  сертификат — запасной путь обоих, и именно он, а не название режима, решает, что сейчас
  доступно). Ресурс добавить в `resources` `actions.json` записью
  `{"uri": "goamnezia://guide", "file": "AGENT_GUIDE.md", "mimeType": "text/markdown"}` — сервер
  читает файл рядом с собой.
- Соответствие спецификации MCP `2025-06-18` — обязательное; своих расширений протокола нет.
- **Действия с `"mcp": null`** в `actions.json` (управление доступами агентов `agent-*`,
  `mcp-http-*`) сервер не публикует как инструменты: агент не должен уметь выдавать или
  продлевать доступы.

### 5.1 HTTP-транспорт и доступы агентов (PLAN §6.10)

- `server.py --http [--bind 127.0.0.1] [--port 1986] [--tls cert.pem,key.pem]` — Streamable HTTP
  по спецификации: `POST /a-<secret>/mcp` (JSON-RPC в теле, ответ `application/json`;
  пакеты-массивы поддерживаются), `GET` → `405`, всё остальное → `404` с телом как у nginx по
  умолчанию (`<html><head><title>404 Not Found</title>…`). Проверка `Origin` (если заголовок есть,
  он должен совпадать с `Host` или быть в `mcp.allowed_origins`), `Content-Length` ≤ 1 МБ,
  заголовок `MCP-Protocol-Version` принимать, `Mcp-Session-Id` не требовать (сервер без состояния:
  каждое сообщение самодостаточно, `initialize` может приходить сколько угодно раз).
- Доступы — `/opt/goamnezia/agents.json` (0600), схема из PLAN §6.10. Секрет: 32 байта
  `secrets.token_urlsafe` (43 символа). Сравнение секретов — `hmac.compare_digest`. Файл
  перечитывается при каждом запросе по mtime.
- **Второй фактор — токен в заголовке.** У доступа есть отдельный секрет `token` (те же 32
  байта base64url), который передаётся **не в адресе**, а заголовком `Authorization: Bearer
  <token>` (схема регистронезависима). Сравнение — `hmac.compare_digest`. Смысл: адрес утекает
  буднично — снимок экрана со ссылкой, пересылка, история браузера, журнал промежуточного
  прокси, конфиг агента на чужой машине и бэкап этого конфига; заголовок ни в одно из этих мест
  не попадает, поэтому утёкший адрес сам по себе бесполезен. Запрос по верному пути без верного
  заголовка получает **тот же `404`**, что неизвестный путь: `401` подтвердил бы, что путь
  существует и нужен лишь пароль, и подобравший адрес перешёл бы к подбору токена.
  Поле `require_token`: у новых доступов `true`, у выданных раньше поля нет — умолчание `false`
  («слабый доступ, только путь»), такие доступы продолжают работать по одному адресу и помечены
  во всех списках. Запись с `require_token: true` и пустым `token` считается испорченной и не
  впускается вовсе (иначе доступ тихо понизился бы до одного пути). `agent-harden --id` выдаёт
  токен существующему доступу; `agent-rotate` меняет **и секрет, и токен**.
- **Одноразовый ключ — `mode: "rolling"`** (docs/MCP_ROTATION.md §1 в редакции §11). Второй вид
  доступа рядом с нынешним `static` (постоянный токен). Записи без поля `mode` — `static`:
  выданное раньше не ломается и молча не повышается. Умолчание при выдаче — `static`; `rolling` —
  явный выбор во всех интерфейсах, потому что обычный клиент MCP заголовок между запросами менять
  не умеет; для обычного клиента есть вид `connect` (ниже).
  - Запрос несёт два заголовка: `X-Ga-Key` — текущий ключ цепочки (43 символа base64url, как
    секрет и токен) и `X-Ga-Nonce` — одноразовый номер этой попытки (8–64 символа `[A-Za-z0-9_-]`,
    придумывает клиент; при дословном повторе тот же). Токен `Authorization: Bearer` остаётся:
    он закрывает утечку одной лишь ссылки, ключ — копирование всего клиента.
  - Ответ несёт следующий ключ **заголовком `X-Ga-Next-Key`**, а не в теле: тело JSON-RPC клиенты
    MCP пишут в отладочные журналы целиком, и ключ уезжал бы туда при каждом вызове.
  - Решение по пришедшему ключу — строго таблица §11.1: **текущий** → выполнить и выдать
    следующий; **предыдущий с тем же номером** → отдать сохранённый ответ, действие заново не
    выполнять, цепочку не вращать; **ключ из истории выданных** (последние 50 отпечатков) → клон,
    доступ **замораживается** и владельцу уходит событие; **что угодно ещё** → обычный неверный
    ключ, тот же `404` и счётчик неудач. Различение последних двух строк обязательно: иначе
    всякий, кто узнал путь, гасит чужого агента одним мусорным запросом.
  - Порядок проверок: путь и срок доступа → ключ. Промах мимо пути **не меняет** состояние
    цепочки. Успешная проверка ключа **обнуляет счётчик неудач** этого адреса.
  - Заморозка (`compromised: true`, `compromised_at`, `compromised_ip`) — не удаление: доступ
    перестаёт впускать всех, включая честного агента, и ждёт решения владельца. `agent-rotate`
    у `rolling` начинает новую цепочку и снимает заморозку («это был я»), `agent-del` отзывает
    совсем. Восстановление машины агента из вчерашней копии и два клиента на одном доступе дают
    ту же картину, что кража, — выбор за человеком.
  - **Один доступ — один клиент.** Два клиента вращают одну цепочку, и второй неизбежно приходит
    с уже сожжённым ключом. Правило идёт в выдачу и в документацию; там же — запрет держать файл
    ключа в синхронизируемой папке (iCloud, Dropbox).
  - Состояние цепочки — **отдельный файл на доступ**, `$GOAMNEZIA_STATE_DIR/agents/<id>.key`
    (по умолчанию `/opt/goamnezia/state/agents/`), права `0600`: отпечаток текущего ключа и сам
    ключ, отпечаток предыдущего с номером и **готовым ответом** на тот запрос, история отпечатков
    (50) и метка цепочки `origin` = `key_hash` записи. Каталог **постоянный**, не `/run`: тот
    чистится при перезагрузке, и цепочка обнулялась бы после каждого включения сервера. Реестр
    `agents.json` от вращения не меняется — только учёт вызовов, как и раньше.
  - Порядок на горячем пути (§10): проверить ключ → выполнить → сгенерировать следующий →
    записать состояние и дождаться `fsync` файла **и каталога** → и только потом ответить. Не
    записалось — ответ без нового ключа, `500` и просьба повторить тем же ключом: уйти вперёд
    сервера агент не должен.
  - В `agents.json` у `rolling`-записи лежит только `key_hash` — отпечаток **первого** ключа
    цепочки; сам ключ печатается один раз при выдаче и перевыпуске (поля `key`, `key_hint`
    карточки) и на сервере не хранится. `rolling` без `key_hash` — испорченная запись, `404`.
  - Событие владельцу — `agent_compromised` (`agent`, `label`, `scope`, `clone_ip`, `clone_at`,
    `last_ip`, `last_used`, `frozen`): два времени и два адреса, без секретов и без ключей.
- **Переходник — `mode: "connect"`** (docs/MCP_ROTATION.md §2–§4, §7; реализация и отступления —
  §12.6). Тот же одноразовый ключ, что у `rolling`, но ключ, номер попытки, путь и сверку времени
  ведёт `goamnezia-connect` на машине агента; клиент MCP запускает его как stdio-сервер
  (`{"command": "<python3 полным путём>", "args": ["<переходник полным путём>", "--grant", "<id>", "--dir", "<каталог настроек>"]}` —
  эту строку печатают `goamnezia-connect enroll` и `status`).
  - `agent-add --mode=connect` (`grant_mode`: `static|rolling|connect`) токена и ключа **не создаёт**.
    Запись: `transport: "connect"`, `mode: "rolling"`, `require_token: true`, пустые `token` и
    `key_hash`, `enroll_path` = base64url(SHA-256(`"goamnezia-enroll|"` + код без пробелов и дефисов,
    заглавными))[:43], `enroll_expires` = сейчас + 600 с (ISO UTC). Карточка несёт `enroll_code`
    (`GA-XXXXX-XXXXX-XXXXX-XXXXX`, алфавит без I, L, O, U, 0, 1) — **один раз**, в `agent-show` его
    нет, — а также `enroll_hint`; `connect` — **только выполнимое**: команда
    `~/bin/goamnezia-connect enroll <код> --server <base> [--fingerprint <sha256>]`, пока код на руках,
    иначе пусто; `connect_note` — пояснение, когда команды нет (привязан — строку для клиента MCP с
    полным путём печатает `goamnezia-connect status`; код истёк; ждёт привязки); `connector_hint`
    (откуда взять переходник), `enroll_pending`, `enroll_expired`, `enroll_expires`, `key_storage`;
    `url`, `token` и `warning` пусты. Выдача и перевыпуск при выключенном транспорте —
    `transport_off`: код сгорел бы раньше, чем переходнику станет куда привязываться. `agent-rotate`
    любого вида проверяет, что адрес есть (`needs_site`), ДО записи. У `rolling` поле `connect` тоже
    пусто, а пояснение про заголовки `X-Ga-Key`/`X-Ga-Next-Key` — в `connect_note`.
  - `POST /a-<enroll_path>/enroll` (тело ≤ 4 КБ, `{"key_storage": "tpm"|"file", "client": …}`): у
    ждущей записи с непросроченным кодом сервер под замком реестра пишет `rot_seed`, `token`,
    `key_hash` первого ключа, `key_storage`, `enrolled_at`, `enrolled_ip` (при `pin_ip: true` и `pinned_ip` —
    адрес привязки), стирает `enroll_path` и
    `enroll_expires` и **один раз** отвечает `200 {grant, rot_seed, token, key, label, scope}` с
    `Cache-Control: no-store`. Неверный, истёкший и использованный код — тот же `404` со счётчиком
    неудач; привязка подчиняется `allow_ips`, персональному порту (`port_exclusive`) и сроку самого
    доступа. Событие владельцу — `agent_enrolled` (`agent`, `label`, `scope`, `ip`, `storage`).
  - Рабочий путь — `POST /r-<P>/mcp`, `P` = base64url(HMAC-SHA256(rot_seed, `"path|"` + slot))[:43],
    `slot` = floor(время сервера / 60), принимаются slot−1…slot+1. Заголовки — как у `rolling`
    (`Authorization`, `X-Ga-Key`, `X-Ga-Nonce`, ответ `X-Ga-Next-Key`). Постоянный путь
    `/a-<secret>/mcp` у записи с `rot_seed` или ждущей привязки — `404`; промах мимо окна
    состояние цепочки не меняет.
  - Сервер следит за своими часами (`ClockWatch`): расхождение календарного и монотонного времени
    больше 60 с между проверками — строка в журнал и событие `server_clock_jump` (`delta_s`), не
    чаще раза в 10 минут.
  - `agent-rotate` у `connect` — новая привязка: стираются `rot_seed`, `token`, `key_hash`,
    `key_storage`, `enrolled_*`, `pinned_ip` и файл цепочки, выдаются новые `enroll_path`/`enroll_expires` и
    `enroll_code` в ответе. `agent-list` отдаёт `mode: "connect"`, `transport`, `key_storage`,
    `enroll_pending`, `enroll_expired`, `enroll_expires` и **не** отдаёт `rot_seed` и `enroll_path`.
- **Привязка к адресу** (`pin_ip`, по умолчанию выключена, предлагается при выдаче): при первом
  успешном вызове сервер записывает `pinned_ip` и дальше принимает только этот адрес, остальные —
  `404`. Отдельная страховка на случай, когда утекли и адрес, и токен. Снимается
  `agent-pin --id --state=off` (обнуляет `pinned_ip`); `--state=on` тоже обнуляет его — включить и
  «перепривязать» это одно действие.
- **Срок по умолчанию — 30 дней** (`GA_AGENTS_DEFAULT_DAYS`): пустой `days` означает «не указали».
  `days=0` (бессрочно) остаётся явным выбором и сопровождается предупреждением во всех
  интерфейсах. Истёкший доступ — `404`, как выключенный.
- **Уведомления владельцу** (очередь бота, docs/BOT.md §6): при первом успешном вызове доступа —
  `agent_first_use`, при вызове с адреса, которого нет в `known_ips` (последние 10) —
  `agent_new_ip`, при запросе по верному пути с неверным или отсутствующим токеном —
  `agent_token_failed` (не чаще раза в час на доступ, чтобы сканер не устроил рассылку). Поля:
  `agent`, `label`, `scope` (кроме тревоги), `ip`. Ни секрета, ни токена в событии нет — оно
  уходит в чат. Каталог очереди — `GOAMNEZIA_NOTIFY_DIR` (по умолчанию `<LOCK_DIR>/notify`).
- Уровни: `read` → инструменты с `readonly: true` и без `secret`; `write` → все с
  `destructive: false`; `admin` → все. Фильтрация в `tools/list`, `resources/list` (ресурсы — всем
  уровням), `prompts/list` (подсказки показываются, если все их шаги доступны). Вызов вне уровня →
  JSON-RPC ошибка `-32003` `forbidden`.
- **Уровень `read` сужен внутри `logs_tail`** (DEFECTS §1d.2). Инструмент помечен `readonly` и
  потому попадал в `read` целиком, а два его компонента «наблюдателю» не принадлежат:
  `component=system` — весь журнал машины от root (чужие службы, ssh, sudo, попытки входа),
  `component=mcp` — **весь** аудит-лог, то есть действия владельца, бота, панели и всех остальных
  агентов, хотя руководство обещает агенту «аудит твоих же вызовов». Поэтому **по HTTP**:
  `system` доступен только уровню `admin` (иначе `-32003` до запуска моста, журнал даже не
  читается), а `mcp` отдаётся суженным до строк этого доступа — по полю `agent` записи (старые строки
  свободного текста опознаются прежней меткой `agent=<id>`); в ответ добавляются `lines_total`, `lines_shown`,
  `narrowed_to_agent` и `message`. По stdio (SSH) сужения нет: там за клавиатурой человек с root.

### 7.1. Журнал действий — один формат на весь продукт

`/var/log/goamnezia-audit.log`: **одна строка — один объект JSON**, права `0600`. Писателей
пять, формат у всех один: ядро (`ga_audit` в `lib/common.sh`), панель (`audit()` в
`admin-web/server.py`), MCP-сервер (`Audit.write`), сервер раздачи (`lib/share_server.py`) и
служба страниц подключения (`lib/pages_server.py`, `iface: pages`, действия `page-visit`,
`mirror-request`).
До 12.09.2026 форматов было столько же, сколько писателей — TSV, JSON и свободный текст в одном
файле, — и собрать «кто что сделал» из него было нечем (DEFECTS §2.2).

Поля: `ts` (UTC ISO), `iface` (`ssh` | `panel` | `bot` | `mcp` | `share` | `pages`), `stage`, `who`,
`action`; дальше по интерфейсу — `details` (строка) у ядра и раздачи, `params` (объект без
секретов), `status`, `code`, а у MCP ещё `tool`, `kind`, `ms`, `client`, `origin` и `agent`.

`stage` отделяет **просьбу** от **случившегося**: `call` пишут интерфейсы (кто и о чём попросил,
чем ответил мост), `effect` — само ядро (что на самом деле изменилось). Одно действие из панели
даёт две записи, и это не дубль: у них разные `stage`, и по ним видно и того, кто просил, и то,
что вышло.

Секретов в журнале нет ни в каком виде: приватные ключи, токены, секретные пути и пароли
вымарываются у каждого писателя до записи. Человеку в SSH-меню журнал показывается словами
(«время · интерфейс · кто · действие · подробности → статус»); мост и MCP отдают строки **как
есть**, потому что машине нужен разбор, а не вид.
- **Предел частоты на доступ** (`RateLimiter`, MCP_ROTATION §6): `HTTP_RATE_CALLS` **удачных**
  вызовов за `HTTP_RATE_WINDOW` (120 за минуту), окно скользящее и ведётся **по доступу**, а не
  по адресу (за адресом NAT, за доступом — один клиент). Сверх предела — **`429` с
  `Retry-After`**, а не `404`: запрос уже доказал знание ключа, скрывать от него существование
  пути нечего, а молчаливый `404` отправил бы агента перевыпускать доступ впустую. Отказанный
  вызов в окно не кладётся. Владельцу — событие `agent_rate_limited` (не чаще раза в 15 минут на
  доступ). Причина запрета, а не наблюдения: каждый вызов — это запуск `install.sh` под root.
- Выключенный, истёкший, не прошедший `allow_ips` или `pin_ip` доступ, неизвестный секрет и
  верный секрет без верного токена → `404` (одинаковый ответ, чтобы не подтверждать
  существование пути). Счётчик неудач по IP: 20 за 10 минут → бан IP на 10 минут (`429` не
  отдаём — тоже `404`).
- **Первое закрепление адреса** (`pin_ip`) пишется под `config.lock` до выполнения вызова. Не
  дождались замка за `HTTP_LOCK_WAIT` (его держит долгое действие моста) или каталога замка нет
  (после перезагрузки) → **`503` с `Retry-After`**: ничего не выполнено, неудачей не считается,
  цепочка ключей не сдвинута. Уже закреплённый адрес замка не ждёт — свежую запись читает без
  него (SEC46-01). nginx пропускает этот `503` мимо перехвата: сам он в локации агентов `503` не шлёт.
- **Бан не должен бить по своему** (DEFECTS §1d.2). Отказ по СОСТОЯНИЮ доступа (`disabled`,
  `expired`, `compromised`) счётчик подбора **не трогает вовсе**: запрос пришёл по верному
  секретному пути, то есть это не перебор, а честный агент, которому ещё не сказали, что доступ
  закрыли. Прежде замороженный по §11.3 доступ за двадцать таких вызовов банил собственный адрес
  на десять минут, и ответ владельца «это был я» не действовал до конца бана. Поэтому ещё:
  **перевыпуск снимает бан** с адресов доступа (`known_ips`, `last_ip`, `pinned_ip`) — сервер
  замечает перевыпуск сам, по смене `secret`/`key_hash` или снятию `compromised` при очередном
  перечитывании `agents.json`, — и **реестр перечитывается до проверки бана**, чтобы перевыпуск
  действовал на первый же запрос. Перебор чужих путей, чужой токен и чужой ключ считаются
  по-прежнему.
- **Истечение не молчит**: доступ с прошедшим сроком отвечает тем же `404`, но владельцу уходит
  событие `agent_expired` (не чаще раза в сутки на доступ), а за `HTTP_EXPIRY_WARN_DAYS` до конца
  — `agent_expiring`, пока доступ ещё работает. Продлевают срок `agents_extend` (§4.8), без смены
  секрета и токена.
- После успешного вызова обновляются `last_used`, `last_ip` (из `X-Real-IP`/`X-Forwarded-For`
  только когда запрос пришёл **на петлевой слушатель**, перед которым стоит nginx и который
  ставит эти заголовки директивой `proxy_set_header`, перезаписывая присланное клиентом; на
  публичном слушателе — `lite` со своим TLS-портом, персональные порты доступов — nginx впереди
  нет, и адрес берётся только из сокета: иначе счётчик банов вёлся бы по адресу, который назвал
  сам стучащийся), `calls`,
  `known_ips` (последние 10) и, если включена привязка, `pinned_ip`. Аудит: `agent=<id>` вместо
  `from=<ip>`; ни секрет, ни токен в аудит и логи не пишутся (путь в логе сервера маскируется
  до `/a-***`, заголовок не логируется вовсе).
- Доступ с `port` > 0 (Lite): дополнительный слушатель на этом порту (тот же процесс), на
  котором принимается только этот доступ; на общем порту он тоже принимается, если
  `mcp.port_exclusive` не включён.
- Где живёт HTTP-транспорт (PLAN §6.10): в **обоих режимах с сайтом** — тот же nginx, что отдаёт
  сайт, и адрес зависит **не от названия режима, а от готовности сайта**
  (`ga_site_domain_ready`): сайт на домене с Let's Encrypt даёт
  `https://<domain>/a-<secret>/mcp` без отпечатка, сайт на запасном самоподписанном сертификате
  (`/opt/goamnezia/tls/site.crt`) — `https://<server_ip>/a-<secret>/mcp`, и в
  `agent-add`/`agent-show`/`mcp-http-status` рядом с адресом отдаётся `cert_fingerprint_sha256`.
  Различать `pro` и `lite_site` здесь нечего: сайт у них один и тот же, а место AmneziaWG на
  маршрут агентов не влияет.
  `lite` — nginx нет, поэтому `mcp-http-on` без `--port` отвечает `code=needs_site` с подсказкой
  включить режим с сайтом, а с `--port=N` поднимает собственный TLS-слушатель `server.py --http
  --bind 0.0.0.0 --port N --tls` на том же самоподписанном сертификате. Во всех режимах
  `server.py --http` слушает `127.0.0.1:1986`; TLS терминирует nginx, кроме варианта с
  отдельным портом.
- **Состояние транспорта различает три вещи, а не одну** (`agents_http_status` → `mcp-http-status`,
  §4.8): `configured` — транспорт включён в `config.json`; `available` — ему есть где слушать
  (`listen` = `nginx` | `public_port` | `grant_ports`, а не `none`); `enabled` — и то, и другое,
  то есть он действительно работает; `reason` (`no_site`) и `hint` называют, чего не хватает.
  Флаг `mcp.http_enabled` переживает смену режима, и одного его мало: включили транспорт в
  режиме с сайтом, ушли в `lite` — и ответ рапортовал «включено» над маршрутом, которого больше
  нет (DEFECTS §1.8). Отдельно `reachable`: `enabled` **и** живой юнит (`unit_state`) **и**
  маршрут на месте — мёртвый юнит за живой nginx-локацией даёт 502, а не работающий транспорт.
  Ту же тройку `enabled` / `configured` / `available` с `reason` (`no_site` | `no_domain` |
  `untrusted_cert`) и `hint` отдаёт `miniapp-status` (§4.7) — один источник правды на состояние и
  на отказ.
- Юнит `goamnezia-mcp-http.service` (только когда `mcp.http_enabled`), `Restart=always`,
  `User=root` (нужен доступ к мосту), `ExecStart=/usr/bin/env python3 /opt/goamnezia-mcp/server.py --http …`.
- Действия `agent-*`/`mcp-http-*` реализует `lib/agents.sh` (§4.8, готово): генерация секрета,
  запись `agents.json`, адрес — по тому же правилу, что выше, то есть по готовности сайта
  (`ga_site_domain_ready`), а не по названию режима: `https://<domain>/a-<secret>/mcp` — сайт на
  домене с Let's Encrypt (оба режима с сайтом), `https://<server_ip>/a-<secret>/mcp` с
  отпечатком — запасной самоподписанный, `https://<server_ip>:<port>/a-<secret>/mcp` — `lite`
  (`mcp.public_port` транспорта или персональный `port` доступа);
  QR адреса в SSH-меню через `qrencode -t UTF8`, nginx-location по
  регулярному выражению (одна запись в `sites-available/goamnezia`, включается `mcp-http-on`,
  `access_log off`), открытие порта в Lite (`preflight_open_local`). Интерфейсы: SSH-меню
  «Настройки → Доступы агентов (MCP)» (`_agents_screen`, docs/MENU.md §6.3), бот (экран
  `se_agents`, docs/BOT.md §5), панель (страница «Агенты», docs/PANEL.md §5). Ответы моста —
  §4.7. Доверие самоподписанному сертификату на стороне клиента — `goamnezia-mcp/README.md`
  («Подключение по HTTP»: скачать `site.crt`, `NODE_EXTRA_CA_CERTS`, сверить отпечаток).
- Тесты: `tests/test_mcp_http.py` — сервер в режиме `--http` на свободном порту петли с
  временным `agents.json`: верный секрет → `tools/list` по уровню; `read` не видит `user_add`;
  вызов вне уровня → `-32003`; выключенный/истёкший/чужой IP/неверный секрет → `404` с
  одинаковым телом; `Origin` чужой → `403`; 21 неверный запрос → бан; `agents.json` изменён на
  диске → следующий запрос видит изменение; `last_used`/`calls` растут; `GET` → `405`.
  Второй фактор: верный путь без заголовка / с чужим токеном / с пустым `Bearer` / со схемой
  `Basic` → `404` байт в байт как у неизвестного пути, с верным токеном → `200`; запись
  `require_token` без токена → `404`; доступ без `require_token` работает по одному пути;
  привязка: первый адрес принят и закреплён, второй → `404`, снятие возвращает доступ; истёкший
  с верным токеном → `404`; события `agent_first_use` / `agent_new_ip` / `agent_token_failed`
  ложатся в очередь без секретов и без токенов.
  Одноразовый ключ (`RollingKeyTests`): цепочка вращается и следующий ключ приезжает заголовком
  `X-Ga-Next-Key`, а в теле его нет; ключ из истории выданных → `404`, заморозка и событие
  `agent_compromised` с двумя временами и двумя адресами; клон первым, честный агент вторым — тот
  же исход; мусорный ключ → `404` и доступ **не** гаснет; повтор с прежним ключом и тем же номером
  отдаёт сохранённый ответ и **не** выполняет разрушительное второй раз (проверяется по числу
  строк аудита); промах мимо пути цепочку не жжёт; успешный запрос обнуляет счётчик неудач;
  состояние цепочки лежит вне `/run` и переживает его удаление; перевыпуск начинает новую цепочку
  и снимает заморозку, а ключи мёртвой цепочки становятся просто неизвестными; запись `rolling`
  без `key_hash` → `404`; `static` работает по-прежнему и заголовка с ключом не получает.
- Установка: `install.sh` копирует `goamnezia-mcp/` в `/opt/goamnezia-mcp/`, симлинк `/usr/local/bin/goamnezia-mcp` → `server.py` (shebang `#!/usr/bin/env python3`, `chmod 755`). Подключение из Claude Code: `claude mcp add goamnezia -- ssh root@HOST goamnezia-mcp`.
- Тесты `tests/test_mcp.py`: сервер как подпроцесс с `GOAMNEZIA_ACTION_BIN=tests/fixtures/fake_action.sh` (печатает заранее заданный JSON), сценарий initialize → tools/list (совпадает с `actions.json`) → tools/call `users_list` → tools/call `user_delete` без `confirm` (ошибка) и с `confirm` → resources/read → prompts/get → неизвестный метод.

## 6. Бот, панель (спринты 2–3) — только границы

Бот и панель не пишут `config.json` и `users.json` — только `--action`. Читать напрямую можно `config.json`
(без секретов); пользователей, ключи, подписку, страницы и раздачи — ответом `user-list`, `user-show`,
`user-key`, `sub-list`, `sub-show`, `page-list`, `share-list`: мостом или модулем ядра `lib/core_read.py`, который
даёт тот же ответ в процессе вызывающего — из тех же файлов, под тем же замком на чтение (`flock LOCK_SH` на
`config.lock`), с той же строкой аудита — и отдаёт мосту всё, в чём не уверен (docs/PANEL.md §5b). Своих правил
чтения `users.json` в интерфейсах нет; ключи показываются только из ответа `user-key`. Общий замок — тот же файл.

## 7. Тесты

`tests/run_static.sh`: `bash -n` (`${BASH5:-bash}`) по `install.sh`, `lib/*.sh`, `lib/lang/*.sh`, `lib/lang/*.d/*.sh`;
`shellcheck -S warning -x` (исключения перечислить в `.shellcheckrc`); `python3 -m unittest discover -s tests -p 'test_*.py'`;
`tests/check_i18n_ssh.py` (все `t key`/`tf key` из `.sh` есть в RU и EN; RU и EN совпадают по множеству ключей);
`tests/check_actions.py` (все действия из `actions.json` есть в `case` диспетчера и наоборот; `mcp`-имена уникальны;
`"mcp": null` — законная пометка «не публиковать», отсутствие поля — ошибка; `resources[].file` ищется в `goamnezia-mcp/`);
`tests/check_bootstrap_files.py` (списки `bootstrap.sh` ↔ репозиторий; файл-ресурс MCP обязан быть в списке как `goamnezia-mcp/<file>`);
сквозные прогоны без Docker: `tests/test_core_dryrun.sh` (функции модулей напрямую во временном `GOAMNEZIA_DIR`) и
`tests/test_actions_dryrun.sh` (настоящий `install.sh --action=… --json` процессом на шаг: status → profile-install → user-add →
user-key → profile-set-param (клиентский и серверный) → profile-subnet → profile-mtu → endpoint-set → user-disable/enable →
backup → user-del → profiles-status → Pro-режим со стендом `lib/website.sh` во временных каталогах (change-mode pro → status с
блоком `site` → change-template из фикстуры `tests/fixtures/site_min` → miniapp-on/rotate/status/off → site-alt-svc → site-check →
change-mode lite → отказы `needs_site` / `not_pro` в Lite); каждый ответ — ровно одна строка валидного JSON, коды как ожидается,
приватных ключей нет нигде, кроме `user-key`, секретного пути панели — нигде, кроме `miniapp-on` / `miniapp-rotate`).
`tests/test_website_dryrun.sh` — функции `lib/website.sh` напрямую (116 проверок: vhost, панель, MCP, Alt-Svc, listen, шаблон,
сертификат Let's Encrypt, `website_check`, `enable_pro`/`disable_pro`, самоподписанный сертификат — CN/SAN/права/отпечаток,
переходы `lite → lite_site → pro → lite_site → lite` без изменения `profiles` и `users.json`). Раздел 11 `tests/test_actions_dryrun.sh`
гоняет те же переходы через `install.sh --action=change-mode` с проверкой `status`, `site-check`, `change-template`, отказов `not_pro`.
Раздел 7б `tests/test_actions_dryrun.sh` — разовый адрес (§4.6): `edge-status` (`keys_host`, `key_choices`), `user-key` без и с
`--endpoint` (меняется только строка `Endpoint`, `users.json` не тронут), пустой `--endpoint=`, отказы `address_not_here` (чужой IPv4
и не настроенный домен), `invalid_ip` (`0.0.0.0`), `bad_arg` (мусор), `vpn://` с адресом в подписи, `user-show` (`key_host`,
`key_host_gone`), `status.key_hosts`, пропавший закреплённый адрес и домен, вписанный в `endpoint_host`.
`tests/test_agents_dryrun.sh` — функции `lib/agents.sh` напрямую (92 проверки: `needs_site` в `lite`, свой TLS-порт с юнитом и
сертификатом, `agents_add` → `agents.json` 0600 и адрес по режиму, `server.py --check` на этом файле, **живой `server.py --http`**
на выданном `agents.json`: уровни в `tools/list`, `allow_ips`, `disable` → 404 без перезапуска, `rotate` → старый 404 / новый 200,
учёт `last_used`/`last_ip`/`calls`; проверки входа, `agents_list` без секрета, истёкший, битый файл, персональный порт,
`http_off`, `lite_site` с location, `pro` с доменом, пересборка юнита при смене режима, секреты нигде, паритет словаря);
раздел 13 `tests/test_actions_dryrun.sh` — то же через `install.sh --action` (`mcp-http-on` в `lite` → `needs_site`, в `lite_site` →
успех с отпечатком, `agent-add` → адрес, `agent-list` без секрета, `agent-show`/`rotate`/`disable`/`enable`/`del`, `mcp-http-off`).
Спринт 4: `tests/test_upstream_dryrun.sh` (фикстуры `tests/fixtures/upstream*`: патч `start.sh` воспроизводит наш файл,
`unreachable` без сети, «есть обновление» — первый не-draft релиз, новейший версионный тег, изменившиеся скрипты и новый
плейсхолдер «не поддержан», частичная недоступность, `upstream_update awg2` со всеми шагами и `previous`, `already_current`,
откат, явный тег со второй страницы Hub, провал пересоздания → откат, `upstream_tick` с событием один раз, `core-update`,
«всё актуально», секреты нигде), `tests/test_import_dryrun.sh` (фикстуры `tests/fixtures/import`: `docker ps` и файлы
контейнеров; `import_scan` находит `amnezia-awg2` и legacy `amnezia-awg`, `import_adopt` создаёт профиль и пользователей с
пометкой `imported` без приватных ключей, legacy → `awg1` на `wg0`, `reconcile` видит новый и исчезнувший пир, ошибки),
`tests/test_timers_dryrun.sh` (юниты в `GOAMNEZIA_SYSTEMD_DIR` с ожидаемыми `OnCalendar`/`OnUnitActiveSec`, `stats_tick`
с дельтами и рестартом, сторож: `docker start`/`restart`, дедупликация событий, тики через `install.sh --action`);
раздел 14 `tests/test_actions_dryrun.sh` — то же через мост.
Страница подключения и зеркало (docs/CLIENTS.md): `tests/test_pages_dryrun.sh` (163 проверки: `pages_need_domain` в Lite и на
самоподписанном; отказы срока, пользователя, профилей, зеркала, выключенного, предела; создание — права, секрет отдельно,
`site.pages_location`, локация и internal-локация зеркала в vhost, юнит `goamnezia-pages`, аудит без секрета; список без
адресов; `page-url`; **живая служба `pages_server.py` на петле с настоящим `install.sh --action=user-key`**: страница 200 с CSP,
noindex, no-store, без внешних ресурсов, устройство по User-Agent, `app.js`, `key/awg2.conf` с `Endpoint = <домен>:<порт>`,
QR ключей и ссылок, состояние и заявка зеркала, `X-Accel-Redirect` готовой копии, 404 nginx-вида на чужой секрет и корень, 301
на косую черту, бан по `X-Real-IP`, визиты и `page-visit`, выключенный пользователь → «приостановлен» (200, файл ключа —
404) и обратно, отзыв, истечение,
`pages_revoke_all` снимает обвязку; `clients_list` с фильтрами; мост `page-*`/`clients-list`/`watch-tick`) и
`tests/test_mirror_dryrun.sh` (75 проверок на подставном GitHub — `GOAMNEZIA_MIRROR_CURL` отдаёт файлы фикстур: каталог,
dry-run без сети, удачная загрузка с проверкой sha256, LICENSE и SOURCE.txt, права 755/644, повтор той же версии без загрузки,
`mirror_unverified` без digest и при неверном sha256, несовпавший размер, `mirror_too_big`, `mirror_no_space` по каталогу и по
диску, `mirror_busy`, файл не по образцу, заявки `mirror_tick` с пределами «одна на пару в 10 минут» и «6 в час», суточная
сверка версий, `mirror_remove`, мост `mirror-*`/`watch-tick`/`upstream-tick`, запуск `--worker` файлом).
Юнит-тесты Python: `test_awgkeys.py` (генераторы по линейкам, валидатор, `conf`/`server-conf`, `vpn-encode` ↔ `vpn-decode`,
`qr-chunks`, `detect-line`, X25519 против известного вектора RFC 7748), `test_mcp.py`.
`install.sh --selftest` → `lib/selftest.sh`: без root и без Docker (через `GOAMNEZIA_DRY_RUN=1` и временный `GOAMNEZIA_DIR`).
