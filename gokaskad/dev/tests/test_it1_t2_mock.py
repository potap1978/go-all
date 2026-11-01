"""Круг 1, Т2: макет разработчика (static/dev-mock.js) отвечает как сервер на 8а (DELETE gw/groups/<gid>?exits=drop —
policy_ops._group_drop) и №41 (DELETE gw/activity — policy_ops.activity_wipe), включая 409; пресет «Дом» — «Часто»,
как policy.preset_fields ядра. Каждый тест падает на макете до Т2."""

import json
import os
import unittest

from kaskad.gw import policy as P
from tests.test_panel_f0 import HAS_NODE, STATIC
from tests.test_panel_js_contract import node as node_app

HOME, OFFICE = "g-d0d0d0", "g-0ff1ce"


@unittest.skipUnless(HAS_NODE, "нет node")
class MockT2Test(unittest.TestCase):
    def run_mock(self, query, script):
        return node_app("""
globalThis.window = globalThis;
globalThis.location = new URL("http://127.0.0.1/index.html?mock=1&lat=0%s");
require(DATA.mock);
const call = async (method, p) => { const r = await window.KASKAD_MOCK.fetch(p, { method, headers: { "X-Kaskad-Admin": "1" } });
  const j = await r.json(); return [r.status, j]; };
const rev = async () => (await call("GET", "/api/gw/policy"))[1].rev;
%s
process.exit(0);
""" % (query, script), {"mock": os.path.join(STATIC, "dev-mock.js")})

    def test_still_has_no_random_alert(self):
        # сведение круга 1: в режиме проверок (still=1) случайный пример alert не перекрывает тост, который ждёт
        # проверка (test_it1_t2_settings.test_wipe_done падал случайно)
        with open(os.path.join(STATIC, "dev-mock.js"), encoding="utf-8") as f:
            src = f.read()
        i = src.index('(пример сообщения alert)", ts: nowS()')
        self.assertIn("!STILL && Math.random()", src[i - 200:i])

    def test_the_last_russian_group_with_its_keys(self):
        out = self.run_mock("&rug=one", """
const out = {};
const ex0 = (await call("GET", "/api/gw/exits"))[1].exits.filter((e) => e.role === "ru").map((e) => e.id).sort();
out.ex0 = ex0;
out.old = await call("DELETE", "/api/gw/groups/%(home)s?reassign=direct&rev=" + await rev());
out.need = await call("DELETE", "/api/gw/groups/%(home)s?exits=drop&rev=" + await rev());
out.bad = await call("DELETE", "/api/gw/groups/%(home)s?exits=keep&reassign=direct&rev=" + await rev());
out.both = await call("DELETE", "/api/gw/groups/%(home)s?exits=drop&exits_to=main&reassign=direct&rev=" + await rev());
out.abroad = await call("DELETE", "/api/gw/groups/g-5f1a2b?exits=drop&rev=" + await rev());
out.stale = await call("DELETE", "/api/gw/groups/%(home)s?exits=drop&reassign=direct&rev=1");
const r0 = await rev();
out.dry = await call("DELETE", "/api/gw/groups/%(home)s?exits=drop&reassign=direct&dry=1");
out.dryRev = (await rev()) === r0;
out.real = await call("DELETE", "/api/gw/groups/%(home)s?exits=drop&reassign=direct&rev=" + r0);
out.left = (await call("GET", "/api/gw/exits"))[1].exits.filter((e) => e.role === "ru").length;
const db = window.KASKAD_MOCK.db();
out.via = db.users.slice(0, 2).map((u) => u.ru_via);
out.groups = (await call("GET", "/api/gw/policy"))[1].policy.groups.map((g) => g.id);
out.again = await call("DELETE", "/api/gw/groups/%(home)s?exits=drop&reassign=direct&rev=" + await rev());
console.log(JSON.stringify(out));
""" % {"home": HOME})
        self.assertEqual(len(out["ex0"]), 2)
        self.assertEqual((out["old"][0], out["old"][1]["code"]), (409, "ru_last"))
        self.assertEqual(sorted(out["old"][1]["data"]["exits"]), out["ex0"])
        self.assertEqual((out["need"][0], out["need"][1]["code"]), (409, "need"))
        self.assertIn("refs", out["need"][1]["data"])
        self.assertNotIn("exits", out["need"][1]["data"])
        for k in ("bad", "both", "abroad"):
            self.assertEqual((out[k][0], out[k][1]["code"], out[k][1]["data"]), (400, "field", {"field": "exits"}), k)
        self.assertEqual((out["stale"][0], out["stale"][1]["code"]), (409, "policy_changed"))
        self.assertEqual((out["dry"][0], out["dry"][1]["dry"], sorted(out["dry"][1]["drop"])), (200, True, out["ex0"]))
        self.assertTrue(out["dryRev"])
        self.assertEqual(out["dry"][1]["moves"]["ru"]["direct"], out["dry"][1]["moves"]["ru"]["total"])
        self.assertEqual(out["real"][0], 200)
        self.assertEqual(sorted(out["real"][1]["dropped"]), out["ex0"])
        self.assertTrue({"ok", "rev", "moves", "dropped", "refs", "warnings"} <= set(out["real"][1]))
        self.assertEqual(out["left"], 0)
        self.assertEqual(out["via"], ["direct", "direct"])
        self.assertNotIn(HOME, out["groups"])
        self.assertEqual((out["again"][0], out["again"][1]["code"]), (404, "gone"))

    def test_not_the_last_and_subscriptions(self):
        out = self.run_mock("&rug=ok", """
const out = {};
const db = window.KASKAD_MOCK.db();
out.office = await call("DELETE", "/api/gw/groups/%(office)s?reassign=%(home)s&rev=" + await rev());
db.subs.push({ id: "s-0d0d0d", group: "%(home)s", keys: [] });
out.subs = await call("DELETE", "/api/gw/groups/%(home)s?exits=drop&reassign=direct&subs_to=main&rev=" + await rev());
console.log(JSON.stringify(out));
""" % {"home": HOME, "office": OFFICE})
        self.assertEqual((out["office"][0], out["office"][1]["code"]), (409, "need"))     # keys of «Офис»: where to
        self.assertEqual(out["office"][1]["data"]["exits"], ["e-a0a0a0a003"])
        self.assertEqual((out["subs"][0], out["subs"][1]["code"], out["subs"][1]["data"]["uses"]), (409, "need", ["subs"]))

    def test_wipe_the_history(self):
        out = self.run_mock("", """
const out = {};
out.set0 = (await call("GET", "/api/gw/activity/settings"))[1];
out.dry = (await call("DELETE", "/api/gw/activity?dry=1"))[1];
out.still = Object.keys((await call("GET", "/api/gw/activity"))[1].devices).length;
out.real = (await call("DELETE", "/api/gw/activity"))[1];
const sum = (await call("GET", "/api/gw/activity"))[1];
out.after = [Object.keys(sum.devices).length, sum.cover.every((c) => c === 0), sum.activity];
const dev = window.KASKAD_MOCK.db().devices[0].id;
out.series = (await call("GET", "/api/gw/devices/" + dev + "/activity?range=1d"))[1].cover.filter((c) => c).length;
out.set1 = (await call("GET", "/api/gw/activity/settings"))[1];
console.log(JSON.stringify(out));
""")
        self.assertEqual((out["dry"]["dry"], out["dry"]["bytes"]), (True, out["set0"]["bytes"]))
        self.assertGreater(out["still"], 0)
        self.assertEqual((out["real"]["freed"], out["real"]["keep"], out["real"]["bytes"]), (out["set0"]["bytes"], True, 0))
        self.assertEqual(out["real"]["ver"], out["set0"]["ver"])                 # «Хранить» не тронуто
        self.assertEqual(out["after"], [0, True, True])
        self.assertEqual(out["series"], 0)
        self.assertEqual(out["set1"]["since"], None)

    def test_wipe_refusals(self):
        off = self.run_mock("&act=off", 'console.log(JSON.stringify(await call("DELETE", "/api/gw/activity?dry=1")));')
        self.assertEqual((off[0], off[1]["code"]), (409, "activity_off"))
        nosql = self.run_mock("&charts=0", 'console.log(JSON.stringify(await call("DELETE", "/api/gw/activity")));')
        self.assertEqual((nosql[0], nosql[1]["code"]), (409, "nosqlite"))
        none = self.run_mock("&act=none", 'console.log(JSON.stringify(await call("DELETE", "/api/gw/activity")));')
        self.assertEqual(none[0], 404)

    def test_home_checks_often_as_the_core(self):
        out = self.run_mock("&rug=ok", """
const p = (await call("GET", "/api/gw/policy"))[1];
console.log(JSON.stringify({ preset: p.presets.ru.ru_home, home: p.policy.groups.find((g) => g.id === "%s").checks }));
""" % HOME)
        core = P.preset_fields("ru_home", "ru")["checks"]
        self.assertEqual(out["preset"]["freq"]["mode"], core["freq"]["mode"])
        self.assertEqual(out["preset"]["geo"]["expect"], core["geo"]["expect"])
        self.assertEqual(out["home"]["freq"]["mode"], "often")


if __name__ == "__main__":
    unittest.main()
