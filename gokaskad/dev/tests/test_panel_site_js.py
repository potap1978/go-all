"""«Настройки → Домен и сайт» (docs/k3/SPEC-wave4.md §4.1, B-лицо): the REAL functions of app.js (cut out by name,
tests/jsparts.py) read the REAL answers of /api/site on the fake node (tests/site/sitecase.py, tests/panel/panelcase.py):

    S1  STATUS without a domain, with a domain, with a Cyrillic one (domain_unicode for people, punycode beside it),
        with the certificate of Let's Encrypt (the page sees it live: «Продлить» only near the end);
    S2  a refusal of the issue (HTTP 409) reaches the page whole: reason, text, retry_after — the dialog of the reason;
    S3  the dry run of SSTP names the devices that need new settings;
    S4  the maps of the page cover every state of DNS and every reason of a refusal of the core;
    S5  what people paste into the field of the domain becomes the bare name; the fingerprint gives way to «Let's
        Encrypt для <домен>, до …» only when the panel really serves it;
    (4.0-C, сайт-маскировка, SPEC-wave4 §7.1)
    S6  the card of the cover site appears with a live production certificate; PUT /api/site/mode with the body the
        page builds turns the site on; STATUS carries only the mask, GET panel-url the full address; the answer of the
        rotation is a mask — the page asks panel-url for the new one; the prefix of the page tells «opened here»;
    S7  every refusal of the site mode (no_domain, no_cert, port443, port80, neighbour, apt, nginx_test, template)
        reaches the window of the reason whole: a title of its own, the text of the server, what to do; apt of the
        site is not «certbot»; a template that did not deploy says why by template_reason and what to do (the key of
        GitHub, the network, another template) — everything stays as it was; a random one that fell back to the
        simple page says so (fallback);
    S8  every verdict of POST /api/site/check (ok and the 200 {ok: false} ones) becomes the five lines in words; the
        warnings of the check (nginx's default page on :80) reach the card;
    S9  the catalog: categories, search and pages through the query the page builds; no key / a bad key / no network
        are told apart by data.reason; a template that failed (200 {ok: false, reason}) is not a verdict.
    S10 «жив ли шаблон» (3.2.21): the rows come at once — known to the server's cache with their state, the rest
        «проверяю…»; one POST /api/site/templates/check of the visible page gives alive / gone (grey, no «Превью»,
        no «Выбрать») / no_preview (no «Превью», «Выбрать» stays) / unknown; a failed check is «не удалось проверить»;
        the check is the admin's change (X-Kaskad-Admin), at most 25 ids; a template refused as removed says so.
Skipped without node."""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
import urllib.error
from unittest import mock

from kaskad import ghkey
from kaskad.errors import KaskadError
from kaskad.gw import ops as gops
from kaskad.gw import registry
from kaskad.gw.inbounds.base import DummyInbound
from kaskad.panel import conf
from kaskad.site import acme, consumers, dns, nginx, ops
from kaskad.site import templates as T
from tests import jsparts
from tests.panel.panelcase import PanelCase
from tests.site import fixtures_templates as fx
from tests.site.sitecase import DAY, DOMAIN, SiteFakes

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib", "kaskad", "panel", "static")


def read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


def scope():
    js = read("app.js")
    parts = [jsparts.const(js, n) for n in ("DASH", "known", "isObj", "arr", "num", "toDate", "API_TIMEOUT_MS",
                                            "APPLY_TIMEOUT_MS", "LONG_TIMEOUT_MS", "CERT_TIMEOUT_MS", "DNS_TIMEOUT_MS",
                                            "SITE_TIMEOUT_MS", "SITE_DEPLOY_TIMEOUT_MS", "SITE_CHECK_TIMEOUT_MS",
                                            "TIMEOUT_BY_ROUTE", "panelBase", "BASE", "apiUrl", "qs",
                                            "SITE_KIND", "SITE_DNS", "SITE_WHY", "SITE_FIX", "SITE_MASK_WHY",
                                            "SITE_CHECK", "SITE_CHECK_ITEMS", "SITE_RANDOM", "SITE_TPL_WHY",
                                            "SITE_STUB", "siteCheckWarnings", "RENEW_DAYS", "siteLe",
                                            "siteLive", "siteDate", "siteClean", "siteMaskOn", "siteMaskAvail",
                                            "siteViaProxy", "siteFullUrl", "siteModeBody", "siteTplQuery",
                                            "siteCheckOf", "siteTplFailOf", "SITE_TPL_HEALTH", "SITE_TPL_STATE",
                                            "siteTplHealthOf", "KS", "SM")]   # KS, SM — «Режим стрима» (stream.js в node нет)
    parts += [jsparts.klass(js, "ApiError"), jsparts.dates(read("boot.js"))]      # DATES: boot.js KaskadLang (3.3.2)
    parts += [jsparts.function(js, n) for n in ("timeoutOf", "request", "fmtDay", "normSite", "siteNames", "siteFp",
                                                "siteRefusalDoc", "siteTplDoc", "siteTplFailDoc", "sitePreviewUrl",
                                                "siteCatalogWhy", "siteCheckRows", "siteTplPending", "siteTplApply",
                                                "siteTplRowState")]
    return """
const app = (function () {
  const t = (key, vars) => key + (vars ? " " + JSON.stringify(vars) : "");
  const LANG = "ru", LOCALE = "ru-RU";                 // the language of the page (boot.js KaskadLang, 3.3.2)
  let FETCH = null;
  const doFetch = (url, init) => FETCH(url, init);
  const showLogin = () => {};
  const location = { pathname: "/" };
  let SITE = null;
  const rd = (key) => (key === "site" ? SITE : null);
%s
  return { request, normSite, siteLe, siteLive, siteDate, siteClean, siteNames, siteFp, SITE_DNS, SITE_WHY, SITE_KIND,
           RENEW_DAYS, SITE_FIX, SITE_CHECK, SITE_RANDOM, siteMaskOn, siteMaskAvail, siteViaProxy, siteFullUrl,
           siteModeBody, siteTplQuery, siteCheckOf, siteTplFailOf, siteRefusalDoc, sitePreviewUrl, siteCatalogWhy,
           siteCheckRows, siteTplDoc, siteTplFailDoc, siteCheckWarnings, SITE_TPL_WHY, SITE_STUB, panelBase, qs,
           timeoutOf, siteTplPending, siteTplApply, siteTplRowState, setSite: (s) => { SITE = s; }, setFetch: (f) => { FETCH = f; } };
})();
""" % "\n".join(parts)


def node(script, data):
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(scope() + "const DATA = %s;\n(async () => {\n%s\n})().catch((e) => { console.log(JSON.stringify("
                "{crash: String(e && e.stack || e)})); });\n" % (json.dumps(data, ensure_ascii=False), script))
        path = f.name
    try:
        res = subprocess.run(["node", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
                             env=dict(os.environ, TZ="Europe/Moscow"))
    finally:
        os.unlink(path)
    out = res.stdout.decode("utf-8").strip().splitlines()
    if res.returncode != 0 or not out:
        raise AssertionError("node: %s" % res.stderr.decode("utf-8", "replace")[-2000:])
    doc = json.loads(out[-1])
    if "crash" in doc:
        raise AssertionError(doc["crash"])
    return doc


NORM = """
const out = {};
for (const k of Object.keys(DATA)) {
  const s = app.normSite(JSON.parse(DATA[k].text));
  out[k] = { s, le: app.siteLe(s), live: app.siteLive(s), until: s && s.cert ? app.siteDate(s.cert.notAfter) : null };
}
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(shutil.which("node"), "нет node: функции app.js не запустить")
class SitePageJsTest(SiteFakes, PanelCase):
    def setUp(self):
        super().setUp()
        self.site_fakes()

    def answer(self, method, path, body=None):
        r = self.call(method, path, body)
        return {"status": r.status, "text": r.body.decode("utf-8")}

    def test_s1_status_as_the_page_reads_it(self):
        got = {"none": self.answer("GET", "/api/site")}
        self.ok("PUT", "/api/site/domain", {"domain": DOMAIN})
        got["domain"] = self.answer("GET", "/api/site")
        self.ok("POST", "/api/site/cert", {})
        got["le"] = self.answer("GET", "/api/site")
        doc = node(NORM, got)
        none, dom, le = doc["none"], doc["domain"], doc["le"]
        self.assertEqual((none["s"]["domain"], none["s"]["dns"], none["le"]), ("", None, False))
        self.assertEqual((dom["s"]["domain"], dom["s"]["name"], dom["s"]["dns"]["state"], dom["s"]["dns"]["ready"]),
                         (DOMAIN, DOMAIN, "ok", True))
        self.assertEqual(dom["s"]["dns"]["own"], json.loads(got["domain"]["text"])["dns"]["own"])
        self.assertFalse(dom["le"])
        s = le["s"]
        self.assertTrue(le["le"] and le["live"])
        self.assertEqual((s["cert"]["kind"], s["cert"]["names"], s["cert"]["days"], s["cert"]["staging"], s["cert"]["issuer"]),
                         ("letsencrypt", [DOMAIN], 83, False, "R11"))
        self.assertEqual(le["until"], "30.12.2026")
        self.assertEqual(s["renewBy"], json.loads(got["le"]["text"])["renew"]["by"] or "")
        self.assertEqual(s["impact"], {"sstp": 0, "ocserv": 0})

    def test_s1_cyrillic_domain(self):
        self.ok("PUT", "/api/site/domain", {"domain": "впн.пример.рф"})
        doc = node(NORM, {"idn": self.answer("GET", "/api/site")})["idn"]["s"]
        self.assertEqual(doc["name"], "впн.пример.рф")
        self.assertTrue(doc["domain"].startswith("xn--"))
        self.assertNotEqual(doc["name"], doc["domain"])

    def test_s2_refusal_reaches_the_page_whole(self):
        self.ok("PUT", "/api/site/domain", {"domain": DOMAIN})
        self.certbot_present()
        self.certbot.mode = "limit"
        limit = self.answer("POST", "/api/site/cert", {})
        self.answers[dns.A] = ("ok", [])
        nodns = self.answer("POST", "/api/site/cert", {})
        self.assertEqual((limit["status"], nodns["status"]), (409, 409))
        doc = node("""
const out = {};
for (const k of ["limit", "nodns"]) {
  const a = DATA[k];
  app.setFetch(async () => ({ status: a.status, ok: false, text: async () => a.text }));
  try { await app.request("POST", "/api/site/cert", {}); out[k] = "no error"; }
  catch (e) { out[k] = { status: e.status, message: e.message, reason: e.body && e.body.reason, retry: e.body && e.body.retry_after || null,
                         title: app.SITE_WHY[e.body && e.body.reason] || null }; }
}
console.log(JSON.stringify(out));
""", {"limit": limit, "nodns": nodns})
        lim = json.loads(limit["text"])
        self.assertEqual(doc["limit"], {"status": 409, "message": lim["text"], "reason": "limit",
                                        "retry": "2026-10-08T12:00:00Z", "title": "site.whyLimit"})
        self.assertEqual((doc["nodns"]["reason"], doc["nodns"]["title"]), ("dns", "site.whyDns"))
        self.assertIn("A-запис", doc["nodns"]["message"])

    def test_s2_change_of_a_working_domain_without_a_record_changes_nothing(self):
        """T-M6-qs330d №1: PUT /api/site/domain with a new name and no A record — 409 «dns», the working domain
        stays; the route waits as long as an issue (the switch issues the new certificate)."""
        self.ok("PUT", "/api/site/domain", {"domain": DOMAIN})
        self.ok("POST", "/api/site/cert", {})
        self.answers_for["other.example.org"] = {dns.A: ("nxdomain", []), dns.AAAA: ("nxdomain", [])}
        got = self.answer("PUT", "/api/site/domain", {"domain": "other.example.org"})
        body = json.loads(got["text"])
        self.assertEqual((got["status"], body["reason"], body["kept"]), (409, "dns", DOMAIN))
        self.assertIn("работает как раньше", body["text"])
        st = json.loads(self.answer("GET", "/api/site")["text"])
        self.assertEqual((st["domain"], st["cert"]["kind"]), (DOMAIN, "letsencrypt"))
        doc = node("console.log(JSON.stringify({t: app.timeoutOf('PUT', '/api/site/domain'), c: app.timeoutOf('POST', '/api/site/cert'), "
                   "why: app.SITE_WHY.switch}));", {})
        self.assertEqual(doc["t"], doc["c"])
        self.assertEqual(doc["why"], "site.whySwitch")

    def test_s3_dry_run_names_the_devices(self):
        registry.register(DummyInbound(kind="sstp", proto="tcp", default_port=0))
        self.gateway(("wg", "sstp"))
        gops.device_add(self.w.runner, self.uid, "Ноутбук", kind="laptop", platform="windows", access=["sstp"])
        self.ok("PUT", "/api/site/domain", {"domain": DOMAIN})
        self.ok("POST", "/api/site/cert", {})
        dry = json.loads(self.answer("PUT", "/api/site/use", {"kind": "sstp", "on": True, "dry_run": True})["text"])
        self.assertEqual([(r["name"], r["user_name"], r["kind"]) for r in dry["affected"]], [("Ноутбук", "Мама", "sstp")])
        rows = dry["affected"] + [dict(dry["affected"][0], device="d-%08d" % i, name="Телефон %d" % i, user_name="")
                                  for i in range(12)]
        doc = node("console.log(JSON.stringify({names: app.siteNames(DATA.rows), one: app.siteNames(DATA.rows.slice(0, 1))}));",
                   {"rows": rows})
        self.assertEqual(doc["one"], "Ноутбук (Мама)")
        self.assertTrue(doc["names"].endswith('Телефон 8 site.andMore {"n":3}'))

    def test_s4_maps_cover_the_core(self):
        doc = node("console.log(JSON.stringify({dns: Object.keys(app.SITE_DNS), why: Object.keys(app.SITE_WHY), kind: app.SITE_KIND}));", {})
        states = set(re.findall(r"^    (\w+) {2,}", dns.__doc__, flags=re.M))
        self.assertEqual(states, {"ok", "partial", "elsewhere", "none", "unknown"})
        self.assertEqual(set(doc["dns"]), states)
        with open(ops.__file__, encoding="utf-8") as f:
            reasons = set(re.findall(r'Refusal\("(\w+)"', f.read()))    # port80, dns, apt + режим «сайт» (4.0-C)
        with open(acme.__file__, encoding="utf-8") as f:
            src = f.read()
        reasons |= set(re.findall(r'return "(\w+)"', src[src.index("def cause("):src.index("def retry_after(")]))
        self.assertEqual(reasons, {"port80", "dns", "limit", "net", "apt",
                                   "no_domain", "no_cert", "port443", "neighbour", "nginx_test", "template",
                                   "switch"})          # a failed change of a working domain (T-M6-qs330d №1)
        self.assertEqual(set(doc["why"]), reasons)
        # the kinds of the rows «выдать заново» of the site: site.ops.TITLES (T12: Hysteria2 switches by itself)
        self.assertEqual(doc["kind"], {"sstp": "SSTP", "ocserv": "OpenConnect", "hy2": "Hysteria2"})
        self.assertEqual(doc["kind"], ops.TITLES)

    def test_s5_field_and_fingerprint(self):
        doc = node("""
const clean = ["https://VPN.Example.com/", "vpn.example.com.", "  http://vpn.example.com:8443/path?q#x ", "впн.пример.рф"].map(app.siteClean);
const le = { domain: "vpn.example.com", name: "vpn.example.com", cert: { kind: "letsencrypt", notAfter: "2026-12-30T10:00:00Z", days: 83, staging: false }, use: { panel: true } };
const fp = [];
app.setSite(le); fp.push(app.siteFp(true));
app.setSite(Object.assign({}, le, { use: { panel: false } })); fp.push(app.siteFp(true), app.siteFp(false));
app.setSite(Object.assign({}, le, { cert: Object.assign({}, le.cert, { staging: true }) })); fp.push(app.siteFp(false));
app.setSite(Object.assign({}, le, { cert: Object.assign({}, le.cert, { days: -2 }) })); fp.push(app.siteFp(false));
app.setSite(Object.assign({}, le, { cert: { kind: "selfsigned", notAfter: "2036-01-01T00:00:00Z", days: 3000 } })); fp.push(app.siteFp(false));
console.log(JSON.stringify({ clean, fp }));
""", {})
        self.assertEqual(doc["clean"], ["vpn.example.com", "vpn.example.com", "vpn.example.com", "впн.пример.рф"])
        until = {"domain": "vpn.example.com", "until": "30.12.2026"}
        self.assertEqual(doc["fp"], [until, None, until, None, None, None])



# ── 4.0-C: сайт-маскировка ───────────────────────────────────────────────────

DEPLOYED = {"ok": True, "id": "gh_good", "name": "Good Business", "source": "github", "fallback": False, "tries": 1,
            "errors": [], "warnings": [], "root": "/var/www/kaskad-site"}


@unittest.skipUnless(shutil.which("node"), "нет node: функции app.js не запустить")
class SiteMaskJsTest(SiteFakes, PanelCase):
    def setUp(self):
        super().setUp()
        self.site_fakes()
        self.deployed = dict(DEPLOYED)
        for p in (mock.patch.object(nginx, "ensure", return_value=[]), mock.patch.object(nginx, "write", return_value="start"),
                  mock.patch.object(nginx, "can_reject_handshake", return_value=True),
                  mock.patch.object(nginx, "ufw_allow443", return_value=False), mock.patch.object(nginx, "ours", return_value=True),
                  mock.patch.object(nginx, "remove", return_value="reload"), mock.patch.object(nginx, "ufw_delete443", return_value=False),
                  mock.patch.object(T, "deploy", side_effect=lambda runner, tid=None, root=None: dict(self.deployed, id=tid or "gh_good")),
                  mock.patch.object(consumers, "_reload_panel", return_value=False)):
            p.start()
            self.addCleanup(p.stop)

    def answer(self, method, path, body=None):
        r = self.call(method, path, body)
        return {"status": r.status, "text": r.body.decode("utf-8")}

    def site_on(self):
        self.ok("PUT", "/api/site/domain", {"domain": DOMAIN})
        self.ok("POST", "/api/site/cert", {})
        got = self.answer("PUT", "/api/site/mode", {"mode": "site", "template": "random"})
        self.assertEqual(got["status"], 200, got["text"])
        return got

    def test_s6_the_site_mode_and_the_secret_address(self):
        self.ok("PUT", "/api/site/domain", {"domain": DOMAIN})
        got = {"domain": self.answer("GET", "/api/site")}
        self.ok("POST", "/api/site/cert", {})
        got["le"] = self.answer("GET", "/api/site")
        bodies = node("""
console.log(JSON.stringify({ random: app.siteModeBody(true, app.SITE_RANDOM), picked: app.siteModeBody(true, { id: "gh_good", name: "Good" }),
  off: app.siteModeBody(false, { id: "gh_good" }) }));""", {})
        self.assertEqual(bodies, {"random": {"mode": "site", "template": "random"},
                                  "picked": {"mode": "site", "template": "gh_good"}, "off": {"mode": "domain"}})
        on = self.answer("PUT", "/api/site/mode", bodies["picked"])
        self.assertEqual(on["status"], 200, on["text"])
        self.assertEqual(json.loads(on["text"])["template"]["id"], "gh_good")
        got["on"] = self.answer("GET", "/api/site")
        got["url"] = self.answer("GET", "/api/site/panel-url")
        got["rotate"] = self.answer("POST", "/api/site/panel-path/rotate", {})
        got["url2"] = self.answer("GET", "/api/site/panel-url")
        doc = node("""
const out = {};
for (const k of ["domain", "le", "on"]) { const s = app.normSite(JSON.parse(DATA[k].text)); out[k] = { avail: app.siteMaskAvail(s), on: app.siteMaskOn(s), masked: s.site.panelMasked, full: app.siteFullUrl(s.site.panelMasked), tpl: s.site.template }; }
const staging = app.normSite(JSON.parse(DATA.le.text)); staging.cert.staging = true;
out.staging = app.siteMaskAvail(staging);
for (const k of ["url", "rotate", "url2"]) { const r = await (app.setFetch(async () => ({ status: DATA[k].status, ok: true, text: async () => DATA[k].text })), app.request("GET", "/api/site/panel-url")); out[k] = app.siteFullUrl(r.url); }
out.proxy = [app.siteViaProxy(app.panelBase("/app-" + "A".repeat(86) + "/")), app.siteViaProxy(app.panelBase("/app-" + "A".repeat(86) + "/index.html")), app.siteViaProxy(app.panelBase("/")), app.siteViaProxy(app.panelBase("/index.html"))];
out.preview = ["https://example.com/t/", "http://x.example/", "javascript:alert(1)", "data:text/html,x", "", null].map(app.sitePreviewUrl);
out.timeouts = [["PUT", "/api/site/mode"], ["POST", "/api/site/template"], ["POST", "/api/site/check"], ["POST", "/api/site/panel-path/rotate"], ["GET", "/api/site/templates"]].map((x) => app.timeoutOf(x[0], x[1]));
console.log(JSON.stringify(out));
""", got)
        self.assertEqual((doc["domain"]["avail"], doc["le"]["avail"], doc["on"]["avail"], doc["staging"]), (False, True, True, False))
        self.assertEqual((doc["le"]["on"], doc["on"]["on"]), (False, True))
        path = conf.load()["proxy"]["path"]
        self.assertIn("••••", doc["on"]["masked"])
        self.assertNotIn(path, got["on"]["text"])                          # STATUS полного пути не несёт
        self.assertEqual(doc["on"]["full"], "")                             # маска — не адрес
        self.assertEqual(doc["on"]["tpl"]["id"], "gh_good")
        self.assertEqual(doc["rotate"], "")                                 # ответ смены — маска: адрес — у panel-url
        self.assertTrue(doc["url"].startswith("https://%s/app-" % DOMAIN) and doc["url2"].startswith("https://%s/app-" % DOMAIN))
        self.assertNotEqual(doc["url"], doc["url2"])
        self.assertEqual(doc["url2"], "https://%s/%s/" % (DOMAIN, path))
        self.assertEqual(doc["proxy"], [True, True, False, False])
        self.assertEqual(doc["preview"], ["https://example.com/t/", "http://x.example/", "", "", "", ""])
        self.assertEqual(doc["timeouts"], [20 * 60000, 20 * 60000, 60000, 60000, 30000])

    def refusal_answers(self):
        got = {"no_domain": self.answer("PUT", "/api/site/mode", {"mode": "site"})}
        self.ok("PUT", "/api/site/domain", {"domain": DOMAIN})
        got["no_cert"] = self.answer("PUT", "/api/site/mode", {"mode": "site"})
        self.ok("POST", "/api/site/cert", {})
        with mock.patch.object(nginx, "neighbours", return_value=["govless"]):
            got["neighbour"] = self.answer("PUT", "/api/site/mode", {"mode": "site"})
        busy = {"free": False, "who": "проброс tcp/443 → 10.77.1.20:443", "conflicts": [{"kind": "forward", "detail": "x"}]}
        with mock.patch.object(ops, "port443", return_value=busy):
            got["port443"] = self.answer("PUT", "/api/site/mode", {"mode": "site"})
        with mock.patch.object(ops, "port80", return_value=dict(busy, who="проброс tcp/80 → 10.77.1.20:80")):
            got["port80"] = self.answer("PUT", "/api/site/mode", {"mode": "site"})
        self.deployed = {"ok": False, "id": "gh_good", "reason": "no_index", "text": "В шаблоне «gh_good» нет index.html",
                         "errors": [], "warnings": [], "root": "/var/www/kaskad-site"}
        got["template"] = self.answer("PUT", "/api/site/mode", {"mode": "site", "template": "gh_good"})
        self.deployed = dict(DEPLOYED)
        nokey = KaskadError("Каталог шаблонов без ключа GitHub не скачать.", data={"reason": "nokey", "needs_key": True})
        with mock.patch.object(T, "deploy", side_effect=nokey):
            got["template_nokey"] = self.answer("PUT", "/api/site/mode", {"mode": "site", "template": "gh_good"})
        with mock.patch.object(T, "deploy", side_effect=KaskadError("нет связи с GitHub", data={"reason": "net"})):
            got["template_net"] = self.answer("PUT", "/api/site/mode", {"mode": "site", "template": "gh_good"})
        with mock.patch.object(nginx, "ensure", side_effect=KaskadError("apt-get install nginx: блокировка dpkg")):
            got["apt"] = self.answer("PUT", "/api/site/mode", {"mode": "site"})
        with mock.patch.object(nginx, "write", side_effect=nginx.NginxTestError("duplicate listen options", ours=False)):
            got["nginx_test"] = self.answer("PUT", "/api/site/mode", {"mode": "site"})
        self.certbot.mode = "limit"
        self.clock += 70 * DAY                                               # «Продлить» — в пределах срока
        got["cert_limit"] = self.answer("POST", "/api/site/cert/renew", {})
        return got

    def test_s7_every_refusal_of_the_site_mode_reaches_its_window(self):
        got = self.refusal_answers()
        doc = node("""
const out = {};
for (const k of Object.keys(DATA)) {
  const a = DATA[k];
  app.setFetch(async () => ({ status: a.status, ok: false, text: async () => a.text }));
  try { await app.request("PUT", "/api/site/mode", { mode: "site" }); out[k] = "no error"; }
  catch (e) { out[k] = { status: e.status, reason: e.body && e.body.reason, mask: app.siteRefusalDoc(e, true), cert: app.siteRefusalDoc(e, false) }; }
}
console.log(JSON.stringify(out));
""", got)
        reasons = ("no_domain", "no_cert", "port443", "port80", "neighbour", "apt", "nginx_test", "template",
                   "template_nokey", "template_net")
        for k in reasons:
            d = doc[k]
            self.assertEqual((d["status"], d["reason"]), (409, k.split("_")[0] if k.startswith("template") else k), k)
            server = json.loads(got[k]["text"])["text"]
            self.assertEqual(d["mask"]["text"], server, k)                  # текст сервера — целиком
            self.assertTrue(d["mask"]["title"] and d["mask"]["list"], k)    # что случилось и что сделать
        self.assertIn("govless", doc["neighbour"]["mask"]["text"])
        self.assertEqual(doc["apt"]["mask"]["title"], "site.whyMaskApt")    # у сайта apt — nginx или шаблон
        self.assertEqual(doc["apt"]["cert"]["title"], "site.whyApt")        # у сертификата — certbot
        self.assertEqual(doc["apt"]["mask"]["list"], ["site.fixMaskApt"])
        self.assertEqual((doc["port80"]["mask"]["title"], doc["port80"]["mask"]["list"]), ("site.whyPort80", ["site.fixMaskPort80"]))
        self.assertIn("10.77.1.20:80", doc["port80"]["mask"]["text"])
        tpl = doc["template"]["mask"]
        self.assertEqual((tpl["title"], tpl["list"], tpl["key"]),
                         ("site.whyTemplate", ["site.tplWhyNoIndex", "site.tplFixOther", "site.tplKeptMode"], False))
        self.assertEqual(doc["template_nokey"]["mask"]["list"], ["site.tplWhyNokey", "site.tplFixKey", "site.tplKeptMode"])
        self.assertTrue(doc["template_nokey"]["mask"]["key"])                # окно предложит ввести ключ GitHub
        self.assertEqual(doc["template_net"]["mask"]["list"], ["site.tplWhyNet", "site.tplFixNet", "site.tplKeptMode"])
        for k in ("template", "template_nokey", "template_net"):              # всё осталось как было
            self.assertEqual(json.loads(got[k]["text"])["template_id"], "gh_good")
        self.assertEqual(self.site()["mode"], "domain")
        self.assertEqual(set(node("console.log(JSON.stringify(Object.keys(app.SITE_TPL_WHY)));", {})), set(ops.TEMPLATE_REASONS))
        self.assertEqual(doc["no_domain"]["mask"]["title"], "site.whyNoDomain")
        self.assertEqual(doc["cert_limit"]["reason"], "limit")              # отказы сертификата — как были
        self.assertEqual(doc["cert_limit"]["cert"]["title"], "site.whyLimit")
        self.assertEqual(len(doc["cert_limit"]["cert"]["list"]), 1)         # только «повторить после …»

    def test_s7_a_random_template_fell_back_to_the_simple_page(self):
        self.ok("PUT", "/api/site/domain", {"domain": DOMAIN})
        self.ok("POST", "/api/site/cert", {})
        self.deployed = dict(DEPLOYED, fallback=True, name="Простая страница", tries=0,
                             warnings=["каталога шаблонов нет: поставлена простая страница"])
        with mock.patch.object(T, "deploy", side_effect=lambda runner, tid=None, root=None: dict(self.deployed, id=T.STUB_ID)):
            on = self.answer("PUT", "/api/site/mode", {"mode": "site", "template": "random"})
        doc = json.loads(on["text"])
        self.assertEqual((on["status"], doc["fallback"], doc["warnings"]), (200, True, self.deployed["warnings"]))
        got = node("console.log(JSON.stringify({ stub: app.SITE_STUB, tpl: DATA.template && DATA.template.id }));", doc)
        self.assertEqual(got["stub"], T.STUB_ID)
        # a failed change of the template on a live site: 200 {ok: false, reason} — the window says the site is the old one
        self.deployed = {"ok": False, "id": "gh_good", "reason": "timeout", "text": "не скачался за 90 секунд"}
        fail = self.answer("POST", "/api/site/template", {"id": "gh_good"})
        with mock.patch.object(T, "deploy", side_effect=KaskadError("без ключа", data={"reason": "bad", "needs_key": True})):
            key = self.answer("POST", "/api/site/template", {"id": "gh_good"})
        doc = node("""
const out = {};
for (const k of Object.keys(DATA)) {
  const a = DATA[k];
  app.setFetch(async () => ({ status: a.status, ok: a.status === 200, text: async () => a.text }));
  try { await app.request("POST", "/api/site/template", { id: "gh_good" }); out[k] = "no error"; } catch (e) { out[k] = app.siteTplFailDoc(e); }
}
console.log(JSON.stringify(out));""", {"fail": fail, "key": key})
        self.assertEqual(doc["fail"], {"title": "site.whyTemplate", "text": "не скачался за 90 секунд",
                                       "list": ["site.tplWhyTimeout", "site.tplFixOther", "site.tplKeptSite"], "key": False})
        self.assertEqual((doc["key"]["key"], doc["key"]["list"][0]), (True, "site.tplWhyNokey"))

    def test_s8_every_verdict_of_the_check_in_words(self):
        with open(ops.__file__, encoding="utf-8") as f:
            verdicts = set(re.findall(r'"verdict": "(\w+)"', f.read()))
        self.assertEqual(verdicts, {"ok", "dns_mismatch", "no_cert", "http_error", "no_site", "cert_expiring",
                                    "panel_unreachable"})
        got = {"no_site": self.answer("POST", "/api/site/check", {})}
        self.site_on()
        self.w.sysd.units["nginx"] = {"enabled": True, "active": True, "packaged": True}   # in autostart (T-M6 №2)
        codes = {"root": 200, "panel": 302, "miss": 404, "http": 301}

        def curl(runner, url, resolve):
            if url.startswith("http://"):
                return codes["http"]
            if "/app-xxxx" in url:
                return codes["miss"]
            return codes["panel"] if "/app-" in url else codes["root"]
        with mock.patch.object(ops, "_curl_code", side_effect=curl), mock.patch.object(nginx, "default_page", return_value=True), \
                mock.patch.object(self.w.runner, "which", side_effect=lambda name: "/usr/sbin/" + name):
            got["warned"] = self.answer("POST", "/api/site/check", {})
        with mock.patch.object(ops, "_curl_code", side_effect=curl):
            got["ok"] = self.answer("POST", "/api/site/check", {})
            got["status_ok"] = self.answer("GET", "/api/site")
            codes["root"] = 502
            got["root_bad"] = self.answer("POST", "/api/site/check", {})
            codes["root"], codes["miss"] = 200, 200
            got["decoy_bad"] = self.answer("POST", "/api/site/check", {})
            codes["miss"], codes["panel"] = 404, 404
            got["panel_unreachable"] = self.answer("POST", "/api/site/check", {})
            codes["panel"] = 200
            self.clock += 60 * DAY
            got["cert_expiring"] = self.answer("POST", "/api/site/check", {})
            self.clock -= 60 * DAY
            self.answers[dns.A] = ("ok", ["198.51.100.99"])
            self.ok("POST", "/api/site/dns", {})
            got["dns_mismatch"] = self.answer("POST", "/api/site/check", {})
        doc = node("""
const out = {};
for (const k of Object.keys(DATA)) {
  const a = DATA[k];
  if (k === "status_ok") { const s = app.normSite(JSON.parse(a.text)); out[k] = { verdict: s.site.check.verdict, rows: app.siteCheckRows(s.site.check, s.cert.days).map((r) => r.st) }; continue; }
  app.setFetch(async () => ({ status: a.status, ok: a.status === 200, text: async () => a.text }));
  let r;
  try { r = await app.request("POST", "/api/site/check", {}); } catch (e) { r = app.siteCheckOf(e); if (!r) { out[k] = { crash: e.message }; continue; } }
  out[k] = { verdict: r.verdict, known: !!app.SITE_CHECK[r.verdict], rows: Object.fromEntries(app.siteCheckRows(r, 83).map((x) => [x.k, x.st])),
             texts: app.siteCheckRows(r, 83).map((x) => x.text), warnings: app.siteCheckWarnings(r) };
}
console.log(JSON.stringify(out));
""", got)
        self.assertEqual(set(doc["status_ok"]["rows"]), {"ok"})                 # последняя проверка — в STATUS
        self.assertEqual((doc["warned"]["verdict"], doc["warned"]["warnings"]), ("ok", [ops.warning(ops.DEFAULT_PAGE)]))
        self.assertEqual(doc["ok"]["warnings"], [])
        want = {"ok": {"root": "ok", "redirect": "ok", "panel": "ok", "decoy": "ok", "cert": "ok"},
                "root_bad": {"root": "bad", "redirect": "ok", "panel": "skip", "decoy": "skip", "cert": "ok"},
                "decoy_bad": {"root": "ok", "redirect": "ok", "panel": "skip", "decoy": "bad", "cert": "ok"},
                "panel_unreachable": {"root": "ok", "redirect": "ok", "panel": "bad", "decoy": "ok", "cert": "ok"},
                "cert_expiring": {"root": "ok", "redirect": "ok", "panel": "ok", "decoy": "ok", "cert": "warn"},
                "dns_mismatch": {"root": "skip", "redirect": "skip", "panel": "skip", "decoy": "skip", "cert": "ok"},
                "no_site": {"root": "skip", "redirect": "skip", "panel": "skip", "decoy": "skip", "cert": "ok"}}
        verdict = {"root_bad": "http_error", "decoy_bad": "http_error"}
        for k, rows in want.items():
            self.assertEqual(doc[k]["verdict"], verdict.get(k, k), k)
            self.assertTrue(doc[k]["known"], k)
            self.assertEqual(doc[k]["rows"], rows, k)
        self.assertIn('site.chkRootBad {"code":"502","n":83}', doc["root_bad"]["texts"])
        self.assertIn('site.chkCertWarn {"code":"site.chkNoAnswer","n":23}', doc["cert_expiring"]["texts"])
        # verdicts the fake node cannot reach: no_cert (STATUS check without a live certificate)
        rows = node("console.log(JSON.stringify(app.siteCheckRows({ ok: false, verdict: 'no_cert' }, null).map((x) => x.st)));", {})
        self.assertEqual(rows, ["skip", "skip", "skip", "skip", "bad"])
        self.assertEqual(set(node("console.log(JSON.stringify(Object.keys(app.SITE_CHECK)));", {})), verdicts)

    def catalog_env(self):
        env = mock.patch.dict(os.environ, {"KASKAD_ASSETS_ROOTS": "", "GH_TOKEN": "", "GITHUB_TOKEN": "",
                                           "GOINST_TOKEN_FILE": self.path("/nonexistent")})
        env.start()
        self.addCleanup(env.stop)
        p = mock.patch.object(T, "CATALOG_SHA256", fx.catalog_sha())
        p.start()
        self.addCleanup(p.stop)

    def test_s9_the_catalog_through_the_query_of_the_page(self):
        self.catalog_env()
        with mock.patch.object(ghkey, "open_github", side_effect=AssertionError("no network in tests")):
            nokey = self.answer("GET", "/api/site/categories")
        key = ("ghp_" + "a" * 36, "env")
        with mock.patch.object(ghkey, "current", return_value=key), \
                mock.patch.object(ghkey, "open_github", side_effect=urllib.error.URLError("down")):
            net = self.answer("GET", "/api/site/categories")
        with mock.patch.object(ghkey, "current", return_value=key), \
                mock.patch.object(ghkey, "open_github", side_effect=urllib.error.HTTPError("u", 401, "no", {}, None)):
            bad = self.answer("GET", "/api/site/templates")
        path = T.catalog_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(fx.catalog_bytes())
        queries = node("""
const q = (st) => "/api/site/templates" + app.qs(app.siteTplQuery(st));
console.log(JSON.stringify([q({ category: "", query: "  ", page: 1 }), q({ category: "business", query: " кафе ", page: 0 }),
  q({ category: "", query: "ромашка", page: 3 }), q({ category: "business", query: "x".repeat(80), page: 2 })]));""", {})
        self.assertEqual(queries[0], "/api/site/templates?page=1")                 # пустое не отправляется
        got = {"cats": self.answer("GET", "/api/site/categories")}
        for i, q in enumerate(queries):
            got["q%d" % i] = self.answer("GET", q)
        got["tplfail"] = None
        self.deployed = {"ok": False, "id": "gh_good", "reason": "no_index", "text": "В шаблоне «gh_good» нет index.html",
                         "errors": [], "warnings": [], "root": "/var/www/kaskad-site"}
        self.site_on_with_deployed()
        got["tplfail"] = self.answer("POST", "/api/site/template", {"id": "gh_good"})
        got.update({"nokey": nokey, "net": net, "bad": bad})
        doc = node("""
const out = {};
for (const k of Object.keys(DATA)) {
  const a = DATA[k];
  app.setFetch(async () => ({ status: a.status, ok: a.status === 200, text: async () => a.text }));
  try { const r = await app.request("GET", "/x"); out[k] = { ok: true, r }; }
  catch (e) { out[k] = { status: e.status, why: app.siteCatalogWhy(e), tpl: app.siteTplFailOf(e), check: app.siteCheckOf(e), message: e.message }; }
}
console.log(JSON.stringify(out));
""", got)
        self.assertEqual((doc["nokey"]["status"], doc["nokey"]["why"]), (500, "key"))
        self.assertIn("ключ", doc["nokey"]["message"])
        self.assertEqual(doc["bad"]["why"], "key")
        self.assertEqual(doc["net"]["why"], "net")
        cats = doc["cats"]["r"]
        self.assertEqual(cats["total"], 17)
        self.assertEqual(cats["categories"][0], {"id": "business", "title": "Бизнес и корпоративные", "icon": "briefcase", "count": 3})
        self.assertEqual(doc["q0"]["r"]["total"], 17)
        self.assertEqual([i["id"] for i in doc["q1"]["r"]["items"]], ["gh_cafe"])
        self.assertEqual(doc["q1"]["r"]["page"], 1)                           # страница 0 — первая
        self.assertEqual([i["id"] for i in doc["q2"]["r"]["items"]], ["gh_cafe"])   # ё = е, без учёта регистра; после конца — последняя
        self.assertEqual(doc["q3"]["r"]["query"], "x" * 64)                   # поиск — до 64 знаков, как у сервера
        one = doc["q1"]["r"]["items"][0]
        for field in ("name", "description", "author", "preview"):
            self.assertIn(field, one)
        tf = doc["tplfail"]
        self.assertEqual((tf["status"], tf["check"]), (200, None))
        self.assertEqual((tf["tpl"]["reason"], tf["tpl"]["text"]), ("no_index", "В шаблоне «gh_good» нет index.html"))

    def test_s10_is_the_template_still_there(self):
        from kaskad.site import tplhealth as H
        from tests.site.test_tplhealth import Web
        self.catalog_env()
        path = T.catalog_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(fx.catalog_bytes())
        repo = {fx.GH + "dist": "gone", fx.GH + "home": "unknown"}
        web = Web({"https://example.org/gh_cafe/": 404})
        H.mark("gh_good", "alive")                                  # the deployment knows it
        with mock.patch.object(H, "repo_state", side_effect=lambda runner, url, timeout=8: repo.get(url, "ok")), \
                mock.patch.object(H, "_open", web):
            got = {"first": self.answer("GET", "/api/site/templates?category=business")}
            got["check"] = self.answer("POST", "/api/site/templates/check", {"ids": ["gh_dist", "gh_cafe", "gh_home", "nope"]})
            got["again"] = self.answer("GET", "/api/site/templates?category=business")
            nocsrf = self.call("POST", "/api/site/templates/check", {"ids": ["gh_good"]}, csrf=False)
            many = self.call("POST", "/api/site/templates/check", {"ids": ["gh_good"] * 26})
            bad = self.call("POST", "/api/site/templates/check", {"ids": "gh_good"})
        self.assertEqual((nocsrf.status, many.status, bad.status), (403, 400, 400))
        self.assertIn("25", many.json()["error"])
        self.deployed = {"ok": False, "id": "gh_dist", "reason": "gone", "text": T.TEXTS["gone"] % "gh_dist",
                         "errors": [], "warnings": [], "root": "/var/www/kaskad-site"}
        self.site_on_with_deployed()
        got["gone"] = self.answer("POST", "/api/site/template", {"id": "gh_dist"})
        doc = node("""
const first = JSON.parse(DATA.first.text), check = JSON.parse(DATA.check.text), again = JSON.parse(DATA.again.text);
const pend = app.siteTplPending(first.items);
const done = app.siteTplApply(pend.hp, pend.need, check);
const failed = app.siteTplApply(pend.hp, pend.need, null);
const rows = (hp) => Object.fromEntries(first.items.map((x) => [x.id, app.siteTplRowState(x, hp[x.id])]));
app.setFetch(async () => ({ status: DATA.gone.status, ok: true, text: async () => DATA.gone.text }));
let gone = null;
try { await app.request("POST", "/x", {}); } catch (e) { gone = app.siteTplFailDoc(e); }
const c = new AbortController();
let seenSignal = null, aborted = null;
app.setFetch((url, init) => new Promise((ok, no) => { seenSignal = !!init.signal;
  init.signal.addEventListener("abort", () => no(Object.assign(new Error("aborted"), { name: "AbortError" }))); }));
const pending = app.request("POST", "/api/site/templates/check", { ids: ["gh_good"] }, { signal: c.signal });
c.abort();
try { await pending; } catch (e) { aborted = e.code; }
console.log(JSON.stringify({ seenSignal, aborted, firstHealth: first.items.map((x) => x.health), pend, before: rows(pend.hp), done, after: rows(done),
  failed, checkItems: check.items, again: Object.fromEntries(again.items.map((x) => [x.id, x.health && x.health.state])), gone,
  timeout: app.timeoutOf("POST", "/api/site/templates/check") }));""", got)
        self.assertEqual(doc["firstHealth"], [{"state": "alive", "text": "Шаблон доступен"}, None, None])
        self.assertEqual(doc["pend"], {"hp": {"gh_good": "alive", "gh_dist": "checking", "gh_cafe": "checking"},
                                       "need": ["gh_dist", "gh_cafe"]})
        b = doc["before"]
        self.assertEqual((b["gh_good"]["chip"], b["gh_good"]["preview"], b["gh_good"]["choose"]), ("", True, True))
        self.assertEqual((b["gh_dist"]["chip"], b["gh_dist"]["preview"], b["gh_dist"]["choose"]),
                         ("site.tplCheckingChip", True, True))                   # «проверяю…», выбрать не мешает
        self.assertEqual(doc["checkItems"], {"gh_dist": H.doc("gone"), "gh_cafe": H.doc("no_preview"),
                                             "gh_home": H.doc("unknown"), "nope": H.doc("unknown")})
        self.assertEqual(doc["done"], {"gh_good": "alive", "gh_dist": "gone", "gh_cafe": "no_preview"})
        a = doc["after"]
        self.assertEqual(a["gh_dist"], {"st": "gone", "chip": "site.tplGoneChip", "tone": "danger", "note": "site.tplGone",
                                        "dim": True, "preview": False, "choose": False})
        self.assertEqual(a["gh_cafe"], {"st": "no_preview", "chip": "site.tplNoPreviewChip", "tone": "warning",
                                        "note": "site.tplNoPreview", "dim": False, "preview": False, "choose": True})
        self.assertEqual((a["gh_good"]["st"], a["gh_good"]["dim"], a["gh_good"]["preview"]), ("alive", False, True))
        self.assertEqual(doc["failed"], {"gh_good": "alive", "gh_dist": "unknown", "gh_cafe": "unknown"})
        self.assertEqual(doc["again"], {"gh_good": "alive", "gh_dist": "gone", "gh_cafe": "no_preview"})   # из кэша
        self.assertEqual(doc["gone"]["list"][0], "site.tplWhyGone")
        self.assertEqual(doc["gone"]["text"], "Шаблон «gh_dist» удалён автором — выберите другой.")
        self.assertEqual(doc["timeout"], 60 * 1000)
        self.assertEqual((doc["seenSignal"], doc["aborted"]), (True, "timeout"))      # отмена вызывающим доходит до fetch
        js = read("app.js")                                           # листание отменяет проверку прежней страницы
        self.assertIn("if (st.checkCtl) { st.checkCtl.abort(); st.checkCtl = null; }", js)
        self.assertIn("API.site.checkTemplates({ ids: pend.need }, ctl ? { signal: ctl.signal } : undefined)", js)
        self.assertNotIn("https://example.org/gh_dist/", [u for _, u in web.seen])      # удалён — образец не спрашивали

    def site_on_with_deployed(self):
        keep, self.deployed = self.deployed, dict(DEPLOYED)
        try:
            self.site_on()
        finally:
            self.deployed = keep


if __name__ == "__main__":
    unittest.main()
