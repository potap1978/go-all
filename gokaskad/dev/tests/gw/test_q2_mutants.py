"""REV-s1-q2 З-2: тесты на выжившие порчи Q2 (таблица в k3/stand/logs/REV-s1-q2.md) — каждая строка таблицы держится
здесь (или рядом: hook_lockwait, hook_ruopen, set_rx_fallback, verify_rx — tests/gw/test_net_rx.py; b_fail_restore —
tests/test_q4_ru_legs.py)."""

from unittest import mock

from kaskad import nft, secrets, state
from kaskad.gw import collect, exits, model, net, peek, xray
from tests.gw.test_ru_target import D1, D3, HOME, RuWorld
from tests.test_rules_apply_ru_exit import _XrayCase

D1_IP = "10.77.1.10"


class PeekKindTest(RuWorld):
    """peek_kind: gw_rx is a MAP — read as one (nft's «list set» refuses a map); the collector's update_map then brings
    a changed pin of the Russian group into gw_rx without a full table."""

    def test_the_collector_moves_the_mark_in_gw_rx(self):
        st = self.world()
        nft.apply(st, self.w.runner)
        mine = self.w.nft.elements("gw_rx")[D1_IP]
        other = [e for e in self.ru_rows if e["mark"] != mine][0]
        dec = self.dec(st)
        dec["groups"] = dict(dec.get("groups") or {}, **{HOME: {"pins": {D1: {"exit": other["id"], "tier": "t1",
                                                                               "why": "evict"}}}})
        live = peek.read(self.w.runner, sets=peek.RX_SETS)
        self.assertIn(net.RX, live.get("set_info") or {})
        self.assertTrue(net.update_map(self.w.runner, state.load(), live=live, decision=dec))
        self.assertEqual(self.w.nft.elements("gw_rx")[D1_IP], other["mark"])


class RuLastByTheGroupTest(RuWorld):
    """ru_last_route: the last step of the Russian side is by ``R.fail`` alone (§2.8) — never by the route of the
    device (its foreign ``fail`` direct must not open Russia from the node's address)."""

    def test_route_fail_direct_group_fail_block(self):
        st = self.world(fail="block")
        for d in st["gateway"]["devices"]:
            if d["id"] == D1:
                d["route"] = {"via": "pool", "fail": "direct"}
        state.save(st)
        st = state.load()
        t = exits.ru_target(st, self.dev(st), self.dec(st, alive=()))
        self.assertEqual((t["step"], t["mark"]), ("last_block", exits.MARK_BLOCK), t)
        # the pinned exit lost its mark (a line that no longer parses): the last step — still by R.fail alone
        import copy
        dec = self.dec(st)
        a = self.ru_ids[0]
        dec["groups"] = {HOME: {"pins": {D1: {"exit": a, "tier": "t1", "why": "new"}}, "steps": {D1: "exit"}}}
        dec["rows"] = copy.deepcopy(dec["rows"])
        dec["rows"][a]["mark"] = None
        t = exits.ru_target(st, self.dev(st), dec)
        self.assertEqual((t["step"], t["mark"]), ("last_block", exits.MARK_BLOCK), t)


class XrayKnownTest(_XrayCase):
    """xray_known: a Russian target whose outbound the config does not have — «fail-block», never a rule to a tag
    xray refuses."""

    def test_an_outbound_not_drawn_is_fail_block(self):
        st = self.socks(self.world(save=False))
        dec = self.dec(st)
        ghost = {"step": "exit", "mark": 0x3c9, "xray": {"outboundTag": "gr-e-0000000000"}, "group": HOME,
                 "exit": "e-0000000000", "tier": "t1"}
        real = exits.ru_target
        with mock.patch.object(exits, "ru_target", lambda s, d, decision=None, is_ready=None:
                               ghost if real(s, d, decision, is_ready) is not None else None):
            cfg = xray.render(st, secrets.load(), self.pool(), (), dec)
        tags = [o["tag"] for o in cfg["outbounds"]]
        self.assertNotIn("gr-e-0000000000", tags)
        rules = [r for r in cfg["routing"]["rules"] if "telefon-0001" in (r.get("user") or [])
                 and any("roscomvpn" in x for x in (r.get("domain") or []) + (r.get("ip") or []))]
        outs = set(r.get("outboundTag") for r in rules)
        self.assertIn(xray.FAIL_BLOCK, outs)                          # the lists «напрямую»: closed
        self.assertNotIn("gr-e-0000000000", outs)
        self.assertFalse([o for o in outs if str(o).startswith("gr-")], outs)


class TeardownRxTest(RuWorld):
    def test_gw_disable_takes_gw_rx(self):
        st = self.world()
        nft.apply(st, self.w.runner)
        script = net.teardown_script(nft.read_live(self.w.runner))
        self.assertIn("delete map %s gw_rx" % nft.TABLE, script)
        self.assertGreater(script.index("delete map %s gw_rx" % nft.TABLE), script.index("delete chain %s gw_pre"
                                                                                          % nft.TABLE))


class RuDevicesTest(RuWorld):
    """devrd_inactive: the Russian group of ``devices_by_group`` has only the devices «Россия напрямую» acts for — not
    one without the consent ⚠РФ, without the profile, off."""

    def test_only_those_russia_acts_for(self):
        st = self.world()
        out = {}
        collect._ru_devices(st, self.dec(st)["policy"], out)
        self.assertEqual(sorted(d["id"] for d in out[HOME]), sorted([D1, D3]))
        no = state.load()
        no["gateway"].pop("rf_consent", None)
        out = {}
        collect._ru_devices(no, self.dec(no)["policy"], out)
        self.assertEqual(out, {})
        off = state.load()
        for d in off["gateway"]["devices"]:
            if d["id"] == D1:
                d["enabled"] = False
            if d["id"] == D3:
                d["rules"] = "all_exit"
        out = {}
        collect._ru_devices(off, self.dec(off)["policy"], out)
        self.assertEqual(out, {})
