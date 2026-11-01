# Kaskad PRO — English glossary

С 3.3.2. Единые английские термины для всего продукта: веб (словари `static/**/*-i18n-en.js` — панель, «Схема»,
мастер, страница человека), каталоги Python `lib/kaskad/i18n/catalog/en/*.json`, каталоги bash
`lib/kaskad/i18n/sh/*.en.sh` (установщик, SSH-меню), бот. Механизм и разделение работ —
`k3/handoff/I18N-3.3.2.md`. Новый термин — сначала сюда, потом в тексты.

## Тон и форма

- Естественный английский интерфейса, не дословный перевод. Коротко: «что случилось → что сделать».
- Заголовки, кнопки, пункты меню — sentence case: «Add key», «Default route», не «Add Key».
- Без восклицаний и извинений. «Couldn't…», «Not saved…» — нормально; «Error!» — нет.
- Кавычки — типографские “…” (в строках JS не нужно экранировать). Многоточие — «…», тире — «—».
- SSH-меню и установщик: выбор цифрами, как в русском («1) Yes … 2) No … Choice [1]:»).
- Числа и даты — `Intl` по языку (`en-US`, либо английская локаль браузера, например `en-GB`); единицы — B, KB,
  MB, GB, TB; kbit/s, Mbit/s; min, s, h, d.
- Даты — одно правило везде (ядро `kaskad.i18n.fmt_date`/`fmt_datetime`, SSH `k3_date`, веб `KaskadLang.date`/`dateTime`
  в `boot.js`): дата — «Jan 9, 2027» / «09.01.2027» (никогда 01/09/2027); дата и время, 24 ч — «Oct 12, 02:40» /
  «12.10, 02:40» в текущем году, иначе с годом «Jan 9, 2027, 02:40» / «09.01.2027, 02:40». Без года и времени
  («Jan 9» / «09.01») — только в тесной строке главного меню SSH.
- Формы числа: в английском две — `one|other` («1 device|{n} devices»); код выбирает форму через
  `Intl.PluralRules`. Если русская строка с тремя формами — английская с двумя.
- Подстановки `{имя}` — те же, что в русском тексте; порядок в фразе — любой.
- Названия продуктов и протоколов не переводятся: Kaskad PRO, AmneziaWG 1.5/2.0/3.1, WireGuard, VLESS, Reality,
  xray, L2TP/IPsec, IKEv2, SSTP, OpenVPN, OpenConnect (ocserv), SOCKS5, PPTP, Telegram, GitHub, Let's Encrypt.

## Термины

| Русский | English | Примечание |
|---|---|---|
| узел, сервер (с Kaskad) | server | машина/VPS с Kaskad во всех текстах для человека; **не node** («this server», «the server’s address»). Другой сервер — «the other server» |
| шлюз | gateway | |
| переводчик протоколов | protocol translator | нейтральная формулировка шлюза |
| протокол (установленный на сервере, выдаётся устройству) | **Protocol** (мн. Protocols) | в интерфейсе для человека (мастер, панель и «Схема», страница человека, бот, SSH-экраны): «Protocol for this device» / «Протокол для этого устройства», раздел «Протоколы» — **Protocols**. **inbound** / «вход» — только в консоли (`kaskadctl gw inbound …`, справка gw_cli), журнале, полях API и для ИИ-агента: inbound — один установленный на сервере протокол, вход для клиентов; человеку — protocol |
| выход | exit | ваш VPN за рубежом |
| пул выходов | exit pool | |
| ключ выхода | exit key | |
| подписка (выходов, пользователя) | subscription | ссылка подписки — subscription link |
| автообновление подписки | subscription auto-refresh | |
| проброс (порта) | forward, port forward | раздел «Пробросы» — **Forwards** (SSH 1–5, 9, бот, панель): «Custom forward», «Delete forward»; «rule» о пробросе — нет |
| пользователи и устройства | Users & devices | раздел; коротко — **Users** (24.10: было «Люди и устройства» / People & devices) |
| пользователь | user (мн. users) | владелец 24.10: «заменим везде человек на пользователя, а у пользователя могут быть устройства» (было человек / person); «человек» / person остаётся только о том, кто выбирает или ведёт ИИ-агента (администратор), — список в tools/k3_text_exceptions.tsv, правило people |
| устройство | device | |
| маршрут | route | маршрут по умолчанию — default route |
| напрямую | direct | «Идти напрямую» — Go direct |
| блокировать (при отказе выходов) | block | |
| при отказе | on failure | |
| правила (маршрутизации) | routing rules | раздел — **Routing rules**; профиль правил — rules profile; слово «Rules» без уточнения не используем |
| российские сайты напрямую | Russian sites direct | профиль `ru_direct` |
| реклама, торренты (блоки) | ads, torrents | |
| схема | Diagram | раздел «Схема» |
| обзор | Overview | |
| настройки (раздел) | Settings | |
| «Вид» | Appearance | тема, акцент, плотность, язык |
| «Внешний вид» | Appearance | заголовок шторки и строка в «Ещё» на телефоне; кнопка в шапке — «Вид», по-английски тоже Appearance |
| тема: светлая / тёмная / как в системе | Light / Dark / System | |
| язык | Language | Русский / English — названия языков всегда на своём языке |
| быстрый старт, мастер | Quick Start, Quick Start wizard | |
| этап / шаг | stage / step | этапы рельса — stages |
| рельс (список этапов) | stage list | в тексте интерфейса — «All stages» |
| сайт-маскировка, сайт маскировки | masking site | |
| шаблон сайта | site template | |
| домен, рабочий домен | domain, working domain | |
| сертификат | certificate | |
| секретный адрес панели | secret panel address | |
| одноразовая ссылка входа | one-time sign-in link | |
| вход в панель (авторизация) | sign in | «Выйти» — Sign out |
| выдать настройки | issue settings | на устройство |
| выдать заново | reissue | |
| настройки подключения | connection settings | |
| страница пользователя | personal page | `/p/…`; в тексте — user’s page |
| QR-код | QR code | |
| рукопожатие | handshake | |
| модуль ядра | kernel module | AmneziaWG kernel module |
| программа amneziawg-go (режим без модуля) | userspace (amneziawg-go) | «Switch to userspace», «Stay on userspace»; **не program** |
| движок AmneziaWG | AmneziaWG engine | только выбор режима: kernel module / userspace (amneziawg-go) |
| ядро AmneziaWG (бинари awg, amneziawg-go) | AmneziaWG core | то, чего нет без ключа GitHub («no AmneziaWG core»); не engine |
| ядро Kaskad 3 (kaskadctl, Python) | Kaskad 3 core | «Kaskad 3 core is unavailable — the panel needs it» |
| сборщик (на узле) | **collector** | служба `kaskad-gw-collect`: раз в 10 секунд проверяет выходы и переключает пул, считает сессии, поднимает упавшие ноги AmneziaWG, пишет сводку для панели и меню. «Сторож выходов» в русских текстах — это он: в английском — collector (exit checks), **не watchdog** |
| сторож (минутный) | **watchdog** | таймер `kaskad-net-watch` (раз в минуту `kaskadctl net check --repair`): проверяет пробросы и шлюз и чинит, что может (таблица nftables, маршрутизация, перезапуск протоколов и служб). Только эта сущность — watchdog; сторож срока сертификата — **certificate watchdog**; «systemd watchdog» — термин systemd, не переводим |
| бот в Telegram | Telegram bot | |
| Прокси для API Telegram | Telegram API proxy | |
| ключ ИИ-агента | AI agent key | |
| копия настроек | **Settings backup** (раздел/карточка везде: панель, мастер, SSH, бот); файл — backup file | перенос — «move to another server» только в описании, не в названии |
| вернуть как было | undo, restore | |
| SSH-меню | SSH menu | |
| панель | panel | веб-панель — web panel; раздел SSH-меню — «Web panel» |
| доступ к панели из интернета | **Access from the internet** | переключатель панели, SSH 18, бот, ссылки в шагах; не «Internet access» |
| адрес для клиентов (IP или домен в ключах) | **Client address** | одно имя везде (не «Address for devices») |
| данные для подключения | **Connection details** | |
| сетевой интерфейс (сетевая карта) | network interface (NIC) | MTU: «the server’s network interface MTU is lower than clients need»; не «narrow card» |
| ключ GitHub | GitHub key | один раз пояснить «(personal access token)», дальше — key |
| выйти из меню / экрана | Quit (главное меню), Back (подменю) | «Exit» — только VPN-выход |
| удалить (ключ, подписку, правило) | Delete | «Remove» — только убрать из списка без удаления |
| пароль | password | |
| сеть, нет связи | network, no connection | |
| работает / не работает / не проверялся | working / down / not checked yet | статусы выходов |
| онлайн / опрос | Live / Polling | состояние связи панели |
| включён / выключен | on / off; enabled / disabled | в кнопках — Turn on / Turn off |
| применить | apply | |
| порт занят | port in use | |
| файрвол хостера | hosting provider's firewall | |
| хостер | hosting provider | |
| «Основная» (группа выходов `main`, имя по умолчанию) | Main | имя не хранится текстом, переводится при показе (SE §3.1) |
| «Уровень N» (уровень группы, имя по умолчанию) | Level N | то же |
| только уведомить (действие условия, квоты, страны) | notify only | не «push only» |
| ⚠РФ (пометка реестра) | ⚠RU | RF §7 |
| настройки выходов / настройки группы | exit settings / group settings | слово «политика» (policy) в панели не используем |
| «<Объект> изменили, пока вы правили» (полоса формы со списком правок) | <Object> changed while you were editing | SE §7 «Версия политики»; без «в другом месте»: изменение могло быть своим |
| «Применить снова» (своя правка из списка «было → стало») | Apply again | SE §7 «Версия политики» (там же — все тексты 409 и пересчёта) |
| «Данные изменились — пересчитал» | Data changed — recalculated | подтверждение и окно после 409; полоса формы после 409 на «Сохранить» |
| «Уже удалено» | Already deleted | повтор удаления — не ошибка |
| «Данные изменились — обновите и повторите» | Data changed — refresh and try again | 409 на «Всё равно <действие>» (SE §7 «Откуда `rev`») |
| «Сервер применяет предыдущее изменение — повторите через минуту» | The server is applying the previous change — try again in a minute | 409 «занято» (код 6) у записи и сухого прогона, рядом «Повторить» / Retry |
| «Правила» (<Объект> полосы формы) | The routing rules | форма «Правила» со своей `ver` (RF §5.1, SE §7.9 «Версия объекта»); в полосе — «Правила изменили, пока вы правили…» / «The routing rules changed while you were editing…» |
| «Тест скорости» (<Объект> полосы формы) | The speed test settings | общая на сервер карточка «Тест скорости» (SE §7.1); в полосе — «Тест скорости изменили, пока вы правили» / «The speed test settings changed while you were editing» |
| «выходы «Z», «W» убраны» | exits “Z”, “W” removed | <что> во множественном числе; вместе с новыми — через «; » |
| «Вернуть имя по умолчанию» | Restore default name | RN §4.1 |

## Правописание и голос

- Американская орфография (en-US): neighbor, license, gray, color, canceled.
- Голос: в вебе и SSH-меню — «we» или безлично; бот говорит от себя — «I». Не смешивать в одном экране.
- Платформы: «iPhone / iPad», «Android», «Windows», «macOS», «Linux»; «exit» = your VPN abroad.
- Внутренние коды протоколов (awg2, wg, l2tp) человеку не показываем — только названия (AmneziaWG 2.0, WireGuard,
  L2TP/IPsec).
- «Please note» вместо «Attention».

## Подписи в сторонних приложениях

Только как в интерфейсе приложения на этом языке, подтверждённом источником (исходники локализации, справка
разработчика): английский текст — по английскому интерфейсу, русский — по русской локализации (решение после повторной
критики, 2026-10-11: исключение из «русский байт в байт как в 3.3.1»). Одно действие — одно название во всех местах (каталоги `gw_issue`, веб `qr.how_*`, bash). Не
подтверждено — нейтрально, без кавычек-подписи: «add the config from the QR code». Таблица — ниже (3.3.2).


| Приложение | Действие | Подпись EN | Источник |
|---|---|---|---|
| AmneziaVPN | вставить ключ / QR / файл / подключить | «+» → “Insert” (вставляет из буфера) → “Continue” / “QR code” / “File with connection settings”; затем “Connect” | amnezia-client 5.0.3.0, `PageSetupWizardConfigSource.qml`, `PageSetupWizardViewConfig.qml` |
| DefaultVPN | вставить ключ | нейтрально: “tap “+”, insert the key from the clipboard and connect” (исходников нет, подписи в шагах не цитируем) | docs.amnezia.org (amnezia-on-ios-in-russia) |
| AmneziaWG, WireGuard Android | QR | “Scan from QR code” (“Import from file or archive”) | amneziawg-android / wireguard-android `strings.xml` |
| AmneziaWG, WireGuard iOS | QR | “Create from QR code” (“Create from file or archive”) | amneziawg-apple / wireguard-apple `Localizable` |
| WireGuard Windows | импорт / пустой туннель / включить | “Import tunnel(s) from file”; “Add Tunnel” → “Add empty tunnel…”; “Activate” | wireguard-windows `messages.gotext.json` |
| WireGuard macOS | импорт / пустой туннель | “Import Tunnel(s) from File…”; “Add Empty Tunnel…”; “Activate” | wireguard-apple |
| AmneziaWG (все) | vpn:// | не принимает — только .conf и его QR | `TunnelImporter.kt`, `QRScanViewController.swift` |
| Keenetic | WireGuard / VPN / PSK / флажок | “Import from a file”; “Create connection”; “Secret key”; “Use for accessing the Internet” | support.keenetic.com |
| Keenetic | линии AmneziaWG | 1.0 — с KeeneticOS 4.2 (4.2 Alpha 2; основной канал — 4.2.1); 1.5 и 2.0 — с 5.1.1 (параметры ASC в 5.1 Alpha 3, первый выпуск основного канала — «Release 5.1.1 (preview -> main)»); 3.1 — с 5.2 Alpha 11 (тестовый канал, NDM-4641) | forum.keenetic.com/topic/17771-changelog-42, /topic/24363-changelog-51, /topic/26598-changelog-52 |
| OpenWrt LuCI | импорт .conf | “Load configuration…” | luci-proto-wireguard `wireguard.js` |
| MikroTik | терминал | Winbox “New Terminal”, WebFig “Terminal”; “Files”; `/import file-name=` | help.mikrotik.com |
| Hiddify | добавить | «+» → “Clipboard” / “Scan QR” (в новых — “Scan QR code”) | hiddify-app `fix_btns.dart`, `en.i18n.json` |
| v2rayN | буфер / подписка | “Import Share Links from clipboard” (Ctrl+V); “Subscription group settings” | v2rayN `ResUI.resx` |
| v2RayTun, Streisand | — | не подтверждено (исходников нет) — нейтрально: “tap “+” and scan the QR code / import the link from the clipboard” | — |
| macOS | профиль .mobileconfig | System Settings → General → Device Management (macOS 13–14: Privacy and Security → Profiles) → “Downloaded” → “Install” | support.apple.com mh35561 (13/14/15/26) |
| macOS | VPN L2TP | System Settings → Network → Action pop-up menu → “Add VPN Configuration” → “L2TP over IPSec” (macOS 13: System Settings → VPN → “Add VPN Configuration”); включить — System Settings → VPN | support.apple.com mchlp2963 (13/14/15/26), mchlp1579 |
| iOS, Android (IKEv2, сертификат), Xiaomi, терминал Windows | — | не подтверждено — нейтрально, без кавычек-подписи | — |

### Русские подписи сторонних приложений (3.3.2)

| Приложение | Действие | Подпись RU | Источник | Статус |
|---|---|---|---|---|
| AmneziaVPN | вставить ключ / QR / подключить | «+» → «Вставить» (ключ из буфера) → «Продолжить»; «QR-код»; «Подключиться» | amnezia-client 5.0.3.0 `amneziavpn_ru_RU.ts` (PageSetupWizardConfigSource, PageSetupWizardViewConfig) | подтверждено |
| DefaultVPN | вставить ключ / QR | нейтрально: «нажмите «+», вставьте ключ из буфера и подключитесь», «добавьте ключ из QR-кода» | App Store: заявлен только английский | нейтрально |
| WireGuard, AmneziaWG Android | QR / файл | «Сканировать QR-код»; «Импорт из файла или архива» | wireguard-android, amneziawg-android `values-ru/strings.xml` | подтверждено |
| WireGuard, AmneziaWG iOS | QR / файл | «Создать из QR-кода»; «Создать из файла или архива» | wireguard-apple `ru.lproj/Localizable.strings` | подтверждено |
| WireGuard Windows | импорт / пустой туннель / включить | «Импорт туннелей из файла»; «Добавить туннель» → «Добавить пустой туннель…»; «Подключить» | wireguard-windows `locales/ru/messages.gotext.json` | подтверждено |
| WireGuard macOS | импорт / пустой туннель / включить | «Импорт туннелей из файла…»; «Добавить пустой туннель…»; кнопка Activate переведена как «Подключен» — в шаге нейтрально «Включите туннель» | wireguard-apple `ru.lproj` | подтверждено / нейтрально |
| AmneziaWG (общая подсказка QR) | QR или файл | нейтрально: «добавьте туннель из QR-кода или файла .conf» (подписи разные по платформам) | — | нейтрально |
| Hiddify, v2rayN, v2RayTun, Streisand | ссылка / подписка | нейтрально: «нажмите «+» и импортируйте ссылку из буфера обмена» (у Hiddify «Добавить профиль» → «Буфер обмена», у v2rayN — «Импорт ссылок общего доступа из буфера обмена»; v2RayTun и Streisand — исходников нет) | hiddify-app `ru.i18n.json`, v2rayN `ResUI.ru.resx` | нейтрально |
| OpenWrt LuCI | импорт .conf | в RU-тексте английский путь и в скобках русский: Сеть → Интерфейсы → Добавить новый интерфейс → WireGuard ВПН → «Загрузка конфигурации…» | luci `modules/luci-base/po/ru/base.po` | подтверждено |
| Keenetic | WireGuard / VPN / PSK | «Другие подключения», «Добавить подключение», «Секретный ключ»; импорт .conf и флажок выхода в интернет — нейтрально (в официальной справке «импортировать из файла», «Использовать для входа в Интернет») | support.netcraze.ru (русская справка Keenetic): 16935-l2tp-ipsec-client, 16937-wireguard-vpn, 21424 | подтверждено / нейтрально |
| macOS | профиль .mobileconfig | Системные настройки → Основные → Управление устройством (macOS 13–14: Конфиденциальность и безопасность → Профили) → «Загружено» → «Установить» | support.apple.com/ru-ru mh35561 (13/14/15/26) | подтверждено |
| macOS | VPN L2TP | Системные настройки → Сеть → меню действий → «Добавить конфигурацию VPN» → «L2TP через IPSec» (macOS 13: → VPN → «Добавить конфигурацию VPN») | support.apple.com/ru-ru mchlp2963 (13/14/15/26) | подтверждено |
| iOS | профиль / VPN | «Профиль загружен» → «Установить»; Настройки → Основные → VPN и управление устройством; добавление VPN — нейтрально | support.apple.com/ru-ru/102400 | подтверждено / нейтрально |
| Windows 11 | VPN | Параметры → Сеть и Интернет → VPN → «Подключиться» (Windows 10 — «Подключить»); терминал администратора — нейтрально | support.microsoft.com/ru-ru «Подключение к VPN в Windows» | подтверждено / нейтрально |
| Android IKEv2, Xiaomi | VPN, сертификат ЦС | нейтрально, «названия зависят от производителя/прошивки» | не найдено | нейтрально |
| MikroTik (Winbox, WebFig), OpenVPN Connect, Cisco Secure Client, OpenConnect GUI | — | английские подписи (русского интерфейса нет или не подтверждён); в RU один раз «подписи … на английском» | help.mikrotik.com, App Store | подтверждено |
