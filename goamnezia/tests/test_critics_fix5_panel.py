# -*- coding: utf-8 -*-
"""Панель: правки CRITICS-FIX-5 (17.10) — в игрушечном DOM (tests/fixtures/panel_dom.js) под node, как
test_critics_fix4_panel.py; без node проверки пропускаются.
  З-1  окно отзыва страницы: галочка ключей следит за галочкой подписки — профили подписки только вместе с ней, без
       ключей на профилях страницы галочка недоступна; ядро ничего не перевыпустило — честный тост.
  З-5-1 sub-list не ответил, а подписка есть по снимку страницы — галочка ключей есть и при пустых профилях страницы,
       доступна вместе с подпиской, профили подписки — условно (как pg_revoke_keys_sub_unknown в боте).
Запуск: python3 -m unittest tests/test_critics_fix5_panel.py
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HARNESS = os.path.join(ROOT, "tests", "fixtures", "panel_dom.js")


def node_bin():
    node = shutil.which("node")
    if not node:
        return None
    try:
        out = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=20).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.match(r"v(\d+)\.", out)
    return node if m and int(m.group(1)) >= 18 else None


NODE = node_bin()

JS = r"""
const { boot } = require(process.argv[2]);
const R = {};
async function scenario(name, opts, body) {
  const rec = { errors: [] };
  R[name] = rec;
  let P = null;
  try {
    P = await boot(opts);
    await body(P, rec);
  } catch (e) {
    rec.fatal = String((e && e.stack) || e);
  }
  if (P) { rec.errors = P.errors.slice(); P.close(); }
}
const txt = (n) => (n ? n.textContent.replace(/\s+/g, " ").trim() : null);
const ok = (data) => ({ status: 200, body: { ok: true, data } });
// Галочки окна: [текст, aria-checked, недоступна]
const checks = (P) => { const d = P.$$(".gx-dlg").pop(); return d ? d.querySelectorAll('[role="checkbox"]').map((b) => [txt(b), b.getAttribute("aria-checked"), Boolean(b.disabled)]) : []; };
const box = (P, i) => P.$$('.gx-dlg [role="checkbox"]')[i];
const toastText = (P) => P.$$("#toastHost .gx-toast").map(txt).join(" | ");
const PRO = {
  "/api/overview": (d) => ({ ...d, mode: "pro", domain: "vpn.example.com" }),
  "/api/settings": (d) => ({ ...d, config: { ...d.config, mode: "pro", domain: "vpn.example.com" } }),
};
const annaRow = (P) => P.$$(".gx-urow").find((n) => n.textContent.includes("Анна"));
const PEER = (ip, line, hasKey) => ({ ip, pubkey: "PUB" + ip, created: "2026-09-01T10:00:00Z", active: true, has_key: hasKey, line });
const ANNA = (k2) => ({ name: "anna", label: "Анна", created: "2026-09-02T10:00:00Z", enabled: true, quota_bytes: 0, expires: "",
  traffic: { rx: 0, tx: 0 }, profiles: ["awg3", "awg2"], state: "on", online: false, last_handshake: 0,
  peers: { awg3: PEER("10.8.3.3", "3.1", true), awg2: PEER("10.8.2.3", "2.0", k2) } });
const SUB = (extra) => ({ name: "anna", label: "Анна", created: "2026-10-03T10:00:00Z", rotated: "", generation: 1, fetches: 0,
  last_fetch: "", last_client: "", lines: [{ line: "3.1", profile: "awg3", has_key: true }, { line: "2.0", profile: "awg2", has_key: false }],
  disabled: false, prev_fetches: 0, prev_last_fetch: "", ...extra });
const PAGE = (id, visits, extra) => ({ id, user: "anna", created: "2026-10-16T10:00:00Z", expires: "2099-01-01T00:00:00Z", forever: false,
  visits, last_visit: "", profiles: ["awg2"], mirror: true, subscription: "exists", ...extra });
const api = (user, pages, subs, extra) => ({
  "GET /api/users/anna": ok({ user }),
  "GET /api/users/anna/key": ok({ name: "anna", line: "3.1", min_client: "5.0.1.5", conf: "[Interface]\\nPrivateKey = X\\n", vpn: "vpn://FAKE" }),
  "GET /api/pages": ok({ available: true, pages }),
  "POST /api/action/sub-list": ok({ status: "success", subs, count: subs.length, domain_ready: true, update_interval: 12 }),
  ...extra,
});
async function openRevoke(P) {
  await P.tick(100);
  await P.click(annaRow(P));
  await P.tick(100);
  const revoke = P.$$('[data-upages="pages"] .gx-upage')[0].querySelectorAll("button").find((b) => /Отозвать|Revoke/.test(txt(b)));
  await P.click(revoke); await P.tick(50);
}

(async () => {
  // З-1: ключ awg2 страницы — из приложения (на сервере нет), подписка (не от этой страницы) отдаёт awg3.
  for (const lang of ["ru", "en"]) {
    const calls = [];
    await scenario(`sub_only_${lang}`, { hash: "#users", lang, data: PRO, api: api(ANNA(false), [PAGE("p-1a2b", 1)], [SUB({})], {
      "POST /api/action/page-revoke": (body) => { calls.push(body); return ok({ status: "success", id: body.id, user: "anna",
        sub_rotated: true, sub_present: true, keys_reissued: ["awg3"], keys_failed: [] }); },
    }) }, async (P, rec) => {
      await openRevoke(P);
      rec.start = checks(P);
      await P.click(box(P, 1)); await P.tick(20);          // недоступна — не включается
      rec.keysAlone = checks(P);
      await P.click(box(P, 0)); await P.tick(20);          // подписка — ключи становятся доступны
      rec.subOn = checks(P);
      await P.click(box(P, 1)); await P.tick(20);
      rec.bothOn = checks(P);
      await P.click(box(P, 0)); await P.tick(20);          // подписку сняли — ключи снова недоступны и сняты
      rec.subOff = checks(P);
      await P.click(box(P, 0)); await P.tick(20);
      await P.click(box(P, 1)); await P.tick(20);
      await P.click(P.$$(".gx-dlg__foot button").pop()); await P.tick(100);
      rec.toast = toastText(P);
      rec.calls = calls;
    });
  }
  // З-1: ключ awg2 страницы на сервере, подписка отдаёт и awg3 — без подписки галочка называет только awg2.
  {
    const calls = [];
    await scenario("page_and_sub", { hash: "#users", lang: "ru", data: PRO, api: api(ANNA(true), [PAGE("p-1a2b", 3)],
      [SUB({ lines: [{ line: "3.1", profile: "awg3", has_key: true }, { line: "2.0", profile: "awg2", has_key: true }] })], {
      "POST /api/action/page-revoke": (body) => { calls.push(body); return ok({ status: "success", id: body.id, user: "anna",
        sub_rotated: false, sub_present: true, keys_reissued: ["awg2"], keys_failed: [] }); },
    }) }, async (P, rec) => {
      await openRevoke(P);
      rec.start = checks(P);
      await P.click(box(P, 0)); await P.tick(20);
      rec.subOn = checks(P);
      await P.click(box(P, 0)); await P.tick(20);
      await P.click(box(P, 1)); await P.tick(20);
      await P.click(P.$$(".gx-dlg__foot button").pop()); await P.tick(100);
      rec.calls = calls;
      rec.toast = toastText(P);
    });
  }
  // З-1: ядро ничего не перевыпустило и не провалило — тост говорит, что перевыпускать было нечего.
  for (const lang of ["ru", "en"]) {
    await scenario(`none_${lang}`, { hash: "#users", lang, data: PRO, api: api(ANNA(true), [PAGE("p-1a2b", 1)], [], {
      "POST /api/action/page-revoke": (body) => ok({ status: "success", id: body.id, user: "anna", keys_reissued: [], keys_failed: [] }),
    }) }, async (P, rec) => {
      await openRevoke(P);
      rec.start = checks(P);
      await P.click(box(P, 0)); await P.tick(20);
      await P.click(P.$$(".gx-dlg__foot button").pop()); await P.tick(100);
      rec.toast = toastText(P);
    });
  }
  // З-5-1: sub-list не ответил (500), страница отдаёт подписку (subscription: exists), открывали дважды.
  const SUB_DOWN = { "POST /api/action/sub-list": { status: 502, body: { ok: false, error: "bridge down", code: "bridge_failed" } } };
  for (const [name, lang, k2] of [["unknown_nokey_ru", "ru", false], ["unknown_nokey_en", "en", false], ["unknown_pagekey_ru", "ru", true]]) {
    const calls = [];
    await scenario(name, { hash: "#users", lang, data: PRO, api: api(ANNA(k2), [PAGE("p-1a2b", 2)], [], { ...SUB_DOWN,
      "POST /api/action/page-revoke": (body) => { calls.push(body); return ok({ status: "success", id: body.id, user: "anna",
        sub_rotated: true, sub_present: true, keys_reissued: k2 ? ["awg2", "awg3"] : ["awg3"], keys_failed: [] }); },
    }) }, async (P, rec) => {
      await openRevoke(P);
      rec.start = checks(P);
      await P.click(box(P, 0)); await P.tick(20);          // подписка — ключи подписки становятся доступны
      rec.subOn = checks(P);
      await P.click(box(P, 1)); await P.tick(20);
      rec.bothOn = checks(P);
      await P.click(P.$$(".gx-dlg__foot button").pop()); await P.tick(100);
      rec.toast = toastText(P);
      rec.calls = calls;
    });
  }
  // П-6-1: то же, но ядро старое — keys_reissued в ответе нет (keys_failed нет или пуст). Ключи не перевыпущены,
  // профилей не знаем — тост о провале без списка, как бот, а не «перевыпускать было нечего».
  for (const [name, lang, extra] of [["oldcore_ru", "ru", {}], ["oldcore_en", "en", {}], ["oldcore_emptyfail_ru", "ru", { keys_failed: [] }]]) {
    await scenario(name, { hash: "#users", lang, data: PRO, api: api(ANNA(false), [PAGE("p-1a2b", 2)], [], { ...SUB_DOWN,
      "POST /api/action/page-revoke": (body) => ok({ status: "success", id: body.id, user: "anna", sub_rotated: true, sub_present: true, ...extra }),
    }) }, async (P, rec) => {
      await openRevoke(P);
      await P.click(box(P, 0)); await P.tick(20);
      await P.click(box(P, 1)); await P.tick(20);
      await P.click(P.$$(".gx-dlg__foot button").pop()); await P.tick(100);
      rec.toast = toastText(P);
      rec.danger = P.$$("#toastHost .gx-toast__ic--danger").length > 0;
    });
  }
  console.log(JSON.stringify(R));
})();
"""


@unittest.skipUnless(NODE, "нужен node 18+")
class CriticsFix5PanelTests(unittest.TestCase):
    R = {}

    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory(prefix="goamnezia-cf5-") as tmp:
            script = os.path.join(tmp, "cf5.js")
            with open(script, "w", encoding="utf-8") as fh:
                fh.write(JS)
            proc = subprocess.run([NODE, script, HARNESS], capture_output=True, text=True, timeout=240, cwd=ROOT)
        lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("{")]
        cls.R = json.loads(lines[-1]) if lines else {"__fatal": "no JSON: rc=%s\n%s\n%s" % (proc.returncode, proc.stdout[-2000:], proc.stderr[-2000:])}

    def scen(self, name):
        self.assertNotIn("__fatal", self.R, self.R.get("__fatal"))
        rec = self.R.get(name)
        self.assertIsNotNone(rec, name)
        self.assertNotIn("fatal", rec, rec.get("fatal"))
        self.assertEqual(rec["errors"], [], name)
        return rec

    def test_z1_subscription_keys_need_the_subscription_box(self):
        """З-1: ключи есть только у профиля подписки — без галочки подписки галочка ключей недоступна и говорит
        почему; с подпиской — доступна; подписку сняли — снова недоступна и снята; итог — sub=on, keys=on."""
        rec = self.scen("sub_only_ru")
        sub, keys = rec["start"]
        self.assertEqual(sub[1:], ["false", False])
        self.assertEqual(keys[1:], ["false", True])
        self.assertIn("только вместе с ней", keys[0])
        self.assertIn("AmneziaWG 3.1", keys[0])
        self.assertEqual(rec["keysAlone"][1][1:], ["false", True])
        self.assertEqual(rec["subOn"][1][1:], ["false", False])
        self.assertNotIn("только вместе с ней", rec["subOn"][1][0])
        self.assertEqual(rec["bothOn"][1][1:], ["true", False])
        self.assertEqual(rec["subOff"][1][1:], ["false", True])
        self.assertEqual(rec["calls"], [{"id": "p-1a2b", "sub": "on", "keys": "on"}])
        self.assertRegex(rec["toast"], r"ключи перевыпущены \(AmneziaWG 3\.1")
        en = self.scen("sub_only_en")
        self.assertIn("only together with it", en["start"][1][0])
        self.assertTrue(en["start"][1][2])

    def test_z1_keys_title_follows_the_subscription_box(self):
        """З-1: без подписки галочка ключей называет только профиль страницы, с подпиской — и профиль подписки;
        подписку сняли — мосту sub=off, keys=on."""
        rec = self.scen("page_and_sub")
        title = rec["start"][1][0]
        self.assertIn("на её профилях (AmneziaWG 2.0", title)
        self.assertNotIn("3.1", title)
        self.assertFalse(rec["start"][1][2])
        self.assertRegex(rec["subOn"][1][0], r"вместе с подпиской — и на профилях, которые отдаёт подписка \(AmneziaWG 3\.1")
        self.assertEqual(rec["calls"], [{"id": "p-1a2b", "sub": "off", "keys": "on"}])
        self.assertRegex(rec["toast"], r"ключи перевыпущены \(AmneziaWG 2\.0")

    def test_z1_nothing_reissued_is_said_honestly(self):
        """З-1: ядро вернуло пустые keys_reissued и keys_failed — тост так и говорит, а не молчит."""
        rec = self.scen("none_ru")
        self.assertEqual(len(rec["start"]), 1, rec["start"])
        self.assertIn("перевыпускать было нечего", rec["toast"])
        self.assertIn("nothing was reissued", self.scen("none_en")["toast"])

    def test_z51_unknown_subscription_lines_keep_the_keys_box(self):
        """З-5-1: sub-list не ответил, ключа на профиле страницы нет — галочка ключей всё равно есть: без подписки
        недоступна, с подпиской доступна и условно говорит о профилях подписки; мосту sub=on, keys=on."""
        rec = self.scen("unknown_nokey_ru")
        self.assertEqual(len(rec["start"]), 2, rec["start"])
        sub, keys = rec["start"]
        self.assertEqual(sub[1:], ["false", False])
        self.assertEqual(keys[1:], ["false", True])
        self.assertIn("на профилях, которые отдаёт подписка — вместе с подпиской", keys[0])
        self.assertIn("только вместе с ней", keys[0])
        self.assertNotIn("AmneziaWG", keys[0])
        self.assertEqual(rec["subOn"][1][1:], ["false", False])
        self.assertIn("сейчас узнать не удалось", rec["subOn"][1][0])
        self.assertEqual(rec["bothOn"][1][1:], ["true", False])
        self.assertEqual(rec["calls"], [{"id": "p-1a2b", "sub": "on", "keys": "on"}])
        self.assertRegex(rec["toast"], r"ключи перевыпущены \(AmneziaWG 3\.1")
        en = self.scen("unknown_nokey_en")
        self.assertTrue(en["start"][1][2])
        self.assertIn("on the profiles the subscription hands out — together with the subscription", en["start"][1][0])
        self.assertIn("could not be checked right now", en["subOn"][1][0])
        self.assertEqual(en["calls"], [{"id": "p-1a2b", "sub": "on", "keys": "on"}])

    def test_z51_unknown_subscription_lines_with_page_key(self):
        """З-5-1: sub-list не ответил, ключ на профиле страницы есть — без подписки галочка называет только его,
        с подпиской — и профили подписки (условно, без списка)."""
        rec = self.scen("unknown_pagekey_ru")
        keys = rec["start"][1]
        self.assertEqual(keys[1:], ["false", False])
        self.assertIn("на её профилях (AmneziaWG 2.0", keys[0])
        self.assertNotIn("сейчас узнать не удалось", keys[0])
        on = rec["subOn"][1][0]
        self.assertIn("на её профилях (AmneziaWG 2.0", on)
        self.assertIn("а вместе с подпиской — и на профилях, которые отдаёт подписка", on)
        self.assertIn("сейчас узнать не удалось", on)
        self.assertEqual(rec["calls"], [{"id": "p-1a2b", "sub": "on", "keys": "on"}])
        self.assertRegex(rec["toast"], r"ключи перевыпущены \(AmneziaWG 2\.0, AmneziaWG 3\.1")

    def test_p61_old_core_without_keys_reissued_is_a_failure(self):
        """П-6-1: ответ без keys_reissued (старое ядро), профили подписки неизвестны, на странице ключа нет — тост
        о провале перевыпуска ключей без списка, как бот («Ключи перевыпустить не удалось»), и тревожный."""
        for name in ("oldcore_ru", "oldcore_emptyfail_ru"):
            rec = self.scen(name)
            self.assertIn("ключи перевыпустить не удалось — перевыпустите их в карточке", rec["toast"], name)
            self.assertNotIn("перевыпускать было нечего", rec["toast"], name)
            self.assertTrue(rec["danger"], name)
        en = self.scen("oldcore_en")
        self.assertIn("the keys could not be reissued — reissue them in the card", en["toast"])
        self.assertNotIn("nothing was reissued", en["toast"])


if __name__ == "__main__":
    unittest.main()
