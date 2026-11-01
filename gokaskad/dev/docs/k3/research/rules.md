# Исследование: RoscomVPN, runetfreedom, .dat, dnsmasq nftset, Xray (2026-10-05)

## RoscomVPN (hydraponique)
- geoip: релизы ежедневно (~08–10 UTC), тег `YYYYMMDDHHMM`. Ассеты: `geoip.dat` (≈425 КБ), `geoip.dat.sha256`
  (`<sha256>  release/geoip.dat` — сравнивать первое поле), `text.tar.gz`, `mihomo.tar.gz`, `sing-box.tar.gz`.
  URL: `https://github.com/hydraponique/roscomvpn-geoip/releases/latest/download/geoip.dat`,
  `https://cdn.jsdelivr.net/gh/hydraponique/roscomvpn-geoip/release/geoip.dat`,
  текст: `https://cdn.jsdelivr.net/gh/hydraponique/roscomvpn-geoip/release/text/direct.txt` (586 КБ; также
  `whitelist.txt`, `private.txt`). В geoip.dat ровно три записи: `DIRECT`, `WHITELIST`, `PRIVATE` (не страны!).
  `direct` = RU+BY (GeoLite2, IPinfo, DB-IP) + ASN VK/Яндекс/CDNVideo + Apple Push − RKN-блок-листы − проблемные
  (4pda, Cloudflare…) − зарубежные CDN/Hetzner. 35 609 CIDR IPv4 → 14 963 диапазона после слияния; 87 IPv6.
- geosite: релизы только по push (последний 202604152235). `geosite.dat` 67 824 Б, `geosite.dat.sha256`.
  URL: `https://github.com/hydraponique/roscomvpn-geosite/releases/latest/download/geosite.dat`.
  Исходники категорий: `https://raw.githubusercontent.com/hydraponique/roscomvpn-geosite/master/data/<имя>`
  (синтаксис domain-list-community, строка без префикса = `domain:`).
  23 категории (записей): whitelist 467, category-ru 55, apple 92, microsoft 66, epicgames 27, riot 54, steam 41,
  escapefromtarkov 4, twitch 40, pinterest 50, faceit 2 (direct); youtube 177, telegram 26, github 28,
  google-play 29, twitch-ads 5 (proxy); win-spy 376, torrent 434, category-ads 2 (block); category-geoblock-ru 882
  (772 domain:, 105 keyword:, 5 regexp:), google-deepmind 41 (не в профилях: «РУ-сервер → зарубеж»), origin 160,
  private 130 (11 full:, 1 regexp:). Атрибутов `@…` нет.
- Профиль DEFAULT (Happ): блок → прокси → direct (`geosite:private`, `category-ru`, `whitelist`, `microsoft`, `apple`,
  `epicgames`, `riot`, `escapefromtarkov`, `steam`, `twitch`, `pinterest`, `faceit`, `geoip:private`, `geoip:direct`)
  → остальное прокси; `DomainStrategy IPIfNonMatch`. WHITELIST: direct только private/whitelist.
- Лицензии: geosite MIT (`Copyright (c) 2018-2019 V2Ray`); geoip и routing без LICENSE; данные geoip: MaxMind GeoLite2
  EULA, DB-IP Lite CC BY 4.0 (ссылка на DB-IP), IPinfo MIT. Качать на узле, не вшивать; атрибуция.

## runetfreedom (запасной)
- `russia-blocked-geoip` (GPL-3.0), релиз каждые 6 ч, ветка `release` `text/<категория>.txt` (266 файлов), страна:
  `https://raw.githubusercontent.com/runetfreedom/russia-blocked-geoip/release/text/ru.txt` (12 944 CIDR IPv4).
- `russia-blocked-geosite` — сборка падает с 2026-08-10 (geosite заморожен), не использовать.

## Формат .dat (protobuf, совместим Xray/v2fly)
```
GeoIPList   1 repeated GeoIP              (LEN 0x0A)
GeoIP       1 string code (ВЕРХНИЙ регистр) (0x0A); 2 repeated CIDR (0x12); 3 bool reverse_match (0x18)
CIDR        1 bytes ip (4 или 16 байт)      (0x0A); 2 uint32 prefix (0x10)
GeoSiteList 1 repeated GeoSite             (0x0A)
GeoSite     1 string code                  (0x0A); 2 repeated Domain (0x12)
Domain      1 enum type (0x08): 0 Plain/keyword, 1 Regex, 2 RootDomain/domain:, 3 Full
            2 string value (0x12); 3 repeated Attribute (0x1A: 1 string key, 2 bool | 3 int64)
```
proto3: нулевые поля не пишутся (нет type → 0, нет prefix → 0). Неизвестные поля пропускать по типу (0 varint,
1 — 8 байт, 2 — длина, 5 — 4 байта). Сравнение кодов без учёта регистра.

## dnsmasq nftset (2.87+; Debian 12 — 2.90, Debian 13 — 2.91, с `HAVE_NFTSET`)
- `nftset=/example.ru/4#ip#kaskad#gw_ru_dns` (dnsmasq выполняет `add element ip kaskad gw_ru_dns { A.B.C.D }`);
  префикс `4#` обязателен для ipv4-набора; несколько наборов — через запятую в одной строке; одна запись на домен
  (самый длинный суффикс); строка ≤ 1024 символов в ≤ 2.92; поиск по списку линейный; SIGHUP конфиг не перечитывает
  — перезапуск.
- Элементы добавляются только из ответов апстрима (не из кэша) → `max-cache-ttl` 300–900 с и timeout набора больше.
- dnsmasq не ставит timeout элемента — таймаут берётся из набора (`flags timeout; timeout 6h`); повторное добавление
  таймаут НЕ продлевает.
- **Риск:** libnftables при каждом `add element` выгружает элементы всех НЕсоставных интервальных наборов во всех
  таблицах (`src/cache.c`, nft 1.0.6/1.1.3/1.1.7): 80 тыс. элементов → +0,4–0,6 с на добавление (openwrt/packages
  #30663). Обход: статический RU-набор составного типа (`ipv4_addr . inet_proto`, pipapo) — его не выгружают;
  динамические наборы — простые (hash) с timeout. Замерить на стенде.
- Реклама: `address=/домен/` (NXDOMAIN) или `address=/домен/#`.

## Xray
- `ext:file.dat:tag` из каталога ресурсов (`XRAY_LOCATION_ASSET`), коды в верхнем регистре; отсутствует файл/код —
  конфиг не стартует. Класть как `roscomvpn-geoip.dat`, `roscomvpn-geosite.dat`.
- IP-правила для TPROXY работают по адресу назначения; `domainStrategy` нужен только для доменов.
- stable v26.3.27: потоковая загрузка геоданных есть (с 26.2.6), объекта `geodata` (автообновление) нет.
