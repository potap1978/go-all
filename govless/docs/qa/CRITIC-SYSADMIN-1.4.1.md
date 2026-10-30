# goVLESS pro 1.4.1 (ветка test, 6dcf4f5) — критика «сисадмина»

Дата: 2026-09-22. Режим: только чтение кода, `bash -n` по всем sh (чисто), `python3 ast` по всем py (чисто). Ничего не менялось, к серверам не подключался. Строки указаны по текущему состоянию файлов в клоне.

Приоритеты: **P0** — потеря VPN/данных/безопасности без действий пользователя; **P1** — ломает установку/обновление/функцию при реалистичных условиях; **P2** — неверное поведение с обходом; **P3** — гигиена.

Пометка «уверенность»: *высокая* = следует из процитированного кода и семантики bash/systemd/HTTP, которые я проверил; *средняя* = зависит от поведения внешнего компонента (3X-UI, acme.sh, ядро), который я не запускал; *низкая* = предположение, дан способ проверки.

---

## Безопасность и доступность control-plane (демон / бот / Mini App)

### S-01 [P0] Lite-релей Mini App пропускает `caller:"local"` — полный RPC без авторизации по URL туннеля

**Где:**
- `phase-a/webapp/server.py:189-199` — тело запроса уходит в сокет демона **как есть**:
  ```python
  body = self.rfile.read(length) if length else b""
  ...
  status, resp_body, resp_ctype = _proxy_to_daemon(method="POST", upstream_path="/rpc", body=body, ...)
  ```
- `phase-a/govlessctl/auth.py:201-205` и `211-217` — для `caller=="local"` разрешён peer с uid `govless`, а `tg_id` берётся из тела **без проверки**:
  ```python
  else ("root", "govless")  # caller=local: root (CLI/admin) + govless (bot service acct)
  ...
  if caller == "local":
      tg_id = body.get("tg_id")
      ...
      return {"caller": "local", "tg_id": tg_id, "user": None, "peer": peer_info}
  ```
- `phase-a/systemd/webapp-frontend.service:16-17` — релей работает от `User=govless`, то есть попадает в этот allow-list.

**Сценарий:** Lite-режим с ботом (cloudflared quick tunnel). Любой, кто знает `https://xxx.trycloudflare.com` (URL уходит всем админам в кнопку «Open app», лежит в `/var/log/cloudflared.log`, легко пересылается), с любого хоста:
```
curl -s -X POST https://xxx.trycloudflare.com/api/rpc -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"panel.access_get","params":{},"caller":"local","tg_id":"1"}'
```
Демон видит peer uid=govless (это server.py), `caller=local` → контекст админа с произвольным `tg_id`. Дальше доступны все 37 методов: `panel.access_get` возвращает **логин и пароль панели открытым текстом** (панель слушает 0.0.0.0), `admin.invite` (добавить себя в админы Mini App/бота), `client.add`, `backup.restore`, `system.restart`, `cert.force_renew`; typed-confirm не защита — токен и ожидаемая строка выдаются тому же вызывающему (`_issue_confirm`, methods.py:1631-1642).

**Последствие:** полный захват панели 3X-UI и бота при знании URL туннеля; докстринг server.py («No auth here. The daemon validates Telegram initData HMAC») неверен для `caller=local`. В Pro этот путь закрыт случайно: nginx-воркер — `www-data`, его нет в списке `("root","govless")`.

**Уверенность:** высокая по механизму (чистый код); эксплуатируемость средняя (нужен URL, но он и есть единственный секрет).

**Как исправить:** (1) в `server.py` парсить JSON и **принудительно** ставить `body["caller"]="webapp"`, удалять `tg_id`, иначе 400; (2) в `auth.py` для `caller=local` разрешать uid `govless` только если peer-pid — процесс бота (или запускать релей и cloudflared от отдельного uid `govless-web`, не входящего в allow-list); (3) пин `test_regressions.sh:1123` («caller=local … include 'govless'») переписать под новую схему. Проверка: команда выше должна давать 401/403.

---

### S-02 [P1] Один общий rate-limit bucket на всех — DoS бота/Mini App из интернета одним `curl`-циклом

**Где:**
- `phase-a/govlessctl/daemon.py:136-137, 143-146`:
  ```python
  peer = request.transport.get_extra_info("peername") if request.transport else None
  addr = peer[0] if peer else "local"
  ...
  if len(bucket) >= _RL_RATE:
      return web.json_response(_err(None, 429, "rate limit exceeded — 60 req/min"), status=429, ...)
  ```
  Для UNIX-сокета `peername` = `''` (falsy) → **все** вызывающие (nginx, релей, бот, CLI) делят один ключ `"local"`. Лимит срабатывает **до** аутентификации (middleware).
- `phase-a/systemd/nginx/govless-webapp.conf:34-36` — `/api/rpc` открыт всему интернету без `limit_req`:
  ```
  location = /api/rpc {
      if ($request_method != POST) { return 405; }
      proxy_pass http://unix:/run/govlessctl.sock:/rpc;
  ```

**Сценарий:** Pro, известен только домен. `for i in $(seq 70); do curl -s -o /dev/null -X POST https://домен/api/rpc -H 'Content-Type: application/json' -d '{}'; done` раз в минуту в фоне.

**Последствие:** бот отвечает «Action failed: rate limit exceeded», Mini App пустой, `tunnel_watcher` не может обновить кнопку — control-plane лежит, пока идёт флуд. Усугубляется легитимной нагрузкой: бот опрашивает `site.template_job` каждые 3 с (site.py:64, 485-487 → 20 req/мин), Mini App параллельно `startPoll` (app.jsx:1982), два админа — и 60/мин выбирается без атаки.

**Уверенность:** высокая.

**Как исправить:** ключ лимита по `X-Real-IP`/`X-Forwarded-For` для `caller=webapp` (nginx их ставит, snippet:39-40) и без лимита/отдельный ключ для локальных peers после проверки uid; в nginx `limit_req_zone $binary_remote_addr … rate=10r/m` на `/api/rpc`; healthz исключить из лимита.

---

### S-03 [P1] EXIT-trap чистит общие `/tmp/govless_*` файлы, а `govless-egress-probe.timer` запускает `govless.sh` каждые 10 с → ломает параллельную смену режима / API-сессию

**Где:**
- `govless.sh:33` — `trap cleanup_temp_files EXIT` (на каждый запуск, включая systemd-режимы, диспетчер на строке 1442 идёт **после** установки trap).
- `lib/common.sh:727` — `rm -f /tmp/govless_cookie.txt /tmp/govless_login_resp.json /tmp/govless_api_resp.json /tmp/govless_payload.json 2>/dev/null`
- `lib/egress.sh:935, 941-942` — `ExecStart=/usr/local/bin/govless --egress-probe`, `OnBootSec=20`, `OnUnitActiveSec=10`.
- `lib/xui_api.sh:105` — `local cookie_file="${1:-/tmp/govless_cookie.txt}"` — одна сессия панели на всех.
- `lib/mode_switch.sh:194 → 273 → 305` — логин, затем длинный шаг `obtain_ssl_certificate` (20-60 с), затем `api_delete_inbound "$inbound_id" || return 1`.

**Сценарий:** включён «Прокси для API Telegram» (таймер живёт). `govless → 1 → 4` Lite→Pro с новым доменом: пока certbot работает, пробник отрабатывает и его trap удаляет `/tmp/govless_cookie.txt`. `api_delete_inbound` шлёт POST без сессии → 3X-UI отвечает редиректом/401 → «HTTP 302» → переключение прерывается. То же при `install_pro` на медленном `get_server_ip` (4 × 10 с), при `check_client_online`, при запущенном из бота `--site-template-apply` (тот же trap в другом процессе).

**Последствие:** воспроизводимый отказ смены режима / автонастройки при активном egress; «мигающие» ошибки, которые невозможно диагностировать.

**Уверенность:** высокая по механизму (таймер + trap + фиксированные пути); код HTTP-ответа 3X-UI без сессии (302 или 401) — средняя, исход одинаков.

**Как исправить:** `cookie_file`/payload/resp через `mktemp` в переменных процесса; trap удаляет только то, что создал сам (`GOVLESS_TMP_OWNED`); для `--egress-probe/--egress-awg-*/--site-template-*` не ставить общий trap (диспетчер до `trap`).

---

### S-04 [P1] `backup.create` / `backup.restore` / `repair.run` в демоне ищут `lib/` там, где его нет → Mini App «Бэкап/Восстановить/Починить» всегда падает

**Где:** `phase-a/govlessctl/methods.py:1179-1181` (и 1218-1220, 1232-1234):
```python
"set -e; cd /root/goVLESS 2>/dev/null || cd /opt/govless 2>/dev/null || true; "
"source lib/common.sh 2>/dev/null || true; source lib/xui_api.sh 2>/dev/null || true; source lib/xui.sh 2>/dev/null && backup_govless"
```
Код goinstall/bootstrap лежит в `/opt/govless-installer` (`_installer_script()` это знает — methods.py:380-384), а в `/opt/govless` каталога `lib/` нет (install_phase_a.sh копирует только govlessctl/bot/webapp).

**Сценарий:** любая штатная установка через goinstall → Mini App → Backup → Create (или `govlessctl backup.create`).

**Последствие:** «backup failed: rc!=0» (stderr скрыт `2>/dev/null`); restore/repair аналогично. Второй слой: даже с правильным путём `restore_govless` начинает с `systemctl stop govless-bot govlessctl` (`lib/xui.sh:1144`) — это убьёт сам демон и его дочерний bash по cgroup, восстановление оборвётся на середине, а govlessctl останется остановленным (чистый stop не перезапускается `Restart=on-failure`).

**Уверенность:** высокая.

**Как исправить:** `cd "$(dirname "$(_installer_script())")"`; restore запускать отделённо (`systemd-run --unit govless-restore …`) или запретить через RPC (в боте он уже «SSH-only»).

---

### S-05 [P1] `panel.creds_rotate` не обновляет креды в памяти демона → после ротации все write-RPC отвечают «login failed» до рестарта govlessctl

**Где:**
- `methods.py:1020-1023, 1058` — меняет пароль через `x-ui setting`, переписывает файл, `systemctl restart x-ui`, но `self.xui.username/password` не трогает.
- `xui_client.py:108, 159` — креды читаются один раз при старте: `self.creds = creds or load_credentials()`; `data = {"username": self.username, "password": self.password}`.
- `xui_client.py:181-182, 238-242` — `raise XuiError(401, f"login failed: …")`; в `_request` ретраится только `"3X-UI auth:"`, `login failed` → немедленный `raise`.

**Сценарий:** Mini App → Panel → «Rotate credentials» → подтвердить → затем любой `client.add/update/delete`, `inbound.toggle`.

**Последствие:** бот и Mini App теряют все операции записи (чтение из sqlite продолжает работать, поэтому выглядит как «частично живой»), пока кто-то не сделает `systemctl restart govlessctl`. То же после `recover_panel_login` из SSH при работающем демоне.

**Уверенность:** высокая.

**Как исправить:** в `_login` при `success=false` перечитать `load_credentials()` и повторить один раз; в `creds_rotate` сразу присвоить `self.xui.username/password`, `self.xui._logged_in_at = 0`.

---

### S-06 [P1] Демон ходит в панель по **публичному** URL с проверкой TLS → «SSH-only», NAT без hairpin и self-signed панель отрезают бот от 3X-UI

**Где:**
- `xui_client.py:110-116` — приоритет `URL=` из `/root/.govless_credentials`; `lib/xui.sh:709` пишет туда `URL=https://${host}:${XUI_PORT}${web_path_norm}` (host = домен или публичный IP).
- `xui_client.py:143-145` — `aiohttp.ClientSession(cookie_jar=..., timeout=timeout)` без `ssl=False` (bash-сторона использует `curl -sk https://127.0.0.1:…`).
- `methods.py:967, 980` — «SSH-only» = `webListen=127.0.0.1` + restart x-ui.

**Сценарий:** (а) Mini App → Panel → SSH-only (после исправления S-07) → демон продолжает стучаться на `https://домен:порт` → connection refused → 3 ретрая по 5 с → «3X-UI unavailable»; (б) VPS за 1:1 NAT (Oracle, часть Hetzner-облаков): соединение на собственный публичный IP не проходит; (в) Lite, у которого LE IP-cert не выпустился и стоит self-signed (`xui.sh:1475-1485`) — `ClientConnectorCertificateError`.

**Последствие:** все операции записи бота/Mini App ломаются; чтение (sqlite) живёт — «загадочные» ошибки.

**Уверенность:** высокая для (а) и (в), средняя для (б).

**Как исправить:** демон должен брать `https://127.0.0.1:{webPort}{webBasePath}` из x-ui.db (`_local_panel_url_from_db` уже есть, но стоит после `URL`), с `ssl=False` (или пиннингом сертификата) — как делает bash.

---

### S-07 [P1] «SSH-only» для панели — UPDATE без INSERT: на свежей установке строки `webListen` нет, панель остаётся публичной, а UI показывает SSH-only

**Где:**
- `methods.py:980` — `conn.execute("UPDATE settings SET value=? WHERE key='webListen'", (listen,))` — 0 строк, если ключа нет.
- Установщик никогда не пишет `webListen` (grep по `lib/*.sh`, `govless.sh` — пусто); 3X-UI создаёт строку только при сохранении настроек через UI/CLI.
- `methods.py:685-694` — при отсутствии строки чтение падает на маркер:
  ```python
  listen = settings.get("webListen")
  if listen is None:
      listen = ""
      if os.path.exists(PANEL_ACCESS_FILE): listen = open(PANEL_ACCESS_FILE).read().strip()
  mode = "ssh-only" if listen in ("127.0.0.1", "localhost", "::1") else "public"
  ```

**Сценарий:** свежая установка → бот/Mini App → Panel access → SSH-only → «LOCKOUT-RISK PROCEED».

**Последствие:** оператор уверен, что панель закрыта (бейдж «ssh-only», x-ui перезапущен), а она по-прежнему на `0.0.0.0:<порт>`. Ровно та ошибка, которую комментарий на 681-684 объявляет исправленной.

**Уверенность:** средняя-высокая — зависит от того, что 3X-UI не создаёт строку `webListen` по умолчанию; проверяется одной командой sqlite.

**Как исправить:** `DELETE … WHERE key='webListen'; INSERT …` (как `configure_panel_tls`, `xui.sh:1499-1507`), после рестарта проверять `ss -ltn` и возвращать реальный listen.

---

### S-08 [P2] Fallback `apt_install` отключает проверку подписей для **всех** репозиториев

**Где:** `lib/common.sh:360-363`:
```bash
DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=300 install -y -qq \
    -o Acquire::AllowInsecureRepositories=true \
    -o APT::Get::AllowUnauthenticated=true \
    "$@" >/dev/null 2>&1
```
Срабатывает при **любой** ошибке первой попытки (сеть, лок, зависимости), не только при «битом стороннем репо».

**Сценарий:** сервер в сети с активным вмешательством (это заявленная среда продукта — RU, DPI); зеркала Ubuntu по умолчанию `http://`. Атакующий срывает первую попытку и подсовывает неподписанный Release/Packages → apt ставит его пакеты (nginx, certbot, python3-venv) от root.

**Последствие:** RCE от root при установке/обновлении. Плюс BBR/sysctl тут ни при чём — это чистый supply-chain.

**Уверенность:** высокая по коду; вероятность атаки средняя.

**Как исправить:** fallback только с `-o Acquire::AllowInsecureRepositories=true` для *конкретного* сломанного источника (`-o Dir::Etc::SourceParts=/dev/null`, временно отключить `sources.list.d/*` третьих сторон), никогда `AllowUnauthenticated` глобально.

---

### S-09 [P2] `www-data` в группе `govless`: читает `bot.env` (BOT_TOKEN, вечно живой `ADMIN_CLAIM_TOKEN`) и пишет в `state.db`

**Где:**
- `phase-a/systemd/install/install_govless_user.sh:39-43` — `usermod -a -G "$GROUP_NAME" "$web_user"` для `www-data`/`nginx`; `68-69` — `chown root:govless; chmod 0640 bot.env`.
- `phase-a/govlessctl/state_db.py:101-106` — `os.chown(f, -1, gid)`; `os.chmod(f, 0o660)` для state.db/-wal/-shm.
- `methods.py:1125` — `expected = self.bot_env.get("ADMIN_CLAIM_TOKEN", "")`; мастер (`govless.sh:935`) пишет `ADMIN_IDS` и **никогда не потребляет** claim-токен → он валиден бессрочно.

**Сценарий:** любая уязвимость/RCE в контексте nginx-воркера (сторонние шаблоны из публичных git-репозиториев, будущий php-fpm) → `cat /etc/govless/bot.env` → токен бота; `sqlite3 /var/lib/govless/state.db "insert into govless_admins…"` → админ Mini App; `/admin <ADMIN_CLAIM_TOKEN>` в чате → primary admin.

**Последствие:** горизонтальная эскалация www-data → полный контроль бота и панели. Группа нужна только ради сокета.

**Уверенность:** высокая по фактам, средняя по эксплуатируемости.

**Как исправить:** доступ nginx к сокету через `setfacl -m u:www-data:rw /run/govlessctl.sock` (демон после bind), убрать www-data из группы; `state.db` 0600 govless; `bot.env` 0600 root (бот получает env через `EnvironmentFile=`, демон — root, файл читать группе не нужно); после успешного мастера обнулять `ADMIN_CLAIM_TOKEN` или помечать `admin_claimed`.

---

### S-10 [P2] Смена режима: удаление inbound через API 3X-UI стирает `client_traffics`; «трафик сохраняется» — неверно, откат его тоже не возвращает

**Где:**
- `lib/mode_switch.sh:11-12, 81-82` — обещание: `# Preserves: client UUIDs, emails, subIds, traffic stats (via email match in x-ui.db client_traffics…)`, `# ── Delete inbound via 3X-UI API (preserves traffic stats by email)`.
- `lib/mode_switch.sh:91` — `-X POST "${API_BASE}/panel/api/inbounds/del/${inbound_id}"`. В 3X-UI `InboundService.DelInbound` удаляет `client_traffics WHERE inbound_id=?` и `client_ips`, а `AddInbound` создаёт строки с нулями.
- `lib/mode_switch.sh:117-122` — snapshot только `SELECT * FROM inbounds`.

**Сценарий:** `1 → 4` в любую сторону.

**Последствие:** счётчики трафика всех клиентов обнуляются (лимиты `totalGB` начинают отсчёт заново — оплаченные квоты «дарятся»); при откате (`mode_switch_restore_inbound_snapshot`) строка inbound возвращается, а `client_traffics` — нет: у клиентов не считается трафик и не работают лимиты до пересоздания.

**Уверенность:** средняя — поведение `DelInbound` я знаю по исходникам 3X-UI, но не проверял на 3.x/2.9.4 вживую; проверка: `sqlite3 x-ui.db "select email,up,down from client_traffics"` до/после.

**Как исправить:** перед `api_delete_inbound` сохранять `client_traffics`/`client_ips` во временную таблицу и восстанавливать после `AddInbound` (по email), либо менять `stream_settings/settings` прямо в БД + restart x-ui без удаления inbound.

---

### S-11 [P2] acme.sh-cron (Lite): каждые ~6 дней `stop nginx → выпуск → start nginx → restart x-ui`; после Lite→Pro cron остаётся и продолжает бесполезные перезапуски

**Где:**
- `lib/xui.sh:1533-1534` — установка через `get.acme.sh` (ставит cron `acme.sh --cron`).
- `lib/xui.sh:1567-1575` — `--days 6 … --pre-hook "systemctl stop nginx …" --post-hook "systemctl start nginx …" --force`; `1590` — `--reloadcmd "systemctl restart x-ui"` (acme.sh запоминает reloadcmd и выполняет при каждом продлении).
- `lib/mode_switch.sh:366-372` — Lite→Pro переключает панель на доменный сертификат, но `acme.sh --remove -d <IP>` не вызывает.

**Сценарий:** Lite ≥ 6 дней; либо Lite→Pro и подождать неделю.

**Последствие:** в Lite раз в ~6 дней обрыв всех VLESS-сессий (restart x-ui = restart xray) и 5-10 с недоступности :80; в Pro после переключения — то же самое ради IP-сертификата, который никто не использует, плюс обрыв ACME-webroot nginx ровно в те секунды, когда мог идти certbot.

**Уверенность:** средняя-высокая (семантика acme.sh `--reloadcmd`/cron документирована; на стенде подтверждается `crontab -l`, `grep -i reloadcmd /root/.acme.sh/*/*.conf`).

**Как исправить:** в Lite→Pro (`switch_mode` ветка pro) — `acme.sh --remove -d "$server_ip" --ecc`; в Lite reloadcmd заменить на скрипт, который перезапускает x-ui только если `webCertFile` указывает на этот сертификат, и делать это в 04:00 (или ждать поддержки hot-reload).

---

### S-12 [P2] `configure_panel_tls … ""` не форсирует IP-ветку (`${3:-…}`) → после Pro→Lite панель сидит на доменном сертификате, а «Удалить только сайт» в Lite этот сертификат сносит без защиты

**Где:**
- `lib/xui.sh:1426` — `local domain="${3:-$(config_get domain 2>/dev/null || true)}"`; пустой третий аргумент (`mode_switch.sh:378`: `configure_panel_tls "${LANG_CODE:-en}" "$server_ip" ""`) в bash **не** отличается от отсутствующего → берётся `config.domain`, который при Pro→Lite не очищается. Проверено: `bash -c 'f(){ local d="${3:-fallback}"; echo "$d"; }; f a b ""'` → `fallback`.
- `lib/xui.sh:1230` — guard удаления сертификата только `if [ "$cur_mode" = "pro" ]`.

**Сценарий:** Pro → `1 → 4` (Lite) → `3 → 6 → 1` «DELETE SITE».

**Последствие:** (1) сразу после переключения URL панели `https://IP:port` показывается с сертификатом домена — браузер ругается, комментарий на 374-377 обещает обратное; (2) удаление сайта уносит `/etc/letsencrypt/live/<домен>`, на который всё ещё ссылаются `webCertFile/webKeyFile` панели → x-ui при рестарте не находит файлы (3X-UI откатывается на HTTP с ошибкой в логе; sub-сервер в Lite без TLS не затронут).

**Уверенность:** высокая для (1), средняя для (2) (реакция 3X-UI на отсутствующий cert не проверялась вживую).

**Как исправить:** `local domain; if [ $# -ge 3 ]; then domain="$3"; else domain=$(config_get domain ""); fi`; при Pro→Lite делать `config_set domain ""` (или `mode_domain`), guard в `remove_site_only` проверять, что на сертификат не ссылаются ни inbound, ни `settings.webCertFile`, ни `subCertFile`.

---

### S-13 [P2] Переустановка панели и прерванный мастер бота оставляют `govlessctl`/`govless-bot` остановленными до перезагрузки

**Где:**
- `lib/xui.sh:339-340` (свежий путь `install_3xui`): `systemctl stop govlessctl govless-bot 2>/dev/null || true` — обратного `start` в `install_pro/install_lite` нет.
- `govless.sh:960` — `if ! confirm "$(t bot_offer_setup)"; then log_info …; return 0; fi` — отказ от мастера = ничего не поднимается.
- `govless.sh:844` — `_bot_detect_admin` делает `systemctl stop govless-bot`, trap/восстановления при Ctrl-C или таймауте нет (подтверждает BM-21).

**Сценарий:** `3 → 6 → 2` «DELETE PANEL» → `1 → 1` установка → в конце «Настроить бота?» → 2 (бот уже настроен). Или мастер прерван Ctrl-C на шаге 2.

**Последствие:** бот и демон мертвы (`enabled`, но не `active`) до reboot или ручного `4 → 4`; Mini App «RPC недоступен».

**Уверенность:** высокая.

**Как исправить:** после успешного `install_3xui` — `systemctl start govlessctl govless-bot` если юниты существуют; в мастере `trap 'systemctl start govless-bot' RETURN/INT`.

---

### S-14 [P2] «Страховочный» бэкап перед миграцией копирует `x-ui.db` без WAL-checkpoint — ровно та ошибка, которую описывает собственный комментарий

**Где:** `lib/migrate.sh:43-44`:
```bash
[ -f "$GOVLESS_CONFIG" ] && cp -a "$GOVLESS_CONFIG" "$dir/config.json" 2>/dev/null
[ -f "$XUI_DB" ] && cp -a "$XUI_DB" "$dir/x-ui.db" 2>/dev/null
```
против `lib/xui.sh:1061-1063`: `# Common bug: tar czf x-ui.db without WAL checkpoint captures only the 4096-byte stub header (writes live in x-ui.db-wal)`.

**Сценарий:** любое будущее повышение `GOVLESS_SCHEMA` → `run_migrations` → `backup_state` → миграция ломает inbound → откат из `/opt/govless/backups/<ts>/x-ui.db` даёт пустую/старую БД.

**Последствие:** отсутствие реального отката = потеря клиентов при неудачной миграции.

**Уверенность:** средняя (зависит от `journal_mode` x-ui.db на конкретной версии 3X-UI; `ls /etc/x-ui/x-ui.db-wal` покажет).

**Как исправить:** `backup_state` → вызывать `backup_govless` (там checkpoint + -wal/-shm).

---

### S-15 [P2] `maybe_sync_version` записывает новую версию **до** redeploy phase-a → при сбое `install_phase_a.sh` демон/бот навсегда остаются на старом коде

**Где:** `govless.sh:1351-1354`:
```bash
config_set "version" "$GOVLESS_VERSION"
if _bot_installed; then
    ...
    bot_install_phasea || log_warning "$(t version_update_phasea_fail)"
```
`install_phase_a.sh` под `set -euo pipefail` ходит в PyPI (`pip install -r …`, строки 133-135) и apt.

**Сценарий:** обновление на RU-сервере, PyPI/apt недоступны или лок apt >15 мин → installer падает → версия в config уже новая → следующий запуск `govless` считает, что всё синхронизировано (строка 1343).

**Последствие:** рассинхрон версий SSH ↔ демон/бот без индикации; новые RPC-методы отсутствуют (то, что QA 1.4.0 BUG-02 уже ловил).

**Уверенность:** высокая.

**Как исправить:** писать `version` после успешного `bot_install_phasea` (или хранить `phasea_version` отдельно и сравнивать с `VERSION` в `methods.py:46`, который, кстати, до сих пор `"1.4.0"`).

---

### S-16 [P2] `listen [::]:80` во всех трёх nginx-конфигах: `nginx -t` проходит, `systemctl restart nginx` падает на ядрах с `ipv6.disable=1` → Pro-установка обрывается на ACME-шаге

**Где:** `lib/website.sh:53, 145, 180` — `listen [::]:80;`; `229-232`:
```bash
generate_nginx_temp_config "$domain"
if ! systemctl restart nginx 2>/dev/null; then
    log_error "$(t site_acme_restart_fail)"; return 1
```
`nginx -t` не биндит сокеты, поэтому ошибку «Address family not supported by protocol» даёт только реальный старт.

**Сценарий:** LXC/OpenVZ/шаблоны хостеров с IPv6, выключенным параметром ядра (не sysctl `disable_ipv6`, а `ipv6.disable=1` — при sysctl bind на `[::]` ещё работает).

**Последствие:** установка прерывается после развёртывания шаблона, сертификат не выпущен; Lite-заглушка тоже не стартует.

**Уверенность:** средняя (механизм точный, распространённость таких образов — низкая-средняя). Уточняет BM-07.

**Как исправить:** добавлять `listen [::]:80` только если `[ -f /proc/net/if_inet6 ]`.

---

## Bash-установщик и системная гигиена

### S-17 [P3] Первый, кто напишет боту в 90-секундное окно мастера, становится админом

**Где:** `govless.sh:867-873` — принимается первое сообщение любого не-бота: `if frm.get("id") and not frm.get("is_bot"): print(...); sys.exit(0)`.

**Сценарий:** username бота известен (печатается на экране, ищется в Telegram по имени) — гонка при первичной настройке.

**Последствие:** чужой tg_id в `ADMIN_IDS`. Низкая вероятность, но нулевая стоимость защиты.

**Уверенность:** высокая.

**Как исправить:** просить оператора отправить одноразовый код (печатать 6 цифр в SSH), принимать только сообщение с этим кодом.

---

### S-18 [P3] `RuntimeDirectory=govless` стирает `/run/govless` при каждом рестарте демона (tunnel.url, notify.queue, tunnel-health.state)

**Где:** `phase-a/systemd/govlessctl.service:12-13` — `RuntimeDirectory=govless`, `RuntimeDirectoryMode=0750`; данные в нём: `methods.py:41,45`, `govless-tunnel-url-extract:12`. systemd удаляет RuntimeDirectory при stop, если нет `RuntimeDirectoryPreserve=yes`.

**Сценарий:** любое обновление (`install_phase_a.sh:298-304` try-restart) или `system.restart` из Mini App в Lite.

**Последствие:** до 5 минут (пока `tunnel-health.timer` не вызовет экстрактор, строки 70-76) кнопка «Open app» без URL, непрочитанные уведомления теряются. Усиливает BM-27.

**Уверенность:** высокая.

**Как исправить:** `RuntimeDirectoryPreserve=yes` (каталог уже создаёт tmpfiles.d/govless.conf).

---

### S-19 [P3] «Удалить ВСЁ» оставляет хвосты, один из которых влияет на следующую установку

**Где:** `lib/xui.sh:1296-1398` не трогает: `/etc/nginx/snippets/govless-webapp.conf` (→ `generate_nginx_pro_config` при следующей Pro-установке снова добавит `include`, `/app/` будет 403 без phase-a), `/etc/sysctl.d/99-govless-bbr.conf`, `/etc/letsencrypt/renewal-hooks/deploy/govless-restart.sh`, cron `certbot renew` (`website.sh:300`), `/etc/tmpfiles.d/govless.conf`, `/root/cert/ip`, `/root/govless-backups` (внутри `bot.env` с токеном и `.govless_credentials`), `/var/log/cloudflared.log`, `/var/log/govless-tunnel*.log`, `/usr/local/bin/govless-{bot,tunnel-*,audit-prune}`, `cloudflared` + `/etc/apt/sources.list.d/cloudflared.list` + keyring, `/tmp/govless_xui_install.log` — в нём **логин/пароль панели открытым текстом** (`xui.sh:460-467`), файл живёт до следующей свежей установки.

**Уверенность:** высокая.

**Как исправить:** явный список в `remove_everything`; install-log удалять сразу после `extract_credentials`.

---

### S-20 [P3] Egress-xray под root, SOCKS5 без auth и API-порт доступны любому локальному пользователю; бинарник без проверки контрольной суммы

**Где:** `lib/egress.sh:239-240` — `"protocol":"socks","settings":{"udp":True,"auth":"noauth"}` на 127.0.0.1:10808 и dokodemo `api-in` на 10851; `360-374` — юнит без `User=`/hardening; `318-326` — скачивание релиза без `.dgst`.

**Последствие:** `www-data`/`govless` (см. S-09) выходят в интернет через платные VLESS-ключи оператора, переключают балансировщик (`xray api bo`), читают статистику. Компрометация GitHub-релиза = root.

**Уверенность:** высокая.

**Как исправить:** `User=govless-egress`, `auth: password` (креды в bot.env), проверять `.dgst` (sha256 есть в релизах Xray).

---

### S-21 [P3] AWG-нога и strict `rp_filter`: ответы через `kawgN` отбрасываются на «захардененных» образах

**Где:** `lib/egress.sh:459-468` (`awg-cfg.sh`): `ip rule add fwmark T table T`, `ip route replace default dev iface table T` — без `sysctl -w net.ipv4.conf.$iface.rp_filter=2`.

**Сценарий:** `net.ipv4.conf.all.rp_filter=1` (CIS-образы, часть панелей хостеров) → входящие ответы на kawgN с src=Telegram не проходят reverse-path (обратный маршрут — через eth0).

**Последствие:** нога «поднята», трафика нет — как в BM-41, но по другой причине; пробник потерь молчит (AWG не измеряется).

**Уверенность:** низкая-средняя (Ubuntu по умолчанию `rp_filter=2`). Проверка: `sysctl net.ipv4.conf.all.rp_filter` + `curl --socks5-hostname 127.0.0.1:10808` при выбранной AWG-ноге.

**Как исправить:** в `awg-cfg.sh` выставлять `rp_filter=2` для `$iface` и `all`.

---

### S-22 [P3] `bootstrap.sh`: токен (base64) в аргументах `git -c` виден в `ps`; `apt-get install git` без ожидания лока

**Где:** `bootstrap.sh:42-43` — `GIT_AUTH_ARGS=(-c "http.${REPO}.extraHeader=Authorization: Basic $AUTH_B64")` (аргументы командной строки читаются любым локальным uid через `/proc/*/cmdline`); `77` — `timeout 180 apt-get update … && timeout 180 apt-get install -y -qq git` без `apt_lock_wait` → на свежем Ubuntu (unattended-upgrades 10-15 мин) git не ставится, дальше «Failed to clone» с ложной подсказкой про токен.

**Уверенность:** высокая.

**Как исправить:** `GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=http.<repo>.extraHeader GIT_CONFIG_VALUE_0="Authorization: Basic …"` (env, не argv; git ≥ 2.31); цикл ожидания `fuser /var/lib/dpkg/lock-frontend`.

---

### S-23 [P3] `config_set` — read-modify-write без блокировки, temp в `/tmp` + `mv` через ФС

**Где:** `lib/common.sh:653-657`: `tmp=$(mktemp)`; `jq … "$GOVLESS_CONFIG" > "$tmp"`; `mv "$tmp" "$GOVLESS_CONFIG"`. Писатели: SSH-меню, фоновый job демона (`config_set "site_template"` в `_site_finalize`), таймеры.

**Последствие:** потерянная запись (например `site_template` или `fp_offer_done`) при совпадении; при tmpfs-`/tmp` `mv` = copy+unlink, не атомарно.

**Уверенность:** высокая (гонка), вероятность низкая.

**Как исправить:** `mktemp "${GOVLESS_CONFIG}.XXXXXX"` в том же каталоге + `flock` на `${GOVLESS_CONFIG}.lock`.

---

### S-24 [P2] `cert.force_renew` из Mini App без ограничений: несколько нажатий сжигают лимит LE (5 дублей/неделю); в Lite метод вообще не работает

**Где:** `methods.py:1083-1091` — `certbot renew --cert-name … --force-renewal` по typed-confirm (`RENEW <домен>`), без проверки «сколько дней до истечения»; `shutil.which("acme.sh")` не найдёт `/root/.acme.sh/acme.sh` (не в PATH) → в Lite либо «no ACME client», либо `certbot renew --cert-name <IP>` на несуществующем имени.

**Последствие:** оператор «на всякий случай» жмёт 3-5 раз → LE отвечает `too many certificates already issued` → штатное продление невозможно до 7 дней (дополняет BM-06 новым вектором).

**Уверенность:** высокая.

**Как исправить:** отказывать, если сертификат действителен > 30 дней (без `--force-renewal`), и ограничить 1 раз/сутки; для Lite вызывать `/root/.acme.sh/acme.sh --renew -d <IP> --ecc`.

---

### S-25 [P3] `admin.claim_first` — «клейм без токена» есть в dispatch-таблице, хотя ни бот, ни Mini App его не вызывают

**Где:** `methods.py:1139-1149, 1666`; `state_db.py:221-247`. Вызывающих в `phase-a/bot`, `app.jsx` нет (grep).

**Последствие:** мёртвый, но опасный метод: любой `caller=local` (сейчас — и релей из S-01) занимает слот primary admin без `ADMIN_CLAIM_TOKEN`, ломая гарантию пина `test_regressions.sh:615` («/start must not claim first admin without ADMIN_CLAIM_TOKEN»).

**Как исправить:** удалить метод и `admin_claim_first_atomic`.

---

### S-26 [P3] Зависимости venv не запинены и обновляются на каждом апдейте

**Где:** `phase-a/bot/requirements.txt` (`aiogram>=3.0,<4.0`, `aiohttp>=3.9.0`, `aiohttp-socks>=0.8.0`), `phase-a/govlessctl/requirements.txt` (`aiohttp>=3.9`); `install_phase_a.sh:132-135` — `pip install --upgrade pip` + `pip install -r` при каждом `maybe_sync_version`.

**Последствие:** любой breaking-минор aiogram 3.x или aiohttp прилетает в прод в момент обновления goVLESS, без тестов; невоспроизводимые сборки, supply-chain через PyPI (см. также S-08).

**Как исправить:** `pip-compile` с хэшами (`--require-hashes`), обновлять осознанно.

---

### S-27 [P3] SSH-меню на каждом запуске воссоздаёт legacy `/opt/govless/state.db` с другой схемой, хотя демон уже мигрировал БД в `/var/lib/govless`

**Где:** `lib/xui_api.sh:863-909` (`init_state_db`: `state_db="${GOVLESS_DIR}/state.db"`, своя схема `govless_user_bindings`, `govless_audit(ts, admin_tg, …, before, after)`), вызовы `govless.sh:57, 63, 121, 362`; `state_db.py:66-79` мигрирует legacy только если `/var/lib/govless/state.db` отсутствует.

**Последствие:** две «state.db» с разными схемами, обе попадают в бэкап (`xui.sh:1101-1102`), путаница при разборе инцидентов; функционально безвредно.

**Как исправить:** удалить `init_state_db` из bash (демон владеет схемой) или писать в `/var/lib/govless/state.db` ту же схему.

---

### S-28 [P3] Фоновый job смены шаблона живёт в cgroup демона: рестарт govlessctl посреди `rm -rf /var/www/html` оставляет пустой сайт

**Где:** `methods.py:1543-1552` — `asyncio.create_subprocess_exec(*cmd, …)` (дочерний процесс демона, `KillMode` по умолчанию = control-group); `lib/website.sh:438, 442` — `rm -rf "${WEBSITE_ROOT:?}"` … `cp -a "$template_dir/." "$WEBSITE_ROOT/"`.

**Сценарий:** админ запустил смену шаблона из бота, в это же время обновление (`install_phase_a.sh` try-restart) или `system.restart`.

**Последствие:** сайт-маскировка пустая (404 за fallback) до ручного восстановления из `/opt/govless/site-backups`; job в памяти демона потерян — бот показывает «Still running».

**Как исправить:** запускать через `systemd-run --unit=govless-site-job …` (отдельный scope) и хранить состояние job в файле.

---

### S-29 [P3] `install_pro` под `set -e`: `read -e -r domain`/`read -r email` без guard — EOF/Ctrl-D в промпте молча завершает весь установщик

**Где:** `govless.sh:15` (`set -euo pipefail`), `242` (`read -e -r domain`), `264` (`read -r email`) — в отличие от `read -r users_count_input || users_count_input=""` (313). `install_pro` вызывается из `case` (1322), errexit внутри активен.

**Последствие:** ansible/expect-прогоны и оборванная сессия завершают скрипт без сообщения; после `kill_port 443`/temp-конфига nginx состояние остаётся полу-настроенным (BM-04).

**Как исправить:** `read … || { log_error …; return 1; }` везде в install-потоке; при выходе из `install_pro` с ошибкой откатывать temp-vhost.

---

### S-30 [P3] `egress_status` выполняет `bot.env` как shell

**Где:** `lib/egress.sh:688` — `source "$BOT_ENV" 2>/dev/null` ради `BOT_PROXY`.

**Последствие:** значение с пробелом/кавычкой, введённое вручную, ломает вывод статуса; при чужой записи в файл (S-09) — выполнение кода от root в SSH-сессии оператора.

**Как исправить:** `_bot_env_get BOT_PROXY` (уже есть в govless.sh:691).

---

### S-31 [P3] `panel_access_get`/`panel.creds_rotate`: `/root/.govless_credentials` содержит пароль панели, Mini App показывает его любому админу; `x-ui setting -password` виден в `ps`

**Где:** `methods.py:476-509` (читает `PASSWORD=`), `1020-1023` (`create_subprocess_exec(xui_bin, "setting", "-username", new_user, "-password", new_pass)` — argv на секунды виден всем uid).

**Последствие:** любой приглашённый админ бота = пароль панели (уровень доступа выше, чем у RPC с typed-confirm); локальные пользователи могут поймать пароль в `/proc/*/cmdline`.

**Как исправить:** показывать пароль по отдельному typed-confirm и в аудит; для смены пароля использовать `x-ui setting` через stdin, если версия поддерживает, либо писать bcrypt в БД (3X-UI v3 хранит хэш).

---

## Что из BUG-MAP подтверждено/уточнено кодом

- **BM-07** — механизм подтверждён, но условие уже: не sysctl `disable_ipv6`, а параметр ядра `ipv6.disable=1` (S-16).
- **BM-21** — подтверждено: `_bot_detect_admin` стопит бота, отката при прерывании нет (S-13).
- **BM-27** — подтверждено и усилено `RuntimeDirectory` (S-18).
- **BM-44** — опровергнуто: `loss.log` ротируется `tail -n 400` (`egress.sh:918`).
- **BM-56** — по текущему коду бот `bot.env` не читает (только `EnvironmentFile=` systemd), 0600 root сам по себе бота не ломает; ломает только `chmod 600` из `restore_govless` (`xui.sh:1154`) — и то нет: systemd читает как root. Можно снять.
- **BM-41** — `strings bin/awg-linux-amd64` содержит токены `S3/S4/I1/I5`, т.е. tool знает параметры 2.0; по `amneziawg-go` вывод неоднозначен — нужна живая проверка `awg show kawg1`.
- **BM-40** — fallback на xray панели подтверждён (`egress_rebuild:560`, `egress_xray_bin:297-304`).
- **BM-30 (ufw)** — в коде действительно ни одного упоминания ufw/nft/iptables.

---

## 8 проверок для живого сервера (по убыванию важности)

1. **S-01 (Lite).** С внешней машины: `curl -s -X POST https://<tunnel>.trycloudflare.com/api/rpc -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"panel.access_get","params":{},"caller":"local","tg_id":"1"}'`. Ожидание при баге: JSON с `username`/`password`; после фикса — 401/403.
2. **S-02 (Pro).** `for i in $(seq 70); do curl -s -o /dev/null -X POST https://<домен>/api/rpc -H 'Content-Type: application/json' -d '{}'; done`, затем нажать любую кнопку бота. Ожидание при баге: «rate limit exceeded — 60 req/min» в ответе бота и `journalctl -u govless-bot`.
3. **S-04.** В Mini App → Backup → «Create» (или `govlessctl backup.create`). Ожидание при баге: `backup failed: rc!=0`; `ls /opt/govless/lib` → нет каталога.
4. **S-05.** Mini App → Panel → Rotate credentials → подтвердить → `govlessctl client.add --arg name=probe1`. Ожидание при баге: `ERROR 401: login failed`; `systemctl restart govlessctl` — снова работает.
5. **S-07.** `sqlite3 /etc/x-ui/x-ui.db "select key,value from settings where key='webListen'"` (пусто на свежей установке) → бот → Panel access → SSH-only → `ss -ltnp | grep x-ui`. Ожидание при баге: панель по-прежнему `*:<порт>`, а `govlessctl panel.access_get` говорит `ssh-only`.
6. **S-03.** Включить egress с одним ключом; `systemctl list-timers | grep egress-probe`; затем `touch /tmp/govless_cookie.txt; sleep 15; ls /tmp/govless_cookie.txt`. Ожидание при баге: файл исчез. Полный сценарий: `govless → 1 → 4` Lite→Pro с новым доменом → ошибка «HTTP 302/401» на шаге удаления inbound.
7. **S-10.** `sqlite3 /etc/x-ui/x-ui.db "select email,up,down from client_traffics"` → нагнать трафик → `1 → 4` → повторить запрос. Ожидание при баге: нули/пустая таблица для тех же email.
8. **S-11/S-12 (Lite ← Pro).** `crontab -l | grep acme`; `grep -i reloadcmd /root/.acme.sh/*/*.conf`; `sqlite3 /etc/x-ui/x-ui.db "select value from settings where key='webCertFile'"`. Ожидание при баге: cron есть, `reloadcmd='systemctl restart x-ui'`, а `webCertFile` после Pro→Lite всё ещё `/etc/letsencrypt/live/<домен>/…`; через ~6 дней `journalctl -u x-ui | grep -c Started` растёт.
