"""Kaskad 3.3.0, live check №4 (k3/stand/logs/T-M6-qs330d.md):

    №1  a WORKING domain (certificate, site, VLESS on TLS) is never taken away for a new name that is not ready: the
        new name is checked first (DNS, port 80 — nothing changes on a refusal), then gets its certificate while the
        old one serves, and only then the switch; a failure of the switch puts the old domain back as a whole. The
        wizard checks without changes and switches in the step «cert» (the move of the wizard to the new name
        prepared before); the open mode of the panel answers the old name a while longer (the page learns the
        result); the CLI, the panel and the bot go the same way (``site.ops.set_domain``);
    №2  the wizard: «Проверяю…» on the button while the check runs, a second press goes nowhere;
    №7  the cover site of Reality stays the same when VLESS comes back from TLS (a new one only by «gw vless mask»).
"""

import hashlib
import json
import os
import shutil
import time
import unittest
from unittest import mock

from kaskad import paths, state
from kaskad.panel import conf, server
from kaskad.setup import steps
from kaskad.setup import model as smodel_setup
from kaskad.site import dns, model, nginx
from kaskad.site import ops as sops
from tests import jsparts
from tests.site.sitecase import DOMAIN, NODE
from tests.test_gw_vless_front import FrontCase
from tests.test_qs330c_live3 import flat, node, read, wizard_scope

NEW = "other.example.org"
TREES = ("/etc", "/opt", "/var/www", "/run")


def tree():
    """Every file of the fake node (path -> sha256 of the content or of the link target)."""
    out = {}
    for top in TREES:
        base = paths.P(top)
        for d, _, files in os.walk(base):
            for fn in files:
                p = os.path.join(d, fn)
                if os.path.islink(p):
                    out[p] = "link:" + os.readlink(p)
                    continue
                try:
                    with open(p, "rb") as f:
                        out[p] = hashlib.sha256(f.read()).hexdigest()
                except OSError:
                    out[p] = "?"
    return out


class SwitchCase(FrontCase):
    """A working domain: the certificate of vpn.example.com, the site on with its template, VLESS on TLS with it."""

    def setUp(self):
        super().setUp()
        got = sops.mode(self.w.runner, "site", yes=True)
        self.assertEqual(got["vless"]["security"], "tls")
        self.tpl = model.peek(state.load())["template_id"]
        smodel_setup.update(lambda d: d.update(password_set=True))
        self.certbot.calls = []                         # the first issue of vpn.example.com is not the change
        self.answers_for[NEW] = {dns.A: ("ok", [NODE]), dns.AAAA: ("ok", [])}

    def no_record(self):
        self.answers_for[NEW] = {dns.A: ("nxdomain", []), dns.AAAA: ("nxdomain", [])}

    def certbot_runs(self):
        return [c for c in self.certbot.calls if c[1] == "certonly"]

    def assert_on(self, d):
        """The node serves ``d`` the way it served vpn.example.com: certificate, site, VLESS TLS, panel on it."""
        s = model.peek(state.load())
        self.assertEqual((s["domain"], s["mode"], s["cert"]), (d, "site", "letsencrypt"))
        self.assertEqual(model.copy_info()["names"], [d])
        self.assertEqual(s["template_id"], self.tpl)                           # the same template
        text = self.vhost()
        self.assertIn("server_name %s;" % d, text)
        self.assertIn("listen unix:", text)                                     # nginx behind xray
        tls = self.vless_ib()["streamSettings"]["tlsSettings"]
        self.assertEqual(tls["serverName"], d)
        self.assertEqual(self.rec()["security"], "tls")
        self.assertEqual(conf.load()["proxy"].get("domain"), d)
        self.assertIn("autorenew = True", self.read("/etc/letsencrypt/renewal/%s.conf" % d))
        u, q, _ = self.link(self.dev["id"])
        self.assertEqual((u.hostname, q["security"], q["sni"]), (d, "tls", d))
        self.assertTrue(self.w.sysd.units["nginx"]["enabled"] and self.w.sysd.units["nginx"]["active"])


class RefusedWithoutChangesTest(SwitchCase):
    def test_no_a_record_changes_nothing(self):
        self.no_record()
        before = tree()
        calls = len(self.w.runner.calls)
        with self.assertRaises(sops.Refusal) as e:
            sops.set_domain(self.w.runner, NEW)
        self.assertEqual(e.exception.doc["reason"], "dns")
        self.assertEqual(e.exception.doc["kept"], DOMAIN)
        self.assertIn("работает как раньше", e.exception.doc["text"])
        self.assertEqual(tree(), before)                                        # state, nginx, xray, certificates
        self.assertEqual(self.certbot_runs(), [])
        changing = [a for a, _ in self.w.runner.calls[calls:] if a[:1] in (["systemctl"], ["nginx"], ["ufw"])
                    and a[1:2] not in (["is-active"], ["is-enabled"], ["status"], ["-v"], ["-t"], ["-T"])]
        self.assertEqual(changing, [])
        self.assert_on(DOMAIN)

    def test_dns_elsewhere_and_cloudflare_refused_too(self):
        for ans in (("ok", ["198.51.100.7"]), ("ok", ["104.16.1.1"])):
            self.answers_for[NEW] = {dns.A: ans, dns.AAAA: ("ok", [])}
            before = tree()
            with self.assertRaises(sops.Refusal):
                sops.set_domain(self.w.runner, NEW)
            self.assertEqual(tree(), before)
        self.assert_on(DOMAIN)

    def test_certificate_refused_changes_nothing(self):
        self.certbot.mode = "limit"
        before = tree()
        with self.assertRaises(sops.Refusal) as e:
            sops.set_domain(self.w.runner, NEW)
        self.assertEqual(e.exception.doc["reason"], "limit")
        self.assertIn("работает как раньше", e.exception.doc["text"])
        self.assertEqual(len(self.certbot_runs()), 1)
        self.assertEqual(tree(), before)                                        # the temporary ACME vhost went again
        self.assertFalse(os.path.lexists(nginx.acme_enabled_path()))
        self.assert_on(DOMAIN)

    def test_wizard_check_changes_nothing(self):
        self.no_record()
        before = tree()
        got = steps.run("domain", {"name": NEW}, self.w.runner)
        self.assertTrue(got["ok"])
        self.assertEqual((got["data"]["dns"], got["data"]["kept"], got["data"]["switch"]), ("none", DOMAIN, True))
        self.assertIn("работает как раньше", got["text"])
        poll = steps.run("dns", {"name": NEW}, self.w.runner)                                  # the poll of the page: a look only
        self.assertEqual((poll["data"]["dns"], poll["data"]["kept"]), ("none", DOMAIN))
        changed = dict((p, h) for p, h in tree().items() if before.get(p) != h)
        self.assertEqual([p for p in changed if not p.endswith("/setup.json")], [])   # only the wizard's answers
        a = smodel_setup.load()["answers"]
        # the working domain stays the domain of the answers; the refused name — its last try (T-M6-qs330e №3)
        self.assertEqual((a["domain"], a.get("domain_next")), (DOMAIN, None))
        self.assertEqual((a["domain_try"]["name"], a["domain_try"]["state"]), (NEW, "none"))
        self.assertEqual(self.certbot_runs(), [])
        self.assert_on(DOMAIN)


class SwitchTest(SwitchCase):
    def test_switch_moves_everything_and_drops_the_old_domain_last(self):
        self.certbot.pem = "other"
        rows = []
        res = sops.set_domain(self.w.runner, NEW, say=lambda k, st, detail="", title=None: rows.append((k, st)))
        self.assertEqual((res["changed_from"], res["site"]["domain"], res["issued"]), (DOMAIN, NEW, True))
        self.assertEqual(rows, [("dns", "wait"), ("dns", "ok"), ("cert", "wait"), ("cert", "ok"), ("switch", "wait"),
                                ("switch", "ok")])
        run = self.certbot_runs()
        self.assertEqual(len(run), 1)
        self.assertIn("--webroot", run[0])                                      # through nginx of the old site
        self.assert_on(NEW)
        # the old domain is gone: no renewal, no hook; its certificate stays on disk
        old = self.read("/etc/letsencrypt/renewal/%s.conf" % DOMAIN)
        self.assertIn("autorenew = False", old)
        self.assertNotIn("site cert-installed", old)
        self.assertNotIn(DOMAIN, self.vhost())
        self.assertFalse(os.path.lexists(nginx.acme_enabled_path()))
        # VLESS: TLS on the new name; the links given out need the new name — said
        self.assertEqual(res["vless"]["security"], "tls")
        self.assertTrue(any(NEW in w for w in res["warnings"]) or NEW in (res["vless"].get("text") or ""))
        # the open mode of the panel still answers the old name for a while (the wizard on it learns the result)
        prev = model.peek(state.load())["prev"]
        self.assertEqual(prev["domain"], DOMAIN)
        self.assertGreater(prev["until"], time.time() + sops.PREV_GRACE - 60)
        got = conf.prev_site_cert()
        self.assertEqual(got[0], DOMAIN)
        self.assertIn("BEGIN CERTIFICATE", got[1])
        self.assertIn("SITE: домен сменён: %s -> %s" % (DOMAIN, NEW), self.log_text())

    def test_grace_runs_out(self):
        self.certbot.pem = "other"
        sops.set_domain(self.w.runner, NEW)
        self.set_site(prev={"domain": DOMAIN, "until": int(time.time()) - 1})
        self.assertIsNone(conf.prev_site_cert())

    def test_switch_back_and_forth_takes_the_live_certificate(self):
        self.certbot.pem = "other"
        sops.set_domain(self.w.runner, NEW)
        self.answers_for[DOMAIN] = {dns.A: ("ok", [NODE]), dns.AAAA: ("ok", [])}
        n = len(self.certbot_runs())
        res = sops.set_domain(self.w.runner, DOMAIN)                            # back: its certificate is still live
        self.assertEqual((res["changed_from"], res["issued"]), (NEW, False))
        self.assertEqual(len(self.certbot_runs()), n)
        self.assert_on(DOMAIN)
        self.assertEqual(model.peek(state.load())["prev"]["domain"], NEW)

    def test_failed_switch_puts_the_old_domain_back(self):
        self.certbot.pem = "other"
        real = sops.mode

        def mode(runner, value, *a, **kw):
            if value == "site" and model.domain_of(state.load()) == NEW:
                raise sops.Refusal("nginx_test", "nginx не принял сайт (тест)")
            return real(runner, value, *a, **kw)
        with mock.patch.object(sops, "mode", mode):
            with self.assertRaises(sops.Refusal) as e:
                sops.set_domain(self.w.runner, NEW)
        self.assertEqual((e.exception.doc["reason"], e.exception.doc["kept"]), ("switch", DOMAIN))
        self.assertIn("Прежний домен %s возвращён" % DOMAIN, e.exception.doc["text"])
        self.assert_on(DOMAIN)                                                  # site, VLESS TLS, panel — as before
        self.assertIsNone(model.peek(state.load())["prev"])
        new = self.read("/etc/letsencrypt/renewal/%s.conf" % NEW)
        self.assertIn("autorenew = False", new)                                 # the new certificate is not renewed
        self.assertNotIn("site cert-installed", new)
        self.assertIn("откат смены домена: %s возвращён" % DOMAIN, self.log_text())


class WizardSwitchTest(SwitchCase):
    def test_check_then_cert_switches_and_the_move_goes_to_the_new_name(self):
        from kaskad.setup import web
        self.certbot.pem = "other"
        got = steps.run("domain", {"name": NEW}, self.w.runner)
        self.assertEqual((got["data"]["dns"], got["data"]["kept"]), ("ok", DOMAIN))
        self.assertEqual(model.domain_of(state.load()), DOMAIN)                 # still the old one
        self.assertEqual(smodel_setup.load()["answers"]["domain_next"], NEW)
        self.assertEqual(self.certbot_runs(), [])
        # the move of the wizard: to the checked name, to nothing else
        self.assertEqual(self.move_host(web, NEW), (200, NEW))
        self.assertEqual(self.move_host(web, "evil.example.net")[0], 409)
        rows = []
        with mock.patch.object(steps.Ctx, "say", lambda c, k, st, detail="", title=None: rows.append((k, st))):
            res = steps.run("cert", {"name": NEW}, self.w.runner)
        self.assertTrue(res["ok"], res)
        self.assertEqual((res["data"]["changed_from"], res["data"]["domain"], res["data"]["site_on"]),
                         (DOMAIN, NEW, True))
        self.assertIn(("switch", "ok"), rows)
        self.assert_on(NEW)
        self.assertIsNone(smodel_setup.load()["answers"].get("domain_next"))
        self.assertEqual(self.move_host(web, NEW), (200, NEW))                  # the domain itself now

    def move_host(self, web, host):
        from kaskad.panel.routes import HttpError

        class Req(object):
            def __init__(self, body):
                self.body = json.dumps(body).encode()

            def json(self):
                return json.loads(self.body.decode())
        with mock.patch.object(web, "need", return_value={"via": "link"}), \
                mock.patch.object(web.link, "move_url", side_effect=lambda doc, h, tok, cfg=None: "https://%s/" % h):
            try:
                r = web.api_move(Req({"host": host}))
            except HttpError as e:
                return e.status, None
        return r.status, json.loads(r.body.decode())["host"]

    def test_cert_refused_on_the_wizard_keeps_the_old_domain(self):
        self.certbot.mode = "limit"
        steps.run("domain", {"name": NEW}, self.w.runner)
        before = tree()
        res = steps.run("cert", {"name": NEW}, self.w.runner)
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"]["code"], "limit")
        self.assertIn("работает как раньше", res["error"]["text"])
        changed = [p for p, h in tree().items() if before.get(p) != h and not p.endswith("/setup.json")]
        self.assertEqual(changed, [])
        self.assert_on(DOMAIN)


class PanelSniTest(SwitchCase):
    def test_callback_gives_the_old_name_its_certificate(self):
        ip_ctx, old_ctx = object(), object()
        cb = server.sni_callback(None, {DOMAIN: old_ctx})

        class Sock(object):
            context = "main"
        for name, want in ((None, "main"), ("203.0.113.7", "main"), (DOMAIN, old_ctx), (DOMAIN.upper() + ".", old_ctx),
                           (NEW, "main")):
            s = Sock()
            cb(s, name, None)
            self.assertIs(s.context, want, name)
        cb = server.sni_callback(ip_ctx, {DOMAIN: old_ctx})
        s = Sock()
        cb(s, None, None)
        self.assertIs(s.context, ip_ctx)


class MaskKeptTest(FrontCase):
    def test_reality_keeps_its_cover_after_tls(self):
        rec = self.rec()
        self.assertEqual(rec["security"], "reality")
        # a cover chosen before (not the first of the list the picker would take)
        st = state.load()
        st["gateway"]["inbounds"]["vless"].update(sni="www.apple.com", dest="www.apple.com:443", mask="list")
        state.save(st)
        sops.mode(self.w.runner, "site", yes=True)
        self.assertEqual(self.rec()["security"], "tls")
        sops.mode(self.w.runner, "domain", yes=True)
        rec = self.rec()
        self.assertEqual((rec["security"], rec["sni"], rec["mask"]), ("reality", "www.apple.com", "list"))
        sops.mode(self.w.runner, "site", yes=True)
        sops.mode(self.w.runner, "domain", yes=True)
        self.assertEqual(self.rec()["sni"], "www.apple.com")                    # again: the same


# ── the page: №1 (no change before the certificate, the move to the new name), №2 (busy) ──────────────────────


def move_scope():
    js = read("setup/setup.js")
    parts = [jsparts.const(js, n) for n in ("onDomain", "domName", "domFull")]
    parts += [jsparts.function(js, n) for n in ("domBody", "prepMove", "startCert", "switchDone")]
    return """
const out = { api: [], steps: [] };
const S = { res: {}, busy: {} };
const U = {};
const ui = (k) => (U[k] = U[k] || {});
const now = () => 1000;
const render = () => {};
const location = { hostname: "old.example.org" };
const api = async (path, body) => { out.api.push([path, body]); return { status: 200, body: { url: "https://" + body.host + ":1/x?t=T", host: body.host } }; };
const step = async (name, body) => { out.steps.push([name, body]); return { job: {} }; };
%s
""" % "\n".join(parts)


@unittest.skipUnless(shutil.which("node"), "нет node")
class WizardPageTest(unittest.TestCase):
    def run_js(self, scope, body):
        return node(scope + "\n(async () => {\n" + body + "\n})().catch((e) => { console.error(e.stack); process.exit(1); });")

    def test_busy_check_shows_itself_and_a_second_press_goes_nowhere(self):
        """3.3.3 (DomainSteps): no «Проверить» — the name checks itself; while its check goes, a second one for the
        same name goes nowhere, and step 4 says «Пока не видно» with the clock, not the old «Проверяю…»."""
        got = self.run_js(wizard_scope(), """
const u = ui("domain");
setDomFields(u, "new.example.org"); u.checking = "new.example.org";
await checkDomain();
const busy = dnsBlock(u);
u.checking = "";
await checkDomain();
console.log(JSON.stringify({ steps: out.steps, busy }));
""")
        self.assertEqual(got["steps"][0], ["dns", {"name": "new.example.org", "zone": "example.org"}])   # only the second one went
        self.assertEqual(len([s for s in got["steps"] if s[0] in ("dns", "domain")]), 1)
        self.assertIn("Пока не видно.", flat(got["busy"]))
        js = read("setup/setup.js")
        self.assertNotIn('"dm-check"', js)
        self.assertIn('if (!name || u.checking === name || running(["cert"])) return;', js)

    def test_refusal_says_the_working_domain_stays(self):
        got = self.run_js(wizard_scope(), """
const u = ui("domain");
DNS = { dns: "none", advice: "У домена new.example.org нет A-записи.", kept: "old.example.org", switch: true };
setDomFields(u, "new.example.org"); await checkDomain();
console.log(JSON.stringify({ steps: out.steps, card: dnsBlock(u), certs: out.certs }));
""")
        card = flat(got["card"])
        self.assertIn("Пока не видно.", card)                 # no A record yet — the record was just made (UX-333 §6)
        self.assertIn("old.example.org работает как раньше — на сервере ничего не менялось.", card)
        self.assertEqual(got["certs"], 0)
        self.assertEqual(got["steps"][0], ["dns", {"name": "new.example.org", "zone": "example.org"}])

    def test_cert_goes_with_the_name_and_the_move_is_prepared_to_it(self):
        got = self.run_js(move_scope(), """
const u = ui("domain");
u.checked = "new.example.org"; u.name = "new.example.org"; u.dns = { dns: "ok", kept: "old.example.org", switch: true };
await startCert();
switchDone({ ok: true, data: { changed_from: "old.example.org", site_on: true, template: { name: "Кофейня" } } });
console.log(JSON.stringify({ api: out.api, steps: out.steps, move: S.move.url, site: S.res.site.ok, kept: u.dns.kept }));
""")
        self.assertEqual(got["api"], [["move", {"host": "new.example.org"}]])
        self.assertEqual(got["steps"], [["cert", {"name": "new.example.org"}]])
        self.assertTrue(got["move"].startswith("https://new.example.org:"))
        self.assertEqual((got["site"], got["kept"]), (True, None))


class NewExitTest(unittest.TestCase):
    """№3, №4: a new exit probed before its leg or xray is up is «проверяется» — no «не работает» for a key that works
    a second later, no «снова работает» when it comes up; dead only by the rule of 3.3.5, told once."""

    def test_new_exit_comes_up_without_words(self):
        from kaskad.gw import exits
        down = {"e1": {"alive": False, "detail": "нет интерфейса kawg5", "dies_after": 3}}
        up = {"e1": {"alive": True, "detail": "рукопожатие 2 с назад", "dies_after": 3}}
        snap, ch, _ = exits.merge({}, down, ["e1"], now=100)              # ops._change probes before xray/legs
        self.assertEqual(ch, [])
        self.assertEqual(exits.state_of(snap["exits"]["e1"]), "unknown")
        self.assertNotIn("e1", exits.alive_ids(snap))
        snap, ch, _ = exits.merge(snap, up, ["e1"], now=105)
        self.assertEqual(ch, [("e1", None, True)])                        # «a new key that works»: nobody says it
        self.assertNotIn("new", snap["exits"]["e1"])
        snap, ch, _ = exits.merge(snap, down, ["e1"], now=110)            # from now on — an ordinary exit
        self.assertEqual(ch, [])                                          # (still alive: 1 failure of 3)

    def test_new_exit_that_never_comes_up_is_told_once(self):
        from kaskad.gw import exits
        down = {"e1": {"alive": False, "detail": "нет ответа"}}
        snap = {}
        for t in (100, 110, 120):                                         # 3.3.5: 4 checks (one per 10 s) at the least
            snap, ch, _ = exits.merge(snap, down, ["e1"], now=t)
            self.assertEqual(ch, [])
        snap, ch, _ = exits.merge(snap, down, ["e1"], now=130)
        self.assertEqual(ch, [("e1", None, False)])
        self.assertEqual(exits.state_of(snap["exits"]["e1"]), "dead")
        snap, ch, _ = exits.merge(snap, down, ["e1"], now=140)
        self.assertEqual(ch, [])
        up = {"e1": {"alive": True, "detail": ""}}
        snap, ch, _ = exits.merge(snap, up, ["e1"], now=150)
        self.assertEqual(ch, [])                                          # one good check: not back yet
        snap, ch, _ = exits.merge(snap, up, ["e1"], now=160)
        self.assertEqual(ch, [("e1", False, True)])                       # «снова работает» — after «не работает»

    def test_collect_says_nothing_for_a_new_exit(self):
        """A new key that works: no event (stage 1: the events of the exits come from health — a new exit «ok» says
        nothing, SE §4.2)."""
        from kaskad.gw import health, policy
        doc = policy.load({})
        doc["groups"][0]["tiers"][0]["exits"] = ["e-0000000001"]
        out, ev = health.evaluate({}, {"e-0000000001": {"kind": "awg", "probes": [{"ok": True, "at": 1.0}]}}, doc,
                                  {"mono": 1.0}, 1000.0)
        self.assertEqual([e for e in ev if e["kind"] != "transition"], [])


class UndoWordsTest(FrontCase):
    """№6: ``backup undo`` says «Возвращаю», and only for what it really brings back or takes away."""

    def test_undo_returns_not_moves(self):
        from kaskad import backup
        snap = backup.take_snapshot(self.w.runner)
        said = []
        backup.undo(self.w.runner, path=snap, say=said.append)
        self.assertTrue(said)
        self.assertEqual([x for x in said if x.startswith("Переношу")], [])
        self.assertIn("Возвращаю: %s…" % backup.TITLES["gateway"], said)
        self.assertNotIn("Возвращаю: %s…" % backup.TITLES["bot"], said)          # no bot in the copy, none here
        self.assertNotIn("Возвращаю: %s…" % backup.TITLES["forwards"], said)     # nothing to bring back
