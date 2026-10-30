const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

// --------------------------------------------------------------------------
// Admin token. The panel is opened once as http://127.0.0.1:1984/?token=...
// (the token is printed by the service and stored in /opt/gotelegram-admin/token);
// after that it lives in sessionStorage for the tab and is sent as a header.
// --------------------------------------------------------------------------
const TOKEN_STORAGE_KEY = "gotelegram-admin-token";

function readStoredToken() {
  try {
    return sessionStorage.getItem(TOKEN_STORAGE_KEY) || "";
  } catch (err) {
    return "";
  }
}

function storeToken(value) {
  try {
    if (value) sessionStorage.setItem(TOKEN_STORAGE_KEY, value);
    else sessionStorage.removeItem(TOKEN_STORAGE_KEY);
  } catch (err) {
    /* private mode: keep the in-memory copy only */
  }
}

let adminToken = (() => {
  let fromUrl = "";
  try {
    fromUrl = (new URLSearchParams(location.search).get("token") || "").trim();
  } catch (err) {
    fromUrl = "";
  }
  if (fromUrl) {
    storeToken(fromUrl);
    // Вычищаем токен из адресной строки: иначе он оседает в истории браузера и
    // в закладках. Панель локальная и за SSH-туннелем, риск невелик, но держать
    // root-токен в history — плохая гигиена.
    try {
      history.replaceState(null, "", location.pathname + location.hash);
    } catch (e) { /* приватный режим / устаревший движок — не критично */ }
    return fromUrl;
  }
  return readStoredToken();
})();

// <img src> cannot carry headers, so the QR endpoint gets the token in the query.
function withToken(path) {
  if (!adminToken) return path;
  return `${path}${path.includes("?") ? "&" : "?"}token=${encodeURIComponent(adminToken)}`;
}

const i18n = {
  en: {
    brandSubtitle: "Local Admin",
    navDashboard: "Dashboard",
    navTraffic: "Traffic",
    navKeys: "Keys",
    navBackups: "Backups",
    navLogs: "Logs",
    navSettings: "Settings",
    refresh: "Refresh",
    requestTimeout: "Server did not answer in time",
    authRequired: "Sign in",
    sessionOver: "Session is over",
    sessionOverHint: "The renewal window has passed. Get a code in the Telegram bot and sign in again.",
    authSendCodeNew: "Request a code",
    authTunnelFallback: "If the bot is unreachable, open the panel over an SSH tunnel: ssh -L 1984:127.0.0.1:1984 root@SERVER, then http://127.0.0.1:1984/?token=… (the token is in /opt/gotelegram-admin/token).",
    renewButton: "Extend session",
    renewApply: "Confirm",
    renewLeft: "Session expired. {n} s left to extend it with a code from the Telegram bot.",
    authSending: "Sending…",
    authCodeSent: "Code sent to the Telegram bot — enter it here",
    authCodeFailed: "Could not send the code",
    authHint: "No active session. Get a code in the Telegram bot and sign in.",
    autoRefresh: "Auto refresh every 5 seconds",
    autoRefreshOn: "Auto refresh is on",
    autoRefreshOff: "Auto refresh is off",
    autoRefreshOffShort: "off",
    themeDark: "Dark",
    themeLight: "Light",
    metricMode: "Mode",
    metricKeys: "Keys",
    metricProxyTraffic: "Proxy traffic",
    metricSiteTraffic: "Site traffic",
    configuredUsers: "configured users",
    packets: "packets",
    servicesEyebrow: "Services",
    servicesTitle: "Service health",
    servicesHelp: "Systemd service status for telemt, nginx, the bot, the traffic collector and the local admin.",
    runtimeEyebrow: "Runtime",
    runtimeTitle: "telemt summary",
    runtimeHelp: "Runtime data comes from the local telemt API and shows what the proxy engine sees right now.",
    trafficEyebrow: "Traffic",
    trafficTitle: "History",
    keysEyebrow: "Access",
    keysTitle: "User keys",
    backupsEyebrow: "Snapshots",
    backupsTitle: "Backups",
    eventsEyebrow: "Events",
    eventsTitle: "Activity",
    logsEyebrow: "Journal",
    logsTitle: "Logs",
    settingsEyebrow: "Settings",
    settingsTitle: "Panel preferences",
    configEyebrow: "Config",
    configTitle: "Installation state",
    collector: "Collector",
    lastPoint: "Last point",
    historyRows: "History rows",
    collectStats: "Update stats",
    collectStatsHelp: "Run one traffic collection now.",
    repairStats: "Restart collector",
    repairStatsHelp: "Reinstall and restart the background service that writes traffic history.",
    tableTime: "Time",
    tablePeriod: "Period",
    tableStatus: "Status",
    tableProxyDelta: "Proxy delta",
    tableSiteDelta: "Site delta",
    tableProxyTotal: "Proxy total",
    tableSiteTotal: "Site total",
    tableUser: "User",
    tableSecret: "Secret",
    tableLink: "Link",
    tableTraffic: "Traffic",
    ipLimit: "IP limit",
    ipLimitHint: "0 = no personal limit, minimum 2",
    ipLimitMinErr: "A limit of one address breaks an ordinary phone: switching Wi-Fi to LTE changes the address while the old connections are still alive, and the client hits the ceiling. Measured on a live server: about 20 seconds without service on average, up to 6 minutes at worst. The minimum is 2. Zero removes the personal limit.",
    saveIpLimit: "OK",
    tableTrafficDelta: "Traffic delta",
    tableTrafficTotal: "Total",
    tableActions: "Actions",
    userPlaceholder: "client-name",
    addKey: "Add key",
    copyLink: "Copy link",
    copyWebLink: "Copy WEB link",
    webTitle: "WEB proxy",
    webState: "State",
    webOn: "on",
    webOff: "off",
    webEnable: "Enable",
    webDisable: "Disable",
    webSetCarrier: "Apply carrier",
    webRefresh: "Refresh",
    webError: "WEB error",
    webDone: "done",
    webBlock_need_pro: "Available only in Pro mode with a domain.",
    webBlock_need_core: "Requires telemt {v} or newer — update the core.",
    webBlock_need_cert: "No Let's Encrypt certificate for the domain.",
    webBlock_need_addr: "Could not determine the server's public IPv4.",
    showWebQr: "WEB QR",
    qrWebTitle: "Scan WEB proxy (Desktop 7.1+)",
    webLinkHint: "WEB proxy for Telegram Desktop 7.1+: MTProxy inside plain HTTPS. The client warns that it runs the provider's page in the background — that is Telegram's standard notice.",
    copySecret: "Copy secret",
    showQr: "QR",
    delete: "Delete",
    enabled: "Enabled",
    disabled: "Disabled",
    applying: "Applying...",
    changesApplyInBackground: "Changes are being applied in the background",
    disableKey: "Disable key",
    enableKey: "Enable key",
    main: "main",
    createBackup: "Create backup",
    restoreBackup: "Restore",
    encryptedRestoreCli: "Encrypted backups are restored from CLI",
    backupScheduleTitle: "Automatic backups",
    backupScheduleLoading: "Loading schedule...",
    backupIncludesTitle: "Backup contents",
    backupIncludesText: "telemt config, goTelegram settings, keys, disabled keys, site, templates, SSL certificates, bot, admin panel and traffic history.",
    scheduleOff: "Off",
    scheduleDaily: "Daily",
    scheduleWeekly: "Weekly",
    scheduleMonthly: "Monthly",
    scheduleSaved: "Schedule saved",
    scheduleNext: "Next run: {value}",
    scheduleDisabled: "Automatic backups are disabled",
    backupRestoreStarted: "Restore started",
    confirmRestoreBackup: "Restore backup",
    loadLogs: "Load",
    panelLanguage: "Panel language",
    theme: "Theme",
    bindAddress: "Bind address",
    edgeTitle: "Server addresses",
    edgeAddr: "Address",
    unitB: "B",
    unitKB: "KB",
    unitMB: "MB",
    unitGB: "GB",
    unitTB: "TB",
    edgeSshBad: "Backup SSH access at risk.",
    edgeW15: "15m",
    edgeW1h: "1h",
    edgeW1d: "24h",
    edgeConns: "Now",
    edgeReach: "Link",
    edgeHint: "Traffic per address over 15 minutes, an hour and a day. ✓ answers on your domain, ✗ does not.",
    publicAddress: "Browser address",
    panelCodeReady: "Code sign-in ready — the code arrives in the Telegram bot",
    panelCodeNeedsBot: "Code sign-in needs a linked Telegram bot with admins",
    panelNotPublished: "Not published — reachable over the SSH tunnel only",
    dashboard: "Dashboard",
    noKeys: "No keys yet",
    noBackups: "No backups yet",
    noEvents: "No events yet",
    noHistory: "No traffic history yet",
    noTrafficForRange: "No data for this range yet",
    noRuntime: "Runtime data is not available",
    userTrafficEyebrow: "Per user",
    userTrafficTitle: "User traffic",
    selectUserTraffic: "Select a key to see its traffic history",
    openStats: "Stats",
    trafficTotal: "Total",
    currentConnections: "Connections",
    activeIps: "Active IPs",
    recentIps: "Recent IPs",
    trafficRuntimeUnavailable: "Runtime unavailable",
    badConnections: "Bad connections",
    connections: "Connections",
    uptime: "Uptime",
    users: "Users",
    revision: "Revision",
    healthOk: "OK",
    healthError: "Error",
    healthStale: "Stale",
    healthStopped: "Stopped",
    healthNotInstalled: "Not installed",
    healthUnknown: "Unknown",
    statusRunning: "running",
    statusInactive: "inactive",
    statusStopped: "stopped",
    statusFailed: "failed",
    statusNotInstalled: "not installed",
    statusActivating: "activating",
    statusDeactivating: "deactivating",
    statusUnknown: "unknown",
    statsMissing: "Collector is not running",
    statsOk: "Collector is running",
    statsStale: "Snapshot is stale",
    statsError: "Collector error",
    restart: "Restart",
    copied: "Copied",
    copyFailed: "Copy failed",
    keyCreated: "Key created",
    keyDeleted: "Key deleted",
    backupCreated: "Backup created",
    qrUnavailable: "QR code is unavailable",
    serviceRestarted: "Service restarted",
    statsRepaired: "Collector restarted",
    statsCollected: "Statistics collected",
    confirmDelete: "Delete key",
    confirmRestart: "Restart",
    invalidUser: "Use latin letters, digits, _, . or -",
    loading: "Loading...",
    never: "never",
    lightTheme: "Light",
    darkTheme: "Dark",
    configMode: "Mode",
    configDomain: "Domain",
    configSiteStatus: "Site check",
    configTemplate: "Template",
    configVersion: "Version",
    siteOk: "Site 200 OK",
    siteHttp: "Site HTTP",
    siteMissing: "Domain is not configured",
    siteInvalid: "Invalid domain",
    siteError: "Site check failed",
    siteNotChecked: "Site check pending",
    logsLines: "lines",
    logsNoData: "No log lines",
    languageSaved: "Language saved",
    keyEnabled: "Key enabled",
    keyDisabled: "Key disabled",
    ipLimitSaved: "IP limit saved",
    visualTitle: "Port 443 map",
    visualText: "Shows the public 443 listener and services routed behind it, including the website on local nginx.",
    port443Checked: "checked",
    port443NoListeners: "No 443 listeners found",
    port443Listeners: "listeners",
    port443Routes: "routed",
    port443Error: "Port check failed",
    port443Public: "public",
    port443Configured: "telemt: {port}",
    port443PublicSection: "Public 443",
    port443BehindSection: "Behind 443",
    port443NoRoutes: "No routed services detected",
    port443Via: "via {value}",
    roleMtproxy: "MTProxy",
    roleEdge: "443 Edge",
    roleSite: "Website",
    roleXray: "Xray / 3x-ui",
    roleAmneziawg: "AmneziaWG",
    roleOther: "Other",
    range15m: "15 min",
    range1h: "1 hour",
    range24h: "24 hours",
    rangeMonth: "Month",
    viewChart: "Chart",
    viewRows: "Rows",
    chartMax: "max {value} per interval",
    chartProxy: "proxy",
    chartSite: "site",
    encrypted: "encrypted",
    ariaAdminSections: "Admin sections",
    ariaMenu: "Open menu",
    ariaLanguage: "Language",
    ariaClose: "Close",
    ariaTrafficHistory: "Traffic history",
    ariaTrafficRange: "Traffic range",
    ariaTrafficView: "Traffic view",
    promoEyebrow: "Promo",
    promoTitle: "Support goTelegram Pro",
    promoHosting1: "Hosting #1",
    promoHosting2: "Hosting #2",
    promoTips: "Tips",
    qrEyebrow: "QR import",
    qrTitle: "Scan Telegram proxy",
    pageDashboardTitle: "Dashboard",
    pageDashboardKicker: "Local Admin",
    pageTrafficTitle: "Traffic",
    pageTrafficKicker: "Statistics",
    pageKeysTitle: "Keys",
    pageKeysKicker: "Access",
    pageBackupsTitle: "Backups",
    pageBackupsKicker: "Migration",
    pageLogsTitle: "Logs",
    pageLogsKicker: "Journal",
    pageSettingsTitle: "Settings",
    pageSettingsKicker: "Preferences",
  },
  ru: {
    brandSubtitle: "Локальная админка",
    navDashboard: "Обзор",
    navTraffic: "Трафик",
    navKeys: "Ключи",
    navBackups: "Бекапы",
    navLogs: "Логи",
    navSettings: "Настройки",
    refresh: "Обновить",
    requestTimeout: "Сервер не ответил вовремя",
    authRequired: "Вход в панель",
    sessionOver: "Сессия закончена",
    sessionOverHint: "Окно продления прошло. Получите код в Telegram-боте и войдите заново.",
    authSendCodeNew: "Запросить код",
    authTunnelFallback: "Если бот недоступен, панель открывается через SSH-туннель: ssh -L 1984:127.0.0.1:1984 root@СЕРВЕР, затем http://127.0.0.1:1984/?token=… (токен в /opt/gotelegram-admin/token).",
    renewButton: "Продлить авторизацию",
    renewApply: "Подтвердить",
    renewLeft: "Сессия истекла. Осталось {n} с, чтобы продлить её кодом из Telegram-бота.",
    authSending: "Отправляю…",
    authCodeSent: "Код отправлен в Telegram-бота — введите его здесь",
    authCodeFailed: "Не удалось отправить код",
    authHint: "Активной сессии нет. Получите код в Telegram-боте и войдите.",
    autoRefresh: "Автообновление каждые 5 секунд",
    autoRefreshOn: "Автообновление включено",
    autoRefreshOff: "Автообновление выключено",
    autoRefreshOffShort: "выкл",
    themeDark: "Тёмная",
    themeLight: "Светлая",
    metricMode: "Режим",
    metricKeys: "Ключи",
    metricProxyTraffic: "Трафик прокси",
    metricSiteTraffic: "Трафик сайта",
    configuredUsers: "настроенных пользователей",
    packets: "пакетов",
    servicesEyebrow: "Сервисы",
    servicesTitle: "Состояние служб",
    servicesHelp: "Статус systemd-служб: telemt, nginx, бот, сборщик трафика и локальная админка.",
    runtimeEyebrow: "Среда выполнения",
    runtimeTitle: "Сводка telemt",
    runtimeHelp: "Данные среды выполнения берутся из локального API telemt и показывают, что ядро прокси видит прямо сейчас.",
    trafficEyebrow: "Трафик",
    trafficTitle: "История",
    keysEyebrow: "Доступ",
    keysTitle: "Ключи пользователей",
    backupsEyebrow: "Снимки",
    backupsTitle: "Бекапы",
    eventsEyebrow: "События",
    eventsTitle: "Активность",
    logsEyebrow: "Журнал",
    logsTitle: "Логи",
    settingsEyebrow: "Настройки",
    settingsTitle: "Параметры панели",
    configEyebrow: "Конфиг",
    configTitle: "Состояние установки",
    collector: "Сборщик",
    lastPoint: "Последняя точка",
    historyRows: "Строк истории",
    collectStats: "Обновить статистику",
    collectStatsHelp: "Запустить один сбор трафика прямо сейчас.",
    repairStats: "Перезапустить сборщик",
    repairStatsHelp: "Переустановить и перезапустить фоновую службу, которая пишет историю трафика.",
    tableTime: "Время",
    tablePeriod: "Период",
    tableStatus: "Статус",
    tableProxyDelta: "Прирост прокси",
    tableSiteDelta: "Прирост сайта",
    tableProxyTotal: "Всего прокси",
    tableSiteTotal: "Всего по сайту",
    tableUser: "Пользователь",
    tableSecret: "Секрет",
    tableLink: "Ссылка",
    tableTraffic: "Трафик",
    ipLimit: "Лимит IP",
    ipLimitHint: "0 — без персонального лимита, минимум 2",
    ipLimitMinErr: "Лимит в один адрес рвёт обычный телефон: при переходе Wi-Fi ↔ LTE адрес меняется, старые соединения ещё живы, и клиент упирается в потолок. Замер на боевом сервере: связь пропадала в среднем на 20 секунд, в худшем случае на 6 минут. Минимум — 2. Ноль снимает персональный лимит.",
    saveIpLimit: "OK",
    tableTrafficDelta: "Прирост трафика",
    tableTrafficTotal: "Всего",
    tableActions: "Действия",
    userPlaceholder: "client-name",
    addKey: "Добавить ключ",
    copyLink: "Копировать ссылку",
    copyWebLink: "Копировать WEB-ссылку",
    webTitle: "WEB-прокси",
    webState: "Состояние",
    webOn: "включён",
    webOff: "выключен",
    webEnable: "Включить",
    webDisable: "Выключить",
    webSetCarrier: "Применить carrier",
    webRefresh: "Обновить",
    webError: "Ошибка WEB",
    webDone: "готово",
    webBlock_need_pro: "Доступен только в Pro-режиме с доменом.",
    webBlock_need_core: "Нужно ядро telemt {v} или новее — обновите ядро.",
    webBlock_need_cert: "Для домена нет сертификата Let's Encrypt.",
    webBlock_need_addr: "Не удалось определить публичный IPv4 сервера.",
    showWebQr: "QR WEB",
    qrWebTitle: "Сканирование WEB-прокси (Desktop 7.1+)",
    webLinkHint: "WEB-прокси для Telegram Desktop 7.1+: MTProxy внутри обычного HTTPS. Клиент предупредит, что запускает страницу провайдера в фоне — это штатное сообщение Telegram.",
    copySecret: "Копировать секрет",
    showQr: "QR",
    delete: "Удалить",
    enabled: "Включён",
    disabled: "Отключён",
    applying: "Применяется...",
    changesApplyInBackground: "Изменения применяются в фоне",
    disableKey: "Отключить ключ",
    enableKey: "Включить ключ",
    main: "основной",
    createBackup: "Создать бекап",
    restoreBackup: "Восстановить",
    encryptedRestoreCli: "Зашифрованные бекапы восстанавливаются через CLI",
    backupScheduleTitle: "Автобекапы",
    backupScheduleLoading: "Загрузка расписания...",
    backupIncludesTitle: "Что входит в бекап",
    backupIncludesText: "конфиг telemt, настройки goTelegram, ключи, отключённые ключи, сайт, шаблоны, SSL-сертификаты, бот, админка и история трафика.",
    scheduleOff: "Выкл",
    scheduleDaily: "Каждый день",
    scheduleWeekly: "Каждую неделю",
    scheduleMonthly: "Каждый месяц",
    scheduleSaved: "Расписание сохранено",
    scheduleNext: "Следующий запуск: {value}",
    scheduleDisabled: "Автобекапы отключены",
    backupRestoreStarted: "Восстановление запущено",
    confirmRestoreBackup: "Восстановить бекап",
    loadLogs: "Загрузить",
    panelLanguage: "Язык панели",
    theme: "Тема",
    bindAddress: "Адрес привязки",
    edgeTitle: "Адреса сервера",
    edgeAddr: "Адрес",
    unitB: "Б",
    unitKB: "КБ",
    unitMB: "МБ",
    unitGB: "ГБ",
    unitTB: "ТБ",
    edgeSshBad: "Запасной вход под угрозой.",
    edgeW15: "15м",
    edgeW1h: "1ч",
    edgeW1d: "24ч",
    edgeConns: "Сейчас",
    edgeReach: "Связь",
    edgeHint: "Трафик по каждому адресу за 15 минут, час и сутки. ✓ отвечает по домену, ✗ не отвечает.",
    publicAddress: "Адрес для браузера",
    panelCodeReady: "Вход по коду готов — код приходит в Telegram-бота",
    panelCodeNeedsBot: "Вход по коду требует привязанного Telegram-бота с админами",
    panelNotPublished: "Не опубликована — только по SSH-туннелю",
    dashboard: "Обзор",
    noKeys: "Ключей пока нет",
    noBackups: "Бекапов пока нет",
    noEvents: "Событий пока нет",
    noHistory: "Истории трафика пока нет",
    noTrafficForRange: "За этот период данных пока нет",
    noRuntime: "Данные среды выполнения недоступны",
    userTrafficEyebrow: "По пользователю",
    userTrafficTitle: "Трафик ключа",
    selectUserTraffic: "Выберите ключ, чтобы увидеть историю трафика",
    openStats: "Статистика",
    trafficTotal: "Всего",
    currentConnections: "Подключения",
    activeIps: "Активные IP",
    recentIps: "Недавние IP",
    trafficRuntimeUnavailable: "Runtime недоступен",
    badConnections: "Ошибочные подключения",
    connections: "Подключения",
    uptime: "Аптайм",
    users: "Пользователи",
    revision: "Ревизия",
    healthOk: "OK",
    healthError: "Ошибка",
    healthStale: "Устарело",
    healthStopped: "Остановлено",
    healthNotInstalled: "Не установлен",
    healthUnknown: "Неизвестно",
    statusRunning: "работает",
    statusInactive: "неактивен",
    statusStopped: "остановлен",
    statusFailed: "ошибка",
    statusNotInstalled: "не установлен",
    statusActivating: "запускается",
    statusDeactivating: "останавливается",
    statusUnknown: "неизвестно",
    statsMissing: "Сборщик не запущен",
    statsOk: "Сборщик работает",
    statsStale: "Снимок устарел",
    statsError: "Ошибка сборщика",
    restart: "Перезапустить",
    copied: "Скопировано",
    copyFailed: "Не удалось скопировать",
    keyCreated: "Ключ создан",
    keyDeleted: "Ключ удалён",
    backupCreated: "Бекап создан",
    qrUnavailable: "QR-код недоступен",
    serviceRestarted: "Сервис перезапущен",
    statsRepaired: "Сборщик перезапущен",
    statsCollected: "Статистика собрана",
    confirmDelete: "Удалить ключ",
    confirmRestart: "Перезапустить",
    invalidUser: "Используйте латиницу, цифры, _, . или -",
    loading: "Загрузка...",
    never: "никогда",
    lightTheme: "Светлая",
    darkTheme: "Тёмная",
    configMode: "Режим",
    configDomain: "Домен",
    configSiteStatus: "Проверка сайта",
    configTemplate: "Шаблон",
    configVersion: "Версия",
    siteOk: "Сайт 200 OK",
    siteHttp: "Сайт HTTP",
    siteMissing: "Домен не настроен",
    siteInvalid: "Некорректный домен",
    siteError: "Проверка сайта не прошла",
    siteNotChecked: "Проверка сайта ожидает",
    logsLines: "строк",
    logsNoData: "Строк логов нет",
    languageSaved: "Язык сохранён",
    keyEnabled: "Ключ включён",
    keyDisabled: "Ключ отключён",
    ipLimitSaved: "Лимит IP сохранён",
    visualTitle: "Карта порта 443",
    visualText: "Показывает публичного слушателя 443 и сервисы, которые живут за ним, включая сайт на локальном nginx.",
    port443Checked: "проверено",
    port443NoListeners: "Слушателей 443 не найдено",
    port443Listeners: "слушателей",
    port443Routes: "за 443",
    port443Error: "Проверка порта не удалась",
    port443Public: "публичный",
    port443Configured: "telemt: {port}",
    port443PublicSection: "Публичный 443",
    port443BehindSection: "За портом 443",
    port443NoRoutes: "Маршрутизируемых сервисов не найдено",
    port443Via: "через {value}",
    roleMtproxy: "MTProxy",
    roleEdge: "443 Edge",
    roleSite: "Сайт",
    roleXray: "Xray / 3x-ui",
    roleAmneziawg: "AmneziaWG",
    roleOther: "Другое",
    range15m: "15 мин",
    range1h: "1 час",
    range24h: "24 часа",
    rangeMonth: "Месяц",
    viewChart: "График",
    viewRows: "Строки",
    chartMax: "макс. {value} за интервал",
    chartProxy: "прокси",
    chartSite: "сайт",
    encrypted: "зашифровано",
    ariaAdminSections: "Разделы админки",
    ariaMenu: "Открыть меню",
    ariaLanguage: "Язык",
    ariaClose: "Закрыть",
    ariaTrafficHistory: "История трафика",
    ariaTrafficRange: "Период трафика",
    ariaTrafficView: "Вид трафика",
    promoEyebrow: "Промо",
    promoTitle: "Поддержать goTelegram Pro",
    promoHosting1: "Хостинг #1",
    promoHosting2: "Хостинг #2",
    promoTips: "Чаевые",
    qrEyebrow: "QR-импорт",
    qrTitle: "Сканирование прокси Telegram",
    pageDashboardTitle: "Обзор",
    pageDashboardKicker: "Локальная админка",
    pageTrafficTitle: "Трафик",
    pageTrafficKicker: "Статистика",
    pageKeysTitle: "Ключи",
    pageKeysKicker: "Доступ",
    pageBackupsTitle: "Бекапы",
    pageBackupsKicker: "Переезд",
    pageLogsTitle: "Логи",
    pageLogsKicker: "Журнал",
    pageSettingsTitle: "Настройки",
    pageSettingsKicker: "Параметры",
  },
};

const state = {
  overview: null,
  stats: null,
  users: [],
  events: [],
  lang: "en",
  page: "dashboard",
  theme: document.documentElement.dataset.theme || "light",
  trafficRange: "1h",
  trafficView: "chart",
  trafficLoading: false,
  userTrafficUser: "",
  userTrafficRange: "1h",
  userTrafficView: "chart",
  userTraffic: null,
  userTrafficLoading: false,
  backupSchedule: null,
  qrLink: "",
  pendingUsers: new Set(),
  refreshingAll: false,
  autoRefreshEnabled: localStorage.getItem("gotelegram-auto-refresh") !== "0",
};

const t = (key) => (i18n[state.lang] && i18n[state.lang][key]) || i18n.en[key] || key;

const trafficRanges = ["15m", "1h", "24h", "month"];
const AUTO_REFRESH_MS = 5000;
let autoRefreshTimer = null;

const fmtBytes = (value = 0) => {
  // Единицы из словаря: в русской панели рядом с «Адреса сервера» стояло
  // «171.2 KB», а бот тот же объём показывал как «171.2 КБ».
  const units = [t("unitB"), t("unitKB"), t("unitMB"), t("unitGB"), t("unitTB")];
  let n = Number(value) || 0;
  let i = 0;
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024;
    i += 1;
  }
  return `${n.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
};

const fmtDate = (epoch) => {
  if (!epoch) return t("never");
  return new Date(epoch * 1000).toLocaleString(state.lang === "ru" ? "ru-RU" : "en-US");
};

const fmtDuration = (seconds = 0) => {
  let value = Math.max(0, Math.floor(Number(seconds) || 0));
  const days = Math.floor(value / 86400);
  value %= 86400;
  const hours = Math.floor(value / 3600);
  value %= 3600;
  const minutes = Math.floor(value / 60);
  if (days) return `${days}d ${hours}h`;
  if (hours) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
};

const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#039;",
})[ch]);

const escapeAttr = (value) => escapeHtml(value).replace(/`/g, "&#096;");

const toast = (message) => {
  const el = $("#toast");
  el.textContent = message;
  el.classList.add("show");
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => el.classList.remove("show"), 2800);
};

const addEvent = (title, detail = "") => {
  state.events.unshift({ title, detail, time: new Date() });
  state.events = state.events.slice(0, 10);
  renderEvents();
};

function updateAutoRefreshToggle() {
  const button = $("#autoRefreshToggle");
  if (!button) return;
  button.classList.toggle("active", state.autoRefreshEnabled);
  button.setAttribute("aria-pressed", String(state.autoRefreshEnabled));
  button.title = state.autoRefreshEnabled ? t("autoRefreshOn") : t("autoRefreshOff");
  const label = button.querySelector(".auto-refresh-state");
  if (label) label.textContent = state.autoRefreshEnabled ? "5s" : t("autoRefreshOffShort");
}

function syncAutoRefreshTimer() {
  if (autoRefreshTimer) {
    clearInterval(autoRefreshTimer);
    autoRefreshTimer = null;
  }
  if (!state.autoRefreshEnabled) return;
  autoRefreshTimer = setInterval(() => {
    refreshAll().catch((err) => toast(err.message));
  }, AUTO_REFRESH_MS);
}

function setAutoRefresh(enabled) {
  state.autoRefreshEnabled = Boolean(enabled);
  localStorage.setItem("gotelegram-auto-refresh", state.autoRefreshEnabled ? "1" : "0");
  updateAutoRefreshToggle();
  syncAutoRefreshTimer();
}

const API_TIMEOUT_MS = 20000;
const API_TIMEOUT_WARP_MS = 40000;
const API_TIMEOUT_WARP_ACTION_MS = 250000;
const API_TIMEOUT_SLOW_MS = 200000;

// Without a deadline a hung server left state.refreshingAll === true forever
// and the panel silently stopped refreshing.
function timeoutSignal(ms) {
  try {
    if (typeof AbortSignal !== "undefined" && typeof AbortSignal.timeout === "function") {
      return { signal: AbortSignal.timeout(ms), cancel: () => {} };
    }
  } catch (err) {
    /* fall through to the AbortController variant */
  }
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), ms);
    return { signal: controller.signal, cancel: () => clearTimeout(timer) };
  } catch (err) {
    return { signal: undefined, cancel: () => {} };
  }
}

let authGateVisible = false;

// ── Жизнь сессии ────────────────────────────────────────────────────────────
//
// Сессия живёт 15 минут, потом 60 секунд её можно продлить кодом из бота.
// Опрашиваем состояние сами, а не ждём первой неудачной кнопки: окно короткое,
// и половину его человек потерял бы, пока сообразит, что панель перестала
// отвечать. Опрос лёгкий и не требует авторизации — он только про сессию.
let sessionPollTimer = null;
let renewGateVisible = false;
let renewCountdownTimer = null;

function stopSessionPoll() {
  if (sessionPollTimer) { clearInterval(sessionPollTimer); sessionPollTimer = null; }
}

function startSessionPoll() {
  stopSessionPoll();
  sessionPollTimer = setInterval(pollSession, 10000);
  pollSession();
}

async function pollSession() {
  if (authGateVisible || renewGateVisible) return;
  try {
    const r = await fetch(apiUrl("/api/session"), {
      credentials: "same-origin", headers: { "X-GoTelegram-Admin": "1" },
    });
    const d = await r.json().catch(() => ({}));
    const st = d?.data?.state || "none";
    if (st === "renew") handleAuthState("renew", Number(d?.data?.seconds) || 0);
    else if (st === "dead") handleAuthState("expired", 0);
  } catch (_e) {
    // Сеть моргнула — не повод пугать человека окном входа: сессия жива,
    // и следующий опрос через десять секунд всё равно всё покажет.
  }
}

function handleAuthState(state, seconds) {
  if (state === "renew") showRenewGate(seconds);
  else showAuthGate(state === "expired");
}


function showRenewGate(seconds) {
  if (renewGateVisible || document.getElementById("renewBanner")) return;
  if (authGateVisible) return;
  renewGateVisible = true;
  stopSessionPoll();
  if (autoRefreshTimer) { clearInterval(autoRefreshTimer); autoRefreshTimer = null; }

  // Баннер, а не окно во весь экран: продление — короткое действие на минуту,
  // и накрывать ради него всю панель значит прятать от человека то, на чём он
  // стоял. Полоса сверху видна, но не отбирает страницу.
  const bar = document.createElement("div");
  bar.id = "renewBanner";
  bar.setAttribute("style",
    "position:fixed;left:0;right:0;top:0;z-index:9998;display:flex;flex-wrap:wrap;" +
    "align-items:center;gap:10px;padding:10px 16px;background:#b45309;color:#fff;" +
    "font:14px/1.4 system-ui,-apple-system,Segoe UI,sans-serif;box-shadow:0 6px 20px rgba(0,0,0,.35)");

  const label = document.createElement("span");
  label.setAttribute("style", "flex:1 1 260px;min-width:0");

  const ask = document.createElement("button");
  ask.type = "button";
  ask.textContent = t("renewButton");
  ask.setAttribute("style",
    "flex:0 0 auto;padding:8px 14px;border:0;border-radius:8px;background:#fff;" +
    "color:#7c3f06;font:inherit;font-weight:600;cursor:pointer");

  const input = document.createElement("input");
  input.type = "text";
  input.inputMode = "numeric";
  input.autocomplete = "one-time-code";
  input.maxLength = 6;
  input.placeholder = "000000";
  // ФОН обязателен вместе с цветом: общие стили панели дают полям тёмный
  // background (`input, select { background: var(--panel) }`), и один только
  // color:#111 давал тёмные цифры по тёмному — набранный код был не виден.
  input.setAttribute("style",
    "display:none;flex:0 0 140px;padding:8px 10px;border:0;border-radius:8px;" +
    "font:inherit;font-size:17px;letter-spacing:4px;text-align:center;" +
    "background:#fff;color:#111;min-height:0");

  const go = document.createElement("button");
  go.type = "button";
  go.textContent = t("renewApply");
  go.setAttribute("style",
    "display:none;flex:0 0 auto;padding:8px 14px;border:0;border-radius:8px;" +
    "background:#fff;color:#7c3f06;font:inherit;font-weight:600;cursor:pointer");

  const msg = document.createElement("span");
  msg.setAttribute("style", "flex:1 1 100%;font-size:13px;opacity:.95");

  const post = async (path, body) => {
    const r = await fetch(apiUrl(path), {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-GoTelegram-Admin": "1" },
      body: JSON.stringify(body || {}),
    });
    return r.json().catch(() => ({ ok: false, error: t("authCodeFailed") }));
  };

  ask.addEventListener("click", async () => {
    ask.disabled = true;
    msg.textContent = t("authSending");
    const r = await post("/api/auth/request", {});
    ask.disabled = false;
    if (r.ok) {
      msg.textContent = t("authCodeSent");
      ask.style.display = "none";
      input.style.display = ""; go.style.display = "";
      input.focus();
    } else {
      // Причина приходит от сервера дословно: она объясняет и «слишком часто»,
      // и «бота нет», а своя формулировка тут только соврала бы.
      msg.textContent = r.error || t("authCodeFailed");
    }
  });

  const submit = async () => {
    const code = input.value.trim();
    if (!code) return;
    go.disabled = true;
    const r = await post("/api/auth/verify", { code });
    go.disabled = false;
    if (r.ok) {
      // Продлили — убираем полосу и продолжаем с той же страницы: перезагрузка
      // потеряла бы вкладку, на которой человек стоял.
      closeRenewGate();
      startSessionPoll();
      // Функция называется syncAutoRefreshTimer; startAutoRefresh не существует,
      // и вызов «если есть» молча не делал бы ничего — автообновление осталось
      // бы выключенным до перезагрузки страницы.
      syncAutoRefreshTimer();
      refreshAll();
    } else {
      msg.textContent = r.error || t("authCodeFailed");
    }
  };
  go.addEventListener("click", submit);
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") submit(); });

  bar.append(label, ask, input, go, msg);
  document.body.append(bar);
  // Полоса перекрыла бы шапку панели, поэтому сдвигаем содержимое на её высоту.
  document.body.style.paddingTop = bar.offsetHeight + "px";

  let remaining = Math.max(0, Number(seconds) || 0);
  const tick = () => {
    label.textContent = t("renewLeft").replace("{n}", String(remaining));
    if (remaining <= 0) {
      // Окно упущено: это уже не «продлите», а «войдите заново».
      closeRenewGate();
      showAuthGate(true);
      return;
    }
    remaining -= 1;
  };
  tick();
  renewCountdownTimer = setInterval(tick, 1000);
  ask.focus();
}

function closeRenewGate() {
  if (renewCountdownTimer) { clearInterval(renewCountdownTimer); renewCountdownTimer = null; }
  document.getElementById("renewBanner")?.remove();
  document.body.style.paddingTop = "";
  renewGateVisible = false;
}


function showAuthGate(expired) {
  if (autoRefreshTimer) {
    clearInterval(autoRefreshTimer);
    autoRefreshTimer = null;
  }
  if (authGateVisible || document.getElementById("authGate")) return;
  authGateVisible = true;

  const overlay = document.createElement("div");
  overlay.id = "authGate";
  overlay.setAttribute("style", "position:fixed;inset:0;z-index:9999;display:flex;align-items:center;justify-content:center;background:rgba(8,11,18,.86);padding:24px");

  const card = document.createElement("div");
  card.setAttribute("style", "max-width:560px;width:100%;background:#fff;color:#111;border-radius:14px;padding:22px;font:14px/1.55 system-ui,-apple-system,Segoe UI,sans-serif;box-shadow:0 24px 60px rgba(0,0,0,.5)");

  closeRenewGate();
  stopSessionPoll();

  const title = document.createElement("h2");
  title.textContent = expired ? t("sessionOver") : t("authRequired");
  title.setAttribute("style", "margin:0 0 10px;font-size:18px");

  const hint = document.createElement("p");
  hint.textContent = expired ? t("sessionOverHint") : t("authHint");
  hint.setAttribute("style", "margin:0 0 14px;opacity:.8");

  // Вход по коду — первым. Прежде здесь было только поле токена, то есть
  // человека, у которого сессия просто истекла, отправляли за файлом на
  // сервере по SSH — при том что код приходит ему в бота одним нажатием.
  const codeWrap = document.createElement("div");
  codeWrap.setAttribute("style", "margin:0 0 16px");

  const codeInput = document.createElement("input");
  codeInput.type = "text";
  codeInput.inputMode = "numeric";
  codeInput.autocomplete = "one-time-code";
  codeInput.maxLength = 6;
  codeInput.placeholder = "000000";
  // Фон и цвет задаём явно: общие стили панели красят поля в тёмное, а карточка
  // здесь белая — без этого получались тёмные цифры на тёмном поле.
  codeInput.setAttribute("style",
    "display:none;width:100%;box-sizing:border-box;padding:10px 12px;" +
    "border:1px solid #c9ced8;border-radius:8px;font:inherit;font-size:20px;" +
    "letter-spacing:6px;text-align:center;background:#fff;color:#111;min-height:0");

  const askBtn = document.createElement("button");
  askBtn.type = "button";
  askBtn.textContent = t("authSendCodeNew");
  askBtn.setAttribute("style",
    "width:100%;padding:11px 16px;border:0;border-radius:8px;background:#2563eb;" +
    "color:#fff;font:inherit;font-weight:600;cursor:pointer");

  const goBtn = document.createElement("button");
  goBtn.type = "button";
  goBtn.textContent = t("renewApply");
  goBtn.setAttribute("style",
    "display:none;width:100%;margin-top:8px;padding:11px 16px;border:0;" +
    "border-radius:8px;background:#2563eb;color:#fff;font:inherit;font-weight:600;cursor:pointer");

  const codeMsg = document.createElement("div");
  codeMsg.setAttribute("style", "margin-top:10px;font-size:13px;min-height:18px");

  const gatePost = async (path, body) => {
    const r = await fetch(apiUrl(path), {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-GoTelegram-Admin": "1" },
      body: JSON.stringify(body || {}),
    });
    return r.json().catch(() => ({ ok: false, error: t("authCodeFailed") }));
  };

  askBtn.addEventListener("click", async () => {
    askBtn.disabled = true;
    codeMsg.textContent = t("authSending"); codeMsg.style.color = "";
    const r = await gatePost("/api/auth/request", {});
    askBtn.disabled = false;
    if (r.ok) {
      codeMsg.textContent = t("authCodeSent"); codeMsg.style.color = "#0a7a43";
      askBtn.style.display = "none";
      codeInput.style.display = ""; goBtn.style.display = "";
      codeInput.focus();
    } else {
      codeMsg.textContent = r.error || t("authCodeFailed"); codeMsg.style.color = "#b23b3b";
    }
  });

  const submitCode = async () => {
    const value = codeInput.value.trim();
    if (!value) return;
    goBtn.disabled = true;
    const r = await gatePost("/api/auth/verify", { code: value });
    goBtn.disabled = false;
    if (r.ok) location.reload();
    else { codeMsg.textContent = r.error || t("authCodeFailed"); codeMsg.style.color = "#b23b3b"; }
  };
  goBtn.addEventListener("click", submitCode);
  codeInput.addEventListener("keydown", (e) => { if (e.key === "Enter") submitCode(); });
  codeWrap.append(askBtn, codeInput, goBtn, codeMsg);

  // Поля токена здесь больше нет. Токен — root-credential из файла на
  // сервере, он не истекает, и предлагать его как равный вариант рядом с
  // одноразовым кодом значит подталкивать к худшему из двух. Аварийный путь
  // никуда не делся: панель по-прежнему открывается по ?token=… через
  // SSH-туннель, и об этом сказано строкой ниже.
  const fallback = document.createElement("div");
  fallback.textContent = t("authTunnelFallback");
  fallback.setAttribute("style", "margin-top:14px;font-size:12px;opacity:.6");

  card.append(title, hint, codeWrap, fallback);
  overlay.append(card);
  document.body.append(overlay);
  askBtn.focus();
}

// Панель может отдаваться не с корня, а из-под секретного пути мини-аппа
// (https://домен/app-xxxx/). Все вызовы вида "/api/…" в этом случае уходили на
// корень домена — то есть на сайт-маскировку — и возвращали 404. Считаем
// префикс из адреса страницы один раз и клеим его ко всем запросам.
const API_BASE = (() => {
  const p = location.pathname.replace(/\/[^/]*$/, "");
  return p === "/" ? "" : p;
})();

function apiUrl(path) {
  return path.startsWith("/") ? API_BASE + path : path;
}

// Подпись Telegram: сервер (verify_telegram_init_data) ждёт её в заголовке
// X-Telegram-Init-Data и по ней пускает админа без кода. Раньше клиент её
// вообще не отправлял — серверная проверка стояла мёртвым кодом.
const TG_INIT_DATA = (() => {
  try {
    const wa = window.Telegram && window.Telegram.WebApp;
    if (!wa || !wa.initData) return "";
    wa.ready();
    try { wa.expand(); } catch (e) { /* старые клиенты */ }
    return wa.initData;
  } catch (e) {
    return "";
  }
})();

async function api(path, options = {}) {
  const { timeout, ...rest } = options;
  const headers = {
    "Accept": "application/json",
    "X-GoTelegram-Admin": "1",
    ...(adminToken ? { "X-GoTelegram-Token": adminToken } : {}),
    ...(TG_INIT_DATA ? { "X-Telegram-Init-Data": TG_INIT_DATA } : {}),
    ...(rest.headers || {}),
  };
  if (rest.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
  const budget = Number(timeout) > 0 ? Number(timeout) : API_TIMEOUT_MS;
  const guard = timeoutSignal(budget);
  let res;
  try {
    res = await fetch(apiUrl(path), { ...rest, headers, credentials: "same-origin", signal: guard.signal });
  } catch (err) {
    if (err && (err.name === "TimeoutError" || err.name === "AbortError")) {
      throw new Error(`${t("requestTimeout")} (${Math.round(budget / 1000)}s)`);
    }
    throw err;
  } finally {
    guard.cancel();
  }
  if (res.status === 401) {
    // Сервер говорит, что именно случилось: идёт окно продления или сессия
    // закончилась совсем. Одинаковый экран на оба случая заставлял человека
    // заново искать токен там, где хватило бы кода.
    const info = await res.clone().json().catch(() => ({}));
    handleAuthState(info.state || "none", Number(info.seconds) || 0);
    throw new Error(info.error || t("authRequired"));
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.ok === false) throw new Error(data.error || `HTTP ${res.status}`);
  // Запрос прошёл — значит сессия уже есть, и держать токен дальше незачем.
  // Пока он лежит в sessionStorage и уходит с каждым запросом, срок сессии
  // ничего не решает: вкладка входила бы заново сама, вечно.
  if (adminToken) {
    adminToken = "";
    storeToken("");
  }
  return data.data ?? data;
}


let _warpInFlight = false;
let _warpLastFetch = 0;
async function renderWarp() {
  if (_warpInFlight) return;
  _warpInFlight = true;
  try { await _renderWarpInner(); } finally { _warpInFlight = false; _warpLastFetch = Date.now(); }
}
async function _renderWarpInner() {
  const el = document.getElementById("warpBody");
  if (!el) return;
  try {
    const d = await api("/api/warp", { timeout: API_TIMEOUT_WARP_MS });
    const on = d.warp_egress === "on";
    const routing = d.routing === "on";
    const colo = d.colo ? (" · " + escapeHtml(d.colo)) : "";
    const hs = (typeof d.handshake === "number" && d.handshake >= 0) ? (d.handshake + "с назад") : "—";
    el.innerHTML =
      '<div style="margin-bottom:6px">Состояние: <b>' + (on ? "🟢 ВКЛ" : "⚪ ВЫКЛ") + '</b> · тариф: ' + escapeHtml(d.tier || "?") + ' · маршрут: ' + (routing ? "Telegram→Cloudflare" : "напрямую") + '</div>' +
      '<div style="margin-bottom:6px;font-size:12px;opacity:.7">Egress через WARP: <code>' + escapeHtml(d.egress || "?") + '</code>' + colo + ' · туннель: ' + hs + '</div>' +
      '<div id="warpProbeInfo" style="margin-bottom:8px;font-size:13px;min-height:18px"></div>' +
      // Кнопки лежали россыпью, разделённые обычным пробелом между тегами, и
      // класс .btn в styles.css не описан вовсе — то есть не делал ничего.
      // На узком экране (мини-апп ~400px) это давало слипшиеся кнопки и
      // рваный перенос. Кладём их в тот же panel-actions, что и весь остальной
      // интерфейс: flex, gap 8px, перенос. Классы — из существующего набора
      // (ghost/danger), чтобы у действий была понятная иерархия.
      '<div class="panel-actions">' +
      (on
        ? '<button data-warp="probe">📡 Проверить связь</button>' +
          '<button class="danger" data-warp="off">Выключить</button>' +
          '<button class="ghost" data-warp="key">Ключ WARP+</button>'
        : '<button data-warp="on">Включить (free)</button>' +
          '<button class="ghost" data-warp="key">Включить с ключом WARP+</button>') +
      '<button class="ghost" data-warp="refresh">Обновить</button>' +
      '</div>';
    el.querySelectorAll("[data-warp]").forEach((b) => { b.onclick = () => warpAction(b.getAttribute("data-warp")); });
  } catch (e) {
    el.textContent = "Ошибка WARP: " + e.message;
  }
}

// ── WEB-прокси (Telegram Desktop 7.1+) ────────────────────────────────────
// Та же дисциплина, что у WARP: не чаще раза в 25 с из общего цикла обновления
// и никогда двумя запросами одновременно — статус ходит в install.sh.
let _webInFlight = false;
let _webLastFetch = 0;
// Идёт действие (включение, выключение, смена carrier): фоновые перерисовки
// в это время только мешают — возвращают кнопки и сбрасывают выбор.
let _webBusy = false;
// Последний ответ /api/web: при смене языка панель перерисовывается из него,
// не дёргая install.sh. Первая отрисовка случается ещё до того, как язык
// приедет из overview, и без этого до следующего цикла висела английская.
let _webData = null;
const WEB_CARRIERS = ["https", "websocket", "https-lanes", "websocket-lanes"];

async function renderWeb() {
  if (_webInFlight || _webBusy) return;
  _webInFlight = true;
  try { await _renderWebInner(); } finally { _webInFlight = false; _webLastFetch = Date.now(); }
}

async function _renderWebInner(fromCache = false) {
  const el = document.getElementById("webBody");
  if (!el) return;
  try {
    const d = (fromCache && _webData) ? _webData : await api("/api/web", { timeout: API_TIMEOUT_WARP_MS });
    // Нет поля enabled — статус не получен (сервер отдал ошибку): рисуем её, а
    // не «выключен» с кнопкой «Включить».
    if (!d || typeof d.enabled !== "boolean") {
      _webData = null;
      el.innerHTML = `<div style="margin-bottom:8px">⚠️ ${escapeHtml(String((d && d.message) || t("webError")))}</div>` +
        `<div class="panel-actions"><button class="ghost" data-web="refresh">${escapeHtml(t("webRefresh"))}</button></div>`;
      el.querySelectorAll("[data-web]").forEach((b) => { b.onclick = () => webAction(b.getAttribute("data-web")); });
      return;
    }
    _webData = d;
    const on = d.enabled === true;
    const bridge = d.bridge === "ok" ? "✓" : (d.bridge === "fail" ? "✗" : "—");
    let html = `<div style="margin-bottom:6px">${escapeHtml(t("webState"))}: <b>${escapeHtml(on ? t("webOn") : t("webOff"))}</b>`;
    if (on) html += ` · carrier: <code>${escapeHtml(String(d.carrier || ""))}</code> · bridge: <b>${bridge}</b>`;
    html += `</div>`;
    if (!on && d.blocker) {
      html += `<div style="margin-bottom:6px;font-size:13px;opacity:.8">${escapeHtml(t("webBlock_" + d.blocker).replace("{v}", String(d.min_core || "")))}</div>`;
    }
    if (on && d.link) {
      html += `<div style="margin-bottom:6px;font-size:12px;overflow-wrap:anywhere"><code>${escapeHtml(String(d.link))}</code></div>`;
    }
    html += `<div style="margin-bottom:8px;font-size:12px;opacity:.7">${escapeHtml(t("webLinkHint"))}</div>`;
    html += '<div class="panel-actions">';
    if (on) {
      html += `<button class="danger" data-web="off">${escapeHtml(t("webDisable"))}</button>` +
              `<select data-web-carrier>` + WEB_CARRIERS.map((c) => `<option value="${c}" ${c === d.carrier ? "selected" : ""}>${c}</option>`).join("") + `</select>` +
              `<button class="ghost" data-web="carrier">${escapeHtml(t("webSetCarrier"))}</button>` +
              (d.link ? `<button class="ghost" data-copy="${escapeAttr(String(d.link))}">${escapeHtml(t("copyWebLink"))}</button>` : "");
    } else if (d.configured === true) {
      // настроен, но не активен (ядро без WEB, нет адреса) — выключить надо дать
      html += `<button class="danger" data-web="off">${escapeHtml(t("webDisable"))}</button>`;
    } else {
      html += `<button data-web="on" ${d.blocker ? "disabled" : ""}>${escapeHtml(t("webEnable"))}</button>`;
    }
    html += `<button class="ghost" data-web="refresh">${escapeHtml(t("webRefresh"))}</button></div>`;
    el.innerHTML = html;
    el.querySelectorAll("[data-web]").forEach((b) => { b.onclick = () => webAction(b.getAttribute("data-web")); });
  } catch (e) {
    el.textContent = t("webError") + ": " + (e && e.message ? e.message : "");
  }
}

async function webAction(act) {
  const el = document.getElementById("webBody");
  if (act === "refresh") { _webLastFetch = 0; renderWeb(); return; }
  const body = { action: act };
  if (act === "carrier") {
    const sel = el ? el.querySelector("[data-web-carrier]") : null;
    body.carrier = sel ? sel.value : "";
  }
  _webBusy = true;
  if (el) el.querySelectorAll("button, select").forEach((b) => { b.disabled = true; });
  try {
    // включение перезапускает telemt и ждёт его готовности — до полутора минут
    const data = await api("/api/web", { method: "POST", body: JSON.stringify(body), timeout: 300000 });
    const message = (data && data.message) ? String(data.message) : t("webDone");
    toast(`WEB: ${message}`);
    addEvent("WEB", message);
  } catch (e) {
    const message = (e && e.message) ? e.message : t("webError");
    toast("WEB: " + message);
    addEvent("WEB", message);
  }
  _webBusy = false;
  _webLastFetch = 0;
  renderWeb();
}

async function warpProbe() {
  const info = document.getElementById("warpProbeInfo");
  if (info) info.textContent = "📡 Проверяю связь с Telegram… (до 15 c)";
  try {
    const d = await api("/api/warp/probe", { timeout: API_TIMEOUT_WARP_MS });
    if (info) {
      if (d && d.reachable) {
        info.innerHTML = "📡 Пинг Telegram: <b>" + escapeHtml(String(d.ping_avg)) + " мс</b> · потери " + escapeHtml(String(d.loss)) + "%";
      } else {
        info.innerHTML = "📡 <b>Telegram недоступен через WARP</b> (потери 100%)";
      }
    }
  } catch (e) {
    if (info) info.textContent = "Ошибка проверки: " + (e && e.message ? e.message : "");
  }
}

async function warpAction(act) {
  const el = document.getElementById("warpBody");
  if (act === "refresh") { renderWarp(); return; }
  if (act === "probe") { warpProbe(); return; }
  let key = "";
  if (act === "key") {
    const raw = prompt("Ключ WARP+ (XXXX-XXXX-XXXX-XXXX). Пусто = free:");
    if (raw === null) { renderWarp(); return; }
    key = raw.trim();
    if (key && !/^(?:[A-Za-z0-9]{4}(?:-[A-Za-z0-9]{4}){3}|[A-Za-z0-9]{8}(?:-[A-Za-z0-9]{8}){2})$/.test(key)) {
      toast("Неверный формат ключа WARP+ (XXXX-XXXX-XXXX-XXXX)");
      renderWarp();
      return;
    }
    act = key ? "key" : "on";
  }
  if (el) el.textContent = "Применяю… (до 4 мин)";
  try {
    // The answer used to be thrown away, so a failure looked like a success.
    const data = await api("/api/warp", {
      method: "POST",
      body: JSON.stringify({ action: act, key: key }),
      timeout: API_TIMEOUT_WARP_ACTION_MS,
    });
    const message = (data && data.message) ? String(data.message) : "готово";
    const warn = data && data.status === "warn";
    toast(warn ? `WARP ⚠️ ${message}` : `WARP: ${message}`);
    addEvent("WARP", message);
  } catch (e) {
    const message = (e && e.message) ? e.message : "ошибка";
    toast("WARP: " + message);
    addEvent("WARP", message);
  }
  renderWarp();
}

function applyI18n() {
  document.documentElement.lang = state.lang;
  $$("[data-i18n]").forEach((el) => {
    el.textContent = t(el.dataset.i18n);
  });
  $$("[data-i18n-placeholder]").forEach((el) => {
    el.placeholder = t(el.dataset.i18nPlaceholder);
  });
  $$("[data-i18n-title]").forEach((el) => {
    el.title = t(el.dataset.i18nTitle);
  });
  $$("[data-i18n-aria-label]").forEach((el) => {
    el.setAttribute("aria-label", t(el.dataset.i18nAriaLabel));
  });
  $("#themeToggle").textContent = state.theme === "dark" ? t("themeLight") : t("themeDark");
  $("#languageSelect").value = state.lang;
  $("#settingsLanguage").textContent = state.lang === "ru" ? "Русский" : "English";
  $("#settingsTheme").textContent = state.theme === "dark" ? t("darkTheme") : t("lightTheme");
  $("#visualTitle").textContent = t("visualTitle");
  $("#visualText").textContent = t("visualText");
  // Не поверх начатого действия: перерисовка возвращала отключённые кнопки и
  // сбрасывала выбранный carrier до нажатия «Применить».
  if (_webData && !_webBusy) _renderWebInner(true);
  updateTrafficControls();
  updateUserTrafficControls();
  renderBackupSchedule();
  updatePageTitle();
  updateAutoRefreshToggle();
}

function setTheme(theme) {
  state.theme = theme === "dark" ? "dark" : "light";
  document.documentElement.dataset.theme = state.theme;
  localStorage.setItem("gotelegram-theme", state.theme);
  applyI18n();
  if (state.overview) renderStats();
  if (state.userTraffic) renderUserTraffic();
}

async function setLanguage(lang) {
  const previous = state.lang;
  state.lang = lang === "ru" ? "ru" : "en";
  applyI18n();
  try {
    const data = await api("/api/settings/language", {
      method: "POST",
      body: JSON.stringify({ language: state.lang }),
    });
    state.lang = data.language === "ru" ? "ru" : "en";
    applyI18n();
    toast(t("languageSaved"));
    await refreshAll();
  } catch (err) {
    state.lang = previous;
    applyI18n();
    toast(err.message);
  }
}

function setPage(page, push = true) {
  // location.hash ends up inside a CSS selector below: a hash like `#a"]`
  // threw a SyntaxError and killed the whole script (dead panel).
  const requested = String(page || "").trim();
  const safe = /^[a-z-]+$/.test(requested) ? requested : "dashboard";
  const next = $(`[data-page="${safe}"]`) ? safe : "dashboard";
  state.page = next;
  $$(".page-panel").forEach((panel) => panel.classList.toggle("active", panel.dataset.page === next));
  $$("[data-nav]").forEach((item) => item.classList.toggle("active", item.dataset.nav === next));
  $("#sidebar").classList.remove("open");
  updatePageTitle();
  if (push && location.hash !== `#${next}`) {
    history.replaceState(null, "", `#${next}`);
  }
  requestAnimationFrame(() => {
    window.scrollTo({ top: 0, behavior: push ? "smooth" : "auto" });
  });
  if (next === "traffic") {
    refreshStats().catch((err) => toast(err.message));
  } else if (next === "keys") {
    ensureUserTrafficSelection();
    renderUserTraffic();
    if (state.userTrafficUser) refreshUserTraffic().catch((err) => toast(err.message));
  }
}

function updatePageTitle() {
  const cap = state.page.charAt(0).toUpperCase() + state.page.slice(1);
  $("#pageTitle").textContent = t(`page${cap}Title`);
  $("#pageKicker").textContent = t(`page${cap}Kicker`);
}

function updateLanguageFromOverview(data) {
  const lang = String(data.language || data.config?.language || "en").toLowerCase();
  state.lang = lang === "ru" ? "ru" : "en";
  applyI18n();
}

function statusLabel(status) {
  const key = `status${String(status || "unknown").replace(/(^|_)([a-z])/g, (_, __, ch) => ch.toUpperCase())}`;
  const label = t(key);
  return label === key ? (status || t("healthUnknown")) : label;
}

function healthLabel(health) {
  const labels = {
    ok: t("healthOk"),
    error: t("healthError"),
    stale: t("healthStale"),
    stopped: t("healthStopped"),
    not_installed: t("healthNotInstalled"),
  };
  return labels[health] || t("healthUnknown");
}

function renderServices(services = {}) {
  const items = [
    { key: "telemt", label: "telemt", api: "telemt" },
    { key: "nginx", label: "nginx", api: "nginx" },
    { key: "bot", label: "bot", api: "gotelegram-bot" },
    { key: "stats", label: "stats", api: "gotelegram-stats" },
    { key: "admin", label: "admin", api: "gotelegram-admin" },
  ];
  $("#services").innerHTML = items.map((item) => {
    const status = services[item.key] || "unknown";
    const disabled = item.key === "admin" || status === "not_installed";
    return `<article class="service status-${escapeAttr(status)}">
      <div>
        <strong>${escapeHtml(item.label)}</strong>
        <span><i></i>${escapeHtml(statusLabel(status))}</span>
      </div>
      <button class="soft" data-restart="${escapeAttr(item.api)}" ${disabled ? "disabled" : ""}>${escapeHtml(t("restart"))}</button>
    </article>`;
  }).join("");
}

function runtimeData() {
  const raw = state.overview?.runtime_summary;
  if (!raw || typeof raw !== "object") return null;
  return raw.data && typeof raw.data === "object" ? raw.data : raw;
}

function renderRuntime() {
  const data = runtimeData();
  if (!data) {
    $("#runtimeCards").innerHTML = `<div class="empty">${escapeHtml(t("noRuntime"))}</div>`;
    $("#runtimeIssues").innerHTML = "";
    return;
  }
  const revision = String(data.revision || state.overview?.runtime_summary?.revision || "--");
  const cards = [
    [t("uptime"), fmtDuration(data.uptime_seconds)],
    [t("connections"), data.connections_total ?? 0],
    [t("badConnections"), data.connections_bad_total ?? 0],
    [t("users"), data.configured_users ?? state.overview?.users_count ?? 0],
    [t("revision"), revision.slice(0, 10)],
  ];
  $("#runtimeCards").innerHTML = cards.map(([label, value]) => `
    <article>
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
    </article>
  `).join("");
  const bad = Array.isArray(data.connections_bad_by_class) ? data.connections_bad_by_class : [];
  $("#runtimeIssues").innerHTML = bad.length ? bad.map((item) => `
    <div class="issue">
      <span>${escapeHtml(item.class || "unknown")}</span>
      <strong>${escapeHtml(item.total ?? 0)}</strong>
    </div>
  `).join("") : "";
}

function siteStatusText(site = {}) {
  if (!site.host) return t("siteMissing");
  if (site.error === "invalid_domain") return t("siteInvalid");
  if (site.ok) return t("siteOk");
  if (site.checked && site.http_code) return `${t("siteHttp")} ${site.http_code}`;
  if (site.error) return t("siteError");
  return t("siteNotChecked");
}

function siteStatusClass(site = {}) {
  if (site.ok) return "ok";
  if (!site.host || !site.checked) return "warn";
  return "error";
}

function renderSiteStatus() {
  const cfg = state.overview?.config || {};
  const site = state.overview?.site_status || {};
  $("#metricDomain").textContent = site.host || cfg.domain || cfg.mask_host || "--";
  const statusEl = $("#siteStatus");
  statusEl.textContent = siteStatusText(site);
  statusEl.className = `metric-status ${siteStatusClass(site)}`;
  statusEl.title = site.url || "";
}

function roleLabel(role) {
  const key = `role${String(role || "other").replace(/(^|_)([a-z])/g, (_, __, ch) => ch.toUpperCase())}`;
  const label = t(key);
  return label === key ? t("roleOther") : label;
}

// Карточка появляется только при нескольких внешних адресах. На обычной машине
// строка была бы одна и повторяла бы то, что уже написано в «адресе привязки».
function renderEdgeIps(payload = {}) {
  const card = $("#edgeIpsCard");
  if (!card) return;
  const ips = Array.isArray(payload.ips) ? payload.ips : [];
  if (ips.length < 2) { card.hidden = true; return; }
  card.hidden = false;

  const dom = $("#edgeIpsDomain");
  if (dom) dom.textContent = payload.domain || "";

  // Плашка только при поломке: пока запасной вход цел, писать о нём нечего.
  const warnBox = $("#edgeIpsWarn");
  if (warnBox) {
    const ssh = payload.ssh || {};
    const text = String(ssh.warning || "").trim();
    if (ssh.ok === false && text) {
      warnBox.hidden = false;
      warnBox.innerHTML =
        `<strong>${escapeHtml(t("edgeSshBad"))}</strong> ${escapeHtml(text)}`;
    } else {
      warnBox.hidden = true;
      warnBox.textContent = "";
    }
  }

  const body = $("#edgeIpsBody");
  body.innerHTML = "";
  const head = document.createElement("div");
  head.className = "edge-row edge-head";
  head.innerHTML =
    `<span>${escapeHtml(t("edgeAddr"))}</span>` +
    `<span>${escapeHtml(t("edgeW15"))}</span><span>${escapeHtml(t("edgeW1h"))}</span>` +
    `<span>${escapeHtml(t("edgeW1d"))}</span>` +
    `<span>${escapeHtml(t("edgeConns"))}</span><span>${escapeHtml(t("edgeReach"))}</span>`;
  body.appendChild(head);

  ips.forEach((e) => {
    const row = document.createElement("div");
    row.className = "edge-row";
    const reach = String(e.reach || "");
    const mark = reach === "ok" ? "✓" : (reach === "down" ? "✗" : "⚠");
    const cls = reach === "ok" ? "ok" : (reach === "down" ? "err" : "warn");
    row.innerHTML =
      `<span class="edge-ip">${escapeHtml(String(e.ip || ""))}</span>` +
      `<span data-label="${escapeAttr(t("edgeW15"))}">${fmtBytes(e.b15m)}</span>` +
      `<span data-label="${escapeAttr(t("edgeW1h"))}">${fmtBytes(e.b1h)}</span>` +
      `<span data-label="${escapeAttr(t("edgeW1d"))}">${fmtBytes(e.b1d)}</span>` +
      `<span data-label="${escapeAttr(t("edgeConns"))}">${Number(e.conns) || 0}</span>` +
      `<span class="edge-reach ${cls}">${mark}</span>`;
    body.appendChild(row);
  });
}

function renderPort443(payload = {}) {
  const listeners = Array.isArray(payload.listeners) ? payload.listeners : [];
  const routes = Array.isArray(payload.routes) ? payload.routes : [];
  const summary = $("#port443Summary");
  const list = $("#port443List");
  const configuredPort = Number(payload.configured_port) || 443;
  $("#port443Number").textContent = "443";
  $("#port443Configured").textContent = configuredPort === 443 ? t("port443Public") : t("port443Configured").replace("{port}", configuredPort);
  if (payload.error) {
    summary.textContent = t("port443Error");
    summary.className = "port-status error";
  } else if (!listeners.length) {
    summary.textContent = t("port443NoListeners");
    summary.className = "port-status warn";
  } else {
    summary.textContent = `${listeners.length} ${t("port443Listeners")}${routes.length ? ` · ${routes.length} ${t("port443Routes")}` : ""}`;
    summary.className = "port-status ok";
  }
  const listenerHtml = listeners.length ? listeners.map((item) => {
    const title = `${item.proto || ""} ${item.address || ""} · ${item.process || "unknown"}${item.pid ? ` · pid ${item.pid}` : ""}`;
    return `<article class="port-listener role-${escapeAttr(item.role || "other")}" title="${escapeAttr(title)}">
      <div>
        <strong>${escapeHtml(roleLabel(item.role))}</strong>
        <span>${escapeHtml(item.process || "unknown")}${item.pid ? ` · pid ${escapeHtml(item.pid)}` : ""}</span>
      </div>
      <small>${escapeHtml(item.proto || "--")} · ${escapeHtml(item.address || "--")}</small>
    </article>`;
  }).join("") : `<div class="port-empty">${escapeHtml(payload.error || t("port443NoListeners"))}</div>`;
  const routeHtml = routes.length ? routes.map((item) => {
    const via = item.via ? t("port443Via").replace("{value}", item.via) : "";
    const title = `${item.public || ""} → ${item.target || ""} · ${item.process || ""}`;
    return `<article class="port-listener role-${escapeAttr(item.role || "other")}" title="${escapeAttr(title)}">
      <div>
        <strong>${escapeHtml(roleLabel(item.role))}</strong>
        <span>${escapeHtml(item.process || "unknown")}${item.status ? ` · ${escapeHtml(statusLabel(item.status))}` : ""}</span>
      </div>
      <small>${escapeHtml(item.public || "--")} → ${escapeHtml(item.target || "--")}${via ? ` · ${escapeHtml(via)}` : ""}</small>
    </article>`;
  }).join("") : `<div class="port-empty">${escapeHtml(t("port443NoRoutes"))}</div>`;
  list.innerHTML = `
    <div class="port-section-label">${escapeHtml(t("port443PublicSection"))}</div>
    ${listenerHtml}
    <div class="port-section-label">${escapeHtml(t("port443BehindSection"))}</div>
    ${routeHtml}
  `;
}

function renderOverview() {
  const data = state.overview;
  if (!data) return;
  const cfg = data.config || {};
  const stats = data.stats_current || {};
  const bind = data.admin_bind || {};
  $("#sidebarVersion").textContent = `v${data.version || "--"}`;
  const local = `${bind.host || "127.0.0.1"}:${bind.port || 1984}`;
  $("#sidebarBind").textContent = local;
  // В подвале места на одну строку, поэтому там остаётся сокет, а полная
  // картина — в настройках: сокет, публичный адрес и работает ли вход по коду.
  const pub = bind.public_url || "";
  const bindEl = $("#settingsBind");
  bindEl.textContent = local;
  const extra = $("#settingsPublic");
  if (extra) {
    if (pub) {
      extra.innerHTML = "";
      const a = document.createElement("a");
      a.href = pub; a.textContent = pub; a.target = "_blank"; a.rel = "noopener";
      extra.appendChild(a);
      const note = document.createElement("small");
      note.className = bind.tfa ? "ok" : "warn";
      note.textContent = bind.tfa ? t("panelCodeReady") : t("panelCodeNeedsBot");
      extra.appendChild(note);
    } else {
      extra.textContent = t("panelNotPublished");
    }
  }
  $("#metricMode").textContent = cfg.mode || "--";
  renderSiteStatus();
  renderPort443(data.port_443 || {});
  renderEdgeIps(data.edge_ips || {});
  $("#metricUsers").textContent = data.users_count ?? 0;
  $("#metricProxyTraffic").textContent = fmtBytes(stats.proxy_bytes);
  $("#metricProxyPackets").textContent = `${stats.proxy_pkts || 0} ${t("packets")}`;
  $("#metricSiteTraffic").textContent = fmtBytes(stats.site_bytes);
  $("#metricSitePackets").textContent = `${stats.site_pkts || 0} ${t("packets")}`;
  $("#lastRefresh").textContent = fmtDate(Math.floor(Date.now() / 1000));
  renderServices(data.services || {});
  renderRuntime();
  renderStats();
  renderBackups(data.backups || []);
  renderConfig();
}

function statsPayload() {
  if (state.stats) return state.stats;
  return {
    current: state.overview?.stats_current || {},
    history: state.overview?.stats_history || [],
    status: state.overview?.stats_status || {},
    summary_rows: [],
  };
}

function updateTrafficControls() {
  $$("[data-traffic-range]").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.trafficRange === state.trafficRange);
  });
  $$("[data-traffic-view]").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.trafficView === state.trafficView);
  });
}

function updateUserTrafficControls() {
  $$("[data-user-traffic-range]").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.userTrafficRange === state.userTrafficRange);
  });
  $$("[data-user-traffic-view]").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.userTrafficView === state.userTrafficView);
  });
}

function trafficRangeLabel(range) {
  const labels = {
    "15m": t("range15m"),
    "1h": t("range1h"),
    "24h": t("range24h"),
    month: t("rangeMonth"),
  };
  return labels[range] || range;
}

function rangeSeconds(range) {
  return {
    "15m": 15 * 60,
    "1h": 60 * 60,
    "24h": 24 * 60 * 60,
    month: 30 * 24 * 60 * 60,
  }[range] || 60 * 60;
}

function filterTrafficRows(rows, range = state.trafficRange) {
  if (!Array.isArray(rows) || !rows.length) return [];
  const latest = Math.max(...rows.map((row) => Number(row.epoch) || 0));
  const cutoff = latest - rangeSeconds(range);
  return rows.filter((row) => (Number(row.epoch) || 0) >= cutoff);
}

function bucketTrafficRows(rows) {
  const filtered = filterTrafficRows(rows);
  if (filtered.length <= 140) return filtered;
  const chunk = Math.ceil(filtered.length / 120);
  const buckets = [];
  for (let i = 0; i < filtered.length; i += chunk) {
    const slice = filtered.slice(i, i + chunk);
    const last = slice[slice.length - 1];
    buckets.push({
      epoch: last.epoch,
      proxy_delta: slice.reduce((sum, item) => sum + (Number(item.proxy_delta) || 0), 0),
      site_delta: slice.reduce((sum, item) => sum + (Number(item.site_delta) || 0), 0),
      proxy_bytes: last.proxy_bytes,
      site_bytes: last.site_bytes,
    });
  }
  return buckets;
}

function fallbackTrafficSummaries(rows) {
  return trafficRanges.map((range) => {
    const windowRows = filterTrafficRows(rows, range);
    if (!windowRows.length) {
      return { range, points: 0, proxy_delta: 0, site_delta: 0, proxy_total: 0, site_total: 0 };
    }
    const first = windowRows[0];
    const last = windowRows[windowRows.length - 1];
    return {
      range,
      points: windowRows.length,
      proxy_delta: windowRows.reduce((sum, item) => sum + Math.max(0, Number(item.proxy_delta) || 0), 0),
      site_delta: windowRows.reduce((sum, item) => sum + Math.max(0, Number(item.site_delta) || 0), 0),
      proxy_total: Number(last.proxy_bytes) || 0,
      site_total: Number(last.site_bytes) || 0,
    };
  });
}

function renderTrafficLoading() {
  $("#trafficChart").classList.toggle("is-hidden", state.trafficView !== "chart");
  $("#trafficTableWrap").classList.toggle("is-hidden", state.trafficView !== "table");
  $("#trafficChart").innerHTML = `<div class="empty-chart"><strong>${escapeHtml(t("loading"))}</strong></div>`;
  $("#historyTable").innerHTML = `<tr><td colspan="5" class="empty-cell">${escapeHtml(t("loading"))}</td></tr>`;
}

function renderStats() {
  const payload = statsPayload();
  const status = payload.status || {};
  const stats = payload.current || {};
  const historyRows = payload.history || [];
  const summaryRows = payload.summary_rows?.length ? payload.summary_rows : fallbackTrafficSummaries(historyRows);
  $("#statsHealth").className = `status-pill health-${escapeAttr(status.health || "unknown")}`;
  $("#statsHealth").textContent = healthLabel(status.health);
  $("#collectorState").textContent = status.service ? statusLabel(status.service) : "--";
  $("#lastStatsPoint").textContent = status.last_ts ? fmtDate(status.last_ts) : t("never");
  $("#historyRows").textContent = status.history_rows ?? historyRows.length;
  $("#repairStatsBtn").classList.toggle("attention", status.health !== "ok");
  $("#collectStatsBtn").disabled = status.service === "not_installed";
  $("#metricProxyTraffic").textContent = fmtBytes(stats.proxy_bytes);
  $("#metricSiteTraffic").textContent = fmtBytes(stats.site_bytes);
  updateTrafficControls();
  if (state.trafficLoading) {
    renderTrafficLoading();
    return;
  }
  $("#trafficChart").classList.toggle("is-hidden", state.trafficView !== "chart");
  $("#trafficTableWrap").classList.toggle("is-hidden", state.trafficView !== "table");
  drawTrafficChart(historyRows);
  renderHistoryTable(summaryRows);
}

function drawTrafficChart(rows) {
  const el = $("#trafficChart");
  const points = bucketTrafficRows(rows);
  const proxyColor = getComputedStyle(document.documentElement).getPropertyValue("--blue").trim() || "#2563eb";
  const siteColor = getComputedStyle(document.documentElement).getPropertyValue("--green").trim() || "#0f9f6e";
  if (points.length < 2) {
    el.innerHTML = `<div class="empty-chart">
      <strong>${escapeHtml(points.length ? t("noTrafficForRange") : t("noHistory"))}</strong>
      <span>${escapeHtml(state.overview?.stats_status?.health === "ok" ? t("statsOk") : t("statsMissing"))}</span>
    </div>`;
    return;
  }
  const width = 900;
  const height = 300;
  const pad = { l: 54, r: 22, t: 24, b: 42 };
  const max = Math.max(1, ...points.map((p) => Math.max(p.proxy_delta || 0, p.site_delta || 0)));
  const plotW = width - pad.l - pad.r;
  const plotH = height - pad.t - pad.b;
  const toX = (i) => pad.l + (plotW * i) / Math.max(1, points.length - 1);
  const toY = (v) => pad.t + plotH - ((v || 0) / max) * plotH;
  const pathFor = (key) => points.map((p, i) => `${i === 0 ? "M" : "L"}${toX(i).toFixed(1)},${toY(p[key]).toFixed(1)}`).join(" ");
  const grid = Array.from({ length: 5 }, (_, i) => {
    const y = pad.t + (plotH / 4) * i;
    return `<line x1="${pad.l}" y1="${y}" x2="${width - pad.r}" y2="${y}"></line>`;
  }).join("");
  const axis = t("chartMax").replace("{value}", fmtBytes(max));
  el.innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeAttr(t("ariaTrafficHistory"))}">
    <g class="grid">${grid}</g>
    <path class="area proxy-area" d="${pathFor("proxy_delta")} L${width - pad.r},${height - pad.b} L${pad.l},${height - pad.b} Z"></path>
    <path class="line proxy-line" d="${pathFor("proxy_delta")}"></path>
    <path class="line site-line" d="${pathFor("site_delta")}"></path>
    <text x="${pad.l}" y="17" class="axis">${escapeHtml(axis)}</text>
    <text x="${pad.l}" y="${height - 12}" class="legend" fill="${proxyColor}">${escapeHtml(t("chartProxy"))}</text>
    <text x="${pad.l + 86}" y="${height - 12}" class="legend" fill="${siteColor}">${escapeHtml(t("chartSite"))}</text>
  </svg>`;
}

function renderHistoryTable(rows) {
  if (!rows.length) {
    $("#historyTable").innerHTML = `<tr><td colspan="5" class="empty-cell">${escapeHtml(t("noHistory"))}</td></tr>`;
    return;
  }
  $("#historyTable").innerHTML = rows.map((row) => `
    <tr>
      <td data-label="${escapeAttr(t("tablePeriod"))}"><strong>${escapeHtml(trafficRangeLabel(row.range))}</strong><small>${escapeHtml(row.points ? `${row.points} ${t("historyRows").toLowerCase()}` : t("noTrafficForRange"))}</small></td>
      <td data-label="${escapeAttr(t("tableProxyDelta"))}">${escapeHtml(fmtBytes(row.proxy_delta))}</td>
      <td data-label="${escapeAttr(t("tableSiteDelta"))}">${escapeHtml(fmtBytes(row.site_delta))}</td>
      <td data-label="${escapeAttr(t("tableProxyTotal"))}">${escapeHtml(fmtBytes(row.proxy_total))}</td>
      <td data-label="${escapeAttr(t("tableSiteTotal"))}">${escapeHtml(fmtBytes(row.site_total))}</td>
    </tr>
  `).join("");
}

function ensureUserTrafficSelection() {
  if (state.userTrafficUser && state.users.some((user) => user.name === state.userTrafficUser)) return;
  state.userTrafficUser = state.users[0]?.name || "";
}

async function selectUserTraffic(name, options = {}) {
  const next = String(name || "");
  if (!next || !state.users.some((user) => user.name === next)) return;
  const changed = state.userTrafficUser !== next;
  state.userTrafficUser = next;
  if (changed) {
    state.userTraffic = null;
  }
  renderUsers();
  renderUserTraffic();
  if (options.scroll) {
    $("#userTrafficPanel")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  try {
    await refreshUserTraffic({ showLoading: true });
  } catch (err) {
    toast(err.message);
  }
}

function userTrafficRows() {
  return state.userTraffic?.history || [];
}

function bucketUserTrafficRows(rows) {
  const filtered = filterTrafficRows(rows, state.userTrafficRange);
  if (filtered.length <= 140) return filtered;
  const chunk = Math.ceil(filtered.length / 120);
  const buckets = [];
  for (let i = 0; i < filtered.length; i += chunk) {
    const slice = filtered.slice(i, i + chunk);
    const last = slice[slice.length - 1];
    buckets.push({
      epoch: last.epoch,
      total_delta: slice.reduce((sum, item) => sum + (Number(item.total_delta) || 0), 0),
      total_octets: last.total_octets,
      current_connections: last.current_connections,
      active_unique_ips: last.active_unique_ips,
    });
  }
  return buckets;
}

function fallbackUserTrafficSummaries(rows) {
  return trafficRanges.map((range) => {
    const windowRows = filterTrafficRows(rows, range);
    if (!windowRows.length) {
      return { range, points: 0, total_delta: 0, total_octets: 0 };
    }
    const last = windowRows[windowRows.length - 1];
    return {
      range,
      points: windowRows.length,
      total_delta: windowRows.reduce((sum, item) => sum + Math.max(0, Number(item.total_delta) || 0), 0),
      total_octets: Number(last.total_octets) || 0,
    };
  });
}

function renderUserTrafficLoading() {
  $("#userTrafficChart").classList.toggle("is-hidden", state.userTrafficView !== "chart");
  $("#userTrafficTableWrap").classList.toggle("is-hidden", state.userTrafficView !== "table");
  $("#userTrafficChart").innerHTML = `<div class="empty-chart"><strong>${escapeHtml(t("loading"))}</strong></div>`;
  $("#userTrafficTable").innerHTML = `<tr><td colspan="3" class="empty-cell">${escapeHtml(t("loading"))}</td></tr>`;
}

function drawUserTrafficChart(rows) {
  const el = $("#userTrafficChart");
  const points = bucketUserTrafficRows(rows);
  const color = getComputedStyle(document.documentElement).getPropertyValue("--blue").trim() || "#2563eb";
  if (points.length < 2) {
    el.innerHTML = `<div class="empty-chart">
      <strong>${escapeHtml(state.userTrafficUser ? t("noTrafficForRange") : t("selectUserTraffic"))}</strong>
      <span>${escapeHtml(state.userTraffic?.status?.runtime_ok ? t("statsOk") : t("trafficRuntimeUnavailable"))}</span>
    </div>`;
    return;
  }
  const width = 900;
  const height = 260;
  const pad = { l: 54, r: 22, t: 24, b: 42 };
  const max = Math.max(1, ...points.map((p) => Number(p.total_delta) || 0));
  const plotW = width - pad.l - pad.r;
  const plotH = height - pad.t - pad.b;
  const toX = (i) => pad.l + (plotW * i) / Math.max(1, points.length - 1);
  const toY = (v) => pad.t + plotH - ((v || 0) / max) * plotH;
  const path = points.map((p, i) => `${i === 0 ? "M" : "L"}${toX(i).toFixed(1)},${toY(p.total_delta).toFixed(1)}`).join(" ");
  const grid = Array.from({ length: 5 }, (_, i) => {
    const y = pad.t + (plotH / 4) * i;
    return `<line x1="${pad.l}" y1="${y}" x2="${width - pad.r}" y2="${y}"></line>`;
  }).join("");
  const axis = t("chartMax").replace("{value}", fmtBytes(max));
  el.innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeAttr(t("ariaTrafficHistory"))}">
    <g class="grid">${grid}</g>
    <path class="area proxy-area" d="${path} L${width - pad.r},${height - pad.b} L${pad.l},${height - pad.b} Z"></path>
    <path class="line proxy-line" d="${path}"></path>
    <text x="${pad.l}" y="17" class="axis">${escapeHtml(axis)}</text>
    <text x="${pad.l}" y="${height - 12}" class="legend" fill="${color}">${escapeHtml(state.userTrafficUser || t("users"))}</text>
  </svg>`;
}

function renderUserTrafficTable(rows) {
  if (!rows.length) {
    $("#userTrafficTable").innerHTML = `<tr><td colspan="3" class="empty-cell">${escapeHtml(t("noHistory"))}</td></tr>`;
    return;
  }
  $("#userTrafficTable").innerHTML = rows.map((row) => `
    <tr>
      <td data-label="${escapeAttr(t("tablePeriod"))}"><strong>${escapeHtml(trafficRangeLabel(row.range))}</strong><small>${escapeHtml(row.points ? `${row.points} ${t("historyRows").toLowerCase()}` : t("noTrafficForRange"))}</small></td>
      <td data-label="${escapeAttr(t("tableTrafficDelta"))}">${escapeHtml(fmtBytes(row.total_delta))}</td>
      <td data-label="${escapeAttr(t("tableTrafficTotal"))}">${escapeHtml(fmtBytes(row.total_octets))}</td>
    </tr>
  `).join("");
}

function renderUserTraffic() {
  updateUserTrafficControls();
  if (!state.userTrafficUser) {
    $("#userTrafficTitle").textContent = t("userTrafficTitle");
    $("#userTrafficHealth").className = "status-pill health-unknown";
    $("#userTrafficHealth").textContent = "--";
    $("#userTrafficTotal").textContent = "--";
    $("#userTrafficConnections").textContent = "--";
    $("#userTrafficIps").textContent = "--";
    $("#userTrafficChart").innerHTML = `<div class="empty-chart"><strong>${escapeHtml(t("selectUserTraffic"))}</strong></div>`;
    $("#userTrafficTable").innerHTML = `<tr><td colspan="3" class="empty-cell">${escapeHtml(t("selectUserTraffic"))}</td></tr>`;
    return;
  }
  $("#userTrafficTitle").textContent = `${t("userTrafficTitle")}: ${state.userTrafficUser}`;
  if (state.userTrafficLoading) {
    renderUserTrafficLoading();
    return;
  }
  const payload = state.userTraffic || {};
  const current = payload.current || {};
  const rows = userTrafficRows();
  const last = rows[rows.length - 1] || {};
  const total = Number(current.total_octets) || Number(last.total_octets) || 0;
  $("#userTrafficHealth").className = `status-pill ${current.enabled === false ? "health-stopped" : (current.ok ? "health-ok" : "health-stale")}`;
  $("#userTrafficHealth").textContent = current.enabled === false ? t("disabled") : (current.ok ? t("healthOk") : t("trafficRuntimeUnavailable"));
  $("#userTrafficTotal").textContent = fmtBytes(total);
  $("#userTrafficConnections").textContent = current.current_connections ?? last.current_connections ?? 0;
  $("#userTrafficIps").textContent = current.active_unique_ips ?? last.active_unique_ips ?? 0;
  $("#userTrafficChart").classList.toggle("is-hidden", state.userTrafficView !== "chart");
  $("#userTrafficTableWrap").classList.toggle("is-hidden", state.userTrafficView !== "table");
  drawUserTrafficChart(rows);
  renderUserTrafficTable(payload.summary_rows?.length ? payload.summary_rows : fallbackUserTrafficSummaries(rows));
}

let pendingUsersRender = false;

function isTypingInUsersTable() {
  const container = $("#usersTable");
  const active = document.activeElement;
  if (!container || !active || active === document.body) return false;
  if (!container.contains(active)) return false;
  const tag = (active.tagName || "").toLowerCase();
  if (tag === "textarea") return true;
  return tag === "input" && (active.type || "text") !== "checkbox";
}

function renderUsers() {
  const container = $("#usersTable");
  if (!container) return;
  // The table is rebuilt from scratch every 5s; re-rendering while the
  // operator types into "IP limit" wiped both focus and the typed value.
  if (isTypingInUsersTable()) {
    pendingUsersRender = true;
    return;
  }
  pendingUsersRender = false;
  if (!state.users.length) {
    container.innerHTML = `<div class="empty empty-cell">${escapeHtml(t("noKeys"))}</div>`;
    return;
  }
  container.innerHTML = state.users.map((user) => {
    const pending = state.pendingUsers.has(user.name);
    const selected = user.name === state.userTrafficUser;
    const traffic = user.traffic || {};
    const trafficTotal = Number(traffic.total_octets) ? fmtBytes(traffic.total_octets) : "--";
    const activeIps = Number(traffic.active_unique_ips) || 0;
    const maxUniqueIps = Number.isFinite(Number(user.max_unique_ips)) ? Math.max(0, Number(user.max_unique_ips)) : 0;
    return `
    <article class="key-card ${user.enabled ? "" : "disabled-row"} ${pending ? "pending-row" : ""} ${selected ? "selected-row" : ""}" data-select-user-traffic="${escapeAttr(user.name)}" aria-selected="${selected ? "true" : "false"}">
      <div class="key-card-user">
        <span class="field-label">${escapeHtml(t("tableUser"))}</span>
        <button class="key-name-button" type="button" data-user-traffic="${escapeAttr(user.name)}">
          <strong>${escapeHtml(user.name)}</strong>${user.main ? ` <small>${escapeHtml(t("main"))}</small>` : ""}
        </button>
        <div class="status-control">
          <label class="switch" title="${escapeAttr(user.main ? t("main") : (user.enabled ? t("disableKey") : t("enableKey")))}">
            <input type="checkbox" data-toggle-user="${escapeAttr(user.name)}" ${user.enabled ? "checked" : ""} ${user.main || pending ? "disabled" : ""}>
            <span></span>
          </label>
          <strong class="${user.enabled ? "state-on" : "state-off"}">${escapeHtml(pending ? t("applying") : (user.enabled ? t("enabled") : t("disabled")))}</strong>
        </div>
      </div>
      <div class="key-card-secret">
        <span class="field-label">${escapeHtml(t("tableSecret"))}</span>
        <code title="${escapeAttr(user.secret)}">${escapeHtml(user.secret)}</code>
      </div>
      <div class="key-card-links">
        <span class="field-label">${escapeHtml(t("tableLink"))}</span>
        <div class="mini-actions">
          <button class="soft" data-copy="${escapeAttr(user.link)}" ${user.enabled ? "" : "disabled"}>${escapeHtml(t("copyLink"))}</button>
          <button class="soft" data-user-qr="${escapeAttr(user.name)}">${escapeHtml(t("showQr"))}</button>
          ${user.web_link ? `
          <button class="soft" data-copy="${escapeAttr(user.web_link)}" ${user.enabled ? "" : "disabled"} title="${escapeAttr(t("webLinkHint"))}">${escapeHtml(t("copyWebLink"))}</button>
          <button class="soft" data-user-webqr="${escapeAttr(user.name)}" title="${escapeAttr(t("webLinkHint"))}">${escapeHtml(t("showWebQr"))}</button>` : ""}
        </div>
      </div>
      <div class="key-card-traffic">
        <span class="field-label">${escapeHtml(t("tableTraffic"))}</span>
        <div class="traffic-cell">
          <div class="traffic-main">
            <span>
              <strong>${escapeHtml(trafficTotal)}</strong>
              <small>${escapeHtml(activeIps ? `${activeIps} ${t("activeIps")}` : fmtDate(traffic.epoch))}</small>
            </span>
            <button class="soft" data-user-traffic="${escapeAttr(user.name)}">${escapeHtml(t("openStats"))}</button>
          </div>
          <form class="ip-limit-control" data-ip-limit-form="${escapeAttr(user.name)}" title="${escapeAttr(t("ipLimitHint"))}">
            <span>${escapeHtml(t("ipLimit"))}</span>
            <input type="number" min="0" max="1000000" step="1" value="${escapeAttr(maxUniqueIps)}" data-ip-limit-input="${escapeAttr(user.name)}" aria-label="${escapeAttr(t("ipLimit"))}: ${escapeAttr(user.name)}">
            <button class="soft" type="submit" data-ip-limit-save="${escapeAttr(user.name)}">${escapeHtml(t("saveIpLimit"))}</button>
          </form>
        </div>
      </div>
      <div class="key-card-actions">
        <span class="field-label">${escapeHtml(t("tableActions"))}</span>
        <div class="action-buttons">
          <button class="soft" data-copy="${escapeAttr(user.secret)}">${escapeHtml(t("copySecret"))}</button>
          <button class="danger" data-delete="${escapeAttr(user.name)}" ${user.main ? "disabled" : ""}>${escapeHtml(t("delete"))}</button>
        </div>
      </div>
    </article>
  `; }).join("");
}

function renderBackups(backups) {
  const box = $("#backupsList");
  renderBackupSchedule();
  if (!backups.length) {
    box.innerHTML = `<div class="empty">${escapeHtml(t("noBackups"))}</div>`;
    return;
  }
  box.innerHTML = backups.map((item) => `
    <div class="backup-item">
      <div>
        <strong>${escapeHtml(item.name)}</strong>
        <span>${escapeHtml(item.path)} · ${escapeHtml(fmtDate(item.mtime))}</span>
      </div>
      <div class="backup-actions">
        <span>${escapeHtml(fmtBytes(item.size))}${item.encrypted ? ` · ${escapeHtml(t("encrypted"))}` : ""}</span>
        <button class="soft" data-restore-backup="${escapeAttr(item.name)}" ${item.encrypted ? "disabled" : ""} title="${escapeAttr(item.encrypted ? t("encryptedRestoreCli") : t("restoreBackup"))}">${escapeHtml(t("restoreBackup"))}</button>
      </div>
    </div>
  `).join("");
}

function renderBackupSchedule() {
  const schedule = state.backupSchedule || state.overview?.backup_schedule || { frequency: "off" };
  const frequency = schedule.frequency || "off";
  $$("[data-backup-schedule]").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.backupSchedule === frequency);
  });
  const next = schedule.next && schedule.next !== "n/a" ? schedule.next : "";
  $("#backupScheduleMeta").textContent = frequency === "off"
    ? t("scheduleDisabled")
    : t("scheduleNext").replace("{value}", next || (schedule.calendar || "--"));
}

function renderEvents() {
  const box = $("#events");
  if (!state.events.length) {
    box.innerHTML = `<div class="empty">${escapeHtml(t("noEvents"))}</div>`;
    return;
  }
  box.innerHTML = state.events.map((item) => `
    <div class="event">
      <strong>${escapeHtml(item.title)}</strong>
      <small>${escapeHtml(item.detail || item.time.toLocaleTimeString())}</small>
    </div>
  `).join("");
}

function renderConfig() {
  const cfg = state.overview?.config || {};
  const site = state.overview?.site_status || {};
  const items = [
    [t("configMode"), cfg.mode || "--"],
    [t("configDomain"), cfg.domain || cfg.mask_host || "--"],
    [t("configSiteStatus"), siteStatusText(site)],
    [t("configTemplate"), cfg.template_id || cfg.template || "--"],
    [t("configVersion"), state.overview?.version || "--"],
    [t("bindAddress"), `${state.overview?.admin_bind?.host || "127.0.0.1"}:${state.overview?.admin_bind?.port || 1984}`],
    [t("publicAddress"), state.overview?.admin_bind?.public_url || t("panelNotPublished")],
  ];
  $("#configList").innerHTML = items.map(([label, value]) => `
    <div>
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
    </div>
  `).join("");
}

async function refreshAll() {
  if (state.refreshingAll) return;
  state.refreshingAll = true;
  if (Date.now() - _warpLastFetch > 25000) renderWarp();
  if (Date.now() - _webLastFetch > 25000) renderWeb();
  const btn = $("#refreshBtn");
  btn.disabled = true;
  try {
    state.overview = await api("/api/overview");
    state.backupSchedule = state.overview.backup_schedule || state.backupSchedule;
    updateLanguageFromOverview(state.overview);
    state.users = await api("/api/users");
    ensureUserTrafficSelection();
    if (!state.stats) {
      state.stats = {
        current: state.overview.stats_current || {},
        history: state.overview.stats_history || [],
        status: state.overview.stats_status || {},
        summary_rows: [],
      };
    } else {
      state.stats = {
        ...state.stats,
        current: state.overview.stats_current || state.stats.current || {},
        status: state.overview.stats_status || state.stats.status || {},
      };
    }
    renderOverview();
    renderUsers();
    if (state.page === "traffic") {
      await refreshStats();
    } else if (state.page === "keys") {
      ensureUserTrafficSelection();
      await refreshUserTraffic();
    }
  } catch (err) {
    toast(err.message);
  } finally {
    btn.disabled = false;
    state.refreshingAll = false;
    updateAutoRefreshToggle();
  }
}

async function refreshUsers() {
  state.users = await api("/api/users");
  renderUsers();
}

async function refreshStats(options = {}) {
  if (options.showLoading) {
    state.trafficLoading = true;
    renderStats();
  }
  try {
    const data = await api(`/api/stats?range=${encodeURIComponent(state.trafficRange)}`);
    state.stats = data;
    return data;
  } finally {
    state.trafficLoading = false;
    renderStats();
  }
}

async function refreshUserTraffic(options = {}) {
  ensureUserTrafficSelection();
  if (!state.userTrafficUser) {
    renderUserTraffic();
    return null;
  }
  if (options.showLoading) {
    state.userTrafficLoading = true;
    renderUserTraffic();
  }
  try {
    const data = await api(`/api/users/${encodeURIComponent(state.userTrafficUser)}/traffic?range=${encodeURIComponent(state.userTrafficRange)}`);
    state.userTraffic = data;
    return data;
  } finally {
    state.userTrafficLoading = false;
    renderUserTraffic();
  }
}

async function changeTrafficRange(range) {
  const next = trafficRanges.includes(range) ? range : "1h";
  if (next === state.trafficRange && state.stats?.range === next) return;
  const previous = state.trafficRange;
  state.trafficRange = next;
  try {
    await refreshStats({ showLoading: true });
  } catch (err) {
    state.trafficRange = previous;
    state.trafficLoading = false;
    renderStats();
    toast(err.message);
  }
}

async function changeUserTrafficRange(range) {
  const next = trafficRanges.includes(range) ? range : "1h";
  if (next === state.userTrafficRange && state.userTraffic?.range === next) return;
  const previous = state.userTrafficRange;
  state.userTrafficRange = next;
  try {
    await refreshUserTraffic({ showLoading: true });
  } catch (err) {
    state.userTrafficRange = previous;
    state.userTrafficLoading = false;
    renderUserTraffic();
    toast(err.message);
  }
}

async function addUser(name) {
  const data = await api("/api/users", {
    method: "POST",
    body: JSON.stringify({ name }),
  });
  addEvent(t("keyCreated"), data.name);
  toast(t("keyCreated"));
  await refreshAll();
}

async function deleteUser(name) {
  await api(`/api/users/${encodeURIComponent(name)}`, { method: "DELETE" });
  addEvent(t("keyDeleted"), name);
  toast(t("keyDeleted"));
  await refreshAll();
}

async function setUserEnabled(name, enabled) {
  const previousUsers = state.users.map((user) => ({ ...user }));
  state.pendingUsers.add(name);
  state.users = state.users.map((user) => user.name === name ? { ...user, enabled } : user);
  renderUsers();
  try {
    const data = await api(`/api/users/${encodeURIComponent(name)}/enabled`, {
      method: "POST",
      body: JSON.stringify({ enabled }),
    });
    state.users = state.users.map((user) => user.name === name ? { ...user, enabled: data.enabled } : user);
    const message = data.enabled ? t("keyEnabled") : t("keyDisabled");
    addEvent(message, name);
    toast(t("changesApplyInBackground"));
    try {
      await refreshUsers();
    } catch (refreshErr) {
      toast(refreshErr.message);
    }
    setTimeout(() => refreshAll().catch((err) => toast(err.message)), 1400);
  } catch (err) {
    state.users = previousUsers;
    toast(err.message);
  } finally {
    state.pendingUsers.delete(name);
    renderUsers();
  }
}

async function setUserMaxUniqueIps(name, value) {
  // Единица запрещена и на сервере; ловим здесь, чтобы объяснить сразу и не гонять
  // запрос впустую. HTML-полем это не выразить: допустимо 0 либо >= 2.
  if (Number(value) === 1) { toast(t("ipLimitMinErr")); return; }
  const limit = Number.parseInt(value, 10);
  if (!Number.isFinite(limit) || limit < 0 || limit > 1000000) {
    toast(t("ipLimitHint"));
    return;
  }
  const form = $$("[data-ip-limit-form]").find((item) => item.dataset.ipLimitForm === name);
  const controls = form ? Array.from(form.querySelectorAll("input, button")) : [];
  controls.forEach((control) => { control.disabled = true; });
  try {
    const data = await api(`/api/users/${encodeURIComponent(name)}/max-ips`, {
      method: "POST",
      body: JSON.stringify({ max_unique_ips: limit }),
    });
    state.users = state.users.map((user) => user.name === name ? { ...user, max_unique_ips: data.max_unique_ips } : user);
    renderUsers();
    addEvent(t("ipLimitSaved"), `${name}: ${data.max_unique_ips}`);
    // Лимит 1 ломает телефон при смене Wi-Fi ↔ LTE — показываем предупреждение сервера, а не глотаем его
    if (data.warning) { addEvent(t("ipLimitSaved"), data.warning); toast(data.warning); }
    else toast(t("changesApplyInBackground"));
    setTimeout(() => refreshAll().catch((err) => toast(err.message)), 1400);
  } catch (err) {
    toast(err.message);
  } finally {
    controls.forEach((control) => { control.disabled = false; });
  }
}

async function createBackup() {
  const btn = $("#createBackupBtn");
  btn.disabled = true;
  try {
    const data = await api("/api/backups", { method: "POST", body: "{}", timeout: API_TIMEOUT_SLOW_MS });
    addEvent(t("backupCreated"), data.path || "");
    toast(t("backupCreated"));
    await refreshAll();
  } catch (err) {
    toast(err.message);
  } finally {
    btn.disabled = false;
  }
}

async function setBackupSchedule(frequency) {
  $$("[data-backup-schedule]").forEach((btn) => { btn.disabled = true; });
  try {
    const data = await api("/api/backups/schedule", {
      method: "POST",
      body: JSON.stringify({ frequency }),
    });
    state.backupSchedule = data.schedule || data;
    renderBackupSchedule();
    addEvent(t("scheduleSaved"), frequency);
    toast(t("scheduleSaved"));
  } catch (err) {
    toast(err.message);
  } finally {
    $$("[data-backup-schedule]").forEach((btn) => { btn.disabled = false; });
  }
}

async function restoreBackup(name) {
  const data = await api("/api/backups/restore", {
    method: "POST",
    body: JSON.stringify({ name }),
  });
  addEvent(t("backupRestoreStarted"), data.name || name);
  toast(t("backupRestoreStarted"));
  setTimeout(() => refreshAll().catch((err) => toast(err.message)), 4000);
}

function showUserQr(name, web = false) {
  const user = state.users.find((item) => item.name === name);
  const link = web ? (user && user.web_link) : (user && user.link);
  if (!user || !link) {
    toast(t("qrUnavailable"));
    return;
  }
  state.qrLink = link || "";
  $("#qrTitle").textContent = `${web ? t("qrWebTitle") : t("qrTitle")} · ${user.name}`;
  $("#qrMeta").textContent = link || "";
  const img = $("#qrImage");
  img.alt = `${user.name} Telegram proxy QR`;
  img.onerror = () => {
    img.removeAttribute("src");
    toast(t("qrUnavailable"));
  };
  img.src = withToken(apiUrl(`/api/users/${encodeURIComponent(user.name)}/qr?ts=${Date.now()}${web ? "&web=1" : ""}`));
  $("#qrModal").hidden = false;
}

async function loadLogs() {
  const service = $("#logService").value;
  const btn = $("#loadLogsBtn");
  btn.disabled = true;
  $("#logsMeta").textContent = "";
  $("#logsBox").textContent = t("loading");
  try {
    const payload = await api(`/api/logs?service=${encodeURIComponent(service)}`);
    if ($("#logService").value === service) {
      const structured = payload && typeof payload === "object";
      const text = typeof payload === "string" ? payload : (payload?.text || "");
      const lines = structured ? (payload.line_count ?? text.split("\n").filter(Boolean).length) : text.split("\n").filter(Boolean).length;
      const stateText = structured ? (payload.ok ? "OK" : `exit ${payload.exit_code ?? "?"}`) : "OK";
      $("#logsMeta").textContent = `${service} · ${lines} ${t("logsLines")} · ${stateText}`;
      $("#logsBox").textContent = text || t("logsNoData");
    }
  } catch (err) {
    $("#logsMeta").textContent = "";
    $("#logsBox").textContent = err.message;
  } finally {
    btn.disabled = false;
  }
}

async function restartService(name) {
  await api(`/api/services/${encodeURIComponent(name)}/restart`, { method: "POST", body: "{}", timeout: 130000 });
  addEvent(t("serviceRestarted"), name);
  toast(`${name} ${t("serviceRestarted").toLowerCase()}`);
  await refreshAll();
}

async function repairStats() {
  const btn = $("#repairStatsBtn");
  btn.disabled = true;
  try {
    await api("/api/stats/repair", { method: "POST", body: "{}", timeout: API_TIMEOUT_SLOW_MS });
    addEvent(t("statsRepaired"));
    toast(t("statsRepaired"));
    await refreshAll();
    await refreshStats();
  } catch (err) {
    toast(err.message);
  } finally {
    btn.disabled = false;
  }
}

async function collectStats() {
  const btn = $("#collectStatsBtn");
  btn.disabled = true;
  try {
    await api("/api/stats/collect", { method: "POST", body: "{}", timeout: 60000 });
    addEvent(t("statsCollected"));
    toast(t("statsCollected"));
    await refreshAll();
    await refreshStats();
  } catch (err) {
    toast(err.message);
  } finally {
    btn.disabled = false;
  }
}

async function copyText(value) {
  try {
    await navigator.clipboard.writeText(value);
    toast(t("copied"));
  } catch (_) {
    const area = document.createElement("textarea");
    area.value = value;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand("copy");
    area.remove();
    toast(ok ? t("copied") : t("copyFailed"));
  }
}

// Внутри мини-аппа Telegram страница живёт в чужом фрейме, и localStorage там
// либо изолирован, либо запрещён. Отметка «промо уже показано» не сохранялась,
// а панель сама обновляется раз в несколько секунд — из-за чего окно промо
// выскакивало снова и снова, и закрыть его было невозможно. Держим отметку ещё
// и в памяти вкладки: даже без хранилища промо покажется один раз за сеанс.
let promoShownThisSession = false;
function maybeShowPromo() {
  if (promoShownThisSession) return;
  const key = "gotelegram-promo-last";
  const now = Math.floor(Date.now() / 1000);
  let last = 0;
  try { last = Number(localStorage.getItem(key) || 0); } catch (e) { last = 0; }
  if (now - last < 86400) { promoShownThisSession = true; return; }
  try { localStorage.setItem(key, String(now)); } catch (e) { /* хранилище закрыто */ }
  promoShownThisSession = true;
  $("#promoModal").hidden = false;
}

document.addEventListener("click", async (eventObj) => {
  const nav = eventObj.target.closest("[data-nav]");
  if (nav) {
    setPage(nav.dataset.nav);
    return;
  }

  const button = eventObj.target.closest("button");
  if (button) {
    if (button.id === "themeToggle") {
      setTheme(state.theme === "dark" ? "light" : "dark");
    } else if (button.id === "menuBtn") {
      $("#sidebar").classList.toggle("open");
    } else if (button.dataset.trafficRange) {
      changeTrafficRange(button.dataset.trafficRange);
    } else if (button.dataset.trafficView) {
      state.trafficView = button.dataset.trafficView === "table" ? "table" : "chart";
      renderStats();
    } else if (button.dataset.userTraffic) {
      selectUserTraffic(button.dataset.userTraffic, { scroll: true });
    } else if (button.dataset.userQr) {
      showUserQr(button.dataset.userQr);
    } else if (button.dataset.userWebqr) {
      showUserQr(button.dataset.userWebqr, true);
    } else if (button.dataset.userTrafficRange) {
      changeUserTrafficRange(button.dataset.userTrafficRange);
    } else if (button.dataset.userTrafficView) {
      state.userTrafficView = button.dataset.userTrafficView === "table" ? "table" : "chart";
      renderUserTraffic();
    } else if (button.dataset.backupSchedule) {
      setBackupSchedule(button.dataset.backupSchedule);
    } else if (button.dataset.restoreBackup) {
      const name = button.dataset.restoreBackup;
      if (confirm(`${t("confirmRestoreBackup")} ${name}?`)) restoreBackup(name).catch((err) => toast(err.message));
    } else if (button.dataset.copy) {
      await copyText(button.dataset.copy);
    } else if (button.dataset.delete) {
      const name = button.dataset.delete;
      if (confirm(`${t("confirmDelete")} ${name}?`)) deleteUser(name).catch((err) => toast(err.message));
    } else if (button.dataset.restart) {
      const name = button.dataset.restart;
      if (confirm(`${t("confirmRestart")} ${name}?`)) restartService(name).catch((err) => toast(err.message));
    }
    return;
  }

  if (eventObj.target.closest("input, select, textarea, label, form")) return;
  const row = eventObj.target.closest("[data-select-user-traffic]");
  if (!row) return;
  selectUserTraffic(row.dataset.selectUserTraffic, { scroll: true });
});

document.addEventListener("change", (eventObj) => {
  const input = eventObj.target.closest("[data-toggle-user]");
  if (!input) return;
  input.disabled = true;
  setUserEnabled(input.dataset.toggleUser, input.checked).catch((err) => {
    input.checked = !input.checked;
    input.disabled = false;
    toast(err.message);
  });
});

$("#addUserForm").addEventListener("submit", (eventObj) => {
  eventObj.preventDefault();
  const input = $("#userName");
  const name = input.value.trim();
  if (!/^[A-Za-z0-9_.-]{1,48}$/.test(name)) {
    toast(t("invalidUser"));
    return;
  }
  input.value = "";
  addUser(name).catch((err) => toast(err.message));
});

document.addEventListener("submit", (eventObj) => {
  const form = eventObj.target.closest("[data-ip-limit-form]");
  if (!form) return;
  eventObj.preventDefault();
  const input = form.querySelector("[data-ip-limit-input]");
  setUserMaxUniqueIps(form.dataset.ipLimitForm, input?.value || "0");
});

$("#refreshBtn").addEventListener("click", refreshAll);
$("#autoRefreshToggle").addEventListener("click", () => setAutoRefresh(!state.autoRefreshEnabled));
$("#languageSelect").addEventListener("change", (eventObj) => setLanguage(eventObj.target.value));
$("#promoClose").addEventListener("click", () => {
  $("#promoModal").hidden = true;
});
$("#qrClose").addEventListener("click", () => {
  $("#qrModal").hidden = true;
});
$("#qrCopyBtn").addEventListener("click", () => {
  if (state.qrLink) copyText(state.qrLink);
});
$("#createBackupBtn").addEventListener("click", createBackup);
$("#loadLogsBtn").addEventListener("click", loadLogs);
$("#repairStatsBtn").addEventListener("click", repairStats);
$("#collectStatsBtn").addEventListener("click", collectStats);
document.addEventListener("focusout", (eventObj) => {
  const container = $("#usersTable");
  if (!container || !container.contains(eventObj.target)) return;
  setTimeout(() => {
    if (pendingUsersRender && !isTypingInUsersTable()) renderUsers();
  }, 0);
});

window.addEventListener("hashchange", () => setPage((location.hash || "#dashboard").slice(1), false));

setPage((location.hash || "#dashboard").slice(1), false);
setTheme(state.theme);
renderEvents();
syncAutoRefreshTimer();
// Опрос сессии — с самого старта: окно продления длится минуту, и узнавать
// о нём по отвалившейся кнопке значит потерять половину окна.
startSessionPoll();
refreshAll();
loadLogs();
maybeShowPromo();
