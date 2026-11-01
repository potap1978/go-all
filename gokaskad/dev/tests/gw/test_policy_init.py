"""T7 — the first policy of a node (SPEC-stage1-exits §1.3, §3.1; ``policy.ensure_initial`` in ``gw/ops.enable``): a
node born on the code of stage 1 (``gen 2``) — «Стандарт», an updated one (``gen 1``) — «Как раньше» (compat); a policy
there stays at the next enable; the write counts ``rev`` (+ 1); nothing else writes it — not the collector of a node
that never set it up, not a refresh of the units (the restart of the bot, the update)."""

from kaskad import state
from kaskad.gw import collect, ops, policy as P, rev as R
from tests.fakes_gw import VLESS_A, install_parser, write_pool
from tests.fakes_nft import F443, save_state
from tests.gwcase import GwCase
from tests.helpers import legacy_state


class EnableTest(GwCase):
    def node(self, gen):
        self.ads = self.dummies()
        save_state([F443])
        st = state.load()
        st["gen"] = gen
        state.save(st)
        install_parser()
        write_pool([VLESS_A])

    def test_a_new_node_gets_standard_and_rev_plus_one(self):
        self.node(2)
        st0 = state.load()
        self.assertIsNone(P.stored(st0))
        rev0 = R.rev_of(st0)
        ops.enable(self.w.runner)
        st = state.load()
        self.assertEqual(P.stored(st)["groups"][0]["preset"], "standard")
        self.assertEqual(P.stored(st)["groups"][0]["name"], "")
        self.assertEqual(R.rev_of(st), rev0 + 1)
        self.assertEqual(R.current(st)[0], rev0 + 1)                    # counted: the next read adds nothing

    def test_an_updated_node_gets_compat_and_a_second_enable_keeps_it(self):
        self.node(1)
        ops.enable(self.w.runner)
        st = state.load()
        self.assertEqual(P.stored(st)["groups"][0]["preset"], "compat")
        doc = P.load(st)
        doc["groups"][0]["fail"] = "direct"
        P.save(st, doc)
        state.save(st)
        before = P.stored(state.load())
        ops.disable(self.w.runner)
        ops.enable(self.w.runner)
        self.assertEqual(P.stored(state.load()), before)

    def test_nobody_else_writes_it(self):
        self.node(2)
        collect.tick(self.w.runner)                                     # the gateway never enabled
        self.assertIsNone(P.stored(state.load()))
        from kaskad import unit_refresh
        try:
            unit_refresh.refresh(self.w.runner)
        except Exception:                                               # noqa: BLE001 — only what it writes matters
            pass
        self.assertIsNone(P.stored(state.load()))

    def test_legacy_fixture_is_gen_1(self):
        self.assertFalse(state.born_new(legacy_state()))


class FirstWriteTest(GwCase):
    def test_a_write_before_enable_stores_the_policy_the_node_would_get(self):
        """A write of policy_ops on a node with no policy (the gateway never enabled): «Стандарт» on a new node, never
        the «compat» of the read (``policy_ops._base_w`` — as ``ensure_initial``)."""
        from kaskad.gw import policy_ops
        save_state([F443])
        install_parser()
        write_pool([VLESS_A])
        self.assertTrue(state.born_new(state.load()))
        policy_ops.group_add(self.w.runner, "Кино")
        doc = P.stored(state.load())
        self.assertEqual([g["preset"] for g in doc["groups"]], ["standard", "standard"])
