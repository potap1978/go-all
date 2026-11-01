"""SPEC-tg-proxy (3.3.7.11): «Как бот ходит в Telegram» — a foreign group or «Напрямую» (``exit_policy.tg_proxy_group``
= ``"direct"``). The policy keeps it (load, validate), the lines of the bot's proxy are none and say «direct»
(tgproxy.indexes, ``gw tg-proxy-indexes``), the panel sets it (``POST /api/bot/proxy`` -> policy_ops.tg_proxy_set,
the rebuild of the bot's proxy — also when its unit is gone after «Напрямую» or an empty group), install.sh takes the
bot's xray off (code 4, the mark «direct») and puts it back; a node of 3.3.7.9 goes as it went (migration), and the
core of 3.3.7.9 reads «direct» as «main» (rollback)."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from kaskad import paths, state
from kaskad.gw import policy, policy_ops as pops, tgproxy
from tests.fakes_gw import write_pool
from tests.gw.test_tg_proxy import LINES, BashBotConfigTest, st_with
from tests.gwcase import GwCase
from tests.panel.test_panel_policy import PolicyCase

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OLD = "52506ae6"                # 3.3.7.9: the rollback target


class DirectPolicyTest(GwCase):
    def setUp(self):
        super().setUp()
        write_pool(LINES)
        from kaskad.gw import exits
        self.ids = [eid for eid, _ in exits.line_ids()]

    def groups(self):
        return [policy.default_group("main", exits=self.ids[:2]),
                policy.default_group("g-0000a1", name="Дом", exits=self.ids[2:])]

    def test_load_keeps_direct_a_gone_group_is_main(self):
        self.assertEqual(policy.load(st_with(self.groups(), "direct"))["tg_proxy_group"], "direct")
        self.assertEqual(policy.load(st_with(self.groups(), "g-ffffff"))["tg_proxy_group"], "main")
        self.assertEqual(policy.load(st_with(self.groups(), "g-0000a1"))["tg_proxy_group"], "g-0000a1")

    def test_validate_direct_only_for_the_bot(self):
        doc = policy.load(st_with(self.groups(), "direct"))
        errs, _ = policy.validate(doc)
        self.assertEqual([e for e in errs if "tg_proxy" in str(e)], [])
        doc["new_exits_group"] = "direct"                           # «direct» is no group for new exits
        errs, _ = policy.validate(doc)
        self.assertTrue(any("new_exits_group" in json.dumps(e) for e in errs), errs)

    def test_indexes_direct_and_without_a_policy_every_line(self):
        r = tgproxy.indexes(st_with(self.groups(), "direct"))
        self.assertEqual((r["indexes"], r["group"], r.get("direct"), r["policy"]), ([], "direct", True, True))
        r = tgproxy.indexes(st_with())                                # 3.3.3: no policy — every line, never direct
        self.assertEqual((r["indexes"], r.get("direct")), ([1, 2, 3, 4], None))

    def test_migration_3379_state_goes_as_it_went(self):
        """A node of 3.3.7.9: its exit_policy has «main» (or no field at all) — the same lines after the update."""
        for tg in ("main", None):
            st = st_with(self.groups(), "main")
            if tg is None:
                del st["gateway"]["exit_policy"]["tg_proxy_group"]
            r = tgproxy.indexes(st)
            self.assertEqual((r["indexes"], r["group"], r.get("direct")), ([1, 2], "main", None))

    def test_choices(self):
        st = st_with(self.groups(), "g-0000a1", {self.ids[3]: {"enabled": False}})
        c = tgproxy.choices(st)
        self.assertEqual(c["group"], "g-0000a1")
        self.assertEqual([(g["id"], g["label"], g["keys"]) for g in c["groups"]],
                         [("main", "Основная", 2), ("g-0000a1", "Дом", 1)])


@unittest.skipUnless(shutil.which("git"), "нет git")
class RollbackTest(unittest.TestCase):
    """«Откат»: the core of 3.3.7.9 (git archive of 52506ae6) reads a state written with «Напрямую» — «main», the
    lines of «Основная» (the bot goes as before the choice), no error."""

    def test_old_core_reads_direct_as_main(self):
        if subprocess.run(["git", "-C", ROOT, "cat-file", "-e", OLD + "^{commit}"], stderr=subprocess.DEVNULL).returncode:
            self.skipTest("нет коммита %s" % OLD)
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        arc = subprocess.run(["git", "-C", ROOT, "archive", OLD, "lib/kaskad"], stdout=subprocess.PIPE, check=True).stdout
        subprocess.run(["tar", "-x", "-C", d], input=arc, check=True)
        code = ("import json, sys\nfrom kaskad.gw import policy, tgproxy\nst = json.load(open(sys.argv[1]))\n"
                "print(json.dumps([policy.load(st)['tg_proxy_group'], tgproxy.indexes(st)['indexes'],"
                " policy.validate(policy.load(st))[0]]))\n")
        root = os.path.join(d, "root")
        os.makedirs(os.path.join(root, "etc", "kaskad", "egress"))
        with open(os.path.join(root, "etc", "kaskad", "egress", "upstream.txt"), "w") as f:
            f.write("\n".join(LINES) + "\n")
        from kaskad.gw import exits
        env = dict(os.environ, KASKAD_ROOT=root, PYTHONPATH=os.path.join(d, "lib"))
        ids = [exits.exit_id(ln) for ln in LINES]
        st = st_with([policy.default_group("main", exits=ids[:2]), policy.default_group("g-0000a1", name="Дом", exits=ids[2:])],
                     "direct")
        sp = os.path.join(d, "state.json")
        with open(sp, "w") as f:
            json.dump(st, f)
        r = subprocess.run([sys.executable, "-c", code, sp], env=env, cwd=d, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        self.assertEqual(json.loads(r.stdout.decode()), ["main", [1, 2], []])


class SetTest(PolicyCase):
    """policy_ops.tg_proxy_set through the panel (POST /api/bot/proxy): rev, gone, Russian, bad id; the rebuild."""

    def built(self):
        return [c[0] for c in self.w.runner.calls if "--egress-tg-rebuild" in c[0]]

    def bot_rev(self):
        return self.ok("GET", "/api/bot")["choice"]["rev"]

    def test_direct_and_back_rebuild_each_time(self):
        self.write("/etc/systemd/system/kaskad-egress.service", "[Unit]\n")
        doc = self.ok("POST", "/api/bot/proxy", {"group": "direct", "rev": self.bot_rev()})
        self.assertEqual(doc["group"], "direct")
        self.assertEqual(policy.stored(state.load())["tg_proxy_group"], "direct")
        self.assertEqual(len(self.built()), 1)
        self.assertEqual(self.ok("GET", "/api/bot")["choice"]["group"], "direct")
        # the unit is gone (install.sh took the bot's xray off), the mark says «direct»: back to «main» rebuilds too
        os.unlink(self.path("/etc/systemd/system/kaskad-egress.service"))
        self.write("/etc/kaskad/egress/.bot-off", "direct\n")
        self.write("/run/kaskad/tg-rebuild.at", "0\n")
        doc = self.ok("POST", "/api/bot/proxy", {"group": "main", "rev": self.bot_rev()})
        self.assertEqual(doc["group"], "main")
        self.assertEqual(len(self.built()), 2)
        self.assertIn("tg_proxy main", self.log())

    def test_refusals(self):
        rv = self.bot_rev()
        self.err(400, "POST", "/api/bot/proxy", {"group": "direct"})                     # no rev
        self.err(409, "POST", "/api/bot/proxy", {"group": "direct", "rev": rv + 5})
        self.err(404, "POST", "/api/bot/proxy", {"group": "g-abcdef", "rev": rv})
        self.err(400, "POST", "/api/bot/proxy", {"group": "../x", "rev": rv})
        self.err(400, "POST", "/api/bot/proxy", {"group": 5, "rev": rv})
        ru = self.add_group("Дом", role="ru")
        doc = self.err(400, "POST", "/api/bot/proxy", {"group": ru, "rev": self.bot_rev()})
        self.assertIn("зарубежной", doc["error"])
        self.assertNotEqual((policy.stored(state.load()) or {}).get("tg_proxy_group"), ru)

    def test_rebuild_needs_a_pool_and_a_built_proxy(self):
        """Without the unit and without the mark (the proxy was never built) — nothing, as before; the mark alone —
        the rebuild runs (REV-s1-t11 С5: an exit moved into the empty group of the bot turns the proxy on again)."""
        self.assertIsNone(pops.tg_rebuild(self.w.runner, now=5000.0))
        self.write("/etc/kaskad/egress/.bot-off", "")
        self.assertEqual(pops.tg_rebuild(self.w.runner, now=5000.0), "now")
        os.unlink(paths.egress_up())
        self.assertIsNone(pops.tg_rebuild(self.w.runner, now=9000.0))


class BashDirectTest(unittest.TestCase):
    """install.sh: «direct» of the helper — code 4 of egress_bot_config, egress_bot_off direct (the bot's xray off,
    BOT_PROXY empty, the mark «direct», its own line in the journal), the legs and the gateway go on; a group again —
    the proxy is back and the mark gone; the menu says it."""

    setUp = BashBotConfigTest.setUp
    kaskadctl = BashBotConfigTest.kaskadctl
    run_sh = BashBotConfigTest.run_sh
    REBUILD = BashBotConfigTest.REBUILD

    def stamp(self):
        try:
            with open(os.path.join(self.eg, ".bot-off")) as f:
                return f.read().strip()
        except OSError:
            return None

    def test_indexes_say_direct(self):
        self.kaskadctl("direct\n")
        rc, out = self.run_sh("egress_tg_indexes; echo RC=$?; egress_tg_select; echo \"D=[$EGRESS_TG_DIRECT] "
                              "ALL=[$EGRESS_TG_ALL] ONLY=[$EGRESS_TG_ONLY]\"")
        self.assertIn("direct\nRC=0", out)
        self.assertIn("D=[1] ALL=[] ONLY=[]", out)                       # never «every line» (D-042 is for failures)

    def test_rebuild_direct_then_back(self):
        self.kaskadctl("direct\n")
        rc, out = self.run_sh("K3_MARKS=1; " + self.REBUILD + "egress_rebuild; echo RC=$?")
        self.assertIn("RC=0", out)
        for want in ("AWG_SYNC", "GW_CHANGED", "UNITS_OFF kaskad-egress.service", "PROBE_OFF", "SAVE BOT_PROXY=\n",
                     "@K3:egress_info:tg_direct", "выбрано в «Настройки → Бот и ИИ-агент»"):
            self.assertIn(want, out + "\n")
        self.assertNotIn("WRITE_SERVICE", out)
        self.assertNotIn("tg_group_empty", out)
        self.assertEqual(self.stamp(), "direct")
        with open(os.path.join(self.d, "log")) as f:
            self.assertIn("выбрано «Напрямую»", f.read())
        self.kaskadctl("1 3\n")
        rc, out = self.run_sh(self.REBUILD + "egress_awg_runs() { return 0; }; egress_tg_rebuild_now; echo RC=$?")
        self.assertIn("RC=0", out)
        self.assertIn("WRITE_SERVICE", out)
        self.assertIn("SAVE BOT_PROXY=socks5h://127.0.0.1:", out)
        self.assertIsNone(self.stamp())

    def test_tg_rebuild_direct(self):
        self.kaskadctl("direct\n")
        rc, out = self.run_sh(self.REBUILD + "egress_awg_runs() { return 0; }; egress_tg_rebuild_now; echo RC=$?")
        self.assertIn("RC=0", out)
        self.assertIn("UNITS_OFF kaskad-egress.service", out)
        self.assertEqual(self.stamp(), "direct")

    def test_menu_header_and_f6(self):
        self.kaskadctl("direct\n")
        rc, out = self.run_sh('_egr_group_refresh; echo "GOK=[$EGR_GOK] G=[$EGR_GROUP] D=[$EGR_DIRECT]"; '
                              '_egr_in_group 1 && echo IN || echo OUT')
        self.assertIn("GOK=[1] G=[] D=[1]", out)
        self.assertIn("OUT", out)
        with open(os.path.join(self.eg, ".bot-off"), "w") as f:
            f.write("direct\n")
        rc, out = self.run_sh('EGR_ST=off; EGR_L30="—"; EGR_GOK=1; _egr_paint 1 0 2>&1 | head -12')
        self.assertIn("Бот ходит в Telegram напрямую — выбрано в «Настройки → Бот и ИИ-агент»", out)   # ≤ 77 столбцов: экран 80 (REV-3310 С1б)
        self.assertNotIn("Прокси бота выключен", out)


if __name__ == "__main__":
    unittest.main()
