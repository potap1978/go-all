# 097 — Architect: смена шаблона сайта-маскировки после установки (SSH + бот + Mini App)

**Роль:** Architect (Claude) → Reviewer (Codex)
**Тип:** proposal + implementation report (ветка `test`, версия 1.3.6 → 1.4.0)
**Триггер:** жалоба пользователя — «в Pro-режиме нельзя сменить шаблон сайта».

## 1. Диагноз (аудит `main` @ 95d8c5d)

| # | Факт | Ссылка |
|---|------|--------|
| 1 | Выбор шаблона существует только внутри `install_pro()` — после установки точки входа нет | `govless.sh` шаг «4. Template selection» |
| 2 | Пункт меню `1 → 4` «Сменить режим / шаблон» (`proxy_change_mode`) вызывает `switch_mode_interactive`, который меняет только Lite↔Pro; при Lite→Pro ставится `deploy_stub_site` без выбора шаблона | `govless.sh submenu_proxy`, `lib/mode_switch.sh:265` |
| 3 | Ключи `website_title` («🌐 Управление сайтом»), `website_only_pro`, `manage_site_ssl` есть в i18n с первого импорта v3.0.6, но нигде не используются — подменю сайта не было реализовано | `lib/lang/*.sh`, `git log -S website_only_pro` |
| 4 | Бот показывает «🔁 Mode / template switch (SSH)» — отправляет в SSH, где смены шаблона тоже нет | `phase-a/bot/handlers/menu.py:230` |
| 5 | Единственный обходной путь — «1 → 1 Установить/обновить → Pro»: повторный ввод домена, DNS-проверка, email, **создание новых ключей** (`generate_clients`) — тяжёлый и пугающий путь | `govless.sh install_pro` |
| 6 | **P2 баг:** `deploy_template_to_nginx` делает `rm -rf $WEBSITE_ROOT` и не восстанавливает маркер `.govless-owned` → после любого шаблона `remove_site_only` отказывается чистить web root («no ownership marker») | `lib/website.sh` (старая версия) |
| 7 | **P2:** бэкап старого сайта уходил в `/tmp/site_backup_<дата>.tgz` — предсказуемый путь, теряется при перезагрузке, «вернуть как было» невозможно | там же |
| 8 | **P3:** служебные файлы шаблона (README, LICENSE, package.json, .github…) копировались в web root и отдавались наружу — раскрывают шаблон/автора | там же |
| 9 | Старый каталог: 1 801 запись, не верифицирована (в базе навыка при проверке отсеивались оболочки CRA/Vite и репо без страницы) | `templates_catalog.json` (старый) |

Побочное наблюдение (не трогал): `test_regressions.sh` на `main` падает на пине «iOS app link must be Happ (id6504287215)» — в `govless.sh:583` ссылка ведёт на `apps.apple.com/us/app/incy/id6756943388`. Либо пин устарел, либо ссылка изменена случайно — нужно решение человека.

## 2. Что сделано

Один backend — три интерфейса.

### Backend (bash)
- `lib/templates_catalog.sh` переписан на базе навыка `site-templates-library` (goTelegram Pro 2.10.45): каталог **33 467** проверенных сайтов (`github` 32 010, ThemeWagon 1 264, dawidolko 141, HTML5 UP 45, StartBootstrap 7), постраничный список (25/стр, `n`/`p`, слово — поиск, `r` — случайный), `_tpl_pick_index_dir` (порядок папок важен), `_tpl_page_ok` (отсев `app_shell` / `broken_assets`), защищённый `git clone` (`protocol.allow=never` + https only, `GIT_TERMINAL_PROMPT=0`), причина отказа в `.last_error`. Сохранены пины Codex 029 (retry-in-place, `__skip__`, `tplcat_*` локализация).
- Новый неинтерактивный API: `site_template_apply <id>` / `_custom <url>` / `_random` / `_stub`, `site_restore_backup <tgz>`, `site_template_cli` → `govless --site-template-{status|apply|apply-url|random|stub|restore} [--json]` (один JSON на stdout, весь UI — на stderr; `flock` — один запуск за раз).
- `lib/website.sh`: `deploy_template_to_nginx` — бэкап в `/opt/govless/site-backups/site_<ts>.tgz` (5 последних), `strip_template_service_files`, маркеры `.govless_template_id` / `.govless_template_source`, **восстанавливает `.govless-owned`** (фикс №6); `deploy_stub_site` пишет маркер `__stub__`.
- `govless.sh`: `lib/templates_catalog.sh` подключается при старте; `submenu_proxy` → `4) Сменить режим (Lite ↔ Pro)`, **`5) 🌐 Сайт-маскировка / шаблон`** → `submenu_site` (каталог / случайный / свой Git / заглушка / вернуть из бэкапа / проверить); `main()` — вход `--site-template-*`; `sync_templates_catalog` при запуске меню и в `install_pro`; `config.site_template`; lazy-режим пробует до 3 случайных шаблонов.
- `lib/mode_switch.sh`: после Lite→Pro предлагается выбрать шаблон; если web root пуст при переиспользовании сертификата — ставится заглушка (раньше nginx отдавал 404 за fallback).
- i18n: +50 ключей в **обоих** языках (паритет проверен), `proxy_change_mode` больше не обещает смену шаблона.

### Демон `govlessctl` (`phase-a/govlessctl/methods.py`, `catalog_tool.py`)
- `site.template_status / categories / list / get / random / apply / job` (см. `CONTRACT.md`). `apply` — фоновая задача `asyncio` (клон 30–180 с > 30 с таймаута RPC/nginx), 409 при параллельном запуске, аудит start/result, `log` = хвост stderr без ANSI. Валидация: id `^[A-Za-z0-9_.-]{1,120}$`, категория `^[a-z0-9_-]{1,32}$` и существует, `custom_url` только https без метасимволов, `restore` только `/opt/govless/site-backups/site_*.tgz` без `..`.
- Установщик находится через `/usr/local/bin/govless` (всегда указывает на свежий код, 1.3.4+), fallback `/opt/govless-installer`, `/root/goVLESS`.
- `catalog_tool.py` (из навыка) + `get_by_key` (`sha1(id)[:12]` для callback_data ≤ 64 байт) и `total()`; кэш по mtime.

### Telegram-бот (`phase-a/bot/handlers/site.py`)
- Кнопка «🌐 Cover site / template» в разделах Proxy/VPN и Manage; `/site [слово]`, `/siteurl <url>`; категории → страницы по 8 → карточка (описание, превью, лицензия, звёзды) → Apply; случайный; заглушка (с подтверждением); бэкапы (с подтверждением). Прогресс — опрос `site.template_job` каждые 3 с, правка сообщения не чаще раза в 6 с (flood-safe), лимит 8 мин. Роутер подключён **до** catch-all confirm.

### Mini App (`phase-a/webapp/dist/app.jsx` → `app.js` пересобран `tools/build-webapp.js`)
- Экран `#/site` (`SiteTemplateScreen`): текущий шаблон, «Открыть сайт», случайный, заглушка, поиск, чипы категорий, список 24/стр, карточка-модал с превью/репо/Apply, свой Git URL, бэкапы, живой прогресс задачи (`TG.showConfirm` для подтверждений, `TG.openLink` для ссылок). Плитка на Home. +38 ключей STR ru/en (паритет OK). Cache-bust `app.js?v=20260908-site`.

### Прочее
- `install_phase_a.sh` копирует каталог в `/opt/govless` (бот может запуститься раньше SSH-меню).
- Версия 1.4.0 (`lib/common.sh`, `methods.py`) — `maybe_sync_version` при первом запуске `govless` переразвернёт phase-a (демон/бот/Mini App получают новый код).
- `test_regressions.sh`: блок пинов на все 8 слоёв фичи; docs: `GOVLESS_DOCS.md §14`, `CONTRACT.md`, `docs/DOCUMENTATION.md`.

## 3. Верификация (локально, macOS, bash 5.3 / python 3 + aiohttp/aiogram в venv)

- `bash -n` всех .sh — OK; паритет ключей ru/en — OK; `bash test_regressions.sh` — все пины проходят, кроме ранее сломанного Happ-пина (см. §1).
- Модуль каталога: по одному шаблону каждого источника (`h5up_*`, `da_*`, `sb_*`, `th_*`, `gh_*`) скачан и прошёл `_tpl_page_ok`; несуществующий id отклонён (`not_found`); деплой во временный web root — маркеры на месте, `.govless-owned` есть, README/LICENSE/package.json вычищены, бэкап создан, `_site_json_result` даёт валидный JSON.
- Демон (фейковый установщик): статус/категории/список/пагинация с clamp/поиск/get by key/random; отклонены `../x`, `nope`, `x;y`, `zz`, `http://`, URL с пробелом, `/etc/passwd`, `..`; фоновая задача → `done` с полями; второй запуск → 409; в Lite → `available:false` и 409 на apply.
- `import handlers.site` под aiogram — OK; `test_bot_regressions.py` — 7/7.
- **Не проверено на живом VPS** (доступ и домен в репо не лежат): полный цикл `govless → 1 → 5` на Pro, Mini App в Telegram WebView, реальные `nginx -t && reload` и HTTP-проверка. Это первый пункт для Reviewer / Human.

## 4. Открытые вопросы для Reviewer

1. Нужен ли typed-confirm для `site.template_apply` в боте/Mini App? Операция обратима (бэкап), сейчас — inline-подтверждение.
2. `SITE_JOB_TIMEOUT=420` с и `JOB_MAX_WAIT=480` с в боте — достаточно ли для слабых VPS с медленным GitHub?
3. Happ-пин на `main` (см. §1) — обновить тест или вернуть ссылку?
