# Kaskad 3 · волна 2: шлюз со всеми протоколами

Версия: 2026-10-04. Основа — волна 1 (`docs/k3/SPEC-wave1.md`: state.json, движок nft, переезд).
Решения владельца на 04.10.2026:

- сразу все протоколы: WireGuard, AmneziaWG (три линии, как три контейнера goAmnezia: `awg1` 1.0/1.5,
  `awg2` 2.0, `awg3` 3.1), L2TP, L2TP/IPsec, IKEv2, SSTP, PPTP, OpenVPN, OpenConnect, SOCKS5;
- протоколы («ядра») ставятся по выбору, как в goAmnezia: мастер первой установки предлагает три основных
  (WireGuard, AmneziaWG, L2TP/IPsec) отмеченными, остальные — неотмеченными; всё можно доставить или убрать
  потом в «Настройки → Протоколы»;
- системы: Debian 12+ (вкл. 13) и Ubuntu 24.04+ (вкл. 26.04). На остальных шлюза нет.

Факты, проверенные на живых серверах Aeza 04.10.2026 (Debian 12 ядро 6.1.0-26, Ubuntu 24.04 ядро
6.8.0-48, 1 vCPU, 2 ГБ): все нужные модули грузятся (ppp_generic, ppp_mppe, pptp, l2tp_ppp, gre, tun,
wireguard, esp4, xfrm_user, nf_tables, nft_tproxy, nf_flow_table, xt_connmark); accel-ppp 1.14.0
собирается из исходников за 30–33 с (+40 с зависимости), 1,6 МБ; strongSwan 5.9.8/5.9.13, ocserv
1.1.6/1.2.4, OpenVPN 2.6.14/2.6.19 (DCO в ядре нет), dnsmasq 2.90/2.91 с поддержкой nftset; пакеты
запускают свои службы сразу при установке (strongSwan занимает UDP 500/4500, dnsmasq — 53, ocserv падает
без настройки); на Debian 12 нет iptables; `apt install iptables-persistent` на Ubuntu удаляет ufw.

---

## 1. Как идёт трафик

```
устройство ──(протокол)──► вход на узле ──► адрес клиента 10.77.x.y на интерфейсе входа
                                           │
                     nft gw_pre (mangle −150): метка маршрута по адресу клиента
                                           │
       ┌──────────────────────┬────────────┼──────────────────┬──────────────┬──────────────┐
  метка 256+N            метка 0x200   метка 0x300+N       метка 0x201     метка 0x2ff    без метки
  быстрый путь:          TPROXY :12345 TPROXY :12400+N     напрямую        блок           (части gw_*
  таблица 256+N → kawgN  → xray, пул   → xray, выход       (main, masq)    (drop)         нет) → таблица
  (AmneziaWG, ядро)      VLESS (балан- VLESS N напрямую                                   199: unreachable
                         сировщик)     (gx-<id>)
```

- Всё, что можно, решает ядро: nft-карта «адрес клиента → метка», `ip rule fwmark → table`. xray нужен
  только для выходов VLESS (и как вход SOCKS5).
- Метка выбирается на первом пакете соединения и запоминается в ct mark: живое соединение не
  перескакивает при смене маршрута; смена выхода — замена элемента карты, без перезапусков (и для
  выходов VLESS: у каждого свой порт TPROXY и вход xray, §1.3).
- Нет живого выхода — блок, а не «напрямую» (`fail = block` по умолчанию; `direct` — только явным
  выбором маршрута).
- Fail-closed и без цепочек `gw_*` (§1.6): непомеченный пакет клиента находит в маршрутизации только
  `unreachable`; входы не стартуют без части шлюза в таблице.
- Клиенты не видят друг друга и не ходят в частные, CGNAT, link-local и multicast-сети (сеть хостера, его
  шлюз, метаданные облака); устройство с маршрутом «блок» получает только DNS 10.77.0.1:53.
- IPv6 от клиентов шлюза не пропускаем (адресов IPv6 не выдаём, forward из интерфейсов шлюза в ip6 — drop;
  DNS шлюза убирает ответы AAAA, §1.4).
- MSS: `tcp flags syn tcp option maxseg size set rt mtu` для соединений из интерфейсов шлюза.

### 1.1 Метки (meta mark / ct mark)

| Значение | Что |
|---|---|
| `256+N` (0x100–0x1ff) | AmneziaWG-выход N пула (как у egress 2.x: `ip rule fwmark 256+N table 256+N`, точное совпадение — в meta mark ничего лишнего) |
| `0x200` | путь «пул» через xray шлюза (TPROXY 127.0.0.1:12345): балансировщик по живым выходам VLESS |
| `0x300+N` (0x301–0x3ff) | выход VLESS строки N пула (маршрут `exit:<id>`): TPROXY 127.0.0.1:12400+N, вход xray `tproxy-<id>` → `gx-<id>`; `ip rule fwmark 0x300/0xff00 lookup 200` |
| `0x201` | напрямую (main) |
| `0x202` | к самому узлу (его службы, пробросы волны 1): ставит gw_pre на `fib daddr type local`; в gw_route не бывает |
| `0x2ff` | блок |
| ct mark: старший байт `0x4b` | соединение Kaskad (пробросы волны 1 и шлюз); младшие 16 бит — метка маршрута (0 — соединение возникло без gw_pre: gw_fwd режет) |

Правило совместимости волны 1 (`kaskad:compat`, connmark 0x4b000000/0xff000000) пропускает и шлюз.

### 1.2 Таблица (дополнение к `ip kaskad`, имена `gw_*`, рендерит ядро шлюза)

```
set gw_nets  { type ipv4_addr; flags interval; }        # база (10.77.0.0/16)
set gw_ru    { type ipv4_addr; flags interval; }        # «Россия напрямую» (geoip, волна 3), пока пустой
set gw_live  { type ipv4_addr; }                        # адреса, которым сейчас можно наружу (не блок)
set gw_priv  { type ipv4_addr; flags interval; }        # 0/8, 10/8, 100.64/10, 127/8, 169.254/16, 172.16/12,
                                                        # 192.168/16, 224/3
map gw_route { type ipv4_addr : mark; }                 # адрес клиента → метка маршрута
chain gw_pre { type filter hook prerouting priority -150; policy accept;
    iifname "lo" return                                              # пакеты самого узла
    iifname "kgw-*" ip saddr != @gw_nets drop                        # из интерфейсов входов — только адреса базы
    iifname "kgw-*" fib saddr . iif oif missing drop                 # ... и свои (строгий обратный путь: PPP)
    ip saddr 10.77.1.0/24 iifname != "kgw-wg" drop                   # подсеть входа — только с его интерфейсов
    ip saddr 10.77.5.0/24 iifname != "kgw-l2tp*" iifname != "kgw-sstp*" drop     # PPP: один пул
    ip saddr 10.77.8.0/24 meta ipsec missing drop                    # IKEv2: только из IPsec
    ip daddr @gw_nets iifname != "kgw-*" meta ipsec missing drop     # база закрыта снаружи (сеть хостера)
    ip saddr != @gw_nets return
    ip daddr 10.77.0.1 meta l4proto { tcp, udp } th dport 53 ct mark set ct mark or 0x4b000000 return
    ip saddr != @gw_live drop                                        # блок/неизвестный: выше был только DNS
    fib daddr type local meta mark set 0x202 ct mark set ct mark or 0x202 return     # сам узел, пробросы
    ip daddr @gw_nets return                                         # другие клиенты: режет gw_fwd
    ct state established,related ct mark and 0xffff == 0x200 meta l4proto udp meta mark set 0x200
        tproxy to 127.0.0.1:12345 accept                             # UDP через xray (и для каждого 0x300+N)
    ct state established,related meta mark set ct mark and 0xffff return
    meta mark set 0x2ff                                              # промах карты — блок
    meta mark set ip saddr map @gw_route
    ip daddr @gw_ru meta mark != 0x2ff meta mark set 0x201           # волна 3
    ct mark set meta mark or 0x4b000000
    meta mark 0x200 meta l4proto { tcp, udp } tproxy to 127.0.0.1:12345
    meta mark 0x301 meta l4proto { tcp, udp } tproxy to 127.0.0.1:12401 }       # по правилу на выход VLESS
chain gw_fwd  { type filter hook forward priority -4; policy accept;
    ip saddr @gw_nets meta mark 0x2ff drop
    ip saddr @gw_nets ct mark and 0xffff == 0x2ff drop
    ip saddr @gw_nets ct mark and 0xffff == 0x200 drop               # путь xray в forward: нет таблицы 200
    ip saddr @gw_nets ct mark and 0xff00 == 0x300 drop               # то же для выходов VLESS
    ip saddr @gw_nets ct mark and 0xff00 == 0x100 oifname != "kawg*" drop     # нет ip rule ноги
    ip saddr @gw_nets ip daddr != @gw_nets ct mark and 0xffff == 0x0 drop    # соединение без gw_pre
    ip daddr 10.77.0.1 drop
    ip saddr @gw_nets ip daddr @gw_nets drop                         # клиент → клиент
    ip saddr @gw_nets ip daddr @gw_priv ct mark and 0xffff != 0x202 drop     # частные сети (кроме пробросов)
    ip saddr @gw_nets tcp flags syn tcp option maxseg size set rt mtu }
chain gw_out  { type filter hook output priority -4; policy accept;
    meta mark and 0xffffff00 == 0x100 oifname != "kawg*" drop        # AWG-выходы xray без ноги
    meta skuid <uid kaskad-xray> ip daddr 127.0.0.0/8 ct direction original meta l4proto { tcp, udp }
        th dport != 53 drop }                                        # xray шлюза: на loopback только DNS
chain gw_post { type nat hook postrouting priority 100; policy accept;
    ip saddr @gw_nets oifname != "lo" ip daddr != @gw_nets masquerade }
```

Плюс `table ip6 kaskad6 { chain gw_fwd6 { type filter hook forward priority -4; iifname <входы> drop }
chain gw_out6 { type filter hook output priority -4; meta skuid <uid> ip6 daddr ::1 ct direction original
meta l4proto { tcp, udp } th dport != 53 drop } }`.

Все интерфейсы входов называются `kgw-…` (на этом держатся правила `kgw-*`; иное имя адаптера `gw check`
показывает как проблему). Адаптер, не назвавший интерфейсы, получает `ip saddr <подсеть> drop`.

**Проверено на стенде 04.10.2026** (`k3/stand/gwcheck.sh`, Debian 12 и Ubuntu 24.04): исходная таблица
шлюза (без строк антиспуфинга, gw_priv, 0x202, 0x300+N, skuid) принимается ядром; элемент карты
переключает путь (напрямую / быстрый путь через таблицу 256 / TPROXY с видимой исходной целью / блок; нет
адреса в карте — блок); мёртвая нога закрыта `unreachable` и не течёт напрямую; живое соединение
остаётся на своём пути после смены карты (ct mark), новое идёт по новому. Новые конструкции (2026-10-04:
`meta ipsec missing`, `fib saddr . iif oif missing`, `meta skuid`, `ct direction`, набор gw_priv, второй
порт TPROXY) проверяет `nft -c` при `gw enable` (`net.probe`); на стенде — по списку проверок отчёта.

**Рядом с UFW и Docker** (там политики iptables FORWARD и INPUT = DROP, а DROP в любой базовой
цепочке побеждает наш accept):

- FORWARD — правило совместимости волны 1 (`kaskad:compat`, connmark 0x4b000000/0xff000000) уже
  пропускает трафик шлюза: gw_pre ставит метку K всем соединениям клиентов;
- INPUT — второе такое же правило `kaskad:compat-in` (тот же connmark): соединения, перехваченные
  TPROXY (путь 0x200), заканчиваются в INPUT;
- DNS шлюза: в gw_pre **до** `ip saddr != @gw_live drop` — `ip daddr 10.77.0.1 meta l4proto { tcp, udp }
  th dport 53 ct mark set ct mark or 0x4b000000 return` (тогда `kaskad:compat-in` пропустит DNS);
  к остальным службам узла клиент ходит как обычный внешний адрес (UFW решает сам: метка 0x202 без
  байта 0x4b) — кроме устройств с маршрутом «блок»;
- порты входов из интернета — `ufw allow <порт>/<proto> comment 'kaskad-gw <вход>'`, если UFW активен
  (для PPTP — ещё GRE, для L2TP/IPsec и IKEv2 — UDP 500/4500 и ESP); убираются при удалении входа.

Точный синтаксис ядро шлюза проверяет `nft -c` и на стенде (`k3/stand/nftcheck.sh`, `gwcheck.sh` — образцы).

### 1.3 Выходы

- Пул — тот же `/etc/kaskad/egress/upstream.txt` («Прокси для API Telegram»), разбор — тем же
  `egress_parse.py` (подключать по пути `/etc/kaskad/egress/egress_parse.py` через importlib; если файла
  нет — `gokaskad --egress-ensure-parser`). ID выхода = первые 10 знаков sha1 строки пула (номер строки
  меняется при удалении, ID — нет). С 3.3.5 ключ, который подписка отдала под другой строкой (другое имя после
  «#» или параметры; тот же сервер и учётные данные — `exits.identity`), сохраняет ID выхода: карта «ids» в
  `subs.json` (`exits.pool_id`), install.sh по-прежнему знает строку по sha1 её текста.
- «Тот же ключ» — одно понятие `exits.peer_key` (= `exits.identity`, в `subs.json` — его хэш `identity_hash`); им же
  проверяются дубли (ядро, подписки, `egress_parse.py peers` в install.sh). VLESS — `vless|uuid|host:port`;
  AmneziaWG (3.3.5 №4) — `awg|<ключ сервера>|<открытый ключ клиента из закрытого>`, без адреса: тот же клиент по IP и
  по домену — один ключ («этот ключ уже есть как e-…, по другому адресу»), разные люди одного сервера — разные
  выходы. Две копии одного ключа, оставшиеся с 3.3.4, помечаются `same_peer`/`same_how` («ip_domain» | «address» |
  «text») и `twin` (`gw exits`, панель, «Проверка сервера») и не удаляются сами; маршруты удаляемой копии переходят
  на оставшуюся (`gw exit-handover`, REV-335-2; подробно — SPEC-subs-backup §1 «Две копии одного ключа»).
- AmneziaWG-выходы — интерфейсы `kawgN` и таблицы `256+N` (их поднимает `gokaskad --egress-awg-up`,
  идемпотентно; fail-closed `unreachable default metric 1000` уже есть с 2.11). Шлюз добавляет только
  masquerade на `kawg*` для адресов клиентов (в gw_post это уже покрыто).
- Ноги держит целыми сторож шлюза через `gokaskad --egress-awg-heal`: пропавшая нога (упал amneziawg-go)
  поднимается одна, у ноги с живым интерфейсом правило `fwmark 256+N`, `default dev kawgN` и `unreachable`
  возвращаются на месте; работающие ноги не пересоздаются (`--egress-awg-up` пересоздаёт все — это подъём
  «Прокси для API Telegram» целиком, шлюз его не зовёт). Перезапуск systemd-networkd (каждое обновление
  пакета systemd) стирает чужие ip rule и маршруты без устройства — поэтому вместе с маршрутизацией ног и
  шлюза пишется `/etc/systemd/networkd.conf.d/90-kaskad.conf` (`ManageForeignRoutingPolicyRules=no`,
  `ManageForeignRoutes=no`; networkd сами не перезапускаем), убирается, когда ни ног, ни правил Kaskad не
  осталось (стенд Ubuntu 26.04, 2026-10-04).
- VLESS-выходы и «пул целиком» — отдельный xray шлюза `kaskad-gw-xray.service` (не трогаем xray бота):
  вход `tproxy-in`/dokodemo-door с `tproxy` на 127.0.0.1:12345 (tcp+udp, без sniffing — см. IPOnDemand
  ниже) → балансировщик по живым VLESS-ногам (observatory leastPing, как у egress); у каждого
  выхода VLESS строки N — свой вход `tproxy-<id>` на 127.0.0.1:12400+N → прямо в `gx-<id>` (метка
  0x300+N); AWG-ноги — `freedom` с `sockopt.mark = 256+N`; ip rule `fwmark 0x200 lookup 200`,
  `fwmark 0x300/0xff00 lookup 200` + `local 0.0.0.0/0 dev lo table 200`. Маршрут устройства на выход
  VLESS — только элемент `gw_route` (правил по адресу источника в xray нет): смена маршрута не
  перезапускает xray, динамические адреса IKEv2 работают как статические. xray перезапускается только при
  смене пула (выходы, их включение) и учёток/маршрутов SOCKS5.
- xray шлюза закрывает клиентам (TPROXY-входы и SOCKS5) частные адреса: правило **до** правил
  пользователей — 127/8, 0/8, 10/8, 172.16/12, 192.168/16, 100.64/10, 169.254/16, 224/3, ::1, fc00::/7,
  fe80::/10, адреса самого узла и `full:localhost` → `block`; `routing.domainStrategy: IPOnDemand`
  (не IPIfNonMatch: тот разрешает имя, только если ни одно правило не подошло, а правило пользователя
  SOCKS5 подходит всегда), так что и имя, указывающее на частный адрес, закрыто. IPOnDemand проверяет
  адреса ИМЕНИ цели, в том числе подсмотренного sniffing'ом (SNI, Host), вместо настоящего адреса —
  поэтому у входов клиентов (TPROXY и SOCKS5) sniffing выключен: ни одно наше правило не маршрутизирует
  их по домену, а поддельный SNI открывал бы частный адрес. Свой DNS xray (`dns`,
  `tag: gw-dns-int`, только IPv4): 1.1.1.1 и 1.0.0.1 **через пул** (правило `inboundTag: gw-dns-int` →
  балансировщик пула, запасной — block; пула нет — `direct`), `localhost` (резолвер узла) — только для
  имён VLESS-серверов пула и хоста пробы observatory (`skipFallback`: для остальных имён — никогда).
- xray шлюза работает от системного пользователя `kaskad-xray` (создаётся при `gw enable`/`apply`):
  `AmbientCapabilities=CAP_NET_ADMIN CAP_NET_BIND_SERVICE` (тот же набор в CapabilityBoundingSet),
  `NoNewPrivileges=yes`; программа — копия `/opt/kaskad/gw/xray` (каталог /etc/kaskad закрыт для всех,
  кроме root); конфиг остаётся 0600 root и попадает в службу через `LoadCredential=xray.json:…`
  (`ExecStart=… run -c %d/xray.json`: личная копия, читает только служба). gw_out/gw_out6: сокеты этого
  пользователя не открывают ничего на 127.0.0.0/8 и ::1, кроме DNS (53).
- Состояние выхода для людей (`gw exits`, `gw status`, меню): «работает» / «не работает: <причина>» / «ещё не
  проверялся» — до первой настоящей проверки (нет записи в снимке или были только ответы «неизвестно»: мастер,
  шлюз ещё не включён), а не «не работает» (поле `state`: alive | dead | unknown).
- Сторож выходов (раз в 10 с, в сборщике): живость AWG-ноги (`awg show latest-handshakes`, проба через
  ногу), VLESS — по observatory. Ответ «xray не отвечает», «xray ещё не проверил» и «недоступен» в первые
  30 с после (пере)запуска xray — НЕИЗВЕСТНО: состояние выхода не меняется (неизвестно дольше 60 с — это
  неудачная проверка). С 3.3.5 правило «упал» другое: за последние 2 минуты не удалась хотя бы половина
  проверок (их не меньше четырёх), одинаково для AWG и VLESS; оживает после двух удачных подряд (`gw/exits.py`,
  README 3.3.5; было — «после 2 неудачных проверок подряд», оживал с первой удачной). В журнал — только переходы. Нога умерла → клиенты её маршрута получают
  в `gw_route` метку другой живой ноги того же маршрута (или 0x200 → пул), а при политике «только этот
  выход» — блок.
- Распределение по выходам маршрута «пул»: клиент → нога по rendezvous-hash (стабильно, при смерти ноги
  переезжают только её клиенты); победила нога VLESS — метка 0x200 (балансировщик xray).
- Метки привязаны к НОМЕРУ строки пула (kawgN, 256+N; VLESS — 0x300+N), а не к выходу: удалённый ключ сдвигает
  строки после него (и замена пула меняет все). Поэтому install.sh после каждой пересборки пула
  (`egress_rebuild` — ноги уже подняты заново — и `egress_disable`) при включённом шлюзе сразу зовёт
  `kaskadctl gw apply` (`k3_gw_pool_changed`), не дожидаясь сборщика (до 10 с). Ядро (gw apply и сборщик, кто
  увидит первым) сравнивает прежний пул снимка с новым (`exits.renumbered`: строки, где выход сменился или
  пропал), после nft.apply сбрасывает соединения этих строк — `conntrack -D --mark 0x4b00XXXX/0xff00ffff` для
  256+N и 0x300+N (`net.flush_marks`): соединения, ушедшие под старой меткой, иначе так и остались бы на
  чужом выходе (ct mark). Ключ, добавленный в конец, строк не сдвигает — ничего не сбрасывается. В журнал —
  «GW exits: строки пула … сменили выход — соединения клиентов через них сброшены (N)».

### 1.4 DNS

- Адрес шлюза `10.77.0.1/32` на dummy-интерфейсе `kgw0`; всем клиентам выдаётся DNS = 10.77.0.1.
- dnsmasq (пакет дистрибутива, своя служба `kaskad-dns.service`, конфиг в `/etc/kaskad/gw/dnsmasq.conf`):
  `bind-interfaces`, `listen-address=10.77.0.1` (только этот адрес, не 127.0.0.1), `no-resolv`, кэш; так он
  не мешает systemd-resolved на Ubuntu и чужому DNS на 53. Пакетная служба `dnsmasq.service` — выключена и
  замаскирована.
- `filter-AAAA` (dnsmasq 2.89+; Debian 12 — 2.89, Ubuntu 24.04 — 2.90): клиенты PPP/IKEv2/OpenConnect с
  IPv6 своей сети иначе ходили бы мимо туннеля. Версия проверяется при каждом apply (`dnsmasq --version`);
  старее — без фильтра, `gw status` говорит об этом.
- Верхний DNS: если в пуле есть выходы — `server=127.0.0.1#5353` (dns-вход xray шлюза → 1.1.1.1:53 через
  пул); прямой `server=1.1.1.1`/`1.0.0.1` — только пока у какого-то активного устройства (не SOCKS5)
  действующий маршрут «напрямую» или политика отказа «напрямую»; вместе с пулом — `strict-order` (пул
  первым, прямой — только если пул не ответил). Ни пула, ни прямых маршрутов — верхнего DNS нет: DNS
  закрыт (fail-closed); с пулом без живых выходов — тоже.
- Доменные правила (волна 3): dnsmasq `nftset=/домены/4#ip#kaskad#gw_ru` — адреса российских доменов
  попадают в набор, путь решает ядро.

### 1.5 Адреса

`10.77.0.0/16` (если пересекается с маршрутами узла — первый свободный из `10.78/16`, `10.79/16`,
`172.27/16`; выбор сохраняется в state). Подсети:

| Вход | Подсеть | Интерфейс |
|---|---|---|
| WireGuard | 10.77.1.0/24 | `kgw-wg` |
| AmneziaWG 1.0/1.5 · 2.0 · 3.1 | 10.77.2.0/24 · .3.0/24 · .4.0/24 | `kgw-awg1` · `kgw-awg2` · `kgw-awg3` |
| PPP: L2TP, L2TP/IPsec, PPTP, SSTP (один пул) | 10.77.5.0/24 | `kgw-l2tp*` · `kgw-pptp*` · `kgw-sstp*` (accel-ppp) |
| IKEv2 | 10.77.8.0/24 | xfrm (политики, без интерфейса) |
| OpenVPN UDP · TCP | 10.77.9.0/24 · .10.0/24 | `kgw-ovpn-u` · `kgw-ovpn-t` |
| OpenConnect | 10.77.11.0/24 | `kgw-oc*` (ocserv) |
| SOCKS5 | — (прокси) | — |

`.1` каждой подсети — узел. Устройству адрес выдаётся один раз на вход и не меняется: `.10` и дальше по
порядку. IKEv2 — из пула strongSwan, адрес попадает в `gw_route` хуком updown (§3.6).

### 1.6 Fail-closed без части шлюза в таблице

Часть `gw_*` может пропасть (`nft flush ruleset`, не загрузившийся при старте kaskad.nft, чужой
скрипт). Тогда клиенты не должны уйти напрямую:

- **Аварийный выключатель в маршрутизации** (не зависит от nft; таблица **199**, свободна: 200 — TPROXY,
  220 — strongSwan, 256–511 — ноги AmneziaWG):
  `ip rule pref 31998 from <база> to <база> lookup main`,
  `ip rule pref 31999 from <база> fwmark 0x0/0xffff lookup 199`, в таблице 199 `unreachable default`.
  gw_pre метит каждый пакет клиента, который уходит с узла (метки маршрута, 0x202 — к самому узлу и
  его пробросам: после DNAT пакет маршрутизируется заново); непомеченный пакет клиента находит только
  `unreachable`. Пакеты к адресам узла (таблица local, pref 0) и между адресами базы (31998: ответы
  dnsmasq и служб узла клиентам) не затронуты; помеченные пакеты обходят оба правила, поэтому правила
  меток (ноги 0x1xx, TPROXY 0x200/0x300) работают при любом своём pref. Держит сторож (`gw check
  --repair`, сборщик каждые 10 с), снимает `gw disable`; при смене базы правила прежней базы убираются.
  **Ответы клиентам и rp_filter.** На Ubuntu `rp_filter=2`: ядро проверяет источник ответа поиском «от
  клиента к источнику» без метки — такой поиск попадал в выключатель, и ответ отбрасывался (стенд, Ubuntu
  26.04: «напрямую» и выходы AmneziaWG не работали, счётчик `IPReversePathFilter` рос). Поэтому первое
  правило gw_pre метит ответы соединений шлюза (`ct direction reply ct mark and 0xffff != 0x0 meta mark
  set meta mark or 0x10000`), `ip rule pref 31995 fwmark 0x10000/0x10000 lookup main` ведёт их в main, а
  `net.ipv4.conf.all.src_valid_mark=1` (как у wg-quick) даёт проверке источника метку пакета. Ответы
  пробросов волны 1 (младшие биты ct mark — 0) и ответы xray (локальные) не затронуты. С `src_valid_mark=1`
  проверка источника пакета клиента с меткой TPROXY (0x200, 0x3xx) смотрит и в таблицу 200, где `local
  default` делает адрес клиента «своим» (martian, отброс): поэтому в таблице 200 ещё `throw <база>` —
  такой поиск уходит дальше, в main.
- **gw_fwd**: соединение клиента без бит маршрута в ct mark (возникло, пока gw_pre не было) — drop;
  когда сторож или сборщик возвращает часть шлюза, соединения клиентов забываются: `conntrack -D -s
  <база>` (пакет `conntrack` ставит `gw enable` вместе с dnsmasq-base под policy-rc.d; нет conntrack —
  строка в журнале, соединения режет gw_fwd).
- **Порядок apply**: при `gw enable`, `gw apply` и любом изменении, когда части шлюза нет в живой
  таблице, — сначала таблица (nft.apply) и маршрутизация, потом адаптеры (входы), потом снова таблица
  (адреса, которые адаптеры выдали) и маршрутизация.
- **Юниты входов**: drop-in `/etc/systemd/system/<юнит>.d/kaskad-gate.conf` с
  `ExecStartPre=+/bin/sh -c 'exec nft list chain ip kaskad gw_pre >/dev/null'` (у xray шлюза — та же
  строка в его юните); `After=kaskad-net.service` остаётся. Drop-in, а не строка в тексте юнита: адаптеры
  перезапускают службу, когда меняется её юнит, — обновление не рвёт сессии, строка действует со
  следующего старта. Сторож не (пере)запускает входы и xray, пока части шлюза нет (сначала шаги 1–2
  `net check` возвращают таблицу).

## 2. Модель данных (state.json → `gateway`, секреты отдельно)

```json
"gateway": {
  "enabled": true,
  "net": {"base": "10.77.0.0/16", "dns": "10.77.0.1"},
  "inbounds": {
    "wg":    {"enabled": true,  "port": 51820, "subnet": "10.77.1.0/24", "pub": "…"},
    "awg1":  {"enabled": true,  "port": 31871, "subnet": "10.77.2.0/24", "line": "1.5", "params": {…}, "pub": "…"},
    "awg2":  {"enabled": true,  "port": 41023, "subnet": "10.77.3.0/24", "line": "2.0", "params": {…}, "pub": "…"},
    "awg3":  {"enabled": true,  "port": 51911, "subnet": "10.77.4.0/24", "line": "3.1", "params": {…}, "pub": "…"},
    "l2tp":  {"enabled": true,  "ipsec": true, "plain": false},
    "pptp":  {"enabled": false},
    "sstp":  {"enabled": false, "port": 4433},
    "ikev2": {"enabled": false, "subnet": "10.77.8.0/24", "cert": "self"},
    "openvpn": {"enabled": false, "udp_port": 1194, "tcp_port": 0},
    "ocserv":  {"enabled": false, "port": 4443},
    "socks5":  {"enabled": false, "port": 1080, "udp": true}
  },
  "users":   [{"id": "u-…", "name": "Мама", "route": null, "enabled": true, "added": "…"}],
  "devices": [{"id": "d-…", "user": "u-…", "name": "Роутер на даче", "kind": "router",
               "platform": "keenetic", "route": null, "enabled": true, "added": "…",
               "login": "dacha-7f3a",
               "ips": {"wg": "10.77.1.10", "awg2": "10.77.3.10", "ppp": "10.77.5.10",
                       "ovpn_u": "10.77.9.10", "ocserv": "10.77.11.10"},
               "access": ["awg2", "l2tp"]}],
  "routes": {"default": {"via": "pool", "fail": "block"}},
  "exits":  {"e-3fa9c1d2b4": {"enabled": true, "name": "Вена"}}
}
```

- `kind`: `phone|tablet|laptop|pc|tv|router|other`; `platform`: `ios|android|androidtv|windows|macos|linux|
  keenetic|mikrotik|openwrt|tplink|huawei|xiaomi|asus|other`.
- `access` — какими входами пользуется устройство (могут быть несколько). Общий пароль устройства
  (`dev.<id>.password`) — у входов, где он идёт внутри TLS или IPsec: L2TP/IPsec, SSTP, IKEv2, OpenConnect.
  Слабым и открытым входам — свои учётки (2026-10-04): PPTP (MS-CHAPv2 без TLS вскрывается по перехваченному
  рукопожатию) — логин `<login>-pptp` и `dev.<id>.pptp.password`; SOCKS5 (пароль открытым текстом) — логин
  устройства и `dev.<id>.socks5.password`. Пароль, пойманный там, ничего больше не открывает. Ключи — свои на
  каждую линию WG/AWG; OpenVPN — свой сертификат устройства (`dev.<id>.ovpn.*`). Устройства, заведённые
  раньше, получают свои пароли PPTP/SOCKS5 при следующем apply; выдача PPTP/SOCKS5 говорит, что учётки
  теперь свои (настроенное раньше с общим логином/паролем — заменить).
- `gw device reset <dev> [--inbound K]` — новые ключи, пароли и сертификаты устройства (все входы или один):
  секреты удаляются, адаптеры делают новые в том же apply; адаптеры рвут сессии этого устройства, у которых
  сменились учётные данные (accel-ppp — по строке chap-secrets, strongSwan — по секрету EAP, ocserv — по хэшу,
  OpenVPN — CRL и kill, WG/AWG — старый пир уходит из syncconf, SOCKS5 — новая учётка, перезапуск xray
  шлюза). Сессии других устройств не трогаются (кроме SOCKS5/TPROXY через xray: у xray нет перечитывания
  конфига). Сброс L2TP/SSTP/IKEv2/OpenConnect меняет общий пароль у всех этих входов (`also` в ответе). Меню:
  «Пользователи и устройства → Сменить ключи и пароли устройства».
- Маршрут: `device.route` → `user.route` → `routes.default`. Значения `via`: `pool`, `exit:<id>`,
  `direct`, `block`; `fail`: `block|direct`.
- Секреты (`/etc/kaskad/secrets.json`, 0600, только через `kaskad.secrets`): приватные ключи серверов и
  устройств WG/AWG, пароли устройств, PSK L2TP/IPsec, CA и ключи OpenVPN/ocserv/IKEv2. В state.json —
  только открытые ключи и ссылки. Секреты не печатаются никогда, кроме экрана выдачи самому владельцу.

## 3. Входы (адаптеры)

Каждый вход — модуль `kaskad/gw/inbounds/<kind>.py`, класс с одним интерфейсом:

```python
class Inbound:
    kind = "wg"                 # wg awg1 awg2 awg3 l2tp pptp sstp ikev2 openvpn ocserv socks5
    engine = "wireguard"        # что ставим (строка для экрана «Протоколы»)
    title = "WireGuard"         # для людей
    def requirements(self, runner) -> dict    # пакеты, модули ядра, порты, supported/why
    def install(self, runner) -> None         # пакеты без автозапуска, сборка, юниты (идемпотентно)
    def uninstall(self, runner) -> None       # остановить, убрать юниты и конфиги, пакеты не удалять
    def render(self, st, sec) -> dict         # {путь: (текст, режим)} — конфиги из state + secrets
    def apply(self, st, sec, runner) -> None  # записать render, перезагрузить службу мягко (без обрыва чужих сессий, где можно)
    def status(self, runner) -> dict          # running, port, sessions: [{device, ip, since, rx, tx}]
    def issue(self, st, sec, device, os) -> dict  # выдача под ОС (§4)
    def ifaces(self) -> list                  # шаблоны iifname для nft
```

Общие правила установки пакетов: `apt-get install` под временным `/usr/sbin/policy-rc.d`, который
возвращает 101 (службы дистрибутива не стартуют); пакетные юниты, которые мы не используем, —
`systemctl disable --now` + `mask`; запускаем только свои юниты `kaskad-gw-<kind>.service`.
apt занят (свежий сервер: unattended-upgrades) — ждём до 10 минут со строкой «Система ставит обновления
(apt занят) — жду до 10 минут…», не беря блокировку (fcntl F_GETLK), потом честная ошибка с именем процесса
(`kaskad/aptlock.py`; install.sh — `k3_apt_get`). Одна установка Kaskad за раз (flock `/run/kaskad/apt.lock`);
SIGHUP/SIGTERM во время apt не оставляют policy-rc.d (apt доделывается, файл убирается, установка
прерывается); что оставил SIGKILL или обрыв питания, убирает сторож `net check` (наш файл — по метке внутри,
когда apt и dpkg свободны).

Меню и мастер (install.sh) о занятом apt говорят **до** установки (стенд, свежая Ubuntu 26.04:
unattended-upgrades ставил обновления больше 10 минут, мастер всё это время печатал точки под обещанием
«до двух минут», потом ✗ и шёл дальше без L2TP):
- что установка поставит через apt, говорит ядро: `packages` в `gw inbound plan` (по входу; PPP — средства
  сборки, пока accel-ppp не собран; уже стоящие пакеты не в счёт) и в `gw probe` (самому шлюзу: dnsmasq-base,
  conntrack). Нужны пакеты и apt занят (`k3_apt_holder`) — вопрос: «Система ставит обновления (<держатель>) —
  после первого запуска сервера это обычно 5–15 минут. 1) Подождать 2) Поставить без пакетов то, что можно,
  остальное позже 0) Назад»;
- «Подождать» — до 30 минут (`K3_GW_APT_WAIT`), строка «жду: N мин из 30» обновляется на месте; Enter или
  Ctrl+C — перестать ждать (= «поставить позже»). Сам kaskadctl (CLI, бот) по-прежнему ждёт 10 минут;
- «Поставить позже» — мастер ставит протоколы без пакетов, у остальных строка «позже» и подсказка
  «Шлюз → Протоколы → <протокол> → Установить» (пункт 17); шлюзу самому нужны пакеты — не включается, подсказка
  «Шлюз → Включить шлюз». Ни один протокол не встал — шлюз не включается;
- во время установки протокола и `gw enable` строки ядра об ожидании («Система ставит обновления (apt занят) —
  жду до 10 минут…», «Kaskad уже ставит пакеты…») меню показывает сразу (сторож stderr ядра), а не точки;
  при занятом apt «accel-ppp собирается — до двух минут» не обещается. Ядро всё равно не дождалось —
  ✗ с его причиной и та же подсказка «поставить позже».

### 3.1 WireGuard (`wg`)

Ядро + `wireguard-tools`. Интерфейс `kgw-wg`, конфиг `wg setconf` (без wg-quick), пиры = устройства с
`wg` в access, `AllowedIPs = <ip>/32`. Изменение пиров — `wg syncconf` (без обрыва сессий).

### 3.2 AmneziaWG (`awg1`, `awg2`, `awg3`)

Три независимых входа, как три контейнера goAmnezia (`awg1` 1.0/1.5 — Keenetic до 5.0 и старые клиенты,
`awg2` 2.0 — Keenetic 5.1+, MikroTik через awg-proxy, AmneziaVPN; `awg3` 3.1 — приложения Amnezia).
Параметры, ключи, `.conf`, `vpn://`, QR — `kaskad/vendor/awgkeys.py` (копия `lib/awgkeys.py` из
goAmnezia pro, тот же владелец; первой строкой — коммит источника). Ядро — `amneziawg-go` + `awg` из
`bin/` этого репозитория (3.1 для amd64/arm64, как egress 2.13), интерфейсы `kgw-awg1..3`; модуль ядра
через DKMS — позже. Порты — случайные свободные UDP (как мастер goAmnezia), меняются в настройках.
`vpn://` для 3.x — обязательно (приложение Amnezia теряет поля 3.x при импорте .conf).

### 3.3 L2TP и L2TP/IPsec (`l2tp`), PPTP (`pptp`), SSTP (`sstp`) — accel-ppp

- accel-ppp 1.14.0 собирается на узле из тарбола GitHub (sha256 сверяется с зашитым значением):
  `build-essential cmake libpcre2-dev libssl-dev liblua5.1-0-dev pkg-config`, флаги
  `-DBUILD_IPOE_DRIVER=FALSE -DBUILD_VLAN_MON_DRIVER=FALSE -DRADIUS=TRUE -DSHAPER=TRUE -DLUA=TRUE`,
  `make install DESTDIR=/opt/kaskad/accel-ppp`; собранное кладётся в `/opt/kaskad/pkg/` для переустановки.
- Одна служба `kaskad-gw-accel.service`, модули по включённым входам: `l2tp`, `pptp`, `sstp`, `chap-secrets`
  (`/etc/kaskad/gw/chap-secrets`: `login * password 10.77.5.x` — один адрес устройства на все PPP-входы; у
  устройства с PPTP вторая строка `<login>-pptp` со своим паролем и тем же адресом — с `check-ip=1` вторая
  учётка, пока первая в сети, получает отказ IPCP «адрес занят»: одно устройство — один протокол за раз;
  `allow/pptp` — логины `-pptp`; хук ip-up отдаёт `<login>-pptp`, `gw hook` находит по нему устройство),
  `auth_mschap_v2`, `auth_mschap_v1`/`auth_chap_md5` (роутеры), `ippool` 10.77.5.0/24, DNS 10.77.0.1,
  `[cli] tcp=127.0.0.1:2001` + `password=` (секрет `accel.cli.password`, создаётся один раз) для
  `accel-cmd -P <пароль> show sessions`/`terminate`: каждый вызов accel-cmd даёт пароль (accel-cmd сразу
  затирает его в argv, шлёт первой строкой), в журналы он не попадает; новый пароль — reload (SIGUSR1
  перечитывает [cli]), не перезапуск.
- L2TP без IPsec — только если включено `plain` (роутеры Huawei/Xiaomi/TP-Link), с предупреждением.
- L2TP/IPsec: strongSwan, IKEv1 PSK, transport mode только для UDP/1701 (`swanctl` conn `l2tp`), PSK
  общий на узел. Windows за NAT — в инструкции `AssumeUDPEncapsulationContextOnSendRule=2`.
- PPTP: выключен по умолчанию, включается с предупреждением (слабое шифрование); GRE в фаерволе.
- SSTP: TLS на своём TCP-порту (по умолчанию 4433; 443 — только в режиме с доменом, волна 4),
  сертификат самоподписанный (`/etc/kaskad/gw/tls/`), отпечаток — в выдаче. Скрипт Windows ставит
  `SstpSvc\Parameters\NoCertRevocationCheck=1`: у самоподписанного сертификата нет списка отзыва, а настройки
  на одно подключение Windows не даёт — значение действует на все SSTP этого компьютера, выдача так и говорит.
  Linux (sstpc): логин и пароль — в файле опций pppd `kaskad-sstp.options` (`user`/`password`), команда ставит
  его `install -m 600` в `/etc/ppp/` и запускает `sstpc … file /etc/ppp/kaskad-sstp.options`: пароля нет ни в
  argv sstpc/pppd, ни в истории оболочки (`--user`/`--password` sstpc передаёт в argv pppd).

### 3.4 IKEv2 (`ikev2`) — strongSwan

`charon-systemd` + `swanctl`, конфиг `/etc/swanctl/conf.d/kaskad.conf`. Сервер — сертификат своего CA
(самоподписанный; IP-адрес в SAN), клиенты — EAP-MSCHAPv2 с логином/паролем устройства; пул
10.77.8.0/24, DNS 10.77.0.1. Предложения, которые понимают Windows 10/11, macOS/iOS и Android 12+
(встроенный клиент) — включая `aes256-sha256-modp2048` и `aes256-sha1-modp1024` для Windows по умолчанию.
updown-хук → `kaskadctl gw hook ikev2 up|down <identity> <vip>` → элемент `gw_route`.

CA IKEv2 устройства кладут в доверенные корневые (Windows `Cert:\LocalMachine\Root`, профиль Apple
`com.apple.security.root`), поэтому он ручается только за этот узел (2026-10-04):

- `nameConstraints = critical` на ОБЕ формы имён: адрес — `permitted;IP:<host>/255.255.255.255, permitted;DNS:<host>`,
  имя — `permitted;DNS:<host>, permitted;IP:0.0.0.0/255.255.255.255`. RFC 5280 ограничивает только
  перечисленные формы: с одним `IP:<host>` CA подписывал бы любое DNS-имя (проверено OpenSSL 3). `DNS:<адрес>`
  нужен, чтобы CN сервера (адрес, без DNS SAN) проходил там, где CN проверяют как имя (OpenSSL), и не совпадает
  ни с одним настоящим именем; `IP:0.0.0.0/32` не совпадает ни с одним адресом (IPv6 вне IPv4-поддерева);
- `extendedKeyUsage = serverAuth, 1.3.6.1.5.5.8.2.2` и у самого CA (Windows сужает цепочку до EKU её CA: ни
  подписи кода, ни S/MIME через этот корень), `basicConstraints = critical, CA:TRUE, pathlen:0`;
- свой CN на узел: `Kaskad IKEv2 CA <host> <6 hex>` (.mobileconfig берёт CN из сертификата);
- CA прежней сборки (без ограничений) при обновлении остаётся — подключённые устройства не ломаются; `gw status`,
  карточка IKEv2 в меню и выдача говорят «старого образца», смена — `kaskadctl gw pki rotate ikev2 --yes` (меню:
  «Протоколы → IKEv2 → Сменить центр сертификации»), устройствам — настройки заново. Отпечатки (SHA-1) прежних CA
  узла — в `inbounds.ikev2.retired_ca` (до 5): скрипт Windows убирает их из Root; профиль Apple с тем же
  PayloadUUID заменяет старый корень сам;
- ограниченный CA, не покрывающий новый `gateway.host`, заменяется при apply сразу (новому адресу устройства всё
  равно нужны новые настройки); CA прежней сборки при смене адреса не меняется — только сертификат сервера;
- вход ставится и без `gateway.host` (мастер ставит входы ДО `gw enable`, а адрес определяет enable): CA и
  сертификат сервера делает первый prepare, когда адрес известен; до того render/выдача отказывают с подсказкой
  `kaskadctl gw host <адрес>` (стенд Ubuntu 24.04: «[6/11] IKEv2 … ✗ неизвестен внешний адрес узла»). Мастер
  сам определяет адрес (`gw host --detect`) перед установкой протоколов, если он ещё не задан.

### 3.5 OpenVPN (`openvpn`)

OpenVPN 2.6 из дистрибутива, `server` + `topology subnet`, UDP (1194) и, по желанию, TCP; свой CA
(`openssl`, без easy-rsa), сертификат на устройство (CN = id устройства), `client-config-dir` с
`ifconfig-push` (постоянный адрес), `data-ciphers AES-256-GCM:CHACHA20-POLY1305`, `tls-crypt`, DNS
10.77.0.1. Службы `kaskad-gw-ovpn-udp.service` / `-tcp`. Выдача — `.ovpn` со встроенными сертификатами.

С 2026-10-04:

- сервер работает от `user nobody` / `group nogroup` с `persist-key`/`persist-tun`. Ключи и конфиг читаются при
  старте (root, `/etc/kaskad/gw/openvpn/`, 0600); то, что сервер читает после сброса прав, — в
  `/etc/openvpn/kaskad/` (0755, файлы 0644, секретов там нет): `ccd-udp/<id>`, `ccd-tcp/<id>` (читаются при
  каждом подключении) и `crl.pem`. Файл статуса и сокет управления открываются до сброса прав. Прежние
  `ccd-*` в `/etc/kaskad/gw/openvpn/` убираются при apply; удаление входа убирает и `/etc/openvpn/kaskad`;
- CRL (`crl-verify`, `openssl ca -gencrl`, действует 10 лет, как CA, пересоздаётся раз в год): в нём каждый
  сертификат устройства, который больше не его — устройство удалено, OpenVPN снят (`gw device revoke` теперь
  чистит `dev.<id>.ovpn.*` по префиксу адаптера), сертификат заменён (`gw device reset`). Учёт — `ovpn.crl.book`
  в secrets (отпечаток CA, устройство → серийный номер, отозванные); новый CA — новый учёт. OpenVPN 2.4+
  перечитывает CRL при следующем рукопожатии, когда файл сменился: перезапуска нет; сессию заменённого
  сертификата рвёт `kill <id>` через сокет управления. Выключенное устройство не отзывается (ccd-exclusive
  его и так не пускает, а включённое снова работает со старым .ovpn);
- `block-ipv6` — в .ovpn клиента (с `ignore-unknown-option block-ipv6`), сервер его больше не шлёт: клиенты 2.6
  отвергают его в PUSH («option 'block-ipv6' cannot be used in this context ([PUSH-OPTIONS])», стенд). Сервер
  по-прежнему шлёт `redirect-gateway def1 ipv6` — IPv6 уходит в туннель и там получает «нет маршрута».

### 3.6 OpenConnect (`ocserv`)

ocserv из дистрибутива, логин/пароль (`ocpasswd`-файл), `config-per-user` с `explicit-ipv4` (постоянный
адрес), TCP и UDP (DTLS) на своём порту (по умолчанию 4443; 443 — только в режиме с доменом),
сертификат самоподписанный — в выдаче `--servercert pin-sha256:…`. Служба `kaskad-gw-ocserv.service`.
Хуки connect/disconnect → `kaskadctl gw hook ocserv …` (для учёта сессий).

### 3.7 SOCKS5 (`socks5`)

xray нужен SOCKS5 и выходам VLESS и на сервере, где «Прокси для API Telegram» не настраивали (нет пула и
/etc/kaskad/egress): `gw inbound install socks5` сначала добывает xray тем же загрузчиком, что egress
(`gokaskad --egress-ensure-xray`: последний выпуск XTLS/Xray-core по тегу из JSON, иначе известная запасная
версия, в /etc/kaskad/egress/xray) — не скачался, установка отказывает с причиной, а не «✓»; `gw enable` и `gw
apply` при SOCKS5 или выходах VLESS пробуют так же (неудача — предупреждение, повтор не чаще раза в 10 минут),
юнит xray появляется с первой учёткой. SOCKS5 без устройств — «работает» (xray пока нечего открывать, порт
откроется с первым устройством; `gw status` так и пишет), не «НЕ работает» и не повод для починки сторожем.

Вход `socks` в xray шлюза (auth `password`, UDP по желанию), учётка = логин устройства и свой пароль
`dev.<id>.socks5.password` (не общий: SOCKS5 передаёт пароль открытым текстом),
`email` = id устройства; маршрут — правилом routing xray по `user` (тот же выбор маршрута, что у
устройства). Частные адреса, адреса самого узла и `localhost` закрыты правилом xray до правил
пользователей (§1.3) и, для loopback, правилом gw_out для сокетов `kaskad-xray`: через SOCKS5 не открыть
метрики xray, CLI accel-ppp и прочие службы узла, сеть хостера. Выдача: `socks5://login:pass@host:port`,
ссылка для Telegram `tg://socks?server=…`.

## 4. Выдача под ОС

`issue()` возвращает `{"format": "qr|conf|vpn|ovpn|mobileconfig|ps1|fields|guide|link", "title", "body",
"filename", "notes"}` (одна или несколько записей). Матрица (что предлагать первым):

| ОС / устройство | Первым | Ещё |
|---|---|---|
| iOS, macOS | AmneziaWG 3.1 (`vpn://` + QR) | IKEv2 `.mobileconfig`, WireGuard QR, OpenConnect |
| Android | AmneziaWG 3.1 (`vpn://`) | WireGuard, IKEv2 (встроенный, Android 12+), OpenVPN |
| Windows | AmneziaWG (приложение) | IKEv2/SSTP/L2TP — PowerShell `Add-VpnConnection`, OpenVPN, OpenConnect |
| Android TV | AmneziaWG `vpn://` (короткая ссылка) | — |
| Keenetic | AmneziaWG 2.0 (KeeneticOS 5.1+) или 1.5 | WireGuard, L2TP/IPsec |
| MikroTik | WireGuard (скрипт RouterOS) | L2TP/IPsec, SSTP, OpenVPN |
| OpenWrt | AmneziaWG (awg-openwrt) | WireGuard, OpenVPN |
| TP-Link, Huawei, Xiaomi, ASUS | L2TP/IPsec или L2TP | PPTP (если включён), OpenVPN (ASUS) |
| Linux | WireGuard/AmneziaWG `.conf` | OpenVPN, OpenConnect, SOCKS5 |

Каталог приложений AmneziaWG — `clients.json` goAmnezia (копия в `kaskad/vendor/`).

QR в терминале (меню): перед печатью ширина кода (`qrencode -t ASCII`: два знака на модуль) сравнивается с шириной
окна (`stty size`, иначе `tput cols`). Не помещается (QR AmneziaVPN — около 117 столбцов) — код не печатается
(переломанный не прочитать), меню говорит, до скольких столбцов растянуть окно, и предлагает «Сохранить в
файлы»: QR-коды пишутся картинками PNG (`qrencode -t PNG`, 0600, текст — через stdin) рядом с остальными
файлами выдачи.

## 5. Установка и настройки (как в goAmnezia)

- **Первая установка** (мастер): проверка системы → выбор протоколов (отмечены WireGuard, AmneziaWG с
  тремя линиями, L2TP/IPsec; ниже неотмеченные IKEv2, SSTP, OpenVPN, OpenConnect, SOCKS5, PPTP) → сводка
  портов с проверкой занятости (пробросы волны 1, слушатели, Docker) и правкой → выходы (взять пул
  «Прокси для API Telegram» или добавить ключ) → установка → первый пользователь и устройство → выдача.
  Перед установкой: адрес для клиентов (`gw host --detect`, если ещё не задан) и вопрос про занятый apt (§3).
- **Настройки → Протоколы**: список с состоянием (стоит / работает / порт / клиентов), действия:
  доставить, выключить, удалить, сменить порт. Удаление входа не удаляет пользователей и их другие входы.
- Порты входов попадают в проверку конфликтов пробросов (`kaskad.conflicts`, вид `gateway`).

## 6. Пакеты работ волны 2

| Пакет | Что | Файлы |
|---|---|---|
| WP4 · ядро шлюза | модель и секреты, адреса, маршруты, nft `gw_*` + ip rule + TPROXY, xray шлюза, выходы и сторож выходов, DNS, хуки, сборщик сессий, CLI `kaskadctl gw …`, юниты | `kaskad/gw/{model,secrets,addr,net,exits,xray,dns,hooks,collect,cli}.py`, `kaskad/secrets.py` |
| WP5 · туннельные входы | WireGuard, AmneziaWG ×3, OpenVPN, OpenConnect, SOCKS5 + выдача | `kaskad/gw/inbounds/{wg,awg,openvpn,ocserv,socks5}.py`, `kaskad/vendor/` |
| WP6 · PPP и IPsec | accel-ppp (сборка, L2TP, PPTP, SSTP), strongSwan (L2TP/IPsec, IKEv2) + выдача (.mobileconfig, PowerShell, инструкции роутеров) | `kaskad/gw/inbounds/{accel,l2tp,pptp,sstp,ikev2}.py`, `kaskad/gw/tls.py` |
| WP7 · меню | мастер и «Настройки → Протоколы», «Пользователи и устройства», выдача — в install.sh | `install.sh` |

Проверка на стенде: Linux-клиент на отдельном VPS проверяет каждый вход настоящим клиентом
(`wg`, `awg`, `xl2tpd`+strongSwan, `sstpc`, `pptp`, strongSwan-клиент IKEv2, `openvpn`, `openconnect`,
`curl --socks5`): туннель поднялся, внешний IP = IP выхода, DNS через шлюз, смена выхода без обрыва
туннеля, отказ выхода → блок, перезагрузка узла.

## 7. Контракт ядра шлюза для адаптеров (пишет WP4, пользуются WP5/WP6)

Модули ядра (WP4) — `lib/kaskad/gw/`:

| Модуль | Что даёт адаптерам |
|---|---|
| `gw/inbounds/base.py` | класс `Inbound` (интерфейс §3), `InboundError`; заглушка `DummyInbound` для тестов |
| `gw/registry.py` | `register(cls)`, `get(kind)`, `kinds()`; адаптеры регистрируются при импорте `kaskad.gw.inbounds` (пакет импортирует модули, которых может ещё не быть, — `ModuleNotFoundError` по имени модуля пропускается, как в `cli.py`) |
| `kaskad/secrets.py` | `load()`, `get(key)`, `set(key, value)`, `delete(key)`, `new_password(n=14)` (буквы и цифры без похожих, для полей роутеров), `new_psk()`; файл `/etc/kaskad/secrets.json` 0600; ключи вида `wg.server.private`, `dev.<id>.password`, `dev.<id>.wg.private`, `l2tp.psk`, `ovpn.ca.key`… |
| `gw/addr.py` | `subnet(kind)`, `server_ip(kind)`, `allocate(st, device, kind)`, `release(st, device, kind)`, проверка пересечения базы с маршрутами узла |
| `gw/model.py` | пользователи, устройства, доступы, маршруты: `add_user`, `add_device`, `grant(device, kind)`, `revoke`, `route_of(device)`, `devices_for(kind)` — всё на dict state под `state.transaction()` |
| `gw/sys.py` | `apt_install(runner, pkgs)` под `policy-rc.d` = 101 (службы дистрибутива не стартуют) + `mask` пакетных юнитов из списка; `write_unit(name, text)`, `systemd(runner, verb, unit)`; `ufw_allow(runner, port, proto, comment)` / `ufw_delete(...)` (только если UFW активен); `modprobe(runner, mods)`; `sysctl_set(runner, key, value)` |
| `gw/hooks.py` | `kaskadctl gw hook <kind> up|down <identity> <ip>`: элемент `gw_route` для динамических адресов (IKEv2), учёт сессий |
| `gw/net.py` | рендер `gw_*` части таблицы (`plan()`: элементы gw_route, выходы VLESS с их TPROXY, uid xray — один раз на apply), `ip rule`/таблицы 200/199 (аварийный выключатель)/256+N, `kgw0` 10.77.0.1, ip6-блок; **встраивается в `nft.render_full` и `nft.apply`** (одна таблица `ip kaskad`, загрузка при старте — тоже один файл), `compat-in` для INPUT; `part_present(live)`, `flush_conntrack(runner, st)`, `node_addresses(runner, st)` |
| `gw/gate.py` | drop-in ExecStartPre юнитов входов (`sync(runner, st)` — при каждом apply, `remove_all(runner)` — для `gw purge`) |
| `gw/exits.py` | пул из `upstream.txt` через `egress_parse.py` (importlib по пути), ID выходов, метки (AWG 256+N, VLESS 0x300+N, пул через xray 0x200), живость ног (неизвестно ≠ мёртв, 2 неудачи подряд), выбор метки устройству (rendezvous-hash), сторож |
| `gw/xray.py` | `kaskad-gw-xray.service` от пользователя `kaskad-xray`: TPROXY-вход 12345 (пул) и 12400+N (выход VLESS N), dns-вход 127.0.0.1:5353, вход SOCKS5 (учётки отдаёт адаптер socks5), выходы и балансировщик по пулу, блок частных адресов, свой DNS через пул, routing по `user` SOCKS |
| `gw/dns.py` | `kaskad-dns.service` (dnsmasq на 10.77.0.1, filter-AAAA, верхний DNS по §1.4) |
| `gw/cli.py` | `kaskadctl gw …` (ниже) |

Адаптер получает в `render/apply/issue` полный state и `secrets`, сам пишет конфиги только в свои пути
(`/etc/kaskad/gw/<kind>/…`, системные пути своих демонов — `/etc/swanctl/conf.d/kaskad.conf`,
`/etc/ocserv/…` — только если демон иначе не умеет) и управляет только своими юнитами
`kaskad-gw-<kind>*.service`. Порты и подсети берёт из `st["gateway"]["inbounds"][kind]`.

`kaskadctl gw`:

| Команда | Что делает |
|---|---|
| `gw status [--json]` | включён ли, входы (стоит/работает/порт/сессий), выходы (живы), DNS, xray |
| `gw enable` / `gw disable` | включить или выключить шлюз (сеть, DNS, xray); пользователи и входы остаются |
| `gw inbound list` / `install <kind> [--port N]` / `uninstall <kind>` / `enable` / `disable` / `port <kind> <N>` | протоколы («ядра») — установка по выбору, как в goAmnezia. `uninstall` оставляет ключи сервера (secrets.json) и запись входа (`gateway.removed_inbounds`: порт, параметры AmneziaWG, опции); `install` удалённого возвращает прежний порт (если свободен) и параметры — выданные устройствам настройки снова работают, а `previous` ответа говорит, что вернулось или что сменилось и каким устройствам выдать заново (тестер T-G1, F-2). `gw purge` забывает и это |
| `gw probe` / `gw inbound plan [kind…]` | для меню, ничего не меняют: годится ли система, порты входов и их конфликты; `packages` — что установка ещё поставит через apt (меню заранее спрашивает, если apt занят системой, §3) |
| `gw user add <name>` / `del` / `list` / `route <user> <via>` | пользователи |
| `gw device add <user> <name> --kind K --platform P [--access a,b]` / `del` / `list` / `grant <dev> <kind>` / `revoke` / `route <dev> <via>` | устройства и их входы; `revoke` удаляет секреты входа по префиксам, которые называет адаптер (`secret_prefixes`: OpenVPN — `dev.<id>.ovpn.`) |
| `gw device reset <dev> [--inbound K]` | новые ключи, пароли, сертификаты устройства (§2); его сессии рвутся |
| `gw issue <dev> [--inbound K] [--os O] [--json]` | выдача под ОС (§4); секреты печатаются только здесь |
| `gw pki rotate ikev2 --yes` | новый CA IKEv2 (ограничен адресом узла, §3.4) и сертификат сервера; устройствам — настройки заново |
| `gw exits [--json]` | выходы пула и их состояние |
| `gw apply` | всё из state: таблица, правила, конфиги входов, DNS, xray |
| `gw hook …`, `gw sessions [--json]`, `gw check [--repair]` | хуки демонов, сессии, сторож (сторож движка `net check` вызывает и его): маршрутизация с аварийным выключателем, ip6, compat-in, службы и входы — входы и xray не запускаются, пока части шлюза нет в таблице; вернул часть — `conntrack -D -s <база>` |

После обновления программы (`--k3-post-update`, код сменился) при включённом шлюзе: перезапуск
`kaskad-gw-collect` (долгий процесс со старым кодом) и `kaskadctl gw apply` — входы при этом не
перезапускаются (ExecStartPre — drop-in, юнит xray меняется: один перезапуск xray).
