# WP-P2 — интерфейс веб-панели (контрольная точка)

Рабочая копия агента от `k3` (6be96b1). Файлы: `lib/kaskad/panel/static/*` (index.html, boot.js, styles.css, icons.js, i18n-ru.js, app.js, qr.js, dev-mock.js, LICENSE-*), `tests/test_panel_static.py`, `tools/qr_check.js`.

## Готово (работает на `?mock=1`, формы ответов сверены с настоящим api.py/sse.py P1)
- Все 8 разделов; выдача настроек с QR; «Вид» (шторка и «Настройки»); вход (логин необязателен + пароль), `GET /api/session` до первого запроса.
- Поток: SSE (первое событие `full`, дальше merge patch RFC 7386, `alert` → события), запасной опрос (setTimeout после ответа, ×2 до 60 с, пауза на скрытой вкладке), `state.parts` → точечное перечитывание.
- Стыки: все POST/PUT/DELETE шлют `X-Kaskad-Admin: 1`; схема (настоящий graph.js от P3 проверен на моке): `ctx.api` — полные пути `/api/scheme…`, `ctx.onLive` отдаёт события live как пришли (full / merge patch), `ctx.onState` — на события state; graph.css, graph.js, graph-i18n-ru.js, qr.js подгружаются сами при входе в «Схему».
- Тесты: `python3 -m unittest tests.test_panel_static` (23, зелёные); `bash tools/k3_test.sh` — зелёный (316) на последнем коммите; payload пересобран (`python3 tools/build_payload.py`).

## Что осталось (по порядку)
1. После любых правок `lib/`: `python3 tools/build_payload.py`, затем `bash tools/k3_test.sh` (последний полный прогон — зелёный, 316, на последнем коммите).
2. Проверить вручную с настоящим сервером (`kaskadctl panel serve`): вход, SSE, выдача, схема (graph.js от P3 грузится при входе в «Схему»); править только `API{…}` и `norm*` в app.js.
3. Не сделано из панели P1: включение/выключение людей и устройств (`PUT gw/users|devices/<id>/enabled`), смена пароля, ссылка входа, открытый режим (`PUT panel/public|password|settings`, `POST panel/link`), подсказки `GET gw/offers`; в «Настройках» нет карточек «Бот» и «Ускорение AmneziaWG».
4. Раздел «Правила»: форма `GET /api/rules` от WP-R2 неизвестна — показаны версия, время, счётчики, профиль; сверить и поправить `normRules` и блок в `PAGES.rules`.
5. Косметика: выравнивание карточек выходов, экран «Правила» на телефоне, проверка режимов меню «сверху»/«значки» и подробности «минимализм».

## Блокеры
- Нет.
