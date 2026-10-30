# goAmnezia pro — веб-панель: контракт установки и интеграции

Документ для того, кто правит `install.sh`, `bootstrap.sh`, диспетчер `--action=` и
nginx-шаблон: где живёт панель, что она читает и зовёт, что ждёт от ядра и как её
ставить. Код панели — `admin-web/` (`server.py`, `static/{index.html,app.js,styles.css}`,
`install_admin.sh`, `README.md`). Общие правила — [CONTRACT.md](CONTRACT.md) (§1 раскладка,
§6 границы панели); слой входа перенесён из goTelegram Pro (`AUDIT.md` §7) и дополнен вторым
равноправным способом — логином с паролем (решение владельца от 10.09.2026, §6).

## 1. Пути, порт, права

| Что | Путь | Права |
|---|---|---|
| эталон кода | `/opt/goamnezia/admin-web/` (привозит `bootstrap.sh`) | 755 / 644, `server.py` и `install_admin.sh` 755 |
| рабочая копия | `/opt/goamnezia-admin/` — `server.py`, `static/`, `token`, `password.json` | каталог **0700** |
| токен аварийного входа | `/opt/goamnezia-admin/token` (hex 64 символа, `openssl rand -hex 32`) | **0600** |
| логин и пароль входа | `/opt/goamnezia-admin/password.json` — `{login, algo, salt, hash, n/r/p или rounds, updated}`; **самого пароля нет нигде**, только соль и вывод KDF (`hashlib.scrypt`, запасной путь `pbkdf2_hmac`). Пишется временным файлом 0600 с `fsync` файла **и каталога** до переименования: переименование атомарно к другим процессам, но не к пропаже питания, а на сервере без бота это единственный вход с домена. Не легло — отказ `password_write_failed`, прежний пароль остаётся рабочим | **0600** |
| юнит | `/etc/systemd/system/goamnezia-admin.service` | 644 |
| слушатель | `127.0.0.1:1985` (`config.json .panel.port`; юнит передаёт `GOAMNEZIA_ADMIN_PORT`) | только петля |
| мост | `/opt/goamnezia/install.sh --action=… --json` (`GOAMNEZIA_ACTION_BIN`) | |
| список действий | `/opt/goamnezia/lib/actions.json` (`GOAMNEZIA_ACTIONS_JSON`) — перечитывается по mtime | |
| каталог шаблонов | `/opt/goamnezia/templates_catalog.json` + `lib/catalog_tool.py` (грузится лениво, при первом открытии выбора шаблона) | |
| `.env` бота | `/opt/goamnezia-bot/.env` — `BOT_TOKEN`, `ALLOWED_IDS` для кода 2FA и подписи мини-аппа | читается, не пишется |
| аудит | `/var/log/goamnezia-audit.log` (`GOAMNEZIA_AUDIT_LOG`) — строка JSON на каждую мутацию и на каждую выдачу ключа, без секретов | 0600 |
| замок ядра | `/run/goamnezia/config.lock` — берёт **мост**, панель его не трогает; внутри процесса панели мутации сериализует свой `threading.Lock` | |
| логи | `journalctl -u goamnezia-admin` (действие `logs-tail --component=admin` уже так делает) | |

Панель **не пишет** `config.json`/`users.json`. Пользователей, ключи, подписку, страницы подключения и раздачи она
получает ответом `user-list`/`user-show`/`user-key`/`sub-list`/`sub-show`/`page-list`/`share-list` — модулем ядра
`lib/core_read.py` в своём процессе (тот же JSON, те же файлы, замок ядра на чтение — §5b) или мостом; своего кода
чтения `users.json` у панели нет, ключи — только из ответа `user-key`. Из `config.json` наружу отдаётся
белый список полей (`public_config` в `server.py`): режим, домен, `server_ip`, `endpoint_host`,
`client_dns`, язык, по профилям — линейка, контейнер, порт, подсеть, образ, пресет, `params.MTU`
(остальные `params` — только через `profile-params`), `panel`, `site`, `options`, `mcp`.

## 2. Переменные окружения `server.py`

| Переменная | По умолчанию | Зачем |
|---|---|---|
| `GOAMNEZIA_DIR` | `/opt/goamnezia` | корень ядра: `config.json`, `lib/actions.json`, `templates_catalog.json` |
| `GOAMNEZIA_ADMIN_DIR`, `GOAMNEZIA_ADMIN_STATIC` | `/opt/goamnezia-admin`, `…/static` | рабочая копия и статика. Адреса `app.js` и `styles.css` в `index.html` и на странице входа сервер ставит сам (`versioned_assets`, PNL-04): `?v=<версия продукта>-<начало sha256 файла>`; по такому адресу — `Cache-Control: public, max-age=31536000, immutable`, без версии или с чужой — `no-store`; сами страницы — `no-store`, поэтому после обновления берётся новая статика. `app.js`, `styles.css` и `index.html` — сжатыми (gzip, `Vary: Accept-Encoding`), если клиент их принимает; файлы держатся в памяти до смены (`static_file`, отпечаток stat) |
| `GOAMNEZIA_SHORTLINKS_PY` | `$GOAMNEZIA_DIR/lib/shortlinks.py` | модуль ядра коротких ссылок: «Ждут подтверждения» панель читает им без моста (PERF-02) |
| `GOAMNEZIA_ADMIN_HOST`, `GOAMNEZIA_ADMIN_PORT` | `127.0.0.1`, `config.json .panel.port` → `1985` | слушатель |
| `GOAMNEZIA_ACTION_BIN`, `GOAMNEZIA_ACTIONS_JSON` | `$GOAMNEZIA_DIR/install.sh`, `$GOAMNEZIA_DIR/lib/actions.json` | мост и его каталог |
| `GOAMNEZIA_BOT_DIR` | `/opt/goamnezia-bot` | `.env` бота |
| `GOAMNEZIA_CATALOG`, `GOAMNEZIA_CATALOG_TOOL` | `$GOAMNEZIA_DIR/templates_catalog.json`, `$GOAMNEZIA_DIR/lib/catalog_tool.py` | выбор шаблона |
| `GOAMNEZIA_AUDIT_LOG`, `GOAMNEZIA_LOCK_DIR` | `/var/log/goamnezia-audit.log`, `/run/goamnezia` | аудит; путь замка показывается в «О программе» |
| `GOAMNEZIA_ADMIN_TOKEN_FILE`, `GOAMNEZIA_ADMIN_TOKEN` | `$GOAMNEZIA_ADMIN_DIR/token` | токен из файла (создаётся при первом старте, если нет) или из окружения |
| `GOAMNEZIA_ADMIN_PASSWORD_FILE` | `$GOAMNEZIA_ADMIN_DIR/password.json` | логин и хеш пароля; файл читается на каждую проверку, поэтому смена пароля снаружи не требует рестарта службы |
| `GOAMNEZIA_TRUST_TUNNEL` | `1` (иначе `config.json .panel.trust_tunnel`) | пускать ли петлю без второго фактора |
| `GOAMNEZIA_SESSION_TTL`, `_GRACE`, `_COOKIE_SLACK` | `900`, `60`, `21600` | сессия 15 мин + минута продления; cookie живёт дольше, чтобы сказать «сессия закончена», а не «нужен токен» |
| `GOAMNEZIA_ADMIN_ACCESS_LOG` | `0` | построчный access-log в journald (по умолчанию выключен: автообновление сыпало бы десятки тысяч строк в сутки) |
| `GOAMNEZIA_ADMIN_LOCK_WAIT` | `3` (секунды) | сколько ждать своей очереди на мутацию внутри процесса панели; не дождались — `lock_timeout` (409) с именем идущего действия. Ноль превратил бы две кнопки, нажатые подряд, в отказ; «ждать сколько понадобится» — в молчание на пятнадцать минут |
| `GOAMNEZIA_TFA_SINK` | пусто | **только тесты**: код 2FA дописывается в файл вместо отправки в Telegram. В юните не задавать никогда |
| `GOAMNEZIA_CORE_READ` | `1` | `0` — частые чтения только мостом, как в 1.0.9 (выключатель быстрого пути §5b) |
| `GOAMNEZIA_READ_SWR` | `1` | `0` — карточка и список людей ждут свежий сбор `traffic` и `edge-status`, как в 1.0.9: без прежнего снимка и без подогрева (§5b). Вместе с `GOAMNEZIA_CORE_READ=0` — чтения панели как в 1.0.9; задают drop-in-ом (§3) |
| `GOAMNEZIA_READ_BG_NICE` | — (`traffic` 10, `edge-status` 19, остальное 15) | уровень `nice` фоновых сборов моста (§5a «Первый кадр и одно ядро»); `0` — фон с обычным приоритетом, число — один уровень для всех |
| `GOAMNEZIA_CORE_READ_ROOT` | каталог настоящего `GOAMNEZIA_ACTION_BIN` | корень ядра с `lib/core_read.py`; задают стенды, где мост — обёртка вокруг `install.sh` (macOS: bash 5) |
| `GOAMNEZIA_READ_LOCK_WAIT` | `1` (секунды) | сколько быстрое чтение ждёт идущую правку ядра под `config.lock` (§5b); дольше — читает без замка, как мост. От 0 до 5; не число, `nan`, бесконечность — 1 |

Мосту панель передаёт `GOAMNEZIA_WHO=panel:<who>` (`tunnel` · `code` · `token` · `tg:<id>`) и
`GOAMNEZIA_CALLER=panel`; из окружения моста вычищаются `GOAMNEZIA_ADMIN_TOKEN`,
`GOAMNEZIA_TFA_SINK`, `BOT_TOKEN`. Таймауты: чтение 60 с, мутации 300 с, `profile-install` и
`upstream-update` 900, `backup-restore` и `change-mode` 600.

## 3. Юнит (пишет `install_admin.sh`)

```ini
[Unit]
Description=goAmnezia pro web panel (127.0.0.1:1985)
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/goamnezia-admin
Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
Environment=PYTHONUNBUFFERED=1
Environment=GOAMNEZIA_DIR=/opt/goamnezia
Environment=GOAMNEZIA_ADMIN_DIR=/opt/goamnezia-admin
Environment=GOAMNEZIA_BOT_DIR=/opt/goamnezia-bot
Environment=GOAMNEZIA_ACTION_BIN=/opt/goamnezia/install.sh
Environment=GOAMNEZIA_ACTIONS_JSON=/opt/goamnezia/lib/actions.json
Environment=GOAMNEZIA_ADMIN_HOST=127.0.0.1
Environment=GOAMNEZIA_ADMIN_PORT=1985
ExecStart=/usr/bin/env python3 /opt/goamnezia-admin/server.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

От root — тот же размен, что у бота (BOT.md §3): мосту нужны docker и запись в `/opt/goamnezia`.
Секретов в юните нет: токен читается из файла, `BOT_TOKEN` — из `.env` бота.

Свои переменные из §2 (например, выключатели быстрых чтений §5b) задают drop-in-ом, а не правкой юнита:
`install_admin.sh` переписывает юнит при каждой установке и обновлении, а каталог `goamnezia-admin.service.d`
не трогает. `systemctl edit goamnezia-admin` откроет `/etc/systemd/system/goamnezia-admin.service.d/override.conf`:

```ini
[Service]
Environment=GOAMNEZIA_CORE_READ=0
Environment=GOAMNEZIA_READ_SWR=0
```

После сохранения — `systemctl restart goamnezia-admin` (`daemon-reload` делает сам `systemctl edit`); действует,
пока строки в drop-in. Вернуть — убрать строки тем же `systemctl edit` (или `systemctl revert goamnezia-admin`:
снимает drop-in-ы, сам юнит в `/etc` остаётся) и снова `systemctl restart goamnezia-admin`.

## 4. Установщик `install_admin.sh` и что нужно от `install.sh` / `bootstrap.sh`

`admin-web/install_admin.sh` автономен (bash 5, `set -uo pipefail`, свой словарь RU/EN, язык из
`GOAMNEZIA_LANG` → `config.json` → `.language`). Шаги: root → `python3` ≥ 3.8 (apt при
нехватке; venv и pip не нужны — панель на stdlib) → копия `server.py` и `static/*` в
`/opt/goamnezia-admin` (по `-ef`, чтобы не копировать файл сам в себя; `py_compile` копии) →
`token` (существующий сохраняется, иначе `openssl rand -hex 32`, запасной путь `secrets.token_hex`)
→ **логин и пароль** (существующие сохраняются, иначе логин `admin` и случайный пароль 20 знаков
из букв и цифр) → юнит → `daemon-reload`, `enable`, `restart`, `is-active`. Коды выхода: 0 —
установлена и запущена; 1 — ошибка (python, копия, токен, юнит не поднялся — при занятом порте
отдельное предупреждение); 2 — неизвестный ключ, плохой `--port` или плохой `--login`.

| Вызов | Поведение |
|---|---|
| `install_admin.sh` | установить/обновить и напечатать команду туннеля, путь к токену, подсказку про домен |
| `install_admin.sh --auto` | то же без подсказок в конце (для меню) |
| `--lang=ru\|en`, `--port=N` | язык сообщений; порт (по умолчанию `config.json .panel.port`, иначе 1985) |
| `--login=NAME` | логин администратора панели (по умолчанию `admin`; при `--reset-password` без него сохраняется прежний) |
| `--reset-password` | перевыпустить пароль и напечатать его; **больше ничего не трогает** — ни файлов, ни юнита, рестарт службы не нужен |

**Пароль печатается ровно один раз**, в терминал того, кто запустил установку, — и при `--auto`
тоже: из меню установщик зовут именно так, и промолчи он там, выданный пароль не увидел бы никто.
Исключение — мастер без домена (NC-13, `_panel_ensure_local`): пароль нужен только для входа с
домена, поэтому вывод установщика там не показывается, а в `config.json` ставится
`panel.password_pending=true`. Пароль выдаёт `_panel_password_pending_issue`
(`install_admin.sh --reset-password`, только из SSH), когда панель выходит на домен
(`_panel_ensure_service`: смена режима, переход на Let's Encrypt, смена домена); отметку снимают и
перевыпуск пароля, и удаление панели. Хеш считает сам `server.py` (`--set-password`, пароль приходит потоком на stdin,
а не аргументом — аргументы видны в `ps`), поэтому правило хеширования живёт в одном месте:

```bash
printf '%s\n' "$password" | GOAMNEZIA_ADMIN_DIR=/opt/goamnezia-admin \
    python3 /opt/goamnezia-admin/server.py --set-password --login=admin
GOAMNEZIA_ADMIN_DIR=/opt/goamnezia-admin python3 /opt/goamnezia-admin/server.py --password-status
# {"set": true, "login": "admin", "file": "…/password.json", "updated": "…"}
```

**Что `install.sh` и `bootstrap.sh` для этого делают:**

1. Пункт «Настройки → Веб-панель, админы бота, ИИ-агенты → Панель и мини-апп» — экран `_panel_screen`
   (`install.sh`): Установить/обновить →
   `_panel_install` (`GOAMNEZIA_LANG="$LANG_CODE" bash "$SCRIPT_DIR/admin-web/install_admin.sh" --auto`);
   Статус → `systemctl status goamnezia-admin` + команда туннеля и путь к токену (`_panel_show_access`);
   Логи → `_ga_logs_tail admin 60`; Перезапуск → `systemctl restart goamnezia-admin`;
   Показать токен → `_panel_show_token` **только в терминал** (не в лог); Панель на домене →
   `_site_panel_screen` (`miniapp-on/off/rotate`, п. 5); Удалить → `_panel_remove` (юнит + `$ADMIN_DIR`),
   то же делает и `_wipe_everything`.
2. `auto_update_component "$SCRIPT_DIR/admin-web" "$ADMIN_DIR" goamnezia-admin.service` в
   `auto_update_components` — пропускает `token`; **должен пропускать и `password.json`** (иначе
   обновление кода сотрёт выданный пароль); юнит панели `install.sh` сам не пишет.
3. Мастер первой установки (шаг «12. Бот и панель»): панель ставится **без вопроса во всех трёх
   режимах** (NC-13): домен с настоящим сертификатом и путь панели — `_panel_ensure_service`
   (служба и панель на домене), иначе — `_panel_ensure_local` (служба только на `127.0.0.1`, вход
   через туннель без пароля). В конце мастера — команда туннеля `_panel_tunnel_hint` (та же
   `_panel_tunnel_cmd`, что на экране панели: адрес SSH-входа, `-p`, если SSH не на 22-м порту).
   Обновление панель не ставит: на сервере, поставленном без домена версией 0.5.0 и раньше, — только
   пункт 1.
4. `bootstrap.sh`: `admin-web/install_admin.sh` в `FILES` и в общем `chmod +x`; `README.md` не возится
   (правило `*.md`).
5. `tests/run_static.sh`: `admin-web/*.sh` попадает в общий список `bash -n` и `shellcheck -S warning -x`,
   `admin-web/server.py` — в `py_compile`, `admin-web/static/app.js` — в `node --check` при наличии
   `node`; юнит-тесты `tests/test_admin_features.py`, `tests/test_admin_http.py` подхватываются
   `discover` автоматически.

## 5. Что панель ждёт от моста (маршрут → действие → поля)

Ответ моста — последняя строка stdout, JSON со `status`; панель берёт **последний** объект со
`status`, идя с конца (как бот). `code` `not_implemented` → HTTP 501 и экран/тост «в следующей
версии»; `lock_timeout`, `port_busy`, `port_taken`, `profile_exists`, `subnet_overlap` → 409;
`closed_local` (порт свободен, но закрыт локальным файрволом — docs/PREFLIGHT.md §6), `needs_site`,
`not_pro`, `no_domain` → 409 (конфликт состояния сервера: действию нужен другой режим или открытый
порт; `message` и `hint` уходят как есть); `user_not_found`, `agent_not_found`, `backup_not_found`,
`unknown_template` → 404; `bad_arg`, `missing_arg`, `confirm_required`, `invalid_*` (в том числе
`invalid_scope`, `invalid_label`, `invalid_days`, `invalid_ips`, `invalid_port`, `invalid_agent_id`, `invalid_argument`, `invalid_page_id`, `invalid_profiles`,
`invalid_platform`, `invalid_line`, `invalid_toggle`, `invalid_name`)
→ 400; `peer_not_found`, `catalog_missing` → 404; `unit_failed`, `no_curl`, `no_python` → 503; `timeout` → 504; нет JSON → 502 `transport`; спринт 4: `unreachable` (GitHub/Docker Hub
недоступны с сервера) → 503, `already_current`, `no_previous`, `already_managed`, `not_adoptable`,
`unsupported_container`, `container_missing`, `conf_read_failed` → 409, `tag_not_found`,
`container_not_found` → 404,
`invalid_container` → 400; адрес сервера в ключе (разовый `endpoint` у `user-key`/`share-create`, свой адрес у `endpoint-set`): `invalid_ip`, `address_not_here` → 400, `addresses_unknown`, `address_gone` → 409 — с `message` ядра как есть (в `address_not_here` оно само перечисляет допустимые адреса); ключ из приложения Amnezia (0.4.5): «Привязать старый ключ» — `no_config`, `config_unreadable` → 400, `server_mismatch`, `key_mismatch`, `already_has_key` → 409; выдача по такому ключу `no_private_key` → 409 (состояние пира, а не сбой сервера); отказ установки профиля `foreign_container` (имя его контейнера занял контейнер приложения Amnezia с людьми — сначала «Импорт») → 409; остальное → 500. Разбор защищённый: отсутствие поля даёт прочерк, а не
падение.

| Маршрут панели | Действие ядра | Поля, которые панель читает |
|---|---|---|
| `GET /api/session` | — | состояние сессии (`state`, `seconds`, `ttl`, `grace`, `tfa`, `password`, `lang`, `version`, `who`); маршрут открыт, но у гостя `state=dead` и данных нет. `tfa` и `password` — чем на этом сервере вообще можно войти: по ним интерфейс решает, что показывать в окне входа |
| `GET /api/overview` | `status --brief=on` (общий снимок, 30 с; ядро без `brief` — полный) + `traffic` (общий снимок, 15 с) + `edge-status` (общий снимок, 120 с) — §5a + `systemctl is-active` (кэш 30 с) для `docker`, `goamnezia-bot`, `goamnezia-admin`, `nginx` (режимы с сайтом) | `version`, `installed`, `mode`, `domain`, `server_ip`, `endpoint_host`, `users`, `profiles[]{profile, container, state, port, line, peers, image_tag, enabled, packet_risk}` (`packet_risk` — `{packet, budget, s4, safe_s4, mtu, keyless}` или `null`, S4-FIX: пакет данных при стандартном MTU 1280 больше 1400 из-за большого S4; белым списком `packet_risk_payload`, целыми числами. Обзор и мини-апп держат строку «Профиль AmneziaWG 2.0: на части сетей скачивание не идёт» — «Слишком большие пакеты (S4 = 142): мобильный интернет, PPPoE и некоторые хостеры их не пропускают» — с кнопкой «Исправить S4»: то же окно, что в карточке профиля); для «тревог» Обзора (0.5.0, docs/CONTRACT.md §4.7a) — `endpoint_host_gone` (вписанного в ключи IP на машине нет, L-01), `server_ip_change{old, new, at, endpoint_host, endpoint_host_action}` или `null` (последний переезд, L-06) и `upstream{checked, attempted, reachable, updates_available, hint, client_release{current, latest, update}, profiles[], unsupported[]}` или `null` (итог суточной проверки обновлений, L-07) — белым списком (`server_ip_change_payload`, `upstream_summary_payload`); `docker_stale{reason, binary_version, binary_build, daemon_version, daemon_build, client_api, daemon_api, since, retry_after, last_attempt{at, ok, by, code}}` или `null` (DK-02: пакет Docker обновлён, а служба старая — `docker_stale_payload`; Обзор держит строку «Docker обновлён, а служба старая» с кнопкой «Перезапустить Docker», пока поле не `null`; та же кнопка — «Настройки → О программе → Docker» и строка «служба старая» в «Логи → Диагностика»; всё через вопрос «VPN пропадёт на 10–30 секунд у всех» и действие `docker-restart`, `confirm` — имя действия, таймаут моста 600 с); `kmod{loaded, version, patched, warn, state, code, reason}` или `null` (`kmod_payload`: этап А — `warn`, модуль без исправлений при профиле 3.1, строка «Модуль ядра AmneziaWG без исправлений»; этап Б — `state` `fallback` / `failed` и `reason` словами ядра, строка «Модуль ядра AmneziaWG: …» / «…не включился: …»; у обеих кнопка «Ускорение AmneziaWG» — к карточке на «Протоколах»); онлайн и суммарный трафик — из `traffic` (`users_online` — `null`, пока нет свежих прочитанных рукопожатий; `traffic_total` и `traffic_total_age` — из снимка до 10 мин, `null` без него; `traffic_kpi{period: 30d\|month\|all, rx, tx, days[], at}` — плитка «Трафик» из того же снимка, `null` без него, PNL-07; `pending` — что ещё собирается в фоне; §5a «Холодный Обзор»). **Адреса машины** — блок `edge{count, domain_endpoint, ips[], dns}`, когда сторож адресов ответил (и при одном адресе: SSH-шапка показывает адрес и его A-запись всегда), а при беде с A-записями — и без строк адресов. Всё **белым списком**: `ips[]{ip, verdict, users, packets_15m/1h/day, bytes_15m/1h/day, in_dns, silent_since}` (`edge_ips_payload`: адрес — только адрес, вердикт — слово сторожа или `null`, счётчики — числа или `null`, у ядра старше 0.4.4 байтов нет), `dns{domain, state: ok\|partial\|elsewhere\|none\|unknown, a[], foreign[], cloudflare}` — A-записи домена **из ключей** (`edge_dns_payload`; незнакомое состояние — `null`). Нынешний интерфейс рисует таблицу от двух адресов, при одном — только строки о беде (`foreign`, `elsewhere`, `none`, проксирование Cloudflare), как SSH. `tunnel_host` — адрес для команды SSH-туннеля: `server_ip`, но если на машине несколько адресов и основной не принимает входящие (вердикт не `ok`), а другой принимает, — первый принимающий (`tunnel_host`, находка PUI-25: у сервера A основной адрес только исходящий) |
| `GET /api/users` | `user-list` + `traffic` | `users[]{name, label, created, enabled, quota_bytes, expires, traffic{rx,tx}, profiles[], peers{<p>{ip, pubkey, active, last_handshake}}}`; панель добавляет `state`/`mark` (● ○ ⏳ ▮), `online`, `peers.<p>.handshake_ts/online`, `peers.<p>.has_key` (см. строку ниже); `privkey`/`psk` вырезаются ещё раз |
| `GET /api/users/<name>` | `user-show --name` | `user{…}`; `user_not_found` → 404. У пользователя — `imported`, у каждого пира — `has_key`: есть ли у сервера приватная часть ключа. У пира из контейнера Amnezia (импорт установки или подхват после потери настроек) её нет вовсе, и `user-key` по нему отвечает `no_private_key`. Панель приводит признак к bool (`decorate_user`): отсутствие поля — ядро постарше — значит «ключ есть», прежнее поведение. Для «ключа на другой адрес» в `user{}` кладутся ещё четыре поля (`user_card_payload`): `key_host` — адрес в ключах **этого** человека «как обычно» (с учётом закреплённого за ним адреса) и `key_host_gone` — закреплённый адрес пропал с машины; оба из `user-show` (из корня ответа или из `user{}`); `key_choices[]{host, kind: ip\|domain, default, verdict, users, in_dns}` и `keys_host` — из `edge-status` тем же кэшем в 30 с, что у Обзора, **белым списком** (`key_choices_payload`: хост только формы домена/IPv4, вердикт — одно из слов сторожа, иначе `null`). Сбой `edge-status` карточку не роняет: вариантов нет — и выбора адреса нет |
| карточка пользователя (`renderUserCard`, `renderPeerPane`, `openAttachKey`) | `user-show` + `user-attach-key` / `user-reissue-peer` / `user-add-peer` / `user-del-peer` | **Ключ из приложения Amnezia** (`has_key === false`: выдан в приложении ещё до подхвата, у сервера только его открытая часть): над рядом действий — каноничная строка `importedNote` (где ключ, что у человека всё работает, почему отсюда его не показать, два выхода; для нескольких профилей — `importedNoteMany` одной строкой), а во вкладке профиля ряда выдачи нет **совсем** — ни QR, ни скачивания .conf, ни `vpn://`, ни текста конфига, ни временной ссылки; на его месте `importedNoKeys` и кнопки «Привязать старый ключ» (первой, `soft`: ничего не ломает), «Перевыпустить ключ», «Убрать профиль». Показывать кнопки выдачи погашенными было нечем объяснить: `key-actions` — плоский переносящийся ряд без места под пояснение к каждой, а на телефоне подсказки по наведению нет. **Привязать старый ключ** (`openAttachKey`): окно с полем для ссылки `vpn://` или текста `.conf` и выбором файла — файл читается в браузере (`FileReader.readAsText`, не больше 64 КБ, `ATTACH_MAX_BYTES`) и ложится в то же поле, уходит ровно то, что видно; «Привязать» — одно действие `user-attach-key` (`name`, `profile`, `config`), ключ идёт телом POST и на сервере только в stdin моста (см. `POST /api/action/<имя>`), нигде не оседает: ни в хранилище браузера, ни в кэше вкладки, ни в тостах. Удача — тост со словами ядра и карточка заново, уже с рядом выдачи; `already_has_key` — то же; остальные отказы ядра (`config_unreadable`, `key_mismatch`, `server_mismatch`) — его словами прямо в окне, чтобы поправить и прислать снова. Выдача, ответившая `no_private_key` (карточка открыта до подхвата), — окно `importedTitle` со словами ядра и карточка заново (`keyError`), а не тост на шесть секунд. **Перевыпуск ключа** (`reissueKey`) стоит рядом с «Убрать профиль», тем же красным и с тем же typed-confirm (ввести имя пользователя); у ключа из приложения окно добавляет жёлтую плашку `reissueImportedWarn` (старый ключ у человека сломается сразу; если ссылка или файл сохранились — лучше привязать). Одно действие `user-reissue-peer` — то же, что зовёт `_user_peer_reissue` в SSH: пара «снять, потом выдать» оставляла окно, в котором закрытая вкладка отнимала доступ на профиль. Перевыпуск не прошёл — это говорится **окном** `reissueKeyFailed` (не тостом), с названием кнопки «Добавить профиль», которой доступ возвращают; из кэша вкладки вычищаются **все** ключи этой пары — и на обычный адрес, и на каждый разовый (префикс `имя/профиль/`). **Адрес сервера в ключе** (`keyAddr`): над рядом выдачи — подписанный `<select>`, когда у пира есть ключ (`has_key !== false`) и в `key_choices` есть хост, отличный от `key_host`. Первый вариант — «как обычно · <key_host>» (значение пустое), дальше остальные: домен — «<host> · домен», IP — со словом сторожа из таблицы «Адреса сервера» (`edgeWord`: принимает / замолчал / тихо / тихо у всех / ключи не смотрят), у «замолчавшего» — ⚠ и строка `keyAddrDark` под выбором; такой адрес не прячется — его выбирают осознанно. Под выбором — `keyAddrHint` («ключ тот же, за пользователем не сохраняется»); закреплённый адрес пропал (`key_host_gone`) — `keyAddrGone`. Выбор разовый: живёт, пока открыта вкладка профиля, при смене вкладки или повторном открытии карточки снова «как обычно». Смена адреса убирает показанный ключ, а ответ, пришедший после смены, в окно не рисуется. Все шесть кнопок выдачи берут выбранный адрес: `fetchKey(name, pid, ep)` (кэш по `имя/профиль/адрес`), «Скачать .conf» называет файл по `file_name` ядра, как без адреса (ядро без поля — `<имя>-<профиль>-<host>.conf`), у QR и текста — строка `keyAt` «Адрес в ключе: <host>» (хост — из `key_host` ответа ядра), «Скачать по ссылке» передаёт `endpoint` в `share-create`. На телефоне выбор — во всю ширину, как поля форм; на ПК ограничен 520 px (`.key-addr select`) |
| `GET /api/users/<name>/key?profile=&format=&endpoint=` | `user-key --name --profile --format` (по умолчанию `all`) `[--endpoint]` | `conf`, `vpn`, `qr_conf_png_b64`, `qr_vpn_png_b64[]`, `line`, `min_client`, `key_host`, `key_endpoint`, `key_host_override`, `file_name` (имя для «Скачать .conf» и «Текст .conf»: `anna-2_0.conf`, как у страницы и подписки; без поля — прежнее `<имя>-<профиль>.conf`) — единственный ответ с секретом; в аудит уходит без ключа (адрес `endpoint` в аудите есть — он не секрет). `endpoint` — разовый адрес сервера в Endpoint: в мост уходит **только непустой**, без него вызов байт в байт прежний. Форму проверяет `validate_params` по типу `key_endpoint` из `actions.json` (мусор → 400 `bad_arg` без запуска моста); пока каталог ядра параметра не знает, панель держит запасную форму `^[A-Za-z0-9.-]{1,253}$` (`KEY_ENDPOINT_PARAM`, `PARAM_TYPE_FALLBACK`) — определение ядра всегда главнее. Какие адреса допустимы, решает мост: `address_not_here` / `invalid_ip` → 400, `addresses_unknown` → 409 с его словами |
| `GET /api/users/<name>/conf?profile=&endpoint=` | `user-key … --format=conf` `[--endpoint]` | `conf` → файл `text/plain`, `Content-Disposition: attachment; filename="<file_name>"` — имя из ответа ядра (`anna-2_0.conf`, как у «Скачать .conf», страницы и подписки; с разовым адресом — то же имя), только формы `^[A-Za-z0-9_-]{1,32}\.conf$` (`KEY_FILE_NAME_RE`). Ядро старше поля или имя негодное — прежнее `"<name>-<profile>.conf"`, с разовым адресом — `"<name>-<profile>-<host>.conf"` (хост из `key_host` ответа, в заголовок — только формы домена/IPv4). Отказ моста — JSON с кодом, а не файл |
| `GET /api/profiles` | `profiles-status` + `config.json .profiles` | `profiles[]{profile, container, state, port, line, peers, image_tag, image_digest, enabled}` + `subnet`, `mtu` (`params.MTU`), `s4` (`params.S4`, для окна MTU), `preset`, `subnet_fixed` (подсеть не сменить — подхваченный старый контейнер на wg0), `packet_risk` (S4-FIX: как в `/api/overview`), `engine` и `engine_want` (движок AmneziaWG: фактический живым опросом и выбранный — KMOD_PLAN Б10, белым списком; у ядра старше 1.0.13 — пусто). Карточка профиля с `packet_risk` — плашка «Слишком большие пакеты. На части сетей (мобильный интернет, PPPoE, некоторые хостеры) скачивание не идёт: S4 = 142, пакет данных 1502 байт больше 1400.» и кнопка «Исправить S4» → окно с тем же текстом, что в SSH (S4 станет не больше 40, перезапуск 15–25 с, ключи прежние, кому импортировать заново — после); у людей приложения без ключа на сервере (`keyless`) — окно с набором порта профиля, согласие — `adopted_confirm`; мост — `profile-fix-s4` (`confirm` = профиль); итог — окно «S4 исправлен» со словами ядра: `message` и по строке на `steps` (подписка — обновится сама; страница, ключ, приложение — что делать); `adopted_keyless` после окна (люди появились позже) — второе окно со словами ядра и повтор с согласием. Окно MTU (решение владельца 08.10): подсказка «1280 — стандарт, работает везде. На проводном интернете можно поднять до 1320 — будет на несколько процентов быстрее; если скачивание начнёт зависать, верните 1280.», кнопка «Вернуть 1280»; ниже 1280 — под полем «Ниже 1280 туннель не поднимется у клиентов с IPv6 (AllowedIPs ::/0) — оставьте 1280 или больше» (`prMtuLow`, L-02б), «Сохранить» не жмётся (сервер панели отвечает теми же словами — `below_minimum` в `actions.json`); предупреждение под полем — только о том, что такой MTU сломает наверняка (как `awgkeys.py mtu_warnings`): MTU выше 1280 и пакет MTU + 80 + S4 больше 1440 — «Пакет N байт: на части хостеров и сетей скачивание встанет — оставьте 1320 или меньше» (`prMtuWarnPath`, L-02а); тогда кнопка — «Сохранить всё равно»; панель считает `udp443`, `not_installed` (из `param_types.profile.enum`). `subnet_overlaps[]{profile, subnet, with[], fixed, move{profile, subnet, keyless} \| null}` — пересечения подсетей от ядра белым списком (`subnet_overlaps_payload`, SUBNET-01): карточка профиля говорит, с кем пересекается подсеть, и даёт кнопку «Перенести <профиль> на <свободная>» (окно с набором порта профиля; люди приложения без ключа названы в нём, и то же слово — `adopted_confirm`); окно «Подсеть» у `subnet_fixed` — отказ сразу, без поля, и та же кнопка; у переносимого профиля свободная подсеть подставлена; `adopted_keyless` в обычном окне подсети — второе окно со словами ядра и повтор с `adopted_confirm` |
| `GET /api/profiles/<p>/params` | `profile-params --profile` | `params{ключ: строка}`; панель раскладывает их по ключам **линейки-основания** профиля (`LINE_FAMILY`: 1.0 и 1.5 → 1.5, 2.0 → 2.0, 3.1 → 3.1 — как `ga_profile_line_base` и `ga_amn_param_keys` в ядре; линейка берётся из `config.json .profiles.<p>.line`, а не из имени профиля, иначе у подхваченного `amnezia-awg2` с 3.x-конфигом список был бы 2.0) со `scope` server/client (`S1–S4, H1–H4, HeaderProtectionKey, RandomTrailers, DisableCookies` — server); в ответе — `line` (фактическая) и `base_line` |
| `GET /api/traffic` | `traffic` (раздел «Трафик» ждёт свежий сбор; `?wait=0` — так спрашивает Обзор — не ждёт: снимок не старше 10 мин — при рукопожатиях старше 45 с с `online: null` — или `{pending: true}` и сбор в фоне, §5a) | `profiles[]{profile, peers[]{pubkey, latest_handshake_ts, rx, tx}}`, `users[]` (с `peers.<p>.pubkey`); онлайн = рукопожатие < 180 с |
| `GET /api/backups` | `backup-list` | `backups[]{name, size, created}`; `restorable` = имя по `^[A-Za-z0-9._-]{1,128}$` |
| `GET /api/logs?component=&lines=` | `logs-tail --component --lines` | `log`, `lines`, `tz_offset` — смещение часов сервера от UTC в минутах (journalctl пишет местное время машины, остальная панель — время браузера; под журналом — «Время в строках — по часам сервера, UTC+3», PNL-COS-03); компоненты — `param_types.component.enum`. Пустой `log` значит ровно «записей нет»: контейнера нет — 404 `logs_no_container`, логи выключены при запуске (`--log-driver none` у профилей) — 409 `logs_driver_unreadable`, демон молчит — 503 `logs_docker_down`, юнита или файла нет — 404 `logs_no_unit` / `logs_absent` (DEFECTS §3.8) |
| `GET /api/logs/status` | `logs-status` | `available`, `areas[]{area, used_mb, budget_mb, percent}` как есть от моста. **`area` — участок диска** (`journal`, `nginx`, `goamnezia`, `docker`, `free`), а не компонент из `logs-tail`: там читают строки, здесь считают место, и подставлять эти имена в `--component` нельзя (DEFECTS §3.9). Остальное — как есть от модуля: проценты — голые числа, `budget_mb: "-"` значит «бюджета нет», последняя строка `free` — **свободное место на диске**, а не занятое (`lib/logbudget.sh logb_status`). Панель рисует таблицу с полосой заполнения над логами, а `free` выносит отдельной строкой — в столбце «Занято» это было бы ровно обратное значение |
| подтверждения на «Протоколах» и в «Настройках» | — | Вопрос задаётся там же, где его задаёт SSH, и теми же словами (DEFECTS §1d.2): `profile-restart` → `restartWarn` (рвёт связь всем подключённым), `profile-port` → `portConfirm` уже с номером (порт стоит в Endpoint каждого выданного ключа), `site-alt-svc` — **только при включении** (`altSvcWarn`; выключение вреда не несёт и идёт сразу, как в SSH). Раньше все три уходили в дело по одному нажатию |
| `GET /api/preflight` | `preflight` (без кэша, по кнопке «Проверить порты» в «Настройках») | `available`, `modes` (ответ `preflight_modes` целиком: `lite/lite_site/pro{available, reasons[]}`, `warnings[]`, `ports{tcp80,tcp443,udp443}{state, ours, process, pid, unit, container, product}`, `hints{free_cmd, open_cmd}`, `dry_run`), `lite_site_available`, `pro_available`, `hint`. Тексты причин и предупреждений панель **не переписывает**: берёт `reasons[].message` / `warnings[].message` — те же строки, что видит человек в SSH-меню, на языке `config.json` |
| `GET /api/share` | `share-list` | `available`, `count`, `shares[]{id, name, profile, format, file_name, port, host, via, size, created, expires, ttl, max_downloads, cert, downloads, seconds_left, endpoint}` (`endpoint` — разовый адрес сервера в файле, пусто — как обычно) — **белым списком** (`SHARE_LIST_FIELDS`): случись в ответе моста `url` или `path`, секретный адрес попал бы на страницу, которую видит любой вошедший. Адрес отдаёт только `share-create` |
| `GET /api/settings` | — (`config.json` белым списком) + `edge-status` (тем же общим снимком, что у Обзора; сбой «Настройки» не роняет) + `status --brief=on` (тем же снимком; сбой — признаков нет); оба не ждут: снимок до 10 мин или сбор в фоне и `pending` (§5a) | + `endpoint_host_gone` и `server_ip_change` (как у Обзора, L-01/L-06), `panel_url` (`https://<domain>/<panel.miniapp_path>/`), `tunnel_host` (как у Обзора — для строки «Туннель» в «О программе»), `bot_state` (состояние службы бота, как на Обзоре: `active` / `inactive` / `failed` / `not_installed` / `unknown` — строка «Администраторы бота» отличает «бот ждёт первого админа» от «бота нет», находка PUI-11), `tfa`, `password{set, login, updated, file, min_len}` (**без соли и хеша**), `trust_tunnel`, `bind`, пути, `session`, `who`, `bot_admins{ids[], owner, managed_by: "bot"}` — **только просмотр**, как в SSH: список читается из того же `.env` бота, откуда панель берёт, кому слать код 2FA, и порядок в нём значим (первый id — владелец, сортировать нельзя). Управляет списком бот (`/invite` и его собственный экран «Администраторы», где удалять может только владелец): `.env` — файл бота. Прежде панель про админов не говорила ни строки, и «кто может управлять сервером из Telegram» нельзя было узнать ни одним её экраном |
| `GET /api/quality` | `quality` | `quality{docker, ip_forward, network, disk_free_mb, ports[]}` |
| `GET /api/agents` | `agent-list` | `agents[]{id, label, scope, enabled, expires, last_used, last_ip, calls, port, allow_ips, state}` (без секретов, `mask_secrets` поверх) и `transport{enabled, mode, listen, base_url, cert_fingerprint_sha256, unit_state, hint, public_port, …}` (= `mcp-http-status`). Ветка `stub: true` (страница-заглушка) — **защитный код на случай ядра без модуля**: `agent-list` нет в `actions.json` или он ответил `not_implemented`. В этой версии действие реализовано, и панель всегда получает `stub: false` |
| страница «Агенты» (`loadAgents`, `agentActions`) | `POST /api/action/agent-add` (`label`, `scope` — предвыбор `read`, под полем `scopeNote*` — **состав** уровня, а не одно свойство: `write` выдаёт приватные ключи всех клиентов И без единого подтверждения сносит сайт-маскировку, панель на домене и весь транспорт агентов, включая собственный; `days`, `allow_ips`, в Lite — `port`), `agent-show`, `agent-rotate` (+ `confirm` = `id`), `agent-enable` / `agent-disable` (**обе стороны спрашивают** `agentEnableText` / `agentDisableText`, как в SSH: «выключить» гасит работающего агента на следующем же вызове, «включить» возвращает управление сервером тому, у кого доступ уже отобрали), `agent-del` (typed-confirm `id`), `mcp-http-on` (в `lite` — `port` из диалога, по умолчанию прежний или случайный **1024–9999**: низкий диапазон, тот же, что у профилей и в SSH — прежде здесь оставался отменённый 30000–49999), `mcp-http-off` (кнопки включения и выключения показываются по `transport.configured`, а состояние — по `transport.enabled`: в режиме без места для транспорта это разные вещи) | карточка доступа в корне ответа: `url` (**секрет** — только в модальном окне `showAgentUrl` с кнопками «скопировать адрес / команду», в тосты и таблицу не попадает), `cert_fingerprint_sha256` (самоподписанный → подсказка про `NODE_EXTRA_CA_CERTS`), `transport_enabled`, `warning`; статус транспорта — как `transport` выше |
| `GET /api/upstream` | `upstream-check` (без кэша, по кнопке «Проверить обновления» на «Протоколах») | `reachable`, `check{client_release{current, tag, update}, images, scripts, unsupported[]{name}, updates_available, hint}`, `profiles[]{profile, container, current_tag, available_tag, update, previous_tag, rollback_available}`; при `unreachable` — `check: null`, `profiles[]` с текущими версиями и `error{code, message}` |
| карточка «Обновления Amnezia» (`renderUpstream`, `upstreamActions`) | `POST /api/action/upstream-update` (`profile` + `confirm` = profile, typed-confirm), `upstream-rollback` (то же), `core-update` (то же) | `message`, `image_tag`, `previous_tag`; таймауты 900 с |
| `GET /api/import` | `import-scan` | `containers[]{container, port, proto, kind, line, subnet, peers, managed, profile, adoptable, reason, suggested_profile}`, `adoptable[]` |
| `GET /api/kmod` | `kmod-status` (общий снимок, 5 с, признаки файлов модуля — §5a) + действие панели в фоне | **Ускорение AmneziaWG (модуль ядра, KMOD_PLAN этап Б, CONTRACT §4.7a).** `kmod` — `kmod-status.kmod` белым списком (`kmod_status_payload`): `loaded`, `version`, `patched`, `warn`, `state` (`off \| ready \| on \| fallback \| failed \| unpatched \| busy`), `code`, `reason` (словами ядра, с ядром), `kernel`, `want`, `installed`, `installed_version`, `owner` (`goamnezia \| kaskad \| other`), `label`, `profiles{<p>: {engine, want}}`, `progress{action, step, text, at, started}` или `null`, `selftest{ok, kernel, label, at, reason}` или `null`, `failed{kernel, step, code, reason, log, at}` или `null`, `packages[]` (имена пакетов Debian); ядро без этапа Б (нет `state`) — `null`, карточки нет. `error` — отказ моста словами. `job` — действие панели (`kmod_job_payload`): `{id, op, done, step, elapsed, ago, steps[]}` — идёт (`done: false`, `step` — шаг моста) или его итог, не старше 30 мин; `steps[]` — итог каждого шага белым списком (`kmod_step_payload`): `{action, ok, code, message}` и у `kmod-install` — `state, version, kernel, owner, reused, packages, headers_meta_skipped, selftest{…, cases[]{profile, line, client, ok, detail}}, want`, у `kmod-engine` — `engine, want, rolled_back, profiles[]{profile, from, to, restarted, alive_before, returned, return_s, note}`, у `kmod-remove` — `removed, kept_for_neighbour, packages, command` (только `apt remove` и имена пакетов, иначе панель собирает её сама) |
| `POST /api/kmod/<действие>` | цепочка моста в фоне: `enable` — `kmod-install --rebuild=off --headers-meta=on\|off`, затем `kmod-engine --engine=kernel`; `program` — `kmod-engine --engine=program`; `rebuild` — `kmod-install --rebuild=on`, затем `kmod-engine --engine=kernel`, если выбран модуль, а профили не на нём (как SSH); `remove` — `kmod-remove` | Тело: `confirm` = имя действия (после окна согласия: любое из четырёх рвёт связь людям на 15–25 с или ставит пакеты) и у `enable` — `headers_meta` (`on \| off`, KMOD-05: метапакет заголовков, который тянет новое ядро, только по выбору). Ответ сразу: 200 `{job}`; без верного `confirm` — 400 `confirm_required`, кривой `headers_meta` — 400 `bad_arg`, незнакомое действие — 404 `unknown_action`, действие панели с модулем уже идёт — 409 `kmod_busy` (словами, `PANEL_STRINGS`); мост в этих случаях не зовётся. Шаги идут в потоке панели по одному, каждый — через `run_action` под общим замком панели и ядра (другие мутации в это время получают «занято (установка модуля ядра)»), с языком нажавшего и `who` в аудите; разрушительным шагам панель передаёт их `confirm` каталога сама (`kmod-engine` — движок, `kmod-remove` — имя действия). Таймауты моста: `kmod-install` 1800 с, `kmod-engine` и `kmod-remove` 900 с, `kmod-check` 120 с. Проверка хоста — `POST /api/action/kmod-check` (только чтение, `check` целиком) |
| карточка «Ускорение AmneziaWG (модуль ядра)» на «Протоколах» и в мини-аппе (`drawPfKmod`, `kmEnable`, `kmStart`, `loadKmod`) | `GET /api/kmod`, `POST /api/action/kmod-check`, `POST /api/kmod/<действие>` | Под карточками профилей, перед «Обновлениями Amnezia» (в панели нет раздела «Обслуживание»; обновления и импорт профилей — тоже здесь). Тексты — те же, что в SSH (`lib/lang/{ru,en}.d/kmod.sh`). «Сейчас» (программа amneziawg-go в Docker / модуль ядра <версия> / идёт установка или перевод), «Ядро», «Профили» — движок каждого словами; `ready` — «Модуль … собран и загружен, а профили работают программой», `off` — «Модуль ядра не ставился»; `fallback`, `failed`, `unpatched` — причина ядра (без пути SSH-меню в скобках) и у `failed` — журнал сборки. Кнопки по состоянию (неприменимых нет): «Включить» (кроме уже включённого), «Вернуть программу» (выбран модуль или профиль на нём), «Пересобрать» и «Удалить модуль» (модуль ставила goAmnezia; «Удалить» — и когда выбран модуль соседа); `fallback` без модуля под ядро — первой «Пересобрать». «Включить» — `kmod-check`: нельзя — окно с фразой ядра; можно — окно согласия: пакеты и место (`plan.missing`, `size_mb`), сборка 1–3 минуты, набор исправлений и самопроверка, метапакет заголовков (тянет новое ядро — выбор «Не ставить (рекомендуется)» / «Поставить и его», иначе «поставлю тоже»), «На том же процессоре — до полутора раз больше скорости…, 15–25 секунд»; кнопки «Нет, оставить программу» (тост «ничего не поставлено») и «Да, включить». «Вернуть программу», «Пересобрать», «Удалить модуль» — вопрос с предупреждением SSH (удаление — красный, у соседа Kaskad — «модуль и DKMS останутся»). Пока идёт действие (своё или чужое — SSH, бот: `state=busy`) — блок хода: название, `progress.text`, часы у своего, кнопок нет; опрос `GET /api/kmod` раз в 4 с на любом разделе (итог своего действия — ещё и тостом), без действия — не чаще раза в минуту вместе с разделом. Итог (`job.steps`) — в карточке полчаса: удалось — что теперь работает, самопроверка по линиям, строка по каждому профилю («вернулись 2 из 2, первый — через 17 с»); перевод откатан — «Перевод на «модуль ядра» отменён — профили вернулись на прежний движок», слова ядра и «за 3 минуты не вернулся ни один из 3 — откат»; установка не удалась — слова ядра и «Профили остались на программе amneziawg-go»; удалено — команда `apt remove …` из ответа с «Скопировать». Действие кончилось — карточки профилей перечитываются. Карточка профиля (`/api/profiles` → `engine`, `engine_want` у каждого профиля, белым списком): строка «Движок: модуль ядра» / «программа amneziawg-go» (кроме старого контейнера на `wg0`), выбран другой — «Выбран «…» — сменится при следующем перезапуске профиля». Обзор: `kmod.state` `fallback` / `failed` — строка «Модуль ядра AmneziaWG: <причина>» / «…не включился: …» с кнопкой «Ускорение AmneziaWG» (и у строки «без исправлений» этапа А); в ⌘K — пункт «Ускорение AmneziaWG (модуль ядра)»; «Настройки → О программе → Движок» — «AmneziaWG (модуль ядра) + Docker», когда профили на модуле |
| панель «Импорт существующей установки» на «Бэкапах» (`renderImport`) | `POST /api/action/import-adopt` (`container`, подтверждение диалогом — действие не разрушительное), `import-reconcile` (`profile`, `apply=off` → разница, кнопка «Подхватить изменения» → `apply=on`) | `profile`, `users_added`, `users[]`; `in_sync`, `new[]`, `gone[]`, `applied`, `added`, `deactivated` |
| `GET /api/pages?name=` | `page-list [--name]` | `available`, `count`, `pages[]{id, user, created, expires, forever, visits, last_visit, profiles, mirror}` — **белым списком** (`PAGE_LIST_FIELDS`), адреса нет по построению (docs/CLIENTS.md §9): его отдают только `page-create` и `page-url` тому, кто нажал. Имя проверяется `NAME_RE` до моста; ядро без модуля → `available: false` |
| `GET /api/mirror` | `mirror-list` | `available`, `max_total_mb`, `used_mb`, `items[]{app, platform, state, version, file, size, bytes, sha256, fetched, error, message, updated, busy, published, source_url}` (`MIRROR_ITEM_FIELDS`); карточка «Копии приложений для клиентов» на «Настройках» считает проценты из `bytes`/`size` (ядро берёт байты по `.part` идущей загрузки), у `failed` показывает фразу `message` и код в скобках, и опрашивает маршрут каждые 5 с, пока есть `queued`/`downloading` и открыты «Настройки» (`syncMirrorTimer`; ушли со страницы — таймер снимается) |
| `GET /api/clients` | `clients-list` | `available`, `checked`, `apps[]{id, name, official, platforms[], mirror_platforms[]}` — каталог в объёме «что можно скачать на сервер»: `mirror_platforms` — ключи `mirror.assets` по договору §8; ссылок, лицензий и регексов сборок наружу нет — панели их нечем показать |
| `GET /api/actions` | — (`lib/actions.json`) | `actions{имя: {readonly, destructive, secret, params, description}}`, `param_types` — фронтенд строит по ним проверки ввода и знает, что разрушительно |
| `GET /api/site/templates?cat=&q=&page=` | — (`catalog_tool.Catalog`) | `categories[]{id, name, count}`, `items[]{id, name, description, category, preview_url, license, stars}`, `pages`, `page`, `total`; `available:false` без каталога |
| `GET /api/site/template` | — (`config.json .site` + каталог) | `template_id`, `template_source`, `template{…}`, `mode`, `domain`, `alt_svc`, `listen` |
| блок «Временные ссылки на файл» на «Пользователях» (`renderShares`, `shareActions`) | `POST /api/action/share-create` (`name`, `profile`, `format` = `conf` \| `vpn`, `endpoint` — только непустой, из выбора «Адрес сервера в ключе» в карточке), `share-revoke` (+ `confirm` = `id`), `share-revoke-all` (+ `confirm` = **имя действия**: параметров у него нет) | у `share-create` — `id`, `url` (**секрет**: только в модальном окне сразу после создания, с кнопками «скопировать адрес» и «открыть», сроком жизни и предупреждением «адрес — это ключ»; в список, тосты и аудит не попадает), `ttl`, `expires`, `file_name` (`anna-2_0.conf`, и при разовом адресе то же — П-2), `endpoint` (окно создания и окно с адресом говорят `shareAddr` «Адрес сервера в файле: <host>»), `size`, `selfsigned`, `fingerprint` (самоподписанный → предупреждение про экран браузера). Форматы только файловые: QR по ссылке не нужен — картинку панель и так показывает на месте. Отказ `no_private_key` (окно открыто до подхвата) — словами ядра в окне, без них — каноничной строкой `importedNote` |
| `POST /api/action/<имя>` | любое из `actions.json` | тело — параметры действия; для `destructive: true` обязателен `confirm` = значение первого параметра (как `confirm` в MCP); у действия со своим параметром `confirm` в каталоге (`users-del-bulk`: число людей из предпросмотра) он остаётся параметром моста (`action_own_confirm`) и сверяется ядром под замком, без него — 400 `confirm_required`; ответ моста целиком в `data`. Пустое значение — законный ответ (`--value=` у `profile-set-param` значит «убрать»), кроме типов из `EMPTY_FORBIDDEN_PARAMS`: пустой `profiles` у `user-add` → 400 `no_profiles_picked` ещё до моста. Это не придирка к форме: отсутствующий `profiles` ядро понимает как «во все установленные», и пустой набор, отданный как отсутствующий, заводил ключи в линейках, которых человек не просил (DEFECTS §1.4). Форма «Новый пользователь» отказывает тем же словом и с тех пор шлёт поле всегда. **Параметр из stdin**: у действия, чей параметр в каталоге ядра помечен `"stdin": true` (сейчас — `config` у `user-attach-key`, ключ человека; имя панель держит и сама, `STDIN_PARAM_FALLBACK`, — на случай каталога без пометки), это поле вынимается из тела ДО сборки argv (`stdin_param`, `stdin_payload`) и уходит мосту только в stdin (`run_action(..., stdin_data=…)`, `Popen(stdin=PIPE)`): в командной строке секрет виден в `ps`. В argv, аудит и журнал панели он не попадает ни при каком каталоге (`validate_params` такой параметр не принимает — это «лишний параметр», а не `--config=…`); вывод упавшего моста у такого действия в журнал не пишется. До моста: не строка → 400 `bad_arg`, длиннее 64 КБ или с нулевым байтом → 400 `config_unreadable` (`PANEL_STRINGS`); пусто — законно, ядро ответит `no_config` своими словами. stdin у любого другого действия сервер отвергает (`bad_arg`). Панель зовёт: `user-add/del/rename/enable/disable/quota/expiry/add-peer/del-peer/reissue-peer/attach-key`, `users-add-bulk` (шторка «Добавить несколько»: число 1–100, галочки профилей, случайные имена или префикс; итог — список в шторке, имя открывает карточку; таймаут 900 с), `users-del-bulk-preview` и `users-del-bulk` («Удалить сразу несколько»: «Ещё» рядом с поиском — «Выбрать несколько» (режим выбора: строки и карточки становятся галочками, полоса «Все на странице · Выбрано: N · Удалить: N · Готово» под отбором и поиском, до 100 за раз; выбор переживает отбор, поиск и страницы, пропавших снимает загрузка) и удаление по условию — префикс, выключенные руками, истёкшие, с исчерпанным лимитом, то же в ⌘K; шторка — список от ядра (`users-del-bulk-preview`), подтверждение набором числа людей (`typedConfirm`), `confirm_mismatch` — «список изменился» и новый список, итог — удалённые, на ком встало и оставленные выключенными; таймаут 900 с; коды `invalid_selection` 400, `confirm_mismatch`, `nothing_selected`, `too_many`, `user_del_failed`, `interrupted` — 409), `profile-install/remove/restart/port/subnet/mtu/params/regen/set-param`, `backup-create/restore`, `change-mode`, `change-template` (в т.ч. `__random__`), `site-check`, `site-alt-svc`, `dns-set`, `endpoint-set`, `miniapp-on/off/rotate`, `share-create/revoke/revoke-all`, `page-create/url/revoke`, `mirror-fetch/remove`, `short-ensure/replace-request/decide/status/pending/bans/unban`. **Короткие ссылки** (`short-*`, договор docs/SUBSCRIPTION.md): `who` у `short-replace-request` и `short-decide` ставит сервер — «panel», если каталог ядра объявил параметр, присланное страницей отбрасывается (`short_params`); итог `short-decide` и `short-status` полем `status` (`ok | wrong | denied | swapped | expired | frozen | pending`) — ответ 200 `{ok: true, data}`, а не отказ (`short_outcome_answer`); ответы с кодом — секрет того же порядка, что адрес подписки: только этим маршрутом, без общего снимка (`use_cache=False`, в `READ_CACHE_TTL` их нет), с `Cache-Control: no-store`, в журнал панели — никогда; цель `short-unban` в аудите — «***», если это не адрес, нажатое число у `short-decide` — «***» (`deny` и `swap` — как есть; `audit_params`, SL-AUD-01); поле `code` строки аудита у действий с `secret: true` (`short-*`, `sub-*`, выдача ключа) — только слово отказа моста или итог решения (`decision`), у удачного ответа — пусто: `code` удачного `short-ensure` и `short-list` — сам короткий код (`audit_code`, SL-LOG-01). К ответам `page-create` и `page-url` сервер дорисовывает `qr_png_b64` (`attach_page_qr` → `qrencode -r /dev/stdin -o - -t PNG`, адрес через stdin, не argv; без `qrencode` — пустая строка): QR рисуется на сервере, чтобы секретный адрес не уезжал вторым запросом и не попадал в строку запроса и журналы |
| карточка пользователя → «Страница подключения» (`openPageCreate`, `showPageUrl`, `renderUserPages`) | `page-create` (`name`, `ttl` — пресеты `1h`/`24h`/`7d`/`30d`/`forever` и своё значение, проверка `validPageTtl` до моста), `page-url` (`id`, **только по кнопке «Показать адрес»**), `page-revoke` (`id`, `sub`, `keys`; окно отзыва `revokePage` — §6c; в каталоге ядра действие не разрушительное, `confirm` сервер не ждёт) | `url` (**секрет**: модальное окно с адресом, «Скопировать», «Открыть», QR из `qr_png_b64`, срок, предупреждение — у бессрочной громче, `pageForeverWarn`), список страниц пользователя из `GET /api/pages?name=` в карточке: срок, открытия, ключи, зеркало, «Показать адрес», «Отозвать». Отказ `pages_need_domain` (409) показывается словами `pagesNeedDomain` с путём через временную ссылку на файл; кнопка есть только у включённого пользователя с ключами и только когда `available` не `false`; `user_disabled` (409) → `pageUserDisabled` своими словами. Профиль с ключом из приложения Amnezia страница показывает человеку пояснением, а не ключом, — поэтому окно создания, окно с адресом (по `imported_profiles` ответа `page-create`, иначе по карточке) и список страниц ставят плашку `pageImportedNote` на каждый такой профиль: ключа там пока нет, после «Привязать старый ключ» он появится на той же странице, пересоздавать её не нужно. Все ключи человека из приложения — ядро страницу не создаёт (`no_private_key`, 409, его словами в окне) |
| карточка пользователя → «Подписка» (`renderUserSub`, `showSubUrls`, `showSubClients`; docs/SUBSCRIPTION.md) | `sub-list` (`name`, без адресов), `sub-create`, `sub-show` (**только по кнопке «Показать адреса»**, пишет аудит), `sub-rotate` и `sub-revoke` (ввод имени) | `base_url`, `urls`, `clients` (**секрет**: идут в панель целиком, ответ не кешируется — `use_cache=False`, в `READ_CACHE_TTL` их нет — и не журналируется). Есть `clients` — окно выбора по приложению (решение владельца 04.10.2026, образец — `.claude/work/concepts/podpiska-tab.html`): слева приложения по группам «Обновляется сам» / «Разово» с платформами (`sgPlatName`), записи с одним `app` — одно приложение; справа выбранное (по умолчанию — первое `recommended`, иначе первое): как обновляется (`update_hours`, 0 — `update_interval` ответа), «Формат» по `variant` (у нескольких записей с одним `app`), «Линия» (у нескольких `items`), два способа в ряд — «QR-код» и «Скопировать» — и под рядом выбранное: ничего — адрес без секрета (`/s-••••/`); QR по полю `qr` — `url` и `deeplink` картинкой сервера (`attach_sub_clients_qr` кладёт в пункт `qr_png_b64`, по `qr_alt: "url"` — второй код `qr_alt_png_b64`; один qrencode на одинаковый текст, адрес — только `https://`), `key` — QR ключа линии путём карточки (`fetchKey` → `/api/users/<имя>/key` → `keyQrParts`, части листаются; профиль линии — по `lines` ответа), `qr: ""` — кнопка неактивна с причиной (роутер, Clash на компьютере, продукты серии без платформ, иначе «не для этого приложения»); «Скопировать» по полю `copy` — `url` адрес, `command` готовую команду, `key` ключ линии в `key_format` (vpn:// или текст .conf, тем же `fetchKey`) — сразу в буфер, затем «Скопировано: что. Теперь вставьте … в приложение» с `hint` (`{minutes}` — интервал в минутах) и само значение (ключ — коротко). «Открыть в приложении» — строкой под рядом, только при `deeplink` своей схемы (`SUB_DEEPLINK_RE`, `javascript:`/`data:` не рисуются). Третьей кнопкой — «Короткая ссылка» (следующая строка таблицы), только при `short_available: true` ответа и ядре с `short-ensure`; на узком окне три кнопки — без мелких подписей. На узком окне (мини-апп, контейнерный запрос `.gx-subcw` уже 560 px) приложения — строкой с переносом над выбранным, без прокрутки вбок. Нет `clients` (ядро старше) — прежний список по форматам и QR профиля mihomo (`qr_png_b64` ответа) |
| выбор клиента подписки → «Короткая ссылка» (`shortShow`, `subShortOut`, `shortSwap`, `shortPoll`; договор — docs/SUBSCRIPTION.md) | `short-ensure` (`name`, `suffix` пункта; один раз на окно — короткие ссылки окна держит `st.short` по суффиксу), `short-replace-request` (`name`, `suffix`; `who` ставит сервер), `short-status` (`request`, опрос раз в 3 с — `SHORT_POLL_MS`) | `code`, `display`, `url` (**секрет того же порядка, что адрес подписки**: только в этом окне). Плашка «Короткие ссылки работают с 2FA через Telegram-бота. Каждый новый вход и каждую замену подтверждает администратор…», код крупно с адресом сайта, круглая ↻ («Заменить короткую ссылку»), «Скопировать» (роутеру — «Скопировать команду»: та же команда, что у «Скопировать», с короткой ссылкой), как набирать и входить. ↻ — число крупно и «Ждём подтверждения · м:сс», опрос до итога или срока (`expires` + 5 с): `ok`/`swapped` — новый код на место прежнего и «Ссылка заменена», `wrong`, `denied`, `frozen`, `expired` — словами; окно закрыли — опрос кончился (`st.alive`). Итог — по договору полем `status`, у моста с занятым «success» — полем `decision` (`shortOutcome`). Отказы `short-*` — словами, без служебного кода (`shortErr`, SL-UX-03): `short_need_bot` — `subShortNeedBot` (что делать — SSH-меню «Telegram-бот»), у известных отказов ядра (`short_pending`, `short_limit`, `short_not_found` …) — его слова, они сами говорят, что делать; у незнакомого — слова без хвоста «(код)» или «Короткие ссылки: действие не выполнено.» и «Повторите через минуту; не выходит — посмотрите «Логи»». Плашки «Выполняется… до 15 минут» у `short-*` нет: доли секунды в файле состояния ядра (`quickAction`, SL-UX-01) |
| «Пользователи» → вкладка «Короткие ссылки» (на телефоне вкладки короче — «Временные», «Короткие», полная подпись подсказкой: три — в одну строку на 375 px, PNL-COS-03; `renderShortTab`, `shortReqBox`, `shortDecide`, `shortBansCard`, `shortUnban`; только у ядра с `short-pending` и при настроенном боте — `tfa` сессии: токен и хотя бы один администратор, то же правило, что `short_available` ядра; без бота вкладки нет, SL-UX-02) | `short-pending`, `short-decide` (`request`, `answer` = `NN` \| `deny` \| `swap`; `who` ставит сервер), `short-bans`, `short-unban` (`target`) | «Ждут подтверждения» — запасной путь, если Telegram недоступен: на каждый запрос (`requests[]` без верного числа) — «Вход по короткой ссылке» / «Замена короткой ссылки», крупно город и устройство, IP и чья подписка, остаток времени (истёк — кнопки гаснут) и те же кнопки, что в боте: 10 чисел (`options`) в два ряда по 5 и «Это не я», у входа — ещё «↻ Заменить ссылку»; нажатие — `short-decide`, итог тостом (`ushDone_*`); новый код («↻ Заменить ссылку» у входа, подтверждённая замена) — окном «Новая короткая ссылка» (`shortNewLinkDialog`, SL-UX-06): код крупно с адресом сайта и «Скопировать», только в этом окне; решено раньше в другом месте (`already`) — «Уже решено: …»; отказ — тостом словами без кода (`shortShowError`), отказ чтения ждущих или банов — в карточке теми же словами. «Баны и заморозки» — таблица: цель (IP или «код ••••XXXX» — код целиком на экран не идёт), до когда, за что и на сколько словами (PNL-COS-03): «бан на сутки (первый раз)», «заморозка на месяц (повторно)» — по `level` ядра, «ступени N» нет; в описании — когда закрывается адрес («Это не я» — сразу, три неверных кода подряд) и когда замораживается ссылка (три неверных числа подряд); «Снять» — `short-unban` с целью. Число ждущих — на вкладке. Ждущие читаются **своим кругом** (`shortPollArm`, PERF-02): при открытии вкладки, затем не чаще раза в 10 с (`SHORT_PENDING_MS`) после ответа на прошлый опрос и **только пока вкладка открыта**; скрытое окно браузера, открытое окно панели, идущее действие — пауза; своё решение или снятие бана и «Обновить» — один внеочередной опрос, после него круг заново; обновление раздела ждущих само не перечитывает. Сервер читает их **без моста** (`short_pending_local`): модулем ядра `lib/shortlinks.py` в своём процессе, под его замком (flock `subs/.short/.lock`), тем же `pending()`, что зовёт мост (просроченный закрывается так же — событие `short_decided` боту, строка effect в аудит); наружу — только поля договора (`SHORT_PENDING_FIELDS`), ни кода, ни верного числа; модуля нет или сбой — мостом. Баны — при открытии, после решения и снятия и по «Обновить» |
| карточка «Копии приложений для клиентов» на «Настройках» (`renderMirror`, `openMirrorFetch`, `settingsActions`) | `mirror-fetch` (`app`, `platform` — выбор из `GET /api/clients`: приложение → платформа из `mirror_platforms`), `mirror-remove` (`app`, `platform`, `confirm` = `app`, после `confirmDialog`) | список с состоянием (`pill`), версией, размером, «занято X из Y MB»; на ядре без модуля карточка говорит `mirrorUnavailable`, кнопка «Скачать на сервер» скрыта |
| `POST /api/auth/request` · `verify` · `miniapp` · `logout` | — | вход по коду / подписи Telegram / выход; все POST — с заголовком `X-GoAmnezia-Admin: 1`. Отказ `/auth/request` несёт `code` и отвечает по существу (`TFA_HTTP_STATUS`): 429 — правда слишком часто, 503 — бот не настроен, 409 — администратор не нажимал Start у бота, 502 — Telegram не принял сообщение (DEFECTS §3.11). **Каждый отказ входа** (решение владельца В4, 26.09.2026) — `code`, `texts{ru, en}` (тот же текст на обоих языках) и `error` на языке панели (`config.json`), чтобы экран входа любого интерфейса показал причину на своём языке; статусы и логика прежние. `/auth/request`: `tfa_off` (`reason`: `no_admins` \| `no_token`), `too_often` (`reason`: `cooldown` + `seconds` \| `hour_limit` \| `net_limit`), `chat_not_started`, `send_failed` (слова Telegram — как есть). `/auth/verify` (401): `code_expired` (своего живого кода нет), `code_wrong` (+ `left`), `code_too_many_attempts`. `/auth/miniapp` (401; 0.5.0 — причина различается, L-03): `signature_invalid` (`reason: bad_signature` — подпись не подошла, подделка или испорченная ссылка), `miniapp_expired` (`reason: expired` — подпись верна, но старше `MINIAPP_MAX_AGE_SECONDS`), `miniapp_not_admin` (`reason: not_admin` — подпись верна, человек не из `ALLOWED_IDS`), `miniapp_not_configured` (`reason: no_admins` — у бота нет администраторов, `reason: no_token` — в `.env` нет `BOT_TOKEN`); пока подпись не верна, про администраторов ответ не говорит ничего (`telegram_init_data_check`). Стража сессии (401 на любом маршруте за входом): `session_renew` (`state: renew`, `seconds`), `session_expired` (`state: expired`), `auth_required` (`state: none`) |
| `POST /api/auth/password` | — | вход по логину и паролю: тело `{login, password}`. Маршрут **открыт** (это и есть вход). 200 + `Set-Cookie` сессии; 401 `bad_credentials`; 429 `too_many_attempts` (корзина адреса исчерпана); 409 `password_unset` (пароль на сервере не задан); у отказов — `texts{ru, en}`, у 429 — `seconds` до разблокировки |
| `POST /api/auth/password/change` | — | смена пароля: тело `{current, new}`. Маршрут **за стражей** — нужен туннель, сессия, подпись мини-аппа или токен. `current` обязателен, пока пароль есть; когда его нет (обновление со старой установки) — не спрашивается. 200 `{login, message}`; 401 `bad_credentials`; 400 `bad_password` (короче `min_len` или недопустимые символы); 500 `password_write_failed` (файл не записался); 429 `too_many_attempts`; у отказов — `texts{ru, en}`. Логин здесь не меняется — это дело `install_admin.sh --login=` |

Поля успешных мутаций, которые панель показывает: `message` всегда; `profile-set-param` →
`reissue_required` (предупреждение «ключи перевыпустить»); `backup-create` → `file`;
`miniapp-on`/`miniapp-rotate` → `url` (если ядро его вернёт; иначе адрес строится из
`config.json`).

**Действия панели на домене и Pro-обвязка** (всё реализовано в диспетчере):

- `miniapp-status` (readonly) → `enabled`, `configured`, `available`, `reason`, `hint`, `mode`,
  `domain`, `panel_port`, `site{nginx_active, https_ok, cert_ok, verdict}` (в Lite `site: null`).
  `enabled` — панель на домене **работает**; `configured` — путь записан в `config.json`;
  `available` — режим её вообще допускает (домен с доверенным сертификатом, `_site_panel_reason`,
  тот же вопрос, что у `miniapp-on`); `reason` — `no_site` | `no_domain` | `untrusted_cert`,
  `hint` — та же строка словаря, что и в отказе. Раньше `enabled` читался из одного лишь конфига,
  и в `lite` ответ рапортовал «включено» над панелью, которой нет (DEFECTS §1.8). Секретного пути
  в ответе нет намеренно — его выдают только `miniapp-on` / `miniapp-rotate`, помеченные
  `"secret": true`.
- `miniapp-on`/`miniapp-rotate` → `website_panel_path_set "$(ga_site_panel_path_new)"`, в ответе
  `url`, `path`, `changed`; `miniapp-on` на уже включённой панели путь не трогает (`changed=false`),
  `miniapp-rotate` на выключенной отвечает `panel_disabled`. `miniapp-off` → `website_panel_path_clear`,
  работает и в Lite. nginx-блок панели (`_ga_site_panel_block`) проксирует под
  `Host 127.0.0.1:<panel.port>` и ставит `X-Forwarded-Host/Proto`, `X-Real-IP`, `X-Forwarded-For` —
  ровно те четыре заголовка, по которым панель отличает домен от туннеля. `panel.port` в
  `config.json` обязан совпадать с портом юнита.
- `change-mode` → `mode`, `domain`, `template`. **Все три режима достижимы из панели** (`mode-lite`, `mode-lite-site`, `mode-pro`; кнопка текущего режима прячется), и название режима одно на все экраны — `modeName()` по словарю (`modeLite` / `modeLiteSite` / `modePro`), те же слова, что печатает мост. Прежде кнопок было две, `lite_site` из панели был недостижим вовсе — хотя предполётная проверка на этой же странице показывала его доступным, — а на обзоре и в настройках он подписывался «Lite», то есть именем соседнего режима. **Переход между двумя режимами с сайтом идёт лёгким путём**: домен не спрашивается и не передаётся, и мост уходит в `website_switch_site_mode` — одна запись `mode`, без certbot и без пересборки vhost. Домен спрашивается только из `lite`, где сайта ещё нет. `change-template` → `template{template_id, name, deployed}`;
  `site-check` → `site{…}`, `verdict` (readonly, панель зовёт через `POST /api/action/site-check`).
  Подтверждение перехода в Pro (`proHint`) прямо говорит, что **UDP-порт VPN не переезжает**:
  на TCP/443 встают сайт и панель, а профиль остаётся на своём порту, перенос — отдельная
  кнопка «Занять UDP/443» на странице профилей (DEFECTS §1d.1 №10).
- Язык панели она **не** меняет через мост: `lang-set` в `actions.json` есть (`mcp: null`, меняет
  язык SSH-меню, панели и умолчание бота), но переключатель панели работает **на браузер**
  (localStorage), а умолчание берёт из `config.json .lang`. Так задумано: язык интерфейса — выбор
  того, кто смотрит, а не свойство сервера.

## 5a. Общий снимок частых чтений и автообновление (CPU-аудит 03.10.2026)

На сервере в 1 vCPU открытая «Пользователи» держала панель на трети ядра, а в пиках — на 80–97 %: каждое чтение —
полный `install.sh` с модулями, проверкой зависимостей, десятками jq и docker, а автообновление каждые 10 с слало
новый опрос поверх идущего. Теперь:

* **Браузер** (`app.js`, «Автообновление»): следующий опрос — через 10 с **после ответа** на предыдущий; паузы прежние
  (окно, вход, скрытая вкладка, идущее действие); после сбоя интервал растёт вдвое до минуты. Обновление раздела
  одно за раз: автоопрос присоединяется к идущему, «Обновить», переход и повтор ставят **одно** следующее; поздний
  ответ не затирает более новый (порядок по разделу, `seqNext`/`seqLatest`). «Обновить» шлёт `X-GoAmnezia-Fresh: 1`.
* **Сервер** (`server.py`, `_shared_read`): частые чтения без параметров из `READ_CACHE_TTL` (и `status --brief=on`)
  идут через общий снимок. Одинаковые одновременные промахи (действие + язык ответа + параметры) ждут **один**
  вызов моста (запрос без заголовка языка — curl, скрипты проверки — живёт на снимках языка установки, и мосту
  этот язык уходит явно: раньше у него были свои снимки, холодные после рестарта, PERF-08); ошибка, таймаут и исключение отпускают всех ждущих; таблица ожиданий ограничена (32). Сроки:
  `traffic` 15 с, `status` и `profiles-status` 30 с, `user-list` 60 с, `share-list` 30 с (остаток жизни ссылки —
  по часам, истёкшая не показывается), `edge-status` 120 с (его счётчики пишет тик сторожа раз в две минуты),
  `kmod-status` 5 с (карточка «Ускорение AmneziaWG» и её опрос хода — одно чтение на все вкладки).
* **Раньше срока** снимок сбрасывают: мутация из панели — новое поколение, всё собранное раньше несвежее; чтение,
  начатое до мутации, свой ответ в кэш не кладёт, новый запрос к нему не присоединяется. Правки из бота, SSH и
  таймеров — по признакам файлов (inode + mtime_ns + size): `config.json`, `users.json`, у `traffic` — `stats.json`,
  у `share-list` — раздачи в `/run/goamnezia/share`, у `edge-status` — `state/edge_ips.csv`, `edge_alerts.json`,
  `dns.cache`, у `kmod-status` — `state/kmod.json`, `/run/goamnezia/kmod-progress.json` и `kmod-engines.json` (новый
  шаг долгого действия виден при следующем же опросе). Признаки снимаются до вызова моста: файл, сменившийся во время сбора, делает снимок несвежим сразу.
  Состояние контейнеров, сменившееся мимо продукта, видно по сроку (30 с).
* **Свежесть.** Ответы экранов несут `snapshot_at` — время начала сбора (ISO, UTC); интерфейс показывает его в
  подсказке «Обновить». Сбор не удался по таймауту или транспорту — экран получает прежнюю удачную картину (не
  старше 10 мин) с пометкой `stale{code, message, since}` («Данные на …» над списком), а не пустоту; отказ по
  существу (битый `users.json`, `config_corrupt`) — ошибкой, как прежде. «Обновить» (`X-GoAmnezia-Fresh: 1`)
  пересобирает снимок старше 2 с и присоединяется к идущему сбору; чтения без ожидания (Обзор, «Настройки»,
  `/api/traffic?wait=0`) при этом отвечают прежним снимком, а новый собирается в фоне — Обзор пишет их в
  `pending` и переспрашивает (раньше «Обновить» давал Обзору «холодную» форму без трафика и адресов, PERF-08).
* **Секреты.** В снимок попадают только публичные ответы: `user-key` и прочие секретные действия мимо него; ответ,
  в котором есть поле с секретным именем (`SECRET_KEYS`), в кэш не ложится. Для снимка панель смотрит у
  `users.json` только `stat` его признаков; содержимое читает модуль ядра (§5b).
* **Холодный Обзор (PERF-03).** Обзор ждёт только состояние ядра (`status --brief=on`), и то — лишь когда нет его
  снимка не старше 10 мин (`READ_SWR_MAX_AGE["status"]`): снимок старше срока, но моложе — ответ сразу, новый сбор
  в фоне, в ответе `swr{since, reads, age}`. Рукопожатия (`traffic`) и адреса сервера (`edge-status`) Обзор не ждёт
  вовсе: снимок не старше 45 с и 10 мин — из него, иначе сбор в фоне и `pending: ["traffic", "edge-status"]`.
  `users_online` — `null` («не знаем»), пока рукопожатия не прочитаны: снимка нет, он старше 45 с (прежняя
  картина после сбоя), Docker не ответил (`docker_unreachable`) или у работающего профиля с людьми пустой список
  пиров (ядро прячет сбой `awg show` в пустой список). Учёт трафика — из снимка не старше 10 мин
  (`TRAFFIC_TOTALS_MAX_AGE`, PERF-05: счётчики только растут): `traffic_total` и его возраст `traffic_total_age`, а
  `/api/traffic?wait=0` отдаёт сутки, месяц и часы с `online: null` (и у людей, и у профилей), пока рукопожатиям
  больше 45 с; «на связи» тогда — `null` и `pending: ["traffic"]`, новый сбор уже идёт. `traffic_total` — `null`
  только без снимка. Плитка «Пользователи» пишет «— на связи», «Трафик» — прочерк и «Загрузка…» (учёт из
  прежнего снимка — с подсказкой «Данные на …»), карта часов — «Загрузка…»; страница переспрашивает через 1,5 с
  (не дольше 30 с подряд), пока есть `pending` или прежняя картина старше минуты (`swr.age`); пауза (окно, вход,
  скрытая вкладка, идущее действие) переспрос не съедает — повтор через секунду (PNL-05). Трафик Обзор просит как
  `/api/traffic?wait=0`; раздел «Трафик» ждёт свежий сбор, как раньше. «Настройки» подсказки (`status`,
  `edge-status`) не ждут — `pending`, команда туннеля до тех пор с адресом сервера. Здоровье служб (`systemctl`) —
  та же картина: прежняя до 10 мин, сбор в фоне. `GOAMNEZIA_READ_SWR=0` — всё ждёт свежий сбор, как в 1.0.9.
* **Первый кадр и одно ядро (PERF-04).** На 1 vCPU всё, что идёт рядом с первым `/api/overview`, делит с его
  status ядро (на 168 в браузере — 4,2–4,5 с вместо 1,2–1,5). Поэтому страница до первого кадра просит только
  `/api/overview`, каталог, сессию и вид; `/api/traffic?wait=0` — после ответа Обзора (сбор трафика он запускает
  сам), «Настройки» — после первой загрузки раздела, медленные части (`/api/logs/status`, `backup-last`) — когда
  Обзор дописан и каталог действий пришёл (без каталога `backup-last` считался изменяющим действием и включал
  плашку «Выполняется», PNL-05; заведомо читающие действия плашку и без каталога не включают). Сервер запускает
  фоновые сборы (прежний снимок с новым сбором, подогрев, `wait=False`) через `nice` и `ionice -c 2 -n 7`:
  `traffic` — nice 10, `edge-status` — 19, остальное — 15 (`READ_BG_NICE`, вес планировщика 110, 15 и 36 против
  1024 у переднего); передний запрос получает ядро первым, фон идёт, когда оно свободно.
  `GOAMNEZIA_READ_BG_NICE=0` — без понижения, число — один уровень для всех.
* **Плитки Обзора (PNL-07, PNL-08).** Плитку «Трафик» целиком — период (`30d` — сумма суточного учёта за 30 суток,
  `month`, `all` — всё накопленное), сумму, 14 суток для столбиков и время снимка — сервер отдаёт в `/api/overview`
  (`traffic_kpi`) из того же снимка, что и `/api/traffic`: подпись и число первого кадра не меняются, когда следом
  приходит учёт (на 168 «за всё время» через 0,35 с становилось «за 30 дней»). Пока учёт собирается (`traffic_kpi:
  null`), подпись — прошлого периода (`localStorage` `goamnezia-ov-period`). Счёт от нуля — только у первого
  значения плитки после прочерка; известное к кадру или уже показанное число встаёт сразу, а перерисовка посреди
  счёта его продолжает. «На связи» — только из ответа Обзора моложе 45 с (окно рукопожатий сервера): переход на
  Обзор после простоя рисует прежнюю картину сразу, но с «— на связи» и без лиц «в сети», пока не придёт новый
  ответ; `users_online: 0` — ноль, а не число «в сети» из прежнего учёта трафика.

Ядро со своей стороны дешевле вдвое (`lib/deps.sh` — отметка «core уже проверен» для чтений и тиков,
`ga_config_memo_load`, `_action_meta`, `ga_emit_json` одним jq — docs/CONTRACT.md, `status --brief=on`).

## 5b. Частые чтения без моста: `lib/core_read.py` (PERF-01, 05.10.2026)

На сервере в 1 vCPU (168, 1.0.10) карточка человека открывалась 4,6–7,7 с, ключ — 3 с, адреса подписки — 2,5 с
при нагрузке около единицы: каждое чтение было процессом `install.sh` — bash, два десятка модулей и словарь
(0,6 CPU-с одна загрузка), отметка зависимостей, снимок `config.json`, от 8 до 113 jq, у ключа ещё четыре python3.
Теперь (замеры «до» и «после» — docs/CPU_OPTIMIZATION_HANDOFF_2026-10-03.md, «Часть C — сделано»):

* **Что.** `user-list`, `user-show`, `user-key` (без разового `--endpoint`), `share-list`, `sub-list`, `sub-show` и
  `page-list` отвечает модуль ядра `lib/core_read.py` в процессе панели (`_read_exec` у `run_action`): те же файлы
  (`users.json`, `config.json`, `subs/`, `pages/`, `/run/goamnezia/share`, `lib/clients.json`, словари `lib/lang`),
  те же правила, тот же JSON — поле в поле и в том же порядке, слова — на языке запроса. Ключи — тем же кодом
  `lib/awgkeys.py` (импортом, а не процессом), QR — тем же `qrencode` (stdout, без временных файлов), короткие
  ссылки — тем же `lib/shortlinks.py` (только чтение `codes_of`/`available`). Выдача ключа и показ адресов
  подписки — та же строка аудита `stage: effect`, что пишет ядро, плюс прежняя `call` панели.
* **Когда мост.** Модуль отвечает `None`, и тогда отвечает мост, на всё, в чём не уверен: любой отказ (нет
  человека, ключа, подписки, профиля — коды, слова и спасательная копия битого файла — у моста), разовый адрес в
  ключе, адрес сервера не записан в `config.json` (ядро спросило бы сеть), дробные числа и поля не того типа,
  подпись с `!*'()` в адресах Clash (`@uri` у jq 1.6 и 1.7 разный), раздача, которую уборщик ядра снял бы
  (истекла, процесс раздачи умер — это мутация, её делает мост), порядок имён каталога в не-C локали. Изменения —
  только мостом под его замком, как прежде.
* **Сверка с ядром.** Места ядра, которые модуль повторяет (`MIRROR`: ветки диспетчера и его начало, разбор и
  проверка аргументов моста, `ga_users_public_filter`, `export_bundle`, `_ga_subs_answer`, `share_list`,
  `pages_list` и их помощники, переменные модулей; обвязка командной строки, через которую мост зовёт
  `awgkeys.py` — `conf`, `vpn-encode`, `qr-chunks`, `detect-line` — и `shortlinks.py urls`, а модуль — те же
  функции импортом; описания действий и типы их параметров в `lib/actions.json`), хешируются по месту без
  комментариев и docstring (`PLACES`) и по действию (`RECORDED`); отпечаток действия не совпал с записанным — его
  быстрый путь выключен до сверки, чтение идёт мостом, в журнале одна строка. Поправили такое место или слили ветку с такой
  правкой — `python3 tests/check_core_read.py` (секунда, без bash; он же в `tests/run_static.sh` среди сверок)
  называет изменённые места и их действия, показывает дифф с ревизии сверки и шаги: меняется ли ответ действия
  (правка только текста отказа — нет: отказы модуль отдаёт мосту) → равенство с мостом `tests/test_core_read.py` →
  `python3 lib/core_read.py --fingerprint --write`.
* **Замок.** На время чтения файлов модуль держит `config.lock` на чтение (`flock LOCK_SH`): правка ядра под
  `LOCK_EX` (users.json и config.json вместе) видна целиком — до или после. Ждёт не дольше
  `GOAMNEZIA_READ_LOCK_WAIT` (1 с, не больше 5): дольше замок держит только установка или обновление, и тогда
  чтение идёт без замка, как у моста (каждый файл ядро пишет атомарно). Под замком — только чтение файлов: адреса
  машины (`ip`, нужны ключу и карточке человека со своим адресом) собираются до него — чтение просит их и
  повторяется, `qrencode` — после; дескриптор — `O_CLOEXEC`. Весь ответ — не дольше 20 с (`ANSWER_TIMEOUT`: замок,
  `ip`, `qrencode` вместе), вышел срок — отвечает мост.
* **Секреты.** Список, карточка, страницы, подписки без адресов и раздачи — без `privkey` и `psk`; `user-key` и
  `sub-show` в общий снимок не попадают (их нет в `READ_CACHE_TTL`, `_holds_secret`); `users.json` разбирается на
  каждый ответ и в памяти панели не хранится; сбой быстрого пути — строка с видом ошибки, без данных.
* **Живые данные у карточки и списка людей.** Рукопожатия (`traffic`) и адреса сервера для «ключа на другой адрес»
  (`edge-status`) карточка и список берут из снимка и тогда, когда он старше срока: `traffic` — до 45 с,
  `edge-status` — до 10 мин (`READ_SWR_MAX_AGE`); ответ сразу, новый сбор — в фоне, один на ключ (`_shared_read`
  с `swr`, в заголовке X-GoAmnezia-Snapshot — `swr`). 45 с — от «в сети»: оно считается от нынешнего времени с
  окном `ONLINE_WINDOW` (180 с), активный пир обновляет рукопожатие раз в 120 с, и снимок старше минуты показал бы
  его «не в сети» (CR-2); старше 45 с — список и карточка ждут свежий сбор. Ответ из прежнего снимка несёт рядом
  со `snapshot_at` поле `swr`: `since` — время снимка, `reads` — какие чтения (`snapshot_meta`; у карточки —
  `traffic` и `edge-status`). Список людей в фоне подогревает `edge-status` для карточки (`read_prefetch`), если
  свежей картины нет. «Трафик» и «Протоколы» ждут свежий сбор, как раньше; Обзор — §5a «Холодный Обзор». `GOAMNEZIA_READ_SWR=0` — без
  прежнего снимка и без подогрева.
* **Где лежит.** Рядом с мостом: `lib/` у настоящего `install.sh` (`GOAMNEZIA_ACTION_BIN`), перечитывается по
  mtime — обновление ядра рестарта панели не требует; привозит `bootstrap.sh`. Нет файла (ядро старше) — всё
  мостом.
* **Выключатели.** `GOAMNEZIA_CORE_READ=0` — частые чтения мостом; `GOAMNEZIA_READ_SWR=0` — живые данные у карточки
  и списка ждут свежий сбор. Оба вместе — чтения панели как в 1.0.9. Задаются drop-in-ом юнита (§3), не правкой
  юнита: его переписывает `install_admin.sh`.
* **Проверки.** `tests/test_core_read.py`: равенство с мостом на стенде dry-run (шесть человек, страницы,
  раздача, короткие ссылки; Lite, Pro, вписанный домен и IP, DNS и MTU из конфига, линия по параметрам; русский и
  английский), края, найденные контролем (истёкший человек, неактивный пир, awg1 с линиями 1.5 и 1.0,
  перевыпущенная и отозванная подписка, короткие ссылки без бота, истёкшая страница и раздача, язык из
  `config.lang` и `config.language`, `lite_site` и самоподписанный сайт, профиль из приложения без `line`, нет
  `server_ip`, `LANG=en_US.UTF-8`, интервал подписки «012»), отказы — мосту, строки аудита, замок (чтение во время
  правки ядра видит всё или ничего; висящая правка держит чтение не дольше `lock_wait`; `ip` и `qrencode` — без
  замка; срок ответа), секреты, «открытие карточки не запускает мост» (до PERF-01 — четыре запуска: `user-show`,
  `user-key`, `page-list`, `sub-list`), выключатель, нет модуля или он битый — мостом, фоновое обновление снимка
  списка и «в сети» на прежнем снимке, `GOAMNEZIA_READ_SWR=0`, отпечаток зеркала (правка кода в каждом месте
  выключает быстрый путь ровно его действий, правка комментария — нет).

## 6. Модель входа (решение владельца от 10.09.2026)

Панель ставится в любом режиме (мастер — без вопроса, NC-13), поэтому с домена равноправных входов
два: код в Telegram (главный, когда бот настроен) и логин с паролем (единственный, когда бота нет;
запасной, когда бот есть). Через туннель не нужно ни того, ни другого. Прежние три пути остались как
были.

| Способ | Когда доступен | Чем доказывает |
|---|---|---|
| **SSH-туннель** (петля) | всегда, пока `GOAMNEZIA_TRUST_TUNNEL=1` (или `panel.trust_tunnel` ≠ `false`) | входом по SSH: ни одного из `X-Forwarded-Host`, `X-Real-IP`, `X-Forwarded-For`, `X-Forwarded-Proto` **и** адрес сокета петлевой. Ни кода, ни пароля |
| **Код в Telegram** | когда в `.env` бота есть `BOT_TOKEN` **и** `ALLOWED_IDS` (`tfa` в `/api/session`) | владением аккаунтом админа: код 6 цифр, 5 минут, одноразовый, 5 попыток, уходит только по нажатию кнопки |
| **Логин и пароль** | когда задан `password.json` (`password` в `/api/session`) — то есть после любой установки этой версии | знанием пароля: сверка `hmac.compare_digest` с выводом KDF по соли из файла; 5 попыток за 15 минут на адрес |
| **Подпись мини-аппа** | режимы с сайтом, панель на домене; открытие из бота | HMAC `WebAppData` по `initData`, `user.id` из `ALLOWED_IDS`, не старше суток |
| **Токен `?token=`** | всегда (аварийно) | root-only файлом `/opt/goamnezia-admin/token`; поля ввода в интерфейсе нет. Страница входа советует открывать его только через SSH-туннель — `http://127.0.0.1:<порт>/?token=<токен>`, не на домене: бессрочный токен остался бы в истории браузера (SEC50-08). Приём `?token=` и на публичных запросах — как был (решение владельца) |

Что показывает форма входа:

| Бот | Пароль | Что видно на странице |
|---|---|---|
| настроен | задан | кнопка «Прислать код» и поле кода **первыми**, ниже разделитель и поля логина с паролем |
| **не настроен** | задан | **только** логин и пароль — про код не сказано ни слова, чтобы не звать туда, куда не пустят |
| настроен | не задан | код; вместо формы пароля — как его задать (`install_admin.sh --reset-password`) и команда туннеля |
| не настроен | не задан | «войти отсюда нечем»: команда туннеля и как задать пароль; аварийный токен строкой ниже |

- **Хранение пароля**: `password.json` (0600 в каталоге 0700) — `login`, `algo`, `salt`, `hash`,
  параметры KDF, `updated`. Пароля в открытом виде нет ни в файле, ни в `config.json`, ни в логах,
  ни в аудите, ни в одном ответе API. KDF — `hashlib.scrypt` (n=2¹⁴, r=8, p=1, 32 байта; ~16 МБ
  памяти на проверку), на сборках Python без OpenSSL 1.1 — `pbkdf2_hmac('sha256', …, 200 000)`;
  параметры читаются **из записи**, поэтому смена умолчаний не обесценивает уже выданные пароли.
- **Имя учётной записи страница входа не называет**: поле логина пустое, в подсказке — слово
  «Логин», в отказе — «неверный логин или пароль» на оба случая. Раньше форма подставляла логин
  значением поля ради экономии одного ввода — и дарила половину пары к паролю всякому, кто открыл
  страницу из интернета: подбирать оставалось одно значение вместо двух (DEFECTS §1.9). Свой
  человек логин и так знает; проверка — `test_admin_features.py::LoginPageTests`.
- **Проверка пароля**: KDF считается всегда — и когда логина нет вовсе (заготовка-пустышка со
  случайной солью), иначе «нет такого логина» отвечало бы мгновенно, а «пароль не подошёл» — через
  десятки миллисекунд, и логин подбирался бы, не зная пароля. Поверх — общий пол ответа 0.35 с, в
  том числе на 429: быстрый отказ сам сообщал бы перебирающему, что он нащупал предел.
- **Корзина попыток пароля**: 5 неудач за 15 минут **на адрес** (IPv6 — на сеть /64) → временная
  блокировка до момента, когда первая из пяти выпадет из окна; успех корзину обнуляет. Проверка
  предела и запись попытки — **один шаг под замком ДО проверки пароля** (`password_attempt_begin`):
  прежде было «заперт ли?» → проверка (KDF + пол 0.35 с) → «записать неудачу», и пачка параллельных
  запросов с одного адреса проходила первый шаг вся — догадок было столько, сколько запросов успели
  отправить за время одной проверки (SEC-P1, аудит 25.09.2026; проверки — `PasswordStoreTests`,
  `PasswordLoginTests.test_parallel_wrong_passwords_get_five_tries_not_more`). Так же устроена смена
  пароля из панели: текущий пароль перебирается той же корзиной.
- **Чей это адрес** (`client_ip`): `X-Real-IP` ставит свой nginx директивой `proxy_set_header`,
  которая перезаписывает присланное клиентом, — поэтому заголовку верим **только на петлевом
  слушателе**, за которым этот nginx и стоит. Панель можно поднять и не на петле
  (`GOAMNEZIA_ADMIN_HOST`): там заголовок целиком клиентский, и адрес берётся только из сокета,
  иначе корзину перебора выбирал бы сам стучащийся — новую и чистую на каждый запрос, а чужие
  адреса запирал бы чужими блокировками. Правило то же, что у MCP-сервера
  (`goamnezia-mcp/server.py`, `McpHttpHandler.client_ip`).
- **След**: в journald — факт и адрес («вход по паролю: неудачная попытка (net, 203.0.113.5),
  осталось попыток: 3»), в `/var/log/goamnezia-audit.log` — строка `auth-password` с `origin` и
  остатком попыток, **без логина и без пароля**.
- **Сессия** 15 минут → 60 секунд на продление кодом (данные не отдаются) → «войдите заново».
  Одинаковая для всех способов; `who` = `tunnel` · `code` · `password` · `token` · `tg:<id>`.
  Корзины кода: публичный вход — **на адрес** `net:<адрес>` (адрес — `X-Real-IP` своего nginx,
  IPv6 — сетью /64) 5/час; без адреса — запасная общая `net` 5/час; туннельный IP 30/час; `renew`
  12/час; кулдаун 30 с в каждой. Поверх — **общий счёт на все внешние корзины вместе**, в два уровня
  (SEC46-06): после 20 кодов в час (`TFA_REQUESTS_PER_HOUR_EXTERNAL`) адресу выдаётся только первый код
  часа, повторный — нет; сверх 60 (`TFA_REQUESTS_PER_HOUR_EXTERNAL_MAX`) — никому. Каждый код — сообщение
  админу в Telegram, и страховка ограничивает перебор (не больше 5 × 60 догадок в час); прежний единый
  потолок 20 выбирали четыре адреса по пять, теперь для этого нужно 44 адреса за час. Туннель и
  продление под счёт не попадают. Код
  проверяется **только в своих корзинах** (`tfa_verify`): адрес этого запроса и код продления, который
  просила эта же вкладка (отпечаток её cookie). Прежде одна корзина `net` на весь интернет была
  готовым отказом в обслуживании: знающий домен раз в 30 секунд выжигал общие пять кодов в час,
  затирал код администратора в общем слоте и пятью неверными догадками выжигал и его (SEC-P2, аудит
  25.09.2026; проверки — `TfaBucketTests`, `TwoFactorTests.test_public_code_bucket_is_per_address`).
  Память ограничена: корзины без запросов за последний час выметаются на каждом запросе кода.
  Продлевают только кодом — когда бота нет, интерфейс не показывает баннер продления вовсе, а сразу
  зовёт войти заново (логином и паролем). Состояние — в памяти процесса: рестарт панели
  разлогинивает всех.
- **Смена пароля**: `install_admin.sh --reset-password` (печатает новый в терминал) либо
  «Настройки → Пароль панели» в самой панели (текущий + новый дважды, не короче 12 символов).
  Пока пароля нет (обновление со старой установки), панель по туннелю показывает подсказку задать
  его, а вход по паролю отвечает `409 password_unset`.
- **DNS-rebinding**: `Host` только `127.0.0.1`, `localhost`, `[::1]`; nginx проксирует под `Host 127.0.0.1:1985`.
- **CSRF**: все POST требуют `X-GoAmnezia-Admin: 1`; разрушительные действия — `confirm`.
- **CSP** без внешних источников: подпись мини-аппа читается из `#tgWebAppData` в адресе, а не из
  скрипта с telegram.org (у аудитории он часто заблокирован); `frame-ancestors` — только Telegram.
- Секреты: `token` 0600; `BOT_TOKEN` не попадает в окружение моста и в логи; ответы `user-key` не
  логируются; в аудите параметры с ключами `privkey/psk/secret/token/…` маскируются.

## 6a. Интерфейс: решения, которые нельзя откатывать молча

Разбор критика по живой панели (10.09.2026). Каждый пункт закрыт кодом и проверкой; если
что-то из этого «упростить обратно», сломается ровно то, что здесь описано.

| Что было | Как сделано |
|---|---|
| **Пятнадцатиминутное действие показывало тост на три секунды.** Установка профиля идёт до 15 минут, тост гас через 3,2 с, и панель выглядела замершей | Плашка в шапке раздела (`#progress` в `index.html`, `startProgress`/`stopProgress` в `app.js`): название действия, объяснение «не закрывайте вкладку», счётчик времени. Показывается на **любой** мутации через 400 мс после начала (короткие действия успевают закончиться и не мигают), гаснет в `finally`. Пока плашка висит, автообновление молчит: ядро под замком, и лишний вызов только отнимет у него время |
| **Замок брался без таймаута.** Второй администратор ждал молча до 15 минут и получал «сервер не отвечает» | `_ACTION_LOCK.acquire(timeout=ACTION_LOCK_WAIT)` (3 с, `GOAMNEZIA_ADMIN_LOCK_WAIT`). Не дождались — `lock_timeout` → **409** с `busy_action` и `busy_seconds`; интерфейс говорит «сейчас выполняется другое действие (*имя*) — повторите через минуту». Чтение замок не берёт и не блокируется |
| **На телефоне меню нельзя было закрыть**: шторка накрывает кнопку ☰, подложки нет, тап мимо ничего не делает | `#navBackdrop` + `setNav()`: подложка (`--scrim`), закрытие тапом по ней, по Escape и переходом в раздел; `aria-expanded` на кнопке. Escape сначала закрывает окно, и только если окна нет — меню |
| **Поля мельче 16px** — iOS увеличивал страницу на каждом фокусе; тач-цели 30px при норме 44 | `@media (max-width: 600px)`: `input, select, textarea, button` → 16px и 44px, `button.small` → 44px, чекбоксы 22px. То же в окне входа поверх панели (`.auth-card input`) и на серверной странице входа |
| **Разрушительное выглядело как настройка**: «Перегенерировать параметры» оформлена как «MTU» и стояла рядом с «Перезапустить» | Отдельный ряд `.danger-row` у заголовка карточки профиля: подпись «НЕОБРАТИМОЕ», красная подложка, полные названия («Перегенерировать параметры», «Удалить профиль»). В ряду настроек остались порт, 443, подсеть, MTU, параметры, перезапуск |
| **Контраст метки «онлайн» ≈ 2,9:1** при норме 4,5; тем же болели предупреждения | Разделены заливка и текст: `--green/--amber/--red/--blue` — фон и точки состояния, `--ink-*` — текст поверх плашек. В светлой теме ink темнее (5,3–5,7:1 замерено на живой панели), в тёмной совпадает с цветом (там подложка тёмная) |
| **Три экрана входа в трёх темах**: панель по выбору, окно входа всегда светлое, серверная страница всегда тёмная | Одни переменные на всех: окно входа и баннер продления рисуются классами `.auth-gate`/`.auth-card`/`.renew-bar` (инлайновых стилей в `app.js` не осталось), серверная страница входа получила тот же набор переменных, `@media (prefers-color-scheme: dark)` и чтение `localStorage["goamnezia-theme"]` до первого кадра. Системная тема поддержана и в `styles.css` — через `:root:not([data-theme="light"])`, чтобы явно выбранная светлая не перекрашивалась |
| **Нет онбординга и пустых состояний**: обзор при нуле профилей молчал, таблица трафика без данных — голая шапка, логи до нажатия — пустое место | Обзор: блок «С чего начать» (шаги + кнопки в нужный раздел), пока нет профиля или пользователя. Пользователи: «Пользователей пока нет» + что нажать + кнопка; отфильтрованный список говорит «Ничего не найдено», а не «пользователей нет». Трафик: «Пока никто не подключался» и когда появятся счётчики. Логи: подсказка «выберите компонент и нажмите Показать» до первого показа + таблица бюджета. Раздачи: «Выданных ссылок нет» и где их выдают |
| **QR — белая карточка на тёмной теме** (владелец 04.10.2026): вкладки, подписи и сам код на белом во всю ширину карточки смотрелись чужими на тёмной панели | Блок QR (`.gx-qrcard` в карточке, `.gx-qrbox` в окнах страницы, подписки и агента) — плитка `--tile-bg` с сегментом и подписями в токенах темы; светлая только плашка самого кода. Код рисуется заново SVG (`qrArt`: `qrModules` читает модули из PNG qrencode — каждый модуль ровный квадрат, поля светлые, три поисковых узора и полосы синхронизации на месте; `qrSvg` — один контур, `crispEdges`) цветами `--qr-ink` на `--qr-paper` с тихой зоной 4 модуля; данные кода те же, смена темы перекрашивает сразу. Токены — смесь с акцентом под `@supports color-mix`: плашка 6 % акцента в белом (тёмная тема — 10 % в `oklch(.92 0 0)`), модули 22 % в `oklch(.18 .006 286)`; контраст модулей к плашке не ниже 8,5:1 при любом акценте (хуже всех — белый: «Графит» в тёмной теме, кнопка Telegram), без color-mix — `#18181b` на `#fff`. Код не инвертируется. Картинку не разобрали (нет canvas, не qrencode) — она сама на плашке через `multiply`. Проверка: `FrontendUiTests.test_qr_follows_the_theme`, `QrModulesTests` (модули SVG = модули PNG) |

## 6b. Линии на сервере: две одинаковые, нет 2.0, «Переставить на 2.0» (1.0.13, DUP-LINE-01)

Случай пользователя 15.10: подхваченный у приложения Amnezia `amnezia-awg2` держит 3.1 (UDP 46567), а «Установить»
ставил в awg3 ещё одну 3.1 (6714) — две «AmneziaWG 3.1», ни одной 2.0, старые приложения и Keenetic не подключаются.
Решение владельца: ядро ставит недостающую линию само, интерфейсы говорят правду.

- **Данные.** `/api/overview` и `/api/profiles` несут `lines_check{no_20, shared[], missing[], reline}` и `free_lines{место:
  линия}` (`lines_check_payload`, `free_lines_payload`, белым списком; `text` ядра не передаётся). В `profiles-status`
  этих полей нет — `/api/profiles` берёт их из краткого `status` (тот же снимок, что у Обзора). У профилей —
  `line_shared` (`null` у ядра старше: панель решает по списку сама), `imported`, `title`.
- **Подписи** (`lineTag` / `profName` в `app.js`, решение владельца 15.10): для пользователей — линия и порт, без имени
  места awg2/awg3: «AmneziaWG 3.1 · порт 6714»; подхваченный у приложения (`imported`) — «AmneziaWG 3.1 · порт 46567 ·
  из приложения»; свободное место — линией, которая встанет по «Установить» (`free_lines`): «AmneziaWG 2.0 — не
  установлена». Где порт стоит рядом своей колонкой или состояние — своей меткой (карточка и окно «Установить» в
  «Протоколах», строки и метки Обзора, «Линия на UDP 443», мастер режимов, «Ускорение AmneziaWG», таблицы с портом) —
  короткая подпись `{ short: true }`: «AmneziaWG 3.1 · из приложения», «AmneziaWG 2.0». «Кому он» и приложение
  свободного места — по его линии. `imported` в «Настройках» (`public_profile`) — чтобы подпись была верной и без Обзора.
  Имя места остаётся в запросах, именах контейнеров и фразах подтверждения.
- **Нет 2.0** (`no_20`): Обзор и мини-апп — строка «На сервере нет AmneziaWG 2.0 — Старые версии приложений и роутеры
  Keenetic на обычной прошивке не подключатся ни к одному профилю (3.1 Keenetic понимает пока только в dev-прошивке).» и, если есть `reline`, «… его можно переставить на AmneziaWG 2.0» с
  кнопкой «Переставить <подпись> на 2.0»; «Протоколы» — та же строка сверху, кнопка «Переставить на 2.0» — в карточке
  своего профиля (у профиля приложения её нет никогда — ядро его в `reline` не называет). Без `reline` строка говорит,
  как получить 2.0: «Протоколы» → свободное место → «Установить».
- **«Переставить на 2.0»**: окно согласия как у разрушительных (набор порта профиля): профиль удалю и поставлю заново
  на тот же порт и подсеть, прежние ключи перестанут работать, всем выдам новые, профиль приложения не трогаю; ниже —
  у каждого пользователя его действие после (подписка обновится сама — только у приложений, которые сами забирают
  `all.yaml`/`all.txt`; страницу открыть заново; ключ или файл выдать заново). Эти шаги ядро считает только в полном
  `status` (`lines_check.reline.steps`, `dupline_fix.steps`, `reline_steps_payload`: `kind` из набора, `text` ≤ 1500
  знаков, не больше 8; поля нет — ключа `steps` нет); краткий (Обзор, «Протоколы») их не несёт, и окно берёт их
  отдельно — `GET /api/profiles/<p>/reline-preview` → `{profile, steps[] | null}` (`reline_preview_payload`, полный
  `status`). Галочка «Выдать ключ на <подпись> остальным пользователям (N)» (ключа нет ни у кого — «… всем пользователям
  (N)») — по умолчанию отмечена и уходит в тот же запрос: `keys_to_others: "on"`, ключи выдаёт ядро вместе с
  перестановкой (DUPLINE-KEYS-01), браузер `user-add-peer` больше не зовёт — закрытая вкладка выдачу не обрывает. Мост —
  `profile-reline {profile, line[, keys_to_others]}`,
  `confirm` = профиль, таймаут 900 с; на карточке — «Перестановка на другую линию». Итог — окно «Профиль переставлен ·
  AmneziaWG 2.0»: `summary` и по строке на `steps[]`, как у «Исправить S4» (там же шаги ядра `others` — «Ключи на
  AmneziaWG 2.0 выданы и остальным пользователям: …» — и `others_failed`). `reline_rolled_back` / `reline_failed` —
  окно «Переставить … не вышло» со словами ядра (кому выданы ключи); прочие отказы — как обычно. Кнопка «Исправить» /
  «Переставить» блокируется сразу (окно согласия собирается 1–3 с), второе окно не открывается (PNL-1). После удачи
  плашка «Ошибка прошлых версий» и строка «нет 2.0» уходят с Обзора сразу, Обзор перечитывается свежим (PNL-3).
- **«Удалить» профиль** (PROFILE-REMOVE-PAGES): до окна согласия — `profile-remove-preview` (только чтение); есть
  страницы подключения, где был только этот профиль, — в окне предупреждение «Страница подключения у anna перестанет
  работать: в ней был только этот профиль.» (несколько — «Страницы … перестанут …», имена — `Intl.ListFormat`). Пока
  превью идёт, кнопка «Удалить» заблокирована. Ядро не ответило — окно без строки; само удаление прежнее.
- **Сообщение после обновления** (`dupline_fix` в `status`, `dupline_fix_payload`: `{profile, from, line, port, title,
  users, detected_at}`, `text` ядра не передаётся): пока поле не `null` — на Обзоре (и в мини-аппе) янтарный блок
  (DUPLINE-BANNER): заголовок «Ошибка прошлых версий: вместо AmneziaWG 2.0 встала вторая 3.1», одно предложение
  (`dupFixS`: «На сервере нет AmneziaWG 2.0: старые версии приложений … не подключатся к 3.1, а роутеры Keenetic на
  обычной прошивке — только к AmneziaWG 1.5 или 1.0, если она стоит (3.1 Keenetic понимает пока только в dev-прошивке).») и ссылка «Подробнее» — она раскрывает остальное (`dupFixD`/`dupFixD0`: что сделает исправление, кому что
  делать, контейнер приложения не тронем); раскрытое держится до перезагрузки. Кнопки «Исправить» (тот же поток
  «Переставить на 2.0») и «Позже» (блок скрыт до перезагрузки страницы; тогда видна обычная строка `lines_check`). Пока
  блок виден, строки `lines_check` о том же нет.
- **R4**: советы, ведущие в 2.0, которой нет, заменены правдой — «Линия на UDP 443» вместо «Рекомендуем 2.0» и окно
  нового человека говорят «На сервере нет AmneziaWG 2.0.» и как её получить. Отказ Ленивого Pro `awg2_container_busy` —
  «Место awg2 занимает контейнер приложения Amnezia amnezia-awg2 с пользователями …» (занято место, а не линия 2.0).
- **KEY-APP-02**: у человека все ключи только в приложении Amnezia (`has_key: false` у всех пиров) — во вкладке, кроме
  «Привязать старый ключ», строка, почему нет QR, файла и ссылок, и что «Перевыпустить ключ …» внизу карточки; окна
  временной ссылки, страницы и подписки не открываются (ядро ответило бы `no_private_key`) — вместо них окно «Все
  ключи — в приложении Amnezia». Смешанный случай — как раньше: профили без ключа не предлагаются или помечены.
- **Проверки**: `tests/test_panel_dom.py` (`PanelDupLineDomTests`), `tests/test_panel_modes_dom.py`
  (`line_dup_ru`, отказ `awg2_container_busy`), `tests/test_admin_features.py` (`SubnetOverlapPayloadTests` — поля
  линий и `dupline_fix`). `GA_PANEL_DOM_ROOT=<корень>` гоняет те же сценарии на другом `app.js` (на прежнем коде они красные).

## 6c. Пользователи, страницы, подписки: окна 1.0.13 (критики 16–17.10)

Итог работы — docs/RELEASE_1.0.13.md. Интерфейс не перестраивали (решение владельца 17.10), чинили логику и косяки.

- **Отзыв страницы** (`revokePage`, B2-2/A2-4, B3-3, Н-1, З-1, З-5-1, П-6-1). Перед окном перечитываются запись
  страницы (`pageNow`: свежие `visits`, `profiles`, `subscription`) и подписка пользователя (`userSubNow` — `sub-list`).
  Галочка «Перевыпустить и подписку» есть, когда подписка есть сейчас; включена, если её завела эта страница
  (`subscription: created`). Страницу открывали — текст называет число открытий, и есть галочка «Перевыпустить и ключи»
  (выключена). Она называет профили страницы с ключом на сервере, а при включённой подписке — и профили её линий: ядро
  перевыпустит и их (`ga_pages_revoke_key_profiles`). Если профили есть только у подписки, галочка ключей доступна лишь
  вместе с галочкой подписки. `sub-list` не ответил, а подписка есть по снимку — профили подписки названы условно. Вызов —
  `page-revoke {id, sub, keys?}`. Итог-тост по полям ответа: `sub_rotated` / `sub_present: false` (подписки уже не было) /
  провал; `keys_reissued`, `keys_failed`. Ответ без `keys_reissued` (старое ядро) считается «ключи не перевыпущены». После
  отзыва с ключами кэш ключей пользователя во вкладке (`state.keyCache`) сбрасывается целиком (B4-2).
- **Перевыпуск ключа** (`reissuePeer`, `reissueFacts`, `reissueFactsText`, B3-4, Н-4). Окно и итог говорят по фактам:
  есть страницы с этим профилем (`/api/pages?name=`), есть подписка, которая отдаёт ключ этого профиля (`sub-list`),
  иначе — выдать вручную; не ответило — условно. Срок подписки — `subWithin`: «до 12 ч» или «до 12 ч, у FlClash — до 24 ч»
  (`update_interval` и `update_slow` из `sub-list`, A4-3/B4-4). Окно «Исправить S4» берёт тот же срок из `sub-list` без
  имени (`subWithinNow`, R8-2); не ответило — «до 12 ч».
- **Временная ссылка выключенного** (A3-5, A4-5, R7-3, R8-3). Ядро отказывает `user_disabled` — окно «Пользователь
  выключен» (`udShareDisabledD`) с «Отмена» / «Включить». «Включить» включает пользователя и продолжает начатое (ссылку
  или страницу). Свежая запись не пришла — тост «Пользователь включён — создайте … ещё раз»; включение не прошло — «Не
  удалось включить пользователя». Выбор профиля в окне ссылки — вкладками карточки (A3-3).
- **Имя файла** (П-1, П-2). `GET /api/users/<name>/conf` отдаёт `Content-Disposition` с `file_name` из ответа ядра
  (`anna-2_0.conf`); поля нет (старое ядро) — прежнее имя.
- **Живые блоки карточки** (A4-1/B4-1, R7-2; `cardLiveTick`). Пока открыта карточка, «Страницы» и «Подписка»
  перечитываются на месте раз в 30 с (`CARD_LIVE_MS`) и при возврате на вкладку, но не чаще раза в 10 с
  (`CARD_LIVE_GAP_MS`), и под открытым окном тоже. Пропускается блок с фокусом, идущее действие, окно входа и скрытая
  вкладка. Страница завела подписку — блок «Подписка» перечитывается сразу (A3-1). Блоки ищутся по `data-user` = имени
  (`userPagesBox`, R-3, Q-1).
- **Подписка в карточке** (A3-2, П-6-2). После перевыпуска — «перевыпущена <когда>» и строка «Прежний адрес забирали:
  N · когда» (`prev_fetches`, `prev_last_fetch`); смысл — в подсказке.
- **Поиск ⌘K / Ctrl+K** (A2-10, B4-5/A4-5, R7-1, R8-1, R9-2, R9-3; `cmdItems`, `cmdRank`, `cmdStem`, `openCmd`).
  - Пункты: разделы, действия, вкладки «Пользователей», пользователи (и по адресу внутри VPN), порт, MTU и подсеть
    профилей, проверка обновлений, подписка; «Страница подключения — в карточке» подсказкой.
  - Сила совпадения: 0 — каждое слово запроса начинает слово подписи (ближайшее по длине первым); 1 — по основе без
    окончания (от 4 букв) или по началу слова группы и ключей; 2 — подстрокой.
  - У строк пользователей группа «Пользователи» сравнивается только подстрокой. Внутри уровня разделы и действия выше
    людей. Лимит строк — только людям. Группы — сплошными блоками, заголовок один раз. При равной силе выше подпись,
    равная запросу («users» — раздел «Users»).
- **Формы** (B2-6/A2-1, B3-1, B3-2, B3-7). Числа в «Лимит, ГБ», «Срок, дней» и «Сколько» не склеиваются. «Добавить»
  проверяет пределы лимита и срока до моста, ошибка — только под полем.
- **Обзор** (A-21, R-5, LH-6, R-6, A-16). Секретный адрес панели скрыт точками, «Показать» — на 5 минут (`ovUrlLive`:
  таймер прячет адрес только в узлах текущего Обзора). IPv4 не рвётся посреди числа. Домен — только имя с буквенной
  последней частью; на телефоне и сенсорном экране он переносится на свою строку. Итог проверки обновлений — на языке
  панели по `hint_key` (белый список `upstream_summary_payload`).
- **Мини-апп и QR** (MINIAPP-TABS-01, MINIAPP-QR-01). Вкладки профилей в карточке — в две строки (версия; ниже порт и
  «из приложения»), ряд переносится без обрезки. Части QR `vpn://` сменяются сами по кругу раз в 1,5 с (`QR_CYCLE_MS`),
  стрелки листают по кругу, закрытая карточка смену останавливает; так же на странице подключения (`lib/pages_server.py`).
- **Проверки**: `tests/test_critics_fix_panel.py`, `tests/test_critics_fix2_panel.py`, `tests/test_critics_fix3_panel.py`,
  `tests/test_critics_fix4_panel.py`, `tests/test_critics_fix5_panel.py`, `tests/test_critics_fix7_panel.py`,
  `tests/test_critics_fix2_static.py`, `tests/test_panel_dom.py`, `tests/test_admin_http.py`.

## 7. Проверки

```bash
python3 -m py_compile admin-web/server.py
node --check admin-web/static/app.js                  # при наличии node
python3 -m unittest tests/test_admin_features.py tests/test_admin_http.py
/opt/homebrew/bin/bash -n admin-web/install_admin.sh
shellcheck -S warning -x admin-web/install_admin.sh tests/fixtures/fake_action_admin.sh
```

`test_admin_features.py` — без сети: разбор ответа моста, argv, валидация по `actions.json`,
сессии/TTL с подменой часов, корзины лимитов кода, **пароль** (на диске только хеш и 0600, соль
новая на каждую запись, верный и неверный вход, битый файл = пароля нет, пять попыток и скользящее
окно блокировки, одинаковое время ответа, CLI `--set-password`/`--password-status`, страница входа
в четырёх сочетаниях «бот × пароль»), подпись WebApp на известном векторе, доверие
петле, маскирование, пометки пользователей, мост на заглушке (кэш, аудит, `transport`/`timeout`),
карта кодов спринта 4 (`unreachable` → 503, `already_current`/`no_previous`/`already_managed`/
`not_adoptable`/`unsupported_container` → 409, `tag_not_found`/`container_not_found` → 404).
`test_upstream_and_import_routes` в `test_admin_http.py` — `GET /api/upstream` и `/api/import` на
заглушке, `upstream-update`/`rollback`/`core-update` только с `confirm`, `import-adopt` и
`import-reconcile --apply=on` доходят до моста с нужными аргументами.
`test_admin_http.py` — панель подпроцессом на свободном порту петли: туннель без кода; каждый из
четырёх прокси-заголовков → 401; `?token=` → cookie → данные; код 2FA через `GOAMNEZIA_TFA_SINK`
→ сессия → продление → истечение; **вход по паролю** (`PasswordLoginTests`: верный и неверный,
блокировка адреса после пяти попыток и соседний адрес свободен, одинаковое время ответа, смена
пароля с текущим и без него на пустом сервере, пароль не появляется ни в `/api/settings`, ни в
аудите, вход кодом остаётся рабочим, предохранитель CSRF на обоих маршрутах — каждый тест со
своего `X-Real-IP` из документационной сети, чтобы корзины не пересекались); мини-апп по подписи; `Host` → 403; статика без внешних
скриптов; каждый API-маршрут по таблице выше; мутации с валидацией, `confirm` и предохранителем;
доступы агентов: `/api/agents` без секрета и с `transport`, `agent-show`/`rotate`/`del`/`add`
(секрет только в этих ответах, `confirm` для разрушительных, `agent_not_found` → 404),
`mcp-http-on --port` с `closed_local` → 409, аудит панели без адреса-ключа.

Новое (пробелы и разбор критика):

* `ShareRoutesTests` — `GET /api/share` без `url`/`path` (заглушка нарочно кладёт их в ответ
  `share-list`), `share-create` отдаёт адрес и **не** пишет его в аудит, чужой формат отбивается
  до моста, `share-revoke` без `confirm` → 400, `share-revoke-all` с `confirm` = имя действия.
* `PagesRoutesTests` и `MirrorRoutesTests` — `GET /api/pages` без `url`/`path` (заглушка нарочно
  кладёт их), фильтр по имени и отказ на мусорное имя; `page-create` отдаёт адрес с `qr_png_b64`
  и не пишет адрес в аудит, негодный срок не запускает мост; `page-url` только POST и только с
  `id` по форме, неизвестный → 404; `page-revoke` по каталогу ядра, `page-revoke-all` без
  `confirm` → 400; `GET /api/mirror` с `bytes` у идущей загрузки; `GET /api/clients` называет
  только зеркалируемые платформы; `mirror-fetch` с чужой платформой → 400, `not_mirrorable` →
  409, `mirror-remove` без `confirm` → 400. В `test_admin_features.py` — `PagesPayloadTests`
  (белые списки, QR только к ответам с адресом, `pages_need_domain` → 409, мусор до моста) и
  `FrontendPagesTests` (`page-url` зовётся ровно в одном месте — по кнопке; срок проверяется
  до моста; отзыв и удаление копии спрашивают; карточка зеркала в разметке и опрос по таймеру;
  строки в обоих словарях).
* `PreflightAndLogStatusTests` — `GET /api/preflight` (режимы, владельцы портов, продукт, совет)
  и `GET /api/logs/status` (занято, бюджет, проценты), маршрут логов не перехвачен статусом.
* `BusyLockTests` — мост задержан файлом `FAKE_ADMIN_SLEEP_FILE`, вторая мутация получает 409
  `lock_timeout` с `busy_action` **быстрее пяти секунд**, а чтение в это же время работает.
  В `test_admin_features.py` то же на уровне `run_action` (`ActionLockTests`), плюс проверка,
  что замок отпускается после обычного вызова.
* `SharePreflightLogsPayloadTests` — сборка данных для трёх новых блоков и белый список полей раздачи.
* `FrontendI18nTests` — RU и EN в словарях `app.js` совпадают ключ в ключ, каждый `data-i18n*`
  из разметки есть в обоих, новые блоки имеют свои строки.
* `FrontendImportedKeyTests` — импортированный пир в карточке: строка правды стоит **выше** ряда
  действий, у пира без приватного ключа нет ни одной из шести кнопок выдачи (включая временную
  ссылку), у обычного они все на месте, перевыпуск спрашивает до моста и идёт снять-потом-выдать,
  а падение выдачи держит окно и называет кнопку возврата. Плюс `UserMarksTests.
  test_decorate_user_marks_the_peer_without_a_private_key` и `ReadApiTests.
  test_peers_carry_the_private_key_flag` — признак доезжает до браузера обоими маршрутами.
* «Привязать старый ключ» (0.4.5): `AttachKeyServerTests` — имя параметра из stdin берётся из
  каталога ядра (`"stdin": true`), а без пометки — из `STDIN_PARAM_FALLBACK`; ключ не становится
  аргументом ни при каком каталоге; `run_action` отдаёт его заглушке только в stdin
  (`FAKE_ADMIN_STDIN_FILE`), в argv и аудите — имя и профиль; stdin у другого действия — отказ до
  моста; настоящий `route_post_api` вынимает `config` из тела; отказы ядра словами и кодами
  (`config_unreadable`, `no_config` → 400; `key_mismatch`, `server_mismatch`, `already_has_key` → 409),
  отказы формы (больше 64 КБ, не строка, нулевой байт) — без запуска bash; `foreign_container` и
  `no_private_key` → 409; вывод упавшего моста с ключом мимо журнала. `FrontendAttachKeyTests` —
  строки на двух языках и каноничные слова договора, кнопка только у ключа из приложения и перед
  перевыпуском, окно читает файл в браузере и зовёт одно действие, ключ нигде не оседает,
  предупреждение в переспросе перевыпуска, `keyError` у всех кнопок выдачи, плашки страницы
  подключения, поле ключа без своего размера шрифта (16px на телефоне).
* «Ключ на другой адрес сервера»: `KeyEndpointServerTests` (форма `endpoint` только у `user-key`/`share-create`, мусор — до моста, определение `key_endpoint` из каталога ядра главнее запасного — и при старом каталоге без параметра и типа, коды `invalid_ip`/`address_not_here` → 400, `addresses_unknown`/`address_gone` → 409, в аудите адрес есть, ключа нет), `KeyChoicesPayloadTests` (`key_host`, `key_host_gone`, `key_choices` в карточке, `edge-status` через кэш — один вызов на два открытия, белый список вариантов, поля внутри `user{}` и сломанный `edge-status`), `FrontendKeyAddressTests` (строки в обоих словарях, выбор над рядом кнопок и только с ключом и выбором, «как обычно» первым, домен и слова сторожа, ⚠ у «замолчавшего», смена адреса убирает ключ, все кнопки и кэш — с адресом, раздача передаёт и называет адрес). В `test_admin_http.py` — `KeyEndpointRoutesTests`: `?endpoint=` доходит до заглушки как `--endpoint=`, пустой не передаётся (argv байт в байт прежний), мусор → 400 без моста, отказы ядра → 4xx с его словами и у `/conf` JSON, а не файл, имя файла с хостом, `share-create` с адресом, `endpoint` в списке раздач, варианты в карточке, аудит без ключа. Заглушка `fake_action_admin.sh` отвечает `edge-status` с тремя адресами и `key_choices` и проверяет `--endpoint` как мост.
* `FrontendUiTests` — подложка меню и закрытие по Escape, 16px/44px в мобильных стилях и в окне
  входа, отдельный ряд необратимого, плашка действия в шапке, `--ink-*` на плашках и
  `prefers-color-scheme` в стилях, ни одного `setAttribute("style"` в `app.js`.
* `LoginPageTests.test_login_page_is_theme_aware` и `test_panel_strings_are_paired` — серверная
  страница входа по теме и словарь ответов панели (`PANEL_STRINGS`) на двух языках.
* Волна 1 (26.09.2026). `OverviewEdgePayloadTests` — A-записи домена из ключей и строки адресов доходят до
  Обзора **белым списком** (`edge_dns_payload`, `edge_ips_payload`; вердикт или состояние не того типа —
  `null`, а не 500), блок `edge` есть и при одном адресе (строка адреса и беда с A-записями), нет — только
  когда сторож не ответил; `tunnel_host` — принимающий адрес вместо только исходящего основного (PUI-25, IPv6
  сравнивается разобранным); `/api/settings` несёт `tunnel_host` и `bot_state` (PUI-11). Кривой ввод — 400/401,
  а не 500 и не обрыв соединения (продолжение UI-5): `GuardTests.test_crooked_strings_are_4xx_not_500`
  (подтверждение не-ASCII, лишний параметр с одиноким суррогатом — ответ с экраном `\ud800`, код входа и
  подпись мини-аппа с суррогатом или «é» — в теле и в заголовке `X-Telegram-Init-Data` на любом маршруте),
  на уровне функций — `WebAppSignatureTests`, `TfaBucketTests`, `RunActionTests`, `PasswordStoreTests`
  (плюс: подсмотр корзины пароля не заводит пустых записей — проверка SEC-P1). Сравнение секретов и
  подтверждений — байтами (`text_bytes`), журнал предохранителей — через `log_text`.

## 8. Стенд и мини-апп — что проверить вживую

1. `install_admin.sh` на чистом сервере: юнит active, `ss -ltn` показывает только `127.0.0.1:1985`,
   `token` 0600 в каталоге 0700; повторный запуск не меняет токен.
2. Туннель `ssh -L 1985:127.0.0.1:1985 root@…` → `http://127.0.0.1:1985/`: панель без кода, в
   подвале «вход по туннелю», кнопки «Выйти» нет; реальные `status`/`user-list` идут через мост
   (`journalctl -u goamnezia-admin`, аудит `/var/log/goamnezia-audit.log`).
3. Пользователи: QR `.conf` сканируется клиентом AmneziaWG; «Скачать .conf» отдаёт файл;
   «Скопировать vpn://» вставляется в приложение Amnezia; серия QR «n из m» читается приложением
   по порядку; удаление только после ввода имени.
4. Протоколы: смена порта и «занять UDP/443» (второй профиль получает `port_taken`), правка
   клиентского параметра без перевыпуска, серверного — с предупреждением и `reissue_required=true`.
5. Pro: `miniapp-on` → адрес `https://<домен>/app-…/` открывает **страницу входа** (не пустую
   панель), «Прислать код» → код в Telegram у всех `ALLOWED_IDS`, вход, через 15 минут баннер
   продления, через минуту — «сессия закончена». Прямой `curl https://<домен>/app-…/api/users`
   без cookie → 401. `?token=` с домена тоже заводит сессию.
6. Мини-апп: кнопка «Панель» в боте открывает панель **без кода** (подпись из `#tgWebAppData`,
   cookie `SameSite=None`, билет `?s=`); внутри Telegram Web (iframe) панель не рвётся CSP;
   аккаунт не из `ALLOWED_IDS` получает «этот Telegram-аккаунт не админ бота» и форму кода;
   после `miniapp-rotate` бот показывает новый адрес, старый отдаёт сайт-маскировку.
7. Соседство: `X-Forwarded-*` от nginx не пускают без кода (проверить `curl -H 'X-Forwarded-For: 203.0.113.4'
   http://127.0.0.1:1985/api/users` с сервера → 401).
8. Логин и пароль: после установки из меню в терминале напечатаны логин и пароль (один раз; мастер без
   домена печатает их, когда панель выходит на домен); в
   `password.json` пароля нет, права 0600, каталог 0700; вход с домена паролем работает; пятая
   подряд неверная попытка запирает адрес на 15 минут; `install_admin.sh --reset-password`
   печатает новый пароль, старый перестаёт подходить сразу, служба не перезапускалась.
9. Установка без бота: панель поднялась, страница входа показывает **только** логин и пароль,
   про код на ней нет ни слова; после установки бота на той же машине появляется и код.

## 9. Что нужно от соседей (готовые куски)

Файлы `install.sh`, `lib/*`, `lib/actions.json` и словари правят другие агенты — здесь то, что
им нужно подключить. Панель со своей стороны готова: пароль заводится при установке, вход и
смена работают, `install_admin.sh --reset-password` печатает новый.

### 1. `install.sh` — панель в мастере во всех трёх режимах, без вопроса — **сделано**

Решение владельца от 10.09.2026: «во всех вариантах не нужно спрашивать поднимание веб-панели, мы
всегда её поднимаем автоматически». Сделано в NC-13 (601a2d0) — как устроено, §4 п. 3; ключа
`wz_panel_lite_note` больше нет. Соседние места:

- `auto_update_component "$SCRIPT_DIR/admin-web" "$ADMIN_DIR" goamnezia-admin.service` обязан
  пропускать **`password.json`** так же, как пропускает `token`: иначе очередное обновление кода
  сотрёт выданный пароль.
- `_panel_show_access` — **сделано**: `pn_login_hint` — «Через туннель вход без пароля; с домена — по
  логину и паролю, выданным при установке, либо одноразовым кодом из Telegram-бота».
- Пункт «Пароль панели» на экране панели — **сделано**: «Пароль панели: перевыпустить»
  (`_panel_password_reset`, `install_admin.sh --reset-password`, пароль — только в терминал).

### 2. Бот — кнопка «Пароль панели»

В экране «Панель» (`goamnezia-bot/bot.py`) нужна кнопка, которая выдаёт новый пароль в личку
администратору **одним сообщением с пометкой удалить его**, и ничего не показывает в групповых
чатах. Пароль приходит в ответе действия (см. п. 3) и в логи бота не пишется — как `user-key`.
Тексты — в `goamnezia-bot/lang/{ru,en}.json`:

```json
"panel_password_btn":  "Пароль панели",
"panel_password_done": "Новый пароль панели: <code>%s</code>\nЛогин: <code>%s</code>\nСтарый пароль больше не работает. Сообщение лучше удалить после того, как сохраните.",
"panel_password_fail": "Не удалось сменить пароль панели: %s"
```

### 3. `lib/actions.json` + диспетчер — действие `panel-password`

Чтобы то же самое умели бот и SSH-меню, нужно одно действие. Панель его **не зовёт** (она правит
свой файл сама, минуя ядро и общий журнал: пароль — ключ от самой панели, а не от ядра), но боту
без моста никак. Запись в `actions.json`:

```json
"panel-password": {
  "description": "Перевыпустить пароль входа в веб-панель (логин сохраняется)",
  "readonly": false,
  "destructive": false,
  "secret": true,
  "mcp": null,
  "params": {
    "login": {"type": "panel_login", "required": false,
              "description": "Логин администратора панели (по умолчанию прежний, иначе admin)"}
  }
}
```

и тип параметра в `param_types`:

```json
"panel_login": {"type": "string", "pattern": "^[A-Za-z0-9._-]{1,32}$", "description": "логин панели"}
```

`"secret": true` — как у `miniapp-on`: ответ содержит выданный пароль, и его нельзя ни
логировать, ни класть в аудит с параметрами. `"mcp": null` — агентам это действие не отдаём:
пароль администратора не то, что должен уметь брать ИИ-агент.

Реализация в диспетчере — ровно то же, что делает установщик (одна строка, KDF считает панель):

```bash
ga_action_panel_password() {
    local login="${1:-}" pass=""
    local admin_dir="${GOAMNEZIA_ADMIN_DIR:-/opt/goamnezia-admin}"
    [ -f "$admin_dir/server.py" ] || { ga_json_error panel_missing "Панель не установлена"; return 1; }
    # Логин сохраняем прежний, если не задан: перевыпуск пароля не повод менять имя.
    [ -n "$login" ] || login=$(GOAMNEZIA_ADMIN_DIR="$admin_dir" python3 "$admin_dir/server.py" \
        --password-status 2>/dev/null | sed -n 's/.*"login": "\([A-Za-z0-9._-]\{1,32\}\)".*/\1/p')
    [ -n "$login" ] || login="admin"
    pass=$(python3 -c 'import secrets,string; print("".join(secrets.choice(string.ascii_letters+string.digits) for _ in range(20)))')
    printf '%s\n' "$pass" | GOAMNEZIA_ADMIN_DIR="$admin_dir" \
        python3 "$admin_dir/server.py" --set-password --login="$login" >/dev/null || {
            ga_json_error password_write_failed "Не удалось записать пароль"; return 1; }
    # Пароль — в ОТВЕТЕ, и больше нигде: ни в лог, ни в аудит.
    printf '{"status":"success","message":"Пароль панели перевыпущен","login":"%s","password":"%s"}\n' "$login" "$pass"
}
```

Замок ядра здесь не нужен: файл лежит в каталоге панели, `config.json` и `users.json` не
затрагиваются. Служба панели не перезапускается — `password.json` читается на каждую проверку.
