# Покрытие дизайна, итерация 2: каждая функция каждого компонента

27.09.2026 · продукт: `release/0.4.6` @ 733487c (кандидат 0.4.6) · дизайн: `handoff/iteration2/` после сведения Codex 273423c
(`Panel.dc.html` + `Panel-core.dc.html` + `Panel-settings.dc.html`, `Telegram.dc.html`, `Client.dc.html`).

**Что сверялось.** Опись функций собрана заново из кода, а не из документов 26.09: все 90 действий моста
(`lib/actions.json`), все пункты SSH-меню и мастера (`install.sh`), все команды, кнопки (`EXACT_HANDLERS` и
`PREFIX_HANDLERS`), 17 видов ввода текстом, 10 фраз подтверждения и 27 видов уведомлений бота (`goamnezia-bot/bot.py`),
все маршруты панели (`admin-web/server.py`) и экраны нынешней панели (`admin-web/static/app.js`), 66 инструментов,
6 ресурсов, 3 подсказки и возможности доступа MCP (`goamnezia-mcp/server.py`), маршруты и состояния страницы
подключения (`lib/pages_server.py`) и временной ссылки (`lib/share_server.py`). Вошли добавки 0.4.5–0.4.6: подхват
контейнеров приложения Amnezia (первый экран мастера, импорт, сверка, снимок), «Привязать старый ключ» (секрет через
stdin), пометки «ключа нет на сервере», почасовая статистика (`traffic.hours`, карта 7×24, «24 часа»), коды и
английские тексты отказов входа (B4), первый админ бота по одноразовому коду, только личный чат, защита от повторного
нажатия, привязка MCP к адресу, одноразовые ключи и уровни, Apple TV на странице подключения.

**Как проверялось (без снимков экрана).** Прототипы открыты по http в безголовом Chromium. Каждое именованное
состояние отрисовано и снят его видимый текст: панель — 285 состояний × 3 варианта (компьютер RU светлая,
телефон 360×800 EN тёмная, компьютер EN) = 855 отрисовок; бот — 179 сцен × RU/EN; мини-приложение — 16 состояний × RU/EN ×
дневная/ночная схема Telegram; страница подключения и ссылки — 98 вариантов свойств. Во всех отрисовках: ошибок JS 0,
внешних запросов 0, пустых экранов 0, сдвига страницы вбок 0. По тексту состояний искалась каждая функция; спорное
перепроверялось точечной загрузкой состояния (`?screen=…&lang=…&device=…`). На телефоне меряются все цели касания
(кнопки, ссылки, вкладки, радио, поля), а не только `button`, как в `qa/check-panels.cjs`.

**Обозначения.** ✅ покрыто — нарисовано с основными полями, кнопками и состояниями · ◐ частично — есть, но не хватает
названного · ✗ нет · — без интерфейса по замыслу (таймер, служебный маршрут, интерфейс агента, или «только SSH», и дизайн
это прямо говорит). Где в дизайне: `P:` — `Panel.dc.html?screen=…`, `T:` — `Telegram.dc.html` сцена бота, `M:` — там же
`view=miniapp`, `mini=…`, `C:` — `Client.dc.html` (`page=…` и свойства). Знак `*` — группа состояний с этим началом.

## Итог

| Компонент | Всего | ✅ покрыто | ◐ частично | ✗ нет | — без интерфейса по замыслу |
|---|---|---|---|---|---|
| Панель | 45 | 40 | 4 | 0 | 1 |
| Мост | 90 | 83 | 0 | 0 | 7 |
| SSH | 82 | 67 | 3 | 1 | 11 |
| Бот | 92 | 75 | 15 | 1 | 1 |
| MCP | 19 | 13 | 2 | 0 | 4 |
| MCP-инстр. | 66 | 0 | 0 | 0 | 66 |
| Страницы | 17 | 14 | 1 | 0 | 2 |
| Ссылки | 7 | 5 | 0 | 0 | 2 |
| **Всего** | **418** | **297** | **25** | **2** | **94** |

Из 324 строк с интерфейсом (без «—»): покрыто 297 (92 %), частично 25 (8 %), нет 2 (1 %).

**Сравнение с 26.09 на той же базе.** 26.09 считались 164 функции §2 `docs/archive/DESIGN_COVERAGE.md` (156 оцениваемых).
Новая опись шире (в ней каждый компонент отдельно, в т.ч. 66 инструментов MCP и 7 таймеров), поэтому для честного
сравнения те же 164 строки переоценены по итерации 2 (приложение А):

| Счёт по тем же функциям §2 от 26.09 | Всего | ✅ | ◐ | ✗ | н/п |
|---|---|---|---|---|---|
| 26.09 (итерация 1) | 164 (оцениваются 156) | 25 (16 %) | 45 (29 %) | 86 (55 %) | 8 |
| 27.09 (итерация 2 + 273423c) | 164 (оцениваются 156) | 152 (97 %) | 4 (3 %) | 0 (0 %) | 8 |

**Главное.** Дизайн итерации 2 закрыл почти всю опись: не нарисованы только две функции продукта: предупреждение SSH-шапки «адреса из ключей нет на сервере»
(`endpoint_host_gone`) и ответ бота «окно назначения администратора уже использовано». Остаток — детали: английский бот,
цели касания на странице подключения, несколько исходов и отказов в боте, расхождение «занято / в очереди» с продуктом,
три отказа запроса кода входа, пояснение про само-продление срока доступа агента в панели. Полный список — в
`design-stage2-brief.md`.


## 1. Веб-панель: маршруты API и экраны (`admin-web/server.py`, `admin-web/static/app.js`)

| Компонент | № | Функция | Источник | Где в дизайне | Статус |
|---|---|---|---|---|---|
| Панель | P1 | Сессия: состояние, секунды, «как вошли», пароль задан, бот настроен | `admin-web/server.py:3593 GET /api/session` | P: подвал меню «Сессия: 12 мин», «Вход: код из бота», «Выйти»; sys-session-renew, sys-session-renew-sent, sys-session-over, sys-session-over-nobot, sys-tunnel | ✅ |
| Панель | P2 | Обзор (статус, трафик, адреса, службы, как открыть панель) | `server.py:3615 GET /api/overview` | P: overview, overview-alerts, -fresh, -single, -dns, -cf, -noa, -docker, -loading, -heat-partial | ✅ |
| Панель | P3 | Список пользователей | `server.py:3618 GET /api/users` | P: users, users-loading, -empty, -notfound, -many (25/50/100), -corrupt, -timeout | ◐ нет сортировки по имени/трафику/сроку/рукопожатию (задание D итерации 2) |
| Панель | P4 | Карточка пользователя | `server.py:3621 GET /api/users/<имя>` | P: user и 60 состояний user-* | ✅ |
| Панель | P5 | Ключ: all/conf/vpn/qr, разовый адрес ?endpoint= | `server.py:3634 GET /api/users/<имя>/key` | P: user, user-vpn, user-text, user-addr, user-addr-domain, user-single-addr | ✅ |
| Панель | P6 | Скачать .conf (имя файла с хостом при разовом адресе) | `server.py:3646 GET /api/users/<имя>/conf` | P: user — «Скачать .conf», «Адрес в ключе: …» | ✅ имя файла <имя>-<профиль>-<host>.conf нигде не показано — не обязательно |
| Панель | P7 | Профили | `server.py:3669 GET /api/profiles` | P: profiles, profiles-loading, -notinstalled, -fresh, -states, -docker, -updating, -locked | ✅ |
| Панель | P8 | Параметры маскировки профиля | `server.py:3672 GET /api/profiles/<p>/params` | P: profiles-params | ✅ |
| Панель | P9 | Трафик по суткам и по часам (hours, HM-1) | `server.py:3680 GET /api/traffic; server.py:2346 hours` | P: traffic, traffic-24, traffic-7, traffic-empty, traffic-24-partial; тепловая карта — overview (density=detailed), overview-heat-partial | ✅ карта 7×24 видна только при «Подробность: детально» |
| Панель | P10 | Бэкапы | `server.py:3683 GET /api/backups` | P: backups и 13 состояний backups-* | ✅ |
| Панель | P11 | Размер логов и предел | `server.py:3686 GET /api/logs/status` | P: logs, logs-follow — карточка «Место под логи» | ✅ |
| Панель | P12 | Логи по источнику и числу строк | `server.py:3691 GET /api/logs` | P: logs-loading, logs-shown, logs-awg, logs-docker, logs-nounit, logs-empty, logs-audit* | ✅ «Следить» (опрос раз в 5 с) и фильтр «Ошибки/Важное» — новое поведение клиента, в продукте нет |
| Панель | P13 | Проверка портов 80/443 | `server.py:3695 GET /api/preflight` | P: settings-ports-checking, карточки режимов в settings-mode | ✅ |
| Панель | P14 | Временные ссылки (без секрета) | `server.py:3698 GET /api/share` | P: users-shares, users-shares-empty, users-shares-revoke | ✅ |
| Панель | P15 | Страницы подключения (без адресов) | `server.py:3703 GET /api/pages` | P: user — блок «Страницы подключения», user-page-url | ✅ |
| Панель | P16 | Копии приложений | `server.py:3712 GET /api/mirror` | P: settings-mirror, -empty, -fetch, -remove | ✅ |
| Панель | P17 | Каталог приложений | `server.py:3715 GET /api/clients` | P: settings-mirror-fetch (выбор приложения) | ✅ |
| Панель | P18 | Настройки | `server.py:3718 GET /api/settings` | P: settings-* (Вид · Режим и сайт · Адреса · Копии · Вход в панель · Язык · О программе) | ✅ |
| Панель | P19 | Диагностика | `server.py:3721 GET /api/quality` | P: diag, diag-problems, diag-running | ✅ |
| Панель | P20 | Агенты и транспорт | `server.py:3724 GET /api/agents` | P: agents и 38 состояний agents-* | ✅ |
| Панель | P21 | Обновления Amnezia | `server.py:3727 GET /api/upstream` | P: updates-checking, updates, -unreachable, -badge | ✅ значок «есть обновление» помечен «нужна доработка сервера» |
| Панель | P22 | Импорт контейнеров приложения Amnezia | `server.py:3730 GET /api/import` | P: import, import-scanning, -empty, -noconf | ✅ |
| Панель | P23 | Список действий моста для фронтенда | `server.py:3733 GET /api/actions` | — | — служебный маршрут, экрана не требует |
| Панель | P24 | Каталог шаблонов: категория, поиск, страницы | `server.py:3736 GET /api/site/templates` | P: settings-template, -loading, -search, -empty | ✅ |
| Панель | P25 | Текущий шаблон сайта | `server.py:3748 GET /api/site/template` | P: settings-template (строка «сейчас») | ✅ |
| Панель | P26 | Запрос кода входа и отказы (B4: code + texts ru/en) | `server.py:3762 POST /api/auth/request; коды tfa_off, too_often, chat_not_started, send_failed` | P: login, login-sending, login-sent, login-refused-nobot, -start, -telegram, -often | ◐ нет трёх вариантов отказа: «у бота не задан BOT_TOKEN» (code_off_no_token), «код уже отправлен — повторите через N с» (code_cooldown), «много запросов из интернета — войдите паролем или по туннелю» (code_net_limit) |
| Панель | P27 | Проверка кода | `server.py:3801 POST /api/auth/verify; code_wrong, code_expired, code_too_many_attempts` | P: login-code-wrong, login-code-expired, login-code-toomany, login-ok | ✅ |
| Панель | P28 | Вход по паролю | `server.py:3819 POST /api/auth/password; bad_credentials, too_many_attempts, password_unset` | P: login-pwd, login-pwd-wrong, -locked, -unset; login-v2, -v2-howto, -v3, -v4 | ✅ |
| Панель | P29 | Смена пароля панели | `server.py:3822 POST /api/auth/password/change; bad_credentials, bad_password, password_write_failed` | P: settings-password, -errors, -wrong, -locked, -unset | ✅ нет «недопустимые символы» и «не удалось записать файл» (pwd_bad_chars, pwd_write_failed) — мелочь |
| Панель | P30 | Вход мини-приложения по подписи Telegram | `server.py:3776 POST /api/auth/miniapp; signature_invalid` | M: entry, denied, denied-code; P: login-miniapp | ◐ нет отказа «подпись Telegram не прошла проверку» (signature_invalid) — что видит человек и что делать |
| Панель | P31 | Выйти | `server.py:3825 POST /api/auth/logout` | P: подвал меню «Выйти»; sys-tunnel — без «Выйти» | ✅ |
| Панель | P32 | Любое действие моста + ошибки 400/404/409 lock_timeout/501/502/503/504 | `server.py:3831 POST /api/action/<имя>; server.py:1651 _busy_answer (busy_action, busy_seconds)` | P: sys-busy, sys-timeout, sys-error-toast, sys-error-dialog, sys-longop; действия — раздел «Мост» | ✅ |
| Панель | P33 | Аварийный токен ?token= и билет ?s= | `server.py:3426` | P: login-v4 (строка «Вход по аварийному токену») | ✅ билет ?s= — служебный, экрана не требует |
| Панель | P34 | Серверная страница входа на домене в теме панели | `server.py:3071 (страница входа)` | P: login (светлая/тёмная) | ✅ |
| Панель | P35 | Плашка долгого действия: название словами и таймер | `admin-web/static/app.js #progress` | P: sys-longop, profiles-installing, profiles-updating, backups-restoring, settings-mode-wizard-run, settings-template-installing | ✅ в EN название действия осталось по-русски в profiles-installing и profiles-updating |
| Панель | P36 | Кнопка «Обновить» и автообновление | `app.js #refreshBtn, #autoRefreshToggle, #lastRefresh` | P: шапка — кнопка «Обновить» (значок) | ✅ переключателя автообновления и «обновлено в HH:MM» в шапке нет — поведение, не экран |
| Панель | P37 | Язык панели RU/EN (в браузере) и язык продукта | `app.js #languageSelect; lang-set` | P: шапка RU/EN; settings-lang, settings-lang-confirm | ✅ |
| Панель | P38 | Вид: тема, акцент, плотность; хранение на сервере | `handoff README §3 (новое)` | P: appearance, settings (Вид) | ✅ применение темы к страницам подключения вынесено на лист идей (C: ideas) |
| Панель | P39 | Первый запуск: онбординг | `app.js #ovOnboarding` | P: overview-fresh | ✅ |
| Панель | P40 | Службы: бот, панель, сайт, Docker, агенты, страницы | `server.py services (systemctl is-active)` | P: overview — «Службы» | ✅ |
| Панель | P41 | Как открыть панель: команда туннеля и адрес app-… | `app.js #ovTunnel, #ovPanelUrl` | P: overview — «Как открыть панель»; settings-access | ✅ |
| Панель | P42 | Загрузка разделов (скелеты) | `app.js #pageLoader` | P: overview-loading, users-loading, profiles-loading, settings-loading, logs-loading, backups-loading, agents-loading | ✅ |
| Панель | P43 | Подтверждение вводом имени объекта | `server.py:1345 confirm = первый параметр` | P: confirm, profiles-remove, profiles-regen, backups-restore-typed, agents-delete | ✅ |
| Панель | P44 | Телефон 360 px: цели ≥ 44 px, поля 16 px, без сдвига вбок | `handoff README §7` | P: все 285 состояний device=phone | ✅ проверено DOM: вылетов нет, поля 16 px; единственные «мелкие» цели — поля поиска 20 px внутри подписи 294×44 (засчитано) |
| Панель | P45 | Английская версия панели | `admin-web/server.py panel_strings en; app.js en` | P: все состояния lang=en | ◐ русский остался в 4 состояниях: profiles-installing и profiles-updating (название действия на плашке), profiles-locked (текст «занято»), import-done (итог подхвата) |


## 2. Действия моста (`lib/actions.json`, 90) — где каждое действие видно в новом дизайне

| Компонент | № | Функция | Источник | Где в дизайне | Статус |
|---|---|---|---|---|---|
| Мост | status | Сводка сервера | `lib/actions.json: status` | P: overview; T: start, state-status | ✅ |
| Мост | profiles-status | Профили: контейнер, порт, линейка, состояние, версия, пиры | `actions.json: profiles-status` | P: profiles, profiles-states, profiles-docker; T: profiles, profile, profile-missing, profile-docker | ✅ |
| Мост | quality | Диагностика | `actions.json: quality` | P: diag, diag-problems, diag-running; T: diag | ✅ |
| Мост | traffic | Трафик и рукопожатия (дни + часы HM-1) | `actions.json: traffic; lib/stats.sh:19 hours` | P: traffic*, overview (карта 7×24), overview-heat-partial; T: state-traffic | ✅ |
| Мост | edge-status | Адреса сервера: пакеты, A-записи, вердикты сторожа, выбор адреса | `actions.json: edge-status` | P: overview (таблица), overview-single, -dns, -cf, -noa, user-addr; T: state-addr, key-addr | ✅ |
| Мост | logs-tail | Логи компонента и журнал действий | `actions.json: logs-tail` | P: logs-*, logs-audit, logs-audit-agent; T: state-logs, state-log, state-log-awg | ✅ логи службы HTTP-транспорта MCP (goamnezia-mcp-http) — только SSH, в дизайне не сказано |
| Мост | logs-enforce | Подрезать логи сейчас | `actions.json: logs-enforce` | P: logs-trim | ✅ в боте нет (и в продукте бота нет) |
| Мост | logs-status | Размеры логов и предел | `actions.json: logs-status` | P: logs — «Место под логи» | ✅ |
| Мост | profile-install | Установить профиль: порт, пресет, подсеть | `actions.json: profile-install` | P: profiles-install, -errors, -443, -443-free, -foreign, -installing, -installed; T: profile-new, profile-install-pick, profile-install, install, install-done | ✅ в боте нет сцены ввода «своего порта» и отказов установки |
| Мост | profile-remove | Удалить профиль | `actions.json: profile-remove` | P: profiles-remove; T: profile-remove (REMOVE awg1) | ✅ |
| Мост | profile-restart | Перезапустить профиль | `actions.json: profile-restart` | P: profiles-restart; T: profile-restart | ✅ |
| Мост | profile-port | Сменить порт / занять UDP/443 | `actions.json: profile-port` | P: profiles-port, profiles-443, profiles-443-pro; T: profile-port, profile-443 | ✅ |
| Мост | profile-params | Показать параметры маскировки | `actions.json: profile-params` | P: profiles-params; T: profile-params | ✅ |
| Мост | profile-regen | Пресет и перегенерация | `actions.json: profile-regen` | P: profiles-regen; T: profile-preset, profile-regen (REGEN) | ✅ |
| Мост | profile-set-param | Изменить один параметр | `actions.json: profile-set-param` | P: profiles-param, -param-client, -param-error; T: profile-param | ✅ |
| Мост | profile-subnet | Сменить подсеть | `actions.json: profile-subnet` | P: profiles-subnet; T: profile-subnet | ✅ |
| Мост | profile-mtu | MTU клиентских конфигов | `actions.json: profile-mtu` | P: profiles-mtu; T: profile-mtu | ✅ |
| Мост | endpoint-set | Адрес сервера в ключах | `actions.json: endpoint-set` | P: settings-clients, settings-endpoint-nodns, -other, -notip; T: endpoint | ✅ в боте нет кнопок «📍 IP сервера» и «↩️ По умолчанию» (bot.py:6991, 7008) |
| Мост | user-add | Создать пользователя | `actions.json: user-add` | P: user-add, -errors, -noprofiles, -busy, -locked, user-created*; T: add-name…add-done, add-error | ✅ |
| Мост | user-del | Удалить пользователя | `actions.json: user-del` | P: confirm, user-delete-nobackup; T: delete, delete-done, -wrong, -expired, -cancel | ✅ |
| Мост | user-list | Список пользователей | `actions.json: user-list` | P: users*; T: users, users-2, users-empty | ✅ |
| Мост | user-show | Карточка пользователя | `actions.json: user-show` | P: user*; T: user, user-lim, user-off, user-imported, user-exp | ✅ |
| Мост | user-key | Ключ: .conf, vpn://, QR, разовый адрес | `actions.json: user-key` | P: user, user-vpn, user-text, user-addr; T: key-qr, key-file, key-vpn, key-qrvpn, key-disabled, key-addr* | ✅ |
| Мост | user-rename | Переименовать (подпись) | `actions.json: user-rename` | P: user-rename; T: rename, rename-expired | ✅ |
| Мост | user-enable | Включить | `actions.json: user-enable` | P: user-disabled, user-enable-limit; T: user-off, user-enable-warn | ✅ |
| Мост | user-disable | Выключить | `actions.json: user-disable` | P: user (Выключить); T: user (⏸ Выключить) | ✅ |
| Мост | user-quota | Лимит трафика | `actions.json: user-quota` | P: user-quota; T: quota, quota-done | ✅ |
| Мост | user-expiry | Срок доступа (дни от сегодня) | `actions.json: user-expiry` | P: user-expiry, user-expiry-past; T: expiry, expiry-done | ✅ |
| Мост | user-set-address | Свой адрес сервера за человеком | `actions.json: user-set-address` | P: user-pin, user-pin-gone, user-pin-domain, user-single-addr | ✅ в боте нет (и в продукте бота нет) |
| Мост | user-add-peer | Добавить профиль человеку | `actions.json: user-add-peer` | P: user-addprofile; T: add-profile | ✅ |
| Мост | user-del-peer | Убрать профиль у человека | `actions.json: user-del-peer` | P: user-removepeer; T: del-profile | ✅ |
| Мост | user-reissue-peer | Перевыпустить ключ на профиле | `actions.json: user-reissue-peer` | P: user-reissue, -imported, -failed; T: reissue, reissue-wait, reissue-done, reissue-fail | ✅ |
| Мост | user-attach-key | Привязать старый ключ (секрет через stdin) | `actions.json: user-attach-key (stdin)` | P: user-attach, -error, -server, -unreadable, -has, -done; T: attach, attach-done, attach-error | ✅ в боте нарисован только отказ server_mismatch и «QR с картинки не читаю» |
| Мост | backup-create | Создать бэкап | `actions.json: backup-create` | P: backups-creating, -created, -failed, -copy; T: backup-running, backup-done | ✅ |
| Мост | backup-list | Список бэкапов | `actions.json: backup-list` | P: backups, backups-empty, -encrypted; T: backup, restore-pick | ✅ |
| Мост | backup-restore | Восстановить из бэкапа | `actions.json: backup-restore` | P: backups-restore, -restore-typed, -restoring, -restored, -busy; T: restore (RESTORE <архив>), backup-encrypted | ✅ |
| Мост | import-scan | Найти контейнеры приложения Amnezia | `actions.json: import-scan` | P: import, import-scanning, -empty, -noconf; T: import | ✅ |
| Мост | import-adopt | Подхватить контейнер (со снимком) | `actions.json: import-adopt; lib/import.sh:181 снимок` | P: import-adopt, import-done; T: import-adopt | ✅ |
| Мост | import-reconcile | Сверить профиль с контейнером | `actions.json: import-reconcile` | P: import-reconcile, import-insync; T: import-reconcile | ✅ в боте нет исхода «расхождений нет» |
| Мост | upstream-check | Проверить обновления Amnezia | `actions.json: upstream-check` | P: updates-checking, updates, -unreachable; T: upstream, upstream-unreachable | ✅ |
| Мост | upstream-update | Обновить ядро профиля | `actions.json: upstream-update` | P: updates-confirm, -done, -failed, -rolledback, profiles-updating; T: upstream-update, upstream-fail | ✅ |
| Мост | upstream-rollback | Откатить ядро | `actions.json: upstream-rollback` | P: updates-rollback; T: upstream-rollback | ✅ |
| Мост | core-update | Починить на закреплённом образе | `actions.json: core-update` | P: updates-repair; T: upstream-repair (REPAIR) | ✅ |
| Мост | quota-tick | Таймер лимитов и сроков | `actions.json: quota-tick` | следствия: T: ntf-users; P: статусы в users | — таймер |
| Мост | watch-tick | Сторож контейнеров | `actions.json: watch-tick` | следствия: T: ntf-containers; P: overview-alerts, overview-docker | — таймер |
| Мост | stats-tick | Снимок статистики (дни и часы) | `actions.json: stats-tick` | следствия: P: traffic*, карта 7×24 | — таймер |
| Мост | upstream-tick | Суточная проверка обновлений | `actions.json: upstream-tick` | следствия: T: ntf-server; P: updates-badge («нужна доработка сервера») | — таймер |
| Мост | change-mode | Сменить режим (с доменом) | `actions.json: change-mode` | P: settings-mode-wizard (+partial, elsewhere, none, dnsfail, nodomain), -wizard-2, -light, -wizard-run, -done, -done-fallback, -done-lefail*, -tolite; T: mode-pick, mode-domain (MODE), mode-done | ✅ в боте нет исходов отказа Let's Encrypt и «сайт на запасном сертификате» |
| Мост | change-template | Сменить шаблон сайта | `actions.json: change-template` | P: settings-template*, -confirm, -installing; T: template, template-list, template-search, template-pick (TEMPLATE site) | ✅ |
| Мост | site-check | Проверка сайта снаружи | `actions.json: site-check` | P: settings-sitecheck, -partial, -down; T: site-check | ✅ нет строки про AAAA-запись не на этот сервер (lib/lang/ru.d/website.sh:92) |
| Мост | dns-set | DNS для клиентов | `actions.json: dns-set` | P: settings-clients, settings-clients-custom; T: dns, dns-custom | ✅ |
| Мост | miniapp-status | Состояние панели на домене | `actions.json: miniapp-status` | P: settings-access, -off, -nosite, -untrusted, -nobot, -nodomain; T: panel, panel-off | ✅ |
| Мост | miniapp-on | Открыть панель на домене | `actions.json: miniapp-on` | P: settings-access-open; T: panel-off → panel-url | ✅ |
| Мост | miniapp-off | Закрыть панель на домене | `actions.json: miniapp-off` | P: settings-access-close; T: panel-close | ✅ |
| Мост | miniapp-rotate | Сменить секретный путь | `actions.json: miniapp-rotate` | P: settings-access-rotate; T: panel-rotate, panel-url | ✅ |
| Мост | site-alt-svc | Alt-Svc | `actions.json: site-alt-svc` | P: settings-altsvc; T: alt-svc | ✅ |
| Мост | preflight | Проверка портов 80/443 | `actions.json: preflight` | P: settings-ports-checking, settings-mode; T: preflight | ✅ |
| Мост | lang-set | Язык продукта | `actions.json: lang-set` | P: settings-lang, -confirm; T: lang | ✅ |
| Мост | agent-list | Доступы агентов | `actions.json: agent-list` | P: agents, -empty, -loading; T: agents | ✅ |
| Мост | agent-add | Выдать доступ (уровень, срок, адреса, привязка, вид) | `actions.json: agent-add` | P: agents-add, -forever, -connect, -errors, agents-created, -rolling, -connect; T: agent-add, agent-days, agent-pin, agent-ips, agent-mode, agent-url | ✅ в боте нет итогов для одноразового ключа и переходника (код привязки, команды) |
| Мост | agent-show | Показать адрес, токен, команду | `actions.json: agent-show` | P: agents-show, agents-show-off; T: agent-card → agent-url | ✅ |
| Мост | agent-rotate | Перевыпустить секрет и токен | `actions.json: agent-rotate` | P: agents-rotate, agents-card-frozen; T: agent-card, agent-frozen | ✅ в боте нет экрана вопроса перед перевыпуском |
| Мост | agent-harden | Выдать токен старому доступу | `actions.json: agent-harden` | P: agents-harden, agents-card-weak; T: agent-weak, agent-harden, agent-harden-wait | ✅ |
| Мост | agent-pin | Привязка к адресу | `actions.json: agent-pin` | P: agents-pin; T: agent-card (⚲ Снять привязку) | ✅ |
| Мост | agent-extend | Продлить срок доступа | `actions.json: agent-extend` | P: agents-extend, agents-extend-forever, agents-card-expired | ✅ в боте нет (в продукте — только SSH, так и сказано в ntf-agents-2) |
| Мост | agent-enable | Включить доступ | `actions.json: agent-enable` | P: agents-enable, agents-card-disabled; T: agent-card | ✅ |
| Мост | agent-disable | Выключить доступ | `actions.json: agent-disable` | P: agents-disable; T: agent-card (⏸) | ✅ |
| Мост | agent-del | Удалить доступ | `actions.json: agent-del` | P: agents-delete (ввод номера); T: agent-delete (DELETE a-3f9c) | ✅ |
| Мост | mcp-http-status | Состояние HTTP-транспорта MCP | `actions.json: mcp-http-status` | P: agents-transport-*; T: agents | ✅ |
| Мост | mcp-http-on | Включить транспорт (в «Только VPN» — порт) | `actions.json: mcp-http-on` | P: agents-transport-on, -lite, -port, -port-busy, -port-closed, -selfsigned; T: agents-on-lite | ✅ |
| Мост | mcp-http-off | Выключить транспорт | `actions.json: mcp-http-off` | P: agents-transport-off, -offq; T: agents-off | ✅ |
| Мост | share-create | Временная ссылка на файл | `actions.json: share-create` | P: user-share, -addr, -ready, -selfsigned, -limit; T: share-pick, share-format, share-ready, share-selfsigned, share-vpn, share-limit | ✅ форматы QR-PNG и архив — только SSH, в дизайне не сказано |
| Мост | share-list | Активные ссылки | `actions.json: share-list` | P: users-shares, -empty; T: shares, shares-empty | ✅ |
| Мост | share-revoke | Снять ссылку | `actions.json: share-revoke` | P: users-shares-revoke; T: shares, share-ready («Снять ссылку») | ✅ |
| Мост | share-revoke-all | Снять все ссылки | `actions.json: share-revoke-all` | P: users-shares; T: shares-revoke-all | ✅ |
| Мост | share-tick | Уборка просроченных ссылок | `actions.json: share-tick` | — | — таймер |
| Мост | clients-list | Каталог приложений | `actions.json: clients-list` | C: connect (каталог по платформам); P: settings-mirror-fetch | ✅ |
| Мост | page-create | Создать страницу подключения | `actions.json: page-create` | P: user-page, -custom, -ready, -forever, -ready-forever, -nodomain, -disabled, -imported; T: page, page-ready, page-custom, page-disabled, page-need-domain, page-ready-imported | ✅ параметр profiles (какие профили на странице) — ни в одном интерфейсе |
| Мост | page-list | Страницы человека | `actions.json: page-list` | P: user — «Страницы подключения»; T: page-list, page-card | ✅ |
| Мост | page-url | Адрес страницы ещё раз | `actions.json: page-url` | P: user-page-url; T: page-card («Отправить ссылку ещё раз») | ✅ |
| Мост | page-revoke | Отозвать страницу | `actions.json: page-revoke` | P: user (Отозвать); T: page-revoke | ✅ |
| Мост | page-revoke-all | Отозвать все страницы человека | `actions.json: page-revoke-all` | P: user-pages-revoke-all | ✅ в боте нет (в продукте бота нет) |
| Мост | mirror-list | Зеркало сборок | `actions.json: mirror-list` | P: settings-mirror, -empty; T: mirror | ✅ |
| Мост | mirror-fetch | Скачать сборку на сервер | `actions.json: mirror-fetch` | P: settings-mirror-fetch; T: mirror-fetch | ✅ в боте нет шага выбора платформы |
| Мост | mirror-remove | Удалить сборку | `actions.json: mirror-remove` | P: settings-mirror-remove; T: mirror (🗑) | ✅ в боте нет вопроса перед удалением |
| Мост | profile-repair | Поднять контейнер из копии состояния | `actions.json: profile-repair` | P: profiles-repair, overview-alerts; T: profile-repair, start-missing | ✅ |
| Мост | backup-schedule | Расписание ночной копии | `actions.json: backup-schedule` | P: backups (сегмент из 4), backups-schedule-off; T: backup — «Расписание — в SSH-меню» | ✅ |
| Мост | backup-last | Последняя копия и итог ночи | `actions.json: backup-last` | P: backups, backups-night-failed; T: backup | ✅ |
| Мост | backup-tick | Тик ночной копии | `actions.json: backup-tick` | следствия: T: ntf-server (backup_failed); P: overview-alerts, backups-night-failed | — таймер |
| Мост | cert-tick | Тик срока сертификата | `actions.json: cert-tick` | следствия: T: ntf-cert; P: settings-mode-expiring, overview-alerts | — таймер |


## 3. SSH-меню и мастер (`install.sh`) — где функция в новом дизайне или где дизайн прямо говорит «только SSH»

| Компонент | № | Функция | Источник | Где в дизайне | Статус |
|---|---|---|---|---|---|
| SSH | S1 | Шапка: версия, режим, адрес, домен, панель, «В ключах», профили с портами, людей | `install.sh:612 show_main_menu` | P: overview (карточка «Сервер», профили); T: start | ✅ |
| SSH | S2 | Шапка: «режим и порт VPN не совпадают» | `install.sh show_main_menu (mode_port_mismatch)` | P: settings-mode-mismatch, overview-alerts; T: start-mismatch | ✅ |
| SSH | S3 | Шапка: вписанного в ключи адреса на сервере нет (endpoint_host_gone) | `install.sh:654 dash_endpoint_host_gone` | — | ✗ ни в панели, ни в боте; просится алерт на Обзоре и строка в «Адрес сервера в ключах» |
| SSH | S4 | Шапка: таблица «Адреса сервера» (A-запись ✓ ✗ ? —, 15 мин/1 ч/сутки, польз., состояние), чужие A-записи | `install.sh show_main_menu; lib/edgeips.sh` | P: overview, overview-single, -dns, -cf, -noa; T: state-addr | ✅ |
| SSH | S5 | Пользователи: таблица по страницам, значки, номер строки → карточка | `install.sh:864 submenu_users` | P: users, users-many; T: users | ✅ |
| SSH | S6 | Открыть карточку по имени или номеру | `install.sh:881 users_open_item` | P: поиск в users, ⌘K (cmd) | ✅ |
| SSH | S7 | Добавить пользователя: имя, галочки профилей, лимит, срок | `install.sh:956 _user_add_interactive` | P: user-add*; T: add-* | ✅ |
| SSH | S8 | После ключа — ожидание первого подключения до 3 мин и три проверки | `install.sh:2160 _wait_first_handshake` | P: user-created, user-created-ok, user-created-timeout | ✅ в боте ожидания нет (и в продукте бота нет) |
| SSH | S9 | Карточка: статус, причина, профили | `install.sh:1075 _user_card` | P: user*; T: user* | ✅ |
| SSH | S10 | Ключ: QR .conf | `install.sh:1566 user_key_qr_conf` | P: user; T: key-qr | ✅ |
| SSH | S11 | Ключ: текст .conf (и файл на диск) | `install.sh:1567 user_key_text_conf` | P: user-text; T: key-file | ✅ |
| SSH | S12 | Ключ: ссылка vpn:// | `install.sh:1568 user_key_vpn_link` | P: user (Скопировать vpn://); T: key-vpn | ✅ |
| SSH | S13 | Ключ: серия QR vpn:// | `install.sh:1569 user_key_qr_vpn` | P: user-vpn; T: key-qrvpn | ✅ |
| SSH | S14 | Ключ на другой адрес сервера (разово) | `install.sh:1638 _pick_key_address` | P: user-addr, user-addr-domain; T: key-addr* | ✅ |
| SSH | S15 | Скачать файлом по ссылке: .conf, .vpn | `install.sh:1926 _user_share (1, 2)` | P: user-share*; T: share-* | ✅ |
| SSH | S16 | ~~Ссылка: QR .conf PNG, QR vpn PNG, всё архивом~~ — снято в 1.0.6 (TAIL-14): по ссылке только файл, SSH даёт те же два формата, что бот и панель | — | — | — сцена не нужна |
| SSH | S17 | Профили пользователя: добавить профиль | `install.sh:1694 user_add_profile_item` | P: user-addprofile; T: add-profile | ✅ |
| SSH | S18 | Отобрать ключ на профиль | `install.sh:1695 user_del_profile_item` | P: user-removepeer; T: del-profile | ✅ |
| SSH | S19 | Перевыпустить ключ на профиле | `install.sh:1696 user_peer_reissue_item` | P: user-reissue*; T: reissue* | ✅ |
| SSH | S20 | Привязать старый ключ | `install.sh:1698 user_attach_item` | P: user-attach*; T: attach* | ✅ |
| SSH | S21 | Лимит трафика | `install.sh:1138 user_set_quota` | P: user-quota; T: quota | ✅ |
| SSH | S22 | Срок действия | `install.sh:1139 user_set_expiry` | P: user-expiry*; T: expiry | ✅ |
| SSH | S23 | Выключить / включить | `install.sh:1141-1143` | P: user, user-disabled, user-enable-limit; T: user-off, user-enable-warn | ✅ |
| SSH | S24 | Переименовать | `install.sh:2398 _user_rename` | P: user-rename; T: rename | ✅ |
| SSH | S25 | Страница подключения: список, создать (1 ч…бессрочно, своё), адрес ещё раз с QR, отозвать | `install.sh:1291 _pages_screen; 1198 _pages_ask_ttl; 1344 _pages_card` | P: user-page*, user-page-url; T: page* | ✅ |
| SSH | S26 | Удалить пользователя (набрать имя) | `install.sh:2409 _user_delete` | P: confirm, user-delete-nobackup; T: delete* | ✅ |
| SSH | S27 | Адрес сервера в ключе за человеком: закрепить, вернуть общий, польз. на адресе | `install.sh:2308 _user_address_screen` | P: user-pin, user-pin-gone, user-pin-domain | ✅ |
| SSH | S28 | Активные раздачи: список, снять, снять все | `install.sh:1986 _shares_screen` | P: users-shares*; T: shares* | ✅ |
| SSH | S29 | Состояние → Статус: контейнеры, порты, версии ядра | `install.sh:2425 st_item_status` | P: profiles, overview; T: state-status | ✅ |
| SSH | S30 | Состояние → Трафик и онлайн | `install.sh:2426 st_item_traffic` | P: traffic*; T: state-traffic | ✅ |
| SSH | S31 | Состояние → Логи: профили, бот, панель, система, журнал действий; команда journalctl | `install.sh:2727 _logs_screen` | P: logs*; T: state-logs, state-log* | ✅ |
| SSH | S32 | Состояние → Адреса сервера | `install.sh:2428 st_item_addr` | P: overview (таблица); T: state-addr | ✅ |
| SSH | S33 | Протоколы: список awg3/awg2/awg1 с портами | `install.sh:3009 submenu_protocols` | P: profiles; T: profiles | ✅ |
| SSH | S34 | Неустановленный профиль: Установить (порт случайный/свой/443), затем «выдать ключ всем» | `install.sh:3136 _profile_install_interactive` | P: profiles-notinstalled, -install*, -installed; T: profile-new, profile-install-pick | ✅ |
| SSH | S35 | Сменить порт | `install.sh:3221 _profile_port_interactive` | P: profiles-port; T: profile-port | ✅ |
| SSH | S36 | Занять UDP/443 | `install.sh:3082 pr_take443` | P: profiles-443*; T: profile-443 | ✅ |
| SSH | S37 | Параметры маскировки | `install.sh:3083 pr_params` | P: profiles-params; T: profile-params | ✅ |
| SSH | S38 | Изменить один параметр | `install.sh:3084 pr_set_param` | P: profiles-param*; T: profile-param | ✅ |
| SSH | S39 | MTU | `install.sh:3085 pr_set_mtu` | P: profiles-mtu; T: profile-mtu | ✅ |
| SSH | S40 | Подсеть | `install.sh:3086 pr_set_subnet` | P: profiles-subnet; T: profile-subnet | ✅ |
| SSH | S41 | Пресет (обычный / QUIC / для роутеров) | `install.sh:3274 _profile_regen_interactive` | P: profiles-regen; T: profile-preset | ✅ |
| SSH | S42 | Перегенерировать параметры | `install.sh:3088 pr_regen` | P: profiles-regen; T: profile-regen | ✅ |
| SSH | S43 | Перезапустить | `install.sh:3089 pr_restart` | P: profiles-restart; T: profile-restart | ✅ |
| SSH | S44 | Поднять контейнер заново из копии состояния | `install.sh:3094 pr_repair` | P: profiles-repair; T: profile-repair | ✅ |
| SSH | S45 | Удалить профиль (сколько людей теряют ключ) | `install.sh:3095 pr_remove` | P: profiles-remove; T: profile-remove | ✅ |
| SSH | S46 | Обслуживание → Перезапустить всё | `install.sh:3691 _restart_everything` | T: restart-all-ask, restart-all | ✅ в панели нет (и в продукте панели нет) |
| SSH | S47 | Бэкап: сделать сейчас | `install.sh:3554 mt_backup_now` | P: backups-creating, -created; T: backup-running, backup-done | ✅ |
| SSH | S48 | Бэкап: расписание ночной копии (выкл / ночь / неделя / месяц, 03:20) | `install.sh:3655 _backup_schedule_interactive` | P: backups (сегмент), backups-schedule-off; T: backup — «в SSH-меню» | ✅ |
| SSH | S49 | Восстановить из бэкапа (в т.ч. зашифрованный и архив «Удалить всё») | `install.sh:3838 _restore_interactive` | P: backups-restore*, backups-encrypted («только из SSH-меню»); T: restore*, backup-encrypted | ✅ |
| SSH | S50 | Обновления Amnezia: проверка, обновить, откат r<N>, плавающий тег, незнакомые параметры | `install.sh:3992 _upstream_screen` | P: updates*; T: upstream* | ◐ нет предупреждения SSH «точного отпечатка образа нет — откат вернёт то, что сейчас под тегом» (mt_upstream_rollback_floating) в окне отката |
| SSH | S51 | Импорт установки Amnezia: список, подхват (снимок), сверка при старте меню | `install.sh:3901 _import_scan_screen; 3951 _import_reconcile_interactive; 3976 _import_reconcile_prompt` | P: import*; T: import* | ✅ подсказка о расхождениях при каждом входе в меню — только SSH |
| SSH | S52 | Обновить goAmnezia (ветка, коммит, ключ доступа к репозиторию) | `install.sh:3772 _selfupdate_screen` | P: settings-about («Делается в SSH-меню»); T: selfupdate | — только SSH — в дизайне сказано явно |
| SSH | S53 | Снимок состояния перед обновлением и «Что нового» | `install.sh:7732 snapshot_preupgrade_state; 7962 show_upgrade_changelog` | — | — часть самообновления в SSH |
| SSH | S54 | Диагностика: Docker, пересылка, сеть, порты, диск, ключи сервера, таймеры | `install.sh:4168 _diagnose_screen` | P: diag, diag-problems; T: diag | ✅ |
| SSH | S55 | Режим и сайт → Сменить режим (домен, проверка портов) | `install.sh:4979 _site_mode_change` | P: settings-mode-wizard*; T: mode* | ✅ |
| SSH | S56 | Режим и сайт → Домен | `install.sh:4915 ms_item_domain` | P: settings-domain, settings-domain-bad | ✅ в боте домен меняется только через смену режима (как в продукте) |
| SSH | S57 | Режим и сайт → Шаблон: каталог с поиском и превью, случайный | `install.sh:5306 _site_template_screen` | P: settings-template*; T: template* | ✅ |
| SSH | S58 | Шаблон по git-адресу | `install.sh _site_template_screen (git)` | P: settings-about «Сертификат, свой шаблон по git-адресу — Настройки → Режим и сайт» | — только SSH — сказано явно |
| SSH | S59 | Сертификат: выпустить/проверить, продлить, почта центра сертификации | `install.sh:5380 _site_ssl_screen` | P: settings-about; T: ntf-cert («в боте этого пункта нет») | — только SSH — сказано явно |
| SSH | S60 | Самоподписанный: перейти на Let's Encrypt, перевыпустить | `install.sh:5332 _site_selfsigned_screen` | P: settings-mode-selfsigned, settings-about | — только SSH — сказано явно |
| SSH | S61 | Проверка сайта снаружи | `install.sh:5447 _site_check_screen` | P: settings-sitecheck*; T: site-check | ✅ |
| SSH | S62 | Alt-Svc вкл/выкл | `install.sh:4920-4922` | P: settings-altsvc; T: alt-svc | ✅ |
| SSH | S63 | Панель на домене: включить, сменить путь, выключить | `install.sh:5551 _site_panel_screen` | P: settings-access*; T: panel* | ✅ |
| SSH | S64 | Повторить проверку портов (и открыть порт в локальном файрволе) | `install.sh:4925 preflight_again_item; 6964 _wizard_offer_open_ports` | P: settings-ports-checking; T: preflight | ✅ открытие порта в ufw/firewalld — только SSH, не сказано |
| SSH | S65 | Адреса для клиентов → DNS (Cloudflare/Google/AdGuard/свой) | `install.sh:4394 _dns_pick` | P: settings-clients*; T: dns* | ✅ |
| SSH | S66 | Адреса для клиентов → Адрес сервера в ключах (проверки домена и IP) | `install.sh:4539 _endpoint_interactive` | P: settings-endpoint-*; T: endpoint | ✅ |
| SSH | S67 | Панель как служба: установить/обновить, удалить | `install.sh:5826 pn_install, 5833 pn_remove` | P: settings-about «Поставить, обновить или удалить веб-панель» | — только SSH — сказано явно |
| SSH | S68 | Панель: статус службы, логи, перезапуск | `install.sh:5827-5829` | P: overview «Службы», logs (источник «Панель») | ✅ перезапуск службы панели — только SSH (сказано в T: restart-all) |
| SSH | S69 | Панель: показать токен аварийного входа | `install.sh:5830 pn_token` | P: login-v4 («Вход по аварийному токену», путь к файлу) | — только SSH — сказано явно |
| SSH | S70 | Панель: пароль — перевыпустить | `install.sh:5831 pn_password` | P: settings-password (смена в панели); подсказки login-pwd* указывают путь в SSH | ✅ |
| SSH | S71 | Админы бота: список, владелец, назначить владельца | `install.sh:6523 _bot_admins_screen; 6497 _bot_owner_set` | P: settings-access (список); T: admins («Назначить владельца — в SSH-меню») | ✅ |
| SSH | S72 | Админ по одноразовому коду или вручную (при установке бота) | `install.sh:6705 _bot_admin_pick` | T: claim* (код в боте) | ✅ ручной ввод ID — только SSH |
| SSH | S73 | Доступы агентов: список, выдать, транспорт вкл/выкл | `install.sh:6266 _agents_screen; 6141 _agents_add_interactive; 6239 _agents_transport_toggle` | P: agents*; T: agents* | ✅ |
| SSH | S74 | Логи службы HTTP-транспорта MCP | `install.sh:6299 ag_item_logs` | — | ◐ нет ни источника в «Логах», ни строки «логи транспорта — в SSH-меню» |
| SSH | S75 | Карточка доступа: перевыпустить (переходник — новый код), токен, привязка, продлить, вкл/выкл, удалить, QR адреса | `install.sh:6346 _agents_card_screen` | P: agents-card*, -rotate, -harden, -pin, -extend*, -enable, -disable, -delete, -show | ✅ |
| SSH | S76 | Язык | `install.sh:4318 _language_interactive` | P: settings-lang; T: lang | ✅ |
| SSH | S77 | О программе | `install.sh:6469 _about_screen` | P: settings-about; T: about | ✅ |
| SSH | S78 | Копии приложений: список, скачать (приложение → платформа), удалить, занято | `install.sh:1434 _mirror_screen; 1493 _mirror_fetch_pick` | P: settings-mirror*; T: mirror* | ✅ |
| SSH | S79 | Удалить всё | `install.sh:6558 _wipe_everything` | P: settings-about; T: wipe | — только SSH — сказано явно |
| SSH | S80 | Telegram-бот: установить/обновить, статус, логи, перезапуск, .env без токена, удалить | `install.sh:6834 submenu_bot; 6813 _bot_env_screen` | P: settings-about («Поставить или удалить Telegram-бота — Главное меню → Telegram-бот»); логи — P: logs «Бот» | — только SSH — сказано явно |
| SSH | S81 | Мастер: контейнеры Amnezia на сервере — первым экраном (подхват до вопросов) | `install.sh:7235 _wz_takeover_gate; 7316 _wz_takeover_left` | T: install («Полный мастер — в SSH-меню»); P: import* (подхват позже) | — мастер — только SSH; тот же подхват доступен в «Импорте» |
| SSH | S82 | Мастер: режим, домен, профили, порты; DNS; адрес в ключах; первый пользователь; бот | `install.sh:7329 install_wizard; 7169 _wz_ports_step` | T: start-empty, install; P: overview-fresh (онбординг) | — только SSH — сказано явно |


## 4. Telegram-бот (`goamnezia-bot/bot.py`): команды, кнопки, ввод текстом, подтверждения, уведомления

| Компонент | № | Функция | Источник | Где в дизайне | Статус |
|---|---|---|---|---|---|
| Бот | B1 | Первый админ по одноразовому коду (8 цифр), окно 10 мин | `goamnezia-bot/bot.py:1998 cb_admin_confirm; admin_confirm_/admin_cancel/admin_again` | T: claim, claim-done, claim-bad, claim-closed | ✅ |
| Бот | B2 | ~~Окно назначения уже использовано~~ — снято в 0.4.7 (B47-2): ключа `admin_claim_used_fmt` нет, окно привязки открывают SSH и установщик сколько угодно раз; вне окна — `admin_not_set_up_fmt` | — | — | — сцена не нужна; claim-closed = `admin_not_set_up_fmt` |
| Бот | B3 | Чужой ID: «Доступ запрещён. Ваш ID» (с пределом ответов чужим) | `bot.py handle_callback/cmd_start (ALLOWED_IDS)` | T: claim-closed (вторая часть) | ✅ |
| Бот | B4 | Только личный чат: в группах бот молчит | `bot.py:8960 register_handlers (filters.ChatType.PRIVATE)` | — | — поведение без экрана; можно одной строкой в /help |
| Бот | B5 | Защита от повторного нажатия (одна операция на кнопку) | `bot.py:1691 _once_callback (8f24a59)` | T: reissue-wait, agent-harden-wait — кнопок подтверждения нет | ✅ повторное нажатие молча игнорируется — рисовать нечего |
| Бот | B6 | /start — главное меню: сервер, версия, режим, на связи, трафик, строка проблемы | `bot.py:8137 cmd_start; 2211 cb_menu_main` | T: start, start-ok, start-missing, start-docker, start-lite (без «Панель»), start-mismatch, start-empty | ✅ версия в сценах — v0.4.5, в панели 0.4.6 |
| Бот | B7 | /help — список команд | `bot.py:8155 cmd_help` | T: commands | ✅ в списке и в подсказках ввода есть /cancel — в продукте такой команды нет; принято как требование к 0.5.0 (D2-10) |
| Бот | B8 | /users, /status | `bot.py:8185 cmd_users; 8172 cmd_status` | T: users, state-status | ✅ |
| Бот | B9 | /adduser имя [ГБ] [дней] — одной строкой | `bot.py:8199 cmd_adduser` | T: commands (только строка справки) | ◐ нет ответа команды: создан / ошибка / занято |
| Бот | B10 | /key имя [профиль] | `bot.py:8226 cmd_key` | T: commands (только строка справки) | ◐ нет ответа команды |
| Бот | B11 | /invite ID — добавить админа (владелец) | `bot.py:8269 cmd_invite` | T: admins (подсказка /invite 123456789) | ◐ нет ответов: добавлен / не сохранён в .env / неверные ID / не владелец |
| Бот | B12 | /lang и язык бота «свой» / «для всех» | `bot.py:8164 cmd_lang; 8065 cb_lang_set; 8077 cb_lang_product` | T: lang | ✅ |
| Бот | B13 | Кнопка мини-приложения только при панели на домене | `bot.py set_chat_menu_button` | T: start (🚀 Панель), start-lite (без неё); M: entry | ✅ |
| Бот | B14 | Установка из бота (awg2 по умолчанию) | `bot.py:2226 cb_menu_install; 2245 cb_install_awg2` | T: start-empty, install, install-done | ✅ |
| Бот | B15 | Закрыть меню | `bot.py:2217 cb_close_menu` | T: start (❌ Закрыть) | ✅ |
| Бот | B16 | Список людей по 8, по два в ряд, значки ● ○ ⏳ ▮, 📥 подхваченные | `bot.py:2322 cb_menu_users` | T: users, users-2, users-empty | ✅ |
| Бот | B17 | Мастер «Добавить»: имя → профили → лимит → срок | `bot.py:2990 cb_user_add; 3095…3143; flow user_add` | T: add-name, add-profiles, add-quota, add-days, add-done, add-error | ✅ |
| Бот | B18 | Карточка: статус и причина, трафик, срок, профили, адрес в VPN, приложение | `bot.py:2579 cb_user_view` | T: user, user-lim, user-off, user-exp, user-imported | ✅ |
| Бот | B19 | Ключ: QR .conf / файл .conf / vpn:// / QR vpn:// по профилю | `bot.py:2848 cb_user_key (u_key~)` | T: key-qr, key-file, key-vpn, key-qrvpn | ✅ |
| Бот | B20 | Длинный vpn:// — отправлен файлом | `lang key_vpn_file_hint` | T: key-vpn | ✅ |
| Бот | B21 | Ключ выключенного — с предупреждением | `bot.py cb_user_key (disabled)` | T: key-disabled | ✅ |
| Бот | B22 | Ключ на другой адрес (разово): адрес → профиль и вид | `bot.py:2773 u_kaddr~, 2785 u_kah~, u_ka~` | T: key-addr, key-addr-gone, key-addr-formats | ✅ |
| Бот | B23 | Временная ссылка: профиль → формат → адрес отдельным сообщением, «Снять», автоудаление | `bot.py:3854 u_share~, 3889 u_shf~, 3925 u_shdo~, 3984 sh_rev~` | T: share-pick, share-format, share-ready, share-selfsigned, share-vpn, share-limit | ✅ |
| Бот | B24 | Страница подключения: срок кнопками / свой срок | `bot.py:4247 u_page~, 4297 pg_ttl~; flow page_ttl` | T: page, page-custom, page-ready, page-ready-imported, page-disabled, page-need-domain | ✅ |
| Бот | B25 | Страницы человека: список, карточка, ссылка ещё раз, отозвать | `bot.py:4357 pg_list~, 4426 pg_card~, 4446 pg_url~, 4473 pg_rev~` | T: page-list, page-card, page-revoke | ✅ |
| Бот | B26 | Лимит: кнопки 10/50/100/без лимита или число | `bot.py:3246 u_quota~, 3260 uq_set~; flow user_quota` | T: quota, quota-done | ✅ |
| Бот | B27 | Срок: 7/30/90/бессрочно или число дней | `bot.py:3284 u_exp~, 3298 ue_set~; flow user_expiry` | T: expiry, expiry-done | ✅ |
| Бот | B28 | Выключить / включить; «включение не удержится» | `bot.py:3322 u_en~` | T: user (⏸), user-off, user-enable-warn | ✅ |
| Бот | B29 | Переименовать (подпись до 48 знаков) | `bot.py:3340 u_ren~; flow user_rename` | T: rename, rename-expired | ✅ |
| Бот | B30 | Добавить профиль человеку | `bot.py:3354 u_addp~, 3376 u_addp_do~` | T: add-profile | ✅ |
| Бот | B31 | Убрать профиль (вопрос) | `bot.py:3400 u_delp~, 3421 u_delp_ask~, 3439 u_delp_do~` | T: del-profile | ✅ |
| Бот | B32 | Перевыпустить ключ (у подхваченного — совет привязать) | `bot.py:3474 u_reis~, 3506 u_reis_do~` | T: reissue, reissue-wait, reissue-done, reissue-fail | ✅ |
| Бот | B33 | Привязать старый ключ: текст vpn://, файл .conf, фото; сообщение с ключом удаляется | `bot.py:3577 u_att~; 3718 handle_file_message; flow peer_attach` | T: attach, attach-done, attach-error | ◐ из отказов нарисован только server_mismatch и «QR с картинки не читаю»; нет key_mismatch (ключ другого человека), config_unreadable, already_has_key |
| Бот | B34 | Удалить: фраза DELETE имя, 60 с, отмена | `bot.py:4677 u_del~; 4701 tc_cancel~; typed-confirm user-del` | T: delete, delete-done, delete-wrong, delete-expired, delete-cancel | ✅ |
| Бот | B35 | Раздел «Состояние» | `bot.py:4821 cb_sec_state` | T: state | ✅ |
| Бот | B36 | Статус: профили, порты, линейки, ключей выдано, версия ядра | `bot.py:4858 cb_st_status` | T: state-status | ✅ |
| Бот | B37 | Трафик: сегодня/месяц, по людям | `bot.py:5033 cb_st_traffic` | T: state-traffic | ✅ |
| Бот | B38 | Логи: источники и 60 строк; профили журнал не ведут | `bot.py:5076 cb_st_logs; 5099 st_log~` | T: state-logs, state-log, state-log-awg | ✅ |
| Бот | B39 | Адреса: таблица, A-записи, чужие записи, Cloudflare | `bot.py:5236 cb_st_addr` | T: state-addr | ✅ |
| Бот | B40 | Список профилей с легендой состояний | `bot.py:5253 cb_sec_protocols` | T: profiles, profile-docker | ✅ |
| Бот | B41 | Карточка профиля со всеми действиями | `bot.py:5332 pr_card~` | T: profile, profile-missing | ✅ |
| Бот | B42 | Установить: рекомендуется / свой порт / UDP 443 | `bot.py:5343 pr_install~, 5366 pr_inst_do~, 5389 pr_inst_port~; flow profile_install_port` | T: profile-new, profile-install-pick, profile-install | ◐ нет сцены ввода своего порта и вопроса «Установить на UDP-порт N?» (pr_install_port_confirm_fmt) |
| Бот | B43 | Сменить порт | `bot.py:5420 pr_port~, 5447 pr_port_do~; flow profile_port` | T: profile-port | ✅ |
| Бот | B44 | Занять UDP/443 | `bot.py:5477 pr_443~, 5492 pr_443_do~` | T: profile-443 | ✅ |
| Бот | B45 | Подсеть | `bot.py:5505 pr_subnet~, 5519 pr_subnet_ok; flow profile_subnet` | T: profile-subnet | ✅ |
| Бот | B46 | MTU | `bot.py:5541 pr_mtu~, 5566 pr_mtu_set~; flow profile_mtu` | T: profile-mtu | ✅ |
| Бот | B47 | Параметры: просмотр, изменить, убрать | `bot.py:5601 pr_params~, 5620 pr_pedit~, 5646 pr_pkey~, 5703 pr_punset, 5714 pr_pok` | T: profile-params, profile-param | ✅ |
| Бот | B48 | Пресет и перегенерация (REGEN) | `bot.py:5732 pr_preset~, 5747 pr_preset_do~, 5759 pr_regen~` | T: profile-preset, profile-regen | ✅ |
| Бот | B49 | Перезапустить (вопрос) | `bot.py:5771 pr_restart~, 5788 pr_restart_do~` | T: profile-restart | ✅ |
| Бот | B50 | Поднять из копии состояния | `bot.py:5804 pr_fix~, 5820 pr_fix_do~` | T: profile-repair, start-missing | ✅ |
| Бот | B51 | Удалить профиль (REMOVE) | `bot.py:5408 pr_remove~` | T: profile-remove | ✅ |
| Бот | B52 | Раздел «Обслуживание» | `bot.py:5841 cb_sec_maint` | T: maint | ✅ |
| Бот | B53 | Перезапустить всё (вопрос, итог по профилям) | `bot.py:5858 mt_restart_all, 5867 mt_restart_all_do` | T: restart-all-ask, restart-all | ✅ |
| Бот | B54 | Бэкап: сделать, список, 🔒 — только SSH | `bot.py:5891 cb_mt_backup` | T: backup, backup-running, backup-done, backup-encrypted | ✅ |
| Бот | B55 | Восстановить (RESTORE <архив>) | `bot.py:5917 mt_restore, 5935 mt_rs~` | T: restore-pick, restore | ✅ |
| Бот | B56 | Обновления Amnezia: обновить / откатить / починить (UPDATE/ROLLBACK/REPAIR) | `bot.py:6016 cb_mt_upstream; 6035 up_upd~, 6049 up_fix~, 6064 up_rb~` | T: upstream, upstream-update, upstream-rollback, upstream-repair, upstream-fail, upstream-unreachable | ✅ |
| Бот | B57 | Импорт: подхватить, сверить | `bot.py:6131 mt_import; 6151 imp_adopt~, 6170 imp_adopt_do~, 6211 imp_rec~, 6233 imp_rec_do~` | T: import, import-adopt, import-reconcile | ◐ нет исходов: «подхвачено, N человек» и «ключи совпадают» (mt_import_in_sync_fmt) |
| Бот | B58 | Диагностика | `bot.py:6320 cb_mt_diag` | T: diag | ✅ |
| Бот | B59 | Временные ссылки: список, снять, снять все | `bot.py:4036 sh_list, 4054 sh_revall` | T: shares, shares-revoke-all, shares-empty | ✅ |
| Бот | B60 | Обновить goAmnezia — в SSH | `bot.py:6332 mt_selfupdate` | T: selfupdate | ✅ |
| Бот | B61 | Раздел «Настройки» | `bot.py:6344 cb_sec_settings` | T: settings | ✅ |
| Бот | B62 | Режим и сайт: сменить режим (домен / без домена / оставить), MODE | `bot.py:6361 se_mode, 6438 se_mode_pick, 6457 se_mode_to~, 6511 se_mode_keep~, 6529 se_mode_nodom~; flow mode_domain` | T: mode, mode-pick, mode-domain, mode-done | ◐ нет исходов отказа (Let's Encrypt отказал, DNS не туда) и «сайт на запасном сертификате»; нет «оставить прежний домен» |
| Бот | B63 | Шаблон: категории, список, поиск, наугад, TEMPLATE site | `bot.py:6613 se_tpl, 6675 se_tplc~, 6696 se_tplq, 6706 se_tplf~, 6735 se_tplp~, 6772 se_tplr; flow tpl_search` | T: template, template-list, template-search, template-pick | ✅ |
| Бот | B64 | Проверить сайт | `bot.py:6790 se_check` | T: site-check | ◐ нет состояний с проблемой (A-запись не сюда, сайт не отвечает, сертификат истекает) |
| Бот | B65 | Alt-Svc | `bot.py:6823 se_alt, 6837 se_alt_set~` | T: alt-svc | ✅ |
| Бот | B66 | Проверка портов 80/443 | `bot.py:6870 se_pf` | T: preflight | ✅ |
| Бот | B67 | DNS: три набора и свой | `bot.py:6904 se_dns, 6933 se_dns_set~, 6947 se_dns_custom; flow dns_custom` | T: dns, dns-custom | ✅ |
| Бот | B68 | Адрес в ключах: IP сервера / по умолчанию / свой | `bot.py:6956 se_endpoint, 6991 se_endpoint_ip, 7008 se_endpoint_auto; flow endpoint` | T: endpoint | ◐ нет кнопок «📍 IP сервера» и «↩️ По умолчанию», нет отказа «нужен домен или IPv4» |
| Бот | B69 | Панель и мини-апп: открыть / сменить путь / закрыть | `bot.py:7019 se_panel, 7093 se_mini~` | T: panel, panel-off, panel-rotate, panel-close, panel-url | ✅ |
| Бот | B70 | Доступы агентов: список, транспорт вкл/выкл, порт в «Только VPN» | `bot.py:7272 se_agents, 7704 ag_http~; flow agent_port` | T: agents, agents-off, agents-on-lite | ◐ нет сцены ввода порта (⌨️ Указать порт) и итога включения с отпечатком |
| Бот | B71 | Мастер доступа: название → уровень → срок → привязка → адреса → вид | `bot.py:7764 ag_add, 7806 ag_scope~, 7824 ag_days~, 7848 ag_apin~, 7889 ag_aips~, 7905 ag_amode~; flow agent_add` | T: agent-add, agent-days, agent-pin, agent-ips, agent-mode, agent-url | ◐ итог нарисован только для постоянного токена; нет итога для одноразового ключа и переходника (код привязки на 10 мин, команды — ag_url_title_connect_fmt) |
| Бот | B72 | Карточка доступа: показать, перевыпустить, привязка, выкл/вкл, удалить (DELETE a-…) | `bot.py:7413 ag_view~, 7499 ag_show~, 7518/7547 ag_rot~, 7609 ag_pin~, 7633 ag_en~, 7675 ag_del~` | T: agent-card, agent-weak, agent-frozen, agent-delete | ◐ нет вопросов перед перевыпуском и выкл/вкл (ag_rotate_confirm_fmt, ag_toggle_off/on_confirm_fmt) и карточки «ждёт привязки / код истёк» |
| Бот | B73 | Выдать токен слабому доступу | `bot.py:7572 ag_hard~, 7588 ag_hard_do~` | T: agent-weak, agent-harden, agent-harden-wait | ✅ |
| Бот | B74 | Админы бота: список, владелец, удалить (только владелец) | `bot.py:7977 se_admins, 7993 se_admin_del~` | T: admins, admin-del | ✅ |
| Бот | B75 | О программе | `bot.py:8101 se_about` | T: about | ✅ версия v0.4.5 |
| Бот | B76 | Копии приложений: список, скачать, удалить | `bot.py:4554 se_mirror, 4580 mr_fetch, 4599 mr_app~, 4622 mr_fetch~, 4643 mr_del~` | T: mirror, mirror-fetch | ◐ нет шага выбора платформы, итога «поставлено в очередь» и вопроса перед удалением |
| Бот | B77 | Удалить всё — только SSH | `bot.py:8122 se_wipe` | T: wipe | ✅ |
| Бот | B78 | Ввод текстом: ждём 10 минут, «время вышло», «Отменено», неожиданный текст | `bot.py:8301 handle_text_message (17 видов flow)` | T: add-name, rename-expired, flow-expired, unexpected-text, delete-cancel | ✅ |
| Бот | B79 | Фразы подтверждения (DELETE, REMOVE, REGEN, RESTORE, UPDATE, ROLLBACK, REPAIR, MODE, TEMPLATE, DELETE a-…): 60 с, неверная фраза | `bot.py:4721 typed-confirm kinds` | T: delete*, profile-remove, profile-regen, restore, upstream-update, upstream-rollback, upstream-repair, mode-domain, template-pick, agent-delete | ✅ |
| Бот | B80 | Занято: кнопка при идущей операции / ввод встаёт в очередь / замок у SSH-панели-агента | `bot.py:1664 _action_busy (action_busy_*); action_queued; err_lock_timeout` | T: busy, queued | ◐ расхождение: макет всегда обещает «поставлено в очередь, выполнится само» и называет чужое действие с длительностью; продукт на нажатие кнопки отвечает «дождитесь и повторите», при замке SSH/панели/агента — «подождите и повторите», имени и длительности чужого действия бот не знает |
| Бот | B81 | Сбои: сервер не ответил, таймаут 60 с, внутренняя ошибка, неизвестная кнопка | `bot.py error_text, error_handler` | T: error, error-timeout, internal-error, unknown-button | ✅ |
| Бот | B82 | Уведомления о людях: 80 % лимита, лимит исчерпан, срок скоро, срок вышел | `bot.py:1245 format_notify_event (quota_80, quota_exceeded, expiry_soon, expired)` | T: ntf-users | ✅ |
| Бот | B83 | Контейнеры: не работает, сторож поднял/не смог, контейнера нет, Docker не отвечает | `format_notify_event (container_down, container_restarted, container_missing, docker_unreachable)` | T: ntf-containers | ✅ |
| Бот | B84 | Адреса: замолчал, напоминание, снова принимает | `format_notify_event (edge_ip_dark, edge_ip_back)` | T: ntf-addr | ✅ |
| Бот | B85 | Сертификат: истекает / не читается (LE и самоподписанный) | `format_notify_event (cert_expiring, cert_unknown)` | T: ntf-cert | ✅ |
| Бот | B86 | Сервер: IP сменился, часы прыгнули, бэкап не сделан, доступно обновление, прочее событие | `format_notify_event (server_ip_changed, server_clock_jump, backup_failed, upstream_available, generic)` | T: ntf-server | ✅ |
| Бот | B87 | Агенты: впервые, новый адрес, без токена, предел частоты, привязан переходником | `format_notify_event (agent_first_use, agent_new_ip, agent_token_failed, agent_rate_limited, agent_enrolled)` | T: ntf-agents | ✅ |
| Бот | B88 | Агенты: истекает, истёк | `format_notify_event (agent_expiring, agent_expired)` | T: ntf-agents-2 | ✅ |
| Бот | B89 | Клон: ЗАМОРОЖЕН / заморозить не удалось, кнопки | `format_notify_event (agent_compromised); bot.py:8798 notify_keyboard` | T: ntf-clone | ✅ |
| Бот | B90 | Код входа в панель (сообщение бота) | `admin-web/server.py send code via bot` | T: ntf-code | ✅ |
| Бот | B91 | Английская версия бота | `goamnezia-bot/lang/en.json (810 ключей)` | T: lang=en | ◐ в 140 из 179 сцен при lang=en остаются русские строки: кнопки (🔄 Обновить, ⬅️ Назад, ✅ Подтвердить…), карточки профиля, мастера агента; переведены в основном рамка чата |
| Бот | B92 | Мини-приложение: вход по подписи, отказ «не админ», вход кодом, новая панель в цветах Telegram | `admin-web (initData); Telegram.dc.html view=miniapp` | M: entry, denied, denied-code, panel (все экраны панели), tgScheme day/night | ✅ D2-09 проверено: мини-апп рисует экраны итерации 2 (settings-access, agents, user-add…), 44 px, EN |


## 5. MCP-сервер (`goamnezia-mcp/server.py`): возможности, которые видны людям

| Компонент | № | Функция | Источник | Где в дизайне | Статус |
|---|---|---|---|---|---|
| MCP | M1 | Транспорт SSH (stdio) — работает всегда | `goamnezia-mcp/server.py:1-30` | P: agents («По SSH агент подключается всегда» + команда); T: agents | ✅ |
| MCP | M2 | HTTP-транспорт: через nginx на 443 или свой порт в «Только VPN», отпечаток самоподписанного | `server.py --http; actions mcp-http-*` | P: agents-transport-on, -off, -offq, -lite, -port, -port-busy, -port-closed, -selfsigned; T: agents-off, agents-on-lite | ✅ |
| MCP | M3 | Уровни read / write / admin и отбор инструментов по уровню | `server.py:189 SCOPES; 915 tool_allowed` | P: agents-add («Только чтение / Чтение и запись / Полный» с пояснениями); T: agent-add | ✅ |
| MCP | M4 | Два фактора: секретный путь + токен Bearer; команда подключения | `server.py:30-50` | P: agents-created, agents-show; T: agent-url | ✅ |
| MCP | M5 | Слабый доступ без токена и «Выдать токен» (agent-harden) | `server.py:2522 weak` | P: agents-card-weak, agents-harden, баннер на agents; T: agent-weak, agent-harden | ✅ |
| MCP | M6 | Срок: по умолчанию 30 дней, бессрочно, сам продлевается от удачных вызовов, продление руками | `server.py:158, 2402; lib/agents.sh:628 sliding_hint` | P: agents-add, -add-forever, -extend, -extend-forever, -card-expired; T: agent-days, ntf-agents-2 | ◐ в панели нет пояснения «срок продлевается сам от каждого удачного вызова» (есть в боте) — в карточке истёкшего это читается как поломка |
| MCP | M7 | Привязка к адресу первого вызова (атомарно, SEC-R3), перепривязать, снять | `server.py:1889 pin_ip; 2307 admit` | P: agents-pin, карточки «закреплён за … / закрепится при первом вызове»; T: agent-pin, agent-card | ✅ |
| MCP | M8 | Разрешённые адреса и подсети | `actions.json agent-add allow_ips` | P: agents-add («Разрешённые адреса · необязательно»), agents-add-errors; T: agent-ips | ✅ |
| MCP | M9 | Одноразовый ключ (rolling): ключ меняется на каждом запросе | `server.py:53 mode rolling` | P: agents-created-rolling; T: agent-mode | ✅ в боте нет итога выдачи (см. B71) |
| MCP | M10 | Клон: заморозка, «Это был я — перевыпустить», «заморозить не удалось» | `server.py (compromised); notify agent_compromised` | P: agents-card-frozen, agents-card-freeze-failed; T: agent-frozen, ntf-clone | ✅ |
| MCP | M11 | Переходник goamnezia-connect: код привязки 10 мин, TPM/файл, «код истёк» | `server.py:213 enroll; 2196 pending_enrollment` | P: agents-add-connect, agents-created-connect, agents-card-enroll, -enroll-expired; T: agent-mode, ntf-agents | ✅ |
| MCP | M12 | Предел частоты вызовов (доступ жив) | `server.py rate limit; notify agent_rate_limited` | T: ntf-agents | ◐ в панели состояние «упёрся в предел частоты» не показано (карточка/список) |
| MCP | M13 | Учёт: запросов всего, последний вызов (время, адрес) | `lib/agents.sh:666 agent-list` | P: agents, agents-card; T: agent-card | ✅ |
| MCP | M14 | Журнал действий агента | `logs-tail component=mcp` | P: logs-audit, logs-audit-agent (ссылка из карточки); T: state-logs «Журнал действий» | ✅ |
| MCP | M15 | Уведомления: впервые, новый адрес, без токена, истекает, истёк, привязан, часы сервера | `server.py notifier` | T: ntf-agents, ntf-agents-2, ntf-server | ✅ |
| MCP | M16 | Параметр confirm у разрушительных инструментов | `server.py:30 (confirm)` | — | — защита для модели, экрана не требует |
| MCP | M17 | 404 на чужой путь, бан подбора | `server.py HTTP` | — | — без экрана по замыслу |
| MCP | M18 | Ресурсы: status, profiles, users, traffic, diagnose, guide | `lib/actions.json resources (6)` | — | — для агента; людям — те же данные в панели |
| MCP | M19 | Подсказки: onboard_user, diagnose_user, safe_core_update | `lib/actions.json prompts (3)` | — | — для агента |


## 6. Инструменты MCP (66) — интерфейс агента, в дизайне людей не рисуются

| Компонент | № | Функция | Источник | Где в дизайне | Статус |
|---|---|---|---|---|---|
| MCP-инстр. | server_status | инструмент агента → `status` | `goamnezia-mcp/server.py ← lib/actions.json: status` | людям: P: overview; T: start, state-status | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | profiles_list | инструмент агента → `profiles-status` | `goamnezia-mcp/server.py ← lib/actions.json: profiles-status` | людям: P: profiles, profiles-states, profiles-docker; T: profiles, profile, profile-missing, profile-docker | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | diagnose | инструмент агента → `quality` | `goamnezia-mcp/server.py ← lib/actions.json: quality` | людям: P: diag, diag-problems, diag-running; T: diag | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | traffic_report | инструмент агента → `traffic` | `goamnezia-mcp/server.py ← lib/actions.json: traffic` | людям: P: traffic*, overview (карта 7×24), overview-heat-partial; T: state-traffic | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | edge_status | инструмент агента → `edge-status` | `goamnezia-mcp/server.py ← lib/actions.json: edge-status` | людям: P: overview (таблица), overview-single, -dns, -cf, -noa, user-addr; T: state-addr, key-addr | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | logs_tail | инструмент агента → `logs-tail` | `goamnezia-mcp/server.py ← lib/actions.json: logs-tail` | людям: P: logs-*, logs-audit, logs-audit-agent; T: state-logs, state-log, state-log-awg | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | logs_enforce | инструмент агента → `logs-enforce` | `goamnezia-mcp/server.py ← lib/actions.json: logs-enforce` | людям: P: logs-trim | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | logs_status | инструмент агента → `logs-status` | `goamnezia-mcp/server.py ← lib/actions.json: logs-status` | людям: P: logs — «Место под логи» | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | profile_install | инструмент агента → `profile-install` | `goamnezia-mcp/server.py ← lib/actions.json: profile-install` | людям: P: profiles-install, -errors, -443, -443-free, -foreign, -installing, -installed; T: profile-new, profile-install-pick, profile-install, install, install-done | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | profile_remove | инструмент агента → `profile-remove` (разрушительный, confirm) | `goamnezia-mcp/server.py ← lib/actions.json: profile-remove` | людям: P: profiles-remove; T: profile-remove (REMOVE awg1) | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | profile_restart | инструмент агента → `profile-restart` | `goamnezia-mcp/server.py ← lib/actions.json: profile-restart` | людям: P: profiles-restart; T: profile-restart | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | profile_set_port | инструмент агента → `profile-port` | `goamnezia-mcp/server.py ← lib/actions.json: profile-port` | людям: P: profiles-port, profiles-443, profiles-443-pro; T: profile-port, profile-443 | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | profile_params | инструмент агента → `profile-params` | `goamnezia-mcp/server.py ← lib/actions.json: profile-params` | людям: P: profiles-params; T: profile-params | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | profile_regen | инструмент агента → `profile-regen` (разрушительный, confirm) | `goamnezia-mcp/server.py ← lib/actions.json: profile-regen` | людям: P: profiles-regen; T: profile-preset, profile-regen (REGEN) | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | profile_set_param | инструмент агента → `profile-set-param` (разрушительный, confirm) | `goamnezia-mcp/server.py ← lib/actions.json: profile-set-param` | людям: P: profiles-param, -param-client, -param-error; T: profile-param | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | profile_set_subnet | инструмент агента → `profile-subnet` (разрушительный, confirm) | `goamnezia-mcp/server.py ← lib/actions.json: profile-subnet` | людям: P: profiles-subnet; T: profile-subnet | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | profile_set_mtu | инструмент агента → `profile-mtu` | `goamnezia-mcp/server.py ← lib/actions.json: profile-mtu` | людям: P: profiles-mtu; T: profile-mtu | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | endpoint_set | инструмент агента → `endpoint-set` | `goamnezia-mcp/server.py ← lib/actions.json: endpoint-set` | людям: P: settings-clients, settings-endpoint-nodns, -other, -notip; T: endpoint | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | user_add | инструмент агента → `user-add` | `goamnezia-mcp/server.py ← lib/actions.json: user-add` | людям: P: user-add, -errors, -noprofiles, -busy, -locked, user-created*; T: add-name…add-done, add-error | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | user_delete | инструмент агента → `user-del` (разрушительный, confirm) | `goamnezia-mcp/server.py ← lib/actions.json: user-del` | людям: P: confirm, user-delete-nobackup; T: delete, delete-done, -wrong, -expired, -cancel | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | users_list | инструмент агента → `user-list` | `goamnezia-mcp/server.py ← lib/actions.json: user-list` | людям: P: users*; T: users, users-2, users-empty | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | user_show | инструмент агента → `user-show` | `goamnezia-mcp/server.py ← lib/actions.json: user-show` | людям: P: user*; T: user, user-lim, user-off, user-imported, user-exp | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | user_get_key | инструмент агента → `user-key` | `goamnezia-mcp/server.py ← lib/actions.json: user-key` | людям: P: user, user-vpn, user-text, user-addr; T: key-qr, key-file, key-vpn, key-qrvpn, key-disabled, key-addr* | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | user_rename | инструмент агента → `user-rename` | `goamnezia-mcp/server.py ← lib/actions.json: user-rename` | людям: P: user-rename; T: rename, rename-expired | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | user_enable | инструмент агента → `user-enable` | `goamnezia-mcp/server.py ← lib/actions.json: user-enable` | людям: P: user-disabled, user-enable-limit; T: user-off, user-enable-warn | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | user_disable | инструмент агента → `user-disable` | `goamnezia-mcp/server.py ← lib/actions.json: user-disable` | людям: P: user (Выключить); T: user (⏸ Выключить) | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | user_set_quota | инструмент агента → `user-quota` | `goamnezia-mcp/server.py ← lib/actions.json: user-quota` | людям: P: user-quota; T: quota, quota-done | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | user_set_expiry | инструмент агента → `user-expiry` | `goamnezia-mcp/server.py ← lib/actions.json: user-expiry` | людям: P: user-expiry, user-expiry-past; T: expiry, expiry-done | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | user_set_address | инструмент агента → `user-set-address` | `goamnezia-mcp/server.py ← lib/actions.json: user-set-address` | людям: P: user-pin, user-pin-gone, user-pin-domain, user-single-addr | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | user_add_profile | инструмент агента → `user-add-peer` | `goamnezia-mcp/server.py ← lib/actions.json: user-add-peer` | людям: P: user-addprofile; T: add-profile | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | user_remove_profile | инструмент агента → `user-del-peer` (разрушительный, confirm) | `goamnezia-mcp/server.py ← lib/actions.json: user-del-peer` | людям: P: user-removepeer; T: del-profile | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | user_reissue_key | инструмент агента → `user-reissue-peer` (разрушительный, confirm) | `goamnezia-mcp/server.py ← lib/actions.json: user-reissue-peer` | людям: P: user-reissue, -imported, -failed; T: reissue, reissue-wait, reissue-done, reissue-fail | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | user_attach_key | инструмент агента → `user-attach-key` | `goamnezia-mcp/server.py ← lib/actions.json: user-attach-key` | людям: P: user-attach, -error, -server, -unreadable, -has, -done; T: attach, attach-done, attach-error | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | backup_create | инструмент агента → `backup-create` | `goamnezia-mcp/server.py ← lib/actions.json: backup-create` | людям: P: backups-creating, -created, -failed, -copy; T: backup-running, backup-done | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | backup_list | инструмент агента → `backup-list` | `goamnezia-mcp/server.py ← lib/actions.json: backup-list` | людям: P: backups, backups-empty, -encrypted; T: backup, restore-pick | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | backup_restore | инструмент агента → `backup-restore` (разрушительный, confirm) | `goamnezia-mcp/server.py ← lib/actions.json: backup-restore` | людям: P: backups-restore, -restore-typed, -restoring, -restored, -busy; T: restore (RESTORE <архив>), backup-encrypted | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | import_scan | инструмент агента → `import-scan` | `goamnezia-mcp/server.py ← lib/actions.json: import-scan` | людям: P: import, import-scanning, -empty, -noconf; T: import | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | import_adopt | инструмент агента → `import-adopt` | `goamnezia-mcp/server.py ← lib/actions.json: import-adopt` | людям: P: import-adopt, import-done; T: import-adopt | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | import_reconcile | инструмент агента → `import-reconcile` | `goamnezia-mcp/server.py ← lib/actions.json: import-reconcile` | людям: P: import-reconcile, import-insync; T: import-reconcile | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | upstream_check | инструмент агента → `upstream-check` | `goamnezia-mcp/server.py ← lib/actions.json: upstream-check` | людям: P: updates-checking, updates, -unreachable; T: upstream, upstream-unreachable | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | upstream_update | инструмент агента → `upstream-update` (разрушительный, confirm) | `goamnezia-mcp/server.py ← lib/actions.json: upstream-update` | людям: P: updates-confirm, -done, -failed, -rolledback, profiles-updating; T: upstream-update, upstream-fail | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | upstream_rollback | инструмент агента → `upstream-rollback` (разрушительный, confirm) | `goamnezia-mcp/server.py ← lib/actions.json: upstream-rollback` | людям: P: updates-rollback; T: upstream-rollback | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | core_update | инструмент агента → `core-update` (разрушительный, confirm) | `goamnezia-mcp/server.py ← lib/actions.json: core-update` | людям: P: updates-repair; T: upstream-repair (REPAIR) | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | mode_set | инструмент агента → `change-mode` | `goamnezia-mcp/server.py ← lib/actions.json: change-mode` | людям: P: settings-mode-wizard (+partial, elsewhere, none, dnsfail, nodomain), -wizard-2, -light, -wizard-run, -done, -done-fallback, -done-lefail*, -tolite; T: mode-pick, mode-domain (MODE), mode-done | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | site_set_template | инструмент агента → `change-template` | `goamnezia-mcp/server.py ← lib/actions.json: change-template` | людям: P: settings-template*, -confirm, -installing; T: template, template-list, template-search, template-pick (TEMPLATE site) | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | site_check | инструмент агента → `site-check` | `goamnezia-mcp/server.py ← lib/actions.json: site-check` | людям: P: settings-sitecheck, -partial, -down; T: site-check | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | client_dns_set | инструмент агента → `dns-set` | `goamnezia-mcp/server.py ← lib/actions.json: dns-set` | людям: P: settings-clients, settings-clients-custom; T: dns, dns-custom | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | panel_status | инструмент агента → `miniapp-status` | `goamnezia-mcp/server.py ← lib/actions.json: miniapp-status` | людям: P: settings-access, -off, -nosite, -untrusted, -nobot, -nodomain; T: panel, panel-off | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | panel_domain_enable | инструмент агента → `miniapp-on` | `goamnezia-mcp/server.py ← lib/actions.json: miniapp-on` | людям: P: settings-access-open; T: panel-off → panel-url | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | panel_domain_disable | инструмент агента → `miniapp-off` | `goamnezia-mcp/server.py ← lib/actions.json: miniapp-off` | людям: P: settings-access-close; T: panel-close | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | panel_domain_rotate | инструмент агента → `miniapp-rotate` | `goamnezia-mcp/server.py ← lib/actions.json: miniapp-rotate` | людям: P: settings-access-rotate; T: panel-rotate, panel-url | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | site_alt_svc | инструмент агента → `site-alt-svc` | `goamnezia-mcp/server.py ← lib/actions.json: site-alt-svc` | людям: P: settings-altsvc; T: alt-svc | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | preflight_modes | инструмент агента → `preflight` | `goamnezia-mcp/server.py ← lib/actions.json: preflight` | людям: P: settings-ports-checking, settings-mode; T: preflight | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | lang_set | инструмент агента → `lang-set` | `goamnezia-mcp/server.py ← lib/actions.json: lang-set` | людям: P: settings-lang, -confirm; T: lang | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | clients_list | инструмент агента → `clients-list` | `goamnezia-mcp/server.py ← lib/actions.json: clients-list` | людям: C: connect (каталог по платформам); P: settings-mirror-fetch | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | page_create | инструмент агента → `page-create` | `goamnezia-mcp/server.py ← lib/actions.json: page-create` | людям: P: user-page, -custom, -ready, -forever, -ready-forever, -nodomain, -disabled, -imported; T: page, page-ready, page-custom, page-disabled, page-need-domain, page-ready-imported | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | page_list | инструмент агента → `page-list` | `goamnezia-mcp/server.py ← lib/actions.json: page-list` | людям: P: user — «Страницы подключения»; T: page-list, page-card | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | page_url | инструмент агента → `page-url` | `goamnezia-mcp/server.py ← lib/actions.json: page-url` | людям: P: user-page-url; T: page-card («Отправить ссылку ещё раз») | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | page_revoke | инструмент агента → `page-revoke` | `goamnezia-mcp/server.py ← lib/actions.json: page-revoke` | людям: P: user (Отозвать); T: page-revoke | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | page_revoke_all | инструмент агента → `page-revoke-all` (разрушительный, confirm) | `goamnezia-mcp/server.py ← lib/actions.json: page-revoke-all` | людям: P: user-pages-revoke-all | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | mirror_list | инструмент агента → `mirror-list` | `goamnezia-mcp/server.py ← lib/actions.json: mirror-list` | людям: P: settings-mirror, -empty; T: mirror | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | mirror_fetch | инструмент агента → `mirror-fetch` | `goamnezia-mcp/server.py ← lib/actions.json: mirror-fetch` | людям: P: settings-mirror-fetch; T: mirror-fetch | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | mirror_remove | инструмент агента → `mirror-remove` (разрушительный, confirm) | `goamnezia-mcp/server.py ← lib/actions.json: mirror-remove` | людям: P: settings-mirror-remove; T: mirror (🗑) | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | profile_repair | инструмент агента → `profile-repair` | `goamnezia-mcp/server.py ← lib/actions.json: profile-repair` | людям: P: profiles-repair, overview-alerts; T: profile-repair, start-missing | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | backup_schedule | инструмент агента → `backup-schedule` | `goamnezia-mcp/server.py ← lib/actions.json: backup-schedule` | людям: P: backups (сегмент из 4), backups-schedule-off; T: backup — «Расписание — в SSH-меню» | — интерфейс агента; для людей см. строку моста |
| MCP-инстр. | backup_last | инструмент агента → `backup-last` | `goamnezia-mcp/server.py ← lib/actions.json: backup-last` | людям: P: backups, backups-night-failed; T: backup | — интерфейс агента; для людей см. строку моста |


## 7. Страница подключения (`lib/pages_server.py`)

| Компонент | № | Функция | Источник | Где в дизайне | Статус |
|---|---|---|---|---|---|
| Страницы | G1 | Страница /p-<секрет>/: шапка (имя, «открывается до» / бессрочная, трафик, доступ до) | `lib/pages_server.py:2083 _page; APP_JS until/forever` | C: connect, lifetime=date/short/forever, name=long | ✅ |
| Страницы | G2 | Вкладки устройств: iPhone, Android, Android TV, Apple TV, Windows, macOS, Linux, роутер; выбор по браузеру | `pages_server.py:120 PLATFORMS` | C: platform=auto…router | ✅ |
| Страницы | G3 | Apple TV: официального нет, WG Connect и incy, отправка с iPhone, совет «VPN на роутере» | `pages_server.py:1180 tvos_note; 1609` | C: platform=tvos, device=tv&platform=tvos | ✅ |
| Страницы | G4 | Каталог приложений: официальное/нет, разработчик, лицензия, поддержка 1.0/1.5/2.0/3.1 с версиями, магазины с QR | `lib/clients.json; APP_JS` | C: connect, app=… (14 приложений) | ✅ в EN у роутеров остались «MikroTik (контейнер)», «OPNsense (плагин AmneziaWG)» |
| Страницы | G5 | Ключи по профилям: .conf, QR .conf, vpn://, QR vpn:// частями, «Открыть в приложении», мин. версия | `pages_server.py:2088-2094 key/qr routes` | C: connect, qr=conf/vpn, copy=ok/fail | ✅ |
| Страницы | G6 | Ключ выдан в приложении Amnezia — справка вместо ключа; все профили такие | `APP_JS key_imported` | C: imported, keys=mixed | ✅ |
| Страницы | G7 | Загрузка ключей: загружаю / сервер занят / не загрузились / страница больше не открывается | `APP_JS keys_loading/busy/failed, page_gone` | C: keys=loading/busy/failed/gone | ✅ |
| Страницы | G8 | Приостановлено: лимит / срок / админ | `APP_JS off_quota/off_expired/off_admin` | C: suspended, reason=quota/expired/admin | ✅ |
| Страницы | G9 | Зеркало: скачать с нашего сервера (версия, размер, QR), запросить копию, в очереди, загружается N %, не удалось, выключено | `pages_server.py:2098-2130 mirror routes` | C: mirror=ready/none/queued/downloading/failed/off, qr=mirror | ✅ |
| Страницы | G10 | Режим телевизора с пультом | `APP_JS (device tv)` | C: device=tv, tvFocus | ✅ |
| Страницы | G11 | 503 «сервер занят» при перегрузке моста | `pages_server.py:2060 send_busy` | C: page=busy | ✅ |
| Страницы | G12 | Превью ссылки в мессенджере без ключей | `pages_server.py:131 PREVIEW_RE` | C: page=preview | ✅ |
| Страницы | G13 | Без JavaScript | `pages_server.py:1728 noscript` | C: page=noscript | ✅ |
| Страницы | G14 | RU/EN на странице | `APP_JS S.ru/S.en` | C: lang=ru/en | ◐ переключатель RU/EN 48×36 и вкладки устройств высотой 40 px — меньше 44 px на телефоне |
| Страницы | G15 | Отозвана / истекла / человек удалён → «404» как у сайта | `pages_server.py send_404` | — (по замыслу ничего не рисовать) | — |
| Страницы | G16 | Тема владельца на странице | `нет в продукте (разрыв Р-43)` | C: ideas («нужна доработка сервера») | — вынесено на лист идей |
| Страницы | G17 | Счёт открытий и последнее открытие (видит администратор) | `pages_server.py visits` | P: user (Открытий, Последнее); T: page-list, page-card | ✅ |


## 8. Временная ссылка на файл (`lib/share_server.py`, `lib/share.sh`)

| Компонент | № | Функция | Источник | Где в дизайне | Статус |
|---|---|---|---|---|---|
| Ссылки | H1 | Файл по /d-<секрет>/<имя>-<профиль>.conf/.vpn отдаётся сразу, несколько раз за 5 мин | `lib/share_server.py:458 do_GET; 500 attachment` | C: share=download-android/-ios/-desktop | ✅ |
| Ссылки | H2 | Самоподписанный сертификат в «Только VPN»: предупреждение браузера и отпечаток | `lib/share.sh; share_server.py TLS` | C: share=warning, warning-open; P: user-share-selfsigned; T: share-selfsigned | ✅ |
| Ссылки | H3 | Истекла / снята / чужая / бан подбора → «404» nginx | `share_server.py:78 NGINX_404; 439 send_404` | C: share=notfound | ✅ |
| Ссылки | H4 | Памятка «Как импортировать файл» вместе со ссылкой | `handoff brief S` | C: share=memo; P: user-share-ready («Скопировать памятку»); T: share-ready | ✅ |
| Ссылки | H5 | Предел 10 одновременных ссылок | `lib/share.sh share_limit` | P: user-share-limit; T: share-limit | ✅ |
| Ссылки | H6 | Срок и число скачиваний — настройки окружения (GOAMNEZIA_SHARE_TTL, _MAX_DOWNLOADS) | `lib/share.sh:57, 158` | — | — не интерфейс |
| Ссылки | H7 | Ссылки не переживают перезагрузку сервера | `share_server.py:14` | — | — без экрана |


## Приложение А. Те же 164 функции, что 26.09 (`docs/archive/DESIGN_COVERAGE.md` §2), — оценка 27.09

| Счёт по тем же функциям §2 от 26.09 | Всего | ✅ | ◐ | ✗ | н/п |
|---|---|---|---|---|---|
| 26.09 (итерация 1) | 164 (оцениваются 156) | 25 (16 %) | 45 (29 %) | 86 (55 %) | 8 |
| 27.09 (итерация 2 + 273423c) | 164 (оцениваются 156) | 152 (97 %) | 4 (3 %) | 0 (0 %) | 8 |

| Ф | Функция (26.09) | 26.09 | 27.09 | Где сейчас / чего не хватает |
|---|---|---|---|---|
| Ф1 | Список пользователей со статусами (`user-list`) | ✅ | ✅ | P: users*, users-many (25/50/100), «из приложения Amnezia», подпись+id; сортировки нет |
| Ф2 | Поиск, фильтр, открыть по имени или номеру | ✅ | ✅ | P: поиск, фильтр, ⌘K |
| Ф3 | Карточка пользователя (`user-show`) | ◐ | ✅ | P: user — id, адрес в VPN, рукопожатие и трафик по профилю, причина выключения |
| Ф4 | Добавить пользователя (`user-add`: имя, подпись, профили, лимит, срок) | ✗ | ✅ | P: user-add, -errors, -noprofiles, -busy, -locked; T: add-* |
| Ф5 | Ожидание первого подключения после ключа (до 3 мин) | ✗ | ✅ | P: user-created, -ok, -timeout (три проверки) |
| Ф6 | QR `.conf` | ✅ | ✅ |  |
| Ф7 | QR `vpn://` серией частей | ✅ | ✅ | P: user-vpn |
| Ф8 | Скачать `.conf` | ✅ | ✅ |  |
| Ф9 | `vpn://` текстом, скопировать | ◐ | ✅ | T: key-vpn («длиннее сообщения — отправлен файлом») |
| Ф10 | Текст `.conf` на экране | ✗ | ✅ | P: user-text |
| Ф11 | Линейка и минимальная версия приложения | ✅ | ✅ |  |
| Ф12 | Ключ выключенного — с предупреждением | ✗ | ✅ | P: user-disabled; T: key-disabled |
| Ф13 | Ключ на другой адрес сервера, разово (`user-key --endpoint`) | ✅ | ✅ | P: user-addr; T: key-addr* |
| Ф14 | Свой адрес сервера за человеком (`user-set-address`) | ✗ | ✅ | P: user-pin, user-pin-domain |
| Ф15 | Закреплённый адрес пропал с сервера (`key_host_gone`) | ✗ | ✅ | P: user-pin-gone; T: key-addr-gone |
| Ф16 | Лимит трафика (`user-quota`) | ◐ | ✅ | P: user-quota; T: quota |
| Ф17 | Срок доступа (`user-expiry`) | ◐ | ✅ | P: user-expiry, -past; T: expiry |
| Ф18 | Выключить / включить (`user-disable` / `user-enable`) | ◐ | ✅ | P: user-disabled, user-enable-limit; T: user-off, user-enable-warn |
| Ф19 | Переименовать подпись (`user-rename`) | ✗ | ✅ | P: user-rename; T: rename |
| Ф20 | Выдать ключ ещё на один профиль (`user-add-peer`) | ✗ | ✅ | P: user-addprofile; T: add-profile |
| Ф21 | Отобрать ключ на профиле (`user-del-peer`) | ✗ | ✅ | P: user-removepeer; T: del-profile |
| Ф22 | Перевыпустить ключ на профиле (`user-reissue-peer`) | ✗ | ✅ | P: user-reissue*, T: reissue* |
| Ф23 | Привязать старый ключ подхваченного (`user-attach-key`) | ✗ | ✅ | P: user-attach + 4 отказа; в боте — только server_mismatch |
| Ф24 | Подхваченный из приложения, приватного ключа нет (`has_key=false`) | ✗ | ✅ | P: user-imported, user-mixed; T: user-imported; C: imported |
| Ф25 | Удалить пользователя (`user-del`) | ◐ | ✅ | P: confirm, user-delete-nobackup; T: DELETE mama |
| Ф26 | Трафик человека по профилям, «на связи» | ◐ | ✅ | P: user — трафик по профилю |
| Ф27 | Команды бота `/users`, `/adduser`, `/key` | ✗ | ◐ | T: commands — ответы /adduser, /key, /invite не нарисованы |
| Ф28 | Временная ссылка на файл (`share-create`: `.conf` / `.vpn`, разовый адрес) | ◐ | ✅ | P: user-share*; T: share-* |
| Ф29 | Активные раздачи: список, снять, снять все (`share-list` / `share-revoke` / `share-revoke-all`) | ✗ | ✅ | P: users-shares*; T: shares* |
| Ф30 | Создать страницу подключения (`page-create`: срок, профили, копия) | ✗ | ✅ | P: user-page*; T: page*; выбор профилей страницы — нигде |
| Ф31 | Страницы человека: список, адрес ещё раз, отозвать (`page-list` / `page-url` / `page-revoke`) | ✗ | ✅ | P: блок страниц, user-page-url; T: page-list, page-card |
| Ф32 | Отозвать все страницы (`page-revoke-all`) | ✗ | ✅ | P: user-pages-revoke-all |
| Ф33 | Копии приложений на сервере (`mirror-list` / `mirror-fetch` / `mirror-remove`) | ✗ | ✅ | P: settings-mirror*; T: mirror* |
| Ф34 | Каталог приложений (`clients-list`) | ◐ | ✅ | C: каталог |
| Ф35 | Ключи по профилям: файл, QR, `vpn://`, «открыть в приложении» | ✅ | ✅ |  |
| Ф36 | Устройство по браузеру: 7 вкладок, ТВ по браузеру | ✅ | ✅ |  |
| Ф37 | **Apple TV** (платформа `tvos`, незаконченная волна 1) | ✗ | ✅ | C: platform=tvos |
| Ф38 | Приложения под устройство и линейки (13 в каталоге) | ◐ | ✅ | C: app=… 14 приложений |
| Ф39 | Копия приложения: скачать / запросить / в очереди / загружается N % / ошибка / выключено | ◐ | ✅ | C: mirror=… |
| Ф40 | Статус доступа: трафик из лимита, срок | ✅ | ✅ |  |
| Ф41 | Приостановлено: лимит / срок / админ | ✅ | ✅ | C: suspended — тексты исправлены |
| Ф42 | Ключ выдан в приложении Amnezia — справка вместо ключа | ✗ | ✅ | C: imported, keys=mixed |
| Ф43 | Срок страницы: «открывается до …, VPN работает и после» / бессрочная | ◐ | ✅ | C: lifetime=date/short/forever |
| Ф44 | Страница отозвана / истекла / человек удалён → «404» как у сайта | ✅ | ✅ |  |
| Ф45 | Режим телевизора | ✅ | ✅ | C: device=tv |
| Ф46 | RU/EN, тёмная тема по системе | ✅ | ✅ | тема владельца — лист идей |
| Ф47 | Превью ссылки в мессенджере (оболочка без ключей) | ✗ | ✅ | C: preview |
| Ф48 | Без JavaScript: ссылки на `.conf` | ✗ | ✅ | C: noscript |
| Ф49 | Временная ссылка у получателя | ✗ | ✅ | C: share=warning/download/memo/notfound |
| Ф50 | Профили: состояние, порт, линейка, образ, людей (`profiles-status`) | ✅ | ✅ |  |
| Ф51 | Состояния: работает / перезапускается / остановлен / нет контейнера / Docker не отвечает / не установлен | ◐ | ✅ | P: profiles-states, profiles-docker |
| Ф52 | Установить профиль (`profile-install`: порт, пресет, подсеть) | ✗ | ✅ | P: profiles-install* |
| Ф53 | После установки — выдать ключ всем | ✗ | ✅ | P: profiles-installed «Выдать всем» |
| Ф54 | Сменить порт (`profile-port`) | ◐ | ✅ | P: profiles-port |
| Ф55 | Занять UDP/443 | ✗ | ✅ | P: profiles-443, -443-pro |
| Ф56 | Параметры маскировки: просмотр (`profile-params`) | ✅ | ✅ |  |
| Ф57 | Изменить один параметр (`profile-set-param`) | ✗ | ✅ | P: profiles-param* |
| Ф58 | MTU (`profile-mtu`) | ◐ | ✅ | P: profiles-mtu |
| Ф59 | Подсеть (`profile-subnet`) | ✗ | ✅ | P: profiles-subnet |
| Ф60 | Пресет и перегенерация (`profile-regen`) | ✗ | ✅ | P: profiles-regen |
| Ф61 | Перезапустить (`profile-restart`) | ◐ | ✅ | P: profiles-restart |
| Ф62 | Поднять из копии состояния (`profile-repair`) | ✗ | ✅ | P: profiles-repair |
| Ф63 | Удалить профиль (`profile-remove`) | ✅ | ✅ |  |
| Ф64 | Логи контейнера профиля | ◐ | ✅ | P: logs-awg |
| Ф65 | Проверить обновления Amnezia (`upstream-check`) | ✗ | ✅ | P: updates-checking, updates, -unreachable |
| Ф66 | Обновить ядро профиля (`upstream-update`) | ◐ | ✅ | P: updates-confirm, -done, -failed, -rolledback |
| Ф67 | Откатить (`upstream-rollback`) | ✗ | ✅ | P: updates-rollback |
| Ф68 | Починить на закреплённом образе (`core-update`) | ✗ | ✅ | P: updates-repair |
| Ф69 | Суточная проверка, «доступно обновление» (`upstream-tick`) | ✗ | ✅ | P: updates-badge (помечено «нужна доработка сервера») |
| Ф70 | Версия продукта | ✅ | ✅ |  |
| Ф71 | Обновить goAmnezia | н/п | — | P: settings-about |
| Ф72 | «Что нового» после обновления | н/п | — |  |
| Ф73 | Найти контейнеры приложения (`import-scan`) | ✗ | ✅ | P: import, -scanning, -empty, -noconf |
| Ф74 | Подхватить контейнер (`import-adopt`) | ✗ | ✅ | P: import-adopt, -done |
| Ф75 | Сверить с приложением (`import-reconcile`, apply) | ✗ | ✅ | P: import-reconcile, -insync |
| Ф76 | Сменить режим (`change-mode`) | ◐ | ✅ | P: settings-mode-wizard* |
| Ф77 | Домен (задать / сменить) | ✗ | ✅ | P: settings-domain* |
| Ф78 | Проверка портов 80/443 (`preflight`) | ✗ | ✅ | P: settings-ports-checking |
| Ф79 | Режим и порт VPN не совпадают (`mode_port_mismatch`) | ✗ | ✅ | P: settings-mode-mismatch |
| Ф80 | Шаблон сайта: каталог, поиск, случайный (`change-template`) | ✗ | ✅ | P: settings-template* |
| Ф81 | Свой шаблон по git-адресу | н/п | — | P: settings-about |
| Ф82 | Сертификат: выпуск, продление, почта | н/п | — | P: settings-about, settings-mode-selfsigned |
| Ф83 | Вид и срок сертификата, отпечаток (`status.site`) | ◐ | ✅ | P: settings-mode (вид, до какого, отпечаток) |
| Ф84 | Сайт на запасном самоподписанном (`dns_fallback`) | ✗ | ✅ | P: settings-mode-selfsigned, -done-fallback |
| Ф85 | Проверка сайта снаружи (`site-check`) | ✗ | ✅ | P: settings-sitecheck* |
| Ф86 | Alt-Svc (`site-alt-svc`) | ✗ | ✅ | P: settings-altsvc |
| Ф87 | Панель на домене: открыть / сменить путь / закрыть (`miniapp-on` / `-rotate` / `-off`) | ◐ | ✅ | P: settings-access-open/-rotate/-close |
| Ф88 | Панель недоступна: нет сайта / домена / настоящего сертификата (`miniapp-status`) | ✗ | ✅ | P: settings-access-nosite/-nodomain/-untrusted |
| Ф89 | DNS для клиентов (`dns-set`) | ✅ | ✅ |  |
| Ф90 | Адрес сервера в ключах (`endpoint-set`) | ✗ | ✅ | P: settings-endpoint-* |
| Ф91 | Таблица «Адреса сервера» (`edge-status`) | ◐ | ✅ | P: overview (польз., ✓ ✗ ? —, 5 вердиктов) |
| Ф92 | Один адрес: только адрес и A-запись | ✗ | ✅ | P: overview-single |
| Ф93 | Чужие A-записи, Cloudflare, AAAA, домен без записей | ✗ | ◐ | P: overview-dns, -cf, -noa; нет строки про AAAA-запись не на этот сервер |
| Ф94 | Домен в ключах — адрес выбирает DNS (`domain_endpoint`) | ◐ | ✅ | P: user-addr-domain |
| Ф95 | IP сервера сменился (`server_ip_changed`) | ✗ | ✅ | P: overview-alerts; T: ntf-server |
| Ф96 | Создать бэкап (`backup-create`) | ✅ | ✅ |  |
| Ф97 | Список бэкапов (`backup-list`) | ◐ | ✅ | P: backups (🔒) |
| Ф98 | Восстановить (`backup-restore`) | ✅ | ✅ | P: backups-restore-typed |
| Ф99 | Расписание ночной копии (`backup-schedule`) | ◐ | ✅ | P: backups (сегмент из 4, 03:20) |
| Ф100 | Последняя копия и итог ночи (`backup-last`) | ◐ | ✅ | P: backups, backups-night-failed |
| Ф101 | Ротация: хранится 30 архивов | ◐ | ✅ | «храним 30» |
| Ф102 | Зашифрованный архив (восстановление только в SSH) | ✗ | ✅ | P: backups-encrypted |
| Ф103 | Ночной бэкап не удался (`backup_failed`) | ✗ | ✅ | P: backups-night-failed, overview-alerts |
| Ф104 | Логи по источникам (`logs-tail`: awg1–3, бот, панель, nginx, система, журнал действий) | ◐ | ✅ | P: logs* |
| Ф105 | Размер логов и предел (`logs-status`) | ✗ | ✅ | P: «Место под логи» |
| Ф106 | Подрезать логи сейчас (`logs-enforce`) | ✗ | ✅ | P: logs-trim |
| Ф107 | Диагностика (`quality`) | ✗ | ✅ | P: diag* |
| Ф108 | Статус: контейнеры, порты, версии ядра (`status`) | ◐ | ✅ |  |
| Ф109 | Трафик и онлайн (`traffic`) | ◐ | ✅ | P: traffic, traffic-24 (по часам) |
| Ф110 | Журнал действий «кто что сделал» (`logs-tail mcp`) | ✗ | ✅ | P: logs-audit |
| Ф111 | Здоровье служб: Docker, бот, панель, nginx, агенты, страницы (`server.py:1810`) | ◐ | ✅ | P: overview «Службы» |
| Ф112 | Перезапустить всё | ✗ | ✅ | T: restart-all* |
| Ф113 | О программе: версия, режим, ядро, линейки, лицензия, руководство | ◐ | ✅ | P: settings-about |
| Ф114 | Бот как служба: поставить / статус / логи / перезапуск / удалить | н/п | — | P: settings-about |
| Ф115 | Панель как служба: поставить / статус / логи / токен / удалить | н/п | — |  |
| Ф116 | Вход кодом из бота (запросить, ввести) | ◐ | ✅ | P: login*, отказы; нет code_off_no_token, cooldown, net_limit |
| Ф117 | Вход по логину и паролю | ◐ | ✅ | P: login-pwd* |
| Ф118 | Четыре варианта формы (бот есть/нет × пароль есть/нет) | ✗ | ✅ | P: login-v2…v4 |
| Ф119 | Аварийный токен | ✗ | ✅ | P: login-v4 |
| Ф120 | Вход через SSH-туннель | ◐ | ✅ | P: sys-tunnel |
| Ф121 | Вход в мини-приложение по подписи Telegram | ◐ | ✅ | P: login-miniapp; M: denied |
| Ф122 | Сессия 15 минут, продление кодом, «Выйти» (`server.py:512-516`) | ✗ | ✅ | P: sys-session-* |
| Ф123 | Смена пароля панели | ✗ | ✅ | P: settings-password* |
| Ф124 | Администраторы бота: список, владелец | ✗ | ✅ | P: settings-access; T: admins |
| Ф125 | Первый администратор бота (окно назначения) | ✗ | ✅ | T: claim* |
| Ф126 | «Код из бота» и «Туннель без кода» — только состояние | ◐ | ✅ | P: settings-access (строки состояния) |
| Ф127 | HTTP-транспорт: состояние, где адрес, отпечаток, порт (`mcp-http-status`) | ◐ | ✅ | P: agents-transport-* |
| Ф128 | Включить / выключить транспорт (`mcp-http-on` / `-off`) | ◐ | ✅ |  |
| Ф129 | Выдать доступ (`agent-add`) | ◐ | ✅ | P: agents-add* |
| Ф130 | Показать адрес, токен, команду, QR (`agent-show`) | ✗ | ✅ | P: agents-show (адрес, токен, команда, QR) |
| Ф131 | Список доступов: уровень, срок, последний вызов, адрес, вызовы, состояние (`agents.sh:666`) | ◐ | ✅ | P: agents |
| Ф132 | Включить / выключить доступ | ✅ | ✅ |  |
| Ф133 | Удалить доступ (`agent-del`) | ✅ | ✅ | P: agents-delete (ввод a-3f9c) |
| Ф134 | Перевыпустить секрет и токен (`agent-rotate`) | ✗ | ✅ | P: agents-rotate |
| Ф135 | Выдать токен старому доступу (`agent-harden`) | ✗ | ✅ | P: agents-harden |
| Ф136 | Привязка к адресу (`agent-pin`) | ✗ | ✅ | P: agents-pin |
| Ф137 | Продлить срок (`agent-extend`) | ✗ | ✅ | P: agents-extend* |
| Ф138 | Клон обнаружен — доступ заморожен | ✗ | ✅ | P: agents-card-frozen |
| Ф139 | Вид доступа: постоянный токен / одноразовый ключ / переходник (код 10 мин) | ✗ | ✅ | P: agents-add-connect, -created-rolling, -card-enroll* |
| Ф140 | Уровни агента: чтение без ключей / запись / полный (`goamnezia-mcp/server.py:910`) | ✗ | ✅ | P: agents-add |
| Ф141 | Люди: лимит 80 %, лимит исчерпан, срок скоро, срок вышел | ✗ | ✅ | T: ntf-users |
| Ф142 | Контейнеры: не работает, сторож поднял / не смог, контейнера нет, Docker не отвечает | ✗ | ✅ | T: ntf-containers |
| Ф143 | Адреса: замолчал, напоминание, снова принимает | ✗ | ✅ | T: ntf-addr |
| Ф144 | Сертификат: истекает / не читается (Let's Encrypt и самоподписанный) | ✗ | ✅ | T: ntf-cert |
| Ф145 | IP сервера сменился; часы сервера прыгнули | ✗ | ✅ | T: ntf-server |
| Ф146 | Ночной бэкап не сделан | ✗ | ✅ | T: ntf-server |
| Ф147 | Доступно обновление Amnezia | ✗ | ✅ | T: ntf-server |
| Ф148 | Агенты: впервые использован, новый адрес, без токена, предел частоты, истекает, истёк, клон (заморожен / не уд | ✗ | ✅ | T: ntf-agents, -2, ntf-clone |
| Ф149 | Язык продукта для всех (`lang-set`) | ✗ | ✅ | P: settings-lang; T: lang |
| Ф150 | Язык бота «свой» (`/lang`) | ✗ | ✅ | T: lang |
| Ф151 | Язык панели в браузере | ✗ | ✅ | P: шапка RU/EN, settings-lang |
| Ф152 | Английские версии экранов | ◐ | ◐ | панель — 4 состояния с русским; бот — 140 из 179 сцен с русским; страницы — 2 строки |
| Ф153 | Внешний вид (новое) | ✅ | ✅ | P: appearance («сохраняется на сервере») |
| Ф154 | Главное меню бота с разделами | ◐ | ✅ | T: start* |
| Ф155 | Состояние: Статус, Трафик, Логи, Адреса | ✗ | ✅ | T: state* |
| Ф156 | Обслуживание (бот) | ✗ | ✅ | T: maint |
| Ф157 | Настройки (бот) | ✗ | ✅ | T: settings |
| Ф158 | Установка из бота, если продукт не установлен | ✗ | ✅ | T: install, install-done |
| Ф159 | Фразы подтверждения и отмена (60 с) | ◐ | ✅ | T: все 10 фраз, неверная, 60 с, отмена |
| Ф160 | Ввод текстом с ожиданием 10 минут | ◐ | ✅ | T: flow-expired, rename-expired |
| Ф161 | Занято и сбои в боте (`_action_busy`, `bot.py:3540`) | ✗ | ◐ | T: busy, queued — расходятся с продуктом (см. B80) |
| Ф162 | Мини-приложение: цвета Telegram, тема «Telegram» | ✅ | ✅ | M: panel на итерации 2, tgScheme day/night |
| Ф163 | Мастер первой установки (режим, домен, профили, порты) | н/п | — | T: install; P: overview-fresh |
| Ф164 | Удалить всё | н/п | — | P: settings-about; T: wipe |


## Приложение Б. Известные хвосты итерации 2 — исправил ли их Codex 273423c

| Хвост | Как проверено | Итог |
|---|---|---|
| D2-02 две части панели не сведены | `Panel.dc.html` — общий вход: по имени состояния подключает `Panel-core` (128 состояний: обзор, люди, карточка, протоколы, обновления, импорт, трафик) или `Panel-settings` (161: настройки, агенты, бэкапы, логи, диагностика, вход, системные); прямой `Panel-settings.dc.html?screen=…` переводит на общий вход с теми же параметрами; Back, тема и язык общие | **исправлено** |
| D2-03 (R5) английский в диалогах и тостах | весь видимый текст 285 состояний в EN (компьютер и телефон), а не только `role=dialog` | **почти**: русский остался в `profiles-installing`, `profiles-updating` (название действия на плашке «Running: Установка профиля…»), `profiles-locked` (текст «занято»), `import-done` (итог подхвата). Бот в EN не переведён: 140 из 179 сцен (кнопки, карточки). Страница подключения: «MikroTik (контейнер)», «OPNsense (плагин AmneziaWG)» во вкладке «Router» |
| D2-04 (R10) цели касания < 44 px на телефоне | все интерактивные элементы, телефон 360×800 | **панель, бот и мини-приложение — исправлено** (единственные «мелкие» — поля поиска высотой 20 px внутри подписи 294×44, это одна цель). **Страница подключения — нет**: переключатель RU/EN 48×36, вкладки устройств высотой 40 px |
| D2-09 мини-приложение на панели 1-й итерации | `view=miniapp`, экраны `settings-access`, `agents`, `user-add` (есть только в итерации 2), RU/EN, `tgScheme=day/night` | **исправлено** |
| D2-08 (R17) мелкие формулировки | исходного списка R17 в дереве нет (так же пишет Codex); проверены правила текстов по всем отрисовкам | **остаток**: бот `reissue-fail` — «254 пиров» (в продукте «всего мест: 254»); бот показывает версию v0.4.5 в 14 сценах, панель — 0.4.6; «Вы назначены администратором!» с восклицанием (так и в продукте, `lang/ru.json` admin_claimed_fmt); «Endpoint» — только в одобренной форме «Адрес сервера в ключах (Endpoint)» |
| D2-05 QR на телефоне | не перепроверялось (не входит в список владельца); flow-проверка Codex: QR на y=266…486 | принято по отчёту Codex |
| D2-06 даты примеров | 26.09 встречается только как история (архивы, выдача доступов, события) | в порядке |
| D2-07 порт после установки | `profiles-installed` пишет выбранный порт | в порядке |

## Приложение В. Состояния, которых нет в одном из прототипов, но они есть в продукте другого интерфейса

Это не ошибки дизайна: у продукта функция живёт не во всех интерфейсах, и дизайн это повторяет. Годится для решения
владельца «выравнивать ли».

| Функция | Панель (дизайн) | Бот (дизайн) | Продукт |
|---|---|---|---|
| Свой адрес за человеком (`user-set-address`) | есть | нет | SSH, MCP |
| Продлить доступ агента (`agent-extend`) | есть | нет, уведомление шлёт в SSH | SSH |
| Отозвать все страницы человека (`page-revoke-all`) | есть | нет | SSH, MCP |
| Подрезать логи (`logs-enforce`) | есть | нет | MCP |
| Расписание ночной копии (`backup-schedule`) | есть | «в SSH-меню» | SSH, MCP |
| Перезапустить всё | нет | есть | SSH, бот |
| Ожидание первого подключения после ключа | есть | нет | SSH |
