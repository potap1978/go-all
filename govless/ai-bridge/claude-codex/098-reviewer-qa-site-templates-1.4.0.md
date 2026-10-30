# 098 — Reviewer QA: смена сайта-маскировки 1.4.0 (ветка `test` @ a342ee7)

Окружение: сервер 78.17.65.7 (Ubuntu 24.04, 1 vCPU, 2 GB), домен ilovelife.site, 3X-UI v3.7.0,
Xray 26.7.28, бот @govless_qa_140_bot, Mini App `https://ilovelife.site/app/`.
Даты прогона: 2026-09-08 19:55 – 2026-09-09 10:00 UTC (два экземпляра Reviewer, один журнал).
Карта тестов: `TEST-MAP-govless-1.4.0.md` (разделы A–I). Журнал и логи: `~/Documents/Claude/Projects/govless/qa-1.4.0/`.

**Важно про версии.** Прогон шёл на трёх коммитах: `b963819` (D, B первый заход) → `0ec1a53`
(фикс BUG-02, повтор B, E, H частично) → `3a805ba` (F13/F1–F4) → **`a342ee7`** (F5–F11, C, A2–A4, G, H3/H4, I).
Стенд был обновлён Architect'ом до `a342ee7` **посреди прогона**, в 08:07:42 UTC (`git reflog`
в `/opt/govless-installer`). `a342ee7` содержит пакет фиксов `16f932f` по багам BUG-03…07 + P3 и
`94cfabd` (кнопка «👁 Превью» в списке Mini App). Все выводы ниже про открытые баги проверены на `a342ee7`.

Итог: **PASS 75 / FAIL 1 / BLOCKED 1 / SKIP 6 / PARTIAL 2** (по кейсам карты A–I; I5 — замеры).
Вердикт: **GO after fixes** — фича смены сайта-маскировки работает во всех трёх интерфейсах и на демоне,
но перед релизом обязателен фикс **BUG-13 [P1]** (произвольное чтение файлов сервера через symlink в
пользовательском шаблоне) и желательны **BUG-14 [P2]** (переустановка поверх ломает Mini App) и
**BUG-11 [P2]** (кнопка Mini App после Pro→Lite).

---

## Баги (по убыванию severity)

### BUG-13 [P1] — symlink в пользовательском шаблоне отдаёт любой файл сервера, включая всю ФС через `/`
Кейс: **H3**. Статус: **ОТКРЫТ**.

Шаги. Создан публичный тестовый репозиторий с `index.html` и символическими ссылками в корне:
`passwd.html -> /etc/passwd`, `secret.txt -> /etc/shadow`, `rootlink -> /`, плюс каталог `.github/`
и файлы `info.php`, `index.php`. Развёрнут штатным путём:
`govlessctl site.template_apply --arg custom_url=https://github.com/potap1978/go-all-qa-symlink-test`.

Ожидалось (карта H3): `curl https://<домен>/<symlink>` не отдаёт содержимое системного файла (404/403).

Фактически. Задача завершилась `state:done, ok:true, http_code:"200"`, символические ссылки развёрнуты
в web root как есть:

```
$ ls -la /var/www/html/
lrwxrwxrwx 1 www-data www-data   11 Sep  9 09:33 passwd.html -> /etc/passwd
lrwxrwxrwx 1 www-data www-data    1 Sep  9 09:33 rootlink -> /
lrwxrwxrwx 1 www-data www-data   11 Sep  9 09:33 secret.txt -> /etc/shadow
-rw-r--r-- 1 www-data www-data   20 Sep  9 09:33 index.php
-rw-r--r-- 1 www-data www-data   43 Sep  9 09:33 info.php
```

nginx их отдаёт (в `/etc/nginx/sites-enabled/govless` нет директивы `disable_symlinks`):

```
GET /passwd.html                                → 200, 1831 б  (содержимое /etc/passwd)
GET /rootlink/etc/passwd                        → 200, 1831 б  (через rootlink -> / доступна вся ФС)
GET /rootlink/etc/nginx/sites-enabled/govless   → 200, 1424 б  (конфиг сервера: пути панели и т. п.)
GET /rootlink/var/log/nginx/access.log          → 200, 228 454 б (IP посетителей + все запрошенные URL)
GET /rootlink/etc/os-release                    → 200
GET /rootlink/etc/hostname                      → 200
GET /secret.txt  (/etc/shadow)                  → 403   ← только из-за прав root, не из-за защиты
GET /rootlink/etc/govless/bot.env               → 403   ← то же (0600 root)
GET /rootlink/etc/letsencrypt/…, /etc/x-ui/x-ui.db → 404 ← нет прав на родительский каталог
```

То есть закрыто всё, что закрыто правами Unix, и открыто всё, что читаемо `www-data`.
Утечка access.log особенно неприятна: там IP клиентов и запрошенные пути.

Позитив: каталог `.github/` вычищен корректно — `/.github/config.yml` и `/.github/MARKER.txt` → 403.

Гипотеза причины: деплой копирует дерево репозитория без фильтрации symlink
(`lib/website.sh`, функция развёртывания шаблона в `WEBSITE_ROOT`), а nginx по умолчанию
`disable_symlinks off`. Предлагаемый фикс — оба слоя:
1) при деплое удалять симлинки (`find "$src" -type l -delete` до копирования, либо `rsync --safe-links`);
2) в `/etc/nginx/snippets/` и в генераторе конфига добавить
   `disable_symlinks on from=$document_root;` в блок сайта.
Отдельно стоит выбрасывать `*.php`, `*.phtml` (см. H4 ниже).

Карта прямо задаёт severity: «Если symlink отдаётся — P1».

Модель угроз: злоумышленником здесь выступает автор шаблона. Это ровно тот сценарий, ради которого
фича сделана — оператор ставит один из 33 467 сторонних репозиториев или любой публичный https-URL.

После теста web root очищен (применён `th_photosen`, `find /var/www/html -type l -o -name "*.php"` = 0,
`/passwd.html` → 404). Тестовый репозиторий обезврежен и переведён в private;
**удалить `potap1978/go-all-qa-symlink-test` вручную** — у токена gh нет scope `delete_repo`.

### BUG-14 [P2] — переустановка Pro поверх выключает Mini App и `/api/rpc` (404)
Кейс: **G3**. Статус: **ОТКРЫТ**.

Шаги: `govless → 1 → 1 → 2 (Pro)`, тот же домен `ilovelife.site`, пустой email, шаблон «0) Пропустить».
Установка прошла успешно («SSL certificate already exists», «Переиспользую существующее подключение на :443»).

Фактически: сгенерированный заново конфиг сайта потерял строку
`include /etc/nginx/snippets/govless-webapp.conf;`, при этом сам сниппет на диске цел.

```
$ grep -n "include\|location" /etc/nginx/sites-enabled/govless     # после переустановки
10:    location ^~ /.well-known/acme-challenge/ {
26:    location / {
31:    location ~* \.(jpg|jpeg|png|gif|ico|css|js|svg|woff|woff2|ttf|eot)$ {
39:    location ~ /\. { deny all; }
40:    location = /robots.txt …
41:    location = /favicon.ico …
                                  ← include сниппета отсутствует
$ ls -la /etc/nginx/snippets/govless-webapp.conf
-rw-r--r-- 1 root root 3008 Sep  9 09:19 …                          ← сниппет на месте
$ curl -sk -o /dev/null -w '%{http_code}' https://ilovelife.site/app/      → 404
$ curl -sk -X POST -o /dev/null -w '%{http_code}' https://ilovelife.site/api/rpc → 404
```

Последствие: кнопка Mini App в боте открывает 404, экран «Сайт-маскировка» в Mini App недоступен.
VPN и SSH-меню не затронуты. Лечится повторным запуском
`bash /opt/govless-installer/phase-a/systemd/install/install_phase_a.sh`
(печатает «wired govless-webapp.conf into /etc/nginx/sites-enabled/govless», после чего `/app/` → 200).

Гипотеза: генератор nginx-конфига Pro (`lib/website.sh` / `lib/xui.sh`, функция создания
`/etc/nginx/sites-available/govless`) перезаписывает файл целиком и не добавляет include сниппета —
include ставит только `phase-a/systemd/install/install_phase_a.sh`, а install_pro его после себя не вызывает.

### BUG-11 [P2] — после Pro→Lite кнопка Mini App в боте ведёт на неработающий Pro-URL
Кейс: **F12/E14 (побочно)**. Статус: **ОТКРЫТ**.

Шаги: `govless → 1 → 4` (Pro→Lite), затем в боте нажать кнопку меню «Open app».

Фактически: кнопка осталась `web_app` с Pro-адресом, который в Lite не открывается (443 занят
xray-Reality, TLS-хендшейк браузера не проходит) — в Telegram открывается пустое окно без объяснения.

```
$ getChatMenuButton (Bot API, в режиме lite)
{"type":"web_app","text":"Open app","web_app":{"url":"https://ilovelife.site/app/?v=…"}}
$ govlessctl tunnel.url_get --format json
{"url": null, "source": "none"}                 ← правильное состояние: тоннеля нет
$ systemctl restart govless-bot && getChatMenuButton
{"type":"commands"}                             ← после рестарта бот сам выставляет верную кнопку
```

Гипотеза причины: `switch_mode` (lib/mode_switch.sh) не трогает меню-кнопку Telegram, а
`tunnel_watcher` в `phase-a/bot/handlers/webapp.py:118-140` реагирует только на изменение mtime
`/run/govless/tunnel.url*`, которое при смене режима не происходит; правильное состояние выставляет
только `apply_menu_buttons_once` при старте бота. Фикс: дёргать применение меню-кнопки после смены режима
(или добавить в watcher периодическую сверку `tunnel.url_get`).

### BUG-12 [P3] — в списке бэкапов печатаются сырые ANSI-последовательности
Кейс: **G4**. Статус: **ОТКРЫТ**.

`govless → 3 → 5`:

```
  Выбери backup для восстановления:
    \033[0;36m1)\033[0m govless-20260909T093157Z.tgz
  ▸
```

Причина: `govless.sh:1205` — `echo "    ${CYAN}${i})${NC} $(basename "$b")"` без `-e`, а
`CYAN='\033[0;36m'` (`lib/common.sh:51`). Рядом `govless.sh:1203` использует обычный текст и выглядит нормально.

### BUG-10 [P3] — ошибка `too_big` показывает лимит вместо реального размера репозитория
Кейс: **F11/D9-подобный**. Статус: **ОТКРЫТ**.

`govlessctl site.template_apply --arg custom_url=https://github.com/mozilla/pdf.js` →

```
job.error : "Репозиторий слишком большой: 100MB (макс 100МБ)"      ← пользователю
job.log   : "✗  Репозиторий слишком большой: 158MB (макс 100МБ)"   ← правда
```

В Mini App пользователь видит «❌ Не удалось: too_big / Репозиторий слишком большой: 100MB (макс 100МБ)» —
сообщение внутренне противоречиво. В `error` подставляется лимит, а не измеренный размер.

### BUG-09 [P3] — документация обещает окно повтора initData 300 с, фактически 24 ч
Кейс: **F13 (побочно)**. Статус: **ОТКРЫТ**. Не относится к фиче 1.4.0, обнаружен попутно.

Вкладка Mini App с `initData` возрастом **10,6 ч** (`auth_date`=1788902153, 38 076 с) продолжала
работать: экран отрисовался живыми данными, RPC отвечал 200.

```
phase-a/govlessctl/auth.py:37   def verify_webapp_init_data(init_data, bot_token, max_age: int = 86400)
phase-a/govlessctl/auth.py:224  ttl = int(bot_env.get("WEBAPP_AUTH_TTL", "86400") or 86400)
phase-a/CONTRACT.md:24          Reject if auth_date older than WEBAPP_AUTH_TTL (default 86400 sec)
docs/DOCUMENTATION.md:902       «auth_date не старше 5 мин»
docs/DOCUMENTATION.md:1168      auth_date in initData must be within 300s of server time
docs/DOCUMENTATION.md:1787      | `auth_date` ≤ 300s | T2 replay attacks |
```

Поведение соответствует коду и CONTRACT, но противоречит разделу безопасности и таблице модели угроз
в DOCUMENTATION.md. Надо привести к одному значению (и, если оставлять 86400, — объяснить почему).

### Ранее найденные и уже исправленные (подтверждено в этом прогоне)

| ID | Sev | Суть | Где исправлено | Проверка |
|----|-----|------|----------------|----------|
| BUG-01 | P1 | `install_phase_a.sh` молча падал на apt-локе unattended-upgrades | `b963819` | до прогона |
| BUG-02 | P1 | `_site_lock()` `exec 9>>… 2>/dev/null` глушил stderr на весь процесс: `site.template_job.log` пустой, SSH-меню «слепло» после любой операции с сайтом | `0ec1a53` (fix(qa) Reviewer) | B1–B13 после фикса, весь UI виден |
| BUG-03 | P3 | `/tmp/govless_git_err.XXXX` не удалялись | `16f932f` | `find /tmp -name 'govless_git_err*' -newermt '2026-09-09 08:07'` = **0** при ≈10 сменах шаблона после фикса |
| BUG-04 | P2 | В списке категорий `0)` = «Пропустить — заглушка»: естественное «назад» молча заменяло рабочий сайт заглушкой | `16f932f` (`TEMPLATES_ZERO_IS_BACK`) | в `1 → 5 → 1` пункт «0) « Назад», ввод 0 → «ℹ Смена шаблона отменена», сайт цел |
| BUG-05 | P2 | Restore терял `.govless_template_source` (в меню «свой из Git: git», `source_url:null`) | `16f932f` | custom git → stub → restore: `source_url` = URL, файл маркера на месте, в боте «Template: custom from Git: <URL>» |
| BUG-06 | P3 | Смешанный язык: EN-интерфейс бота + русские тексты установщика | `16f932f` | в EN-боте `/siteurl <нет репо>` → целиком английское сообщение. **Остаток см. ниже** |
| BUG-07 | P3 | Таймаут клонирования рапортовался как `download_failed` «репозиторий недоступен» | `16f932f` | three.js: `error_code: "timeout"`, «Клонирование не уложилось в 90 с…», длительность 91 с |
| BUG-08 | P1 | При обновлении `install_phase_a.sh` делал `systemctl start` (no-op для активного юнита) → демон и бот оставались на старом коде | `3a805ba` (Architect) | G1a: «restarted govlessctl.service (new code)», PID 129732→140483 и 134511→140490 |
| P3 | — | В результате бот показывал внутренний id `__stub__` | `16f932f` | «✅Template deployed: built-in stub page» |
| P3 | — | Сырой код «[409]» в алерте бота | `16f932f` | «Busy: a template change is already running — wait for it to finish, then try again.» |
| P3 | — | `TelegramBadRequest: message is not modified` на каждый Refresh | `16f932f` | 8 быстрых нажатий → 0 предупреждений в journalctl, NRestarts=0 |
| I1 | P3 | `printf %-30s` считал байты → рваная колонка в кириллических категориях | `16f932f` | рендер на сервере: колонка счётчиков ровная во всех 18 строках |
| B13 | P3 | `chmod -R 755` вешал +x на html/css/js | `16f932f` | файлы 644, каталоги 755, `find /var/www/html -type f -perm -u+x` = **0** |

**Остаток по BUG-06 (P3, открыт):** в Mini App при EN-интерфейсе блок результата берёт имя от установщика
по-русски — «✅ Template deployed: **стандартная заглушка** / HTTP check: 200 · previous site saved»,
хотя строкой выше «Current template» локализована как «built-in stub page».

---

## Наблюдения P3 без отдельного номера

1. **Голая строка `5000` в выводе.** При настройке Panel TLS печатается `5000` — это результат
   `PRAGMA busy_timeout = 5000;`, который sqlite3 CLI выводит в stdout (`lib/xui.sh:1460`;
   такой же паттерн `lib/xui_api.sh:858`). Видно и при установке, и при смене режима.
2. **`.php` отдаётся как текст (H4).** php-fpm нет, PHP не исполняется — но `GET /info.php` → 200 с телом
   `<?php echo "PHP-EXECUTED-".php_uname(); ?>`. Лучше не отдавать `.php` вовсе (404) и выбрасывать при деплое.
3. **Файлы смены режима остаются в /tmp.** `/tmp/govless_clients.json`, `/tmp/govless_users_map.json`,
   `/tmp/govless_mode_switch_meta.json` содержат UUID клиентов; права 0600 root, но их стоит удалять.
4. **Бэкап `govless-*.tgz` не содержит web root.** После `3 → 5` `config.site_template` восстанавливается
   (проверено: вернулся `th_photosen`), но сам сайт остаётся прежним → конфиг расходится с фактом.
   Тот же эффект после G5: сайт удалён, а `config.site_template` = `th_photosen`.
   Пользовательский статус при этом корректен — бот/Mini App читают `/var/www/html/.govless_template_id`.
5. **«✓ Restore complete. Run repair (Manage → Repair) if links look stale.»** — хардкод по-английски
   в русском интерфейсе (`3 → 5`).
6. **Lite→Pro снова спрашивает email для Let's Encrypt**, хотя сертификат уже есть и переиспользуется
   (в конфиге `email` пустой).
7. **Промпт «Что отдать клиенту?» не имеет выхода** (`1/2/3`, нет `0) Назад`): ввод `0` бесконечно
   повторяет вопрос. Вне фичи 1.4.0, но того же класса, что BUG-04.
8. **Пустой поиск в Mini App рисует счётчик страниц** — «Найдено: 0 · 1/1 / Ничего не найдено».
9. **Пины `test_regressions.sh` протухли (3 шт., G6)** — подробности в разделе G ниже.

---

## Таблица кейсов

| ID | Вердикт | Заметка / доказательство |
|----|---------|--------------------------|
| A1 | PASS | Первичная установка Pro с шаблоном из каталога (`h5up_aerial`), сертификат выпущен, панель на домене — `qa-1.4.0/logs` предыдущего прогона + D1 |
| A2 | PASS | `logs/A2-A3-A4-catalog.log`: `n`/`p` листают («Страница 2 из 16 · всего 389» → 1), слово — поиск, `9999`/out-of-range → «⚠ Неверный выбор», пусто → перерисовка, `0` → категории |
| A3 | PASS | `logs/A3-preview-decline.log`: предпросмотр → «2) Нет» → возврат в ту же страницу той же категории; затем «1» → установка Photosen |
| A4 | PASS | `logs/G3-reinstall.log`: при установке `0) ⏭ Пропустить — поставить заглушку` → «✓ Stub site deployed», `.govless_template_id` = `__stub__`, HTTP 200 |
| A5 | SKIP | Ленивый режим (пункт 1) не прогонялся — на стенде одна установка Pro, лимит времени |
| A6 | SKIP | Свой Git при УСТАНОВКЕ не прогонялся; путь покрыт после установки (B5, E9, F7) |
| B1 | PASS | Заголовок «Управление сайтом», Домен/Сайт/Шаблон, пункты 1–6 и 0, «33467 шаблонов» |
| B2 | PASS | Категория → поиск → предпросмотр → установка, бэкап, ссылка на домен |
| B3 | PASS | 3 источника (`th_*`, `h5up_*`, `sb_*`), бэкапов ровно 5 (ротация) |
| B4 | PASS | 2) случайный шаблон |
| B5 | PASS | Свой Git `StartBootstrap/startbootstrap-agency`; **замечание к карте**: `https://github.com/html5up-collective/strata` из B5/F7 не существует (gh api → 404) |
| B6 | PASS | пустой ввод / `ssh://` / URL с `; rm -rf /` / несуществующий репо / репо без страницы — все отклонены, сайт не менялся |
| B7 | PASS | Заглушка + подтверждение |
| B8 | PASS | Восстановление из бэкапа (BUG-05 тогда, сейчас исправлен) |
| B9 | PASS | «✓ Сайт отвечает: HTTP 200, index.html на месте» |
| B10 | PASS | `x`, `99`, пусто → «Неверный выбор», меню живо |
| B11 | PASS | README/LICENSE/package.json → 404; маркеры и `.git` → 403; ни одного 200 |
| B12 | PASS | Параллельно демон+SSH: «✗ Смена шаблона уже идёт…», задача демона доведена, сайт целостен |
| B13 | PASS | www-data, каталоги 755, файлы 644, `-perm -u+x` = 0, css отдаётся |
| B14 | PASS | В Lite `1 → 5` → «⚠ Управление сайтом доступно только в pro-режиме» + «1) Переключиться на Pro сейчас» |
| C1 | PASS | Pro→Lite и Lite→Pro: UUID сохранены (3 шт. посимвольно совпадают), сертификат переиспользован («Сертификат … уже есть, использую»), один inbound, после Lite→Pro задан вопрос про шаблон |
| C2 | PASS | При отказе от выбора — «ℹ Сменить шаблон сайта позже: govless → 1 → 5», сайт = встроенная заглушка, HTTP 200 (не 404) |
| C3 | SKIP | Требует Lite→Pro после G5, т. е. повторного выпуска LE-сертификата — запрещено условиями прогона |
| D1–D15 | PASS | Демон/CLI полностью (см. журнал): статус, категории, списки/поиск/clamp, get/random, apply+job, 409, 400 на плохих входах, failed/download_failed, stub/random/restore, аудит, идентичность каталога, один JSON на stdout, рестарт демона во время задачи, нагрузка 30×list = 3011 мс |
| E1–E11 | PASS | Бот: кнопка «Cover site», статус-экран, категории→карточка→Apply с прогрессом, `/site <слово>`, `/site`, Random, Stub, Backups, `/siteurl` (плохие и хорошие входы), busy, старая клавиатура |
| E12 | SKIP | Нет второго Telegram-аккаунта |
| E13 | PASS | 30 быстрых нажатий Refresh — бот жив; после `16f932f` предупреждений «message is not modified» больше нет |
| E14 | PASS | В Lite: «Mode: lite / The cover (masqueraded) site exists only in Pro mode…», кнопок применения нет |
| E15 | PASS | `/help` содержит `/site` и `/siteurl`, в меню «/» команды присутствуют |
| F1 | PASS | Плитка «Сайт-маскировка» в блоке «МЕНЮ» → `#/site` |
| F2 | PASS | Статус: текущий шаблон + id, «33467 проверенных шаблонов · 18 категорий», 3 кнопки, подсказка про бота и SSH |
| F3 | PASS | Чип «Юридические» → «76 · 1/4», 24 карточки, «Дальше »» → «76 · 2/4» |
| F4 | PASS | «cafe» → «Найдено: 68 · 1/3» (сходится с D3/E4), «zzzzqqq» → «Ничего не найдено» |
| F5 | PASS | Карточка → подтверждение «Установить «Klassy Cafe»? …» → тост «Запущено…» → «Применяю: … · 0с» → «✅ Шаблон установлен: Klassy Cafe / Проверка HTTP: 200 · прежний сайт сохранён»; на сервере `th_klassy_cafe`, `<title>Klassy Cafe …</title>` |
| F6 | PASS | Случайный и Заглушка; во время задачи все кнопки `disabled=true` |
| F7 | PASS | `http://…` → кнопка неактивна; `https://…startbootstrap-creative` → установлен, `source_url` = URL |
| F8 | PASS | Бэкапы (имя/размер/дата) → «Вернуть сайт из site_…tgz?» → восстановление |
| F9 | PASS | «❌ Не удалось: download_failed / …репозиторий недоступен…», текущий шаблон не изменился |
| F10 | PASS | RU↔EN и темы: EN-перевод полный, `undefined`/сырых ключей нет, тёмная тема корректна. Замечание — остаток BUG-06 |
| F11 | PASS | Полная перезагрузка страницы во время задачи: экран сразу показал «Применяю: … · 1с» + заблокированные кнопки и дождался результата |
| F12 | BLOCKED | В Lite Mini App по https недоступен (443 = Reality, cloudflared/webapp-frontend на стенде не настроены) — экран «есть только в Pro» проверить нельзя. Вместо этого найден BUG-11 |
| F13 | PASS | Чистая вкладка → «🔒 RPC недоступен / Открой из Telegram-бота»; `POST /api/rpc` без initData → 401 «caller field missing or invalid» |
| G1 | SKIP | Нет сервера с установленной 1.3.6 |
| G1a | PASS | Повтор bootstrap при актуальном коде — no-op; `install_phase_a.sh` → «restarted govlessctl/govless-bot (new code)», PID изменились, сервисы active, `site.template_status` работает |
| G2 | PASS | `2 → 1/2/3/4`: список, ссылки, QR по выбору («Отсканируйте в INCY»), «✓ Ссылки пересобраны из x-ui.db»; UUID не менялись; в боте карточка клиента, в Mini App «Ключи» + 3 клиента |
| G3 | PASS (с BUG-14) | Переустановка поверх: «SSL certificate already exists», «3X-UI уже установлен», «Переиспользую существующее подключение на :443», один inbound, 3 UUID целы, сертификат тот же, шаблон выбирается заново. **Но** Mini App и `/api/rpc` после неё 404 — BUG-14 |
| G4 | PASS (с BUG-12) | `3 → 4` создал бэкап, `3 → 5` восстановил, `config.site_template` вернулся; замечания — BUG-12 и рассинхрон конфиг/факт |
| G5 | PASS | В Pro: «✗ Не могу удалить сайт: активный Pro VPN использует сертификат этого домена…» + подсказка. После Pro→Lite: «ℹ Removed /var/www/html (had .govless-owned marker)», «Deleted all files relating to certificate ilovelife.site», «✓ Сайт удалён. 3X-UI и Xray не тронуты.» Проверено: `/var/www/html` нет, `/etc/letsencrypt/live` пуст, nginx остановлен, x-ui/xray/демон/бот active, :443 Reality, 3 клиента целы |
| G6 | PASS (3 протухших пина) | 81 PASS до первого FAIL (`set -e`); с отключёнными 3 пинами — **88 PASS / 0 FAIL**, включая пины cover-site, restart-after-redeploy и «QA follow-ups» |
| G7 | SKIP | Требует «Удалить всё» + чистую Lite-установку, что уничтожает стенд; по условиям прогона G5 идёт строго последним |
| H1 | PASS | `template_id` = `../../etc`, `$(id)`, `` `id` ``, 200 символов → 400 «invalid template_id», в journalctl ни traceback, ни следов выполнения |
| H2 | PASS | `custom_url` = `…; curl evil`, `https://x/$(id)`, `ext::sh -c id`, `file:///etc/passwd` → 400 «must be a public https:// git repository URL» |
| H3 | **FAIL** | BUG-13 — symlink отдаётся, через `rootlink -> /` читается вся доступная `www-data` ФС |
| H4 | PASS (замечание) | PHP не исполняется, но `.php` отдаётся как текст с исходником — P3 |
| H5 | PASS | `/.govless_template_id`, `/.govless-owned`, `/.govless_template_source`, `/.git/config`, `/.github/...` → 403 |
| H6 | PARTIAL | Подделать callback_data из UI нельзя; эквиваленты на уровне RPC (несуществующий бэкап, `key=zz`) → 400, бот не падает |
| H7 | PASS | `sudo -u qauser govlessctl system.status` → «permission denied opening /run/govlessctl.sock (need group govless or root)», сокет `srw-rw---- root:govless` |
| I1 | PASS | Боксов `╔═╗` нет (лёгкая рамка `┌─┐` только в выборе языка); выравнивание категорий исправлено — колонка счётчиков ровная во всех 18 строках |
| I2 | PASS | `3 → 1 → English`, затем `1 → 5`: «Website management», 6 пунктов + Back, «Website template categories» с 18 английскими названиями; сырых ключей нет |
| I3 | PARTIAL | Тексты кнопок бота короткие, ссылки превью кликабельны; шаблона с `<`/`&` в имени в каталоге не нашлось — HTML-экранирование не проверено |
| I4 | PASS | При ширине 380 px горизонтально прокручивается только лента категорий (так задумано); длинные имена обрезаются (`text-overflow: ellipsis; white-space: nowrap; overflow: hidden`) |
| I5 | — | Замеры ниже |

---

## Замеры (I5)

Длительность задачи по серверному аудиту (`site.template_apply` → `site.template_result`):

| Источник | Шаблон | Время |
|----------|--------|-------|
| каталог, themewagon | `th_klassy_cafe` | **3 с** |
| каталог, github (случайный) | `gh_omindud_chinese_resturant_page` | **4 с** |
| свой Git | `StartBootstrap/startbootstrap-creative` | **2 с** |
| свой Git, отказ по содержимому | `twbs/bootstrap` (320 МБ) → `broken_assets` | 3 с |
| свой Git, отказ по размеру | `mozilla/pdf.js` → `too_big` (158 МБ) | 10 с |
| свой Git, отказ по таймауту | `mrdoob/three.js` → `timeout` | 91 с (лимит 90 с) |

В интерфейсах сверху добавляется задержка опроса: Mini App опрашивает `site.template_job` раз в 2,5 с,
бот редактирует то же сообщение — итого пользователь видит результат через ~3–13 с для типового шаблона.

Нагрузка (D15): 30 подряд `site.template_list` разных категорий и `q` — 3011 мс, в среднем 100 мс на вызов.
Память демона: 22,6 МБ на старте → 62 МБ после первого чтения каталога → 214 МБ (peak 274,8 МБ)
после серии списков — ~11 % от 2 ГБ ОЗУ стенда. Утечки между повторными вызовами нет
(`MemoryCurrent` до и после одинаков), но базовое потребление после первого обращения к каталогу
на 14 МБ JSON заметное — стоит иметь в виду для VPS с 1 ГБ.

---

## Разбор «известных падающих пинов» `test_regressions.sh` (G6)

Все три — протухшие проверки, а не регрессии продукта:

1. `iOS app link must be Happ (id6504287215)` и 2. `Android app link must be Happ (com.happproxy)` —
   рекомендуемое клиентское приложение сменено на **INCY** коммитом `a7d2cc5`
   (`govless.sh:594` → `apps.apple.com/us/app/incy/id6756943388`,
   `govless.sh:599` → `play.google.com/store/apps/details?id=llc.itdev.incy`).
   Строк `id6504287215` / `com.happproxy` в коде больше нет вообще (`grep -rn` находит их только
   в самом `test_regressions.sh`). Пины надо переписать под INCY.
   Соседние пины `grep -qi 'Happ' lib/lang/{ru,en}.sh` проходят **случайно** — совпадают на
   `I18N[ms_what_happens]="Что произойдёт:"`, то есть тоже ничего не проверяют.
3. `main_menu must run with 'set +e'` — проверка `grep -A2 "set +e" govless.sh | grep -q "main_menu"`
   не видит цель: `set +e` на `govless.sh:1478`, `main_menu` на `:1494` (16 строк, окно `-A2` мало).
   Поведение корректное — пин надо расширить (или искать по блоку).

---

## Что не удалось проверить и почему

- **F12** (Mini App в Lite) — BLOCKED: в Lite Mini App отдаётся только через cloudflared/`webapp-frontend`,
  которые на стенде не настроены (стенд ставился сразу в Pro); по https в Lite сайт недоступен, так как
  :443 занят xray-Reality.
- **C3** — SKIP: сценарий требует Lite→Pro после G5, то есть повторного выпуска LE-сертификата;
  условиями прогона это запрещено (риск rate-limit).
- **G1** (обновление с реальной 1.3.6) — SKIP: нет второго сервера с 1.3.6. Путь обновления проверен
  суррогатом G1a (bootstrap той же ветки + `install_phase_a.sh`).
- **G7** (Lite-установка после «Удалить всё») — SKIP: требует полного сноса стенда, а G5 по условиям
  идёт строго последним; частично покрыто C1 (Pro→Lite создаёт рабочий Reality-inbound).
- **A5** (ленивый режим), **A6** (свой Git при установке) — SKIP по времени; соответствующие механизмы
  покрыты после установки (B4/B5, E9, F7).
- **E12** (не-админ) — SKIP: нет второго Telegram-аккаунта.
- **I3** — частично: шаблон с `<`/`&` в имени в каталоге не нашёлся, HTML-экранирование в боте не проверено.
- **Ветка «1» в вопросе после Lite→Pro** («Выбрать шаблон сейчас?» → открывается каталог) не проверена:
  в единственном цикле Lite→Pro проверялась ветка «2» (C2), второй цикл не делался ради экономии LE-квоты.
  Сам каталог из этой же функции (`site_change_from_catalog`) проверен через `1 → 5 → 1` (A2/A3).

## Состояние стенда после прогона

Режим **lite**, сайт и LE-сертификат удалены (G5), nginx остановлен; x-ui, xray (Reality на :443),
`govlessctl`, `govless-bot` активны, 3 клиента с исходными UUID целы. `/opt/govless-installer` на `a342ee7`.
Тестовый репозиторий `potap1978/go-all-qa-symlink-test` обезврежен (симлинки и PHP удалены) и переведён
в private — **удалить вручную**, у токена gh нет scope `delete_repo`.
Токен `/root/.gh_token` на стенде — удалить после окончания тестов.
