# Kaskad 3 · волна 1: движок пробросов на nftables и переезд с 2.x

Версия спецификации: 2026-10-04 (правки по замечаниям критика к 3.0.1). База: ветка `k3` от `main`
efadd55 (Kaskad PRO 2.13).
Выпуск волны: `KASKAD_VERSION="3.0.1"` в ветку `test` (только после проверки на живых серверах и
подтверждения владельца).

Волна 1 — фундамент. Пользователь получает всё, что умеет 2.13, плюс:

- пробросы работают через одну таблицу nftables `ip kaskad`, а не через россыпь правил iptables;
- проброс срабатывает только для адресов самого сервера (2.x перехватывал транзит);
- у каждого проброса виден трафик;
- корректный перенос с бесплатного Kaskad (все версии), Kaskad PRO 2.0–2.4 и 2.5–2.13;
- резервная копия, проверка после переключения, автоматический откат, ручной возврат на 2.x.

Шлюз (входы WG/AWG/L2TP, устройства, выходы) — волна 2. Таблица и state.json уже оставляют ему место:
всё с префиксом `gw_` в таблице и ключ `gateway` в state.json зарезервированы.

---

## 1. Что стоит на серверах сейчас (факты из кода)

### 1.1 Бесплатный Kaskad (`anten-ka/kaskad`, v1.0 от 11.12.2025, v1.1 от 21.02.2026)

Своего состояния нет, всё в iptables, правила без меток. На одно правило:

```
-A INPUT -p tcp --dport IN -j ACCEPT                                   (добавляет в конец: -A)
-A PREROUTING (nat) -p tcp --dport IN -j DNAT --to-destination IP:OUT
-A POSTROUTING (nat) -o IFACE -j MASQUERADE                             (одно на все правила, без метки)
-A FORWARD -p tcp -d IP --dport OUT -m state --state NEW,ESTABLISHED,RELATED -j ACCEPT
-A FORWARD -p tcp -s IP --sport OUT -m state --state ESTABLISHED,RELATED -j ACCEPT
```

- v1.0 не копирует себя в команду; v1.1 копирует в `/usr/local/bin/gokaskad`, без строки `KASKAD_VERSION`.
- Перед добавлением удаляет только точно такое же правило. Второе правило на тот же порт с другой целью
  ложится в конец, а работает первое (DNAT срабатывает по первому совпадению).
- Если UFW активен — `ufw allow IN/proto` и `DEFAULT_FORWARD_POLICY="ACCEPT"` в `/etc/default/ufw`.
- Сохраняет через `netfilter-persistent save` (весь набор правил целиком), sysctl: ip_forward, fq, bbr.
- Пункт «Сбросить ВСЕ настройки» делает `iptables -F/-X`, `-t nat -F`, `-t mangle -F`.

### 1.2 Kaskad PRO 2.0–2.4

DNAT без метки, INPUT и FORWARD с меткой `kaskad:IN:proto`. Начиная с 2.5 `migrate_dnat_comments`
при запуске доставляет метку к DNAT, если есть FORWARD-правило с той же целью и портом.
Файлы: `/etc/kaskad/config`, `aliases` (2.1: `IP=имя`, с 2.2: `IP=имя|заметка|страна|провайдер`),
`monitors/<ip>.conf`, `bot_state/`.

### 1.3 Kaskad PRO 2.5–2.13

```
-I INPUT   -p P --dport IN -m comment --comment kaskad:IN:P -j ACCEPT
-A PREROUTING (nat) -p P --dport IN -m comment --comment kaskad:IN:P -j DNAT --to-destination IP:OUT
-A POSTROUTING (nat) -o IFACE -m comment --comment kaskad:masq -j MASQUERADE        (одно)
-I FORWARD -p P -d IP --dport OUT -m state --state NEW,ESTABLISHED,RELATED -m comment --comment kaskad:IN:P -j ACCEPT
-I FORWARD -p P -s IP --sport OUT -m state --state ESTABLISHED,RELATED -m comment --comment kaskad:IN:P -j ACCEPT
```

- С 2.12 при каждом запуске `adopt_free_kaskad_rules` забирает правила бесплатной версии: DNAT без
  метки + FORWARD без метки к той же цели. **Ошибка:** при двух правилах на один порт применяется
  последнее, а до переноса работало первое — поведение меняется (см. §4.3).
- Службы: `kaskad-bot`, `kaskad-monitor`, `kaskad-egress` (xray, SOCKS 127.0.0.1:10808, API 10851,
  `ExecStartPre=/usr/local/bin/gokaskad --egress-awg-up`), `kaskad-egress-probe.timer`. Все запускают
  `/usr/local/bin/gokaskad`.
- Пул «Прокси для API Telegram»: `/etc/kaskad/egress/upstream.txt` (строка на ключ: `vless://…` или
  `awg://<base64 json>`), интерфейсы `kawgN`, `ip rule fwmark 256+N table 256+N`. В волне 1 не трогаем.
- `self_update` всегда качает `main/install.sh`; проверяет первую строку `#!/bin/bash`, `bash -n` и что
  в последних трёх строках есть `esac`. После замены перезапускает `kaskad-bot` и `kaskad-monitor`.

### 1.4 goInstaller

Ищет продукт по `/usr/local/bin/gokaskad` или `/etc/kaskad/config`, версию — по `KASKAD_VERSION=` в
`install.sh`/команде, бесплатную версию — по отсутствию этой строки. Ставит так:
`cp install.sh $work/ && (cd $work && ./install.sh)` — без ключа GitHub и без ветки в окружении.
Сравнение версий — `sort -V`. Порты продукта: 10808, 10851 (loopback).

## 2. Проблемы, которые закрывает волна

| # | Проблема | Чем закрываем |
|---|---|---|
| P1 | DNAT без ограничения адреса: проброс `tcp/443` перехватывает весь транзитный HTTPS (контейнеры, будущие клиенты шлюза) | `fib daddr type local` |
| P2 | Два правила на один порт: работало первое, перенос 2.12 оставляет последнее | перенос берёт действующее (первое) |
| P3 | Бесплатный скрипт затирает `/usr/local/bin/gokaskad`; службы запускают именно его | службы запускают копию `/opt/kaskad/bin/gokaskad`, сторож возвращает команду |
| P4 | `self_update` всегда тянет `main` | ветка обновлений, запрет понижения версии |
| P5 | `netfilter-persistent save` сохраняет весь набор правил, включая чужие временные | Kaskad не сохраняет iptables; таблицу восстанавливает своя служба |
| P6 | UFW/Docker с FORWARD DROP; 2.x ставил по два FORWARD-правила на проброс | одно правило совместимости по ct mark |
| P7 | Трафик по пробросам не виден | именованный счётчик на проброс |
| P8 | `iptables -F` («Сбросить всё» бесплатной версии) сносит пробросы | таблица nft не задета; сторож чинит недостающее |

## 3. Движок пробросов (пакет WP1)

### 3.1 Таблица

```
table ip kaskad {
    counter fwd_tcp_443 { packets 0 bytes 0 }
    counter fwd_udp_51820 { packets 0 bytes 0 }

    chain fwd_pre {
        type nat hook prerouting priority -101; policy accept;
        fib daddr type local iifname != "lo" jump fwd_dnat
    }
    chain fwd_dnat {
        tcp dport 443 ct mark set ct mark or 0x4b000000 dnat to 203.0.113.5:443 comment "kaskad:443:tcp"
        udp dport 51820 ct mark set ct mark or 0x4b000000 dnat to 198.51.100.7:51820 comment "kaskad:51820:udp"
    }
    chain fwd_count {
        type filter hook forward priority -5; policy accept;
        ct mark and 0xff000000 != 0x4b000000 return
        meta l4proto tcp ct original proto-dst 443 counter name "fwd_tcp_443" return
        meta l4proto udp ct original proto-dst 51820 counter name "fwd_udp_51820" return
    }
    chain fwd_post {
        type nat hook postrouting priority 101; policy accept;
        ct status dnat ct mark and 0xff000000 == 0x4b000000 masquerade
    }
}
```

`fwd_post` — на приоритете 101 (srcnat + 1): iptables nat POSTROUTING (100) отрабатывает раньше,
и чужой SNAT (на закреплённый адрес) по-прежнему побеждает, как с `kaskad:masq` 2.x в конце
POSTROUTING. Правило в `fwd_count` метит любое DNAT-соединение с исходным портом проброса (чужой DNAT
на том же proto/порту — и так конфликт, §3.6). Почему так — в docstring `lib/kaskad/nft.py`. Порядок правил в `fwd_dnat` и `fwd_count` — по
`(port, proto)`. Числовые приоритеты (а не `dstnat`) — ради старых nft. Если какая-то конструкция не
работает на целевой системе, `probe_support` это ловит, и сервер остаётся на старом движке.

### 3.2 Применение (`nft.apply`)

1. `live = read_live()`; `script = render_update(st, live)`.
2. `check(script)` → `nft -c -f -`. Ошибка → `KaskadError`, ничего не меняли.
3. `nft -f -` (одна транзакция: всё или ничего).
4. `read_live()` и сверка: набор `(proto, port) → (to, to_port)` равен state; есть jump и masquerade;
   есть счётчик на каждый проброс. Расхождение → `KaskadError` с подробностями.
5. Записать `/etc/kaskad/net/kaskad.nft` = `render_full(st, counters=live counters)` (атомарно, 0600).
6. Обеспечить `net.ipv4.ip_forward=1`: если 0 — `sysctl -w` и файл `/etc/sysctl.d/90-kaskad.conf`.

`render_update` (живую таблицу не пересоздаёт, иначе обнулятся счётчики):

```
add table ip kaskad
add chain ip kaskad fwd_pre { type nat hook prerouting priority -101; policy accept; }
add chain ip kaskad fwd_dnat
add chain ip kaskad fwd_count { type filter hook forward priority -5; policy accept; }
add chain ip kaskad fwd_post { type nat hook postrouting priority 101; policy accept; }
flush chain ip kaskad fwd_pre
flush chain ip kaskad fwd_dnat
flush chain ip kaskad fwd_count
flush chain ip kaskad fwd_post
add counter ip kaskad fwd_tcp_443            # только недостающие
add rule ip kaskad fwd_pre fib daddr type local iifname != "lo" jump fwd_dnat
add rule ip kaskad fwd_dnat tcp dport 443 ct mark set ct mark or 0x4b000000 dnat to 203.0.113.5:443 comment "kaskad:443:tcp"
add rule ip kaskad fwd_count ct mark and 0xff000000 != 0x4b000000 return
add rule ip kaskad fwd_count meta l4proto tcp ct original proto-dst 443 counter name "fwd_tcp_443" return
add rule ip kaskad fwd_post ct status dnat ct mark and 0xff000000 == 0x4b000000 masquerade
delete counter ip kaskad fwd_udp_1234        # счётчики удалённых пробросов — после flush
```

`render_full` начинается с `table ip kaskad` + `delete table ip kaskad` и затем полное определение
(см. 3.1) с сохранёнными значениями счётчиков — так `nft -f` при загрузке всегда пересоздаёт таблицу
одной транзакцией.

Пустой список пробросов — таблица с цепочками и jump/masquerade, но без правил пробросов (так
она готова к шлюзу и проверка проще).

### 3.3 Правило совместимости (`compat.py`)

```
iptables -I FORWARD 1 -m connmark --mark 0x4b000000/0xff000000 -m comment --comment kaskad:compat -j ACCEPT
```

- Ставится при включённом движке nft всегда (идемпотентно, `-C` перед `-I`).
- Нет бинаря `iptables` — пропускаем молча (значит, нет ни UFW, ни Docker на iptables).
- Kaskad это правило не сохраняет в rules.v4: его возвращают `kaskad-net.service` и сторож.
- INPUT-правила и `ufw allow` для пробросов не нужны: DNAT-трафик идёт через FORWARD, а не INPUT.
  Существующие `ufw allow` не трогаем.

### 3.4 Службы (`units.py`)

`kaskad-net.service`, `kaskad-net-watch.service`, `kaskad-net-watch.timer` — текст и порядок в
docstring `units.py`. Загрузка таблицы при старте — чистый `nft -f` без Python (если /opt/kaskad
повреждён, пробросы всё равно поднимутся). Python нужен только в ExecStartPost/ExecStop.

### 3.5 Сторож: `kaskadctl net check --repair`

Раз в минуту и при загрузке. Только если `engine == "nft"`, иначе выход 0 без действий.

1. Таблицы нет → `restore` (из kaskad.nft; файла нет — отрисовать из state). Запись в журнал.
2. Пробросы в живой таблице ≠ state (кто-то правил руками или `nft flush ruleset`) → `apply`. Журнал.
3. Нет правила совместимости → `ensure`. Журнал (не чаще раза в час на одно и то же).
4. `net.ipv4.ip_forward` = 0 → вернуть 1. Журнал.
5. `/usr/local/bin/gokaskad` без `KASKAD_VERSION="3.` (бесплатный скрипт или 2.x поверх), а
   `/opt/kaskad/bin/gokaskad` — настоящий 3.x → вернуть копию (cp во временный файл + rename, 0755).
   Журнал: «Команду gokaskad подменила другая версия — вернул 3.x».
6. Сохранять счётчики в kaskad.nft не чаще раза в 10 минут.

Без `--repair` — только отчёт (`--json`: `{"ok": bool, "problems": [...]}`), ничего не меняет.

### 3.6 Проверка конфликтов при добавлении (`fwd add`)

Отказ с кодом 3 (CONFLICT) и `data.kind`, если нет `--force`:

- `ssh` — порт sshd (`ss -Hltnp` с процессом sshd) или порт из `$SSH_CONNECTION`: проброс отрежет
  SSH к самому серверу;
- `listener` — порт слушает локальный процесс (`ss -Hltnup`), например xray или nginx на 443;
- `docker` — порт опубликован контейнером (`iptables -t nat -S DOCKER`, правила `-j DNAT`);
- `foreign_dnat` — в iptables `nat PREROUTING` есть чужой DNAT на этот proto/порт (наш приоритет −101
  его перебьёт).

Повторное добавление того же proto:port с другой целью — замена (как в 2.x), не конфликт.

### 3.7 CLI (`cli_fwd.py`, `cli_net.py`)

Общие правила: `--json` печатает ровно один JSON-документ; без него — короткий русский текст;
ошибки в stderr; коды выхода — `kaskad/errors.py`. Все изменения — под `state.locked()`; при неудаче
применения state возвращается к прежнему (сначала `apply` новой копии, потом `save`).

| Команда | Что делает | Вывод без `--json` |
|---|---|---|
| `fwd list [--traffic]` | пробросы из state (engine nft; иначе код 5) | `proto\|port\|to:to_port` построчно (формат `get_rules_list` 2.x); с `--traffic` ещё `\|packets\|bytes` |
| `fwd add --proto P --port N --to IP --to-port M [--name S] [--force]` | проверка, конфликты, upsert, apply | `tcp: :443 -> 203.0.113.5:443` |
| `fwd del --proto P --port N` | удалить, apply; нет такого — код 1 | `Правило удалено.` |
| `fwd flush` | удалить все, apply | `Удалено правил: N.` |
| `fwd stats` | счётчики из живой таблицы | таблица |
| `net status` | движок, поддержка, таблица, совместимость, службы (сторож-oneshot — «по таймеру»), версия nft | строки «ключ: значение» |
| `net probe` | `probe_support` | — |
| `net apply [--dry-run]` | применить state; `--dry-run` печатает скрипт | — |
| `net render [--full]` | скрипт update или полный | скрипт |
| `net restore` | как при загрузке | — |
| `net check [--repair] [--quiet]` | §3.5 | список проблем |
| `net save-counters [--quiet]` | §3.2 п.5 | — |
| `net install-units` / `net remove-units` | службы движка | — |
| `net teardown` | удалить таблицу, правило совместимости и службы (для отката и удаления) | — |

Журнал `/var/log/kaskad.log` (`util.log_action`) — тем же текстом, что 2.x:
`ADD rule: tcp :443 -> 203.0.113.5:443 (имя)`, `DELETE rule: tcp :443 -> 203.0.113.5:443`,
`FLUSH all kaskad rules`, плюс `NET repair: …`, `NET restore`.

## 4. Переезд (пакет WP2)

### 4.1 Принципы

- Переносим то, что **реально работает сейчас** (живые правила), с тем же поведением.
- Чужое не трогаем: правила без признаков Kaskad, Docker, UFW, MASQUERADE без метки.
- Всё обратимо: копия до изменений, проверка после, автоматический откат при расхождении.
- Без простоя: сначала поднимаем nft-пробросы (они перебивают старые правила с тем же итогом),
  потом снимаем старые. Текущие соединения живут на conntrack и не рвутся.
- Повторный запуск безопасен (идемпотентность): уже перенесённое не трогается, новые правила
  бесплатной версии (если её запустили поверх) дозабираются тем же `apply`.

### 4.2 Сбор (`migrate scan`, только чтение)

Источники:

- `iptables -V` (бэкенд `nf_tables`/`legacy`); `iptables -t nat -S`, `iptables -S` целиком, по
  цепочкам и по порядку; если есть `iptables-legacy` и в нём тоже правила — предупреждение «смешанный
  бэкенд»; переносим из того бэкенда, который отвечает на `iptables`;
- сохранённые правила: `/etc/iptables/rules.v4` или `/etc/sysconfig/iptables` (формат iptables-save);
- `/usr/local/bin/gokaskad`: есть ли, `KASKAD_VERSION`, похож ли на бесплатный (нет строки версии,
  есть `configure_custom_rule` и `show_promo`);
- `/etc/kaskad/state.json` (если уже 3.x), `config` (только наличие ключей, без значений!),
  `aliases` (имена целей → имена пробросов), `monitors/*.conf`, `egress/upstream.txt` (число строк);
- `systemctl is-enabled/is-active` для kaskad-bot, kaskad-monitor, kaskad-egress,
  kaskad-egress-probe.timer, kaskad-net.service, kaskad-net-watch.timer, ufw, docker, firewalld,
  nftables;
- `ufw status`, `DEFAULT_FORWARD_POLICY`; политика цепочки FORWARD; цепочка `DOCKER` в nat;
- `ss -Hltnup` (кто слушает); `net.probe_support()`.

Разбор правил — `kaskad/ipt.py`: строку `-S`/iptables-save режем `shlex.split`, без `eval`. Учесть
варианты печати: комментарий в кавычках и без; `-m tcp`/`-m udp` есть или нет; `/32` у адреса;
`--state NEW,RELATED,ESTABLISHED` в любом порядке; `-m conntrack --ctstate` вместо `-m state`;
`! -i docker0`; лишние модули. Каждое правило — объект: цепочка, индекс, токены, `proto`, `dport`,
`sport`, `src`, `dst`, `iif`, `oif`, `comment`, `target`, `to_destination`, `extra` (всё
нераспознанное), `simple` (нет `extra` и квалификаторов).

### 4.3 Классификация DNAT в `nat PREROUTING`

Ключ правила — `(proto, dport)`.

| Класс | Признак |
|---|---|
| `pro` | комментарий `kaskad:N:P`, и N/P совпадают с `--dport`/`-p` правила |
| `pro-legacy` | без метки, простая форма; есть FORWARD с меткой `kaskad:N:P` и `-d IP/32 --dport OUT` той же цели (2.0–2.4) |
| `free` | без метки, простая форма; есть FORWARD без метки `-d IP/32 -p P --dport OUT … -j ACCEPT` к той же цели |
| `foreign` | всё остальное: без пары FORWARD, с `-d`/`-i`/`-s`/addrtype/multiport/диапазонами, DNAT без порта |

Простая форма DNAT: только `-p P`, `-m P`, `--dport N`, `-j DNAT`, `--to-destination A.B.C.D:PORT`
(+ комментарий у `pro`). Метка `kaskad:…` с квалификаторами → всё равно `pro`, с предупреждением
«правило правили вручную, условия … будут сняты».

Какое правило действует — идём по цепочке сверху вниз; первое совпадение по ключу побеждает.
Переход `-j DOCKER` (и другие переходы в пользовательские цепочки, глубина ≤ 3) раскрываем: DNAT
внутри перехваченной цепочки считается правилом в позиции перехода.

Статус каждого правила Kaskad (`pro`/`pro-legacy`/`free`):

- `import` — первое по своему ключу и раньше него нет чужого DNAT/Docker с этим ключом;
- `duplicate` — по ключу раньше стоит другое правило Kaskad: не действовало, не переносим, снимаем;
- `shadowed` — раньше стоит чужой DNAT или Docker: не действовало; не переносим и **не трогаем**
  (ни DNAT, ни его INPUT/FORWARD), сообщаем, человек решает сам;
- `skipped` / `partial_before` — раньше стоят только чужие правила с условиями (`-s …`): правило Kaskad
  работает для остального трафика — «работает не для всего трафика (раньше стоит правило с условием …)
  — оставлю как есть», не «не работало»;
- `-d` внутри 127.0.0.0/8 (Docker `-p 127.0.0.1:…`) трафика снаружи не видит: не перехватывает и не
  публикует порт;
- конфликт `foreign_after` — чужой DNAT того же ключа стоит **после** правила Kaskad: он и раньше не
  действовал, и с приоритетом −101 не начнёт. Переносим, предупреждаем.
- конфликт `docker_port` — ключ опубликован Docker-контейнером, но переход в DOCKER стоит после
  правила Kaskad (так бывает после перезагрузки) — какое правило действует, зависело от порядка
  загрузки. Без `--force` переезд отказывается (код 3) и показывает это.

### 4.4 Что снимаем из iptables

Только правила ключей со статусом `import` и `duplicate`:

- DNAT этих ключей (любой класс);
- `pro`/`pro-legacy`: INPUT и FORWARD с меткой `kaskad:N:P` этих ключей;
- `free`: INPUT без метки ровно вида `-p P [-m P] --dport N -j ACCEPT`; FORWARD-пары без метки к
  цели `IP:OUT` — **кроме** пар, на которые ссылается какой-то остающийся DNAT (чужой или `shadowed`).

Остаются: `kaskad:masq` (маскирует всё, что уходит через интерфейс, — на него может опираться другой
VPN на сервере; возврат на 2.13 второго не добавляет), MASQUERADE без метки (мог служить чему-то ещё —
например, своему WireGuard), чужие правила, `shadowed`, правила ufw/Docker, записи `ufw allow`.

Снятие — одной транзакцией на таблицу: `iptables-restore --noflush` с `-D`-строками (сначала nat,
потом filter). Ошибка любой строки → таблица не меняется → переезд откатывается.

Сохранённый файл (rules.v4 / sysconfig) правим точечно: убираем ровно снятые строки (сравнение по
токенам, с учётом кратности), а также правила Kaskad, которых нет в живом наборе (они вернулись бы
после перезагрузки). Остальное — байт в байт. Файла нет — ничего не пишем. Файл перечитывается в
момент записи: строки снимаются из ТЕКУЩЕГО содержимого; запланированных строк там уже нет — отказ и
автооткат (файл остаётся таким, каким его записал другой); автооткат возвращает только свою правку.

Пробросы Kaskad, которые есть **только** в файле (живые правила сброшены или не поднялись после
перезагрузки), — настроенные каскады: 2.x вернул бы их при загрузке. При переезде (switch) они
переносятся в nftables (предпросмотр: «Сейчас не работают: … — их сразу поднимет nftables»), их строки
уходят из файла. Порт, который сейчас забирает живое правило, и правила, которые и в файле работали бы
не так, остаются в файле как были; мёртвые (второе на порту) — как устаревшие строки, с предупреждением.
На движке nft (дозабор) строки Kaskad только из файла — устаревшие, как раньше.

Движок legacy, в state.json остались пробросы (после «Вернуть iptables»), а работающих правил Kaskad нет
ни в iptables, ни в файле (перезагрузка без netfilter-persistent) — переезд предлагает их вернуть
(«Верну N … из прошлой настройки Kaskad 3»), а не затирает state.forwards пустым списком.

### 4.5 Применение (`migrate apply --yes`)

Порядок:

1. `scan` + `plan`. Блокеры (нет поддержки nft, конфликты без `--force`, неизвестная схема
   state.json) → код 3/5, ничего не меняли.
2. Снимок (§4.7).
3. Нет `nft` → `apt-get install -y nftables` (не включать `nftables.service`). Нет `python3` не бывает —
   мы в нём.
4. state: `engine = "nft"`, перенесённые пробросы (`origin` = класс, `name` = имя цели из aliases,
   если есть), запись в `migrations` (результат `pending`).
5. `nft.apply(st)` — пробросы уже работают через nft (и перебивают старые правила).
6. `compat.ensure()`.
7. `units.install()` — до снятия старого: убитый процесс или отключение питания после следующего шага
   не оставят engine=nft без `kaskad-net.service`.
8. Снятие из iptables (§4.4), правка сохранённого файла.
9. Проверка (§4.6). Не прошла → автооткат (§4.8), код 4. Служба в перезапуске (`activating`) — не
   расхождение; остальное переспрашивается через 3 с.
10. Запись в журнал и `migrations[-1].result = "ok"`; `units.activate()` — уже вне блокировки.

SIGHUP/SIGTERM на шагах 2–9 роняют текущий шаг исключением — срабатывает автооткат; второй сигнал
его не прерывает (`migrate/interrupt.py`). То же у `migrate rollback`, пока пишутся правила 2.13.

Движок уже nft, а таблица не загружена или `kaskad-net.service` / `kaskad-net-watch.timer` не включены
(`scan.engine_health`): `migrate apply` сначала грузит таблицу из state.json и ставит службы — одна
строка «Движок nftables был не в порядке (…) — таблица загружена, службы движка включены.».
`apt-get` для nftables — с `DEBIAN_FRONTEND=noninteractive`, `NEEDRESTART_MODE=a` и закрытым stdin.

Пустой переезд (правил нет, свежая установка) — те же шаги без снятия; меню включает так новый
движок без вопросов.

### 4.6 Проверка после переключения

- живая таблица: набор пробросов = перенесённому; jump, masquerade, счётчики на месте;
- в iptables не осталось правил снятых ключей; правило совместимости на месте;
- в сохранённом файле нет снятых строк;
- `kaskad-net.service` и `kaskad-net-watch.timer` включены;
- state.json перечитывается, engine = nft, пробросы = перенесённым;
- службы kaskad-bot/monitor/egress в том же состоянии, что до переезда.

### 4.7 Снимок

`/var/backups/kaskad/<UTC id>-<edition>-<version>/`, каталог 0700, файлы 0600:

- `iptables-save.txt`, `ip6tables-save.txt`, `nft-ruleset.txt` (`nft list ruleset`, если есть nft),
  `ip-rule.txt`, `ip-route-all.txt`, `ss.txt`, `ufw-status.txt`, `systemctl.txt`;
- `etc-kaskad.tar.gz` (`/etc/kaskad` без бинарей `egress/xray` и `egress/awg/*`), копии
  `/usr/local/bin/gokaskad`, `/etc/iptables/rules.v4` или `/etc/sysconfig/iptables`,
  `/etc/systemd/system/kaskad-*`, `/etc/sysctl.conf`, `/etc/default/ufw`;
- `scan.json`, `plan.json`, `manifest.json` (id, время, версия from/to, sha256 файлов).

Внутри секреты (токен бота, ключ GitHub, ключи выхода) — поэтому права только root и никакого вывода
содержимого в терминал/журнал. Хранить последние 5 снимков.

### 4.8 Автооткат (проверка не прошла)

1. `nft.delete_table`, `compat.remove`, `units.remove`;
2. `iptables-restore -T nat` и `-T filter` из снимка (секунды назад — правила Docker/UFW те же);
3. вернуть сохранённый файл правил из снимка;
4. state: `engine = "legacy"`, `migrations[-1].result = "rolled_back"` + причина;
5. код 4 и отчёт: что не сошлось.

### 4.9 Ручной возврат (`migrate rollback --to-legacy --yes`)

Для «Вернуть старый движок» и возврата на 2.13. engine должен быть nft.

1. Для каждого проброса из state — правила ровно в формате 2.13 (§1.3) одной транзакцией
   `iptables-restore --noflush` (INPUT `-I`, DNAT `-A`, `kaskad:masq` если нет, две FORWARD `-I`).
   Интерфейс для masq — `ip route get 8.8.8.8` (как `detect_interface`).
2. Потом `nft.delete_table`, `compat.remove`, `units.remove` (порядок — без простоя).
3. `netfilter-persistent save`, если есть (так делал 2.x). Нет — предупреждение (и без файла правил).
   Предупреждение «После перезагрузки пробросы пропадут…» меню показывает ДО вопроса «Вернуть
   iptables? (y/n)», если netfilter-persistent нет на Debian 12+/Ubuntu 24.04+. Службы загрузки правил
   старого движка нет (решение владельца не принято).
4. state: `engine = "legacy"`, пробросы оставить (для справки), запись в `migrations`.

Возврат самой программы 2.13 (команда, пути служб) делает install.sh (§5.6).

### 4.10 CLI (`migrate/cli.py`)

| Команда | Что делает |
|---|---|
| `migrate scan [--json]` | отчёт §4.2–4.3 (без секретов) |
| `migrate plan [--json]` | действия + русский текст предпросмотра (§4.11) |
| `migrate apply --yes [--force] [--json]` | §4.5; без `--yes` — код 2 |
| `migrate rollback --to-legacy --yes [--json]` | §4.9 |
| `migrate backups [--json]` | список снимков |

`scan --json`: `edition` (`free`/`pro`/`k3`/`none`), `version`, `engine`, `command{}`,
`support{}`, `iptables{backend, mixed}`, `forwards[]` (key, proto, port, to, to_port, class,
status, name, rules[]), `foreign_dnat[]`, `masquerade{kaskad, unlabeled[]}`, `conflicts[]`
(key, kind, detail), `persisted{file, exists, only_live[], only_persisted[]}`,
`firewall{ufw_active, ufw_forward_policy, forward_policy, docker, firewalld}`, `services{}`,
`egress{keys}`, `aliases`, `monitors`, `listeners[]`, `warnings[]`.

### 4.11 Предпросмотр (пример текста)

```
Перевод пробросов на nftables

Перенесу 3 проброса:
  tcp :443   → 203.0.113.5:443      Вена        из бесплатной версии
  udp :51820 → 198.51.100.7:51820   Амстердам   из Kaskad PRO
  tcp :8443  → 198.51.100.7:443     Амстердам   из Kaskad PRO
Не перенесу:
  tcp :8080 → 10.0.0.5:80 — не работало: порт раньше забирает Docker. Правило оставлю как есть.
  tcp :443 → 192.0.2.9:443 — не работало: второе правило на тот же порт. Уберу.
Останется как было: чужое правило выхода в интернет через eth0 в iptables, 1 чужое правило DNAT,
  правило Kaskad для выхода в интернет через eth0 в iptables — на него может опираться другой VPN на сервере.

Что изменится:
  • проброс срабатывает только для адресов этого сервера
  • у каждого проброса появится счётчик трафика
  • правила iptables Kaskad заменит таблица nftables «kaskad»
Резервная копия: /var/backups/kaskad/20261004T120000Z-pro-2.13
Вернуть как было: меню «Движок пробросов» → «Вернуть iptables».
```

## 5. install.sh (пакет WP3)

### 5.1 Версия и совместимость с 2.x

- `KASKAD_VERSION="3.0.1"`; шапка-комментарий — Kaskad PRO 3.
- Файл по-прежнему начинается с `#!/bin/bash`, проходит `bash -n`, а в последних трёх строках есть
  `esac` — иначе `self_update` 2.x откажется ставить 3.x.
- 2.x после обновления сразу перезапускает `kaskad-bot` и `kaskad-monitor` новым файлом: режимы
  `--bot-daemon`/`--monitor-daemon` обязаны работать без state.json (движок legacy).

### 5.2 Код Python внутри install.sh

- `tools/build_payload.py` собирает `lib/kaskad/**` (только `.py`) в детерминированный tar.gz
  (Python `tarfile`: имена по порядку, mtime 0, uid/gid 0, права 0644; gzip mtime 0), base64 по 76
  символов, и вставляет в install.sh между строками
  `# >>> K3_PAYLOAD (generated by tools/build_payload.py, do not edit)` и `# <<< K3_PAYLOAD`:
  `K3_LIB_SHA="<sha256 архива>"` и функция `k3_payload() { cat <<'K3PAYLOAD' … K3PAYLOAD }`.
  Блок стоит в середине файла (не в конце). `--check` — код 1, если блок устарел.
- `k3_ensure_lib`: если нет `/opt/kaskad/lib/<sha12>/.ok` — распаковать во временный каталог рядом,
  сверить sha256, `touch .ok`, переименовать; `current` → `<sha12>` атомарно (`ln -sfn` + `mv -Tf`);
  обёртка `/opt/kaskad/bin/kaskadctl` (`PYTHONPATH=/opt/kaskad/lib/current exec python3 -m kaskad "$@"`);
  копия себя в `/opt/kaskad/bin/gokaskad`, если `$0` — настоящий файл 3.x и копия отличается; старые
  каталоги lib чистить, оставляя текущий и предыдущий. Вызывать при запуске меню, в начале режимов
  демонов и после обновления.
- `K3_OPT="${K3_OPT:-/opt/kaskad}"` и `KASKAD_DIR="${KASKAD_DIR:-/etc/kaskad}"` — переопределяемы для
  тестов (службы systemd окружение не передают — значения по умолчанию).

### 5.3 Два режима функций правил

`k3_engine` → `nft`, если `/etc/kaskad/state.json` содержит `"engine": "nft"`, иначе `legacy`.

| Функция 2.x | legacy | nft |
|---|---|---|
| `get_rules_list` | как в 2.13 | state.json через `jq` (формат и порядок `kaskadctl fwd list`, без python) |
| `apply_iptables_rules proto in out ip name` | как в 2.13 | `kaskadctl fwd add …`; код 3 → в меню вопрос «Порт занят: … Всё равно? (y/n)» и повтор с `--force` (для `ssh` — ввод слова-подтверждения); в боте — отказ с причиной |
| `remove_rules_for_port` | как в 2.13 | `kaskadctl fwd del` |
| `delete_single_rule`, `flush_rules`, `flush_all_kaskad_rules` | как в 2.13 | через `fwd del`/`fwd flush`; ufw включён — `ufw delete allow PORT/proto` (как 2.x) |
| `remove_masquerade_if_unused`, `save_iptables` | как в 2.13 | ничего не делают |
| `list_active_rules` | как в 2.13 | + столбец трафика (`fwd list --traffic`) |
| `migrate_dnat_comments`, `adopt_free_kaskad_rules` при запуске | как в 2.13, но перенос бесплатных — **с первым совпадением** (обход найденного в обратном порядке) | не вызываются; вместо них предложение дозабрать (§5.4) |

Бот (`bot_handle_callback`, `bot_handle_message`) ходит через те же функции — менять его логику не нужно,
только сообщения об отказе по конфликту.

Запись старого движка (правила iptables и rules.v4: добавить, удалить, сбросить, перенос бесплатных,
удаление Kaskad) идёт под той же блокировкой, что у kaskadctl: `flock -w 15 /run/kaskad/state.lock`
(нет flock — без блокировки, как 2.13); правило вместе с `netfilter-persistent save` — одним куском.
Занято — код 6 и строка «Kaskad сейчас что-то меняет…» (бот отказывает, а не пишет «✅»); пока ждали,
движок стал nft — код 7, ничего не пишется.

### 5.4 Запуск меню

После проверки зависимостей (добавить `python3`; `nftables` ставит сам переезд): `k3_ensure_lib`,
затем `kaskadctl migrate scan --json`:

- engine legacy, показывать нечего (summary: `kaskad_rules` = `import` = `persisted_lines` = `warnings` = 0,
  без блокеров), nft поддерживается → `migrate apply --yes` молча (свежая установка сразу на новом
  движке); иначе — предпросмотр и вопрос;
- engine legacy, есть что переносить → экран предпросмотра (`migrate plan`) и вопрос:
  `1) Перевести сейчас  2) Позже  3) Не спрашивать больше` (3 → `K3_MIGRATE_ASK="0"` в config);
- engine legacy, nft не поддерживается → одна строка «Новый движок здесь недоступен: <причина>.
  Пробросы работают по-старому.»;
- engine nft, но scan нашёл новые правила Kaskad в iptables (бесплатный скрипт запускали поверх) →
  предложить «дозабрать» тем же `migrate apply`: `1) Забрать сейчас  2) Позже  3) Не спрашивать больше`
  (3 → `K3_ADOPT_ASK="0"`); план заблокирован — не спрашивать;
- engine nft, а `engine_health` не пуст (таблица не загружена, службы движка не включены) →
  `net apply` + `net install-units` и одна строка о том, что сделано.

Новый пункт меню «Движок пробросов»: состояние (`net status`), перевести на nftables, вернуть
iptables (§4.9), резервные копии, «Вернуться на Kaskad 2.13» (§5.6). В шапке меню — версия и движок.

### 5.5 Службы и команда

- Все юниты, которые пишет install.sh (`kaskad-bot`, `kaskad-monitor`, `kaskad-egress`
  ExecStartPre/ExecStopPost, `kaskad-egress-probe`), запускают `/opt/kaskad/bin/gokaskad`.
  `--k3-post-update` переписывает в существующих юнитах `/usr/local/bin/gokaskad` → копию в /opt,
  `daemon-reload`, без перезапуска.
- Новые служебные режимы: `--k3-ensure-lib`, `--k3-post-update` (на движке nft — ещё и
  `kaskadctl net install-units`: тексты юнитов движка этой версии; если код сменился и шлюз включён —
  `systemctl try-restart kaskad-gw-collect` и `kaskadctl gw apply`, docs/k3/SPEC-wave2-gateway.md §7).
- Демон 3.x (`--bot-daemon`/`--monitor-daemon`) при движке legacy, когда команда — снова целая
  программа 2.x (self_update 2.x или его откат): юниты обратно на команду, `daemon-reload` и `exec`
  программы 2.x (тот же PID). На движке nft — как раньше (команду вернёт сторож).
- `full_uninstall`: в режиме nft — `kaskadctl net teardown` (и `ufw delete allow` входных портов
  пробросов, если ufw включён), потом удалить `/opt/kaskad` и `state.json`; остальное как в 2.13.

### 5.6 Обновление и возврат

- `KASKAD_BRANCH` в config (не задан → `main`). Перед загрузкой узнать версию на ветке; если ветка не
  задана, установлена 3.x, а на `main` версия старше — брать `test`.
- Понижение версии запрещено: версия на ветке ниже установленной → «На ветке X версия Y — старше вашей
  Z. Обновлять нечего.» Сравнение — `sort -V`.
- После замены: копия в /opt, `--k3-post-update`, затем перезапуск bot/monitor как в 2.x.
- «Вернуться на Kaskad 2.13»: `kaskadctl migrate rollback --to-legacy --yes` → взять 2.x-программу
  (из последнего снимка с версией 2.x; нет — скачать `v2.13` с GitHub по ключу) → вернуть её в
  `/usr/local/bin/gokaskad` → пути юнитов обратно на `/usr/local/bin/gokaskad` → `daemon-reload`,
  перезапуск bot/monitor. `/opt/kaskad` и снимки в `/var/backups/kaskad` оставить.

### 5.7 Тесты (`tools/k3_install_test.sh`)

Без root и сети, с поддельными `iptables`, `systemctl`, `kaskadctl`, `nft` в PATH и
`K3_OPT`/`KASKAD_DIR`/`KASKAD_ROOT` во временном каталоге:

- сборка блока и распаковка в /opt (`k3_ensure_lib`), повторный вызов ничего не делает, порча
  `.ok`/архива ловится;
- диспетчеризация: legacy → iptables-путь; nft → вызовы `kaskadctl` с верными аргументами; код 3 →
  вопрос и повтор с `--force`;
- перенос бесплатных правил в legacy с двумя правилами на порт → остаётся первое;
- `--k3-post-update` переписывает пути юнитов и не трогает чужие юниты;
- self_update: выбор ветки, запрет понижения, проверка `esac` в хвосте нового файла;
- хвост install.sh: `tail -3 install.sh | grep -q '^esac'`.

`tools/free_rules_adopt_test.sh` дополнить случаем двух правил на порт.

## 6. Общие правила для всех пакетов

- Python ≥ 3.8, только стандартная библиотека. `bash tools/k3_test.sh` зелёный (там же проверка
  совместимости с 3.8).
- Команды — только через `runner` (`kaskad/sysrun.py`), списками аргументов, без `shell=True` и
  `eval`. В тестах — `FakeRunner`; ни одного неподдельного вызова (`runner.unmatched == []`).
- Пути — только через `kaskad/paths.py` (тесты подменяют `KASKAD_ROOT`).
- Тексты для людей — по-русски, коротко и конкретно, нейтрально (не «обход блокировок»). Секреты
  (токен бота, ключ GitHub, ключи выхода) не печатать нигде: `util.redact` на всё, что идёт в журнал.
- Общие файлы (`__init__`, `__main__`, `cli`, `errors`, `paths`, `sysrun`, `util`, `state`,
  `tests/helpers.py`, `tests/test_state.py`, `tools/k3_test.sh`, `tools/k3_py_compat.py`, эта
  спецификация) **не правят** пакеты. Нужна правка — опишите её в отчёте.
- Контракты-заглушки `nft.py`, `compat.py`, `units.py` реализует только WP1; WP2 их вызывает и в
  своих тестах подменяет (`unittest.mock.patch`).
- Коммиты — в своей рабочей копии, сообщение `k3 WPn: …`. Ничего не пушить.

## 7. Проверка на живых серверах (делает координатор после слияния пакетов)

Стенд: узел в РФ (Debian 12 и Ubuntu 24.04), цель проброса за рубежом (WireGuard/AWG-сервер +
VLESS на 443), клиент. Сценарии:

1. Чистая установка 3.0.1 → новый движок сразу; проброс udp и tcp; трафик идёт; счётчики растут.
2. Бесплатный Kaskad v1.1: 3 правила, из них два на один порт → переезд → работает первое, как было;
   перезагрузка; повторный запуск бесплатного скрипта поверх → сторож возвращает команду, меню
   предлагает дозабрать.
3. Kaskad PRO 2.11 и 2.13 (с ботом, мониторами, egress из VLESS + AWG) → обновление пунктом 11 и через
   goInstaller → переезд → бот и egress живы, пробросы работают; перезагрузка.
4. PRO 2.3 (DNAT без метки) → переезд.
5. UFW активен (FORWARD DROP) и Docker с опубликованным портом → пробросы работают, конфликт порта
   Docker показан.
6. Откат: автоматический (подложить сбой проверки) и ручной «Вернуть iptables», затем «Вернуться на
   2.13» → 2.13 видит и ведёт пробросы.
7. `iptables -F`, `nft flush ruleset`, `ufw reload` → сторож восстанавливает за минуту.
8. Нагрузка: iperf3 через проброс до и после переезда (1 vCPU), CPU сторожа.
