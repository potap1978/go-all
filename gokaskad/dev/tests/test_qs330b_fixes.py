"""Kaskad 3.3.0, the live check №2 (k3/stand/logs/T-M6-qs330b.md §1), the blocker №1 and what let it through:

    №1   xray of the gateway refused its own config on a clean node (``::ffff:0:0/96``) — 443 never listened, the
         wizard said «VLESS установлен», ``gw status`` «работает». Now: the rule is one xray takes (the real xray checks
         it — tests/test_real_validators.py); an install of VLESS (SOCKS5) that xray refuses is taken back and fails in
         words; VLESS «работает» only by the fact — the unit active and its port listening; ``gw apply`` never says
         «Работает прежний конфиг» when no xray runs.
    №14  (xray's half) no ``ocspStapling`` — the OCSP warning at every start of xray is gone (test_gw_vless_front).
"""

import os
from unittest import mock

from kaskad import state
from kaskad.gw import model, xray
from kaskad.setup import steps
from tests.setup.test_setup_core import new_link, open_panel
from tests.test_gw_vless import VlessCase

REFUSED = ("Failed to start: main: failed to load config files: [xray.new.json] > infra/conf: failed to build routing "
           "configuration > infra/conf: invalid field rule > infra/conf: invalid IP: ::ffff:0:0/96 > infra/conf: "
           "invalid network mask for router: 96\n")


class XrayRefusesTheConfigTest(VlessCase):
    def setUp(self):
        super().setUp()
        self.enabled_world()

    def test_the_install_is_taken_back_and_fails_in_words(self):
        self.w.xray.fail = REFUSED
        rc, doc, err = self.run_json("gw", "inbound", "install", "vless")
        self.assertNotEqual(rc, 0)
        text = doc.get("error") or err
        self.assertIn("VLESS не установлен", text)
        self.assertIn("xray не принял конфиг шлюза", text)
        self.assertIn("Узел оставлен как был", text)
        st = state.load()
        self.assertIsNone(model.inbound(st, "vless"))                  # no VLESS «installed» that listens nowhere
        self.assertIsNone(model.kept_inbound(st, "vless"))
        ins = [i.get("tag") for i in (self.cfg().get("inbounds") or [])]     # xray of the exits runs on, without it
        self.assertNotIn("gw-vless", ins)
        # xray takes it again (an update of Kaskad): the same command installs it, the port listens
        self.w.xray.fail = None
        self.ok("gw", "inbound", "install", "vless")
        row = [r for r in self.run_json("gw", "status")[1]["inbounds"] if r["kind"] == "vless"][0]
        self.assertTrue(row["installed"] and row["running"], row)

    def test_the_wizard_says_it_did_not_install(self):
        open_panel()
        new_link()
        with mock.patch.object(steps, "online", lambda force=False: True):
            steps.run("password", {"password": "Correct-Horse-9"}, self.w.runner)
            self.w.xray.fail = REFUSED
            out = steps.run("protocols", {"kinds": ["vless"]}, self.w.runner)
        self.assertEqual(out["state"], "error", out)
        self.assertFalse(out["ok"])
        self.assertEqual(out["data"]["installed"], [])
        self.assertIn("xray не принял конфиг шлюза", out["data"]["failed"][0]["text"])

    def test_gw_apply_without_a_running_xray_says_so(self):
        self.ok("gw", "inbound", "install", "vless")
        self.w.sysd.units[xray.UNIT]["active"] = False
        self.w.xray.fail = REFUSED
        os.unlink(self.path("/etc/kaskad/gw/xray.json"))               # the config to write again: -test runs
        rc, doc, err = self.run_json("gw", "apply")
        warn = " ".join(doc.get("warnings") or [])
        self.assertIn("xray шлюза не запущен", warn)
        self.assertNotIn("Работает прежний конфиг", warn)


class VlessStatusByTheFactTest(VlessCase):
    def setUp(self):
        super().setUp()
        self.enabled_world()
        self.ok("gw", "inbound", "install", "vless")

    def row(self):
        return [r for r in self.run_json("gw", "status")[1]["inbounds"] if r["kind"] == "vless"][0]

    def test_running_only_while_xray_runs_and_the_port_listens(self):
        r = self.row()
        self.assertTrue(r["running"], r)                                # no device yet: idle, but it listens
        self.assertIn("устройств с протоколом VLESS пока нет", r["warnings"])
        self.w.sysd.units[xray.UNIT]["active"] = False                  # T-M6: the unit never came up
        r = self.row()
        self.assertFalse(r["running"])
        self.assertTrue(any("xray шлюза не работает" in w for w in r["warnings"]), r["warnings"])
        self.w.sysd.units[xray.UNIT]["active"] = True                   # active, the inbound not in what it runs
        with mock.patch.object(self.w, "ss_with_xray", lambda argv, inp: (0, "", "")):
            self.w.runner._handlers.insert(0, (["ss"], lambda argv, inp: (0, "", "")))
            r = self.row()
        self.assertFalse(r["running"])
        self.assertTrue(any("порт %d/tcp не слушается" % self.rec()["port"] in w for w in r["warnings"]), r["warnings"])
