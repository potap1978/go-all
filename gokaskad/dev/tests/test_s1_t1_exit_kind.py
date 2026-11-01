"""Stage 1, T1: the exit through xray is of the kind "xray" with its protocol in ``proto`` (SPEC-stage1-exits §9.3, §7.8);
"vless" of 3.3.5 is read as "xray"; the INBOUND "vless" does not change; the node behaves as in 3.3.5 — the same xray
config and the same nft plan («Как раньше», no restart of xray)."""

import copy
import json
import os
import shutil
import subprocess
import unittest

from kaskad import secrets, state
from kaskad.i18n import tr
from kaskad.gw import collect, exits, model, net, ops, trace, xray
from kaskad.gw.inbounds import vless as vless_in
from kaskad.panel import scheme
from tests import jsparts
from tests.fakes_gw import VLESS_A, VLESS_B, register_dummies, strip_awg_keepalive, write_pool
from tests.gwcase import GwCase

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "gw")
STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib", "kaskad", "panel", "static")


def read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


def as_old(pool):
    """The same pool as kaskad 3.3.5 made it: an xray exit of the kind "vless", no ``proto``, no ``role``."""
    out = copy.deepcopy(pool)
    for e in out:
        if e["kind"] == "xray":
            e["kind"] = "vless"
        e.pop("proto", None)
        e.pop("role", None)
    return out


class ExitKindTest(GwCase):
    def test_pool_and_public(self):
        st = self.scenario(save=False)
        pool = exits.pool(self.w.runner)
        v = next(e for e in pool if e["id"] == self.vid)
        a = next(e for e in pool if e["id"] == self.aid)
        self.assertEqual((v["kind"], v["proto"], v["role"], v["tag"]), ("xray", "vless", "abroad", "gx-" + self.vid))
        self.assertEqual((a["kind"], a["proto"], a["role"]), ("awg", "", "abroad"))
        self.assertEqual(exits.KINDS, ("awg", "xray"))
        pv = exits.public(v, st)
        self.assertEqual((pv["kind"], pv["proto"], pv["role"]), ("xray", "vless", "abroad"))
        self.assertEqual(exits.public(a, st)["proto"], "")
        old = as_old([v])[0]                               # a row of 3.3.5 (live.json, the API of an older node)
        self.assertTrue(exits.is_xray(old) and exits.parsed(old))
        self.assertEqual((exits.public(old, st)["kind"], exits.public(old, st)["proto"]), ("xray", "vless"))
        self.assertEqual(exits.kind_title(old), "VLESS")
        self.assertEqual(exits.kind_title(v), "VLESS")
        self.assertIn("VLESS", v["where"])
        self.assertFalse(exits.parsed({"kind": "bad"}))
        self.assertEqual(exits.kind_title({"kind": "bad", "line": "trojan://x@h:1"}), "Trojan")

    def test_preview_row(self):
        rows = exits.preview(exits.load_parser(self.w.runner), VLESS_B)
        self.assertEqual((rows[0]["kind"], rows[0]["proto"], rows[0]["title"]), ("xray", "vless", "VLESS"))

    def test_protocol_titles_and_server(self):
        self.assertEqual([exits.link_proto(x) for x in ("vless://a@b:1", "hy2://a@b:1", "hysteria2://a@b:1",
                                                         "ss://YQ@b:1", "trojan://a@b:1", "vmess://e30", "awg://x", "")],
                         ["vless", "hysteria2", "hysteria2", "shadowsocks", "trojan", "vmess", "", ""])
        self.assertEqual(exits.key_title("xray", proto="hysteria2"), "Hysteria2")
        self.assertEqual(exits.key_title("vless"), "VLESS")
        obs = {"vless": {"settings": {"vnext": [{"address": "a.example.com", "port": 443}]}},
               "trojan": {"settings": {"servers": [{"address": "t.example.com", "port": 8443}]}},
               "hysteria": {"settings": {"address": "h.example.com", "port": 9443}}}
        self.assertEqual(dict((k, exits.outbound_server(v)) for k, v in obs.items()),
                         {"vless": ("a.example.com", 443), "trojan": ("t.example.com", 8443),
                          "hysteria": ("h.example.com", 9443)})
        self.assertEqual(exits.outbound_server(None), (None, None))
        self.assertEqual(exits.outbound_server({"settings": {}}), (None, None))
        e = {"kind": "xray", "proto": "trojan", "outbound": obs["trojan"]}
        self.assertEqual(xray._server_name(e), "t.example.com")          # no vnext: still resolved locally
        self.assertIn("t.example.com", xray.local_names([e]))

    def test_inbound_vless_untouched(self):
        self.assertEqual(model.XRAY_KINDS, ("socks5", "vless", "hy2"))     # T12: + the inbound Hysteria2
        self.assertIn("vless", model.INBOUND_KINDS)
        self.assertEqual(vless_in.VlessInbound.kind, "vless")

    def test_xray_config_and_nft_plan_as_in_335(self):
        """«Как раньше»: the rows of the new kind give the very config and plan of the rows of 3.3.5 — no restart."""
        st = self.scenario(save=False)
        write_pool([VLESS_A, VLESS_B] + [x for x in [exits.pool_lines()[1]]])
        pool = exits.pool(self.w.runner)
        self.assertEqual(sum(1 for e in pool if e["kind"] == "xray"), 2)
        sec = secrets.load()
        self.assertEqual(xray.render(st, sec, pool), xray.render(st, sec, as_old(pool)))
        self.assertEqual(sorted((e["mark"], exits.tproxy_port(e)) for e in exits.xray_exits(st, pool)),
                         sorted((e["mark"], exits.tproxy_port(e)) for e in exits.xray_vless(st, as_old(pool))))
        # the reference of 3.3.5 (REV-s1-t5 С1: its render is for this reference only) — the same for both forms too
        self.assertEqual(xray._render_335(st, sec, pool), xray._render_335(st, sec, as_old(pool)))
        bal = dict((b["tag"], b) for b in xray._render_335(st, sec, pool)["routing"]["balancers"])
        self.assertEqual(sorted(bal["gw-vless"]["selector"]), sorted(e["tag"] for e in pool if e["kind"] == "xray"))
        # stage 1 (SPEC-stage1-exits §1.3 отличие 2, §2.8): the devices are pinned (0x300+N), nobody goes through
        # gw-vless without «по соединениям» — its selector is the prefix gx- (a change of the pool changes no balancer)
        bal = dict((b["tag"], b) for b in xray.render(st, sec, pool)["routing"]["balancers"])
        self.assertEqual(bal["gw-vless"]["selector"], [exits.ABROAD_PREFIX])
        st["gateway"]["inbounds"] = {}                         # no xray inbound: the xray exits alone need xray
        self.assertTrue(ops._needs_xray(st, self.w.runner, pool))
        self.assertFalse(ops._needs_xray(st, self.w.runner, [e for e in pool if e["kind"] == "awg"]))

    def test_node_335_byte_for_byte(self):
        """«Как раньше» against the code of 3.3.5 itself (REV-s1-t1 П-3): the xray config (with SOCKS5 users on the pool
        and on an exit), the nft plan, the internal ports, the marks and the tags of a node without settings — 2 VLESS +
        AWG + a broken key — are those the code before T1 (1a31a8d) made: tests/fixtures/gw/s1_t1_node_335.json."""
        with open(os.path.join(FIXTURES, "s1_t1_node_335.json"), encoding="utf-8") as f:
            want = json.load(f)
        st = self.scenario(save=True)
        write_pool([VLESS_A, VLESS_B] + exits.pool_lines()[1:] + ["vless://broken"])
        pool = exits.pool(self.w.runner)
        sec = secrets.load()
        # the xray of 3.3.5 — its own render, kept for this reference alone (REV-s1-t5 С1: no caller draws it)
        got = {"xray": xray._render_335(st, sec, pool), "marks": exits.marks(st, pool, set(e["id"] for e in pool)),
               "tags": [e.get("tag") for e in pool]}
        # the ports: those of 3.3.5 all stay; stage 1 (SPEC-stage1-exits §1.3: «без влияния на маршрут»; §5.2) adds
        # exactly the node's own inbounds of its config — the API 10854, the pool inbound 12699, probe-<id> of the
        # xray exits (12700+N) — the ports gw_xports closes to the users of the node
        ports = sorted(xray.internal_ports(st, pool))
        old = [tuple(x) for x in want["ports"]]
        self.assertEqual([x for x in ports if x in old], old)
        cfg = xray.render(st, sec, pool)
        self.assertEqual(sorted(set(p for pr, p, _ in ports if (pr, p) not in [(a, b) for a, b, _ in old])),
                         sorted(set(xray.guarded_ports(cfg)) - set(p for _, p, _ in old)))
        got["ports"] = old
        p = net.plan(st, self.w.runner)
        self.assertEqual(p.pop("ruonly"), [])        # stage 1 (T4, §2.3): gw_ruonly — empty on a node without settings
        self.assertEqual(p.pop("xray_ports"), xray.guarded_ports(cfg))     # stage 1 (§5.2): gw_xports, by the render
        # PLAN-multi-ip Ш3/Ш4: the plan of the addresses — nothing chosen, nothing drawn (REV-mip-s34 Б-1: no Н4 either)
        self.assertEqual(p.pop("mip"), {"in": None, "snat": None, "legs": [], "xsrc": {}, "gone_src": {}})
        got["plan"] = dict((k, p[k]) for k in sorted(p) if k != "uid")
        register_dummies(("socks5", {"proto": "tcp", "default_port": 1080, "ifaces": [], "xray_inbound": {
            "tag": "socks-in", "listen": "0.0.0.0", "port": 1080, "protocol": "socks"}}))
        st["gateway"]["inbounds"]["socks5"] = {"enabled": True, "port": 1080}
        for i, d in enumerate(st["gateway"]["devices"]):
            d["access"].append("socks5")
            d["route"] = [None, {"via": "exit:" + self.vid, "fail": "direct"},
                          {"via": "exit:" + self.vid, "fail": "block"}][i % 3]
        got["xray_socks"] = xray._render_335(st, sec, pool)
        for k in ("xray", "xray_socks"):                # Н3: the keepalive of the AWG outbounds — the one change
            self.assertEqual(strip_awg_keepalive(got[k]), 1, k)
        self.assertEqual(json.loads(json.dumps(got, sort_keys=True, default=str)), want)

    def test_now_of_balancer(self):
        st = self.scenario(save=False)
        pool = [e for e in exits.pool(self.w.runner) if e["kind"] == "xray"]
        alive = set(e["id"] for e in pool)
        dev = {"id": "d-00000001", "route": None, "user": "u-00000001"}
        n = exits.now_of(st, dev, pool, alive)
        # stage 1 (SE §2.3, difference 2): the xray exits of the pool by the device — its own exit, no balancer 0x200;
        # a row of 3.3.5 (kind "vless") is the same exit
        self.assertEqual((n["target"], n["balancer"]), ("exit:" + n["exit"], False))
        self.assertEqual(exits.now_of(st, dev, as_old(pool), alive)["exit"], n["exit"])
        t = exits.target(st, dev, pool, alive)
        self.assertEqual(t["mark"], exits.VLESS_MARK_BASE + next(e["index"] for e in pool if e["id"] == n["exit"]))

    def test_mark_name_by_protocol(self):
        self.assertEqual(collect.mark_name(0x302, [VLESS_B, VLESS_A]), "VLESS 2")
        self.assertEqual(collect.mark_name(0x302), "выход 2")               # the line not known: no protocol made up
        self.assertEqual(collect.mark_name(0x301, ["hy2://a@b:1"]), "Hysteria2 1")
        self.assertIn("xray-выход", tr("gw_trace.note.vless_ping"))        # §9.3: not «VLESS-выход» (REV П-4)


class XrayKindChecksTest(GwCase):
    """REV-s1-t1 З-2: the checks of the kind replaced in T1 count an exit of the kind "xray" (and "vless" of 3.3.5) as a
    parsed exit — a return to ("awg", "vless") would call it broken, unusable or forget it."""

    def setUp(self):
        super().setUp()
        self.st = self.scenario(save=True)
        self.pool = exits.pool(self.w.runner)
        self.v = next(e for e in self.pool if e["id"] == self.vid)
        self.assertEqual(self.v["kind"], "xray")

    def test_twin_unfit(self):                                          # ops.twin_unfit (ops.py, «broken»)
        self.assertIsNone(ops.twin_unfit(self.st, self.vid, rows=self.pool))
        self.assertIsNone(ops.twin_unfit(self.st, self.vid, rows=as_old(self.pool)))
        self.assertEqual(ops.twin_unfit(self.st, self.vid, rows=[dict(self.v, kind="bad")]), "broken")

    def test_trace_exit_row(self):                                       # trace._exit_row: usable
        snap = {"exits": {}}                     # no state of its own: «работает» comes from usable alone
        ctx = {"pool": self.pool, "alive": {self.vid}, "snap": snap}
        row = trace._exit_row(self.st, self.vid, ctx)
        pub = exits.public(self.v, self.st, snap)
        good = exits.state_text(dict(pub, usable=True))
        self.assertNotEqual(good, exits.state_text(dict(pub, usable=False)))
        self.assertEqual(row["text"], good)
        self.assertEqual((row["kind"], row["proto"]), ("xray", "vless"))

    def test_panel_exit_rows(self):                                      # panel/api._exit_rows: usable
        from kaskad.panel import api
        snap = exits.load_snapshot()
        snap.setdefault("exits", {}).setdefault(self.vid, {})["alive"] = True
        exits.save_snapshot(snap)
        rows, err = api._exit_rows(state.load())
        self.assertIsNone(err)
        row = next(r for r in rows if r["id"] == self.vid)
        self.assertEqual((row["kind"], row["proto"], row["usable"]), ("xray", "vless", True))


class ExitKindJsTest(unittest.TestCase):
    """The panel names an xray exit by its protocol and keeps its notes (ping, the balancer) for "xray" and "vless"."""

    @unittest.skipUnless(shutil.which("node"), "node is needed")
    def test_app_and_graph(self):
        app = read("app.js")
        graph = read("graph.js")
        parts = [jsparts.const(app, n) for n in ("EXIT_PROTO", "isXrayExit", "EXIT_KIND", "exitKindText")]
        parts.append(jsparts.function(graph, "isXrayKind"))
        script = "\n".join(parts) + """
        const rows = [{kind: "xray", proto: "vless"}, {kind: "xray", proto: "hysteria2"}, {kind: "vless"},
                      {kind: "awg", version: "2.0"}, {kind: "awg", wg: true}, {kind: "bad"}];
        console.log(JSON.stringify({text: rows.map(exitKindText), xr: rows.map(isXrayExit),
                                    g: ["xray", "vless", "awg", "socks5"].map(isXrayKind)}));
        """
        out = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
        self.assertEqual(out["text"], ["VLESS", "Hysteria2", "VLESS", "AWG 2.0", "WireGuard", ""])
        self.assertEqual(out["xr"], [True, True, True, False, False, False])
        self.assertEqual(out["g"], [True, True, False, False])

    @unittest.skipUnless(shutil.which("node"), "node is needed")
    def test_api_rows_keep_proto_and_role(self):
        """REV-s1-t1 С-1: the chain API → normExits → exitKindText; the rows are built by normExits, not by hand."""
        from tests.test_panel_js_contract import app_scope
        data = [{"id": "e-0000000001", "kind": "xray", "proto": "hysteria2", "role": "abroad", "host": "h1"},
                {"id": "e-0000000002", "kind": "xray", "proto": "vless", "role": "ru", "host": "h2"},
                {"id": "e-0000000003", "kind": "vless", "host": "h3"},              # a row of 3.3.5: no proto, no role
                {"id": "e-0000000004", "kind": "awg", "version": "2.0", "role": "abroad", "host": "h4"}]
        script = app_scope() + """
const rows = app.normExits({ exits: %s }).rows;
console.log(JSON.stringify({ text: rows.map(app.exitKindText), proto: rows.map((r) => r.proto), role: rows.map((r) => r.role) }));
""" % json.dumps(data)
        res = subprocess.run(["node", "-e", script], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr[-2000:])
        out = json.loads(res.stdout.strip().splitlines()[-1])
        self.assertEqual(out["text"], ["Hysteria2", "VLESS", "VLESS", "AWG 2.0"])
        self.assertEqual(out["proto"], ["hysteria2", "vless", "", ""])
        self.assertEqual(out["role"], ["abroad", "ru", "", "abroad"])
        # nav2 r1 (UI-A1): карточки «Выходы» на «Обзоре» больше нет (ovExits удалён, на «Сводке» — плитка с числами);
        # строки выходов панели (exitRows: «Все выходы», деталь, телефон) — копия строки API целиком: proto и role идут с ней
        rows = jsparts.function(read("app.js"), "exitRows")
        self.assertIn("return Object.assign({}, r, { state: exitState(r)", rows)

    def test_graph_balancer_of_the_api(self):
        graph = read("graph.js")
        self.assertIn("d.now.balancer === 'xray'", graph)          # exits.now_of says «xray» since T1


class SchemeTraceTest(GwCase):
    def test_scheme_and_trace_say_the_balancer(self):
        st = self.scenario(save=False)
        pool = [e for e in exits.pool(self.w.runner) if e["kind"] == "xray"]
        alive = set(e["id"] for e in pool)
        dev = {"id": "d-00000001", "route": None, "user": "u-00000001", "access": ["wg"]}
        now = trace._route_now(st, dev, model.route_of(st, dev), {"pool": pool, "alive": alive, "snap": {}}, False)
        # stage 1 (SE §2.3, difference 2): an xray exit of the pool by the device (0x300+N), no balancer of the kernel
        self.assertEqual((now["balancer"], len(now["exits"])), (False, 1))
        self.assertIn(now["exits"][0], alive)
        ch = trace.chain(device="d-00000001", inbound="wg", category="rest", via="pool", target="pool",
                         balancer="xray", exits=[e["id"] for e in pool])
        self.assertEqual(ch["rows"].get("pool"), "vless")          # the group row of the xray exits in the scheme
        self.assertEqual(ch, trace.chain(device="d-00000001", inbound="wg", category="rest", via="pool",
                                         target="pool", balancer="vless", exits=[e["id"] for e in pool]))
        real = st["gateway"]["devices"][0]                     # «Телефон», wg, the default route: the pool
        got = scheme._now(st, real, pool, alive, inbound="wg")
        # stage 1 (difference 2): the scheme says the exit of the device, as trace
        self.assertEqual((got["balancer"], got["exit"]), (False, now["exits"][0]))


if __name__ == "__main__":
    unittest.main()
