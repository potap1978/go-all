# -*- coding: utf-8 -*-
"""Панель: правки CRITICS-FIX-4 (17.10) — в игрушечном DOM (tests/fixtures/panel_dom.js) под node, как
test_critics_fix3_panel.py; без node проверки пропускаются.
  Н-1  окно отзыва страницы называет и профили линий подписки (вместе с подпиской ядро перевыпускает и их);
       карточка подписки после перевыпуска — «прежний адрес до перевыпуска забирали N».
Запуск: python3 -m unittest tests/test_critics_fix4_panel.py
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
const dlgs = (P) => P.$$(".gx-dlg").map((d) => ({ title: txt(d.querySelector(".gx-dlg__title")), text: txt(d),
  checks: d.querySelectorAll('[role="checkbox"]').map((b) => [txt(b), b.getAttribute("aria-checked")]),
  foot: d.querySelectorAll(".gx-dlg__foot button").map(txt) }));
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
  last_fetch: "", last_client: "", lines: [{ line: "3.1", profile: "awg3", has_key: true }, { line: "2.0", profile: "awg2", has_key: true }],
  disabled: false, prev_fetches: 0, prev_last_fetch: "", ...extra });
const PAGE = (id, visits, extra) => ({ id, user: "anna", created: "2026-10-16T10:00:00Z", expires: "2099-01-01T00:00:00Z", forever: false,
  visits, last_visit: "", profiles: ["awg2"], mirror: true, subscription: "created", ...extra });
const api = (user, pages, subs, extra) => ({
  "GET /api/users/anna": ok({ user }),
  "GET /api/users/anna/key": ok({ name: "anna", line: "3.1", min_client: "5.0.1.5", conf: "[Interface]\\nPrivateKey = X\\n", vpn: "vpn://FAKE" }),
  "GET /api/pages": ok({ available: true, pages }),
  "POST /api/action/sub-list": ok({ status: "success", subs, count: subs.length, domain_ready: true, update_interval: 12 }),
  ...extra,
});
const subBox = (P) => txt(P.$$('[data-upages="sub"]')[0] || P.$$(".gx-upages").find((n) => /Подписка|Subscription/.test(txt(n))));

(async () => {
  // Н-1: страница на awg2, подписка отдаёт и awg3 — окно называет оба; с подпиской и ключами — мосту sub=on keys=on
  for (const lang of ["ru", "en"]) {
    const calls = [];
    await scenario(`n1_${lang}`, { hash: "#users", lang, data: PRO, api: api(ANNA(true), [PAGE("p-1a2b", 2)], [SUB({})], {
      "POST /api/action/page-revoke": (body) => { calls.push(body); return ok({ status: "success", id: body.id, user: "anna",
        sub_rotated: true, sub_present: true, keys_reissued: ["awg2", "awg3"], keys_failed: [] }); },
    }) }, async (P, rec) => {
      await P.tick(100);
      await P.click(annaRow(P));
      await P.tick(100);
      const revoke = P.$$('[data-upages="pages"] .gx-upage')[0].querySelectorAll("button").find((b) => /Отозвать|Revoke/.test(txt(b)));
      await P.click(revoke); await P.tick(50);
      rec.d = dlgs(P).pop();
      await P.click(P.$$('.gx-dlg [role="checkbox"]')[1]); await P.tick(20);
      await P.click(P.$$(".gx-dlg__foot button").pop()); await P.tick(100);
      rec.toast = toastText(P);
      rec.calls = calls;
    });
  }
  // Н-1: ключ awg2 страницы — из приложения (на сервере нет), подписка отдаёт awg3 — «только вместе с подпиской»
  await scenario("n1_sub_only", { hash: "#users", lang: "ru", data: PRO, api: api(ANNA(false), [PAGE("p-1a2b", 1)],
    [SUB({ lines: [{ line: "3.1", profile: "awg3", has_key: true }, { line: "2.0", profile: "awg2", has_key: false }] })]) }, async (P, rec) => {
    await P.tick(100);
    await P.click(annaRow(P));
    await P.tick(100);
    const revoke = P.$$('[data-upages="pages"] .gx-upage')[0].querySelectorAll("button").find((b) => /Отозвать/.test(txt(b)));
    await P.click(revoke); await P.tick(50);
    rec.d = dlgs(P).pop();
  });
  // Н-1: карточка подписки после перевыпуска — след прежнего адреса
  for (const [name, extra] of [["prev", { rotated: "2026-10-17T08:17:35Z", generation: 2, prev_fetches: 5, prev_last_fetch: "2026-10-16T21:04:00Z" }],
                               ["prev_none", { rotated: "2026-10-17T08:17:35Z", generation: 2 }]]) {
    await scenario(name, { hash: "#users", lang: "ru", data: PRO, api: api(ANNA(true), [], [SUB(extra)]) }, async (P, rec) => {
      await P.tick(100);
      await P.click(annaRow(P));
      await P.tick(100);
      rec.box = subBox(P);
      rec.tips = P.$$("[title]").map((n) => n.getAttribute("title")).filter((t) => /до перевыпуска/.test(t || ""));
    });
  }
  // Н-2: имя файла .conf — из ответа ядра (file_name); ядро старше поля — прежнее имя
  for (const [name, extra] of [["n2_core", { file_name: "verylon2f2-3_1.conf" }], ["n2_old", {}], ["n2_junk", { file_name: "../x.conf" }]]) {
    await scenario(name, { hash: "#users", lang: "ru", data: PRO, api: {
      ...api(ANNA(true), [], []),
      "GET /api/users/anna/key": ok({ name: "anna", line: "3.1", min_client: "5.0.1.5", conf: "[Interface]\\nPrivateKey = X\\n", vpn: "vpn://FAKE", ...extra }),
    } }, async (P, rec) => {
      await P.tick(100);
      await P.click(annaRow(P));
      await P.tick(100);
      await P.click(P.$$("button").find((b) => txt(b) === "Текст .conf"));
      await P.tick(100);
      rec.fname = txt(P.$$(".gx-conftext__n")[0]);
    });
  }
  console.log(JSON.stringify(R));
})();
"""


@unittest.skipUnless(NODE, "нужен node 18+")
class CriticsFix4PanelTests(unittest.TestCase):
    R = {}

    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory(prefix="goamnezia-cf4-") as tmp:
            script = os.path.join(tmp, "cf4.js")
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

    def test_n1_revoke_window_names_subscription_profiles(self):
        """Н-1: галочка ключей называет профиль страницы и профиль, который отдаёт подписка; включили — sub=on, keys=on;
        итог называет оба перевыпущенных профиля."""
        rec = self.scen("n1_ru")
        self.assertEqual(len(rec["d"]["checks"]), 2, rec["d"]["checks"])
        title = rec["d"]["checks"][1][0]
        self.assertRegex(title, r"на её профилях \(AmneziaWG 2\.0[^)]*\), а вместе с подпиской — и на профилях, которые "
                                r"отдаёт подписка \(AmneziaWG 3\.1[^)]*\)")
        self.assertEqual(rec["calls"], [{"id": "p-1a2b", "sub": "on", "keys": "on"}])
        self.assertRegex(rec["toast"], r"ключи перевыпущены \(AmneziaWG 2\.0[^)]*, AmneziaWG 3\.1")
        en = self.scen("n1_en")
        self.assertIn("together with the subscription, on the profiles the subscription hands out (AmneziaWG 3.1",
                      en["d"]["checks"][1][0])

    def test_n1_subscription_only_keys(self):
        """Н-1: у страницы ключа на сервере нет, у подписки есть — галочка есть и говорит «вместе с подпиской»."""
        rec = self.scen("n1_sub_only")
        self.assertEqual(len(rec["d"]["checks"]), 2, rec["d"]["checks"])
        self.assertRegex(rec["d"]["checks"][1][0],
                         r"на профилях, которые отдаёт подписка \(AmneziaWG 3\.1[^)]*\) — вместе с подпиской")
        self.assertNotIn("2.0", rec["d"]["checks"][1][0])

    def test_n1_card_keeps_old_address_fetches(self):
        """Н-1: после перевыпуска карточка подписки показывает, сколько раз забирали прежний адрес и когда последний раз;
        не забирали — строки нет. П-6-2: подпись короткая, «N · когда», что значит «прежний» — в подсказке."""
        rec = self.scen("prev")
        self.assertRegex(rec["box"], r"Прежний адрес забирали: 5 · ")
        self.assertIn("Забирали: 0", rec["box"])
        self.assertEqual(len(rec["tips"]), 1, rec["tips"])
        self.assertIn("который успели забрать", rec["tips"][0])
        none = self.scen("prev_none")
        self.assertNotIn("Прежний адрес забирали", none["box"])   # З-6-1: та же подпись, что выше
        self.assertEqual(none["tips"], [])

    def test_n2_conf_file_name_comes_from_the_core(self):
        """Н-2: «Текст .conf» и «Скачать .conf» называют файл по file_name ядра; без поля (старое ядро) или с
        негодным значением — прежнее <имя>-<профиль>.conf."""
        self.assertEqual(self.scen("n2_core")["fname"], "verylon2f2-3_1.conf")
        self.assertEqual(self.scen("n2_old")["fname"], "anna-awg3.conf")
        self.assertEqual(self.scen("n2_junk")["fname"], "anna-awg3.conf")


if __name__ == "__main__":
    unittest.main()
