"""«Сейчас» схемы по каждому входу устройства (scheme._now_by) — один ответ с «Куда пойдёт трафик?» и при «Через»: у
устройства с входом xray (VLESS, SOCKS5) и входом ядра (AmneziaWG, WireGuard) путь через ядро — выход по hash
устройства, через xray — любой живой выход. graph.js берёт ответ сервера для выбранного входа, hash сам не считает."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest

from kaskad import state
from kaskad.gw import trace
from tests import jsparts
from tests.test_panel_scheme import SchemeCase

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib", "kaskad", "panel", "static")


def dev(did, name, access, ips=None):
    return {"id": did, "user": "u-00000001", "name": name, "kind": "phone", "platform": "android", "route": None,
            "enabled": True, "added": "2026-10-09T00:00:00Z", "login": name + "-" + did[-4:], "ips": ips or {},
            "access": access}


class NowByInboundTest(SchemeCase):
    def setUp(self):
        super().setUp()
        st = self.scenario()
        g = st["gateway"]
        g["inbounds"]["vless"] = {"enabled": True, "port": 8443}
        g["inbounds"]["socks5"] = {"enabled": True, "port": 1080}
        g["devices"] += [dev("d-00000011", "vless-awg", ["vless", "awg2"], {"awg2": "10.77.3.21"}),
                         dev("d-00000012", "awg-vless", ["awg2", "vless"], {"awg2": "10.77.3.22"}),
                         dev("d-00000013", "socks-wg", ["socks5", "wg"], {"wg": "10.77.1.23"}),
                         dev("d-00000014", "socks", ["socks5"])]
        state.save(st)

    def rows(self):
        g = self.graph()
        return dict((d["id"], d) for n in g["nodes"] if n["type"] == "user" for d in n["data"]["devices"])

    def same(self, st, did, kind, now, why, via):
        res = trace.trace(st, did, "8.8.8.8", inbound=kind, resolver=lambda name: ([], "нет"))
        hl = res["highlight"]
        self.assertEqual(hl["inbound"], kind, why)
        self.assertEqual(hl["balancer"], now["balancer"], why)
        pinned = now["exit"] if not now["balancer"] and (now["target"] or "").startswith("exit:") and via == "pool" \
            else None                                       # a route to one exit: the exit is the target itself
        self.assertEqual(hl["exit"] or None, pinned, why)
        if now["balancer"]:
            self.assertEqual(sorted(hl["exits"]), sorted(now["exits"]), why)
        if res["verdict"] != "exit":
            self.assertEqual(hl["target"], now["target"], why)

    def test_every_inbound_of_a_device_has_the_answer_of_trace(self):
        for dead in ((), (self.vid,), (self.vid, self.aid)):
            self.dead(*dead)
            st = state.load()
            rows = self.rows()
            for did, row in rows.items():
                acc = [k for k in row["access"] if k in ("wg", "awg2", "vless", "socks5")]
                self.assertEqual(sorted(row["now_by"]), sorted(acc), did)
                self.assertEqual(row["now_by"][acc[0]], row["now"], did)        # «now» — the first one, as before
                for k, now in row["now_by"].items():
                    self.assertEqual(now["inbound"], k)
                    self.same(st, did, k, now, (dead, did, k), row["effective"]["via"])
        self.dead()                                                             # every exit alive again
        rows = self.rows()
        va = rows["d-00000011"]["now_by"]
        # stage 1 (SE §2.2, §2.4): xray (VLESS, SOCKS5) and the kernel take the SAME pin of the device in its group
        self.assertEqual(va["vless"]["exit"], va["awg2"]["exit"])
        self.assertFalse(va["vless"]["balancer"] or va["awg2"]["balancer"])
        self.assertIn(va["awg2"]["exit"], (self.vid, self.aid))
        sw = rows["d-00000013"]["now_by"]
        self.assertEqual(sw["socks5"]["exit"], sw["wg"]["exit"])           # stage 1: one pin for every inbound (§2.4)
        self.assertFalse(sw["socks5"]["balancer"] or sw["wg"]["balancer"])

    def test_a_device_that_is_off_has_none(self):
        st = state.load()
        st["gateway"]["devices"][-1]["enabled"] = False
        state.save(st)
        self.assertEqual(self.rows()["d-00000014"]["now_by"], {})

    @unittest.skipUnless(shutil.which("node"), "node не установлен")
    def test_graph_takes_the_answer_of_the_chosen_inbound(self):
        """devPath of graph.js: «Через: AmneziaWG» у телефона [VLESS, AmneziaWG] — выход по hash (ответ сервера), «Через:
        VLESS» — любой живой выход; без ответа сервера для входа (черновик) — прежние правила."""
        self.dead()
        rows = self.rows()
        with open(os.path.join(STATIC, "graph.js"), encoding="utf-8") as f:
            js = f.read()
        parts = [jsparts.function(js, n) for n in ("kindOn", "pathInbound", "liveExits", "isXrayKind", "isVless", "xrayIn", "devPath")]
        script = """
var DATA = %s;
var S = { idx: { node: {}, dev: DATA.devs, exits: DATA.exits.map(function (id) { return { data: { id: id, usable: true } }; }) },
          draft: { devices: DATA.devs }, sim: null, live: { devices: {} } };
['wg', 'awg2', 'vless', 'socks5'].forEach(function (k) { S.idx.node['in:' + k] = { data: { enabled: true } }; });
function dupExit() { return false; }
function effRoute(did) { return S.idx.dev[did].effective; }
function effProfile(did) { return [S.idx.dev[did].profile, S.idx.dev[did].profile_from]; }
function defProfile() { return 'ru_direct'; }
%s
var out = {};
Object.keys(DATA.devs).forEach(function (did) {
  out[did] = {};
  (DATA.devs[did].access || []).forEach(function (k) {
    var P = devPath(did, k);
    out[did][k] = { inbound: P.inbound, target: P.target, exit: P.exit, balancer: P.balancer, exits: P.exits.slice().sort() };
  });
});
var d = JSON.parse(JSON.stringify(DATA.devs['d-00000011'])); delete d.now_by; S.idx.dev['d-00000011'] = d;
out.draft = devPath('d-00000011', 'awg2');
console.log(JSON.stringify(out));
""" % (json.dumps({"devs": rows, "exits": [self.vid, self.aid]}, ensure_ascii=False), "\n".join(parts))
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
            f.write(script)
            path = f.name
        try:
            res = subprocess.run(["node", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        finally:
            os.unlink(path)
        self.assertEqual(res.returncode, 0, res.stderr.decode("utf-8", "replace"))
        out = json.loads(res.stdout.decode("utf-8"))
        for did in ("d-00000011", "d-00000012", "d-00000013", "d-00000014"):
            for k, now in rows[did]["now_by"].items():
                p = out[did][k]
                self.assertEqual(p["inbound"], k)
                self.assertEqual(p["balancer"] or False, now["balancer"] or False, (did, k))
                if not now["balancer"] and (now["target"] or "").startswith("exit:"):
                    self.assertEqual(p["exit"], now["exit"], (did, k))
                if now["balancer"]:
                    self.assertEqual(p["exits"], sorted(now["exits"]), (did, k))
        va = out["d-00000011"]
        self.assertFalse(va["vless"]["balancer"])                          # stage 1: the pin, as the kernel (§2.4)
        self.assertEqual(va["vless"]["exit"], va["awg2"]["exit"])
        self.assertTrue(va["awg2"]["exit"])
        # no answer of the server for this inbound: no guess of its own — stage 1 (§2.4): the answer of another inbound of
        # the device is the same pin, so taking it is no guess
        self.assertIn(out["draft"]["exit"], (None, False, "", va["vless"]["exit"]))
