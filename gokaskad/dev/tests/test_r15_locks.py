"""3.3.7.15 F11 — the locks of «Приём подключений» (SPEC-R1-3315 §3 F11, PLAN-multi-ip-roles §2.3, §9.7 «Защита
доступа»): what closing an address breaks that Kaskad cannot repair is asked before it is written — 409 ``need:
"confirm"`` with ``consequences`` (the panel asks and repeats with ``yes``; the CLI — with ``--yes``; the menu — a
question by digits, «Отмена» by default); what it can never let through — a refusal with the way back.

* IPv4: the domain of the panel (the open mode by name, behind nginx) with an A record on the address closed — a
  confirmation (was a warning before); its A records not known — the same question; not on a closed address — nothing;
* IPv6: the panel opened over the IPv6 closed — a confirmation «the browser moves to IPv4» when the domain has an A
  record on an IPv4 that takes connections, else a refusal; SSH over an IPv6 closed — a warning; an AAAA record of a
  name the devices get on a closed IPv6 — a confirmation;
* the menu 17 → 4 → «Приём подключений»: the consequences, the question by digits, the repeat with ``--yes``."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from kaskad import errors, state
from kaskad.gw import mip
from tests.test_3351_cmdinj import function
from tests import test_mip_s34 as s34
from tests.test_mip_s34 import A_IP, B_IP, Node
from tests.test_r15_addr6 import doc6, with_ipv6

DOMAIN = "vpn.example.com"
X6, Y6 = "2001:db8:10::5", "2001:db8:10::7"


def site(a=None, aaaa=None, domain=DOMAIN):
    with state.locked():
        st = state.load()
        st["site"] = {"mode": "site", "domain": domain, "dns": dict({"a": a} if a is not None else {},
                                                                    **({"aaaa": aaaa} if aaaa is not None else {}))}
        state.save(st)


class Locks(Node):
    def setUp(self):
        super().setUp()
        doc6(self)
        with_ipv6(self)
        self.panel = (DOMAIN, "")
        p = mock.patch.object(mip, "_panel_host", lambda: self.panel)
        p.start()
        self.addCleanup(p.stop)

    def asked(self, rc, doc, *words):
        self.assertEqual((rc, doc["data"]["reason"], doc["data"]["need"]), (errors.CONFLICT, "confirm", "confirm"), doc)
        for w in words:
            self.assertIn(w, " ".join(doc["data"]["consequences"]))
        self.assertIn("--yes", doc["error"])
        self.assertNotIn("addresses", state.load()["gateway"])                 # nothing written

    def api(self, fn, body, local=None, listener="public"):
        from kaskad.panel import api

        class Req:
            query = {}

            def json(self):
                return dict(body, ver=mip.addresses_ver(state.load()))
        Req.listener, Req.local_ip = listener, local
        with mock.patch.object(api, "runner", lambda: self.w.runner), mock.patch.object(api, "_changed", lambda: None):
            return getattr(api, fn)(Req())


class DomainTest(Locks):
    def test_a_record_on_the_closed_address_asks(self):
        """The panel by its domain, A = [A, B], only A takes connections: 409 confirm without ``yes`` (before F11 — a
        warning and the write), with ``--yes`` — written, the consequence among the warnings."""
        site(a=[A_IP, B_IP])
        rc, doc, _ = self.run_in(A_IP)
        self.asked(rc, doc, DOMAIN, B_IP)
        rc, doc, _ = self.run_json("gw", "addresses", "in", A_IP, "--yes")
        self.assertEqual(rc, 0, doc)
        self.assertEqual(state.load()["gateway"]["addresses"]["in"], {"*": [A_IP]})
        self.assertTrue(any(DOMAIN in w and B_IP in w for w in doc["warnings"]), doc)

    def test_unknown_records_ask_too_others_do_not(self):
        site(a=[A_IP], domain="other.example.com")                          # the records of another domain: unknown
        rc, doc, _ = self.run_in(A_IP)
        self.asked(rc, doc, DOMAIN, "неизвестны")
        site(a=[A_IP])                                                      # only on the address that stays
        rc, doc, _ = self.run_in(A_IP)
        self.assertEqual(rc, 0, doc)
        self.ok("gw", "addresses", "in", "--all")                           # the way back is never asked
        site(a=["198.51.100.20"])                                           # a CDN: not an address of ours
        rc, doc, _ = self.run_in(A_IP)
        self.assertEqual(rc, 0, doc)

    def test_the_domain_behind_nginx(self):
        self.panel = ("", "panel.example.com")
        rc, doc, _ = self.run_in(A_IP)
        self.asked(rc, doc, "panel.example.com")

    def test_the_panel_asks_and_takes_yes(self):
        from kaskad.panel import routes
        site(a=[A_IP, B_IP])
        with self.assertRaises(routes.HttpError) as cm:
            self.api("gw_addresses_in", {"ips": [A_IP]}, local=A_IP)
        e = cm.exception
        self.assertEqual((e.status, e.data["need"]), (409, "confirm"))
        self.assertIn(B_IP, e.data["consequences"][0])
        self.assertIn("Сохранить всё равно?", e.text)                        # the panel's question, not the CLI's
        self.assertNotIn("addresses", state.load()["gateway"])
        resp = self.api("gw_addresses_in", {"ips": [A_IP], "yes": True}, local=A_IP)
        self.assertEqual(json.loads(resp.body)["in"], [A_IP])


class Ip6Test(Locks):
    def setUp(self):
        super().setUp()
        site(aaaa=[])                                                       # the AAAA records of the panel: none, known

    def in6(self, *args, env=None):
        with mock.patch.dict(os.environ, env or {}, clear=False):
            return self.run_json("gw", "addresses", "in6", *args)

    def test_the_panel_over_a_closed_ipv6(self):
        from kaskad.panel import routes
        with self.assertRaises(routes.HttpError) as cm:                      # no A record: the panel would be lost
            self.api("gw_addresses_in6", {"ips": [Y6]}, local=X6)
        self.assertEqual((cm.exception.status, cm.exception.data["reason"]), (409, "via_out"))
        self.assertIn(X6, cm.exception.text)
        self.assertNotIn("addresses", state.load()["gateway"])
        site(a=[A_IP], aaaa=[])                                             # an A record on an IPv4 that takes them
        with self.assertRaises(routes.HttpError) as cm:
            self.api("gw_addresses_in6", {"ips": [Y6]}, local=X6)
        self.assertEqual((cm.exception.status, cm.exception.data["need"]), (409, "confirm"))
        self.assertIn("браузер перейдёт на IPv4 (%s → %s)" % (DOMAIN, A_IP), cm.exception.data["consequences"][0])
        resp = self.api("gw_addresses_in6", {"ips": [Y6], "yes": True}, local=X6)
        self.assertEqual(json.loads(resp.body)["in6"], [Y6])
        self.api("gw_addresses_in6", {"ips": [X6]}, local=X6)               # over the chosen one: nothing asked
        self.api("gw_addresses_in6", {"none": True}, local=A_IP)            # over IPv4: the IPv6 list is not its way
        self.ok("gw", "addresses", "in", "--all")
        with mock.patch.object(mip, "in_list", lambda st: [B_IP]):          # the A record on an IPv4 already closed
            with self.assertRaises(routes.HttpError) as cm:
                self.api("gw_addresses_in6", {"ips": [Y6]}, local=X6)
        self.assertEqual(cm.exception.data["reason"], "via_out")

    def test_ssh_over_a_closed_ipv6_is_a_warning(self):
        rc, doc, _ = self.in6(Y6, env={"SSH_CONNECTION": "2001:db8:99::1 50000 %s 22" % X6})
        self.assertEqual(rc, 0, doc)
        self.assertTrue(any("Вы подключены по SSH через IPv6 %s" % X6 in w for w in doc["warnings"]), doc)

    def test_an_aaaa_record_on_a_closed_ipv6_asks(self):
        site(aaaa=["2001:DB8:10:0::7"])                                     # canonical in the comparison
        rc, doc, _ = self.in6(X6)
        self.assertEqual((rc, doc["data"]["need"]), (errors.CONFLICT, "confirm"), doc)
        self.assertIn("AAAA-запись на %s" % Y6, doc["data"]["consequences"][0])
        self.assertIn("kaskadctl gw addresses in6 %s --yes" % X6, doc["error"])
        self.assertNotIn("addresses", state.load()["gateway"])
        rc, doc, _ = self.in6(X6, "--yes")
        self.assertEqual((rc, state.load()["gateway"]["addresses"]["in6"]), (0, {"*": [X6]}), doc)
        rc, doc, _ = self.in6(Y6)                                           # the record on the one that stays
        self.assertEqual(rc, 0, doc)
        site(aaaa=["2001:db8:77::1"])                                       # not an address of ours: nothing
        rc, doc, _ = self.in6("--none")
        self.assertEqual(rc, 0, doc)

    def test_unknown_aaaa_of_the_panel_domain_asks(self):
        """REV-3315-b1 З-2 (PLAN-multi-ip-roles §9.7 п. 3): the panel behind nginx on a domain whose AAAA records Kaskad
        does not know (the site is on another one) — closing an IPv6 asks, as the A records not known do; with
        ``--yes`` — written; nothing closed (``--all``) — never asked."""
        self.panel = ("", "panel.example.com")
        site(aaaa=[Y6], domain="other.example.com")
        rc, doc, _ = self.in6(X6)
        self.assertEqual((rc, doc["data"]["need"]), (errors.CONFLICT, "confirm"), doc)
        self.assertIn("AAAA-записи домена панели panel.example.com Kaskad неизвестны", doc["data"]["consequences"][0])
        self.assertIn(Y6, doc["data"]["consequences"][0])
        self.assertNotIn("addresses", state.load()["gateway"])
        rc, doc, _ = self.in6(X6, "--yes")
        self.assertEqual((rc, state.load()["gateway"]["addresses"]["in6"]), (0, {"*": [X6]}), doc)
        rc, doc, _ = self.in6("--all")
        self.assertEqual(rc, 0, doc)
        site(aaaa=[], domain="panel.example.com")                           # known: none — nothing to ask
        rc, doc, _ = self.in6(X6)
        self.assertEqual(rc, 0, doc)


class LockMenuTest(unittest.TestCase):
    """The menu 17 → 4 → «Приём подключений»: the core asks (rc 3, ``need: "confirm"``) — its consequences on the
    screen, the question by digits (2 — «Отмена» by default), 1 — the same call again with ``--yes``."""
    STUBS, ADDRS, MIP = s34.MenuTest.STUBS, s34.MenuTest.ADDRS, s34.MenuTest.MIP
    CONFIRM = json.dumps({"ok": False, "error": "ASK", "data": {"reason": "confirm", "need": "confirm",
                                                                 "consequences": ["LOCK-ONE", "LOCK-TWO"]}})

    def run_lock(self, keys):
        d = tempfile.mkdtemp(prefix="k3-r15-lock-menu-")
        self.addCleanup(shutil.rmtree, d, True)
        names = ("k3_jq", "k3_term_esc", "k3_gw_name_arg", "k3_gw_in_menu", "k3_choose")
        stub = self.STUBS.replace('''        "addresses in")
            if [ "$3" = "$REFUSE" ]; then K3_GW_RC=3; K3_GW_ERR="REFUSED"; K3_GW_OUT='{"ok":false}'
            else K3_GW_OUT='{"ok":true,"text":"DONE-IN"}'; fi ;;''', '''        "addresses in")
            if [ "${4:-}" = --yes ]; then K3_GW_OUT='{"ok":true,"text":"DONE-IN"}'
            else K3_GW_RC=3; K3_GW_ERR="ASK"; K3_GW_OUT=$CONFIRM; fi ;;''')
        self.assertNotEqual(stub, self.STUBS)
        src = "".join(function(n) for n in names) + stub
        src += "k3_gw_in_menu <<'EOF'\n%sEOF\n" % "".join(k + "\n" for k in keys)
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LC_ALL": "C.UTF-8", "CONFIRM": self.CONFIRM,
               "ADDRS": json.dumps(dict(self.ADDRS, mip=self.MIP))}
        r = subprocess.run(["bash", "-c", src], capture_output=True, text=True, timeout=60, env=env)
        return r.stdout + r.stderr

    def test_the_consequences_and_the_question(self):
        out = self.run_lock(["2", "1", "0"])
        self.assertIn("! LOCK-ONE\n", out)
        self.assertIn("! LOCK-TWO\n", out)
        self.assertIn("GWS_IN_CONFIRM\n", out)
        self.assertIn(" 1) GWS_IN_CONFIRM_SAVE\n", out)
        self.assertIn(" 2) C_CANCEL\n", out)
        calls = [ln for ln in out.splitlines() if ln.startswith("CALL:addresses in")]
        self.assertEqual(calls, ["CALL:addresses in %s" % A_IP, "CALL:addresses in %s --yes" % A_IP])
        self.assertIn("DONE-IN", out)
        self.assertNotIn("[ERROR] ASK", out)

    def test_cancel_by_default(self):
        for keys in (["2", "", "0"], ["2", "2", "0"]):                      # Enter — «Отмена»; 2 — «Отмена»
            out = self.run_lock(keys)
            calls = [ln for ln in out.splitlines() if ln.startswith("CALL:addresses in")]
            self.assertEqual(calls, ["CALL:addresses in %s" % A_IP], keys)
            self.assertNotIn("DONE-IN", out)
