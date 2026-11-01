"""3.3.2 E1, the group «issue»: kaskad.gw.issue_os (what to offer a device and how to connect it — the SSH menu, the
panel, the person's page, the bot, the agent), kaskad.gw.model (people, devices, routes) and kaskad.gw.addr (the
address base) speak the language of the context; the Russian stays what it was."""

import json
import re
import unittest

from kaskad.errors import KaskadError
from kaskad.gw import addr, issue_os, model
from kaskad.i18n import check, use_lang
from tests.userpage_case import UID1, PageCase

CYR = re.compile("[А-Яа-яЁё]")
FILES = ("lib/kaskad/gw/issue_os.py", "lib/kaskad/gw/model.py", "lib/kaskad/gw/addr.py")
KINDS = list(issue_os.TITLES)
MENU_WORDS_EN = ("PuTTY", "Cmd+C", "Save to files", "this screen", "Termius", "WinSCP", "gokaskad")


def _err(fn, *a, **kw):
    try:
        fn(*a, **kw)
    except KaskadError as e:
        return e
    raise AssertionError("no refusal")


def _all_texts(where):
    """Every text issue_os gives one reader: the offers, the notes, the steps, the guides, the apps."""
    out = []
    for p in issue_os.PLATFORMS:
        out += [o["note"] for o in issue_os.offers(p)] + list(issue_os.NOTES.get(p, []))
        out += [issue_os.vless_apps_note(p)] + [issue_os.apps_note(p, line) for line in ("1.5", "2.0", "3.1")]
        for k in KINDS:
            out += issue_os.howto(p, k, where=where) + issue_os.steps(p, k, where=where)
            out.append(issue_os.builtin_gone(p, k))
    out += [issue_os.plain_off_note(where), issue_os.PLAIN_ON[where], issue_os.L2TP_PLAIN_PATH]
    return [t for t in out if t]


class CatalogTest(unittest.TestCase):
    def test_no_russian_left_in_code(self):
        check.assert_no_cyrillic(self, *FILES)

    def test_catalogs_complete(self):
        check.assert_catalog_complete(self, ["gw_issue", "gw_model", "gw_addr"])


class IssueEnglishTest(unittest.TestCase):
    def test_every_os_every_reader_in_english(self):
        with use_lang("en"):
            for w in issue_os.WHERE:
                texts = _all_texts(w)
                self.assertGreater(len(texts), 300, w)
                bad = [t for t in texts if CYR.search(t) or "{" in t or "}" in t]
                self.assertEqual(bad, [], w)

    def test_the_same_flow_and_short_steps_in_english(self):
        for w in issue_os.WHERE:
            for p in issue_os.PLATFORMS:
                for k in KINDS:
                    ru, ru_steps = issue_os.howto(p, k, where=w), issue_os.steps(p, k, where=w)
                    with use_lang("en"):
                        en, en_steps = issue_os.howto(p, k, where=w), issue_os.steps(p, k, where=w)
                    self.assertEqual((len(en), len(en_steps)), (len(ru), len(ru_steps)), (w, p, k))
                    self.assertTrue(all(len(s) <= 240 for s in en), (w, p, k))

    def test_other_screens_do_not_speak_the_menu_in_english(self):
        with use_lang("en"):
            for w in ("panel", "page", "bot"):
                for p in issue_os.PLATFORMS:
                    for k in KINDS:
                        text = " ".join(issue_os.howto(p, k, where=w))
                        for word in MENU_WORDS_EN:
                            self.assertNotIn(word, text, (w, p, k, word))
                self.assertNotIn("gokaskad → 17 → 1 → 5 (L2TP/IPsec)", " ".join(issue_os.steps("tplink", "l2tp", where=w)))

    def test_english_words_of_each_screen(self):
        with use_lang("en"):
            menu = issue_os.howto("android", "awg3")
            self.assertEqual(len(menu), 4)
            self.assertTrue(menu[0].startswith("Install AmneziaVPN 5.0.1.5 or later from Google Play"), menu[0])
            self.assertIn("from vpn:// to the end", menu[1])
            self.assertIn("PuTTY", menu[1])
            self.assertIn("on your phone", menu[2])
            self.assertEqual(issue_os.howto("android", "awg3", where="panel")[1],
                             "Copy the vpn:// link below with the “Copy” button — it takes the whole link, to the end.")
            self.assertEqual(issue_os.howto("windows", "ikev2", where="panel")[0],
                             "Download the script kaskad-ikev2.ps1 with the “Download” button below.")
            self.assertEqual(issue_os.howto("keenetic", "awg2", where="page")[0],
                             "Download the file with the “Download file” button below.")
            win = issue_os.howto("windows", "sstp")
            self.assertEqual(win[2], "Run: powershell -ExecutionPolicy Bypass -File .\\kaskad-sstp.ps1")
            self.assertIn("“Kaskad SSTP” → “Connect”", win[3])
            self.assertIn("Interfaces → kaskad-wg", issue_os.howto("mikrotik", "wg", where="bot")[2])
            self.assertIn("vless://", issue_os.howto("windows", "vless", where="agent")[1])
            self.assertIn("gokaskad → 17 → 1 → 5 (L2TP/IPsec) → 2 (L2TP without encryption)",
                          issue_os.steps("tplink", "l2tp")[2])
            self.assertIn("ask whoever sent you the link", issue_os.plain_off_note("page"))
            with issue_os.issued_for("panel"):                         # the guides of the adapters: the reader of ops
                self.assertIn("“Protocols”", issue_os.plain_off_note())
            self.assertEqual(issue_os.builtin_gone("ios", "sstp"), "iOS has no built-in SSTP — use IKEv2.")
            self.assertTrue(issue_os.apps_note("windows", "3.1").startswith("Apps: AmneziaVPN 5.0.1.5+"))
            self.assertEqual(issue_os.vless_apps_note("ios"), "Apps: Karing, incy, Shadowrocket")
            self.assertIn("MikroTik (container)", issue_os.apps_note("mikrotik", "2.0"))

    def test_offer_notes_in_english_do_not_repeat_the_protocol(self):
        with use_lang("en"):
            for p in issue_os.PLATFORMS:
                for o in issue_os.offers(p):
                    title = issue_os.TITLES[o["inbound"]]
                    self.assertIsNone(re.match(re.escape(title) + r"\s*([:,]|—|$)", o["note"]), (p, o))
            self.assertEqual(dict((o["inbound"], o["note"]) for o in issue_os.offers("tplink"))["pptp"],
                             "only if the protocol is on")

    def test_constants_follow_the_language_of_the_moment(self):
        """NOTES, PLAIN_ON and L2TP_PLAIN_PATH are read at the moment, not frozen at import (ops.offers, tests)."""
        ru_note = issue_os.NOTES["tplink"][0]
        self.assertIn("роутер не подойдёт", ru_note)
        self.assertTrue(issue_os.L2TP_PLAIN_PATH.startswith("17 → 1 → 5 (L2TP/IPsec) → 2 (L2TP без"))
        with use_lang("en"):
            self.assertIn("the router won’t work", issue_os.NOTES["tplink"][0])
            self.assertEqual(issue_os.NOTES.get("huawei"), ["These routers usually have only L2TP and PPTP."])
            self.assertEqual(issue_os.L2TP_PLAIN_PATH, "17 → 1 → 5 (L2TP/IPsec) → 2 (L2TP without encryption)")
            self.assertTrue(issue_os.PLAIN_ON["menu"].endswith(issue_os.L2TP_PLAIN_PATH))
            plan = issue_os.plan({}, {"platform": "androidtv", "access": []})
            self.assertEqual(plan["notes"], ["A TV has no camera: paste the vpn:// link from the clipboard or load "
                                             "the .conf file."])
        self.assertEqual(issue_os.NOTES["tplink"][0], ru_note)                 # back to Russian outside
        self.assertEqual(issue_os.howto("android", "awg3")[3], "В AmneziaVPN: «+» → «Вставить» (ключ из буфера) → «Продолжить», затем «Подключиться».")
        with self.assertRaises(AttributeError):
            issue_os.NO_SUCH_THING                                              # noqa: B018


class ModelAddrEnglishTest(unittest.TestCase):
    def test_refusals_of_the_model(self):
        st = {"gateway": {"users": [{"id": "u-00000001", "name": "Mom", "route": None}],
                          "devices": [{"id": "d-00000001", "user": "u-00000001", "name": "Phone", "login": "phone-0001"}]}}
        with use_lang("en"):
            e = _err(model.get_user, st, "Nobody")
            self.assertIsInstance(e, model.NotFound)
            self.assertEqual(str(e), "No user Nobody (or the name is ambiguous — give the id).")
            e = _err(model.get_device, st, "x")
            self.assertIsInstance(e, model.NotFound)
            self.assertEqual(str(e), "No device x (or the name is ambiguous — give the id or login).")
            self.assertEqual(str(_err(model.add_user, st, "Mom")), "User “Mom” already exists.")
            self.assertEqual(str(_err(model.del_user, st, "Mom")),
                             "User “Mom” has devices (1): remove them or repeat with --force.")
            self.assertEqual(str(_err(model.add_user, st, "   ")), "User name: empty. Use 1 to 40 characters.")   # T13
            self.assertEqual(str(_err(model.add_device, st, "Mom", "")), "Device name: empty. Use 1 to 40 characters.")
            self.assertIn("device type — one of: phone", str(_err(model.add_device, st, "Mom", "Tab", kind="toaster")))
            self.assertIn("platform — one of: ios", str(_err(model.add_device, st, "Mom", "Tab", platform="beos")))
            self.assertIn("unknown inbound 'zz'; available: wg",
                          str(_err(model.add_device, st, "Mom", "Tab", access=["zz"])))
            self.assertEqual(str(_err(model.parse_route, "nowhere")),
                             "the route must be pool, group:<group id>, exit:<exit id>, direct or block, not 'nowhere'")
            self.assertEqual(str(_err(model.parse_route, "pool", "maybe")),
                             "on exit failure — block or direct, not 'maybe'")
            self.assertEqual(str(_err(model.parse_profile, "x")),
                             "rules profile — inherit, all_exit, ru_direct, not 'x'")
            self.assertEqual(str(_err(model.set_exit, st, "e-1")), "exit id — like e-3fa9c1d2b4, not 'e-1'")
            bad = {"gateway": {"users": [{"id": "bad", "rules": "x"}],
                               "devices": [{"id": "d-00000001", "user": "u-1", "login": "BAD"}]}}
            probs = model.validate(bad)
            self.assertEqual(probs[:2], ["user with an invalid id 'bad'", "user bad: unknown rules profile 'x'"])
            self.assertFalse([p for p in probs if CYR.search(p)], probs)
        e = _err(model.get_user, st, "Nobody")                                  # Russian by default, as it was
        self.assertEqual(str(e), "Пользователя Nobody нет (или имя неоднозначно — укажите id).")

    def test_refusals_of_the_address_base(self):
        with use_lang("en"):
            self.assertEqual(str(_err(addr.parse_base, "foo")),
                             "the gateway’s address base must be a network like 10.77.0.0/16, not 'foo'")
            self.assertEqual(str(_err(addr.parse_base, "8.8.0.0/16")),
                             "the gateway network must be a /16 within 10.0.0.0/8, 172.16.0.0/12 or 192.168.0.0/16, "
                             "not 8.8.0.0/16")
            self.assertEqual(str(_err(addr.subnet, "socks5")), "inbound socks5 has no subnet of its own")

    def test_why_a_base_is_taken(self):
        class Res:
            def __init__(self, out):
                self.ok, self.out = True, out

        class Runner:
            def run(self, argv, **kw):
                if "route" in argv:
                    return Res("10.0.0.0/8 dev eth1 proto kernel scope link\n")
                return Res("2: eth0    inet 172.16.5.2/12 brd 172.31.255.255 scope global eth0\n"
                           "3: eth2    inet 192.168.1.2/16 scope global eth2\n")

        with use_lang("en"):
            nets = addr.node_networks(Runner())
            self.assertIn("route 10.0.0.0/8 via eth1", [w for _n, w in nets])
            self.assertIn("address 172.16.5.2/12 on eth0", [w for _n, w in nets])
            e = _err(addr.choose_base, Runner())
            self.assertTrue(str(e).startswith("No free network for the gateway: "), str(e))
            self.assertIn(" is taken: ", str(e))
            self.assertFalse(CYR.search(str(e)), str(e))


class PageInEnglishTest(PageCase):
    """The person's page: the panel calls issue_os inside use_lang(the visitor's language)."""

    def test_steps_of_the_page_in_english(self):
        tok = self.token()
        with use_lang("en"):
            doc = self.keys_json(tok)
            phone = doc["devices"][0]
            wg = next(i for i in phone["inbounds"] if i["kind"] == "wg")
            self.assertEqual(wg["howto"], issue_os.howto("android", "wg", where="page"))
            self.assertIn("another screen", wg["howto"][2])
            laptop = doc["devices"][2]
            ike = next(i for i in laptop["inbounds"] if i["kind"] == "ikev2")
            self.assertEqual(ike["howto"][0], "Download the script kaskad-ikev2.ps1 with the “Download file” button below.")
            for d in doc["devices"]:
                for inb in d["inbounds"]:
                    self.assertFalse([s for s in inb["howto"] if CYR.search(s)], (d["name"], inb["kind"]))

    def test_no_such_person_is_404_in_any_language(self):
        """The panel told «not found» by the Russian word «нет» in the message; now by model.NotFound."""
        for lang in ("ru", "en"):
            with use_lang(lang):
                resp = self.call("GET", "/api/gw/users/u-ffffffff/page")
            self.assertEqual(resp.status, 404, lang)
            doc = json.loads(resp.body.decode("utf-8"))
            self.assertFalse(doc["ok"])
            if lang == "en":
                self.assertTrue(doc["error"].startswith("No user u-ffffffff"), doc)
        resp = self.call("POST", "/api/gw/users/%s/page" % UID1, body={"action": "explode"})
        self.assertEqual(resp.status, 400)


if __name__ == "__main__":
    unittest.main()
