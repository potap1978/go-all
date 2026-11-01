# Kaskad 3, волна 3 (3.2): правила, панель, бот, ускорение AmneziaWG

Основа — ветка `k3` после выпуска 3.1.0 в `test` (f1592b8). Решения владельца:

- правила RoscomVPN: российские сайты и сети — напрямую, реклама — блок, YouTube/Telegram/GitHub/Google Play — всегда
  через выход; списки качаем у автора, в репозиторий не кладём;
- веб-панель со схемой (люди → устройства → входы → правила → пул → выходы), дизайн-система goAmnezia pro
  (токены, компоненты, иконки Gravity UI, «Вид»), без переноса её бэкенда;
- бот на Python (вместо бота на bash) + управление шлюзом;
- страница пользователя;
- ускорение AmneziaWG модулем ядра через DKMS с автоматическим откатом на amneziawg-go (2026-10-05).

Нагрузка: панель и сборщик вместе — не больше **3–5 % одного ядра** при трёх открытых вкладках (урок goAmnezia:
их панель после всех правок ≈14 % на 1 vCPU — потому что читает через bash-мост). Только stdlib Python 3.

## 1. Процессы и данные

| Служба | Что делает | Новое |
|---|---|---|
| `kaskad-gw-collect` | сторож выходов, сессии, ноги (есть) | пишет **`/run/kaskad/live.json`**: сессии, счётчики, живость выходов, состояние входов, CPU/RAM; раз в 10 с, а пока есть подписчики панели (файл-маяк `/run/kaskad/live.want`, mtime ≤ 30 с) — раз в 2 с |
| `kaskad-panel` | HTTP API, статика, SSE, страница пользователя | новая, `python3 -m kaskad panel serve`, один процесс, ThreadingHTTPServer |
| `kaskad-bot` | бот Telegram | ExecStart переходит с `gokaskad --bot-daemon` на `kaskadctl bot run` (Python); bash-бот остаётся запасным (§4.6) |
| `kaskad-gw-rules.timer` | обновление списков RoscomVPN раз в сутки | новая (§2) |

Правила, общие для панели и бота:

1. **Чтение — в своём процессе.** `state.json`, `secrets.json`, `live.json`, `exits.json` читаются через общий кэш
   `kaskad.snapshot`: ключ — путь, отпечаток — (inode, mtime_ns, size), снимается ДО чтения; повторное чтение без
   изменений — из памяти. Никаких `kaskadctl`/bash/jq на чтение.
2. **Изменения — тем же кодом, что CLI**: `kaskad.gw.ops`, `kaskad.forwards`, … под `state.locked()`. Каждый успешный
   вызов поднимает поколение (`snapshot.bump()`), старые сборы не перезаписывают новый снимок.
3. **Одинаковые одновременные чтения — одна работа** (single-flight, очередь ≤ 32), ошибка отпускает всех.
4. Тяжёлое (nft list, conntrack, ss) делает только сборщик, не панель и не бот.
5. Неизвестно ≠ 0: нет данных — `null` и «—», не ноль.

## 2. Правила «Россия напрямую» (WP-R)

Исследование 2026-10-05 (RoscomVPN, runetfreedom, формат .dat, dnsmasq nftset, Xray).

**Источник — RoscomVPN** (hydraponique), качается на самом узле, в пакет не вшивается (geoip и routing без лицензии,
geosite — MIT; данные geoip из GeoLite2/DB-IP — атрибуция в «Правилах» и документации):
- IP: `https://github.com/hydraponique/roscomvpn-geoip/releases/latest/download/geoip.dat` (+ `.sha256`, сравнивать
  первое поле) и текстовый `release/text/direct.txt` (≈35,6 тыс. CIDR IPv4 → после слияния ≈15 тыс. диапазонов);
  зеркало jsDelivr `https://cdn.jsdelivr.net/gh/hydraponique/roscomvpn-geoip/release/...`; запасной —
  `runetfreedom/russia-blocked-geoip` ветка `release` `text/ru.txt` (≈13 тыс. CIDR, GPL-3.0).
- Домены: `https://github.com/hydraponique/roscomvpn-geosite/releases/latest/download/geosite.dat` (68 КБ, 23
  категории, ≈3,2 тыс. записей) — разбирает свой stdlib-декодер protobuf (GeoSiteList/GeoSite/Domain, поля и
  подводные камни — в отчёте исследования, `docs/k3/research/rules.md`).
- Обновление: geoip — ежедневно (таймер раз в сутки, случайная минута), geosite — по выходу релиза; хранить последнюю
  удачную копию в `/var/lib/kaskad/rules/`, атомарная замена, проверка sha256 и разбора до замены.

**Группы (как профиль DEFAULT RoscomVPN), порядок проверки — блок → «всегда через выход» → «Россия напрямую» → маршрут:**
- блок: `win-spy`, `category-ads` (+ переключатель «Блокировать рекламу», по умолчанию вкл.); `torrent` — отдельный
  переключатель, по умолчанию выкл. (торренты идут по маршруту устройства);
- всегда через выход: `youtube`, `telegram`, `github`, `google-play`, `twitch-ads`, `category-geoblock-ru`,
  `google-deepmind` (сайты, которые сами режут российские адреса — для узла в РФ это главное);
- напрямую: `geoip:direct` (основной объём), домены `private`, `category-ru`, `whitelist`, `apple`, `microsoft`,
  `epicgames`, `riot`, `steam`, `escapefromtarkov`, `twitch`, `pinterest`, `faceit`.
  `keyword:`/`regexp:` в dnsmasq не выразить — для ядра пропускаются (есть в xray).

**Профиль** у пользователя и устройства: `inherit` / `all_exit` / `ru_direct`; по умолчанию узла — `ru_direct`, если
списки скачаны, иначе всё как `all_exit` (без списков ничего не уходит напрямую).

**Ядро (входы WG/AWG/PPP/IKEv2/OpenVPN/OpenConnect):**
- `gw_ru` — статические диапазоны России. Тип — **составной** `ipv4_addr . inet_proto`, `flags interval`
  (элементы `a.b.c.d/n . 0-255`), потому что libnftables при каждом `add element` от dnsmasq выгружает элементы всех
  НЕсоставных интервальных наборов во всех таблицах (+0,1–0,3 с на каждое добавление при ≈30 тыс. элементов, DNS
  встанет). Перед выбором — замер на стенде (`time nft add element` в простой набор при загруженном `gw_ru` обоих типов);
  если составной не нужен/не работает на nft 1.0.6 — простой интервальный с `auto-merge`, но тогда динамические наборы
  наполняются не dnsmasq, а пачками.
- `gw_ru_dns` (адреса доменов «напрямую») и `gw_force` (адреса доменов «всегда через выход») — простые (hash)
  наборы `flags timeout; timeout 6h`, наполняет dnsmasq: `nftset=/домен/4#ip#kaskad#gw_ru_dns` (одна строка на домен,
  строка короче 1000 символов); dnsmasq `max-cache-ttl 600`, чтобы адреса из кэша не «выпадали» из наборов.
- `gw_rd` — адреса клиентов с профилем `ru_direct` (из устройств; динамические IKEv2 — хуком).
- gw_pre (после выбора маршрута, до TPROXY): `ip saddr @gw_rd ip daddr . meta l4proto @gw_ru ip daddr != @gw_force
  meta mark != 0x2ff meta mark set 0x201` и то же для `ip daddr @gw_ru_dns`.
- Реклама: `address=/домен/` в dnsmasq (ответ «нет такого адреса»), ≈800 строк.

**xray (SOCKS5):** файлы как `ext:roscomvpn-geoip.dat:direct` / `ext:roscomvpn-geosite.dat:<категория>` в каталоге
ресурсов xray шлюза; правила по `user` для SOCKS5-устройств с профилем `ru_direct`: блок → через выход → напрямую.
Клиенты ядра, ушедшие в xray (TPROXY), к этому моменту уже разделены в nft — для них правил в xray не нужно.

**Схема и панель:** колонка «Правила»; в «Правилах» — дата списков, источник, счётчики (сколько адресов/доменов),
«Обновить сейчас», переключатели «Реклама», «Торренты», профиль по умолчанию.

## 3. Панель (WP-P1 сервер, WP-P2 интерфейс, WP-P3 схема)

### 3.1 Доступ

- По умолчанию слушает **127.0.0.1:1980** — вход через SSH-туннель (`ssh -L 1980:127.0.0.1:1980 root@узел`), меню 17
  показывает команду.
- **Открытый режим** (выключен по умолчанию, «Панель → Доступ из интернета»): случайный порт 30000–60999 на внешнем
  адресе, HTTPS с самоподписанным сертификатом узла (отпечаток SHA-256 показывается в меню и боте), вход только по
  паролю или по одноразовой ссылке из бота. Доменный режим и настоящий сертификат — волна 4.
- Вход: пароль (scrypt, `/etc/kaskad/panel/password.json` 0600, пол ответа 0,35 с, KDF и при неверном логине,
  не больше 2 KDF одновременно, 5 попыток / 15 мин на IP); одноразовая ссылка из SSH-меню или бота (`/login?t=…`,
  1 час, сгорает при входе, токен не в журнале и не в Referer: сразу 303 на `/`); туннель без пароля — только
  петля без `X-Forwarded-*` (как в goAmnezia, риск «любой локальный процесс = админ» принят).
- Сессии в памяти, 12 ч простоя; cookie `HttpOnly; SameSite=Strict; Path=/` (+ `Secure` в HTTPS); изменения только
  POST/PUT/DELETE с заголовком `X-Kaskad-Admin: 1` (CSRF); чтения, которые запускают команды на узле
  (`runs=True`: `/api/site`, `/api/bot`, журнал протокола, цена «Проверить сервер», `/api/gw/vless/ask` — что спросит
  действие VLESS), чужой странице — 403, как изменения
  (`Origin` не свой или `null`, `Sec-Fetch-Site: cross-site`; 3.3.7.7, REV-3377-stand-fix 1); проверка `Host` (защита от DNS-rebinding); CSP
  `default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'`; без журнала запросов.
- Перенос правил входа goAmnezia (`server.py` 774–866, 1078–1500, 5028–5110) — паттерн, код пишем свой модулем
  `kaskad.panel.auth`.

### 3.2 API (JSON, префикс `/api`)

| Метод и путь | Что |
|---|---|
| `GET overview` | шлюз вкл/выкл, входы (работает/сессий), выходы (живы), устройства (кто в сети), пробросы (число, трафик), CPU/RAM, версия, предупреждения, `gen` (меняется вместе с событием `state`) |
| `GET forwards` · `POST forwards` · `DELETE forwards/{proto}/{port}` | пробросы волны 1 (`kaskad.forwards`) |
| `GET gw` · `POST gw/enable` · `POST gw/disable` · `PUT gw/host` · `PUT gw/route` | шлюз |
| `GET gw/inbounds` · `POST gw/inbounds/{kind}/{install,uninstall,enable,disable}` · `PUT gw/inbounds/{kind}/port` · `PUT gw/inbounds/{kind}/option` | протоколы |
| `GET gw/people` · `POST gw/users` · `DELETE gw/users/{id}` · `PUT gw/users/{id}/route` · `POST gw/devices` · `DELETE gw/devices/{id}` · `PUT gw/devices/{id}/{access,route,rules}` · `POST gw/devices/{id}/reset` | люди и устройства |
| `PUT gw/users/{id}/enabled` · `PUT gw/devices/{id}/enabled` | пауза человека и устройства (`{enabled}`): ключи остаются, подключиться нельзя |
| `GET gw/users/{id}/page` · `POST gw/users/{id}/page` | страница человека (§5): `{action: enable\|rotate\|disable}`; ссылка — только в ответе этого маршрута |
| `GET gw/offers?platform=` | что дать устройству платформы первым (`issue_os.MATRIX`) — предвыбор входа в «Добавить устройство» |
| `POST gw/devices/{id}/issue` | выдача (секреты): только POST + CSRF, `Cache-Control: no-store`, не попадает в снимки |
| `GET gw/exits` · `PUT gw/exits/{id}` | выходы |
| `GET rules` · `POST rules/update` · `PUT rules/profile` | правила (§2) |
| `GET scheme` · `PUT scheme/layout` · `POST scheme/apply` (`dry_run`) | схема (§3.5) |
| `GET events` | SSE (§3.3) |
| `GET appearance` · `PUT appearance` | «Вид» (`/etc/kaskad/panel/appearance.json`, белый список полей) |
| `GET panel` · `PUT panel/password` · `PUT panel/public` · `PUT panel/settings` · `POST panel/link` | доступ к панели, как меню 18: пароль, доступ из интернета (без пароля не включается), пароль в туннеле, одноразовая ссылка |
| `GET github` · `PUT github` · `DELETE github` · `POST github/check` | ключ GitHub (`kaskad.ghkey`): ядро AmneziaWG и обновления; PUT проверяет формат и то, что GitHub отдаёт с ним ядро; ключ в ответах и журнале не появляется никогда |

Ответ при ошибке: `{"ok": false, "error": "<русский текст>", "code": <код kaskad.errors>}` и HTTP 400/409/500.
Имена/подписи пользователей в ответах экранирует клиент (textContent), сервер не отдаёт HTML.

### 3.3 SSE `/api/events`

`text/event-stream`, `X-Accel-Buffering: no`, heartbeat-комментарий каждые 15 с, `retry: 3000`. События:
`live` (сессии, счётчики по входам/устройствам, живость выходов — только изменившиеся поля), `state` (поколение
изменилось — клиент перечитывает нужные разделы), `alert` (выход умер/ожил, вход упал). Подписчик обновляет маяк
`/run/kaskad/live.want`; нет подписчиков 30 с — сборщик снова раз в 10 с. Лимит: 8 потоков SSE на процесс.
Нет свежих данных (live.json старше 30 с — сборщик остановлен или завис — или его ещё нет): `live` — `{"full": true}`
без данных, и в `GET scheme` `live: null`; панель показывает «неизвестно», а не «работает» и «0 сессий».
Запасной путь клиента — опрос `GET overview` (следующий — после ответа, пауза на скрытой вкладке, ×2 при сбое до 60 с).

### 3.4 Интерфейс

- Статика — `lib/kaskad/panel/static/` (`index.html`, `app.js`, `graph.js`, `styles.css`, `icons.js`, `LICENSE-*`),
  без сборки и CDN; встраивается в payload `install.sh` (`tools/build_payload.py` берёт и `*.js/*.css/*.html/*.txt`
  из `lib/kaskad/panel/static`). Отдача: `?v=<версия>-<sha10>` → `Cache-Control: immutable`, gzip в памяти.
- Дизайн-система из goAmnezia pro (копия с заголовками лицензий): токены `admin-web/static/styles.css` 11–180,
  компоненты 181–738, 833–1123, телефон 1500+; иконки Gravity UI (`ICONS`, MIT, `LICENSE.txt` в поставку);
  «Вид» (`app.js` 6817–7180). Тексты — русский словарь отдельным файлом.
- Разделы: Обзор · Пробросы · Протоколы · Люди и устройства · Выходы · Правила · Схема · Настройки (Вид, доступ к
  панели, бот, ускорение AmneziaWG).
- Телефон: всё работает от 360 px, поля ≥ 16 px (iOS не зумит), цели ≥ 44 px.

### 3.5 Схема

- Основа — `hub-plan/src/proto.js` (ядро ≈ 65 КБ: 198–760, 1162–1263, 1352–1563) → `graph.js`; данные в форме React
  Flow (`node {id, type, position, data}`, `edge {source, sourceHandle, target, targetHandle}`).
- Граф строит сервер из `state.json` (`GET scheme`): узлы user (с devices), inbound, rules (профили), pool, exit,
  direct, block; рёбра — доступ устройства к входу, маршрут устройства/пользователя (через профиль правил).
  Раскладка (x, y) — отдельно, `/etc/kaskad/panel/layout.json`, с версией (конфликт двух вкладок — 409).
- «Применить»: клиент шлёт список намерений (`grant`, `revoke`, `route`, `rules`), сервер проверяет теми же функциями
  `ops` (`dry_run` для диалога различий), применяет по порядку, отдаёт итог по каждому. Проверки `canConnect`/`validate`
  на клиенте — только подсказки; источник истины — Python.
- Живые цифры по рёбрам — из события `live`; без бесконечных CSS-анимаций и без перестройки всего SVG на каждый тик.
- Телефон (< 720 px): список по этапам с «Направить в…», холст — только просмотр.

### 3.6 Приёмка нагрузки

`tools/panel_load.py` (по образцу goAmnezia `tests/live/panel_load.py`): N вкладок (SSE + опрос), 10 минут, CPU панели
и сборщика из cgroup (`cpu.stat`) — не больше 3–5 % ядра при 3 вкладках на 1 vCPU; форков в секунду — 0.

## 4. Бот на Python (WP-B)

### 4.1 Что умеет (паритет с ботом на bash, опись 2026-10-05)

Команды `/start /menu /id /help /inline`; меню inline и reply (`MENU_STYLE`); пробросы (AWG/VLESS/MTProto/Custom —
диалоги с проверкой сервера, список, удаление, «Сбросить всё»); ping (1×, 10×, 60 с); мониторинг (добавить, список,
удалить; файлы `monitors/<ip>.conf` тех же форматов); «Система»; «Хостинг» (промо); имена из `aliases`.

### 4.2 Шлюз в боте

Состояние шлюза и входов; люди и устройства (список, добавить устройство с выбором платформы и входов, удалить,
сменить маршрут, сменить ключи); **выдача** — файлами (`.conf`, `.ovpn`, `.mobileconfig`, `.ps1`), QR (PNG через
`qrencode`, текст — через stdin) и текстом `vpn://`; выходы (живы/нет); одноразовая ссылка входа в панель (§3.1).

### 4.3 Что не повторяем (опись, §7)

offset — ПОСЛЕ обработки; нажатия старше 10 минут не выполняются («кнопка устарела»); удаление — по ключу
`proto:port`, не по номеру строки; сбой отправки не пропускает чужого; списки с пагинацией (≤ 30 строк, ≤ 90 кнопок);
таймауты на все запросы к API, backoff ×2 до 60 с, разбор ответа (`ok`, `retry_after` при 429); диалоги с TTL 30 мин
и отменой любой кнопкой меню; алерт — `last_alert` пишется только после удачной отправки, «восстановлено» после
падения; токен не в argv (HTTP-клиент в процессе).

### 4.4 Рантайм

Long polling `getUpdates` (timeout 30, `allowed_updates` = message, callback_query), один поток приёма + пул из 4
потоков на действия (ping, проверки, выдача), лимит 1 действие на чат одновременно. Прокси для API Telegram: свой
клиент SOCKS5 (RFC 1928, без пароля, имя через прокси — как `socks5h`) поверх `socket` + `ssl`; `BOT_PROXY`
из `config` (как в 2.x). Доступ: `BOT_CHAT_ID` (+ необязательный список `BOT_ADMINS`); чужим — один ответ «Нет
доступа» в сутки на чат.

### 4.5 Монитор

Проверки мониторинга переезжают в процесс бота (поток): ICMP одним пакетом + TCP на порт проброса, 3 неудачи подряд —
алерт, «восстановлено» при первой удаче; `kaskad-monitor.service` выключается, когда работает бот на Python.
Алерты шлюза: выход умер/ожил, вход не работает, части шлюза не было (из `live.json`/журнала сборщика).

### 4.6 Переход и откат

`kaskadctl bot run` — новый режим; юнит `kaskad-bot.service` переписывает `k3_post_update` на него, когда
`BOT_IMPL` в `config` ≠ `bash`. «Вернуться на 2.13» и `BOT_IMPL="bash"` возвращают бота на bash. Форматы `config`,
`aliases`, `monitors/*.conf`, `bot_state/` сохраняются (2.13 их читает).

## 5. Страница пользователя (WP-U)

Ссылка `/p/<43 символа base64url>` у человека (выдаёт админ в панели/боте). Ключей в первом HTML нет: после жеста
(`isTrusted`) страница шлёт POST с одноразовым nonce и получает настройки своих устройств; QR — SVG из матрицы
модулей, построенной на сервере. Подходящая вкладка по ОС (User-Agent + `maxTouchPoints` для iPadOS). Неверная
ссылка — 404 как у пустого сайта; 10 промахов за 5 мин с адреса — бан на 15 мин.

**Открытый вопрос:** без домена страница доступна только по HTTPS с самоподписанным сертификатом (предупреждение
браузера, на телефоне это пугает). Предложение: в 3.2 страница работает в открытом режиме панели, а «чистый» HTTPS —
вместе с доменом в волне 4; проверить, выдаёт ли Let's Encrypt сертификаты на IP-адрес (короткоживущие) — тогда и без
домена.

## 6. Ускорение AmneziaWG модулем ядра (WP-K)

Исследование 2026-10-05: модуль `amneziawg-linux-kernel-module` (master = тег v3.1.20260906) реализует весь набор
ключей amneziawg-go 3.1 (UAPI `amneziawg`: S3/S4, H-диапазоны NLA_U64, I1–I5, HeaderProtectionKey, таймеры-диапазоны,
RandomTrailers, DisableCookies), но 3.x в ядре с 30.07–12.08.2026 и с открытыми ошибками совместимости
(#222: клиент-модуль не рукопожимается с amneziawg-go-сервером при HeaderProtectionKey; #226: потери ≈6,8 % при
RandomTrailers; #227: RejectAfterTime на data-path). Поэтому:

- **в ядро** — входы `awg1` (1.0/1.5), `awg2` (2.0) и ноги выходов линии 2.0; **на amneziawg-go** — вход `awg3`
  (3.1) и ноги 3.1, пока не выйдет тег новее 06.09.2026 с закрытыми #222/#226/#227 (флаг в коде, тест-переключатель);
- установка: DKMS из исходников по закреплённому тегу v3.1.20260906 (архив GitHub с sha256 в коде, `PACKAGE_VERSION`
  в `dkms.conf` = тег), `dkms`, `gcc`, `make`, `linux-headers-$(uname -r)` + метапакет заголовков своего флейвора
  (cloud/kvm/generic — чтобы следующее ядро тоже собралось); полные исходники ядра не нужны; PPA — не используем
  (сам меняет поколение модуля);
- **Ubuntu 26.04, ядра 7.0.0-38+**: Ubuntu бэкпортировала новую сигнатуру `setup_udp_tunnel_sock` (`struct sock *`)
  — без патча (PR #218: определение сигнатуры при сборке) модуль не собирается; патч — в репозитории Kaskad
  (`lib/kaskad/vendor/awg-kmod-218.patch`, GPL-2.0, с атрибуцией), применяется только когда в заголовках новая
  сигнатура;
- **обновления ядра не должны ломать apt**: `AUTOINSTALL=no` в dkms.conf, свой хук `/etc/kernel/postinst.d/` с
  `dkms build/install -k <версия> || true`; при старте узла — `modprobe amneziawg`; нет модуля для текущего ядра —
  интерфейсы этих линий на amneziawg-go, предупреждение в `gw status`, меню и боте;
- проверка перед включением: самопроверка (пара интерфейсов ядро↔ядро и ядро↔amneziawg-go в netns с параметрами
  линии, рукопожатие + ping), валидация параметров до `awg setconf` (Jmin < Jmax, S1–S4, синтаксис I1–I5 — у модуля
  есть паника при Jmin > Jmax, #254); смена параметров = пересоздание интерфейса; `ip link del` при остановке (интерфейс
  ядра переживает процесс — fail-closed проверить);
- Secure Boot: `mokutil --sb-state` — включён → не ставим, объясняем; LXC/OpenVZ (нельзя грузить модули) → не
  предлагаем;
- «Протоколы → Ускорить AmneziaWG»: включить (поставить и перевести линии), выключить (обратно на amneziawg-go,
  модуль удалить), состояние по каждому интерфейсу («ядро» / «программа» / «откат: модуль не собрался под ядро …»).

## 7. Пакеты работ

| Пакет | Модель | Что | Файлы |
|---|---|---|---|
| WP-R | Opus | правила целиком: загрузка, декодер `.dat`, хранение, таймер, профили в модели, nft-наборы, dnsmasq `nftset=`/блок рекламы, xray routing, `kaskadctl rules`, маршруты панели | `lib/kaskad/rules/*`, свои функции в `lib/kaskad/gw/{net,dns,xray,model}.py` |
| WP-P1 | Opus | сервер панели: auth, API, SSE, снимки, статика, `kaskadctl panel …`, юнит | `lib/kaskad/panel/{server,auth,api,sse}.py`, `lib/kaskad/snapshot.py`, сборщик → `live.json` |
| WP-P2 | Sonnet | интерфейс: дизайн-система, разделы, «Вид» | `lib/kaskad/panel/static/{index.html,app.js,styles.css,icons.js}` |
| WP-P3 | Opus | схема: `graph.js`, `GET scheme`/`apply`/layout на сервере | `lib/kaskad/panel/{scheme}.py`, `static/graph.js` |
| WP-B | Opus | бот на Python + монитор | `lib/kaskad/bot/*`, `lib/kaskad/net/socks.py` |
| WP-U | Sonnet | страница пользователя | `lib/kaskad/panel/{userpage}.py`, `static/user.*` |
| WP-K | Opus | модуль ядра AmneziaWG через DKMS | `lib/kaskad/gw/awgkmod.py`, адаптер awg, ноги egress в `install.sh` |
| WP-I | Opus | интеграция в `install.sh`: меню (панель, бот, правила, ускорение), юниты, удаление, обновление | `install.sh` |

Общие правила: только stdlib; секреты не в журнале и не в argv; все тексты по-русски; `bash tools/k3_test.sh` зелёный;
payload пересобирается после изменений `lib/`; каждый пакет пишет тесты рядом (`tests/…`).

## 8. Проверка на стенде

Два почасовых узла Aeza: узел (переустанавливается под систему) и выход/клиент. Сценарии: свежая установка и
обновление 3.1.0 → 3.2; правила (российский сайт напрямую, заблокированный — через выход, реклама — блок, YouTube —
через выход даже с российским адресом кэша); панель через туннель и в открытом режиме (вход, выдача, схема
«применить», SSE, нагрузка 3 вкладки ≤ 5 %); бот (паритет, шлюз, выдача файлами, алерты, «устаревшая кнопка»);
модуль ядра на Debian 12/13, Ubuntu 24.04/26.04 (сборка, скорость до/после, обновление ядра → пересборка или откат).

## 9. Контракты между пакетами

### 9.1 Маршруты панели

`lib/kaskad/panel/routes.py` (написан до раздачи пакетов): `@route(method, pattern, auth="admin"|"public"|"page",
csrf=None, runs=False)`, `Request` (method, path, query, headers, body, match, session, client_ip, scheme, `json()`), `Response`,
`HttpError(status, text, code, data)`, `json_response`, `error_response`. Сервер (WP-P1) импортирует модули
`kaskad.panel.MODULES`: `kaskad.panel.api` (WP-P1), `kaskad.panel.scheme` (WP-P3), `kaskad.panel.userpage` (WP-U),
`kaskad.rules.panel` (WP-R2). Группы CLI: `kaskad.panel.cli` (WP-P1), `kaskad.panel.userpage_cli` (WP-U),
`kaskad.bot.cli` (WP-B), `kaskad.rules.cli` (WP-R1).

### 9.2 `live.json` (пишет сборщик, WP-P1; читают панель и бот)

`/run/kaskad/live.json`, атомарно (tmp + rename), 0600:

```json
{"v": 1, "ts": 1791190000.5, "seq": 412,
 "gateway": {"enabled": true, "problems": ["…"]},
 "inbounds": {"wg": {"running": true, "sessions": 2, "ports": "51820/udp"}},
 "exits": {"e-41a19b9cbe": {"alive": true, "kind": "vless", "label": "…", "state": "ok|dead|unknown", "since": 1791189000}},
 "devices": {"d-7ae67ae0": {"online": true, "inbound": "wg", "addr": "10.77.1.10", "since": 1791188000,
                             "rx": 123456, "tx": 654321}},
 "forwards": {"tcp:443": {"packets": 7, "bytes": 700}},
 "host": {"cpu": 3.1, "mem_total": 2063990784, "mem_used": 431000000, "load": [0.1, 0.2, 0.2], "uptime": 3600},
 "events": [{"ts": 1791189999, "kind": "exit_dead|exit_alive|inbound_down|inbound_up|gw_part_restored", "text": "…"}]}
```

`rx`/`tx` — где их дешёво взять (WireGuard/AmneziaWG `transfer`, accel-ppp, strongSwan, ocserv, OpenVPN status),
иначе `null`. `events` — последние 50 за 24 ч (бот шлёт алерты по ним, панель показывает). Маяк
`/run/kaskad/live.want`: mtime свежее 30 с — сборщик пишет раз в 2 с, иначе раз в 10 с.
3.3.5: `exit_dead`/`exit_alive` — антиспам 15 мин на вид события выхода (повтор придерживается, в конце окна —
текущее состояние со счётчиком обрывов). Что лента знает о выходах — `/run/kaskad/live-feed.json` (переживает
перезапуск сборщика), последнее сказанное о каждом выходе — ещё и `/etc/kaskad/gw/exits-told.json` (пишется на каждом
событии выхода); первый раунд после старта сверяет его с состоянием выхода и при расхождении говорит текущее.
3.3.7.9 (активность устройств, k3/handoff/SPEC-activity.md §5.3): `"activity": {"ts": 1791189900}` — начало последнего
5-минутного окна, записанного в `metrics.db` (`meta.dev_flush`); поля нет, когда история выключена
(`gateway.activity.keep: false`) или окно ещё не записано. Панель по смене `ts` перечитывает
`GET /api/gw/activity` (с `If-None-Match`; 304 — без тела). Счётчики окна сборщик держит в
`/run/kaskad/gw/activity.json` (0600, раз в минуту и после окна; переживает перезапуск процесса, старше 15 мин не
берётся). Маршруты (только админ): `GET /api/gw/activity` (сводка «Людей»: 48 окон по 30 мин, ETag),
`GET /api/gw/devices/<id>/activity?range=1d|7d|30d`, `GET /api/gw/users/<id>/activity?range=…`,
`GET|PUT /api/gw/activity/settings` (`{keep, ver}`; выключение стирает историю). CLI — `kaskadctl gw activity`,
MCP — `gw_activity` (только чтение). `/p/` активность не показывает.
3.3.7.11 (владелец 27.10 «всё до 5 с»): пока панель смотрит (маяк свежий — его взводит и поток SSE, и запасной опрос
`GET /api/overview`), то, что показывают «Пользователи» и «Выходы», не старше 5 с (`collect.LIGHT_MAX`): шаг паузы, после
которого данные стали бы старше, — лёгкий круг вместо живого: статус каждого включённого входа (по одному вызову, как
полный круг) — кто в сети и открытое окно активности; счётчики выходов (/sys, один `/debug/vars`) — открытая минута
`"metrics": {"ts", "at", "cur": {"e-…": [rx, tx]}}` (байты после точки `ts` по тому же правилу, что следующая точка
m1; панель прибавляет их к линии и объёму за сутки). Без проб, nft и блокировки: состояние выхода — по его последней
проверке (раз в 10 с). Без панели — прежний темп. Запасной опрос панели — раз в 2,5 с.

### 9.3 Подключение схемы к интерфейсу

`static/graph.js` (WP-P3) экспортирует `window.KaskadGraph = {mount(el, ctx), unmount()}`, где `ctx = {api(method,
path, body) -> Promise, onLive(fn), t(key)}`; раздел «Схема» в `app.js` (WP-P2) монтирует его в свой контейнер.
