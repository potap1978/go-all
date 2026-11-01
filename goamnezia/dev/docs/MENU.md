# goAmnezia pro — дерево SSH-меню

Дерево из [PLAN.md](../PLAN.md) §5.1 с именами функций `install.sh` и ключами словаря
(`lib/lang/ru.d/menu.sh`, `lib/lang/en.d/menu.sh`). Файл был источником для
ручного обхода экранов (теперь `docs/archive/MENU_REVIEW.md`): по нему сверяли, что экран есть, что его строки
переведены и что разрушительное закрыто подтверждением.

Правила экранов (одинаковы во всех подменю, шаблон взят из goTelegram):

- заголовок `${BOLD}${WHITE}$(t <title>)`, под ним линейка `─` шириной 54;
- пункты `${CYAN}N${NC}) $(t <key>)`, последним `0) $(t back)`;
- ввод `read -r ch || ch=0` — EOF на оборванной SSH-сессии обязан читаться как «Назад»,
  иначе ветка `*)` и повторный `read` дают busy-loop на 100 % CPU;
- неизвестный пункт → `log_error "$(t invalid_choice)"`;
- после действия пауза `_pause` → `$(t press_enter)`;
- «0» — назад СРАЗУ, без «Нажмите Enter»: экран или выборщик, закрытый нулём, ставит `GA_BACK=1`,
  и ближайший `_pause` пропускается один раз (сброс — в `_screen_head` и `show_main_menu`); голый
  Enter в меню — перерисовать (`"") continue`), а не «неверный пункт» (25.09.2026);
- глубина не больше двух уровней, третий — конечная карточка (пользователь, профиль).

Ключи `choose`, `back`, `exit`, `press_enter`, `invalid_choice`, `menu_*`, `running`,
`stopped`, `not_installed`, `cancelled`, `err_no_jq` берутся из ядра словаря
(`lib/lang/ru.sh`), в `menu.sh` они не дублируются.

---

## 0. Вход

| Что | Функция | Комментарий |
|---|---|---|
| точка входа | `main` | `--action=` разбирается ДО всего; затем `--selftest`, `--version`, `--help` |
| проверки окружения | `_ga_preflight` | `check_root`, `get_arch`, `init_dirs`; под `GOAMNEZIA_DRY_RUN=1` root и архитектура не проверяются |
| зависимости | `_ga_ensure_deps` | `check_deps_present` → `deps_install_missing --yes core` (`lib/deps.sh`); в `GOAMNEZIA_DRY_RUN=1` только предупреждение `deps_missing_dry` |
| миграция | `auto_migrate_state` / `_auto_migrate_state_locked` | маркер `.migrated_<ver>`, снимок `snapshot_preupgrade_state` |
| рабочие копии | `auto_update_components` → `auto_update_component <src> <dst> <unit> [create]` | `cmp -s` → `cp` (мимо `.env`, `token`, `venv/`) → рестарт юнита; бот и панель обновляются только если уже установлены своими установщиками, рабочая копия MCP (`/opt/goamnezia-mcp`) создаётся здесь же (`create`, `upd_component_created`), и `_mcp_ensure_symlink` перенацеливает `/usr/local/bin/goamnezia-mcp` на неё |
| язык при первом запуске | `first_run_language_picker` | `pick_language_interactive` / `save_language` |
| баннер | `show_banner` (из `lib/common.sh`) | ключи `banner_title`, `banner_subtitle`, `banner_features` |
| ченджлог после обновления | `show_upgrade_changelog` | `upd_title`, `upd_l1`…`upd_l12` (до двенадцати) |
| первая установка | `_wz_session` → `install_wizard` | если `config.json .installed != true` или установка не закончена (запись `.wizard`; прерванную установку прежней версии — режим с сайтом без vhost и без `site.cert` — узнаёт `_wz_legacy_pending`); мастер на сервере один (`_wz_lock`, `$LOCK_DIR/wizard.pid`); конец ввода (3) и второй мастер (4) — выход без меню |
| таймеры продукта | `_timers_apply` → `timers_install` (`lib/timers.sh`) | при установленном продукте на каждом интерактивном запуске, в мастере после `installed=true` и в миграции; идемпотентно |
| сверка с контейнерами | `_import_reconcile_prompt` → `import_reconcile` по установленным профилям с живым контейнером | приложение Amnezia могло добавить/удалить пира напрямую: показывает разницу и спрашивает `import_reconcile_ask` (PLAN §6.5) |
| цикл меню | `main` | `read -t 30`, таймаут (`rc > 128`) = перерисовка, EOF (`rc != 0`) = выход |

## 1. Главный экран — `show_main_menu`

Компактный дашборд: версия, режим (`dash_mode`, название из `_mode_name` → `ms_mode_name_lite` /
`ms_mode_name_lite_site` / `ms_mode_name_pro`), адрес (`dash_ip`, `_server_ip` — из
`config.json`, в сеть только если поле пусто), домен (`dash_domain`),
профили значками `●` работает / `○` остановлен / `✗` нет (`dash_profiles`, `_profile_icon`,
`_profile_state` → `amnezia_profile_status`), число пользователей (`dash_users`,
`_users_count` → `users_list`), подпись `dash_auto_refresh`. У профиля — его линия из `config.json`: «● awg2 (2.0)
:443» (NC6-09); режим с сайтом на домене, который поставил Ленивый Pro (`.lazy.domain` = `.domain`), подписан
`dash_mode_by_lazy` «(поставлен Ленивым Pro)» (NC6-04). Установка не закончена (запись `.wizard`: мастер отменён,
сорвался шаг, «0 — в меню» на экране продолжения) — строка `dash_wizard_pending` «Установка не закончена (выбран режим
«…»)… запустите goamnezia снова» под режимом; сам режим — действующий (режим с сайтом пишется, только когда сайт поднят,
INST-01). «На связи» в шапку не добавлено: живой счёт — `docker exec`
на каждый профиль при каждой перерисовке (раз в 30 с), а `users.json` отстаёт до 5 минут; число — на экране
«Трафик и онлайн».

Под таблицей адресов — строки-тревоги шапки: откат обновления (`_dash_update_notice`), место на диске (`_dash_disk_warn`),
старая служба Docker (`_dash_docker_stale_warn`) и с 1.0.12 модуль ядра AmneziaWG без исправлений (`_dash_kmod_warn` →
`dash_kmod_unpatched` «Модуль ядра AmneziaWG без исправлений: профиль 3.1 может повесить сервер при изменении
пользователей»): на хосте загружен `amneziawg` без метки `+kaskad-k31` в `/sys/module/amneziawg/version`, а профиль 3.1
есть (`status.kmod.warn`, `ga_amn_kmod_json`). Только строка: продукт ничего не ставит и не перезапускает — выбор и
пересборка будут в этапе Б (`docs/KMOD_PLAN.md`). Та же тревога — на главном экране бота и на «Обзоре» панели и мини-аппа.
С 1.0.12 (S4-FIX, MTU-01) — ещё строка на каждый профиль с большим S4 (`_dash_s4_warn` → `amnezia_s4_risk` «Профиль
AmneziaWG 2.0 (awg2): на части сетей (мобильный интернет, PPPoE, некоторые хостеры) скачивание не идёт — слишком большие
пакеты (S4 = 142).» + `dash_s4_hint` «Исправить: «Протоколы» → профиль → «Исправить S4».»): при стандартном MTU 1280 пакет
данных 1280 + 80 + S4 больше 1400 (`ga_amn_s4_risky`). Считается по тому же снимку конфига (`_dash_snapshot`,
`GA_DASH_S4`), без Docker и без python.

| № | Ключ | Куда ведёт |
|---|---|---|
| 1 | `menu_users` | `submenu_users` |
| 2 | `menu_status` | `submenu_status` |
| 3 | `menu_protocols` | `submenu_protocols` |
| 4 | `menu_maintenance` | `submenu_maintenance` |
| 5 | `menu_settings` | `submenu_settings` |
| 6 | `menu_bot` | `submenu_bot` |
| 0 | `menu_exit` | выход, `bye` |

## 2. Пользователи — `submenu_users` (заголовок `users_title`)

Сверху таблица `_users_render_list`: значок `● / ○ / ⏳ срок / ▮ лимит`, идентификатор,
отображаемое имя, профили, трафик, дата окончания. Пусто → `users_none`.

Список берётся через `ga_capture js _users_json`, а НЕ через `$( )`: подстановка команды уводит
`_users_json` в дочерний процесс и теряет флаг `GA_USERS_BROKEN`, из-за чего неразбираемый
`users.json` рисовался словами «пользователей пока нет» — человек читает это как «сервер пуст» и
заводит людей заново поверх существующих ключей. Сломанный файл — отдельный экран:
`users_err_corrupt` («восстановите из бэкапа»), и ни одной строки таблицы. В карточке дашборда то
же различие держит `_users_count` (`st_users_broken`).

Управление ТОЛЬКО цифрами (решение владельца 12.09.2026): пункты нумеруются сразу за
последней строкой списка (`GA_USERS_LAST`), поэтому с номерами строк не сталкиваются
никогда, а глаз находит их там, где кончился список. Номера названы в легенде
`users_pick_legend` на каждом показе — их не надо запоминать.

| № | Ключ | Функция |
|---|---|---|
| номер строки | — | `_user_card` по строке списка |
| `N+1` | `users_add_item` | `_user_add_interactive` → `users_add` |
| `N+2` | `users_bulk_item` | `_users_add_bulk_interactive` → `users_add_bulk` — «Добавить сразу несколько (до 100)» (просьба владельца 03.10) |
| `N+3` | `users_bulk_del_item` | `_users_del_bulk_interactive` → `users_del_bulk` — «Удалить сразу несколько» (просьба владельца 03.10) |
| `N+4` | `users_open_item` | `_user_card <name>` (принимает и номер строки, и имя) |
| `N+5` | `share_ui_list_title` | `_shares_screen` — активные раздачи |
| `N+6` / `N+7` | `users_page_next` / `users_page_prev` | листание, показываются только когда страниц больше одной |
| 0 | `back` | выход из подменю |

Вопросы мастера добавления: имя — `_user_add_ask_name` (`user_ask_name_random`: своё имя латиницей или пустой
Enter / «?» — «🎲 Случайное имя»: `users_name_suggest` на языке меню, строка `user_name_suggested` «🎲 Туманный
Лондон · foggy_london» и `user_name_take_prompt` «Enter — взять, r — другое, или введите своё»; взятый вариант
заводится с его подписью; нет набора или свободных — слова ядра или `user_name_random_failed`), затем
`user_ask_profiles`, `user_ask_quota`, `user_ask_days`; отказ — `user_bad_name`, `user_add_failed`. После удачного заведения —
`user_added_key_ask`: ключ предлагается сразу, в той же цепочке (§1d.2 №3), а список
возвращается на страницу с новой записью (`GA_USERS_FOCUS`, иначе при двадцати с лишним
людях новый уходит в конец, а список остаётся на прежней странице). С доменом (у страницы подключения есть где
жить, `_pages_domain_ok`) вопрос — `user_added_key_ask_page` (называет и страницу для друга), а в «что дальше»
(`_key_next_steps`) первым пунктом — `kn_page` «Страница подключения для друга»: страниц у человека нет —
`_pages_create`, есть — `_pages_screen`, как пункт 7 карточки; временная ссылка там — `kn_link` «перенести ключ на
свой телефон за 5 минут» (1.0.6, NC6-01, NC6-02). Лимит в вопросе и в итоге — «за всё время, без сброса по месяцам»
(`user_ask_quota`, `user_quota_set_total`, NC6-06).

«Добавить сразу несколько» (`_users_add_bulk_interactive`): `users_bulk_intro`, число `users_bulk_ask_count` (1–100,
0 — назад), те же галочки профилей (`_user_pick_profiles` — общий помощник с одиночным добавлением), имена
`users_bulk_ask_prefix` (Enter — случайные «прилагательное + столица», префикс — `<префикс>-01…`, 0 — назад),
подтверждение `users_bulk_confirm` → `users_add_bulk` (lib/users.sh: тот же `_users_add_locked` на каждого, одно
применение к контейнерам в конце; отказы до изменений — нет мест в подсети, мало случайных имён; сбой на середине —
добавленные остаются, модуль называет, на ком встало). Итог — `users_bulk_list_title` и список (подпись и id), где
ключи — `users_bulk_where_keys`, с доменом — ещё `users_bulk_where_pages` (страница подключения — в карточке
человека: создать страницы всем сразу продукт не умеет). Лимит и срок не спрашиваются — их ставят в карточке.

«Удалить сразу несколько» (`_users_del_bulk_interactive`): `users_bulk_del_intro`, способ — `select_option`
`users_bulk_del_how`: по префиксу (`users_bulk_del_ask_prefix`, удаляются `<префикс>-<число>`, как их заводит массовое
добавление), по номерам строк списка (`users_bulk_del_ask_rows`: `3-7,12`, номера сквозные, как на экране —
`_users_rows_to_names` переводит их в имена по `GA_USER_NAMES`; не та запись или номер вне списка —
`users_bulk_del_bad_rows`, больше 100 строк — `users_bulk_del_rows_many`), выключенные руками, истёкшие, с исчерпанным
лимитом; 0 — назад. Кого задевает выбор, считает ядро (`users_del_bulk_preview`, то же правило, что при удалении):
никого — `users_bulk_del_nobody`, больше 100 — `users_bulk_del_too_many`; иначе `users_bulk_del_list_title` и до 20 имён
(подпись и id), «…и ещё N» — `users_bulk_del_more`, предупреждение `users_bulk_del_warn`. Подтверждение — числом людей
(`confirm_phrase`, как у одиночного удаления, вопрос `users_bulk_del_ask`) → `users_del_bulk` (lib/users.sh: выключить →
применить к контейнерам → стереть, пачками по 10; список изменился между показом и удалением — отказ
`confirm_mismatch` до изменений). Итог — `users_bulk_del_done_title` и удалённые; оставленные выключенными (контейнер не
принял пачку) — `users_bulk_del_kept`; на ком встало — говорит модуль.

Экран раздач (`_shares_screen`) работает на `share_list` / `share_revoke` / `share_revoke_all`
и строках `share_ui_list_*`, `share_ui_revoke_*`: создать раздачу из SSH было можно, а снять —
нечем, хотя и модуль, и мост, и строки словаря были готовы. Секретной части адреса на экране
нет и быть не может — `share_list` её не отдаёт.

### 2.1 Карточка пользователя — `_user_card` (третий уровень, конечная)

Шапка `user_card_title`, поля `user_state` (`state_enabled` / `state_disabled`),
`user_quota_label` (`unlimited`), `user_traffic_label`, `user_expiry_label` (`never`),
`user_profiles_label`; строка адреса сервера в ключах `user_address_line_*` — только когда у машины два
внешних адреса и больше или адрес пользователю уже задан (`_user_address_offer`).

Карточка была на четырнадцать пунктов и читалась как стена. Близкое собрано в два
подменю: всё про выдачу ключа — в «Ключ», всё про набор профилей — в «Профили».

| № | Ключ | Функция / модуль |
|---|---|---|
| 1 | `user_keys_menu` | `_user_keys_menu` (заголовок `user_keys_menu`, под ним `user_menu_for`): 1 `user_key_qr_conf` (`export_conf` + `qrencode -t UTF8`), 2 `user_key_text_conf` (`export_conf`; файл `<пользователь>-<линия>.conf` по `ga_key_file_base` — `anna-2_0.conf` — в своём каталоге `ga_mktemp_secret_dir` (0700, случайное имя), под ним `key_saved_to` и готовая строка `scp` (`key_saved_scp`; вошли через sudo — файл отдан `SUDO_USER`, каталог 0711); пауза `key_saved_enter`, после Enter каталог затирается (`ga_wipe_dir`); Ctrl+C, TERM и обрыв SSH на паузе стирают его ловушкой `_key_conf_signal`; забытые `.goamnezia-conf.*` старше 10 минут стирает `_key_conf_sweep` перед новым показом — A-2, R-2 критиков 16.10), 3 `user_key_vpn_link` (`export_vpn`), 4 `user_key_qr_vpn` (`export_vpn` + `awgkeys.py qr-chunks`), 5 `share_menu_item` (`_user_share`: выключенному — `share_user_disabled` до выбора профиля, ядро ссылку не создаст (`user_disabled`, A3-5); профиль → формат из двух, как у бота и панели, — `conf` или `vpn` (файл `.vpn`); QR сканируют с экрана, по ссылке только файл (TAIL-14) — → `share_create`, ссылка живёт пять минут, файл называется так же, как «Текст .conf»). Если у человека есть импортированные пиры, экран говорит об этом до нажатия (`user_imported_note`). Хвост нумеруется подряд, без дыр, и пункты в нём есть, только когда есть что выбирать: 6 `user_key_addr_item` — «Ключ на другой адрес сервера (разово)» (`_pick_key_address`, см. ниже), когда его предлагает `_key_addr_offer`; следующий номер (7, а без пункта про разовый адрес — 6) — `user_address_item` (`_user_address_screen` → `users_set_endpoint_ip`) — только когда у машины два внешних адреса и больше или адрес пользователю уже задан (`_user_address_offer`): строки — адреса машины с числом пользователей, общий подписан (`user_address_common_mark`), и выбрать его — вернуть общий, а не записать копию; над пунктами — что сейчас в ключах (`_user_address_line`), после смены — надо ли выдать ключ заново (`_user_address_note`, те же слова у моста `user-set-address`) |
| 2 | `user_profiles_menu` | `_user_profiles_menu`: 1 `user_add_profile_item` (`users_add_peer`, только профили, которых ещё нет), 2 `user_del_profile_item` (`confirm` `user_peer_del_warn` → `users_del_peer`), 3 `user_peer_reissue_item` (`_user_peer_reissue`: `user_peer_reissue_warn`, под ним — что будет у пользователя по фактам (`_user_reissue_facts` → `_user_reissue_facts_text`, Н-4): есть страница с этим профилем — `ur_facts_pages` (страница сразу отдаст новый ключ, открыть заново), подписка отдаёт ключ этого профиля — `ur_facts_sub` со сроком `_user_sub_within` («до 12 ч» или «до 12 ч, у FlClash — до 24 ч»: `update_slow`), и всегда `ur_facts_manual` (отправленное вручную — заново); ни страницы, ни подписки — `ur_facts_only_manual`; модуль не ответил — условно (`ur_facts_unknown`); затем `confirm` → **`users_reissue_peer`**, одно действие ядра: подмена под общим замком, адрес прежний, при сорвавшемся применении возвращается прежний ключ) |
| 3 | `user_set_quota` | `_user_edit_quota` → `users_set_quota`; вопрос показывает текущий лимит, ноль назван «без ограничения» |
| 4 | `user_set_expiry` | `_user_edit_expiry` → `users_set_expiry`; вопрос показывает текущий срок, ноль назван «бессрочно» |
| 5 | `user_enable_item` / `user_disable_item` | `users_enable` / `users_disable` |
| 6 | `user_rename_item` | `_user_rename` → `users_rename` |
| 7 | `user_page_item` | `_pages_screen` (см. 2.2) — страница подключения: список страниц человека, создание, карточка |
| 8 | `user_delete_item` | `_user_delete` → `confirm` (`user_delete_warn`, `user_delete_ask`) → `users_del` |
| 0 | `back` | назад в список |

Профиль для ключа выбирается в `_pick_user_profile` (если он один — без вопроса):
заголовок `user_pick_profile_title`, строки — имена линеек (`pr_awg*`), `0` = `back`,
вопрос через `ga_ask` с тремя попытками. Коды возврата различаются и разбираются общим
`_pick_profile_into`: 1 — профилей нет (`user_no_peers`), 2 — передумал (`cancelled`).
Раньше на любой промах печаталось «нет ни одного профиля», хотя экран только что показал два.
Нет `qrencode` — `qr_missing`.

Ключ ВЫКЛЮЧЕННОГО человека выдаётся, но не молча: `ga_export_peer` смотрит на `.enabled`
пользователя и `.active` пира и печатает `export_warn_disabled`, а `export_bundle` несёт
признак полем `disabled` — то есть бот и панель говорят то же самое (§1d.2 №1).

**Ключ на другой адрес сервера (разово)** — пункт 6 экрана ключей, `user_key_addr_item` (docs/EDGEIPS.md §3.4
«Разовый ключ на адрес», CONTRACT §4.6). Тот же ключ — та же пара ключей и тот же порт профиля — с другим
адресом сервера внутри: другим IPv4 этой машины или её настроенным доменом. Пункт есть, когда у человека есть
приватный ключ хотя бы в одном профиле (у подхваченных из приложения его нет) и у сервера есть хост, отличный
от того, что сейчас в ключах человека (`_key_addr_offer`: `ga_export_key_hosts` против
`ga_export_endpoint_host <имя>`); на машине с одним адресом и без домена пункта нет.

Выбор — `_pick_key_address`: заголовок `user_key_addr_title` («На какой адрес сервера выдать ключ <имя>?»),
пояснение `user_key_addr_note` (ключ тот же, меняется только адрес внутри, за пользователем ничего не
сохраняется); `1)` `user_key_addr_usual` — «как обычно: <текущий адрес>», он же сбрасывает выбор; дальше
остальные варианты `export_key_choices_json` (по `ga_edge_status_json`; текущий адрес второй раз не
показывается) в порядке ядра — домены, затем адреса, замолчавшие в конце — с пометками: `user_key_addr_domain_mark`
(«домен»); вердикт сторожа (он есть, только когда у машины два адреса и больше) словами таблицы «Адреса
сервера» — `edge_v_ok`, `edge_v_dark`, `edge_v_quiet`, `edge_v_quiet_all`, а вместо `unused` —
`user_key_addr_unverified` («не проверен — сюда ещё никто не подключался»); адрес, которого нет в A-записях
домена из ключей, — `user_key_addr_spare_mark` («нет в A-записях — запасной»). `0` и пустой ввод — назад,
выбор прежний; замолчавший адрес выбрать можно, но с предупреждением `user_key_addr_dark_warn`. Выбор адреса —
настройка экрана, а не действие: «Нажмите Enter» после него нет.

Пока адрес выбран, над пунктами стоит жёлтая строка `user_key_addr_active` («Выдача на адрес <хост> — разово,
пока открыт этот экран.»), и пункты 1–5 выдают ключ с ним: хост уходит третьим аргументом в `export_conf` /
`export_vpn` и четвёртым — в `share_create`; заголовки ключа — `key_title_conf_at` / `key_title_vpn_at`
(« · адрес <хост>»), под ссылкой на файл — `share_ui_endpoint`; файл называется так же, как без адреса (`anna-2_0.<ext>`, П-2).
После выдачи в пунктах 1–4, если на экране есть пункт `user_address_item`, — подсказка
`user_key_addr_pin_hint` («Чтобы и следующие ключи шли на <хост>, а сторож считал человека на нём, — пункт
«Адрес сервера в ключе».»). Выход с экрана выбор забывает, в `users.json` не пишется ничего. Сторож адресов
(`lib/edgeips.sh`) считает людей только по закреплённому адресу, а доменных — по A-записям, поэтому разовые
ключи ему не видны: кого надо считать на адресе и прикрывать тревогой `dark`, тому адрес закрепляют — пункт
`user_address_item` (мост `user-set-address`). Одна пара ключей — один пир: тот же ключ одновременно с двух
адресов (два устройства или две записи одного ключа в приложении) будет «прыгать»; для одновременных
устройств — отдельные пользователи.

Проверка: `tests/test_wizard_pty.py::KeyForAnotherAddress` — с двумя адресами у машины пункт 6 есть, выбор
адреса без «Нажмите Enter», строка «Выдача на адрес …», в `.conf` — выбранный адрес, `endpoint_ip` не
записан; с одним адресом пункта нет.

### 2.2 Страница подключения — `_pages_screen` (заголовок `user_page_item`, модуль `lib/pages.sh`, docs/CLIENTS.md §10)

Страница в браузере с ключами человека, приложениями под его устройство и копиями сборок с
зеркала; живёт только на домене с настоящим сертификатом. Адрес — секрет: печатается **только
`echo` в терминал** (не через `log_*`; в аудит модуль пишет лишь `id`), показывается по явному
действию и рядом рисуется QR (`_qr_utf8`, как у ключа: текст идёт в `qrencode` через stdin, а не argv, — в argv его видел бы `ps`; не влез — сам адрес уже напечатан).

Экран списка (`pages_list <имя>`): строки `pg_list_row` — идентификатор, срок (`pg_until` или
`never`), открытий, ключи; пусто — `pg_list_empty`. Номер строки открывает карточку, пункты
нумеруются за списком (`pg_pick_legend`).

| № | Ключ | Функция / что делает |
|---|---|---|
| номер строки | — | `_pages_card <id> <имя>` — карточка: `pg_card_created`, `pg_card_expires` (`never` у бессрочной), `pg_card_visits` и `pg_card_last` (`pg_never_opened`), `pg_card_profiles`, `pg_card_mirror` (`pg_mirror_on` / `pg_mirror_off`), подсказка `pg_card_hint`; 1 `pg_item_url` → `pages_url <id>` → `_pages_show_url` (адрес, срок, ключи, предупреждение `pg_is_key` / `pg_forever_warn`, QR); 2 `pg_item_revoke` (ссылка могла быть уже у пользователя): у пользователя есть подписка **сейчас** (`_pages_user_has_sub`; страница отдаёт её адрес на лету) — выбор `pg_revoke_sub_note`: 1 `pg_revoke_with_sub` (с пометкой `pg_recommended`, если подписку завела эта страница — `subscription: created`), 2 `pg_revoke_page_only`, 0 — назад; подписки нет — `confirm` `pg_revoke_ask`. Страницу открывали (`visits` > 0) и на сервере есть что перевыпустить — `pg_revoke_keys_note` с числом открытий и отдельный `confirm` (по умолчанию «нет»): `pg_revoke_keys_ask_page` / `_sub` / `_both` с профилями страницы и, при отзыве с подпиской, её линий (`ga_pages_revoke_key_profiles`, B3-3, Н-1) → `pages_revoke <id> <sub> <keys>`; 0 — назад |
| `N+1` | `pg_item_create` | `_pages_create <имя>`: `pg_intro` → срок цифрами `_pages_ask_ttl` (`pg_ttl_pick`: 1 час · 24 часа · 7 дней · 30 дней · бессрочно · своё значение — `ga_ask` `pg_ask_custom` по регексу договора `12h` / `3d` / `forever`, границы 720 часов и 365 дней проверяются здесь, `pg_bad_ttl`) → `pages_create <имя> <срок>` → `_pages_show_url`. Выключенному человеку страницу не создаём: `pg_user_disabled` до вопроса о сроке (по `users.json`, включается он пунктом 5 карточки, а не командой). Отказ `pages_need_domain` (Lite и запасной путь без домена): причину печатает модуль (`pages_err_need_domain`), экран добавляет `pg_need_domain` и спрашивает `pg_offer_share_ask` → `_user_share` — прежний путь через временную ссылку на файл. Отказ `no_private_key` (все выбранные ключи выданы в приложении Amnezia): текст модуля `pages_err_no_privkey` пути по меню не называет — его показывают как есть и бот, и панель (UI-9); где «Привязать старый ключ» в SSH, экран добавляет строкой `pg_attach_where` |
| 0 | `back` | назад в карточку пользователя |

Без модуля (`pages_list` нет) экран говорит `module_missing`, как везде. Проверка:
`tests/test_wizard_pty.py::PagesScreen` — пункт на экране карточки, удаление осталось последним,
список пуст, в Lite создание честно отказывает и предлагает ссылку на файл, адреса и QR нет.

## 3. Состояние — `submenu_status` (заголовок `st_title`)

| № | Ключ | Функция |
|---|---|---|
| 1 | `st_item_status` | `_status_report` — профили, порт, линейка, версия образа словом «образ 3.1.…» (`st_image_fmt`, колонка `col_core`; NC6-09), Docker, число пользователей |
| 2 | `st_item_traffic` | `_traffic_report` → `amnezia_show` всех профилей сначала: сверху `st_online_now` «Сейчас на связи: N из M» (люди с подключением моложе 3 минут из всех в списке); в строках `●` на связи / `○` давно не выходил / `–` ещё не подключался (рукопожатия не было), имя с подписью, если она другая (NC6-08) + трафик по пользователям + `_traffic_summary` → `stats_summary` |
| 3 | `st_item_logs` | `_logs_screen` → `_logs_print <component> <высота окна>`; в списке и **журнал действий** (`mcp`, `st_logs_audit`) — «кто что сделал»: на диске построчный JSON (CONTRACT §7.1), человеку печатается словами |
| 4 | `st_item_addr` | `_addresses_report` — IP, домен, Endpoint, `host:port/udp` по профилям |
| 0 | `back` | |

В «Трафике» три разные величины и каждая подписана (§1d.2 №6): верхняя таблица —
СЧЁТЧИКИ КОНТЕЙНЕРА (`st_traffic_live_note`, обнуляются при его перезапуске), средняя —
сумма за всё время, которую считает сам продукт (`st_traffic_total_note`), нижняя — сводка
за сегодня и за месяц из `stats_summary` (`st_traffic_period`). Раньше первые две стояли под
одной подписью, а сводки в SSH не было вовсе, хотя бот её показывает.

`_svc_card <юнит>` — карточка службы вместо сырого `systemctl status`: состояние,
автозапуск, с какого времени, чем кончился прошлый запуск, юнит и команда журнала
(DEFECTS §3.12). `_unit_label <юнит>` переводит имя юнита в человеческое —
единственное место, где это знание живёт.

Про «0 — назад» говорит сам `ga_ask`: подсказка печатается в приглашении везде,
кроме вопросов «номер пункта», где у списка своя строка «0) Назад», а 0 — законный
ответ (DEFECTS §3.10). Признак такого вопроса — не регулярка, а само приглашение:
слово `choose` («Выбор») принадлежит только экранам со списком; по регулярке
`^[0-4]$` подсказка задваивалась со строкой списка (DEFECTS §4.8).

Счётчик «Шаг i из N» один на всё действие: шаги со спиннером считает `ga_step`,
а сделанное без спиннера (`systemctl restart`) отмечает `ga_step_done` — иначе
«Шаг 2 из 2» останавливался на профилях, а следом шли ещё четыре службы.

`_ga_logs_tail` — общая функция экрана и действия `logs-tail`: профили через
`docker logs`, `bot`/`admin`/`nginx`/`system` через `journalctl`, `mcp` — `$AUDIT_LOG`.
Список имён — `GA_LOG_SOURCES`, он же `param_types.component.enum` (сверяет тест).

Отказ и пустой лог — разные ответы (DEFECTS §3.8). Причину называет `ga_err`
кодом и строкой: `logs_no_container` (контейнера нет), `logs_driver_unreadable`
(профили запускаются с `--log-driver none`, как у самого приложения, — `docker
logs` им не отдаёт ничего и не отдаст), `logs_docker_down`, `logs_no_unit`,
`logs_no_journal`, `logs_absent` (журнала действий ещё не создали),
`logs_read_failed`. Печатает всё это `_logs_print` — одна функция на три экрана
(логи, панель, бот), раньше каждый звал `_ga_logs_tail` сам и все трое любую
неудачу называли «лог пуст».

## 4. Протоколы — `submenu_protocols` (заголовок `pr_title`)

Список карточек со значком состояния и портом: `pr_awg2`, `pr_awg3`, `pr_awg1`. У профиля с большим S4 (S4-FIX) в
конце строки — пометка `pr_s4_mark` «⚠ S4 = 142».

### 4.1 Карточка профиля — `_profile_card <profile>` (`pr_card_title`)

Поля: `pr_state`, `pr_port`, `pr_line`, `pr_core_ver`, `pr_peers`; у профиля на `awg0` — строка «Движок»
(`ga_kmod_card_line`, `kmod_card_engine`: «модуль ядра» или «программа amneziawg-go», живым опросом), а если выбран
другой — `kmod_card_pending` «сменится при следующем перезапуске профиля» (этап Б, CONTRACT §4.4a). Подсеть профиля пересекается с другим
(SUBNET-01: приложение Amnezia ставит свои контейнеры AmneziaWG на одну 10.8.1.0/24, подхват берёт их как есть) —
под полями строка-предупреждение `ga_amn_subnet_overlap_text`: с кем и выход (кого перенести и на какую свободную).
Пакет данных не влезает в мобильные сети из-за большого S4 (`amnezia_profile_status` → `packet_risk`, S4-FIX) — строка
`amnezia_s4_risk` + `pr_s4_card_hint` «Исправить — пункт N.» и пункт «Исправить S4» (`pr_s4_fix`, жёлтым). Пункты
починки («Исправить S4», «Переставить на 2.0») идут по порядку сразу за постоянными 1–10 (11, 12), «Удалить» — последним
следующим номером (D-7, 15.10): постоянные номера не сдвигаются, пропусков нет, набранный номер пункта, которого на
карточке нет, — `invalid_choice`. Согласие на необратимое с профилем («Удалить», «Переставить», люди приложения у «Исправить
S4», подсети и порта) — набором порта профиля (`_profile_confirm_phrase`, `pr_confirm_port_ask`, D-5): имени места awg2/awg3
пользователь нигде не видит; мосту — по-прежнему `adopted_confirm=<профиль>`.

Профиль не поставлен — в карточке один пункт «1) Установить» (`pr_install`):
`_profile_install_interactive`: порт (`_ask_port`), затем подсеть (`_ask_install_subnet`, Enter — по
умолчанию, подсказка `pr_subnet_install_note` про туннели роутера к другим серверам) →
`amnezia_profile_install`; после установки — вопрос «выдать ключ на этот профиль всем?»
(`_profile_offer_keys_to_all`: `pr_keys_all_ask` / `pr_keys_all_done` / `pr_keys_all_later`).
Поставлен — пункты ниже; «Удалить» последним и красным (25.09.2026, LC-10).

| № | Ключ | Функция / модуль |
|---|---|---|
| 1 | `pr_set_port` | `_profile_port_interactive` → `pr_port_current` + у кого ключи (`pr_port_holders`: число и имена) + `_ask_port` (предлагает СВОБОДНЫЙ порт, нынешний отвергает своим словом `pr_port_same`, занятость спрашивает у `ga_amn_port_check` — он один знает про порты соседних профилей и не считает занятым свой) → `amnezia_profile_set_port` → что делать дальше (`pr_port_next_steps`) |
| 2 | `pr_take443` | тот же путь с портом 443; занят другим профилем → `pr_443_taken` |
| 3 | `pr_params` | `amnezia_profile_params` |
| 4 | `pr_set_param` | `_profile_set_param_interactive` — см. ниже |
| 5 | `pr_set_mtu` | `_profile_set_mtu_interactive` → `pr_mtu_note` («1280 — стандарт, работает везде. На проводном интернете можно поднять до 1320 — будет на несколько процентов быстрее; если скачивание начнёт зависать, верните 1280.», решение владельца 08.10) → `pr_ask_mtu_cur` (1280–1500, **Enter — вернуть 1280**; ответ — через `ga_capture`, чтобы конец ввода был отменой, а не молчаливым 1280); ниже 1280 — `amnezia_err_mtu_low` «Ниже 1280 туннель не поднимется у клиентов с IPv6 (AllowedIPs ::/0) — оставьте 1280 или больше» (L-02б), выше 1500 — `pr_bad_mtu`, и тот же вопрос снова (три попытки) → `ga_amn_mtu_check` + `ga_amn_mtu_warn_lines`: что такой MTU сломает наверняка — `amnezia_mtu_warn_path` (MTU выше 1280 и пакет MTU + 80 + S4 больше 1440: «Пакет N байт: на части хостеров и сетей скачивание встанет — оставьте 1320 или меньше», L-02а); есть — `confirm` `pr_mtu_keep_ask` «Сохранить MTU N всё равно?»; 1320 при S4 ≤ 40 — без вопроса → `amnezia_profile_set_mtu` → `pr_mtu_set` + `_note_only_new_keys` |
| 6 | `pr_set_subnet` | `_profile_set_subnet_interactive` → `pr_ask_subnet_cur` (`pr_bad_subnet`) → `pr_subnet_warn` + `confirm` (`pr_ask_subnet_confirm`) → у людей приложения без ключа — `pr_subnet_adopted_warn` + набор порта профиля (ACC-2) → `amnezia_profile_set_subnet` под спиннером `pr_subnet_changing`. SUBNET-01: подхваченному старому контейнеру (wg0) подсеть не сменить — отказ сразу (`ga_amn_subnet_legacy_msg`), а при пересечении — выход: «Перенести X на свободную Y?» (`_subnet_move_offer`, `pr_subnet_move_ask`); пересекается подсеть самого профиля — предупреждение, совет перенести соседа (`pr_subnet_move_other_ask`, «нет» — сменить этому), вопрос `pr_ask_subnet_free` с подставленной свободной (Enter — она) |
| 7 | `pr_preset` | `_profile_regen_interactive <profile> ask` — выбор `default` / `quic` / `router` |
| 8 | `pr_regen` | `_profile_regen_interactive <profile> default` → `pr_regen_warn` + `confirm` → `amnezia_profile_regen` |
| 9 | `pr_restart` | `amnezia_profile_restart` |
| 10 | `pr_repair` | `_profile_repair_interactive` → `pr_repair_note` + `confirm` (`pr_repair_ask`) → `amnezia_state_restore` под спиннером `pr_repairing` / `pr_repaired` |
| 11–12 | `pr_s4_fix` | только при `packet_risk` (S4-FIX), номер — следом за 10: `_profile_fix_s4_interactive` → `pr_s4_fix_note` (S4 станет 12–24, при MTU 1280 пакет данных — не больше 1384 байт, с запасом до 1400 (S4-MARGIN, с 1.0.13); профиль перезапустится — клиенты переподключатся сами, обычно за 15–25 секунд; ключи людей прежние; подписки — сами, срок словами окон перевыпуска (`_user_sub_within`: «до 12 ч, у FlClash — до 24 ч»), или по «Обновить», страницы — сразу, ключи `vpn://` и файлы `.conf` — импортировать заново, кому — после) → `confirm` `pr_s4_fix_ask` (цифрами) → у людей приложения без ключа на сервере — `pr_s4_adopted_warn` + набор порта профиля (`_profile_confirm_phrase`, `pr_s4_adopted_ask`, ACC-2) → `ga_step` `pr_s4_fixing` / `pr_s4_fixed` → `amnezia_profile_fix_s4` → итог: `summary` ядра (`amnezia_s4_fixed`) и по строке на шаг (`amnezia_s4_step_*`: перезапуск, подписка, страница, ключ, приложение, никого). Риска уже нет — `pr_s4_nothing` |
| 11–13 | `pr_remove` | последним пунктом: `_profile_remove_interactive` → сколько людей теряют ключ (`pr_remove_holders`) → чьи страницы подключения перестанут работать (в них был только этот профиль; `ga_amn_remove_preview`, `amnezia_remove_pages_one`/`_many`, PROFILE-REMOVE-PAGES) → набор порта профиля (`_profile_confirm_phrase`) + `amnezia_profile_remove` |
| 0 | `back` | |

**Поднять контейнер заново** (`pr_repair`) — ответ на «контейнер пропал»: `amnezia_state_restore`
поднимает его из `state/` на прежнем образе с прежними ключами, архив не нужен и ни один
выданный ключ не перестаёт работать. Функция была с самого начала, кнопки не было нигде, и
сторож с ботом советовали восстановление из бэкапа — самое разрушительное действие продукта
(§1d.2 №10). Действие моста — `profile-repair`.

Версия ядра профиля показана в шапке карточки (`pr_core_ver`), отдельного пункта нет.

**Изменить один параметр** (`_profile_set_param_interactive`, PLAN §10 п.9 — «как в настройках
приложения Amnezia»):

1. список из `amnezia_profile_param_list` — все параметры линейки-основания профиля
   (`ga_profile_line_base` — семейство его фактической линейки, CONTRACT §4.4) с текущим
   значением и стороной: заголовок `pr_param_pick_title`, серверные помечены
   `pr_param_scope_server`, клиентские — `pr_param_scope_client`; пусто → `pr_params_unavailable`;
2. выбор номером (`choose`, `0` = `back`, не число → `invalid_choice`);
3. ввод значения — `pr_ask_param_value` с именем и текущим значением, пусто = убрать параметр;
4. для серверного параметра — предупреждение `pr_param_reissue_note` ДО подтверждения; для клиентского, который
   живёт строкой в `[Interface]` сервера (`Jc`, `Jmin`, `Jmax`, тайминги 3.x — `ga_amn_iface_mask_param`), когда
   значение меняется, контейнер работает и профиль на модуле ядра (`ga_amn_engine` ≠ `program`), — `pr_param_kmod_restart_note`
   «…применится перезапуском профиля — клиенты переподключатся сами, обычно за 15–25 секунд» (KMOD-01);
5. `confirm` (`pr_ask_param_confirm` с именем и значением) → `amnezia_profile_set_param`
   (валидация по правилам линейки — в модуле, причина в stderr; беда в I1–I5 — словами, `amnezia_err_ipacket_*`,
   KMOD-02); перезапуск ради маскировки на модуле модуль сам объявляет строкой `amnezia_kmod_mask_restart`. `S4`
   больше 40 (PARAM-01, с 1.0.12) — отказ словами `amnezia_err_s4_budget` («…пакет данных выйдет 1401 байт — больше
   1400 … Задайте S4 до 40.»); подхваченный с большим S4 — другие параметры меняются, починка — пункт 12 «Исправить S4».
6. С 1.0.13 (живые проверки 08.10). Пределы того, что пишем мы, — отказ словами с допустимым диапазоном
   (CONTRACT §4.2): тайминги 3.1 и `ContentPaddingAddition` вне коридора (`amnezia_err_corridor` + почему,
   `amnezia_corridor_why_*`), связка таймингов (`amnezia_err_timing_order`), `Jc × Jmax` больше 16 КБ
   (`amnezia_err_junk_burst`), `Jc + число I` больше 127 (`amnezia_err_jc_ipackets`), хвосты 3.1 при разных S и широких H
   (`amnezia_err_trailers_loss`). Не применилось — прежнее значение назад, отказ словами (`amnezia_err_param_rolled_back`
   / `amnezia_err_param_not_applied`). Успех: итог модуля (`amnezia_param_set`) и одна фраза, кому нужен новый конфиг
   (`amnezia_param_summary_server` предупреждением / `amnezia_param_summary_client`), затем по людям — шаги модуля
   строками «•» (`steps`: подписка — обновится сама, страница — открыть снова, ключ — выдать заново, ключ из
   приложения), как у «Исправить S4». Свои строки экрана «Параметр … теперь …» и «выдайте ключи заново» сняты:
   они повторяли модуль.

### 4.2 Линии на сервере, «Переставить на 2.0» (DUP-LINE-01, 1.0.13)

Подпись профиля везде — `_pr_name` → `ga_profile_title`: «AmneziaWG 3.1 · порт 6714», подхваченный — «… · из
приложения», свободное место — «AmneziaWG 2.0 — не установлена» (линия, которая встанет, `ga_profile_auto_line`).
`noport` — где порт стоит колонкой: список «Протоколов», шапка главного меню. Список пользователей — колонка «Профили»
линиями («3.1:6714 3.1:46567» при повторе). Строка `_dash_lines_warn` (шапка, «Протоколы»): `amnezia_lines_no20` и
`amnezia_lines_reline` + `dash_reline_hint` или `amnezia_lines_install` (где поставить недостающую); при флаге миграции —
одна-две строки `dash_dupline_short` (в «Протоколах» — `dash_dupline_here`, без «Протоколы →»; D-1), полный текст ядра —
только в сообщении после обновления и до согласия. Карточка профиля: при `lines_check.reline` на этом профиле — строка и
пункт «Переставить на 2.0» (`pr_reline`, номер — следом за 10) → `_profile_reline_interactive`: `pr_reline_note` и у
каждого пользователя его действие после (`ga_amn_reline_preview`) → ключ на новой линии остальным, до согласия
(DUPLINE-KEYS-01): ключи на профиле уже есть — `pr_reline_keys_rest_ask` «Ключа на этом профиле нет у пользователей:
имена. Выдать им ключ на AmneziaWG 2.0 вместе с перестановкой?», ни у кого — `pr_reline_keys_all_ask` (D-4) → набор
порта профиля (`_profile_confirm_phrase`, `pr_reline_ask`) → `ga_step` `pr_relining` / `pr_relined` →
`amnezia_profile_reline <p> <линия> on|off` (ключи остальным выдаёт ядро тем же действием) → `summary` и шаги ядра
(`amnezia_reline_step_*`, в том числе `others` / `others_failed`); отказались от ключей — `pr_keys_all_later`.
Сообщение после обновления — `_dupline_notice_once` после `show_upgrade_changelog`: `dupline_title`, текст ядра,
«1) Исправить» (`dupline_fix_item`), «2) Позже» (`dupline_later`, `dupline_later_note`); исправить отменили или
перестановка сорвалась — `dupline_retry_note` (R-4). «Режим и сайт → Линия на UDP 443» (`_pro_line_pick`): при одной линии и свободном
месте — «1) Поставить AmneziaWG 2.0 — сразу на UDP 443» (`pl_install_item`, `pl_recommend_install`, `pl_install_ask`) или
«2) перенести стоящую» (у профиля приложения с пользователями без ключа — ACC-2, набор порта профиля); советы
`user_pick_profile_advice` и `pl_recommend` — только когда 2.0 на сервере есть, иначе `*_no20`. Мастер первой установки:
галочки по линиям — подхваченный профиль своей подписью, свободное место — линией, которая встанет; строка 2.0
отмечена и замкнута; отказ от подхвата `amnezia-awg2` не с 2.0 — `wz_tk_declined_main_line`. Экран «Ключ»
(`_user_keys_menu`): все ключи из приложения — только «Привязать старый ключ» и «Перевыпустить ключ на профиле»
(`user_keys_app_only_menu`, KEY-APP-02); выбор профиля для выдачи — без профилей с ключом приложения
(`user_pick_app_skipped`); отказ модуля — один раз (`_err_once`, KEY-APP-01).

## 5. Обслуживание — `submenu_maintenance` (заголовок `mt_title`)

| № | Ключ | Функция / модуль |
|---|---|---|
| 1 | `mt_restart_all` | `_restart_everything` → `amnezia_profile_restart` по всем + рестарт юнитов |
| 2 | `mt_backup` | `_backup_screen`: `_backup_last_line` (`backup_last_json` — «когда была последняя копия» и чем кончился последний ночной проход), `_backup_schedule_line` (`backup_schedule_status`), `mt_backup_now` → `create_backup`, `mt_backup_schedule` → `_backup_schedule_interactive` → `set_backup_schedule` (выключение — с отдельным подтверждением `mt_backup_off_ask`) |
| 3 | `mt_restore` | `_restore_interactive` → `mt_restore_warn` → `interactive_restore` (`lib/backup.sh`: `list_backups` печатает список в stderr → номер или путь → `restore_backup` с `confirm` внутри) |
| 4 | `mt_upstream` | `_upstream_screen` → `upstream_check` (`lib/upstream.sh`): релиз клиента `mt_upstream_release`, изменившиеся скрипты, `mt_upstream_unsupported` для новых плейсхолдеров, строка на профиль «текущий → доступный» (`mt_upstream_available` / `mt_upstream_current` / `mt_upstream_rollback_ok`); номер → `mt_upstream_update_warn` → `confirm` → `upstream_update`; `r<номер>` → `mt_upstream_rollback_warn` → `confirm` → `upstream_rollback`; источники недоступны → предупреждение и текущие версии (`ga_up_profiles_current`), меню живёт |
| 5 | `mt_import` | `_import_scan_screen` → `import_scan` (`lib/import.sh`; отказ — например, демон Docker молчит — сказан словами модуля, а не «контейнеров нет»): таблица `_import_rows` (та же, что на первом экране мастера: протокол, порт, работает ли, людей, `import_row_*`, причина `reason_text`); номер → подхват (`mt_import_adopt_note`, `mt_import_adopt_ask` → `confirm` → `import_adopt`, подсказку про ключи людей печатает модуль; затем отметка установки `_ga_mark_installed` — таймеры; подсеть совпала с другим профилем (в таблице заранее — `import_scan_overlap`, SUBNET-01) — модуль говорит, с кем и выход, а экран сразу предлагает его: `_subnet_move_offer`, «нет» — `pr_subnet_move_later`) или для нашего профиля `_import_reconcile_interactive` → `import_reconcile` (список `mt_import_new_peers` / `mt_import_gone_peers`) → `import_reconcile_ask` → `confirm` → `import_reconcile … apply` |
| 6 | `mt_selfupdate` | `_selfupdate_screen`: источник из `.install-source` → ветка `mt_su_main` / `mt_su_dev` (рабочая ветка стенда — третьим пунктом `mt_su_other`; Enter — откуда стоит сервер, `mt_su_here`) → ключ доступа (`GOAMNEZIA_PAT` или ввод без эха) → `_su_head_sha` (API, голый SHA; нет ветки — `mt_su_no_branch`, API молчит — `mt_su_no_sha` и дальше по имени ветки) → `_su_remote_version` (`lib/common.sh` коммита, `mt_su_remote`) → обновление или та же версия: `mt_su_up` / `mt_su_same` → `confirm`; версия ниже: `mt_su_down_warn` → `confirm_phrase` (`mt_su_down_phrase`) → копия `create_backup` (не снялась — `mt_su_down_nobackup`) → загрузчик коммита (первая строка `#!`) → `_selfupdate_exec` с `GOAMNEZIA_BRANCH`, `GOAMNEZIA_REF`, `GOAMNEZIA_EXPECT_VERSION` |
| 7 | `mt_diag` | `_diagnose_screen` → `_ga_quality_json` (та же функция, что у действия `quality`); последней строкой — итог: `diag_summary_ok` / `diag_summary_warn` / `diag_summary_bad` «проблем — N, они отмечены ✗ выше» (✗ системы, ключей, таймеров и причин проверки портов; NC6-07). Служба Docker старая (DK-02) — `docker_diag_stale` с версиями и `docker_diag_stale_since` |
| 8 | `mt_docker_restart` | `_docker_restart_screen` (DK-02): что со службой сейчас (`docker_stale_probe` заново: `mt_docker_stale_now` / `mt_docker_fresh`) → `mt_docker_restart_warn` («VPN пропадёт на 10–30 секунд у всех») → `confirm` (`mt_docker_restart_ask`) → `ga_step` `amnezia_docker_restart manual` → `mt_docker_done` или слова модуля и `mt_docker_manual`; по профилям — `pr_restarted` / причина. Пока служба старая, в шапке главного меню — `dash_docker_stale` (`_dash_docker_stale_warn`) |
| 9 | `mt_kmod` | `ga_kmod_screen` — «Ускорение AmneziaWG (модуль ядра)», этап Б (`lib/kmod.sh`, CONTRACT §4.4a): см. §5.1 ниже. Пока выбран модуль, а профили работают программой (после обновления ядра модуля под новое ядро нет) или последняя установка не удалась, — строка в шапке главного меню (`ga_kmod_dash_warn`: `kmod_dash_fallback` с ядром и подсказкой «Обслуживание → Ускорение AmneziaWG → 3», `kmod_dash_failed`) |
| 0 | `back` | |

### 5.1 Ускорение AmneziaWG (модуль ядра) — `ga_kmod_screen` (заголовок `kmod_title`, модуль `lib/kmod.sh`)

По умолчанию профили работают программой `amneziawg-go` в Docker; модуль ядра — только по выбору человека, и продукт
сам никогда не переключает (решение владельца, docs/KMOD_PLAN.md). Шапка экрана (`_ga_kmod_screen_state`): что сейчас
(`kmod_now_program` / `kmod_now_kernel` с версией / `kmod_now_busy`), ядро (`kmod_kernel_line`), профили с линией и
движком (`kmod_profiles_line`), причина словами при `fallback` / `failed` / `unpatched` (`reason` из
`ga_kmod_status_json`). Модуля нет: не ставился — `kmod_off_line`; стоял и удалён пунктом 4 — `kmod_removed_line`
«Модуль ядра удалён <когда> — профили работают программой amneziawg-go; пункт 1 поставит его снова» (`removed_at`,
KMODB-01, с 1.0.13). Пункты всегда те же (номера не скачут); неприменимый объясняет словами, почему делать нечего.
Строку по каждому профилю и итог «✓ Профили работают: …» печатает сам перевод (`ga_kmod_engine_set`) по ходу дела,
один раз (KMODB-02); экран добавляет только `kmod_rolled_back_all`, если перевод откатился.

| № | Ключ | Функция / модуль |
|---|---|---|
| 1 | `kmod_item_enable` | `_ga_kmod_enable_interactive`: уже на модуле — `kmod_already_on`. Проверка хоста `ga_kmod_check_json plan` — нельзя (контейнер LXC/OpenVZ, Secure Boot, старое ядро, нет заголовков, мало места) — одна фраза с причиной (`kmod_why_*`) и выход, без вопросов и без apt. Что поставится: готовый модуль с годной меткой (`kmod_plan_reuse`) или пакеты и место (`kmod_plan_packages`, `kmod_plan_build`); метапакет заголовков, который тянет новое ядро, — отдельный вопрос с размером, по Enter — «нет» (`kmod_meta_warn`, `kmod_meta_ask`, KMOD-05). Согласие цифрами по рецепту: `kmod_consent_q` «Ускорить AmneziaWG модулем ядра?», `1) kmod_consent_yes` «Да (рекомендуется): … до полутора раз больше скорости, память не растёт; люди переподключатся сами за 15–25 секунд», `2) kmod_consent_no` «Нет, оставить программу amneziawg-go», `kmod_consent_prompt` «Выбор [1]:»; «2» — `kmod_consent_declined`, ничего не поставлено. «1» — `ga_kmod_install off <meta>` (ожидание apt до 30 минут строками хода, сборка, самопроверка `kmod_selftest_title` по строке на линию), затем `kmod_switch_note` и перевод `ga_kmod_engine_set kernel` с итогом по профилю словами (`_ga_kmod_row_text`: откуда → куда, сколько вернулось и за сколько секунд); откат — `kmod_rolled_back_all`. Неудача установки — `kmod_left_program` (профили остались на программе) |
| 2 | `kmod_item_program` | `_ga_kmod_program_interactive`: и так программой — `kmod_already_program`, без перезапусков; иначе `kmod_program_warn` (какие профили, люди переподключатся сами) → `confirm` (`kmod_program_ask`) → `ga_kmod_engine_set program`. Модуль остаётся на диске (вернуться можно пунктом 1 без сборки) |
| 3 | `kmod_item_rebuild` | `_ga_kmod_rebuild_interactive`: модуля нет — `kmod_rebuild_none`, модуль соседа Kaskad — `kmod_rebuild_neighbour`; иначе `kmod_rebuild_warn` с ядром → `confirm` → `ga_kmod_install on off`. С 1.0.13 (KMODB-03) — как обещает предупреждение: модуль в памяти и профили на нём — `kmod_step_swap`, профили по одному на программу (копия state/, проверка людей, как у пункта 2), `modprobe -r`, новый модуль, самопроверка, обратно на модуль; итог — `kmod_rebuilt_swapped` «…пересобран и загружен заново: в памяти <метка> (ядро …), профили снова на модуле»; на модуле никого — выгрузка и загрузка без перезапусков (`kmod_rebuilt_reloaded`); модулем пользуется сосед Kaskad — только на диск, `kmod_rebuilt_neighbour` (новый загрузится при перезагрузке). Выбран модуль, а профили на программе (новое ядро) — вернуть их на модуль тем же переводом. Неудача — где профили по факту (`_ga_kmod_left_words`): на прежнем модуле — `kmod_rebuild_left_kernel`; перевод успел начаться — `kmod_rebuild_left_program` (выбрано у сервера + `kmod_rebuild_again_ssh` «Пункт 1 переведёт их на модуль снова»), а если новый модуль не загрузился или не прошёл самопроверку — `kmod_rebuild_left_bad` («не годится — включать его снова бесполезно»); не записались файлы в /etc после удачного перевода (шаг hooks) — `kmod_rebuild_left_hooks` («загружен… профили переведены на него обратно»); откат не поднял профили — `kmod_rebuild_left_swap`/`kmod_rebuild_left_switch_down` и `kmod_left_down` («не поднялись после отката: … — проверьте их»), без «пользователи переподключились»; перевод не начинался — `kmod_left_program`. Слова «где профили» лежат отдельно от ошибки (`.failed.left`) и показываются (`kmod-status`, шапка SSH), пока движок не переводили и сервер не перезагружали. Удачный перевод на модуль (`kmod-engine kernel`) снимает прошлый провал этого ядра на загрузке, самопроверке и переводе (`load`, `selftest`, `swap`, `switch`); провал сборки (с журналом) и до неё, и `hooks` — остаётся |
| 4 | `kmod_item_remove` (красным) | `_ga_kmod_remove_interactive`: модуля нет — `kmod_remove_none`; иначе `kmod_remove_warn` (профили — на программу, модуль, исходники, хуки — прочь), сосед пользуется — `kmod_remove_neighbour` → `confirm` (`kmod_remove_ask`) → `ga_kmod_remove` (перевод сам говорит «✓ Профили работают: программа amneziawg-go») → `kmod_removed` «Модуль ядра удалён.» / `kmod_removed_kept`; пакеты сборки не удаляются — команду печатает модуль (`kmod_remove_packages`) |
| 0 | `back` | |

Вопросы — с терминала (`_ga_kmod_ask12`: «1», «2»/«0», Enter — умолчание; конец ввода — «нет»). Долгие шаги идут под
общим замком; панель и бот того же экрана нет — им действия моста `kmod-status`, `kmod-check`, `kmod-install`,
`kmod-engine`, `kmod-remove` (CONTRACT §4.7a).

## 6. Настройки — `submenu_settings` (заголовок `se_title`)

| № | Ключ | Функция / модуль |
|---|---|---|
| 1 | `se_mode_site` | `_mode_site_screen` — режим, домен, шаблон, сертификат, проверка, Alt-Svc, панель на домене (см. 6.1) |
| 2 | `se_clients_menu` | `_clients_menu` — что получают клиенты, два пункта: 1 `se_dns` (`_dns_interactive` → `_dns_pick` → `ga_set_client_dns` + `_note_only_new_keys`), 2 `se_endpoint` (`_endpoint_interactive` — экран: «Сейчас в ключах» и откуда, пункты «По умолчанию» (в Pro домен, иначе IP сервера), «IP сервера» (вписывает адрес; нет в Lite без вписанного адреса), «Другой домен или IP» → `se_endpoint_ask` → `_endpoint_check`; выбор того, что уже стоит, ничего не пишет → `ga_set_endpoint_host`) |
| 3 | `se_access_menu` | `_access_menu` — кто управляет сервером, три пункта: 1 `se_panel` (`_panel_screen`, служба `goamnezia-admin`, см. 6.2), 2 `se_admins` (`_bot_admins_screen` — тот же экран, что «Telegram-бот → Администраторы», раздел 7: состояние бота через `_svc_state_line`, админы и владелец, «1) Привязать по сообщению», «2) Назначить владельца»), 3 `se_agents` (`_agents_screen`, доступы агентов и HTTP-транспорт, см. 6.3) |
| 4 | `se_lang` | `_language_interactive` → `pick_language_interactive` (0, Enter и опечатка — «передумал», а не «русский»), `save_language`, `load_language` |
| 5 | `se_about` | `_about_screen` (`about_*`, в том числе `about_mode` — название режима) |
| 6 | `se_mirror` | `_mirror_screen` — зеркало сборок приложений (см. 6.4) |
| 7 | `se_wipe` | `_wipe_everything` — два `confirm` + бэкап в `/root`; возвращает на место чужой конфиг ротации журналов (`logb_remove_logrotate`) до удаления каталога |

Три настройки действуют ТОЛЬКО на ключи, выданные после них: DNS клиентов, MTU профиля и
Endpoint. Все три печатают одну строку `se_only_new_keys` (`_note_only_new_keys`) — раньше её
печатал один экран из трёх, и человек менял адрес серверов имён, а выданные ключи оставались
со старым, без единого слова об этом (§1d.2 №7).

`_endpoint_check` проверяет хост ДО записи и той же функцией, что вся Pro-обвязка
(`ga_site_dns_a`): домен, который никуда не ведёт (`se_endpoint_no_dns`) или ведёт на другую
машину (`se_endpoint_dns_other`), и IPv4, не равный адресу сервера (`se_endpoint_not_server`),
— это ВОПРОС (`se_endpoint_use_anyway`), а не запрет: домен заводят заранее, сервер бывает за
NAT. Запрета нет, молчания тоже: опечатка в домене раньше проходила молча, и каждый ключ,
выданный после неё, не соединялся никогда.
| 0 | `back` | |

Удаление стоит последним и отдельной строкой (PLAN §5.1): `se_wipe_warn1`, `se_wipe_warn2`,
`se_wipe_ask1`, `se_wipe_ask2`, бэкап `se_wipe_backup`, при неудаче бэкапа — отдельный
вопрос `se_wipe_ask_nobackup`.

### 6.1 Режим и сайт — `_mode_site_screen` (заголовок `ms_title`, модуль `lib/website.sh`)

Три режима (PLAN §4.3, замысел уточнён владельцем 10.09.2026): `lite` — только VPN, сайта нет;
`lite_site` — домен, сайт-заглушка и панель на TCP/443 с настоящим сертификатом Let's Encrypt, а
AmneziaWG на своём отдельном порту; `pro` — то же самое, но AmneziaWG стоит на UDP/443 рядом с
сайтом. Два режима с сайтом отличаются только местом AmneziaWG. Самоподписанный сертификат
(`/opt/goamnezia/tls/site.{key,crt}`, CN и SAN = IP сервера, 10 лет, сайт и по 80, и по 443) —
**запасной путь** любого из них: домена ещё нет или A-запись ещё не указывает на сервер. Вид
сертификата берётся из `config.json` (`_site_cert_kind`), а не из режима; «есть домен с доверенным
сертификатом» — `_site_domain_ready`. Название режима — `_mode_name` (`ms_mode_name_*`), текущий
режим — `_mode_current` (пусто и мусор в `config.json` читаются как `lite`).

Шапка начинается с предполётной проверки портов (`lib/preflight.sh`, PLAN §4.3.1): при каждом
показе экрана `preflight_modes` → `preflight_render_modes … brief` — три режима с ✓/✗, причины
(«TCP/443 занят: nginx (PID 812, юнит nginx.service) — похоже, это goTelegram Pro», «закрыт
локальным файрволом»), предупреждение про чужой UDP/443 и подсказка `preflight_hint_free` /
`preflight_hint_closed`; ввод `r` (`preflight_rechecking`) повторяет проверку без кэша. Без
модуля блок не показывается. Дальше `ms_mode` (название и код), `ms_domain`; в режимах с сайтом (`pro`, `lite_site`) —
`ms_site_url` (`https://<домен>/` или `https://<IP>/` на запасном пути), `ms_cert` (на настоящем
сертификате `_site_cert_line` → `website_cert_expiry`: `ms_cert_days` / `ms_cert_none`; на
запасном — `ms_cert_selfsigned_fp` с отпечатком SHA-256 из `website_cert_fingerprint` и строка
`ms_cert_selfsigned_why`), `ms_template` (`_site_template_line` →
`website_current_template`: «Имя (id)» или `ms_template_none`), `ms_alt_svc` (`website_on` /
`website_off`); в режимах с сайтом — `ms_panel`: адрес `https://<домен>/<путь>/` из
`panel.miniapp_path`, если `_site_domain_ready` (домен с доверенным сертификатом), иначе
`ms_panel_off_state`. Ни одна функция модуля не зовётся под спиннером: certbot и клон
шаблона идут до минуты и печатают подсказки.

| № | Ключ | Функция / модуль |
|---|---|---|
| 1 | `ms_item_change_mode` (с названием текущего режима) | `_site_mode_change <pf>` — экран `ms_change_title` / `ms_change_current` с четырьмя режимами в порядке договора 0.6.0 (`_mode_change_item` → 1 `ms_change_to_lazy`, 2 `ms_change_to_pro`, 3 `ms_change_to_lite_site`, 4 `ms_change_to_lite`; нынешний — `ms_change_now`, в Pro — `ms_change_now_pro`, и его выбор ведёт к пункту 9). 1 → `_site_lazy_run`: `ms_lazy_info`, домен `_lazy_ask_domain` (Enter — нынешний), `_lazy_run <ключей> ask` — проверки без вопросов, одно `confirm` `lz_confirm_installed` (итог и что порты профилей с людьми не меняются), `lazy_setup`, сводка `_lazy_summary`. Переход в Pro при двух линиях — сначала `_pro_line_pick` (какая встанет на UDP 443); из Semi-Pro без домена — отказ `ms_pro_needs_domain` («сначала «Домен»», ничего не меняется; LZ-10). Режим с сайтом, недоступный по предполётной проверке, помечен `ms_change_unavailable` и не выбирается: `_mode_available` → `_mode_blocked_explain` (`preflight_mode_blocked` с первой причиной, при `closed_local` — `_wizard_offer_open_ports` → `confirm` `preflight_ask_open` → `preflight_open_local`, иначе `preflight_mode_blocked_hint`); доступный → `_site_transition <из> <в>` (см. таблицу переходов ниже) |
| 2 | `ms_item_domain` | `_site_domain_change`: тот же домен на настоящем сертификате — `ms_domain_same`; в любом режиме С САЙТОМ — `ms_domain_change_warn` + `confirm` `ms_domain_change_ask` → `website_enable_pro <new> "" <текущий режим>` (новый сертификат и vhost, старый сертификат остаётся; с запасного самоподписанного это и есть переезд на домен); в Lite — `ga_config_merge` `domain` → `ms_domain_saved_lite` |
| 3 | `ms_item_template` | `_site_template_screen` (в `pro` и `lite_site`, иначе `ms_needs_site`): `ms_tpl_current`; 1) `ms_tpl_pick` → `website_change_template_interactive` (категория → список по 25 с поиском → превью → `confirm`); 2) `ms_tpl_random` → `confirm` `ms_tpl_random_ask` → `website_deploy_template __random__` |
| 4 | `ms_item_ssl` | `_site_ssl_screen` (нужен сайт, иначе `ms_needs_site`); ветка выбирается по ВИДУ СЕРТИФИКАТА, а не по режиму. На настоящем (нужен домен, иначе `website_err_no_domain`) — `ms_ssl_issue` → `website_issue_cert` (живой сертификат не перевыпускается — лимиты Let's Encrypt), `ms_ssl_renew` → `website_renew_cert`. На запасном — `_site_selfsigned_screen` (`ms_ssl_title_selfsigned`): срок `ms_cert_days`, `ms_fingerprint`, подсказка `ms_ssl_selfsigned_hint`, 1) `ms_ssl_to_le` → `confirm` `ms_ssl_to_le_ask` → `website_enable_pro` (переезд на домен и Let's Encrypt), 2) `ms_ssl_regen` → `ms_ssl_regen_warn` + `confirm` `ms_ssl_regen_ask` → `website_selfsigned_renew` (новый ключ и отпечаток, vhost перечитан) |
| 5 | `ms_item_check` | `_site_check_screen` → `website_check` → поля `ms_check_*` (`ms_ok` / `ms_bad`, `ms_active` / `ms_inactive`, `ms_present` / `ms_absent`; `ms_check_dns` только при домене; `ms_check_http` — код по 80; на запасном пути строка `ms_cert_selfsigned_fp` с отпечатком), вердикт `_site_verdict_text` → `ms_verdict_ok|not_pro|no_domain|dns_mismatch|no_cert|http_error|no_site|cert_expiring|dry_run|unknown`; в dry-run — `ms_check_dry` |
| 6 | `ms_item_alt_svc_on` / `ms_item_alt_svc_off` | `_site_alt_svc_toggle`: включение — `ms_alt_svc_warn` (браузеры попробуют QUIC на UDP/443 и откатятся на TCP) + `confirm` `ms_alt_svc_ask` → `website_set_alt_svc on`; выключение сразу → `website_set_alt_svc off`; применяется в обоих режимах с сайтом (и на домене, и на запасном пути), в Lite модуль сохраняет настройку до включения сайта (`website_saved_applies_later`) |
| 9 | `ms_item_pro_line` (только в Pro; над пунктами — строка `pl_header` с линией на UDP 443) | `_pro_line_screen` → `_pro_line_pick` (`pl_title`, `pl_explain`, `pl_recommend`; у 2.0 — `pl_recommended`, у нынешней — `pl_now_443`; линия одна — без вопроса, `pl_one_line`) → `_site_vpn_port_plan pro <линия>` (`ms_vpn_port_warn`, `pl_holder_moves`; у обоих люди приложения без ключа — `pl_both_adopted` и отказ; у одного — набор порта профиля, ACC-2) → `_site_vpn_port_apply` → `pro_line_set` (`lib/modes.sh`, то же, что мост `pro-line`): выбранная линия на 443, прежняя — на случайный свободный порт (`pl_holder_moved`), сорвалось — прежняя возвращается на 443 |
| 7 | `ms_item_panel` | `_site_panel_screen` (нужен домен с доверенным сертификатом — `_site_domain_ready`, иначе `ms_panel_needs_domain`; это оба режима с сайтом, но не запасной путь): состояние `ms_panel_state_on` / `ms_panel_state_off`, адрес `ms_panel_url`, подсказка `ms_panel_hint_bot`; `ms_panel_enable` / `ms_panel_rotate` (с `ms_panel_rotate_warn` + `confirm` `ms_panel_rotate_ask`) → `_site_panel_apply` → путь `ga_site_panel_path_new` (`app-` + 86 знаков base64url, 512 бит) → `website_panel_path_set` (пишет `panel.miniapp_path` и `site.panel_path`); `ms_panel_disable` → `confirm` `ms_panel_disable_ask` → `website_panel_path_clear` |
| 0 | `back` | |

Переходы `_site_transition` (каждый — объяснение, `confirm` `ms_change_ask`, функция модуля;
профили, ключи и пользователи не меняются, модуль пишет `mode` только при успехе):

| Из → в | Объяснение | Что зовётся | Что меняется |
|---|---|---|---|
| lite → lite_site | `ms_tr_lite_to_site` | домен (`_site_ask_domain`) → `_site_bring_up <режим> <домен>`, затем `confirm` `wz_pick_template_ask` → `website_change_template_interactive` | nginx, сертификат Let's Encrypt, vhost на домене, случайный шаблон; `mode=lite_site`, `site.cert=letsencrypt`. Домена нет или A-запись ещё не разошлась — запасной путь: `website_enable_site_selfsigned`, vhost на IP (80 и 443), `site.cert=selfsigned` |
| lite → pro | `ms_tr_lite_to_pro` | то же `_site_bring_up`, затем `_site_move_vpn_port pro` | то же самое + AmneziaWG переезжает на UDP/443; `mode=pro` |
| lite_site → pro | `ms_tr_site_to_pro` | `website_switch_site_mode pro` → `_site_move_vpn_port pro` | сайт, домен, сертификат, шаблон и панель НЕ трогаются (certbot не зовётся, vhost прежний); меняется только `mode` и порт AmneziaWG |
| pro → lite_site | `ms_tr_pro_to_site` | `website_switch_site_mode lite_site` → `_site_move_vpn_port lite_site` | зеркально: сайт остаётся как есть, AmneziaWG уходит с UDP/443 на свой порт |
| lite_site → lite | `ms_tr_site_to_lite` | `website_disable_site` | vhost и симлинк убраны, 80/443 закрыты; файлы сайта, домен и сертификат остаются; `mode=lite`, `site.cert=""` |
| pro → lite | `ms_tr_site_to_lite` | `website_disable_site` | то же; сертификат, домен и файлы сайта сохраняются; `mode=lite` |

Неудача включения (`wz_pro_failed_hint` / `wz_site_failed_hint`) оставляет прежний режим.
Адрес панели — секрет: он показывается только здесь (человеку с root) и в ответах
`miniapp-on` / `miniapp-rotate`; `status` и `miniapp-status` отдают лишь факт включения.

### 6.2 Панель и мини-апп — `_panel_screen` (заголовок `pn_title`, docs/PANEL.md §4)

Шапка: `pn_state` — состояние `goamnezia-admin.service` (`_svc_state_line` → `running` /
`stopped` / `not_installed`), `pn_port_label` — порт из `config.json .panel.port` (1985).
Установщик — `admin-web/install_admin.sh --auto` из эталонного каталога (`$SCRIPT_DIR`), язык
сообщений передаётся через `GOAMNEZIA_LANG`; существующий токен установщик сохраняет.

| № | Ключ | Функция / что делает |
|---|---|---|
| 1 | `pn_install` | `_panel_install` → `install_admin.sh --auto` (`pn_installing` → `pn_installed` / `pn_install_failed`; нет файла — `pn_installer_missing`) → `_panel_show_access`: `pn_access_title`, `pn_tunnel_hint` с командой `ssh -L <port>:127.0.0.1:<port> root@<server_ip>`, `pn_login_hint`, `pn_token_hint` (где выпустить аварийный ключ — пункт 5) |
| 2 | `pn_status` | `_svc_card goamnezia-admin.service` + `_panel_show_access`; не установлена — `pn_not_installed_hint` |
| 3 | `pn_logs` | `_logs_print admin 60` (`pn_logs_title`; пусто — `st_logs_empty`, отказ — причина словами) |
| 4 | `pn_restart` | `systemctl restart goamnezia-admin` → `pn_restarted` / `pn_restart_failed` |
| 5 | `pn_token` | `_panel_emergency_key` (SEC50-08, решение владельца 28.09: «на домене — только пароль или код из бота; аварийный ключ — только формировать в SSH полной строкой»): новый ключ на каждый вызов, прежний перестаёт действовать; `ADMIN_TOKEN_FILE` (`/opt/goamnezia-admin/token`, одна строка, base64url 43 знака — как `_create_token_file` в server.py) и рядом `token.meta` `{"created":<epoch>,"expires":<epoch+3600>}` — оба 0600, времянкой и `mv` (сначала ключ, потом срок). Печатает полной строкой команду туннеля (`_panel_tunnel_cmd`, с `-p`, если SSH не на 22-м порту) и `http://127.0.0.1:<port>/?token=<ключ>` — **только в терминал** (`echo`, не `log_*`; в аудит — `panel-emergency-key` со сроком, без ключа); `pn_key_done`, `pn_key_rules` (1 час, сгорает после входа, с домена не работает), `pn_key_until`, `pn_key_secret`; панели нет — `pn_key_no_panel`, не записалось — `pn_key_failed` (прежний не тронут). Проверку срока, сгорание и отказ на домене делает служба панели. Без меню — `goamnezia --panel-emergency-key`; в мост (бот, панель, MCP) не выносится |
| 6 | `pn_password` | `_panel_password_reset` → `install_admin.sh --reset-password` нашим языком: `pn_password_warn` + `confirm` `pn_password_ask`; новый пароль установщик печатает ОДИН раз, на сервере остаётся только его отпечаток |
| 7 | `pn_domain` | `_site_panel_screen` — панель на домене (только Pro, см. 6.1 п. 7) |
| 8 | `pn_remove` | `_panel_remove`: `pn_remove_warn` + `confirm` `pn_remove_ask` → `website_panel_path_clear` (если панель была на домене) → `_component_remove goamnezia-admin.service $ADMIN_DIR` (disable --now, файл юнита, daemon-reload, каталог) → `pn_removed` |
| 0 | `back` | |

### 6.3 Доступы агентов (MCP) — `_agents_screen` (заголовок `ag_title`, модуль `lib/agents.sh`, PLAN §6.10)

Шапка (`_agents_print_transport` по `agents_http_status`): `ag_transport` — `ag_transport_on` /
`ag_transport_off`; где живёт адрес — `ag_listen_nginx` (`https://<домен>/` или `https://<IP>/`),
`ag_listen_port` (свой TLS-порт в Lite) или `ag_listen_none`; `ag_fingerprint` — отпечаток SHA-256
самоподписанного сертификата (только `lite_site` / Lite с портом); `ag_unit` — состояние
`goamnezia-mcp-http.service` (`running` / `stopped` / `not_installed` / `ag_unit_dry`); подсказка
`hint` из статуса; `ag_ssh_hint` — SSH-транспорт работает всегда. Ниже — список
(`_agents_print_list`, колонки `ag_list_cols`: № · id · метка · уровень · срок · последний вызов и IP ·
вызовы · состояние `ag_state_active` / `ag_state_disabled` / `ag_state_expired`; пусто —
`ag_list_empty`). Доступ выбирается НОМЕРОМ СТРОКИ — он открывает карточку
(`_agents_card_screen`), и все действия над доступом живут в ней. Раньше это были семь пунктов
главного экрана, и каждый заново спрашивал номер: чтобы посмотреть, продлить и выдать токен
одному доступу, человек называл его три раза подряд.

**Экран списка.** Номер строки открывает карточку доступа; пункты нумеруются сразу за
списком (`ag_pick_legend` называет их на каждом показе).

| № | Ключ | Функция / что делает |
|---|---|---|
| номер строки | — | `_agents_card_screen <id>` — карточка доступа и все действия над ним |
| `N+1` | `ag_item_add` | `_agents_add_interactive`: `ag_ask_label` (`ga_agents_label_valid`, иначе `ag_bad_label`) → `select_option` `ag_ask_scope` (`ag_scope_read` / `ag_scope_write` / `ag_scope_admin`, Enter = read — наименьшие права; `write` выдаёт приватный ключ любого клиента, и в пункте это сказано) → `ag_ask_days` (0 = бессрочно, `ag_bad_days`) → `ag_ask_ips` (Enter = любые, `ga_agents_ips_valid`, `ag_bad_ips`) → только в `lite`: `ag_ask_port` через `_ask_agent_port` (персональный порт, Enter — без порта; негодное значение — `ag_bad_port` и переспрос, после третьей неудачи `ag_port_fallback_none`) → `agents_add` → `_agents_show_card` |
| `N+2` | `ag_item_transport_on` / `ag_item_transport_off` | `_agents_transport_toggle` (пункт выбирается по `configured`, а не по `enabled`: флаг, оставшийся от прежнего режима, должно быть чем снять): выключен → в `lite` подсказка `ag_transport_on_lite_note` и вопрос `ag_ask_public_port` через `_ask_agent_port` (по умолчанию прежний `mcp.public_port` или случайный 1024–9999 из `ga_agents_random_port`; негодное значение — `ag_bad_port` и **переспрос**, три попытки, после третьей вслух `pr_port_fallback`) → `confirm` `ag_transport_on_ask` → `agents_http_on [port]` (в `pro`/`lite_site` порт не спрашивается: location в nginx) → шапка транспорта; включён → `confirm` `ag_transport_off_ask` → `agents_http_off` |
| `N+3` | `ag_item_logs` | `journalctl -u goamnezia-mcp-http.service -n 60` (`ag_logs_title`, нет — `ag_logs_empty`) |
| 0 | `back` | |

**Карточка доступа — `_agents_card_screen`.** Сверху `_agents_show_card` (адрес, токен,
команда подключения, отпечаток, QR), ниже действия. Доступ выбран один раз — номер больше
не спрашивается ни в одном пункте.

| № | Ключ | Функция / что делает |
|---|---|---|
| 1 | `ag_item_rotate` | `ag_rotate_warn` + `confirm` `ag_rotate_ask` → `agents_rotate <id>` → `_agents_show_card` |
| 2 | `ag_item_harden` | `ag_harden_warn` + `confirm` `ag_harden_ask` → `agents_harden <id>` (второй фактор старому доступу) → `_agents_show_card` |
| 3 | `ag_item_pin` | `select_option` `ag_pin_ask` (`ag_pin_item_on` / `ag_pin_item_off`) → `agents_pin <id> on\|off`; иной ответ — `ag_bad_state` |
| 4 | `ag_item_extend` | `_agents_extend_interactive`: `ag_extend_note` → `ag_ask_extend_days` (Enter = `GA_AGENTS_DEFAULT_DAYS`, негодное — `ag_bad_days`) → **переспрос** `confirm` `ag_extend_ask` (id и дни) → `agents_extend <id> <дней>`. Ответ `0` — не «на ноль дней», а снятие срока: `agents_warn_forever` + отдельный `confirm` `ag_forever_warn`, тот же вопрос, что и при выдаче (ослабление). Адрес и токен не меняются — клиента агента перенастраивать не надо; сюда же ведут тревоги бота `agent_expiring` / `agent_expired` |
| 5 | `ag_item_toggle` | по состоянию записи: `confirm` `ag_toggle_off_ask` → `agents_disable`, либо `ag_toggle_on_ask` → `agents_enable` |
| 6 | `ag_item_del` | `confirm` `ag_del_ask` (id и метка) → `agents_del`. После удаления экран сам возвращается в список: карточки больше нет |
| 0 | `back` | назад в список |

`_agents_show_card` (`ag_card_title`): `ag_card_level`, `ag_card_url` — адрес **только `echo` в
терминал** (не через `log_*`, не в аудит — там лишь `id`), `ag_card_cmd` — готовая команда
`claude mcp add --transport http goamnezia <адрес>`, при самоподписанном сертификате —
`ag_fingerprint` и `cert_hint` (доверить отпечаток, `NODE_EXTRA_CA_CERTS`), предупреждение
`agents_warn_url_is_key` («адрес = ключ»), при выключенном транспорте — `agents_hint_transport_off`,
затем `ag_qr_title` и QR адреса (`_qr_utf8` → `qrencode -t UTF8`, без qrencode — `qr_missing`).
Адрес по режиму собирает модуль (`ga_agents_endpoint`): `pro` → `https://<домен>/a-<secret>/mcp`,
`lite_site` → `https://<IP>/a-<secret>/mcp`, Lite с портом → `https://<IP>:<порт>/a-<secret>/mcp`
(**не сделано:** адрес выбирается по названию режима, а не по `ga_site_domain_ready`, поэтому в
`lite_site` с готовым доменом агенту достаётся IP с отпечатком запасного сертификата — см. PRO.md §3.6);
в `lite` без единого порта выдать доступ нельзя — `agents_err_needs_site`.

### 6.4 Копии приложений для клиентов — `_mirror_screen` (заголовок `se_mirror`, модуль `lib/mirror.sh`, docs/CLIENTS.md §8)

Копии официальных сборок на сервере, которые люди скачивают со страницы подключения. Экран
(`mirror_list`): строки `mr_list_row` — приложение, платформа (`_mirror_platform_name` →
`mr_platform_*`), состояние (`_mirror_state_name` → `mr_state_*`; у `downloading` проценты из
`bytes`/`size` — ядро считает байты по `.part` идущей загрузки, у `failed` — фраза ядра `message` и код в скобках), версия, размер; внизу `mr_usage` (занято из
предела); пусто — `mr_list_empty`. Номер строки — удалить копию, пункты за списком
(`mr_pick_legend`).

| № | Ключ | Функция / что делает |
|---|---|---|
| номер строки | — | `confirm` `mr_remove_ask` (копию могли уже раздать со страниц — кнопка у людей пропадёт) → `mirror_remove <app> <platform>` |
| `N+1` | `mr_item_fetch` | `_mirror_fetch_pick`: `clients_list` → только приложения с `mirror.assets` (`mr_pick_app`, неофициальные подписаны `mr_unofficial`; нет таких — `mr_no_apps`) → платформа из ключей `assets` (`mr_pick_platform`) → `mirror_fetch <app> <platform>` → `mr_queued_hint` (загрузка идёт в фоне, состояние — на этом же экране) |
| 0 | `back` | |

Проверка: `tests/test_wizard_pty.py::MirrorScreen` — пункт в «Настройках», «Удалить всё»
осталось последним, экран открывается, называет занятое место и предлагает скачать сборку.

## 7. Telegram-бот — `submenu_bot` (заголовок `bot_title`, docs/BOT.md §4)

Шапка: `bot_state` — состояние `goamnezia-bot.service` (`_svc_state_line`). Установщик —
`goamnezia-bot/install_bot.sh --auto` из эталонного каталога; токен и ID админов уходят ему
через окружение `BOT_TOKEN` / `ALLOWED_IDS` (не через аргументы — argv виден в `/proc`), язык —
`GOAMNEZIA_LANG`. Токен читается `read -rs` (без эха), не логируется и не попадает в аудит.

| № | Ключ | Функция / что делает |
|---|---|---|
| 1 | `bot_install` | `_bot_install_interactive`: есть `/opt/goamnezia-bot/.env` → `bot_env_reuse` и установщик без вопросов; иначе `bot_token_ask` (формат `^[0-9]{6,12}:[A-Za-z0-9_-]{30,50}$`, `bot_token_bad`) и экран «кто администратор» `_bot_admin_pick`: Enter/1 — **привязка сообщением** (`bot_admin_auto`, `bot_admin_pair_note`; docs/BOT.md «Контракт привязки»), 2 — `bot_ids_ask` (`^[0-9]+(,[0-9]+)*$`, `bot_ids_bad`) → `bot_installing` → `install_bot.sh --auto` с `GOAMNEZIA_ADMIN_PAIR=1` (одноразового кода нет: терминал здесь; код — запасной путь только установки без терминала) → `bot_install_failed` или, если админов в `.env` нет (выбрали привязку или прежний `.env` без админов), `bot_installed_pair` и сразу `_bot_pair_by_message` (окно привязки, ниже), иначе `bot_installed`; нет файла — `bot_installer_missing`. У вопроса про токен ТРИ выхода, и все обязательны: `0` — отмена (сказано в самом вопросе), конец ввода (оборванная сессия, `read` отдаёт EOF мгновенно — без разбора цикл крутился бы вхолостую на 100 % CPU) и счётчик из трёх попыток (`bot_token_giveup` называет пункт меню, куда вернуться). Тот же разбор EOF стоит в главном меню у `read -t 30` |
| 2 | `bot_status` | `_svc_card goamnezia-bot.service`; не установлен — `bot_not_installed_hint` |
| 3 | `bot_logs` | `_logs_print bot 60` (`bot_logs_title`) |
| 4 | `bot_restart` | `systemctl restart goamnezia-bot` → `bot_restarted` / `bot_restart_failed` |
| 5 | `bot_env` | `_bot_env_screen`: `.env` без строки `BOT_TOKEN` (`bot_env_title`, `bot_env_token_hidden`); нет файла — `bot_env_missing` |
| 6 | `bot_admins` | `_bot_admins_screen "$(t bot_admins)"` (бот не установлен и `.env` нет — `bot_not_installed_hint`): состояние службы, `se_admins_list` и `se_owner_is` (или `se_owner_missing`), без админов — `se_admins_none`; пункты: 1 `se_admins_pair` — `_bot_pair_by_message`: `install_bot.sh --pair --lang=<язык>` отдельным процессом (окно привязки целиком — docs/BOT.md §4; его Ctrl-C закрывает окно и возвращает в меню), 2 `se_owner_item` — `_bot_owner_set` (только когда админы есть), 0 — назад |
| 7 | `bot_remove` | `_bot_remove`: `bot_remove_warn` + `confirm` `bot_remove_ask` → `_component_remove goamnezia-bot.service $BOT_DIR` → `bot_removed` |
| 0 | `back` | |

Остальных админов добавляют и из самого бота (`/invite`, `.env` `ALLOWED_IDS`); первого — привязкой сообщением отсюда.

---

## 8. Мастер первой установки — `install_wizard` (`wz_title`)

| Шаг | Ключи | Что делает |
|---|---|---|
| 0. Контейнеры Amnezia на сервере (OWN-1) | `import_scanning`, `wz_tk_title`, `wz_tk_explain`, `wz_tk_ask`, `wz_tk_done`, `wz_tk_nothing_to_adopt`, `wz_tk_declined_main` / `_optional` / `_empty`, `wz_tk_docker_down` / `_start_ask` / `_down_go`, строки таблицы `import_row_*`, `import_state_*`, `import_col_people` | ПЕРВЫЙ экран мастера, до выбора режима (`_wz_takeover_gate`): `import_scan` (docker ps -a — и остановленные) → таблица `_import_rows` всех `amnezia-*`, кроме наших (протокол, порт, работает ли, сколько людей; что будет с каждым, причина — если подхватить нельзя). Есть что подхватить — один вопрос `confirm`: «да» → `import_adopt` по каждому (остановленный запускается), «нет» — сказано, что это значит (конец ввода — не «нет», а остановка мастера, `wz_eof_stop`, INST-02): место профиля 2.0 занято контейнером с людьми → мастер останавливается, ничего не изменено; место 3.1/1.x → профиль снят и замкнут на галочках (`wz_tk_note_busy`); контейнер без людей — «поставлю на его место свой». Подхваченные профили на галочках отмечены и замкнуты (`wz_tk_note_adopted`), на экране портов — `wz_port_imported`, мастер их не переустанавливает. Демон Docker молчит — предупреждение и вопрос «запустить и проверить?» |
| 1. Режим | экран `preflight_render_modes` (`preflight_title`, `preflight_mode_*`, `preflight_desc_*`, `preflight_available` / `preflight_unavailable`, `preflight_prompt`); без модуля — `wz_mode_title`, `wz_mode_lazy` / `wz_mode_lazy_desc`, `wz_mode_pro` / `wz_mode_pro_desc`, `wz_mode_lite_site` / `wz_mode_lite_site_desc`, `wz_mode_lite` / `wz_mode_lite_desc` | цикл: `preflight_modes` перед показом (PLAN §4.3.1) → порядок договора 0.6.0 (docs/MODES_047.md): 1 Ленивый Pro (`_lazy_wizard`, ниже), 2 `pro`, 3 `lite_site`, 4 `lite` (Enter), 5 — повторить проверку (`preflight_rechecking`), 0 — отмена. Ленивый Pro доступен там же, где Pro (TCP 80/443). Недоступный режим (TCP/80 или 443 занят чужим / закрыт файрволом) не выбирается: `_mode_blocked_explain` → `preflight_mode_blocked` с причиной, при `closed_local` — `_wizard_offer_open_ports` (`confirm` `preflight_ask_open` → `preflight_open_local` → проверка повторяется), иначе `preflight_mode_blocked_hint`. Свой nginx/контейнер (`ours`) не блокирует |
| 1а. Ленивый Pro | `lz_*`, `modes_*` (`lib/lang/*.d/modes.sh`) | `_lazy_wizard` → `_lazy_ask_domain` (петля с подсказками; чистка ввода и кириллица → punycode — `ga_modes_domain_norm`; 0 — назад к режимам) → `_lazy_run`: `_lazy_dns_wait` (A-запись не сюда — повтор каждые 15 с до 5 минут, `0` — другой домен) → `_ga_modes_plan` + `_lazy_explain` без вопросов (Cloudflare — ключи на IP; UDP 443 занят — Semi-Pro; TCP 80/443 у чужого или у контейнера приложения — стоп с именем держателя) → `lazy_setup` (сертификат, случайный шаблон — 3 попытки, затем встроенная заглушка; 2.0 на UDP 443 и 3.1 на случайном; панель на домене; ключи с именами 🎲 и страницами) → `_lazy_summary` (режим и почему такая линия на 443, сайт, панель и пароль один раз, страницы, QR по номеру, `lz_bot_ask` → `_bot_install_interactive pair`). Отказ от подхвата на экране 0 соблюдается (`GA_MODES_ADOPT=0`); без меню — `install.sh --lazy --domain=<d> [--keys=N]` (`_lazy_headless`, конец ввода — отмена, установленный сервер не трогается) |
| 2. Домен (Pro) | `wz_domain_ask`, `wz_domain_bad`, подсказка DNS `website_dns_*` (набор A-записей против адресов машины) | нижний регистр (server_name, путь сертификата, pattern домена у моста), `validate_domain`, сверка всех A-записей с адресами машины (`website_dns_hint`; предупреждение, не отказ) |
| 2а. Линия на UDP 443 (только Pro, LZ-11) | `pl_title`, `wz_line_explain`, `pr_awg2` + `pl_recommended`, `pr_awg3`, `wz_line_why`, `wz_line_prompt`, `wz_line_lock31` | `_wz_line_pick`: 2.0 первой и рекомендуем, Enter — 2.0, «2» — 3.1, «0» — назад к домену; выбор → `_WZ_LINE443`, 443 в сводке портов отдаётся ей (`_wz_ports_default`), галочка 3.1 при выборе 3.1 замкнута. Линия уже стоит (подхвачена, прошлый заход) или место 3.1 занято людьми приложения — экрана нет, 2.0. Ленивый Pro — без вопроса, 2.0 |
| 3. Профили | `wz_profiles_title`, `wz_profiles_hint`, `wz_awg2_always`, `wz_profiles_done` | галочки: `awg2` всегда, `awg3` и `awg1` по выбору (`_checkbox`) |
| 1–4. Режим → домен → профили → порты | `wz_mode_prompt`, `wz_domain_ask_wizard` / `wz_domain_ask_keep`, `wz_pick_legend`, `wz_cancelled_hint` | шаги идут по кругу (`step` в `install_wizard`): «0» на каждом — шаг назад (домен → режим, галочки → домен или режим, порты → галочки), «0» на первом экране — выход с `wz_cancelled_hint`; вернувшись к домену, Enter оставляет прежний; конец ввода на любом вопросе (режим, домен, линия, галочки, порты, их правка, вопросы «переключиться на Pro/Semi-Pro?») — остановка без установки (`wz_eof_stop`, INST-02): данные ответы — в запись `.wizard` (`_wz_save`), выход без меню; раньше конец ввода значил «дальше с умолчаниями», и мастер без терминала сам ставил профили |
| Повтор после обрыва (INST-01) | `wz_resume_title`, `wz_resume_profiles`, `wz_resume_port`, `wz_resume_installed`, `wz_resume_next_*`, `wz_resume_prompt`, `wz_resume_port_bad`, `wz_busy` | запись `.wizard` есть — экран `_wz_resume_screen` после экрана подхвата: режим, домен, линия, профили с портами и «уже стоит», откуда дальше; Enter — с того же вопроса (`step`), все ответы есть (`install`) — сразу установка: порты из записи сверяются (`_wz_resume_ports`: стоящий профиль — его нынешний порт, разошёлся с записанным — `wz_port_kept`; записанный порт занят или Pro без профиля на UDP 443 — экран портов с прежними, где годятся); 1 — вопросы заново (прежние ответы подсказкой); 0 — в меню, запись остаётся, шапка — `dash_wizard_pending`. Второй мастер, пока жив первый, — `wz_busy` с номером процесса, ничего не тронуто |
| 4. Порты всех выбранных профилей | `wz_ports_title`, `wz_ports_note`, `wz_ports_ask`, `wz_port_*`, `pr_ask_port`, `pr_bad_port`, `pr_port_busy` | один экран-сводка (`_wz_ports_step`): у каждого отмеченного профиля свой порт, все разные — случайные 1024–9999 (`_random_port` с исключением уже выбранных), в Pro `awg2` — 443. Enter — принять, 1 — задать свои по очереди (`_wz_ports_edit`: 0 — шаг назад, повтор порта — отказ с именем профиля), 0 — назад к галочкам (порты сохраняются). 443 держит не больше одного профиля; в Semi-Pro 443 — вопрос «переключиться на Pro?»; Pro без профиля на 443 — вопрос «поставить режим Semi-Pro?» |
| 5. DNS | `wz_dns_title`, `wz_dns_cf`, `wz_dns_google`, `wz_dns_adguard`, `wz_dns_custom` | `_dns_pick` — тот же экран, что в настройках |
| 6. Ответы в конфиг | — | ответы — в запись `.wizard` (`step: install`, режим, домен, линия, профили, порты) ДО установки: сорвавшаяся установка продолжается повторным запуском, опрос заново не начинается. `ga_config_merge` — `version`, `server_ip` (`_server_ip`), `client_dns`; IP нужен `start.sh` контейнера и Endpoint ключей. `mode` и `domain` режима с сайтом НЕ пишутся — их пишет модуль сайта, когда сайт поднят (шаг 11; INST-01: прежде здесь писался желаемый режим, и прерванная установка оставляла «Режим: Pro» без сайта); Lite — сразу `mode: lite`; режим с сайтом от прерванной установки прежней версии при vhost, которого нет, — `lite` |
| 7. Docker | `wz_docker`, `wz_network` | `deps_install_missing --yes qr` (qrencode для QR в конце мастера), затем `docker_ensure_installed`, `docker_ensure_network` под `run_with_spinner` |
| 8. Профили | `wz_install_profile`, `wz_profile_kept`, `wz_profile_kept_down`, `wz_profile_revive_failed`, `wz_profile_imported_down`, `wz_port_kept`, `wz_unfinished_hint` | `_wz_profile_ensure`: не стоит — `amnezia_profile_install <profile> <порт из шага 4> default`; уже стоит (прошлый заход, подхват, вторая установка успела раньше — отказ `profile_exists` тоже сюда) — не переставляется: работает — «уже стоит — оставляю», лежит — `amnezia_profile_repair` из копии состояния (ключи прежние; подхваченный у приложения — только подсказка), порт не двигается и при расхождении с выбранным назван вслух. Не встал `awg2` — `wz_unfinished_hint`, запись остаётся |
| 9. Признак установки | — | `ga_config_merge '{"installed":true}'` — только после удачной установки `awg2` |
| 10. Первый пользователь | `wz_first_user`, `wz_admin_label`, `wz_admin_name_taken` | `users_add admin "Администратор" <профили>` — на все стоящие профили, кроме подхваченного старого 1.x (образ amnezia-wg); имя `admin` уже занято человеком из приложения — `admin-ga`. Заведённый прошлым заходом (`.wizard.admin` или `admin` с ключом на сервере) — остаётся (`wz_admin_kept`), второго не заводим |
| 11. Сайт | `wz_pick_template_ask`, `wz_pro_failed_hint`, `wz_site_failed_hint`, `ms_no_domain_fallback`, `ms_dns_not_ready_fallback`, `ms_issue_later_hint` | оба режима с сайтом поднимает одна ветка `_site_bring_up <режим> <домен>` (не под спиннером: certbot идёт до минуты), и путь выбирается по готовности домена, а не по режиму: A-запись указывает сюда → `website_enable_pro <domain> "" <режим>` (nginx, Let's Encrypt, случайный шаблон); домена нет или DNS ещё не разошёлся → `website_enable_site_selfsigned "" <режим>` (самоподписанный на IP, vhost на 80/443) с честной строкой, чего человек лишается и каким пунктом выпустить настоящий сертификат позже; затем `confirm` → `website_change_template_interactive`. Неудача — `mode` остаётся `lite` (модуль сайта пишет режим только при удаче), домен сохранён, повтор из «Настройки → Режим и сайт → Сменить режим». Сайт, поднятый прошлым заходом, — тот же вызов (ремонт, живой сертификат не перевыпускается) |
| 12. Бот и панель | `wz_bot_ask`, `wz_bot_skipped_hint`, `wz_panel_lite_note`, `wz_panel_failed_hint` | бот — предложение (`confirm` → `_bot_install_interactive`: токен и ID → `install_bot.sh --auto`; бот уже стоит — не предлагается; конец ввода — «нет»), отказ — подсказка, где пункт в меню. После панели запись `.wizard` снимается (`_wz_clear`), замок мастера — тоже (`_wz_unlock`), дальше ключ. Панель без вопроса: на домене с настоящим сертификатом служба ставится вместе с сайтом (`_panel_ensure_service` → `_panel_install`, решение владельца 17.09.2026), иначе — строка, где её включить (панель открывается через SSH-туннель) |
| 13. Ключ | `wz_done`, `key_title_conf`, `wz_link` | QR `.conf` (`export_conf` + `qrencode -t UTF8`) и ссылка `vpn://` (`export_vpn`) |

## 9. Мост `--action=` — `action_dispatch` / `_action_dispatch_locked`

Имя действия проверяется по `lib/actions.json` (`_action_known`), аргументы `--k=v` кладутся
в `ARG_<k>` (`_action_parse_args`, дефис → подчёркивание) и проверяются по `param_types`
(`_action_validate_args`: `required`, `type`, `enum`, `pattern`, `minimum`/`maximum`,
подстановка `default`). Пустой аргумент (`--value=`, `--host=`) — не отсутствующий:
`missing_arg` только когда обязательного нет вовсе, пустому подставляется `default` или он
уходит модулю как есть (`--value=` = убрать параметр, `--host=` = IP сервера).
`readonly: true` — без замка, остальные — `flock -w 30`; таймаут →
`lock_timeout` и выход 75. Отсутствующая функция модуля → `_require_fn` и код
`module_missing`. Функции модулей зовутся через `_action_run <код-по-умолчанию> fn args…`
(`ga_capture`, без подоболочки), поэтому в ответе — код и сообщение самого модуля
(`port_busy`, `user_not_found`, `params_invalid`, `subnet_overlap`, `backup_not_found`…), а
обобщённый `*_failed` — только когда модуль кода не назвал. Полный список меток `case`
совпадает с `lib/actions.json` (сверяется `tests/check_actions.py`); весь сценарий моста
без Docker гоняет `tests/test_actions_dryrun.sh`.

Заглушек `not_implemented` в диспетчере не осталось. Спринт 4 (docs/CONTRACT.md §4.7, §4.9–4.12):
`import-scan` → `import_scan` (полный разбор, `adoptable[]`), `import-adopt --container` →
`import_adopt`, `import-reconcile --profile [--apply=on]` → `import_reconcile`; `upstream-check` →
`upstream_check` (одна строка JSON и при `unreachable` — с текущими версиями профилей),
`upstream-update --profile [--tag]`, `upstream-rollback --profile`, `core-update --profile` →
`upstream_update` / `upstream_rollback` / `upstream_core_update` (ответ модуля в корне);
тики таймеров `quota-tick`, `watch-tick`, `stats-tick`, `upstream-tick`, `cert-tick`,
`backup-tick` (`mcp: null`, их зовут юниты `lib/timers.sh` и `lib/backup.sh`); `traffic` несёт
`today`/`month`/`days` из `stats_summary`, `quality` — `timers[]`.

Доделки §1d.2: `profile-repair --profile` → `amnezia_state_restore` (поднять контейнер из
`state/` без архива и без перевыпуска ключей); `backup-schedule --frequency=off|daily|weekly|monthly`
→ `set_backup_schedule`; `backup-last` (readonly) → `backup_last_json` — «когда была последняя
копия» и чем кончился последний ночной проход; `cert-tick` → `website_cert_tick` (пробует
продлить, не вышло — событие `cert_expiring` за три недели до конца); `backup-tick` →
`backup_tick` (на забитом диске ротация идёт первой, отказ уходит событием `backup_failed`).
`user-key` и `key-bundle` несут поле `disabled`: ключ выключенного выдаётся, но об этом
сказано во всех трёх интерфейсах.

Режимы с сайтом (`lib/website.sh`): `change-mode --mode=lite|lite_site|pro [--domain]` — полный
граф переходов (`_mode_current` → цель) по уточнённому договору «`pro` и `lite_site` — один и тот
же сайт на домене, разница только в месте AmneziaWG»:
из `lite` в `pro|lite_site` → `website_enable_pro <домен> "" <режим>` (домен из аргумента или из
`config.json`; домена нет вовсе — `website_enable_site_selfsigned "" <режим>`, запасной путь, а не
отказ); `pro ↔ lite_site` и повтор того же режима → `website_switch_site_mode` — сайт, домен и
сертификат не трогаются вовсе (certbot не зовётся, vhost прежний); в `lite` из любого режима с
сайтом → `website_disable_site`. Порт профиля мост не двигает — это отдельное действие
`profile-port`. Ответ один на все переходы: `mode`, `previous_mode`, `domain` (сохранённый),
`template`, блок `site` (в `lite` — `null`), `dns_fallback`, `keys_host_before` / `keys_host` (адрес
в новых ключах до и после). Запасной путь при неготовом DNS — только пока сайта на Let's Encrypt нет;
смена домена живого сайта идёт через `website_enable_pro` с откатом (SR-54).
`change-template` → `website_deploy_template` (в `lite` — `needs_site`); `site-check` →
`website_check` (readonly, без замка; в `lite` вердикт `not_pro`; на запасном пути HTTPS по IP с
`--insecure`, `cert=selfsigned`, `cert_fingerprint_sha256`, `http_code`); `miniapp-on` → путь
`ga_site_panel_path_new` + `website_panel_path_set` — требуется не режим, а домен с доверенным сертификатом
(`_action_require_pro`: `site.cert=letsencrypt` и непустой `domain`), то есть панель работает в
ОБОИХ режимах с сайтом; на запасном самоподписанном и в `lite` — `not_pro` с полями `mode`,
`cert`, `domain` (повторный вызов путь не меняет, `changed=false`; ответ с `url` — `secret`);
`miniapp-rotate` → новый путь (у выключенной панели — `panel_disabled`); `miniapp-off` →
`website_panel_path_clear` (любой режим); `miniapp-status` — состояние без пути: `enabled` (панель на домене
работает), `configured` (путь записан в конфиге), `available` + `reason` + `hint` (можно ли в этом
режиме вообще и чего не хватает);
`site-alt-svc --state=on|off` → `website_set_alt_svc` (`applied` — дошло ли до nginx: `true` в
обоих режимах с сайтом).
`status` в режимах с сайтом несёт блок `site` (`domain` и `url` — на домене, на запасном пути
`domain` пуст и `url` по IP; шаблон, `cert` `letsencrypt|selfsigned`, `cert_fingerprint_sha256`
только у самоподписанного, срок, `panel`, `alt_svc`, `listen`, `mcp_location`). Коды модуля
(`cert_failed`, `nginx_conf_invalid`, `unknown_template`, `download_failed`, `no_index`,
`app_shell`, `broken_assets`, `invalid_path`, `selfsigned_failed`, `tls_dir_failed`,
`no_server_ip`…) доходят до ответа через `_action_run`; сценарий гоняют разделы 11 и 11a
`tests/test_actions_dryrun.sh`.

Предполётная проверка портов (`lib/preflight.sh`, PLAN §4.3.1): `preflight` (readonly) →
`preflight_modes` через `_action_run`, ответ — `modes` (JSON модуля как есть: `lite`, `lite_site`,
`pro` с `available` и `reasons[]`, `warnings`, `ports`, `hints`), `lite_site_available`,
`pro_available`, `hint`. `change-mode --mode=pro|lite_site` перед `website_enable_*` зовёт
`_action_require_ports <mode>`: режим недоступен → `ga_emit_json error … code=port_busy` (порт держит
чужой процесс) или `code=closed_local` (закрыт локальным файрволом) с полями `mode`, `port`, `proto`,
`pid`, `process`, `unit`, `container`, `product`, `hint` (команда освобождения или открытия) и
`reasons[]`; переход в `lite` и `pro → lite_site` портов не требуют, свой nginx/контейнер (`ours`)
не блокирует. `status` несёт `lang` (язык ядра) и `ports` — `_ga_profile_ports_json`
(`[{profile, port, proto, listening, ours, container, state}]`, один `ss`/`docker ps` на все профили
через `ga_pf_collect` / `ga_pf_owner`); `quality` — `public_ports` (`tcp80`, `tcp443`, `udp443`),
`modes{lite_site, pro}`, `port_warnings[]`. `lang-set --lang=ru|en` → `save_language` (маркер
`.language` и `config.json lang`) + `load_language`, ответ `lang` с сообщением уже на новом языке
(`se_lang_saved`), аудит `lang-set`; в MCP не публикуется (`mcp: null`).

Без пункта в SSH-меню (только мост): `core-update` (пересоздание на закреплённом образе — из
бота, панели и MCP), тики таймеров, `agent-*`/`mcp-http-*` (в меню — экран «Доступы агентов»),
`miniapp-status` (в меню — экран «Панель на домене» с адресом).
