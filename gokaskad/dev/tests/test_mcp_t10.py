"""T10 — the MCP tools of the groups and the exit policy (SPEC-stage1-exits §7.8): their command lines, ``group:<gid>``
in the routes, ``dry`` and ``rev``, the destructive ones (``confirm`` and the client's question — destructiveHint), no
tool that sets the policy as a whole or creates a group (plans F and G)."""

from tests import test_mcp as TM
from tests.test_mcp import call, req


class T10ToolsTest(TM.McpCase):
    def test_argv_of_the_new_tools(self):
        s = self.run_session([
            call(1, "exit_groups_list"),
            call(2, "exit_policy_get", {"group": "g-1a2b3c"}),
            call(3, "exit_standby", {"id": "e-0123456789", "dry": True, "rev": 7}),
            call(4, "exit_restore", {"id": "e-0123456789"}),
            call(5, "exit_maintenance", {"id": "e-0123456789"}),
            call(6, "exit_accept_country", {"id": "e-0123456789", "rev": 3}),
            call(7, "exit_move", {"id": "e-0123456789", "group": "g-1a2b3c", "tier": "t2"}),
            call(8, "group_flap_standby_set", {"group": "main", "on": "off", "dry": True}),
            call(9, "exit_quota_set", {"id": "e-0123456789", "gb": "12.5", "period": "day", "action": "standby",
                                       "warn_pct": 90, "reset_day": 3}),
            call(10, "exit_quota_clear", {"id": "e-0123456789"}),
            call(11, "exit_transitions", {"id": "e-0123456789", "days": 3, "limit": 20, "before": 1700000000}),
            call(12, "exit_speedtest", {"id": "e-0123456789"}),
            call(13, "tz_get"),
            call(14, "tz_set", {"name": "Europe/Moscow"}),
            call(15, "default_route_set", {"via": "group:g-1a2b3c", "fail": "block"}),
            call(16, "device_route", {"device": "d-1a2b3c4d", "via": "group:main"}),
            call(17, "exit_disable", {"id": "e-0123456789", "dry": True, "rev": 2}),
            call(18, "exit_add", {"key": "vless://x@198.51.100.7:443#k", "group": "g-1a2b3c"}),
        ])
        argvs = [c["argv"] for c in s.calls]
        self.assertEqual(argvs, [
            ["gw", "groups", "--json"],
            ["gw", "policy", "--group=g-1a2b3c", "--json"],
            ["gw", "exit", "e-0123456789", "--dry", "--rev=7", "--standby", "--json"],
            ["gw", "exit", "e-0123456789", "--restore", "--json"],
            ["gw", "exit", "e-0123456789", "--maintenance", "--json"],
            ["gw", "exit", "e-0123456789", "--rev=3", "--accept-country", "--json"],
            ["gw", "exit-move", "e-0123456789", "--group=g-1a2b3c", "--tier=t2", "--yes", "--json"],   # KT3-9
            ["gw", "group-flap-standby", "main", "off", "--dry", "--json"],
            ["gw", "exit", "e-0123456789", "--quota-gb=12.5", "--quota-period=day", "--quota-reset-day=3",
             "--quota-warn=90", "--quota-action=standby", "--json"],
            ["gw", "exit", "e-0123456789", "--no-quota", "--json"],
            ["gw", "exit-history", "e-0123456789", "--days=3", "--limit=20", "--before=1700000000", "--json"],
            ["gw", "exit-speedtest", "e-0123456789", "--json"],
            ["tz", "get", "--json"],
            ["tz", "set", "Europe/Moscow", "--json"],
            ["gw", "route", "group:g-1a2b3c", "--fail=block", "--json"],
            ["gw", "device", "route", "d-1a2b3c4d", "group:main", "--json"],
            ["gw", "exit", "e-0123456789", "--dry", "--rev=2", "--disable", "--json"],
            ["gw", "exit-add", "--group=g-1a2b3c", "--public-only", "--json"]])
        self.assertEqual(s.calls[-1]["stdin"].strip(), "vless://x@198.51.100.7:443#k")      # the key — stdin only
        self.assertNotIn("vless://", " ".join(argvs[-1]))

    def test_destructive_ones_need_confirm_of_their_target(self):
        s = self.run_session([
            call(1, "exit_policy_reset", {"group": "main", "preset": "standard"}),
            call(2, "exit_policy_reset", {"group": "main", "preset": "standard", "confirm": "exit_policy_reset"}),
            call(3, "exit_policy_reset", {"group": "main", "preset": "standard", "dry": True, "confirm": "main"}),
            call(4, "group_delete", {"group": "g-1a2b3c", "exits_to": "main", "reassign": "direct", "confirm": "main"}),
            call(5, "group_delete", {"group": "g-1a2b3c", "exits_to": "main", "reassign": "direct",
                                     "tg_proxy_to": "main", "new_exits_to": "main", "subs_to": "main", "rev": 4,
                                     "confirm": "g-1a2b3c"}),
            call(6, "exit_move_role", {"id": "e-0123456789", "group": "g-1a2b3c"}),
            call(7, "exit_move_role", {"id": "e-0123456789", "group": "g-1a2b3c", "dry": True, "confirm": "e-0123456789"}),
            call(8, "exit_delete", {"id": "e-0123456789", "dry": True, "confirm": "e-0123456789"}),
        ])
        for i in (1, 2, 4, 6):
            self.assertEqual(s.error(i)["code"], -32602, i)
        self.assertEqual([c["argv"] for c in s.calls], [
            ["gw", "policy-reset", "--group=main", "--preset=standard", "--dry", "--yes", "--json"],
            ["gw", "group-del", "g-1a2b3c", "--exits-to=main", "--reassign=direct", "--tg-proxy-to=main",
             "--new-exits-to=main", "--subs-to=main", "--rev=4", "--yes", "--json"],
            ["gw", "exit-move", "e-0123456789", "--group=g-1a2b3c", "--dry", "--confirm-role", "--yes", "--json"],
            ["gw", "exit-del", "e-0123456789", "--dry", "--json"]])

    def test_hints_and_bad_values(self):
        s = self.run_session([req(1, "tools/list")])
        tools = dict((t["name"], t) for t in s.result(1)["tools"])
        for name in ("exit_policy_reset", "exit_move_role", "group_delete"):
            self.assertTrue(tools[name]["annotations"]["destructiveHint"], name)        # the client asks the human
        for name in ("exit_standby", "exit_restore", "exit_maintenance", "exit_accept_country", "exit_move",
                     "group_flap_standby_set", "exit_quota_set", "exit_quota_clear", "exit_speedtest", "tz_set"):
            self.assertFalse(tools[name]["annotations"]["destructiveHint"], name)
            self.assertNotIn("readOnlyHint", tools[name]["annotations"], name)          # false — умолчание MCP
        for name in ("exit_groups_list", "exit_policy_get", "exit_transitions", "tz_get"):
            self.assertTrue(tools[name]["annotations"]["readOnlyHint"], name)
        for name in ("exit_policy_set", "group_add", "group_create", "group_rename", "exit_tier_set", "exit_rule_set"):
            self.assertNotIn(name, tools)                            # plans F and G: those are the panel's (§7.8)
        for name in ("exit_standby", "exit_move", "exit_policy_reset", "group_delete", "exit_quota_set"):
            self.assertIn("rev", tools[name]["inputSchema"]["properties"], name)
            self.assertIn("dry", tools[name]["inputSchema"]["properties"], name)
        bad = [("default_route_set", {"via": "group:g-xyz"}), ("default_route_set", {"via": "group:--force"}),
               ("exit_move", {"id": "e-0123456789", "group": "ru"}), ("exit_quota_set", {"id": "e-0123456789", "gb": "1e9"}),
               ("exit_quota_set", {"id": "e-0123456789", "gb": "5", "reset_day": 31}),
               ("group_delete", {"group": "g-1a2b3c", "reassign": "pool", "confirm": "g-1a2b3c"}),
               ("exit_standby", {"id": "e-0123456789", "rev": "7"}), ("tz_set", {"name": "-x"}),
               ("exit_policy_reset", {"group": "main", "preset": "custom", "confirm": "main"}),
               ("exit_move", {"id": "e-0123456789", "group": "main", "tier": "-t"})]
        s = self.run_session([call(i, t, a) for i, (t, a) in enumerate(bad)])
        for i, (t, a) in enumerate(bad):
            self.assertEqual(s.error(i)["code"], -32602, (t, a))
        self.assertEqual(s.calls, [])

    def test_round2_speed_force_and_tofu_need_the_human(self):
        """REV-s1-t10-sec №2: past the day's limit — a tool of its own with confirm; TOFU (SE §7.8): the fingerprint is
        read by exit_tofu_check, pinned by exit_add_tofu with confirm = pin_tofu; the key — stdin only."""
        pin = "ab" * 32
        s = self.run_session([
            call(1, "exit_speedtest", {"id": "e-0123456789", "force": True}),
            call(2, "exit_speedtest_force", {"id": "e-0123456789"}),
            call(3, "exit_speedtest_force", {"id": "e-0123456789", "confirm": "e-0000000000"}),
            call(4, "exit_speedtest_force", {"id": "e-0123456789", "confirm": "e-0123456789"}),
            call(5, "exit_tofu_check", {"key": "vless://x@198.51.100.7:443?security=tls&allowInsecure=1#k"}),
            call(6, "exit_add_tofu", {"key": "vless://x@198.51.100.7:443#k", "pin_tofu": pin}),
            call(7, "exit_add_tofu", {"key": "vless://x@198.51.100.7:443#k", "pin_tofu": pin, "confirm": "cd" * 32}),
            call(8, "exit_add_tofu", {"key": "vless://x@198.51.100.7:443#k", "pin_tofu": "zz", "confirm": "zz"}),
            call(9, "exit_add_tofu", {"key": "vless://x@198.51.100.7:443#k", "pin_tofu": pin, "group": "g-1a2b3c",
                                      "confirm": pin}),
        ])
        for i in (1, 2, 3, 6, 7, 8):
            self.assertEqual(s.error(i)["code"], -32602, i)
        self.assertEqual([c["argv"] for c in s.calls], [
            ["gw", "exit-speedtest", "e-0123456789", "--force", "--json"],
            ["gw", "exit-preview", "--tofu", "--tofu-by=mcp", "--json"],
            ["gw", "exit-add", "--pin-tofu=" + pin, "--group=g-1a2b3c", "--public-only", "--pin-by=mcp", "--json"]])
        self.assertIn("allowInsecure", s.calls[1]["stdin"])
        self.assertEqual(s.calls[2]["stdin"].strip(), "vless://x@198.51.100.7:443#k")
        self.assertNotIn("vless://", " ".join(" ".join(c["argv"]) for c in s.calls))
        s = self.run_session([req(1, "tools/list")])
        tools = dict((t["name"], t) for t in s.result(1)["tools"])
        for name in ("exit_speedtest_force", "exit_add_tofu"):
            self.assertTrue(tools[name]["annotations"]["destructiveHint"], name)
        self.assertNotIn("force", tools["exit_speedtest"]["inputSchema"]["properties"])
        a = tools["exit_tofu_check"]["annotations"]
        self.assertEqual(a, {"destructiveHint": False, "openWorldHint": True})     # readOnlyHint=false — умолчание MCP

    def test_round2_words_for_the_agent(self):
        """The refusal of the day's limit says --force to a person; to an agent — the tool exit_speedtest_force."""
        from kaskad.i18n import tr
        words = TM.TOOLS["exit_speedtest"]["error_words"]
        for lang, key in (("ru", "с --force"), ("en", "with --force")):
            self.assertIn(key, tr("gw_cli.speed.force_hint", lang))
            self.assertIn("exit_speedtest_force", words[key])
