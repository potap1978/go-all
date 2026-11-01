"""VLESS: TLS with the node's own domain by default, Reality as the fallback — step Ш1, the core of the choice
(k3/handoff/SPEC-vless-tls-default.md §3.1, §3.3, §3.7, §6 «Ш1», решения координатора после круга 3): ``want`` of the
record, the consent to Reality, ``front.plan`` explicit/implicit, ``front.reason``, ``will_front``, ``predict``,
``tls_error``, ``vless_view``, the rule «as before» for a record without ``want``, the port 443 next to the site."""

import copy
import json
import os
from unittest import mock

from kaskad import paths, state, util
from kaskad.i18n import tr
from kaskad.site import front
from kaskad.site import ops as site_ops
from kaskad.gw import model
from tests import test_gw_vless_front as F


def set_want(want, **extra):
    """The VLESS record of state with ``want`` (None — removed: a record of 3.3.7) and ``extra`` fields."""
    st = state.load()
    rec = model.inbound(st, "vless")
    if want is None:
        rec.pop("want", None)
    else:
        rec["want"] = want
    rec.update(extra)
    state.save(st)


class _Legacy(object):
    """A record made before the choice (З7): ``want`` taken out after ``gw inbound install vless``."""
    WANT = None

    def setUp(self):
        super().setUp()
        set_want(self.WANT)


class _NewRecord(_Legacy):
    """A record of this version: ``want`` "tls" (the default of a new install, §3.2)."""
    WANT = "tls"


# the 19 tests of test_gw_vless_front.py, on both kinds of the record
_CLASSES = (F.SiteModeSwitchesVlessTest, F.SchemeTest, F.CountryTest, F.HotPlanTest, F.BackupTest, F.BackupFlowTest,
            F.WizardTest)
for _cls in _CLASSES:
    for _mix, _suffix in ((_Legacy, "Legacy"), (_NewRecord, "WantTls")):
        _name = "%s_%s" % (_cls.__name__, _suffix)
        globals()[_name] = type(_name, (_mix, _cls), {})
del _cls, _mix, _suffix, _name


# ── the one test of the 19 whose expectation changes for a record with ``want`` ────────────────────────────────────

def _moved_to_443_refused(self):
    """З-2 (решения координатора после круга 3): a record with ``want`` never switches the mode by a port — VLESS on
    443 next to the site is refused with the hint «gw vless mode tls», whether TLS is possible now or not; nothing
    changes. (A record of 3.3.7 keeps the old way — TLS in front; the Legacy twin checks it.)"""
    from kaskad import errors
    self.site_next_to_reality()                                             # VLESS stepped aside to 8443
    self.nginx.refuse = "set_real_ip_from"
    rc, doc, _ = self.run_json("gw", "inbound", "port", "vless", "443")     # TLS not possible: refused, with the hint
    self.assertEqual(rc, errors.CONFLICT)
    self.assertIn("gw vless mode tls", doc["error"])
    self.assertEqual(self.rec()["port"], 8443)
    self.nginx.refuse = None
    rc, doc, _ = self.run_json("gw", "inbound", "port", "vless", "443")     # TLS possible: still refused
    self.assertEqual(rc, errors.CONFLICT)
    self.assertIn("kaskadctl gw vless mode tls", doc["error"])
    self.assertIn("выдать заново", doc["error"])
    self.assertEqual((self.rec()["port"], self.rec()["security"]), (8443, "reality"))
    self.assertIn("listen 443 ssl", self.vhost())


SiteModeSwitchesVlessTest_WantTls.test_vless_moved_to_443_next_to_the_site_goes_tls_in_front = \
    _moved_to_443_refused                                                   # noqa: F821 — made by the loop above


GOOD = {"at": "2026-10-20T10:00:00Z", "via": "panel", "who": "admin", "risks": "r1a", "site": "www.microsoft.com",
        "domain": F.DOMAIN, "lang": "ru"}


class WantCase(F.FrontCase):
    """The node of FrontCase (VLESS Reality on 443, one device «Телефон», a domain with a certificate of Let's Encrypt,
    the site off) and helpers to put it into a state of the matrix."""

    def runner(self):
        return self.w.runner

    def put(self, **fields):
        st = state.load()
        model.inbound(st, "vless").update(fields)
        state.save(st)

    def neighbour(self):
        d = paths.P("/etc/nginx/sites-enabled")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "goamnezia"), "w") as f:
            f.write("server { listen 443 ssl; server_name example.com; }\n")

    def link_fields(self):
        u, q, _ = self.link(self.dev["id"])
        return (q.get("security"), u.hostname, u.port, q.get("sni"), q.get("pbk"))

    def journal(self):
        return util.read_text(paths.log()) or ""

    def site_on_reality(self):
        """The site on while VLESS stays on Reality next to it (8443): the way a node with devices gets there."""
        self.site_next_to_reality()


class MatrixTest(WantCase):
    """want × domain × certificate × site × 443 (a neighbour / the open mode of the panel) × consent × devices ->
    the mode, the port and the reason of ``plan`` and ``reason`` (no switch: what an implicit ``sync`` would do)."""

    def check(self, st, want=None, sec=None, port="same", reason=None, forced=None, sub=None):
        rec = model.inbound(st, "vless")
        p = front.plan(st, self.w.runner, pick=lambda s: {"sni": "picked.example.com", "mask": "list"}, want=want)
        self.assertEqual(p["security"], sec)
        cur = rec.get("port")
        want_port = cur if port == "same" else port
        got_port = p["fields"].get("port", cur) if "port" in p["fields"] else cur
        if got_port is None:
            got_port = "free"
        self.assertEqual(got_port, want_port)
        if want is None and reason is not None:
            r = front.reason(st, self.w.runner)
            self.assertEqual(r["reason"], reason)
            if forced is not None:
                self.assertEqual(r["forced"], forced)
            if sub is not None:
                self.assertEqual(r["sub"], sub)

    def st(self, want="tls", domain=True, cert=True, site="domain", port=443, sec="reality", **rec):
        st = copy.deepcopy(state.load())
        r = model.inbound(st, "vless")
        r.update(port=port, security=sec)
        if want is None:
            r.pop("want", None)
        else:
            r["want"] = want
        r.update(rec)
        s = st.setdefault("site", {})
        if not domain:
            s["domain"] = ""
        if not cert:
            s["cert"] = ""
        s["mode"] = site
        return st

    def test_want_tls_and_reality_nodomain(self):
        self.site_on_reality()              # the real layout (Reality on 8443 next to the site): 443 is nginx's
        for w in ("tls", "reality_nodomain"):
            with self.subTest(want=w):
                self.check(self.st(w, domain=False), sec="reality", reason="no_domain", forced=True)
                self.check(self.st(w, cert=False), sec="reality", reason="no_cert", forced=True)
                self.check(self.st(w), sec="reality", reason="no_site", forced=True)
                # the site on, VLESS on Reality next to it, devices: no change, «ready» — a recommendation
                self.check(self.st(w, site="site", port=8443), sec="reality", reason="ready", forced=False)
                # an explicit want "tls" (the transition): TLS
                self.check(self.st(w, site="site", port=8443), want="tls", sec="tls", port=443)
                with mock.patch.object(front, "devices_of", return_value=[]):     # nobody to give links again: TLS
                    st = self.st(w, site="site", port=8443)
                    p = front.plan(st, self.w.runner)
                    self.assertEqual((p["security"], p["port"], p["auto"]), ("tls", 443, True))
                    # a refusal of TLS before: not tried again implicitly — earlier than the automatic switch
                    st = self.st(w, site="site", port=8443, tls_error={"at": "x", "why": "xray"})
                    p = front.plan(st, self.w.runner)
                    self.assertEqual((p["security"], p["fields"], p["reason"]), ("reality", {}, "tls_failed"))
                st = self.st(w, site="site", port=8443, tls_error={"at": "x", "why": "xray"})
                self.check(st, sec="reality", reason="tls_failed", forced=True)
                self.assertEqual(front.plan(st, self.w.runner)["reason"], "tls_failed")   # earlier than «ready»
                self.check(st, want="tls", sec="tls", port=443)                   # «Повторить TLS»: explicit
                # TLS now: stays
                self.check(self.st(w, site="site", port=443, sec="tls"), sec="tls", reason="")

    def test_443_held_by_others(self):
        self.site_on_reality()
        with mock.patch.object(front, "_panel_443", return_value=True):
            self.check(self.st("tls", site="site", port=8443), sec="reality", reason="busy_443", forced=True,
                       sub="panel_443")
            self.check(self.st("tls", site="site", port=8443), want="tls", sec="reality")     # explicit: still no
            self.check(self.st(None, site="site", port=8443), sec="reality", reason="busy_443")
        self.neighbour()
        self.check(self.st("tls", site="site", port=8443), sec="reality", reason="busy_443", forced=True, sub="")
        self.check(self.st("reality_nodomain", site="site", port=8443), sec="reality", reason="busy_443")

    def test_reality_with_a_valid_consent(self):
        self.check(self.st("reality", reality_consent=GOOD), sec="reality", reason="chosen", forced=False)
        # the site on: Reality is never on 443 (nginx holds it) — the port moves, the choice stays
        self.check(self.st("reality", site="site", port=443, reality_consent=GOOD), sec="reality", port="free",
                   reason="chosen")
        self.check(self.st("reality", site="site", port=8443, reality_consent=GOOD), sec="reality", reason="chosen")
        # TLS now and Reality chosen (the transition T→R): Reality
        self.check(self.st("reality", site="site", sec="tls", reality_consent=GOOD), sec="reality", port="free",
                   reason="chosen")
        # without a domain no consent is needed
        self.check(self.st("reality", domain=False), sec="reality", reason="no_domain", forced=False)
        for variant in ("r1a", "r1b"):
            self.assertTrue(front.consent_ok({"reality_consent": dict(GOOD, risks=variant)}))

    def test_a_broken_consent_changes_nothing(self):
        bad = [{}, {"at": None, "via": "panel", "risks": "r1a"}, dict(GOOD, via="telegram"), dict(GOOD, via="bot"),
               dict(GOOD, risks="r1"), dict(GOOD, risks="r9a"), dict(GOOD, risks="r1z"), dict(GOOD, at=""),
               "yes", None]
        for c in bad:
            with self.subTest(consent=c):
                self.assertFalse(front.consent_ok({"reality_consent": c}))
                self.check(self.st("reality", reality_consent=c), sec="reality", reason="consent_bad", forced=False)
                self.check(self.st("reality", site="site", port=443, sec="tls", reality_consent=c), sec="tls",
                           reason="consent_bad")
                st = self.st("reality", site="site", port=8443, reality_consent=c)
                self.assertEqual(front.plan(st, self.w.runner)["fields"], {})
        with mock.patch.object(front, "RISKS_ASK_AGAIN", frozenset(["r1"])):     # the text changed in meaning
            self.assertFalse(front.consent_ok({"reality_consent": GOOD}))

    def test_a_record_of_337_follows_the_site_as_before(self):
        set_want(None)
        self.check(self.st(None), sec="reality", reason="legacy", forced=False)
        self.check(self.st(None, domain=False), sec="reality", reason="no_domain")
        self.check(self.st(None, cert=False), sec="reality", reason="no_cert")
        # the site ready: TLS even with devices (3.3.7), and a refusal before does not hold it
        self.site_on_reality()
        set_want(None)
        self.check(self.st(None, site="site", port=8443), sec="tls", port=443)
        self.check(self.st(None, site="site", port=8443, tls_error={"at": "x", "why": "y"}), sec="tls", port=443)
        self.check(self.st("something", site="site", port=8443), sec="tls", port=443)  # unknown want: as 3.3.7

    def test_vless_off(self):
        self.ok("gw", "inbound", "disable", "vless")
        r = front.reason(state.load(), self.w.runner)
        self.assertEqual((r["reason"], r["forced"]), ("vless_off", True))


class FlowTest(WantCase):
    """The same rules through the real entries: ``gw vless sync``, ``site mode``, the reinstall with the kept record,
    the port, ``gw vless show``."""

    def test_a_broken_consent_keeps_the_mode_and_says_so(self):
        for c in ({}, {"at": None}, dict(GOOD, via="bot"), dict(GOOD, via="ssh-key"), dict(GOOD, risks="r0a")):
            with self.subTest(consent=c), mock.patch.object(front, "RISKS_ASK_AGAIN", frozenset(["r0"])), \
                    mock.patch.object(front, "RISKS_KNOWN", ("r0", "r1")):
                self.put(want="reality", reality_consent=c)
                before = (self.rec()["security"], self.rec()["port"], self.rec()["sni"], self.link_fields())
                rc, doc, err = self.run_json("gw", "vless", "sync")
                self.assertEqual(rc, 0, err)
                self.assertEqual((doc["changed"], doc["reason"]), (False, "consent_bad"))
                self.assertEqual((self.rec()["security"], self.rec()["port"], self.rec()["sni"], self.link_fields()),
                                 before)
                self.assertIn("согласие с рисками не найдено", self.journal())
                show = self.run_json("gw", "vless", "show")[1]
                self.assertEqual((show["reason"], show["forced"], show["want"]), ("consent_bad", False, "reality"))
                self.assertEqual(show["recommend"]["mode"], "tls")
                self.assertFalse(show["consent"] and show["consent"]["ok"])
                # and the site switched on does not put it in front either: Reality, off 443 for the site
                self.assertFalse(front.will_front(state.load(), self.w.runner))

    def test_reality_by_consent_and_the_site_switched_on(self):
        """З5: the site takes 443 itself, VLESS stays Reality and moves to 8443 — a change of the link that
        ``predict`` sees beforehand (the whole link is compared)."""
        self.put(want="reality", reality_consent=GOOD)
        before = self.link_fields()
        st = state.load()
        self.assertFalse(front.will_front(st, self.w.runner))
        pr = front.predict(st, self.w.runner, "site")
        self.assertTrue(pr["changes"])
        self.assertEqual((pr["from"]["port"], pr["to"]["port"], pr["to"]["security"]), (443, 8443, "reality"))
        self.assertEqual(pr["from"]["sni"], pr["to"]["sni"])
        self.assertEqual([d["id"] for d in pr["devices"]], [self.dev["id"]])
        got = site_ops.mode(self.w.runner, "site", yes=True)
        self.assertEqual(got["site"]["mode"], "site")
        self.assertIn("listen 443 ssl", self.vhost())                       # nginx in front, not behind xray
        self.assertNotIn("listen unix:", self.vhost())
        self.assertFalse(front.xray_fronts())
        rec = self.rec()
        self.assertEqual((rec["security"], rec["port"], rec["want"]), ("reality", 8443, "reality"))
        after = self.link_fields()
        self.assertNotEqual(after, before)
        self.assertEqual(after[:2] + after[3:], before[:2] + before[3:])    # only the port: 443 -> 8443
        self.assertEqual((before[2], after[2]), (443, 8443))
        v = got["vless"]
        self.assertTrue(v["changed"])
        self.assertEqual([r["id"] for r in v["reissue"]], [self.dev["id"]])
        show = self.run_json("gw", "vless", "show")[1]
        self.assertEqual((show["reason"], show["forced"]), ("chosen", False))
        self.assertTrue(show["consent"]["ok"])

    def test_want_tls_site_off_and_on_again(self):
        self.put(want="tls")
        site_ops.mode(self.w.runner, "site", yes=True)                                # explicit: TLS (the device given again)
        self.assertEqual(self.rec()["security"], "tls")
        got = site_ops.mode(self.w.runner, "domain", yes=True)
        self.assertEqual(got["vless"]["security"], "reality")
        rec = self.rec()
        self.assertEqual((rec["security"], rec["want"]), ("reality", "tls"))   # want does not change
        show = self.run_json("gw", "vless", "show")[1]
        self.assertEqual((show["reason"], show["forced"], show["fix"]["do"]), ("no_site", True, "site_on"))
        self.assertIsNone(show["recommend"])
        site_ops.mode(self.w.runner, "site", yes=True)
        self.assertEqual((self.rec()["security"], self.rec()["port"]), ("tls", 443))

    def test_a_refused_tls_is_remembered_and_not_retried_implicitly(self):
        self.put(want="tls")
        self.site_on_reality()
        with mock.patch.object(front, "xray_fronts", return_value=False):   # xray did not take the config
            got = front.sync(self.w.runner, want="tls")
        self.assertFalse(got["ok"])
        self.assertEqual(got["reason"], "tls_failed")
        rec = self.rec()
        self.assertEqual((rec["security"], rec["want"]), ("reality", "tls"))
        self.assertTrue(rec["tls_error"]["at"] and rec["tls_error"]["why"])
        show = self.run_json("gw", "vless", "show")[1]
        self.assertEqual((show["reason"], show["forced"], show["fix"]["do"]), ("tls_failed", True, "mode_tls"))
        self.assertEqual(show["tls_error"]["why"], rec["tls_error"]["why"])
        # an implicit sync does not try again: with devices the reason is the refusal, not «ready»
        rc, doc, err = self.run_json("gw", "vless", "sync")
        self.assertEqual(rc, 0, err)
        self.assertEqual((doc["changed"], doc["reason"]), (False, "tls_failed"))
        # — and not even with nobody to give links again
        with mock.patch.object(front, "devices_of", return_value=[]):
            rc, doc, err = self.run_json("gw", "vless", "sync")
        self.assertEqual(rc, 0, err)
        self.assertEqual((doc["changed"], doc["security"], doc["reason"]), (False, "reality", "tls_failed"))
        # explicitly («Повторить TLS»): TLS, and the refusal is gone
        got = front.sync(self.w.runner, want="tls")
        self.assertTrue(got["ok"])
        rec = self.rec()
        self.assertEqual(rec["security"], "tls")
        self.assertNotIn("tls_error", rec)

    def test_implicit_sync_with_devices_changes_nothing(self):
        """З-1: Reality, TLS possible and devices — ``gw vless sync``, the reinstall with the kept record: no change
        (security, port, sni), «ready», a recommendation with the price."""
        self.put(want="tls")
        self.site_on_reality()
        before = (self.rec()["security"], self.rec()["port"], self.rec()["sni"], self.link_fields())
        rc, doc, err = self.run_json("gw", "vless", "sync")
        self.assertEqual(rc, 0, err)
        self.assertEqual((doc["changed"], doc["reason"]), (False, "ready"))
        self.ok("gw", "inbound", "uninstall", "vless")
        rc, doc, err = self.run_json("gw", "inbound", "install", "vless")   # keep_inbound: the record as it was
        self.assertEqual(rc, 0, err)
        self.assertEqual((self.rec()["security"], self.rec()["port"], self.rec()["sni"], self.link_fields()),
                         before)
        self.assertEqual(self.rec()["want"], "tls")
        show = self.run_json("gw", "vless", "show")[1]
        self.assertEqual((show["reason"], show["forced"]), ("ready", False))
        self.assertEqual(show["recommend"]["devices"], 1)
        self.assertIn("1 устройство", show["recommend"]["text"])
        self.assertEqual(show["fix"]["do"], "mode_tls")

    def test_implicit_sync_without_devices_switches_by_itself(self):
        self.put(want="tls")
        self.site_on_reality()
        with mock.patch.object(front, "devices_of", return_value=[]):
            rc, doc, err = self.run_json("gw", "vless", "sync")
        self.assertEqual(rc, 0, err)
        self.assertEqual((doc["changed"], doc["security"]), (True, "tls"))
        self.assertEqual((self.rec()["security"], self.rec()["port"]), ("tls", 443))

    def test_reality_nodomain_and_the_domain_came(self):
        """«reality_nodomain» — Reality chosen when there was no domain: the domain with a certificate came — as
        «tls»: without devices TLS by itself, with devices only the recommendation (§3.3)."""
        self.put(want="reality_nodomain")
        self.site_on_reality()
        rc, doc, err = self.run_json("gw", "vless", "sync")
        self.assertEqual(rc, 0, err)
        self.assertEqual((doc["changed"], doc["reason"]), (False, "ready"))
        with mock.patch.object(front, "devices_of", return_value=[]):
            rc, doc, err = self.run_json("gw", "vless", "sync")
        self.assertEqual((doc["changed"], doc["security"]), (True, "tls"))
        # Ш2: every R→T goes through vless_to_tls, whose success writes ``want`` "tls" (§3.5 п.5)
        self.assertEqual(self.rec()["want"], "tls")

    def test_a_record_of_337_on_tls_gets_want_tls(self):
        set_want(None)
        site_ops.mode(self.w.runner, "site", yes=True)                                # as 3.3.7: TLS with the site
        self.assertEqual((self.rec()["security"], self.rec()["want"]), ("tls", "tls"))
        set_want(None)
        self.ok("gw", "vless", "sync")                                      # the first sync after the update
        self.assertEqual(self.rec()["want"], "tls")

    def test_a_record_of_337_on_reality_stays_legacy(self):
        set_want(None)
        self.ok("gw", "vless", "sync")
        self.assertNotIn("want", self.rec())
        show = self.run_json("gw", "vless", "show")[1]
        self.assertEqual((show["want"], show["legacy"], show["reason"]), (None, True, "legacy"))
        self.assertEqual((show["recommend"]["mode"], show["recommend"]["devices"]), ("tls", 1))

    def test_port_443_next_to_the_site_is_refused_with_the_hint(self):
        self.put(want="tls")
        self.site_on_reality()
        from kaskad import errors
        rc, doc, _ = self.run_json("gw", "inbound", "port", "vless", "443")
        self.assertEqual(rc, errors.CONFLICT)
        self.assertIn("kaskadctl gw vless mode tls", doc["error"])
        self.assertEqual((self.rec()["port"], self.rec()["security"]), (8443, "reality"))
        for lang in ("ru", "en"):
            for key in ("gw_ops.err.vless_443_mode", "gw_ops.err.vless_443_mode_not"):
                text = tr(key, lang, why="x")
                self.assertIn("gw vless mode tls", text)
                self.assertNotEqual(text, key)
        self.assertIn("443", tr("gw_ops.err.vless_443_mode", "en"))

    def test_the_view_of_the_mode(self):
        self.put(want="tls")
        show = self.run_json("gw", "vless", "show")[1]
        for k in ("want", "legacy", "reason", "forced", "fix", "recommend", "consent", "reality_risks", "pending"):
            self.assertIn(k, show)
        # T-3378 Н-17: the site never on under this domain, a device — «TLS уже можно» (the site goes on with TLS)
        self.assertEqual((show["want"], show["reason"], show["forced"]), ("tls", "ready", False))
        risks = show["reality_risks"]
        self.assertEqual((risks["ver"], risks["site"], risks["need_pick"]), ("r1a", "www.microsoft.com", False))
        self.assertIn("www.microsoft.com", risks["text"])
        self.assertIn("не изменятся", risks["text"])                 # Reality already, the same cover and port
        site_ops.mode(self.w.runner, "site", yes=True)                                # TLS now: the choice of Reality costs
        risks = self.run_json("gw", "vless", "show")[1]["reality_risks"]
        self.assertIn("заново (1 устройство).", risks["text"])
        self.assertEqual(risks["phrase"], "Понимаю риски, выбрать Reality")
        en = front.risks_data(state.load(), "en", site="own")
        self.assertEqual(en["ver"], "r1b")
        self.assertIn("8443", en["text"])
        self.assertEqual(en["phrase"], "I understand the risks, choose Reality")
        # the panel's read (no runner): the same keys, no probing
        from kaskad.gw import ops as gops
        v = gops.vless_view(state.load())
        self.assertEqual((v["security"], v["reason"], v["want"], v["forced"]), ("tls", "", "tls", False))
