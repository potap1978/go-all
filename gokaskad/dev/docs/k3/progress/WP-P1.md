# WP-P1 — сервер панели: готово

Сделано и проверено (`bash tools/k3_test.sh` — ALL GREEN, 812 тестов, из них 77 новых в `tests/panel/`):
- `panel/server.py` — ThreadingHTTPServer, Host, CSRF (+Origin/Sec-Fetch-Site), тело ≤ 1 МБ, заголовки, статика (gzip, `?v=`, ETag), 500 без деталей, без журнала запросов, HTTPS открытого режима, `not_found()` через `userpage.not_found()`, ScopedRunner (apt/make вне лимитов юнита), CountingRunner.
- `panel/auth.py` — scrypt, пол 0,35 с, 2 KDF, 5/15 мин, сессии, ссылки хэшами, туннель, `login_link(ttl)` для WP-B.
- `panel/api.py` — §3.2 (без scheme/userpage/rules), «Вид», вход/выход, `panel/*`; `panel/sse.py`; `panel/conf.py` (config/status/access.json); `panel/cli.py`.
- Сборщик: `gw/live.py` + крючки `gw/collect.py` (live.json каждый раунд, раз в 2 с при маяке).
- `tools/panel_load.py` — приёмка нагрузки (запускать на узле).
- Живой процесс проверен руками: `kaskadctl panel serve`, SIGHUP, SIGTERM.

Что ждёт других: WP-I — меню и `systemctl try-restart kaskad-panel` после обновления кода; стенд — прогон `tools/panel_load.py`.
