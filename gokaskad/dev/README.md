# Kaskad PRO 3

**Каскадный менеджер VPN / Proxy с Telegram-ботом, мониторингом, GeoIP и live ping.**

> Канал: [YouTube @antenkaru](https://www.youtube.com/@antenkaru)

**Не разбираетесь в серверах и терминале?** → [ИНСТРУКЦИЯ_ДЛЯ_ПОЛЬЗОВАТЕЛЯ.md](ИНСТРУКЦИЯ_ДЛЯ_ПОЛЬЗОВАТЕЛЯ.md) — пошагово простыми словами.

---

## Телефон и роутер через этот сервер (меню 17)

С версии 3.1.0 к серверу можно подключить телефон, компьютер или роутер: они выйдут в интернет через ваш
зарубежный VPN (или прямо с адреса сервера — тогда заблокированное в России не откроется).

1. После первой установки Kaskad спросит: «Хотите подключить к этому серверу телефоны и роутеры? (Y/n)» —
   нажмите Enter. Пропустили — `gokaskad` → **17**.
2. Мастер проведёт по шагам: проверка сервера → протоколы (WireGuard, AmneziaWG, L2TP/IPsec уже отмечены — Enter)
   → порты (Enter) → выход в интернет → установка → первое устройство.
3. Шаг «выход»: выберите «через зарубежный VPN» и вставьте ключ вашего VPN — `vless://…` или `vpn://…`.
   Kaskad проверит его и скажет «✓ выход работает».
4. Заведите пользователя (например, «Мама») и устройство (например, Android) — Kaskad сразу покажет настройки:
   сначала четыре шага (откуда поставить приложение, что скопировать, как отправить себе, что нажать), затем
   ссылку `vpn://`. Окно маленькое — экран идёт частями, «Enter — дальше».
5. Скопируйте ссылку, отправьте её себе — например, в «Избранное» Telegram, на телефоне в AmneziaVPN:
   «+» → «Вставить» → «Подключить».

Дальше:

- ещё одно устройство — **17 → 2 → 2**; настройки снова — **17 → 2 → 1**;
- файлы на компьютер — в выдаче «1) Сохранить в файлы»: Kaskad покажет готовую команду
  `scp -r root@<IP сервера>:/root/kaskad-issue/<устройство> .` (или WinSCP / Termius);
- всё ли работает — **17 → 5** «Состояние»: протоколы, выходы (ключи VPN) и проблемы; выход «не работает: сервер выхода
  не отвечает» — не работает ваш зарубежный VPN, добавьте другой ключ: **17 → 3** →
  «Добавить ключ выхода» (пункт 3; при маршруте «напрямую» или «блок» — 2);
- роутеры: Keenetic и OpenWrt — файл `.conf`; TP-Link, Huawei, Xiaomi, ASUS — L2TP (в меню роутера должен быть
  раздел «VPN-клиент», иначе роутер не подойдёт).

---

## MCP-сервер — управление узлом из Claude

`kaskad-mcp` даёт ИИ-агенту (Claude Code, Claude Desktop, Cursor) готовые инструменты: «добавь Маму с телефоном и
пришли QR», «добавь этот ключ выхода и проверь», «куда пойдёт трафик роутера до youtube.com». Агент делает то же,
что SSH-меню, бот и панель, теми же командами и с теми же проверками. Портов сервер не открывает: он работает через
ваш SSH, пока агент подключён. Ставится вместе с Kaskad; проверка на сервере — `kaskad-mcp --check`.

**Claude Code — одной командой** (на своём компьютере, где уже работает `ssh root@HOST`):

```bash
claude mcp add kaskad -- ssh root@HOST kaskad-mcp
```

**Claude Desktop** — в файл `claude_desktop_config.json` (macOS: `~/Library/Application Support/Claude/`,
Windows: `%APPDATA%\Claude\`), затем перезапустить Claude Desktop:

```json
{
  "mcpServers": {
    "kaskad": {"command": "ssh", "args": ["-o", "BatchMode=yes", "root@HOST", "kaskad-mcp"]}
  }
}
```

Пароль SSH здесь не подойдёт — нужен вход по ключу. **Лучше отдельный ключ для агента**: им можно запустить только
MCP-сервер — ни командной строки, ни туннелей, даже если ваш компьютер взломают.

1. На своём компьютере: `ssh-keygen -t ed25519 -f ~/.ssh/kaskad_agent -N ""`.
2. На сервере: `gokaskad` → **18** (Веб-панель) → **«Ключи ИИ-агентов (MCP)»** → **1** и вставьте строку из файла
   `~/.ssh/kaskad_agent.pub`. Kaskad проверит ключ и допишет его в `/root/.ssh/authorized_keys` с запретами; там же —
   список ключей, удаление и готовые команды подключения (пункт 3). То же командой:
   `kaskadctl mcp key add < kaskad_agent.pub`.
3. Подключить: `claude mcp add kaskad -- ssh -i ~/.ssh/kaskad_agent -o IdentitiesOnly=yes -o BatchMode=yes root@HOST kaskad-mcp`.

Что важно знать:

- всё необратимое (удалить, переустановить, сбросить ключи, сменить порт или адрес в выданных настройках,
  обновить Kaskad) агент делает только с параметром `confirm` — повтором того, что удаляет или меняет. Это защита от
  ошибки модели, а **не ваше согласие**: согласие даёте вы, когда Claude спрашивает разрешение на вызов инструмента.
  Не включайте для kaskad «разрешать всегда» на такие инструменты;
- пароли и ссылки входа в панель, ключи агентов, переезд движка и удаление Kaskad агенту недоступны — это делаете вы;
- каждый вызов записан в `/var/log/kaskad-mcp.log`, а каждое изменение — ещё и в `/var/log/kaskad.log`; если журнал
  не пишется, изменения через агента запрещены;
- ключи устройств агент получает, только когда просит настройки устройства (инструмент `device_issue`);
  QR-коды приходят картинками;
- долгие действия (установка протокола, первый ключ AmneziaWG, смена движка, сертификат, обновление) идут минутами.
  Claude Code по умолчанию ждёт ответа инструмента недолго — поднимите предел:
  `MCP_TOOL_TIMEOUT=3600000 claude` (в миллисекундах). Оборвал ожидание клиент — на сервере действие всё равно
  доделается; агент проверит результат чтением;
- сервер общается через stdin/stdout SSH: если `~/.bashrc` или `~/.profile` root печатает что-то при входе (приветствие,
  `echo`, `neofetch`), это ломает соединение — уберите вывод или оберните его в `if [ -t 1 ]; then … fi`;
- в Claude Code готовые сценарии — команды `/mcp__kaskad__onboard_person`, `/mcp__kaskad__add_exit`,
  `/mcp__kaskad__where_traffic`. Подробно — [docs/k3/SPEC-mcp.md](docs/k3/SPEC-mcp.md).

---

## Kaskad 3 (версии 3.0.1, 3.1.0, 3.3.0, 3.3.1, 3.3.2, 3.3.3, 3.3.4, 3.3.5, 3.3.5.1, 3.3.6, 3.3.7, 3.3.7.1, 3.3.7.2, 3.3.7.3, 3.3.7.4, 3.3.7.5, 3.3.7.6, 3.3.7.7, 3.3.7.8, 3.3.7.9, 3.3.7.10, 3.3.7.11, 3.3.7.12, 3.3.7.13, 3.3.7.14 и 3.3.7.15)

Kaskad 3 выходит в ветке `dev` (до 3.3.3 — `k3`, она заморожена); в `main` пока Kaskad PRO 2.13.

### Что нового

**3.3.7.15 — настройки протокола одним окном (порт, MTU, TLS), безопасное применение с откатом через 2 мин, «ждут новых настроек» у всех протоколов, сеть шлюза, «Адреса сервера» с IPv6, «Разложить заново»**

- «Протоколы» → у протокола «Настройки»: порт, «MTU клиента» (WireGuard и AmneziaWG) и «Версии TLS» (VLESS TLS, SSTP,
  OpenConnect) — в одном окне и одним применением. До «Применить» — «Что изменится»: что перезапустится, какие
  устройства не подключатся до новой выдачи настроек, кому их желательно выдать заново, предупреждения. После — «Вернуть
  прежние»: всё последнее применение одним действием; если возврат оборвёт другие подключения (перезапуск общей службы)
  или ваше собственное — сначала спросит. MTU — от 1280 до предела сервера (MTU его сетевой карты, но не
  больше 1400, минус 80 байт заголовков и S4), меняется на ходу, без перезапуска; выше предела — отказ словами. Версии
  TLS: «1.2 и 1.3», «только 1.3», «только 1.2» (у VLESS — первые две); у SSTP на «только 1.3» — предупреждение «Windows
  10 и MikroTik могут не подключиться». Ссылки и файлы устройств от версии TLS не меняются. Сменили порт, а за 10 мин
  после выдачи никто не подключился — строка протокола спросит «порт закрыт у хостера?».
- Безопасное применение. Вы открыли панель или SSH через протокол, который меняете (WireGuard, AmneziaWG, OpenVPN и
  другие, где устройство получает адрес в туннеле), — подтверждение предупредит: «Вы подключены через WireGuard: после
  применения связь может оборваться», а рядом будет «Скачать мои новые настройки». У VLESS, SOCKS5 и Hysteria2 сервер
  этого не видит, поэтому при смене порта или TLS — строка «Если вы открыли панель через VLESS, SOCKS5 или Hysteria2
  этого сервера, связь может оборваться» и кнопка «Применить с откатом через 2 мин». Пока сервер ждёт, над разделами
  панели — полоса с остатком времени, «Подтвердить» и «Вернуть прежние»; из SSH — `kaskadctl gw pending confirm`. Не
  подтвердили за 2 мин — настройки возвращаются сами, в ленте событий и в боте: «Настройки WireGuard возвращены:
  подтверждения за 2 мин не было». Перезагрузка сервера в эти 2 мин ожидание не теряет. Так же меняется и сеть шлюза.
- «Ждут новых настроек» — у всех протоколов: после смены порта, MTU, сети шлюза или S4. В «Пользователях» у устройства —
  метка «ждёт настроек»: заливкой — «не подключится — выдайте заново», пунктиром — «желательно выдать заново», с
  причиной («сменился порт», «сменился MTU», «сменилась сеть шлюза»); фильтр «Ждут настроек». На «Обзоре» — «WireGuard:
  2 устройства ждут новых настроек» с именами и ссылкой в «Пользователи», в боте — «⏳ SSTP: ждут новых настроек — …»:
  там только те, кто не подключится. Метку снимает новая выдача настроек: «Выдать настройки» в панели, «Скачать .conf»
  (или другой файл настроек) на странице пользователя или свои новые настройки, скачанные перед применением;
  «Показать настройки» на странице и обновление подписки выдачей не считаются. «Сменился порт» снимает и первое подключение устройства на новом порту; у
  WireGuard и SOCKS5 — только новая выдача (подключённое устройство WireGuard само переходит на новый порт, а в его
  сохранённых настройках остаётся прежний). Прежний порт возвращайте кнопкой «Вернуть прежние»: после нового
  применения со старым портом метка у WireGuard и SOCKS5 остаётся.
- Сеть шлюза меняется в панели: «Настройки › Шлюз» → «Сеть шлюза» → «Сменить». В окне «Сменить сеть шлюза» — любая
  10.X.0.0/16, 172.16–31.X.0.0/16 или 192.168.0.0/16 (с предупреждением: она совпадёт с домашними сетями многих
  клиентов); сети, занятые на сервере (Docker, другой VPN), отмечены и не выбираются. «Что изменится»: WireGuard и
  AmneziaWG не подключатся до новой выдачи настроек, у MikroTik на SSTP, L2TP и PPTP до неё пропадёт DNS через туннель,
  остальные устройства переподключатся сами, переадресации на устройства сдвинутся вместе с их адресами. После —
  «Вернуть прежнюю сеть». Ключ выхода WireGuard или AmneziaWG с той же сетью, что у шлюза, по-прежнему не добавляется,
  но теперь рядом кнопка «Сменить сеть шлюза на 10.78.0.0/16». Из командной строки сеть при устройствах больше не
  меняется — Kaskad отправляет в панель; сеть вне 10.0.0.0/8, 172.16.0.0/12 и 192.168.0.0/16 не принимается.
- «Настройки › Сервер › Адреса» — страница «Адреса сервера»: IPv4 и IPv6 сервера и их роли — сначала полосой, столбец на
  адрес, затем списком. У IPv6 видно «авто · может смениться», «устаревает: хостер сменил префикс», «IPv6 есть, маршрута
  наружу нет», «наружу виден как …», «в AAAA-записи»; «Проверить заново» — проверка сейчас. «Приём» — у каждого адреса:
  у IPv4 «Принимать» или «Не принимать» (это прежний «Приём подключений»), у IPv6 — «Принимать и выдавать», «Только
  принимать» (по умолчанию, как раньше) или «Не принимать»: раньше IPv6 не ограничивался. SSH, порт 80 и пробросы
  открыты на всех адресах. Адреса из записей домена панели и своего сайта и из AAAA-записи домена, который получают
  устройства, закрываются только после подтверждения со списком «Что изменится»; адрес для клиентов — только после
  выбора другого. Хостер сменил префикс IPv6 — в ленте событий и в боте «Префикс IPv6 сменился: … → …», на странице —
  «Перенести роль на …».
- Там же блок «Выход» (только IPv4). «Сайты видят:» — с какого адреса уходит трафик устройств «напрямую», мимо выходов;
  «Напрямую — с адреса» — выбрать его, один на все протоколы. У каждого IPv4 — «Для выхода»: да или нет, можно ли с него
  ходить «напрямую» и к выходам. Снять «для выхода» с адреса маршрута можно с подтверждением — «напрямую» и выходы
  переедут на другой адрес; другого нет — отказ словами. «Разнести выходы по адресам»: выходам без своего адреса —
  адреса «для выхода» поровну; выходы со своим адресом не двигаются, нужно хотя бы два таких адреса. Вернуть выход на
  прежний путь — «по маршруту системы». «Сбросить выбор» снимает «для выхода», «напрямую» и строку IPv6, приём не
  меняет. Собственный трафик сервера (обновления, бот) идёт с адреса маршрута.
- IPv6 строкой в подписке: у IPv6 «Принимать и выдавать» — после всех строк IPv4 подписка получает такие же строки на
  этот IPv6, с « · IPv6» в конце имени: у VLESS Reality, Hysteria2 и `amneziawg://` для WireGuard и AmneziaWG. Файлы и
  QR — по IPv4, строки IPv4 — первыми, как раньше. Выдаётся только IPv6, у которого есть путь наружу и который снаружи
  виден самим собой. Включение и выключение перезапускают xray: VLESS Reality начинает или перестаёт слушать IPv6.
  Hysteria2 с маскировкой и AmneziaWG на движке «программа» получают строку, только если этот IPv6 — адрес маршрута
  IPv6.
- «Разложить заново» — в строке «Уровни» группы («Выходы › Группы» и «Выходы › Внутренние группы»): устройства группы
  раскладываются по выходам сейчас, как при «Ночной перебалансировке», не дожидаясь ночи. До нажатия — «сейчас» и
  «после» по выходам и кто переедет; «Открытые соединения доживут; у переехавших сайты увидят другой адрес — банки и
  почта могут попросить войти заново». Итог — в ленте событий: «устройства разложены заново — …». Нет связи у сервера —
  «Раскладка заморожена…»; в группе «по соединениям» раскладывать нечего.
- SSH-меню 17 → 1 → протокол → «Сменить порт»: сначала «Что изменится», затем «1) Применить 2) Отмена», у VLESS и SOCKS5
  — ещё «Применить с откатом через 2 мин». После смены в карточке протокола — «Вернуть прежние настройки». Если сервер
  ждёт подтверждения — «1) Оставить новые настройки 2) Вернуть прежние сейчас» без выбора по умолчанию, с отсчётом
  («Выбор (осталось 1:59):»); не ответили — настройки вернутся сами. MTU и TLS — в панели. Бот пишет «ждут новых
  настроек» по всем протоколам и сам присылает, что настройки вернулись или не вернулись и что сменился префикс IPv6.
  Новое в этом выпуске настраивается в панели: новых инструментов MCP нет, прежние `inbound_port`, `gateway_addresses` и
  `gateway_addresses_in` работают, как раньше; адрес из A-записи домена `gateway_addresses_in` не закрывает — это
  подтверждают в панели или `kaskadctl gw addresses in … --yes`.
- Защита IPv6: пока включён IKEv2, сервер не пересылает IPv6, пришедший из IPsec, — закрыта возможная утечка IPv6
  клиентов IKEv2 мимо выхода. `kaskadctl gw check` следит и за этим правилом.

*In English.* **3.3.7.15 — protocol settings in one window (port, MTU, TLS), safe apply with a rollback in 2 min, "waiting for new settings" for every protocol, the gateway network, "Server addresses" with IPv6, "Regroup"**

- "Protocols" → "Settings" of a protocol: the port, the "Client MTU" (WireGuard and AmneziaWG) and the "TLS versions"
  (VLESS TLS, SSTP, OpenConnect) — in one window and one apply. Before "Apply" — "What will change": what restarts,
  which devices won’t connect until their settings are issued again, which had better get them again, warnings. After it
  — "Restore previous": the whole last apply in one action; if the restore cuts other connections (a restart of a
  shared service) or your own, it asks first. MTU is from 1280 up to the server’s limit (the MTU of its
  network card, at most 1400, minus 80 bytes of headers and S4) and changes on the fly, without a restart; above the
  limit — a refusal in words. TLS versions: "1.2 and 1.3", "1.3 only", "1.2 only" (VLESS has the first two); SSTP on
  "1.3 only" warns "Windows 10 and MikroTik may fail to connect". Device links and files don’t depend on the TLS
  version. The port changed and nothing connected within 10 min of the issue — the protocol row asks "is the port closed
  at the hoster?".
- Safe apply. When you opened the panel or SSH through the protocol you change (WireGuard, AmneziaWG, OpenVPN and the
  others where a device gets an address in the tunnel), the confirmation warns: "You are connected through WireGuard:
  the link may drop after the change", with "Download my new settings" next to it. With VLESS, SOCKS5 and Hysteria2 the
  server can’t see it, so a change of the port or TLS gets the line "If you opened the panel through VLESS, SOCKS5 or
  Hysteria2 of this server, the connection may drop" and the button "Apply with a rollback in 2 min". While the server
  waits, a bar over the panel sections shows the time left, "Confirm" and "Restore previous"; from SSH —
  `kaskadctl gw pending confirm`. No confirmation within 2 min — the settings go back by themselves, in the event feed
  and the bot: "The WireGuard settings were reverted: no confirmation within 2 min". A reboot of the server within those
  2 min does not lose the wait. The gateway network is changed the same way.
- "Waiting for new settings" for every protocol: after a change of the port, the MTU, the gateway network or S4. In
  "Users" a device gets the mark "waits for settings": filled — "won’t connect — issue again", dashed — "better to issue
  again", with the reason ("the port changed", "the MTU changed", "the gateway network changed"); the filter "Waiting
  for settings". The "Overview" says "WireGuard: 2 devices wait for new settings" with the names and a link to "Users",
  the bot — "⏳ SSTP: waiting for new settings — …": only the ones that won’t connect. A new issue of the settings takes
  the mark off: "Issue settings" in the panel, "Download .conf" (or another settings file) on the user’s page or your
  new settings downloaded before applying; "Show settings" on the page and a refresh of the subscription are not an issue. "The port changed"
  also goes with the first connection of the device on the new port; for WireGuard and SOCKS5 — only a new issue (a
  connected WireGuard device moves to the new port by itself, while its saved settings keep the old one). Bring the
  old port back with "Restore previous": after a new application with it the mark stays on WireGuard and SOCKS5.
- The gateway network is changed in the panel: "Settings › Gateway" → "Gateway network" → "Change". The window "Change
  the gateway network" takes any 10.X.0.0/16, 172.16–31.X.0.0/16 or 192.168.0.0/16 (with a warning: it matches many
  clients’ home networks); networks taken on the server (Docker, another VPN) are marked and can’t be chosen. "What will
  change": WireGuard and AmneziaWG won’t connect until their settings are given again, MikroTik on SSTP, L2TP and PPTP
  loses DNS through the tunnel until then, the other devices reconnect by themselves, port forwards to devices move with
  their addresses. After it — "Restore the previous network". A WireGuard or AmneziaWG exit key with the same network as
  the gateway is still not added, but now there is the button "Change the gateway network to 10.78.0.0/16" next to it.
  From the command line the network no longer changes while there are devices — Kaskad sends you to the panel; a network
  outside 10.0.0.0/8, 172.16.0.0/12 and 192.168.0.0/16 is not taken.
- "Settings › Server › Addresses" — the page "Server addresses": the IPv4 and IPv6 addresses of the server and their
  roles — first as a bar, a column per address, then as a list. An IPv6 shows "auto · may change", "deprecated: the
  hoster changed the prefix", "IPv6 is here, no route out", "seen outside as …", "in the AAAA record"; "Check again"
  checks now. "Accepting" for each address: an IPv4 — "Accept" or "Don’t accept" (the former "Taking connections"), an
  IPv6 — "Accept and hand out", "Accept only" (the default, as before) or "Don’t accept": before, IPv6 was not limited.
  SSH, port 80 and port forwards stay open on every address. The addresses in the records of the domain of the panel and
  of the own site and in the AAAA record of the domain the devices get are closed only after a confirmation with the
  list "What changes"; the client address — only after another one is chosen. The hoster changed the IPv6 prefix — the
  event feed and the bot say "The IPv6 prefix changed: … → …", the page offers "Move the role to …".
- There, too, the block "Exit" (IPv4 only). "Sites see:" — the address the "direct" traffic of devices leaves from, past
  the exits; "Direct — from" chooses it, one for every protocol. Each IPv4 has "For exits": yes or no, whether "direct"
  traffic and the exits may go from it. "For exits" comes off the route address with a confirmation — "direct" and the
  exits move to another address; with no other one — a refusal in words. "Spread exits over addresses": the exits
  without their own address get the addresses "for exits" evenly; the exits with their own address never move, at least
  two such addresses are needed. An exit goes back to the old way with "by the system’s route". "Reset the choice" takes
  off "for exits", "direct" and the IPv6 line and leaves accepting as it is. The server’s own traffic (updates, the bot)
  goes from the route address.
- An IPv6 line in the subscription: for an IPv6 on "Accept and hand out", after all the IPv4 lines the subscription gets
  the same lines on that IPv6 with " · IPv6" at the end of the name: VLESS Reality, Hysteria2 and `amneziawg://` for
  WireGuard and AmneziaWG. Files and QR stay on IPv4, the IPv4 lines come first, as before. Only an IPv6 with a way out
  that is seen outside as itself is handed out. Turning it on or off restarts xray: VLESS Reality starts or stops
  listening on IPv6. Hysteria2 with the mask and AmneziaWG on the userspace engine get the line only when this IPv6 is
  the source of the IPv6 route.
- "Regroup" in the "Levels" row of a group ("Exits › Groups" and "Exits › Domestic groups"): the devices of the group
  are laid out over its exits now, as by "Night rebalancing", without waiting for the night. Before it — "now" and
  "after" by exits and who moves; "Open connections stay up; for the devices that move, sites will see another address —
  banks and mail may ask to sign in again". The result is in the event feed: "devices regrouped — …". The server is
  offline — "The layout is frozen…"; a "per connection" group has nothing to regroup.
- SSH menu 17 → 1 → a protocol → "Change port": first "What will change", then "1) Apply 2) Cancel", VLESS and SOCKS5
  also have "Apply with a rollback in 2 min". After the change the protocol card has "Return the previous settings".
  When the server waits for a confirmation — "1) Keep the new settings 2) Return the previous ones now" with no default
  choice and a countdown ("Choice (1:59 left):"); no answer — the settings return by themselves. MTU and TLS are in the
  panel. The bot says "waiting for new settings" for every protocol and tells by itself that settings went back or
  failed to, and that the IPv6 prefix changed. The new things of this release are set in the panel: there are no new MCP
  tools, the former `inbound_port`, `gateway_addresses` and `gateway_addresses_in` work as before;
  `gateway_addresses_in` does not close an address of a domain’s A record — that is confirmed in the panel or with
  `kaskadctl gw addresses in … --yes`.
- IPv6 protection: while IKEv2 is on, the server does not forward IPv6 that came out of IPsec — a possible leak of the
  IPv6 of IKEv2 clients past the exit is closed. `kaskadctl gw check` watches this rule too.

**3.3.7.14 — сетка на графиках и исправления после 3.3.7.13: чужие маршруты при удалении ключа AmneziaWG, ключ с доменом этого сервера, порядок в `gw groups`, понятные отказы**

- Графики — с сеткой: линии делений с числами слева, отметки времени — на круглых часах, в полночь или по датам, над
  колонкой чисел — единица («МБ за час», «мс»), у правого края — «сейчас». Шкала одна во всех окнах: «График»
  пользователя и устройства, «Графики» выхода, «Трафик сервера», замер скорости. «Графики» выхода за «90 сут»
  размечены 1-ми числами месяцев («1 окт.»); у молодого выхода, где таких чисел на оси меньше двух, — понедельниками
  или днями.
  Вид панели: «Знакомство с панелью» на телефонах ниже 680 px (360×640, 375×667) — карточка плотнее, над ней видно
  раздел; «Все выходы» на «Крупнее» — «Добавить ключ», которому не хватило места, стоит справа под переключателем вида.
- Удаление ключа AmneziaWG и удаление Kaskad больше не стирают чужие маршруты. Раньше таблица ноги (256 + номер)
  очищалась целиком (`ip route flush`) — вместе с маршрутами администратора, если он настроил в ней маршрутизацию по
  источнику для второго адреса сервера; у второго адреса пропадала исходящая связь. Теперь убираются только маршруты
  Kaskad.
- Ключ VLESS, Hysteria2, Shadowsocks, Trojan или VMess, у которого адрес сервера — домен этого же сервера, не
  добавляется: «адрес сервера в ключе указывает на этот сервер», как у AmneziaWG. Раньше так проверялся только
  IP-адрес. Ключ из подписки, который уже есть в выходах, остаётся.
- `kaskadctl gw groups` печатает выходы в порядке очереди, как «Уровни» в панели: в ручном уровне — по списку
  и с местом («место 1»), в автоматическом — по рядам («ряд 1», «ряд 2») и с откликом, по которому выбирает сервер.
  Раньше при ручном порядке сверху мог стоять не первый выход очереди.
- SSH-меню 17 → 4 → «Приём подключений» → «Несколько адресов»: неверный список (`abc`, `1,2`) получает понятный отказ
  — «abc — не внешний IPv4-адрес…» или «Адреса … на сервере сейчас нет…», как в `kaskadctl gw addresses in`. Раньше
  отвечало «Нет такого пункта».
- Шапка панели на ширине 1100–1199: пока полю поиска не хватает места, оно значком, как на узком экране. Раньше
  подсказка «⌘K» вылезала из поля на «Онлайн» в «Пользователях и устройствах».
- Копия настроек (`.kbk`): адрес для клиентов из копии проверяется при импорте так же, как `kaskadctl gw host`;
  негодный не переносится — сервер определит адрес сам. API панели отвечает 400 на NaN, Infinity и числа за
  пределами ±10^18 вместо внутренней ошибки; лимит квоты выхода — от 0,1 до 1 000 000 ГБ.

*In English.* **3.3.7.14 — a grid on the charts and fixes after 3.3.7.13: foreign routes on removing an AmneziaWG key, a key with the domain of this server, the order of `gw groups`, clear refusals**

- Charts have a grid: tick lines with numbers on the left, time marks on round hours, midnights or dates, the unit over
  the column of numbers ("MB per hour", "ms"), "now" at the right edge. One scale in every window: the "Chart" of a
  user and of a device, the "Charts" of an exit, "Server traffic", the speed test. The "Charts" of an exit over
  "90 days" are marked on the 1st of each month ("Oct 1"); for a young exit with fewer than two of them on the axis —
  on Mondays or days. The panel view: the "Panel tour" on phones lower than 680 px (360×640, 375×667) has a denser card,
  the section shows above it; "All exits" with "Larger" text: "Add key" that does not fit stands on the right under the
  view switch.
- Removing an AmneziaWG key and removing Kaskad no longer wipe foreign routes. The table of a leg (256 + its number)
  was flushed whole (`ip route flush`) — together with the admin's routes when they had set up source routing for a
  second address of the server in it; the second address lost its outgoing traffic. Now only Kaskad's routes go.
- A VLESS, Hysteria2, Shadowsocks, Trojan or VMess key whose server address is a domain of this very server is not
  added: "the server address in the key points to this server", as with AmneziaWG. Before, only an IP address was
  checked. A key of a subscription that is already among the exits stays.
- `kaskadctl gw groups` lists the exits in the order of the queue, as "Levels" in the panel: in a manual level — by its
  list and with the place ("place 1"), in an automatic one — by rows ("row 1", "row 2") and with the response the
  server chooses by. Before, with the manual order the top line could be not the first exit of the queue.
- SSH menu 17 → 4 → "Taking connections" → "Several addresses": a wrong list (`abc`, `1,2`) gets a clear refusal —
  "abc isn’t an external IPv4 address…" or "The address … is not on the server now…", as in
  `kaskadctl gw addresses in`. Before, it said "No such item".
- The panel header at 1100–1199 wide: while the search field has no room, it is an icon, as on a narrow screen.
  Before, the "⌘K" hint stuck out of the field onto "Live" in "Users & devices".
- A copy of the settings (`.kbk`): the client address of the copy is checked on import the way `kaskadctl gw host`
  checks it; a bad one does not come — the server finds its address itself. The panel API answers 400 to NaN, Infinity
  and numbers beyond ±10^18 instead of an internal error; the quota limit of an exit is 0.1 to 1,000,000 GB.

**3.3.7.13 — несколько IP на одном сервере, слабое звено балла и отдача из России, «Внутренние группы», порядок выходов в группах, UDP через SOCKS5, режим стрима в панели, поля чисел в условиях групп и в квоте выхода снова видны**

- В «Выходы › Группы» форма условия снова показывает поля чисел: порог проверки и «держится» (сколько замеров подряд
  или минут). В окне выхода («Выходы › Все выходы») в «Квота и проверки выхода» снова есть «Лимит, ГБ», «День сброса»
  и «Предупредить при, %». В 3.3.7.12 этих полей на экране не было: порог условия и квоту выхода в панели было не
  задать. Условия и квоты, сохранённые до 3.3.7.12, всё это время работали. Если же в 3.3.7.12 вы добавляли проверку
  («+ условие») или меняли в ней «Что проверять», её порог мог молча сохраниться нулём, а квота, включённая
  в 3.3.7.12, — с невидимыми 100 ГБ и предупреждением при 80%. Проверьте условия и квоты, заданные в 3.3.7.12.
- Форма условия — ровными колонками: подписи «Если», «держится», «то», «Вернуть, когда», «Для выходов» — слева одна
  под другой, поля и проверки начинаются с одной линии. В узком окне строки идут фразой, проверка — в две строки.
  «Только вручную» — в строке «Вернуть, когда», «Сообщить в бот» — в строке «Для выходов».
- Условие пересказано один раз: фраза — в строке над формой, она меняется сразу при правке; под формой — только сроки
  («… до ухода», «… до возврата»). У каждой проверки есть «×» («Убрать проверку»); у единственной он неактивен — нужно
  от 1 до 5 проверок.
- «Обзор › Сводка» → «Балл сервера»: слабое звено — только то, чей балл ниже среднего по звеньям этой проверки больше
  чем на 30%; такого звена нет — нет и предела «слабое + 350». Слабое звено видно на радаре: его вершина и подпись
  выделены, под значением — «слабое звено». «Почему итог не выше» — одна строка и только когда предел держит итог:
  «Слабое звено — отдача 77: итог не выше 346, без предела было бы 790». Итог прошлых проверок пересчитан по новому
  правилу: он меняется (растёт) только там, где предел держал его без такого провала звена.
- Сервер в России: полная проверка теперь сама замеряет отдачу — до серверов замера скорости российских провайдеров
  (iperf3; выбранная точка, а если она недоступна — следующая по списку, видит адрес сервера). Из тех же потоков —
  «Повторная отправка под нагрузкой» и «Рост отклика под нагрузкой». Выключить можно перед запуском: галочка в окне
  «Проверить сервер», вопрос в SSH-меню, `kaskadctl host bench --full --no-upload-ru` (в MCP — `no_upload_ru`); тогда в
  «Истории» у отдачи — «выключено при запуске», а не «не измерено». Быстрая проверка и проверки по расписанию отдачу
  из России по-прежнему не замеряют. Для замера нужна программа iperf3 — её предложат только для полной проверки
  с замером отдачи.
- «Под нагрузкой» на быстром сервере: раньше, если отдача доходила до предела трафика проверки быстрее 5 с (полная —
  при отдаче выше ≈ 3,2 Гбит/с), обе строки были «не измерено». Теперь они считаются по тому отрезку, который был:
  от первой четверти секунды до упора в предел, если он не короче 1 с; в «Истории» подсказка у значения говорит, что
  отрезок короткий и число менее надёжно. Короче 1 с — по-прежнему «не измерено». Скорость отдачи не меняется.
- В том же окне в «Как считается балл» больше нет списка эталонных серверов (хостер, тариф, процессор, город):
  остаётся, какие звенья выверены по эталонам, а какие пока предварительные. Сервер этот список панели больше не
  отдаёт.
- `kaskadctl gw policy` показывает у каждой группы ещё две строки — теми же словами, что панель: «Новые и
  перенесённые выходы» (у внутренней группы — «ключи»: куда они встают) и «Ночная перебалансировка» (нет или да и во
  сколько).
- «Выходы › Маршрут по умолчанию» и квота выхода: если их успели изменить в другом окне, пока вы правили, после
  «Сохранить» сразу появляется «… изменили, пока вы правили» со списком ваших правок и «Применить снова» — без
  лишнего пересчёта и без ожидания следующего обновления.
- Страница пользователя (`/p/…`) больше не просит у сервера значок сайта: в консоли браузера нет ошибки 404.
- На только что установленном сервере проверка сервера больше не обрывается ошибкой, когда несколько проверок
  начинаются одновременно.
- «Российские группы» теперь называются «Внутренние группы», а российские выходы и ключи — внутренними: в панели
  («Выходы › Внутренние группы»), в SSH-меню, в боте, в `kaskadctl` и в описаниях MCP. «Российские сайты»,
  «Россия напрямую» и пометка ⚠РФ называются как раньше; команды и настройки не изменились.
- Порядок выходов в группе («Выходы › Группы»). Строки уровня стоят так, как сервер раздаёт новые устройства:
  по силе, весу или скорости — по способу уровня; у строки — полоска «Сила» и число. Выходы, которые сейчас новых не
  получают, — внизу, в «Не участвуют», с причиной. Выход можно переставить: за ручку мышью или пальцем, стрелками ↑ ↓
  в строке или с клавиатуры (пробел, стрелки, пробел; Esc — отменить). Так же выход переносится в другой уровень, а
  уровни меняются местами. Переставили выходы внутри уровня — его способ становится «По порядку (вручную)»: автовыбор
  выключен, новые устройства идут на первый рабочий выход сверху, подключённые остаются на своих. Прежний способ
  сохранён, «Вернуть автовыбор» включает его снова. Мимо уровней выход не бросить — строка «Сюда нельзя». Правки
  копятся до «Сохранить», у переставленных видно «было: …», итог — в «Что изменится». `kaskadctl gw policy` пишет
  способ каждого уровня и строку про автовыбор, в MCP `exit_groups_list` — `method` и `order`.
- «Выходы › Внутренние группы»: ключи стоят по шагам, стрелками ↑ ↓ или перетаскиванием шаги меняются местами. Два
  ключа в одном шаге — только явно: ключ бросают на ключ другого шага, подпись «в один шаг с «…»» и строка в «Что
  изменится». У внутренней группы, её выходов и ключей вместо метки «РФ» теперь значок роутера — в карточке группы, в
  «Выходы · час» на «Обзоре», в окнах «Новая группа» и «Перенести … в другую группу», в окне выхода, на «Схеме».
  Экранный диктор читает его «Внутренняя группа».
  Пометка ⚠РФ у ресурсов, запрещённых в России, — как раньше.
- Несколько IP на одном сервере. `kaskadctl gw addresses` и SSH-меню («Шлюз › Адрес для клиентов») показывают адреса
  сервера: найденные на интерфейсах, внешние адреса облака с NAT и добавленные вручную (`kaskadctl gw addresses add`);
  у каждого — основной ли он, адрес ли для клиентов, A-запись домена. Адрес для клиентов в меню выбирается цифрой.
  IPv4 для клиентов должен быть адресом сервера, иначе — отказ с подсказкой. Если адрес пропал или сервер выходит в
  интернет с другого, видна жёлтая строка; сам Kaskad ничего не переключает. В MCP — `gateway_addresses`.
- «Приём подключений» (`kaskadctl gw addresses in A,B`, в MCP — `gateway_addresses_in`): протоколы и свой сайт
  принимают подключения только на выбранных адресах, на остальных их порты закрыты. SSH, пробросы и порт 80 открыты
  везде, IPv6 не ограничивается, панель в открытом режиме слушает только выбранные адреса. `--all` — снова на всех.
  Kaskad откажет, если без выбранных адресов останутся устройства, панель и подписки или вы сами, открывшие панель
  через другой адрес; адрес для клиентов вне выбранных он тоже не задаст. Пропали все выбранные адреса — подключения
  принимаются на всех. Hysteria2 с маскировкой и AmneziaWG на движке «программа» пока отвечают только с основного
  адреса — это сказано строкой.
- «IP выхода» (`kaskadctl gw addresses exit <выход> <адрес>`, `--group` — всем выходам группы, `main` — по маршруту
  системы; в MCP — `gateway_exit_src`): выход подключается к своему серверу с выбранного адреса. Пропал адрес —
  выход временно подключается с основного и возвращается сам. «Прокси для API Telegram» подключается к выходам
  VLESS, Hysteria2, Shadowsocks, Trojan и VMess всегда с основного адреса. Проверка сервера меряет канал основного
  адреса. Копия, восстановленная на другом сервере, выбор адресов не переносит.
- Нога выхода AmneziaWG не поднимается, если её таблицей маршрутизации уже пользуется не Kaskad: чужие маршруты
  Kaskad не трогает, трафик этой ноги закрыт и мимо выхода не уходит, в журнале службы — просьба сменить номер своей
  таблицы.
- SOCKS5: UDP пользователей теперь идёт по их маршруту, а не отбрасывается. Если у пользователей SOCKS5 разные
  маршруты, UDP закрыт, и выдача настроек говорит об этом. Открытый UDP идёт без правил «Россия напрямую» и списков
  пользователя — об этом тоже строка в выдаче.
- «Режим стрима» — для записи экрана и трансляций: «Настройки › Вид» или кнопка-глаз в шапке панели. Панель прячет
  адреса серверов, ключи, QR-коды, токены, логины, адрес панели и новый пароль панели: вместо значения — точки.
  Показать — щелчком на 10 секунд, скопировать — не показывая; QR рисуется только по «Показать QR». Сверху — полоса
  «Режим стрима включён» со «Скрыть показанное» и «Выключить»; выключение — с подтверждением. Галочка «И имена
  пользователей и устройств» заменяет их на «Пользователь 1», «Устройство 3». Режим действует только в этом браузере:
  телефон и другие компьютеры остаются обычными. Адресную строку браузера он не закрывает — «Скопировать команду» даёт
  команду окна без неё; что ещё остаётся видно, сказано в «Чего режим не закрывает».

*In English.* **3.3.7.13 — several IPs on one server, the weak link of the score and the upload from Russia, "Domestic groups", the order of exits in groups, UDP through SOCKS5, stream mode in the panel, the number fields in group conditions and in an exit's quota are back**

- In "Exits › Groups" the condition form shows its number fields again: the threshold of a check and "holds" (how
  many checks in a row or minutes). In an exit's window ("Exits › All exits") "The exit’s quota and checks" has
  "Limit, GB", "Reset day" and "Warn at, %" again. In 3.3.7.12 these fields were not on the screen: a condition's
  threshold and an exit's quota could not be set in the panel. The conditions and quotas saved before 3.3.7.12 kept
  working. But if in 3.3.7.12 you added a check ("+ condition") or changed "What to check" in it, its threshold could
  be saved as 0 without a word, and a quota turned on in 3.3.7.12 — with an unseen 100 GB and a warning at 80%. Check
  the conditions and quotas set in 3.3.7.12.
- The condition form is in even columns: the labels "If", "holds", "then", "Return when", "For exits" are on the left
  one under another, the fields and the checks start at one line. In a narrow window the rows read as a phrase, a
  check takes two lines. "Only by hand" is in the "Return when" row, "Tell the bot" is in the "For exits" row.
- A condition is told once: the phrase is in the row above the form and changes at once while you edit; under the
  form there are only the times ("… to leave", "… to return"). Every check has "×" ("Remove the check"); with a
  single check it is inactive — from 1 to 5 checks are needed.
- "Overview › Summary" → "Server score": the weak link is only the one whose score is more than 30% below the mean of
  the links of this check; with no such link there is no "weak + 350" limit. The weak link is seen on the radar: its
  vertex and label stand out, with "weak link" under the value. "Why the total is not higher" is one line and only when
  the limit holds the total: "Weak link — upload 77: the total is at most 346, without the limit 790". The total of past
  checks is recalculated by the new rule: it changes (grows) only where the limit held it without such a drop of a
  link.
- A server in Russia: a full check now measures the upload by itself — to the speed test servers of Russian providers
  (iperf3; the chosen point, and when it does not answer the next one of the list, sees the address of the server).
  The same streams give "resent under load" and "delay rise under load". It can be turned off before the start: the
  tick in the "Check the server" window, the question of the SSH menu, `kaskadctl host bench --full --no-upload-ru`
  (`no_upload_ru` in MCP); then "History" says "turned off at the start" for the upload, not "not measured". A quick
  check and the scheduled checks still do not measure the upload from Russia. The measurement needs iperf3 — it is offered
  only for a full check that measures the upload.
- "Under load" on a fast server: before, when the upload reached the traffic limit of the check in under 5 s (a full
  check — above ≈ 3.2 Gbit/s), both rows were "not measured". Now they are counted over the stretch there was: from the
  first quarter of a second to the limit, if it is 1 s or longer; in "History" the hint of the value says the stretch
  was short and the number is less reliable. Shorter than 1 s — still "not measured". The upload speed does not change.
- In the same window "How the score is counted" no longer lists the reference servers (hoster, plan, processor,
  city): it still says which links are checked against them and which are provisional for now. The server no longer
  sends this list to the panel.
- `kaskadctl gw policy` shows two more lines for each group, in the words of the panel: "New and moved exits" ("keys"
  for a domestic group: where they go) and "Night rebalancing" (no, or yes and at what time).
- "Exits › Default route" and an exit's quota: if they were changed in another window while you were editing, "Save"
  shows at once "… changed while you were editing" with the list of your edits and "Apply again" — with no extra
  recount and no wait for the next refresh.
- The user page (`/p/…`) no longer asks the server for a site icon: no 404 error in the browser console.
- On a freshly installed server a server check no longer stops with an error when several checks start at the same
  moment.
- "Russian groups" are now called "Domestic groups", and Russian exits and keys are now domestic: in the panel
  ("Exits › Domestic groups"), the SSH menu, the bot, `kaskadctl` and the MCP descriptions. "Russian sites",
  "Russian sites direct" and the ⚠RU mark keep their names; commands and settings did not change.
- The order of exits in a group ("Exits › Groups"). The rows of a tier stand the way the server hands out new
  devices: by strength, weight or speed — by the tier's way; each row has a "Strength" bar and a number. Exits that
  get no new devices now are at the bottom, in "Not taking part", with the reason. An exit can be moved: by its handle
  with the mouse or a finger, with the ↑ ↓ arrows in the row, or from the keyboard (Space, arrows, Space; Esc
  cancels). The same way an exit goes to another tier, and tiers swap places. Exits moved within a tier turn its way
  into "In order (manual)": automatic choice is off, new devices go to the first working exit from the top, connected
  ones stay where they are. The old way is kept, and "Turn automatic choice back on" brings it back. An exit cannot be
  dropped outside the tiers — the line says "Can’t drop here". Changes wait for "Save", moved rows show "was: …", the
  sum is in "What will change". `kaskadctl gw policy` shows the way of each tier and a line about automatic choice;
  in MCP `exit_groups_list` has `method` and `order`.
- "Exits › Domestic groups": keys stand by steps; the ↑ ↓ arrows or dragging swap the steps. Two keys in one step only
  on purpose: a key is dropped on a key of another step, with the label "one step with “…”" and a line in "What will
  change". A domestic group, its exits and keys now carry a router icon instead of the "RU" mark — on the group card,
  in "Exits · hour" on "Overview", in the "New group" and "Move … to another group" windows, in the exit window, on
  "Diagram". A screen reader reads it as "Domestic group". The ⚠RU mark of resources banned in Russia stays as before.
- Several IPs on one server. `kaskadctl gw addresses` and the SSH menu ("Gateway › Client address") show the server
  addresses: found on the interfaces, the external addresses of a NAT cloud and the ones added by hand
  (`kaskadctl gw addresses add`); for each — whether it is the primary one, the client address, the domain's A record.
  The client address is chosen in the menu by its number. An IPv4 client address must be an address of the server,
  else it is refused with a hint. If the address is gone or the server goes online from another one, a yellow line
  says so; Kaskad switches nothing by itself. In MCP — `gateway_addresses`.
- "Taking connections" (`kaskadctl gw addresses in A,B`, in MCP — `gateway_addresses_in`): the protocols and the own
  site take connections only on the chosen addresses, their ports are closed on the others. SSH, port forwards and
  port 80 stay open everywhere, IPv6 is not limited, the panel in the open mode listens only on the chosen addresses.
  `--all` — on every address again. Kaskad refuses when devices, the panel and the subscriptions, or you yourself
  with the panel opened through another address would be left without the chosen addresses; nor does it set a client
  address outside the chosen ones. When all the chosen addresses are gone, connections are taken on every one.
  Hysteria2 with the mask and AmneziaWG on the userspace engine answer only from the main address for now — a line
  says so.
- "Exit IP" (`kaskadctl gw addresses exit <exit> <address>`, `--group` — every exit of a group, `main` — by the
  system route; in MCP — `gateway_exit_src`): an exit connects to its server from the chosen address. The address is
  gone — the exit connects from the main one meanwhile and comes back by itself. The "Telegram API proxy" always
  connects to the VLESS, Hysteria2, Shadowsocks, Trojan and VMess exits from the main address. The server check
  measures the channel of the main address. A backup restored on another server does not carry the choice of
  addresses.
- An AmneziaWG exit leg does not come up when its routing table is already used by something other than Kaskad:
  Kaskad leaves foreign routes alone, the traffic of the leg is closed and does not go past the exit, and the service
  log asks to renumber your own table.
- SOCKS5: the UDP of the users now follows their route instead of being dropped. If the SOCKS5 users have different
  routes, UDP is closed and the issued settings say so. Open UDP skips the "Russian sites direct" rules and the user's
  lists — a line of the issued settings says that too.
- "Stream mode" is for screen recording and live streams: "Settings › Appearance" or the eye button in the panel
  header. The panel hides server addresses, keys, QR codes, tokens, logins, the panel address and a new panel
  password: dots stand instead of the value. A click reveals a value for 10 seconds, copying works without revealing;
  a QR code is drawn only after "Show QR". A bar on top says "Stream mode is on" with "Hide revealed" and "Turn off";
  turning it off asks first. The tick "Also user and device names" turns them into "User 1", "Device 3". The mode
  applies to this browser only: your phone and other computers stay as they are. It does not cover the browser address
  bar — "Copy command" gives the command of a window without it; what else stays visible is listed in "What it doesn’t
  cover".

**3.3.7.12 — новый «Обзор» с баллом сервера, «Пользователи» таблицей, «Выдать настройки» одним окном, порт при установке протокола, российские группы в общей карточке**

*Пользователи*

- Список «Пользователи» — таблицей со столбцами «Активность · сутки», «В сети», «Протоколы» и «Маршрут». Состояние
  пользователя — меткой у имени, кто в сети — точками. Линия активности длиннее, подписи объёма у неё больше нет.
- Столбцы «Активность · сутки», «Протоколы» и «Маршрут» сворачиваются нажатием на заголовок, а на узком окне — сами
  (сначала «Маршрут», потом «Протоколы»). Выбор запоминается в этом браузере.
- В строке пользователя — QR-код, добавление устройства и «⋯» (удаление — там), пауза — переключателем слева. Вместо
  «Выключен» теперь «На паузе» — в метке, в меню и в фильтре.
- Размер текста: «A A A» в шапке панели (на телефоне — «Aa») — мельче, обычный, крупнее. Меняется вся панель, выбор
  запоминается в этом браузере и на сервер не уходит. Тот же выбор — «Размер текста» в «Настройки › Вид».
- «График активности» в режиме «30 дней» делит время на сутки по часовому поясу сервера, а не браузера.

*Выдать настройки*

- «Выдать настройки» — одно окно с вкладками «Подписка (обновляется сама)» и «Статический ключ». Устройство выбирается
  сверху и не сбрасывается при смене вкладки. В подписке — ряд «Платформа» и приложения из каталога панели,
  «рекомендуем» — первым. Адреса подписки на экране нет, пока не нажать «QR-код» или «Скопировать»; при следующем
  открытии окна он снова скрыт. Выключенная подписка включается здесь же.
- Окно «Ссылка для приложений» показывает приложения тем же списком, адрес — только по нажатию.
- У приложений больше нет треугольников — ни в «Статическом ключе», ни на странице пользователя, ни в быстрой
  настройке. Пометки приложения (бесплатное ли, есть ли в российском App Store, проверено ли) — серой строкой под его
  именем, предупреждение о подделках в магазине — одной строкой над списком. Пометка пары «платформа и протокол» —
  серым под заголовком «Установите приложение», там же ссылка на инструкцию. Пометки, которые повторяли друг друга,
  убраны.

*Протоколы*

- Над списком «Протоколы» — схема «Устройства → Сервер → Выходы». Каждый протокол — строкой: точки устройств и сколько
  из них на связи, порт и «Для чего». Нажатие раскрывает строку: описание и кнопки действий, без «⋯»; удаление и
  перезапуск спрашивают подтверждение тут же, в строке.
- Окно установки протокола сразу предлагает свободный порт. Варианты: «Рекомендуемый», «Прежний» (порт и ключи
  с прошлой установки — выданные настройки продолжат работать), «Ближайший свободный», «Случайный свободный» и «Свой
  порт»; на кнопке — какой порт будет занят, например «Установить на 1195». Свой порт проверяется, пока вы его
  вводите: «свободен на сервере» или чем он занят. Если прежний порт занят, окно скажет, что устройствам этого
  протокола придётся выдать настройки заново.
- Служебные порты Kaskad теперь считаются занятыми — при установке, при смене порта и в быстрой настройке; раньше они
  выглядели свободными, а протокол на них работать не мог. Про порт выключенного протокола так и сказано: его держит
  протокол, который сейчас выключен.
- Новый протокол встаёт в конец списка, установленные не меняют мест (порядок помнит браузер).

*Обзор и проверка сервера*

- «Обзор» собран заново. «Сводка»: состояние сервера — паутинка звеньев, слова («Работает нормально», «Работает, есть
  предупреждения», «Требует внимания», при выключенном шлюзе — «Шлюз выключен») и балл сервера; справа — «30 дней»;
  ниже — трафик сервера за сутки и за 7 дней, «Выходы · час», здоровье сервера и события. Числа замечаний больше нет:
  остаются только строки о сбоях и о том, что ждёт вашего действия.
- «Балл сервера» — от 0 до 1000. Нажатие на него открывает окно: паутинка «обычно» и «свежие», что изменилось к
  прошлой проверке, итог по проверкам, «Почему итог не выше» и «Как считается балл». Балл есть только в панели.
- Первые 12 часов нового сервера итог помечен «предварительно»: сеть и потери пакетов ещё измеряются и в итог не
  входят; сколько часов набрано — в «Почему итог не выше». У звеньев, для которых ещё нет эталонных серверов, на большой
  паутинке стоит «≈»; в «Как считается балл» — какие звенья выверены и по каким эталонам.
- «Здоровье сервера»: у каждой метрики — ряд за сутки с порогом, число и что сделать.
- «Расписание проверок» и общий тест скорости (он был в «Выходы › Группы») — теперь в «Настройки › Проверка сервера»;
  старые ссылки ведут туда. В «Обзор › Проверка сервера» остались «Сейчас» и «История».
- «Звенья»: у звена без значения вместо «—» — короткая причина («ждём проб», «не измерялось», «под нагрузкой» и
  другие), полный текст — в подсказке. Пока истории мало, видны точки замеров и когда появится «обычно».
- «Отклик» и «Дрожание» на «Сейчас» больше не пишут «ждём проб», когда пробы идут; кривая отклика и «обычно» по ним
  тоже появились — и для уже накопленной истории.
- DNS-тест проверки сервера там, где шлюзом никто не пользуется (нет включённых протоколов), идёт через обычный DNS
  сервера и больше не пишет каждый раз ошибку.

*Выходы и российские группы*

- «Все выходы»: столбцы «Тип», «Отклик», «Группа» и «Активность» сворачиваются так же, как в «Пользователях»;
  «Отклик» у работающего выхода — последний измеренный, а не «—». В «Выходы · час» имя выхода видно целиком.
- Российский выход везде назван своим именем, а не служебным номером — в «Российских группах», в окне ключа и в
  `kaskadctl`.
- «Группы»: «Удалить группу» — в «⋯» шапки карточки, и её можно нажать, даже если есть несохранённые правки (окно
  предупредит, что они пропадут). В «⋯» выхода — «Убрать из группы». Редкие переключатели — ночная перебалансировка,
  обрыв соединений, «По соединениям», «Новые и перенесённые выходы» — под «Ещё»; «Ещё» раскрыт сам, если что-то из
  них включено.
- «Российские группы» — та же карточка, что у зарубежных групп: значок «РФ», что работает сейчас, своя кнопка добавления
  ключа и блок «О группе». «Ночная перебалансировка» у российской группы на виду: если в уровне два ключа и меняется
  способ выбора, подсказка скажет, что новые устройства раскладываются сразу, а подключённые перейдут ночью (или
  останутся на своём ключе, если ночная выключена). В «Группах» российских групп больше нет — там строка-ссылка
  «Российские группы».
- Последнюю российскую группу с ключами можно удалить вместе с ключами: «Удалить группу и ключи» в панели,
  `kaskadctl gw group-del … --with-keys` и MCP. Раньше это заканчивалось отказом.
- На «Сводке» в «Выходы · час» видны и российские выходы — с серым значком «РФ».

*Прочее*

- «Адрес для клиентов» — только в «Настройки › Шлюз» (в «Домен и сайт» — ссылка на него). Если устройства подключаются
  по домену, там же есть «Вернуть на IP»: окно перечислит устройства, которым нужны новые настройки, и скажет, какие
  протоколы и дальше идут по домену. Если сервер видит только частный адрес, домен остаётся.
- «Домен и сайт»: «Проверка сайта» запускается сама — при открытии страницы и после смены шаблона.
- «Настройки › Хранение данных» → «Удалить историю активности»: с подтверждением и тем, сколько места освободится;
  хранение остаётся включённым, запись начинается заново.
- «Настройки › Проверка сервера» при выключенном расписании — без пустых строк, в пояснениях нет команд.
- Слова состояния — те же, что в панели: `kaskadctl host health` и бот говорят «Всё в норме», «Есть предупреждения»,
  «Требует внимания», как «Здоровье сервера»; шапка SSH-меню — словами «Сводки» и «Здоровья сервера».
- Поле поиска в шапке — «Поиск по панели».

*In English.* **3.3.7.12 — a new "Overview" with the server score, "Users" as a table, "Issue settings" in one window, the port when installing a protocol, Russian groups in the common card**

*Users*

- The "Users" list is a table with the columns "Activity · day", "Online", "Protocols" and "Route". The state of a
  user is a label at the name, who is online — dots. The activity line is longer and has no traffic label.
- The columns "Activity · day", "Protocols" and "Route" fold with a click on the heading, and by themselves in a narrow
  window ("Route" first, then "Protocols"). The choice is kept in this browser.
- A user's row has the QR code, adding a device and "⋯" (deleting is there), the pause is a switch on the left. "Off"
  is now "Paused" — in the label, in the menu and in the filter.
- Text size: "A A A" in the panel header ("Aa" on a phone) — smaller, normal, larger. The whole panel changes, the
  choice is kept in this browser and does not go to the server. The same choice is "Text size" in "Settings ›
  Appearance".
- The "Activity chart" in "30 days" splits the time into days by the time zone of the server, not of the browser.

*Issue settings*

- "Issue settings" is one window with the tabs "Subscription (updates itself)" and "Static key". The device is chosen
  at the top and stays when the tab changes. The subscription has a "Platform" row and the apps from the panel's
  catalog, the recommended one first. The subscription address is not on the screen until you press "QR code" or
  "Copy"; the next time the window opens it is hidden again. A subscription that is off is turned on right there.
- The "Link for apps" window shows the apps in the same list, the address only on a press.
- The apps have no triangles any more — neither in "Static key", nor on the user's page, nor in the quick setup. The
  notes of an app (is it free, is it in the Russian App Store, is it checked) are a grey line under its name, the
  warning about fakes in the store is one line above the list. The note of a pair "platform and protocol" is grey
  under the heading "Install the app", together with the link to the guide. Notes that repeated each other are gone.

*Protocols*

- Above the "Protocols" list there is a diagram "Devices → Server → Exits". Each protocol is a row: the dots of the
  devices and how many of them are online, the port and "Used for". A click opens the row: a description and the
  action buttons, no "⋯"; deleting and restarting ask for a confirmation right there, in the row.
- The protocol install window offers a free port at once. The options: "Recommended", "Previous" (the port and the
  keys of the last install — the settings given out keep working), "Nearest free", "Random free" and "Your own port";
  the button says which port will be taken, for example "Install on 1195". Your own port is checked as you type it:
  "free on the server" or what holds it. If the previous port is taken, the window says that the devices of this
  protocol will need new settings.
- The service ports of Kaskad now count as taken — when installing, when changing a port and in the quick setup; they
  used to look free, but a protocol could not work on them. The port of a protocol that is off says so: a protocol that
  is off now holds it.
- A new protocol goes to the end of the list, the installed ones keep their places (the browser remembers the order).

*Overview and server check*

- "Overview" is built anew. "Summary": the state of the server — a web of the links, words ("Working normally",
  "Working, with warnings", "Needs attention", with the gateway off — "Gateway is off") and the server score; on the
  right — "30 days"; below — the server traffic for the day and for 7 days, "Exits · hour", the server health and the
  events. There is no count of remarks any more: only the lines about failures and about what waits for your action
  stay.
- The "Server score" goes from 0 to 1000. A click on it opens a window: a web of "usual" and "fresh", what changed
  since the previous check, the total by checks, "Why the total is not higher" and "How the score is counted". The
  score is in the panel only.
- In the first 12 hours of a new server the total is marked "provisional": the network and the packet loss are still
  being measured and are left out of the total; how many hours there are is in "Why the total is not higher". The
  links with no reference servers yet have "≈" on the big web; "How the score is counted" says which links are checked
  and against which references.
- "Server health": every metric has a row for the day with its threshold, a number and what to do.
- "Check schedule" and the common speed test (it was in "Exits › Groups") are now in "Settings › Server check"; the
  old links lead there. "Overview › Server check" keeps "Now" and "History".
- "Links": a link with no value has a short reason instead of "—" ("awaiting probes", "not measured yet", "under load"
  and others), the full text is in the tooltip. While the history is short, the dots of the measurements show and
  when "usual" will appear.
- "Latency" and "Jitter" on "Now" no longer say "awaiting probes" while the probes run; the latency curve and "usual"
  for them are there too — for the history already collected as well.
- The DNS test of the server check, where nobody uses the gateway (no protocol is on), goes through the server's usual
  DNS and no longer writes an error every time.

*Exits and Russian groups*

- "All exits": the columns "Type", "Latency", "Group" and "Activity" fold the same way as in "Users"; the "Latency" of
  a working exit is the last one measured, not "—". In "Exits · hour" the name of an exit is seen in full.
- A Russian exit is called by its name everywhere, not by a service number — in "Russian groups", in the key window
  and in `kaskadctl`.
- "Groups": "Delete group" is in the "⋯" of the card header, and it can be pressed even with unsaved edits (the window
  warns that they will be lost). The "⋯" of an exit has "Remove from the group". The rare switches — night
  rebalancing, cutting the connections, "Per connection", "New and moved exits" — are under "More"; "More" opens by
  itself if one of them is on.
- "Russian groups" have the same card as the foreign groups: the "RU" badge, what works now, a button of their own to
  add a key and the block "About the group". "Night rebalancing" of a Russian group is in sight: with two keys in a tier
  and a new way of choosing, a hint says that new devices are spread at once and the connected ones move at night (or
  stay on their key if the night one is off). "Groups" no longer has the Russian groups — there is a link row "Russian
  groups" there.
- The last Russian group with keys can be deleted together with its keys: "Delete group and keys" in the panel,
  `kaskadctl gw group-del … --with-keys` and MCP. It used to end with a refusal.
- In "Exits · hour" of the "Summary" the Russian exits are seen too — with a grey "RU" badge.

*Other*

- "Client address" is only in "Settings › Gateway" ("Domain and site" has a link to it). If the devices connect by
  the domain, there is "Back to IP" there too: the window lists the devices that need new settings and says which
  protocols keep going by the domain. If the server sees only a private address, the domain stays.
- "Domain and site": "Site check" runs by itself — when the page opens and after the template changes.
- "Settings › Data storage" → "Delete activity history": with a confirmation and how much space is freed; the storage
  stays on, the recording starts anew.
- "Settings › Server check" with the schedule off has no empty lines, and its texts have no commands.
- The words of the state are those of the panel: `kaskadctl host health` and the bot say "All normal", "There are
  warnings", "Needs attention", like "Server health"; the header of the SSH menu uses the words of "Summary" and "Server
  health".
- The search field in the header is "Search the panel".

**3.3.7.11 — «Пользователи» и «Выходы» раз в 5 секунд, «Вернуть как было» с доменом и сертификатом, перенос в российскую группу без ошибки, «График активности» в часах**

- Разделы «Пользователи» и «Выходы» обновляются сами, пока вкладка панели открыта: кто в сети, линия активности, объём
  за сутки и линии выходов — не реже раза в 5 секунд (без открытой панели сервер собирает данные в прежнем темпе).
  Состояние выхода, как и раньше, проверяется раз в 10 секунд. История активности не зависит от того, открыта ли
  панель. Перезагружать страницу не нужно.
- «Вернуть как было» в «Настройках» (и `kaskadctl backup undo`) после загрузки копии с другим доменом возвращает и домен
  этого сервера: сертификат, который остался на диске, снова в работе, сайт на домене включается, если был, VLESS снова
  идёт через TLS (или через Reality, если так было до загрузки), SSTP и OpenConnect снова на сертификате домена, если
  копия загружена уже на этой версии. Новый сертификат при этом не запрашивается; если на диске его больше нет или он
  истёк, заметка скажет выпустить его заново. Просьба выдать устройствам настройки заново из-за VLESS или Hysteria2
  появляется, только если их настройки и правда изменились. То же — когда загрузка копии не удалась и сервер сам
  возвращает прежние настройки. Копия с другого сервера, как и раньше, сама ничего не переключает. Загрузка копии
  и «Вернуть как было» больше не сбрасывают выбор движка AmneziaWG этого сервера (модуль ядра или программа).
- Перенос выхода в российскую группу больше не обрывается ошибкой «Неверный id выхода». Если один и тот же ключ лежит и
  среди зарубежных, и среди российских выходов, его двойник не попадает в группы и графики и не мешает сохранять
  группы; двойник, оставшийся от прошлых версий, убирается сам. Пока идёт перенос (или «Вернуть как было»), в ленте
  больше не появляется ложное «Выход «…» выключен».
- `kaskadctl gw exits` показывает и российские выходы — отдельным блоком «Российские выходы»; список зарубежных
  выходов прежний.
- «Российские сайты» → «Выбрать пользователей…»: если выбор того же пользователя успели изменить в другом окне или
  командой `kaskadctl rules ru-via`, сохранение ничего не затирает — панель скажет об этом и покажет текущий выбор.
- Лента и бот говорят о переключении уровней выходов без лишних повторов: пока устройства ждут возврата на основной
  уровень, новый короткий сбой не даёт второго «Переход с уровня …», а у группы без устройств возврат тоже сказан —
  «Снова основной уровень». В ленте у событий выхода стоит его имя, а не служебный номер.
- После добавления выхода AmneziaWG в ленте больше не появляется «Маршрутизация шлюза … восстановлена»: маршрут нового
  выхода сразу встаёт на своё место.
- Бот не присылает сообщение о сбое связи короче минуты (раньше приходило «… (0 мин)»); в ленте такой сбой виден, как и
  раньше.
- «График активности» (пункт в «⋯» у пользователя или устройства или нажатие на линию): в режиме «7 дней» подписан
  каждый день подряд, в режиме «Сутки» подписи идут ровно через 4 часа; время на связи — в часах («423 ч 5 мин», а не
  «17 д 15 ч»), в строке устройства — часы без минут, без обрезки. Нажатие на линию выхода в «Выходах» открывает окно
  выхода сразу на части «Графики».
- В окне «Поделиться» размер ролика указан пределом — «до 0,4 МБ», а не примерно. Карточка «От вашего устройства до
  сервера», если в это время идёт проверка сервера, отвечает прямо: «Идёт проверка сервера — замер подождёт её».
- Если когда-нибудь вернуться на эту версию с более новой, события новой версии в ленте и в боте будут словами, а не
  служебным ключом.

*In English.* **3.3.7.11 — "Users" and "Exits" every 5 seconds, "Undo" brings back the domain and the certificate, a move into a Russian group without an error, the "Activity chart" in hours**

- The "Users" and "Exits" sections update by themselves while the panel tab is open: who is online, the activity line,
  the day's traffic and the lines of the exits at least every 5 seconds (with no panel open the server collects at the
  usual pace). The state of an exit is still checked every 10 seconds. The activity history is the same whether the
  panel is open or not. There is no need to reload the page.
- "Undo" in "Settings" (and `kaskadctl backup undo`) after uploading a backup with another domain brings back this
  server's domain too: the certificate still on the disk is in use again, the site on the domain is turned on if it was
  there, VLESS goes through TLS again (or through Reality, if so it was before the upload), SSTP and OpenConnect are on
  the domain certificate again if the backup was uploaded on this version already. No new certificate is requested; if
  it is no longer on the disk or has expired, a note says to issue it again. The request to give the devices new
  settings because of VLESS or Hysteria2 shows up only if their settings really changed. The same goes when an upload
  fails and the server brings back the previous settings by itself. A backup of another server, as before, switches
  nothing by itself. Uploading a backup and "Undo" no longer reset this server's choice of the AmneziaWG engine (the
  kernel module or the program).
- Moving an exit into a Russian group no longer ends with the error "Wrong exit id". If the same key is both among the
  foreign and among the Russian exits, its double gets into no group and no chart and does not stop the groups from
  being saved; a double left by earlier versions goes away by itself. While a move (or "Undo") is under way, the feed
  no longer shows a false "Exit “…” is turned off".
- `kaskadctl gw exits` shows the Russian exits too — in a block of their own, "Russian exits"; the list of the foreign
  exits is as before.
- "Russian sites" → "Choose users…": if the choice of the same user was changed in another window or by
  `kaskadctl rules ru-via`, saving overwrites nothing — the panel says so and shows the current choice.
- The feed and the bot speak of switching the levels of exits without extra repeats: while the devices wait to return to
  the primary level, a new short failure does not give a second "Switched from level …", and for a group with no
  devices the return is said too — "Back on the primary level". In the feed the events of an exit have its name, not a
  service number.
- After an AmneziaWG exit is added, the feed no longer shows "Gateway routing … restored": the route of the new exit
  takes its place at once.
- The bot does not send a message about a connection failure shorter than a minute (it used to say "… (0 min)"); the
  feed shows such a failure as before.
- "Activity chart" (an item in "⋯" of a user or a device, or a tap on the line): in "7 days" every day in a row has its
  label, in "Day" the labels go exactly every 4 hours; the time online is in hours ("423 h 5 min", not "17 d 15 h"),
  in a device's row hours without minutes, never cut off. A tap on the line of an exit in "Exits" opens the exit's
  window right at "Charts".
- In the "Share" window the size of the clip is given as a bound — "up to 0.4 MB", not an estimate. The card "From
  your device to the server", while a server check is running, says it plainly: "A server check is running — the test
  will wait for it".
- If you ever go back to this version from a newer one, the events of the newer version in the feed and in the bot are
  words, not a service key.

**3.3.7.10 — «Пользователи», подписка со всеми протоколами, выбор при QR, российские сайты через свой роутер, «Поделиться», замер от устройства до сервера**

- «Люди» теперь «Пользователи»: раздел панели называется «Пользователи и устройства», а слово «пользователь» стоит во
  всех текстах панели, SSH-меню, бота и командной строки, на русском и английском. Команды и адреса не менялись.
- Подписка пользователя отдаёт все протоколы сервера: к VLESS и Hysteria2 добавились AmneziaWG и WireGuard. Ссылка та
  же — приложение получит новые ключи при следующем обновлении, добавлять подписку заново не нужно. VLESS и Hysteria2
  берут v2RayTun, Hiddify, v2rayN, Streisand и incy, AmneziaWG и WireGuard — incy на телефоне; приложение, которое
  какой-то протокол не знает, просто пропускает его строки.
- В подписке — ключи всех устройств пользователя, в имени каждой записи есть устройство, например «goKaskad PRO ·
  AmneziaWG 2.0 · Телефон». Ключ AmneziaWG или WireGuard работает на одном устройстве — на телефоне выбирайте запись
  своего телефона.
- Кнопка QR у пользователя и у устройства открывает выбор: «Подписка (обновляется сама)» — сменятся ключи или
  настройки, приложение подтянет новые само; «Статический ключ» — QR или файл, добавить один раз. Если подписке нечего
  отдать (например, у устройства только SOCKS5 или L2TP), сразу открывается статический ключ.
- Когда приложение само обновляет подписку, это не считается выдачей настроек: пометка «ждут новых настроек» остаётся
  до настоящей выдачи, а протокол не подозревается в закрытом порте зря.
- Разделы «Пользователи» и «Выходы» обновляются сами, пока вкладка панели открыта: линия активности и объём за сутки —
  примерно раз в 10 секунд, линии выходов — раз в минуту. Перезагружать страницу не нужно.
- Новые строки списка «Пользователи»: устройства — ветками под пользователем, у имени пользователя — число его
  устройств, система устройства — под его именем. На телефоне устройства раскрываются под пользователем по нажатию на
  «N из M»; раскрытое не сворачивается, когда список обновляется.
- Российские сайты выбранных пользователей могут идти через ваш роутер или сервер в России, а не напрямую с этого
  сервера. Российская группа выходов создаётся в «Выходы › Российские группы»: каждый ключ — свой шаг по порядку, а
  если ни один не отвечает — напрямую с сервера. Кому через какую группу, выбирается в «Правилах», блок «Российские
  сайты» → «Выбрать пользователей…»; выбор действует у пользователей с профилем «Россия напрямую», остальным —
  напрямую с сервера, как раньше. Эти соединения увидит интернет-провайдер роутера или сервера в России. В SSH-меню
  выбор виден в карточке пользователя, задать его можно и командой `kaskadctl rules ru-via`.
- Бот ходит в Telegram через выходы группы — так он работает и там, где Telegram закрыт, например на сервере в России.
  Группа выбирается в «Настройки › Бот и ИИ-агент» → «Как бот ходит в Telegram», там же можно выбрать «Напрямую»; по
  умолчанию, как и раньше, — «Основная». Если выходы группы не отвечают, а напрямую Telegram открывается, бот временно
  ходит напрямую и сам возвращается к выходам. Как бот ходит сейчас, сказано словами в панели, в SSH-меню бота и
  командой `kaskadctl bot proxy`.
- Когда бот спрашивает согласия на шаг, после которого устройствам понадобятся новые настройки, «Да» относится именно
  к названным устройствам: если с тех пор устройства поменялись, кнопка ничего не делает и бот спрашивает заново.
- «Поделиться» заработало. В «Обзор › Проверка сервера › Сейчас» кнопка открывает окно, где собирается ролик на 15
  секунд (GIF) или картинка (PNG) с цифрами последней проверки: квадрат или пост, тёмная или светлая тема, русский или
  английский. IP, домен и имя сервера в кадр не попадают; тариф, страну и модель процессора можно добавить галочками.
  Рядом с датой — время проверки по UTC и по Москве; под скачиванием и отдачей видно, до какой точки мерили, под
  откликом и потерями — до каких DNS сервер отправлял пинг. В боте — кнопка «📤 Поделиться проверкой» на экране
  «💻 Система» и команда `/share`: бот присылает ссылку, которая открывает это окно в панели. Если проверка показала,
  что сервер заметно хуже, чем обычно, бот об этом пишет.
- В «Обзор › Проверка сервера › Сейчас» появилась карточка «От вашего устройства до сервера»: кнопка «Проверить» за
  ≈ 20 секунд меряет отклик, скачивание и отдачу между вашим браузером и сервером и показывает, насколько растёт
  отклик под нагрузкой. Это замер пути, а не оценка самого сервера. Сколько трафика уйдёт, написано у кнопки, в
  мобильной сети панель спросит ещё раз; число проверок за час и за сутки ограничено, история хранится только на этом
  устройстве. Замер работает, когда панель открыта и по IP, и по домену.

*In English.* **3.3.7.10 — "Users", a subscription with every protocol, a choice at the QR button, Russian sites through your own router, "Share", a test from your device to the server**

- "People" are "Users" now: the panel section is called "Users & devices", and the word "user" is used in every text of
  the panel, the SSH menu, the bot and the command line, in Russian and in English. Commands and addresses did not
  change.
- A user's subscription gives every protocol of the server: AmneziaWG and WireGuard joined VLESS and Hysteria2. The link
  is the same — the app gets the new keys on its next refresh, there is no need to add the subscription again. VLESS
  and Hysteria2 are taken by v2RayTun, Hiddify, v2rayN, Streisand and incy, AmneziaWG and WireGuard by incy on a phone;
  an app that does not know a protocol just skips its lines.
- The subscription holds the keys of all the user's devices, and the name of each entry has its device, for example
  "goKaskad PRO · AmneziaWG 2.0 · Phone". An AmneziaWG or WireGuard key works on one device — on a phone pick the entry
  of that phone.
- The QR button of a user and of a device opens a choice: "Subscription (updates itself)" — if keys or settings change,
  the app picks up the new ones by itself; "Static key" — a QR code or a file, added once. If the subscription has
  nothing to give (for example, the device has only SOCKS5 or L2TP), the static key opens at once.
- When an app refreshes the subscription by itself, it does not count as giving out the settings: the mark "waiting for
  new settings" stays until they are really given out, and the protocol is not suspected of a closed port for nothing.
- The "Users" and "Exits" sections update by themselves while the panel tab is open: the activity line and the day's
  traffic about every 10 seconds, the lines of the exits once a minute. There is no need to reload the page.
- New rows of the "Users" list: devices are branches under their user, the user's name shows the number of devices, the
  device's system is under its name. On a phone the devices open under the user with a tap on "N of M"; what is open
  stays open when the list updates.
- The Russian sites of selected users can go through your own router or a server in Russia instead of straight from
  this server. A Russian group of exits is made in "Exits › Russian groups": each key is its own step in order, and if
  none answers — directly from the server. Who goes through which group is chosen in "Routing rules", the "Russian
  sites" block → "Choose users…"; the choice works for users with the "Russia direct" profile, the others go directly
  from the server as before. The internet provider of the router or of the server in Russia sees these connections. In
  the SSH menu the choice is shown in the user's card, and the command `kaskadctl rules ru-via` can set it too.
- The bot reaches Telegram through the exits of a group, so it works where Telegram is blocked too, for example on a
  server in Russia. The group is chosen in "Settings › Bot and AI agent" → "How the bot reaches Telegram", where you
  can also pick "Directly"; by default, as before, it is "Main". If the exits of the group do not answer while
  Telegram opens directly, the bot goes directly for a while and returns to the exits by itself. How the bot goes now
  is said in words in the panel, in the bot's SSH menu and by `kaskadctl bot proxy`.
- When the bot asks for a yes to a step after which devices will need new settings, "Yes" is about exactly the devices
  it named: if the devices have changed since, the button does nothing and the bot asks again.
- "Share" works now. In "Overview › Server check › Now" the button opens a window that makes a 15-second clip (GIF) or
  a picture (PNG) with the numbers of the latest check: square or post, dark or light theme, Russian or English. The
  IP, the domain and the name of the server never get into the frame; the tariff, the country and the processor model
  can be added with switches. Next to the date is the time of the check in UTC and in Moscow time; under download and
  upload you see which point was measured, under latency and loss — which DNS the server pinged. In the bot — the
  button "📤 Share the check" on the "💻 System" screen and the command `/share`: the bot sends a link that opens this
  window in the panel. If a check shows that the server is clearly worse than usual, the bot says so.
- "Overview › Server check › Now" has a new card "From your device to the server": the button "Run test" measures in
  about 20 seconds the latency, download and upload between your browser and the server and shows how much the latency
  grows under load. It measures the path, not the server itself. How much traffic it takes is written next to the
  button, on a mobile network the panel asks once more; the number of tests per hour and per day is limited, and the
  history is kept only on this device. The test works with the panel open both by IP and by domain.

**3.3.7.9 — история активности людей, российские выходы, окна VLESS по макету**

- Линия активности в «Людях» теперь рисуется по настоящим данным. Сервер сам копит историю: когда устройство было на
  связи и сколько трафика прошло — по 5 минут за сутки, по часу за 30 дней, итоги по дням за 400 дней. Адреса, сайты и
  отдельные соединения не записываются. История видна только администратору, страница человека её не показывает.
  Выключить и стереть — «Настройки › Хранение данных», «Хранить историю активности». Посмотреть из командной строки:
  `kaskadctl gw activity <человек|устройство>`.
- После обновления история начинается с нуля. Прежние графики выходов и «Проверка сервера» остаются как были.
- Российские выходы проверяются без частых обращений к сайтам — по времени соединения. Сообщения бота о них называют
  ключи по именам, а короткие пропадания собираются в одну сводку. О долгом сбое российской сети сервера бот пишет один
  раз. Выбор российской группы для людей появится в следующих версиях.
- Если выход AWG с именем хоста перестал отвечать, Kaskad сам заново узнаёт его адрес — не чаще раза в 5 минут.
- Окна VLESS — как в макете: имена устройств видны сразу, окна сайта короче. «Убрать домен» и «Выключить сайт» —
  красной кнопкой, как и без VLESS. На телефоне в списке протоколов виден режим VLESS.

*In English.* **3.3.7.9 — the history of people's activity, Russian exits, VLESS windows as designed**

- The activity line in "People" now shows real data. The server keeps the history itself: when a device was connected
  and how much traffic went through — every 5 minutes for a day, every hour for 30 days, daily totals for 400 days.
  Addresses, sites and single connections are not written. Only the admin sees the history, the person's page does not
  show it. To turn it off and erase it — "Settings › Data storage", "Keep activity history". To view it from the
  command line: `kaskadctl gw activity <person|device>`.
- After the update the history starts from zero. The exits' charts and "Server check" stay as they were.
- Russian exits are checked without frequent requests to sites — by the time of the connection. The bot's messages about
  them name the keys, and short outages come as one summary. About a long outage of the server's Russian network the bot
  writes once. Choosing a Russian group for people comes in the next versions.
- If an AWG exit with a host name stops answering, Kaskad looks up its address again by itself — not more than once in
  5 minutes.
- The VLESS windows are as designed: device names are seen at once, the site windows are shorter. "Remove the domain"
  and "Turn off the site" have a red button, as without VLESS. On a phone the protocols list shows the VLESS mode.

**3.3.7.8 — VLESS: свой сертификат (TLS) по умолчанию**

- Новая установка VLESS на сервере с доменом и сертификатом сама включает сайт на домене: VLESS работает в TLS со
  своим доменом на 443. Без домена — Reality, а когда домен и сертификат появятся и ссылок ещё никто не получал, VLESS
  перейдёт на TLS сам. Если ссылки уже выданы, Kaskad только рекомендует переход: называет устройства и говорит, как
  перейти, а цена предупреждает, что сайт на домене включится вместе с переходом.
- «Маскировка VLESS» в SSH-меню (17 → 1 → VLESS) вместо «Сменить сайт маскировки» и «Режим по сайту узла»:
  «1) Свой сертификат (TLS) — рекомендуем» и «2) Reality». По умолчанию предлагается текущий режим. Если TLS сейчас
  нельзя, пункт 1 называет причину и что исправить.
- В панели — окно «Маскировка VLESS» (Протоколы → VLESS → «Маскировка…»): свой сертификат первым, ниже Reality,
  цена — до нажатия. Режим — в имени карточки («VLESS · TLS» / «VLESS · Reality»), причина Reality — строкой под
  ним, в раскрытой — кнопка, которая устраняет причину, или рекомендация перейти на TLS с ценой. «Режим по сайту
  узла» убран.
- Мастер установки на шаге «Домен» включает сайт по «Дальше» — VLESS переходит на TLS; «Не включать сайт»
  оставляет Reality с согласием. Есть устройства VLESS — сначала цена: кому выдать ссылки заново.
- Reality при возможном TLS — сознательный выбор: сначала сайт маскировки, затем текст рисков и «Понимаю риски».
  Согласие записывается. Бот согласия не даёт: он показывает режим, причину и рекомендацию, а переключать — в панели
  или SSH-меню.
- Включение и выключение сайта, «Убрать домен» (в панели, боте и SSH-меню), переход между TLS и Reality, шаг
  мастера «сайт» сначала называют цену: кому придётся выдать ссылки VLESS заново. Без согласия ничего не меняется.
- Если смена домена не удалась и откат не вернул VLESS на прежние ссылки, Kaskad так и говорит и называет, кому выдать
  ссылки заново.
- Серверы после обновления работают как раньше: режим VLESS не меняется, пока вы его не выберете.

*In English.* **3.3.7.8 — VLESS: own certificate (TLS) by default**

- A new install of VLESS on a server with a domain and its certificate turns on the site on the domain by itself:
  VLESS works in TLS with its own domain on 443. Without a domain — Reality; when the domain and the certificate come
  and nobody has got links yet, VLESS goes to TLS by itself. If links are given out already, Kaskad only recommends the
  switch: it names the devices and says how to switch, and the price warns that the site on the domain goes on with
  the switch.
- “VLESS masking” in the SSH menu (17 → 1 → VLESS) instead of “Change masking site” and “Match the mode to the
  server’s site”: “1) Own certificate (TLS) — recommended” and “2) Reality”. The current mode is the default. If TLS
  isn’t possible now, item 1 names the reason and what to fix.
- In the panel — the “VLESS masking” window (Protocols → VLESS → “Masking…”): own certificate first, Reality below,
  the price before you press. The mode is in the card’s name (“VLESS · TLS” / “VLESS · Reality”), the reason for
  Reality is a line under it; the open card has a button that removes the reason or the recommendation to switch to
  TLS with its price. “Match the mode to the server’s site” is gone.
- The setup wizard turns the site on at the “Domain” step on “Next” — VLESS moves to TLS; “Don’t turn on the site”
  keeps Reality with the consent. With VLESS devices the price comes first: whom to give the links again.
- Reality while TLS is possible is a choice made on purpose: the masking site first, then the text of the risks and
  “I understand the risks”. The consent is recorded. The bot gives no consent: it shows the mode, the reason and the
  recommendation, and the switch is in the panel or the SSH menu.
- Switching the site on or off, “Remove the domain” (in the panel, the bot and the SSH menu), moving
  between TLS and Reality, the wizard step “site” name the price first: who will need the VLESS links again. Without a
  yes nothing changes.
- If a change of the domain fails and the undo does not bring VLESS back to its old links, Kaskad says so and names
  whom to give the links again.
- Servers work as before after the update: the VLESS mode does not change until you choose it.

**3.3.7.7 — новый вид панели и проверка сервера**

- Панель в новом виде: у каждого раздела своё меню второго уровня. «Обзор» собирает главное на одном экране.
  «Настройки» разложены по частям.
- «Люди» и «Все выходы» — плотные таблицы: свёрнутая строка, раскрытие по нажатию. В строке — линия активности за сутки.
  Можно выбрать плотность и показать только работающие или только с проблемой. «Группы» тоже показаны строками.
- Пометка ⚠РФ показана один раз — в «Списках».
- Новое — проверка сервера: процессор, память, диск, канал и сеть. Быстрая или полная проверка запускается кнопкой
  «Проверить сейчас» в «Обзор › Проверка сервера», в SSH-меню (пункт 22 «Нагрузка и сеть сервера» → «Проверить
  сервер»), командой `kaskadctl host bench` и из MCP (`host_bench`).
  - Каждое звено сравнивается с тем, как обычно работает этот же сервер: «в целом 100 от обычного» — значит, как
    всегда. Сравнение появляется, когда накопятся 3 дня истории. Порог отметок ▲/▼ задаётся в «Настройки › Проверка
    сервера».
  - Проверки можно поставить в расписание по дням и времени: в панели, в SSH-меню («Расписание проверок») и командой
    `kaskadctl host bench schedule`. В «Истории проверок» — каждая проверка подробно, сравнение двух и выгрузка.
  - Бот сообщает, когда проверка закончилась и когда проверки по расписанию пропущены, потому что сервер был занят.
  - Для тестов процессора, памяти и диска нужны программы sysbench и fio, для замера отдачи до точек провайдеров —
    iperf3. Если их нет, Kaskad спросит, ставить ли их; без согласия ничего не ставит. Проверка по расписанию
    программы не устанавливает.
  - Кнопка «Поделиться» заработает в версии 3.3.7.10.
- Сборка модуля ядра AmneziaWG при нехватке памяти теперь честно об этом говорит и сохраняет журнал сборки. Мы рекомендуем
  сервер от 2 ГБ памяти.
- Старые ссылки на разделы панели ведут на новые места.

*In English.* **3.3.7.7 — a new panel look and the server check**

- The panel has a new look: each section has its own second-level menu. "Overview" puts the main things on one screen.
  "Settings" are split into parts.
- "People" and "All exits" are dense tables: a collapsed row that opens on a tap, with a one-day activity line in the
  row. You can pick the density and show only the working ones or only the ones with a problem. "Groups" are shown as
  rows too.
- The ⚠RU mark is shown once, in "Lists".
- New: the server check — processor, memory, disk, channel and network. A quick or a full check starts with "Check
  now" in "Overview › Server check", in the SSH menu (item 22 "Server load & network" → "Check the server"), with
  `kaskadctl host bench` and from MCP (`host_bench`).
  - Each link is compared with how this same server usually works: "overall 100 of usual" means as always. The
    comparison appears after 3 days of history. The ▲/▼ mark threshold is set in "Settings › Server check".
  - Checks can be put on a schedule by days and time: in the panel, in the SSH menu ("Check schedule") and with
    `kaskadctl host bench schedule`. "Check history" shows each check in detail, compares two checks and exports
    them.
  - The bot tells you when a check has ended and when scheduled checks were skipped because the server was busy.
  - The processor, memory and disk tests need the programs sysbench and fio, the upload test to the points of the
    providers needs iperf3. If they are missing, Kaskad asks whether to install them; without a yes it installs
    nothing. A scheduled check never installs programs.
  - The "Share" button starts working in version 3.3.7.10.
- When there is not enough memory to build the AmneziaWG kernel module, Kaskad now says so plainly and keeps the build
  log. We recommend a server with 2 GB of memory or more.
- Old links to panel sections lead to their new places.

**3.3.7.6 — быстрая настройка снова с оформлением**

- В 3.3.7.5 мастер быстрой настройки на новых серверах открывался без оформления: не загружались шрифты, цвета и
  часть кнопок. Мастер брал общие файлы панели с корня порта, а 3.3.7.5 закрыл корень от сканеров. Теперь, пока мастер
  открыт, сервер отдаёт ему только эти файлы. Сама панель и всё остальное на корне порта по-прежнему не открываются.
- Если мастер уже открылся без оформления — обновите Kaskad и откройте быструю настройку заново из меню `gokaskad`.

*In English.* **3.3.7.6 — the quick setup has its look again**

- In 3.3.7.5 the quick setup wizard on new servers opened without its look: fonts, colors and some buttons did not
  load. The wizard took the shared files of the panel from the root of the port, and 3.3.7.5 closed the root to
  scanners. Now, while the wizard is open, the server gives it only these files. The panel itself and everything else
  at the root of the port still do not open.
- If the wizard has already opened without its look, update Kaskad and open the quick setup again from the `gokaskad`
  menu.

**3.3.7.5 — адрес панели с секретным путём**

Доступ к панели из интернета по порту теперь тоже открывается только по секретному адресу — так же, как панель на
домене: `https://адрес:порт/app-…/`. По прежнему адресу без пути — корень порта, страница входа, файлы панели —
отвечает обычная страница «не найдено», и по ней не видно, что там панель. Если доступ из интернета был включён, после
обновления старый адрес без пути сразу перестаёт открываться; новый — в меню сервера (пункт 18 и шапка главного меню:
F1 или «п» — показать целиком) и в `kaskadctl panel status --show`. Войти в панель после обновления нужно заново.
Страницы людей и выданные им ссылки работают как раньше.

- Ссылки людей — страницы, подписки для приложений, QR-коды, ссылки в боте и в меню — с сайтом на домене теперь
  строятся на домене. Уже выданные ссылки с портом открываются, пока включён запасной вход по порту; «Выключить
  запасной вход» предупреждает, что они перестанут открываться. Без домена панель и ссылки людей — по порту, на
  сертификате Let’s Encrypt для IP-адреса, если он выпущен; иначе — на самоподписанном.
- «Сменить домен» и «Убрать домен» предупреждают, что ссылки людей на домене перестанут открываться и людям нужно
  выдать новые. Если вход по порту выключен, «Убрать домен» сначала включает его — иначе у панели и у людей не
  останется адреса.
- На новой установке вход по порту выключается сам — после первого входа администратора в панель через домен и только
  если ни одной ссылки людей с портом ещё не выдавалось. Иначе он остаётся, а «Адрес панели» подсказывает, что его
  можно выключить. На обновлённых серверах он остаётся как был.

- У панели один секретный адрес. С сайтом на домене главный адрес — на домене, а вход по порту — запасной, по тому же
  секрету; «Сменить секретный адрес» меняет оба.
- В «Настройках» адрес панели показывает одна карточка «Адрес панели» (раздел «Доступ к панели»): маска, «Показать
  полностью», «Скопировать», «Сменить секретный адрес» и строка о запасном входе с кнопкой «Выключить запасной вход».
  Блок «Новый адрес панели» из карточки сертификата домена убран.
- Переключатели «на сертификате домена» для SSTP и OpenConnect есть и в карточках этих протоколов, в «Подробнее»: это
  та же настройка, что в «Настройках», с тем же предупреждением о настройках устройств.

*In English.* **3.3.7.5 — the panel address with a secret path**

Access to the panel from the internet by the port now also opens only at a secret address — just like the panel on the
domain: `https://address:port/app-…/`. The old address without the path — the root of the port, the login page, the
panel files — answers with an ordinary “not found” page that does not show a panel is there. If access from the
internet was on, the old address without the path stops opening right after the update; the new one is in the server
menu (item 18 and the head of the main menu: F1 or p shows it in full) and in `kaskadctl panel status --show`. Sign in
to the panel again after the update. People’s pages and the links given to them work as before.

- People’s links — pages, subscriptions for apps, QR codes, links in the bot and in the menu — are now built on the
  domain when the site on the domain is on. Links with the port given earlier keep opening while the backup entry by
  port is on; “Turn off the backup entry” warns that they stop opening. Without a domain the panel and people’s links
  are on the port, with a Let’s Encrypt certificate for the IP address if it was issued; otherwise a self-signed one.
- “Change domain” and “Remove domain” warn that people’s links on the domain stop opening and people need new ones.
  If the entry by the port is off, “Remove domain” switches it on first — otherwise neither the panel nor people keep
  an address.
- On a new install the entry by the port goes off by itself — after the admin’s first sign-in to the panel through the
  domain, and only if no people’s link with the port has been given yet. Otherwise it stays, and “Panel address”
  hints that it can be turned off. On updated servers it stays as it was.

- The panel has one secret address. With the site on the domain the main address is on the domain, and the entry by
  the port is a backup one under the same secret; “Change secret address” changes both.
- In “Settings” one card, “Panel address” (the “Panel access” section), shows the panel address: masked, “Show in
  full”, “Copy”, “Change secret address” and a line about the backup entry with “Turn off the backup entry”. The
  “New panel address” block is gone from the domain certificate card.
- The “on the domain certificate” switches for SSTP and OpenConnect are in the cards of these protocols too, under
  “Details”: the same setting as in “Settings”, with the same warning about device settings.

**3.3.7.4 — основа российских групп**

Внутри Kaskad готова основа российских групп. Включить их пока нельзя: выбор российской группы для человека или
устройства появится в следующих версиях, а в панели пункт «Российская» помечен «Появится в следующем обновлении». У
тех, кто обновляется, люди, группы, маршруты и ⚠РФ остаются как были.

Когда выбор появится:
- российские сайты человека или устройства пойдут через его российскую группу — выход в России или свой ключ,
  например домашний роутер; остальное — по его зарубежному маршруту;
- резерв — второй и третий ключ по уровням; если не отвечают все, российские сайты откроются напрямую с адреса
  сервера или закроются — как выбрано у группы;
- российский выход проверяется по российским адресам, без зарубежных;
- смена роутера закроет только открытые соединения xray через прежний роутер, сам xray не перезапускается;
- частота проверок группы — «Часто» или «Лениво»; у группы «Дом» по умолчанию «Часто», и пропавший домашний роутер
  заметен примерно за минуту.

*In English.* **3.3.7.4 — the groundwork of Russian groups**

The groundwork of Russian groups is inside Kaskad. They cannot be turned on yet: choosing a Russian group for a person
or a device comes in the next versions, and the “Russian” item in the panel is marked “Comes in the next update”. On
update, people, groups, routes and the ⚠RU marks stay as they were.

When the choice arrives:
- the Russian sites of a person or a device will go through their Russian group — an exit in Russia or your own key,
  for example a home router; the rest follows their foreign route;
- reserve — a second and a third key by tiers; if none answers, Russian sites open directly from the server’s address
  or are closed, as the group is set;
- a Russian exit is checked by Russian addresses, without foreign ones;
- changing the router closes only the open xray connections through the old router, xray itself does not restart;
- the group’s check frequency is “Often” or “Lazy”; a “Home” group defaults to “Often”, and a lost home router is
  noticed in about a minute.

**3.3.7.3 — панель: вёрстка, приложения incy и Karing, выход и отзыв доступа, карточки протоколов**

- Вёрстка панели по замечаниям. Подробности входа VLESS и Hysteria2 свёрнуты в «Подробнее»; строка журнала в
  карточке «не работает» — без служебных символов и не длиннее двух строк. Убраны пустоты в карточках «Протоколов»
  и в окне выдачи, «Настройки» — в одну колонку. На телефоне кнопки окон
  подписаны целиком, длинные имена в списках переносятся на вторую строку. Смена раздела закрывает открытые боковые
  панели.
- В списке приложений для подключения — incy и Karing там, где у них есть официальные сборки: VLESS на Android,
  Android TV, Windows, macOS и Linux, Hysteria2 на iOS, macOS, Android, Windows и Linux.
- «Выйти» закрывает сеанс на сервере, и только после ответа сервера панель показывает вход. Если ответа нет —
  панель говорит «Выход не подтверждён». После выхода на странице не остаётся данных сервера.
- Отзыв доступа доходит до уже открытых вкладок. Это срабатывает, когда вы:
  - выключили доступ из интернета;
  - включили «Пароль и в SSH-туннеле»;
  - задали первый пароль или сменили его в SSH-меню;
  - вышли в другой вкладке;
  - закрыли мастер быстрой установки;
  - сменили секретный путь или домен панели за nginx.

  Вкладка с доступом по старым правилам сразу перестаёт получать живые данные, а её запросы больше не выполняются.
- Карточки протоколов точнее называют причину. Если порт 443/udp занят чужой программой, карточка Hysteria2 говорит
  об этом, а VLESS и SOCKS5 больше не пишут «порт 443/tcp занят». Если у Hysteria2 нет файла сертификата, он не
  читается, к нему не подходит ключ или вышел срок, карточка показывает «не работает» — и на «Обзоре» тоже — и
  предлагает кнопку «Применить настройки»: сервер запишет сертификат заново и перезапустит Xray шлюза. Если сертификат
  остался тем же, ключи устройств прежние; если сменился — панель покажет, кому выдать ключи заново.
  Если сертификат домена и это не помогло — продлите его в «Настройки → Домен и сайт».

*In English.* **3.3.7.3 — the panel: layout, the incy and Karing apps, sign-out and revoked access, protocol cards**

- The panel layout after the review. The details of the VLESS and Hysteria2 inbounds are folded under “Details”; the log
  line on a “not working” card has no control characters and takes at most two lines. The empty gaps in the Protocols
  cards and in the issue window are gone, and Settings is one column. On a phone the
  dialog buttons show their whole labels, and long names in lists wrap to a second line. Switching sections closes
  the side panels that are open.
- The list of apps to connect with has incy and Karing where they have official builds: VLESS on Android,
  Android TV, Windows, macOS and Linux, Hysteria2 on iOS, macOS, Android, Windows and Linux.
- “Sign out” ends the session on the server, and the panel shows the login only after the server answers. With no
  answer, the panel says “Sign-out not confirmed”. After signing out, no server data stays on the page.
- Revoked access reaches tabs that are already open. This works when you:
  - turn the access from the internet off;
  - turn on “Password in SSH tunnel too”;
  - set the first password or change it in the SSH menu;
  - sign out in another tab;
  - close the quick setup wizard;
  - change the secret path or the domain of the panel behind nginx.

  A tab whose access came from the old rules stops getting live data at once, and its requests are no longer carried
  out.
- Protocol cards name the cause more precisely. When port 443/udp is taken by another program, the Hysteria2 card
  says so, and VLESS and SOCKS5 no longer say “port 443/tcp is taken”. When Hysteria2 has no certificate file, cannot
  read it, the key does not fit it or it has expired, the card shows “not working” (and so does the Overview) and
  offers an “Apply settings” button: the server writes the certificate again and restarts the gateway’s Xray. If the
  certificate stayed the same, the devices keep their keys; if it changed, the panel shows whom to give new keys. With
  the domain certificate, if that does not help, renew it in Settings → Domain and site.

**3.3.7.2 — исправление безопасности: копия настроек и DNS устройств**

- Копия настроек (`.kbk`) больше не может выполнить команды на сервере. Файлы мониторинга из копии раньше
  восстанавливались как есть, а SSH-меню и служба мониторинга читали их как скрипт. Теперь Kaskad берёт из них только
  адрес сервера и числа (интервал, порог, пауза между уведомлениями) и записывает своим форматом; файл без них не
  переносится — Kaskad говорит об этом при восстановлении. Файлы, которые уже лежат на сервере, проверяются при
  обновлении: лишние строки убираются, негодные файлы удаляются, всё записывается в журнал `/var/log/kaskad.log`.
- Восстановление копии с испорченным текстом больше не обрывается на полпути: вся копия проверяется до того, как
  что-то меняется, а любой сбой по дороге возвращает сервер как было — из копии на нём ничего не остаётся.
- Ключи ИИ-агентов из копии больше не переносятся сами: агент с таким ключом управляет сервером по SSH. Перенести их
  можно только явно — отметкой в окне «Загрузить копию» панели, ответом «2» на вопрос SSH-меню или флагом
  `kaskadctl backup import … --agents`; делайте это только со своей копией. «Вернуть как было» возвращает ключи точно.
- Имена и заметки серверов из копии и из прежних версий больше не обрезаются до 64 знаков. Файл имён, который копия
  привезла в 3.3.5–3.3.7, при обновлении очищается от служебных символов терминала.
- DNS устройств WireGuard, AmneziaWG, IKEv2, L2TP и других протоколов шлюза больше не уходит мимо выхода из-за того,
  что у кого-то другого маршрут «напрямую». Раньше общий DNS шлюза в этом случае мог спрашивать имена у 1.1.1.1 прямо
  с сервера для всех устройств. Теперь у устройств, которые сейчас идут напрямую, свой DNS (служба
  `kaskad-gw-dnsdirect`), а остальные спрашивают имена только через выход; нет живого выхода — их DNS молчит.
  Устройствам «напрямую» имена отдают DNS-серверы самого сервера (те же, что у хостинга), 1.1.1.1 — запасом: у
  сервера в России 1.1.1.1 бывает недоступен. Порты этих служб устройствам напрямую закрыты.
- Режим «Только Россия» (все выходы лежат, у человека «Россия напрямую»): у устройств WireGuard, AmneziaWG и других
  протоколов шлюза российские сайты открываются по адресу и из кэша, а по новому имени — нет. Раньше имена в этом
  режиме иногда разрешались через 1.1.1.1 мимо выхода — это и была закрытая утечка.
- Справка SSH-меню называет верный номер пункта «Добавить ключ выхода»: 17 → 3 → 3, а при маршруте «напрямую» или
  «блок» — 17 → 3 → 2.
- `kaskadctl gw exit … --enable --name … --json`: если имя не принято уже после включения, ответ — один JSON.

*In English.* **3.3.7.2 — a security fix: the settings copy and the DNS of devices**

- A settings copy (`.kbk`) can no longer run commands on the server. The monitoring files of a copy used to be restored
  as they were, and the SSH menu and the monitoring service read them as a script. Now Kaskad takes only the server
  address and the numbers (interval, threshold, pause between alerts) from them and writes them in its own format; a
  file without them is not moved, and Kaskad says so during the restore. The files already on the server are checked
  on the update: extra lines are removed, bad files are deleted, all of it goes to the log `/var/log/kaskad.log`.
- Restoring a copy with damaged text no longer stops half-way: the whole copy is checked before anything changes, and
  any failure on the way brings the server back as it was — nothing of the copy stays on it.
- The AI agent keys of a copy are no longer brought over by themselves: an agent with such a key runs the server over
  SSH. They come only when asked for — a tick in the panel’s “Load a copy” window, the answer “2” in the SSH menu, or
  `kaskadctl backup import … --agents`; do it only with your own copy. “Undo” brings the keys back exactly.
- Server names and notes from a copy and from earlier versions are no longer cut to 64 characters. The names file a
  copy brought in 3.3.5–3.3.7 is cleaned of terminal control characters on the update.
- The DNS of WireGuard, AmneziaWG, IKEv2, L2TP and the other gateway protocols no longer goes past the exit because
  someone else has the route “direct”. Before, the shared gateway DNS could then ask 1.1.1.1 for names straight from
  the server for every device. Now the devices going directly at the moment have their own DNS (the service
  `kaskad-gw-dnsdirect`), and the others ask for names only through the exit; with no live exit their DNS is silent.
  The devices going directly get names from the server’s own DNS servers (the hosting’s), with 1.1.1.1 as a reserve:
  a server in Russia may not reach 1.1.1.1 at all. The ports of these services are closed to the devices.
- The “Russia only” mode (every exit down, the person has “Russia directly”): for WireGuard, AmneziaWG and the other
  gateway protocols Russian sites open by address and from the cache, not by a new name. Before, names in this mode
  sometimes resolved through 1.1.1.1 past the exit — that was the leak now closed.
- The SSH menu guide gives the right number of “Add an exit key”: 17 → 3 → 3, and 17 → 3 → 2 with the route “direct”
  or “block”.
- `kaskadctl gw exit … --enable --name … --json`: when the name is refused after the switch, the answer is one JSON.

**3.3.7.1 — исправления 3.3.7, вход Hysteria2, группы выходов в kaskadctl и SSH-меню, переименование**

- **Ни одного включённого выхода** (не добавлены, выключены или сняты квотой), а у кого-то на сервере маршрут
  «напрямую». Раньше сервер сам узнавал адреса сайтов остальных людей мимо выхода — их имена видел провайдер
  сервера. Теперь их трафик блокируется: у людей с «Россия напрямую» российские сайты не открываются ни по имени, ни
  по адресу — на VLESS и SOCKS5 раньше открывались, на новом Hysteria2 так же закрыты. Если выходы есть, но не
  работают, всё как раньше.
- **«Россия напрямую» при повреждённом списке организаций ⚠РФ.** Если список не читается, согласие не записывается
  ни при установке, ни в SSH-меню, панели, боте или у ИИ-агентов: Kaskad пишет, что не прочитал список организаций
  ⚠РФ, и советует обновиться. Путь к файлу — в журнале.
- **Лента событий групп.** «Весь трафик идёт напрямую» больше не теряется, если за минуты до этого был сказан блок.
  Итог окна называет верный шаг, число устройств и название группы — и после перезапуска сборщика.
- **Здоровье сервера:** серьёзный уровень везде одними словами — «Есть серьёзные неполадки» (было «Серверу плохо»);
  «Повторов TCP» на тихом сервере не мигает: при малом числе повторов уровень не поднимается, а предупреждение
  держится, пока доля не опустится ниже 1,5%.
- **Новый вход Hysteria2 (UDP).** Устройства подключаются по Hysteria2: ссылка `hysteria2://`, QR-код и подписка, как
  у VLESS. С доменом — его сертификат, без домена — свой сертификат с отпечатком в ссылке. Маскировка Salamander и
  смена портов (диапазон до 100 портов) — в карточке входа; там же предупреждение, если домена нет. Блокировки UDP
  в мобильных сетях РФ не проверены — если не подключается, VLESS (TCP). Роутеры Hysteria2 не поддерживают — для них
  AmneziaWG или WireGuard. Добавление устройства не перезапускает остальные подключения.
- **Группы выходов — в kaskadctl, SSH-меню и для ИИ-агентов.** `kaskadctl gw groups` показывает группы, уровни и
  устройства без интернета; выход можно увести в запас, вернуть, поставить на обслуживание, задать ему квоту, перенести
  в другую группу; группу можно удалить. Перед необратимым Kaskad показывает, что изменится, и спрашивает цифрами.
  Маршрут человека или устройства можно направить на группу. В SSH-меню «Выходы» — пункт «Состояние и группы»
  (просмотр), «если выходы недоступны — как у группы». Настройка групп по-прежнему в панели.
- **Часовой пояс.** По умолчанию — Москва; сменить можно в мастере первой установки, в SSH-меню (11 → «Часовой пояс»),
  в `kaskadctl tz set` и у ИИ-агентов. По нему сбрасываются дневные квоты, считается трафик за день и время в
  уведомлениях; системный пояс сервера не меняется.
- **Ключ выхода с непроверенным сертификатом.** `kaskadctl gw exit-preview --tofu` показывает отпечаток сертификата;
  `kaskadctl gw exit-add --pin-tofu <отпечаток>` добавляет ключ, только если отпечаток тот же. ИИ-агентам — только с
  подтверждением человека.
- **Переименование.** Людей, устройства, выходы, подписи протоколов, подписки выходов, пробросы и серверы мониторинга
  можно переименовать в панели, SSH-меню, `kaskadctl` и у ИИ-агентов. Меняется только имя: ключи, ссылки подписок и
  маршруты работают как прежде, выданные раньше настройки остаются со старым именем. Имя — до 40 знаков.
- **SSH-меню** печатает старые имена с управляющими знаками безопасно — эти знаки показываются пробелом.

*In English.* **3.3.7.1 — fixes of 3.3.7, the Hysteria2 inbound, exit groups in kaskadctl and the SSH menu, renaming**

- **No exit is turned on** (none added, all turned off or taken out by the quota), and someone on the server has the
  route “Direct”. Before, the server itself looked up the addresses of the sites of everyone else past the exit — the
  provider of the server saw their names. Now their traffic is blocked: for people with “Russian sites direct” Russian
  sites don’t open — neither by name nor by address (on VLESS and SOCKS5 they opened before; on the new Hysteria2 they
  are closed the same way). If there are exits but they are down, nothing changes.
- **“Russian sites direct” with a damaged list of the ⚠RU organisations.** If the list can’t be read, the consent is
  not saved — neither at the install nor in the SSH menu, the panel, the bot or by AI agents: Kaskad says it could not
  read the list of the ⚠RU organisations and suggests updating. The path of the file is in the log.
- **The feed of group events.** “All traffic goes direct” is no longer lost when a block was told minutes before. The
  summary of the window names the right step, the number of devices and the group name — after a restart of the
  collector too.
- **Server health:** the serious level is said the same way everywhere — “Serious problems”; “TCP retransmits” no
  longer flickers on a quiet server: with few retransmits the level does not rise, and a warning stays until the share
  drops below 1.5%.
- **The new Hysteria2 inbound (UDP).** Devices connect over Hysteria2: a `hysteria2://` link, a QR code and the
  subscription, as with VLESS. With a domain — its certificate; without one — the server’s own certificate with its
  fingerprint in the link. Salamander obfuscation and port hopping (a range of up to 100 ports) are on the inbound card,
  with a warning when there is no domain. Blocking of UDP in Russian mobile networks has not been checked — if it does
  not connect, VLESS (TCP). Routers don’t support Hysteria2 — use AmneziaWG or WireGuard for them. Adding a device does not
  restart the other connections.
- **Exit groups — in kaskadctl, the SSH menu and for AI agents.** `kaskadctl gw groups` shows the groups, the tiers and
  the devices without internet; an exit can be put on standby, restored, put into maintenance, given a quota, moved to
  another group; a group can be deleted. Before an irreversible change Kaskad shows what changes and asks with numbers.
  The route of a person or a device can point to a group. The SSH menu “Exits” has “State and groups” (view only) and
  “if the exits are down — as its group”. Groups are still set up in the panel.
- **Time zone.** Moscow by default; change it in the first-install wizard, in the SSH menu (11 → “Time zone”), with
  `kaskadctl tz set` or by AI agents. It sets when day quotas reset, the traffic of the day and the time in
  notifications; the system zone of the server stays.
- **An exit key with an unverified certificate.** `kaskadctl gw exit-preview --tofu` shows the fingerprint of the
  certificate; `kaskadctl gw exit-add --pin-tofu <fingerprint>` adds the key only if the fingerprint is the same. AI
  agents — only with the person’s confirmation.
- **Renaming.** People, devices, exits, protocol labels, exit subscriptions, port forwards and monitored servers can be
  renamed in the panel, the SSH menu, `kaskadctl` and by AI agents. Only the name changes: keys, subscription links and
  routes keep working, settings issued before keep the old name. A name is up to 40 characters.
- **The SSH menu** prints old names with control characters safely — those characters are shown as spaces.

**3.3.7 — проверка и группы выходов, пометка ⚠РФ, здоровье сервера**

- **Внутри — всё из 3.3.6:** мобильная версия панели, поиск по всем настройкам, «Внешний вид» как у goAmnezia Pro и
  исправление безопасности 3.3.5.1 (подробнее — в записях ниже).
- **Проверка выходов.** Kaskad постоянно проверяет каждый выход: отклик и потери, по желанию — скорость и то,
  открываются ли сайты (например, YouTube). По итогам выход «годен», «ухудшен», «в запасе» или «снят». Когда выход
  считать ухудшенным, уводить в запас или снимать, задают условия; у каждого — порог возврата и сколько минут выход
  должен продержаться в норме, чтобы вернуться. В наборах «Экономно», «Стандарт» и «Строго» нестабильный выход (3
  падения за 15 минут) уходит в запас на 15 минут, если есть другие выходы. У выхода можно задать квоту трафика: когда
  она исчерпана, выход снимается, уходит в запас или Kaskad только сообщает. В подробностях выхода — графики. Бот
  сообщает, когда выход ухудшился, ушёл в запас, снят или снова годен и когда устройства переехали на другой выход;
  что присылать — в «Настройки → Уведомления». Kaskad следит и за страной выхода: сменилась — сообщает. Проверки шлют
  меньше лишних пакетов, а сколько трафика уйдёт на них в сутки, видно в карточке группы.
- **Новые ключи выхода: Hysteria2, Shadowsocks, Trojan и VMess** — ссылкой (`hysteria2://`, `ss://`, `trojan://`,
  `vmess://`) или в подписке, как VLESS. Адрес сервера в ключе проверяется так же строго, как у VLESS и AmneziaWG в
  3.3.6: ключ, который указывает на этот сервер или служебные адреса хостера, не добавляется.
- **Группы выходов** — вкладка «Выходы → Группы». В группе выходы стоят по уровням: устройства идут на первый уровень,
  где есть годный выход, следующий уровень — запасной; когда первый снова годен 10 минут подряд (срок можно изменить),
  устройства возвращаются. Способ выбора на уровне — поровну, лучший отклик, по весам или по скорости. В карточке
  группы — наборы проверок «Экономно», «Стандарт» и «Строго» (или свой) и условия; в подробностях выхода — его
  состояние, квота и графики. Маршрут человека и устройства можно направить на группу, а не только на один выход; в
  карточке «Маршрут по умолчанию» кнопка «Что изменится» до сохранения показывает, какие устройства переедут. Новый
  сервер сразу работает по набору «Стандарт». Обновлённый — «как раньше» (без условий), с плашкой «Включить
  «Стандарт»?». Для группы «Основная» есть режим «по соединениям» (по умолчанию выключен): каждое соединение выбирает
  выход отдельно, и сайты могут видеть разные IP. Вкладка «Группы», наборы, условия и плашка «Включить «Стандарт»?»
  пока только на компьютере — на телефоне они появятся следующим выпуском.
- **Пометка ⚠РФ.** Ресурсы Meta (кроме WhatsApp) и ФБК во встроенном списке «всегда через выход» помечены ⚠РФ — ресурс
  признан экстремистским в РФ. Пометка видна в «Правилах», на схеме, в «Куда пойдёт трафик?», в SSH-меню, в MCP и в
  мастере установки, а бот присылает сообщение о ⚠РФ. «Россия напрямую» включается только с галочкой «Скрипт не
  используется на территории РФ»; галочка относится к показанным организациям — появится новая, Kaskad попросит
  подтвердить снова. Без галочки весь трафик идёт по маршруту, как при «всё через выход». На сервере, где «Россия
  напрямую» уже работала, всё остаётся как раньше: панель и SSH-меню показывают плашку с просьбой подтвердить, бот
  присылает одно сообщение.
- **Здоровье сервера.** На «Обзоре» (и на телефоне) — светофор «Сервер»: процессор, память, диск, сеть, службы шлюза,
  сертификаты, часы. В боте экран «💻 Система» начинается с того же светофора, и бот присылает сообщения о проблемах
  сервера. Лёгкие пробы сети (раз в ≈ 30 с по 3 пакета ping к трём точкам) показывают потери и отклик сети хостера: на
  новой установке они включены, на обновлённом сервере выключены — Kaskad подскажет в итоге обновления, как включить:
  пункт 22 SSH-меню «Нагрузка и сеть сервера» или `kaskadctl host set --probes on`. Сводка — `kaskadctl host health` и
  в MCP.
- **Что важно при обновлении:** всё работает как раньше, делать ничего не нужно. Если «Россия напрямую» включена,
  подтвердите галочку по плашке. Пробы сети включаются вручную (см. выше), набор «Стандарт» — кнопкой в «Выходы →
  Группы» на компьютере. Одно отличие: если выключены или сняты все выходы, имена сайтов больше не разрешаются мимо
  них — российские сайты по имени тогда не откроются и у людей с «Россия напрямую» (раньше открывались через VPN
  SOCKS5 и VLESS).
- **Следующим выпуском:** российские группы выходов, группы выходов на телефоне, переименование людей и устройств
  (выходы, группы и уровни переименовываются уже сейчас), вход Hysteria2 на этом сервере, «Настройки → Время», тест
  сервера с баллом и звездой, расписание его проверок и «Поделиться» результатом.

*In English.* **3.3.7 — exit checks and exit groups, the ⚠RU mark, server health**

- **Includes everything of 3.3.6:** the mobile version of the panel, search across every setting, Appearance as in
  goAmnezia Pro and the security fix of 3.3.5.1 (see the entries below).
- **Exit checks.** Kaskad keeps checking every exit: response time and losses, and if you like, speed and whether
  sites open (YouTube, for example). As a result an exit is “healthy”, “degraded”, “standby” or “out”. Conditions set
  when an exit counts as degraded, goes to standby or is taken out; each has a return threshold and the minutes the
  exit has to stay fine to come back. In the “Economy”, “Standard” and “Strict” sets an unstable exit (3 failures in
  15 minutes) goes to standby for 15 minutes if there are other exits. An exit can have a traffic quota: when it is
  used up, the exit is taken out, goes to standby, or Kaskad only notifies. The details of an exit show charts. The
  bot tells you when an exit got degraded, went to standby, was taken out or is healthy again, and when devices moved
  to another exit; what it sends is set in Settings → Notifications. Kaskad also watches the country of an exit and
  tells you when it changes. The checks send fewer needless packets, and the card of a group shows how much traffic
  they take a day.
- **New exit keys: Hysteria2, Shadowsocks, Trojan and VMess** — as a link (`hysteria2://`, `ss://`, `trojan://`,
  `vmess://`) or in a subscription, like VLESS. The server address in the key is checked as strictly as for VLESS and
  AmneziaWG in 3.3.6: a key pointing to this server or to the hoster’s service addresses is not added.
- **Exit groups** — the Exits → Groups tab. In a group, exits stand on levels: devices go to the first level with a
  healthy exit, the next level is the backup one; when the first is healthy for 10 minutes in a row (the time can be
  changed), devices come back. The way of picking on a level is evenly, best response, by weight or by speed. The card
  of a group has the check sets “Economy”, “Standard” and “Strict” (or a custom one) and conditions; the details of an
  exit show its state, quota and charts. The route of a person and of a device can point to a group, not only to one
  exit; in the “Default route” card, “What will change” shows which devices will move before you save. A new server
  works with the “Standard” set at once. An updated one works “as before” (no conditions), with the banner “Turn on
  “Standard”?”. The “Main” group has the “Per connection” mode (off by default): each connection picks an exit on its
  own, and sites may see different IPs. The Groups tab, the sets, the conditions and the “Turn on “Standard”?” banner
  are on a computer only for now — on a phone they come in the next release.
- **The ⚠RU mark.** Meta resources (except WhatsApp) and FBK in the built-in “always via an exit” list are marked ⚠RU
  — recognised as extremist in Russia. The mark is shown in the routing rules, on the scheme, in “Where will the
  traffic go?”, in the SSH menu, in MCP and in the setup wizard, and the bot sends a message about ⚠RU. “Russian sites
  direct” turns on only with the tick “The script is not used in the Russian Federation”; the tick covers the
  organizations shown — when a new one appears, Kaskad asks to confirm again. Without the tick all traffic follows the
  route, as with “everything via an exit”. On a server where “Russian sites direct” already worked, everything stays
  as before: the panel and the SSH menu show a banner asking to confirm, and the bot sends one message.
- **Server health.** The Overview (on a phone too) has the “Server” light: CPU, memory, disk, network, the gateway
  services, certificates, the clock. In the bot the “💻 System” screen starts with the same light, and the bot sends
  messages about server problems. Light network probes (every ≈ 30 s, 3 ping packets to three points) show the losses
  and response time of the hoster’s network: on a new install they are on, on an updated server they are off — the
  summary of the update tells you how to turn them on: item 22 of the SSH menu “Server load & network” or `kaskadctl
  host set --probes on`. The summary is in `kaskadctl host health` and in MCP.
- **What matters when updating:** everything works as before, nothing needs to be done. If “Russian sites direct” is
  on, confirm the tick from the banner. Network probes are turned on by hand (see above), the “Standard” set — by the
  button in Exits → Groups on a computer. One difference: when all exits are off or removed, site names are no longer
  resolved around them — Russian sites by name then don’t open even for people with “Russian sites direct” (they used
  to open over SOCKS5 and VLESS).
- **In the next release:** Russian exit groups, exit groups on a phone, renaming people and devices (exits, groups and
  levels can be renamed already), a Hysteria2 inbound on this server, Settings → Time, the server test with a score
  and a star, a schedule of its checks and sharing its result.

**3.3.6 — панель на телефоне, поиск по всем настройкам, «Внешний вид» как у goAmnezia Pro**

- **Уже содержит исправление безопасности 3.3.5.1:** адрес и порт ключа в «Прокси для API Telegram» всегда
  проверяются, имена ключей в SSH-меню — без служебных символов терминала (подробнее — в записи 3.3.5.1 ниже).
  Перезагружать сервер после обновления на 3.3.6 не нужно — даже если после обновления с 3.3.5 или раньше его так и
  не перезагрузили: подключения AmneziaWG, поднятые прежней версией, Kaskad сам пересобирает по новым правилам.
- **Ключи AmneziaWG проверяются строже.** Адрес клиента в ключе (Address) — только из частных сетей (10.x,
  172.16–172.31.x, 192.168.x, 100.64–100.127.x, fc…/fd…) и не из сетей, маршрутов и DNS этого сервера и его шлюза.
  Адрес сервера (Endpoint) — не сам этот сервер: имя из ключа Kaskad проверяет сам и подключается по найденному IP.
  Такой ключ не добавляется: меню, панель и подписка говорят «ключ не подходит, попросите новый». Если он уже был
  в пуле, после обновления его подключение снимается. Ключ WARP с двумя строками Address (IPv4 и IPv6) теперь берёт
  адрес IPv4 и встаёт и там, где уже работает WARP goTelegram или goWARP. Ключ WARP, добавленный в 3.3.5 и снятый
  после обновления, — добавьте тот же файл .conf заново: новая версия возьмёт из него адрес IPv4. Ключ устройства
  другого сервера Kaskad годится как выход, если шлюзы двух серверов на разных сетях. По умолчанию оба берут
  10.77.0.0/16 — тогда Kaskad говорит «адрес клиента 10.77.0.10 попадает в сеть шлюза этого сервера 10.77.0.0/16»:
  переведите шлюз одного из серверов на другую сеть (в панели: Настройки › Шлюз › Сеть шлюза — там видна
  свободная сеть, например 10.78.0.0/16; устройства этого шлюза получат новые адреса, их ключи придётся выдать
  заново — выбирайте сервер, где устройств меньше). Имя сервера из ключа Kaskad
  разрешает, когда поднимает подключение (и при каждом его перезапуске): не разрешилось — подключение закрыто, трафик
  этого выхода не идёт мимо него; Kaskad пробует снова (первый час — раз в минуту, потом раз в 10 минут) и, когда DNS
  ответит, поднимает подключение сам (и на сервере, где включён только бот). Адрес сервера VLESS и Endpoint
  AmneziaWG тоже не могут указывать на этот сервер или служебные адреса хостера: 127.0.0.1, localhost, адрес
  метаданных 169.254.169.254, имя без точки (например, `vpn`), имена в зонах `.internal` и `.localhost.localdomain` —
  в том числе с точкой в конце (`localhost.`). Такой ключ, уже лежавший в пуле, после обновления снимается.
- **Новая мобильная версия панели.** На телефоне панель теперь своя, а не уменьшенная компьютерная: вкладки внизу
  экрана, остальные разделы — на странице «Ещё», списки людей, выходов и пробросов — строками, подробности — во весь
  экран, меню «⋯» и действия — листом снизу. Кнопка «Поделиться» у человека собирает в одном месте его страницу,
  подписку и QR или файл для устройства. На планшете в «Людях» список и подробности стоят рядом.
- **Поиск по всей панели.** Поле поиска — в шапке (на телефоне — под заголовком), открывается и с клавиатуры: ⌘K на
  Mac, Ctrl+K на Windows и Linux, «/» вне полей ввода. Ищет не только разделы, а каждую настройку и действие —
  по названию, подсказке и похожим словам на русском и английском («пароль» → «Настройки → Доступ к панели →
  Пароль панели»), а также людей, устройства, выходы, протоколы и пробросы («порт 443»). У каждого результата виден
  путь; выбор открывает нужное место и на пару секунд подсвечивает его. Последние выборы — в «Недавних».
- **«Внешний вид» — как у goAmnezia Pro.** Готовые темы, акцент, тема, оттенок фона, карточки, кнопки, скругление,
  меню, размер текста, подробность, яркие статусы, анимации и язык — в том же порядке, что у goAmnezia Pro, и
  одинаково в шторке, на телефоне и в «Настройках». Акцент «Каскад» — мятный в тёмной теме и фиолетовый в светлой;
  лишний двойной кружок в тёмной теме убран.
- **Мелкие исправления:**
  - на телефоне касание корзины другого выхода, пока открыт вопрос «Удалить?», открывает его вопрос с первого раза, и
    список под пальцем не прыгает;
  - после Esc, Enter или удаления курсор клавиатуры остаётся рядом — на кнопке или строке, а не в начале страницы;
  - подсветка найденного видна и в тёмной теме;
  - «Только этот выход выбран в маршруте: устройства «a», «b»» — имена одной строкой, без повтора слова;
  - в «Прокси для API Telegram» ключ назван своей подписью («Мой выход»), а не именем из строки подписки
    («3 GB left»);
  - удаление выхода в SSH-меню записывается в журнал, как в панели;
  - ключ, добавленный дважды: если при удалении копии маршруты не перешли из-за сбоя шлюза, Kaskad больше не советует
    «повторите», а говорит, что повтор не поможет, и предлагает сначала проверить шлюз.

*In English.* **3.3.6 — the panel on a phone, search across every setting, Appearance as in goAmnezia Pro**

- **Includes the security fix of 3.3.5.1:** the address and port of a key in “Telegram API proxy” are always
  checked, and key names in the SSH menu come without terminal control characters (see 3.3.5.1 below). No reboot
  is needed after updating to 3.3.6, even if the server was never rebooted after updating from 3.3.5 or earlier:
  Kaskad itself rebuilds the AmneziaWG connections brought up by the previous version under the new rules.
- **AmneziaWG keys are checked more strictly.** The client address in a key (Address) must be in a private network
  (10.x, 172.16–172.31.x, 192.168.x, 100.64–100.127.x, fc…/fd…) and outside the networks, routes and DNS servers of
  this server and its gateway. The server address (Endpoint) must not be this server itself: Kaskad checks the name
  from the key itself and connects to the IP it found. Such a key is not added: the menu, the panel and the
  subscription say “the key won’t do, ask for a new one”. If it was already in the pool, its connection is removed
  after the update. A WARP key with two Address lines (IPv4 and IPv6) now takes the IPv4 address and works even where
  the WARP of goTelegram or goWARP already runs. A WARP key added in 3.3.5 and removed after the update: add the same
  .conf file again — the new version takes its IPv4 address. A key of a device of another Kaskad server works as an
  exit when the gateways of the two servers are on different networks. By default both take 10.77.0.0/16 — then
  Kaskad says “the client address 10.77.0.10 falls within the gateway network of this server 10.77.0.0/16”: move
  the gateway of one of the servers to another network (in the panel: Settings › Gateway › Gateway network — it
  shows a free network, for example 10.78.0.0/16; the devices of that gateway get new addresses and their keys
  have to be issued again — pick the server with fewer devices). Kaskad resolves the server name
  from the key when it brings the connection up (and on each of its restarts): if the name does not resolve, the
  connection stays closed, and the traffic of this exit does not go around it; Kaskad tries again (the first hour once
  a minute, then once every 10 minutes) and brings the connection up by itself when DNS answers (on a server with
  only the bot too). The address of a VLESS server and an AmneziaWG Endpoint cannot point to this server or to the
  hoster’s service addresses either: 127.0.0.1, localhost, the metadata address 169.254.169.254, a name without a dot
  (for example, `vpn`), names in the `.internal` and `.localhost.localdomain` zones — with a trailing dot too
  (`localhost.`). Such a key already in the pool is removed after the update.
- **A new mobile version of the panel.** On a phone the panel has its own layout instead of a shrunk desktop one:
  tabs at the bottom, the other sections on the “More” page, people, exits and forwards as rows, details full
  screen, “⋯” menus and actions as a bottom sheet. “Share” on a person gathers their page, the subscription and a QR
  code or file for one device in one place. On a tablet, People shows the list and the details side by side.
- **Search across the whole panel.** The search field is in the header (on a phone — under the title); from the
  keyboard: ⌘K on a Mac, Ctrl+K on Windows and Linux, “/” outside text fields. It finds not only sections but every
  setting and action — by name, hint and similar words in English and Russian (“password” → “Settings → Panel
  access → Panel password”) — and people, devices, exits, protocols and forwards (“port 443”). Every result shows its
  path; picking one opens the place and highlights it for a couple of seconds. Recent picks are under “Recent”.
- **Appearance as in goAmnezia Pro.** Preset themes, accent, theme, background tint, cards, buttons, corner radius,
  menu, text size, detail, vivid statuses, animations and language — in the same order as in goAmnezia Pro, and the
  same in the drawer, on a phone and in Settings. The “Kaskad” accent is mint in the dark theme and violet in the
  light one; the extra double swatch of the dark theme is gone.
- **Small fixes:**
  - on a phone, tapping the trash button of another exit while “Delete?” is open opens its question on the first tap,
    and the list under the finger does not jump;
  - after Esc, Enter or a delete, the keyboard focus stays close — on the button or the row, not at the top of the page;
  - the highlight of a search result is visible in the dark theme too;
  - “This is the only exit in the route for: …” — the names in one line, without repeating the word;
  - in “Telegram API proxy” a key is called by its own label (“My exit”), not by the name from the subscription line
    (“3 GB left”);
  - deleting an exit in the SSH menu is written to the log, as in the panel;
  - a key added twice: when deleting the copy fails because the gateway could not move the routes, Kaskad no longer
    says “try again” — it says a retry will not help and suggests checking the gateway first.

**3.3.5.1 — исправление безопасности: всем, у кого 3.3.5, — обновиться**

- Исправлена проверка адреса ключа в меню «Прокси для API Telegram» (17 → 3): адрес и порт ключа теперь всегда
  проверяются. Ключ, у которого адрес или порт сервера не разобран, не добавляется — меню, панель и подписка так и
  говорят. Если такой ключ уже есть в пуле, он не работает и помечен — удалите его и добавьте ссылку заново.
- Имена ключей в SSH-меню показываются без служебных символов терминала.
- После обновления перезагрузите сервер (`reboot`): так уже поднятые подключения AmneziaWG пересоберутся по новым правилам.

*In English.* **3.3.5.1 — a security fix: everyone on 3.3.5 should update**

- Fixed the check of a key’s address in the “Telegram API proxy” menu (17 → 3): the address and port of a key are now
  always checked. A key whose server address or port can’t be read is not added — the menu, the panel and the
  subscription say so. If such a key is already in the pool, it doesn’t work and is marked — remove it and add the
  link again.
- Key names in the SSH menu are shown without terminal control characters.
- After the update, reboot the server (`reboot`) so that running AmneziaWG connections are rebuilt under the new rules.

**3.3.5 — смена имени ключа в подписке больше не отвязывает устройства, выход не «мигает» в «Событиях»**

- **Выход сохраняется, когда подписка переименовала ключ:** многие провайдеры пишут остаток трафика или срок в имя
  ключа («10GB left» → «9GB left»), и раньше каждое обновление подписки создавало новый выход: устройства и люди с
  маршрутом на прежний выход оставались без связи, пока маршрут не перепривязывали руками. Теперь ключ узнаётся по
  серверу и учётной записи (у VLESS — uuid, адрес и порт; у AmneziaWG — ключ сервера и ключ клиента): новая
  строка встаёт на место старой, id выхода, маршруты устройств и людей, подпись и выключатель выхода остаются
  прежними, а имя показывается новое — из подписки. Итог обновления: «обновлено 1 … маршруты устройств
  сохранены». Если у ключа другой uuid или другой сервер — это новый выход, как раньше. Id уже существующих
  выходов при обновлении до 3.3.5 не меняются. Ключ, удалённый руками, подписка не возвращает и под новым именем,
  если его удалили после первого обновления этой подписки на 3.3.5; удалённый раньше не возвращается, пока подписка
  отдаёт его той же строкой (с тем же именем и адресом).
- **Выход больше не «мигает» в «Событиях»:** выход считается упавшим, только если за последние 2 минуты не прошли
  не меньше половины проверок (минимум 4 проверки) — одинаково для VLESS и AmneziaWG. Одна потерянная проба или
  короткая серия больше не отрезает закреплённые устройства на 10–25 секунд. В окне — одна проверка на 10 секунд:
  перепроверка через 5 секунд заменяет результат, а проба VLESS считается один раз, сколько бы раз её ни прочитали.
  Полный отказ AmneziaWG замечается примерно за минуту, VLESS — за 1–1,5 минуты. Выход возвращается после двух удачных проверок подряд. Если xray узла
  молчит минуту или минуту не проверяет выход заново, выход «не работает» сразу. Пока проверок меньше четырёх, а не
  прошла половина из них, выход «проверяется», а не «работает». В журнале у VLESS теперь видна причина от xray
  (задержка и ошибка пробы). В ленте и в боте повторы одного вида на выход глушатся на 15 минут; если к концу этого
  окна состояние не то, что в ленте, приходит текущее — со счётчиком («не работает (обрывов за 15 мин: 3)»).
  Перезапуск сборщика и перезагрузка узла это не сбивают: если лента говорит не то, что сейчас, сразу приходит текущее
  состояние. Состояние в «Выходах» и `gw exits` показывается без задержки.
- **Предупреждения — только о своих ключах:** `gw exit-add` говорит о добавленных ключах, `gw exit-del` и
  обновление подписки — о своих. Остальные ключи пула с предупреждениями — одной строкой «Ещё в пуле ключей с
  предупреждениями: N — подробности в списке выходов».
- **Один ключ AmneziaWG — один выход, по любому адресу:** ключ узнаётся по ключу сервера и ключу клиента, без
  адреса. Тот же ключ по IP и по домену больше не добавляется второй раз (два подключения с одним ключом мешали друг
  другу: потери 25–100 %) — ни вручную, ни подпиской, ни импортом с заменой пула. Kaskad отвечает «этот ключ уже
  есть как e-… («имя»), по другому адресу». И наоборот: ключи разных людей одного сервера теперь — разные выходы
  (раньше второй считался дублем). Подписка, которая сменила адрес ключа (IP → домен), сохраняет выход и его
  маршруты. Сервер, восстановленный из резервной копии другого (с теми же ключами), Kaskad тоже считает тем же
  ключом — об этом говорит причина отказа.
- **Ключ, добавленный дважды до 3.3.5** (по IP и по домену): `gw exits`, панель и «Поиск неполадок» (пункт 21) говорят «Ключ «…»
  добавлен дважды: по IP и по домену. Работать будет только один — удалите лишний, маршруты перейдут на оставшийся».
  Kaskad сам ничего не удаляет: обновление подписки оставляет обе копии с их id и маршрутами. Когда вы удаляете лишнюю
  (панель, меню, мастер, `gw exit-del`, MCP), подтверждение называет оставшуюся, маршруты устройств и людей переходят
  на неё, а подписка удалённую копию больше не возвращает. Не удалось перевести маршруты (или оставшаяся копия
  выключена для шлюза) — удаление отменяется, Kaskad говорит почему.
- **Дубли подписки названы:** если ключ подписки уже есть в пуле другой строкой (такой же ключ goAmnezia добавлен
  руками или пришёл другой подпиской — по IP вместо домена; или подписка сама отдаёт его дважды), итог говорит
  «Пропущено дублей: N — этот ключ уже есть как e-… («имя»), по другому адресу» (или «записан иначе»), строка
  подписки — «пропущено дублей: N». Раньше было только «добавлено 1» без причины.
- **Меню 11:** после обновления шапка открытого меню говорит «обновлено до … — откройте меню заново», а не «есть …:
  11»; если новая версия сама сменила ветку, журнал пишет «Самообновление -> v… (test → dev, …)». Это работает
  начиная с обновления с 3.3.5 на следующие версии: обновление на 3.3.5 ещё проводит прежняя версия меню, поэтому
  после него шапка может показать «есть 3.3.5: 11» — просто откройте меню заново.
- Резервная копия переносит сохранённые id выходов вместе с подписками. Копия 3.3.5 открывается в 3.3.4, но
  переименованные подпиской ключи там получат новые id — маршруты на них придётся перепривязать.

**3.3.4 — сборки Kaskad 3 переехали в ветку `dev`, поддомен в мастере необязателен, xray закреплён на проверенной версии**

- **Ветка обновлений — `dev`:** ветка `k3` заморожена на 3.3.3, новые сборки выходят в `dev`. Сервер, у которого
  записана `KASKAD_BRANCH="k3"`, один раз сам переходит на `dev` — при обновлении или при первом запуске меню — и
  пишет «Ветка обновлений: k3 → dev». Старая команда `KASKAD_BRANCH=k3 ./install.sh` теперь тоже записывает `dev`.
  Меню 11 без записанной ветки идёт `main` → `test` → `dev`.
- **Поддомен в мастере необязателен:** поле подписано «Поддомен (необязательно)». Пусто — адресом станет сам домен,
  A-запись — «@». Мастер предупредит: если на самом домене уже работает сайт, он перестанет открываться — тогда
  лучше вписать поддомен.
- **xray закреплён на версии 26.3.27:** архив сверяется по sha256. Kaskad больше не скачивает «последнюю» версию и
  не берёт чужой xray с сервера (3x-ui, x-ui). При обновлении старый xray меняется только после проверки текущих
  настроек, прежний остаётся рядом для отката.
- **Ключи VLESS с `allowInsecure` больше не ломают добавление:** ключ с отпечатком сертификата (`pcs`) работает, без
  отпечатка — пропускается один, с понятной причиной; остальные ключи и подписка применяются («добавлено N,
  пропущено M»).
- **Выходы с самоподписанным сертификатом продолжают работать:** отпечаток закрепляется автоматически при
  обновлении. Для ключей с `allowInsecure`, которые уже были в пуле, Kaskad один раз проверяет сертификат их
  сервера. Настоящий сертификат (например, Let's Encrypt) — дальше обычная проверка, продление его не ломает;
  если он выписан на другое имя (ключ по IP, сертификат на домен), проверка идёт по имени из сертификата.
  Самоподписанный или другой, который не проходит проверку, — закрепляется его отпечаток. Это строже прежнего:
  раньше подходил любой сертификат; если закреплённый сертификат на сервере потом сменят, ключ нужно выдать
  заново, с отпечатком (`pcs`). Если сервер в момент обновления не ответил, ключ остаётся в пуле с
  предупреждением, но выход пока не работает; Kaskad сам пробует снова примерно раз в час. Через 7 дней без
  ответа повторы прекращаются — ключ нужно выдать заново, с отпечатком (`pcs`).
  Резервная копия: ключи с `allowInsecure` без отпечатка из копии старой версии на уже работающем узле 3.3.4
  принимаются, только если этот узел уже запомнил сертификат их сервера; остальные нужно выдать заново: с
  отпечатком (`pcs`) или, если у сервера настоящий сертификат, без `allowInsecure`. Ключ, который ещё ждёт
  ответа сервера, на другом узле из копии тоже не заработает. Копия 3.3.4, в которой Kaskad запомнил
  сертификаты серверов (отпечатки или отметки «сертификат настоящий»), в 3.3.3 не открывается; остальные копии
  3.3.4 открываются. Для отката на 3.3.3 держите копию, снятую до обновления.
- **Keenetic:** в подсказках L2TP/IPsec стоит выше IKEv2; IKEv2 помечен «не проверено» — нужен сертификат центра
  Kaskad.

**3.3.3 — мастер и панель проще и понятнее новичку, QR AmneziaWG 3.1 снова сканируется**

- **Домен в мастере — 4 шага с автопроверкой:** номер шага сам становится галочкой, когда имя записано верно и DNS
  отвечает; сертификат выпускается кнопкой, когда DNS готов. «Купить домен» ведёт на AdminVPS с подсказкой, где
  раздел «Домены»; если Let's Encrypt отказал — «Дальше без домена». В примерах и подсказках — просто «домен» и example.com, без зоны .ru.
- **Выходы — одно поле** для ключа и для подписки, видно сразу, с подсказкой форматов: вставили — мастер узнаёт ключ
  («ключ VLESS · имя») или подписку, «Добавить», строка в «Добавлено», × удаляет. Тот же ключ второй раз не добавится.
  Отдельной карточки «Автообновление подписки» больше нет. Нет своего сервера за рубежом — строка «Нет сервера за
  рубежом?» с хостингами AdminVPS (РФ и Европа) и Cloud VPS (Беларусь); так же в панели, в разделе «Выходы».
- **Порты для хостера — в итоге мастера:** «Откройте порты у хостера» с настоящими портами сервера и «Скопировать
  списком». Порт протокола занят — мастер говорит, какой порт и чем занят, даёт команду для SSH, «Установить снова» и
  «Не ставить».
- **Карточки «Протоколы» в панели:** одной высоты в ряду; у неработающего протокола — причина («служба остановлена»…),
  «Перезапустить» и «Журнал» (последние строки службы, «Скопировать»). Если служба общая с другими протоколами,
  панель предупредит, что их подключения тоже оборвутся на несколько секунд.
- **Telegram-бот и ключ ИИ-агента — по шагам**, одинаково в мастере (этап 5) и в панели («Настройки → Бот и
  ИИ-агент»): токен бота проверяется сам, «Отправить тест»; для агента — одна команда на своём компьютере, вставить
  строку, подключить Claude Code, Claude Desktop или Cursor; статус меняется сам, когда агент подключился.
- **«Поделиться ▾» у человека:** страница, ссылка для приложений (подписка) и «Выдать настройки» — ссылка сразу в меню
  с «Скопировать».
- **Вёрстка панели на телефоне и ноутбуке:** «Настройки», «Пробросы», шапка с длинным адресом, подписи схемы — без
  наездов и обрезок; тур по панели не уезжает за край экрана.
- **QR ключа AmneziaWG 3.1 — не больше 2 частей** (как ждёт приложение Amnezia), рамка крупнее, части сменяются раз
  в 2 секунды. На ноутбуке код помещается на экран целиком. Где ключ в один QR не влезает (`.conf` 3.1), окно даёт
  файл и QR страницы человека. Имя ключа — без платформы: «goKaskad PRO · AmneziaWG 3.1 · Мой телефон».
- **English без русских строк:** ход установки протоколов в мастере, лента событий «Overview» и всплывающие
  уведомления панели (в том числе события, случившиеся до смены языка) — по-английски.

**3.3.2 — новое окно «Выдать настройки» для 14 платформ, русский и английский везде, тёмная тема, мастер короче**

- **Два языка во всём:** установщик, SSH-меню, `kaskadctl`, мастер быстрого старта, веб-панель, страница человека,
  Telegram-бот и его меню команд — по-русски и по-английски полностью. Язык выбирается первым экраном при первой
  установке (цифрами; `KASKAD_LANG=ru|en` в окружении — без вопроса) и запоминается в `/etc/kaskad/config`. Сменить —
  в любой момент: меню 11 «Обновить · Язык», «Настройки» панели, переключатель в шапке мастера, «Язык / Language» в
  боте, `kaskadctl lang set ru|en` — меняется сразу везде. Страница человека `/p/…` — по умолчанию на языке сервера,
  у посетителя свой переключатель. Журнал `/var/log/kaskad.log` — на языке сервера.
- **Тёмная тема по умолчанию** в мастере, панели и на странице человека; светлая — кнопкой.
- **Мастер короче:** на каждом этапе — вопрос, выбор и «Дальше»; подробности — в «Подробнее» и видеоинструкциях.
  Раскладка не прыгает после загрузки списков; у L2TP/IPsec в итоге — его порты; почту для Let's Encrypt можно задать
  на этапе домена.
- **Новое окно «Выдать настройки»** — одно и то же в панели, на странице человека и в шаге 6 мастера: шаг 1
  «Установите приложение» (логотипы, «рекомендуем», ссылка в магазин и «QR · установить»), шаг 2 «Добавьте ключ»,
  шаг 3 «Включите VPN». На экране всегда один QR: QR магазина открывается карточкой, ключ на это время закрыт.
  Формат ключа задаёт выбранное приложение (`vpn://`, `.conf`, `vless://`). Ключ AmneziaWG 3.1 — частями в одной
  рамке, части сменяются сами раз в 2 секунды. Условия и ограничения — оранжевым треугольником с подсказкой и
  ссылками на документацию. На телефоне QR свёрнут, сверху «Открыть в магазине» и «Скопировать ключ».
- **14 платформ × 12 протоколов:** iPhone и iPad, Android, Android TV, Windows, macOS, Linux, роутеры Keenetic,
  MikroTik, OpenWrt, TP-Link, Huawei, Xiaomi, ASUS и «Другое». Для каждой пары — что ставить, с какой версии
  прошивки и где инструкция. Если штатно нельзя — окно подсказывает обходной путь (например, MikroTik + VLESS или
  AmneziaWG через контейнер) или протоколы, которые подойдут, с кнопками «Добавить устройству» и «Включить на
  сервере»; человеку на его странице — «Попросите администратора…». Каталог приложений проверен по российским
  App Store и Google Play: прямые ссылки, предупреждение о подделках.
- **Имена ключей:** в приложении — «goKaskad PRO · AmneziaWG 3.1 · Телефон мамы», у VLESS — после `#`; файлы —
  `goKaskadPro-<имя>.conf`, на Android и Linux — `gKP-<имя>` (не длиннее 15 знаков). Выданные раньше ключи не меняются.
- **Плашки протоколов у устройства** открывают выдачу сразу с этим протоколом; «Как у человека» показывает имя
  человека и действующий маршрут.
- **Знакомство с панелью при первом входе:** короткий тур поверх настоящей панели и схема пути трафика (из мастера их
  убрали).
- **Домен в шапке панели**, если он привязан (IP — подсказкой), крошки «домен › раздел».
- **SSH-меню:** явная `KASKAD_BRANCH=…` в команде установки записывает ветку обновлений; пункты 18 → 3 и 18 → 4
  спрашивают подтверждение цифрами и говорят «Не изменено», если отказались.
- **Обмен меню ↔ ядро — машинный:** меню, мастер и бот решают по кодам и полям `--json` и меткам `@K3:…`
  (`K3_MARKS=1`), а не по словам ответа — поэтому работают одинаково на любом языке.

**3.3.1 — доводка быстрого старта после живой проверки**

- **Смена домена в мастере:** после переключения мастер показывает плашку «Внимание» — выданные раньше ссылки VLESS
  (и настройки протоколов на сертификате домена) не подключатся. Кнопка «Выдать заново» ведёт на этап «Готово» и сразу
  выдаёт новые настройки устройству мастера тем же входом; остальные устройства — панель → «Люди и устройства».
  Плашка держится и после переезда мастера на новый домен.
- Предупреждения, которые сервер возвращал молча, теперь видны и на других шагах: сайт-маскировка, домен без
  сертификата, файл AmneziaWG, ключ выхода (предел ключей, «AWG-ядро недоступно»).
- **Неудачная проверка нового имени** больше не подменяет рабочий домен: после перезагрузки в поле — рабочий домен,
  а попытка — подсказкой «Последняя проверка: … — нет A-записи» с кнопкой «Проверить снова».
- **`backup undo`:** журнал пишет «BACKUP undo: возвращено — …» и только то, что вернулось (как «Возвращаю: …» в
  консоли); итог в панели — «Возвращено: …», а не «Перенесено: … бот, ключи агентов, пробросы».

**3.3.0 — быстрый старт в браузере, VLESS Reality, подписки выходов, копия настроек, новый вид панели**

- **Быстрая настройка в браузере** (пункт 20 и выбор после первой установки): одноразовая ссылка и QR, сертификат
  Let's Encrypt прямо на IP сервера (если свободен порт 80), шаги «пароль → перенос → протоколы → выходы → домен →
  дополнительно → первое устройство». Описание — [docs/k3/SPEC-setup.md](docs/k3/SPEC-setup.md). В этой сборке —
  ядро мастера и API (`/api/setup/*`); страница мастера по дизайну приходит отдельной веткой (`k3-qsui`).
- **Шапка главного SSH-меню** — что с сервером: IP и пробросы, домен и сертификат, адрес панели (маской, F1 —
  целиком на 5 минут), входы шлюза значками, живые выходы, первая неполадка. Пункт 21 «Поиск неполадок» — все неполадки
  с советом и переходом цифрой. [docs/k3/SPEC-ssh-menu.md](docs/k3/SPEC-ssh-menu.md).
- **Вход VLESS Reality** (меню 17 → Протоколы, панель, бот, MCP): ссылки `vless://` для v2RayTun, Hiddify,
  Streisand; сайт маскировки из списка или свой, проверка TLS 1.3 с узла, новые ключи узла.
  [docs/k3/SPEC-vless-inbound.md](docs/k3/SPEC-vless-inbound.md).
- **Подписки выходов** (17 → 3 → «Подписки выходов», панель → «Выходы»): ключи пула по ссылке VPN-сервиса,
  обновление раз в сутки; меняются только ключи подписки, ручные не трогаются. Понимает `amneziawg://`.
- **Копия настроек файлом** (16 «Движок и перенос» → 6, панель → «Настройки»): зашифрованный паролем файл `.kbk`
  со шлюзом, выходами, пробросами, доменом и ботом — перенос на другой сервер и «Вернуть как было».
  [docs/k3/SPEC-subs-backup.md](docs/k3/SPEC-subs-backup.md).
- **Новый вид веб-панели** — дизайн-система серии go: шрифты Onest, Rubik и JetBrains Mono (свои файлы, без
  внешних адресов), одна система цветов для тёмной и светлой темы, текст на акценте подбирается по контрасту.
- Кнопки по ролям (основная — одна на карточку, опасные действия — мягкой красной кнопкой со словом действия),
  статусы — точкой в своей колонке, информационные сообщения — нейтральные, таблицы «подпись — значение» одной
  сеткой с копированием значком, «Опасная зона» у карточек. «Выходы» — ровные плитки с действиями в одну строку,
  «Вид» в «Настройках» — с предпросмотром. Описание — [docs/k3/DESIGN-SYSTEM.md](docs/k3/DESIGN-SYSTEM.md).

**3.0.1 — пробросы на nftables**

- Таблица nftables `kaskad` вместо россыпи правил iptables. Проброс срабатывает только для адресов самого сервера: транзитный трафик (Docker, клиенты шлюза) он не перехватывает.
- У каждого проброса виден трафик (меню 5 «Правила»). Занятый порт (SSH, служба сервера, Docker) — предупреждение и вопрос.
- Переезд с бесплатного Kaskad и Kaskad PRO 2.x: предпросмотр, резервная копия в `/var/backups/kaskad`, проверка после переключения, автоматический откат.
- Сторож раз в минуту возвращает таблицу, если правила сбросили (`iptables -F`, `nft flush ruleset`, `ufw reload`).
- Меню 16 «Движок и перенос»: перевести на nftables, вернуть iptables, резервные копии, копия настроек файлом (перенос на другой сервер), вернуться на Kaskad 2.13.
- Службы запускают копию программы в `/opt/kaskad` (бесплатный скрипт поверх их не ломает); обновление идёт по ветке и не понижает версию; `iptables-persistent` на Debian 12+ и Ubuntu 24.04+ не ставится (он удаляет ufw); `ip_forward` и BBR — в `/etc/sysctl.d/90-kaskad-tuning.conf`.

**3.1.0 — шлюз (меню 17)**

Шлюз — переводчик протоколов: телефон, компьютер или роутер подключается к вашему серверу по привычному протоколу, а в интернет выходит через выходы пула «Прокси для API Telegram» (ключи VLESS и AmneziaWG) или напрямую.

- 11 входов: WireGuard, AmneziaWG 1.5 / 2.0 / 3.1, L2TP/IPsec, IKEv2, SSTP, PPTP, OpenVPN, OpenConnect, SOCKS5. Ставятся по выбору; мастер первой настройки отмечает WireGuard, AmneziaWG и L2TP/IPsec.
- Пользователи и устройства; настройки выдаются под систему устройства: QR, ссылка `vpn://`, файлы `.conf` и `.ovpn`, профиль `.mobileconfig`, скрипт PowerShell, поля для ручного ввода.
- Маршруты для всех, пользователя или устройства: пул выходов, один выход, напрямую, блок. Нет живого выхода — интернета нет, а не «напрямую».
- Клиенты шлюза не видят друг друга и частные сети сервера; IPv6 внутрь не пропускается; если правила шлюза пропали, трафик клиентов не уходит напрямую.
- Также: на свежем сервере Kaskad ждёт, пока система закончит обновления (apt занят), и говорит об этом; выходы AmneziaWG переживают перезапуск systemd-networkd.

### Системы

- **Debian 12 и 13, Ubuntu 24.04 и 26.04 (и новее)** — движок nftables и шлюз.
- Остальные системы (старее Debian 12 и Ubuntu 24.04, CentOS, Fedora) — как в 2.13: пробросы на iptables, шлюза нет.

### Требования к серверу

**Типовой случай — каскад на 10–15 устройств** (узел в России → выход за рубежом):

| | Узел в России (Kaskad) | Выход за рубежом |
|---|---|---|
| Процессор | 2 vCPU | 1 vCPU |
| Память | 2 ГБ (4 ГБ — если своих списков сайтов много) | 1 ГБ |
| Диск | 20 ГБ | — |
| Канал | 500 Мбит/с – 1 Гбит/с | не меньше, чем у узла |

В пике 10–15 устройств дают примерно 100–300 Мбит/с — это 5–28 % процессора узла с 2 vCPU.

- **Минимум (оценка, не замер):** 1 vCPU и 1 ГБ памяти (лучше 2 ГБ) — должно хватить, но без запаса: для
  нескольких устройств, AmneziaWG — модулем ядра (он экономнее программы).
- **Много устройств** (десятки, общий поток до ~1 Гбит/с): 2 vCPU, 2–4 ГБ. AmneziaWG в виде модуля ядра на одном
  ядре пропускает 1,2–1,6 Гбит/с, в каскаде — около 0,9 Гбит/с.
- **Виртуализация — KVM** (или выделенный сервер). OpenVZ и LXC не подходят: шлюзу нужны свои модули ядра и
  nftables.
- **Система:** Debian 12 или 13, Ubuntu 24.04 или 26.04 (см. «Системы» выше).
- **Трафик считается дважды:** каждый байт устройства проходит через узел два раза — от устройства к узлу и от узла
  к выходу (ответ — так же обратно). Если хостер считает весь трафик сервера, на устройства остаётся половина тарифа
  узла.

Сколько занимает Kaskad (замер на 2 vCPU AMD Ryzen 9 9950X, 4 ГБ, Ubuntu 24.04): в простое с 12 входами и панелью
система и Kaskad занимают около 580 МБ памяти, процессор — около 3 % от двух ядер, на диске — около 130 МБ. Поток
100 Мбит/с через узел — 4–13 % процессора: дороже всего AmneziaWG, дешевле — VLESS.

### Как обновиться

- **Уже Kaskad 3:** меню 11 «Обновить скрипт». Пока в `main` лежит 2.13, меню само берёт ветку `test`, а если и там версия старше вашей — `dev`; версию не понижает. Перед загрузкой меню пишет, какая ветка и откуда. Ветку можно закрепить: строка `KASKAD_BRANCH="test"` в `/etc/kaskad/config`.
- **Тестовые сборки Kaskad 3 (ветка `dev`):** команда установки с адресом `.../kaskad-pro/dev/install.sh` сама записывает в `/etc/kaskad/config` строку `KASKAD_BRANCH="dev"` и пишет «Ветка обновлений: dev (тестовые сборки) — записана автоматически». Дальше меню 11 и каталог шаблонов сайта берут файлы из `dev`. Ветку можно передать и явно: `KASKAD_BRANCH=dev ./install.sh` — явная переменная перебивает записанную строку и при запуске поверх установленного (строка «Ветка обновлений: … — записана из команды установки»); без переменной записанная ветка не меняется. Сервер, поставленный раньше без этой строки, запишет её сам при первом обновлении из меню 11. Сервер с записанной `k3` (до 3.3.4) один раз переходит на `dev` сам.
- **Вернуться на стабильную ветку:** в `/etc/kaskad/config` замените строку на `KASKAD_BRANCH="test"` (или `"main"`, когда там выйдет 3.x). Версия при этом не понижается: обновление придёт, когда на выбранной ветке появится версия новее вашей.
- **Бесплатный Kaskad и Kaskad PRO 2.x:** обновление 2.x из меню берёт только `main` (там 2.13), поэтому 3.x ставится командой с веткой `test`:

  ```bash
  export GH_TOKEN=ВАШ_КЛЮЧ_GITHUB; curl -sL -H "Authorization: token $GH_TOKEN" https://raw.githubusercontent.com/anten-ka/kaskad-pro/test/install.sh -o install.sh && chmod +x install.sh && ./install.sh
  ```

  Репозиторий закрытый: нужен ключ GitHub из клуба (тот же, что для «Варианта B» ниже, или goInstaller с выбором
  ветки `test`). При первом запуске Kaskad покажет, что перенесёт на nftables, и спросит согласие.
- **Вернуться на 2.13:** меню 16 → 4 «Вернуться на Kaskad 2.13».

### Документы

- [docs/k3/SPEC-wave1.md](docs/k3/SPEC-wave1.md) — движок пробросов, переезд, возврат;
- [docs/k3/SPEC-wave2-gateway.md](docs/k3/SPEC-wave2-gateway.md) — шлюз;
- [docs/k3/TESTERS-3.1.md](docs/k3/TESTERS-3.1.md) — проверка шлюза на настоящих устройствах;
- [docs/k3/DESIGN-SYSTEM.md](docs/k3/DESIGN-SYSTEM.md) — дизайн-система веб-панели и мастера (токены, компоненты, шрифты);
- в самой программе — меню 13 «Инструкция» (12 страниц).

---

## Возможности

| Функция | Описание |
|---|---|
| **Каскад UDP** | AmneziaWG, WireGuard — одна команда |
| **Каскад TCP** | VLESS, XRay, VMess, Reality, Trojan |
| **Каскад MTProto** | Telegram-прокси |
| **Custom Rule** | Разные порты входа/выхода, SSH, RDP и т.д. |
| **GeoIP + Ping-test** | При добавлении IP: GeoIP (страна/ISP) + 3 пинга |
| **Live Ping** | Пинг с ASCII-графикой (обновление 1 сек) |
| **Telegram Bot** | Полное управление через кнопки (Inline и Reply-клавиатура) |
| **Прокси для API Telegram** | Бот ходит в Telegram через пул ключей VLESS и AmneziaWG 1.0–3.1 (`vpn://` и `.conf` приложения Amnezia и goAmnezia pro) с резервом |
| **Ping в боте** | 1 раз / 10 раз (среднее) / 60 секунд (непрерывно) |
| **Мониторинг** | Автопинг с порогом и частотой уведомлений (10с/60с/5м/15м) |
| **Имена/примечания** | Имена IP серверов + примечания, отображаются везде |
| **BBR Turbo** | Автоматическое включение Google BBR |
| **Безопасность** | Валидация IP/портов, маркировка правил, изоляция iptables |

---

## Установка

Подключитесь к VPS (Ubuntu/Debian/CentOS/Fedora) под `root`.

### Вариант A: репозиторий публичный

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/anten-ka/kaskad-pro/main/install.sh)
```

Или файлом: `curl -fsSL https://raw.githubusercontent.com/anten-ka/kaskad-pro/main/install.sh -o install.sh && bash install.sh`.
Однострочник сам сохраняет программу во временный файл, проверяет, что она скачана целиком, и продолжает с него —
результат тот же, что при запуске файлом. `curl … | bash` тоже работает, если открыт терминал (вопросы читаются с него).

### Вариант B: репозиторий приватный (с GitHub PAT)

```bash
bash <(curl -fsSL -H "Authorization: token YOUR_GITHUB_PAT" https://raw.githubusercontent.com/anten-ka/kaskad-pro/main/install.sh)
```

Далее скрипт установит зависимости, включит IP forwarding/BBR и создаст глобальную команду `gokaskad`.

> Тот же PAT нужен пункту «Прокси для API Telegram» для ядра AmneziaWG (`bin/` репозитория приватный).
> Ключам AmneziaWG 3.x (их по умолчанию выдаёт goAmnezia pro) нужно ядро 3.1 — Kaskad 2.13 сам меняет
> на него прежнее ядро; пока нового нет, ключи 1.x/2.0 работают на старом, а меню говорит, чего не хватает.
> Скрипт ищет ключ сам: меню 8 → 6, `GITHUB_PAT` в `/etc/kaskad/config`, переменная `GH_TOKEN`,
> ключ goInstaller (`/opt/go-installer/token`); если нигде нет — спросит при первом добавлении AWG-ключа.
> Клон репозитория с `bin/` в `/opt/kaskad-pro` (или рядом со скриптом) избавляет от сети вовсе.

```bash
gokaskad
```

---

## Документация

Полная документация: **`DOCS.md`**

---

## Быстрый старт

### 1. Создать каскад (пример WireGuard)

```
Меню → 1
IP адрес зарубежного сервера: 192.0.2.4   (0 или Enter — назад)
Порт: 51820
Применить? y                                 (Enter или n — отменено, правило не создано)
```

Kaskad напишет: «В приложении на телефоне укажите адрес <IP этого сервера> и порт 51820» — замените в клиенте
Endpoint (адрес сервера) на IP этого сервера, порт тот же.

### 2. Настроить Telegram-бота

```
Меню → 8 → 1  →  Вставить токен от @BotFather
Меню → 8 → 2  →  Отправить сообщение боту, нажать Enter
Меню → 8 → 4  →  Запустить бота
```

В Telegram: `/start` — появится меню с кнопками.

### 3. Включить мониторинг

```
Меню → 7 → 1  →  Выбрать сервер → Интервал → Порог (мс)
Меню → 7 → 3  →  Запустить службу
```

Алерты приходят в Telegram, когда пинг превышает порог.

---

## Структура меню

Сверху — шапка «что с сервером»: IP и пробросы, домен и сертификат, адрес панели (маской, F1 — целиком на
5 минут), входы шлюза значками (● работает, ✗ не работает, ○ выключен), живые выходы и первая неполадка.
Ниже — пункты в два столбца (всё вместе — в окно 80×24):

```
 1) AmneziaWG / WireGuard (UDP)       12) PROMO
 2) VLESS / XRay (TCP)                13) Инструкция
 3) TProxy / MTProto (TCP)            14) Имена серверов
 4) Кастомное правило                 15) Удалить Kaskad PRO
 5) Правила                           16) Движок и перенос
 6) Ping (live)                       17) Шлюз: телефоны и роутеры
 7) Мониторинг                        18) Веб-панель
 8) Telegram Bot                      19) Домен и сайт
 9) Удалить правило                   20) Быстрая настройка (в браузере)
10) Сбросить всё                      21) Поиск неполадок
11) Обновить скрипт                    0) Выход
```

Номера 1–19 прежние. 21 — все неполадки списком с советом и переходом к нужному пункту цифрой.

---

## Telegram Bot — кнопки

```
🔀 AWG/WireGuard (UDP)     — добавить UDP каскад
🔀 VLESS/XRay (TCP)        — добавить TCP каскад
🔀 MTProto/TProxy (TCP)    — добавить TCP каскад
🛠 Custom Rule             — произвольное правило
📋 Правила                 — список активных правил
🏓 Ping                    — пинг (1x / 10x / 60 сек)
📊 Мониторинг              — добавить/удалить/список мониторов
❌ Удалить правило          — удалить конкретный каскад
🗑 Сбросить всё            — полная очистка (с подтверждением)
⌨️ Reply-клавиатура         — переключение типа меню
```

---

## Файлы и пути

| Путь | Назначение |
|---|---|
| `/usr/local/bin/gokaskad` | Глобальная команда запуска |
| `/opt/kaskad/bin/gokaskad` | Копия программы, которую запускают службы |
| `/opt/kaskad/bin/kaskadctl` | Ядро Kaskad 3 (Python): `fwd`, `net`, `migrate`, `gw` |
| `/etc/kaskad/config` | Токен бота и Chat ID |
| `/etc/kaskad/state.json` | Движок, пробросы и шлюз (Kaskad 3) |
| `/etc/kaskad/secrets.json` | Ключи и пароли шлюза, только root |
| `/etc/kaskad/monitors/` | Конфигурации мониторов |
| `/etc/kaskad/aliases` | Имена/примечания/GeoIP для IP |
| `/var/backups/kaskad/` | Резервные копии перед переездом на nftables |
| `/var/log/kaskad.log` | Лог всех действий |

---

## Системные службы

```bash
# Telegram-бот
systemctl status kaskad-bot
journalctl -u kaskad-bot -f

# Мониторинг
systemctl status kaskad-monitor
journalctl -u kaskad-monitor -f

# Движок пробросов nftables (Kaskad 3): загрузка таблицы и сторож раз в минуту
systemctl status kaskad-net kaskad-net-watch.timer
/opt/kaskad/bin/kaskadctl net status

# Шлюз (Kaskad 3.1): входы, DNS, xray, сборщик сессий
/opt/kaskad/bin/kaskadctl gw status
journalctl -u 'kaskad-gw-*' -n 50
```

---

## Лицензия

Для частного использования. Распространение запрещено.
