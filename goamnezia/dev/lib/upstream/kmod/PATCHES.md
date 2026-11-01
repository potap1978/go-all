# Патчи модуля ядра AmneziaWG (`lib/upstream/kmod/`)

Модуль ядра ставит `lib/kmod.sh` только по выбору человека (`docs/KMOD_PLAN.md`, этап Б). Исходники — тег upstream,
поверх него два патча из этого каталога. Патчи взяты без изменений из навыка Kaskad `amneziawg-kernel-module`
(`gokaskad Pro/k3/skills/amneziawg-kernel-module/assets/`, 08.10.2026): так на сервере с обоими продуктами один и тот
же модуль с одной меткой, и второй раз его не собирают.

**Лицензия — GPL-2.0**, как у самого модуля (`src/compat/compat.h`: Copyright (C) 2015-2019 Jason A. Donenfeld,
AmneziaVPN). Остальной код goAmnezia эти файлы не меняет и в себя не включает: они уходят на сервер как есть и
накладываются на исходники модуля в `/usr/src/amneziawg-3.1.20260906`.

## Основа

| Что | Значение |
|---|---|
| репозиторий | `amnezia-vpn/amneziawg-linux-kernel-module` |
| тег | `v3.1.20260906`, коммит `4569c4c67f3a57414969260cafbbd04694fbaae0` |
| архив | `https://github.com/amnezia-vpn/amneziawg-linux-kernel-module/archive/refs/tags/v3.1.20260906.tar.gz` |
| sha256 архива | `e32fa46f1b6f9e319c5261f0b0765c3a9261e40cb7c4616fd73500da961a2932` — другой архив не собирается никогда |

## Файлы

| Файл | sha256 | Что |
|---|---|---|
| `awg-kmod-218.patch` | `edcb1dfbb8bdd8e960b91f03e302fe3d4330f176e6a25c55e43d026fd5ed907d` | upstream PR #218 без изменений: сигнатура `setup_udp_tunnel_sock(…, struct sock *)` распознаётся при сборке. Накладывается, **только когда её объявляют заголовки ядра** (`include/net/udp_tunnel.h` в `/lib/modules/<ядро>/build` или `…/source`), а не по номеру ядра: на Ubuntu 24.04.1 Aeza 6.8.0-48 и 6.8.0-146 он не нужен (KMOD-04, замер 08.10), на Ubuntu 26.04 7.0.0-38+ — нужен. Признак наложения — `__compat_setup_udp_tunnel_sock` в `compat/compat.h` |
| `awg-kmod-31.patch` | `5e7ed57d114b5462ee5bf9d38b6666a41ce4ff90a39efb3980b36fcff9b7b603` | набор `kaskad-k31.1`: исправление #253 (rwlock + `read_lock_bh`/`write_lock_bh` вместо rw_semaphore: `awg syncconf` с ключом защиты заголовка под трафиком вешал сервер с 1 vCPU) и открытые PR сообщества #234, #235+#236, #237, #238–#240, #241, #242, #243, #247. Последний ханк — метка: `WIREGUARD_VERSION "3.1.20260906+kaskad-k31.1"` |

`lib/kmod.sh` сверяет sha256 обоих файлов перед наложением (`GA_KMOD_PATCH_218_SHA`, `GA_KMOD_PATCH_31_SHA`), а
`tests/test_kmod_assets.py` — что файлы, константы и эта таблица совпадают.

## Метка

После сборки с набором `/sys/module/amneziawg/version`, `modinfo amneziawg` и dmesg показывают
`3.1.20260906+kaskad-k31.1`. Метку берём ту же, что у Kaskad: модуль, который поставил сосед, с этой меткой
продукт не пересобирает, а использует. Модуль без метки считается стоковым — на нём профили не переводятся.

## Как накладывается

Внутри `/usr/src/amneziawg-3.1.20260906` (там лежит `src/` архива), сначала проверка, потом наложение:

```bash
patch -p2 -N --dry-run < awg-kmod-218.patch && patch -p2 -N < awg-kmod-218.patch   # только если требуют заголовки
patch -p2 -N --dry-run < awg-kmod-31.patch  && patch -p2 -N < awg-kmod-31.patch
```

Оба порядка ложатся чисто. Набор не лёг — модуль не собирается и не ставится вовсе (стоковый модуль опаснее
программы: #253), причина записывается. Хук нового ядра (`/etc/kernel/postinst.d/zz-goamnezia-amneziawg`) накладывает
#218 по заголовкам нового ядра сам.

## Где проверено

- Ubuntu 24.04, ядро 6.8: arm64 — лаборатория Kaskad (35 мин транзита, ≈78 000 syncconf с ключом под iperf3, 0
  сбоев); x86 — Aeza (замеры Kaskad и goAmnezia 08.10.2026, сборка 17 с на 1 vCPU).
- Debian 12/13 и Ubuntu 26.04 — не проверено.

## Когда выйдет новый тег upstream

Сменить тег и sha256 в `lib/kmod.sh`, убрать из набора то, что уже влито, поднять метку (`…-k31.2`) — серверы со
старой меткой пересоберут модуль сами после обновления goAmnezia (отдельной службой, `kmod-install --rebuild=on`).
