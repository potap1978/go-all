"""Kaskad 3.3.1, the live check T-M6-qs330e (k3/stand/logs/T-M6-qs330e.md):

    №1  after a change of the domain the wizard says «выданные ссылки VLESS не подключатся — выдайте заново»: the
        warnings of a step (``data.warnings``) are shown by the page as a plate «Внимание», from the result of the job
        (state.job.result — after the move of the wizard to the new name too); ``data.reissue`` — whom, and «Выдать
        заново» goes where the settings are given (the stage «Готово» for the wizard's own device, the panel's
        «Люди и устройства» for others). The other steps that returned warnings silently do so too;
    №2  ``backup undo``: the journal says «BACKUP undo: возвращено — …» and only the parts it brought back (the words of
        «Возвращаю: …» in the console), not «BACKUP import: перенесено — … бот, ключи агентов, пробросы»;
    №3  a refused new name next to a working domain is kept apart (``answers.domain_try``): ``answers.domain`` stays
        the working domain, the field after a reload shows it, the try is a hint «Последняя проверка: … — …».
"""

import json
import re
import shutil
import unittest

from kaskad import backup, paths, state, util
from kaskad.setup import model as smodel_setup
from kaskad.setup import steps
from kaskad.site import dns
from kaskad.site import model
from tests import jsparts
from tests.test_qs330c_live3 import flat, node, read, wizard_scope
from tests.test_qs330d_domain import NEW, SwitchCase
from tests.test_gw_vless_front import FrontCase
from kaskad.site import ops as sops

DOMAIN_RE = re.compile(r"заново", re.I)


def log_text():
    return util.read_text(paths.log()) or ""


# ── №1 (server): the warnings and whom to give the settings again ─────────────────────────────────────────────────

class SwitchWarningsTest(SwitchCase):
    def test_switch_tells_whom_to_reissue(self):
        self.certbot.pem = "other"
        steps.run("domain", {"name": NEW}, self.w.runner)
        res = steps.run("cert", {"name": NEW}, self.w.runner)
        self.assertTrue(res["ok"], res)
        w = " ".join(res["data"]["warnings"])
        self.assertIn("VLESS", w)
        self.assertRegex(w, DOMAIN_RE)                                  # «… выдайте их заново …»
        self.assertIn(self.dev["id"], [x["id"] for x in res["data"]["reissue"]])
        self.assertEqual(len(res["data"]["reissue"]), len(set(x["id"] for x in res["data"]["reissue"])))
        # the domain of the answers is the new working one; the try is gone
        a = smodel_setup.load()["answers"]
        self.assertEqual(a["domain"], NEW)
        self.assertNotIn("domain_try", a)


class SiteWarningsTest(FrontCase):
    def test_site_on_tells_whom_to_reissue(self):
        smodel_setup.update(lambda d: d.update(password_set=True))
        self.assertEqual(self.rec()["security"], "reality")
        self.link(self.dev["id"])                                       # a link of VLESS given out
        res = steps.run("site", {"template": "random"}, self.w.runner)  # the price first (REV-vless-sh3 заметно 4)
        self.assertEqual((res["state"], res["data"].get("need")), ("error", "vless_change"), res)
        self.assertEqual(self.rec()["security"], "reality")
        res = steps.run("site", {"template": "random", "yes": True}, self.w.runner)
        self.assertTrue(res["ok"], res)
        self.assertEqual(self.rec()["security"], "tls")
        self.assertRegex(" ".join(res["data"]["warnings"]), DOMAIN_RE)
        self.assertEqual([x["id"] for x in res["data"]["reissue"]], [self.dev["id"]])


class ReissueRowsTest(unittest.TestCase):
    def test_affected_and_vless_rows_once_each(self):
        r = {"affected": [{"device": "d-00000001", "name": "A", "kind": "sstp"},
                          {"device": "d-00000001", "name": "A", "kind": "ocserv"}],
             "vless": {"changed": True, "reissue": [{"id": "d-00000001", "name": "A"}, {"id": "d-00000002", "name": "B"}]}}
        self.assertEqual(steps._reissue(r), [{"id": "d-00000001", "name": "A"}, {"id": "d-00000002", "name": "B"}])
        # VLESS kept its mode: nobody of VLESS
        r["vless"]["changed"] = False
        self.assertEqual(steps._reissue(r), [{"id": "d-00000001", "name": "A"}])
        self.assertEqual(steps._reissue(None), [])


# ── №3 (server): the try apart from the working domain ───────────────────────────────────────────────────────────

class DomainTryTest(SwitchCase):
    def test_refused_check_keeps_the_working_domain(self):
        from tests.site.sitecase import DOMAIN
        self.no_record()
        steps.run("domain", {"name": NEW}, self.w.runner)
        a = smodel_setup.load()["answers"]
        self.assertEqual(a["domain"], DOMAIN)
        self.assertEqual((a["domain_try"]["name"], a["domain_try"]["state"]), (NEW, "none"))
        # the poll of the page with the same answer writes nothing
        before = util.read_text(smodel_setup.path())
        steps.run("dns", {"name": NEW}, self.w.runner)
        self.assertEqual(util.read_text(smodel_setup.path()), before)
        # the A record appears: the try is «ok», the move may be prepared to it
        self.answers_for[NEW] = {dns.A: ("ok", ["203.0.113.10"]), dns.AAAA: ("ok", [])}
        steps.run("dns", {"name": NEW}, self.w.runner)
        a = smodel_setup.load()["answers"]
        self.assertEqual((a["domain"], a["domain_try"]["state"], a["domain_next"]), (DOMAIN, "ok", NEW))

    def test_refused_certificate_is_a_refused_try(self):
        from tests.site.sitecase import DOMAIN
        self.certbot.mode = "limit"
        steps.run("domain", {"name": NEW}, self.w.runner)
        res = steps.run("cert", {"name": NEW}, self.w.runner)
        self.assertFalse(res["ok"])
        a = smodel_setup.load()["answers"]
        self.assertEqual(a["domain"], DOMAIN)
        self.assertEqual((a["domain_try"]["state"], a["domain_try"]["why"]), ("error", "limit"))
        self.assertNotIn("domain_next", a)
        self.assertEqual(model.domain_of(state.load()), DOMAIN)

    def test_a_domain_set_without_a_working_one_is_the_domain(self):
        st = state.load()
        st["site"] = {}
        state.save(st)
        self.assertFalse(sops.replaces_working(NEW))
        steps.run("domain", {"name": NEW}, self.w.runner)
        a = smodel_setup.load()["answers"]
        self.assertEqual(a["domain"], NEW)
        self.assertNotIn("domain_try", a)


# ── №2: the journal of «backup undo» ─────────────────────────────────────────────────────────────────────────────

class UndoJournalTest(FrontCase):
    def test_undo_journal_names_only_what_came_back(self):
        snap = backup.take_snapshot(self.w.runner)
        said = []
        start = len(log_text())
        res = backup.undo(self.w.runner, path=snap, say=said.append)
        log = log_text()[start:]
        self.assertNotIn("BACKUP import", log)
        back = re.search(r"BACKUP undo: возвращено — (.*)", log)
        self.assertIsNotNone(back, log)
        told = [x[len("Возвращаю: "):-1] for x in said if x.startswith("Возвращаю: ")]
        parts = dict((backup.TITLES[p], backup.SHORT[p]) for p in backup.TITLES)
        self.assertEqual(back.group(1).split(", "), [parts[x] for x in told])
        for word in (backup.SHORT["bot"], backup.SHORT["mcp"], backup.SHORT["forwards"]):
            self.assertNotIn(word, back.group(1))
        self.assertIn("BACKUP undo: шлюз", log)
        self.assertNotIn("ключи из копии", " ".join(said))
        self.assertTrue(res["text"][0].startswith("Возвращено: "), res["text"])

    def test_import_journal_unchanged(self):
        doc = backup.read_snapshot(backup.take_snapshot(self.w.runner))
        start = len(log_text())
        backup.apply(self.w.runner, doc, same_node=True)
        log = log_text()[start:]
        self.assertIn("BACKUP import: перенесено — ", log)
        self.assertNotIn("BACKUP undo", log)


# ── the page: №1 (the plate «Внимание», «Выдать заново»), №3 (the field and the hint) ──────────────────────────────

WARN = ("VLESS перешёл на TLS со своим доменом — выданные раньше ссылки VLESS не подключатся. Выдайте их заново "
        "(меню 17 → 2 → 1 или панель → «Пользователи»): qs3e · Linux.")


def page_scope():
    js = read("setup/setup.js")
    parts = [jsparts.const(js, "jobRunning"), jsparts.function(js, "onJob")]
    i = js.index("ENTER.domain = () => {")
    parts.append("const ENTER = {};\n" + js[i:jsparts.close_of(js, js.index("{", i)) + 1] + ";")
    return wizard_scope() + """
let es = { readyState: 1 };
const pollJob = () => {};
const jobDone = () => {};
const exitsAddDone = () => {};                           // onJob closes the wait of the exits field too (R-333i С1)
S.jobSeen = {};
function flat(x) { const o = []; (function w(v) { if (typeof v === "string") o.push(v); else if (Array.isArray(v)) v.forEach(w); else if (v && typeof v === "object") Object.keys(v).sort().forEach((k) => w(v[k])); })(x); return o.join(" | "); }
%s
""" % "\n".join(parts)


@unittest.skipUnless(shutil.which("node"), "нет node")
class WizardWarningsPageTest(unittest.TestCase):
    def run_js(self, body):
        return node(page_scope() + "\n(async () => {\n" + body + "\n})().catch((e) => { console.error(e.stack); process.exit(1); });")

    def test_cert_warning_from_the_job_with_reissue_in_the_wizard(self):
        got = self.run_js("""
S.st = { answers: { device: "d-0000abcd", domain: "new.example.org", domain_plan: "have" } };
// the page opened anew on the new name: the result comes only from state.job
onJob({ id: "j1", step: "cert", state: "done", result: { ok: true, state: "ok", text: "Домен сменён", data: {
  cert: { names: ["new.example.org"], not_after: "2027-01-08" }, changed_from: "old.example.org",
  warnings: [%s], reissue: [{ id: "d-0000abcd", name: "qs3e · Linux" }] } } }, true);
const u = ui("domain"); u.name = u.checked = "new.example.org"; u.dns = { dns: "ok" };
const c = certCard(u);
const plate = c.kids.find((k) => k && k.note);
plate.btns[0].act();
console.log(JSON.stringify({ text: flat(c), plate, done: U.done, steps: out.steps }));
""" % json.dumps(WARN))
        self.assertIn("Внимание", got["text"])
        self.assertIn("выдайте их заново".lower(), got["text"].lower())
        self.assertEqual(got["plate"]["note"], "warning")
        self.assertEqual([b["btn"] for b in got["plate"]["btns"]], ["Выдать заново"])
        self.assertEqual((got["done"]["tab"], got["done"]["reissue"]), ("connect", True))
        self.assertIn(["open", "done"], got["steps"])

    def test_others_go_to_the_panel(self):
        got = self.run_js("""
S.st = { answers: {} };
const p = warnNote({ ok: true, data: { warnings: [%s], reissue: [{ id: "d-0000beef", name: "Телефон" }] } });
console.log(JSON.stringify(p));
""" % json.dumps(WARN))
        a = got["btns"][0]
        self.assertEqual((a["tag"], a["a"]["href"], a["a"]["target"]), ("a", "/#people?focus=d-0000beef", "_blank"))
        self.assertIn("Выдать заново", flat(a))

    def test_no_plate_without_warnings_and_none_while_running(self):
        got = self.run_js("""
S.st = { answers: {} };
const u = ui("domain"); u.name = u.checked = "new.example.org"; u.dns = { dns: "ok" };
S.res.cert = { ok: true, state: "ok", data: { cert: {}, warnings: [] } };
const a = flat(certCard(u));
S.res.cert = { ok: false, state: "error", error: { text: "лимит" }, data: { warnings: ["x"] } };
const b = flat(certCard(u));
S.res.site = { ok: true, state: "ok", data: { warnings: ["Режим VLESS не переключён: x"] } };
const c = flat(siteCard(u));
console.log(JSON.stringify({ a, b, c }));
""")
        self.assertNotIn("Внимание", got["a"])
        self.assertNotIn("Внимание", got["b"])                         # a refusal speaks for itself
        self.assertIn("Внимание", got["c"])
        self.assertIn("Режим VLESS не переключён", got["c"])

    def test_field_shows_the_working_domain_and_the_try_as_a_hint(self):
        got = self.run_js("""
S.st = { answers: { domain_plan: "have", domain: "m6d.example.org",
  domain_try: { name: "m6e.example.org", state: "none", why: "" } }, steps: {} };
ENTER.domain();
await Promise.resolve();
const u = ui("domain");
const hint = lastTry(u);
u.name = "m6e.example.org";
const same = lastTry(u);
console.log(JSON.stringify({ name: u.name, checked: u.checked, dns: out.steps[0], hint: flat(hint), same }));
""")
        self.assertEqual(got["checked"], "m6d.example.org")
        self.assertEqual(got["dns"], ["dns", {"name": "m6d.example.org", "zone": "example.org"}])
        self.assertIn("Последняя проверка: m6e.example.org — нет A-записи", got["hint"])
        self.assertIn("Проверить снова", got["hint"])
        self.assertIsNone(got["same"])                                 # the same name in the field: the DNS block says it

    def test_reload_splits_the_fields_by_the_saved_zone(self):
        """3.3.4 (REV-334 №4): example.co.nz без поддомена после перезагрузки — поддомен пуст, A-запись «@»; с
        поддоменом — «vpn»; запись до 3.3.4 (без зоны) — по списку зон второго уровня; новая попытка — по своей зоне."""
        got = {}
        for key, answers in (("bare", '{ domain_plan: "have", domain: "example.gen.nz", domain_zone: "example.gen.nz" }'),
                             ("sub", '{ domain_plan: "have", domain: "vpn.example.co.nz", domain_zone: "example.co.nz" }'),
                             ("old", '{ domain_plan: "have", domain: "example.co.nz" }'),
                             ("stale", '{ domain_plan: "have", domain: "vpn.example.com", domain_zone: "other.com" }')):
            got[key] = self.run_js("""
S.st = { answers: %s, steps: {} };
ENTER.domain();
await Promise.resolve();
const u = ui("domain");
console.log(JSON.stringify({ sub: u.sub, dom: u.dom, name: u.name, dns: out.steps[0] }));
""" % answers)
        self.assertEqual((got["bare"]["sub"], got["bare"]["dom"]), ("", "example.gen.nz"))      # gen.nz нет в списке — делит зона
        self.assertEqual(got["bare"]["dns"], ["dns", {"name": "example.gen.nz", "zone": "example.gen.nz"}])
        self.assertEqual((got["sub"]["sub"], got["sub"]["dom"], got["sub"]["name"]), ("vpn", "example.co.nz", "vpn.example.co.nz"))
        self.assertEqual((got["old"]["sub"], got["old"]["dom"]), ("", "example.co.nz"))
        self.assertEqual((got["stale"]["sub"], got["stale"]["dom"]), ("vpn", "example.com"))
        got = self.run_js("""
S.st = { answers: { domain_plan: "have", domain: "m6c.example.org", domain_zone: "example.org",
  domain_try: { name: "example.gen.nz", state: "ok", zone: "example.gen.nz" } }, steps: {} };
JOB = "cert";
ENTER.domain();
const u = ui("domain");
console.log(JSON.stringify({ sub: u.sub, dom: u.dom }));
""")
        self.assertEqual((got["sub"], got["dom"]), ("", "example.gen.nz"))

    def test_switch_in_progress_keeps_the_new_name(self):
        got = self.run_js("""
S.st = { answers: { domain_plan: "have", domain: "m6c.example.org", domain_try: { name: "m6d.example.org", state: "ok" } }, steps: {} };
JOB = "cert";
ENTER.domain();
console.log(JSON.stringify({ name: ui("domain").name }));
""")
        self.assertEqual(got["name"], "m6d.example.org")

    def test_refused_cert_hint_says_the_limit(self):
        got = self.run_js("""
S.st = { answers: { domain: "a.example.org", domain_try: { name: "b.example.org", state: "error", why: "limit" } } };
const u = ui("domain"); u.name = u.checked = "a.example.org";
console.log(JSON.stringify({ hint: flat(lastTry(u)) }));
""")
        self.assertIn("b.example.org — лимит Let's Encrypt, домен не сменён", got["hint"])


class PageSourceTest(unittest.TestCase):
    """The other steps whose warnings the page dropped: the AmneziaWG file, a key of an exit, the step «domain»; the
    stage «Готово» issues again at once with the same input."""

    def test_wired(self):
        js = read("setup/setup.js")
        self.assertIn("resNote(lastRes), jf ? null : warnNote(lastRes)", js)                      # import-file (awg)
        self.assertIn('const add = running(["exits-add"]) ? null : S.res["exits-add"]', js)       # exits-add
        self.assertIn("[lastTry(u), dnsBlock(u)]", js)            # domain (3.3.3: warnings of the step — with «cert»)
        self.assertIn("if (!u.inp && a.issued_kind) u.inp = a.issued_kind;", js)
        self.assertIn("if (u.reissue && u.device) {", js)
        i18n = read("setup/setup-i18n-ru.js")
        for k in ("c.attention", "c.gotIt", "dm.reissue", "dm.reissuePanel", "dm.lastTry", "dm.try_none",
                  "dm.try_elsewhere", "dm.try_cloudflare", "dm.try_wait", "dm.try_ok", "dm.tryLimit", "dm.tryErr"):
            self.assertIn('"%s":' % k, i18n)


if __name__ == "__main__":
    unittest.main()
