"""Static files of the web panel (docs/k3/SPEC-wave3.md §3.4, work package WP-P2): lib/kaskad/panel/static/.

Node-free checks of what must stay true without a browser: the page loads only its own files (CSP
``default-src 'self'; style-src 'self'; script-src 'self'``), has no inline handlers, scripts or styles, every
text key used by app.js exists in i18n-ru.js (and every key there is used), nothing builds DOM from strings,
the API paths app.js calls are the ones the spec lists, the licences are in the delivery. Where ``node`` is
installed, the syntax of every script is checked too (skipped without it).
"""

import importlib.util
import json
import os
import re
import shutil
import subprocess
import unittest
from html.parser import HTMLParser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "lib", "kaskad", "panel", "static")


def read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


def pages_files(suffix):
    """Names (``pages/<имя>``) of the lazy sections' files ending in ``suffix`` (F0, UX-FOUNDATION §3.3)."""
    d = os.path.join(STATIC, "pages")
    return ["pages/" + n for n in sorted(os.listdir(d)) if n.endswith(suffix)] if os.path.isdir(d) else []


def panel_code():
    """The code of the panel (F0): kit.js, templates.js, app.js and the lazy sections pages/<id>.js."""
    return ["kit.js", "templates.js", "app.js"] + [n for n in pages_files(".js") if "-i18n-" not in n]


def panel_dicts():
    """The Russian dictionaries of the panel's code: the core, Kit and the lazy sections."""
    return ["i18n-ru.js", "kit-i18n-ru.js"] + pages_files("-i18n-ru.js")


def sections_of(app):
    """[(id, label, lazy, i18n)] of the inventory SECTIONS of app.js."""
    body = app[app.index("  const SECTIONS = ["):app.index("  const SECTION_BY = ")]
    return [(m.group(1), m.group(2), None if m.group(3) == "null" else m.group(3).strip('"'), m.group(4) == "true")
            for m in re.finditer(r'\{ id: "([a-z0-9-]+)", label: "([A-Za-z0-9_.]+)", lazy: (null|"[^"]+"), i18n: (true|false)', body)]


def strip_comments(js):
    """Source without /* … */ and // … comments (good enough for these files: no regex literals with //)."""
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    return re.sub(r"(?m)(^|[^:\"'\\])//.*$", r"\1", js)


class Page(HTMLParser):
    def __init__(self):
        HTMLParser.__init__(self)
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    handle_startendtag = handle_starttag


def parse_index():
    p = Page()
    p.feed(read("index.html"))
    return p.tags


class IndexHtmlTest(unittest.TestCase):
    def test_loads_only_its_own_files_with_the_version_placeholder(self):
        refs = []
        for tag, a in parse_index():
            if tag == "script" and a.get("src"):
                refs.append(a["src"])
            if tag == "link" and a.get("href"):
                if a["href"] == "data:,":                      # an empty icon: no request for /favicon.ico
                    continue
                refs.append(a["href"])
        self.assertTrue(refs)
        for ref in refs:
            m = re.match(r"^([A-Za-z0-9._-]+)\?v=__KASKAD_V__$", ref)
            self.assertIsNotNone(m, "внешний или без версии: %r" % ref)
            self.assertTrue(os.path.isfile(os.path.join(STATIC, m.group(1))), "нет файла %s" % m.group(1))
        names = [re.match(r"^([^?]+)", r).group(1) for r in refs]
        for need in ("boot.js", "styles.css", "icons.js", "i18n-ru.js", "app.js", "kit.js", "templates.js", "kit-i18n-ru.js"):
            self.assertIn(need, names)
        # F0 (UX-FOUNDATION §3.3): русские словари → ux.js → kit.js → templates.js → app.js — по порядку (defer)
        order = [n for n in names if n.endswith(".js") and n != "boot.js"]
        self.assertEqual(order, ["icons.js", "stream.js", "i18n-ru.js", "ux-i18n-ru.js", "kit-i18n-ru.js", "ux.js", "kit.js", "templates.js",
                                 "find.js", "xt.js", "spark.js", "app.js"])   # find.js — модель поиска по панели (3.3.6), xt.js — строка списка с раскрытием (UI-4), spark.js — линия активности (UI-B)
        self.assertFalse([n for n in names if n.startswith("pages/")])     # разделы из pages/ app.js грузит сам
        self.assertNotIn("dev-mock.js", names)                  # данные для проверки подгружает app.js сам и только при ?mock=1

    def test_no_inline_scripts_styles_or_handlers(self):
        for tag, a in parse_index():
            self.assertNotEqual(tag, "style")
            if tag == "script":
                self.assertTrue(a.get("src"), "вложенный скрипт: CSP script-src 'self' его не пустит")
            for k in a:
                self.assertFalse(k.startswith("on"), "обработчик в разметке: %s" % k)
                self.assertNotEqual(k, "style", "атрибут style в разметке: CSP style-src 'self'")

    def test_every_section_has_a_page_in_app_js(self):
        """F0: the inventory SECTIONS of app.js is the list of sections — those of app.js (lazy: null) have their
        <section> in index.html and PAGES.<id>; those of pages/ (lazy: "pages/<id>.js") have the file (and both
        dictionaries when i18n: true) and no <section> in index.html (buildPage adds it). The menu names only them."""
        app = read("app.js")
        sections = [a["data-page"] for tag, a in parse_index() if tag == "section" and "data-page" in a]
        pages = re.findall(r"^\s*PAGES\.(\w+) = \{", app, flags=re.M)
        inv = sections_of(app)
        self.assertEqual(len(inv), len(re.findall(r'(?m)^    \{ id: "', app[app.index("  const SECTIONS = ["):app.index("  const SECTION_BY = ")])))
        # страница только телефона (phone: true — «Ещё», 3.3.6): своей <section> в index.html нет, её добавляет buildPage
        phone = re.findall(r'\{ id: "([a-z0-9-]+)"[^\n]*phone: true', app[app.index("  const SECTIONS = ["):app.index("  const SECTION_BY = ")])
        self.assertEqual(phone, ["more"])
        pages = [x for x in pages if x not in phone]
        core = [i for i, _l, lazy, _n in inv if lazy is None and i not in phone]
        lazy = [(i, lz, n) for i, _l, lz, n in inv if lz is not None]
        self.assertEqual(sorted(sections), sorted(pages))
        self.assertEqual(sorted(sections), sorted(core))
        self.assertEqual(len(sections), 8)
        self.assertTrue(lazy)
        for i, lz, n in lazy:
            self.assertEqual(lz, "pages/%s.js" % i)
            self.assertTrue(os.path.isfile(os.path.join(STATIC, lz)), lz)
            self.assertNotIn(i, sections)
            self.assertRegex(read(lz), r'K\.page\((?:"%s"|ID), \{' % re.escape(i))       # раздел регистрирует себя сам
            for lang in ("ru", "en"):
                self.assertEqual(os.path.isfile(os.path.join(STATIC, "pages", "%s-i18n-%s.js" % (i, lang))), n, i)
        nav = re.findall(r'\{ id: "(\w+)", icon: "[a-z0-9-]+", label: "([A-Za-z0-9_.]+)"', app[app.index("  const NAV = ["):app.index("  const NAV_SOON")])
        labels = dict((i, l) for i, l, _lz, _n in inv)
        # nav2 r1 (владелец 23.10): «Диагностики» в меню нет — её части в «Обзоре › Проверка сервера»; запись описи
        # осталась (moved: "overview") — ей грузится pages/diag.js
        self.assertEqual(len(nav), 8)
        self.assertNotIn("diag", [i for i, _l in nav])
        self.assertIn("diag", labels)
        for i, label in nav:
            self.assertEqual(labels.get(i), label, i)
        # будущий раздел первого уровня — серым («Сценарии · этап 3»): в меню есть, раздела (описи) нет
        soon = re.findall(r'\{ id: "(\w+)", icon: "[a-z0-9-]+", label: "([A-Za-z0-9_.]+)", soon: "([a-z0-9.]+)"', app[app.index("  const NAV_SOON"):app.index("  const PHONE_TABS")])
        self.assertEqual(soon, [("scenarios", "n2.scenarios", "n2.stage3")])
        self.assertNotIn("scenarios", labels)

    def test_no_absolute_urls(self):
        html = read("index.html")
        self.assertIsNone(re.search(r"(https?:)?//[a-z0-9.-]+\.[a-z]{2,}", re.sub(r"<!--.*?-->", "", html, flags=re.S)))


class I18nTest(unittest.TestCase):
    """The texts of the panel's code (F0: app.js, kit.js, templates.js, pages/*.js) and its Russian dictionaries (the
    core, Kit, the sections): one set of keys — a key of Kit or a section equal to one of the core is a duplicate."""

    @classmethod
    def setUpClass(cls):
        cls.dicts = [(n, read(n)) for n in panel_dicts()]
        cls.dict_text = "\n".join(text for _n, text in cls.dicts)
        cls.keys = re.findall(r'^\s*"([A-Za-z0-9_.]+)":\s*"', cls.dict_text, flags=re.M)
        cls.js = "\n".join(strip_comments(read(n)) for n in panel_code())
        cls.vals = dict(re.findall(r'^\s*"([A-Za-z0-9_.]+)":\s*"(.*)",?$', cls.dict_text, flags=re.M))

    def used_literals(self):
        ns = set(k.split(".")[0] for k in self.keys)
        out = set()
        for lit in re.findall(r'"([a-z][A-Za-z0-9]*(?:\.[A-Za-z0-9_]+)+)"', self.js):
            if re.search(r"\.(js|css|html|json|svg|txt)$", lit):
                continue
            if lit.split(".")[0] in ns:
                out.add(lit)
        return out

    def dynamic_keys(self):
        """Keys app.js assembles from lists: kinds of inbounds and devices, systems, events, route sources."""
        def arr(name):
            m = re.search(r"const %s = \[([^\]]*)\]" % name, self.js)
            self.assertIsNotNone(m, name)
            return re.findall(r'"([^"]+)"', m.group(1))
        out = set()
        for kind in arr("KINDS"):
            out.update(("ibt." + kind, "ibs." + kind, "ibd." + kind))
        out.update("kind." + k for k in arr("DEVICE_KINDS"))
        out.update("plat." + p for p in arr("PLATFORMS"))
        ev = re.search(r"const EV_ICON = \{(.*?)\};", self.js, flags=re.S)
        self.assertIsNotNone(ev)
        out.update("ev." + k for k in re.findall(r"(\w+): \[", ev.group(1)))
        out.add("ev.other")
        out.update("route.from." + s for s in ("default", "user", "device"))
        out.update("pp.rulesFrom." + s for s in ("default", "user", "device"))
        out.update("conn.tip." + s for s in ("init", "live", "poll", "lost"))
        # A6 (SPEC-node-score §10.1 п. 7): подписи видов здоровья узла — их берёт по виду карточка «Уведомления» (T8, A7)
        from kaskad.gw import hostmon
        out.update("nt.k." + k for k in hostmon.HOST_KINDS)
        # 3.3.7.15 «Адреса сервера» (pages/addresses.js): коды строк страницы (gw/addresses.page), дорожки полосы ролей,
        # причины выдачи IPv6 (addresses.give6_state) и «напрямую» (mip.direct_plan)
        out.update("adr.inS." + k for k in ("accept", "accept_give", "reserve", "off"))
        out.update("adr.seg." + k for k in ("accept", "accept_give", "reserve", "off"))
        out.update("adr.lane." + k for k in ("in", "give", "out", "panel"))
        out.update("adr.give.why." + k for k in ("off", "gone", "deprecated", "no_route6", "echo_other"))
        out.update("adr.oWhy." + k for k in ("gone", "not_out", "route"))
        # BC5: карточка замера (pages/speed.js) — пометки из своего списка NOTES, подписи фаз и плиток по фазе, слово
        # шкалы и причина «нет строки под нагрузкой» — коды SpeedCalc.loadScale / SpeedCalc.loaded, пределы — коды 429
        sp = read("pages/speed.js")
        notes = re.search(r"const NOTES = \[([^\]]*)\]", sp)
        self.assertIsNotNone(notes)
        out.update("panel.speed.note." + n for n in re.findall(r'"([^"]+)"', notes.group(1)))
        out.update("panel.speed.k." + k for k in ("ping", "jitter", "down", "up"))
        out.update("panel.speed.phase." + k for k in ("idle", "down", "up"))
        out.update("panel.speed.word." + k for k in ("barely", "noticeably", "a_lot"))
        out.update("panel.speed.why." + k for k in ("one_conn", "cpu", "queue", "idle"))
        # «Поделиться», «чьи это цифры» (b5cd50fb): имя точки теста 8 — «dg.sh.pt.» + id точки до «-» (каталог сервера
        # bench/channel-targets.json и своя точка «allowed»), DNS проб — список DNS окна (share.DNS_OF)
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib", "kaskad", "bench",
                               "channel-targets.json"), encoding="utf-8") as f:
            cat = json.load(f)
        ids, todo = [], [cat.get("http"), cat.get("iperf3")]
        while todo:                                       # {down|up: {ru|abroad: [точки]}}, {ru: [точки]}
            x = todo.pop()
            if isinstance(x, dict) and isinstance(x.get("id"), str):
                ids.append(x["id"])
            elif isinstance(x, (dict, list)):
                todo.extend(x.values() if isinstance(x, dict) else x)
        self.assertTrue(ids)
        out.update("dg.sh.pt." + i.split("-")[0] for i in ids + ["allowed"])
        dns = re.search(r"const DNS = \[([^\]]*)\]", read("pages/diag-share.js"))
        self.assertIsNotNone(dns)
        out.update("dg.sh.dns." + d for d in re.findall(r'"([^"]+)"', dns.group(1)))
        out.update("panel.speed.lim." + k for k in ("hour", "day"))
        # круг 1, Т1 (pages/ovb.js): звенья радара из LINKS, пояса итога 0–4, цвета дней,
        # метрики здоровья из MET, уровни и строки вердикта собираются по значению
        ov = read("pages/ovb.js")
        links = re.search(r"const LINKS = \[([^\]]*)\]", ov)
        self.assertIsNotNone(links)
        lk = re.findall(r'"([^"]+)"', links.group(1))
        out.update("ovb.ln." + k for k in lk)
        out.update("ovb.lns." + k for k in ("stab", "loss", "crypto"))
        out.update("ovb.band.%d" % i for i in range(5))
        out.update("ovb.dc." + k for k in ("green", "yellow", "red", "grey"))
        out.update("ovb.m." + k for k in re.findall(r'\{ id: "(\w+)", chk:', ov))
        out.update("ovb.lv." + k for k in ("ok", "warn", "crit", "wait"))
        out.update("ovb.v." + k for k in ("ok", "warn", "bad", "wait", "off", "nohc"))    # verdictOf: состояние словами без числа (28.10; off, nohc — REV-it1 №3)
        out.update("ovb.hh." + k for k in ("ok", "warn", "crit", "wait", "off"))
        # «Режим стрима»: stream.js, give.js, ux.js и graph.js берут тексты из словаря панели (KaskadStream.bind) — T("stm.…");
        # список «Чего режим не закрывает» — stm.nc1…stm.nc6 (streamCard; nc6 — «И имена» по общему слову, REV-stream З-4)
        for name in ("stream.js", "give.js", "ux.js", "graph.js"):
            out.update(re.findall(r'["\'](stm\.[A-Za-z0-9.]+)["\']', read(name)))
        out.update("stm.nc%d" % i for i in range(1, 7))
        return out

    def test_no_duplicate_keys(self):
        self.assertEqual(len(self.keys), len(set(self.keys)))

    def test_every_key_used_by_app_js_exists(self):
        have = set(self.keys)
        used = self.used_literals() | self.dynamic_keys()
        called = set(re.findall(r'\b(?:t|tn|tr|raw)\(\s*"([^"]+)"', self.js))
        called = set(k for k in called if not k.endswith("."))             # t("ev." + вид): собираемые ключи — в dynamic_keys
        missing = sorted((used | called) - have)
        self.assertEqual(missing, [], "в i18n-ru.js нет ключей: %s" % ", ".join(missing))

    def test_every_key_in_the_dictionary_is_used(self):
        used = self.used_literals() | self.dynamic_keys()
        unused = sorted(set(self.keys) - used)
        self.assertEqual(unused, [], "ключи словаря, которых нет в app.js: %s" % ", ".join(unused))

    def test_placeholders_match_the_call(self):
        """t("key", {a: …, b: …}) must pass every {name} of the text (a forgotten one would show as «{name}»)."""
        bad = []
        for m in re.finditer(r'\b(t|tn)\(\s*"([^"]+)"\s*,\s*(.*)', self.js):
            fn, key, rest = m.groups()
            text = self.vals.get(key)
            if text is None:
                continue
            need = set(re.findall(r"\{(\w+)\}", text))
            if fn == "tn":
                need.discard("n")
                om = re.match(r"[^,)]*,?\s*\{(.*)", rest)       # tn(key, n, {vars})
                rest = "{" + om.group(1) if om else ""
            if not need:
                continue
            om = re.match(r"\{(.*?)\}\s*\)", rest)
            if not om:
                bad.append("%s: vars not literal" % key)
                continue
            given = set()
            for part in om.group(1).split(","):
                part = part.strip()
                if part:
                    given.add(part.split(":")[0].strip())
            if need - given:
                bad.append("%s: нет %s" % (key, ", ".join(sorted(need - given))))
        self.assertEqual(bad, [])

    def test_neutral_wording(self):
        low = self.dict_text.lower()
        for word in ("обход",):                                  # тема владельца: шлюз — «переводчик протоколов»
            self.assertNotIn(word, low, "в текстах интерфейса нельзя: %s" % word)
        for name, text in self.dicts:
            self.assertNotIn("<", text.split("window.KASKAD_I18N", 1)[1], "%s: в текстах нет разметки" % name)

    def test_texts_are_russian(self):
        for key, val in self.vals.items():
            if key.startswith(("fmt.", "app.", "qr.app", "ibt.", "ibs.", "plat.", "sh.product", "lk.pHero", "login.loginPh", "lk.langEn", "st.ghShort",
                               "dg.sh.pt.", "dg.sh.dns.",               # имена точек и DNS (Cloudflare, OVH, Google)
                               "ep.rg.move", "ep.rg.path",
                               "adr.rowS", "adr.life.dynS", "adr.gV4", "adr.gV6", "adr.gV6none", "adr.from.nat",
                               "adr.from.slaac", "adr.det.sub", "ep.rg.moveOne")):                            # «4 IPv4 · 2 IPv6» — числа и имена протоколов            # F14: «{who} — {device}:», «{from} → {to}» — шаблоны имён
                continue
            self.assertTrue(re.search("[А-Яа-яЁё]", val), "нет русского текста у %s" % key)


class SecurityTest(unittest.TestCase):
    FILES = ("app.js", "boot.js", "icons.js", "qr.js", "dev-mock.js", "kit.js", "templates.js", "find.js", "xt.js", "spark.js", "stream.js") + tuple(pages_files(".js"))

    def test_no_dom_from_strings_no_eval(self):
        bad = re.compile(r"\binnerHTML\b|\bouterHTML\b|insertAdjacentHTML|document\.write|\beval\(|new Function\(|"
                         r"setAttribute\(\s*[\"']style[\"']|\.setTimeout\(\s*[\"']|DOMParser|createContextualFragment")
        for name in self.FILES:
            js = strip_comments(read(name))
            m = bad.search(js)
            self.assertIsNone(m, "%s: %s" % (name, m and m.group(0)))

    def test_no_network_addresses(self):
        for name in ("app.js", "boot.js", "icons.js", "qr.js", "kit.js", "templates.js", "find.js", "xt.js", "spark.js", "stream.js") + tuple(pages_files(".js")):
            js = strip_comments(read(name))
            for m in re.finditer(r"[\"'](?:https?:)?//[^\"']+[\"']", js):
                self.assertEqual(m.group(0).strip("\"'"), "http://www.w3.org/2000/svg", "%s: %s" % (name, m.group(0)))

    def test_no_interval_polling(self):
        """Следующий запрос — setTimeout ПОСЛЕ ответа; setInterval — только часы плашки идущего действия."""
        js = strip_comments(read("app.js"))
        self.assertEqual(len(re.findall(r"\bsetInterval\(", js)), 1)
        self.assertIn("setInterval(busyClock, 1000)", js)

    def test_mutations_carry_the_csrf_header_and_json(self):
        js = read("app.js")
        self.assertIn('headers["X-Kaskad-Admin"] = "1"', js)
        self.assertIn('"Content-Type"', js)

    def test_mock_is_loaded_only_by_request(self):
        js = read("app.js")
        self.assertIn('QS.get("mock") === "1"', js)
        self.assertIn('loadScript("dev-mock.js")', js)
        mock = read("dev-mock.js")
        self.assertIn("?mock=1", mock)                          # как включить — в первом комментарии
        self.assertLess(mock.index("?mock=1"), 1200)

    def test_style_only_through_cssom(self):
        css = read("styles.css")
        self.assertIsNone(re.search(r"@import|url\(\s*[\"']?https?:|//[a-z0-9.-]+\.[a-z]{2,}/", css))
        self.assertIn("Apache-2.0", css)                         # заголовок HeroUI оставлен


class ApiPathsTest(unittest.TestCase):
    """The paths app.js calls — exactly the table of docs/k3/SPEC-wave3.md §3.2."""
    ALLOWED = [
        r"/api/overview", r"/api/forwards", r"/api/forwards/", r"/api/gw", r"/api/gw/(enable|disable|host|route)",
        r"/api/gw/inbounds", r"/api/gw/inbounds/", r"/api/gw/people", r"/api/gw/users", r"/api/gw/users/",
        r"/api/gw/devices", r"/api/gw/devices/", r"/api/gw/exits", r"/api/gw/exits/", r"/api/gw/exits/preview", r"/api/rules",
        r"/api/rules/(update|profile|blocks|consent|ru-via)", r"/api/panel", r"/api/appearance", r"/api/intro", r"/api/events", r"/api/login", r"/api/logout", r"/api/session",
        r"/api/",
        # /api/rules/consent — «Подтвердить» ⚠РФ (SPEC-stage1-rf-mark-builtin §5.1, R4); /api/rules/ru-via — выбор
        # российской группы пользователей и устройств одной записью (T15 Q6, SPEC-groups §8.6)
        # контракт: возможности панели — порядок выдачи, доступ к панели, ключ GitHub
        r"/api/gw/offers", r"/api/panel/(password|public|settings|link)", r"/api/github", r"/api/github/check",
        # волна 4.0-B: домен и сертификат (docs/k3/SPEC-wave4.md §4.1)
        r"/api/site", r"/api/site/(domain|dns|email|cert|use)", r"/api/site/cert/renew",
        # волна 4.0-C: сайт-маскировка (docs/k3/SPEC-wave4.md §7.1)
        r"/api/site/(mode|categories|templates|template|panel-url|check)", r"/api/site/panel-path/rotate",
        # 3.2.21: «жив ли шаблон» — пакетная проверка видимой страницы каталога
        r"/api/site/templates/check",
        # движок AmneziaWG (k3/SPEC-awg-engine.md §4)
        r"/api/gw/awg/(engine|check|export|import)",
        # безопасный MTU: узкая карта сервера (k3/SPEC-awg-engine.md §6.1)
        r"/api/gw/mtu",
        # подписки выходов и копия настроек (docs/k3/SPEC-subs-backup.md)
        r"/api/gw/exit-subs", r"/api/gw/exit-subs/", r"/api/gw/exit-subs/refresh",
        r"/api/backup/(summary|export|preview|import|undo)",
        # VLESS как вход: сайт маскировки, ключи узла, режим по сайту узла (docs/k3/SPEC-vless-inbound.md)
        r"/api/gw/vless/(masks|masks/check|mask|rekey|sync)",
        # выбор режима VLESS человеком: TLS | Reality с согласием (k3/handoff/SPEC-vless-tls-default.md §3.5, §4.2)
        r"/api/gw/vless/mode",
        # что спросит действие VLESS (согласие или цену) — чтением заранее, без 409 (§4.2, T-3378 Н-2)
        r"/api/gw/vless/ask",
        r"/api/gw/hy2", r"/api/gw/hy2/apply",
        # язык сервера (3.3.2, k3/handoff/I18N-3.3.2.md)
        r"/api/lang",
        # 3.3.3 UX-333 (kaskad.panel.ux): бот, ключ ИИ-агента, журнал и перезапуск протокола
        r"/api/bot", r"/api/bot/(check|test|proxy)", r"/api/mcp", r"/api/mcp/keys", r"/api/mcp/keys/", r"/(log|restart)",
        # «Нет сервера за рубежом?» — партнёрские хостинги из product.HOSTING (OWNER-FEEDBACK №45)
        r"/api/hosting",
        # этап 1 (T8 на моке, контракт SE §7.9): политика и группы выходов, общий тест скорости, уведомления
        r"/api/gw/policy", r"/api/gw/policy/(reset|check-url|hint-seen)", r"/api/gw/groups", r"/api/gw/groups/", r"/api/gw/probes/speed",
        r"/api/notify",
        # UI-A1: «Настройки › Время» — часовой пояс узла (SE §7.9, docs/k3/SPEC-stage1-exits/11-ui-api-bot.md)
        r"/api/settings/time",
        # лёгкое здоровье узла и его история (kaskad.panel.host, k3/handoff/SPEC-node-score.md §3.8, A7)
        r"/api/host/(health|history|score|share)", r"/api/host/day/",     # 6C (П4-а): «от обычного», день полосы (кривых нет, NS-D34); share — «Поделиться» (C10)
        r"/api/host/dev-rule",                                     # UI-C: запись порога ▲/▼ (host set --dev-threshold)
        # расписание и история проверок (kaskad.panel.host, SPEC-node-score-schedule-share §2.6, §3.4; экраны B15)
        r"/api/host/bench", r"/api/host/bench/", r"/api/host/bench/(schedule|schedule/preview|compare|export|price)",
        # активность людей и устройств (k3/handoff/SPEC-activity.md §5; сбор на сервере — с 3.3.7.9, до него — заглушка)
        r"/api/gw/activity",
        r"/api/gw/activity/settings",
        r"/api/gw/exits/activity",             # 3.3.7.10: линия «Выходов» — все выходы одним ответом
        # круг 1 Т1 (вариант Б «Обзора»): балл 0–1000 только панели, «Выходы · час» Сводки одним ответом
        r"/api/host/synth", r"/api/gw/exits/hour",
        # замер «от вашего устройства до сервера» (SPEC-browser-channel §4.2, BC5): пределы до «Проверить»; сам замер —
        # воркер speed-worker.js (start, ping, down, up, finish — tests/test_panel_contract.py WORKER_CALLS)
        r"/api/host/speed/budget",
        # 3.3.7.15 (вид №13 r2): «ждут новых настроек» (F1), ожидание безопасного применения (F7), «Сеть шлюза» (F6);
        # окно «Настройки протокола» — /api/gw/inbounds/<kind>/settings (префикс /api/gw/inbounds/ выше)
        r"/api/gw/reissue", r"/api/gw/pending", r"/api/gw/pending/confirm", r"/api/gw/net", r"/api/gw/net/(base|undo)",
        # 3.3.7.15 «Настройки › Сервер › Адреса» (SPEC-R1 §3 F10–F13, F12а): документ страницы и записи ролей адресов;
        # «Разложить заново» (F14) — /api/gw/groups/<gid>/regroup (под /api/gw/groups/)
        r"/api/gw/addresses", r"/api/gw/addresses/(in|in6|give|direct|out|exit|reset|spread)", r"/regroup",
    ]

    def test_paths(self):
        js = strip_comments(read("app.js"))
        found = set(re.findall(r'"(/api/[A-Za-z0-9/_-]*)"', js))
        self.assertGreater(len(found), 15)
        for p in found:
            self.assertTrue(any(re.match("^" + a + "$", p) for a in self.ALLOWED), "путь вне спецификации: " + p)
        for need in ("/api/events", "/api/gw/devices/", "/api/rules/update", "/api/login"):
            self.assertTrue(any(p.startswith(need) for p in found), need)


PANEL_CSS = ("tokens.css", "components.css", "styles.css", "give.css", "ux.css", "charts.css", "mobile.css", "find.css", "xt.css", "people.css", "check.css", "speed.css", "ovb.css", "grid.css", "pset.css", "regroup.css", "addresses.css", "stream.css")  # regroup.css, addresses.css — «Разложить заново» и «Адреса сервера» (3.3.7.15 V2); pset.css — «Настройки протокола» и «Сеть шлюза» (№13 r2, 3.3.7.15); grid.css — сетка графиков (Claude Design «Сетка на графиках r1», после charts.css и ovb.css); stream.css — «Режим стрима» (последним, PLAN-stream-mode); ovb.css — «Обзор» по варианту Б (круг 1, Т1); speed.css — карточка замера из браузера (BC5, после check.css); charts.css — графики этапа 1 (T8), mobile.css — телефон и планшет (3.3.6, после графиков: его правила для узкого экрана — последними), find.css — поиск по панели (3.3.6); порядок подключения в index.html (give.css — окно «Выдать настройки», 3.3.2; ux.css — общие компоненты UX-333, 3.3.3)


def panel_css():
    return "\n".join(read(n) for n in PANEL_CSS)


class CssTest(unittest.TestCase):
    def test_classes_used_by_app_js_exist(self):
        css = panel_css()
        js = "\n".join(strip_comments(read(n)) for n in panel_code())
        known = set(re.findall(r"\.((?:gx|ga|kk|gi)[A-Za-z0-9_-]*)", css))
        used = set(re.findall(r"(?<![A-Za-z0-9_-])((?:gx|ga|kk)-[A-Za-z0-9_-]+)", js))
        missing = sorted(c for c in used if not c.endswith("-") and c not in known)
        self.assertEqual(missing, [], "классы без стилей: %s" % ", ".join(missing))

    def test_tokens_and_phone_rules_present(self):
        css = panel_css()
        for need in ('[data-hero][data-theme="dark"]', '[data-hero][data-device="phone"]', "--accent-soft", ":focus-visible",
                     "prefers-reduced-motion", "min-height: 44px", "font-size: max(1rem, 16px)"):
            self.assertIn(need, css)

    def test_phone_rules_are_phone_only(self):
        # РФ-группы Ш0 (REV-plan-ru-groups, П-4 — только styles.css): правило для телефона начинается с
        # [data-device="phone"] — и каждый его селектор через запятую тоже; иначе второй селектор действует на любой
        # ширине (так на компьютере пропадали «Отклик» и «Устройств» у ключей «Дома» и верхняя граница фактов)
        css = re.sub(r"/\*.*?\*/", "", read("styles.css"), flags=re.S)
        bad = []
        for m in re.finditer(r"([^{}]+)\{", css):
            sels = [s.strip() for s in m.group(1).split(",") if s.strip()]
            if sels and sels[0].startswith('[data-device="phone"]') and not all(s.startswith('[data-device="phone"]') for s in sels):
                bad.append(", ".join(sels))
        self.assertEqual(bad, [], "селектор без [data-device=\"phone\"] в правиле телефона: %s" % bad)

    def test_design_system_files(self):
        """Дизайн-система (docs/k3/DESIGN-SYSTEM.md): токены и компоненты — отдельными файлами в порядке index.html,
        шрифты — свои (fonts/*.woff2, font-display: swap), одна система переменных без старых имён HeroUI."""
        names = [re.match(r"^([^?]+)", a["href"]).group(1) for tag, a in parse_index() if tag == "link" and a.get("rel") == "stylesheet"]
        self.assertEqual(names, list(PANEL_CSS))
        tokens, comp = read("tokens.css"), read("components.css")
        faces = re.findall(r"@font-face\s*\{([^}]*)\}", tokens)
        self.assertEqual(len(faces), 3)
        for f in faces:
            self.assertIn("font-display: swap", f)
            m = re.search(r"url\((fonts/[a-z]+\.woff2)\)", f)
            self.assertIsNotNone(m, f)
            self.assertTrue(os.path.isfile(os.path.join(STATIC, m.group(1))), m.group(1))
        for need in ("--text-2", "--text-3", "--surface-2", "--surface-3", "--success-text", "--danger-text", "--info-soft",
                     "--accent-text", "--r-tile", "--r-dialog", "--h-field", "--kv-label", "--font-head"):
            self.assertIn(need + ":", tokens)
        for cls in (".ga-btn--primary", ".ga-btn--danger-soft", '.ga-btn[aria-busy="true"]', ".gx-chip--off", ".gx-chip--checking",
                    ".gx-chip--none", ".gx-al--plain", ".gx-al__icon", ".gx-kv__row { display: contents; }", ".gx-kv__copy",
                    ".gx-kv__end", ".gx-card__actions", ".gx-card__danger", ".gx-cols"):
            self.assertIn(cls, comp)
        everything = panel_css() + read("graph.css") + read("app.js")
        for old in ("--muted)", "--default)", "--default-foreground", "--surface-secondary", "--surface-tertiary", "-soft-foreground",
                    "--overlay)", "--btn-h", "--r-sm)", "--snow", "--eclipse"):
            self.assertNotIn("var(" + old if old.endswith(")") else old, everything, "старая переменная: " + old)
        # информация — нейтральная: акцент не красит сообщения и статусы
        self.assertNotRegex(comp, r"\.gx-al--accent|\.gx-chip--success[^{]*\{[^}]*--accent")


class DeliveryTest(unittest.TestCase):
    def test_licences_and_files(self):
        for name in ("LICENSE-gravity-ui.txt", "LICENSE-heroui-apache-2.0.txt", "index.html", "boot.js", "app.js",
                     "styles.css", "icons.js", "i18n-ru.js", "qr.js", "dev-mock.js", "kit.js", "templates.js",
                     "kit-i18n-ru.js", "kit-i18n-en.js", "pages/dev-kit.js", "find.js", "find.css", "xt.js", "xt.css", "people.css", "spark.js", "stream.js", "stream.css"):
            self.assertTrue(os.path.isfile(os.path.join(STATIC, name)), name)
        self.assertIn("MIT License", read("LICENSE-gravity-ui.txt"))
        self.assertIn("Apache License", read("LICENSE-heroui-apache-2.0.txt"))
        self.assertIn("MIT", read("icons.js"))
        self.assertIn("gravity-ui/icons", read("icons.js"))
        for name in ("onest", "rubik", "jbmono"):                              # шрифты дизайн-системы: свои файлы, OFL рядом
            with open(os.path.join(STATIC, "fonts", name + ".woff2"), "rb") as f:
                body = f.read()
            self.assertEqual(body[:4], b"wOF2", name)
            self.assertLess(len(body), 60000, name)                             # подмножество: кириллица и латиница
        for lic in ("OFL-onest.txt", "OFL-rubik.txt", "OFL-jetbrains-mono.txt"):
            self.assertIn("SIL Open Font License", read(os.path.join("fonts", lic)))
        self.assertIn("raw.githubusercontent.com/google/fonts", read(os.path.join("fonts", "SOURCES.md")))

    def test_icons_used_by_app_js_exist(self):
        have = set(re.findall(r'^\s*"([a-z0-9-]+)": \[[FES],', read("icons.js"), flags=re.M))
        js = strip_comments(read("app.js"))
        used = set(re.findall(r"\bicon\(\s*\"([a-z0-9-]+)\"", js))
        used |= set(re.findall(r"\bicon:\s*\"([a-z0-9-]+)\"", js))
        for name in ("IB_ICON", "KIND_ICON", "TONE_ICON"):
            m = re.search(r"const %s = \{(.*?)\};" % name, js, flags=re.S)
            self.assertIsNotNone(m, name)
            used |= set(re.findall(r":\s*\"([a-z0-9-]+)\"", m.group(1)))
        m = re.search(r"const EV_ICON = \{(.*?)\};", js, flags=re.S)
        used |= set(re.findall(r"\[\"([a-z0-9-]+)\",", m.group(1)))
        missing = sorted(u for u in used if u not in have)
        self.assertEqual(missing, [], "значков нет в icons.js: %s" % ", ".join(missing))
        self.assertGreater(len(used), 30)

    def test_payload_embeds_the_static_files(self):
        spec = importlib.util.spec_from_file_location("k3_build_payload", os.path.join(ROOT, "tools", "build_payload.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        names = [n for n, _ in mod.collect(os.path.join(ROOT, "lib"))]
        for name in ("index.html", "boot.js", "app.js", "styles.css", "icons.js", "i18n-ru.js", "qr.js", "dev-mock.js",
                     "kit.js", "templates.js", "kit-i18n-ru.js", "kit-i18n-en.js", "pages/dev-kit.js", "find.js", "find.css", "xt.js", "xt.css", "people.css", "spark.js",
                     "pages/dev-kit-i18n-ru.js", "pages/dev-kit-i18n-en.js", "LICENSE-gravity-ui.txt", "LICENSE-heroui-apache-2.0.txt", "fonts/onest.woff2", "fonts/rubik.woff2",
                     "fonts/jbmono.woff2", "fonts/OFL-onest.txt", "fonts/OFL-rubik.txt", "fonts/OFL-jetbrains-mono.txt"):
            self.assertIn("kaskad/panel/static/" + name, names)
        self.assertNotIn("kaskad/panel/static/fonts/SOURCES.md", names)


class ReinstallTest(unittest.TestCase):
    """F-2 (тестер T-G1): «Удалить → Установить» протокола. Окно удаления говорит правду — порт и ключи сохранятся;
    после установки панель читает ``previous`` ответа: вернулись — тост, сменились — окно со списком устройств и
    кнопками «Выдать заново»."""

    def setUp(self):
        self.js = strip_comments(read("app.js"))
        self.vals = dict(re.findall(r'^\s*"([A-Za-z0-9_.]+)":\s*"(.*)",?$', read("i18n-ru.js"), flags=re.M))

    def fn(self, name):
        m = re.search(r"\n  function %s\(.*?\n  }\n" % name, self.js, flags=re.S)
        self.assertIsNotNone(m, name)
        return m.group(0)

    def test_install_reads_previous(self):
        inst = self.fn("openInstall")
        self.assertIn("const res = await act(() => API.inbounds.install(", inst)
        self.assertIn("if (res) afterInstall(r.title, res);", inst)
        self.assertNotIn('ok: t("ib.installed"', inst)                     # the toast is afterInstall's
        after = self.fn("afterInstall")
        for need in ("res.previous", "prev.restored", 't("ib.restored")', "reissueDialog(", 't("ib.installed", { title })'):
            self.assertIn(need, after)
        dlg = self.fn("reissueDialog")
        for need in ("openIssue(dev)", 't("ib.reissue")', 't("ib.reissueText", { title, what })', 'setPage("people")',
                     "x.user_name", "x.platform"):
            self.assertIn(need, dlg)
        # круг 1 №14: прежний порт сервер сам кладёт в поле (ops.port_offer: kept); подсказка — если не ответил.
        # Т3 (Б r3): прежний — вариант «Прежний» окна, выбран сразу, если свободен (ibPortOpts, tests/test_it1_t3_proto.py)
        # REV-it1-b З-2: тексты о прошлой установке — из ibKeptNotes (занят — без зелёной, tests/test_it1_t3_proto.py)
        for need in ("ibKeptNotes(r, null)", 'U.al("success", "", kn0.ok', "ibKeptNotes(r, ok ? doc : null)", "API.inbounds.offer(r.kind",
                     't("ib.portHintKept")', "if (kept) kept.remove();"):
            self.assertIn(need, inst)                                           # the dialog says the port comes back
        notes = self.fn("ibKeptNotes")
        for need in ('t("ib.keptOpt", { ports: r.kept.join(", ") })', 't("ib.keptBusy", { port: String(doc.kept) })'):
            self.assertIn(need, notes)
        card = self.fn("ibCard")
        self.assertIn('} else if (r.kept) {', card)                             # the card of a removed protocol
        self.assertIn('t("ib.keptFixed")', card)
        self.assertIn("kept: isObj(r.kept) ? arr(r.kept.ports).map(String) : null", self.js)   # normInbounds

    def test_texts_say_what_is_kept(self):
        self.assertIn("Порт и ключи сервера сохранятся", self.vals["ib.uninstallWarn"])      # корзина, подтверждение на месте
        self.assertIn("{n}", self.vals["ib.uninstallWarn"])
        self.assertIn("снова работают", self.vals["ib.restored"])
        self.assertIn("выдайте их этим устройствам заново", self.vals["ib.reissueText"])

    def test_mock_keeps_the_access_of_devices(self):
        mock = strip_comments(read("dev-mock.js"))
        self.assertIn("r._kept = r.ports", mock)
        self.assertNotIn("d.access = d.access.filter(function (k) { return k !== m[1]; })", mock)
        self.assertIn("previous: prev", mock)

class ContractFixesTest(unittest.TestCase):
    """The critic of the panel ↔ server contract (C4, C6, C11–C13, C15): what app.js must keep doing."""

    @classmethod
    def setUpClass(cls):
        cls.js = strip_comments(read("app.js"))

    def test_offers_come_from_the_server(self):
        """C11: the preselected inbound of a new device — GET gw/offers (issue_os.MATRIX), no copy of the table."""
        self.assertNotIn("OFFER_ORDER", self.js)
        self.assertNotIn("ISSUE_ORDER", self.js)
        self.assertIn("API.offers(", self.js)

    def test_an_access_change_sends_only_its_inbound(self):
        """C13: {kind, on} — a whole list taken from a stale R.people would revoke what the bot granted a second ago."""
        m = re.search(r"API\.devices\.access\(d\.id, \{([^}]*)\}\)", self.js)
        self.assertIsNotNone(m)
        self.assertEqual(sorted(x.strip().split(":")[0] for x in m.group(1).split(",")), ["kind", "on"])

    def test_traffic_arrows_are_the_devices_view(self):
        """C12: ↓ — what the device downloaded: tx of the node (live.py: rx is what the client sent)."""
        self.assertIn('"↓ " + fmtBytes(l.tx) + "  ↑ " + fmtBytes(l.rx)', self.js)
        self.assertNotIn('"↓ " + fmtBytes(l.rx)', self.js)
        # активность (UI-B, SPEC-activity §5: rx/tx — глазами узла): тот же взгляд устройства в листе, подсказке и «Графике»
        self.assertIn('"↓ " + fmtIn(a.tx, tot) + " ↑ " + fmtIn(a.rx, tot)', self.js)
        self.assertIn('"↓ " + fmtIn(tx, tot) + " ↑ " + fmtIn(rx, tot)', self.js)
        self.assertNotRegex(self.js, r'"↓ " \+ fmt\w+\((a\.|x\.)?rx')

    def test_legacy_forwards_and_the_notice_of_a_dead_link(self):
        """C4: managed: false of GET forwards is read; C6: ?link=expired|limit is explained."""
        self.assertIn("j.managed !== false", self.js)
        self.assertRegex(self.js, r'LINK_NOTE = \{ expired: \[[^\]]+\], limit: \[[^\]]+\] \}')

    def test_howto_of_the_issue_is_shown(self):
        """C15: the steps for a beginner the server sends with the issue."""
        self.assertIn("arr(r.howto)", read("give.js"))           # 3.3.2: the window is give.js (step 3, the server's step)

    def test_the_github_key_never_stays_in_the_browser(self):
        js = read("app.js")
        self.assertRegex(js, r'function githubForm\(o = \{\}\) \{\n\s*const input = U\.input\(\{ type: "password"')
        self.assertIsNone(re.search(r"lsSet\([^)]*(token|key)", self.js))
        self.assertIn('input.value = "";', js[js.index("function githubForm"):js.index("function openGithubKey")])

    def test_the_exit_key_never_stays_in_the_browser(self):
        """3.2.21: the key of an exit lives in the field of the dialog and in the body of the request only: the field is
        wiped on «Добавить» and on closing, nothing is stored or logged; a monospace field without autocomplete,
        autocapitalize and spellcheck."""
        js = read("app.js")
        body = js[js.index("function addExitDialog"):js.index("function exitPinned")]
        self.assertIn('U.input({ tag: "textarea", mono: true', body)
        self.assertIn('area.setAttribute("autocapitalize", "off")', body)
        self.assertIn('const wipe = () => { area.value = ""; fileText = ""; pick.value = ""; };', body)
        self.assertIn("onClose: () => { wipe(); checked = null; window.removeEventListener", body)
        # 3.2.21, critic: a failed add keeps the dialog and its key (no paste again); success closes it — and wipes
        go = body[body.index("    const go = async () => {"):body.index("    function foot()")]
        self.assertNotIn("d.close()", go[:go.index("API.exits.add(")])    # T15 Q6: тело — { key } или { key, group }
        self.assertIn("if (open) d.close();", go)
        self.assertIn("keyNeeded(e, go);", go)                      # no core, no GitHub key: the window of the key
        # a second «Проверить» while the first waits does nothing (no skeleton left hanging)
        chk = body[body.index("    const check = async () => {"):body.index("    const readFile")]
        self.assertLess(chk.index("if (checking || adding) return;"), chk.index("U.skel(1)"))
        self.assertIn("finally { checking = false; }", chk)
        # a file dropped beside the dialog does not take the page away
        self.assertIn('window.addEventListener("drop", stopDrop);', body)
        self.assertIn('window.removeEventListener("drop", stopDrop);', body)
        # «Добавить (N)» counts neither keys in the pool nor keys with a problem
        self.assertIn("items.filter((x) => !x.in_pool && !x.problem).length", body)
        # the content of a file never goes on the screen: only «Файл … прочитан (… КБ)»
        self.assertNotRegex(body, r"area\.value = (fileText|text|rd\.result)")
        self.assertIn("rd.readAsText(f)", body)
        self.assertIn("if (f.size > KEY_FILE_MAX)", body)
        self.assertIn("const KEY_FILE_MAX = 64 * 1024;", js)
        self.assertIsNone(re.search(r"lsSet|localStorage|sessionStorage|console\.|\bS\.\w+\s*=|\bP\.\w+\s*=", body))
        u = js[js.index("    input(o = {}) {"):js.index("    select(options")]
        self.assertIn('autocomplete: o.ac || "off", spellcheck: tag === "select" ? null : "false"', u)

    def test_the_address_of_a_subscription_never_stays_in_the_browser(self):
        """The address of a subscription is a password of its keys: it lives in the field of the dialog and in the body
        of POST gw/exit-subs only — wiped on closing and after «Добавить подписку», never stored, logged or put into
        a URL; daily refresh is on by default; a link pasted on the tab «Ключ» is offered the tab «Подписка»."""
        js = read("app.js")
        body = js[js.index("function addExitDialog"):js.index("function exitPinned")]
        self.assertIn('const subWipe = () => { subIn.value = ""; };', body)
        self.assertIn('window.removeEventListener("drop", stopDrop); subWipe(); }', body)
        self.assertIn("let subAuto = true;", body)
        self.assertIn("API.exitSubs.add({ url, auto: subAuto })", body)
        go = body[body.index("    const goSub = async () => {"):body.index("    const go = async () => {")]
        self.assertNotIn("d.close()", go[:go.index("API.exitSubs.add(")])        # a failure keeps the address
        self.assertIn("if (open) d.close();", go)
        self.assertIn("if (!subUrlOk(url))", go)
        self.assertIn('setTab("sub")', body)                                    # «Добавить как подписку»
        self.assertIsNone(re.search(r"lsSet|localStorage|sessionStorage|console\.|\bS\.\w+\s*=|\bP\.\w+\s*=", body))
        self.assertIn('add: (b) => request("POST", "/api/gw/exit-subs", b)', js)

    def test_the_file_and_the_password_of_a_copy_never_stay_in_the_browser(self):
        """A copy of the settings holds every key and token: its file and password live in the dialogs and in the
        bodies of POST backup/* only — wiped on closing, the exported file goes straight into a Blob; nothing is
        stored or logged; a body over the panel's limit (1 MB) is not sent — the SSH command is said instead."""
        js = read("app.js")
        # до подменю «Настроек» (UI-5): дальше — навигация по частям, не копия
        body = js[js.index("// ── 22b. Копия настроек"):js.index("  // UI-5 (шаблон T13 «Страница с подменю»")]
        self.assertIn("const BACKUP_BODY_MAX = 20 * 1024 * 1024;", body)
        self.assertIn("new Blob([JSON.stringify(b)]).size > BACKUP_BODY_MAX", body)
        self.assertIn('const wipe = () => { pw1.value = ""; pw2.value = ""; };', body)
        self.assertIn('const wipe = () => { fileText = ""; pw.value = ""; pick.value = ""; pv = null; };', body)
        self.assertIn("onClose: () => { wipe();", body)
        self.assertIn("onClose: wipe,", body)
        self.assertIn('saveFile(String(r.name || "kaskad.kbk"), r.file, "application/octet-stream");', body)
        self.assertIn('r.file = "";', body)
        self.assertIn("if (f.size > BACKUP_BODY_MAX)", body)
        self.assertIn("rd2.readAsText(f)", body)
        self.assertIn("if (backupBig(b)) {", body)
        self.assertIsNone(re.search(r"lsSet|localStorage|sessionStorage|console\.|\bS\.\w+\s*=|\bP\.\w+\s*=", body))
        # the wizard «Быстрый старт» opens the same dialogs
        for fn in ("function openBackupExport(o = {})", "function openBackupImport(o = {})", "function backupUndo(o = {})"):
            self.assertIn(fn, body)
        # the import waits for the check: «Перенести» is off while there are blockers
        self.assertIn("arr(pv.blockers).length > 0 || pv.ok === false", body)


@unittest.skipUnless(shutil.which("node"), "нет node: макет не проверить")
class MockCoverageTest(unittest.TestCase):
    """dev-mock.js answers every call of app.js and graph.js — not «Нет такого пути» (a mock behind the API hid the
    critic's C4 and C6: M6, M12)."""

    SCRIPT = r"""
globalThis.window = globalThis;
globalThis.location = new URL("http://127.0.0.1/index.html?mock=1&lat=0");
require(process.argv[2]);
require(process.argv[4]);                    // 3.3.3: бот, ключ ИИ-агента, журнал протокола — dev-mock-ux.js поверх
require(process.argv[5]);                    // B15: расписание и история проверок — dev-mock-diag.js поверх
require(process.argv[6]);                    // BC5: пределы замера из браузера — dev-mock-speed.js поверх
require(process.argv[7]);                    // 3.3.7.15 F14: «Разложить заново» — dev-mock-regroup.js поверх
require(process.argv[8]);                    // 3.3.7.15 F10–F13: «Адреса сервера» — dev-mock-adr.js поверх
const calls = JSON.parse(process.argv[3]);
(async () => {
  const out = [];
  for (const [m, p] of calls) {
    const r = await window.KASKAD_MOCK.fetch(p, { method: m, headers: { "X-Kaskad-Admin": "1" }, body: m === "GET" ? undefined : "{}" });
    const tx = await r.text();
    let j = {};
    try { j = JSON.parse(tx); } catch (e) { j = {}; }           // выгрузка истории (B15) — файл CSV, не JSON
    out.push([m, p, r.status, j.error || ""]);
  }
  console.log(JSON.stringify(out));
  process.exit(0);
})();
"""

    def test_every_call_has_an_answer(self):
        import json
        import tempfile
        from tests.test_panel_contract import PanelContractTest
        from tests.test_panel_contract import WORKER_CALLS
        # вызовы воркера замера в демо отвечает подменённый воркер dev-mock-speed.js, а не fetch
        worker = set((m, p) for _rx, m, p in WORKER_CALLS)
        calls = [c for c in PanelContractTest("test_every_call_has_a_route").calls() if tuple(c) not in worker]
        self.assertIn("FakeWorker.prototype.postMessage", read("dev-mock-speed.js"))
        with tempfile.TemporaryDirectory() as d:
            script = os.path.join(d, "mock_calls.js")
            with open(script, "w", encoding="utf-8") as f:
                f.write(self.SCRIPT)
            r = subprocess.run(["node", script, os.path.join(STATIC, "dev-mock.js"), json.dumps(calls), os.path.join(STATIC, "dev-mock-ux.js"),
                                os.path.join(STATIC, "dev-mock-diag.js"), os.path.join(STATIC, "dev-mock-speed.js"),
                                os.path.join(STATIC, "dev-mock-regroup.js"), os.path.join(STATIC, "dev-mock-adr.js")],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace")[:500])
        answers = json.loads(r.stdout.decode("utf-8"))
        self.assertEqual(len(answers), len(calls))
        lost = ["%s %s" % (m, p) for m, p, status, err in answers if status == 404 and err.startswith("Нет такого пути")]
        self.assertEqual(lost, [], "макет не знает этих вызовов")


@unittest.skipUnless(shutil.which("node"), "нет node: синтаксис скриптов не проверить")
class NodeSyntaxTest(unittest.TestCase):
    def test_scripts_parse(self):
        for name in ("app.js", "boot.js", "icons.js", "qr.js", "dev-mock.js", "i18n-ru.js", "kit.js", "templates.js",
                     "kit-i18n-ru.js", "kit-i18n-en.js", "find.js") + tuple(pages_files(".js")):
            r = subprocess.run(["node", "--check", os.path.join(STATIC, name)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(r.returncode, 0, "%s: %s" % (name, r.stderr.decode("utf-8", "replace")[:300]))


class LiveEventsTest(unittest.TestCase):
    """SSE ``state`` (parts state/exits/pool/panel) re-reads the resources of the page: every resource that lives in the
    files of the node is in ``PART_RES.state`` (REV-s1-t8-a С-3: «Группы» did not follow a write from CLI/bot)."""

    def test_part_res_covers_the_resources_of_the_files(self):
        js = read("app.js")
        res = set(re.findall(r'defRes\("([a-z]+)"', js))
        m = re.search(r"const PART_RES = (\{.*?\});", js)
        self.assertIsNotNone(m)
        parts = json.loads(re.sub(r"(\w+):", r'"\1":', m.group(1)))
        # activity — не из файлов узла: сводку перечитывает отметка activity.ts живых данных (liveSubs, SPEC-activity §5)
        self.assertEqual(set(parts["state"]), res - {"panel", "github", "activity"})
        for part in ("exits", "pool"):
            self.assertTrue({"exits", "policy"} <= set(parts[part]), part)


if __name__ == "__main__":
    unittest.main()
