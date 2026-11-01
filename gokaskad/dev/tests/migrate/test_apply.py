"""migrate apply end to end (SPEC §4.5–4.8): order of steps, verification, automatic rollback at
every step, idempotency, taking over rules that appeared later (engine already nft)."""

import contextlib
import io
import os
from unittest import mock

from kaskad import aptlock, errors, ipt, state, util
from kaskad.errors import KaskadError
from kaskad.migrate import apply as apply_mod
from kaskad.migrate import scan, snapshot
from tests.helpers import RootCase, lock_held_elsewhere
from tests.migrate import scripts
from tests.migrate.env import Env

SCENARIOS = ("free_v10", "free_v11_two_rules", "pro_23", "pro_213", "pro_211_over_free", "docker_before",
             "foreign", "empty", "real-free-v1.1", "real-pro213-over-free", "real-pro23-ufw")


def effective(env, proto, port):
    """What catches (proto, port) traffic addressed to the server: the nft table first (priority
    -101), then the first iptables nat PREROUTING rule (jumps expanded)."""
    fw = (env.nft.table or {}).get("forwards") or {}
    if (proto, port) in fw:
        return "%s:%d" % fw[(proto, port)]
    nat = ipt.parse_S(env.ipt.list_S("nat"), "nat")
    hits = scan.entries_for_key(scan.expand(nat), proto, port)
    if not hits:
        return None
    r = hits[0][0].rule
    return r.to_destination or r.target


def all_keys(env):
    nat = ipt.parse_S(env.ipt.list_S("nat"), "nat")
    keys = set()
    for r in nat.get("PREROUTING", []):
        if r.port is not None and r.proto in ("tcp", "udp"):
            keys.add((r.proto, r.port))
    for c in nat.get("DOCKER", []):
        if c.port is not None and c.proto:
            keys.add((c.proto, c.port))
    return sorted(keys)


class ApplyTest(RootCase):
    def test_pro_213_end_to_end(self):
        env = Env(self, "pro_213", services={"kaskad-bot.service": ("enabled", "active"),
                                             "kaskad-egress.service": ("enabled", "active")}).start()
        res = apply_mod.run(env.runner)
        self.assertEqual(res["result"], "ok")
        self.assertEqual([c["key"] for c in res["imported"]], ["udp:51820", "tcp:443", "tcp:8443"])
        # the nft table carries the forwards, iptables has only the compat rule left of Kaskad
        self.assertEqual(env.nft.table["forwards"], {("udp", 51820): ("198.51.100.7", 51820),
                                                     ("tcp", 443): ("203.0.113.5", 443),
                                                     ("tcp", 8443): ("198.51.100.7", 443)})
        self.assertEqual(env.ipt.rules("nat", "PREROUTING"), [])
        # kaskad:masq stays: it masquerades everything leaving eth0, another VPN may rely on it
        self.assertEqual(env.ipt.rules("nat", "POSTROUTING"),
                         ['-A POSTROUTING -o eth0 -m comment --comment "kaskad:masq" -j MASQUERADE'])
        self.assertEqual(env.ipt.rules("filter", "INPUT"), [])
        self.assertEqual(env.ipt.rules("filter", "FORWARD"), [
            '-A FORWARD -m connmark --mark 0x4b000000/0xff000000 -m comment --comment "kaskad:compat" -j ACCEPT'])
        self.assertTrue(env.units.installed)
        st = state.load()
        self.assertEqual(st["engine"], "nft")
        self.assertEqual([(f["proto"], f["port"], f["name"], f["origin"]) for f in st["forwards"]],
                         [("tcp", 443, "Вена", "pro"), ("tcp", 8443, "Амстердам", "pro"),
                          ("udp", 51820, "Амстердам", "pro")])
        mig = st["migrations"][-1]
        self.assertEqual((mig["result"], mig["kind"], mig["from"]["edition"], mig["from"]["version"]),
                         ("ok", "migrate", "pro", "2.13"))
        self.assertTrue(os.path.isdir(self.path(mig["backup"])))
        rules_v4 = self.read("/etc/iptables/rules.v4")
        self.assertEqual([l for l in rules_v4.splitlines() if "kaskad:" in l],
                         ['-A POSTROUTING -o eth0 -m comment --comment "kaskad:masq" -j MASQUERADE'])
        self.assertIn("*nat\n:PREROUTING ACCEPT [0:0]", rules_v4)
        self.assertIn("MIGRATE to nft: imported 3, removed 12", self.read("/var/log/kaskad.log"))
        self.assertEqual(env.runner.unmatched, [])

    def test_old_rules_go_only_after_the_nft_table_is_up(self):
        env = Env(self, "pro_213").start()
        apply_mod.run(env.runner)
        names = env.names()
        i_nft = env.index("nft.apply")
        i_compat = env.index("compat.ensure")
        removals = [i for i, e in enumerate(env.events) if e[0] == "iptables-restore" and e[2] == "noflush"]
        self.assertEqual([env.events[i][1] for i in removals], ["nat", "filter"])
        self.assertLess(i_nft, i_compat)
        # the engine's units are in place before anything of the old engine goes: a kill or a power
        # cut after the removal still finds kaskad-net.service to bring the forwards back at boot
        self.assertLess(i_compat, env.index("units.install"))
        self.assertLess(env.index("units.install"), removals[0])
        self.assertLess(env.index("iptables-save"), i_nft)                 # snapshot first
        self.assertEqual(names.count("nft.apply"), 1)

    def test_behaviour_is_preserved_on_every_fixture(self):
        for sc in SCENARIOS:
            with self.subTest(sc):
                self.tearDown()
                self.setUp()
                env = Env(self, sc, ufw_active=(sc == "real-pro23-ufw")).start()
                keys = all_keys(env)
                before = dict((k, effective(env, *k)) for k in keys)
                res = apply_mod.run(env.runner, force=True)
                self.assertEqual(res["result"], "ok", sc)
                after = dict((k, effective(env, *k)) for k in keys)
                self.assertEqual(after, before, sc)
                self.assertEqual(env.runner.unmatched, [], sc)
                again = apply_mod.run(env.runner)
                self.assertEqual(again["result"], "nothing", sc)

    def test_legacy_backend_uses_wait(self):
        env = Env(self, "free_v10", backend="legacy").start()
        apply_mod.run(env.runner)
        restores = env.runner.commands(["iptables-restore"])
        self.assertTrue(restores)
        for argv in restores:
            self.assertEqual(argv[1:3], ["-w", "10"])

    def test_empty_server_switches_engine(self):
        env = Env(self, "empty").start()
        res = apply_mod.run(env.runner)
        self.assertEqual(res["result"], "ok")
        self.assertEqual(env.nft.table["forwards"], {})
        self.assertEqual(env.restores("noflush"), [])
        self.assertTrue(env.units.installed)
        self.assertEqual(state.load()["engine"], "nft")
        self.assertEqual(len(snapshot.snapshots()), 1)

    def test_installs_nftables_when_missing(self):
        env = Env(self, "free_v10", nft_installed=False, supported=False).start()
        res = apply_mod.run(env.runner)
        self.assertEqual(res["result"], "ok")
        self.assertEqual(env.apt_calls, [["apt-get", "-o", "DPkg::Lock::Timeout=600", "install", "-y", "nftables"]])
        # no hidden prompt can hang a CLI run: no debconf dialog, needrestart decides itself, stdin closed
        self.assertEqual(env.apt_env, [({"DEBIAN_FRONTEND": "noninteractive", "NEEDRESTART_MODE": "a"}, "")])

    def test_install_failure_changes_nothing(self):
        env = Env(self, "free_v10", nft_installed=False, supported=False).start()
        env.apt_fails = True
        before = env.snapshot_state()
        with self.assertRaises(KaskadError) as cm:
            apply_mod.run(env.runner)
        self.assertEqual(cm.exception.code, errors.UNSUPPORTED)
        self.assertEqual(env.snapshot_state(), before)
        self.assertFalse(state.exists())
        self.assertIn(["apt-get", "-o", "DPkg::Lock::Timeout=600", "update"], env.apt_calls)
        self.assertEqual(len(env.apt_env), 3)                     # install, update, install — all without prompts
        for envs, stdin in env.apt_env:
            self.assertEqual((envs.get("DEBIAN_FRONTEND"), envs.get("NEEDRESTART_MODE"), stdin),
                             ("noninteractive", "a", ""))

    def _apt_clock(self, held):
        """aptlock with a fake clock: ``held`` — what holder() answers, in turn (then: free)."""
        clock = [0.0]

        def sleep(s):
            clock[0] += s
        seq = list(held)
        return [mock.patch.object(aptlock, "_now", lambda: clock[0]), mock.patch.object(aptlock, "_sleep", sleep),
                mock.patch.object(aptlock, "holder", lambda: seq.pop(0) if seq else None)], clock

    def test_busy_apt_is_waited_for_and_said_once(self):
        """Fresh server: unattended-upgrades holds the dpkg lock — one line on stderr, then nftables goes in
        as soon as the lock is free; the runner may wait longer than apt itself does."""
        env = Env(self, "free_v10", nft_installed=False, supported=False).start()
        uu = {"path": "/var/lib/dpkg/lock-frontend", "pid": 4321, "name": "unattended-upgrade"}
        patches, clock = self._apt_clock([uu] * 3)                     # held at the first look and 2 polls more
        timeouts = []
        run = env.runner.run

        def spy(argv, input=None, check=False, timeout=60):
            if "apt-get" in argv:
                timeouts.append(timeout)
            return run(argv, input=input, check=check, timeout=timeout)
        env.runner.run = spy
        err = io.StringIO()
        with patches[0], patches[1], patches[2], contextlib.redirect_stderr(err):
            res = apply_mod.run(env.runner)
        self.assertEqual(res["result"], "ok")
        self.assertEqual(err.getvalue(), "Система ставит обновления (apt занят) — жду до 10 минут…\n")
        self.assertEqual(clock[0], 6.0)                                   # three polls of 2 s
        self.assertEqual(env.apt_calls, [["apt-get"] + aptlock.OPTS + ["install", "-y", "nftables"]])
        self.assertTrue(timeouts and all(t > aptlock.WAIT for t in timeouts), timeouts)
        self.assertIn("APT: ждал 6 с, пока система ставила обновления (unattended-upgrade (pid 4321))",
                      self.read("/var/log/kaskad.log"))

    def test_apt_busy_for_ten_minutes_is_a_clear_error_and_nothing_changes(self):
        env = Env(self, "free_v10", nft_installed=False, supported=False).start()
        before = env.snapshot_state()
        uu = {"path": "/var/lib/dpkg/lock-frontend", "pid": 4321, "name": "unattended-upgrade"}
        patches, clock = self._apt_clock([uu] * 1000)
        with patches[0], patches[1], patches[2], mock.patch("sys.stderr"):
            with self.assertRaises(KaskadError) as cm:
                apply_mod.run(env.runner)
        self.assertEqual(cm.exception.code, errors.ERROR)                # not «unsupported»: try again later
        self.assertEqual(str(cm.exception), "nftables не установлен: apt занят дольше 10 минут: блокировку держит "
                         "unattended-upgrade (pid 4321) — система ставит обновления. Повторите, когда закончит.")
        self.assertEqual(clock[0], 600.0)
        self.assertEqual(env.apt_calls, [])                               # apt-get never started
        self.assertEqual(env.snapshot_state(), before)
        self.assertFalse(state.exists())

    def test_apt_that_hits_the_lock_anyway_is_named_and_not_updated(self):
        env = Env(self, "free_v10", nft_installed=False, supported=False).start()
        lock_err = ("E: Could not get lock /var/lib/dpkg/lock-frontend. It is held by process 4321 (unattended-upgr)\n"
                    "E: Unable to acquire the dpkg frontend lock (/var/lib/dpkg/lock-frontend), is another process "
                    "using it?\n")
        env.runner._handlers.insert(0, (["env"], lambda argv, inp: (env.apt_calls.append(argv[3:]) or 100, "", lock_err)))
        with self.assertRaises(KaskadError) as cm:
            apply_mod.run(env.runner)
        self.assertEqual(cm.exception.code, errors.ERROR)
        self.assertIn("блокировку держит unattended-upgr (pid 4321)", str(cm.exception))
        self.assertEqual([c[-1] for c in env.apt_calls], ["nftables", "nftables"])   # waited again, no «update»

    def test_blocked_changes_nothing(self):
        env = Env(self, "docker_after").start()
        before = env.snapshot_state()
        with self.assertRaises(KaskadError) as cm:
            apply_mod.run(env.runner)
        self.assertEqual(cm.exception.code, errors.CONFLICT)
        self.assertEqual(env.snapshot_state(), before)
        self.assertEqual(snapshot.snapshots(), [])
        res = apply_mod.run(env.runner, force=True)
        self.assertIn(("tcp", 8080), env.nft.table["forwards"])
        self.assertTrue(any("--force" in w for w in res["warnings"]))

    def test_unsupported_changes_nothing(self):
        env = Env(self, "pro_213", supported=False).start()
        with self.assertRaises(KaskadError) as cm:
            apply_mod.run(env.runner)
        self.assertEqual(cm.exception.code, errors.UNSUPPORTED)
        self.assertEqual(env.nft.table, None)

    def test_snapshot_failure_changes_nothing(self):
        env = Env(self, "pro_213").start()
        env.ipt.inject(lambda argv, inp: (1, "", "boom\n") if argv[0] == "iptables-save" else None)
        before = env.snapshot_state()
        with self.assertRaises(KaskadError) as cm:
            apply_mod.run(env.runner)
        self.assertEqual(cm.exception.code, errors.ERROR)
        self.assertEqual(env.snapshot_state(), before)
        self.assertFalse(state.exists())


class RollbackOnFailureTest(RootCase):
    """Failure injected at each step: exit code 4 and nothing lost."""

    def run_failing(self, setup, scenario="pro_213"):
        env = Env(self, scenario, services={"kaskad-bot.service": ("enabled", "active")}).start()
        before = env.snapshot_state()
        setup(env)
        with self.assertRaises(KaskadError) as cm:
            apply_mod.run(env.runner)
        self.assertEqual(cm.exception.code, errors.ROLLED_BACK, str(cm.exception))
        self.assertIn("Вернул как было", str(cm.exception))
        self.assertEqual(env.snapshot_state(), before)
        st = state.load()
        self.assertEqual(st["engine"], "legacy")
        self.assertEqual(st["migrations"][-1]["result"], "rolled_back")
        self.assertTrue(st["migrations"][-1]["reason"])
        self.assertEqual(env.runner.unmatched, [])
        return env, cm.exception

    def test_state_save_fails(self):
        real = state.save
        calls = []

        def flaky(st, path=None):
            calls.append(1)
            if len(calls) == 1:
                raise OSError("No space left on device")
            return real(st, path)
        patcher = mock.patch("kaskad.state.save", flaky)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.run_failing(lambda env: None)

    def test_nft_apply_fails(self):
        env, _e = self.run_failing(lambda env: setattr(env.nft, "fail_apply", KaskadError("nft: syntax error")))
        self.assertEqual(env.restores("noflush"), [])

    def test_compat_fails(self):
        self.run_failing(lambda env: setattr(env.compat, "fail_ensure", KaskadError("iptables refused")))

    def test_nat_removal_fails(self):
        def setup(env):
            env.ipt.inject(lambda argv, inp: (1, "", "iptables-restore: line 2 failed\n")
                           if argv[0] == "iptables-restore" and "--noflush" in argv and inp.startswith("*nat") else None)
        env, _e = self.run_failing(setup)
        self.assertEqual(env.restores("full"), [])      # nat was not changed: nothing to restore

    def test_filter_removal_fails_after_nat(self):
        def setup(env):
            env.ipt.inject(lambda argv, inp: (1, "", "iptables-restore: line 3 failed\n")
                           if argv[0] == "iptables-restore" and "--noflush" in argv and inp.startswith("*filter")
                           else None)
        env, _e = self.run_failing(setup)
        self.assertEqual([e[1] for e in env.restores("full")], ["nat"])   # nat restored from the snapshot

    def test_persisted_write_fails(self):
        def boom(path, edit):
            raise OSError("Read-only file system")
        patcher = mock.patch("kaskad.migrate.apply._write_edit", boom)
        patcher.start()
        self.addCleanup(patcher.stop)
        env, _e = self.run_failing(lambda env: None)
        self.assertEqual(sorted(e[1] for e in env.restores("full")), ["filter", "nat"])

    def test_units_install_fails(self):
        self.run_failing(lambda env: setattr(env.units, "fail_install", KaskadError("systemctl failed")))

    def test_interrupted(self):
        self.run_failing(lambda env: setattr(env.units, "fail_install", KeyboardInterrupt()))

    def test_verify_table_differs(self):
        def setup(env):
            env.nft.corrupt = lambda t: t["forwards"].pop(("tcp", 443))
        _env, e = self.run_failing(setup)
        self.assertIn("Проверка после переключения не прошла", str(e))
        self.assertIn("tcp:443", str(e))

    def test_verify_counter_missing(self):
        _env, e = self.run_failing(lambda env: setattr(env.nft, "corrupt", lambda t: t["counters"].clear()))
        self.assertIn("счётчик", str(e))

    def test_verify_units_disabled(self):
        self.run_failing(lambda env: setattr(env.units, "broken_status", True))

    def test_verify_compat_missing(self):
        def setup(env):
            env.compat.present_override = False
            patcher = mock.patch("kaskad.compat.present", lambda runner: False)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.run_failing(setup)

    def test_verify_service_stopped(self):
        def setup(env):
            env.nft.after_apply = lambda: env.services.__setitem__("kaskad-bot.service", ("enabled", "failed"))
        env, e = self.run_failing(setup)
        self.assertIn("kaskad-bot.service", str(e))
        self.assertEqual(env.sleeps, [apply_mod.RECHECK_DELAY])       # asked once more before failing

    def test_verify_rule_survived_in_iptables(self):
        def setup(env):
            # iptables-restore says ok but does nothing for filter
            env.ipt.inject(lambda argv, inp: (0, "", "") if argv[0] == "iptables-restore" and "--noflush" in argv
                           and inp.startswith("*filter") else None)
        _env, e = self.run_failing(setup)
        self.assertIn("в iptables осталось правило", str(e))

    def test_free_scenario_rollback(self):
        self.run_failing(lambda env: setattr(env.units, "fail_install", KaskadError("x")), "real-free-v1.1")

    def test_rollback_itself_fails_keeps_nft(self):
        env = Env(self, "pro_213").start()

        def inj(argv, inp):
            if argv[0] != "iptables-restore":
                return None
            if "--noflush" in argv and inp.startswith("*filter"):
                return 1, "", "line 2 failed\n"
            if "-T" in argv:
                return 1, "", "restore failed\n"
            return None
        env.ipt.inject(inj)
        with self.assertRaises(KaskadError) as cm:
            apply_mod.run(env.runner)
        self.assertEqual(cm.exception.code, errors.ERROR)
        self.assertIn("Пробросы работают через nftables", str(cm.exception))
        self.assertIsNotNone(env.nft.table)                 # traffic keeps a path
        st = state.load()
        self.assertEqual(st["engine"], "nft")
        self.assertEqual(st["migrations"][-1]["result"], "failed")


class ServiceRecheckTest(RootCase):
    """Verify step 6: a Kaskad service caught in the middle of a restart is not a mismatch."""

    def env(self):
        return Env(self, "pro_213", services={"kaskad-bot.service": ("enabled", "active"),
                                              "kaskad-monitor.service": ("enabled", "active")}).start()

    def test_activating_counts_as_running(self):
        env = self.env()
        env.nft.after_apply = lambda: env.services.__setitem__("kaskad-bot.service", ("enabled", "activating"))
        res = apply_mod.run(env.runner)
        self.assertEqual(res["result"], "ok")
        self.assertEqual(env.sleeps, [])

    def test_restarting_service_is_asked_again(self):
        env = self.env()
        env.nft.after_apply = lambda: env.services.__setitem__("kaskad-monitor.service", ("enabled", "inactive"))
        env.on_sleep = lambda: env.services.__setitem__("kaskad-monitor.service", ("enabled", "active"))
        res = apply_mod.run(env.runner)
        self.assertEqual(res["result"], "ok")
        self.assertEqual(env.sleeps, [apply_mod.RECHECK_DELAY])
        self.assertEqual(state.load()["engine"], "nft")

    def test_running_services_need_no_pause(self):
        env = self.env()
        self.assertEqual(apply_mod.run(env.runner)["result"], "ok")
        self.assertEqual(env.sleeps, [])


FOREIGN_LINE = "-A INPUT -p tcp -m tcp --dport 2200 -j ACCEPT\n"
KASKAD_LINE = '-A INPUT -p tcp -m tcp --dport 443 -m comment --comment "kaskad:443:tcp" -j ACCEPT\n'


class PersistedWrittenMeanwhileTest(RootCase):
    """rules.v4 changed by another tool between the scan and the edit (critic #7): the planned lines
    are taken out of the CURRENT content; if they are gone, the edit is refused and rolled back."""

    def rewrite(self, fn):
        p = self.path("/etc/iptables/rules.v4")
        with open(p, encoding="utf-8") as f:
            text = f.read()
        with open(p, "w", encoding="utf-8") as f:
            f.write(fn(text))

    def test_unrelated_change_is_kept(self):
        env = Env(self, "pro_213").start()
        # netfilter-persistent save by someone else while the nft table goes up
        env.nft.after_apply = lambda: self.rewrite(lambda t: t.replace(":OUTPUT ACCEPT [0:0]\n",
                                                                        ":OUTPUT ACCEPT [0:0]\n" + FOREIGN_LINE, 1))
        res = apply_mod.run(env.runner)
        self.assertEqual(res["result"], "ok")
        got = self.read("/etc/iptables/rules.v4")
        self.assertIn(FOREIGN_LINE, got)
        self.assertEqual([l for l in got.splitlines() if "kaskad:" in l],
                         ['-A POSTROUTING -o eth0 -m comment --comment "kaskad:masq" -j MASQUERADE'])

    def test_planned_line_gone_refuses_and_leaves_the_file_to_its_writer(self):
        env = Env(self, "pro_213").start()
        ipt_before = env.ipt.state()
        holder = {}

        def someone_saves():
            self.rewrite(lambda t: t.replace(KASKAD_LINE, ""))
            holder["text"] = self.read("/etc/iptables/rules.v4")
        env.nft.after_apply = someone_saves
        with self.assertRaises(KaskadError) as cm:
            apply_mod.run(env.runner)
        self.assertEqual(cm.exception.code, errors.ROLLED_BACK)
        self.assertIn("rules.v4 изменился во время переезда", str(cm.exception))
        self.assertEqual(self.read("/etc/iptables/rules.v4"), holder["text"])     # not overwritten
        self.assertEqual(env.ipt.state(), ipt_before)
        self.assertEqual(state.load()["engine"], "legacy")

    def test_rollback_does_not_overwrite_a_file_rewritten_after_our_edit(self):
        env = Env(self, "pro_213").start()

        def later(t):
            if "fwd" not in later.__dict__:
                later.fwd = True
                self.rewrite(lambda text: text + "# saved by someone else\n")
            t["forwards"].pop(("tcp", 443), None)            # and the verification fails
        env.nft.corrupt = later
        with self.assertRaises(KaskadError) as cm:
            apply_mod.run(env.runner)
        self.assertEqual(cm.exception.code, errors.ERROR)
        self.assertIn("rules.v4 изменился после правки", str(cm.exception))
        self.assertTrue(self.read("/etc/iptables/rules.v4").endswith("# saved by someone else\n"))
        self.assertIsNotNone(env.nft.table)                  # forwards keep a path
        self.assertEqual(state.load()["engine"], "nft")

    def plan(self):
        from kaskad.migrate import plan, scan
        env = Env(self, "pro_213").start()
        inv = scan.collect(env.runner)
        return inv, plan.build(inv)

    def test_write_persisted_from_current_content(self):
        inv, pl = self.plan()
        self.rewrite(lambda t: t.replace(":OUTPUT ACCEPT [0:0]\n", ":OUTPUT ACCEPT [0:0]\n" + FOREIGN_LINE, 1))
        old, new, _mode = apply_mod.write_persisted(inv.persisted, pl.persisted_lines)
        got = self.read("/etc/iptables/rules.v4")
        self.assertEqual(got.encode("utf-8"), new)
        self.assertNotEqual(old, inv.persisted.raw)          # the content of now, not of the scan
        self.assertIn(FOREIGN_LINE, got)
        self.assertEqual([l for l in got.splitlines() if "kaskad:" in l],
                         ['-A POSTROUTING -o eth0 -m comment --comment "kaskad:masq" -j MASQUERADE'])

    def test_write_persisted_refuses_when_a_planned_line_is_gone(self):
        inv, pl = self.plan()
        self.rewrite(lambda t: t.replace(KASKAD_LINE, ""))
        before = self.read("/etc/iptables/rules.v4")
        with self.assertRaises(KaskadError) as cm:
            apply_mod.write_persisted(inv.persisted, pl.persisted_lines)
        self.assertIn("нет 1 из строк", str(cm.exception))
        self.assertEqual(self.read("/etc/iptables/rules.v4"), before)


class IdempotencyTest(RootCase):
    def test_second_run_does_nothing(self):
        env = Env(self, "free_v11_two_rules").start()
        apply_mod.run(env.runner)
        n_snap = len(snapshot.snapshots())
        n_events = len(env.events)
        res = apply_mod.run(env.runner)
        self.assertEqual(res["result"], "nothing")
        self.assertEqual(len(snapshot.snapshots()), n_snap)
        self.assertFalse([e for e in env.events[n_events:] if e[0] in ("nft.apply", "iptables-restore", "iptables-save")])

    def test_adopt_rules_added_later(self):
        env = Env(self, "pro_213").start()
        apply_mod.run(env.runner)
        # the free script is run on top of Kaskad 3: one new port, one port Kaskad 3 already has
        scripts.free_add(env.ipt, "tcp", 8080, 80, "192.0.2.10")
        scripts.free_add(env.ipt, "tcp", 443, 443, "192.0.2.9")
        prev = dict(env.nft.table["forwards"])
        res = apply_mod.run(env.runner)
        self.assertEqual((res["result"], res["mode"]), ("ok", "adopt"))
        self.assertEqual([c["key"] for c in res["imported"]], ["tcp:8080"])
        fw = env.nft.table["forwards"]
        self.assertEqual(fw[("tcp", 443)], prev[("tcp", 443)])         # Kaskad 3 kept its target
        self.assertEqual(fw[("tcp", 8080)], ("192.0.2.10", 80))
        self.assertEqual(env.ipt.rules("nat", "PREROUTING"), [])
        self.assertEqual(env.ipt.rules("filter", "INPUT"), [])
        st = state.load()
        self.assertEqual(len(st["forwards"]), 4)
        self.assertEqual(st["migrations"][-1]["kind"], "adopt")
        self.assertEqual(apply_mod.run(env.runner)["result"], "nothing")

    def test_adopt_failure_restores_previous_nft_state(self):
        env = Env(self, "pro_213").start()
        apply_mod.run(env.runner)
        scripts.free_add(env.ipt, "tcp", 8080, 80, "192.0.2.10")
        before = env.snapshot_state()
        st_before = state.load()
        env.units.fail_install = KaskadError("systemctl failed")
        with self.assertRaises(KaskadError) as cm:
            apply_mod.run(env.runner)
        self.assertEqual(cm.exception.code, errors.ROLLED_BACK)
        self.assertEqual(env.snapshot_state(), before)
        st = state.load()
        self.assertEqual(st["engine"], "nft")
        self.assertEqual(st["forwards"], st_before["forwards"])
        self.assertEqual(st["migrations"][-1]["result"], "rolled_back")

    def test_locked(self):
        env = Env(self, "pro_213").start()
        with lock_held_elsewhere():          # «busy»: another process holds it (T1: the same thread — NestedLockError)
            with mock.patch("kaskad.state.time.sleep", lambda s: None), \
                    mock.patch("kaskad.state.time.time", side_effect=[0, 100, 200, 300]):
                with self.assertRaises(KaskadError) as cm:
                    apply_mod.run(env.runner)
        self.assertEqual(cm.exception.code, errors.LOCKED)
        self.assertEqual(env.nft.table, None)

    def test_log_has_no_secrets(self):
        env = Env(self, "pro_213").start()
        apply_mod.run(env.runner)
        util.log_action("x")
        from tests.migrate.env import SECRETS
        log = self.read("/var/log/kaskad.log")
        for s in SECRETS:
            self.assertNotIn(s, log)


class HealTest(RootCase):
    """Critic #1c: engine nft, but the table is not loaded or kaskad-net.service / the watchdog timer
    is not enabled (a switch cut off by a power loss, units removed by hand): migrate apply loads the
    table from state.json and installs the units, and says so in one line."""

    def migrated(self, scenario="pro_213"):
        env = Env(self, scenario).start()
        apply_mod.run(env.runner)
        self.assertEqual(state.load()["engine"], "nft")
        return env

    def power_cut(self, env):
        env.nft.table = None                 # nothing loaded it at boot...
        env.units.installed = False          # ...because the units never got written
        del env.events[:]

    def test_power_cut_before_the_units(self):
        env = self.migrated()
        self.power_cut(env)
        n_snap = len(snapshot.snapshots())
        res = apply_mod.run(env.runner)
        self.assertEqual(res["result"], "nothing")
        self.assertEqual(res["healed"], "Движок nftables был не в порядке (таблица nftables «kaskad» не загружена; "
                                        "служба kaskad-net.service не включена; таймер kaskad-net-watch.timer не "
                                        "включён) — таблица загружена, службы движка включены.")
        fw = dict(((f["proto"], f["port"]), (f["to"], f["to_port"])) for f in state.load()["forwards"])
        self.assertEqual(env.nft.table["forwards"], fw)
        self.assertTrue(env.units.installed)
        names = env.names()
        self.assertLess(names.index("nft.apply"), names.index("units.install"))
        self.assertIn("units.activate", names)                         # started, after the lock
        self.assertIn("NET heal: таблица nftables «kaskad» не загружена", self.read("/var/log/kaskad.log"))
        self.assertEqual(len(snapshot.snapshots()), n_snap)              # nothing of iptables touched
        again = apply_mod.run(env.runner)
        self.assertNotIn("healed", again)

    def test_units_removed_by_hand(self):
        env = self.migrated()
        env.units.installed = False
        del env.events[:]
        res = apply_mod.run(env.runner)
        self.assertIn("служба kaskad-net.service не включена", res["healed"])
        self.assertNotIn("таблица nftables «kaskad» не загружена", res["healed"])
        self.assertEqual(env.names().count("nft.apply"), 1)
        self.assertTrue(env.units.installed)

    def test_healthy_engine_is_left_alone(self):
        env = self.migrated()
        del env.events[:]
        res = apply_mod.run(env.runner)
        self.assertEqual(res["result"], "nothing")
        self.assertNotIn("healed", res)
        self.assertNotIn("nft.apply", env.names())
        self.assertNotIn("units.install", env.names())

    def test_unreadable_table_is_not_called_missing(self):
        env = self.migrated()
        from unittest import mock
        with mock.patch("kaskad.nft.read_live", side_effect=KaskadError("nft: Permission denied")):
            inv = scan.collect(env.runner)
        self.assertEqual(scan.engine_health(inv), [])
        self.assertEqual(inv.report()["nft_table"]["error"], "nft: Permission denied")

    def test_heal_and_adopt_in_one_run(self):
        env = self.migrated()
        self.power_cut(env)
        scripts.free_add(env.ipt, "tcp", 8080, 80, "192.0.2.10")
        res = apply_mod.run(env.runner)
        self.assertEqual((res["result"], res["mode"]), ("ok", "adopt"))
        self.assertIn("таблица nftables «kaskad» не загружена", res["healed"])
        self.assertIn(("tcp", 8080), env.nft.table["forwards"])
        self.assertEqual(len(env.nft.table["forwards"]), 4)

    def test_heal_even_when_the_adoption_is_blocked(self):
        env = self.migrated()
        self.power_cut(env)
        e = env.ipt
        e.cmd("-t", "nat", "-N", "DOCKER")
        e.cmd("-t", "nat", "-A", "DOCKER", "!", "-i", "docker0", "-p", "tcp", "--dport", "8080", "-j", "DNAT",
              "--to-destination", "172.17.0.2:80")
        scripts.pro213_add(e, "tcp", 8080, 80, "10.0.0.5")
        e.cmd("-t", "nat", "-A", "PREROUTING", "-m", "addrtype", "--dst-type", "LOCAL", "-j", "DOCKER")
        with self.assertRaises(KaskadError) as cm:
            apply_mod.run(env.runner)
        self.assertEqual(cm.exception.code, errors.CONFLICT)
        self.assertTrue(str(cm.exception).startswith("Движок nftables был не в порядке ("))
        self.assertIn("Переезд сейчас невозможен", str(cm.exception))
        self.assertIsNotNone(env.nft.table)
        self.assertTrue(env.units.installed)
        self.assertIn("units.activate", env.names())

    def test_kaskad_net_started_even_if_a_later_step_fails(self):
        env = self.migrated()
        self.power_cut(env)
        scripts.free_add(env.ipt, "tcp", 8080, 80, "192.0.2.10")         # adoption work -> a snapshot
        env.ipt.inject(lambda argv, inp: (1, "", "boom\n") if argv[0] == "iptables-save" else None)
        with self.assertRaises(KaskadError) as cm:
            apply_mod.run(env.runner)
        self.assertIn("резервная копия не сделана", str(cm.exception))
        self.assertIsNotNone(env.nft.table)                               # healed before
        self.assertTrue(env.units.installed)
        self.assertIn("units.activate", env.names())

    def test_heal_failure(self):
        env = self.migrated()
        self.power_cut(env)
        env.nft.fail_apply = KaskadError("nft: Could not process rule")
        with self.assertRaises(KaskadError) as cm:
            apply_mod.run(env.runner)
        self.assertEqual(cm.exception.code, errors.ERROR)
        self.assertIn("и починить не вышло: nft: Could not process rule", str(cm.exception))

    def test_cli_text(self):
        import contextlib
        import io
        from kaskad import cli
        env = self.migrated()
        self.power_cut(env)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = cli.main(["migrate", "apply", "--yes"])
        self.assertEqual(rc, errors.OK)
        lines = out.getvalue().splitlines()
        self.assertTrue(lines[0].startswith("Движок nftables был не в порядке ("), lines)
        self.assertEqual(lines[1], "Переносить нечего.")

    def test_scan_report_and_legacy(self):
        env = self.migrated()
        self.power_cut(env)
        rep = scan.collect(env.runner).report()
        self.assertEqual(rep["engine_health"][0], "таблица nftables «kaskad» не загружена")
        self.assertFalse(rep["nft_table"]["exists"])
        self.tearDown()
        self.setUp()
        env = Env(self, "pro_213").start()                 # engine legacy: nothing to heal
        self.assertEqual(scan.collect(env.runner).report()["engine_health"], [])
