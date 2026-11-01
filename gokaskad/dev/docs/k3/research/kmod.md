# Исследование: модуль ядра AmneziaWG и DKMS (2026-10-05)

- Репозиторий `amnezia-vpn/amneziawg-linux-kernel-module`, только теги: v3.1.20260906 (= master), v3.1.20260828,
  v3.1.20260812 («awg 3.1 features»), v3.0.20260805, v3.0.20260730 («AmneziaWG 3.0»), v1.0.20251004 (H-диапазоны,
  S3/S4, I1–I5 = 1.5 и 2.0). `modinfo version` у всех 3.1.* — «3.1.20260812».
- UAPI (`src/uapi/wireguard.h`): `WG_GENL_NAME "amneziawg"`, атрибуты S3/S4, I1..I5, HEADER_PROTECTION_KEY,
  CONTENT_PADDING_ADDITION, REKEY_AFTER_TIME, REKEY_TIMEOUT, REJECT_AFTER_TIME, KEEPALIVE_TIMEOUT,
  MAX_HANDSHAKE_ATTEMPTS, RANDOM_TRAILERS, DISABLE_COOKIES; H1–H4 — NLA_U64 (диапазон), таймеры — u16-диапазоны.
- `awg` (amneziawg-tools 3.1) один для ядра и программы: есть сокет `/var/run/amneziawg/<if>.sock` → userspace,
  иначе netlink `amneziawg`. `ip link add <if> type amneziawg` (alias `rtnl-link-amneziawg`). Смешивать ядро и
  программу на одном узле можно (по интерфейсам). `awg setconf` не снимает прежние Jc/S4/H1/I1 → смена линии =
  пересоздать интерфейс. Интерфейс ядра переживает процесс → `ip link del` при остановке.
- Установка: Ubuntu PPA `ppa:amnezia/ppa` (amneziawg-dkms, amneziawg-tools; серии вплоть до resolute 26.04) — PPA сам
  меняет поколение модуля; Debian — пакетов нет. Надёжнее из исходников: `make -C src dkms-install` или
  `dkms add/build/install amneziawg/<версия>` (dkms.conf фиксирует 1.0.0 — править PACKAGE_VERSION). Полные исходники
  ядра не нужны (с 02.10.2025): `/lib/modules/$(uname -r)/build`; пакеты: dkms, gcc, make, linux-headers-$(uname -r)
  + метапакет флейвора. Модуль ≈110 КБ, исходники ≈1,7 МБ.
- Ядра: 6.1 (Debian 12) — 3.1 собирается (6.1.0-52); 6.8 (Ubuntu 24.04) — ок (без HWE); 6.12 (Debian 13) — ок;
  7.0 (Ubuntu 26.04) — GA и 7.0.0-34 ок, **7.0.0-38+ не собирается** (бэкпорт сигнатуры
  `setup_udp_tunnel_sock`/`udp_tunnel_sock_release` с `struct sock *`, issues #259/#252), фикс — PR #218 (не принят);
  падение DKMS при апгрейде оставляет пакеты ядра недонастроенными → `AUTOINSTALL=no` + свой хук с `|| true`.
- Secure Boot: DKMS подписывает своим ключом, при включённом SB нужен `mokutil --import` + консоль → не ставить.
  LXC/OpenVZ — модуль не загрузить.
- Скорость: 3x-ui PR #6105 / issue #6378 — userspace ≈125–300 Мбит/с при 50–63 % CPU, модуль ≈480 Мбит/с (потолок
  канала) при 66 % простоя (1 vCPU).
- Ошибки: исправлены #98, #153, #212, #216; открыты: UAF I1–I5 при удалении интерфейса (#139/#225, PR #234),
  Jmin>Jmax → паника (#254), #228 зацикливание `awg show` при больших I1–I5, #146 дедлок при удалении пира;
  3.1: #226 (потери ≈6,8 % при RandomTrailers), #227 (RejectAfterTime на data-path), #222 (модуль-клиент ↔
  amneziawg-go-сервер с HeaderProtectionKey не рукопожимаются), #215 (регрессия с Keenetic). 2.0 модуль↔Go — ок.
- goAmnezia pro модуль не использует (идея DKMS-01 отложена владельцем 05.10).
