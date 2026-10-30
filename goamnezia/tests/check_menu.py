# -*- coding: utf-8 -*-
"""Сплошная проверка меню Telegram-бота goamnezia-bot/bot.py.

Мёртвая кнопка в Telegram ничем себя не выдаёт: нажатие просто не даёт ответа,
и понять это можно только ткнув в неё руками. Поэтому проверяем механически,
по AST исходника — без импорта бота и без python-telegram-bot:

  1. у каждой callback_data, которую рисует кнопка btn(...), есть обработчик в
     EXACT_HANDLERS (точное имя) или PREFIX_HANDLERS (префикс);
  2. динамические callback_data ("u_key~%s~%s~%s" % …) не длиннее 64 байт при
     самых длинных допустимых подстановках (лимит Telegram, иначе BadRequest);
  3. каждый раздел главного экрана достижим с него, и у каждого обработчика
     экрана есть возврат (кнопка «Назад»/«Отмена» или делегирование экрану,
     который её рисует);
  4. маршрут без единой кнопки — предупреждение (не ошибка: команды тоже
     рисуют кнопки, но их роутер не читает).

Подстановки в шаблонах разрешаются по именам переменных (name → 32 символа,
profile → 4 …); аргумент-параметр функции (back_to, prefix, to) прослеживается
до вызывающих на глубину трёх вызовов. Незнакомое имя считается 32 символами.

Запуск: python3 tests/check_menu.py [корень репозитория]
"""
import ast
import pathlib
import sys

ROOT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else '.').resolve()
BOT = ROOT / 'goamnezia-bot' / 'bot.py'
if not BOT.exists():
    print("!! нет %s" % BOT)
    sys.exit(2)

src = BOT.read_text(encoding='utf-8')
tree = ast.parse(src, filename=str(BOT))

# Максимальная длина подстановки по имени переменной (см. регексы в bot.py:
# NAME_RE — 32, профиль — 4, ключ параметра — ContentPaddingAddition = 22,
# Telegram ID — до 20 цифр, страница/индекс — 6 цифр).
PLACEHOLDER_MAX = {
    'name': 32, 'profile': 4, 'p': 7, 'key': 22, 'page': 6, 'idx': 6, 'i': 6,
    'user_id': 20, 'uid': 20, 'target': 32, 'new_admin_id': 20, 'comp': 6,
    'preset': 7, 'gb': 6, 'd': 5, 'm': 4, 'code': 2, 'value': 32, 'fmt': 6,
    # Страница подключения и зеркало сборок (param_types в lib/actions.json):
    # срок страницы — forever (7), приложение каталога — app (32), платформа
    # — android_tv (10), идентификатор страницы — p-<8 hex> (10).
    'ttl': 7, 'app': 32, 'app_id': 32, 'platform': 10, 'pid': 10,
    # «Ключ на другой адрес»: код адреса сервера — 8 hex IPv4 или «h» + 6 hex sha1 домена
    # (KEY_ADDR_RE в bot.py).
    'addr': 8,
    # Смена режима (0.6.0): режим — lite_site (9); линия на UDP 443 за ним — «~awg3» (5).
    'mode': 9, 'tail': 5,
    # Подписка по клиенту: id записи — до 24 знаков (SUB_CLIENT_ID_RE в bot.py), номер пункта — одна цифра
    # (SUB_ITEMS_MAX).
    'cid': 24, 'item_no': 1,
    # Короткие ссылки: id запроса — до 48 знаков (SHORT_REQUEST_RE), ответ — NN | deny | swap (4), суффикс подписки —
    # до awg31.conf (10, SHORT_SUFFIX_RE), метка бана — 8 hex (short_ban_hash).
    'req': 48, 'ans': 4, 'suffix': 10, 'bh': 8,
    # Выход из пересечения подсетей (SUBNET-01, pr_smv~<профиль>~<подсеть>): подсеть — SUBNET_RE бота, частная сеть с
    # маской /20–/24, самая длинная — 192.168.255.0/24 (16).
    'subnet': 16,
    # Окно MTU (pr_mtu_set~ / pr_mtu_ok~<профиль>~<MTU>): стандарт 1280 (MTU_STANDARD) — 4 цифры, как m.
    'std': 4,
}
DEFAULT_MAX = 32
SPECIAL = {'noop'}

# ── функции и вызовы ─────────────────────────────────────────────────────────
functions = {}          # имя → узел FunctionDef/AsyncFunctionDef
for node in ast.walk(tree):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        functions[node.name] = node

calls_by_name = {}      # имя функции → [Call]
for node in ast.walk(tree):
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        calls_by_name.setdefault(node.func.id, []).append(node)


def enclosing_function(target):
    for fn in functions.values():
        for n in ast.walk(fn):
            if n is target:
                return fn
    return None


def param_index(fn, pname):
    names = [a.arg for a in fn.args.args]
    return names.index(pname) if pname in names else -1


def param_default(fn, pname):
    args = fn.args.args
    defaults = fn.args.defaults
    idx = param_index(fn, pname)
    if idx < 0:
        return None
    off = idx - (len(args) - len(defaults))
    if off >= 0:
        return defaults[off]
    return None


class Candidate(object):
    """Шаблон callback_data: текст до первого % (префикс) и оценка длины."""

    def __init__(self, template, max_len, static):
        self.template = template
        self.max_len = max_len
        self.static = static
        self.prefix = template.split('%')[0] if not static else template

    def __repr__(self):
        return self.template


def name_max(name):
    return PLACEHOLDER_MAX.get(name, DEFAULT_MAX)


def resolve(node, fn, depth=0):
    """Множество Candidate для выражения, которое стало callback_data."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {Candidate(node.value, len(node.value.encode('utf-8')), True)}
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod) \
            and isinstance(node.left, ast.Constant) and isinstance(node.left.value, str):
        template = node.left.value
        fixed = len(template.replace('%s', '').replace('%d', '').encode('utf-8'))
        rights = list(node.right.elts) if isinstance(node.right, ast.Tuple) else [node.right]
        out = {Candidate(template, fixed, False)}
        # длину подстановок считаем по именам; параметр функции → по вызывающим
        for r in rights:
            add = DEFAULT_MAX
            if isinstance(r, ast.Name):
                if fn is not None and param_index(fn, r.id) >= 0 and depth < 3:
                    subs = resolve_param(fn, r.id, depth + 1)
                    add = max([c.max_len for c in subs] or [DEFAULT_MAX])
                    # префикс, приехавший параметром, становится шаблоном целиком
                    if template.startswith('%s'):
                        out = set()
                        for c in subs:
                            tail = template[2:]
                            tail_fixed = len(tail.replace('%s', '').replace('%d', '').encode('utf-8'))
                            extra = sum(name_max(x.id) if isinstance(x, ast.Name) else DEFAULT_MAX
                                        for x in rights[1:])
                            out.add(Candidate(c.template + tail, c.max_len + tail_fixed + extra, False))
                        return out
                else:
                    add = name_max(r.id)
            elif isinstance(r, ast.Constant):
                add = len(str(r.value).encode('utf-8'))
            elif isinstance(r, ast.IfExp):
                # "off" if enabled else "on" — берём самую длинную ветку
                branches = [b for b in (r.body, r.orelse) if isinstance(b, ast.Constant)]
                add = max([len(str(b.value).encode('utf-8')) for b in branches] or [DEFAULT_MAX])
            for c in out:
                c.max_len += add
        return out
    if isinstance(node, ast.Name) and fn is not None and param_index(fn, node.id) >= 0 and depth < 3:
        return resolve_param(fn, node.id, depth + 1)
    if isinstance(node, ast.IfExp):
        # "menu_users" if first else "ua_back" — кнопкой становится любая из
        # веток, поэтому маршрут обязан быть у обеих.
        return resolve(node.body, fn, depth) | resolve(node.orelse, fn, depth)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'retry_data':
        # «🔄 Повторить» после lock_timeout: callback_data нажатой кнопки как есть — маршрут у неё
        # уже есть, длина ≤ 64 по определению (Telegram её принял). Считаем служебной, как noop.
        return {Candidate('noop', 4, True)}
    return set()


def resolve_param(fn, pname, depth):
    """Значения параметра pname функции fn по всем её вызовам (и по умолчанию)."""
    out = set()
    default = param_default(fn, pname)
    # retry: Optional[str] = None — «кнопки нет»: такое умолчание кнопкой не становится
    if default is not None and not (isinstance(default, ast.Constant) and default.value is None):
        out |= resolve(default, fn, depth)
    idx = param_index(fn, pname)
    for call in calls_by_name.get(fn.name, []):
        arg = None
        if idx >= 0 and idx < len(call.args):
            arg = call.args[idx]
        for kw in call.keywords:
            if kw.arg == pname:
                arg = kw.value
        if arg is None:
            continue
        out |= resolve(arg, enclosing_function(call), depth)
    return out


# ── 1. кнопки: btn(text, data) и InlineKeyboardButton(callback_data=…) ───────
buttons = []            # (Candidate, функция-владелец)
unresolved = []
for call in calls_by_name.get('btn', []):
    if len(call.args) < 2:
        continue
    fn = enclosing_function(call)
    cands = resolve(call.args[1], fn)
    if not cands:
        unresolved.append((fn.name if fn else '?', ast.dump(call.args[1])[:60]))
    for c in cands:
        buttons.append((c, fn.name if fn else '?'))
for call in calls_by_name.get('InlineKeyboardButton', []):
    for kw in call.keywords:
        if kw.arg == 'callback_data':
            fn = enclosing_function(call)
            cands = resolve(kw.value, fn)
            if not cands:
                unresolved.append((fn.name if fn else '?', ast.dump(kw.value)[:60]))
            for c in cands:
                buttons.append((c, fn.name if fn else '?'))

# ── 2. маршруты ──────────────────────────────────────────────────────────────
exact, prefixes = {}, {}
for node in tree.body:
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        tname = targets[0].id if targets and isinstance(targets[0], ast.Name) else ''
        value = node.value
        if tname == 'EXACT_HANDLERS' and isinstance(value, ast.Dict):
            for k, v in zip(value.keys, value.values):
                if isinstance(k, ast.Constant) and isinstance(v, ast.Name):
                    exact[k.value] = v.id
        if tname == 'PREFIX_HANDLERS' and isinstance(value, (ast.List, ast.Tuple)):
            for elt in value.elts:
                if isinstance(elt, ast.Tuple) and len(elt.elts) == 2 \
                        and isinstance(elt.elts[0], ast.Constant) and isinstance(elt.elts[1], ast.Name):
                    prefixes[elt.elts[0].value] = elt.elts[1].id

fail = []
if not exact or not prefixes:
    print("!! не найдены EXACT_HANDLERS / PREFIX_HANDLERS в bot.py")
    sys.exit(2)

print("кнопок (с учётом подстановок): %d   точных маршрутов: %d   префиксов: %d"
      % (len(buttons), len(exact), len(prefixes)))
for fn, what in unresolved:
    print("   ? не удалось разобрать callback_data в %s: %s" % (fn, what))
if unresolved:
    fail.append("неразобранных callback_data: %d" % len(unresolved))


def routed(cand):
    if cand.static:
        if cand.template in exact or cand.template in SPECIAL:
            return True
        return any(cand.template.startswith(p) for p in prefixes)
    return any(cand.prefix.startswith(p) for p in prefixes)


def handler_of(cand):
    if cand.static and cand.template in exact:
        return exact[cand.template]
    best = None
    for p in prefixes:
        if (cand.template if cand.static else cand.prefix).startswith(p):
            if best is None or len(p) > len(best):
                best = p
    return prefixes.get(best) if best else None


print("── 1. кнопки без обработчика ──")
dead = sorted(set(c.template for c, _ in buttons if not routed(c)))
if dead:
    for d in dead:
        print("   ✗ %s" % d)
    fail.append("мёртвых кнопок: %d" % len(dead))
else:
    print("   ✓ у всех %d шаблонов кнопок есть обработчик" % len(set(c.template for c, _ in buttons)))

print("── 2. длина callback_data ≤ 64 байт ──")
too_long = sorted(set((c.template, c.max_len) for c, _ in buttons if c.max_len > 64))
if too_long:
    for t, n in too_long:
        print("   ✗ %s → до %d байт" % (t, n))
    fail.append("слишком длинных callback_data: %d" % len(too_long))
else:
    worst = max(buttons, key=lambda b: b[0].max_len)[0] if buttons else None
    print("   ✓ все укладываются; самый длинный: %s → до %d байт" % (worst, worst.max_len if worst else 0))

print("── 3. маршруты, для которых обработчик не определён ──")
missing_fn = sorted(set(h for h in list(exact.values()) + list(prefixes.values()) if h not in functions))
if missing_fn:
    for h in missing_fn:
        print("   ✗ %s" % h)
    fail.append("маршрутов на несуществующие функции: %d" % len(missing_fn))
else:
    print("   ✓ все %d обработчиков существуют" % len(set(list(exact.values()) + list(prefixes.values()))))

# ── 4. достижимость разделов с главного экрана ───────────────────────────────
print("── 4. достижимость разделов с главного экрана ──")
main_fn = functions.get('get_main_menu')
from_main = set()
if main_fn:
    for call in ast.walk(main_fn):
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == 'btn' and len(call.args) > 1:
            for c in resolve(call.args[1], main_fn):
                from_main.add(c.template)
for want in ('menu_users', 'sec_state', 'sec_protocols', 'sec_maint', 'sec_settings', 'close_menu'):
    ok = want in from_main
    print("   %s %s" % ('✓' if ok else '✗', want))
    if not ok:
        fail.append("раздел недостижим с главного: %s" % want)

# ── 5. возврат из экранов ────────────────────────────────────────────────────
# Помощники, которые гарантированно рисуют «Назад»/«Отмена» или делегируют
# экрану с возвратом. Обработчик обязан звать хотя бы один из них.
RETURN_MARKERS = {
    '_back_row', 'back_kb', 'cancel_kb', 'show_error', 'show_bad_input', 'render_main',
    'show_user_card', 'show_profile_card', 'arm_typed_confirm', 'dispatch_callback_data',
    'users_keyboard', 'user_card_keyboard', '_quota_keyboard', '_expiry_keyboard', 'lang_keyboard',
    '_user_add_name_screen', '_user_add_profiles_screen', '_user_add_quota_screen',
    '_user_add_days_screen', '_user_add_nav_row', '_user_add_finish',
    # «Добавить сразу несколько»: число → имена → галочки → итог; у каждого экрана — «Назад» или «Пользователи»
    '_user_bulk_count_screen', '_user_bulk_names_screen', '_user_bulk_prefix_screen', '_user_bulk_profiles_screen',
    '_user_bulk_set_count', '_user_bulk_finish', '_user_bulk_lost',
    # «Удалить сразу несколько»: список и ожидание фразы — «Отмена», никого или больше 100 — «Назад»
    '_user_bulk_del_preview',
    # Кнопка под уведомлением (nt~…): тот же экран, что у кнопки без приставки, только новым сообщением
    '_dispatch_as_new_message',
    '_apply_user_quota', '_apply_user_expiry',
    '_apply_profile_port', '_apply_profile_mtu', '_apply_param', '_param_confirm_or_apply',
    '_apply_dns', '_apply_endpoint', 'profile_card_keyboard',
    # Кнопки под отказом моста: «Назад» всегда, у «занято» над ним ещё «🔄 Повторить».
    'fail_markup',
    'agents_keyboard', 'agent_card_keyboard', '_apply_http_on',
    'upstream_rows', 'import_rows', '_admin_claim_keyboard',
    # Шаблоны сайта и смена режима: экраны рисуют помощники, а
    # «Назад»/«Отмена» стоит уже внутри них.
    '_tpl_page_screen', '_tpl_search_page', '_arm_mode_confirm', '_arm_template_confirm',
    # Переход в режим после выбора линии на UDP 443 (0.6.0, F-3): вопрос о домене с «Отменой» или фраза.
    '_mode_to_step',
    # Подписка по клиенту: экраны выбора и приложения рисуют «Назад» сами (_back_row), пропавшее приложение — выбор заново.
    'sub_chooser_keyboard', 'sub_client_keyboard', '_sub_gone',
    # Короткая ссылка: показ — отдельным сообщением (экран приложения с «Назад» остаётся), отказ — с «Назад» к подписке;
    # баны рисует экран с «Назад»; отказ моста словами без кода — те же кнопки, что у show_error (fail_markup).
    '_short_show_target', '_short_send_link', '_short_bans_screen', 'short_show_error',
    # Ускорение AmneziaWG (модуль ядра): экран и вопросы «Вернуть программу», «Пересобрать», «Удалить» — «Назад» к экрану
    # и «Обновить» рисуют помощники.
    'kmod_screen_keyboard', '_kmod_ask',
}
# Экраны без возврата по замыслу: закрытие меню, онбординг до авторизации
# (выход с них — кнопка «Нет», а не «Назад»), выдача ключа отдельным
# сообщением (карточка с кнопками остаётся в чате).
# Ответ на запрос короткой ссылки (sc~) правит само сообщение запроса — итог без кнопок, экрана с возвратом у него нет.
# «Позже» под уведомлением (nt_later, DUP-LINE-01) только убирает кнопки уведомления — экрана нет.
ALLOW_NO_RETURN = {'cb_close_menu', 'cb_admin_confirm', 'cb_admin_again', 'cb_user_key', 'cb_short_decide',
                   'cb_notice_later'}
print("── 5. возврат из экранов ──")
handlers = sorted(set(list(exact.values()) + list(prefixes.values())))
no_return = []
for h in handlers:
    fn = functions.get(h)
    if fn is None or h in ALLOW_NO_RETURN:
        continue
    names = set(n.func.id for n in ast.walk(fn)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name))
    # Кнопка «Назад»/«Отмена», нарисованная прямо в обработчике, тоже возврат.
    own_back = False
    for n in ast.walk(fn):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'btn' and n.args:
            label = n.args[0]
            if isinstance(label, ast.Call) and isinstance(label.func, ast.Name) and label.func.id == '_t' \
                    and len(label.args) > 1 and isinstance(label.args[1], ast.Constant) \
                    and label.args[1].value in ('btn_back', 'btn_cancel'):
                own_back = True
    if not (names & RETURN_MARKERS) and not own_back:
        no_return.append(h)
if no_return:
    for h in no_return:
        print("   ✗ %s" % h)
    fail.append("экранов без возврата: %d" % len(no_return))
else:
    print("   ✓ у всех %d обработчиков экранов есть возврат (исключения по замыслу: %s)"
          % (len(handlers) - len(ALLOW_NO_RETURN), ', '.join(sorted(ALLOW_NO_RETURN))))

# ── 6. маршруты без кнопок (предупреждение) ──────────────────────────────────
print("── 6. маршруты, на которые не ведёт ни одна кнопка ──")
used_exact = set(c.template for c, _ in buttons if c.static)
used_prefix = set()
for c, _ in buttons:
    for p in prefixes:
        if (c.template if c.static else c.prefix).startswith(p):
            used_prefix.add(p)
orphans = sorted([k for k in exact if k not in used_exact] + [p for p in prefixes if p not in used_prefix])
if orphans:
    print("   • %s" % ", ".join(orphans))
else:
    print("   ✓ каждый маршрут кем-то нажимается")

print()
if fail:
    print("ИТОГ: НЕ ПРОШЛО — " + "; ".join(fail))
    sys.exit(1)
print("ИТОГ: всё чисто")
