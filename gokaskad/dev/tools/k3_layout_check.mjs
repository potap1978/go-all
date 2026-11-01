#!/usr/bin/env node
// k3_layout_check.mjs — вёрстка панели в настоящем Chrome (T-333): каждый раздел на мок-данных (?mock=1),
// ширины 390…1440 (виды UX-333 — ещё 360: частая ширина Android), светлая и тёмная, RU и EN; карточка ключа агента — и на
// вкладке Windows (подпись команды длиннее). Падает, если:
//   hscroll   — документ шире окна (горизонтальная прокрутка страницы);
//   overlap   — строки текста двух разных элементов налезают друг на друга (видимые части, с учётом обрезки);
//   clipped   — текст обрезан без многоточия (overflow: hidden, scrollWidth > clientWidth);
//   textcut   — строка текста вылезла из блока и срезана его краем (overflow: hidden у предка, не прокрутка);
//   ellipsis  — многоточие без title (полного текста не узнать);
//   colhead   — заголовок колонки таблицы (role=columnheader) обрезан, даже с title: подпись колонки видна целиком
//               («Замер…» вместо «Замеров» в «Обзор › Проверка сервера», REV-3377-final П-1);
//   controls  — кнопки/ссылки накрывают друг друга;
//   cut       — кнопка или поле обрезаны краем окна или карточки (не в ряду, который прокручивается вбок);
//   column    — текст встал столбиком по 1–3 буквы (колонка уже слова);
//   field     — поле ввода уже 60 px;
//   columns   — колонки шапки таблицы (.gx-tbl) не совпадают с колонками строк (R-333 №2);
//   farstate  — схема в общем виде (масштаб < 62 %): у блока есть метка «не работает» / «отказ → напрямую»,
//               а крупной метки состояния не видно (R-333 №1).
//   ptbl      — компактная карточка «Пробросов» (720–1099): строка шире своей колонки (наезжает на кнопки) или переносится.
//   pooldown  — в пуле схемы строка выхода, который не работает, без своего фона (R-333 повтор №2).
//   js        — исключение на странице или console.error (R-333i Б1: вёрстка цела, а отрисовка падала).
//   hy2udp    — у карточки Hysteria2 справка про UDP и VLESS не ровно один раз внутри «Подробнее» или раскрываемой части
//               свёрнутой карточки (решение владельца 20.10).
//   pcrow     — строки списка «Протоколов» (Б r3) разной высоты (> 2 px) или колонка строки не под колонкой шапки (> 2 px)
//               (строка с пометкой «ждут новых настроек» — выше ровно на эту строку, №13 r2)
//               (UI-3 ч.2: тогда — карточки одного ряда; Т3 — список строк).
//   pcfirst   — сцена node3373 на 1440×900: не все установленные протоколы в первом экране (UI-3 ч.2; Т3 — строки под схемой).
//   pcopen    — там же: раскрытие VLESS сдвинуло строки выше неё, под курсором не VLESS, тело не сразу под её строкой или
//               следующая строка наехала на тело (UI-3 круг 2; Т3 — строка раскрывается на месте).
//   egfit     — «Выходы › Группы» на мок-данных при 1440: часть выше 780 px (HANDOFF r3, UI-A2); у вида групп — ещё
//               вариант [open]: группы, «Тест скорости», «Поведение и проверки» и полный текст про адреса сайтов раскрыты.
//   «Протоколы» с моками uxfail и node3373 — ещё вариант [open]: все карточки раскрыты (пустоты, обрезка, наезды); без мока —
//   [del] (у строки Hysteria2 открыт вопрос «Удалить протокол?») и [inst] (окно «Установить …?» с выбором порта, Т3 №14).
//   phantom   — пустой узел (0 px) в сетке или ряду с gap: лишний зазор (REV-ui1 З-2, F14).
//   adrgrid   — «Настройки › Адреса сервера» (3.3.7.15, HANDOFF-addresses «Ширина → сетка»): на части ≥ 1360 «Выдача»
//               и «Выход» справа от «Адресов» (7 : 5, верх по одной линии), уже — под ними; строка адреса на части < 640 —
//               в две линии (адрес · приём, ниже метки · для выхода), шире — в одну; раскрытая строка [open] на части
//               ≥ 760 — «Приём / Для выхода» слева, цифры справа, уже — цифры под выбором.
//   adrfit    — «Настройки › Адреса сервера» (3.3.7.15 Д2, D-3315-view-adr): подпись колонки полосы — полный адрес обрезан,
//               а короткий есть, или короткий, хотя полный входит; без IPv6 — дорожки «Выдача IPv6» / «Срок IPv6»; ?adr=c3 —
//               не «IPv6 выключен в ядре»; «Выход» — середина схемы уже 56 px, узел слева без линии (?ex6=1 — выход по
//               IPv6 за «ещё N»), выбор в строке с подписью обрезан; [open] — значение цифр раскрытой строки обрезано,
//               дорожки не 2 × 2 или «Приём» не под ними, на части ≥ 760 — цифры не на линии дорожек, пустота под «Для
//               выхода» > 56 px (Д2b, D-3315-view-adr-r2 «Д1, пустота»).
//   rggrid    — «Выходы › Группы» [rg] (3.3.7.15 F14, HANDOFF-regroup «Ширина → сетка»): подтверждение «Разложить
//               заново» на части ≥ 880 — полоски слева, итог справа, кнопки у правого края; уже — итог под полосками.
//   exend     — «Выходы › Все выходы», компьютер: переключатель вида и «Добавить ключ» не у правого края строки, перенесённый
//               «+» — не под переключателем справа (3.3.7.14, Claude Design r1 «вправо», Е2/Е3; на 1000 «Крупнее»).
//   headaxis  — шапка компьютера не в одну линию: центры логотипа, заголовка и кнопок расходятся > 2 px (UI-3).
//   subnav    — раздел с подменю: второй уровень шире 0,8 первого (и шире 200 — эталон r4 на ≥ 2000) или отстоит от бокового меню > 24 px (владелец 20.10, UI-5).
//   sidenav   — строка бокового меню с подписями (или метка «этап N») шире колонки меню, меню прокручивается вбок (3.3.7.10).
//   headtitle — телефон: заголовок раздела в шапке (.kk-mhead__t) обрезан — многоточие тоже не норма: рядом «Aa», «+» и
//               поиск, заголовок должен помещаться целиком на всех трёх «A A A» (стенд T-it1b Н-3, «Пол…»).
//   headprio  — компьютер: заголовок раздела в шапке обрезан (многоточие), а поле поиска ещё не сжалось до значка —
//               заголовок уступает место последним (З-1 REV-3313-final: на 1100 «Вид» уходил за край, а простое сжатие
//               заголовка обрезало и короткие «Сводка», «Вид»).
//   headbox   — компьютер: часть кнопки или поля шапки вылезла за свою кнопку по горизонтали или легла на соседнюю
//               (Н-5 стенда 3.3.7.13: «⌘K» из сжатого поля поиска на «Онлайн» у «Пользователей» на 1100–1199).
//   cardedge  — «Обзор › Проверка сервера › Сейчас»: содержимое карточки (.gx-now .gx-card) вылезло за её край
//               (стенд T-it1b Н-5: правая колонка балла с «Почему итог не выше» и «Новая база…» — на 8–85 px за рамкой).
//   grfit     — «График» активности (окно и лист): график шире себя, его окно прокручивается вбок или подписи оси налезают (А5).
//   psgrid    — окна «Настройки протокола» и «Сеть шлюза» (3.3.7.15 V1): раскладка не по таблице «Ширина → сетка» HANDOFF-n13 r2,
//               на телефоне подвал не столбиком, акцентных кнопок в подвале не одна (виды pset:*, K3_PAGES=pset — только они).
//   pend      — полоса ожидания отката (?ps=pend) не по краям содержимого раздела (кадр Н8); в подменю «Пользователей» нет
//               пункта «Ждут настроек», хотя пометки пришли (П1).
//   (3.3.7.15 Д1, сверка Claude Design №13 r2) psgrid — ещё: отметка полосы сети шлюза ближе 6 px к краю сегмента, подпись
//               сегмента срезана краем (Ш3, Ш4); pcrow — строка пометки «… желательно выдать заново» срезана (Н8);
//   psneed    — подтверждение с ?ps=hit: «Сначала скачайте свои настройки…» не над кнопками подвала (Н10);
//   pswait    — «Пользователи» с пометками: слова «ждёт настроек» срезаны, а не значок; имя срезано рядом с несжатой меткой (П1);
//   subfit    — «Настройки» на 1440×900: подменю ниже области раздела (вопрос V2: «этап 3» в строку, зазор групп 8).
//   behave:*  — поведение окон на моке (REV-3315-view С-1, З-1…З-4; сверка r2 Е11, Ш5): netundo, nettoast, replan, e11,
//               pendkeep, sugbase, sugnet, sugdlg, pendbusy, adrdrop, adrempty, adrget, adrafter, adrdown — 1440, светлая.
// Остальной холст схемы (.kg-stage) не проверяется: его двигают и масштабируют (подписи схемы — test_333p_layout.SchemeTest).
//   tour*     — знакомство с панелью (?intro=1, 390/820/1000/1024/1440 из набора ширин, светлая; при 390 — ещё 390×664, 375×548,
//               в full и K3_PAGES=tour — 360×640 и 375×667; с K3_PAGES=tour — и тёмная из набора тем): на телефоне карточка
//               плотная ниже 680 px по высоте и обычная от 680 (tourdense, 3.3.7.14); на компьютере карточка тура не закрывает шапку (tourhead), подсвеченный блок
//               виден и не весь под карточкой (tourring), карточка раздела с тем же заголовком, что у шага («Выходы»), или помеченная [data-tour] («Домен и сайт»), —
//               обведена она, а не соседняя (tourcard), её заголовок не под карточкой тура (tourtitle); «Добавить
//               человека» на экране (touradd) (R-333 №3, №4; повтор №3–№5; круг 3 №1).
// K3_PAGES=settings,forwards — только эти разделы; K3_PAGES=tour — только знакомство с панелью (и 360×640, 375×667). K3_FONT=s|l — шаг «Размер текста» (Т3 №17, ключ kaskad-font в браузере).
//   Группы видов (R-speed-2) — имена для K3_PAGES: раздел (overview, settings?tab=addresses …) — его виды и виды телефона
//   m:* этого раздела (people — ещё graph=*, settings — ещё subfit); pset — окна «Настроек протокола» и полоса ожидания
//   ?ps=pend; behave — поведение окон; find — поиск; tour — знакомство. Через запятую — любые вместе (K3_PAGES=pset,find);
//   pset, behave, tour по одной — как раньше (с добавками: pset — и behave, tour — тёмная и 360×640, 375×667).
// K3_LAYOUT_JOBS=N (R-speed-2; tools/k3_test.sh — 6) — N процессов, у каждого свой Chrome, свой профиль и свой порт
//   (оба выбирает система): K3_SHARD=i/N у части i — каждая N-я «загрузка» (раздел с его ширинами и вариантами), от i.
//   Итог — одна строка «LAYOUT OK: N видов» на всех; поломка или обрыв любой части — FAIL; часть без Chrome (и после
//   повтора) — тоже FAIL, если у других частей итог был; SKIP — только когда Chrome не поднялся ни у одной (REV-speed-2b С2).
// K3_LAYOUT_MAP_OUT=<файл> — какие .css и .js панели нужны каждой группе видов (JSON): .js — загружен страницей, .css —
//   хоть одно его правило подходит к элементу вида (состояния :hover…, ::before… сняты, @media не смотрится — с запасом).
//   tools/k3_test.sh копит это в ~/.cache/kaskad-tests/layout-map.json, tools/test_select.py берёт вёрстку только групп правки.
// Окно выдачи ключа с QR (панель и шаг 6 мастера, сценарии R-333-qr) — отдельный tools/k3_give_layout.mjs: его гоняет
// tests/test_333q_layout.py в шаге unittest tools/k3_test.sh.
// Поиск 3.3.6: виды find, find=па, find=zzqqxx — окно поиска на 390 и 1440 (K3_PAGES=find — только они).
// «График» активности (А5): виды graph=1d|7d|30d — окно человека на ширинах от 700: таблица от 1100, список планшета на 768–1024 (на телефоне — m:people-graph*).
// Телефон 3.3.6: виды m:<имя> — «Ещё», детали, листы снизу, форма, части «Настроек», вход (MVIEWS ниже); планшет 768 — в наборе ширин.
// Наборы: node tools/k3_layout_check.mjs quick — 390, 768, 820, 1024, 1100, 1440 × светлая × RU и EN (~5,5 мин; по
// умолчанию в tools/k3_test.sh); без аргументов или full — 390…1440 × светлая и тёмная × RU и EN (~13 мин).
// Нужны Node ≥ 22 (WebSocket) и Google Chrome / Chromium. Запуск: node tools/k3_layout_check.mjs [ширины] [темы] [языки]
//   CHROME=/путь/к/chrome — свой браузер.
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { readFile, mkdtemp, writeFile, unlink } from "node:fs/promises";
import { existsSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, extname, dirname, normalize } from "node:path";
import { fileURLToPath } from "node:url";

// K3_LAYOUT_STATIC=<папка> — статика панели оттуда (копия с внесённой поломкой — tests/test_layout_par.py)
const ROOT = normalize(process.env.K3_LAYOUT_STATIC || join(dirname(fileURLToPath(import.meta.url)), "..", "lib", "kaskad", "panel", "static"));
const PRESET = { quick: ["390,768,820,1024,1100,1440", "light", "ru,en"], full: ["390,600,720,768,820,900,1000,1100,1200,1366,1440", "light,dark", "ru,en"] };
const ARGS = PRESET[process.argv[2]] ? [...PRESET[process.argv[2]], ...process.argv.slice(3)] : process.argv.slice(2);
const WIDTHS = (ARGS[0] || PRESET.full[0]).split(",").map(Number);
const THEMES = (ARGS[1] || PRESET.full[1]).split(",");
const LANGS = (ARGS[2] || PRESET.full[2]).split(",");
// этап 1 (T8): вкладка «Выходы → Группы» и деталь выхода (боковая панель) — свои виды, адрес раздела с параметрами
// nav2 r1 (UI-A1): части разделов — свои виды: «Обзор» (Сводка, Здоровье, События и «Проверка сервера» — бывшая
// «Диагностика», её адреса #diag… ведут сюда), «Выходы» (Ключи и подписки, Маршрут по умолчанию, Все выходы, Группы),
// «Правила» (Профиль, Блокировка, Списки, Куда пойдёт трафик), фильтр «Людей» в адресе
const FONT = ["s", "l"].includes(process.env.K3_FONT) ? process.env.K3_FONT : "";
const FONT_LS = FONT ? `localStorage.setItem("kaskad-font", "${FONT}"); ` : `localStorage.removeItem("kaskad-font"); `;
const PAGES = (process.env.K3_PAGES || "overview,overview?tab=health,overview?tab=events,overview?tab=now,overview?tab=history,overview?tab=schedule,forwards,inbounds,people,people?show=on,people?focus=d-3c3c0003&open=settings,exits,exits?tab=keys,exits?tab=default,exits?tab=groups,exits?tab=groups&open=rule,exits?tab=ru,exits?focus=e-41a19b9cbe&open=exit,rules,rules?tab=block,rules?tab=lists,rules?tab=trace,scheme,settings,settings?tab=health,settings?tab=gateway,settings?tab=addresses,settings?tab=bot,settings?tab=site,settings?tab=notify").split(",");   // K3_PAGES — только эти разделы
// режимы мока: длинный адрес узла — шапка (T-333 №3); длинные названия пробросов (R-333 повтор №1); UX-333 (3.3.3): протоколы
// «не работает» трёх видов, бот без выхода, ключ агента ждёт подключения (dev-mock-ux.js) — «Протоколы», «Люди», «Выходы»,
// «Настройки» с раскрытыми карточками бота и агента
const UX = "uxfail=awg3:other,wg:busy,l2tp:closed&bot=exit&mcp=key";
// ⚠РФ (R4): по умолчанию мок — режим legacy (плашка «работает как раньше» на «Обзоре» и в «Правилах»); need — выбрана, не
// действует; uncovered — новая организация после подтверждения; full — подтверждено: обычные «Обзор» и «Правила» без плашки
// лёгкое здоровье узла в плитке «Сервер» (SPEC-node-score A7): три находки с советами и строки ниже, находка «по последней
// проверке», снимок панели без шлюза («без истории» и подсказка про бота) — «Обзор»
const HEALTH = ["health=crit", "health=stale", "health=instant"];
// «Диагностика» (B15): пустое расписание с предложением двух записей и плашкой «начнёт работать после первой проверки»
const DIAG_EMPTY = "sched=empty&armed=no_manual";
// UI-2: сцена живого узла 3.3.7.3 (dev-mock-scene-node3373.js — записанные ответы узла, адреса и ключи вычищены): реальные
// тексты описаний и плашек «Протоколов», события и адреса «Обзора»; ширины — ещё 2000 (там пустоты и были)
const SCENE = "scene=node3373", SCENE_W = 2000;
// T15 Q6: российские группы (dev-mock ?rug=): «Дом» на последнем шаге — часть, «Все выходы» со строкой-ссылкой и
// «Правила» со строкой «Российские сайты»; «Дом» без ключей. Без rug — узел без российских групп (пустое состояние)
// РФ-группы Ш1: rug=tier — два ключа в одном уровне «по отклику» (столбец «Устройств», «сейчас: Уровень 1 · 2 ключа»)
const RUG = "rug=last", RUG_E = "rug=empty", RUG_T = "rug=tier";
// стенд it1 Н-1/Н-2 (dev-mock ?nd=up): «Отдача» без данных во всех проверках — радар с отметкой «нет данных»; у первого
// выхода задержка через минуту — линия «Выходы · час» без обрывков
const ND = "nd=up";
// владелец 28.10, решения 4 и 5 (dev-mock ?young=1): первые часы нового узла — итог «предварительно», «Сеть» и «Потери»
// ещё измеряются; звенья без эталонов — «≈» и подпись под радаром
const YOUNG = "young=1";
// стенд it1 Н-4: «Сейчас» без числа «от обычного» — длинный заголовок «Сравнивать пока не с чем: история 1 из 3 дней»
// (dev-mock-diag ?score=few — история 2 из 3 дней, links — мало звеньев со своей историей); без этих видов срез на 390 не виден
// Т-3312 «Мелочь 2»: score=young — первые часы нового узла, у «Дрожания» причина «ещё измеряется — 5 ч из 12» длиннее имени
const REL = ["score=few", "score=links", "score=young"];
// Л3: пятый выход с длинным именем без своего («AmneziaWG 3.1 · 198.51.100.23», dev-mock ?exits=5) — имя в «Выходы · час»
// Сводки целиком, без многоточия (стенд it1, кадр n2b-xh-1440)
const X5 = "exits=5";
// стенд T-it1b Н-5: правая колонка балла на «Сейчас» с длинными строками — две «Новая база с …» и «Расписание выключено —
// Настройки › Проверка сервера» (dev-mock-diag ?now=long); вместе с cardedge
const NOWL = "now=long";
const QS = ["", "site=long", "fw=long", UX, "rf=need", "rf=uncovered", "rf=full", ...HEALTH, DIAG_EMPTY, SCENE, RUG, RUG_E, ND, YOUNG, ...REL, X5, NOWL];
const QS_PAGES = { "site=long": ["overview", "settings"], "fw=long": ["forwards"], [UX]: ["inbounds", "people", "exits", "exits?tab=keys", "settings?tab=bot"],
  "rf=need": ["overview", "rules", "rules?tab=lists", "people"], "rf=uncovered": ["rules"], "rf=full": ["overview", "rules"],
  ...Object.fromEntries(HEALTH.map((q) => [q, ["overview", "overview?tab=health"]])), [DIAG_EMPTY]: ["overview?tab=schedule"], [SCENE]: ["overview", "inbounds"],
  [RUG]: ["exits?tab=ru", "exits", "rules"], [RUG_E]: ["exits?tab=ru"],
  [ND]: ["overview", "overview?tab=sum", "overview?tab=now"], [YOUNG]: ["overview", "overview?tab=now"], ...Object.fromEntries(REL.map((q) => [q, ["overview?tab=now"]])),
  [X5]: ["overview?tab=sum", "exits"], [NOWL]: ["overview?tab=now"] };
const NARROW = 360;                                       // UX-333 (повтор З2′): шапка блока команды на 360 обрезала подпись
const sleep = (ms) => new Promise((ok) => setTimeout(ok, ms));
const CHROMES = [process.env.CHROME, "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/usr/bin/google-chrome",
  "/usr/bin/chromium", "/usr/bin/chromium-browser"].filter(Boolean);
const CHROME = CHROMES.find((p) => existsSync(p));
// РФ-группы Ш1: вид rug=tier — сразу за rug=empty (отдельной строкой: списки выше правит и пачка Б); на «Сводке» —
// российский выход в «Выходы · час» со значком внутренней группы (решение 6; до 3.3.7.13 — метка РФ)
QS.splice(QS.indexOf(RUG_E) + 1, 0, RUG_T); QS_PAGES[RUG_T] = ["exits?tab=ru", "overview"];   // и «Выходы · час» со значком
// PLAN-order Ш5: «Основная» с рядами силы (ord=1: метки Р1/Р2, строка под шапкой уровня) и с ручным порядком
// (ord=manual: предупреждение «Автовыбор выключен» с кнопкой) — карточки «Групп» раскрыты
const ORD = "ord=1", ORD_M = "ord=manual";
QS.push(ORD, ORD_M); QS_PAGES[ORD] = ["exits?tab=groups"]; QS_PAGES[ORD_M] = ["exits?tab=groups"];
// 3.3.7.15 «Адреса сервера»: смена префикса IPv6 (dev-mock-adr ?adr=c6) — полоса с кнопкой «Перенести роль на …» над
// блоком и длинная метка «устаревает: хостер сменил префикс, исчезнет через 3 ч»
const ADR6 = "adr=c6";
QS.push(ADR6); QS_PAGES[ADR6] = ["settings?tab=addresses"];
// 3.3.7.15 Д2: IPv6 выключен в ядре (adr=c3: полоса без дорожек IPv6, строка «выключен в ядре») и выход по IPv6 за «ещё N»
// в схеме «Выхода» (ex6=1: шестой выход — «Стокгольм» по IPv6)
const ADR3 = "adr=c3", ADRX6 = "ex6=1";
QS.push(ADR3, ADRX6); QS_PAGES[ADR3] = ["settings?tab=addresses"]; QS_PAGES[ADRX6] = ["settings?tab=addresses"];
if (!CHROME || typeof WebSocket === "undefined") { console.log("SKIP: нет Chrome или Node без WebSocket — вёрстка панели не проверена"); process.exit(77); }

// R-speed-2: части рядом. K3_SHARD=i/N — эта часть берёт каждую N-ю «загрузку» (единица — раздел или окно с его
// ширинами и вариантами: внутри неё виды идут друг за другом на одной странице), начиная с i; N частей вместе — те же
// виды, что один процесс. Неверное значение — ошибка, а не молча весь набор (части посчитали бы виды дважды).
const SHARD = (() => {
  const s = process.env.K3_SHARD || ""; if (!s) return null;
  const m = /^(\d+)\/(\d+)$/.exec(s);
  if (!m || +m[2] < 1 || +m[1] >= +m[2]) { console.log(`FAIL K3_SHARD=${s}: ждёт i/N, 0 ≤ i < N`); process.exit(2); }
  return { i: +m[1], n: +m[2] };
})();
const JOBS = /^[1-9][0-9]*$/.test(process.env.K3_LAYOUT_JOBS || "1") ? +(process.env.K3_LAYOUT_JOBS || 1) : 0;
if (!JOBS) { console.log(`FAIL K3_LAYOUT_JOBS=${process.env.K3_LAYOUT_JOBS}: ждёт число ≥ 1`); process.exit(2); }
let unitNo = 0;
const mine = () => { const k = unitNo++; return !SHARD || k % SHARD.n === SHARD.i; };
const MAP_OUT = process.env.K3_LAYOUT_MAP_OUT || "";
// карта групп: части пишут <файл>.<i>, общий итог — объединение; full — набор без K3_PAGES и все части дошли до итога
const mapMerge = (docs, full) => {
  const groups = {}, rules = {};
  const join = (a, b) => [...new Set([...(a || []), ...b])].sort();
  for (const d of docs) {
    for (const [g, fs] of Object.entries((d && d.groups) || {})) groups[g] = join(groups[g], fs);
    for (const [f, sels] of Object.entries((d && d.rules) || {})) { const t = rules[f] || (rules[f] = {}); for (const [x, gs] of Object.entries(sels)) t[x] = join(t[x], gs); }
  }
  return { v: 2, full: !!full && docs.every((d) => d && d.full), groups, rules };
};
if (!SHARD && JOBS > 1) {
  const self = fileURLToPath(import.meta.url), kids = new Set();
  const part = (i) => new Promise((done) => {
    const env = { ...process.env, K3_SHARD: `${i}/${JOBS}` }; delete env.K3_LAYOUT_JOBS;
    if (MAP_OUT) env.K3_LAYOUT_MAP_OUT = `${MAP_OUT}.${i}`;
    const c = spawn(process.execPath, [self, ...process.argv.slice(2)], { env, stdio: ["ignore", "pipe", "pipe"] });
    kids.add(c);
    let buf = "", err = "", last = "";
    const line = (l) => { if (/^(LAYOUT|SKIP)/.test(l)) last = l; else if (l) console.log(l); };   // FAIL — сразу, итог части — себе
    c.stdout.setEncoding("utf8"); c.stderr.setEncoding("utf8");
    c.stdout.on("data", (b) => { buf += b; let k; while ((k = buf.indexOf("\n")) >= 0) { line(buf.slice(0, k)); buf = buf.slice(k + 1); } });
    c.stderr.on("data", (b) => { err = (err + b).slice(-1500); });
    c.on("error", (e) => { err += e.message; });
    c.on("close", (code, sig) => { kids.delete(c); line(buf); done({ i, code, sig, last, err }); });
  });
  // Ctrl-C, TERM: части — тоже (у каждой свой Chrome, его гасит сама часть при выходе)
  for (const [s, n] of [["SIGINT", 2], ["SIGTERM", 15]]) process.on(s, () => { for (const c of kids) { try { c.kill("SIGTERM"); } catch (e) { /* уже нет */ } } process.exit(128 + n); });
  const res = await Promise.all([...Array(JOBS).keys()].map(part));
  // Chrome части не поднялся (занятая машина) — эта часть ещё раз, одна (и когда не поднялся ни у одной: Chrome здесь есть)
  for (const r of res) if (r.code === 77) Object.assign(r, await part(r.i));
  let bad = 0, runs = 0; const skip = [];
  for (const r of res) {
    const ok = /^LAYOUT OK: (\d+) видов$/.exec(r.last), b = /^LAYOUT: (\d+) поломок на (\d+) видах$/.exec(r.last);
    if (r.code === 0 && ok) runs += +ok[1];
    else if (r.code === 1 && b) { bad += +b[1]; runs += +b[2]; }
    else if (r.code === 77) skip.push(`часть ${r.i}/${JOBS}: ${r.last.replace(/^SKIP: */, "") || "SKIP"}`);
    else { bad++; console.log(`FAIL часть ${r.i}/${JOBS}: оборвалась без итога (${r.sig || "код " + r.code})${r.err.trim() ? ": " + r.err.trim().split("\n").slice(-3).join(" | ").slice(0, 400) : ""}`); }
  }
  if (MAP_OUT) {
    const docs = [];
    for (const r of res) { try { docs.push(JSON.parse(await readFile(`${MAP_OUT}.${r.i}`, "utf8"))); } catch (e) { docs.push(null); } await unlink(`${MAP_OUT}.${r.i}`).catch(() => {}); }
    const done = res.every((r) => (r.code === 0 || r.code === 1) && /^LAYOUT/.test(r.last));
    await writeFile(MAP_OUT, JSON.stringify(mapMerge(docs.filter(Boolean), done && docs.every(Boolean))));
  }
  if (skip.length === res.length) { console.log("SKIP: " + skip[0].replace(/^часть \S+: /, "")); process.exit(77); }
  // REV-speed-2b С2: часть без Chrome рядом с частями, где он был, — её виды не проверены: красное, а не SKIP с зелёным
  // итогом (раньше 4 вида из 12 молча уходили в «ALL GREEN»)
  for (const s of skip) { bad++; console.log(`FAIL ${s.replace(/ — вёрстка панели не проверена$/, "")} — её виды не проверены`); }
  if (bad) { console.log(`LAYOUT: ${bad} поломок на ${runs} видах`); process.exit(1); }
  console.log(`LAYOUT OK: ${runs} видов`); process.exit(0);
}

const TYPES = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".woff2": "font/woff2", ".png": "image/png", ".json": "application/json" };
const srv = createServer(async (req, res) => {
  let p = decodeURIComponent(new URL(req.url, "http://x").pathname);
  if (p.startsWith("/static/")) p = p.slice(7);
  const f = normalize(join(ROOT, p === "/" ? "index.html" : p));
  if (!f.startsWith(ROOT)) { res.writeHead(403); res.end(); return; }
  try { const b = await readFile(f); res.writeHead(200, { "content-type": TYPES[extname(f)] || "application/octet-stream" }); res.end(b); }
  catch (e) { res.writeHead(404); res.end(); }
});
await new Promise((ok) => srv.listen(0, "127.0.0.1", ok));
const BASE = `http://127.0.0.1:${srv.address().port}/index.html`;

// R-333 повтор №7: порт отладки выбирает сам Chrome (0 → свободный) и пишет его в DevToolsActivePort профиля — к чужому
// браузеру на занятом порту набор не подключится. Под root на Linux Chrome без --no-sandbox не стартует.
const prof = await mkdtemp(join(tmpdir(), "k3-layout-"));
const root = typeof process.getuid === "function" && process.getuid() === 0;
const chrome = spawn(CHROME, ["--headless=new", "--remote-debugging-port=0", "--user-data-dir=" + prof, "--no-first-run",
  "--hide-scrollbars", "--window-size=1440,900", ...(root ? ["--no-sandbox"] : []), "about:blank"], { stdio: ["ignore", "ignore", "pipe"], detached: true });
let chromeErr = "", chromeGone = null;
chrome.stderr.on("data", (b) => { chromeErr = (chromeErr + b).slice(-2000); });
chrome.on("error", (e) => { chromeGone = e.message; });
chrome.on("exit", (code, sig) => { chromeGone = chromeGone || "код " + code + (sig ? ", " + sig : ""); });
// REV-speed-2b С3: профиль Chrome (до 170 МБ) — удаляется при любом выходе: итог, SKIP, FAIL, INT/TERM (через «exit»)
const stop = () => { try { process.kill(-chrome.pid, "SIGKILL"); } catch (e) { try { chrome.kill("SIGKILL"); } catch (x) { /* уже нет */ } } srv.close();
  try { rmSync(prof, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 }); } catch (e) { /* не вышло — останется в $TMPDIR */ } };
process.on("exit", stop);
// Ctrl-C, TERM (и от части-родителя K3_LAYOUT_JOBS) — выход через «exit»: Chrome в своей группе гасится, а не остаётся
for (const [s, n] of [["SIGINT", 2], ["SIGTERM", 15]]) process.on(s, () => process.exit(128 + n));
let wsUrl = null;
for (let i = 0; i < 75 && !wsUrl && !chromeGone; i++) {
  try {
    const port = Number((await readFile(join(prof, "DevToolsActivePort"), "utf8")).split("\n")[0]);
    const l = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json(); const p = l.find((x) => x.type === "page"); if (p) wsUrl = p.webSocketDebuggerUrl;
  } catch (e) { /* ещё поднимается */ }
  if (!wsUrl) await sleep(200);
}
// Chrome не поднялся — это среда, а не вёрстка: SKIP с причиной (как без Chrome), k3_test.sh не краснеет
if (!wsUrl) {
  const why = chromeGone ? "Chrome завершился (" + chromeGone + ")" : "Chrome не ответил за 15 с";
  const tail = chromeErr.trim().split("\n").filter(Boolean).slice(-1)[0] || "";
  console.log("SKIP: " + why + (tail ? ": " + tail.slice(0, 200) : "") + " — вёрстка панели не проверена"); process.exit(77);
}
const ws = new WebSocket(wsUrl);
await new Promise((ok) => ws.addEventListener("open", ok));
let seq = 0; const wait = new Map();
// ошибки страницы — находка того вида, на котором случились
const jsErrs = [];
const onEvent = (m) => {
  if (m.method === "Runtime.exceptionThrown") { const d = m.params.exceptionDetails || {}; jsErrs.push(((d.exception && d.exception.description) || d.text || "exception").split("\n").slice(0, 2).join(" | ")); }
  else if (m.method === "Runtime.consoleAPICalled" && (m.params.type === "error" || m.params.type === "assert")) jsErrs.push("console." + m.params.type + ": " + (m.params.args || []).map((a) => a.value !== undefined ? String(a.value) : (a.description || a.type)).join(" ").slice(0, 300));
};
ws.addEventListener("message", (ev) => { const m = JSON.parse(ev.data); if (m.id && wait.has(m.id)) { wait.get(m.id)(m); wait.delete(m.id); } else if (m.method) onEvent(m); });
const cdp = (method, params = {}) => new Promise((ok, bad) => { const id = ++seq; wait.set(id, (m) => (m.error ? bad(new Error(method + ": " + m.error.message)) : ok(m.result))); ws.send(JSON.stringify({ id, method, params })); });
const js = async (expr) => { const r = await cdp("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true }); if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails).slice(0, 300)); return r.result.value; };
const until = async (expr, ms) => { const end = Date.now() + ms; while (Date.now() < end) { try { if (await js(expr)) return true; } catch (e) { /* страница грузится */ } await sleep(150); } return false; };
const view = (w, h = 900) => cdp("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile: w < 600 });
// окно вытягивается по высоте содержимого: проверяется вся страница, а не первый экран (область .gx-main прокручивается)
async function fit(w) {
  let h = 900;
  for (let i = 0; i < 4; i++) {
    const need = await js(`(() => { let m = document.documentElement.scrollHeight - innerHeight; for (const e of document.querySelectorAll('.gx-main, .gx-sheet__b, .gs-host')) m = Math.max(m, e.scrollHeight - e.clientHeight); return m; })()`);
    if (need <= 2) break;
    h = Math.min(12000, h + need + 4); await view(w, h); await sleep(200);
  }
}

// Проверка в странице: только явные поломки (см. шапку), без вкусовых замечаний.
const CHECK = `(() => {
  const out = [], vw = innerWidth, de = document.documentElement;
  const skip = (e) => !!e.closest('.kg-stage, [aria-hidden="true"], .sr-only, .gx-vh, .kk-vh, .up-vh, [hidden]');
  const vis = (e) => { try { return e.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true }); } catch (x) { return e.offsetParent !== null; } };
  const name = (e) => { const p = []; for (let x = e, n = 0; x && x.tagName && n < 3; x = x.parentElement, n++) p.unshift(x.tagName.toLowerCase() + ((x.getAttribute('class') || '').trim() ? '.' + x.getAttribute('class').trim().split(/\\s+/).slice(0, 2).join('.') : '')); return p.join(' > '); };
  if (de.scrollWidth > vw + 1) out.push(['hscroll', 'html', de.scrollWidth + ' > ' + vw]);
  // видимая часть прямоугольника: обрезка предками с overflow ≠ visible
  const clipOf = (e) => { let r = { left: -1e9, top: -1e9, right: 1e9, bottom: 1e9 }; for (let a = e; a && a !== document.body; a = a.parentElement) { const cs = getComputedStyle(a); if (cs.overflowX !== 'visible' || cs.overflowY !== 'visible') { const q = a.getBoundingClientRect(); r = { left: Math.max(r.left, q.left), top: Math.max(r.top, q.top), right: Math.min(r.right, q.right), bottom: Math.min(r.bottom, q.bottom) }; } } return r; };
  // обрезка только «глухими» предками (overflow hidden/clip, не прокрутка): строка, которая из них вылезла, срезана
  // (многоточие — правило ellipsis; строка внутри прокручиваемой области — прокрутка, а не обрезка). По осям отдельно
  // (стенд it1 Н-4): область, которая прокручивается вниз, не оправдывает строку, срезанную справа, — раньше любой
  // прокручиваемый по вертикали предок (основная колонка панели) снимал проверку целиком, и срез на 390 не ловился
  const hardClip = (e) => { let x = null, y = null, fx = true, fy = true; const cut = (o, a, b) => (o ? [Math.max(o[0], a), Math.min(o[1], b)] : [a, b]); for (let a = e; a && a !== document.body; a = a.parentElement) { const cs = getComputedStyle(a); if (cs.textOverflow === 'ellipsis') return null; const q = a.getBoundingClientRect(); if (fx) { if (/^(auto|scroll)$/.test(cs.overflowX)) { if (a.scrollWidth > a.clientWidth + 1) fx = false; } else if (/^(hidden|clip)$/.test(cs.overflowX)) x = cut(x, q.left, q.right); } if (fy) { if (/^(auto|scroll)$/.test(cs.overflowY)) { if (a.scrollHeight > a.clientHeight + 1) fy = false; } else if (/^(hidden|clip)$/.test(cs.overflowY)) y = cut(y, q.top, q.bottom); } } return x || y ? { left: x ? x[0] : -1e9, right: x ? x[1] : 1e9, top: y ? y[0] : -1e9, bottom: y ? y[1] : 1e9 } : null; };
  // слой: ближайший предок с position fixed/sticky — содержимое уезжает под шапку и вкладки, это не наложение
  const layer = (e) => { for (let a = e; a && a !== document.body; a = a.parentElement) { const ps = getComputedStyle(a).position; if (ps === 'fixed' || ps === 'sticky') return a; } return null; };
  const lines = [];
  const tw = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n; (n = tw.nextNode());) {
    const p = n.parentElement; if (!p || !n.textContent.trim() || /^(SCRIPT|STYLE|OPTION|TEXTAREA)$/.test(p.tagName) || skip(p) || !vis(p)) continue;
    const rg = document.createRange(); rg.selectNodeContents(n);
    const c = clipOf(p);
    for (const r of rg.getClientRects()) {
      const q = { left: Math.max(r.left, c.left), top: Math.max(r.top, c.top), right: Math.min(r.right, c.right), bottom: Math.min(r.bottom, c.bottom) };
      if (q.right - q.left > 1 && q.bottom - q.top > 1) {
        lines.push({ p, q, h: r.height, s: n.textContent.trim().slice(0, 40), l: layer(p) });
        const k = hardClip(p);
        if (k && (r.top < k.top - 2 || r.bottom > k.bottom + 2 || r.left < k.left - 2 || r.right > k.right + 2)) out.push(['textcut', name(p), n.textContent.trim().slice(0, 40) + ' ' + (r.left < k.left - 2 || r.right > k.right + 2 ? 'x ' + Math.round(r.left) + '…' + Math.round(r.right) + ' в ' + Math.round(k.left) + '…' + Math.round(k.right) : Math.round(r.top) + '…' + Math.round(r.bottom) + ' в ' + Math.round(k.top) + '…' + Math.round(k.bottom))]);
      }
    }
  }
  for (const L of lines.reduce((m, L) => { (m.get(L.p) || m.set(L.p, []).get(L.p)).push(L); return m; }, new Map())) {
    const [p, ls] = L, fs = parseFloat(getComputedStyle(p).fontSize) || 14;
    if (ls.length >= 4 && ls.filter((x) => x.q.right - x.q.left < fs * 2.5).length >= ls.length * 0.75) out.push(['column', name(p), ls[0].s + ' (' + ls.length + ' строк)']);
  }
  const seen = new Set();
  for (let i = 0; i < lines.length; i++) for (let j = i + 1; j < lines.length; j++) {
    const A = lines[i], B = lines[j]; if (A.p === B.p || A.l !== B.l) continue;
    const x = Math.min(A.q.right, B.q.right) - Math.max(A.q.left, B.q.left), y = Math.min(A.q.bottom, B.q.bottom) - Math.max(A.q.top, B.q.top);
    if (x > 3 && y > Math.min(A.h, B.h) * 0.35) { const k = A.s + B.s; if (!seen.has(k)) { seen.add(k); out.push(['overlap', name(A.p), A.s + ' <> ' + B.s]); } }
  }
  for (const e of document.body.querySelectorAll('*')) {
    if (skip(e) || !vis(e)) continue;
    const cs = getComputedStyle(e);
    const own = [...e.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
    if (!own || cs.overflowX === 'visible' || cs.overflowX === 'auto' || cs.overflowX === 'scroll' || e.clientWidth < 2) continue;
    if (e.scrollWidth <= e.clientWidth + 1) continue;
    if (cs.textOverflow === 'ellipsis') { if (!e.getAttribute('title') && !(e.closest('[title]'))) out.push(['ellipsis', name(e), e.textContent.trim().slice(0, 60)]); }
    else out.push(['clipped', name(e), e.textContent.trim().slice(0, 60)]);
  }
  for (const e of document.body.querySelectorAll('[role=columnheader]')) {
    if (skip(e) || !vis(e) || e.clientWidth < 2) continue;
    if (e.scrollWidth > e.clientWidth + 1) out.push(['colhead', name(e), e.textContent.trim().slice(0, 60) + ' ' + e.scrollWidth + '>' + e.clientWidth]);
  }
  const ints = [...document.body.querySelectorAll('button, a[href], input, select, textarea')].filter((e) => !skip(e) && vis(e));
  for (let i = 0; i < ints.length; i++) for (let j = i + 1; j < ints.length; j++) {
    const a = ints[i], b = ints[j]; if (a.contains(b) || b.contains(a) || layer(a) !== layer(b)) continue;
    const cut = (e) => { const r = e.getBoundingClientRect(), c = clipOf(e.parentElement || e); return { left: Math.max(r.left, c.left), top: Math.max(r.top, c.top), right: Math.min(r.right, c.right), bottom: Math.min(r.bottom, c.bottom) }; };
    const ra = cut(a), rb = cut(b);   // видимые части: прокрученное под край области не считается
    if (Math.min(ra.right, rb.right) - Math.max(ra.left, rb.left) > 3 && Math.min(ra.bottom, rb.bottom) - Math.max(ra.top, rb.top) > 3)
      out.push(['controls', name(a), (a.textContent || a.getAttribute('aria-label') || '').trim().slice(0, 30) + ' <> ' + (b.textContent || b.getAttribute('aria-label') || '').trim().slice(0, 30)]);
  }
  // обрезка края: предки с overflow-x hidden/clip и область без прокрутки вбок; ряд, который на деле прокручивается, — можно
  const hclip = (e) => { let l = 0, r = vw; for (let a = e.parentElement; a && a !== document.body; a = a.parentElement) { const cs = getComputedStyle(a); if (cs.overflowX === 'visible') continue; if ((cs.overflowX === 'auto' || cs.overflowX === 'scroll') && a.scrollWidth > a.clientWidth + 1) return null; const q = a.getBoundingClientRect(); l = Math.max(l, q.left); r = Math.min(r, q.right); } return [l, r]; };
  for (const e of ints) {
    const q = e.getBoundingClientRect(); if (q.width < 2) continue;
    if (/^(INPUT|SELECT|TEXTAREA)$/.test(e.tagName) && e.type !== 'checkbox' && e.type !== 'radio' && e.type !== 'hidden' && q.width < (e.closest('.gx-gwr__oct') ? 48 : 60)) out.push(['field', name(e), Math.round(q.width) + ' px']);   // число сети шлюза — 52 px по №13 r2 (три цифры)
    const c = hclip(e); if (!c) continue;
    if (q.left < c[0] - 4 || q.right > c[1] + 4) out.push(['cut', name(e), (e.textContent || e.getAttribute('aria-label') || '').trim().slice(0, 30) + ' ' + Math.round(q.left) + '…' + Math.round(q.right) + ' в ' + Math.round(c[0]) + '…' + Math.round(c[1])]);
  }
  // колонки шапки и строк таблицы: левый и правый край каждой ячейки совпадают с ячейкой шапки
  for (const tb of document.querySelectorAll('.gx-tbl')) {
    const hd = tb.querySelector('.gx-tbl__h'); if (!hd || skip(hd) || !vis(hd)) continue;
    const hc = [...hd.children].map((x) => x.getBoundingClientRect());
    for (const r of tb.querySelectorAll('.gx-tbl__r')) {
      const j = [...r.children].findIndex((c, k) => { const a = hc[k], b = c.getBoundingClientRect(); return !a || Math.abs(a.left - b.left) > 1.5 || Math.abs(a.right - b.right) > 1.5; });
      if (j >= 0) { const a = hc[j], b = r.children[j].getBoundingClientRect(); out.push(['columns', name(tb), (hd.children[j] ? hd.children[j].textContent.trim() || '#' + (j + 1) : '#' + (j + 1)) + ': шапка ' + (a ? Math.round(a.left) + '…' + Math.round(a.right) : 'нет') + ', строка ' + Math.round(b.left) + '…' + Math.round(b.right)]); break; }
    }
  }
  // схема в общем виде: состояние блока видно крупно
  for (const nd of document.querySelectorAll('.kg-stage.is-far .kg-nd')) {
    const ch = nd.querySelector('.kg-chip--bad, .kg-chip--warn'); if (!ch) continue;
    const st = nd.querySelector('.kg-nd__farst');
    if (!st || !vis(st) || !st.textContent.trim()) out.push(['farstate', name(nd), ((nd.querySelector('.kg-nd__name') || {}).textContent || '') + ': «' + ch.textContent.trim() + '» не видно']);
  }
  // компактная карточка «Пробросов»: строка не вылезает из своей колонки на кнопки и не переносится (R-333 повтор №1)
  for (const l of document.querySelectorAll('.gx-ptbl--compact .gx-ptbl__l')) {
    if (skip(l) || !vis(l)) continue;
    if (l.scrollWidth > l.clientWidth + 1) { out.push(['ptbl', name(l), l.textContent.trim().slice(0, 50) + ': ' + l.scrollWidth + ' > ' + l.clientWidth + ' px']); continue; }
    for (const i of l.children) { const lh = parseFloat(getComputedStyle(i).lineHeight) || 20; if (i.getBoundingClientRect().height > lh * 1.6) out.push(['ptbl', name(i), i.textContent.trim().slice(0, 50) + ': перенос в ' + Math.round(i.getBoundingClientRect().height) + ' px']); }
  }
  // пул схемы: строка выхода, который не работает (как на его блоке), — со своим фоном, а не как обычная плитка
  // (R-333 повтор №2; строки в общем виде спрятаны — цвет считается и у спрятанных)
  for (const m of document.querySelectorAll('.kg-nd--pool .kg-nd__mem[data-exit]')) {
    const ex = document.querySelector('.kg-nd[data-id="exit:' + CSS.escape(m.dataset.exit) + '"]'), chip = ex && ex.querySelector('.kg-chip--bad'), rn = m.querySelector('.kg-nd__rn');
    if (!chip || !rn || !chip.textContent.trim() || !rn.textContent.trim().startsWith(chip.textContent.trim())) continue;
    const tile = document.createElement('div'); tile.className = 'kg-nd__row'; m.parentElement.appendChild(tile);
    const plain = getComputedStyle(tile).backgroundColor; tile.remove();
    if (getComputedStyle(m).backgroundColor === plain) out.push(['pooldown', name(m), ((m.querySelector('.kg-nd__rl') || {}).firstChild || {}).textContent + ': «' + chip.textContent.trim() + '», а фон строки как у обычной']);
  }
  // графики выхода (charts.js): подписи осей и подсказка не мельче 11 px на любой ширине — размер в SVG считается в единицах
  // viewBox, на 390 px viewBox 600 давал ≈ 6 px (REV-s1-t8-a З-6)
  for (const tx of document.querySelectorAll('.kc__svg text')) {
    if (skip(tx) || !vis(tx)) continue;
    const sv = tx.ownerSVGElement, vb = sv && sv.viewBox && sv.viewBox.baseVal;
    if (!vb || !vb.width) continue;
    const px = parseFloat(getComputedStyle(tx).fontSize) * sv.getBoundingClientRect().width / vb.width;
    if (px < 10.5) out.push(['tinytext', name(tx), tx.textContent.trim().slice(0, 30) + ': ' + px.toFixed(1) + ' px']);
  }
  // «Выходы › Все выходы» (3.3.7.14, Claude Design r1 «вправо», кадры Е2/Е3/Е5): переключатель вида и «Добавить ключ» —
  // у правого края строки; «+», которому не хватило места, — под переключателем, тоже у правого края, а не в начале
  // второй строки; в одной строке между ними зазор 10 px (на телефоне строка — столбик, там не проверяется)
  if (de.dataset.device !== 'phone') for (const bar of document.querySelectorAll('.kk-ex__bar')) {
    if (skip(bar) || !vis(bar)) continue;
    const b = bar.getBoundingClientRect(), dn = bar.querySelector('.kk-pp__dn'), add = bar.querySelector('.kk-ex__add');
    const a = add && vis(add) ? add.getBoundingClientRect() : null, d = dn && vis(dn) ? dn.getBoundingClientRect() : null;
    if (a && Math.abs(a.right - b.right) > 1.5) out.push(['exend', name(add), '«Добавить ключ» ' + Math.round(a.left) + '…' + Math.round(a.right) + ', правый край строки ' + Math.round(b.right)]);
    if (d && a && Math.abs((d.top + d.bottom) - (a.top + a.bottom)) / 2 < Math.min(d.height, a.height) / 2) { if (Math.abs(a.left - d.right - 10) > 1.5) out.push(['exend', name(dn), 'вид → «+»: ' + Math.round(a.left - d.right) + ' px вместо 10']); }
    else if (d && Math.abs(d.right - b.right) > 1.5) out.push(['exend', name(dn), 'переключатель вида ' + Math.round(d.left) + '…' + Math.round(d.right) + ', правый край строки ' + Math.round(b.right)]);
  }
  // пустой узел в сетке или ряду с gap: сам невидим, а зазор добавляет (REV-ui1 З-2: правила :empty не срабатывали —
  // внутри пустого ряда лежал пустой ряд кнопок, в строке устройства — пустой счётчик). Проверяется готовая раскладка
  for (const e of document.querySelectorAll('body *')) {
    if (skip(e) || e.children.length < 2 || !vis(e)) continue;
    const cs = getComputedStyle(e), row = cs.display.includes('flex') && cs.flexDirection.startsWith('row');
    if (!/grid|flex/.test(cs.display) || (row && cs.flexWrap === 'nowrap')) continue;
    const gap = row ? parseFloat(cs.columnGap) || 0 : parseFloat(cs.rowGap) || 0;
    if (gap < 4) continue;
    if (!row && cs.display.includes('grid') && cs.gridTemplateColumns.trim().split(/\\s+/).length > 1) continue;   // пустая ячейка в ряду нескольких колонок зазора не добавляет
    const z = [...e.children].filter((c) => { const s = getComputedStyle(c), q = c.getBoundingClientRect(); return s.display !== 'none' && s.display !== 'contents' && !/absolute|fixed/.test(s.position) && (row ? q.width < 1 : q.height < 1); });
    if (z.length) out.push(['phantom', name(e), z.length + ' пустых × ' + Math.round(gap) + ' px: ' + z.slice(0, 2).map((c) => name(c).split(' > ').pop()).join(', ')]);
  }
  // UI-3 (владелец 20.10): шапка компьютера в одну линию — центры логотипа (с именем узла), заголовка и кнопок шапки по
  // вертикали расходятся не больше чем на 2 px (у телефона своя шапка kk-mhead)
  {
    const hd = document.querySelector('#head.gx-head');
    const cy2 = (n) => { const b = n.getBoundingClientRect(); return b.top + b.height / 2; };
    const cy = (q) => { const n = q && document.querySelector(q); if (!n || !vis(n)) return null; return cy2(n); };
    if (hd && vis(hd)) {
      // круг 2 (П2): меряются все кнопки и поля шапки (поиск, «Онлайн», обновить, тема, «Вид»), не одна; нет логотипа,
      // заголовка или кнопки «обновить» — тоже находка (раньше правило молча сужалось)
      const ctl = [...hd.children].filter((c) => !c.matches('.gx-head__title, .gx-head__sp') && vis(c)).map((c) => [(c.id ? '#' + c.id : String(c.className).split(' ')[0]), cy2(c)]);
      const must = [['logo', cy('.gx-side:not([hidden]) .gx-brand') ?? cy('.gx-side:not([hidden]) > .gx-logo') ?? cy('#head .gx-logo')],
        ['title', cy('#head .gx-h1') ?? cy('#head .gx-topnav')], ['refresh', cy('#head #refreshBtn')]];
      const miss = must.filter((x) => x[1] === null).map((x) => x[0]);
      const pts = must.filter((x) => x[1] !== null && x[0] !== 'refresh').concat(ctl);
      const ys = pts.map((x) => x[1]);
      if (!ctl.length && !hd.querySelector('.gx-head__title')) { /* шапка не нарисована (вход) — мерить нечего */ }
      else if (miss.length) out.push(['headaxis', '#head', 'нет: ' + miss.join(', ')]);
      else if (Math.max(...ys) - Math.min(...ys) > 2) out.push(['headaxis', '#head', pts.map((x) => x[0] + '=' + Math.round(x[1])).join(' ')]);
    }
    // UI-5 (владелец 20.10): раздел с подменю — второй уровень прилегает к боковому меню (зазор ≤ 24 px на любой ширине)
    // и заметно уже первого (≤ 0,8 ширины бокового меню с подписями; меню значками или сверху — по токену --side-w)
    const sn = document.querySelector('nav.gx-subnav');
    if (sn && vis(sn)) {
      const side = document.querySelector('.gx-side:not([hidden])'), b2 = sn.getBoundingClientRect();
      const lab = side && vis(side) && !side.classList.contains('gx-side--icons');
      const w1 = lab ? side.getBoundingClientRect().width : parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--side-w')) || 248;
      const edge = side && vis(side) ? side.getBoundingClientRect().right : 0;
      // эталон 23.10 (HANDOFF r4, задание UI-A1): на ≥ 2000 подменю 200 px — это 0,81 меню 248; предел — 0,8 первого или 200
      if (b2.width > Math.max(w1 * 0.8, 200) + 0.5) out.push(['subnav', 'nav.gx-subnav', Math.round(b2.width) + 'px > 0,8 × ' + Math.round(w1) + 'px первого уровня и > 200']);
      if (b2.left - edge > 24.5) out.push(['subnav', 'nav.gx-subnav', 'зазор до первого уровня ' + Math.round(b2.left - edge) + 'px > 24']);
    }
    // 3.3.7.10 (R-q6-screens): строка бокового меню с подписями не шире его колонки — длинная подпись («Пользователи и
    // устройства») переносится, метка «этап 3» у «Сценариев» видна целиком. Меню прокручивается, поэтому «cut» его не видит
    const sd = document.querySelector('.gx-side:not([hidden]):not(.gx-side--icons)');
    if (sd && vis(sd)) {
      const r = sd.getBoundingClientRect().right - (parseFloat(getComputedStyle(sd).paddingRight) || 0);
      for (const it of sd.querySelectorAll('.gx-nav .gx-nav__item, .gx-nav .gx-soon')) {
        const b = it.getBoundingClientRect();
        if (b.width && b.right > r + 0.5) out.push(['sidenav', '.gx-nav ' + (it.dataset.soon || it.dataset.goto || it.textContent.trim().slice(0, 24)), Math.round(b.right) + ' > ' + Math.round(r)]);
      }
      if (sd.scrollWidth > sd.clientWidth + 0.5) out.push(['sidenav', '.gx-side', 'прокрутка вбок ' + sd.scrollWidth + ' > ' + sd.clientWidth]);
    }
  }
  // T-it1b Н-3: заголовок раздела в шапке телефона — целиком (многоточие с title «clipped»/«ellipsis» пропускают)
  for (const e of document.querySelectorAll('#head .kk-mhead__t')) {
    if (vis(e) && e.clientWidth > 1 && e.scrollWidth > e.clientWidth + 1) out.push(['headtitle', '.kk-mhead__t', e.textContent.trim().slice(0, 40) + ' ' + e.scrollWidth + '>' + e.clientWidth]);
  }
  // З-1 (REV-3313-final): шапка компьютера — заголовок раздела уступает место последним: многоточие у него только когда
  // поле поиска уже значком (не шире своего min-width). Простое flex-shrink делило нехватку с полем — «Сво…» на 1100
  for (const h of document.querySelectorAll('.gx-frame > header.gx-head')) {
    const t = h.querySelector('.gx-h1'), f = h.querySelector('.kk-hfind');
    if (!t || !f || !vis(t) || !vis(f) || t.scrollWidth <= t.clientWidth + 1) continue;
    const fw = f.getBoundingClientRect().width, fmin = parseFloat(getComputedStyle(f).minWidth) || 0;
    if (fw > fmin + 1) out.push(['headprio', '.gx-head .gx-h1', t.textContent.trim().slice(0, 40) + ' ' + t.scrollWidth + '>' + t.clientWidth + ', поле поиска ' + Math.round(fw) + ' > ' + fmin]);
  }
  // Н-5 стенда 3.3.7.13: потомок кнопки или поля шапки компьютера не выходит за свою кнопку по горизонтали и не ложится
  // на соседнюю — «⌘K» (aria-hidden: его строку overlap не видит) вылезал из сжатого поля поиска на «Онлайн» на 1100–1199.
  // Всплывающие слои (меню «Вид», подсказки, окна) — свои слои, не мерятся
  for (const h of document.querySelectorAll('.gx-frame > header.gx-head')) {
    if (!vis(h)) continue;
    const ctl = [...h.children].filter((c) => vis(c) && !c.matches('.gx-head__sp, .gx-head__title'));
    const box = ctl.map((c) => c.getBoundingClientRect());
    ctl.forEach((c, i) => {
      for (const e of c.querySelectorAll('*')) {
        if (!vis(e) || e.closest('.gx-pop, [role="menu"], [role="dialog"], [role="tooltip"], [popover], dialog')) continue;
        const b = e.getBoundingClientRect();
        if (b.width < 1 || b.height < 1) continue;
        const j = box.findIndex((q, k) => k !== i && Math.min(b.right, q.right) - Math.max(b.left, q.left) > 0.5 && Math.min(b.bottom, q.bottom) - Math.max(b.top, q.top) > 0.5);
        const outx = b.left < box[i].left - 0.5 || b.right > box[i].right + 0.5;
        if (j < 0 && !outx) continue;
        out.push(['headbox', name(e), (outx ? Math.round(b.left) + '…' + Math.round(b.right) + ' вне ' + Math.round(box[i].left) + '…' + Math.round(box[i].right) : '') + (j >= 0 ? (outx ? ', ' : '') + 'на ' + name(ctl[j]).split(' > ').pop() : '')]);
        break;
      }
    });
  }
  // T-it1b Н-5: на «Сейчас» ничто не вылезает за край своей карточки (рамка — край минус отступ, допуск 0,5 px)
  for (const c of document.querySelectorAll('.gx-page:not([hidden]) .gx-now .gx-card')) {
    if (!vis(c)) continue;
    const cb = c.getBoundingClientRect(), right = cb.right - (parseFloat(getComputedStyle(c).borderRightWidth) || 0);
    for (const e of c.querySelectorAll('*')) {
      const b = e.getBoundingClientRect();
      if (!b.width || !vis(e) || e.closest('svg')) continue;
      if (b.right > right + 0.5) { out.push(['cardedge', name(e), (e.textContent || '').trim().slice(0, 40) + ' ' + Math.round(b.right) + ' > ' + Math.round(right)]); break; }
    }
  }
  // UI-2 круг 3 (решение владельца 20.10): у карточки Hysteria2 мысль «UDP — если в мобильной сети не подключается, VLESS»
  // ровно один раз и только внутри «Подробнее» (ни жёлтой строки, ни описания, ни второй плашки) — вид hy2udp
  // UI-3 круг 2: тело свёрнутой карточки — полосой .ux-pc--drawer под рядом (data-drawer), считается вместе с карточкой
  for (const c of document.querySelectorAll('.ux-pc[data-hold-row="ib:hy2"]')) {
    const own = [c].concat(c.classList.contains('ux-pc--fold') ? [...c.parentElement.querySelectorAll(':scope > .ux-pc--drawer[data-drawer="hy2"]')] : []);
    const hits = own.flatMap((x) => [...x.querySelectorAll('*')]).filter((n) => ![...n.children].length && /UDP/.test(n.textContent) && /VLESS/.test(n.textContent));
    const attrs = own.flatMap((x) => [...x.querySelectorAll('[title], [aria-label]')]).filter((n) => /UDP[\s\S]*VLESS/.test((n.getAttribute('title') || '') + ' ' + (n.getAttribute('aria-label') || '')));
    const inside = (n) => !!n.closest('details, .ux-pc__body') && !n.closest('summary');     // в раскрываемой части, а не в строке «Подробнее»
    if (hits.length !== 1 || !inside(hits[0]) || attrs.length) out.push(['hy2udp', name(c), 'UDP→VLESS на карточке: ' + hits.length + ' раз, вне «Подробнее»: ' + hits.filter((n) => !inside(n)).length + ', в подсказках: ' + attrs.length]);
  }
  // UI-3 ч.2 → Т3 (Б r3): строки списка «Протоколов» — одной высоты (±2 px), колонки строки — под колонками шапки (±2 px)
  for (const l of document.querySelectorAll('.kb2-pl')) {
    if (!vis(l)) continue;
    const heads = [...l.querySelectorAll(':scope > .kb2-pl__g > .kb2-pl__r')].filter(vis);
    const hs = heads.map((r) => Math.round(r.getBoundingClientRect().height));
    // 3.3.7.15 V1 (№13 r2): у строки с «N устройств ждут новых настроек» (.kb2-wait) — третья строка: такие строки без неё
    // одной высоты между собой и выше остальных не больше чем на эту строку (+ 8 px зазора); Д1 (Н8): строка пометки
    // переносится в колонке имени — сравнивается высота строки без высоты её пометки (одна строка пометки или две)
    const wt = heads.map((r) => r.querySelector('.kb2-wait')), wh = wt.map((w) => (w ? Math.round(w.getBoundingClientRect().height) : 0));
    const hw = hs.map((h, i) => h - wh[i]).filter((h, i) => wt[i]), hn = hs.filter((h, i) => !wt[i]);
    if ((hn.length && Math.max(...hn) - Math.min(...hn) > 2) || (hw.length && Math.max(...hw) - Math.min(...hw) > 2) || (hw.length && hn.length && (Math.min(...hs.filter((h, i) => wt[i])) < Math.max(...hn) - 2 || Math.max(...hw) - Math.min(...hn) > 8))) out.push(['pcrow', name(l), 'высоты строк ' + hs.join('/')]);
    // Д1 (Н8): строка пометки не срезана (была «1 устройство — желательно выдать» без «заново» — многоточие в колонке имени)
    for (const w of wt) if (w && w.scrollWidth > w.clientWidth + 1) out.push(['pcrow', name(w), 'строка пометки срезана: «' + w.textContent.trim() + '»']);
    const hd = l.querySelector(':scope > .kb2-pl__hd');
    if (!hd || !vis(hd)) continue;
    // подписи шапки — с колонки имени (у строки первая ячейка — значок): подпись i ↔ ячейка i + 1
    const cols = [null, ...[...hd.children].map((c) => (vis(c) && c.textContent.trim() ? Math.round(c.getBoundingClientRect().left) : null))];
    for (const r of heads) [...r.children].forEach((c, i) => {
      if (cols[i] === null || cols[i] === undefined || !vis(c)) return;
      const x = Math.round(c.getBoundingClientRect().left);
      if (Math.abs(x - cols[i]) > 2) out.push(['pcrow', name(r) + ' [' + (r.parentElement.getAttribute('data-kind') || '') + ']', 'колонка ' + (i + 1) + ': ' + x + ' ≠ шапка ' + cols[i]]);
    });
  }
  // UI-2 (живой узел 3.3.7.3): пустота > 80 px внутри карточки «Протоколов», её строки описания (общая на ряд) и плитки
  // «Обзора» (плитки ряда одной высоты) — низ блока минус низ самого нижнего ребёнка, как VOID в k3/stand/ui_audit.js.
  // Кроме мока uxfail: причина «не работает» (3–5 строк) пока растягивает строку описания ряда — было до UI-2 (130 → 106 px
  // на 1440). Принято координатором как есть (BRIEF-UI-2-r2 п. 6: на виду причина «не работает» важнее ровного ряда), в бэклоге —
  // k3/stand/logs/R-ui2.md, «Круг 2» (там же пустой узел empty=1: колонки «Обзора» 961/751)
  if (!/uxfail=/.test(location.search)) for (const e of document.querySelectorAll('.ux-pc, .ux-pc__desc, .gx-kpi')) {
    if (skip(e) || !vis(e)) continue;
    const kids = [...e.children].filter((c) => vis(c) && !/absolute|fixed/.test(getComputedStyle(c).position));
    if (!kids.length) continue;
    const r = e.getBoundingClientRect(), cs = getComputedStyle(e);
    const g = r.bottom - (parseFloat(cs.paddingBottom) || 0) - (parseFloat(cs.borderBottomWidth) || 0) - Math.max(...kids.map((c) => c.getBoundingClientRect().bottom));
    if (g > 80) out.push(['void', name(e), Math.round(g) + ' px пустоты: ' + (e.textContent || '').trim().slice(0, 30)]);
  }
  // колонки «Обзора» (.gx-flow) — низ вровень (разброс ≤ 80 px); только на сцене живого узла: у обычного мока события
  // приходят случайно, и высота колонки «События» от прогона к прогону разная
  if (new URLSearchParams(location.search).get('scene')) for (const e of document.querySelectorAll('.gx-flow')) {
    if (!vis(e) || getComputedStyle(e).columnCount === '1') continue;
    // UI-2 круг 3 (REV-ui2-2 З1, решение координатора): «Показать ещё» прячет только строки «Событий» без тона — тревоги,
    // предупреждения и восстановления на виду всегда. Если без тона на виду не осталось ни одной строки, прятать нечего:
    // разброс — цена правила, а не сбой flowFill (на сцене node3373 все 10 событий с тоном)
    const fill = e.querySelector('[data-flow-fill]');
    if (fill && ![...fill.children].some((r) => r.classList.contains('gx-list__i') && !r.hidden
      && !r.querySelector('.gx-list__ic--danger, .gx-list__ic--warning, .gx-list__ic--success, .gx-list__ic--accent'))) continue;
    const b = {};
    for (const c of e.children) { if (!vis(c)) continue; const k = Math.round(c.getBoundingClientRect().left / 4); b[k] = Math.max(b[k] || 0, c.getBoundingClientRect().bottom); }
    const v = Object.values(b), top = e.getBoundingClientRect().top;
    if (v.length > 1 && Math.max(...v) - Math.min(...v) > 80) out.push(['colspread', name(e), v.map((x) => Math.round(x - top)).join('/')]);
  }
  // А5 (27.10): окно и лист «Графика» не прокручиваются вбок, подписи оси времени не налезают друг на друга (восемь
  // «20 Tue» распирали лист на 390 — график уезжал за край, а прокрутка внутри листа — не hscroll документа)
  for (const e of document.querySelectorAll('.gx-gr')) {
    if (!vis(e) || skip(e)) continue;
    if (e.scrollWidth > e.clientWidth + 1) out.push(['grfit', name(e), 'шире себя ' + e.scrollWidth + ' > ' + e.clientWidth]);
    for (let a = e.parentElement; a && a !== document.body; a = a.parentElement) {
      if (/auto|scroll/.test(getComputedStyle(a).overflowX) && a.scrollWidth > a.clientWidth + 1) { out.push(['grfit', name(a), 'вбок ' + a.scrollWidth + ' > ' + a.clientWidth]); break; }
      if (a.matches('.gx-bsheet, .gx-sheet, [role="dialog"]')) break;
    }
    // сетка графиков (grid.css): подписи времени gx-grid__x — налезающие код прячет (is-hid), видимые не налезают
    const ax = [...e.querySelectorAll('.gx-grid__x > span')].filter(vis).map((x) => x.getBoundingClientRect());
    for (let i = 1; i < ax.length; i++) if (ax[i].left < ax[i - 1].right - 0.5) { out.push(['grfit', name(e), 'подписи оси ' + i + '/' + (i + 1) + ' налезают']); break; }
  }
  return out;
})()`;

let bad = 0, runs = 0;
await cdp("Page.enable"); await cdp("Runtime.enable");
const jsFail = (where) => { for (const e of jsErrs.splice(0)) { bad++; console.log(`FAIL ${where}: js | ${e.slice(0, 300)}`); } };
// K3_PAGES=tour — только знакомство с панелью (с 360×640 и без полного набора): поломки тура не ждут 13-минутного прогона
const TOUR_ONLY = PAGES.length === 1 && PAGES[0] === "tour";
const PS_ONLY = PAGES.length === 1 && PAGES[0] === "pset";          // K3_PAGES=pset — только окна «Настройки протокола» (3.3.7.15 V1)
const BEH_ONLY = PAGES.length === 1 && PAGES[0] === "behave";      // K3_PAGES=behave — только поведение окон (3.3.7.15 Д1)
// R-speed-2: группы, а не разделы — в цикле разделов их нет (K3_PAGES=pset,find — окна и поиск, а не страница «#pset»)
const GROUPS = ["tour", "pset", "behave", "find"];
const MAIN = PAGES.filter((p) => !GROUPS.includes(p));
// K3_LAYOUT_MAP_OUT: какие .css и .js нужны группе видов. .js — загружен страницей; .css — правила, подходящие к элементу
// вида сейчас (состояния и псевдоэлементы сняты, @media не смотрится, разбор не удался — «подходит»: с запасом). Правило —
// по номеру в листе («12», «40.3» — в @media); уже подошедшие в этой группе не проверяются снова; список селекторов
// листа — один раз (у правил, не подошедших нигде, — пустой список групп: tools/test_select.py их правку не гоняет)
function usedProbe(known, listed) {
  const got = { js: [], css: {}, sels: {} };
  const rel = (u) => { try { const x = new URL(u, location.href); return x.origin === location.origin ? x.pathname.replace(/^\/(?:static\/)?/, "") : ""; } catch (e) { return ""; } };
  for (const s of document.scripts) if (s.src) { const f = rel(s.src); if (f) got.js.push(f); }
  const strip = (sel) => sel.replace(/::?(?:before|after|first-line|first-letter|marker|placeholder|selection|backdrop|file-selector-button|-webkit-[-\w]+|-moz-[-\w]+)(?:\([^)]*\))?|:(?:hover|focus-visible|focus-within|focus|active|visited|target|checked|indeterminate|placeholder-shown|user-invalid|user-valid|invalid|valid|autofill|open|popover-open|modal|fullscreen|playing|paused|default)(?![-\w])/g, "");
  for (const sh of document.styleSheets) {
    const f = rel(sh.href || ""); if (!f) continue;
    const skip = new Set(known[f] || []), all = listed.includes(f) ? null : {}, hits = [];
    let rules; try { rules = sh.cssRules; } catch (e) { got.css[f] = ["*"]; continue; }
    const walk = (rs, path) => {
      for (let i = 0; i < rs.length; i++) {
        const r = rs[i], id = path + i;
        if (r.selectorText !== undefined) {
          if (all) all[id] = r.selectorText;
          if (!skip.has(id)) {
            const t = strip(r.selectorText).trim(); let ok = !t;
            if (!ok) { try { ok = !!document.querySelector(t); } catch (e) { ok = true; } }
            if (ok) hits.push(id);
          }
        }
        if (r.cssRules && r.cssRules.length) walk(r.cssRules, id + ".");    // @media, @supports, @container, @layer
      }
    };
    walk(rules, "");
    if (hits.length) got.css[f] = hits;
    if (all) got.sels[f] = all;
  }
  return got;
}
const USED = new Map(), SELS = new Map();    // группа → { files, ids: файл → номера правил }; файл → { номер: селектор }
const note = async (group) => {
  if (!MAP_OUT) return;
  const u = USED.get(group) || { files: new Set(), ids: new Map(), all: false }; USED.set(group, u);
  if (u.all) return;
  const known = Object.fromEntries([...u.ids].map(([f, s]) => [f, [...s]]));
  try {
    const got = await js(`(${usedProbe.toString()})(${JSON.stringify(known)}, ${JSON.stringify([...SELS.keys()])})`);
    for (const f of got.js) u.files.add(f);
    for (const [f, ids] of Object.entries(got.css)) {
      u.files.add(f); const s = u.ids.get(f) || new Set(); u.ids.set(f, s);
      for (const id of ids) s.add(id);
    }
    for (const [f, all] of Object.entries(got.sels)) SELS.set(f, all);
  } catch (e) { u.all = true; }      // не узнали — группе нужно всё (test_select возьмёт её при любой правке)
};
const mapDoc = () => {
  const groups = {}, rules = {};
  for (const [g, u] of USED) groups[g] = u.all ? ["*"] : [...u.files];
  for (const [f, all] of SELS) {
    const t = rules[f] = {};
    for (const [id, sel] of Object.entries(all)) {
      const gs = t[sel] || (t[sel] = []);
      for (const [g, u] of USED) if ((u.ids.get(f) || new Set()).has(id) || (u.ids.get(f) || new Set()).has("*")) gs.push(g);
    }
  }
  return { full: !process.env.K3_PAGES, groups, rules };
};
for (const lang of LANGS) for (const theme of THEMES) for (const qs of QS) for (const page of MAIN) {
  if (qs && !QS_PAGES[qs].includes(page)) continue;
  if (!mine()) continue;
  await view(WIDTHS[WIDTHS.length - 1]);
  await cdp("Page.navigate", { url: `${BASE}?mock=1&lang=${lang}${qs ? "&" + qs : ""}#${page}` }); await sleep(300);
  // раскрытые карточки «Протоколов» панель помнит (UI-3) — каждый вид начинается со свёрнутых
  await js(`${FONT_LS}localStorage.setItem("kaskad-appearance-v1", JSON.stringify({ v: 3, theme: "${theme}" })); localStorage.removeItem("kaskad-ib-open"); true`);
  await cdp("Page.reload"); await sleep(600);
  if (qs === UX && page === "settings?tab=bot") { await sleep(300); await js(`document.querySelectorAll('.ux-fold:not(.is-open) .ux-fold__head').forEach((b) => b.click()); true`); }
  const ws = qs === UX && !WIDTHS.includes(NARROW) ? [NARROW, ...WIDTHS] : qs === SCENE && !WIDTHS.includes(SCENE_W) ? [...WIDTHS, SCENE_W] : WIDTHS;
  // ключ агента: шаг 1 и на вкладке Windows («PowerShell · Windows 10 и 11» длиннее подписи macOS)
  // UI-3 ч.2: «Протоколы» — свёрнутые (как по умолчанию) и все раскрытые
  // UI-A2: «Выходы › Группы» — свёрнутые (по умолчанию) и всё раскрытое: группы, «Тест скорости», «Поведение и проверки»,
  // полный текст строки про адреса сайтов
  for (const os of qs === UX && page === "settings?tab=bot" ? ["", "win"] : page === "inbounds" && (qs === UX || qs === SCENE) ? ["", "open"] : page === "inbounds" && !qs ? ["", "del", "inst"] : page === "exits?tab=groups" && !qs ? ["", "open", "rg"] : page === "exits?tab=groups" && (qs === ORD || qs === ORD_M) ? ["", "open"] : page === "settings?tab=addresses" && !qs ? ["", "open"] : page === "exits?tab=ru" && (qs === RUG || qs === RUG_T) ? ["", "open"] : [""]) {
  if (os === "open" && page === "exits?tab=groups") {
    await until(`!!document.querySelector('.kk-eg-tog')`, 6000);
    for (const sel of ['.kk-eg-tog[aria-expanded="false"]', '.kk-eg-more__tog[aria-expanded="false"]', '.kk-eg-rare__tog[aria-expanded="false"]', '.kk-eg-dns button[aria-expanded="false"]']) {
      await js(`(() => { for (let i = 0; i < 20; i++) { const b = document.querySelector(${JSON.stringify(sel)}); if (!b) break; b.click(); } return true; })()`); await sleep(400);
    }
  } else if (os === "open" && page === "exits?tab=ru") {
    // карточки российских групп раскрыты все (общая карточка групп, РФ-группы Ш1: стрелка перерисовывает карточку —
    // по одной), с «Поведением и проверками» и «Ещё»
    await until(`!!document.querySelector('.kk-eg-card--ru .kk-eg-tog')`, 6000);
    for (const sel of ['.kk-eg-card--ru .kk-eg-tog[aria-expanded="false"]', '.kk-eg-card--ru .kk-eg-more__tog[aria-expanded="false"]', '.kk-eg-card--ru .kk-eg-rare__tog[aria-expanded="false"]']) {
      await js(`(() => { for (let i = 0; i < 20; i++) { const b = document.querySelector(${JSON.stringify(sel)}); if (!b) break; b.click(); } return true; })()`); await sleep(400);
    }
  } else if (os === "open" && page === "settings?tab=addresses") {
    // 3.3.7.15: раскрыта строка первого адреса (два замка «Не принимать», цифры с отметками и ссылкой)
    await until(`!!document.querySelector('.kk-adr-r__b')`, 6000);
    await js(`(() => { const b = document.querySelector('.kk-adr-r__b'); if (b) b.click(); return true; })()`); await sleep(300);
  } else if (os === "rg") {
    // 3.3.7.15 F14: подтверждение «Разложить заново» у первой группы, где кнопка доступна (прогон dry мока — 0,5 с)
    await until(`!!document.querySelector('[data-rg]')`, 6000);
    await js(`(() => { const b = [...document.querySelectorAll('[data-rg]')].find((x) => x.getAttribute('aria-disabled') !== 'true'); if (b) b.click(); return true; })()`);
    await until(`!!document.querySelector('.kk-rg__bars')`, 4000);
  } else if (os === "open") await js(`document.querySelectorAll('.ux-pc__tog[aria-expanded="false"]').forEach((b) => b.click()); true`);
  else if (os) await js(`[...document.querySelectorAll('.ux-step [role=tab]')].filter((e) => e.offsetParent && /Windows/.test(e.textContent)).forEach((e) => e.click()); true`);
  for (const w of ws) {
    await view(w); await sleep(250);
    // #…&open=rule — форма условия открывается после чтения политики (T8 ч.2): ждать и её
    await until(`(() => { const p = document.querySelector('.gx-page:not([hidden])'); return !!p && p.children.length > 0 && !document.querySelector('#progress:not([hidden])') && !document.querySelector('.gx-skel') && (!location.hash.includes('open=rule') || !!document.querySelector('.kk-eg-rform')); })()`, 8000);
    // UI-3 ч.2, Т3: на 1440×900 все установленные протоколы сцены node3373 — в первом экране, под схемой (до fit: окно ещё 900)
    if (page === "inbounds" && qs === SCENE && w === 1440 && !os) {
      const miss = await js(`(() => { const cs = [...document.querySelectorAll('.kb2-pl > .kb2-pl__g > .kb2-pl__r')]; if (!cs.length) return 'нет строк протоколов'; const low = cs.filter((c) => c.getBoundingClientRect().bottom > innerHeight); return low.length ? low.length + ' из ' + cs.length + ' ниже ' + innerHeight + ' px' : ''; })()`);
      if (miss) { bad++; console.log(`FAIL ${page}&${qs} ${w} ${theme} ${lang}: pcfirst | .kb2-pl | ${miss}`); }
      // UI-3 круг 2 (З1) → Т3: раскрытие VLESS — под курсором та же VLESS, строки выше на местах (относительно списка:
      // раскрытие докручивает тело в окно), тело — сразу под её строкой, следующая строка — ниже тела; затем свернуть
      const moved = await js(`(async () => { const l = document.querySelector('.kb2-pl'), v = l && l.querySelector(':scope > .kb2-pl__g[data-kind="vless"]'); if (!v) return 'нет VLESS';
        const gs = [...l.querySelectorAll(':scope > .kb2-pl__g')], i = gs.indexOf(v), head = v.querySelector(':scope > .kb2-pl__r');
        const l0 = l.getBoundingClientRect().top, before = gs.map((g) => Math.round(g.getBoundingClientRect().top - l0));
        const b0 = head.getBoundingClientRect(), x = b0.left + b0.width / 2, y = b0.top + b0.height / 2;
        head.click(); await new Promise((r) => setTimeout(r, 200));
        const err = [], l1 = l.getBoundingClientRect().top, at = document.elementFromPoint(x, y + (l1 - l0));
        if (!at || at.closest('.kb2-pl__g') !== v) err.push('под курсором не VLESS');
        gs.forEach((g, k) => { if (k <= i && Math.round(g.getBoundingClientRect().top - l1) !== before[k]) err.push(g.dataset.kind + ' сдвинулась'); });
        const body = v.querySelector(':scope > .kb2-pl__b');
        if (!body || body.hidden || !body.getClientRects().length) err.push('нет тела');
        else {
          const hb = head.getBoundingClientRect().bottom, bb = body.getBoundingClientRect(), nx = gs[i + 1];
          if (bb.top < hb - 1 || bb.top - hb > 24) err.push('тело не сразу под строкой: ' + Math.round(bb.top - hb) + ' px');
          if (nx && nx.getBoundingClientRect().top < bb.bottom - 1) err.push(nx.dataset.kind + ' наехала на тело');
        }
        head.click(); return err.join('; '); })()`);
      if (moved) { bad++; console.log(`FAIL ${page}&${qs} ${w} ${theme} ${lang}: pcopen | .kb2-pl | ${moved}`); }
    }
    // Т3: [del] — вопрос «Удалить протокол?» в раскрытой строке Hysteria2, [inst] — окно установки первого из «Можно
    // установить» (варианты порта пришли с сервера). Вопрос сам закрывается через 30 с, окно — нет: на каждой ширине
    // открыть заново, если закрылось
    if (page === "inbounds" && os === "del") {
      const miss = await js(`(async () => { if (document.querySelector('.ux-confirm')) return ''; const g = document.querySelector('.kb2-pl__g[data-kind="hy2"]'); if (!g) return 'нет строки Hysteria2';
        if (!g.classList.contains('is-open')) g.querySelector('.kb2-pl__r').click(); await new Promise((r) => setTimeout(r, 200));
        const d = g.querySelector('.ux-pc__del'); if (!d) return 'нет «Удалить протокол»'; d.click(); await new Promise((r) => setTimeout(r, 200)); return g.querySelector('.ux-confirm') ? '' : 'нет вопроса'; })()`);
      if (miss) { bad++; console.log(`FAIL ${page} [del] ${w} ${theme} ${lang}: view | ${miss}`); }
    } else if (page === "inbounds" && os === "inst") {
      const miss = await js(`(async () => { if (document.querySelector('.kb2-pp')) return ''; const b = document.querySelector('section[data-page="inbounds"] .kk-irow:not(.is-na) button'); if (!b) return 'нет «Установить»';
        b.click(); for (let k = 0; k < 40 && !document.querySelector('.kb2-pp__r'); k++) await new Promise((r) => setTimeout(r, 100)); return document.querySelector('.kb2-pp__r') ? '' : 'нет вариантов порта'; })()`);
      if (miss) { bad++; console.log(`FAIL ${page} [inst] ${w} ${theme} ${lang}: view | ${miss}`); }
      await sleep(400);
    }
    // UI-A2 (HANDOFF r3: часть — в один экран 1440×900, содержимое ≤ 900 − 12 − 64 − 20 − 24 = 780): список групп мока
    if (page === "exits?tab=groups" && !qs && !os && w === 1440) {
      const h = await js(`(() => { const p = document.querySelector('.kk-part'); return p ? Math.round(p.getBoundingClientRect().height) : -1; })()`);
      if (!(h > 0 && h <= 780)) { bad++; console.log(`FAIL ${page} ${w} ${theme} ${lang}: egfit | .kk-part | высота ${h} > 780`); }
    }
    await fit(w); await sleep(300);
    // 3.3.7.15 «Ширина → сетка» (HANDOFF-addresses, HANDOFF-regroup): пороги частей — по ширине части (kkpart)
    if (page === "settings?tab=addresses" && w >= 700) {
      const r = await js(`(() => { const a = document.querySelector('#stAdr'), s = document.querySelector('.kk-adr__side'); if (!a || !s) return 'нет блоков «Адреса» или «Выдача» и «Выход»';
        const pw = a.closest('.kk-adr-host').getBoundingClientRect().width, A = a.getBoundingClientRect(), B = s.getBoundingClientRect(), err = [], P = Math.round(pw);
        if (pw >= 1360 && !(B.left >= A.right - 1 && Math.abs(B.top - A.top) <= 2)) err.push('часть ' + P + ': «Выдача» и «Выход» не справа от «Адресов»');
        if (pw < 1360 && !(B.top >= A.bottom - 1)) err.push('часть ' + P + ': «Выдача» и «Выход» не под «Адресами»');
        const ip = document.querySelector('.kk-adr-r__ip'), out = document.querySelector('.kk-adr-r__out');
        if (ip && out) { const I = ip.getBoundingClientRect(), O = out.getBoundingClientRect(), dy = Math.abs((O.top + O.bottom) / 2 - (I.top + I.bottom) / 2);
          if (pw < 640 && !(O.top >= I.bottom - 1)) err.push('часть ' + P + ': строка адреса не в две линии');
          if (pw >= 640 && dy > 6) err.push('часть ' + P + ': строка адреса не в одну линию'); }
        const c = document.querySelector('.kk-adr-det .kk-adr-ctl'), k = document.querySelector('.kk-adr-det > .gx-kv');
        if (c && k) { const C = c.getBoundingClientRect(), V = k.getBoundingClientRect();
          if (pw >= 760 && !(V.left >= C.right - 1)) err.push('часть ' + P + ': раскрытая строка не в две колонки');
          if (pw < 760 && !(V.top >= C.bottom - 1)) err.push('часть ' + P + ': цифры раскрытой строки не под выбором'); }
        return err.join('; '); })()`);
      if (r) { bad++; console.log(`FAIL ${page}${qs ? "&" + qs : ""}${os ? " [" + os + "]" : ""} ${w} ${theme} ${lang}: adrgrid | .kk-adr | ${r}`); }
    }
    if (page === "settings?tab=addresses") {
      const r = await js(`(() => { const err = [], vis = (e) => !!e && getComputedStyle(e).display !== 'none', ctx = document.createElement('canvas').getContext('2d');
        const tw = (e, s) => { ctx.font = getComputedStyle(e).font; return ctx.measureText(s).width; };
        const cols = [...document.querySelectorAll('.kk-adr-map__c')];
        cols.forEach((c) => { const ip = c.querySelector('.kk-adr-map__ip'), sh = c.querySelector('.kk-adr-map__ips'); if (!ip || !sh) return;
          if (vis(ip) && ip.scrollWidth > ip.clientWidth + 1 && sh.textContent !== ip.textContent) err.push(ip.textContent + ': полный адрес обрезан, а короткий есть');
          if (vis(sh) && !vis(ip) && tw(sh, ip.textContent) <= sh.clientWidth - 1) err.push(ip.textContent + ': короткая подпись, хотя полный адрес входит'); });
        const lg = document.querySelector('.kk-adr-map__lg');
        if (lg && cols.length && !cols.some((c) => (c.getAttribute('data-adr-col') || '').indexOf(':') >= 0) && lg.children.length > 5) err.push('нет IPv6, а дорожки «Выдача IPv6» и «Срок IPv6» есть');
        const e6 = document.querySelector('[data-adr-e]');
        if (${JSON.stringify(qs)} === 'adr=c3' && (!e6 || e6.getAttribute('data-adr-e') !== 'off')) err.push('ipv6_off: не «IPv6 выключен в ядре»');
        const svg = document.querySelector('.kk-adr-sch__svg');
        if (svg && svg.getBoundingClientRect().width < 55.5) err.push('середина схемы «Выхода» ' + Math.round(svg.getBoundingClientRect().width) + ' px < 56');
        if (svg) { const ys = [...svg.querySelectorAll('path')].map((p) => Math.round(+((p.getAttribute('d') || '').split(' ')[1] || -1)));
          document.querySelectorAll('.kk-adr-sch__l > .kk-adr-sch__n').forEach((n) => { const y = Math.round(parseFloat(n.style.top));
            if (!ys.includes(y)) err.push('узел схемы ' + (n.querySelector('.kk-adr-sch__ip') || n).textContent + ' без линии'); }); }
        document.querySelectorAll('.kk-adr-out .gx-kv__row').forEach((row) => { const k = row.querySelector('.gx-kv__k'), s = row.querySelector('select'); if (!k || !s) return;
          if (Math.abs(k.getBoundingClientRect().top - s.closest('.gx-kv__v').getBoundingClientRect().top) > 2) return;
          const cs = getComputedStyle(s), need = Math.max(...[...s.options].map((o) => tw(s, o.text))) + parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight);
          if (s.clientWidth + 1 < need) err.push('«' + k.textContent + '»: выбор обрезан (' + s.clientWidth + ' < ' + Math.round(need) + ')'); });
        document.querySelectorAll('.kk-adr-det .gx-kv__text').forEach((t) => { if (t.scrollWidth > t.clientWidth + 1) err.push('цифры: «' + t.textContent.slice(0, 40) + '» обрезано'); });
        // Д2b (D-3315-view-adr-r2 «Д1, пустота», option-Д1-grid): дорожки 2 × 2 на любой ширине, «Приём» под ними; часть
        // ≥ 760 — цифры справа, верх по линии дорожек, пустота под «Для выхода» (низ последнего значения цифр − низ поля)
        // не больше 56 px
        const det = document.querySelector('.kk-adr-det'), L = det && det.querySelector(':scope > .kk-adr-lanes'), C = det && det.querySelector(':scope > .kk-adr-ctl'), V = det && det.querySelector(':scope > .gx-kv');
        if (L && C && V) { const pw = det.closest('.kk-adr-host').getBoundingClientRect().width, P = Math.round(pw), ln = [...L.children], LR = L.getBoundingClientRect();
          if (ln.length >= 3 && Math.abs(ln[2].getBoundingClientRect().top - ln[0].getBoundingClientRect().top) <= 1) err.push('часть ' + P + ': дорожки раскрытой строки не 2 × 2');
          if (C.getBoundingClientRect().top < LR.bottom - 1) err.push('часть ' + P + ': «Приём» не под дорожками');
          const F = C.lastElementChild, lv = [...V.querySelectorAll('.gx-kv__v')].pop();
          if (pw >= 760 && F && lv) { const rg = document.createRange(); rg.selectNodeContents(lv);
            const gap = Math.round(rg.getBoundingClientRect().bottom - F.getBoundingClientRect().bottom);
            if (gap > 56) err.push('часть ' + P + ': пустота под «Для выхода» ' + gap + ' px > 56');
            if (Math.abs(V.getBoundingClientRect().top - LR.top) > 2) err.push('часть ' + P + ': цифры раскрытой строки не на линии дорожек'); } }
        return err.join('; '); })()`);
      if (r) { bad++; console.log(`FAIL ${page}${qs ? "&" + qs : ""}${os ? " [" + os + "]" : ""} ${w} ${theme} ${lang}: adrfit | .kk-adr | ${r}`); }
    }
    if (page === "exits?tab=groups" && os === "rg" && w >= 700) {
      const r = await js(`(() => { const g = document.querySelector('.kk-rg'); if (!g) return 'нет подтверждения «Разложить заново»';
        const b = g.querySelector('.kk-rg__bars'), s = g.querySelector('.kk-rg__sum'), f = g.querySelector('.kk-rg__foot'); if (!b || !s) return 'нет полосок или итога';
        const pw = g.closest('.kk-part').getBoundingClientRect().width, B = b.getBoundingClientRect(), S = s.getBoundingClientRect(), G = g.getBoundingClientRect(), err = [], P = Math.round(pw);
        if (pw >= 880 && !(S.left >= B.right - 1)) err.push('часть ' + P + ': итог не справа от полосок');
        if (pw < 880 && !(S.top >= B.bottom - 1)) err.push('часть ' + P + ': итог не под полосками');
        const btn = f ? [...f.querySelectorAll('.ga-btn')].pop() : null;
        if (pw >= 880 && btn && G.right - btn.getBoundingClientRect().right > 24) err.push('часть ' + P + ': кнопки не у правого края подтверждения');
        return err.join('; '); })()`);
      if (r) { bad++; console.log(`FAIL ${page} [rg] ${w} ${theme} ${lang}: rggrid | .kk-rg | ${r}`); }
    }
    const found = await js(CHECK); runs++;
    await note(page);
    for (const [kind, where, what] of found) { bad++; console.log(`FAIL ${page}${qs ? "&" + qs : ""}${os ? " [" + os + "]" : ""} ${w} ${theme} ${lang}: ${kind} | ${where} | ${what}`); }
    jsFail(`${page}${qs ? "&" + qs : ""}${os ? " [" + os + "]" : ""} ${w} ${theme} ${lang}`);
  }
  }
}
// Телефон 3.3.6 (макеты «Kaskad Mobile» М1–М15): виды, которых нет среди разделов, — «Ещё», детали во весь экран, листы
// снизу («Поделиться», «⋯», устройство, подписка), форма во весь экран, части «Настроек», вход. Ширины телефона из набора
// (< 700) и 360; нажатие — первая строка списка (id мока не нужны). K3_PAGES — только разделы с этими именами.
const MCLICK = (sel, then) => `(async () => { const b = document.querySelector(${JSON.stringify(sel)}); if (!b) return 'нет ' + ${JSON.stringify(sel)}; b.click(); ${then ? `await new Promise((r) => setTimeout(r, 900)); const c = document.querySelector(${JSON.stringify(then)}); if (!c) return 'нет ' + ${JSON.stringify(then)}; c.click();` : ""} return ''; })()`;
const MVIEWS = [
  // «Люди» (UI-B 23.10, эталон Т1–Т4): строка — лист человека с линией; «График» — лист графика; устройство — лист
  // устройства (из листа человека с устройствами); «⋯» строки — меню
  ["more", "more"], ["people", "people-detail", MCLICK(".kk-plist .kk-prow__main")],
  ["people", "people-graph", `(async () => { const r = [...document.querySelectorAll(".kk-plist .kk-prow")].find((x) => x.querySelector(".kk-on2__d .gx-dot")); if (!r) return "нет пользователя с устройствами"; r.querySelector(".kk-prow__main").click(); await new Promise((ok) => setTimeout(ok, 900)); const b = document.querySelector(".gx-bsheet--act .kk-msp__btn .ga-btn"); if (!b) return "нет «График»"; b.click(); return ""; })()`],
  // А5: «График» листом — и «7 дней» (восемь подписей оси), и «30 дней»
  ["people", "people-graph7", `(async () => { const r = [...document.querySelectorAll(".kk-plist .kk-prow")].find((x) => x.querySelector(".kk-on2__d .gx-dot")); if (!r) return "нет пользователя с устройствами"; r.querySelector(".kk-prow__main").click(); await new Promise((ok) => setTimeout(ok, 900)); const b = document.querySelector(".gx-bsheet--act .kk-msp__btn .ga-btn"); if (!b) return "нет «График»"; b.click(); await new Promise((ok) => setTimeout(ok, 900)); const g = document.querySelectorAll(".gx-bsheet--act .kk-gr__bar .gx-seg button")[1]; if (!g) return "нет переключателя периода"; g.click(); return ""; })()`],
  ["people", "people-graph30", `(async () => { const r = [...document.querySelectorAll(".kk-plist .kk-prow")].find((x) => x.querySelector(".kk-on2__d .gx-dot")); if (!r) return "нет пользователя с устройствами"; r.querySelector(".kk-prow__main").click(); await new Promise((ok) => setTimeout(ok, 900)); const b = document.querySelector(".gx-bsheet--act .kk-msp__btn .ga-btn"); if (!b) return "нет «График»"; b.click(); await new Promise((ok) => setTimeout(ok, 900)); const g = document.querySelectorAll(".gx-bsheet--act .kk-gr__bar .gx-seg button")[2]; if (!g) return "нет переключателя периода"; g.click(); return ""; })()`],
  ["people", "people-device", `(async () => { const r = [...document.querySelectorAll(".kk-plist .kk-prow")].find((x) => x.querySelector(".kk-on2__d .gx-dot")); if (!r) return "нет пользователя с устройствами"; r.querySelector(".kk-prow__main").click(); await new Promise((ok) => setTimeout(ok, 900)); const d = document.querySelector(".gx-bsheet--act .gx-mrow[data-hold-row^='dev:']"); if (!d) return "нет устройства"; d.click(); return ""; })()`],
  ["people", "people-more", MCLICK(".kk-plist > .kk-pg:nth-child(2) .kk-prow__more")],
  ["people", "people-qr", MCLICK(".kk-plist > .kk-pg:nth-child(2) .kk-prow__qr")],
  // «Протоколы» (Т3, Б r3 Т2): тот же вид, что на компьютере — строка раскрывается на месте (VLESS — самая длинная),
  // вопрос «Удалить протокол?» в строке Hysteria2, окно установки с выбором порта (номер — строкой, Т3)
  ["inbounds", "inbounds-open", MCLICK("section[data-page='inbounds'] .kb2-pl__g[data-kind='vless'] > .kb2-pl__r")],
  ["inbounds", "inbounds-del", MCLICK("section[data-page='inbounds'] .kb2-pl__g[data-kind='hy2'] > .kb2-pl__r", "section[data-page='inbounds'] .kb2-pl__g.is-open .ux-pc__del")],
  ["inbounds", "inbounds-install", MCLICK("section[data-page='inbounds'] .kk-irow:not(.is-na) button")], ["inbounds", "inbounds-add", MCLICK("#head .ga-btn--primary")],
  ["exits", "exits-detail", MCLICK("button.gx-mrow[data-hold-row^='exit:']")], ["exits?tab=keys", "exits-sub", MCLICK("button.gx-mrow[data-hold-row^='sub:']")],
  // nav2 r1: выбор части — кнопкой над ней, лист частей снизу («Выходы», фильтр «Людей»)
  ["exits", "exits-parts", MCLICK("section[data-page='exits'] .gx-partpick")], ["people", "people-show", MCLICK("section[data-page='people'] .gx-partpick")],
  ["exits", "exits-form", MCLICK("#head .ga-btn--primary")], ["forwards", "forwards-row", MCLICK("button.gx-mrow")],
  ["scheme", "scheme-graph", MCLICK("button.gx-mrow")],
  ["more", "look-sheet", MCLICK("[data-look-open]")],                    // «Внешний вид» — лист снизу (3.3.6, BACKLOG п. 4)
  ...["look", "access", "gateway", "addresses", "bot", "site", "github", "backup", "about"].map((x) => ["settings", "settings?tab=" + x]),
  // 3.3.7.15: «Адреса сервера» — адрес во весь экран (М3); «Разложить заново» — подтверждение листом снизу (Р2т:
  // российская группа «Дом», rug=tier; зарубежных «Групп» на телефоне нет)
  ["settings?tab=addresses", "addresses-detail", MCLICK(".kk-adr-ph button.gx-mrow")],
  ["exits?tab=ru", "rg-sheet", `(async () => { const g = document.querySelector(".kk-eg-tog[aria-expanded='false']"); if (g) { g.click(); await new Promise((r) => setTimeout(r, 900)); } const b = document.querySelector("[data-rg]:not([aria-disabled='true'])"); if (!b) return "нет [data-rg]"; b.click(); return ""; })()`, "rug=tier"],
  ["overview", "login", "", "login=1"],
  // «Обзор › Проверка сервера» (бывшая «Диагностика», B15, 6C; nav2 r1 — часть выбирается адресом, вкладок модуля нет):
  // деталь проверки, лист записи расписания (барабан), новая запись с ошибкой сервера; «Сейчас» — окно цены «Проверить
  // сервер» и подсказка дня полосы (UI-C: нажатие закрепляет её над рядом дней во всю ширину)
  ["overview?tab=history", "diag-detail", MCLICK(".gx-hrow")], ["overview?tab=schedule", "diag-sched", MCLICK(".gx-sched__open")],
  ["overview?tab=schedule", "diag-new", MCLICK(".gx-sched__head .ga-btn")],
  ["overview?tab=now", "diag-cost", MCLICK(".gx-chk__btns .ga-btn")], ["overview?tab=now", "diag-day", MCLICK(".gx-days__row .gx-day:nth-last-child(2)")],
].filter((v) => PAGES.includes(v[0]));
const MWIDTHS = [...new Set([...WIDTHS.filter((x) => x < 700), ...(WIDTHS.some((x) => x < 700) ? [NARROW] : [])])];
for (const lang of LANGS) for (const theme of THEMES) for (const [page, name, act, qs] of MVIEWS) for (const w of MWIDTHS) {
  if (!mine()) continue;
  const hash = name.indexOf("?") > 0 ? name : page;
  await view(w);
  await cdp("Page.navigate", { url: `${BASE}?mock=1&lang=${lang}${qs ? "&" + qs : ""}#${hash}` }); await sleep(300);
  // раскрытые строки «Протоколов» панель помнит — каждый вид начинается со свёрнутых (нажатие раскрывает, а не сворачивает)
  await js(`${FONT_LS}localStorage.setItem("kaskad-appearance-v1", JSON.stringify({ v: 3, theme: "${theme}" })); localStorage.removeItem("kaskad-ib-open"); true`);
  await cdp("Page.reload"); await sleep(600);
  await until(qs === "login=1" ? `!!document.querySelector('.kk-login')` : `(() => { const p = document.querySelector('.gx-page:not([hidden])'); return !!p && p.children.length > 0 && !document.querySelector('#progress:not([hidden])') && !document.querySelector('.gx-skel'); })()`, 8000);
  const miss = act ? await js(act) : "";
  if (miss) { bad++; console.log(`FAIL m:${name} ${w} ${theme} ${lang}: view | ${miss}`); continue; }
  await sleep(act ? 900 : 300);
  if (!act) await fit(w);
  await sleep(200);
  const found = await js(CHECK); runs++;
  await note(page);
  for (const [kind, where, what] of found) { bad++; console.log(`FAIL m:${name} ${w} ${theme} ${lang}: ${kind} | ${where} | ${what}`); }
  jsFail(`m:${name} ${w} ${theme} ${lang}`);
}
// Поиск по панели (3.3.6): окно поиска на 390 и 1440 из набора ширин — пустой запрос («Недавние» и разделы), найдено,
// ничего не нашлось. K3_PAGES — только с «find» в списке
// «График» активности окном (А5, 27.10; эталон Г1–Г3): ширины от 700 из набора, человек с устройствами — «⋯» строки →
// «График активности», затем «Сутки · 7 дней · 30 дней». Строка — таблицы (от 1100) или списка планшета (768–1024,
// .kk-plist: у человека с устройствами есть стрелка .kk-prow__exp). K3_PAGES — только с «people» в списке
const GWIDTHS = WIDTHS.filter((x) => x >= 700);
const GOPEN = (i) => `(async () => { const w = (ms) => new Promise((ok) => setTimeout(ok, ms));
  const g = [...document.querySelectorAll(".gx-xt__g")].find((x) => x.querySelectorAll(".gx-xt__r").length > 2);
  const p = g ? null : [...document.querySelectorAll(".kk-plist .kk-pg")].find((x) => x.querySelector(".kk-prow__exp"));
  if (!g && !p) return "нет пользователя с устройствами";
  const m = g ? g.querySelector(".gx-xt__r .gx-menu-btn") : p.querySelector(".kk-prow > .kk-prow__more"); if (!m) return "нет «⋯»"; m.click(); await w(300);
  const it = [...document.querySelectorAll("[role='menuitem']")].find((x) => /График активности|Activity chart/.test(x.textContent)); if (!it) return "нет «График активности» в «⋯»"; it.click(); await w(900);
  const b = document.querySelectorAll(".gx-dlg--act .kk-gr__bar .gx-seg button")[${i}]; if (!b) return "нет окна «График»"; b.click(); await w(900); return ""; })()`;
if (!process.env.K3_PAGES || PAGES.includes("people")) for (const lang of LANGS) for (const theme of THEMES) for (const w of GWIDTHS) for (const [i, rng] of [[0, "1d"], [1, "7d"], [2, "30d"]]) {
  if (!mine()) continue;
  await view(w);
  await cdp("Page.navigate", { url: `${BASE}?mock=1&lang=${lang}#people` }); await sleep(300);
  await js(`${FONT_LS}localStorage.setItem("kaskad-appearance-v1", JSON.stringify({ v: 3, theme: "${theme}" })); true`);
  await cdp("Page.reload"); await sleep(600);
  await until(`!!document.querySelector('.gx-xt__g, .kk-plist .kk-pg') && !document.querySelector('.gx-skel')`, 8000);
  const miss = await js(GOPEN(i));
  if (miss) { bad++; console.log(`FAIL graph=${rng} ${w} ${theme} ${lang}: view | ${miss}`); continue; }
  const found = await js(CHECK); runs++;
  await note("people");
  for (const [kind, where, what] of found) { bad++; console.log(`FAIL graph=${rng} ${w} ${theme} ${lang}: ${kind} | ${where} | ${what}`); }
  jsFail(`graph=${rng} ${w} ${theme} ${lang}`);
}
// «Настройки протокола» и «Сеть шлюза» (3.3.7.15 V1, №13 r2): окна на всех ширинах набора, обе темы, RU и EN —
// [awg2] (порт и MTU, сменён порт: «Что изменится» заполнено), [vless] (порт; две кнопки «Применить»), [wg+yes]
// (подтверждение поверх окна), [net] («Сеть шлюза» из «Настройки › Шлюз»), полоса ожидания отката ?ps=pend,wait,last на
// «Обзоре», «Протоколах» и «Пользователях». Сверх CHECK — psgrid (таблица «Ширина → сетка» HANDOFF-n13 r2): окно
// min(1200, ширина − 32), на телефоне — лист во весь экран; тело ≥ 988 — «Порт» и «MTU» рядом, «Что изменится» справа
// 320; 760–987 — блоки друг под другом, «Что изменится» справа 320; < 760 — одна колонка, «Что изменится» под блоками;
// на телефоне подвал столбиком: кнопки во всю ширину, акцентная — первой, «Отмена» — последней из кнопок; акцентная
// кнопка в подвале одна; pend — полоса ожидания по краям содержимого раздела (кадр Н8). K3_PAGES=pset — только они.
const PSGRID = `(() => { const out = [], R = (e) => e.getBoundingClientRect(), px = (x) => Math.round(x);
  const box = document.querySelector('.gx-dlg.gx-dlg--pset'); if (!box) return [['psgrid', '.gx-dlg--pset', 'нет окна']];
  const ps = box.querySelector('.gx-pset'), main = ps && ps.querySelector(':scope > .gx-pset__main'), chg = ps && ps.querySelector(':scope > .gx-chg');
  if (!main || !chg) return [['psgrid', '.gx-pset', 'нет блоков или «Что изменится»']];
  const phone = document.documentElement.dataset.device === 'phone', W = document.documentElement.clientWidth, b = R(box);
  if (phone) { if (Math.abs(b.width - W) > 1) out.push(['psgrid', '.gx-dlg--pset', 'телефон: окно ' + px(b.width) + ' px, не во всю ширину ' + W]); }
  else { const lim = Math.min(1200, W - 32); if (Math.abs(b.width - lim) > 1) out.push(['psgrid', '.gx-dlg--pset', 'окно ' + px(b.width) + ' px, а min(1200, ' + W + ' − 32) = ' + lim]); }
  const B = ps.clientWidth, m = R(main), c = R(chg), half = [...main.querySelectorAll(':scope > .gx-pblk--half')].map(R);
  if (B >= 760) { if (Math.abs(c.width - 320) > 1 || c.left < m.right - 1 || Math.abs(c.top - m.top) > 12) out.push(['psgrid', '.gx-chg', 'тело ' + B + ' px: «Что изменится» не справа шириной 320 (' + px(c.width) + ' px, от блоков ' + px(c.left - m.right) + ', сверху ' + px(c.top - m.top) + ')']); }
  else if (c.top < m.bottom - 1 || Math.abs(c.width - B) > 1) out.push(['psgrid', '.gx-chg', 'тело ' + B + ' px (< 760): «Что изменится» не под блоками во всю ширину (' + px(c.width) + ' px)']);
  if (half.length === 2) { const [p, q] = half;
    if (B >= 988) { if (Math.abs(p.top - q.top) > 1 || q.left < p.right - 1) out.push(['psgrid', '.gx-pblk--half', 'тело ' + B + ' px (≥ 988): «Порт» и «MTU» не рядом']); }
    else if (q.top < p.bottom - 1 || Math.abs(p.width - q.width) > 1) out.push(['psgrid', '.gx-pblk--half', 'тело ' + B + ' px (< 988): «Порт» и «MTU» не друг под другом во всю ширину']); }
  const f = box.querySelector(':scope > .gx-dlg__foot'), bs = f ? [...f.querySelectorAll(':scope > .ga-btn')] : [];
  const acc = bs.filter((x) => x.classList.contains('ga-btn--primary'));
  if (acc.length !== 1) out.push(['psgrid', '.gx-dlg__foot', 'акцентных кнопок в подвале: ' + acc.length]);
  if (phone && f && acc.length === 1) { const cs = getComputedStyle(f), fw = f.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
    const narrow = bs.filter((x) => Math.abs(R(x).width - fw) > 1); if (narrow.length) out.push(['psgrid', '.gx-dlg__foot', 'телефон: кнопка «' + narrow[0].textContent.trim() + '» ' + px(R(narrow[0]).width) + ' px, не во всю ширину ' + px(fw)]);
    const top = Math.min(...bs.map((x) => R(x).top)), cancel = f.querySelector(':scope > .gx-pset__cancel');
    if (R(acc[0]).top > top + 1) out.push(['psgrid', '.gx-dlg__foot', 'телефон: акцентная кнопка не первой']);
    if (cancel && R(cancel).top < Math.max(...bs.map((x) => R(x).top)) - 1) out.push(['psgrid', '.gx-dlg__foot', 'телефон: «Отмена» не последней']); }
  // 3.3.7.15 Д1 (сверка Claude Design Ш3, Ш4): отметка полосы сети шлюза — не ближе 6 px к краю сегмента (иначе не видна);
  // подпись сегмента — внутри сегмента (на 390 «192.168.0.0/16» срезалась слева: overflow влево scrollWidth не видит)
  for (const sg of box.querySelectorAll('.gx-gwr__s')) { const r = R(sg);
    for (const mk of sg.querySelectorAll('.gx-gwr__mk')) { const k = R(mk); if (k.left < r.left + 6 - 0.5 || k.right > r.right - 6 + 0.5) out.push(['psgrid', '.gx-gwr__mk', 'отметка у края сегмента «' + (sg.title || sg.textContent).trim() + '»: ' + px(k.left - r.left) + ' / ' + px(r.right - k.right) + ' px']); }
    const tw = document.createTreeWalker(sg, NodeFilter.SHOW_TEXT), tr = [];
    for (let n; (n = tw.nextNode());) { const rg = document.createRange(); rg.selectNodeContents(n); tr.push(...[...rg.getClientRects()].filter((x) => x.width > 0.5)); }
    if (tr.some((x) => x.left < r.left - 0.5 || x.right > r.right + 0.5)) out.push(['psgrid', '.gx-gwr__s', 'подпись сегмента срезана краем: «' + sg.textContent.trim() + '»']); }
  return out; })()`;
const PEND_CHECK = `(() => { const bar = document.querySelector('#pendHost > .gx-pend'); if (!bar) return [['pend', '#pendHost', 'нет полосы ожидания']];
  const pg = document.querySelector('#main > .gx-page:not([hidden])'); if (!pg) return [];
  const a = bar.getBoundingClientRect(), r = pg.getBoundingClientRect(), cs = getComputedStyle(pg);
  const k = { left: r.left + parseFloat(cs.paddingLeft), right: r.right - parseFloat(cs.paddingRight) };
  return Math.abs(a.left - k.left) > 1 || Math.abs(a.right - k.right) > 1 ? [['pend', '.gx-pend', 'края ' + Math.round(a.left) + '…' + Math.round(a.right) + ', у содержимого раздела ' + Math.round(k.left) + '…' + Math.round(k.right)]] : []; })()`;
// 3.3.7.15 Д1 (П1): метка «ждёт настроек» у имени — слова целиком или значок ключа (is-icon), не «жд…»; имя не срезано, пока
// метка не сжата до значка (было «Ма…» рядом с метками на 1440 «Средне»)
const WAIT_CHECK = `(() => { const out = [];
  for (const c of document.querySelectorAll('.gx-xt__nl > .gx-wait')) { if (!c.getClientRects().length) continue;
    const icon = c.classList.contains('is-icon') || c.getBoundingClientRect().width <= 26.5, cut = c.scrollWidth > c.clientWidth + 1;
    if (cut && !c.classList.contains('is-icon')) out.push(['pswait', '.gx-wait', 'слова метки срезаны: ' + Math.round(c.clientWidth) + ' из ' + c.scrollWidth + ' px']);
    const n = c.parentElement.querySelector(':scope > .gx-xt__n'); if (n && !icon && n.scrollWidth > n.clientWidth + 1) out.push(['pswait', '.gx-xt__n', 'имя «' + n.textContent + '» срезано рядом с меткой']); }
  // П2 r2 (телефон): у пользователя с ключом слово маршрута не срезано, ключ — в круге
  for (const k of document.querySelectorAll('.kk-prow__t > .gx-waiti')) { if (!k.getClientRects().length) continue;
    const r = k.getBoundingClientRect(); if (getComputedStyle(k).borderRadius !== '50%' || Math.abs(r.width - r.height) > 0.5) out.push(['pswait', '.gx-waiti', 'ключ у пользователя не в круге']);
    const ch = k.parentElement.querySelector(':scope > .gx-chip'); if (ch && ch.scrollWidth > ch.clientWidth + 1) out.push(['pswait', '.kk-prow__t > .gx-chip', 'слово маршрута срезано рядом с ключом: ' + Math.round(ch.clientWidth) + ' из ' + ch.scrollWidth + ' px']); }
  // П1 r2: «Активность · сутки» целиком, когда влезает; «За сутки» — только когда целиком не влезает
  for (const h of document.querySelectorAll('.gx-xt--k1.kk-pp-wait .gx-xt__hc:not(.is-cc)')) { const f = h.querySelector(':scope > .gx-xt__hl.kk-nrow'), sh = h.querySelector(':scope > .gx-xt__hl.kk-nmd');
    if (!f || !sh || !h.getClientRects().length) continue;
    if (f.getClientRects().length) { if (f.scrollWidth > f.clientWidth + 1) out.push(['pswait', '.gx-xt__hc', 'подпись «' + f.textContent + '» срезана']); continue; }
    if (!sh.getClientRects().length) continue;
    f.style.setProperty('display', 'block'); sh.style.setProperty('display', 'none'); const cut = f.scrollWidth > f.clientWidth + 1; f.style.removeProperty('display'); sh.style.removeProperty('display');
    if (!cut) out.push(['pswait', '.gx-xt__hc', '«' + sh.textContent + '» вместо «' + f.textContent + '», хотя она влезает']); }
  return out; })()`;
const PSWAIT = (sel, ms = 5000) => `(async () => { for (let k = 0; k < ${ms / 100} && !document.querySelector(${JSON.stringify(sel)}); k++) await new Promise((r) => setTimeout(r, 100)); return !!document.querySelector(${JSON.stringify(sel)}); })()`;
// открыть «Настройки» протокола из его раскрытой строки; rnd — выбрать «Случайный» (план сервера в «Что изменится»)
const PSOPEN = (kind, rnd) => `(async () => { const w = (ms) => new Promise((ok) => setTimeout(ok, ms));
  const g = document.querySelector('.kb2-pl__g[data-kind="${kind}"]'); if (!g) return 'нет строки ${kind}';
  if (!g.classList.contains('is-open')) g.querySelector('.kb2-pl__r').click(); await w(300);
  const b = [...g.querySelectorAll('button')].find((x) => /^(Настройки|Settings)$/.test(x.textContent.trim())); if (!b) return 'нет «Настройки» в строке ${kind}';
  b.click(); for (let k = 0; k < 50 && !document.querySelector('.gx-dlg--pset .gx-pset'); k++) await w(100);
  if (!document.querySelector('.gx-dlg--pset .gx-pset')) return 'окно «Настройки» не открылось';
  ${rnd ? `const r = document.querySelector('.gx-dlg--pset input.kb2-pp__rad[value="rnd"]'); if (!r) return 'нет «Случайный»'; r.click();
  for (let k = 0; k < 50 && !document.querySelector('.gx-dlg--pset .gx-chg .gx-impact__i'); k++) await w(100);
  if (!document.querySelector('.gx-dlg--pset .gx-chg .gx-impact__i')) return 'нет «Что изменится» после смены порта';` : ""}
  return ''; })()`;
const PSVIEWS = [["awg2", "inbounds", PSOPEN("awg2", true)], ["vless", "inbounds", PSOPEN("vless", true)],
  ["wg+yes", "inbounds", `(async () => { const m = await ${PSOPEN("wg", true)}; if (m) return m;
    const a = document.querySelector('.gx-dlg--pset .gx-dlg__foot > .ga-btn--primary'); if (!a) return 'нет «Применить»'; a.click();
    for (let k = 0; k < 50 && !document.querySelector('.gx-dlg--stack[role="alertdialog"]'); k++) await new Promise((r) => setTimeout(r, 100));
    return document.querySelector('.gx-dlg--stack[role="alertdialog"]') ? '' : 'нет подтверждения'; })()`],
  ["net", "settings?tab=gateway", `(async () => { const b = document.querySelector('[data-find="settings.gwNet"]'); if (!b) return 'нет «Сменить» у «Сети шлюза»'; b.click();
    return (await ${PSWAIT(".gx-dlg--pset .gx-gwr")}) ? '' : 'окно «Сеть шлюза» не открылось'; })()`]];
// 3.3.7.15 Д1 (Н10): подтверждение, когда администратор подключён через WireGuard (мок ?ps=hit) — «Сначала скачайте…» над
// кнопками подвала (на телефоне подвал column-reverse ставил её под ними)
PSVIEWS.push(["wg+hit", "inbounds", PSVIEWS[2][2], "ps=hit"]);
const NEED_CHECK = `(() => { const d = document.querySelector('.gx-dlg--stack[role="alertdialog"]'); if (!d) return [['psneed', '.gx-dlg--stack', 'нет подтверждения']];
  const h = d.querySelector('.gx-dlg__foot > .gx-pset__need'); if (!h || h.hidden || !h.getClientRects().length) return [['psneed', '.gx-pset__need', 'нет подсказки «Сначала скачайте…»']];
  // над кнопками или слева в их ряду (кадр Н7: «слева в ряду», 560 — строкой выше); не ниже верхней кнопки
  const bs = [...d.querySelectorAll('.gx-dlg__foot > .ga-btn')].filter((b) => b.getClientRects().length), low = Math.min(...bs.map((b) => b.getBoundingClientRect().bottom));
  return h.getBoundingClientRect().top >= low - 1 ? [['psneed', '.gx-pset__need', 'подсказка под кнопками (верх ' + Math.round(h.getBoundingClientRect().top) + ', низ верхней кнопки ' + Math.round(low) + ')']] : []; })()`;
if (!process.env.K3_PAGES || PS_ONLY || PAGES.includes("pset") || PAGES.includes("inbounds")) for (const lang of LANGS) for (const theme of THEMES) {
  for (const [name, page, act, q] of PSVIEWS) for (const w of WIDTHS) {
    if (!mine()) continue;
    await view(w, 1000);
    await cdp("Page.navigate", { url: `${BASE}?mock=1&lang=${lang}${q ? "&" + q : ""}#${page}` }); await sleep(300);
    await js(`${FONT_LS}localStorage.setItem("kaskad-appearance-v1", JSON.stringify({ v: 3, theme: "${theme}" })); localStorage.removeItem("kaskad-ib-open"); true`);
    await cdp("Page.reload"); await sleep(600);
    await until(`(() => { const p = document.querySelector('.gx-page:not([hidden])'); return !!p && p.children.length > 0 && !document.querySelector('#progress:not([hidden])') && !document.querySelector('.gx-skel'); })()`, 8000);
    const miss = await js(act);
    if (miss) { bad++; console.log(`FAIL pset:${name} ${w} ${theme} ${lang}: view | ${miss}`); continue; }
    await sleep(500);
    const found = [...(await js(CHECK)), ...(name.startsWith("wg+") ? [] : await js(PSGRID)), ...(name === "wg+hit" ? await js(NEED_CHECK) : [])]; runs++;
    await note("pset");
    for (const [kind, where, what] of found) { bad++; console.log(`FAIL pset:${name} ${w} ${theme} ${lang}: ${kind} | ${where} | ${what}`); }
    jsFail(`pset:${name} ${w} ${theme} ${lang}`);
  }
  for (const page of ["overview", "inbounds", "people"]) for (const w of WIDTHS) {
    if (!mine()) continue;
    await view(w);
    await cdp("Page.navigate", { url: `${BASE}?mock=1&lang=${lang}&ps=pend,wait,last#${page}` }); await sleep(300);
    await js(`${FONT_LS}localStorage.setItem("kaskad-appearance-v1", JSON.stringify({ v: 3, theme: "${theme}" })); localStorage.removeItem("kaskad-ib-open"); true`);
    await cdp("Page.reload"); await sleep(600);
    await until(`(() => { const p = document.querySelector('.gx-page:not([hidden])'); return !!p && p.children.length > 0 && !document.querySelector('#progress:not([hidden])') && !document.querySelector('.gx-skel') && !!document.querySelector('#pendHost > .gx-pend'); })()`, 8000);
    await fit(w); await sleep(300);
    // «Пользователи» с подменю (компьютер): пункт «Ждут настроек · N» появляется, когда пришли пометки (subnavSync, 3.3.7.15
    // V1); планшет — переключатель фильтров, где «Ждут настроек» (как «Без выхода») — только выбранным, с «Обзора»
    const ppw = page === "people" && (await js(`(() => { const n = document.querySelector('nav[data-subnav="people"]'); return !!n && !n.querySelector('[data-part="wait"]'); })()`)) ? [["pend", 'nav[data-subnav="people"]', "нет пункта «Ждут настроек»"]] : [];
    const found = [...(await js(CHECK)), ...(await js(PEND_CHECK)), ...ppw, ...(page === "people" ? await js(WAIT_CHECK) : [])]; runs++;
    await note("pset");
    for (const [kind, where, what] of found) { bad++; console.log(`FAIL ${page}&ps=pend ${w} ${theme} ${lang}: ${kind} | ${where} | ${what}`); }
    jsFail(`${page}&ps=pend ${w} ${theme} ${lang}`);
  }
}
// 3.3.7.15 Д1 (сверка Claude Design, вопрос V2): подменю «Настроек» на 1440×900 влезает целиком — низ подменю не ниже низа
// области раздела (было +4 px в EN: «этап 3» второй строкой и зазор 16 между группами)
if (!process.env.K3_PAGES || PAGES.includes("settings")) for (const lang of LANGS) {
  if (!mine()) continue;
  await view(1440, 900);
  await cdp("Page.navigate", { url: `${BASE}?mock=1&lang=${lang}#settings` }); await sleep(300);
  await js(`${FONT_LS}localStorage.setItem("kaskad-appearance-v1", JSON.stringify({ v: 3, theme: "light" })); true`);
  await cdp("Page.reload"); await sleep(600);
  await until(`!!document.querySelector('nav.gx-subnav[data-subnav="settings"] .gx-subnav__i')`, 8000);
  const r = await js(`(() => { const n = document.querySelector('nav.gx-subnav[data-subnav="settings"]'), m = document.querySelector('.gx-main'); if (!n || !m) return 'нет подменю';
    const b = n.getBoundingClientRect().bottom, mb = Math.min(innerHeight, m.getBoundingClientRect().bottom - parseFloat(getComputedStyle(m).paddingBottom || 0)); return b > mb + 0.5 ? 'низ подменю ' + Math.round(b) + ' ниже области раздела ' + Math.round(mb) : ''; })()`); runs++;
  await note("settings");
  if (r) { bad++; console.log(`FAIL settings 1440×900 light ${lang}: subfit | nav.gx-subnav | ${r}`); }
  jsFail(`settings 1440×900 light ${lang}`);
}
// 3.3.7.15 Д1 (REV-3315-view С-1, З-1…З-4): поведение окон на моке — 1440, светлая, RU и EN. netundo — «Вернуть прежнюю
// сеть» только при прошлой смене (ps=netlast); nettoast — итог смены сети с «"Вернуть прежнюю сеть" … не будет» тоном
// «внимание», без дубля строк плана (ps=netnolast); replan — 409 need=yes с новым планом: окно остаётся, план показан, вторая
// запись только по второму нажатию (ps=netreplan); pendkeep — полоса ожидания не пересобирается опросом, фокус остаётся на
// «Подтвердить» (ps=pend, 6,5 с); sugbase — Ш5 на «Выходах»: пакет с ключом в сети шлюза — итог с кнопкой смены сети,
// один такой ключ, «Добавить» раньше предпросмотра — кнопка у ошибки карточки; sugnet (З-1, Н-2 REV-3315-fin) — ключ
// помечен уже предпросмотром (как ядро): у предупреждения та же кнопка → «Применить»: предупреждения и кнопки у поля нет,
// «Добавить» доступна и тот же ключ добавляется (было: ошибка оставалась, второе нажатие звало к «Вернуть прежнюю сеть»);
// sugdlg (Н-2) — то же в окне «Добавить ключ»: после смены сети окно перепроверяет ключ, второй кнопки нет;
// adr* (3.3.7.15 П2, обрыв связи на перезапуске xray; сцены dev-mock-adr.js): «Напрямую» в «Адресах» — ответ записи
// оборвался (drop) или пуст (empty) — не ошибка, через ~2 с перечитано: выбор как на сервере и пояснение; оборвалось
// следующее чтение (dropget) — повтор, без полосы ошибки; ответ с xray_after: true (after, ядро П3) — чтение через ~2 с,
// его warnings — одной полосой на странице, без повтора и без тоста; связь не вернулась (down) — обычная ошибка, выбор прежний; pendbusy (П-3 REV-3315-fin) — ждёт подтверждения другой протокол (ps=pend): после 409 pending_busy «Применить»
// окна протокола доступна без правки поля и повторное нажатие отправляет запрос
const BEH_LOAD = async (lang, q, page) => {
  await view(1440, 900);
  await cdp("Page.navigate", { url: `${BASE}?mock=1&lang=${lang}${q ? "&" + q : ""}#${page}` }); await sleep(300);
  await js(`${FONT_LS}localStorage.setItem("kaskad-appearance-v1", JSON.stringify({ v: 3, theme: "light" })); localStorage.removeItem("kaskad-ib-open"); true`);
  await cdp("Page.reload"); await sleep(600);
  await until(`(() => { const p = document.querySelector('.gx-page:not([hidden])'); return !!p && p.children.length > 0 && !document.querySelector('#progress:not([hidden])') && !document.querySelector('.gx-skel'); })()`, 8000);
};
const BEH_W = `const w = (ms) => new Promise((ok) => setTimeout(ok, ms)); const W = async (f, n = 60) => { for (let k = 0; k < n && !f(); k++) await w(100); return f(); };`;
// окно «Сеть шлюза»; x — вписать второе число 10.x (план сервера в «Что изменится»)
const BEH_NET = (x) => `${BEH_W} const b = document.querySelector('[data-find="settings.gwNet"]'); if (!b) return 'нет «Сменить» у «Сети шлюза»'; b.click();
  if (!(await W(() => document.querySelector('.gx-dlg--pset .gx-gwr')))) return 'окно «Сеть шлюза» не открылось';
  ${x ? `const i = document.querySelector('.gx-dlg--pset .gx-gwr__oct input'); if (!i) return 'нет поля 10.x'; i.value = "${x}"; i.dispatchEvent(new Event("input", { bubbles: true }));
  if (!(await W(() => document.querySelector('.gx-dlg--pset .gx-chg .gx-impact__i')))) return 'нет «Что изменится» после смены сети';` : ""}`;
const BEH_APPLY = `{ const a = document.querySelector('.gx-dlg--pset .gx-dlg__foot > .ga-btn--primary'); if (!a || a.disabled) return 'нет доступной «Применить»'; a.click(); }`;
const BEH = [
  ["netundo", "ps=netlast", "settings?tab=gateway", `${BEH_NET("")} const u = [...document.querySelectorAll('.gx-dlg--pset .gx-dlg__foot .ga-btn')].some((x) => /^(Вернуть прежнюю сеть|Restore the previous network)/.test(x.textContent.trim()));
    return u ? '' : 'нет «Вернуть прежнюю сеть» при прошлой смене';`],
  ["netundo-", "", "settings?tab=gateway", `${BEH_NET("")} const u = [...document.querySelectorAll('.gx-dlg--pset .gx-dlg__foot .ga-btn')].some((x) => /^(Вернуть прежнюю сеть|Restore the previous network)/.test(x.textContent.trim()));
    return u ? '«Вернуть прежнюю сеть» без прошлой смены' : '';`],
  ["nettoast", "ps=netnolast", "settings?tab=gateway", `${BEH_NET("0")}
    // чужой тост до «Применить» (успех от живого обновления мока) — не итог смены сети (R-3315-p2: разовый FAIL «тон тоста»)
    const th = document.querySelector('#toastHost'); if (th) th.replaceChildren();
    const net = () => [...document.querySelectorAll('#toastHost .gx-toast')].find((x) => /(Сеть шлюза сменена|The gateway network changed)/.test(x.textContent));
    ${BEH_APPLY}
    if (!(await W(net))) return 'нет тоста итога: ' + ((document.querySelector('#toastHost .gx-toast') || {}).textContent || '').slice(0, 120);
    const tt = net(), s = tt.textContent;
    if (!tt.querySelector('.gx-toast__ic--warning')) return 'тон тоста не «внимание»: ' + tt.querySelector('.gx-toast__ic').className;
    if (!/(Вернуть прежнюю сеть» для этой смены не будет|Restore the previous network” won’t be there)/.test(s)) return 'в тосте нет строки «последнее применение»: ' + s.slice(0, 160);
    return /частыми домашними|домашними сетями|home networks/.test(s) ? 'в тосте дубль строки плана' : '';`],
  ["replan", "ps=netreplan", "settings?tab=gateway", `${BEH_NET("79")} ${BEH_APPLY}
    if (!(await W(() => { const e = document.querySelector('.gx-dlg--pset .gx-pset__err'); return e && !e.hidden; }, 40))) return 'после 409 need=yes нет подсказки о новом плане (окно ' + (document.querySelector('.gx-dlg--pset') ? 'открыто' : 'закрыто') + ')';
    if (!/(Пока окно было открыто|While the window was open)/.test(document.querySelector('.gx-dlg--pset .gx-pset__err').textContent)) return 'не та подсказка: ' + document.querySelector('.gx-dlg--pset .gx-pset__err').textContent.slice(0, 120);
    if (!document.querySelector('.gx-dlg--pset .gx-chg .gx-impact__i--hard')) return 'новый план (устройства) не показан';
    await w(1500); if (!document.querySelector('.gx-dlg--pset')) return 'окно закрылось без второго нажатия';
    ${BEH_APPLY} return (await W(() => !document.querySelector('.gx-dlg--pset'))) ? '' : 'второе нажатие не применило';`],
  ["e11", "ps=front", "inbounds", `${BEH_W} const m = await ${PSOPEN("vless", false)}; if (m) return m;
    const o = document.querySelector('.gx-dlg--pset input.kb2-pp__in'); if (!o) return 'нет поля «Свой порт»'; o.focus(); o.value = '443'; o.dispatchEvent(new Event('input', { bubbles: true }));
    if (!(await W(() => document.querySelector('.gx-dlg--pset .kb2-pp__s.is-warn')))) return 'своё 443 перед сайтом — не предупреждение';
    const s = document.querySelector('.gx-dlg--pset .kb2-pp__s.is-warn'), g = s.querySelector('svg'), tri = window.KaskadIcons.svg('triangle-exclamation', 12);
    if (!g || g.innerHTML !== tri.innerHTML) return 'у предупреждения нет треугольника';
    const ref = document.createElement('span'); ref.style.setProperty('color', 'var(--warning-text)'); s.parentElement.append(ref); const want = getComputedStyle(ref).color; ref.remove();
    return getComputedStyle(s).color === want ? '' : 'тон не «внимание»: ' + getComputedStyle(s).color + ' вместо ' + want;`],
  ["pendkeep", "ps=pend", "inbounds", `${BEH_W} if (!(await W(() => document.querySelector('#pendHost > .gx-pend .ga-btn--primary')))) return 'нет полосы ожидания';
    const b0 = document.querySelector('#pendHost > .gx-pend .ga-btn--primary'); b0.focus(); await w(6500);
    const b1 = document.querySelector('#pendHost > .gx-pend .ga-btn--primary');
    return b1 !== b0 ? 'полоса пересобрана опросом (кнопка «Подтвердить» — новая)' : document.activeElement !== b0 ? 'фокус ушёл с «Подтвердить» на ' + document.activeElement.tagName : '';`],
  ["sugbase", "", "exits?tab=keys", `${BEH_W} const area = document.querySelector('textarea.ux-area--exit'); if (!area) return 'нет поля ключа на «Выходах»';
    const conf = "[Interface]\\nPrivateKey = aGVsbG8gd29ybGQgaGVsbG8gd29ybGQgaGVsbG8gd28=\\nAddress = 10.77.0.9/32\\n\\n[Peer]\\nPublicKey = d29ybGQgaGVsbG8gd29ybGQgaGVsbG8gd29ybGQgaGU=\\nEndpoint = 198.51.100.7:51820\\nAllowedIPs = 0.0.0.0/0\\n";
    const put = async (s) => { area.value = s; area.dispatchEvent(new Event("input", { bubbles: true })); await w(900);
      const ab = area.closest('section.ux-ei').querySelector('.ux-ei__btns > .ux-btn--primary');
      if (!ab || ab.disabled) return false; ab.click(); return true; };
    if (!(await put(conf + "vless://11111111-2222-3333-4444-555555555555@203.0.113.9:443?security=reality#sb-1"))) return 'нет доступной «Добавить» (пакет)';
    if (!(await W(() => document.querySelector('.gx-dlg--stack[role="alertdialog"]')))) return 'пакет с ключом в сети шлюза: нет итога по ключам';
    const d = document.querySelector('.gx-dlg--stack[role="alertdialog"]');
    if (d.querySelectorAll('li').length < 2) return 'в итоге пакета нет строк по ключам';
    const sb = [...d.querySelectorAll('.ga-btn')].find((x) => /(Сменить сеть шлюза на|Change the gateway network to)/.test(x.textContent)); if (!sb) return 'в итоге пакета нет кнопки «Сменить сеть шлюза на …»';
    const di = d.querySelector('.gx-dlg__ic svg'); if (!di || di.innerHTML !== window.KaskadIcons.svg('triangle-exclamation', 20).innerHTML || !d.querySelector('.gx-dlg__ic--warning')) return 'итог пакета с непринятым ключом — не «внимание»';
    [...d.querySelectorAll('.ga-btn')].find((x) => /^(ОК|OK)$/.test(x.textContent.trim())).click(); await w(400);
    // один ключ: «Добавить» раньше ответа предпросмотра (350 мс) — ошибка ядра с кнопкой (предупреждение предпросмотра — sugnet)
    area.value = conf.replace("10.77.0.9", "10.77.0.10"); area.dispatchEvent(new Event("input", { bubbles: true }));
    { const b = area.closest('section.ux-ei').querySelector('.ux-ei__btns > .ux-btn--primary'); if (!b || b.disabled) return 'нет доступной «Добавить» (один ключ)'; b.click(); }
    if (!(await W(() => document.querySelector('.ux-ei .ux-errtx') && document.querySelector('[data-ux-k="err-fix"]')))) return 'один ключ в сети шлюза: у ошибки карточки нет кнопки смены сети';
    const ab = area.closest('section.ux-ei').querySelector('.ux-ei__btns > .ux-btn--primary'); if (!ab.disabled) return '«Добавить» доступна с тем же отклонённым ключом';
    area.value = area.value.replace("10.77.0.10", "10.66.0.10"); area.dispatchEvent(new Event("input", { bubbles: true })); await w(900);
    return ab.disabled ? '«Добавить» не вернулась после правки ключа' : '';`],
  ["sugnet", "", "exits?tab=keys", `${BEH_W} const area = document.querySelector('textarea.ux-area--exit'); if (!area) return 'нет поля ключа на «Выходах»';
    const sec = area.closest('section.ux-ei'), ab = sec.querySelector('.ux-ei__btns > .ux-btn--primary');
    area.value = "[Interface]\\nPrivateKey = aGVsbG8gd29ybGQgaGVsbG8gd29ybGQgaGVsbG8gd28=\\nAddress = 10.77.0.9/32\\n\\n[Peer]\\nPublicKey = d29ybGQgaGVsbG8gd29ybGQgaGVsbG8gd29ybGQgaGU=\\nEndpoint = 198.51.100.7:51820\\nAllowedIPs = 0.0.0.0/0\\n";
    area.dispatchEvent(new Event("input", { bubbles: true }));
    // Н-2: ядро помечает такой ключ уже в предпросмотре — предупреждение с кнопкой, «Добавить» закрыта
    if (!(await W(() => sec.querySelector('.ux-warntx')))) return 'ключ в сети шлюза: нет предупреждения предпросмотра';
    if (!(await W(() => sec.querySelector('[data-ux-k="err-fix"]'), 20))) return 'у предупреждения предпросмотра нет кнопки «Сменить сеть шлюза на …»: ' + sec.querySelector('.ux-warntx').textContent.slice(0, 100);
    if (!ab || !ab.disabled) return '«Добавить» доступна с ключом в сети шлюза';
    sec.querySelector('[data-ux-k="err-fix"]').click();
    if (!(await W(() => document.querySelector('.gx-dlg--pset .gx-gwr')))) return 'окно «Сеть шлюза» не открылось';
    if (!(await W(() => { const a = document.querySelector('.gx-dlg--pset .gx-dlg__foot > .ga-btn--primary'); return a && !a.disabled; }))) return 'нет доступной «Применить» в окне сети';
    ${BEH_APPLY}
    if (!(await W(() => !document.querySelector('.gx-dlg--pset')))) return 'окно сети не закрылось после «Применить»';
    await W(() => !sec.querySelector('[data-ux-k="err-fix"]') && !sec.querySelector('.ux-errtx, .ux-warntx') && !ab.disabled, 40);
    if (sec.querySelector('[data-ux-k="err-fix"]')) return 'сеть сменена, а у поля та же кнопка «' + sec.querySelector('[data-ux-k="err-fix"]').textContent.trim() + '»';
    if (sec.querySelector('.ux-errtx, .ux-warntx')) return 'сеть сменена, а у поля то же предупреждение: ' + sec.querySelector('.ux-errtx, .ux-warntx').textContent.slice(0, 120);
    if (ab.disabled) return 'сеть сменена, а «Добавить» недоступна';
    ab.click();
    if (!(await W(() => area.value === '' && !sec.querySelector('.ux-errtx')))) return 'тот же ключ после смены сети не добавился: ' + ((sec.querySelector('.ux-errtx') || {}).textContent || 'поле не очистилось');
    return '';`],
  ["sugdlg", "", "exits?tab=keys", `${BEH_W} location.hash = '#exits?tab=keys&open=add';       // open= из адреса — один раз, обновление его не повторит
    const dlg = () => [...document.querySelectorAll('.gx-dlg')].find((x) => x.querySelector('textarea') && !x.classList.contains('gx-dlg--pset'));
    if (!(await W(dlg))) return 'окно «Добавить ключ» не открылось';
    const box = dlg(), ta = box.querySelector('textarea'), prim = () => box.querySelector('.gx-dlg__foot > .ga-btn--primary');
    const sug = () => [...box.querySelectorAll('.ga-btn')].find((x) => /(Сменить сеть шлюза на|Change the gateway network to)/.test(x.textContent));
    ta.value = "[Interface]\\nPrivateKey = aGVsbG8gd29ybGQgaGVsbG8gd29ybGQgaGVsbG8gd28=\\nAddress = 10.77.0.9/32\\n\\n[Peer]\\nPublicKey = d29ybGQgaGVsbG8gd29ybGQgaGVsbG8gd29ybGQgaGU=\\nEndpoint = 198.51.100.7:51820\\nAllowedIPs = 0.0.0.0/0\\n";
    ta.dispatchEvent(new Event("input", { bubbles: true })); await w(200);
    if (!prim() || prim().disabled) return 'нет доступной «Проверить»'; prim().click();
    if (!(await W(sug))) return 'предпросмотр ключа в сети шлюза: нет кнопки «Сменить сеть шлюза на …»';
    if (!prim().disabled) return '«Добавить» окна доступна с ключом в сети шлюза';
    sug().click();
    if (!(await W(() => document.querySelector('.gx-dlg--pset .gx-gwr')))) return 'окно «Сеть шлюза» не открылось';
    if (!(await W(() => { const a = document.querySelector('.gx-dlg--pset .gx-dlg__foot > .ga-btn--primary'); return a && !a.disabled; }))) return 'нет доступной «Применить» в окне сети';
    ${BEH_APPLY}
    if (!(await W(() => !document.querySelector('.gx-dlg--pset')))) return 'окно сети не закрылось после «Применить»';
    if (!box.isConnected) return 'окно «Добавить ключ» закрылось вместе с окном сети';
    await W(() => !sug() && prim() && !prim().disabled, 40);
    if (sug()) return 'сеть сменена, а в окне «Добавить ключ» та же кнопка «' + sug().textContent.trim() + '»';
    return prim() && !prim().disabled ? '' : 'сеть сменена, а «Добавить» окна недоступна';`],
  ["pendbusy", "ps=pend", "inbounds", `${BEH_W} const m = await ${PSOPEN("awg2", true)}; if (m) return m;
    const mk = window.KASKAD_MOCK, f0 = mk.fetch; let puts = 0;
    mk.fetch = (u, init) => { if (init && init.method === 'PUT' && /\\/settings(\\?|$)/.test(String(u))) puts++; return f0(u, init); };
    try {
      ${BEH_APPLY}
      const busy = () => { const e = document.querySelector('.gx-dlg--pset .gx-pset__err'); return e && !e.hidden && /(ждёт подтверждения|waits for a confirmation)/.test(e.textContent); };
      for (let k = 0; k < 60 && !busy(); k++) { const y = document.querySelector('.gx-dlg--stack[role="alertdialog"] .gx-dlg__foot > .ga-btn--primary'); if (y && !y.disabled) y.click(); await w(100); }
      if (!busy()) return 'нет ошибки «… ждёт подтверждения» (409 pending_busy) в окне протокола';
      const a = document.querySelector('.gx-dlg--pset .gx-dlg__foot > .ga-btn--primary');
      if (!a || a.disabled) return '«Применить» недоступна после pending_busy без правки поля';
      const n0 = puts; a.click();
      for (let k = 0; k < 40 && puts === n0; k++) { const y = document.querySelector('.gx-dlg--stack[role="alertdialog"] .gx-dlg__foot > .ga-btn--primary'); if (y && !y.disabled) y.click(); await w(100); }
      return puts > n0 ? '' : 'повторное «Применить» после pending_busy не отправило запрос';
    } finally { mk.fetch = f0; }`]];
const ADR_W = `${BEH_W} if (!(await W(() => document.querySelector('[data-adr-sel="direct"]')))) return 'нет выбора «Напрямую» в «Адресах»';
    const s0 = document.querySelector('[data-adr-sel="direct"]'), was = s0.value, want = [...s0.options].map((o) => o.value).find((v) => v && v !== was);
    if (!want) return 'нет другого адреса для «Напрямую»';
    const cur = () => document.querySelector('[data-adr-sel="direct"]'), th = document.querySelector('#toastHost'), seen = [];
    if (th) th.replaceChildren();
    const note = () => th && th.querySelectorAll('.gx-toast').forEach((x) => { const v = (x.querySelector('.gx-toast__ic--danger') ? 'danger: ' : 'ok: ') + x.textContent.trim(); if (seen.indexOf(v) < 0) seen.push(v); });
    const mo = new MutationObserver(note); if (th) mo.observe(th, { childList: true, subtree: true });
    const LOST = /(Ответ на запись не дошёл|The reply to the change didn’t arrive)/, bad = () => seen.find((x) => x.indexOf('danger') === 0);
    s0.value = want; s0.dispatchEvent(new Event('change', { bubbles: true }));`;
const ADR_LOST = `${ADR_W}
    try {
      await W(() => seen.some((x) => LOST.test(x)) || bad(), 60);
      if (bad()) return 'обрыв ответа записи показан ошибкой: ' + bad().slice(0, 120);
      if (!seen.some((x) => LOST.test(x))) return 'нет пояснения «Ответ на запись не дошёл» после перечитывания';
      if (!document.querySelector('.kk-adr')) return 'после перечитывания — полоса ошибки вместо адресов';
      return cur() && cur().value === want ? '' : '«Напрямую» после перечитывания — не ' + want + ': ' + (cur() ? cur().value : 'нет выбора');
    } finally { mo.disconnect(); }`;
BEH.push(["adrdrop", "adr=drop", "settings?tab=addresses", ADR_LOST], ["adrempty", "adr=empty", "settings?tab=addresses", ADR_LOST],
  ["adrget", "adr=dropget", "settings?tab=addresses", `${ADR_W}
    try {
      const broke = await W(() => !document.querySelector('.kk-adr'), 35);         // 3,5 с: повтор чтения — через 2 с
      if (broke) return 'оборванное чтение после записи — полоса ошибки вместо адресов (без повтора)';
      if (bad()) return 'оборванное чтение после записи показано ошибкой: ' + bad().slice(0, 120);
      return cur() && cur().value === want ? '' : '«Напрямую» после повтора чтения — не ' + want;
    } finally { mo.disconnect(); }`],
  ["adrafter", "adr=after", "settings?tab=addresses", `${ADR_W}
    try {
      const AFT = /(xray не перезапустился после последнего изменения адресов|xray didn’t restart after the last change of the addresses)/g;
      const strip = () => [...document.querySelectorAll('.gx-al--warning')].find((x) => x.textContent.match(AFT));
      await W(() => strip() || bad(), 60);
      if (bad()) return 'xray_after: ошибка вместо перечитывания: ' + bad().slice(0, 120);
      if (!strip()) return 'xray_after: warnings чтения через ~2 с не показаны полосой на странице';
      const n = (strip().textContent.match(AFT) || []).length; if (n !== 1) return 'xray_after: предупреждение в полосе ' + n + ' раз(а), нужно одно';
      if ([...document.querySelectorAll('.gx-al--warning')].filter((x) => x.textContent.match(AFT)).length !== 1) return 'xray_after: полос с предупреждением больше одной';
      if (seen.some((x) => new RegExp(AFT.source).test(x))) return 'xray_after: то же предупреждение ещё и тостом (дубль)';
      if (!document.querySelector('.kk-adr')) return 'xray_after: полоса ошибки вместо адресов';
      return cur() && cur().value === want ? '' : '«Напрямую» после перечитывания — не ' + want;
    } finally { mo.disconnect(); }`],
  ["adrdown", "adr=down", "settings?tab=addresses", `${ADR_W}
    try {
      if (!(await W(bad, 60))) return 'связь не вернулась, а обычной ошибки нет';
      if (seen.some((x) => LOST.test(x))) return 'пояснение «Ответ на запись не дошёл», хотя перечитать не вышло';
      if (!document.querySelector('.kk-adr')) return 'перечитать не вышло — пропали прежние адреса';
      return cur() && cur().value === was ? '' : 'выбор «Напрямую» не вернулся к прежнему: ' + (cur() ? cur().value : 'нет выбора');
    } finally { mo.disconnect(); }`]);
if (!process.env.K3_PAGES || PS_ONLY || BEH_ONLY || PAGES.includes("behave") || PAGES.includes("inbounds") || PAGES.includes("exits")) for (const lang of LANGS) for (const [name, q, page, act] of BEH) {
  if (!mine()) continue;
  await BEH_LOAD(lang, q, page);
  const r = await js(`(async () => { ${act} })()`); runs++;
  await note("behave");
  if (r) { bad++; console.log(`FAIL behave:${name} 1440 light ${lang}: ${name} | ${page} | ${r}`); }
  jsFail(`behave:${name} 1440 light ${lang}`);
}
const FWIDTHS = WIDTHS.filter((x) => x === 390 || x === 1440);
if (!process.env.K3_PAGES || PAGES.includes("find")) for (const lang of LANGS) for (const theme of THEMES) for (const w of FWIDTHS) for (const q of ["", lang === "ru" ? "па" : "pa", "zzqqxx"]) {
  if (!mine()) continue;
  const name = `find${q ? "=" + q : ""}`;
  await view(w);
  await cdp("Page.navigate", { url: `${BASE}?mock=1&lang=${lang}#overview` }); await sleep(300);
  await js(`${FONT_LS}localStorage.setItem("kaskad-appearance-v1", JSON.stringify({ v: 3, theme: "${theme}" })); localStorage.setItem("kaskad-find-recent", JSON.stringify(["settings.password", "people"])); true`);
  await cdp("Page.reload"); await sleep(600);
  await until(`(() => { const p = document.querySelector('.gx-page:not([hidden])'); return !!p && p.children.length > 0 && !document.querySelector('#progress:not([hidden])') && !document.querySelector('.gx-skel'); })()`, 8000);
  const miss = await js(`(async () => { const b = document.querySelector('#findBtn'); if (!b) return 'нет #findBtn'; b.click(); await new Promise((r) => setTimeout(r, 300)); const i = document.querySelector('#findQ'); if (!i) return 'нет поля поиска'; i.value = ${JSON.stringify(q)}; i.dispatchEvent(new Event('input')); return ''; })()`);
  if (miss) { bad++; console.log(`FAIL ${name} ${w} ${theme} ${lang}: view | ${miss}`); continue; }
  await sleep(500);
  const found = await js(CHECK); runs++;
  await note("find");
  for (const [kind, where, what] of found) { bad++; console.log(`FAIL ${name} ${w} ${theme} ${lang}: ${kind} | ${where} | ${what}`); }
  jsFail(`${name} ${w} ${theme} ${lang}`);
}
// Знакомство с панелью: каждый шаг после того, как тур расставил рамки (таймеры до 1,2 с)
const TOUR_CHECK = `(() => {
  const out = [], box = document.querySelector('.kk-tour'); if (!box) return [['tour', 'нет карточки тура', '']];
  const R = (e) => e.getBoundingClientRect(), cross = (a, b) => Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left)) * Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));
  const q = R(box), step = (box.querySelector('.kk-tour__t') || {}).textContent || '?', phone = document.documentElement.dataset.device === 'phone';
  const head = document.querySelector('#head');
  // 3.3.7.14 (Claude Design r1, вариант (а)): на телефоне ниже 680 px карточка плотная — без точек, «3 из 4» в строке
  // заголовка; от 680 (390×844) — обычная, как была
  if (phone) {
    const dots = box.querySelector('.kk-tour__dots'), of = box.querySelector('.kk-tour__of'), tt = box.querySelector('.kk-tour__t');
    const dense = !!dots && getComputedStyle(dots).display === 'none' && !!of && !!tt && R(of).bottom > R(tt).top + 1;   // «3 из 4» не над заголовком, а рядом
    const low = document.documentElement.clientHeight < 680;
    if (dense !== low) out.push(['tourdense', step, (low ? 'обычная карточка' : 'плотная карточка') + ' при высоте ' + document.documentElement.clientHeight + ', высота карточки ' + Math.round(q.height)]);
  }
  if (!phone && head && cross(q, R(head)) > 0) out.push(['tourhead', step, 'карточка ' + Math.round(q.top) + '…' + Math.round(q.bottom) + ' на шапке']);
  const ring = document.querySelector('.kk-tour__ring--block');
  if (!ring || ring.hidden) out.push(['tourring', step, 'рамки блока нет на экране']);
  else {
    const r = R(ring), free = r.width * r.height - cross(r, q);
    if (free < Math.min(r.width * r.height * 0.4, r.width * 80)) out.push(['tourring', step, 'блок ' + Math.round(r.top) + '…' + Math.round(r.bottom) + ' почти весь под карточкой ' + Math.round(q.top) + '…' + Math.round(q.bottom)]);
    const sec = document.querySelector('.gx-page:not([hidden])'), main = document.querySelector('#main');
    // блок шага: помеченный разделом [data-tour] (непустой) или карточка с тем же заголовком, что у шага
    const own = sec && sec.querySelector('[data-tour="' + sec.dataset.page + '"]');
    const card = (own && own.offsetHeight ? own : null) || (sec && [...sec.querySelectorAll('.gx-card')].find((c) => { const h = c.querySelector('.gx-card__tt .gx-h2'); return h && h.textContent.trim() === step.trim(); }));
    if (card) { const c = R(card); if (Math.abs(r.left - (c.left - 6)) > 2 || Math.abs(r.width - (c.width + 12)) > 2 || Math.abs(r.top - Math.max(c.top - 6, 4)) > 2) out.push(['tourcard', step, 'обведён не блок «' + step + '»']); }
    // заголовок обведённого блока не под карточкой тура (R-333 повтор №4: телефон, шаг «Выходы»)
    const h2 = card && card.querySelector('.gx-card__tt .gx-h2');
    if (h2 && cross(R(h2), q) > 0) out.push(['tourtitle', step, 'заголовок «' + h2.textContent.trim() + '» ' + Math.round(R(h2).top) + '…' + Math.round(R(h2).bottom) + ' под карточкой ' + Math.round(q.top) + '…' + Math.round(q.bottom)]);
    // «Пользователи и устройства»: главная кнопка раздела («Добавить пользователя») на экране — не уехала под шапку прокруткой тура
    // и не под карточкой (R-333 повтор №3)
    const add = sec && sec.dataset.page === 'people' && sec.querySelector('.gx-toolbar .ga-btn--primary');
    if (add && main) { const a = R(add), m = R(main); if (a.top < m.top - 1 || a.bottom > m.bottom + 1 || cross(a, q) > 0) out.push(['touradd', step, '«' + add.textContent.trim() + '» ' + Math.round(a.top) + '…' + Math.round(a.bottom) + ', видно ' + Math.round(m.top) + '…' + Math.round(m.bottom)]); }
  }
  return out;
})()`;
// ноутбук 1000/1024 — по высоте 768: там прокрутка тура уводила «Добавить человека» под шапку (R-333 повтор №3).
// Телефон с короткой высотой: iPhone в Safari с панелями 390×664 и iPhone SE в Safari 375×548 (всегда: там плотная карточка
// тура, @media max-height 600 — R-333 круг 4 №2), Android 360×640 (full): там карточка тура закрывала «Выходы», «Домен и
// сайт» и «Добавить человека» (R-333 круг 3 №1); 375×667 (iPhone SE / 8 без панелей Safari) — плотная карточка с 3.3.7.14
const FULL = !process.argv[2] || process.argv[2] === "full";
const TOUR_VIEWS = WIDTHS.filter((x) => [390, 820, 1000, 1024, 1440].includes(x)).flatMap((w) => w === 390
  ? [[390, 844], [390, 664], [375, 548], ...(FULL || TOUR_ONLY ? [[360, 640], [375, 667]] : [])] : [[w, w === 1000 || w === 1024 ? 768 : 900]]);
// K3_PAGES=tour — ещё и тёмная (темы из набора); в общем прогоне — светлая (геометрия от темы не зависит)
if (!process.env.K3_PAGES || PAGES.includes("tour")) for (const lang of LANGS) for (const theme of TOUR_ONLY ? THEMES : ["light"]) for (const [w, h] of TOUR_VIEWS) {
  if (!mine()) continue;
  await view(w, h);
  await cdp("Page.navigate", { url: `${BASE}?mock=1&lang=${lang}&intro=1#overview` }); await sleep(300);
  await js(`${FONT_LS}localStorage.setItem("kaskad-appearance-v1", JSON.stringify({ v: 3, theme: "${theme}" })); true`);
  await cdp("Page.reload");
  if (!(await until(`!!document.querySelector('.kk-tour')`, 8000))) { bad++; console.log(`FAIL tour ${w}×${h} ${theme} ${lang}: карточка тура не появилась`); continue; }
  for (let i = 0; i < 4; i++) {
    await sleep(1400);
    const found = await js(TOUR_CHECK); runs++;
    await note("tour");
    for (const [kind, where, what] of found) { bad++; console.log(`FAIL tour ${w}×${h} ${theme} ${lang} шаг ${i + 1}: ${kind} | ${where} | ${what}`); }
    jsFail(`tour ${w}×${h} ${theme} ${lang} шаг ${i + 1}`);
    await js(`(document.querySelector('.kk-tour__next') || { click() {} }).click(), true`);
  }
}
if (MAP_OUT) await writeFile(MAP_OUT, JSON.stringify(mapMerge([mapDoc()], true)));
console.log(bad ? `LAYOUT: ${bad} поломок на ${runs} видах` : `LAYOUT OK: ${runs} видов`);
stop();
process.exit(bad ? 1 : 0);
