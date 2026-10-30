# -*- coding: utf-8 -*-
"""Панель: правки по итогам двух критиков 16.10 (CRITICS-FIX-1) — по тексту app.js/styles.css и, где есть node, по
самой функции.
  A-16  итог суточной проверки обновлений — на языке панели из данных, а не готовой фразой ядра;
  A-19  «Логи» — профилей AmneziaWG среди источников нет;
  B-6   число у «Пользователей» в меню — по свежему списку;
  B-7   плашка ожидания: «до 15 минут» — только у долгих действий или после 15 с;
  B-9   «Перевыпустить ключ»: над окном имя, без «имя · awg3»; итог — про страницу подключения;
  B-10  карточка профиля и расписание бэкапов переносят строки, а не режут;
  B-12  отказ транспорта словами: «Нет связи с сервером» / «Сервер не смог выполнить запрос», без «мост» и «JSON».
Запуск: python3 -m unittest tests/test_critics_fix_panel.py
"""
import json
import os
import re
import shutil
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "admin-web", "static")


def read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as fh:
        return fh.read()


def cut(src, start, end):
    i = src.index(start)
    return src[i:src.index(end, i)]


class CriticsFixPanel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = read("app.js")
        cls.css = read("styles.css")

    def test_a16_update_summary_in_panel_language(self):
        fn = cut(self.app, "  function prUpdHint(sum) {", "  function dailyBlock() {")
        self.assertIn('h: prUpdHint(sum)', self.app)
        self.assertNotIn('h: sum.hint || t("prUpdLastNone")', self.app)
        node = shutil.which("node")
        if not node:
            self.skipTest("node нет")
        dict_ru = {"prUpdHUpdate": "U", "prUpdHCurrent": "C", "prUpdHClient": "K{v}", "prUpdHUnreach": "N{v}",
                   "prUpdLastNone": "-"}
        js = ("const D=%s; const t=(k)=>D[k]; const tf=(k,o)=>D[k].replace('{v}',o.v);\n%s\n"
              "const cases=[{hint_key:'client_only',client_release:{latest:'5.0.3.0'},hint:'Для сервера…'},"
              "{updates_available:false,client_release:{update:true,latest:'5.0.3.0'},hint:'по-русски'},"
              "{hint_key:'current'},{hint_key:'unreachable',client_release:{latest:'5.1'}},{updates_available:true},"
              "{hint:'Готовая фраза'}];\nconsole.log(JSON.stringify(cases.map(prUpdHint)));"
              % (json.dumps(dict_ru), fn))
        out = subprocess.run([node, "-e", js], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout), ["K5.0.3.0", "K5.0.3.0", "C", "N5.1", "U", "Готовая фраза."])

    def test_a16_core_keeps_hint_key(self):
        with open(os.path.join(ROOT, "lib", "upstream.sh"), encoding="utf-8") as fh:
            up = fh.read()
        self.assertIn("hint: $hint, hint_key: $hk}", up)
        self.assertIn('hint_key: ($l.hint_key // ""),', up)

    def test_a19_logs_without_profiles(self):
        fn = cut(self.app, "  function lgSources() {", "  // Время в строках журналов")
        self.assertIn("!/^awg/.test(c)", fn)

    def test_b6_menu_count_follows_user_list(self):
        self.assertIn("state.overview.users_total = state.users.length;", self.app)

    def test_b7_short_hint_for_quick_actions(self):
        self.assertIn('busyHintShort: "Панель ждёт ответа сервера — обычно несколько секунд."', self.app)
        self.assertIn('busyHintShort: "The panel is waiting for the server — usually a few seconds."', self.app)
        show = cut(self.app, "  function showProgress() {", "  function startProgress(name) {")
        self.assertIn('LONG_ACTION_RE.test(progressName) ? "busyHint" : "busyHintShort"', show)
        m = re.search(r"const LONG_ACTION_RE = (/.*/);", self.app)
        rx = re.compile(m.group(1).strip("/"))
        for name in ("profile-install", "mode", "upstream-update", "kmod-install", "lazy-setup", "backup-restore"):
            self.assertTrue(rx.search(name), name)
        for name in ("user-add", "page-create", "user-reissue-peer", "share-create"):
            self.assertFalse(rx.search(name), name)

    def test_b9_reissue_without_service_names(self):
        fn = cut(self.app, "  async function reissuePeer(user, pid) {", "  async function removePeer(user, pid) {")
        self.assertNotIn("${pid}`", fn)
        self.assertIn("kicker: userName(user)", fn)
        self.assertIn("пусть пользователь откроет её заново", self.app)
        self.assertIn("ask the user to open it again", self.app)
        for lang_body in (self.app,):
            self.assertNotIn("новый конфиг заберите тут же", lang_body)

    def test_b10_wrapping(self):
        title = re.search(r"\.gx-pcard__title \{[^}]*\}", self.css).group(0)
        img = re.search(r"\.gx-pcard__img \{[^}]*\}", self.css).group(0)
        self.assertNotIn("nowrap", title)
        self.assertNotIn("nowrap", img)
        self.assertIn(".gx-seg.gx-seg--wrap { display: flex; flex-wrap: wrap;", self.css)
        self.assertIn('aria: t("bkSchedL"), wrap: true', self.app)

    def test_a8_a17_app_profile_build_and_mtu(self):
        self.assertIn('p.image_tag === "latest" ? (p.core_version && p.core_version !== "latest" ? tf("prImageApp"', self.app)
        self.assertIn('tile(t("prMtu"), p.mtu ? String(p.mtu) : tf("prMtuKeys", { v: 1280 }))', self.app)
        with open(os.path.join(ROOT, "admin-web", "server.py"), encoding="utf-8") as fh:
            self.assertIn('"image_tag", "image_digest", "core_version", "enabled"', fh.read())
        with open(os.path.join(ROOT, "goamnezia-bot", "bot.py"), encoding="utf-8") as fh:
            bot = fh.read()
        self.assertEqual(bot.count("tag = core_ver_words(user_id, "), 2)
        self.assertEqual(bot.count("or str(MTU_STANDARD))"), 2)

    def test_b4_keenetic_lines_everywhere(self):
        """B-4 (решение владельца 16.10): Keenetic на обычной прошивке — 1.5 и 2.0, 3.1 — пока только в dev-прошивке.
        Ни в одном тексте для людей нет «роутеры Keenetic не подключатся» без «на обычной прошивке»; без 2.0, но с 1.x —
        «только к AmneziaWG 1.5 или 1.0, если она стоит»; 1.x не выдаётся за линию «для Keenetic»."""
        def rd(*p):
            with open(os.path.join(ROOT, *p), encoding="utf-8") as fh:
                return fh.read()
        sources = {
            "app.js": self.app, "ru.json": rd("goamnezia-bot", "lang", "ru.json"), "en.json": rd("goamnezia-bot", "lang", "en.json"),
            "ru amnezia": rd("lib", "lang", "ru.d", "amnezia.sh"), "en amnezia": rd("lib", "lang", "en.d", "amnezia.sh"),
            "ru menu": rd("lib", "lang", "ru.d", "menu.sh"), "en menu": rd("lib", "lang", "en.d", "menu.sh"),
        }
        bad = re.compile(r"(роутеры Keenetic не подключ|Keenetic routers (cannot|will not) connect|и Keenetic не подключ)")
        for name, src in sources.items():
            lines = [l for l in src.splitlines() if bad.search(l) and not l.lstrip().startswith(("//", "#"))]
            self.assertEqual(lines, [], name)
        ru_dup = ("роутеры Keenetic на обычной прошивке — только к AmneziaWG 1.5 или 1.0, если она стоит "
                  "(3.1 Keenetic понимает пока только в dev-прошивке)")
        en_dup = ("Keenetic routers on regular firmware connect only to AmneziaWG 1.5 or 1.0 if it is installed "
                  "(Keenetic understands 3.1 only in dev firmware so far)")
        for name, want, n in (("ru amnezia", ru_dup, 2), ("en amnezia", en_dup, 2), ("ru.json", ru_dup, 2), ("en.json", en_dup, 2),
                              ("app.js", ru_dup, 1)):
            self.assertEqual(sources[name].count(want), n, name)
        self.assertIn(en_dup, self.app)
        # B-4 остаток (CRITICS-FIX-2): под 1.x — что Keenetic хватит 2.0, а не «1.5 и 2.0», от которого выбирали 1.x
        self.assertIn('unFor1x: "Для старых клиентов и роутеров MikroTik, OpenWrt. Роутерам Keenetic хватит 2.0 — ради них 1.x '
                      'не нужна."', self.app)
        self.assertIn("и роутеры Keenetic — ставьте всегда.", self.app)
        clients = json.loads(rd("lib", "clients.json"))
        keen = [a for a in clients["apps"] if a.get("id") == "keenetic"][0]
        notes = json.dumps(keen, ensure_ascii=False)
        self.assertIn("3.1 — пока только в dev-прошивке", notes)
        self.assertNotIn("3.1 KeeneticOS пока не умеет", notes)

    def test_a20_pages_reload_in_place(self):
        loader = cut(self.app, "  async function loadUserPages(user, box, opts = {}) {", "  function renderUserPages(")
        self.assertIn("const had = box.childNodes.length > 0;", loader)
        self.assertIn('if (pb) loadUserPages(user, pb).catch(() => {});', self.app)
        # T-2 (CRITICS-FIX-2): «Готово» только закрывает окно — карточку другого пользователя не подменяет
        self.assertIn('dlg.setFoot([U.btn(t("lkDone"), "primary", () => dlg.close(), { md: true })]);', self.app)
        self.assertNotIn("if (user && !userPagesBox(user)) renderUserCard(user);", self.app)

    @unittest.skipUnless(shutil.which("node"), "нет node")
    def test_r3_pages_box_of_this_user_only(self):
        """R-3/Q-1: после создания страницы перечитывается блок карточки ЭТОГО человека, а не первый попавшийся.

        Блок строится настоящими el() и строкой pagesBox из renderUserCard, user — объект человека (как в карточке),
        setAttribute приводит значение к строке, как DOM. С объектом в data-user атрибут был «[object Object]», и блок
        не находился никогда."""
        self.assertNotIn("document.querySelector('.gx-upages[data-upages=\"pages\"]')", self.app)
        self.assertIn("const pb = userPagesBox(user);", self.app)
        el_src = cut(self.app, "  const el = (tag, attrs = {}, ...children) => {", "  // Кусок разметки строкой")
        m = re.search(r'^\s*const pagesBox = el\("div", \{[^\n]*"data-user":[^\n]*\);$', self.app, re.M)
        self.assertIsNotNone(m, "строка pagesBox в renderUserCard не найдена")
        fn = cut(self.app, "  function userPagesBox(user, kind = \"pages\") {", "  async function loadUserPages(")
        js = """
const made = [];
globalThis.document = {
  createElement: (tag) => {
    const attrs = {};
    const node = { tag, className: "", attrs, childNodes: [],
      setAttribute(k, v) { attrs[k] = String(v); }, getAttribute(k) { return k in attrs ? attrs[k] : null; },
      addEventListener() {}, append(...c) { this.childNodes.push(...c); } };
    made.push(node);
    return node;
  },
  querySelectorAll: () => made.filter((n) => n.className === "gx-upages" && n.attrs["data-upages"] === "pages"),
};
""" + el_src + "\n" + fn + """
const card = (user) => { """ + m.group(0).strip() + """ return pagesBox; };
const anna = { name: "anna", peers: {} }, bob = { name: "bob", peers: {} };
const bobBox = card(bob);
const out = [typeof bobBox.getAttribute("data-user"), bobBox.getAttribute("data-user"), userPagesBox(anna)];
out.push(userPagesBox(bob) === bobBox);
const annaBox = card(anna);
out.push(userPagesBox(anna) === annaBox, userPagesBox(bob) === bobBox, userPagesBox(null));
console.log(JSON.stringify(out));
"""
        r = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), ["string", "bob", None, True, True, True, None])

    def test_a21_panel_url_masked(self):
        fn = cut(self.app, "  function overviewOpen(o) {", "  function overviewSkeleton() {")
        self.assertIn('url.replace(/^(https?:\\/\\/[^/]+\\/).+$/, "$1••••••••")', fn)
        self.assertIn("U.copyBtn(url)", fn)
        self.assertIn("5 * 60 * 1000", fn)
        self.assertIn('ovUrlShow: "Показать на 5 минут"', self.app)

    @unittest.skipUnless(shutil.which("node"), "нет node")
    def test_r5_timer_hides_the_current_node(self):
        """R-5: таймер «Показать на 5 минут» прячет адрес в узле текущего Обзора, а не в убранном перерисовкой."""
        fn = cut(self.app, "  function overviewOpen(o) {", "  function overviewSkeleton() {")
        self.assertIn("if (shown()) ovUrlTimer = setTimeout(ovUrlHideNow, 5 * 60 * 1000);", fn)
        self.assertIn("ovUrlLive = { code, tog, masked };", fn)
        self.assertNotIn("code.textContent = masked; tog.textContent", fn)
        hide = cut(self.app, "  let ovUrlShownUntil = 0;", "  function overviewOpen(o) {")
        js = "const t = (k) => k;\n" + hide + """
const node = (txt) => ({ textContent: txt, isConnected: true });
const old = { code: node("URL"), tog: node("ovUrlHide") };
old.code.isConnected = false; old.tog.isConnected = false;
const cur = { code: node("URL"), tog: node("ovUrlHide"), masked: "M" };
ovUrlShownUntil = Date.now() + 1000;
ovUrlLive = cur;
ovUrlHideNow();
console.log(JSON.stringify([cur.code.textContent, cur.tog.textContent, ovUrlShownUntil, old.code.textContent]));
"""
        r = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), ["M", "ovUrlShow", 0, "URL"])

    def test_a22_small_things(self):
        self.assertNotIn("сторож не отличит от тишины", self.app)
        self.assertIn('ovNotInstalled: "не установлена"', self.app)
        self.assertIn(".gx-srv > div.gx-srv__host > span:last-child { white-space: nowrap;", self.css)

    @unittest.skipUnless(shutil.which("node"), "нет node")
    def test_r6_domain_regex_and_phone_wrap(self):
        """R-6: версия и IP — не домен; на телефоне домен переносится, а не обрезается многоточием."""
        line = [l for l in self.app.splitlines() if l.lstrip().startswith("const host = (v) =>")]
        self.assertEqual(len(line), 1)
        js = "const ip4 = (v) => /^\\d{1,3}(\\.\\d{1,3}){3}$/.test(String(v));\n" + line[0] + """
console.log(JSON.stringify(["1.0.13", "2.56.177.96", "dupline-1015.lol123.uk", "xn--80ak6aa92e.xn--p1ai", "—", "10.0"].map(host)));
"""
        r = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), [False, False, True, True, False, False])
        phone = cut(self.css, "@media (max-width: 599.98px), (hover: none) {", "}\n}")
        self.assertIn(".gx-srv > div.gx-srv__host { flex-wrap: wrap;", phone)
        self.assertIn("white-space: normal; overflow-wrap: anywhere; overflow: visible; text-overflow: clip;", phone)

    def test_a15_one_min_version_everywhere(self):
        """A-15: минимальная версия AmneziaVPN для 1.5 и 2.0 — одна: ядро, быстрый путь панели, бот, панель и каталог
        страницы подключения (стабильные выпуски из lib/clients.json; у iOS своя версия App Store — её не сравниваем)."""
        def rd(*p):
            with open(os.path.join(ROOT, *p), encoding="utf-8") as fh:
                return fh.read()
        core = dict(re.findall(r'^\s+(1\.5|2\.0)\) echo "([0-9.]+)" ;;', rd("lib", "export.sh"), re.M))
        fast = json.loads(re.search(r"_MIN_CLIENT = (\{[^}]*\})", rd("lib", "core_read.py")).group(1))
        botv = json.loads(re.search(r"MIN_CLIENT_BY_LINE = (\{[^}]*\})", rd("goamnezia-bot", "bot.py")).group(1))
        clients = json.loads(rd("lib", "clients.json"))
        amn = [a for a in clients["apps"] if a.get("id") == "amneziavpn"][0]
        for line in ("1.5", "2.0"):
            page = {pl: (v.get("support") or {}).get(line) for pl, v in amn["platforms"].items() if pl != "ios"}
            self.assertEqual(set(page.values()), {core[line]}, (line, page))
            self.assertEqual(fast[line], core[line], line)
            self.assertEqual(botv[line], core[line], line)
        self.assertIn('prApp20: "Amnezia ≥ %s"' % core["2.0"], self.app)
        self.assertIn("(%s и новее)" % core["2.0"], self.app)

    def test_b12_transport_in_words(self):
        self.assertFalse("Мост не ответил JSON" in self.app)
        self.assertFalse("bridge returned no JSON" in self.app)
        self.assertIn('errOffline: "Нет связи с сервером.', self.app)
        api = cut(self.app, "  async function api(path, options = {}) {", "    if (res.status === 401) {")
        self.assertIn('new Error(t("errOffline")); e.code = "transport"; e.offline = true;', api)


if __name__ == "__main__":
    unittest.main()
