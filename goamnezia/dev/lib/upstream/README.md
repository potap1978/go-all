# `lib/upstream` — вендоренные серверные скрипты Amnezia

Здесь лежат **официальные** скрипты клиента Amnezia, которыми ставятся и запускаются
контейнеры, плюс зафиксированные дайджесты образов ядра. goAmnezia повторяет порядок
установки приложения (PLAN §2.3) и подставляет в эти файлы свои значения — чтобы
контейнеры оставались «родными» и приложение Amnezia их понимало (PLAN §7.3).

Правило серии: **эти файлы не редактируются**, кроме одного явного исключения —
`awg/start.sh` (см. раздел «Наша правка»). Всё остальное — байт в байт как в upstream.

> **Каталог `xray/` — только справка.** goAmnezia — продукт про AmneziaWG, ядра Xray в нём
> нет (решение владельца 09.09.2026; для VLESS есть отдельный продукт серии — goVLESS).
> Файлы `xray/*` оставлены здесь как провенанс официального клиента Amnezia: по ним видно,
> что и как ставит приложение в контейнер `amnezia-xray`. Продукт их **не использует**:
> `bootstrap.sh` их на сервер не возит, `lib/upstream.sh` их не качает и не сверяет,
> `upstream-check` про них не отчитывается. Всё, что ниже сказано про обновление скриптов
> и про плейсхолдеры, относится к каталогу `awg/`; таблица sha256 и `xray_release`
> сохраняются как зафиксированный снимок и проверяются `tests/test_upstream_vendoring.py`.

## Откуда взято

| Что | Значение |
|---|---|
| репозиторий | `amnezia-vpn/amnezia-client` |
| тег | **`5.0.2.1`** (не ветка `dev` — CONTRACT §2, PLAN §6.8) |
| дата релиза тега | 2026-09-03 |
| каталоги | `client/server_scripts/awg/` (рабочий), `client/server_scripts/xray/` (справка) |
| дата загрузки | **2026-09-08** |
| способ | `curl -fsSL -o <файл> https://raw.githubusercontent.com/amnezia-vpn/amnezia-client/5.0.2.1/client/server_scripts/<awg|xray>/<файл>` |

Все 10 файлов скачались по тегу, обращаться к `dev` не пришлось (ни одного 404).
Одиннадцатый файл в таблице ниже — `awg/start.sh.upstream` — это наша копия скачанного
`awg/start.sh` до правки, чтобы `upstream-check` было с чем сверять.

**Про выбор тега.** `GET /repos/amnezia-vpn/amnezia-client/releases/latest` отдаёт `5.0.1.5`
(2026-08-21), потому что этот эндпоинт по правилам GitHub пропускает пре-релизы. Самый свежий
тег на самом деле `5.0.2.1` (2026-09-03, `prerelease: true`), и именно он зафиксирован в
CONTRACT §3.3 (`client_release`) и описан в PLAN §2.2 как текущая версия клиента. Поэтому
берём `5.0.2.1`, а `upstream-check` должен читать **список** релизов
(`GET /releases?per_page=…`) и брать первый недрафтовый, а не `releases/latest`.

## Файлы и контрольные суммы

sha256 считается по файлу на диске (`shasum -a 256`), сверяется тестом
`tests/test_upstream_vendoring.py`.

| Файл | sha256 | байт | Что это |
|---|---|---:|---|
| `awg/Dockerfile` | `9a2330227d9950f20c03022885710d5982ed6e13c80dc4acefb0b0526c30b60a` | 1461 | образ контейнера `amnezia-awg2`: `amneziavpn/amneziawg-go:latest` + bash/curl/dumb-init, тюнинг sysctl |
| `awg/configure_container.sh` | `2da679ca4c65e51ae42279fc2b14f948d8736f1b8d0ccdcac7da8f63a8f159b6` | 1548 | генерация ключей сервера (`awg genkey/pubkey/genpsk`) и запись `/opt/amnezia/awg/awg0.conf` |
| `awg/run_container.sh` | `a7ebca174111894cc17c00950c2c386b5f18bb5fc9ccdfd056b95584e0c70332` | 557 | `docker run` профиля AWG: `--privileged`, `NET_ADMIN`, `SYS_MODULE`, порт UDP, сеть `amnezia-dns-net` |
| `awg/start.sh` | `a0af343434558407a7ae6432676867d9fe3ae22b96b62544860adc617db38416` | 2415 | **наша копия** — ENTRYPOINT контейнера; отличается от upstream только идемпотентными правилами iptables |
| `awg/start.sh.upstream` | `25410e31cd6cdaff9fe5ab9440526693395083018392b9f805a4d1c45a29d89c` | 1065 | дословный upstream `start.sh`, эталон для сверки и для `upstream-check` |
| `awg/template.conf` | `115b3b4e4b0970ecc3736a4bca8b3679eed46fe7312e2d236d5341a1be4fd231` | 1128 | шаблон клиентского `.conf` (все параметры AWG 1.0–3.1) |
| `xray/Dockerfile` | `86fa5539e124084124a0ccd0fa80391abe6005656531f5d18a7d023415c668be` | 1646 | *(справка)* образ контейнера `amnezia-xray`: Alpine 3.15 + Xray `ARG XRAY_RELEASE` |
| `xray/configure_container.sh` | `1341c321dfadfe56afa228ab9f91f2f84612870982063b954ab8b5be7cf87f11` | 994 | *(справка)* генерация `uuid`, `short_id`, пары x25519 для Reality; `server.json` пишет клиент, не скрипт |
| `xray/run_container.sh` | `cf322019d34272b0185ccc138f000866673ac2243866b14c5d57ec7e021cf2a6` | 662 | *(справка)* `docker run` контейнера Xray: порт TCP+UDP, создание `/dev/net/tun` |
| `xray/start.sh` | `026b840844898960fcab2c398c7de18bb2a491b28ff8042692b713d50a21d77e` | 968 | *(справка)* ENTRYPOINT контейнера Xray (дословный upstream, наших правок нет) |
| `xray/template.json` | `a9bd5f7085168c7164b0a9857c11840fd6ceab0f5f8327b26917a01729b378c9` | 1275 | *(справка)* шаблон клиентской конфигурации VLESS + Reality (исходящее соединение) |

## Наша правка: `awg/start.sh`

`awg/start.sh.upstream` — дословный оригинал. `awg/start.sh` — наша копия, и она отличается
от оригинала **только** правилами iptables плюс шапкой-комментарием (в шапке в каждой строке
есть слово `iptables`, чтобы тест мог проверить «diff без строк с iptables пуст»).

Почему: upstream на каждом старте контейнера делает `iptables -A …`, а контейнер стартует
далеко не один раз — `--restart always`, перезагрузка хоста, `core-update`/`upstream-update`
из PLAN §6.6 и §6.8. Правила при этом не заменяются, а **добавляются**, и цепочки `INPUT`,
`FORWARD`, `OUTPUT` и `nat/POSTROUTING` копят дубли одного и того же правила. На живом
сервере после десятка рестартов это десятки одинаковых строк: мешает читать `iptables -S`,
мешает диагностике и разбору чужих правил (goWARP, nginx, fail2ban — PLAN §7.2).

Форма правки — ровно одна, механическая:

```
было:  iptables        -A ЦЕПОЧКА …
стало: iptables        -C ЦЕПОЧКА … 2>/dev/null || iptables        -A ЦЕПОЧКА …
было:  iptables -t nat -A POSTROUTING …
стало: iptables -t nat -C POSTROUTING … 2>/dev/null || iptables -t nat -A POSTROUTING …
```

Переписаны все 8 правил: `INPUT -i awg0`, `FORWARD -i awg0`, `OUTPUT -o awg0`,
два `FORWARD … -o eth0|eth1 -s $AWG_SUBNET_IP/$WIREGUARD_SUBNET_CIDR`,
`FORWARD -m state --state ESTABLISHED,RELATED` и два `nat POSTROUTING … MASQUERADE`.
`-C` возвращает ненулевой код, если правила нет, — тогда срабатывает `||` и правило
добавляется; stderr гасится, потому что при отсутствии правила iptables пишет туда шум.
Порядок правил в цепочке сохраняется: при первом старте они добавляются тем же `-A`
в том же порядке, что и в upstream.

Проверки: `bash -n` (локально `/opt/homebrew/bin/bash -n`) и `shellcheck -S warning` — чисто.

`xray/start.sh` оставлен дословным: это справочный файл, наших правок в нём нет и
не будет.

## Статические проверки: `lib/upstream/` — исключение

`tests/run_static.sh` (CONTRACT §0.12) гоняет `shellcheck -S warning` по всем `.sh`.
Два upstream-файла его не проходят — `awg/configure_container.sh` и
`xray/configure_container.sh` дают `SC2164` («`cd … || exit`»). Править их нельзя:
это чужой код, который должен остаться байт в байт. Поэтому `run_static.sh` обязан
**исключать `lib/upstream/` из shellcheck**, кроме нашего `awg/start.sh` — его проверяем
как свой (сейчас чисто). `bash -n` проходят все файлы, его исключать не нужно.

## Образы ядра — `images.json`

Схема — CONTRACT §3.3, плюс два наших поля: `amd64_digest` (дайджест образа под
`linux/amd64` внутри manifest list, если он отличается от дайджеста самого списка) и
`xray_release` (версия Xray из `ARG XRAY_RELEASE` в `xray/Dockerfile` — справочное поле
снимка, продукт его не читает: сверяет только `tests/test_upstream_vendoring.py`).

| Образ | Тег | Дайджест (manifest) | amd64 | Обновлён на Docker Hub |
|---|---|---|---|---|
| `amneziavpn/amneziawg-go` | `3.1.20260828` | `sha256:cbafc02b…b12b72` | `sha256:c68009d3…d7c13a` | 2026-08-28 |
| `amneziavpn/amnezia-wg` | `latest` | `sha256:ea050861…381250` | тот же | 2023-10-03 |

Факты, зафиксированные на 2026-09-08:

- `amneziawg-go:latest` и `amneziawg-go:3.1.20260828` — **один и тот же manifest digest**
  `sha256:cbafc02b8373a83f428272db6d8001b37bc02e6211cbd8c0cb4e2e3759b12b72`. Значение
  совпало с ориентиром из приёмки goWARP PRO от 2026-09-05, расхождений нет.
- У `amneziawg-go` дайджест manifest list и дайджест `linux/amd64` **разные** — поэтому в
  `images.json` есть `amd64_digest`. Архитектура в списке ровно одна: `linux/amd64`,
  arm64 официально не собирается (PLAN §2.1).
- У `amnezia-wg:latest` дайджест верхнего уровня совпадает с дайджестом `linux/amd64`
  (это не manifest list, а обычный манифест), поэтому `amd64_digest` не пишем.
  Образ заморожен с 2023-10-03 — это legacy-ветка AWG 1.0.
- `xray_release` = `v26.7.28`, значение берётся из `ARG XRAY_RELEASE` в `xray/Dockerfile`
  и меняется вместе с этим файлом.

## Как обновлять (основа для `upstream-check` / `upstream-update`)

Порядок ровно такой, никаких «молча подтянуть»:

1. **Тег клиента.** `GET https://api.github.com/repos/amnezia-vpn/amnezia-client/releases?per_page=10`,
   берём первый с `draft: false` (пре-релизы **не** пропускаем — см. выше про `5.0.2.1`).
   Сравниваем с `client_release` из `images.json`.
2. **Скрипты.** Для каждого файла каталога `awg/` из таблицы выше (кроме `awg/start.sh` —
   он наш; файлы `xray/*` не обновляются, это снимок-справка):
   `curl -fsSL https://raw.githubusercontent.com/amnezia-vpn/amnezia-client/<тег>/client/server_scripts/awg/<файл>`,
   считаем sha256 и сравниваем с таблицей. Для `awg/start.sh` сверяем с
   `awg/start.sh.upstream`. Изменившиеся файлы — в отчёт поимённо.
3. **Образы.** `GET https://hub.docker.com/v2/repositories/amneziavpn/<образ>/tags?page_size=100`:
   среди `results[].name` берём новейший ВЕРСИОННЫЙ тег (`^[0-9]+(\.[0-9]+){1,3}$`, `sort -V`),
   `latest` — только справка, куда он указывает; поля `digest` и `images[]` с
   `architecture: amd64`. Явный тег, которого нет на первой странице, — отдельным
   `GET …/tags/<тег>`. Сравниваем с `images.json` и с `image_digest` профилей в `config.json`.
4. **Плейсхолдеры.** Разбираем новые `template.conf` и `configure_container.sh` регуляркой
   `\$\{?[A-Za-z_][A-Za-z0-9_]*\}?` и сравниваем со списками ниже. Появился новый
   `$…` → это новый параметр протокола: `upstream-check` помечает его «новый параметр,
   не поддержан», поддержку добавляем релизом goAmnezia (PLAN §6.8). Молча подставлять
   пустое значение нельзя — так ломается совместимость линейки.
5. **Применение.** Обновление только по кнопке: `upstream-update --profile=…` кладёт новые
   скрипты, заново накатывает нашу правку iptables на `start.sh`, делает бэкап и вызывает
   `core-update`; при провале — `upstream-rollback` (храним один шаг назад).
6. **Сеть.** GitHub и Docker Hub доступны не с каждого хостинга: при ошибке сети —
   код `unreachable` и совет, меню не падает.

После любого обновления пересчитать таблицу sha256 в этом файле и прогнать
`python3 -m unittest tests.test_upstream_vendoring`.

## Механизм на сервере: `lib/upstream.sh`

Всё выше реализовано в `lib/upstream.sh` (CONTRACT §4.9). Репозиторий он не трогает — работает
с рабочей копией `$UPSTREAM_DIR` (`/opt/goamnezia/lib/upstream` на сервере, временный каталог в
тестах):

| Что | Где |
|---|---|
| скачанные скрипты релиза, как есть | `$UPSTREAM_DIR/awg/<тег>/` |
| вендоренные файлы до обновления + `previous.json` (`client_release`) | `$UPSTREAM_DIR/awg/previous/` — для `upstream-rollback` (один шаг назад) |
| закреплённый образ, которым пользуются новые установки | `$UPSTREAM_DIR/images.json` (тег, `digest`, `amd64_digest`, `checked`, `client_release` того, что реально работает) |
| предыдущий образ профиля | `config.json .profiles.<p>.previous` |
| память таймера `goamnezia-upstream.timer` | `/opt/goamnezia/upstream/last_check.json`, `notified.json` (уведомление — один раз на пару «релиз + дайджесты») |

`awg/start.sh` после обновления не копируется, а **пересобирается** из нового `start.sh`
патчем `ga_up_patch_start_sh` (шапка-комментарий + `-A` → `-C … 2>/dev/null || -A`): применённый
к текущему `start.sh.upstream`, патч даёт байт в байт текущий `awg/start.sh` — это проверяет
`tests/test_upstream_dryrun.sh`. Скрипты качаются только когда тег релиза отличается от
`client_release`: одинаковый тег — те же файлы. Фикстуры для тестов без сети —
`tests/fixtures/upstream` («есть обновление»: релиз `5.0.3.0`, образ `3.2.20260915`, новый
плейсхолдер `$CONTENT_PADDING_MAX`) и `tests/fixtures/upstream_current` («всё актуально»);
переменная `GOAMNEZIA_UPSTREAM_FIXTURE_DIR`.

## Плейсхолдеры `$…` — что подставляет генератор

Именование сохраняем как у клиента Amnezia (CONTRACT §2). Пустое значение → строка удаляется
из конфига (`sed -i '/^[^=]*= *$/d'`), это делает сам `configure_container.sh`.

### `awg/template.conf` — клиентский конфиг, 34 плейсхолдера

<!-- placeholders:awg/template.conf -->
```text
$WIREGUARD_CLIENT_IP
$PRIMARY_DNS
$SECONDARY_DNS
$WIREGUARD_CLIENT_PRIVATE_KEY
$JUNK_PACKET_COUNT
$JUNK_PACKET_MIN_SIZE
$JUNK_PACKET_MAX_SIZE
$INIT_PACKET_JUNK_SIZE
$RESPONSE_PACKET_JUNK_SIZE
$COOKIE_REPLY_PACKET_JUNK_SIZE
$TRANSPORT_PACKET_JUNK_SIZE
$INIT_PACKET_MAGIC_HEADER
$RESPONSE_PACKET_MAGIC_HEADER
$UNDERLOAD_PACKET_MAGIC_HEADER
$TRANSPORT_PACKET_MAGIC_HEADER
$SPECIAL_JUNK_1
$SPECIAL_JUNK_2
$SPECIAL_JUNK_3
$SPECIAL_JUNK_4
$SPECIAL_JUNK_5
$HEADER_PROTECTION_KEY
$CONTENT_PADDING_ADDITION
$REKEY_AFTER_TIME
$REKEY_TIMEOUT
$REJECT_AFTER_TIME
$KEEPALIVE_TIMEOUT
$MAX_HANDSHAKE_ATTEMPTS
$RANDOM_TRAILERS
$DISABLE_COOKIES
$WIREGUARD_SERVER_PUBLIC_KEY
$WIREGUARD_PSK
$SERVER_IP_ADDRESS
$AWG_SERVER_PORT
$PERSISTENT_KEEPALIVE
```

Группы: адресация и DNS (`$WIREGUARD_CLIENT_IP`, `$PRIMARY_DNS`, `$SECONDARY_DNS`),
ключи (`$WIREGUARD_CLIENT_PRIVATE_KEY`, `$WIREGUARD_SERVER_PUBLIC_KEY`, `$WIREGUARD_PSK`),
эндпоинт (`$SERVER_IP_ADDRESS`, `$AWG_SERVER_PORT`, `$PERSISTENT_KEEPALIVE`),
AWG 1.0 (`Jc/Jmin/Jmax` = `$JUNK_PACKET_*`, `S1/S2` = `$INIT_PACKET_JUNK_SIZE`,
`$RESPONSE_PACKET_JUNK_SIZE`, `H1–H4` = `$*_MAGIC_HEADER`),
1.5 (`I1–I5` = `$SPECIAL_JUNK_1..5`), 2.0 (`S3/S4` = `$COOKIE_REPLY_PACKET_JUNK_SIZE`,
`$TRANSPORT_PACKET_JUNK_SIZE`), 3.x (`$HEADER_PROTECTION_KEY`, `$CONTENT_PADDING_ADDITION`,
`$REKEY_AFTER_TIME`, `$REKEY_TIMEOUT`, `$REJECT_AFTER_TIME`, `$KEEPALIVE_TIMEOUT`,
`$MAX_HANDSHAKE_ATTEMPTS`, `$RANDOM_TRAILERS`, `$DISABLE_COOKIES`).

### `awg/configure_container.sh` — серверный конфиг, 31 плейсхолдер

<!-- placeholders:awg/configure_container.sh -->
```text
$WIREGUARD_SERVER_PRIVATE_KEY
$WIREGUARD_SERVER_PUBLIC_KEY
$WIREGUARD_PSK
$AWG_SUBNET_IP
$WIREGUARD_SUBNET_CIDR
$AWG_SERVER_PORT
$JUNK_PACKET_COUNT
$JUNK_PACKET_MIN_SIZE
$JUNK_PACKET_MAX_SIZE
$INIT_PACKET_JUNK_SIZE
$RESPONSE_PACKET_JUNK_SIZE
$COOKIE_REPLY_PACKET_JUNK_SIZE
$TRANSPORT_PACKET_JUNK_SIZE
$INIT_PACKET_MAGIC_HEADER
$RESPONSE_PACKET_MAGIC_HEADER
$UNDERLOAD_PACKET_MAGIC_HEADER
$TRANSPORT_PACKET_MAGIC_HEADER
$HEADER_PROTECTION_KEY
$CONTENT_PADDING_ADDITION
$REKEY_AFTER_TIME
$REKEY_TIMEOUT
$REJECT_AFTER_TIME
$KEEPALIVE_TIMEOUT
$MAX_HANDSHAKE_ATTEMPTS
$RANDOM_TRAILERS
$DISABLE_COOKIES
$SPECIAL_JUNK_1
$SPECIAL_JUNK_2
$SPECIAL_JUNK_3
$SPECIAL_JUNK_4
$SPECIAL_JUNK_5
```

Важно: три из них — **не наши**, это локальные переменные, которые скрипт вычисляет уже
внутри контейнера, и подставлять их снаружи нельзя:
`$WIREGUARD_SERVER_PRIVATE_KEY`, `$WIREGUARD_SERVER_PUBLIC_KEY`, `$WIREGUARD_PSK`
(в `template.conf` последние две — наоборот, подставляются: их читают обратно из
`/opt/amnezia/awg/*.key`). Остальные подставляет генератор.

Серверная часть отличается от клиентской: у сервера есть `$AWG_SUBNET_IP`,
`$WIREGUARD_SUBNET_CIDR` и `$AWG_SERVER_PORT` вместо клиентского адреса и эндпоинта,
а `I1–I5` (`$SPECIAL_JUNK_*`) записаны закомментированными — сигнатурные пакеты шлёт
только клиент.

### Прочие файлы

`awg/run_container.sh`: `$AWG_SERVER_PORT`, `$CONTAINER_NAME`, `$SERVER_IP_ADDRESS`.
`awg/start.sh`: `$SERVER_IP_ADDRESS`, `$AWG_SUBNET_IP`, `$WIREGUARD_SUBNET_CIDR`.

Ниже — плейсхолдеры справочного каталога `xray/`; продукт их не подставляет, список
оставлен как часть снимка.

`xray/template.json` (6): `$SERVER_IP_ADDRESS`, `$XRAY_SERVER_PORT`, `$XRAY_CLIENT_ID`, `$XRAY_SITE_NAME`, `$XRAY_PUBLIC_KEY`, `$XRAY_SHORT_ID`.

`xray/configure_container.sh` (5): `$XRAY_CLIENT_ID`, `$XRAY_SHORT_ID`, `$KEYPAIR`, `$XRAY_PUBLIC_KEY`, `$XRAY_PRIVATE_KEY` — здесь **все** локальные, скрипт только делает
ключи, `server.json` пишет клиент.

`xray/run_container.sh`: `$XRAY_SERVER_PORT`, `$CONTAINER_NAME`, `$SERVER_IP_ADDRESS`.
`xray/start.sh`: `$SERVER_IP_ADDRESS`, `$XRAY_SERVER_PORT`.
`xray/Dockerfile`: `$XRAY_RELEASE` — это Docker `ARG`, а не плейсхолдер генератора.
