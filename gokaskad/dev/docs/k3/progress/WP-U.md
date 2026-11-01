# WP-U — страница человека: итог (2026-10-05)
Ветка worktree-agent-a64dac1199be58a5b (от k3 6be96b1), без push. Пакет закончен.
## Готово
- `panel/userpage.py`: токен `user.<id>.page` (43 знака, 0600, сравнение за постоянное время), `enable/rotate/disable/url/status/pages`; `/p/<токен>` (оболочка без ключей), `POST …/keys` (nonce 5 мин, один раз), `GET …/file/<grant>/<имя>` (1 час), статика `/p/user.*`; один 404 для всего чужого (`not_found()` → Response); бан 10/5 мин → 15 мин (IPv6 по /64); QR — `qrencode -t SVG`, текст через stdin, матрица → путь SVG; админ-маршруты `/api/gw/users/<id>/page`.
- `panel/userpage_cli.py`: `kaskadctl page on|off|rotate|url|list`, везде `--json`.
- `static/user.{html,css,js}`, `user-i18n-ru.js`: от 360 px, вкладки ОС, карточки, копирование, скачивание, QR, безопасный DOM; проверено в браузере.
- `gw/ops.py` (минимально): `user_del` удаляет `user.<id>.page`; `purge` без `--users` оставляет ссылки сохранённых людей.
- Тесты: `tests/test_userpage.py`, `test_userpage_static_cli.py`, `userpage_case.py`.
## Стыки (согласованы)
- Открытый режим панели: `<panel dir>/access.json` {public, port, scheme, host?} — пишет WP-P1; нет файла/поля → ссылка путём `/p/<токен>` + понятная заметка.
- Общий 404 сервера — `userpage.not_found()`.
## Тесты
- `bash tools/k3_test.sh`: зелёно (860 тестов, py38, payload актуален, 316 проверок установщика).
- Открытый вопрос: HTTPS без домена (см. отчёт).
