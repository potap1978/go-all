"""T8 круг 2 (REV-s1-t8-a З-8): «Оставить как раньше» на плашке «Появились группы выходов» — POST /api/gw/policy/hint-seen
пишет только ``hint_seen``: без версии (rev не растёт), без применения шлюза (ни одной команды узла), повтор — то же."""
from tests.panel.test_panel_policy import PolicyCase


class HintSeenTest(PolicyCase):
    def test_only_the_flag_without_an_apply(self):
        before = self.policy()
        self.assertFalse(before["policy"].get("hint_seen"))
        rev0, calls0, scripts0 = before["rev"], len(self.w.runner.calls), len(self.w.script.calls or [])
        res = self.ok("POST", "/api/gw/policy/hint-seen", {})
        self.assertTrue(res["hint_seen"])
        after = self.policy()
        self.assertTrue(after["policy"]["hint_seen"])
        self.assertEqual(after["rev"], rev0)                               # вне отпечатка политики (gw/rev.py)
        self.assertEqual(after["policy"]["groups"], before["policy"]["groups"])
        self.assertEqual(len(self.w.runner.calls), calls0)                 # шлюз не применялся: ни nft, ни systemctl
        self.assertEqual(len(self.w.script.calls or []), scripts0)
        self.ok("POST", "/api/gw/policy/hint-seen", {})                    # двойной щелчок — то же, без ошибки
        self.assertEqual(self.policy()["rev"], rev0)
        self.assertEqual(len(self.w.runner.calls), calls0)


class NewNodeBeforeEnableTest(PolicyCase):
    def test_no_banner_and_no_write_on_a_new_node_before_enable(self):
        """REV-s1-t8m П-2: новый узел до первого ``gw enable`` (политики нет, читатели видят «Как раньше») — плашки нет
        (``hint_seen`` в чтении), а «Оставить как раньше» из CLI/MCP ничего не пишет: «Стандарт» даст первый enable."""
        from kaskad import state
        from kaskad.gw import policy as P
        with state.transaction() as st:
            st.setdefault("gateway", {}).pop("exit_policy", None)
            st["gen"] = 2                                                    # узел родился на коде этапа 1
        st = state.load()
        self.assertIsNone(P.stored(st))
        self.assertTrue(state.born_new(st))
        view = self.policy()
        self.assertTrue(view["policy"]["hint_seen"])
        self.assertEqual(view["policy"]["groups"][0]["preset"], "compat")
        res = self.ok("POST", "/api/gw/policy/hint-seen", {})
        self.assertTrue(res["hint_seen"])
        self.assertIsNone(P.stored(state.load()))                           # ни «Стандарта», ни флага не записано
        self.assertEqual(self.policy()["rev"], view["rev"])

