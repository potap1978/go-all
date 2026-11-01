# Быстрая настройка: контракт API для фронтенда

2026-10-09. Код — `lib/kaskad/setup/web.py` (маршруты), `steps.py` (шаги), `model.py` (состояние), `jobs.py` (ход
долгих шагов), `product.py` (объект продукта). Мок для работы без сервера — `lib/kaskad/panel/static/setup/dev-mock-setup.js`.
Устройство бэкенда и сертификат на IP — `docs/k3/SPEC-setup.md`. Экраны — `k3/design/quick-start-design-v2/`
(принят владельцем). Страница мастера — `lib/kaskad/panel/static/setup/` (§8).

## 0. Общие правила

- **Адрес.** Всё живёт под секретным путём: `https://<IP или домен>:<порт панели>/setup-<секрет>/`. Страница —
  `<база>/`, статика — `<база>/static/setup/…`, API — `<база>/api/setup/…`. Фронтенд берёт базу из `location.pathname`
  и пишет только относительные адреса (`api/setup/state`, `static/setup/app.js`).
- **Ответы** — JSON: `{"ok": true, …}`. Ошибка запроса (не шага) — HTTP 4xx/5xx с `{"ok": false, "error": "<текст для
  человека>", "code"?, "data"?}`, как у панели.
- **Изменения** (POST) — с заголовками `X-Kaskad-Admin: 1` и `Content-Type: application/json`. Без первого — 403.
  Запрос с чужим Origin — тоже 403.
- **Сессия** — cookie `kaskad_setup` (HttpOnly, браузер шлёт его сам).
- **Тексты** приходят готовые, на языке страницы: клиент шлёт `X-Kaskad-Lang: ru|en` в каждом запросе, сервер
  отвечает через каталог `kaskad.i18n` (3.3.2; без заголовка — язык сервера `KASKAD_LANG`). Фронтенд их показывает и
  не собирает фразы из кодов. Коды (`code`, `reason`) нужны, чтобы выбрать экран и кнопку.
- **Язык** — один на сервер (`KASKAD_LANG`, выбор при установке): сервер подставляет его в `<html lang>` страницы
  мастера; словарь `setup-i18n-<язык>.js`. Смена — `POST api/setup/lang {lang: ru|en}` (только с сессией мастера,
  затем перезагрузка страницы); то же в панели — `POST /api/lang`.
- **Секреты** (ключи, токены, пароли) — только в теле POST. Шаги с `secret: true` ничего из тела не сохраняют и не
  пишут в журнал. Ответ шага `issue` содержит настройки устройства: не кэшировать, не класть в localStorage.

### 0.1 Статусы HTTP

| HTTP | Когда | `data` |
|---|---|---|
| 200 | успех запроса. **Итог шага — в теле** (`state`), даже если шаг не удался | — |
| 202 | долгий шаг запущен заданием | `job` |
| 400 | тело не JSON / не те поля | — |
| 401 | нет сессии | `{"auth": "required", "reason": "link" \| "password"}` — показать вход по ссылке из SSH или форму пароля |
| 401 | `login`: неверный пароль | `{"auth": "failed", "left": N}` |
| 403 | нет `X-Kaskad-Admin` / чужой Origin | — |
| 404 | чужой путь, мастер закрыт («Готово», `setup off`), другой IP у привязанной ссылки, нет шага | — |
| 409 | идёт другое задание (`data.job`, `data.step`); этап ещё впереди (`reason: stage`); пароль не задан (`reason: password_first`) | см. слева |
| 410 | ссылка истекла (24 ч) | `{"reason": "expired", "cmd": "kaskadctl setup link"}` |
| 429 / 503 | `login`: исчерпаны попытки / занят | `{"retry_after": N}`, заголовок `Retry-After` |
| 503 | `events`: открыто больше 4 потоков | `{"poll": "/api/setup/job"}` — перейти на опрос |

## 1. Вход (этап 0)

### 1.1 Ссылка

`GET <база>/?t=<токен>` → 303. Фронтенд этот адрес не вызывает — его открывает человек.

| Location | Смысл | Экран |
|---|---|---|
| `<база>/` + cookie | вошёл | приветствие и пароль |
| `<база>/?link=used` | токен уже использован | «Ссылка одноразовая — уже открыта. Запросите новую в SSH: `kaskadctl setup link`» |
| `<база>/?link=revoked` | пароль уже задан, токены погашены | форма пароля |
| `<база>/?link=expired` | истекла | `sys=expired` дизайна |
| `<база>/?link=unknown` | такого токена нет | как `used` |
| `<база>/?link=limit` | 10 неверных ссылок за 15 мин с адреса | «Слишком много попыток — подождите 15 минут» |

### 1.2 `GET api/setup/session` — кто я (без сессии тоже 200)

```json
{"ok": true, "auth": true, "via": "link" | "password", "reason": null | "link" | "password" | "expired",
 "password_set": false, "cmd": "kaskadctl setup link", "ssh": "gokaskad → «Быстрая настройка»",
 "name": "Kaskad PRO", "login": "admin" | null, "expires_in": 86000}
```

`reason`: `link` — войти можно только по ссылке из SSH; `password` — показать форму пароля; `expired` — экран
«Ссылка больше не действует».

### 1.3 `POST api/setup/login` `{"password", "login"?}`

Только после шага «Пароль». Ответ — 200 + cookie. Ошибки — 401 / 409 / 410 / 429 (§0.1).

`POST api/setup/logout` — выход.

`POST api/setup/lang {lang: "ru"|"en"}` — язык сервера (3.3.2): `{ok, lang}`; без сессии мастера — 401, другой язык —
400.

### 1.4 Пароль панели — шаг `password` (§3)

- Правила сервера: **10–200 знаков**, без пробелов по краям и управляющих символов. Шкала надёжности на клиенте — только
  подсказка; кнопка «Начать» доступна с 10 знаков.
- Отказ: `state: error`, `error.code: weak`, `error.field: password`, текст сервера — показать под полем.
- Успех: новая cookie (сессия «password»). Все одноразовые ссылки погашены, этап → первый этап рельса.

### 1.5 `GET api/setup/ipcert` — плашка защищённого соединения

```json
{"ok": true, "state": "none" | "valid" | "expiring" | "expired" | "other_ip", "ip": "203.0.113.67",
 "not_after": "2026-10-16T08:00:00Z", "hours_left": 159.6, "days_left": 6.6, "staging": false,
 "serving": true, "domain": false, "fingerprint": "" | "AB:CD:…",
 "last_try": {"at", "ok", "reason": "port80" | "challenge" | "limit" | "net" | "private" | "profile" | …,
              "text", "retry_after"} | null,
 "text": "Соединение защищено сертификатом Let's Encrypt на IP · действует ≈ 6 дней, продлевается сам."}
```

Если `serving: false` и `domain: false`, в `text` — причина и «браузер покажет предупреждение один раз», а
`fingerprint` — отпечаток самоподписанного сертификата. При `domain: true` страница, открытая по IP, получает
сертификат на IP (выбор по SNI, `SPEC-setup.md` §1.4) — и `text` говорит о нём, а не о доменном.

### 1.6 `GET api/setup/net` → `{"online": bool, "every": 10}`

Плашка `sys=offline`: «У сервера нет интернета». Опрашивать раз в 10 с, пока плашка видна; `?now=1` — «Проверить
сейчас».

## 2. Состояние, продукт, рельс

### 2.1 `GET api/setup/state`

```json
{"ok": true, "status": "active", "active": true, "expires": 1791200000, "expires_in": 86000, "bind": false,
 "password_set": true, "mode": "node" | "classic" | null,
 "rail": {"welcome": {"kind": "welcome", "title": "Вход", "sub": "…", "min": 1, "range": [1, 1], "n": 0, "state": "done"},
          "stages": [{"kind": "import", "title": "Импорт", "sub": "Что уже есть на сервере", "min": 2, "range": [1, 2],
                      "n": 1, "state": "done" | "current" | "ahead" | "skipped"}, …],
          "current": "protocols", "position": 2, "total": 6, "left_min": 33, "left": [11, 33]},
 "answers": {"protocols": ["wg", "awg2"], "exits": "exits", "device": "d-…", …},
 "steps": {"<шаг>": {"state", "at", "text", "data"}},
 "job": <задание §4> | null,
 "product": <§2.2>}
```

- Рельс («Шаг 2 из 6 · ≈ 11–33 минуты осталось») целиком считает сервер: `position`, `total`, `left` — диапазон
  минут (сумма `range` текущего и следующих этапов, HANDOFF v2 §9), `left_min` — его верхний край. `range` этапа —
  «≈ 1–2 мин» рельса и шапки, `min` — его верхний край.
- Классический режим — свой рельс (решение владельца §12): `import, forward, domain, done` — Вход → Импорт →
  Пробросы → Домен → Готово. Карточки режима стоят на втором этапе обоих рельсов: шаг `mode` переводит текущий этап
  `protocols` ↔ `forward`.
- Кликабельны этапы `done`, `skipped` и `current`. Этап `ahead` открыть нельзя: сервер ответит 409.
- `steps` — последний итог каждого шага: после перезагрузки страницы экран восстанавливается из него.

### 2.2 `GET api/setup/product` — объект продукта (HANDOFF §4)

```json
{"id": "kaskad", "name": "Kaskad PRO", "version": "3.2.23", "logo": "static/logo.svg",
 "accent": "#45D0C0", "accentLight": "oklch(.53 .17 288)", "hue": 288,
 "cmd": "kaskadctl setup link", "ssh": "gokaskad → «Быстрая настройка»",
 "modes": ["classic", "node"], "mode": "node",
 "welcome": {"kind": "welcome", "title": "Вход", …},
 "steps": [{"kind": "import", "title": "Импорт", "sub": "…", "min": 2}, …],
 "videos": {"import": {"title", "dur": "1:10", "src": null, "chapters": [["0:00", "Что мастер нашёл", "found"], …]}, …},
 "extras": [{"id": "bot" | "mcp" | "sub" | "backup", "title", "desc", "hl"}],
 "devices": ["iphone", "android", "windows", "mac", "router"],
 "device_platform": {"iphone": "ios", "router": "keenetic", …},
 "platforms": {"ios": "iPhone и iPad", … 14 систем},
 "protocol_cards": {"awg2": {"why", "tags", "mb", "on"}, …}, "base_mb": 40}
```

- `kind` выбирает шаблон шага: `import`, `protocols`, `forward` (классический режим), `exits`, `domain`, `extras`,
  `done`.
- Видео — заглушки (`src: null`): показывать карточку «Ролик готовится — шаг проходится и без него».

### 2.3 `POST api/setup/stage` `{"kind", "action": "open" | "done" | "skip"}`

Управляет рельсом: «Назад», клик по пройденному этапу, «Пропустить», «Дальше».

```json
{"ok": true, "current": "exits", "rail": {…}}
```

Отказ — 409 с `data.reason = stage`. Нельзя: этап впереди, пропустить «Готово», любой этап до пароля.

## 3. Шаги

`POST api/setup/step/<имя>` с телом-объектом.

### 3.1 Ответ шага

Короткий шаг отвечает 200 сразу:

```json
{"ok": bool, "step": "<имя>",
 "state": "ok" | "partial" | "error" | "skipped" | "offline" | "need_input" | "checking" | "unavailable",
 "text": "слова для человека", "items": [<строки §4>], "data": {…},
 "error": {"code", "text", "field"?, "action"?: {"step"?, "stage"?, "label", …параметры повтора}}?}
```

Долгий (`long`) отвечает **202** `{"ok": true, "step", "job": {…}}` — дальше §4.

| state | Экран дизайна |
|---|---|
| `ok` | успех |
| `partial` | частичный успех: «2 из 3», жёлтая строка — `items` и `data.failed` |
| `error` | ошибка: `error.text` — что случилось и что сделать; `error.action` — кнопка исправления (повторить шаг с этими полями, перейти на этап) |
| `need_input` | нужен ответ человека: поле `error.field` или подтверждение `error.action` |
| `checking` | ещё ждём: DNS, подключение; опросить снова |
| `offline` | у сервера нет интернета — плашка `sys=offline` |
| `unavailable` | функции ещё нет в этой версии — «появится в этой версии» (`error.code: soon`) |
| `skipped` | шаг пропущен |

Общие коды `error.code`:

| Код | Смысл |
|---|---|
| `password_first` | сначала пароль |
| `bad_input` | не то поле (`field`) |
| `conflict` / `port_busy` | порт или правило заняты: показать текст, кнопка «всё равно» из `action` |
| `busy` | держит блокировку другой процесс |
| `unsupported` | здесь не работает |
| `offline` | нет интернета |
| `soon` | функции ещё нет |
| `internal` | внутренняя ошибка |
| `interrupted` | задание прервалось — шаг повторяемый |

### 3.2 Каталог шагов (`GET api/setup/steps` — тот же список с флагами)

Флаги в таблице: **Д** — долгий (задание, 202), **С** — секретный (ничего из тела не сохраняется), **Н** — нужна сеть.

#### Этап 0 — Вход

| Шаг | Флаги | Тело | `data` ответа и состояния |
|---|---|---|---|
| `password` | С | `{password, login?}` | `{login, revoked_links}`. `error.code`: `weak` |

#### Этап 1 — Импорт

Сначала предпросмотр (`scan`), потом применение (`import`) и «вернуть как было» (`import-undo`).

| Шаг | Флаги | Тело | `data` ответа и состояния |
|---|---|---|---|
| `scan` | | `{}` | см. ниже |
| `import` | Д | `{force?}` | строки `forwards`, `bot`, `egress`, `w0…` (замечания — `warn`). `data: {imported, backup, warnings, undo}`. `partial`, если есть замечания. `error.code`: `rolled_back` (проверка не прошла — всё вернулось), `error` |
| `import-undo` | Д | `{}` | вернуть пробросы в iptables (формат 2.13) — доступно до конца мастера. `error.code`: `nothing` |
| `backup-open` | С | `{file: <текст .kbk>, password}` · `{ticket}` · `{drop: true}` | копия расшифрована и разобрана, **система не меняется** (kaskad.backup.unseal + preview — тот же предпросмотр, что у панели). Расшифрованная копия ждёт переноса в `/run/kaskad/setup/backup-<ticket>.json` (0600, каталог 0700, tmpfs) не дольше 15 минут; пароль не хранится нигде. `data: {ticket, expires, created, version, parts: [{part, title, action, text}], lost: [{part, what, n, text}], changed: [{part, what, text}], empty, confirm, warnings, blockers, address}` — `lost`: что пропадёт с сервера (люди и устройства, созданные после копии, протоколы, выходы, подписки, пробросы, которых нет в копии), `changed`: что заменится (маршрут по умолчанию, цель проброса, домен, бот), `confirm`: перенос только с подтверждением. `{ticket}` — предпросмотр заново без пароля, `{drop}` — закрыть копию. Файл копии — до 16 МБ (как в панели): тело этого шага сервер читает до 20 МБ только при живой сессии мастера (`panel/server.py` `SETUP_BIG_BODY_PATHS`), остальные шаги — до 1 МБ. `need_input` — есть препятствия (`blockers`). `error.code`: `password` (`field: password`), `bad_input`, `expired` |
| `import-file` | Д С | `{kind: "awg", doc: <JSON файла gokaskad → 17 экспорт AmneziaWG>}` или `{kind: "backup", ticket, confirm?}` | awg: `{inbounds, users, devices, warnings}`; backup: перенос копии, открытой `backup-open` (kaskad.backup.apply, снимок до переноса) — `{parts, backup (снимок), warnings, text, undo}`. `error.code`: `preview_first` (файл без билета — сначала `backup-open`), `expired` (билета нет или прошло 15 минут), `confirm` (что-то пропадёт или заменится, а `confirm` не `true`), `changed` (сервер изменился после предпросмотра — `backup-open {ticket}` ещё раз) |

`data` шага `scan`:
- `found: [{id, title, detail, move, done?, upload?, available?}]` — карточки:
  - `k2` — Kaskad 2.x / бесплатный: пробросы, бот, выход, мониторы. `move` — есть что переносить, `done` — уже
    перенесено;
  - `amnezia`, `xui`, `wg`, `goamnezia` — «не трогаем»;
  - `awgfile` — загрузка файла экспорта AmneziaWG;
  - `backup` — копия настроек (ветка `k3-feat`, в 3.3.0 — есть).
- `clean: bool` — «Чистый сервер, начинаем с нуля»: нет ни карточек выше, ни своих настроек (`here.empty`).
- `here: {items: [{part, what, n, text}], empty, text}` — что уже есть в этом Kaskad: люди, устройства, протоколы,
  выходы, подписки выходов, пробросы, домен, бот, ключи ИИ-агентов (kaskad.backup.here_doc). Не пусто — карточка
  «На сервере уже есть: …» вместо «Чистый сервер» (T-M6-qs330 №4).
- `plan: {summary, preview, imports, blockers}` — что перенесётся и что останется: чужие, затенённые и дубли из
  `summary`; `preview` — готовый текст.
- `checked` — что проверяли.
- `undo` — доступно «Вернуть как было».

#### Этап 2 — Режим и протоколы

| Шаг | Флаги | Тело | `data` ответа и состояния |
|---|---|---|---|
| `mode` | | `{mode: "node" \| "classic"}` | `{mode, rail}` — рельс меняется: у классического нет «Протоколов», «Выходов» и «Дополнительно», есть «Пробросы»; текущий этап `protocols` ↔ `forward`. Отметки этапов другого режима в `stages` остаются (вернулись — они на месте). Страница шлёт шаг только по «Дальше»: щелчок по карточке — выбор на странице (T-M6-qs330b №18) |
| `protocols-plan` | | `{}` | см. ниже |
| `github-key` | С Н | `{key, force?}` | **запасной путь**, поле показывать только при `github.ask`. `ok` — сохранён. `error.code`: `bad_format`, `bad` (GitHub не принял), `net` (GitHub не ответил: `action {force: true, label: "Сохранить без проверки"}`). `partial` — сохранён без проверки |
| `protocols` | Д Н | `{kinds: [...], ports?: {kind: port}, force?: {kind: true}, awg_engine?: "kernel" \| "program"}` | см. ниже |

`data` шага `protocols-plan`:
- `protocols` — список **из реестра входов**. Сейчас там `wg, awg1, awg2, awg3, l2tp, pptp, sstp, ikev2, openvpn,
  ocserv, socks5`; вход `vless` появится сам после ветки `k3-vless`. Поля карточки:
  `{kind, title, why, tags, mb, on, awg, supported, why_not, warning, installed, kept, needs_core, needs_key, build,
  packages, port: {field, protos, port, default, conflicts: [{kind, detail}], free} | null, fixed: [{proto, port,
  conflicts}]}`.
  - Занятый порт: в `port.conflicts` — кто занял, в `port.free` — какой предложить.
  - `supported: false` — «Недоступно: why_not».
- `defaults` — отмечены сразу (решение владельца): `awg2`, `awg3`, `wg`, `l2tp`.
- `module: {can, reasons, codes, build, text}` — модуль ядра AmneziaWG. «Соберём модуль — 1–3 минуты» или «модуль
  нельзя (контейнер, Secure Boot, нет заголовков) — будет программа».
- `github: {state: "ok" | "none" | "bad" | "unchecked", source, from, ask, text}` — найденный ключ проверяется **молча**
  одним запросом. Работает — `ask: false`, вопроса нет; в сводке — строка «Ключ GitHub: найден, из команды
  установки». Поле ключа — только при `ask: true`: ключа нет или он не подошёл.
- `gateway: {supported, reason, packages}`, `base_mb`.

Строки шага `protocols`:
- `gateway` (если шлюз выключен);
- `module` («собираю модуль…» → `ok` / `warn`: «модуль нельзя: …» или «не собрался — на программе»);
- по строке на каждый вход: `ok` «установлен · порт N», `err` с причиной.

`data: {installed, failed: [{kind, code: "port_busy" | "error", text}]}`. Итог: `ok`, `partial` или `error`.

#### Классический режим — проброс

| Шаг | Флаги | Тело | `data` ответа и состояния |
|---|---|---|---|
| `forward-check` | | `{to, to_port, proto?}` | `ok` «192.0.2.4:443 отвечает · 38 мс» `{checked, ms}`. `error.code: unreachable`. UDP не проверяется |
| `forward` | | `{proto, port, to, to_port, name?, force?, force_443?}` | см. ниже; в `answers.forward` — `{proto, port, to, to_port}` для финала и сводки |

Ответы шага `forward`:
- 443/tcp, пока сайт не отменён: `need_input` с `error.code: port443_site` — «займёт порт сайта»; кнопка
  `action {force_443: true}`.
- Порт занят: `error.code: conflict`, кнопка `action {force: true}`.

#### Этап 3 — Выходы

| Шаг | Флаги | Тело | `data` ответа и состояния |
|---|---|---|---|
| `exits-choice` | | `{choice: "direct" \| "exits" \| "later"}` | см. ниже |
| `exits-preview` | С | `{text}` (вставка, текст файла) | `{items: [{id, kind, title, where, name, in_pool, problem?}], subscription, module_build}`. Для подписки: `sub_available`, `sub_text` |
| `exits-add` | Д Н С | `{text}` | см. ниже |
| `exits` | | `{}` | `{exits: [{id, name, kind, title, where, enabled, usable, handshake, text, state: "ok" \| "err" \| "idle"}], alive, total, route: {via, fail}, subs}`. Опрашивать раз в 10 с, пока экран открыт |
| `exit-del` | | `{id}` | убрать ошибочный ключ |
| `route` | | `{via: "pool" \| "direct" \| "block" \| "exit:<id>", fail?: "block" \| "direct"}` | см. ниже |

Варианты `exits-choice`:
- `direct` — маршрут «напрямую».
- `later` — этап помечается пропущенным (DECISIONS-3.3.0 D1): если выходов нет — маршрут «напрямую» и честная
  пометка в `text` («Пока интернет у устройств идёт напрямую, с IP этого сервера…»); выходы уже есть — маршрут не
  меняется. Выходы, добавленные потом, получают «Блокировать» при отказе (шаг `route` и панель — fail-closed).

Шаг `exits-add`:
- Строки: `module` (первый AmneziaWG-ключ собирает модуль — «Собираю модуль ядра — 1–3 минуты»), `add`, `k-<id>` на
  каждый ключ (`ok` добавлен / `warn` уже в пуле / `err` не добавлен).
- `https://…` — подписка: тонкая прослойка к `gw exit-sub add`, без команды — `unavailable`.
- `error.code`: `conflict` (уже в пуле), `busy`, `github_key` (нужен ключ GitHub, `action {step: "github-key"}`).

Шаг `route`:
- **`fail` по умолчанию — `block`** (Kaskad не выпускает трафик в обход выходов).
- «Идти напрямую» — только явным выбором, в `text` предупреждение.

#### Этап 4 — Домен и сайт

| Шаг | Флаги | Тело | `data` ответа и состояния |
|---|---|---|---|
| `domain` | | `{name, zone?}` · `{plan: "none"}` (без домена) · `{clear: true}` | имя — в `answers.domain` (страница восстанавливает этап после перезагрузки), `zone` — в `answers.domain_zone` (см. «Зона» ниже). При работающем домене новое имя только проверяется: `answers.domain` остаётся рабочим, попытка — в `answers.domain_try` `{name, state: ok\|wait\|none\|elsewhere\|cloudflare\|error, why, zone, at}` (подсказка «Последняя проверка: …», 3.3.1). DNS-проверка сразу: `{dns, dns_state, ready, a, own, advice, checked_at, hint_after: 900, affected, warnings, reissue}` |
| `dns` | Н | `{}` · `{name, zone?}` | см. ниже |
| `cert` | Д Н | `{}` · `{name, zone?}` | см. ниже |
| `site` | Д Н | `{template?: id \| "random"}` | nginx, сайт и панель на домене. `data.panel_url` — **новый адрес панели**: показать с «Скопировать» и «сохраните новый адрес». `data.warnings` — плашкой «Внимание», `data.reissue` `[{id, name}]` — кому выдать настройки заново (кнопка «Выдать заново», 3.3.1; так же у `cert` и `domain`). `error.code`: `neighbour`, `port443`, `port80`, `no_domain` |
| `site-email` | | `{email}` (`""` — убрать) | почта Let's Encrypt с этапа «Домен и сайт» (3.3.2 №5) — та же функция, что `kaskadctl site email` и панель (site.ops.set_email: есть аккаунт certbot — `certbot update_account`, иначе почта пойдёт в следующий выпуск). `data: {email, warnings}`; `partial` — Let's Encrypt не принял почту сейчас. `error.code`: `bad_input` (`field: email`). Предупреждение «почта не задана» шаг `cert` отдаёт отдельно: `data.email: {need, text}` (не в `warnings`); факт — `state.site.email` (bool) |
| `templates` | | `{category?, query?, page?}` | `{items, page, pages, per_page, total, category, query, categories: [{id, title, count}]}` — страница каталога (по 25) с категориями для фильтра; `items[].gone` — удалён автором (серый, не выбрать). Каталог (14 МБ, 33 тыс.) панель не разбирает: читает указатель на диске, а сам указатель (и каталог, если его нет) готовит отдельный процесс `kaskadctl site templates-index` в `systemd-run --scope` (T-M6-qs330 №3). `error.code`: `github_key` (каталога нет, нужен ключ GitHub, `action {step: "github-key"}`), `catalog_damaged`, `unknown_category`, `net` |
| `template` | Д Н | `{template: id \| "random"}` | `{id, name, category, fallback}`. «Подобрать автоматически» — `random`, «Другой» — ещё раз `random` |

Зона (`zone`, 3.3.4, REV-334 №4) — необязательное поле тел `domain`, `dns` и `cert` с именем: домен, как его вписали в
поле «Ваш домен» мастера (без поддомена). По нему страница после перезагрузки делит имя на «Поддомен» и «Ваш домен» без
догадок (`example.co.nz` больше не делится на «example» и «co.nz»).
- Сервер (`steps._zone_of`) берёт `zone`, только если имя равно ей или на одну часть длиннее (`vpn` + `.` + `zone`) и в
  `zone` есть точка; иначе — пустая строка. Длиннее 253 знаков или не строка — `bad_input` (`field: zone`).
- Где хранится: `answers.domain_zone` — зона имени из `answers.domain` (пустая — ключ снимается); у попытки рядом с
  работающим доменом — `answers.domain_try.zone`. Когда `answers.domain` сменяется, `domain_zone` прежнего имени
  снимается.
- Кто пишет: `domain` с именем; `dns` — только с новым именем (с именем текущего домена ничего не пишет, у узлов до 3.3.4
  зона так и не появится); `cert` — только при смене работающего домена (`answers.domain_zone` после выпуска,
  `domain_try.zone` при отказе). Те же имя и зона повторно (опрос раз в 10 с) ничего не пишут.
- Зоны нет (имя записано до 3.3.4, задано не мастером, старая страница из кэша) — страница делит по запасному правилу:
  три и больше частей — первая поддомен, кроме зон второго уровня вида `example.co.uk`. Миграция не нужна.

Шаг `dns`:
- Опрос раз в 10 с, пока `state: checking`.
- `dns`: `ok`, `wait` («Жду DNS… обычно 1–15 минут»), `cloudflare` (оранжевое облако), `elsewhere` (A-запись ведёт на
  чужой IP), `none` (записи нет). `advice` — готовый текст.
- Через `hint_after` секунд ожидания — блок подсказок «Самое вероятное — сверху».

Шаг `cert`:
- Строка `cert`. `data: {cert: {names, not_after, days_left, staging, issuer}, use_impact: {sstp: N, ocserv: N},
  consumers, served}`. `served` (T-M6-qs330b №4): `true` — панель уже отвечает имени домена его сертификатом (шаг
  ждёт этого до 10 с рукопожатием с самим собой, на трети срока ещё раз шлёт панели SIGHUP); `false` — всё время
  прежний; `null` — проверять нечего (панель не открыта, нет рукопожатия). `use_impact` — сколько устройств SSTP и OpenConnect получат новые настройки.
- `error.code`: `port80` (кто держит порт 80), `limit` (+ `error.retry_after`), `net`, `dns`, `apt`.

Переезд мастера на домен (3.3.0, T-M6-qs330 №2) — `POST api/setup/move` `{}` (сессия, `X-Kaskad-Admin`):
`{"ok", "url": "https://<домен>:<порт>/setup-<секрет>/?t=<токен>", "clean": "<то же без токена>", "host",
"expires_in"}`. Токен одноразовый, живёт 15 мин (не дольше ссылки), сессия «по паролю» на домене остаётся «по
паролю»; устаревший или повторный — `?link=revoked` (вход паролем). `409`: `no_domain` (домен не задан),
`not_public` (панель не открыта из интернета). Страница берёт токен **до** шага `cert`, пока адрес по IP открывается
наверняка; после `cert: ok` показывает «Мастер переезжает на <домен>» и через 6 с открывает `url` (токен берёт свежий,
если связь есть; «Остаться здесь» отменяет). Перед самим переходом (T-M6-qs330b №4) страница спрашивает
`GET api/setup/move-ready` → `{"ok", "ready", "reason": "" | "not_public" | "no_cert" | "stale" | "no_answer", "host"}`:
панель тут же перечитывает сертификаты и делает TLS-рукопожатие сама с собой (свой адрес и порт, SNI — домен),
`ready` — только если ответил сертификат домена. До 10 вопросов через 1,5 с; без `ready` страница остаётся и пишет
«Домен пока отвечает прежним сертификатом… нажмите «Перейти сейчас»» (кнопка переходит сразу). fetch на сам домен
не годится: ошибка сертификата в нём — безликий TypeError, а CSP страницы (`connect-src 'self'`) его и не пустит.
Панель перечитывает TLS-контекст по SIGHUP сразу (а не раз в 5 с), сигнал будит её цикл немедленно. Без связи — липкая плашка над прокруткой: «Страница по IP больше не
открывается» с `clean` и кнопкой «Открыть на домене»; без токена переезда или на самом домене — нейтральное «Нет связи
с сервером».

#### Этап 5 — Дополнительно

Пункты включаются **по списку** `extras-list`; состав первой версии решает владелец.

| Шаг | Флаги | Тело | `data` ответа и состояния |
|---|---|---|---|
| `extras-list` | | `{}` | `{items: [{id, title, desc, hl, available, state: "on" \| "off", text, fields: [...], keys?}]}`. Если функции ещё нет: `available: false`, `text` «Появится в этой версии…». У `mcp` — `keys: [{fingerprint, type, label}]` (список ключей агентов, без самих ключей) |
| `bot` | Н С | `{token, chat_id?: "auto" \| "<число>"}` | см. ниже |
| `mcp-key` | С | `{pubkey, label?}` | строка из `~/.ssh/kaskad_agent.pub`. `{fingerprint, connect: {claude_code, claude_desktop}}`. `error.code`: `private_key` (вставили закрытый ключ), `conflict` |
| `sub` | Н С | `{url}` | автообновление подписки — `gw exit-sub add`. Без команды — `unavailable` |
| `backup` | С | `{password, parts?}` | копия настроек файлом: `data: {file (текст .kbk — странице сохранить), name, size, parts}`; kaskad.backup в процессе, как панель |
| `rules` | | `{ru_direct?, confirm_not_rf?, orgs?, ads?, torrent?}` | «Россия напрямую», реклама, торренты. На новом узле по умолчанию — всё через выход (D-019), реклама блокируется. `ru_direct` — только JSON `true`/`false` (строка не считается). `ru_direct: true` — только с `confirm_not_rf: true` (галочка ⚠РФ «Скрипт не используется на территории РФ», которую поставил человек; `SPEC-stage1-rf-mark-builtin.md` §5.3) и `orgs` — коды организаций показанного блока (`data.rf.orgs`; согласие — только на них): без них или список уже шире — `need_input`, `error: {code: "rf_consent", field: "confirm_not_rf"}`, `data.need = {need, orgs, names, block, consent}`, профиль не меняется. Согласие — `via = setup`. `{}` ничего не меняет. `data` всегда: `{profile, ads, torrent, rf: {required, mode: full \| legacy \| need \| none, orgs, names, block, consent, uncovered}}` — пункт «Россия напрямую» этапа «Дополнительно» берёт блок и текст галочки отсюда. Установка без мастера (goInstaller): `KASKAD_CONFIRM_NOT_RF=1` при первой установке; goInstaller её сам не ставит |

Шаг `bot`:
- Сервер проверяет токен (`getMe`), находит чат по последнему сообщению боту, сохраняет `BOT_TOKEN` и
  `BOT_CHAT_ID`, запускает бота и шлёт тестовое сообщение.
- `need_input` (`error.code: need_start`) — «Напишите боту /start и нажмите «Проверить»» (повтор с тем же токеном или
  без него).
- `error.code`: `bad_format`, `bad_token`, `net`.
- `data: {username, chat_id, started, sent}`; `partial` — сохранено, но бот не запустился.

Мониторинг выходов сообщает в бот: без бота пункт «сообщит, если выход упал» не обещать.

#### Этап 6 — Готово

| Шаг | Флаги | Тело | `data` ответа и состояния |
|---|---|---|---|
| `platforms` | | `{}` или `{platform}` | см. ниже |
| `person` | | `{user, device, platform, inbound?}` | создать человека (или взять с этим именем) и устройство с входами из `offers`. `{user, device, access, platform, offers}`; в `answers` — `device`, `device_user`, `device_platform` (после перезагрузки страница не создаёт второе устройство). `error.code: no_inbound` |
| `issue` | С | `{device?, inbound?, os?, png?}` | см. ниже |
| `connection` | | `{device?}` | см. ниже |
| `trace` | | `{device?, target: "ru" \| "foreign" \| "<домен>"}` | схема пути — ответ `gw trace` (kaskad.gw.trace): `{verdict: exit \| direct \| block, summary, origin, steps: [{text, note, tone}], notes, who, route, exit, exits, highlight: {nodes, edges, …}, target, host}`. Страница рисует путь «устройство → вход → узел → маршрут → выход \| напрямую → интернет» по `verdict`, `who`, `route`, `exit` |
| `summary` | | `{}` | `{panel_url, protocols, exits: {names, alive, total}, people: [{id, name, devices}], domain, skipped, github}` |

Шаг `platforms`:
- Без `platform` — 14 систем `{platforms: [{id, title}]}`.
- С `platform` — что выдать первым: `{platform, offers: [{inbound, format, note, title, installed, enabled}], notes}`.
- Быстрые кнопки дизайна (`devices`) отображаются на платформы через `product.device_platform`.

Шаг `issue` — **выдача по формату**, ответ `gw issue`:
- `data: {device, name, kind, os, items: [{format, title, body, filename, notes, png_b64?}], available, howto}`.
- Форматы:

  | `format` | Что показать |
  |---|---|
  | `qr`, `link` | QR (`vpn://`, может быть «код 1 из N» — `body` массивом) |
  | `file` | файл: `.conf`, `.ovpn`, `.mobileconfig`, PowerShell для SSTP — кнопка «Скачать» |
  | `fields` | поля с «Скопировать»: адрес, логин, пароль, PSK — L2TP, IKEv2, SSTP, PPTP, OpenConnect |

- `howto` — шаги «Как подключить» для этой системы.
- **Не сохранять** нигде.

Шаг `connection` — **опрос раз в 3–5 с**:
- `state: checking`, пока `conn` = `wait` или `stuck`; `ok` — при `connected` / `traffic`.
- `data: {conn: "wait" | "connected" | "traffic" | "stuck" | "unknown", since, online_since, inbound, bytes,
  stuck_after: 120}`.
- При `stuck` — `hints: [{code: "port" | "line31" | "udp", text, action?: {step: "issue", inbound, label}}]`. Это блок
  «Самое вероятное — сверху»: «Выдать 2.0», «Выдать по TCP».

Закрыть мастер — `POST api/setup/finish` `{}`:
- Мастер закрыт: его путь дальше отвечает 404.
- Ответ ставит cookie панели и стирает cookie мастера: `{ok, panel_url: "/", proxy_url}`. «Открыть панель» —
  перейти на `panel_url` (или `proxy_url`, если панель уже на домене).

## 4. Ход долгих действий

### 4.1 Задание

`GET api/setup/job` → `{"ok": true, "job": {…} | null}`:

```json
{"id": "a1b2c3d4e5f6", "step": "protocols", "state": "starting" | "running" | "done" | "failed",
 "started": 1791190000.1, "updated": 1791190042.7, "pid": 12345,
 "items": [{"item": "module", "title": "Модуль ядра AmneziaWG", "state": "wait" | "ok" | "warn" | "err" | "idle",
            "detail": "собираю модуль… обычно 1–3 минуты", "since": 1791190001.2}, …],
 "result": <ответ шага §3.1> | null}
```

- Строка = компонент «живая проверка» (HANDOFF §5): значок по `state`, таймер `мм:сс` от `since` на клиенте.
- `idle` — «в очереди».
- Пока `state` = `starting` | `running`: основная кнопка показывает ход («Устанавливаем…»), под списком — «Сервер
  работает — не закрывайте страницу».
- `done` / `failed`: итог — в `result`.
- Задание переживает закрытую вкладку: при входе на этап сначала `GET api/setup/job`.
- Одновременно одно задание — второе получит 409.

### 4.2 SSE `GET api/setup/events`

```
retry: 3000

event: job
data: {<задание целиком, как в GET api/setup/job>}

event: state
data: {"updated": "2026-10-09T12:00:00Z"}      ← setup.json изменился: перечитать GET api/setup/state

: hb                                           ← раз в 15 с тишины
```

- Не больше 4 потоков. При 503 или обрыве — опрос `GET api/setup/job` раз в 2 с, пока задание идёт.
- Нет данных 30 с во время задания — показать «Связь со страницей прервалась — сервер продолжает работу» и перейти на
  опрос.

## 5. Пороги и интервалы

| Что | Значение |
|---|---|
| Подключение без рукопожатия → подсказки | 120 с (`connection.data.stuck_after`) |
| Ожидание DNS → подсказки | 15 мин (`dns.data.hint_after`) |
| Опрос DNS | раз в 10 с |
| Опрос выходов | раз в 10 с |
| Опрос подключения | 3–5 с |
| Опрос сети (`api/setup/net`) | 10 с |
| Ссылка | до конца настройки, не больше 24 ч (`state.expires_in`) |
| Сертификат на IP | 160 ч, продление при остатке < 72 ч, таймер каждые 12 ч |

## 6. Системные состояния

| Состояние | Как узнать | Экран |
|---|---|---|
| Ссылка истекла | `session.reason = expired`, 410 у API | `sys=expired` + `cmd` |
| Ссылка уже использована | `?link=used` / `unknown` | «Ссылка одноразовая — запросите новую: `kaskadctl setup link`» |
| Нужен пароль | `session.reason = password` | форма «Пароль панели» → `api/setup/login` |
| Нет сети у сервера | шаг `offline` / `api/setup/net` `online: false` | `sys=offline` |
| Ошибка сервера | HTTP 500 `{"error"}` | тост с текстом, повтор |
| Обрыв SSE | `onerror` / нет событий 30 с | опрос `api/setup/job` |
| Вторая вкладка | 409 «Сейчас идёт шаг …» | показать идущее задание (`data.job`) |
| Мастер закрыт | 404 у всего | — |

## 7. Мок без сервера

Файл `lib/kaskad/panel/static/setup/dev-mock-setup.js` — `window.KASKAD_SETUP_MOCK = {fetch(url, init), EventSource,
state, flags}`. Страница мастера подключает его сама только при `?mock=1` и только вне секретного пути сервера
(`/setup-<секрет>/` — никогда). Формы ответов — как у сервера: продукт, состояние и рельс, ответ `trace` сверяет тест
`tests/setup/test_setup_static.py` (`MockShapesTest`).

Как запустить:

```
python3 -m http.server 8765 --directory lib/kaskad/panel/static
http://127.0.0.1:8765/setup/index.html?mock=1
```

Сверху — панель просмотра (`dev-view-setup.js`, как «Просмотр» прототипа): любой этап и его состояния кнопками,
системные экраны, тема. `?bar=0` — без панели; `window.KaskadDev.run("<сцена>")` — сцена по имени
(`KaskadDev.scenes()` — список), так снимаются экраны.

Параметры адреса:

| Параметр | Что делает |
|---|---|
| `lat=400` | задержка ответа, мс |
| `auth=link` / `auth=password` / `auth=expired` | состояние входа |
| `pw=1` | пароль уже задан |
| `step=<kind>` | текущий этап (этапы до него — пройдены, пароль задан; `skipped=<kind,…>` — пропущены) |
| `mode=classic` | классический режим |
| `theme=light` / `theme=dark` | тема (без него — как в системе) |
| `offline=1` | нет сети |
| `cert=fallback` | сертификат на IP не вышел |
| `github=none` / `github=bad` | ключа GitHub нет / не подошёл |
| `module=no` | модуль ядра здесь нельзя (контейнер) |
| `clean=1` | чистый сервер |
| `fail=<шаг>` | шаг отвечает ошибкой |
| `soon=1` | подписки и копии настроек ещё нет («появится в этой версии»; в 3.3.0 они есть) |
| `conn=stuck` | подключение не случилось за 2 минуты |
| `dns=cloudflare` / `dns=elsewhere` / `dns=none` | исходы DNS |
| `sse=off` | поток событий не открывается — ход заданий опросом |

Долгие шаги мок проводит по строкам (wait → ok/warn/err) через событие `job`.

## 8. Страница мастера (фронтенд)

`lib/kaskad/panel/static/setup/`: `index.html`, `setup.js` (чистый JS без сборки), `setup-i18n-ru.js` (все тексты),
`qs.css` (классы мастера поверх `../tokens.css` и `../components.css`), мок и панель просмотра — только для
разработки. Общие файлы панели (вид `boot.js`/`KaskadLook`, токены, компоненты, значки, QR, шрифты) страница берёт из
корня панели адресом `../` — так же работает и стенд `http.server`. CSP панели: ни вложенных скриптов, ни атрибутов
`style` (единственное CSSOM — оттенок нейтралей `--qs-hue` и акцент продукта на `<html>`).

- Раскладки (HANDOFF §3): ≥ 1100 — рельс 256 · шаг до 760 · видео 420; 768–1099 — рельс-полоса 76, видео под шагом;
  ≤ 767 — полоса этапов сверху (шторка со списком), видео-карточка над шагом, нижняя панель кнопок 52 px.
- Ход долгих шагов — SSE `api/setup/events`; обрыв, 503 или 30 с тишины во время задания — опрос `api/setup/job` раз в 2 с.
- Опросы: сеть — 10 с (пока плашка «нет интернета»), выходы — 10 с, DNS — 10 с, подключение — 4 с.
- 401 — экран входа по ссылке или паролю, 410 — «Ссылка больше не действует», 404 — «Мастер закрыт», обрыв fetch —
  плашка «Нет связи с сервером».
- Секреты (пароль, ключи, токены, выданные настройки) — только в полях и памяти вкладки, не в `localStorage`, адресе
  и консоли; ключ выхода стирается при закрытии окна.
- Видео: заглушка с пунктами `product.videos[kind]`; когда у шага появится `src`, вместо заглушки встанет `<video>`
  с того же сервера, пункты перематывают ролик. Наведение на пункт подсвечивает `[data-hl-target]` на странице.
- Схема на финале — по ответу шага `trace` (тот же разбор, что «Куда пойдёт трафик?» панели): устройство → вход →
  узел → маршрут → выход | напрямую → интернет; переключатель «до зарубежного / до российского».
