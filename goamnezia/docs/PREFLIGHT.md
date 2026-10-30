# goAmnezia pro — предполётная проверка портов (`lib/preflight.sh`)

Модуль PLAN §4.3.1, контракт `docs/CONTRACT.md` §4.6a. Написан отдельно от `install.sh`, бота,
панели и MCP — здесь перечислено, что в них подключить, готовыми сниппетами (§6).
Проверки: `tests/test_preflight_dryrun.sh` (без сервера, на фикстурах в
`tests/fixtures/preflight/`).

Вопрос, на который отвечает модуль: можно ли на этом сервере включить режим с сайтом
(`lite_site`, `pro`), а если нельзя — **кто именно мешает**: процесс, PID, юнит systemd, контейнер
Docker, продукт серии — и не закрыт ли порт локальным файрволом. Ответ отдаётся и как JSON для
моста/бота/панели/MCP, и как готовый экран выбора режима с ✓/✗/⚠ для SSH-меню.

## 1. Файлы

| Файл | Что это |
|---|---|
| `lib/preflight.sh` | пять функций контракта + помощники `ga_pf_*` (источники данных, разбор `ss`/`cgroup`/`docker ps`/`ufw`/`iptables`/`nft`, таблица продуктов) |
| `lib/lang/ru.d/preflight.sh`, `lib/lang/en.d/preflight.sh` | 50 ключей `preflight_*` (паритет проверяется тестом и `check_i18n_ssh.py`) |
| `tests/test_preflight_dryrun.sh` | сквозной прогон на фикстурах: настоящие `lib/common.sh`, `lib/i18n.sh`, `lib/preflight.sh` во временном `GOAMNEZIA_DIR` |
| `tests/fixtures/preflight/` | сохранённые выводы `ss -ltnp`/`ss -lunp` (с заголовком, IPv4+IPv6, `*:443`, без `-p`), `proc/<pid>/cgroup` (v1 и v2), `docker ps --format`, `ufw status verbose`, `iptables -S INPUT`, `nft list ruleset`, каталоги `sites-enabled` nginx |
| `docs/PREFLIGHT.md` | этот документ |
| `bootstrap.sh` | три строки в `FILES` (модуль и два словарных фрагмента) — иначе `check_bootstrap_files.py` красный, а установка на сервер приезжает без модуля |

## 2. Функции

Все функции: результат в stdout только там, где сказано; всё для человека — в stderr; ни одного
`exit`; ошибка = `ga_err <код> <сообщение> [rc]` (`GA_ERR_CODE`/`GA_ERR_MSG` доходят до
`_action_run` диспетчера). Внешние команды — с `timeout` (5 с, `GOAMNEZIA_PREFLIGHT_TIMEOUT`) и
`2>/dev/null`; отсутствие любого инструмента не роняет проверку.

| Функция | Что делает | stdout | rc / коды |
|---|---|---|---|
| `preflight_port_owner <tcp\|udp> <port>` | кто держит порт и закрыт ли он локально | JSON §3.1 | 0; 2 `preflight_bad_args`; 1 `no_jq` |
| `preflight_modes` | три порта (TCP/443, TCP/80, UDP/443) одним сбором данных → доступность режимов, причины, предупреждения, подсказки | JSON §3.2 | 0; 1 `no_jq` |
| `preflight_render_modes <json> [wizard\|brief]` | экран по макету PLAN §4.3.1 в **stderr**; `brief` — без заголовка и без строки «выбрать режим — цифра» (для «Режим и сайт», где меню своё) | — | 0; 1 `preflight_bad_json` |
| `preflight_open_local <port> <proto>` | открыть порт: `ufw allow` (если ufw активен), иначе `iptables -I INPUT … -j ACCEPT` + `ip6tables` + `netfilter-persistent save`; в dry-run `DRY: …`; аудит `preflight-open`. **Сам не спрашивает** — `confirm` делает меню | — | 0; 1 `preflight_open_failed`, `preflight_no_fw_tool`; 2 `preflight_bad_args` |
| `preflight_guess_product <process> <unit> <container>` | продукт серии по NEIGHBOURS.md и goInstaller | `gotelegram\|govless\|godns\|gowarp\|gokaskad\|xui\|amnezia\|goamnezia\|unknown` | 0 |

Пакетный путь (для `status`, где портов много): `ga_pf_collect tcp udp` один раз собирает сырые
выводы в глобальные `_GA_PF_*`, затем `ga_pf_owner <proto> <port>` отдаёт тот же JSON, что
`preflight_port_owner`, но без повторных вызовов `ss`/`docker`/`ufw`. Кэша между вызовами нет
намеренно: «r — повторить проверку» обязан показывать свежие данные.

### Как определяется владелец (приёмы goInstaller)

1. **Слушатели** — `ss -H -ltnp` / `ss -H -lunp`: адрес, порт, имя процесса, PID. Разбор не привязан
   к номеру колонки (локальный адрес — поле перед peer-адресом `…:*`), поэтому читается и вывод с
   колонкой Netid (`ss -lntup`), и с заголовком, и `*:443` старых версий, и `[::]:443`, и
   `127.0.0.53%lo:53`. Любой слушатель на порту — `busy`, в том числе только IPv6 или только
   `127.0.0.1`: Linux не даст nginx привязать `0.0.0.0:443`, если кто-то держит `127.0.0.1:443`.
2. **Юнит systemd** — по `/proc/<pid>/cgroup` (`…/nginx.service`), а не по имени процесса: x-ui
   запускает `xray-linux-amd64`, telemt и dnsdist могут быть переименованы, а cgroup всегда
   указывает на юнит, который надо остановить — его и подсказываем. Берутся только `*.service`
   (scope сессии или контейнера как подсказка `systemctl disable --now …` бессмысленны). Понимает
   cgroup v1 (несколько строк) и v2 (`0::/…`).
3. **Контейнер** — `docker-proxy` сам ничего не говорит: имя берётся из
   `docker ps --format '{{.Names}} {{.Ports}}'` по опубликованному порту (`:443->443/tcp`). Строка без
   `->` — порт лишь объявлен (`EXPOSE`), хост он не занимает. Тот же список смотрится и для порта
   **без слушателя**: при `userland-proxy=false` контейнер держит порт правилом DNAT, в `ss` его нет,
   а nginx получит порт и не получит трафик — такой порт `busy` с `process: "docker"` и пометкой
   `dnat_only`.
4. **Продукт** — контейнер → юнит → процесс (§3.3). nginx общий на сервер: чей он — по
   `sites-enabled` (`goamnezia` → наш, `gotelegram` или `stream-conf.d/gotelegram-shared443.conf` →
   goTelegram, `govless` → goVLESS, иначе `unknown`).
5. **Своё** (`ours: true`) — nginx с нашим vhost, контейнеры из `config.json` (`profiles.*.container`),
   юниты `goamnezia-*`. Это переустановка/обновление, а не конфликт: режим не блокируется, в
   причины не попадает. Контейнер `amnezia-*`, которого нет в `config.json` (поставлен приложением
   Amnezia), — чужой (`product: amnezia`): его сначала берут через `import-adopt`.

### Локальный файрвол

Порядок: `ufw status verbose` — если активен, решает он (его правила лежат в iptables цепочками
`ufw-*`, которые мы не читаем). Иначе `iptables -S INPUT`, затем `nft list ruleset` для таблиц
**помимо** `ip filter`/`ip6 filter` (это отражение iptables-nft, уже разобранное; без iptables
читаются все таблицы). Инструментов нет вовсе — файрвола нет (`firewall: open`, `source: none`);
инструмент есть, но не ответил (не root, таймаут) — `unknown` и предупреждение.

Правила чтения, одинаковые для трёх источников: первое совпавшее правило решает; правила с
конкретным источником (`-s 10.0.0.0/8`, `ufw allow from 203.0.113.4`, `ip saddr …`) **не** считаются
открытием для всего интернета; правила на `lo` и только для `ESTABLISHED` пропускаются; профили
ufw `Nginx Full`/`WWW`/`Apache …` раскрываются в порты (`ufw allow 'Nginx Full'` — самый частый
способ открыть веб); списки `80,443`, диапазоны `80:443` (ufw/iptables) и `80-443`, множества
`{ 80, 443 }`, именованные `@web` и `dport vmap { … }` (nft) понимаются. Переходы в чужие цепочки
(`INPUT_ZONES` firewalld, `ufw-*` без ответа ufw) не читаем: после них общий `DROP`/`REJECT` или
политика `DROP` дают `unknown`, а не `closed`. **Асимметрия сознательная**: ложное «закрыто»
блокирует выбор режима целиком, ложное «открыто» человек заметит по `site-check` после установки.

Состояния порта: `busy` чужой важнее файрвола (`firewall: closed` остаётся полем); порт
свободный **или наш**, но закрытый локально → `closed_local` — его мы умеем открыть сами.
Внешняя достижимость (файрвол хостинга) изнутри не проверяется — это `site-check` (PLAN §4.3.1).

## 3. JSON

### 3.1 `preflight_port_owner`

```json
{"proto":"tcp","port":443,"state":"busy","addr":"0.0.0.0","pid":812,"process":"nginx",
 "unit":"nginx.service","container":"","product":"gotelegram","ours":false,
 "firewall":"open","firewall_source":"ufw","notes":[]}
```

`state`: `free` | `busy` | `closed_local` | `unknown` (нет `ss` или он не ответил — режимы при этом
доступны, с предупреждением). Поля контракта — `state, pid, process, unit, container, product, ours`;
сверх контракта — `proto`, `port`, `addr` (кому виден порт), `firewall` (`open|closed|unknown`),
`firewall_source` (`ufw|iptables|nft|iptables+nft|none`), `notes` — коды пометок: `no_ss`, `ss_failed`,
`no_pid` (ss без root — процесс не виден), `no_docker` (docker-proxy есть, `docker ps` не ответил),
`dnat_only`, `fw_unknown`, `dry_run`. `pid` — число или `null`.

### 3.2 `preflight_modes`

```json
{"lite": {"available": true},
 "lite_site": {"available": false, "reasons": [
    {"port": 443, "proto": "tcp", "state": "busy", "process": "nginx", "pid": 812, "unit": "nginx.service",
     "container": "", "product": "gotelegram", "firewall_source": "ufw",
     "message": "TCP/443 занят: nginx (PID 812, юнит nginx.service) — похоже, это goTelegram Pro"},
    {"port": 80, "proto": "tcp", "state": "busy", "process": "nginx", "pid": 812, "unit": "nginx.service",
     "container": "", "product": "gotelegram", "firewall_source": "ufw", "message": "TCP/80 занят: nginx (PID 812)"}]},
 "pro": {"available": false, "reasons": ["…те же две причины…"]},
 "warnings": [
    {"code": "udp443_busy", "port": 443, "proto": "udp", "process": "xray-linux-amd6", "pid": 1700,
     "unit": "x-ui.service", "container": "", "product": "xui",
     "message": "UDP/443 занят: xray-linux-amd6 (PID 1700, юнит x-ui.service) — похоже, это панель 3x-ui — профиль по умолчанию получит случайный порт (не мешает)"}],
 "ports": {"tcp80": {"…§3.1…"}, "tcp443": {"…"}, "udp443": {"…"}},
 "hints": {"free_cmd": "systemctl disable --now nginx.service", "open_cmd": ""},
 "dry_run": true}
```

Правила: `lite_site` и `pro` недоступны при `busy` (не `ours`) или `closed_local` на TCP/80 или
TCP/443; причины у них одни и те же (обоим нужны оба порта), порядок — 443, затем 80, как в макете;
повтор того же владельца печатается коротко (`nginx (PID 812)`). `ours` не блокирует и в причины не
попадает. `warnings[].code`: `udp443_busy` (чужой UDP/443 — профиль по умолчанию получит случайный
порт), `check_unavailable` (нет `ss`), `firewall_unknown`. `message` — уже на языке словаря, бот и
панель могут показывать его как есть. `hints.free_cmd` — первая команда освобождения
(`docker stop <контейнер>` важнее `systemctl disable --now <юнит>`: у docker-proxy юнит —
`docker.service`, и его остановка погасила бы и наши контейнеры); `hints.open_cmd` — команда
открытия закрытых портов (`ufw allow 80,443/tcp` / `iptables …` / `nft …`).

Экран (`preflight_render_modes`, 1.0.6, находка NC6-04): у режимов с сайтом справа — «порт 443/tcp — наш же, всё в
порядке» (`preflight_ports_ours_one` / `_many`), когда TCP/80 или TCP/443 держит наше же (`ours`), а не «порты
свободны»; UDP/443 у нашего контейнера — строка ℹ «UDP 443 — наша линия 2.0, всё в порядке» (профиль по имени
контейнера из `config.json`, `preflight_udp443_ours`); у Ленивого Pro хвост описания — по положению дел
(`_ga_pf_lazy_desc`): на установленном сервере — «доставит недостающее и людей не тронет»
(`preflight_lazy_installed`), UDP/443 у чужого — «UDP 443 занят — встанет Semi-Pro» (`preflight_lazy_udp443_busy`);
раньше последнее стояло всегда, и на Pro со своей линией на UDP/443 читалось как поломка.

### 3.3 Таблица продуктов (`preflight_guess_product`)

| Признак | Продукт |
|---|---|
| контейнер из `config.json`; юнит `goamnezia-*`; nginx с `sites-enabled/goamnezia` | `goamnezia` (ours) |
| контейнер `*amnezia*` не из `config.json` | `amnezia` |
| контейнер `*x-ui*`/`*3x-ui*`; юнит `x-ui.service`/процесс `x-ui`, `xray*` под ним | `xui`, а при vhost `govless` или юнитах `govless*` на диске — `govless` |
| юниты `govless*`, `cloudflared*`, `webapp-frontend*`, `tunnel-health*` | `govless` |
| `telemt.service`/процесс `telemt`; юниты `gotelegram*`; nginx с vhost `gotelegram` | `gotelegram` |
| `dnsdist.service`/`dnsdist`; `godns*` | `godns` |
| `kaskad*`, `gokaskad*`, `wg-quick@kawg*`; процессы `wg-quick`, `awg`, `amneziawg-go` вне контейнера | `gokaskad` |
| `warp-svc.service`, `warp-bot.service`, `warp-taskd*`, `gowarp*`; `warp-svc`/`warp-cli` | `gowarp` |
| прочее | `unknown` (в сообщении без «похоже, это …») |

## 4. Пример экрана (443 и 80 занят goTelegram Pro, UDP/443 — 3x-ui)

```
  Режим установки

  1) Lite          AmneziaWG на случайном порту, без домена и сайта           ✓ доступен
  2) Lite + сайт   то же + сайт-заглушка на 80/443 по IP                      ✗ недоступен
  3) Pro           домен, сайт и VPN на 443, панель и MCP на домене           ✗ недоступен

  ✗ TCP/443 занят: nginx (PID 812, юнит nginx.service) — похоже, это goTelegram Pro
  ✗ TCP/80 занят: nginx (PID 812)
  ⚠ UDP/443 занят: xray-linux-amd6 (PID 1700, юнит x-ui.service) — похоже, это панель 3x-ui — профиль по умолчанию получит случайный порт (не мешает)

  Освободите порт (например: systemctl disable --now nginx.service) или используйте чистый сервер.
  Повторить проверку — r, выбрать доступный режим — цифра, выйти — 0.
```

Тот же экран при `ufw` без правил (`brief`):

```
  1) Lite          AmneziaWG на случайном порту, без домена и сайта           ✓ доступен
  2) Lite + сайт   то же + сайт-заглушка на 80/443 по IP                      ✗ недоступен
  3) Pro           домен, сайт и VPN на 443, панель и MCP на домене           ✗ недоступен

  ✗ TCP/443 закрыт локальным файрволом (ufw активен, правила для порта нет)
  ✗ TCP/80 закрыт локальным файрволом (ufw активен, правила для порта нет)

  Порт закрыт локально: мастер предложит открыть его (ufw allow 80,443/tcp).
  Повторить проверку портов — r.
```

Выравнивание колонок — в символах (`ga_pf_pad`), а не `printf '%-14s'`: тот считает байты, и
«Lite + сайт» уезжал бы вправо. Цвета — палитра `lib/common.sh` (`GREEN ✓`, `RED ✗`, `YELLOW ⚠`,
`CYAN` номер, `DIM` подсказка).

## 5. Переменные окружения (стенды и тесты)

| Переменная | Что подменяет |
|---|---|
| `GOAMNEZIA_SS_FIXTURE_TCP`, `GOAMNEZIA_SS_FIXTURE_UDP` | файлы вместо `ss -H -ltnp` / `ss -H -lunp` (заголовок допустим) |
| `GOAMNEZIA_SS_FIXTURE` | общий файл в формате `ss -H -lntup` (первая колонка Netid, как `GOINST_SS_FIXTURE` в goInstaller); используется, когда раздельных нет |
| `GOAMNEZIA_PROC_ROOT` | вместо `/proc` — каталог с `<pid>/cgroup` |
| `GOAMNEZIA_DOCKER_PS_FIXTURE` | вместо `docker ps --format '{{.Names}} {{.Ports}}'` |
| `GOAMNEZIA_UFW_FIXTURE`, `GOAMNEZIA_IPTABLES_FIXTURE`, `GOAMNEZIA_NFT_FIXTURE` | вместо `ufw status verbose`, `iptables -S INPUT`, `nft list ruleset` |
| `GOAMNEZIA_NGINX_DIR` | вместо `/etc/nginx` (vhost'ы в `sites-enabled`, `stream-conf.d`); без неё наш vhost ищется по `NGINX_SITE_LINK` из `lib/common.sh` |
| `GOAMNEZIA_SYSTEMD_DIR` | вместо `/etc/systemd/system` (юниты `govless*.service` как признак goVLESS) |
| `GOAMNEZIA_SS_BIN`, `GOAMNEZIA_DOCKER_BIN`, `GOAMNEZIA_UFW_BIN`, `GOAMNEZIA_IPTABLES_BIN`, `GOAMNEZIA_NFT_BIN` | путь к инструменту; несуществующий путь = «инструмента нет» (ветка `unknown`) |
| `GOAMNEZIA_PREFLIGHT_TIMEOUT` | секунд на одну внешнюю команду (5) |
| `GOAMNEZIA_DRY_RUN=1` | мутации печатаются `DRY: ufw allow 443/tcp`; **чтения без фикстур считаются пустыми** (нет слушателей, нет файрвола, все режимы доступны) и печатают `DRY: ss -H -ltnp` — так сквозные прогоны `install.sh` других модулей детерминированы на любой машине. Фикстуры имеют приоритет всегда |

Фикстуры не обязаны быть парными: тест сценария «нет ss» снимает фикстуры, ставит
`GOAMNEZIA_DRY_RUN=0` и все `*_BIN` в несуществующие пути — ни одна настоящая команда не выполняется.

## 6. Что подключить в `install.sh`

> Подключено (dev, после 0.1.0 «предполётная проверка»): модуль в шапке `install.sh`; мастер — шаг
> «Режим» через `preflight_render_modes` с циклом `r` и `_wizard_offer_open_ports`; экран «Режим и
> сайт» — `brief` в шапке, `_site_mode_change` не даёт выбрать недоступный режим; диспетчер —
> `_action_require_ports` в `change-mode`, ветка `preflight`, `quality.public_ports/modes/port_warnings`,
> `status.ports` (`_ga_profile_ports_json`). Сквозные проверки — раздел 12 `tests/test_actions_dryrun.sh`.
> Имена функций меню отличаются от сниппетов ниже: `_site_mode_change`/`_site_transition` вместо
> `_site_mode_toggle`. Панель (`admin-web/server.py`) отдаёт `port_busy` и `closed_local` как 409
> (карта `BRIDGE_HTTP_STATUS`, docs/PANEL.md §5) — `message` и `hint` с командой открытия уходят
> фронтенду как есть.

### 6.1 Модуль

После `lib/website.sh` (модуль не зависит от него, но `_mode_site_screen` зовёт оба):

```bash
[ -f "$LIB_DIR/preflight.sh" ] && source "$LIB_DIR/preflight.sh"
```

Все обращения ниже обёрнуты `type preflight_modes >/dev/null 2>&1` — без модуля мастер и мост
работают как прежде (кроме `--action=preflight`, который честно отвечает `module_missing`).

### 6.2 Мастер: первый шаг «Режим» (три режима, цикл с `r`)

Заменяет блок «── 1. Режим ──» в `install_wizard` (сейчас две строки `wz_mode_lite`/`wz_mode_pro`).
Цифры совпадают с экраном `preflight_render_modes`: 1 — `lite`, 2 — `lite_site`, 3 — `pro`.

```bash
    # ── 1. Режим ─────────────────────────────────────────────────────────────
    # Предполётная проверка портов ДО показа выбора (PLAN §4.3.1): режимы с
    # сайтом недоступны, пока TCP/80 или TCP/443 заняты чужим или закрыты
    # локальным файрволом. Недоступный режим показывается, но не выбирается:
    # объяснение то же, что на экране, плюс предложение открыть порт (ufw).
    local pf="" pick
    while true; do
        pf=""
        if type preflight_modes >/dev/null 2>&1; then
            pf=$(preflight_modes 2>/dev/null) || pf=""
        fi
        if [ -n "$pf" ]; then
            preflight_render_modes "$pf"
        else
            echo -e "  ${CYAN}1${NC}) $(t wz_mode_lite)"
            echo -e "  ${CYAN}2${NC}) $(t wz_mode_lite_site)"
            echo -e "  ${CYAN}3${NC}) $(t wz_mode_pro)"
        fi
        echo -ne "  ${WHITE}$(t choose):${NC} "
        read -r ch || ch=1
        case "$ch" in
            r|R) [ -n "$pf" ] && log_info "$(t preflight_rechecking)"; continue ;;
            0)   log_info "$(t cancelled)"; return 1 ;;
            1|"") mode="lite"; break ;;
            2|3)
                pick="lite_site"; [ "$ch" = 3 ] && pick="pro"
                if [ -z "$pf" ] || [ "$(printf '%s' "$pf" | jq -r --arg m "$pick" '.[$m].available')" = true ]; then
                    mode="$pick"; break
                fi
                log_error "$(tf preflight_mode_blocked "$(t "preflight_mode_${pick}")" \
                    "$(printf '%s' "$pf" | jq -r --arg m "$pick" '.[$m].reasons[0].message')")"
                # Закрыто только локально — открыть прямо здесь и проверить снова
                _wizard_offer_open_ports "$pf" && continue
                log_info "$(t preflight_mode_blocked_hint)"
                ;;
            *)   log_error "$(t invalid_choice)" ;;
        esac
    done
```

Помощник рядом с `install_wizard` (ключ `wz_mode_lite_site` — в `lib/lang/*.d/menu.sh`, зона меню):

```bash
# Порты закрыты только локальным файрволом → «Открыть 80/443 в ufw? [y/N]»
# прямо в мастере (PLAN §4.3.1). Согласие спрашиваем здесь, модуль только делает.
_wizard_offer_open_ports() {
    local pf="$1" ports p opened=0
    ports=$(printf '%s' "$pf" | jq -r '.pro.reasons[] | select(.state == "closed_local") | .port' | sort -un | paste -sd, -)
    [ -n "$ports" ] || return 1
    confirm "$(tf preflight_ask_open "${ports}/tcp" "$(printf '%s' "$pf" | jq -r '.hints.open_cmd')")" || return 1
    for p in ${ports//,/ }; do
        preflight_open_local "$p" tcp && opened=1
    done
    [ "$opened" = 1 ]
}
```

Дальше мастер идёт как сейчас: домен спрашивается при `mode = pro`; порт `awg2` — 443 в `pro`
(занятый UDP/443 уже обрабатывает цикл `_port_free`), `lite_site` после установки профилей зовёт
`website_enable_site_selfsigned` (модуль сайта), как `pro` зовёт `website_enable_pro`.

### 6.3 Экран «Режим и сайт» (`_mode_site_screen`, `_site_mode_toggle`)

Перед отрисовкой меню — проверка и краткий экран; `r` — повтор:

```bash
_mode_site_screen() {
    local ch mode domain ppath alt pf=""
    _site_require || return 1
    while true; do
        …
        # Предполётная проверка при каждом показе экрана (PLAN §4.3.1): человек
        # видит, доступен ли переход в режим с сайтом, ещё до выбора пункта
        if type preflight_modes >/dev/null 2>&1; then
            pf=$(preflight_modes 2>/dev/null) || pf=""
            [ -n "$pf" ] && preflight_render_modes "$pf" brief
        fi
        echo -e "  ${DIM}$(_rule '─' 54)${NC}"
        … пункты 1–7, 0 …
        read -r ch || ch=0
        case "$ch" in
            1) _site_mode_toggle "$pf" ;;
            r|R) continue ;;
            …
        esac
        _pause
    done
}
```

В `_site_mode_toggle` перед `website_enable_pro`/`website_enable_site_selfsigned` — отказ с тем же
объяснением (модуль сайта портов не проверяет — это зона preflight):

```bash
_site_mode_toggle() {
    local pf="${1:-}" domain target
    if _site_is_pro; then … как сейчас …; fi
    target="pro"   # или lite_site — по выбранному пункту
    if [ -n "$pf" ] && [ "$(printf '%s' "$pf" | jq -r --arg m "$target" '.[$m].available')" != true ]; then
        log_error "$(tf preflight_mode_blocked "$(t "preflight_mode_${target}")" \
            "$(printf '%s' "$pf" | jq -r --arg m "$target" '.[$m].reasons[0].message')")"
        _wizard_offer_open_ports "$pf" || log_info "$(t preflight_mode_blocked_hint)"
        return 1
    fi
    … website_enable_pro "$domain" …
}
```

### 6.4 Ветка `change-mode`: отказ `port_busy` / `closed_local`

Помощник рядом с `_action_require_site` / `_action_require_pro`:

```bash
# Режимы с сайтом требуют свободных и открытых TCP/80 и TCP/443 (PLAN §4.3.1).
# Отказ несёт поля первой причины (port, proto, pid, process, unit, container,
# product) и все причины списком — бот показывает message и «Освободите порт
# или используйте чистый сервер», панель и MCP разбирают код.
_action_require_ports() {
    local mode="$1" js first code
    case "$mode" in pro|lite_site) ;; *) return 0 ;; esac
    type preflight_modes >/dev/null 2>&1 || return 0
    js=$(preflight_modes 2>/dev/null) || return 0
    [ "$(printf '%s' "$js" | jq -r --arg m "$mode" '.[$m].available')" = true ] && return 0
    first=$(printf '%s' "$js" | jq -c --arg m "$mode" '.[$m].reasons[0]')
    code="port_busy"
    [ "$(printf '%s' "$first" | jq -r .state)" = closed_local ] && code="closed_local"
    ga_emit_json error "$(printf '%s' "$first" | jq -r .message)" "code=${code}" "mode=${mode}" \
        "port=@json:$(printf '%s' "$first" | jq -c .port)" \
        "proto=$(printf '%s' "$first" | jq -r .proto)" \
        "pid=@json:$(printf '%s' "$first" | jq -c .pid)" \
        "process=$(printf '%s' "$first" | jq -r .process)" \
        "unit=$(printf '%s' "$first" | jq -r .unit)" \
        "container=$(printf '%s' "$first" | jq -r .container)" \
        "product=$(printf '%s' "$first" | jq -r .product)" \
        "hint=$(printf '%s' "$js" | jq -r 'if .hints.open_cmd != "" then .hints.open_cmd else .hints.free_cmd end')" \
        "reasons=@json:$(printf '%s' "$js" | jq -c --arg m "$mode" '.[$m].reasons')"
    return 1
}
```

В ветке — до включения (после `_require_fn`):

```bash
        change-mode)
            m=$(_arg mode); d=$(_arg domain); d="${d,,}"
            case "$m" in
                pro)
                    _require_fn website_enable_pro || return 1
                    _action_require_ports pro || return 1
                    …
                lite_site)
                    …
                    _action_require_ports lite_site || return 1
                    …
```

Переход **в `lite`** портов не требует. Если сервер уже в `pro`/`lite_site`, 80/443 держит наш
nginx (`ours`) — проверка проходит, повторное включение не блокируется. Коды `port_busy` и
`closed_local` уже описаны в `lib/actions.json`; панель отдаёт `port_busy` как 409
(`admin-web/server.py`), `closed_local` стоит добавить туда же.

### 6.5 Ветка `preflight` (readonly, без замка)

```bash
        preflight)
            _require_fn preflight_modes || return 1
            out=$(preflight_modes 2>/dev/null) || { _action_fail preflight_failed; return 1; }
            ga_emit_json success "$(t act_ok)" "modes=@json:${out}" \
                "lite_site_available=@json:$(printf '%s' "$out" | jq -c .lite_site.available)" \
                "pro_available=@json:$(printf '%s' "$out" | jq -c .pro.available)" \
                "hint=$(printf '%s' "$out" | jq -r 'if .hints.open_cmd != "" then .hints.open_cmd else .hints.free_cmd end')"
            ;;
```

Бот на этом рисует тот же экран из `modes.lite_site.reasons[].message` / `modes.warnings[].message`;
MCP-инструмент `preflight_modes` (уже в `actions.json`) отдаёт ответ как есть. Дополнительно
`tests/check_actions.py` перестанет краснеть по `preflight`, `tests/test_mcp.py` ждёт упоминания
`preflight_modes` в `goamnezia-mcp/AGENT_GUIDE.md`.

### 6.6 `quality`: таблица портов

В `_ga_quality_json` (там уже есть `ports` — UDP-порты профилей) добавить блок публичных портов:

```bash
    local pf='null'
    if type preflight_modes >/dev/null 2>&1; then
        pf=$(preflight_modes 2>/dev/null) || pf='null'
    fi
    jq -nc --argjson d "$docker_ok" --arg f "${fwd:-0}" --argjson n "$net_ok" \
           --argjson disk "$disk" --argjson ports "$ports" --argjson pf "$pf" \
           '{docker:$d, ip_forward:$f, network:$n, disk_free_mb:$disk, ports:$ports,
             public_ports: ($pf.ports // null),
             modes: (if $pf == null then null else {lite_site: $pf.lite_site.available, pro: $pf.pro.available} end),
             port_warnings: ($pf.warnings // [])}'
```

`_diagnose_screen` может после `jq .` вызвать `preflight_render_modes "$pf" brief` — человеку
таблица понятнее сырого JSON.

### 6.7 `status`: `ports` профилей

Пакетный путь — один сбор `ss`/`docker ps` на все профили; `listening=false` у включённого
профиля — верный признак, что контейнер не слушает свой порт:

```bash
# [{profile, port, proto, listening, ours, container, state}] — по установленным профилям.
_ga_profile_ports_json() {
    local acc="[]" p port js
    type ga_pf_collect >/dev/null 2>&1 || { printf '[]'; return 0; }
    ga_pf_collect udp
    for p in $PROFILE_IDS; do
        _profile_installed "$p" || continue
        port=$(_cfg ".profiles.$p.port"); [[ "$port" =~ ^[0-9]+$ ]] || continue
        js=$(ga_pf_owner udp "$port") || continue
        acc=$(jq -nc --argjson acc "$acc" --arg p "$p" --argjson o "$js" \
              '$acc + [{profile:$p, port:$o.port, proto:"udp", listening:($o.state == "busy"),
                        ours:$o.ours, container:$o.container, state:$o.state}]') || acc="$acc"
    done
    printf '%s' "$acc"
}
```

и в ветке `status`: `"ports=@json:$(_ga_profile_ports_json)"`. Все профили продукта — AmneziaWG,
то есть UDP. `status` дёргают бот и панель на каждом экране: в dry-run это только DRY-строки,
на сервере — один `ss` и один `docker ps` (~100 мс).

## 7. Ключи словаря (`lib/lang/{ru,en}.d/preflight.sh`)

Экран: `preflight_title`, `preflight_mode_lite`, `preflight_mode_lite_site`, `preflight_mode_pro`,
`preflight_desc_lite`, `preflight_desc_lite_site`, `preflight_desc_pro`, `preflight_available`,
`preflight_unavailable`, `preflight_prompt`, `preflight_prompt_brief`.
Для меню и мастера (сниппеты §6): `preflight_rechecking`, `preflight_mode_blocked` (%s режим, %s
причина), `preflight_mode_blocked_hint`, `preflight_ask_open` (%s порты, %s команда).
Причины и предупреждения: `preflight_reason_busy` (%s метка порта, %s владелец),
`preflight_reason_closed` (%s метка, %s источник), `preflight_warn_udp443` (%s владелец),
`preflight_warn_unknown` (%s метка, %s причина), `preflight_warn_fw_unknown` (%s метка, %s источник),
`preflight_note_no_ss`, `preflight_who_pid`, `preflight_who_unit`, `preflight_who_container`,
`preflight_who_unknown`, `preflight_looks_like` (%s имя продукта), `preflight_hint_free` (%s команда),
`preflight_hint_free_generic`, `preflight_hint_closed` (%s команда).
Источники файрвола: `preflight_fw_src_ufw`, `preflight_fw_src_iptables`, `preflight_fw_src_nft`,
`preflight_fw_src_none` (ключ строится динамически: `t "preflight_fw_src_${src}"`).
Продукты: `preflight_product_{gotelegram,govless,godns,gowarp,gokaskad,xui,amnezia,goamnezia}`
(динамически: `t "preflight_product_$id"`).
Открытие порта: `preflight_open_ufw`, `preflight_open_iptables` (%s порт, %s протокол),
`preflight_open_done` (%s, %s, %s способ), `preflight_open_failed` (%s, %s, %s команда),
`preflight_open_no_tool` (%s, %s), `preflight_open_persist_warn`.
Ошибки: `preflight_err_args` (%s, %s), `preflight_err_no_jq`, `preflight_err_json`.

Метки портов («TCP/443») и команды-подсказки одинаковы в обоих языках и собираются кодом.

## 8. Тест и что проверять на стенде

`/opt/homebrew/bin/bash tests/test_preflight_dryrun.sh` — итоговое число проверок печатает сам прогон: всё свободно; 80/443 у чужого
nginx с юнитом и vhost gotelegram (продукт, PID 812, юнит, экран, короткая вторая строка, brief);
nginx без vhost → `unknown`; наш nginx → `ours`; ss без root → `no_pid`; docker-proxy своего
контейнера (не блокирует, UDP без предупреждения) и чужого `amnezia-xray` (блокирует, подсказка
`docker stop`); DNAT без слушателя; telemt → goTelegram; UDP/443 у чужого `xray-linux-amd6` (3x-ui) → только предупреждение,
с vhost govless → goVLESS; ufw без правил → `closed_local`, `preflight_open_local` (DRY ufw /
iptables+ip6tables+netfilter-persistent, аудит, неверные аргументы), DENY раньше ALLOW, списки,
умолчание deny; наш nginx за закрытым портом → `closed_local`+`ours`, чужой → `busy`; iptables
(явный DROP, политика DROP + multiport, источник `-s`, firewalld-подобные переходы → `unknown`);
nft (множества, диапазоны, vmap, `@set`, `ip saddr`, пропуск отражения iptables-nft); все
инструменты отсутствуют → `unknown`, режимы доступны, `preflight_no_fw_tool`; dry-run без фикстур;
общая фикстура Netid; таблица продуктов; ошибки вызова; паритет словарей и ключи t/tf; EN.
JSON проверяется на каждом шаге, stdout `preflight_render_modes` — пуст.

На стенде (root, Ubuntu/Debian):

1. `ss -H -ltnp` и `ss -H -lunp` — сохранить как новые фикстуры, если формат отличается
   (разбор проверен на iproute2 5.x/6.x: `users:(("nginx",pid=812,fd=6),…)`).
2. `cat /proc/$(pidof -s nginx)/cgroup` — строка `…/nginx.service`; для docker-proxy —
   `…/docker.service`; внутри `preflight_port_owner tcp 443` поле `unit` совпадает.
3. Поставленный `awg2` на UDP/443: `docker ps --format '{{.Names}} {{.Ports}}'` показывает
   `amnezia-awg2 0.0.0.0:443->443/udp, :::443->443/udp`; `preflight_port_owner udp 443` →
   `busy`, `container: amnezia-awg2`, `ours: true`, без предупреждения в `preflight_modes`.
   Повторить с `"userland-proxy": false` в `/etc/docker/daemon.json` — должен сработать путь
   `dnat_only`.
4. `ufw enable` без правил на 80/443 → `closed_local`; в мастере `y` на «Открыть 80,443/tcp…» →
   `ufw status` показывает правила, повтор `r` — режимы доступны. Без ufw:
   `iptables -I INPUT -p tcp --dport 443 -j DROP` → `closed_local` по iptables, `-D` → free.
5. Соседи: сервер с goTelegram Pro (telemt на 443) — продукт `gotelegram`, подсказка
   `systemctl disable --now telemt.service`; с goVLESS (x-ui) — `govless`.
6. Таймауты: `systemctl stop docker` → `preflight_modes` отвечает не дольше ~5 с (`docker ps`
   упирается в `timeout`), `notes` содержит `no_docker` только там, где есть docker-proxy.
7. Мост: `goamnezia --action=preflight --json` — ровно одна строка JSON; `change-mode --mode=pro`
   при занятом 443 — `code: port_busy` с полями; при закрытом ufw — `closed_local`.
8. Не root: `preflight_port_owner tcp 443` → `busy` без PID и пометка `no_pid`, файрвол `unknown`.

Известные границы: firewalld и произвольные цепочки дают `unknown` (не блок); правила ufw/iptables,
ограниченные интерфейсом (`-i eth0`), считаются решающими; профили ufw раскрываются только для
Nginx/WWW/Apache; внешний файрвол хостинга не проверяется (это `site-check`).

## 9. Решения, принятые самостоятельно

- **Состояние `unknown`** добавлено к трём из контракта: без `ss` честнее сказать «не проверено»,
  чем «свободно»; режимы при этом доступны, но с ⚠. Поля сверх контракта (`proto`, `port`, `addr`,
  `firewall`, `firewall_source`, `notes`, `message`, `hints`, `dry_run`) — добавки, контрактные поля
  на месте и с теми же именами.
- **Причины несут готовое `message`** на языке словаря — бот и панель показывают его как есть, а
  экран `preflight_render_modes` только раскрашивает; так текст один и тот же во всех интерфейсах.
- **Порядок причин 443 → 80** и короткая строка для повтора владельца — как в макете владельца.
- **`ours` + закрытый файрвол = `closed_local`**, чужой `busy` важнее файрвола; UDP/443 закрытый
  локально — не причина (UDP не блокирует режимы).
- **`docker stop <контейнер>` важнее `systemctl disable --now <юнит>`** в подсказке: у docker-proxy
  юнит — `docker.service`, и его остановка убила бы и наши контейнеры.
- **Dry-run без фикстур = «пусто»**, а не «настоящие команды»: иначе сквозные прогоны `install.sh`
  на машине разработчика зависели бы от её nginx и ufw. Чтения на живом сервере не требуют dry-run
  и выполняются всегда, мутации в dry-run — `DRY:`.
- **Асимметрия ошибок файрвола** (сомнение → `unknown`, а не `closed`) — описана в §2.
- **`preflight_render_modes` получил второй аргумент `brief`** для экрана «Режим и сайт», где
  нумерованное меню своё; по умолчанию — полный экран мастера.
- **Чистый nftables без iptables `preflight_open_local` не трогает**: имена таблиц/цепочек
  произвольны, промахнуться проще, чем помочь — печатается подсказка, возвращается
  `preflight_no_fw_tool`.
- **`bootstrap.sh` дополнен тремя строками** (модуль и словари) — без этого `check_bootstrap_files.py`
  красный, а сервер получил бы `install.sh` без модуля. Других чужих файлов не трогал.
