# Kaskad 3 — MCP-сервер для ИИ-агентов (`kaskad-mcp`)

Дата: 2026-10-09. Основа — готовый `goamnezia-mcp` (goAmnezia Pro, `goamnezia-mcp/server.py`, `lib/actions.json`,
`AGENT_GUIDE.md`, `tests/test_mcp*.py`), переложенный на Kaskad. Ветка `k3-mcp` от `k3` (3.2.22). Живой проверки в
этой задаче нет — только офлайн-тесты (`tools/k3_test.sh`).

## 0. Итог в пяти строках

1. `kaskad-mcp` — четвёртый интерфейс узла (после SSH-меню, бота и панели), для ИИ-агента: Claude Code, Claude
   Desktop, Cursor видят **104 типизированных инструмента** (чтение 40, изменение 41, разрушительных 23), 6 ресурсов и
   3 подсказки-сценария.
2. Мост — **`kaskadctl … --json` отдельным процессом**, тот же путь, что у меню, бота и панели; вызов Python-функций
   в процессе MCP отвергнут (§3).
3. Транспорт — **stdio через SSH** (`ssh root@HOST kaskad-mcp`); портов нет. HTTP — на потом (§6).
4. Безопасность как в goAmnezia: `confirm` у разрушительных, аудит JSON-строками в `/var/log/kaskad-mcp.log` (0600),
   ключ выхода — через stdin, QR — картинками, доступ в панель и секреты узла агенту не публикуются (§5).
5. Каталог — один файл `lib/kaskad/mcp/actions.json`; тест сверяет его с разборщиком `kaskadctl`: каждая команда
   kaskadctl либо инструмент, либо в `hidden` с причиной.

## 1. Как устроен goamnezia-mcp и что перенесено как есть

goamnezia-mcp — один файл на stdlib Python (4101 строка: ~1800 — протокол и мост, ~2300 — HTTP-транспорт с
одноразовыми ключами). Переходник ничего не делает сам: инструмент → `install.sh --action=<имя> --json --k=v`,
последняя JSON-строка stdout — результат. Всё продуктовое — в `lib/actions.json` (`param_types`, `actions` с флагами
`readonly` / `destructive` / `secret`, `resources`, `prompts`).

Перенесено почти без изменений (lib/kaskad/mcp/server.py, ~1300 строк):

- JSON-RPC 2.0 и MCP 2025-06-18 (совместимость с 2025-03-26 и 2024-11-05), `initialize.instructions` из каталога,
  `tools/list` с аннотациями `readOnlyHint` / `destructiveHint`, `resources/*`, `prompts/*`, пакеты, коды ошибок;
- «stdout священен»: протокол — только stdout, логи — stderr, `sys.stdout` после старта направлен в stderr;
- валидация до запуска: типы, `pattern`, `enum`, границы, длины, управляющие символы, неизвестные параметры;
- `confirm` у разрушительных; аудит JSON-строкой (отклонённые — только имена и размеры), потолок строки 4 КБ;
- `stdin: true` у параметра — значение каналом, в аудит — длина; журнал debug без значений (находка TK-6 goAmnezia);
- поля `*_png_b64` → `image`-блоки, в текстовой копии — заглушка; полный ответ — в `structuredContent`;
- таймаут: SIGTERM группе процессов, через 3 с SIGKILL; сессия не рвётся;
- `--check`, `--tools-md`, `--version`; гайд для агента как ресурс-файл, читается без кэша.

Не перенесено: HTTP-транспорт, уровни доступа `read/write/admin`, доступы агентов (`agents.json`), одноразовые
ключи, уведомления в бот — всё это нужно только для HTTP (§6).

## 2. Чем контракт Kaskad отличается

| | goAmnezia | Kaskad |
|---|---|---|
| Мост | `install.sh --action=<имя> --json --k=v` | `kaskadctl <группа> <команда> [позиционные] [--ключи] --json` |
| Параметры | все `--k=v` | позиционные (`gw user add <имя>`), ключи-значения, флаги без значения, списки через запятую |
| Ответ | последняя строка stdout, `{"status": "success"\|"error"}` | ровно один документ (`cli.emit`), успех — `ok: true` или данные; ошибка — `KaskadError`: `{"ok": false, "error", "code": 1–6, "data"}` и тот же код выхода |
| Замок | `config.lock` берёт мост | `state.lock` берёт каждая команда сама (`state.locked`, 15 с, иначе код 6) |
| Секреты в ответе | `user-key` | `gw issue` (единственная команда с ключами), `page on/url/rotate`, `page sub on/url/rotate` (ссылки-секреты) |
| Ключ в stdin | `user-attach-key` | `gw exit-add`, `gw exit-preview` (как `gokaskad --egress-add` в 3.2.21) |
| Сессии/ссылки | у панели goAmnezia | у панели Kaskad свои сессии и одноразовые ссылки `panel link` — агенту не публикуются |

Отсюда в каталоге Kaskad у каждого инструмента `cmd` (путь команды), а у параметра — где он в командной строке:
`pos` (позиционный, по порядку), `flag` (`--k=значение`, у boolean — сам ключ при `true`), `stdin`; `args` —
постоянные ключи (`--yes`, `--png`, `--new-only`). Одна команда kaskadctl может дать несколько инструментов
(`gw exit` → `exit_enable` / `exit_disable` / `exit_rename`; `gw inbound install` → `inbound_install` /
`inbound_reinstall`). Ответ нормализуется: код выхода ≠ 0 без `ok` → `ok: false`; добавляются `exit_code` и
`code_name` (`error`, `usage`, `conflict`, `rolled_back`, `unsupported`, `locked`; свои — `timeout`, `transport`).

## 3. Решение по мосту: `kaskadctl` подпроцессом

Рассматривались два пути: вызывать функции `kaskad.gw.ops` прямо в процессе MCP или запускать `kaskadctl`.
Выбран подпроцесс:

1. **Замок и прерывание.** Каждая команда берёт `state.lock` сама и отпускает его при выходе; зависшую по таймауту
   команду можно убить вместе с её детьми (apt, certbot, сборка модуля DKMS) — замок освободит ядро ОС. Поток внутри
   MCP так не убить.
2. **Обновление.** Код живёт в `/opt/kaskad/lib/<sha12>`, `current` переключается при обновлении. Долгий процесс MCP
   держал бы в памяти старые модули и брал бы новые вперемешку — та же ловушка, из-за которой `k3_post_update`
   перезапускает панель и бота. Подпроцесс каждый раз идёт текущим кодом.
3. **Изоляция.** Команды пишут в stdout (`emit`), в stderr («Внимание: …»), держат глобальное состояние (контекст
   адресов выдачи) — в процессе MCP это ломало бы протокол stdio и смешивало вызовы.
4. **Один путь для всех.** Меню, бот и подсказки панели уже говорят «kaskadctl gw …»; MCP получает те же проверки и
   те же тексты ошибок, а тест сверяет каталог с тем же разборщиком.

Цена — ~150 мс на запуск python3 на вызов; для агента это незаметно.

Чего не хватало в kaskadctl и что добавлено (под MCP, но полезно всем):

| Команда | Зачем |
|---|---|
| `gw exit-add` (ключ из stdin) | добавить выход: то же `ops.exit_add`, что у панели (`gokaskad --egress-add`, ключ — stdin) |
| `gw exit-preview` (stdin) | что в ключе — до добавления |
| `gw exit-del <id>` | убрать ключ из пула (`ops.exit_del`) |
| `gw scheme` | схема компактно: маршрут и правила узла, протоколы, выходы, люди → устройства с действующим маршрутом и профилем |
| `gw issue --png [--where W]` | QR-коды картинками (`qrencode -t PNG`, данные через stdin) в поле `png_b64` записей `qr` и `link`; инструкция словами панели |
| `gw inbound install --new-only` | установка только нового: установленный — отказ (код 3), переустановка — отдельный разрушительный инструмент |
| `update check` / `update run` | обновление как меню 11: `gokaskad --update-check` (JSON, ничего не меняет) и `gokaskad --self-update` (меню 11 без вопросов); «[!]» скрипта — в `warnings`, `partial` |
| `gw exit-add --public-only` | ссылка подписки скачивается в ядре (`gw/subfetch.py`), а скрипту уходит уже текст ключей: `urllib.parse`, без `user:pass@`, только http(s), все адреса хоста — внешние, соединение — с проверенным адресом (имя только в Host/SNI), перенаправления вручную с той же проверкой (до 3), до 64 КБ; ссылки внутри скачанного выбрасываются |
| `gw issue --where agent` | инструкция нейтральными словами (без кнопок панели и пунктов меню) |
| `fwd add --new-only` | только новый проброс: существующий — отказ (код 3, `data.exists`), а не тихая замена |
| `mcp key add\|list\|del` | ключи SSH агентов (§5): ключ — stdin, строка с принудительной командой и запретами; агенту не публикуется |

## 4. Каталог

Источник правды — `lib/kaskad/mcp/actions.json`: `param_types` (тип, шаблон, `enum`, границы, описание RU/EN),
`actions` (инструменты), `hidden` (команды kaskadctl, которые агенту не публикуются, — с причиной RU/EN),
`resources`, `prompts` (шаги — прямо в JSON). Покрытие по задаче:

- **чтение**: статус узла и шлюза (`gateway_status`, `gateway_check`, `gateway_probe`, `net_status`, `net_check`),
  протоколы (`inbounds_list`, `inbounds_plan`), люди и устройства (`users_list`, `devices_list`, `device_offers`,
  `sessions_list`, `gw_activity`), выходы и их здоровье (`exits_list`, `exit_preview`, `exit_sub_list`), правила (`rules_status`), пробросы
  (`forwards_list`), схема и «куда пойдёт трафик» (`scheme`, `traffic_trace`), сайт и домен (`site_status`,
  `site_check`, `site_dns`, `site_templates`, `site_template_status`, `site_use_preview`), движок AWG
  (`awg_engine_status`, `awg_engine_check`, `awg_engine_preview`, `accel_status`), VLESS Reality (`vless_show`,
  `vless_masks`), MTU (`mtu_show`,
  `mtu_s4_preview`), версия и обновление (`version`, `update_check`), панель, бот, страницы (`panel_status`,
  `bot_status`, `pages_list`, `page_url`, `page_sub_list`, `page_sub_url`), что войдёт в копию настроек (`backup_summary`), здоровье сервера (`host_health`), оценка, история и расписание проверок, цена проверки (`host_score`: `action` `score`, `history`, `show`, `schedule`, `price`, `share`), тариф сервера (`host_tariff`: `action` `show`, `preview`);
- **изменения**: люди и устройства (`user_add`, `device_add`, `device_grant`, `device_issue` с QR, включить и
  выключить, маршруты), выходы (`exit_add` через stdin, `exit_enable` / `exit_disable` / `exit_rename`,
  `default_route_set`), подписки выходов (`exit_sub_add` — адрес через stdin, `exit_sub_refresh`, `exit_sub_restore`, `exit_sub_auto`),
  копия настроек на сервер (`backup_export`: файл, зашифрованный паролем человека, остаётся на сервере), правила (`rules_default`, `rules_profile`, `rules_consent`, `rules_ru_via`, `rules_block`, `rules_update`), пробросы
  (`forward_add`), протоколы (`inbound_install`, `inbound_enable`, `inbound_port`, `inbound_option`), домен и сайт
  (`site_domain_set`, `site_email_set`, `site_cert_issue`, `site_mode`, `site_template_set`), движок
  AWG (`awg_engine_set`), MTU, шлюз (`gateway_enable`, `gateway_apply`, `gateway_repair`), страницы, настройки здоровья
  сервера (`host_set`: проверка, лёгкие пробы сети, точки, пуши бота; `open_world` — пробы шлют пакеты наружу), проверка сервера
  (`host_bench`), расписание проверок (`host_bench_schedule`: `action` `add`, `on`, `off`, `rm`; только с `code`
  предпросмотра), тариф сервера (`host_tariff_set` — только с `code` из `host_tariff` `action=preview`);
- **разрушительные** (`destructive`, обязательный `confirm`): удаления (`user_delete`, `device_delete`,
  `exit_delete`, `exit_sub_delete`, `forward_delete`, `inbound_uninstall`, `site_remove`, `site_domain_clear`, `gateway_purge`),
  переустановка (`inbound_reinstall`), то, после чего старые настройки не работают (`device_revoke`,
  `device_reset`, `pki_rotate`, `vless_mask`, `vless_rekey`, `vless_sync`, `hy2_option`, `hy2_rekey`, `mtu_s4`, `page_rotate`, `page_sub_rotate`, `inbound_port`,
  `gateway_set_host`, `gateway_detect_host`, `gateway_addresses_in`,
  `site_use`), выключение шлюза (`gateway_disable`), обновление (`update_run`, решение владельца). Флаги с потерей
  данных (`user_delete.with_devices`, `gateway_purge.with_users`) — ещё и свой `confirm_<флаг>`. У сводного
  инструмента `confirm` — только у разрушительного действия (`host_bench_schedule` `action=rm`, `confirm` = `entry`).
- Фраза подтверждения (R7, `SPEC-stage1-rf-mark-builtin.md` §5.3): у параметра в `actions.json` поле `"phrase": "<ключ i18n>"` (сейчас — `confirm_not_rf` у `rules_default`, `rules_profile`, `rules_consent`, ключ `rules.rf.consent`). Значение — строка, дословно текст ключа на русском или английском (после NFC и обрезки пробелов по краям); иначе `-32602` до запуска, попытка — в аудите. В `kaskadctl` уходит только флаг `--confirm-not-rf` (и `--via=mcp` из `args`), сама фраза — нет. Отказ на неверную фразу — с текстом блока ⚠РФ в сообщении и в `data` (`block`, `orgs`, `consent`); в описании параметра фразы нет (круг 2 R6+R7). `orgs` — коды организаций показанного блока (`--orgs=meta,fbk`): у `--via=mcp` ядро без них отказывает с блоком, согласие — только на показанные. Поле инструмента `"ask_human": true` (эти три) — `destructiveHint: true` без `confirm`: клиент спрашивает человека перед вызовом.

Скрыто намеренно (`hidden`, 72 команды): ключи агентов (`mcp key …`), пароль, ссылки входа и открытый доступ панели, секретный путь панели на
домене (агент не выдаёт доступ себе), экспорт/импорт ключей в файл, загрузка копии настроек и её откат (`backup import`,
`backup undo` — заменяют людей, протоколы и ключи узла), переезд движка пробросов (`migrate`), служебные
команды служб и таймеров (`gw collect`, `gw hook`, `net restore`, `site tick`…), `fwd flush`, `bot send-test`.

Ветка обновлений (3.2.23): `update_check` отвечает полями `branch` и `auto`. `auto: true` — в `/etc/kaskad/config` нет
`KASKAD_BRANCH`, и ветка выбрана сама: `main`, на ней версия старше — `test`, и там старше — `dev` (понижения нет).
Цепочка дошла до `dev`, и там не старше — `KASKAD_BRANCH="dev"` записывается (`remembered: true`), как в меню 11.
Установка из `dev` записывает ветку сама (`KASKAD_BRANCH` из окружения или сравнение версии с `main` и `test`).
3.3.4: до 13.10.2026 сборки шли в `k3`, теперь она заморожена на 3.3.3; записанная `KASKAD_BRANCH="k3"` один раз
переключается на `dev` (отметка `KASKAD_BRANCH_K3_DEV` в настройках), `update_check` делает это молча — строку о
переходе покажет меню.

## 5. Модель безопасности

- **Доступ — ключ SSH, портов нет.** Сервер живёт, пока жива SSH-сессия, от root (как kaskadctl). Отдельный ключ
  агента выдаёт человек: меню 18 → «Ключи ИИ-агентов (MCP)» или `kaskadctl mcp key add` (ключ — stdin). Строка в
  `/root/.ssh/authorized_keys`: `restrict,command="/usr/local/bin/kaskad-mcp",no-pty,no-port-forwarding,
  no-agent-forwarding,no-X11-forwarding,no-user-rc <тип> <ключ> kaskad-mcp-agent[:подпись]` — этим ключом нельзя
  получить shell или туннель. Отказ: не один ключ, мусор, закрытый ключ, свои опции перед ключом, DSA, RSA < 2048,
  ключ, который уже даёт полный вход root (sshd берёт первую строку). Удаляются только наши строки (опции + метка).
  sshd не рвёт открытое соединение при удалении ключа, поэтому удаление (`kaskad.mcp.sessions`) завершает процессы
  `kaskad-mcp` с SSH_CONNECTION, открытые этим ключом (по строке `Accepted publickey` журнала sshd), и те, чей ключ
  журнал не назвал: тогда ответ `unsure`, панель пишет «могли отключиться и другие агенты». Сессии других ключей, shell
  владельца, sshd и `kaskad-mcp` без SSH не трогаются. Перед каждым сигналом сверяется, что pid — та же сессия
  (`kaskad-mcp`, тот же SSH_CONNECTION и время старта): pid, ушедший другому процессу, сигнала не получает. Панель и
  меню спрашивают подтверждение и заранее говорят, что агент отключится. Откат импорта настроек (`backup undo` и
  автоматический) удаляет ключи, добавленные импортом, и завершает их сессии одним проходом; что прервано — в его ответе.
- **Поверхность — только каталог.** Значения уходят в argv списком, без shell. Позиционное значение не может
  начинаться с «-» (argparse принял бы его за ключ: `--force`), ключи-значения идут одним словом `--k=v`.
- **`confirm`** = значение параметра цели (по умолчанию первого; у `forward_delete` — порт) или имя инструмента, если
  цели нет. Несовпадение — `-32602`, kaskadctl не запускается, попытка — в аудит со статусом `rejected`. `confirm` —
  защита от вызова «не того», а не согласие человека; согласие — разрешение клиента на вызов.
- **Сочетания** (`one_of`, `at_most_one` в каталоге: `site_template_set` — `id` или `random`, `site_mode` — не оба)
  проверяются сервером до запуска; схемы без `anyOf`/`oneOf` верхнего уровня — их принимают не все клиенты.
- **Таймауты.** Чтение по таймауту останавливается (группа процессов). Изменение — нет: внутри может идти apt/dpkg
  (до ~50 мин: чужая установка 30 + блокировка apt 10 + работа 20), и убить его посреди хуже; ответ `timeout` с
  `running: true`. stdout/stderr команды — во временные безымянные файлы: команда, которую перестали ждать, не
  падает от SIGPIPE. Установка и сборка — таймаут 4000 с; при `progressToken` — `notifications/progress` раз в 15 с.
- **Подписка от root.** `exit_add` идёт с `--public-only`: ссылку скачивает ядро, а не `install.sh` (там `curl -L`
  без проверок — путь человека, меню и панели), по закреплённому проверенному адресу, перенаправления — с проверкой
  каждого. Обходы стенда T-M6 (`user:pw@127.0.0.1`, редирект на 127.0.0.1 и на метаданные) закрыты тестами.
- **Нет интернета у устройств.** `gw status` (`problems`) и `gw check` без `--repair` дают `no_exits` по правилу
  красной строки меню 17 (`k3_gw_dead`): шлюз включён, маршрут по умолчанию через выходы, при отказе «блок», данные
  сборщика свежие (≤ 60 с), ни один нужный выход не работает. Сторож (`--repair`) его не видит — чинить нечего.
- **Секреты.** `gw issue` (настройки устройства), `page on/url/rotate` (ссылки на страницы людей) и `page sub on/url/rotate` (подписки для приложений) помечены `secret`;
  ресурсы на них не ссылаются. Ключ выхода — только через stdin (`/proc/<pid>/cmdline` читают все), в аудит —
  длина, в журнал Kaskad — ничего. QR приходят картинками; ответ kaskadctl в аудит не пишется никогда.
- **Аудит.** `/var/log/kaskad-mcp.log` (0600, JSON-строка на вызов и чтение ресурса: инструмент, команда,
  параметры, статус, код, мс, клиент из `initialize`, адрес SSH); ротация — `/etc/logrotate.d/kaskad`. Каждое
  изменение — ещё и строка «MCP <клиент>: <инструмент> … → готово/ошибка» в `/var/log/kaskad.log` (через
  `util.redact`). Аудит не пишется — изменения отклоняются (`-32603`), чтение работает. Журнал debug — только
  команда и число аргументов, без значений.
- **Устойчивость.** Строка, которую нельзя разобрать (глубина, `NaN`), — `-32700`; ответ, который нельзя
  сериализовать, — `-32603`; сервер не падает. `structuredContent` — с заглушками вместо PNG (base64 один раз — в
  `image`). Описания — на одном языке (`KASKAD_MCP_LANG`, по умолчанию ru): `tools/list` ~50 тыс. знаков вместо ~66.
- **Размер каталога (единое правило, 3.3.7.4).** `tools/list` на каждом языке (ru и en) — не больше 600 × N знаков
  (N — число инструментов, компактный JSON), один инструмент — не больше 2 400; проверяет `test_mcp.test_list_is_compact`.
  Частный предел линии оценки 76 200 отменён. Аннотации — только значимое и отличное от умолчаний MCP: `readOnlyHint:
  true` у чтения; у изменяющих — `destructiveHint` всегда (умолчание MCP — `true`, наше `false` — «клиент не
  спрашивает»); `openWorldHint` всегда; `idempotentHint` не отдаём. Новое — в предел, иначе ужимать описания.
- **Не публикуется**: всё из `hidden` (§4). Новая команда kaskadctl без решения «инструмент или hidden» роняет тест.

## 6. HTTP-транспорт — не в первой версии

Решение: только stdio. Причины: (1) у Kaskad нет ни доменного входа для агентов, ни выдачи доступов агентам —
пришлось бы переносить ~2300 строк goAmnezia (секретный путь + токен, одноразовые ключи с обнаружением клонов,
уровни доступа, лимиты, бан, уведомления в бот) и меню выдачи доступов; (2) SSH-ключ с принудительной командой
даёт ту же изоляцию без открытого порта; (3) все целевые клиенты (Claude Code, Desktop, Cursor) умеют stdio через
`ssh`. На потом — «агент без SSH» (claude.ai, мобильные клиенты): HTTP за nginx на домене узла по образцу
goAmnezia, с уровнями доступа и выдачей из SSH-меню (выбор цифрами).

## 7. Установка и удаление

- Код едет в `install.sh` (K3_PAYLOAD; `build_payload.py` теперь берёт и `lib/kaskad/mcp/*.md` — гайд).
- `k3_ensure_lib` пишет обёртку `/opt/kaskad/bin/kaskad-mcp` (тем же способом, что kaskadctl: `python3 -I`, путь —
  `lib/current`) и ссылку `/usr/local/bin/kaskad-mcp`; чужой файл с этим именем не трогается. Не записалась —
  узел работает, строка в журнале.
- Полное удаление (меню 15) снимает ссылку, аудит и его ротированные копии, строки ключей агентов из
  `/root/.ssh/authorized_keys` (только свои: опции kaskad-mcp и метка; без ядра, `k3_mcp_keys_remove`) и говорит об
  этом; `/opt/kaskad` уходит целиком. Иначе после новой установки старый ключ агента снова работал бы (T-M6, F2).
- Меню 18 (Веб-панель) → «Ключи ИИ-агентов (MCP)»: добавить, список, удалить, команды подключения; выбор цифрами.
- Сервер kaskadctl ищет в `/opt/kaskad/bin/kaskadctl`; нет обёртки — запускает свой пакет тем же способом
  (`KASKAD_MCP_CTL` — подмена для тестов).

## 8. Тесты

- `tests/test_mcp.py` — сервер подпроцессом с заглушкой kaskadctl: initialize, tools/list против каталога, argv
  каждого вида параметров, отказы без запуска (схема, «-», управляющие символы, confirm), коды ошибок и
  `code_name`, таймаут с убийством команды, PNG → картинки, аудит (0600, без ответа), ключ только в stdin (не в
  argv, аудите, журналах, debug), ресурсы и гайд, подсказки, `--check` / `--tools-md`; сверка каталога с
  настоящим разборщиком kaskadctl (`parser_problems`, плюс проверка, что сверка ловит ошибки); правила
  «разрушительное = необратимое», «секрет только у выдачи», полнота гайда, SPEC и README; сессия с настоящим
  kaskadctl (люди, удаление с confirm, отказы ядра).
- `tests/test_cli_mcp_cmds.py` — новые команды kaskadctl; ключи агентов (формат, отпечаток как у ssh-keygen, отказ на
  мусор, закрытый ключ, свои опции, DSA, короткий RSA, ключ полного входа; удаление только своих строк).
- После критики: флаги с потерей данных, сочетания, `usage` при отказе argparse, аудит недоступен — изменения
  запрещены, `notifications/progress`, изменение не убивается по таймауту, плохой ввод не роняет сервер, ошибка
  ресурса — ошибка JSON-RPC, один язык и размер `tools/list`, `fwd add --new-only`, `--public-only`.
- `tools/k3_install_test.sh` — обёртка и ссылка `kaskad-mcp`, `kaskad-mcp --check` из встроенного кода, вызов
  через обёртку, удаление; `--update-check`; меню ключей агентов с настоящим kaskadctl.

## 9. На потом

1. HTTP-транспорт (§6) и выдача доступов агентам из SSH-меню.
2. Таймаут `sysrun` (общий для ядра) останавливает только сам процесс, без его детей; для `update` сделано своё
   (группа процессов), остальное — отдельной задачей.
3. Журналы для агента (`kaskad.log`, аудит MCP) — команда kaskadctl `log tail` с сужением.
4. Живая проверка на стенде: Claude Code → `ssh root@стенд kaskad-mcp`, сценарии трёх подсказок.
5. Пункт SSH-меню «сменить ветку обновлений» (с `dev` на `main`/`test` и обратно); пока — строка в `/etc/kaskad/config`.

## Журнал изменений контракта

- **Этап 1, T15 Q7 (`SPEC-stage1-exit-groups.md` §8.7–8.8, §9; 3.3.7.11).** Новый инструмент `rules_ru_via`
  (`rules ru-via --yes`, **разрушительное**, `confirm` = `id`): «Российские сайты» пользователя или устройства — `inherit`,
  `direct` или российская группа `g-…` (тип параметра `ru_via`); ответ — `ru_via`, `changed`, `warning` (российский
  трафик пойдёт через роутер группы). `exit_add` с `group` российской группы — ключ только в `upstream-ru.txt`
  (описание параметра). `traffic_trace` отдаёт поле `ru`: `{group, name, step, exit, tier, since, offline, chain}` —
  для российского адреса у человека с действующей «Россией напрямую», иначе `null`. EN: new destructive tool
  `rules_ru_via` (`confirm` = `id`); `exit_add` `group` may be a Russian group; `traffic_trace` adds `ru`.
- **Размер каталога и аннотации (3.3.7.4, шаг 2а).** Единое правило для всех линий: `tools/list` ≤ 600 × N знаков на
  каждом языке и ≤ 2 400 на инструмент (§5); предел 76 200 линии оценки отменён. В `annotations` нет `idempotentHint`
  (как wt-vless Ш2-б), `readOnlyHint` — только `true` у чтения, `destructiveHint` — только у изменяющих (по MCP значим
  лишь при `readOnlyHint: false`); смысл для клиента прежний — это умолчания MCP. Ужаты описания `host_tariff`,
  `host_set`, `host_bench_schedule`, `host_score`, `host_bench` и общих параметров (тексты wt-vless Ш2-б); условия «по
  просьбе человека», цены, пределы и имена параметров — те же. EN: one rule — ≤ 600 × N chars per language, ≤ 2 400 per
  tool; annotations carry only non-default hints (no `idempotentHint`, no `readOnlyHint: false`, no `destructiveHint`
  on reads).
- **Сводные инструменты оценки (B14 круг 2, REV-ns-b14 З-1…З-3).** Действие — параметр `action` (`by_action` в
  actions.json: у действия своя команда kaskadctl, свои параметры из общих, свои обязательные, `args`, `readonly`,
  `destructive`; описание и общие параметры — один раз). 13 инструментов оценки, новых после 3.3.7, сведены в 5:
  `host_score` (чтение; `action` `score` по умолчанию, `history` — до 50 строк, `show`, `schedule`, `price` — бывшие
  `host_score`, `host_bench_history`, `host_bench_show`, `host_bench_schedule`, `host_bench_price`), `host_bench`
  (без изменений), `host_bench_schedule` (изменение, «спросить человека»; `action` `add`, `on`, `off`, `rm` — бывшие
  `host_bench_schedule_add/_on/_off/_rm`; `rm` — разрушительное, `confirm` = `entry`), `host_tariff` (чтение;
  `action` `show` по умолчанию, `preview` — бывшие `host_tariff`, `host_tariff_preview`) и `host_tariff_set` (изменение,
  как прежде: в одном инструменте с чтением тариф терял пометку «только чтение» — REV-ns-b14-2 П-3). Расписание:
  согласие на «много трафика» — только когда трафик в месяц растёт (`consent` в предпросмотре): выключить, удалить,
  облегчить — без отказа `heavy` (С-1). `host_set`: `bench_retry` и `dev_*` — только с `code` предпросмотра, его
  сверяет сервер (`--by-mcp`; первый вызов ничего не пишет, З-2); остальные ключи — сразу, как в 3.3.7; пометка
  `ask_human` снята (её не было в 3.3.7). Предел `tools/list` — весь список на каждом языке ≤ 76 200 знаков (отменён в 3.3.7.4: 600 × N, §5; было 600 на
  инструмент только для ru): сейчас ru ≈ 74 400, en ≈ 74 600 (119 инструментов, с `host_tariff_set` — П-3). EN: 13 score tools (new since 3.3.7) became 5: with
  an `action` parameter `host_score`, `host_bench_schedule`, `host_tariff` (read: show, preview), plus `host_bench` and
  `host_tariff_set`; `host_set` saves
  `bench_retry`/`dev_*` only with the `code` of its preview, checked by the server; the list limit is the whole list in
  each language.
- **Расписание и история проверок (B14, `k3/handoff/SPEC-node-score-schedule-share.md` §6.3).** Новые инструменты
  `host_bench_schedule` (чтение: `host bench schedule list --json` — тот же ответ, что `GET /api/host/bench/schedule`),
  `host_bench_schedule_add`, `_on`, `_off`, `_rm` (изменение, `--by-mcp`: без `code` — предпросмотр с `code`, ничего не
  сохраняет; с `code` — сохранить; сервер сверяет код — версия расписания и список после изменения, иначе
  `code_mismatch`, код 3; «много трафика» `heavy` — отказ, код 2: такое сохраняет только человек; `--yes` MCP не
  передаёт), `host_bench_history`, `host_bench_show` (чтение). Ошибки и цена — те же функции, что у API панели
  (`host_sched.py`). Подтверждение — ключом с сервера (как `host_tariff_set`), а не `confirm: "@tool"` §6.3 ТЗ.
  `host_set`: `bench_retry`, `dev_threshold`, `dev_spread` (SS §2.8 п. 5, NS-D25) и `ask_human`. В `hidden`:
  `host bench schedule` (без подкоманды), `schedule cost`, `schedule apply`, `compare`, `export`. EN: new tools
  `host_bench_schedule` (read), `host_bench_schedule_add/_on/_off/_rm` (write: a preview without `code`, the save with
  the `code` the server checks; heavy is refused), `host_bench_history` and `host_bench_show` (read); `host_set` gets
  `bench_retry`, `dev_threshold`, `dev_spread`.
- **Тариф сервера (C1, `k3/handoff/SPEC-node-score.md` §6.3).** Новые инструменты `host_tariff` (чтение: `kaskadctl host
  tariff show --json` — тот же ответ, что `GET /api/host/tariff`: каждое значение с источником, у пустого поля —
  подсказка сервера), `host_tariff_preview` (чтение: `host tariff set --dry` — строки изменения и `code`) и
  `host_tariff_set` (изменение: `host tariff set --by-mcp --code=…`; подтверждение на сервере — код того же изменения
  над тем же сохранённым тарифом, иначе `code_mismatch`, как `pin_tofu` у T10). `host tariff` без подкоманды — в
  `hidden`. EN: new tools `host_tariff` (read), `host_tariff_preview` (read, the change and its code) and
  `host_tariff_set` (write, only with the code of the preview — checked by the server).
- **Оценка сервера (B7, `k3/handoff/SPEC-node-score.md` §6.2 B7).** Новые инструменты `host_score` (чтение: `kaskadctl host
  score --json` — тот же ответ, что `GET /api/host/score`), `host_bench_price` (чтение: `host bench --dry`, цена и
  пакеты; у каждого вида — ключ `confirm`) и `host_bench` (изменение, «спросить человека» и обязательный `confirm` —
  ключ из `host_bench_price` того же вида: сервер пересчитывает цену и пакеты, другой ключ — отказ, код 2, ничего не
  запущено и не поставлено; `--yes` MCP не передаёт — SS §6.3; `open_world`: `host bench --confirm KEY`, ответ сразу,
  итог — `host_score`). EN: new tools `host_score` (read), `host_bench_price` (read; each kind has its `confirm` key)
  and `host_bench` (write, the client asks the human and `confirm` — the key of the price — is required: the server
  counts the price again and refuses another key; open world: the check uses traffic).
- **Этап 1, T10 (`SPEC-stage1-exits/10-ui-screens-cli-mcp.md` §7.7–7.8, D-050, D-078).** Группы выходов и политика: чтение
  `exit_groups_list` (`gw groups`), `exit_policy_get` (`gw policy`), `exit_transitions` (`gw exit-history`), `tz_get`;
  кнопки над выходом `exit_standby`, `exit_restore`, `exit_maintenance`, `exit_accept_country`, `exit_quota_set`,
  `exit_quota_clear`, `exit_move` (та же роль), `group_flap_standby_set`, `exit_speedtest`, `tz_set`; разрушительные
  `exit_policy_reset` (`confirm` = `group`), `exit_move_role` (смена роли, `confirm` = `id`), `group_delete`
  (`confirm` = `group`). У всех записей политики — `dry` («что изменится», `moves`) и `rev` (сверка версии, иначе
  `conflict`); у `exit_enable`, `exit_disable`, `exit_delete` — тоже. `via` маршрутов принимает `group:<id группы>`
  (только зарубежная; российская и несуществующая — отказ ядра), `exit_add` — параметр `group`. `exits_list` отдаёт
  поля этапа 1 (`health`, `reasons`, `group`, `tier`, `devices`, `rtt_ms`, `recovering`, `unstable`, `manual_why` …) и
  `default_route` с `group`, `group_name`, `group_fail`, `fail_eff`. Задать политику целиком (`exit_policy_set`),
  создать и переименовать группу, уровни и условия — нельзя: только панель. EN: the tools of the groups and the exit
  policy of stage 1; `dry` and `rev` on every write of the policy; `via` takes `group:<group id>`; no raw policy.
- **Этап 1, T10, круг 2.** `exit_speedtest` без `force`: отказ юнита (занято, лимит, выход выключен или снят по квоте) —
  сразу ошибкой с `data.why` и `data.running=false`; сверх суточного лимита — отдельный `exit_speedtest_force`
  (разрушительное, `confirm` = `id`: клиент спросит человека). TOFU ключа без своей проверки сертификата:
  `exit_tofu_check` (`gw exit-preview --tofu`) и `exit_add_tofu` (`gw exit-add --pin-tofu`, разрушительное, `confirm` =
  `pin_tofu`). `exit_enable`/`exit_disable` — всегда тот же путь, что кнопка панели (`rev`, `moves` в ответе).
  `gateway_status` отдаёт ещё `default_route` (с `fail_eff`, `group_fail`) и `groups`. EN: `exit_speedtest` refuses at
  once with `data.why`; `exit_speedtest_force`, `exit_tofu_check`, `exit_add_tofu` are new; `gateway_status` adds
  `default_route` and `groups`.

- **Здоровье сервера (A8, `k3/handoff/SPEC-node-score.md` §3.8).** Новые инструменты `host_health` (чтение: `kaskadctl
  host health --json` — тот же ответ, что `GET /api/host/health`, и настройки `hostmon`) и `host_set` (изменение,
  `open_world`: включение проверки, лёгкие пробы сети, точки, уровень уведомлений бота). `host probes-hint` — в `hidden`
  (строка установщика). EN: new tools `host_health` (read) and `host_set` (write, open world: the probes send packets out).
- **Этап 1 Kaskad 3 (T1, `SPEC-stage1-exits/10-ui-screens-cli-mcp.md` §7.8, `SPEC-stage1-exits/12-pushes-new-keys.md` §9.3).** `kind` выхода: `awg` | `xray`; протокол выхода через
  xray — в новом поле `proto` (`vless`, `hysteria2`, `shadowsocks`, `trojan`, `vmess`); в 3.3.5 и раньше такой выход
  был `kind: "vless"` без `proto`. Новое поле `role`: `abroad` (строки `upstream.txt`) | `ru` (`upstream-ru.txt`,
  этап 1, T15). Касается `kaskadctl gw exits --json`, MCP `exits_list`, `exit_preview`, API панели (`/api/gw/exits`,
  схема) и мока. Вид **входа** `vless` (`inbounds_*`, `access` устройств) не меняется. EN: the exit `kind` is
  `awg` | `xray`, the protocol is in `proto`; it was `vless` in 3.3.5 and before; new field `role` (`abroad` | `ru`).
  Ресурс схемы (`kaskad://scheme`, `kaskadctl gw scheme --json`) отдаёт у выхода тоже `proto` и `role`. Балансировщик
  пула xray-выходов в ответах «куда идёт трафик» — `"xray"` вместо `"vless"`: поле `balancer` в `traffic_trace`
  (`highlight.balancer`), в `now` схемы панели и в `exits.now_of`; читатели принимают оба значения. EN: the scheme
  resource gives `proto` and `role` of an exit too; the balancer of the xray exits is `"xray"` (was `"vless"`) in
  `traffic_trace` `highlight.balancer` and the scheme's `now`.

## Приложение. Инструменты и ресурсы (`kaskad-mcp --tools-md`)

| Инструмент | Тип | Параметры | Команда kaskadctl | Описание |
|---|---|---|---|---|
| `version` | чтение | — | `version` | Версия Kaskad на сервере |
| `gateway_status` | чтение | — | `gw status` | Сводка узла: шлюз вкл/выкл, сеть, адрес для клиентов, число людей и устройств, протоколы (работают ли, порты, сессии), выходы и их здоровье, DNS, xray, правила, движок AmneziaWG, MTU, проблемы. С него начинают |
| `gateway_probe` | чтение | — | `gw probe` | Можно ли включить шлюз на этом сервере (система, nftables); код 5 — нельзя, в ответе причина |
| `gateway_check` | чтение | — | `gw check` | Проверка шлюза без исправлений: список проблем (пустой — порядок). isError при найденных проблемах |
| `net_status` | чтение | — | `net status` | Движок пробросов: nft или legacy (iptables), таблица, службы |
| `net_check` | чтение | — | `net check` | Проверка движка пробросов без исправлений (сторож); isError при проблемах |
| `inbounds_list` | чтение | — | `gw inbound list` | Протоколы (входы): установлен ли, включён, работает, порты, устройства, сессии, предупреждения |
| `inbounds_plan` | чтение | `kinds` | `gw inbound plan` | Какие порты займёт установка протоколов и кто их сейчас держит (ничего не меняет) |
| `users_list` | чтение | — | `gw user list` | Люди: id, имя, свой маршрут, включён ли, число устройств |
| `devices_list` | чтение | `user` | `gw device list` | Устройства (всех или одного человека): id, имя, логин, тип, платформа, протоколы, адреса, действующий маршрут. Без ключей |
| `device_offers` | чтение | `platform`* | `gw offers` | Что выдавать устройству этой платформы первым: протоколы по порядку, установлен ли, заметки |
| `device_issue` | чтение, секрет | `device`*, `inbound`, `os` | `gw issue --png --where=agent` | Настройки устройства для подключения: инструкция (howto) и записи (.conf, ссылка vpn://, QR-коды — приходят картинками). ВНИМАНИЕ: в ответе ПРИВАТНЫЕ КЛЮЧИ и пароли устройства — отдавай только тому, кто попросил, не сохраняй в заметки. Ничего не меняет на сервере. Пишет строку о выдаче в журнал Kaskad (без ключей) |
| `sessions_list` | чтение | — | `gw sessions` | Кто сейчас подключён: протокол, адрес, устройство, с какого времени |
| `gw_activity` | чтение | `who`*, `range` | `gw activity` | Активность одного человека или устройства (SPEC-activity §7): на связи, «трафик шёл», объём ↓ ↑ за сутки (по часам), 7 или 30 дней (по дням), по устройствам. Только объём и время, без адресов и сайтов; по всем людям — нет. История выключена — `activity: false` |
| `exits_list` | чтение | — | `gw exits` | Выходы (пул «Прокси для API Telegram»): id, имя, тип, где, включён ли для шлюза, usable — работает ли, рукопожатие, text |
| `exit_preview` | чтение | `key`* | `gw exit-preview` | Что в ключе выхода — до добавления: тип (VLESS, AmneziaWG 1.5/2.0/3.1, WireGuard), где, есть ли уже в пуле, проблемы. Ничего не меняет; ключ — через stdin. Если на узле ещё нет программы для разбора ключей, она один раз скачивается |
| `exit_sub_list` | чтение | — | `gw exit-sub list` | Подписки выходов: имя («подписка N» или домен), раз в сутки или вручную, её ключей в пуле, состояние (stale, text). Без адресов |
| `scheme` | чтение | — | `gw scheme` | Схема: маршрут и правила узла, протоколы, выходы, люди и их устройства — у каждого устройства протоколы, действующий маршрут (и откуда он: device/user/default) и профиль правил. Без секретов |
| `traffic_trace` | чтение | `who`*, `target`*, `inbound` | `gw trace` | Куда пойдёт трафик устройства до домена или адреса: путь по шагам (протокол → правила → маршрут → выход), вердикт. Ничего не меняет |
| `rules_status` | чтение | — | `rules status` | Правила «Россия напрямую»: профиль узла, готовы ли списки, блокировка рекламы и торрентов, последнее обновление |
| `forwards_list` | чтение | `traffic` | `fwd list` | Пробросы портов (движок nft): протокол, порт, куда; traffic — со счётчиками. На старом движке — код 5 |
| `awg_engine_status` | чтение | — | `gw awg status` | Движок AmneziaWG: модуль ядра или программа amneziawg-go, что выбрано и почему, совет, предупреждения |
| `awg_engine_check` | чтение | — | `gw awg check` | Можно ли здесь модуль ядра AmneziaWG (ядро, заголовки, пакеты). Ничего не меняет |
| `awg_engine_preview` | чтение | `engine`* | `gw awg engine --dry-run` | Проверка смены движка AmneziaWG без смены (сколько клиентов переподключится, что мешает) |
| `accel_status` | чтение | — | `gw accel status` | Модуль ядра AmneziaWG подробно: загружен ли, на чём каждый вход и нога выхода |
| `mtu_show` | чтение | — | `gw mtu show` | Безопасный MTU: карта сервера, бюджет, MTU сервера и клиентов по входам, ноги выходов, советы |
| `mtu_s4_preview` | чтение | `kind`* | `gw mtu s4` | Кому придётся выдать настройки заново, если уменьшить S4 входа AmneziaWG 2.0 (ничего не меняет) |
| `site_status` | чтение | — | `site status` | Домен и сайт: домен, DNS, сертификат Let's Encrypt (срок), режим (site/domain), шаблон, порт 80 |
| `site_check` | чтение | — | `site check` | Проверить сайт, панель на секретном пути, редирект и сертификат с самого сервера; вердикт и что сделать |
| `site_dns` | чтение | `wait` | `site dns` | Проверить DNS домена: A-записи против адресов сервера; wait — подождать готовности. Запоминает итог проверки (его показывают меню и панель) |
| `site_templates` | чтение | `category`, `query`, `page` | `site templates` | Каталог шаблонов сайта-маскировки: категории, поиск, страницы по 25. Скачивает каталог в кэш узла при первом обращении |
| `site_template_status` | чтение | — | `site template status` | Какой шаблон сайта стоит |
| `site_use_preview` | чтение | `kind`*, `value`* | `site use --dry-run` | Сколько устройств затронет перевод SSTP или OpenConnect на сертификат домена (ничего не меняет) |
| `panel_status` | чтение | — | `panel status` | Веб-панель: работает ли, адреса, режим, отпечаток сертификата, сессии. Без паролей и ссылок входа |
| `pages_list` | чтение | — | `page list` | У кого включена личная страница с настройками (без ссылок) |
| `page_sub_list` | чтение | — | `page sub list` | У кого включена подписка для приложений и сколько в ней устройств (без ссылок) |
| `page_url` | чтение, секрет | `user`* | `page url` | Ссылка на личную страницу человека. ВНИМАНИЕ: ссылка — секрет, по ней открываются настройки его устройств; отдавай только этому человеку |
| `page_sub_url` | чтение, секрет | `user`* | `page sub url` | Ссылка подписки пользователя для приложений (v2RayTun, Hiddify, v2rayN, incy). ВНИМАНИЕ: ссылка — секрет, по ней приложение получает ключи VLESS, Hysteria2, AmneziaWG и WireGuard всех его устройств; отдавай только этому пользователю |
| `bot_status` | чтение | — | `bot status` | Telegram-бот: настроен ли, последний опрос и ошибка, служба. Токен не показывается |
| `bot_proxy` | чтение | — | `bot proxy` | Как бот ходит в Telegram: через выходы группы, напрямую (выбрано, нет ключей, выходы группы не отвечают) или нет связи. Выбор — в панели (SPEC-tg-proxy) |
| `update_check` | чтение | — | `update check` | Есть ли новая версия Kaskad (по правилам меню 11): branch — ветка, auto — выбрана сама. Программу не меняет; найденную сама ветку dev запоминает |
| `host_health` | чтение | — | `host health` | Здоровье сервера: светофор, находки с советами (процессор, память, диск, сеть, часы, сертификаты, службы), каждая проверка и лёгкие пробы сети; by — откуда: collector — сборщик шлюза, bot — бот без шлюза, panel — мгновенный снимок без истории; hostmon — настройки. Ничего не меняет |
| `host_set` | изменение | `on`, `off`, `probes`, `anchors`, `push_level`, `bench_retry`, `dev_threshold`, `dev_spread`, `code` | `host set --by-mcp` | Настройки здоровья сервера: on или off — сама проверка; probes — лёгкие пробы сети (раз в ≈ 30 с по 3 пакета ping к точкам; off — ни одного пакета наружу); anchors — точки проб; push_level — какие уведомления о здоровье шлёт бот. Пробы — только по просьбе человека, anchors — только адреса, которые он назвал. bench_retry (повтор после падения, до 2 проверок в сутки) и dev_* (правило ▲/▼): без code — предпросмотр и code, ничего не сохранено; после согласия человека — тот же вызов с code |
| `host_score` | чтение | `action`=`score` | `host score` | Сервер относительно своей истории и проверки; ничего не меняет. score — как «Обзор › Проверка сервера» в панели: summary.rel_total — главное число, 100 — как обычно у этого сервера (null — меньше 3 звеньев с rel), checked, stale, retry; links — 8 звеньев (сейчас fresh_raw, «обычно» usual_raw, rel — от обычного; rel null — «сравнивать пока не с чем: история N из 3 дней», usual_n — дней истории, или свежего значения нет; ▲/▼ dev, hint); days — 30 дней. Без балла 0–1000 и выводов о сервере. history — проверки за 30 дней (до 50): id, вид, кто (auto — повтор), итог; show — тесты и трафик одной. schedule — записи с номерами, следующие, цена, armed, retry. price — цена до запуска: МБ, минуты процессора, pkgs (что поставит); modes.<вид>.confirm — ключ для host_bench |
| `host_score` | чтение | `action`=`history`, `days`, `mode`, `by`, `skipped` | `host bench history --limit=50` | ″ |
| `host_score` | чтение | `action`=`show`, `id`* | `host bench show` | ″ |
| `host_score` | чтение | `action`=`schedule` | `host bench schedule list` | ″ |
| `host_score` | чтение | `action`=`price`, `full` | `host bench --dry` | ″ |
| `host_bench` | изменение | `full`, `confirm`* | `host bench --by-mcp` | Запустить проверку сервера (как кнопка в панели): тесты процессора, памяти, диска, канала и сети в отдельной службе с низким приоритетом; ответ сразу — id проверки, итог через несколько минут — host_score. Тратит трафик (до сотен МБ, полная — до гигабайт) и, если их нет, ставит sysbench и fio (≈ 50 МБ): только по просьбе человека и после цены host_score action=price: confirm — её ключ того же вида (согласие человека с этой ценой и установкой). Цена изменилась или ключ чужой — отказ, ничего не запущено и не поставлено. Идёт другая проверка — отказ |
| `host_bench_schedule` | изменение | `action`=`add`, `time`*, `mode`*, `days`*, `no_quiet`, `off`, `monthly`, `code` | `host bench schedule add --by-mcp` | Изменить расписание проверок (список и номера — host_score action=schedule; проверки тратят трафик). Без code — предпросмотр: список, цена и code, ничего не сохранено; покажите человеку, согласен — тот же вызов с code. consent — трафик в месяц вырастет до «много трафика»: такое сохраняет только человек (отказ heavy) |
| `host_bench_schedule` | изменение | `action`=`on`, `entry`*, `code` | `host bench schedule on --by-mcp` | ″ |
| `host_bench_schedule` | изменение | `action`=`off`, `entry`*, `code` | `host bench schedule off --by-mcp` | ″ |
| `host_bench_schedule` | **разрушительное** | `action`=`rm`, `entry`*, `code`, `confirm`* | `host bench schedule rm --by-mcp` | ″ |
| `host_tariff` | чтение | `action`=`show` | `host tariff show` | Тариф сервера; ничего не меняет. Скорость порта — для правила здоровья «порт», трафик в месяц — для цены проверок. show — значения и src (user — человек, catalog — каталог хостера). preview — без сохранения: lines (было → будет) и code; значения — только названные человеком. Покажите lines; согласен — host_tariff_set с теми же значениями и code |
| `host_tariff` | чтение | `action`=`preview`, `port`, `traffic`, `clear` | `host tariff set --dry` | ″ |
| `host_tariff_set` | изменение | `port`, `traffic`, `clear`, `code`* | `host tariff set --by-mcp` | Сохранить тариф после согласия человека на lines из host_tariff action=preview: те же значения и code (сервер сверяет, иначе code_mismatch). Новая скорость порта — новая база проверок |
| `backup_summary` | чтение | `parts` | `backup summary` | Что вошло бы в копию настроек: числа и имена по частям, без секретов |
| `hy2_show` | чтение | — | `gw hy2 show` | Hysteria2: порт и смена портов, Salamander, сертификат (домена или самоподписанный и его отпечаток), предупреждения (без секретов; T12) |
| `gateway_enable` | изменение | `base`, `host`, `force` | `gw enable` | Включить шлюз (нужен движок nft): сеть, DNS, адрес для клиентов |
| `gateway_addresses` | чтение | `refresh` | `gw addresses` | Адреса сервера: найденные на интерфейсах, внешние адреса облака с NAT, добавленные вручную; основной, адрес для клиентов, A-запись домена, `route_ok`; `client_found=false` — адреса для клиентов на сервере нет; `client_primary=false` — он есть, но маршрут по умолчанию идёт с другого адреса (`primary`) (PLAN-multi-ip Ш1, Н6). Ничего не меняет; `refresh` — заново, с запросами к api.ipify.org |
| `gateway_set_host` | **разрушительное** | `host`*, `force`, `confirm`* | `gw host` | Задать адрес для клиентов (внешний IP или имя в настройках устройств). IPv4 — только один из адресов сервера (`gateway_addresses`), иначе отказ `address_not_here`; `force` — без проверки, только по прямой просьбе человека (Ш2). Сменился — ВСЕ выданные настройки смотрят на старый адрес: каждому устройству выдать заново |
| `gateway_addresses_in` | разрушительное (`confirm` = имя инструмента) | `ips` (через запятую) или `all` | `gw addresses in` | На каких адресах сервера протоколы и свой сайт принимают подключения (PLAN-multi-ip Ш3): одно правило в `gw_pre`, панель в открытом режиме — только на них. SSH, пробросы и порт 80 — всегда на всех. Отказы «Защиты доступа»: `client_out`, `panel_out`, `via_out` (панель открыта через другой адрес), `address_not_here`; в тексте — возврат `kaskadctl gw addresses in --all` |
| `gateway_exit_src` | изменение | `exit` или `group`, `ip`* (IPv4 или `main`) | `gw addresses exit` | С какого адреса сервера выход подключается к своему серверу (Ш4): xray-выходы — `sendThrough`, AmneziaWG — SNAT ноги в `gw_post` и `conntrack -D` её потока. Отказы: `address_not_here`, `route_other`, `same_endpoint`. Адрес пропал — выход временно с основного (журнал), вернулся — снова с него. «Прокси для API Telegram» к выходам VLESS, Hysteria2, Shadowsocks, Trojan и VMess — всегда с основного адреса |
| `gateway_detect_host` | **разрушительное** | `confirm`* (= `gateway_detect_host`) | `gw host --detect` | Определить адрес для клиентов заново (внешний IP сервера). Сменился — все выданные настройки выдать заново |
| `gateway_apply` | изменение | — | `gw apply` | Применить всё из настроек заново (таблица, маршруты, xray, DNS). Безопасно повторять |
| `gateway_repair` | изменение | — | `gw check --repair` | Проверить шлюз и исправить найденное (как сторож) |
| `net_repair` | изменение | — | `net check --repair` | Проверить движок пробросов и исправить найденное |
| `default_route_set` | изменение | `via`*, `fail` | `gw route` | Маршрут по умолчанию узла: pool, group:<id группы>, exit:<id>, direct, block; fail — если выходы недоступны: block — как у группы, direct — напрямую |
| `user_add` | изменение | `name`*, `route`, `fail` | `gw user add` | Добавить человека (без устройств). route/fail — свой маршрут, иначе как по умолчанию узла |
| `user_route` | изменение | `user`*, `via`*, `fail` | `gw user route` | Маршрут человека: inherit (как у узла), pool, group:<id группы>, exit:<id>, direct, block |
| `user_enable` | изменение | `user`* | `gw user enable` | Включить человека (все его устройства снова подключаются) |
| `user_disable` | изменение | `user`* | `gw user disable` | Выключить человека: его устройства перестают подключаться, записи и ключи сохраняются |
| `device_add` | изменение | `user`*, `name`*, `kind`, `platform`, `access`, `route`, `fail` | `gw device add` | Добавить устройство человеку: тип, платформа, протоколы (access: wg,awg2 — без него входы не выданы), маршрут. Ключи создаются сразу; настройки выдаёт device_issue |
| `device_grant` | изменение | `device`*, `kind`* | `gw device grant` | Выдать устройству ещё один протокол (новые ключи этого протокола) |
| `device_route` | изменение | `device`*, `via`*, `fail` | `gw device route` | Маршрут устройства: inherit (как у человека), pool, group:<id группы>, exit:<id>, direct, block |
| `device_enable` | изменение | `device`* | `gw device enable` | Включить устройство |
| `device_disable` | изменение | `device`* | `gw device disable` | Выключить устройство: перестаёт подключаться, ключи сохраняются |
| `exit_add` | изменение | `key`*, `group` | `gw exit-add --public-only` | Добавить выход: ключ VPN (vless, hy2, ss, trojan, vmess, vpn://) или текст .conf (можно несколько) — в пул, как меню и панель. Первый ключ AmneziaWG может собирать модуль ядра 1–3 мин. Ключ — через stdin, в ответе и журналах его нет. Ссылку подписки (https://…) сервер скачивает сам от root: ссылки на внутренние адреса (сам узел, локальная сеть, метаданные облака) отклоняются. group — зарубежная группа ключей |
| `exit_sub_add` | изменение | `url`*, `manual` | `gw exit-sub add` | Добавить подписку выходов: скачивается сразу (только внешние адреса), её ключи — в пул; дальше раз в сутки меняются только её ключи, ручные не трогаются. goAmnezia — адрес all.vpn |
| `exit_sub_refresh` | изменение | `sub` | `gw exit-sub refresh` | Обновить подписку сейчас (без sub — все): меняются только её ключи; сбой или пустой ответ — пул не трогается |
| `exit_sub_restore` | изменение | `sub`* | `gw exit-sub restore` | Вернуть исключённые: ключи подписки, удалённые из пула вручную, подписка снова приносит — она скачивается заново |
| `exit_sub_auto` | изменение | `sub`*, `value`* | `gw exit-sub auto` | Подписка: on — обновлять раз в сутки, off — только вручную |
| `exit_enable` | изменение | `id`*, `dry`, `rev` | `gw exit --enable` | Включить выход для шлюза |
| `exit_disable` | изменение | `id`*, `dry`, `rev` | `gw exit --disable` | Выключить выход для шлюза (ключ остаётся в пуле, клиенты уходят на другие) |
| `exit_rename` | изменение | `id`*, `name`* | `gw exit` | Дать выходу имя для людей |
| `person_rename` | изменение | `user`*, `name`*, `allow_duplicate` | `gw user rename` | Переименовать человека: id не меняется; дубль имени — отказ (код 3) (T13) |
| `device_rename` | изменение | `device`*, `name`*, `allow_duplicate` | `gw device rename` | Переименовать устройство: id и логин не меняются; дубль — код 3, `data.need = confirm_duplicate` (T13) |
| `inbound_label` | изменение | `kind`*, `name`*, `allow_duplicate` | `gw inbound label` | Подпись протокола для людей; в выданные настройки не попадает (T13) |
| `exit_sub_rename` | изменение | `sub`*, `name`*, `allow_duplicate` | `gw exit-sub rename` | Подпись подписки выходов; пусто — снова домен (T13) |
| `forward_rename` | изменение | `proto`*, `port`*, `name`*, `allow_duplicate` | `fwd rename` | Переименовать проброс; правила не меняются (T13) |
| `server_rename` | изменение | `ip`*, `name`*, `allow_duplicate` | `server rename` | Имя сервера мониторинга (aliases); заметка, страна, провайдер — прежние (T13) |
| `inbound_install` | изменение | `kind`*, `port`, `force`, `awg_engine` | `gw inbound install --new-only` | Установить НОВЫЙ протокол (пакеты, служба, порт). Уже установленный — отказ (код 3): переустановка — inbound_reinstall. Порт занят — отказ с перечнем; force — только по просьбе человека |
| `inbound_enable` | изменение | `kind`* | `gw inbound enable` | Включить протокол |
| `inbound_disable` | изменение | `kind`* | `gw inbound disable` | Выключить протокол: его клиенты отключаются, настройки сохраняются |
| `inbound_port` | **разрушительное** | `kind`*, `port`*, `proto`, `force`, `confirm`* | `gw inbound port` | Сменить порт протокола; выданные настройки со старым портом перестают подключаться — выдать заново |
| `inbound_option` | изменение | `kind`*, `key`*, `value`* | `gw inbound option` | Настройка протокола: l2tp plain (L2TP без IPsec для старых роутеров), socks5 udp |
| `rules_default` | изменение | `profile`*, `confirm_not_rf`, `orgs` | `rules default --via=mcp` | Профиль правил узла: ru_direct (Россия напрямую) или all_exit. ru_direct без действующего согласия ⚠РФ — отказ (code 3, data.need = rf_consent, data.block): покажите блок человеку, затем confirm_not_rf |
| `rules_profile` | изменение | `target`*, `id`*, `profile`*, `confirm_not_rf`, `orgs` | `rules profile --via=mcp` | Профиль правил человека или устройства: inherit, ru_direct, all_exit. ru_direct без действующего согласия ⚠РФ — отказ с блоком, как у rules_default |
| `rules_consent` | изменение | `confirm_not_rf`*, `orgs` | `rules consent --via=mcp` | Подтвердить ⚠РФ без смены профиля (rules_status → rf.mode = legacy, need или rf.uncovered не пуст): «Россия напрямую» уже выбрана, согласия нет. Только с фразой, которую подтвердил человек |
| `rules_block` | изменение | `what`*, `value`* | `rules block` | Блокировка рекламы или торрентов: on/off |
| `rules_update` | изменение | `force` | `rules update` | Скачать списки правил сейчас (таймер делает это раз в сутки) |
| `forward_add` | изменение | `proto`*, `port`*, `to`*, `to_port`*, `name`, `force` | `fwd add --new-only` | Добавить НОВЫЙ проброс порта (движок nft): входящий порт → адрес:порт. Такой проброс уже есть — отказ (код 3, data.exists), заменить — forward_delete и снова forward_add. Порт занят другой программой — код 3; force — только по просьбе |
| `site_domain_set` | изменение | `name`* | `site domain` | Задать домен (DNS проверяется сразу, сертификат не выпускается — site_cert_issue). Рабочий домен (сертификат или сайт) меняется целиком и только после проверки DNS и выпуска сертификата для нового; при отказе остаётся прежний |
| `site_email_set` | изменение | `email`* | `site email` | Почта для Let's Encrypt |
| `site_cert_issue` | изменение | `staging` | `site cert issue` | Выпустить сертификат Let's Encrypt (порт 80; живой не перевыпускается). Отказ — reason и text |
| `site_mode` | изменение | `mode`*, `template`, `random` | `site mode` | Режим: site — сайт-маскировка на домене (template или random), domain — сайт снят, домен и сертификат остаются |
| `site_template_set` | изменение | `id`, `random` | `site template set` | Поставить шаблон сайта из каталога (id) или случайный (random); прежний сайт — в резервную копию |
| `site_use` | **разрушительное** | `kind`*, `value`*, `confirm`* | `site use` | Перевести SSTP или OpenConnect на сертификат домена (on) или обратно (off); устройствам — настройки заново |
| `awg_engine_set` | изменение | `engine`*, `force` | `gw awg engine --why=MCP` | Сменить движок AmneziaWG: kernel (модуль ядра, сборка 1–3 мин) или program. Копия до смены, самопроверка, откат; клиенты переподключаются сами за 15–25 с. Ключи и выданные настройки те же |
| `mtu_reissue` | изменение | `kind`* | `gw mtu reissue` | Узкая карта сервера: новые настройки протокола с меньшим MTU (перечень устройств — выдать заново) |
| `mtu_keep` | изменение | `kind`* | `gw mtu keep` | Оставить MTU как есть: предупреждение не повторится, пока карта та же |
| `page_on` | изменение, секрет | `user`* | `page on` | Включить личную страницу человека и показать ссылку. ВНИМАНИЕ: ссылка — секрет (настройки его устройств) |
| `page_off` | изменение | `user`* | `page off` | Выключить личную страницу: ссылка сразу перестаёт работать |
| `page_sub_on` | изменение, секрет | `user`* | `page sub on` | Включить подписку для приложений (своя ссылка, не страница) и показать ссылку. ВНИМАНИЕ: ссылка — секрет (ключи VLESS, Hysteria2, AmneziaWG и WireGuard его устройств) |
| `page_sub_off` | изменение | `user`* | `page sub off` | Отозвать подписку для приложений: ссылка сразу перестаёт работать |
| `backup_export` | изменение | `password`*, `without` | `backup export --save` | Копия настроек узла, зашифрованная паролем человека, — на сервер в /var/backups/kaskad/exports (0600); агенту — только путь и числа. Импорт — только человек |
| `update_run` | **разрушительное** | `confirm`* (= `update_run`) | `update run` | Обновить Kaskad сейчас — меню 11 без вопросов: проверки файла, копия gokaskad.bak, перезапуск бота с откатом. После обновления переподключите MCP-сервер |
| `inbound_reinstall` | **разрушительное** | `kind`*, `port`, `force`, `confirm`* | `gw inbound install` | Переустановить протокол поверх установленного: службы и пакеты заново; порт и ключи сервера сохраняются, где можно, иначе — перечень устройств, которым выдать настройки заново |
| `inbound_uninstall` | **разрушительное** | `kind`*, `confirm`* | `gw inbound uninstall` | Удалить протокол: службы и порт; устройства теряют этот протокол (порт и ключи сервера хранятся до новой установки) |
| `user_delete` | **разрушительное** | `user`*, `with_devices`, `confirm`* | `gw user del` | Удалить человека; with_devices — вместе с устройствами (их настройки перестают работать навсегда) |
| `device_delete` | **разрушительное** | `device`*, `confirm`* | `gw device del` | Удалить устройство: его ключи и настройки перестают работать навсегда |
| `device_revoke` | **разрушительное** | `device`*, `kind`*, `confirm`* | `gw device revoke` | Снять с устройства протокол: его настройки этого протокола перестают работать (ключи стираются) |
| `device_reset` | **разрушительное** | `device`*, `inbound`, `confirm`* | `gw device reset` | Новые ключи и пароли устройства (все протоколы или один): старые настройки перестают работать, подключения рвутся |
| `exit_delete` | **разрушительное** | `id`*, `dry`, `rev`, `confirm`* | `gw exit-del` | Убрать ключ выхода из пула. Последний ключ — пул пуст, «Прокси для API Telegram» выключается. Тот же ключ в пуле ещё раз (по IP и по домену) — маршруты удаляемой копии переходят на оставшуюся |
| `exit_sub_delete` | **разрушительное** | `sub`*, `keep_keys`, `confirm`* | `gw exit-sub del` | Удалить подписку; её ключи уходят из пула (keep_keys — остаются) |
| `forward_delete` | **разрушительное** | `proto`*, `port`*, `confirm`* | `fwd del` | Удалить проброс порта |
| `gateway_disable` | **разрушительное** | `confirm`* (= `gateway_disable`) | `gw disable` | Выключить шлюз: все протоколы и выходы для клиентов останавливаются (люди, устройства и настройки сохраняются) |
| `gateway_purge` | **разрушительное** | `with_users`, `confirm`* (= `gateway_purge`) | `gw purge --yes` | Удалить шлюз целиком: протоколы, службы, настройки; with_users — ещё людей и устройства |
| `pki_rotate` | **разрушительное** | `kind`*, `confirm`* | `gw pki rotate --yes` | Новый центр сертификации IKEv2: всем устройствам IKEv2 — настройки заново, старые не подключатся |
| `hy2_option` | **разрушительное** | `hop`, `cert`, `confirm`* | `gw hy2 set` | Смена портов Hysteria2 (`hop 20000-20099` / `off`) или его сертификат (`cert domain` / `self`); занятый диапазон — отказ со списком; ссылки Hysteria2 — заново (T12) |
| `hy2_rekey` | **разрушительное** | `confirm`* (= `hy2_rekey`) | `gw hy2 rekey --yes` | Новый самоподписанный сертификат Hysteria2: ссылки с прежним отпечатком — заново (T12) |
| `mtu_s4` | **разрушительное** | `kind`*, `confirm`* | `gw mtu s4 --yes` | Уменьшить S4 входа AmneziaWG 2.0 (сервер ниже 1232): старые настройки входа перестают подключаться — сначала mtu_s4_preview |
| `page_rotate` | **разрушительное**, секрет | `user`*, `confirm`* | `page rotate` | Новая ссылка на личную страницу; старая сразу перестаёт работать. ВНИМАНИЕ: ответ — секрет |
| `page_sub_rotate` | **разрушительное**, секрет | `user`*, `confirm`* | `page sub rotate` | Новая ссылка подписки; старая сразу перестаёт работать — в приложении её нужно заменить. ВНИМАНИЕ: ответ — секрет |
| `site_domain_clear` | **разрушительное** | `confirm`* (= `site_domain_clear`) | `site domain --clear` | Убрать домен: панель, страницы людей и входы — на самоподписанном сертификате узла |
| `site_remove` | **разрушительное** | `confirm`* (= `site_remove`) | `site remove` | Убрать домен и все файлы Kaskad для него (сайт, сертификат, таймеры) |
| `vless_show` | чтение | — | `gw vless show` | VLESS: режим (Reality или TLS со своим доменом), порт, сайт маскировки и его проверка, публичный ключ, shortId (без секретов) |
| `vless_masks` | чтение | `check` | `gw vless masks` | Сайты маскировки VLESS на выбор (свой сайт узла — первым); с check — проверка каждого с узла |
| `vless_mask` | **разрушительное** | `site`*, `no_check`, `confirm`* | `gw vless mask` | Сменить сайт маскировки VLESS (проверка TLS 1.3 с узла); выданные ссылки VLESS перестают подключаться — выдать заново |
| `vless_rekey` | **разрушительное** | `confirm`* (= `vless_rekey`) | `gw vless rekey --yes` | Новые ключ узла и shortId VLESS: всем устройствам VLESS — ссылки заново, старые не подключатся |
| `vless_sync` | **разрушительное** | `confirm`* (= `vless_sync`) | `gw vless sync` | Режим VLESS по сайту узла: TLS со своим доменом (сайт включён) или Reality; при смене — всем устройствам VLESS ссылки заново |
| `vless_mode` | изменяющее, `ask_human` | `mode`*, `site`, `accept_risks` (фраза, `phrase_block: vless`), `risks`, `yes`, `no_check` | `gw vless mode --via=mcp` | Режим VLESS: tls или reality; Reality при домене — с согласием на риски; цена — отказом ядра (`data.text`), затем `yes` (SPEC-vless-tls-default §4.2) |
| `exit_groups_list` | чтение | — | `gw groups` | Группы выходов: id, name, label, role (abroad, ru), выходы по уровням, active_tier, last_step, fail, no_exit — устройств без интернета (null — нет данных), rev. Настройка — в панели |
| `exit_policy_get` | чтение | `group` | `gw policy` | Настройки выходов по группам (только чтение; меняют в панели) |
| `exit_policy_reset` | **разрушительное** | `group`*, `preset`, `dry`, `rev`, `confirm`* | `gw policy-reset --yes` | Сбросить группу к набору (preset, по умолчанию compat); уровни, выходы и fail остаются |
| `exit_standby` | изменение | `id`*, `dry`, `rev` | `gw exit --standby` | Выход в запас вручную: устройства уходят на другие выходы группы |
| `exit_restore` | изменение | `id`*, `dry`, `rev` | `gw exit --restore` | Вернуть выход в работу |
| `exit_maintenance` | изменение | `id`*, `dry`, `rev` | `gw exit --maintenance` | Выход на обслуживание: новые устройства на него не идут |
| `exit_accept_country` | изменение | `id`*, `dry`, `rev` | `gw exit --accept-country` | Принять новую страну выхода |
| `exit_move` | изменение | `id`*, `group`*, `tier`, `dry`, `rev` | `gw exit-move --yes` | Перенести выход в группу той же роли; другая роль — exit_move_role |
| `exit_move_role` | **разрушительное** | `id`*, `group`*, `tier`, `dry`, `rev`, `confirm`* | `gw exit-move --confirm-role --yes` | Перенести выход в группу другой роли (зарубежная ↔ российская): трафик пойдёт через другой IP. Только с согласия человека |
| `rules_ru_via` | **разрушительное** | `target`*, `id`*, `via`*, `confirm`* | `rules ru-via --yes` | «Российские сайты» пользователя или устройства: через российскую группу (её ключи — домашний роутер или сервер в России) или напрямую с сервера. Через группу российский трафик идёт с IP роутера — его провайдер увидит эти соединения: только с согласия человека. Действует при профиле «Россия напрямую» |
| `group_delete` | **разрушительное** | `group`*, `exits_to`, `with_keys` (+ `confirm_with_keys`; не вместе с `exits_to` и `subs_to`), `reassign`, `tg_proxy_to`, `new_exits_to`, `subs_to`, `dry`, `rev`, `confirm`* | `gw group-del --yes` (`--with-keys`) | Удалить группу (не main): exits_to — выходы, reassign — маршруты пользователей, tg_proxy_to, new_exits_to, subs_to — замены; with_keys — российская группа вместе с её ключами (В1, как `exits=drop` панели); не хватает — conflict |
| `group_flap_standby_set` | изменение | `group`*, `on`*, `dry`, `rev` | `gw group-flap-standby` | Нестабильный выход (3 падения за 15 мин) — в запас на 15 мин: on/off у группы |
| `exit_quota_set` | изменение | `id`*, `gb`*, `period`, `reset_day`, `warn_pct`, `action`, `dry`, `rev` | `gw exit` | Квота трафика выхода; не заданное остаётся |
| `exit_quota_clear` | изменение | `id`*, `dry`, `rev` | `gw exit --no-quota` | Снять квоту трафика выхода |
| `exit_transitions` | чтение | `id`*, `days`, `limit`, `before` | `gw exit-history` | Смены состояния выхода с причинами; db=false — истории нет |
| `exit_speedtest` | изменение | `id`* | `gw exit-speedtest` | Тест скорости выхода сейчас, ждёт до 60 с; отказ (занято, лимит, выход выключен) — сразу, `data.why`; не успел — `data.running=true` |
| `exit_speedtest_force` | **разрушительное** | `id`*, `confirm`* | `gw exit-speedtest --force` | То же сверх суточного лимита тестов (месячный остаётся, один тест за раз). Только по просьбе человека |
| `exit_tofu_check` | изменение | `key`* | `gw exit-preview --tofu` | Отпечаток сертификата ключа без своей проверки (allowInsecure) — показать человеку до `exit_add_tofu`; идёт к серверу ключа, цепочка доверия сошлась — проверка включается без вопроса |
| `exit_add_tofu` | **разрушительное** | `key`*, `pin_tofu`*, `group`, `confirm`* (= `pin_tofu`) | `gw exit-add --public-only --pin-tofu` | Добавить ключ, закрепив отпечаток из `exit_tofu_check` (TOFU, SE §7.7–7.8, §9.1); сертификат сменился — отказ. Только с согласия человека |
| `tz_get` | чтение | — | `tz get` | Часовой пояс Kaskad и системы |
| `tz_set` | изменение | `name`* | `tz set` | Задать часовой пояс Kaskad (IANA) |

| Ресурс | Источник | MIME | Что внутри |
|---|---|---|---|
| `kaskad://status` | инструмент `gateway_status` | `application/json` | Состояние узла / Node status |
| `kaskad://scheme` | инструмент `scheme` | `application/json` | Схема / Scheme |
| `kaskad://exits` | инструмент `exits_list` | `application/json` | Выходы / Exits |
| `kaskad://devices` | инструмент `devices_list` | `application/json` | Устройства / Devices |
| `kaskad://version` | инструмент `version` | `application/json` | Версия / Version |
| `kaskad://guide` | файл `AGENT_GUIDE.md` | `text/markdown` | Гайд для агента / Agent guide |
