"""The binding of the tariff (k3/handoff/SPEC-node-score.md NS-D34: only the port — the health rule «port», and the
traffic — the price of the schedule) — ``kaskadctl host tariff [show|set]``, the routes GET/POST /api/host/tariff, the
MCP tools ``host_tariff`` action=show|preview and ``host_tariff_set`` (the code of one change checked by the server),
the file of a new base (NS-S17) in the queue of the checks, a tariff stored before 23.10 read silently."""

import json
import os
from unittest import mock

from kaskad import host_tariff, state
from kaskad.bench import spool
from kaskad.bench import tariff as T
from kaskad.gw import model
from tests.helpers import RootCase
from tests.panel.panelcase import PanelCase
from tests.test_host_cli import run_cli, run_json
from tests.test_mcp import ACTIONS, McpCase, TOOLS, call
from tests.test_rules_rf_consent import RfCase

class CliTest(RootCase):
    def setUp(self):
        super().setUp()
        st = state.empty()
        st["engine"] = "nft"
        st["gateway"] = {"enabled": True, "host": "1.2.3.4"}
        state.save(st)

    def stored(self):
        return state.load()["gateway"].get("hostmon", {}).get("tariff")

    def epochs(self):
        try:
            return sorted(n for n in os.listdir(self.path("/var/lib/kaskad/bench/spool")) if n.startswith("epoch-"))
        except OSError:
            return []

    def test_empty_tariff_shows_two_fields(self):
        rc, doc, _ = run_json("host", "tariff")
        self.assertEqual(rc, 0)
        self.assertEqual(set(doc), {"ok", "tariff", "dropped"})
        self.assertEqual(set(doc["tariff"]), {"v", "at", "fields", "rev"})      # no counted / of / complete (C2)
        f = doc["tariff"]["fields"]
        self.assertEqual(set(f), {"port_mbit", "traffic_gb"})
        self.assertEqual(f["port_mbit"], {"v": None, "src": None, "label": "Порт", "text": None, "src_text": None})
        rc, out, _ = run_cli("host", "tariff", "show")
        self.assertEqual(rc, 0)
        self.assertEqual([x.strip() for x in out.strip().splitlines()],
                         ["Тариф сервера", "Порт: не введено", "Трафик в месяц: не введено"])
        self.assertIsNone(self.stored())                                     # reading writes nothing

    def test_the_core_checks_the_shape_of_rev(self):
        """REV-ns-c2 П-3 (M21): the core itself (the CLI and MCP go to it without the panel) refuses a rev that is not
        10 lowercase hex signs — 400 bad_rev, nothing written; the right rev writes."""
        for bad in ("ё" * 10, "ABCDEF0123", "abc", "0123456789a", 5, ["x"]):
            with self.assertRaises(host_tariff.TariffError) as e:
                host_tariff.apply(values={"port_mbit": 100}, rev=bad)
            self.assertEqual((e.exception.key, e.exception.status), ("bad_rev", 400), bad)
            self.assertIsNone(self.stored(), bad)
        with self.assertRaises(host_tariff.TariffError) as e:
            host_tariff.apply(values={"port_mbit": 100}, rev="0" * 10)            # the shape is right, the version not
        self.assertEqual(e.exception.key, "rev_changed")
        host_tariff.apply(values={"port_mbit": 100}, rev=T.rev(None))
        self.assertEqual(self.stored()["vals"]["port_mbit"]["v"], 100)

    def test_set_writes_with_the_source_and_a_new_base(self):
        rc, doc, _ = run_json("host", "tariff", "set", "--port", "1000", "--traffic", "2000")
        self.assertEqual(rc, 0, doc)
        self.assertEqual(doc["changed"], ["port_mbit", "traffic_gb"])
        t = self.stored()
        self.assertEqual(t["vals"], {"port_mbit": {"v": 1000, "src": "user"}, "traffic_gb": {"v": 2000, "src": "user"}})
        self.assertEqual(model.hostmon(state.load())["port_mbit"], 1000)        # the rule «port» takes it
        ep = self.epochs()
        self.assertEqual(len(ep), 1)
        self.assertEqual(spool.load_epoch(self.path("/var/lib/kaskad/bench/spool/" + ep[0]))[2],
                         {"port_mbit": 1000, "fields": 1})
        # the traffic — no new base
        with mock.patch.object(host_tariff.time, "time", lambda: 1792999999):
            rc, doc, _ = run_json("host", "tariff", "set", "--traffic", "unlimited")
        self.assertEqual(rc, 0, doc)
        self.assertEqual(doc["changed"], ["traffic_gb"])
        self.assertEqual(len(self.epochs()), 1)
        rc, out, _ = run_cli("host", "tariff")
        self.assertIn("Порт: 1 Гбит/с (введено вами)", out)
        self.assertIn("Трафик в месяц: без лимита (введено вами)", out)
        with mock.patch.object(host_tariff.time, "time", lambda: 1793000000):    # another second: its own file
            rc, doc, _ = run_json("host", "tariff", "set", "--clear", "port")
        self.assertEqual(doc["changed"], ["port_mbit"])
        self.assertIsNone(model.hostmon(state.load())["port_mbit"])
        self.assertEqual(len(self.epochs()), 1)            # REV-ns-c1 П-4: a value cleared is no new base
        self.assertNotIn("tariff.note.new_base", json.dumps(doc))
        rc, doc, _ = run_json("host", "tariff", "set", "--clear", "all")
        self.assertNotIn("tariff", state.load()["gateway"]["hostmon"])

    def test_bad_values_write_nothing(self):
        before = self.read("/etc/kaskad/state.json")
        for argv, field, code in ((("--port", "0"), "port_mbit", "range"), (("--traffic", "abc"), "traffic_gb", "number"),
                                  (("--port", "1.5"), "port_mbit", "int"),
                                  (("--traffic", "1000", "--port", "unlimited"), "port_mbit", "number"),
                                  (("--clear", "bogus"), "bogus", "unknown"),
                                  (("--clear", "ram"), "ram", "unknown")):           # a field of the former tariff
            rc, doc, _ = run_json("host", "tariff", "set", *argv)
            self.assertEqual(rc, 2, argv)
            self.assertIs(doc["ok"], False)
            self.assertEqual([(e["field"], e["code"]) for e in doc["data"]["errors"]], [(field, code)], argv)
            self.assertNotIn("\x1b", doc["error"])
        for gone in ("--cores", "--ram", "--disk", "--price", "--currency", "--hoster", "--name"):   # NS-D34: no flag
            rc, _out, err = run_cli("host", "tariff", "set", gone, "2")
            self.assertEqual(rc, 2, gone)
        rc, doc, _ = run_json("host", "tariff", "set")
        self.assertEqual(rc, 2)
        self.assertEqual(self.read("/etc/kaskad/state.json"), before)
        self.assertEqual(self.epochs(), [])

    def test_dry_and_the_code_of_the_agent(self):
        rc, doc, _ = run_json("host", "tariff", "set", "--port", "500", "--dry")
        self.assertEqual(rc, 0)
        self.assertTrue(doc["dry"])
        self.assertRegex(doc["code"], r"^[0-9a-f]{10}$")
        self.assertEqual(doc["lines"], ["Порт: — → 500 Мбит/с"])
        self.assertIsNone(self.stored())
        code = doc["code"]
        rc, doc, _ = run_json("host", "tariff", "set", "--port", "500", "--by-mcp")
        self.assertEqual((rc, doc["error"][:6]), (2, "Сначал"))
        rc, doc, _ = run_json("host", "tariff", "set", "--port", "600", "--by-mcp", "--code", code)
        self.assertEqual(rc, 3)                                          # another change — refused
        self.assertIsNone(self.stored())
        run_json("host", "tariff", "set", "--traffic", "1000")           # the tariff changed meanwhile
        rc, doc, _ = run_json("host", "tariff", "set", "--port", "500", "--by-mcp", "--code", code)
        self.assertEqual(rc, 3)
        self.assertNotIn("port_mbit", self.stored()["vals"])
        for bad in ("ё" * 10, code.upper(), code + "0", "-"):         # REV-ns-c1 П-1: refused, not a crash
            rc, doc, _ = run_json("host", "tariff", "set", "--port", "500", "--by-mcp", "--code", bad)
            self.assertEqual(rc, 3, bad)
        rc, doc, _ = run_json("host", "tariff", "set", "--port", "500", "--dry")
        rc, doc, _ = run_json("host", "tariff", "set", "--port", "500", "--by-mcp", "--code", doc["code"])
        self.assertEqual(rc, 0, doc)
        self.assertEqual(self.stored()["vals"]["port_mbit"], {"v": 500, "src": "user"})

    def test_the_tariff_is_written_with_the_machine(self):
        # REV-ns-c1 З-1: the mid of the machine goes with the tariff (the restore of another machine drops it)
        from kaskad.bench import machine
        self.write("/etc/machine-id", "0123456789abcdef0123456789abcdef\n")
        rc, doc, _ = run_json("host", "tariff", "set", "--port", "100")
        self.assertEqual(rc, 0, doc)
        self.assertEqual(self.stored()["mid"], machine.mid())
        self.assertRegex(self.stored()["mid"], r"^[0-9a-f]{32}$")
        self.assertNotIn("0123456789abcdef", json.dumps(self.stored()))        # the raw id never
        os.unlink(self.path("/etc/machine-id"))
        rc, doc, _ = run_json("host", "tariff", "set", "--port", "200")
        self.assertNotIn("mid", self.stored())                                 # unknown machine — no mid

    def test_a_bad_stored_tariff_is_dropped_by_field(self):
        st = state.load()
        st["gateway"]["hostmon"] = {"tariff": {"v": 1, "vals": {"port_mbit": {"v": 100, "src": "user"},
                                                                "traffic_gb": {"v": 1.9, "src": "inventory"}}}}
        state.save(st)
        rc, doc, _ = run_json("host", "tariff")
        self.assertEqual(doc["dropped"], ["traffic_gb"])
        rc, out, _ = run_cli("host", "tariff")
        self.assertIn("Отброшены неверные значения: Трафик в месяц", out)    # REV-ns-c1 П-2: words, not keys
        self.assertNotIn("traffic_gb", out)
        self.assertEqual(doc["tariff"]["fields"]["port_mbit"]["v"], 100)
        self.assertIsNone(doc["tariff"]["fields"]["traffic_gb"]["v"])

    def test_an_old_tariff_reads_silently_and_its_fields_go_at_the_next_write(self):
        """NS-D34: a tariff of 3.3.7.x with cores, memory, disk, price, hoster … — no «dropped» line, the port still
        reaches the rule «port» and the traffic the price; the next write keeps only the port and the traffic."""
        st = state.load()
        st["gateway"]["hostmon"] = {"tariff": {"v": 1, "at": 1792000000, "vals": {
            "vcpu": {"v": 2, "src": "user"}, "ram_gb": {"v": 2, "src": "user"}, "disk_type": {"v": "nvme", "src": "user"},
            "price": {"v": 5.5, "src": "user"}, "currency": {"v": "EUR", "src": "user"},
            "hoster": {"v": "Aeza", "src": "user"}, "name": {"v": "MSK-1", "src": "user"},
            "port_mbit": {"v": 1000, "src": "user"}, "traffic_gb": {"v": 20, "src": "user"}}}}
        state.save(st)
        rc, doc, _ = run_json("host", "tariff")
        self.assertEqual(doc["dropped"], [])
        self.assertEqual(set(doc["tariff"]["fields"]), {"port_mbit", "traffic_gb"})
        rc, out, _ = run_cli("host", "tariff")
        self.assertNotIn("Отброшены", out)
        self.assertEqual(model.hostmon(state.load())["port_mbit"], 1000)
        from kaskad import host_sched
        host_sched.reset()                                                 # its cache of the history: not shared
        self.addCleanup(host_sched.reset)
        self.assertEqual(host_sched.cost_ctx({}, 1792000000)["tariff_traffic_gb"], 20)        # the price «% of the tariff»
        rc, doc, _ = run_json("host", "tariff", "set", "--traffic", "30")
        self.assertEqual((rc, doc["changed"]), (0, ["traffic_gb"]), doc)
        self.assertEqual(self.stored()["vals"], {"port_mbit": {"v": 1000, "src": "user"},
                                                 "traffic_gb": {"v": 30, "src": "user"}})
        self.assertEqual(self.epochs(), [])                                # the port is the same — no new base

    def test_the_new_base_failing_keeps_the_tariff(self):
        with mock.patch.object(spool, "put_epoch", side_effect=OSError("disk")):
            rc, doc, _ = run_json("host", "tariff", "set", "--port", "100")
        self.assertEqual(rc, 0)
        self.assertEqual(len(doc["warnings"]), 1)
        self.assertEqual(self.stored()["vals"]["port_mbit"]["v"], 100)


class RestoreTariffTest(RootCase):
    """REV-ns-c1 З-1 (решение координатора 19.10): the restore of a copy of another machine drops the tariff, with a line
    in the journal and a note — as the marks of B11, but whether ``hostmon.bench`` is there or not."""

    class Ctx(object):
        def __init__(self):
            self.lines, self.notes = [], []

        def log(self, text):
            self.lines.append(text)

    def restore(self, tariff, bench=None):
        from kaskad import backup
        hm = {"tariff": tariff}
        if bench is not None:
            hm["bench"] = bench
        g = {"hostmon": hm}
        ctx = self.Ctx()
        backup._tariff_of_this_machine(ctx, g)
        return g["hostmon"].get("tariff"), ctx

    def test_another_machine_drops_the_tariff(self):
        from kaskad.bench import machine
        self.write("/etc/machine-id", "0123456789abcdef0123456789abcdef\n")
        here = machine.mid()
        other = machine.mid_of("fedcba9876543210fedcba9876543210")
        t = {"v": 1, "vals": {"port_mbit": {"v": 1000, "src": "user"}}}
        got, ctx = self.restore(dict(t, mid=here))
        self.assertEqual(got, dict(t, mid=here))                              # this machine — kept, silently
        self.assertEqual((ctx.lines, ctx.notes), ([], []))
        for raw in (dict(t, mid=other), t, dict(t, mid="ё" * 32), dict(t, mid=None), "junk"):
            got, ctx = self.restore(raw)
            self.assertIsNone(got, raw)
            self.assertEqual(len(ctx.lines), 1, raw)
            self.assertIn("другого сервера", ctx.lines[0])
            self.assertEqual(len(ctx.notes), 1)
        got, ctx = self.restore(dict(t, mid=other), bench={"mid": here})    # the marks of B11 do not decide it
        self.assertIsNone(got)
        os.unlink(self.path("/etc/machine-id"))
        got, ctx = self.restore(dict(t, mid=here))                          # no machine-id here — unknown: dropped
        self.assertIsNone(got)

    def test_no_tariff_no_line(self):
        from kaskad import backup
        for g in ({}, {"hostmon": {}}, {"hostmon": {"bench": {}}}, {"hostmon": "x"}):
            ctx = self.Ctx()
            backup._tariff_of_this_machine(ctx, g)
            self.assertEqual((ctx.lines, ctx.notes), ([], []), g)


class RestoreTariffApplyTest(RfCase):
    """REV-ns-c2 З-6: the whole restore of ``gateway`` (``backup._apply_gateway``) drops the tariff of another machine —
    a test of the call itself, not only of ``_tariff_of_this_machine``."""

    def copy_with(self, mid):
        from kaskad import backup
        st = self.node()
        st["gateway"]["hostmon"] = {"tariff": {"v": 1, "vals": {"port_mbit": {"v": 1000, "src": "user"}}, "mid": mid}}
        state.save(st)
        return backup.payload(self.w.runner, ["gateway"])

    def apply(self, doc):
        from kaskad import backup
        self.node()                                           # the state before the restore: no tariff
        ctx = backup._Ctx(self.w.runner, doc, {"changed": False}, None)
        backup._apply_gateway(ctx)
        return state.load()["gateway"].get("hostmon", {}), ctx

    def test_the_restore_drops_the_tariff_of_another_machine(self):
        from kaskad.bench import machine
        self.write("/etc/machine-id", "0123456789abcdef0123456789abcdef\n")
        hm, ctx = self.apply(self.copy_with(machine.mid_of("fedcba9876543210fedcba9876543210")))
        self.assertNotIn("tariff", hm)
        self.assertEqual(sum(1 for n in ctx.notes if "тариф" in n.lower()), 1, ctx.notes)
        hm, ctx = self.apply(self.copy_with(machine.mid()))      # the copy of this machine — the tariff comes back
        self.assertEqual(hm["tariff"]["vals"]["port_mbit"]["v"], 1000)
        self.assertFalse(any("тариф" in n.lower() for n in ctx.notes), ctx.notes)


    def test_the_preview_says_it_before_the_import(self):
        """T-3377 Н-13: the preview (``backup import`` without --yes, the panel's window) says that the tariff will not
        come — the same rule as the import; the copy of this machine — nothing said."""
        from kaskad import backup
        from kaskad.bench import machine
        self.write("/etc/machine-id", "0123456789abcdef0123456789abcdef\n")
        for mid, said in ((machine.mid_of("fedcba9876543210fedcba9876543210"), 1), (machine.mid(), 0)):
            doc = self.copy_with(mid)
            self.node()
            before = state.load()
            pv = backup.preview(self.w.runner, doc, parts=["gateway"])
            self.assertEqual(state.load(), before)                          # a preview changes nothing
            self.assertEqual(sum(1 for w in pv["warnings"] if w.startswith("Тариф из копии не перенесётся")), said, mid)
            self.assertEqual(sum(1 for x in pv["text"] if "Тариф из копии не перенесётся" in x), said, pv["text"])


class PanelTariffTest(PanelCase):
    def test_get_post_and_their_guards(self):
        doc = self.ok("GET", "/api/host/tariff")
        self.assertEqual(set(doc["tariff"]["fields"]), {"port_mbit", "traffic_gb"})
        rev = doc["tariff"]["rev"]
        body = {"values": {"port_mbit": "1000", "traffic_gb": "unlimited"}, "rev": rev}
        # the guards of every admin POST: the header, the origin, the login
        self.assertEqual(self.call("POST", "/api/host/tariff", body, csrf=False).status, 403)
        self.assertEqual(self.call("POST", "/api/host/tariff", body,
                                   headers={"Origin": "https://evil.example.com"}).status, 403)
        self.assertIn(self.call("POST", "/api/host/tariff", body, tunnel=False).status, (401, 403))
        self.assertIn(self.call("GET", "/api/host/tariff", tunnel=False).status, (401, 403))
        self.assertNotIn("tariff", state.load()["gateway"].get("hostmon", {}))
        doc = self.ok("POST", "/api/host/tariff", dict(body, dry=True))
        self.assertTrue(doc["dry"])
        self.assertNotIn("tariff", state.load()["gateway"].get("hostmon", {}))
        doc = self.ok("POST", "/api/host/tariff", body)
        self.assertEqual(doc["changed"], ["port_mbit", "traffic_gb"])
        self.assertEqual(doc["tariff"]["fields"]["port_mbit"]["text"], "1 Гбит/с")
        self.assertEqual(doc["tariff"]["fields"]["port_mbit"]["src"], "user")
        e = self.err(409, "POST", "/api/host/tariff", body)              # the old rev: another window
        self.assertEqual(e.get("code"), "rev_changed")
        rev = doc["tariff"]["rev"]
        e = self.err(400, "POST", "/api/host/tariff", {"values": {"port_mbit": 0, "traffic_gb": "x"}, "rev": rev})
        self.assertEqual(sorted(x["field"] for x in e["data"]["errors"]), ["port_mbit", "traffic_gb"])
        e = self.err(400, "POST", "/api/host/tariff", {"values": {"hoster": "Aeza"}, "rev": rev})   # NS-D34: gone
        self.assertEqual([(x["field"], x["code"]) for x in e["data"]["errors"]], [("hoster", "unknown")])
        for bad in ({"values": []}, {"clear": "all"}, {"clear": [1]}, {"rev": 5}, {"dry": "yes"},
                    {"values": dict((str(i), 1) for i in range(20))},
                    {"values": {"port_mbit": 1, "traffic_gb": 1, "ram_gb": 2}},
                    {"values": {"port_mbit": 100}, "rev": "ё"},              # REV-ns-c1 П-1: 400, not 500
                    {"values": {"port_mbit": 100}, "rev": rev.upper()},
                    {"values": {"port_mbit": 100}, "rev": rev + "0"}):
            self.err(400, "POST", "/api/host/tariff", bad)
        # REV-ns-c1 З-3: a write needs the rev of the GET — none or null is 428 (as If-Match of B13), nothing written
        before = state.load()["gateway"]["hostmon"]["tariff"]
        for body in ({"values": {"port_mbit": 2}}, {"values": {"port_mbit": 2}, "rev": None}, {"clear": ["all"]}):
            e = self.err(428, "POST", "/api/host/tariff", body)
            self.assertEqual(e.get("code"), "rev_required")
        self.assertEqual(state.load()["gateway"]["hostmon"]["tariff"], before)
        doc = self.ok("POST", "/api/host/tariff", {"values": {"port_mbit": 2}, "dry": True})   # a preview needs none
        self.assertEqual(doc["changed"], ["port_mbit"])
        e = self.err(400, "POST", "/api/host/tariff", {"rev": rev})            # REV-ns-c1 П-2: no «--clear» here
        self.assertEqual(e.get("code"), "nothing")
        self.assertNotIn("--clear", e["error"])
        doc = self.ok("POST", "/api/host/tariff", {"clear": ["all"], "rev": rev})
        self.assertEqual(doc["changed"], ["port_mbit", "traffic_gb"])
        self.assertNotIn("tariff", state.load()["gateway"]["hostmon"])
        doc = self.ok("GET", "/api/host/tariff", headers={"X-Kaskad-Lang": "en"})
        self.assertEqual(doc["tariff"]["fields"]["traffic_gb"]["label"], "Traffic a month")


class McpTariffTest(McpCase):
    def test_catalog(self):
        t = TOOLS["host_tariff"]                # REV-ns-b14 З-3, REV-ns-b14-2 П-3: show и preview — одно чтение, set — свой
        acts = t["by_action"]
        self.assertEqual(t["action_default"], "show")
        self.assertEqual(set(acts), {"show", "preview"})
        self.assertTrue(t["readonly"] and acts["show"]["readonly"] and acts["preview"]["readonly"])
        self.assertEqual(acts["preview"]["args"], ["--dry"])
        s = TOOLS["host_tariff_set"]
        self.assertFalse(s.get("readonly") or s.get("destructive"))
        self.assertEqual(s["args"], ["--by-mcp"])
        self.assertNotIn("required", s)
        self.assertIs(s["params"]["code"]["required"], True)                 # code — до kaskadctl
        self.assertEqual(set(s["params"]) - {"code"}, set(acts["preview"]["params"]))
        self.assertEqual(set(acts["preview"]["params"]), {"port", "traffic", "clear"})          # NS-D34

    def test_argv_and_the_code_required(self):
        s = self.run_session([call(2, "host_tariff_set", {"port": 1000, "traffic": "unlimited", "code": "0123456789"}),
                              call(3, "host_tariff_set", {"port": 1000}),
                              call(4, "host_tariff", {"action": "preview", "traffic": "1,5", "clear": "port"}),
                              call(5, "host_tariff_set", {"port": 1000, "code": "XYZ"}),
                              call(6, "host_tariff", {}),
                              call(7, "host_tariff", {"action": "preview", "hoster": "Aeza"}),     # NS-D34: gone
                              call(8, "host_tariff_set", {"cores": 2, "code": "0123456789"}),
                              call(9, "host_tariff", {"action": "preview", "clear": "port,traffic,ram,disk"})])
        argvs = [c["argv"] for c in s.calls]
        self.assertEqual(argvs, [
            ["host", "tariff", "set", "--port=1000", "--traffic=unlimited", "--code=0123456789", "--by-mcp",
             "--json"],
            ["host", "tariff", "set", "--traffic=1,5", "--clear=port", "--dry", "--json"],
            ["host", "tariff", "show", "--json"]])
        for i in (3, 5, 7, 8, 9):                                        # before kaskadctl
            self.assertEqual(s.error(i)["code"], -32602, i)
