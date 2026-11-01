# Приёмка дизайна стадии 2 (`handoff/iteration3`) и связь экранов с сервером

27.09.2026. База: `release/0.4.6` @ 566830e (detached). Бриф: `docs/DESIGN_STAGE2_BRIEF.md` (DS2-01…58), `docs/MODES_047.md`.

Как проверяли: `python3 -m http.server 8768 --directory handoff`, Chromium headless-shell 1243 через Playwright 1.61. Только DOM, без снимков экрана. Анимации отключены: `*,*::before,*::after{animation:none!important;transition:none!important}` (init-скрипт и повторно после загрузки) плюс `reducedMotion: reduce`. Каждое состояние открывалось в отдельном контексте, адресом из README (`?screen=`, `?scene=`, `?view=miniapp&screen=`, `?page=`). Свойства страницы подключения, которых нет в адресе (`platform`, `keys`, `mirror`, `share`, `qr`, `copy`, `lifetime`, `reason`, `apps`, `name`, `noqr`, `tvFocus`), задавались через `__dcSetProps`. На 42 состояниях локальный `http.server` сбрасывал соединение под нагрузкой; их перепрогнали по одному, и все прошли чисто — это шум стенда. Прогонщик: `.claude/work/design3-accept/dom-run.cjs`, задания: `dom-jobs.py`.

## 1. Итог в цифрах

Всего 3 783 отрисовки: бот 960, панель 2 086, мини-приложение 596, страница подключения 141.

| Прототип | Отрисовок | Конфигурации | Ошибки JS | Внешние | Сдвиг вбок (страница) | Цели < 44 на телефоне | Кириллица при EN: текст / атрибуты | `undefined`, `{{` |
|---|---|---|---|---|---|---|---|---|
| Бот, 240 сцен (в списке 241, `mode-pick` дважды) | 960 | RU и EN на 360 px, светлая; RU и EN на 390 px, тёмная | React #185 в 959/960 | 0 | 0 | только поле ввода Telegram «Сообщение», 40 px, во всех 240 сценах | 0 вне данных людей / `alt="QR-код"` в 4 сценах | 4 сцены (`profile-install-port*`) |
| Панель, 298 состояний (у дизайнера «297») | 2 086 | телефон 360 EN светлая и тёмная, 360 RU, 390 RU; компьютер 1440 EN светлая, RU светлая и тёмная | 0 | 0 | 0 | 0 (1 192 отрисовки на телефоне) | 0 вне данных людей и демо-поиска («кузнецов», «космос») / 23 строки в атрибутах | 0 |
| Мини-приложение (298 экранов панели) | 596 | 390 px: EN ночная, RU дневная | React #185 в 298/298 EN | 0 | 0 | 0 | как у панели | 0 |
| Страница подключения | 141 | 8 страниц × RU/EN × светлая/тёмная на 360; 8 × RU/EN на 1440; 43 варианта свойств × RU/EN; ТВ, 7 фокусов | React #185 в 47/48 отрисовок с параметрами в адресе | 0 | 0 | 0 | «Р-43» на `ideas` / 0 | 0 |

Что ещё нашлось:
- **Обрезанный текст.** Жёстко обрезанного текста нет, везде многоточие. Имена людей и длинные адреса обрезаны намеренно. Обрезаны при этом и подписи самого интерфейса — D3-05, D3-06, D3-17.
- **Прокрутка вбок внутри блока.** На телефоне 360 px в 10 состояниях таблица «Адреса сервера» шириной 590 px стоит в блоке 294 px: `overview`, `overview-endpoint-gone`, `overview-alerts`, `overview-dns`, `overview-cf`, `overview-noa`, `overview-docker`, `overview-heat-partial`, `appearance`, `cmd`. На 1440 в `diag` и `diag-problems` таблица таймеров вылезает на 19 px (520 в 501).
- **Новые состояния относительно итерации 2:** в панели 285 → 298 (+13), в боте 179 → 240 (+61). Все 13 новых экранов панели отрисовывают своё содержимое, а не подставляют экран по умолчанию.

## 2. DS2-01…58: 39 пунктов — 31 сделан, 8 частично, не сделанных нет

| DS2 | Приор. | Итог | Чем подтверждено / чего не хватает |
|---|---|---|---|
| 01 | О | сделано | EN «Running: Installing profile AmneziaWG 1.5» (`profiles-installing`), «Updating the AmneziaWG 2.0 core» (`profiles-updating`), «Another action is running» (`profiles-locked`), `import-done` без кириллицы. Другие остатки русского — D3-04. |
| 02 | О | сделано | `login-refused-notoken`, `-cooldown` («через 0:25»), `-netlimit`, RU и EN. |
| 03 | Ж | сделано | `?view=miniapp&screen=login-miniapp-signature`, день и ночь, RU и EN, с формой кода. Сделано экраном панели, а не `mini=denied-signature`. Сервер при этом не различает два отказа — L-03. |
| 04 | О | частично | На телефоне есть список (Пользователь / Трафик / Срок / Рукопожатие) и кнопка направления 44×44. На компьютере сортировка только в трёх заголовках (Пользователь, Трафик, Срок): по рукопожатию нельзя, колонки нет, `aria-sort` нет. |
| 05 | О | сделано | `overview-endpoint-gone` (с пометкой «нужна доработка сервера»), `overview-alerts`, `settings-endpoint-gone` с кнопкой «Поправить адрес в ключах»; в боте `start-endpoint-gone` и `endpoint-gone`. Данные от сервера — L-01, L-02. |
| 06 | О | сделано | «Срок продлевается сам…» есть в `agents-add` у поля срока, в `agents-card` и `agents-show*`, в `agents-card-expired`. «Упёрся в предел частоты» ушло в идеи — L-13. |
| 07 | Ж | частично | AAAA есть в `settings-sitecheck-partial` и `site-check-aaaa`. На шаге домена мастера (`settings-mode-wizard*`) нет. |
| 08 | Ж | частично | В боте `upstream-rollback-floating` сделан. В панели варианта `updates-rollback` нет. |
| 09 | Ж | сделано | `logs` → «Журнал службы транспорта агентов… Только в SSH-меню». |
| 10 | Ж | сделано | В `user-share`: «QR-картинки и архив со всеми форматами — в SSH-меню». |
| 11 | О | сделано | «Срок вышел 20.09.2026» (`user-expired`, `user-expiry-past`), EN «Expired 20.09.2026». На 360 px не обрезается. |
| 12 | Ж | сделано | `settings-password-chars` (`bad_password`), `settings-password-write` (`password_write_failed`). |
| 13 | Ж | частично | Даты совпадают («до 01.11.2026 · ещё 36 дн.» и «… до 01.11.2026»), но на 360 px дата в поле обрезана — D3-05. |
| 20 | О | сделано | EN: кириллица только в данных людей; непереведённый `alt="QR-код"` — D3-04. |
| 21 | О | сделано | `busy` (без имени и длительности чужого действия, «Назад»), `queued`, `busy-lock`. |
| 22 | Ж | сделано | `claim-used`. |
| 23 | Ж | сделано | `cmd-adduser`, `-bad`, `-busy`, `cmd-key`, `-none`, `cmd-invite`, `-noenv`, `-bad`, `-owner`. |
| 24 | О | сделано | `attach-key-mismatch`, `attach-unreadable`, `attach-has-key`. |
| 25 | Ж | частично | Подтверждение и 3 отказа нарисованы. Запрос «⌨️ Отправьте UDP-порт…» не выводится: вместо него «🔌 Порт профиля undefined» — D3-02. |
| 26 | Ж | сделано | `import-done`, `import-in-sync`. |
| 27 | О | сделано | `mode-le-dns`, `-net`, `-limit` («Домен и режим остались прежними»), `mode-fallback`, «✅ Оставить vpn.example.ru», `site-check-a`, `-foreign`, `-https`, `-cert`, `-aaaa`. |
| 28 | Ж | сделано | «📍 IP сервера» и «↩️ По умолчанию»; `endpoint-bad`, `endpoint-noip`, `endpoint-gone`. |
| 29 | О | частично | (а) `agents-port` и (б) `agent-url-rolling`, `agent-url-connect` сделаны; (г) `agent-enroll-wait`, `-expired` сделаны. (в) Не работает: `agent-rotate-connect` показывает общий текст, `agent-toggle-on` — «⏸ Выключить» (D3-03). |
| 30 | Ж | сделано | `mirror-platform`, `mirror-queued`, `mirror-delete`. |
| 31 | О | сделано | «0.4.5» нигде нет; везде 0.4.6. |
| 32 | Ж | сделано | «(всего мест: 254)»; `claim-done` без «!». Остатки — D3-15. |
| 33 | Ж | сделано | В `commands` — строка про личный чат. |
| 34 | Ж | сделано | `start-endpoint-gone` с кнопкой «📍 Адрес в ключах». |
| 40 | О | сделано | На 360: RU/EN 48×44, вкладки 44 px; целей меньше 44 нет. |
| 41 | О | сделано | «MikroTik (container)», «OPNsense (AmneziaWG plugin)». |
| 50 | О | сделано | Бот выровнен по продукту — см. DS2-21. |
| 51 | О | сделано | Версия 0.4.6 и в боте, и в панели. |
| 52 | О | сделано | «Только чтение (read) / Чтение и запись (write) / Полный (admin)», «Запросов всего», «Привязка к адресу». В форме `agents-add` подписи без «(write)»/«(admin)». |
| 53 | Ж | сделано | `ntf-agents-2` называет панель и SSH; новую кнопку бота — нет (D3-18). |
| 54 | Ж | частично | Правило принято, но подписи пояса в панели стоят не везде (дизайнер это признаёт). |
| 55 | Ж | частично | В 33 сценах бота голые id: `state-status`, `state-addr`, `profile-443`, `-mtu`, `-subnet`, `-preset`, `-repair`, `-restart`, `-port`, `-param(s)`, `-regen`, `-remove`, `restart-all`, `upstream-update`, `-rollback`, `-repair`, `-fail`, `-unreachable`, `attach*`, карточки ключей. |
| 56 | Ж | сделано | Решение: оставить разным, как в продукте. |
| 57 | реш. | сделано | В боте `user-pin`, `agent-extend`, `pages-revoke-all`; в панели «Перезапустить все профили». |
| 58 | реш. | сделано | «Активность по часам» видна при подробности min, balanced и detailed. |

0.4.7, вне нумерации DS2:
- Работает: `claim-request`, `-confirmed`, `-not-set`; `add-name-random`, `-2`. Кнопка 🎲 в панели (`user-add`, 157×44) заполняет id и подпись, повторное нажатие даёт другое имя.
- Нарисованы замороженные функции — D3-07, F-1…F-4.

## 3. Дефекты

- **D3-01 (высокая). React #185 «Maximum update depth exceeded».**
  - Где: бот 959/960, мини-приложение 298/298 EN, страница подключения 47/48; в панели не встречается.
  - Причина: `componentDidUpdate(pp)` сравнивает «сырые» `pp` с `this.P()`, где уже учтены параметры адреса (`Telegram.dc.html:424-428`, `Client.dc.html:338-342`) — равенства не бывает.
  - Последствие: прототип не нажимается. `?scene=start&lang=en` → «👥 Users» не открывается; `Client ?page=connect&lang=en` → «Android» не выбирается.
  - Исправить: сравнивать с `{...pp, ...this._url}` или один раз перенести параметры адреса в `state`.
- **D3-02 (высокая).** Дубль `prPortAsk` (`Telegram.dc.html:937` и `:951`) → в `profile-install-port`, `-busy`, `-subnet`, `-amnezia` «🔌 Порт профиля undefined / Сменить порт профиля undefined на undefined?». Исправить: переименовать новый ключ.
- **D3-03 (высокая).** Дубли `agentRotate` (776/806) и `agentToggle` (777/810): старые определения затирают новые. `agent-rotate-connect` показывает общий текст перевыпуска, `agent-toggle-on` — «⏸ Выключить / Turn off access a-0b52?». Исправить: удалить строки 806 и 810.
- **D3-04 (средняя). Русский в атрибутах при EN.**
  - 8 видимых плейсхолдеров: «по умолчанию» (`profiles-install*`), «бессрочно» (`user-expiry*`), «без лимита» (`user-quota`), «пусто — убрать параметр» (`profiles-param*`), «vpn://… или [Interface] …» (`user-attach*`), «Человек, раздел или действие» (`cmd`), «12h или 3d» (`user-page-custom`), «например, 5184» (`profiles-install-errors`).
  - 15 подсказок, aria-label и alt: «Поиск ⌘K» (130 экранов на компьютере), «N ГБ» (`overview*`), «день N · X ГБ» (`traffic*`), «Порт», «Что положить в файл», «QR-код ключа» (41), «QR-код адреса страницы», 8 образцов вида («Синий HeroUI», «Небо», «Лаванда», «Мята», «Коралл», «Графит», «Свой», «Случайная тема»).
  - В боте: `alt="QR-код"` в `key-qr`, `key-qrvpn`, `page-ready`, `page-ready-imported`.
- **D3-05 (средняя).** `user-expiry` и `user-expiry-past` на 360 px: «до 01.11.2026» (104 px в 89) и «until 01.11.2026» (115 в 89) обрезаются.
- **D3-06 (средняя).** «Сессия: 12 мин» (100 px в 97) и «Session: 12 min» (98 в 87) обрезаны на 1440 во всех 143 экранах Panel-settings.
- **D3-07 (средняя, заморозка).** В 39 экранах `settings-mode*`, `settings-domain*`, `settings-altsvc` показаны 4 режима с «Ленивым Pro» и у Lite написано «Доверенный сертификат Let’s Encrypt на IP (~6 дней, продлевается сам)». Названия Semi-Pro и Lite стоят в 54 состояниях панели и 23 сценах бота. В продукте режимы называются «Только VPN / VPN и сайт / Сайт и VPN на 443», сертификата на IP нет. Держать вариант с названиями и возможностями продукта, пока владелец не снимет заморозку.
- **D3-08 (низкая).** DS2-55: голые awg1/2/3 в 33 сценах бота.
- **D3-09 (низкая).** Нет варианта «плавающего» тега в `updates-rollback` панели (DS2-08); данные есть — L-19.
- **D3-10 (низкая).** Нет AAAA на шаге домена в `settings-mode-wizard*` (DS2-07); данных для нового домена тоже нет — L-09.
- **D3-11 (низкая).** `users` и `users-many` на компьютере: нет сортировки по рукопожатию, нет `aria-sort`.
- **D3-12 (низкая).** `diag` и `diag-problems`: переполнение на 19 px при 1440; 10 состояний `overview*` листаются вбок на 360 px.
- **D3-13 (низкая).** Поле ввода Telegram 40 px во всех 240 сценах.
- **D3-14 (низкая).** `mode-pick` дважды в списке сцен и в `qa/tg-scenes.json` (`Telegram.dc.html:294` и `:349`); мёртвый дубль `restart15` в `Panel-core.dc.html:2188` (срабатывает `:2232`).
- **D3-15 (низкая).** В сцене `claim` — «👋 Привет, Виталий!» (так же в продуктовом `waiting_admin_title`). В продукте `admin_claimed_fmt` всё ещё с «!» (и на `bot-047`): продуктовая часть DS2-32 не сделана.
- **D3-16 (низкая).** `Client ideas` при EN: «Р-43» с кириллической «Р».
- **D3-17 (низкая).** `settings-template` и `-search`: строка «категория · лицензия» обрезается (275 px в 187 на компьютере, 231 в 136 на телефоне).
- **D3-18 (низкая).** `ntf-agents-2` не называет новую кнопку бота «📅 Продлить»; в `agent-extend` только 30 и 90 дней, без «бессрочно» (в панели он есть: `agents-extend-forever`).

Где README и сообщение дизайнера расходятся с проверкой:
- «0 ошибок JS» — неверно (D3-01).
- «241 сцена» → 240; «297 состояний» → 298.
- DS2-55 сделан лишь частично.
- Коды входа в «нужно от сервера» записаны зря — L-15, L-16.

## 4. Связь с сервером: 27 пунктов

Всего 14 требуют работы (2 из них идеи), 9 уже есть, 4 заморожены. Проверено по `admin-web/server.py`, `lib/actions.json` (90 действий), `install.sh`, `lib/*.sh`, `goamnezia-bot/bot.py` и `lang/*.json`, `lib/pages_server.py`, а также по веткам `bot-047` и `names-047` (в `release/0.4.6` не влиты).

### 4.1. Нужна работа (не заморожено)

| ID | Экран / состояние | Что нужно | Есть сейчас? | Куда добавить |
|---|---|---|---|---|
| L-01 | `overview-endpoint-gone`, `overview-alerts`, `settings-endpoint-gone` | `endpoint_host_gone` | Частично: мост отдаёт в `status` (`install.sh:8686-8695`); `overview_payload` (`server.py:2005-2027`) и `public_config` (`:244-263`) — нет | Одна строка в `overview_payload` и данные «Настроек» |
| L-02 | `start-endpoint-gone`, `endpoint-gone` | тот же признак в боте | Нет: `bot.py` его не читает | `bot.py` + `lang/*.json` |
| L-03 | `login-miniapp-signature` и `mini=denied` | различать «подпись» / «не администратор» / «устарела» / «нет администраторов или токена» | Нет: `verify_telegram_init_data` (`server.py:425-465`) → везде один `signature_invalid` (`:3785`) | Причины в ответе 401 |
| L-04 | 🎲 `user-add`, `add-name-random`, `-2` | `name-suggest` (lang, exclude → id, label) | Нет ни на одной ветке; `bot-047` уже вызывает; данные — `names-047` | Ядро C47-1 (`actions.json`, `install.sh`, `lib/users.sh`). Панели правки не нужны: общий `/api/action/name-suggest` |
| L-05 | `claim-request`, `-confirmed`, `-not-set`, путь в SSH-меню | привязка сообщением: окно, SSH, `ALLOWED_IDS`, остаток окна, пункт меню | Нет в `release/0.4.6`; сторона бота на `bot-047`; SSH (C47-1) нет. Дизайн показывает вопрос SSH и время подтверждения 14:53, в текстах `bot-047` их нет | `install.sh` и слияние `bot-047` |
| L-06 | `overview-alerts` «IP изменился» | было, стало, когда | Частично: `lib/amnezia.sh:864-898` шлёт только боту | Сохранять → `status` → `overview_payload` |
| L-07 | `overview-alerts` «обновление», `updates-badge` | итог суточной проверки | Частично: `upstream_last_check()` (`lib/upstream.sh:982`) нигде не вызывается | Поле в `status` или `upstream-check cached=on` |
| L-08 | `agents-card-freeze-failed` | «заморозить не удалось» | Нет в `agents_list` (`lib/agents.sh:672-695`) | Сохранять и отдавать в `agents_list` |
| L-09 | шаг домена в мастере (`settings-mode-wizard-*`, AAAA) | проверка A и AAAA нового домена до перехода | Нет: `site-check` проверяет только текущий домен | Новое действие только на чтение или `site-check domain=` |
| L-10 | `user-pin`, `agent-extend`, `pages-revoke-all` | `user-set-address`, `agent-extend`, `page-revoke-all` | Мост есть, обработчиков в `bot.py` нет | `bot.py` + `lang` |
| L-11 | тексты бота | строка про личный чат в `/help`; `admin_claimed_fmt` без «!»; линии вместо id (`pr_restarting_fmt` и др.) | Нет | `lang/*.json`, `bot.py` |
| L-12 | `profiles-restart-all` | перезапуск всех профилей | Отдельного действия нет; бот перезапускает по одному (`bot.py:5867-5886`) | Цикл во фронтенде |
| L-13 | идея DS2-06 «предел частоты» | признак у доступа | Нет, только уведомление бота | Поле в `agents_list` (лист идей) |
| L-14 | `Client ideas`: CP-5, Р-43 | QR собственного адреса страницы; вид из панели на сервере | Нет | `pages_server.py` и admin-web (лист идей) |

### 4.2. Уже есть: сервер не трогаем, нужна только новая вёрстка

| ID | Экран | Данные и где |
|---|---|---|
| L-15 | `login-refused-notoken`, `-cooldown`, `-netlimit`, `-often`, `-nobot` | `reason` `no_token`, `cooldown` + `seconds`, `net_limit`, `hour_limit`, `no_admins` с текстами ru и en — `server.py:822-844`, `/api/auth/request` |
| L-16 | `login-miniapp-signature` | `signature_invalid` — `server.py:3785` (см. L-03) |
| L-17 | сортировка `users` | все люди сразу с `handshake_ts` — `users_payload` |
| L-18 | `settings-sitecheck-partial`, `site-check-aaaa` | `dns_aaaa_foreign` — `site-check` |
| L-19 | `upstream-rollback-floating`, `updates-rollback` | `previous_digest` — `upstream-check` / `upstream_payload` |
| L-20 | `attach-*` | `key_mismatch`, `config_unreadable`, `already_has_key` — `user-attach-key` |
| L-21 | `agents-port`, `agent-url-connect`, `agent-enroll-*` | `port`, `mode=connect`, `enroll_pending`, `enroll_expired`, `enroll_expires` — `agent-add`, `agents_list` |
| L-22 | `settings-password-chars`, `-write` | `bad_password`, `password_write_failed` — `server.py:1060-1098` |
| L-23 | `settings-mode`, `mode-pick`, `mode-pick-busy` (3 режима) | `preflight` `{lite, lite_site, pro}.available` и причины (`lib/preflight.sh:783-786`) |

### 4.3. Заморожено

| ID | Экраны | Что нужно | Есть сейчас? |
|---|---|---|---|
| F-1 | Ленивый Pro: `mode-lazy`, строка в 39 экранах `settings-mode*`, `mode-pick` | `install.sh --lazy` | нет |
| F-2 | Semi-Pro / Lite / Pro: 54 состояния панели, 23 сцены бота | новые названия режимов | нет: в продукте «Только VPN / VPN и сайт / Сайт и VPN на 443» |
| F-3 | `mode-pro-line`, «2.0 на 443» | выбор линии на UDP 443 | Можно в два шага: `change-mode pro` + `profile-port <профиль> 443` (`install.sh:9310-9323`); одного действия нет |
| F-4 | `settings-mode-lite-cert*`, `mode-lite-cert*`, `ntf-ipcert`, описание Lite | сертификат на IP: состояние, таймер, перевыпуск, событие бота | нет (`cert-tick` — только сертификат сайта) |

### 4.4. Обратный разрыв: в продукте есть, в дизайне не нарисовано
- `bot-047`, 🎲: `user_name_random_unavailable`, `user_name_random_failed_fmt`, `user_name_random_gone`, `user_name_already_chosen`.
- `bot-047`, привязка: `admin_claim_need_code`, `admin_pairing_code_hint`.

## 5. Как перепроверить
```sh
python3 -m http.server 8768 --bind 127.0.0.1 --directory handoff
python3 .claude/work/design3-accept/dom-jobs.py
node .claude/work/design3-accept/dom-run.cjs <jobs.json> <out.jsonl> 8
```
Сброс соединения локальным `http.server` — шум стенда: такие состояния перепрогоните с параллельностью 1.
