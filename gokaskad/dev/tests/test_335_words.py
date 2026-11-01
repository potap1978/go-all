"""3.3.5 №1–2: exit-add / exit-del tell the warnings of the touched keys only (the others — one line), a subscription
refresh too; the keys of a subscription left out because the pool has their server by another line are said
(RU and EN), in the result and in the row of the subscription (BACKLOG-3.3.5 №1, №2; stand T-334 §13в, около стр. 101)."""

from unittest import mock

from kaskad.gw import exits, exitsubs, ops
from kaskad.i18n import use_lang
from tests.fakes_gw import VLESS_A, VLESS_B, write_pool
from tests.test_335_rename import b64, key
from tests.test_gw_exits import conf_of, vpn_of
from tests.test_gw_exitsubs import GA_URL, IP_URL, SubsCase

WARN = "⚠ Ключ #{n}: …\n@K3:egress_warn:vless_key:{n}:insecure\n"


class ScriptWarningsTest(SubsCase):
    LINES = ["⚠ Ключ #1: …", "@K3:egress_warn:vless_key:1:insecure", "⚠ Ключ #3: …",
             "@K3:egress_warn:awg_key:3:need31", "@K3:egress_warn:skipped:insecure:2", "@K3:egress_warn:awg_core_missing"]

    def test_only_the_touched_keys_and_one_line_for_the_others(self):
        with use_lang("ru"):
            every = ops._script_warnings(self.LINES)
            self.assertEqual(len(every), 4)
            mine = ops._script_warnings(self.LINES, {3})
            self.assertTrue(mine[0].startswith("Ключ #3: AmneziaWG 3.1"))
            self.assertNotIn("Ключ #1", " ".join(mine))
            self.assertIn("Пропущено ключей: 2", " ".join(mine))          # about the keys being added: kept
            self.assertIn("AWG-ядро недоступно", " ".join(mine))           # about the whole node: kept
            self.assertEqual(mine[-1], "Ещё в пуле ключей с предупреждениями: 1 — подробности в списке выходов.")
            none = ops._script_warnings(self.LINES, set())
            self.assertEqual(none[-1], "Ещё в пуле ключей с предупреждениями: 2 — подробности в списке выходов.")
            self.assertEqual(ops._script_warnings(["@K3:egress_warn:awg_core_missing"], set()),
                             ["AWG-ядро недоступно — поднимаю только VLESS-ключи (AWG пропущены)."])
        with use_lang("en"):
            self.assertEqual(ops._script_warnings(self.LINES, set())[-1],
                             "Other pool keys with warnings: 2 — see the list of exits.")

    def add_with(self, out):
        def fake(text):
            keys = [l.strip() for l in text.splitlines() if l.strip()]
            pool = self.pool()
            self.w.script._write(pool + [k for k in keys if k not in pool])
            return 0, out, ""
        return mock.patch.object(self.w.script, "egress_add", fake)

    def test_exit_add_tells_the_new_key_not_the_pool(self):
        write_pool([VLESS_A])                                            # key #1 is a pending one
        with use_lang("ru"), self.add_with(WARN.format(n=1)):
            doc = ops.exit_add(self.w.runner, VLESS_B)
        self.assertEqual(doc["warnings"], ["Ещё в пуле ключей с предупреждениями: 1 — подробности в списке выходов."])
        with use_lang("ru"), self.add_with(WARN.format(n=1) + WARN.format(n=3)):
            doc = ops.exit_add(self.w.runner, key("новый"))
        self.assertEqual(len(doc["warnings"]), 2)
        self.assertTrue(doc["warnings"][0].startswith("Ключ #3: VLESS без проверки сертификата"))

    def test_exit_del_of_another_key_is_one_line(self):
        write_pool([VLESS_A, VLESS_B])
        self.w.script.del_rebuild = (0, WARN.format(n=1))
        with use_lang("ru"):
            doc = ops.exit_del(self.w.runner, exits.exit_id(VLESS_B))
        self.assertEqual(doc["warnings"], ["Ещё в пуле ключей с предупреждениями: 1 — подробности в списке выходов."])

    def test_refresh_tells_its_new_keys_only(self):
        write_pool([VLESS_A])
        real = self.w.script.egress_replace

        def replace(want, text):
            rc, out, err = real(want, text)
            return rc, out + WARN.format(n=1) + WARN.format(n=2), err
        with use_lang("ru"), mock.patch.object(self.w.script, "egress_replace", replace):
            res = self.add(IP_URL, VLESS_B)
        w = res["result"]["warnings"]
        self.assertTrue(w[0].startswith("Ключ #2:"), w)
        self.assertEqual(w[1:], ["Ещё в пуле ключей с предупреждениями: 1 — подробности в списке выходов."])


class DupsTest(SubsCase):
    def test_goamnezia_key_of_a_server_added_by_hand_is_said(self):
        """T-334 §13в: single keys of the server first, then its subscription — «added 1 (keys 2)» said why."""
        a, b = conf_of("2.0", 3), conf_of("3.1", 4)
        parser = exits.load_parser()
        hand = parser.extract(vpn_of(a, "руками"))[0]
        write_pool([hand])
        with use_lang("ru"):
            res = self.add(GA_URL, "\n".join([vpn_of(a, "Аня · A"), vpn_of(b, "Аня · B")]))
        r = res["result"]
        self.assertEqual((r["added"], r["count"], r["dups"]), (1, 2, 1))
        self.assertIn("Пропущено дублей: 1 — этот ключ уже есть как %s («руками»), записан иначе."    # REV-335-2 Н4
                      % exits.exit_id(hand), r["text"])
        self.assertIn("дублей 1", self.journal())
        with use_lang("ru"):
            row = exitsubs.listing()[0]
        self.assertIn("; пропущено дублей: 1 (ключ уже в пуле)", row["text"])
        self.assertEqual(row["keys"], 2)                                 # both of its keys are in the pool
        with use_lang("en"):
            r = self.refresh()["results"][0]
            row = exitsubs.listing()[0]
        self.assertIn("Duplicates skipped: 1 — this key is already there as %s (“руками”), written differently."
                      % exits.exit_id(hand), r["text"])
        self.assertIn("; duplicates skipped: 1 (the key is already in the pool)", row["text"])

    def test_vless_of_the_same_account_by_hand(self):
        write_pool([key("руками")])
        with use_lang("ru"):
            r = self.add(IP_URL, b64([key("10GB left"), VLESS_B]))["result"]
        self.assertEqual((r["added"], r["dups"]), (1, 1))
        self.assertIn("Пропущено дублей: 1", r["text"])

    def test_no_dups_no_words(self):
        with use_lang("ru"):
            r = self.add(IP_URL, b64([VLESS_A, VLESS_B]))["result"]
            row = exitsubs.listing()[0]
        self.assertEqual(r["dups"], 0)
        self.assertNotIn("дубл", r["text"] + row["text"])
