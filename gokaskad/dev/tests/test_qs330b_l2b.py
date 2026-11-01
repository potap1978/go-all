"""Kaskad 3.3.0, live check №2 (T-M6-qs330b), part L2-b:

    №3   heavy children of the panel (kaskadctl, the rebuild of the pool, nft with the networks of Russia, apt…) run in a
         ``systemd-run --scope`` — out of the panel's MemoryMax (one rule: kaskad.sysrun.heavy); no systemd-run or no
         scope — in place; the limit of the unit is the measured 384M;
    №6   DELETE of a device (a person) the panel repeats — the browser re-sends it when the connection broke before the
         answer — is «уже удалено», 200; a name that is not there stays an error;
    №7   all.txt, all.yaml, the bare address and all.vpn of one goAmnezia subscription are ONE subscription, in any order;
    №9   «N ключей в пуле» counts the keys of the subscription that are in the pool (its own and those there before it,
         the same server under another line), the time moves with every good refresh;
    №14  ``systemctl enable`` never reloads systemd by itself (--no-reload): a daemon-reload only when a unit file or
         its links changed — a repeated ``gw apply`` reloads nothing.
"""

import itertools
import os
import re

from kaskad import paths, sysrun
from kaskad.errors import ConflictError, KaskadError
from kaskad.gw import exits, exitsubs, ops
from kaskad.gw import sys as gsys
from kaskad.panel import cli as pcli
from kaskad.sysrun import FakeRunner, Result, ScopedRunner
from tests.fakes_gw import VLESS_A, write_pool
from tests.panel.panelcase import PanelCase
from tests.test_330_fixes import lines_of
from tests.test_gw_exits import conf_of, vpn_of
from tests.test_gw_exitsubs import GA_SECRET, GA_URL, SubsCase, b64
from tests.test_gw_ops import CliGwCase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = "/opt/kaskad/bin/gokaskad"
CTL = "/opt/kaskad/bin/kaskadctl"


# ── №3: heavy commands outside the panel's cgroup ──────────────────────────

class HeavyTest(CliGwCase):
    def test_what_is_heavy(self):
        big = "add element ip kaskad gw_ru { %s }\n" % ", ".join("10.%d.%d.0/24" % (i // 256, i % 256)
                                                                   for i in range(8000))
        yes = [([CTL, "gw", "apply"], None), ([CTL, "site", "templates-index"], None),
               ([SCRIPT, "--egress-replace", "0123456789abcdef"], "vless://x\n"), ([SCRIPT, "--egress-add"], "k\n"),
               ([SCRIPT, "--egress-del", "1", "0123456789"], None), ([SCRIPT, "--egress-ensure-xray"], None),
               (["env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "install", "-y", "x"], None),
               (["dpkg", "-i", "x.deb"], None), (["make", "-C", "/x"], None), (["dkms", "build", "x/1"], None),
               (["certbot", "renew"], None), (["nft", "-f", "-"], big), (["nft", "-f", "/etc/kaskad/net/kaskad.nft"], None),
               (["nft", "-j", "list", "table", "ip", "kaskad"], None), (["nft", "list", "ruleset"], None),
               (["/usr/local/bin/xray", "-test", "-c", "/tmp/x.json"], None)]
        no = [(["nft", "-f", "-"], "add element ip kaskad gw_live { 10.77.1.2 }\n"),
              (["nft", "-j", "list", "set", "ip", "kaskad", "gw_ru_ver"], None), (["nft", "--version"], None),
              (["systemctl", "is-active", "x.service"], None), (["ip", "-o", "addr", "show"], None),
              ([SCRIPT, "--egress-awg-heal"], None), ([SCRIPT, "--egress-ensure-parser"], None),
              (["env", "EGRESS_AWG_REMODE=1", SCRIPT, "--egress-awg-heal"], None),
              (["/usr/local/bin/xray", "api", "statsquery"], None), ([], None)]
        for argv, inp in yes:
            self.assertTrue(sysrun.heavy(argv, inp), argv[:3])
        for argv, inp in no:
            self.assertFalse(sysrun.heavy(argv, inp), argv[:3])

    def scoped(self, binaries=("systemd-run",)):
        inner = FakeRunner(binaries=binaries)
        inner.on(["systemd-run"], lambda argv, inp: (0, "out:" + " ".join(argv[argv.index("--description=kaskad-panel job")
                                                                              + 1:]), ""))
        inner.on([CTL], (0, "in place", ""))
        inner.on(["nft"], (0, "", ""))
        return inner, ScopedRunner(inner)

    def test_a_heavy_command_runs_in_a_scope_with_its_stdin(self):
        inner, sr = self.scoped()
        res = sr.run([SCRIPT, "--egress-replace", "0123456789abcdef"], input="vless://a\n", timeout=99)
        argv, inp = inner.calls[-1]
        self.assertEqual(argv[:5], ["systemd-run", "--scope", "--quiet", "--collect", "--description=kaskad-panel job"])
        self.assertEqual(argv[5:], [SCRIPT, "--egress-replace", "0123456789abcdef"])
        self.assertEqual(inp, "vless://a\n")
        self.assertEqual(res.out, "out:%s --egress-replace 0123456789abcdef" % SCRIPT)
        sr.run(["nft", "-f", "-"], input="flush set ip kaskad gw_rd\n")              # a light one: in place
        self.assertEqual(inner.calls[-1][0], ["nft", "-f", "-"])

    def test_no_systemd_run_runs_in_place(self):
        inner, sr = self.scoped(binaries=())
        self.assertEqual(sr.run([CTL, "gw", "apply"]).out, "in place")
        self.assertEqual(inner.commands(), [[CTL, "gw", "apply"]])

    def test_a_scope_that_was_not_made_runs_the_command_in_place(self):
        inner = FakeRunner(binaries=["systemd-run"])
        inner.on(["systemd-run"], (1, "", "Failed to start transient scope unit: Access denied\n"))
        inner.on([CTL], (0, "in place", ""))
        res = ScopedRunner(inner).run([CTL, "gw", "apply"])
        self.assertEqual((res.rc, res.out), (0, "in place"))
        self.assertEqual([a[0] for a in inner.commands()], ["systemd-run", CTL])

    def test_a_failure_of_the_command_itself_is_not_run_twice(self):
        inner = FakeRunner(binaries=["systemd-run"])
        inner.on(["systemd-run"], (3, "", "Пул ключей сейчас меняет другое окно\n"))
        res = ScopedRunner(inner).run([SCRIPT, "--egress-replace", "0" * 16], input="")
        self.assertEqual(res.rc, 3)
        self.assertEqual(len(inner.calls), 1)
        with self.assertRaises(sysrun.CommandError):
            ScopedRunner(inner).run([SCRIPT, "--egress-replace", "0" * 16], input="", check=True)

    def test_the_panel_server_uses_the_one_rule(self):
        from kaskad.panel import server
        self.assertIs(server.ScopedRunner, sysrun.ScopedRunner)

    def test_the_unit_has_the_measured_limit(self):
        text = pcli.unit_text()
        self.assertIn("MemoryMax=384M\n", text)
        self.assertNotIn("MemoryMax=200M", text)


class SubscriptionRefreshOutsideTest(SubsCase):
    """The case of the stand: «Обновить» of a subscription in the panel — the rebuild of the pool goes to a scope."""

    def test_the_rebuild_of_the_pool_is_scoped(self):
        world = self.w.runner
        seen = []

        class Unwrap(sysrun.Runner):                 # systemd-run of the fake node: records and runs the command
            def which(self, name):
                return "/usr/bin/systemd-run" if name == "systemd-run" else world.which(name)

            def run(self, argv, input=None, check=False, timeout=60, env=None):
                argv = list(argv)
                if argv[:2] == ["systemd-run", "--scope"]:
                    seen.append(argv[5:])
                    argv = argv[5:]
                return world.run(argv, input=input, check=check, timeout=timeout)

        sr = ScopedRunner(Unwrap())
        self.feed.bodies[GA_URL] = b64([VLESS_A])
        exitsubs.add(sr, GA_URL, fetcher=self.feed)
        self.assertEqual(self.pool(), [VLESS_A])
        self.assertTrue(any(a[1:2] == ["--egress-replace"] for a in seen), seen)


# ── №6: a repeated DELETE ──────────────────────────────────────────────────

class RepeatedDeleteTest(PanelCase):
    def test_the_second_delete_of_a_device_says_it_is_gone(self):
        self.gateway()
        d = self.ok("POST", "/api/gw/devices", {"user": self.uid, "name": "dev-tmp", "kind": "phone",
                                                "platform": "android", "access": ["wg"]})["device"]
        first = self.ok("DELETE", "/api/gw/devices/%s" % d["id"])
        self.assertEqual((first["device"]["id"], first["gone"]), (d["id"], False))
        again = self.ok("DELETE", "/api/gw/devices/%s" % d["id"])        # the browser re-sent it: 200, not 400
        self.assertEqual((again["device"]["id"], again["gone"]), (d["id"], True))
        self.assertEqual(again["text"], ops.GONE_DEVICE_TEXT)
        self.assertNotIn("неоднозначно", again["text"])

    def test_the_second_delete_of_a_person_says_he_is_gone(self):
        self.gateway()
        uid = ops.user_add(self.w.runner, "Гость")["id"]
        self.ok("DELETE", "/api/gw/users/%s" % uid)
        again = self.ok("DELETE", "/api/gw/users/%s" % uid)
        self.assertEqual((again["user"]["id"], again["gone"], again["devices"]), (uid, True, []))

    def test_the_page_rereads_after_a_cut_connection_too(self):
        """The answer lost when the change restarted xray (a network error, not a timeout): act() reads the sections
        again — the deleted device does not hang in the list until «Обновить»."""
        with open(os.path.join(paths_static(), "app.js"), encoding="utf-8") as f:
            js = f.read()
        self.assertRegex(js, r'e\.code === "timeout" \|\| e\.code === "net"\) && o\.reload\) loadAll\(o\.reload\)')

    def test_the_core_by_name_still_says_there_is_none(self):
        self.gateway()
        with self.assertRaises(KaskadError):
            ops.device_del(self.w.runner, "d-00000000")                  # the CLI: no device — an error, as before
        with self.assertRaises(KaskadError):
            ops.device_del(self.w.runner, "нет-такого", missing_ok=True)  # a name is never «already deleted»


# ── №7 and №9: subscriptions of goAmnezia ──────────────────────────────────

GA_BASE = "https://vpn.example.org/s-%s/" % GA_SECRET


class OneSubscriptionTest(SubsCase):
    def test_every_form_of_one_subscription_in_any_order_is_one(self):
        forms = ["all.txt", "all.yaml", "all.vpn", ""]
        for order in itertools.permutations(forms, 3):
            if os.path.exists(exitsubs.path()):
                os.unlink(exitsubs.path())
            write_pool([])
            self.feed.bodies[GA_URL] = b64([VLESS_A])
            first = exitsubs.add(self.w.runner, GA_BASE + order[0], fetcher=self.feed)
            self.assertTrue(first["ok"])
            for other in order[1:]:
                with self.assertRaises(ConflictError, msg=order) as c:
                    exitsubs.add(self.w.runner, GA_BASE + other, fetcher=self.feed)
                self.assertIn("уже добавлена", str(c.exception))
            subs = exitsubs.load()["subs"]
            self.assertEqual([s["url"] for s in subs], [GA_URL], order)

    def test_all_txt_kept_by_an_earlier_version_is_the_same_too(self):
        self.feed.bodies[GA_URL] = b64([VLESS_A])
        exitsubs.add(self.w.runner, GA_URL, fetcher=self.feed)
        doc = exitsubs.load()
        doc["subs"][0]["url"] = GA_BASE + "all.txt"                     # as 3.3.0 before the fix kept it
        exitsubs.save(doc)
        for form in ("all.vpn", "all.yaml", "all.txt"):
            with self.assertRaises(ConflictError):
                exitsubs.add(self.w.runner, GA_BASE + form, fetcher=self.feed)
        self.assertEqual(exitsubs.find_same(GA_BASE + "all.yaml")["id"], doc["subs"][0]["id"])
        url, note = exitsubs.normalize(GA_BASE + "all.txt")
        self.assertEqual(url, GA_URL)
        self.assertIn("all.vpn", note)

    def test_a_conf_of_one_line_stays_its_own_address(self):
        self.assertEqual(exitsubs.normalize(GA_BASE + "awg20.conf")[0], GA_BASE + "awg20.conf")


class KeysInThePoolTest(SubsCase):
    def test_three_keys_in_the_pool_are_three_though_added_before(self):
        confs = [conf_of("2.0", n) for n in (3, 4, 5)]
        write_pool([lines_of(c)[0] for c in confs])                      # added by hand as .conf
        res = exitsubs.add(self.w.runner, GA_URL, fetcher=self._feed("\n".join(vpn_of(c, "Офис %d" % i)
                                                                               for i, c in enumerate(confs))))
        self.assertEqual(res["result"]["added"], 0)
        sub = exitsubs.load()["subs"][0]
        self.assertEqual((sub["keys"], len(sub["seen"])), ([], 3))     # not its own, yet listed by it
        row = exitsubs.listing()[0]
        self.assertEqual((row["keys"], row["count"], row["exits"]), (3, 3, []))
        # one of them removed from the pool: two left
        write_pool(exits.pool_lines()[:2])
        self.assertEqual(exitsubs.listing()[0]["keys"], 2)

    def test_a_record_of_an_earlier_version_counts_what_it_lists(self):
        write_pool([VLESS_A])
        self.feed.bodies[GA_URL] = b64([VLESS_A])
        exitsubs.add(self.w.runner, GA_URL, fetcher=self.feed)
        doc = exitsubs.load()
        doc["subs"][0].pop("present")
        exitsubs.save(doc)
        self.assertEqual(exitsubs.listing()[0]["keys"], 1)

    def test_every_good_refresh_moves_the_time_even_without_changes(self):
        self.feed.bodies[GA_URL] = b64([VLESS_A])
        exitsubs.add(self.w.runner, GA_URL, fetcher=self.feed)
        t0 = exitsubs.load()["subs"][0]["last_ok"]
        self.now += 30 * 60
        self.assertIn("обновлена 30 мин назад", exitsubs.listing()[0]["text"])
        n = len(self.w.script.replace_inputs)
        res = exitsubs.refresh(self.w.runner, None, fetcher=self.feed)
        self.assertIn("без изменений", res["text"])
        self.assertEqual(len(self.w.script.replace_inputs), n)          # the pool not rewritten
        sub = exitsubs.load()["subs"][0]
        self.assertEqual(sub["last_ok"], int(self.now))
        self.assertGreater(sub["last_ok"], t0)
        self.assertIn("обновлена 1 мин назад", exitsubs.listing()[0]["text"])

    def _feed(self, body):
        self.feed.bodies[GA_URL] = body
        return self.feed


# ── №14: daemon-reload only for a change ───────────────────────────────────

class ReloadTest(CliGwCase):
    def test_ensure_unit_reloads_once_for_a_change_and_never_for_nothing(self):
        text = "[Unit]\nDescription=x\n[Service]\nExecStart=/bin/true\n[Install]\nWantedBy=multi-user.target\n"
        gsys.ensure_unit(self.w.runner, "kaskad-x.service", text)
        self.assertEqual(self.w.sysd.reloads, 1)
        gsys.ensure_unit(self.w.runner, "kaskad-x.service", text)
        gsys.ensure_unit(self.w.runner, "kaskad-x.service", text)
        self.assertEqual(self.w.sysd.reloads, 1)
        enables = [c for c in self.w.sysd.calls if c[1] == "enable"]
        self.assertTrue(enables and all("--no-reload" in c for c in enables), enables)

    def test_enable_of_a_new_link_reloads_once(self):
        self.write("/etc/systemd/system/kaskad-y.timer", "[Timer]\nOnCalendar=daily\n[Install]\nWantedBy=timers.target\n")
        _, changed = gsys.enable(self.w.runner, "kaskad-y.timer")
        self.assertTrue(changed)
        self.assertEqual(self.w.sysd.reloads, 1)
        _, changed = gsys.enable(self.w.runner, "kaskad-y.timer")
        self.assertFalse(changed)
        self.assertEqual(self.w.sysd.reloads, 1)

    def test_a_repeated_gw_apply_reloads_nothing(self):
        self.enabled_world()
        self.ok("gw", "apply")
        before = self.w.sysd.reloads
        for _ in range(3):
            self.ok("gw", "apply")
            self.ok("net", "check", "--repair", "--quiet")
        self.assertEqual(self.w.sysd.reloads, before)

    def test_the_fake_reloads_as_systemd_does(self):
        """The guard of the guard: a bare ``systemctl enable`` of an enabled unit reloads (that is what the journal
        counted 562 times) — the tests above would not see a regression otherwise."""
        self.write("/etc/systemd/system/kaskad-z.service", "[Service]\nExecStart=/bin/true\n")
        self.w.runner.run(["systemctl", "enable", "kaskad-z.service"])
        self.w.runner.run(["systemctl", "enable", "kaskad-z.service"])
        self.assertEqual(self.w.sysd.reloads, 2)

    def test_install_sh_hot_paths_do_not_reload_for_nothing(self):
        with open(os.path.join(ROOT, "install.sh"), encoding="utf-8") as f:
            text = f.read()

        def body(name):
            m = re.search(r"(?ms)^%s\(\) \{\n(.*?)^\}\n" % re.escape(name), text)
            self.assertIsNotNone(m, name)
            return m.group(1)
        for fn in ("egress_write_service", "egress_awg_leg_up", "egress_probe_install", "k3_update_timer_install",
                   "egress_awg_leg_remove"):
            for ln in body(fn).splitlines():
                if re.search(r"systemctl (enable|disable)\b", ln):
                    self.assertIn("--no-reload", ln, "%s: %s" % (fn, ln.strip()))
        ws = body("egress_write_service")
        self.assertNotIn("cat >", ws)                                    # the file only when its text differs
        self.assertRegex(ws, r'if \[ "\$\(cat "\$f" 2>/dev/null\)" != "\$text" \]; then\n\s+printf .*\n\s+systemctl '
                             r'daemon-reload\n\s+fi')


def paths_static():
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib", "kaskad", "panel", "static")
