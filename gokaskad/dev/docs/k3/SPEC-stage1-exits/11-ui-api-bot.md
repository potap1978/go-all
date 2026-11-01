### 7.9 Контракт API панели (общий для T7 и T8)
Все изменяющие — `auth="admin"`, CSRF по умолчанию (`routes.py:46`, `server.py:619-622`); ошибки —
`HttpError(400, text, code, {"field": …})`, тексты — `i18n/catalog/*/panel.json`.

| Метод и путь | Тело / параметры | Ответ |
|---|---|---|
| `GET /api/gw/exits` (есть, `api.py:964`) | — | **`rev`** (§3.1; для формы квоты — её `base_rev`, §7; проверка 16, придирка 4) — **из того же чтения, что и строки** (проверка 19, З-3; было «вне кэша», проверка 17, придирка 1): `view()` под `snapshot.derived` зовёт `rev.current(st)` и строит строки из отданного им `st`, пул читает после; вид кэшируется по подписи `state.json` и файлов пула (`api.py:972`), так что новая `rev` или новый пул — новый вид; **`default_route_ver`** — `ver` маршрута узла по умолчанию рядом с `default_route` (абзац «Версия объекта»; проверка 19, З-4); у строки новые поля (**правка 5, З1**): `recovering` (bool, §4.3), `off_until` (ts или `null`), `unstable` (bool) и `unstable_until` (ts или `null`, §4.3, З2), `manual_why` (`sub_confirm` или `null`, §3.8); метки «отвечает снова · новые устройства — с ЧЧ:ММ» и «нестабилен» строятся из этих полей; прежние новые: `proto`, `state` (`ok/degraded/standby/off/unknown`), `reasons [{code, args}]`, `tier`, `devices`, `rtt_ms`, `loss_pct`, `jitter_ms`, `country`, `speed {mbps, ts}`, `quota {period, limit_gb, reset_day, warn_pct, action, used_gb, pct, reset, paused_until}` или `null` — **настройки квоты целиком** (схема §3.1; из них строится форма квоты, проверка 17, З-5) и счётчики (`paused_until` — проверки остановлены по квоте, §5.6), `speed_skip`, `probe_mb_day`, `last_check_ts` (для «Лениво»), `group`, `role` |
| `PUT /api/gw/exits/<id>` (есть, `:986`) (D-080) | поля формы выхода: `name` (как сейчас; **без `rev`** — имя вне версии, правка на месте RN §4.1, З-4), `quota` или `null`, `speed_skip` — с `rev`; **`quota` — только настройки** `period`, `limit_gb`, `reset_day`, `warn_pct`, `action` (схема §3.1): счётчики из ответа `GET` (`used_gb`, `pct`, `reset`, `paused_until`) и прочие поля в теле — 400 `field` (иначе попали бы в `exit_pref` и в отпечаток; проверка 18, придирка 5); `?dry=1`. **Не принимаются** (правки 7–8): `group`/`tier` — `/move`; `enabled` — `/enabled` (сейчас `api.py:986–1000` принимает `{enabled, name}`). **Порядок проверок** (проверка 17, З-4 и придирка 2): 1) тело: пустое — 400; `enabled` без `rev` — 400 `rev_required` (старая панель из кэша, `app.js:4492`), с `rev` — 400 `use_enabled`; `group`/`tier` — 400 `use_move`; `quota`/`speed_skip` без `rev` — 400 `rev_required`; 2) выхода нет — 404 `gone` (`data.gone: true`, `data.exit`; проверка 18, придирка 4); строка-повтор ключа (`e-…-N`, C17) — 409 `conflict` с `data.exit` = исходный выход, как сейчас; 3) сверка `rev` (если есть `quota`/`speed_skip`) — 409 `policy_changed`; 4) имя — `names.check`, дубль — 409 `confirm_duplicate` | `{ok, rev, moves?}` |
| `POST /api/gw/exits` (добавление, есть) | + `group` (по умолчанию `main`); **`rev` не нужен** (абзац `rev` ниже; проверка 16, З-4) | как сейчас + `rev` (новая — для перекладки форм вкладки, §7) |
| `GET /api/gw/people` (есть, `api.py:826`) | — | у устройства и человека + `net: {step, group, since, missing}` (`step`: `exit` \| `last_direct` \| `last_block`; `missing` — маршрут на удалённый выход, §2.2; §4.4, §7.3) и `route_ver` (абзац «Версия объекта» ниже; проверка 17) |
| `POST /api/gw/groups` | `{name, role, fail?, allow_duplicate?, rev}` | `{ok, id, rev}`; имя — `names.check`, дубль — как у `PUT /api/gw/groups/<gid>` |
| `PUT /api/gw/groups/<gid>` (точечная проверка, З-4; проверка 16, З-6) (D-086) | `{name, allow_duplicate?}` — правка имени на месте в карточке группы (T8, `Kit.rename`); без `rev`, `rev` не меняет; `""` у `main` — имя по умолчанию (§3.1) | `{ok, id, name, label, old}`; пусто (кроме `main`) или длинно — 400 (`names.check`, RN §2); дубль по показанному имени на обоих языках (§3.1) без `allow_duplicate` — 409 `{need: "confirm_duplicate", duplicates}` (как выходы, RN §2); группы нет (удалили в другой вкладке) — 404 `gone` с `data.gone: true` (панель: «Уже удалено в другом месте — обновите страницу», §7), ничего не создаётся (проверка 17, придирка 8: `group_missing` — только 409 у `PUT /api/gw/policy`); `PUT /api/gw/policy` имена групп не меняет (имя в теле пропускается) |
| `POST /api/gw/exits/<id>/manual` | `{"state": "standby"\|"restore"\|"maintenance", rev}`; `?dry=1` | `{ok, state, rev, moves, warnings}` — **и настоящий ответ отдаёт `moves`** (правка 8, тест T7); при `dry` — `{ok, dry, rev, moves, warnings}` (как у всех сухих прогонов, ниже) |
| `POST /api/gw/exits/<id>/country` | `{"accept": true, rev}`; `?dry=1` | `{ok, country, rev, moves, warnings}` (правка 8) |
| `POST /api/gw/exits/<id>/enabled` (правка 8) | `{on: bool, rev}`; `?dry=1` | `{ok, enabled, rev, moves, warnings}`; вместо `enabled` в `PUT` |
| `DELETE /api/gw/exits/<id>` (есть, `api.py:1039`) | `?rev=`, `?dry=1` | `{ok, rev, moves, warnings}`; запись через скрипт пула в три шага (§3.1; проверка 19, С-1) (D-098): 404 `gone`, сверка `rev` и `moves` — в короткой транзакции до скрипта, скрипт — без блокировки, `rev` ответа — `rev.note` после скрипта; повтор по уже удалённому — 404 `gone` (абзац «Повтор `DELETE`» ниже; проверка 16, З-5) |
| `POST /api/gw/exits/<id>/move` (правка 7) (D-078) | `{group, tier?, confirm_role?, rev}`; `?dry=1`; между ролями — `confirm_role: true`, иначе 409 (T15) | `{ok, rev, moves, warnings}`; перенос — сразу, от сохранённой политики; уровень — `tier` или `new_exits_tier` цели; затронута группа прокси бота — пересборка прокси (§2.9) |
| `DELETE /api/gw/groups/<gid>` (правки 7–8) (D-078, D-079) | `?rev=`; `?exits_to=<gid>` (обязателен, если в группе есть выходы; та же роль; выходы встают на `new_exits_tier` цели); `?reassign=direct\|<gid>` (если на группу ссылаются люди, устройства или `ru_via`); **замены других ссылок** (правка 8, З-1): `?tg_proxy_to=<gid>` (если это `tg_proxy_group`; зарубежная), `?new_exits_to=<gid>` (если это `new_exits_group`; зарубежная, как `tg_proxy_to`), `?subs_to=<gid>` (если на группу указывают `subs[].group`; та же роль); `?dry=1` | `{ok, rev, moves, refs: {users, devices}, uses: [tg_proxy, new_exits, subs], warnings}`; `main` — 400; нет нужного параметра — 409 (`data.exits`, `data.refs`, `data.uses`); российская группа с ключами без другой российской — 409 «сначала перенесите или удалите ключи»; повтор по уже удалённой группе — 404 `gone` до сверки `rev` (абзац «Повтор `DELETE`» ниже); меняется группа прокси бота — пересборка прокси (§2.9) |
| `POST /api/gw/exits/<id>/speedtest` | `{"force": false}` | `202 {job}`; итог — событие SSE и поле `speed` |
| `GET /api/gw/exits/<id>/history` | `range=1d\|7d\|90d` | `{charts: bool, step, cols: [...], points: [[ts, …]], states: [[from, to, state]]}`, ≤ 1440 точек |
| `GET /api/gw/exits/<id>/transitions` (правки 5–6) (D-071) | `days=1..90` (по умолчанию 7), `limit` 1–500 (по умолчанию 100), `before=<ts>` — курсор продолжения | `{db: bool, items: [{ts, from, to, why, args}], next: <ts>\|null}` — история переходов с причинами из `metrics.db.events` (`key = "transition"`, §3.6), новые сверху; `next` — значение для `before` следующей страницы; нет базы — `db: false`, `items: []`, `next: null` |
| `GET /api/gw/policy` | — | `{rev, policy (с groups и tg_proxy_group; у групп и уровней — `label`, §3.1), presets: {abroad: {...}, ru: {...}}, forecast: {probe_mb_day: {gid: n}, speed_gb_month}}`; `presets.ru` — пустой объект до T15 (Q3) |
| `PUT /api/gw/policy` (D-078) | политика §3.1 и `rev`; `?dry=1` | `{ok, rev, moves: {total, by_group: {gid: {by_tier}}}, warnings: [{code, field}]}`; **список групп и состав групп этим запросом не меняются** (правки 7–8): выход сменил группу — 400 `use_move`; новая — `POST /api/gw/groups`, удаление — `DELETE /api/gw/groups/<gid>`; группы из сохранённой политики нет в теле — 409 `group_missing`; смена номеров `tg_proxy_group` — пересборка прокси бота (§2.9); **`probes.speed` этим запросом не меняется** — в теле пропускается, как имена групп (своя форма — `PUT /api/gw/probes/speed`; проверка 19, З-2) |
| `POST /api/gw/policy/reset` | `{"preset": "compat"\|"standard"\|…, "group": "<gid>", rev}`; `?dry=1` (правка 8: «Включить «Стандарт»» и сброс — окно со строкой «что изменится») | `{ok, rev, moves, warnings}`; **`probes.speed` сброс и «Стандарт» не трогают** — у него своя форма «Тест скорости» (§7.1) и своя `ver` (проверка 20, З-3) |
| `POST /api/gw/policy/check-url` | `{"url"}` | `{ok, code, mbps, final: "хост/путь", error_code}` (лимит частоты §5.4) |
| `GET/PUT /api/settings/time` | `{"tz", ver}` (`ver` — абзац «Версия объекта» ниже; без `ver` — 400 `ver_required`) | `{tz, os_tz, now, offset, ntp: true\|false\|null, zones: [...], warn?, ver}` |
| `GET/PUT /api/notify` | `{push, flap_summary, ver}` (§8.1; `ver` — абзац «Версия объекта» ниже) | `{push: {...}, flap_summary, ver}`; `flap_summary` — действующее значение (нет поля — по поколению, §8.1; проверка 18, З-4) |
| `PUT /api/gw/route` (есть, `api.py:673`; проверка 19, З-4) — маршрут узла по умолчанию, форма карточки «Маршрут по умолчанию» вкладки «Выходы» (`defaultRouteCard`, `app.js:4494–4506`) со своей полосой | `{via, fail, ver}`; `?dry=1`; без `ver` — 400 `ver_required` | `{ok, route, ver, moves, warnings}`; при `dry` — `{ok, dry, rev, ver, moves, warnings}` (как прогон маршрута человека: `moves` по текущей политике, текущий `ver`); `ver` другой — 409 `object_changed` |
| `GET/PUT /api/gw/probes/speed` (новый; проверка 19, З-2) (D-098) — общие на сервер настройки теста скорости, карточка «Тест скорости» (§7.1) | `{max_mb, max_s, only_quiet, budget_day_mb, budget_month_gb, ver}` (ограничения §3.2); без `ver` — 400 `ver_required`; лишнее поле — 400 `field`; сухого прогона нет | `{max_mb, max_s, only_quiet, budget_day_mb, budget_month_gb, ver}`; запись — `state.transaction()` со сверкой `ver`, без применения шлюза (юнит проб читает `probes.speed` при каждом запуске, §5.7) |

- **Версия политики `rev`** (правка 8, С-2; проверка 16, З-4) (D-079, D-080, D-081). `GET /api/gw/policy` и `GET /api/gw/exits` отдают
  `rev` (`gateway.policy_rev`, §3.1); SSE `rev` не несёт (`live.policy` — ниже; откуда панель берёт `rev` — §7).
  **Требуют `rev`** (тело или `?rev=`) ровно эти запросы: `PUT /api/gw/policy`, `/reset`, `POST /api/gw/groups`,
  `DELETE /api/gw/groups/<gid>`, `PUT /api/gw/exits/<id>` с `quota` или `speed_skip` (а также с `enabled` — ради
  кода, порядок — в строке таблицы), `/manual`, `/country`,
  `/enabled`, `/move`, `DELETE /api/gw/exits/<id>`. Не совпала — 409 `policy_changed` с `data.rev` (D-079), ничего не
  пишется. Успех — `rev` в ответе (счёт — `gw/rev.py`, §3.1). Сухой прогон `rev` не требует; **его ответ несёт `rev`,
  на которой посчитан**: `moves` и `rev` считаются по одному `st` внутри `state.transaction()` (блокировка — только на
  расчёт, без `apply` и сети; `rev` — `rev.note(st)`, §3.1; проверка 18, С-2); её шлёт кнопка или окно (§7, проверка
  17, З-2). Сверка `rev` записи — тоже `rev.note(st)` в транзакции самой записи (D-097); у удаления ключа — в короткой
  транзакции до скрипта пула (§3.1, «Записи через скрипт пула»; проверка 19, С-1). Блокировку прогон ждёт до 15 с, не
  дождался — 409 «занято» (код 6; §7 «Ошибки», проверка 19, З-1).
  **Запрос из этого списка без `rev`** — 400 `rev_required` («Панель обновилась — обновите страницу», RU+EN), ничего
  не пишется: HTTP API зовёт только панель (CLI и MCP идут через `kaskadctl`, там `rev` необязателен, §7.7–7.8), так
  старая панель из кэша браузера не пишет мимо сверки (придирка 4). Проверка — у каждого маршрута списка, не общая
  для `/api/gw/*`. **Остальные записи `/api` `rev` не требуют** и без него не отказывают: добавление ключа
  `POST /api/gw/exits` (с `group` и без; ответ отдаёт новую `rev` — для перекладки форм, §7), подписки выходов
  (`/api/gw/exit-subs*`; ответ записи тоже отдаёт `rev` — подписка меняет пул; проверка 17; `rev` — `note` после
  скрипта, §3.1), маршруты людей, устройств и узла (`/route`, `PUT /api/gw/route`), `/api/gw/probes/speed`,
  `/api/settings/time`, `/api/notify` (в `rev` не входят, требуют свою `ver` — ниже),
  `PUT /api/rules/*`, имена (`PUT /api/gw/exits/<id>` с одним `name`, `PUT /api/gw/groups/<gid>`, RN §4.1 (D-081, D-086) — `rev` не
  требуют и не меняют). Без `rev` идут и две записи, которые `rev` поднимают: `POST /api/gw/enable` (первая
  политика — `ensure_initial`, §3.1) и восстановление копии `POST /api/backup/import` (`rev.bump`): они пишут
  состояние целиком; открытые формы после них — перекладкой (§7; проверка 18, придирка 2).
- **Версия объекта `ver`** (проверка 17, придирка 5) (D-095, D-098) — у форм вне `rev`: маршрут человека и устройства
  (`PUT /api/gw/users/<uid>/route`, `…/devices/<did>/route`), маршрут узла по умолчанию (`PUT /api/gw/route`;
  проверка 19, З-4), общие на сервер настройки теста скорости (`PUT /api/gw/probes/speed`; проверка 19, З-2), «Время»
  (`PUT /api/settings/time`), «Уведомления» (`PUT /api/notify`). `ver` — 12 hex `sha1` от канонического JSON самого
  объекта (маршрут — `route` и с T15 `ru_via`; маршрут узла — `gateway.routes.default`; тест скорости —
  `exit_policy.probes.speed`; `tz`; у «Уведомлений» — весь `gateway.notify`: `push` и `flap_summary`, проверка 18,
  З-4). `GET` отдаёт его рядом с объектом: `GET /api/gw/people` — `route_ver` у человека и устройства,
  `GET /api/gw/exits` — `default_route_ver`, `GET /api/gw/probes/speed`, `GET /api/settings/time` и `GET /api/notify`
  — `ver`. **«Правила»** (RF §5.1, R3/R4; сведение с R1/N1) (D-100): объект — профиль узла `gateway.rules.default`
  (действующее значение `model.rules_conf`), `GET /api/rules` отдаёт `ver`, `PUT /api/rules/profile` с
  `target: "default"` его сверяет. Профиль человека и устройства (`target: "user"|"device"`), переключатели рекламы и
  торрентов (`PUT /api/rules/blocks`) и «Подтвердить» ⚠РФ (`PUT /api/rules/consent`) — действия «сразу» над одним
  полем, без `ver`: они не трогают других полей, как имена. Галочка ⚠РФ в объект не входит — это согласие, а не
  правка правил (RF §3). Запись с `ver` сверяет его под той же блокировкой, что и запись:
  не совпал — 409 `object_changed` с `data.ver`, ничего не пишется; успех — новый `ver` в ответе; сухой прогон
  маршрута отдаёт текущий `ver`. **Запрос HTTP без `ver`** — 400 `ver_required` («Панель обновилась — обновите
  страницу», как `rev_required`; проверка 18, З-5): HTTP API зовёт только панель, а без `ver` приходит лишь старая
  вкладка, которая иначе молча затёрла бы правку бота или SSH. Без `ver` пишут только вызовы Python (CLI, MCP, бот,
  SSH-меню — мимо HTTP), как сейчас: маршрут узла из CLI (`kaskadctl gw route`) — так же, как маршруты людей и
  устройств. Ещё одна запись маршрутов по HTTP без `ver` — схема (`POST /api/scheme/apply`, намерения `route`): её
  защищает своя версия графа `graph_version`, которую шлёт экран схемы (`scheme.py:1275–1280`, под блокировкой —
  `:1294`); маршрут, изменённый схемой, меняет `ver`, и открытая форма маршрута получит 409 (проверка 19, придирка 7).
  В `rev` не входит. Владелец — T7 (API), T8 (формы); «Правила» — R3 (сверка в `rules/ops.set_default`), R4 (HTTP и
  форма).
- **Повтор `DELETE` по уже удалённому** (D-132) (проверка 16, З-5; заменяет «`ok` до сверки» точечной проверки, З-7). Ответ —
  404 с кодом `gone`, `data.gone: true` и текстом «…обновите страницу»; панель считает его успехом («Уже удалено», без
  ошибки). **Сейчас** кода и `gone` в ответе нет (проверка 17, З-3): `api.py:1044–1045` отдаёт 404 с `data =
  {"exit": id}`, подписки (`:1104`) — `{"sub": id}`, `HttpError` (`routes.py:117`) кода не ставит, `gone` есть только
  в исключении ядра. T7 добавляет `code: "gone"` и `data.gone: true` (поля `exit`/`sub` остаются); тест
  `tests/panel/test_panel_api.py:495–496` проверяет `data.gone`. 404 на несуществующий путь `gone` не несёт.
  **Объекта нет** у остальных запросов к одному объекту — `/manual`, `/country`, `/enabled`, `/move` (и их сухие
  прогоны), `PUT /api/gw/exits/<id>`, `PUT /api/gw/groups/<gid>` — тоже 404 `gone` с `data.gone: true` (поля
  `exit`/`group` остаются): «Уже удалено» панель показывает только по `gone` (§7; проверка 18, придирка 4).
  Порядок: объект не найден — 404 `gone` **до** сверки `rev` (повтор после обрыва приходит со старой `rev`) — у группы
  и у ключа с id без суффикса (id первой строки с этим ключом: такой id не найден — ключа в пуле нет). Id повтора
  ключа `e-<hex>-<N>` зависит от номера строки (`gw/exits.py:424`, `:486`): удаление строки выше сдвигает такие id
  (пул `[A, B, A]`, удалили `B` — `e-A-3` стал `e-A-2`), поэтому для них сначала сверка `rev` (не совпала — 409), и
  только потом 404 `gone`. Объект найден — сверка `rev`, как у всех. CLI и MCP `exit-del` не меняются (тот же
  `ops.exit_del`, «уже удалён» — ошибка).
- **Исключение из сухого прогона:** форма «Правила» (`PUT /api/rules/profile`, RF §5.1) — сухого прогона правил в
  этапе 1 нет (этап 2); общие настройки теста скорости (`PUT /api/gw/probes/speed`) — путь трафика не меняют
  (проверка 19, З-2); «что изменится» у них — только известные заранее строки.
- **Сухой прогон** (правки 5–8, UX): `?dry=1` у всех запросов, меняющих путь трафика, — по одному на форму или
  кнопку (§7). Формы: `PUT /api/gw/policy` (карточка группы), `PUT /api/gw/exits/<id>` (квота). **Действия над одним
  объектом — свои запросы** (правка 7): «В запас»/«Вернуть»/«Обслуживание» — `POST /api/gw/exits/<id>/manual`,
  «Принять новую страну» — `POST /api/gw/exits/<id>/country`, «Перенести в группу» и «Добавить выход» —
  `POST /api/gw/exits/<id>/move`, вкл/выкл выхода — `POST /api/gw/exits/<id>/enabled`, «Удалить ключ» —
  `DELETE /api/gw/exits/<id>`, «Удалить группу» — `DELETE /api/gw/groups/<gid>`, «Включить «Стандарт»» и сброс —
  `POST /api/gw/policy/reset`. Действие строится от сохранённого состояния; на сервере правок
  форм нет, поэтому выключение кнопок при несохранённых правках — в панели (§7). Маршрут — форма на
  другом экране («Люди», своя полоса): **`PUT /api/gw/users/<uid>/route`** и **`PUT /api/gw/devices/<did>/route`**
  (есть, `api.py:858`, `:917`); маршрут узла по умолчанию — `PUT /api/gw/route` (карточка вкладки «Выходы»; проверка
  19, З-4). Ответ на `dry` — `{ok, dry: true, rev, moves, warnings}` (`rev` — абзац «Версия
  политики» выше), состояние не пишется; тексты «что
  изменится» строит панель из `moves`; строки, известные заранее («xray перезапустится», квота), сервер не отдаёт —
  их считает панель (§7). Владелец
  — T7.
- SSE (`panel/sse.py`): `alert.kind` += `exit_degraded`, `exit_recovered`, `exit_standby`, `exit_removed`,
  `exit_failover`, `exit_quota`, `exit_country_changed`, `group_last_step`, `group_restored`, `node_offline`,
  `node_online`; `live.exits[id]` += `state`, `reasons`, `tier`, `rtt_ms`, `loss_pct`, `group`, `recovering`, `off_until`, `unstable`, `unstable_until`, `manual_why`; `live.groups[gid] =
  {active_tier, last_step, since}`; `live.no_exit` (§4.4); `live.policy` = `{node_offline}`.
- Российские группы (`PUT /api/rules/ru-via`, поле `ru_via` у людей и устройств, `devices_ru`) — T15, SPEC-groups §8.9.
- Мок `dev-mock.js`/`dev-mock-en.js` повторяет этот контракт до готовности T7: две зарубежные группы, устройство на
  `last_block` и на `last_direct`.

### 7.10 Бот — нынешние кнопки с группами (находка А5)
Кнопки бота остаются (решено 14.10, ночь) (D-027), но должны понимать группы. Владелец — T10 (`bot/gateway.py`):
- `_route_text` (`bot/gateway.py:85-106`): маршрут `group:<gid>` показывается **именем группы** («Группа
  «Стриминг»»), а не сырым `group:g-7c1d2e`; `pool` подписан показанным именем `main` (`policy.label`, §3.1:
  «Группа «Основная»» / «Main group», после переименования — её имя; `bot.gw.route_pool` с `{name}`).
- Выбор маршрута (`:644-647`, `exit_rows`): только выходы зарубежных групп (`exits.pool()` по умолчанию, §3.1);
  выбора группы в боте нет (новое — только в браузере), маршрут к другой группе показывается, но меняется в панели.
  Запись маршрута — через `model.check_route`.
- Кнопки `gwrf` (`:690-699`, `cb_route_fail`) — без нового значения; подпись «Блок» → «Как у группы».
- Пуши новых событий (§8) — бот присылает; управления группами в боте нет.

---

