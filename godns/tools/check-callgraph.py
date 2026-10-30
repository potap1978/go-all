#!/usr/bin/env python3
"""Ловит вызовы функций goDNS, которых не существует.

Зачем отдельная проверка: `bash -n` разбирает синтаксис, но про несущест-
вующую функцию узнаёт только рантайм — и узнаёт в проде, когда оператор
нажал пункт меню. Модули goDNS пишутся раздельно, и разъезд имён (godns.sh
звал engine_select, а библиотека определяла engine_load) — самый вероятный
способ уронить меню.

Смотрим ТОЛЬКО на своё пространство имён: внешние бинарники и метки `case`
нас не касаются, а попытка перечислить всё, что бывает в PATH, даёт шум,
из-за которого проверку перестают читать.
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Префиксы функций проекта. Новый модуль — новый префикс сюда.
all_prefixes_global = ()

PREFIXES = (
    "certs_", "prov_", "bench_", "engine_", "dnsdist_", "selftest_",
    "config_", "json_", "port_", "os_", "print_", "log_", "apt_", "i18n_",
    "load_", "save_", "detect_", "pick_", "wizard_", "submenu_", "show_",
    "first_free", "valid_", "get_server", "random_path", "godns_",
    "cleanup_", "require_", "confirm", "typed_confirm", "ensure_packages",
    "wait_for", "firewall_", "reconfigure", "measure_", "rotate_",
    "uninstall_", "cert_", "startup_", "main_menu", "run_action",
    "logs_",
)


def derive_prefixes(defined):
    """Префиксы, выведенные ИЗ САМИХ определённых функций.

    ГРАБЛИ: список PREFIXES выше — рукописный, и он отстал от кода. Не
    покрыты были 223 функции из 568: весь `webui_*`, все 35 действий бота
    `bot_act_*`, весь `st_*` из самопроверки, `domain_*`, `str_*`. Значит
    опечатка `run_action webui_menyu` в пункте меню проходила ВСЮ батарею
    насквозь — а это ровно тот отказ, ради которого проверка написана:
    оператор жмёт пункт и получает command not found в проде.

    Берём приставку до первого подчёркивания включительно у каждой
    определённой функции. Новый модуль покрывается сам, без правки списка,
    а опечатка внутри модуля ловится, потому что рядом есть его сёстры.
    """
    out = set()
    for name in defined:
        head, sep, _rest = name.partition("_")
        if sep and len(head) >= 2:
            out.add(head + "_")
    return tuple(sorted(out))

# Ключевые слова, после которых идёт КОМАНДА, а не аргумент.
LEAD = {"if", "while", "until", "elif", "then", "else", "do", "!",
        "run_action", "command", "time", "exec"}

# Разделители команд. ТОЛЬКО настоящие: `$(` есть, а голых `(` и `)` нет.
# С ними текст Lua внутри printf (`getPool(''):setCache(godns_pc)`) распадался
# на «команды», и генерируемый конфиг dnsdist читался как вызовы bash —
# проверка ругалась на godns_pc, которая переменная Lua, а не функция.
# Фигурные скобки — тоже граница команды: в однострочной обёртке
# `bot_act_x() { engine_upstream_report; }` первый токен фрагмента — это
# `bot_act_x()`, и вызов внутри `{ … }` оставался невидимым (нашёл
# исполнитель бота). `{` режем только НЕ после `$`: у `${var}` скобка —
# часть подстановки, и `${st_good}` читался бы как вызов st_good.
SPLIT = re.compile(r'\|\||&&|;;|;|\||\$\(|(?<!\$)\{|\}')



# ГРАБЛИ: метка `case` — не вызов. Ниже мы снимаем с имени хвостовую `)`
# (она нужна для `$(имя)`), и из-за этого строка `cert_info) bot_act_cert_info`
# читалась как вызов несуществующей функции `cert_info`. На диспетчерах бота
# и веб-моста это дало пять ложных срабатываний разом — а ложные
# срабатывания в такой проверке хуже её отсутствия: на неё перестают
# смотреть, и настоящий разъезд имён проезжает вместе с шумом.
#
# Метку узнаём строго: одно слово (или несколько через `|`), затем `)`.
# Внутри метки не может быть `(` — это отсекает и `x=$(foo)`, и `func() {`,
# и подоболочку `( cmd )`. Пробел внутри тоже запрещён — это отсекает
# `echo "текст)"`, где скобка живёт в кавычках.
_CASE_LABEL = re.compile(
    r"""^\s*
        (?: ['"]?[A-Za-z0-9_.*?\[\]{}%@:+-]*['"]?
            (?: \s*\|\s* ['"]?[A-Za-z0-9_.*?\[\]{}%@:+-]*['"]? )*
        )
        \)""", re.X)


def strip_case_labels(code: str) -> str:
    """Убрать метки `case` из текста, оставив команды после них."""
    out = []
    for line in code.splitlines():
        m = _CASE_LABEL.match(line)
        if m and "(" not in m.group(0):
            line = line[m.end():]
        out.append(line)
    return "\n".join(out)


def call_names(code: str):
    """Имена в позиции команды.

    Регулярка «одним куском» здесь не работает: в `if engine_is_running; then`
    она захватывала само `if` как имя и до вызова уже не доходила — опечатку
    проверка молча пропускала (поймано отрицательным тестом). Поэтому режем
    строку на команды и снимаем ведущие ключевые слова по одному.
    """
    for frag in SPLIT.split(strip_case_labels(code)):
        toks = frag.split()
        while toks and toks[0] in LEAD:
            toks.pop(0)
        if not toks:
            continue
        # Хвостовая пунктуация прилипает к имени: в `$(prov_list_ids); do`
        # токен приходит как `prov_list_ids);`. Голые скобки мы намеренно
        # не делаем разделителями (см. SPLIT), поэтому снимаем их здесь.
        name = toks[0].rstrip(');}"\'`&')
        if re.fullmatch(r'[a-z_][a-z0-9_]*', name):
            yield name
        # Аргументы trap — тоже позиция команды, просто отложенная.
        # `trap cleanup_tmp_files EXIT` с опечаткой проходил ВСЕ проверки:
        # bash -n доволен, граф вызовов в аргументы не заглядывал, а
        # command not found случался у оператора на каждом выходе из меню,
        # оставляя несобранный мусор. Имя берём только у голого слова:
        # `trap 'f; g' EXIT` разбирается кавычками ниже как обычный код.
        if name == 'trap' and len(toks) >= 2:
            arg = toks[1].strip('\'"').rstrip(');}"\'`&')
            if re.fullmatch(r'[a-z_][a-z0-9_]*', arg):
                yield arg


def main() -> int:
    files = [ROOT / "godns.sh"]
    files += sorted(p for p in (ROOT / "lib").rglob("*.sh") if p.parent.name != "lang")
    # Мосты бота и панели — тоже наш код и тоже зовут lib/*.sh. Без них
    # проверка объявляла бы недостижимыми девять функций webui_* и весь
    # фасад бота: их вызывающий лежит в godns-bot/actions.sh и
    # godns-web/bridge.sh, куда сканер раньше не заглядывал. Заглушить
    # предупреждение списком исключений было бы хуже — тогда настоящая
    # осиротевшая функция в этих файлах прошла бы незамеченной.
    files += sorted(ROOT.glob("godns-bot/*.sh"))
    files += sorted(ROOT.glob("godns-web/*.sh"))

    defined = set()
    for f in files:
        for line in f.read_text(encoding="utf-8").splitlines():
            m = re.match(r'^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\(\)', line)
            if m:
                defined.add(m.group(1))

    i18n = set()
    ru = ROOT / "lib" / "lang" / "ru.sh"
    if ru.is_file():
        for line in ru.read_text(encoding="utf-8").splitlines():
            m = re.match(r'I18N\[([a-zA-Z0-9_]+)\]', line)
            if m:
                i18n.add(m.group(1))

    missing: dict[str, list[str]] = {}
    # Считаем, ЧТО осматриваем: страж, которому нечего проверять,
    # обязан сказать это, а не молча зеленеть.
    all_prefixes = tuple(sorted(set(PREFIXES) | set(derive_prefixes(defined))))
    global all_prefixes_global
    all_prefixes_global = all_prefixes

    for f in files:
        rel = f.relative_to(ROOT)
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            code = line.split(" #")[0]
            for name in call_names(code):
                if name in defined or name in i18n:
                    continue
                if not name.startswith(all_prefixes):
                    continue
                if re.search(r'\b' + re.escape(name) + r'\s*=', code):
                    continue
                missing.setdefault(name, []).append(f"{rel}:{n}")

    if missing:
        print(f"✗ {len(missing)} call(s) to a goDNS function that is never defined:")
        for name, locs in sorted(missing.items()):
            extra = f"  (+{len(locs) - 1} more)" if len(locs) > 1 else ""
            print(f"    {name:<26} {locs[0]}{extra}")
        return 1

    print(f"✓ call graph resolves — {len(defined)} function(s) defined, "
          f"{len(files)} file(s) scanned")

    # Второй проход: у бота и моста свой ограниченный набор source, и
    # общий граф выше слеп к границе процессов.
    return check_source_boundaries()


def check_source_boundaries():
    """Вызовы, недостижимые в СВОЁМ процессе из-за набора source.

    ГРАБЛИ: check_call_graph выше сканирует все файлы одним пространством
    имён, и вызов `firewall_open_ports` из godns-bot/actions.sh «резолвился»
    определением из godns.sh — которого процесс демона не сорсит вовсе
    (godns.sh при source исполняет main). В проде это было `command not
    found`, проглоченное `|| true`: «Включить DoT» из бота не открывало
    порт. Страж слеп к границе процессов.

    Здесь проверяем ровно два самостоятельных процесса — бот и мост
    панели, — у которых свой ограниченный список source. Каждая
    goDNS-функция, которую они ЗОВУТ, обязана быть определена в одном из
    файлов, что они реально сорсят (плюс их собственное тело).
    """
    root = pathlib.Path(__file__).resolve().parent.parent
    entries = {
        "godns-bot/actions.sh": "godns-bot",
        "godns-web/bridge.sh":  "godns-web",
    }
    rc = 0
    checked = 0
    for entry, _tag in entries.items():
        ep = root / entry
        if not ep.exists():
            print(f"✗ source-граница: точки входа {entry} нет")
            return 1
        # Замыкание по source, а не один файл. ГРАБЛИ: страж читал только
        # строки самой actions.sh, и вызов функции из godns.sh, вставленный
        # в lib/engine.sh, был невидим — тот же самый баг «Включить DoT не
        # открывает порт», просто на уровень глубже. Что процесс исполняет,
        # то он исполняет и через свои библиотеки.
        texts = {}
        queue = [ep]
        while queue:
            f = queue.pop()
            if f in texts:
                continue
            try:
                texts[f] = f.read_text(encoding="utf-8")
            except OSError:
                texts[f] = ""
                continue
            for stem in re.findall(
                    r'(?:^|\s)(?:source|\.)\s+\S*?([a-z0-9_]+)\.sh', texts[f]):
                for cand in (root / "lib" / f"{stem}.sh",
                             root / "lib" / "engines" / f"{stem}.sh"):
                    if cand.exists() and cand not in texts:
                        queue.append(cand)
        # engine_load подключает lib/engines/<name>.sh по ПЕРЕМЕННОЙ, и
        # регулярка выше такой source не видит. Движок — часть процесса
        # ровно так же, добавляем все реализации явно.
        if (root / "lib" / "engine.sh") in texts:
            for cand in sorted((root / "lib" / "engines").glob("*.sh")):
                if cand not in texts:
                    texts[cand] = cand.read_text(encoding="utf-8")
        src = texts[ep]
        avail = set()
        for txt in texts.values():
            avail |= set(re.findall(r'^([a-z0-9_]+)\(\)', txt, re.M))
        # Разбираем ПОСТРОЧНО, как основной проход: call_names считает
        # команду по первому токену фрагмента, и на всём файле разом
        # почти все вызовы внутри строк терялись — страж молчал даже на
        # настоящем firewall_open_ports. Комментарии пропускаем.
        for f, txt in sorted(texts.items()):
            where = f.relative_to(root).as_posix()
            # Тело heredoc — данные, а не код. Каталог провайдеров в
            # lib/providers.sh хранит в полях ИМЕНА КЛЮЧЕЙ i18n
            # (prov_priv_cloudflare и такие же), и построчный разбор читал
            # их как вызовы несуществующих функций.
            here = None
            for lineno, line in enumerate(txt.splitlines(), 1):
                if here is not None:
                    if line.strip() == here:
                        here = None
                    continue
                # Хвост после метки бывает («<<'EOF' || true»), поэтому не
                # привязываемся к концу строки.
                m = re.search(r"""<<-?\s*(["']?)([A-Za-z_][A-Za-z0-9_]*)\1""", line)
                if m:
                    here = m.group(2)
                    continue
                if line.lstrip().startswith("#"):
                    continue
                code = line.split(" #")[0]
                for name in call_names(code):
                    if not name.startswith(all_prefixes_global):
                        continue
                    if re.search(r'\b' + re.escape(name) + r'\s*=', code):
                        continue  # присваивание, не вызов
                    checked += 1
                    if name in avail:
                        continue
                    # `if declare -F f; then f; fi` — осознанно
                    # необязательный вызов, а не забытый source.
                    if re.search(r'declare\s+-F\s+' + re.escape(name), txt):
                        continue
                    rc = 1
                    print(f"✗ {entry}: {where}:{lineno} зовёт {name}(), но её "
                          f"нет ни в одном файле, который этот процесс "
                          f"подключает — здесь это command not found")
    if checked == 0:
        print("✗ source-граница: не осмотрено ни одного вызова")
        return 1
    if rc == 0:
        print(f"✓ source-границы бота и моста целы "
              f"({checked} вызов(ов) проверено)")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
