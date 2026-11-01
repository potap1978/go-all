"""The contract «panel ↔ server» in node: the REAL functions of app.js and graph.js (cut out of the files by name,
tests/jsparts.py) run against REAL answers of the server on the fake node (tests/panel/panelcase.py):

    C1   an error of the server reaches the scheme whole: its text, «stale» (data.reason), the rows of the intents
         (data.results), the saved layout of a 409 (data.version); the HTTP 404 of an unknown path keeps a text;
    C2   an old live snapshot is «no data»: the page's normLive gives no clock and no devices, the scheme forgets what
         it knew ({"full": true} of SSE) instead of «0 online»;
    C9   the poll (GET overview) gives the scheme the devices and the node's clock: GET /api/scheme's live is taken;
    C10  the address of a device on L2TP comes from the access edges (device.ips is keyed by address pools);
    C16  a timed out change has its own code (the page re-reads its sections);
    C17  a repeated key of the pool (e-<10 hex>-<line>) is no route target, and «bad» is not shown as a word;
    T-G1b N2  the collector silent on a gateway that is on: the exits are «нет данных» in the page and the scheme, and
         right after «Включить шлюз» the page waits calmly (live.starting);
    T-G1b N9  the version of the lists is told by the browser's clock, as every time of the panel;
    T-G1b N10 the first view of the scheme is fitted, but never smaller than readable.
Skipped without node."""

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest import mock

from kaskad import snapshot, state
from kaskad.gw import ops, registry
from kaskad.gw.inbounds.base import DummyInbound
from tests import jsparts
from kaskad.panel import auth
from tests.panel.panelcase import PanelCase
from tests.panel.test_panel_host import HostPanelCase
from tests.panel.test_panel_proxy import ProxyCase

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib", "kaskad", "panel", "static")


def read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


def app_scope(pathname="/"):
    js = read("app.js")
    parts = [jsparts.const(js, n) for n in ("panelBase", "BASE", "apiUrl", "API_TIMEOUT_MS", "APPLY_TIMEOUT_MS", "LONG_TIMEOUT_MS",
                                            "CERT_TIMEOUT_MS", "DNS_TIMEOUT_MS", "SITE_TIMEOUT_MS", "SITE_DEPLOY_TIMEOUT_MS",
                                            "SITE_CHECK_TIMEOUT_MS", "DASH", "known",
                                            "isObj", "firstOf", "arr", "num", "toMap", "toDate", "TIMEOUT_BY_ROUTE",
                                            "EXIT_PROTO", "isXrayExit", "EXIT_KIND", "exitKindText", "exitOwn", "exitsUnknown", "qs", "enc",
                                            "FOCUS_ID", "TRACE_REF", "PP_SHOW", "SECTIONS", "SECTION_BY", "secOf", "isItem", "navOk", "goNav",
                                            "KEY_FILE_MAX", "keyLine", "PSTATES", "KS", "SM")]   # KS, SM — «Режим стрима» (stream.js в node нет)
    parts += [jsparts.klass(js, "ApiError"), jsparts.dates(read("boot.js"))]      # DATES: boot.js KaskadLang (3.3.2)
    parts += [jsparts.function(js, n) for n in ("timeoutOf", "request", "graphError", "normLive", "normRoute",
                                                "normExits", "normExitPolicy", "exitStateName", "exitState", "normOverview", "fmtDay",
                                                "listsLabel", "navState", "showOnMap", "backFromMap", "traceNav", "normMtu",
                                                "exitsAddCode", "keyTextBad")]
    return """
const app = (function () {
  const t = (key, vars) => key + (vars ? " " + JSON.stringify(vars) : "");
  const LANG = "ru", LOCALE = "ru-RU";                 // the language of the page (boot.js KaskadLang, 3.3.2)
  const MOCK = false;                                  // the live panel: no ?mock=1 (the dev sections are closed)
  let FETCH = null;
  const doFetch = (url, init) => FETCH(url, init);
  const out = { logins: 0 };
  const showLogin = () => { out.logins += 1; };
  // what the cut functions read of the page: the live model, «сборщик молчит», «шлюз включён»
  const ST = { liveOff: false, gw: true, lv: {} };
  const liveOff = () => ST.liveOff;
  const gwEnabled = () => ST.gw;
  const lvExit = (id) => ST.lv[id] || null;
  // the address of a section (#people?focus=…): the page's sections and its location
  const PAGES = { overview: {}, people: {}, scheme: {} };
  const location = { hash: "", pathname: %s };
%s
  return { request, graphError, normLive, timeoutOf, exitOwn, exitKindText, ApiError, out, ST, normExits, exitState,
           normOverview, listsLabel, navState, showOnMap, backFromMap, traceNav, normMtu, location, BASE, apiUrl, panelBase,
           exitsAddCode, keyTextBad, keyLine, KEY_FILE_MAX,
           setFetch: (f) => { FETCH = f; } };
})();
""" % (json.dumps(pathname), "\n".join(parts))


def graph_scope():
    js = read("graph.js")
    parts = [jsparts.function(js, n) for n in ("call", "errBody", "errText", "mergePatch", "mergeLive", "liveFresh",
                                               "devOnline", "indexBase", "ipOf", "dupExit", "clamp", "liveExitSt",
                                               "exitsNoData", "exitSt", "fitView", "focusRef", "focusOf", "traceOf",
                                               "effRoute", "effProfile", "userProfile", "rebuildDraft",
                                               "applyToDraft", "computeView", "copyRoute", "sameRoute",
                                               "nodeTitle", "viaTitle", "kindTitle", "defProfile", "ruInfo",
                                               "ruWorks", "ruOpen", "simRef", "simTrace", "kindOn", "pathInbound",
                                               "isXrayKind", "isVless", "xrayIn", "devPath", "whoPath", "chainOf", "linkGeom", "viewPut",
                                               "traceEdges", "mkTrace", "whoTrace", "filterHint", "liveExits",
                                               "inheritedRoute", "keptFail", "simKinds", "onNow", "edgeLive",
                                               "overlapFix", "nodeW", "nodeH", "poolRows", "poolEmptyHelp",
                                               "rfSync", "rfKey", "rfReset", "pending", "intentKey", "sameAsBase",
                                               "ruOf", "ruKnown", "ruNodeOk")]
    parts += [jsparts.const(js, "GAP_X", keyword="var")]
    parts += [jsparts.const(js, "READABLE", keyword="var")]
    return """
const graph = (function () {
  var API = '/api', OWN = Object.prototype.hasOwnProperty, LIVE_STALE = 60;
  var S = { ctx: null, live: { devices: {}, exits: {}, inbounds: {}, ts: null }, rates: {}, prev: {}, liveAt: 0, idx: null,
            order: {}, intents: [], draft: null, view: null, base: null };
  function t(key) { return key; }
%s
  return { S: S, call: call, errText: errText, mergeLive: mergeLive, liveFresh: liveFresh, devOnline: devOnline,
           indexBase: indexBase, ipOf: ipOf, dupExit: dupExit, exitSt: exitSt, fitView: fitView, READABLE: READABLE,
           focusRef: focusRef, focusOf: focusOf, rebuildDraft: rebuildDraft, computeView: computeView,
           effProfile: effProfile, userProfile: userProfile, defProfile: defProfile, ruOpen: ruOpen, ruWorks: ruWorks,
           traceOf: traceOf, applyToDraft: applyToDraft, simRef: simRef, simTrace: simTrace, devPath: devPath,
           chainOf: chainOf, linkGeom: linkGeom, pathInbound: pathInbound, whoTrace: whoTrace, traceEdges: traceEdges,
           inheritedRoute: inheritedRoute, keptFail: keptFail, onNow: onNow, edgeLive: edgeLive, overlapFix: overlapFix,
           poolRows: poolRows, poolEmptyHelp: poolEmptyHelp };
})();
// the scheme's ctx.api — exactly what graphCtx() of app.js passes (test_graph_ctx_passes_the_whole_body checks it)
graph.S.ctx = { api: (method, path, body) => app.request(String(method || "GET").toUpperCase(), String(path), body === null ? undefined : body).catch((e) => { throw app.graphError(e); }) };
""" % "\n".join(parts)


def node(script, data, env=None, pathname="/"):
    """Run ``script`` (app and graph scopes are in front of it; DATA is ``data``); it prints one JSON line. ``env`` —
    more of the environment (TZ: the browser's time zone); ``pathname`` — the address of the page (a prefix)."""
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(app_scope(pathname) + graph_scope() + "const DATA = %s;\n(async () => {\n%s\n})().catch((e) => { "
                "console.log(JSON.stringify({crash: String(e && e.stack || e)})); });\n"
                % (json.dumps(data, ensure_ascii=False), script))
        path = f.name
    try:
        res = subprocess.run(["node", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
                             env=dict(os.environ, **(env or {})))
    finally:
        os.unlink(path)
    out = res.stdout.decode("utf-8").strip().splitlines()
    if res.returncode != 0 or not out:
        raise AssertionError("node: %s" % res.stderr.decode("utf-8", "replace")[-2000:])
    doc = json.loads(out[-1])
    if "crash" in doc:
        raise AssertionError(doc["crash"])
    return doc


# a fake fetch of the answers the server gave: DATA.answers[name] = {status, text}
FETCH = """
function answer(a) {
  app.setFetch(async () => ({ status: a.status, ok: a.status >= 200 && a.status < 300, text: async () => a.text }));
}
"""


@unittest.skipUnless(shutil.which("node"), "нет node: функции app.js и graph.js не запустить")
class JsContractTest(PanelCase):
    def setUp(self):
        super().setUp()
        self.l2tp = DummyInbound(kind="l2tp", default_port=0, proto=None)
        registry.register(self.l2tp)
        self.gateway(("wg", "l2tp"))
        self.did = ops.device_add(self.w.runner, self.uid, "Роутер", kind="router", platform="keenetic",
                                  access=["wg", "l2tp"])["device"]["id"]
        snapshot.bump()

    def answer(self, method, path, body=None):
        r = self.call(method, path, body)
        return {"status": r.status, "text": r.body.decode("utf-8")}

    def test_graph_ctx_passes_the_whole_body(self):
        api = [ln for ln in read("app.js").splitlines() if ln.strip().startswith("api: (method, path, body) =>")]
        self.assertEqual(len(api), 1)
        self.assertIn(".catch((e) => { throw graphError(e); })", api[0])

    def test_c1_errors_reach_the_scheme_whole(self):
        intent = [{"type": "route", "device": self.did, "via": "block"}]
        answers = {
            "stale": self.answer("POST", "/api/scheme/apply", {"intents": intent, "version": "0000000000000000"}),
            "results": self.answer("POST", "/api/scheme/apply", {"intents": [
                {"type": "route", "device": self.did, "via": "block"},
                {"type": "grant", "device": self.did, "kind": "foo"}]}),
            "layout_ok": self.answer("PUT", "/api/scheme/layout", {"version": 0, "positions": {"pool": [1, 2]}}),
            "layout": self.answer("PUT", "/api/scheme/layout", {"version": 0, "positions": {"pool": [3, 4]}}),
            "nopath": self.answer("GET", "/api/scheme/nosuch"),
        }
        self.assertEqual([answers[k]["status"] for k in ("stale", "results", "layout", "nopath")], [409, 409, 409, 404])
        doc = node(FETCH + """
const out = {};
for (const name of ["stale", "results", "layout", "nopath"]) {
  answer(DATA[name]);
  const r = await graph.call(name === "layout" ? "PUT" : "POST", name === "layout" ? "/scheme/layout" : "/scheme/apply", {});
  out[name] = { ok: r.ok, status: r.status, text: graph.errText(r), reason: r.data && r.data.data && r.data.data.reason,
                results: r.data && r.data.data && r.data.data.results ? r.data.data.results.map((x) => [x.ok, x.error || ""]) : null,
                version: r.data && r.data.data ? r.data.data.version : null };
}
app.setFetch(async () => { throw new TypeError("Failed to fetch"); });
const net = await graph.call("GET", "/scheme");
out.net = { ok: net.ok, text: graph.errText(net) };
// an error of an older ctx (ApiError with only the nested data) keeps both the text and data.reason
const old = await new Promise((resolve) => {
  graph.S.ctx = { api: () => Promise.reject(new app.ApiError("текст сервера", 409, 3, { reason: "stale" })) };
  graph.call("POST", "/scheme/apply", {}).then(resolve);
});
out.old = { text: graph.errText(old), reason: old.data.data.reason };
console.log(JSON.stringify(out));
""", answers)
        stale = json.loads(answers["stale"]["text"])
        self.assertEqual(doc["stale"], {"ok": False, "status": 409, "text": stale["error"], "reason": "stale",
                                        "results": None, "version": stale["data"]["version"]})
        self.assertIn("настройки изменились", doc["stale"]["text"])
        res = doc["results"]
        self.assertEqual(res["results"][0], [True, ""])
        self.assertFalse(res["results"][1][0])
        self.assertIn("неизвестный вход", res["results"][1][1])
        self.assertIn("неизвестный вход", res["text"])                       # the server's own words, not «нет связи»
        layout = json.loads(answers["layout"]["text"])
        self.assertEqual((doc["layout"]["version"], layout["data"]["version"]), (1, 1))   # the retry takes this
        self.assertEqual(doc["nopath"]["text"], 'err.http {"s":404}')
        self.assertEqual(doc["net"]["text"], "err.net")
        self.assertEqual(doc["old"], {"text": "текст сервера", "reason": "stale"})

    def test_c16_a_timed_out_change_says_so(self):
        doc = node("""
const out = {};
app.setFetch((url, init) => new Promise((resolve, reject) => { init.signal.addEventListener("abort", () => { const e = new Error("aborted"); e.name = "AbortError"; reject(e); }); }));
for (const m of ["PUT", "GET"]) {
  try { await app.request(m, "/api/gw/route", m === "PUT" ? { via: "pool" } : undefined, { timeout: 5 }); } catch (e) { out[m] = [e.code, e.message, e.status]; }
}
out.t = [app.timeoutOf("PUT", "/api/gw/devices/d-1a2b3c4d/access"), app.timeoutOf("POST", "/api/gw/devices/d-1a2b3c4d/issue"),
         app.timeoutOf("POST", "/api/gw/inbounds/wg/install"), app.timeoutOf("GET", "/api/gw?x=1"), app.timeoutOf("POST", "/api/scheme/apply")];
console.log(JSON.stringify(out));
""", {})
        self.assertEqual(doc["PUT"], ["timeout", "err.timeoutChange", 0])
        self.assertEqual(doc["GET"], ["timeout", "err.timeout", 0])
        self.assertEqual(doc["t"], [300000, 30000, 1500000, 30000, 300000])

    def test_c2_c9_live_snapshots(self):
        self.ads["wg"].sessions = [{"device": self.did, "ip": "10.77.1.10", "since": 1790000000, "rx": 10, "tx": 20}]
        self.collect()
        data = {"fresh": self.ok("GET", "/api/overview"), "scheme": self.ok("GET", "/api/scheme")}
        doc = json.loads(self.read("/run/kaskad/live.json"))
        doc["ts"] = time.time() - 3600                                        # the collector stopped an hour ago
        self.write("/run/kaskad/live.json", json.dumps(doc))
        snapshot.forget()
        data["stale"] = self.ok("GET", "/api/overview")
        self.assertIsNone(self.ok("GET", "/api/scheme")["live"])
        data["did"] = self.did
        out = node("""
const out = {};
const fresh = app.normLive(DATA.fresh), stale = app.normLive(DATA.stale);
out.fresh = { ts: fresh.ts, online: fresh.devices[DATA.did].online, running: fresh.inbounds.wg.running };
out.stale = { ts: stale.ts === undefined ? null : stale.ts, devices: "devices" in stale, running: stale.inbounds.wg.running };
// the poll: app.js hands the scheme {full: true, …S.live}; then GET /api/scheme's own live must be taken
graph.mergeLive(Object.assign({ full: true }, fresh), true);
out.poll = { ts: graph.S.live.ts, fresh: graph.liveFresh(), online: graph.devOnline(DATA.did),
             takesScheme: !!(DATA.scheme.live && (!graph.S.live.ts || (DATA.scheme.live.ts && DATA.scheme.live.ts >= graph.S.live.ts))) };
// SSE without fresh data: {"full": true} — the scheme knows nothing any more
graph.mergeLive({ full: true }, true);
out.gone = { ts: graph.S.live.ts, fresh: graph.liveFresh(), online: graph.devOnline(DATA.did) };
graph.mergeLive({ devices: { x: { online: true } } }, false);           // a patch with nothing under it
out.patch = { fresh: graph.liveFresh() };
// and the stale overview in the poll: no clock — not fresh either
graph.mergeLive(Object.assign({ full: true }, stale), true);
out.stalePoll = { fresh: graph.liveFresh(), online: graph.devOnline(DATA.did) };
console.log(JSON.stringify(out));
""", data)
        ts = data["fresh"]["live"]["ts"]
        self.assertEqual(out["fresh"], {"ts": ts, "online": True, "running": True})
        self.assertEqual(out["stale"], {"ts": None, "devices": False, "running": None})
        self.assertEqual(out["poll"], {"ts": ts, "fresh": True, "online": True, "takesScheme": True})
        self.assertEqual(out["gone"], {"ts": None, "fresh": False, "online": None})
        self.assertEqual(out["patch"], {"fresh": False})
        self.assertEqual(out["stalePoll"], {"fresh": False, "online": None})

    def test_c10_c17_addresses_and_repeated_keys(self):
        scheme = self.ok("GET", "/api/scheme")
        dev = next(d for n in scheme["nodes"] if n["type"] == "user" for d in n["data"]["devices"] if d["id"] == self.did)
        self.assertNotIn("l2tp", dev["ips"])                                 # keyed by pools: «ppp»
        edge = next(e for e in scheme["edges"] if e["id"] == "access:%s:l2tp" % self.did)
        out = node("""
graph.S.idx = graph.indexBase(DATA.scheme);
console.log(JSON.stringify({
  l2tp: graph.ipOf(DATA.did, "l2tp"), wg: graph.ipOf(DATA.did, "wg"), none: graph.ipOf(DATA.did, "awg2"),
  dup: [graph.dupExit("exit:e-41a19b9cbe-3"), graph.dupExit("e-41a19b9cbe-12"), graph.dupExit("exit:e-41a19b9cbe"), graph.dupExit("pool")],
  own: [app.exitOwn("e-41a19b9cbe"), app.exitOwn("e-41a19b9cbe-3")],
  kind: [app.exitKindText({ kind: "bad", version: "3.1" }), app.exitKindText({ kind: "awg", version: "2.0" }), app.exitKindText({ kind: "vless", version: "" }),
         app.exitKindText({ kind: "xray", proto: "vless" }), app.exitKindText({ kind: "xray", proto: "trojan" })]
}));
""", {"scheme": scheme, "did": self.did})
        self.assertEqual(out["l2tp"], edge["data"]["ip"])
        self.assertEqual(out["l2tp"], dev["ips"]["ppp"])
        self.assertEqual(out["wg"], dev["ips"]["wg"])
        self.assertIsNone(out["none"])
        self.assertEqual(out["dup"], [True, True, False, False])
        self.assertEqual(out["own"], [True, False])
        self.assertEqual(out["kind"], ["", "AWG 2.0", "VLESS", "VLESS", "Trojan"])   # T1: «xray» by its proto

    def test_n2_without_the_collector_the_exits_are_no_data(self):
        """T-G1b N2: the collector stopped — the exits stayed «работает» on «Обзор», «Выходы» and «Схема». Their state
        is the collector's too: with a silent collector on a gateway that is on the page and the scheme say «нет
        данных»; fresh data — the states again; the gateway off — the snapshot as before. Right after «Включить
        шлюз» GET overview carries live.starting: the page waits calmly until then."""
        st = state.load()
        st["gateway"]["enabled_at"] = "2026-01-01T00:00:00Z"               # switched on long ago: silence, not a start
        state.save(st)
        snapshot.forget()
        data = {"stale": self.ok("GET", "/api/overview"), "exits_stale": self.ok("GET", "/api/gw/exits"),
                "scheme_stale": self.ok("GET", "/api/scheme")}
        self.collect()
        data.update(fresh=self.ok("GET", "/api/overview"), exits_fresh=self.ok("GET", "/api/gw/exits"),
                    scheme_fresh=self.ok("GET", "/api/scheme"))
        out = node("""
const out = {};
const rows = (j) => app.normExits(j).rows;
app.ST.liveOff = true; app.ST.gw = true;
out.stale = rows(DATA.exits_stale).map((r) => [r.state, app.exitState(r)]);
out.staleOfFresh = rows(DATA.exits_fresh).map((r) => app.exitState(r));       // an older answer: still «нет данных»
app.ST.liveOff = false; app.ST.lv = app.normLive(DATA.fresh).exits;
out.fresh = rows(DATA.exits_fresh).map((r) => [r.id, app.exitState(r)]);
app.ST.liveOff = true; app.ST.gw = false;                                      // the gateway is off
out.off = rows(DATA.exits_fresh).map((r) => app.exitState(r));
const exitNodes = (doc) => doc.nodes.filter((n) => n.type === "exit");
graph.S.base = DATA.scheme_stale;
graph.mergeLive({ full: true }, true);                                         // SSE without fresh data
out.schemeStale = exitNodes(DATA.scheme_stale).map((n) => graph.exitSt(n.data.id, n.data.state));
graph.mergeLive(Object.assign({ full: true }, DATA.scheme_fresh.live), true);
out.schemeFresh = exitNodes(DATA.scheme_fresh).map((n) => [n.data.id, graph.exitSt(n.data.id, n.data.state)]);
const ov = app.normOverview({ live: { fresh: false, starting: 30 } });
out.starting = ov.startUntil > Date.now() + 25000 && ov.startUntil <= Date.now() + 30000;
out.notStarting = app.normOverview(DATA.stale).startUntil;
// 3.3.2: codes of the exit rows (gw: detail_code, problem_code) — the page decides by them, not by the words
out.codes = app.normExits({ exits: [{ id: "e-1", detail: "x", detail_code: "detail.not_checked", problem_code: "p.c" }] }).rows
  .map((r) => [r.detailCode, r.problemCode]);
console.log(JSON.stringify(out));
""", data)
        self.assertEqual(out["stale"], [["nodata", "nodata"]] * 2)
        self.assertEqual(out["staleOfFresh"], ["nodata"] * 2)
        self.assertEqual(dict(out["fresh"])[self.aid], "alive")
        self.assertNotIn("nodata", dict(out["fresh"]).values())
        self.assertNotIn("nodata", out["off"])
        self.assertEqual(out["codes"], [["detail.not_checked", "p.c"]])
        self.assertEqual(out["schemeStale"], ["nodata"] * 2)
        self.assertEqual(dict(out["schemeFresh"])[self.aid], "alive")
        self.assertNotIn("nodata", dict(out["schemeFresh"]).values())
        self.assertEqual((out["starting"], out["notStarting"]), (True, None))

    def test_an_empty_pool_says_where_to_add_a_key(self):
        """3.2.21: the owner did not find how to add a key on a fresh install — «Меню узла → Прокси для API Telegram →
        добавить ключ» told him nothing. By the server's own answers: the page says the panel's button and the SSH menu
        with the number the menu really has (17 → 3 → 3 while the default route goes through the exits, → 2 for direct
        and block), and «без интернета» only while the empty pool meets the fail policy «Блокировать»; the scheme too."""
        from tests.fakes_gw import write_pool
        from kaskad.gw import exits as gexits
        write_pool([])
        snapshot.forget()
        data = {"block": self.ok("GET", "/api/gw/exits"), "scheme_block": self.ok("GET", "/api/scheme")}
        # T7 (§7.9): the route of the node with its ver (the one of the read)
        self.ok("PUT", "/api/gw/route", {"via": "pool", "fail": "direct", "ver": data["block"]["default_route_ver"]})
        data["fail_direct"] = self.ok("GET", "/api/gw/exits")
        self.ok("PUT", "/api/gw/route", {"via": "direct", "ver": data["fail_direct"]["default_route_ver"]})
        data.update(direct=self.ok("GET", "/api/gw/exits"), scheme_direct=self.ok("GET", "/api/scheme"))
        out = node("""
const out = {};
for (const k of ["block", "fail_direct", "direct"]) {
  const d = app.normExits(DATA[k]);
  out[k] = { code: app.exitsAddCode(d), item: d.add.item, offline: d.offline, text: d.offlineText, rows: d.rows.length };
}
out.old = app.exitsAddCode(app.normExits({ ok: true, exits: [] }));            // a server before 3.2.21
const pool = (doc) => doc.nodes.filter((n) => n.type === "pool")[0].data;
out.scheme = [pool(DATA.scheme_block), pool(DATA.scheme_direct)].map((p) => [p.add_menu, p.offline, graph.poolEmptyHelp(p)]);
out.schemeOld = graph.poolEmptyHelp({ exits: [] });
console.log(JSON.stringify(out));
""", data)
        b, fd, d = out["block"], out["fail_direct"], out["direct"]
        self.assertIn('"menu":"gokaskad → 17 → 3 → 3"', b["code"])
        self.assertIn("«Добавить ключ выхода»", b["code"])
        self.assertEqual((b["item"], b["offline"], b["text"], b["rows"]), (3, True, gexits.NO_EXITS_OFFLINE, 0))
        self.assertEqual((fd["item"], fd["offline"], fd["text"]), (3, False, ""))
        self.assertIn('"menu":"gokaskad → 17 → 3 → 2"', d["code"])
        self.assertEqual((d["item"], d["offline"]), (2, False))
        self.assertEqual(out["old"], "ex.emptyCodeOld")
        self.assertEqual(out["scheme"], [["gokaskad → 17 → 3 → 3", True, "graph.pool.emptyHelp"],
                                         ["gokaskad → 17 → 3 → 2", False, "graph.pool.emptyHelp"]])
        self.assertEqual(out["schemeOld"], "graph.pool.emptyHelpOld")

    def test_a_file_of_amnezia_is_told_by_its_content(self):
        """3.2.21-P2: a file read by the browser goes as text; the preview of the server names the kind and the line by
        the content (WireGuard, AmneziaWG 3.1 of a vpn://), the page's own check refuses a binary file and a big one
        before any request; the waits of the three calls."""
        from tests.test_gw_exits import conf_of, vpn_of
        text = conf_of("wg", 3, "198.51.100.33:51820") + "\n" + vpn_of(conf_of("3.1", 4), "Дом")
        data = {"preview": self.answer("POST", "/api/gw/exits/preview", {"key": text}),
                "binary": self.answer("POST", "/api/gw/exits/preview", {"key": "[Interface]\u0000\u0001"})}
        out = node(FETCH + r"""
const out = {};
answer(DATA.preview);
const r = await app.request("POST", "/api/gw/exits/preview", { key: "x" });
out.lines = r.items.map((x) => app.keyLine(x));
out.secret = JSON.stringify(r).indexOf("PrivateKey") >= 0;
answer(DATA.binary);
try { await app.request("POST", "/api/gw/exits/preview", { key: "x" }); } catch (e) { out.binary = [e.status, e.message]; }
out.bad = [app.keyTextBad("vless://x\n"), app.keyTextBad("a\u0000b"), app.keyTextBad("\uFFFD".repeat(20) + "x".repeat(10)),
           app.keyTextBad("x".repeat(5000) + "\uFFFD")];
out.max = app.KEY_FILE_MAX;
out.t = [app.timeoutOf("POST", "/api/gw/exits/preview"), app.timeoutOf("POST", "/api/gw/exits"), app.timeoutOf("DELETE", "/api/gw/exits/e-0123456789-3")];
console.log(JSON.stringify(out));
""", data)
        self.assertEqual(out["lines"], ["WireGuard · 198.51.100.33:51820", "AmneziaWG 3.1 · 198.51.100.31:51820 «Дом»"])
        self.assertFalse(out["secret"])
        self.assertEqual(out["binary"], [400, "Это не текстовый файл ключа."])
        self.assertEqual(out["bad"], [False, True, True, False])
        self.assertEqual(out["max"], 64 * 1024)
        self.assertEqual(out["t"], [30000, 1500000, 1500000])

    def test_n9_the_version_of_the_lists_is_told_by_the_browsers_clock(self):
        """T-G1b N9: «Версия списков … 10:12:00 UTC» stood beside «Последняя проверка 03:16» of the browser's clock.
        The panel tells every time by the browser's clock: the version too (the tag of geoip stays a tag)."""
        from types import SimpleNamespace
        from kaskad.rules import ops as rules_ops
        v = SimpleNamespace(meta={"geoip": {"date": "2026-10-06T10:12:00Z", "tag": "202610061012"},
                                  "created": "2026-10-06T10:15:30Z"})
        lists = {"version": "20261006T101530Z", "label": rules_ops.label(v), "created": v.meta["created"],
                 "geoip": v.meta["geoip"]}
        # the menu's words: the date in the common format (T-M6-332b №7), the moment in UTC and said so
        self.assertRegex(lists["label"], r"^от 06\.10(\.2026)?, 10:12 UTC \(geoip 202610061012\)$")
        out = node("""
console.log(JSON.stringify({ msk: app.listsLabel(DATA.lists), created: app.listsLabel({ created: DATA.lists.created }),
  noDate: app.listsLabel({ label: "от ? (geoip x)" }), none: app.listsLabel(null) }));
""", {"lists": lists}, env={"TZ": "Europe/Moscow"})
        self.assertEqual(out["msk"], 'ru.versionAt {"at":"06.10, 13:12"} (geoip 202610061012)')     # this year: no year
        self.assertEqual(out["created"], 'ru.versionAt {"at":"06.10, 13:15"}')
        self.assertEqual((out["noDate"], out["none"]), ("от ? (geoip x)", "—"))

    def test_the_mtu_card_does_not_repeat_its_headline(self):
        """T-N5 3.2.19-r2, Н-5: «WireGuard: сетевая карта сервера уже, чем нужно клиентам. WireGuard: карта сервера 1250
        уже, чем нужно клиентам …» — the card's text (normMtu of the real GET gw) is the core's detail: no title, no
        headline again, the bytes explained."""
        with state.transaction() as st:
            st["gateway"]["mtu"] = {"nic": {"dev": "eth0", "mtu": 1250}}
        self.write("/proc/net/route", "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\n"
                                      "eth0\t00000000\t0102A8C0\t0003\t0\t0\t0\t00000000\n")
        self.write("/sys/class/net/eth0/mtu", "1250\n")
        snapshot.bump()
        out = node(FETCH + """
answer(DATA.gw);
const j = await app.request("GET", "/api/gw");
console.log(JSON.stringify(app.normMtu(j.mtu)));
""", {"gw": self.answer("GET", "/api/gw")})
        row = [r for r in out["inbounds"] if r["kind"] == "wg"][0]
        self.assertEqual(out["ask"], ["wg"])
        self.assertTrue(row["text"].startswith("Карта 1250, а пакету клиента с MTU 1280 при S4 = 0 нужно 1360 байт "
                                               "(MTU клиента + 80 + S4)."), row["text"])
        self.assertNotIn("WireGuard", row["text"])
        self.assertNotIn("уже, чем нужно", row["text"])

    def test_n10_the_first_view_of_the_scheme_is_readable(self):
        """T-G1b N10: the scheme opened at 30 % («Вписать» of a big scheme) — no label could be read. The first view is
        fitted, but never smaller than READABLE: a big scheme from its start (people, top left); «Вписать» and the key 0
        still show all of it."""
        out = node("""
const big = { x1: 24, y1: 24, x2: 1700, y2: 2400 }, wide = { x1: 0, y1: 0, x2: 1300, y2: 1500 };
const small = { x1: 24, y1: 24, x2: 900, y2: 500 };
const r = (v) => ({ k: Math.round(v.k * 1000) / 1000, x: Math.round(v.x), y: Math.round(v.y) });
console.log(JSON.stringify({ readable: graph.READABLE,
  big: r(graph.fitView(big, 1100, 700, false, 0.3)), bigAll: r(graph.fitView(big, 1100, 700, true, 0.3)),
  wide: r(graph.fitView(wide, 1100, 700, false, 0.3)), small: r(graph.fitView(small, 1100, 700, false, 0.3)),
  smallAll: r(graph.fitView(small, 1100, 700, true, 0.3)) }));
""", {})
        self.assertEqual(out["readable"], 0.75)
        self.assertEqual(out["big"], {"k": 0.75, "x": 10, "y": 10})           # its start, readable
        self.assertEqual(out["bigAll"]["k"], 0.3)                              # «Вписать»: all of it
        self.assertEqual(out["wide"], {"k": 0.803, "x": 28, "y": 28})          # all its width fits readably
        self.assertEqual(out["small"], out["smallAll"])                        # a small one: simply fitted
        self.assertEqual(out["small"]["k"], 1.1)
        js = read("graph.js")
        self.assertIn("case 'fit': fit(true); break;", js)
        self.assertIn("if (e.key === '0') { e.preventDefault(); fit(true); return; }", js)


    def test_on_the_map_the_highlight_of_a_person(self):
        """«На схеме»: the highlight of a person is the union of the SINGLE paths of his devices (each through its live
        or first inbound — not every inbound) on the data of the real GET /api/scheme; nothing of another person. A
        device — only its own path. A person without devices — the path of a new device of his."""
        phone = ops.device_add(self.w.runner, self.uid, "Телефон", kind="phone", platform="ios", access=["wg"],
                               via="block")["device"]["id"]
        papa = ops.user_add(self.w.runner, "Папа", via="exit:" + self.aid)["id"]
        laptop = ops.device_add(self.w.runner, papa, "Ноутбук", kind="laptop", platform="windows",
                                access=["l2tp"])["device"]["id"]
        guest = ops.user_add(self.w.runner, "Гость", via="direct")["id"]
        snapshot.bump()
        scheme = self.ok("GET", "/api/scheme")
        out = node("""
const S = graph.S;
S.base = DATA.scheme; S.idx = graph.indexBase(DATA.scheme); S.order = {};
S.idx.users.forEach((u) => { S.order[u.id] = (u.data.devices || []).map((d) => d.id); });
graph.rebuildDraft(); graph.computeView();
const sets = (T) => T && { nodes: Object.keys(T.nodes).sort(), devs: Object.keys(T.devs).sort(), edges: Object.keys(T.edges).sort(),
                           focus: T.focus };
console.log(JSON.stringify({
  mama: sets(graph.focusOf({ t: "user", id: DATA.uid })), phone: sets(graph.focusOf({ t: "dev", id: DATA.phone })),
  papa: sets(graph.focusOf({ t: "user", id: DATA.papa })), guest: sets(graph.focusOf({ t: "user", id: DATA.guest })),
  gone: graph.focusOf({ t: "dev", id: "d-00000000" }),
  refs: [graph.focusRef(DATA.uid), graph.focusRef(DATA.phone), graph.focusRef("pool"), graph.focusRef("u-1"), graph.focusRef(null)]
}));
""", {"scheme": scheme, "uid": self.uid, "did": self.did, "phone": phone, "papa": papa, "guest": guest})
        mama, ph, pa, gu = out["mama"], out["phone"], out["papa"], out["guest"]
        prof = next(d for n in scheme["nodes"] if n["type"] == "user" for d in n["data"]["devices"]
                    if d["id"] == self.did)["profile"]
        self.assertEqual(mama["devs"], sorted([self.did, phone]))
        for nid in (self.uid, "in:wg", "filter", "rules:" + prof, "pool", "block"):
            self.assertIn(nid, mama["nodes"])
        # the union of SINGLE paths: each device through ONE inbound — the first given (wg), not every one
        for k in ("access:%s:wg" % self.did, "access:%s:wg" % phone, "flow:wg", "pass:" + prof,
                  "route:%s:pool" % prof, "route:%s:block" % prof):
            self.assertIn(k, mama["edges"])
        self.assertNotIn("access:%s:l2tp" % self.did, mama["edges"])
        self.assertNotIn("in:l2tp", mama["nodes"])
        self.assertNotIn(papa, mama["nodes"])
        self.assertNotIn(laptop, mama["devs"])
        self.assertEqual(mama["focus"], {"t": "user", "id": self.uid})
        self.assertEqual(ph["devs"], [phone])                                    # a device — only its path
        self.assertIn(self.uid, ph["nodes"])
        self.assertIn("block", ph["nodes"])
        self.assertNotIn("in:l2tp", ph["nodes"])
        self.assertNotIn("pool", ph["nodes"])
        self.assertNotIn("access:%s:wg" % self.did, ph["edges"])
        self.assertEqual(ph["edges"], sorted(["access:%s:wg" % phone, "flow:wg", "pass:" + prof, "route:%s:block" % prof]))
        self.assertIn("exit:" + self.aid, pa["nodes"])                           # «как у пользователя»: his exit
        self.assertIn("route:%s:exit:%s" % (prof, self.aid), pa["edges"])
        self.assertEqual(pa["devs"], [laptop])
        self.assertEqual(gu["devs"], [])                                         # no devices: the path of a new one
        self.assertIn("direct", gu["nodes"])
        self.assertEqual(gu["edges"], sorted(["pass:" + prof, "route:%s:direct" % prof]))
        self.assertIsNone(out["gone"])
        self.assertEqual(out["refs"], [{"t": "user", "id": self.uid}, {"t": "dev", "id": phone}, None, None, None])

    def test_one_path_is_one_line(self):
        """Владелец (этап 2): «один путь трафика = одна линия». pptp-g3 (PPTP) и l2tp-g3 (L2TP + SOCKS5 + IKEv2) на
        настоящем GET /api/scheme: от строки устройства на холсте выходит ОДНА линия — к входу его пути (первому
        выданному; на связи — тому, через который оно сейчас); подсвеченный путь — непрерывная цепочка без ветвей:
        устройство → вход → фильтр → правила → цель → выход; «Через» (выбранный вход) переносит путь на тот вход."""
        for kind, port, proto in (("pptp", 1723, "tcp"), ("socks5", 1080, "tcp"), ("ikev2", 4500, "udp")):
            registry.register(DummyInbound(kind=kind, default_port=port, proto=proto))
            ops.inbound_install(self.w.runner, kind)
        pptp = ops.device_add(self.w.runner, self.uid, "pptp-g3", kind="pc", platform="windows",
                              access=["pptp"])["device"]["id"]
        l2tp = ops.device_add(self.w.runner, self.uid, "l2tp-g3", kind="pc", platform="windows",
                              access=["l2tp", "socks5", "ikev2"])["device"]["id"]
        snapshot.bump()
        scheme = self.ok("GET", "/api/scheme")
        out = node("""
const S = graph.S;
S.base = DATA.scheme; S.idx = graph.indexBase(DATA.scheme); S.order = {};
S.idx.users.forEach((u) => { S.order[u.id] = (u.data.devices || []).map((d) => d.id); });
graph.rebuildDraft(); graph.computeView();
// a path is ONE line: every link starts where the previous one ends, no node is left twice
const line = (edges) => {
  const g = edges.map(graph.linkGeom);
  if (g.some((x) => !x)) return "no geometry: " + edges.filter((e, i) => !g[i]).join(",");
  for (let i = 1; i < g.length; i++) if (g[i - 1].t !== g[i].s) return "break at " + edges[i];
  const from = g.map((x) => x.s + "|" + x.sh);
  return new Set(g.map((x) => x.s)).size === g.length ? "ok" : "branch";
};
const conns = (did) => S.view.edges.filter((e) => e.type === "conn" && e.dev === did).map((e) => e.k);
const one = (did) => { const T = graph.traceOf(did); return { edges: Object.keys(T.edges), line: line(Object.keys(T.edges)),
  nodes: Object.keys(T.nodes).sort(), rows: T.rows, inView: Object.keys(T.edges).every((k) => !!S.view.byKey[k]) }; };
const out = { pptp: one(DATA.pptp), l2tp: one(DATA.l2tp), connsP: conns(DATA.pptp), connsL: conns(DATA.l2tp) };
// online through IKEv2 now: the path is through IKEv2
S.live.devices[DATA.l2tp] = { online: true, inbound: "ikev2" };
graph.computeView(); out.online = one(DATA.l2tp); out.connsOnline = conns(DATA.l2tp);
// «Через: SOCKS5» in «Куда пойдёт трафик?»
S.live.devices = {}; S.sim = { who: DATA.l2tp, inbound: "socks5" };
graph.computeView(); out.socks = one(DATA.l2tp); out.connsSocks = conns(DATA.l2tp);
// the server's answer through SOCKS5 lights the same single chain
S.sim = null; graph.computeView();
const T = graph.simTrace(DATA.answer); S.trace = T; graph.traceEdges();
out.answer = { edges: Object.keys(T.edges), line: line(Object.keys(T.edges)), inView: Object.keys(T.edges).every((k) => !!S.view.byKey[k]) };
console.log(JSON.stringify(out));
""", {"scheme": scheme, "pptp": pptp, "l2tp": l2tp,
      "answer": self.ok("POST", "/api/gw/trace", {"who": l2tp, "target": "8.8.8.8", "inbound": "socks5"})})
        prof = next(d for n in scheme["nodes"] if n["type"] == "user" for d in n["data"]["devices"] if d["id"] == l2tp)["profile"]
        self.assertEqual(out["connsP"], ["access:%s:pptp" % pptp])                 # one line out of each device
        self.assertEqual(out["connsL"], ["access:%s:l2tp" % l2tp])                 # the first given, not all three
        for k in ("pptp", "l2tp", "online", "socks"):
            self.assertEqual(out[k]["line"], "ok", (k, out[k]["edges"]))
            self.assertTrue(out[k]["inView"], k)
        self.assertEqual(out["l2tp"]["edges"][:4], ["access:%s:l2tp" % l2tp, "flow:l2tp", "pass:" + prof,
                                                    "route:%s:pool" % prof])
        self.assertNotIn("in:socks5", out["l2tp"]["nodes"])
        self.assertNotIn("in:ikev2", out["l2tp"]["nodes"])
        self.assertEqual(out["l2tp"]["rows"]["rules:" + prof], "rest")            # the main row, no other row lit
        self.assertEqual(out["l2tp"]["rows"]["filter"], "pass")
        self.assertEqual((out["online"]["edges"][0], out["connsOnline"]), ("access:%s:ikev2" % l2tp, ["access:%s:ikev2" % l2tp]))
        self.assertEqual((out["socks"]["edges"][0], out["connsSocks"]), ("access:%s:socks5" % l2tp, ["access:%s:socks5" % l2tp]))
        self.assertEqual(out["answer"]["line"], "ok", out["answer"]["edges"])
        self.assertTrue(out["answer"]["inView"])
        self.assertEqual(out["answer"]["edges"][:2], ["access:%s:socks5" % l2tp, "flow:socks5"])

    # «Куда идёт устройство сейчас» — один ответ у схемы и у «Куда пойдёт трафик?» (T-scheme-3.2.16, Н-1): подсветка
    # пути устройства на схеме (traceOf) и подсветка ответа сервера (simTrace) — одни и те же линии, ветви и кандидаты
    ONE_ANSWER = """
const S = graph.S;
const load = (scheme) => { S.base = scheme; S.idx = graph.indexBase(scheme); S.order = {}; S.intents = []; S.sim = null; S.live.devices = {};
  S.idx.users.forEach((u) => { S.order[u.id] = (u.data.devices || []).map((d) => d.id); }); graph.rebuildDraft(); graph.computeView(); };
const pick = (T) => ({ edges: Object.keys(T.edges).sort(), alts: Object.keys(T.alts).sort(), cands: Object.keys(T.cands).sort(),
  nodes: Object.keys(T.nodes).filter((k) => !T.cands[k] && k.indexOf("exit:") === 0).sort(), rows: T.rows });
const one = (did, inbound, answer) => {
  S.sim = inbound ? { who: did, inbound: inbound } : null; graph.computeView();
  const A = graph.traceOf(did), B = graph.simTrace(answer);
  S.sim = null;
  return { scheme: pick(A), trace: pick(B), summary: answer.summary, origin: answer.origin, bal: A.path.balancer || false };
};
"""

    def one_answer_check(self, out):
        for key, r in out.items():
            self.assertEqual(r["scheme"], r["trace"], key)
            self.assertTrue(r["origin"], key)

    def test_the_scheme_and_trace_say_the_same(self):
        """Н-1 на настоящем сервере: для каждого устройства и каждого его входа путь на схеме = ответ «Куда пойдёт
        трафик?»; SOCKS5 — любой живой выход (кандидаты — все живые), ядро — выход по hash или VLESS-балансировщик."""
        for kind, port, proto in (("socks5", 1080, "tcp"), ("ikev2", 4500, "udp")):
            registry.register(DummyInbound(kind=kind, default_port=port, proto=proto))
            ops.inbound_install(self.w.runner, kind)
        socks = ops.device_add(self.w.runner, self.uid, "socks-g3", kind="pc", platform="windows",
                               access=["socks5"])["device"]["id"]
        l2tp = ops.device_add(self.w.runner, self.uid, "l2tp-g3", kind="pc", platform="windows",
                              access=["l2tp", "socks5", "ikev2"])["device"]["id"]
        wg = ops.device_add(self.w.runner, self.uid, "wg-g3", kind="phone", platform="ios", access=["wg"])["device"]["id"]
        snapshot.bump()
        scheme = self.ok("GET", "/api/scheme")
        rows = dict((d["id"], d) for n in scheme["nodes"] if n["type"] == "user" for d in n["data"]["devices"])
        cases, answers = [], {}
        for did in (self.did, socks, l2tp, wg):
            for k in [None] + rows[did]["access"]:
                body = {"who": did, "target": "8.8.8.8"}
                if k:
                    body["inbound"] = k
                answers["%s|%s" % (did, k or "")] = self.ok("POST", "/api/gw/trace", body)
                cases.append([did, k])
        out = node(self.ONE_ANSWER + """
load(DATA.scheme);
const out = {};
for (const [did, k] of DATA.cases) out[did + "|" + (k || "")] = one(did, k, DATA.answers[did + "|" + (k || "")]);
console.log(JSON.stringify(out));
""", {"scheme": scheme, "cases": cases, "answers": answers})
        self.one_answer_check(out)
        # stage 1 (SE §2.2, §2.4): SOCKS5 takes the pin of the device in its group, as the kernel — no balancer
        self.assertFalse(rows[socks]["now"]["balancer"])
        self.assertIn(rows[socks]["now"]["exit"], (self.vid, self.aid))
        self.assertEqual(rows[l2tp]["now_by"]["socks5"]["exit"], rows[l2tp]["now_by"]["l2tp"]["exit"])

    def test_vless_is_decided_by_xray_like_socks5(self):
        """Вход VLESS Reality — вход xray шлюза, как SOCKS5: на настоящем сервере путь устройства только с VLESS и путь
        «Через: VLESS» у устройства L2TP + VLESS — любой живой выход пула (кандидаты — все живые), схема = «Куда
        пойдёт трафик?»; шаг входа говорит «решает xray шлюза»."""
        registry.register(DummyInbound(kind="vless", default_port=443, proto="tcp"))
        ops.inbound_install(self.w.runner, "vless")
        only = ops.device_add(self.w.runner, self.uid, "vless-g3", kind="phone", platform="android",
                              access=["vless"])["device"]["id"]
        both = ops.device_add(self.w.runner, self.uid, "l2tp-vless", kind="pc", platform="windows",
                              access=["l2tp", "vless"])["device"]["id"]
        snapshot.bump()
        scheme = self.ok("GET", "/api/scheme")
        rows = dict((d["id"], d) for n in scheme["nodes"] if n["type"] == "user" for d in n["data"]["devices"])
        cases, answers = [], {}
        for did, k in ((only, None), (only, "vless"), (both, None), (both, "l2tp"), (both, "vless")):
            body = {"who": did, "target": "8.8.8.8"}
            if k:
                body["inbound"] = k
            answers["%s|%s" % (did, k or "")] = self.ok("POST", "/api/gw/trace", body)
            cases.append([did, k])
        out = node(self.ONE_ANSWER + """
load(DATA.scheme);
const out = {};
for (const [did, k] of DATA.cases) out[did + "|" + (k || "")] = one(did, k, DATA.answers[did + "|" + (k || "")]);
S.sim = { who: DATA.both, inbound: "vless", picked: true }; graph.computeView();
out.step = graph.traceOf(DATA.both).steps[1].s;
console.log(JSON.stringify(out));
""", {"scheme": scheme, "cases": cases, "answers": answers, "both": both})
        step = out.pop("step")
        self.one_answer_check(out)
        # этап 1 (SE §2.2, §2.4): VLESS, как SOCKS5 и ядро, — закрепление устройства в его группе, без балансировщика
        self.assertEqual((rows[only]["now"]["balancer"], rows[only]["now"]["inbound"]), (False, "vless"))
        self.assertEqual(rows[both]["now_by"]["vless"]["exit"], rows[both]["now_by"]["l2tp"]["exit"])
        self.assertFalse(out[both + "|vless"]["bal"])
        self.assertIn("graph.trace.socks", step)                                       # «решает xray шлюза»

    def test_scheme_stage_3(self):
        """Этап 3 (T-scheme-3.2.15) на настоящих функциях и настоящем GET /api/scheme:
        Н1 — политика отказа без своего маршрута: унаследованная («по умолчанию: напрямую»), и новая цель сохраняет её;
        Н3 — путь через балансировщик VLESS и через SOCKS5 доходит до выходов: ствол до пула одной линией, ветви — только
             в конце, от пула к кандидатам (alts); к выходу AmneziaWG — одна линия до него;
        Н4 — «закреплено» и «на связи» считаются раздельно (на связи — только живые);
        М3 — «Выделить активные» приглушает пути тех, кто не на связи, постоянные звенья — нет;
        М8 — повтор ключа пула: строка пула, не блок и не линия; Н12 — наложенные карточки расходятся."""
        st = state.load()
        st["gateway"]["routes"]["default"] = {"via": "pool", "fail": "direct"}
        state.save(st)
        snapshot.bump()
        scheme = self.ok("GET", "/api/scheme")
        dup = "exit:%s-3" % self.vid                                             # a repeated key of the pool
        scheme["nodes"].append({"id": dup, "type": "exit", "position": None, "data": {"id": self.vid + "-3", "kind": "bad", "name": "повтор"}})
        next(n for n in scheme["nodes"] if n["id"] == "pool")["data"]["exits"].append(self.vid + "-3")
        scheme["edges"].append({"id": "member:%s-3" % self.vid, "type": "member", "source": "pool", "target": dup})
        out = node("""
const S = graph.S;
S.base = DATA.scheme; S.idx = graph.indexBase(DATA.scheme); S.order = {}; S.intents = [];
S.idx.users.forEach((u) => { S.order[u.id] = (u.data.devices || []).map((d) => d.id); });
graph.rebuildDraft(); graph.computeView();
const out = {};
// Н1
out.inh = graph.inheritedRoute("user", DATA.uid);
out.keptUser = graph.keptFail({ type: "route", user: DATA.uid, via: "pool" });
out.keptDev = graph.keptFail({ type: "route", device: DATA.did, via: "exit:" + DATA.aid });
// Н3: the balancer of the VLESS exits and SOCKS5
const dev = S.idx.dev[DATA.did];
delete dev.now_by;                    // the answer per inbound (scheme now_by) would win over the «now» set here
dev.now = { target: "pool", balancer: true, exits: [DATA.vid] };
let c = graph.chainOf(graph.devPath(DATA.did));
out.vless = { edges: c.edges, alts: c.alts, rows: c.rows, geoms: c.alts.map((k) => { const g = graph.linkGeom(k); return g && [g.s, g.t]; }) };
dev.now = { target: "exit:" + DATA.aid, exit: DATA.aid };
c = graph.chainOf(graph.devPath(DATA.did));
out.awg = { last: c.edges[c.edges.length - 1], alts: c.alts };
// Н4: pinned and online apart
out.on = [graph.onNow([DATA.did]), (S.live.devices[DATA.did] = { online: true, inbound: "wg" }, graph.onNow([DATA.did]))];
// М3
S.onlyActive = true; S.live.devices = {}; graph.computeView();
out.idle = S.view.edges.filter((e) => !graph.edgeLive(e)).map((e) => e.type).sort();
out.liveStatic = S.view.edges.filter((e) => e.type === "member" && !e.devs.length).every((e) => graph.edgeLive(e));
S.onlyActive = false;
// М8
out.dupNode = S.view.nodes.some((n) => n.id === DATA.dup);
out.dupEdge = S.view.edges.some((e) => e.k === "member:" + DATA.vid + "-3");
out.dupRow = graph.poolRows().filter((r) => r.dup).map((r) => r.id);
// Н12
S.ports = { a: { w: 200, h: 120, p: {} }, b: { w: 200, h: 80, p: {} }, c: { w: 200, h: 80, p: {} } };
S.pos = { a: { x: 0, y: 0 }, b: { x: 20, y: 90 }, c: { x: 600, y: 10 } };
out.moved = graph.overlapFix(["a", "b", "c"]); out.posB = S.pos.b;
console.log(JSON.stringify(out));
""", {"scheme": scheme, "uid": self.uid, "did": self.did, "vid": self.vid, "aid": self.aid, "dup": dup})
        self.assertEqual((out["inh"]["fail"], out["inh"]["from"]), ("direct", "default"))
        self.assertEqual((out["keptUser"], out["keptDev"]), ("direct", "direct"))
        self.assertEqual(out["vless"]["alts"], ["member:" + self.vid])
        self.assertEqual(out["vless"]["geoms"], [["pool", "exit:" + self.vid]])      # branches from the pool only
        self.assertEqual(out["vless"]["rows"]["pool"], "vless")
        self.assertFalse([k for k in out["vless"]["edges"] if k.startswith("member:")])
        self.assertEqual((out["awg"]["last"], out["awg"]["alts"]), ("member:" + self.aid, []))
        self.assertEqual(out["on"], [0, 1])
        self.assertIn("conn", out["idle"])
        self.assertTrue(out["liveStatic"])
        self.assertEqual((out["dupNode"], out["dupEdge"], out["dupRow"]), (False, False, [self.vid + "-3"]))
        self.assertEqual(out["moved"], ["b"])
        self.assertEqual(out["posB"]["y"], 133)                                  # under «a» (120) + GAP_Y / 2

    def test_russia_directly_is_open_only_where_it_works(self):
        """С2: «Россия напрямую» of a device: not while the networks are not in the table of the kernel; closed by the
        route «блок» and by dead exits with the fail policy «блок»; «напрямую» at failure sends everything directly."""
        phone = ops.device_add(self.w.runner, self.uid, "Телефон", kind="phone", platform="ios", access=["wg"],
                               via="block")["device"]["id"]
        snapshot.bump()
        scheme = self.ok("GET", "/api/scheme")
        out = node("""
const S = graph.S;
S.base = DATA.scheme; S.idx = graph.indexBase(DATA.scheme); S.order = {}; S.intents = [];
graph.rebuildDraft();
const r = (via, fail) => ({ via: via, fail: fail || "block" });
const out = { off: graph.ruOpen(DATA.did, r("pool")), works0: graph.ruWorks() };
S.idx.node["rules:ru_direct"].data.ru = { state: "ok" };
out.works1 = graph.ruWorks();
out.pool = graph.ruOpen(DATA.did, r("pool"));
out.block = graph.ruOpen(DATA.phone, r("block"));
out.direct = graph.ruOpen(DATA.did, r("direct"));
const dev = S.idx.dev[DATA.did];
dev.effective = { via: "pool", fail: "block", from: "default" }; dev.now = { target: "block" };
out.deadBlock = graph.ruOpen(DATA.did, r("pool", "block"));
dev.effective.fail = "direct"; dev.now = { target: "direct" };
out.deadDirect = graph.ruOpen(DATA.did, r("pool", "direct"));
console.log(JSON.stringify(out));
""", {"scheme": scheme, "did": self.did, "phone": phone})
        self.assertEqual(self.node_of(scheme, "rules:ru_direct")["data"]["ru"]["state"], "no_lists")
        self.assertEqual((out["works0"], out["off"]), (False, {"open": False, "why": "graph.trace.ruNotWorking"}))
        self.assertTrue(out["works1"])
        self.assertEqual(out["pool"], {"open": True, "why": "graph.trace.ruDirect"})
        self.assertEqual(out["block"], {"open": False, "why": "graph.trace.ruBlock"})
        self.assertEqual(out["direct"], {"open": True, "why": "graph.trace.ruRouteDirect"})
        self.assertEqual(out["deadBlock"], {"open": False, "why": "graph.trace.ruFailBlock"})
        self.assertEqual(out["deadDirect"], {"open": True, "why": "graph.trace.ruFailDirect"})

    @staticmethod
    def node_of(doc, nid):
        return next(n for n in doc["nodes"] if n["id"] == nid)

    def test_on_the_map_the_address_and_the_way_back(self):
        """The address of a section: «На схеме» — #scheme?focus=…&from=…, a new entry of the history («Назад» returns);
        «Вернуться к настройкам» — a person: his card (from the window of his route — that window again), a device: its
        «Настроить», from «Схема → Список» — the list; a wrong id is not a focus."""
        out = node("""
const L = app.location, out = {};
L.hash = "#people?focus=u-1a2b3c4d&open=route&from=x"; out.people = app.navState();
L.hash = "#scheme?focus=d-1a2b3c4d"; out.scheme = app.navState();
L.hash = "#scheme?view=list&focus=u-1a2b3c4d"; out.list = app.navState();
L.hash = "#scheme?focus=x-1"; out.bad = app.navState();
L.hash = "#nosuch?focus=u-1a2b3c4d"; out.nosuch = app.navState().page;
L.hash = "#people"; out.plain = app.navState();
L.hash = "#people?show=noexit&focus=u-1a2b3c4d"; out.show = [app.navState().show, app.navState().focus];
L.hash = "#scheme?show=noexit"; out.showElse = app.navState().show;
app.showOnMap("u-1a2b3c4d", "people"); out.toMap = L.hash;
app.showOnMap("d-1a2b3c4d"); out.toMapPlain = L.hash;
const back = [["u-1a2b3c4d", "people"], ["u-1a2b3c4d", "route"], ["d-1a2b3c4d", "people"], ["d-1a2b3c4d", "settings"],
              ["u-1a2b3c4d", "list"], ["d-1a2b3c4d", "list"], ["u-1a2b3c4d", ""]];
out.back = back.map(([id, from]) => { app.backFromMap(id, from); return L.hash; });
console.log(JSON.stringify(out));
""", {})
        # nav2 r1 (UI-A1): show — фильтр «Людей» в адресе (#people?show=…), у других разделов пусто
        self.assertEqual(out["people"], {"page": "people", "tab": "", "focus": "u-1a2b3c4d", "open": "route", "from": "x",
                                         "view": "", "trace": None, "pin": False, "show": ""})
        self.assertEqual(out["scheme"], {"page": "scheme", "tab": "", "focus": "d-1a2b3c4d", "open": "", "from": "", "view": "",
                                         "trace": None, "pin": False, "show": ""})
        self.assertEqual(out["show"], ["noexit", "u-1a2b3c4d"])
        self.assertEqual(out["showElse"], "")
        self.assertEqual(out["list"]["view"], "list")
        self.assertIsNone(out["bad"]["focus"])
        self.assertEqual(out["nosuch"], "overview")
        self.assertEqual(out["plain"], {"page": "people", "tab": "", "focus": None, "open": "", "from": "", "view": "",
                                       "trace": None, "pin": False, "show": ""})
        self.assertEqual(out["toMap"], "#scheme?focus=u-1a2b3c4d&from=people")
        self.assertEqual(out["toMapPlain"], "#scheme?focus=d-1a2b3c4d")
        self.assertEqual(out["back"], ["#people?focus=u-1a2b3c4d", "#people?focus=u-1a2b3c4d&open=route",
                                       "#people?focus=d-1a2b3c4d&open=settings", "#people?focus=d-1a2b3c4d&open=settings",
                                       "#scheme?view=list&focus=u-1a2b3c4d", "#scheme?view=list&focus=d-1a2b3c4d",
                                       "#people?focus=u-1a2b3c4d"])
        app = read("app.js")
        self.assertIn('if (n.page === S.page && PAGES[n.page] && PAGES[n.page].nav && PAGES[n.page].nav(n)) return;', app)
        for need in ('showOnMap(u.id, "people")', 'showOnMap(d.id, "people")', 'showOnMap(u.id, "route")',
                     'showOnMap(d.id, "settings")', "nav: navState(),", "back: backFromMap,"):
            self.assertIn(need, app)

    def test_traffic_simulator(self):
        """«Куда пойдёт трафик?»: the address #scheme?trace=<кто>:<куда> (a new entry of the history, closing — none),
        and the highlight of the REAL answers of POST /api/gw/trace laid onto the view of the real GET /api/scheme: the
        device row, its inbounds, its route line, the row of the rules node (ads | ru | rest), the target, the exit;
        «через выход» lights the line from the profile to the route, «напрямую» and «блок» by the lists do not."""
        from kaskad.gw import exits, net, trace
        from kaskad.rules import apply as rapply
        from tests.rules_helpers import consented, seed_small, small_limits
        with small_limits():
            seed_small()
        st = state.load()
        st["gateway"]["users"][0]["rules"] = "ru_direct"
        state.save(consented(st))          # stage 1: «Россия напрямую» applies with the consent ⚠РФ (R2)
        rapply._save_load_state({"ver": net.ru_wanted(), "ok": True})
        exits.save_snapshot({"at": self.now, "pool": [self.vid, self.aid],
                             "exits": {self.vid: {"alive": True, "since": self.now, "detail": "xray: доступен"},
                                       self.aid: {"alive": True, "since": self.now, "detail": "работает"}}})
        snapshot.bump()
        with mock.patch.object(trace, "resolve", lambda name: (["93.184.216.34"], None)):
            answers = dict((name, self.ok("POST", "/api/gw/trace", {"who": who, "target": target}))
                           for name, who, target in (("force", self.did, "youtube.com"), ("ru", self.did, "77.88.1.1"),
                                                     ("ads", self.did, "ad.mail.ru"), ("new", "-", "example.com"),
                                                     ("person", self.uid, "8.8.8.8")))
        scheme = self.ok("GET", "/api/scheme")
        out = node("""
const S = graph.S;
S.base = DATA.scheme; S.idx = graph.indexBase(DATA.scheme); S.order = {};
S.idx.users.forEach((u) => { S.order[u.id] = (u.data.devices || []).map((d) => d.id); });
graph.rebuildDraft(); graph.computeView();
const sets = (T) => ({ nodes: Object.keys(T.nodes).sort(), devs: Object.keys(T.devs).sort(), edges: Object.keys(T.edges).sort(),
                       rows: T.rows, sim: T.sim });
const out = {};
for (const k of Object.keys(DATA.answers)) out[k] = sets(graph.simTrace(DATA.answers[k]));
out.refs = ["d-1a2b3c4d:youtube.com", "u-1a2b3c4d:8.8.8.8", "-:ya.ru", "x-1:ya.ru", "d-1a2b3c4d:", "d-1a2b3c4d:a b", ""].map(graph.simRef);
const L = app.location;
L.hash = "#scheme?trace=d-1a2b3c4d:youtube.com"; out.nav = app.navState();
L.hash = "#scheme?trace=x-1:ya.ru"; out.navBad = app.navState().trace;
L.hash = "#scheme"; app.traceNav("d-1a2b3c4d:youtube.com"); out.to = L.hash;
app.traceNav("-:ya.ru"); out.toNew = L.hash;
// проверку закрыли, путь остался чипом (Н-3): адрес хранит это без новой записи истории; перезагрузка — снова чип
const hist = [];
globalThis.history = { replaceState: (a, b, h) => { hist.push(h); L.hash = h; } };
app.traceNav("d-1a2b3c4d:youtube.com", true); out.pinned = [L.hash, hist.length];
out.navPin = app.navState();
app.traceNav(null); out.unpinned = L.hash;
L.hash = "#scheme?pin=1"; out.pinAlone = app.navState().pin;
console.log(JSON.stringify(out));
""", {"scheme": scheme, "answers": answers})
        f, ru, ads, new, person = out["force"], out["ru"], out["ads"], out["new"], out["person"]
        prof = "rules:ru_direct"
        self.assertEqual(answers["force"]["verdict"], "exit")
        self.assertEqual(f["devs"], [self.did])
        for nid in (self.uid, "in:wg", "filter", prof, "pool"):
            self.assertIn(nid, f["nodes"])
        self.assertNotIn("in:l2tp", f["nodes"])                               # ONE inbound: the first given
        # one chain: device → wg → filter → «Россия напрямую» → pool (→ its exit, or the VLESS balancer row)
        for k in ("access:%s:wg" % self.did, "flow:wg", "pass:ru_direct", "route:ru_direct:pool"):
            self.assertIn(k, f["edges"])
        self.assertNotIn("access:%s:l2tp" % self.did, f["edges"])
        self.assertEqual((f["rows"]["filter"], f["rows"][prof], f["sim"]), ("pass", "rest", True))
        self.assertEqual(answers["ru"]["verdict"], "direct")
        self.assertEqual(ru["edges"], sorted(["access:%s:wg" % self.did, "flow:wg", "pass:ru_direct", "cat:ru_direct:ru"]))
        self.assertIn("direct", ru["nodes"])
        self.assertNotIn("pool", ru["nodes"])                                  # the route is not taken
        self.assertEqual(ru["rows"], {"filter": "pass", prof: "ru"})
        self.assertEqual(answers["ads"]["verdict"], "block")
        self.assertEqual(ads["edges"], sorted(["access:%s:wg" % self.did, "flow:wg"]))   # ends at the row of the filter (М2)
        self.assertIn("filter", ads["nodes"])
        self.assertNotIn("block", ads["nodes"])
        self.assertEqual(ads["rows"], {"filter": "ads"})
        self.assertEqual(new["devs"], [])                                     # a new device: no row, no inbound
        self.assertNotIn(self.uid, new["nodes"])
        self.assertIn("rules:" + answers["new"]["profile"]["profile"], new["nodes"])
        self.assertFalse([k for k in new["edges"] if k.startswith("access:")])
        self.assertEqual(person["devs"], [])
        self.assertIn(self.uid, person["nodes"])
        self.assertIn("pass:ru_direct", person["edges"])
        self.assertEqual(out["refs"], [{"who": "d-1a2b3c4d", "target": "youtube.com"},
                                       {"who": "u-1a2b3c4d", "target": "8.8.8.8"}, {"who": "-", "target": "ya.ru"},
                                       None, None, None, None])
        self.assertEqual(out["nav"]["trace"], "d-1a2b3c4d:youtube.com")
        self.assertIsNone(out["navBad"])
        self.assertEqual(out["to"], "#scheme?trace=d-1a2b3c4d:youtube.com")
        self.assertEqual(out["toNew"], "#scheme?trace=-:ya.ru")
        self.assertEqual(out["pinned"], ["#scheme?trace=d-1a2b3c4d:youtube.com&pin=1", 1])
        self.assertEqual((out["navPin"]["trace"], out["navPin"]["pin"]), ("d-1a2b3c4d:youtube.com", True))
        self.assertEqual(out["unpinned"], "#scheme")
        self.assertFalse(out["pinAlone"])
        g = read("graph.js")
        self.assertIn("if (simRef(nav.trace) && nav.pin) { pinNav(simRef(nav.trace)); return; }", g)
        app = read("app.js")
        self.assertIn("onTrace: traceNav,", app)


# fetch of the page through the proxy listener: node's http on the unix socket, the headers nginx adds
FETCH_NGINX = """
const http = require("http");
const urls = [];
app.setFetch((url, init) => new Promise((resolve, reject) => {
  urls.push(url);
  const headers = Object.assign({}, init.headers, { Host: "localhost", "X-Real-IP": "198.51.100.7",
    "X-Forwarded-Proto": "https", "X-Forwarded-Host": "localhost" });
  if (DATA.cookie) headers.Cookie = DATA.cookie;
  if (init.body) headers["Content-Length"] = Buffer.byteLength(init.body);
  const req = http.request({ socketPath: DATA.sock, path: url, method: init.method, headers }, (res) => {
    const chunks = [];
    res.on("data", (c) => chunks.push(c));
    res.on("end", () => { const text = Buffer.concat(chunks).toString("utf8");
      resolve({ status: res.statusCode, ok: res.statusCode >= 200 && res.statusCode < 300, text: async () => text }); });
  });
  req.on("error", reject);
  if (init.body) req.write(init.body);
  req.end();
}));
"""


@unittest.skipUnless(shutil.which("node"), "нет node: функции app.js и user.js не запустить")
@unittest.skipUnless(shutil.which("node"), "нет node")
class RouteSummaryTest(unittest.TestCase):
    """Н-6, Н-7: маршрут на «Людях» применяется кнопкой со сводкой — routeSummary из app.js (настоящая функция): было →
    станет с унаследованным; свой = унаследованному — «путь не меняется», без предупреждений; «блок» — без интернета;
    «мимо выхода» — только когда путь правда стал таким; снятие — к тому, что унаследуется."""

    def test_summary(self):
        js = read("app.js")
        script = """
const DASH = "—";
const t = (key, vars) => key + (vars ? " " + JSON.stringify(vars) : "");
const exitName = (id) => "Вена";
const isObj = (x) => !!x && typeof x === "object" && !Array.isArray(x);
const R = { people: { loaded: true, data: { viaFails: { "exit:e-9": "direct" } } } };
%s
%s
%s
%s
%s
const S = (b, rt, inh, whom) => routeSummary(b, rt, inh, whom);
const pool = (fail, from) => ({ via: "pool", fail: fail, from: from });
const out = {
  same: S(pool("block", "user"), { via: "pool", fail: "block" }, pool("block", "user"), "device"),
  sameFail: S(pool("direct", "default"), { via: "pool" }, null, "user"),
  leak: S(pool("block", "user"), { via: "pool", fail: "direct" }, pool("block", "user"), "device"),
  leakSame: S(pool("direct", "user"), { via: "pool", fail: "direct" }, pool("direct", "user"), "device"),
  block: S(pool("block", "user"), { via: "block", fail: "block" }, null, "device"),
  blockUser: S(pool("block", "default"), { via: "block", fail: "block" }, null, "user"),
  drop: S({ via: "direct", fail: "block", from: "device" }, { via: "inherit" }, pool("block", "user"), "device"),
  dropSame: S(pool("block", "device"), { via: "inherit" }, pool("block", "user"), "device"),
  exit: S(pool("block", "default"), { via: "exit:e-1", fail: "block" }, null, "default"),
  // REV-s1-b4 З-1: отказ — как действует (свой fail или группы): группа выхода e-9 шлёт напрямую
  groupLeak: S(pool("block", "default"), { via: "exit:e-9", fail: "block" }, null, "default"),
  effText: routeText({ via: "pool", fail: "block", failEff: "direct" }, true),
  ownText: routeText({ via: "pool", fail: "block", failEff: "block" }, true),
  groupText: routeText({ via: "group:g-123456", fail: "block", failEff: "direct", groupName: "Дом" }, true),
  // этап 1 (SE §7.3): «пул» — группа «Основная»: имя группы от сервера (group_name), без политики — «Пул выходов»
  poolGroupText: routeText({ via: "pool", fail: "block", groupName: "Основная" }, false),
  poolOldText: routeText({ via: "pool", fail: "block" }, false),
};
console.log(JSON.stringify(out));
""" % ("\n".join(jsparts.const(js, n) for n in ("routeGroups", "routeGroupOf", "routeGroupLabel")),
       jsparts.function(js, "routeThrough"), jsparts.function(js, "effFail"), jsparts.function(js, "routeText"),
       jsparts.function(js, "routeSummary"))
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
            f.write(script)
            path = f.name
        try:
            res = subprocess.run(["node", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        finally:
            os.unlink(path)
        self.assertEqual(res.returncode, 0, res.stderr.decode("utf-8", "replace"))
        o = json.loads(res.stdout.decode("utf-8"))
        self.assertEqual(o["poolGroupText"], 'route.group {"name":"Основная"}')
        self.assertEqual(o["poolOldText"], "route.pool")
        self.assertTrue(o["same"]["same"])
        self.assertEqual((o["same"]["warn"], o["same"]["danger"]), ("", False))
        self.assertIn("route.sumSame", o["same"]["list"][2])
        self.assertIn("route.from.user", o["same"]["list"][0])                   # было: как у человека
        self.assertIn("route.sumOwnDev", o["same"]["list"][1])                   # станет: свой у устройства
        self.assertTrue(o["sameFail"]["same"])                                   # своя цель сохраняет унаследованную политику
        self.assertEqual(o["leak"]["warn"], "route.sumLeakFail")
        self.assertEqual((o["leakSame"]["warn"], o["leakSame"]["same"]), ("", True))   # ничего не ухудшилось — молчим
        self.assertEqual((o["block"]["warn"], o["block"]["danger"]), ("route.sumBlock", True))
        self.assertEqual(o["blockUser"]["warn"], "route.sumBlockUser")
        self.assertIn("route.from.user", o["drop"]["list"][1])                   # снятие — к тому, что унаследуется
        self.assertIn("route.pool", o["drop"]["list"][1])
        self.assertFalse(o["drop"]["same"])
        self.assertIn("route.sumSameInh", o["dropSame"]["list"][2])
        self.assertIn("route.from.default", o["exit"]["list"][1])
        self.assertEqual(o["exit"]["warn"], "")
        self.assertEqual(o["groupLeak"]["warn"], "route.sumLeakFail")             # группа цели шлёт напрямую
        self.assertIn("route.failDirectShort", o["effText"])                     # свой block, группа — direct
        self.assertNotIn("route.failDirectShort", o["ownText"])
        self.assertIn('route.group {"name":"Дом"}', o["groupText"])              # имя группы, не g-…
        self.assertIn("route.failDirectShort", o["groupText"])
        for k in ("route.sumTitle", "route.sumBefore", "route.sumAfter", "route.sumBlockDefault"):
            self.assertIn('"%s"' % k, read("i18n-ru.js"))
        self.assertIn('"route.from.default": "по умолчанию узла"', read("i18n-ru.js"))
        # карточка устройства: маршрут — только по кнопке; без выбора кнопка неактивна
        card = js[js.index("// маршрут — только по кнопке «Применить»"):js.index("// профиль правил")]
        self.assertIn("confirmRoute(d.name, d.routeEff", card)
        # без выбора неактивна; выбор, сохранённый вне перерисовки (строка 19), — активна (поведение — k3_writes_check route-keep)
        self.assertIn("applyBtn.disabled = !rsel.pick || unchanged(route.get());", card)
        self.assertNotIn("dis: true", card)                                       # o.dis снимает и обработчик щелчка
        self.assertNotIn("API.devices.route", card[card.index("onChange"):])


@unittest.skipUnless(shutil.which("node"), "нет node")
class FitTraceTest(unittest.TestCase):
    """Н-2: путь при открытой панели «Куда пойдёт» — не мельче 55 %; не влезает — виден хвост (правила → цель →
    кандидаты), прижатый к правому краю видимого слева от панели. Настоящие fitTrace и boxOf из graph.js."""

    def test_tail_is_seen(self):
        js = read("graph.js")
        script = """
%s
%s
var OWN = Object.prototype.hasOwnProperty, NARROW = 680, LAYER = %s;
var S = { V: null, pos: {}, idx: { node: {} }, dom: { stage: { clientWidth: 970, clientHeight: 540 }, insp: { hidden: false, offsetWidth: 380 }, root: { clientWidth: 970 } } };
function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
function zmin() { return 0.3; }
function nodeW() { return 240; }
function nodeH() { return 120; }
var fitted = null; function fitNodes(ids) { fitted = ids; } function applyView() {}
function traceIds(T) { return Object.keys(T.nodes).concat(Object.keys(T.cands || {})); }
var kinds = [["u", "user"], ["in:wg", "inbound"], ["filter", "filter"], ["rules:ru_direct", "rules"], ["pool", "pool"], ["exit:a", "exit"], ["exit:b", "exit"]];
kinds.forEach(function (x, i) { S.idx.node[x[0]] = { type: x[1] }; S.pos[x[0]] = { x: Math.min(i, 5) * 300, y: x[0] === "exit:b" ? 200 : 0 }; });
var T = { nodes: { u: 1, "in:wg": 1, filter: 1, "rules:ru_direct": 1, pool: 1 }, cands: { "exit:a": 1, "exit:b": 1 } };
fitTrace(T);
var aw = 970 - 380 - 24, V = S.V, seen = {};
Object.keys(S.pos).forEach(function (id) { var x1 = S.pos[id].x * V.k + V.x, x2 = (S.pos[id].x + 240) * V.k + V.x; seen[id] = x1 >= 0 && x2 <= aw; });
var small = { nodes: { filter: 1, pool: 1 }, cands: {} }; fitted = null; fitTrace(small);
console.log(JSON.stringify({ k: V.k, seen: seen, smallFitted: fitted }));
""" % (jsparts.function(js, "fitTrace"), jsparts.function(js, "boxOf"), "{ user: 0, inbound: 1, filter: 2, rules: 3, pool: 4, direct: 4, block: 4, exit: 5 }")
        script = jsparts.const(js, "MIN_TRACE_K", keyword="var") + "\n" + script
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
            f.write(script)
            path = f.name
        try:
            res = subprocess.run(["node", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        finally:
            os.unlink(path)
        self.assertEqual(res.returncode, 0, res.stderr.decode("utf-8", "replace"))
        o = json.loads(res.stdout.decode("utf-8"))
        self.assertEqual(o["k"], 0.55)
        for nid in ("rules:ru_direct", "pool", "exit:a", "exit:b"):                # хвост: правила → цель → кандидаты
            self.assertTrue(o["seen"][nid], nid)
        self.assertFalse(o["seen"]["u"])                                           # начало названо в панели
        self.assertEqual(sorted(o["smallFitted"]), ["filter", "pool"])            # влезает — вписан целиком
        self.assertIn("var LAYER = { user: 0, inbound: 1, filter: 2, rules: 3, pool: 4, direct: 4, block: 4, exit: 5, "
                      "ru_group: 4 };", js)                       # российская группа — в колонке целей (T15 Q6)
        css = read("graph.css")
        self.assertIn("grid-template-columns: minmax(0, 1fr)", css)               # холст не шире карточки (Н-4)
        self.assertIn(".kg.is-narrow .kg-insp.kg-insp--sim { max-height: 55vh;", css)


@unittest.skipUnless(shutil.which("node"), "нет node")
@unittest.skipUnless(shutil.which("node"), "нет node: макет не проверить")
class MockExitKeysTest(unittest.TestCase):
    """3.2.21: the mock (?mock=1) adds and removes keys of exits as the server does: a preview by content, a result per
    key, 409 for a key in the pool, 400 for no keys, 404 for a removed exit."""

    def test_the_mock_adds_and_removes_keys(self):
        from tests.test_gw_exits import conf_of
        out = node(r"""
globalThis.window = globalThis;
globalThis.location = new URL("http://127.0.0.1/index.html?mock=1&lat=0&none=exits");
require(DATA.mock);
const call = async (m, p, body) => { const r = await window.KASKAD_MOCK.fetch(p, { method: m, headers: { "X-Kaskad-Admin": "1" },
  body: body ? JSON.stringify(body) : undefined }); return [r.status, await r.json()]; };
const out = {};
const two = DATA.vless + "\n" + DATA.vless2;
out.empty = (await call("GET", "/api/gw/exits"))[1];
out.pre = (await call("POST", "/api/gw/exits/preview", { key: DATA.wg }))[1].items.map((x) => [x.title, x.where, x.in_pool]);
out.preV = (await call("POST", "/api/gw/exits/preview", { key: DATA.awg15 }))[1].items.map((x) => x.title);
out.add = (await call("POST", "/api/gw/exits", { key: DATA.vless }))[1];
out.two = (await call("POST", "/api/gw/exits", { key: two }))[1].results.map((x) => x.result);
out.dup = await call("POST", "/api/gw/exits", { key: DATA.vless });
out.bad = await call("POST", "/api/gw/exits", { key: "привет" });
// этап 1 (SE §7.9): удаление сверяет версию политики — без rev 400 rev_required, с rev чтения — удалено, повтор — 404 gone
out.norev = await call("DELETE", "/api/gw/exits/" + out.add.added[0].id);
const rev = (await call("GET", "/api/gw/exits"))[1].rev;
out.del = await call("DELETE", "/api/gw/exits/" + out.add.added[0].id + "?rev=" + rev);
out.gone = await call("DELETE", "/api/gw/exits/" + out.add.added[0].id + "?rev=" + out.del[1].rev);
console.log(JSON.stringify(out));
process.exit(0);
""", {"mock": os.path.join(STATIC, "dev-mock.js"), "wg": conf_of("wg"), "awg15": conf_of("1.5"),
      "vless": "vless://00000000-0000-0000-0000-000000000000@198.51.100.9:443?security=reality#Тест",
      "vless2": "vless://00000000-0000-0000-0000-000000000001@198.51.100.10:443?security=reality#Тест2"})
        self.assertEqual((out["empty"]["add"]["item"], out["empty"]["offline"]), (3, False))       # mock: fail «direct»
        self.assertEqual(out["pre"], [["WireGuard", "198.51.100.31:51820", False]])
        self.assertEqual(out["preV"], ["AmneziaWG 1.5"])
        self.assertEqual(out["two"], ["in_pool", "added"])
        self.assertEqual((out["dup"][0], out["dup"][1]["error"]), (409, "Такой ключ уже есть в пуле."))
        self.assertEqual(out["bad"][0], 400)
        self.assertEqual((out["norev"][0], out["norev"][1]["code"]), (400, "rev_required"))
        self.assertEqual((out["del"][0], out["del"][1]["left"], out["gone"][0]), (200, 1, 404))
        self.assertEqual((out["gone"][1]["code"], out["gone"][1]["data"]["gone"]), ("gone", True))


@unittest.skipUnless(shutil.which("node"), "нет node: макет не проверить")
class MockSubsBackupTest(unittest.TestCase):
    """Подписки выходов и копия настроек: normExits читает subs и source ответа GET gw/exits (без подписок — пустой
    список, у ключа вручную — null); макет (?mock=1) отвечает как сервер — подписка приносит свои ключи, повтор — 409,
    «оставить ключи» оставляет их в пуле; неверный пароль копии — 400 с текстом сервера, препятствие — 409."""

    def test_norm_exits_reads_subscriptions(self):
        out = node(r"""
const a = app.normExits(DATA.with);
const b = app.normExits(DATA.without);
console.log(JSON.stringify({ subs: a.subs, src: a.rows.map((r) => r.source), none: b.subs, src0: b.rows[0].source }));
""", {"with": {"exits": [{"id": "e-41a19b9cbe", "kind": "vless", "source": {"sub": "s-1a2b3c", "name": "sub.example.net"}},
                         {"id": "e-7c2d8e0f13", "kind": "awg", "source": None}],
               "subs": [{"id": "s-1a2b3c", "n": 1, "name": "sub.example.net", "domain": "sub.example.net", "auto": True,
                         "keys": 1, "exits": ["e-41a19b9cbe"], "count": 1, "full": 0, "last_ok": 1791550000,
                         "error": "", "stale": False, "text": "обновлена 3 ч назад: ключей 1"}, {"bad": 1}]},
      "without": {"exits": [{"id": "e-41a19b9cbe", "kind": "vless"}]}})
        self.assertEqual(len(out["subs"]), 1)
        s = out["subs"][0]
        self.assertEqual((s["id"], s["name"], s["auto"], s["keys"], s["exits"], s["stale"], s["text"]),
                         ("s-1a2b3c", "sub.example.net", True, 1, ["e-41a19b9cbe"], False, "обновлена 3 ч назад: ключей 1"))
        self.assertEqual(out["src"], [{"sub": "s-1a2b3c", "name": "sub.example.net"}, None])
        self.assertEqual((out["none"], out["src0"]), ([], None))

    def test_the_mock_keeps_subscriptions_and_copies(self):
        out = node(r"""
globalThis.window = globalThis;
globalThis.location = new URL("http://127.0.0.1/index.html?mock=1&lat=0");
require(DATA.mock);
const call = async (m, p, body) => { const r = await window.KASKAD_MOCK.fetch(p, { method: m, headers: { "X-Kaskad-Admin": "1" },
  body: body ? JSON.stringify(body) : undefined }); return [r.status, await r.json()]; };
const out = {};
out.before = (await call("GET", "/api/gw/exits"))[1].subs.map((x) => x.id);
const add = (await call("POST", "/api/gw/exit-subs", { url: "https://ga.example.org/s-x/all.vpn", auto: false }))[1];
out.add = [add.sub.name, add.sub.auto, add.sub.keys, add.result.added];
out.dup = (await call("POST", "/api/gw/exit-subs", { url: "https://ga.example.org/s-x/all.vpn" }))[0];
out.bad = (await call("POST", "/api/gw/exit-subs", { url: "ftp://x" }))[0];
const ex = (await call("GET", "/api/gw/exits"))[1];
out.src = ex.exits.filter((e) => e.source && e.source.sub === add.sub.id).length;
out.url = JSON.stringify(ex).indexOf("s-x/all.vpn") >= 0;
out.one = (await call("POST", "/api/gw/exit-subs/" + add.sub.id + "/refresh"))[1].result.ok;
out.all = (await call("POST", "/api/gw/exit-subs/refresh"))[1].all_ok;
out.auto = (await call("PUT", "/api/gw/exit-subs/" + add.sub.id, { auto: true }))[1].sub.auto;
const n = ex.exits.length;
out.keep = (await call("DELETE", "/api/gw/exit-subs/" + add.sub.id + "?keep_keys=1"))[1].removed;
out.left = (await call("GET", "/api/gw/exits"))[1].exits.length === n;
out.gone = (await call("DELETE", "/api/gw/exit-subs/" + add.sub.id))[0];
const sum = (await call("GET", "/api/backup/summary"))[1];
out.parts = sum.parts;
const exp = (await call("POST", "/api/backup/export", { password: "secret-123", parts: ["gateway", "exits"] }))[1];
out.exp = [exp.parts, /\.kbk$/.test(exp.name), exp.file.indexOf("KASKAD-BACKUP 1") === 0];
out.short = (await call("POST", "/api/backup/export", { password: "short" }))[0];
out.wrong = await call("POST", "/api/backup/preview", { file: exp.file, password: "wrong" });
const pv = (await call("POST", "/api/backup/preview", { file: exp.file, password: "secret-123" }))[1].preview;
out.pv = [pv.ok, pv.parts.map((r) => r.part), pv.address.kind];
out.block = (await call("POST", "/api/backup/import", { file: exp.file + "blocker", password: "secret-123" }))[0];
out.undo0 = (await call("POST", "/api/backup/undo", {}))[0];
out.imp = (await call("POST", "/api/backup/import", { file: exp.file, password: "secret-123" }))[1].ok;
out.undo = (await call("POST", "/api/backup/undo", {}))[1].ok;
console.log(JSON.stringify(out));
process.exit(0);
""", {"mock": os.path.join(STATIC, "dev-mock.js")})
        self.assertEqual(out["before"], ["s-1a2b3c", "s-4d5e6f"])
        self.assertEqual(out["add"], ["ga.example.org", False, 2, 2])
        self.assertEqual((out["dup"], out["bad"]), (409, 400))
        self.assertEqual(out["src"], 2)
        self.assertFalse(out["url"])                                    # the address is never answered
        self.assertEqual((out["one"], out["all"], out["auto"]), (True, False, True))     # s-4d5e6f fails
        self.assertEqual((out["keep"], out["left"], out["gone"]), (0, True, 404))
        self.assertEqual(out["parts"], ["gateway", "exits", "forwards", "site", "bot", "mcp", "panel", "github"])
        self.assertEqual(out["exp"], [["gateway", "exits"], True, True])
        self.assertEqual(out["short"], 400)
        self.assertEqual((out["wrong"][0], out["wrong"][1]["error"]), (400, "Неверный пароль копии."))
        self.assertEqual(out["pv"], [True, ["gateway", "exits"], "ip"])
        self.assertEqual((out["block"], out["undo0"]), (409, 400))
        self.assertEqual((out["imp"], out["undo"]), (True, True))


class MockOneAnswerTest(unittest.TestCase):
    """Н-1 на макете (?mock=1): тот же сверочный прогон — схема макета и «Куда пойдёт трафик?» макета говорят одно для
    каждого устройства и каждого его входа (макет — копия сервера, разойтись он не должен)."""

    def test_the_mock_says_the_same(self):
        out = node(JsContractTest.ONE_ANSWER + """
globalThis.window = globalThis;
globalThis.location = new URL("http://127.0.0.1/index.html?mock=1&lat=0");
require(DATA.mock);
const call = async (m, p, body) => (await window.KASKAD_MOCK.fetch(p, { method: m, headers: { "X-Kaskad-Admin": "1" },
  body: body ? JSON.stringify(body) : undefined })).json();
const scheme = await call("GET", "/api/scheme");
load(scheme);
const out = {};
for (const u of scheme.nodes.filter((n) => n.type === "user")) for (const d of u.data.devices) {
  if (!d.active) continue;
  for (const k of [null].concat(d.access)) {
    const ans = await call("POST", "/api/gw/trace", k ? { who: d.id, target: "8.8.8.8", inbound: k } : { who: d.id, target: "8.8.8.8" });
    if (!ans.ok) continue;                                     // вход выключен или не установлен
    out[d.id + "|" + (k || "")] = Object.assign(one(d.id, k, ans), { now: d.now });
  }
}
console.log(JSON.stringify(out));
process.exit(0);
""", {"mock": os.path.join(STATIC, "dev-mock.js")})
        self.assertGreater(len(out), 8)
        for key, r in out.items():
            self.assertEqual(r["scheme"], r["trace"], key)
            self.assertTrue(r["origin"], key)
        socks = out["d-3c3c0003|"]
        self.assertEqual((socks["now"]["balancer"], socks["now"]["exit"]), ("any", None))
        self.assertEqual(len(socks["scheme"]["cands"]), 2)                       # Вена и Хельсинки — живые
        self.assertIn("выберет автоматически", socks["summary"])
        vless = out["d-3c3c0004|"]                                               # вход VLESS — как SOCKS5: решает xray
        self.assertEqual((vless["now"]["balancer"], vless["now"]["exit"], vless["now"]["inbound"]), ("any", None, "vless"))
        self.assertEqual(vless["scheme"]["cands"], socks["scheme"]["cands"])
        self.assertTrue(vless["now"]["why"].startswith("VLESS:"), vless["now"]["why"])


class PrefixJsTest(ProxyCase):
    """D8: the real request() of app.js and the scheme's call under the secret prefix — against the real proxy
    listener; without a prefix the addresses are the old ones."""

    def test_the_addresses_follow_the_page(self):
        for pathname, base in (("/", "/"), ("/index.html", "/"), ("/app-test/", "/app-test/"),
                               ("/app-test/index.html", "/app-test/"), ("/a/b/", "/a/b/")):
            doc = node("""
const urls = [];
app.setFetch(async (url) => { urls.push(url); return { status: 200, ok: true, text: async () => '{"ok": true}' }; });
await app.request("GET", "/api/overview");
await graph.call("GET", "/scheme");
console.log(JSON.stringify({ base: app.BASE, urls, events: app.apiUrl("/api/events") }));
""", {}, pathname=pathname)
            self.assertEqual(doc, {"base": base, "urls": [base + "api/overview", base + "api/scheme"],
                                   "events": base + "api/events"}, pathname)

    def test_request_under_the_prefix_against_the_server(self):
        sock = self.proxy_on(path="app-test")
        auth.set_password("правильный-пароль")
        doc = node(FETCH_NGINX + """
const out = { logins: 0 };
try { await app.request("GET", "/api/overview"); } catch (e) { out.first = e.status; }
out.logins = app.out.logins;
const r = await app.request("POST", "/api/login", { password: "правильный-пароль" }, { noAuth: true });
out.login = r.ok;
out.urls = urls;
console.log(JSON.stringify(out));
""", {"sock": sock}, pathname="/app-test/")
        self.assertEqual((doc["first"], doc["logins"], doc["login"]), (401, 1, True))
        self.assertEqual(doc["urls"], ["/app-test/api/overview", "/app-test/api/login"])
        tok = self.cookie_of(self.pcall("POST", "/app-test/api/login", {"password": "правильный-пароль"}))
        doc = node(FETCH_NGINX + """
const out = {};
out.session = await app.request("GET", "/api/session");
out.overview = (await app.request("GET", "/api/overview")).ok;
const g = await graph.call("GET", "/scheme");
out.scheme = [g.ok, g.status];
out.urls = urls;
console.log(JSON.stringify(out));
""", {"sock": sock, "cookie": "%s=%s" % (auth.COOKIE, tok)}, pathname="/app-test/")
        self.assertEqual((doc["session"]["auth"], doc["session"]["via"], doc["session"]["scheme"]),
                         (True, "password", "https"))
        self.assertTrue(doc["overview"])
        self.assertEqual(doc["scheme"], [True, 200])
        self.assertEqual(doc["urls"], ["/app-test/api/session", "/app-test/api/overview", "/app-test/api/scheme"])

    def test_the_files_of_the_page_of_a_person(self):
        js = read("user.js")
        script = "%s\nconst out = {};\nfor (const p of DATA) { location.pathname = p; out[p] = siteBase(); }\n" \
                 "console.log(JSON.stringify(out));\n" % jsparts.function(js, "siteBase")
        tok = "A" * 43
        paths = ["/p/" + tok, "/p/" + tok + "/", "/app-test/p/" + tok, "/a/b/p/" + tok + "/"]
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
            f.write("const location = { pathname: '/' };\nconst DATA = %s;\n%s" % (json.dumps(paths), script))
            path = f.name
        try:
            res = subprocess.run(["node", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        finally:
            os.unlink(path)
        self.assertEqual(json.loads(res.stdout.decode()), {paths[0]: "", paths[1]: "", paths[2]: "/app-test",
                                                           paths[3]: "/a/b"}, res.stderr)
        self.assertIn("o.file = siteBase() + o.file", js)                 # 3.3.2: the file of an item goes to give.js so



@unittest.skipUnless(shutil.which("node"), "нет node: функции app.js и graph.js не запустить")
class RfJsContractTest(PanelCase):
    """R4: the tick ⚠РФ — the same node of the fake server as JsContractTest."""
    def setUp(self):
        PanelCase.setUp(self)
        self.gateway(("wg",))
        self.did = ops.device_add(self.w.runner, self.uid, "Роутер", kind="router", platform="keenetic",
                                  access=["wg"])["device"]["id"]
        snapshot.bump()

    answer = JsContractTest.answer

    def test_rf_tick_against_the_server(self):
        """R4 (RF §5.1, §9 «JS-контракт»): the refusal ⚠РФ of the server reaches the page whole — app.request gives
        ``e.data.need`` and the block, the scheme's call gives ``r.data.data.need`` (doApply reads it); the dry run of
        the scheme stays valid with ``results[].need``; the body the page then sends (rfTick; graph withRf) is accepted
        by the server and writes the consent of the panel (``via`` panel / scheme)."""
        from kaskad.i18n import tr
        ru = {"type": "rules", "device": self.did, "profile": "ru_direct"}
        answers = {
            "dry": self.answer("POST", "/api/scheme/apply", {"intents": [ru], "dry_run": True}),
            "scheme": self.answer("POST", "/api/scheme/apply", {"intents": [ru]}),
            "rules": self.answer("PUT", "/api/rules/profile", {"target": "device", "id": self.did, "profile": "ru_direct"}),
        }
        self.assertEqual([answers[k]["status"] for k in ("dry", "scheme", "rules")], [200, 409, 409])
        app_js, graph_js = read("app.js"), read("graph.js")
        cut = "\n".join([jsparts.const(app_js, "isObj"), jsparts.const(app_js, "arr"), jsparts.const(app_js, "rfNeedOf"),
                         jsparts.const(app_js, "rfTick")])
        gcut = jsparts.function(graph_js, "withRf") + "\nfunction rfSync() {}"     # rfSync — tests/test_panel_rf_js.py
        out = node(FETCH + cut + "\nconst OWN = Object.prototype.hasOwnProperty;\nconst S = { rfTick: false, rfOrgs: null };\n"
                   + gcut + """
const out = {};
answer(DATA.rules);
try { await app.request("PUT", "/api/rules/profile", {}); out.rules = null; }
catch (e) { const n = rfNeedOf(e.data); out.rules = { status: e.status, need: n && n.need, orgs: n && n.orgs, block: n && n.block, tick: rfTick(n) }; }
answer(DATA.scheme);
const r = await graph.call("POST", "/scheme/apply", {});
out.scheme = { ok: r.ok, need: r.data && r.data.data && r.data.data.need };
answer(DATA.dry);
const d = await graph.call("POST", "/scheme/apply", {});
out.dry = { valid: d.data.valid, ok: d.data.results[0].ok, need: d.data.results[0].need.need };
const list = [{ type: "rules", device: "d-1", profile: "ru_direct" }, { type: "route", device: "d-1", via: "block" }];
out.noTick = withRf(list);
S.rfTick = true; S.rfOrgs = d.data.results[0].need.orgs;
out.ticked = withRf(list);
out.same = list[0].confirm_not_rf === undefined;
console.log(JSON.stringify(out));
""", answers)
        rules = json.loads(answers["rules"]["text"])
        self.assertEqual(out["rules"]["status"], 409)
        self.assertEqual(out["rules"]["need"], "rf_consent")
        self.assertEqual(out["rules"]["block"], rules["data"]["block"])
        self.assertEqual(out["rules"]["tick"], {"confirm_not_rf": True, "orgs": rules["data"]["orgs"]})
        self.assertEqual(out["scheme"], {"ok": False, "need": "rf_consent"})
        self.assertEqual(out["dry"], {"valid": True, "ok": True, "need": "rf_consent"})
        self.assertEqual(out["noTick"][0], {"type": "rules", "device": "d-1", "profile": "ru_direct"})
        self.assertEqual(out["ticked"][0]["confirm_not_rf"], True)
        self.assertEqual(out["ticked"][0]["orgs"], rules["data"]["orgs"])
        self.assertNotIn("confirm_not_rf", out["ticked"][1])              # only the rules intents carry it
        self.assertTrue(out["same"])                                       # the draft itself is not changed
        # what the page sends next — accepted, the consent is the panel's
        self.ok("PUT", "/api/rules/profile", dict({"target": "device", "id": self.did, "profile": "ru_direct"},
                                                  **out["rules"]["tick"]))
        self.assertEqual(state.load()["gateway"]["rf_consent"]["via"], "panel")
        # the texts of the page are the owner's (§7.1, §7.2), RU and EN, the mark of EN — «⚠RU»
        for lang, mark in (("ru", "⚠РФ"), ("en", "⚠RU")):
            src = read("i18n-%s.js" % lang)
            self.assertIn('"rf.consent": %s' % json.dumps(tr("rules.rf.consent", lang), ensure_ascii=False), src)
            self.assertNotIn('"rf.block"', src)          # the block is always the server's (the owner's words, §7.1)
            self.assertIn('"rf.mark": "%s"' % mark, src)
            gsrc = read("graph-i18n-%s.js" % lang)
            self.assertIn("'graph.rf.consent': '%s'" % tr("rules.rf.consent", lang), gsrc)
        # the form «Правила» sends its ver and the tick; «Подтвердить» — its own route
        self.assertIn('target: "default", profile: pick, ver: d.ver', app_js)
        self.assertIn('request("PUT", "/api/rules/consent", b)', app_js)
        self.assertIn("saveBtn.disabled = asks && !form.tick", app_js)       # «Сохранить» held without the tick


# ── A7: the light health of the node (SPEC-node-score §3.6, §3.9 п. 5) — the data of the live «Сводка» and «Здоровье
# сервера» (OVB_CTX.health → ovbHealth). REV-it1 №4: the old tile ovHost / ovMhost is gone; how the part ovb draws this
# answer — tests/panel/test_ovb_live.HealthLiveTest ──────────────────────────────────────────────────────────────────

def tile_scope():
    """ovbHealth and its pieces cut out of app.js (what OVB_CTX.health gives the part ovb), the clock of the light,
    the request GET /api/host/health counted."""
    js = read("app.js")
    parts = [jsparts.const(js, n) for n in ("DASH", "known", "isObj", "arr", "num", "HH_EVERY_MS", "HH", "HH_LEVELS", "KS")]
    parts += [jsparts.function(js, n) for n in ("hostHealthForget", "hostHealthPull", "hostHealthOf", "ovbHealth",
                                                "setLiveOk", "resetData", "applyLive")]
    return """
const tile = (function () {
  const t = (key, vars) => key + (vars ? " " + JSON.stringify(vars) : "");
  const ST = { off: false, host: null, page: "overview", hidden: false, calls: 0, answer: null, fail: false, refresh: 0,
    timers: [], quiet: false };
  const QUIET = { get on() { return ST.quiet; } };       // BC5: пауза панели на замер из браузера
  const S = { get page() { return ST.page; }, live: null, liveOk: null, sse: false, auth: "unknown" };
  const document = { get hidden() { return ST.hidden; } };
  const liveOff = () => ST.off;
  const lvHost = () => ST.host;
  const R = { overview: { data: { version: "3.4.0" } } };
  const API = { host: { health: () => { ST.calls += 1; return ST.fail ? Promise.reject(new Error("500")) : Promise.resolve(ST.answer); } } };
  // applyLive / setLiveOk / resetData: только их связь со здоровьем плитки (смена свежести live, выход)
  const loadQuiet = () => {}, normLive = (o) => o, liveStale = () => false, mergePatch = (a, b) => Object.assign({}, a, b);
  const liveSubs = new Set();
  const scheduleRefresh = () => { ST.refresh += 1; };
  const setTimeout = (fn, ms) => { ST.timers.push(ms); };
%s
  // what the part ovb gets now (its blocks ask OVB_CTX.health on every refresh of the page): {raw, host, view}
  function draw() { return JSON.parse(JSON.stringify(ovbHealth())); }
  return { ST, S, HH, draw, HH_EVERY_MS, setLiveOk, resetData, applyLive };
})();
const tick = () => new Promise((ok) => setImmediate(ok));
""" % "\n".join(parts)


def tile(script, data):
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(tile_scope() + "const DATA = %s;\n(async () => {\n%s\n})().catch((e) => { "
                "console.log(JSON.stringify({crash: String(e && e.stack || e)})); });\n"
                % (json.dumps(data, ensure_ascii=False), script))
        path = f.name
    try:
        res = subprocess.run(["node", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    finally:
        os.unlink(path)
    out = res.stdout.decode("utf-8").strip().splitlines()
    if res.returncode != 0 or not out:
        raise AssertionError("node: %s" % res.stderr.decode("utf-8", "replace")[-2000:])
    doc = json.loads(out[-1])
    if "crash" in doc:
        raise AssertionError(doc["crash"])
    return doc


HOST = {"cpu": 12.0, "mem_total": 2000, "mem_used": 1000, "load": [0.1, 0.2, 0.3], "uptime": 100, "steal": 0.3}
DISK = {"id": "disk", "level": "crit", "stale": False, "confirm_until": None, "since": 1791180000, "value": 98.0,
        "unit": "pct", "text": "Диск / почти заполнен", "advice": "Совет: почистить журналы"}
MEM = dict(DISK, id="mem", level="warn", text="Мало свободной памяти", advice="Совет: тариф с 2 ГБ")
STEAL = dict(DISK, id="steal", level="warn", text="Соседи отнимают процессор", advice="", confirm_until=1791190600)


def hdoc(level, top=(), **kw):
    """host.health of live.json (§3.6) of a level."""
    checks = dict((f["id"], {"level": f["level"], "shown": f["level"]}) for f in top)
    checks["cpu"] = {"level": "ok", "shown": "ok"}
    h = {"v": 1, "at": 1791190000, "level": level, "since": 1791180000, "instant": False, "by": "collector",
         "cost": None, "hint": None, "top": list(top), "info": [], "checks": checks, "probes": None}
    h.update(kw)
    return h


@unittest.skipUnless(shutil.which("node"), "нет node: части «Обзора» не собрать")
class PhoneHostTileTest(unittest.TestCase):
    """Было: телефонный светофор ovMhost (3.3.6, A7). Сейчас «Сводка» и «Здоровье сервера» — одна часть ovb на телефоне
    и на компьютере (раскладку меняет ovb.css), данные — тот же OVB_CTX.health, что у компьютера (HostTileTest)."""

    def test_phone_shows_the_light(self):
        from tests.test_panel_rf_js import overview_scope, run
        out = run(overview_scope(), r"""
const o = {};
for (const id of ["sum", "health"]) o[id] = [false, true].map((phone) => ovPart(id, phone).map((n) => n.getAttribute("data-ov")));
console.log(JSON.stringify(o));
""")
        self.assertEqual(out["sum"], [["ovb:summary"], ["ovb:summary"]])
        self.assertEqual(out["health"], [["ovb:health"], ["ovb:health"]])
        js = read("app.js")
        self.assertIn("health: ovbHealth,", js)
        self.assertIn("view: hostHealthOf(hh)", jsparts.function(js, "ovbHealth"))


@unittest.skipUnless(shutil.which("node"), "нет node: ovbHealth не запустить")
class HostTileTest(HostPanelCase):
    """ovbHealth (OVB_CTX.health of the live «Сводка» and «Здоровье сервера»; was — the tile ovHost) by its behaviour on
    fake data and on real answers of GET /api/host/health (not by searching a line — SPEC-todo-coordinator.md:17, П-2):
    every state of the light, the request only when live is stale."""

    STATES = """
const out = {};
for (const name of Object.keys(DATA.states)) {
  tile.ST.host = Object.assign({}, DATA.host, { health: DATA.states[name] });
  out[name] = tile.draw();
}
out.calls = tile.ST.calls;
console.log(JSON.stringify(out));
"""

    @staticmethod
    def head(d):
        return d["view"]                                             # the light: level, since, findings, notes

    @staticmethod
    def rows(d):
        return d["view"]["top"] if d["view"] else []

    def test_every_state_on_fresh_live(self):
        states = {
            "ok": hdoc("ok", checks={"cpu": {"level": "ok", "shown": "ok"}, "steal": {"level": "ok", "shown": "ok"}}),
            "warn": hdoc("warn", [STEAL]),
            "crit": hdoc("crit", [DISK, MEM, STEAL], info=[{"id": "anchor", "text": "Точка 8.8.8.8 режет пинги"}],
                         checks={"disk": {"shown": "crit"}, "mem": {"shown": "warn"}, "steal": {"shown": "warn"},
                                 "swap": {"shown": "warn"}, "cpu": {"shown": "ok"}}),
            "wait": hdoc("wait", checks={"cpu": {"level": "wait", "why": "wait"}}),      # since есть, но не говорится
            "off": hdoc("off", checks={}),
            "stale": hdoc("crit", [dict(DISK, stale=True, stale_msg={"key": "gw_host.stale", "args": {"min": 4}})]),
            "instant": hdoc("warn", [dict(DISK, level="warn")], instant=True, by="bot"),
        }
        out = tile(self.STATES, {"host": HOST, "states": states})
        self.assertEqual(out["calls"], 0)                                     # live fresh: no request
        want = {"ok": "ok", "warn": "warn", "crit": "crit", "wait": "wait", "off": "off", "stale": "crit", "instant": "warn"}
        for name, level in want.items():
            with self.subTest(state=name):
                d = out[name]
                self.assertEqual(self.head(d)["level"], level)                 # the part ovb says it in words (ovb.hh.*)
                self.assertEqual(self.head(d)["since"], 1791180000)            # «с …» — the part shows it for a said level
                self.assertEqual(self.head(d)["noHistory"], name == "instant")
                self.assertEqual(d["host"], dict(HOST, health=d["raw"]))      # the rows of the node — as they came
        # findings: ≤ 3, with their advice, crit first as the server ordered; the 4th — counted, not listed
        crit = self.rows(out["crit"])
        self.assertEqual([(r["level"], r["text"]) for r in crit], [("crit", DISK["text"]), ("warn", MEM["text"]),
                                                                   ("warn", STEAL["text"])])
        self.assertEqual(crit[0]["advice"], DISK["advice"])
        self.assertEqual(crit[2]["until"], 1791190600)                       # «стало лучше, подтверждаю до …»
        self.assertEqual(self.head(out["crit"])["more"], 1)                  # swap: the 4th finding (no count on screen)
        self.assertEqual(self.head(out["crit"])["info"], ["Точка 8.8.8.8 режет пинги"])
        self.assertEqual(self.rows(out["ok"]), [])
        self.assertEqual(self.rows(out["wait"]), [])
        stale = self.rows(out["stale"])
        self.assertEqual(len(stale), 1)
        self.assertEqual((stale[0]["stale"], stale[0]["staleMin"]), (True, 4))
        self.assertFalse(any(r["stale"] for r in crit))

    def test_no_health_draws_the_meters_as_before(self):
        out = tile("""
tile.ST.host = DATA.host;
const a = tile.draw();
tile.ST.host = null;
console.log(JSON.stringify({ a, none: tile.draw(), calls: tile.ST.calls }));
""", {"host": dict(HOST, steal=None)})
        self.assertEqual((out["a"]["host"], out["a"]["raw"], out["a"]["view"]), (dict(HOST, steal=None), None, None))
        self.assertEqual(out["none"], {"raw": None, "host": None, "view": None})   # the part: «—» and «ждём данных»
        self.assertEqual(out["calls"], 0)

    def test_stale_live_asks_the_route_and_marks_no_history(self):
        self.gw_off(bot_impl="bash")
        self.disk = 98.5
        r = self.call("GET", "/api/host/health")
        self.assertEqual(r.status, 200)
        answer = json.loads(r.body.decode("utf-8"))
        self.assertEqual((answer["by"], answer["health"]["level"]), ("panel", "crit"))
        out = tile("""
const out = {};
tile.ST.off = true;
tile.ST.answer = DATA.answer;
tile.ST.host = DATA.sample;                         // api._with_live: /proc of the panel, no health
out.first = tile.draw();                            // asks; nothing to show yet
out.calls1 = tile.ST.calls;
await tick(); await tick();
out.refresh = tile.ST.refresh;
out.timers = tile.ST.timers.slice();
out.second = tile.draw();                           // the answer drawn, no second request within a minute
out.calls2 = tile.ST.calls;
tile.HH.at -= tile.HH_EVERY_MS + 1;                 // a minute later: asks again
tile.draw();
out.calls3 = tile.ST.calls;
await tick(); await tick();
tile.HH.at -= tile.HH_EVERY_MS + 1;
tile.ST.page = "people";                            // another section open: no request
tile.draw();
tile.ST.page = "overview";
tile.ST.hidden = true;                              // a hidden tab: no request
tile.draw();
tile.ST.hidden = false;
tile.ST.quiet = true;                               // BC5: a test from the browser is running — no request
tile.draw();
tile.ST.quiet = false;
out.calls4 = tile.ST.calls;
tile.ST.off = false;                                // the collector writes again: host.health of live, no request
tile.ST.host = Object.assign({}, DATA.sample, { health: DATA.fresh });
out.fresh = tile.draw();
out.calls5 = tile.ST.calls;
console.log(JSON.stringify(out));
""", {"answer": answer, "sample": HOST, "fresh": hdoc("ok")})
        self.assertEqual(out["calls1"], 1)
        self.assertEqual((out["first"]["view"], out["first"]["host"]), (None, HOST))   # asks; the rows of the node stay
        self.assertGreaterEqual(out["refresh"], 1)
        self.assertEqual(out["timers"], [60000])                               # the block asks again in a minute
        self.assertEqual(out["calls2"], 1)
        second = out["second"]
        self.assertEqual(self.head(second)["level"], "crit")
        self.assertTrue(self.head(second)["noHistory"])                        # «без истории»
        self.assertEqual(self.head(second)["hint"], answer["health"]["hint_text"])   # the bot on bash
        self.assertEqual([r["text"] for r in self.rows(second)], [answer["health"]["top"][0]["text"]])
        self.assertTrue(self.rows(second)[0]["advice"])
        self.assertEqual(out["calls3"], 2)
        self.assertEqual(out["calls4"], 2)
        self.assertEqual(out["calls5"], 2)
        self.assertFalse(self.head(out["fresh"])["noHistory"])
        self.assertEqual(self.head(out["fresh"])["level"], "ok")

    def test_failed_request_keeps_the_meters(self):
        out = tile("""
tile.ST.off = true;
tile.ST.answer = null;
tile.ST.host = DATA.sample;
tile.draw();
await tick(); await tick();
console.log(JSON.stringify({ nodes: tile.draw(), calls: tile.ST.calls }));
""", {"sample": HOST})
        self.assertEqual(out["calls"], 1)
        self.assertEqual(out["nodes"], {"raw": None, "host": HOST, "view": None})   # no light, the rows of the node stay

    def test_steal_only_where_it_is_measured(self):
        """З-1: «соседи по серверу» — только при правиле steal не na (контейнер: steal в /proc/stat нулевой); при
        несвежем live — никогда, даже если в ответе маршрута steal измерен (П-2а). Живая часть ovb steal не рисует вовсе
        (было — подпись у ЦП старой плитки ovHost): steal только в составе находок сервера (TOP_OF.cpu), а ответ
        маршрута при несвежем live — свой, без steal снимка /proc."""
        fresh = {"ok": hdoc("ok", checks={"steal": {"level": "ok", "shown": "ok"}}),
                 "wait": hdoc("wait", checks={"steal": {"level": "wait", "why": "wait"}}),
                 "container": hdoc("ok", checks={"steal": {"level": "na", "why": "container"}}),
                 "none": hdoc("ok", checks={"cpu": {"level": "ok", "shown": "ok"}})}
        out = tile("""
const out = {};
for (const name of Object.keys(DATA.fresh)) {
  tile.ST.host = Object.assign({}, DATA.host, { health: DATA.fresh[name] });
  out[name] = tile.draw();
}
tile.ST.off = true;
tile.ST.host = DATA.host;
tile.ST.answer = { health: DATA.fresh.ok, instant: true, by: "bot" };
tile.draw();
await tick(); await tick();
out.stale = tile.draw();
out.calls = tile.ST.calls;
console.log(JSON.stringify(out));
""", {"host": dict(HOST, steal=0.0), "fresh": fresh})

        for name, doc in fresh.items():                                       # live fresh: its own health, as it came
            self.assertEqual((out[name]["raw"], out[name]["view"]["level"]), (doc, doc["level"]))
        self.assertEqual(out["calls"], 1)
        self.assertEqual(self.head(out["stale"])["level"], "ok")              # the answer drawn …
        self.assertEqual(out["stale"]["raw"]["checks"]["steal"], {"level": "ok", "shown": "ok"})   # … its own checks
        ovb = read("pages/ovb.js")
        self.assertNotIn(".steal", ovb)                                        # no steal of a snapshot of /proc drawn
        self.assertNotIn('"ov.steal"', read("app.js"))

    def test_old_health_is_not_shown_as_current(self):
        """П-1: ответ маршрута забывается при ошибке следующего запроса, при смене свежести live (поток и снимок GET
        overview), при выходе; запоздавший ответ забытого периода не ложится."""
        out = tile("""
const out = {};
const shows = () => !!tile.draw().view;
const expire = () => { tile.HH.at -= tile.HH_EVERY_MS + 1; };
tile.ST.off = true;
tile.ST.host = DATA.host;
tile.ST.answer = DATA.answer;
tile.S.liveOk = false;
tile.draw(); await tick(); await tick();
out.drawn = shows();
// 1) the next request fails: the old answer is not drawn
tile.ST.fail = true; expire();
tile.draw(); await tick(); await tick();
out.afterFail = shows();
tile.ST.fail = false; expire();
tile.draw(); await tick(); await tick();
out.again = shows();
// 2) live fresh → stale (SSE): forgotten, asked at once
const calls = tile.ST.calls;
tile.setLiveOk(true); tile.setLiveOk(false);
out.afterFlip = tile.HH.data;
tile.draw();
out.askedAtOnce = tile.ST.calls - calls;
await tick(); await tick();
// 3) a late answer of a forgotten period: asked, then live changed before the answer
expire();
tile.draw();                                  // a request in flight
tile.setLiveOk(true); tile.setLiveOk(false);
await tick(); await tick();
out.late = tile.HH.data;
// 4) GET overview snapshot with live.fresh changed
tile.draw(); await tick(); await tick();
out.beforeSnap = !!tile.HH.data;
tile.applyLive({ live: { fresh: true, ts: 1 } }, "snapshot");
out.afterSnapFresh = tile.HH.data;
tile.applyLive({ live: { fresh: false } }, "snapshot");
tile.draw(); await tick(); await tick();
tile.applyLive({ live: { fresh: false } }, "snapshot");       // same freshness: kept
out.sameFresh = !!tile.HH.data;
// 5) logout
tile.resetData();
out.afterLogout = tile.HH.data;
out.atAfterLogout = tile.HH.at;
console.log(JSON.stringify(out));
""", {"host": HOST, "answer": {"health": hdoc("crit", [DISK], instant=True, by="panel"), "instant": True, "by": "panel"}})
        self.assertTrue(out["drawn"])
        self.assertFalse(out["afterFail"])
        self.assertTrue(out["again"])
        self.assertIsNone(out["afterFlip"])
        self.assertEqual(out["askedAtOnce"], 1)
        self.assertIsNone(out["late"])
        self.assertTrue(out["beforeSnap"])
        self.assertIsNone(out["afterSnapFresh"])
        self.assertTrue(out["sameFresh"])
        self.assertIsNone(out["afterLogout"])
        self.assertEqual(out["atAfterLogout"], 0)


if __name__ == "__main__":
    unittest.main()
