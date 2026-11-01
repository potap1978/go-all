"""Kaskad 3.3.2, the owner's notes (k3/handoff/OWNER-FEEDBACK-3.3.md), the panel:

    №1   the theme is dark by default (the panel and the page of a person), not the system's; light and «as the system»
         stay choices. The first frame (boot.js) is dark without a saved choice. A choice saved explicitly stays; the
         «system» the old versions saved along with any other change (``v`` below 3) is read as dark — the browser
         (boot.js, migrateLook of app.js) and the server (appearance_read) the same;
    №14  the first visit of the panel after the wizard: a short tour over the real panel (people, exits, rules,
         settings — the texts of the wizard's tour; no «Схема» among them: the end leads there) and at the end «Схема» with the path of the wizard's device. The mark
         «done» is on the node (GET/PUT /api/intro), not in the browser; «Пропустить» always seen, Escape closes, «Назад»
         and «Далее» on fixed places; «Показать знакомство снова» in «Настройки»;
    №15  with a domain the header names the domain (the IP — a hint); the tile of the gateway says what really goes into
         the settings: GET gw ``clients`` — the domain, the IP and the address by inbound (VLESS with TLS, SSTP and
         OpenConnect on the domain's certificate — the domain, everything else — «Адрес для клиентов»), «Изменить» —
         to the card where it is changed.
"""

import json
import os
import shutil
import unittest

from unittest import mock

from kaskad import state
from kaskad import util as kutil
from kaskad.gw import model as gmodel
from kaskad.panel import api, conf
from tests import jsparts
from tests.panel.panelcase import PanelCase
from tests.test_panel_js_contract import STATIC, node


def read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


# ── №1 тема ───────────────────────────────────────────────────────────────────────────────────────────────────────

class ThemeServerTest(PanelCase):
    def save(self, ap, v=None):
        doc = {"schema": 1, "appearance": ap}
        if v is not None:
            doc["v"] = v
        os.makedirs(os.path.dirname(conf.appearance_path()), exist_ok=True)
        kutil.atomic_write(conf.appearance_path(), json.dumps(doc), 0o600)

    def theme(self):
        return self.ok("GET", "/api/appearance")["appearance"]["theme"]

    def test_dark_by_default(self):
        doc = self.ok("GET", "/api/appearance")
        self.assertEqual((doc["saved"], doc["appearance"]["theme"], doc["defaults"]["theme"]), (False, "dark", "dark"))
        self.assertEqual(api.APPEARANCE_ENUMS["theme"], ("light", "dark", "system"))      # «as the system» — a choice

    def test_the_old_default_system_is_dark_an_explicit_choice_stays(self):
        self.save(dict(api.APPEARANCE_DEFAULTS, theme="system", density="min"))           # before 3.3.0: no v
        ap = self.ok("GET", "/api/appearance")["appearance"]
        self.assertEqual((ap["theme"], ap["density"]), ("dark", "min"))
        self.save(dict(api.APPEARANCE_DEFAULTS, theme="system"), v=2)                       # 3.3.0–3.3.1
        self.assertEqual(self.theme(), "dark")
        self.save(dict(api.APPEARANCE_DEFAULTS, theme="light"), v=2)                        # light — chosen: stays
        self.assertEqual(self.theme(), "light")
        self.save(dict(api.APPEARANCE_DEFAULTS, theme="system", accent="ga"), v=2)          # the blue of 3.3.0+ stays
        self.assertEqual(self.ok("GET", "/api/appearance")["appearance"]["accent"], "ga")
        # «as the system» chosen now stays: PUT writes v 3
        self.ok("PUT", "/api/appearance", {"theme": "system"})
        with open(conf.appearance_path(), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["v"], 3)
        self.assertEqual(self.theme(), "system")
        # a PUT of another field over an old saved «system» writes the migrated dark
        self.save(dict(api.APPEARANCE_DEFAULTS, theme="system"), v=2)
        self.ok("PUT", "/api/appearance", {"density": "min"})
        self.assertEqual(self.theme(), "dark")


@unittest.skipUnless(shutil.which("node"), "нет node")
class ThemeBrowserTest(unittest.TestCase):
    def first_frame(self, cases):
        """boot.js for each stored value (null — nothing stored) and the system's light/dark: data-theme."""
        return node(r"""
const out = [];
for (const [stored, sysLight] of DATA.cases) {
  globalThis.window = globalThis;
  globalThis.localStorage = { getItem: () => stored, setItem: () => {} };
  globalThis.matchMedia = (q) => ({ matches: /light/.test(q) ? sysLight : /dark/.test(q) ? !sysLight : false });
  const attrs = {};
  globalThis.document = { documentElement: { setAttribute: (k, v) => { attrs[k] = v; }, style: { setProperty: () => {}, removeProperty: () => {} } } };
  delete require.cache[require.resolve(DATA.boot)];
  require(DATA.boot);
  out.push(attrs["data-theme"]);
}
console.log(JSON.stringify({ out }));
process.exit(0);
""", {"boot": os.path.join(STATIC, "boot.js"), "cases": cases})["out"]

    def test_first_frame(self):
        cases = [[None, True],                                                       # nothing saved, light system
                 [json.dumps({"theme": "system"}), True],                            # the old default — dark
                 [json.dumps({"theme": "system", "v": 2}), True],
                 [json.dumps({"theme": "system", "v": 3}), True],                    # chosen in 3.3.2 — the system's
                 [json.dumps({"theme": "system", "v": 3}), False],
                 [json.dumps({"theme": "light"}), False],                            # explicit — stays
                 [json.dumps({"theme": "dark", "v": 3}), True]]
        self.assertEqual(self.first_frame(cases), ["dark", "dark", "dark", "light", "dark", "light", "dark"])

    def test_app_migrates_like_the_first_frame(self):
        js = read("app.js")
        out = node(r"""
const isObj = (x) => !!x && typeof x === "object" && !Array.isArray(x);
%s
%s
%s
%s
console.log(JSON.stringify({
  none: normLook(migrateLook(null)).theme,
  old: normLook(migrateLook({ theme: "system", accent: "ga" })),
  v2: normLook(migrateLook({ theme: "system", accent: "ga", v: 2 })),
  v3: normLook(migrateLook({ theme: "system", v: 3 })).theme,
  light: normLook(migrateLook({ theme: "light" })).theme, ver: LOOK_V }));
""" % (jsparts.const(js, "LOOK_DEFAULTS"), jsparts.const(js, "LOOK_ENUM"), jsparts.const(js, "LOOK_V"),
       jsparts.function(js, "migrateLook") + "\n" + jsparts.function(js, "normLook")), {})
        self.assertEqual(out["none"], "dark")
        self.assertEqual((out["old"]["theme"], out["old"]["accent"]), ("dark", "auto"))
        self.assertEqual((out["v2"]["theme"], out["v2"]["accent"]), ("dark", "ga"))
        self.assertEqual((out["v3"], out["light"], out["ver"]), ("system", "light", 3))

    def test_the_view_offers_dark_first_and_the_system_as_a_choice(self):
        js = read("app.js")
        self.assertIn('lookSeg(t("lk.theme"), "theme", [["dark", t("lk.dark"), "moon"], ["light", t("lk.light"), "sun"], '
                      '["system", t("lk.system"), "display"]])', js)
        self.assertIn('data-hero="" data-theme="dark"', read("index.html"))
        self.assertIn('var LOOK_DEFAULTS = { theme: "dark"', read("dev-mock.js"))

    def test_the_page_of_a_person_is_dark_with_a_light_button(self):
        js, css, i18n = read("user.js"), read("user.css"), read("user-i18n-ru.js")
        self.assertIn("var theme = 'dark';", js)
        self.assertIn("document.documentElement.setAttribute('data-theme', 'light')", js)
        self.assertIn("themeButton();", js)
        self.assertIn(".up-theme {", css)
        for k in ("theme_light", "theme_dark"):
            self.assertIn("  %s: '" % k, i18n)



# ── №14 знакомство с панелью ──────────────────────────────────────────────────────────────────────────────────────

class IntroServerTest(PanelCase):
    def finish_wizard(self, device=None):
        from kaskad.setup import model as setup_model
        doc = setup_model.load()
        doc["status"] = "finished"
        if device:
            doc["answers"]["device"] = device
        setup_model.save(doc)

    def test_due_only_after_the_wizard_and_until_done(self):
        doc = self.ok("GET", "/api/intro")
        self.assertEqual((doc["due"], doc["done"], doc["setup_finished"], doc["device"]), (False, False, False, None))
        self.finish_wizard()
        self.assertTrue(self.ok("GET", "/api/intro")["due"])
        self.assertEqual(self.ok("PUT", "/api/intro", {"done": True})["done"], True)
        doc = self.ok("GET", "/api/intro")
        self.assertEqual((doc["due"], doc["done"]), (False, True))                 # another browser: no tour again
        with open(conf.intro_path(), encoding="utf-8") as f:
            self.assertIs(json.load(f)["done"], True)
        self.assertEqual(oct(os.stat(conf.intro_path()).st_mode & 0o777), "0o600")
        self.ok("PUT", "/api/intro", {"done": False})
        self.assertTrue(self.ok("GET", "/api/intro")["due"])
        for bad in ({}, {"done": "yes"}, {"done": 1}):
            self.assertEqual(self.err(400, "PUT", "/api/intro", bad)["data"], {"field": "done"})

    def test_the_device_of_the_wizard_or_the_first(self):
        from kaskad.gw import ops
        self.gateway(("wg",))
        a = ops.device_add(self.w.runner, self.uid, "Телефон", kind="phone", platform="ios", access=["wg"])["device"]["id"]
        b = ops.device_add(self.w.runner, self.uid, "Ноутбук", kind="pc", platform="windows", access=["wg"])["device"]["id"]
        self.finish_wizard(device=b)
        self.assertEqual(self.ok("GET", "/api/intro")["device"], b)
        self.finish_wizard(device="d-00000000")                                      # deleted since — the first
        self.assertEqual(self.ok("GET", "/api/intro")["device"], a)

    def test_needs_the_admin(self):
        self.assertIn("/api/intro", read("app.js"))
        r = self.call("PUT", "/api/intro", {"done": True}, csrf=False)
        self.assertNotEqual(r.status, 200)


class IntroPanelTest(unittest.TestCase):
    def test_the_tour_reuses_the_wizard_texts_and_has_fixed_buttons(self):
        js, css, i18n = read("app.js"), read("styles.css"), read("i18n-ru.js")
        # 3.3.3 (R-333 №4): у шага «Выходы» четвёртый элемент — заголовок карточки, которую обвести
        self.assertIn('const TOUR = [["people", "tour.s1", "tour.s1D"], ["exits", "tour.s2", "tour.s2D", "ex.listTitle"], ["rules", "tour.s3", "tour.s3D"],\n'
                      '    ["settings", "tour.s4", "tour.s4D"]];', js)
        tour = js[js.index("const TOUR = "):js.index("];", js.index("const TOUR = "))]
        self.assertNotIn('"scheme"', tour)                  # four hints; the end — «Схема» with the path
        self.assertNotIn('"tour.s5', i18n)
        for k, v in (("tour.s1", "Пользователи и устройства"), ("tour.s2", "Выходы"), ("tour.s3", "Правила"),
                     ("tour.s4", "Настройки"), ("tour.skip", "Пропустить"), ("tour.finish", "Показать путь трафика"),
                     ("tour.s2D", "Здесь ключи выходов: видно, какие живы, и можно добавить новые.")):
            self.assertIn('"%s": "%s"' % (k, v), i18n)
        # the buttons are made once: a step changes their texts, not their places
        self.assertIn('el("div", { class: "kk-tour__btns" }, parts.back, parts.next)', js)
        self.assertIn("P1.back.disabled = i === 0;", js)
        self.assertIn(".kk-tour__btns { display: grid; grid-template-columns: minmax(0, 2fr) minmax(0, 3fr);", css)
        self.assertIn(".kk-tour__back { grid-column: 1; }", css)
        self.assertIn(".kk-tour__next { grid-column: 2; }", css)
        # no wall: no backdrop, the rings let the clicks through; skip always there; Escape
        self.assertIn('role: "dialog", "aria-modal": "false"', js)
        self.assertIn("pointer-events: none;", css[css.index(".kk-tour__ring {"):])
        self.assertIn('if (ev.key === "Escape" && tour && !ev.defaultPrevented && !layerStack.length) { ev.preventDefault(); tourClose(true); }', js)
        self.assertIn('const skip = tourBtn("kk-tour__skip", "ghost", () => tourClose(true));', js)
        # the mark on the node; the end — the scheme with the path of the device
        self.assertIn("if (done) API.intro.done(true).catch(() => null);", js)
        self.assertIn('traceNav((dev || "-") + ":" + TOUR_TARGET);', js)
        self.assertIn("introCheck();", js)
        self.assertIn('U.btn(t("tour.again"), "tertiary", introAgain,', js)
        self.assertIn('tourClose(false);                                     // знакомство не пройдено', js)
        mock = read("dev-mock.js")
        self.assertIn('path === "/api/intro"', mock)


    def test_the_offer_of_the_kernel_module_is_one_line(self):
        js, i18n = read("app.js"), read("i18n-ru.js")
        # UI-6 (аудит 20.10 №10): совета движка на «Обзоре» нет — переход на модуль ядра только в карточке AmneziaWG
        self.assertNotIn("awg.offerT", i18n + js)
        self.assertNotIn("awg.offerText", i18n + js)
        self.assertIn('U.btn(t("awg.toKernel"), "tertiary", () => awgDetails(), { md: true, icon: "cpu" })', js)
        # the price (≈200 МБ, 1–3 мин, the clients reconnect) — in the window of «Подробнее» / «Перейти на модуль ядра»
        self.assertIn('if (engine === "kernel") return awgDetails();', js)
        from kaskad.gw import awgengine
        self.assertIn("15–25 секунд", awgengine.RECONNECT)


# ── №15 шапка и адрес для клиентов ────────────────────────────────────────────────────────────────────────────────

class ClientAddressesTest(PanelCase):
    def test_get_gw_tells_the_address_by_inbound(self):
        self.gateway(("wg",))
        doc = self.ok("GET", "/api/gw")["clients"]
        host = gmodel.public_host(state.load())
        self.assertEqual(doc["domain"], None)
        self.assertEqual(doc["ip"], host)
        self.assertEqual(doc["hosts"], [{"host": host, "kinds": ["wg"]}])

    def test_vless_tls_and_sstp_on_the_domain_name_the_domain(self):
        self.gateway(("wg",))
        st = state.load()
        for k in ("sstp", "vless"):                         # records only: the address is read from the state
            st["gateway"]["inbounds"][k] = {"enabled": True, "port": 443}
        st["site"] = {"domain": "vpn.example.com", "mode": "site", "cert": "letsencrypt"}
        host = gmodel.public_host(st)
        with mock.patch("kaskad.gw.ops.vless_view", return_value={"security": "tls", "domain": "vpn.example.com"}), \
                mock.patch("kaskad.site.model.use_for", side_effect=lambda s_, k: "vpn.example.com" if k == "sstp" else None):
            doc = api._client_addresses(st)
        self.assertEqual((doc["domain"], doc["domain_unicode"], doc["ip"]), ("vpn.example.com", "vpn.example.com", host))
        self.assertEqual(doc["hosts"], [{"host": host, "kinds": ["wg"]}, {"host": "vpn.example.com", "kinds": ["sstp", "vless"]}])
        # Reality: VLESS goes by «Адрес для клиентов» like the rest
        with mock.patch("kaskad.gw.ops.vless_view", return_value={"security": "reality", "domain": ""}):
            doc = api._client_addresses(st)
        self.assertEqual(doc["hosts"], [{"host": host, "kinds": ["wg", "sstp", "vless"]}])

    def test_devices_by_the_domain_the_ip_from_the_dns_check(self):
        self.gateway(("wg",))
        st = state.load()
        st["gateway"]["host"] = "vpn.example.com"
        st["site"] = {"domain": "vpn.example.com", "mode": "domain",
                      "dns": {"state": "ok", "a": ["198.51.100.7"], "aaaa": [], "own": ["198.51.100.7", "10.0.0.5"]}}
        doc = api._client_addresses(st)
        self.assertEqual((doc["ip"], doc["hosts"]), ("198.51.100.7", [{"host": "vpn.example.com", "kinds": ["wg"]}]))
        st["site"]["dns"] = None
        self.assertIsNone(api._client_addresses(st)["ip"])

    def test_the_panel_shows_the_domain_and_where_to_change(self):
        js, i18n, mock_js = read("app.js"), read("i18n-ru.js"), read("dev-mock.js")
        self.assertIn('const hostReal = () => (gwClients() && gwClients().domain ? gwClients().name : "") || (R.gw.data && R.gw.data.host)', js)
        self.assertIn("const hostName = () => (SM() ? KS.MASK : hostReal());", js)          # «Режим стрима» — маска
        self.assertIn('el("span", { id: "crumbHost", text: hostName(), title: hostTip() || null })', js)
        # где сменить адрес: «Настройки › Шлюз» — поиск ведёт на само поле адреса. Плитки «Шлюза» старой «Сводки» (ovTiles)
        # больше нет (REV-it1 №4): живая Сводка — часть ovb, адрес — в шапке (crumbHost, выше)
        self.assertIn('{ id: "settings.host", kind: "field", group: "st.gw", label: "st.gwHost", hint: "st.gwHostD", '
                      'syn: "find.hostSyn", need: "gw", at: { tab: "gateway", focus: "settings.host" } },', js)
        self.assertNotIn("function ovTiles", js)
        self.assertIn('if (n.open === "host") settingsFocusHost();', js)                  # UI-5: build(root, n) и phone
        self.assertIn('const n = $("#stGwAddr") || $("#stGw");', js)           # круг 1 П4: адрес — только в «Шлюзе»
        self.assertIn('"sh.hostIp": "IP сервера: {ip}"', i18n)
        self.assertIn("clients: clientsDoc()", mock_js)


if __name__ == "__main__":
    unittest.main()
