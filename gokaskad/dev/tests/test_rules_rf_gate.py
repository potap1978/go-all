"""R2 — рубеж 2, the action (docs/k3/SPEC-stage1-rf-mark-builtin.md §3.5, §9 «Рубеж 2», «Работающий узел»).

«Россия напрямую» applies only with the lists AND ``rfgate.allowed`` (the consent ⚠РФ; on an updated node the mark
``rf_legacy`` or «not migrated yet»). A ru_direct that reached state.json past the check of a write (a hand edit, an
import in the mode ``need``) applies as «всё через выход» in EVERY path: gw_rd and gw_ruonly (the fallback block of a
group), the IKEv2 hook, dnsmasq nftset, the rules of xray (SOCKS5 chains, VLESS chains, the shared ``ls-``), the status,
the scheme and «Куда пойдёт трафик?». An updated node in the mode ``legacy`` renders exactly as without the gate."""

import contextlib
import json
from unittest import mock

from kaskad import nft, secrets, state
from kaskad.gw import exits, hooks, net, trace, xray
from kaskad.i18n import tr
from kaskad.panel import scheme
from kaskad.rules import apply as rapply
from kaskad.rules import ops as rops
from kaskad.rules import rfgate
from tests.fakes_gw import register_dummies, strip_awg_keepalive
from tests.gw.test_trace import DNS, fake_dns
from tests.rules_helpers import consented
from tests.test_rules_gw import SOCKS_IN, RulesGwCase

D1, D2, D3 = "d-00000001", "d-00000002", "d-00000003"      # Телефон (Мама), Роутер на даче (Дача), Ноутбук (Мама)
MAMA = ["10.77.1.10", "10.77.1.12"]                          # gw_rd of «Мама» (d1, d3) with «Россия напрямую»


class GateCase(RulesGwCase):
    def need_state(self, gen=2, socks=False, switches=True, **extra):
        """``ru_state`` (lists, «Мама» on ru_direct) WITHOUT the consent: a hand edit of state.json on ``gen``."""
        st = self.ru_state(save=False)
        g = st["gateway"]
        g.pop("rf_consent", None)
        g.update(extra)
        st["gen"] = gen
        if not switches:
            g["rules"].update(block_ads=False, block_torrent=False)
        if socks:
            register_dummies(("socks5", {"proto": "tcp", "default_port": 1080, "ifaces": [],
                                         "xray_inbound": SOCKS_IN}))
            g["inbounds"]["socks5"] = {"enabled": True, "port": 1080}
            for d in g["devices"]:
                d["access"].append("socks5")
        state.save(st)
        return state.load()

    def give_consent(self):
        st = state.load()
        state.save(consented(st))
        return state.load()

    @staticmethod
    def dev(st, did):
        return next(d for d in st["gateway"]["devices"] if d["id"] == did)

    def ru_rules(self, cfg):
        """The rules of xray that send something directly or «always through the exit» for a user."""
        return [r for r in cfg["routing"]["rules"] if r.get("user") and ("domain" in r or "ip" in r)
                and "ext:" in json.dumps(r)]


class ProfileTest(GateCase):
    def test_hand_edit_on_a_new_node_applies_all_exit(self):
        st = self.need_state()
        self.assertEqual(rfgate.mode(st), "need")
        d1 = self.dev(st, D1)
        self.assertEqual(rapply.profile(st, d1), {"profile": "ru_direct", "from": "user", "applied": "all_exit",
                                                  "why": "no_consent"})
        self.assertFalse(rapply.ru_ok(st))
        self.assertFalse(rapply.ru_ok(st, device=d1))
        self.assertEqual(rapply.profile(st, self.dev(st, D2))["why"], None)          # all_exit asks nothing
        st = self.give_consent()
        self.assertEqual(rfgate.mode(st), "full")
        self.assertEqual(rapply.profile(st, self.dev(st, D1))["applied"], "ru_direct")
        self.assertTrue(rapply.ru_ok(st))
        self.assertTrue(rapply.ru_ok(st, device=self.dev(st, D1)))
        self.assertFalse(rapply.ru_ok(st, device=self.dev(st, D2)))

    def test_no_lists_is_said_before_no_consent(self):
        st = self.need_state()
        self.assertEqual(rapply.profile(st, self.dev(st, D1), is_ready=False)["why"], "no_lists")

    def test_status_default_and_devices(self):
        st = self.need_state(rules={"default": "ru_direct"})
        doc = rops.status(st)
        self.assertEqual((doc["default"], doc["default_applied"], doc["default_why"]),
                         ("ru_direct", "all_exit", "no_consent"))
        self.assertEqual(set(d["applied"] for d in doc["devices"]), {"all_exit"})
        self.assertEqual(doc["ru_direct"], 0)
        doc = rops.status(self.give_consent())
        self.assertEqual((doc["default_applied"], doc["default_why"]), ("ru_direct", None))
        self.assertEqual(doc["ru_direct"], 3)


class FormTest(GateCase):
    """SEC-s1-r2 З1: the second line believes only the FORM the code writes — a broken record goes to the block."""

    JUNK = ({}, {"x": 1}, {"at": None}, {"at": "", "via": "cli", "orgs": []}, {"at": "t", "via": "bot", "orgs": []},
            {"at": "t", "via": "cli"}, {"at": "t", "via": "cli", "orgs": "meta"}, {"at": "t", "via": "cli", "orgs": [1]},
            "yes", 1, ["meta"])

    def test_junk_consent_is_no_consent(self):
        for junk in self.JUNK:
            st = self.need_state(rf_consent=junk)
            self.assertEqual(rfgate.mode(st), "need", junk)
            self.assertFalse(rfgate.allowed(st), junk)
            self.assertEqual(rapply.rd(st), [], junk)
            rf = rfgate.state(st)
            self.assertTrue(rf["required"], junk)                 # honest: the consent IS needed in the mode need
            self.assertIsNone(rf["consent"], junk)
        st = self.need_state(rf_consent={"at": "2026-10-16T00:00:00Z", "via": "panel", "orgs": []})
        self.assertEqual(rfgate.mode(st), "full")                 # the form of grant: who/registry/lang are optional
        self.assertEqual(rapply.rd(st), MAMA)
        self.assertFalse(rfgate.state(st)["required"])

    def test_junk_consent_does_not_skip_the_check_of_a_write(self):
        old = self.need_state(rf_consent={"x": 1})
        new = json.loads(json.dumps(old))
        self.dev(new, D2)["rules"] = "ru_direct"
        with self.assertRaises(Exception) as e:
            rfgate.check(old, new)
        self.assertEqual(e.exception.data["need"], "rf_consent")

    def test_legacy_mark_counts_only_on_an_updated_node(self):
        mark = {"at": "2026-10-16T00:00:00Z", "from": "gen1", "by": "3.3.6"}
        for gen in (2, 3, "1", "2", True, 1.0, 0, None):
            st = self.need_state(gen=gen, rf_gate=1, rf_legacy=dict(mark))
            self.assertEqual(rfgate.mode(st), "need", gen)
            self.assertEqual(rapply.rd(st), [], gen)
        self.assertEqual(rfgate.mode(self.need_state(gen=1, rf_gate=1, rf_legacy=dict(mark))), "legacy")
        for junk in ({}, {"x": 1}, {"at": "t"}, {"at": "t", "from": "gen2"}, {"at": None, "from": "gen1"}, "gen1"):
            st = self.need_state(gen=1, rf_gate=1, rf_legacy=junk)
            self.assertEqual(rfgate.mode(st), "need", junk)

    def test_a_broken_gen_is_a_new_node_not_an_unmigrated_one(self):
        for gen in ("1", "2", True, 2.0, 0, None):
            st = self.need_state(gen=gen)                         # no rf_gate, ru_direct chosen: was «legacy» before
            self.assertEqual(rfgate.mode(st), "need", gen)
            self.assertEqual(rapply.rd(st), [], gen)
        st = self.need_state()
        del st["gen"]                                             # a dict past ``load`` without the field
        self.assertEqual(rfgate.mode(st), "need")
        self.assertEqual(rapply.rd(st), [])


class KernelPathTest(GateCase):
    def test_gw_rd_dnsmasq_and_the_table(self):
        st = self.need_state()
        self.assertEqual(rapply.rd(st), [])
        self.assertFalse(rapply.kernel_clients(st))
        self.assertFalse([ln for ln in rapply.dnsmasq_lines(st) if ln.startswith("nftset=")])
        nft.apply(st, self.w.runner)
        self.assertEqual(self.w.nft.elements("gw_rd"), [])
        self.assertNotIn("elements = { %s }" % ", ".join(MAMA), [ln.strip() for ln in nft.render_full(st).splitlines()])
        st = self.give_consent()
        self.assertEqual(rapply.rd(st), MAMA)
        self.assertTrue(rapply.kernel_clients(st))
        self.assertTrue([ln for ln in rapply.dnsmasq_lines(st) if ln.startswith("nftset=")])
        self.assertTrue(net.update_map(self.w.runner, st))           # the collector brings gw_rd
        self.assertEqual(sorted(self.w.nft.elements("gw_rd")), MAMA)

    def test_fallback_block_of_a_group_closes_russia_without_consent(self):
        """gw_ruonly is built from rd() (SPEC-todo-coordinator, REV-s1-t4): the last step «block» of a group keeps
        Russia open only for a ru_direct that applies."""
        tg = {"10.77.1.10": {"mark": exits.MARK_BLOCK, "step": "last_block", "exit": None},
              "10.77.1.11": {"mark": 0x102, "step": "exit", "exit": None}}
        for consent, want in ((False, []), (True, ["10.77.1.10"])):
            st = self.give_consent() if consent else self.need_state()
            with mock.patch.object(exits, "targets", lambda *a, **kw: dict(tg)):
                p = net.plan(st, self.w.runner, ports=False)
            self.assertEqual(p["ruonly"], want, consent)
            self.assertEqual(p["rd"], MAMA if consent else [], consent)
            self.assertEqual(net.live_ips(st, p["elems"], p["ruonly"]),
                             ["10.77.1.10", "10.77.1.11"] if consent else ["10.77.1.11"], consent)

    def test_ikev2_hook(self):
        st = self.need_state()
        register_dummies(("ikev2", {"ifaces": [], "dynamic": True}))
        st["gateway"]["inbounds"]["ikev2"] = {"enabled": True, "subnet": "10.77.8.0/24"}
        st["gateway"]["devices"][0]["access"].append("ikev2")
        state.save(st)
        st = state.load()
        nft.apply(st, self.w.runner)
        self.assertFalse(rapply.device_rd(st, self.dev(st, D1)))
        tgt = {"mark": exits.MARK_BLOCK, "exit": None, "route": {"via": "pool"}, "group": "main", "step": "last_block"}
        with mock.patch.object(exits, "target", lambda *a, **kw: dict(tgt)):
            res = hooks.hook("ikev2", "up", "telefon-0001", "10.77.8.10", self.w.runner)
        self.assertEqual((res["ru_direct"], res["ru_only"]), (False, False))
        for s in ("gw_rd", "gw_ruonly", "gw_live"):
            self.assertNotIn("10.77.8.10", self.w.nft.elements(s), s)
        hooks.hook("ikev2", "down", "telefon-0001", "10.77.8.10", self.w.runner)
        self.give_consent()
        with mock.patch.object(exits, "target", lambda *a, **kw: dict(tgt)):
            res = hooks.hook("ikev2", "up", "telefon-0001", "10.77.8.10", self.w.runner)
        self.assertEqual((res["ru_direct"], res["ru_only"]), (True, True))
        self.assertIn("10.77.8.10", self.w.nft.elements("gw_rd"))

    def test_ikev2_hook_without_the_state_lock_never_puts_the_address_into_gw_rd(self):
        # SEC-s1-r2 П4: the lock busy longer than LOCK_WAIT — the state read may be older than a change that took the
        # choice or the consent away: the address waits for the collector, never «directly» by a stale state
        st = self.need_state()
        register_dummies(("ikev2", {"ifaces": [], "dynamic": True}))
        st["gateway"]["inbounds"]["ikev2"] = {"enabled": True, "subnet": "10.77.8.0/24"}
        st["gateway"]["devices"][0]["access"].append("ikev2")
        state.save(consented(st))
        st = state.load()
        nft.apply(st, self.w.runner)

        @contextlib.contextmanager
        def busy():
            yield False

        with mock.patch.object(hooks, "_maybe_state_lock", busy):
            res = hooks.hook("ikev2", "up", "telefon-0001", "10.77.8.10", self.w.runner)
        self.assertEqual((res["ru_direct"], res["ru_only"], res.get("rd_wait")), (False, False, True))
        self.assertNotIn("10.77.8.10", self.w.nft.elements("gw_rd"))
        self.assertTrue(net.update_map(self.w.runner, state.load()))               # the collector brings it
        self.assertIn("10.77.8.10", self.w.nft.elements("gw_rd"))

    def test_device_rd_never_for_a_route_block_by_choice(self):
        """As rd(): a device blocked by choice is not in gw_rd from the hook either (the collector would take it out
        again — update_map compares gw_rd with rd())."""
        self.need_state()
        st = self.give_consent()
        d1 = self.dev(st, D1)
        self.assertTrue(rapply.device_rd(st, d1))
        d1["route"] = {"via": "block"}
        self.assertFalse(rapply.device_rd(st, d1))
        self.assertNotIn("10.77.1.10", rapply.rd(st))


class XrayPathTest(GateCase):
    def test_socks5_rules_and_the_shared_lists(self):
        st = self.need_state(socks=True, switches=False)
        sec = secrets.load()
        ads = list(xray._adapters(st))
        self.assertFalse(rapply.xray_wanted(st, sec, ads))
        rapply.sync_assets(consented(json.loads(json.dumps(st))), sec, ads)       # the files are there anyway
        pool = exits.pool(self.w.runner)
        self.assertEqual(self.ru_rules(xray.render(st, sec, pool)), [])
        target = lambda route, d: {"outboundTag": "x"}                          # noqa: E731
        target.shared_lists = True
        self.assertEqual(rapply.xray_rules(st, sec, target, ads, chained=("socks-in",)), [])     # no ls- rules
        target.shared_lists = False
        self.assertEqual(rapply.user_chain(st, target, ["vless-in"], "telefon-0001", self.dev(st, D1)), [])
        st = self.give_consent()
        self.assertTrue(rapply.xray_wanted(st, sec, ads))
        self.assertTrue(self.ru_rules(xray.render(st, sec, pool)))
        target.shared_lists = True
        ls = rapply.xray_rules(st, sec, target, ads, chained=("socks-in",))
        self.assertTrue(ls and all(r["ruleTag"].startswith("ls-") for r in ls))
        target.shared_lists = False
        self.assertTrue(rapply.user_chain(st, target, ["vless-in"], "telefon-0001", self.dev(st, D1)))


class SurfacesTest(GateCase):
    def test_scheme_shows_what_applies(self):
        st = self.need_state()
        d1 = self.dev(st, D1)
        self.assertEqual(scheme._applied_profile(st, d1, True), scheme.PROFILE_ALL)
        st = self.give_consent()
        self.assertEqual(scheme._applied_profile(st, self.dev(st, D1), True), scheme.PROFILE_RU)

    def test_trace_says_through_the_exit_and_why(self):
        from kaskad.gw import dns
        st = self.need_state(host="203.0.113.7")
        rapply._save_load_state({"ver": net.ru_wanted(), "ok": True, "at": "x", "seconds": 0, "elements": 3,
                                 "fails": 0})
        dns._save_features({"version": (2, 90), "nftset": True})
        trace._dns_cache.clear()
        res = trace.trace(st, D1, "mail.yandex.ru", resolver=fake_dns(DNS))
        self.assertNotEqual(res["verdict"], "direct")
        self.assertIn(tr("gw_trace.who.no_consent"), "\n".join(trace.lines(res)))
        res = trace.trace(self.give_consent(), D1, "mail.yandex.ru", resolver=fake_dns(DNS))
        self.assertEqual(res["verdict"], "direct")

    def test_trace_at_the_fallback_block_of_a_group_without_consent(self):
        """B6 (REV-s1-r2 2): the exits of the group unfit, its last step a block — «Россия напрямую» goes directly only
        when it APPLIES (the consent); without it the trace says «блок», as nft (gw_ruonly) and xray do."""
        from kaskad.gw import dns
        st = self.need_state(host="203.0.113.7")
        rapply._save_load_state({"ver": net.ru_wanted(), "ok": True, "at": "x", "seconds": 0, "elements": 3,
                                 "fails": 0})
        dns._save_features({"version": (2, 90), "nftset": True})
        fallback = {"mark": "block", "exits": [], "balancer": False, "why": "выходы группы не работают",
                    "group_label": "Основная", "step": "last_block"}
        with mock.patch.object(trace, "_route_now", lambda *a, **kw: dict(fallback)):
            trace._dns_cache.clear()
            res = trace.trace(st, D1, "mail.yandex.ru", resolver=fake_dns(DNS))
            self.assertEqual(res["verdict"], "block")
            trace._dns_cache.clear()
            res = trace.trace(self.give_consent(), D1, "mail.yandex.ru", resolver=fake_dns(DNS))
            self.assertEqual(res["verdict"], "direct")


class UpdatedNodeTest(GateCase):
    """An updated node (``gen 1``): before the mark — legacy, renders exactly as without the gate; after the mark
    without ``rf_legacy`` (it chose all_exit then) a hand-edited ru_direct does not apply."""

    def renders(self, st):
        sec = secrets.load()
        return (nft.render_full(st), rapply.dnsmasq_lines(st),
                json.dumps(xray.render(st, sec, exits.pool(self.w.runner)), sort_keys=True))

    def test_legacy_renders_as_without_the_gate(self):
        st = self.need_state(gen=1, socks=True)
        self.assertEqual(rfgate.mode(st), "legacy")
        self.assertEqual(rapply.rd(st), MAMA)
        got = self.renders(st)
        with mock.patch.object(rfgate, "allowed", lambda st: True):
            self.assertEqual(got, self.renders(st))
        marked = self.need_state(gen=1, socks=True, rf_gate=1, rf_legacy={"at": "x", "from": "gen1", "by": "t"})
        self.assertEqual(rfgate.mode(marked), "legacy")
        self.assertEqual(self.renders(marked), got)

    def test_legacy_renders_as_the_code_before_r2(self):
        """B6 (REV-s1-r2 3): the mode legacy renders byte for byte as the code BEFORE R2 (f71e70a) did on this very
        state — tests/fixtures/nft/rf_legacy_gen1.json was made by that code (nft, dnsmasq, xray with SOCKS5 and the
        lists), not by mocking ``allowed``. A change of the rendering of an updated node shows here first."""
        st = self.need_state(gen=1, socks=True)
        sec = secrets.load()
        rapply.sync_assets(st, sec, list(xray._adapters(st)))
        doc = {"nft": nft.render_full(st), "dnsmasq": rapply.dnsmasq_lines(st),
               "xray": json.loads(json.dumps(xray.render(st, sec, exits.pool(None)), sort_keys=True))}
        strip_awg_keepalive(doc["xray"])                 # Н3: the keepalive of the AWG outbounds — not of the rules
        got = json.dumps(doc, sort_keys=True, ensure_ascii=False, indent=1).replace(self.root, "<ROOT>")
        self.assertIn("ext:", got)                                               # the lists of Russia are drawn
        self.golden("rf_legacy_gen1.json", got)

    def test_marked_without_legacy_is_need(self):
        st = self.need_state(gen=1, rf_gate=1)
        self.assertEqual(rfgate.mode(st), "need")
        self.assertEqual(rapply.rd(st), [])
        self.assertEqual(rapply.profile(st, self.dev(st, D1))["why"], "no_consent")


if __name__ == "__main__":
    import unittest
    unittest.main()
