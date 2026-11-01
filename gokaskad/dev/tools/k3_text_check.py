#!/usr/bin/env python3
"""Проверка текстов продукта RU/EN по правилам владельца (tests/test_texts_rules.py).

Тексты: каталоги ядра lib/kaskad/i18n/catalog/{ru,en}/*.json, словари веба lib/kaskad/panel/static/**/*i18n-{ru,en}.js
(панель, мастер, «Схема», выдача, страница человека, общие компоненты), тексты SSH lib/kaskad/i18n/sh/*.{ru,en}.sh,
install.sh вне сгенерированных блоков (K3_I18N, K3_PAYLOAD), документы для человека (README.md, DOCS.md,
ИНСТРУКЦИЯ_ДЛЯ_ПОЛЬЗОВАТЕЛЯ.md) и строки в коде веба, которые видит человек (литералы static/**/*.js — подсказки ph:,
макеты dev-mock*; страницы .html) — для них только domain, zone, ip, push.

Правила (код правила — в первой колонке файла исключений):
  domain   пример домена в региональной зоне (.ru, .рф, .su, .by, .kz, .ua…) — пишем «домен» / example.com
           (владелец 12.10, память neutral-domain-examples); настоящие сайты (ozon.ru в правилах «RU напрямую»,
           партнёрские хостинги) — в REAL_SITES;
  zone     совет зоны: «в зоне .ru», «.ru zone», «домен .ru»;
  ip       настоящий IPv4 в тексте вместо 192.0.2.x / 198.51.100.x / 203.0.113.x (частные, служебные адреса и
           публичные DNS — не примеры, их не трогаем);
  yn       вопрос «y/n» вместо выбора цифрами «1) Да … 2) Нет … Выбор [1]:» (владелец 07.10, память
           menu-numbered-choices): подсказки (y/n), [Y/n], (д/н), «ответьте y»; в install.sh — и разбор ответа «y»;
  num-dec  десятичный разделитель: в RU запятая (1,5 ГБ), в EN точка (1.5 GB);
  num-unit пробел между числом и единицей: в RU и EN единица отделена пробелом (12 МБ, 30 мс, 5 GB) — не «12МБ»;
           неразрывный он или обычный, решает вывод (см. R-texts.md), тест требует только наличие;
  pct      проценты пишутся слитно: «5%», не «5 %» (так в каталогах);
  keys     в паре словарей RU/EN одинаковые ключи;
  holes    в паре одинаковые заполнители: {name} (каталоги, веб) и %s/%d (SSH);
  empty    пустой текст в одном языке пары при непустом в другом;
  push     навязывающие формулировки: выбор зоны, регистратора, страны или хостинга как единственный путь;
  people   сущность учёта — «пользователь / пользователи» (у пользователя — устройства), не «человек / люди»
           (владелец 24.10): каталоги, веб, SSH, литералы кода — RU «человек…/люд…», EN person/people; описания
           MCP (lib/kaskad/mcp/actions.json) — только множественное «люди…» / people: «человек» / person там —
           тот, кто ведёт ИИ-агента (администратор узла), а не пользователь шлюза. README и install.sh — нет
           (старые «Что нового» не переписываем, в install.sh вне блоков текстов о пользователях нет).
  domestic роль группы и выхода «ru» — «внутренняя группа / внутренний выход (ключ)», EN domestic, не «российская
           группа / российский выход» / Russian group, exit (владелец 29.10): каталоги, веб, SSH, литералы кода,
           описания MCP. Факты про страну («российские сайты», Russian networks, ⚠РФ) — не роль; README и
           install.sh — нет (старые «Что нового» и комментарии; текущие разделы README — tests/test_texts_rules.py).

Исключения — tools/k3_text_exceptions.tsv: «правило<TAB>файл<TAB>ключ<TAB>совпадение<TAB>причина». Ключ — ключ
текста в словаре; для построчных файлов (install.sh, документы) — пусто: строка файла не годится, она сдвигается.
Одинаковые совпадения в одном месте — столько строк, сколько раз встречаются.

Запуск: python3 tools/k3_text_check.py [--root DIR] [--all] [--baseline]
  без ключей — только новые нарушения и устаревшие исключения (код 1, если они есть);
  --all — все нарушения; --baseline — напечатать строки файла исключений для всех текущих нарушений.
"""

import argparse
import collections
import glob
import ipaddress
import json
import os
import re
import string
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
EXCEPTIONS = os.path.join(HERE, "k3_text_exceptions.tsv")

Item = collections.namedtuple("Item", "src lang key text kind")      # kind: catalog web sh install doc
Finding = collections.namedtuple("Finding", "rule src key match")

DOCS = ("README.md", "DOCS.md", "ИНСТРУКЦИЯ_ДЛЯ_ПОЛЬЗОВАТЕЛЯ.md")
GENERATED = (("# >>> K3_I18N", "# <<< K3_I18N"), ("# >>> K3_PAYLOAD", "# <<< K3_PAYLOAD"))

# ── чтение текстов ──────────────────────────────────────────────────────────


def _rel(root, path):
    return os.path.relpath(path, root).replace(os.sep, "/")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def catalog_pairs(root):
    """[(label, {lang: {key: text}})] — области каталогов ядра."""
    base = os.path.join(root, "lib", "kaskad", "i18n", "catalog")
    areas = sorted(set(os.path.basename(p)[:-5] for p in glob.glob(os.path.join(base, "*", "*.json"))))
    out = []
    for area in areas:
        docs = {}
        for lang in ("ru", "en"):
            p = os.path.join(base, lang, area + ".json")
            docs[lang] = json.loads(_read(p)) if os.path.isfile(p) else {}
        out.append(("lib/kaskad/i18n/catalog/{ru,en}/%s.json" % area, docs))
    return out


_TOKEN = re.compile(r"""\s+|//[^\n]*|/\*.*?\*/|"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'|`(?:[^`\\]|\\.)*`"""
                    r"""|[A-Za-z_$][\w$.]*|[{}:,;=()\[\]]|.""", re.S)


def _unq(tok):
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), tok[1:-1])


def js_dictionary(src):
    """{key: text} первого литерала объекта после «=» (вложенные объекты — «группа.ключ»)."""
    toks = [t for t in (m.group(0) for m in _TOKEN.finditer(src))
            if not (t.isspace() or t.startswith("//") or t.startswith("/*"))]
    i = next(k for k, t in enumerate(toks) if t == "=" and toks[k + 1] == "{") + 1
    out = {}

    def obj(i, prefix):
        i += 1
        while toks[i] != "}":
            key = _unq(toks[i]) if toks[i][0] in "\"'" else toks[i]
            if toks[i + 1] != ":":
                raise ValueError("словарь: после ключа %r нет «:»" % key)
            i += 2
            if toks[i] == "{":
                i = obj(i, prefix + key + ".")
            else:
                if toks[i][0] not in "\"'`":
                    raise ValueError("словарь: значение %r — не строка" % key)
                out[prefix + key] = _unq(toks[i])
                i += 1
            if toks[i] == ",":
                i += 1
        return i + 1

    obj(i, "")
    return out


def web_pairs(root):
    static = os.path.join(root, "lib", "kaskad", "panel", "static")
    out = []
    for ru in sorted(glob.glob(os.path.join(static, "**", "*i18n-ru.js"), recursive=True)):
        en = ru[:-len("ru.js")] + "en.js"
        docs = {"ru": js_dictionary(_read(ru)), "en": js_dictionary(_read(en)) if os.path.isfile(en) else {}}
        out.append((_rel(root, ru)[:-len("ru.js")] + "{ru,en}.js", docs))
    return out


_SH_LINE = re.compile(r"^M_(ru|en)_([A-Za-z0-9_]+)=((?:'[^']*'|\\')*)\s*$")


def sh_texts(src):
    """{lang: {key: text}} одного файла SSH-текстов."""
    out = {}
    for line in src.splitlines():
        m = _SH_LINE.match(line)
        if m:
            text = "".join(p[1:-1] if p.startswith("'") else "'" for p in re.findall(r"'[^']*'|\\'", m.group(3)))
            out.setdefault(m.group(1), {})[m.group(2)] = text
    return out


def sh_pairs(root):
    base = os.path.join(root, "lib", "kaskad", "i18n", "sh")
    out = []
    for ru in sorted(glob.glob(os.path.join(base, "*.ru.sh"))):
        en = ru[:-len("ru.sh")] + "en.sh"
        docs = {"ru": sh_texts(_read(ru)).get("ru", {}),
                "en": sh_texts(_read(en)).get("en", {}) if os.path.isfile(en) else {}}
        out.append((_rel(root, ru)[:-len("ru.sh")] + "{ru,en}.sh", docs))
    return out


def install_lines(root):
    """[(номер, строка)] install.sh без сгенерированных блоков и без строк-комментариев.

    Блок без закрывающей метки — ошибка: иначе остаток файла молча выпал бы из проверки."""
    p = os.path.join(root, "install.sh")
    if not os.path.isfile(p):
        return []
    out, skip, opened = [], None, 0
    for n, line in enumerate(_read(p).splitlines(), 1):
        if skip:
            if line.startswith(skip):
                skip = None
            continue
        hit = [end for beg, end in GENERATED if line.startswith(beg)]
        if hit:
            skip, opened = hit[0], n
            continue
        if not line.lstrip().startswith("#"):
            out.append((n, line))
    if skip:
        raise ValueError("install.sh:%d: блок без закрывающей метки «%s»" % (opened, skip))
    return out


# строки и шаблоны в коде веба, которые видит человек (подсказки ph:, макеты dev-mock*), и страницы .html;
# словари *i18n-*.js читаются отдельно (web_pairs), комментарии пропускаются
_JS_LIT = re.compile(r"""//[^\n]*|/\*.*?\*/|"((?:[^"\\\n]|\\.)*)"|'((?:[^'\\\n]|\\.)*)'|`((?:[^`\\]|\\.)*)`""", re.S)
_JS_T_CALL = re.compile(r"\bt\(\s*(?:[\w.]+\s*,\s*)?$")          # t(lang, 'ns.ru') — ключ словаря, не текст
_JS_KEY = re.compile(r"""(?:([A-Za-z_$][\w$]*)|"([^"\n]*)"|'([^'\n]*)')\s*:\s*$""")


def code_texts(root):
    """[(файл, ключ, текст)] — литералы JS с точкой (домен, адрес) или пробелом (текст) и строки .html панели; ключ — имя свойства
    перед литералом («ph»), иначе пусто: номер строки не годится, он сдвигается."""
    static = os.path.join(root, "lib", "kaskad", "panel", "static")
    out = []
    for p in sorted(glob.glob(os.path.join(static, "**", "*.js"), recursive=True)):
        if re.search(r"i18n-(?:ru|en)\.js$", p):
            continue
        src, rel = _read(p), _rel(root, p)
        for m in _JS_LIT.finditer(src):
            if m.lastindex is None:
                continue                                   # комментарий
            text = m.group(m.lastindex)
            if ("." not in text and " " not in text) or _JS_T_CALL.search(src, max(0, m.start() - 40), m.start()):
                continue                                   # без точки и пробела — имя в коде, не текст (REV-texts-2)
            k = _JS_KEY.search(src, max(0, m.start() - 80), m.start())
            key = (k.group(1) or k.group(2) or k.group(3) or "") if k else ""
            out.append((rel, key, _unq('"' + text + '"')))
    for p in sorted(glob.glob(os.path.join(static, "**", "*.html"), recursive=True)):
        rel = _rel(root, p)
        out.extend((rel, "", line) for line in _read(p).splitlines() if "." in line)
    return out


def mcp_texts(root):
    """[(ключ, язык, текст)] — описания каталога MCP (lib/kaskad/mcp/actions.json): строки под ключами ru / en;
    ключ — путь через точку («actions.user_add.description»), номер строки не годится."""
    p = os.path.join(root, "lib", "kaskad", "mcp", "actions.json")
    if not os.path.isfile(p):
        return []
    out = []

    def walk(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                if k in ("ru", "en") and isinstance(v, str):
                    out.append((".".join(path), k, v))
                else:
                    walk(v, path + [str(k)])
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, path + [str(v.get("name", i)) if isinstance(v, dict) else str(i)])

    walk(json.loads(_read(p)), [])
    return out


def all_pairs(root):
    return ([("catalog",) + p for p in catalog_pairs(root)] + [("web",) + p for p in web_pairs(root)]
            + [("sh",) + p for p in sh_pairs(root)])


def items(root, pairs=None):
    out = []
    for kind, label, docs in (pairs if pairs is not None else all_pairs(root)):
        for lang in ("ru", "en"):
            src = label.replace("{ru,en}", lang)
            for k, v in sorted(docs[lang].items()):
                out.append(Item(src, lang, k, v, kind))
    for n, line in install_lines(root):
        out.append(Item("install.sh", None, "", line, "install"))
    for src, key, text in code_texts(root):
        out.append(Item(src, None, key, text, "code"))
    for key, lang, text in mcp_texts(root):
        out.append(Item("lib/kaskad/mcp/actions.json", lang, key, text, "mcp"))
    for name in DOCS:
        p = os.path.join(root, name)
        if os.path.isfile(p):
            for line in _read(p).splitlines():
                out.append(Item(name, None, "", line, "doc"))
    return out


# ── правила ─────────────────────────────────────────────────────────────────

# 1. домены и адреса
# .md, .am, .ge, .az не берём: README.md, «i.am» — имена файлов и слова чаще, чем домены
REGIONAL = ("ru", "рф", "su", "by", "kz", "ua", "uz", "kg", "tj", "moscow", "москва", "рус", "дети", "spb", "msk",
            "tatar")
# перед именем можно «//» (https://vpn.example.ru/…) и «@» (you@example.ru, vless://id@host), но не «/» пути (a/b.ru)
_DOMAIN = re.compile(r"(?i)(?<![\w.-])(?<![^/]/)(?<!^/)((?:[a-zа-яё0-9](?:[a-zа-яё0-9-]*[a-zа-яё0-9])?\.)+(?:%s))(?![\w.-]*[\w])"
                     % "|".join(re.escape(z) for z in sorted(REGIONAL, key=len, reverse=True)))
# настоящие сайты и службы, а не примеры домена человека: правила «RU напрямую», проверки, партнёры владельца
REAL_SITES = {
    "ozon.ru", "gosuslugi.ru", "yandex.ru", "ya.ru", "mail.ru", "vk.ru", "sberbank.ru", "online.sberbank.ru",
    "tbank.ru", "wildberries.ru", "avito.ru", "kinopoisk.ru", "rutube.ru", "dzen.ru", "mos.ru", "nalog.ru",
    "nalog.gov.ru", "ok.ru", "max.ru", "rbc.ru", "ria.ru", "lenta.ru", "2ip.ru", "anten-ka.ru", "kaskad.ru",
    "adminvps.ru", "vk.cc", "ru.wikipedia.org", "nic.ru", "reg.ru", "tinkoff.ru", "gismeteo.ru", "hh.ru",
    "kremlin.ru", "government.ru", "cbr.ru", "pochta.ru", "rzd.ru", "aeza.ru", "timeweb.ru", "beget.ru",
    "selectel.ru", "vdsina.ru", "firstvds.ru", "ruvds.com", "sber.ru", "netcraze.ru", "hoaxisr.ru",
}
# зоны второго уровня (список SLD в setup.js — деление имени на поддомен и домен): зона, а не пример домена человека
SECOND_LEVEL_ZONES = {
    "com.ru", "net.ru", "org.ru", "pp.ru", "msk.ru", "spb.ru", "com.ua", "net.ua", "org.ua", "in.ua", "co.ua", "kiev.ua",
    "pp.ua", "kh.ua", "biz.ua", "gov.ua", "edu.ua", "od.ua", "dp.ua", "lviv.ua", "zp.ua", "com.kz", "org.kz", "net.kz",
    "com.by", "net.by", "org.by",
}
# имя файла или свойство кода, а не домен: «файл rules.ru», «name.by»
_FILE_BEFORE = re.compile(r"(?i)(?:файл\w*|file\w*)\s+$")
CODE_NAMES = {"name.by", "sort.by", "group.by", "order.by", "filter.by", "split.by"}
# пример с кириллицей (IDN) показывает, что имя кириллицей работает, — не совет зоны (tests/test_333_no_ru_domain.py)
IDN_EXAMPLES = {"впн.пример.рф", "vpn.пример.рф"}
_ZONE = re.compile(r"(?i)(?<!без )(?<!without )(?<!no )зон[аеуы]\s+\.?(?:%s)\b|\.(?:%s)[\s-]+zone|zone\s+\.(?:%s)\b|домен(?:ы|а)?\s+\.(?:%s)\b"
                   % (("|".join(REGIONAL),) * 4))
_IPV4 = re.compile(r"(?<![\w.:-])(?<![^/\s]/)(?<!^/)((?:\d{1,3}\.){3}\d{1,3})(?:/\d{1,2})?(?![\w.]*\d|\.\w)")
DOC_NETS = [ipaddress.ip_network(n) for n in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")]
# публичные DNS — настоящие службы, которые советуют как есть, не пример адреса
# номер версии из 4 чисел — не адрес: «версия 1.10.2.3», «v4.8.12.9», «AmneziaVPN 4.8.12.9 или новее», «4.8.12.9+».
# Название клиента само по себе версию не делает: «WireGuard 45.10.20.31», «Happ 45.10.20.31» — адрес (REV-texts-2 Н1);
# «+» — только вплотную к числу («Адрес 45.10.20.31 + порт 443» — адрес); после числа «:порт» или «/маска» — адрес
_VER_BEFORE = re.compile(r"(?i)(?:\bv|\bver\.?|верси[яиюей]|version)\s*$")
_VER_AFTER = re.compile(r"(?i)(?:\+|\s*(?:или\s+(?:новее|выше)|и\s+(?:новее|выше)|or\s+(?:newer|later|above)|and\s+(?:newer|later|above)))")
_ADDR_AFTER = re.compile(r":\d")
PUBLIC_DNS = {"1.1.1.1", "1.0.0.1", "8.8.8.8", "8.8.4.4", "9.9.9.9", "149.112.112.112", "77.88.8.8", "77.88.8.1",
              "94.140.14.14", "208.67.222.222"}


def _ip_is_example_or_service(s):
    try:
        ip = ipaddress.ip_address(s)
    except ValueError:
        return True                                    # 999.1.2.3 — не адрес (версия, номер)
    if any(ip in n for n in DOC_NETS) or s in PUBLIC_DNS:
        return True
    return not ip.is_global                            # частные, 100.64/10, 127/8, 0.0.0.0, 255.255.255.0, …


def _own_versions(root=ROOT):
    """Выпуски самого Kaskad из 4 чисел («3.3.5.1») — номер версии, а не адрес, в любом окружении («исправление
    безопасности 3.3.5.1», «до 3.3.5.1»). Берутся из заголовка «## Kaskad 3 (версии …)» README.md и KASKAD_VERSION
    install.sh: новый выпуск из 4 чисел попадает сюда сам, когда его вписали в README."""
    out = set()
    try:
        with open(os.path.join(root, "README.md"), encoding="utf-8") as f:
            head = re.search(r"(?m)^## Kaskad 3 \(версии ([^)]*)\)", f.read())
        if head:
            out.update(re.findall(r"\b\d+(?:\.\d+){3}\b", head.group(1)))
        with open(os.path.join(root, "install.sh"), encoding="utf-8") as f:
            ver = re.search(r'(?m)^KASKAD_VERSION="([0-9.]+)"', f.read(8192))
        if ver:
            out.add(ver.group(1))
    except OSError:
        pass
    return frozenset(out)


OWN_VERSIONS = _own_versions()


def _is_version(text, m):
    if "/" in m.group(0) or _ADDR_AFTER.match(text, m.end()):
        return False
    if m.group(1) in OWN_VERSIONS:
        return True
    return bool(_VER_BEFORE.search(text, 0, m.start()) or _VER_AFTER.match(text, m.end()))


def _not_a_domain(text, m):
    name = m.group(1).lower()
    return (name in CODE_NAMES or name in SECOND_LEVEL_ZONES or bool(_FILE_BEFORE.search(text, max(0, m.start() - 20), m.start()))
            or _site(name) or name in IDN_EXAMPLES)


def _site(name):
    n = name.lower()
    return n in REAL_SITES or any(n.endswith("." + s) for s in REAL_SITES)


# 2. y/n
_YN_PAIR = r"(?:y\s*/\s*n|д\s*/\s*н|yes\s*/\s*no|да\s*/\s*нет)"
# (y/n), [Y/n] и без скобок («Удалить всё? y/N: »); «ответьте y», «Enter y to confirm»
# без скобок — только как вопрос: после «?»/«:» или перед «:»/«?»/концом («a simple yes/no question» — не вопрос)
_YN = re.compile(r"(?i)[(\[]\s*" + _YN_PAIR + r"\s*[)\]]"
                 r"|(?:(?<=[?:])|(?<=[?:]\s))" + _YN_PAIR + r"(?![\w/])"
                 r"|(?<![\w/(\[])" + _YN_PAIR + r"(?=\s*[:?]|\s*$)"
                 r"|(?:ответьте|отвечайте|напечатайте|введите|нажмите|answer|type|press|enter)\s+[«\"*']*[yд][»\"*']*"
                 r"(?![\wа-яё])")
# install.sh: разбор ответа «y» (как в tools/k3_install_test.sh): = "y", == [yY]*, =~ ^(y|yes)$, case … in y|Y),
# д|Д|y|yes), [yYдД]*), k3_yes
_YN_WORD = r"""["']?(?:[yYдД]|[yY]es|YES|[дД]а)["']?"""
# сравнение в [ ] / [[ ]]: «= "y"», «== 'yes'», «== [yY]*», «== y*»; голое «= yes» — нет: так сравнивают вывод
# systemctl (NeedDaemonReload), а «RemainAfterExit=yes», «y=…» — присваивание
_CASE_ITEM = r"[^\s|()]+"
_YN_CODE = re.compile(r"=~\s*['\"]?\^?[(\[]*[yYдД]"
                      r"|\s==?\s*(?:[\"'](?:[yYдД]|[yY]es|YES|[дД]а)[\"'](?=[\s\]);]|$)|\[[yYдД]{2,}\]\*?|[yYдД]\*?(?=[\s\];]|$))"
                      r"|\"\" *\| *y *\|"
                      r"|(?:^|[\s(])" + _YN_WORD + r"(?:\s*\|\s*" + _CASE_ITEM + r")+\s*\)"
                      r"|(?:^|[\s(])" + _CASE_ITEM + r"(?:\s*\|\s*" + _CASE_ITEM + r")*\s*\|\s*" + _YN_WORD + r"\s*\)"
                      r"|(?:^|[ (])\[[yYдД]{2,}\]\*?\)|\bk3_yes\b")
_IS_PROMPT_SRC = ("sh", "install", "doc", "catalog")   # бот и веб — кнопками, правило их не касается

# 3. числа
# одна буква «с» (секунды) не берём: «AmneziaWG 3.1 с …», «TLS 1.3 с узла» — версия и предлог, а не 3,1 секунды
RU_UNITS = r"(?:Б|КБ|МБ|ГБ|ТБ|Кбит/с|Мбит/с|Гбит/с|КБ/с|МБ/с|мс|сек|мин|МиБ|ГиБ)"
EN_UNITS = r"(?:B|KB|MB|GB|TB|KiB|MiB|GiB|Kbit/s|Mbit/s|Gbit/s|kbps|Mbps|Gbps|KB/s|MB/s|ms|sec|min)"
_RU_DEC_POINT = re.compile(r"(?<![\w.,{%])(\d+\.\d+)\s?" + RU_UNITS + r"(?![\wа-яё])")
_EN_DEC_COMMA = re.compile(r"(?<![\w.,{%])(\d+(?:,\d+)+)\s?" + EN_UNITS + r"(?![\w])")
_EN_THOUSANDS = re.compile(r"[1-9]\d{0,2}(?:,\d{3})+")                     # «1,500 MB» — разделитель тысяч в EN
# число, подстановка {n} или %s/%d вплотную к единице: «12МБ», «{ms}ms», «%sс». Одна «с» (секунды) — только после
# подстановки: после цифры это чаще предлог («3.1 с»), а «{sec}с» — всегда секунды. В EN «{n}s» и «%ds» — секунды
# вплотную; «12s» не берём (десятилетия, «x2s»)
_NUM = r"(?:(?<![\w.,{%])\d+(?:[.,]\d+)?|\{\w+\}|%[sd])"
_GLUED_EN = r"KB|MB|GB|TB|Mbit/s|Gbit/s|Mbps|Gbps|ms"
_RU_GLUED = re.compile(r"(" + _NUM + r")(?:КБ|МБ|ГБ|ТБ|Мбит/с|Гбит/с|мс|сек|мин|" + _GLUED_EN + r"|(?<=[}s])с)(?![\wа-яё])")
_EN_GLUED = re.compile(r"(" + _NUM + r")(?:" + _GLUED_EN + r"|sec|min|(?<=\})s|(?<=%d)s)(?![\w])")
# в RU единица по-русски: «{ms} мс», не «{ms} ms» (вплотную латиницей ловит _RU_GLUED — одно нарушение на место)
_RU_LATIN = re.compile(r"(" + _NUM + r")[  ](?:" + _GLUED_EN + r"|sec|min)(?![\w])")
_PCT_SPACE = re.compile(r"(?<![\w.,{])(?:\d+(?:[.,]\d+)?|\{\w+\}|%[sd])[ \u00a0\u202f](?:%%|%(?![-+0-9.]*[A-Za-z%]))")    # «%d %H» — формат даты, не процент

# 5. навязывающие формулировки: зона, регистратор, страна, хостинг как единственный путь
PUSH_MARKERS = (
    r"(?:купите|возьмите|зарегистрируйте|закажите|арендуйте)\s+(?:домен|сервер|vps)\s+(?:только\s+)?(?:в|на|у)\s+\S+",
    r"(?:домен|сервер|vps)\w*\s+(?:нужен\s+|подойдёт\s+)?только\s+(?:в|на|у)\s+\S+",
    r"только\s+(?:в\s+)?зон[еау]\s+\.?\S+",
    r"российск\w+\s+(?:домен|регистратор|хостинг|зон\w+)",
    r"(?:лучше|советуем|рекомендуем)\s+(?:всего\s+)?(?:взять|купить|выбрать|зарегистрировать)\s+(?:домен|сервер|vps|зону)",
    r"(?:buy|register|order|get|rent)\s+(?:a\s+|the\s+|your\s+)?(?:domain|server|vps)\s+(?:only\s+)?(?:at|from|in)\s+\S+",
    r"(?:domain|server|vps)\s+only\s+(?:in|at|from)\s+\S+",
    r"only\s+in\s+the\s+\.?\w+\s+zone",
    r"russian\s+(?:domain|registrar|hosting|zone)",
    r"(?:we\s+recommend|better|best)\s+(?:to\s+)?(?:buy|take|choose|register)\s+(?:a\s+)?(?:domain|server|vps|zone)",
    r"(?:возьмите|купите|закажите|арендуйте)\s+(?:vps|сервер)\s+у\s+\S+",
    r"(?:take|buy|order|get|rent)\s+(?:a\s+)?(?:vps|server)\s+(?:from|at)\s+\S+",
    # страна как единственный путь: «Сервер нужен в Нидерландах», «нужен VPS в Германии» (не «сервер не в России»)
    # «первый сервер нужен в России» — схема каскада, не навязывание (REV-texts-2)
    r"(?:сервер|vps)\w*\s+нужен\s+(?:именно\s+)?в\s+(?!Росси)(?-i:[А-ЯЁ])\w+",
    r"нуж(?:ен|на)\s+(?:сервер|vps)\s+(?:именно\s+)?в\s+(?!Росси)(?-i:[А-ЯЁ])\w+",
    r"(?:server|vps)\s+(?:must|should|has\s+to|needs\s+to)\s+be\s+(?:located\s+)?in\s+(?-i:[A-Z])\w+",
    # регистратор — когда его советуют («домен на reg.ru», «через nic.ru»), а не просто называют («доступ к nic.ru»)
    r"(?:\b(?:на|у|через|в|at|from|on|via|with)\s+)(?:reg\.ru|рег\.ру|nic\.ru|ru-center|r01\.ru|namecheap|godaddy|porkbun)",
)
_PUSH = re.compile("(?i)" + "|".join("(?:%s)" % m for m in PUSH_MARKERS))

# 6. сущность учёта — «пользователь», не «человек» (владелец 24.10: «заменим везде человек на пользователя, а у
# пользователя могут быть устройства»); слово внутри другого («наблюдений»), ключи (nav.people), заполнители
# ({people}), имена в коде (people.css, person:menu) — не тексты
_PEOPLE_RU = re.compile(r"(?i)(?<![А-Яа-яЁё])(человек(?:а|у|ом|е)?|люд(?:и|ей|ям|ьми|ях))(?![А-Яа-яЁё])")
_PEOPLE_EN = re.compile(r"(?i)(?<![A-Za-z_.:/#{-])(person(?:'s|’s)?|persons|people(?:'s|’s)?)(?![A-Za-z_-]|[.:][A-Za-z])")
_PEOPLE_RU_MANY = re.compile(r"(?i)(?<![А-Яа-яЁё])(люд(?:и|ей|ям|ьми|ях))(?![А-Яа-яЁё])")
_PEOPLE_EN_MANY = re.compile(r"(?i)(?<![A-Za-z_.:/#{-])(people(?:'s|’s)?)(?![A-Za-z_-]|[.:][A-Za-z])")
_PEOPLE_KINDS = ("catalog", "web", "sh", "code", "mcp")


def people_findings(it):
    if it.kind not in _PEOPLE_KINDS:
        return []
    if it.kind == "mcp":
        rxs = (_PEOPLE_RU_MANY,) if it.lang == "ru" else (_PEOPLE_EN_MANY,)
    else:
        rxs = (_PEOPLE_RU,) if it.lang == "ru" else (_PEOPLE_EN,) if it.lang == "en" else (_PEOPLE_RU, _PEOPLE_EN)
    return [Finding("people", it.src, it.key, m.group(1)) for rx in rxs for m in rx.finditer(it.text)]


# 7. роль группы и выхода «ru» — «внутренняя группа / внутренний выход (ключ)», EN domestic group / exit (key), не
# «российская группа» / Russian group (владелец 29.10: «везде по продукту переименовать "российские группы" во
# "внутренняя группа"»). Факты про страну — не роль: «российские сайты», «российских адресов», «российских
# провайдеров», «Российской Федерации», «в российском App Store», Russian sites / networks / Federation, ⚠РФ;
# метку «РФ» / RU у группы и выхода 3.3.7.13 заменила значком роутера (K.domMark, tests/test_dom_mark.py). Ловит
# прилагательное перед «групп… / выход… / ключ…» (и через «и / или / ↔ зарубежн…»), пару ролей «зарубежная ↔
# российская», «… — российская:» и подпись роли одним словом. Имя языка (lang.name.ru: Russian) — не роль.
_DOMESTIC_RU = re.compile(
    r"(?i)(?<![А-Яа-яЁё])(российск[а-яё]*(?:\s*(?:,|и|или|↔|/)\s*зарубежн[а-яё]*)?\s+(?:групп|выход|ключ)[а-яё]*"
    r"|зарубежн[а-яё]*\s*(?:,|и|или|↔|/)\s*российск[а-яё]*"
    r"|—\s*российск(?:ая|ий|ие)(?![А-Яа-яЁё])\s*:"
    r"|в\s+[«\"„“]?внутренн[а-яё]*)")               # З4 REV-score-rename: «во внутренних», не «в «Внутренних»»
_DOMESTIC_EN = re.compile(
    r"(?i)\b(Russian\s+(?:groups?|exits?|keys?)"
    r"|(?:foreign|abroad)\s*(?:,|and|or|↔|/)\s*(?:a\s+)?Russian"
    r"|Russian\s*(?:,|and|or|↔|/)\s*(?:a\s+)?(?:foreign|abroad)"
    r"|is\s+Russian\s*:)(?![A-Za-z])")
_DOMESTIC_WORD = re.compile(r"(?i)\s*(?:российск[а-яё]*|Russian)\s*")
_DOMESTIC_KINDS = ("catalog", "web", "sh", "code", "mcp")
DOMESTIC_NOT_ROLE_KEYS = ("lang.name.ru",)          # «Russian» — имя языка в выборе языка


def domestic_findings(it):
    if it.kind not in _DOMESTIC_KINDS:
        return []
    rxs = (_DOMESTIC_RU,) if it.lang == "ru" else (_DOMESTIC_EN,) if it.lang == "en" else (_DOMESTIC_RU, _DOMESTIC_EN)
    out = [Finding("domestic", it.src, it.key, m.group(1)) for rx in rxs for m in rx.finditer(it.text)]
    if it.kind in ("catalog", "web", "sh") and it.key not in DOMESTIC_NOT_ROLE_KEYS and _DOMESTIC_WORD.fullmatch(it.text):
        out.append(Finding("domestic", it.src, it.key, it.text.strip()))
    return out


# З4 REV-score-rename: подпись и ссылка — соседние ключи, код склеивает их («… — в» + «„Внутренних группах“»); перед
# «внутренн…» нужно «во». Порядок — как в файле словаря.
_DANGLING_V = re.compile(r"(?<![А-Яа-яЁё])в\s*$")
_STARTS_DOMESTIC = re.compile(r"(?i)^\s*[«\"„“]?внутренн")


def domestic_glue_findings(pairs):
    out = []
    for kind, label, docs in pairs:
        if kind not in ("catalog", "web"):
            continue
        rows = list(docs["ru"].items())
        for (k1, v1), (_k2, v2) in zip(rows, rows[1:]):
            if _DANGLING_V.search(v1) and _STARTS_DOMESTIC.match(v2):
                out.append(Finding("domestic", label.replace("{ru,en}", "ru"), k1, "в " + v2.strip()))
    return out


# 4. пары
_PRINTF = re.compile(r"%(?:%|[-+0#]*[0-9*]*(?:\.[0-9*]+)?[sdiouxXfFeEgGcbq])")


def _holes(kind, text):
    if kind == "sh":
        return ["%" + h[-1] for h in _PRINTF.findall(text) if h != "%%"]
    out = []
    try:
        for _lit, name, _spec, _conv in string.Formatter().parse(text):
            if name is not None:
                out.append(re.split(r"[.\[]", name, maxsplit=1)[0])
    except ValueError:
        out = sorted(re.findall(r"\{(\w+)\}", text))
    return sorted(set(out))


def pair_findings(pairs):
    out = []
    for kind, label, docs in pairs:
        ru, en = docs["ru"], docs["en"]
        for k in sorted(set(ru) ^ set(en)):
            out.append(Finding("keys", label, k, "нет в EN" if k in ru else "нет в RU"))
        for k in sorted(set(ru) & set(en)):
            a, b = ru[k], en[k]
            if (a == "") != (b == ""):          # пробел — тоже текст (разделитель тысяч в RU — « »)
                out.append(Finding("empty", label, k, "пусто в RU" if a == "" else "пусто в EN"))
            ha, hb = _holes(kind, a), _holes(kind, b)
            if ha != hb:
                out.append(Finding("holes", label, k, "RU %s ≠ EN %s" % (" ".join(ha) or "—", " ".join(hb) or "—")))
    return out


# install.sh: строка вывода — echo, printf, read -p или кириллица; рабочие адреса и числа кода — не тексты
_INSTALL_OUTPUT = re.compile(r"\b(?:echo|printf|read\s+-\w*p)\b|[А-Яа-яЁё]")


def text_findings(it):
    if it.kind == "mcp":
        return people_findings(it) + domestic_findings(it)   # описания MCP проверяют только people и domestic
    out = people_findings(it) + domestic_findings(it)
    t = it.text

    def add(rule, match):
        out.append(Finding(rule, it.src, it.key, match.strip()))

    for m in _DOMAIN.finditer(t):
        if not _not_a_domain(t, m):
            add("domain", m.group(1))
    for m in _ZONE.finditer(t):
        add("zone", m.group(0))
    output = it.kind != "install" or bool(_INSTALL_OUTPUT.search(t))
    if output:
        for m in _IPV4.finditer(t):
            if not _ip_is_example_or_service(m.group(1)) and not _is_version(t, m):
                add("ip", m.group(1))
    if it.kind in _IS_PROMPT_SRC:
        for m in _YN.finditer(t):
            add("yn", m.group(0))
        if it.kind == "install":
            for m in _YN_CODE.finditer(t):
                add("yn", m.group(0))
    if output and it.kind != "code":
        ru = it.lang == "ru" or (it.lang is None and re.search(r"[А-Яа-яЁё]", t))
        if ru:
            for m in _RU_DEC_POINT.finditer(t):
                add("num-dec", m.group(0))
            for m in _RU_GLUED.finditer(t):
                add("num-unit", m.group(0))
            for m in _RU_LATIN.finditer(t):
                add("num-unit", m.group(0))
        else:
            for m in _EN_DEC_COMMA.finditer(t):
                if not _EN_THOUSANDS.fullmatch(m.group(1)):
                    add("num-dec", m.group(0))
            for m in _EN_GLUED.finditer(t):
                add("num-unit", m.group(0))
        for m in _PCT_SPACE.finditer(t):
            add("pct", m.group(0))
    for m in _PUSH.finditer(t):
        add("push", m.group(0))
    return out


def findings(root=ROOT):
    pairs = all_pairs(root)
    out = pair_findings(pairs) + domestic_glue_findings(pairs)
    for it in items(root, pairs):
        out.extend(text_findings(it))
    return out


# ── исключения ──────────────────────────────────────────────────────────────


def _esc(s):
    return s.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n")


def _unesc(s):
    return re.sub(r"\\(.)", lambda m: {"t": "\t", "n": "\n"}.get(m.group(1), m.group(1)), s)


def read_exceptions(path=EXCEPTIONS):
    """Counter{Finding: сколько} и {Finding: причина}. Строки «#…» и пустые — пропуск."""
    cnt, why = collections.Counter(), {}
    if not os.path.isfile(path):
        return cnt, why
    for n, line in enumerate(_read(path).splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 5 or not parts[4].strip():
            raise ValueError("%s:%d: нужно 5 колонок через TAB и причина" % (path, n))
        f = Finding(*(_unesc(p) for p in parts[:4]))
        cnt[f] += 1
        why[f] = parts[4]
    return cnt, why


def format_exception(f, why):
    return "\t".join(_esc(x) for x in (f.rule, f.src, f.key, f.match)) + "\t" + why


def compare(found, allowed):
    """(новые, устаревшие) — списки Finding с повторами."""
    have = collections.Counter(found)
    return sorted((have - allowed).elements()), sorted((allowed - have).elements())


def show(f):
    return "%-8s %s%s  «%s»" % (f.rule, f.src, (" " + f.key) if f.key else "", f.match)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--exceptions", default=EXCEPTIONS)
    ap.add_argument("--all", action="store_true", help="все нарушения, вместе с исключёнными")
    ap.add_argument("--baseline", action="store_true", help="строки файла исключений для всех нарушений")
    a = ap.parse_args(argv)
    found = findings(a.root)
    if a.baseline:
        _, why = read_exceptions(a.exceptions)
        for f in sorted(found):
            print(format_exception(f, why.get(f, "TODO: причина")))
        return 0
    if a.all:
        for f in sorted(found):
            print(show(f))
        print("всего: %d" % len(found))
        return 0
    allowed, _ = read_exceptions(a.exceptions)
    new, stale = compare(found, allowed)
    for f in new:
        print("НОВОЕ   " + show(f))
    for f in stale:
        print("УБРАТЬ  " + show(f) + "  (исключение больше не нужно)")
    print("нарушений: %d, в исключениях: %d, новых: %d, устаревших исключений: %d"
          % (len(found), sum(allowed.values()), len(new), len(stale)))
    return 1 if new or stale else 0


if __name__ == "__main__":
    sys.exit(main())
