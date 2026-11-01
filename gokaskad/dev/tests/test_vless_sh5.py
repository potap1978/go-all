"""VLESS: TLS with the node's own domain by default — the findings of REV-vless-sh3 fixed in step Ш5 of
SPEC-vless-tls-default (заметно 1–4, придирки 1–4): the undo of a change of the domain said truly, a new record with
devices given the access before the install, the rules of Ш3 pinned against the mutations M3–M7, the price of the
wizard's step «site», the lock busy after the first certificate, one «VLESS не перешёл на TLS»."""

import threading
from unittest import mock

from kaskad import errors, paths, state, util
from kaskad.gw import model
from kaskad.site import front
from kaskad.site import model as smodel
from kaskad.site import ops as site_ops
from tests import test_gw_vless_front as F
from tests.test_qs330d_domain import DOMAIN, NEW, SwitchCase
from tests.test_vless_sh3 import InstallCase
from tests.test_vless_want import GOOD


class SwitchBackTextTest(SwitchCase):
    """REV-vless-sh3 заметно 1 (P1b): the new site went on with TLS refused, then the switch broke; the old name took
    TLS back neither — the refusal must not say «VLESS работает как раньше»: why, and whom to give the links again."""

    def test_the_undo_says_vless_moved_and_whom_to_give_links(self):
        self.certbot.pem = "other"
        real, n = site_ops.mode, {"c": 0}

        def broken_after_the_site(*a, **k):
            got = real(*a, **k)
            if a[1] == "site" and k.get("keep_site") and n["c"] == 0:
                n["c"] += 1
                raise RuntimeError("boom-after-site")
            return got
        with mock.patch.object(front, "xray_fronts", return_value=False), \
                mock.patch.object(site_ops, "mode", side_effect=broken_after_the_site):
            with self.assertRaises(site_ops.Refusal) as e:
                site_ops.set_domain(self.w.runner, NEW)
        text = e.exception.doc["text"]
        r = model.inbound(state.load(), "vless")
        self.assertEqual(smodel.peek(state.load())["domain"], DOMAIN)
        self.assertEqual(r["security"], "reality")                              # TLS refused on the old name too
        self.assertNotIn("работают как раньше", text)
        self.assertIn("возвращён не полностью", text)
        self.assertIn("VLESS — не на прежних ссылках", text)
        self.assertIn("VLESS не перешёл на TLS", text)
        self.assertIn("Выдайте их заново", text)
        self.assertIn(self.dev["name"], text)

    def test_vless_back_as_it_was_says_nothing_of_it(self):
        self.certbot.pem = "other"
        with mock.patch.object(site_ops, "_switch", side_effect=RuntimeError("boom")):
            with self.assertRaises(site_ops.Refusal) as e:
                site_ops.set_domain(self.w.runner, NEW)
        text = e.exception.doc["text"]
        self.assertIn("работают как раньше", text)
        self.assertNotIn("Выдайте их заново", text)
        self.assertEqual(model.inbound(state.load(), "vless")["security"], "tls")

    def test_one_reason_and_no_sync_hint(self):
        """придирка 2 (P1a): «VLESS не перешёл на TLS» once; «gw vless sync» (it never tries TLS again) not advised."""
        self.certbot.pem = "other"
        with mock.patch.object(front, "xray_fronts", return_value=False):
            res = site_ops.set_domain(self.w.runner, NEW)
        text = " ".join(res.get("warnings") or [])
        self.assertEqual(text.count("VLESS не перешёл на TLS"), 1, text)
        self.assertNotIn("gw vless sync", text)
        self.assertIn("gw vless mode tls", text)
        self.assertNotIn("gw vless sync", model.inbound(state.load(), "vless")["tls_error"]["why"])


class NewRecordPriceTest(InstallCase):
    """REV-vless-sh3 заметно 2 (P2a, порча M7): devices given the access to VLESS before its install have no links of
    the new record — its keys are new, the price is 0: the site goes on, TLS, no note against itself."""

    def test_devices_given_access_before_the_install(self):
        self.with_domain(issued="le")
        rc, _, err = self.run_json("gw", "device", "add", "Мама", "Телефон", "--platform", "android",
                                   "--access", "vless")
        self.assertEqual(rc, 0, err)
        doc = self.install()
        r = self.rec()
        self.assertEqual((r["security"], r["port"], r["want"]), ("tls", 443, "tls"))
        self.assertEqual(self.site()["mode"], "site")
        notes = " ".join(doc["notes"])
        self.assertNotIn("не включён", notes)
        self.assertNotIn("перестанут работать", notes)


class Sh3RulesTest(InstallCase):
    """REV-vless-sh3 заметно 3: the rules of Ш3 the mutations M3–M6 broke unnoticed."""

    def issue(self):
        site_ops.set_domain(self.w.runner, F.DOMAIN)
        self.certbot.pem = "le"
        return site_ops.issue(self.w.runner)

    def test_m3_a_refusal_of_tls_before_stops_the_first_certificate(self):
        self.install()
        st = state.load()
        model.inbound(st, "vless")["tls_error"] = {"at": "x", "why": "old"}
        state.save(st)
        res = self.issue()
        self.assertTrue(res["ok"])
        self.assertEqual(self.rec()["security"], "reality")
        self.assertNotEqual(self.site()["mode"], "site")
        self.assertNotIn("включён сам", " ".join(res["warnings"]))

    def test_m5_not_the_first_certificate_switches_nothing(self):
        """P3b: TLS by §3.3, the person turned the site off (no devices: Reality, ``want`` stays) — the certificate
        issued again is not the first one: the site stays off."""
        self.install()
        self.issue()
        self.assertEqual(self.rec()["security"], "tls")
        site_ops.mode(self.w.runner, "domain", yes=True)
        self.assertEqual(self.rec()["security"], "reality")
        self.assertEqual(self.rec()["want"], "tls")
        res = site_ops.issue(self.w.runner)
        self.assertEqual(self.rec()["security"], "reality")
        self.assertNotEqual(self.site()["mode"], "site")
        self.assertNotIn("включён сам", " ".join(res["warnings"]))

    def test_m6_a_reinstall_of_the_kept_record_never_switches_the_site_on(self):
        """§3.2 П4: the kept record (``want`` "tls", the site off by the person) installed again is not new."""
        self.with_domain(issued="le")
        self.install()
        self.assertEqual(self.site()["mode"], "site")
        site_ops.mode(self.w.runner, "domain", yes=True, vless_site="www.microsoft.com")
        self.assertEqual((self.rec()["security"], self.rec()["want"]), ("reality", "tls"))
        self.ok("gw", "inbound", "uninstall", "vless")
        self.install()
        self.assertNotEqual(self.site()["mode"], "site")
        self.assertEqual(self.rec()["security"], "reality")

    def test_m4_the_site_never_goes_on_over_an_undo_that_failed(self):
        """keep_site: the transition refused and its undo failed (``restored`` false, ``mode_pending`` stays) — the
        site does not go on over it: the refusal goes up."""
        self.install()
        self.add_device()                                                       # §3.3: only the recommendation
        self.issue()
        self.assertNotEqual(self.site()["mode"], "site")
        from kaskad.gw import ops as gops
        failed = {"ok": False, "changed": False, "restored": False, "error": "undo failed", "why": "x"}
        with mock.patch.object(gops, "vless_to_tls", return_value=failed):
            with self.assertRaises(errors.KaskadError):
                site_ops.mode(self.w.runner, "site", yes=True, keep_site=True)
        self.assertNotEqual(self.site()["mode"], "site")


class AfterCertTest(InstallCase):
    """§3.3 after the first certificate: the lock busy (решение координатора 20.10), the conditions read again under
    the lock (придирка 1), the recommendation names the devices and the way (придирка 3)."""

    def issue(self):
        site_ops.set_domain(self.w.runner, F.DOMAIN)
        self.certbot.pem = "le"
        return site_ops.issue(self.w.runner)

    def test_the_lock_busy_says_put_off(self):
        self.install()
        site_ops.set_domain(self.w.runner, F.DOMAIN)
        self.certbot.pem = "le"
        held, done = threading.Event(), threading.Event()

        def hold():
            with front.vless_guard(self.w.runner):
                held.set()
                done.wait(10)
        t = threading.Thread(target=hold)
        t.start()
        self.assertTrue(held.wait(5))
        try:
            res = site_ops.issue(self.w.runner)
        finally:
            done.set()
            t.join()
        self.assertTrue(res["ok"])
        self.assertEqual(self.rec()["security"], "reality")
        text = " ".join(res["warnings"])
        self.assertIn("переход VLESS на TLS отложен".lower(), text.lower())
        self.assertIn("«Включить TLS»", text)
        self.assertNotIn("sync", text)
        self.assertIn("пропущен", util.read_text(paths.log()) or "")

    def test_a_choice_made_before_the_lock_wins(self):
        """P3a: Reality chosen with the consent between the first reading and the lock — nothing switches."""
        self.install()
        real, n = front.vless_guard, {"c": 0}

        def guard(*a, **k):
            if k.get("recover") is False and n["c"] == 0:
                n["c"] += 1
                st = state.load()
                model.inbound(st, "vless").update(want="reality", reality_consent=dict(GOOD, domain=F.DOMAIN))
                state.save(st)
            return real(*a, **k)
        with mock.patch.object(front, "vless_guard", side_effect=guard):
            res = self.issue()
        self.assertTrue(res["ok"])
        r = self.rec()
        self.assertEqual((r["security"], r["want"]), ("reality", "reality"))
        self.assertTrue(r.get("reality_consent"))
        self.assertNotEqual(self.site()["mode"], "site")

    def test_devices_appearing_before_the_lock_get_the_recommendation(self):
        self.install()
        real, n = front.vless_guard, {"c": 0}

        def guard(*a, **k):
            if k.get("recover") is False and n["c"] == 0:
                n["c"] += 1
                self.add_device()
            return real(*a, **k)
        with mock.patch.object(front, "vless_guard", side_effect=guard):
            res = self.issue()
        self.assertEqual(self.rec()["security"], "reality")
        self.assertIn("Телефон", " ".join(res["warnings"]))

    def test_the_recommendation_names_the_devices_and_the_way(self):
        self.install()
        self.add_device()
        res = self.issue()
        line = [w for w in res["warnings"] if "перейти на TLS" in w]
        self.assertEqual(len(line), 1, res["warnings"])
        self.assertIn("1 устройство: Телефон (Мама)", line[0])
        self.assertIn("«Маскировка VLESS»", line[0])
        self.assertIn("kaskadctl gw vless mode tls --yes", line[0])


class WizardSitePriceTest(InstallCase):
    """REV-vless-sh3 заметно 4: the wizard's step «site» takes ``yes`` from the request — devices with links of VLESS
    that the site on would change: a refusal with the price and nothing changed; ``yes`` true — the site on, TLS."""

    def setUp(self):
        super().setUp()
        from kaskad.setup import model as stmodel
        stmodel.update(lambda d: d.update(password_set=True))

    def test_the_price_first_then_yes(self):
        from kaskad.setup import steps
        self.install()
        dev = self.add_device()
        self.with_domain(issued="le")                                           # §3.3: devices — the recommendation
        self.assertNotEqual(self.site()["mode"], "site")
        link0 = self.link(dev["id"])[0].port
        out = steps.run("site", {"template": "random"}, self.w.runner)
        self.assertEqual(out["state"], "error", out)
        self.assertEqual((out["error"]["code"], out["data"]["need"]), ("conflict", "vless_change"))
        self.assertIn("выдать заново", out["error"]["text"].lower())
        self.assertTrue(out["error"]["action"]["yes"])
        self.assertNotEqual(self.site()["mode"], "site")
        self.assertEqual(self.rec()["security"], "reality")
        self.assertEqual(self.link(dev["id"])[0].port, link0)
        out = steps.run("site", {"template": "random", "yes": True}, self.w.runner)
        self.assertEqual(out["state"], "ok", out)
        self.assertEqual(self.site()["mode"], "site")
        self.assertEqual(self.rec()["security"], "tls")

    def test_yes_must_be_true_not_a_word(self):
        from kaskad.setup import steps
        self.install()
        self.add_device()
        self.with_domain(issued="le")
        out = steps.run("site", {"template": "random", "yes": "1"}, self.w.runner)
        self.assertEqual(out["state"], "error", out)
        self.assertNotEqual(self.site()["mode"], "site")


class BotVlessLinesTest(InstallCase):
    """§4.2 «Бот»: the mode of VLESS, its reason and the recommendation in the summaries of the bot — no consent and no
    choice of Reality there (the way is the panel or the SSH menu)."""

    def lines(self):
        from kaskad.bot import site as bsite
        return bsite.vless_lines(state.load())

    def test_reality_without_a_domain(self):
        self.install()
        text = " ".join(self.lines())
        self.assertIn("VLESS: Reality — у узла нет домена.", text)
        self.assertNotIn("💡", text)

    def test_the_fix_after_the_first_certificate_with_devices(self):
        self.install()
        self.add_device()
        self.with_domain(issued="le")                                           # devices: the site stays off
        text = " ".join(self.lines())
        # T-3378 Н-17: the site never on under this domain — «TLS уже можно» with the price (the site goes on with TLS)
        self.assertIn("VLESS: Reality — TLS со своим доменом уже можно включить", text)
        # REV-3378-fix С1: the price says the site goes on with the transition
        self.assertIn("💡 Можно перейти на TLS со своим доменом. Сайт включится, VLESS перейдёт на TLS со своим доменом — "
                      "ссылки VLESS выдать заново (устройств — 1: ", text)
        self.assertNotIn("Включите сайт на домене", text)

    def test_the_recommendation_and_the_way(self):
        from kaskad.gw import ops as gops
        self.install()
        view = {"security": "reality", "reason": "ready", "reason_text": "TLS со своим доменом уже можно включить",
                "recommend": {"mode": "tls", "devices": 1, "text": "Можно перейти на TLS со своим доменом. Цена: ключи "
                              "VLESS нужно выдать заново (1 устройство)."}, "fix": {"do": "mode_tls", "text": "x"}}
        with mock.patch.object(gops, "vless_view", return_value=view):
            text = " ".join(self.lines())
            from kaskad.bot import gateway
            summary = gateway.status_text(state.load(), {}, 0)
        self.assertIn("VLESS: Reality", text)
        self.assertIn("Цена: ключи VLESS нужно выдать заново (1 устройство)", text)
        self.assertIn("«Маскировка VLESS»", text)
        self.assertIn("«Маскировка VLESS»", summary)
        self.assertNotIn("Оставить Reality", text)
        self.assertNotIn("Понимаю риски", text)

    def test_tls(self):
        self.with_domain(issued="le")
        self.install()
        self.assertEqual(self.lines(), ["🔐 VLESS: TLS со своим доменом."])

    def test_no_vless_no_lines(self):
        self.assertEqual(self.lines(), [])
