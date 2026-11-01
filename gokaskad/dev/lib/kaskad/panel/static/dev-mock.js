/* Kaskad — dev-mock.js: данные для проверки панели без сервера. В index.html НЕ подключается.

   Как включить: открыть панель с адресом   index.html?mock=1   (или http://127.0.0.1:1980/?mock=1 у настоящего
   сервера — тогда он только отдаёт файлы, а все ответы API рисует этот файл). app.js сам подгружает dev-mock.js,
   когда видит mock=1 и файл лежит рядом; нет файла — тихо работает с настоящим API. Статика без сервера:
       python3 -m http.server 8765 --directory lib/kaskad/panel/static   →   http://127.0.0.1:8765/index.html?mock=1

   Параметры адреса (к mock=1):
       sse=0        без SSE: панель работает на запасном опросе GET /api/overview
       still=1      «в сети» у устройств не меняется само (без него сборщик раз в такт с вероятностью 4 % переключает
                    каждое): проверки, которые ждут точные состояния («Мама, в сети 2 из 2»), не зависят от случая;
                    случайного сообщения-примера alert («Выход «Токио» снова работает») тоже нет — оно перекрывало
                    тост, который проверка ждёт
       lat=400      задержка каждого ответа, мс (по умолчанию 60)
       login=1      до входа все запросы отвечают 401; пароль «demo»; страница входа и «Выйти»
       rules=0      как будто GET /api/rules ещё нет (404) — раздел «Правила» показывает «появится в 3.2»
       lists=none   списков «Россия напрямую» нет: первое скачивание не удалось (раздел «Правила» — предупреждения)
       rf=legacy|full|need|none|uncovered  режим ⚠РФ (rules/rfgate.state, R4): legacy — работает как раньше (по
                    умолчанию: узел 3.3.x), full — подтверждено, need — выбрана, не действует, none — «всё через выход»,
                    uncovered — подтверждено, но в списке появилась новая организация (ФБК)
       ru=notloaded|unknown|nodomains  списки есть, но «Россия напрямую» не работает: сети России не загрузились в таблицу
                    ядра (ошибка и время повтора) / ещё не загружались (после перезагрузки) / работает без доменов —
                    у dnsmasq нет nftset (как scheme.ru_state)
       panel=0      как будто GET /api/panel нет
       empty=1      пустой узел: шлюз выключен, ни одного протокола, человека и выхода
       gw=off       шлюз выключен, но протоколы, люди и устройства на месте: службы протоколов остановлены (так и должно
                    быть, это не поломка); «Включить шлюз» запускает их (8 с «Сборщик запускается…», как первый круг
                    сборщика: у сервера live.starting), «Выключить» — снова останавливает
       none=…       убрать из данных части через запятую: exits, forwards, events, people (например none=exits,events —
                    ряд «список и пустое состояние» на «Обзоре»)
       fail=1       каждое изменение отвечает ошибкой 500 (проверка показа текста ошибки сервера)
       warn=1       GET overview возвращает предупреждение (плашка «Обратите внимание» на «Обзоре»)
       stale=1      сборщик молчит с самого начала (live.json старше 30 с): поток SSE шлёт {"full": true} без данных, у
                    GET overview live.fresh = false, running/sessions/online/счётчики — null, выходы — «нет данных»
                    (state nodata), плашка «Нет свежих данных сборщика»; в консоли KASKAD_MOCK.collector(false | true) —
                    остановить и вернуть сборщик на ходу
       exits=5      пятый выход — AmneziaWG 3.1 без своего имени (имя «AmneziaWG 3.1 · 198.51.100.23», как у живого
                    узла): ряд из пяти плиток со смешанными AWG и VLESS
       fw=long      ещё два проброса с длинными названиями: 56 знаков с пробелами и 64 без (предел API) — вёрстка карточек
                    «Пробросов» (tools/k3_layout_check.mjs); трафик у них постоянный и широкий (миллионы пакетов, ГБ):
                    название обязано сжаться при любом прогоне, не по случаю (R-333 круг 3, придирка 3)
       badexits=1   в пуле ещё ключ, который не разобран (kind «bad»), и повтор ключа (id e-…-6): его не настроить (409)
       rug=ok|last|empty|rudown|free|one  российские группы (T15 Q6, SPEC-groups §8): «Дом» — «Роутер» (AmneziaWG 2.0) → «Дача»
                    (VLESS) → с сервера напрямую, «Офис» — «Офисный роутер» (WireGuard) → с сервера напрямую; Мама и
                    Папа выбрали «Дом», ноутбук Папы — своё «Офис». ok — «Дом» через «Роутер»; last — выходы «Дома»
                    недоступны 34 мин, «Дом» на последнем шаге (с сервера напрямую); empty — «Дом» без ключей, «Офиса»
                    нет; rudown — как ok, но сервер не достаёт до российских адресов (live.policy.node_ru_offline); free — как ok,
                    но группы никому не выбраны; one — только «Дом» с двумя ключами («Офиса» нет): последняя российская
                    группа — «Удалить группу» с её ключами (8а, exits=drop); tier — как one, но «Роутер» и «Дача» в
                    одном уровне «Дома» со способом «Лучший отклик» (РФ-группы Ш1: устройства разложены по двум ключам)
                    Без rug — российских групп нет (узел прошлой версии после обновления)
       ord=1|manual|conn  порядок в группах (PLAN-order Ш5): у «Основной» уровень 1 — «Ключ 1…4» (11 / 95 / 12 / 12 мс,
                    «Лучший отклик»; ряды сервера: Р1 = 1, 3, 4, Р2 = 2), уровень 2 «Запасной» — прежние выходы; manual —
                    уровень 1 «По порядку (вручную)»; conn — «Основная» по соединениям (порядок не действует)
       apply=stale  первое «Применить» схемы отвечает 409 «настройки изменились» (data.reason «stale», как у сервера)
       layout=conflict  первое сохранение раскладки схемы отвечает 409 (другая вкладка сохранила раньше) — схема повторяет
       engine=legacy  пробросы ведёт старый движок (iptables): GET forwards {managed: false, forwards: []}, на «Обзоре» —
                    предупреждение сервера, добавить проброс и включить шлюз нельзя (409, как у сервера)
       github=none  ключа GitHub нет и ядра AmneziaWG на диске нет: AmneziaWG 1.5/2.0/3.1 не установлены, у их карточек
                    needs_key — «Ввести ключ GitHub»; установка без ключа — 409 {needs_key, key: "none"}. Ключ годится
                    любой вида ghp_… / github_pat_…; с «bad» внутри GitHub его «не принял», с «net» — «не ответил»
       link=expired|limit  как после редиректа сервера с одноразовой ссылки (GET /login): с login=1 — объяснение на экране
                    входа, без него — тост
       inst=sstp,ocserv  ещё и эти входы установлены (по умолчанию SSTP и OpenConnect не установлены)
       public=1     вкладка открыта из интернета (GET session: scheme https, открытый режим включён): выключение доступа
                    из интернета предупреждает, что связь пропадёт
       listener=proxy|public|local  каким путём открыта вкладка (GET session: listener); без него — по public=1
       pw=none      пароль панели не задан: «Доступ из интернета» придумывает его в своём окне (режим стрима, Б-1)
       vless=ru|tls|block  вход VLESS: ru — сервер в России (список российских сайтов маскировки, country RU); tls — режим
                    TLS со своим доменом узла (VLESS + TLS на 443, сайта маскировки и ключей Reality нет — «Сайт
                    маскировки» и «Новые ключи» отвечают 409); block — Reality, домен есть, но TLS нельзя (tls_block).
                    Без параметра — Reality, сервер не в России (DE), зарубежные сайты, домена нет (причина no_domain).
                    Причины Reality (SPEC-vless-tls-default §3.1, карточка и окно «Маскировка VLESS»): nocert, busy,
                    panel443, nosite, failed, ready, chosen, consent, legacy, pending (переход прерван). Цена сайта для
                    VLESS (Ц1/Ц2): site=on&vless=tls — выключение сайта; site=le&vless=nosite — включение
       site=…, certfail=…  домен и сертификат («Настройки → Домен и сайт») — список режимов в блоке «Домен и сайт» ниже
       health=…     лёгкое здоровье узла в плитке «Сервер» (SPEC-node-score A7): ok (по умолчанию), warn, crit (три находки,
                    «ещё замечаний», строка точки, «стало лучше»), wait, off, stale (находка «по последней проверке N мин
                    назад»), instant (шлюз выключен, сборщик молчит: плитка сама спрашивает GET /api/host/health — снимок
                    панели «без истории» и подсказка про бота на Python)
       intro=1      мастер только что завершён, знакомство с панелью не пройдено: GET intro due — тур по разделам
                    (без параметра — пройдено, тура нет; «Показать знакомство снова» в «Настройках» работает всегда)

   Что внутри: window.KASKAD_MOCK = { fetch(url, init), EventSource } — их app.js берёт вместо настоящих. Хранилище в
   памяти; POST/PUT/DELETE меняют его так, как менял бы сервер, и шлют событие state; события live (счётчики,
   процессор, живость выходов) идут раз в 2 с, изредка — alert. Формы ответов — те же, что у настоящего сервера
   (lib/kaskad/panel/api.py, scheme.py, rules/panel.py); живые данные — в формате live.json (docs/k3/SPEC-wave3.md §9.2):
   запись есть у КАЖДОГО устройства (не в сети — online:false, остальное null), у событий — id (мс) и exit/inbound.
   Поток SSE ведёт себя как у сервера: первое событие live — {"full": true, …} (полная картина), следующие — JSON
   merge patch (RFC 7386: изменённые поля, пропавшие — null, массив заменяется целиком); нет свежих данных —
   {"full": true} без них. Ошибки — {ok: false, error, code, data?}. Все ключи, пароли и QR-данные ниже выдуманные.

   Ожидания панели от сервера, на которых держится этот файл (если у сервера иначе — править надо app.js, API{…}):
       GET  overview            {ok, version, gen (меняется с каждым state), engine, gateway{enabled, host, users, devices,
                                online, problems[]}, inbounds[], exits{total, alive, items[]}, devices{id: live}|null,
                                forwards{count, packets, bytes}, events[], warnings[], live{ts, age, fresh, starting?}, now,
                                host}; без свежих данных у включённого шлюза exits.alive — null, у выходов state «nodata»
       GET  forwards            {ok, forwards:[{proto, port, to, to_port, name, origin, added}]}  (счётчики — из live)
       GET  gw                  api._gw_view: {ok, enabled, engine, net{base,dns}, host, host_auto, routes{default}, users,
                                devices, inbounds[] (как gw/inbounds), exits[] (как gw/exits), services{dns, xray}, problems[]}
       GET  gw/inbounds         {ok, inbounds:[{kind, title, engine, available, installed, enabled, ports[], running, sessions,
                                devices, warnings[], why, needs_key, record{}, options:[{key, title, value}]}]} (warnings, why — из live.json)
       GET  gw/people           {ok, users:[{id, name, route, enabled, added, devices:n, page}], devices:[{…, route_effective{via,fail,from},
                                user_name, live{online, inbound, addr, since, rx, tx}|null}]}
       GET  gw/exits            {ok, exits:[{id, index, tag, kind, mark, iface, version, host, name, where, named, enabled, problem?,
                                alive, detail, since, state (alive|dead|unknown|nodata), usable, text}]} — маршрута по умолчанию
                                тут нет (он в gw.routes.default)
       GET  scheme              {ok, version, layout_version, gateway, default_route, profiles, kinds, orphans, nodes, edges, live|null}
       PUT  scheme/layout       {version, positions, orders?} -> {ok, version}; 409 {data: {version, positions, orders}}
       POST scheme/apply        dry_run: {ok, dry_run, valid, version, noop, results[{i, type, ok, text, warnings[], error?, code?}],
                                changes[{op, what, text}], affected[], warnings[]}; иначе {ok, applied, results, changes, affected,
                                warnings, gateway_applied, graph}; 409 {data: {reason: "stale", version}} | {data: {results}}
       POST gw/devices/{id}/issue  {ok, device, name, kind, os, items:[{format, title, body, filename, notes[]}], available[], howto[]}
       POST gw/trace            {who: d-…|u-…|-, target, inbound?} -> {ok, query, who{…, inbound}, profile, route, match, addresses, verdict,
                                reason, summary, steps[{text, note, tone}], notes[], exit, exits[], highlight{nodes, edges, rows, devices,
                                inbound, profile, category, via, target, exit, balancer, exits} — ОДНА цепочка, как kaskad.gw.trace.chain}
                                (как kaskad.gw.trace: списки и «DNS» — маленькие, свои)
       GET  gw/offers?platform=   {ok, platform, offers:[{inbound, format, note, title, installed, enabled}], notes[]}
       PUT  gw/users|devices/{id}/enabled {enabled}   пауза человека и устройства
       GET|POST gw/users/{id}/page {action}  {ok, user, enabled, path, url, public, note, qr, qr_note} — страница человека
       GET|POST gw/users/{id}/sub {action}   {ok, user, enabled, path, url, public, note, devices, empty_note, interval} — подписка
       PUT  panel/password {password, current?, login?} · PUT panel/public {on} · PUT panel/settings {require_password}
            · POST panel/link {ttl} -> {url, expires_in, public}
       GET|PUT|DELETE github, POST github/check   {ok, set, source, ours, state, checked, error, repo} — ключа в ответе нет
       POST gw/exit-subs {url, auto} · POST gw/exit-subs/refresh | gw/exit-subs/{id}/refresh | gw/exit-subs/{id}/restore
            · PUT gw/exit-subs/{id} {auto} | {label, allow_duplicate?} (T13)
       PUT gw/users/{id}/name · gw/devices/{id}/name · gw/inbounds/{kind}/label · forwards/{proto}/{port}/name — переименование (T13)
            · DELETE gw/exit-subs/{id}[?keep_keys=1] — подписки выходов (GET gw/exits: subs[], у строк source); адрес с «fail»
            не скачивается; подписка по IP (s-4d5e6f) не обновляется — «не обновлялась 30 ч»
       GET|PUT settings/time {tz, ver}      {ok, tz, os_tz, now, offset, ntp, zones, warn?, ver} — часовой пояс узла (UI-A1);
            сцены: time=warn (пояс не из базы), time=ntpoff (часы не синхронизированы), time=none (404 — старое ядро)
       GET|PUT gw/activity/settings {keep, ver}  {ok, keep, ver, db, since, bytes, kept} — «Хранить историю активности»
            (policy_ops.activity_get / activity_set); act=none — 404 (старое ядро: заготовка «с версии …»)
       DELETE gw/activity[?dry=1]  №41 «Удалить историю активности» (policy_ops.activity_wipe), «Хранить» не меняется:
            dry — {ok, dry, bytes, since}; запись — {ok, freed, …настройки}; 409 activity_off (выключено), nosqlite
            (charts=0); после удаления сводка и ряды — без окон до него, открытое окно — пустое
       DELETE gw/groups/{gid}?exits=drop  8а: группа вместе с ключами (только российская; иначе 400 field «exits»):
            dry — {…, drop: [ids]}; запись — {ok, rev, moves, dropped, refs, uses, warnings}; без exits=drop последняя
            российская группа с ключами — 409 ru_last {exits}; её подписки — 409 need {uses: ["subs"]}
       GET backup/summary · POST backup/export {password, parts} · POST backup/preview|import {file, password} · POST backup/undo
            — копия настроек; пароль «wrong» — «Неверный пароль копии», файл с «blocker» — препятствие переноса */
(function (g) {
  "use strict";
  var Q = new URLSearchParams(location.search);
  // host=<IPv4> — адрес этого сервера в моке (по умолчанию 203.0.113.5): страж «Режима стрима» (tools/k3_stream_check.mjs)
  // берёт другой, чтобы адрес сервера не совпадал с образцом в поле «Адрес для клиентов» (тоже 203.0.113.5); host=none — без адреса
  var SRV = /^\d{1,3}(\.\d{1,3}){3}$/.test(Q.get("host") || "") ? Q.get("host") : "203.0.113.5";
  var LAT = Math.max(0, parseInt(Q.get("lat") || "60", 10) || 0);
  var USE_SSE = Q.get("sse") !== "0";
  var STILL = Q.get("still") === "1";                  // «в сети» устройств не переключается само (проверки)
  var NEED_LOGIN = Q.get("login") === "1";
  var FAIL = Q.get("fail") === "1";
  var HAS_RULES = Q.get("rules") !== "0";
  var RF = Q.get("rf") || "legacy";
  var HAS_PANEL = Q.get("panel") !== "0";
  var TIME_SCENE = Q.get("time") || "";                 // warn | ntpoff | none — «Настройки › Время»
  var INTRO_DUE = Q.get("intro") === "1";
  var EMPTY = Q.get("empty") === "1";
  var HEALTH = ["ok", "warn", "crit", "wait", "off", "stale", "instant"].indexOf(Q.get("health")) >= 0 ? Q.get("health") : "ok";
  var GW_OFF = Q.get("gw") === "off" || HEALTH === "instant";      // health=instant: узел без шлюза — сборщика нет
  var NONE = (Q.get("none") || "").split(",");
  var BAD_EXITS = Q.get("badexits") === "1";
  var FIVE_EXITS = Q.get("exits") === "5";
  var RUG = ["ok", "last", "empty", "rudown", "free", "one", "tier"].indexOf(Q.get("rug")) >= 0 ? Q.get("rug") : "";
  var RU_HOME = "g-d0d0d0", RU_OFFICE = "g-0ff1ce", RU_X = ["e-a0a0a0a001", "e-a0a0a0a002", "e-a0a0a0a003"];
  var ORD = ["1", "manual", "conn"].indexOf(Q.get("ord")) >= 0 ? Q.get("ord") : "";
  var ORD_X = ["e-0d0d0d0d01", "e-0d0d0d0d02", "e-0d0d0d0d03", "e-0d0d0d0d04"];
  var STALE_TEXT = "Нет свежих данных сборщика (kaskad-gw-collect): состояние протоколов и выходов, сессии и счётчики неизвестны.";
  // как exits.NO_DATA: сборщик молчит на включённом шлюзе — состояние выходов тоже неизвестно (T-G1b, N2)
  var NO_DATA_TEXT = "нет данных: сборщик (kaskad-gw-collect) не присылает сводок, проверок выходов нет";
  var loggedIn = !NEED_LOGIN;
  var LEGACY = Q.get("engine") === "legacy";
  var NO_KEY = Q.get("github") === "none";
  var FROM_NET = Q.get("public") === "1";
  // mip=1 — «Адреса сервера» (PLAN-multi-ip Ш2, Л3): у сервера SRV и внешний адрес облака с NAT MIP_NAT;
  // IPv4 для клиентов не из них — отказ 409 address_not_here, как api._known_host (без параметра мок принимает любой)
  var MIP = Q.get("mip") === "1", MIP_NAT = "198.51.100.61";
  // каким путём открыта вкладка (GET session: listener): listener=proxy — домен через nginx, иначе по public=1
  var LISTENER = /^(local|public|proxy)$/.test(Q.get("listener") || "") ? Q.get("listener") : (FROM_NET ? "public" : "local");
  var VLESS_MODE = Q.get("vless") || "";
  // VLESS: тексты макета на языке страницы, причины Reality и «исправить» (vlInit, vlView — у маршрутов VLESS ниже)
  var VL_EN = Q.get("lang") === "en";
  var ML = function (ru, en) { return VL_EN ? en : ru; };
  var VL_DOM = "vpn.example.net";
  var VL_FORCED = ["no_domain", "no_cert", "busy_443", "no_site", "tls_failed"];
  var VL_FIXES = { no_domain: "domain", no_cert: "cert", busy_443: "free_443", panel_443: "panel_port", no_site: "site_on",
    tls_failed: "mode_tls", ready: "mode_tls", chosen: "mode_tls", consent_bad: "consent", legacy: "mode_tls" };
  var VL_FIX_T = { domain: ML("Добавьте домен: Настройки → Домен и сайт", "Add a domain: Settings → Domain and site"),
    cert: ML("Выпустите сертификат домена", "Issue the domain certificate"), free_443: ML("Освободите 443", "Free 443"),
    panel_port: ML("Переведите открытый режим панели на свой порт: Настройки → Панель", "Move the panel’s open mode to its own port: Settings → Panel"),
    site_on: ML("Включите сайт на домене — VLESS перейдёт на TLS", "Turn on the site on the domain — VLESS switches to TLS"),
    mode_tls: ML("Перейти на TLS: gw vless mode tls", "Switch to TLS: gw vless mode tls"), consent: ML("Подтвердите Reality или перейдите на TLS", "Confirm Reality or switch to TLS") };
  var vlPl = function (n) { return VL_EN ? (n === 1 ? " device" : " devices") : ([" устройство", " устройства", " устройств"][(n % 10 === 1 && n % 100 !== 11) ? 0 : (n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 10 || n % 100 >= 20)) ? 1 : 2]); };
  // как сервер (auth.tunnel): вкладка из SSH-туннеля — админ без пароля, пока не включён открытый режим или «пароль и в
  // туннеле»; иначе нужен вход (POST login) — via "password"
  var via = NEED_LOGIN ? null : "tunnel";
  var authed = function () { return via === "tunnel" ? !(db.panel.public || db.panel.require) : !!via; };
  var nowS = function () { return Math.floor(Date.now() / 1000); };
  var rid = function (p, n) { var s = ""; var a = "0123456789abcdef"; for (var i = 0; i < n; i++) s += a[Math.floor(Math.random() * 16)]; return p + s; };
  var clone = function (o) { return JSON.parse(JSON.stringify(o)); };

  // ── Данные ────────────────────────────────────────────────────────────────
  var INBOUND_DEFS = [
    ["wg", "WireGuard", "wireguard", ["51820/udp"], true],
    ["awg1", "AmneziaWG 1.5", "amneziawg-go", ["31871/udp"], true],
    ["awg2", "AmneziaWG 2.0", "amneziawg-go", ["48445/udp"], true],     // 48445/udp — самый широкий порт живой проверки (T-M6-qs330b №13)
    ["awg3", "AmneziaWG 3.1", "amneziawg-go", ["51911/udp"], true],
    ["l2tp", "L2TP/IPsec", "accel-ppp + strongSwan", ["500/udp", "4500/udp", "1701/udp"], true],
    ["pptp", "PPTP", "accel-ppp", [], true],
    ["sstp", "SSTP", "accel-ppp", [], true],
    ["ikev2", "IKEv2", "strongSwan", ["500/udp", "4500/udp"], true],
    ["openvpn", "OpenVPN", "openvpn", [], true],
    ["ocserv", "OpenConnect", "ocserv", [], false],
    ["socks5", "SOCKS5", "xray", ["1080/tcp"], true],
    ["vless", "VLESS", "xray", ["4443/tcp"], true],
    ["hy2", "Hysteria2", "xray", ["443/udp", "20000–20099/udp"], true]
  ];
  // входы xray шлюза (model.XRAY_KINDS): без адресов, путь решает xray — пул: любой живой выход («any»)
  var XRAY_KINDS = ["socks5", "vless", "hy2"];
  // сайты маскировки VLESS Reality (inbounds/vless.py MASKS) — два списка по стране сервера (mask_list "int" | "ru");
  // [sni, название, tls13 — ответит ли «с узла» по TLS 1.3 (ya.ru в макете — нет), h2]
  var VLESS_MASKS_BY = {
    int: [["www.microsoft.com", "Microsoft", true, true], ["dl.google.com", "Google (загрузки)", true, true], ["www.apple.com", "Apple", true, true],
      ["www.samsung.com", "Samsung", true, true], ["www.nvidia.com", "NVIDIA", true, false], ["addons.mozilla.org", "Mozilla", true, true]],
    ru: [["vk.com", "ВКонтакте", true, true], ["www.ozon.ru", "Ozon", true, true], ["www.wildberries.ru", "Wildberries", true, true], ["ya.ru", "Яндекс", false, false]]
  };
  var VLESS_MASKS_ALL = VLESS_MASKS_BY.int.concat(VLESS_MASKS_BY.ru);
  var db;
  function freshDb() {
    var d = {
      gen: 1, seq: 100, version: "3.3.0-mock",
      gw: { enabled: !EMPTY && !GW_OFF && !LEGACY, host: EMPTY || Q.get("host") === "none" ? "" : SRV, host_auto: true, net: { base: "10.77.0.0/16", dns: "10.77.0.1" }, route: { via: "pool", fail: "direct" } },   // как на сервере владельца O: пул, при отказе — напрямую
      inbounds: {}, users: [], devices: [], forwards: [], exits: [], events: [], subs: [], subNext: 1, snapshot: false,
      // как kaskad.rules.ops.status(): devices, users, ru_direct, default_applied досчитывает GET /api/rules
      rules: { default: "ru_direct", block_ads: true, block_torrent: false,
        lists: { version: "20261004T094210Z", label: "от 2026-10-04 09:42:00 UTC (geoip 202610040942)", created: "2026-10-04T09:42:10Z", geoip: { date: "2026-10-04T09:42:00Z", tag: "202610040942" }, kernel: { from: "geoip", ranges: 15012, cidrs: 35604, v6: 2210, wide: 0 }, fallback: false },
        groups: { block: 54210, force: 640, direct: 3120, missing: [] },
        last: { at: new Date(Date.now() - 7200e3).toISOString().replace(/\.\d+Z$/, "Z"), ok: true, changed: false, warnings: [] },
        table: { loaded: true, wanted: 7, state: { ver: 7, ok: true } },
        attribution: "Списки: RoscomVPN (hydraponique, github.com/hydraponique). Домены (geosite) — лицензия MIT; сети России (geoip) собраны из GeoLite2 (MaxMind), DB-IP Lite (CC BY 4.0, db-ip.com) и IPinfo. Запасной список сетей — runetfreedom/russia-blocked-geoip (GPL-3.0)." },
      look: null, calls: 0,
      // сборщик молчит (stale=1 или KASKAD_MOCK.collector(false)): live.json старше 30 с — живых данных нет
      stale: Q.get("stale") === "1" || HEALTH === "instant",
      layout: { version: 0, positions: {}, orders: {} }, layoutConflict: Q.get("layout") === "conflict",
      applyStale: Q.get("apply") === "stale",
      // доступ к панели (GET panel): открытый режим при login=1 / public=1, пароль задан
      panel: { public: NEED_LOGIN || FROM_NET, port: NEED_LOGIN || FROM_NET ? 43125 : 0, password: Q.get("pw") !== "none", pw: "demo", login: "admin", require: false },
      // ключ GitHub — только его состояние, как отдаёт сервер; ядра AmneziaWG на диске нет при github=none
      github: NO_KEY ? { set: false, source: null, state: "none", checked: null, error: "" }
        : { set: true, source: "bot", state: "ok", checked: new Date(Date.now() - 86400e3).toISOString().replace(/\.\d+Z$/, "Z"), error: "" },
      awgCore: !NO_KEY, pages: {}           // subs — выше ([]): второй ключ subs: {} затирал его, и пустой узел (empty=1) падал на «Выходах»
    };
    // ⚠РФ: согласие (rf_consent) и метка «работало до обновления» (rf_legacy)
    d.rf = { consent: RF === "full" || RF === "uncovered" ? { at: new Date(Date.now() - 86400e3).toISOString().replace(/\.\d+Z$/, "Z"), via: "panel", orgs: ["meta"] } : null, legacy: RF === "legacy", uncovered: RF === "uncovered" ? ["fbk"] : [] };
    if (RF === "none") d.rules.default = "all_exit";
    if (Q.get("lists") === "none") {
      d.rules.lists = null; d.rules.groups = null; d.rules.table = { loaded: null, wanted: 0, state: {} };
      d.rules.last = { at: new Date(Date.now() - 600e3).toISOString().replace(/\.\d+Z$/, "Z"), ok: false, changed: false, error: "geoip: GitHub не ответил за 30 с", kept: null };
    }
    var inst = NO_KEY ? { wg: 1, l2tp: 1, pptp: 1, ikev2: 1, socks5: 1, vless: 1, hy2: 1 } : { wg: 1, awg1: 1, awg2: 1, awg3: 1, l2tp: 1, pptp: 1, ikev2: 1, socks5: 1, vless: 1, hy2: 1 };
    // inst=sstp,ocserv — ещё и эти входы установлены (снимки карточек протоколов с переключателем сертификата)
    (Q.get("inst") || "").split(",").forEach(function (k) { if (k) inst[k] = 1; });
    INBOUND_DEFS.forEach(function (x) {
      var on = !EMPTY && inst[x[0]];
      // warnings — что адаптер сказал в круге сборщика, why — почему включённый вход не работает (оба приходят из live.json)
      d.inbounds[x[0]] = { kind: x[0], title: x[1], engine: x[2], available: x[4] || !!on, installed: !!on, enabled: !!on && x[0] !== "ikev2", ports: on ? x[3] : [], running: !!on && d.gw.enabled, sessions: 0, options: x[0] === "l2tp" ? { plain: false } : (x[0] === "socks5" ? { udp: true } : (x[0] === "hy2" ? { obfs: true } : {})), warnings: [], why: null };
    });
    // запись входа Hysteria2, как её отдаёт GET gw/inbounds (_public_record + ops.hy2_show, note — справка про UDP: без секретов; домена нет)
    d.inbounds.hy2._record = { enabled: true, port: 443, hop: "20000-20099", obfs: true, cert: "domain", cert_mode: "self",
      congestion: "bbr", pin: "3f1c5a0e9b7d2c4f6a8e1b3d5c7f9a0b2d4e6f8a1c3e5b7d9f0a2c4e6b8d0f1a", domain: "", domain_ready: false,
      ports_view: [{ proto: "udp", port: "443" }, { proto: "udp", port: "20000–20099" }],
      warnings: ["Без домена — самоподписанный сертификат. Часть приложений проверяет только «небезопасно: да» и примет любой сертификат. Надёжнее — домен."], note: "Hysteria2 работает по UDP (QUIC). Если в мобильной сети не подключается — используйте VLESS." };
    // запись входа VLESS, как её отдаёт GET gw/inbounds (_public_record: без закрытого ключа)
    // security "reality" | "tls", mask_list и country — по стране сервера; domain и tls_block — только когда есть (vless=…)
    var vru = VLESS_MODE === "ru", vsni = vru ? "vk.com" : "www.microsoft.com";
    d.inbounds.vless._record = { enabled: true, port: 4443, sni: vsni, dest: vsni + ":443", mask: "list", fp: "chrome",
      mask_check: { ok: true, tls: "TLSv1.3", alpn: "h2", h2: true, why: "", at: new Date(Date.now() - 3 * 86400e3).toISOString().replace(/\.\d+Z$/, "Z") },
      short_id: "6ba85179e30d4fc2", pub: "Gh7Kq2mVx0bQp5LrT9sWnE3yJcA8dUfZ1iHoXkM4vR0",
      security: "reality", mask_list: vru ? "ru" : "int", country: { code: vru ? "RU" : "DE", source: "ipinfo" } };
    if (VLESS_MODE === "tls") {
      Object.assign(d.inbounds.vless._record, { security: "tls", domain: "vpn.example.net", port: 443 });
      if (d.inbounds.vless.installed) d.inbounds.vless.ports = ["443/tcp"];
    } else if (VLESS_MODE === "block") {
      Object.assign(d.inbounds.vless._record, { domain: "vpn.example.net", tls_block: "на 443 в nginx есть чужой сайт" });
    }
    vlInit(d.inbounds.vless._record);
    if (d.gw.enabled) {                       // на включённом шлюзе один вход «упал» — для проверки показа поломки; при gw=off службы стоят, и это норма
      d.inbounds.awg1.running = false;
      d.inbounds.awg1.why = "служба kaskad-gw-awg1.service завершилась с ошибкой (код 1)";
      d.inbounds.awg3.warnings = ["откат на amneziawg-go: модуль ядра не загружен для ядра 6.8.0-45-generic"];
    }
    if (!EMPTY) {
      // как public() + usable + text у настоящего GET gw/exits: index — номер строки пула (с 1), tag, mark, iface
      d.exits = [
        Object.assign(exit("e-41a19b9cbe", 1, "awg", "2.0", "vienna.example.net", "Вена", "alive", "отвечает", nowS() - 86400), { _hs: 40 }),
        exit("e-7c2d8e0f13", 2, "vless", "", "hel.example.net", "Хельсинки", "alive", "xray: выход работает", nowS() - 5400),
        Object.assign(exit("e-9aa1b2c3d4", 3, "awg", "3.1", "tokyo.example.net", "Токио", "dead", "нет ответа", nowS() - 600), { _hs: 640, _silent: 625, fails: 3 }),
        exit("e-0f0f0f0f0f", 4, "vless", "", "new.example.net", "Новый ключ", "unknown", "ещё не проверялся", null)
      ];
      if (ORD) d.exits.push(exit(ORD_X[0], 5, "awg", "1.0", "k1.example.net", "Ключ 1", "alive", "отвечает", nowS() - 86400),
        exit(ORD_X[1], 6, "awg", "2.0", "k2.example.net", "Ключ 2", "alive", "отвечает", nowS() - 86400),
        exit(ORD_X[2], 7, "awg", "2.0", "k3.example.net", "Ключ 3", "alive", "отвечает", nowS() - 86400),
        Object.assign(exit(ORD_X[3], 8, "awg", "", "k4.example.net", "Ключ 4", "alive", "отвечает", nowS() - 86400), { wg: true }));
      if (FIVE_EXITS) d.exits.push(Object.assign(exit("e-c6a51e0023", 5, "awg", "3.1", "198.51.100.23", "AmneziaWG 3.1 · 198.51.100.23", "alive", "отвечает", nowS() - 7200), { _hs: 25 }));
      if (RUG && RUG !== "empty") {           // российские выходы (T15): свой файл, тег «gr-», роль ru
        var down = RUG === "last", n0 = d.exits.length;
        d.exits.push(ruExit(exit(RU_X[0], n0 + 1, "awg", "2.0", "home.example.com", "Роутер", down ? "dead" : "alive", down ? "нет ответа" : "отвечает", nowS() - (down ? 2040 : 86400))),
          ruExit(exit(RU_X[1], n0 + 2, "vless", "", "dacha.example.com", "Дача", down ? "dead" : "alive", down ? "нет ответа" : "xray: выход работает", nowS() - (down ? 2160 : 7200))));
        if (RUG !== "one" && RUG !== "tier") d.exits.push(Object.assign(ruExit(exit(RU_X[2], n0 + 3, "awg", "", "office.example.com", "Офисный роутер", "alive", "отвечает", nowS() - 3 * 86400)), { wg: true }));
      }
      if (BAD_EXITS) {                        // ключ не разобран и тот же ключ, что в строке 1, ещё раз (kaskad.gw.exits.pool)
        d.exits.push(exit("e-5b5b5b5b5b", 5, "bad", "", "", "e-5b5b5b5b5b", "unknown", "ещё не проверялся", null, "ключ VLESS не разобран"));
        d.exits.push(exit("e-41a19b9cbe-6", 6, "bad", "2.0", "vienna.example.net", "Вена", "unknown", "ещё не проверялся", null, "повтор ключа строки выше — шлюз его не использует"));
      }
      // подписки выходов (как exitsubs.public — адреса нет): «Новый ключ» принесла подписка по домену; подписка по IP не
      // обновляется (ошибка сервера подписки) — «не обновлялась 30 ч»
      d.subs = [
        { id: "s-1a2b3c", n: 1, domain: "sub.example.net", auto: true, keys: ["e-0f0f0f0f0f"], excluded: ["e-0e0e0e0e0e"], count: 2, full: 0, added: "2026-10-01T10:00:00Z", last_ok: nowS() - 3 * 3600, last_try: nowS() - 3 * 3600, error: "" },
        { id: "s-4d5e6f", n: 2, domain: "", auto: true, keys: [], count: 0, full: 0, added: "2026-10-05T10:00:00Z", last_ok: null, last_try: nowS() - 1800, error: "Сервер подписки ответил 404 — проверьте адрес. Ключи пула не тронуты" }
      ];
      d.subNext = 3;
      d.users = [
        { id: "u-1a2b3c4d", name: "Мама", route: null, enabled: true, added: "2026-09-01T10:00:00Z" },
        { id: "u-5e6f7a8b", name: "Папа", route: { via: "exit:e-41a19b9cbe", fail: "block" }, enabled: true, added: "2026-09-02T10:00:00Z" },
        { id: "u-9c0d1e2f", name: "Гости", route: { via: "block", fail: "block" }, enabled: false, added: "2026-09-03T10:00:00Z" },
        // сценарии владельца (этап 2 схемы): устройство с одним входом и с тремя — от каждого одна линия
        { id: "u-3c3c3c3c", name: "Тест G3", route: null, enabled: true, added: "2026-10-07T10:00:00Z" }
      ];
      if (RUG && RUG !== "free") { d.users[0].ru_via = RU_HOME; d.users[1].ru_via = RU_HOME; }   // free — группы есть, никому не выбраны
      d.devices = [
        dev("d-7ae67ae0", "u-1a2b3c4d", "Телефон Мамы", "phone", "ios", ["awg3", "awg2", "wg", "vless"], null, { wg: "10.77.1.10", awg2: "10.77.3.10", awg3: "10.77.4.10" }, "mama-phone-1f3a"),
        dev("d-b1c2d3e4", "u-1a2b3c4d", "Роутер на даче", "router", "keenetic", ["awg2", "l2tp"], { via: "pool", fail: "direct" }, { awg2: "10.77.3.10", ppp: "10.77.5.10" }, "dacha-7f3a"),
        dev("d-f5a6b7c8", "u-5e6f7a8b", "Ноутбук Папы", "laptop", "windows", ["awg3", "l2tp", "socks5"], null, { awg3: "10.77.4.11", ppp: "10.77.5.11" }, "papa-pc-0b9c"),
        dev("d-0a1b2c3d", "u-5e6f7a8b", "Телевизор", "tv", "androidtv", ["awg3"], { via: "direct", fail: "block" }, { awg3: "10.77.4.12" }, "tv-77aa"),
        // маршрут на удалённый выход (§2.2, §7.3 «выбранный выход удалён — идёт через группу»)
        dev("d-3c3c0001", "u-3c3c3c3c", "pptp-g3", "pc", "windows", ["pptp"], { via: "exit:e-0d0d0d0d0d", fail: "block" }, { ppp: "10.77.5.20" }, "pptp-g3-11aa"),
        dev("d-3c3c0002", "u-3c3c3c3c", "l2tp-g3", "pc", "windows", ["l2tp", "socks5", "ikev2"], null, { ppp: "10.77.5.21" }, "l2tp-g3-22bb"),
        // только SOCKS5 (стенд T-scheme-3.2.16, Н-1): выход выбирает xray среди всех живых — ни за одним не закреплено
        dev("d-3c3c0003", "u-3c3c3c3c", "socks-g3", "pc", "windows", ["socks5"], null, {}, "socks-g3-33cc"),
        // VLESS Reality: адресов нет (вход xray), на связи — addr null, путь — как у SOCKS5
        dev("d-3c3c0004", "u-3c3c3c3c", "vless-g3", "phone", "android", ["vless"], null, {}, "vless-g3-44dd")
      ];
      if (RUG && RUG !== "empty" && RUG !== "free" && RUG !== "one" && RUG !== "tier") d.devices[2].ru_via = RU_OFFICE;   // ноутбук Папы — своё «Офис»
      d.forwards = [
        { proto: "tcp", port: 443, to: "203.0.113.50", to_port: 443, name: "Сайт", origin: "k3", added: "2026-09-10T10:00:00Z" },
        { proto: "udp", port: 51821, to: "198.51.100.7", to_port: 51820, name: "AWG на Вену", origin: "k3", added: "2026-09-11T10:00:00Z" },
        { proto: "tcp", port: 8443, to: "203.0.113.51", to_port: 8443, name: "", origin: "imported", added: "2026-09-12T10:00:00Z" }
      ];
      // как у live.py: id — миллисекунды часов события, у событий выходов — exit, у событий входов — inbound
      d.events = [
        { id: (nowS() - 600) * 1000, ts: nowS() - 600, kind: "exit_dead", text: "Выход «Токио» не работает: нет ответа", exit: "e-9aa1b2c3d4" },
        { id: (nowS() - 5400) * 1000, ts: nowS() - 5400, kind: "exit_alive", text: "Выход «Хельсинки» снова работает", exit: "e-7c2d8e0f13" }
      ];
      if (d.gw.enabled) d.events.push({ id: (nowS() - 7200) * 1000, ts: nowS() - 7200, kind: "inbound_down", text: "Протокол AmneziaWG 1.5 не работает", inbound: "awg1" });
    }
    if (NONE.indexOf("exits") >= 0) { d.exits = []; d.subs = []; }
    if (Q.get("fw") === "long") d.forwards.push(
      { proto: "udp", port: 27015, to: "203.0.113.60", to_port: 27015, name: "Игровой сервер друзей во Франкфурте, основной и запасной", origin: "k3", added: "2026-09-13T10:00:00Z", _c: { packets: 4821377, bytes: 5905580032 }, _fix: true },
      { proto: "tcp", port: 25565, to: "203.0.113.61", to_port: 25565, name: "game-server-frankfurt-europe-west-primary-node-01-backup-replica", origin: "k3", added: "2026-09-14T10:00:00Z", _c: { packets: 9384216, bytes: 87960930222 }, _fix: true });
    if (NONE.indexOf("forwards") >= 0 || LEGACY) d.forwards = [];          // старый движок: пробросов панель не видит
    if (NONE.indexOf("events") >= 0) d.events = [];
    if (NONE.indexOf("people") >= 0) { d.users = []; d.devices = []; }
    // ?people=40 — UI-4: плотный список «Людей» (детектор «сколько людей в первом экране»): 40 человек, у каждого 1–4
    // устройства, двое выключены, у одного выключено устройство; данные постоянные (без случайностей)
    var NP = Math.min(200, parseInt(Q.get("people") || "0", 10) || 0);
    if (NP && !EMPTY) {
      var PN = ["Анна", "Борис", "Вера", "Глеб", "Дарья", "Егор", "Жанна", "Захар", "Ирина", "Кирилл", "Лена", "Максим", "Нина", "Олег", "Полина", "Роман",
        "Света", "Тимур", "Ульяна", "Фёдор", "Юлия", "Яков", "Офис", "Склад", "Дача", "Гости 2", "Бабушка", "Дедушка", "Соседи", "Студия"];
      var KD = [["phone", "ios", "Телефон"], ["laptop", "windows", "Ноутбук"], ["phone", "android", "Телефон"], ["router", "keenetic", "Роутер"], ["tv", "androidtv", "Телевизор"], ["laptop", "macos", "Макбук"]];
      var AC = [["awg3", "vless"], ["awg2"], ["vless", "hy2", "wg", "socks5"], ["l2tp"], ["awg3"], ["wg", "awg2", "vless"]];
      for (var pi = 0; pi < NP; pi++) {
        var uid = "u-f" + ("000000" + pi.toString(16)).slice(-7), nm = PN[pi % PN.length] + (pi >= PN.length ? " " + (Math.floor(pi / PN.length) + 1) : "");
        d.users.push({ id: uid, name: nm, route: pi % 7 === 3 ? { via: "exit:e-41a19b9cbe", fail: "block" } : null, enabled: pi % 13 !== 5, added: "2026-10-01T10:00:00Z" });
        for (var di = 0, dn = 1 + (pi * 7) % 4; di < dn; di++) {
          var k = KD[(pi + di) % KD.length], dd = dev("d-f" + ("000000" + (pi * 4 + di).toString(16)).slice(-7), uid, k[2] + " " + nm, k[0], k[1], AC[(pi + di * 2) % AC.length].slice(), null, { wg: "10.77.9." + (pi + 1), awg2: "10.77.9." + (pi + 1), awg3: "10.77.9." + (pi + 1) }, "p" + pi + "-" + di);
          if (pi % 11 === 4 && di === 0) dd.enabled = false;
          // UI-B: недавно добавленные — «История с …» в «Графике» и «нет данных до …» в линии за сутки
          if (pi % 10 === 2 && di === 0) dd.added = new Date(Date.now() - 2 * 86400000).toISOString();
          if (pi % 10 === 7 && di === 0) { var am = new Date(); am.setHours(Math.max(0, am.getHours() - 6), 0, 0, 0); dd.added = am.toISOString(); }
          d.devices.push(dd);
        }
      }
    }
    return d;
  }
  function dev(id, user, name, kind, platform, access, route, ips, login) {
    return { id: id, user: user, name: name, kind: kind, platform: platform, route: route, enabled: true, login: login, ips: ips, access: access, added: "2026-09-05T10:00:00Z" };
  }
  // выход пула, как его видит сервер (kaskad.gw.exits.pool + public): tag/mark/iface по виду и строке; у плохого ключа — problem
  // выход через xray (этап 1, T1): вид «xray», протокол — в proto (как kaskad.gw.exits.public); «vless» в вызовах ниже —
  // протокол такого выхода; тег xray — kaskad.gw.exits.tag: «gx-» у зарубежного, «gr-» у российского (T15)
  var XRAY_PROTO = { vless: "VLESS", hysteria2: "Hysteria2", shadowsocks: "Shadowsocks", trojan: "Trojan", vmess: "VMess" };
  function isXray(e) { return !!e && (e.kind === "xray" || e.kind === "vless"); }
  function exitTag(id, role) { return (role === "ru" ? "gr-" : "gx-") + id; }
  function ruExit(e) { e.role = "ru"; e.tag = exitTag(e.id, "ru"); return e; }
  function exit(id, index, kind, version, host, name, state, detail, since, problem) {
    var proto = XRAY_PROTO[kind] ? kind : "";
    if (proto) kind = "xray";
    // where — имя ключа без своего имени (exits.set_where): «AmneziaWG 2.0 · адрес»; named — имя своё, не where
    var where = [kind === "awg" ? "AmneziaWG" + (version ? " " + version : "") : (proto ? XRAY_PROTO[proto] : ""), host].filter(Boolean).join(" · ");
    var e = { id: id, index: index, tag: exitTag(id, "abroad"), kind: kind, proto: proto, role: "abroad", mark: kind === "awg" ? 256 + index : (proto ? 0x300 + index : null),
      iface: kind === "awg" ? "kawg" + index : null, version: version, host: host, name: name, where: where, named: !!where && name !== where,
      enabled: true, state: state, detail: detail, since: since };
    if (problem) e.problem = problem;
    return e;
  }
  db = freshDb();

  // ── Помощники ответов ─────────────────────────────────────────────────────
  var EXIT_PREF = {};
  function routeOf(d) {
    var u = db.users.filter(function (x) { return x.id === d.user; })[0] || {};
    if (d.route) return Object.assign({ from: "device" }, d.route);
    if (u.route) return Object.assign({ from: "user" }, u.route);
    return Object.assign({ from: "default" }, db.gw.route);
  }
  // как api._inbound_rows: running/sessions — из свежего live.json (сборщик молчит — null), warnings и why — тоже оттуда
  function inboundRows() {
    return INBOUND_DEFS.map(function (x) {
      var r = clone(db.inbounds[x[0]]);
      r.kept = r._kept && !r.installed ? { ports: r._kept } : null;     // как ops.inbound_list: удалённый ждёт порт и ключи
      delete r._kept;
      var live = r.installed && r.enabled && db.gw.enabled;
      r.devices = db.devices.filter(function (d) { return d.access.indexOf(x[0]) >= 0; }).length;
      r.label = r.label || ""; r.shown = r.label ? r.title + " — " + r.label : r.title;      // T13: подпись — только показ
      r.record = clone(r._record || r.options);
      delete r._record;
      if (x[0] === "vless" && r.record && r.record._vl) Object.assign(r.record, vlView(r.record, r.devices));
      r.options = Object.keys(r.options).map(function (k) { return { key: k, title: k, value: !!r.options[k] }; });
      if (!live) { r.running = false; r.sessions = 0; r.warnings = []; r.why = null; }
      else if (db.stale) { r.running = null; r.sessions = null; r.warnings = []; r.why = null; }
      else if (r.running) r.why = null;
      r.rx = null;
      r.tx = null;
      r.needs_key = needsKey(x[0]);
      return r;
    });
  }
  // как api.py _needs_key: AmneziaWG не установить, пока нет ни ядра на диске, ни ключа GitHub
  function needsKey(kind) {
    var r = db.inbounds[kind];
    return /^awg\d$/.test(kind) && !!r && r.available && !r.installed && !db.awgCore && !db.github.set;
  }
  function githubDoc() {
    var g = db.github;
    return { ok: true, set: g.set, source: g.source, ours: g.source === "bot" || g.source === "config", state: g.state, checked: g.checked, error: g.error, repo: "anten-ka/kaskad-pro" };
  }
  // как userpage.status: ссылка полная, только когда панель открыта из интернета
  function pageDoc(u) {
    var tok = db.pages[u.id];
    var pub = db.panel.public;
    return { ok: true, user: { id: u.id, name: u.name, enabled: u.enabled !== false }, enabled: !!tok, path: tok ? "/p/" + tok : null,
      url: tok && pub ? "https://" + SRV + ":43125/p/" + tok : null, public: pub,
      note: tok && !pub ? "Панель сейчас закрыта для интернета (только через SSH-туннель): пользователь не откроет эту ссылку. Включите «Панель → Доступ из интернета» — ссылка станет полной." : "",
      qr: true, qr_note: "" };
  }
  // как userpage.sub_status: своя ссылка …/p/<токен>/sub; устройств — включённые с выданным VLESS, Hysteria2, AmneziaWG
  // или WireGuard (SUB_KINDS, SUB_CONF_KINDS); apps — приложения подписки по платформам
  var SUB_IB = ["vless", "hy2", "awg3", "awg2", "awg1", "wg"];
  function subDoc(u) {
    var tok = db.subs[u.id];
    var pub = db.panel.public;
    var on = u.enabled !== false;
    var n = on ? db.devices.filter(function (d) { return d.user === u.id && d.enabled !== false && (d.access || []).some(function (k) { return SUB_IB.indexOf(k) >= 0; }); }).length : 0;
    return { ok: true, user: { id: u.id, name: u.name, enabled: on }, enabled: !!tok, path: tok ? "/p/" + tok + "/sub" : null,
      url: tok && pub ? "https://" + SRV + ":43125/p/" + tok + "/sub" : null, public: pub,
      note: tok && !pub ? "Панель сейчас закрыта для интернета (только через SSH-туннель): пользователь не откроет эту ссылку. Включите «Панель → Доступ из интернета» — ссылка станет полной." : "",
      devices: n, interval: 12, qr: true, qr_note: "",
      // приложения подписки по платформам — настоящий каталог (install_apps.sub_blocks через tools/gen_mock_install.py)
      apps: window.KaskadMockSubApps ? window.KaskadMockSubApps(document.documentElement.lang) : {},
      empty_note: n ? "" : on ? "Сейчас подписка пустая: у пользователя нет включённых устройств с VLESS, Hysteria2, AmneziaWG или WireGuard. Выдайте устройству один из них — ключ появится в приложении при следующем обновлении." : "Пользователь приостановлен: подписка отдаёт пустой список, пока доступ не возобновят." };
  }
  function panelPublic() {
    var p = db.panel;
    return { on: p.public, port: p.port, bind: p.public ? "10.0.0.5" : "", host: p.public ? SRV : "", cert_host: p.public ? SRV : "",
      url: p.public ? "https://" + siteUrlHost() + ":" + p.port + "/" + panelSecret() + "/" : null };
  }
  // Один секрет панели — у nginx и у открытого режима; «Адрес панели» — главный (домен с сайтом, иначе порт)
  function panelSecret() { var s = siteDb(); return s.path || (s.path = sitePath()); }
  function panelAddress() {
    var s = siteDb(), p = db.panel, m = siteMask(panelSecret());
    var pub = p.public ? "https://" + siteUrlHost() + ":" + p.port + "/" + m + "/" : null;
    if (s.mode === "site" && s.domain) return { kind: "domain", masked: "https://" + s.domain + "/" + m + "/", fallback: pub, port: p.public ? p.port : null };
    return pub ? { kind: "public", masked: pub, fallback: null, port: p.port } : null;
  }
  var FINGERPRINT = "AB:12:CD:34:EF:56:78:90:AB:12:CD:34:EF:56:78:90:AB:12:CD:34:EF:56:78:90:AB:12:CD:34:EF:56:78:90";
  // что дать первым по платформе — issue_os.MATRIX целиком, примечания — каталог gw_issue (ru); английские — в
  // dev-mock-en.js. Сверку держит tests/test_setup_layout_332.py (MockOffers)
  var OFFERS = {
    ios: [["awg3", "AmneziaVPN: ссылка vpn:// или QR; AmneziaWG: файл .conf или QR"], ["awg2", "AmneziaVPN: ссылка vpn:// или QR; AmneziaWG: файл .conf или QR"], ["awg1", "AmneziaVPN: ссылка vpn:// или QR; AmneziaWG: файл .conf или QR"], ["vless", "Streisand или Hiddify: ссылка vless:// или QR"], ["hy2", "Hysteria2 (UDP): ссылка hysteria2:// или QR — v2RayTun, Hiddify, Karing, v2rayN"], ["ikev2", "профиль .mobileconfig — встроенный клиент, без приложений"], ["wg", "приложение WireGuard: QR"], ["ocserv", "Cisco Secure Client: адрес, логин и пароль"]],
    macos: [["awg3", "AmneziaVPN: ссылка vpn://; AmneziaWG: файл .conf"], ["awg2", "AmneziaVPN: ссылка vpn://; AmneziaWG: файл .conf"], ["awg1", "AmneziaVPN: ссылка vpn://; AmneziaWG: файл .conf"], ["vless", "Hiddify, Streisand или v2rayN: ссылка vless://"], ["hy2", "Hysteria2 (UDP): ссылка hysteria2:// или QR — v2RayTun, Hiddify, Karing, v2rayN"], ["ikev2", "профиль .mobileconfig — встроенный клиент"], ["wg", "приложение WireGuard: файл .conf"], ["ocserv", "OpenConnect GUI или Cisco Secure Client"]],
    android: [["awg3", "AmneziaVPN: ссылка vpn:// или QR; AmneziaWG: файл .conf или QR"], ["awg2", "AmneziaVPN: ссылка vpn:// или QR; AmneziaWG: файл .conf или QR"], ["awg1", "AmneziaVPN: ссылка vpn:// или QR; AmneziaWG: файл .conf или QR"], ["vless", "v2RayTun, Hiddify, v2rayNG или NekoBox: ссылка vless:// или QR"], ["hy2", "Hysteria2 (UDP): ссылка hysteria2:// или QR — v2RayTun, Hiddify, Karing, v2rayN"], ["wg", "приложение WireGuard: QR"], ["ikev2", "встроенный клиент IKEv2 — Android 12 и новее"], ["openvpn", "OpenVPN Connect: файл .ovpn"]],
    androidtv: [["awg3", "AmneziaVPN: ссылку vpn:// удобно вставить из буфера; AmneziaWG — файл .conf"], ["awg2", "AmneziaVPN: ссылку vpn:// удобно вставить из буфера; AmneziaWG — файл .conf"], ["awg1", "AmneziaVPN: ссылку vpn:// удобно вставить из буфера; AmneziaWG — файл .conf"], ["vless", "v2RayTun или Hiddify: ссылку vless:// вставьте из буфера"]],
    windows: [["awg3", "AmneziaVPN: ссылка vpn://; AmneziaWG: файл .conf"], ["awg2", "AmneziaVPN: ссылка vpn://; AmneziaWG: файл .conf"], ["awg1", "AmneziaVPN: ссылка vpn://; AmneziaWG: файл .conf"], ["vless", "Hiddify или v2rayN: ссылка vless://"], ["hy2", "Hysteria2 (UDP): ссылка hysteria2:// или QR — v2RayTun, Hiddify, Karing, v2rayN"], ["ikev2", "встроенный клиент: скрипт PowerShell Add-VpnConnection"], ["sstp", "встроенный клиент: скрипт PowerShell Add-VpnConnection"], ["l2tp", "встроенный клиент: скрипт PowerShell Add-VpnConnection"], ["openvpn", "OpenVPN Connect или OpenVPN GUI: файл .ovpn"], ["ocserv", "OpenConnect GUI или Cisco Secure Client"]],
    linux: [["wg", "wg-quick up ./gKP-….conf"], ["awg3", "amneziawg-tools 3.1: awg-quick up ./gKP-….conf"], ["awg2", "amneziawg-tools: awg-quick up ./gKP-….conf"], ["awg1", "amneziawg-tools: awg-quick up ./gKP-….conf"], ["vless", "Hiddify или v2rayN: ссылка vless://"], ["hy2", "Hysteria2 (UDP): ссылка hysteria2:// или QR — v2RayTun, Hiddify, Karing, v2rayN"], ["openvpn", "openvpn --config kaskad-openvpn.ovpn"], ["ocserv", "команда openconnect с отпечатком сертификата"], ["socks5", "прокси SOCKS5 для отдельных программ"]],
    keenetic: [["awg2", "KeeneticOS 5.1.1 и новее: импорт из файла"], ["awg1", "линия 1.5 — KeeneticOS 5.1.1+, линия 1.0 — с 4.2"], ["wg", "файл .conf: импорт из файла"], ["l2tp", "«Другие подключения» → VPN-подключения"]],
    mikrotik: [["wg", "RouterOS 7: скрипт настройки WireGuard"], ["l2tp", "скрипт RouterOS 7: /interface l2tp-client"], ["sstp", "скрипт RouterOS 7: /interface sstp-client"], ["openvpn", "RouterOS 7: /interface ovpn-client, сертификаты из .ovpn"]],
    openwrt: [["awg2", "пакеты awg-openwrt: LuCI → «Load configuration…» («Загрузка конфигурации…»)"], ["awg3", "3.1 — только OpenWrt 24.10/25.12 со сборками AWG 3.1"], ["awg1", "пакеты awg-openwrt: LuCI → «Load configuration…» («Загрузка конфигурации…»)"], ["wg", "LuCI → «Load configuration…» («Загрузка конфигурации…»)"], ["openvpn", "пакет openvpn-openssl, файл .ovpn"]],
    tplink: [["l2tp", "у старых моделей только L2TP без IPsec"], ["pptp", "только если протокол включён"]],
    huawei: [["l2tp", "у старых моделей только L2TP без IPsec"], ["pptp", "только если протокол включён"]],
    xiaomi: [["l2tp", "у старых моделей только L2TP без IPsec"], ["pptp", "только если протокол включён"]],
    asus: [["l2tp", "у старых моделей только L2TP без IPsec"], ["openvpn", "VPN-клиент роутера: импорт файла .ovpn"], ["pptp", "только если протокол включён"]],
    other: [["wg", "файл .conf для любого клиента WireGuard"], ["awg3", "AmneziaVPN: ссылка vpn:// или QR; AmneziaWG: файл .conf или QR"], ["awg2", "AmneziaVPN: ссылка vpn:// или QR; AmneziaWG: файл .conf или QR"], ["awg1", "файл .conf AmneziaWG"], ["vless", "любой клиент VLESS (Hiddify, v2RayTun, v2rayN): ссылка vless://"], ["hy2", "Hysteria2 (UDP): ссылка hysteria2:// или QR — v2RayTun, Hiddify, Karing, v2rayN"], ["openvpn", "файл .ovpn"], ["socks5", "прокси SOCKS5"]]
  };
  var OFFER_NOTES = { ios: ["В российском App Store AmneziaVPN скрыт — подойдут AmneziaWG или DefaultVPN."], androidtv: ["На ТВ нет камеры: ссылку vpn:// вставьте из буфера или загрузите файл .conf."], keenetic: ["AmneziaWG 3.1 — только с KeeneticOS 5.2 Alpha 11 (тестовый канал); на прошивках до 5.1.1 — линия 1.0 или WireGuard."], mikrotik: ["AmneziaWG в RouterOS нет — только в контейнере; проще WireGuard."], openwrt: ["Официальных пакетов AmneziaWG у OpenWrt нет — только сборки сообщества."], tplink: ["Посмотрите в меню роутера раздел «VPN-клиент»; если его нет — роутер не подойдёт. Обычно у TP-Link только L2TP и PPTP."], huawei: ["У этих роутеров обычно только L2TP и PPTP."], xiaomi: ["У этих роутеров обычно только L2TP и PPTP."] };
  function offersDoc(p) {
    var rows = OFFERS[p] || OFFERS.other;
    return { ok: true, platform: OFFERS[p] ? p : "other", notes: OFFER_NOTES[p] || [], offers: rows.map(function (x) {
      var r = db.inbounds[x[0]];
      return { inbound: x[0], format: "", note: x[1], title: r ? r.title : x[0], installed: !!(r && r.installed), enabled: !!(r && r.installed && r.enabled) };
    }) };
  }
  function nextTs() { return (db._ts = Math.max((db._ts || 0) + 0.001, Date.now() / 1000)); }
  // как у сервера (live.py): «не работает» — только у включённого входа на включённом шлюзе; на выключенном шлюзе службы стоят, и это не проблема
  var PROBLEMS = function () {
    return !db.gw.enabled ? [] : INBOUND_DEFS.filter(function (x) { var r = db.inbounds[x[0]]; return r.installed && r.enabled && !r.running; }).map(function (x) { return "Протокол " + x[1] + " не работает"; });
  };
  // host.health live.json (SPEC-node-score §3.6) по режиму ?health=; тексты — как у сервера (gw_host.*, язык читателя)
  var H_DISK = { id: "disk", level: "crit", stale: false, confirm_until: null, value: 98.0, unit: "pct", text: "Диск / почти заполнен: осталось 0,8 ГБ (98% занято)", advice: "Чаще всего растут журналы. Совет: journalctl --vacuum-size=200M" };
  var H_MEM = { id: "mem", level: "warn", stale: false, confirm_until: null, value: 7.0, unit: "pct", text: "Мало свободной памяти: 7% (140 МБ)", advice: "Совет: модуль ядра AmneziaWG вместо программы, тариф с 2 ГБ памяти и больше" };
  var H_STEAL = { id: "steal", level: "warn", stale: false, confirm_until: null, value: 14.0, unit: "pct", text: "Соседи по серверу отнимают процессор: steal 14% за 15 мин", advice: "Хостер продал ядро нескольким клиентам. Совет: напишите хостеру или возьмите тариф с выделенным ядром" };
  function healthDoc(mode) {
    var now = nowS(), h = { v: 1, at: now, level: "ok", since: now - 3 * 86400, instant: false, by: "collector", cost: { cpu_ms_h: 412.0, since: now - 3600 },
      hint: null, top: [], info: [], checks: { cpu: { level: "ok", value: 12.0, shown: "ok" }, mem: { level: "ok", value: 41.0, shown: "ok" }, disk: { level: "ok", value: 38.0, shown: "ok" }, steal: { level: "ok", value: 0.3, shown: "ok" } }, probes: { on: false, loss10: null, rtt10: null, norm: null, anchors: [] } };
    var top = function (fs) { h.top = fs.map(function (f) { return Object.assign({}, f, { since: now - 1800 }); }); fs.forEach(function (f) { h.checks[f.id] = { level: f.level, value: f.value, shown: f.level }; }); };
    if (mode === "warn") { h.level = "warn"; h.since = now - 1500; top([H_STEAL]); }
    if (mode === "crit") {
      h.level = "crit"; h.since = now - 1200; top([H_DISK, H_MEM, Object.assign({}, H_STEAL, { level: "crit", confirm_until: now + 600 })]);
      h.top[2].level = "warn"; h.checks.steal = { level: "warn", value: 14.0, shown: "crit" }; h.checks.swap = { level: "warn", value: 61.0, shown: "warn" };
      h.info = [{ id: "anchor", addr: "8.8.8.8", text: "Точка 8.8.8.8 теряет 40% — похоже, режет пинги; на здоровье не влияет" }];
    }
    if (mode === "stale") { h.level = "crit"; h.since = now - 7200; top([Object.assign({}, H_DISK, { stale: true, stale_msg: { key: "gw_host.stale", args: { min: 4 } } })]); }
    if (mode === "wait") { h.level = "wait"; h.since = null; h.checks = { cpu: { level: "wait", why: "wait", why_text: "ждёт истории: 2 мин из 30" } }; }
    if (mode === "off") { h.level = "off"; h.since = null; h.checks = {}; h.probes = null; }
    if (mode === "instant") {
      h.level = "warn"; h.since = null; h.instant = true; h.by = "panel"; h.cost = null; h.probes = null; h.hint = "bot_python";
      h.hint_text = "Уведомления о здоровье сервера без шлюза присылает бот на Python: пункт 8 SSH-меню";
      top([Object.assign({}, H_DISK, { level: "warn", value: 91.0, text: "На диске / осталось 3,4 ГБ (91% занято)" })]);
      delete h.checks.cpu;
    }
    return h;
  }
  // full-раунд сборщика кладёт ещё cores/busy/steal/iowait/softirq и health (§3.6)
  function hostDoc() { return { cpu: db._cpu, mem_total: 2063990784, mem_used: db._mem, load: [0.12, 0.2, 0.18], uptime: 3 * 86400 + 4000,
    cores: 2, busy: db._cpu, steal: HEALTH === "warn" || HEALTH === "crit" ? 14.0 : 0.3, iowait: 0.1, softirq: 0.2, health: healthDoc(HEALTH) }; }
  // GET /api/host/health (panel/host.py): сборщик пишет — его host.health; молчит — снимок панели, мгновенные правила
  function hostHealthAnswer() {
    var l = liveInfo(), h = l.fresh ? healthDoc(HEALTH) : healthDoc("instant");
    if (!l.fresh && HEALTH !== "instant") { h.hint = null; h.hint_text = null; }
    return { ok: true, health: h, by: h.by, instant: h.instant, live: { age: l.age, fresh: l.fresh } };
  }
  var OFFLINE = { online: false, inbound: null, addr: null, since: null, rx: null, tx: null };
  // live.json (docs/k3/SPEC-wave3.md §9.2) — то, что несёт поток SSE: БЕЗ events и seq; запись есть у КАЖДОГО устройства
  // (live.py devices_of: не в сети — online:false, остальное null); у входа — warnings и why адаптера
  function liveDoc() {
    var ib = {}, ex = {}, dv = {}, fw = {};
    INBOUND_DEFS.forEach(function (x) {
      var r = db.inbounds[x[0]];
      var on = db.gw.enabled && r.enabled;
      if (r.installed) ib[x[0]] = { enabled: on, running: on ? r.running : false, sessions: on ? r.sessions : 0, ports: r.ports.join(", "), title: x[1], rx: null, tx: null,
        warnings: on ? r.warnings.slice() : [], why: on && !r.running ? r.why : null };
      // VLESS: интерфейсов нет — rx/tx входа из счётчиков xray (сумма по устройствам)
      if (ib[x[0]] && x[0] === "vless" && on) db.devices.forEach(function (d) { if (d._live && d._live.inbound === "vless") { ib.vless.rx = (ib.vless.rx || 0) + (d._live.rx || 0); ib.vless.tx = (ib.vless.tx || 0) + (d._live.tx || 0); } });
    });
    // как у сборщика: рукопожатие ноги AmneziaWG и начало тишины — моментами, возраст считает страница
    db.exits.forEach(function (e) { ex[e.id] = { alive: e.state === "alive", kind: e.kind, label: e.name, state: e.state === "alive" ? "ok" : e.state, since: e.since, enabled: e.enabled,
      detail: e.detail || "", handshake_at: e._hs ? nowS() - e._hs : null, silent_since: e._silent ? nowS() - e._silent : null, fails: e.fails || 0, dies_after: e.kind === "awg" ? 3 : 2 }; });
    db.devices.forEach(function (d) { dv[d.id] = d._live && d._live.online ? d._live : clone(OFFLINE); });
    db.forwards.forEach(function (f) { fw[f.proto + ":" + f.port] = f._c || { packets: 0, bytes: 0 }; });
    var doc = { v: 1, ts: nextTs(), gateway: { enabled: db.gw.enabled, problems: PROBLEMS(), services: { dns: db.gw.enabled ? true : null, xray: db.gw.enabled ? true : null } },
      inbounds: ib, exits: ex, devices: dv, forwards: fw, host: hostDoc() };
    // T15 Q6 (§8.9): шаг групп, российский последний шаг отдельно, сервер не достаёт до российских адресов
    if (db.policy || RUG) { doc.groups = liveGroups(); doc.no_exit_ru = noExitRu(); doc.policy = { node_offline: false, node_ru_offline: RUG === "rudown" }; }
    // метки линий (владелец 24.10): открытое окно активности и минута истории выходов
    var act = actOpen();
    if (act) doc.activity = act;
    if (!NO_CHARTS) doc.metrics = { ts: Math.floor(nowS() / 60) * 60 - 60 };
    return doc;
  }
  // поле live ответов GET: возраст снимка сборщика (несвежий — старше 30 с, как live.STALE)
  // шлюз только что включён: сборщик делает первый круг — у сервера отсрочка 120 с (api.START_GRACE), здесь 8 с;
  // live.starting — сколько секунд ещё ждать спокойно, плашки STALE_TEXT в это время нет (T-G1b, O)
  function startingLeft() { return db.stale && db.startingUntil > Date.now() ? Math.ceil((db.startingUntil - Date.now()) / 1000) : 0; }
  function liveInfo() {
    if (!db.stale) return { ts: nextTs(), age: 1.2, fresh: true };
    var out = { ts: nowS() - 600, age: 600.0, fresh: false };
    if (startingLeft()) out.starting = startingLeft();
    return out;
  }
  var staleAlarm = function () { return db.stale && db.gw.enabled && !startingLeft(); };
  // как kaskad.panel.api._exit_rows: public() + usable + text
  // как api._exits_now (exits.no_data): сборщик молчит на включённом шлюзе — «нет данных»; проблема ключа остаётся
  function exitRowNow(e) {
    var r = exitRow(e);
    if (db.policy && e.kind !== "bad" && !/-\d+$/.test(e.id)) policyRow(r, e);
    if (!(db.stale && db.gw.enabled) || r.problem) return r;
    return Object.assign(r, { state: "nodata", alive: null, usable: null, text: NO_DATA_TEXT, since: null, fails: 0, silent_since: null, silent: null });
  }
  function exitRow(e) {
    var r = clone(e);
    r.source = subOwner(e.id);
    r.alive = e.state === "alive";
    r.usable = (e.kind === "awg" || isXray(e)) && e.enabled && r.alive;
    r.text = e.problem ? e.problem : (r.usable || e.state === "alive") ? "работает" : e.state === "unknown" ? "ещё не проверялся" : "не работает" + (e.detail ? ": " + e.detail : "");
    return r;
  }
  // GET overview — как у kaskad.panel.api: gateway, inbounds[], exits{total, alive, items[]}, devices, forwards{count, packets, bytes},
  // events, warnings, live{ts, age, fresh}, gen; без свежих данных сборщика — null вместо чисел и плашка STALE_TEXT
  function overviewDoc() {
    if (!Sse.clients.length) Sse.simulate();              // без потока данные «сборщика» движутся от опроса
    var fresh = !db.stale;
    var inb = INBOUND_DEFS.filter(function (x) { return db.inbounds[x[0]].installed; }).map(function (x) {
      var r = db.inbounds[x[0]], on = r.enabled && db.gw.enabled;
      return { kind: x[0], title: x[1], enabled: r.enabled, running: on ? (fresh ? r.running : null) : false, sessions: on ? (fresh ? r.sessions : null) : 0, ports: r.ports.join(", ") };
    });
    var rows = db.exits.map(exitRowNow);
    var items = rows.map(function (e) { return { id: e.id, label: e.name, kind: e.kind, state: e.state, alive: e.alive, enabled: e.enabled, usable: e.usable }; });
    var pk = 0, by = 0;
    db.forwards.forEach(function (f) { if (f._c) { pk += f._c.packets; by += f._c.bytes; } });
    var lv = liveDoc();
    var online = db.devices.filter(function (d) { return d._live && d._live.online; }).length;
    var warnings = Q.get("warn") === "1" ? ["Установлена новая версия Kaskad — перезапустите службы после обновления (пример предупреждения)."] : [];
    if (LEGACY) warnings.unshift("Пробросы ведёт старый движок (iptables): панель управляет пробросами движка nftables (меню gokaskad «Движок и перенос»).");
    if (staleAlarm()) warnings.push(STALE_TEXT);
    if (offlineNow()) warnings.push(exitsDoc().offline_text);            // как api._overview_core: пул пуст, маршрут блокирует
    return {
      ok: true, version: db.version, engine: LEGACY ? "legacy" : "nft", gen: db.gen,
      gateway: { enabled: db.gw.enabled, host: db.gw.host, users: db.users.length, devices: db.devices.length, online: fresh ? online : null, problems: fresh ? PROBLEMS() : [] },
      inbounds: inb, exits: { total: items.length, alive: items.some(function (i) { return i.state === "nodata"; }) ? null : items.filter(function (i) { return i.usable; }).length, items: items },
      devices: fresh ? lv.devices : null,
      forwards: LEGACY ? { count: 0, packets: null, bytes: null } : { count: db.forwards.length, packets: fresh ? pk : null, bytes: fresh ? by : null },
      events: db.events.slice(0, 20),
      warnings: warnings,
      // как api._with_live: сборщик молчит — host из /proc панели (live.Host.sample), без health и полей полного раунда
      live: liveInfo(), now: nowS(), host: fresh ? hostDoc() : { cpu: db._cpu || 6.0, mem_total: 2063990784, mem_used: db._mem || 813000000, load: [0.12, 0.2, 0.18], uptime: 3 * 86400 + 4000 }
    };
  }
  // net устройства (§4.4, §7.9): «Роутер на даче» — выходы группы недоступны, идёт напрямую; «socks-g3» — нет выхода (блок)
  function netOf(d) {
    if (!db.gw.enabled || db.stale) return null;
    var rt = routeOf(d);
    if (rt && (rt.via === "direct" || rt.via === "block")) return null;      // свой маршрут мимо выходов — без net (как api._nets)
    // маршрут на выход, которого нет в пуле (§2.2 target()["missing"]): идёт через группу «Основная»
    var gone = rt && rt.via.indexOf("exit:") === 0 && !find(db.exits, rt.via.slice(5));
    if (d.id === "d-b1c2d3e4") return { step: "last_direct", group: "main", since: nowS() - 1500, missing: false };
    if (d.id === "d-3c3c0003") return { step: "last_block", group: "main", since: nowS() - 780, missing: false };
    return { step: "exit", group: "main", since: null, missing: !!gone };
  }
  function peopleDoc() {
    var lv = db.stale ? null : liveDoc().devices;
    return {
      ok: true,
      users: db.users.map(function (u) { return Object.assign(clone(u), { route_ver: ver12(u.route || null), devices: db.devices.filter(function (d) { return d.user === u.id; }).length, page: !!db.pages[u.id], sub: !!db.subs[u.id] }, ruFields(u, true)); }),
      devices: db.devices.map(function (d) { var u = db.users.filter(function (x) { return x.id === d.user; })[0]; return Object.assign(clone(d), { route_effective: routeOf(d), route_ver: ver12(d.route || null), net: netOf(d), user_name: u ? u.name : "", live: lv ? clone(lv[d.id]) : null }, ruFields(d, false)); }),
      routes: { default: Object.assign({ from: "default" }, db.gw.route) },
      inbounds: INBOUND_DEFS.filter(function (x) { return db.inbounds[x[0]].installed; }).map(function (x) { return { kind: x[0], title: x[1], enabled: db.inbounds[x[0]].enabled }; }),
      platforms: ["ios", "android", "androidtv", "windows", "macos", "linux", "keenetic", "mikrotik", "openwrt", "tplink", "huawei", "xiaomi", "asus", "other"],
      device_kinds: ["phone", "tablet", "laptop", "pc", "tv", "router", "other"], live: liveInfo()
    };
  }
  // GET gw/exits — выходы и, как у сервера, add (где добавить ключ: путь SSH-меню с номером пункта — exits.add_hint) и
  // offline (пул пуст, маршрут по умолчанию через выходы, при отказе — блок: устройства без интернета)
  function exitsDoc() {
    var off = offlineNow();
    pol();
    return { ok: true, rev: db.policyRev, exits: db.exits.map(exitRowNow), subs: subsDoc(), add: addHint(), offline: off, default_route_ver: ver12(db.gw.route),
      offline_text: off ? "Устройства шлюза сейчас без интернета: выходов нет, а при отказе выходов стоит «Блокировать». Добавьте ключ VPN или выберите «Идти напрямую»." : "",
      default_route: Object.assign({ from: "default" }, db.gw.route) };
  }
  function viaExits() { var via = String(db.gw.route.via || ""); return via === "pool" || via.indexOf("exit:") === 0; }
  function addHint() {
    var n = viaExits() ? 3 : 2, menu = "gokaskad → 17 → 3 → " + n;
    return { panel: "«Выходы» → «Добавить ключ»", menu: menu, item: n, what: "«Добавить ключ выхода»",
      text: "Добавьте ключ VPN: в веб-панели — «Выходы» → «Добавить ключ», в SSH-меню — " + menu + " («Добавить ключ выхода»)." };
  }
  function offlineNow() { return !!db.gw.enabled && !db.exits.length && viaExits() && db.gw.route.fail !== "direct"; }
  // POST gw/exits {key} — как ops.exit_add поверх gokaskad --egress-add: vless://, vpn:// и текст .conf AmneziaWG
  // ([Interface]) — новый выход; другое — 400 «Не удалось разобрать ключи»; тот же ключ ещё раз — 409. Ключ не хранится:
  // только его отпечаток, чтобы узнать повтор
  var KEY_SEEN = {};
  function keyPrint(k) { var h = 0; for (var i = 0; i < k.length; i++) h = (h * 31 + k.charCodeAt(i)) | 0; return String(h) + ":" + k.length; }
  // Ключи текста — по строкам: vless://… — VLESS, vpn://… — AmneziaWG 2.0; текст .conf ([Interface]) — один ключ: с Jc —
  // AmneziaWG (S3/S4 — 2.0, I1 — 1.5, иначе 1.0), без параметров AmneziaWG — WireGuard. Как exits.preview, без секретов
  function mockKeys(text) {
    var out = [];
    if (/^\s*\[Interface\]/m.test(text)) {
      var line = /^\s*(S3|S4)\s*=/m.test(text) ? "2.0" : /^\s*I1\s*=/m.test(text) ? "1.5" : /^\s*Jc\s*=/m.test(text) ? "1.0" : "";
      var ep = (/^\s*Endpoint\s*=\s*(\S+)/m.exec(text) || [])[1] || "";
      var ad = (/^\s*Address\s*=\s*([0-9.]+)/m.exec(text) || [])[1] || "";
      out.push({ kind: "awg", version: line || "1.0", title: line ? "AmneziaWG " + line : "WireGuard", where: ep, name: "", addr: ad, print: keyPrint(text.trim()) });
    }
    text.split(/\s+/).forEach(function (w) {
      if (/^vless:\/\//.test(w)) out.push({ kind: "xray", proto: "vless", version: "", title: "VLESS", where: (/@([^?#/]+)/.exec(w) || [])[1] || "", name: decodeURIComponent((/#(.*)$/.exec(w) || [])[1] || ""), print: keyPrint(w) });
      else if (/^vpn:\/\//.test(w)) out.push({ kind: "awg", version: "2.0", title: "AmneziaWG 2.0", where: "vpn.example.net:51820", name: "", print: keyPrint(w) });
    });
    return out;
  }
  function keyText(body) {
    var key = typeof body.key === "string" ? body.key : "";
    if (!key.trim()) throw httpErr(400, "Вставьте ключ: vless://…, vpn://… или весь текст файла AmneziaWG .conf.", 2);
    if (key.length > 64 * 1024) throw httpErr(400, "Ключ длиннее 64 КБ — это не ключ выхода.", 2);
    if (key.indexOf("\u0000") >= 0) throw httpErr(400, "Это не текстовый файл ключа.", 2);
    return key;
  }
  function exitPreview(body) {
    var key = keyText(body);
    if (/^https?:\/\//im.test(key)) return { ok: true, items: [], subscription: true, text: "Ссылка подписки: ключи будут известны при добавлении." };
    // Н-2 REV-3315-fin: как ядро (exits.preview + ops.exit_preview) — адрес .conf в сети этого шлюза: problem и свободная
    // сеть suggest_base уже в предпросмотре (как exitAdd: та же сеть и тот же выбор свободной)
    var gwb = String(db.gw && db.gw.net ? db.gw.net.base : "10.77.0.0/16"), gwp = gwb.replace(/\.0\.0\/16$/, "."), free = gwb === "10.79.0.0/16" ? "172.27.0.0/16" : "10.79.0.0/16";
    var items = mockKeys(key).map(function (k) {
      var x = { id: "e-" + rid("", 10), kind: k.kind, title: k.title, where: k.where, name: k.name, in_pool: !!(KEY_SEEN[k.print] && find(db.exits, KEY_SEEN[k.print])) };
      if (!x.in_pool && k.addr && k.addr.indexOf(gwp) === 0) {
        x.problem = ML("адрес клиента " + k.addr + " попадает в сеть шлюза этого сервера " + gwb + " — переведите шлюз одного из серверов на другую сеть (в панели: Настройки › Шлюз › Сеть шлюза); устройствам переведённого шлюза придётся выдать ключи заново",
          "the client address " + k.addr + " falls within the gateway network of this server " + gwb + " — move the gateway of one of the servers to another network (in the panel: Settings › Gateway › Gateway network); the devices of the gateway you move will need new keys");
        x.suggest_base = free;
      }
      return x;
    });
    if (!items.length) throw httpErr(400, "Не нашёл ключей: нужен vless://…, vpn://… или текст .conf AmneziaWG/WireGuard.", 2);
    // первый ключ AmneziaWG на узле без ног AmneziaWG — «Собираю модуль ядра…» (как ops.module_build у сервера)
    var build = items.some(function (x) { return !x.in_pool && x.kind === "awg"; }) && !db.exits.some(function (e) { return e.kind === "awg"; });
    return { ok: true, items: items, subscription: false, module_build: build, text: items.every(function (x) { return x.in_pool; }) ? "Все ключи уже есть в пуле." : "" };
  }
  function exitAdd(body) {
    var key = keyText(body);
    var keys = mockKeys(key);
    if (!keys.length) throw httpErr(400, "Не удалось разобрать ключи.", 1);
    if (db.policy && body.group && !polGroup(body.group)) throw httpErr(400, "Такой группы нет.", 2, { field: "group" });   // до записи ключа
    var results = [], added = [];
    // Ш5 (F6 Е3, З-1 REV-3315-view): адрес ключа .conf (Address) — в сети этого шлюза: ключ пропущен, в ответе — свободная сеть
    var gwb = String(db.gw && db.gw.net ? db.gw.net.base : "10.77.0.0/16"), gwp = gwb.replace(/\.0\.0\/16$/, "."), free = gwb === "10.79.0.0/16" ? "172.27.0.0/16" : "10.79.0.0/16", same = 0;
    keys.forEach(function (k) {
      var r = { id: "", kind: k.kind, proto: k.proto || "", title: k.title, where: k.where, name: k.name };
      if (KEY_SEEN[k.print] && find(db.exits, KEY_SEEN[k.print])) { r.id = KEY_SEEN[k.print]; r.result = "in_pool"; results.push(r); return; }
      if (k.addr && k.addr.indexOf(gwp) === 0) { same += 1; r.result = "not_added"; r.why = ML("адрес " + k.addr + " — в сети шлюза " + gwb, "the address " + k.addr + " is in the gateway network " + gwb); results.push(r); return; }
      var idx = db.exits.length + 1, host = k.where.replace(/:\d+$/, "") || "new" + idx + ".example.net";
      var e = exit(rid("e-", 10), idx, k.proto || k.kind, k.kind === "awg" ? k.version : "", k.where || host, k.name || (k.title + " · " + host), "unknown", "ещё не проверялся", null);
      if (k.title === "WireGuard") e.wg = true;
      db.exits.push(e); KEY_SEEN[k.print] = e.id; r.id = e.id; r.result = "added"; results.push(r); added.push({ id: e.id, name: e.name });
      if (db.policy) {
        var tgr = body.group ? polGroup(body.group) : null;
        if (body.group && !tgr) throw httpErr(400, "Такой группы нет.", 2, { field: "group" });
        if (tgr && tgr.role === "ru") ruExit(e);
        placeNew(e.id, body.group); db.policyRev += 1;
      }
    });
    if (!added.length && same) throw httpErr(400, ML("Ключ не добавлен: его адрес — в сети шлюза " + gwb + ". Свободна, например, сеть " + free + ".",
      "The key isn’t added: its address is in the gateway network " + gwb + ". Free is, for example, " + free + "."), 1, { suggest_base: free });
    if (!added.length) throw httpErr(409, keys.length > 1 ? "Все эти ключи уже есть в пуле." : "Такой ключ уже есть в пуле.", 3, keys.length > 1 ? { results: results } : null);
    bump();
    var out = { ok: true, added: added, results: results, warnings: [], rev: db.policy ? db.policyRev : undefined, text: added.length > 1 ? "Добавлено ключей: " + added.length + "." : "Ключ добавлен." };
    if (same) out.suggest_base = free;
    return out;
  }
  // DELETE gw/exits/<id> (и повтор e-…-N) — как ops.exit_del: строка уходит из пула, номера строк после неё сдвигаются
  function exitDel(id, query) {
    var dry = !!query && query.get("dry") === "1";
    if (/-\d+$/.test(id) && !dry) needRev(null, query);       // повтор ключа e-…-N: номер строки сдвигается — сначала сверка
    if (!find(db.exits, id)) throw httpErr(404, "Такого выхода уже нет — обновите страницу.", "gone", { gone: true, exit: id });
    pol();
    if (dry) return dryAnswer(db.policyRev, movesOf(outOf(id)), []);
    if (!/-\d+$/.test(id)) { busyOnce(); needRev(null, query); }
    var mvd = movesOf(outOf(id));
    unplace(id);
    // ключ подписки, удалённый вручную, — «исключён»: её обновление его не вернёт (kaskad.gw.exitsubs, 3.3.0)
    db.subs.forEach(function (sb) { var i = sb.keys.indexOf(id); if (i >= 0) { sb.keys.splice(i, 1); (sb.excluded = sb.excluded || []).push(id); } });
    db.exits = db.exits.filter(function (e) { return e.id !== id; });
    db.exits.forEach(function (e, i) { e.index = i + 1; });
    db.policyRev += 1; bump();
    return { ok: true, id: id, left: db.exits.length, rev: db.policyRev, moves: mvd, warnings: [] };
  }
  // ⚠РФ — как rules/rfgate.state (+ need от rules/panel._status): режим по выбору «Россия напрямую» и согласию
  function rfSelected() {
    return db.rules["default"] === "ru_direct" || db.users.some(function (u) { return u.rules === "ru_direct"; }) || db.devices.some(function (d) { return d.rules === "ru_direct"; });
  }
  function rfMode() { return !rfSelected() ? "none" : db.rf.consent ? "full" : db.rf.legacy ? "legacy" : "need"; }
  function rfAllowed() { return !!db.rf.consent || db.rf.legacy; }
  function rfNeedData() {
    var orgs = db.rf.uncovered.length ? ["meta"].concat(db.rf.uncovered) : ["meta"], names = orgs.map(function (o) { return o === "fbk" ? "ФБК" : "Meta"; }).join(", ");
    return { need: "rf_consent", orgs: orgs, names: names, block: "⚠ Данная организация признана экстремистской на территории Российской Федерации (" + names + ")", consent: "Скрипт не используется на территории РФ" };
  }
  function rfDoc() {
    var vals = ["meta.com", "facebook.com", "instagram.com", "fbcdn.net", "oculus.com"];
    return { required: !db.rf.consent, mode: rfMode(), consent: db.rf.consent ? clone(db.rf.consent) : null, legacy: db.rf.legacy && !db.rf.consent ? { at: null, from: "gen1" } : null,
      orgs: [{ id: "meta", name: "Meta", since: "2022-03-21" }].concat(db.rf.uncovered.length ? [{ id: "fbk", name: "ФБК", since: "2021-06-09" }] : []),
      marked: { force: vals.map(function (v) { return { code: "category-geoblock-ru", type: "domain", value: v, org: "meta" }; }), direct: [], block: [] }, marked_n: { force: vals.length, direct: 0, block: 0 },
      uncovered: db.rf.consent ? db.rf.uncovered.slice() : [], registry: "2026-10-15",
      scan: { at: "2026-10-16T00:00:00Z", stale: false, entries: 640, unknown: 0, regexp: 12, regexp_unchecked: 1, regexp_unchecked_by: { long: 0, syntax: 0, dangerous: 1, timeout: 0, budget: 0 } },
      need: rfNeedData() };
  }
  // запись, включающая новый «Россия напрямую» без согласия, — 409 с блоком (rfgate.check); с галочкой — согласие
  function rfCheck(newPoint, tick) {
    if (tick && tick.confirm_not_rf === true) { db.rf.consent = { at: new Date().toISOString().replace(/\.\d+Z$/, "Z"), via: tick.via || "panel", orgs: rfNeedData().orgs }; db.rf.legacy = false; db.rf.uncovered = []; return; }
    if (newPoint && !db.rf.consent) { var n = rfNeedData(); throw httpErr(409, n.block + ". Подтвердите: «" + n.consent + "».", 3, n); }
  }
  function rfReset() { if (!rfSelected()) { db.rf.consent = null; db.rf.legacy = false; } }
  // GET /api/rules — как kaskad.rules.ops.status(): профиль устройства — свой, иначе человека, иначе по умолчанию
  function rulesDoc() {
    var r = db.rules, ready = !!r.lists && rfAllowed();
    var devices = db.devices.map(function (d) {
      var u = find(db.users, d.user) || {};
      var prof = d.rules || u.rules || r.default, from = d.rules ? "device" : u.rules ? "user" : "default";
      return { id: d.id, name: d.name, user: d.user, rules: d.rules || "inherit", profile: prof, from: from,
        applied: prof === "ru_direct" && !ready ? "all_exit" : prof, active: d.enabled !== false && u.enabled !== false,
        ru_via: ruOwn(d) || "inherit", ru: ruViaOf(d), ru_applies: ruApplies(d), ru_ver: ruVer(d) };
    });
    return Object.assign({ ok: true, ready: !!r.lists, default_applied: r.default === "ru_direct" && !ready ? "all_exit" : r.default,
      default_why: r.default === "ru_direct" && !ready ? (r.lists ? "no_consent" : "no_lists") : null, ver: "mockver-" + r.default, rf: rfDoc(),
      sources: [], dns: { nftset: true, version: "2.90" }, devices: devices,
      users: db.users.map(function (u) { return { id: u.id, name: u.name, rules: u.rules || "inherit", ru_via: ruOwn(u) || "inherit", ru: ruViaOf(u, true), ru_ver: ruVer(u) }; }),
      ru_groups: ruGroupsDoc(),
      ru_direct: devices.filter(function (d) { return d.applied === "ru_direct" && d.active; }).length }, clone(r));
  }
  // GET gw — как api._gw_view: services — true/false/null (null — службы нет или она не нужна), без свежих данных — пусто
  function gwDoc() {
    var fresh = !db.stale && db.gw.enabled;
    return { ok: true, enabled: db.gw.enabled, engine: LEGACY ? "legacy" : "nft", net: db.gw.net, host: db.gw.host, host_auto: db.gw.host_auto, users: db.users.length, devices: db.devices.length,
      problems: fresh ? PROBLEMS() : [], routes: { default: Object.assign({ from: "default" }, db.gw.route) }, inbounds: inboundRows(), exits: db.exits.map(exitRowNow),
      services: { dns: fresh ? true : null, xray: fresh ? true : null }, live: liveInfo(), warnings: staleAlarm() ? [STALE_TEXT] : [], clients: clientsDoc() };
  }
  // clients GET gw — как api._client_addresses: домен, IP и адрес в настройках по входам (VLESS с TLS, SSTP и
  // OpenConnect на сертификате домена — домен, остальное — адрес для клиентов)
  function clientsDoc() {
    var s = siteDb(), host = db.gw.host, rows = [{ host: host, kinds: [] }];
    Object.keys(db.inbounds).forEach(function (k) {
      var ib = db.inbounds[k];
      if (!ib.installed) return;
      var h = k === "vless" && ib._record && ib._record.security === "tls" ? (s.domain || ib._record.domain || host) : (s.use[k] && s.domain ? s.domain : host);
      var row = rows.filter(function (x) { return x.host === h; })[0];
      if (!row) { row = { host: h, kinds: [] }; rows.push(row); }
      row.kinds.push(k);
    });
    var ip = /^\d+\.\d+\.\d+\.\d+$/.test(host) ? host : (s.domain ? SITE_IP : null);
    return { domain: s.domain || null, domain_unicode: s.domain ? s.uni : null, ip: ip, hosts: rows };
  }
  // адрес устройства на входе — как scheme._ip_of: ключи ips — пулы адресов (addr.KIND_POOLS), а не виды входов
  var KIND_POOLS = { l2tp: ["ppp"], pptp: ["ppp"], sstp: ["ppp"], openvpn: ["ovpn_u", "ovpn_t"], ikev2: [], socks5: [], vless: [], hy2: [] };
  function ipOf(d, kind) {
    var pools = KIND_POOLS[kind] || [kind];
    for (var i = 0; i < pools.length; i++) if (d.ips[pools[i]]) return d.ips[pools[i]];
    return null;
  }
  function pos(id) { var p = db.layout.positions[id]; return p ? { x: p[0], y: p[1] } : null; }
  // как scheme.ru_state(): «Россия напрямую» по таблице ядра, а не по «списки скачаны»
  function ruState() {
    var out = { state: "no_lists", domains: null, error: null, next: null };
    if (!db.rules.lists) return out;
    var q = Q.get("ru");
    out.domains = q !== "nodomains";
    if (q === "notloaded") { out.state = "not_loaded"; out.error = "nft: не хватило памяти"; out.next = nowS() + 900; return out; }
    if (q === "unknown") { out.state = "unknown"; return out; }
    out.state = rfAllowed() ? "ok" : "no_consent";
    return out;
  }
  var PROFILE_TITLES = { all_exit: "Всё через выход", ru_direct: "Россия напрямую" };
  // профиль устройства: свой → человека → узла (model.profile_of), {profile, from}
  function profileOf(D, d) {
    var u = find(D.users, d.user) || {};
    if (d.rules) return { profile: d.rules, from: "device" };
    if (u.rules) return { profile: u.rules, from: "user" };
    return { profile: db.rules["default"], from: "default" };
  }
  // Куда идёт трафик устройства СЕЙЧАС — как kaskad.gw.exits.now_of: один ответ для «Куда пойдёт трафик?», схемы и
  // счётчиков (Н-1). socks — путь через SOCKS5: решает xray, пул — любой живой выход («any»), ни за одним не закреплено.
  // российские ключи (rug) — не выходы пула: зарубежные устройства через них не идут (как exits.usable сервера, роль abroad)
  function liveExits() { return db.exits.filter(function (e) { return e.role !== "ru" && (e.kind === "awg" || isXray(e)) && e.enabled && e.state === "alive"; }); }
  function nowFor(dev, route, socks) {
    var via = route.via, out = { target: via, exit: null, balancer: false, exits: [], dead: [], pool_live: [] };
    if (via === "block" || via === "direct") return out;
    var live = liveExits(), fall = route.fail === "direct" ? "direct" : "block";
    if (via.indexOf("exit:") === 0) {
      var eid = via.slice(5);
      if (live.some(function (e) { return e.id === eid; })) { out.exit = eid; out.exits = [eid]; } else { out.target = fall; out.dead = [eid]; }
      return out;
    }
    if (!live.length) { out.target = fall; return out; }
    var ids = live.map(function (e) { return e.id; });
    if (socks) { out.target = "pool"; out.balancer = "any"; out.exits = ids; return out; }
    if (!dev || !dev.id) {
      out.target = "pool"; out.pool_live = ids;
      if (ids.length === 1) { out.exit = ids[0]; out.target = "exit:" + ids[0]; out.exits = [ids[0]]; }
      return out;
    }
    var pick = live[hashOf(dev.id) % live.length];
    if (isXray(pick)) { out.target = "pool"; out.balancer = "xray"; out.exits = live.filter(function (e) { return isXray(e); }).map(function (e) { return e.id; }); }
    else { out.target = "exit:" + pick.id; out.exit = pick.id; out.exits = [pick.id]; }
    return out;
  }
  // вход пути — как kaskad.gw.trace.path_inbound без выбора: первый выданный включённый
  function firstInbound(d) {
    var on = INBOUND_DEFS.filter(function (x) { return db.inbounds[x[0]].installed && db.inbounds[x[0]].enabled; }).map(function (x) { return x[0]; });
    return (d.access || []).filter(function (k) { return on.indexOf(k) >= 0; })[0] || null;
  }
  // GET scheme — как kaskad.panel.scheme.build(): люди (устройства — строками внутри), входы, правила, пул, выходы, «напрямую», «блок»
  function schemeDoc() {
    var droute = db.gw.route, nodes = [], edges = [], counts = {}, nowCounts = {}, profCount = {};
    var alive = {};
    db.exits.forEach(function (e) { alive[e.id] = e.state === "alive"; });
    // пул — только зарубежные ключи (scheme.pool_exits); российские — внутри узлов российских групп (T15 Q6)
    var pex = db.exits.filter(function (e) { return e.role !== "ru"; });
    var usable = pex.filter(function (e) { return (e.kind === "awg" || isXray(e)) && e.enabled && alive[e.id]; });
    var rug = RUG ? pol().groups.filter(function (g) { return g.role === "ru"; }) : [], ruDevs = {}, ruChosen = {};
    var installed = INBOUND_DEFS.filter(function (x) { return db.inbounds[x[0]].installed; }).map(function (x) { return x[0]; });
    // как scheme._now: путь через первый выданный вход, ответ — nowFor (exits.now_of)
    var nowOf = function (eff, active, d) {
      if (!active) return { target: null, exit: null, balancer: false, exits: [], inbound: null, why: "устройство выключено — трафика нет" };
      var inbound = firstInbound(d), n = nowFor(d, eff, XRAY_KINDS.indexOf(inbound) >= 0);
      var fallText = eff.fail === "direct" ? "напрямую" : "блок", name = function (id) { var e = find(db.exits, id); return e ? e.name : id; };
      var why = n.balancer === "any" ? (inbound === "vless" ? "VLESS" : "SOCKS5") + ": любой живой выход пула — выберет автоматически"
        : n.balancer === "xray" ? "пул: один из VLESS-выходов — выберет автоматически"
        : n.exit ? (eff.via === "pool" ? "пул: выход «" + name(n.exit) + "»" : "выход «" + name(n.exit) + "»")
        : eff.via === "direct" || eff.via === "block" ? "маршрут «" + (eff.via === "direct" ? "напрямую" : "блок") + "»"
        : n.dead.length ? "выход «" + name(n.dead[0]) + "» не работает — " + fallText : "в пуле нет живых выходов — " + fallText;
      return { target: n.target, exit: n.exit, balancer: n.balancer, exits: n.exits, inbound: inbound, why: why };
    };
    db.users.forEach(function (u) {
      var rows = [];
      db.devices.filter(function (d) { return d.user === u.id; }).forEach(function (d) {
        var eff = routeOf(d);
        var pof = profileOf(db, d), prof = pof.profile;
        var active = d.enabled !== false && u.enabled !== false;
        var now = nowOf(eff, active, d);
        profCount[prof] = (profCount[prof] || 0) + 1;
        if (active) { counts[eff.via] = (counts[eff.via] || 0) + 1; if (now.target) nowCounts[now.target] = (nowCounts[now.target] || 0) + 1; }
        // «Российские сайты» (как scheme.build): линия к российской группе — только при действующем выборе
        var rv = ruViaOf(d), ruRow = { via: rv.via, from: rv.from, acts: null, now: null }, rt = null;
        if (rug.length) {
          ruRow.acts = ruApplies(d) && eff.via !== "block";
          if (rv.via !== "direct") {
            ruChosen[rv.via] = (ruChosen[rv.via] || 0) + 1;
            if (ruRow.acts) { var rs = ruStep(polGroup(rv.via)); rt = rs.exit ? { step: "exit", exit: rs.exit } : { step: "last_" + rs.last, exit: null }; }
          }
          if (rt) { ruRow.now = rt; ruDevs[rv.via] = (ruDevs[rv.via] || 0) + 1; }
        }
        rows.push({ id: d.id, name: d.name, kind: d.kind, platform: d.platform, enabled: d.enabled !== false, active: active, login: d.login, access: d.access.slice(),
          missing: d.access.filter(function (k) { return installed.indexOf(k) < 0; }), ips: clone(d.ips), route: d.route ? clone(d.route) : null, rules: d.rules || null,
          effective: { via: eff.via, fail: eff.fail, from: eff.from }, profile: prof, profile_from: pof.from, now: now, suits: null,
          ru_via: ruOwn(d), ru: ruRow });
        if (rt) edges.push({ id: "ruvia:" + d.id, type: "ru_via", source: u.id, sourceHandle: d.id, target: "rug:" + rv.via, targetHandle: "in",
          data: { device: d.id, user: u.id, group: rv.via, from: rv.from, rules: "rules:ru_direct", step: rt.step, exit: rt.exit } });
        d.access.forEach(function (k) {
          if (installed.indexOf(k) < 0) return;
          edges.push({ id: "access:" + d.id + ":" + k, type: "access", source: u.id, sourceHandle: d.id, target: "in:" + k, targetHandle: "in",
            data: { device: d.id, kind: k, ip: ipOf(d, k), enabled: db.gw.enabled && db.inbounds[k].enabled } });
        });
        edges.push({ id: "route:" + d.id, type: "route", source: u.id, sourceHandle: d.id, target: eff.via, targetHandle: "in",
          data: { device: d.id, user: u.id, via: eff.via, fail: eff.fail, from: eff.from, inherited: eff.from !== "device", rules: "rules:" + prof, active: active } });
      });
      if (db.layout.orders[u.id]) {                // порядок строк из раскладки (новые — после)
        var rank = db.layout.orders[u.id];
        rows.sort(function (a, b) { var ia = rank.indexOf(a.id), ib = rank.indexOf(b.id); return (ia < 0 ? 1e6 : ia) - (ib < 0 ? 1e6 : ib); });
      }
      nodes.push({ id: u.id, type: "user", position: pos(u.id), data: { name: u.name, enabled: u.enabled !== false, route: u.route ? clone(u.route) : null, rules: u.rules || null,
        profile: u.rules || db.rules["default"], profile_from: u.rules ? "user" : "default", devices: rows, ru_via: ruOwn(u) } });
      if (u.route) edges.push({ id: "uroute:" + u.id, type: "user_route", source: u.id, sourceHandle: "user", target: u.route.via, targetHandle: "in",
        data: { user: u.id, via: u.route.via, fail: u.route.fail, rules: "rules:" + (u.rules || db.rules["default"]) } });
    });
    installed.forEach(function (k) {
      var r = db.inbounds[k];
      nodes.push({ id: "in:" + k, type: "inbound", position: pos("in:" + k), data: { kind: k, title: r.title, enabled: db.gw.enabled && r.enabled, ports: r.ports.slice(), subnet: "", adapter: true,
        devices: db.devices.filter(function (d) { return d.access.indexOf(k) >= 0; }).length } });
      edges.push({ id: "flow:" + k, type: "flow", source: "in:" + k, sourceHandle: "out", target: "filter", targetHandle: "in", data: { kind: k } });
    });
    // фильтр узла — общий, до правил (как scheme.build)
    nodes.push({ id: "filter", type: "filter", position: pos("filter"), data: { ads: !!db.rules.block_ads, torrent: !!db.rules.block_torrent, lists: !!db.rules.lists,
      rows: [{ id: "ads", title: "Реклама и телеметрия", on: !!db.rules.block_ads, target: "block" }, { id: "torrent", title: "Торренты", on: !!db.rules.block_torrent, target: "block" },
        { id: "pass", title: "Остальное", on: true, target: "rules" }] } });
    // реклама и торренты отсеивает сам фильтр: линии в «Блок» нет (как scheme.build, ревизия М2)
    var titles = PROFILE_TITLES, ru = ruState();
    ["all_exit", "ru_direct"].forEach(function (p) {
      var cats = p === "ru_direct" ? [{ id: "ru", title: "Сайты и сети России", target: "direct" }, { id: "rest", title: "Остальное", note: "и YouTube, Telegram, GitHub, Google Play — всегда, даже с российским адресом", target: "route" }]
        : [{ id: "rest", title: "Весь трафик", target: "route" }];
      nodes.push({ id: "rules:" + p, type: "rules", position: pos("rules:" + p), data: { profile: p, title: titles[p], devices: profCount[p] || 0, in_use: !!profCount[p], supported: true, "default": p === db.rules["default"],
        lists: p === "ru_direct" ? !!db.rules.lists : null, ru: p === "ru_direct" ? clone(ru) : null, categories: cats } });
      edges.push({ id: "pass:" + p, type: "pass", source: "filter", sourceHandle: "pass", target: "rules:" + p, targetHandle: "in", data: { profile: p } });
      if (p === "ru_direct") edges.push({ id: "cat:ru_direct:ru", type: "rule", source: "rules:ru_direct", sourceHandle: "ru", target: "direct", targetHandle: "in", data: { category: "ru" } });
    });
    nodes.push({ id: "pool", type: "pool", position: pos("pool"), data: { exits: pex.map(function (e) { return e.id; }), total: pex.length, usable: usable.length,
      alive: pex.filter(function (e) { return alive[e.id]; }).length, default: droute.via === "pool", devices: counts.pool || 0,
      add_menu: addHint().menu, add_what: addHint().what, offline: offlineNow() } });
    pex.forEach(function (e) {
      var row = Object.assign(exitRow(e), { alive: !!alive[e.id], usable: usable.indexOf(e) >= 0, missing: false, default: droute.via === "exit:" + e.id, devices: counts["exit:" + e.id] || 0, now_devices: nowCounts["exit:" + e.id] || 0 });
      delete row.text;
      nodes.push({ id: "exit:" + e.id, type: "exit", position: pos("exit:" + e.id), data: row });
      edges.push({ id: "member:" + e.id, type: "member", source: "pool", sourceHandle: e.id, target: "exit:" + e.id, targetHandle: "in", data: { exit: e.id, state: e.state, enabled: e.enabled, usable: row.usable, problem: e.problem || null } });
    });
    ["direct", "block"].forEach(function (k) { nodes.push({ id: k, type: k, position: pos(k), data: { default: droute.via === k, devices: counts[k] || 0, now_devices: nowCounts[k] || 0 } }); });
    // российские группы (scheme._ru_node): ключи по шагам внутри, «сейчас» — шаг группы (ruStep), линия правил → группа
    rug.forEach(function (g) {
      var st = ruStep(g), ids = [].concat.apply([], g.tiers.map(function (t) { return t.exits; }));
      var rows = [];
      g.tiers.forEach(function (t, i) { t.exits.forEach(function (id) { var e = find(db.exits, id);
        rows.push(e ? { id: id, name: e.name, kind: e.kind, proto: e.proto || "", step: i + 1, tier: t.id, missing: false, enabled: e.enabled !== false, usable: e.enabled !== false && e.state === "alive", state: e.state, problem: e.problem || null }
          : { id: id, name: id, kind: "missing", proto: "", step: i + 1, tier: t.id, missing: true, enabled: true, usable: false, state: "missing", problem: null }); }); });
      nodes.push({ id: "rug:" + g.id, type: "ru_group", position: pos("rug:" + g.id), data: { group: g.id, name: grpLabel(g), fail: g.fail === "block" ? "block" : "direct", exits: rows,
        now: st.exit ? { step: "exit", exit: st.exit, since: null, empty: false } : { step: "last_" + st.last, exit: null, since: st.since, empty: !ids.length },
        devices: ruDevs[g.id] || 0, chosen: ruChosen[g.id] || 0 } });
      edges.push({ id: "rulink:" + g.id, type: "ru_link", source: "rules:ru_direct", sourceHandle: "ru", target: "rug:" + g.id, targetHandle: "in", data: { group: g.id } });
    });
    // последние звенья пути: строка «остальное» профиля → цель маршрута или → цель отказа (все возможные)
    var vias = ["pool"].concat(pex.map(function (e) { return "exit:" + e.id; }), ["direct", "block"]);
    ["all_exit", "ru_direct"].forEach(function (p) {
      vias.forEach(function (via) {
        edges.push({ id: "route:" + p + ":" + via, type: "route_link", source: "rules:" + p, sourceHandle: "rest", target: via, targetHandle: "in", data: { profile: p, via: via } });
        if (via !== "direct" && via !== "block") ["direct", "block"].forEach(function (f) {
          edges.push({ id: "fail:" + p + ":" + via + ":" + f, type: "fail", source: "rules:" + p, sourceHandle: "rest", target: f, targetHandle: "in", data: { profile: p, via: via, fail: f } });
        });
      });
    });
    var l = liveDoc();
    var kinds = {};
    INBOUND_DEFS.forEach(function (x) { kinds[x[0]] = x[1]; });
    return { ok: true, version: "mock" + db.gen, layout_version: db.layout.version, gateway: { enabled: db.gw.enabled, engine: "nft", host: db.gw.host }, default_route: clone(droute),
      profiles: { supported: true, names: ["all_exit", "ru_direct"], titles: titles, "default": db.rules["default"], lists: !!db.rules.lists, ru: ru, soon: "",
        rf: { mode: rfMode(), required: !db.rf.consent, uncovered: db.rf.consent ? db.rf.uncovered.slice() : [], need: rfNeedData() } }, kinds: kinds, orphans: [], nodes: nodes, edges: edges,
      // как scheme.live_part(): без свежих данных сборщика — null
      live: db.stale ? null : { ts: l.ts, seq: null, devices: l.devices, exits: l.exits, inbounds: l.inbounds, groups: l.groups || {} } };
  }
  function hashOf(s) { var h = 7; String(s || "").split("").forEach(function (c) { h = (h * 31 + c.charCodeAt(0)) % 100003; }); return h; }
  function bump() { db.gen += 1; Sse.emit("state", { gen: db.gen, parts: ["state"] }); }

  // ── «Применить» схемы (kaskad.panel.scheme.apply): намерения по порядку, сухой прогон — без изменений ──────────
  var VIA_TEXT = { pool: "пул", direct: "напрямую", block: "блок" };
  function routeText(r) { var x = viaText(r.via); return r.via === "direct" || r.via === "block" ? x : x + " (при отказе — " + (r.fail === "direct" ? "напрямую, мимо выхода" : "блок") + ")"; }
  function viaText(via) { if (VIA_TEXT[via]) return VIA_TEXT[via]; var e = find(db.exits, String(via).slice(5)); return "выход «" + (e ? e.name : via) + "»"; }
  function devLabel(d) { var u = find(db.users, d.user) || {}; return "«" + d.name + "» (" + (u.name || "без пользователя") + ")"; }
  function checkVia(via) {
    if (VIA_TEXT[via]) return;
    var m = /^exit:(e-[0-9a-f]{10})$/.exec(String(via));
    if (!m) throw httpErr(400, "маршрут должен быть pool, direct, block или exit:<id выхода>, а не '" + via + "'", 2);
    if (!find(db.exits, m[1])) throw httpErr(409, "Выхода " + m[1] + " нет в пуле «Прокси для API Telegram» — выберите другой.", 3);
  }
  // одно намерение на копии хранилища: {type, changed, text, warnings} или исключение (как _run_intent)
  function runIntent(D, it) {
    var t = String(it.type || ""), d, u, out;
    if (t === "grant" || t === "revoke") {
      d = find(D.devices, it.device);
      if (!d) throw httpErr(400, "Устройства " + it.device + " нет.", 2);
      var r = D.inbounds[it.kind];
      if (!r) throw httpErr(400, "неизвестный вход '" + it.kind + "'", 2);
      var had = d.access.indexOf(it.kind) >= 0;
      if (t === "grant") {
        if (!r.installed) throw httpErr(409, "Протокол " + r.title + " не установлен на узле — сначала установите его в «Протоколах».", 3);
        if (!had) d.access.push(it.kind);
        return { type: t, device: d.id, kind: it.kind, changed: !had, warnings: r.enabled ? [] : ["протокол " + r.title + " выключен — устройство подключится, когда его включат"],
          text: devLabel(d) + ": " + (had ? "уже есть" : "+") + " протокол " + r.title };
      }
      d.access = d.access.filter(function (k) { return k !== it.kind; });
      return { type: t, device: d.id, kind: it.kind, changed: had, warnings: had && !d.access.length ? ["у устройства не останется ни одного протокола"] : [],
        text: devLabel(d) + ": " + (had ? "− протокол " + r.title + ", ключи и учётки протокола удаляются" : "протокола " + r.title + " и так не было") };
    }
    if (t === "route") {
      var via = it.via === undefined || it.via === null || it.via === "" ? "inherit" : String(it.via);
      if (it["default"]) {
        if (via === "inherit") throw httpErr(400, "У маршрута по умолчанию нет «наследования»: выберите пул, выход, напрямую или блок.", 2);
        checkVia(via);
        var before = JSON.stringify(D.gw.route);
        D.gw.route = { via: via, fail: it.fail || D.gw.route.fail || "block" };          // как model.kept_fail
        return { type: t, "default": true, changed: before !== JSON.stringify(D.gw.route), route: clone(D.gw.route), warnings: [], text: "Маршрут по умолчанию: " + routeText(D.gw.route) };
      }
      var obj = it.device ? find(D.devices, it.device) : find(D.users, it.user);
      if (!obj) throw httpErr(400, it.device ? "Устройства " + it.device + " нет." : "Пользователя " + it.user + " нет.", 2);
      var what = it.device ? "device" : "user", label = it.device ? devLabel(obj) : obj.name;
      var was = JSON.stringify(obj.route), eb = effOf(D, what, obj);
      if (via === "inherit") obj.route = null;
      // новая цель сохраняет политику отказа — свою, иначе унаследованную (как model.kept_fail, T-scheme-3.2.15 Н1)
      else { checkVia(via); obj.route = { via: via, fail: it.fail || (obj.route && obj.route.fail) || inheritedFail(D, obj, !!it.device) }; }
      var ch = routeChange(eb, effOf(D, what, obj), !!obj.route, what, label);
      out = { type: t, changed: was !== JSON.stringify(obj.route), route: obj.route ? clone(obj.route) : null, text: ch.text, same_path: ch.same, warnings: [] };
      if (was === "null" && !obj.route) out.text = label + ": своего маршрута и так нет — " + eb.src + ": " + routeText(eb.r);
      // как scheme._leak_warnings: мимо выходов — сказать до «Применить», если путь правда становится таким (Н-7)
      if (out.changed && obj.route && !ch.same) {
        if (obj.route.via === "direct") out.warnings.push(label + ": маршрут «напрямую» — трафик пойдёт с адреса узла, мимо выходов");
        else if (obj.route.via !== "block" && obj.route.fail === "direct") out.warnings.push(label + ": если выходы перестанут работать, трафик пойдёт с адреса узла мимо выхода — и YouTube, Telegram тоже");
      }
      out[what] = obj.id;
      return out;
    }
    if (t === "rules") {
      var prof = String(it.profile || "inherit");
      if (["inherit", "all_exit", "ru_direct"].indexOf(prof) < 0) throw httpErr(400, "профиль правил — один из: all_exit, ru_direct, inherit", 2);
      u = it.device ? find(D.devices, it.device) : find(D.users, it.user);
      if (!u) throw httpErr(400, "rules: укажите device или user.", 2);
      var pb = u.rules || null;
      u.rules = prof === "inherit" ? null : prof;
      out = { type: t, changed: pb !== u.rules, profile: prof, warnings: [], text: (it.device ? devLabel(u) : u.name) + ": правила → " + (prof === "inherit" ? inheritedRules(D, u, !!it.device) : "«" + PROFILE_TITLES[prof] + "»") };
      out[it.device ? "device" : "user"] = u.id;
      return out;
    }
    if (t === "ru_via") {                         // как scheme._run_ru_via: inherit | direct | российская группа
      var rv = it.via === undefined || it.via === null || it.via === "" ? "inherit" : it.via;
      if (typeof rv !== "string" || (rv !== "inherit" && rv !== "direct" && !/^(main|g-[0-9a-f]{6})$/.test(rv))) throw httpErr(400, "внутренняя группа — inherit, direct или id внутренней группы, а не " + JSON.stringify(rv), 2);
      var rgp = rv === "inherit" || rv === "direct" ? null : polGroup(rv);
      if (rv !== "inherit" && rv !== "direct" && !rgp) throw httpErr(400, "Внутренней группы " + rv + " нет", 2);
      if (rgp && rgp.role !== "ru") throw httpErr(400, "Группа «" + grpLabel(rgp) + "» — зарубежная: для российских сайтов можно выбрать только внутреннюю группу", 2);
      u = it.device ? find(D.devices, it.device) : find(D.users, it.user);
      if (!u) throw httpErr(400, it.device ? "Устройства " + it.device + " нет." : it.user ? "Пользователя " + it.user + " нет." : "ru_via: укажите device или user.", 2);
      var vb = ruOwn(u);
      if (rv === "inherit") delete u.ru_via; else u.ru_via = rv;
      var ruTxt = function (v) { return v === "direct" ? "напрямую с сервера" : "через «" + grpLabel(polGroup(v) || { id: v }) + "»"; };
      var uo = it.device ? ruOwn(find(D.users, u.user) || {}) : null;
      out = { type: t, changed: vb !== ruOwn(u), ru_via: rv, warnings: [],
        text: (it.device ? devLabel(u) : u.name) + ": российские сайты → " + (ruOwn(u) ? ruTxt(ruOwn(u)) : uo ? "как у пользователя: " + ruTxt(uo) : "по умолчанию: напрямую с сервера") };
      out[it.device ? "device" : "user"] = u.id;
      return out;
    }
    throw httpErr(400, "неизвестное изменение '" + t + "': grant, revoke, route, rules или ru_via", 2);
  }
  // как scheme._eff_of: действующий маршрут и откуда он — {r, src}
  function effOf(D, what, obj) {
    if (what === "device") {
      if (obj.route) return { r: obj.route, src: "свой" };
      var u = find(D.users, obj.user) || {};
      return u.route ? { r: u.route, src: "как у пользователя «" + (u.name || u.id || "") + "»" } : { r: D.gw.route, src: "по умолчанию узла" };
    }
    return obj.route ? { r: obj.route, src: "свой" } : { r: D.gw.route, src: "по умолчанию узла" };
  }
  // как scheme._route_change: «было → станет»; свой = унаследованному путь не меняет (Н-7)
  function routeChange(b, a, own, what, label) {
    var same = (b.r.via === a.r.via && (b.r.fail || "block") === (a.r.fail || "block")) || (b.r.via === a.r.via && (a.r.via === "direct" || a.r.via === "block"));
    var whom = what === "device" ? "за устройством" : "за пользователем";
    if (own && same) return { same: true, text: label + ": путь не меняется — " + routeText(a.r) + "; маршрут закрепляется " + whom };
    if (own) return { same: same, text: label + ": маршрут " + b.src + ": " + routeText(b.r) + " → свой: " + routeText(a.r) };
    return { same: same, text: label + ": свой маршрут снят → " + a.src + ": " + routeText(a.r) + (same ? " — путь не меняется" : "") };
  }
  // как scheme._inherited_rules_text: что станет вместо снятого своего профиля
  function inheritedRules(D, obj, isDev) {
    if (isDev) { var p = profileOf(D, obj); if (p.from === "user") return "как у пользователя: «" + PROFILE_TITLES[p.profile] + "»"; }
    return "по умолчанию узла: «" + PROFILE_TITLES[db.rules["default"]] + "»";
  }
  // как scheme._affected: путь (маршрут, отказ, действующий профиль) и подключение (выданный или отозванный вход)
  function affectedOf(D) {
    var out = [], ready = !!db.rules.lists;
    var applied = function (S, d) { var p = profileOf(S, d).profile; return p === "ru_direct" && !ready ? "all_exit" : p; };
    var routeIn = function (S, d) {
      var u = find(S.users, d.user) || {};
      return d.route ? d.route : u.route ? u.route : S.gw.route;
    };
    // как scheme._route_text: политика отказа — всегда, где она действует
    var rtext = function (r) { var x = viaText(r.via); return r.via === "direct" || r.via === "block" ? x : x + " (при отказе — " + (r.fail === "direct" ? "напрямую, мимо выхода" : "блок") + ")"; };
    var ptext = function (p, r, withRules) { return withRules && p ? "правила «" + PROFILE_TITLES[p] + "», маршрут: " + rtext(r) : rtext(r); };
    var leaks = function (r) { return r.via === "direct" ? "always" : r.via !== "block" && r.fail === "direct" ? "fail" : ""; };
    // российские сайты (scheme._ru_target_of): null — «Россия напрямую» не действует, direct или российская группа
    var ruT = function (S, d) {
      var u = find(S.users, d.user) || {};
      if (!RUG || applied(S, d) !== "ru_direct" || !rfAllowed() || d.enabled === false || u.enabled === false || routeIn(S, d).via === "block") return null;
      var v = ruOwn(d) || ruOwn(u) || "direct", g = v === "direct" ? null : polGroup(v);
      return g && g.role === "ru" ? v : "direct";
    };
    var rtx = function (v) { return v === null ? "российские сайты — по маршруту" : "российские сайты — " + (v === "direct" ? "напрямую с сервера" : "через «" + grpLabel(polGroup(v)) + "»"); };
    D.devices.forEach(function (d) {
      var o = find(db.devices, d.id); if (!o) return;
      var qb = ruT(db, o), qa = ruT(D, d);
      if (qb !== qa && ((qb && qb !== "direct") || (qa && qa !== "direct"))) out.push({ kind: "ru", device: d.id, name: d.name, before: rtx(qb), after: rtx(qa), from: ruOwn(d) ? "device" : "user" });
      var rb = routeIn(db, o), ra = routeIn(D, d);
      // при «напрямую» и «блоке» профиль путь не меняет (scheme._profile_on)
      var pb = rb.via === "direct" || rb.via === "block" ? null : applied(db, o), pa = ra.via === "direct" || ra.via === "block" ? null : applied(D, d);
      var moved = rb.via !== ra.via || (rb.fail || "block") !== (ra.fail || "block");
      if (moved || pb !== pa) {
        var withRules = pb !== pa;
        var lk = leaks(ra);
        out.push({ kind: "path", device: d.id, name: d.name, before: ptext(pb, rb, withRules), after: ptext(pa, ra, withRules),
          from: d.route ? "device" : (find(D.users, d.user) || {}).route ? "user" : "default", profile_before: pb, profile_after: pa, leak: lk && lk !== leaks(rb) ? lk : "" });
      }
      d.access.forEach(function (k) {
        if (o.access.indexOf(k) < 0) out.push({ kind: "access", op: "+", device: d.id, name: d.name, inbound: k, text: "новый протокол " + db.inbounds[k].title + " — выдайте устройству его настройки («Пользователи и устройства» → «Выдать»)" });
      });
      o.access.forEach(function (k) {
        if (d.access.indexOf(k) >= 0) return;
        var text = "протокол " + db.inbounds[k].title + " отзывается: его ключи удаляются, настройки на устройстве перестанут работать";
        if (o._live && o._live.online && o._live.inbound === k) text += " — устройство сейчас на связи через него, связь оборвётся";
        if (!d.access.length) text += "; других протоколов у устройства не останется";
        out.push({ kind: "access", op: "-", device: d.id, name: d.name, inbound: k, text: text });
      });
    });
    return out;
  }
  // как scheme.disruptions: что изменение перезапустит у ВСЕХ — xray шлюза (учётки и маршруты SOCKS5) и DNS шлюза
  function disruptionsOf(D) {
    if (!db.gw.enabled) return [];
    var routeIn = function (S, d) { var u = find(S.users, d.user) || {}; return d.route ? d.route : u.route ? u.route : S.gw.route; };
    var xray = function (d) { return d.access.filter(function (k) { return XRAY_KINDS.indexOf(k) >= 0; }); };
    var socks = function (S) { return JSON.stringify(S.devices.filter(function (d) { return xray(d).length; }).map(function (d) { var r = routeIn(S, d); return [d.id, xray(d), r.via, r.fail || "block", profileOf(S, d).profile]; })); };
    var direct = function (S) { return S.devices.some(function (d) { var r = routeIn(S, d); return d.access.some(function (k) { return XRAY_KINDS.indexOf(k) < 0; }) && (r.via === "direct" || (r.via !== "block" && r.fail === "direct")); }); };
    var out = [];
    if (socks(db) !== socks(D)) {
      var n = db.devices.filter(function (d) { return d._live && d._live.online && XRAY_KINDS.indexOf(d._live.inbound) >= 0; }).length;
      out.push({ what: "xray", devices: n, text: "Перезапустится xray шлюза (учётки или маршруты SOCKS5 и VLESS): переподключатся все соединения через SOCKS5, протокол VLESS и выходы VLESS" + (n ? " — сейчас так на связи устройств: " + n : "") });
    }
    if (direct(db) !== direct(D)) out.push({ what: "dns", devices: null, text: "Перезапустится DNS шлюза: на секунду-другую имена сайтов не будут находиться у всех устройств" });
    return out;
  }
  function inheritedFail(S, obj, isDev) {
    var u = isDev ? find(S.users, obj.user) : null;
    return (u && u.route ? u.route.fail : S.gw.route.fail) || "block";
  }
  // все намерения по порядку на копии; сухой прогон собирает все ошибки сразу
  function passIntents(intents) {
    var D = { devices: clone(db.devices), users: clone(db.users), gw: clone(db.gw), inbounds: clone(db.inbounds) };
    var results = intents.map(function (it, i) {
      try { return Object.assign(runIntent(D, it), { i: i, ok: true }); }
      catch (e) { return { i: i, type: String(it.type || ""), ok: false, error: e.message, code: e.code === undefined ? 1 : e.code }; }
    });
    var changes = results.filter(function (r) { return r.ok && r.changed; }).map(function (r) {
      return { op: r.type === "grant" ? "+" : r.type === "revoke" ? "-" : "~", what: r.type === "grant" || r.type === "revoke" ? "access" : r.type === "route" && r["default"] ? "default" : r.type, text: r.text };
    });
    var aff = affectedOf(D), warns = [].concat.apply([], results.map(function (r) { return r.warnings || []; }));
    // как scheme.dry_run: путь стал уходить мимо выходов по наследству (снят свой маршрут, сменился у человека) — сказать
    aff.forEach(function (a) { if (a.leak && a.from !== "device") warns.push("«" + a.name + "»: теперь " + a.after + (a.leak === "always" ? " — трафик пойдёт с адреса узла, мимо выходов" : " — если выходы перестанут работать, трафик пойдёт с адреса узла мимо выхода")); });
    return { D: D, results: results, changes: changes, affected: aff, disruptions: disruptionsOf(D), warnings: warns };
  }
  function schemeApply(body) {
    var intents = body.intents;
    if (!Array.isArray(intents) || !intents.length) throw httpErr(400, "Нечего применять: intents — непустой список изменений.", 2);
    var version = "mock" + db.gen;
    var pre = passIntents(intents);
    var valid = pre.results.every(function (r) { return r.ok; });
    // ⚠РФ (scheme._rf_need): новый «Россия напрямую» без согласия и без галочки — results[].need, ok остаётся true
    var tick = intents.some(function (it) { return it.type === "rules" && it.confirm_not_rf === true; });
    var fresh = pre.results.filter(function (r) { return r.ok && r.type === "rules" && r.profile === "ru_direct" && r.changed; });
    if (fresh.length && !db.rf.consent && !tick) fresh.forEach(function (r) { r.need = rfNeedData(); });
    if (body.dry_run) return { ok: true, dry_run: true, valid: valid, version: version, noop: !pre.changes.length, results: pre.results, changes: pre.changes, affected: pre.affected, disruptions: pre.disruptions, warnings: pre.warnings };
    if (db.applyStale) { db.applyStale = false; bump(); version = "mock" + db.gen; }      // apply=stale: «бот» успел изменить настройки
    if (body.version !== undefined && body.version !== version) throw httpErr(409, "Пока вы правили схему, настройки изменились (другая вкладка, бот или меню) — проверьте изменения ещё раз.", 3, { reason: "stale", version: version });
    if (!valid) { var bad = pre.results.filter(function (r) { return !r.ok; })[0]; throw httpErr(409, "Ничего не применено: " + bad.error, bad.code || 3, { results: pre.results }); }
    if (!pre.changes.length) return { ok: true, applied: false, results: pre.results, changes: [], affected: [], warnings: pre.warnings, graph: schemeDoc() };
    rfCheck(fresh.length > 0, tick ? { confirm_not_rf: true, via: "scheme" } : null);
    db.devices = pre.D.devices; db.users = pre.D.users; db.gw = pre.D.gw;
    rfReset();
    bump();
    return { ok: true, applied: true, results: pre.results, changes: pre.changes, affected: pre.affected, warnings: pre.warnings, gateway_applied: db.gw.enabled, graph: schemeDoc() };
  }
  // PUT scheme/layout — только поверх версии, которую видела вкладка; иначе 409 с тем, что сохранено (scheme.save_layout)
  function saveLayout(body) {
    var L = db.layout;
    if (typeof body.version !== "number") throw httpErr(400, "version — целое число (версия раскладки, которую видела вкладка)", 2);
    if (db.layoutConflict) { db.layoutConflict = false; L.version += 1; }        // layout=conflict: «другая вкладка» сохранила раньше
    if (body.version !== L.version) throw httpErr(409, "Раскладку схемы только что сохранила другая вкладка — взял её и повторяю.", 3, { version: L.version, positions: clone(L.positions), orders: clone(L.orders) });
    if (body.reset) { L.positions = {}; L.orders = {}; }
    Object.keys(body.positions || {}).forEach(function (k) { L.positions[k] = body.positions[k]; });
    Object.keys(body.orders || {}).forEach(function (k) { L.orders[k] = body.orders[k]; });
    L.version += 1;
    return { ok: true, version: L.version };
  }
  function fakeB64(n) { var a = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_", s = ""; for (var i = 0; i < n; i++) s += a[(i * 7 + (i >> 3) * 13) % a.length]; return s; }
  // .conf AmneziaWG настоящей длины (3.3.3 №38: QR файла 3.1 — версия 19, плотнее порога камеры): как awgkeys.render_conf,
  // ключи выдуманные (не ключ)
  function awgConfText(line, ip, host, port) {
    var k = function (c) { return new Array(44).join(c) + "="; };
    var s = ["[Interface]", "Address = " + ip + "/32", "DNS = 10.66.0.1", "PrivateKey = " + k("P"), "MTU = 1280", "Jc = 4", "Jmin = 10", "Jmax = 50"];
    if (line === "3.1") s = s.concat(["S1 = 12", "S2 = 12", "S3 = 12", "S4 = 12", "H1 = 1", "H2 = 2", "H3 = 3", "H4 = 4"]);
    else if (line === "2.0") s = s.concat(["S1 = 61", "S2 = 116", "S3 = 23", "S4 = 9", "H1 = 412736819-512736819", "H2 = 1312736819-1412736819", "H3 = 2212736819-2312736819", "H4 = 3112736819-3212736819"]);
    else s = s.concat(["S1 = 61", "S2 = 116", "H1 = 1412736819", "H2 = 2312736819", "H3 = 3212736819", "H4 = 4012736819"]);
    s.push("I1 = <r 2><b 0x858000010001000000000669636c6f756403636f6d0000010001c00c000100010000105a00044d583737>");
    if (line === "3.1") s = s.concat(["HeaderProtectionKey = " + k("D"), "RekeyAfterTime = 100-120", "RekeyTimeout = 3-7", "RejectAfterTime = 150-180",
      "KeepaliveTimeout = 5-15", "MaxHandshakeAttempts = 15-20", "RandomTrailers = on", "DisableCookies = on"]);
    s = s.concat(["", "[Peer]", "PublicKey = " + k("K"), "PresharedKey = " + k("S"), "AllowedIPs = 0.0.0.0/0, ::/0", "Endpoint = " + host + ":" + port,
      "PersistentKeepalive = " + (line === "3.1" ? "25-35" : "25")]);
    return s.join("\n") + "\n";
  }
  // части как awg.py с 3.3.3 (qr.part_size): сжатый ключ настоящей длины (3.1 ≈1150 байт, 2.0 ≈960, 1.5 ≈905) делится
  // на одну или две части одного размера (qr.MAX_PARTS: владелец, «не более 2 частей»; одна — если влезает в версию 21)
  var VPN_BYTES = { "3.1": 1150, "2.0": 960, "1.5": 905 };
  function partsFor(line) {
    var total = VPN_BYTES[line] || 905, room = Math.floor(929 * 3 / 4) - 8, n = total <= room ? 1 : 2;
    return { n: n, len: Math.ceil(total / n) };
  }
  // части ключа для QR, как vendor/awgkeys.qr_chunks_from_bytes: заголовок «>hBBI» (1984, число частей, номер, длина)
  // + кусок (выдуманный), base64url — страница узнаёт в них части AmneziaVPN (qr-slides.js); размер — partsFor
  function qrParts(n, len) {
    var out = [], i, j, bin, x = 7;
    len = len || 300;
    for (i = 0; i < n; i++) {
      bin = String.fromCharCode(7, 192, n, i, 0, 0, len >> 8, len & 255);
      for (j = 0; j < len; j++) { x = (x * 1103 + 12345) % 65521; bin += String.fromCharCode(x & 255); }
      out.push(btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, ""));
    }
    return out;
  }
  function issueDoc(d, body) {
    var avail = d.access.filter(function (k) { var r = db.inbounds[k]; return r && r.installed && r.enabled; });
    if (!avail.length) throw httpErr(400, "У устройства нет рабочих протоколов: выдайте протокол и установите его.", 1);
    var kind = body.inbound && avail.indexOf(body.inbound) >= 0 ? body.inbound : avail[0];
    var os = body.os || d.platform;
    var ROUTER = ["keenetic", "mikrotik", "openwrt", "tplink", "huawei", "xiaomi", "asus"].indexOf(os) >= 0;
    var SHORT = ["android", "androidtv", "linux", "other"].indexOf(os) >= 0;
    // имя файла как issue_os.conf_filename: goKaskadPro-<имя транслитом> (Android, Android TV, Linux — gKP-, до 15)
    var slug = mockTranslit(d.name), fname = function (sfx) {
      var base = SHORT ? "gKP-" : "goKaskadPro-", lim = SHORT ? 15 : 32, multi = d.access.filter(function (k) { return k === "wg" || k.indexOf("awg") === 0; }).length > 1;
      var tail = multi ? "-" + sfx : "", room = lim - base.length - tail.length;
      return base + (slug.slice(0, room).replace(/-+$/, "") || sfx) + tail + ".conf";
    };
    var conf = "[Interface]\nPrivateKey = MOCK+private+key+not+real+" + d.id + "=\nAddress = " + (d.ips.wg || d.ips[Object.keys(d.ips)[0]] || "10.77.1.10") + "/32\nDNS = 10.77.0.1\n\n[Peer]\nPublicKey = MOCK+server+public+key+not+real=\nEndpoint = " + db.gw.host + ":51820\nAllowedIPs = 0.0.0.0/0\n";
    var items;
    if (kind === "wg" && os === "mikrotik") items = [rec("conf", "WireGuard — скрипт RouterOS 7", "/interface wireguard add name=kaskad-wg listen-port=13231 private-key=\"MOCK+private+key+not+real=\"\n/interface wireguard peers add interface=kaskad-wg public-key=\"MOCK+server+public+key+not+real=\" endpoint-address=" + db.gw.host + " endpoint-port=51820 allowed-address=0.0.0.0/0 persistent-keepalive=25s\n/ip address add address=10.77.1.20/32 interface=kaskad-wg\n/ip route add dst-address=0.0.0.0/0 gateway=kaskad-wg distance=1\n/ip firewall nat add chain=srcnat out-interface=kaskad-wg action=masquerade", "kaskad-wg.rsc", ["New Terminal → вставить; 192.168.88.0/24 замените на свою сеть."]), rec("conf", "WireGuard — файл .conf", conf, fname("wg"), ["RouterOS 7: файл .conf можно не загружать — выше скрипт для терминала."])];
    else if (kind === "wg") items = [rec("qr", "WireGuard — QR-код", [conf], null, ["Для приложения WireGuard на телефоне."]), rec("conf", "WireGuard — файл .conf", conf, fname("wg"), ["Приложение WireGuard: «+» → «Создать из QR-кода»."])];
    else if (kind.indexOf("awg") === 0 && ROUTER) {
      var line = { awg1: "1.5", awg2: "2.0", awg3: "3.1" }[kind], rconf = conf.replace("DNS = ", "Jc = 4\nJmin = 40\nJmax = 70\nS1 = 0\nS2 = 0\nH1 = 1\nDNS = ");
      var rnotes = os === "keenetic" ? ["Интернет → Другие подключения → WireGuard → импорт из файла."].concat(line === "3.1" ? ["Линия 3.1 — только в KeeneticOS 5.2 Alpha 11 и новее (тестовый канал); на основном канале выдайте AmneziaWG 2.0."] : [])
        : os === "mikrotik" ? ["Контейнер amneziawg-mikrotik: .conf → конфигуратор timbrs.github.io."] : os === "openwrt" ? ["Пакеты awg-openwrt; LuCI → Network → Interfaces → «Load configuration…» (в русском LuCI: Сеть → Интерфейсы → «Загрузка конфигурации…»)."] : [];
      items = [rec("conf", "AmneziaWG " + line + " — файл .conf", rconf, fname("awg" + line.replace(".", "")), rnotes)];
    }
    else if (kind.indexOf("awg") === 0) {
      var aline = { awg1: "1.5", awg2: "2.0", awg3: "3.1" }[kind], pf = partsFor(aline);
      var vpn = "vpn://" + fakeB64(Math.ceil(VPN_BYTES[aline] * 4 / 3)), awgConf = awgConfText(aline, d.ips[kind] || "10.77.1.10", db.gw.host, 40443);
      var aw = "AmneziaWG " + aline;     // как gw_in_awg.rec.*: «AmneziaWG {line} — …»
      items = [rec("vpn", aw + " — ссылка vpn://", vpn, null, ["AmneziaVPN: «+» → «Вставить» (ключ из буфера) → «Продолжить»."]), rec("qr", aw + " — QR для AmneziaVPN", qrParts(pf.n, pf.len), null, ["Кодов: " + pf.n + ", сканируйте в любом порядке."]), rec("qr", aw + " — QR файла .conf", [awgConf], null, []), rec("conf", aw + " — файл .conf", awgConf, fname("awg" + { awg1: "15", awg2: "20", awg3: "31" }[kind]), ["Приложение Amnezia теряет поля 3.x при импорте .conf — в нём только vpn:// или его QR."])];
    } else if (kind === "openvpn") items = [rec("ovpn", "OpenVPN — файл .ovpn", "client\ndev tun\nproto udp\nremote " + db.gw.host + " 1194\n<ca>\nMOCK-NOT-A-REAL-CERT\n</ca>\n", "kaskad-openvpn.ovpn", ["OpenVPN Connect: «Import profile»."])];
    else if (kind === "vless") {
      var vr = db.inbounds.vless._record, vtls = vr.security === "tls";
      var link = "vless://3f1c2a9e-7b4d-4e2a-9c1f-mock" + d.id.slice(2) + "@" + (vtls ? vr.domain : db.gw.host) + ":" + vr.port + "?encryption=none&flow=xtls-rprx-vision"
        + (vtls ? "&security=tls&sni=" + vr.domain + "&fp=chrome" : "&security=reality&sni=" + vr.sni + "&fp=chrome&pbk=" + vr.pub + "&sid=" + vr.short_id) + "&type=tcp&headerType=none#Kaskad%20%C2%B7%20" + encodeURIComponent(d.name);
      items = [rec("qr", "VLESS — QR-код", [link], null, ["v2RayTun или Hiddify: «+» → «Сканировать QR»."]), rec("link", "VLESS — ссылка vless://", link, null, ["v2RayTun: «+» → «Импорт из буфера»."]),
        rec("fields", "VLESS — поля для ручной настройки", [["Адрес", db.gw.host], ["Порт", String(vr.port)], ["UUID", "3f1c2a9e-7b4d-4e2a-9c1f-mock" + d.id.slice(2)], ["flow", "xtls-rprx-vision"], ["SNI", vr.sni], ["fingerprint", "chrome"], ["pbk", vr.pub], ["sid", vr.short_id]], null, [])];
    } else if (kind === "socks5") items = [rec("link", "SOCKS5 — адрес", "socks5://" + d.login + ":MockPass1234@" + db.gw.host + ":1080", null, ["Пароль учётки SOCKS5 уходит открытым текстом — своя учётка, не общий пароль."])];
    else items = [rec("fields", d.platform === "windows" ? "Windows — поля для ручного ввода" : "Поля для ручного ввода", [["Сервер", db.gw.host], ["Логин", d.login], ["Пароль", "MockPass1234"], ["Общий ключ IPsec (PSK)", "mock-psk-not-real"]], null, ["Параметры «Другие подключения» → VPN."]), rec("guide", "Инструкция", "1. Откройте «Параметры» → «VPN».\n2. Нажмите «Добавить VPN».\n3. Введите сервер, логин и пароль из полей выше.", null, [])];
    var onSrv = Object.keys(db.inbounds).filter(function (k) { var r = db.inbounds[k]; return r && r.installed && r.enabled; });
    var sup = window.KaskadMockSupport ? window.KaskadMockSupport(kind, os, document.documentElement.lang, onSrv, d.access) : null;
    return { ok: true, device: d.id, name: d.name, kind: kind, os: os, items: items, available: avail, howto: HOWTO[kind + ":" + os] || [],
      // «Установите приложение» — из настоящих каталогов (dev-mock-install.js, tools/gen_mock_install.py)
      install: window.KaskadMockInstall ? window.KaskadMockInstall(kind, os, document.documentElement.lang) : null,
      // таблица поддержки и другие протоколы — из настоящего каталога (KaskadMockSupport), состояние — по макету
      support: sup ? sup.support : null, alternatives: sup ? sup.alternatives : [] };
  }
  // howto выдачи — до четырёх шагов для новичка (issue_os.howto); у встроенных клиентов и Linux — пусто
  // словами панели: ops.issue(where="panel") — «Скопировать», «Скачать» (T-G1b, N4); тексты — issue_os.howto(os, kind, where="panel")
  var HOWTO = {
    "awg3:ios": ["Поставьте приложение DefaultVPN 2.0.1 или новее из App Store (это официальная версия AmneziaVPN для российского App Store; с Apple ID другой страны подойдёт и сам AmneziaVPN).", "Скопируйте ссылку vpn:// ниже кнопкой «Скопировать» — она берёт всю ссылку, до конца.", "Отправьте её себе на телефон — например, в «Избранное» Telegram — и там скопируйте.", "В DefaultVPN: нажмите «+», вставьте ключ из буфера и подключитесь."],
    "awg2:ios": ["Поставьте приложение DefaultVPN из App Store (это официальная версия AmneziaVPN для российского App Store; с Apple ID другой страны подойдёт и сам AmneziaVPN).", "Скопируйте ссылку vpn:// ниже кнопкой «Скопировать» — она берёт всю ссылку, до конца.", "Отправьте её себе на телефон — например, в «Избранное» Telegram — и там скопируйте.", "В DefaultVPN: нажмите «+», вставьте ключ из буфера и подключитесь."],
    "awg1:ios": ["Поставьте приложение DefaultVPN из App Store (это официальная версия AmneziaVPN для российского App Store; с Apple ID другой страны подойдёт и сам AmneziaVPN).", "Скопируйте ссылку vpn:// ниже кнопкой «Скопировать» — она берёт всю ссылку, до конца.", "Отправьте её себе на телефон — например, в «Избранное» Telegram — и там скопируйте.", "В DefaultVPN: нажмите «+», вставьте ключ из буфера и подключитесь."],
    "wg:ios": ["Поставьте приложение WireGuard из App Store.", "В WireGuard нажмите «+» → «Создать из QR-кода».", "Наведите камеру телефона на QR-код ниже.", "Назовите подключение, например gKP-wg, и включите его."],
    "vless:ios": ["Поставьте приложение Karing из App Store.", "В Karing нажмите «+» и отсканируйте QR-код.", "Наведите камеру телефона на QR-код ниже.", "Включите подключение."],
    "openvpn:ios": ["Поставьте приложение OpenVPN Connect из App Store.", "Скачайте файл кнопкой «Скачать» ниже.", "Перенесите файл .ovpn на телефон (на телефон — например, через «Избранное» Telegram) и откройте его в OpenVPN Connect.", "Нажмите «Connect» (подписи OpenVPN Connect — на английском)."],
    "ikev2:ios": ["Скачайте профиль kaskad-ikev2.mobileconfig кнопкой «Скачать» ниже.", "Перенесите файл на iPhone или iPad (AirDrop с Mac или «Файлы» → iCloud Drive) и откройте его.", "Настройки → «Профиль загружен» → «Установить» (iPhone спросит код-пароль).", "Настройки → VPN → «Kaskad IKEv2» — включите."],
    "awg3:macos": ["Поставьте приложение AmneziaVPN 5.0.1.5 или новее с сайта amnezia.org/downloads.", "Скопируйте ссылку vpn:// ниже кнопкой «Скопировать» — она берёт всю ссылку, до конца.", "Отправьте её себе на компьютер — например, в «Избранное» Telegram — и там скопируйте.", "В AmneziaVPN: «+» → «Вставить» (ключ из буфера) → «Продолжить», затем «Подключиться»."],
    "awg2:macos": ["Поставьте приложение AmneziaVPN 4.8.12.9 или новее с сайта amnezia.org/downloads.", "Скопируйте ссылку vpn:// ниже кнопкой «Скопировать» — она берёт всю ссылку, до конца.", "Отправьте её себе на компьютер — например, в «Избранное» Telegram — и там скопируйте.", "В AmneziaVPN: «+» → «Вставить» (ключ из буфера) → «Продолжить», затем «Подключиться»."],
    "awg1:macos": ["Поставьте приложение AmneziaVPN 4.8.8.3 или новее с сайта amnezia.org/downloads.", "Скопируйте ссылку vpn:// ниже кнопкой «Скопировать» — она берёт всю ссылку, до конца.", "Отправьте её себе на компьютер — например, в «Избранное» Telegram — и там скопируйте.", "В AmneziaVPN: «+» → «Вставить» (ключ из буфера) → «Продолжить», затем «Подключиться»."],
    "wg:macos": ["Поставьте WireGuard из App Store.", "Скопируйте текст файла .conf кнопкой «Скопировать» ниже.", "В WireGuard: «Добавить пустой туннель…», сотрите текст в окне, вставьте скопированный и сохраните.", "Включите туннель."],
    "vless:macos": ["Поставьте приложение Karing из App Store.", "Скопируйте ссылку vless:// ниже кнопкой «Скопировать» — она берёт всю ссылку, до конца.", "В Karing: нажмите «+» и импортируйте ссылку из буфера обмена (в v2rayN — Ctrl+V в списке серверов), затем подключитесь."],
    "openvpn:macos": ["Поставьте приложение OpenVPN Connect с сайта openvpn.net/client.", "Скачайте файл кнопкой «Скачать» ниже.", "Перенесите файл .ovpn на компьютер (на телефон — например, через «Избранное» Telegram) и откройте его в OpenVPN Connect.", "Нажмите «Connect» (подписи OpenVPN Connect — на английском)."],
    "ikev2:macos": ["Скачайте профиль kaskad-ikev2.mobileconfig кнопкой «Скачать» ниже.", "Откройте файл двойным щелчком → Системные настройки → Основные → Управление устройством (macOS 13–14: Конфиденциальность и безопасность → Профили) → в разделе «Загружено» дважды нажмите профиль → «Установить».", "Системные настройки → VPN → «Kaskad IKEv2» — включите."],
    "awg3:android": ["Поставьте приложение AmneziaVPN 5.0.1.5 или новее из Google Play (нет Google Play — файл APK: github.com/amnezia-vpn/amnezia-client/releases).", "Скопируйте ссылку vpn:// ниже кнопкой «Скопировать» — она берёт всю ссылку, до конца.", "Отправьте её себе на телефон — например, в «Избранное» Telegram — и там скопируйте.", "В AmneziaVPN: «+» → «Вставить» (ключ из буфера) → «Продолжить», затем «Подключиться»."],
    "awg2:android": ["Поставьте приложение AmneziaVPN 4.8.12.9 или новее из Google Play (нет Google Play — файл APK: github.com/amnezia-vpn/amnezia-client/releases).", "Скопируйте ссылку vpn:// ниже кнопкой «Скопировать» — она берёт всю ссылку, до конца.", "Отправьте её себе на телефон — например, в «Избранное» Telegram — и там скопируйте.", "В AmneziaVPN: «+» → «Вставить» (ключ из буфера) → «Продолжить», затем «Подключиться»."],
    "awg1:android": ["Поставьте приложение AmneziaVPN 4.8.8.3 или новее из Google Play (нет Google Play — файл APK: github.com/amnezia-vpn/amnezia-client/releases).", "Скопируйте ссылку vpn:// ниже кнопкой «Скопировать» — она берёт всю ссылку, до конца.", "Отправьте её себе на телефон — например, в «Избранное» Telegram — и там скопируйте.", "В AmneziaVPN: «+» → «Вставить» (ключ из буфера) → «Продолжить», затем «Подключиться»."],
    "wg:android": ["Поставьте приложение WireGuard из Google Play.", "В WireGuard нажмите «+» → «Сканировать QR-код».", "Наведите камеру телефона на QR-код ниже.", "Назовите подключение, например gKP-wg, и включите его."],
    "vless:android": ["Поставьте приложение v2RayTun из Google Play (нет Google Play — v2rayNG: файл APK с GitHub).", "В v2RayTun нажмите «+» и отсканируйте QR-код.", "Наведите камеру телефона на QR-код ниже.", "Включите подключение."],
    "openvpn:android": ["Поставьте приложение OpenVPN Connect из Google Play.", "Скачайте файл кнопкой «Скачать» ниже.", "Перенесите файл .ovpn на телефон (на телефон — например, через «Избранное» Telegram) и откройте его в OpenVPN Connect.", "Нажмите «Connect» (подписи OpenVPN Connect — на английском)."],
    "awg3:androidtv": ["Поставьте приложение AmneziaVPN 5.0.1.5 или новее на телевизор из Google Play.", "Скопируйте ссылку vpn:// ниже кнопкой «Скопировать» — она берёт всю ссылку, до конца.", "Отправьте её себе на телевизор — например, в «Избранное» Telegram — и там скопируйте.", "В AmneziaVPN: «+» → «Вставить» (ключ из буфера) → «Продолжить», затем «Подключиться»."],
    "awg2:androidtv": ["Поставьте приложение AmneziaVPN 4.8.12.9 или новее на телевизор из Google Play.", "Скопируйте ссылку vpn:// ниже кнопкой «Скопировать» — она берёт всю ссылку, до конца.", "Отправьте её себе на телевизор — например, в «Избранное» Telegram — и там скопируйте.", "В AmneziaVPN: «+» → «Вставить» (ключ из буфера) → «Продолжить», затем «Подключиться»."],
    "awg1:androidtv": ["Поставьте приложение AmneziaVPN 4.8.8.3 или новее на телевизор из Google Play.", "Скопируйте ссылку vpn:// ниже кнопкой «Скопировать» — она берёт всю ссылку, до конца.", "Отправьте её себе на телевизор — например, в «Избранное» Telegram — и там скопируйте.", "В AmneziaVPN: «+» → «Вставить» (ключ из буфера) → «Продолжить», затем «Подключиться»."],
    "vless:androidtv": ["Поставьте приложение v2RayTun на телевизор из Google Play.", "Скопируйте ссылку vless:// ниже кнопкой «Скопировать» — она берёт всю ссылку, до конца.", "Отправьте её себе на телевизор — например, в «Избранное» Telegram — и там скопируйте.", "В v2RayTun: нажмите «+» и импортируйте ссылку из буфера обмена (в v2rayN — Ctrl+V в списке серверов), затем подключитесь."],
    "awg3:windows": ["Поставьте приложение AmneziaVPN 5.0.1.5 или новее с сайта amnezia.org/downloads (или github.com/amnezia-vpn/amnezia-client/releases).", "Скопируйте ссылку vpn:// ниже кнопкой «Скопировать» — она берёт всю ссылку, до конца.", "Отправьте её себе на компьютер — например, в «Избранное» Telegram — и там скопируйте.", "В AmneziaVPN: «+» → «Вставить» (ключ из буфера) → «Продолжить», затем «Подключиться»."],
    "awg2:windows": ["Поставьте приложение AmneziaVPN 4.8.12.9 или новее с сайта amnezia.org/downloads (или github.com/amnezia-vpn/amnezia-client/releases).", "Скопируйте ссылку vpn:// ниже кнопкой «Скопировать» — она берёт всю ссылку, до конца.", "Отправьте её себе на компьютер — например, в «Избранное» Telegram — и там скопируйте.", "В AmneziaVPN: «+» → «Вставить» (ключ из буфера) → «Продолжить», затем «Подключиться»."],
    "awg1:windows": ["Поставьте приложение AmneziaVPN 4.8.8.3 или новее с сайта amnezia.org/downloads (или github.com/amnezia-vpn/amnezia-client/releases).", "Скопируйте ссылку vpn:// ниже кнопкой «Скопировать» — она берёт всю ссылку, до конца.", "Отправьте её себе на компьютер — например, в «Избранное» Telegram — и там скопируйте.", "В AmneziaVPN: «+» → «Вставить» (ключ из буфера) → «Продолжить», затем «Подключиться»."],
    "wg:windows": ["Поставьте WireGuard с сайта wireguard.com/install.", "Скопируйте текст файла .conf кнопкой «Скопировать» ниже.", "В WireGuard: «Добавить туннель» → «Добавить пустой туннель…», сотрите текст в окне, вставьте скопированный и сохраните.", "Нажмите «Подключить»."],
    "vless:windows": ["Поставьте приложение v2rayN с сайта github.com/2dust/v2rayN/releases.", "Скопируйте ссылку vless:// ниже кнопкой «Скопировать» — она берёт всю ссылку, до конца.", "В v2rayN: нажмите «+» и импортируйте ссылку из буфера обмена (в v2rayN — Ctrl+V в списке серверов), затем подключитесь."],
    "openvpn:windows": ["Поставьте приложение OpenVPN Connect с сайта openvpn.net/client.", "Скачайте файл кнопкой «Скачать» ниже.", "Перенесите файл .ovpn на компьютер (на телефон — например, через «Избранное» Telegram) и откройте его в OpenVPN Connect.", "Нажмите «Connect» (подписи OpenVPN Connect — на английском)."],
    "ikev2:windows": ["Скачайте скрипт kaskad-ikev2.ps1 кнопкой «Скачать» ниже.", "Правой кнопкой по «Пуск» откройте Терминал (в Windows 10 — Windows PowerShell) от имени администратора и перейдите в папку со скриптом командой cd <путь к папке>.", "Выполните: powershell -ExecutionPolicy Bypass -File .\\kaskad-ikev2.ps1", "Параметры → Сеть и Интернет → VPN → «Kaskad IKEv2» → «Подключиться». Логин и пароль Windows спросит — они ниже, в блоке «Данные для подключения»."],
    "l2tp:windows": ["Скачайте скрипт kaskad-l2tp.ps1 кнопкой «Скачать» ниже.", "Правой кнопкой по «Пуск» откройте Терминал (в Windows 10 — Windows PowerShell) от имени администратора и перейдите в папку со скриптом командой cd <путь к папке>.", "Выполните: powershell -ExecutionPolicy Bypass -File .\\kaskad-l2tp.ps1", "Параметры → Сеть и Интернет → VPN → «Kaskad L2TP» → «Подключиться». Логин и пароль Windows спросит — они ниже, в блоке «Данные для подключения»."],
    "sstp:windows": ["Скачайте скрипт kaskad-sstp.ps1 кнопкой «Скачать» ниже.", "Правой кнопкой по «Пуск» откройте Терминал (в Windows 10 — Windows PowerShell) от имени администратора и перейдите в папку со скриптом командой cd <путь к папке>.", "Выполните: powershell -ExecutionPolicy Bypass -File .\\kaskad-sstp.ps1", "Параметры → Сеть и Интернет → VPN → «Kaskad SSTP» → «Подключиться». Логин и пароль Windows спросит — они ниже, в блоке «Данные для подключения»."],
    "vless:linux": ["Поставьте приложение v2rayN с сайта github.com/2dust/v2rayN/releases.", "Скопируйте ссылку vless:// ниже кнопкой «Скопировать» — она берёт всю ссылку, до конца.", "В v2rayN: нажмите «+» и импортируйте ссылку из буфера обмена (в v2rayN — Ctrl+V в списке серверов), затем подключитесь."],
    "awg3:keenetic": ["Скачайте файл кнопкой «Скачать» ниже.", "Откройте веб-интерфейс Keenetic: Интернет → Другие подключения → WireGuard → импорт из файла и выберите этот файл.", "Включите подключение и проверьте, что сайты открываются."],
    "awg2:keenetic": ["Скачайте файл кнопкой «Скачать» ниже.", "Откройте веб-интерфейс Keenetic: Интернет → Другие подключения → WireGuard → импорт из файла и выберите этот файл.", "Включите подключение и проверьте, что сайты открываются."],
    "awg1:keenetic": ["Скачайте файл кнопкой «Скачать» ниже.", "Откройте веб-интерфейс Keenetic: Интернет → Другие подключения → WireGuard → импорт из файла и выберите этот файл.", "Включите подключение и проверьте, что сайты открываются."],
    "wg:mikrotik": ["Скопируйте скрипт кнопкой «Скопировать» ниже. Если сеть роутера не 192.168.88.0/24 (так у MikroTik по умолчанию), замените её в скрипте на свою.", "Winbox → New Terminal (в WebFig — Terminal; подписи RouterOS на английском) → вставьте скрипт. Длинный удобнее файлом: «Скачать» ниже, загрузите kaskad-wg.rsc в Files и выполните /import file-name=kaskad-wg.rsc", "Interfaces → kaskad-wg: флаг «R» — подключено, устройства сети идут через VPN."],
    "l2tp:mikrotik": ["Скопируйте скрипт кнопкой «Скопировать» ниже. Если сеть роутера не 192.168.88.0/24 (так у MikroTik по умолчанию), замените её в скрипте на свою.", "Winbox → New Terminal (в WebFig — Terminal; подписи RouterOS на английском) → вставьте скрипт. Длинный удобнее файлом: «Скачать» ниже, загрузите kaskad-l2tp.rsc в Files и выполните /import file-name=kaskad-l2tp.rsc", "Interfaces → kaskad-l2tp: флаг «R» — подключено, устройства сети идут через VPN."],
    "sstp:mikrotik": ["Скопируйте скрипт кнопкой «Скопировать» ниже. Если сеть роутера не 192.168.88.0/24 (так у MikroTik по умолчанию), замените её в скрипте на свою.", "Winbox → New Terminal (в WebFig — Terminal; подписи RouterOS на английском) → вставьте скрипт. Длинный удобнее файлом: «Скачать» ниже, загрузите kaskad-sstp.rsc в Files и выполните /import file-name=kaskad-sstp.rsc", "Interfaces → kaskad-sstp: флаг «R» — подключено, устройства сети идут через VPN."],
    "awg3:openwrt": ["Скачайте файл кнопкой «Скачать» ниже.", "Откройте LuCI → Network → Interfaces → «Load configuration…» (в русском LuCI: Сеть → Интерфейсы → «Загрузка конфигурации…»; пакеты awg-openwrt) и выберите этот файл.", "Включите подключение и проверьте, что сайты открываются."],
    "awg2:openwrt": ["Скачайте файл кнопкой «Скачать» ниже.", "Откройте LuCI → Network → Interfaces → «Load configuration…» (в русском LuCI: Сеть → Интерфейсы → «Загрузка конфигурации…»; пакеты awg-openwrt) и выберите этот файл.", "Включите подключение и проверьте, что сайты открываются."],
    "awg1:openwrt": ["Скачайте файл кнопкой «Скачать» ниже.", "Откройте LuCI → Network → Interfaces → «Load configuration…» (в русском LuCI: Сеть → Интерфейсы → «Загрузка конфигурации…»; пакеты awg-openwrt) и выберите этот файл.", "Включите подключение и проверьте, что сайты открываются."],
    "awg3:other": ["Поставьте приложение AmneziaVPN с сайта amnezia.org/downloads (телефоны — из Google Play или App Store).", "Скопируйте ссылку vpn:// ниже кнопкой «Скопировать» — она берёт всю ссылку, до конца.", "Отправьте её себе на устройство — например, в «Избранное» Telegram — и там скопируйте.", "В AmneziaVPN: «+» → «Вставить» (ключ из буфера) → «Продолжить», затем «Подключиться»."],
    "awg2:other": ["Поставьте приложение AmneziaVPN с сайта amnezia.org/downloads (телефоны — из Google Play или App Store).", "Скопируйте ссылку vpn:// ниже кнопкой «Скопировать» — она берёт всю ссылку, до конца.", "Отправьте её себе на устройство — например, в «Избранное» Telegram — и там скопируйте.", "В AmneziaVPN: «+» → «Вставить» (ключ из буфера) → «Продолжить», затем «Подключиться»."],
    "awg1:other": ["Поставьте приложение AmneziaVPN с сайта amnezia.org/downloads (телефоны — из Google Play или App Store).", "Скопируйте ссылку vpn:// ниже кнопкой «Скопировать» — она берёт всю ссылку, до конца.", "Отправьте её себе на устройство — например, в «Избранное» Telegram — и там скопируйте.", "В AmneziaVPN: «+» → «Вставить» (ключ из буфера) → «Продолжить», затем «Подключиться»."],
    "vless:other": ["Поставьте приложение Hiddify с сайта hiddify.com (телефоны — Hiddify из Google Play или App Store).", "Скопируйте ссылку vless:// ниже кнопкой «Скопировать» — она берёт всю ссылку, до конца.", "В Hiddify: нажмите «+» и импортируйте ссылку из буфера обмена (в v2rayN — Ctrl+V в списке серверов), затем подключитесь."]
  };
  function mockTranslit(name) {
    var T = { "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya" };
    return String(name || "").split("").map(function (c) { var l = c.toLowerCase(); return T.hasOwnProperty(l) ? (c !== l && T[l] ? T[l].charAt(0).toUpperCase() + T[l].slice(1) : T[l]) : c; }).join("")
      .replace(/[^A-Za-z0-9_.-]+/g, "-").replace(/^-+|-+$/g, "");
  }
  function rec(format, title, body, filename, notes) { return { format: format, title: title, body: body, filename: filename, notes: notes || [] }; }
  // ── «Куда пойдёт трафик?» (POST gw/trace) — как kaskad.gw.trace.trace() на маленьких списках ──────────────────────
  // Порядок тот же: выключено → блок по домену (для всех) → маршрут «блок»/отказ «блок» → «Россия напрямую» (всегда
  // через выход / напрямую по домену / сети России по адресу) → маршрут и выход сейчас. Самый длинный суффикс побеждает.
  var TR_DOMAINS = {
    block: { "doubleclick.net": "category-ads", "googlesyndication.com": "category-ads", "ad.mail.ru": "category-ads", "an.yandex.ru": "category-ads", "vortex.data.microsoft.com": "win-spy" },
    force: { "youtube.com": "youtube", "googlevideo.com": "youtube", "ytimg.com": "youtube", "telegram.org": "telegram", "t.me": "telegram", "github.com": "github",
      "openai.com": "category-geoblock-ru", "chatgpt.com": "category-geoblock-ru", "ifconfig.me": "", "ipinfo.io": "" },
    direct: { "ya.ru": "category-ru", "yandex.ru": "category-ru", "vk.com": "category-ru", "mail.ru": "category-ru", "gosuslugi.ru": "whitelist", "sber.ru": "whitelist",
      "apple.com": "apple", "microsoft.com": "microsoft", "steampowered.com": "steam" }
  };
  // «DNS узла» макета и «сети России» макета — адреса для документации (RFC 5737), не настоящие: «Россия» здесь —
  // 198.51.100.128/25 (ozon.ru, avito.ru, kinopoisk.ru смотрят туда), остальное — 192.0.2.0/24 (3.3.6, бэклог п. 6)
  var TR_DNS = { "example.com": ["192.0.2.80"], "wikipedia.org": ["192.0.2.81"], "ozon.ru": ["198.51.100.131"], "avito.ru": ["198.51.100.141"], "kinopoisk.ru": ["198.51.100.151"] };
  var TR_RU = [["198.51.100.128", 25]];
  function trIp(s) { var p = String(s).split("."); return p.length === 4 && p.every(function (x) { return /^\d{1,3}$/.test(x) && +x < 256; }) ? (((+p[0]) * 256 + (+p[1])) * 256 + (+p[2])) * 256 + (+p[3]) : null; }
  function trRu(ip) { var n = trIp(ip); return n != null && TR_RU.some(function (r) { var b = trIp(r[0]), size = Math.pow(2, 32 - r[1]); return n >= b && n < b + size; }); }
  function trTarget(raw) {
    var s = String(raw || "").trim();
    if (!s) throw httpErr(400, "Введите домен или адрес IPv4, например youtube.com или 8.8.8.8.", 2);
    s = s.replace(/^[a-z][a-z0-9+.-]*:\/\//i, "").split(/[\/?#\s]/)[0].split("@").pop();
    if (s.split(":").length === 2) s = s.split(":")[0];
    s = s.replace(/\.$/, "").toLowerCase();
    if (trIp(s) != null) return { input: raw, kind: "ip", value: s };
    if (/^[0-9.]+$/.test(s) || !/^[a-z0-9_]([a-z0-9_-]*[a-z0-9_])?(\.[a-z0-9_]([a-z0-9_-]*[a-z0-9_])?)+$/.test(s))
      throw httpErr(400, "«" + String(raw).slice(0, 64) + "» — не домен и не адрес IPv4. Пример: youtube.com, ya.ru, 8.8.8.8.", 2);
    return { input: raw, kind: "domain", value: s };
  }
  function trMatch(name) {
    var parts = name.split(".");
    for (var i = 0; i < parts.length; i++) {
      var suf = parts.slice(i).join(".");
      for (var g in TR_DOMAINS) if (OWN_M.call(TR_DOMAINS[g], suf)) {
        var c = TR_DOMAINS[g][suf];
        if (g === "block" && !db.rules.block_ads) continue;
        return { group: g, suffix: suf, categories: c ? [c] : [] };
      }
    }
    return null;
  }
  var OWN_M = Object.prototype.hasOwnProperty;
  // ид звеньев ОДНОГО пути — как kaskad.gw.trace.chain (их же рисует схема)
  function trChain(o) {
    var out = { nodes: [], edges: [], rows: {}, alts: [] }, c = o.category || "rest";
    var n = function (x) { if (x && out.nodes.indexOf(x) < 0) out.nodes.push(x); }, e = function (x) { if (x && out.edges.indexOf(x) < 0) out.edges.push(x); };
    n(o.user);
    if (c === "off") return out;
    if (o.device && o.inbound) { e("access:" + o.device + ":" + o.inbound); n("in:" + o.inbound); e("flow:" + o.inbound); }
    n("filter");
    if (c === "ads" || c === "torrent") { out.rows.filter = c; return out; }          // отсеивает сам фильтр (М2)
    out.rows.filter = "pass";
    var p = o.profile || "all_exit", rules = "rules:" + p;
    e("pass:" + p); n(rules);
    if (c === "ru") { out.rows[rules] = "ru"; e("cat:" + p + ":ru"); n("direct"); return out; }
    out.rows[rules] = "rest";
    var via = o.via || "pool", tgt = o.target || via;
    if (tgt !== via && (tgt === "direct" || tgt === "block")) { e("fail:" + p + ":" + via + ":" + tgt); n(tgt); return out; }
    e("route:" + p + ":" + via); n(via);
    // выбирает xray: ствол до пула, ветви к кандидатам — только в конце (alts), как kaskad.gw.trace.chain
    if (via === "pool") { if (o.balancer) { if (o.balancer === "xray" || o.balancer === "vless") out.rows.pool = "vless"; (o.exits || []).forEach(function (x) { out.alts.push("member:" + x); }); }
      else if (o.exit) { out.rows.pool = o.exit; e("member:" + o.exit); n("exit:" + o.exit); } }
    return out;
  }
  function traceDoc(body) {
    if (body.who != null && typeof body.who !== "string" || typeof body.target !== "string")
      throw httpErr(400, "Нужны who (устройство d-…, пользователь u-… или «-») и target (домен или адрес IPv4).", 2);
    if (body.inbound != null && (typeof body.inbound !== "string" || !db.inbounds[body.inbound])) throw httpErr(400, "inbound — вход устройства.", 2);
    var q = trTarget(body.target), ref = body.who || "-";
    var d = db.devices.filter(function (x) { return x.id === ref || x.login === ref; })[0] || null;
    var u = d ? db.users.filter(function (x) { return x.id === d.user; })[0] || null : db.users.filter(function (x) { return x.id === ref; })[0] || null;
    if (ref !== "-" && !d && !u) throw httpErr(400, "Нет устройства или пользователя «" + ref.slice(0, 64) + "» (или имя неоднозначно — укажите id d-… или u-…).", 2);
    var kind = d ? "device" : u ? "user" : "new";
    var dev = d || { id: null, user: u ? u.id : null, route: null, rules: null, enabled: true, access: [] };
    var ready = !!db.rules.lists, steps = [], notes = [];
    var step = function (text, note, tone) { steps.push({ text: text, note: note || "", tone: tone || "" }); };
    var installed = INBOUND_DEFS.filter(function (x) { return db.inbounds[x[0]].installed; }).map(function (x) { return x[0]; });
    var on = (dev.access || []).filter(function (k) { return installed.indexOf(k) >= 0 && db.inbounds[k].enabled; });
    var title = function (k) { return db.inbounds[k] ? db.inbounds[k].title : k; };
    // путь идёт через ОДИН вход: заданный (inbound) или первый включённый
    var chosen = on[0] || null;
    if (body.inbound) {
      if (kind !== "device") throw httpErr(400, "Протокол выбирают у устройства, а у нового устройства протоколов ещё нет.", 2);
      if (dev.access.indexOf(body.inbound) < 0) throw httpErr(400, "У устройства «" + dev.name + "» нет протокола " + title(body.inbound) + " — есть: " + (dev.access.map(title).join(", ") || "никаких") + ".", 2);
      if (on.indexOf(body.inbound) < 0) throw httpErr(400, "Протокол " + title(body.inbound) + " у устройства «" + dev.name + "» выключен или не установлен — через него трафика нет.", 2);
      chosen = body.inbound;
    }
    var socks = XRAY_KINDS.indexOf(chosen) >= 0, xname = socks ? title(chosen).split(" ")[0] : "";
    var otherX = on.filter(function (k) { return XRAY_KINDS.indexOf(k) >= 0; })[0];
    var active = kind !== "device" || (dev.enabled !== false && (!u || u.enabled !== false));
    var prof = dev.rules ? { profile: dev.rules, from: "device" } : (u && u.rules) ? { profile: u.rules, from: "user" } : { profile: db.rules.default, from: "default" };
    var applied = prof.profile === "ru_direct" && !ready ? "all_exit" : prof.profile;
    var route = d ? routeOf(d) : (u && u.route ? Object.assign({ from: "user" }, u.route) : Object.assign({ from: "default" }, db.gw.route));
    var TITLES = { ru_direct: "Россия напрямую", all_exit: "Всё через выход" }, FROM = { device: "своя у устройства", user: "от пользователя", default: "по умолчанию" };
    var whoText = kind === "device" ? "Устройство «" + d.name + "»" + (u ? " (" + u.name + ")" : "") : kind === "user" ? "Новое устройство пользователя «" + u.name + "»" : "Новое устройство с настройками по умолчанию";
    var others = on.filter(function (k) { return k !== chosen; });
    var whoNote = kind === "device" ? (chosen ? "через протокол " + title(chosen) + (others.length ? " (есть и " + others.map(title).join(", ") + " — путь через " + (others.length > 1 ? "них" : "него") + " проверяется отдельно)" : "")
      : "протоколов нет — подключиться нечем") : "протокол любой, кроме SOCKS5 и VLESS (WireGuard, AmneziaWG, L2TP, IKEv2, OpenVPN и другие)";
    if (prof.profile === "ru_direct" && applied !== "ru_direct") whoNote += "; списков RoscomVPN ещё нет — пока всё через выход";
    step(whoText + ", профиль «" + TITLES[prof.profile] + "» (" + FROM[prof.from] + ")", whoNote, kind === "device" && !chosen ? "warn" : "");
    if (!db.gw.enabled) notes.push("Шлюз выключен — сейчас трафика нет; показано, как пойдёт после включения.");
    if (socks && kind === "device") notes.push("Через " + xname + " решает xray шлюза по тем же спискам плюс keyword и regexp — итог может отличаться от протоколов на ядре (WireGuard, L2TP и других).");
    else if (otherX && kind === "device") notes.push("Через " + title(otherX).split(" ")[0] + " это устройство решал бы xray (те же списки плюс keyword и regexp) — выберите " + title(otherX).split(" ")[0] + " во «Через», чтобы увидеть его путь.");
    var server = db.gw.host || "";
    var direct = function () { return server ? "Напрямую с адреса сервера " + server : "Напрямую с адреса этого сервера"; };
    var exitName = function (id) { var e = db.exits.filter(function (x) { return x.id === id; })[0]; return e ? e.name : id; };
    var res = { ok: true, query: q, who: { kind: kind, id: d ? d.id : null, name: d ? d.name : null, user: u ? { id: u.id, name: u.name } : null, active: active, inbounds: on, inbound: chosen, socks: socks, kernel: !socks },
      profile: Object.assign({ applied: applied }, prof), route: route, match: null, addresses: [], resolve: null, exit: null, exits: [], server: server };
    var extra = [];
    var finish = function (verdict, reason, summary, category, now, exitId, balancer, cands) {
      var c = trChain({ user: u ? u.id : null, device: d ? d.id : null, inbound: chosen, profile: prof.profile, category: category, via: route.via, target: now || route.via, exit: exitId, balancer: balancer, exits: cands });
      extra.concat(balancer ? (cands || []).map(function (x) { return "exit:" + x; }) : []).forEach(function (x) { if (c.nodes.indexOf(x) < 0) c.nodes.push(x); });
      var hl = { nodes: c.nodes, edges: c.edges, rows: c.rows, devices: d ? [d.id] : [], user: u ? u.id : null, device: d ? d.id : null, inbound: chosen, profile: prof.profile, category: category,
        via: route.via, target: category === "ads" || category === "torrent" ? "filter" : now || route.via, exit: exitId || null, balancer: balancer || false, exits: cands || [], alts: c.alts };
      var origin = verdict === "direct" ? "Сайт откроется напрямую, с адреса этого сервера" + (server ? " (" + server + ")" : "") : verdict === "block" ? "Сайт не откроется" : res.origin || "";
      return Object.assign(res, { verdict: verdict, reason: reason, summary: summary, origin: origin, steps: steps, notes: notes, highlight: hl });
    };
    if (!active) { step("Устройство или его пользователь выключены", "трафика нет: шлюз не пропускает ни одного пакета", "bad"); return finish("block", "off", "Заблокировано: устройство выключено", "off", "block"); }
    if (kind === "device" && !chosen) { step("Подключиться нечем", "у устройства нет включённого протокола — трафика нет", "bad"); return finish("block", "no_inbound", "Трафика нет: у устройства нет включённого протокола", "off", "block"); }
    var m = null;
    if (q.kind === "domain") {
      if (!ready) step(q.value + " — списков доменов нет", "списки RoscomVPN не скачаны: блокировок и правил по доменам нет", "warn");
      else {
        m = res.match = trMatch(q.value);
        if (!m) step(q.value + " — нет в списках", "не реклама, не «всегда через выход», не «напрямую»");
        else {
          var where = m.suffix === q.value ? q.value : q.value + " (как " + m.suffix + ")", cat = m.categories.length ? " (категория " + m.categories.join(", ") + ")" : "";
          var text = m.group === "block" ? where + " — реклама и телеметрия: блок" + cat : where + " — список " + (m.group === "force" ? "«всегда через выход»" : "«напрямую»") + (cat || (m.group === "force" ? " (сайт проверки адреса)" : ""));
          var note = m.group === "block" ? "блок для всех устройств, какой бы ни был профиль" : applied !== "ru_direct" ? "при профиле «" + TITLES[applied] + "» не действует — трафик идёт по маршруту"
            : m.group === "direct" ? "значит, напрямую с адреса сервера, мимо выходов" : "значит, по маршруту устройства, даже если адрес в сетях России";
          step(text, note);
        }
      }
    }
    if (m && m.group === "block") {
      if (!socks) notes.push("Блок по домену работает, когда устройство спрашивает адреса у DNS шлюза (так в выданных настройках); свой DNS в браузере (DoH) его обходит.");
      return finish("block", "ads", "Заблокировано: реклама и телеметрия", "ads", "block");
    }
    // куда сейчас — nowFor (как _route_now поверх exits.now_of): один ответ со схемой (Н-1)
    var fall = route.fail === "direct" ? "напрямую" : "блок", n = nowFor(dev, route, socks), t;
    if (route.via === "block") t = { mark: "block", exits: [], why: "маршрут «блок»" };
    else if (route.via === "direct") t = { mark: "direct", exits: [], why: "маршрут «напрямую»" };
    else if (n.dead.length) t = { mark: n.target, exits: n.dead.slice(), dead: true, why: "выход «" + exitName(n.dead[0]) + "» не работает" };
    else if (n.target === "direct" || n.target === "block") t = { mark: n.target, exits: [], why: "в пуле нет живых выходов" };
    else if (n.balancer) t = { mark: "exit", exits: n.exits.slice(), balancer: n.balancer, why: "" };
    else if (n.pool_live.length && !n.exit) t = { mark: "exit", exits: [], live: n.pool_live.slice(), why: "" };
    else t = { mark: "exit", exits: [n.exit], why: "" };
    if (t.dead) extra = t.exits.map(function (x) { return "exit:" + x; });
    var rtext = "Маршрут: " + (route.via === "pool" ? "пул выходов" : route.via === "direct" ? "напрямую" : route.via === "block" ? "блок" : "выход «" + exitName(route.via.slice(5)) + "»") + " (" + FROM[route.from] + ")";
    var rnote = route.via === "direct" || route.via === "block" ? "" : "при отказе выхода — " + fall;
    var cap = function (s) { return s.charAt(0).toUpperCase() + s.slice(1); };
    if (t.mark === "block") {
      step(rtext, rnote);
      if (route.via === "block") { step("Маршрут «блок» закрывает всё", "и сайты России тоже", "bad"); return finish("block", "route_block", "Заблокировано: маршрут «блок»", "rest", "block"); }
      step(cap(t.why) + " — блок", "политика отказа «блок»: закрыто всё, и сайты России тоже", "bad");
      return finish("block", "fail_block", "Заблокировано: " + t.why, "rest", "block");
    }
    if (applied === "ru_direct" && t.mark !== "direct") {
      if (m && m.group === "direct") return finish("direct", "ru_domain", direct(), "ru", "direct");
      if (!(m && m.group === "force")) {
        var ips = q.kind === "ip" ? [q.value] : (TR_DNS[q.value] || []);
        if (q.kind === "domain") res.resolve = { ok: !!ips.length, error: ips.length ? null : "такого домена нет" };
        if (!ips.length) step("Адреса " + q.value + " не найдены (такого домена нет)", "проверить по сетям России нечего — трафик пойдёт по маршруту", "warn");
        else {
          res.addresses = ips;
          var head = q.kind === "ip" ? ips.join(", ") : q.value + ": " + ips.join(", ");
          if (!ips.some(trRu)) step(head + " — не в сетях России", "значит, по маршруту устройства");
          else if (ruState().state !== "ok") step(head + " — в сетях России", "сети России сейчас не загружены в таблицу — по IP напрямую не пойдёт", "warn");
          else { step(head + " — в сетях России", "значит, напрямую с адреса сервера, мимо выходов"); return finish("direct", "ru_ip", direct(), "ru", "direct"); }
        }
      }
    }
    step(rtext, rnote);
    if (t.mark === "direct") {
      if (route.via === "direct") return finish("direct", "route_direct", direct(), "rest", "direct");
      step(cap(t.why) + " — напрямую", "политика отказа «напрямую»: всё идёт мимо выходов", "warn");
      return finish("direct", "fail_direct", direct(), "rest", "direct");
    }
    var rows = t.exits.map(function (id) { var e = db.exits.filter(function (x) { return x.id === id; })[0]; return { id: id, name: exitName(id), state: e ? e.state : "missing", text: e ? exitRow(e).text : "нет в пуле", kind: e ? e.kind : null }; });
    res.exits = rows; res.exit = rows.length === 1 ? rows[0] : null;
    if (route.via !== "pool" || (!t.balancer && rows.length === 1)) {
      step("Сейчас выход «" + rows[0].name + "» — " + rows[0].text, "", "ok");
      res.origin = "Сайт откроется с адреса выхода «" + rows[0].name + "»";
      return finish("exit", "route", "Через выход «" + rows[0].name + "»", "rest", null, route.via === "pool" ? rows[0].id : null);
    }
    var names = rows.map(function (r) { return "«" + r.name + "»"; }).join(", ");
    if (t.balancer === "any") {
      step("Сейчас: любой живой выход пула — выберет автоматически (" + names + ")", (xname || "SOCKS5") + ": выход выбирает xray среди всех живых выходов пула", "ok");
      res.origin = rows.length > 1 ? "Сайт откроется с адреса одного из выходов: " + names : "Сайт откроется с адреса выхода " + names;
      return finish("exit", "route", "Через любой живой выход пула — выберет автоматически", "rest", null, null, "any", t.exits);
    }
    if (t.balancer) {
      step("Сейчас: один из VLESS-выходов — выберет автоматически (" + names + ")", "самый быстрый из живых", "ok");
      res.origin = rows.length > 1 ? "Сайт откроется с адреса одного из них: " + names : "Сайт откроется с адреса выхода " + names;
      return finish("exit", "route", "Через один из VLESS-выходов — выберет автоматически", "rest", null, null, "xray", t.exits);
    }
    extra = (t.live || []).map(function (x) { return "exit:" + x; });
    step("Выход выберет шлюз среди живых выходов пула", "живых выходов: " + (t.live || []).length + " — устройство закрепится за одним из них", "ok");
    res.origin = "Сайт откроется с адреса одного из выходов пула";
    return finish("exit", "route", "Через выход пула", "rest", null);
  }
  // ── Переименование (T13, SPEC-stage1-rename): как names.check — 1–40 знаков, без управляющих и «|»; дубль человека —
  // 409 user_exists, прочие — 409 confirm_duplicate без allow_duplicate; id не меняется, применения нет
  var RN_NOTE = { same: "Сам ключ и настройки не меняются.", user: "Страница пользователя и заголовок подписки — с новым именем. Логины устройств не меняются.",
    wg: "В приложении имя останется прежним, пока вы не выдадите настройки заново. Новый файл .conf или QR-код даст в приложении WireGuard или AmneziaWG второй туннель с теми же ключами — удалите старый туннель в приложении.",
    sub: "Имя обновится в приложении при следующем обновлении подписки.", profile: "Подключение работает как раньше. Имя профиля в приложении — прежнее, пока не настроите заново.",
    dev: "Подключение работает как раньше." };
  var RN_WHAT = { user: "Имя пользователя", device: "Имя устройства", exit: "Подпись выхода", inbound: "Подпись протокола", sub: "Подпись подписки", forward: "Имя проброса" };
  function rnCheck(v, what, reset) {
    if (typeof v !== "string") throw httpErr(400, RN_WHAT[what] + ": нужен текст.", "field", { reason: "type", field: "name" });
    if (/[\x00-\x1f\x7f|]/.test(v)) throw httpErr(400, RN_WHAT[what] + ": недопустимый знак. Нельзя использовать управляющие и невидимые знаки и вертикальную черту.", "field", { reason: "char", field: "name" });
    var c = v.trim().replace(/\s+/g, " ");
    if (!c && reset) return "";
    if (!c) throw httpErr(400, RN_WHAT[what] + ": пусто. Нужно от 1 до 40 знаков.", "field", { reason: "empty", field: "name", max: 40 });
    if ([...c].length > 40) throw httpErr(400, RN_WHAT[what] + ": длиннее 40 знаков (" + [...c].length + ").", "field", { reason: "long", field: "name", max: 40 });
    return c;
  }
  function rnDup(items, c, selfId, allow) {
    var low = c.toLowerCase();
    var dup = c ? items.filter(function (x) { return x.id !== selfId && String(x.name || "").trim().toLowerCase() === low; }).map(function (x) { return x.id; }) : [];
    if (dup.length && !allow) throw httpErr(409, "Имя «" + c + "» уже есть. Сохранить всё равно?", "confirm_duplicate", { need: "confirm_duplicate", duplicates: dup });
    return dup;
  }
  function rnDevNote(d) {
    var a = d.access || [], out = [];
    if (a.some(function (k) { return ["wg", "awg1", "awg2", "awg3"].indexOf(k) >= 0; })) out.push(RN_NOTE.wg);
    if (a.indexOf("vless") >= 0) out.push(RN_NOTE.sub);
    if (a.some(function (k) { return ["openvpn", "ikev2", "l2tp", "pptp", "sstp", "ocserv", "socks5"].indexOf(k) >= 0; })) out.push(RN_NOTE.profile);
    return out.join(" ") || RN_NOTE.dev;
  }
  function rnDone(what, id, old, name, shown, dup, note) { bump(); return { ok: true, what: what, id: id, old: old || "", name: name, shown: shown, duplicates: dup || [], note: note }; }
  function rnRoute(method, path, body) {
    var m;
    if ((m = /^\/api\/gw\/users\/(u-[0-9a-f]{8})\/name$/.exec(path)) && method === "PUT") {
      var u = find(db.users, m[1]);
      if (!u) throw httpErr(404, "Пользователя " + m[1] + " нет (или имя неоднозначно — укажите id).", "gone", { gone: true });
      var cu = rnCheck(body.name, "user", false);
      var du = db.users.filter(function (x) { return x.id !== u.id && String(x.name).toLowerCase() === cu.toLowerCase(); }).map(function (x) { return x.id; });
      if (du.length) throw httpErr(409, "Пользователь «" + cu + "» уже есть.", "user_exists", { duplicates: du });
      var ou = u.name; u.name = cu;
      return rnDone("user", u.id, ou, cu, cu, [], RN_NOTE.user);
    }
    if ((m = /^\/api\/gw\/devices\/(d-[0-9a-f]{8})\/name$/.exec(path)) && method === "PUT") {
      var d = find(db.devices, m[1]);
      if (!d) throw httpErr(404, "Устройства " + m[1] + " нет.", "gone", { gone: true });
      var cd = rnCheck(body.name, "device", false);
      var dd = rnDup(db.devices.filter(function (x) { return x.user === d.user; }), cd, d.id, body.allow_duplicate);
      var od = d.name; d.name = cd;
      return rnDone("device", d.id, od, cd, cd, dd, rnDevNote(d));
    }
    if ((m = /^\/api\/gw\/inbounds\/([a-z0-9]{2,8})\/label$/.exec(path)) && method === "PUT") {
      var ib = db.inbounds[m[1]];
      if (!ib || !ib.installed) throw httpErr(404, "Протокол " + m[1] + " не установлен.", "gone", { gone: true });
      var cl = rnCheck(body.label !== undefined ? body.label : body.name, "inbound", true);
      var di = rnDup(Object.keys(db.inbounds).map(function (k) { return { id: k, name: db.inbounds[k].label || null }; }), cl, m[1], body.allow_duplicate);
      var ol = ib.label || ""; if (cl) ib.label = cl; else delete ib.label;
      return rnDone("inbound", m[1], ol, cl, cl ? ib.title + " — " + cl : ib.title, di, RN_NOTE.same);
    }
    if ((m = /^\/api\/forwards\/(tcp|udp)\/([0-9]{1,5})\/name$/.exec(path)) && method === "PUT") {
      var f = db.forwards.filter(function (x) { return x.proto === m[1] && String(x.port) === m[2]; })[0];
      if (!f) throw httpErr(404, "Проброса " + m[1] + " :" + m[2] + " нет.", "gone", { gone: true });
      var cf = rnCheck(body.name, "forward", true);
      var df = rnDup(db.forwards.map(function (x) { return { id: x.proto + ":" + x.port, name: x.name || null }; }), cf, m[1] + ":" + m[2], body.allow_duplicate);
      var of = f.name || ""; f.name = cf;
      return rnDone("forward", m[1] + ":" + m[2], of, cf, cf || m[1] + " :" + m[2], df, RN_NOTE.same);
    }
    return null;
  }

  function httpErr(status, text, code, data) { var e = new Error(text); e.http = status; e.code = code; e.data = data; return e; }

  // ── Домен и сайт (волна 4.0-B: kaskad.site.panel, docs/k3/SPEC-wave4.md §4.1) ─────────────────────────────────────
  /* Параметры адреса (к mock=1):
       site=none        домена нет (так и без параметра)
       site=dnsnone     домен задан, A-записи нет («Ждать до 5 минут» в макете — 6 с, и запись «появляется»)
       site=partial     часть A-записей ведёт на чужой адрес, AAAA — не на узел
       site=ok          DNS в порядке, сертификата ещё нет — «Выпустить сертификат»
       site=cloudflare  оранжевое облако Cloudflare (A и AAAA — адреса Cloudflare)
       site=le          сертификат Let's Encrypt (74 дня), панель открыта из интернета — уже на сертификате домена
       site=staging     тестовый сертификат (staging)
       site=expiring    сертификат истекает через 12 дней — «Продлить»
       site=sstp        как le, SSTP уже на сертификате домена, адрес для клиентов — домен
       site=idn         кириллический домен (domain_unicode), сертификат Let's Encrypt
       site=long        очень длинный домен (переносы строк), DNS в порядке
       certfail=port80|limit|net|dns|apt   выпуск (и продление — limit, net) отвечает отказом 409 с этой причиной, как
                        сервер; port80 — и в статусе порт 80 занят пробросом
     Сайт-маскировка (волна 4.0-C, docs/k3/SPEC-wave4.md §7.1):
       site=on          как le, и сайт-маскировка включена: nginx, шаблон из каталога, панель на секретном адресе домена
       sitefail=no_domain|no_cert|port443|port80|neighbour|apt|nginx_test|template   «Включить сайт» отвечает
                        отказом 409 с этой причиной, как сервер; port443 — и в статусе порт 443 занят; template — шаблон
                        не развёрнут (template_reason — из tplfail, иначе download_failed; nokey и net — тоже)
       catalog=nokey|bad|net|missing   каталог шаблонов не открывается (data.reason у сервера); nokey и bad проходят,
                        когда ключ GitHub сохранён в панели (окно «Ввести ключ GitHub» из каталога)
       tplfail=no_index|download_failed|timeout|too_big|app_shell|broken_assets|bad_repo|nokey|net   выбранный в
                        каталоге шаблон не разворачивается: при включении — отказ 409 template, на включённом сайте —
                        200 {ok: false, reason, text}; сайт прежний (случайный — разворачивается)
       fallback=1       случайный шаблон не скачался: сайт включается с простой страницей (fallback: true, warnings)
       checkwarn=1      проверка в порядке, но с предупреждением: на порту 80 — страница nginx по умолчанию
       check=ok|cert_expiring|http_error|panel_unreachable|dns_mismatch|no_cert|no_site   что ответит «Проверить сайт»;
                        с site=on — и последняя проверка в статусе
     Включение сайта идёт 8 с, выключение — 2,5 с, смена шаблона — 3 с, проверка — 2,5 с. Каталог — 74 шаблона в шести
     категориях (страницы по 25), поиск — по названию и id, потом по описанию, без учёта регистра, «ё» = «е».
     Новый домен, заданный в панели, «расходится» по DNS через 20 с (до того — A-записи нет); имя с «cloudflare» —
     оранжевое облако, с «partial» — чужая A-запись. Выпуск идёт 5 с, продление — 3 с. Кириллица в макете
     превращается в ненастоящий punycode (xn--…): у сервера — настоящий. */
  var SITE_MODE = Q.get("site") || "none";
  var CERT_FAIL = Q.get("certfail") || "";
  var MASK_FAIL = Q.get("sitefail") || "";
  var CATALOG_FAIL = Q.get("catalog") || "";
  var TPL_FAIL = Q.get("tplfail") || "";
  var CHECK = Q.get("check") || "";
  var FALLBACK = Q.get("fallback") === "1";
  var CHECK_WARN = Q.get("checkwarn") === "1";
  var SITE_IP = SRV;
  var SITE_DEVS = { sstp: ["d-f5a6b7c8", "d-3c3c0002"], ocserv: ["d-7ae67ae0"] };       // как будто у них есть эти входы
  var SITE_FAIL = {
    port80: "Порт 80/tcp занят: проброс tcp/80 → 203.0.113.50:80 («Сайт»). Для выпуска сертификата Let's Encrypt должен временно занять этот порт. Уберите этот проброс или остановите службу, затем повторите выпуск. Kaskad чужие службы не останавливает.",
    limit: "Let's Encrypt временно не выдаёт сертификаты домену {d} (лимит). Повторить можно {at} UTC (через 3 ч).",
    net: "Сервер не достучался до Let's Encrypt: исходящее соединение оборвалось. Домен и порт 80 тут ни при чём — это сеть сервера или хостинга. Повторите через несколько минут; не проходит — спросите хостинг про исходящие соединения на порт 443.",
    dns: "Не удалось выпустить сертификат для {d}. Проверьте, что A-запись домена ведёт на этот сервер, а порт 80 открыт снаружи (файрвол хостинга).",
    apt: "certbot не установлен: apt-get install certbot: не удалось получить блокировку /var/lib/dpkg/lock-frontend — её держит процесс 2481 (unattended-upgr)."
  };
  var siteIso = function (days) { return new Date(Date.now() + days * 86400e3).toISOString().replace(/\.\d+Z$/, "Z"); };
  var later = function (ms, fn) { return new Promise(function (res, rej) { setTimeout(function () { try { res(fn()); } catch (e) { rej(e); } }, ms); }); };
  function sitePuny(name) {
    return name.split(".").map(function (p) { return /[^\x00-\x7f]/.test(p) ? "xn--" + p.length + "mock" + (p.charCodeAt(0) % 97) : p; }).join(".");
  }
  function siteDnsDoc(s, state) {
    var d = { state: state, a: [], aaaa: [], own: [SITE_IP, "2001:db8:5::1"], cloudflare: false, aaaa_foreign: false, ready: false, checked_at: new Date().toISOString().replace(/\.\d+Z$/, "Z"), advice: null };
    var n = s.uni;
    if (state === "ok") { d.a = [SITE_IP]; d.ready = true; }
    else if (state === "none") d.advice = "У домена " + n + " нет A-записи — Let's Encrypt проверку не пройдёт. В настройках DNS домена добавьте A-запись со значением " + SITE_IP + " (адрес этого сервера). Запись начинает работать через несколько минут, иногда через час.";
    else if (state === "partial") {
      d.a = [SITE_IP, "198.51.100.20"]; d.aaaa = ["2001:db8:99::7"]; d.aaaa_foreign = true;
      d.advice = "Домен " + n + " указывает ещё и на адреса не этого сервера: 198.51.100.20. Подключения по домену пойдут через раз, а проверка Let's Encrypt может попасть туда. Часто это запись хостера по умолчанию или запись старого сервера — удалите её в DNS-панели и оставьте только адреса этого сервера (" + SITE_IP + ").";
    } else if (state === "cloudflare") {
      d.state = "elsewhere"; d.a = ["198.51.100.201", "198.51.100.202"]; d.aaaa = ["2001:db8:3030::6815:2001"]; d.cloudflare = true;
      d.advice = "Похоже, у домена " + n + " в Cloudflare включено проксирование (оранжевое облако): вместо адреса сервера видны адреса Cloudflare. Для VPN и сертификата переключите запись в «DNS only» (серое облако).";
    }
    return d;
  }
  function siteFresh() {
    var m = SITE_MODE;
    var s = { domain: "", uni: "", dns: null, cert: null, use: { sstp: false, ocserv: false }, email: "", certbot: false, readyAt: 0 };
    if (m === "none" || !m) return s;
    s.uni = m === "idn" ? "vpn.пример.рф" : (m === "long" ? "very-long-subdomain-name-for-the-kaskad-gateway.europe-west-frankfurt.customer-accounts.example-hosting-provider.com" : "vpn.example.com");
    s.domain = sitePuny(s.uni);
    s.dns = siteDnsDoc(s, { dnsnone: "none", partial: "partial", cloudflare: "cloudflare" }[m] || "ok");
    var days = { le: 74, sstp: 74, idn: 74, staging: 80, expiring: 12, on: 74 }[m];
    if (days) { s.cert = { days: days, staging: m === "staging", sha: 1 }; s.certbot = true; }
    if (m === "sstp") s.use.sstp = true;
    if (m === "on") {
      var tp = MOCK_TPLS[7];
      s.mode = "site"; s.path = sitePath(); s.nginx = true;
      s.tpl = { id: tp.id, name: tp.name, source: tp.source, deployed_at: siteIso(-3) };
      if (CHECK) s.check = siteCheckDoc(s, CHECK);
    }
    return s;
  }
  function siteDb() {
    if (!db.site) {
      db.site = siteFresh();
      // сертификат есть: панель открыта из интернета (как будто вошли паролем) — /api/site скажет use.panel
      if (db.site.cert) { db.panel.public = true; db.panel.port = db.panel.port || 43125; if (via === "tunnel") via = "password"; }
      if (SITE_MODE === "sstp") db.gw.host = db.site.domain;
      // каталог без ключа или с неподошедшим ключом: карточка «Ключ GitHub» говорит то же
      if (CATALOG_FAIL === "nokey") db.github = { set: false, source: null, state: "none", checked: null, error: "" };
      if (CATALOG_FAIL === "bad") db.github = { set: true, source: "bot", state: "bad", checked: null, error: "GitHub не принял ключ (ответ 401)." };
    }
    return db.site;
  }
  var siteActive = function (s) { return !!(s.domain && s.cert && s.cert.days >= 0); };
  // адрес открытого режима: домен, когда его сертификат действителен (conf.public_host)
  function siteUrlHost() { var s = siteDb(); return siteActive(s) ? s.domain : SITE_IP; }
  function siteCert(s) {
    if (s.domain && s.cert) return { kind: "letsencrypt", names: [s.domain], not_after: siteIso(s.cert.days), days_left: s.cert.days, staging: s.cert.staging, issuer: s.cert.staging ? "(STAGING) Ersatz Edamame E1" : "R11" };
    return { kind: "selfsigned", names: [SITE_IP], not_after: siteIso(3600), days_left: 3600, staging: false, issuer: "kaskad-gw" };
  }
  function siteAffected(kind) {
    return (SITE_DEVS[kind] || []).map(function (id) { return find(db.devices, id); }).filter(Boolean).map(function (d) {
      var u = find(db.users, d.user);
      return { device: d.id, name: d.name, kind: kind, user_name: u ? u.name : "" };
    });
  }
  function siteStatus() {
    var s = siteDb();
    var act = siteActive(s);
    var on = s.mode === "site";
    return { ok: true, domain: s.domain || null, domain_unicode: s.domain ? s.uni : null, mode: s.domain ? (on ? "site" : "domain") : "off", cert: siteCert(s), dns: s.domain ? s.dns : null,
      port80: CERT_FAIL === "port80" ? { free: false, who: "проброс tcp/80 → 203.0.113.50:80 («Сайт»)" } : { free: true, who: null },
      site: { template: s.tpl ? clone(s.tpl) : null, root: "/var/www/kaskad-site",
        panel_url_masked: on && s.domain && s.path ? "https://" + s.domain + "/" + siteMask(s.path) + "/" : null,
        nginx: { installed: !!s.nginx, active: on, version: s.nginx ? "1.24.0" : null, ours: on, owned: !!s.nginx, acme: false, default_page: CHECK_WARN && on },
        port443: MASK_FAIL === "port443" && !on ? { free: false, who: "проброс tcp/443 → 10.77.1.20:443 («Игровой сервер»)" } : { free: true, who: null },
        check: s.check ? clone(s.check) : null },
      use: { panel: act && (db.panel.public || on), pages: act && (db.panel.public || on), sstp: act && s.use.sstp, ocserv: act && s.use.ocserv },
      use_impact: { sstp: siteAffected("sstp").length, ocserv: siteAffected("ocserv").length },
      use_impact_total: (function () { var ids = {}; ["sstp", "ocserv"].forEach(function (k) {
        if (s.use[k]) siteAffected(k).forEach(function (x) { ids[x.device] = true; });
      }); return Object.keys(ids).length; }()),
      host_is_domain: !!s.domain && db.gw.host === s.domain, renew: { by: s.domain && s.cert ? "certbot.timer" : null }, deps: { certbot: s.certbot }, email: s.email };
  }
  var REISSUE = function (rows, what) {
    if (!rows.length) return "";
    return (rows[0].kind === "ocserv" ? "OpenConnect" : "SSTP") + ": " + what + " — выданные раньше настройки не подключатся. Выдайте их заново (меню 17 → 2 → 1 или панель → «Пользователи»): " + rows.map(function (r) { return r.name + (r.user_name ? " (" + r.user_name + ")" : ""); }).join(", ") + ".";
  };
  function siteRefuse(reason, s) {
    var e = httpErr(409, "", reason === "port80" ? 3 : 1);
    var text = SITE_FAIL[reason].replace("{d}", s.uni).replace("{at}", siteIso(0.125).slice(0, 16).replace("T", " "));
    if (reason === "dns" && s.dns && s.dns.advice) text = s.dns.advice;
    e.message = text;
    e.body = { ok: false, reason: reason, text: text, error: text, code: e.code };
    if (reason === "limit") e.body.retry_after = siteIso(0.125);
    return e;
  }
  function siteNeed(s) { if (!s.domain) throw httpErr(400, "Домен не задан: kaskadctl site domain <имя> (меню 19 → домен).", 2); }
  function siteRoute(method, path, body, query) {
    var s = siteDb();
    if (method === "GET" && path === "/api/site") return siteStatus();
    if (method === "PUT" && path === "/api/site/domain") {
      var name = typeof body.domain === "string" ? body.domain.trim().toLowerCase().replace(/\.$/, "") : "";
      if (!name) throw httpErr(400, "нужен домен, например vpn.example.com", 2);
      if (!/^[a-z0-9а-яё-]+(\.[a-z0-9а-яё-]+)+$/.test(name) || name.length > 253) throw httpErr(400, "«" + name + "» — не домен: нужно имя вида vpn.example.com (буквы, цифры, дефис, точки между частями).", 2);
      var puny = sitePuny(name), affected = [];
      if (s.domain && s.domain !== puny) {
        ["sstp", "ocserv"].forEach(function (k) { if (s.use[k]) affected = affected.concat(siteAffected(k)); });
        s.cert = null; s.use = { sstp: false, ocserv: false };
      }
      var fresh = s.domain !== puny;
      s.domain = puny; s.uni = name;
      if (fresh) s.readyAt = Date.now() + 20000;
      var state = /cloudflare/.test(name) ? "cloudflare" : (/partial/.test(name) ? "partial" : (Date.now() >= s.readyAt ? "ok" : "none"));
      s.dns = siteDnsDoc(s, state);
      bump();
      var out = { ok: true, site: siteStatus(), dns: s.dns, affected: affected };
      if (affected.length) out.text = REISSUE(affected, "домен сменился, сертификат домена убран");
      return out;
    }
    if (method === "DELETE" && path === "/api/site/domain") {
      if (!s.domain) return { ok: true, site: siteStatus(), affected: [], kept: [] };
      vlSiteGate("off", body || {});
      vlSiteAfter("off", body || {});
      var aff = [];
      ["sstp", "ocserv"].forEach(function (k) { if (s.use[k]) aff = aff.concat(siteAffected(k)); });
      var kept = s.cert ? ["/etc/letsencrypt/live/" + s.domain] : [];
      db.site = { domain: "", uni: "", dns: null, cert: null, use: { sstp: false, ocserv: false }, email: s.email, certbot: s.certbot, readyAt: 0, nginx: s.nginx, tpl: s.tpl };
      bump();
      var o2 = { ok: true, site: siteStatus(), affected: aff, kept: kept };
      if (aff.length) o2.text = REISSUE(aff, "домен убран, протокол вернулся на самоподписанный сертификат и адрес узла");
      return o2;
    }
    if (method === "POST" && path === "/api/site/dns") {
      var wait = body.wait === undefined ? 0 : body.wait;
      if (typeof wait !== "number" || wait % 1 || wait < 0 || wait > 300) throw httpErr(400, "wait — число секунд от 0 до 300", 2);
      siteNeed(s);
      return later(wait ? 6000 : 400, function () {
        var keep = s.dns && (s.dns.cloudflare || s.dns.state === "partial");
        if (wait) s.readyAt = Math.min(s.readyAt, Date.now());
        s.dns = siteDnsDoc(s, keep ? (s.dns.cloudflare ? "cloudflare" : "partial") : (Date.now() >= s.readyAt ? "ok" : "none"));
        bump();
        return s.dns;
      });
    }
    if (method === "PUT" && path === "/api/site/email") {
      if (body.email !== undefined && body.email !== null && typeof body.email !== "string") throw httpErr(400, "email — строка (пустая — без почты)", 2);
      var em = (body.email || "").trim();
      if (em && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(em)) throw httpErr(400, "«" + em + "» — не похоже на почту (нужно имя@домен).", 2);
      s.email = em; bump();
      return { ok: true, email: em, warnings: [] };
    }
    if (method === "POST" && path === "/api/site/cert") {
      siteNeed(s);
      if (s.cert && !s.cert.staging && s.cert.days >= 30) return { ok: true, issued: false, kept: true, cert: siteCert(s), warnings: [], consumers: [], text: "Сертификат " + s.uni + " действителен ещё " + s.cert.days + " дн. — не перевыпускаю (лимиты Let's Encrypt), только автопродление." };
      if (CERT_FAIL === "port80") throw siteRefuse("port80", s);
      if (s.dns && (s.dns.state === "none" || s.dns.state === "elsewhere" || s.dns.cloudflare)) throw siteRefuse("dns", s);
      return later(5000, function () {
        if (CERT_FAIL && SITE_FAIL[CERT_FAIL]) throw siteRefuse(CERT_FAIL, s);
        var warns = [];
        if (!s.email) warns.push("Почта для Let's Encrypt не задана — важные письма Let's Encrypt (об отзыве сертификата, об изменении условий) приходить не будут. За сроком сертификата Kaskad следит сам: продлевает его заранее, а если за 21 день до конца продлить не вышло — сообщает в Telegram.");
        if (s.dns && !s.dns.ready && s.dns.advice) warns.push(s.dns.advice);
        if (s.cert && s.cert.staging) warns.push("У " + s.uni + " сейчас тестовый сертификат (staging) — выпускаю настоящий поверх него.");
        s.cert = { days: 89, staging: false, sha: (s.cert ? s.cert.sha : 0) + 1 }; s.certbot = true;
        bump();
        return { ok: true, issued: true, kept: false, cert: siteCert(s), warnings: warns, consumers: [{ name: "панель" }], text: "Сертификат Let's Encrypt для " + s.uni + " выпущен." };
      });
    }
    if (method === "POST" && path === "/api/site/cert/renew") {
      siteNeed(s);
      if (!s.cert) throw httpErr(409, "Сертификата Let's Encrypt для " + s.uni + " нет — сначала выпустите его: kaskadctl site cert issue", 3);
      return later(3000, function () {
        if (CERT_FAIL === "limit" || CERT_FAIL === "net") throw siteRefuse(CERT_FAIL, s);
        var renewed = s.cert.days < 30;
        if (renewed) { s.cert = { days: 89, staging: s.cert.staging, sha: s.cert.sha + 1 }; bump(); }
        return { ok: true, renewed: renewed, cert: siteCert(s) };
      });
    }
    if (method === "PUT" && path === "/api/site/use") {
      if (body.kind !== "sstp" && body.kind !== "ocserv") throw httpErr(400, "kind — sstp или ocserv", 2);
      if (typeof body.on !== "boolean") throw httpErr(400, "on — true или false", 2);
      var kind = body.kind, on = body.on;
      if (on) { siteNeed(s); if (!siteActive(s)) throw httpErr(409, "Сертификата Let's Encrypt для " + s.uni + " пока нет или он истёк — сначала kaskadctl site cert issue.", 3); }
      var same = s.use[kind] === on;
      var rows = same ? [] : siteAffected(kind);
      var title = kind === "sstp" ? "SSTP" : "OpenConnect";
      var warnings = [];
      if (on && !(db.inbounds[kind] && db.inbounds[kind].installed)) warnings.push(title + " пока не установлен — сертификат домена он возьмёт при установке.");
      if (on && s.cert && s.cert.staging) warnings.push("Сертификат тестовый (staging): клиенты " + title + " ему не поверят.");
      var text = REISSUE(rows, on ? "адрес сервера в настройках меняется на " + s.uni + ", сертификат — на Let's Encrypt" : "протокол вернулся на самоподписанный сертификат и адрес узла");
      if (body.dry_run === true || same) {
        var useNow = { sstp: s.use.sstp, ocserv: s.use.ocserv };
        if (body.dry_run === true) useNow[kind] = on;
        var o3 = { ok: true, use: useNow, affected: rows, text: text, warnings: warnings };
        if (body.dry_run === true) o3.dry_run = true;
        return o3;
      }
      return later(1500, function () { s.use[kind] = on; bump(); return { ok: true, use: { sstp: s.use.sstp, ocserv: s.use.ocserv }, affected: rows, text: text, warnings: warnings }; });
    }
    var mask = maskRoute(s, method, path, body, query);
    if (mask !== undefined) return mask;
    throw httpErr(404, "Нет такого пути: " + path, null);
  }

  // ── Сайт-маскировка (волна 4.0-C: kaskad.site.panel, ops.mode/template_set/panel_url/rotate_panel_path/check) ──
  var MOCK_CATS = [["business", "Бизнес и корпоративные", 16], ["landing", "Лендинги", 18], ["restaurant", "Кафе и рестораны", 12],
    ["education", "Образование", 10], ["portfolio", "Портфолио", 8], ["other", "Разное", 10]];
  var MOCK_WORDS = { business: ["Консалтинг", "Логистика", "Юридическая фирма", "Агентство", "Строительство", "Бухгалтерия", "Финансы", "Страхование"],
    landing: ["Приложение", "Стартап", "Продукт", "Конференция", "Подписка", "Сервис", "Онлайн-курс", "Мероприятие", "Запуск"],
    restaurant: ["Кафе", "Пиццерия", "Кофейня", "Бистро", "Пекарня", "Суши-бар"], education: ["Школа", "Курсы", "Университет", "Детский сад", "Репетитор"],
    portfolio: ["Фотограф", "Дизайнер", "Архитектор", "Иллюстратор"], other: ["Блог", "Путешествия", "Спорт", "Ремонт", "Ёлочные игрушки"] };
  var MOCK_TPLS = (function () {
    var out = [];
    MOCK_CATS.forEach(function (c) {
      var words = MOCK_WORDS[c[0]];
      for (var i = 0; i < c[2]; i += 1) {
        var w = words[i % words.length], n = Math.floor(i / words.length) + 1, owner = ["colorlib", "themewagon", "startbootstrap", "html5up", "bootstrapmade"][i % 5];
        var id = "gh_" + owner + "_" + c[0] + "_" + (i + 1);
        out.push({ id: id, name: w + (n > 1 ? " " + n : "") + (i === 3 ? " — очень длинное название шаблона, которое не помещается в одну строку на телефоне" : ""), category: c[0],
          source: "github", license: i % 4 === 0 ? "CC BY 3.0" : "MIT", preview: "https://example.com/templates/" + id + "/",
          description: "Сайт: " + w.toLowerCase() + ". Адаптивная вёрстка, " + (i % 2 ? "тёмная тема" : "светлая тема") + ", " + (3 + i % 5) + " страниц.",
          repo: "https://github.com/" + owner + "/" + c[0] + "-" + (i + 1), author: owner });
      }
    });
    return out;
  }());
  var CHECK_VERDICTS = ["ok", "cert_expiring", "http_error", "panel_unreachable", "dns_mismatch", "no_cert", "no_site"];
  var TPL_TEXT = { no_index: "В шаблоне «%s» нет готовой страницы index.html — в репозитории исходники для сборки, а не сайт.",
    download_failed: "Шаблон «%s» не скачался: репозиторий недоступен или нет связи с GitHub.",
    timeout: "Шаблон «%s» не скачался за 90 секунд: GitHub отвечает медленно или репозиторий слишком большой.",
    too_big: "Шаблон «%s» больше 100 МБ — для сайта-маскировки это слишком много, выберите другой.",
    app_shell: "Шаблон «%s» — оболочка приложения без содержимого, в браузере будет пустой экран.",
    broken_assets: "В шаблоне «%s» страница ссылается на стили и скрипты, которых в репозитории нет.",
    bad_repo: "У шаблона «%s» в каталоге недопустимый адрес репозитория — выберите другой.",
    nokey: "Каталог шаблонов сайта лежит в закрытом репозитории Kaskad, и без ключа GitHub его не скачать (шаблон «%s»).",
    net: "Нет связи с GitHub (raw.githubusercontent.com) — шаблон «%s» не скачан.",
    gone: "Шаблон «%s» удалён автором — выберите другой." };
  /* «Жив ли шаблон» (3.2.21): кэш сервера (TPL_HEALTH: id → state) и пакетная проверка страницы. Состояние — по
     номеру шаблона в каталоге: каждый 9-й с 5-го удалён автором, с 7-го — без образца, с 9-го — не проверить. */
  var TPL_HEALTH = {};
  var TPL_HEALTH_TEXT = { alive: "Шаблон доступен", gone: "Шаблон удалён автором — выбрать нельзя",
    no_preview: "Образец снят автором, сам шаблон ставится", unknown: "Не удалось проверить" };
  function tplHealthState(id) {
    var i = 0;
    MOCK_TPLS.some(function (t, k) { if (t.id === id) { i = k; return true; } return false; });
    return ["alive", "alive", "alive", "alive", "gone", "alive", "no_preview", "alive", "unknown"][i % 9];
  }
  function tplHealthDoc(st) { return st ? { state: st, text: TPL_HEALTH_TEXT[st] } : null; }
  var MASK_TEXT = {
    no_domain: "Для сайта сначала задайте домен: kaskadctl site domain <имя>.",
    no_cert: "Для сайта нужен действующий боевой сертификат Let's Encrypt для {d}. Сначала выпустите его: kaskadctl site cert issue (тестовый --staging для сайта не подходит).",
    port443: "Порт 443/tcp занят: проброс tcp/443 → 10.77.1.20:443 («Игровой сервер»). Сайт на домене не сможет открыться. Оставьте режим «домен», перенесите протокол на другой порт или дождитесь режима 4.1 «всё на 443»; чужую службу Kaskad не останавливает.",
    neighbour: "На 443 уже настроен nginx соседа (govless). Оставьте режим «домен», перенесите его протокол или дождитесь режима 4.1 «всё на 443».",
    port80: "Порт 80/tcp занят: проброс tcp/80 → 203.0.113.50:80 («Сайт»). Сайту он нужен для перехода с http на https и для продления сертификата Let's Encrypt. Уберите этот проброс или остановите службу, затем повторите. Kaskad чужие службы не останавливает.",
    apt: "Не удалось поставить nginx для сайта: apt-get install nginx: не удалось получить блокировку /var/lib/dpkg/lock-frontend — её держит процесс 2481 (unattended-upgr).",
    nginx_test: "nginx не принял настройки сайта: nginx: [emerg] duplicate listen options for [::]:443 in /etc/nginx/sites-enabled/other-site:12. Прежний файл возвращён; проверьте конфигурацию nginx соседа."
  };
  function sitePath() {
    var abc = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_", out = "app-";
    for (var i = 0; i < 86; i += 1) out += abc[Math.floor(Math.random() * abc.length)];
    return out;
  }
  function siteMask(p) { return p.length <= 16 ? "••••" : p.slice(0, 8) + "••••" + p.slice(-4); }
  function maskRefuse(reason, s, tpl) {
    var text, e;
    if (reason === "template") {
      var tr = TPL_TEXT[TPL_FAIL] ? TPL_FAIL : "download_failed", tid = tpl ? tpl.id : null;
      text = "Шаблон сайта не развёрнут: " + TPL_TEXT[tr].replace("%s", tid || "случайный") + " Сайт не включён, всё осталось как было.";
      e = httpErr(409, text, 3);
      e.body = { ok: false, reason: "template", template_reason: tr, template_id: tid, text: text, error: text, code: 3 };
      return e;
    }
    text = MASK_TEXT[reason].replace("{d}", s.uni || "домена");
    e = httpErr(409, text, 3);
    e.body = { ok: false, reason: reason, text: text, error: text, code: 3 };
    return e;
  }
  function tplFold(x) { return String(x || "").toLowerCase().replace(/ё/g, "е"); }
  function tplRec(t) { return { id: t.id, name: t.name, category: t.category, source: t.source, license: t.license, preview: t.preview, description: t.description, repo: t.repo, author: t.author, health: tplHealthDoc(TPL_HEALTH[t.id]) }; }
  function catalogFail() {
    if (!CATALOG_FAIL) return null;
    var keyFixed = !!db.ghSaved;
    if ((CATALOG_FAIL === "nokey" || CATALOG_FAIL === "bad") && keyFixed) return null;
    if (CATALOG_FAIL === "nokey") return httpErr(500, "Каталог шаблонов сайта лежит в закрытом репозитории Kaskad, и без ключа GitHub его не скачать. Задайте ключ (SSH: gokaskad → 8 → 6) и повторите.", 1, { reason: "nokey", needs_key: true });
    if (CATALOG_FAIL === "bad") return httpErr(500, "GitHub не принял ключ (ответ 401) — каталог шаблонов сайта не скачан. Проверьте ключ GitHub (SSH: gokaskad → 8 → 6) и повторите.", 1, { reason: "bad", needs_key: true });
    if (CATALOG_FAIL === "net") return httpErr(500, "Нет связи с GitHub (raw.githubusercontent.com) — каталог шаблонов сайта не скачан. Проверьте интернет на сервере и повторите.", 1, { reason: "net" });
    return httpErr(500, "В репозитории Kaskad (ветки main, test) нет каталога шаблонов сайта — обновите Kaskad и повторите.", 1, { reason: "missing" });
  }
  function tplPick(id) {
    if (id === null) { var pool = MOCK_TPLS.filter(function (t) { return t.category !== "portfolio"; }); return pool[Math.floor(Math.random() * pool.length)]; }
    var got = MOCK_TPLS.filter(function (t) { return t.id === id; })[0];
    if (!got) throw httpErr(400, "Шаблона «" + id + "» нет в каталоге.", 2, { reason: "unknown_template" });
    return got;
  }
  function siteCheckDoc(s, want) {
    var v = s.mode !== "site" ? "no_site" : (CHECK_VERDICTS.indexOf(want) >= 0 ? want : "ok");
    var d = { ok: v === "ok", verdict: v, checked_at: new Date().toISOString().replace(/\.\d+Z$/, "Z") };
    if (v === "http_error") d.http = { root: 200, redirect: 200 };
    if (v === "cert_expiring") d.days_left = 12;
    if (v === "ok") d.warnings = CHECK_WARN ? ["на порту 80 отвечает страница nginx по умолчанию — сервер выдаёт себя"] : [];
    return d;
  }
  function maskRoute(s, method, path, body, query) {
    var on = s.mode === "site";
    if (method === "PUT" && path === "/api/site/mode") {
      if (body.mode !== "site" && body.mode !== "domain") throw httpErr(400, "mode — site или domain", 2);
      if (!s.domain) throw maskRefuse("no_domain", s);
      if (body.mode === "domain") {
        if (!on) return { ok: true, site: siteStatus(), unchanged: true };
        if (MASK_FAIL === "nginx_test") throw maskRefuse("nginx_test", s);
        vlSiteGate("off", body);
        return later(2500, function () { s.mode = "domain"; s.check = null; vlSiteAfter("off", body); bump(); return { ok: true, site: siteStatus() }; });
      }
      if (body.template !== undefined && body.template !== "random" && typeof body.template !== "string") throw httpErr(400, "template — номер шаблона или random", 2);
      if (!s.cert || s.cert.staging || s.cert.days < 0) throw maskRefuse("no_cert", s);
      if (MASK_FAIL && MASK_FAIL !== "template" && MASK_TEXT[MASK_FAIL]) throw maskRefuse(MASK_FAIL, s);
      var want = body.template && body.template !== "random" ? tplPick(body.template) : null;
      vlSiteGate("on", body);
      return later(8000, function () {
        // шаблон разворачивается первым: его отказ — до nginx, всё остаётся как было
        if (MASK_FAIL === "template" || (want && TPL_FAIL && TPL_TEXT[TPL_FAIL])) throw maskRefuse("template", s, want);
        var stub = !want && FALLBACK;
        var tp = stub ? { id: "builtin_stub", name: "Страница-заглушка", source: "" } : (want || tplPick(null));
        s.mode = "site"; s.nginx = true; s.path = s.path || sitePath(); s.check = null;
        vlSiteAfter("on", body);
        s.tpl = { id: tp.id, name: tp.name, source: tp.source, deployed_at: new Date().toISOString().replace(/\.\d+Z$/, "Z") };
        bump();
        return { ok: true, site: siteStatus(), template: clone(s.tpl), fallback: stub,
          warnings: stub ? ["Шаблоны сайта не скачались (3 попытки: нет связи с GitHub) — поставлена страница-заглушка. Шаблон можно сменить."] : [] };
      });
    }
    if (method === "GET" && (path === "/api/site/categories" || path === "/api/site/templates")) {
      var cf = catalogFail();
      if (cf) return later(700, function () { throw cf; });
      if (path === "/api/site/categories") return { ok: true, total: MOCK_TPLS.length, categories: MOCK_CATS.map(function (c) { return { id: c[0], title: c[1], icon: "", count: c[2] }; }) };
      var cat = query.get("category") || null, q = String(query.get("query") || "").trim().slice(0, 64), page = parseInt(query.get("page") || "1", 10) || 1;
      if (cat && !MOCK_CATS.some(function (c) { return c[0] === cat; })) throw httpErr(400, "Категории «" + cat + "» в каталоге нет.", 2, { reason: "unknown_category" });
      var pool = MOCK_TPLS.filter(function (t) { return !cat || t.category === cat; });
      if (q) {
        var w = tplFold(q);
        var first = pool.filter(function (t) { return tplFold(t.name).indexOf(w) >= 0 || tplFold(t.id).indexOf(w) >= 0; });
        pool = first.concat(pool.filter(function (t) { return first.indexOf(t) < 0 && tplFold(t.description).indexOf(w) >= 0; }));
      }
      var pages = Math.max(1, Math.ceil(pool.length / 25));
      page = Math.max(1, Math.min(page, pages));
      return later(350, function () { return { ok: true, items: pool.slice((page - 1) * 25, page * 25).map(tplRec), page: page, pages: pages, per_page: 25, total: pool.length, category: cat, query: q || null }; });
    }
    if (method === "POST" && path === "/api/site/templates/check") {
      var ids = body.ids;
      if (!Array.isArray(ids) || ids.some(function (x) { return typeof x !== "string" || !x; })) throw httpErr(400, "ids — список номеров шаблонов", 2);
      if (ids.length > 25) throw httpErr(400, "ids — не больше 25 шаблонов за раз (видимая страница)", 2);
      return later(1800, function () {
        var items = {};
        ids.forEach(function (id) {
          var known = MOCK_TPLS.some(function (t) { return t.id === id; });
          var st = known ? tplHealthState(id) : "unknown";
          if (st !== "unknown") TPL_HEALTH[id] = st;          // «не проверить» сервер не хранит
          items[id] = tplHealthDoc(st);
        });
        return { ok: true, items: items };
      });
    }
    if (method === "POST" && path === "/api/site/template") {
      var tid = body.id === undefined || body.id === "random" ? null : body.id;
      if (tid !== null && (typeof tid !== "string" || !tid)) throw httpErr(400, "id — номер шаблона или random", 2);
      var rec = tplPick(tid);
      if (tid !== null && TPL_HEALTH[tid] === "gone") return { ok: false, id: rec.id, reason: "gone", text: TPL_TEXT.gone.replace("%s", rec.id), errors: [], warnings: [], root: "/var/www/kaskad-site" };
      return later(3000, function () {
        if (tid !== null && TPL_FAIL && TPL_TEXT[TPL_FAIL]) return { ok: false, id: rec.id, reason: TPL_FAIL, text: TPL_TEXT[TPL_FAIL].replace("%s", rec.id), errors: [], warnings: [], root: "/var/www/kaskad-site" };
        s.tpl = { id: rec.id, name: rec.name, source: rec.source, deployed_at: new Date().toISOString().replace(/\.\d+Z$/, "Z") };
        bump();
        return { ok: true, id: rec.id, name: rec.name, source: rec.source, fallback: false, backup: "/var/backups/kaskad/site/previous", tries: 1, errors: [], warnings: [], root: "/var/www/kaskad-site" };
      });
    }
    if (method === "GET" && path === "/api/site/panel-url") {
      var pa = panelAddress();
      if (!pa) throw httpErr(409, "Панель не открыта из интернета: нет ни сайта на домене, ни доступа по порту.", 3);
      var full = pa.kind === "domain" ? "https://" + s.domain + "/" + s.path + "/" : "https://" + siteUrlHost() + ":" + db.panel.port + "/" + s.path + "/";
      return { ok: true, url: full, masked: pa.masked, kind: pa.kind, fallback: pa.fallback, fallback_masked: pa.fallback };
    }
    if (method === "POST" && path === "/api/site/panel-path/rotate") {
      if (!panelAddress()) throw httpErr(409, "Секретный адрес меняется, когда панель открыта из интернета.", 3);
      return later(1200, function () { s.path = sitePath(); bump(); var pa = panelAddress(); return { ok: true, url: pa.masked, masked: pa.masked, kind: pa.kind }; });
    }
    if (method === "POST" && path === "/api/site/check") {
      return later(2500, function () { s.check = siteCheckDoc(s, CHECK); bump(); return clone(s.check); });
    }
    return undefined;
  }
  siteDb();

  // ── Подписки выходов (kaskad.gw.exitsubs) ─────────────────────────────────
  // Как exitsubs.public: адреса в ответе нет; keys — её ключей в пуле; text — «обновлена 3 ч назад: ключей 4» или
  // «не обновлялась N ч: причина». Адрес макет не хранит — только отпечаток (повтор — 409)
  function subName(sb) { return sb.label || sb.domain || "подписка " + sb.n; }
  function subTitle(sb) { var n = subName(sb); return sb.domain || sb.label ? n : n.charAt(0).toUpperCase() + n.slice(1); }
  function subAgo(sec) { sec = Math.max(0, Math.floor(sec)); return sec < 3600 ? Math.max(1, Math.floor(sec / 60)) + " мин" : (sec < 172800 ? Math.floor(sec / 3600) + " ч" : Math.floor(sec / 86400) + " дн."); }
  function subFailing(sb) { return !!sb.error && (!sb.last_ok || (sb.last_try || 0) > sb.last_ok); }
  function subText(sb) {
    var now = nowS();
    if (subFailing(sb)) return "не обновлялась " + Math.floor((now - (sb.last_ok || Date.parse(sb.added) / 1000 || now)) / 3600) + " ч: " + sb.error.replace(/\.$/, "") + ".";
    if (!sb.last_ok) return "ещё не обновлялась";
    return "обновлена " + subAgo(now - sb.last_ok) + " назад: ключей " + sb.count + (sb.full ? "; не вошло в пул (предел 100): " + sb.full : "") +
      ((sb.excluded || []).length ? "; исключено вручную: " + sb.excluded.length : "");
  }
  function subPublic(sb) {
    var owned = sb.keys.filter(function (id) { return !!find(db.exits, id); });
    return { id: sb.id, n: sb.n, name: subName(sb), domain: sb.domain, label: sb.label || "", auto: sb.auto, keys: owned.length, exits: owned, count: sb.count, full: sb.full, excluded: (sb.excluded || []).length,
      added: sb.added, last_ok: sb.last_ok, last_try: sb.last_try, error: sb.error, stale: subFailing(sb) || (sb.auto && !!sb.last_ok && nowS() - sb.last_ok > 26 * 3600),
      text: subText(sb), pinned: false };
  }
  function subsDoc() { return db.subs.map(subPublic); }
  function subOwner(id) { var sb = db.subs.filter(function (x) { return x.keys.indexOf(id) >= 0; })[0]; return sb ? { sub: sb.id, name: subName(sb) } : null; }
  function subFind(id) { var sb = find(db.subs, id); if (!sb) throw httpErr(404, "Нет такой подписки: " + id + " (список — kaskadctl gw exit-sub list).", 2, { sub: id }); return sb; }
  // «скачать» подписку: два ключа VLESS (у новой) — как будто в подписке они и есть
  function subPull(sb) {
    var now = nowS();
    sb.last_try = now;
    if (sb.id === "s-4d5e6f") return { id: sb.id, name: subName(sb), ok: false, error: sb.error, text: subTitle(sb) + " не обновилась: " + sb.error };
    var added = 0;
    if (!sb.count) {
      [1, 2].forEach(function (i) {
        var e = exit(rid("e-", 10), db.exits.length + 1, "vless", "", "node" + i + "." + (sb.domain || "example.net"), "VLESS · node" + i, "unknown", "ещё не проверялся", null);
        db.exits.push(e); sb.keys.push(e.id); added += 1;
      });
      sb.count = 2;
    }
    sb.last_ok = now; sb.error = "";
    var text = subTitle(sb) + (added ? ": добавлено " + added + ", убрано 0 (ключей в подписке: " + sb.count + ")." : ": без изменений (ключей в подписке: " + sb.count + ").");
    return { id: sb.id, name: subName(sb), ok: true, added: added, removed: 0, count: sb.count, full: 0, warnings: [], text: text };
  }
  function subAdd(body) {
    var url = typeof body.url === "string" ? body.url.trim() : "";
    if (!/^https?:\/\/[^\s\/?#]+\S*$/i.test(url)) throw httpErr(400, "Вставьте адрес подписки: http(s)://…", 2);
    var print = keyPrint(url), host = /^https?:\/\/([^\s\/?#:]+)/i.exec(url)[1].toLowerCase();
    var had = db.subs.filter(function (x) { return x._print === print; })[0];
    if (had) throw httpErr(409, "Эта подписка уже добавлена (" + subName(had) + ").", 3, { sub: had.id });
    if (db.subs.length >= 20) throw httpErr(409, "Подписок уже 20 — больше не добавить.", 3);
    if (/fail/i.test(url)) throw httpErr(400, "Сервер подписки ответил 404 — проверьте адрес. Ключи пула не тронуты.", 1);
    // https://IP, а сертификат выдан на домен (адрес с «certdomain»): сервер называет домен, адрес не возвращает
    if (/^https:\/\/[0-9.]+[:/]/i.test(url) && /certdomain/i.test(url)) throw httpErr(400, "Сертификат выдан на домен sub.example.net — добавьте подписку по домену или по http.", 1, { cert_domain: "sub.example.net", same_server: true });
    var sb = { id: rid("s-", 6), n: db.subNext++, domain: /^[0-9.]+$/.test(host) ? "" : host, auto: body.auto !== false, keys: [], count: 0, full: 0,
      added: new Date().toISOString().replace(/\.\d+Z$/, "Z"), last_ok: null, last_try: null, error: "", _print: print };
    url = null;
    db.subs.push(sb);
    var res = subPull(sb);
    bump();
    return { ok: true, sub: subPublic(sb), result: res, note: "", text: "Подписка добавлена: " + subName(sb) + ". " + res.text, warnings: [] };
  }
  function subsRefresh(id) {
    var list = id ? [subFind(id)] : db.subs.slice();
    var results = list.map(subPull);
    bump();
    var out = { ok: true, all_ok: results.every(function (r) { return r.ok; }), text: results.length ? results.map(function (r) { return r.text; }).join(" ") : "Подписок нет.", subs: subsDoc() };
    if (id) out.result = results[0]; else out.results = results;
    return out;
  }
  // «Вернуть исключённые» (POST gw/exit-subs/<id>/restore): подписка скачивается заново, удалённые вручную ключи — снова в пуле
  function subRestore(id) {
    var sb = subFind(id), n = (sb.excluded || []).length;
    (sb.excluded || []).forEach(function (old, i) {
      var e = exit(old, db.exits.length + 1, "vless", "", "back" + (i + 1) + "." + (sb.domain || "example.net"), "VLESS · back" + (i + 1), "unknown", "ещё не проверялся", null);
      db.exits.push(e); sb.keys.push(e.id);
    });
    sb.excluded = [];
    sb.last_ok = sb.last_try = nowS();
    bump();
    var res = { id: sb.id, name: subName(sb), ok: true, added: n, removed: 0, count: sb.count, full: 0, warnings: [], text: subTitle(sb) + ": добавлено " + n + ", убрано 0 (ключей в подписке: " + sb.count + ")." };
    return { ok: true, all_ok: true, restored: n, result: res, text: (n ? "Исключённые ключи подписки «" + subName(sb) + "» возвращены. " : "У подписки «" + subName(sb) + "» исключённых ключей нет. ") + res.text, subs: subsDoc() };
  }
  function subDel(id, keep) {
    var sb = subFind(id), removed = 0;
    if (!keep) { removed = sb.keys.filter(function (k) { return !!find(db.exits, k); }).length; db.exits = db.exits.filter(function (e) { return sb.keys.indexOf(e.id) < 0; }); db.exits.forEach(function (e, i) { e.index = i + 1; }); }
    db.subs = db.subs.filter(function (x) { return x.id !== id; });
    bump();
    return { ok: true, id: id, removed: removed, text: "Подписка «" + subName(sb) + "» удалена" + (keep ? "; её ключи остались в пуле как добавленные вручную." : (removed ? "; убрано ключей: " + removed + "." : ".")) };
  }

  // ── Копия настроек (kaskad.backup) ────────────────────────────────────────
  // Файл макета — не настоящее шифрование: заголовок KASKAD-BACKUP 1 и части; пароль «wrong» — неверный, «blocker» в
  // файле — препятствие переноса (как iptables у сервера). Сводка — по данным макета, без секретов
  var BACKUP_PARTS = ["gateway", "exits", "forwards", "site", "bot", "mcp", "panel", "github"];
  var BACKUP_TITLES = { gateway: "шлюз: пользователи, устройства, протоколы и их ключи, маршруты, правила", exits: "выходы: пул ключей и подписки",
    forwards: "пробросы портов, имена серверов, мониторинг", site: "домен и сайт (без сертификата)", bot: "Telegram-бот", mcp: "ключи ИИ-агентов (MCP)",
    panel: "настройки веб-панели (без пароля)", github: "ключ GitHub и ветка обновлений" };
  var BACKUP_SHORT = { gateway: "шлюз", exits: "выходы", forwards: "пробросы", site: "домен", bot: "бот", mcp: "ключи агентов", panel: "панель", github: "ключ GitHub" };
  function backupSummary(parts) {
    var out = {};
    var all = { gateway: { present: true, users: db.users.length, devices: db.devices.length, inbounds: INBOUND_DEFS.filter(function (x) { return db.inbounds[x[0]].installed; }).map(function (x) { return x[0]; }), host: db.gw.host, enabled: db.gw.enabled },
      exits: { keys: db.exits.length, subs: db.subs.length }, forwards: { forwards: db.forwards.length, names: 2, monitors: 0 },
      site: { domain: "vpn.example.net", mode: "domain", template: "" }, bot: { token: true, chat: true }, github: { key: !!db.github.set, branch: "dev" },
      mcp: { keys: 1 }, panel: { present: true, require_password: !!db.panel.require, public: !!db.panel.public, proxy: false } };
    parts.forEach(function (p) { out[p] = all[p]; });
    return out;
  }
  function backupParts(v) {
    if (v === undefined || v === null) return BACKUP_PARTS.slice();
    var list = Array.isArray(v) ? v : String(v).split(",");
    list.forEach(function (p) { if (BACKUP_PARTS.indexOf(p) < 0) throw httpErr(400, "Неизвестная часть копии: " + p + " (части: " + BACKUP_PARTS.join(", ") + ").", 2); });
    return BACKUP_PARTS.filter(function (p) { return list.indexOf(p) >= 0; });
  }
  function backupDoc(body) {
    var text = typeof body.file === "string" ? body.file : "";
    if (!text.trim()) throw httpErr(400, "Выберите файл копии (.kbk).", 2);
    if (typeof body.password !== "string" || !body.password) throw httpErr(400, "Введите пароль копии.", 2);
    if (text.indexOf("KASKAD-BACKUP 1") !== 0) throw httpErr(400, "Это не файл копии Kaskad (его делает «Скачать копию» в панели или меню 16).", 2);
    if (body.password === "wrong") throw httpErr(400, "Неверный пароль копии.", 2);
    var head = {};
    try { head = JSON.parse(text.split("\n")[1] || "{}"); } catch (e) { throw httpErr(400, "Файл копии повреждён (не сходится контрольная сумма) — скачайте или скопируйте его заново.", 2); }
    return { parts: backupParts(head.parts), blocker: text.indexOf("blocker") >= 0, created: head.created || "", version: head.version || "" };
  }
  function backupPreview(doc, agents) {
    var words = { add: "перенесётся", replace: "заменит", keep: "останется", none: "нет в копии" };
    var rows = doc.parts.map(function (p) {
      var row = { gateway: ["replace", "пользователей 3, устройств 7, протоколы: wg, awg2, awg3; заменит здесь: пользователей " + db.users.length + ", устройств " + db.devices.length],
        exits: ["replace", "ключей 3, подписок 1; заменит здесь: ключей " + db.exits.length + ", подписок " + db.subs.length], forwards: ["add", "пробросов 2, имён серверов 2"],
        site: ["add", "домен vpn.example.net; сертификат выпускается заново, когда домен укажет на этот сервер"], bot: ["replace", "токен и чат владельца; заменит бота этого сервера"],
        github: [db.github.set ? "keep" : "add", "ключ GitHub" + (db.github.set ? "; здесь уже свой — останется он" : "")], mcp: agents ? ["add", "ключей агентов 1 (добавятся к здешним: 0)"] : ["none", "ключей агентов 1 — не переносятся: агент с таким ключом управляет сервером. Перенести — отдельной отметкой и только из своей копии"],
        panel: ["replace", "пароль спрашивать: нет; пароль панели не переносится"] }[p];
      return { part: p, title: BACKUP_TITLES[p], action: row[0], text: row[1], file: {}, here: {} };
    });
    var warnings = [], blockers = [];
    var address = { old: "", here: "", kind: "", changed: false };
    if (doc.parts.indexOf("gateway") >= 0) {
      address = { old: "198.51.100.77", here: db.gw.host || SRV, kind: "ip", changed: true };
      warnings.push("Устройства подключаются по адресу 198.51.100.77, а у этого сервера — " + address.here + ": после переноса выдайте им настройки заново (меню 17 → устройства или панель «Пользователи»).");
    }
    if (doc.parts.indexOf("bot") >= 0) warnings.push("Один токен бота — один сервер: после переноса остановите бота на старом сервере (меню 8), иначе Telegram будет отдавать сообщения то одному, то другому.");
    if (doc.parts.indexOf("mcp") >= 0 && agents) warnings.push("Ключи ИИ-агентов из копии (1) добавятся: агент с таким ключом управляет этим сервером по SSH — все действия, включая выгрузку копии и обновление. Переносите их только из своей копии.");
    if (doc.blocker) blockers.push("Шлюз и пробросы переносятся только на движок nftables, а здесь — iptables: меню 16 → 1 («Перевести на nftables»), потом повторите.");
    var text = rows.map(function (r) { return "• " + BACKUP_SHORT[r.part] + " — " + words[r.action] + ": " + r.text; })
      .concat(warnings.map(function (w) { return "! " + w; })).concat(blockers.map(function (b) { return "✗ " + b; }));
    var out = { ok: !blockers.length, parts: rows, address: address, warnings: warnings, blockers: blockers, created: doc.created, version: doc.version, text: text };
    if (doc.parts.indexOf("mcp") >= 0) out.agents = { keys: 1, on: !!agents };      // SEC-3372 З-3: отметка в предпросмотре
    return out;
  }
  function backupRoute(method, path, body) {
    if (method === "GET" && path === "/api/backup/summary") {
      var size = 18000 + db.devices.length * 900;
      return { ok: true, parts: BACKUP_PARTS.slice(), titles: clone(BACKUP_TITLES), summary: backupSummary(BACKUP_PARTS), size: size,
        undo: { available: !!db.snapshot, created: "", path: db.snapshot ? "/var/backups/kaskad/backup-import-20261009T120000Z" : "", agent_keys_gone: 0 } };
    }
    if (method === "POST" && path === "/api/backup/export") {
      if (typeof body.password !== "string") throw httpErr(400, "Задайте пароль копии.", null);
      if (body.password.length < 8) throw httpErr(400, "Пароль копии — не короче 8 знаков.", 2);
      var parts = backupParts(body.parts).filter(function (p) { return (body.without || []).indexOf(p) < 0; });
      if (!parts.length) throw httpErr(400, "Нечего сохранять: все части исключены.", null);
      var created = new Date().toISOString().replace(/\.\d+Z$/, "Z");
      var head = JSON.stringify({ format: "kaskad-backup", v: 1, created: created, version: db.version, parts: parts });
      var bodyText = "";
      for (var i = 0; i < 40; i++) bodyText += rid("", 76) + "\n";                   // не шифр — заполнитель вида base64
      var file = "KASKAD-BACKUP 1\n" + head + "\n" + bodyText + "MAC " + rid("", 64) + "\n";
      return { ok: true, file: file, name: "kaskad-" + (db.gw.host || "copy") + "-" + created.slice(0, 10).replace(/-/g, "") + ".kbk", parts: parts, summary: backupSummary(parts), size: file.length };
    }
    if (method === "POST" && path === "/api/backup/preview") {
      var doc = backupDoc(body);
      return { ok: true, preview: backupPreview(doc, body.agents === true), parts: doc.parts };
    }
    if (method === "POST" && path === "/api/backup/import") {
      var d2 = backupDoc(body), pv = backupPreview(d2, body.agents === true);
      if (pv.blockers.length) throw httpErr(409, pv.blockers.join(" "), 3, { preview: pv });
      var done = pv.parts.filter(function (r) { return r.action !== "none"; }).map(function (r) { return r.part; });
      db.snapshot = true;
      bump();
      return later(1500, function () {
        return { ok: true, parts: done, backup: "/var/backups/kaskad/backup-import-20261009T120000Z", warnings: pv.warnings, notes: ["протоколы установлены: wg, awg2, awg3"], address: pv.address,
          text: ["Перенесено: " + done.map(function (p) { return BACKUP_SHORT[p]; }).join(", ") + ".", "• протоколы установлены: wg, awg2, awg3"].concat(pv.warnings.map(function (w) { return "! " + w; }))
            .concat(["Прежние настройки сохранены: /var/backups/kaskad/backup-import-20261009T120000Z (вернуть — kaskadctl backup undo)."]) };
      });
    }
    if (method === "POST" && path === "/api/backup/undo") {
      if (!db.snapshot) throw httpErr(400, "Копии прежних настроек нет: импорт на этом сервере не делали.", 1);     // как routes.core_error: 400
      bump();
      return { ok: true, parts: BACKUP_PARTS.slice(), backup: "/var/backups/kaskad/backup-undo-20261009T121000Z", warnings: [], notes: [], agents_cut: 0,
        text: ["Перенесено: " + BACKUP_PARTS.map(function (p) { return BACKUP_SHORT[p]; }).join(", ") + "."], restored_from: "/var/backups/kaskad/backup-import-20261009T120000Z" };
    }
    throw httpErr(404, "Нет такого пути: " + path, null);
  }

  // ── Маршрутизатор ─────────────────────────────────────────────────────────
  function find(list, id) { return list.filter(function (x) { return x.id === id; })[0]; }
  // Движок AmneziaWG (k3/SPEC-awg-engine.md): узел после обновления на программе — совет «Перейти на модуль ядра»
  function awgDoc() {
    var k = db.awgEngine === "kernel";
    return { ok: true, want: db.awgEngine || null, active: k ? "kernel" : "program", enabled: k, fallback: false, reason: null, code: null,
      pending: !db.awgEngine, force: false, has_awg: true, mix: [], warnings: [],
      offer: { show: !k && db.awgEngine !== "program", blocked: [], pending: !db.awgEngine },
      text: k ? "Движок AmneziaWG: модуль ядра" : (db.awgEngine === "program" ? "Движок AmneziaWG: программа — так выбрано" : "Движок AmneziaWG: программа — модуль ядра ещё не включали (рекомендуется)") };
  }
  // VLESS Reality (api.py gw_vless_*): сайты маскировки, их проверка «с узла», смена сайта, новые ключи узла
  function vlessCheck(sni) {
    var m = VLESS_MASKS_ALL.filter(function (x) { return x[0] === sni; })[0];
    var ok = m ? m[2] : !/^(localhost|example\.)/.test(sni);
    return { ok: ok, tls: ok ? "TLSv1.3" : "TLSv1.2", alpn: ok && (!m || m[3]) ? "h2" : "http/1.1", h2: ok && (!m || m[3]), why: ok ? "" : "сайт ответил TLSv1.2, а нужен TLS 1.3", at: new Date().toISOString().replace(/\.\d+Z$/, "Z") };
  }
  function vlessReissue() {
    return db.devices.filter(function (d) { return d.access.indexOf("vless") >= 0; }).map(function (d) {
      var u = find(db.users, d.user);
      return { id: d.id, name: d.name, platform: d.platform, user: d.user, user_name: u ? u.name : "" };
    });
  }
  // Hysteria2: смена портов и сертификат (PUT /api/gw/hy2 → ops.hy2_set)
  function hy2Route(method, path, body) {
    if (method === "POST" && path === "/api/gw/hy2/apply") {          // REV-3373 З-3: копия пары заново — карточка снова «работает»
      if (db.inbounds.hy2.fail && db.inbounds.hy2.fail.why === "cert") db.inbounds.hy2.fail = null;
      bump();
      return { ok: true, kind: "hy2", restarted: true, reissue: [], text: "", warnings: [] };
    }
    if (method === "PUT" && path === "/api/gw/hy2") {
      var hr = db.inbounds.hy2._record || db.inbounds.hy2.record || {};
      if (body.hop !== undefined) { hr.hop = body.hop || null; db.inbounds.hy2.ports = hr.hop ? ["443/udp", hr.hop.replace("-", "–") + "/udp"] : ["443/udp"]; }
      if (body.cert) hr.cert = body.cert;
      bump();
      return { ok: true, kind: "hy2", hop: hr.hop, cert: hr.cert, cert_mode: hr.cert_mode, changed: ["hop"], reissue: [], text: "", warnings: [] };
    }
    throw httpErr(404, "Нет такого пути: " + method + " " + path);
  }
  // VLESS: режим и причина Reality (SPEC-vless-tls-default §3.1) — как gw.ops.vless_view в записи GET gw/inbounds.
  // Внутреннее состояние — rec._vl {reason, want, why, panel, consent, pending, tls_error}; ответы и отказы режима
  // (POST gw/vless/mode, цена сайта) — как ядро: согласие — 409 reality_consent с текстом рисков по сайту, цена —
  // 409 vless_change с устройствами. Тексты — на языке страницы (?lang=en), как у настоящего сервера.
  // начальное состояние по ?vless=…: причина Reality и что выбрано
  function vlInit(rec) {
    var M = { "": ["no_domain"], ru: ["no_domain"], tls: [""], block: ["busy_443", "tls", ML("на 443 в nginx есть чужой сайт", "a foreign site is in nginx on 443")],
      nocert: ["no_cert"], busy: ["busy_443", "tls", ML("сосед goAmnezia слушает 443/tcp", "the goAmnezia neighbour listens on 443/tcp")], panel443: ["busy_443", "tls", "", true],
      nosite: ["no_site"], failed: ["tls_failed"], ready: ["ready"], chosen: ["chosen", "reality"], consent: ["consent_bad", "reality"], legacy: ["legacy", null], pending: ["ready"] };
    var m = M[VLESS_MODE] || M[""];
    rec._vl = { reason: m[0], want: m[1] === undefined ? "tls" : m[1], why: m[2] || "", panel: !!m[3], dom: m[0] === "no_domain" ? "" : VL_DOM };
    if (m[0] === "tls_failed") rec._vl.tls_error = { at: new Date(Date.now() - 2 * 3600e3).toISOString().replace(/\.\d+Z$/, "Z"), why: ML("xray не принял настройки", "xray did not accept the settings") };
    if (m[0] === "chosen") rec._vl.consent = { at: new Date(Date.now() - 5 * 86400e3).toISOString().replace(/\.\d+Z$/, "Z"), via: "panel", who: "admin", risks: "r1a", site: rec.sni, domain: VL_DOM, lang: "ru", ok: true };
    if (m[0] === "consent_bad") rec._vl.consent = { at: null, via: "bot", who: "", risks: "r0a", site: rec.sni, domain: VL_DOM, lang: "ru", ok: false };
    if (VLESS_MODE === "pending") rec._vl.pending = { at: new Date(Date.now() - 600e3).toISOString().replace(/\.\d+Z$/, "Z"), busy: false };
    if (VLESS_MODE === "tls" || VLESS_MODE === "busy" || VLESS_MODE === "panel443" || VLESS_MODE === "block") rec.domain = VL_DOM;
  }
  function vlRisks(rec, site, n) {
    var own = site === "own", same = rec.security !== "tls" && (own ? rec.mask === "own" : rec.sni === site);
    var price = !n ? "" : same ? ML("ссылки, выданные сейчас, не изменятся.", "the links given out now stay the same.")   // устройств нет — пункта цены нет (М3)
      : n ? ML("ссылки, выданные сейчас, перестанут работать — их нужно выдать заново (" + n + vlPl(n) + ").", "the links given out now will stop working — they have to be given out again (" + n + vlPl(n) + ").") : "";
    var body = own ? ML("С доменом мы рекомендуем TLS со своим сертификатом. Reality со своим сайтом (" + VL_DOM + ") — самый осторожный вариант Reality: адрес и сертификат совпадают, чужой сайт не нужен. Что меняется:\n- узел показывает сайт и сертификат своего домена, но сам VLESS уходит с 443 на другой порт (8443) — нестандартный порт рядом с сайтом заметнее, чем один 443;\n- домен по-прежнему виден в открытых журналах сертификатов, как и при TLS",
      "With a domain we recommend TLS with your own certificate. Reality with your own site (" + VL_DOM + ") is the most careful kind of Reality: the address and the certificate match, no one else’s site is needed. What changes:\n- the server shows the site and certificate of your domain, but VLESS itself moves off 443 to another port (8443) — an unusual port next to a site stands out more than a single 443;\n- the domain is still visible in the public certificate logs, as with TLS")
      : ML("С доменом мы рекомендуем TLS со своим сертификатом: узел предъявляет свой настоящий сертификат, показывает свой сайт и не зависит от чужих сайтов. Reality выдаёт узел за чужой сайт (" + site + "). Что это значит:\n- адрес вашего сервера не принадлежит этому сайту — такое несовпадение можно заметить со стороны; если его станут использовать для блокировок, соединения начнут замедлять или обрывать;\n- если сайт перестанет отвечать с узла по TLS 1.3 или сменит настройки, VLESS перестанет подключаться, пока вы не смените сайт (ссылки — выдать заново)",
        "With a domain we recommend TLS with your own certificate: the server presents its real certificate, shows its own site and does not depend on other people’s sites. Reality makes the server pose as someone else’s site (" + site + "). This means:\n- your server’s address does not belong to that site — the mismatch can be noticed from outside; if it is ever used for blocking, connections will be slowed down or dropped;\n- if the site stops answering the server over TLS 1.3 or changes its settings, VLESS stops connecting until you pick another site (links have to be given out again)");
    var tail = own ? ML("Выбирайте его, если TLS со своим сертификатом у ваших пользователей не проходит, а Reality проходит.", "Choose it if TLS with your own certificate does not get through for your users and Reality does.")
      : ML("Иногда Reality проходит там, где свой домен не пускают (например, мобильные «белые списки»). Если выбираете его по этой причине — выбирайте осознанно.", "Sometimes Reality gets through where your own domain is not let in (for example, mobile “allow lists”). If that is why you choose it, choose it knowingly.");
    return { ver: "r1" + (own ? "b" : "a"), variant: own ? "b" : "a", site: site, need_pick: !site, same: same,
      title: own ? ML("Reality со своим сайтом вместо сертификата", "Reality with your own site instead of the certificate") : ML("Reality вместо своего сертификата", "Reality instead of your own certificate"),
      text: body + (price ? ";\n- " + price : ".") + "\n\n" + tail, phrase: ML("Понимаю риски, выбрать Reality", "I understand the risks, choose Reality") };
  }
  // поля vless_view поверх записи (inboundRows): причина, «исправить», рекомендация с ценой, согласие, переход
  function vlView(rec, n) {
    var v = rec._vl || {};
    delete rec._vl;
    var tls = rec.security === "tls", reason = tls ? "" : (v.reason || "");
    var fx = VL_FIXES[v.panel ? "panel_443" : reason];
    var site = rec.mask === "own" ? "own" : (rec.sni || null);
    // как gw.ops.vless_view: domain — только у TLS (у Reality ""), домен узла — в node_domain
    return { domain: tls ? String(rec.domain || v.dom || "") : "", node_domain: v.dom || "", want: v.want, legacy: v.want === null, reason: reason, forced: VL_FORCED.indexOf(reason) >= 0 && v.want !== "reality",
      reason_text: v.why || "", fix: fx ? { do: fx, text: VL_FIX_T[fx] } : null, consent: v.consent || null, pending: v.pending || null,
      recommend: ["ready", "legacy", "consent_bad"].indexOf(reason) >= 0 ? { mode: "tls", devices: n, reissue: vlessReissue(), text: n ? ML("TLS уже можно — нужна перевыдача ключей (" + n + vlPl(n) + "): gw vless mode tls.", "TLS is possible now — keys have to be given out again (" + n + vlPl(n) + "): gw vless mode tls.") : ML("TLS уже можно: gw vless mode tls.", "TLS is possible now: gw vless mode tls.") } : null,
      reality_risks: v.dom ? vlRisks(rec, site, n) : null, tls_error: v.tls_error || undefined };
  }
  function vlNames(rows) { var nm = rows.map(function (x) { return x.user_name ? x.name + " (" + x.user_name + ")" : x.name; }); return nm.length > 3 ? nm.slice(0, 3).join(", ") + ML(" и ещё " + (nm.length - 3), " and " + (nm.length - 3) + " more") : nm.join(", "); }
  // переход на TLS (vless_to_tls): цена — отказом, затем TLS на 443, сайт включён, согласие и tls_error сняты
  function vlToTls(rec, yes) {
    var v = rec._vl, rows = vlessReissue();
    if (rec.security === "tls") return { ok: true, changed: false, security: "tls", old: "tls", port: 443, reissue: [], text: "", reason: "" };
    if (["no_domain", "no_cert", "busy_443"].indexOf(v.reason) >= 0) throw httpErr(409, ML("TLS сейчас нельзя: ", "TLS is not possible now: ") + (VL_FIX_T[VL_FIXES[v.panel ? "panel_443" : v.reason]] || v.reason), 3, { need: "tls_impossible", reason: v.reason });
    if (rows.length && !yes) {
      // сайт выключен — включится вместе с переходом: цена говорит это и несёт site_on false (REV-3378-fix С1)
      var son = !!(db.site && db.site.domain && db.site.mode === "site");
      var pt = (son ? "" : ML("Сайт включится. ", "The site will go on. ")) + ML("VLESS перейдёт на TLS со своим доменом " + VL_DOM + ": ссылки VLESS, выданные раньше, перестанут работать — выдайте их заново (" + rows.length + vlPl(rows.length) + "): " + vlNames(rows) + ".",
        "VLESS switches to TLS with its own domain " + VL_DOM + ": VLESS links given out earlier stop working — give them out again (" + rows.length + vlPl(rows.length) + "): " + vlNames(rows) + ".");
      throw httpErr(409, pt, 3, { need: "vless_change", kind: "vless", text: pt, devices: rows, to: { security: "tls" }, site_on: son });
    }
    rec.security = "tls"; rec.port = 443; rec.domain = VL_DOM;
    v.reason = ""; v.want = "tls"; v.consent = null; v.tls_error = null; v.pending = null;
    if (db.inbounds.vless.installed) db.inbounds.vless.ports = ["443/tcp"];
    if (db.site && db.site.domain) db.site.mode = "site";
    bump();
    return { ok: true, changed: true, security: "tls", old: "reality", port: 443, reissue: rows, reason: "",
      text: rows.length ? ML("VLESS перешёл на TLS: выдайте ссылки заново: ", "VLESS switched to TLS: give the links out again: ") + vlNames(rows) + "." : "" };
  }
  // цена VLESS у действий сайта (site.ops.vless_price, §3.6/§3.7): выключение сайта при TLS — Reality под сохранённой
  // маской (её нет — need_pick: сайт называет панель, vless_site); включение при Reality с want tls — TLS
  function vlSiteGate(kind, body) {
    var ib = db.inbounds.vless, rec = ib && ib._record, rr = vlessReissue();
    if (!ib || !ib.installed || !rec || !rec._vl) return;
    var tls = rec.security === "tls";
    if (kind === "off" ? !tls : (tls || rec._vl.want === "reality")) return;
    if (!rr.length || (body.yes === true && (kind === "on" || body.vless_site || rec.mask !== "own"))) return;
    var kept = kind === "off" && (rec.mask === "list" || rec.mask === "custom") ? rec.sni : null, site = body.vless_site || kept;
    var text = kind === "off" ? ML("Сайт на домене выключится, VLESS перейдёт на запасной Reality под " + (site || "сайт, который вы выберете") + ". Ссылки VLESS — выдать заново (" + rr.length + vlPl(rr.length) + "): " + vlNames(rr) + ".",
      "The site on the domain turns off, VLESS moves to Reality (backup) as " + (site || "the site you choose") + ". VLESS links — give out again (" + rr.length + vlPl(rr.length) + "): " + vlNames(rr) + ".")
      : ML("VLESS перейдёт на TLS со своим сертификатом — выдать заново (" + rr.length + vlPl(rr.length) + "): " + vlNames(rr) + ".", "VLESS switches to TLS with its own certificate — give out again (" + rr.length + vlPl(rr.length) + "): " + vlNames(rr) + ".");
    throw httpErr(409, text, 3, { need: "vless_change", kind: "site", text: text, site: kind === "off" ? site : null, need_pick: kind === "off" && !site, devices: rr,
      to: kind === "off" ? { security: "reality", port: 443, sni: site } : { security: "tls", port: 443 } });
  }
  function vlSiteAfter(kind, body) {
    var ib = db.inbounds.vless, rec = ib && ib._record;
    if (!ib || !ib.installed || !rec || !rec._vl) return;
    if (kind === "off" && rec.security === "tls") {
      var site = body.vless_site || rec.sni;
      rec.security = "reality"; rec.sni = site; rec.dest = site + ":443"; rec.port = 443; delete rec.domain; rec._vl.reason = "no_site";
      ib.ports = ["443/tcp"];
    } else if (kind === "on" && rec.security !== "tls" && rec._vl.want !== "reality" && rec._vl.dom) {
      rec.security = "tls"; rec.port = 443; rec.domain = VL_DOM; rec._vl.reason = ""; rec._vl.want = "tls"; rec._vl.tls_error = null;
      ib.ports = ["443/tcp"];
    }
  }
  // T-3378 Н-2: GET gw/vless/ask — что спросит действие, ответом 200 (как gw.ops.vless_ask): согласие или цена — need с
  // данными отказа (те же, что у 409 действия), иначе need null; ничего не меняет
  function vlAskDoc(query) {
    var ib = db.inbounds.vless, rec = ib && ib._record, v = rec && rec._vl, rr = vlessReissue();
    var q = { "do": query ? String(query.get("do") || "") : "", site: query ? String(query.get("site") || "") : "" };
    if (["tls", "reality", "site_on", "site_off", "clear"].indexOf(q.do) < 0) throw httpErr(400, "do — tls, reality, site_on, site_off или clear", 2);
    var none = { ok: true, need: null };
    if (!ib || !ib.installed || !rec || !v) return none;
    try {
      if (q.do === "tls") { if (rec.security !== "tls" && ["no_domain", "no_cert", "busy_443"].indexOf(v.reason) < 0) vlToTls(rec, false); }
      else if (q.do === "reality") {
        if (!v.dom) return none;
        var site = q.site && q.site !== "auto" ? q.site : null, rd = vlRisks(rec, site, rr.length);
        return { ok: true, need: "reality_consent", kind: "vless", block: rd.text, title: rd.title, risks: rd.ver, site: site, phrase: rd.phrase,
          message: site ? ML("Нужно согласие с рисками Reality.", "Consent to the risks of Reality is needed.") : ML("Сначала подберите сайт — его имя войдёт в текст рисков.", "Pick the site first — its name goes into the text of the risks.") };
      } else if (db.site && db.site.domain) vlSiteGate(q.do === "site_on" ? "on" : "off", q.site ? { vless_site: q.site } : {});
    } catch (e) {
      var d = e && e.data;
      if (d && (d.need === "reality_consent" || d.need === "vless_change")) return Object.assign({ ok: true, message: e.message }, d);
      if (!(e && e.http)) throw e;
    }
    return none;
  }
  function vlessRoute(method, path, body) {
    var r = db.inbounds.vless, rec = r._record;
    if (!r.installed && path !== "/api/gw/vless/masks" && path !== "/api/gw/vless/masks/check") throw httpErr(400, "Протокол VLESS не установлен: kaskadctl gw inbound install vless (меню 17 → «Протоколы»).", 1);
    var list = VLESS_MASKS_BY[rec.mask_list] || VLESS_MASKS_BY.int;
    var masks = function (check) {
      return { ok: true, current: rec.sni, checked: check, list: rec.mask_list, country: clone(rec.country),
        masks: list.map(function (x) { return { sni: x[0], title: x[1], mask: "list", current: x[0] === rec.sni, check: check ? vlessCheck(x[0]) : null }; }) };
    };
    // выбор режима человеком (SPEC-vless-tls-default §3.5): TLS — vless_to_tls (цена отказом); Reality при домене — сначала
    // 409 reality_consent с текстом рисков по сайту, с согласием — цена (vless_change) и переход; без домена — без согласия
    if (method === "POST" && path === "/api/gw/vless/mode") {
      var want = body.mode, v = rec._vl, rr = vlessReissue();
      if (want !== "tls" && want !== "reality") throw httpErr(400, ML("Режим «" + want + "» — нужен tls или reality.", "Mode “" + want + "” — tls or reality."), 1);
      if (want === "tls") return vlToTls(rec, body.yes === true);
      var site = String(body.site || "");
      if (!v.dom) {                                     // без домена — согласие не нужно, выбор запоминается
        if (site && site !== "auto" && site !== rec.sni) { rec.sni = site; rec.dest = site + ":443"; rec.mask = list.some(function (x) { return x[0] === site; }) ? "list" : "custom"; }
        v.want = "reality_nodomain"; bump();
        return { ok: true, changed: false, security: "reality", port: rec.port, sni: rec.sni, want: v.want, reissue: [], text: "" };
      }
      if (!site || site === "auto") { var rp = vlRisks(rec, null, rr.length); throw httpErr(409, ML("Сначала подберите сайт — его имя войдёт в текст рисков.", "Pick the site first — its name goes into the text of the risks."), 3, { need: "reality_consent", kind: "vless", block: rp.text, title: rp.title, risks: rp.ver, site: null, phrase: rp.phrase }); }
      var rd = vlRisks(rec, site, rr.length);
      if (body.accept_risks !== true || body.risks !== rd.ver) throw httpErr(409, ML("Нужно согласие с рисками Reality.", "Consent to the risks of Reality is needed."), 3, { need: "reality_consent", kind: "vless", block: rd.text, title: rd.title, risks: rd.ver, site: site, phrase: rd.phrase });
      var nsni = site === "own" ? VL_DOM : site, nport = rec.security === "tls" || site === "own" ? 8443 : rec.port;
      var changed = rec.security === "tls" || nsni !== rec.sni || nport !== rec.port;
      if (changed && rr.length && body.yes !== true) {
        var pr = ML("Ссылки VLESS изменятся (Reality под " + nsni + ", порт " + nport + "): выдать заново — " + vlNames(rr) + ".", "VLESS links change (Reality as " + nsni + ", port " + nport + "): give out again — " + vlNames(rr) + ".");
        throw httpErr(409, pr, 3, { need: "vless_change", kind: "vless", text: pr, devices: rr, to: { security: "reality", port: nport, sni: nsni } });
      }
      rec.security = "reality"; rec.sni = nsni; rec.dest = nsni + ":443"; rec.port = nport; rec.mask = site === "own" ? "own" : list.some(function (x) { return x[0] === site; }) ? "list" : "custom";
      delete rec.domain;
      v.want = "reality"; v.reason = "chosen"; v.consent = { at: new Date().toISOString().replace(/\.\d+Z$/, "Z"), via: "panel", who: "admin", risks: rd.ver, site: site, domain: VL_DOM, lang: VL_EN ? "en" : "ru", ok: true };
      if (db.inbounds.vless.installed) db.inbounds.vless.ports = [nport + "/tcp"];
      bump();
      return { ok: true, kind: "vless", changed: changed, security: "reality", old: "tls", port: nport, sni: nsni, want: "reality", reissue: changed ? rr : [],
        text: changed ? "" : ML("ссылки, выданные сейчас, не изменятся.", "the links given out now stay the same."), error: null };
    }
    // режим TLS: сайта маскировки и ключей Reality нет — менять нечего (панель эти действия и не показывает)
    if (rec.security === "tls" && method !== "GET" && path !== "/api/gw/vless/masks/check") throw httpErr(409, "VLESS работает в режиме TLS со своим доменом " + rec.domain + ": сайт маскировки и ключи Reality не используются.", 1);
    if (method === "GET" && path === "/api/gw/vless/masks") return masks(false);
    if (method === "POST" && path === "/api/gw/vless/masks/check") return masks(true);
    if (method === "PUT" && path === "/api/gw/vless/mask") {
      var v = String(body.site || "").trim().toLowerCase(), check = body.check !== false;
      if (!v) throw httpErr(400, "Поле «site» обязательно.", null);
      if (v === "own") throw httpErr(400, "Своего сайта на узле нет: «свой сайт» работает в режиме «сайт» (меню «Домен и сайт»).", 2);
      if (v === "auto") v = list[0][0];
      var c = check ? vlessCheck(v) : { ok: null, tls: "", alpn: "", h2: false, why: "не проверялся", at: new Date().toISOString().replace(/\.\d+Z$/, "Z") };
      if (check && !c.ok) throw httpErr(400, v + " не подходит для маскировки: " + c.why + ".", 2);
      var changed = v !== rec.sni;
      rec.sni = v; rec.dest = v + ":443"; rec.mask = list.some(function (x) { return x[0] === v; }) ? "list" : "custom"; rec.mask_check = c;
      var rows = changed ? vlessReissue() : [];
      bump();
      return { ok: true, sni: v, mask: rec.mask, mask_check: c, changed: changed, reissue: rows,
        text: rows.length ? "Сайт маскировки VLESS сменился: выданные раньше ссылки не подключатся. Выдайте их заново: " + rows.map(function (x) { return x.name; }).join(", ") + "." : "", warnings: [] };
    }
    if (method === "POST" && path === "/api/gw/vless/rekey") {
      rec.pub = fakeB64(43).split("").reverse().join(""); rec.short_id = rid("", 16);
      var rk = vlessReissue();
      bump();
      return { ok: true, pub: rec.pub, short_id: rec.short_id, reissue: rk,
        text: rk.length ? "Ключи узла VLESS сменились: выданные раньше ссылки не подключатся. Выдайте их заново: " + rk.map(function (x) { return x.name; }).join(", ") + "." : "", warnings: [] };
    }
    if (method === "POST" && path === "/api/gw/vless/sync") {      // режим по сайту узла: в макете — без смены
      return { ok: true, changed: false, security: rec.security === "tls" ? "tls" : "reality", port: rec.port,
        why: rec.security === "tls" ? "" : (rec.tls_block || "сайт на домене не включён (режим «сайт»)"), error: "", reissue: [], text: "", warnings: [] };
    }
    throw httpErr(404, "Нет такого пути: " + method + " " + path, null);
  }
  // ── Группы выходов и политика (этап 1, T8 на моке; контракт SE §7.9 — до готовности T7) ─────────────────────────
  /* Параметры адреса (к mock=1):
       policy=standard  узел уже на «Стандарте» (плашки «Появились группы выходов» нет); без параметра — обновлённый узел:
                        «Основная» — «Как раньше (без условий)», плашка вверху вкладки «Группы»
       busy=1           первая запись политики отвечает 409 «занято» (код 6) — «Повторить» у формы и подтверждения
       dry=slow|fail    сухой прогон отвечает через 25 с (дольше срока панели 20 с) | ошибкой 500 — «не удалось посчитать»
       charts=0         истории нет (sqlite3 нет): «графики недоступны»
     Версия политики rev — db.policyRev; сверка у записей списка §7.9: нет — 400 rev_required, не та — 409 policy_changed
     (data.rev); сухой прогон отдаёт rev, на которой посчитан. ver маршрута узла, теста скорости, уведомлений — 12 hex от
     JSON объекта. Повтор DELETE и запросы к удалённому объекту — 404 gone (data.gone). */
  var POLICY_STD = Q.get("policy") === "standard";
  var DRY_MODE = Q.get("dry") || "";
  var BUSY_ONCE = Q.get("busy") === "1";
  var NO_CHARTS = Q.get("charts") === "0";
  var MANY_TRANS = Q.get("trans") === "many";          // ?trans=many — 45 переходов: «Показать ещё» листает (k3_writes_check)
  var PRESET_TEXT = { economy: "Экономно", standard: "Стандарт", strict: "Строго", compat: "Как раньше (без условий)", custom: "Свой" };
  function mockPreset(preset) {
    var freq = preset === "economy" ? "normal" : "often";                 // как policy.preset_fields: «Дом» — тоже «Часто»
    var rules = [];
    if (preset === "standard") rules = [mockRule("r-rtt", "rtt", ">", 2, "x_norm", "degraded", 5)];
    if (preset === "strict") rules = [mockRule("r-rtt", "rtt", ">", 1.5, "x_norm", "degraded", 3), mockRule("r-loss", "loss", ">", 10, "pct", "degraded", 2)];
    return { flap_standby: preset !== "compat", evict_degraded: true, return_to_primary: { enabled: true, hold_min: 10 },
      night_rebalance: { enabled: false, at: "04:00" }, drop_on_degrade: false, rules: rules,
      freq: { mode: freq, every_s: null }, services: preset === "strict" ? [{ id: "yt", name: "YouTube", url: "https://www.youtube.com/generate_204", expect: [204], max_ms: 3000, every_min: 30 }] : [],
      geo: { enabled: true, every_h: preset === "strict" ? 6 : 24, confirm: 2, action: "push", expect: preset === "ru_home" ? "RU" : null } };
  }
  function mockRule(id, metric, op, value, unit, then, min) {
    return { id: id, name: "", enabled: true, exits: "all", when: { match: "all", conds: [{ metric: metric, op: op, value: value, unit: unit }], hold: { minutes: min } },
      then: then, back: { match: "all", conds: [{ metric: metric, op: "<", value: unit === "x_norm" ? 1.5 : 5, unit: unit }], hold: { minutes: 10 }, manual: false }, push: true };
  }
  function mockGroup(id, name, preset, tiers) {
    var p = mockPreset(preset);
    return { id: id, name: name, role: "abroad", preset: preset, tiers: tiers, new_exits_tier: "last", per_connection: false, fail: "block",
      flap_standby: p.flap_standby, evict_degraded: p.evict_degraded, return_to_primary: p.return_to_primary, night_rebalance: p.night_rebalance,
      drop_on_degrade: p.drop_on_degrade, rules: p.rules,
      checks: { targets: "abroad", freq: p.freq, speed: { enabled: false, every_h: 6, url: "default" }, services: p.services, geo: p.geo } };
  }
  // российская группа (policy.default_group(gid, "ru", "ru_home")): каждый ключ — свой шаг, проверки «Часто», страна —
  // RU раз в сутки, в конце — с сервера напрямую
  function ruGroup(gid, name, exits) {
    var g = mockGroup(gid, name, "ru_home", exits.length ? exits.map(function (id, i) { return mockTier("t" + (i + 1), "", [id]); }) : [mockTier("t1", "", [])]);
    g.role = "ru"; g.fail = "direct"; g.checks.targets = "ru"; g.checks.geo.expect = "RU";
    return g;
  }
  function mockTier(id, name, exits) { return { id: id, name: name || "", exits: exits, method: "even", weights: {}, tolerance_ms: 30, tolerance_pct: 20 }; }
  // политика по умолчанию: «Основная» (Вена, Хельсинки — уровень 1; Токио и пятый — уровень 2) и «Стриминг» («Новый ключ»)
  function pol() {
    if (db.policy) return db.policy;
    var ids = db.exits.filter(function (e) { return e.kind !== "bad" && !/-\d+$/.test(e.id) && e.role !== "ru"; }).map(function (e) { return e.id; });
    var has = function (id) { return ids.indexOf(id) >= 0; };
    var t1 = ["e-41a19b9cbe", "e-7c2d8e0f13"].filter(has), t2 = ["e-9aa1b2c3d4", "e-46a226b167"].filter(has), st = ["e-0f0f0f0f0f"].filter(has);
    var rest = ids.filter(function (id) { return t1.concat(t2, st).indexOf(id) < 0; });
    var main = mockGroup("main", "", POLICY_STD ? "standard" : "compat", [mockTier("t1", "", t1.concat(rest))].concat(t2.length ? [mockTier("t2", "Запасной", t2)] : []));
    if (ORD) {                                // PLAN-order Ш5: «Ключ 1…4» — первым уровнем «Лучший отклик», прежние — «Запасной»
      var kx = ORD_X.filter(has), old = t1.concat(rest, t2).filter(function (id) { return kx.indexOf(id) < 0; });
      main.tiers = [Object.assign(mockTier("t1", "", kx), { method: "best_rtt" }, ORD === "manual" ? { order: "manual" } : {}), mockTier("t2", "Запасной", old)];
      if (ORD === "conn") main.per_connection = true;
    }
    var groups = [main];
    if (st.length || ids.length) groups.push(mockGroup("g-5f1a2b", "Стриминг", "standard", [mockTier("t1", "", st)]));
    if (RUG) {
      var ru = db.exits.filter(function (e) { return e.role === "ru"; }).map(function (e) { return e.id; });
      groups.push(ruGroup(RU_HOME, "Дом", ru.filter(function (id) { return id !== RU_X[2]; })));
      if (RUG === "tier") { var hm = groups[groups.length - 1]; hm.tiers = [mockTier("t1", "", hm.tiers.reduce(function (a, t) { return a.concat(t.exits); }, []))]; hm.tiers[0].method = "best_rtt"; }
      if (ru.indexOf(RU_X[2]) >= 0) groups.push(ruGroup(RU_OFFICE, "Офис", [RU_X[2]]));
    }
    db.policy = { version: 1, hint_seen: POLICY_STD, probes: { off_backoff: { base_s: 30, max_s: 600, reset_after_s: 3600 }, window: 10,
      speed: { max_mb: 10, max_s: 8, only_quiet: true, budget_day_mb: 200, budget_month_gb: 3 } }, new_exits_group: "main", tg_proxy_group: "main", groups: groups };
    db.policyRev = db.policyRev || 14;
    // виды — как model.NOTIFY_PUSH сервера (SE §8.1): других ключей сервер не знает и отвечает 400
    db.notify = db.notify || { push: { exit_dead: true, exit_alive: true, exit_degraded: true, exit_recovered: true, exit_standby: true, exit_removed: true,
      exit_rule_push: true, exit_failover: false, exit_quota: true, exit_country_changed: true, node_offline: true, group_last_step: true,
      group_restored: true, speedtest_budget: false, node_ru_offline: true,
      // здоровье узла (SPEC-node-score §3.5, A6): виды hostmon.HOST_KINDS — в NOTIFY_PUSH сервера, по умолчанию включены
      host_cpu: true, host_steal: true, host_io: true, host_mem: true, host_disk: true, host_net: true, host_conntrack: true,
      host_clock: true, host_cert: true, host_loss: true, bench_done: true, bench_skipped_day: true, coalesce: true }, flap_summary: true };
    return db.policy;
  }
  function polGroup(gid) { return pol().groups.filter(function (g) { return g.id === gid; })[0] || null; }
  function placeOf(id) {
    var out = null;
    pol().groups.forEach(function (g) { g.tiers.forEach(function (t, i) { if (t.exits.indexOf(id) >= 0) out = { group: g.id, tier: t.id, n: i }; }); });
    return out;
  }
  function unplace(id) { pol().groups.forEach(function (g) { g.tiers.forEach(function (t) { t.exits = t.exits.filter(function (x) { return x !== id; }); delete t.weights[id]; }); }); }
  function placeNew(id, gid) {
    var g = polGroup(gid || pol().new_exits_group) || polGroup("main");
    unplace(id); newTierOf(g).exits.push(id);
  }
  // уровень нового и перенесённого выхода — как policy.new_tier (REV-plan П-6): «first», id уровня, «last» (неизвестный
  // id — последний); у российской группы «last» — свой уровень в конце, пока в последнем есть ключи и уровней < 10
  function newTierOf(g) {
    if (!g.tiers.length) g.tiers.push(mockTier("t1", "", []));
    var w = g.new_exits_tier || "last";
    if (w === "first") return g.tiers[0];
    var t = g.tiers.filter(function (x) { return x.id === w; })[0];
    if (t && w !== "last") return t;
    var last = g.tiers[g.tiers.length - 1];
    if (g.role === "ru" && last.exits.length && g.tiers.length < 10) {
      var n = g.tiers.length + 1;
      while (g.tiers.some(function (x) { return x.id === "t" + n; })) n++;
      last = mockTier("t" + n, "", []); g.tiers.push(last);
    }
    return last;
  }
  function grpLabel(g) { return g.name || (g.id === "main" ? "Основная" : g.id); }
  function tierLabel(t, i) { return t.name || "Уровень " + (i + 1); }
  // ── «Российские сайты» (T15 Q6): как model.ru_via_of, api._ru_nets и rules.ops._ru_groups ─────────────────────
  function ruOwn(o) { return o && typeof o.ru_via === "string" && o.ru_via !== "inherit" ? o.ru_via : null; }
  function ruViaOf(o, isUser) {
    if (ruOwn(o)) return { via: ruOwn(o), from: isUser ? "user" : "device" };
    var u = isUser ? null : find(db.users, o.user);
    if (u && ruOwn(u)) return { via: ruOwn(u), from: "user" };
    return { via: "direct", from: "default" };
  }
  // «Россия напрямую» действует для устройства (rules.apply.device_rd): профиль и списки, устройство и человек включены
  function ruApplies(d) {
    var r = db.rules, u = find(db.users, d.user) || {}, prof = d.rules || u.rules || r["default"];
    return prof === "ru_direct" && !!r.lists && rfAllowed() && d.enabled !== false && u.enabled !== false;
  }
  // шаг группы: первый годный выход по порядку шагов; нет — последний шаг (с какого времени — смена состояния выходов)
  function ruStep(g) {
    var ex = null, since = 0;
    g.tiers.some(function (t) { return t.exits.some(function (id) { var e = find(db.exits, id); if (e && e.enabled !== false && e.state === "alive" && !e._manual) { ex = id; return true; } return false; }); });
    if (ex) {
      var tt = g.tiers.filter(function (t) { return t.exits.indexOf(ex) >= 0; })[0];
      var alive = tt.exits.filter(function (id) { var e = find(db.exits, id); return e && e.enabled !== false && e.state === "alive" && !e._manual; });
      return { exit: ex, tier: tt.id, alive: alive };
    }
    g.tiers.forEach(function (t) { t.exits.forEach(function (id) { var e = find(db.exits, id); if (e && e.since > since) since = e.since; }); });
    return { exit: null, tier: null, last: g.fail === "block" ? "block" : "direct", since: since || null };
  }
  function ruNet(d) {
    var v = ruViaOf(d).via, g = v === "direct" ? null : polGroup(v);
    if (v === "direct") return { step: "direct", group: null, since: null, exit: null };
    if (!g || !ruApplies(d) || !db.gw.enabled || db.stale) return { step: "none", group: v, since: null, exit: null };
    var st = ruStep(g);
    // два годных ключа в уровне (rug=tier) — устройства разложены между ними (у ядра — способ уровня, assign._choose)
    if (st.exit && st.alive.length > 1) { var h = 0; String(d.id).split("").forEach(function (c) { h = (h * 31 + c.charCodeAt(0)) % 9973; }); return { step: "exit", group: v, since: null, exit: st.alive[h % st.alive.length] }; }
    if (st.exit) return { step: "exit", group: v, since: null, exit: st.exit };
    return { step: "last_" + st.last, group: v, since: st.since, exit: null };
  }
  var RU_WORST = ["last_block", "last_direct", "none", "exit", "direct"];
  function ruNetUser(u) {
    var nets = db.devices.filter(function (d) { return d.user === u.id; }).map(ruNet);
    if (!nets.length) { var v = ruViaOf(u, true).via; return { step: v === "direct" ? "direct" : "none", group: v === "direct" ? null : v, since: null, exit: null }; }
    return nets.sort(function (a, b) { return RU_WORST.indexOf(a.step) - RU_WORST.indexOf(b.step); })[0];
  }
  function ruFields(o, isUser) {
    if (!RUG && !ruOwn(o)) return { ru_via: "inherit", ru: { via: "direct", from: "default" }, ru_net: { step: "direct", group: null, since: null, exit: null } };
    return { ru_via: ruOwn(o) || "inherit", ru: ruViaOf(o, isUser), ru_net: isUser ? ruNetUser(o) : ruNet(o) };
  }
  function ruGroupsDoc() {
    return pol().groups.filter(function (g) { return g.role === "ru"; }).map(function (g) {
      var devs = db.devices.filter(function (d) { return ruViaOf(d).via === g.id; });
      return { id: g.id, name: g.name, label: grpLabel(g), users: db.users.filter(function (u) { return ruOwn(u) === g.id; }).map(function (u) { return u.id; }),
        devices: devs.map(function (d) { return d.id; }), applies: devs.filter(ruApplies).map(function (d) { return d.id; }) };
    });
  }
  // live.groups (§8.9): шаг каждой группы — первый уровень с годным выходом или последний шаг
  function liveGroups() {
    var out = {};
    if (!db.policy && !RUG) return out;
    pol().groups.forEach(function (g) {
      var st = ruStep(g), any = g.tiers.some(function (t) { return t.exits.length; });
      out[g.id] = { active_tier: st.tier, last_step: st.exit ? null : (any || g.role === "ru" ? st.last : null), since: st.exit ? null : st.since,
        ranks: mockRanks(g) };
    });
    return out;
  }
  // ряды «силы» уровней (PLAN-order Ш2, как assign.rows сервера, упрощённо: отклик — rtt мока, без «× нормы»)
  function mockHealth(id) {
    var e = find(db.exits, id), x = EXIT_MOCK[id] || {};
    if (!e) return "off";
    var ps = e._manual ? (e._manual === "maintenance" ? "off" : "standby") : !e.enabled ? "off" : e.state === "dead" ? "off" : x.pstate || (e.state === "alive" ? "ok" : "unknown");
    return e._quotaHit ? "off" : ps;
  }
  function mockRanks(g) {
    var out = {};
    g.tiers.forEach(function (t) {
      var ex = t.exits.slice(), cls = ["ok", "degraded", "standby"].filter(function (k) { return ex.some(function (id) { return mockHealth(id) === k; }); })[0] || null;
      var cands = cls ? ex.filter(function (id) { return mockHealth(id) === cls; }) : [];
      var r = { by: "even", queue: false, order: t.order === "manual" ? "manual" : "auto", klass: cls, nodata: false, rows: [],
        out: ex.filter(function (id) { return cands.indexOf(id) < 0; }).map(function (id) { return { id: id, why: mockHealth(id) }; }) };
      var even = function (id) { return { id: id, v: null, share: Math.round(1000 / cands.length) / 1000 }; };
      out[t.id] = r;
      if (!cands.length) return;
      if (g.id === "main" && g.per_connection) { r.by = "per_connection"; r.order = "auto"; r.rows = [cands.map(even)]; return; }
      if (t.order === "manual") { r.by = "manual"; r.queue = true; r.rows = cands.map(function (id, i) { return [{ id: id, v: i + 1, share: i ? 0 : 1 }]; }); return; }
      if (t.method === "best_rtt") {
        var v = {}; cands.forEach(function (id) { var x = EXIT_MOCK[id] || {}; if (typeof x.rtt === "number") v[id] = x.rtt; });
        var rest = cands.filter(function (id) { return v[id] !== undefined; }).sort(function (a, b) { return v[a] - v[b] || cands.indexOf(a) - cands.indexOf(b); });
        r.by = "rtt"; r.queue = true;
        if (!rest.length) { r.nodata = true; r.rows = [cands.map(even)]; return; }
        r.out = r.out.concat(cands.filter(function (id) { return v[id] === undefined; }).map(function (id) { return { id: id, why: "nometric" }; }));
        while (rest.length) {
          var best = v[rest[0]], tol = Math.max(t.tolerance_ms === undefined ? 30 : t.tolerance_ms, best * (t.tolerance_pct === undefined ? 20 : t.tolerance_pct) / 100);
          var row = rest.filter(function (id, i) { return !i || v[id] <= best + tol; });
          rest = rest.filter(function (id) { return row.indexOf(id) < 0; });
          row.sort(function (a, b) { return cands.indexOf(a) - cands.indexOf(b); });
          var first = !r.rows.length;
          r.rows.push(row.map(function (id) { return { id: id, v: v[id], share: first ? Math.round(1000 / row.length) / 1000 : 0 }; }));
        }
        return;
      }
      if (t.method === "weights") {
        var w = {}, sum = 0; cands.forEach(function (id) { w[id] = (t.weights || {})[id] > 0 ? t.weights[id] : 1; sum += w[id]; });
        r.by = "weights";
        Object.keys(w).map(function (id) { return w[id]; }).filter(function (x, i, a) { return a.indexOf(x) === i; }).sort(function (a, b) { return b - a; }).forEach(function (wv) {
          r.rows.push(cands.filter(function (id) { return w[id] === wv; }).map(function (id) { return { id: id, v: wv, share: Math.round(1000 * wv / sum) / 1000 }; }));
        });
        return;
      }
      r.rows = [cands.map(even)];
    });
    return out;
  }
  function noExitRu() {
    var out = {};
    if (!RUG) return out;
    db.devices.forEach(function (d) { var n = ruNet(d); if (n.step === "last_block" || n.step === "last_direct") out[d.id] = { step: n.step, group: n.group, since: n.since }; });
    return out;
  }
  // ver своего выбора строки (rules.ops.ru_ver — 12 hex от sha1; в макете — читаемая метка, сверяется так же)
  function ruVer(o) { return "mockver-" + (ruOwn(o) || "inherit"); }
  // PUT /api/rules/ru-via (rules.panel.put_ru_via): одна запись, всё или ничего; id — только u-… / d-…; у каждой строки —
  // ver, что страница прочла: без него — 400 ver_required (вкладка до обновления), чужая правка — 409 object_changed
  // (REV-3310 П1). Проверить в макете (rug=ok): «Выбрать пользователей», отметить «Гости», в консоли
  // KASKAD_MOCK.db().users[2].ru_via = "direct", «Сохранить» — 409 и окно заново с текущим выбором.
  function putRuVia(b) {
    var items = Array.isArray(b.items) ? b.items : null;
    if (!items || !items.length || items.length > 2000) throw httpErr(400, "Нужен список items: от 1 до 2000 строк.", 2, { field: "items" });
    var plan = items.map(function (it) {
      var isU = it && typeof it.user === "string", id = isU ? it.user : it && it.device;
      if (!/^[ud]-[0-9a-f]{8}$/.test(String(id || "")) || (isU ? id[0] !== "u" : id[0] !== "d")) throw httpErr(400, "Пользователь или устройство — по id (u-… / d-…).", 2, { field: "items" });
      var obj = isU ? find(db.users, id) : find(db.devices, id);
      if (!obj) throw httpErr(404, "Такого пользователя или устройства уже нет — обновите страницу.", "gone", { gone: true, id: id });
      var via = String(it.via || ""), g = /^g-[0-9a-f]{6}$/.test(via) ? polGroup(via) : null;
      if (!(via === "inherit" || via === "direct" || (g && g.role === "ru"))) throw httpErr(400, "via: inherit, direct или id внутренней группы.", 2, { field: "via" });
      return [obj, via, isU ? "user" : "device", it.ver];
    });
    if (plan.some(function (x) { return typeof x[3] !== "string" || !x[3]; })) throw httpErr(400, "Панель обновилась — обновите страницу", 2, { code: "ver_required" });
    plan.forEach(function (x) {
      var cur = ruVer(x[0]);
      if (x[3] !== cur) throw httpErr(409, "«Российские сайты» у «" + (x[0].name || x[0].id) + "» уже изменили (в другом окне, на схеме, в боте или в SSH-меню) — ничего не сохранено. Обновите страницу и выберите снова.", 3, { code: "object_changed", target: x[2], id: x[0].id, ver: cur });
    });
    var changed = 0;
    plan.forEach(function (x) { var was = ruOwn(x[0]) || "inherit"; if (was !== x[1]) changed++; if (x[1] === "inherit") delete x[0].ru_via; else x[0].ru_via = x[1]; });
    if (changed) bump();
    return { ok: true, changed: changed };
  }
  function polView() {
    var p = clone(pol());
    p.groups.forEach(function (g) { g.label = grpLabel(g); g.preset = presetOf(g); g.tiers.forEach(function (t, i) { t.label = tierLabel(t, i); }); });
    return p;
  }
  // «Свой» — поле пресета отличается от его значения (§1.3)
  function presetOf(g) {
    if (g.preset === "custom") return "custom";
    var p = mockPreset(g.preset), keys = ["flap_standby", "evict_degraded", "return_to_primary", "night_rebalance", "drop_on_degrade", "rules"];
    for (var i = 0; i < keys.length; i++) if (JSON.stringify(g[keys[i]]) !== JSON.stringify(p[keys[i]])) return "custom";
    if (JSON.stringify(g.checks.freq) !== JSON.stringify(p.freq) || JSON.stringify(g.checks.services) !== JSON.stringify(p.services)) return "custom";
    return g.preset;
  }
  function ver12(o) { var s = JSON.stringify(o), h = 5381, h2 = 52711; for (var i = 0; i < s.length; i++) { h = (h * 33 + s.charCodeAt(i)) >>> 0; h2 = (h2 * 31 + s.charCodeAt(i)) >>> 0; } return ("00000000" + h.toString(16)).slice(-8) + ("0000" + (h2 & 0xffff).toString(16)).slice(-4); }
  function revOf(body, query) { var r = body && body.rev !== undefined ? body.rev : (query && query.get("rev") !== null ? Number(query.get("rev")) : undefined); return r === undefined || r === null || r === "" || isNaN(Number(r)) ? undefined : Number(r); }
  function needRev(body, query) {
    var r = revOf(body, query);
    if (r === undefined) throw httpErr(400, "Панель обновилась — обновите страницу.", "rev_required");
    if (r !== db.policyRev) throw httpErr(409, "Политику изменили в другом месте — данные устарели.", "policy_changed", { rev: db.policyRev });
  }
  function needVer(body, cur) {
    if (!body || typeof body.ver !== "string") throw httpErr(400, "Панель обновилась — обновите страницу.", "ver_required");
    if (body.ver !== cur) throw httpErr(409, "Настройки изменили в другом месте.", "object_changed", { ver: cur });
  }
  function busyOnce() { if (BUSY_ONCE && !db._busyDone) { db._busyDone = true; throw httpErr(409, "Сервер применяет предыдущее изменение — повторите через минуту.", 6); } }
  function goneExit(id) { throw httpErr(404, "Такого выхода уже нет — обновите страницу.", "gone", { gone: true, exit: id }); }
  function polBump() { db.policyRev += 1; bump(); }
  // устройства на выходе (макет): Вена 7, Хельсинки 3, Токио 0, Новый ключ 2, пятый 1
  var EXIT_DEV = { "e-41a19b9cbe": 7, "e-7c2d8e0f13": 3, "e-9aa1b2c3d4": 0, "e-0f0f0f0f0f": 2, "e-46a226b167": 1 };
  function devOn(id) {
    var e = find(db.exits, id);
    if (e && e.role === "ru") return db.devices.filter(function (d) { return ruNet(d).exit === id; }).length;
    return e && e.enabled !== false ? (EXIT_DEV[id] || 0) : 0;
  }
  // moves — как policy_ops.impact: {total, by_group: {gid: {total, by_tier: {tid: n}, last_direct, last_block}}, direct,
  // block, devices}; m.step «block»/«direct» — устройства группы уходят на её последний шаг (годных выходов не осталось)
  // как model.kept_fail: fail не задан — свой маршрут объекта, иначе унаследованный (устройство — человека, затем
  // узла; человек — узла); новая цель не меняет «напрямую» на «блок» молча (REV-s1-t8-b С-1: мок подставлял «block»)
  function keptFail(what, obj) {
    if (obj && obj.route && obj.route.fail) return obj.route.fail;
    if (what === "device" && obj) { var ow = find(db.users, obj.user); if (ow && ow.route && ow.route.fail) return ow.route.fail; }
    return (db.gw.route && db.gw.route.fail) || "block";
  }
  function movesOf(list) {
    var out = { total: 0, by_group: {}, direct: 0, block: 0, devices: [] };
    list.forEach(function (m) {
      if (!m.n) return;
      out.total += m.n;
      var g = out.by_group[m.group] = out.by_group[m.group] || { total: 0, by_tier: {}, last_direct: 0, last_block: 0 };
      g.total += m.n;
      if (m.step) g["last_" + m.step] += m.n;
      else g.by_tier[m.tier] = (g.by_tier[m.tier] || 0) + m.n;
    });
    return out;
  }
  // выход уходит из работы: другие годные выходы группы есть — устройства переезжают внутри, нет — на последний шаг группы
  function liveExit(id) { var e = find(db.exits, id); return !!e && e.enabled !== false && !e._manual && !e._quotaHit; }
  function outOf(id) {
    var p = placeOf(id), g = p && polGroup(p.group);
    if (!p || !g) return [];
    var others = g.tiers.some(function (t) { return t.exits.some(function (x) { return x !== id && liveExit(x); }); });
    return others ? [{ group: p.group, tier: p.tier, n: devOn(id) }] : [{ group: p.group, step: g.fail === "direct" ? "direct" : "block", n: devOn(id) }];
  }
  function dryAnswer(rev, moves, warnings) {
    if (DRY_MODE === "fail") throw httpErr(500, "Не удалось посчитать последствия (пример ошибки сервера).", 1);
    var out = { ok: true, dry: true, rev: rev, moves: moves, warnings: warnings || [] };
    if (DRY_MODE === "slow") return new Promise(function (res) { setTimeout(function () { res(out); }, 25000); });
    return out;
  }
  // поля строки выхода по политике — как policy_ops.exit_fields (§7.9): health ok/degraded/standby/off/unknown (state —
  // слова 3.3.5 alive/dead), reasons [{code, args}] из кодов снимка (dead, quota, manual_standby, maintenance, rule:<id> →
  // {code: rule, args: {rule}}), country — код страны сейчас, country_expected — принятый, …
  var EXIT_MOCK = {
    "e-41a19b9cbe": { rtt: 38, loss: 0, jit: 4, country: "DE", expected: "AT", speed: { mbps: 84, up: 41, ts: nowS() - 3 * 3600 },
      quota: { period: "month", limit_gb: 50, reset_day: 1, warn_pct: 80, action: "standby", used_gb: 12.4, pct: 25, reset: "2026-11-01", paused_until: null } },
    "e-7c2d8e0f13": { rtt: 352, loss: 2, jit: 30, pstate: "degraded", reasons: [{ code: "rule", args: { rule: "r-rtt" } }], country: "FI", speed: { mbps: 46, ts: nowS() - 7 * 3600 } },
    "e-9aa1b2c3d4": { rtt: null, loss: 100, jit: null, reasons: [{ code: "dead", args: {} }], country: "JP" },
    "e-0f0f0f0f0f": { rtt: null, loss: null, jit: null, pstate: "standby", manual_why: "sub_confirm", reasons: [{ code: "manual_standby", args: {} }] },
    "e-46a226b167": { rtt: 61, loss: 1, jit: 9, unstable: true, country: "NL" },
    "e-a0a0a0a001": { rtt: 14, loss: 0, jit: 2, country: "RU" },
    "e-a0a0a0a002": RUG === "last" ? { rtt: 21, loss: 0, jit: 3, country: "FI", expected: "RU" } : { rtt: 21, loss: 0, jit: 3, country: "RU" },   // rug=last: «Дача» сменила страну (кадр 3.3)
    "e-a0a0a0a003": { rtt: 9, loss: 0, jit: 1, country: "RU" },
    "e-0d0d0d0d01": { rtt: 11, loss: 0, jit: 1, country: "NL" }, "e-0d0d0d0d02": { rtt: 95, loss: 0, jit: 6, country: "NL" },
    "e-0d0d0d0d03": { rtt: 12, loss: 0, jit: 1, country: "NL" }, "e-0d0d0d0d04": { rtt: 12, loss: 0, jit: 2, country: "NL" }
  };
  function policyRow(r, e) {
    var x = EXIT_MOCK[e.id] || {}, p = placeOf(e.id), alive = e.state === "alive";
    var ps = e._manual ? (e._manual === "maintenance" ? "off" : "standby") : !e.enabled ? "off" : e.state === "dead" ? "off" : x.pstate || (alive ? "ok" : "unknown");
    if (e._quotaHit) ps = "off";
    var q = e._quota !== undefined ? e._quota : (x.quota || null);
    return Object.assign(r, { health: ps, alive: alive, reasons: e._manual ? [{ code: e._manual === "standby" ? "manual_standby" : "maintenance", args: {} }] : e._quotaHit ? [{ code: "quota", args: {} }] : (x.reasons || []),
      group: p ? p.group : null, tier: p ? p.tier : null, devices: devOn(e.id), rtt_ms: alive ? x.rtt === undefined ? 50 : x.rtt : null,
      loss_pct: x.loss === undefined ? null : x.loss, jitter_ms: alive ? x.jit || null : null,
      country: x.country || null, country_expected: e._countryOk ? x.country || null : x.expected || x.country || null, speed: x.speed || null,
      quota: q ? Object.assign({}, q, e._quotaHit ? { pct: 100, used_gb: q.limit_gb, paused_until: "2026-11-01" } : {}) : null,
      speed_skip: !!e._speedSkip, probe_mb_day: e.kind === "awg" ? 0.4 : 1.2, last_check_ts: nowS() - 20,
      recovering: e.id === "e-41a19b9cbe" && !!db._recover, off_until: null, unstable: !!x.unstable, unstable_until: x.unstable ? nowS() + 600 : null,
      manual: e._manual || (x.manual_why && !e._confirmed ? "standby" : null), manual_why: e._manual ? null : (x.manual_why && !e._confirmed ? x.manual_why : null) });
  }
  // ── Активность людей (UI-B 23.10; SPEC-activity §5): условные данные, чтобы видеть «кардиограмму» и «График».
  // Сбора на узле ещё нет (ждёт сервер со сбором активности) — формат ответов из ТЗ: /api/gw/activity — 48 окон по 30 мин на устройство,
  // /api/gw/devices|users/<id>/activity?range=1d|7d|30d — ряд за период. ?act=off — история выключена, ?act=none —
  // сервер без сбора (404: линии «нет данных»). Данные постоянные для устройства (зерно — его id), без Math.random.
  var ACT = Q.get("act") || "";
  var ACT_STEP = 1800, ACT_N = 48;
  function actRng(seed) { var a = seed | 0; return function () { a = a + 0x6D2B79F5 | 0; var t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
  function actSeed(str) { var h = 7; for (var i = 0; i < str.length; i++) h = (h * 31 + str.charCodeAt(i)) >>> 0; return h; }
  // конец записанного (как metrics.dev_anchor у сборщика, что пишет): начало открытого окна 5 мин; начало — как у сервера
  function actAnchor() { return Math.floor(nowS() / 300) * 300; }
  function actT0() { return Math.floor((actAnchor() - 1) / ACT_STEP) * ACT_STEP - (ACT_N - 1) * ACT_STEP; }
  // открытое окно сборщика (live.json → activity, владелец 24.10): что накопили устройства в сети с начала окна 5 мин —
  // растёт каждый круг «сборщика» (Sse.simulate); новое окно — с нуля, ts — последнее «записанное»
  function actOpen() {
    if (ACT === "off" || ACT === "none") return null;
    var w = actAnchor(), now = nowS(), cur = {};
    if (db._actW !== w) { db._actW = w; db.devices.forEach(function (d) { d._cur = null; }); }
    if (!(db._actWiped && w <= db._actWiped))                          // №41: окно удаления остаётся пустым, как у сборщика
      db.devices.forEach(function (d) { if (d._cur && (d._cur[0] || d._cur[2] || d._cur[3])) cur[d.id] = d._cur.slice(); });
    return { ts: w - 300, w: w, at: now, secs: now - w, cur: cur };
  }
  // перезагрузка узла ночью — три окна без раунда сборщика (у всех строк на одной X)
  var ACT_GAP = [20, 21, 22];
  function actWins(d, idx) {
    if (d._act) return d._act;
    var r = actRng(actSeed(d.id)), on = [], kb = [], i, lv = function (lo, hi) { return Math.round(lo * Math.pow(hi / lo, r())); };
    var kind = d.kind === "router" ? "router" : d.kind === "tv" ? "tv" : d.kind === "laptop" || d.kind === "pc" ? "lap" : "phone";
    var live = !!(d._live ? d._live.online : idx % 3 !== 2) && d.enabled !== false;
    var tail = live ? 0 : 2 + Math.floor(r() * 10);                   // не в сети — последние окна пустые
    var never = idx % 9 === 7, quiet = idx % 9 === 5;                 // ещё не подключалось · на связи без трафика
    for (i = 0; i < ACT_N; i++) {
      var o = 0, k = 0, x = r();
      if (never) { on.push(0); kb.push(0); continue; }
      if (kind === "router") { o = 10; k = x < 0.15 ? lv(1500, 9000) : lv(30, 900); }
      else if (kind === "tv") { if (i >= 6 && i <= 12) { o = 10; k = lv(14000, 520000); } }
      else if (kind === "lap") { if (i < 3 || i > 32) { o = x < 0.8 ? 10 : 5; k = x < 0.6 ? lv(1500, 90000) : lv(60, 900); } }
      else { o = x < 0.55 ? 10 : x < 0.7 ? 5 : 0; k = o ? (r() < 0.45 ? lv(60, 9000) : r() < 0.6 ? lv(9000, 110000) : lv(0.5, 15)) : 0; }
      if (quiet && o) k = lv(1, 15);
      if (i >= ACT_N - tail) { o = 0; k = 0; }
      on.push(o); kb.push(k);
    }
    if (live && !never) { on[ACT_N - 1] = 10; if (!kb[ACT_N - 1] && !quiet) kb[ACT_N - 1] = lv(60, 9000); on[ACT_N - 2] = on[ACT_N - 2] || 10; }
    ACT_GAP.forEach(function (j) { on[j] = 0; kb[j] = 0; });
    var sum = kb.reduce(function (a, b) { return a + b; }, 0) * 1024;
    var onS = on.reduce(function (a, b) { return a + b * 180; }, 0), actS = kb.filter(function (b) { return b >= 1024; }).length * ACT_STEP;
    d._act = { on: on, kb: kb, rx: Math.round(sum * 0.09), tx: Math.round(sum * 0.91), on_s: onS, act_s: actS, never: never,
      last: never ? null : live ? nowS() - 30 : actT0() + (ACT_N - tail) * ACT_STEP - 600 };
    return d._act;
  }
  function activityDoc() {
    if (ACT === "none") throw httpErr(404, "Нет такого адреса.", 404);
    if (ACT === "off") return { ok: true, activity: false, why: "off" };
    var t0 = actT0(), cover = [], devs = {};
    if (db._actWiped) {                                                 // №41: окна до удаления стёрты, новых в макете нет
      for (var k = 0; k < ACT_N; k++) cover.push(0);
      return { ok: true, activity: true, step: ACT_STEP, t0: t0, n: ACT_N, now: actAnchor(), since: null, cover: cover, devices: devs };
    }
    for (var i = 0; i < ACT_N; i++) cover.push(ACT_GAP.indexOf(i) >= 0 ? 0 : 10);
    db.devices.forEach(function (d, idx) {
      var a = actWins(d, idx);
      if (a.never) return;                                              // ещё не подключалось — в ответ не попадает
      devs[d.id] = { on: a.on.slice(), kb: a.kb.slice(), rx: a.rx, tx: a.tx, on_s: a.on_s, act_s: a.act_s, last: a.last };
    });
    return { ok: true, activity: true, step: ACT_STEP, t0: t0, n: ACT_N, now: actAnchor(), since: t0 - 86400 * 40, cover: cover, devices: devs };
  }
  // «Хранить историю активности» и что она занимает (policy_ops.activity_get): после «Удалить историю» — 0 и since null
  function actKeepRec() { return db.actKeep || (db.actKeep = { keep: ACT !== "off" }); }
  function actSettingsDoc() {
    var ak = actKeepRec(), has = ak.keep && !db._actWiped;
    return { ok: true, keep: ak.keep, ver: ver12(ak), db: true, since: has ? actT0() - 86400 * 40 : null, bytes: has ? 2411724 : 0,
      kept: { m5_hours: 25, h1_days: 30, d1_days: 400 } };
  }
  // DELETE /api/gw/activity (№41, policy_ops.activity_wipe): «Хранить» не меняется; выключено — 409 activity_off,
  // sqlite3 нет (charts=0) — 409 nosqlite; ?dry=1 — сколько освободится
  function actWipe(dry) {
    if (ACT === "none") throw httpErr(404, "Нет такого адреса.", 404);
    if (!actKeepRec().keep || ACT === "off") throw httpErr(409, "Хранение истории активности выключено — история уже стёрта, удалять нечего.", "activity_off");
    if (NO_CHARTS) throw httpErr(409, "На сервере нет модуля sqlite3 — история активности не хранится, удалять нечего.", "nosqlite");
    var was = actSettingsDoc();
    if (dry) return { ok: true, dry: true, bytes: was.bytes, since: was.since };
    db._actWiped = nowS();
    db.devices.forEach(function (d) { d._cur = null; });
    bump();
    return Object.assign(actSettingsDoc(), { freed: was.bytes });
  }
  // ряд за период: 1d — 288 точек по 5 мин (из 48 окон), 7d — 168 по часу, 30d — 720 по часу; история устройства — с
  // его добавления (у ?people=40 — с 1 окт.: на «30 днях» видно «История с …»)
  function actSeries(d, idx, range) {
    var step = range === "1d" ? 300 : 3600, n = range === "1d" ? 288 : range === "7d" ? 168 : 720;
    var t0 = Math.floor((actAnchor() - 1) / step) * step - (n - 1) * step, a = actWins(d, idx), r = actRng(actSeed(d.id + range));
    var added = Math.floor(Date.parse(d.added || "") / 1000) || 0;
    var out = { on_s: [], act_s: [], rx: [], tx: [], cover: [] };
    for (var i = 0; i < n; i++) {
      var ts = t0 + i * step, w = Math.floor((ts - actT0()) / ACT_STEP), b = 0, o = 0, cov = step;
      if (db._actWiped && ts <= db._actWiped) { cov = 0; }             // №41: стёрто
      else if (a.never || ts + step <= added) { cov = ts + step <= added ? 0 : step; }
      else if (range === "1d" && w >= 0 && w < ACT_N) { o = a.on[w] / 10 * step; b = a.kb[w] * 1024 / 6 * (0.3 + r() * 1.4); if (ACT_GAP.indexOf(w) >= 0) cov = 0; }
      else { var hr = new Date(ts * 1000).getHours(), day = (hr >= 7 && hr <= 23) ? 1 : 0.15, x = r(); o = x < 0.7 * day + 0.1 ? step : 0; b = o ? Math.pow(10, 4 + r() * 4.2) * day : 0; if (range === "30d" && Math.floor((nowS() - ts) / 86400) === 21) cov = 0; }
      if (!cov) { o = 0; b = 0; }
      out.on_s.push(Math.round(o)); out.act_s.push(b > 1048576 ? Math.round(step * 0.6) : 0); out.rx.push(Math.round(b * 0.09)); out.tx.push(Math.round(b * 0.91)); out.cover.push(cov ? 10 : 0);
    }
    return Object.assign({ ok: true, step: step, t0: t0, n: n, now: actAnchor(), since: Math.max(added, t0 - 86400 * 40) }, out);
  }
  function actRoute(path, query) {
    if (ACT === "none") throw httpErr(404, "Нет такого адреса.", 404);
    if (ACT === "off") return { ok: true, activity: false, why: "off" };
    var range = query.get("range") || "1d", m;
    if (["1d", "7d", "30d"].indexOf(range) < 0) throw httpErr(400, "Поле range: 1d, 7d или 30d.", 2);
    if ((m = /^\/api\/gw\/devices\/(d-[0-9a-f]{8})\/activity$/.exec(path))) {
      var di = db.devices.findIndex(function (x) { return x.id === m[1]; });
      if (di < 0) throw httpErr(404, "Устройства нет.", 404);
      return actSeries(db.devices[di], di, range);
    }
    m = /^\/api\/gw\/users\/(u-[0-9a-f]{8})\/activity$/.exec(path);
    var u = m ? db.users.filter(function (x) { return x.id === m[1]; })[0] : null;
    if (!u) throw httpErr(404, "Пользователя нет.", 404);
    var sum = null, list = [];
    db.devices.forEach(function (d, i) {
      if (d.user !== u.id) return;
      var x = actSeries(d, i, range), sm = function (k) { return x[k].reduce(function (p, q) { return p + q; }, 0); };
      list.push({ id: d.id, rx: sm("rx"), tx: sm("tx"), on_s: sm("on_s"), act_s: sm("act_s") });
      if (!sum) { sum = x; return; }
      ["rx", "tx", "act_s"].forEach(function (k) { sum[k] = sum[k].map(function (v, j) { return v + x[k][j]; }); });
      sum.on_s = sum.on_s.map(function (v, j) { return Math.max(v, x.on_s[j]); });
      sum.cover = sum.cover.map(function (v, j) { return Math.max(v, x.cover[j]); });
      sum.since = Math.min(sum.since, x.since);
    });
    if (!sum) { var e = actSeries({ id: u.id, added: u.added }, 0, range); Object.keys(e).forEach(function (k) { if (Array.isArray(e[k])) e[k] = e[k].map(function () { return 0; }); }); sum = e; }
    return Object.assign(sum, { devices: list });
  }

  // история (§7.9 /history): ряд точек за диапазон, ≤ 1440
  function historyDoc(id, range) {
    if (NO_CHARTS) return { ok: true, charts: false, step: 0, cols: [], points: [], states: [] };
    var n = range === "90d" ? 360 : range === "7d" ? 336 : 288, span = range === "90d" ? 90 * 86400 : range === "7d" ? 7 * 86400 : 86400, step = Math.round(span / n);
    var x = EXIT_MOCK[id] || {}, base = x.rtt || 60, t0 = nowS() - span, pts = [], seed = id.charCodeAt(4);
    for (var i = 0; i < n; i++) {
      var ts = t0 + i * step, w = Math.sin((i + seed) / 9) * 0.25 + Math.sin(i / 2.3) * 0.08, bad = i > n * 0.62 && i < n * 0.66;
      var rtt = Math.round(base * (1 + w) + (bad ? 300 : 0)), mb = Math.max(0, Math.round((300 + 200 * w) * (i % 48 < 30 ? 1 : 0.2)));
      pts.push([ts, rtt, Math.round(rtt * 1.4), bad ? 12 : Math.max(0, Math.round(w * 4)), Math.round(4 + 3 * Math.abs(w)), mb * 7e5, mb * 3e5,
        Math.max(0, Math.round((EXIT_DEV[id] || 1) * (1 + w))), i % 24 === 3 ? Math.round(80 + w * 40) : null]);
    }
    // столбцы — как metrics.history: rx/tx — байты за шаг, mbps — тест скорости в шаге (null — не было)
    return { ok: true, charts: true, step: step, cols: ["rtt_med", "rtt_p90", "loss", "jitter", "rx", "tx", "devices", "mbps"], points: pts,
      states: [[t0, t0 + Math.round(span * 0.62), "ok"], [t0 + Math.round(span * 0.62), t0 + Math.round(span * 0.66), "degraded"], [t0 + Math.round(span * 0.66), nowS(), "ok"]] };
  }
  // линия «Выходов» (владелец 24.10, metrics.exit_summary): 48 окон по 30 мин из суточной истории каждого выхода
  function exitsActDoc() {
    var now = Math.floor(nowS() / 60) * 60, end = Math.floor(now / 1800) * 1800, t0 = end - 47 * 1800, out = {};
    if (NO_CHARTS) return { ok: true, charts: false, step: 1800, t0: t0, n: 48, now: now, exits: {} };
    var RANK = { ok: 1, degraded: 2, off: 3 };
    db.exits.forEach(function (e) {
      var h = historyDoc(e.id, "1d"), x = { kb: [], nd: [], st: [], rx: 0, tx: 0, off: 0, deg: 0 }, i;
      for (i = 0; i < 48; i++) { x.kb.push(0); x.nd.push(1); x.st.push(0); }
      h.points.forEach(function (p) { i = Math.floor((p[0] - t0) / 1800); if (i < 0 || i >= 48) return; x.kb[i] += Math.round((p[5] + p[6]) / 1024); x.nd[i] = 0; x.rx += p[5]; x.tx += p[6]; });
      h.states.forEach(function (st) {
        var a = Math.max(st[0], t0), b = Math.min(st[1], now), k = RANK[st[2]] || 0;
        if (b <= a) return;
        if (st[2] === "off") x.off += b - a; else if (st[2] === "degraded") x.deg += b - a;
        for (i = Math.max(0, Math.floor((a - t0) / 1800)); i <= Math.min(47, Math.floor((b - 1 - t0) / 1800)); i++) x.st[i] = Math.max(x.st[i], k);
      });
      out[e.id] = x;
    });
    return { ok: true, charts: true, step: 1800, t0: t0, n: 48, now: now, exits: out };
  }
  // ── круг 1 Т1 (вариант Б «Обзора»): балл 0–1000, «Выходы · час», ряды узла, «Сейчас» и расписание проверок ─────────
  // Числа условные, форма — как у сервера (panel/host.py synth, gw/metrics.exit_hour, host_history, hostscore.score_doc,
  // bench/schedule). ?charts=0 — истории нет (пустые ответы, как без sqlite3).
  var T1_LINKS = ["net_bw", "net_q", "cpu", "stab", "down", "up", "loss", "crypto"];
  // ?nd=up — как на стенде it1 (Н-1, Н-2): звено без данных во всех проверках, у первого выхода задержка через минуту
  var T1_ND = T1_LINKS.indexOf(Q.get("nd")) >= 0 ? Q.get("nd") : null;
  // ?young=1 — первые часы нового узла (владелец 28.10, решение 4): пробы набрали 5 ч из 12, «Сеть» и «Потери» ещё
  // измеряются, итог «предварительно»; калибровка по звеньям (решение 5) — как у сервера: выверены процессор и шифрование
  var T1_YOUNG = Q.get("young") === "1";
  var T1_CALIB = { cpu: true, crypto: true };
  function t1Day(i) { return new Date((Math.floor(nowS() / 86400) - i) * 86400000).toISOString().slice(0, 10); }
  // пороги правил у каждой проверки — как panel/host._limits (hostmon.RULES: warn, crit, down)
  var T1_LIM = { cpu: [85, 95, false], mem: [10, 5, true], disk: [90, 98, false], retrans: [2, 5, false] };
  function t1Limits(ans) {
    var c = ans && ans.health && ans.health.checks;
    if (c) Object.keys(T1_LIM).forEach(function (k) { if (c[k]) { c[k].warn_at = T1_LIM[k][0]; c[k].crit_at = T1_LIM[k][1]; c[k].down = T1_LIM[k][2]; } });
    return ans;
  }
  function synthDoc() {
    var base = { net_bw: 812, net_q: 864, cpu: 745, stab: 902, down: 838, up: 512, loss: 879, crypto: 790 };
    var usual = { net_bw: 818, net_q: 861, cpu: 748, stab: 897, down: 842, up: 694, loss: 876, crypto: 791 };
    var doc = { ok: true, charts: !NO_CHARTS, version: "abs-0.5-k1", calibrated: false, anchors: [100, 500, 700, 900], margin: 350, weak_gap: 30, provisional: null, net_window_h: 12,
      fresh: { total: null, raw: null, cap: null, weak: null, limited: false, why: "nothing_measured", measured: 0 }, links: [], checks: [],
      totals: { usual: null, min: null, max: null, n: 0 }, last_at: null, stale: false };
    if (NO_CHARTS) { T1_LINKS.forEach(function (id) { doc.links.push({ id: id, fresh: null, usual: null, lo: null, hi: null, n: 0, dev: null, calib: !!T1_CALIB[id] }); }); return doc; }
    var young = T1_YOUNG ? { net_q: 1, loss: 1 } : {};
    if (T1_YOUNG) doc.provisional = { why: "net_young", have_h: 5, need_h: 12 };
    T1_LINKS.forEach(function (id) {
      doc.links.push(young[id] ? { id: id, fresh: null, usual: null, lo: null, hi: null, n: 0, dev: null, calib: false, why: "young", why_text: null }
        : id === T1_ND ? { id: id, fresh: null, usual: null, lo: null, hi: null, n: 0, dev: null, calib: !!T1_CALIB[id], why: id === "up" ? "no_upload_ru" : null, why_text: null }
        : { id: id, fresh: base[id], usual: usual[id], lo: usual[id] - 24, hi: Math.min(999, usual[id] + 21), n: 11, dev: id === "up" ? "down" : null, calib: !!T1_CALIB[id] });
    });
    // слабое звено — как у сервера (D-143): отдача 512 ниже среднего звеньев больше чем на 30 %; без неё (?nd=up) — нет
    doc.fresh = T1_ND === "up" ? { total: 805, raw: 805, cap: null, weak: null, limited: false, why: null, measured: 1 }
      : { total: 805, raw: 805, cap: 862, weak: "up", limited: false, why: null, measured: 1 };   // предел не держит: итог = raw
    var now = nowS(), i, tot = [802, 798, 806, 811, 795, 800, 803, 808, 799, 806, 794, 791];
    for (i = 0; i < 12; i++) {
      var links = {}, k = 11 - i;
      T1_LINKS.forEach(function (id, j) { links[id] = id === T1_ND ? null : usual[id] + ((k * 7 + j * 5) % 17) - 8 - (id === "up" && k < 2 ? 160 : 0); });
      doc.checks.push({ id: "r" + k, ts: now - i * 86400 - 3 * 3600, day: t1Day(i), mode: i % 7 === 3 ? "full" : "quick", by: i ? "schedule" : "panel",
        total: tot[11 - i], raw: tot[11 - i], cap: 900, weak: "up", limited: false, why: null, links: links });
    }
    doc.totals = { usual: 802, min: 791, max: 811, n: 12 };
    doc.last_at = now - 3 * 3600;
    return doc;
  }
  function exitHourDoc() {
    var now = nowS(), t0 = (Math.floor(now / 60) - 59) * 60, out = {};
    if (NO_CHARTS) return { ok: true, charts: false, step: 60, t0: t0, n: 60, now: now, exits: {} };
    db.exits.forEach(function (e, k) {
      var x = { rtt: [], loss: [], mbps: [], st: [] }, base = (EXIT_MOCK[e.id] || {}).rtt || 40 + k * 15, i;
      for (i = 0; i < 60; i++) {
        var off = !e.enabled;
        x.rtt.push(off || (T1_ND && k === 0 && i % 2) ? null : Math.round(base * (1 + Math.sin((i + k * 7) / 6) * 0.2 + (i > 40 && i < 44 && k === 1 ? 2.5 : 0))));
        x.loss.push(off ? null : (i % 13 === 0 ? 0.4 : 0)); x.mbps.push(off ? null : Math.round((8 + 6 * Math.sin(i / 5 + k)) * 10) / 10); x.st.push(off ? 3 : 1);
      }
      out[e.id] = x;
    });
    return { ok: true, charts: true, step: 60, t0: t0, n: 60, now: now, exits: out };
  }
  function hostHistDoc(range) {
    if (NO_CHARTS) return { ok: true, charts: false };
    var week = range === "7d", step = week ? 3600 : 300, n = week ? 168 : 288, now = Math.floor(nowS() / step) * step, pts = [], i;
    var cols = ["busy", "mem_avail", "disk", "rx", "tx", "retrans", "out_segs"];
    for (i = 0; i < n; i++) {
      var ts = now - (n - 1 - i) * step, h = new Date(ts * 1000).getHours(), day = h >= 9 && h <= 23 ? 1 : 0.35;
      var w = Math.sin(i / 7) * 0.2 + day, rx = Math.round((week ? 3600 : 300) * 1e6 / 8 * 40 * w), tx = Math.round(rx * 0.9);
      pts.push([ts, Math.round((22 + 30 * w) * 10) / 10, Math.round((58 - 6 * w) * 10) / 10, 41.5 + i * 0.002, rx, tx, Math.round(40 * w), 40000]);
    }
    var doc = { charts: true, step: step, cols: cols, points: pts };
    return { ok: true, charts: true, host: doc, ping: { charts: true, step: step, cols: [], points: [] } };
  }
  function scoreMockDoc() {
    var days = [], i;
    for (i = 29; i >= 0; i--) days.push({ day: t1Day(i), color: i === 3 ? "yellow" : i === 17 ? "grey" : i === 9 ? "red" : "green",
      reason: i === 3 ? { code: "warn", args: {}, msg: "Отдача хуже обычного" } : null, test: i % 2 === 0, full: i === 14, partial: false, checks: [], m: {} });
    if (NO_CHARTS) return { ok: true, charts: false, links: [], days: [], series: {}, summary: { rel_total: null, rel_n: 0, rel_need: 3 }, dev_rule: { pct: 15, use_spread: true } };
    var P = function (test, f, u, unit, rel) { return { test: test, fresh_raw: f, usual_raw: u, unit: unit, rel: rel, usual_n: 11, fresh_n: 3 }; };
    var one = function (id, src, p, dev) { return { id: id, source: src, rel: p.rel, test: p.test, fresh_raw: p.fresh_raw, fresh_n: 3, unit: p.unit, usual_raw: p.usual_raw, usual_n: 11,
      usual_mode: null, p25_raw: null, p75_raw: null, dev: dev || null, dev_test: dev ? p.test : null, dev_pending: null, parts: [p], hint: null }; };
    var down = P("net_down", 675, 690, "Мбит/с", 98), up = P("net_up", 120, 300, "Мбит/с", 40);
    var links = [one("net_bw", "tests", P("net_down", null, null, null, 70)), one("net_q", "probes", P("rtt", 38, 36, "мс", 95)), one("cpu", "tests", P("cpu_mt", 1530, 1540, "событий/с", 99)),
      one("stab", "probes", P("net_cv", 6, 7, "%", 101)), one("down", "tests", down), one("up", "tests", up, "down"), one("loss", "probes", P("loss", 0.06, 0.05, "%", 83)),
      one("crypto", "tests", P("aes_1t", 2900, 2950, "МБ/с", 98))];
    links[1].parts = [P("rtt", 38, 36, "мс", 95), P("jitter", 1.2, 1.1, "мс", 92), P("loss", 0.06, 0.05, "%", 83)];
    links[2].parts = [P("cpu_1t", 1180, 1200, "событий/с", 98), P("cpu_mt", 1530, 1540, "событий/с", 99)];
    return { ok: true, charts: true, dev_rule: { pct: 15, use_spread: true }, links: links, rows: { people: null, load: null }, stab: null, series: {}, days: days,
      summary: { rel_total: 96, rel_n: 7, rel_need: 3, last_id: "r11", last_at: nowS() - 3 * 3600, fresh_from: null, first: false, stale: false,
        checked: { part: "night", hhmm: "04:10" }, retry: null, base_since: [] } };
  }
  function benchSchedDoc() {
    var nx = nowS() + 14 * 3600;
    return { ok: true, schedule: [{ id: "a", time: "04:30", mode: "quick", days: [1, 2, 3, 4, 5, 6, 7], on: true, monthly: false },
      { id: "b", time: "05:00", mode: "full", days: [7], on: false, monthly: false }], retry: true, armed: true, armed_why: null, tz: "Europe/Moscow",
      next: [{ ts: nx, key: "", mode: "quick" }], cost: { basis: "caps", day: { rx_mb: 120, tx_mb: 120, cpu_min: 0.9 }, month: { rx_mb: 3600, tx_mb: 3600 },
        week: { quick: 7, full: 0 }, max_per_day: 1, per_day_even: true, notes: [] }, limits: { per_day: 6, full_per_day: 2 }, etag: "\"m1\"" };
  }
  function transitionsDoc(id, query) {
    if (NO_CHARTS) return { ok: true, db: false, items: [], next: null };
    var base = nowS(), before = Number(query.get("before")) || 0;
    // why — как health._why_word: причина нового состояния (dead, quota, maintenance, manual, sub_confirm, unstable, rule:<id>);
    // возврат в «годен» — без причины (null)
    var all = [[base - 420, "degraded", "ok", null, {}], [base - 780, "ok", "degraded", "rule:r-rtt", {}],
      [base - 13 * 3600, "off", "ok", null, {}], [base - 13.2 * 3600, "ok", "off", "dead", {}], [base - 3 * 86400, "standby", "ok", null, {}],
      [base - 3.1 * 86400, "ok", "standby", "manual", {}]];
    if (MANY_TRANS) for (var i = 0; i < 39; i++) all.push([base - (4 + i) * 86400, i % 2 ? "ok" : "degraded", i % 2 ? "degraded" : "ok", i % 2 ? "rule:r-rtt" : null, {}]);
    var items = all.filter(function (x) { return !before || x[0] < before; }).map(function (x) { return { ts: Math.round(x[0]), from: x[1], to: x[2], why: x[3], args: x[4] }; });
    var lim = Math.max(1, Math.min(500, Number(query.get("limit")) || 100)), page = items.slice(0, lim);
    return { ok: true, db: true, items: page, next: items.length > lim ? page[page.length - 1].ts : null };
  }
  function exitOrGone(id) { var e = find(db.exits, id); if (!e) goneExit(id); return e; }
  function policyRoute(method, path, body, query) {
    var m, b = body || {}, dry = query && query.get("dry") === "1";
    pol();
    if (method === "GET" && path === "/api/gw/policy") return { ok: true, rev: db.policyRev, policy: polView(), presets: { abroad: { economy: mockPreset("economy"), standard: mockPreset("standard"), strict: mockPreset("strict"), compat: mockPreset("compat") }, ru: { ru_home: mockPreset("ru_home") } },
      forecast: { probe_mb_day: pol().groups.reduce(function (o, g) { o[g.id] = g.checks.freq.mode === "lazy" ? 5 : g.checks.freq.mode === "normal" ? 35 : 105; return o; }, {}), speed_gb_month: 0.4 } };
    if (method === "PUT" && path === "/api/gw/policy") {
      if (!dry) { busyOnce(); needRev(b, query); }
      var np = b.policy || b, keep = pol(), moved = [], warns = [];
      if (!np || !Array.isArray(np.groups)) throw httpErr(400, "Нет политики в запросе.", 2, { field: "groups" });
      keep.groups.forEach(function (g) { if (!np.groups.some(function (x) { return x.id === g.id; })) throw httpErr(409, "Группу «" + grpLabel(g) + "» изменили в другом месте.", "group_missing", { group: g.id }); });
      np.groups.forEach(function (ng) {
        var og = polGroup(ng.id);
        if (!og) throw httpErr(409, "Новую группу создают отдельно.", "group_missing", { group: ng.id });
        var before = [], after = [];
        og.tiers.forEach(function (t) { before = before.concat(t.exits); });
        (ng.tiers || []).forEach(function (t) { after = after.concat(t.exits || []); });
        if (before.slice().sort().join() !== after.slice().sort().join()) throw httpErr(400, "Состав группы меняют кнопками «Добавить выход» и «Перенести».", "use_move", { field: "tiers" });
        (ng.tiers || []).forEach(function (t, i) { (t.exits || []).forEach(function (id) { var p = placeOf(id); if (p && p.n !== i) moved.push({ group: ng.id, tier: t.id, n: devOn(id) }); }); });
        // предупреждения сервер отдаёт строками (policy_ops._save → текст проверки политики); «напрямую» и перезапуск
        // xray панель пишет сама (строки, известные заранее)
        if ((ng.tiers || []).some(function (t) { return t.method !== "weights" && Object.keys(t.weights || {}).some(function (k) { return t.weights[k] !== 1; }); })) warns.push("Веса действуют только при способе «по весам»");
      });
      if (dry) return dryAnswer(db.policyRev, movesOf(moved), warns);
      np.groups.forEach(function (ng) { var og = polGroup(ng.id), nm = og.name; Object.keys(ng).forEach(function (k) { if (k !== "label") og[k] = clone(ng[k]); }); og.name = nm; og.tiers.forEach(function (t) { delete t.label; }); delete og.label; });
      if (np.tg_proxy_group && polGroup(np.tg_proxy_group)) keep.tg_proxy_group = np.tg_proxy_group;
      if (np.hint_seen === true) keep.hint_seen = true;
      polBump();
      return { ok: true, rev: db.policyRev, moves: movesOf(moved), warnings: warns };
    }
    if (method === "POST" && path === "/api/gw/policy/reset") {
      var g0 = polGroup(b.group || "main");
      if (!g0) throw httpErr(404, "Такой группы уже нет — обновите страницу.", "gone", { gone: true, group: b.group });
      if (dry) return dryAnswer(db.policyRev, movesOf([]), b.preset === "standard" ? ["Нестабильный выход — в запас на 15 минут"] : []);
      busyOnce(); needRev(b, query);
      var fresh = mockGroup(g0.id, g0.name, b.preset || "standard", g0.tiers);
      fresh.fail = g0.fail; fresh.checks.speed = g0.checks.speed;
      pol().groups[pol().groups.indexOf(g0)] = fresh;
      if (b.preset === "standard") pol().hint_seen = true;
      polBump();
      return { ok: true, rev: db.policyRev, moves: movesOf([]), warnings: [] };
    }
    // плашка «Появились группы выходов» закрыта: только флаг, вне версии (policy_ops.hint_seen) — rev не растёт
    if (method === "POST" && path === "/api/gw/policy/hint-seen") { pol().hint_seen = true; return { ok: true, hint_seen: true }; }
    if (method === "POST" && path === "/api/gw/policy/check-url") {
      var u = String(b.url || "");
      if (!/^https:\/\//.test(u)) return { ok: false, code: null, mbps: null, final: "", error_code: "not_https" };
      return { ok: true, code: 200, mbps: 92, final: u.replace(/^https:\/\//, "").slice(0, 60), error_code: null };
    }
    if (method === "POST" && path === "/api/gw/groups") {
      needRev(b, query);
      var nm = String(b.name || "").trim();
      if (!nm || nm.length > 64) throw httpErr(400, "Имя группы: от 1 до 64 знаков.", 2, { field: "name" });
      var dups = pol().groups.filter(function (g) { return grpLabel(g).toLowerCase() === nm.toLowerCase(); }).map(grpLabel);
      if (dups.length && !b.allow_duplicate) throw httpErr(409, "Группа с таким именем уже есть.", 3, { need: "confirm_duplicate", duplicates: dups });
      if (pol().groups.length >= 8) throw httpErr(409, "Групп не больше 8.", 3);
      if (b.role !== undefined && b.role !== "abroad" && b.role !== "ru") throw httpErr(400, "role: abroad или ru.", 2, { field: "role" });
      var gid = rid("g-", 6), ng2 = b.role === "ru" ? ruGroup(gid, nm, []) : mockGroup(gid, nm, "standard", [mockTier("t1", "", [])]);
      if (b.fail === "direct" || b.fail === "block") ng2.fail = b.fail;
      pol().groups.push(ng2); polBump();
      return { ok: true, id: gid, rev: db.policyRev };
    }
    if ((m = /^\/api\/gw\/groups\/(main|g-[0-9a-f]{6})$/.exec(path)) && method === "PUT") {
      var gr = polGroup(m[1]);
      if (!gr) throw httpErr(404, "Уже удалено в другом месте — обновите страницу.", "gone", { gone: true, group: m[1] });
      var name = typeof b.name === "string" ? b.name.trim() : null;
      if (name === null) throw httpErr(400, "Нужно поле «name».", 2, { field: "name" });
      if (!name && gr.id !== "main") throw httpErr(400, "Имя группы не может быть пустым.", 2, { field: "name" });
      if (name.length > 64) throw httpErr(400, "Имя группы: не длиннее 64 знаков.", 2, { field: "name" });
      var d2 = pol().groups.filter(function (g) { return g.id !== gr.id && grpLabel(g).toLowerCase() === name.toLowerCase(); }).map(grpLabel);
      if (name && d2.length && !b.allow_duplicate) throw httpErr(409, "Группа с таким именем уже есть.", 3, { need: "confirm_duplicate", duplicates: d2 });
      var old = grpLabel(gr);
      gr.name = name === "Основная" && gr.id === "main" ? "" : name; bump();
      return { ok: true, id: gr.id, name: gr.name, label: grpLabel(gr), old: old };
    }
    if ((m = /^\/api\/gw\/groups\/(main|g-[0-9a-f]{6})$/.exec(path)) && method === "DELETE") {
      // как policy_ops.group_delete (+ 8а _group_drop): «Основная» — 400; нет группы — 404 gone; exits — только «drop» и
      // только у российской группы (иначе 400 field «exits»); чего не хватает — 409 «need» с data {exits, refs, uses}
      var dg = polGroup(m[1]), xq = query.get("exits"), fieldErr = function (f) { return httpErr(400, "Неверное поле «" + f + "».", "field", { field: f }); };
      if (m[1] === "main") throw httpErr(400, "Основную группу удалить нельзя.", "main");
      if (xq !== null && xq !== "" && (xq !== "drop" || query.get("exits_to"))) throw fieldErr("exits");
      var drop = xq === "drop", isRu = !!dg && dg.role === "ru";
      if (drop && dg && !isRu) throw fieldErr("exits");
      if (!dg) throw httpErr(404, "Такой группы уже нет — обновите страницу.", "gone", { gone: true, group: m[1] });
      if (!dry) needRev(null, query);
      // ask=1 (только с dry, как api.gw_group_delete): «need» и «ru_last» — ответом 200 {ask: {code, data}}, без 409
      var ask = query.get("ask");
      if (ask !== null && (ask !== "1" || !dry)) throw fieldErr("ask");
      var askOf = function (e) { if (ask && e && e.http === 409 && (e.code === "need" || e.code === "ru_last")) return { ok: true, ask: { code: e.code, data: e.data || {} } }; throw e; };
      var exitsIn = [];
      dg.tiers.forEach(function (t) { exitsIn = exitsIn.concat(t.exits); });
      var to = query.get("exits_to"), re = query.get("reassign"), uses = [], need = {};
      var via = "group:" + dg.id, refs = { users: [], devices: [] };
      if (isRu) {                                    // российская группа: на неё ссылается свой выбор «Российские сайты» (ru_via)
        db.users.forEach(function (u) { if (ruOwn(u) === dg.id) refs.users.push(u.id); });
        db.devices.forEach(function (d) { if (ruOwn(d) === dg.id) refs.devices.push(d.id); });
      } else {
        db.users.forEach(function (u) { if (u.route && u.route.via === via) refs.users.push(u.id); });
        db.devices.forEach(function (d) { if (d.route && d.route.via === via) refs.devices.push(d.id); });
        if (db.gw.route && db.gw.route.via === via) refs["default"] = true;
      }
      if (pol().tg_proxy_group === dg.id) uses.push("tg_proxy");
      if (pol().new_exits_group === dg.id) uses.push("new_exits");
      var subIds = db.subs.filter(function (x) { return x.group === dg.id; }).map(function (x) { return x.id; });
      if (subIds.length) uses.push("subs");
      var hasRefs = refs.users.length || refs.devices.length || refs["default"];
      if (exitsIn.length && !to && !drop) need.exits = exitsIn;
      if (hasRefs && !re) need.refs = refs;
      // подписки группы при drop не переносятся и не удаляются молча — «сначала удалите подписку»
      var miss = uses.filter(function (u) { return !query.get(u === "tg_proxy" ? "tg_proxy_to" : u === "new_exits" ? "new_exits_to" : "subs_to") || (drop && u === "subs"); });
      if (miss.length) need.uses = miss;
      var lastRu = isRu && !pol().groups.some(function (g) { return g.role === "ru" && g.id !== dg.id; });
      if (exitsIn.length && !drop && lastRu) return askOf(httpErr(409, "Это последняя внутренняя группа: сначала перенесите или удалите её ключи.", "ru_last", { exits: exitsIn }));
      if (Object.keys(need).length) return askOf(httpErr(409, "Выберите, куда перенести выходы, пользователей и другие ссылки на группу.", "need", need));
      var tgt = function (v, field, role) {
        var t = polGroup(v);
        if (!t || v === dg.id) throw httpErr(400, "Неверное поле «" + field + "»: " + v, "field", { field: field });
        if ((t.role || "abroad") !== role) throw httpErr(400, "Поле «" + field + "»: группа другой роли — " + v, "field", { field: field });
        return t;
      };
      if (exitsIn.length && !drop) tgt(to, "exits_to", dg.role || "abroad");
      if (hasRefs && re !== "direct") tgt(re, "reassign", dg.role || "abroad");
      uses.forEach(function (u) { tgt(query.get(u === "tg_proxy" ? "tg_proxy_to" : u === "new_exits" ? "new_exits_to" : "subs_to"), u + "_to", u === "subs" ? dg.role || "abroad" : "abroad"); });
      var mv, mvd;
      if (isRu) {                                    // российская сторона устройств (impact «ru»): куда пойдут их российские сайты
        var ruDevs = db.devices.filter(function (d) { return ruViaOf(d).via === dg.id && ruApplies(d); }).length;
        mvd = { total: ruDevs, by_group: {}, direct: 0, block: 0, devices: [], ru: { total: ruDevs, by_group: {}, direct: 0, none: 0 } };
        if (ruDevs && re === "direct") mvd.ru.direct = ruDevs;
        else if (ruDevs && re) mvd.ru.by_group[re] = { total: ruDevs, by_tier: { t1: ruDevs }, last_direct: 0, last_block: 0 };
      } else {
        mv = exitsIn.map(function (id) { return { group: to, tier: "t1", n: devOn(id) }; });
        mvd = movesOf(mv);
      }
      var outRefs = { users: refs.users, devices: refs.devices };
      if (dry) {
        var da = dryAnswer(db.policyRev, mvd, []), extra = { refs: outRefs, uses: uses };
        if (drop) extra.drop = exitsIn.slice();
        return da && typeof da.then === "function" ? da.then(function (x) { return Object.assign(x, extra); }) : Object.assign(da, extra);
      }
      if (drop) busyOnce();                          // ключи уходят скриптом пула — «занято» бывает, как у «Удалить ключ»
      if (drop) {
        exitsIn.forEach(function (id) { unplace(id); });
        db.exits = db.exits.filter(function (e) { return exitsIn.indexOf(e.id) < 0; });
        db.exits.forEach(function (e, i) { e.index = i + 1; });
      } else {
        exitsIn.forEach(function (id) { placeNew(id, to); });
      }
      if (hasRefs && isRu) {
        db.users.concat(db.devices).forEach(function (o) { if (ruOwn(o) === dg.id) o.ru_via = re; });
      } else if (hasRefs) {
        var nvia = re === "direct" ? "direct" : re === "main" ? "pool" : "group:" + re;
        db.users.concat(db.devices).forEach(function (o) { if (o.route && o.route.via === via) o.route = Object.assign({}, o.route, { via: nvia }); });
        if (refs["default"]) db.gw.route = Object.assign({}, db.gw.route, { via: nvia });
      }
      if (uses.indexOf("tg_proxy") >= 0) pol().tg_proxy_group = query.get("tg_proxy_to");
      if (uses.indexOf("new_exits") >= 0) pol().new_exits_group = query.get("new_exits_to");
      db.subs.forEach(function (x) { if (x.group === dg.id) x.group = query.get("subs_to"); });
      pol().groups = pol().groups.filter(function (g) { return g.id !== dg.id; });
      polBump();
      var ans = { ok: true, rev: db.policyRev, moves: mvd, refs: outRefs, uses: uses, warnings: [] };
      if (drop) ans.dropped = exitsIn.slice();
      return ans;
    }
    if ((m = /^\/api\/gw\/exits\/(e-[0-9a-f]{10})\/(move|manual|enabled|country)$/.exec(path)) && method === "POST") {
      var ex = exitOrGone(m[1]), what = m[2];
      if (!dry) { busyOnce(); needRev(b, query); }
      if (what === "move") {
        var tg = polGroup(b.group);
        if (!tg) throw httpErr(404, "Такой группы уже нет — обновите страницу.", "gone", { gone: true, group: b.group });
        var cur = placeOf(ex.id), mvs = cur && cur.group !== tg.id ? outOf(ex.id) : [];
        var w = mvs.length && mvs[0].n ? ["Устройства, закреплённые за выходом, перейдут на другие выходы своей группы"] : [];
        var toRole = tg.role || "abroad", fromRole = ex.role || "abroad";
        if (toRole !== fromRole && b.confirm_role !== true) throw httpErr(409, "Выход переходит в группу другой роли — подтвердите перенос.", "confirm_role", { exit: ex.id, from: cur ? cur.group : null, to: tg.id });
        if (dry) return dryAnswer(db.policyRev, movesOf(mvs), w);
        unplace(ex.id);
        if (toRole !== fromRole) { ex.role = toRole; ex.tag = exitTag(ex.id, toRole); }
        var tt = b.tier ? tg.tiers.filter(function (t) { return t.id === b.tier; })[0] : null;
        if (tt) tt.exits.push(ex.id); else placeNew(ex.id, tg.id);
        polBump(); return { ok: true, rev: db.policyRev, moves: movesOf(mvs), warnings: w };
      }
      if (what === "manual") {
        if (["standby", "restore", "maintenance"].indexOf(b.state) < 0) throw httpErr(400, "state: standby, restore или maintenance.", 2, { field: "state" });
        var mm = b.state === "restore" ? [] : outOf(ex.id);
        if (dry) return dryAnswer(db.policyRev, movesOf(mm), []);
        ex._manual = b.state === "restore" ? null : b.state;
        if (b.state === "restore") { ex._quotaHit = false; ex._confirmed = true; }
        polBump(); return { ok: true, state: b.state, rev: db.policyRev, moves: movesOf(mm), warnings: [] };
      }
      if (what === "enabled") {
        if (typeof b.on !== "boolean" && !dry) throw httpErr(400, "on: true или false.", 2, { field: "on" });
        var me = b.on === false ? outOf(ex.id) : [];
        if (dry) return dryAnswer(db.policyRev, movesOf(me), []);
        ex.enabled = b.on; polBump();
        return { ok: true, enabled: ex.enabled, rev: db.policyRev, moves: movesOf(me), warnings: [] };
      }
      if (dry) return dryAnswer(db.policyRev, movesOf([]), []);
      ex._countryOk = true; polBump();
      return { ok: true, country: (EXIT_MOCK[ex.id] || {}).country || null, rev: db.policyRev, moves: movesOf([]), warnings: [] };
    }
    if ((m = /^\/api\/gw\/exits\/(e-[0-9a-f]{10})\/speedtest$/.exec(path)) && method === "POST") { exitOrGone(m[1]); return { ok: true, job: "sp-" + rid("", 6) }; }
    if ((m = /^\/api\/gw\/exits\/(e-[0-9a-f]{10})\/history$/.exec(path)) && method === "GET") { exitOrGone(m[1]); return historyDoc(m[1], query.get("range") || "1d"); }
    if ((m = /^\/api\/gw\/exits\/(e-[0-9a-f]{10})\/transitions$/.exec(path)) && method === "GET") { exitOrGone(m[1]); return transitionsDoc(m[1], query); }
    if (path === "/api/gw/probes/speed") {
      var sp = pol().probes.speed;
      if (method === "GET") return Object.assign({ ok: true }, clone(sp), { ver: ver12(sp) });
      needVer(b, ver12(sp));
      var keys = ["max_mb", "max_s", "only_quiet", "budget_day_mb", "budget_month_gb"];
      Object.keys(b).forEach(function (k) { if (k !== "ver" && keys.indexOf(k) < 0) throw httpErr(400, "Лишнее поле «" + k + "».", 2, { field: k }); });
      keys.forEach(function (k) { if (b[k] !== undefined) sp[k] = b[k]; });
      bump(); return Object.assign({ ok: true }, clone(sp), { ver: ver12(sp) });
    }
    // «Настройки › Хранение данных» (policy_ops.activity_get / activity_set): выключатель истории активности, ver
    if (path === "/api/gw/activity/settings") {
      if (ACT === "none") throw httpErr(404, "Нет такого адреса.", 404);
      var ak = actKeepRec(), akDoc = actSettingsDoc;
      if (method === "GET") return akDoc();
      needVer(b, ver12(ak));
      if (typeof b.keep !== "boolean") throw httpErr(400, "Поле keep — да или нет.", 2, { field: "keep" });
      ak.keep = b.keep; ACT = b.keep ? "" : "off";
      bump(); return akDoc();
    }
    // «Настройки › Время» (policy_ops.tz_get / tz_set): пояс узла, пояс и часы системы, NTP, пояса базы, ver
    if (path === "/api/settings/time") {
      if (TIME_SCENE === "none") throw httpErr(404, "Not found");
      var tz = db.tz || (db.tz = { tz: TIME_SCENE === "warn" ? "Europe/Atlantis" : "Europe/Moscow" });
      var zones = mockZones(), nowTs = Math.floor(Date.now() / 1000);
      var tzDoc = function () {
        var bad = zones.indexOf(tz.tz) < 0, d = { ok: true, tz: tz.tz, os_tz: "Etc/UTC", now: nowTs, offset: bad ? 10800 : mockOffset(tz.tz, nowTs),
          ntp: TIME_SCENE !== "ntpoff", zones: zones, ver: ver12(tz) };
        if (bad) d.warn = "no_zone";
        return d;
      };
      if (method === "GET") return tzDoc();
      needVer(b, ver12(tz));
      if (typeof b.tz !== "string" || zones.indexOf(b.tz) < 0) throw httpErr(400, "Часового пояса «" + b.tz + "» нет в базе поясов сервера.", 2, { field: "tz" });
      tz.tz = b.tz;
      bump(); return tzDoc();
    }
    if (path === "/api/notify") {
      if (method === "GET") return { ok: true, push: clone(db.notify.push), flap_summary: db.notify.flap_summary, ver: ver12(db.notify) };
      needVer(b, ver12(db.notify));
      if (b.push !== undefined && (!b.push || typeof b.push !== "object")) throw httpErr(400, "Неверное поле «push»", 2, { field: "push" });
      Object.keys(b.push || {}).forEach(function (k) {               // как policy_ops.notify_set: чужой вид или не bool — 400
        if (!(k in db.notify.push) || typeof b.push[k] !== "boolean") throw httpErr(400, "Неверное поле «push." + k + "»", 2, { field: "push." + k });
      });
      if (b.flap_summary !== undefined && typeof b.flap_summary !== "boolean") throw httpErr(400, "Неверное поле «flap_summary»", 2, { field: "flap_summary" });
      if (b.push) Object.keys(b.push).forEach(function (k) { db.notify.push[k] = b.push[k]; });
      if (typeof b.flap_summary === "boolean") db.notify.flap_summary = b.flap_summary;
      bump(); return { ok: true, push: clone(db.notify.push), flap_summary: db.notify.flap_summary, ver: ver12(db.notify) };
    }
    return undefined;
  }
  // пояса «базы сервера» заглушки — пояса браузера (Chrome: Intl.supportedValuesOf), иначе короткий список; сдвиг — по Intl
  function mockZones() {
    var z = [];
    try { z = Intl.supportedValuesOf("timeZone").slice(); } catch (e) { z = ["Asia/Almaty", "Asia/Tbilisi", "Europe/Berlin", "Europe/Kaliningrad", "Europe/Moscow", "Europe/Riga", "Etc/UTC"]; }
    if (z.indexOf("Etc/UTC") < 0) z.push("Etc/UTC");
    if (z.indexOf("UTC") < 0) z.push("UTC");
    return z.sort();
  }
  function mockOffset(zone, ts) {
    try {
      var p = new Intl.DateTimeFormat("en-US", { timeZone: zone, timeZoneName: "longOffset" }).formatToParts(new Date(ts * 1000)).filter(function (x) { return x.type === "timeZoneName"; })[0];
      var m = /GMT(?:([+\-\u2212])(\d{1,2})(?::?(\d{2}))?)?$/.exec(p ? p.value : "");
      return m && m[1] ? (m[1] === "+" ? 1 : -1) * (Number(m[2]) * 3600 + Number(m[3] || 0) * 60) : 0;
    } catch (e) { return 0; }
  }
  function isPolicyPath(path) {
    return path.indexOf("/api/gw/policy") === 0 || path.indexOf("/api/gw/groups") === 0 || path === "/api/notify" || path === "/api/settings/time" || path === "/api/gw/activity/settings" || path === "/api/gw/probes/speed" ||
      /^\/api\/gw\/exits\/e-[0-9a-f]{10}\/(move|manual|enabled|country|speedtest|history|transitions)$/.test(path);
  }

  // ── №13 r2 (V1): «Настройки протокола» (F1–F4), безопасное применение (F7), «Сеть шлюза» (F6) — как ops.* и api.py ──────
  /* Параметр адреса ps=<сцены через запятую>:
       wait     пометки «ждут новых настроек»: WireGuard — жёсткая у «Телефона Мамы» (порт), AmneziaWG 2.0 — мягкая у
                «Роутера на даче» (MTU)
       pend     идёт ожидание подтверждения WireGuard (осталось 1:43)
       hit      администратор подключён через WireGuard (адрес «Телефона Мамы»): смена порта или сети шлюза его задевает
                (admin_hit, подтверждение с «Скачать мои новые настройки», откат через 2 мин)
       old      ядро без добавок 4–5 договора pending: в плане нет hit_text, reissue_text, with_text (запасные слова панели)
       nodev    «Скачать мои новые настройки»: по адресу сессии устройство не найти (400 — выбор из списка)
       last     у WireGuard есть «последнее применение» (порт 51820 → 51821, «Вернуть прежние»)
       closed   у WireGuard «настройки выдали больше 10 мин назад, а подключений нет» (Е1)
       undobusy «Вернуть прежние» WireGuard: прежний порт занят (Е9)
       stale    первая запись отвечает 409 object_changed (Е10)
       mtuoff   у AmneziaWG 2.0 S4 70: предел ниже 1280, поле MTU недоступно (Е7)
       netlast  у сети шлюза есть прошлая смена (10.78 → 10.77, «Вернуть прежнюю сеть»)
       afterfail  запись сделана, шаг после неё не прошёл: ответ ok с after_failed: true и текстом (добавка 6, П1)
       netbusy  «Вернуть прежнюю сеть»: ждёт подтверждения другой протокол (409 pending_busy, добавка 6)
       netodd   «Вернуть прежнюю сеть»: прежняя сеть не частная (400 why=rfc1918, добавка 6)
       netnolast  смена сети: «последнее применение» не записано — в ответе строка «"Вернуть прежнюю сеть" … не будет»
                (С-1 REV-3315-view; как ядро: warnings = план + запись, plan в ответе)
       netreplan  смена сети: предпросмотр без подтверждения, а запись — 409 need=yes с новым планом (З-2 REV-3315-view)
       front    VLESS на порт 443: 443 занят сайтом — переход перед сайтом (front, front_note), откат недоступен (Е11)  */
  var PSQ = (Q.get("ps") || "").split(",");
  var psOn = function (k) { return PSQ.indexOf(k) >= 0; };
  var PS_TLS_TEXT = { "1.2-1.3": ["1.2 и 1.3", "1.2 and 1.3"], "1.3": ["только 1.3", "1.3 only"], "1.2": ["только 1.2", "1.2 only"] };
  var PS_RESTART = { wg: { port: "syncconf", mtu: "live" }, awg1: { port: "awg", mtu: "live" }, awg2: { port: "awg", mtu: "live" }, awg3: { port: "awg", mtu: "live" },
    openvpn: { port: "ovpn" }, ocserv: { port: "ocserv", tls: "ocserv" }, sstp: { port: "sstp", tls: "sstp" }, vless: { port: "xray", tls: "xray" }, socks5: { port: "xray" }, hy2: { port: "xray" } };
  // слова — как у ядра (gw_ops.settings.restart.*): строка начинается с поля, «Порт и TLS» — одна перезагрузка (Д1, Н1)
  var PS_RESTART_T = {
    syncconf: ["{w}: без перезапуска — новый порт подхватывается на ходу.", "{w}: no restart — the new port is taken on the fly."],
    live: ["{w}: меняется на ходу, без перезапуска.", "{w}: changes on the fly, no restart."],
    awg: ["{w}: перезапуск на 15–25 с; устройства со старыми настройками не подключатся, пока не получат новые.", "{w}: restart for 15–25 s; devices with the old settings won’t connect until they get the new ones."],
    ovpn: ["{w}: перезапуск экземпляра OpenVPN, у которого сменился порт; устройства со старыми настройками к нему не подключатся, пока не получат новые.", "{w}: the OpenVPN instance whose port changed restarts; devices with the old settings won’t connect to it until they get the new ones."],
    ocserv: ["{w}: перезапуск службы OpenConnect — прервутся все её подключения.", "{w}: the OpenConnect service restarts — all its connections drop."],
    sstp: ["{w}: перезапуск accel-ppp — на несколько секунд прервутся и подключения L2TP и PPTP.", "{w}: accel-ppp restarts — L2TP and PPTP connections drop for a few seconds too."],
    xray: ["{w}: перезапуск xray шлюза на несколько секунд — на это время прервутся и выходы через xray, и DNS шлюза.", "{w}: the gateway xray restarts for a few seconds — exits through xray and the gateway DNS drop for that time too."]
  };
  var PS_FIELD_T = { port: ["Порт", "Port"], mtu: ["MTU", "MTU"], tls: ["TLS", "TLS"] };
  var PS_S4 = { wg: 0, awg1: 0, awg2: 30, awg3: 0 };
  function psDb() {
    if (db.ps) return db.ps;
    var p = db.ps = { ver: {}, mtu: {}, tls: { vless: "1.2-1.3", sstp: "1.2-1.3", ocserv: "1.2-1.3" }, last: {}, marks: {}, pending: null, net: { last: null, ver: 1 }, staleOnce: psOn("stale") };
    if (psOn("mtuoff")) PS_S4.awg2 = 70;
    Object.keys(PS_S4).forEach(function (k) { p.mtu[k] = psLimit(k) >= 1280 ? psLimit(k) : 1260; });
    if (psOn("wait")) {
      p.marks.wg = [{ id: "d-7ae67ae0", what: ["port"], hard: true }];
      p.marks.awg2 = [{ id: "d-b1c2d3e4", what: ["mtu"], hard: false }];
    }
    if (psOn("last") || psOn("closed") || psOn("undobusy")) {
      db.inbounds.wg.ports = ["51821/udp"];
      p.last.wg = { id: "a-1a2b3c4d", at: nowS() - 360, old: { port: 51820 }, new: { port: 51821 } };
    }
    if (psOn("pend")) p.pending = { scope: "wg", token: "0123456789abcdef", deadline: nowS() + 103, via: "panel" };
    if (psOn("netlast")) p.net.last = { old: "10.78.0.0/16", new: "10.77.0.0/16", at: nowS() - 600, id: "a-9f8e7d6c" };
    return p;
  }
  var psT = function (k) { var x = (INBOUND_DEFS.filter(function (d) { return d[0] === k; })[0] || [k, k])[1]; return k === "vless" ? x + (VLESS_MODE === "tls" ? " · TLS" : " · Reality") : x; };
  function psLimit(k) { return 1400 - 80 - (PS_S4[k] || 0); }
  function psPortOf(k) {
    var r = db.inbounds[k], p = r && r.ports && r.ports[0] ? parseInt(r.ports[0], 10) : 0;
    if (!p && k === "sstp") p = 4433;
    if (!p && k === "ocserv") p = 4443;
    if (!p && k === "openvpn") p = 1194;
    return p || null;
  }
  function psProto(k) { var r = db.inbounds[k], m = r && r.ports && r.ports[0] ? /\/(udp|tcp)/.exec(r.ports[0]) : null; return m ? m[1] : (/^(sstp|vless|socks5)$/.test(k) ? "tcp" : k === "ocserv" ? "tcp+udp" : "udp"); }
  function psTlsOn(k) { return k === "sstp" || k === "ocserv" || (k === "vless" && VLESS_MODE === "tls"); }
  function psDevs(k) { return db.devices.filter(function (d) { return d.access.indexOf(k) >= 0; }); }
  function psRow(d) { var u = find(db.users, d.user); return { id: d.id, name: d.name, platform: d.platform, user: d.user, user_name: u ? u.name : "" }; }
  function psWho(rows) { var n = rows.map(function (x) { return x.user_name ? x.name + " (" + x.user_name + ")" : x.name; }); return n.slice(0, 10).join(", ") + (n.length > 10 ? ML(" и ещё ", " and ") + (n.length - 10) + ML("", " more") : ""); }
  function psVer(k) { var p = psDb(); return "mockver-" + k + "-" + (p.ver[k] || 1); }
  function psPendView() {
    var p = psDb().pending;
    if (!p) return null;
    var left = Math.max(0, p.deadline - nowS());
    if (!left) { psRevert("timer"); return null; }
    return { scope: p.scope, left: left, deadline: p.deadline, token: p.token, via: p.via };
  }
  // откат по таймеру (pending.expire): прежние настройки, событие settings_reverted в ленту
  function psRevert(why) {
    var p = psDb(), pd = p.pending;
    if (!pd) return;
    p.pending = null;
    if (pd.scope === "base") { if (pd.prev) { db.gw.net = pd.prev; p.net.ver += 1; } }
    else if (pd.prev) { var r = db.inbounds[pd.scope]; r.ports = pd.prev.ports; p.mtu[pd.scope] = pd.prev.mtu; p.tls[pd.scope] = pd.prev.tls; p.marks[pd.scope] = pd.prev.marks; delete p.last[pd.scope]; p.ver[pd.scope] = (p.ver[pd.scope] || 1) + 1; }
    var ev = { id: Date.now(), kind: "settings_reverted", ts: nowS(), args: { scope: pd.scope, why: why },
      text: pd.scope === "base" ? ML("Сеть шлюза возвращена: подтверждения за 2 мин не было", "The gateway network is back: no confirmation within 2 min") : ML("Настройки " + psT(pd.scope) + " возвращены: подтверждения за 2 мин не было", "The " + psT(pd.scope) + " settings are back: no confirmation within 2 min") };
    db.events.unshift(ev);
    if (typeof Sse !== "undefined") Sse.emit("alert", ev);
    bump();
  }
  function psMtuView(k) {
    var lim = psLimit(k), s4 = PS_S4[k] || 0, open = lim >= 1280, cur = psDb().mtu[k];
    return { cur: cur, min: 1280, limit: lim, open: open, server: Math.min(cur + s4, 1500 - 80), nic: 1500, budget: 1400, overhead: 80, s4: s4,
      why: open ? "" : ML(psT(k) + ": MTU вручную сейчас не поменять — при MTU сетевого интерфейса 1500 и S4 = " + s4 + " пакет с MTU 1280 снаружи уже больше 1400 байт. Сейчас MTU " + cur + ".",
        psT(k) + ": the MTU can’t be set by hand now — with the network interface MTU 1500 and S4 = " + s4 + " a packet with MTU 1280 is already over 1400 bytes outside. The MTU now is " + cur + ".") };
  }
  function psTlsView(k) {
    var ch = k === "vless" ? ["1.2-1.3", "1.3"] : ["1.2-1.3", "1.3", "1.2"];
    return { cur: psDb().tls[k] || "1.2-1.3", choices: ch.map(function (v) { return { value: v, text: ML(PS_TLS_TEXT[v][0], PS_TLS_TEXT[v][1]), hint: v === "1.2" ? ML("если хостер или сеть ломают TLS 1.3", "if the hoster or the network breaks TLS 1.3") : "" }; }),
      note: k === "vless" ? ML("Подключения VLESS и так идут по TLS 1.3; «только 1.3» закрывает TLS 1.2 для сайта за VLESS.", "VLESS connections use TLS 1.3 anyway; “1.3 only” closes TLS 1.2 for the site behind VLESS.") : "" };
  }
  function psRestart(k, fields) {
    var rows = [], by = {};
    fields.forEach(function (f) { var w = (PS_RESTART[k] || {})[f]; if (!w) return; if (by[w]) by[w].push(f); else { by[w] = [f]; rows.push({ what: f, word: w }); } });
    rows.forEach(function (r) {
      var names = by[r.word].map(function (f) { return ML(PS_FIELD_T[f][0], PS_FIELD_T[f][1]); });
      var what = names.length === 1 ? names[0] : names.slice(0, -1).join(", ") + ML(" и ", " and ") + names[names.length - 1];
      r.text = ML(PS_RESTART_T[r.word][0], PS_RESTART_T[r.word][1]).replace("{w}", what);
    });
    var loud = rows.some(function (r) { return r.word !== "syncconf" && r.word !== "live"; });
    var withs = loud && k === "sstp" ? ["l2tp", "pptp"].filter(function (x) { return db.inbounds[x] && db.inbounds[x].installed; }).map(function (x) { return { kind: x, title: psT(x) }; }) : [];
    return { rows: rows, with: withs, gateway: loud && /^(vless|socks5|hy2)$/.test(k) };
  }
  function psMarksView(k) {
    var out = { title: psT(k), hard: [], soft: [] };
    (psDb().marks[k] || []).forEach(function (x) { var d = find(db.devices, x.id); if (d) out[x.hard ? "hard" : "soft"].push({ id: d.id, name: d.name, what: x.what }); });
    return out;
  }
  function psChangedText(c) {
    var out = [];
    if (c.port) out.push(ML("порт ", "port ") + c.port.old + " → " + c.port.new);
    if (c.mtu) out.push("MTU " + c.mtu.old + " → " + c.mtu.new);
    if (c.tls) out.push("TLS " + ML(PS_TLS_TEXT[c.tls.old][0], PS_TLS_TEXT[c.tls.old][1]) + " → " + ML(PS_TLS_TEXT[c.tls.new][0], PS_TLS_TEXT[c.tls.new][1]));
    return out.join(", ");
  }
  function psView(k) {
    var r = db.inbounds[k], p = psDb();
    if (!r || !r.installed) throw httpErr(400, ML(psT(k) + " не установлен.", psT(k) + " is not installed."), 1);
    var fixed = ["l2tp", "pptp", "ikev2"].indexOf(k) >= 0, mtu = PS_S4[k] !== undefined, tls = psTlsOn(k);
    var fields = [].concat(fixed ? [] : ["port"], mtu ? ["mtu"] : [], tls ? ["tls"] : []);
    var last = p.last[k];
    var lastView = last ? (function () { var ch = {}; Object.keys(last.old).forEach(function (f) { ch[f] = { old: last.old[f], new: last.new[f] }; }); return { at: last.at, id: last.id, changed: ch, text: psChangedText(ch) }; })() : null;
    return { ok: true, kind: k, title: psT(k), port: { kind: k, fixed: fixed, protos: fixed ? [] : psProto(k).split("+"), port: fixed ? null : psPortOf(k), recommended: [], kept: null, kept_busy: "", check: null },
      mtu: mtu ? psMtuView(k) : null, tls: tls ? psTlsView(k) : null, restart: psRestart(k, fields), devices: psDevs(k).map(function (d) { return { id: d.id, name: d.name }; }),
      reissue: psMarksView(k), last: lastView, closed: k === "wg" && psOn("closed"), pending: psPendView(), ver: psVer(k) };
  }
  var PS_FORM = ["port", "proto", "mtu", "tls", "dry", "yes", "force", "safe", "ver"];
  function psPlan(k, req) {
    var r = db.inbounds[k], changed = {}, warnings = [];
    if (req.port !== undefined) changed.port = { old: psPortOf(k), new: req.port, field: req.proto === "tcp" ? "tcp_port" : "port", protos: [req.proto || psProto(k)] };
    if (req.mtu !== undefined) changed.mtu = { old: psDb().mtu[k], new: req.mtu, server: req.mtu + (PS_S4[k] || 0) };
    if (req.tls !== undefined) changed.tls = { old: psDb().tls[k] || "1.2-1.3", new: req.tls };
    var devs = psDevs(k).map(psRow), hard = changed.port ? devs : [], soft = !changed.port && changed.mtu ? devs : [];
    var xray = /^(vless|socks5|hy2)$/.test(k) && !!(changed.port || changed.tls);
    if (xray) warnings.push(ML("Если вы открыли панель через VLESS, SOCKS5 или Hysteria2 этого сервера, связь может оборваться.", "If you opened the panel through VLESS, SOCKS5 or Hysteria2 of this server, the link may drop."));
    var tlsWarn = changed.tls && req.tls === "1.3" && k === "sstp";
    if (tlsWarn) warnings.push(ML("Windows 10 и MikroTik могут не подключиться: TLS 1.3 у них нет по умолчанию.", "Windows 10 and MikroTik may not connect: they have no TLS 1.3 by default."));
    var hit = psOn("hit") && k === "wg" && !!changed.port ? "pool" : null;
    var hitText = hit ? ML("Вы подключены через " + psT(k) + ": после применения связь может оборваться. Если за 2 мин не подтвердите — настройки вернутся сами.", "You are connected through " + psT(k) + ": the link may drop after the change. If you do not confirm within 2 min, the settings go back by themselves.") : "";
    if (hitText) warnings.push(hitText);
    var front = psOn("front") && k === "vless" && req.port === 443;              // ops._settings_parse: 443 занят сайтом
    if (front) warnings.push(ML("Порт 443 занят сайтом: VLESS встанет перед сайтом и перейдёт на TLS со своим доменом — ссылки у всех устройств сменятся. «Вернуть прежние» для этого перехода не будет.",
      "Port 443 is the site’s: VLESS will stand in front of the site and move to TLS with its own domain — the links of every device change. The previous settings can’t be returned after this switch."));
    var restart = psRestart(k, Object.keys(changed));
    if (k === "sstp" && (changed.port || changed.tls)) { var n = 3; warnings.push(ML("Оборвутся " + n + " подключения (SSTP, L2TP, PPTP).", n + " connections will drop (SSTP, L2TP, PPTP).")); }
    var plan = { kind: k, title: psT(k), changed: changed, restart: restart, reissue: { hard: hard, soft: soft }, warnings: warnings, via_xray: xray, admin_hit: hit, front: front,
      need_yes: hard.length > 0 || xray || !!hit || !!tlsWarn || (k === "sstp" && !!(changed.port || changed.tls)) };
    if (!psOn("old")) {
      plan.hit_text = hitText;
      plan.reissue_text = { hard: hard.length ? ML("Устройства с прежними настройками " + psT(k) + " не подключатся — выдайте им настройки заново: " + psWho(hard) + ".", "Devices with the previous " + psT(k) + " settings won’t connect — issue their settings again: " + psWho(hard) + ".") : "",
        soft: soft.length ? ML("Устройствам " + psT(k) + " желательно выдать настройки заново: " + psWho(soft) + ".", "It’s better to issue the " + psT(k) + " settings again: " + psWho(soft) + ".") : "" };
      plan.with_text = restart.with.length ? ML("Перезапуск задевает и: ", "The restart also affects: ") + restart.with.map(function (w) { return w.title; }).join(", ") + "." : "";
    }
    return plan;
  }
  function psReq(k, body) {
    Object.keys(body).forEach(function (f) { if (PS_FORM.indexOf(f) < 0) throw httpErr(400, ML("Поле «" + f + "» здесь не принимается.", "The field “" + f + "” is not accepted here."), 2); });
    var req = {};
    if (body.port !== undefined && body.port !== null) {
      var pn = Number(body.port);
      if (!Number.isInteger(pn) || pn < 1 || pn > 65535) throw httpErr(400, ML("«port» — номер порта, целое число от 1 до 65535.", "“port” is a port number, a whole number from 1 to 65535."), 2);
      var wantPr = body.proto ? [String(body.proto)] : psProto(k).split("+");
      var held = Object.keys(db.inbounds).filter(function (x) { return x !== k && db.inbounds[x].installed && (db.inbounds[x].ports || []).some(function (pp) { var pm = /\/(tcp|udp)$/.exec(pp); return parseInt(pp, 10) === pn && (!pm || wantPr.indexOf(pm[1]) >= 0); }); })[0];
      if (held && !body.force) throw httpErr(409, ML("Порт " + pn + "/" + psProto(k) + " занят: его занимает протокол шлюза " + psT(held) + ".", "Port " + pn + "/" + psProto(k) + " is taken: the gateway protocol " + psT(held) + " holds it."), 3, { conflicts: [{ kind: "inbound", detail: held }] });
      if (pn !== psPortOf(k)) { req.port = pn; if (body.proto) req.proto = body.proto; }
    }
    if (body.mtu !== undefined && body.mtu !== null) {
      var mv = psMtuView(k), n = body.mtu;
      if (PS_S4[k] === undefined) throw httpErr(400, ML(psT(k) + ": MTU вручную — только у WireGuard и AmneziaWG.", psT(k) + ": a manual MTU is only for WireGuard and AmneziaWG."), 2);
      if (!mv.open) throw httpErr(400, mv.why, 2);
      if (typeof n !== "number" || !Number.isInteger(n) || n < 1280) throw httpErr(400, ML(psT(k) + ": MTU — целое число от 1280 до " + mv.limit + ".", psT(k) + ": MTU must be a whole number from 1280 to " + mv.limit + "."), 2);
      if (n > mv.limit) throw httpErr(400, ML(psT(k) + ": MTU не выше " + mv.limit + " — иначе пакет снаружи больше 1400 байт, а в мобильных сетях и PPPoE на таких пакетах скорость падает.", psT(k) + ": MTU " + mv.limit + " at most — otherwise the outer packet is over 1400 bytes, and mobile networks and PPPoE slow down on such packets."), 2);
      if (n !== mv.cur) req.mtu = n;
    }
    if (body.tls !== undefined && body.tls !== null) {
      if (!psTlsOn(k)) throw httpErr(400, ML(psT(k) + ": версии TLS здесь не выбираются.", psT(k) + ": TLS versions are not chosen here."), 2);
      if (psTlsView(k).choices.map(function (c) { return c.value; }).indexOf(body.tls) < 0) throw httpErr(400, ML(psT(k) + ": версии TLS — одна из перечисленных.", psT(k) + ": TLS versions — one of the listed."), 2);
      if (body.tls !== psDb().tls[k]) req.tls = body.tls;
    }
    return req;
  }
  function psBusy(scope) {
    var pv = psPendView();
    if (pv) throw httpErr(409, ML("Сначала подтвердите или верните прежние настройки: " + (pv.scope === "base" ? "Сеть шлюза" : psT(pv.scope)) + " ждёт подтверждения ещё " + pv.left + " с.",
      "Confirm or restore the previous settings first: " + (pv.scope === "base" ? "the gateway network" : psT(pv.scope)) + " waits for a confirmation for " + pv.left + " s more."), "pending_busy", { pending: { scope: pv.scope, left: pv.left } });
  }
  function psArm(scope, prev) {
    var p = psDb();
    p.pending = { scope: scope, token: rid("", 16).replace(/[^0-9a-f]/g, "a").slice(0, 16), deadline: nowS() + 120, via: "panel", prev: prev };
    var tok = p.pending.token;
    setTimeout(function () { if (psDb().pending && psDb().pending.token === tok) psRevert("timer"); }, 120000);
    return { token: tok, deadline: p.pending.deadline, scope: scope, left: 120 };
  }
  function psRoute(method, path, body, query) {
    var m;
    psDb();                                     // сцены ps= — с первого же ответа (порт WireGuard у «last» — и в списке протоколов)
    if (method === "GET" && path === "/api/gw/reissue") { psDb(); var rv = {}; Object.keys(db.inbounds).forEach(function (k) { var v = psMarksView(k); if (v.hard.length || v.soft.length) rv[k] = v; }); return { ok: true, reissue: rv }; }
    if (method === "GET" && path === "/api/gw/pending") return { ok: true, pending: psPendView() };
    if (method === "POST" && path === "/api/gw/pending/confirm") {
      var pv = psPendView();
      if (!pv || body.token !== pv.token) throw httpErr(409, ML("Не успели: настройки уже возвращены — работают прежние.", "Too late: the settings were already reverted — the previous ones work."), "pending_gone");
      psDb().pending = null; bump();
      return { ok: true, scope: pv.scope, text: pv.scope === "base" ? ML("Новые настройки сети шлюза оставлены.", "The new gateway network is kept.") : ML("Новые настройки " + psT(pv.scope) + " оставлены.", "The new " + psT(pv.scope) + " settings are kept.") };
    }
    if ((m = /^\/api\/gw\/inbounds\/(\w+)\/settings$/.exec(path)) && method === "GET") return psView(m[1]);
    if ((m = /^\/api\/gw\/inbounds\/(\w+)\/settings$/.exec(path)) && method === "PUT") {
      var k = m[1], p = psDb(), r = db.inbounds[k];
      if (!r || !r.installed) throw httpErr(400, ML(psT(k) + " не установлен.", psT(k) + " is not installed."), 1);
      var dry = !!body.dry || (query && query.get("dry") === "1");
      if (!dry) {
        if (!body.ver) throw httpErr(400, ML("Обновите страницу: форма без версии настроек.", "Reload the page: the form has no settings version."), "ver_required");
        if (p.staleOnce) { p.staleOnce = false; p.ver[k] = (p.ver[k] || 1) + 1; }
        if (body.ver !== psVer(k)) throw httpErr(409, ML("Настройки уже изменили в другом окне — обновите окно и примените снова.", "The settings were changed in another window — refresh this one and apply again."), "object_changed", { ver: psVer(k) });
      }
      var req = psReq(k, body), plan = psPlan(k, req);
      var base = { ok: true, kind: k, field: "port", old: psPortOf(k), port: req.port || psPortOf(k), ver: psVer(k) };
      if (dry) return Object.assign(base, { dry: true, plan: plan }, plan);
      if (!Object.keys(plan.changed).length) return Object.assign(base, { plan: plan, changed: {}, reissue: [], text: ML(psT(k) + ": менять нечего — эти настройки уже стоят.", psT(k) + ": nothing to change — these settings are already set."), warnings: [], pending: null, id: null });
      if (plan.need_yes && !body.yes) throw httpErr(409, ML(psT(k) + ": нужно подтверждение.", psT(k) + ": a confirmation is needed."), 3, { need: "yes", plan: plan });
      if (plan.front && body.safe) throw httpErr(409, ML(psT(k) + ": на 443 рядом с сайтом VLESS переходит через смену режима — безопасное применение с откатом здесь недоступно. Примените без него или выберите другой порт.",
        psT(k) + ": on 443 next to the site VLESS moves by a switch of its mode — a safe apply with a rollback isn’t available here. Apply without it or pick another port."), 3, { why: "front" });
      psBusy(k);
      var prev = { ports: (r.ports || []).slice(), mtu: p.mtu[k], tls: p.tls[k], marks: p.marks[k] };
      var aid = "a-" + rid("", 8);
      if (req.port) { var pr = (req.proto || psProto(k).split("+")[0]); if (k === "openvpn") r.ports = (r.ports.length ? r.ports : ["1194/udp"]).map(function (x) { return /\/(\w+)$/.exec(x)[1] === pr ? req.port + "/" + pr : x; }); else r.ports = [req.port + "/" + psProto(k)].concat((r.ports || []).slice(1)); }
      if (req.mtu) p.mtu[k] = req.mtu;
      if (req.tls) p.tls[k] = req.tls;
      var rows = plan.reissue.hard.concat(plan.reissue.soft);
      if (rows.length) p.marks[k] = rows.map(function (x) { return { id: x.id, what: Object.keys(plan.changed).filter(function (f) { return f !== "tls"; }), hard: plan.reissue.hard.length > 0 }; });
      var old = {}, nw = {};
      Object.keys(plan.changed).forEach(function (f) { old[f] = plan.changed[f].old; nw[f] = plan.changed[f].new; });
      p.last[k] = { id: aid, at: nowS(), old: old, new: nw };
      p.ver[k] = (p.ver[k] || 1) + 1;
      var pend = body.safe || plan.admin_hit ? psArm(k, prev) : null;
      bump();
      if (psOn("afterfail")) return Object.assign(base, { changed: plan.changed, reissue: [], warnings: [], pending: pend, id: aid, after_failed: true,
        text: ML(psT(k) + ": настройки применены, но последний шаг не прошёл (OSError): кому выдать настройки заново, могло не отметиться. Проверьте устройства или нажмите «Вернуть прежние».",
          psT(k) + ": the settings are applied, but the last step failed (OSError): whom to give the settings again may be unmarked. Check the devices or return the previous settings.") });
      return Object.assign(base, { changed: plan.changed, reissue: rows, text: ML(psT(k) + ": применено — ", psT(k) + ": applied — ") + psChangedText(plan.changed) + ".", warnings: [], pending: pend, id: aid });
    }
    if ((m = /^\/api\/gw\/inbounds\/(\w+)\/settings\/undo$/.exec(path)) && method === "POST") {
      var k2 = m[1], p2 = psDb(), l = p2.last[k2], r2 = db.inbounds[k2];
      if (!l) throw httpErr(409, ML("Возвращать нечего: прошлого применения нет.", "Nothing to restore: there is no previous application."), 3);
      if (psOn("undobusy") && k2 === "wg") throw httpErr(409, ML("WireGuard: прежний порт " + l.old.port + " теперь занят: AmneziaWG 3.1. Выберите другой порт.", "WireGuard: the previous port " + l.old.port + " is now in use: AmneziaWG 3.1. Choose another port."), 3);
      var ch = {};
      Object.keys(l.old).forEach(function (f) { ch[f] = { old: l.new[f], new: l.old[f] }; });
      // П6: общая служба (accel-ppp у SSTP, xray шлюза у VLESS/SOCKS5/Hysteria2) — подтверждение, как у «Применить»
      var shared = k2 === "sstp" ? ML("перезапуск accel-ppp — на несколько секунд прервутся и подключения L2TP и PPTP.", "accel-ppp restarts — L2TP and PPTP connections drop for a few seconds too.")
        : (["vless", "socks5", "hy2"].indexOf(k2) >= 0 && (l.old.port || l.old.tls) ? ML("перезапуск xray шлюза на несколько секунд.", "the gateway xray restarts for a few seconds.") : "");
      if (shared && !body.yes && !(p2.pending && p2.pending.scope === k2)) {
        throw httpErr(409, ML(psT(k2) + ": вернуть прежние — нужно подтверждение.", psT(k2) + ": returning the previous settings needs a confirmation."), 3,
          { need: "yes", plan: { kind: k2, title: psT(k2), changed: ch, need_yes: true, lines: [shared] } });
      }
      if (l.old.port) r2.ports = [l.old.port + "/" + psProto(k2)].concat((r2.ports || []).slice(1));
      if (l.old.mtu) p2.mtu[k2] = l.old.mtu;
      if (l.old.tls) p2.tls[k2] = l.old.tls;
      delete p2.last[k2]; delete p2.marks[k2];
      if (p2.pending && p2.pending.scope === k2) p2.pending = null;
      p2.ver[k2] = (p2.ver[k2] || 1) + 1;
      bump();
      return { ok: true, kind: k2, changed: ch, text: ML(psT(k2) + ": прежние настройки возвращены — ", psT(k2) + ": the previous settings are back — ") + psChangedText(ch) + ".", warnings: [] };
    }
    if ((m = /^\/api\/gw\/inbounds\/(\w+)\/settings\/issue$/.exec(path)) && method === "POST") {
      var d = body.device ? find(db.devices, body.device) : (psOn("hit") && !psOn("nodev") && m[1] === "wg" ? find(db.devices, "d-7ae67ae0") : null);
      if (!d) throw httpErr(400, ML("Выберите устройство: по адресу подключения его не найти.", "Pick a device: it cannot be found by the address of the connection."), 2);
      if (d.access.indexOf(m[1]) < 0) throw httpErr(400, ML("Устройству не выдан " + psT(m[1]) + ".", psT(m[1]) + " is not granted to the device."), 2);
      return Object.assign(issueDoc(d, { inbound: m[1] }), { preview: true, device: d.id, name: d.name });
    }
    if (method === "GET" && path === "/api/gw/net") {
      var pn2 = psDb(), cur = db.gw.net.base;
      var cands = ["10.77.0.0/16", "10.78.0.0/16", "10.79.0.0/16", "172.27.0.0/16"].map(function (c) { var busy = c === "10.78.0.0/16"; return { base: c, current: c === cur, free: c !== cur && !busy, why: busy ? ["Docker"] : [] }; });
      return { ok: true, base: cur, dns: db.gw.net.dns, default: "10.77.0.0/16", candidates: cands, taken: [{ net: "10.78.0.0/16", why: "Docker" }], suggest_base: cur === "10.79.0.0/16" ? "172.27.0.0/16" : "10.79.0.0/16",
        last: pn2.net.last, pending: psPendView(), ver: "mocknet-" + pn2.net.ver, warnings: [] };
    }
    if (method === "PUT" && path === "/api/gw/net/base") {
      var pn3 = psDb(), nb = String(body.base || "").trim(), mm = /^(10|172|192)\.(\d{1,3})\.0\.0\/16$/.exec(nb);
      Object.keys(body).forEach(function (f) { if (["base", "ver", "dry", "yes"].indexOf(f) < 0) throw httpErr(400, ML("Поле «" + f + "» здесь не принимается.", "The field “" + f + "” is not accepted here."), 2); });
      if (!mm || (mm[1] === "10" && +mm[2] > 255) || (mm[1] === "172" && (+mm[2] < 16 || +mm[2] > 31)) || (mm[1] === "192" && mm[2] !== "168")) throw httpErr(400, ML("Сеть шлюза — частная /16: 10.X.0.0/16, 172.16–31.0.0/16 или 192.168.0.0/16.", "The gateway network is a private /16: 10.X.0.0/16, 172.16–31.0.0/16 or 192.168.0.0/16."), 2, { field: "base" });
      if (!body.dry) { if (!body.ver) throw httpErr(400, "ver", "ver_required"); if (body.ver !== "mocknet-" + pn3.net.ver) throw httpErr(409, ML("Сеть шлюза уже изменили — обновите окно.", "The gateway network was changed — refresh the window."), "object_changed"); }
      var old2 = db.gw.net.base;
      if (nb === old2) throw httpErr(400, ML("Сеть шлюза уже " + nb + " — менять нечего.", "The gateway network is " + nb + " already — nothing to change."), 2);
      if (nb === "10.78.0.0/16") throw httpErr(409, ML("Сеть " + nb + " занята: Docker. Свободна, например, сеть 10.79.0.0/16.", "The network " + nb + " is taken: Docker. Free is, for example, 10.79.0.0/16."), 3, { why: "busy", base: nb, taken: ["Docker"], suggest_base: "10.79.0.0/16" });
      var rs = {}, lines = [];
      ["wg", "awg1", "awg2", "awg3", "sstp", "l2tp", "pptp"].forEach(function (k3) { if (db.inbounds[k3] && db.inbounds[k3].installed) { var dv = psDevs(k3).filter(function (d3) { return Object.keys(d3.ips || {}).length; }).map(psRow); if (dv.length) rs[k3] = dv; } });
      Object.keys(rs).forEach(function (k3) { lines.push(["sstp", "l2tp", "pptp"].indexOf(k3) >= 0 ? ML("MikroTik на " + psT(k3) + ": DNS через туннель пропадёт до новой выдачи — " + psWho(rs[k3]) + ".", "MikroTik on " + psT(k3) + ": DNS through the tunnel is gone until reissued — " + psWho(rs[k3]) + ".")
        : ML(psT(k3) + " не подключится до новой выдачи (сменятся адреса и DNS): " + psWho(rs[k3]) + ".", psT(k3) + " won’t connect until reissued (addresses and DNS change): " + psWho(rs[k3]) + ".")); });
      lines.push(ML("Остальные переподключатся сами: OpenVPN, IKEv2.", "The rest reconnect on their own: OpenVPN, IKEv2."));
      var w2 = [];
      if (nb === "192.168.0.0/16") w2.push(ML("192.168.0.0/16 совпадёт с домашними сетями многих клиентов: у них дома может пропасть своя сеть или туннель.", "192.168.0.0/16 will match many clients’ home networks: their own network or the tunnel may be gone at home."));
      if (nb === "10.0.0.0/16") w2.push(ML("Подсети шлюза 10.0.1.0/24 совпадают с частыми домашними сетями — у клиента с такой сетью дома туннель может не заработать.", "The gateway subnets 10.0.1.0/24 match common home networks — the tunnel may not work for a client with such a home network."));
      var hit2 = psOn("hit") ? "base" : null, ht = hit2 ? ML("Вы подключены через сеть шлюза: после применения связь может оборваться. Если за 2 мин не подтвердите — настройки вернутся сами.", "You are connected through the gateway network: the link may drop after the change. If you do not confirm within 2 min, the settings go back by themselves.") : "";
      if (ht) w2.push(ht);
      var dns2 = nb.replace(/\.0\.0\/16$/, ".0.1");
      var plan2 = { scope: "base", title: ML("Сеть шлюза", "Gateway network"), changed: { base: { old: old2, new: nb } }, dns: { old: db.gw.net.dns, new: dns2 }, reissue: rs, forwards: [], lines: lines, warnings: w2, admin_hit: hit2, need_yes: Object.keys(rs).length > 0 || !!hit2 || nb === "192.168.0.0/16" };
      if (!psOn("old")) plan2.hit_text = ht;
      if (body.dry && psOn("netreplan")) { var pr0 = Object.assign({}, plan2, { reissue: {}, lines: lines.slice(-1), need_yes: false });
        return { ok: true, dry: true, plan: pr0, scope: "base", changed: plan2.changed, lines: pr0.lines, warnings: w2, need_yes: false }; }
      if (body.dry) return { ok: true, dry: true, plan: plan2, scope: "base", changed: plan2.changed, lines: lines, warnings: w2, need_yes: plan2.need_yes };
      if (plan2.need_yes && !body.yes) throw httpErr(409, ML("Сеть шлюза " + old2 + " → " + nb + ": нужно подтверждение.", "Gateway network " + old2 + " → " + nb + ": a confirmation is needed."), 3, { need: "yes", plan: plan2 });
      psBusy("base");
      var prev2 = clone(db.gw.net);
      db.gw.net = { base: nb, dns: dns2 };
      Object.keys(rs).forEach(function (k3) { pn3.marks[k3] = rs[k3].map(function (x) { return { id: x.id, what: ["net"], hard: ["sstp", "l2tp", "pptp"].indexOf(k3) < 0 }; }); });
      var nol = psOn("netnolast") && !hit2;
      pn3.net.last = nol ? null : { old: old2, new: nb, at: nowS(), id: "a-" + rid("", 8) };
      pn3.net.ver += 1;
      var pend2 = hit2 ? psArm("base", prev2) : null;
      bump();
      return { ok: true, changed: plan2.changed, text: ML("Сеть шлюза сменена: " + old2 + " → " + nb + ".", "The gateway network changed: " + old2 + " → " + nb + "."), pending: pend2, plan: plan2,
        warnings: w2.concat(nol ? [ML("Не записано «последнее применение» (/var/lib/kaskad/settings-last.json: нет места) — «Вернуть прежнюю сеть» для этой смены не будет.",
          "The record of the last change wasn’t written (/var/lib/kaskad/settings-last.json: no space) — “Restore the previous network” won’t be there for this change.")] : []) };
    }
    if (method === "POST" && path === "/api/gw/net/undo") {
      var pn4 = psDb(), ls = pn4.net.last;
      if (!ls) throw httpErr(409, ML("Возвращать нечего: прошлой смены сети шлюза нет.", "Nothing to restore: there was no previous change of the gateway network."), 3);
      if (psOn("netbusy")) throw httpErr(409, ML("Сначала подтвердите или верните прежние настройки: WireGuard ждёт подтверждения ещё 97 с.", "Confirm or restore the previous settings first: WireGuard waits for a confirmation for 97 s more."), "pending_busy", { pending: { scope: "wg", left: 97 } });
      if (psOn("netodd")) throw httpErr(400, ML("Прежнюю сеть " + ls.old + " вернуть нельзя: она не из частных диапазонов 10/8, 172.16/12, 192.168/16.", "The previous network " + ls.old + " can’t be restored: it is not in the private ranges 10/8, 172.16/12, 192.168/16."), 2, { why: "rfc1918", base: ls.old });
      db.gw.net = { base: ls.old, dns: ls.old.replace(/\.0\.0\/16$/, ".0.1") };
      pn4.net.last = null; pn4.net.ver += 1;
      if (pn4.pending && pn4.pending.scope === "base") pn4.pending = null;
      bump();
      return { ok: true, text: ML("Сеть шлюза возвращена: " + ls.new + " → " + ls.old + ".", "The gateway network is back: " + ls.new + " → " + ls.old + "."), warnings: [] };
    }
    return undefined;
  }
  function route(method, path, body, query) {
    var m;
    var psr = psRoute(method, path, body, query);                 // №13 r2: настройки протокола, ожидание, сеть шлюза
    if (psr !== undefined) return psr;
    if (method !== "GET" && FAIL && path !== "/api/login") throw httpErr(500, "Не удалось применить изменения: nft вернул ошибку (пример текста сервера).", 1);
    if (method === "GET" && path === "/api/overview") return overviewDoc();
    if (isPolicyPath(path)) { var pr = policyRoute(method, path, body, query || new URLSearchParams()); if (pr !== undefined) return pr; }
    if (method === "GET" && path === "/api/host/health") return t1Limits(hostHealthAnswer());
    if (method === "GET" && path === "/api/host/history") return hostHistDoc((query && query.get("range")) || "1d");   // круг 1 Т1: трафик и здоровье за сутки
    if (method === "GET" && path === "/api/host/synth") return synthDoc();                                // круг 1 Т1: балл 0–1000
    if (method === "GET" && path === "/api/host/score") return scoreMockDoc();                           // «Сейчас» и полоса 30 дней
    if (method === "GET" && path === "/api/host/bench/schedule") return benchSchedDoc();                 // «Настройки › Проверка сервера»
    if (method === "GET" && path === "/api/host/bench") return { ok: true, runs: [], more: false };
    if (method === "GET" && path === "/api/gw/exits/hour") return exitHourDoc();                         // круг 1 Т1: «Выходы · час»
    if (method === "GET" && path === "/api/gw/activity") return activityDoc();                           // UI-B: «кардиограмма» (SPEC-activity §5.1)
    if (method === "DELETE" && path === "/api/gw/activity") return actWipe(!!query && query.get("dry") === "1");   // №41
    if (method === "GET" && path === "/api/gw/exits/activity") return exitsActDoc();                    // 24.10: линия «Выходов» одним ответом
    if (method === "GET" && /^\/api\/gw\/(devices|users)\/[ud]-[0-9a-f]{8}\/activity$/.test(path)) return actRoute(path, query || new URLSearchParams());
    if (path === "/api/site" || path.indexOf("/api/site/") === 0) return siteRoute(method, path, body, query || new URLSearchParams());       // домен и сайт — блок выше
    if (method === "GET" && path === "/api/forwards" && LEGACY) return { ok: true, engine: "legacy", managed: false, forwards: [], live: { ts: nowS(), age: 1.2, fresh: true } };
    if (method === "GET" && path === "/api/forwards") return { ok: true, engine: "nft", managed: true, forwards: db.forwards.map(function (f) { return { proto: f.proto, port: f.port, to: f.to, to_port: f.to_port, name: f.name, origin: f.origin, added: f.added, packets: f._c ? f._c.packets : null, bytes: f._c ? f._c.bytes : null }; }), live: { ts: nowS(), age: 1.2, fresh: true } };
    if (method === "POST" && path === "/api/forwards" && LEGACY) throw httpErr(409, "Пробросы ведёт старый движок (iptables); эта команда — для движка nftables.", 5);
    if (method === "POST" && path === "/api/forwards") {
      if (!db.gw.enabled && false) throw httpErr(409, "x", 3);
      if (!body.force && (body.port === 22 || body.port === 51820)) throw httpErr(409, "Порт " + body.port + "/" + body.proto + " занят: его слушает служба sshd (пример конфликта).", 3, { kind: "listener", conflicts: [{ kind: "listener", detail: "sshd" }] });
      var f = { proto: body.proto, port: body.port, to: body.to, to_port: body.to_port, name: body.name || "", origin: "k3", added: new Date().toISOString() };
      db.forwards = db.forwards.filter(function (x) { return !(x.proto === f.proto && x.port === f.port); }).concat([f]).sort(function (a, b) { return a.port - b.port; });
      bump();
      return { ok: true, forward: f, replaced: null, apply: { warnings: [] } };
    }
    if (method === "DELETE" && (m = /^\/api\/forwards\/(tcp|udp)\/(\d+)$/.exec(path))) {
      db.forwards = db.forwards.filter(function (x) { return !(x.proto === m[1] && String(x.port) === m[2]); });
      bump();
      return { ok: true };
    }
    if (method === "GET" && path === "/api/gw") return gwDoc();
    if (method === "POST" && path === "/api/gw/enable" && LEGACY) throw httpErr(409, "Шлюз работает только с новым движком пробросов (nftables). Сначала переведите пробросы: меню «Движок и перенос».", 5);
    if (method === "POST" && path === "/api/gw/enable") {
      db.gw.enabled = true; db.gw.host = db.gw.host || SRV;
      // как ops.enable: gateway.enabled_at — сборщик делает первый круг, страница ждёт спокойно («Сборщик запускается…»)
      var keepStale = db.stale;
      db.stale = true; db.startingUntil = Date.now() + 8000;
      if (Sse.clients.length) Sse.tick();
      setTimeout(function () { db.startingUntil = 0; if (!keepStale) collector(true); }, 8000);
      INBOUND_DEFS.forEach(function (x) { var r = db.inbounds[x[0]]; r.running = r.installed && r.enabled; r.warnings = []; });
      bump(); return { ok: true, base: "10.77.0.0/16", host: db.gw.host, warnings: [] };
    }
    if (method === "POST" && path === "/api/gw/disable") {
      db.gw.enabled = false;
      INBOUND_DEFS.forEach(function (x) { var r = db.inbounds[x[0]]; r.running = false; r.sessions = 0; r.warnings = []; });
      db.devices.forEach(function (d) { if (d._live) d._live.online = false; });
      bump(); return { ok: true };
    }
    // mip=1: IPv4 для клиентов — только адрес сервера (api._known_host; тексты — gw_addresses.err.not_here_panel)
    if (method === "PUT" && path === "/api/gw/host" && MIP && !(body.detect || body.to_ip) && /^\d{1,3}(\.\d{1,3}){3}$/.test(String(body.host || ""))
      && body.host !== db.gw.host && [SRV, MIP_NAT].indexOf(body.host) < 0) {
      throw httpErr(409, ML("Адреса " + body.host + " у этого сервера нет. Есть: " + SRV + ", " + MIP_NAT + ". Если это внешний адрес, который сервер сам не видит (облако с NAT), добавьте его в SSH-меню: 17 → 4 → «Другой адрес или имя» → «Да».",
        "This server has no address " + body.host + ". It has: " + SRV + ", " + MIP_NAT + ". If it’s an external address the server doesn’t see itself (a NAT cloud), add it in the SSH menu: 17 → 4 → “Another address or name” → “Yes”."),
        3, { reason: "address_not_here", ip: body.host, known: [SRV, MIP_NAT] });
    }
    if (method === "PUT" && path === "/api/gw/host") { var det = !!(body.detect || body.to_ip); db.gw.host = det ? SRV : body.host; db.gw.host_auto = det; bump(); return { ok: true, host: db.gw.host, auto: det }; }   // to_ip — «Вернуть на IP» (№39)
    if (method === "PUT" && path === "/api/gw/route") {
      var dryr = query && query.get("dry") === "1";
      needVer(body, ver12(db.gw.route));
      if (dryr) { var drr = dryAnswer(db.policy ? db.policyRev : null, { total: 0, by_group: {} }, []), rv = ver12(db.gw.route); return drr && drr.then ? drr.then(function (v) { v.ver = rv; return v; }) : Object.assign(drr, { ver: rv }); }
      db.gw.route = { via: body.via, fail: body.fail || keptFail("default") }; bump(); return { ok: true, route: db.gw.route, ver: ver12(db.gw.route), moves: { total: 0, by_group: {} }, warnings: [] };
    }
    if (method === "GET" && path === "/api/gw/inbounds") return { ok: true, inbounds: inboundRows(), live: liveInfo() };
    if ((m = /^\/api\/gw\/inbounds\/(\w+)\/(install|uninstall|enable|disable)$/.exec(path)) && method === "POST") {
      var r = db.inbounds[m[1]];
      if (!r) throw httpErr(400, "неизвестный вход", 2);
      var prev = null;
      if (m[2] === "install") {
        if (needsKey(m[1])) throw httpErr(409, r.title + " здесь не работает: нет ядра AmneziaWG и ключа GitHub, чтобы его скачать (меню 8 → 6)", 5, { needs_key: true, key: "none" });
        if (body.port === 22 && !body.force) throw httpErr(409, "Порт 22/udp занят: его слушает sshd. Взять всё равно — с --force.", 3, { conflicts: [{ kind: "listener", detail: "sshd" }] });
        r.installed = true; r.enabled = true; r.running = true;
        var def = INBOUND_DEFS.filter(function (x) { return x[0] === m[1]; })[0];
        r.ports = body.port ? [body.port + "/" + (def[3].length && /\/tcp$/.test(def[3][0]) ? "tcp" : "udp")] : (r._kept || (def[3].length ? def[3] : [String(20000 + Math.floor(Math.random() * 30000)) + "/udp"]));
        if (r._kept) {          // как ops.inbound_install: удалённый протокол вернулся — прежний порт или другой (порт 31999 — «другой»)
          var moved = r._kept.length && r.ports.length && r._kept[0] !== r.ports[0] ? ["порт " + r._kept[0].split("/")[0] + " → " + r.ports[0].split("/")[0]] : [];
          prev = { restored: !moved.length, changed: moved, text: "", devices: db.devices.filter(function (d) { return d.access.indexOf(m[1]) >= 0; }).map(function (d) {
            var u = db.users.filter(function (x) { return x.id === d.user; })[0];
            return { id: d.id, name: d.name, platform: d.platform, user: d.user, user_name: u ? u.name : "" };
          }) };
          delete r._kept;
        }
      } else if (m[2] === "uninstall") { r._kept = r.ports; r.installed = false; r.enabled = false; r.running = false; r.ports = []; }   // доступ устройств остаётся: вернётся с протоколом
      else { r.enabled = m[2] === "enable"; r.running = r.enabled; }
      bump();
      return { ok: true, kind: m[1], notes: m[2] === "install" && !prev ? ["порт выбран автоматически (пример заметки сервера)"] : [], previous: prev, apply: { warnings: [] } };
    }
    // GET gw/inbounds/<kind>/port (ops.port_offer, круг 1 №14): рекомендованные порты протокола, занятость, случайный.
    // Занято в макете: порты установленных протоколов, 22 (sshd) и 1194/udp (?mock=1&port1194=busy — ближайший 1195)
    if ((m = /^\/api\/gw\/inbounds\/(\w+)\/port$/.exec(path)) && method === "GET") {
      var REC = { wg: [51820], openvpn: [1194], sstp: [4433], ocserv: [4443], socks5: [1080], vless: [443, 8443], hy2: [443, 8443] };
      var ri = db.inbounds[m[1]];
      if (!ri) throw httpErr(400, "неизвестный вход", 2);
      if (["l2tp", "pptp", "ikev2"].indexOf(m[1]) >= 0) return { ok: true, kind: m[1], fixed: true, protos: [], port: null, recommended: [], kept: null, kept_busy: "", check: null };
      var held = {};
      // как ops.port_conflicts — по протоколу: VLESS 443/tcp и Hysteria2 443/udp друг другу не мешают (№13 r2, Е11)
      var ownPr = psProto(m[1]).split("+");
      Object.keys(db.inbounds).forEach(function (k) { if (k !== m[1] && db.inbounds[k].installed) (db.inbounds[k].ports || []).forEach(function (pp) { var pm = /\/(tcp|udp)$/.exec(pp); if (!pm || ownPr.indexOf(pm[1]) >= 0) held[parseInt(pp, 10)] = "его занимает протокол шлюза " + db.inbounds[k].title; }); });
      held[22] = "здесь слушает SSH-сервер (sshd) — протокол на этом порту отрежет SSH-доступ к серверу";
      if (Q.get("port1194") === "busy") held[1194] = "его слушает openvpn";
      var whyOf = function (p) { return held[p] || ""; };
      var recs = (REC[m[1]] || []).map(function (p) { return { port: p, free: !whyOf(p), why: whyOf(p) }; });
      var pick = function () { for (;;) { var p = 20000 + Math.floor(Math.random() * 40000); if (!whyOf(p)) return p; } };
      var out = { ok: true, kind: m[1], fixed: false, protos: [/^(sstp|vless)$/.test(m[1]) ? "tcp" : "udp"], port: null, recommended: recs, kept: null, kept_busy: "", check: null };
      var qp = query && query.get("port");
      if (qp) { if (!/^[0-9]{1,5}$/.test(qp) || +qp < 1 || +qp > 65535) throw httpErr(400, "«port» — номер порта, целое число от 1 до 65535.", 2); out.check = { port: +qp, free: !whyOf(+qp), why: whyOf(+qp) }; }
      if (query && query.get("random") === "1") { out.port = pick(); return out; }
      var kp = ri._kept && ri._kept.length ? parseInt(ri._kept[0], 10) : 0;
      if (kp) { out.kept = kp; if (!whyOf(kp)) { out.port = kp; return out; } out.kept_busy = whyOf(kp); }
      var fr = recs.filter(function (x) { return x.free; })[0];
      if (fr) out.port = fr.port;
      else if (recs.length) { var b = Math.max.apply(null, recs.map(function (x) { return x.port; })); for (var dd = 1; dd < 200 && !out.port; dd++) { if (!whyOf(b + dd)) out.port = b + dd; else if (!whyOf(b - dd)) out.port = b - dd; } }
      if (!out.port) out.port = pick();
      return out;
    }
    if ((m = /^\/api\/gw\/inbounds\/(\w+)\/port$/.exec(path)) && method === "PUT") {
      var r2 = db.inbounds[m[1]];
      r2.ports = [body.port + "/" + (body.proto || "udp")];
      bump();
      return { ok: true, kind: m[1], port: body.port };
    }
    if ((m = /^\/api\/gw\/inbounds\/(\w+)\/option$/.exec(path)) && method === "PUT") { db.inbounds[m[1]].options[body.key] = !!body.value; bump(); return { ok: true, kind: m[1], key: body.key, value: !!body.value }; }
    if (method === "GET" && path === "/api/gw/people") return peopleDoc();
    if (method === "POST" && path === "/api/gw/users") {
      var u = { id: rid("u-", 8), name: body.name, route: body.via ? { via: body.via, fail: body.fail || keptFail("user") } : null, enabled: true, added: new Date().toISOString() };
      if (db.users.some(function (x) { return x.name === u.name; })) throw httpErr(400, "Пользователь «" + u.name + "» уже есть.", 2);
      db.users.push(u); bump(); return { ok: true, user: u };
    }
    if ((m = /^\/api\/gw\/users\/(u-[0-9a-f]{8})$/.exec(path)) && method === "DELETE") {
      var cnt = db.devices.filter(function (d) { return d.user === m[1]; }).length;
      if (cnt && !body.force) throw httpErr(400, "У пользователя есть устройства (" + cnt + "): удалите их или повторите с --force.", 2);
      db.devices = db.devices.filter(function (d) { return d.user !== m[1]; }); db.users = db.users.filter(function (x) { return x.id !== m[1]; }); bump(); return { ok: true };
    }
    if ((m = /^\/api\/gw\/users\/(u-[0-9a-f]{8})\/enabled$/.exec(path)) && method === "PUT") {
      var pu = find(db.users, m[1]);
      if (!pu) throw httpErr(400, "Пользователя " + m[1] + " нет.", 1);
      if (typeof body.enabled !== "boolean") throw httpErr(400, "Поле «enabled» — true или false.", null);
      pu.enabled = body.enabled; bump(); return { ok: true, user: pu, warnings: [] };
    }
    if ((m = /^\/api\/gw\/users\/(u-[0-9a-f]{8})\/page$/.exec(path)) && (method === "GET" || method === "POST")) {
      var pg = find(db.users, m[1]);
      if (!pg) throw httpErr(404, "Пользователя " + m[1] + " нет", 1);
      if (method === "POST") {
        if (["enable", "rotate", "disable"].indexOf(body.action) < 0) throw httpErr(400, "действие — enable, rotate или disable", null);
        if (body.action === "disable") delete db.pages[pg.id];
        else if (body.action === "rotate" || !db.pages[pg.id]) db.pages[pg.id] = rid("", 43);         // выдуманный токен страницы
        bump();
      }
      return pageDoc(pg);
    }
    if ((m = /^\/api\/gw\/users\/(u-[0-9a-f]{8})\/sub$/.exec(path)) && (method === "GET" || method === "POST")) {
      var sb = find(db.users, m[1]);
      if (!sb) throw httpErr(404, "Пользователя " + m[1] + " нет", 1);
      if (method === "POST") {
        if (["enable", "rotate", "disable"].indexOf(body.action) < 0) throw httpErr(400, "действие — enable, rotate или disable", null);
        if (body.action === "disable") delete db.subs[sb.id];
        else if (body.action === "rotate" || !db.subs[sb.id]) db.subs[sb.id] = rid("", 43);          // выдуманный токен подписки
        bump();
      }
      return subDoc(sb);
    }
    if ((m = /^\/api\/gw\/users\/(u-[0-9a-f]{8})\/route$/.exec(path)) && method === "PUT") { var uu = find(db.users, m[1]); needVer(body, ver12(uu.route || null)); uu.route = body.via === "inherit" ? null : { via: body.via, fail: body.fail || keptFail("user", uu) }; bump(); return { ok: true, user: uu }; }
    var rn = rnRoute(method, path, body || {});
    if (rn) return rn;
    if (method === "POST" && path === "/api/gw/devices") {
      var user = find(db.users, body.user);
      if (!user) throw httpErr(400, "Пользователя нет.", 1);
      var nd = dev(rid("d-", 8), body.user, body.name, body.kind || "other", body.platform || "other", body.access || [], body.via ? { via: body.via, fail: body.fail || keptFail("device", { user: body.user }) } : null, {}, "dev-" + rid("", 4));
      (body.access || []).forEach(function (k) { nd.ips[k] = "10.77." + (db.devices.length + 1) + ".10"; });
      db.devices.push(nd); bump(); return { ok: true, device: nd, apply: { warnings: [] } };
    }
    if ((m = /^\/api\/gw\/devices\/(d-[0-9a-f]{8})$/.exec(path)) && method === "DELETE") { db.devices = db.devices.filter(function (d) { return d.id !== m[1]; }); bump(); return { ok: true }; }
    if ((m = /^\/api\/gw\/devices\/(d-[0-9a-f]{8})\/(access|route|reset|issue|enabled)$/.exec(path))) {
      var d = find(db.devices, m[1]);
      if (!d) throw httpErr(400, "Устройства нет.", 1);
      if (m[2] === "access" && method === "PUT") {
        if (Array.isArray(body.access)) d.access = body.access.slice(); else if (body.kind) d.access = body.on ? d.access.concat(body.kind) : d.access.filter(function (k) { return k !== body.kind; });
        bump(); return { ok: true, device: d, kind: body.kind };
      }
      if (m[2] === "route" && method === "PUT") { needVer(body, ver12(d.route || null)); d.route = body.via === "inherit" ? null : { via: body.via, fail: body.fail || keptFail("device", d) }; bump(); return { ok: true, device: d }; }
      if (m[2] === "reset" && method === "POST") { bump(); return { ok: true, device: d, kinds: body.inbound ? [body.inbound] : d.access, also: d.access.indexOf("l2tp") >= 0 && body.inbound === "awg3" ? ["l2tp"] : [] }; }
      if (m[2] === "issue" && method === "POST") return issueDoc(d, body || {});
      if (m[2] === "enabled" && method === "PUT") {
        if (typeof body.enabled !== "boolean") throw httpErr(400, "Поле «enabled» — true или false.", null);
        d.enabled = body.enabled; bump(); return { ok: true, device: d, warnings: [] };
      }
    }
    if (method === "POST" && path === "/api/gw/mtu") {
      if (body.action !== "reissue" && body.action !== "s4" && body.action !== "keep") throw httpErr(400, "«action» — reissue, s4 или keep.", null);
      if (body.action === "s4") { bump(); return { ok: true, kind: body.kind, changed: true, was: 120, s4: 33, server: 1280, devices: [],
        text: "S4 120 → 33, MTU сервера 1280. Устройств на этом протоколе нет.", warnings: [] }; }
      bump();
      if (body.action === "keep") return { ok: true, kind: body.kind, kept: true, nic: 1400 };
      return { ok: true, kind: body.kind, changed: true, was: 1280, client: 1248, server: 1248, devices: [],
        text: "MTU клиентов 1280 → 1248. Устройств на этом протоколе нет.", warnings: [] };
    }
    if (method === "GET" && path === "/api/gw/awg/engine") return awgDoc();
    if (method === "POST" && path === "/api/gw/awg/check") return { ok: true, can: true, blockers: [], items: [], codes: [], warnings: [],
      install: { packages: ["dkms", "gcc", "make", "patch", "linux-headers-6.8.0-50-generic"], mb: 210, minutes: "1–3", text: "Поставится: компилятор и заголовки ядра (≈210 МБ); сборка модуля 1–3 мин, затем самопроверка. Клиенты AmneziaWG переподключатся сами, обычно за 15–25 секунд; WireGuard не затрагивается." },
      benefit: ["Обычный VPN: процессора вдвое меньше на том же трафике.", "Транзит: в 2–3 раза больше трафика."], risks: ["Ошибка в модуле роняет всё ядро сервера.", "Нужны компилятор и заголовки ядра (≈200 МБ)."], pays: [], facts: {} };
    if (method === "POST" && path === "/api/gw/awg/engine") {
      if (body.engine !== "kernel" && body.engine !== "program") throw httpErr(400, "«engine» — kernel или program.", null);
      var was = db.awgEngine; db.awgEngine = body.engine; bump();
      return { ok: true, engine: body.engine, was: was, rolled_back: false, unchanged: was === body.engine, backup: "/var/backups/kaskad/awg-engine-20261007T120000Z", warnings: [] };
    }
    if (method === "POST" && path === "/api/gw/awg/export") return { ok: true, doc: { kind: "kaskad-awg-export", v: 1, inbounds: {}, users: [], devices: [], secrets: {} } };
    if (method === "POST" && path === "/api/gw/awg/import") { bump(); return { ok: true, inbounds: [], users: 0, devices: 0, warnings: [] }; }
    if (method === "GET" && path === "/api/gw/vless/ask") return vlAskDoc(query);
    if (path.indexOf("/api/gw/vless/") === 0) return vlessRoute(method, path, body || {});
    if (path === "/api/gw/hy2" || path === "/api/gw/hy2/apply") return hy2Route(method, path, body || {});
    if (method === "GET" && path === "/api/gw/exits") return exitsDoc();
    if (method === "POST" && path === "/api/gw/exit-subs") return subAdd(body || {});
    if (method === "POST" && path === "/api/gw/exit-subs/refresh") return subsRefresh(null);
    if ((m = /^\/api\/gw\/exit-subs\/(s-[0-9a-f]{6})\/refresh$/.exec(path)) && method === "POST") return subsRefresh(m[1]);
    if ((m = /^\/api\/gw\/exit-subs\/(s-[0-9a-f]{6})\/restore$/.exec(path)) && method === "POST") return subRestore(m[1]);
    if ((m = /^\/api\/gw\/exit-subs\/(s-[0-9a-f]{6})$/.exec(path)) && method === "PUT") {
      var sa = subFind(m[1]);
      if (body.label !== undefined) {                       // T13: подпись подписки; «» — снова домен
        var cs = rnCheck(body.label, "sub", true);
        var ds = rnDup(db.subs.map(function (x) { return { id: x.id, name: subName(x) }; }), cs, sa.id, body.allow_duplicate);
        var os = sa.label || ""; if (cs) sa.label = cs; else delete sa.label;
        var res = rnDone("exitsub", sa.id, os, cs, subName(sa), ds, RN_NOTE.same);
        if (typeof body.auto === "boolean") { sa.auto = body.auto; res.sub = subPublic(sa); }
        return res;
      }
      if (typeof body.auto !== "boolean") throw httpErr(400, "Поле «auto» — true или false.", null);
      sa.auto = body.auto; bump(); return { ok: true, sub: subPublic(sa) };
    }
    if ((m = /^\/api\/gw\/exit-subs\/(s-[0-9a-f]{6})$/.exec(path)) && method === "DELETE") return subDel(m[1], ["1", "true", "yes"].indexOf((query && query.get("keep_keys")) || "") >= 0);
    if (path.indexOf("/api/backup/") === 0) return backupRoute(method, path, body || {});
    if (method === "GET" && path === "/api/scheme") return schemeDoc();
    if (method === "PUT" && path === "/api/scheme/layout") return saveLayout(body);
    if (method === "POST" && path === "/api/scheme/apply") return schemeApply(body);
    if (method === "POST" && path === "/api/gw/trace") return traceDoc(body || {});
    if (method === "POST" && path === "/api/gw/exits") return exitAdd(body || {});
    if (method === "POST" && path === "/api/gw/exits/preview") return exitPreview(body || {});
    if ((m = /^\/api\/gw\/exits\/(e-[0-9a-f]{10}(?:-[0-9]{1,4})?)$/.exec(path)) && method === "DELETE") return exitDel(m[1], query || new URLSearchParams());
    if ((m = /^\/api\/gw\/exits\/(e-[0-9a-f]{10})(-[0-9]{1,4})?$/.exec(path)) && method === "PUT") {
      if (m[2]) throw httpErr(409, "Эта строка пула — повтор ключа строки выше: шлюз её не использует, и своих настроек у неё нет. Переименуйте или выключите исходный выход, а лишнюю строку удалите из пула «Прокси для API Telegram».", 3, { exit: m[1] });
      var ks = Object.keys(body || {}).filter(function (k) { return k !== "rev"; });
      if (!ks.length) throw httpErr(400, "Нечего менять.", 2);
      if (body.enabled !== undefined) { if (body.rev === undefined) throw httpErr(400, "Панель обновилась — обновите страницу.", "rev_required"); throw httpErr(400, "Вкл/выкл выхода — отдельным запросом.", "use_enabled", { field: "enabled" }); }
      if (body.group !== undefined || body.tier !== undefined) throw httpErr(400, "Перенос выхода — отдельным запросом.", "use_move", { field: "group" });
      if ((body.quota !== undefined || body.speed_skip !== undefined) && body.rev === undefined) throw httpErr(400, "Панель обновилась — обновите страницу.", "rev_required");
      var e = find(db.exits, m[1]);
      if (!e) goneExit(m[1]);
      var dryq = query && query.get("dry") === "1";
      if (body.quota !== undefined || body.speed_skip !== undefined) {
        if (body.quota) Object.keys(body.quota).forEach(function (k) { if (["period", "limit_gb", "reset_day", "warn_pct", "action"].indexOf(k) < 0) throw httpErr(400, "Лишнее поле квоты «" + k + "».", 2, { field: "quota." + k }); });
        if (dryq) return dryAnswer(db.policyRev, movesOf([]), []);
        needRev(body, query);
      }
      var exName = body.name !== undefined ? rnCheck(body.name, "exit", true) : undefined;   // T13: «» — имя из ключа, 41 — 400
      if (exName !== undefined) body.name = exName;
      if (body.name !== undefined && String(body.name).trim()) {
        // как policy_ops._exit_name: показанное имя другого выхода — 409 confirm_duplicate без allow_duplicate
        var nm = String(body.name).trim().toLowerCase();
        var dupE = db.exits.filter(function (x) { return x.id !== e.id && String(x.name || "").trim().toLowerCase() === nm; }).map(function (x) { return x.id; });
        if (dupE.length && !body.allow_duplicate) throw httpErr(409, "Выход с именем «" + body.name + "» уже есть.", "confirm_duplicate", { need: "confirm_duplicate", duplicates: dupE });
      }
      var exOld = e.name;
      if (body.name !== undefined) { if (!e._own) e._own = e.name; e.name = body.name || e._own; }
      if (body.quota !== undefined) e._quota = body.quota ? Object.assign({ used_gb: 12.4, pct: body.quota.limit_gb ? Math.round(1240 / body.quota.limit_gb) : 0, reset: "2026-11-01", paused_until: null }, body.quota) : null;
      if (body.speed_skip !== undefined) e._speedSkip = !!body.speed_skip;
      if (body.quota !== undefined || body.speed_skip !== undefined) { pol(); db.policyRev += 1; }
      bump();
      if (body.name !== undefined && body.quota === undefined && body.speed_skip === undefined) return { ok: true, id: m[1], rev: db.policy ? db.policyRev : undefined, name: body.name, old: exOld, shown: e.name, duplicates: [], note: RN_NOTE.same };
      return { ok: true, id: m[1], rev: db.policy ? db.policyRev : undefined };
    }
    if (method === "GET" && path === "/api/gw/offers") return offersDoc((query && query.get("platform")) || "other");
    if (method === "PUT" && path === "/api/panel/password") {
      if (typeof body.password !== "string" || body.password.length < 10 || body.password.length > 200) throw httpErr(400, "пароль: от 10 до 200 символов", 2);
      if (db.panel.password && via !== "tunnel" && body.current !== db.panel.pw) throw httpErr(403, "Текущий пароль не подошёл.", null, { left: 4 });
      db.panel.password = true; db.panel.pw = body.password; db.panel.login = body.login || db.panel.login;
      if (via !== "tunnel") via = "password";
      bump(); return { ok: true, login: db.panel.login, updated: new Date().toISOString().replace(/\.\d+Z$/, "Z") };
    }
    if (method === "PUT" && path === "/api/panel/public") {
      if (typeof body.on !== "boolean") throw httpErr(400, "Не задано поле «on» (true или false).", null);
      if (body.on && !db.panel.password) throw httpErr(400, "Сначала задайте пароль панели: в открытом режиме вход — только по паролю или одноразовой ссылке, и SSH-туннель тоже спросит пароль.", null);
      db.panel.public = body.on;
      if (body.on) db.panel.port = db.panel.port || 43125;
      bump(); return { ok: true, public: panelPublic(), fingerprint: body.on ? FINGERPRINT : "" };
    }
    if (method === "PUT" && path === "/api/panel/settings") {
      if (typeof body.require_password !== "boolean") throw httpErr(400, "Не задано поле «require_password» (true или false).", null);
      if (body.require_password && !db.panel.password) throw httpErr(400, "Сначала задайте пароль панели — иначе в неё не войти.", 1);
      db.panel.require = body.require_password; bump(); return { ok: true, require_password: db.panel.require };
    }
    if (method === "POST" && path === "/api/panel/link") {
      var ttl = Math.max(60, Math.min(Number(body.ttl) || 3600, 86400));
      return { ok: true, url: (db.panel.public ? "https://" + SRV + ":" + db.panel.port + "/" : "http://127.0.0.1:1980/") + "login?t=" + fakeB64(43), expires_in: ttl, public: db.panel.public };
    }
    if (path === "/api/github" && method === "GET") return githubDoc();
    if (path === "/api/github" && method === "PUT") {
      var tok = typeof body.token === "string" ? body.token.trim() : "";
      if (!/^(ghp_|github_pat_)[A-Za-z0-9_]{20,240}$/.test(tok)) throw httpErr(400, "Это не похоже на ключ GitHub: он начинается с ghp_ или github_pat_ и состоит только из латинских букв, цифр и знака «_».", null, { reason: "format" });
      if (/bad/i.test(tok)) throw httpErr(400, "GitHub не принял ключ (ответ 401): он неверный, отозван или истёк.", null, { reason: "bad" });
      var net = /net/i.test(tok);
      if (net && !body.force) throw httpErr(409, "Нет связи с GitHub (raw.githubusercontent.com): ключ не проверен.", null, { reason: "net" });
      db.ghSaved = true;                  // каталог шаблонов с catalog=nokey|bad после этого открывается
      db.github = { set: true, source: "bot", state: net ? "unchecked" : "ok", checked: net ? null : new Date().toISOString().replace(/\.\d+Z$/, "Z"), error: net ? "Нет связи с GitHub (raw.githubusercontent.com): ключ не проверен." : "" };
      bump(); return githubDoc();
    }
    if (path === "/api/github" && method === "DELETE") {
      var had = db.github.set && (db.github.source === "bot" || db.github.source === "config");
      db.github = { set: false, source: null, state: "none", checked: null, error: "" };
      bump(); return Object.assign(githubDoc(), { removed: had });
    }
    if (path === "/api/github/check" && method === "POST") {
      if (!db.github.set) throw httpErr(400, "Ключ GitHub не задан.", null);
      db.github.state = "ok"; db.github.error = ""; db.github.checked = new Date().toISOString().replace(/\.\d+Z$/, "Z");
      bump(); return githubDoc();
    }
    if (method === "GET" && path === "/api/rules") { if (!HAS_RULES) throw httpErr(404, "Нет такого пути.", null); return rulesDoc(); }
    if (method === "POST" && path === "/api/rules/update") {
      db.rules.last = { at: new Date().toISOString().replace(/\.\d+Z$/, "Z"), ok: true, changed: false, warnings: [], version: db.rules.lists.version };
      bump(); return { ok: true, changed: false, warnings: [], version: db.rules.lists.version, lists: db.rules.lists.label, status: rulesDoc() };
    }
    if (method === "PUT" && path === "/api/rules/ru-via") return putRuVia(body || {});
    if (method === "PUT" && path === "/api/rules/profile") {           // как kaskad.rules.panel.put_profile
      if (typeof body.profile !== "string") throw httpErr(400, "нужен профиль: inherit, all_exit или ru_direct", 2);
      if (body.target === "default") {
        if (body.profile !== "all_exit" && body.profile !== "ru_direct") throw httpErr(400, "Профиль по умолчанию — all_exit или ru_direct.", 2);
        if (typeof body.ver !== "string") throw httpErr(400, "Панель обновилась — обновите страницу", 2, { code: "ver_required" });
        if (body.ver !== "mockver-" + db.rules.default) throw httpErr(409, "Профиль «Правил» успел измениться — показан текущий.", 3, { code: "object_changed", ver: "mockver-" + db.rules.default });
        rfCheck(body.profile === "ru_direct" && db.rules.default !== "ru_direct", body);
        db.rules.default = body.profile;
      } else if (body.target === "user" || body.target === "device") {
        var who = find(body.target === "user" ? db.users : db.devices, body.id);
        if (!who) throw httpErr(404, (body.target === "user" ? "Пользователя " : "Устройства ") + body.id + " нет", 1);
        rfCheck(body.profile === "ru_direct" && who.rules !== "ru_direct", body);
        who.rules = body.profile === "inherit" ? null : body.profile;
      } else throw httpErr(400, "target — default, user или device", 2);
      rfReset();
      bump(); return { ok: true, ver: "mockver-" + db.rules.default, status: rulesDoc() };
    }
    if (method === "PUT" && path === "/api/rules/consent") {           // как kaskad.rules.panel.put_consent
      rfCheck(true, body);
      bump(); return { ok: true, granted: rfSelected(), status: rulesDoc() };
    }
    if (method === "PUT" && path === "/api/rules/blocks") {
      if (typeof body.ads !== "boolean" && typeof body.torrent !== "boolean") throw httpErr(400, "Нечего менять: укажите рекламу или торренты.", 2);
      if (typeof body.ads === "boolean") db.rules.block_ads = body.ads;
      if (typeof body.torrent === "boolean") db.rules.block_torrent = body.torrent;
      bump(); return { ok: true, status: rulesDoc() };
    }
    if (method === "GET" && path === "/api/panel") {
      if (!HAS_PANEL) throw httpErr(404, "Нет такого пути.", null);
      return { ok: true, public: panelPublic(), address: panelAddress(), listen: [{ kind: "local", addr: "127.0.0.1:1980", scheme: "http" }].concat(db.panel.public ? [{ kind: "public", addr: "10.0.0.5:" + db.panel.port, scheme: "https" }] : []),
        fingerprint: db.panel.public ? FINGERPRINT : "", sessions: 1, password: db.panel.password, login: db.panel.password ? db.panel.login : null, require_password: db.panel.require,
        tunnel: "ssh -L 1980:127.0.0.1:1980 root@" + SRV, version: db.version, you: { via: via } };
    }
    if (method === "GET" && path === "/api/session") return { ok: true, auth: authed(), via: authed() ? via : null, scheme: FROM_NET || LISTENER !== "local" ? "https" : "http", listener: LISTENER, public: db.panel.public, password: db.panel.password, login: authed() ? db.panel.login : null, require_password: db.panel.require, version: db.version, expires_in: null,
      has: authed() ? { activity: ACT !== "none" } : undefined };                 // как сервер (T-3377 Н-2): ?act=none — сервер без сбора
    // «Знакомство с панелью» — как api.intro_get / intro_put: устройство мастера — первое
    if (method === "GET" && path === "/api/intro") return { ok: true, due: INTRO_DUE && !db.introDone, done: !INTRO_DUE || !!db.introDone, setup_finished: true, device: db.devices.length ? db.devices[0].id : null };
    if (method === "PUT" && path === "/api/intro") { if (typeof body.done !== "boolean") throw httpErr(400, "Нужно «done»: true или false.", 2); db.introDone = body.done; return { ok: true, done: body.done }; }
    if (method === "GET" && path === "/api/appearance") return { ok: true, appearance: db.look || clone(LOOK_DEFAULTS), saved: !!db.look, defaults: clone(LOOK_DEFAULTS), fields: LOOK_FIELDS };
    if (method === "PUT" && path === "/api/appearance") { db.look = body.appearance || body; return { ok: true, appearance: db.look }; }
    // язык сервера (3.3.2): без сервера язык страницы — из ?lang=…, его и меняем перед перезагрузкой
    if (method === "POST" && path === "/api/lang") {
      if (body.lang !== "ru" && body.lang !== "en") throw httpErr(400, "Язык: ru или en.", 2);
      try { var u = new URL(location.href); u.searchParams.set("lang", body.lang); history.replaceState(null, "", u.pathname + u.search + u.hash); } catch (e) { /* старый браузер */ }
      return { ok: true, lang: body.lang };
    }
    if (method === "POST" && path === "/api/login") { if (body.password === db.panel.pw && (!body.login || body.login === db.panel.login)) { via = "password"; return { ok: true, via: "password" }; } throw httpErr(401, "Неверный логин или пароль.", null, { left: 4, auth: "failed" }); }
    if (method === "POST" && path === "/api/logout") { via = NEED_LOGIN || db.panel.public || db.panel.require ? null : "tunnel"; return { ok: true }; }
    throw httpErr(404, "Нет такого пути: " + path, null);
  }

  var LOOK_DEFAULTS = { theme: "dark", accent: "auto", hue: 200, tint: "neutral", density: "detailed", radius: 8, btn: "pill", cards: "shadow", nav: "full", fs: "m", vibrant: false, motion: true };
  var LOOK_FIELDS = { enums: { theme: ["light", "dark", "system"], accent: ["auto", "ga", "kaskad", "blue", "sky", "lavender", "mint", "coral", "graphite", "custom"], tint: ["neutral", "warm", "cool"],
    density: ["min", "balanced", "detailed"], btn: ["pill", "round"], cards: ["shadow", "border", "flat"], nav: ["full", "icons", "top"], fs: ["s", "m", "l"] }, ints: { hue: [0, 359], radius: [0, 16] }, bools: ["vibrant", "motion"] };

  function respond(status, obj) {
    return new Response(JSON.stringify(obj), { status: status, headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" } });
  }
  function mockFetch(url, init) {
    init = init || {};
    var method = (init.method || "GET").toUpperCase();
    var u = new URL(url, location.href);
    var path = u.pathname;
    // под префиксом (/app-…/api/overview, как за nginx) — путь API без него, как его видит сервер
    var base = location.pathname.replace(/[^/]*$/, "") || "/";
    if (base !== "/" && path.indexOf(base) === 0) path = "/" + path.slice(base.length);
    var body = {};
    try { body = init.body ? JSON.parse(init.body) : {}; } catch (e) { body = {}; }
    if (method === "DELETE" && u.searchParams.get("force")) body.force = true;
    return new Promise(function (resolve) {
      setTimeout(function () {
        if (!authed() && path !== "/api/login" && path !== "/api/session") { resolve(respond(401, { ok: false, error: "Нужен вход в панель.", data: { auth: "required" } })); return; }
        if (method !== "GET" && (init.headers || {})["X-Kaskad-Admin"] !== "1" && path !== "/api/login") { resolve(respond(403, { ok: false, error: "Нет заголовка X-Kaskad-Admin" })); return; }
        db.calls += 1;
        // ошибка — {ok: false, error, code, data}, а у отказа со своим телом (e.body: выпуск сертификата) — оно само;
        // долгий ответ (выпуск сертификата, ожидание DNS) — обещание
        var fail = function (e) { resolve(respond(e.http || 500, e.body || { ok: false, error: e.message, code: e.code === undefined ? null : e.code, data: e.data })); };
        try {
          var out = route(method, path.replace(/\/+$/, ""), body, u.searchParams);
          if (out && typeof out.then === "function") out.then(function (v) { resolve(respond(200, v)); }, fail);
          else resolve(respond(200, out));
        } catch (e) { fail(e); }
      }, LAT);
    });
  }

  // ── SSE ───────────────────────────────────────────────────────────────────
  // JSON merge patch (RFC 7386) из двух документов: изменённые поля, пропавшие — null, массив заменяется целиком
  function diff(a, b) {
    var plain = function (x) { return x !== null && typeof x === "object" && !Array.isArray(x); };
    if (!plain(a) || !plain(b)) return JSON.stringify(a) === JSON.stringify(b) ? undefined : b;
    var out = {}, any = false;
    Object.keys(b).forEach(function (k) { var d = diff(a[k], b[k]); if (d !== undefined) { out[k] = d; any = true; } });
    Object.keys(a).forEach(function (k) { if (!(k in b)) { out[k] = null; any = true; } });
    return any ? out : undefined;
  }
  var Sse = { clients: [], timer: null,
    emit: function (type, data) { Sse.clients.forEach(function (c) { c._emit(type, data); }); },
    // «сборщик»: счётчики, сессии, кто в сети; без SSE (sse=0) его круг делает запрос GET overview — не чаще раза в 2 с
    simulate: function () {
      if (db.stale || Date.now() - (db._simAt || 0) < 1500) return;
      db._simAt = Date.now();
      db._cpu = Math.max(1, Math.min(95, (db._cpu || 8) + (Math.random() - 0.5) * 6));
      db._mem = (db._mem || 431000000) + Math.floor((Math.random() - 0.4) * 900000);
      db.forwards.forEach(function (f) { if (f._fix) return; var c = f._c || (f._c = { packets: 7, bytes: 700 }); c.packets += Math.floor(Math.random() * 40); c.bytes += Math.floor(Math.random() * 40000); });
      db.devices.forEach(function (d, i) {
        var l = d._live || (d._live = { online: i % 3 !== 2, inbound: d.access[0] || "wg", addr: ipOf(d, d.access[0] || "wg") || (XRAY_KINDS.indexOf(d.access[0]) >= 0 ? null : "10.77.1.10"), since: nowS() - 3000 - i * 700, rx: 60000 * (i + 1), tx: 120000 * (i + 1) });          // rx — что прислало устройство, tx — что оно скачало
        if (db.gw.enabled && !STILL && Math.random() < 0.04) { l.online = !l.online; if (l.online) l.since = nowS(); }
        if (!db.gw.enabled) l.online = false;
        if (l.online) {
          var drx = Math.floor(Math.random() * 30000), dtx = Math.floor(Math.random() * 90000);
          l.rx += drx; l.tx += dtx;
          if (db._actW === actAnchor()) { var c = d._cur || (d._cur = [0, 0, 0, 0]); c[0] += 2; c[1] += drx + dtx > 4096 ? 2 : 0; c[2] += drx; c[3] += dtx; }   // открытое окно (actOpen)
        }
      });
      INBOUND_DEFS.forEach(function (x) { var r = db.inbounds[x[0]]; r.sessions = db.gw.enabled && r.installed && r.enabled ? db.devices.filter(function (d) { return d.access.indexOf(x[0]) >= 0 && d._live && d._live.online; }).length : 0; });
    },
    tick: function () {
      Sse.simulate();
      var doc = liveDoc();
      Sse.clients.forEach(function (c) {
        // как sse.py: нет свежих данных — {"full": true} без них (один раз), данные вернулись — снова полная картина
        if (db.stale) { if (c._last !== "stale") c._emit("live", { full: true }); c._last = "stale"; return; }
        if (!c._last || c._last === "stale") c._emit("live", Object.assign({ full: true }, doc));          // первое событие потока — полная картина
        else { var p = diff(c._last, doc); if (p) c._emit("live", p); }
        c._last = JSON.parse(JSON.stringify(doc));
      });
      if (!db.stale && !STILL && Math.random() < 0.03) {
        var ev = { id: Date.now(), kind: "exit_alive", text: "Выход «Токио» снова работает (пример сообщения alert)", ts: nowS(), exit: "e-9aa1b2c3d4" };
        db.events.unshift(ev); Sse.emit("alert", ev);
      }
    }
  };
  function MockES(url) {
    var self = this, handlers = {};
    this.url = url;
    this.readyState = 0;
    this.onopen = null;
    this.onerror = null;
    this.addEventListener = function (t, fn) { (handlers[t] = handlers[t] || []).push(fn); };
    this._emit = function (type, data) { (handlers[type] || []).forEach(function (fn) { fn({ data: JSON.stringify(data) }); }); };
    this.close = function () { this.readyState = 2; Sse.clients = Sse.clients.filter(function (c) { return c !== self; }); if (!Sse.clients.length) { clearInterval(Sse.timer); Sse.timer = null; } };
    setTimeout(function () {
      if (self.readyState === 2) return;
      if (!authed()) { self.readyState = 2; if (self.onerror) self.onerror({}); return; }
      self.readyState = 1;
      Sse.clients.push(self);
      if (!Sse.timer) Sse.timer = setInterval(Sse.tick, 2000);
      if (self.onopen) self.onopen({});
      Sse.tick();
    }, LAT);
  }

  // collector(false) — сборщик остановился (через 30 с live.json несвежий: поток шлёт {"full": true}), collector(true) — снова пишет
  function collector(on) { db.stale = !on; if (Sse.clients.length) Sse.tick(); return !db.stale; }
  // emit — событие потока от надстроек мока (dev-mock-regroup.js: group_regrouped после «Разложить»); alert — и в ленту
  g.KASKAD_MOCK = { fetch: mockFetch, EventSource: USE_SSE ? MockES : undefined, db: function () { return db; }, reset: function () { db = freshDb(); }, collector: collector,
    emit: function (kind, data) { if (kind === "alert") db.events.unshift(data); Sse.emit(kind, data); } };
})(window);
