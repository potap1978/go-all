/* Kaskad — dev-mock-setup.js: ответы API мастера быстрой настройки без сервера. В index.html НЕ подключается:
   setup.js подгружает его сам при ?mock=1.

       python3 -m http.server 8765 --directory lib/kaskad/panel/static
       http://127.0.0.1:8765/setup/index.html?mock=1

   window.KASKAD_SETUP_MOCK = { fetch(url, init), EventSource } — формы ответов те же, что у сервера
   (lib/kaskad/setup/web.py, steps.py; контракт — docs/k3/SPEC-setup-api.md). Хранилище в памяти; долгие шаги идут
   строками живой проверки (wait → ok/warn/err) и событием «job» потока api/setup/events.

   Параметры адреса (к mock=1):
       lat=400            задержка каждого ответа, мс (по умолчанию 60)
       auth=link|password|expired   нет сессии: войти по ссылке из SSH / форма пароля / ссылка истекла
       pw=1               пароль уже задан (сессия «password»)
       step=<kind>        текущий этап: import, protocols, forward, exits, domain, extras, done
       mode=classic       классический Каскад (только пробросы): свой рельс без «Выходов»
       offline=1          у сервера нет интернета (api/setup/net и шаги с сетью — offline)
       cert=fallback      сертификат на IP не вышел (порт 80 занят): плашка с причиной и отпечатком
       github=none|bad    ключа GitHub нет / не подошёл: в protocols-plan github.ask = true
       module=no|ready    модуль ядра AmneziaWG здесь нельзя / уже собран (подсказки «Соберём модуль» нет)
       fail=<шаг>         этот шаг отвечает ошибкой (долгий — строкой err и failed)
       soon=1             подписки и копии настроек ещё нет — «появится в этой версии» (в 3.3.0 они есть)
       conn=stuck         подключение не случилось за 2 минуты: подсказки «Самое вероятное — сверху»
       dns=cloudflare|elsewhere|none   исход проверки DNS (без него — wait, через 3 опроса ok)
       sse=off            поток событий не открывается — ход заданий опросом api/setup/job
       tpl=nokey          «Выбрать вручную»: каталог шаблонов не скачать без ключа GitHub (fail=templates — любая ошибка)
       le=limit           выпуск сертификата: лимит Let's Encrypt (5 одинаковых за неделю) со сроком «retry after»
       site=removed       сайт снят вручную (kaskadctl site remove): запись шага «site: ok» осталась, по факту — выключен
       have=<домен>       у сервера уже работает этот домен (сертификат, сайт, VLESS на TLS): новое имя в поле только
                          проверяется — сервер не меняется, пока для него не выпущен сертификат; смена — шагом cert
                          (строки DNS → сертификат → переключение; fail=cert — откат к прежнему); после смены —
                          плашка «Внимание: выданные ссылки VLESS не подключатся» с «Выдать заново»
       try=<имя>          (с have=) прошлая проверка нового имени не прошла (нет A-записи): после перезагрузки в поле —
                          рабочий домен, под полем «Последняя проверка: <имя> — нет A-записи»
       vless=m1|m2|chosen VLESS на этапе «Домен и сайт» (VLESS Ш4, HANDOFF-vless §5), домен vpn.example.com с
                          сертификатом, сайт выключен: m1 — устройств VLESS нет, сайт «включится сам» (М1; ссылка
                          «Не включать сайт» → окно согласия М3); m2 — копия настроек с 9 устройствами VLESS на
                          Reality: рекомендация с ценой (М2; «Перейти на TLS» → окно цены Ц4, «Оставить Reality» →
                          согласие); chosen — Reality выбран человеком. Шаг vless-mode и цена шага site — отказами,
                          как ядро (reality_consent / vless_change), повтор с согласием или yes
   Подписка в окне ключа: адрес на *.invalid или nope.* — «адрес не найден», 127./10./192.168./169.254. — внутренний;
   тот же адрес второй раз — «уже добавлена» (не ошибка). Ключ того же сервера (тот же адрес и вид) — «уже в пуле».
   Все адреса, ключи и пароли ниже выдуманные. */
(function () {
  "use strict";
  // флаги макета: из адреса (?mock=1&github=none…) и из панели просмотра dev-view-setup.js (KASKAD_SETUP_MOCK.flags)
  var FL = {};
  new URLSearchParams(location.search).forEach(function (v, k) { FL[k] = v; });
  var P = { get: function (k) { return Object.prototype.hasOwnProperty.call(FL, k) ? FL[k] : null; } };
  var LAT = +(P.get("lat") || 60);
  var IP = "203.0.113.67";
  var NOW = function () { return Date.now() / 1000; };

  // как lib/kaskad/setup/product.py (тест test_mock_product_is_the_server_one сверяет): название, подпись, минуты, диапазон
  var STAGES = {
    welcome: ["Вход", "Приветствие и пароль", 1, [1, 1]], "import": ["Импорт", "Что уже есть на сервере", 2, [1, 2]],
    protocols: ["Режим и протоколы", "Что будет делать сервер", 5, [2, 5]], forward: ["Пробросы", "Куда перекидывать трафик", 3, [2, 3]],
    exits: ["Выходы", "Куда выпускать трафик", 3, [2, 3]], domain: ["Домен и сайт", "По желанию", 20, [5, 20]],
    extras: ["Дополнительно", "Бот, ключ ИИ-агента, копия настроек", 2, [1, 2]], done: ["Готово", "Первое подключение", 3, [1, 3]]
  };
  var RAIL = { node: ["import", "protocols", "exits", "domain", "extras", "done"],
               classic: ["import", "forward", "domain", "done"] };
  var MODE_SLOT = { node: "protocols", classic: "forward" };
  var VIDEOS = {
    welcome: { title: "Как пройти быстрый старт", dur: "0:50", src: null, chapters: [["0:00", "Как устроен мастер", "steps"], ["0:20", "Придумываем пароль", "pw"], ["0:38", "Почему браузер не ругается", "trust"]] },
    "import": { title: "Переезд со старой версии", dur: "1:10", src: null, chapters: [["0:00", "Что мастер нашёл", "found"], ["0:30", "Что не трогаем", "keep"], ["0:52", "Файл копии настроек", "other"]] },
    protocols: { title: "Узел или классический Каскад", dur: "1:30", src: null, chapters: [["0:00", "Два режима", "mode"], ["0:35", "Смешивание протоколов", "mix"], ["0:58", "Какие протоколы отметить", "protos"], ["1:18", "Порты и место", "summary"]] },
    forward: { title: "Первый проброс", dur: "0:50", src: null, chapters: [["0:00", "Куда и какой порт", "fwd"], ["0:30", "Проверка", "fwdcheck"]] },
    exits: { title: "Выходы за рубеж", dur: "1:20", src: null, chapters: [["0:00", "Нужны ли выходы", "choice"], ["0:22", "Добавляем ключ", "addkey"], ["0:50", "Проверка ключей", "keys"], ["1:05", "Маршрут и отказ", "route"]] },
    domain: { title: "Домен и A-запись в AdminVPS", dur: "2:10", src: null, chapters: [["0:00", "Покупка и A-запись", "record"], ["0:50", "IP сервера", "ipcopy"], ["1:25", "Проверка DNS", "dns"], ["1:50", "Сайт-маскировка", "site"]] },
    extras: { title: "Что включить сразу", dur: "0:40", src: null, chapters: [["0:00", "Бот в Telegram", "xbot"], ["0:18", "Ключ ИИ-агента", "xmcp"], ["0:30", "Копия настроек", "xbak"]] },
    done: { title: "Подключаем телефон по QR", dur: "1:10", src: null, chapters: [["0:00", "Устройство", "device"], ["0:20", "QR и файлы", "qr"], ["0:45", "Подключение", "status"]] }
  };
  var EXTRAS = [
    { id: "bot", title: "Бот в Telegram", desc: "Управление и уведомления в Telegram.", hl: "xbot" },
    { id: "mcp", title: "Ключ ИИ-агента", desc: "Claude Code, Claude Desktop или Cursor управляют узлом.", hl: "xmcp" },
    { id: "backup", title: "Копия настроек файлом", desc: "Все настройки одним файлом — сохраните его.", hl: "xbak" }];
  var CARDS = {
    awg2: ["AmneziaWG 2.0", "Телефоны и компьютеры (AmneziaVPN)", ["рекомендуем"], 6, true, 41023, "udp"],
    awg3: ["AmneziaWG 3.1", "Новая линия, лучше прячется от фильтров", ["рекомендуем"], 6, true, 51911, "udp"],
    wg: ["WireGuard", "Роутеры и простые клиенты WireGuard", [], 2, true, 51820, "udp"],
    l2tp: ["L2TP/IPsec", "Роутеры и встроенный VPN — без приложений", [], 18, true, null, "udp"],
    awg1: ["AmneziaWG 1.5", "Keenetic и старые приложения (линия 1.5)", [], 6, false, 31871, "udp"],
    ikev2: ["IKEv2", "iPhone и Mac без приложений", [], 14, false, null, "udp"],
    sstp: ["SSTP", "Windows без приложений, по TCP", [], 12, false, 4443, "tcp"],
    openvpn: ["OpenVPN", "Старые устройства, телевизоры, OpenVPN Connect", [], 26, false, 1194, "udp"],
    ocserv: ["OpenConnect", "OpenConnect и Cisco AnyConnect (TCP)", [], 22, false, 8443, "tcp"],
    socks5: ["SOCKS5", "Прокси для браузера и Telegram", [], 3, false, 1080, "tcp"],
    pptp: ["PPTP", "Только для старых роутеров", ["слабое шифрование"], 8, false, null, "tcp"],
    vless: ["VLESS", "Телефоны и компьютеры (v2RayTun, Hiddify, Streisand)", [], 0, false, 443, "tcp"],
    hy2: ["Hysteria2", "Телефоны и компьютеры по UDP (v2RayTun, Hiddify, Karing); блокировки UDP в мобильных сетях РФ не проверены", [], 0, false, 443, "udp"]
  };
  var ORDER = ["wg", "awg1", "awg2", "awg3", "l2tp", "pptp", "sstp", "ikev2", "openvpn", "ocserv", "socks5", "vless", "hy2"];
  var TITLES = { wg: "WireGuard", awg1: "AmneziaWG 1.5", awg2: "AmneziaWG 2.0", awg3: "AmneziaWG 3.1", l2tp: "L2TP/IPsec", pptp: "PPTP",
                 sstp: "SSTP", ikev2: "IKEv2", openvpn: "OpenVPN", ocserv: "OpenConnect", socks5: "SOCKS5", vless: "VLESS", hy2: "Hysteria2" };
  // протоколы по умолчанию стоят, если макет открыт на этапе после «Режим и протоколы» (step=exits…)
  var INSTALLED = ["exits", "domain", "extras", "done"].indexOf(P.get("step")) >= 0 ? ["awg2", "awg3", "wg", "l2tp"] : [];
  // что выдать первым по платформе — issue_os.MATRIX целиком (вход, формат, примечание из каталога gw_issue, ru);
  // примечания платформы — issue_os.NOTES. Английские — в dev-mock-en.js; сверка — tests/test_setup_layout_332.py
  var OFFERS = {
    ios: [["awg3", "vpn", "AmneziaVPN: ссылка vpn:// или QR; AmneziaWG: файл .conf или QR"], ["awg2", "vpn", "AmneziaVPN: ссылка vpn:// или QR; AmneziaWG: файл .conf или QR"], ["awg1", "vpn", "AmneziaVPN: ссылка vpn:// или QR; AmneziaWG: файл .conf или QR"], ["vless", "link", "Streisand или Hiddify: ссылка vless:// или QR"], ["hy2", "link", "Hysteria2 (UDP): ссылка hysteria2:// или QR — v2RayTun, Hiddify, Karing, v2rayN"], ["ikev2", "mobileconfig", "профиль .mobileconfig — встроенный клиент, без приложений"], ["wg", "qr", "приложение WireGuard: QR"], ["ocserv", "fields", "Cisco Secure Client: адрес, логин и пароль"]],
    macos: [["awg3", "vpn", "AmneziaVPN: ссылка vpn://; AmneziaWG: файл .conf"], ["awg2", "vpn", "AmneziaVPN: ссылка vpn://; AmneziaWG: файл .conf"], ["awg1", "vpn", "AmneziaVPN: ссылка vpn://; AmneziaWG: файл .conf"], ["vless", "link", "Hiddify, Streisand или v2rayN: ссылка vless://"], ["hy2", "link", "Hysteria2 (UDP): ссылка hysteria2:// или QR — v2RayTun, Hiddify, Karing, v2rayN"], ["ikev2", "mobileconfig", "профиль .mobileconfig — встроенный клиент"], ["wg", "conf", "приложение WireGuard: файл .conf"], ["ocserv", "fields", "OpenConnect GUI или Cisco Secure Client"]],
    android: [["awg3", "vpn", "AmneziaVPN: ссылка vpn:// или QR; AmneziaWG: файл .conf или QR"], ["awg2", "vpn", "AmneziaVPN: ссылка vpn:// или QR; AmneziaWG: файл .conf или QR"], ["awg1", "vpn", "AmneziaVPN: ссылка vpn:// или QR; AmneziaWG: файл .conf или QR"], ["vless", "link", "v2RayTun, Hiddify, v2rayNG или NekoBox: ссылка vless:// или QR"], ["hy2", "link", "Hysteria2 (UDP): ссылка hysteria2:// или QR — v2RayTun, Hiddify, Karing, v2rayN"], ["wg", "qr", "приложение WireGuard: QR"], ["ikev2", "fields", "встроенный клиент IKEv2 — Android 12 и новее"], ["openvpn", "ovpn", "OpenVPN Connect: файл .ovpn"]],
    androidtv: [["awg3", "vpn", "AmneziaVPN: ссылку vpn:// удобно вставить из буфера; AmneziaWG — файл .conf"], ["awg2", "vpn", "AmneziaVPN: ссылку vpn:// удобно вставить из буфера; AmneziaWG — файл .conf"], ["awg1", "vpn", "AmneziaVPN: ссылку vpn:// удобно вставить из буфера; AmneziaWG — файл .conf"], ["vless", "link", "v2RayTun или Hiddify: ссылку vless:// вставьте из буфера"]],
    windows: [["awg3", "vpn", "AmneziaVPN: ссылка vpn://; AmneziaWG: файл .conf"], ["awg2", "vpn", "AmneziaVPN: ссылка vpn://; AmneziaWG: файл .conf"], ["awg1", "vpn", "AmneziaVPN: ссылка vpn://; AmneziaWG: файл .conf"], ["vless", "link", "Hiddify или v2rayN: ссылка vless://"], ["hy2", "link", "Hysteria2 (UDP): ссылка hysteria2:// или QR — v2RayTun, Hiddify, Karing, v2rayN"], ["ikev2", "ps1", "встроенный клиент: скрипт PowerShell Add-VpnConnection"], ["sstp", "ps1", "встроенный клиент: скрипт PowerShell Add-VpnConnection"], ["l2tp", "ps1", "встроенный клиент: скрипт PowerShell Add-VpnConnection"], ["openvpn", "ovpn", "OpenVPN Connect или OpenVPN GUI: файл .ovpn"], ["ocserv", "fields", "OpenConnect GUI или Cisco Secure Client"]],
    linux: [["wg", "conf", "wg-quick up ./gKP-….conf"], ["awg3", "conf", "amneziawg-tools 3.1: awg-quick up ./gKP-….conf"], ["awg2", "conf", "amneziawg-tools: awg-quick up ./gKP-….conf"], ["awg1", "conf", "amneziawg-tools: awg-quick up ./gKP-….conf"], ["vless", "link", "Hiddify или v2rayN: ссылка vless://"], ["hy2", "link", "Hysteria2 (UDP): ссылка hysteria2:// или QR — v2RayTun, Hiddify, Karing, v2rayN"], ["openvpn", "ovpn", "openvpn --config kaskad-openvpn.ovpn"], ["ocserv", "guide", "команда openconnect с отпечатком сертификата"], ["socks5", "link", "прокси SOCKS5 для отдельных программ"]],
    keenetic: [["awg2", "conf", "KeeneticOS 5.1.1 и новее: импорт из файла"], ["awg1", "conf", "линия 1.5 — KeeneticOS 5.1.1+, линия 1.0 — с 4.2"], ["wg", "conf", "файл .conf: импорт из файла"], ["l2tp", "guide", "«Другие подключения» → VPN-подключения"]],
    mikrotik: [["wg", "conf", "RouterOS 7: скрипт настройки WireGuard"], ["l2tp", "guide", "скрипт RouterOS 7: /interface l2tp-client"], ["sstp", "guide", "скрипт RouterOS 7: /interface sstp-client"], ["openvpn", "ovpn", "RouterOS 7: /interface ovpn-client, сертификаты из .ovpn"]],
    openwrt: [["awg2", "conf", "пакеты awg-openwrt: LuCI → «Load configuration…» («Загрузка конфигурации…»)"], ["awg3", "conf", "3.1 — только OpenWrt 24.10/25.12 со сборками AWG 3.1"], ["awg1", "conf", "пакеты awg-openwrt: LuCI → «Load configuration…» («Загрузка конфигурации…»)"], ["wg", "conf", "LuCI → «Load configuration…» («Загрузка конфигурации…»)"], ["openvpn", "ovpn", "пакет openvpn-openssl, файл .ovpn"]],
    tplink: [["l2tp", "guide", "у старых моделей только L2TP без IPsec"], ["pptp", "guide", "только если протокол включён"]],
    huawei: [["l2tp", "guide", "у старых моделей только L2TP без IPsec"], ["pptp", "guide", "только если протокол включён"]],
    xiaomi: [["l2tp", "guide", "у старых моделей только L2TP без IPsec"], ["pptp", "guide", "только если протокол включён"]],
    asus: [["l2tp", "guide", "у старых моделей только L2TP без IPsec"], ["openvpn", "ovpn", "VPN-клиент роутера: импорт файла .ovpn"], ["pptp", "guide", "только если протокол включён"]],
    other: [["wg", "conf", "файл .conf для любого клиента WireGuard"], ["awg3", "vpn", "AmneziaVPN: ссылка vpn:// или QR; AmneziaWG: файл .conf или QR"], ["awg2", "vpn", "AmneziaVPN: ссылка vpn:// или QR; AmneziaWG: файл .conf или QR"], ["awg1", "conf", "файл .conf AmneziaWG"], ["vless", "link", "любой клиент VLESS (Hiddify, v2RayTun, v2rayN): ссылка vless://"], ["hy2", "link", "Hysteria2 (UDP): ссылка hysteria2:// или QR — v2RayTun, Hiddify, Karing, v2rayN"], ["openvpn", "ovpn", "файл .ovpn"], ["socks5", "link", "прокси SOCKS5"]]
  };
  var OFFER_NOTES = { ios: ["В российском App Store AmneziaVPN скрыт — подойдут AmneziaWG или DefaultVPN."], androidtv: ["На ТВ нет камеры: ссылку vpn:// вставьте из буфера или загрузите файл .conf."], keenetic: ["AmneziaWG 3.1 — только с KeeneticOS 5.2 Alpha 11 (тестовый канал); на прошивках до 5.1.1 — линия 1.0 или WireGuard."], mikrotik: ["AmneziaWG в RouterOS нет — только в контейнере; проще WireGuard."], openwrt: ["Официальных пакетов AmneziaWG у OpenWrt нет — только сборки сообщества."], tplink: ["Посмотрите в меню роутера раздел «VPN-клиент»; если его нет — роутер не подойдёт. Обычно у TP-Link только L2TP и PPTP."], huawei: ["У этих роутеров обычно только L2TP и PPTP."], xiaomi: ["У этих роутеров обычно только L2TP и PPTP."] };
  // шаги выдачи — issue_os.howto(платформа, вход, where="panel") на русском; английские — в dev-mock-en.js
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
  var SOON = "Появится в этой версии Kaskad: функцию ещё доделывают — пункт заработает после обновления.";
  // занятость — словами, как kaskad.setup.jobs.BUSY (без внутренних имён шагов)
  var BUSY = { "import": "Ещё переношу настройки Kaskad 2.x — подождите, пока закончу.", "import-undo": "Ещё возвращаю прежние настройки — подождите, пока закончу.",
    "import-file": "Ещё переношу настройки из файла — подождите, пока закончу.", protocols: "Ещё ставлю протоколы — подождите, пока закончу.",
    "exits-add": "Ещё проверяю предыдущий ключ — подождите, пока закончу.", cert: "Ещё выпускаю сертификат домена — подождите, пока закончу.",
    site: "Ещё ставлю сайт на домен — подождите, пока закончу.", template: "Ещё меняю шаблон сайта — подождите, пока закончу.",
    templates: "Ещё загружаю каталог шаблонов — подождите, пока закончу.", "vless-mode": "Ещё меняю режим VLESS — подождите, пока закончу." };
  var BUSY_OTHER = "Ещё выполняю предыдущее действие мастера — подождите, пока закончу.";
  var LONG = { "import": 1, "import-undo": 1, "import-file": 1, protocols: 1, "exits-add": 1, cert: 1, site: 1, template: 1, "vless-mode": 1 };
  var SECRET = { password: 1, "import-file": 1, "backup-open": 1, "github-key": 1, "exits-preview": 1, "exits-add": 1, backup: 1, issue: 1 };
  var NET = { "github-key": 1, protocols: 1, "exits-add": 1, dns: 1, cert: 1, site: 1, template: 1, "vless-mode": 1 };

  var PW0 = P.get("pw") === "1" || (!!P.get("step") && P.get("step") !== "welcome");   // этап после входа — пароль задан
  var S = {
    auth: P.get("auth") ? null : (PW0 ? "password" : "link"),
    reason: P.get("auth") || null,
    pw: PW0 || P.get("auth") === "password",
    mode: P.get("mode") === "classic" ? "classic" : "node",
    current: P.get("step") || (P.get("pw") === "1" ? "import" : "welcome"),
    stages: {}, answers: {}, steps: {}, job: null, exits: [], dnsPolls: 0, connSince: NOW(), route: { via: "pool", fail: "block" },
    devices: [], subs: [], certDomain: null,
    siteMode: "domain",                                // сайт по факту (state.site в ответе сервера) — site | domain
    acmeEmail: P.get("email") || "",                   // почта Let's Encrypt (?email=a@b.c — задана заранее)
    expires: NOW() + 86000
  };
  // подписки выходов: адрес — только для сравнения «уже добавлена» (как exitsubs.find_same); наружу — имя и состояние
  function subOf(url) { var u = String(url || "").trim().replace(/\/+$/, "").toLowerCase(); return S.subs.filter(function (x) { return x.url === u; })[0] || null; }
  function subAdd(url) {
    var u = String(url || "").trim().replace(/\/+$/, "").toLowerCase();
    var host = (/^https?:\/\/([^/:?#]+)/.exec(u) || [])[1] || "";
    var sub = { id: "s-" + (100000 + S.subs.length).toString(16), url: u, name: /^[\d.]+$/.test(host) ? "подписка " + (S.subs.length + 1) : host, auto: true, keys: 3, text: "обновлена только что: ключей 3" };
    S.subs.push(sub);
    // как сервер: ключи подписки — в пуле, с меткой источника (exits_view: source)
    ["Вена", "Амстердам", "Варшава"].forEach(function (n, i) {
      S.exits.push({ id: "e-" + sub.id.slice(2) + "000" + i, name: n, kind: i === 1 ? "xray" : "awg", proto: i === 1 ? "vless" : "", title: i === 1 ? "VLESS" : "AmneziaWG 2.0", where: host + ":" + (51820 + i),
        enabled: true, usable: true, handshake: 5, state: "ok", text: "работает", source: { sub: sub.id, name: sub.name } });
    });
  }
  function subAlready(x) { return "Эта подписка уже добавлена (" + x.name + "): её ключи в пуле, обновляется раз в сутки. Второй раз добавлять не нужно."; }
  // проверка адреса до «добавить?» — как steps.sub_check: имя, которого нет (*.invalid), и внутренние адреса — отказ
  function subCheck(url) {
    var host = (/^https?:\/\/([^/:?#]+)/i.exec(String(url || "").trim()) || [])[1] || "";
    if (!host) return fail("Подписка — ссылка http:// или https:// с адресом сервера.", "bad_input", { field: "text" });
    if (/\.invalid$|^nope\./i.test(host)) return fail("Адрес сервера подписки не найден (" + host + ").", "bad_url", { field: "text" });
    if (/^(127\.|10\.|192\.168\.|169\.254\.)/.test(host)) return fail("Ссылка подписки ведёт на внутренний адрес (" + host + "): такие ссылки не скачиваются — вставьте сами ключи подписки.", "bad_url", { field: "text" });
    if (/^https:\/\/[\d.]+[:/]/i.test(String(url || "").trim()) && /certdomain/i.test(url)) {   // сертификат на домен (как subfetch.check_cert)
      var cn = fail("Сертификат выдан на домен sub.example.net — добавьте подписку по домену или по http.", "cert_name", { field: "text" });
      cn.data = { cert_domain: "sub.example.net", same_server: true };
      return cn;
    }
    var x = subOf(url);
    if (x) return res("ok", subAlready(x), { sub_already: true, sub_checked: true });
    return res("ok", "Сервер подписки найден. «Добавить подписку» — скачаю её и добавлю ключи в пул; обновляться она будет раз в сутки.", { sub_already: false, sub_checked: true });
  }
  var listeners = [];
  (function () {                                       // ?step=<kind>: этапы до него пройдены (skipped=<kind,kind> — пропущены)
    var kinds = RAIL[S.mode], i = kinds.indexOf(S.current), sk = (P.get("skipped") || "").split(",");
    for (var n = 0; n < i; n++) S.stages[kinds[n]] = sk.indexOf(kinds[n]) >= 0 ? "skipped" : "done";
    if (P.get("step") === "done" && S.mode === "classic") S.answers.forward = { proto: "tcp", port: 8443, to: "192.0.2.4", to_port: 443 };
    if (P.get("have")) {                               // рабочий домен: сертификат, сайт, VLESS на TLS
      S.answers.domain_plan = "have"; S.answers.domain = S.domain = S.certDomain = P.get("have").toLowerCase(); S.siteMode = "site";
      S.steps.site = { state: "ok", at: new Date().toISOString(), text: "На домене — обычный сайт; панель переехала на новый адрес.", data: {} };
      if (P.get("try")) S.answers.domain_try = { name: P.get("try").toLowerCase(), state: "none", why: "", at: new Date().toISOString() };
    }
    if (P.get("vless")) {                              // VLESS Ш4: домен с сертификатом, сайт выключен, VLESS на Reality
      S.answers.domain_plan = "have"; S.answers.domain = S.domain = S.certDomain = "vpn.example.com";
      vlessSet(P.get("vless"));
    }
    if (P.get("site") === "removed") {                 // прошлый проход включил сайт, потом его сняли из SSH
      S.answers.domain_plan = "have"; S.answers.domain = S.domain = "k3.example.com";
      S.steps.site = { state: "ok", at: new Date().toISOString(), text: "На домене — обычный сайт; панель переехала на новый адрес.", data: {} };
    }
  })();

  function stageDoc(k) { return { kind: k, title: STAGES[k][0], sub: STAGES[k][1], min: STAGES[k][2], range: STAGES[k][3].slice() }; }
  function rail() {
    var kinds = RAIL[S.mode], idx = kinds.indexOf(S.current), rows = kinds.map(function (k, i) {
      var st = k === S.current ? "current" : (S.stages[k] || "ahead");
      return Object.assign(stageDoc(k), { n: i + 1, state: st });
    });
    var rest = rows.filter(function (r, i) { return r.state !== "done" && (idx < 0 || i >= idx); });
    var left = rest.reduce(function (a, r) { return a + r.min; }, 0);
    var lo = rest.reduce(function (a, r) { return a + r.range[0]; }, 0), hi = rest.reduce(function (a, r) { return a + r.range[1]; }, 0);
    return { welcome: Object.assign(stageDoc("welcome"), { n: 0, state: S.current === "welcome" ? "current" : (S.pw ? "done" : "ahead") }),
             stages: rows, current: S.current, position: idx + 1, total: rows.length, left_min: left, left: [lo, hi] };
  }
  function product() {
    var cards = {};
    Object.keys(CARDS).forEach(function (k) { cards[k] = { why: CARDS[k][1], tags: CARDS[k][2], mb: CARDS[k][3], on: CARDS[k][4] }; });
    return { id: "kaskad", name: "Kaskad PRO", version: "3.3.0", logo: "static/logo.svg", accent: "#45D0C0",
             accentLight: "oklch(.53 .17 288)", hue: 288, cmd: "kaskadctl setup link", ssh: "gokaskad → «Быстрая настройка»",
             modes: ["classic", "node"], mode: S.mode, welcome: stageDoc("welcome"), steps: RAIL[S.mode].map(stageDoc),
             videos: VIDEOS, extras: EXTRAS.map(function (x) { return Object.assign({}, x); }),
             devices: ["iphone", "android", "windows", "mac", "router"],
             device_platform: { iphone: "ios", android: "android", windows: "windows", mac: "macos", router: "keenetic" },
             platforms: { ios: "iPhone и iPad", android: "Android", androidtv: "Android TV", windows: "Windows", macos: "macOS",
                          linux: "Linux", keenetic: "Keenetic", mikrotik: "MikroTik", openwrt: "OpenWrt", tplink: "TP-Link",
                          huawei: "Huawei", xiaomi: "Xiaomi", asus: "ASUS", other: "Другое" },
             protocol_cards: cards, base_mb: 40, links: { domain_buy: "https://example.com/adminvps", exit_guide: null,
               // партнёрские адреса живут только в product.HOSTING (сервер отдаёт их сам; domain_buy — адрес AdminVPS
               // оттуда же, №47) — в макете заглушки
               hosting: [{ id: "adminvps", name: "AdminVPS", url: "https://example.com/adminvps", region: "РФ и Европа" },
                         { id: "cloudvps", name: "Cloud VPS", url: "https://example.com/cloudvps", region: "Беларусь" }] } };
  }
  function res(state, text, data, error, items) {
    var out = { ok: ["error", "offline", "unavailable"].indexOf(state) < 0, state: state, text: text || "", items: items || [],
                data: data || {} };
    if (error) out.error = error;
    return out;
  }
  function fail(text, code, extra) { return res("error", text, null, Object.assign({ code: code || "error", text: text }, extra || {})); }

  // ── шаги ──
  var HERE = [{ part: "gateway", what: "users", n: 3, text: "3 пользователя" }, { part: "gateway", what: "devices", n: 5, text: "5 устройств" },
    { part: "gateway", what: "inbounds", n: 4, text: "протоколы: AmneziaWG 2.0, AmneziaWG 3.1, WireGuard, L2TP/IPsec" },
    { part: "exits", what: "keys", n: 9, text: "9 выходов" }, { part: "exits", what: "subs", n: 1, text: "1 подписка выходов" },
    { part: "forwards", what: "forwards", n: 1, text: "1 проброс" }, { part: "site", what: "domain", n: 1, text: "домен m6.example.com" }];
  var BAK_EXPIRED = "Копия закрыта: с её открытия прошло больше 15 минут (или сервер перезагружали). Откройте файл копии снова.";
  var STEP = {
    password: function (b) {
      var pw = b.password || "";
      if (pw.length < 10 || pw.length > 200 || pw !== pw.trim()) {
        return fail("Пароль не подходит: пароль: от 10 до 200 символов.", "weak", { field: "password" });
      }
      S.pw = true; S.auth = "password"; if (S.current === "welcome") S.current = RAIL[S.mode][0];
      return res("ok", "Пароль задан. Дальше мастер и панель открываются только по нему.", { login: "admin", revoked_links: 1 });
    },
    scan: function () {
      if (P.get("clean") === "1") return res("ok", "Чистый сервер — начинаем с нуля.", { found: [
          { id: "awgfile", title: "Файл экспорта AmneziaWG", detail: "пользователи, устройства и протоколы AmneziaWG с другого сервера Kaskad 3 (gokaskad → 17)", move: false, upload: true },
          { id: "backup", title: "Копия настроек Kaskad", detail: P.get("soon") !== "1" ? "файл копии со всеми настройками" : SOON, move: false, upload: true, available: P.get("soon") !== "1" }],
        clean: true, here: { items: [], empty: true, text: "" }, checked: "Kaskad 2.x, Amnezia, 3X-UI, WireGuard, goAmnezia", undo: false,
        plan: { summary: { mode: "none", "import": 0, has_work: false }, preview: "", imports: 0, blockers: [] } });
      // узел уже настроен (мастер открыт повторно): ни Kaskad 2.x, ни чужих VPN, но свои люди, выходы, проброс
      if (P.get("here") === "1") return res("ok", "На сервере уже есть: " + HERE.map(function (x) { return x.text; }).join(", ") + ".", { found: [
          { id: "awgfile", title: "Файл экспорта AmneziaWG", detail: "пользователи, устройства и протоколы AmneziaWG с другого сервера Kaskad 3 (gokaskad → 17)", move: false, upload: true },
          { id: "backup", title: "Копия настроек Kaskad", detail: "файл копии со всеми настройками", move: false, upload: true, available: true }],
        clean: false, here: { items: HERE, empty: false, text: HERE.map(function (x) { return x.text; }).join(", ") },
        checked: "Kaskad 2.x, Amnezia, 3X-UI, WireGuard, goAmnezia", undo: false,
        plan: { summary: { mode: "none", "import": 0, has_work: false }, preview: "", imports: 0, blockers: [] } });
      return res("ok", "Мастер проверил сервер.", {
        found: [
          { id: "k2", title: "Kaskad PRO 2.x", detail: "12 пробросов · бот · выход «Прокси для API Telegram» (2)", move: true, done: false },
          { id: "amnezia", title: "Amnezia-контейнеры", detail: "3 контейнера в Docker · продолжат работать как сейчас", move: false },
          { id: "xui", title: "3X-UI", detail: "панель 3X-UI · продолжит работать", move: false },
          { id: "awgfile", title: "Файл экспорта AmneziaWG", detail: "пользователи, устройства и протоколы AmneziaWG с другого сервера Kaskad 3", move: false, upload: true },
          { id: "backup", title: "Копия настроек Kaskad", detail: P.get("soon") !== "1" ? "файл копии со всеми настройками" : SOON,
            move: false, upload: true, available: P.get("soon") !== "1" }],
        clean: false, here: { items: [], empty: true, text: "" }, checked: "Kaskad 2.x, Amnezia, 3X-UI, WireGuard, goAmnezia", undo: !!S.answers.imported,
        plan: { summary: { mode: "switch", "import": 12, duplicate: 0, shadowed: 1, skipped: 0, has_work: true },
                preview: "Перенесу 12 пробросов на nftables. Правило Docker 8080/tcp останется как есть (чужое).",
                imports: 12, blockers: [] } });
    },
    mode: function (b) {
      if (!RAIL[b.mode]) return fail("Режим — node (узел-переводчик) или classic (только пробросы).", "bad_input", { field: "mode" });
      S.mode = b.mode;
      // отметки этапов другого режима остаются (как steps.s_mode, T-M6-qs330b №18)
      if (S.current === "protocols" || S.current === "forward" || (RAIL[b.mode].indexOf(S.current) < 0 && S.current !== "welcome")) S.current = MODE_SLOT[b.mode];
      return res("ok", "Режим: " + (b.mode === "node" ? "узел-переводчик." : "классический Каскад."), { mode: S.mode, rail: rail() });
    },
    "protocols-plan": function () {
      var gh = P.get("github");
      // ?flag warn (сцена «protocols.warn»): жёлтые строки, как на сервере без ядра AmneziaWG 3.1 (снимок владельца 2026-10-12)
      var warnOf = function (k) {
        if (!P.get("warn") || INSTALLED.indexOf(k) >= 0) return "";
        if (/^awg/.test(k)) return "ядро AmneziaWG скачается с GitHub при установке";
        return k === "pptp" ? "PPTP — слабое шифрование (MS-CHAPv2 и MPPE давно взломаны): только для роутеров, которые не умеют ничего другого." : "";
      };
      return res("ok", "", {
        gateway: { supported: true, reason: "", packages: [] },
        protocols: ORDER.map(function (k) {
          var c = CARDS[k], busy = k === "pptp";
          return { kind: k, title: c[0], why: c[1], tags: c[2], mb: c[3], on: c[4], awg: /^awg/.test(k), supported: true,
                   why_not: "", warning: warnOf(k), installed: INSTALLED.indexOf(k) >= 0, kept: false, needs_core: false, needs_key: !!gh && /^awg/.test(k),
                   build: /^(l2tp|sstp|pptp)$/.test(k) ? "accel-ppp" : "", packages: [],
                   port: c[5] ? { field: "port", protos: [c[6]], port: c[5], default: c[5],
                                  conflicts: busy ? [{ kind: "listener", detail: "pptpd слушает 1723/tcp" }] : [], free: null,
                                  random: /^awg/.test(k) && INSTALLED.indexOf(k) < 0 } : null,   // AmneziaWG: порт тянет установка (ops.inbound_plan)
                   fixed: k === "pptp" ? [{ proto: "tcp", port: 1723, conflicts: [{ kind: "listener", detail: "pptpd (pid 812)" }] }] : [] };
        }),
        defaults: ["awg2", "awg3", "wg", "l2tp"],
        module: P.get("module") === "no" ? { can: false, reasons: ["сервер работает в контейнере (OpenVZ или LXC)"], codes: ["container"], build: "",
                    text: "Модуль ядра здесь нельзя (сервер работает в контейнере (OpenVZ или LXC)) — AmneziaWG будет работать программой amneziawg-go." }
          : { can: true, reasons: [], codes: [], build: "1–3 минуты, сервер работает",
                  text: "Соберём модуль ядра AmneziaWG — 1–3 минуты, сервер работает. Вдвое меньше нагрузки на процессор.",
                  // модуль уже в памяти: ?module=ready или после установки AmneziaWG в этом макете
                  ready: P.get("module") === "ready" || INSTALLED.some(function (k) { return /^awg/.test(k); }),
                  ready31: P.get("module") === "ready" || INSTALLED.indexOf("awg3") >= 0, program: false },
        github: gh === "none" ? { state: "none", source: null, from: "", ask: true, text: "Ключа GitHub нет: без него ядро AmneziaWG не скачается." }
          : gh === "bad" ? { state: "bad", source: "config", from: "из команды установки", ask: true, text: "Ключ GitHub не подошёл (отозван или истёк): вставьте новый." }
          : { state: "ok", source: "config", from: "из команды установки", ask: false, text: "Ключ GitHub: найден, из команды установки." },
        base_mb: 40 });
    },
    "github-key": function (b) {
      if (!/^(ghp_|github_pat_)[A-Za-z0-9_]{20,}$/.test(b.key || "")) return fail("Это не похоже на ключ GitHub.", "bad_format", { field: "key" });
      if (/bad/.test(b.key)) return fail("GitHub не принял ключ (ответ 401): он неверный, отозван или истёк.", "bad", { field: "key" });
      if (/net/.test(b.key) && !b.force) return fail("Нет связи с GitHub: ключ не проверен.", "net",
        { field: "key", action: { step: "github-key", force: true, label: "Сохранить без проверки" } });
      return res(b.force ? "partial" : "ok", b.force ? "Ключ сохранён без проверки: GitHub не ответил." : "Ключ GitHub работает и сохранён.");
    },
    "forward-check": function (b) {
      return /^10\./.test(b.to || "") ? res("error", (b.to || "") + ":" + b.to_port + " не отвечает по TCP.", { checked: true }, { code: "unreachable", text: "не отвечает" })
        : res("ok", (b.to || "") + ":" + (b.to_port || 443) + " отвечает · 38 мс — проброс будет работать.", { checked: true, ms: 38 });
    },
    forward: function (b) {
      if (+b.port === 443 && (b.proto || "tcp") === "tcp" && !b.force_443) {
        return res("need_input", "Проброс TCP/443 займёт порт сайта: сайт-маскировка и панель на домене тогда не поднимутся.",
          null, { code: "port443_site", text: "порт 443 нужен сайту", action: { step: "forward", force_443: true, label: "Всё равно 443" } });
      }
      return res("ok", "Проброс " + (b.proto || "tcp") + "/" + b.port + " → " + b.to + ":" + b.to_port + " работает.",
        { forward: { proto: b.proto || "tcp", port: +b.port, to: b.to, to_port: +b.to_port } });
    },
    "exits-choice": function (b) {
      S.answers.exits = b.choice;
      var text = "";
      var off = S.route.via === "direct" || S.route.via === "block";
      S.answers.route_pool_on_add = false;
      if (b.choice === "direct") { S.route.via = "direct"; text = "Интернет у устройств пойдёт с IP этого сервера. Выходы можно добавить позже в панели."; }
      if (b.choice === "exits" && off) { if (S.exits.length) S.route.via = "pool"; else S.answers.route_pool_on_add = true; }   // как steps.s_exits_choice
      if (b.choice === "later") {
        S.stages.exits = "skipped"; S.current = "domain";
        if (S.exits.length && !off) text = "Выходы уже есть — маршрут не меняли: интернет у устройств идёт через них. Этап отмечен как пропущенный: вернуться можно из списка этапов.";
        else { S.route.via = "direct"; text = "Пока интернет у устройств идёт напрямую, с IP этого сервера. Добавите выходы в панели — переключите маршрут на них. Этап отмечен как пропущенный: вернуться можно из списка этапов."; }
      }
      return res("ok", text, { choice: b.choice, route: { via: S.route.via, fail: S.route.fail, from: "default" } });
    },
    "exits-preview": function (b) {
      var t = (b.text || "").trim();
      if (/^https?:\/\//.test(t)) {
        if (P.get("soon") === "1") return res("ok", "", { items: [], subscription: true, sub_available: false, sub_text: SOON });
        var chk = subCheck(t);
        chk.data = Object.assign({ items: [], subscription: true, sub_available: true, sub_text: "" }, chk.data || {});
        return chk;
      }
      if (!/^(vless|vpn):\/\/|\[Interface\]/.test(t)) return fail("Не нашёл ключей: вставьте vless://, vpn:// или текст .conf.", "bad_input", { field: "text" });
      var awg = !/^vless/.test(t), where = "198.51.100.45:" + (awg ? 51820 : 443);
      // тот же сервер в другом виде (vpn:// и .conf, VLESS с другим именем) — «уже в пуле», как exits.peer_key
      var same = S.exits.some(function (e) { return e.where === where && e.kind === (awg ? "awg" : "xray"); });
      return res("ok", same ? "Все ключи уже есть в пуле." : "", { items: [{ id: "e-1a2b3c4d5e", kind: awg ? "awg" : "xray", proto: awg ? "" : "vless", title: awg ? "AmneziaWG 2.0" : "VLESS",
        where: where, name: "Париж", in_pool: same, same_peer: same || undefined }], subscription: false, module_build: awg && !S.exits.length && !same });
    },
    exits: function () {
      var alive = S.exits.filter(function (e) { return e.usable; }).length;
      return res("ok", S.exits.length ? alive + " из " + S.exits.length + " ключей работают" : "Ключей пока нет.",
        { exits: S.exits, alive: alive, total: S.exits.length, route: { via: S.route.via, fail: S.route.fail, from: "default" }, subs: P.get("soon") !== "1",
          sub_rows: P.get("soon") === "1" ? [] : S.subs.map(function (y) { return { id: y.id, name: y.name, auto: y.auto, keys: y.keys, text: y.text,
            stale: !!y.stale, error: !!y.error, exits: S.exits.filter(function (e) { return e.source && e.source.sub === y.id; }).map(function (e) { return e.id; }) }; }) });
    },
    "exit-del": function (b) { S.exits = S.exits.filter(function (e) { return e.id !== b.id; }); return res("ok", "Ключ убран из пула.", { left: S.exits.length }); },
    "exit-sub-del": function (b) {
      var x = S.subs.filter(function (y) { return y.id === b.id; })[0];
      if (!x) return fail("Такой подписки нет — обновите страницу.", "bad_input", { field: "id" });
      S.subs = S.subs.filter(function (y) { return y.id !== b.id; });
      if (b.keep === true) {                           // ключи остаются как добавленные вручную (--keep-keys)
        S.exits.forEach(function (e) { if (e.source && e.source.sub === b.id) delete e.source; });
        return res("ok", "Подписка удалена, её ключи остались в пуле.", { removed: 0 });
      }
      S.exits = S.exits.filter(function (e) { return !(e.source && e.source.sub === b.id); });
      return res("ok", "Подписка удалена вместе с её ключами.", { removed: x.keys });
    },
    route: function (b) {
      var f = b.fail || "block";
      S.answers.route_pool_on_add = false;
      S.route = { via: b.via, fail: f };
      return res("ok", f === "direct" ? "Если выходы упадут, интернет пойдёт с IP этого сервера." : "", { route: { via: b.via, fail: f } });
    },
    domain: function (b) {
      if (b.plan === "none") { S.answers.domain_plan = "none"; return res("ok", "Без домена."); }
      var nm = (b.name || "").toLowerCase();
      S.dnsPolls = 0; S.answers.domain_plan = "have";
      if (working() && nm !== S.domain) return STEP.dns({ name: nm });     // рабочий домен: только проверка, без изменений
      S.answers.domain = nm; delete S.answers.domain_try;
      S.domain = b.name; return STEP.dns();
    },
    dns: function (b) {
      var nm = ((b && b.name) || "").toLowerCase(), sw = working() && !!nm && nm !== S.domain;
      var mode = P.get("dns"), view = mode || (S.dnsPolls++ >= 2 ? "ok" : "wait");
      if (sw) {
        var kept = "Домен " + S.domain + " работает как раньше — на сервере ничего не менялось.";
        var adv2 = { cloudflare: "Похоже, у домена в Cloudflare включено проксирование (оранжевое облако). Переключите запись в «DNS only».",
                     elsewhere: "Домен указывает на 192.0.2.3, а адрес этого сервера — " + IP + ": поправьте A-запись.",
                     none: "У домена " + nm + " нет A-записи: добавьте A-запись со значением " + IP + "." }[view] || "";
        S.next = view === "ok" ? nm : null;
        // как steps._domain_try: рабочий домен остаётся в answers.domain, новое имя — последней попыткой (qs330e №3)
        S.answers.domain = S.domain; S.answers.domain_try = { name: nm, state: view, why: "", at: new Date().toISOString() };
        return res(view === "ok" ? "ok" : "checking", adv2 ? adv2 + " " + kept : (view === "ok" ? "Домен смотрит на этот сервер." : "Жду DNS…"),
          { dns: view, dns_state: view === "wait" ? "unknown" : view, ready: view === "ok", a: view === "ok" ? [IP] : view === "elsewhere" ? ["192.0.2.3"] : [], own: [IP],
            advice: adv2, checked_at: new Date().toISOString(), hint_after: 900, kept: S.domain, switch: true });
      }
      var adv = { cloudflare: "Похоже, у домена в Cloudflare включено проксирование (оранжевое облако). Переключите запись в «DNS only».",
                  elsewhere: "Домен указывает на 192.0.2.3, а адрес этого сервера — " + IP + ": поправьте A-запись.",
                  none: "У домена нет A-записи: добавьте A-запись со значением " + IP + "." }[view] || "";
      return res(view === "ok" ? "ok" : "checking", adv || (view === "ok" ? "Домен смотрит на этот сервер." : "Жду DNS…"),
        { dns: view, dns_state: view === "wait" ? "unknown" : view, ready: view === "ok", a: view === "ok" ? [IP] : view === "elsewhere" ? ["192.0.2.3"] : [], own: [IP],
          advice: adv, checked_at: new Date().toISOString(), hint_after: 900 });
    },
    templates: function (b) {
      var all = [{ id: "photo-rakurs", name: "Фотостудия «Ракурс»", category: "photo" }, { id: "cafe-zerno", name: "Кофейня «Зерно»", category: "food" },
        { id: "lawyer", name: "Юрист Лебедева", category: "services" }, { id: "bakery", name: "Пекарня «Утро»", category: "food" },
        { id: "tyres-24", name: "Шиномонтаж 24", category: "auto" }, { id: "barber", name: "Барбершоп «Бритва»", category: "services" },
        { id: "photo-folio", name: "Портфолио фотографа", category: "photo" }, { id: "garden-blog", name: "Блог о садах", category: "blog", gone: true }];
      // как сервер: страницы по 25 (каталог большой — «Показать ещё»), категории со счётчиками; tpl=nokey — нужен ключ GitHub
      if (P.get("tpl") === "nokey") return fail("Каталог шаблонов сайта лежит в закрытом репозитории Kaskad, и без ключа GitHub его не скачать. Задайте ключ и повторите.", "github_key", { action: { step: "github-key" } });
      var kinds = [["services", "Студия"], ["food", "Кафе"], ["photo", "Фотограф"], ["auto", "Автосервис"], ["blog", "Блог"]];
      for (var n = 1; n <= 64; n++) { var k = kinds[n % kinds.length]; all.push({ id: "mock-" + k[0] + "-" + n, name: k[1] + " №" + n, category: k[0] }); }
      var cats = [{ id: "services", title: "Услуги" }, { id: "food", title: "Еда" }, { id: "photo", title: "Фото" }, { id: "auto", title: "Авто" }, { id: "blog", title: "Блоги" }]
        .map(function (c) { return Object.assign(c, { count: all.filter(function (x) { return x.category === c.id; }).length }); });
      if (b.category && !cats.some(function (c) { return c.id === b.category; })) return fail("Категории «" + b.category + "» в каталоге нет.", "unknown_category");
      var q = (b.query || "").toLowerCase(), items = all.filter(function (x) { return (!b.category || x.category === b.category) && (!q || x.name.toLowerCase().indexOf(q) >= 0); });
      var per = 25, pages = Math.max(1, Math.ceil(items.length / per)), page = Math.max(1, Math.min(pages, +b.page || 1));
      return res("ok", "", { page: page, pages: pages, per_page: per, total: items.length, category: b.category || null, query: q || null,
        items: items.slice((page - 1) * per, page * per), categories: cats });
    },
    "extras-list": function () {
      var soon = P.get("soon") === "1";
      return res("ok", "", { items: product().extras.map(function (x) {
        var avail = !(soon && x.id === "backup");
        var it = Object.assign({}, x, { available: avail, text: avail ? "" : SOON });
        if (x.id === "backup") it.fields = ["password"];
        return it;
      }) });
    },

    backup: function (b) {
      if (P.get("soon") === "1") return res("unavailable", SOON, null, { code: "soon", text: SOON });
      if (!b.password) return fail("Задайте пароль копии.", "bad_input", { field: "password" });
      return res("ok", "Копия настроек готова: сохраните файл и запишите пароль.", { file: "KASKAD-BACKUP demo-not-a-real-copy", name: "kaskad-203.0.113.67-2026-10-09.kbk", size: 2480, parts: ["gw", "forwards", "bot", "panel"] });
    },
    "backup-open": function (b) {
      if (P.get("soon") === "1") return res("unavailable", SOON, null, { code: "soon", text: SOON });
      if (b.drop) { S.bak = null; return res("ok", "Копию закрыли."); }
      if (b.ticket) return S.bak && S.bak.ticket === b.ticket ? res("ok", "Копия открыта: посмотрите, что изменится.", S.bak) : fail(BAK_EXPIRED, "expired");
      if (!b.file) return fail("Выберите файл копии (.kbk).", "bad_input", { field: "file" });
      if (!b.password) return fail("Введите пароль копии.", "bad_input", { field: "password" });
      if (b.password === "wrong-wrong") return fail("Неверный пароль копии.", "password", { field: "password" });
      var empty = P.get("here") !== "1";
      S.bak = { ticket: "0123456789abcdef0123456789abcdef", expires: Math.floor(Date.now() / 1000) + 900, created: "2026-10-01T09:12:00Z", version: "3.3.0",
        parts: [{ part: "gateway", title: "шлюз", action: empty ? "add" : "replace", text: "пользователей 2, устройств 4, протоколы: awg2, wg" + (empty ? "" : "; заменит здесь: пользователей 3, устройств 5, протоколы: awg2, awg3, wg, l2tp") },
          { part: "exits", title: "выходы", action: empty ? "add" : "replace", text: "ключей 4, подписок 1" + (empty ? "" : "; заменит здесь: ключей 9, подписок 1") },
          { part: "forwards", title: "пробросы", action: empty ? "none" : "replace", text: "пробросов 0, имён серверов 0" + (empty ? "" : "; заменит здесь пробросов: 1") },
          { part: "site", title: "домен", action: "add", text: "домен vpn.example.com; сертификат выпускается заново, когда домен укажет на этот сервер" },
          { part: "bot", title: "бот", action: "none", text: "в копии бота нет" },
          { part: "mcp", title: "ключи агентов", action: "none", text: "ключей агентов 1 — мастер их не переносит: агент с таким ключом управляет сервером. Перенести можно позже и только из своей копии: панель «Настройки» → «Загрузить копию» с отметкой «Перенести ключи ИИ-агентов из копии» или меню 16 → 6 → 2" },   // REV-3372-2 З-3: the words of the wizard (no tick here)
          { part: "panel", title: "панель", action: "replace", text: "пароль спрашивать: да; пароль панели не переносится" },
          { part: "github", title: "ключ GitHub", action: "keep", text: "ключ GitHub; здесь уже свой — останется он" }],
        lost: empty ? [] : [{ part: "gateway", what: "users", n: 1, text: "1 пользователь: Петя" },
          { part: "gateway", what: "devices", n: 2, text: "2 устройства: Ноутбук (Петя), Телевизор (Мама)" },
          { part: "gateway", what: "inbounds", n: 2, text: "протоколы: AmneziaWG 3.1, L2TP/IPsec" },
          { part: "exits", what: "keys", n: 5, text: "5 выходов: Вена, Рига, AmneziaWG · 198.51.100.121, VLESS · 198.51.100.23, Берлин" },
          { part: "forwards", what: "forwards", n: 1, text: "1 проброс: tcp 8443 → 198.51.100.23:22" }],
        changed: empty ? [] : [{ part: "gateway", what: "route", text: "маршрут по умолчанию: через выходы, при отказе — блок → напрямую" }],
        empty: empty, confirm: !empty,
        warnings: ["Устройства подключаются по адресу 192.0.2.4, а у этого сервера — " + IP + ": после переноса выдайте им настройки заново (меню 17 → устройства или панель «Пользователи»)."],
        blockers: [], address: { old: "192.0.2.4", here: IP, kind: "ip", changed: true, warnings: [] } };
      return res("ok", empty ? "Копия открыта: посмотрите, что перенесётся." : "Копия открыта: посмотрите, что изменится, и подтвердите перенос.", S.bak);
    },
    // «Россия напрямую» с галочкой ⚠РФ (R7): новый узел — всё через выход; ru_direct без confirm_not_rf — need_input
    rules: function (b) {
      var R = S.rules = S.rules || { profile: "all_exit", consent: false };
      var view = function () {
        return { profile: R.profile, ads: true, torrent: false, rf: { required: !R.consent, mode: R.profile === "ru_direct" ? (R.consent ? "full" : "need") : "none",
          orgs: ["meta", "fbk"], names: "Meta, ФБК", block: "⚠ Данная организация признана экстремистской на территории Российской Федерации (Meta, ФБК)",
          consent: "Скрипт не используется на территории РФ", uncovered: [] } };
      };
      // с галочкой — и коды показанных организаций (orgs), иначе тоже need_input (SEC-s1-r67 №1)
      if (b.ru_direct === true && !R.consent && (b.confirm_not_rf !== true || !Array.isArray(b.orgs) || b.orgs.length < 2)) {
        var v = view(), text = v.rf.block + ". Подтвердите: «" + v.rf.consent + "».";
        return res("need_input", text, Object.assign(v, { need: { need: "rf_consent", orgs: v.rf.orgs, names: v.rf.names, block: v.rf.block, consent: v.rf.consent } }),
          { code: "rf_consent", text: text, field: "confirm_not_rf" });
      }
      if (b.ru_direct === true) { R.profile = "ru_direct"; R.consent = true; }
      if (b.ru_direct === false) { R.profile = "all_exit"; R.consent = false; }   // всё через выход — согласие сброшено (§3.4)
      return res("ok", "ru_direct" in b ? "Правила сохранены." : "", view());
    },
    platforms: function (b) {
      if (b.platform) {
        var pl = OFFERS[b.platform] ? b.platform : "other";
        return res("ok", "", { platform: pl, offers: OFFERS[pl].map(function (o) {
          return { inbound: o[0], format: o[1], note: o[2], title: TITLES[o[0]], installed: INSTALLED.indexOf(o[0]) >= 0, enabled: INSTALLED.indexOf(o[0]) >= 0 };
        }), notes: OFFER_NOTES[pl] || [] });
      }
      var pt = product().platforms;
      return res("ok", "", { platforms: ["ios", "android", "androidtv", "windows", "macos", "linux", "keenetic", "mikrotik", "openwrt", "tplink", "huawei", "xiaomi", "asus", "other"]
        .map(function (k) { return { id: k, title: pt[k] }; }) });
    },
    person: function (b) {
      var id = "d-" + (0x0a1b2c3d + S.devices.length).toString(16);
      S.devices.push({ id: id, name: b.device || "", user: b.user || "", platform: b.platform || "other" });
      S.answers.device = id; S.answers.device_user = b.user || ""; S.answers.device_platform = b.platform || "other"; S.connSince = NOW();
      var offers = (OFFERS[b.platform] || OFFERS.other).filter(function (o) { return INSTALLED.indexOf(o[0]) >= 0; }).map(function (o) { return o[0]; });
      if (b.inbound && offers.indexOf(b.inbound) >= 0) offers = [b.inbound].concat(offers.filter(function (k) { return k !== b.inbound; }));
      if (!offers.length) return fail("Для этой платформы нет установленных протоколов — вернитесь на этап «Режим и протоколы».", "no_inbound", { action: { stage: "protocols" } });
      return res("ok", "Устройство «" + (b.device || "") + "» создано.", { user: "u-1a2b3c4d", device: id, access: offers.slice(0, 3), platform: b.platform || "other", offers: [] });
    },
    grant: function (b) {
      if (INSTALLED.indexOf(b.inbound) < 0) return fail("Для этой платформы нет установленных протоколов — вернитесь на этап «Режим и протоколы».", "no_inbound", { action: { stage: "protocols" } });
      return res("ok", "", { device: b.device || S.answers.device, inbound: b.inbound, access: [b.inbound] });
    },
    issue: function (b) {
      var dev = S.devices.filter(function (d) { return d.id === b.device; })[0] || { id: b.device, name: "Устройство", platform: "ios" };
      var os = b.os || dev.platform, k = b.inbound || "awg2", slug = "mama-" + os, items;
      var phone = os === "ios" || os === "android";
      if (k === "wg") {                                  // как wg.py: QR файла .conf и сам файл
        var wconf = "[Interface]\nPrivateKey = demo-not-a-real-key\nAddress = 10.66.0.2/32\n";
        items = [{ format: "qr", title: "WireGuard — QR-код", body: [wconf], filename: "", notes: ["Для приложения WireGuard на телефоне."] },
          { format: "conf", title: "WireGuard — файл .conf", body: wconf, filename: "gKP-telefon-wg.conf", notes: [] }];
      } else if (/^awg/.test(k)) {                       // как awg.py: vpn://, его части для QR, QR файла .conf, файл .conf
        var line = { awg1: "1.5", awg2: "2.0", awg3: "3.1" }[k], pf = partsFor(line), n = pf.n;
        var link = "vpn://AAAD3nic-demo-" + k + "-not-a-real-key-" + slug;
        var conf = awgConfText(line, "10.66.0.2", IP, 40443);
        items = [{ format: "vpn", title: "AmneziaWG " + line + " — ссылка vpn://", body: link, filename: "", notes: ["AmneziaVPN: «+» → «Вставить» (ключ из буфера) → «Продолжить»."] },
          { format: "qr", title: "AmneziaWG " + line + " — QR для AmneziaVPN", body: qrParts(n, k + slug, pf.len), filename: "", notes: n > 1 ? ["Кодов: " + n + ", сканируйте в любом порядке."] : [] },
          { format: "qr", title: "AmneziaWG " + line + " — QR файла .conf", body: [conf], filename: "", notes: ["Для AmneziaWG и других приложений, которые читают .conf."] },
          { format: "conf", title: "AmneziaWG " + line + " — файл .conf", body: conf, filename: "goKaskadPro-telefon-awg" + line.replace(".", "") + ".conf", notes: line === "3.1" ? ["Приложение Amnezia теряет поля 3.x при импорте .conf — в нём только vpn:// или его QR."] : [] }];
        if (!phone && os !== "macos" && os !== "windows") items = [items[3]];
      } else if (k === "openvpn") {
        items = [{ format: "ovpn", title: "OpenVPN — файл .ovpn", body: "client\nremote " + IP + " 1194 udp\n", filename: slug + ".ovpn", notes: [] }];
      } else if (k === "ikev2" && (os === "ios" || os === "macos")) {
        items = [{ format: "mobileconfig", title: "IKEv2 — профиль .mobileconfig", body: "<?xml version=\"1.0\"?><plist/>", filename: slug + ".mobileconfig", notes: [] }];
      } else {
        var body = [["Сервер", IP + (k === "sstp" ? ":4443" : k === "ocserv" ? ":8443" : "")], ["Логин", slug], ["Пароль", "Zx7q-Demo-Pass"]];
        if (k === "l2tp") body.push(["Тип", "L2TP/IPsec с общим ключом"], ["Общий ключ IPsec (PSK)", "demo-psk-123456"]);
        if (k === "socks5") body.splice(1, 0, ["Порт", "1080"]);
        items = [{ format: "fields", title: TITLES[k], body: body, filename: "", notes: [] }];
      }
      S.answers.issued_kind = k; S.connSince = NOW();
      var howto = HOWTO[k + ":" + os] || [];             // как issue_os.howto(os, kind, where="panel")
      var sup = window.KaskadMockSupport ? window.KaskadMockSupport(k, os, document.documentElement.lang, INSTALLED.slice(), [k]) : null;
      return res("ok", "", { device: dev.id, name: dev.name, kind: k, os: os, items: items, available: INSTALLED.slice(), howto: howto,
        install: window.KaskadMockInstall ? window.KaskadMockInstall(k, os, document.documentElement.lang) : null,   // настоящие каталоги (tools/gen_mock_install.py)
        support: sup ? sup.support : null, alternatives: sup ? sup.alternatives : [] });
    },
    connection: function () {
      var t = NOW() - S.connSince, conn = P.get("conn") === "stuck" ? "stuck" : P.get("conn") === "wait" ? "wait" : (t > 9 ? "traffic" : t > 6 ? "connected" : "wait");
      var data = { conn: conn, since: S.connSince, online_since: conn === "wait" || conn === "stuck" ? null : S.connSince + 6, inbound: S.answers.issued_kind || "awg2",
                   bytes: conn === "traffic" ? 1250000 : 0, stuck_after: 120 };
      if (conn === "stuck") data.hints = [
        { code: "port", text: "Хостер мог закрыть порт протокола: откройте его в файрволе хостера (в панели хостера, раздел «Сеть» или «Firewall»)." },
        { code: "line31", text: "Приложение может не знать линию 3.1 — выдадим AmneziaWG 2.0.", action: { step: "issue", inbound: "awg2", label: "Выдать 2.0" } },
        { code: "udp", text: "Мобильная сеть может резать UDP — попробуйте Wi-Fi или выдайте протокол по TCP.", action: { step: "issue", inbound: "sstp", label: "Выдать по TCP" } }];
      return res(conn === "connected" || conn === "traffic" ? "ok" : "checking",
        { wait: "Жду подключения…", stuck: "Устройство пока не подключилось.", connected: "Устройство подключилось.", traffic: "Трафик идёт." }[conn], data);
    },
    trace: function (b) {
      // форма ответа — как у kaskad.gw.trace.trace (steps.s_trace): verdict, summary, origin, steps, notes, exit, exits, who, route, highlight
      var ru = b.target === "ru", host = ru ? "ya.ru" : "youtube.com", dev = S.devices.filter(function (d) { return d.id === (b.device || S.answers.device); })[0];
      var alive = S.exits.filter(function (e) { return e.usable; }), via = S.route.via;
      var verdict = ru ? "direct" : via === "direct" ? "direct" : via === "block" ? "block" : alive.length ? "exit" : (S.route.fail === "direct" ? "direct" : "block");
      var ex = verdict === "exit" ? { id: alive[0].id, name: alive[0].name, text: alive[0].text } : null;
      var steps = [{ text: (dev ? "Устройство «" + dev.name + "»" : "Новое устройство с настройками по умолчанию") + ", профиль «Россия напрямую» (по умолчанию)", note: "через протокол " + TITLES[S.answers.issued_kind || "awg2"], tone: "" },
        { text: host + (ru ? " — список «напрямую»" : " — нет в списках"), note: "", tone: "" },
        { text: "Маршрут: " + (via === "pool" ? "пул выходов" : via === "direct" ? "напрямую" : via) + " (по умолчанию)", note: via === "pool" ? "при отказе выхода — " + (S.route.fail === "direct" ? "напрямую" : "блок") : "", tone: "" }];
      if (verdict === "exit") steps.push({ text: "Сейчас выход «" + ex.name + "» — " + ex.text, note: "", tone: "ok" });
      if (verdict === "block") steps.push({ text: "В пуле нет живых выходов — блок", note: "политика отказа «блок»: закрыто всё, и сайты России тоже", tone: "bad" });
      return res("ok", "", { ok: true, query: { input: host, kind: "domain", value: host }, verdict: verdict, reason: verdict === "exit" ? "route" : verdict === "block" ? "fail_block" : "route_direct",
        summary: verdict === "exit" ? "Через выход «" + ex.name + "»" : verdict === "block" ? "Заблокировано: в пуле нет живых выходов" : "Напрямую, с адреса этого сервера",
        origin: verdict === "exit" ? "Сайт откроется с адреса выхода «" + ex.name + "»" : verdict === "block" ? "Сайт не откроется" : "Сайт откроется напрямую, с адреса этого сервера (" + IP + ")",
        steps: steps, notes: [], exit: ex, exits: ex ? [ex] : [],
        who: { kind: dev ? "device" : "new", id: dev ? dev.id : null, name: dev ? dev.name : null, user: null, active: true, inbounds: INSTALLED.slice(), inbound: S.answers.issued_kind || "awg2", socks: false, kernel: true },
        profile: { profile: "ru_direct", from: "default", applied: "ru_direct" }, route: { via: via, fail: S.route.fail, from: "default" }, match: null, addresses: [], resolve: null, server: IP,
        highlight: { nodes: [], edges: [], devices: dev ? [dev.id] : [], category: ru ? "ru" : "rest", via: via, target: verdict === "exit" ? "pool" : verdict, rows: {}, inbound: S.answers.issued_kind || "awg2" },
        target: b.target || "foreign", host: host });
    },
    summary: function () {
      var MOCK_PORTS = { awg2: ["42060/udp"], awg3: ["43304/udp"], wg: ["51820/udp"], l2tp: ["1701/udp", "500/udp", "4500/udp"], vless: ["443/tcp"], pptp: ["1723/tcp"] };
      var users = {};
      S.devices.forEach(function (d) { (users[d.user] = users[d.user] || []).push(d.name); });
      return res("ok", "", { panel_url: "https://" + IP + ":41234/", protocols: INSTALLED.slice(), titles: INSTALLED.reduce(function (o, k) { o[k] = TITLES[k] || k; return o; }, {}),
        exits: { names: S.exits.filter(function (e) { return !e.source; }).map(function (e) { return e.name; }),
          subs: S.subs.map(function (y) { return { name: y.name, keys: y.keys }; }),
          alive: S.exits.filter(function (e) { return e.usable; }).length, total: S.exits.length },
        // online — факт сборщика, как steps._online_of (тот же, что «Устройство подключилось»)
        people: Object.keys(users).map(function (u, i) { return { id: "u-" + (0x1a2b3c4d + i).toString(16), name: u, devices: users[u],
          online: P.get("conn") === "stuck" || P.get("conn") === "wait" || NOW() - S.connSince <= 6 ? [] : users[u].slice(0, 1) }; }), domain: S.domain || null,
        skipped: Object.keys(S.stages).filter(function (k) { return S.stages[k] === "skipped"; }),
        github: { state: "ok", source: "config", from: "из команды установки", ask: false, text: "Ключ GitHub: найден, из команды установки." },
        // как steps._host_ports: настоящие порты сервера для «Откройте порты у хостера»
        ports: { inbounds: INSTALLED.map(function (k) { return Object.assign({ kind: k, title: TITLES[k] || k, ports: MOCK_PORTS[k] || [] }, k === "pptp" ? { ip_protos: [{ name: "gre", num: 47 }] } : {}); }),
                 forwards: S.mode === "classic" && S.answers.forward ? [{ proto: S.answers.forward.proto, port: S.answers.forward.port }] : [],
                 domain: S.domain || null, panel: 41234 } });
    },
    // короткий шаг (setup.js зовёт его без задания), как steps.s_site_email
    "site-email": function (b) {
      var e = String(b.email || "").trim();
      if (e && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(e)) return fail("Почта — адрес вида admin@example.com.", "bad_input", { field: "email" });
      S.acmeEmail = e;
      return res("ok", e ? "Почта для Let's Encrypt: " + e + "." : "Почта для Let's Encrypt убрана.", { email: e, warnings: [] });
    },
    finish: function () { return res("ok", "Мастер закрыт. Его адрес больше не открывается."); }
  };

  // ── долгие шаги: строки по таймеру ──
  var PLAN = {
    "import": function () { return [["forwards", "Пробросы портов", "переношу пробросы на nftables", "ok", "12 пробросов перенесено"],
      ["bot", "Бот в Telegram", "проверяю", "ok", "остаётся как был — настройки те же"],
      ["egress", "Выход «Прокси для API Telegram»", "проверяю", "ok", "ключи остаются в пуле — их видно на этапе «Выходы»"],
      ["w0", "Замечание", "", "warn", "правило Docker 8080/tcp оставлено как есть (чужое)"]]; },
    "import-undo": function () { return [["undo", "Вернуть как было", "возвращаю правила iptables", "ok", "пробросы снова в iptables"]]; },
    "import-file": function (b) { return b.kind === "backup" && P.get("bak") === "changed" ? [] : b.kind === "backup" ? [["backup", "Копия настроек", "переношу настройки из копии", "ok", "настройки перенесены"]]
      : [["awg", "Файл экспорта AmneziaWG", "переношу", "ok", "протоколов 2, пользователей 3, устройств 5"]]; },
    protocols: function (b) {
      var rows = [["gateway", "Шлюз", "включаю шлюз: DNS для устройств, правила, модули", "ok", "включён"]];
      var kinds = (b.kinds || []).slice();
      if (kinds.some(function (k) { return /^awg/.test(k); })) rows.push(["module", "Модуль ядра AmneziaWG", "собираю модуль… обычно 1–3 минуты", "ok", "модуль ядра работает"]);
      kinds.forEach(function (k) {
        var c = CARDS[k] || [k];
        rows.push(k === "pptp" ? [k, c[0], "ставлю…", "err", "PPTP не установлен: порт 1723/tcp занят другой программой — pptpd (pid 812)."] : [k, c[0], "ставлю…", "ok", "установлен" + (c[5] ? " · порт " + c[5] : k === "l2tp" ? " · порты 1701, 500, 4500/udp" : k === "ikev2" ? " · порты 500, 4500/udp" : "")]);
      });
      return rows;
    },
    "exits-add": function (b) {
      if (/^https?:\/\//i.test((b.text || "").trim())) return [["sub", "Подписка", "скачиваю подписку и проверяю ключи", "ok", "Подписка добавлена."]];
      var awg = !/^vless/.test((b.text || "").trim());
      var rows = awg && !S.exits.length ? [["module", "Модуль ядра AmneziaWG", "собираю модуль ядра — 1–3 минуты, сервер работает", "ok", "готов"]] : [];
      rows.push(["add", "Ключ выхода", "добавляю ключ", "ok", "Ключ добавлен."]);
      return rows;
    },
    cert: function (b) {
      var nm = ((b && b.name) || "").toLowerCase();
      if (working() && nm && nm !== S.domain) {          // смена рабочего домена: как site.ops.change_domain
        return [["dns", "DNS нового домена", "куда ведёт A-запись " + nm, "ok", nm + " → " + IP],
                ["cert", "Сертификат нового домена", "Let's Encrypt проверяет " + nm + " через порт 80; " + S.domain + " пока работает", "ok", "выпущен · до 2027-01-07"],
                ["switch", "Переключение домена", S.domain + " → " + nm, "ok", nm + " работает; " + S.domain + " снят"]];
      }
      return [["cert", "Сертификат домена", "Let's Encrypt проверяет домен через порт 80", "ok", "выпущен · до 2027-01-07"]];
    },
    site: function () { return [["site", "Сайт-маскировка", "ставлю nginx, сайт и панель на домене", "ok", "сайт открыт"]]; },
    template: function () { return [["tpl", "Шаблон сайта", "скачиваю и проверяю шаблон", "ok", "Кофейня «Зерно»"]]; },
    "vless-mode": function () { return [["vless", "VLESS", L("меняю режим VLESS", "changing the VLESS mode"), "ok", L("готово", "done")]]; }
  };
  var RESULT = {
    "import": function () { S.answers.imported = true; return res("partial", "Перенесено: 12 пробросов.", { imported: 12, backup: "/var/backups/kaskad/demo", warnings: ["правило Docker 8080/tcp оставлено как есть (чужое)"], undo: true }); },
    "import-undo": function () { S.answers.imported = false; return res("ok", "Вернули как было: пробросы снова в iptables."); },
    "import-file": function (b) {
      if (b.kind === "backup") {
        if (!b.ticket) return fail("Сначала откройте копию — покажем, что изменится, и только потом перенесём.", "preview_first", { field: "file" });
        if (!S.bak || S.bak.ticket !== b.ticket) return fail(BAK_EXPIRED, "expired");
        if (S.bak.confirm && b.confirm !== true) return fail("Перенос заменит то, что уже есть на сервере, — подтвердите это.", "confirm");
        if (P.get("bak") === "changed") return fail("На сервере что-то изменилось после предпросмотра — посмотрите, что изменится, ещё раз.", "changed", { data: { ticket: b.ticket } });
        S.bak = null;
        return res("ok", "Настройки из копии перенесены.", { parts: ["gateway", "exits", "site", "panel"], backup: "/var/backups/kaskad/demo", warnings: [], text: "", undo: true });
      }
      return res("ok", "Перенесено из файла.", { inbounds: ["awg2"], users: [], devices: [], warnings: [] }); },
    protocols: function (b) {
      var bad = (b.kinds || []).filter(function (k) { return k === "pptp"; }), ok = (b.kinds || []).filter(function (k) { return k !== "pptp"; });
      ok.forEach(function (k) { if (INSTALLED.indexOf(k) < 0) INSTALLED.push(k); });
      return bad.length ? res("partial", "Установлено " + ok.length + " из " + (b.kinds || []).length + ".", { installed: ok, failed: [{ kind: "pptp", code: "port_busy", text: "PPTP не установлен: порт 1723/tcp занят другой программой — pptpd (pid 812).", title: "PPTP", port: 1723, protos: "tcp", who: "pptpd (pid 812)", cmd: "systemctl disable --now pptpd" }] })
        : res("ok", "Все протоколы установлены.", { installed: ok });
    },
    "exits-add": function (b) {
      var vless = /^vless/.test((b.text || "").trim()), n = S.exits.length;
      if (S.answers.route_pool_on_add) { S.answers.route_pool_on_add = false; S.route.via = "pool"; }
      if (/^https?:\/\//i.test((b.text || "").trim())) {                       // подписка: повтор — не ошибка
        var was = subOf(b.text);
        if (was) return res("ok", subAlready(was), { already: true });
        var chk = subCheck(b.text);
        if (!chk.ok) return chk;
        subAdd(b.text);
        return res("ok", "Подписка добавлена.", { result: { ok: true } });
      }
      var where = "198.51.100.45:" + (vless ? 443 : 51820);
      if (S.exits.some(function (e) { return e.where === where && e.kind === (vless ? "xray" : "awg"); })) {
        return fail("Этот выход уже в пуле: то же подключение к тому же серверу, только записано иначе — например, vpn:// и .conf одного подключения или ссылка VLESS с другим именем. Второй раз не добавляю.", "conflict");
      }
      S.exits.push({ id: "e-" + (1000000000 + n), name: ["Париж", "Хельсинки", "Токио"][n % 3], kind: vless ? "xray" : "awg", proto: vless ? "vless" : "",
                     title: vless ? "VLESS" : "AmneziaWG 2.0", where: "198.51.100.45:" + (vless ? 443 : 51820), enabled: true,
                     usable: n !== 2, handshake: vless ? null : 12, state: n !== 2 ? "ok" : "err",
                     text: n !== 2 ? "работает, рукопожатие 12 с назад" : "не работает: нет ответа 35 с — клиенты переходят на другой выход" });
      return res("ok", "Ключ добавлен.", { added: [{ id: "e-" + (1000000000 + n), name: "Париж" }], results: [], warnings: [] });
    },
    cert: function (b) {
      var nm = ((b && b.name) || "").toLowerCase();
      if (working() && nm && nm !== S.domain) {
        var was = S.domain;
        S.domain = S.certDomain = nm; S.answers.domain = nm; delete S.answers.domain_try; S.next = null;
        // как site.ops._reissue_warning: VLESS сменил режим — выданные ссылки не подключатся (qs330e №1); reissue — кому
        var dev = S.answers.device || "d-0000beef", who = S.answers.device ? (S.answers.device_user || "Мама") + " · " + "iPhone" : "Телефон (Мама)";
        var warn = "VLESS перешёл на TLS со своим доменом " + nm + " (порт 443) — выданные раньше ссылки VLESS не подключатся. Выдайте их заново (меню 17 → 2 → 1 или панель → «Пользователи»): " + who + ".";
        return res("ok", "Домен сменён: " + was + " → " + nm + ".", { cert: { names: [nm], not_after: "2027-01-07T10:00:00Z", days_left: 89, staging: false, issuer: "R11" },
          use_impact: { sstp: 0, ocserv: 0 }, consumers: [], warnings: [warn], reissue: [{ id: dev, name: who }], changed_from: was, domain: nm, site_on: S.siteMode === "site",
          template: S.siteMode === "site" ? { id: "cafe-zerno", name: "Кофейня «Зерно»" } : null,
          vless: { security: "tls", changed: true, port: 443, text: "VLESS перешёл на TLS со своим доменом " + nm + " (порт 443)", error: "" } });
      }
      if (P.get("le") === "limit") {                   // как site.acme: срок по часам сервера (retry_text) и вид лимита
        var at = new Date(Date.now() + 29 * 3600 * 1000), iso = at.toISOString().slice(0, 19) + "Z";
        var when = iso.slice(0, 16).replace("T", " ") + " UTC (через 29 ч)", d = S.domain || "k3.example.com";
        if (working() && nm && nm !== S.domain) { d = nm; S.answers.domain_try = { name: nm, state: "error", why: "limit", at: new Date().toISOString() }; S.next = null; }
        var text = "Let's Encrypt временно не выдаёт сертификаты домену " + d + " (лимит). Повторить можно " + when + ". Сервер в порядке — это ограничение Let's Encrypt: не больше пяти одинаковых сертификатов на одно имя за неделю. Не хотите ждать — возьмите другое имя (например, поддомен vpn2." + d + "): для нового имени лимит отдельный, нужна только его A-запись на этот сервер.";
        return res("error", text, { retry_after: iso }, { code: "limit", text: text, retry_after: iso, retry_text: when, limit: "dup" });
      }
      S.certDomain = S.domain || "k3.example.com"; return res("ok", "Сертификат Let's Encrypt выпущен.", { cert: { names: [S.domain || "k3.example.com"], not_after: "2027-01-07T10:00:00Z", days_left: 89, staging: false, issuer: "R11" }, use_impact: { sstp: 0, ocserv: 0 }, consumers: [], warnings: [],
        // как steps._email_split: «почта не задана» — отдельно от прочих предупреждений
        email: S.acmeEmail ? { need: false } : { need: true, text: NO_EMAIL } }); },
    "vless-mode": function (b) { return vlessMode(b || {}); },
    site: function (b) {
      var price = vlessPrice(b || {}, "site");
      if (price) return price;
      S.siteMode = "site"; if (S.vless) { S.vless.security = "tls"; S.vless.want = "tls"; }
      return res("ok", "На домене — обычный сайт; панель переехала на новый адрес.", { panel_url: "https://" + (S.domain || "k3.example.com") + "/app-DEMOdemoDEMOdemo/", template: { id: "cafe-zerno", name: "Кофейня «Зерно»" }, warnings: [] }); },
    template: function () { return res("ok", "", { id: "cafe-zerno", name: "Кофейня «Зерно»", category: "food", fallback: false }); }
  };

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
  // + кусок, base64url; данные — выдуманные (не ключ), размер — partsFor
  function qrParts(n, seed, len) {
    var out = [], i, j, bin, x = 7;
    len = len || 300;
    for (i = 0; i < seed.length; i++) x = (x * 31 + seed.charCodeAt(i)) % 65521;
    for (i = 0; i < n; i++) {
      bin = String.fromCharCode(7, 192, n, i, 0, 0, len >> 8, len & 255);
      for (j = 0; j < len; j++) { x = (x * 1103 + 12345) % 65521; bin += String.fromCharCode(x & 255); }
      out.push(btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, ""));
    }
    return out;
  }
  var NO_EMAIL = "Почта для Let's Encrypt не задана — важные письма Let's Encrypt (об отзыве сертификата, об изменении условий) приходить не будут. За сроком сертификата Kaskad следит сам: продлевает его заранее, а если за 21 день до конца продлить не вышло — сообщает в Telegram.";
  function working() { return !!P.get("have") && !!S.domain && !!S.certDomain && S.certDomain === S.domain; }

  // ── VLESS Ш4: как gw.ops.vless_view / vless_mode и цена site.ops (SPEC-vless-tls-default §3.2, §3.7, §4.3) ──
  // Тексты ядра — на языке запроса (X-Kaskad-Lang), как сервер: dev-mock-en.js их не переводит
  var VL_SITE = "news.example.com", REQ_EN = false;
  var L = function (ru, en) { return REQ_EN ? en : ru; };
  function vlBlock(n) {
    return L("С доменом мы рекомендуем TLS со своим сертификатом: узел предъявляет свой настоящий сертификат, показывает свой сайт и не зависит от чужих сайтов. Reality выдаёт узел за чужой сайт (" + VL_SITE + "). Что это значит:\n" +
      "- адрес вашего сервера не принадлежит этому сайту — такое несовпадение можно заметить со стороны; если его станут использовать для блокировок, соединения начнут замедлять или обрывать;\n" +
      "- если сайт перестанет отвечать с узла по TLS 1.3 или сменит настройки, VLESS перестанет подключаться, пока вы не смените сайт (ссылки — выдать заново)",
      "With a domain we recommend TLS with your own certificate: the server presents its real certificate, shows its own site and does not depend on other people’s sites. Reality makes the server pose as someone else’s site (" + VL_SITE + "). This means:\n" +
      "- your server’s address does not belong to that site — the mismatch can be noticed from outside; if it is ever used for blocking, connections will be slowed down or dropped;\n" +
      "- if the site stops answering the server over TLS 1.3 or changes its settings, VLESS stops connecting until you pick another site (links have to be given out again)") +
      // сайт выключен, Reality на прежней маске: ссылки не меняются — пункт «не изменятся»; устройств нет — пункта
      // цены нет совсем (как front.risks_data, макет М3)
      (n ? ";\n- " + L("ссылки, выданные сейчас, не изменятся.", "the links given out now stay the same.") : ".") + "\n\n" +
      L("Иногда Reality проходит там, где свой домен не пускают (например, мобильные «белые списки»). Если выбираете его по этой причине — выбирайте осознанно.",
        "Sometimes Reality gets through where your own domain is not let in (for example, mobile “allow lists”). If that is why you choose it, choose it knowingly.");
  }
  function vlDevs(n) { var m = n % 100, d = n % 10; return n + " " + (REQ_EN ? (n === 1 ? "device" : "devices") : m > 10 && m < 20 ? "устройств" : d === 1 ? "устройство" : d > 1 && d < 5 ? "устройства" : "устройств"); }
  function vlWho(devs) { var n = devs.map(function (d) { return d.name; }); return n.slice(0, 3).join(", ") + (n.length > 3 ? L(" и ещё ", " and ") + (n.length - 3) + L("", " more") : ""); }
  // m1 | m2 | chosen | null (VLESS нет): сайт выключен, VLESS на Reality (сцены dev-view-setup.js зовут это же)
  function vlessSet(kind) {
    S.siteMode = "domain";
    if (!kind) { S.vless = null; return; }
    var m2 = kind === "m2";
    S.vless = { security: "reality", want: m2 ? null : kind === "chosen" ? "reality" : "tls",
                devices: m2 ? ["iPhone", "MacBook", "iPad", "Android TV", "Pixel 8", "ThinkPad", "Galaxy S24", "Surface", "Keenetic"]   // имена — без языка
                  .map(function (n, i) { return { id: "d-0000a0" + (10 + i), name: n, platform: "other", user: "", user_name: "" }; }) : [] };
  }
  function vlessDoc() {
    var v = S.vless;
    if (!v) return null;
    var site = S.siteMode === "site", n = v.devices.length, tls = v.security === "tls";
    // сайт выключен: в мастере его на этом домене ещё не включали — при устройствах «TLS уже можно» (ядро, T-3378 Н-17)
    var reason = tls ? "" : v.want === "reality" ? "chosen" : site ? (v.want ? "ready" : "legacy") : (v.want ? (n ? "ready" : "no_site") : "legacy");
    // сайт выключен — включится вместе с переходом: цена словами сайта, как в ядре (REV-3378-fix С1)
    var rec = reason === "ready" || reason === "legacy" ? { mode: "tls", devices: n, text: n && !site
      ? L("Можно перейти на TLS со своим доменом. ", "You can switch to TLS with your own domain. ") + vlSitePrice(v) : n
      ? L("Можно перейти на TLS со своим доменом. Цена: ключи VLESS нужно выдать заново (", "You can switch to TLS with your own domain. The price: the VLESS keys have to be given out again (") + vlDevs(n) + ")."
      : L("Можно перейти на TLS со своим доменом: устройств с VLESS нет, выдавать ключи заново некому.", "You can switch to TLS with your own domain: no devices use VLESS, nobody needs new keys.") } : null;
    return { security: v.security, devices: n, reason: reason, forced: false, want: v.want, legacy: !v.want, recommend: rec, node_domain: S.domain || "",
             reality_risks: { ver: "r1a", site: VL_SITE, need_pick: false, title: L("Reality вместо своего сертификата", "Reality instead of your own certificate"), text: vlBlock(n),
                              price: n ? L("ссылки, выданные сейчас, не изменятся.", "the links given out now stay the same.") : "", phrase: L("Понимаю риски, выбрать Reality", "I understand the risks, choose Reality") } };
  }
  function vlConsent(text) {
    var e = fail(text || L("Reality при домене — только с согласием на риски.", "Reality with a domain needs a consent to its risks."), "conflict");
    e.data = { need: "reality_consent", kind: "vless", block: vlBlock(S.vless ? S.vless.devices.length : 0), title: L("Reality вместо своего сертификата", "Reality instead of your own certificate"), risks: "r1a", site: VL_SITE,
               phrase: L("Понимаю риски, выбрать Reality", "I understand the risks, choose Reality") };
    return e;
  }
  // цена новых ссылок VLESS: устройства есть, VLESS не на TLS, нет yes — отказ «vless_change» (ничего не меняется)
  function vlSitePrice(v) {
    var n = v.devices.length;
    return L("Сайт включится, VLESS перейдёт на TLS со своим доменом — ссылки VLESS выдать заново (устройств — " + n + ": " + vlWho(v.devices) + ").",
      "The site will go on, VLESS will switch to TLS with your own domain — give the VLESS links out again (devices — " + n + ": " + vlWho(v.devices) + ").");
  }
  // переход на TLS при выключенном сайте (шаг vless-mode) — те же слова сайта и site_on false, как vless_to_tls ядра
  function vlessPrice(b, kind) {
    var v = S.vless;
    if (!v || v.security === "tls" || !v.devices.length || b.yes === true) return null;
    var n = v.devices.length, son = S.siteMode === "site", text = kind === "site" || !son ? vlSitePrice(v)
      : L("VLESS перейдёт на TLS со своим доменом. Ссылки VLESS, выданные раньше, перестанут работать. Выдать заново — устройств: " + n + " (" + vlWho(v.devices) + "). Подписки обновятся сами при следующем обновлении в приложении.",
          "VLESS will switch to TLS with your own domain. The VLESS links given before will stop working. Give them again — devices: " + n + " (" + vlWho(v.devices) + "). Subscriptions update by themselves on the next refresh in the app.");
    var e = fail(text, "conflict", kind === "site" ? { action: { step: "site", yes: true, template: b.template || null,
      label: L("Включить сайт и выдать ключи VLESS заново", "Switch the site on and give the VLESS keys out again") } } : null);
    e.data = { need: "vless_change", kind: kind === "site" ? "site" : "vless", text: text, devices: v.devices, to: { security: "tls" } };
    if (kind !== "site") e.data.site_on = son;
    return e;
  }
  function vlessMode(b) {
    var v = S.vless;
    if (!v) return fail(L("VLESS не установлен.", "VLESS is not installed."), "error");
    if (b.mode !== "tls" && b.mode !== "reality") return fail(L("Режим VLESS — tls или reality.", "The VLESS mode is tls or reality."), "bad_input", { field: "mode" });
    if (b.mode === "tls") {
      var price = vlessPrice(b, "vless");
      if (price) return price;
      var who = v.devices.slice();
      S.siteMode = "site"; v.security = "tls"; v.want = "tls";
      return res("ok", L("VLESS — TLS со своим доменом, порт 443.", "VLESS — TLS with your own domain, port 443."), { vless: { security: "tls", changed: !!who.length, port: 443, want: "tls",
        text: who.length ? L("VLESS перешёл на TLS со своим доменом " + S.domain + " (порт 443) — выданные раньше ссылки VLESS не подключатся. Выдайте их заново: ",
          "VLESS moved to TLS with your own domain " + S.domain + " (port 443) — the VLESS links given before will not connect. Give them again: ") + vlWho(who) + "." : "" },
        reissue: who.map(function (d) { return { id: d.id, name: d.name }; }) });
    }
    if (b.accept_risks !== true) return vlConsent();
    if (b.risks !== "r1a" || b.site !== VL_SITE) return vlConsent(L("Текст рисков или сайт не совпадают с текущими — покажите новый текст и передайте risks и site из data",
      "The text of the risks or the site do not match the current ones — show the new text and pass risks and site from data"));
    v.want = "reality";
    return res("ok", L("VLESS остаётся на Reality (", "VLESS stays on Reality (") + VL_SITE + ").", { vless: { security: "reality", changed: false, port: 443, want: "reality",
      text: L("ссылки, выданные сейчас, не изменятся.", "the links given out now stay the same.") }, reissue: [] });
  }

  function emit(name, data) { listeners.forEach(function (l) { l(name, data); }); }
  function runJob(name, body) {
    var rows = PLAN[name](body), id = Math.random().toString(16).slice(2, 14);
    var job = { id: id, step: name, state: "running", started: NOW(), updated: NOW(), pid: 4242, result: null,
                items: rows.map(function (r) { return { item: r[0], title: r[1], state: "idle", detail: "в очереди", since: NOW() }; }) };
    S.job = job;
    var own = name === "cert" && P.get("le") === "limit";      // отказ со своими словами (RESULT.cert), не общий
    var i = 0, failAt = P.get("fail") === name || own ? Math.max(0, rows.length - 1) : -1;
    function tick() {
      if (i >= rows.length) {
        job.state = failAt >= 0 ? "failed" : "done";
        var swFail = name === "cert" && working() && body && body.name && String(body.name).toLowerCase() !== S.domain;
        job.result = failAt >= 0 && !own ? (swFail ? fail("Домен не сменён: nginx не принял сайт для " + String(body.name).toLowerCase() + " (мок: fail=cert). Прежний домен " + S.domain + " возвращён — сайт, панель на домене и VLESS работают как раньше.", "switch")
          : fail("Шаг не удался (мок: fail=" + name + ").", "error")) : RESULT[name](body);
        job.result.step = name; job.result.items = job.items; job.updated = NOW();
        S.steps[name] = { state: job.result.state, at: new Date().toISOString(), text: job.result.text, data: {} };
        emit("job", job); emit("state", { updated: new Date().toISOString() }); return;
      }
      var r = rows[i], it = job.items[i];
      if (it.state === "idle") { it.state = "wait"; it.detail = r[2]; it.since = NOW(); }
      else { it.state = i === failAt ? "err" : r[3]; it.detail = i === failAt ? "ошибка (мок)" : r[4]; it.since = NOW(); i++; }
      job.updated = NOW(); emit("job", job);
      setTimeout(tick, 900);
    }
    setTimeout(tick, 300);
    return job;
  }

  function route(method, path, body) {
    var m;
    if (path === "session") {
      return [200, { ok: true, auth: !!S.auth, via: S.auth, reason: S.auth ? null : (S.reason || (S.pw ? "password" : "link")),
                     password_set: S.pw, cmd: "kaskadctl setup link", ssh: "gokaskad → «Быстрая настройка»", name: "Kaskad PRO",
                     login: S.auth ? "admin" : null, expires_in: Math.max(0, Math.round(S.expires - NOW())) }];
    }
    if (path === "login") {
      if (body.password === "demo-demo-1") { S.auth = "password"; return [200, { ok: true, via: "password" }]; }
      return [401, { ok: false, error: "Неверный пароль.", data: { auth: "failed", left: 4 } }];
    }
    if (path === "logout") { S.auth = null; return [200, { ok: true }]; }
    if (path === "lang") {                     // язык сервера (3.3.2): без сервера — ?lang=… страницы
      if (!S.auth) return [401, { ok: false, error: "Нужен вход в мастер." }];
      if (body.lang !== "ru" && body.lang !== "en") return [400, { ok: false, error: "Язык: ru или en." }];
      try { const u = new URL(location.href); u.searchParams.set("lang", body.lang); history.replaceState(null, "", u.pathname + u.search + u.hash); } catch (e) { /* старый браузер */ }
      return [200, { ok: true, lang: body.lang }];
    }
    if (S.reason === "expired") return [410, { ok: false, error: "Ссылка на мастер больше не действует: kaskadctl setup link.", data: { reason: "expired", cmd: "kaskadctl setup link" } }];
    if (!S.auth) return [401, { ok: false, error: "Нужен вход в мастер.", data: { auth: "required", reason: S.pw ? "password" : "link" } }];
    if (path === "state") {
      return [200, { ok: true, status: "active", active: true, expires: S.expires, expires_in: Math.round(S.expires - NOW()), bind: false,
                     password_set: S.pw, mode: S.mode, rail: rail(), answers: S.answers, steps: S.steps, job: S.job, product: product(),
                     site: { domain: S.domain || null, mode: S.siteMode, template: S.siteMode === "site" ? { id: "cafe-zerno", name: "Кофейня «Зерно»" } : null, email: !!S.acmeEmail,
                             cert: !!S.domain && S.certDomain === S.domain, domain_u: S.domain || null }, vless: vlessDoc(),
                     started: null, updated: new Date().toISOString() }];
    }
    if (path === "product") return [200, Object.assign({ ok: true }, product())];
    if (path === "steps") return [200, { ok: true, steps: Object.keys(STEP).concat(Object.keys(PLAN).filter(function (k) { return !STEP[k]; }))
      .map(function (k) { return { name: k, long: !!LONG[k], secret: !!SECRET[k], network: !!NET[k] }; }) }];
    if (path === "net") return [200, { ok: true, online: P.get("offline") !== "1", every: 10 }];
    if (path === "job") return [200, { ok: true, job: S.job }];
    if (path === "ipcert") {
      var fb = P.get("cert") === "fallback";
      return [200, { ok: true, state: fb ? "none" : "valid", ip: IP, not_after: fb ? null : "2026-10-16T08:00:00Z", hours_left: fb ? null : 159.6,
                     days_left: fb ? null : 6.6, staging: false, serving: !fb, domain: false,
                     fingerprint: fb ? "AB:CD:EF:01:23:45:67:89:AB:CD:EF:01:23:45:67:89:AB:CD:EF:01:23:45:67:89:AB:CD:EF:01:23:45:67:89" : "",
                     last_try: fb ? { at: new Date().toISOString(), ok: false, reason: "port80", text: "Порт 80/tcp занят: nginx (pid 777).", retry_after: null } : { ok: true },
                     text: fb ? "Сертификат на IP не выпустился: Порт 80/tcp занят: nginx (pid 777). Работаем на своём — браузер покажет предупреждение один раз."
                              : "Соединение защищено сертификатом Let's Encrypt на IP · действует ≈ 6 дней, продлевается сам." }];
    }
    if (path === "stage") {
      var kinds = RAIL[S.mode];
      if (body.action === "open" && S.stages[body.kind] === undefined && body.kind !== S.current) return [409, { ok: false, error: "этап ещё впереди: сначала пройдите предыдущие", data: { reason: "stage" } }];
      if (body.action === "done" || body.action === "skip") {
        S.stages[body.kind] = body.action === "done" ? "done" : "skipped";
        var next = kinds.filter(function (k, i) { return i > kinds.indexOf(body.kind) && !S.stages[k]; })[0];
        S.current = next || body.kind;
      } else { S.current = body.kind; }
      emit("state", { updated: new Date().toISOString() });
      return [200, { ok: true, current: S.current, rail: rail() }];
    }
    if (path === "move") {                               // переезд на домен: одноразовый токен (мок — ненастоящий)
      var host = (body && body.host ? String(body.host) : S.domain || "").toLowerCase();
      if (!host) return [409, { ok: false, error: "Домен ещё не задан.", data: { reason: "no_domain" } }];
      if (host !== (S.domain || "").toLowerCase() && host !== S.next) return [409, { ok: false, error: "Домен " + host + " ещё не проверен на этом шаге.", data: { reason: "not_checked" } }];
      var base = "https://" + host + ":43193/setup-DEMOdemoDEMOdemoDEMOdemoDEMOdemo/";
      return [200, { ok: true, host: host, url: base + "?t=DEMO-move-token", clean: base, expires_in: 900 }];
    }
    if (path === "move-ready") {                         // домен отвечает своим сертификатом? (мок: ?late=1 — нет)
      return [200, { ok: true, ready: P.get("late") !== "1", reason: P.get("late") === "1" ? "stale" : "", host: (S.domain || "").toLowerCase() }];
    }
    if (path === "finish") { S.auth = null; return [200, Object.assign(STEP.finish(), { step: "finish", panel_url: "/", proxy_url: null })]; }
    if ((m = /^step\/([a-z0-9-]+)$/.exec(path))) {
      var name = m[1];
      if (!STEP[name] && !PLAN[name]) return [404, { ok: false, error: "Нет такого шага: " + name + ".", data: { reason: "unknown_step" } }];
      if (!S.pw && name !== "password") return [200, Object.assign(fail("Сначала задайте пароль панели — шаг «Вход».", "password_first", { action: { stage: "welcome" } }), { step: name })];
      if (NET[name] && P.get("offline") === "1") {
        var t = "У сервера нет интернета: шаг подождёт.";
        return [200, Object.assign(res("offline", t, null, { code: "offline", text: t }), { step: name })];
      }
      if (PLAN[name]) {
        if (S.job && S.job.state === "running") return [409, { ok: false, error: BUSY[S.job.step] || BUSY_OTHER, data: { job: S.job.id, step: S.job.step } }];
        return [202, { ok: true, step: name, job: runJob(name, body) }];
      }
      var out = P.get("fail") === name ? fail("Шаг не удался (мок: fail=" + name + ").", "error") : STEP[name](body);
      out.step = name;
      if (!SECRET[name] && ["exits", "dns", "templates", "extras-list", "connection", "trace", "summary", "platforms"].indexOf(name) < 0) {
        S.steps[name] = { state: out.state, at: new Date().toISOString(), text: out.text, data: {} };
      }
      return [200, out];
    }
    return [404, { ok: false, error: "Нет такого пути в макете: " + path }];
  }

  function respond(status, body) {
    return new Promise(function (ok) {
      setTimeout(function () {
        ok({ ok: status >= 200 && status < 300, status: status, headers: { get: function () { return "application/json"; } },
             json: function () { return Promise.resolve(JSON.parse(JSON.stringify(body))); } });
      }, LAT);
    });
  }

  function mockFetch(url, init) {
    var u = new URL(url, location.href), path = u.pathname.replace(/^.*\/api\/setup\//, "");
    var method = ((init || {}).method || "GET").toUpperCase(), body = {};
    if (method !== "GET") {
      if (((init || {}).headers || {})["X-Kaskad-Admin"] !== "1") return respond(403, { ok: false, error: "Запрос без заголовка X-Kaskad-Admin: 1 отклонён." });
      try { body = JSON.parse((init || {}).body || "{}"); } catch (e) { return respond(400, { ok: false, error: "тело запроса — не JSON" }); }
    }
    REQ_EN = ((init || {}).headers || {})["X-Kaskad-Lang"] === "en";       // язык текстов ядра VLESS (vlBlock …)
    var r = route(method, path, body);
    return respond(r[0], r[1]);
  }

  function MockEventSource(url) {
    var self = this, handlers = {};
    if (P.get("sse") === "off") {                        // sse=off: поток не открылся (503, прокси) — страница идёт опросом
      this.readyState = 2; this.addEventListener = function () {}; this.close = function () {};
      setTimeout(function () { if (self.onerror) self.onerror({ type: "error" }); }, LAT);
      return;
    }
    this.readyState = 1;
    this.addEventListener = function (name, fn) { (handlers[name] = handlers[name] || []).push(fn); };
    this.close = function () { self.readyState = 2; listeners = listeners.filter(function (l) { return l !== on; }); };
    function on(name, data) {
      (handlers[name] || []).forEach(function (fn) { fn({ type: name, data: JSON.stringify(data) }); });
    }
    listeners.push(on);
    setTimeout(function () { (handlers.open || []).forEach(function (fn) { fn({ type: "open" }); }); if (S.job) on("job", S.job); }, LAT);
  }

  function demoExits() {                               // панель просмотра: «2 из 3 работают»
    S.exits = [
      { id: "e-1a2b3c4d5e", name: "Париж", kind: "awg", title: "AmneziaWG 2.0", where: "198.51.100.45:51820", enabled: true, usable: true, handshake: 12, state: "ok", text: "работает, рукопожатие 12 с назад" },
      { id: "e-2b3c4d5e6f", name: "Хельсинки", kind: "xray", proto: "vless", title: "VLESS", where: "hel.example.net:443", enabled: true, usable: true, handshake: null, state: "ok", text: "работает, проверка через xray 8 с назад" },
      { id: "e-3c4d5e6f70", name: "Токио", kind: "awg", title: "AmneziaWG 3.1", where: "tokyo.example.net:51820", enabled: true, usable: false, handshake: null, state: "err", text: "не работает: нет ответа 35 с — клиенты переходят на другой выход" }];
  }
  window.KASKAD_SETUP_MOCK = { demoExits: demoExits, fetch: mockFetch, EventSource: MockEventSource, state: S, flags: FL, installed: INSTALLED, rail: rail,
                               emit: emit, stages: RAIL, vless: vlessSet };
})();
