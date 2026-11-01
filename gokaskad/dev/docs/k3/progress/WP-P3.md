# WP-P3 · схема панели — прогресс (2026-10-05, итог)

Готово:
- `lib/kaskad/panel/scheme.py`: GET /api/scheme (граф из state.json через snapshot, пул без ключей, живость
  выходов, «куда идёт сейчас» по exits.target, профили правил терпимо к их отсутствию), PUT /api/scheme/layout
  (версия, 409 со свежими позициями, слияние, порядок строк устройств), POST /api/scheme/apply (dry_run на копии
  в памяти; настоящий — тот же прогон, затем одна ops._change под замком; 409 при устаревшей версии).
- `static/graph.js` + `graph.css` + `graph-i18n-ru.js`: холст (пан/зум/щипок, перетаскивание с сохранением
  раскладки, связи с подсказками canConnect, отмена, «Применить» с диалогом dry_run, «Куда пойдёт», «Выровнять»,
  живые цифры на месте из live: full + merge patch RFC 7386), список < 720 px с «Направить в…», клавиатура,
  reduced-motion, тёмная тема через токены.
- Тесты: `tests/test_panel_scheme.py` (20), `tests/test_panel_graph_js.py` (7, без node; node --check — если есть).
- Проверено в браузере на обвязке с CSP панели: связи, применение, 409 раскладки между «вкладками», устаревшая
  версия при «Применить», телефон, клавиатура, тёмная тема.

Сверить с WP-P2: ctx.api получает полные пути `/api/scheme…` (или ctx.apiPrefix = ''), сам ставит
X-Kaskad-Admin; graph.js подгружает `graph-i18n-ru.js` сам, если index.html его не подключил.

Тесты: `bash tools/k3_test.sh` — ALL GREEN (762 unit OK, py38, payload актуален, install 316).
