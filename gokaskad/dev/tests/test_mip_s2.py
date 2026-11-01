"""«Адрес для клиентов» среди «Адресов сервера» — PLAN-multi-ip Ш2 (k3/handoff/briefs/PLAN-multi-ip-2026-10-29.md §4).

Each case fails without its fix (the table of §4, Ш2):
* Л1 — a copy whose address for clients is the SECOND address of this very server keeps it (the restore used to put the
  route's address instead, with the node certificate still issued on the second one); a copy of another server gets
  this machine's address and never brings its manual addresses;
* Л3 — ``gw host`` (CLI, the panel, MCP) takes an IPv4 only of «Адреса сервера»; another one — 409/«address_not_here»
  with the ways on, ``--force`` takes it;
* Л7 — the DNS check of the site counts the external addresses of a NAT cloud as the server's: a domain with A records
  on both is «ok», not «partial»;
* Л6, Л7, REV З4 — the open mode of the panel: a ``bind`` gone from the machine gives its place to the route's address,
  an address of a second interface only when added by hand; the external NAT pair of a listened address is a name;
* the SSH menu 17 → 4: the addresses as numbered items, «другой адрес» refused → a numbered question → added and set.
"""

import json
import os
import shutil
import subprocess
import unittest
from unittest import mock

from kaskad import backup, errors, secrets, state, sysrun
from kaskad.gw import addresses as A
from kaskad.gw import model
from kaskad.panel import conf
from kaskad.site import dns
from tests.fakes_gw import FakeIp
from tests.gw.test_mip_addresses import Runner
from tests.panel.panelcase import PanelCase
from tests.test_3351_cmdinj import function
from tests.test_backup import PW, BackupCase
from tests.test_gw_ops import CliGwCase

A_IP, B_IP, C_IP = "85.143.0.20", "85.143.0.10", "85.143.0.30"


def two_addresses(case, primary=A_IP, second=B_IP):
    """The fake machine: ``primary`` (the source of the default route) and ``second`` on eth0."""
    case.w.ip = FakeIp(node_ip=primary, extra_addrs=["%s/32" % second] if second else [])
    case.w.runner._handlers = [h for h in case.w.runner._handlers if h[0] != ["ip"]]
    case.w.runner.on(["ip"], case.w.ip)


class HostTest(CliGwCase):
    def setUp(self):
        super().setUp()
        from tests.fakes_nft import save_state
        save_state()
        two_addresses(self)

    def test_only_an_address_of_this_server(self):
        self.ok("gw", "host", B_IP)                                               # the second address: no --force
        self.assertEqual(model.public_host(state.load()), B_IP)
        rc, doc, err = self.run_json("gw", "host", "85.143.0.99")
        self.assertEqual((rc, doc["data"]["reason"], doc["data"]["known"]), (errors.CONFLICT, "address_not_here",
                                                                             [A_IP, B_IP]))
        self.assertIn("Адреса 85.143.0.99 у этого сервера нет. Есть: %s, %s." % (A_IP, B_IP), doc["error"])
        self.assertIn("kaskadctl gw addresses add 85.143.0.99", doc["error"])
        self.assertTrue(doc["error"].endswith(". Задать без проверки — ключ --force."))
        self.assertNotIn("--force", subprocess.run(                                # the menu cuts it off (k3_no_force_hint)
            ["bash", "-c", function("k3_no_force_hint") + 'k3_no_force_hint "$E"'], capture_output=True, text=True,
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "E": doc["error"]}).stdout)
        self.assertEqual(model.public_host(state.load()), B_IP)                   # nothing changed
        self.ok("gw", "host", "85.143.0.99", "--force")
        self.assertEqual(model.public_host(state.load()), "85.143.0.99")
        self.ok("gw", "host", "85.143.0.99")                                      # the one it is already: no check
        self.ok("gw", "addresses", "add", "93.184.216.34")
        self.ok("gw", "host", "93.184.216.34")                                    # added by hand — the server's
        self.ok("gw", "host", "vpn.example.com")                                  # a name: DNS, not checked
        self.assertEqual(model.public_host(state.load()), "vpn.example.com")

    def test_a_nat_pair_found_on_the_second_look(self):
        """A cloud whose pair was never looked at: the check looks once more with the probes before refusing."""
        self.w.ip = FakeIp(node_ip="10.0.0.5", extra_addrs=["10.0.0.6/24"])
        self.w.runner._handlers = [h for h in self.w.runner._handlers if h[0] != ["ip"]]
        self.w.runner.on(["ip"], self.w.ip)
        pairs = {"10.0.0.5": A_IP, "10.0.0.6": B_IP}
        with mock.patch.object(A, "_echo", lambda runner, local: pairs.get(local)):
            A.collect(self.w.runner, net=False)                                   # a cache without the pairs
            self.ok("gw", "host", B_IP)
        self.assertEqual(model.public_host(state.load()), B_IP)

    def test_enable_with_a_host(self):
        rc, doc, _ = self.run_json("gw", "enable", "--host", "85.143.0.99")
        self.assertEqual((rc, doc["data"]["reason"]), (errors.CONFLICT, "address_not_here"))
        self.assertFalse(model.enabled(state.load()))


class PanelHostTest(PanelCase):
    def test_another_ip_is_409_with_the_ways_on(self):
        self.gateway()
        two_addresses(self)
        self.assertEqual(self.ok("PUT", "/api/gw/host", {"host": B_IP})["host"], B_IP)
        doc = self.err(409, "PUT", "/api/gw/host", {"host": "85.143.0.99"})
        self.assertIn("Адреса 85.143.0.99 у этого сервера нет", doc["error"])
        self.assertIn("SSH-меню: 17 → 4", doc["error"])                       # REV З-5: the way the panel has
        self.assertNotIn("--force", doc["error"])
        self.assertNotIn("kaskadctl", doc["error"])
        self.assertEqual(model.public_host(state.load()), B_IP)
        self.err(409, "POST", "/api/gw/enable", {"host": "85.143.0.99"})


class BackupAddressTest(BackupCase):
    def setUp(self):
        super().setUp()
        two_addresses(self)
        self.here_ip = A_IP                       # the route's address (detect_host): not the one clients use

    def node_on(self, host, manual=()):
        self.old_node()                           # its host is the old server's (--force) — this server's now:
        self.ok("gw", "host", host)
        for ip in manual:
            self.ok("gw", "addresses", "add", ip)
        self.before = {"gw": json.loads(json.dumps(state.load()["gateway"])), "sec": dict(secrets.load())}

    def test_the_second_address_of_this_server_stays(self):
        """Л1: «Вернуть как было» / a copy of this very server — B stays, and the node certificate of the copy with it."""
        self.node_on(B_IP, manual=["93.184.216.35"])
        doc = backup.unseal(self.w.runner, backup.export(self.w.runner, PW)["text"], PW)
        self.new_node()
        pv = backup.preview(self.w.runner, doc)
        self.assertEqual((pv["address"]["changed"], pv["address"]["same"]), (False, True))
        self.assertFalse(any("выдайте им настройки заново" in w for w in pv["warnings"]))
        backup.apply(self.w.runner, doc)
        g = state.load()["gateway"]
        self.assertEqual((g["host"], g["host_auto"]), (B_IP, False))
        self.assertEqual(g["addresses"], {"manual": [{"ip": "93.184.216.35"}]})       # its own list came back
        sec = secrets.load()
        for k in backup.NODE_SECRETS:
            self.assertEqual(sec.get(k), self.before["sec"].get(k), k)

    def test_an_automatic_address_of_this_server_stays_after_the_hoster_moved_the_route(self):
        """REV С-1 (T-mip-lab Н6): A was determined by itself, then the hoster made B primary — a copy of this very
        server keeps A, automatic as it was, and says nothing of a move (решение 4: never moved by itself)."""
        self.old_node()
        st = state.load()
        st["gateway"]["host"], st["gateway"]["host_auto"] = A_IP, True
        state.save(st)
        self.ok("gw", "apply")
        self.assertTrue(model.enabled(state.load()))
        two_addresses(self, B_IP, A_IP)           # the route goes from B now, A is the second address
        self.here_ip = B_IP
        doc = backup.unseal(self.w.runner, backup.export(self.w.runner, PW)["text"], PW)
        self.new_node()
        pv = backup.preview(self.w.runner, doc)
        self.assertEqual((pv["address"]["changed"], pv["address"]["same"]), (False, True))
        backup.apply(self.w.runner, doc)
        g = state.load()["gateway"]
        self.assertEqual((g["host"], g["host_auto"], g["enabled"]), (A_IP, True, True))

    def test_another_server_gets_its_own_address_and_keeps_its_own_list(self):
        self.node_on(B_IP, manual=["93.184.216.35"])
        doc = backup.unseal(self.w.runner, backup.export(self.w.runner, PW)["text"], PW)
        self.new_node()
        two_addresses(self, C_IP, None)           # another machine: its address, its cache
        if os.path.exists(A.cache_path()):       # (gw purge of new_node takes /var/lib/kaskad/gw away)
            os.unlink(A.cache_path())
        self.ok("gw", "addresses", "add", "93.184.216.36")
        self.here_ip = C_IP
        pv = backup.preview(self.w.runner, doc)
        self.assertEqual((pv["address"]["changed"], pv["address"]["same"], pv["address"]["here"]), (True, False, C_IP))
        with mock.patch("kaskad.gw.net.detect_host", lambda runner: (C_IP, "route")):
            backup.apply(self.w.runner, doc)
        g = state.load()["gateway"]
        self.assertEqual(g["host"], C_IP)
        self.assertEqual(g["addresses"], {"manual": [{"ip": "93.184.216.36"}]})       # not the copy's 93.184.216.35


class DnsNatTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.root = tempfile.mkdtemp(prefix="k3-mip-dns-")
        self.addCleanup(shutil.rmtree, self.root, True)
        p = mock.patch.dict(os.environ, {"KASKAD_ROOT": self.root})
        p.start()
        self.addCleanup(p.stop)

    def test_both_external_addresses_of_a_cloud_are_the_servers(self):
        r = Runner({
            ("ip", "-o", "addr", "show"): (0, "2: ens5    inet 10.0.0.5/24 scope global ens5\\\n"
                                              "2: ens5    inet 10.0.0.6/24 scope global secondary ens5\\\n"),
            ("ip", "-4", "route", "get", "1.1.1.1"): (0, "1.1.1.1 via 10.0.0.1 dev ens5 src 10.0.0.5 uid 0 \n"),
            ("curl",): lambda argv: (0, {"10.0.0.5": A_IP, "10.0.0.6": B_IP}.get(
                argv[argv.index("--interface") + 1], "") if "--interface" in argv else A_IP)})
        own4, _ = dns.own_addresses({}, r)
        self.assertEqual(own4, sorted([A_IP, B_IP]))
        self.assertEqual(dns.evaluate("ok", [A_IP, B_IP], [], own4, [])["state"], "ok")       # was «partial»


class PanelListenTest(CliGwCase):
    def setUp(self):
        super().setUp()
        from tests.fakes_nft import save_state
        save_state()
        self.w.ip.links["eth0"]["addrs"].append("203.0.113.77/32")
        self.w.ip.add_link("eth1", "ether", ["10.10.0.5/24"])            # a private network of the hoster
        self.w.ip.add_link("docker0", "bridge", ["172.17.0.1/16"])

    def test_a_second_interface_only_when_chosen_by_hand(self):
        self.assertEqual(conf.listen_addresses(self.w.runner, "203.0.113.10"), ["203.0.113.10", "203.0.113.77"])
        self.ok("gw", "addresses", "add", "93.184.216.34", "--local", "10.10.0.5")
        self.assertEqual(conf.listen_addresses(self.w.runner, "203.0.113.10"),
                         ["203.0.113.10", "203.0.113.77", "10.10.0.5"])
        rc, _, _ = self.run_json("gw", "addresses", "add", "93.184.216.35", "--local", "172.17.0.1")
        self.assertEqual(rc, errors.CONFLICT)                                     # never a container network
        st = state.load()
        st["gateway"]["addresses"]["manual"].append({"ip": "93.184.216.35", "local": "172.17.0.1"})   # by hand in the file
        state.save(st)
        self.assertEqual(conf.listen_addresses(self.w.runner, "203.0.113.10"),
                         ["203.0.113.10", "203.0.113.77", "10.10.0.5"])

    def test_a_public_address_of_another_interface_is_chosen_by_hand(self):
        """REV З-3: B on eth2 (not the interface of the route) — «добавить вручную» is the plan's choice (§2.2 С2)."""
        self.w.ip.add_link("eth2", "ether", ["85.143.1.5/24"])
        self.assertEqual(conf.listen_addresses(self.w.runner, "203.0.113.10"), ["203.0.113.10", "203.0.113.77"])
        _, doc, _ = self.run_json("gw", "addresses", "add", "85.143.1.5")
        self.assertTrue(doc["added"])
        self.assertIn("интерфейс eth2", doc["text"])
        self.assertEqual(state.load()["gateway"]["addresses"]["manual"], [{"ip": "85.143.1.5", "local": "85.143.1.5"}])
        self.assertEqual(conf.listen_addresses(self.w.runner, "203.0.113.10"),
                         ["203.0.113.10", "203.0.113.77", "85.143.1.5"])
        self.ok("gw", "addresses", "remove", "85.143.1.5")
        self.assertEqual(conf.listen_addresses(self.w.runner, "203.0.113.10"), ["203.0.113.10", "203.0.113.77"])

    def test_bind_on_another_interface_and_the_same_by_hand_once(self):
        """REV П-2: the manual ``local`` equal to ``bind`` (on another interface) — listed once."""
        self.ok("gw", "addresses", "add", "93.184.216.34", "--local", "10.10.0.5")
        self.assertEqual(conf.listen_addresses(self.w.runner, "10.10.0.5"), ["10.10.0.5"])

    def test_a_bind_gone_from_the_machine_is_read_again(self):
        self.assertEqual(conf.listen_addresses(self.w.runner, "203.0.113.99"), ["203.0.113.10", "203.0.113.77"])
        self.assertEqual(conf.listen_addresses(self.w.runner, "127.0.0.1"), ["127.0.0.1"])    # on the machine: as it was
        self.assertEqual(conf.listen_addresses(self.w.runner, "203.0.113.77"), ["203.0.113.77", "203.0.113.10"])


class PanelNatNamesTest(PanelCase):
    def test_the_external_pair_of_a_listened_address_is_a_name(self):
        A.save_cache({"at": 1, "route": {"dev": "ens5", "via": "10.0.0.1", "src": "10.0.0.5"},
                      "addrs": [{"ip": "10.0.0.5", "iface": "ens5", "v": 4}, {"ip": "10.0.0.6", "iface": "ens5", "v": 4}],
                      "nat": [{"local": "10.0.0.5", "ip": A_IP}, {"local": "10.0.0.6", "ip": B_IP}], "nat_at": 1,
                      "ways": {}})
        self.panel._addrs = ["10.0.0.5", "10.0.0.6"]
        self.assertEqual(self.panel._nat_names(state.load()), {A_IP, B_IP})
        self.panel._addrs = ["10.0.0.5"]
        self.assertIn(A_IP, self.panel._public_names())
        self.assertNotIn(B_IP, self.panel._public_names())


@unittest.skipUnless(shutil.which("jq"), "нет jq")
class MenuTest(unittest.TestCase):
    STUBS = r"""
t() { printf '%s' "$1"; }
tf() { local k=$1; shift; printf '%s' "$k:$*"; }
k3_gw_head() { :; }
k3_gw_fold() { printf '%s\n' "$2"; }
k3_gw_pause() { :; }
k3_note_take() { :; }
k3_bad_choice() { echo "BAD"; }
k3_gw_show_warn() { :; }
k3_choose_yes() { echo "Q:$1 [$3|$4]"; [ "$YES" = 1 ]; }
k3_gw() {
    echo "CALL:$*"
    K3_GW_RC=0; K3_GW_ERR=""
    case "$1 $#" in
        "host 1") K3_GW_OUT=$HOSTDOC ;;
        "addresses 1") K3_GW_OUT=$ADDRS; [ -n "$NOADDR" ] && K3_GW_RC=1 ;;
        "host 2")
            if [ "$2" = 85.143.0.99 ] && [ ! -e "$FLAG" ]; then
                K3_GW_RC=3; K3_GW_ERR="NOT_HERE"; K3_GW_OUT='{"ok":false,"data":{"reason":"address_not_here"}}'
            else K3_GW_OUT="{\"ok\":true,\"host\":\"$2\"}"; fi ;;
        "addresses 3") : > "$FLAG"; K3_GW_OUT='{"ok":true}' ;;
        *) K3_GW_OUT='{"ok":true,"host":"x"}' ;;
    esac
    return $K3_GW_RC
}
WHITE="" NC="" YELLOW="" GREEN="" RED=""
"""
    ADDRS = {"ok": True, "client_found": True, "addresses": [
        {"ip": A_IP, "origin": "iface", "text": A_IP + " — интерфейс eth0 · основной · адрес для клиентов"},
        {"ip": B_IP, "origin": "iface", "text": B_IP + " — интерфейс eth0"}]}

    def run_menu(self, keys, cur=A_IP, addrs=None, yes=False, noaddr=False):
        import tempfile
        d = tempfile.mkdtemp(prefix="k3-mip-menu-")
        self.addCleanup(shutil.rmtree, d, True)
        src = "".join(function(n) for n in ("k3_jq", "k3_term_esc", "k3_gw_name_arg", "k3_gw_host_menu")) + self.STUBS
        src += "k3_gw_host_menu <<'EOF'\n%sEOF\n" % "".join(k + "\n" for k in keys)
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LC_ALL": "C.UTF-8", "FLAG": os.path.join(d, "added"),
               "HOSTDOC": json.dumps({"ok": True, "host": cur, "auto": True}),
               "ADDRS": json.dumps(addrs or self.ADDRS), "YES": "1" if yes else "0", "NOADDR": "1" if noaddr else ""}
        r = subprocess.run(["bash", "-c", src], capture_output=True, text=True, timeout=60, env=env)
        return r.stdout + r.stderr

    def test_the_addresses_are_numbered_items(self):
        out = self.run_menu(["2", "0"])
        self.assertIn("GWS_HOST_LIST\n 1) %s — интерфейс eth0 · основной · адрес для клиентов\n 2) %s — интерфейс eth0\n"
                      " 3) GWS_HOST_OTHER\n 4) GWS_HOST_M2\n 0) C_BACK" % (A_IP, B_IP), out)
        self.assertIn("CALL:host %s" % B_IP, out)
        self.assertIn("GWS_HOST_OK:%s" % B_IP, out)

    def test_the_current_one_changes_nothing(self):
        out = self.run_menu(["1", "0"])
        self.assertIn("GWS_HOST_SAME:%s" % A_IP, out)
        self.assertNotIn("CALL:host %s" % A_IP, out)

    def test_another_address_not_here_asks_and_adds(self):
        out = self.run_menu(["3", "85.143.0.99", "0"], yes=True)
        self.assertIn("NOT_HERE", out)
        self.assertIn("Q:GWS_HOST_ADD_Q:85.143.0.99 [GWS_HOST_ADD_YES|C_CANCEL]", out)
        self.assertLess(out.index("CALL:addresses add 85.143.0.99"), out.rindex("CALL:host 85.143.0.99"))
        self.assertIn("GWS_HOST_OK:85.143.0.99", out)
        out = self.run_menu(["3", "85.143.0.99", "0"], yes=False)
        self.assertNotIn("CALL:addresses add", out)
        self.assertNotIn("GWS_HOST_OK", out)

    def test_a_gone_address_is_said_and_no_list_falls_back(self):
        out = self.run_menu(["0"], cur="85.143.0.99", addrs=dict(self.ADDRS, client_found=False))
        self.assertIn("GWS_HOST_GONE:85.143.0.99 85.143.0.99", out)               # REV З-2: «add it: … add <ip>»
        out = self.run_menu(["2", "0"], noaddr=True)
        self.assertIn(" 1) GWS_HOST_M1\n 2) GWS_HOST_M2\n 0) C_BACK", out)
        self.assertIn("CALL:host --detect", out)
        out = self.run_menu(["x", "9", "0"])
        self.assertEqual(out.count("BAD"), 2)

    def test_the_client_address_not_the_primary_one_is_said(self):
        # T-mip-lab Н6: the hoster made B primary, the address for clients stayed A — said, nothing changed by itself
        out = self.run_menu(["0"], addrs=dict(self.ADDRS, client_primary=False, primary=B_IP))
        self.assertIn("GWS_HOST_NOT_PRIMARY:%s %s %s" % (A_IP, B_IP, B_IP), out)
        self.assertNotIn("CALL:host %s" % B_IP, out)
        self.assertNotIn("GWS_HOST_NOT_PRIMARY", self.run_menu(["0"], addrs=dict(self.ADDRS, client_primary=True)))


if __name__ == "__main__":
    unittest.main()
