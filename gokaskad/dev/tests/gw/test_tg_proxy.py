"""Stage 1, T11 (SPEC-stage1-exits §2.9): the bot's proxy takes the keys of its group. gw/tgproxy.indexes (what
``kaskadctl gw tg-proxy-indexes`` prints: no policy — every line, the group ``tg_proxy_group`` — its enabled lines, an
empty group — «main», a group gone — «main»), and install.sh: egress_bot_config builds the bot's config of the lines of
the group with their numbers kept (out_N), «without probes» with the gateway on or without the bot, its own probes
every 60 s otherwise; the helper that does not answer — every line (D-042); the header of the menu by policy.label."""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from kaskad.gw import exits, policy, tgproxy
from tests import realbin
from tests.fakes_gw import INSTALL_SH, write_pool
from tests.gwcase import GwCase
from tests.test_egress_parse_s1 import FIX

LINES = [FIX["trojan_tls"], FIX["vless_hu"], FIX["ss_sip002"], FIX["hy2"]]


def st_with(groups=None, tg="main", prefs=None):
    st = {"gateway": {"exits": prefs or {}}}
    if groups is not None:
        st["gateway"]["exit_policy"] = {"version": policy.VERSION, "tg_proxy_group": tg, "groups": groups}
    return st


class IndexesTest(GwCase):
    def setUp(self):
        super().setUp()
        write_pool(LINES)
        self.ids = [eid for eid, _ in exits.line_ids()]

    def test_no_policy_every_line(self):
        r = tgproxy.indexes(st_with())
        self.assertEqual((r["indexes"], r["group"], r["label"], r["total"], r["policy"]),
                         ([1, 2, 3, 4], "main", "Основная", 4, False))

    def test_the_group_and_its_enabled_lines(self):
        g = [policy.default_group("main", exits=[self.ids[0]]),
             policy.default_group("g2", name="Дом", exits=[self.ids[1], self.ids[3], self.ids[2]])]
        r = tgproxy.indexes(st_with(g, "g2", {self.ids[2]: {"enabled": False}}))
        self.assertEqual((r["indexes"], r["group"], r["label"]), ([2, 4], "g2", "Дом"))
        r = tgproxy.indexes(st_with(g, "main"))
        self.assertEqual((r["indexes"], r["label"]), ([1], "Основная"))

    def test_empty_group_and_a_group_gone_give_main(self):
        g = [policy.default_group("main", exits=self.ids[:2]),
             policy.default_group("g2", name="Пусто", exits=[]), policy.default_group("g3", exits=self.ids[2:])]
        self.assertEqual(tgproxy.indexes(st_with(g, "g2"))["indexes"], [1, 2])          # D-042
        self.assertEqual(tgproxy.indexes(st_with(g, "g2"))["group"], "main")
        self.assertEqual(tgproxy.indexes(st_with(g, "gone"))["indexes"], [1, 2])
        # an exit the collector has not placed yet goes where place_new puts it: «main»
        g = [policy.default_group("main", exits=[])]
        self.assertEqual(tgproxy.indexes(st_with(g, "main"))["indexes"], [1, 2, 3, 4])
        g = [policy.default_group("main", exits=[]), policy.default_group("g2", name="X", exits=self.ids)]
        self.assertEqual(tgproxy.indexes(st_with(g, "main"))["indexes"], [])          # main and the group: none

    def test_cli_label_only_with_a_policy(self):
        """REV-s1-t11 З3: ``gw tg-proxy-indexes --label`` — the name of the group only when the node has a policy."""
        import argparse
        import contextlib
        import io
        from kaskad.gw import cli
        g = [policy.default_group("main", exits=[self.ids[0]]),
             policy.default_group("g2", name="Дом", exits=[self.ids[1], self.ids[3]])]
        for st, want in ((st_with(), ["1 2 3 4"]), (st_with(g, "g2"), ["2 4", "Дом"])):
            with mock.patch.object(tgproxy.state, "load", lambda: st), contextlib.redirect_stdout(io.StringIO()) as o:
                cli.cmd_tg_proxy_indexes(argparse.Namespace(label=True, json=False))
            self.assertEqual(o.getvalue().split("\n")[:-1], want)

    def test_main_with_a_name(self):
        g = [policy.default_group("main", name="Европа", exits=self.ids)]
        self.assertEqual(tgproxy.indexes(st_with(g))["label"], "Европа")


class BashBotConfigTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        self.root = os.path.join(self.d, "root")
        self.eg = os.path.join(self.root, "etc", "kaskad", "egress")
        os.makedirs(self.eg)
        with open(os.path.join(self.eg, "upstream.txt"), "w") as f:
            f.write("\n".join(LINES) + "\n")
        self.ctl = os.path.join(self.root, "opt", "kaskad", "bin", "kaskadctl")
        os.makedirs(os.path.dirname(self.ctl))
        x = realbin.xray_path()
        if x:
            os.symlink(x, os.path.join(self.eg, "xray"))

    def kaskadctl(self, out, rc=0):
        with open(self.ctl, "w") as f:
            f.write("#!/bin/sh\necho \"$*\" >> %s/ctl.calls\nprintf '%s'\nexit %d\n" % (self.d, out, rc))
        os.chmod(self.ctl, 0o755)

    def run_sh(self, script, token="", gw=False):
        with open(os.path.join(self.root, "etc", "kaskad", "state.json"), "w") as f:
            json.dump({"gateway": {"enabled": gw}}, f)
        env = dict(os.environ, KASKAD_ROOT=self.root, LANG="en_US.UTF-8")
        for k in ("KASKAD_LANG", "LC_ALL", "BOT_TOKEN"):
            env.pop(k, None)
        head = ('source "$1" --lib >/dev/null 2>&1 || exit 97\n'
                'KASKAD_LOG=%s/log; log_action() { echo "LOG: $*" >> %s/log; }; K3_LANG=ru; BOT_TOKEN=%s\n'
                'egress_ensure_parser\n' % (self.d, self.d, token))
        r = subprocess.run(["bash", "-c", head + script, "x", INSTALL_SH], env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=120)
        out = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", r.stdout.decode("utf-8"))
        if r.returncode == 97:
            self.skipTest("install.sh --lib did not load")
        return r.returncode, out

    def cfg(self):
        with open(os.path.join(self.eg, "config.json")) as f:
            return json.load(f)

    def tags(self):
        return [o["tag"] for o in self.cfg()["outbounds"] if o["tag"].startswith("out_")]

    def test_only_the_lines_of_the_group_numbers_kept(self):
        self.kaskadctl("2 4\n")
        rc, out = self.run_sh("egress_bot_config; echo RC=$?")
        self.assertIn("RC=0", out)
        self.assertEqual(self.tags(), ["out_2", "out_4"])
        cfg = self.cfg()
        self.assertNotIn("observatory", cfg)                               # no bot, no gateway: «without probes»
        self.assertEqual(cfg["routing"]["balancers"][0]["strategy"], {"type": "roundRobin"})
        with open(os.path.join(self.eg, ".tg_indexes")) as f:
            self.assertEqual(f.read(), "2 4\n")

    def test_gateway_on_and_bot_with_token(self):
        self.kaskadctl("1 3\n")
        self.run_sh("egress_bot_config", token="123456:" + "A" * 35, gw=True)
        self.assertNotIn("observatory", self.cfg())
        self.run_sh("egress_bot_config", token="123456:" + "A" * 35, gw=False)
        cfg = self.cfg()
        self.assertEqual(cfg["observatory"]["probeInterval"], "60s")      # gateway off, the bot set: its own probes
        self.assertEqual(cfg["routing"]["balancers"][0]["strategy"], {"type": "leastPing"})

    def test_token_only_in_the_settings_file(self):
        """REV-s1-t11 С1: --egress-tg-rebuild (panel, systemd-run) and --egress-xray-upgrade run without init_config —
        the token is read from the settings of the bot, not from the variable."""
        self.kaskadctl("1 3\n")
        conf = os.path.join(self.root, "etc", "kaskad", "config")
        with open(conf, "w") as f:
            f.write('BOT_TOKEN="123456:%s"\nBOT_CHAT_ID=""\n' % ("A" * 35))
        rc, out = self.run_sh("egress_bot_config; echo RC=$?; echo \"VAR=[$BOT_TOKEN]\"")
        self.assertIn("RC=0", out)
        self.assertIn("VAR=[]", out)                                        # read in a subshell: nothing leaks out
        self.assertEqual(self.cfg()["observatory"]["probeInterval"], "60s")
        with open(conf, "w") as f:
            f.write('BOT_TOKEN=""\n')
        self.run_sh("egress_bot_config", token="123456:" + "A" * 35)        # the file says: no bot (a stale variable)
        self.assertNotIn("observatory", self.cfg())

    def test_config_of_335_rebuilt_once_after_the_update(self):
        """REV-s1-t11 З4: a node updated from 3.3.x keeps the config of the bot (every line, observatory every 10 s)
        until the pool changes — egress_xray_upgrade (--k3-post-update) rebuilds it for the group once."""
        unit = os.path.join(self.root, "etc", "systemd", "system", "kaskad-egress.service")
        os.makedirs(os.path.dirname(unit))
        open(unit, "w").close()
        with open(os.path.join(self.eg, "config.json"), "w") as f:
            json.dump({"observatory": {"probeInterval": "10s"}}, f)             # as 3.3.5 left it
        stubs = ('egress_ensure_xray() { :; }; egress_tofu_snap() { :; }; egress_xray_follow() { :; }\n'
                 'egress_xray_gw_stale() { return 1; }; egress_xray_follow_due() { return 1; }; egress_tofu_due() { return 1; }\n'
                 'egress_xray_pin() { return 1; }\n'
                 'egress_tg_rebuild() { echo TG_REBUILD >> %s/calls; egress_bot_config; }\n' % self.d)
        self.kaskadctl("1 3\n")
        rc, out = self.run_sh(stubs + 'egress_xray_upgrade_due && echo DUE; egress_xray_upgrade; '
                              'egress_xray_upgrade_due || echo NOT_DUE; egress_xray_upgrade')
        self.assertIn("DUE", out)
        with open(os.path.join(self.d, "calls")) as f:
            self.assertEqual(f.read().count("TG_REBUILD"), 1, out)          # once: the stamp is written by the build
        self.assertIn("NOT_DUE", out)
        with open(os.path.join(self.eg, ".bot-config-ver")) as f:
            self.assertEqual(f.read().strip(), "2")

    def test_helper_fails_every_line(self):
        self.kaskadctl("", rc=3)
        rc, out = self.run_sh("egress_bot_config; echo RC=$?")
        self.assertIn("RC=0", out)
        self.assertEqual(self.tags(), ["out_1", "out_2", "out_3", "out_4"])
        self.assertFalse(os.path.exists(os.path.join(self.eg, ".tg_indexes")))
        with open(os.path.join(self.d, "log")) as f:
            self.assertIn("все ключи пула", f.read())

    def test_empty_group_no_config(self):
        """REV-s1-t11 С5: the group (and «Main») has no key — code 3 (not a failure): the caller turns the proxy off."""
        self.kaskadctl("\n")
        rc, out = self.run_sh("EGRESS_MARKS=1; K3_MARKS=1; egress_bot_config; echo RC=$?")
        self.assertIn("RC=3", out)
        self.assertIn("нет включённых ключей", out)
        self.assertIn("бот ходит напрямую", out)
        self.assertIn("@K3:egress_warn:tg_group_empty", out)
        self.assertFalse(os.path.exists(os.path.join(self.eg, "config.json")))

    REBUILD = ('egress_ensure_xray() { :; }; egress_tofu_snap() { :; }; egress_awg_sync() { echo AWG_SYNC; }\n'
               'egress_write_service() { echo WRITE_SERVICE; }; k3_gw_pool_changed() { echo GW_CHANGED; }\n'
               'k3_units_off() { echo "UNITS_OFF $*"; }; egress_probe_remove() { echo PROBE_OFF; }; systemctl() { :; }\n'
               'save_config_val() { echo "SAVE $1=$2"; }; EGRESS_MARKS=1\n')

    def test_empty_group_does_not_stop_the_pool(self):
        """REV-s1-t11 С5: an empty group of the bot — the bot's proxy is off (the bot direct), the legs and the gateway
        get the pool (egress_awg_sync, gw apply): an added key and a subscription are not rolled back."""
        self.kaskadctl("\n")
        rc, out = self.run_sh(self.REBUILD + "egress_rebuild; echo RC=$?")
        self.assertIn("RC=0", out)
        for want in ("AWG_SYNC", "GW_CHANGED", "UNITS_OFF kaskad-egress.service", "PROBE_OFF", "SAVE BOT_PROXY=\n"):
            self.assertIn(want, out + "\n")
        self.assertNotIn("WRITE_SERVICE", out)
        with open(os.path.join(self.d, "log")) as f:
            self.assertIn("бот ходит напрямую", f.read())
        self.kaskadctl("1 3\n")                                          # a key in the group again — the proxy is back
        rc, out = self.run_sh(self.REBUILD + "egress_rebuild; echo RC=$?")
        self.assertIn("RC=0", out)
        self.assertIn("WRITE_SERVICE", out)
        self.assertIn("SAVE BOT_PROXY=socks5h://127.0.0.1:", out)
        self.assertNotIn("UNITS_OFF", out)

    def test_tg_rebuild_turns_the_proxy_off_and_on(self):
        self.kaskadctl("\n")
        rc, out = self.run_sh(self.REBUILD + "egress_awg_runs() { return 0; }; egress_tg_rebuild_now; echo RC=$?")
        self.assertIn("RC=0", out)
        self.assertIn("UNITS_OFF kaskad-egress.service", out)
        self.kaskadctl("2\n")
        rc, out = self.run_sh(self.REBUILD + "egress_awg_runs() { return 0; }; egress_tg_rebuild_now; echo RC=$?")
        self.assertIn("RC=0", out)
        self.assertIn("WRITE_SERVICE", out)
        self.assertIn("SAVE BOT_PROXY=socks5h://127.0.0.1:", out)

    def test_no_awg_keeps_the_numbers(self):
        from tests.fakes_gw import awg_conf
        from tests.test_334_xray import parser
        awg = parser().extract(awg_conf(2))[0]
        with open(os.path.join(self.eg, "upstream.txt"), "w") as f:
            f.write("\n".join([LINES[0], awg, LINES[2]]) + "\n")
        self.kaskadctl("1 2 3\n")
        self.run_sh("EGRESS_NO_AWG=1 egress_bot_config")
        self.assertEqual(self.tags(), ["out_1", "out_3"])

    def test_menu_header_and_rows_of_the_group(self):
        self.kaskadctl("2 4\nДом\n")
        rc, out = self.run_sh('EGR_ST=on; EGR_OPING=1; EGR_ACTIVE=2; _egr_group_refresh; _egr_paint 2 1\n'
                              'echo "STEP:$(_egr_step 2 1) $(_egr_step 4 1) $(_egr_step 4 -1)"')
        self.assertIn("Ключи группы «Дом» (2 из 4) — группа меняется в панели", out)
        self.assertIn("HTTPUpgrade", out)
        self.assertIn("Hysteria One", out)
        # REV-s1-t11 З3: the keys of other groups are in the list (greyed out — they can be removed), the cursor goes
        # over every key; the manual pick takes the keys of the group only (F6)
        self.assertIn("Trojan", out)
        self.assertIn("Серым — ключи других групп", out)
        self.assertIn("STEP:3 4 3", out)
        rc, out = self.run_sh('egress_set_active() { echo "PICK $1"; }; _egr_group_refresh\n'
                              'for k in 1 2; do sel=$k; if _egr_in_group "$sel"; then egress_set_active "$sel"; '
                              'else echo "NOTE $(tf EGRESS_TG_NOT_IN_GROUP "$sel")"; fi; done')
        self.assertIn("NOTE Ключ № 1 — не в группе прокси бота", out)
        self.assertIn("PICK 2", out)
        self.assertNotIn("PICK 1", out)
        with open(os.path.join(self.d, "ctl.calls")) as f:
            self.assertIn("gw tg-proxy-indexes --label", f.read())

    def test_menu_without_a_policy_has_no_header(self):
        """REV-s1-t11 З3: a node without groups (no policy) — the helper gives no name, the menu no header."""
        self.kaskadctl("1 2 3 4\n")
        rc, out = self.run_sh('EGR_ST=on; EGR_OPING=1; EGR_ACTIVE=2; _egr_group_refresh; _egr_paint 2 1')
        self.assertNotIn("Ключи группы", out)
        self.assertNotIn("Серым", out)

    def add_key(self, text):
        with open(os.path.join(self.d, "paste"), "w") as f:
            f.write(text)
        rc, out = self.run_sh('egress_rebuild() { return 0; }; EGRESS_NO_TEST=1; EGRESS_MARKS=1; K3_MARKS=1\n'
                              'egress_add_key_now "$(cat %s/paste)"; echo "RC=$? ADDED=$EGRESS_ADDED"' % self.d)
        with open(os.path.join(self.eg, "upstream.txt")) as f:
            return out, [l for l in f.read().splitlines() if l.strip()]

    def test_add_key_knows_a_repeat_of_every_scheme(self):
        """§3.8, Р3: the same key of hy2/ss/trojan/vmess under another name — «already in the pool», not added."""
        renamed = [FIX["trojan_tls"].replace("#Trojan", "#Other"), FIX["ss_old"], FIX["hy2"].replace("#Hysteria", "#X")]
        out, pool = self.add_key("\n".join(renamed))
        self.assertEqual(pool, LINES, out)
        self.assertIn("@K3:egress_same_peer:3", out)

    def test_add_key_second_hy2_at_one_address(self):
        """§9.1: a second Hysteria2 (another auth) at the address of a key of the pool or of upstream-ru.txt —
        dup_endpoint; another port — added."""
        other = FIX["hy2"].replace("hyAuthSecret1", "otherAuth").replace("#Hysteria%20One", "#Dup")
        out, pool = self.add_key(other)
        self.assertEqual(pool, LINES, out)
        self.assertIn("@K3:egress_fail:skipped:dup_endpoint", out)
        self.assertNotIn("otherAuth", out)
        with open(os.path.join(self.eg, "upstream-ru.txt"), "w") as f:
            f.write("hy2://ruAuth@ru.example.com:443?sni=ru.example.com#ru\n")
        out, pool = self.add_key("hy2://newAuth@ru.example.com:443?sni=ru.example.com#n\n" + other.replace(":443", ":8443"))
        self.assertEqual(len(pool), 5, out)
        self.assertIn("@K3:egress_warn:skipped:dup_endpoint:1", out)

    def test_add_key_a_line_xray_refuses_is_left_out(self):
        """REV-s1-t11 С2: a key the parser takes but the real xray refuses — left out alone (xray_refused), the rest added."""
        if realbin.xray_path() is None:
            self.skipTest("no real xray (tools/fetch_test_bins.sh)")
        from tests.test_t11_rev_s1 import FM_BOGUS
        out, pool = self.add_key(FIX["vmess_tls"] + "\n" + FM_BOGUS)
        self.assertEqual(pool, LINES + [FIX["vmess_tls"]], out)
        self.assertIn("@K3:egress_warn:skipped:xray_refused:1", out)
        self.assertIn("FmBogus", out)

    def test_add_key_never_takes_a_key_of_the_russian_file(self):
        """REV-s1-q4 З3, SEC-s1-q4 З-4: a key upstream-ru.txt holds — by its text or its server and account under
        another name — is not written into upstream.txt by the menu, --egress-add (a subscription link too: the script
        downloads it itself) nor the import; said and marked; other keys of the same text are added."""
        home = FIX["vmess_tls"]
        with open(os.path.join(self.eg, "upstream-ru.txt"), "w") as f:
            f.write(home + "\n")
        out, pool = self.add_key(home)
        self.assertEqual(pool, LINES, out)
        self.assertIn("@K3:egress_in_ru:1", out)
        self.assertIn("RC=0 ADDED=0", out)
        self.assertIn("уже во внутренних выходах", out)
        renamed = home.rsplit("#", 1)[0] + "#Other"
        out, pool = self.add_key(renamed)                              # the same server and account, another name
        self.assertEqual(pool, LINES, out)
        self.assertIn("@K3:egress_in_ru:1", out)

    def test_import_never_takes_a_key_of_the_russian_file(self):
        home = FIX["vmess_tls"]
        with open(os.path.join(self.eg, "upstream-ru.txt"), "w") as f:
            f.write(home + "\n")
        with open(os.path.join(self.d, "paste"), "w") as f:
            f.write("\n".join([home, FIX["trojan_tls"]]))
        rc, out = self.run_sh('egress_rebuild() { return 0; }; egress_test() { return 0; }; sleep() { :; }\n'
                              'EGRESS_MARKS=1; K3_MARKS=1; egress_import_now "$(cat %s/paste)"; echo "RC=$?"' % self.d)
        with open(os.path.join(self.eg, "upstream.txt")) as f:
            pool = [l for l in f.read().splitlines() if l.strip()]
        self.assertEqual(pool, [FIX["trojan_tls"]], out)
        self.assertIn("@K3:egress_in_ru:1", out)
        with open(os.path.join(self.d, "paste"), "w") as f:
            f.write(home)
        rc, out = self.run_sh('egress_rebuild() { return 0; }; EGRESS_MARKS=1; K3_MARKS=1\n'
                              'egress_import_now "$(cat %s/paste)"; echo "RC=$?"' % self.d)
        self.assertIn("RC=1", out)
        with open(os.path.join(self.eg, "upstream.txt")) as f:
            self.assertEqual([l for l in f.read().splitlines() if l.strip()], [FIX["trojan_tls"]])

    def replace(self, lines, pool=None):
        if pool is not None:
            with open(os.path.join(self.eg, "upstream.txt"), "w") as f:
                f.write("\n".join(pool) + "\n")
        with open(os.path.join(self.d, "paste"), "w") as f:
            f.write("\n".join(lines))
        rc, out = self.run_sh('egress_rebuild() { return 0; }; EGRESS_MARKS=1; K3_MARKS=1\n'
                              'egress_replace_now "$(egress_pool_hash)" "$(cat %s/paste)"; echo "RC=$?"' % self.d)
        with open(os.path.join(self.eg, "upstream.txt")) as f:
            return out, [l for l in f.read().splitlines() if l.strip()]

    def test_replace_leaves_out_a_new_line_xray_refuses(self):
        """REV-s1-t11 С2: the replace of the pool (a copy, a subscription) — a new line xray refuses goes, the others
        are applied; a line the pool had stays as it was."""
        if realbin.xray_path() is None:
            self.skipTest("no real xray (tools/fetch_test_bins.sh)")
        from tests.test_t11_rev_s1 import FM_BOGUS
        out, pool = self.replace(LINES[:2] + [FM_BOGUS])
        self.assertIn("RC=0", out)
        self.assertEqual(pool, LINES[:2], out)
        self.assertIn("@K3:egress_warn:skipped:xray_refused:1", out)
        out, pool = self.replace([FM_BOGUS, LINES[0]], pool=[FM_BOGUS])
        self.assertEqual(pool, [FM_BOGUS, LINES[0]], out)
        out, pool = self.replace([FM_BOGUS], pool=[LINES[0]])
        self.assertIn("RC=1", out)
        self.assertEqual(pool, [LINES[0]], out)

    def test_upper_case_scheme_in_the_replace(self):
        """REV-s1-t11 З1: a scheme in upper case is a key in the replace too (the parser takes it)."""
        out, pool = self.replace([LINES[0].replace("trojan://", "Trojan://", 1)], pool=[LINES[1]])
        self.assertIn("RC=0", out)
        self.assertEqual(pool, [LINES[0]], out)

    def test_skip_report_counts_the_other_schemes(self):
        """REV-s1-t11 З5 (M13): the keys of the schemes the pool does not take — one summary «другие протоколы: TUIC — 2,
        AnyTLS», not a line per key; the mark per code."""
        with open(os.path.join(self.eg, ".extract.skip"), "w") as f:
            f.write("K3SKIP\tunsupported_scheme\tTUIC\nK3SKIP\tunsupported_scheme\tAnyTLS\n"
                    "K3SKIP\tunsupported_scheme\tTUIC\nK3SKIP\tweak_cipher\tPlain\n")
        rc, out = self.run_sh('EGRESS_MARKS=1; K3_MARKS=1; egress_skip_report; echo "N=$EGRESS_SKIPPED WHY=$EGRESS_SKIP_WHY"')
        self.assertIn("N=4 ", out)
        self.assertIn("другие протоколы: TUIC — 2, AnyTLS", out)
        self.assertIn("@K3:egress_warn:skipped:unsupported_scheme:3", out)
        self.assertEqual(out.count("Пропущен ключ"), 1, out)               # only the weak cipher, by its name

    def test_endpoint_is_cached_while_the_pool_is_the_same(self):
        """REV-s1-t11 З7: the probe of losses asks every 10 s — python runs once per pool, not each time."""
        rc, out = self.run_sh('python3() { echo PY >> %s/py; command python3 "$@"; }\n'
                              'for i in 1 2 3 4 1 2; do egress_key_endpoint $i >/dev/null; done\n'
                              'echo "%s" >> "$EGRESS_UP"; echo "EP5:$(egress_key_endpoint 5)"' % (self.d, FIX["vmess_tls"]))
        with open(os.path.join(self.d, "py")) as f:
            self.assertEqual(f.read().count("PY"), 2, out)                 # the first call, then the pool changed
        self.assertIn("EP5:vm.example.com\t443\tvmess", out)
        with open(os.path.join(self.eg, ".endpoints")) as f:
            text = f.read()
        self.assertNotIn("tjSecret", text)

    def test_import_says_a_repeat_of_the_new_schemes_in_its_words(self):
        """REV-s1-t11 П3: a repeat of hy2/ss/trojan/vmess in the replace of the whole pool — not «ключей AmneziaWG»."""
        with open(os.path.join(self.d, "paste"), "w") as f:
            f.write("\n".join([FIX["trojan_tls"], FIX["trojan_tls"].replace("#Trojan", "#Again"), FIX["ss_sip002"],
                               FIX["ss_old"]]))
        rc, out = self.run_sh('egress_rebuild() { return 0; }; egress_test() { :; }; sleep() { :; }\n'
                              'egress_import_now "$(cat %s/paste)"' % self.d)
        self.assertIn("Не добавлено ключей: 2 — тот же ключ того же сервера", out)
        self.assertNotIn("AmneziaWG", out)

    def test_endpoint_of_the_new_schemes(self):
        rc, out = self.run_sh('for i in 1 2 3 4; do echo "EP$i:$(egress_key_endpoint $i)"; done')
        self.assertIn("EP1:tj.example.com\t443\ttrojan", out)
        self.assertIn("EP3:ss.example.com\t8388\tshadowsocks", out)
        self.assertIn("EP4:hy.example.com\t443\thysteria2", out)


if __name__ == "__main__":
    unittest.main()
