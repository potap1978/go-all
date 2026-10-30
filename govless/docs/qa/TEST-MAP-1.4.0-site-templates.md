# Карта тестов goVLESS 1.4.0 (ветка `test`) — смена сайта-маскировки: SSH + бот + Mini App

Документ для тестирующего ИИ-агента (Opus 5). Цель: пройти по карте, найти баги, написать отчёт.
Объект: репозиторий `potap1978/go-all`, ветка **`test`**, коммит `8115067` и новее. Фича описана в
`ai-bridge/claude-codex/097-architect-site-template-switch-all-surfaces.md` и `GOVLESS_DOCS.md §14`.

## 0. Правила для агента

1. Тестируешь на **выделенном тестовом VPS** (раздел 1). Ничего не запускаешь на других серверах.
2. В `main` не пушишь никогда. Правки кода допустимы только для очевидных локальных багов (≤ 20 строк),
   отдельным коммитом в ветку `test` с префиксом `fix(qa):`, и только после того, как баг записан в отчёт.
   Всё остальное — только отчёт.
3. Секреты (токен бота, пароли, ключи) не попадают ни в репозиторий, ни в отчёт. Токен лежит локально
   в `~/Documents/Claude/Projects/govless/bot-token.local.md`.
4. Каждый кейс: выполнил → записал факт (команда, вывод, скриншот) → вердикт PASS / FAIL / BLOCKED / SKIP.
   Утверждение без вывода команды или скриншота не считается проверкой.
5. Let's Encrypt: домен один, сертификат уже выпущен при первой установке — **не удалять** `/etc/letsencrypt`
   между прогонами (rate limit → бан на неделю). «Удалить только сайт» (кейс G5) удаляет сертификат: делать
   его один раз, в самом конце.
6. Паузы между проверками состояния: 10–15 секунд, не минуты. Фоновая задача смены шаблона обычно 10–90 с.
7. Отчёт: `ai-bridge/claude-codex/098-reviewer-qa-site-templates-1.4.0.md` (формат — раздел 7), коммит в `test`.

## 1. Окружение и доступы

| Что | Значение |
|-----|----------|
| Сервер | `78.17.65.7` (Ubuntu 24.04, 1 vCPU, 2 GB) — hostname mefinlandserver.com |
| SSH | `ssh -i ~/.ssh/govless_test_key root@78.17.65.7` (ключ уже на сервере); пароль — в `qa-access.local.md` |
| Домен | `ilovelife.site` → A-запись на 78.17.65.7 (проверено 2026-09-08) |
| ОС | Ubuntu 24.04 / Debian 12, порты 80 и 443 открыты |
| Telegram-бот | `@govless_qa_140_bot` (https://t.me/govless_qa_140_bot); токен в `~/Documents/Claude/Projects/govless/bot-token.local.md` |
| Telegram-аккаунт | web.telegram.org в Chrome пользователя (Claude in Chrome) — им же тестируется бот и Mini App |
| Репозиторий | `gh repo clone potap1978/go-all -- -b test` (gh уже авторизован как potap1978) |

Правила безопасности сервера: тестовый VPS может быть переустановлен в любой момент; на нём нет чужих данных.

## 2. Подготовка (один раз)

```bash
# 2.1 DNS должен указывать на сервер
dig +short ilovelife.site            # == 78.17.65.7

# 2.2 Установка ветки test (на сервере, root). Репозиторий ПРИВАТНЫЙ: raw-ссылка без токена даёт 404,
#     поэтому токен gh (repo scope) лежит на стенде в /root/.gh_token (600) — bootstrap.sh берёт GH_TOKEN
#     и передаёт его git через Authorization-заголовок (в URL/.git/config не попадает). Удалить после тестов.
GH_TOKEN=$(cat /root/.gh_token) GOVLESS_BRANCH=test bash <(curl -sL -H "Authorization: Bearer $(cat /root/.gh_token)" https://raw.githubusercontent.com/potap1978/go-all/test/bootstrap.sh)
#     Повторный запуск той же команды = обновление кода (git fetch/reset в /opt/govless-installer).
#   выбор языка → 2) Pro → домен → email (можно пустой) → ШАБЛОН: категория → шаблон → 1 (установить)
#   → 3 ключа → 3X-UI New → TCP → safari → 1 подтвердить. Ждать LE-сертификат и панель.

# 2.3 После установки: версия, режим, шаблон, сайт
govless --site-template-status
cat /opt/govless/config.json | jq '{mode,domain,version,site_template}'
curl -sk -o /dev/null -w '%{http_code}\n' https://ilovelife.site/            # 200
cat /var/www/html/.govless_template_id; ls -la /var/www/html/.govless-owned

# 2.4 Бот: govless → 4) Бот → 5) мастер настройки → вставить токен → написать боту /start (auto-detect админа)
systemctl status govlessctl govless-bot --no-pager
govlessctl system.status

# 2.5 Mini App: в боте кнопка меню (слева от поля ввода) → откроется https://ilovelife.site/app/
```

Если установка не в Pro (например, DNS не готов) — это BLOCKED для разделов B–F, записать причину.

## 3. Как гонять три интерфейса

**SSH-меню.** Меню читает ввод с терминала, поэтому нужен pty. Два способа:
- интерактивно: `ssh -tt -i ~/.ssh/govless_test_key root@78.17.65.7 govless` и печатать ответы;
- скриптом `expect` (пример):
```bash
expect -c '
  set timeout 600
  spawn ssh -tt -i ~/.ssh/govless_test_key root@78.17.65.7 govless
  expect -re {▸ } ; send "1\r"          ;# VPN
  expect -re {▸ } ; send "5\r"          ;# Сайт-маскировка / шаблон
  expect -re {▸ } ; send "2\r"          ;# случайный шаблон
  expect -re {Enter}; send "\r"
  expect -re {▸ } ; send "0\r"; expect -re {▸ }; send "0\r"; expect -re {▸ }; send "0\r"
  expect eof'
```
Сохраняй вывод (`expect ... | tee logs/B5.log`). Русские подсказки в меню: «▸ » — приглашение ввода.

**Демон / CLI.** `govlessctl <method> --arg k=v --format json` (сокет `/run/govlessctl.sock`, root).
Неинтерактивный вход установщика: `govless --site-template-apply <id> --json` (один JSON на stdout).

**Бот.** В Chrome: `https://web.telegram.org/k/#@govless_qa_140_bot`. Кнопки — inline, нажимать кликом;
команды — текстом. После каждого действия — скриншот. Прогресс смены шаблона редактируется в том же сообщении.

**Mini App.** Открывать только из Telegram (нужен `initData`, иначе 401): кнопка меню в боте или
`/start` → кнопка «Web menu». Плитка «Сайт-маскировка» на главной → экран `#/site`. В Telegram Web K
Mini App открывается во всплывающем окне (iframe) — кликать по скриншоту.

Логи, которые надо смотреть при любом FAIL:
```bash
journalctl -u govlessctl -n 100 --no-pager
journalctl -u govless-bot -n 100 --no-pager
tail -50 /var/log/nginx/error.log
govlessctl audit.tail --arg limit=20
ls -la /opt/govless/site-backups/ /var/www/html/ | head -40
```

## 4. Тест-кейсы

Обозначения: **Ожид.** — ожидаемый результат; **Проверка** — чем подтвердить.

### A. Установка Pro и выбор шаблона при установке

| ID | Шаги | Ожид. | Проверка |
|----|------|-------|----------|
| A1 | Чистая установка Pro с выбором шаблона из каталога (категория → номер → превью → установить) | сайт = выбранный шаблон, сертификат выпущен, панель на домене | `curl -sk https://ilovelife.site/ \| head -20` содержит контент шаблона; `.govless_template_id` = id; `config.site_template` = id |
| A2 | В списке категории: `n`, `p`, слово (например `cafe`), `r`, неверный ввод (`abc123`, `9999`, пусто), `0` назад | листание работает, поиск фильтрует, `r` показывает превью, мусор не роняет меню, `0` возвращает в категории | скриншот/лог |
| A3 | В превью ответить «2» (не устанавливать) | возврат к списку той же категории, не к началу | лог |
| A4 | Выбрать «0) Пропустить — заглушка» | сайт = заглушка «Server is running», `.govless_template_id` = `__stub__` | `curl`, файл маркера |
| A5 | Ленивый режим (пункт 1 при установке) | случайный шаблон скачан и проверен; при неудаче — до 3 попыток (видно в логе `lazy_template`) | вывод установки |
| A6 | Свой Git при установке: `https://github.com/StartBootstrap/startbootstrap-agency` | сайт из репо, `.govless_template_source` = URL | `curl`, маркер |

Примечание: A1/A4/A5/A6 — разные установки; чтобы не жечь LE-квоту, повторные установки делать **поверх**
(сертификат переиспользуется: «SSL certificate already exists»). Если переустановка ломает что-то — это баг (G3).

### B. SSH-меню: `govless → 1 → 5` (Pro)

| ID | Шаги | Ожид. | Проверка |
|----|------|-------|----------|
| B1 | Открыть подменю | заголовок «Управление сайтом», строки Домен / Сайт / Шаблон с актуальным именем и id, число шаблонов в каталоге (33 467), пункты 1–6 и 0 | скриншот |
| B2 | 1) каталог: категория → страница → поиск словом → выбрать → превью → 1 | «Скачиваю…», «Шаблон установлен: …», «Прежний сайт сохранён: /opt/govless/site-backups/site_*.tgz», «Откройте https://ilovelife.site» | `curl -sk https://ilovelife.site/` — новый контент; `ls /opt/govless/site-backups` +1; `.govless_template_id` новый; `.govless-owned` есть |
| B3 | Повторить B2 ещё 2 раза с шаблонами из разных источников (`th_*`, `gh_*`, `h5up_*`, `da_*`, `sb_*`) | каждый раз успех, бэкапов не больше 5 (старые удаляются) | `ls -t /opt/govless/site-backups \| wc -l` ≤ 5 |
| B4 | 2) случайный | успех; если шаблон не прошёл проверку — «пробую другой…», до 3 попыток | лог |
| B5 | 3) свой Git: `https://github.com/html5up-collective/strata` | успех, «Сайт найден в: …», `.govless_template_source` = URL, в меню «свой из Git: URL» | маркер, B1 |
| B6 | 3) свой Git с плохими входами: пустой ввод; `ssh://git@github.com/x/y`; `https://github.com/user/repo; rm -rf /`; `https://github.com/nonexistent-org-xyz/nonexistent-repo-xyz`; репо без страницы `https://github.com/facebook/react` (или любой с исходниками) | каждый отклонён понятным сообщением (`bad_url`, `download_failed`, `no_index`/`app_shell`), сайт не изменился, меню не упало | `curl` контент прежний, `.govless_template_id` прежний |
| B7 | 4) заглушка → подтверждение 1 | заглушка, бэкап создан, HTTP 200 | `curl` содержит «Server is running» |
| B8 | 5) вернуть из бэкапа → выбрать самый свежий (это сайт до B7) | сайт вернулся, id восстановлен, ещё один бэкап (текущего) создан | `curl`, маркер |
| B9 | 6) проверить сайт | «Сайт отвечает: HTTP 200, index.html на месте» | вывод |
| B10 | Ввести мусор в подменю (`x`, `99`, пусто) | «Неверный выбор», меню перерисовано, из меню не выкидывает | скриншот |
| B11 | Служебные файлы не отдаются: после B2 `curl -sk -o /dev/null -w '%{http_code}' https://ilovelife.site/README.md`, `/LICENSE`, `/package.json`, `/.govless_template_id`, `/.git/config` | все 403/404, ни один 200 | коды |
| B12 | Параллельный запуск: в SSH выбрать 2) случайный и в это же время в боте нажать «Random» (или `govlessctl site.template_apply --arg random=true`) | второй получает «Смена шаблона уже идёт…» / 409, первый завершается нормально, сайт консистентен | логи обеих сторон |
| B13 | Права и владелец после деплоя | `/var/www/html` — www-data, 755, nginx отдаёт css/js шаблона (не только index) | `ls -la`, `curl -I https://ilovelife.site/<css из index>` = 200 |
| B14 | В Lite (после C2): `1 → 5` | «Управление сайтом доступно только в pro-режиме», подсказка `govless → 1 → 4`, пункт «Переключиться на Pro сейчас» | скриншот |

### C. Смена режима и шаблон

| ID | Шаги | Ожид. | Проверка |
|----|------|-------|----------|
| C1 | Pro → Lite (`1 → 4`), затем Lite → Pro (`1 → 4`) | после Lite→Pro задан вопрос «Выбрать шаблон сайта-маскировки сейчас?»; при «1» открывается каталог, при «2» — подсказка `govless → 1 → 5`; сертификат переиспользован; клиенты/UUID сохранены | лог, `govlessctl client.list` до/после |
| C2 | После Lite→Pro с отказом от выбора | сайт = заглушка или прежний шаблон (не 404) | `curl` 200 |
| C3 | «Удалить только сайт» (G5, делать в конце) → Lite→Pro | web root пустой → ставится заглушка, nginx не отдаёт 404 | `curl` 200 |

### D. Демон govlessctl и CLI

| ID | Команда | Ожид. |
|----|---------|-------|
| D1 | `govlessctl site.template_status --format json` | `available:true`, `domain`, `site_url`, `current.{id,kind,template.name}`, `catalog.total=33467`, `catalog.categories=18`, `backups[]`, `job` |
| D2 | `govlessctl site.template_categories --format json` | 18 записей с `id,name,name_en,count`, сумма count = 33467 |
| D3 | `site.template_list --arg category=legal --arg page=0 --arg per=5`; `--arg page=999` (clamp); `--arg q=cafe`; `--arg q=cafe --arg category=restaurant`; `--arg per=500` (clamp 50) | страницы, `pages`, `total`; `items[].key` — 12 hex |
| D4 | `site.template_get --arg template_id=th_law_firm`; `--arg key=<key из D3>`; `--arg template_id='x;y'`; `--arg key=zz` | первые два — карточка; последние два — 400 |
| D5 | `site.template_random`; `--arg category=legal`; `--arg category=nope` | карточка / карточка из legal / 404 |
| D6 | `site.template_apply --arg template_id=<id>` → каждые 3 с `site.template_job` | `state: running → done`, `ok:true`, `http_code:"200"`, `backup` путь, `log` без ANSI-кодов; сайт сменился |
| D7 | Сразу второй `apply` во время running | 409 «already running» с `data.job` |
| D8 | Плохие входы `apply`: `--arg custom_url=http://x`; `--arg custom_url='https://a b'`; `--arg restore=/etc/passwd`; `--arg restore=/opt/govless/site-backups/../x.tgz`; `--arg template_id=нет_такого` | 400/404, задача не запускается |
| D9 | `apply --arg custom_url=https://github.com/nonexistent-org-xyz/nonexistent-repo-xyz` → job | `state: failed`, `error_code: download_failed`, `error` человекочитаемый, сайт не изменился |
| D10 | `apply --arg stub=true`, `apply --arg random=true`, `apply --arg restore=<путь из status.backups[0].path>` | каждая задача `done`, сайт соответствует |
| D11 | `govlessctl audit.tail --arg limit=10` | записи `site.template_apply` и `site.template_result` с target/id |
| D12 | Каталог: `cmp /opt/govless/templates_catalog.json /opt/govless-installer/templates_catalog.json` (или `/root/goVLESS/`) | идентичны; после `git -C /opt/govless-installer pull` и запуска `govless` (выход 0) — снова идентичны |
| D13 | `govless --site-template-apply <id> --json` напрямую (и `--site-template-status --json`) | ровно один JSON-объект на stdout, весь UI на stderr (`... 2>/dev/null \| jq .`) |
| D14 | Перезапуск демона во время задачи: `apply` → `systemctl restart govlessctl` | после рестарта `job:null`, сайт либо прежний, либо новый, но целостный (index.html есть, HTTP 200); повторный apply работает |
| D15 | Нагрузка: 30 подряд `site.template_list` разных категорий и `q` | все < 1 с, память демона (`systemctl status govlessctl`) не растёт неограниченно (14 МБ каталог читается один раз, кэш по mtime) |

### E. Telegram-бот (через web.telegram.org)

| ID | Шаги | Ожид. |
|----|------|-------|
| E1 | `/start` → «Proxy / VPN» | есть кнопка «🌐 Cover site / template»; кнопка «🔁 Mode switch Lite ↔ Pro (SSH)» без слова template; в «Manage» тоже есть «🌐 Cover site / template» |
| E2 | Нажать «Cover site» | статус: Domain, Site, Template (имя + источник + id), Catalog 33467 · 18; кнопки Categories / Random / Stub / Backups / Refresh / Main |
| E3 | Categories → категория → страница (8 кнопок, Prev/Next, «1/N») → шаблон → карточка (описание, лицензия, звёзды, превью-ссылка, репо, id) → «✅ Apply this template» | сообщение «⏳ Applying …» редактируется (счётчик секунд), затем «✅ Template deployed: … HTTP check: 200, Previous site saved: …»; сайт сменился (`curl`) |
| E4 | `/site cafe` | список результатов (до 8) с кнопками; клик → карточка → Apply работает; `/site zzzzqqq` → «Nothing found» |
| E5 | `/site` без аргумента | тот же статус-экран, что E2 |
| E6 | «🎲 Random» → карточка → Apply; «🎲 Another random» в карточке | работает, каждый раз другой шаблон |
| E7 | «⏭ Built-in stub page» → «✅ Yes, install the stub» | заглушка установлена, прогресс/результат как в E3 |
| E8 | «↩️ Previous sites (backups)» → выбрать → «✅ Yes, restore» | сайт восстановлен |
| E9 | `/siteurl https://github.com/StartBootstrap/startbootstrap-landing-page` | «⏳ Applying custom template» → «✅ Custom template deployed»; `/siteurl` без аргумента — подсказка; `/siteurl ftp://x` — отказ; `/siteurl https://github.com/nonexistent-org-xyz/nonexistent-repo-xyz` → «❌ … download_failed» |
| E10 | Нажать Apply дважды подряд (второй раз во время выполнения) | второй: alert «Busy …», первый доходит до конца |
| E11 | Старая клавиатура: открыть карточку, сменить шаблон из SSH, вернуться и нажать Apply в старой карточке | работает (ключ резолвится демоном), либо понятное сообщение; бот не падает |
| E12 | Не-админ (второй аккаунт, если есть) нажимает кнопки / `/site` | «Not authorized» |
| E13 | Быстрые нажатия 20+ раз по категориям | rate limit «Too many taps» через ~30/мин — допустимо; после паузы работает |
| E14 | В Lite (после C1 Pro→Lite) «Cover site» | текст «exists only in Pro…», кнопок apply нет |
| E15 | `/help` | есть строки `/site [word]` и `/siteurl <https url>`; в списке команд бота (меню «/») есть `site`, `siteurl` |

### F. Mini App (открыть из бота)

| ID | Шаги | Ожид. |
|----|------|-------|
| F1 | Главная → плитка «Сайт-маскировка» (RU) / «Cover site» (EN) | открывается `#/site`, заголовок с доменом |
| F2 | Экран статуса | «Текущий шаблон» с именем/источником/id, «N проверенных шаблонов · 18 категорий», кнопки «Открыть сайт», «Случайный», «Заглушка», подсказка про бота и SSH |
| F3 | Чипы категорий → список (24/стр) → «Дальше »/« Назад» | список, счётчик «total · page/pages», навигация |
| F4 | Поиск «cafe» + Enter / кнопка «Найти» | «Найдено: N», список; поиск «zzzzqqq» → «Ничего не найдено» |
| F5 | Клик по шаблону → карточка → «Превью» (открывает ссылку), «Применить» → подтверждение (TG.showConfirm) → OK | тост «Запущено…», карточка закрывается, блок прогресса со спиннером и секундами, затем «✅ Шаблон установлен: … Проверка HTTP: 200 · прежний сайт сохранён», тост; «Текущий шаблон» обновился; сайт сменился |
| F6 | «Случайный» → OK; «Заглушка» → OK | работает; кнопки задизейблены, пока идёт задача |
| F7 | Свой Git: вставить `https://github.com/html5up-collective/strata` → «Применить» | успех; при `http://…` кнопка неактивна |
| F8 | «Бэкапы (N)» → раскрыть → «Вернуть» → OK | восстановление, прогресс, результат |
| F9 | Ошибка: `https://github.com/nonexistent-org-xyz/nonexistent-repo-xyz` | блок «❌ Не удалось: download_failed» + текст, сайт прежний |
| F10 | Переключить язык в настройках (RU ↔ EN) и тему | все строки экрана переведены (нет `undefined`), тёмная/светлая тема корректны |
| F11 | Открыть экран, пока задача уже идёт (запущена из бота) | экран сразу показывает прогресс и дожидается результата |
| F12 | Lite (после C1) | текст «есть только в Pro…», без кнопок |
| F13 | Открыть `https://ilovelife.site/app/#/site` в обычном браузере (без Telegram) | экран ошибки авторизации (401), не белый экран |

### G. Регрессия существующего функционала

| ID | Шаги | Ожид. |
|----|------|-------|
| G1 | Обновление с 1.3.6: на сервере с установленной 1.3.6 (main) выполнить `GOVLESS_BRANCH=test` bootstrap и запустить `govless` | экран «Обновление 1.3.6 → 1.4.0», бэкап создан, phase-a переразвёрнут (`journalctl -u govlessctl` показывает новый старт), в боте появилась кнопка «Cover site», Mini App показывает плитку |
| G1a | Путь обновления на этом стенде (1.3.6 недоступна): записать `systemctl show -p MainPID --value govlessctl govless-bot`, затем команда обновления из 2.2 (bootstrap той же ветки; **через `ssh -tt` + expect**, после обновления меню → `0` выход) и отдельно `bash /opt/govless-installer/phase-a/systemd/install/install_phase_a.sh` | после установщика PID govlessctl и govless-bot **изменились** (BUG-02 исправлен в b963819+), сервисы active, `govlessctl site.template_status` работает; повторный запуск установщика идемпотентен |
| G2 | Клиенты: `2 → 1/2/3/4` (список, ссылки, QR, регенерация), в боте «Users», в Mini App «Ключи» | без изменений в поведении |
| G3 | Переустановка поверх (`1 → 1 → 2 Pro` тот же домен) | не создаёт дубли inbound, не ломает сертификат, шаблон можно выбрать заново |
| G4 | Бэкап/восстановление (`3 → 4`, `3 → 5`) | работает; `config.site_template` сохраняется |
| G5 | В самом конце: «Удалить только сайт» (`3 → 6 → 1`, набрать `DELETE SITE`) при активном Pro | отказ с текстом «активный Pro VPN использует сертификат…» (guard). Переключиться в Lite и повторить → web root удалён (маркер `.govless-owned` есть после любого шаблона — это был баг) |
| G6 | `bash test_regressions.sh` в клоне ветки `test` | падают только 3 известных пина (Happ ×2, set +e), все `site`-пины проходят |
| G7 | Lite-установка на этом же сервере после «Удалить всё» | без ошибок; `1 → 5` показывает «только в Pro» |

### H. Безопасность

| ID | Шаги | Ожид. |
|----|------|-------|
| H1 | RPC `site.template_apply` с `template_id` вида `../../etc`, `$(id)`, `` `id` ``, 200-символьная строка | 400, ни одной команды не выполнено (проверить `journalctl -u govlessctl`, аудит) |
| H2 | `custom_url` = `https://github.com/x/y; curl evil`, `https://x/$(id)`, `ext::sh -c id`, `file:///etc/passwd` | 400 или `download_failed`, `git` не выполняет ничего, кроме https-клона |
| H3 | Свой Git-репозиторий, где в корне лежат symlink на `/etc/passwd` и папка `.git`/`.github` | после деплоя `curl https://ilovelife.site/<symlink>` не отдаёт содержимое системного файла (404/403); `.github` вычищен. Если symlink отдаётся — P1 |
| H4 | Свой Git с `index.html` + PHP-файлами | PHP не исполняется (нет php-fpm), отдаётся как текст или 404 — записать факт |
| H5 | Маркеры `/.govless_template_id`, `/.govless-owned`, `/.govless_template_source` | 403/404 снаружи |
| H6 | Бот: callback_data подделать нельзя из UI; но проверить `st:bk:999`, `st:v:zzzz:_:0` через старую клавиатуру/повтор | понятная ошибка, бот не падает |
| H7 | Не-root вызов `govlessctl` (пользователь без группы govless) | permission denied на сокет |

### I. UX и тексты

| ID | Что смотреть |
|----|--------------|
| I1 | В SSH-меню нет боксов `╔═╗`, только линии `═══`; эмодзи не ломают выравнивание списка категорий/шаблонов |
| I2 | Все строки на выбранном языке (RU/EN): переключить `3 → 1` и пройти `1 → 5` заново — нет ключей вида `site_opt_catalog` в сыром виде |
| I3 | Бот: тексты влезают в кнопки (≤ 60 символов), ссылки превью открываются, HTML-экранирование (имя шаблона с `<` `&`) не ломает сообщение |
| I4 | Mini App: длинные имена шаблонов обрезаются с `…`, ничего не выезжает за экран 380px |
| I5 | Время смены шаблона от нажатия до результата (замерить 3 раза, разные источники) — записать |

### J. Миграция с предыдущей версии (1.3.6 → 1.4.0) на реальном сервере

Отдельная фаза ПОСЛЕ основного прогона (после G5 стенд всё равно надо переустанавливать). Цель: убедиться,
что обновление рабочей 1.3.6 с ботом и Mini App проходит без ручных действий и ничего не теряет.

| ID | Шаги | Ожид. | Проверка |
|----|------|-------|----------|
| J1 | Подготовка: `govless → 3 → 6 → 3` «Удалить ВСЁ» (набрать `DELETE EVERYTHING`), затем чистая установка **1.3.6 из `main`**: `GH_TOKEN=$(cat /root/.gh_token) GOVLESS_BRANCH=main bash <(curl -sL -H "Authorization: Bearer $(cat /root/.gh_token)" https://raw.githubusercontent.com/potap1978/go-all/main/bootstrap.sh)` → Pro, тот же домен, шаблон из старого каталога, 3 ключа | установка 1.3.6 проходит (это ЕЩЁ один выпуск сертификата — второй за неделю, лимит 5; больше не переустанавливать) | `jq .version /opt/govless/config.json` = 1.3.6, сайт 200, `govless` показывает меню 1.3.6 (в VPN нет пункта 5) |
| J2 | Бот на 1.3.6: `govless → 4 → 1` (установить) и `4 → 5` мастер с тем же токеном (или BOT_TOKEN в bot.env + `systemctl restart govless-bot`), в Telegram `/admin <новый ADMIN_CLAIM_TOKEN из bot.env>` | бот 1.3.6 отвечает, в Proxy/VPN есть «🔁 Mode / template switch (SSH)» и НЕТ «Cover site»; Mini App открывается, плитки «Сайт-маскировка» нет | скриншоты «до» |
| J3 | Зафиксировать «до»: `govlessctl client.list` (UUID, имена, subId), `/root/.govless_credentials`, `/opt/govless/config.json`, `.govless_template_id`, `ls -la /var/www/html/.govless-owned` (на 1.3.6 после шаблона его НЕТ — известный баг), PID govlessctl/govless-bot, `systemctl show -p ActiveEnterTimestamp` | сохранено в `qa-1.4.0/migration-before.txt` | файл |
| J4 | Обновление: команда из 2.2 (ветка `test`) через `ssh -tt` + expect; на экране должно быть «Обновление 1.3.6 → 1.4.0», бэкап, «переразвёртывание phase-a»; выйти из меню `0` | без ошибок и без ручных действий; `journalctl -u govlessctl` показывает новый старт; PID демона и бота изменились (BUG-08) | лог expect, PID |
| J5 | «После»: `config.version` = 1.4.0, `client.list` идентичен «до» (UUID/subId/имена), панель доступна по прежнему URL, сайт тот же (id в `.govless_template_id`), подписка клиента работает (`curl -sk <sub_url>` → 200) | ничего не потеряно | diff before/after |
| J6 | Новые функции без переустановки: `govless → 1 → 5` показывает текущий шаблон 1.3.6; `govlessctl site.template_status` (available, current.kind=catalog); бот: кнопка «🌐 Cover site / template», `/site`; Mini App: плитка «Сайт-маскировка», список с кнопками «👁 Превью» и «Применить» | всё работает сразу после обновления; смена шаблона из бота проходит (backup создаётся) | скриншоты «после» |
| J7 | Маркер владения: после обновления, до любой смены шаблона, `ls /var/www/html/.govless-owned` — его нет (наследие 1.3.6). Проверить `govless → 3 → 6 → 1` «Удалить только сайт» в Lite (после `1 → 4`): web root чистится или отказ «no ownership marker»? | если отказ — это баг миграции (нужна миграция: восстановить маркер, когда есть `.govless_template_id`); записать как BUG с severity P2 | лог |
| J8 | Каталог после обновления: `cmp /opt/govless/templates_catalog.json /opt/govless-installer/templates_catalog.json` сразу после `govless` (без захода в 1 → 5) | синхронизирован (33 467), не старый 1 801 | `jq '[.categories[].templates[]]|length'` |
| J9 | Повторное обновление (та же команда ещё раз) | идемпотентно: без экрана обновления, PID меняются только от install_phase_a (или не меняются, если он не вызывался) — записать факт | лог |

Итог фазы — отдельный раздел в отчёте 098 (или 099, если 098 уже закоммичен): вердикт по миграции GO / NO-GO.

## 5. Что считать багом и как оценивать

- **P0** — потеря данных / компрометация / VPN перестал работать после смены шаблона (клиенты не подключаются, панель недоступна) / отдаются системные файлы.
- **P1** — функция не работает в одном из интерфейсов, сайт 404/500 после операции, бот или демон падают, сертификат сломан.
- **P2** — работает с обходным путём: неверное сообщение, залипание прогресса, лишний рестарт, не обновился статус.
- **P3** — тексты, выравнивание, i18n, мелочи.

Проверка «VPN жив» после каждого раздела B/D/E/F (обязательно):
```bash
govlessctl system.status --format json | jq '{xui_active,xray_active,nginx_active}'
ss -tlnp | grep -E ':443 |:80 '
govlessctl client.list --format json | jq 'length'
```
и подключение клиентом (если есть устройство) или хотя бы `curl -sk --max-time 5 https://ilovelife.site/` = 200.

## 6. Cheat sheet

```bash
# состояние сайта
govless --site-template-status
govlessctl site.template_status --format json | jq .
cat /var/www/html/.govless_template_id; ls -la /opt/govless/site-backups/
# смена
govlessctl site.template_apply --arg template_id=th_law_firm --format json
watch -n3 'govlessctl site.template_job --format json | jq .job.state'
govless --site-template-random --json 2>/dev/null | jq .
# бэкапы
govlessctl site.template_status --format json | jq '.backups[0].path'
# логи
journalctl -u govlessctl -f
journalctl -u govless-bot -f
tail -f /var/log/nginx/error.log
```

## 7. Формат отчёта (`ai-bridge/claude-codex/098-reviewer-qa-site-templates-1.4.0.md`)

```
# 098 — Reviewer QA: смена сайта-маскировки 1.4.0 (ветка test @ <sha>)
Окружение: сервер <IP>, домен <домен>, ОС, 3X-UI <версия>, xray <версия>, дата.
Итог: PASS <n> / FAIL <n> / BLOCKED <n> / SKIP <n>. Вердикт: GO / NO-GO / GO after fixes.

## Баги (по убыванию severity)
### BUG-01 [P1] <заголовок>
Кейс: B6. Шаги: … Ожидалось: … Фактически: … 
Доказательство: команда + вывод / скриншот (путь). Логи: … Гипотеза причины: файл:строка. 

## Таблица кейсов
| ID | Вердикт | Заметка / ссылка на доказательство |

## Замеры (I5), наблюдения UX (I1–I4), что не удалось проверить и почему
```

Скриншоты и логи складывать в `~/Documents/Claude/Projects/govless/qa-1.4.0/` (не в репозиторий).

---

## Доступы

Все секреты — только в локальных файлах `~/Documents/Claude/Projects/govless/qa-access.local.md` и
`bot-token.local.md` (ключ SSH: `~/.ssh/govless_test_key`). В отчёт и репозиторий их не переносить.
