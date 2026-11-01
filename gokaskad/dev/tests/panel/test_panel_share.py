"""GET /api/host/share (C9; SPEC-node-score-schedule-share §5.1–5.2, «Сверка 24.10») on the fake node: behind the login,
GET only, no-store, the switches strictly 0|1, «Сначала проверьте сервер» (no check, stale), the frame of the latest
check, the bait of the node (the name of the host, the address of the node, the panel behind nginx) never in the
answer, and the answer writes nothing on the node (no new state: the update from 3.3.7.9 and the way back see nothing)."""

import json
import os
import time
from unittest import mock

from kaskad import score as S
from kaskad import paths, state, tz
from kaskad.bench import hostscore as HS
from kaskad.bench import share as SH
from kaskad.bench import spool
from kaskad.gw import hostprobe, metrics
from kaskad.panel import conf
from tests.panel.panelcase import PanelCase

DAY = 86400
VALS = {"aes_1t": 7000, "chacha_1t": 2400, "cpu_1t": 2700, "cpu_mt": 10500, "disk_fsync": 0.15, "disk_rr": 130000,
        "disk_rw": 85000, "dns": 5, "jitter": {"n": 17280, "v": 0.25},
        "loss": {"lo": 0.0015905403461511298, "n": 25920.0, "v": 0.01, "points": [{"e": 0.864, "k": 0.864, "n": 8640.0}] * 3},
        "mem_bw": 20000, "mem_gb": 8, "net_cv": 4, "net_down": 1700, "net_up": 1300, "rtt": 2.5, "steal_p95": 0.1}
INV = {"cpu": "AMD EPYC 7763 64-Core Processor", "nproc": 2, "mem_gb": 3.912}
PATH = "app-" + "Qw7" * 28 + "ZZ"


class ShareCase(PanelCase):
    def setUp(self):
        super().setUp()
        HS.reset()
        self.addCleanup(HS.reset)
        if not metrics.available():
            self.skipTest("no sqlite3")
        metrics.open_writer().close()
        self.now = int(time.time())
        self.n = 0
        st = state.load()
        st.setdefault("gateway", {})["host"] = "192.0.2.10"
        st["gateway"].setdefault("hostmon", {})["geo"] = {"country": "FI", "ru": False, "src": "exits_geo",
                                                          "ts": self.now}
        state.save(st)
        cfg = conf.load()
        cfg["proxy"].update(on=True, domain="panel.example.com", path=PATH)
        for p in (mock.patch.object(SH, "_hostnames", lambda: ["node-anna"]),
                  mock.patch.object(hostprobe, "own_addrs", lambda mono=None: ("198.51.100.7", "fe80::1")),
                  mock.patch.object(conf, "load", lambda *a, **k: cfg)):
            p.start()
            self.addCleanup(p.stop)

    def check(self, ago, inv=INV, vals=None, more=None, **head):
        self.n += 1
        h = {"id": "%010x" % self.n, "ts": self.now - int(ago), "mode": "full", "by": "panel", "status": "ok",
             "inv": inv}
        h.update(head)
        spool.put(spool.check_doc(h, S.with_mode(dict(VALS if vals is None else vals), "full"), more))
        return h["id"]

    def share(self, q=""):
        r = self.call("GET", "/api/host/share" + q)
        self.assertEqual(r.status, 200, r.text)
        self.assertEqual(r.headers.get("cache-control"), "no-store")
        return r.json()

    def files(self):
        out = set()
        for d, _dirs, names in os.walk(paths.P("/")):          # the fake root of the case (KASKAD_ROOT)
            out.update(os.path.join(d, n) for n in names)
        return out

    def test_login_and_get_only(self):
        self.assertEqual(self.call("GET", "/api/host/share", tunnel=False).status, 401)
        for method in ("POST", "PUT", "DELETE"):
            self.assertEqual(self.call(method, "/api/host/share", {}).status, 405, method)

    def test_the_switches_strictly(self):
        for q in ("?hoster=1", "?tariff=yes", "?cpu=2", "?country=", "?ip=1", "?lang=ru", "?tariff=1&x=0"):
            r = self.call("GET", "/api/host/share" + q)
            self.assertEqual(r.status, 400, q)
            self.assertEqual(r.json()["data"]["field"] in ("tariff", "country", "cpu", "query"), True, q)

    def test_check_the_server_first(self):
        self.assertEqual(self.share("?tariff=1&country=1&cpu=1"),
                         {"v": 1, "ok": True, "ready": False, "why": "no_check"})
        self.check(46 * DAY)
        self.assertEqual(self.share()["why"], "stale")

    def test_the_frame_of_the_latest_check(self):
        for d in range(6, 0, -1):
            self.check(d * DAY)
        a = self.share()
        self.assertTrue(a["ready"])
        self.assertEqual(a["date"], tz.day(self.now - DAY))
        self.assertIsInstance(a["at"], int)                                     # время UTC и МСК рядом с датой
        self.assertTrue(self.now - DAY - 3600 <= a["at"] <= self.now - DAY + 3600)
        self.assertEqual([(m["id"], m["v"], m["unit"]) for m in a["metrics"]][:2],
                         [("down", 1700.0, "Мбит/с"), ("up", 1300.0, "Мбит/с")])
        self.assertEqual(len(a["days"]), 30)
        self.assertEqual(a["extra"], {"tariff": None, "country": None, "cpu": None})      # off by default
        b = self.share("?tariff=1&country=1&cpu=1")
        self.assertEqual(b["extra"], {"tariff": {"vcpu": 2, "ram_gb": 3.9, "port_mbit": None}, "country": "FI",
                                      "cpu": "AMD EPYC 7763 64-Core Processor"})
        text = json.dumps(b, ensure_ascii=False)
        for bait in ("192.0.2.10", "198.51.100.7", "panel.example.com", PATH, "node-anna", "fe80", "app-"):
            self.assertNotIn(bait, text)

    def test_where_the_numbers_were_measured(self):
        """Владелец 24.10, «чьи это цифры»: точки теста 8 из истории последних проверок, якоря пинга — публичные DNS
        списком, свои — только числом; кривой id точки не выходит."""
        self.check(3 * DAY, more={"net_down": {"extra": {"target": "ovh"}}})
        self.check(DAY, more={"net_down": {"extra": {"target": "cloudflare"}}, "net_up": {"extra": {"target": "cloudflare"}}})
        w = self.share()["where"]
        self.assertEqual((w["down"], w["up"]), ("cloudflare", "cloudflare"))
        self.assertEqual((w["anchors"], w["n"]), (["cloudflare", "google", "yandex"], 3))     # имена, не адреса
        st = state.load()
        st["gateway"]["hostmon"]["anchors"] = ["9.9.9.9", "1.1.1.1"]
        state.save(st)
        w = self.share()["where"]
        self.assertEqual((w["anchors"], w["n"]), ("own", 2))
        self.assertNotIn("9.9.9.9", json.dumps(self.share(), ensure_ascii=False))
        with self.assertRaises(ValueError):
            spool.extra({"target": "Cloud flare/../x"})
        self.assertEqual(spool.extra({"target": "hostkey-msk"}), {"target": "hostkey-msk"})

    def test_the_bait_in_the_model_of_the_cpu(self):
        for cpu in ("node-anna Xeon", "Xeon 192.0.2.10", "panel.example.com", "EPYC 198.51.100.7", "Xeonㅤ"):
            self.check(DAY, inv=dict(INV, cpu=cpu))
            a = self.share("?cpu=1")
            self.assertIsNone(a["extra"]["cpu"], cpu)
            self.assertEqual(a["dropped"], ["cpu"], cpu)
            self.assertNotIn(cpu, json.dumps(a, ensure_ascii=False))

    def test_writes_nothing_on_the_node(self):
        """The update from 3.3.7.9 (and the way back): «Поделиться» keeps no state — no file appears, state.json is
        the same."""
        self.check(DAY)
        self.share()                                 # the score of the node read once (its own caches)
        before, st = self.files(), state.load()
        for q in ("", "?tariff=1", "?country=1&cpu=1"):
            self.share(q)
        self.assertEqual(self.files(), before)
        self.assertEqual(state.load(), st)

    def test_cli_says_what_the_clip_shows(self):
        """``kaskadctl host share`` (§6.1, «Сверка 24.10» п. 7): the same answer as the panel, the text for the eyes."""
        from tests.test_host_cli import run_cli, run_json
        rc, out, _ = run_cli("host", "share")
        self.assertEqual((rc, out.strip()), (0, "Сначала проверьте сервер: годной проверки нет или она давняя."))
        for d in range(6, 0, -1):
            self.check(d * DAY)
        rc, doc, _ = run_json("host", "share", "--cpu")
        self.assertEqual(rc, 0)
        self.assertEqual(doc, self.share("?cpu=1"))
        rc, out, _ = run_cli("host", "share", "--tariff", "--country", "--cpu")
        self.assertEqual(rc, 0)
        for want in ("Поделиться проверкой · " + tz.day(self.now - DAY), "Скачивание 1 700 Мбит/с", "Отдача",
                     "30 дней: ровно —", "Машина: ядер: 2 · 3,9 ГБ", "Страна: FI",
                     "Процессор: AMD EPYC 7763 64-Core Processor", "Обзор › Проверка сервера → Поделиться"):
            self.assertIn(want, out)
        for bait in ("192.0.2.10", "panel.example.com", "node-anna", "app-"):
            self.assertNotIn(bait, out)
        with mock.patch.dict(os.environ, {"KASKAD_LANG": "en"}):
            rc, out, _ = run_cli("host", "share")
        self.assertEqual(rc, 0)
        self.assertIn("Share the check", out)
        self.assertFalse(any("Ѐ" <= c <= "ӿ" for c in out), out)

    def test_the_push_notably_worse(self):
        """«Сервер заметно хуже, чем обычно: …» (the design r1, «Сверка 24.10» п. 6): the push of the check says the
        first link with a confirmed ▼ after it, with its numbers; no ▼ — the usual «закончена: N от обычного»."""
        from kaskad.bench import donepush
        from kaskad.gw import live
        for d in range(10, 4, -1):
            self.check(d * DAY)
        a = donepush.with_rel({"ts": self.now, "id": "x", "mode": "full", "by": "panel", "status": "ok", "why": None})
        self.assertNotIn("worse", a)
        self.assertEqual(live.bench_key(a), "gw_live.event.bench_done")
        HS.reset()
        for d in (3, 2, 1):
            self.check(d * DAY - 3600, vals=dict(VALS, net_down=850))
        a = donepush.with_rel({"ts": self.now, "id": "y", "mode": "full", "by": "panel", "status": "ok", "why": None})
        self.assertEqual(a["worse"], {"link": "down", "test": "net_down", "now": 850.0, "usual": 1700.0})
        ev = {"kind": "bench_done", "key": live.bench_key(a), "args": a}
        self.assertEqual(live.event_text(ev, "ru"),
                         "Сервер заметно хуже, чем обычно: «Скачивание» 850 Мбит/с, обычно 1\u202f700 Мбит/с")
        self.assertEqual(live.event_text(ev, "en"),
                         "The server is notably worse than usual: «Download» 850 Mbit/s, usually 1,700 Mbit/s")
        for bad in ({"link": "<b>"}, {"link": "down", "test": "rm -rf", "now": 1, "usual": 2}, "x",
                    {"link": "down", "test": "net_down", "now": float("nan"), "usual": 2}):
            b = dict(a, worse=bad)
            text = live.event_text({"kind": "bench_done", "key": "x", "args": b}, "ru")
            self.assertNotIn("<b>", text)
            self.assertNotIn("rm -rf", text)
            self.assertNotIn("nan", text)


from tests.test_mcp import TOOLS, McpCase, call      # noqa: E402 — after the case of the panel


class ShareMcpTest(McpCase):
    def test_share_is_a_read(self):
        sc = TOOLS["host_score"]
        sh = sc["by_action"]["share"]
        self.assertEqual((sh["cmd"], sh["readonly"], sh["params"]), (["host", "share"], True,
                                                                      ["tariff", "country", "cpu"]))
        self.assertTrue(sc["readonly"])
        for p in ("tariff", "country", "cpu"):
            self.assertEqual(sc["params"][p]["flag"], "--" + p)

    def test_argv(self):
        sess = self.run_session([call(1, "host_score", {"action": "share", "cpu": True})])
        argv = sess.result(1)["structuredContent"]["argv"]
        self.assertEqual(argv[:2], ["host", "share"])
        self.assertIn("--cpu", argv)
        self.assertIn("--json", argv)
        self.assertNotIn("--tariff", argv)
