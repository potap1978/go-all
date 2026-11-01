"""T13 N5 (SPEC-stage1-rename §4.1): the panel's renames — the API calls are ordinary writes (no long APPLY timeout,
no ``rev``), every place of §4.1 opens the rename, the fields of the creation take 40 characters, Kit has the dialog
and the hint of the id, the texts exist in RU and EN."""

import os
import re
import unittest

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib", "kaskad", "panel", "static")


def read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


class PanelRenameStaticTest(unittest.TestCase):
    def setUp(self):
        self.app = read("app.js")

    def test_api_calls_are_light(self):
        for pat in (r'rename: \(id, b\) => request\("PUT", "/api/gw/users/" \+ enc\(id\) \+ "/name", b\)',
                    r'rename: \(id, b\) => request\("PUT", "/api/gw/devices/" \+ enc\(id\) \+ "/name", b\)',
                    r'label: \(k, b\) => request\("PUT", "/api/gw/inbounds/" \+ enc\(k\) \+ "/label", b\)',
                    r'rename: \(proto, port, b\) => request\("PUT", "/api/forwards/"'):
            m = re.search(pat + r"[^\n]*", self.app)
            self.assertIsNotNone(m, pat)
            self.assertNotIn("timeout", m.group(0).split("//")[0], pat)
            self.assertNotIn("rev", m.group(0).split("//")[0], pat)

    def test_every_place_opens_the_rename(self):
        for fn in ("renameUser(u)", "renameDevice(d)", "renameExit(r)", "renameInbound(r)", "renameSub(x)",
                   "renameForward(f)"):
            # UI-B 23.10: «⋯» человека одно на всех ширинах (ppMenu, и в строке телефона) — одно место
            self.assertGreaterEqual(self.app.count(fn), 1 if fn in ("renameExit(r)", "renameUser(u)") else 2, fn)
        self.assertIn('{ label: t("kit.rename"), icon: "pencil", on: () => renameUser(u) },', self.app)
        self.assertIn('U.menu(ppMenu(all), { label: t("c.moreOf", { title: u.name })', self.app)
        # UI-4 (таблица К1): id и логин устройства — строками «⋯» с копированием, а не меткой в строке
        self.assertIn('{ label: "id", icon: "copy", k: d.id, kmono: true, sep: true, on: () => copyText(d.id) }', self.app)
        # «Режим стрима»: в меню логин маской, копируется настоящий
        self.assertIn('{ label: t("pp.login"), icon: "copy", k: SM() ? KS.MASK : d.login, kmono: true, on: () => copyText(d.login) }', self.app)
        self.assertIn('reset: { label: t("rn.resetExit") }', self.app)
        self.assertIn('reset: { label: t("rn.resetExit") }', read("pages/exits-groups.js"))
        # a subscription by IP has no domain: «Вернуть имя по умолчанию» («подписка N»), not «Вернуть домен»
        self.assertIn('reset: { label: t(x.domain ? "rn.resetSub" : "rn.resetSubIp") }', self.app)

    def test_creation_fields_take_40(self):
        for aria in ('aria: t("fw.name"), max: 40', 'aria: t("pp.userName"), ph: t("pp.userNamePh"), max: 40',
                     'aria: t("pp.devName"), ph: t("pp.devNamePh"), max: 40'):
            self.assertIn(aria, self.app)
        self.assertNotIn("max: 60", self.app)

    def test_the_limit_counts_characters_not_utf16(self):
        """REV-s1-t13, придирка 2: «max» of a field is code points, as the counter «n из 40» and the server: maxlength
        lets twice as many UTF-16 units in (an emoji is two), the input handler cuts to ``max`` characters."""
        from tests.test_332f_give import HAS_NODE, run_node
        setup = read("setup/setup.js")
        self.assertIn("maxlength: o.max ? o.max * 2 : null", self.app)
        self.assertIn("maxlength: o.max ? o.max * 2 : null", setup)
        m = re.search(r'if \(o\.max\) i\.addEventListener\("input", (\(ev\) => \{.*?\})\);', self.app)
        self.assertIsNotNone(m)
        if not HAS_NODE:
            self.skipTest("нет node")
        js = ("const o = {max: 40}; const i = {value: ''}; const f = %s;\nconst out = [];\n"
              "for (const v of ['😀'.repeat(25), 'я'.repeat(39) + '😀😀', 'abc', '😀'.repeat(40)]) {"
              " i.value = v; f({isComposing: false}); out.push([Array.from(i.value).length, i.value.length]); }\n"
              "i.value = '😀'.repeat(41); f({isComposing: true}); out.push([Array.from(i.value).length, 0]);\n"
              "console.log(JSON.stringify(out));" % m.group(1))
        self.assertEqual(run_node(js), [[25, 50], [40, 41], [3, 3], [40, 80], [41, 0]])

    def test_kit_and_texts(self):
        kit = read("kit.js")
        for name in ("renameDialog,", "idHint,", "NAME_MAX,"):
            self.assertIn("    " + name, kit)
        self.assertIn("const NAME_MAX = 40;", kit)
        self.assertNotRegex(kit, r"innerHTML|outerHTML|insertAdjacentHTML")
        for lang in ("ru", "en"):
            d = read("i18n-%s.js" % lang)
            for k in ("rn.user", "rn.device", "rn.inbound", "rn.sub", "rn.fwd", "rn.resetExit", "rn.resetSub",
                      "rn.resetSubIp", "rn.resetLabel", "rn.resetFwd", "rn.inboundBtn"):
                self.assertIn('"%s":' % k, d, (lang, k))
            k = read("kit-i18n-%s.js" % lang)
            for key in ("kit.nameCount", "kit.renamed", "kit.idHint"):
                self.assertIn('"%s":' % key, k, (lang, key))

    def test_mock_has_the_routes(self):
        mock = read("dev-mock.js")
        for frag in ("/name$/.exec(path)) && method === \"PUT\"", "\\/label$/.exec(path)", "body.label !== undefined"):
            self.assertIn(frag, mock)


if __name__ == "__main__":
    unittest.main()
