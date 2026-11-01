"""F9 (3.3.7.15, Ш6, решение 1): что меню SSH и бот берут у ядра — ``lines`` «Что изменится» в ``gw inbound set --dry
--json``, ``last.text`` в ``gw inbound show``, строка ``note`` отдельно от ``text`` при ожидании F7; бот — значки и
короткие слова пушей ``settings_reverted``, ``settings_revert_failed`` (F7) и ``ip6_prefix_changed`` (F10), всегда
(без переключателя). Само меню — tools/k3_install_test.sh, раздел 20а."""

import json
import os
from unittest import mock

from kaskad import i18n, state
from kaskad.bot import monitor
from kaskad.errors import ConflictError
from kaskad.gw import model, ops, pending
from tests.bot.test_monitor import S1PushCase
from tests.test_gw_ops import CliGwCase
from tests.test_r15_settings import SettingsWorld, tearDownModule  # noqa: F401 — the trees of old versions

KINDS = ("settings_reverted", "settings_revert_failed", "ip6_prefix_changed")


class MenuFromCoreTest(SettingsWorld, CliGwCase):
    def test_dry_json_carries_the_lines_of_the_menu(self):
        self.world()
        rc, doc, _ = self.run_json("gw", "inbound", "set", "wg", "--port", "51999", "--dry")
        self.assertEqual(rc, 0)
        self.assertIn("что изменится — порт 51820 → 51999", doc["lines"][0])
        self.assertTrue(any("Телефон (Мама)" in ln for ln in doc["lines"][1:]))
        self.assertTrue(all(ln.startswith("  ") for ln in doc["lines"][1:]))
        self.assertEqual(doc["changed"]["port"]["new"], 51999)
        self.assertIs(doc["via_xray"], False)
        rc, doc, _ = self.run_json("gw", "inbound", "set", "wg", "--port", "51820", "--dry")
        self.assertEqual(doc["changed"], {})
        self.assertEqual(len(doc["lines"]), 1)
        self.assertIn("менять нечего", doc["lines"][0])

    def test_show_gives_the_last_application_in_words(self):
        self.world()
        rc, doc, _ = self.run_json("gw", "inbound", "show", "wg")
        self.assertIsNone(doc["last"])
        ops.inbound_settings(self.w.runner, "wg", port=51999, mtu=1300, yes=True)
        rc, doc, _ = self.run_json("gw", "inbound", "show", "wg")
        self.assertEqual(doc["last"]["text"], "порт 51820 → 51999, MTU 1280 → 1300")
        self.assertEqual([d["name"] for d in doc["reissue"]["hard"]], ["Телефон (Мама)"])

    def test_the_note_of_a_wait_is_its_own_key(self):
        self.world()

        def guard(runner, st_old, plan, scope, who=None, safe=False, apply=None):
            out = apply()
            out["pending"] = {"token": "0" * 16, "deadline": 1, "scope": scope, "left": 120}
            return out
        with mock.patch.object(pending, "guard", guard):
            res = ops.inbound_settings(self.w.runner, "wg", port=51999, yes=True)
        self.assertEqual(res["note"], "Подтвердите в панели или командой kaskadctl gw pending confirm за 2 мин — "
                                      "иначе настройки вернутся.")
        self.assertTrue(res["text"].endswith(" " + res["note"]))
        self.assertIn("не подключатся", res["text"])
        res = ops.inbound_settings(self.w.runner, "wg", port=51998, yes=True)          # no wait — no note
        self.assertNotIn("note", res)

    def test_the_plan_says_in_advance_that_there_is_nothing_to_undo_with(self):
        # CONTRACT-pending-3315, добавка 3: нечем откатить — guard (полоса Г) откажет 409 pending_no_undo; план говорит
        # причину до «Применить», сам отказ — только в guard
        self.world()
        want = ": безопасное применение недоступно — прежние настройки не записаны"

        def words(**kw):
            return " ".join(ops.inbound_settings(self.w.runner, "wg", port=51999, dry=True, **kw)["warnings"])
        self.assertNotIn(want, words(safe=True))                       # the record can be written: a wait is possible
        seen = []

        def guard(runner, st_old, plan, scope, who=None, safe=False, apply=None):
            seen.append((plan["id"], safe, any(want in w for w in plan["warnings"])))
            raise ConflictError("nothing to undo with", data={"why": "pending_no_undo"})
        with mock.patch.object(ops, "_last_can_write", lambda: False):
            self.assertNotIn(want, words())                            # neither safe nor a hit: applied without a wait
            self.assertIn(want, words(safe=True))
            with mock.patch.object(pending, "admin_hit", lambda *a, **k: "pool"):
                self.assertIn(want, words())                           # the administrator would be cut
            rc, doc, _ = self.run_json("gw", "inbound", "set", "wg", "--port", "51999", "--dry", "--safe")
            self.assertEqual(rc, 0)
            self.assertTrue(any(want in ln for ln in doc["lines"]), doc["lines"])   # what the SSH menu prints
            with mock.patch.object(pending, "guard", guard):
                with self.assertRaises(ConflictError):
                    ops.inbound_settings(self.w.runner, "wg", port=51999, yes=True, safe=True)
        self.assertEqual(seen, [(None, True, True)])                   # the refusal — guard's, with the words seen
        self.assertEqual(model.inbound(state.load(), "wg")["port"], 51820)
        self.assertIn("safe apply isn’t available", i18n.catalog("en")["gw_ops.settings.no_undo"])


class BotPushTest(S1PushCase):
    def event(self, kind, ts, text, **args):
        """An event as the feed holds it: the key of lane Г (``gw_pending.event.*``) or Б, its words in ``text``."""
        return {"id": int(ts * 1000), "ts": ts, "kind": kind, "text": text, "key": "gw_pending.event.x." + kind,
                "args": args}

    def test_always_sent_with_their_icons_even_with_every_switch_off(self):
        self.switches(**dict((k, False) for k in monitor._push_default() if k != "coalesce"))   # one message
        t = self.now
        evs = [self.event("settings_reverted", t, "Настройки WireGuard возвращены: подтверждения за 2 мин не было",
                          scope="wg", why="no_confirm"),
               self.event("settings_revert_failed", t, "Настройки WireGuard вернуть не удалось: порт 51820 занят",
                          scope="wg", why="port_busy", port=51820),
               self.event("ip6_prefix_changed", t, "Префикс IPv6 сменился: 2001:db8:1::/64 → 2001:db8:2::/64")]
        self.live(evs)
        self.tick()
        want = "🛡 <b>Шлюз</b>\n" + "\n".join("%s %s" % (monitor.GW_ICONS[e["kind"]], monitor.h(monitor.event_text(e)))
                                             for e in evs)
        self.assertEqual(self.gw_texts(), [want])
        self.assertTrue(want.startswith("🛡 <b>Шлюз</b>\n↩️ ") and "\n❗ " in want and "\n🌐 " in want)
        self.assertIn("порт 51820 занят", want)

    def test_kinds_short_words_and_no_switch(self):
        for k in KINDS:
            self.assertIn(k, monitor.GW_KINDS)
            self.assertNotEqual(monitor.gw_icon({"kind": k}), "ℹ️")
            self.assertTrue(monitor.push_on(k, {}))
            self.assertTrue(monitor.push_on(k, {k: False}))               # no switch of its own: always
            self.assertNotIn(k, monitor._push_default())
            for lang in ("ru", "en"):
                self.assertIn("bot.mon.gw_" + k, i18n.catalog(lang))
        held = [{"kind": "settings_reverted"}, {"kind": "settings_reverted"}, {"kind": "ip6_prefix_changed"}]
        self.assertEqual(monitor.meanwhile_text(held),
                         "За это время: настройки протокола возвращены — 2, префикс IPv6 сменился — 1")

    def test_mcp_and_menu_do_not_get_new_tools(self):
        # решение 1 и 13: в меню — только прежний «Сменить порт» через новое действие; новых инструментов MCP нет
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "..", "install.sh"), encoding="utf-8") as f:
            text = f.read()
        menu = text[text.index("k3_gw_proto_port() {"):text.index("# «Вернуть прежние настройки» (F3, решение 1)")]
        self.assertNotIn("gw inbound port", menu)
        self.assertNotIn("--mtu", menu)
        self.assertNotIn("--tls", menu)
        # MCP (REV-3315-a2 П-3): «Настройки протокола» и ожидание — не инструменты, а скрытые команды с причиной;
        # порт из MCP — прежний inbound_port
        with open(os.path.join(here, "..", "lib", "kaskad", "mcp", "actions.json"), encoding="utf-8") as f:
            spec = json.load(f)
        cmds = [" ".join(c["cmd"]) for a in spec["actions"].values()
                for c in ([a] if "cmd" in a else list((a.get("by_action") or {}).values()))]
        for c in ("gw inbound set", "gw inbound undo", "gw inbound show", "gw pending"):
            self.assertFalse([x for x in cmds if x.startswith(c)], c)
        for c in ("gw inbound set", "gw inbound undo", "gw inbound show"):
            self.assertIn(c, spec["hidden"])
        flags = set(p.get("flag") for a in spec["actions"].values() for p in (a.get("params") or {}).values()
                    if isinstance(p, dict))
        self.assertFalse(flags & {"--tls", "--safe", "--mtu"}, flags & {"--tls", "--safe", "--mtu"})
        self.assertEqual(spec["actions"]["inbound_port"]["cmd"], ["gw", "inbound", "port"])
