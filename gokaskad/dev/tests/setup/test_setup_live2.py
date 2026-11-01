"""Kaskad 3.3.0, the live check №2 (k3/stand/logs/T-M6-qs330b.md), the wizard:

    №10  a subscription by https://<IP> with a certificate of a domain: the add refuses with the domain in a JSON field
         (``cert_domain``, ``same_server``); «Добавить по домену» (the address built from the field) is offered by the
         field of ExitsInput since 3.3.3 (tests/test_333d_ux.py, one with the panel);
    №18  the cards of the mode: the choice lives on the page until «Дальше»; a change of the mode keeps the ticks of the
         stages of the other one (setup.json).

The real functions of setup.js (tests/jsparts.py) run in node against real answers of the steps. Node-less: skipped."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from kaskad.errors import KaskadError
from kaskad.setup import model, steps
from tests import jsparts
from tests.test_qs330_e import WizardCase, read

URL = "https://203.0.113.5:8443/s-%s/all.vpn" % ("Q" * 20)


def node(src):
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(src)
        path = f.name
    try:
        r = subprocess.run(["node", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    finally:
        os.unlink(path)
    if r.returncode:
        raise AssertionError(r.stderr.decode("utf-8", "replace")[-2000:])
    return json.loads(r.stdout.decode("utf-8").strip().splitlines()[-1])


class SubByDomainStepTest(WizardCase):
    def test_add_refusal_names_the_domain_in_json(self):
        err = KaskadError("Сертификат выдан на домен sub.example.org — добавьте подписку по домену или по http.",
                          data={"ok": False, "error": "…", "data": {"cert_domain": "sub.example.org", "same_server": True}})
        with mock.patch("kaskad.setup.features.call", side_effect=err):
            out = steps.run("exits-add", {"text": URL}, self.runner)
        self.assertEqual((out["state"], out["error"]["code"]), ("error", "cert_name"), out)
        self.assertEqual(out["data"], {"cert_domain": "sub.example.org", "same_server": True})
        self.assertNotIn("203.0.113.5", json.dumps(out))
        other = KaskadError("Подписок уже 20 — больше не добавить.", code=3, data={"ok": False, "code": 3})
        with mock.patch("kaskad.setup.features.call", side_effect=other):
            out = steps.run("exits-add", {"text": URL}, self.runner)
        self.assertNotEqual(out["error"]["code"], "cert_name")

    def test_preview_and_add_say_the_same(self):
        from kaskad.gw import subfetch
        named = subfetch.cert_name_error(["sub.example.org"], "203.0.113.5", resolve=lambda h: ["203.0.113.5"])
        with mock.patch.object(subfetch, "check_url", lambda url, resolve=None: ("https", "203.0.113.5", 8443, "/",
                                                                                "203.0.113.5")), \
                mock.patch.object(subfetch, "check_cert", side_effect=named), \
                mock.patch("kaskad.gw.ops.exit_preview", lambda r, t: {"subscription": True, "text": "", "items": []}):
            pre = steps.run("exits-preview", {"text": URL}, self.runner)
        err = KaskadError(str(named), data={"ok": False, "error": str(named), "data": named.data})
        with mock.patch("kaskad.setup.features.call", side_effect=err):
            add = steps.run("exits-add", {"text": URL}, self.runner)
        self.assertEqual((pre["ok"], pre["error"]["code"], pre["text"]), (False, "cert_name", add["text"]))
        self.assertEqual((pre["data"]["cert_domain"], pre["data"]["same_server"]), ("sub.example.org", True))
        self.assertTrue(pre["data"]["subscription"])


class ModeKeepsStagesTest(WizardCase):
    """№18: classic and back — the ticks of «Протоколы», «Выходы», «Дополнительно» stay in setup.json."""

    def test_mode_there_and_back(self):
        model.update(lambda d: d.update(mode="node", current="domain", stages={"import": "done", "protocols": "done",
                                                                               "exits": "skipped", "extras": "done"}))
        out = steps.run("mode", {"mode": "classic"}, self.runner)
        self.assertTrue(out["ok"], out)
        doc = model.load()
        self.assertEqual(doc["stages"], {"import": "done", "protocols": "done", "exits": "skipped", "extras": "done"})
        self.assertEqual([r["kind"] for r in out["data"]["rail"]["stages"]], ["import", "forward", "domain", "done"])
        steps.run("mode", {"mode": "node"}, self.runner)
        rl = model.rail(model.load())
        self.assertEqual(rl["current"], "domain")                          # where the owner was, in both modes
        self.assertEqual(dict((r["kind"], r["state"]) for r in rl["stages"]),
                         {"import": "done", "protocols": "done", "exits": "skipped", "domain": "current", "extras": "done",
                          "done": "ahead"})

    def test_summary_names_the_skipped_of_this_mode_only(self):
        model.update(lambda d: d.update(mode="classic", stages={"import": "done", "exits": "skipped"}))
        with mock.patch("kaskad.gw.ops.exits_view", lambda r: []):
            out = steps.run("summary", {}, self.runner)
        self.assertEqual(out["data"]["skipped"], [])


@unittest.skipUnless(shutil.which("node"), "нет node")
class ModePickJsTest(unittest.TestCase):
    def test_the_card_waits_for_next(self):
        js = read("setup/setup.js")
        parts = [jsparts.const(js, "savedMode")] + [jsparts.function(js, n) for n in ("modePending", "applyMode", "modeCards")]
        doc = node("""
const S = { st: { mode: "node" }, busy: {} }, sent = [];
let loads = 0, renders = 0;
const t = (k, v) => k + (v ? " " + JSON.stringify(v) : "");
const note = (tone, title, text) => ({ tone, title, text });
const choice = (o) => o; const radio = () => null; const chip = () => null;
const h = (tag, a, ...kids) => ({ tag, a, kids: [].concat(...kids) });
const miniClassic = () => null, miniNode = () => null;
const render = () => { renders++; };
const step = async (name, body) => { sent.push([name, body.mode]); S.st.mode = body.mode; return { ok: true }; };
const loadState = async () => { loads++; };
%s
(async () => {
  const r = {};
  // a card picked («classic»): nothing sent, the page says what «Дальше» does
  r.before = modePending();
  modeCards().kids.find((c) => c.k === "mode-classic").pick();      // the real handler of the card
  r.picked = [S.modePick, renders, modeCards().kids.find((c) => c.k === "mode-classic").on];
  r.pend = modePending(); r.sent0 = sent.length;
  S.modePick = "node"; r.back = modePending();           // the saved one again: nothing pending
  S.modePick = "classic";
  await applyMode();
  r.after = { sent, loads, pick: S.modePick, mode: S.st.mode };
  console.log(JSON.stringify(r));
})().catch((e) => { console.error(e); process.exit(1); });
""" % "\n".join(parts))
        self.assertIsNone(doc["before"])
        self.assertEqual(doc["pend"]["main"][1]["title"], 'mode.pendT {"m":"mode.classic"}')
        self.assertEqual(doc["sent0"], 0)
        self.assertEqual(doc["picked"], ["classic", 1, True])               # the card is on, setup.json untouched
        self.assertIsNone(doc["back"])
        self.assertEqual(doc["after"], {"sent": [["mode", "classic"]], "loads": 1, "pick": None, "mode": "classic"})

    def test_the_card_handler_writes_nothing(self):
        js = read("setup/setup.js")
        cards = jsparts.function(js, "modeCards")
        self.assertIn('const pick = (mode) => { if (S.busy.mode) return; S.modePick = mode === savedMode() ? null : mode; render(); };', cards)
        self.assertNotIn("step(", cards)
        for view in ("VIEW.protocols = () => {\n    const pend = modePending();\n    if (pend) return pend;",
                     "VIEW.forward = () => {\n    const pend = modePending();\n    if (pend) return pend;"):
            self.assertIn(view, js)
        self.assertNotIn("delete S.stages[k]", read("setup/dev-mock-setup.js"))


if __name__ == "__main__":
    unittest.main()
