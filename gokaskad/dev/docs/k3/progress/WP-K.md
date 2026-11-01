# WP-K — ускорение AmneziaWG модулем ядра + ноги под systemd: итог

Обновлено: 2026-10-05, готово (основа k3 d76cf83). `bash tools/k3_test.sh` — ALL GREEN (770 Python, 343 bash).

Сделано:
- lib/kaskad/gw/awgkmod.py — модуль amneziawg-linux-kernel-module v3.1.20260906 через DKMS: проверки хоста (x86_64/
  aarch64, не контейнер, Secure Boot выкл.), apt dkms gcc make patch (+gcc-NN ядра) и заголовки + метапакет флейвора,
  архив тега с sha256 e32fa46f1b6f9e319c5261f0b0765c3a9261e40cb7c4616fd73500da961a2932 (посчитан с настоящего архива),
  dkms.conf (тег, AUTOINSTALL="no"), патч #218 только при новой сигнатуре setup_udp_tunnel_sock, сборка и под
  установленное новое ядро, хуки /etc/kernel/{postinst.d,header_postinst.d}/zz-kaskad-amneziawg, служба
  kaskad-gw-awgkmod (откат при старте), самопроверка в netns (ядро↔ядро, ядро↔amneziawg-go, линии 1.5 и 2.0),
  проверка параметров (Jmin < Jmax и др.), off, status. Состояние — /etc/kaskad/gw/awg-kmod.json.
- lib/kaskad/vendor/awg-kmod-218.patch — PR #218 без изменений + атрибуция (GPL-2.0); едет в payload.
- gw/inbounds/awg.py — awg1/awg2 на интерфейсе модуля (oneshot, ip link add … type amneziawg, ExecStopPost удаляет),
  awg3 — amneziawg-go; смена режима = пересоздание интерфейса; модуль не поднял вход → откат на amneziawg-go.
- gw/cli.py — kaskadctl gw accel status|on|off|boot|check; «ядро/программа» в gw status и inbound list (ops: 1 строка).
- install.sh — ноги kawgN под юнитами kaskad-leg@kawgN (amneziawg-go -f, Restart=on-failure; линия 2.0 — drop-in
  kernel.conf на модуле), правила ног — ExecStartPre (--egress-awg-routes), xray бота ноги не поднимает и не снимает,
  heal/сторож — только systemctl, миграция «голых» ног 3.1.0 (один короткий обрыв), удаление снимает юниты ног.
- gw/exits.py — ensure_legs больше не ждёт kaskad-egress (activating).

Осталось: проверка на стенде (команды — в отчёте WP-K): Debian 12/13, Ubuntu 24.04/26.04 (в т.ч. 7.0.0-38+), iperf
ядро/программа, сценарии (а)(б)(в) находки про ноги. Блокеров нет.
