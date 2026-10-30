# -*- coding: utf-8 -*-
"""Автообновление панели (CPU-аудит 03.10.2026): опросы не накладываются, ручные клики не множатся.

Панель — настоящие index.html и app.js в игрушечном DOM под node (tests/fixtures/panel_dom.js): таймеры двигает
тест, а сервер «медлит» по команде теста (o.hold) — ответ приходит тогда, когда тест его отпустит. Так видно ровно
то, что было на живом сервере: раньше setInterval слал новое обновление каждые 10 с, даже если прежнее (Обзор —
дольше 10 с) ещё шло, и опросы копились. Без node проверки пропускаются, как и test_panel_dom.

Запуск: python3 -m unittest tests/test_panel_polling.py
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

SCENARIOS_JS = r"""
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
// Медленный сервер: ответы на выбранный путь ждут, пока тест их не отпустит.
function slow(path) {
  const pend = [];
  return {
    hold: (m, p) => (m === "GET" && p === path ? new Promise((r) => pend.push(r)) : null),
    release: () => { const n = pend.length; while (pend.length) pend.shift()(); return n; },
    pending: () => pend.length,
  };
}
const n = (P, p) => P.calls("GET", p).length;

(async () => {
  // Долгий ответ «Пользователей»: пока он идёт, автообновление новых запросов не шлёт; следующий — через 10 с
  // после ответа.
  {
    const s = slow("/api/users");
    await scenario("slow_users", { hash: "#users", hold: s.hold }, async (P, rec) => {
      rec.atBoot = n(P, "/api/users");
      await P.tick(35000);
      rec.after35sPending = n(P, "/api/users");
      rec.released = s.release();
      await P.settle(); await P.tick(0);
      rec.shareAfterRelease = n(P, "/api/share");
      await P.tick(9000);
      rec.before10s = n(P, "/api/users");
      await P.tick(1500);
      rec.after10s = n(P, "/api/users");
      s.release(); await P.settle(); await P.tick(0);
      await P.tick(10500);
      rec.next = n(P, "/api/users");
      s.release(); await P.settle();
    });
  }
  // «Обновить» три раза, пока идёт обновление: одно следующее, и оно просит у сервера свежий сбор.
  {
    const s = slow("/api/users");
    await scenario("manual_coalesce", { hash: "#users", hold: s.hold }, async (P, rec) => {
      rec.atBoot = n(P, "/api/users");
      for (let i = 0; i < 3; i += 1) await P.click("#refreshBtn");
      rec.whilePending = n(P, "/api/users");
      s.release(); await P.settle(); await P.tick(0);
      rec.afterRelease = n(P, "/api/users");
      s.release(); await P.settle(); await P.tick(0);
      rec.afterSecond = n(P, "/api/users");
      rec.fresh = P.calls("GET", "/api/users").map((c) => c.headers["X-GoAmnezia-Fresh"] || "");
    });
  }
  // Скрытая вкладка не опрашивает; вернулась — опрос через обычный интервал.
  await scenario("hidden_pause", { hash: "#overview" }, async (P, rec) => {
    rec.atBoot = n(P, "/api/overview");
    P.doc.hidden = true;
    await P.tick(60000);
    rec.hidden60s = n(P, "/api/overview");
    P.doc.hidden = false;
    await P.tick(10500);
    rec.visibleAgain = n(P, "/api/overview");
    // Обзор просит свой ответ и трафик разом — сервер собирает их одним снимком.
    rec.trafficWithOverview = n(P, "/api/traffic");
  });
  // Сбой сервера: интервал растёт вдвое до минуты (10 → 20 → 40 → 60 с), а не бьёт каждые 10 с.
  {
    const times = [];
    const hold = (m, p, h, clock) => { if (m === "GET" && p === "/api/traffic") times.push(clock); return null; };
    await scenario("error_backoff", { hash: "#traffic", hold, api: { "GET /api/traffic": { status: 500, body: { ok: false, error: "boom", code: "transport" } } } },
      async (P, rec) => {
        await P.tick(200000);
        rec.times = times.slice();
      });
  }
  // Поздний ответ раздела, из которого ушли, новый раздел не трогает.
  {
    const s = slow("/api/users");
    await scenario("late_after_switch", { hash: "#users", hold: s.hold }, async (P, rec) => {
      await P.go("#overview");
      rec.pageBefore = P.page();
      s.release(); await P.settle(); await P.tick(0);
      rec.pageAfter = P.page();
      rec.loaderHidden = P.$("#pageLoader") ? P.$("#pageLoader").hidden : true;
    });
  }
  // Сбор на сервере не удался, и он отдал прежнюю картину: над списком — от какого она времени; возраст данных —
  // в подсказке «Обновить».
  {
    const since = "2026-10-03T19:00:05+00:00";
    const users = (d) => ({ ...d, snapshot_at: since, stale: { code: "timeout", message: "мост не ответил", since } });
    await scenario("stale_note", { hash: "#users", data: { "/api/users": users } }, async (P, rec) => {
      const al = P.$$('section[data-page="users"] .gx-al');
      rec.alerts = al.map((n) => n.textContent.replace(/\s+/g, " ").trim());
      rec.title = P.$("#refreshBtn") ? P.$("#refreshBtn").getAttribute("title") : null;
    });
  }
  console.log(JSON.stringify(R));
})().catch((e) => { console.log(JSON.stringify({ __fatal: String((e && e.stack) || e) })); });
"""


@unittest.skipUnless(NODE, "нет node 18+ — панель в игрушечном DOM не проверить")
class PanelPollingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory(prefix="goamnezia-panel-poll-") as tmp:
            script = os.path.join(tmp, "poll.js")
            with open(script, "w", encoding="utf-8") as fh:
                fh.write(SCENARIOS_JS)
            proc = subprocess.run([NODE, script, HARNESS], capture_output=True, text=True, timeout=240, cwd=ROOT)
        lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("{")]
        cls.R = json.loads(lines[-1]) if lines else {"__fatal": "no JSON: rc=%s\n%s\n%s" % (
            proc.returncode, proc.stdout[-2000:], proc.stderr[-2000:])}

    def scen(self, name):
        self.assertNotIn("__fatal", self.R, self.R.get("__fatal"))
        rec = self.R.get(name)
        self.assertIsNotNone(rec, name)
        self.assertNotIn("fatal", rec, rec.get("fatal"))
        self.assertEqual(rec["errors"], [], name)
        return rec

    def test_slow_answer_is_not_overlapped_by_auto_refresh(self):
        r = self.scen("slow_users")
        self.assertEqual(r["atBoot"], 1)
        self.assertEqual(r["after35sPending"], 1)       # прежний setInterval за 35 с послал бы ещё три
        self.assertEqual(r["released"], 1)
        self.assertEqual(r["shareAfterRelease"], 1)     # ссылки — после списка, тем же обновлением
        self.assertEqual(r["before10s"], 1)              # следующий — не раньше 10 с после ответа…
        self.assertEqual(r["after10s"], 2)               # …и ровно один
        self.assertEqual(r["next"], 3)

    def test_manual_clicks_while_loading_make_one_fresh_refresh(self):
        r = self.scen("manual_coalesce")
        self.assertEqual(r["atBoot"], 1)
        self.assertEqual(r["whilePending"], 1)
        self.assertEqual(r["afterRelease"], 2)
        self.assertEqual(r["afterSecond"], 2)
        self.assertEqual(r["fresh"], ["", "1"])

    def test_hidden_tab_does_not_poll(self):
        r = self.scen("hidden_pause")
        self.assertEqual(r["atBoot"], 1)
        self.assertEqual(r["hidden60s"], 1)
        self.assertEqual(r["visibleAgain"], 2)
        self.assertEqual(r["trafficWithOverview"], 2)

    def test_failures_back_off_up_to_a_minute(self):
        r = self.scen("error_backoff")
        self.assertEqual(r["times"], [0, 10000, 30000, 70000, 130000, 190000])

    def test_previous_picture_is_marked_with_its_time(self):
        r = self.scen("stale_note")
        self.assertTrue(any("Данные на" in a and "мост не ответил" in a for a in r["alerts"]), r["alerts"])
        self.assertTrue(r["title"] and r["title"].startswith("Обновить · данные на "), r["title"])

    def test_late_answer_of_a_left_section_does_not_touch_the_new_one(self):
        r = self.scen("late_after_switch")
        self.assertEqual(r["pageBefore"], "overview")
        self.assertEqual(r["pageAfter"], "overview")


if __name__ == "__main__":
    unittest.main()
