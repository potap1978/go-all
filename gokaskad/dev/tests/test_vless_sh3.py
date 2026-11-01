"""VLESS: TLS with the node's own domain by default — step Ш3 of SPEC-vless-tls-default (§3.2 a new install of VLESS
and the wizard, §3.3 the domain came later, В12 one message «выдайте заново») and the findings of REV-vless-sh2
(серьёзно 1: a compound action switching the site on never hangs on TLS; заметно 1–4; придирки)."""

from unittest import mock

from kaskad import state
from kaskad.gw import model
from kaskad.site import front
from kaskad.site import model as smodel
from kaskad.site import ops as site_ops
from tests.test_qs330d_domain import DOMAIN, NEW, SwitchCase


class DomainChangeTlsRefusedTest(SwitchCase):
    """REV-vless-sh2 серьёзно 1: VLESS does not take TLS with the new name — the change of the domain goes through all
    the same (as on 4ddc8ba3): the site and the panel on the new domain, VLESS on Reality next to the site with the
    reason «tls_failed» and the hint; the undo of a change never leaves the old domain without its site."""

    def rec(self):
        return model.inbound(state.load(), "vless")

    def test_the_domain_changes_and_vless_stays_on_reality(self):
        self.certbot.pem = "other"
        with mock.patch.object(front, "xray_fronts", return_value=False):
            res = site_ops.set_domain(self.w.runner, NEW)
        s = smodel.peek(state.load())
        self.assertEqual((s["domain"], s["mode"]), (NEW, "site"))
        r = self.rec()
        self.assertEqual(r["security"], "reality")
        self.assertNotEqual(r["port"], 443)                                     # nginx holds 443 for the site
        self.assertEqual(r["want"], "tls")                                      # forced, not a choice
        self.assertIn("tls_error", r)
        self.assertNotIn("mode_pending", r)
        self.assertEqual(front.reason(state.load(), self.w.runner)["reason"], "tls_failed")
        text = " ".join(res.get("warnings") or [])
        self.assertIn("VLESS не перешёл на TLS", text)
        self.assertIn("gw vless mode tls", text)
        self.assertIn("listen 443 ssl", self.vhost())                           # the site itself on 443
        self.assertIn("server_name %s;" % NEW, self.vhost())

    def test_the_undo_of_a_change_brings_the_old_site_back_without_tls(self):
        self.certbot.pem = "other"
        with mock.patch.object(front, "xray_fronts", return_value=False), \
                mock.patch.object(site_ops, "_switch", side_effect=RuntimeError("boom")):
            with self.assertRaises(site_ops.Refusal) as e:
                site_ops.set_domain(self.w.runner, NEW)
        # TLS refused on the old name too: VLESS is not on its links of before — said (REV-vless-sh3 заметно 1)
        self.assertIn("VLESS — не на прежних ссылках", e.exception.doc["text"])
        s = smodel.peek(state.load())
        self.assertEqual((s["domain"], s["mode"]), (DOMAIN, "site"))            # the old site is ON again
        self.assertEqual(self.rec()["security"], "reality")
        self.assertIn("tls_error", self.rec())


from kaskad import errors                                                       # noqa: E402
from tests import test_gw_vless_front as F                                      # noqa: E402
from tests import test_vless_mode as VM                                         # noqa: E402
from tests.test_vless_want import GOOD, WantCase                                # noqa: E402


class PriceOffTextTest(WantCase):
    """REV-vless-sh2 заметно 1 (З-1): the price of the site off said truly — the cover will be foreign (the short risks of
    variant A), «вернуть TLS» only when VLESS is on TLS now, the choice of Reality with the own site said to go."""

    def own_reality(self):
        self.site_next_to_reality()                                             # Reality 8443 next to the site
        self.put(want="reality", reality_consent=dict(VM.ConsentVariantTest.OWN), mask="own", sni=F.DOMAIN,
                 dest="%s:443" % F.DOMAIN)

    def test_reality_with_the_own_site(self):
        self.own_reality()
        before = VM.raw()
        with self.assertRaises(errors.KaskadError) as e:
            site_ops.mode(self.w.runner, "domain")
        text = e.exception.data["text"]
        self.assertIn("выдаёт узел за другой сайт", text)                       # variant A: a foreign cover
        self.assertNotIn("Reality со своим сайтом уводит", text)
        self.assertNotIn("вернуть TLS", text)                                   # it was not on TLS
        self.assertIn("выбор Reality со своим сайтом снимется", text)
        self.assertEqual(VM.raw(), before)

    def test_tls_says_how_to_get_it_back(self):
        site_ops.mode(self.w.runner, "site", yes=True)
        self.assertEqual(self.rec()["security"], "tls")
        with self.assertRaises(errors.KaskadError) as e:
            site_ops.mode(self.w.runner, "domain")
        text = e.exception.data["text"]
        self.assertIn("Чтобы вернуть TLS", text)
        self.assertIn("выдаёт узел за другой сайт", text)
        self.assertNotIn("снимется", text)

    def test_the_cover_picked_by_the_system_drops_the_choice_of_the_own_site(self):
        """REV-vless-sh2 заметно 4, X2: the site off with nobody to ask (no devices) — ``sync`` picks a foreign cover,
        and the consent of variant B goes with the choice (``fit_consent`` in ``_sync``)."""
        self.own_reality()
        self.ok("gw", "device", "del", self.dev["id"])
        site_ops.mode(self.w.runner, "domain")
        r = self.rec()
        self.assertNotEqual(r["mask"], "own")
        self.assertEqual(r["want"], "tls")
        self.assertNotIn("reality_consent", r)
        self.assertIn("согласие", self.journal())


class MaskConsentTest(WantCase):
    """REV-vless-sh2 заметно 4 (X1) and придирка 1: a consent of variant B and a foreign cover by ``gw vless mask``."""

    def setUp(self):
        super().setUp()
        self.site_next_to_reality()
        self.put(want="reality", reality_consent=dict(VM.ConsentVariantTest.OWN), mask="own", sni=F.DOMAIN,
                 dest="%s:443" % F.DOMAIN)

    def test_mcp_and_panel_must_name_the_text(self):
        before = VM.raw()
        for via in ("mcp", "panel", "setup"):
            rc, doc, _ = self.run_json("gw", "vless", "mask", "www.microsoft.com", "--no-check", "--accept-risks",
                                       "--via", via)
            self.assertEqual(rc, errors.CONFLICT, (via, doc))
            self.assertEqual(doc["data"]["need"], "reality_consent")
            self.assertEqual(VM.raw(), before)
        rc, doc, err = self.run_json("gw", "vless", "mask", "www.microsoft.com", "--no-check", "--accept-risks")
        self.assertEqual(rc, 0, err)                                            # the CLI at a terminal saw the text

    def test_auto_with_a_consent_to_give_asks_to_name_the_site(self):
        from kaskad.gw.inbounds import vless as vin
        orig = vin.masks
        no_own = mock.patch.object(vin, "masks", lambda st, country=None: [
            r for r in orig(st, country=country) if r["mask"] != "own"])        # «auto» takes a foreign site
        before = VM.raw()
        for extra in ((), ("--accept-risks", "--risks", "r1a")):
            with no_own:
                rc, doc, _ = self.run_json("gw", "vless", "mask", "auto", "--no-check", *extra)
            self.assertEqual(rc, errors.CONFLICT, doc)
            self.assertEqual(doc["data"]["need"], "pick_first")
            self.assertIn("Сначала подберите сайт", doc["error"])
        self.assertEqual(VM.raw(), before)


class _Ctx(object):
    """A chat of the bot: what it says and its buttons; a job runs at once."""

    def __init__(self, runner):
        self.runner = runner
        self.app = self
        self.said = []

    def reply(self, text, kb=None):
        self.said.append((text, kb))

    send = reply

    def job(self, name, fn):
        fn()
        return True

    def buttons(self):
        kb = self.said[-1][1] or {}
        return [b.get("callback_data") for row in kb.get("inline_keyboard", []) for b in row]


class BotSiteOffPicksTest(WantCase):
    """REV-vless-sh2 заметно 3 (§3.6): «Выключить сайт» in the bot on TLS without a kept cover — the bot picks the site
    itself (the first that passes the check), names it in the question and passes it with «yes»."""

    def test_the_bot_names_the_cover_and_passes_it(self):
        from kaskad.bot import site as bsite
        site_ops.mode(self.w.runner, "site", yes=True)
        self.put(mask="own", sni=F.DOMAIN, dest="%s:443" % F.DOMAIN)
        self.assertEqual(self.rec()["security"], "tls")
        ctx = _Ctx(self.w.runner)
        bsite.cb_site_mode_ask(ctx, "sdm")
        text = ctx.said[-1][0]
        data = [d for d in ctx.buttons() if d.startswith("sdmy:")][0]
        from kaskad.gw import ops as gops
        key = bsite._price_key(gops._reissue_devices(state.load(), "vless"))      # REV-3378-fix придирка 4
        self.assertTrue(key.startswith("1."), key)
        self.assertTrue(data.startswith("sdmy:offY%s:" % key), data)            # T-3378 Н-13: 1 device priced
        site = data[len("sdmy:offY%s:" % key):]
        self.assertIn(site, text)                                               # named in the question
        self.assertNotIn("--vless-site", text)                                  # no flags of the CLI in Telegram
        self.assertIn("выдать заново", text)
        bsite.cb_site_mode_do(ctx, data)
        self.assertEqual(smodel.peek(state.load())["mode"], "domain")
        self.assertEqual((self.rec()["security"], self.rec()["sni"]), ("reality", site))

    def test_a_forged_site_is_an_old_button(self):
        from kaskad.bot import site as bsite
        ctx = _Ctx(self.w.runner)
        with mock.patch.object(bsite, "_mode_job") as job:
            bsite.cb_site_mode_do(ctx, "sdmy:offY:bad host;rm")
        job.assert_not_called()


# ── Ш3: a new install of VLESS (§3.2) ────────────────────────────────────────────────────────────────────────────────

class InstallCase(F.FrontCase):
    """The node of FrontCase without VLESS yet: the gateway on, «Мама», no devices; the domain as the test puts it."""

    def setUp(self):
        from kaskad.setup import steps
        from kaskad.site import consumers, nginx, templates  # noqa: F401
        from tests.fakes_nft import save_state
        super(F.FrontCase, self).setUp()                                        # FrontCase.setUp but the install
        self.site_fakes()
        self.nginx = F.FakeNginx()
        self.w.runner.binaries.add("nginx")
        self.w.runner.on(["nginx"], self.nginx)
        self.w.sysd.units["nginx"] = {"enabled": True, "active": True, "packaged": True}
        deployed = {"ok": True, "id": "demo", "name": "Демо", "source": "repo", "fallback": False}
        for p in (mock.patch.object(templates, "deploy", return_value=deployed),
                  mock.patch.object(consumers, "_reload_panel", return_value=False),
                  mock.patch.object(steps, "online", lambda force=False: True)):
            p.start()
            self.addCleanup(p.stop)
        save_state([])
        self.ok("gw", "enable")
        self.ok("gw", "user", "add", "Мама")

    def install(self):
        rc, doc, err = self.run_json("gw", "inbound", "install", "vless")
        self.assertEqual(rc, 0, err)
        return doc

    def site(self):
        return smodel.peek(state.load())

    def why(self):
        return front.reason(state.load(), self.w.runner)


class NewInstallTest(InstallCase):
    def test_no_domain_reality_with_the_reason(self):
        doc = self.install()
        r = self.rec()
        self.assertEqual((r["security"], r["want"]), ("reality", "tls"))
        self.assertEqual(self.why()["reason"], "no_domain")
        self.assertTrue(self.why()["forced"])
        self.assertIn("Нет домена — запасной вариант Reality", " ".join(doc["notes"]))
        self.assertNotIn("reality_consent", r)

    def test_a_domain_with_its_certificate_switches_the_site_on(self):
        self.with_domain(issued="le")                                           # no VLESS: nothing of §3.3
        self.assertNotEqual(self.site()["mode"], "site")
        doc = self.install()
        r = self.rec()
        self.assertEqual((r["security"], r["port"], r["want"]), ("tls", 443, "tls"))
        self.assertEqual(self.site()["mode"], "site")
        self.assertIn("listen unix:", self.vhost())                             # nginx behind xray
        self.assertIn("включён обычный сайт", " ".join(doc["notes"]))
        self.assertNotIn("mode_pending", r)

    def test_a_domain_waiting_for_its_certificate(self):
        site_ops.set_domain(self.w.runner, F.DOMAIN)
        doc = self.install()
        self.assertEqual((self.rec()["security"], self.rec()["want"]), ("reality", "tls"))
        self.assertEqual(self.why()["reason"], "no_cert")
        self.assertIn("Ждём сертификат домена", " ".join(doc["notes"]))

    def test_a_neighbour_on_443_keeps_reality_and_the_install_goes(self):
        self.with_domain(issued="le")
        WantCase.neighbour(self)                                                # goAmnezia on 443
        doc = self.install()
        self.assertEqual((self.rec()["security"], self.rec()["want"]), ("reality", "tls"))
        self.assertNotEqual(self.site()["mode"], "site")
        self.assertEqual(self.why()["reason"], "busy_443")
        self.assertIn("сайт на домене", " ".join(doc["notes"]))

    def test_the_panel_on_443_keeps_reality_and_the_install_goes(self):
        self.with_domain(issued="le")
        with mock.patch.object(front, "_panel_443", return_value=True):
            self.install()
            self.assertEqual(self.why()["sub"], "panel_443")
        self.assertEqual((self.rec()["security"], self.rec()["want"]), ("reality", "tls"))
        self.assertNotEqual(self.site()["mode"], "site")

    def test_xray_refusing_tls_leaves_reality_and_the_site_off(self):
        self.with_domain(issued="le")
        with mock.patch.object(front, "xray_fronts", return_value=False):
            doc = self.install()
        r = self.rec()
        self.assertEqual((r["security"], r["want"]), ("reality", "tls"))
        self.assertIn("tls_error", r)
        self.assertNotIn("mode_pending", r)
        self.assertNotEqual(self.site()["mode"], "site")                        # the snapshot back: the site off
        self.assertEqual(self.why()["reason"], "no_site")                       # «Включить сайт» tries TLS again
        self.assertIn("Повторить TLS", " ".join(doc["notes"]))

    def test_the_reinstall_keeps_the_choice_and_the_consent(self):
        """§3.2 П4: a reinstall with the kept record — ``want``, the consent and the cover as they were; ``tls_error``
        and ``mode_pending`` go; the site is not switched on by the install."""
        self.with_domain(issued="le")
        self.install()
        st = state.load()
        model.inbound(st, "vless").update(want="reality", reality_consent=dict(GOOD), security="reality",
                                          tls_error={"at": "x", "why": "y"})
        state.save(st)
        site_ops.mode(self.w.runner, "domain", yes=True, vless_site="www.microsoft.com")
        sni = self.rec()["sni"]
        self.ok("gw", "inbound", "uninstall", "vless")
        self.install()
        r = self.rec()
        self.assertEqual((r["want"], r["reality_consent"]["at"], r["sni"]), ("reality", GOOD["at"], sni))
        self.assertNotIn("tls_error", r)
        self.assertNotIn("mode_pending", r)
        self.assertNotEqual(self.site()["mode"], "site")

    def test_a_kept_record_without_want_stays_legacy(self):
        self.install()
        st = state.load()
        model.inbound(st, "vless").pop("want", None)
        state.save(st)
        self.ok("gw", "inbound", "uninstall", "vless")
        self.install()
        self.assertNotIn("want", self.rec())


class DomainLaterTest(InstallCase):
    """§3.3: VLESS installed without a domain (``want`` "tls", Reality «no_domain»); the domain and its first certificate
    came later — nobody has links: the site on and TLS by itself; devices have them: only the recommendation."""

    def issue(self):
        site_ops.set_domain(self.w.runner, F.DOMAIN)
        self.certbot.pem = "le"
        return site_ops.issue(self.w.runner)

    def test_nobody_has_links_tls_by_itself(self):
        self.install()
        res = self.issue()
        self.assertEqual((self.rec()["security"], self.rec()["port"]), ("tls", 443))
        self.assertEqual(self.site()["mode"], "site")
        text = " ".join(res["warnings"])
        self.assertIn("включён сам: VLESS перешёл на TLS", text)
        self.assertNotIn("Выдайте их заново", text)
        res = site_ops.issue(self.w.runner)                                     # not the first one: nothing again
        self.assertNotIn("включён сам", " ".join(res["warnings"]))

    def test_devices_have_links_only_the_recommendation(self):
        self.install()
        self.add_device()
        res = self.issue()
        self.assertEqual(self.rec()["security"], "reality")
        self.assertNotEqual(self.site()["mode"], "site")
        self.assertIn("можно перейти на TLS со своим доменом", " ".join(res["warnings"]))

    def test_a_staging_certificate_switches_nothing(self):
        self.install()
        site_ops.set_domain(self.w.runner, F.DOMAIN)
        self.certbot.pem = "staging"
        site_ops.issue(self.w.runner, staging=True)
        self.assertEqual(self.rec()["security"], "reality")
        self.assertNotEqual(self.site()["mode"], "site")

    def test_the_lock_of_a_transition_busy_skips_it(self):
        self.install()
        busy = errors.ConflictError("busy", data={"need": "busy"})
        from kaskad.gw import ops as gops
        with mock.patch.object(gops, "vless_to_tls", side_effect=busy):
            res = self.issue()
        self.assertTrue(res["ok"])                                              # the certificate stays issued
        self.assertEqual(self.rec()["security"], "reality")
        from kaskad import paths, util
        self.assertIn("пропущен", util.read_text(paths.log()) or "")

    def test_a_refusal_never_fails_the_certificate(self):
        self.install()
        with mock.patch.object(front, "xray_fronts", return_value=False):
            res = self.issue()
        self.assertTrue(res["ok"])
        self.assertEqual(self.rec()["security"], "reality")
        self.assertNotEqual(self.site()["mode"], "site")
        self.assertIn("tls_error", self.rec())
        self.assertIn("не включён", " ".join(res["warnings"]))

    def test_hysteria2_and_vless_one_message(self):
        """В12: whom to give links again of Hysteria2 and VLESS after the first certificate — one line."""
        from kaskad.site import consumers
        hy2 = {"name": "hy2", "action": "switch",
               "reissue": [{"device": "d1", "name": "Ноутбук", "kind": "hy2", "user_name": "Мама"}]}
        real = consumers.cert_installed

        def with_hy2(runner, lineage=None, force=False):
            got = real(runner, lineage=lineage, force=force)
            got["consumers"] = list(got.get("consumers") or []) + [hy2]
            return got
        self.install()
        with mock.patch.object(consumers, "cert_installed", with_hy2):
            res = self.issue()
        text = " ".join(res["warnings"])
        self.assertEqual(text.count("Выдайте их заново"), 1, text)
        self.assertIn("Hysteria2", text)
        self.assertEqual(self.rec()["security"], "tls")
        from kaskad.gw import ops as gops
        v = {"changed": True, "security": "tls", "line": "",
             "rows": [{"id": "d2", "name": "Телефон", "user_name": "Мама"}]}
        site_ops.clear(self.w.runner, yes=True)
        with mock.patch.object(consumers, "cert_installed", with_hy2), \
                mock.patch.object(gops, "vless_after_cert", return_value=v):
            res = self.issue()
        lines = [w for w in res["warnings"] if "Выдайте их заново" in w]
        self.assertEqual(len(lines), 1, res["warnings"])
        self.assertIn("Hysteria2", lines[0])
        self.assertIn("VLESS", lines[0])
        self.assertIn("Ноутбук", lines[0])
        self.assertIn("Телефон", lines[0])


class WizardSiteTest(InstallCase):
    """The wizard: its step «cert» never switches the site on by §3.3 (the card «Сайт и панель на домене» does, with
    «Не включать сайт»); its step «site» never hangs on TLS (REV-vless-sh2 серьёзно 1)."""

    def setUp(self):
        super().setUp()
        from kaskad.setup import model as stmodel
        stmodel.update(lambda d: d.update(password_set=True))

    def test_the_cert_step_leaves_the_site_to_its_card(self):
        from kaskad.setup import steps
        self.install()
        site_ops.set_domain(self.w.runner, F.DOMAIN)
        self.certbot.pem = "le"
        out = steps.run("cert", {}, self.w.runner)
        self.assertEqual(out["state"], "ok", out)
        self.assertNotEqual(self.site()["mode"], "site")
        self.assertEqual(self.rec()["security"], "reality")
        out = steps.run("site", {"template": "random"}, self.w.runner)
        self.assertEqual(out["state"], "ok", out)
        self.assertEqual((self.rec()["security"], out["data"]["vless"]["security"]), ("tls", "tls"))

    def test_the_site_step_goes_on_when_tls_is_refused(self):
        from kaskad.setup import steps
        self.install()
        with mock.patch.object(front, "xray_fronts", return_value=False):
            self.with_domain(issued="le")                                       # §3.3 outside the wizard: refused
            self.assertNotEqual(self.site()["mode"], "site")
            out = steps.run("site", {"template": "random"}, self.w.runner)
        self.assertEqual(out["state"], "ok", out)
        self.assertEqual(self.site()["mode"], "site")
        self.assertEqual(self.rec()["security"], "reality")
        self.assertIn("VLESS не перешёл на TLS", " ".join(out["data"]["warnings"]))
