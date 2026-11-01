# WP-B — бот на Python: готово

- `lib/kaskad/net/socks.py` — SOCKS5 (RFC 1928, имя через прокси), HTTPS с проверкой сертификата, HTTP CONNECT, multipart.
- `lib/kaskad/bot/`: telegram, config, store (диалоги TTL 30 мин, offset.json, чужие раз в сутки), scheduler (полосы по
  чатам + 1 задача на чат, пул 4), app (приём, доступ, устаревшие нажатия, offset после обработки), handlers_main/fwd/tools
  (паритет с bash-ботом), gateway (статус, люди/устройства, добавление, маршрут, ключи, выдача, выходы, ссылка в панель),
  monitor (3 неудачи → алерт, «восстановлено», события live.json), sysinfo, cli (`kaskadctl bot run|status|test|send-test`).
- Ссылка в панель: `kaskad.panel.auth.login_link(ttl=3600, by="bot")` → {"url"|null, "token", "ttl"}; нет модуля или
  url = null — «Веб-панель не установлена».
- Тесты: `tests/bot/` — 98; `bash tools/k3_test.sh` — ALL GREEN (833 теста Python), payload пересобран.

Для WP-I: `kaskad-bot.service` → `ExecStart=/opt/kaskad/bin/kaskadctl bot run` при BOT_IMPL ≠ bash; бот сам делает
`systemctl disable --now kaskad-monitor.service`; `sync_monitoring_service` не должен включать его, пока работает бот на Python;
при возврате на bash — включить обратно (если есть monitors/*.conf).
