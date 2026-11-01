"""F3 (3.3.7.15, PLAN-n13 Ш3, Е4, 8.4.1): «Настройки протокола» одним действием — ops.inbound_settings (порт и MTU
одним применением, «Что изменится», подтверждение, ``ver``), псевдоним inbound_port, «Вернуть прежние»
(inbound_settings_undo), окно inbound_settings_view, CLI ``gw inbound set/undo/show``, API ``…/settings``."""

import json
import os
import subprocess
import sys
import tempfile
from unittest import mock

from kaskad import errors, paths, state
from kaskad.errors import ConflictError, KaskadError
from kaskad.gw import inlog, model, ops, pending, reissue
from kaskad.mcp import server as mcp_server
from tests import test_r15_reissue as r15r
from tests.gw.test_awg import AwgCase
from tests.gw.test_wg import WgCase
from tests.panel.panelcase import PanelCase
from tests.test_gw_mtu import proc_route
from tests.gwcase import GwCase
from tests.test_gw_ops import CliGwCase
from tests.test_gw_vless_front import FrontCase

with open(os.path.join(os.path.dirname(mcp_server.__file__), "actions.json"), encoding="utf-8") as _f:
    ACTIONS = json.load(_f)


OLD_SIDE = r"""
import json
from kaskad import state
from kaskad.gw import mtu
st = state.load()
g = st["gateway"]["inbounds"]
print(json.dumps({"wg": [g["wg"]["port"], mtu.client_mtu(st, "wg"), mtu.server_for(st, "wg")],
                  "awg2": [g["awg2"]["port"], mtu.client_mtu(st, "awg2"), mtu.server_for(st, "awg2")]}))
"""


def tearDownModule():
    r15r.tearDownModule()


class SettingsWorld(object):
    """The gateway on with wg + awg2 (dummies), the card 1500; Мама: Телефон (wg, awg2); Дача: Роутер (awg2)."""

    def world(self, card=1500):
        self.write("/proc/net/route", proc_route([("eth0", "00000000", 0, "00000000")]))
        self.write("/sys/class/net/eth0/mtu", "%d\n" % card)
        with mock.patch.object(self, "ok", self.cli):        # the panel case has its own ok()
            self.enabled_world()
        self.cli("gw", "inbound", "install", "wg")
        self.cli("gw", "inbound", "install", "awg2")
        with state.transaction() as st:
            st["gateway"]["inbounds"]["awg2"]["line"] = "2.0"
            st["gateway"]["inbounds"]["awg2"]["params"] = {"S4": "15", "MTU": "1280"}
        self.cli("gw", "user", "add", "Мама")
        self.cli("gw", "user", "add", "Дача")
        self.cli("gw", "device", "add", "Мама", "Телефон", "--access", "wg,awg2")
        self.cli("gw", "device", "add", "Дача", "Роутер", "--access", "awg2")
        self.cli("gw", "apply")
        st = state.load()
        self.dev = dict((d["name"], d["id"]) for d in model.devices(st))
        return st

    def cli(self, *argv):
        return CliGwCase.ok(self, *argv)

    def files(self):
        out = {}
        for p in (paths.state(), reissue.path(), ops.settings_last_path(), self.ads["awg2"].conf_path("dummy.conf"),
                  self.ads["wg"].conf_path("dummy.conf")):
            out[p] = None
            if os.path.exists(p):
                with open(p, "rb") as f:
                    out[p] = (os.stat(p).st_mtime_ns, f.read())
        return out


class SettingsTest(SettingsWorld, CliGwCase):
    def test_port_and_mtu_of_awg2_in_one_application(self):
        self.world()
        calls = []
        real = ops._apply

        def counting(*a, **kw):
            calls.append(1)
            return real(*a, **kw)
        before = self.ads["awg2"].calls.count("apply")
        with mock.patch.object(ops, "_apply", counting):
            res = ops.inbound_settings(self.w.runner, "awg2", port=41999, mtu=1300, yes=True)
        self.assertEqual(len(calls), 1)                                    # one _change, one _apply
        self.assertEqual(self.ads["awg2"].calls.count("apply") - before, 1)   # the adapter applied once
        rec = model.inbound(state.load(), "awg2")
        self.assertEqual((rec["port"], rec["params"]["MTU"], rec["params"]["S4"]), (41999, "1300", "15"))
        self.assertEqual(res["changed"], {"port": {"old": 41023, "new": 41999}, "mtu": {"old": 1280, "new": 1300}})
        m = reissue.load()["awg2"]
        self.assertEqual(sorted(m), sorted([self.dev["Телефон"], self.dev["Роутер"]]))
        for v in m.values():
            self.assertEqual((v["what"], v["hard"], v["by"]), (["port", "mtu"], True, res["id"]))
        self.assertIn("не подключатся", res["text"])
        last = ops._last_read()["awg2"]
        self.assertEqual((last["id"], last["old"], last["new"]), (res["id"], {"port": 41023, "mtu": 1280},
                                                                  {"port": 41999, "mtu": 1300}))
        self.assertEqual(os.stat(ops.settings_last_path()).st_mode & 0o777, 0o600)

    def test_the_alias_inbound_port_gives_the_same(self):
        self.world()
        r1 = ops.inbound_port(self.w.runner, "wg", "51999")
        for k in ("kind", "field", "old", "port", "reissue", "text", "warnings", "apply"):
            self.assertIn(k, r1)                                           # the old form of the answer
        m1 = reissue.load()["wg"][self.dev["Телефон"]]
        ops.inbound_settings_undo(self.w.runner, "wg")
        reissue.issued("wg", self.dev["Телефон"])
        r2 = ops.inbound_settings(self.w.runner, "wg", port=51999, yes=True)
        for k in ("kind", "field", "old", "port", "text"):
            self.assertEqual(r1[k], r2[k], k)
        self.assertEqual([d["id"] for d in r1["reissue"]], [d["id"] for d in r2["reissue"]])
        m2 = reissue.load()["wg"][self.dev["Телефон"]]
        self.assertEqual((m1["what"], m1["hard"]), (m2["what"], m2["hard"]))
        self.assertEqual((r1["field"], r1["old"], r1["port"]), ("port", 51820, 51999))

    def test_dry_writes_nothing(self):
        self.world()
        reissue.mark("wg", [self.dev["Телефон"]], ["mtu"], False)
        before = self.files()
        calls = list(self.ads["awg2"].calls)
        res = ops.inbound_settings(self.w.runner, "awg2", port=41999, mtu=1300, dry=True)
        self.assertTrue(res["dry"])
        self.assertEqual(set(res["changed"]), {"port", "mtu"})
        self.assertEqual([r["word"] for r in res["restart"]["rows"]], ["awg", "live"])
        self.assertEqual(len(res["reissue"]["hard"]), 2)
        self.assertTrue(res["need_yes"])
        self.assertEqual(self.files(), before)
        self.assertEqual(self.ads["awg2"].calls, calls)

    def test_need_yes_without_yes_changes_nothing(self):
        self.world()
        before = self.files()
        with self.assertRaises(ConflictError) as cm:
            ops.inbound_settings(self.w.runner, "awg2", port=41999)
        self.assertEqual(cm.exception.data["need"], "yes")
        self.assertEqual(len(cm.exception.data["plan"]["reissue"]["hard"]), 2)
        self.assertNotIn("st_new", cm.exception.data["plan"])             # never the whole state in an answer
        self.assertEqual(self.files(), before)
        # MTU alone: soft marks, nothing to confirm
        res = ops.inbound_settings(self.w.runner, "awg2", mtu=1290)
        self.assertEqual(model.inbound(state.load(), "awg2")["params"]["MTU"], "1290")
        self.assertFalse(any(m["hard"] for m in reissue.load()["awg2"].values()))
        self.assertIn("желательно", res["text"])

    def test_undo_brings_back_and_marks_who_got_the_new_settings(self):
        self.world()
        res = ops.inbound_settings(self.w.runner, "awg2", port=41999, mtu=1300, yes=True)
        ops.issue(self.w.runner, self.dev["Роутер"], inbound="awg2")      # given the new settings meanwhile
        self.assertNotIn(self.dev["Роутер"], reissue.load()["awg2"])
        undo = ops.inbound_settings_undo(self.w.runner, "awg2")
        rec = model.inbound(state.load(), "awg2")
        self.assertEqual((rec["port"], rec["params"]["MTU"]), (41023, "1280"))
        self.assertEqual(undo["undone"], {"port": {"old": 41999, "new": 41023}, "mtu": {"old": 1300, "new": 1280}})
        m = reissue.load()["awg2"]
        self.assertEqual(sorted(m), [self.dev["Роутер"]])                  # the phone never got them: its mark gone
        self.assertEqual((m[self.dev["Роутер"]]["what"], m[self.dev["Роутер"]]["hard"]), (["port", "mtu"], True))
        self.assertIsNone(m[self.dev["Роутер"]].get("by"))
        self.assertNotIn("awg2", ops._last_read())
        with self.assertRaises(KaskadError) as cm:                         # no «forward» again
            ops.inbound_settings_undo(self.w.runner, "awg2")
        self.assertEqual(cm.exception.code, errors.USAGE)
        self.assertNotEqual(res["id"], None)

    def test_undo_refuses_a_port_taken_since(self):
        self.world()
        ops.inbound_settings(self.w.runner, "wg", port=51999, yes=True)
        with state.transaction() as st:                                    # somebody took 51820/udp meanwhile
            st["forwards"].append({"proto": "udp", "port": 51820, "to": "198.51.100.7", "to_port": 51820,
                                   "name": "", "added": "2026-10-31T00:00:00Z"})
        before = self.files()
        with self.assertRaises(KaskadError) as cm:
            ops.inbound_settings_undo(self.w.runner, "wg")
        self.assertEqual((cm.exception.data["why"], cm.exception.data["port"]), ("port_busy", 51820))
        self.assertEqual(self.files(), before)
        self.assertEqual(model.inbound(state.load(), "wg")["port"], 51999)

    def test_undo_after_the_port_was_changed_elsewhere(self):
        # REV-3315-a1 С-2: another action moved the port (front.sync, an older version after a rollback…)
        self.world()
        ops.inbound_settings(self.w.runner, "wg", port=51999, yes=True)
        with state.transaction() as st:
            st["gateway"]["inbounds"]["wg"]["port"] = 52500
        self.assertIsNone(ops.inbound_settings_view(self.w.runner, "wg")["last"])
        with self.assertRaises(ConflictError) as cm:
            ops.inbound_settings_undo(self.w.runner, "wg")
        self.assertEqual((cm.exception.data["why"], cm.exception.data["now"]), ("moved", {"port": 52500}))
        self.assertIn("сейчас порт 52500", str(cm.exception))
        self.assertEqual(model.inbound(state.load(), "wg")["port"], 52500)
        self.assertNotIn("wg", ops._last_read())                          # nothing of it is in force: the record goes
        with self.assertRaises(KaskadError) as cm:
            ops.inbound_settings_undo(self.w.runner, "wg")
        self.assertEqual(cm.exception.code, errors.USAGE)

    def test_undo_after_the_mtu_was_changed_elsewhere(self):
        self.world()
        ops.inbound_settings(self.w.runner, "awg2", mtu=1300)                # F3: 1280 -> 1300 (soft: no --yes)
        self.write("/sys/class/net/eth0/mtu", "1390\n")                      # the card narrows: S4 15 + 1300 no more
        self.cli("gw", "apply")
        ops.mtu_reissue(self.w.runner, "awg2")                               # «Выдать с меньшим MTU»
        cur = model.inbound(state.load(), "awg2")["params"]["MTU"]
        self.assertNotEqual(cur, "1300")
        self.assertIsNone(ops.inbound_settings_view(self.w.runner, "awg2")["last"])
        with self.assertRaises(ConflictError) as cm:
            ops.inbound_settings_undo(self.w.runner, "awg2")
        self.assertEqual(cm.exception.data["why"], "moved")
        self.assertEqual(model.inbound(state.load(), "awg2")["params"]["MTU"], cur)   # never 1280 over it

    def test_undo_refuses_an_mtu_the_card_no_longer_takes(self):
        self.world()
        ops.inbound_settings(self.w.runner, "wg", mtu=1320)
        ops.inbound_settings(self.w.runner, "wg", mtu=1300)                  # the last: 1320 -> 1300
        with state.transaction() as st:
            st["gateway"]["mtu"]["nic"]["mtu"] = 1380                       # the card is 1380 now: WG at most 1300
        with self.assertRaises(KaskadError) as cm:
            ops.inbound_settings_undo(self.w.runner, "wg")
        self.assertIn("не выше 1300", str(cm.exception))
        self.assertEqual(model.inbound(state.load(), "wg")["mtu"], 1300)
        self.assertIn("wg", ops._last_read())                              # still in force: the record stays

    def test_the_id_given_to_guard_is_the_record_of_settings_last(self):
        # REV-3315-a1 З-2 (CONTRACT-pending-3315: id = settings-last[kind].id once the guard lets go of its lock)
        self.world()
        seen = []

        def guard(runner, st_old, plan, scope, who=None, safe=False, apply=None):
            before = (ops._last_read().get(scope) or {}).get("id")
            out = apply()
            seen.append((plan["id"], before, (ops._last_read().get(scope) or {}).get("id")))
            return out

        def refuse(runner, st_old, plan, scope, who=None, safe=False, apply=None):
            raise ConflictError("a live wait", data={"why": "pending"})   # F7: 409 before apply()
        with mock.patch.object(pending, "guard", guard):
            ops.inbound_settings(self.w.runner, "wg", port=51999, yes=True)
        first = ops._last_read()["wg"]
        self.assertEqual(seen[0], (first["id"], None, first["id"]))
        with mock.patch.object(pending, "guard", refuse):
            with self.assertRaises(ConflictError):
                ops.inbound_settings(self.w.runner, "wg", mtu=1300)
        self.assertEqual(ops._last_read()["wg"], first)                   # the record of the live wait stays
        with mock.patch.object(pending, "guard", guard), mock.patch.object(ops, "_last_can_write", lambda: False):
            res = ops.inbound_settings(self.w.runner, "wg", mtu=1300)
        self.assertEqual(seen[1][0], None)                                 # no record — no id, no wait possible
        self.assertTrue(any("Вернуть прежние" in w for w in res["warnings"]), res["warnings"])
        self.assertEqual(ops._last_read()["wg"], first)
        self.assertEqual(model.inbound(state.load(), "wg")["mtu"], 1300)   # the application itself went

    def test_a_record_that_fails_inside_the_guard_applies_nothing(self):
        self.world()
        with mock.patch.object(ops, "_last_write", side_effect=OSError("read-only file system")):
            with self.assertRaises(KaskadError) as cm:
                ops.inbound_settings(self.w.runner, "wg", port=51999, yes=True)
        self.assertIn("Ничего не применено", str(cm.exception))
        self.assertEqual(model.inbound(state.load(), "wg")["port"], 51820)

    def test_a_failed_application_puts_the_record_back(self):
        self.world()
        ops.inbound_settings(self.w.runner, "wg", port=51999, yes=True)
        first = ops._last_read()["wg"]
        with self.assertRaises(KaskadError):
            ops.inbound_settings(self.w.runner, "wg", mtu=1300, ver="0" * 12)   # another tab changed it first
        self.assertEqual(ops._last_read()["wg"], first)
        self.assertEqual(model.inbound(state.load(), "wg")["port"], 51999)

    def test_a_manual_undo_disarms_a_wait_of_this_protocol_only(self):
        self.world()
        ops.inbound_settings(self.w.runner, "wg", port=51999, yes=True)
        seen = []
        with mock.patch.object(pending, "view", lambda: {"scope": "awg2", "left": 90}), \
                mock.patch.object(pending, "disarm", lambda runner: seen.append("disarm")):
            ops.inbound_settings_undo(self.w.runner, "wg")
        self.assertEqual(seen, [])
        ops.inbound_settings(self.w.runner, "wg", port=51999, yes=True)
        with mock.patch.object(pending, "view", lambda: {"scope": "wg", "left": 90}), \
                mock.patch.object(pending, "disarm", lambda runner: seen.append("disarm")):
            ops.inbound_settings_undo(self.w.runner, "wg")
        self.assertEqual(seen, ["disarm"])
        # the timer of F7: its own record only
        ops.inbound_settings(self.w.runner, "wg", port=51999, yes=True)
        with self.assertRaises(ConflictError) as cm:
            ops.inbound_settings_undo(self.w.runner, "wg", pending={"id": "a-00000000"})
        self.assertEqual(cm.exception.data["why"], "changed")
        ops.inbound_settings_undo(self.w.runner, "wg", pending={"id": ops._last_read()["wg"]["id"]})
        self.assertEqual(model.inbound(state.load(), "wg")["port"], 51820)

    def test_guard_gets_the_contract_plan(self):
        self.world()
        got = []

        def guard(runner, st_old, plan, scope, who=None, safe=False, apply=None):
            got.append((plan, scope, who, safe))
            out = apply()
            out["pending"] = {"token": "0" * 16, "deadline": 1, "scope": scope, "left": 120}
            return out
        with mock.patch.object(pending, "guard", guard):
            res = ops.inbound_settings(self.w.runner, "wg", port=51999, yes=True, safe=True,
                                       who={"via": "panel", "ip": "10.77.1.10"})
        plan, scope, who, safe = got[0]
        self.assertEqual((scope, who["via"], safe), ("wg", "panel", True))
        self.assertEqual(plan["id"], ops._last_read()["wg"]["id"])
        self.assertEqual(plan["changed"]["port"]["new"], 51999)
        self.assertEqual(model.inbound(plan["st_new"], "wg")["port"], 51999)
        self.assertIn("kaskadctl gw pending confirm", res["text"])

    def test_the_alias_says_who_over_ssh(self):
        self.world()
        got = []

        def guard(runner, st_old, plan, scope, who=None, safe=False, apply=None):
            got.append(who)
            return apply()
        with mock.patch.object(pending, "guard", guard), \
                mock.patch.dict(os.environ, {"SSH_CONNECTION": "203.0.113.5 50000 198.51.100.1 22"}):
            ops.inbound_port(self.w.runner, "wg", "51999")
            self.cli("gw", "inbound", "set", "wg", "--mtu", "1300")
        self.assertEqual(got, [{"via": "ssh", "ip": "203.0.113.5"}] * 2)

    def test_the_admin_hit_by_the_contract(self):
        # CONTRACT-pending-3315, добавка 2: admin_hit gets ``changed`` and gives a code only if the change cuts; the
        # warning is hit_text, not the code; need_yes because of it — only at a hit
        self.world()
        calls = []

        def hit(st_old, st_new, scope, who=None, changed=None):
            calls.append(sorted(changed or {}))
            return "pool" if pending.cuts(scope, changed) else None
        with mock.patch.object(pending, "admin_hit", hit), \
                mock.patch.object(pending, "hit_text", lambda code, kind: "вы подключены через %s (%s)" % (kind, code)):
            only_mtu = ops.inbound_settings(self.w.runner, "awg2", mtu=1300, dry=True)
            port = ops.inbound_settings(self.w.runner, "awg2", port=41999, dry=True)
        self.assertEqual(calls, [["mtu"], ["port"]])
        self.assertEqual((only_mtu["admin_hit"], only_mtu["need_yes"], only_mtu["warnings"]), (None, False, []))
        self.assertEqual(port["admin_hit"], "pool")
        self.assertIn("вы подключены через awg2 (pool)", port["warnings"])
        self.assertNotIn("pool", port["warnings"])
        self.assertTrue(port["need_yes"])

    def test_refusals_before_any_lock(self):
        self.world()
        before = self.files()
        for kw, code in ((dict(net="10.77.20.0/24"), errors.USAGE), (dict(tls="1.3"), errors.USAGE),
                         (dict(proto="udp"), errors.USAGE), (dict(mtu=True), errors.USAGE),
                         (dict(mtu=1321), errors.USAGE), (dict(port="70000"), None)):
            with self.assertRaises(KaskadError, msg=kw) as cm:
                ops.inbound_settings(self.w.runner, "wg", yes=True, **kw)
            if code is not None:
                self.assertEqual(cm.exception.code, code, kw)
        self.assertIn("следующем выпуске", str(self.capture(ops.inbound_settings, self.w.runner, "wg", net="x")))
        self.assertEqual(self.files(), before)
        res = ops.inbound_settings(self.w.runner, "wg", port=51820, yes=True)     # the same port: nothing to do
        self.assertEqual((res["changed"], res["apply"]), ({}, None))

    def capture(self, fn, *a, **kw):
        try:
            fn(*a, **kw)
        except KaskadError as e:
            return e
        self.fail("no refusal")

    def test_the_port_closed_hint_after_a_port_change(self):
        self.world()
        ops.inbound_settings(self.w.runner, "wg", port=51999, yes=True)
        t = int(inlog._read_seen()["wg"]["issued"])
        self.assertTrue(inlog.closed("wg", now=t + 601))
        inlog.note_sessions([{"kind": "wg", "device": self.dev["Телефон"], "since": t + 5}], now=t + 10)
        self.assertFalse(inlog.closed("wg", now=t + 601))

    def test_the_collector_round_gives_the_hint_its_sessions(self):
        from kaskad.gw import collect
        self.world()
        self.now = 1730300000
        inlog.mark_issued("wg", now=self.now - 100)                        # the port changed 100 s ago
        self.ads["wg"].running = True
        phone = {"device": self.dev["Телефон"], "ip": "10.77.1.10", "rx": 1, "tx": 1}
        self.ads["wg"].sessions = [dict(phone, since=self.now - 150)]       # the handshake from before the change
        collect.tick(self.w.runner)
        self.assertTrue(inlog.closed("wg", now=self.now + 600))
        self.ads["wg"].sessions = [dict(phone, since=self.now - 5)]         # it came on the new port
        collect.tick(self.w.runner)
        self.assertFalse(inlog.closed("wg", now=self.now + 600))

    def test_the_restart_table(self):
        st = {"gateway": {"inbounds": {}}}
        want = {"wg": "syncconf", "awg1": "awg", "awg2": "awg", "awg3": "awg", "openvpn": "ovpn", "ocserv": "ocserv",
                "sstp": "sstp", "vless": "xray", "socks5": "xray", "hy2": "xray"}
        for kind, word in want.items():
            rows = ops._restart_rows(st, kind, ["port"])["rows"]
            self.assertEqual([r["word"] for r in rows], [word], kind)
            self.assertTrue(rows[0]["text"])
        for kind in ("wg", "awg1", "awg2", "awg3"):
            self.assertEqual([r["word"] for r in ops._restart_rows(st, kind, ["mtu"])["rows"]], ["live"])
        for kind in ("l2tp", "pptp", "ikev2"):
            self.assertEqual(ops._restart_rows(st, kind, ["port"])["rows"], [])
        # REV-3315-a1 З-3: a new port — no promise that the connections come back by themselves
        for kind in ("awg2", "openvpn"):
            text = ops._restart_rows(st, kind, ["port"])["rows"][0]["text"]
            self.assertIn("не подключатся, пока не получат новые", text, kind)
            self.assertNotIn("восстановятся сами", text, kind)
        self.assertIn("на ходу", ops._restart_rows(st, "awg2", ["mtu"])["rows"][0]["text"])
        self.assertTrue(pending.via_xray("vless", {"port": {}}))
        self.assertFalse(pending.via_xray("wg", {"port": {}}))
        self.assertFalse(pending.via_xray("socks5", {"mtu": {}}))

    def test_cli_set_show_undo(self):
        self.world()
        rc, doc, err = self.run_json("gw", "inbound", "set", "wg", "--port", "51999", "--dry")
        self.assertEqual(rc, 0, err)
        self.assertEqual(doc["plan"]["changed"]["port"]["new"], 51999)
        out, _ = self.ok("gw", "inbound", "set", "wg", "--port", "51999", "--dry")
        self.assertIn("что изменится — порт 51820 → 51999", out)
        rc, doc, err = self.run_json("gw", "inbound", "set", "wg", "--port", "51999")     # a device would lose it
        self.assertEqual((rc, doc["data"]["need"]), (errors.CONFLICT, "yes"))
        self.assertTrue(doc["error"].endswith("Применить — с --yes."), doc["error"])
        self.assertEqual(model.inbound(state.load(), "wg")["port"], 51820)
        rc, doc, err = self.run_json("gw", "inbound", "set", "wg", "--port", "51999", "--mtu", "1300", "--yes")
        self.assertEqual(rc, 0, err)
        out, _ = self.ok("gw", "inbound", "show", "wg")
        self.assertIn("  порт: 51999/udp", out)
        self.assertIn("  MTU: 1300 (можно от 1280 до 1320; сервер 1300)", out)
        self.assertIn("не подключатся — выдайте настройки заново: Телефон (Мама)", out)
        self.assertIn("kaskadctl gw inbound undo wg", out)
        rc, v, _ = self.run_json("gw", "inbound", "show", "wg")
        self.assertEqual(v["last"]["changed"]["port"], {"old": 51820, "new": 51999})
        self.assertEqual(v["ver"], ops._settings_ver(state.load(), "wg"))
        out, _ = self.ok("gw", "inbound", "undo", "wg")
        self.assertIn("прежние настройки возвращены — порт 51999 → 51820, MTU 1300 → 1280", out)
        rc, doc, _ = self.run_json("gw", "inbound", "set", "wg", "--mtu", "abc", "--yes")
        self.assertEqual(rc, errors.USAGE)

    def test_old_versions_read_what_an_application_wrote(self):
        self.world()
        ops.inbound_settings(self.w.runner, "awg2", port=41999, mtu=1300, yes=True)
        ops.inbound_settings(self.w.runner, "wg", mtu=1310)
        for ver, rev in sorted(r15r.OLD.items()):
            env = dict(os.environ, PYTHONPATH=r15r.old_lib(rev), KASKAD_ROOT=os.environ["KASKAD_ROOT"])
            p = subprocess.run([sys.executable, "-c", OLD_SIDE], env=env, capture_output=True, text=True, timeout=60,
                               cwd=tempfile.gettempdir())
            self.assertEqual(p.returncode, 0, "%s: %s" % (ver, p.stderr[-800:]))
            got = json.loads(p.stdout.strip().splitlines()[-1])
            self.assertEqual(got, {"wg": [51820, 1310, 1310], "awg2": [41999, 1300, 1296]}, ver)   # the same values
        self.assertEqual(sorted(ops._last_read()), ["awg2", "wg"])        # settings-last: new only, old ones skip it

    def test_mcp_catalog_hides_the_new_commands_with_a_reason(self):
        for cmd in ("gw inbound set", "gw inbound undo", "gw inbound show"):
            self.assertTrue(ACTIONS["hidden"][cmd]["ru"] and ACTIONS["hidden"][cmd]["en"], cmd)
        self.assertEqual(ACTIONS["actions"]["inbound_port"]["cmd"], ["gw", "inbound", "port"])   # the old tool stays


class SettingsApiTest(SettingsWorld, PanelCase, CliGwCase):
    def test_get_put_undo_with_ver(self):
        self.world()
        v = self.ok("GET", "/api/gw/inbounds/wg/settings")
        self.assertEqual(self.call("GET", "/api/gw/inbounds/wg/settings",
                                   headers={"Sec-Fetch-Site": "cross-site"}).status, 403)   # runs=True
        self.assertEqual((v["mtu"]["limit"], v["port"]["port"]), (1320, 51820))
        self.assertTrue(v["ver"])
        dry = self.ok("PUT", "/api/gw/inbounds/wg/settings", {"port": 51999, "dry": True})     # no ver needed
        self.assertTrue(dry["need_yes"])
        self.assertEqual(self.err(400, "PUT", "/api/gw/inbounds/wg/settings", {"port": 51999, "yes": True})["code"],
                         "ver_required")
        stale = self.err(409, "PUT", "/api/gw/inbounds/wg/settings", {"port": 51999, "yes": True, "ver": "0" * 12})
        self.assertEqual(stale["code"], "object_changed")
        self.assertEqual(model.inbound(state.load(), "wg")["port"], 51820)
        need = self.err(409, "PUT", "/api/gw/inbounds/wg/settings", {"port": 51999, "ver": v["ver"]})
        self.assertEqual(need["data"]["need"], "yes")
        self.assertNotIn("--yes", need["error"])                           # the panel asks with its own button
        done = self.ok("PUT", "/api/gw/inbounds/wg/settings", {"port": 51999, "mtu": 1300, "yes": True,
                                                               "ver": v["ver"]})
        self.assertEqual((done["port"], done["pending"]), (51999, None))
        self.assertNotIn("st_new", done["plan"])
        self.err(409, "PUT", "/api/gw/inbounds/wg/settings", {"mtu": 1310, "yes": True, "ver": v["ver"]})   # old ver
        back = self.ok("POST", "/api/gw/inbounds/wg/settings/undo")
        self.assertEqual(back["undone"]["port"], {"old": 51999, "new": 51820})

    def test_the_old_port_route_says_it_is_the_panel(self):
        self.world()
        got = []

        def guard(runner, st_old, plan, scope, who=None, safe=False, apply=None):
            got.append(who)
            return apply()
        with mock.patch.object(pending, "guard", guard):
            self.ok("PUT", "/api/gw/inbounds/wg/port", {"port": 51999})
            ver = self.ok("GET", "/api/gw/inbounds/wg/settings")["ver"]
            self.ok("PUT", "/api/gw/inbounds/wg/settings", {"mtu": 1300, "ver": ver})
        self.assertEqual([w["via"] for w in got], ["panel", "panel"])
        self.assertTrue(all(w["ip"] for w in got))

    def test_refusals(self):
        self.world()
        ver = self.ok("GET", "/api/gw/inbounds/wg/settings")["ver"]
        for body in ({"port": True}, {"port": "abc"}, {"mtu": True}, {"mtu": "abc"}, {"mtu": 1300.5}, {"extra": 1},
                     {"net": "10.77.20.0/24"}, {"tls": True}, {"yes": "yes"}):
            self.err(400, "PUT", "/api/gw/inbounds/wg/settings", dict(body, ver=ver))
        self.err(400, "PUT", "/api/gw/inbounds/zzz/settings", {"port": 51999, "ver": ver})
        self.assertIn(self.call("PUT", "/api/gw/inbounds/wg/settings", {"port": 51999, "ver": ver},
                                tunnel=False).status, (401, 403))
        self.assertEqual(self.call("PUT", "/api/gw/inbounds/wg/settings", {"port": 51999, "ver": ver},
                                   csrf=False).status, self.call("PUT", "/api/gw/inbounds/wg/port", {"port": 51999},
                                                                 csrf=False).status)
        self.assertEqual(model.inbound(state.load(), "wg")["port"], 51820)


class RoamingPortTest(SettingsWorld, CliGwCase):
    """П4, стенд T-3315 2.3 и 2.4: порт WG меняется ``syncconf`` без перезапуска — живая сессия устройства сама
    переезжает на новый порт роумингом (сервер шлёт с нового порта, клиент принимает), рукопожатие позже смены есть,
    а в сохранённых настройках устройства — прежний порт. «Сменился порт» у WireGuard снимает только выдача; без правки
    круг сборщика снимал пометку, а «Вернуть прежние» после этого метил устройство заново. У AmneziaWG порт меняется
    перезапуском (старая сессия рвётся) — рукопожатие позже смены снимает пометку (П5 З-1)."""

    def setUp(self):
        super().setUp()
        p = mock.patch.object(reissue, "_now", lambda: int(self.now))     # at.port on the clock of the collector
        p.start()
        self.addCleanup(p.stop)

    def round(self, kind, name, after):
        """A round of the collector: ``name`` has a handshake ``after`` s after now - the session of ``kind``."""
        from kaskad.gw import collect
        self.ads[kind].running = True
        self.ads[kind].sessions = [{"device": self.dev[name], "ip": "10.77.1.10", "since": int(self.now) + after,
                                    "rx": 1, "tx": 1}]
        self.now += max(after, 0) + 5
        collect.tick(self.w.runner)

    def test_wg_a_roamed_session_keeps_the_mark_an_issue_takes_it_off(self):
        self.world()
        ops.inbound_settings(self.w.runner, "wg", port=51825, yes=True)
        phone = self.dev["Телефон"]
        self.assertEqual(reissue.load()["wg"][phone]["what"], ["port"])
        self.now += 140
        self.round("wg", "Телефон", 0)                                     # 145 s later: the roamed live session
        self.assertEqual([r["id"] for r in reissue.view(state.load())["wg"]["hard"]], [phone])
        ops.issue(self.w.runner, phone, inbound="wg")                      # the new settings given
        self.assertNotIn("wg", reissue.load())
        self.round("wg", "Телефон", 1)                                     # and its handshake: nothing to put back
        self.assertNotIn("wg", reissue.load())

    def test_awg_a_handshake_after_the_restart_takes_the_port_off(self):
        self.world()
        ops.inbound_settings(self.w.runner, "awg2", port=41999, yes=True)
        self.assertEqual(sorted(reissue.load()["awg2"]), sorted([self.dev["Телефон"], self.dev["Роутер"]]))
        self.round("awg2", "Роутер", -3)                                   # a handshake from before the restart
        self.assertEqual(sorted(reissue.load()["awg2"]), sorted([self.dev["Телефон"], self.dev["Роутер"]]))
        self.round("awg2", "Роутер", 1)                                    # on the new port: the new settings
        self.assertEqual(sorted(reissue.load()["awg2"]), [self.dev["Телефон"]])

    def test_awg_the_subscription_a_handshake_and_undo_mark_it_hard(self):
        """З-1: the router took the new port from the subscription (``mark=False``: not «выдали») and connected —
        its mark goes; «Вернуть прежние» — it holds the new port now: a hard mark (П4 left none — the panel silent
        till the next refresh of the subscription); the phone never got the new port — no mark."""
        self.world()
        ops.inbound_settings(self.w.runner, "awg2", port=41999, yes=True)
        ops.issue(self.w.runner, self.dev["Роутер"], inbound="awg2", mark=False)     # /p/<t>/sub
        self.assertIn(self.dev["Роутер"], reissue.load()["awg2"])
        self.round("awg2", "Роутер", 2)
        self.assertNotIn(self.dev["Роутер"], reissue.load()["awg2"])
        ops.inbound_settings_undo(self.w.runner, "awg2")
        m = reissue.load()["awg2"]
        self.assertEqual(sorted(m), [self.dev["Роутер"]])
        self.assertEqual((m[self.dev["Роутер"]]["what"], m[self.dev["Роутер"]]["hard"]), (["port"], True))

    def test_undo_after_a_roamed_session_marks_nobody(self):
        """2.4: the wait of F7 ran out (or «Вернуть прежние»): the phone was never given the new port — its old
        settings work again, no mark. Without the fix the roamed handshake «proved» the new settings and the undo marked
        it hard."""
        self.world()
        ops.inbound_settings(self.w.runner, "wg", port=51826, yes=True)
        self.round("wg", "Телефон", 2)
        ops.inbound_settings_undo(self.w.runner, "wg")
        self.assertEqual(model.inbound(state.load(), "wg")["port"], 51820)
        self.assertNotIn("wg", reissue.load())


class PreviewGivenTest(SettingsWorld, CliGwCase):
    """П5 З-2: «Скачать мои новые настройки» (F7) before «Применить» — the same выдача: the application of exactly
    that change leaves this device without a mark (П4: only «Выдать» took it off — the administrator's own device
    waited after every change made through its tunnel); another change, a stale preview — the mark as for the rest."""

    def two(self):
        self.world()
        self.cli("gw", "device", "add", "Мама", "Ноутбук", "--access", "wg")
        self.dev = dict((d["name"], d["id"]) for d in model.devices(state.load()))
        return self.dev["Телефон"], self.dev["Ноутбук"]

    def test_the_previewed_device_is_given(self):
        phone, laptop = self.two()
        ops.issue_preview(self.w.runner, phone, "wg", {"port": 51826})
        self.assertTrue(os.path.exists(ops.preview_path()))
        with open(ops.preview_path(), encoding="utf-8") as f:
            note = f.read()
        self.assertEqual(oct(os.stat(ops.preview_path()).st_mode & 0o777), "0o600")
        self.assertNotIn("PrivateKey", note)
        self.assertNotIn("private", note.lower())
        res = ops.inbound_settings(self.w.runner, "wg", port=51826, yes=True)
        m = reissue.load()["wg"]
        self.assertEqual(sorted(m), [laptop])
        self.assertTrue(m[laptop]["hard"])
        self.assertEqual([r["id"] for r in res["reissue"]], [laptop])        # «выдайте заново» names the laptop only
        self.assertNotIn("Телефон", res["text"])
        self.assertFalse(os.path.exists(ops.preview_path()))                # used once
        ops.inbound_settings_undo(self.w.runner, "wg")                     # the phone holds the new port now
        m = reissue.load()["wg"]
        self.assertEqual(sorted(m), [phone])
        self.assertEqual((m[phone]["what"], m[phone]["hard"]), (["port"], True))

    def test_another_change_or_a_stale_preview_gives_nothing(self):
        phone, laptop = self.two()
        ops.issue_preview(self.w.runner, phone, "wg", {"port": 51826})
        ops.inbound_settings(self.w.runner, "wg", port=51827, yes=True)    # not what it downloaded
        self.assertEqual(sorted(reissue.load()["wg"]), sorted([phone, laptop]))
        self.assertFalse(os.path.exists(ops.preview_path()))
        ops.issue(self.w.runner, phone, inbound="wg")
        ops.issue(self.w.runner, laptop, inbound="wg")
        ops.issue_preview(self.w.runner, phone, "wg", {"port": 51828})
        with open(ops.preview_path(), encoding="utf-8") as f:
            doc = json.load(f)
        doc["wg"]["at"] -= ops.PREVIEW_TTL + 1                             # the window closed long ago
        with open(ops.preview_path(), "w", encoding="utf-8") as f:
            json.dump(doc, f)
        ops.inbound_settings(self.w.runner, "wg", port=51828, yes=True)
        self.assertEqual(sorted(reissue.load()["wg"]), sorted([phone, laptop]))

    def test_mtu_and_the_port_of_awg_in_one_preview(self):
        self.world()
        phone, router = self.dev["Телефон"], self.dev["Роутер"]
        ops.issue_preview(self.w.runner, phone, "awg2", {"port": 41999, "mtu": 1290})
        ops.inbound_settings(self.w.runner, "awg2", port=41999, mtu=1290, yes=True)
        self.assertEqual(sorted(reissue.load()["awg2"]), [router])
        ops.issue_preview(self.w.runner, router, "awg2", {"mtu": 1300})
        ops.inbound_settings(self.w.runner, "awg2", port=41998, mtu=1300, yes=True)   # the port too: not the same
        self.assertEqual(sorted(reissue.load()["awg2"]), sorted([phone, router]))


class ClosedHintTest(GwCase):
    """Е4 «порт закрыт?» after a change of the port (REV-3315-a1 З-1): only a session that proves a connection after
    the change puts it out — a handshake after it, a growth of xray's counters 30 s after it."""

    T = 10000

    def test_an_old_handshake_does_not_open_the_new_port(self):
        inlog.mark_issued("wg", now=self.T)
        inlog.note_sessions([{"kind": "wg", "device": "d-1", "ip": "10.77.1.10", "since": self.T - 10}], now=self.T + 8)
        self.assertTrue(inlog.closed("wg", now=self.T + 601))
        inlog.note_sessions([{"kind": "wg", "device": "d-1", "ip": "10.77.1.10", "since": self.T + 20}], now=self.T + 28)
        self.assertFalse(inlog.closed("wg", now=self.T + 601))

    def test_xray_needs_a_growth_after_the_change(self):
        inlog.mark_issued("vless", now=self.T)
        inlog.note_sessions([{"kind": "vless", "device": "d-1", "since": self.T - 50, "last": self.T - 5}],
                            now=self.T + 40)
        self.assertTrue(inlog.closed("vless", now=self.T + 601))
        inlog.note_sessions([{"kind": "vless", "device": "d-1", "since": self.T - 50, "last": self.T + 40}],
                            now=self.T + 40)
        self.assertFalse(inlog.closed("vless", now=self.T + 601))

    def test_a_bare_kind_proves_nothing(self):
        inlog.mark_issued("wg", now=self.T)
        self.assertFalse(inlog.note_sessions(["wg"], now=self.T + 8))
        self.assertTrue(inlog.closed("wg", now=self.T + 700))


class FrontPathTest(FrontCase):
    """VLESS on 443 next to the site (site.front): no settings-last, so no id for pending.guard (REV-3315-a1 З-2),
    no safe apply, the switch to TLS said in the plan (П-6)."""

    def test_no_id_no_safe_and_the_plan_says_tls(self):
        st = state.load()
        model.inbound(st, "vless").pop("want", None)                       # a record of 3.3.7 (no mode choice)
        state.save(st)
        self.site_next_to_reality()                                         # VLESS stepped aside to 8443
        plan = ops.inbound_settings(self.w.runner, "vless", port=443, dry=True)
        self.assertTrue(plan["front"])
        self.assertTrue(any("перейдёт на TLS" in w for w in plan["warnings"]), plan["warnings"])
        with self.assertRaises(ConflictError) as cm:
            ops.inbound_settings(self.w.runner, "vless", port=443, yes=True, safe=True)
        self.assertEqual(cm.exception.data["why"], "front")
        ids = []

        def guard(runner, st_old, plan, scope, who=None, safe=False, apply=None):
            ids.append(plan["id"])
            return apply()
        with mock.patch.object(pending, "guard", guard):
            ops.inbound_settings(self.w.runner, "vless", port=443, yes=True)
        self.assertEqual(ids, [None])
        rec = model.inbound(state.load(), "vless")
        self.assertEqual((rec["port"], rec["security"]), (443, "tls"))
        self.assertNotIn("vless", ops._last_read())


class MtuS4MarkTest(SettingsWorld, CliGwCase):
    """REV-3315-a1 П-3: a new S4 is saved — a mark that could not be written goes to the journal, not to the answer."""

    def test_a_failed_mark_after_a_saved_s4(self):
        from kaskad.gw import mtu as gwmtu
        self.world()
        with state.transaction() as st:
            st["gateway"]["inbounds"]["awg2"]["params"]["S4"] = "150"        # the server below 1232
        self.cli("gw", "apply")
        with mock.patch.object(gwmtu, "mark_waiting", side_effect=OSError("locked")):
            res = ops.mtu_s4(self.w.runner, "awg2", yes=True)
        self.assertTrue(res["done"])
        self.assertNotEqual(model.inbound(state.load(), "awg2")["params"]["S4"], "150")
        self.assertIn("не записана (locked)", self.log())


class PlanTextsTest(SettingsWorld, CliGwCase):
    """CONTRACT-pending-3315 добавки 4 и 5 (просьба V1): the plan of F3 carries the words alone for the panel —
    ``hit_text`` (the words of ``admin_hit``, also in ``warnings`` as before), ``reissue_text`` {hard, soft} and
    ``with_text`` (the lines of ``plan_lines``, the same ``tr``); no line — ""."""

    def test_the_words_alone_match_plan_lines_ru_en(self):
        from kaskad.i18n import use_lang
        self.world()
        ip = model.get_device(state.load(), self.dev["Телефон"])["ips"]["awg2"]
        who = {"via": "panel", "ip": ip}
        shared = mock.patch.object(inlog, "shared", lambda kind, kinds: {"with": ["wg"], "gateway": False})
        for lang in ("ru", "en"):
            with self.subTest(lang=lang), use_lang(lang), shared:
                plan = ops.inbound_settings(self.w.runner, "awg2", port=41999, dry=True, who=who)["plan"]
                lines = ops.plan_lines(plan)
                self.assertEqual(plan["hit_text"], pending.hit_text("pool", "awg2"))
                self.assertTrue(plan["hit_text"])
                self.assertIn(plan["hit_text"], plan["warnings"])                   # the old readers keep it
                self.assertTrue(plan["with_text"])
                self.assertIn(plan["with_text"], lines)
                self.assertTrue(plan["reissue_text"]["hard"])
                self.assertIn(plan["reissue_text"]["hard"], lines)
                self.assertEqual(plan["reissue_text"]["soft"], "")
                plain = ops.inbound_settings(self.w.runner, "awg2", mtu=1300, dry=True)["plan"]   # no who, live
                lines = ops.plan_lines(plain)
                self.assertEqual((plain["hit_text"], plain["with_text"], plain["reissue_text"]["hard"]), ("", "", ""))
                self.assertTrue(plain["reissue_text"]["soft"])
                self.assertIn(plain["reissue_text"]["soft"], lines)
        self.assertNotEqual(*[self._hard_of(lang, who) for lang in ("ru", "en")])

    def _hard_of(self, lang, who):
        from kaskad.i18n import use_lang
        with use_lang(lang):
            return ops.inbound_settings(self.w.runner, "awg2", port=41999, dry=True, who=who)["plan"]["reissue_text"][
                "hard"]


class RestartRowsWgTest(WgCase):
    """The rows of «Что изменится» are what the adapters do: WireGuard — the port through syncconf, MTU live."""

    def test_rows_match_the_adapter(self):
        self.st["gateway"]["mtu"] = {"nic": {"dev": "eth0", "mtu": 1500}}
        self.seed()
        self.installed()
        self.wg.apply(self.st, self.sec, self.runner)
        self.node.sysd.calls[:] = []
        for field, change, action in (("port", lambda: ops.settings_mutate(self.st, "wg", {"port": 51830}), "synced"),
                                      ("mtu", lambda: ops.settings_mutate(self.st, "wg", {"mtu": 1300}), "mtu")):
            word = ops._restart_rows(self.st, "wg", [field])["rows"][0]["word"]
            change()
            res = self.wg.apply(self.st, self.sec, self.runner)
            self.assertEqual((word, res["action"]), ({"port": "syncconf", "mtu": "live"}[field], action))
            self.assertNotIn("restart", self.node.sysd.verbs("kaskad-gw-wg.service"))


class RestartRowsAwgTest(AwgCase):
    """AmneziaWG: the port restarts its unit (the row «awg»), MTU goes live (the row «live»)."""

    def test_rows_match_the_adapter(self):
        self.seed()
        self.st["gateway"]["mtu"] = {"nic": {"dev": "eth0", "mtu": 1500}}
        self.core("/etc/kaskad/egress/awg")
        a = self.adapter("awg3")
        a.install(self.runner)
        a.apply(self.st, self.sec, self.runner)
        self.assertEqual([r["word"] for r in ops._restart_rows(self.st, "awg3", ["mtu"])["rows"]], ["live"])
        ops.settings_mutate(self.st, "awg3", {"mtu": 1300})
        self.node.sysd.calls[:] = []
        self.assertEqual(a.apply(self.st, self.sec, self.runner)["action"], "mtu")
        self.assertNotIn("restart", self.node.sysd.verbs("kaskad-gw-awg3.service"))
        self.assertEqual([r["word"] for r in ops._restart_rows(self.st, "awg3", ["port"])["rows"]], ["awg"])
        ops.settings_mutate(self.st, "awg3", {"port": 51912})
        self.assertEqual(a.apply(self.st, self.sec, self.runner)["action"], "restarted")
        self.assertIn("restart", self.node.sysd.verbs("kaskad-gw-awg3.service"))
