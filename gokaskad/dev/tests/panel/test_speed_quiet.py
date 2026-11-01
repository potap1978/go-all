"""BC5 (SPEC-browser-channel §6.2 п. 0, З-3 круга 2): тишина панели на время замера из браузера — НАСТОЯЩИЕ quietOn,
quietOff, quietHear и startTransport из app.js (вырезаны tests/jsparts.py) в node против заглушек связи:

    * свой замер: связь стоит, у связи «Пауза на проверку», другим вкладкам — speed-pause с until; конец — speed-done,
      связь обратно, только если шла до паузы и вход не показан (выход, 401, «сервер ушёл» за время замера — без неё);
    * чужая пауза из другой вкладки: не дольше 50 с, своё «готово» не шлётся; чужое «готово» при своём замере не в счёт;
    * старт связи во время паузы (загрузка панели, вход) — откладывается до её конца, а не крутит поток событий;
    * страховочный таймер снимает паузу сам; мусор в канале — мимо.

Без node — пропуск."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest

from tests import jsparts

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "lib", "kaskad",
                      "panel", "static")


def read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


def run(script):
    js = read("app.js")
    src = """
const log = [];
let now = 1000000;
const Date = { now: () => now };
let timers = [];
const setTimeout = (fn, ms) => { timers.push({ fn, at: now + ms }); return timers.length; };
const clearTimeout = (id) => { if (id && timers[id - 1]) timers[id - 1].fn = null; };
const fire = (ms) => { now += ms; timers.forEach((x) => { if (x.fn && x.at <= now) { const f = x.fn; x.fn = null; f(); } }); };
const T = { running: false };
let authGate = null;
const S = { conn: "live" };
const setConn = (m) => { S.conn = m; log.push(["conn", m]); };
const stopTransport = () => { T.running = false; log.push(["stop"]); setConn("init"); };
const openSSE = () => log.push(["sse"]);
const startPoll = () => log.push(["poll"]);
const scheduleRefresh = () => log.push(["refresh"]);
const POLL_MS = 5000;
let quietBc = { postMessage: (m) => log.push(["bc", m.type, m.until === undefined ? null : m.until - now]) };
%s
%s
%s
%s
%s
%s
%s
(async () => {
%s
})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });
""" % (jsparts.const(js, "QUIET_MAX_MS"), jsparts.const(js, "QUIET"), jsparts.const(js, "quietSay"),
       jsparts.function(js, "quietOn"), jsparts.function(js, "quietOff"), jsparts.function(js, "quietHear"),
       jsparts.function(js, "startTransport").replace("function startTransport", "function startTransport", 1), script)
    d = tempfile.mkdtemp()
    try:
        p = os.path.join(d, "t.js")
        with open(p, "w", encoding="utf-8") as f:
            f.write(src)
        res = subprocess.run(["node", p], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    if res.returncode != 0:
        raise AssertionError("node: %s" % res.stderr.decode("utf-8", "replace")[-2000:])
    return json.loads(res.stdout.decode("utf-8").strip().splitlines()[-1])


START = '["conn", "init"], ["sse"], ["poll"], ["refresh"]'


@unittest.skipUnless(shutil.which("node"), "нет node: тишину панели не проверить")
class QuietTest(unittest.TestCase):
    def test_own_test_pauses_and_brings_the_link_back(self):
        out = run("""
T.running = true;
quietOn(true, now + 45000);
log.push(["mid", S.conn, T.running, QUIET.on]);
quietHear({ type: "speed-done" });                 // чужое «готово» при своём замере — не в счёт
log.push(["still", QUIET.on]);
quietOff(true);
console.log(JSON.stringify([log, QUIET.on, T.running]));
""")
        log, on, running = out
        self.assertEqual(log, json.loads('[["stop"], ["conn", "init"], ["conn", "test"], ["bc", "speed-pause", 45000], '
                                         '["mid", "test", false, true], ["still", true], %s, ["bc", "speed-done", null]]' % START))
        self.assertEqual([on, running], [False, True])

    def test_no_link_back_if_it_was_off_or_the_login_came(self):
        out = run("""
quietOn(true, now + 45000);                        // связи не было (вход, «сервер ушёл»)
quietOff(true);
log.push(["--"]);
T.running = true;
quietOn(true, now + 45000);
authGate = true;                                   // 401 / выход / «сервер ушёл» за время замера
quietOff(true);
console.log(JSON.stringify([log, T.running]));
""")
        log, running = out
        self.assertNotIn(["sse"], log)
        self.assertEqual(log[-2:], [["conn", "init"], ["bc", "speed-done", None]])
        self.assertFalse(running)

    def test_start_of_the_link_during_the_pause_waits_for_its_end(self):
        out = run("""
quietOn(true, now + 45000);                        // замер начался раньше, чем панель включила связь
startTransport();
log.push(["mid", T.running, QUIET.was]);
quietOff(true);
console.log(JSON.stringify([log, T.running]));
""")
        log, running = out
        self.assertIn(["mid", False, True], log)
        self.assertEqual(log.count(["sse"]), 1)
        self.assertTrue(running)

    def test_a_pause_from_another_tab_is_capped_and_silent(self):
        out = run("""
T.running = true;
quietHear({ type: "speed-pause", until: now + 3600000 });
log.push(["left", QUIET.until - now, QUIET.own]);
fire(49999); log.push(["at49999", QUIET.on]);
fire(1); log.push(["at50000", QUIET.on]);
quietHear(null); quietHear({ type: "x" }); quietHear("speed-done");
console.log(JSON.stringify([log, T.running]));
""")
        log, running = out
        self.assertIn(["left", 50000, False], log)
        self.assertIn(["at49999", True], log)
        self.assertIn(["at50000", False], log)
        self.assertFalse(any(x[0] == "bc" for x in log), log)            # чужая пауза — без своих сообщений
        self.assertTrue(running)

    def test_foreign_done_ends_a_foreign_pause_and_own_test_takes_over(self):
        out = run("""
T.running = true;
quietHear({ type: "speed-pause", until: now + 40000 });
quietHear({ type: "speed-done" });
log.push(["after-done", QUIET.on, T.running]);
quietHear({ type: "speed-pause", until: now + 40000 });
quietOn(true, now + 45000);                         // свой замер поверх чужой паузы
quietHear({ type: "speed-done" });
log.push(["own-still", QUIET.on, QUIET.own]);
fire(45000);                                        // страховка своего замера
log.push(["end", QUIET.on]);
console.log(JSON.stringify(log));
""")
        self.assertIn(["after-done", False, True], out)
        self.assertIn(["own-still", True, True], out)
        self.assertIn(["end", False], out)
        self.assertEqual(out[-2], ["bc", "speed-done", None])

    def test_a_foreign_pause_does_not_shorten_a_longer_one(self):
        out = run("""
quietHear({ type: "speed-pause", until: now + 40000 });
quietHear({ type: "speed-pause", until: now + 5000 });
log.push(["left", QUIET.until - now]);
fire(5001); log.push(["at5001", QUIET.on]);
console.log(JSON.stringify(log));
""")
        self.assertIn(["left", 40000], out)
        self.assertIn(["at5001", True], out)


class QuietWiringTest(unittest.TestCase):
    """Where the pause is checked: the health tile, «the collector is starting» re-read, the agent key poll, the
    connection chip; BroadcastChannel named kaskad-panel; K.quiet exported for the card."""

    def test_wiring(self):
        app = read("app.js")
        self.assertIn('test: ["conn.test", "none", "conn.tip.test"]', app)
        self.assertIn("if (QUIET.on || !liveOff() ||", jsparts.function(app, "hostHealthPull"))
        self.assertIn("if (!QUIET.on) loadQuiet(\"overview\");", jsparts.function(app, "startWatch"))
        self.assertIn('new BroadcastChannel("kaskad-panel")', app)
        self.assertIn("quiet: Object.freeze({ start: (until) => quietOn(true, until), end: () => quietOff(true), on: () => QUIET.on })", app)
        self.assertIn("paused: () => QUIET.on", app)
        self.assertIn("(o.paused && o.paused())", read("ux.js"))
        for lang in ("ru", "en"):
            d = read("i18n-%s.js" % lang)
            self.assertIn('"conn.test":', d)
            self.assertIn('"conn.tip.test":', d)


if __name__ == "__main__":
    unittest.main()
