"""F1 (3.3.7.15, PLAN-n13 Ш1): «ждут новых настроек» для всех протоколов — gw/reissue.py, единственный владелец
reissue.json. Пометки объединяются (порт, потом MTU — по-прежнему «не подключится»), жёсткие и мягкие, «порт» снимает
первое подключение на новом порту (круг сборщика; у WireGuard, AmneziaWG и SOCKS5 — только выдача: П4, роуминг WG),
выдача снимает всё, подписка — нет; старые версии читают новый
файл без ошибок, новая читает старый как жёсткие ``mtu``. Смена порта у протоколов (F3) — ``SettingsMarksTest``."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from kaskad import state
from kaskad.gw import collect, mtu as gwmtu, ops, registry, reissue
from tests.gwcase import GwCase
from tests.panel.panelcase import PanelCase
from tests.userpage_case import PageCase

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OLD = {"3.3.7.13": "bc541e29", "3.3.7.14": "fc5ef683"}
_TREES = {}

D1, D2, D3 = "d-00000001", "d-00000002", "d-00000003"     # Телефон (Мама): wg; Роутер на даче (Дача): awg2+wg; Ноутбук


def tearDownModule():
    for d in _TREES.values():
        shutil.rmtree(d, ignore_errors=True)
    _TREES.clear()                          # test_r15_settings takes the trees too: a removed one is made again


def old_lib(rev):
    """lib/ of a released commit (git archive) — SkipTest without git or the commit."""
    if rev in _TREES:
        return os.path.join(_TREES[rev], "lib")
    try:
        ok = subprocess.run(["git", "-C", REPO, "cat-file", "-e", rev + "^{commit}"], capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        raise unittest.SkipTest("no git")
    if ok.returncode != 0:
        raise unittest.SkipTest("no commit %s" % rev)
    d = tempfile.mkdtemp(prefix="k3-r15-reissue-")
    arc = subprocess.run(["git", "-C", REPO, "archive", rev, "lib/kaskad"], capture_output=True, timeout=120)
    if arc.returncode != 0:
        shutil.rmtree(d, ignore_errors=True)
        raise unittest.SkipTest("git archive %s" % rev)
    subprocess.run(["tar", "-x", "-C", d], input=arc.stdout, check=True, timeout=120)
    _TREES[rev] = d
    return os.path.join(d, "lib")


OLD_SIDE = r"""
import json, sys
from kaskad.gw import mtu
marks = mtu._marks()
mtu.issued("wg", "d-00000001")
print(json.dumps({"marks": marks, "after": mtu._marks()}, sort_keys=True))
"""


class ReissueCase(GwCase):
    def setUp(self):
        super().setUp()
        reissue._SEEN.update(sig=None, port=True)
        self.st = self.scenario()
        g = self.st["gateway"]
        # SSTP and SOCKS5 — the protocols without WireGuard's MTU (F1: «для всех протоколов»)
        g["inbounds"]["sstp"] = {"enabled": True, "port": 443}
        g["inbounds"]["socks5"] = {"enabled": True, "port": 1080}
        g["inbounds"]["vless"] = {"enabled": True, "port": 8443, "security": "reality"}
        for d in g["devices"]:
            if d["id"] == D1:
                d["access"] = ["wg", "sstp", "socks5", "vless"]
        state.save(self.st)
        self.st = state.load()

    def doc(self):
        with open(reissue.path(), encoding="utf-8") as f:
            return json.load(f)


class MarksTest(ReissueCase):
    def test_port_then_mtu_stays_hard_and_unites(self):
        reissue.mark("wg", [D1], ["port"], True, by="a-00000001", now=1000)
        reissue.mark("wg", [D1, D2], ["mtu"], False, by="a-00000002", now=1100)
        m = reissue.load()["wg"]
        self.assertEqual(m[D1]["what"], ["port", "mtu"])
        self.assertTrue(m[D1]["hard"])                                   # MTU after the port is no «softer»
        self.assertEqual(m[D1]["at"], {"port": 1000, "mtu": 1100})
        self.assertFalse(m[D2]["hard"])
        self.assertEqual(sorted(m), [D1, D2])                            # the union of the devices
        self.assertEqual(self.doc()["wg"], [D1])                         # the old lists: the hard marks only

    def test_mark_waiting_of_s4_unites_instead_of_replacing(self):
        reissue.mark("awg2", [D2], ["port"], True, now=1000)
        gwmtu.mark_waiting("awg2", [])                                    # 3.3.7.14 replaced the list: D2 was lost
        gwmtu.mark_waiting("awg2", [D2])
        m = reissue.load()["awg2"][D2]
        self.assertEqual((m["what"], m["hard"]), (["port", "s4"], True))

    def test_waiting_and_view_drop_the_gone_and_the_unauthorised(self):
        reissue.mark("wg", [D1, D3, "d-0000dead"], ["port"], True, now=1000)
        reissue.mark("sstp", [D1, D2], ["port"], True, now=1000)          # D2 has no SSTP: dropped at the write
        self.assertEqual(sorted(reissue.load()["wg"]), [D1, D3])
        self.assertEqual(sorted(reissue.load()["sstp"]), [D1])
        w = reissue.waiting(self.st)
        self.assertEqual([r["id"] for r in w["wg"]], [D1, D3])
        self.assertEqual(w["wg"][0]["name"], "Телефон (Мама)")
        for d in self.st["gateway"]["devices"]:
            if d["id"] == D3:
                d["access"] = []                                          # access taken: not shown
        self.assertEqual([r["id"] for r in reissue.waiting(self.st)["wg"]], [D1])

    def test_view_has_titles_and_splits_hard_from_soft(self):
        reissue.mark("sstp", [D1], ["port"], True, now=1000)
        reissue.mark("wg", [D3], ["mtu"], False, now=1000)
        v = reissue.view(self.st)
        self.assertEqual(v["sstp"]["title"], "SSTP")
        self.assertEqual(v["wg"]["title"], registry.title("wg"))       # the adapter's title (a dummy here)
        self.assertNotEqual(v["wg"]["title"], "wg")
        self.assertEqual([r["name"] for r in v["sstp"]["hard"]], ["Телефон (Мама)"])
        self.assertEqual((v["wg"]["hard"], [r["id"] for r in v["wg"]["soft"]]), ([], [D3]))

    def test_issue_takes_the_mark_off_the_subscription_does_not(self):
        reissue.mark("wg", [D1, D3], ["port", "mtu"], True, now=1000)
        ops.issue(self.w.runner, D3, inbound="wg", mark=False)           # the subscription of /p/
        self.assertIn(D3, reissue.load()["wg"])
        ops.issue(self.w.runner, D3, inbound="wg")
        self.assertNotIn(D3, reissue.load()["wg"])
        self.assertIn(D1, reissue.load()["wg"])

    def test_drop_by_brings_back_the_mark_before_the_application(self):
        reissue.mark("wg", [D1], ["port"], True, by="a-00000001", now=1000)
        reissue.mark("wg", [D1, D3], ["mtu"], False, by="a-00000002", now=1100)
        self.assertEqual(reissue.marked_by("wg", "a-00000002"), [D1, D3])
        self.assertEqual(sorted(reissue.drop_by("wg", "a-00000002")), [D1, D3])
        m = reissue.load()["wg"]
        self.assertEqual(sorted(m), [D1])
        self.assertEqual((m[D1]["what"], m[D1]["hard"], m[D1].get("by")), (["port"], True, "a-00000001"))
        self.assertEqual(reissue.drop_by("wg", "a-00000002"), [])

    def test_soft_marks_stay_out_of_the_overview_card_and_the_bot(self):
        from kaskad.bot import gateway as bgw
        reissue.mark("wg", [D3], ["mtu"], False, now=1000)               # soft: «желательно выдать заново»
        reissue.mark("sstp", [D1], ["port"], True, now=1000)             # hard, not WireGuard
        self.assertEqual(gwmtu.waiting(self.st), {})                       # the card «ждут» of the MTU view
        rows = [r for r in gwmtu.view(self.st)["inbounds"] if r["waiting"]]
        self.assertEqual(rows, [])
        _, wait = bgw._mtu_rows(self.st)
        self.assertEqual(wait, [{"kind": "sstp", "title": "SSTP", "waiting": ["Телефон (Мама)"]}])
        for r in wait:
            self.assertNotEqual(r["title"], r["kind"])                    # Д6: no bare kind for people



class SettingsMarksTest(ReissueCase):
    """F3 decides how hard a mark is (F1 checks it): a new port leaves every device of the protocol without working
    settings; OpenVPN with two ``remote`` — soft while the other one still connects."""

    PORTED = ("wg", "awg1", "awg2", "awg3", "openvpn", "ocserv", "sstp", "vless", "socks5", "hy2")

    def test_a_new_port_is_hard_on_each_of_the_ten(self):
        g = self.st["gateway"]
        for kind in self.PORTED:
            rec = g["inbounds"].setdefault(kind, {"enabled": True, "port": 40000})
            rec["enabled"] = True
        g["inbounds"]["openvpn"] = {"enabled": True, "udp_port": 1194, "tcp_port": 0}
        for d in g["devices"]:
            if d["id"] == D1:
                d["access"] = list(self.PORTED)
        for kind in self.PORTED:
            field = "udp_port" if kind == "openvpn" else "port"
            m = ops._settings_marks(self.st, kind, {"field": field, "port": 40001})
            self.assertEqual((m["what"], m["hard"]), (["port"], True), kind)
            self.assertIn(D1, m["ids"], kind)
        m = ops._settings_marks(self.st, "wg", {"field": "port", "port": 40001, "mtu": 1300})
        self.assertEqual((m["what"], m["hard"]), (["port", "mtu"], True))
        m = ops._settings_marks(self.st, "wg", {"field": None, "port": None, "mtu": 1300})
        self.assertEqual((m["what"], m["hard"]), (["mtu"], False))

    def test_openvpn_with_two_remotes(self):
        g = self.st["gateway"]
        for d in g["devices"]:
            if d["id"] == D1:
                d["access"] = ["openvpn"]
        g["inbounds"]["openvpn"] = {"enabled": True, "udp_port": 1194, "tcp_port": 443}
        m = ops._settings_marks(self.st, "openvpn", {"field": "udp_port", "port": 1195})
        self.assertEqual((m["hard"], m["inst"]), (False, ["udp"]))       # the TCP remote still connects
        m = ops._settings_marks(self.st, "openvpn", {"field": "tcp_port", "port": 8443})
        self.assertEqual((m["hard"], m["inst"]), (False, ["tcp"]))
        g["inbounds"]["openvpn"]["tcp_port"] = 0                          # no working remote left
        m = ops._settings_marks(self.st, "openvpn", {"field": "udp_port", "port": 1195})
        self.assertEqual((m["hard"], m["inst"]), (True, ["udp"]))


class ReissueApiTest(PanelCase):
    def test_get_reissue_is_the_view_for_an_admin_only(self):
        self.scenario()
        reissue.mark("wg", [D1], ["port"], True, now=1000)
        reissue.mark("wg", [D3], ["mtu"], False, now=1000)
        doc = self.ok("GET", "/api/gw/reissue")
        self.assertEqual(doc["reissue"]["wg"]["title"], registry.title("wg"))
        self.assertEqual([r["id"] for r in doc["reissue"]["wg"]["hard"]], [D1])
        self.assertEqual([r["what"] for r in doc["reissue"]["wg"]["soft"]], [["mtu"]])
        self.assertIn(self.call("GET", "/api/gw/reissue", tunnel=False).status, (401, 403))


class SeenTest(ReissueCase):
    def all_handshake_kinds(self):
        with state.transaction() as st:
            for d in st["gateway"]["devices"]:
                if d["id"] == D1:
                    d["access"] = sorted(set(d["access"]) | set(reissue.HANDSHAKE_KINDS))
        self.st = state.load()

    def test_a_handshake_after_the_change_keeps_the_port_of_wg(self):
        # stand T-3315 2.3 (П4): a live session moves to the new port by WireGuard's roaming (the server sends from
        # the new port, the client takes it) — a handshake after the change, while the device keeps the old port
        self.all_handshake_kinds()
        reissue.mark("wg", [D1], ["port"], True, now=1000)
        ss = [{"kind": "wg", "device": D1, "ip": "10.77.1.10", "since": 1001}]
        self.assertEqual(reissue.seen(ss, now=2000, st=self.st), [])
        self.assertIn(D1, reissue.load()["wg"])
        self.assertEqual(self.doc()["wg"], [D1])                            # hard: the card and the bot
        self.assertTrue(reissue.issued("wg", D1, st=self.st))               # only a new issue takes it off
        self.assertEqual(reissue.seen([dict(ss[0], since=3000)], now=3000, st=self.st), [])
        self.assertEqual(reissue.load(), {})

    def test_a_handshake_after_the_change_takes_the_port_of_awg_off(self):
        # П5 З-1: the port of AmneziaWG changes with a restart of its line — the old session is cut (stand 2.3: the
        # old client c3a — 000), a handshake after the change is a device with the new settings (the subscription)
        self.all_handshake_kinds()
        awg = [k for k in reissue.HANDSHAKE_KINDS if k != "wg"]
        self.assertEqual(awg, ["awg1", "awg2", "awg3"])
        for kind in awg:
            reissue.mark(kind, [D1], ["port"], True, now=1000)
        old = [{"kind": k, "device": D1, "ip": "10.77.1.10", "since": 999} for k in awg]
        self.assertEqual(reissue.seen(old, now=2000, st=self.st), [])       # a handshake from before the change
        new = [dict(x, since=1001) for x in old]
        self.assertEqual(sorted(reissue.seen(new, now=2000, st=self.st)), [(k, D1) for k in awg])
        self.assertEqual(reissue.load(), {})

    def test_restarting_kinds_count_a_round_30_s_after(self):
        reissue.mark("sstp", [D1], ["port"], True, now=1000)                # accel-ppp restarts with a new port
        s = [{"kind": "sstp", "device": D1, "ip": "10.77.6.10", "since": 1001}]
        self.assertEqual(reissue.seen(s, now=1020, st=self.st), [])          # too early: an old session's round
        self.assertEqual(reissue.seen(s, now=1031, st=self.st), [("sstp", D1)])
        self.assertNotIn("sstp", reissue.load())

    def vless_rounds(self, rounds, mark_at=1000):
        """Sessions of the real ``VlessInbound.traffic`` (REV-3315-a1 С-1): ``rounds`` [(now, counters)], the port
        changed at ``mark_at`` (after the rounds before it). -> the sessions of the last round."""
        from unittest import mock
        from kaskad.gw.inbounds.vless import VlessInbound
        ad, users = VlessInbound(), {"lg1": D1}
        seq = iter([c for _, c in rounds])
        out = None
        with mock.patch.object(VlessInbound, "counters", lambda self, runner: next(seq)):
            for now, _ in rounds:
                if now > mark_at and reissue.load().get("vless") is None:
                    reissue.mark("vless", [D1], ["port"], True, now=mark_at)
                out = [dict(x, kind="vless") for x in ad.traffic(None, users, now=now)[0]]
        return out

    def test_an_xray_session_from_before_the_change_proves_nothing(self):
        # grew at 990 and 995, the port changed at 1000, xray restarted (counters from 0): the session is still
        # listed (ACTIVE 120 s after the last growth) with since 990 — the device never came on the new port
        ss = self.vless_rounds([(990, {"lg1": (100, 100)}), (995, {"lg1": (200, 200)}), (1035, {"lg1": (0, 0)})])
        self.assertEqual([(x["device"], x["since"], x["last"]) for x in ss], [(D1, 990, 995)])
        self.assertEqual(reissue.seen(ss, now=1035, st=self.st), [])
        self.assertIn(D1, reissue.load()["vless"])

    def test_an_xray_growth_after_the_change_takes_the_port_off(self):
        ss = self.vless_rounds([(990, {"lg1": (100, 100)}), (995, {"lg1": (200, 200)}), (1035, {"lg1": (0, 0)}),
                                (1040, {"lg1": (10, 50)})])
        self.assertEqual(ss[0]["last"], 1040)
        self.assertEqual(reissue.seen(ss, now=1040, st=self.st), [("vless", D1)])
        self.assertNotIn("vless", reissue.load())

    def test_the_collector_keeps_the_last_growth_of_xray(self):
        st = dict(self.st)
        st["gateway"] = dict(st["gateway"], inbounds=dict(st["gateway"]["inbounds"]))
        stats = {"vless": {"sessions": [{"device": D1, "ip": None, "since": 990, "last": 1040, "rx": 1, "tx": 2}]},
                 "wg": {"sessions": [{"device": D1, "ip": "10.77.1.10", "since": 1000, "rx": 1, "tx": 1}]}}
        got = dict((x["kind"], x) for x in collect.sessions(st, self.w.runner, stats))
        self.assertEqual(got["vless"]["last"], 1040)
        self.assertNotIn("last", got["wg"])                                 # WireGuard: its handshake is «since»

    def test_a_growth_in_the_first_30_s_is_not_enough(self):
        ss = self.vless_rounds([(990, {"lg1": (100, 100)}), (1010, {"lg1": (300, 300)}), (1031, {"lg1": (300, 300)})])
        self.assertEqual(ss[0]["last"], 1010)                               # old port still served for a moment?
        self.assertEqual(reissue.seen(ss, now=1031, st=self.st), [])

    def test_socks5_only_by_issue_and_mtu_never_by_session(self):
        reissue.mark("socks5", [D1], ["port"], True, now=1000)
        reissue.mark("sstp", [D1], ["port", "mtu"], True, now=1000)
        got = reissue.seen([{"kind": "socks5", "device": D1, "ip": None, "since": 2000},
                            {"kind": "sstp", "device": D1, "ip": "10.77.6.10", "since": 2000}], now=2000, st=self.st)
        self.assertEqual(got, [("sstp", D1)])
        self.assertIn(D1, reissue.load()["socks5"])
        m = reissue.load()["sstp"][D1]
        self.assertEqual((m["what"], m["hard"], m.get("by")), (["mtu"], False, None))   # MTU stays (soft now)

    def test_openvpn_only_the_instance_whose_port_changed(self):
        st = self.st
        st["gateway"]["inbounds"]["openvpn"] = {"enabled": True, "udp_port": 1194, "tcp_port": 1443}
        for d in st["gateway"]["devices"]:
            if d["id"] == D1:
                d["access"].append("openvpn")
        state.save(st)
        st = state.load()
        reissue.mark("openvpn", [D1], ["port"], False, now=1000, inst=["udp"])
        tcp = [{"kind": "openvpn", "device": D1, "ip": "10.77.10.10", "since": 1100}]
        self.assertEqual(reissue.seen(tcp, now=1100, st=st), [])             # on the TCP one: proves nothing
        udp = [{"kind": "openvpn", "device": D1, "ip": "10.77.9.10", "since": 1100}]
        self.assertEqual(reissue.seen(udp, now=1100, st=st), [("openvpn", D1)])

    def test_the_file_is_not_read_again_without_port_marks(self):
        reissue.mark("wg", [D3], ["mtu"], False, now=1000)
        reissue.seen([], now=1100, st=self.st)
        calls = []
        orig = reissue.load
        try:
            reissue.load = lambda: calls.append(1) or orig()
            reissue.seen([{"kind": "wg", "device": D3, "since": 2000}], now=2000, st=self.st)
        finally:
            reissue.load = orig
        self.assertEqual(calls, [])                                       # mtime the same, no «port» in it

    def test_the_file_is_not_read_again_for_port_marks_only_an_issue_takes_off(self):
        reissue.mark("wg", [D3], ["port"], True, now=1000)
        reissue.mark("socks5", [D1], ["port"], True, now=1000)
        reissue.seen([], now=1100, st=self.st)
        calls = []
        orig = reissue.load
        try:
            reissue.load = lambda: calls.append(1) or orig()
            reissue.seen([{"kind": "wg", "device": D3, "since": 2000}], now=2000, st=self.st)
        finally:
            reissue.load = orig
        self.assertEqual(calls, [])                                       # no «port» a session takes off
        reissue.mark("sstp", [D1], ["port"], True, now=1000)              # the file changed, a session may take it
        self.assertEqual(reissue.seen([{"kind": "sstp", "device": D1, "ip": "10.77.6.10", "since": 2000}],
                                      now=2000, st=self.st), [("sstp", D1)])

    def test_the_collector_round_keeps_the_port_of_wg(self):
        self.now = 1730300000
        reissue.mark("wg", [D1], ["port"], True, now=self.now - 100)
        self.ads["wg"].running = True
        self.ads["wg"].sessions = [{"device": D1, "ip": "10.77.1.10", "since": self.now - 5, "rx": 1, "tx": 1}]
        res = collect.tick(self.w.runner)
        self.assertTrue(res.get("enabled"), res)
        self.assertIn(D1, reissue.load()["wg"])                           # roamed to the new port: still waits


class PageMarksTest(PageCase):
    """П5 С-1: the page of a user — «Показать» (``POST /p/<t>/keys``) builds the settings of every device and kind of
    the user: that is not «выдали», the marks stay; the file of one device and kind (``GET /p/<t>/file/…``) is — its
    mark alone goes. Before: one «Показать» took the marks off every device of the user (the TV, the router)."""

    def test_show_keeps_the_marks_a_file_takes_its_own_off(self):
        p1, p2, p3 = "d-00000001", "d-00000002", "d-00000003"
        reissue.mark("wg", [p1, p2, p3], ["port"], True, now=1000)
        reissue.mark("awg2", [p1], ["port"], True, now=1000)
        before = reissue.load()
        tok = self.token()
        doc = self.keys_json(tok)
        self.assertEqual(reissue.load(), before)
        dev = next(d for d in doc["devices"] if d["id"] == p1)
        inb = next(i for i in dev["inbounds"] if i["kind"] == "wg")
        url = next(it["file"] for it in inb["items"] if it.get("file"))
        self.assertEqual(self.call("GET", url).status, 200)
        after = reissue.load()
        self.assertEqual(sorted(after["wg"]), [p2, p3])                     # only this device and this kind
        self.assertEqual(sorted(after["awg2"]), [p1])


class FilesTest(ReissueCase):
    def test_the_file_of_3_3_7_14_reads_as_hard_mtu(self):
        with open(reissue.path(), "w", encoding="utf-8") as f:
            json.dump({"awg2": [D2], "wg": [D1, D3]}, f)
        m = reissue.load()
        self.assertEqual(m["awg2"][D2], {"what": ["mtu"], "hard": True, "at": {}})
        self.assertEqual(sorted(r["name"] for r in reissue.view(self.st)["wg"]["hard"]), ["Ноутбук (Мама)",
                                                                                           "Телефон (Мама)"])
        self.assertEqual(gwmtu.waiting(self.st)["awg2"], [{"id": D2, "name": "Роутер на даче (Дача)"}])

    def test_garbage_reads_as_nothing(self):
        for bad in ("[1, 2]", "{bad", json.dumps({"why": [1], "wg": "x"}),
                    json.dumps({"why": {"wg": {D1: {"what": ["tls"], "hard": True}}}})):
            with open(reissue.path(), "w", encoding="utf-8") as f:
                f.write(bad)
            self.assertEqual(reissue.load(), {}, bad)

    def test_old_versions_read_the_new_file(self):
        reissue.mark("wg", [D1], ["port"], True, by="a-00000001", now=1000)
        reissue.mark("wg", [D3], ["mtu"], False, now=1000)                 # soft: old versions never show it
        reissue.mark("awg2", [D2], ["s4"], True, now=1000)
        reissue.mark("sstp", [D1], ["port"], True, now=1000)               # not WireGuard: old versions skip it
        self.assertEqual(os.stat(reissue.path()).st_mode & 0o777, 0o600)
        with open(reissue.path(), encoding="utf-8") as f:
            new = f.read()
        for ver, rev in sorted(OLD.items()):
            with open(reissue.path(), "w", encoding="utf-8") as f:
                f.write(new)
            env = dict(os.environ, PYTHONPATH=old_lib(rev), KASKAD_ROOT=os.environ["KASKAD_ROOT"])
            p = subprocess.run([sys.executable, "-c", OLD_SIDE], env=env, capture_output=True, text=True, timeout=60,
                               cwd=tempfile.gettempdir())
            self.assertEqual(p.returncode, 0, "%s: %s" % (ver, p.stderr[-800:]))
            got = json.loads(p.stdout.strip().splitlines()[-1])
            self.assertEqual(got["marks"], {"awg2": [D2], "wg": [D1]}, ver)
            self.assertEqual(got["after"], {"awg2": [D2]}, ver)             # its own write: why and others dropped
            m = reissue.load()                                              # and back on this version: no errors
            self.assertEqual(sorted(m), ["awg2"])
            self.assertEqual(m["awg2"][D2], {"what": ["mtu"], "hard": True, "at": {}})   # «s4» lost: a hint only


if __name__ == "__main__":
    unittest.main()
