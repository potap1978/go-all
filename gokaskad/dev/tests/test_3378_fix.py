"""3.3.7.8, правки по стенду T-3378 и проверке REV-3378-final.

Н-3 (блокер): Hysteria2 на 443/udp — не «xray держит 443/tcp». VLESS ушёл с 443 (Reality с согласием, выключен) при сайте
за xray и Hysteria2 на 443 — nginx снова на 443; узел, который уже попал в это состояние, лечится ``gw vless sync`` и
``gw apply`` (обновление зовёт его само)."""

import json
import os

from kaskad import util
from kaskad.gw import xray
from kaskad.i18n import tr
from kaskad.site import cli as site_cli
from kaskad.site import front
from kaskad.site import ops as site_ops
from tests import test_gw_vless_front as F
from tests.gw.tunnel_fakes import FakeOpenssl


def _deploy(inbounds):
    """/etc/kaskad/gw/xray.json with these inbounds only (what xray of the gateway runs, as the file says)."""
    util.ensure_dir(os.path.dirname(xray.config_path()), 0o755)
    util.atomic_write(xray.config_path(), json.dumps({"inbounds": inbounds}), 0o600)


VLESS_REALITY = {"tag": "gw-vless", "port": 8443, "protocol": "vless",
                 "streamSettings": {"network": "tcp", "security": "reality"}}
HY2_443 = {"tag": "gw-hy2", "port": 443, "protocol": "hysteria",
           "streamSettings": {"network": "hysteria", "hysteriaSettings": {"version": 2}, "security": "tls"}}


class HoldsTcp443Test(F.FrontCase):
    """Н-3: who holds TCP 443 — by the transport of the inbound, not by the number alone."""

    def test_hysteria2_on_443_is_udp(self):
        _deploy([VLESS_REALITY, HY2_443])
        self.assertFalse(front.xray_holds_443())
        _deploy([dict(HY2_443, protocol="hysteria2")])
        self.assertFalse(front.xray_holds_443())
        _deploy([{"tag": "x", "port": 443, "protocol": "vless", "streamSettings": {"network": "kcp"}}])
        self.assertFalse(front.xray_holds_443())
        _deploy([{"tag": "x", "port": 443, "protocol": "dokodemo-door", "settings": {"network": "udp"}}])
        self.assertFalse(front.xray_holds_443())

    def test_vless_reality_on_443_holds_it(self):
        _deploy([dict(VLESS_REALITY, port=443), HY2_443])
        self.assertTrue(front.xray_holds_443())
        _deploy([{"tag": "x", "port": 443, "protocol": "socks"}])                # no streamSettings: TCP
        self.assertTrue(front.xray_holds_443())
        _deploy([{"tag": "x", "port": 443, "protocol": "dokodemo-door", "settings": {"network": "tcp,udp"}}])
        self.assertTrue(front.xray_holds_443())

    def test_after_xray_brings_nginx_back_past_hysteria2(self):
        site_ops.mode(self.w.runner, "site", yes=True)
        self.assertIn("listen unix:", self.vhost())
        _deploy([VLESS_REALITY, HY2_443])                                      # VLESS left 443, Hysteria2 stays
        self.assertEqual(front.after_xray(self.w.runner), "")
        self.assertIn("listen 443 ssl", self.vhost())
        self.assertNotIn("listen unix:", self.vhost())

    def test_after_xray_keeps_nginx_behind_reality_on_443(self):
        site_ops.mode(self.w.runner, "site", yes=True)
        _deploy([dict(VLESS_REALITY, port=443), HY2_443])                      # Reality holds TCP 443: nginx cannot
        self.assertTrue(front.after_xray(self.w.runner))                        # the warning, nothing rewritten
        self.assertIn("listen unix:", self.vhost())


class Hy2On443Test(F.FrontCase):
    """Н-3 on the real core: TLS + the site + Hysteria2 on 443/udp (the wizard's default protocols)."""

    def setUp(self):
        super().setUp()
        self.ssl = FakeOpenssl()
        self.w.runner.binaries.add("openssl")
        self.w.runner.on(["openssl"], self.ssl)
        rc, doc, err = self.run_json("gw", "inbound", "install", "hy2")
        self.assertEqual(rc, 0, err)
        site_ops.mode(self.w.runner, "site", yes=True)
        self.assertEqual((self.rec()["security"], self.rec()["port"]), ("tls", 443))
        hy2 = [i for i in self.cfg()["inbounds"] if i["tag"] == "gw-hy2"]
        self.assertEqual(hy2[0]["port"], 443)
        self.assertIn("listen unix:", self.vhost())

    def assert_site_on_443(self):
        v = self.vhost()
        self.assertTrue("listen 443 ssl" in v and "listen unix:" not in v, "the vhost is behind the socket")
        p = site_ops.status(self.w.runner)["site"]["port443"]
        self.assertFalse(p["xray"] or p["orphan"])
        self.assertEqual(site_cli.port443_line(p, "site"), "Порт 443/tcp: на нём сайт (nginx).")

    def test_reality_with_consent_brings_the_site_back_to_443(self):
        rc, doc, err = self.run_json("gw", "vless", "mode", "reality", "--site", "ya.ru", "--accept-risks",
                                     "--risks", "r1a", "--yes")
        self.assertEqual(rc, 0, err)
        self.assertEqual((self.rec()["security"], self.rec()["port"]), ("reality", 8443))
        self.assert_site_on_443()
        self.assertFalse([w for w in doc.get("warnings") or [] if "без TLS" in w], doc.get("warnings"))

    def test_vless_disabled_brings_the_site_back_to_443(self):
        rc, doc, err = self.run_json("gw", "inbound", "disable", "vless")
        self.assertEqual(rc, 0, err)
        self.assert_site_on_443()
        self.assertFalse([w for w in doc.get("warnings") or [] if "без TLS" in w], doc.get("warnings"))

    def test_a_node_already_broken_heals_by_sync_and_by_apply(self):
        """3.3.7.8 before the fix left the vhost on the socket with VLESS on 8443: ``gw vless sync`` (the advice of the
        warning) and ``gw apply`` (k3_post_update calls it when the code changed) bring nginx back on 443."""
        for heal in (("gw", "vless", "sync"), ("gw", "apply")):
            self.ok("gw", "vless", "mode", "reality", "--site", "ya.ru", "--accept-risks", "--risks", "r1a", "--yes")
            front._rewrite(self.w.runner, True)                                 # the state of the stand (T-3378 §4.1)
            self.assertIn("listen unix:", self.vhost())
            p = site_ops.status(self.w.runner)["site"]["port443"]                 # status no longer says «на нём сайт»
            self.assertTrue(p["orphan"])
            self.assertEqual(site_cli.port443_line(p, "site"), tr("site_cli.p443.orphan"))
            self.ok(*heal)
            self.assert_site_on_443()
            self.ok("gw", "vless", "mode", "tls", "--yes")                       # back for the next round
            self.assertIn("listen unix:", self.vhost())


# ── Н-2: the windows of the consent and of the price read what the action asks (200), not a 409 per opening ──────────

import threading                                                             # noqa: E402
import unittest                                                              # noqa: E402

from kaskad.errors import ConflictError                                      # noqa: E402
from kaskad.gw import ops as gops                                            # noqa: E402
from kaskad.panel.once import Once                                           # noqa: E402
from tests import test_vless_mode as VM                                      # noqa: E402
from tests.panel.panelcase import PanelCase                                  # noqa: E402
from tests.test_vless_want import WantCase                                   # noqa: E402


class AskTest(WantCase):
    """VLESS Reality on 443, one device, a domain with a certificate of LE, the site off (WantCase)."""

    def refusal(self, fn, *a, **kw):
        with self.assertRaises(ConflictError) as cm:
            fn(*a, **kw)
        return cm.exception.data, str(cm.exception)

    def ask(self, do, site=None):
        before = VM.raw()
        got = gops.vless_ask(self.w.runner, do, site=site)
        self.assertEqual(VM.raw(), before, "the read wrote state")             # a question changes nothing
        self.assertTrue(got["ok"])
        return got

    def same(self, got, data, text):
        self.assertEqual(got["need"], data["need"])
        self.assertEqual(got["message"], text)
        for k in data:
            self.assertEqual(got[k], data[k], k)

    def test_tls_and_the_site_on_ask_the_price(self):
        self.same(self.ask("tls"), *self.refusal(gops.vless_mode, self.w.runner, "tls", via="panel"))
        self.same(self.ask("site_on"), *self.refusal(site_ops.mode, self.w.runner, "site"))
        self.assertEqual(self.ask("tls")["need"], "vless_change")

    def test_reality_asks_the_consent(self):
        site_ops.mode(self.w.runner, "site", yes=True)
        self.assertEqual(self.rec()["security"], "tls")
        got = self.ask("reality", "ya.ru")
        self.assertEqual((got["need"], got["site"], got["risks"]), ("reality_consent", "ya.ru", "r1a"))
        self.same(got, *self.refusal(gops.vless_mode, self.w.runner, "reality", site="ya.ru", via="panel"))
        self.assertEqual(self.ask("reality")["need"], "reality_consent")      # none named: «pick first», as POST

    def test_the_site_off_and_the_domain_away_ask_the_price(self):
        site_ops.mode(self.w.runner, "site", yes=True)
        self.same(self.ask("site_off"), *self.refusal(site_ops.mode, self.w.runner, "domain"))
        self.same(self.ask("clear"), *self.refusal(site_ops.clear, self.w.runner, way_in=True))
        got = self.ask("site_off", "vk.com")
        self.assertEqual(got["site"], "vk.com")
        self.assertIn("vk.com", got["text"])

    def test_nothing_to_ask_is_need_none_and_nothing_done(self):
        self.ok("gw", "device", "del", self.dev["id"])
        for do in ("tls", "site_on", "site_off", "clear"):
            self.assertIsNone(self.ask(do)["need"], do)
        self.assertEqual(self.rec()["security"], "reality")                   # dry: the transition not done
        site_ops.clear(self.w.runner, way_in=True)
        self.assertIsNone(self.ask("reality", "ya.ru")["need"])                # no domain: no consent, nothing done
        self.assertNotEqual(self.rec().get("want"), "reality_nodomain")

    def test_dry_takes_no_consent(self):
        from kaskad.errors import KaskadError
        with self.assertRaises(KaskadError):
            gops.vless_mode(self.w.runner, "reality", site="ya.ru", accept=True, risks="r1a", dry=True)
        with self.assertRaises(KaskadError):
            gops.vless_ask(self.w.runner, "rm -rf")


class AskRouteTest(PanelCase):
    def test_the_read_answers_200_and_checks_its_query(self):
        r = self.call("GET", "/api/gw/vless/ask?do=tls")
        self.assertEqual(r.status, 200, r.body)
        self.assertEqual(r.json()["need"], None)
        for bad in ("do=sh", "do=tls&x=1", "do=reality&site=a%20b", "do=reality&site=" + "a" * 254, ""):
            self.assertEqual(self.call("GET", "/api/gw/vless/ask?" + bad).status, 400, bad)
        self.assertEqual(self.call("GET", "/api/gw/vless/ask?do=tls", headers={"Sec-Fetch-Site": "cross-site"}).status,
                         403)
        self.assertEqual(self.call("GET", "/api/gw/vless/ask?do=tls", headers={"Origin": "https://evil.example"}).status,
                         403)


class OnceUnitTest(unittest.TestCase):
    def test_one_count_per_key_and_one_at_a_time(self):
        once, entered, release, calls, out = Once(), threading.Event(), threading.Event(), [], []

        def slow():
            calls.append(1)
            entered.set()
            self.assertTrue(release.wait(10))
            return {"n": len(calls)}
        ts = [threading.Thread(target=lambda: out.append(once(("tls", ""), slow)))]
        ts[0].start()
        try:
            self.assertTrue(entered.wait(10))
            ts.append(threading.Thread(target=lambda: out.append(once(("tls", ""), slow))))
            ts[1].start()
            for _ in range(500):
                if once.flight.get(("tls", ""), {}).get("n", 0) >= 2:
                    break
                threading.Event().wait(0.01)
            self.assertEqual(once.flight[("tls", "")]["n"], 2, "the second request does not wait for the count")
        finally:
            release.set()
            for t in ts:
                t.join(10)
        self.assertEqual((len(calls), out), (1, [{"n": 1}, {"n": 1}]))
        self.assertEqual(once.flight, {})
        self.assertEqual(once(("tls", ""), lambda: 7), 7)                       # the next one counts anew


# ── Н-13 / С2: «Да, выключить / включить сайт» in the bot — the consent to the price of <n> devices ─────────────────

from unittest import mock                                                    # noqa: E402

from kaskad import state                                                     # noqa: E402
from kaskad.site import model as smodel                                      # noqa: E402
from tests.test_vless_sh3 import _Ctx                                        # noqa: E402

NO_LINKS = {"domain": "", "count": 0, "port": True}       # the panel is not on the domain: the port stays as it is


class BotSiteModePriceTest(WantCase):
    """sdmy:onY<k> | sdmy:offY<k>[:<site>]: ``yes`` only while the price still names the devices of <k> (their number
    and print, ``_price_key``; as sdxy:Y<k>, REV-vless-fix З1); else, or a button with no <k> or no print (sent
    before), the core is not called with ``yes`` — the question again with the price as it is now."""

    def setUp(self):
        super().setUp()
        from kaskad.bot import site as bsite
        self.bsite = bsite
        p = mock.patch.object(site_ops, "people_links", return_value=dict(NO_LINKS))
        p.start()
        self.addCleanup(p.stop)
        self.ctx = _Ctx(self.w.runner)

    def ask(self):
        self.bsite.cb_site_mode_ask(self.ctx, "sdm")
        return self.ctx.said[-1][0], self.ctx.buttons()

    def press(self, data):
        from kaskad.bot import registry
        registry.load()
        fn, _ = registry.find_callback(data)
        self.assertIsNotNone(fn, data)
        fn(self.ctx, data)

    def site_mode(self):
        return smodel.peek(state.load())["mode"]

    def key(self, n):
        """The key of the devices of VLESS now (``_price_key``: «<n>.<print>», REV-3378-fix придирка 4)."""
        k = self.bsite._price_key(gops._reissue_devices(state.load(), "vless"))
        self.assertTrue(k.startswith("%d." % n), k)
        return k

    def three(self):
        self.add_device("Ноутбук", "windows")
        self.add_device("Планшет", "android")

    def test_off_stale_yes_after_a_4th_device(self):
        site_ops.mode(self.w.runner, "site", yes=True)
        self.assertEqual(self.rec()["security"], "tls")
        self.three()
        text, btns = self.ask()
        self.assertIn("устройств — 3", text)
        self.assertEqual(btns, ["sdmy:offY" + self.key(3), "sd"])     # the consent: these 3 devices
        self.add_device("Телевизор", "android")                       # a device after the question
        before = VM.raw()
        with mock.patch.object(site_ops, "mode", wraps=site_ops.mode) as m:
            self.press(btns[0])
        self.assertFalse([c for c in m.call_args_list if c.kwargs.get("yes")], m.call_args_list)
        self.assertEqual(VM.raw(), before)                            # nothing changed
        self.assertEqual((self.site_mode(), self.rec()["security"]), ("site", "tls"))
        self.assertIn("устройств — 4", self.ctx.said[-1][0])          # the question again, the new price
        self.assertIn("Телевизор", self.ctx.said[-1][0])
        self.assertEqual(self.ctx.buttons(), ["sdmy:offY" + self.key(4), "sd"])
        self.press("sdmy:offY" + self.key(4))
        self.assertEqual((self.site_mode(), self.rec()["security"]), ("domain", "reality"))

    def test_on_stale_yes_after_a_4th_device(self):
        self.three()                                                  # Reality, the site off, 3 devices
        text, btns = self.ask()
        self.assertIn("устройств — 3", text)
        self.assertEqual(btns, ["sdmy:onY" + self.key(3), "sd"])
        self.add_device("Телевизор", "android")
        before = VM.raw()
        with mock.patch.object(site_ops, "mode", wraps=site_ops.mode) as m:
            self.press(btns[0])
        self.assertFalse([c for c in m.call_args_list if c.kwargs.get("yes")], m.call_args_list)
        self.assertEqual(VM.raw(), before)
        self.assertEqual((self.site_mode(), self.rec()["security"]), ("domain", "reality"))
        self.assertIn("устройств — 4", self.ctx.said[-1][0])
        self.assertEqual(self.ctx.buttons(), ["sdmy:onY" + self.key(4), "sd"])
        self.press("sdmy:onY" + self.key(4))
        self.assertEqual((self.site_mode(), self.rec()["security"]), ("site", "tls"))

    def test_on_without_a_price_then_a_device(self):
        """No devices when asked: «Да» with 0 — a device came since: its links would change without a price — the
        question again (before the fix sdmy:onY passed ``yes`` with no price at all)."""
        self.ok("gw", "device", "del", self.dev["id"])
        text, btns = self.ask()
        self.assertNotIn("выдать заново", text)
        self.assertEqual(btns, ["sdmy:onY0", "sd"])
        self.add_device("Ноутбук", "windows")
        before = VM.raw()
        self.press(btns[0])
        self.assertEqual(VM.raw(), before)
        self.assertIn("устройств — 1", self.ctx.said[-1][0])
        self.assertEqual(self.ctx.buttons(), ["sdmy:onY" + self.key(1), "sd"])

    def test_off_with_the_cover_after_a_new_device(self):
        site_ops.mode(self.w.runner, "site", yes=True)
        self.put(mask="own", sni=F.DOMAIN, dest="%s:443" % F.DOMAIN)  # TLS, no cover kept: the bot picks it
        _, btns = self.ask()
        old = btns[0]
        self.assertTrue(old.startswith("sdmy:offY%s:" % self.key(1)), old)
        site = old[len("sdmy:offY%s:" % self.key(1)):]
        self.add_device("Ноутбук", "windows")
        before = VM.raw()
        self.press(old)
        self.assertEqual(VM.raw(), before)
        self.assertEqual(self.site_mode(), "site")
        self.assertTrue(self.ctx.buttons()[0].startswith("sdmy:offY%s:" % self.key(2)), self.ctx.buttons())
        self.press("sdmy:offY%s:" % self.key(2) + site)
        self.assertEqual((self.site_mode(), self.rec()["security"], self.rec()["sni"]), ("domain", "reality", site))

    def test_a_button_with_no_count_asks_again(self):
        """sdmy:onY | sdmy:offY[:<site>] — sent before the fix: no count of the consent — no ``yes``, the question;
        sdmy:onY1 | sdmy:offY1[:<site>] — a count with no print (before REV-3378-fix): the same."""
        for old in ("sdmy:onY", "sdmy:onY1"):
            before = VM.raw()
            self.press(old)
            self.assertEqual(VM.raw(), before, old)
            self.assertIn("устройств — 1", self.ctx.said[-1][0])
            self.assertEqual(self.ctx.buttons(), ["sdmy:onY" + self.key(1), "sd"], old)
        site_ops.mode(self.w.runner, "site", yes=True)
        before = VM.raw()
        for old in ("sdmy:offY", "sdmy:offY:www.example.com", "sdmy:offY1", "sdmy:offY1:www.example.com"):
            self.press(old)
            self.assertEqual(VM.raw(), before, old)
            self.assertEqual(self.ctx.buttons()[0], "sdmy:offY" + self.key(1), old)

    def test_done_already_shows_the_screen(self):
        """«Да, включить» of a question when the site is on already (by the panel, say): the screen, no question to
        switch it off."""
        site_ops.mode(self.w.runner, "site", yes=True)
        before = VM.raw()
        self.press("sdmy:onY1")
        self.assertEqual(VM.raw(), before)
        self.assertNotIn("sdmy:offY1", self.ctx.buttons())
        self.assertIn("sdm", self.ctx.buttons())                      # the screen of the domain

    def test_forged_data_is_an_old_button(self):
        from kaskad.bot import menus
        before = VM.raw()
        with mock.patch.object(self.bsite, "_mode_job") as job:
            for data in ("sdmy:onY1:www.example.com", "sdmy:offY1:bad host;rm", "sdmy:onYx", "sdmy:onY²",
                         "sdmy:offY1x", "sdmy:onY1234567", "sdmy:offY:bad host;rm", "sdmy:onY-1", "sdmy:offY 1",
                         "sdmy:offY1.ABCDEF", "sdmy:offY1.abcde", "sdmy:offY1.abcdefa", "sdmy:onY.abcdef"):
                self.press(data)
                self.assertEqual(self.ctx.said[-1][0], menus.old_button(), data)
        job.assert_not_called()
        self.assertEqual(VM.raw(), before)

    def test_the_button_fits_callback_data(self):
        self.assertEqual(self.bsite._off_data(True, "a" * 50 + ".example.com", 3), "sdmy:off0")
        self.assertEqual(self.bsite._off_data(True, "www.example.com", 123456), "sdmy:offY123456:www.example.com")
        self.assertEqual(self.bsite._off_data(False, "www.example.com", 3), "sdmy:off0")
        self.assertEqual(self.bsite._off_data(True, None, 2), "sdmy:offY2")


# ── Н-17 (решение координатора): домен с сертификатом, сайт на нём ещё не включали, есть устройства — «TLS уже можно» ─

from kaskad.gw import model as gm                                            # noqa: E402
from kaskad.site import model as site_model                                  # noqa: E402


class SiteNeverOnReadyTest(WantCase):
    """WantCase: VLESS Reality on 443, want tls, one device, a domain with a certificate of LE, the site never switched
    on under this domain (``template_id`` empty) — the state after the certificate came with devices (§3.3, T-3378 §3
    (б)). TLS can be had — the site goes on as a part of the transition: ``ready`` with the price, not ``no_site``. A
    site switched off by hand keeps ``no_site`` (§3.6)."""

    def setUp(self):
        super().setUp()
        p = mock.patch.object(site_ops, "people_links", return_value=dict(NO_LINKS))
        p.start()
        self.addCleanup(p.stop)
        self.put(want="tls")
        self.assertEqual(site_model.peek(state.load())["template_id"], "")

    def show(self):
        rc, doc, err = self.run_json("gw", "vless", "show")
        self.assertEqual(rc, 0, err)
        return doc

    def test_ready_with_the_price_everywhere(self):
        before = VM.raw()
        r = front.reason(state.load(), self.w.runner)
        self.assertEqual((r["reason"], r["forced"]), ("ready", False))
        show = self.show()
        self.assertEqual((show["reason"], show["forced"], show["fix"]["do"]), ("ready", False, "mode_tls"))
        self.assertEqual((show["recommend"]["mode"], show["recommend"]["devices"]), ("tls", 1))
        self.assertIn("выдать заново", show["recommend"]["text"])
        self.assertNotIn("tls_block", show)                          # no «TLS сейчас нельзя» next to «TLS уже можно»
        rc, out, err = self.run_cli("gw", "vless", "show")
        self.assertEqual(rc, 0, err)
        self.assertNotIn("нельзя", out)
        p = front.plan(state.load(), self.w.runner)                  # an implicit sync: no change (devices — no switch)
        self.assertEqual((p["security"], p["fields"], p["reason"]), ("reality", {}, "ready"))
        rc, doc, err = self.run_json("gw", "vless", "sync")
        self.assertEqual((rc, doc["changed"], doc["security"], doc["reason"]), (0, False, "reality", "ready"), err)
        self.assertEqual(self.rec()["security"], "reality")
        self.assertEqual(site_model.peek(state.load())["mode"], "domain")
        self.assertEqual(VM.raw(), before)                           # nothing switched silently (правило 20.10)
        from kaskad.bot import site as bsite
        lines = "\n".join(bsite.vless_lines(state.load()))
        self.assertIn("выдать заново", lines)                        # the bot: the recommendation with the price
        from kaskad.setup import steps
        v = steps.vless_now()
        self.assertEqual((v["reason"], v["devices"], v["recommend"]["devices"]), ("ready", 1, 1))   # the wizard: М2

    def test_the_two_buttons(self):
        """«Перейти на TLS» — the price, then TLS with the site on; «Оставить Reality» — the consent (TLS is
        possible)."""
        a = gops.vless_ask(self.w.runner, "reality", None)
        self.assertEqual(a["need"], "reality_consent")
        a = gops.vless_ask(self.w.runner, "tls", None)
        self.assertEqual((a["need"], len(a["devices"])), ("vless_change", 1))
        rc, doc, err = self.run_json("gw", "vless", "mode", "tls", "--yes")
        self.assertEqual(rc, 0, err)
        self.assertEqual((self.rec()["security"], site_model.peek(state.load())["mode"]), ("tls", "site"))

    def test_reality_nodomain_too(self):
        self.put(want="reality_nodomain")
        self.assertEqual(front.reason(state.load(), self.w.runner)["reason"], "ready")

    def test_the_site_switched_off_by_hand_keeps_no_site(self):
        site_ops.mode(self.w.runner, "site", yes=True)
        self.assertEqual(self.rec()["security"], "tls")
        site_ops.mode(self.w.runner, "domain", yes=True)
        self.assertTrue(site_model.peek(state.load())["template_id"])
        show = self.show()
        self.assertEqual((show["reason"], show["forced"], show["fix"]["do"]), ("no_site", True, "site_on"))
        self.assertIsNone(show["recommend"])
        self.assertEqual(front.plan(state.load(), self.w.runner)["reason"], "no_site")

    def test_a_new_domain_after_the_old_one_was_cleared(self):
        """T-3378 §3 (а)→(б): the site was on under the old domain, the domain cleared (the site with it), a new one
        with its certificate while devices have links — «TLS уже можно» again."""
        site_ops.mode(self.w.runner, "site", yes=True)
        site_ops.clear(self.w.runner, yes=True, vless_auto=True)
        self.assertEqual(self.rec()["security"], "reality")
        self.with_domain(issued="le")
        self.assertEqual((self.rec()["security"], site_model.peek(state.load())["mode"]), ("reality", "domain"))
        self.assertEqual(self.show()["reason"], "ready")

    def test_without_devices_or_after_a_refusal_no_site(self):
        self.put(tls_error={"at": "2026-10-24T01:00:00Z", "why": "xray"})
        self.assertEqual(front.reason(state.load(), self.w.runner)["reason"], "no_site")
        self.put(tls_error=None)
        st = state.load()
        gm.inbound(st, "vless").pop("tls_error", None)
        state.save(st)
        self.ok("gw", "device", "del", self.dev["id"])
        self.assertEqual(front.reason(state.load(), self.w.runner)["reason"], "no_site")    # §3.3: switches by itself
        self.put(want=None)
        st = state.load()
        gm.inbound(st, "vless").pop("want", None)
        state.save(st)
        self.assertEqual(front.reason(state.load(), self.w.runner)["reason"], "legacy")


# ── Н-16 (+ Н-5, Н-14): the text of «gw vless show» says why Reality and how to get TLS, by the reason (as --json) ────

class ShowTextTest(WantCase):
    def text(self):
        rc, out, err = self.run_cli("gw", "vless", "show")
        self.assertEqual(rc, 0, out + err)
        return out

    def test_no_domain(self):
        site_ops.clear(self.w.runner, way_in=True, yes=True)
        out = self.text()
        self.assertIn("Почему Reality: нет домена — запасной вариант.", out)
        self.assertIn("Как перейти на TLS: Добавьте домен: Настройки → Домен и сайт.", out)
        self.assertNotIn("нельзя", out)
        self.ok("gw", "vless", "mode", "reality")                                 # chosen without a domain
        self.assertIn("Почему Reality: выбран вами без домена.", self.text())

    def test_chosen_is_no_refusal(self):
        """Н-5: Reality chosen by the person — no «TLS сейчас нельзя … sync попробует»; the way back to TLS."""
        site_ops.mode(self.w.runner, "site", yes=True)
        self.ok("gw", "vless", "mode", "reality", "--site", "ya.ru", "--accept-risks", "--risks", "r1a", "--yes")
        out = self.text()
        self.assertRegex(out, r"Почему Reality: выбран вами \([^)]*\d\d:\d\d, (cli|admin)\)\.")
        self.assertIn("Перейти на TLS: kaskadctl gw vless mode tls", out)
        self.assertNotIn("нельзя", out)
        self.assertNotIn("gw vless sync", out)

    def test_busy_443_names_the_neighbour(self):
        """Н-14: 443 held by a neighbour — that, not «сайт на домене не включён»."""
        self.neighbour()
        out = self.text()
        self.assertIn("Почему Reality: 443 занят — ", out)
        self.assertIn("goAmnezia", out)
        self.assertNotIn("сайт на домене не включён", out)
        self.assertIn("Как перейти на TLS: Освободите порт 443", out)

    def test_ready_says_the_price(self):
        """The site never on (WantCase) — the price says the site goes on (REV-3378-fix С1); «Почему Reality» does not
        repeat the price (REV-3378-fix придирка 2: with no devices there is nothing to give again)."""
        self.put(want="tls")
        out = self.text()
        self.assertIn("Почему Reality: TLS со своим доменом уже можно.\n", out)
        self.assertIn("Можно перейти на TLS со своим доменом. Сайт включится, VLESS перейдёт на TLS со своим доменом — "
                      "ссылки VLESS выдать заново (устройств — 1: Телефон (Мама)). Перейти: kaskadctl gw vless mode tls",
                      out)
        self.assertNotIn("нельзя", out)
        site_ops.mode(self.w.runner, "site", yes=True)              # the site on, VLESS on TLS — then Reality by hand
        self.ok("gw", "vless", "mode", "reality", "--site", "ya.ru", "--accept-risks", "--risks", "r1a", "--yes")
        st = state.load()
        gm.inbound(st, "vless")["want"] = "tls"                     # the consent gone — «ready» with the site on
        gm.inbound(st, "vless").pop("reality_consent", None)
        state.save(st)
        self.assertEqual(front.reason(state.load(), self.w.runner)["reason"], "ready")
        out = self.text()
        self.assertIn("Цена: ключи VLESS нужно выдать заново (1 устройство). Перейти: kaskadctl gw vless mode tls", out)
        self.assertNotIn("Сайт включится", out)

    def test_english(self):
        from kaskad.i18n import use_lang
        site_ops.clear(self.w.runner, way_in=True, yes=True)
        with use_lang("en"):
            from kaskad.gw import cli as gcli
            from kaskad.gw import ops as gops2
            lines = gcli._vless_why(gops2.vless_show(self.w.runner))
        self.assertEqual(lines[0], "Why Reality: no domain — the backup option.")
        self.assertTrue(lines[1].startswith("How to switch to TLS: "), lines)


# ── REV-3378-final придирка 4: «просмотр копии» говорит заранее, что согласие на Reality не перенесётся ──────────────

class PreviewConsentTest(unittest.TestCase):
    def fst(self, consent_domain, site_domain="vpn.example.com", want="reality"):
        return {"gateway": {"inbounds": {"vless": {"want": want, "reality_consent": {
            "at": "2026-10-20T10:00:00Z", "via": "panel", "risks": "r1a", "domain": consent_domain}}}},
            "site": {"domain": site_domain}}

    def test_said_when_the_domain_differs(self):
        from kaskad import backup
        self.assertEqual(backup._vless_consent_gone(self.fst("old.example.com"), backup.PARTS, {}, False),
                         "old.example.com")
        self.assertIsNone(backup._vless_consent_gone(self.fst("vpn.example.com"), backup.PARTS, {}, False))
        self.assertIsNone(backup._vless_consent_gone(self.fst("old.example.com", want="tls"), backup.PARTS, {}, False))
        # «site» not imported: the domain of this node counts (none here) — the consent goes
        self.assertEqual(backup._vless_consent_gone(self.fst("vpn.example.com"), ("gateway",), {}, False),
                         "vpn.example.com")
        self.assertEqual(tr("backup.preview.vless_consent", domain="old.example.com")[:6], "VLESS:")


class PreviewConsentFlowTest(WantCase):
    """The same through ``backup.preview`` of a real copy: Reality chosen with a consent to this domain, the copy taken
    to a node of another domain — the warning before the import; the same domain — none."""

    def test_the_warning_before_the_import(self):
        from unittest import mock as _mock
        from kaskad import backup
        from tests.test_backup import PW, FakeOpenssl as BOpenssl
        self.w.runner.binaries.add("openssl")
        self.w.runner.on(["openssl"], BOpenssl(self.w.runner))
        for p in (_mock.patch.object(backup, "ITER", 100000),
                  _mock.patch.object(backup, "_here_ip", lambda runner: state.load()["gateway"].get("host") or "")):
            p.start()
            self.addCleanup(p.stop)
        site_ops.mode(self.w.runner, "site", yes=True)
        self.ok("gw", "vless", "mode", "reality", "--site", "ya.ru", "--accept-risks", "--risks", "r1a", "--yes")
        self.assertEqual(self.rec()["reality_consent"]["domain"], F.DOMAIN)
        doc = backup.unseal(self.w.runner, backup.export(self.w.runner, PW)["text"], PW)
        said = tr("backup.preview.vless_consent", domain=F.DOMAIN)
        self.assertNotIn(said, backup.preview(self.w.runner, doc)["warnings"])            # the same domain after
        got = backup.preview(self.w.runner, doc, parts=("gateway",), st={"site": {"domain": "other.example.com"}})
        self.assertIn(said, got["warnings"])
