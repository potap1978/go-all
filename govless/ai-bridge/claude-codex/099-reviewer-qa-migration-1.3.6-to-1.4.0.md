# 099 — Reviewer QA: миграция 1.3.6 → 1.4.0 на реальном сервере + перепроверка фиксов второй волны

Окружение: сервер `78.17.65.7` (Ubuntu 24.04 LTS, 6.8.0-36, 1 vCPU / 2 GB RAM / 15 GB, swap 512 MB),
домен `ilovelife.site`, 3X-UI **v3.7.0 (new)**, Xray **26.7.28**, nginx 1.24.0, Telegram-бот
`@govless_qa_140_bot`, админ — Telegram-аккаунт владельца. Дата: **2026-09-09**.
Ветки: старт с `main` @ `95d8c5d` (**v1.3.6**), целевая `test` @ `771e277` → к концу прогона `53cc798` (**v1.4.0**).

Итог фазы J: **PASS 8 / FAIL 1 / SKIP 0** (J1–J9; J7 — «частично», описан ниже).
Перепроверка фиксов второй волны: **BUG-10, BUG-11, BUG-12, BUG-13, BUG-14 — подтверждены**,
**BUG-06 — подтверждён**, **BUG-09 — исправлен частично**. Регрессии: **89 PASS / 0 FAIL**.

**Вердикт по миграции: GO after fixes.**
Сам путь обновления 1.3.6 → 1.4.0 отработал безупречно и ничего не потерял. Но за прогон найден
**P0 вне фичи сайта-маскировки** (BUG-19: смена режима Pro→Lite делает нерабочий Reality-inbound,
xray жрёт память и его убивает OOM — VPN ложится) и **P1 в «Удалить ВСЁ»** (BUG-15: state.db
переживает полное удаление, новый владелец не может забрать админку). Оба воспроизводимы и
блокируют релиз до фикса.

---

## Баги (по убыванию severity)

### BUG-19 [P0] — Pro → Lite создаёт битый Reality-inbound: xray утекает по памяти и его убивает OOM

Кейс: перепроверка BUG-11 (переключение режимов). Статус: **НОВЫЙ, воспроизводится 100 %**.

**Шаги.** Инсталляция, сделанная как **Pro** (в `config.json` нет ключа `mask_domain` — его пишет
только `install_lite`, см. `govless.sh:188`), затем `govless → 1 → 4` (Pro → Lite).

**Ожидалось.** Reality-inbound с реальным сайтом-донором (`dest: <домен>:443`, непустые `serverNames`).

**Фактически.** `lib/mode_switch.sh:307`

```bash
mask_domain=$(config_get mask_domain 2>/dev/null || echo "google.com")
```

`config_get` (`lib/common.sh:630-642`) при отсутствующем ключе печатает пустой `default` и
**возвращает 0**, поэтому `|| echo "google.com"` никогда не срабатывает. Проверено на стенде:

```
$ jq 'has("mask_domain")' /opt/govless/config.json
false
$ config_get mask_domain => []
```

В результате `api_create_reality_inbound ""` (`lib/xui_api.sh:214-215`) пишет:

```
dest: :443
serverNames: ['', 'www.']
```

Reality-«донор» указывает сам на себя (`:443` = собственный слушатель). Xray начинает соединяться
сам с собой; RSS растёт линейно ~2 МБ/с при **трёх неактивных клиентах** и конфиге 3.4 КБ:

```
t=68s  rss=628MB free=577MB
t=78s  rss=650MB free=538MB
t=88s  rss=672MB free=500MB
t=98s  rss=692MB free=466MB
t=108s rss=711MB free=432MB
t=118s rss=731MB free=394MB
```

Через ~10 минут ядро убивает xray — **VPN падает**:

```
Sep 09 15:44:47 kernel: Out of memory: Killed process 231197 (xray-linux-amd6)
                        total-vm:3003576kB, anon-rss:965516kB
Sep 09 15:44:50 systemd[1]: x-ui.service: Failed with result 'oom-kill'.
```

Плюс ссылки клиентов уезжают с пустым SNI:
`vless://…&security=reality&pbk=…&fp=safari&sni=&sid=…` — подключиться по ним нельзя.

**Доказательство фикса гипотезы.** После ручной записи `mask_domain: "www.microsoft.com"` в
`config.json` ровно та же операция `1 → 4` даёт корректный inbound и стабильную память:

```
dest: www.microsoft.com:443
serverNames: ['www.microsoft.com', 'www.www.microsoft.com']
xray rss = 39 000 KB (39 MB) при uptime 28 s — рост прекратился
```

**Гипотеза причины.** `lib/mode_switch.sh:307` — неверный идиом отката: нужно
`config_get mask_domain "google.com"` (у `config_get` есть второй аргумент-default) либо явная
проверка на пустоту + интерактивный выбор донора (`select_reality_domain`).
Дополнительно стоит валидировать `mask_domain` перед `api_create_reality_inbound` и не создавать
inbound с пустым `dest`/`serverNames`.

Логи: `qa-1.4.0/logs/BUG11-pro2lite.log`, `qa-1.4.0/logs/BUG11-lite2pro.log`.

---

### BUG-15 [P1] — «Удалить ВСЁ» не трогает `/var/lib/govless`: админ-права и аудит переживают полное удаление

Кейс: J1 (подготовка). Статус: **НОВЫЙ**.

**Шаги.** `govless → 3 → 6 → 3`, ввод `DELETE EVERYTHING` → «✓ Всё удалено. VPS чистая, как будто
goVLESS никогда не ставился.» Затем чистая установка 1.3.6 и `govless → 4 → 1` (Phase-A), новый
`ADMIN_CLAIM_TOKEN`, в чате бота — `/admin <новый токен>`.

**Ожидалось.** Свежая база, первый `/admin <token>` забирает админку.

**Фактически.** Бот отвечает **«Admin is already claimed.»** (RPC 409), а на старте пишет
`admins=[346345888]` — id админа ПРЕДЫДУЩЕЙ инсталляции, при том что `ADMIN_IDS=` в `bot.env` пуст.

Причина: `remove_everything` (`lib/xui.sh:1313-1317`) удаляет `/opt/govless`, `/opt/govless-installer`,
`/etc/govless`, `/root/.govless_credentials`, но **не** `/var/lib/govless` — а реальная база с
Rule 3a живёт именно там (`phase-a/govlessctl/state_db.py:25 DB_PATH = "/var/lib/govless/state.db"`,
`:26 LEGACY_PATH = "/opt/govless/state.db"`). Проверено:

```
$ ls -l /proc/$(systemctl show -p MainPID --value govlessctl)/fd | grep db
… 6 -> /var/lib/govless/state.db

$ python3  # /var/lib/govless/state.db, СВЕЖАЯ 1.3.6-инсталляция
tables: ['govless_user_bindings','govless_audit','govless_capabilities','govless_meta',
         'govless_admins','admin_claimed','pending_confirms']
admin_claimed  [(1, '346345888', 1788897308)]        # claim от вчерашней инсталляции
govless_admins [('346345888', 'self-claim', 1788897308)]
govless_meta   [('schema_version','2'), ('install_at','1788895664'), ('last_audit_prune','1788923641')]
audit rows: 82
```

**Последствия.**
1. Безопасность: после «полного удаления» и переустановки (в т.ч. другим владельцем сервера)
   Telegram-ID старого админа сохраняет полный доступ к боту и панели.
2. Новый владелец **не может** забрать админку штатным путём: `admin_claimed` занят → 409 навсегда.
3. Утечка истории: 82 записи аудита и `install_at` прошлой инсталляции остаются на «чистой» VPS.

Побочно: `/root/govless-backups/govless-20260909T093157Z.tgz` (архив предыдущего прогона) тоже
пережил «Удалить ВСЁ».

**Ещё один эффект той же путаницы путей.** Бэкап перед обновлением (`maybe_sync_version` →
`backup_govless`) кладёт в архив **пустую легаси-базу**:

```
Contents:
  etc/x-ui/x-ui.db … opt/govless/state.db … etc/govless/bot.env  root/.govless_credentials
```

`/opt/govless/state.db` — осиротевший файл (4 таблицы, 0 строк), созданный при установке до
миграции путей; рабочая `/var/lib/govless/state.db` (7 таблиц, аудит, админы) в бэкап **не попадает**.
То есть restore из такого архива потеряет привязки админов и аудит.

**Гипотеза причины.** `lib/xui.sh:1313` и `backup_govless` перечисляют пути вручную и не были
обновлены после переезда state.db в `/var/lib/govless` (Rule 3a).

Логи: `qa-1.4.0/logs/J1-nuke.log`, `qa-1.4.0/logs/J2-bot-136.log`.

---

### BUG-18 [P1] — смена режима не атомарна: падение на середине оставляет несогласованное состояние

Кейс: перепроверка BUG-11 (Lite → Pro). Статус: **НОВЫЙ**.

**Шаги.** `govless → 1 → 4` (Lite → Pro) в момент, когда API панели недоступен (у нас — следствие
BUG-19: xray убит OOM, x-ui отвечает TLS-таймаутами).

**Фактически** (`qa-1.4.0/logs/BUG11-lite2pro.log`):

```
▸ Switching mode: lite → pro
✓  Авторизация в панели OK
Extracted 3 clients from inbound id=1 (transport=tcp, security=reality)
ℹ  Preserving 3 clients · transport=tcp
ℹ  Сертификат для ilovelife.site уже есть, использую
✓  nginx config created for ilovelife.site      ← nginx уже переписан под Pro
ℹ  Удаляю старый inbound id=1…
✗  Delete inbound: HTTP 000
✗  Переключение не удалось — состояние могло частично изменилось
✗  Проверь: systemctl status x-ui  и  govless → 1 → 2
```

`config.mode` остался `lite`, при этом Pro-конфиг nginx был сгенерирован до провала. Пользователю
предлагается разбираться руками. Отката nginx-конфига/сертификатной обвязки нет; регрессия
«mode switch rolls back old inbound when new inbound creation fails» покрывает только inbound.

**Что нужно.** Проверять доступность API панели ДО записи nginx-конфига (health-check + ретраи),
и откатывать nginx-конфиг при провале, как это делается для inbound.

Повторный запуск после `systemctl restart x-ui` прошёл штатно (`BUG11-lite2pro-retry.log`),
сертификат переиспользован («Сертификат для ilovelife.site уже есть, использую»).

---

### BUG-16 [P2] — `bootstrap.sh` не умеет переключать ветку и при этом рапортует успех

Кейс: **J4**. Статус: **НОВЫЙ**. Это единственный FAIL в фазе J.

**Шаги.** Стенд установлен с `GOVLESS_BRANCH=main` (клон `git clone --depth 1 -b main`). Далее —
документированная команда обновления из раздела 2.2 карты с `GOVLESS_BRANCH=test`.

**Ожидалось.** `/opt/govless-installer` переезжает на `test`, запускается 1.4.0.

**Фактически** (`qa-1.4.0/logs/J4-update.log`):

```
ℹ  Updating...
✓  Updated to latest test          ← ЛОЖЬ
✓  Ready
  goVLESS  v1.3.6                  ← остались на main/1.3.6
```

Состояние клона после «обновления»:

```
$ cd /opt/govless-installer && git log --oneline -1
95d8c5d hotfix: goVLESS 1.3.6 …
$ git config --get-all remote.origin.fetch
+refs/heads/main:refs/remotes/origin/main
$ git branch -r
  origin/HEAD -> origin/main
  origin/main
```

**Гипотеза причины.** `bootstrap.sh:88` клонирует `--depth 1 -b "$BRANCH"` → single-branch refspec.
При апдейте (`bootstrap.sh:73-80`) `git fetch origin "$BRANCH"` завершается успешно, но кладёт ветку
только в `FETCH_HEAD`; `refs/remotes/origin/test` не создаётся, поэтому и `git checkout -f test`,
и `git checkout -qB test origin/test`, и `git reset --hard origin/test` падают — все три под `|| true`.
Сообщение «✓ Updated to latest $BRANCH» печатается по успеху одного лишь `fetch`.

**Последствия.** Для штатного пути `main → main` всё работает. Но: (а) любая смена ветки
(main→test, test→main, релизная ветка) молча не происходит; (б) сообщение об успехе печатается,
даже когда `reset --hard` не удался по любой причине (грязное дерево, обрыв) — пользователь уверен,
что стоит новая версия.

**Фикс-направление.** `git remote set-branches origin "$BRANCH"` (или `--add`) перед fetch, либо
`git fetch origin "+refs/heads/$BRANCH:refs/remotes/origin/$BRANCH"`; печатать «✓ Updated» только
после успешного `reset --hard`, иначе — предупреждение.

**Обход для продолжения прогона (на стенде, не в коде):**

```
git remote set-branches --add origin test
git -c http.<repo>.extraHeader="Authorization: Basic …" fetch --depth 1 origin test
```

после чего та же команда 2.2 отработала штатно (`J4-update2.log`).

---

### BUG-17 [P2] — обновление 1.3.6 → 1.4.0 не перегенерирует nginx-конфиг Pro: защита BUG-13 не приезжает

Кейс: **J5 / перепроверка BUG-13**. Статус: **НОВЫЙ**.

Сразу после обновления (`qa-1.4.0/migration-after.txt`):

```
sites-available include: 0
sites-enabled include:   1
placeholder left:        0
disable_symlinks:        0      ← директивы из фикса BUG-13 в живом конфиге НЕТ
```

`generate_nginx_pro_config` вызывается только при установке / переустановке Pro и при смене режима,
а `maybe_sync_version` их не вызывает. Значит на **обновлённых** инсталляциях (главный сценарий
выката 1.4.0!) эшелон «nginx не ходит по симлинкам» отсутствует до первой переустановки Pro.
Практическая защита (вычистка симлинков при развёртывании) при этом работает — см. BUG-13 ниже, —
но заявленный второй рубеж не активируется.

После переустановки поверх (`1 → 1 → 2`) обе директивы появляются (см. BUG-14).

**Фикс-направление.** В `maybe_sync_version` (или в отдельной миграции) перегенерировать
nginx-конфиг для Pro и перечитать nginx.

---

### BUG-20 [P2] — после миграции «Удалить только сайт» удаляет сертификат, но оставляет сайт и рапортует успех

Кейс: **J7**. Статус: **НОВЫЙ** (карта прямо предсказывала этот класс).

После обновления 1.3.6 → 1.4.0 маркера владения нет (наследие 1.3.6):

```
$ ls -la /var/www/html/.govless-owned
ls: cannot access '/var/www/html/.govless-owned': No such file or directory
$ cat /var/www/html/.govless_template_id
h5up_aerial
```

`govless → 1 → 4` (в Lite) → `3 → 6 → 1` → `DELETE SITE` (`qa-1.4.0/logs/J7-delete-site.log`):

```
▸ Удаляю Pro-сайт + nginx config + LE-сертификат...
⚠  Пропускаю /var/www/html — нет ownership marker (.govless-owned). Не удаляю чужой контент.
Deleted all files relating to certificate ilovelife.site.
✓  ✓ Сайт удалён. 3X-UI и Xray не тронуты.
```

Итог: nginx-конфиг снят, **LE-сертификат удалён безвозвратно**, файлы сайта остались на диске,
а пользователю сказано «Сайт удалён». Половинчатое состояние + вводящее в заблуждение сообщение.

**Фикс-направление.** Миграция: при обновлении, если существует `.govless_template_id`, создавать
`.govless-owned`. И/или в `remove_site_only` при пропуске web root писать не «Сайт удалён»,
а «Сертификат и конфиг удалены, файлы сайта оставлены (нет маркера владения): <путь>».

---

### BUG-21 [P3] — EN Mini App: сырой код ошибки и русский текст в английском интерфейсе

Кейс: перепроверка BUG-06/BUG-10. Экран «Cover site» в EN-локали Mini App:

```
❌ Failed: too_big
Репозиторий слишком большой: 1809MB (макс 100МБ)
```

Пользователю показывают внутренний код ошибки (`too_big`) и русскую фразу при английском UI:
`SITE_RESULT_ERROR` собирается на сервере с языком инсталляции (`config.language=ru`), а Mini App
печатает строку как есть. Ожидалось «Repository too large: 1809MB (max 100MB)»
(строка `en.sh:308 custom_git_too_big` существует).

---

### BUG-22 [P3] — установка заглушки не чистит web root

После переустановки Pro с выбором «0) Пропустить — поставить заглушку» рядом с новым `index.html`
остались файлы предыдущего шаблона:

```
.govless_template_id (__stub__)   index.html (1192 b, 15:26)
pic.svg   (100 b, 15:24)   style.css (29 b, 15:24)   ← от предыдущего шаблона
```

`deploy_stub_site` пишет `index.html` и маркеры, но не удаляет прежнее содержимое каталога.

---

### BUG-23 [P3] — `serverNames` дублирует префикс `www.`

`lib/xui_api.sh:215` — `serverNames: [mask_domain, f"www.{mask_domain}"]`. Если `mask_domain` уже
начинается с `www.`, получается мусорный второй SNI:

```
serverNames: ['www.microsoft.com', 'www.www.microsoft.com']
```

---

## Перепроверка фиксов второй волны (коммит 771e277)

| Баг | Статус | Доказательство |
|-----|--------|----------------|
| **BUG-13 [P1]** symlink-побег | **ПОДТВЕРЖДЁН** | Шаблон `github.com/potap1978/go-all-qa-symlink-test@symlinks` (`passwd.html -> /etc/passwd`, `rootlink -> /`, `secret.txt -> /etc/shadow`) установлен (`job.state=done`, `http_code=200`). `find /var/www/html -type l \| wc -l` = **0**; в web root только `index.html`, `pic.svg`, `style.css` + маркеры. HTTP: `/passwd.html` 404, `/secret.txt` 404, `/rootlink/etc/passwd` 404, `/rootlink/var/log/nginx/access.log` 404, `/rootlink/etc/os-release` 404, `/rootlink/etc/hostname` 404 — ни одной 200 с системным содержимым (в отчёте 098 они отдавались 200 с реальными файлами). Отдельно проверен nginx-рубеж: после переустановки Pro вручную созданы `qa-nginx-symlink.html -> /etc/passwd` и `qa-rootlink -> /` → все запросы 404, в error.log `openat() … failed (40: Too many levels of symbolic links)`. |
| BUG-13 — замечание | P3 | Предупреждения про удалённые симлинки в логе установки **нет**: `download_custom_template` удаляет их раньше (`lib/templates_catalog.sh:616`), поэтому `strip_template_service_files` (`lib/website.sh:373-378`) видит 0 ссылок и молчит. Сообщение `website_symlinks_removed` в этом сценарии недостижимо. См. также BUG-17 (на обновлённых инсталляциях nginx-директивы нет). |
| **BUG-14 [P2]** потеря Mini App при переустановке | **ПОДТВЕРЖДЁН** | После переустановки поверх (`1 → 1 → 2`, тот же домен, шаблон «Пропустить»): `nginx -t` — «syntax is ok / test is successful»; `grep -c govless-webapp.conf /etc/nginx/sites-available/govless` = **1**; `grep -c WEBAPP_INCLUDE_PLACEHOLDER` = **0**; `grep -c disable_symlinks` = **1**; `curl https://ilovelife.site/app/` = **200**, `GET /api/rpc` = **405** (не 404), `/` = 200. Клиенты и сертификат не тронуты (те же 3 UUID, серийник `056B40…9E73`). |
| **BUG-11 [P2]** залипшая кнопка меню | **ПОДТВЕРЖДЁН (в обе стороны)** | Pro→Lite: `getChatMenuButton` → `{"type":"commands"}` **без рестарта бота** (PID `227578`, `ActiveEnterTimestamp` 15:15:34 — не менялся), в чате «🔌 Tunnel URL cleared (no active tunnel).» 18:34. Lite→Pro: журнал `15:49:17 handlers.webapp setting WebApp menu button: source=pro url_fp=51cab24f7391` тем же PID, `getChatMenuButton` → `type: web_app, text: "Open app", host ilovelife.site, path /app/?v=…`, в чате «🔄 Tunnel URL updated (pro). Menu button refreshed.» Интервал реакции ≤ 30 с (POLL_INTERVAL_SECONDS). |
| **BUG-10 [P2]** реальный размер в «слишком большой» | **ПОДТВЕРЖДЁН** | `site.template_apply custom_url=https://github.com/torvalds/linux` → `error_code: too_big`, `error: «Репозиторий слишком большой: 1809MB (макс 100МБ)»`, тот же текст в логе задания и в Mini App. Раньше показывалось «100MB». Прежний сайт не тронут («✗ Не удалось сменить шаблон — прежний сайт не тронут»). |
| **BUG-12 [P2]** ANSI в списке бэкапов | **ПОДТВЕРЖДЁН** | `3 → 5`: `Выбери backup для восстановления:` / `1) govless-20260909T151522Z.tgz` / `2) govless-20260909T093157Z.tgz`. В сыром логе (`qa-1.4.0/logs/BUG12-backups.log`) литералов `\033[` и `\e[` — **0**. |
| **BUG-06 [P2]** имя заглушки на языке клиента | **ПОДТВЕРЖДЁН** | Mini App → Settings → Interface language = English → Cover site: «Current template / **built-in stub page** / 33467 verified templates · 18 categories», кнопки «Open site / 🎲 Random / ⏭ Stub page». Русского названия нет. |
| **BUG-09 [P3]** docs про `auth_date` | **ИСПРАВЛЕН ЧАСТИЧНО** | Поправлено: `docs/DOCUMENTATION.md:1168` («within `WEBAPP_AUTH_TTL` … default **86400s**», со ссылкой на QA BUG-09) и `:1787` («`auth_date` ≤ `WEBAPP_AUTH_TTL` (default 86400s)»). Осталось неверным: `:902` «`auth_date` не старше **5 мин**» и псевдокод `:1206` `assert (now - int(fields["auth_date"])) < 300`, тогда как `phase-a/govlessctl/auth.py:37` — `max_age: int = 86400`. |
| **G6** протухшие пины | **ПОДТВЕРЖДЁН** | `bash test_regressions.sh` в клоне: **89 PASS / 0 FAIL** и на `771e277`, и на `53cc798` (`qa-1.4.0/logs/G6-regressions.log`, `G6-regressions-53cc798.log`). Пины про Happ→INCY и `set +e` починены. |

---

## Таблица кейсов J1–J9

| ID | Вердикт | Заметка / доказательство |
|----|---------|--------------------------|
| **J1** | **PASS** | «Удалить ВСЁ» (`3 → 6 → 3`, `DELETE EVERYTHING`) → «✓ Всё удалено. VPS чистая…» (`logs/J1-nuke.log`), после него `/opt/govless`, `/opt/govless-installer`, `/etc/govless`, `/var/www/html`, `/usr/local/bin/govless` отсутствуют. Чистая установка 1.3.6 из `main`: Pro (свой сайт + TLS), домен `ilovelife.site`, шаблон из старого каталога (категория «Бизнес и корпоративные» → **Aerial [html5up]**, каталог **1801** шаблонов), 3 ключа, 3X-UI v3.7.0, TCP, safari. Сертификат выпущен один раз («Successfully received certificate… expires on 2026-12-08»). Проверка: `jq .version` = **1.3.6**, `curl https://ilovelife.site/` = 200 с `<title>Aerial by HTML5 UP</title>`, `.govless_template_id` = `h5up_aerial`. Меню 1.3.6: в подменю VPN пункты 1–4, пункта «Сайт-маскировка / шаблон» нет (`logs/J1-menu-136.log`). Оговорка: см. **BUG-15** — база `/var/lib/govless/state.db` пережила удаление. |
| **J2** | **PASS** | `govless → 4 → 1` — Phase-A установлен («install_phase_a.sh: MODE=pro», «wired govless-webapp.conf into /etc/nginx/sites-enabled/govless», «✓ Phase-A установлен»); `4 → 1` — токен принят («✓ Токен рабочий, бот: @govless_qa_140_bot», «✓ Токен сохранён», «✓ Бот перезапущен»). Бот 1.3.6 отвечает `/start`. В Proxy/VPN: «👥 Users, links and QR», «📊 Full status», «🔁 Mode / template switch (SSH)», «🏠 Main menu» — **кнопки «Cover site» нет**. Mini App показывает **v1.3.6**, плитки «Сайт-маскировка» нет; на сервере: `grep -c '#/site' /opt/govless/webapp/dist/app.js` = **0**, `grep -c template_apply /opt/govless/govlessctl/methods.py` = **0**, `handlers/site.py` отсутствует. Привязка админа: `/admin <новый ADMIN_CLAIM_TOKEN>` → «Admin is already claimed.» — см. **BUG-15**; фактически админ уже был в унаследованной базе, так что доступ работал. |
| **J3** | **PASS** | Снимок «до» — `qa-1.4.0/migration-before.txt`: config.json (v1.3.6, mode pro, sub_port/sub_path), `client.list` (3 клиента с UUID/subId/link/sub_url), клиенты из `x-ui.db`, `/root/.govless_credentials`, маркеры web root (`.govless_template_id=h5up_aerial`, `.govless-owned` **отсутствует**), sha256 `index.html`, PID и `ActiveEnterTimestamp` четырёх юнитов, сертификат (serial `056B40…9E73`), каталоги 1801/1801, HTTP-коды. |
| **J4** | **FAIL → BUG-16** (после обхода — PASS по существу) | Документированная команда 2.2 с `GOVLESS_BRANCH=test` напечатала «✓ Updated to latest test», но клон остался на `main`/1.3.6 (`logs/J4-update.log`). После ручной починки refspec на стенде та же команда отработала штатно (`logs/J4-update2.log`): экран «**Обновление goVLESS: 1.3.6 → 1.4.0**», бэкап `/root/govless-backups/govless-20260909T151522Z.tgz (12K, 7 files)`, «Обновляю компоненты бота (демон · бот · веб)…», переразвёртывание phase-a («templates catalog synced», «restarted govlessctl.service (new code)», «restarted govless-bot.service (new code)»), «✓ goVLESS обновлён до v1.4.0 ✓». Ручных действий не потребовалось. PID: govlessctl `218536 → 227571`, govless-bot `225243 → 227578`, nginx `218341 → 227363`, x-ui `217090` не менялся. |
| **J5** | **PASS** | `qa-1.4.0/migration-after.txt`: `config.version` = **1.4.0**; `client.list` идентичен «до» — те же имена/UUID/subId (`nexus-margay eb4f1d12-…`, `nova-macaw 12d27e4b-…`, `inlet-toad c754e6e4-…`), `sub_url` без изменений; `/root/.govless_credentials` побайтно тот же (порт 41196, тот же web-path); сайт тот же (`.govless_template_id=h5up_aerial`, sha256 `index.html` `8d9ee93d…7751b`, 25 файлов); сертификат тот же (serial `056B40…9E73`, notAfter Dec 8 2026); HTTP: `/` 200, `/app/` 200, `/api/rpc` 405, подписка клиента **200**, панель **200**. |
| **J6** | **PASS** | SSH: `1 → 5` → «🌐 Управление сайтом», Домен/Сайт/**Шаблон: Aerial (h5up_aerial)**, пункты 1–6 и каталог «33467 шаблонов» (`logs/J6-ssh-site.log`). Демон: `site.template_status` → `available:true`, `current.kind = "catalog"`, `current.id = h5up_aerial`, `catalog.total = 33467`, `categories = 18`. Бот: в Proxy/VPN появилась «🌐 Cover site / template»; `/site` → «Cover site / Domain: ilovelife.site / Template: Aerial [html5up] h5up_aerial / Catalog: 33467 verified templates · 18 categories» + кнопки Categories / Random / Built-in stub page / Refresh; появились команды `/site` и `/siteurl`. Mini App: плитка «Сайт-маскировка» есть, экран показывает текущий шаблон, поиск, категории, «Свой шаблон из Git». Смена шаблона из бота (Random → Apply this template): «✅ Template deployed: My Portfolio Website / HTTP check: 200 / Previous site saved: /opt/govless/site-backups/site_20260909_152131.tgz» — бэкап создан, `.govless-owned` появился. Всё — **без переустановки**. |
| **J7** | **ЧАСТИЧНО → BUG-20** | Маркер `.govless-owned` после обновления отсутствует (подтверждено в `migration-after.txt`, до любой смены шаблона). Проверка `3 → 6 → 1` выполнена в Lite при восстановленном «постмиграционном» состоянии (маркер удалён вручную): отказа нет — сертификат и nginx-конфиг удалены, web root оставлен с предупреждением, финальное сообщение «✓ Сайт удалён» вводит в заблуждение. Детали — BUG-20. |
| **J8** | **PASS** | Сразу после обновления, без захода в `1 → 5`: `jq '[.categories[].templates[]]\|length'` = **33467** и в `/opt/govless/templates_catalog.json`, и в `/opt/govless-installer/templates_catalog.json`; `cmp` — файлы идентичны. Старых 1801 нет. Синхронизацию делает `install_phase_a.sh` («templates catalog synced to /opt/govless/templates_catalog.json»). |
| **J9** | **PASS** | Повторный запуск той же команды обновления: экрана «Обновление …» нет, версия остаётся 1.4.0, PID **не изменились** ни у одного юнита (x-ui `233283`, govlessctl `227571`, govless-bot `227578`, nginx `229463` — до и после). Идемпотентно (`logs/J9-update-again.log`). Замечание (P3): обновление кода **в пределах одной версии** (у нас `771e277 → 53cc798`) не перезапускает phase-a — демон и бот продолжают работать на старом коде до следующего повышения версии. |

---

## Наблюдения и что не проверено

**Наблюдения.**
- Память на стенде (2 ГБ) — узкое место: в штатном Pro-режиме xray держит ~35–39 МБ RSS и стабилен;
  весь всплеск до 965 МБ и OOM вызван битым Reality-конфигом (BUG-19), а не фичей сайта-маскировки.
- Проверка размера кастомного Git-репозитория выполняется **после** полного клона
  (`lib/templates_catalog.sh:575`), т.е. `torvalds/linux` реально скачивается (1809 МБ на диск) и лишь
  затем отбраковывается. На маленькой VPS это ощутимый расход диска/времени; помогло бы
  `git clone --filter=blob:none` + проверка объёма до чекаута или ограничение по `du` в процессе.
- Гейт качества страницы (`_tpl_page_ok`) отбраковал первый вариант тестового репозитория как
  `app_shell` — работает как задумано, тестовый `index.html` пришлось сделать «настоящим».
- `/etc/nginx/sites-enabled/govless` после установки Phase-A перестаёт быть симлинком (в него
  вписывается include), а `generate_nginx_pro_config` возвращает симлинк — именно эта пара и была
  механикой BUG-14; после фикса include живёт в `sites-available`, симлинк восстановлен.

**Что не проверено и почему.**
- Реальное подключение VPN-клиентом (нет устройства в прогоне) — проверялись только генерация ссылок,
  подписка (HTTP 200) и состояние inbound/портов.
- Применение шаблона **из Mini App** (кнопки «👁 Превью» / «Применить» в списке категории): экран
  открыт и осмотрен, но применение делалось из бота и из демона; в Lite Mini App стал недоступен
  (см. финальное состояние).
- J7 выполнен на искусственно восстановленном «постмиграционном» состоянии (маркер удалён вручную),
  потому что к тому моменту смена шаблона из бота уже создала `.govless-owned`.
- BUG-19 диагностирован до корня и подтверждён обходом (`mask_domain` в config.json), но
  «правильный» интерактивный выбор донора при Pro→Lite не тестировался — его в коде нет.

**Изменения на стенде, сделанные ради продолжения прогона (кода не касались):**
1. `git remote set-branches --add origin test` + `git fetch --depth 1 origin test` в
   `/opt/govless-installer` — обход BUG-16.
2. `jq '. + {mask_domain:"www.microsoft.com"}'` в `/opt/govless/config.json` (бэкап —
   `config.json.qa-bak`) — обход BUG-19, чтобы Lite-режим не убивал сервер.
3. `rm -f /var/www/html/.govless-owned` — восстановление постмиграционного состояния для J7.

**Состояние стенда на момент завершения.** Режим **lite**, Reality на :443 (`dest www.microsoft.com:443`),
xray стабилен (~38 МБ RSS), 3 клиента целы, `x-ui`/`govlessctl`/`govless-bot` активны; nginx остановлен,
сайта и LE-сертификата нет (удалены кейсом J7), файлы прежнего сайта остались в `/var/www/html`
(BUG-20). `/opt/govless-installer` на ветке `test` @ `53cc798`. Токен `/root/.gh_token` удалён —
обновление стенда из приватного репозитория больше не сработает без нового токена.

**Тестовый репозиторий.** `potap1978/go-all-qa-symlink-test` — публичный, в ветке `symlinks` лежат
фикстуры для BUG-13 (`passwd.html -> /etc/passwd`, `rootlink -> /`, `secret.txt -> /etc/shadow`,
реалистичный `index.html`). Ветку можно удалить, когда регресс больше не нужен.
