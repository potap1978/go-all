# Карта проекта goVLESS pro 1.4.1

Дата: 2026-09-22. Ветка `main` = `test` = ff24306. Установка и обновление — через goInstaller (`potap1978/go-all`, `goinstall --install govless pro|lite main`).

## 1. Что это

Установщик и панель управления VPN-сервером на связке **3X-UI + Xray (VLESS)** для одного VPS под root (Ubuntu 22.04/24.04, Debian 12+). Три режима установки:

| Режим | Что на 443 | Сайт | Сертификат | Домен |
|---|---|---|---|---|
| **Pro** (`2`) | xray VLESS+TLS (Vision), fallback → nginx:80 | сайт-маскировка из каталога (33 467 шаблонов), свой Git-репозиторий или заглушка | Let's Encrypt (certbot, webroot) на домен | нужен |
| **Pro-авто** (`1`, по умолчанию) | то же | случайный шаблон, 5 ключей, TCP, safari, без вопросов | LE | нужен |
| **Lite** (`3`) | xray VLESS+Reality под чужой домен (список RU/международных) | заглушка nginx:80 (не обязательна) | 6-дневный LE на IP для панели (acme.sh) | не нужен |

Поверхности управления (один бэкенд):
1. **SSH-меню** `govless` (bash, RU/EN): VPN → Пользователи → Управление → Telegram-бот → О программе.
2. **CLI-флаги** `govless --site-template-* --json`, `--egress-awg-up/-down`, `--egress-probe` (для демона и systemd).
3. **Telegram-бот** (aiogram v3) + **демон govlessctl** (JSON-RPC по UNIX-сокету, привилегированный).
4. **Mini App** (React, `/app/` на домене в Pro; в Lite — через cloudflared quick tunnel на :8443).
5. **goInstaller** — предпроверка портов, выбор ветки, запуск `bootstrap.sh`, постпроверка ветки/версии.

## 2. Репозиторий

| Путь | Назначение | Размер |
|---|---|---|
| `bootstrap.sh` | загрузчик: клон/обновление приватного репозитория в `/opt/govless-installer` по `GH_TOKEN`, переезд между ветками с проверкой HEAD, `GOVLESS_BOOTSTRAP_ONLY=1` | 203 стр. |
| `govless.sh` | точка входа: CLI-режимы, язык, баннер, преflight, установка (`select_and_install` → `install_pro`/`install_lite`), пост-установка, главное меню и подменю, бот-мастер, `maybe_sync_version`/`maybe_offer_fingerprint_change` | 1533 |
| `lib/common.sh` | цвета/логи/спиннер, apt с ожиданием лока, IP/гео, `config_get/set` (JSON), `confirm`/`typed_confirm`, дисклеймер, версия `GOVLESS_VERSION` | 980 |
| `lib/i18n.sh` + `lib/lang/{ru,en}.sh` | `t`/`tf`, выбор и сохранение языка (`/opt/govless/.language`) | |
| `lib/xui.sh` | установка/обновление 3X-UI (ветки new 3.x / legacy 2.x), извлечение кредов, TLS панели, sub-сервер, бэкап/восстановление, repair, **удаление трёх уровней** | 1642 |
| `lib/xui_api.sh` | REST 3X-UI: логин, inbound Reality/TLS (fallback :80, ALPN по транспорту), клиенты, ссылки vless://, подписки, QR, онлайн-проверка, `tls/reality_inbound_serving` | 1178 |
| `lib/website.sh` | nginx (Pro-vhost на :80 с security-заголовками, Lite-заглушка, temp для ACME), certbot + deploy-hook + таймер продления, развёртывание шаблона (`strip_template_service_files`, symlink-защита) | 572 |
| `lib/templates_catalog.sh` | каталог `templates_catalog.json` (14 МБ), выбор/поиск/случайный, клон шаблона (sparse), свой Git, бэкапы сайта `/opt/govless/site-backups`, `submenu_site`, `site_template_cli` (`--json`) | 1406 |
| `lib/reality_domains.sh` | списки доменов для Reality по гео + проверка TLS1.3/H2 | 323 |
| `lib/mode_switch.sh` | Lite ↔ Pro: снимок клиентов, пересоздание inbound, сертификат, ссылки, предложение шаблона после Lite→Pro | 466 |
| `lib/egress.sh` | «Прокси для API Telegram»: пул vless:// + awg:// (парсер на Python внутри), отдельный xray (SOCKS5 127.0.0.1:10808, API 10851, балансировщик leastPing), ноги AmneziaWG `kawgN` (amneziawg-go из `bin/`, fwmark 256+N → таблица 256+N), пробник потерь, TUI-панель | 1048 |
| `lib/migrate.sh` | схема-миграции config.json | 190 |
| `bin/` | `amneziawg-go-linux-amd64`, `awg-linux-amd64` (вендорённое ядро AWG) | 3,5 МБ |
| `phase-a/govlessctl/` | демон (`daemon.py`, aiohttp на UNIX-сокете `/run/govlessctl.sock` 0660 root:govless), `methods.py` (37 RPC-методов), `auth.py` (HMAC initData Telegram + SO_PEERCRED), `state_db.py` (SQLite `/var/lib/govless/state.db`: audit, capabilities, admins, admin_claimed, pending_confirms), `xui_client.py`, `catalog_tool.py`, `cli.py` (`govlessctl <method> --arg k=v`) | 1686+ |
| `phase-a/bot/` | aiogram-бот: `bot.py`, `common.py` (роли/админы), `rpc.py`, handlers: `start`, `menu` (разделы VPN/Manage/About), `clients` (список/добавить/лимиты/QR/подписка), `confirm` (типовые подтверждения «RESET name» и т.п.), `site` (`/site`, `/siteurl`, каталог, бэкапы), `admin` (`/admin`, `/invite`, `/audit`), `webapp` (кнопка Mini App, уведомления `/run/govless/notify.queue`, tunnel watcher) | ~2800 |
| `phase-a/webapp/` | `dist/app.jsx` (сборка `tools/build-webapp.js` через @babel/standalone), маршруты `#/clients #/inbounds #/site #/panel #/backup #/repair #/admins #/notifications #/settings #/system #/about #/disclaimer`; `server.py` — релей для Lite (`/api/rpc`, `/api/healthz`) | 2295 |
| `phase-a/systemd/` | юниты `govlessctl`, `govless-bot`, `govless-audit-prune.timer`, `webapp-frontend` (Lite), `cloudflared-quick`/`cloudflared-url.path` (Lite), `tunnel-health.timer`; `nginx/govless-webapp.conf` (snippet `/app/`, `/api/rpc` → сокет, CSP); `install/install_phase_a.sh` (venv, пользователь govless, права, ожидание apt-лока, перезапуск работающих служб), `install_govless_user.sh`, `install_cloudflared.sh` | |
| `tools/` | `awg_core_selftest.sh`, `egress_selftest.sh` (парсер), `bootstrap_branch_switch_test.sh`, `live_branch_switch_check.sh`, `check-str-parity.py`, `build-webapp.js`, `deploy-manifest.sh` | |
| `test_regressions.sh` | 102 grep/awk-пина + оффлайн-самотесты; `fail()` останавливает прогон на первом FAIL | 99 КБ |
| `test_bot_regressions.py`, `test_*.sh`, `debug_inbound.py`, `test_api_debug.py` | точечные проверки бота/API/установок (нужны venv или сервер) | |
| `docs/` | `DOCUMENTATION.md`, `qa/` (карты тестов, эта карта), `codex/`, `superpowers/`; `ai-bridge/claude-codex/` — 100+ хендоффов между агентами | |
| `GOVLESS_DOCS.md`, `README.md`, `AUDIT_GUIDE.md`, `CLAUDE.md`, `PROMPT_FOR_NEW_CHAT.md` | документация и инструкции ИИ | |
| `install.sh`, `deploy_pro.sh`, `self_signed_cert.sh` | старые точки входа/утилиты (goInstaller использует только `bootstrap.sh`) | |

## 3. Раскладка на сервере

| Что | Где |
|---|---|
| Код (клон ветки) | `/opt/govless-installer` (git, все ветки по refspec), ярлык `/usr/local/bin/govless` |
| Конфиг и данные | `/opt/govless/config.json` (mode, domain, version, transport, fingerprint, sub_port/path, site_template, users_count…), `/opt/govless/templates_catalog.json`, `/opt/govless/site-backups/*.tgz`, `/opt/govless/.language`, `/opt/govless/.disclaimer-accepted` |
| Phase-A | `/opt/govless/{govlessctl,bot,webapp,venv,bin}`, `/etc/govless/bot.env` (BOT_TOKEN, ADMIN_IDS, ADMIN_CLAIM_TOKEN, BOT_PROXY, WEBAPP_AUTH_TTL; root:govless 0640), `/var/lib/govless/state.db`, `/run/govlessctl.sock`, `/run/govless/{notify.queue,tunnel.url}` |
| 3X-UI | `/usr/local/x-ui/` (бинарь + xray), `/etc/x-ui/x-ui.db`, панель на случайном порту + случайном пути; креды в `/root/.govless_credentials` |
| Сайт | `/var/www/html` (маркеры `.govless-owned`, `.govless_template_id`), vhost `/etc/nginx/sites-available/govless` → `sites-enabled/govless` (+ include snippet Mini App), ACME-webroot `/var/www/certbot` |
| Сертификат | `/etc/letsencrypt/live/<домен>/`, deploy-hook `renewal-hooks/deploy/govless-restart.sh`, systemd-таймер продления |
| Egress | `/etc/govless/egress/{upstream.txt,config.json,xray,egress_parse.py,awg/,loss.log}`, юниты `govless-egress`, `govless-egress-probe.timer`, `govless-awg@kawgN`, `govless-awg-reconcile.timer` |
| Бэкапы | `/root/govless-backups/govless-<UTC>.tgz` (config, x-ui.db, state.db, creds) |
| Порты | 443/tcp xray (оба режима), 80/tcp nginx, панель и sub-сервер — случайные 20000–65000, 8443 loopback Mini App (Lite), 10808/10851 loopback egress, 18181 loopback cloudflared metrics |
| Пользователи/права | всё под root; `govless` — пользователь демона/бота (сокет 0660, `/opt/govless/{govlessctl,bot,webapp}` root:govless 0644/0755) |

## 4. Основные потоки

1. **Установка.** `goinstall` (проверка ОС/портов/ключа) → `bootstrap.sh` (клон по токену) → `govless.sh`: язык → баннер → ярлык → `require_fresh_os` → диск ≥ 500 МБ → `preflight_deps` (sqlite3 expect qrencode…) → конфликт 443 → дисклеймер → режим → (`install_pro`: домен → DNS-проверка с ожиданием → email → шаблон → ключи → версия 3X-UI → транспорт → отпечаток → подтверждение → зависимости → **kill_port 443** для не-xray → nginx+certbot+шаблон+сертификат → 3X-UI → креды → TLS панели на домен → sub-сервер → API: клиенты + TLS-inbound (fallback :80) → ссылки → config) → `post_install_flow` (QR, проверка подключения, показать всех, предложить бота).
2. **Обновление.** Тот же `goinstall`/`bootstrap.sh` на установленном: fetch + `checkout -f -B` + проверка HEAD → `govless.sh` видит установку → `maybe_run_migrations` (schema) → `maybe_sync_version` (версия в config ≠ коду: бэкап → `bot_install_phasea` если бот стоит → регенерация Pro-vhost) → `maybe_offer_fingerprint_change` → меню.
3. **Смена режима** `1 → 4`: снимок клиентов → удаление inbound → (Pro: домен/email/сертификат или переиспользование, заглушка) / (Lite: Reality-ключи, домен маскировки) → новый inbound → ссылки → предложение шаблона после Lite→Pro.
4. **Смена шаблона** `1 → 5` / бот `/site` / Mini App `#/site` → демон `site.template_*` → фоновый job `govless --site-template-apply … --json` → `_site_lock`, клон, `strip_template_service_files`, бэкап старого сайта, деплой, `site_http_code` (https://домен, затем loopback).
5. **Прокси для API Telegram** `4 → 6` (или `4 → 2` без бота): вставка vless://, подписки, AmneziaWG `.conf`/`vpn://` → `egress_parse.py` → пул `upstream.txt` → `egress_rebuild` (отдельный xray из релизов Xray, ядро AWG из `bin/`, `kawgN` + fwmark-маршруты) → `BOT_PROXY=socks5://127.0.0.1:10808` → перезапуск бота → `egress_test` (api.telegram.org). Панель: пинг/трафик/потери, F2/F3/F6/F7.
6. **Бот.** Токен → `install_phase_a.sh` (venv, юниты) → мастер: авто-определение админа по getUpdates или ID вручную → `admin.claim` (одноразовый токен) → меню разделов; опасные действия через типовые подтверждения; уведомления через очередь; Mini App через кнопку (initData HMAC, TTL).
7. **Удаление** `3 → 6`: только сайт (с защитой сертификата, занятого inbound) / только панель / всё (службы, egress, x-ui, сайт+серт, юниты, `/opt/govless`, `/var/lib/govless`, `/etc/govless`, пользователь, ярлыки, journal, tmp, acme.sh если наш).

## 5. Внешние зависимости

GitHub (приватный `potap1978/go-all` по токену; релизы Xray-core и 3X-UI; публичные репозитории шаблонов), Let's Encrypt (лимит 5 сертификатов/неделю на домен), api.telegram.org (бот, `egress_test`), apt (unattended-upgrades держит лок до 15 мин), cloudflared (Lite Mini App), DNS домена → IP сервера.

## 6. Как проверять

- Без сервера: `bash test_regressions.sh` (102 PASS), `python3 tools/check-str-parity.py`, `bash tools/awg_core_selftest.sh`, `bash tools/egress_selftest.sh`.
- На сервере: `goinstall --install govless pro main` → сценарии из `docs/qa/TEST-MAP-1.4.0-site-templates.md`, отчёты `qa-1.4.0/098–101` в папке проекта; `govlessctl system.status --format json`, `govless --site-template-status --json`, `curl --socks5-hostname 127.0.0.1:10808 https://api.telegram.org/`.
