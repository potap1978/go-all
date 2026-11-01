# Kaskad 3 — волна 4: домен, сертификат, сайт-маскировка, «всё на 443»

Дата: 2026-10-07. Основа — исследование `k3/docs-research-wave4.md` (факты со ссылками на код goAmnezia и Kaskad,
внешние источники). Решение владельца: «сделай как в goAmnezia», и «если надо — сайт, AmneziaWG и VLESS на 443
одновременно». Архитектор — координатор (Opus 5.5); реализация — исправители Opus 5.5 (агент `kaskad-fixer`),
проверка — тестеры Sonnet 5.5 (`kaskad-finder`).

## 0. Итог в пяти строках

1. Домен — новая сущность узла: A-запись → проверка DNS → сертификат Let's Encrypt (HTTP-01 на 80) → им пользуются
   панель, страница человека, SSTP и OpenConnect. Без домена всё работает как сейчас (самоподписанный).
2. Сайт-маскировка из каталога 33 467 шаблонов (как в goAmnezia) на TCP/443 через nginx; панель — на секретном пути
   `/app-<86 знаков>/` этого же сайта; страницы людей — `/p/<токен>` на домене.
3. «Всё на 443» — отдельный режим 4.1: спереди HAProxy по SNI — наш домен → nginx, остальное → выход VLESS; UDP/443 —
   AmneziaWG. Сначала замер цены на стенде (1 vCPU), потом решение «по умолчанию».
4. До любой публикации панели за nginx — обязательная переделка входа (§3): за прокси сейчас любой получил бы
   админа без пароля (`auth.tunnel`).
5. Зависимости (nginx, certbot, git, каталог) ставятся только когда человек включает домен или сайт (правило
   владельца), из любой точки: меню, панель, бот, CLI.

## 1. Этапы

| Этап | Что | Условие выхода |
|---|---|---|
| **4.0-A** | Безопасность панели за прокси (§3) | тесты «за прокси нет туннельного админа», лимиты по настоящему адресу, Origin/Host, cookie Secure |
| **4.0-B** | Домен, DNS, сертификат LE, раздача сертификата (§4) | живая проверка на стенде с тестовым поддоменом Cloudflare (claude-test) |
| **4.0-C** | nginx, сайт из каталога, панель на секретном пути, страницы на домене, соседство, удаление (§5–§7) | живая проверка: сайт открывается, панель по секретному пути, `/p/`, глушилка без журнала |
| **4.1** | «Всё на 443» (HAProxy SNI), AWG на UDP/443 и Alt-Svc, LE на IP (lego), SSTP/ocserv на 443 по поддомену | замер ЦП/скорости HAProxy против DNAT на 1 vCPU; тест встроенного SSTP Windows (SNI) |

## 2. Режимы и состояние

`state.json` → верхний ключ `site` (не `gateway.site`: домен нужен панели и без шлюза и переживает «Удалить шлюз»;
schema state не меняется — поле необязательное, читается терпимо; уточнено в §4.1):
```
"site": {"domain": "", "cert": "" | "letsencrypt" | "selfsigned", "mode": "off" | "domain" | "site" | "site443",
         "template_id": "", "template_source": "", "panel_path": "<скрыт: только в secrets>", "alt_svc": false,
         "acme_email": ""}
```
- `off` — как сейчас (самоподписанный, панель по туннелю или на случайном порту).
- `domain` — домен с LE-сертификатом без сайта (вариант E исследования): сертификат получают панель (открытый режим
  на своём порту), `/p/`, SSTP, ocserv. Подходит, когда TCP/443 держит проброс. Для HTTP-01 нужен только порт 80.
- `site` — `domain` + nginx на TCP/443: сайт-маскировка, панель на `/app-…/`, `/p/` на 443. Требует свободного TCP/443.
- `site443` (4.1) — `site` + фронт HAProxy: тот же 443 делят сайт и проброс VLESS; AWG может встать на UDP/443.
- Секретный путь панели — в `secrets.json` (`site.panel_path`), не в state и не в журнале; в меню маскируется
  `app-AbCd••••WxYz`, полный — по запросу.

Переходы без потери клиентов: смена режима не трогает ключи входов; смена адреса для клиентов (IP → домен) —
выдачи с новым адресом помечаются «нужны новые настройки» по механизму F-2 (кому выдать заново).

## 3. Панель за прокси (4.0-A, обязательно первым)

Проблемы (исследование §3.D, D1–D10) и решение:
1. **Отдельный слушатель для nginx:** unix-сокет `/run/kaskad/panel/proxy.sock` (0660, группа www-data) или
   `127.0.0.1:1981`, `kind="proxy"`. На нём **туннельного входа нет никогда** (`auth.tunnel` → False), нужен пароль
   или одноразовая ссылка; на прежнем `127.0.0.1:1980` всё как сейчас.
2. **Доверие заголовкам только от этого слушателя:** адрес клиента — `X-Real-IP` (nginx перезаписывает), схема —
   `X-Forwarded-Proto`, имя — `X-Forwarded-Host`. На остальных слушателях эти заголовки игнорируются (и по-прежнему
   выключают туннель, как сейчас).
3. Лимиты паролей, ссылок и промахов `/p/` — по настоящему адресу (IPv6 по /64), не по 127.0.0.1.
4. Host/Origin: разрешён домен из `gateway.site.domain`; Origin сверяется со `https://<домен>`.
5. Cookie: `Secure` всегда на прокси-слушателе; `Path=/app-…/` для админки; `SameSite=Strict`.
6. Префикс: панель знает свой базовый путь (`<base href>` и `apiPath()` от `location.pathname`), SSE, редиректы
   `Location` и ссылки входа — относительно префикса. Абсолютные `/api/…` в JS и `/p/user.*` — через базу.
7. `scheme._client_device` за прокси — по `X-Real-IP`.
8. 404 панели не говорит «nginx»; HSTS ставит nginx.

## 4. Домен и сертификат (4.0-B)

- **Ввод домена:** меню (новый пункт главного меню «19) Домен и сайт»), панель («Настройки → Домен и сайт»), бот
  (статус и ввод), CLI `kaskadctl site domain <имя> | --clear`.
- **Проверка DNS** (как `ga_site_dns_state` goAmnezia, без запуска с узла чужих проверок): все A-записи против
  всех адресов узла (`gateway.host` и адреса интерфейсов) → `ok | partial | elsewhere | none | unknown`;
  Cloudflare-прокси (оранжевое облако) — по сетям Cloudflare, совет «DNS only»; AAAA не на узел — предупреждение
  (LE пробует IPv6 первым). Ожидание DNS — опросом до 5 минут с прогрессом.
- **Порт 80:** проверка занятости (`ops.port_conflicts` по tcp/80: проброс, чужой nginx). Если занят чужим —
  отказ с объяснением (как goAmnezia: чужое не останавливаем).
- **Выпуск:** certbot из apt (`certonly --non-interactive --agree-tos --keep-until-expiring --cert-name <домен>`),
  webroot `/var/www/kaskad-acme` через наш vhost на :80 (только ACME + редирект на https в режиме `site`) или
  `--standalone`, если nginx не нужен (режим `domain`). Почта — по желанию (`--register-unsafely-without-email`
  иначе). «Не перевыпускать», если до конца ≥ 30 дней (лимиты LE: 5 одинаковых наборов в неделю).
- **Продление:** `certbot.timer` (если есть) или свой `kaskad-cert-renew.timer`; deploy-хук
  `/etc/letsencrypt/renewal-hooks/deploy/kaskad.sh` → `kaskadctl site cert-installed` (копирует в места
  потребителей, перечитывает: панель, accel-ppp reload, ocserv restart, nginx reload). Сторож срока: 21 день →
  событие и попытка продлить.
- **Потребители сертификата:** панель (открытый режим), `/p/`, SSTP (accel-ppp `ssl-pemfile/keyfile`; хеш
  сертификата для crypto binding меняется — сессии переподключатся при продлении), ocserv. IKEv2 остаётся на своём
  CA (переход на публичный сертификат — 4.1, после проверки клиентов).
- **Тексты** — как в goAmnezia (исследование §1.13), словами Kaskad.

### 4.1 Контракт 4.0-B (для исполнителей; 2026-10-07)

Порядок: **B-ядро** (один исполнитель: ядро, CLI, API, тесты) → после слияния **B-лицо** (два исполнителя
параллельно: панель; меню и бот) → живая проверка. Шаблоны сайта (§5, часть «каталог и модуль») идут параллельно
с B-ядром отдельным исполнителем.

**Решения архитектора:**
1. **Где лежит.** `state.json` → `site` (верхний ключ): `{"domain": "", "mode": "off"|"domain", "cert": ""|"letsencrypt",
   "staging": false, "acme_email": "", "use": {"sstp": false, "ocserv": false}, "dns": {последняя проверка}}`.
   Отсутствие ключа = `off`. Режимы `site`/`site443` добавят 4.0-C и 4.1.
2. **Файлы сертификата.** Потребители берут только пару
   `/etc/kaskad/site/tls/current/{fullchain.pem,privkey.pem}` (0644/0600): `current` — атомарно заменяемая ссылка
   на проверенную пару. Её делает `kaskadctl site cert-installed` из `/etc/letsencrypt/live/<домен>/`. Чужие
   сертификаты на машине не трогаем.
3. **Кто переходит на сертификат домена сам, а кто — по кнопке.**
   - Сам, как только сертификат действителен: **панель** (открытый режим) и **страницы людей** `/p/`. Их адреса
     становятся `https://<домен>:<порт>/…` (`conf.public_url`, `access.json`), 443 не печатается. Клиентов это не
     ломает.
   - Только по явному шагу `site use sstp|ocserv on`: **SSTP** и **OpenConnect**. Имя в сертификате должно совпасть
     с адресом в настройках устройства, поэтому их выдачи с этим входом помечаются «нужны новые настройки» (механизм
     F-2), а новые выдачи берут адрес-домен. Перед шагом показывается, скольким устройствам это нужно.
   - **IKEv2** остаётся на своём CA (4.1).
   - **Адрес для клиентов** (`gateway.host`) на домен — отдельный шаг через существующий `gw host <домен>`;
     интерфейс его предлагает, сам не меняет.
4. **Выпуск в 4.0-B — только `--standalone` на TCP/80** (своего nginx ещё нет; webroot — в 4.0-C).
   - Порт 80 должен быть свободен. Отказ с объяснением, если на нём чужой слушатель (`ss`), проброс tcp/80 (DNAT
     перехватит вызов LE) или вход шлюза.
   - При активном UFW: `ufw allow 80/tcp` с комментарием `kaskad-acme`, пока домен задан. Снимается вместе с доменом.
   - Обратный случай: добавление проброса tcp/80 при заданном домене предупреждает «сертификат перестанет
     продлеваться» (`conflicts.check`).
5. **certbot** ставится из apt только в момент `site cert issue` (правило владельца о зависимостях) и записывается
   в `/etc/kaskad/site/pkgs` для строки удаления. Флаги:
   - `certonly --standalone --preferred-challenges http --non-interactive --agree-tos --keep-until-expiring
     --cert-name <домен> -d <домен>`;
   - почта или `--register-unsafely-without-email`;
   - `--staging` по флагу (для стенда);
   - свой deploy-хук на сертификат (`--deploy-hook "<kaskadctl> site cert-installed --quiet"`, хранится в
     renewal-конфиге; хук сверяет `RENEWED_LINEAGE` с нашим доменом).

   Правила выпуска:
   - Действителен ≥ 30 дней и не staging (или staging по флагу) → не перевыпускать.
   - staging → боевой: один раз `--force-renewal`.
   - Ошибки по причинам `limit | net | dns` — тексты как в goAmnezia (исследование §1.3).
6. **Продление.** Есть `certbot.timer` — включить его. Нет — свой `kaskad-cert-renew.timer`.
   - Сторож: ежедневный `kaskad-site.timer` → `kaskadctl site tick`. Если до конца < 21 дня — `certbot renew
     --cert-name`, не помогло — событие в журнал и уведомление бота, если бот умеет.
   - Юниты пишет Python (как `units.py`, `rules/timer.py`).
7. **DNS-проверка без новых пакетов:** свой разбор DNS по UDP на stdlib.
   - Резолверы: 1.1.1.1, 8.8.8.8, 77.88.8.8; таймаут 2 с, 2 попытки; запрашиваются A и AAAA.
   - Состояния `ok | partial | elsewhere | none | unknown` и флаги `cloudflare` (15 сетей Cloudflare из goAmnezia),
     `aaaa`, `aaaa_foreign`. `ready = ok && !cloudflare && !aaaa_foreign`.
   - «Свои адреса» = `gateway.host` (если IP) + публичный IPv4 узла (как у `host_auto`) + глобальные адреса
     интерфейсов.
   - `--wait N` (≤ 300 с) повторяет каждые 15 с.
8. **Убрать домен** (`site domain --clear`):
   - потребители возвращаются на самоподписанный;
   - SSTP и OpenConnect, если были на домене, получают пометки F-2;
   - в renewal-конфиге `autorenew = False`; сертификаты LE остаются (лимиты);
   - снимаются наш таймер (если ставили), наш хук и правило UFW `kaskad-acme`.

   Полное удаление Kaskad (меню 15) зовёт `kaskadctl site remove --json` — то же плюс `/etc/kaskad/site`.

**CLI** (`kaskadctl site …`, у всех `--json`; ошибки — код и текст по-русски, как у `gw`):

| Команда | Что делает | JSON |
|---|---|---|
| `site status` | состояние | `STATUS` (ниже) |
| `site domain <имя>` / `site domain --clear` | задать (проверка имени, сразу DNS-проверка, без выпуска; рабочий домен — смена целиком: DNS и порт 80 нового, выпуск, пока прежний работает, переключение, откат при сбое — 3.3.0 T-M6-qs330d №1) / убрать (п. 8) | `{ok, site: STATUS, dns: DNS}` / `{ok, site: STATUS, affected: [...]}` |
| `site dns [--wait N]` | DNS-проверка | `DNS` |
| `site email <почта>` / `--clear` / без аргумента — показать | почта для LE | `{ok, email}` |
| `site cert issue [--staging]` | поставить certbot при нужде, проверить 80, выпустить или оставить живой, раздать | `{ok, issued: bool, kept: bool, cert: CERT, warnings: [...]}`; отказ — `{ok: false, reason: "port80"\|"dns"\|"limit"\|"net"\|"apt", text, retry_after?}` |
| `site cert renew` | `certbot renew --cert-name` (только если подошёл срок) | `{ok, renewed: bool, cert: CERT}` |
| `site cert-installed [--quiet]` | атомарная копия в `/etc/kaskad/site/tls/current`, перечитать панель, accel-ppp `reload`, ocserv (перечитать без обрыва, если умеет, иначе restart) | `{ok, consumers: [...]}` |
| `site use sstp\|ocserv on\|off [--dry-run]` | перевести вход на сертификат домена / вернуть | `{ok, use: {...}, affected: [{device, name, kind}]}` |
| `site tick` | сторож срока (п. 6) | `{ok, days_left, action}` |
| `site remove` | п. 8 + каталог | `{ok, kept: [...], packages: [...]}` |

`STATUS` (одинаковый в CLI `--json` и `GET /api/site`):
```
{"ok": true, "domain": "vpn.example.com" | null, "mode": "off" | "domain",
 "cert": CERT | null,          CERT = {"kind": "letsencrypt" | "selfsigned", "names": ["vpn.example.com"],
                                        "not_after": "2027-01-05T10:00:00Z", "days_left": 89, "staging": false,
                                        "issuer": "R11"}
 "dns": DNS | null,            DNS = {"state": "ok|partial|elsewhere|none|unknown", "a": [...], "aaaa": [...],
                                      "own": [...], "cloudflare": false, "aaaa_foreign": false, "ready": true,
                                      "checked_at": "…Z", "advice": "текст для человека" | null}
 "port80": {"free": true, "who": null | "текст: кто держит"},
 "use": {"panel": bool, "pages": bool, "sstp": bool, "ocserv": bool},
 "use_impact": {"sstp": N, "ocserv": M},       — сколько устройствам понадобятся новые настройки при переводе
 "use_impact_total": N,                             — уникальные устройства активных SSTP/OpenConnect
 "host_is_domain": bool,                        — gateway.host == домен
 "renew": {"by": "certbot.timer" | "kaskad-cert-renew.timer" | null},
 "deps": {"certbot": bool}, "email": "" }
```

**API панели** (админ; запись — с `X-Kaskad-Admin: 1`; модуль `site/panel.py` по образцу `rules/panel.py`):
`GET /api/site` → STATUS · `PUT /api/site/domain` `{domain}` · `DELETE /api/site/domain` · `POST /api/site/dns`
`{wait?}` · `PUT /api/site/email` `{email}` · `POST /api/site/cert` `{staging?}` (долгий: таймаут клиента 10 мин в
`TIMEOUT_BY_ROUTE`) · `POST /api/site/cert/renew` · `PUT /api/site/use` `{kind: "sstp"|"ocserv", on: bool,
dry_run?: bool}`. Ответы — те же JSON, что у CLI.

**Лицо (B-лицо, после ядра):**
- Меню: пункт главного меню «19) 🔐 Домен и сайт (<домен> · сертификат до ДД.ММ | нет)». Главное меню остаётся в
  80×24 и с красной строкой ядра; существующие номера не меняются.
- Подменю — один сквозной путь «как в goAmnezia»: ввести домен → DNS (ожидание до 5 мин с прогрессом) → «Выпустить
  сертификат?» → итог. Дальше предложить SSTP/OpenConnect (с числом устройств) и «Адрес для клиентов — домен».
- Панель: «Настройки → Домен и сайт» — тот же путь карточками.
- Бот: экран «🌐 Домен»: статус, «Задать домен» (текстом), «Проверить DNS», «Выпустить сертификат».

**Файлы B-ядра:**
- `lib/kaskad/site/` (кроме `templates.py`) и регистрация в `lib/kaskad/cli.py`;
- маршруты API;
- потребители сертификата: `panel/conf.py`, `panel/server.py`, `panel/userpage.py`, входы `sstp`/`accel`/`ocserv` и
  выдачи;
- `conflicts.py` (tcp/80);
- тесты `tests/site/*` и затронутые.

`install.sh`, `static/*` и бот — не трогать (это B-лицо).

**Уточнения исполнителя B-ядра (2026-10-07):**
- Уточнение исполнителя: «пометки F-2» в коде — это ответ, а не сохранённая метка (как `previous` у `gw inbound
  install`): `affected: [{device, name, kind, user_name}]` и `text` «… Выдайте их заново (меню 17 → 2 → 1 или панель
  → «Люди и устройства»): …». Их возвращают `site use`, `site domain --clear` и смена домена; лицо показывает их сразу.
- Уточнение исполнителя: перед certbot `cert issue` сам проверяет DNS. `none`, `elsewhere` и `cloudflare` — отказ
  `reason: "dns"` без обращения к LE (неудачные проверки тратят лимит); `partial`, `aaaa_foreign`, `unknown` —
  предупреждение в `warnings`, выпуск идёт.
- Уточнение исполнителя: `port80` — код выхода 3 (HTTP 409), остальные отказы — 1 (HTTP 409 у API); в JSON отказа
  ещё `error` (= `text`) и `code`. «Нет сертификата» у `use … on` и `cert renew` — код 3.
- Уточнение исполнителя: в `STATUS` добавлено `domain_unicode` (имя кириллицей для IDN); `domain` — в ASCII
  (punycode), его берут certbot и выдачи. `use.panel`/`use.pages` истинны, только когда открытый режим включён.
- Уточнение исполнителя: в ответах есть поля сверх таблицы: `text` (для человека), `warnings`, `kept` у
  `domain --clear`, `changed`/`skipped` у `cert-installed`, `dry_run` у `use --dry-run`. Ключи таблицы — без изменений.
- Уточнение исполнителя: проброс tcp/80 при заданном домене в `conflicts.check` — конфликт вида `site` (как прочие:
  отказ без `--force`), текст «… сертификат домена перестанет продлеваться».
- Уточнение исполнителя: ocserv перечитывает файлы сертификата по SIGHUP (NEWS ocserv 0.11.0; в Debian 12/13 и
  Ubuntu 24.04/26.04 — 1.1.6 и новее), поэтому продление — `systemctl reload kaskad-gw-ocserv` без обрыва сессий.
  Переход `use ocserv on|off` — `restart`: путь `server-cert`/`server-key` не перечитывается (`non-reloadable`).
  Выдача OpenConnect на домене — без `pin-sha256`: certbot по умолчанию меняет ключ при продлении, закреплённый
  отпечаток ломал бы клиентов каждые 60 дней.
- Уточнение исполнителя: вход на `use` берёт копию, пока она есть; годность сертификата проверяется при `use … on`,
  а не при каждой отрисовке (вход не прыгает между сертификатами у границы срока). Панель же перечитывает годность
  раз в час и при истечении возвращается на свой сертификат.
- Уточнение исполнителя: чужой deploy-хук в renewal-конфиге того же имени не трогаем — новый сертификат после
  продления подхватывает ежедневный `site tick` (сравнивает копию с `live/`).
- Уточнение исполнителя: `panel/conf.py` и `panel/server.py` 4.0-A читали домен из `gateway.site.domain`; теперь —
  из верхнего `site.domain` (старый ключ читается тоже).

## 5. Сайт-маскировка (4.0-C)

- **Каталог:** чистый файл из goAmnezia (`templates_catalog.json`, 14 266 581 байт, sha256
  `ca31e8d0d6a58687a73cef43fc3c13efe0b2d9f9afa1d257dbe85951a03028e1`). Хранение — в репозитории kaskad-pro
  `assets/templates_catalog.json.gz`; на узел скачивается **только при включении сайта** тем же ключом GitHub
  (`ghkey.open_github`), проверяется sha256 (зашит в код), кладётся в `/opt/kaskad/assets/`. Копия в навыке
  `site-templates-library` испорчена (5 id) — не использовать, восстановить отдельно.
- **Модуль:** `templates_lib.sh` goAmnezia — логика выбора и проверки страницы сохраняется (это гарантия качества
  базы), но с доработками исследования: таймаут и предел размера клона (90 с, 100 МБ — как у «своего git»),
  **атомарная подмена** корня (новый каталог → проверка → rename; прежний — в `/var/backups/kaskad/site/`, один),
  чистка служебных файлов без `license.html/security.html` (маска только по точным именам), удаление симлинков,
  вырезание трекеров (как `_ga_site_strip_trackers`), подписи авторов в подвале не трогаются.
- **Выбор:** по умолчанию случайный из пула без `portfolio`, `*.github.io` и шаблонов без лицензии; 3 попытки,
  затем встроенная заглушка. Меню — категории, поиск, страницы по 25, превью-ссылка; панель — категории, поиск,
  карточки с превью; бот — категории по 8, поиск, «наугад», `callback_data` через `sha1(id)[:12]`.
- **Каталоги:** сайт `/var/www/kaskad-site`, ACME `/var/www/kaskad-acme`, маркеры `.kaskad_template_id`,
  `.kaskad_template_source`.

### 5.1 Контракт модуля шаблонов (исполнитель C1, параллельно с B-ядром; 2026-10-07)

- **Источник логики** — goAmnezia Pro `lib/templates_lib.sh` и шаблонные части `lib/website.sh` (папка
  `Projects/goAmnezia Pro`).
  - Переносится на Python в `lib/kaskad/site/templates.py`.
  - Правила выбора и проверки страницы сохраняются один в один, с доработками §5.
  - Чистый каталог: `Projects/goAmnezia Pro/templates_catalog.json`, sha256 `ca31e8d0…3028e1`.
- **Хранение каталога:**
  - В репозитории — `assets/templates_catalog.json.gz`. В payload не входит: `build_payload` берёт только
    `lib/kaskad`.
  - Скачивание на узел — `fetch_catalog(runner)`: через `ghkey.open_github` из репозитория kaskad-pro, с той же
    ветки, что и самообновление. Проверка sha256 распакованного содержимого (значение зашито в код).
  - Путь на узле — `/opt/kaskad/assets/templates_catalog.json`. Есть и сумма верна — не качать.
- **Функции модуля** (имена фиксированы — их зовут CLI, панель и бот):
  - `catalog()` → `{"categories": [{"id", "title", "count"}], "total": N}`;
  - `search(category=None, query=None, page=1, per_page=25)` → `{"items": [{"id", "name", "category", "source",
    "license", "preview"}], "page", "pages", "total"}`;
  - `pick_random(exclude=())` → id из пула §5;
  - `deploy(runner, template_id=None, root=paths.site_root())` → `{"ok", "id", "source", "fallback": bool,
    "backup": path|null, "tries": n}` (атомарная подмена; 3 попытки; затем встроенная заглушка);
  - `status()` → `{"id", "source", "deployed_at", "root"}` по маркерам.
- **Внешние вызовы:**
  - `git` ставится через apt только в `deploy` и пишется в `/etc/kaskad/site/pkgs`;
  - клон — `git clone --depth 1` с таймаутом 90 с и пределом 100 МБ;
  - всё через `runner`, тесты — на поддельных runner/git (локальные репозитории).
- **CLI:** модуль отдаёт `register_cli(sub)` с командами
  - `site templates [--category C] [--query Q] [--page N]`;
  - `site template set <id>|--random`;
  - `site template status`;

  у всех `--json`. B-ядро в `site/cli.py` зовёт `templates.register_cli(sub)` (импорт под `try`, если модуля ещё
  нет). nginx, отдача сайта и меню — в 4.0-C после слияния B.
- **Файлы C1:**
  - `lib/kaskad/site/templates.py`, `assets/templates_catalog.json.gz`, `tests/site/test_templates.py` и фикстуры;
  - `paths.py` — только новые функции `site_root()`, `site_backup_dir()`, `assets_dir()`;
  - `lib/kaskad/site/__init__.py` — только если его нет, пустым.
- Уточнение исполнителя (C1, 2026-10-07):
  - `deploy(runner, template_id=None, root=None)`: `None` → `paths.site_root()` в момент вызова (пути не кешируются
    при импорте, тесты меняют `KASKAD_ROOT`).
  - Явный `template_id` — одна попытка без заглушки; при неудаче `{"ok": false, "reason", "text"}`, сайт прежний.
    Случайный (`None`, `--random`) — до 3 шаблонов пула; заглушка `builtin_stub` — **только если в корне ещё нет
    `index.html`**: рабочий сайт заглушкой не заменяется. Нет каталога/ключа/git при пустом корне — сразу заглушка
    (`tries: 0`, причина в `warnings`); при явном id — ошибка `KaskadError` (`data.reason`).
  - Коды отказа шаблона: как в goAmnezia (`download_failed`, `no_index`, `app_shell`, `broken_assets`) плюс
    `timeout` (90 с на все вызовы git одного шаблона), `too_big` (100 МБ без `.git`), `bad_repo`. Два `deploy`
    сразу → `LockedError` (код 6). Мало места (< 256 МБ) → `KaskadError` `low_disk`.
  - Поля сверх контракта: категории — `icon`; записи `search` — `description`, `repo`, `author` (кого благодарить);
    `search` — `per_page`, `category`, `query`; `deploy` — `name`, `errors[]`, `warnings[]`, `root`, при отказе
    `reason`, `text`; `status` — `stub`, `site`, `backup`. Ещё функции `get(id)`, `short_key(id)` (= `sha1(id)[:12]`
    для бота), `by_short_key(key)`.
  - Поиск — без учёта регистра, «ё» = «е»; сначала совпадения в названии и id, затем в описании (описания в
    каталоге русские: «ресторан» находит «Сайт ресторана»). Неизвестная категория — ошибка с кодом 2.
  - `catalog()`/`search()`/`get()`/`by_short_key()`/`pick_random()` только читают; нет каталога или он повреждён —
    `CatalogMissing` (`data.reason`: `catalog_missing` | `catalog_damaged`): панель, мастер и бот зовут
    `prepare(runner)`. CLI `site templates` и `site template set` сами зовут `prepare`/`fetch_catalog`.
  - **Указатель каталога (3.3.0, T-M6-qs330 №3).** Разобранный целиком каталог — ~190 МБ объектов Python; панель
    живёт в `MemoryMax=200M` и падала по OOM на «Выбрать вручную». Поэтому чтения не разбирают JSON: рядом с
    каталогом лежит `/opt/kaskad/assets/templates_index.bin` (заголовок с sha256/размером/mtime каталога и
    категориями-отрезками, строки записей, смещения, отсортированные `short_key`), и каждое чтение — несколько seek.
    Устарел (другой каталог, новый формат, файл обрезан) — собирается заново: `kaskadctl site templates-index
    [--fetch]`; под systemd (панель, бот, MCP) — отдельным процессом в `systemd-run --scope` (вне cgroup вызвавшего),
    в CLI — в самом процессе. Запись атомарная (временный файл + rename), сборки сериализует flock. Пик памяти
    процесса панели на настоящем каталоге — +3 МБ вместо +189 МБ (`python3 tools/tpl_index_mem.py`).
  - `fetch_catalog`: сначала `assets/templates_catalog.json.gz` из копии репозитория на диске (`KASKAD_SRC_DIR`,
    `/opt/kaskad-pro`, …), затем GitHub: ветка `KASKAD_BRANCH` из `/etc/kaskad/config`, потом `main`, `test`
    (сумма решает). Ошибки: `data.reason` `nokey` | `bad` | `net` | `missing` | `sha`.
  - Резервная копия — `site_backup_dir()/previous`. `git` пишется в `/etc/kaskad/site/pkgs`, только если его
    поставил Kaskad. Модуль не пишет `state.json` (`site.template_id/source` — по маркерам; синхронизация — 4.0-C).

## 6. nginx (4.0-C)

- Ставится только при режиме `site`. Свои файлы `sites-available/kaskad` (+ симлинк), `kaskad-acme`; зоны и кэш
  `kaskad_*`; `nginx.conf`, `conf.d`, чужие vhost и `sites-enabled/default` не трогаем; `default_server` не ставим;
  `nginx -t` до reload, при ошибке — откат файла.
- vhost: 80 — ACME + `return 301 https://$server_name$request_uri`; 443 — сайт (`server_name <домен>`), TLS 1.2/1.3,
  HSTS без preload, `server_tokens off`, точечные файлы закрыты, трекеры вырезаны.
- Панель: `location ^~ /app-<86>/ { proxy_pass http://unix:/run/kaskad/panel/proxy.sock:/; proxy_set_header
  X-Real-IP $remote_addr; X-Forwarded-Proto https; X-Forwarded-Host $host; proxy_read_timeout 30m; access_log off;
  error_log /dev/null crit; }` (§3).
- Страницы людей: `location ^~ /p/` → тот же сокет, лимит запросов на уровне nginx.
- **Глушилка:** неверный секретный путь (`/app-…`, `/p/<не тот токен>`) отвечает как обычный 404 сайта и не
  пишется в журнал (как goAmnezia W:968-991).
- Нет SNI / чужое имя: `ssl_reject_handshake on` в отдельном блоке-заглушке только при отсутствии соседей на 443.

## 7. Соседство, порты, удаление

- **TCP/443 занят** (проброс, SSTP/ocserv на 443, чужой nginx goVLESS/goTelegram/3X-UI) → режим `site` недоступен с
  объяснением: «что держит 443 и что можно сделать» — режим `domain` (сертификат без сайта), перенос SSTP/ocserv,
  или 4.1 «всё на 443».
- **Обратный случай** — проброс на 443 добавляют при включённом `site`: `conflicts.check` знает наш nginx
  (`kind = site`) и предупреждает «сайт перестанет открываться».
- Удаление (меню 15 и «Выключить сайт»): снимаются наши vhost, таймер, хук; сертификаты LE остаются
  (`autorenew=False`), файлы сайта — по выбору; nginx и certbot — как пакеты, поставленные Kaskad (H.4).
- NEIGHBOURS: добавить Kaskad (1980, 1981/сокет, `/var/www/kaskad-*`) в список соседей goAmnezia и наоборот.

### 7.1 Контракт 4.0-C, ядро (исполнитель Codex, поток s2; 2026-10-07)

Порядок: **C-ядро** (этот раздел: Python, CLI, API, тесты) → **C-лицо** (меню 19, панель, бот) → живая проверка.

**Решения архитектора:**
1. **Режим `site`** = домен + действующий сертификат LE (не staging) + свободный TCP/443 + nginx. Без любого из
   условий — отказ с причиной и советом (код 3, `reason`: `no_domain`, `no_cert`, `port443`, `port80`, `neighbour`,
   `apt`, `nginx_test`, `template`). Переходы: `off|domain → site` и `site → domain` (сайт снят, домен и сертификат на месте).
   `site domain --clear` при режиме `site` сначала снимает сайт.
2. **nginx** ставится из apt только при первом `site mode site` и пишется в `/etc/kaskad/site/pkgs`, если его
   поставил Kaskad.
   - Наши файлы — только `sites-available/kaskad` (+ симлинк в `sites-enabled`).
   - Зоны `limit_req` и кэша — с префиксом `kaskad_`.
   - `nginx.conf`, `conf.d/`, чужие vhost и `sites-enabled/default` не трогаем. `default_server` не ставим.
   - Запись: копия прежнего файла → новый во временный → `mv` → `nginx -t`. Не прошло — вернуть прежний, отказ
     `nginx_test` с хвостом вывода. Потом `systemctl reload nginx`, или `start`, если не запущен.
   - Права vhost — 0600: в нём секретный путь.
3. **vhost.** Сертификат — копия `/etc/kaskad/site/tls/…`, стабильные пути из `site/model.py`.
   - **:80** — `location ^~ /.well-known/acme-challenge/ { root /var/www/kaskad-acme; }`, остальное
     `return 301 https://$server_name$request_uri;`.
   - **:443 ssl** (+`http2` по версии nginx, как в goAmnezia), `server_name <домен>`:
     - TLS 1.2/1.3, шифры и сессии как в goAmnezia;
     - HSTS `max-age=63072000; includeSubDomains` без preload;
     - `server_tokens off`, `nosniff`, `Referrer-Policy`, `disable_symlinks on from=$document_root`;
     - точечные файлы и маркеры `.kaskad_*` закрыты (`location ~ /\. { return 404; }` после ACME);
     - корень `paths.site_root()`.
   - **Панель:** `location ^~ /<path>/ { proxy_pass http://unix:<proxy.sock>:/; proxy_set_header Host $host;
     X-Real-IP $remote_addr; X-Forwarded-For $remote_addr; X-Forwarded-Proto https; X-Forwarded-Host $host;
     X-Forwarded-Prefix /<path>; proxy_http_version 1.1; proxy_read_timeout 30m; proxy_send_timeout 30m;
     client_max_body_size 1m; access_log off; error_log /dev/null crit; }`.
     - Путь и слушатель — существующий механизм 4.0-A: `panel.conf.set_proxy(on=True, domain=<домен>, new_path=…)`,
       `kaskadctl panel proxy`.
     - Новый путь при первом включении сайта; при `site mode domain` прокси панели выключается.
   - **Страницы людей:** `location ^~ /p/` → тот же сокет, те же заголовки, `limit_req zone=kaskad_pages burst=40
     nodelay` (5r/s), `limit_req_status 404`, `access_log off`.
   - **Глушилка** (после живых `^~`, до regex сайта): `location ~ "^/(app-[A-Za-z0-9_-]{86}|app-[0-9a-f]{12,128})(/|$)"
     { access_log off; error_log /dev/null crit; return 404; }`. Ответ неотличим от обычного 404 сайта: у сайта
     свой `error_page 404` (страница шаблона или простая).
   - **Нет SNI или чужое имя на 443** — блок `server { listen 443 ssl; ssl_reject_handshake on; }` только если на
     443 нет других наших или чужих vhost (по `nginx -T`). Иначе не ставим.
4. **Продление при nginx.**
   - При переходе в `site`: renewal-конфиг домена → webroot `/var/www/kaskad-acme` (`certbot reconfigure
     --cert-name <д> --webroot -w /var/www/kaskad-acme`, если certbot умеет; иначе правка `[renewalparams]`
     `authenticator = webroot` + `[[webroot_map]]` с копией прежнего файла).
   - При выходе из `site` — обратно `standalone`.
   - Первый выпуск в режиме `site` (смена домена) идёт через webroot.
   - Тесты: файлы renewal до и после, откат при сбое.
5. **Порты и соседи.**
   - Перед `site`: TCP/443 и TCP/80 по `ops.port_conflicts` и `ss`. Мешают проброс tcp/443, SSTP/ocserv на 443,
     чужой слушатель.
   - Чужой nginx определяем по `sites-enabled`: goamnezia, gotelegram, govless, 3x-ui. Отказ `neighbour` называет
     соседа и что можно сделать: оставить режим `domain`, перенести вход, ждать 4.1 «всё на 443».
   - Наш nginx на 80/443 — не конфликт.
   - `conflicts.check`: проброс tcp/443 или tcp/80 при `site` — конфликт вида `site`: «сайт перестанет
     открываться» / «сертификат перестанет продлеваться».
   - UFW активен → `ufw allow 443/tcp comment kaskad-site` (снимается с сайтом).
6. **Шаблон.**
   - `site mode site [--template ID | --random]`: по умолчанию случайный (`templates.deploy`).
   - `site template set` при режиме `site` — после развёртывания `nginx -t` + reload не нужен: корень тот же,
     подмена атомарная.
   - В `state.site`: `template_id`, `template_source`, `deployed_at` — из `templates.status()`.
7. **Проверка сайта** `site check`: `curl --max-time 10 --resolve <д>:443:<свой IP>` (и `:80`).
   - `https://<д>/` → 2xx/3xx;
   - `https://<д>/<путь панели>/` → 200/302;
   - `https://<д>/app-<случайный>/` → 404;
   - `http://<д>/` → 301;
   - срок сертификата.
   - Вердикты словами: `ok | dns_mismatch | no_cert | http_error | no_site | cert_expiring | panel_unreachable`.
8. **Выключение и удаление.**
   - `site mode domain`:
     - снять vhost и проверить `nginx -t`; если упал из-за чужого конфига — вернуть vhost, отказ;
     - reload;
     - prox панели off;
     - UFW 443 снять;
     - renewal → standalone;
     - файлы сайта оставить.
   - `site remove` (меню 15): то же, плюс `/var/www/kaskad-site` и `/var/www/kaskad-acme` удаляются. Защита путей
     как у goAmnezia: только наши каталоги, глубина ≥ 2. nginx и certbot — в строке пакетов.

**CLI** (у всех `--json`):
- `site mode site [--template ID|--random]`;
- `site mode domain`;
- `site panel-url [--show]` — адрес панели на домене; полный только с `--show`, иначе маска `app-AbCd••••WxYz`;
- `site panel-path rotate` — новый секретный путь, старый сразу 404;
- `site check`.

`site status` (`STATUS`) дополняется:
```
"mode": "off"|"domain"|"site",
"site": {"template": {"id","name","source","deployed_at"} | null, "root": "...",
         "panel_url_masked": "https://<д>/app-AbCd••••WxYz/" | null,
         "nginx": {"installed": bool, "active": bool, "version": "1.24.0" | null, "ours": bool},
         "port443": {"free": bool, "who": null | "текст"}, "check": {последняя проверка} | null}
```

**API:**
- `PUT /api/site/mode {mode: "site"|"domain", template?: id|"random"}` — долгий: 20 мин (apt, git, nginx);
- `GET /api/site/templates?category&query&page` и `GET /api/site/categories` — через `fetch_catalog` при нужде;
- `POST /api/site/template {id|random}` — долгий;
- `GET /api/site/panel-url` — полный адрес;
- `POST /api/site/panel-path/rotate`;
- `POST /api/site/check`.

Ответы — те же JSON, что у CLI. Маршруты в `site/panel.py`.

Уточнение исполнителя: `limit_req_zone kaskad_pages` находится первой строкой собственного
`sites-available/kaskad`: в штатном Debian/Ubuntu этот файл включается из контекста `http`, поэтому зона валидна,
а `nginx.conf` и `conf.d/` не меняются. При неудаче `nginx -t` прежние файл и ссылка восстанавливаются до ответа.

Уточнение исполнителя (живая проверка 4.0-C, порт 80): nginx «наш» (`site/nginx.py: owned()`) — его поставил Kaskad
(строка `nginx` в `/etc/kaskad/site/pkgs`), и в `sites-enabled` кроме ссылки Debian `default` → `sites-available/default`
и наших `kaskad`, `kaskad-acme` ничего нет, а в `conf.d/*.conf` нет блоков `server`/`listen`. Иначе nginx «чужой».
- Режим `domain`, nginx наш: наши vhost сняты, `systemctl disable --now nginx`, продление `standalone`. Режим `site`:
  `systemctl enable nginx`. Повторный `site mode domain`, `site cert issue` и сторож `site tick` чинят узел, где наш
  nginx остался на :80 (3.2.18).
- Режим `domain`, nginx чужой и запущен: `sites-available/kaskad-acme` (0600, ссылка в `sites-enabled`):
  `server_name <домен>`, только `/.well-known/acme-challenge/` из `/var/www/kaskad-acme`, остальное 404; продление и
  выпуск — `webroot`; `port80` этот nginx занятым не считает. Чужой nginx остановлен — его не запускаем, `standalone`.
  Ставится и при `off → domain` (`site domain`), снимается `site domain --clear`. Замена `kaskad` ↔ `kaskad-acme` —
  одна проверка `nginx -t`, при отказе возвращаются оба файла.
- Сайт на нашем nginx: ссылка `sites-enabled/default` снимается в той же транзакции (маркер
  `/etc/kaskad/site/nginx-default` с целью ссылки), `site remove` возвращает её, если `sites-available/default` на
  месте. На :80 — `server { listen 80 default_server; server_name _; return 444; }`, если по `nginx -T` других
  слушателей :80, кроме наших файлов (и снимаемого `default`), нет — так же, как `ssl_reject_handshake` на 443.
  Чужой nginx: `default` не трогаем, `site check` добавляет в `warnings` «на порту 80 отвечает страница nginx по
  умолчанию — сервер выдаёт себя».
- Подсчёт чужих `listen 443`/`listen 80` идёт по разделам `# configuration file …` вывода `nginx -T` без наших файлов
  (3.2.18 считала собственные строки `listen` и при `panel-path rotate` с IPv6 снимала `ssl_reject_handshake`).
- `certbot` «Could not bind TCP port 80» → причина `port80`; `cert renew` и сторож пишут, кто держит порт («Порт 80/tcp
  занят: его слушает nginx… повторите продление»), а не вывод certbot.
- Проброс tcp/443 и tcp/80 в режиме `site` при нашем nginx — одна причина вида `site`, без строки про слушателя nginx.

Уточнение исполнителя (отказы `site mode site`): у всех один вид JSON `{ok: false, reason, text, error, code}`, код
выхода 3, HTTP 409. Занят TCP/80 — `reason: "port80"` (раньше `port443`), текст называет, кто держит порт, и что
сделать. Шаблон не развёрнут — `reason: "template"`, `template_reason`: `download_failed | no_index | app_shell |
broken_assets | timeout | too_big | bad_repo | nokey | net`, `template_id`; шаблон ставится до nginx и renewal, поэтому
режим `domain`, nginx и продление остаются как были. Не поставился git — `apt`; занятость (`LOCKED`) и неверный номер
шаблона (`USAGE`) — прежние ошибки. Случайный шаблон: после трёх неудач на пустом корне сайт включается с простой
страницей, в ответе `fallback: true` (и `warnings` развёртывания). Сбой после установки nginx из режима `domain`
оставляет наш nginx выключенным (`disable --now`).

**Файлы C-ядра:**
- `lib/kaskad/site/` — новый `nginx.py` и правки `ops.py`, `cli.py`, `panel.py`, `model.py`, `acme.py`;
- `lib/kaskad/conflicts.py`;
- `lib/kaskad/panel/conf.py` — только если нужно для прокси;
- тесты `tests/site/*`.

`install.sh`, `static/*`, бот — не трогать (это C-лицо). Для контрактного теста панели новые маршруты временно в
`AWAITING_FACE`.

## 8. «Всё на 443» (4.1)

- Фронт — **HAProxy** (tcp, `req.ssl_sni`, PROXY на каждый `server` отдельно): наш домен (и `vpn.<домен>` для SSTP,
  ocserv) → локальные nginx/accel-ppp/ocserv (с PROXY: реальный адрес клиента), остальное и пустой SNI → выход
  VLESS (без PROXY, как сегодня DNAT+masquerade). Проброс TCP/443 на выход при этом снимается из nft и становится
  «пробросом через фронт» (в списке пробросов виден как раньше, счётчики — из HAProxy).
- UDP/443 — одно из: AWG-вход шлюза (снять 443 из `AVOID_PORTS` только в этом режиме), проброс UDP на выход.
  Alt-Svc — переключатель, по умолчанию выключен (как goAmnezia).
- До решения «по умолчанию» — замер на стенде: iperf через DNAT и через HAProxy на 1 vCPU (ЦП, Мбит/с, задержка),
  результат — в эту спецификацию.
- LE на IP (без домена): lego (бинарник по выбору), профиль `shortlived`, обновление раз в 2–3 дня (ARI);
  для `/p/` и открытой панели. SSTP/Windows на IP-сертификате — только после живого теста.

## 9. Проверка

- Офлайн: каждое решение — тест (`tests/site/*`, `tests/panel/*` за прокси, `tools/k3_install_test.sh` для меню).
- Стенд: G1 + поддомен в тестовой зоне Cloudflare (запись с `comment: claude-test`, только свои записи):
  DNS-проверка, выпуск, сайт, панель по секретному пути, `/p/`, глушилка, продление (`--dry-run`), удаление,
  соседство с занятым 443. Тестер — Sonnet «как человек», отчёт `stand/logs/T-W4*.md`.
