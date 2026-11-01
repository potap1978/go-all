"""VLESS Ш4 — the wizard (SPEC-vless-tls-default §3.2, §3.7, §4.2 «Мастер», §4.3; HANDOFF-vless §5 М1–М3, Ц4):

    the step ``vless-mode`` — ``gw vless mode`` with ``via``/``who`` «setup»: the foreign input checked before the core
    (mode, site, risks; ``accept_risks``/``yes`` only JSON true), the refusals of the core (``reality_consent``,
    ``vless_change``) handed to the page with their data, ``tls_impossible`` in words;
    ``state.vless`` of the page — the mode, the reason, the recommendation and the text of the risks, no secrets;
    the step ``site`` with devices of VLESS — the price and the action ``yes`` for the page;
    the page (setup.js in node): the card of the site — М1 (turns on by itself with «Дальше»), М2 (the recommendation),
    М3 (the consent through ``vless-mode`` only after the window, «Назад» focused), the price of the site with ``yes``.
"""

import json
import os
import re
import shutil
import unittest
from unittest import mock

from kaskad import state
from kaskad.gw import model
from kaskad.setup import model as smodel_setup
from kaskad.setup import steps
from kaskad.site import model as smodel
from kaskad.site import ops as site_ops
from tests import jsparts
from tests import test_gw_vless_front as F
from tests.test_qs330c_live3 import node, read, wizard_scope
from tests.test_vless_mode import raw
from tests.test_vless_want import WantCase


class _Case(WantCase):
    """FrontCase: VLESS Reality on 443 (``want`` tls), the device «Телефон» with VLESS, the domain with a certificate
    of Let's Encrypt, the site off; the wizard's password set."""

    def setUp(self):
        super().setUp()
        self.put(want="tls")
        smodel_setup.update(lambda d: d.update(password_set=True))

    def no_devices(self):
        st = state.load()
        for d in model.devices(st):
            d["access"] = [k for k in d.get("access") or [] if k != "vless"]
        state.save(st)

    def step(self, body):
        return steps.run("vless-mode", body, self.w.runner)


# ── the step «vless-mode» ────────────────────────────────────────────────────────────────────────────────────────

class VlessModeInputTest(_Case):
    def test_foreign_input_is_refused_before_the_core(self):
        before = raw()
        with mock.patch("kaskad.gw.ops.vless_mode") as core:
            for body, field in (({}, "mode"), ({"mode": "x"}, "mode"), ({"mode": "TLS "}, "mode"),
                                ({"mode": ["tls"]}, "mode"), ({"mode": "reality", "risks": "r1"}, "risks"),
                                ({"mode": "reality", "risks": "R1A"}, "risks"),
                                ({"mode": "reality", "risks": "r1a; rm"}, "risks"),
                                ({"mode": "reality", "site": "a b"}, "site"),
                                ({"mode": "reality", "site": "auto"}, "site"),
                                ({"mode": "reality", "site": "https://x.example.com/p"}, "site"),
                                ({"mode": "reality", "site": "x" * 254}, "site"),
                                ({"mode": "reality", "site": {"a": 1}}, "site")):
                out = self.step(body)
                self.assertEqual(out["state"], "error", body)
                self.assertEqual(out["error"].get("field"), field, (body, out))
            core.assert_not_called()
        self.assertEqual(raw(), before)

    def test_via_who_and_only_true_is_true(self):
        with mock.patch("kaskad.gw.ops.vless_mode", return_value={"ok": True, "security": "reality",
                                                                  "sni": "www.microsoft.com"}) as core:
            self.step({"mode": "reality", "site": "www.microsoft.com", "accept_risks": "true", "risks": "r1a",
                       "yes": 1})
            kw = core.call_args[1]
            self.assertEqual((kw["via"], kw["who"], kw["accept"], kw["yes"], kw["site"], kw["risks"]),
                             ("setup", "setup", False, False, "www.microsoft.com", "r1a"))
            self.step({"mode": "reality", "site": "own", "accept_risks": True, "risks": "r1b", "yes": True})
            kw = core.call_args[1]
            self.assertEqual((kw["accept"], kw["yes"], kw["site"]), (True, True, "own"))
            self.step({"mode": "tls", "site": "www.microsoft.com", "yes": True})
            self.assertEqual((core.call_args[0][1], core.call_args[1]["site"]), ("tls", None))

    def test_the_step_is_long_and_of_the_domain(self):
        s = steps.STEPS["vless-mode"]
        self.assertEqual((s.stage, s.long, s.network, s.secret), ("domain", True, True, False))


class VlessModeCoreTest(_Case):
    def test_reality_consent_goes_to_the_page_then_the_choice(self):
        before = raw()
        out = self.step({"mode": "reality"})
        self.assertEqual((out["state"], out["error"]["code"]), ("error", "conflict"), out)
        d = out["data"]
        self.assertEqual((d["need"], d["risks"], d["site"]), ("reality_consent", "r1a", "www.microsoft.com"))
        self.assertTrue(d["block"] and d["title"] and d["phrase"])
        self.assertEqual(raw(), before)                                          # nothing changed
        # the consent as a string is no consent
        out = self.step({"mode": "reality", "site": d["site"], "accept_risks": "true", "risks": d["risks"]})
        self.assertEqual(out["data"].get("need"), "reality_consent")
        self.assertEqual(raw(), before)
        out = self.step({"mode": "reality", "site": d["site"], "accept_risks": True, "risks": d["risks"], "yes": True})
        self.assertEqual(out["state"], "ok", out)
        rec = self.rec()
        self.assertEqual((rec["security"], rec["want"], rec["sni"]), ("reality", "reality", "www.microsoft.com"))
        c = rec["reality_consent"]
        self.assertEqual((c["via"], c["who"], c["risks"], c["site"]), ("setup", "setup", "r1a", "www.microsoft.com"))
        self.assertNotEqual(smodel.peek(state.load())["mode"], "site")          # the site stays off
        self.assertIn("Reality", out["text"])

    def test_the_risks_of_another_text_are_refused(self):
        before = raw()
        out = self.step({"mode": "reality", "site": "www.microsoft.com", "accept_risks": True, "risks": "r1b",
                         "yes": True})
        self.assertEqual(out["data"].get("need"), "reality_consent", out)
        self.assertEqual(raw(), before)

    def test_tls_asks_the_price_then_yes(self):
        out = self.step({"mode": "tls"})
        self.assertEqual((out["state"], out["error"]["code"]), ("error", "conflict"), out)
        d = out["data"]
        self.assertEqual(d["need"], "vless_change")
        self.assertIn("Телефон", d["text"])
        self.assertEqual([x["id"] for x in d["devices"]], [self.dev["id"]])
        self.assertEqual(self.rec()["security"], "reality")
        out = self.step({"mode": "tls", "yes": 1})                              # «1» is not true
        self.assertEqual(out["data"].get("need"), "vless_change")
        out = self.step({"mode": "tls", "yes": True})
        self.assertEqual(out["state"], "ok", out)
        self.assertEqual((self.rec()["security"], out["data"]["vless"]["security"]), ("tls", "tls"))
        self.assertEqual(smodel.peek(state.load())["mode"], "site")
        self.assertEqual([x["id"] for x in out["data"]["reissue"]], [self.dev["id"]])
        self.assertIn("panel_url", out["data"])                                 # the site came on: the new address
        self.assertIsInstance(out["data"]["warnings"], list)

    def test_tls_impossible_in_words(self):
        self.neighbour()                                                         # 443 held by goAmnezia
        out = self.step({"mode": "tls", "yes": True})
        self.assertEqual((out["state"], out["error"]["code"]), ("error", "tls_impossible"), out)
        self.assertEqual(out["data"]["reason"], "busy_443")
        self.assertIn("443", out["text"])
        self.assertEqual(self.rec()["security"], "reality")

    def test_no_secrets_in_the_answers(self):
        st = state.load()
        rec = model.inbound(st, "vless")
        secrets = [str(rec.get(k)) for k in ("pub", "short_id") if rec.get(k)]
        outs = [self.step({"mode": "reality"}), self.step({"mode": "tls"})]
        text = json.dumps(outs, ensure_ascii=False)
        for s in secrets:
            self.assertNotIn(s, text)
        self.assertNotRegex(text, r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")   # no uuid


# ── state.vless ─────────────────────────────────────────────────────────────────────────────────────────────────

class VlessStateTest(_Case):
    def test_m1_no_devices(self):
        self.no_devices()
        v = steps.vless_now()
        self.assertEqual((v["security"], v["devices"], v["reason"], v["want"], v["legacy"]),
                         ("reality", 0, "no_site", "tls", False))
        self.assertEqual(v["node_domain"], F.DOMAIN)
        self.assertEqual(v["reality_risks"]["ver"], "r1a")
        self.assertEqual(sorted(v["reality_risks"]), sorted(steps._VLESS_RISKS_FIELDS))

    def test_m2_a_copy_with_devices_is_a_recommendation(self):
        st = state.load()
        model.inbound(st, "vless").pop("want", None)                             # the import drops ``want``
        state.save(st)
        v = steps.vless_now()
        self.assertEqual((v["devices"], v["reason"], v["legacy"]), (1, "legacy", True))
        self.assertEqual((v["recommend"]["mode"], v["recommend"]["devices"]), ("tls", 1))
        self.assertTrue(v["recommend"]["text"])

    def test_no_secrets(self):
        st = state.load()
        rec = model.inbound(st, "vless")
        text = json.dumps(steps.vless_now(), ensure_ascii=False)
        for k in ("pub", "short_id", "short_ids", "private"):
            if rec.get(k):
                self.assertNotIn(str(rec[k]), text)
        self.assertNotRegex(text, r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
        self.assertNotIn(self.dev["id"], text)

    def test_none_without_vless_and_in_the_state_of_the_page(self):
        from kaskad.setup import web
        with mock.patch.object(web, "need"):
            doc = json.loads(web.api_state(None).body)
        self.assertEqual(doc["vless"]["security"], "reality")
        self.ok("gw", "inbound", "uninstall", "vless")
        self.assertIsNone(steps.vless_now())
        with mock.patch.object(web, "need"):
            doc = json.loads(web.api_state(None).body)
        self.assertIn("vless", doc)
        self.assertIsNone(doc["vless"])


# ── the step «site» with devices of VLESS: the price and the action for the page ─────────────────────────────────

class SitePriceTest(_Case):
    def test_the_action_says_yes_with_the_same_template(self):
        out = steps.run("site", {"template": "gh_cafe"}, self.w.runner)
        self.assertEqual((out["state"], out["error"]["code"]), ("error", "conflict"), out)
        self.assertEqual(out["data"]["need"], "vless_change")
        self.assertTrue(out["data"]["text"])
        self.assertEqual([x["id"] for x in out["data"]["devices"]], [self.dev["id"]])
        a = out["error"]["action"]
        self.assertEqual((a["step"], a["yes"], a["template"]), ("site", True, "gh_cafe"))
        self.assertTrue(a["label"])


# ── the page (setup.js in node) ─────────────────────────────────────────────────────────────────────────────────

STUBS = (
    # the answer of a step — the one the test puts (the refusals of the core), else ok
    ('const step = async (name, body) => { out.steps.push([name, body]); const r = { ok: true, data: DNS }; S.res[name] = r; return r; };',
     'let STEP_ANSWER = null;\nconst step = async (name, body) => { out.steps.push([name, body]); const r = STEP_ANSWER || { ok: true, data: DNS }; S.res[name] = r; return r; };'),
    # the real ones: the note of a refusal with its action, the chip and the end of the head, «Поставить сайт»
    ('const resNote = (r) => ({ resNote: r && r.text });', ''),
    ('const chip = () => null;', 'const chip = (x, tone) => ({ chip: x, tone });'),
    ('const cardHead = (a, b) => ({ head: a, sub: b });', 'const cardHead = (a, b, o) => ({ head: a, sub: b, end: (o || {}).end || null });'),
    ('const installSite = () => {};', ''),
    ('const btn = (label, kind, act, o) => ({ btn: label, k: (o || {}).k, act });',
     'const btn = (label, kind, act, o) => ({ btn: label, kind, k: (o || {}).k, disabled: !!(o || {}).disabled, act });'),
)


def page_scope():
    js = read("setup/setup.js")
    view = re.search(r"\n  VIEW\.domain = \(\) => \{\n.*?\n  \};\n", js, re.S).group(0)
    scope = wizard_scope()
    for a, b in STUBS:
        assert scope.count(a) == 1, a
        scope = scope.replace(a, b)
    real = "\n".join(jsparts.function(js, n) for n in ("resNote", "actionBtns", "installSite"))
    return scope + real + """
const VIEW = {};
const domainSteps = () => ({ steps: true });
const moveNote = () => null;
const matchMedia = () => ({ matches: false });
const tn = (key, n) => t(key).split("|")[n === 1 ? 0 : n > 1 && n < 5 ? 1 : 2];
let SKIPPED = 0, NEXT = 0;
const skip = () => { SKIPPED++; };
const next = () => { NEXT++; };
function flat(x) { const o = []; (function w(v) { if (typeof v === "string") o.push(v); else if (Array.isArray(v)) v.forEach(w); else if (v && typeof v === "object") Object.keys(v).sort().forEach((k) => w(v[k])); })(x); return o.join(" | "); }
function find(x, pred) { let got = null; (function w(v) { if (got || !v || typeof v !== "object") return; if (pred(v)) { got = v; return; } (Array.isArray(v) ? v : Object.keys(v).map((k) => v[k])).forEach(w); })(x); return got; }
""" + view


@unittest.skipUnless(shutil.which("node"), "нет node")
class WizardPageTest(unittest.TestCase):
    def run_js(self, body):
        return node(page_scope() + "\n(async () => {\n" + body + "\n})().catch((e) => { console.error(e.stack); process.exit(1); });")

    M1 = """
const u = ui("domain"); setDomFields(u, "vpn.example.com"); await checkDomain();
S.res.cert = { ok: true, state: "ok", data: { cert: { names: ["vpn.example.com"] } } };
S.st = { site: { domain: "vpn.example.com", mode: "domain", cert: true }, steps: {},
  vless: { security: "reality", devices: 0, reason: "no_site", forced: false, want: "tls", legacy: false, recommend: null,
    node_domain: "vpn.example.com", reality_risks: { ver: "r1a", site: "www.example.com", need_pick: false, title: "Reality", text: "x", price: "", phrase: "Понимаю риски, выбрать Reality" } } };
out.steps = [];
"""

    def test_m1_turns_on_with_next_and_the_link_goes_through_the_consent(self):
        got = self.run_js(self.M1 + """
const card = siteCard(u), text = flat(card);
const foot = VIEW.domain().foot.next;
await foot.act();
const afterNext = out.steps.slice();
out.steps = [];
// «Не включать сайт»: first vless-mode without the consent, the core answers reality_consent
const link = find(card, (x) => x.k === "site-decline");
STEP_ANSWER = { ok: false, state: "error", error: { code: "conflict", text: "нужно согласие" }, data: { need: "reality_consent",
  block: "Текст\\n- первый риск;\\n- второй риск.\\n\\nХвост", title: "Reality вместо своего сертификата", risks: "r1a", site: "www.example.com", phrase: "Понимаю риски, выбрать Reality" } };
await link.act();
const asked = out.steps.slice(), modal = S.modal;
const body = flat(modal.body()), foot2 = modal.foot(() => { S.modal = null; });
const accept = find(foot2, (x) => x.btn === "Понимаю риски, выбрать Reality"), back = find(foot2, (x) => x.btn === "Назад");
out.steps = [];
STEP_ANSWER = null;
await accept.act();
console.log(JSON.stringify({ text, label: foot.label || "", afterNext, asked, title: modal.title, body, focus: modal.focus,
  backKind: back.kind, acceptKind: accept.kind, sent: out.steps }));
""")
        self.assertIn("включится сам", got["text"])
        self.assertIn("Нужен для VLESS со своим сертификатом.", got["text"])
        self.assertIn("https://vpn.example.com/", got["text"])
        self.assertIn("Не включать сайт", got["text"])
        self.assertEqual(got["afterNext"][0][0], "site")                       # «Дальше» switches the site on first
        self.assertEqual(got["asked"], [["vless-mode", {"mode": "reality"}]])  # no consent before the window
        self.assertEqual(got["title"], "Reality вместо своего сертификата")
        self.assertIn("первый риск", got["body"])
        self.assertEqual(got["focus"], "consent-back")                         # «Назад» focused
        self.assertEqual((got["backKind"], got["acceptKind"]), ("primary", "tertiary"))
        self.assertEqual(got["sent"], [["vless-mode", {"mode": "reality", "site": "www.example.com", "accept_risks": True,
                                                       "risks": "r1a", "yes": True}]])

    def test_m1_not_when_the_person_chose_reality_or_devices_or_no_cert(self):
        got = self.run_js(self.M1 + """
const v = S.st.vless, res = {};
res.m1 = flat(siteCard(u));
v.reason = "chosen"; v.want = "reality";
res.chosen = flat(siteCard(u)); res.chosenFoot = VIEW.domain().foot.next;
v.reason = "no_site"; v.want = "tls"; v.devices = 2;
res.devices = flat(siteCard(u));
v.devices = 0; S.st.site.cert = false; v.reason = "no_cert";
res.nocert = flat(siteCard(u));
S.st.vless = null; S.st.site.cert = true;
res.novless = flat(siteCard(u)); res.novlessFoot = VIEW.domain().foot.next;
console.log(JSON.stringify(res));
""")
        self.assertIn("включится сам", got["m1"])
        for k in ("chosen", "devices", "nocert", "novless"):
            self.assertNotIn("включится сам", got[k], k)
            self.assertNotIn("Не включать сайт", got[k], k)
        self.assertIn("VLESS останется на Reality (выбрано вами)", got["chosen"])
        self.assertFalse(got["chosenFoot"].get("act"))
        self.assertFalse(got["novlessFoot"].get("act"))

    def test_m2_the_recommendation_and_the_price_of_tls(self):
        got = self.run_js(self.M1 + """
Object.assign(S.st.vless, { devices: 9, reason: "legacy", want: null, legacy: true,
  recommend: { mode: "tls", devices: 9, text: "Можно перейти на TLS со своим доменом — выдать заново 9 устройств." } });
const card = siteCard(u), text = flat(card), foot = VIEW.domain().foot.next;
const toTls = find(card, (x) => x.btn === "Перейти на TLS"), keep = find(card, (x) => x.btn === "Оставить Reality");
STEP_ANSWER = { ok: false, state: "error", error: { code: "conflict", text: "цена" }, data: { need: "vless_change", text: "VLESS перейдёт на TLS — выдать заново 9.",
  devices: [1,2,3,4,5,6,7,8,9].map((i) => ({ id: "d-0000000" + i, name: "Телефон " + i })) } };
await toTls.act();
const asked = out.steps.slice(), m = S.modal, body = flat(m.body()), f = m.foot(() => { S.modal = null; });
const go = find(f, (x) => x.btn === "Перейти на TLS");
// DQA-3378: М-5 — кнопки под текстом плашки (не справа), чип «не включён»; М-3 — «Подписки обновятся сами» вторым
// пунктом; «Кому выдать заново» — последним; М3 — значок окна
const plate = find(card, (x) => x.note === "accent"), vb = plate && plate.extra;
const paras = m.body().filter(Boolean);
const extra = { right: plate ? plate.btns.length : -1, under: vb && vb.a.class === "qs-vbtns" ? vb.kids.map((b) => b.btn) : null,
  chip: find(card, (x) => x.chip !== undefined), second: paras[1] && paras[1].a.text, last: paras[paras.length - 1].a.class, icon: m.icon };
out.steps = []; STEP_ANSWER = null;
await go.act();
console.log(JSON.stringify({ text, foot: !!foot.act, asked, title: m.title, body, sent: out.steps, keep: !!keep, extra }));
""")
        self.assertIn("Устройства VLESS из копии уже получили ключи Reality — сайт сам не включается.", got["text"])
        self.assertIn("Можно перейти на TLS", got["text"])
        self.assertNotIn("включится сам", got["text"])
        self.assertTrue(got["keep"])
        self.assertFalse(got["foot"])                                           # the site does not turn on by itself
        self.assertEqual(got["asked"], [["vless-mode", {"mode": "tls"}]])
        self.assertEqual(got["title"], "Перейти на TLS?")
        self.assertIn("Телефон 1", got["body"])
        self.assertIn("Телефон 3", got["body"])
        self.assertNotIn("Телефон 4", got["body"])
        self.assertIn("и ещё 6", got["body"])
        self.assertIn("узел вернёт всё как было", got["body"])                   # Ц4: the undo said before the press
        self.assertEqual(got["sent"], [["vless-mode", {"mode": "tls", "yes": True}]])
        x = got["extra"]
        self.assertEqual((x["right"], x["under"]), (0, ["Перейти на TLS", "Оставить Reality"]))     # М-5: под текстом
        self.assertEqual(x["chip"], {"chip": "не включён"})
        self.assertEqual(x["second"], "Подписки обновятся сами при следующем обновлении в приложении.")    # М-3
        self.assertEqual(x["last"], "qs-vdevs")
        self.assertEqual(x["icon"], "lock")                                                          # М3: значок, без крестика

    def test_back_changes_nothing_and_tls_shows_the_site(self):
        got = self.run_js(self.M1 + """
const link = find(siteCard(u), (x) => x.k === "site-decline");
STEP_ANSWER = { ok: false, state: "error", error: { code: "conflict", text: "согласие" }, data: { need: "reality_consent",
  block: "x", title: "Reality", risks: "r1a", site: "www.example.com", phrase: "Понимаю риски, выбрать Reality" } };
await link.act();
const f = S.modal.foot(() => { S.modal = null; });
out.steps = [];
find(f, (x) => x.k === "consent-back").act();
const afterBack = { modal: S.modal, steps: out.steps.slice() };
// М2 → «Перейти на TLS» with yes: the core switched the site on — the card says «Сайт открыт» with the new address
Object.assign(S.st.vless, { devices: 2, reason: "legacy", legacy: true, recommend: { mode: "tls", devices: 2, text: "Можно. Цена: 2." } });
STEP_ANSWER = { ok: true, state: "ok", text: "VLESS — TLS со своим доменом, порт 443.", data: { vless: { security: "tls", changed: true, port: 443, text: "выдайте заново" },
  reissue: [{ id: "d-00000001", name: "Телефон" }], panel_url: "https://vpn.example.com/app-x/", template: { name: "Кофейня" }, warnings: [] } };
await vlessMode({ mode: "tls", yes: true });
S.st.site.mode = "site"; S.stAt = 0;
const done = flat(siteCard(u));
// not a copy (consent_bad): the recommendation stays, the words «из копии» do not
Object.assign(S.st.vless, { reason: "consent_bad", legacy: false }); S.st.site.mode = "domain"; S.res.site = null;
const bad = flat(siteCard(u));
console.log(JSON.stringify({ afterBack, done, bad }));
""")
        self.assertEqual(got["afterBack"], {"modal": None, "steps": []})
        self.assertIn("Сайт открыт", got["done"])
        self.assertIn("https://vpn.example.com/app-x/", got["done"])
        self.assertIn("Кофейня", got["done"])
        self.assertIn("Перейти на TLS", got["bad"])
        self.assertNotIn("из копии", got["bad"])

    def test_the_price_of_the_site_repeats_the_step_with_yes(self):
        got = self.run_js(self.M1 + """
S.st.vless.devices = 1; S.st.vless.reason = "no_site";
S.res.site = { ok: false, state: "error", step: "site", text: "цена", error: { code: "conflict", text: "VLESS перейдёт на TLS — выдать заново: Телефон.",
  action: { step: "site", yes: true, template: "gh_cafe", label: "Включить сайт и выдать ключи VLESS заново" } },
  data: { need: "vless_change", text: "VLESS перейдёт на TLS — выдать заново: Телефон.", devices: [{ id: "d-00000001", name: "Телефон" }] } };
const card = siteCard(u);
const note = find(card, (x) => x.note);
const act = note.btns.find((b) => b.btn === "Включить сайт и выдать ключи VLESS заново");
act.act();
const m = S.modal, body = flat(m.body()), f = m.foot(() => { S.modal = null; });
const go = find(f, (x) => x.btn === "Включить сайт"), cancel = find(f, (x) => x.btn === "Отмена");
out.steps = [];
await go.act();
console.log(JSON.stringify({ title: m.title, body, sent: out.steps, cancel: !!cancel }));
""")
        self.assertEqual(got["title"], "Включить сайт на домене?")
        self.assertIn("Телефон", got["body"])
        self.assertIn("Кому выдать заново", got["body"])
        self.assertIn("сайт выключится обратно", got["body"])                    # Ц2
        self.assertTrue(got["cancel"])
        self.assertEqual(got["sent"], [["site", {"template": "gh_cafe", "yes": True}]])

    def test_texts_in_both_languages(self):
        ru = read("setup/setup-i18n-ru.js")
        en = read("setup/setup-i18n-en.js")
        keys = ("dm.site.auto", "dm.site.need", "dm.site.decline", "dm.site.import", "dm.site.chosen", "dm.site.l1",
                "dm.site.l2", "dm.site.l3", "dm.site.onQ", "dm.site.onGo", "dm.vl.toTls", "dm.vl.keep", "dm.vl.r2tQ",
                "dm.vl.who", "dm.vl.more", "dm.vl.back", "dm.vl.r2tBack", "dm.site.onBack", "dm.vl.phrase", "dm.vl.subs",
                "dm.site.off")
        for k in keys:
            self.assertIn('"%s"' % k, ru, k)
            self.assertIn('"%s"' % k, en, k)
        self.assertNotRegex(ru + en, r"\.ru\b")

    def test_the_wiring_of_the_page(self):
        js = read("setup/setup.js")
        done = jsparts.function(js, "jobDone")
        self.assertIn('"vless-mode": () => vlessAnswer(res)', done)              # the answer of the long step
        self.assertIn("siteAnswer(res)", done)                                  # the price of the site, М1 «Дальше»
        draw = jsparts.function(js, "draw")
        self.assertIn("S.modal.focus", draw)                                    # «Назад» focused when it opens
        self.assertLess(draw.index("S.modal.focus"), draw.index('button:not(.gx-close)'))
        box = jsparts.function(js, "modal")
        self.assertIn("m.cls", box)
        # DQA-3378 М3: окно-вопрос со значком (согласие, цена) — значок над заголовком и без крестика
        self.assertIn('m.icon ? null : h("button", { type: "button", class: "gx-close"', box)
        self.assertIn('icon: "shield-exclamation"', jsparts.function(js, "askReality"))
        # М1: заголовок карточки — как в макете и ТЗ («Сайт и панель на домене»), в обоих языках
        self.assertIn('"dm.site": "Сайт и панель на домене"', read("setup/setup-i18n-ru.js"))
        self.assertIn('"dm.site": "Site and panel on the domain"', read("setup/setup-i18n-en.js"))
        # Esc and a click outside close the window without an answer (= «Назад»)
        self.assertIn('if (e.key === "Escape")', js)
