"""F4 (3.3.7.15, PLAN-n13 §8.9): версии TLS у VLESS TLS, SSTP и OpenConnect — поле ``inbounds.<kind>.tls.versions``
(«1.2 и 1.3» как раньше · «только 1.3» · «только 1.2»; у VLESS — без «только 1.2»), запись только через «Настройки
протокола» (F3), адаптеры (``minVersion`` xray, ``ssl-protocol`` accel-ppp — перезапуск, ``tls-priorities`` ocserv —
перезапуск), мягкое чтение незнакомого и старые версии 3.3.7.13/14 на записанном."""

import json
import os
import subprocess
import sys
import tempfile
from unittest import mock

from kaskad import errors, i18n, state
from kaskad.errors import ConflictError, KaskadError
from kaskad.gw import model, ops, reissue
from kaskad.gw.inbounds import accel
from kaskad.gw.inbounds import ocserv as ocin
from kaskad.gw.inbounds import tunnel
from tests import test_r15_reissue as r15r
from tests.fakes_gw import register_dummies
from tests.gw.ppp_case import session_row
from tests.gw.test_ocserv import OcCase
from tests.gw.test_ppp_apply import ApplyCase
from tests.panel.panelcase import PanelCase
from tests.test_gw_ops import CliGwCase
from tests.test_gw_vless_front import FrontCase

OLD_SIDE = r"""
import json
from kaskad import state
from kaskad.gw import model
from kaskad.gw.inbounds import accel, ocserv
st = state.load()
ssl = [l for l in accel.conf_text(st, ["sstp"]).splitlines() if l.startswith("ssl-protocol")]
pri = [l for l in ocserv.OcservInbound().conf_text(st).splitlines() if l.startswith("tls-priorities")]
print(json.dumps({"problems": model.validate(st), "ssl": ssl, "pri": pri}))
"""
DEFAULT_PRI = 'tls-priorities = "NORMAL:%SERVER_PRECEDENCE:%COMPAT:-VERS-SSL3.0:-VERS-TLS1.0:-VERS-TLS1.1"'


def tearDownModule():
    r15r.tearDownModule()


class TlsWorld(object):
    """The gateway on with SSTP and OpenConnect (dummies) next to wg; Мама: Ноутбук (windows, sstp), Роутер (mikrotik,
    sstp), Телефон (android, sstp + ocserv)."""

    def dummies(self):
        return register_dummies(("wg", {"ifaces": ["kgw-wg"]}),
                                ("sstp", {"title": "SSTP", "proto": "tcp", "default_port": 4433,
                                          "ifaces": ["kgw-sstp"]}),
                                ("ocserv", {"title": "OpenConnect", "proto": "tcp", "default_port": 4443,
                                            "ifaces": ["kgw-oc"]}))

    def world(self):
        with mock.patch.object(self, "ok", self.cli):        # the panel case has its own ok()
            self.enabled_world()
        for k in ("wg", "sstp", "ocserv"):
            self.cli("gw", "inbound", "install", k)
        self.cli("gw", "user", "add", "Мама")
        self.cli("gw", "device", "add", "Мама", "Ноутбук", "--platform", "windows", "--access", "sstp")
        self.cli("gw", "device", "add", "Мама", "Роутер", "--platform", "mikrotik", "--access", "sstp")
        self.cli("gw", "device", "add", "Мама", "Телефон", "--platform", "android", "--access", "sstp,ocserv")
        self.cli("gw", "apply")
        return state.load()

    def cli(self, *argv):
        return CliGwCase.ok(self, *argv)

    def rec(self, kind):
        return model.inbound(state.load(), kind)


class TlsSettingsTest(TlsWorld, CliGwCase):
    def test_sstp_only_13_one_application_a_warning_no_marks(self):
        self.world()
        plan = ops.inbound_settings(self.w.runner, "sstp", tls="1.3", dry=True)["plan"]
        self.assertEqual(plan["changed"], {"tls": {"old": "1.2-1.3", "new": "1.3"}})
        self.assertEqual([r["word"] for r in plan["restart"]["rows"]], ["sstp"])          # accel-ppp restarts
        self.assertEqual((plan["reissue"], plan["need_yes"]), ({"hard": [], "soft": []}, True))
        warn = " ".join(plan["warnings"])
        self.assertIn("Windows 10 и MikroTik могут не подключиться", warn)
        self.assertIn("Ноутбук (Мама), Роутер (Мама)", warn)
        self.assertNotIn("Телефон", warn)                                                # android has TLS 1.3
        before = state.load()
        with self.assertRaises(ConflictError) as e:
            ops.inbound_settings(self.w.runner, "sstp", tls="1.3")
        self.assertEqual(e.exception.data["need"], "yes")
        self.assertEqual(state.load(), before)                                            # nothing written
        calls = []
        real = ops._apply

        def counting(*a, **kw):
            calls.append(1)
            return real(*a, **kw)
        with mock.patch.object(ops, "_apply", counting):
            res = ops.inbound_settings(self.w.runner, "sstp", tls="1.3", yes=True)
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.rec("sstp")["tls"], {"versions": "1.3"})
        self.assertEqual(res["changed"], {"tls": {"old": "1.2-1.3", "new": "1.3"}})
        self.assertEqual(res["text"], "SSTP: применено — TLS 1.2 и 1.3 → только 1.3.")
        self.assertNotIn("sstp", reissue.load())                                         # files and links the same
        last = ops._last_read()["sstp"]
        self.assertEqual((last["old"], last["new"]), ({"tls": "1.2-1.3"}, {"tls": "1.3"}))

    def test_undo_brings_the_versions_back_and_the_default_leaves_no_field(self):
        self.world()
        ops.inbound_settings(self.w.runner, "sstp", tls="1.2", yes=True)
        self.assertEqual(self.rec("sstp")["tls"], {"versions": "1.2"})
        res = ops.inbound_settings_undo(self.w.runner, "sstp")
        self.assertEqual(res["undone"], {"tls": {"old": "1.2", "new": "1.2-1.3"}})
        self.assertIn("TLS только 1.2 → 1.2 и 1.3", res["text"])
        self.assertNotIn("tls", self.rec("sstp"))                                         # the record of 3.3.7.14
        ops.inbound_settings(self.w.runner, "ocserv", tls="1.3", yes=True)
        ops.inbound_settings(self.w.runner, "ocserv", tls="1.2-1.3")                      # back by hand: no field
        self.assertNotIn("tls", self.rec("ocserv"))

    def test_undo_of_tls_alone_asks_nobody_to_take_the_settings_again(self):
        # REV-3315-a2 З-1: при смене TLS файлы и ссылки те же (SPEC F4) — откат никого не зовёт выдавать заново;
        # порт вместе с TLS — как было: заново тем, кому успели выдать новые
        self.world()
        ops.inbound_settings(self.w.runner, "sstp", tls="1.2", yes=True)
        res = ops.inbound_settings_undo(self.w.runner, "sstp")
        self.assertEqual(res["reissue"], [])
        self.assertEqual(res["text"], "SSTP: прежние настройки возвращены — TLS только 1.2 → 1.2 и 1.3.")
        self.assertNotIn("sstp", reissue.load())
        ops.inbound_settings(self.w.runner, "sstp", port=8443, tls="1.3", yes=True)
        laptop = [d["id"] for d in model.devices(state.load()) if d["name"] == "Ноутбук"][0]
        reissue.issued("sstp", laptop)                                                   # given the new ones meanwhile
        res = ops.inbound_settings_undo(self.w.runner, "sstp")
        self.assertEqual([d["id"] for d in res["reissue"]], [laptop])
        self.assertIn("Ноутбук (Мама)", res["text"])
        self.assertEqual((sorted(reissue.load()["sstp"]), reissue.load()["sstp"][laptop]["what"]), ([laptop], ["port"]))

    def test_undo_after_tls_was_changed_elsewhere(self):
        # слияние с правками А1 (REV-3315-a1 С-2): «в силе ли последнее применение» знает и TLS
        self.world()
        ops.inbound_settings(self.w.runner, "sstp", tls="1.2", yes=True)
        self.assertEqual(ops.inbound_settings_view(self.w.runner, "sstp")["last"]["text"], "TLS 1.2 и 1.3 → только 1.2")
        with state.transaction() as st:
            st["gateway"]["inbounds"]["sstp"]["tls"] = {"versions": "1.3"}
        self.assertIsNone(ops.inbound_settings_view(self.w.runner, "sstp")["last"])
        with self.assertRaises(ConflictError) as cm:
            ops.inbound_settings_undo(self.w.runner, "sstp")
        self.assertEqual((cm.exception.data["why"], cm.exception.data["now"]), ("moved", {"tls": "1.3"}))
        self.assertIn("сейчас TLS только 1.3", str(cm.exception))
        self.assertEqual(self.rec("sstp")["tls"], {"versions": "1.3"})
        self.assertNotIn("sstp", ops._last_read())

    def test_ocserv_warnings_and_only_12_without_a_confirmation(self):
        self.world()
        plan = ops.inbound_settings(self.w.runner, "ocserv", tls="1.3", dry=True)["plan"]
        self.assertEqual(plan["warnings"], ["Старые приложения OpenConnect могут не подключиться."])
        self.assertTrue(plan["need_yes"])
        res = ops.inbound_settings(self.w.runner, "ocserv", tls="1.2")                    # no warning, no marks
        self.assertEqual(res["changed"], {"tls": {"old": "1.2-1.3", "new": "1.2"}})
        self.assertEqual(self.rec("ocserv")["tls"], {"versions": "1.2"})

    def test_port_and_tls_of_sstp_one_restart_line(self):
        self.world()
        plan = ops.inbound_settings(self.w.runner, "sstp", port=8443, tls="1.3", dry=True)["plan"]
        self.assertEqual(sorted(plan["changed"]), ["port", "tls"])
        self.assertEqual([r["word"] for r in plan["restart"]["rows"]], ["sstp"])
        ops.inbound_settings(self.w.runner, "sstp", port=8443, tls="1.3", yes=True)
        rec = self.rec("sstp")
        self.assertEqual((rec["port"], rec["tls"]), (8443, {"versions": "1.3"}))
        back = ops.inbound_settings_undo(self.w.runner, "sstp")
        self.assertEqual(sorted(back["undone"]), ["port", "tls"])
        rec = self.rec("sstp")
        self.assertEqual((rec["port"], rec.get("tls")), (4433, None))

    def test_a_restart_of_accel_ppp_with_live_sessions_asks(self):
        # решение координатора к REV-3315-a2 п. 7: «только 1.2» никого не отрезает, но перезапуск accel-ppp рвёт живые
        # SSTP/L2TP/PPTP — при N > 0 строка «Оборвутся N подключений» и подтверждение, при 0 — как было
        self.world()
        two = [{"ifname": "kgw-l2tp0", "type": "l2tp"}, {"ifname": "kgw-pptp0", "type": "pptp"}]
        here = tunnel.unit_installed
        engine = mock.patch.object(tunnel, "unit_installed", lambda n: n == accel.UNIT or here(n))  # the dummy SSTP
        engine.start()
        self.addCleanup(engine.stop)
        with mock.patch.object(accel, "sessions", return_value=two) as seen:
            plan = ops.inbound_settings(self.w.runner, "sstp", tls="1.2", dry=True)["plan"]
            self.assertTrue(plan["need_yes"])
            self.assertIn("Оборвутся 2 подключения (SSTP, L2TP, PPTP).", ops.plan_lines(plan))
            with self.assertRaises(ConflictError) as cm:
                ops.inbound_settings(self.w.runner, "sstp", tls="1.2")
            self.assertEqual(cm.exception.data["need"], "yes")
            self.assertIn("Оборвутся 2 подключения", str(cm.exception))
            self.assertNotIn("tls", self.rec("sstp"))                                    # nothing applied
            rc, doc, _ = self.run_json("gw", "inbound", "set", "sstp", "--tls", "1.2")
            self.assertEqual(rc, errors.CONFLICT)
            ops.inbound_settings(self.w.runner, "sstp", tls="1.2", yes=True)
            self.assertEqual(self.rec("sstp")["tls"], {"versions": "1.2"})
            n = seen.call_count
            ops.inbound_settings(self.w.runner, "ocserv", tls="1.3", dry=True)            # not accel-ppp: not asked
            ops.inbound_settings(self.w.runner, "wg", port=51999, dry=True)
            self.assertEqual(seen.call_count, n)
        with state.transaction() as st:                                                  # SSTP without devices
            for d in model.devices(st):
                d["access"] = [k for k in d.get("access") or [] if k != "sstp"]
        with mock.patch.object(accel, "sessions", return_value=two[:1]):
            plan = ops.inbound_settings(self.w.runner, "sstp", port=8443, dry=True)["plan"]
        self.assertEqual((plan["reissue"]["hard"], plan["need_yes"]), ([], True))      # the port: the same restart
        self.assertIn("Оборвётся 1 подключение (SSTP, L2TP, PPTP).", plan["warnings"])
        with mock.patch.object(accel, "sessions", return_value=[]):
            plan = ops.inbound_settings(self.w.runner, "sstp", port=8443, dry=True)["plan"]
            self.assertFalse(plan["need_yes"])
            self.assertFalse(any("Оборв" in ln for ln in ops.plan_lines(plan)))
            res = ops.inbound_settings(self.w.runner, "sstp", tls="1.2-1.3")             # 0 sessions: no confirmation
        self.assertEqual(res["changed"], {"tls": {"old": "1.2", "new": "1.2-1.3"}})
        engine.stop()
        with mock.patch.object(accel, "sessions", return_value=two) as seen:            # no accel-ppp: not asked
            plan = ops.inbound_settings(self.w.runner, "sstp", port=8443, dry=True)["plan"]
        self.assertEqual((plan["need_yes"], seen.call_count), (False, 0))
        for k, ru, en in ((1, "Оборвётся 1 подключение", "1 connection (SSTP"), (2, "Оборвутся 2 подключения", "2 conn"),
                          (5, "Оборвутся 5 подключений", "5 conn"), (21, "Оборвётся 21 подключение", "21 conn")):
            self.assertTrue(i18n.trn("gw_ops.settings.sessions_cut", k, "ru").startswith(ru), k)
            self.assertTrue(i18n.trn("gw_ops.settings.sessions_cut", k, "en").startswith(en), k)

    def test_refusals_before_any_lock(self):
        self.world()
        before = state.load()
        for kind, v in (("wg", "1.3"), ("sstp", "1.0"), ("sstp", True), ("sstp", 13), ("ocserv", "")):
            with self.assertRaises(KaskadError, msg=repr((kind, v))) as e:
                ops.inbound_settings(self.w.runner, kind, tls=v, yes=True)
            self.assertEqual(e.exception.code, errors.USAGE)
        with self.assertRaises(KaskadError) as e:
            ops.inbound_settings(self.w.runner, "sstp", tls="1.1", yes=True)
        self.assertIn("Можно: 1.2 и 1.3, только 1.3, только 1.2", str(e.exception))
        with self.assertRaises(KaskadError) as e:
            ops.inbound_settings(self.w.runner, "wg", tls="1.3")
        self.assertIn("версии TLS здесь не настраиваются", str(e.exception))
        self.assertEqual(state.load(), before)
        res = ops.inbound_settings(self.w.runner, "sstp", tls="1.2-1.3")                  # the same: nothing to do
        self.assertEqual((res["changed"], res["id"]), ({}, None))
        self.assertNotIn("sstp", ops._last_read())

    def test_settings_mutate_is_pure_and_knows_tls(self):
        self.world()
        st = state.load()
        before = state.load()
        self.assertEqual(ops.settings_mutate(st, "sstp", {"tls": "1.3"}), {"tls": "1.2-1.3"})
        self.assertEqual(model.inbound(st, "sstp")["tls"], {"versions": "1.3"})
        self.assertEqual(state.load(), before)
        with self.assertRaises(KaskadError):
            ops.settings_mutate(st, "wg", {"tls": "1.3"})

    def test_soft_read_and_the_reserved_alpn(self):
        for t, want in ((None, "1.2-1.3"), ({"versions": "1.0"}, "1.2-1.3"), ({"alpn": "h2,http/1.1"}, "1.2-1.3"),
                        ("1.3", "1.2-1.3"), ({"versions": ["1.3"]}, "1.2-1.3"), ({"versions": "1.3"}, "1.3")):
            self.assertEqual(tunnel.tls_versions({"tls": t} if t is not None else {}), want, repr(t))
        self.assertEqual(tunnel.tls_versions(None), "1.2-1.3")
        self.world()
        with state.transaction() as st:
            model.inbound(st, "sstp")["tls"] = {"versions": "1.0", "alpn": "h2,http/1.1"}
        self.assertEqual(ops.inbound_settings_view(self.w.runner, "sstp")["tls"]["cur"], "1.2-1.3")
        ops.inbound_settings(self.w.runner, "sstp", tls="1.3", yes=True)
        self.assertEqual(self.rec("sstp")["tls"], {"versions": "1.3", "alpn": "h2,http/1.1"})   # not ours: kept

    def test_view_and_the_cli(self):
        self.world()
        v = ops.inbound_settings_view(self.w.runner, "sstp")
        self.assertEqual(v["tls"]["cur"], "1.2-1.3")
        self.assertEqual([c["value"] for c in v["tls"]["choices"]], ["1.2-1.3", "1.3", "1.2"])
        self.assertEqual(v["tls"]["choices"][2]["hint"], "если хостер или сеть ломают TLS 1.3")
        self.assertIsNone(ops.inbound_settings_view(self.w.runner, "wg")["tls"])
        out, _ = self.cli("gw", "inbound", "show", "sstp")
        self.assertIn("  TLS: 1.2 и 1.3 (можно: только 1.3, только 1.2 — если хостер или сеть ломают TLS 1.3)", out)
        out, _ = self.cli("gw", "inbound", "set", "sstp", "--tls", "1.3", "--dry")
        self.assertIn("TLS 1.2 и 1.3 → только 1.3", out)
        self.assertIn("Windows 10 и MikroTik", out)
        rc, _, _ = self.run_json("gw", "inbound", "set", "sstp", "--tls", "1.3")
        self.assertEqual(rc, errors.CONFLICT)                                              # the warning: --yes
        self.cli("gw", "inbound", "set", "sstp", "--tls", "1.3", "--yes")
        self.assertEqual(self.rec("sstp")["tls"], {"versions": "1.3"})
        out, _ = self.cli("gw", "inbound", "show", "sstp")
        self.assertIn("TLS 1.2 и 1.3 → только 1.3", out)                                   # «Вернуть прежние»

    def test_old_versions_read_a_state_with_tls(self):
        self.world()
        ops.inbound_settings(self.w.runner, "sstp", tls="1.3", yes=True)
        ops.inbound_settings(self.w.runner, "ocserv", tls="1.2", yes=True)
        for ver, rev in sorted(r15r.OLD.items()):
            env = dict(os.environ, PYTHONPATH=r15r.old_lib(rev), KASKAD_ROOT=os.environ["KASKAD_ROOT"])
            p = subprocess.run([sys.executable, "-c", OLD_SIDE], env=env, capture_output=True, text=True, timeout=60,
                               cwd=tempfile.gettempdir())
            self.assertEqual(p.returncode, 0, "%s: %s" % (ver, p.stderr[-800:]))
            got = json.loads(p.stdout.strip().splitlines()[-1])
            self.assertEqual(got, {"problems": [], "ssl": ["ssl-protocol=tls1.2,tls1.3"], "pri": [DEFAULT_PRI]}, ver)


class TlsApiTest(TlsWorld, PanelCase, CliGwCase):
    def test_put_tls_with_ver_and_strict_body(self):
        self.world()
        v = self.ok("GET", "/api/gw/inbounds/sstp/settings")
        self.assertEqual(v["tls"]["cur"], "1.2-1.3")
        for bad in (13, True, "1.0", ["1.3"]):
            self.err(400, "PUT", "/api/gw/inbounds/sstp/settings", {"tls": bad, "yes": True, "ver": v["ver"]})
        self.err(400, "PUT", "/api/gw/inbounds/wg/settings", {"tls": "1.3", "dry": True})
        need = self.err(409, "PUT", "/api/gw/inbounds/sstp/settings", {"tls": "1.3", "ver": v["ver"]})
        self.assertEqual(need["data"]["need"], "yes")
        done = self.ok("PUT", "/api/gw/inbounds/sstp/settings", {"tls": "1.3", "yes": True, "ver": v["ver"]})
        self.assertEqual(done["changed"], {"tls": {"old": "1.2-1.3", "new": "1.3"}})
        self.assertNotEqual(self.ok("GET", "/api/gw/inbounds/sstp/settings")["ver"], v["ver"])


class SstpTlsTest(ApplyCase):
    """accel-ppp: ``ssl-protocol`` from the field; a change — a restart, not a reload."""

    def conf(self):
        return self.read("/etc/kaskad/gw/accel/accel-ppp.conf")

    def test_versions_restart_accel_ppp(self):
        self.ready()
        self.assertIn("\nssl-protocol=tls1.2,tls1.3\n", self.conf())
        for v, line in (("1.3", "tls1.3"), ("1.2", "tls1.2")):
            self.st["gateway"]["inbounds"]["sstp"]["tls"] = {"versions": v}
            self.node.sysd.calls[:] = []
            self.assertEqual(self.sstp.apply(self.st, self.sec, self.runner)["action"], "restarted", v)
            self.assertIn("\nssl-protocol=%s\n" % line, self.conf())
            self.assertIn("restart", self.verbs())
            self.assertNotIn("reload", self.verbs())

    def test_unknown_values_keep_the_line(self):
        self.ready()
        for t in ({"versions": "1.0"}, {"alpn": "h2,http/1.1"}, "1.3"):
            self.st["gateway"]["inbounds"]["sstp"]["tls"] = t
            self.assertEqual(self.sstp.apply(self.st, self.sec, self.runner)["action"], "unchanged", repr(t))
            self.assertIn("\nssl-protocol=tls1.2,tls1.3\n", self.conf())


    def test_live_sessions_are_counted_by_accel_cmd(self):
        # п. 7: the count — «accel-cmd show sessions» of the real adapter; no answer (rc != 0) — 0, as before
        self.ready()
        self.node.sessions = (session_row("kgw-l2tp0", "noutbuk-1a2b", "10.77.5.11", "l2tp") +
                              session_row("kgw-pptp0", "tplink-5e6f-pptp", "10.77.5.10", "pptp"))

        def cut():
            plan = {"restart": {"rows": [{"what": "tls", "word": "sstp"}]}, "warnings": [], "need_yes": False}
            ops._accel_cut(self.runner, plan)
            return plan["need_yes"], plan["warnings"]
        self.assertEqual(cut(), (True, ["Оборвутся 2 подключения (SSTP, L2TP, PPTP)."]))
        self.node.cli_password = "другой"                                               # accel-cmd: connection closed
        self.assertEqual(cut(), (False, []))


class OcservTlsTest(OcCase):
    def line(self):
        conf = self.read("/etc/kaskad/gw/ocserv/ocserv.conf")
        return [x for x in conf.splitlines() if x.startswith("tls-priorities")]

    def test_three_values_three_lines_a_change_restarts(self):
        self.ready()
        self.assertEqual(self.line(), [DEFAULT_PRI])
        for v, cut in (("1.3", ":-VERS-TLS1.2"), ("1.2", ":-VERS-TLS1.3"), ("1.2-1.3", "")):
            self.st["gateway"]["inbounds"]["ocserv"]["tls"] = {"versions": v}
            self.node.sysd.calls[:] = []
            self.assertEqual(self.oc.apply(self.st, self.sec, self.runner)["action"], "restarted", v)
            self.assertEqual(self.line(), [DEFAULT_PRI[:-1] + cut + '"'])
        self.assertEqual(ocin.tls_priorities("1.0"), DEFAULT_PRI.split('"')[1])

    def test_unknown_values_keep_the_line(self):
        self.ready()
        for t in ({"versions": "1.0"}, {"alpn": "h2,http/1.1"}):
            self.st["gateway"]["inbounds"]["ocserv"]["tls"] = t
            self.assertEqual(self.oc.apply(self.st, self.sec, self.runner)["action"], "unchanged", repr(t))
            self.assertEqual(self.line(), [DEFAULT_PRI])


class VlessTlsTest(FrontCase):
    def tls_mode(self):
        from kaskad.site import ops as sops
        got = sops.mode(self.w.runner, "site", yes=True)
        self.assertEqual(got["vless"]["security"], "tls")

    def test_only_13_sets_min_version_and_restarts_xray(self):
        self.tls_mode()
        self.assertEqual(self.vless_ib()["streamSettings"]["tlsSettings"]["minVersion"], "1.2")
        v = ops.inbound_settings_view(self.w.runner, "vless")["tls"]
        self.assertEqual([c["value"] for c in v["choices"]], ["1.2-1.3", "1.3"])          # no «только 1.2»
        self.assertIn("закрывает TLS 1.2 для сайта за VLESS", v["note"])
        plan = ops.inbound_settings(self.w.runner, "vless", tls="1.3", dry=True)["plan"]
        self.assertTrue(plan["via_xray"] and plan["need_yes"])                            # З-6
        self.assertEqual([r["word"] for r in plan["restart"]["rows"]], ["xray"])
        u0, q0, _ = self.link(self.dev["id"])
        n = self.xray_restarts()
        ops.inbound_settings(self.w.runner, "vless", tls="1.3", yes=True)
        self.assertEqual(self.vless_ib()["streamSettings"]["tlsSettings"]["minVersion"], "1.3")
        self.assertEqual(self.xray_restarts(), n + 1)
        u1, q1, _ = self.link(self.dev["id"])
        self.assertEqual((u1.geturl(), q1), (u0.geturl(), q0))                             # the link the same
        self.assertNotIn("vless", reissue.load())
        back = ops.inbound_settings_undo(self.w.runner, "vless", yes=True)     # П6: xray шлюза — с подтверждением
        self.assertEqual(self.vless_ib()["streamSettings"]["tlsSettings"]["minVersion"], "1.2")
        self.assertEqual((back["reissue"], back["text"]),
                         ([], "VLESS: прежние настройки возвращены — TLS только 1.3 → 1.2 и 1.3."))  # З-1

    def test_only_12_refused_and_unknown_is_as_before(self):
        self.tls_mode()
        with self.assertRaises(KaskadError) as e:
            ops.inbound_settings(self.w.runner, "vless", tls="1.2", yes=True)
        self.assertEqual(e.exception.code, errors.USAGE)
        self.assertIn("Можно: 1.2 и 1.3, только 1.3.", str(e.exception))
        with state.transaction() as st:
            model.inbound(st, "vless")["tls"] = {"versions": "1.2"}            # by hand: «only 1.2» is not VLESS's
        self.ok("gw", "apply")
        self.assertEqual(self.vless_ib()["streamSettings"]["tlsSettings"]["minVersion"], "1.2")
        # REV-3315-a2 П-1: read as xray reads it — «1.2 и 1.3»; asking for it changes nothing
        self.assertEqual(ops.inbound_settings_view(self.w.runner, "vless")["tls"]["cur"], "1.2-1.3")
        self.assertEqual(ops.inbound_settings(self.w.runner, "vless", tls="1.2-1.3", dry=True)["plan"]["changed"], {})

    def test_reality_has_no_tls_block(self):
        self.assertEqual(self.rec()["security"], "reality")
        self.assertIsNone(ops.inbound_settings_view(self.w.runner, "vless")["tls"])
        with self.assertRaises(KaskadError) as e:
            ops.inbound_settings(self.w.runner, "vless", tls="1.3", yes=True)
        self.assertEqual(e.exception.code, errors.USAGE)
        self.assertIn("версии TLS здесь не настраиваются", str(e.exception))
