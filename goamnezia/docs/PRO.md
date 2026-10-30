# goAmnezia pro — режимы с сайтом: «VPN и сайт» и «Сайт и VPN на 443» (домен и Let's Encrypt; самоподписанный на IP — запасной путь) — `lib/website.sh`

Модуль спринта 3 (PLAN §4.3–4.4, §6.10; CONTRACT §1, §3.1, §5.1). Написан отдельно от `install.sh`,
`bootstrap.sh`, `lib/actions.json`, бота и MCP — здесь перечислено, что в них подключить.
Проверки: `tests/test_website_dryrun.sh` (116 проверок без сервера), `tests/test_templates_catalog.py`, разделы 11 и 11a `tests/test_actions_dryrun.sh`.

**Договор о режимах** (уточнён владельцем 10.09.2026; в меню — названия в кавычках, в `config.json` —
`mode`): `lite` — «Только VPN», сайта нет; `lite_site` — «VPN и сайт»: домен, сайт-заглушка и панель на
TCP/443 с настоящим сертификатом Let's Encrypt, AmneziaWG на своём отдельном порту; `pro` — «Сайт и VPN
на 443»: то же самое, но AmneziaWG стоит на UDP/443 рядом с сайтом. Два
режима с сайтом отличаются **только местом AmneziaWG**: сайт, домен, сертификат и панель у них
общие, и переход между ними ничего в nginx и сертификате не трогает. Самоподписанный сертификат —
**запасной путь** ровно для двух случаев: домена ещё нет или A-запись ещё не указывает на сервер.
Вид сертификата хранится в `config.json` (`site.cert`) и больше не выводится из режима — его
спрашивают `ga_site_cert_kind` и `ga_site_domain_ready`.

## 1. Файлы

| Файл | Что это |
|---|---|
| `lib/website.sh` | nginx, Let's Encrypt, vhost из шаблона, панель и доступы агентов на домене, шаблон сайта, внешняя проверка, включение и выключение режимов с сайтом |
| `lib/nginx/goamnezia.conf.tpl` | один шаблон vhost `sites-available/goamnezia` на оба режима: блоки-варианты `# GOAMNEZIA_ONLY_PRO_BEGIN…END` (ACME-локация и редирект на 80, HSTS) и `# GOAMNEZIA_ONLY_SELFSIGNED_BEGIN…END` (сайт по 80 без редиректа); метки `SERVER_NAME_PLACEHOLDER`, `SSL_CERT_PLACEHOLDER`, `SSL_KEY_PLACEHOLDER`; готовый файл собирает `website_write_conf` |
| `lib/templates_lib.sh` | библиотека шаблонов из навыка `site-templates-library` — меню выбора, клон, проверка страницы, деплой с чисткой служебных файлов. Изменены только пути и маркеры (см. §8) |
| `lib/catalog_tool.py` | python-помощник каталога для бота и панели (страницы, поиск, случайный, `short_key` для callback_data, `validate`) |
| `templates_catalog.json` | каталог: 18 категорий, 33 467 шаблонов, sha256 совпадает с навыком (`ca31e8d0…3028e1`) |
| `lib/lang/ru.d/website.sh`, `lib/lang/en.d/website.sh` | 129 ключей: `website_*` модуля + `templates_*`/`custom_git_*`, которые запрашивает библиотека |
| `tests/test_website_dryrun.sh`, `tests/test_templates_catalog.py`, `tests/fixtures/site_min/` | проверки без сервера |

Раскладка на сервере (CONTRACT §1, NEIGHBOURS.md): vhost `/etc/nginx/sites-available/goamnezia` +
симлинк в `sites-enabled`; сайт `/var/www/goamnezia-site`; **новое** — каталог ACME-проверок
`/var/www/goamnezia-acme` (отдельно от корня сайта: смена шаблона переписывает корень целиком, а
`deny /\.` закрыл бы `/.well-known`); хук `/etc/letsencrypt/renewal-hooks/deploy/goamnezia-nginx-reload.sh`;
таймер `goamnezia-cert-renew.timer` (только если в системе нет `certbot.timer`). Все три — в
`NEIGHBOURS.md`.

## 2. Функции `lib/website.sh`

Все функции: результат в stdout только там, где сказано; UI — stderr; мутации под
`ga_with_config_lock` (пара `fn` / `_fn_locked`); ошибка = `ga_err <код> <сообщение> [rc]`,
то есть `GA_ERR_CODE`/`GA_ERR_MSG` доходят до `_action_run` диспетчера.

| Функция | Что делает | stdout | Коды ошибок |
|---|---|---|---|
| `website_ensure_nginx` | apt (профиль `web` из `lib/deps.sh`), каталоги `sites-*`, снять `default`, `systemctl enable --now nginx` | — | `nginx_install_failed`, `nginx_dir_failed` |
| `website_ensure_certbot` | apt (профиль `tls`) | — | `certbot_install_failed` |
| `website_write_conf <домен> [auto\|full\|http\|selfsigned]` | vhost из шаблона + config.json, симлинк, `nginx -t` с откатом на прежний файл, reload. `auto`: полный vhost на домене, если сертификат есть, иначе временный HTTP-only для ACME; `selfsigned`: vhost запасного пути — `server_name _`, `ssl_certificate` на `tls/site.{crt,key}`, сайт и по 80 (без ACME, редиректа и HSTS), панельная location не собирается, маршрут MCP и Alt-Svc — да. Всегда, в обоих вариантах и в обоих server — глушилка секретных путей (`/p-`, `/s-`, `/a-`, `/r-`, `/d-` + 43 знака, путь панели `app-…`): без живого маршрута и по `http://` такой путь отвечает как любой путь сайта (на 80 в Pro — редирект на HTTPS), но без строки в журнале nginx | — | `invalid_domain`, `invalid_conf_kind`, `nginx_template_missing`, `nginx_dir_failed`, `nginx_render_failed`, `write_failed`, `nginx_backup_failed`, `nginx_conf_invalid`, `nginx_reload_failed` |
| `website_set_alt_svc on\|off` | `options.alt_svc` → `add_header Alt-Svc 'h3=":443"; ma=86400' always` | — | `invalid_toggle`, … |
| `website_panel_path_set <путь>` / `website_panel_path_clear` | `panel.miniapp_path` → `location ^~ /<путь>/ { proxy_pass http://127.0.0.1:1985/; … access_log off; }`. Путь: `^[A-Za-z0-9][A-Za-z0-9_-]{5,63}$`, не начинается с `a-` (маршрут агентов) | — | `invalid_path`, … |
| `website_mcp_location_on` / `_off` | `site.mcp_location` → `location ~ ^/a-[A-Za-z0-9_-]{43}/ { proxy_pass http://127.0.0.1:1986; proxy_http_version 1.1; access_log off; }` | — | … |
| `website_issue_cert <домен>` | **не перевыпускает**, если сертификат есть и не истекает 30 дней (правило владельца, лимиты LE); иначе certbot `certonly --keep-until-expiring --deploy-hook "systemctl reload nginx"`, webroot через наш vhost (nginx жив) или standalone на 80; `GOAMNEZIA_LE_STAGING=1` → `--staging`; `GOAMNEZIA_LE_EMAIL` → `--email`, иначе `--register-unsafely-without-email`; затем `website_setup_auto_renew` | — | `invalid_domain`, `certbot_install_failed`, `acme_root_failed`, `cert_failed`, `cert_missing` |
| `website_setup_auto_renew` | deploy-hook в `renewal-hooks/deploy/` + `certbot.timer`, а без него `goamnezia-cert-renew.{service,timer}` (ежедневно, `certbot renew --quiet --deploy-hook`) | — | только предупреждения |
| `website_renew_cert` | `certbot renew` (продление по сроку, принудительного нет) | — | `certbot_missing`, `renew_failed` |
| `website_cert_expiry <домен>` | | `<дата ISO-8601 UTC> <дней>`; rc 1 без сертификата | `invalid_domain` |
| `website_deploy_template <id\|__random__>` | проверка id → `download_template` (кэш через `mktemp`, код причины из `<кэш>/.last_error`) → под замком `deploy_template_to_nginx` (бэкап старого сайта, чистка README/LICENSE/*.md/package.json/…, chown www-data) → маркеры `.goamnezia_template_id` / `.goamnezia_template_source` → `site.template_id`, `site.template_source` (= `repo_url`) | JSON `website_current_template` | `module_missing`, `catalog_missing`, `unknown_template` (rc 2), `download_failed`, `no_index`, `app_shell`, `broken_assets`, `deploy_failed`, `tmp_failed`, `config_write_failed` |
| `website_change_template_interactive` | SSH-меню: `interactive_template_selection` библиотеки (категория → список по 25 с поиском/`r` случайный → превью → `confirm`), свой git-репозиторий тоже; затем деплой | — | те же + отмена rc 1 |
| `website_current_template` | маркеры в корне + config.json + каталог | JSON `{template_id, template_source, name, category, license, preview_url, root, deployed, index_present}` | — |
| `website_check [домен]` | без замка: все A-записи домена (`dig`, `ga_site_dns_state`) против всех адресов машины — глобальные IPv4 на интерфейсах и `server_ip` из config.json (сеть — только если пусто); `dns_state` ok / partial / elsewhere / none / unknown (unknown — DNS не ответил или своих адресов не узнать), `dns_ips`, `dns_foreign`, `dns_aaaa_foreign` (AAAA не на IPv6 машины — Let's Encrypt сначала пробует IPv6), `dns_cloudflare` (чужие адреса сплошь из сетей Cloudflare — включено проксирование); вердикт `dns_partial` — после сертификатных, чтобы не заслонять истекающий сертификат; ответ DNS разбирается по статусу (SERVFAIL и таймаут — не «записей нет»), кэш по доменам в `state/dns.cache`, сбой кэшируется на минуту; HTTPS с внешнего IP `curl --resolve <домен>:443:<ip>`, состояние nginx, `index.html`, срок сертификата | JSON `{domain, mode, server_ip, dns_ip, dns_ok, listen, https_code, https_ok, nginx_active, index_present, cert_expires, cert_days_left, cert_ok, verdict, dry_run, checked_at}`; `verdict`: `ok\|not_pro\|no_domain\|dns_mismatch\|dns_partial\|dns_unknown\|no_cert\|http_error\|no_site\|cert_expiring\|dry_run` (`dns_unknown` — DNS не ответил: ниже сертификатных проверок, не «поправьте DNS») | — |
| `website_enable_pro <домен> [tpl] [pro\|lite_site]` | DNS-подсказка → nginx → `domain` в config.json → временный vhost (если сертификата нет) → сертификат → `site.mcp_location=true`, если `mcp.http_enabled` уже включён → полный vhost → шаблон (явный — обязателен; без него случайный, если `index.html` нет, до 3 попыток; иначе оставить) → `mode` = третий аргумент (по умолчанию `pro`), `site.cert=letsencrypt`. Это **основной путь обоих режимов с сайтом**: работа модуля одинакова, меняется лишь слово в `mode`. С запасного самоподписанного: он заменяется на LE, сайт остаётся. **Временный vhost пишется, только когда терять нечего** — живого vhost с ACME-локацией на сервере нет: он знает лишь про 80 и уносит с собой 443, а на смене домена это роняло работающий сайт ещё до ответа certbot. Если наш vhost уже стоит, проверку HTTP-01 он проводит сам, и 443 всё это время работает на прежнем домене. **Откат при отказе certbot — по фактическому состоянию, а не по `mode`:** прежний домен возвращается в config.json (`website_domain_rolled_back`), а vhost — тот, что работал до попытки (полный на прежнем домене, если его сертификат жив, иначе запасной самоподписанный). Если нашего vhost до вызова не было (первая установка, переход из `lite`), всё записанное снимается: мастер пишет ЖЕЛАЕМЫЙ режим в config.json ДО сайта, поэтому опора на `mode` оставляла живой vhost при `mode=lite`, а предполётная проверка принимала занятый чужим порт за свой | — | все выше + `config_write_failed` |
| `website_switch_site_mode <pro\|lite_site>` | переключить режим МЕЖДУ двумя режимами с сайтом: одна запись `mode` в config.json, вид сертификата сохраняется. nginx, сертификат, домен, шаблон и панель не трогаются вовсе — иначе перенос VPN с 443 стоил бы перевыпуска сертификата и потери домена. Порт профиля двигает вызывающий. Из `lite` — `needs_site` | — | `invalid_mode` (rc 2), `needs_site` (rc 2), `config_write_failed` |
| `website_disable_pro [lite_site\|lite]` | совместимое имя: `lite_site` — то же, что `website_switch_site_mode lite_site` (домен и сертификат НЕ снимаются, они нужны второму режиму так же); `lite`: vhost и симлинк убрать, `nginx -t` + reload, `mode=lite`. Сертификат LE и файлы сайта остаются (повторное включение не тратит лимит LE и не качает шаблон) | — | `invalid_mode` (rc 2), `config_write_failed`, коды `switch_site_mode` |
| `website_selfsigned_ensure [--force]` | ключ EC P-256 и сертификат на 3650 дней в `$GOAMNEZIA_DIR/tls/site.{key,crt}` (каталог 0700, файлы 0600, атомарно через mv), CN и SAN IP = `server_ip`; живой (≥ 30 дней, тот же IP) не трогается — его отпечаток закреплён у агентов MCP; смена IP, истечение и `--force` → перевыпуск. openssl-расширения через конфиг-файл (OpenSSL 1.1/3.x и LibreSSL). В dry-run создаётся по-настоящему | — | `openssl_missing`, `no_server_ip`, `tls_dir_failed`, `tmp_failed`, `selfsigned_failed`, `write_failed` |
| `website_selfsigned_renew` | `--force` + пересборка vhost (она же перечитывает сертификат в nginx); новый отпечаток в stderr. Условие пересборки — по **виду сертификата** (`ga_site_cert_kind = selfsigned`), а не по названию режима: запасной путь бывает и в `pro`, и там проверка по режиму оставляла nginx со старым сертификатом до следующего reload | — | те же |
| `website_cert_fingerprint [файл]` | | SHA-256 «AA:BB:…» (по умолчанию `tls/site.crt`); rc 1 без файла | — |
| `website_selfsigned_expiry` | | `<дата ISO-8601 UTC> <дней>`; rc 1 без сертификата | — |
| `website_enable_site_selfsigned [tpl] [lite_site\|pro]` | **запасной путь** (домена нет или A-запись ещё не разошлась): nginx → самоподписанный сертификат → `site.mcp_location=true`, если `mcp.http_enabled` → vhost `selfsigned` (`server_name _`, ответ по IP) → шаблон (как у `enable_pro`) → `mode` = второй аргумент (по умолчанию `lite_site`), `site.cert=selfsigned`. Панель на домене здесь не работает: браузер сертификату не доверяет. Повтор — ремонт vhost | — | коды nginx, сертификата, шаблона, `config_write_failed` |
| `website_disable_site` | vhost и симлинк убрать, `nginx -t` + reload, `mode=lite`, `site.cert=""`. Файлы сайта и сертификат остаются | — | `config_write_failed` |

Настройки `website_set_*`, `website_panel_path_*`, `website_mcp_location_*` в «Только VPN» (`lite`) только
пишут config.json (`website_saved_applies_later`) и применяются при включении режима с сайтом — иначе
«Alt-Svc on» в «Только VPN» воскрешал бы снятый сайт. В обоих режимах с сайтом они пересобирают текущий
vhost; панельная location собирается там, где vhost стоит на домене (вариант `pro` шаблона), и не
собирается на запасном самоподписанном.

### 2.0 Запасной путь: сайт по IP с самоподписанным сертификатом — почему так

Этот путь включается в ЛЮБОМ из двух режимов с сайтом, когда домена ещё нет или его A-запись
ещё не указывает на сервер (`website_enable_site_selfsigned`). Основной путь обоих режимов —
домен и Let's Encrypt; сюда сервер попадает временно и выходит, как только домен готов.

- **Сайт и по 80, и по 443, без редиректа.** Обычный веб-сервер на голом IP отдаёт страницу по
  HTTP как есть; редирект на `https://<IP>/` вёл бы сканер и браузер прямо в предупреждение о
  самоподписанном сертификате — лишний сигнал. ACME-локации на 80 нет: домена нет, проверять
  Let's Encrypt нечего. HSTS не отдаём: на недоверенном сертификате браузер его игнорирует, а
  для наблюдателя «строгий HTTPS без доверия» — противоречие.
- **`server_name _`** — сервер отвечает на любой Host и по IP; `default_server` не ставим, чтобы
  не конфликтовать с соседями по nginx (NEIGHBOURS.md), при одном нашем vhost на 443 он и так
  умолчание.
- **Сертификат — наш секрет, а не системный**: `/opt/goamnezia/tls` (0700), в бэкапе и wipe вместе
  с каталогом продукта. EC P-256 (как у большинства современных сайтов — не выделяется), CN и SAN
  IP = `server_ip`, 10 лет (продлевать некому). Перевыпуск меняет отпечаток, по которому агенты
  MCP доверяют серверу (PLAN §6.10), поэтому живой сертификат не трогаем; исключения — смена IP,
  истечение, явный `website_selfsigned_renew` из SSH («Сертификат → Перевыпустить»).
- **Панель на домене не включается**: браузер самоподписанному сертификату не доверяет, а её
  allowlist Host и код входа из бота рассчитаны на домен. Отказ моста — `not_pro` с полями
  `cert=selfsigned` и `domain`; на настоящем сертификате панель работает в ОБОИХ режимах с
  сайтом (`_action_require_pro` спрашивает про сертификат и домен, а не про название режима).
  Маршрут агентов `/a-<secret>/mcp` и Alt-Svc — включаются и здесь.
- **Переходы без пересоздания контейнеров и ключей**: меняются только nginx-конфиг, сертификат в
  конфиге и `mode`/`site.cert`; профили и `users.json` не трогаются (проверяется тестом).
  Переход `pro ↔ lite_site` не трогает и их: certbot не зовётся, vhost остаётся байт-в-байт
  прежним, каталог `tls/` не заводится (раздел 11a `tests/test_actions_dryrun.sh`).

### 2.1 Поля config.json (дополнить CONTRACT §3.1 и `_ga_config_skeleton` в `lib/common.sh`)

```json
"site": {"template_id": "", "template_source": "", "listen": "public", "mcp_location": false, "cert": ""}
```

`listen` — всегда `public` (сайт сам держит TCP/443; поле оставлено ради стабильности ответов
`status` и `site-check`); `mcp_location` — маршрут `/a-<secret>/mcp` в vhost; `cert` —
`letsencrypt` (домен готов — оба режима с сайтом) | `selfsigned` (запасной путь) | `""` («Только VPN»),
пишется вместе с `mode` одной записью (`_ga_site_set_mode`), чтобы `status` не увидел «pro» с
«selfsigned» между двумя вызовами.
Модуль терпит отсутствие полей (по умолчанию `public` / `false`). Уже существующие поля:
`domain`, `mode`, `panel.miniapp_path`, `panel.port`, `options.alt_svc`, `mcp.http_enabled`,
`mcp.http_port`.

### 2.2 Переменные окружения (стенды и тесты)

`GOAMNEZIA_DRY_RUN=1` — ни apt, ни certbot, ни nginx, ни systemctl (в stderr `DRY: …`); на диск
пишется только туда, куда стенд указал явно: `GOAMNEZIA_NGINX_DIR` (вместо `/etc/nginx`),
`GOAMNEZIA_SITE_ROOT`, `GOAMNEZIA_LE_DIR` (вместо `/etc/letsencrypt`), `GOAMNEZIA_ACME_ROOT`,
`GOAMNEZIA_SYSTEMD_DIR`, `GOAMNEZIA_TLS_DIR` (вместо `$GOAMNEZIA_DIR/tls`). Без них в dry-run — только
DRY-строки, `/etc` и `/var/www` не трогаются; самоподписанный сертификат и в dry-run создаётся
по-настоящему в `$GOAMNEZIA_DIR/tls` (каталог продукта стенд задаёт всегда).
`GOAMNEZIA_TEMPLATE_LOCAL_DIR=<каталог>` — шаблон из локального каталога без клона (проверка
готовности страницы `_tpl_page_ok` всё равно выполняется). `GOAMNEZIA_CATALOG` — путь к каталогу
(bash и python). `GOAMNEZIA_LE_STAGING=1`, `GOAMNEZIA_LE_EMAIL`, `GOAMNEZIA_NGINX_TPL`.
`GOAMNEZIA_SERVER_IP` — как у остальных модулей.

### Почта центра сертификации и сторож срока (доделки §1d.2 №11)

| Функция | stdout | Что делает |
|---|---|---|
| `ga_site_acme_email` | адрес или пусто (rc 1) | почта из `config.json` (`acme_email`), проверенная `ga_site_email_valid` |
| `website_set_acme_email <адрес|пусто>` | пусто | запись под замком; пустая строка стирает; негодный адрес → `invalid_email` (rc 2) |
| `website_cert_tick` | JSON `{mode, kind, domain, expires, days, warn, renewed}` | суточный тик `goamnezia-cert.timer`: за `GA_SITE_CERT_WARN_DAYS` (21) до конца пробует продлить сам (`website_renew_cert` или `website_selfsigned_renew`), не вышло — `cert_expiring` в очередь событий; срока не видно вовсе — `cert_unknown` (у режима с сайтом сертификат обязан быть) |

Почта уезжает в `certbot --email`; `GOAMNEZIA_LE_EMAIL` её перекрывает (стенды и CI). Без
почты остаётся `--register-unsafely-without-email` и предупреждение `website_cert_no_email`:
без адреса Let's Encrypt физически не может сообщить о сорвавшемся продлении. Порог тревоги
выше порога перевыпуска (30) намеренно — если к 21-му дню срок не сдвинулся, автопродление
сорвалось, а времени починить руками ещё три недели.

## 3. Что подключить в `install.sh`

### 3.1 Модули

После `export.sh`:

```bash
# shellcheck source=/dev/null
[ -f "$LIB_DIR/website.sh" ]    && source "$LIB_DIR/website.sh"    # сам подключает templates_lib.sh
```

### 3.2 Мастер установки — шаг «Сайт» (реализовано, `_site_bring_up` в `install.sh`)

Оба режима с сайтом поднимаются ОДНОЙ веткой: спросили домен — и путь выбирается не по режиму, а
по готовности домена.

```bash
_site_bring_up "$mode" "$domain"   # mode = lite_site | pro
#   домена нет вовсе          → ms_no_domain_fallback   → website_enable_site_selfsigned "" "$mode"
#   A-запись указывает сюда   →                            website_enable_pro "$domain" "" "$mode"
#   домен есть, DNS не дошёл  → ms_dns_not_ready_fallback → website_enable_site_selfsigned "" "$mode",
#                                домен всё равно в config.json + ms_issue_later_hint
```

Модуль записывает `mode` сам — только при успехе; при провале мастер возвращает `mode=lite`, но
`domain` в config.json остаётся, и повтор из меню ничего не спрашивает заново.

### 3.3 Меню «Настройки → Режим и сайт» (пункт 1 `submenu_settings`, реализовано)

Экран `_mode_site_screen` (`install.sh`). В шапке — предполётная проверка портов
(`preflight_modes` + `preflight_render_modes … brief`, PLAN §4.3.1; пересчёт — пункт 8); дальше режим,
домен, адрес сайта, сертификат, шаблон, Alt-Svc и состояние панели на домене (сам адрес — только
пунктом 7, NC-15):

| № | Пункт | Что зовёт |
|---|---|---|
| — | шапка: режим, домен, шаблон (`website_current_template` → `name`), сертификат (`website_cert_expiry`) | |
| 1 | Сменить режим (`_site_mode_change`): два других режима, недоступные по проверке портов помечены → `_site_transition`. Из «Только VPN» — домен (`_site_ask_domain_optional`, пока домена нет — три строки `_domain_help`), подтверждение, `_site_bring_up`, затем порт VPN под новый режим (`_site_vpn_port_plan`: на UDP/443 или с него, с вопросом); между режимами с сайтом — `website_switch_site_mode` и тот же перенос порта; в «Только VPN» — `confirm` → `website_disable_site` | |
| 2 | Домен (`_site_domain_change`): при живом сайте (оба режима с сайтом) — `website_enable_pro "$new" "" "$mode"` и `_panel_ensure_service` (новый сертификат и vhost; старый сертификат остаётся в `/etc/letsencrypt`); в «Только VPN» — только запись в config.json (`ms_domain_saved_lite`). Предупреждение `ms_domain_change_warn` говорит всё, что происходит с живым сайтом: пока идёт выпуск, сайт и панель отвечают по ПРЕЖНЕМУ домену; не удалось — всё вернулось к нему же; удалось — прежний домен сайт больше не открывает, и выданные на нём ссылки и адреса агентов надо раздать заново | |
| 3 | Шаблон сайта: `website_change_template_interactive`; подпункт «случайный» — `website_deploy_template __random__ >/dev/null` | |
| 4 | SSL: показать `website_cert_expiry`; «выпустить/проверить» — `website_issue_cert "$domain"` (сам не перевыпускает живой); «продлить» — `website_renew_cert` | |
| 5 | Проверка сайта: `website_check` → напечатать поля и `verdict` (ключи `site_check_*`) | |
| 6 | Объявлять HTTP/3 (Alt-Svc) — переключатель `website_set_alt_svc on\|off` с текущим значением из `options.alt_svc` | |
| 7 | Панель на домене — `_site_panel_screen` (см. ниже) | |
| 8 | Повторить проверку портов (`preflight_modes` заново) | |
| 0 | назад | |

Панель на домене — экран `_site_panel_screen` (сюда же ведёт «Настройки → Веб-панель, админы бота, ИИ-агенты →
Панель и мини-апп» → пункт 7):
включить = `website_panel_path_set "$(ga_site_panel_path_new)"`, выключить =
`website_panel_path_clear`, перевыпустить = снова `set` с новым путём. Адрес:
`https://<domain>/<path>/`.

### 3.4 Диспетчер `--action=`

Набросок ниже — ранний эскиз ветки (в нём ещё нет `lite_site`); в install.sh она устроена по
уточнённому замыслу владельца. Что в ней обязательно и чего в эскизе не было: **значение `--mode`
проверяется первым делом** — допустимы только `pro`, `lite_site` и `lite`, всё остальное
отвечает `invalid_mode` (`act_mode_unknown`). Без этого страж отсутствовал вовсе: ветка `*)`
читала «не pro и не lite_site» как «иди в lite», и `change-mode --mode=` с пустым значением
СНИМАЛ живой сайт — домен, сертификат и панель на 443 — и отвечал «успешно». Общий страж
диспетчера тут не помогает: непустое значение он ловит по `enum` (`bad_arg`), а пустое пропускает
намеренно — у других действий пустой аргумент осмыслен (`--value=` значит «убрать»).

```bash
change-mode)
    m=$(_arg mode); d=$(_arg domain)
    if [ "$m" = "pro" ]; then
        _require_fn website_enable_pro || return 1
        [ -n "$d" ] || d=$(_cfg '.domain')
        [ -n "$d" ] || { ga_emit_json error "$(t act_domain_required)" "code=missing_arg"; return 1; }
        _action_run enable_failed website_enable_pro "$d" || return 1
        out=$(website_current_template 2>/dev/null) || out=""
        ga_emit_json success "$(tf act_mode_set pro)" "mode=pro" "domain=${d}" "template=@json:$(_as_json "$out" 'null')"
    else
        if [ "$(_cfg '.mode')" = "pro" ]; then
            _require_fn website_disable_pro || return 1
            _action_run disable_failed website_disable_pro || return 1
        else
            ga_config_merge '{"mode":"lite"}' || { _action_fail config_write_failed; return 1; }
        fi
        ga_emit_json success "$(tf act_mode_set lite)" "mode=lite" "domain=$(_cfg '.domain')"
    fi
    ;;

change-template)
    _require_fn website_deploy_template || return 1
    _action_run deploy_failed website_deploy_template "$(_arg template)" || return 1
    ga_emit_json success "$(tf act_template_set "$(printf '%s' "$out" | jq -r .template_id)")" \
        "template=@json:$(_as_json "$out" 'null')"
    ;;

site-check)   # readonly: замок не берётся
    _require_fn website_check || return 1
    out=$(website_check 2>/dev/null) || out=""
    ga_emit_json success "$(t act_ok)" "site=@json:$(_as_json "$out" 'null')" \
        "verdict=$(printf '%s' "$out" | jq -r '.verdict // "unknown"')"
    ;;

miniapp-status)
    # Сначала факт, потом ответ: путь в конфиге — ещё не работающая панель.
    # configured — что записано, available/reason — можно ли в этом режиме
    # вообще (_site_panel_reason — тот же вопрос, что задаёт miniapp-on),
    # enabled — работает ли на самом деле (DEFECTS §1.8).
    p=$(_cfg '.panel.miniapp_path'); d=$(_cfg '.domain')
    configured=false; available=false; on=false
    [ -n "$p" ] && configured=true
    reason=$(_site_panel_reason); [ -z "$reason" ] && available=true
    [ "$available" = true ] && [ "$configured" = true ] && on=true
    ga_emit_json success "$(t act_ok)" "enabled=@json:${on}" "configured=@json:${configured}" \
        "available=@json:${available}" "reason=${reason}" "hint=$(_site_panel_reason_hint "$reason")" \
        "mode=$(_cfg '.mode')" "domain=${d}" \
        "nginx=@json:$(_as_json "$(website_check 2>/dev/null | jq -c '{nginx_active, https_ok, verdict}')" 'null')"
    ;;
# miniapp-on / miniapp-rotate: path=$(ga_site_panel_path_new); _action_run panel_failed website_panel_path_set "$path";
#   ответ содержит url=https://<domain>/<path>/ → в actions.json пометить "secret": true (и, как agent-*, "mcp": null —
#   агент не должен выдавать себе вход в панель). miniapp-off: website_panel_path_clear.
```

Коды модуля (`unknown_template`, `download_failed`, `no_index`, `app_shell`, `broken_assets`,
`cert_failed`, `nginx_conf_invalid`, `invalid_path`, …) доходят до ответа через `_action_run`.
Бот и панель показывают причину отказа шаблона по `code` — как в навыке.

### 3.5 `lib/actions.json` (согласовать с диспетчером и MCP)

- `param_types.template.pattern` сейчас `^[A-Za-z0-9_-]{1,64}$`, а в каталоге 165 id длиннее 64
  символов (максимум 80): нужно `^[A-Za-z0-9_-]{1,120}$` (`__random__` подходит). Иначе
  `change-template` отвергает часть каталога до вызова модуля.
- Добавить `miniapp-on`, `miniapp-off`, `miniapp-rotate` (PLAN §5.3) и `site-alt-svc --state=on|off`
  (простые обёртки над `website_set_*`).
- `site-check` — `readonly: true` (уже так), `change-template` и `change-mode` — с замком (уже так).

### 3.6 Соседние модули

- `lib/agents.sh` (`mcp-http-on|off`, готово — CONTRACT §4.8): в режимах с сайтом после
  включения транспорта — `website_mcp_location_on`, при выключении — `website_mcp_location_off`.
  Адрес агента по договору — тот, которым сайт отдаётся сейчас: `https://<domain>/a-<secret>/mcp`
  при готовом домене (оба режима с сайтом) и `https://<IP>/a-<secret>/mcp` с отпечатком
  самоподписанного — на запасном пути. `ga_agents_endpoint` и `agents_http_status` спрашивают
  `ga_site_domain_ready`, а не название режима: проверка по режиму выдавала в `lite_site` с
  готовым доменом адрес по IP с отпечатком запасного сертификата, то есть ссылку, по которой
  доверия не будет.
  Если транспорт включили ещё в «Только VPN»,
  `website_enable_pro` / `website_enable_site_selfsigned` добавляют маршрут сами
  (`_ga_site_mcp_route_auto`), а `_ga_site_set_mode` после каждой смены режима зовёт
  `_agents_unit_reconcile_locked` — юнит `goamnezia-mcp-http` пересобирается под новый режим
  (петля за nginx ↔ свой TLS-порт в «Только VPN»).
- `lib/backup.sh`: nginx-конфиг и `/etc/letsencrypt/live|archive|renewal/<domain>` уже в архиве
  (пути `NGINX_SITE_CONF`/`NGINX_SITE_LINK` те же). Корень сайта не архивируем; после
  `restore_backup` на чистом сервере: `[ -f "$SITE_ROOT/index.html" ] || website_deploy_template "$(ga_config_get .site.template_id)"`
  и `website_write_conf "$(ga_config_get .domain)"` (правило 5 из integration.md навыка).
  Самоподписанный сертификат (`$GOAMNEZIA_DIR/tls`, путь `GOAMNEZIA_TLS_DIR` тот же, что у
  `ga_site_tls_dir`) в архив **входит** (`tls/`), восстановление возвращает его на место с правами
  0700/0600 — иначе на чистом сервере `website_enable_site_selfsigned` создал бы новый сертификат с
  новым отпечатком, который агентам MCP пришлось бы доверить заново (проверка — раздел 11a
  `tests/test_actions_dryrun.sh`).
- `lib/selftest.sh`: можно добавить секцию «website: vhost из шаблона в dry-run» — сейчас это
  делает `tests/test_website_dryrun.sh`.
- Бот и панель: смена шаблона только через `--action=change-template --template=ID` (таймаут ≥ 180 с),
  каталог — `lib/catalog_tool.py` (`Catalog(...)`, `categories()`, `page(cat, page, 24)`,
  `search()`, `random()`, `short_key()` ≤ 64 байт; путь по умолчанию `/opt/goamnezia/templates_catalog.json`).

## 4. `bootstrap.sh` — список `FILES`

Сделано: все семь файлов режимов с сайтом — `lib/website.sh`, `lib/templates_lib.sh`, `lib/catalog_tool.py`,
`lib/nginx/goamnezia.conf.tpl`, `templates_catalog.json`, `lib/lang/ru.d/website.sh`, `lib/lang/en.d/website.sh` —
в обязательном `FILES`: без каталога режим с сайтом молча ставил бы сайт «404», а без шаблона vhost nginx не
собирается вовсе. Подкаталог `lib/nginx/` загрузчик создаёт сам (`mkdir -p` по `dirname` пути), файлы качаются по
SHA коммита ветки. Сверяет список `tests/check_bootstrap_files.py`.

## 5. Что проверять только на стенде (VPS с доменом, Ubuntu 24.04)

Правило владельца: сертификат выпускается **один раз**; повторные эксперименты —
`GOAMNEZIA_LE_STAGING=1` (браузеры и `website_check` такой сертификат не примут — `https_ok=false`,
это ожидаемо).

1. `website_enable_pro <домен>` на чистом сервере: nginx 1.24 принимает `listen 443 ssl http2`
   (на nginx ≥ 1.25.1 модуль пишет `http2 on;`), IPv6-слушатели появляются только при живом
   `/proc/net/if_inet6`; certbot идёт через webroot `/var/www/goamnezia-acme`; после выпуска —
   `certbot.timer` включён, deploy-hook лежит в `renewal-hooks/deploy/`; `certbot renew --dry-run`
   проходит.
2. Повторный `website_issue_cert` на живом сертификате — «не перевыпускаем», certbot не вызывается.
3. `website_check` снаружи: `dns_ok=true`, `https_code=200`, `verdict=ok`; с браузера — сайт,
   `https://домен/README.md` и `/package.json` — 404, `/.goamnezia_template_id` — 403.
4. Шаблоны по одному на источник (`h5up_*`, `sb_*`, `th_*`, `da_*`, `gh_*`) через
   `--action=change-template`; коды отказа (`download_failed` при отключённой сети).
5. Панель: `website_panel_path_set app-…` → `https://домен/app-…/` открывает панель с
   `X-Forwarded-Host`, без `X-Frame-Options`; `clear` → 404 сайта.
6. MCP: `website_mcp_location_on` → `POST https://домен/a-<secret>/mcp` доходит до `server.py --http`
   на 1986, чужой путь → 404 сайта, секрет не попадает в `access.log`.
7. `website_set_alt_svc on` → в ответе `Alt-Svc: h3=":443"; ma=86400`; браузер откатывается на TCP.
8. `website_disable_pro` (= `website_switch_site_mode lite_site`) → сайт, домен и сертификат на
   месте, снаружи ничего не изменилось, `certbot` не звался, vhost прежний; меняется только порт
   AmneziaWG (его двигает вызывающий). `website_disable_pro lite` → 80/443 закрыты; повторный
   `enable` — без certbot и без скачивания шаблона.
9. Запасной путь (домена нет) снаружи: `curl -I http://<IP>/` → 200 и страница шаблона (не редирект),
   `curl -kI https://<IP>/` → 200, `openssl s_client -connect <IP>:443 </dev/null | openssl x509
   -noout -fingerprint -sha256` совпадает с отпечатком из «Режим и сайт» и `status`; `nmap -sV
   -p 80,443 <IP>` показывает обычный nginx с сайтом; сканер Shodan/Censys увидит самоподписанный
   сертификат с CN = IP — как у миллионов хостов; `site-check` отдаёт `https_code=200`,
   `http_code=200`, `cert=selfsigned`, `verdict=ok`. `https://<IP>/README.md` → 404,
   `/.goamnezia_template_id` → 403. Переходы `lite → lite_site → pro → lite_site → lite` из меню и
   через `change-mode`: `docker ps` без пересозданий, ключи пользователей работают всё время.
10. Бэкап/восстановление: nginx-конфиг и `letsencrypt/` переезжают; после восстановления —
    `website_write_conf` и при пустом корне `website_deploy_template <id>`.

## 6. Проверки без сервера

```
/opt/homebrew/bin/bash tests/test_website_dryrun.sh        # 116 проверок, dry-run, временные каталоги
python3 -m unittest tests/test_templates_catalog.py         # 12 тестов каталога и catalog_tool.py
bash tests/run_static.sh                                    # bash -n, shellcheck, i18n, actions, bootstrap
```

## 7. Коды ошибок модуля (для бота, панели, MCP)

`invalid_domain` (rc 2), `no_domain`, `module_missing`, `nginx_install_failed`, `nginx_dir_failed`,
`certbot_install_failed`, `certbot_missing`, `invalid_conf_kind`, `nginx_template_missing`,
`nginx_render_failed`, `write_failed`, `nginx_backup_failed` (копию прежнего vhost снять негде —
работающий сайт не трогаем), `nginx_conf_invalid`, `nginx_reload_failed`,
`invalid_listen`, `invalid_toggle`, `invalid_path`, `invalid_mode` (rc 2), `config_write_failed`,
`acme_root_failed`, `cert_failed`, `cert_missing`, `renew_failed`, `catalog_missing`, `unknown_template` (rc 2),
`download_failed`, `no_index`, `app_shell`, `broken_assets`, `deploy_failed`, `tmp_failed`,
`openssl_missing`, `no_server_ip`, `tls_dir_failed`, `selfsigned_failed`,
`invalid_email` (rc 2 — `website_set_acme_email`), `low_disk` (места под клон шаблона меньше
`GA_SITE_MIN_DISK_MB`; проверяется ДО клона, чтобы человек видел «мало места», а не английскую
ошибку git).
Коды шаблона совпадают с `.last_error` навыка, чтобы тексты бота из goTelegram подошли без правок.

## 8. Отклонения от навыка `site-templates-library`

Пути, имена и видимые строки (`diff` с оригиналом — около двадцати изменённых строк, 3 добавленные директивы shellcheck и шапка):
`CATALOG_FILE` по умолчанию `../templates_catalog.json` (+ `GOAMNEZIA_CATALOG`), `WEBSITE_ROOT`
по умолчанию `GOAMNEZIA_SITE_ROOT`/`SITE_ROOT`, префиксы `mktemp` `goamnezia_*`, маркеры
`.goamnezia_template_id` / `.goamnezia_template_source`, три директивы `# shellcheck disable`
(SC2010, SC2034, SC2115 — код навыка, логика не тронута). В `catalog_tool.py` — только
`_default_path()`. Логика `_tpl_pick_index_dir`, `_tpl_page_ok`, `strip_template_service_files`,
`deploy_template_to_nginx` — байт в байт; отличаются в них только видимые строки. Пять сообщений
библиотеки (нет `index.html`, «старый сайт сохранён», «шаблон развёрнут» и два про `repo_url`)
раньше оставались русскими литералами «как в навыке» — и английский интерфейс отвечал в этих
местах по-русски (дефект 1a.18). Теперь они идут через словарь: ключи `templates_err_no_index`,
`templates_site_backup`, `templates_deployed`, `templates_err_repo_url`,
`templates_err_repo_url_bad` есть и в `lib/lang/{ru,en}.d/website.sh`, и в запасных таблицах самой
библиотеки (хост без i18n), поэтому переносимость навыка сохранена. Оттуда же убрана развилка
`if type tf` в `load_catalog`: `t`/`tf` библиотека определяет сама, когда их нет у хоста, а
английский литерал в ветке `else` был тем же ответом мимо словаря. Подписи авторов и лицензии в подвалах
шаблонов модуль не трогает (правило навыка: у 1 036 шаблонов ThemeWagon ссылка в футере — условие
использования).
