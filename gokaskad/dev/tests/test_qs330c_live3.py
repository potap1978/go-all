"""Kaskad 3.3.0, live check №3 (k3/stand/logs/T-M6-qs330c.md):

    №1   the wizard, stage 4: ONE check of the domain for every button («Проверить», Enter, «Проверить снова» of the
         certificate) and always by the field as it is now; the results of the name before go away when the name
         changes — the DNS block never signs them with the new name;
    §0/2 the limit of Let's Encrypt (5 certificates of the same names a week): «take another name (a subdomain)» and
         the time to repeat by the clock of the server (CLI, SSH menu, bot) — the wizard says it by the browser's;
    №3   a device behind a VLESS exit: ping (ICMP) does not go — said where people look (the path of the traffic, the
         exit in the panel and the wizard), a note, not a failure;
    №4   after ``site remove`` the wizard tells the site by the fact of the node, not by the record of its step;
    №5   the site switched on again keeps its template; a new random one — only by «Другой» / ``--random``;
    №6   «Конфиг xray невалиден» names the cause: the field of the key, never its value, no uuid/keys/paths;
    №7   the restore after a reboot is told once per boot (the journal and the events of the panel);
    №17  (check №2) a key without a name of its own: the overview of the panel and the scheme do not repeat it;
    №19  (check №2) ``backup undo`` on the same server: no words of a move (the A record, the bot on «the old server»).
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from kaskad import backup, boot, state
from kaskad.gw import collect, exits, ops as gops, trace
from kaskad.setup import steps
from kaskad.site import acme, model as smodel, ops as sops
from tests import jsparts
from tests.fakes_gw import parser_source
from tests.helpers import zone
from tests.site.test_port80 import Port80Case
from tests.test_gw_tg1 import AfterRebootTest
from tests.gw.test_trace import TraceCase, D2, D3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "lib", "kaskad", "panel", "static")
INSTALL = os.path.join(ROOT, "install.sh")


def read(rel):
    with open(os.path.join(STATIC, rel), encoding="utf-8") as f:
        return f.read()


def node(src, env=None):
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(src)
        path = f.name
    try:
        r = subprocess.run(["node", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
                           env=dict(os.environ, **(env or {})))
    finally:
        os.unlink(path)
    if r.returncode:
        raise AssertionError(r.stderr.decode("utf-8", "replace")[-3000:])
    return json.loads(r.stdout.decode("utf-8").strip().splitlines()[-1])


# ── №1, №2 (page), №4: the wizard's stage «Домен и сайт» in node ────────────────────────────────────────────────

def wizard_scope():
    js = read("setup/setup.js")
    # 3.3.3 (DomainSteps): поддомен и домен — два поля; их вид и имя целиком — SLD … domReady, setDomFields
    parts = [jsparts.const(js, "domName"), jsparts.const(js, "isMail"), jsparts.dates(read("boot.js"))]
    parts += [jsparts.const(js, n) for n in ("SLD", "LBL", "subOk", "domFull", "domReady", "isWorkDomain")]
    parts += [jsparts.function(js, n) for n in ("domSplit", "domBody", "domOk", "setDomFields", "checkDomain", "dropDomainResults", "domainEdited", "autoCert",
                                                "certNow", "workDomain",
                                                "dnsBlock", "certErrText", "localWhen", "siteOn", "certCard",
                                                "siteCard", "warnNote", "reissueBtns", "lastTry", "emailNote",
                                                # VLESS Ш4: the card of the site asks VLESS (state.vless) too
                                                "vlessSite", "vlessKids", "vlessMode", "vlessAnswer", "riskBlock",
                                                "askReality", "askPrice", "whoList", "sitePrice", "siteAnswer",
                                                "siteThenNext")]
    parts += [jsparts.const(js, n) for n in ("vlessAsks", "sitePriced")]
    i18n = read("setup/setup-i18n-ru.js")
    return """
const window = {};
%s
const RU = window.KASKAD_SETUP_I18N || window.KASKAD_I18N_SETUP || {};
const t = (k, v) => { let s = RU[k] || k; Object.keys(v || {}).forEach((x) => { s = s.split("{" + x + "}").join(v[x]); }); return s; };
const out = { steps: [], stops: [], renders: 0, certs: 0 };
let NOW = 1000;
const now = () => NOW;
const S = { res: {}, busy: {}, st: null, stAt: 0, job: null };
const U = {};
const ui = (k) => (U[k] = U[k] || {});
const arr = (x) => (Array.isArray(x) ? x : []);
const render = () => { out.renders++; };
const stop = (n) => out.stops.push(n);
let JOB = null;
const running = (names) => (JOB && names.indexOf(JOB) >= 0 ? { items: [] } : null);
const startDnsPoll = () => out.steps.push(["dns-poll"]);
const startCert = async () => { out.certs++; out.steps.push(["cert"]); };
let DNS = { dns: "ok", a: ["203.0.113.10"] };
const step = async (name, body) => { out.steps.push([name, body]); const r = { ok: true, data: DNS }; S.res[name] = r; return r; };
const h = (tag, a, ...kids) => ({ tag, a: a || {}, kids: [].concat(...kids).filter((x) => x !== null && x !== undefined) });
const note = (tone, title, text, o) => ({ note: tone, title, text, btns: ((o && o.btns) || []), extra: (o && o.extra) || null });
const btn = (label, kind, act, o) => ({ btn: label, k: (o || {}).k, act });
const liveList = (title, rows, o) => ({ live: rows, acts: (o && o.acts) || [] });
const liveRow = () => null;
const runningNote = () => [];
const cardHead = (a, b) => ({ head: a, sub: b });
const card = (a, ...kids) => ({ card: [].concat(...kids) });
const code = (x) => ({ code: x });
const resNote = (r) => ({ resNote: r && r.text });
const input = () => ({ input: true });
const chip = () => null;
const tplList = () => [];
const loadTemplates = () => {};
const installSite = () => {};
let tplTimer = 0;
const tplNum = (n) => String(n);
const open = (k) => { out.steps.push(["open", k]); };
const ip = () => "203.0.113.10";
const icon = (n) => ({ icon: n });
const mmss = (x) => String(x);
let domTimer = 0;
%s
""" % (i18n, "\n".join(parts))


def flat(x):
    """Every string of a tree of the stubs above, in order."""
    out = []

    def walk(v):
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, list):
            for y in v:
                walk(y)
        elif isinstance(v, dict):
            for k in sorted(v):
                walk(v[k])
    walk(x)
    return " | ".join(out)


@unittest.skipUnless(shutil.which("node"), "нет node")
class WizardDomainTest(unittest.TestCase):
    def run_js(self, body, env=None):
        return node(wizard_scope() + "\n(async () => {\n" + body + "\n})().catch((e) => { console.error(e.stack); process.exit(1); });",
                    env)

    def test_check_again_checks_the_name_in_the_field(self):
        """№1: the limit on m6-k3, the name changed to m6b-k3 in the field, «Проверить снова» — the name of the field
        goes to the server, DNS is checked for it, then the certificate."""
        got = self.run_js("""
const u = ui("domain");
setDomFields(u, "m6-k3.example.org"); await checkDomain();
S.res.cert = { ok: false, state: "error", error: { code: "limit", text: "лимит m6-k3" } };
setDomFields(u, "M6B-k3.example.org");             // typed without a re-render: the old blocks stay
const c = certCard(u);
const again = c.kids.find((x) => x && x.live).acts[0];
out.steps = []; out.certs = 0;
await again.act();
const before = { steps: out.steps.slice(), certs: out.certs };
await certNow();                                     // R-333 З1: the certificate — by the explicit press
console.log(JSON.stringify({ k: again.k, before, steps: out.steps, checked: u.checked, name: u.name, certs: out.certs,
  dns: flat(dnsBlock(u)) }));
function flat(x) { const o = []; (function w(v) { if (typeof v === "string") o.push(v); else if (Array.isArray(v)) v.forEach(w); else if (v && typeof v === "object") Object.keys(v).sort().forEach((k) => w(v[k])); })(x); return o.join(" | "); }
""")
        self.assertEqual(got["k"], "cert-again")
        # 3.3.3: the check is DNS only (nothing changes on the server); the name goes to the node with «cert» — by the
        # press «Выпустить сертификат», not by itself (R-333 З1)
        self.assertEqual(got["before"], {"steps": [["dns", {"name": "m6b-k3.example.org", "zone": "example.org"}]], "certs": 0})
        self.assertEqual(got["steps"], [["dns", {"name": "m6b-k3.example.org", "zone": "example.org"}], ["cert"]])
        self.assertEqual((got["checked"], got["name"], got["certs"]), ("m6b-k3.example.org",) * 2 + (1,))
        self.assertIn("m6b-k3.example.org → 203.0.113.10", got["dns"])

    def test_editing_the_field_drops_the_results_of_the_name_before(self):
        got = self.run_js("""
const u = ui("domain");
setDomFields(u, "old.example.org"); await checkDomain();
S.res.cert = { ok: false, state: "error", error: { code: "limit", text: "лимит old" } };
const before = [!!u.dns, !!S.res.cert, out.renders];
u.dom = "Example.org "; domainEdited();             // the same name (spaces, case): nothing goes
const same = [!!u.dns, !!S.res.cert];
u.sub = "new"; domainEdited();
const after = { dns: u.dns, cert: S.res.cert, checked: u.checked, stops: out.stops, block: flat(dnsBlock(u)) };
clearTimeout(domTimer);
// while a certificate is being issued nothing is dropped under it
setDomFields(u, "old.example.org"); await checkDomain(); S.res.cert = null; JOB = "cert";
u.sub = "third"; domainEdited();
console.log(JSON.stringify({ before, same, after, during: [!!u.dns, u.checked] }));
function flat(x) { const o = []; (function w(v) { if (typeof v === "string") o.push(v); else if (Array.isArray(v)) v.forEach(w); else if (v && typeof v === "object") Object.keys(v).sort().forEach((k) => w(v[k])); })(x); return o.join(" | "); }
""")
        self.assertEqual(got["before"][:2], [True, True])
        self.assertEqual(got["same"], [True, True])
        self.assertEqual((got["after"]["dns"], got["after"]["cert"], got["after"]["checked"]), (None, None, ""))
        self.assertIn("dns", got["after"]["stops"])
        self.assertNotIn("смотрит на этот сервер", got["after"]["block"])   # not with someone else's data
        self.assertIn("Пока не видно", got["after"]["block"])
        self.assertEqual(got["during"], [True, "old.example.org"])

    def test_the_answer_for_a_name_no_longer_in_the_field_is_dropped(self):
        got = self.run_js("""
const u = ui("domain");
const p = (async () => { setDomFields(u, "a.example.org"); await checkDomain(); })();
setDomFields(u, "b.example.org");                  // the person typed on while the check went
await p;
console.log(JSON.stringify({ checked: u.checked, certs: out.certs }));
""")
        self.assertEqual(got, {"checked": "", "certs": 0})

    def test_the_limit_time_by_the_clock_of_the_browser(self):
        """§0: the server writes «2026-10-11 04:23 UTC (через 29 ч)» (its clock); the page — Moscow time."""
        got = self.run_js("""
const at = new Date(Date.now() + 29 * 3600 * 1000 + 600 * 1000);
const iso = at.toISOString().slice(0, 19) + "Z", when = iso.slice(0, 16).replace("T", " ") + " UTC (через 29 ч)";
const text = "Let's Encrypt временно не выдаёт сертификаты домену x.org (лимит). Повторить можно " + when + ". Не хотите ждать — возьмите другое имя (например, поддомен vpn2.x.org).";
const res = { ok: false, error: { code: "limit", text, retry_after: iso, retry_text: when } };
const hh = String(at.getHours()).padStart(2, "0") + ":" + String(at.getMinutes()).padStart(2, "0");
console.log(JSON.stringify({ shown: certErrText(res), hh,
  plain: certErrText({ ok: false, error: { code: "dns", text: "DNS не туда" } }),
  foreign: certErrText({ ok: false, error: { code: "limit", text: "другой текст", retry_after: iso, retry_text: when } }) }));
""", env={"TZ": "Europe/Moscow"})
        self.assertNotIn("UTC", got["shown"])
        self.assertIn("%s по вашему времени (через 29 ч)" % got["hh"], got["shown"])
        self.assertIn("возьмите другое имя (например, поддомен vpn2.x.org)", got["shown"])
        self.assertEqual(got["plain"], "DNS не туда")
        self.assertEqual(got["foreign"], "другой текст")

    def test_site_open_only_by_the_fact(self):
        """№4: the record «site: ok» of a pass before ``site remove`` — the card offers to switch the site on; the site
        on in fact — «Сайт открыт» with the template of the node."""
        got = self.run_js("""
const u = ui("domain"); u.name = u.checked = "k3.example.org";
S.res.site = { ok: true, state: "ok", text: "сайт открыт", data: { template: { name: "Sourya Kumar" } } };
S.st = { site: { domain: "k3.example.org", mode: "domain", template: null } }; S.stAt = 50;
const off = flat(siteCard(u)), offOn = siteOn();
S.st.site = { domain: "k3.example.org", mode: "site", template: { id: "gh_x", name: "Kahawa Culture" } };
const on = flat(siteCard(u));
S.st.site.mode = "domain"; S.res.site.at = 60;       // the result of the step came after the state: it is newer
const fresh = siteOn();
S.st = { steps: {} };                                // a server without the field: by the result, as before
const old = siteOn();
console.log(JSON.stringify({ off, offOn, on, fresh, old }));
function flat(x) { const o = []; (function w(v) { if (typeof v === "string") o.push(v); else if (Array.isArray(v)) v.forEach(w); else if (v && typeof v === "object") Object.keys(v).sort().forEach((k) => w(v[k])); })(x); return o.join(" | "); }
""")
        self.assertFalse(got["offOn"])
        self.assertNotIn("Сайт открыт", got["off"])
        self.assertNotIn("Sourya Kumar", got["off"])
        self.assertIn("site-go", got["off"])
        self.assertIn("Сайт открыт", got["on"])
        self.assertIn("Шаблон: Kahawa Culture", got["on"])
        self.assertTrue(got["fresh"])
        self.assertTrue(got["old"])

    def test_one_check_for_every_button(self):
        js = read("setup/setup.js")
        self.assertIn('btn(t("dm.certAgain"), "tertiary", () => checkDomain(), { sm: true, k: "cert-again" })', js)
        # 3.3.3: the fields check themselves (DNS only); the domain goes to the node only with «cert»
        self.assertIn('input("dm-name", u.dom, (v) => { u.dom = v; domainEdited(); }', js)
        self.assertEqual(len(re.findall(r'step\("domain", (\{ name|domBody)', js)), 0)
        self.assertEqual(len(re.findall(r'step\("dns", domBody\(name\)\)', js)), 1)     # 3.3.4: name + zone (REV-334 №4)


class SiteFactTest(Port80Case):
    """№4: the state of the wizard carries the site as it is; after ``site remove`` — not on."""

    def setUp(self):
        super().setUp()
        self.with_domain(issued="le")

    def test_site_now(self):
        self.assertEqual(steps.site_now()["mode"], "domain")
        self.to_site()
        with mock.patch("kaskad.site.templates.status", return_value={"id": "gh_cafe", "source": "x", "site": True,
                                                                     "stub": False}):
            got = steps.site_now()
        self.assertEqual((got["mode"], got["domain"], got["template"]["id"]), ("site", smodel.domain_of(state.load()),
                                                                              "gh_cafe"))
        sops.remove(self.w.runner)
        got = steps.site_now()
        self.assertEqual((got["domain"], got["template"]), (None, None))
        self.assertNotEqual(got["mode"], "site")

    def test_the_state_of_the_page_has_it(self):
        from kaskad.setup import web
        with mock.patch.object(web, "need"), mock.patch.object(steps, "site_now", return_value={"mode": "x"}):
            r = web.api_state(None)
        doc = json.loads(r.body if isinstance(r.body, str) else r.body.decode("utf-8"))
        self.assertEqual(doc["site"], {"mode": "x"})


# ── §0/№2: the words of the limit of Let's Encrypt ──────────────────────────────────────────────────────────────

DUP = ("Error creating new order :: too many certificates (5) already issued for this exact set of identifiers in the "
       "last 168h0m0s, retry after 2026-10-11 04:23:00 UTC: see https://letsencrypt.org/docs/rate-limits/")
FAILED = ("too many failed authorizations (5) for \"x.example.org\" in the last 1h0m0s, retry after 2026-10-10 "
          "01:00:00 UTC")
REG = "too many certificates (50) already issued for \"example.org\" in the last 168h0m0s, retry after 2026-10-11 04:23:00 UTC"


class LimitWordsTest(unittest.TestCase):
    NOW = 1791587580.0           # 2026-10-09 23:13 UTC: 29 h 10 min before the retry

    def setUp(self):
        p = mock.patch.object(acme, "_now", lambda: self.NOW)
        p.start()
        self.addCleanup(p.stop)

    def test_kinds(self):
        self.assertEqual([acme.limit_kind(x) for x in (DUP, FAILED, REG)], ["dup", "failed", "other"])
        self.assertEqual(acme.retry_after(DUP), "2026-10-11T04:23:00Z")

    def test_duplicate_says_another_name_and_the_local_clock(self):
        with zone("Europe/Moscow"):
            text = acme.refusal_text("m6-k3.example.org", "limit", "2026-10-11T04:23:00Z", "dup")
            when = acme.when_text("2026-10-11T04:23:00Z")
        self.assertIn("Повторить можно 11.10, 07:23 MSK (через 29 ч).", text)      # 3.3.2: the date of the language
        self.assertEqual(when, "11.10, 07:23 MSK (через 29 ч)")
        self.assertIn("возьмите другое имя (например, поддомен vpn2.m6-k3.example.org): для нового имени лимит "
                      "отдельный", text)
        self.assertIn("Сервер в порядке", text)
        with zone("UTC"):
            self.assertIn("Повторить можно 11.10, 04:23 UTC (через 29 ч).",
                          acme.refusal_text("m6-k3.example.org", "limit", "2026-10-11T04:23:00Z", "dup"))

    def test_other_limits_do_not_promise_another_name(self):
        with zone("UTC"):
            failed = acme.refusal_text("x.example.org", "limit", "2026-10-10T01:00:00Z", "failed")
            reg = acme.refusal_text("x.example.org", "limit", "2026-10-11T04:23:00Z", "other")
        self.assertIn("неудачных проверок", failed)
        self.assertNotIn("другое имя", failed + reg)
        self.assertIn("Повторить можно 11.10, 04:23 UTC", reg)

    def test_the_wizard_gets_the_part_to_swap(self):
        """The step «cert» passes ``retry_text`` (the exact part of the words) and the kind of the limit."""
        with zone("Europe/Moscow"):
            ref = sops.Refusal("limit", acme.refusal_text("x.org", "limit", "2026-10-11T04:23:00Z", "dup"),
                               "2026-10-11T04:23:00Z", extra={"limit": "dup",
                                                               "retry_text": acme.when_text("2026-10-11T04:23:00Z")})
        with mock.patch.object(sops, "issue", side_effect=ref):
            got = steps.s_cert(steps.Ctx(None, {}, None))
        e = got["error"]
        self.assertEqual((e["code"], e["limit"], e["retry_after"]), ("limit", "dup", "2026-10-11T04:23:00Z"))
        self.assertIn(e["retry_text"], e["text"])


class IssueLimitTest(Port80Case):
    def test_the_refusal_of_certbot_carries_kind_and_time(self):
        self.with_domain()
        self.certbot_present()
        self.certbot.mode = "limit"
        with zone("Europe/Moscow"), self.assertRaises(sops.Refusal) as c:
            sops.issue(self.w.runner)
        doc = c.exception.doc
        self.assertEqual((doc["reason"], doc["limit"]), ("limit", "dup"))
        self.assertRegex(doc["retry_text"], r"^08\.10(\.2026)?, 15:00 MSK", doc)      # the year — when not this one
        self.assertIn(doc["retry_text"], doc["text"])
        self.assertIn("другое имя", doc["text"])


# ── №3: ping through a VLESS exit ───────────────────────────────────────────────────────────────────────────────

class VlessPingTest(TraceCase):
    def test_a_note_on_the_path_through_vless_only(self):
        st = self.world()
        res = self.run_trace(st, D3, "example.com")                     # Ноутбук -> «Вена» (VLESS)
        self.assertEqual(res["verdict"], "exit")
        self.assertIn(trace.VLESS_PING_NOTE, res["notes"])
        self.assertIs(res["icmp"], False)
        self.assertIn("! " + trace.VLESS_PING_NOTE, trace.lines(res))
        self.assertFalse(any(s.get("tone") == "bad" for s in res["steps"]))       # a note, not a failure
        res = self.run_trace(st, D2, "example.com")                     # Дача -> «Amsterdam» (AmneziaWG)
        self.assertNotIn(trace.VLESS_PING_NOTE, res["notes"])
        self.assertNotIn("icmp", res)

    def test_health_checks_do_not_ping(self):
        """The exits are checked by xray (a request through the exit) and by the handshake of AmneziaWG: no ping."""
        import inspect
        for fn in (exits.probe, collect.probe_round):
            self.assertNotRegex(inspect.getsource(fn), r"\bping\b")

    def test_the_panel_and_the_wizard_say_it(self):
        app, i18n = read("app.js"), read("i18n-ru.js")
        self.assertIn(': (isXrayExit(r) && exitOwn(r.id) ? { k: "default", text: t("ex.vlessPing") } : null)', app)
        self.assertIn('"ex.vlessPing": "ping через VLESS-выход не ходит — это нормально: сайты и приложения открываются."',
                      i18n)
        js, si = read("setup/setup.js"), read("setup/setup-i18n-ru.js")
        self.assertIn('if (list.some((e) => e.kind === "xray" || e.kind === "vless")) kids.push(h("p", { class: "qs-small", "data-k": "ex-vless-ping", text: t("ex.vlessPing") }));', js)
        self.assertIn('"ex.vlessPing"', si)
        self.assertIn("ping через VLESS не ходит — сайты открываются", read("graph-i18n-ru.js"))


# ── №5: the template stays ──────────────────────────────────────────────────────────────────────────────────────

class TemplateKeptTest(Port80Case):
    def setUp(self):
        super().setUp()
        self.with_domain(issued="le")

    def status(self, tid="gh_cafe", stub=False, site=True):
        return mock.patch("kaskad.site.templates.status", return_value={
            "id": tid, "source": "https://github.com/x/y", "site": site, "stub": stub, "deployed_at": None})

    def test_on_again_keeps_the_template(self):
        from kaskad.site import templates
        with self.status(), mock.patch.object(templates, "deploy") as dep:
            got = sops.mode(self.w.runner, "site")
        dep.assert_not_called()
        self.assertEqual(got["template"]["id"], "gh_cafe")
        self.assertEqual(self.site()["template_id"], "gh_cafe")
        self.assertIn("шаблон сайта оставлен прежний (gh_cafe)", self.log_text())

    def test_random_explicitly_or_nothing_to_keep(self):
        from kaskad.site import templates
        ok = {"ok": True, "id": "gh_new", "name": "Новый", "source": "s", "fallback": False}
        with self.status(), mock.patch.object(templates, "deploy", return_value=ok) as dep:
            sops.mode(self.w.runner, "site", fresh=True)
        dep.assert_called_once_with(self.w.runner, None)
        sops.mode(self.w.runner, "domain")
        for kw in ({"tid": None, "site": False}, {"tid": templates.STUB_ID, "stub": True}):
            with self.status(**kw), mock.patch.object(templates, "deploy", return_value=ok) as dep:
                sops.mode(self.w.runner, "site")
            dep.assert_called_once_with(self.w.runner, None)
            sops.mode(self.w.runner, "domain")
        with self.status(), mock.patch.object(templates, "deploy", return_value=ok) as dep:
            sops.mode(self.w.runner, "site", template_id="gh_other")
        dep.assert_called_once_with(self.w.runner, "gh_other")

    def test_cli_random_flag(self):
        from kaskad.site import cli as site_cli
        with mock.patch.object(sops, "mode", return_value={"ok": True, "template": {"name": "X"}}) as m:
            self.run_cli("site", "mode", "site")
            self.run_cli("site", "mode", "site", "--random")
        self.assertEqual([c.kwargs.get("fresh") for c in m.call_args_list], [False, True])
        self.assertIsNotNone(site_cli)

    def test_the_menu_enter_keeps_it(self):
        with open(INSTALL, encoding="utf-8") as f:
            sh = f.read()
        body = sh[sh.index("k3_site_enable() {"):]
        body = body[:body.index("\n}\n")]
        self.assertIn("local -a args=()", body)
        self.assertIn('2) args=(--random) ;;', body)

    def run_cli(self, *argv):
        from kaskad import cli
        with mock.patch.object(sys, "stdout"), mock.patch.object(sys, "stderr"):
            try:
                return cli.main(list(argv))
            except SystemExit as e:
                return e.code


# ── №6: why xray refused the key ────────────────────────────────────────────────────────────────────────────────

REAL_PBK = ('Failed to start: main: failed to load config files: [/etc/kaskad/egress/.config.new.json] > infra/conf: '
            'failed to build outbound config with tag out_6 > infra/conf: failed to build stream settings for outbound '
            'detour > infra/conf: Failed to build REALITY config. > infra/conf: invalid "password": bad-pbk-SECRET1')
REAL_SID = REAL_PBK.replace('invalid "password": bad-pbk-SECRET1', 'invalid "shortId": zzzzSECRET2')
OTHER = ('Failed to start: main: failed to load config files: [/etc/kaskad/egress/.config.new.json] > infra/conf: '
         'failed to build outbound config with tag out_2 > infra/conf: unknown transport protocol: xhttp9 for '
         '11111111-2222-3333-4444-555555555555 key AbCdEfGhIjKlMnOpQrStUv /root/.tok/abc')


class XrayWhyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        cls.parser = os.path.join(cls.dir, "egress_parse.py")
        with open(cls.parser, "w", encoding="utf-8") as f:
            f.write(parser_source())

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def why(self, text, lang=None):
        env = dict(os.environ)
        env.pop("KASKAD_LANG", None)
        if lang:
            env["KASKAD_LANG"] = lang                   # install.sh passes the server's language (K3_LANG)
        r = subprocess.run([sys.executable, "-I", self.parser, "xray-why"], input=text.encode("utf-8"),
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.decode("utf-8").strip()

    def test_the_field_never_the_value(self):
        head = "Xray 26.3.27 (Xray, Penetrates Everything.)\nA unified platform for anti-censorship.\n" \
               "2026/10/09 23:00:00.000000 [Info] infra/conf/serial: Reading config: &{Name:/x Format:json}\n"
        pbk, sid = self.why(head + REAL_PBK), self.why(head + REAL_SID)
        self.assertTrue(pbk.startswith("неверное поле publicKey (pbk)"), pbk)
        self.assertTrue(sid.startswith("неверное поле shortId (sid)"), sid)
        for got in (pbk, sid):
            self.assertIn("проверьте в ключе publicKey (pbk) и shortId (sid)", got)
            self.assertNotIn("SECRET", got)

    def test_any_other_cause_without_secrets(self):
        got = self.why(OTHER)
        self.assertTrue(got.startswith("unknown transport protocol: xhttp9"), got)
        for bad in ("11111111-2222", "AbCdEfGhIjKlMnOpQrStUv", "/root/.tok", "/etc/kaskad"):
            self.assertNotIn(bad, got)
        self.assertEqual(self.why(""), "xray не назвал причину")

    def test_in_english(self):
        head = "Xray 26.3.27 (Xray, Penetrates Everything.)\n"
        sid = self.why(head + REAL_SID, "en")
        self.assertTrue(sid.startswith("wrong field shortId (sid) — check publicKey (pbk) and shortId (sid)"), sid)
        other = self.why(OTHER, "en")
        self.assertTrue(other.startswith("unknown transport protocol: xhttp9"), other)
        for got in (sid, other, self.why("", "en")):
            self.assertIsNone(re.search("[А-Яа-яЁё]", got), got)
            self.assertNotIn("SECRET", got)
        self.assertEqual(self.why("", "en"), "xray gave no reason")
        self.assertEqual(self.why("", "de"), "xray не назвал причину")      # another language — Russian, as the menu

    def code(self, text):
        r = subprocess.run([sys.executable, "-I", self.parser, "xray-code"], input=text.encode("utf-8"),
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.decode("utf-8").strip()

    def test_the_cause_as_a_code_for_the_core(self):
        """3.3.2: @K3:egress_fail:xray_invalid:<code> — gw/ops.py says it in its words; never a value of the key."""
        head = "Xray 26.3.27 (Xray, Penetrates Everything.)\n"
        self.assertEqual(self.code(head + REAL_PBK), "field:password:reality")
        self.assertEqual(self.code(head + REAL_SID), "field:shortid:reality")
        self.assertEqual(self.code(OTHER), "other")
        self.assertEqual(self.code(""), "none")
        for text in (REAL_PBK, REAL_SID, OTHER):
            self.assertNotIn("SECRET", self.code(text))
            self.assertRegex(self.code(text), r"^[a-z0-9_:]+$")          # one token of a mark: no spaces, no colons inside

    def test_with_the_real_xray(self):
        from tests import realbin
        xray = realbin.xray_path()
        if not xray:
            self.skipTest("нет xray")
        cfg = {"outbounds": [{"tag": "out_1", "protocol": "vless", "settings": {"vnext": [{"address": "192.0.2.1",
               "port": 443, "users": [{"id": "11111111-2222-3333-4444-555555555555", "encryption": "none"}]}]},
               "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {
                   "serverName": "a.example", "fingerprint": "chrome", "publicKey": "Y_XlU6E0f4Ua1nqO9nO7Xn8xTkVUPkiOJMeDHqhJmmw",
                   "shortId": "zzzzSECRET3"}}}]}
        path = os.path.join(self.dir, "c.json")
        with open(path, "w") as f:
            json.dump(cfg, f)
        r = subprocess.run([xray, "-test", "-c", path], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
        self.assertNotEqual(r.returncode, 0)
        got = self.why(r.stdout.decode("utf-8", "replace"))
        self.assertTrue(got.startswith("неверное поле shortId (sid)"), got)
        self.assertNotIn("SECRET3", got)

    def test_the_rebuild_says_it_and_the_page_shows_it(self):
        with open(INSTALL, encoding="utf-8") as f:
            sh = f.read()
        self.assertIn('$(tf EGRESS_XRAY_INVALID "$(KASKAD_LANG="$K3_LANG" python3 "$EGRESS_PARSE_PY" xray-why < '
                      '"$EGRESS_DIR/test.log"', sh)
        with open(os.path.join(os.path.dirname(INSTALL), "lib/kaskad/i18n/sh/egress.ru.sh"), encoding="utf-8") as f:
            self.assertIn("M_ru_EGRESS_XRAY_INVALID='Конфиг xray невалиден: %s.'", f.read())
        self.assertNotIn('tail -3 "$EGRESS_DIR/test.log"', sh)
        lines = ["[*] Пересобираю пул…",
                 "Конфиг xray невалиден: неверное поле shortId (sid) — проверьте в ключе publicKey (pbk) и shortId "
                 "(sid): их выдаёт сервер VLESS.",
                 "Ключ не добавлен — пул оставлен прежним."]
        got = gops._script_error(lines, "x")
        self.assertIn("неверное поле shortId (sid)", got)
        self.assertIn("пул оставлен прежним", got)


# ── №7: the restore after a reboot — once ───────────────────────────────────────────────────────────────────────

class RestoreOnceTest(AfterRebootTest):
    def test_two_rounds_one_line_one_event(self):
        self.up(30)
        restored = lambda: [e for e in collect.LIVE.events if e.get("kind") == "gw_part_restored"]    # noqa: E731
        before = len(restored())                        # the collector's events live in its process
        self.w.networkd_restart()
        first = collect.tick(self.w.runner)["exits"]
        self.w.networkd_restart()                       # the rules lost again while the legs came up (M6, +10 s)
        second = collect.tick(self.w.runner)["exits"]
        self.assertEqual((first["repaired"], second["repaired"]), (["routing"], ["routing"]))
        self.assertNotIn("hushed", first)
        self.assertEqual(second["hushed"], ["routing"])
        log = self.log()
        self.assertEqual(log.count("GW после перезагрузки: правила шлюза восстановлены (маршрутизация шлюза)"), 1, log)
        self.assertNotIn("GW repair", log)
        self.assertEqual(len(restored()) - before, 1, restored())

    def test_a_new_boot_tells_it_again(self):
        self.assertTrue(boot.first("GW: x"))
        self.assertFalse(boot.first("GW: x"))
        os.unlink(boot.said_path())                     # /run is empty after a reboot
        self.assertTrue(boot.first("GW: x"))
        self.assertEqual(boot.summary(["a", "b"], "NET"), "NET после перезагрузки: правила шлюза восстановлены (a, b)")
        self.assertEqual(boot.summary(["b", "c"], "NET"), "NET после перезагрузки: правила шлюза восстановлены (c)")
        self.assertIsNone(boot.summary(["a"], "NET"))
        self.assertIsNotNone(boot.summary(["a"], "GW"))


# ── №17, №19 of check №2 ────────────────────────────────────────────────────────────────────────────────────────

class UnnamedKeyEverywhereTest(unittest.TestCase):
    def test_overview_and_scheme(self):
        app, graph = read("app.js"), read("graph.js")
        # nav2 r1 (UI-A1, шаг 1): списка выходов на «Обзоре» больше нет (ovExits снята вместе с карточками) — ключ без
        # своего имени проверяется там, где выходы видны: карточка «Выходы › Все выходы» и строка выхода на телефоне
        self.assertIn('subNode: r.unnamed ? null : [exitKindText(r)', app)
        self.assertIn('subTip: r.unnamed ? "" : [exitKindText(r), r.host].filter(Boolean).join(" · "),', app)
        self.assertIn('const exitSub = (r) => (r.unnamed ? (r.source ? r.source.name : "") :', app)
        self.assertIn("var unnamedExit = !!e.where && e.name === e.where;", graph)
        self.assertIn("unnamedExit ? '' : (e.host ||", graph)


from tests.test_gw_vless_front import FrontCase   # noqa: E402 — after the other cases: its import is heavy


class UndoSaysNoMoveTest(FrontCase):
    def test_no_a_record_and_no_old_server_on_undo(self):
        st = state.load()
        st["gateway"]["host"] = "vpn.example.org"
        state.save(st)
        cfg = "/etc/kaskad/config"
        self.write(cfg, 'BOT_TOKEN="123456:AAtokenTOKENtokenTOKEN"\nBOT_CHAT_ID="42"\n')
        snap = backup.take_snapshot(self.w.runner)
        doc = backup.read_snapshot(snap)
        moved = " | ".join(backup.preview(self.w.runner, doc)["warnings"])
        self.assertIn("укажите его A-запись", moved)                       # a real move still says it
        self.assertIn("Один токен бота — один сервер", moved)
        res = backup.undo(self.w.runner, path=snap)
        said = " | ".join(res["warnings"] + res["notes"] + res["text"])
        self.assertNotIn("A-запись", said)
        self.assertNotIn("старом сервере", said)


if __name__ == "__main__":
    unittest.main()
