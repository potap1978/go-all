"""Snapshots (SPEC §4.7): permissions, manifest, contents, pruning, integrity."""

import hashlib
import io
import json
import os
import stat
import tarfile
from unittest import mock

from kaskad.errors import KaskadError
from kaskad.migrate import plan, scan, snapshot
from tests.helpers import RootCase
from tests.migrate.env import SECRETS, Env
import kaskad


class SnapshotTest(RootCase):
    def make(self, scenario="pro_213", **kw):
        env = Env(self, scenario, **kw).start()
        self.write("/etc/systemd/system/kaskad-bot.service", "[Service]\nExecStart=/usr/local/bin/gokaskad --bot-daemon\n")
        self.write("/etc/sysctl.conf", "net.ipv4.ip_forward=1\n")
        inv = scan.collect(env.runner)
        pl = plan.build(inv)
        return env, inv, snapshot.create(env.runner, inv, pl)

    def test_layout_permissions_and_manifest(self):
        env, inv, path = self.make()
        self.assertTrue(os.path.basename(path).endswith("-pro-2.13"))
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(self.path("/var/backups/kaskad")).st_mode), 0o700)
        m = snapshot.manifest(path)
        self.assertEqual((m["from"]["edition"], m["from"]["version"], m["to"]), ("pro", "2.13", kaskad.__version__))
        for rel, meta in m["files"].items():
            full = os.path.join(path, rel)
            self.assertEqual(stat.S_IMODE(os.stat(full).st_mode), 0o600, rel)
            with open(full, "rb") as f:
                self.assertEqual(hashlib.sha256(f.read()).hexdigest(), meta["sha256"], rel)
        for d, _dirs, _files in os.walk(path):
            self.assertEqual(stat.S_IMODE(os.stat(d).st_mode), 0o700, d)
        for name in ("iptables-save.txt", "ip6tables-save.txt", "nft-ruleset.txt", "ip-rule.txt",
                     "ip-route-all.txt", "ss.txt", "systemctl.txt", "etc-kaskad.tar.gz", "scan.json", "plan.json",
                     "files/usr/local/bin/gokaskad", "files/etc/iptables/rules.v4", "files/etc/sysctl.conf",
                     "files/etc/systemd/system/kaskad-bot.service"):
            self.assertIn(name, m["files"])
        self.assertEqual(m["files"]["files/usr/local/bin/gokaskad"]["version"], "2.13")
        self.assertEqual(m["files"]["files/etc/iptables/rules.v4"]["mode"], "0640")
        self.assertEqual(snapshot.read_text(path, "iptables-save.txt"), env.ipt.save_text())
        self.assertEqual(env.runner.unmatched, [])

    def test_tar_without_egress_binaries(self):
        _env, _inv, path = self.make()
        data = snapshot.read_file(path, "etc-kaskad.tar.gz")
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            names = tar.getnames()
        self.assertIn("kaskad/config", names)
        self.assertIn("kaskad/egress/upstream.txt", names)
        self.assertIn("kaskad/monitors/198.51.100.7.conf", names)
        self.assertNotIn("kaskad/egress/xray", names)
        self.assertFalse(any(n.startswith("kaskad/egress/awg") for n in names))

    def test_reports_inside_have_no_secrets(self):
        _env, _inv, path = self.make()
        for rel in ("scan.json", "plan.json", "manifest.json", "systemctl.txt"):
            with open(os.path.join(path, rel), encoding="utf-8") as f:
                text = f.read()
            for s in SECRETS:
                self.assertNotIn(s, text, rel)

    def test_iptables_save_failure_leaves_nothing(self):
        env = Env(self, "pro_213").start()
        env.ipt.inject(lambda argv, inp: (1, "", "boom\n") if argv[0] == "iptables-save" else None)
        inv = scan.collect(env.runner)
        with self.assertRaises(KaskadError):
            snapshot.create(env.runner, inv, None)
        self.assertEqual(os.listdir(self.path("/var/backups/kaskad")), [])

    def test_optional_outputs_may_fail(self):
        env = Env(self, "pro_213").start()
        env.runner._handlers.insert(0, (["ip", "rule", "show"], (1, "", "no ip\n")))
        inv = scan.collect(env.runner)
        path = snapshot.create(env.runner, inv, None)
        m = snapshot.manifest(path)
        self.assertIn("ip-rule.txt", m["errors"])
        self.assertNotIn("plan.json", m["files"])

    def test_tampered_file_is_detected(self):
        _env, _inv, path = self.make()
        with open(os.path.join(path, "iptables-save.txt"), "a") as f:
            f.write("-A INPUT -j DROP\n")
        with self.assertRaises(KaskadError):
            snapshot.read_text(path, "iptables-save.txt")

    def test_prune_keeps_five_and_the_original(self):
        env = Env(self, "pro_213").start()
        inv = scan.collect(env.runner)
        os.makedirs(self.path("/var/backups/kaskad/not-ours"))
        stamps = iter("202610%02dT120000Z" % d for d in range(1, 20))
        paths = []
        with mock.patch("kaskad.util.stamp", lambda: next(stamps)):
            paths.append(snapshot.create(env.runner, inv, None))               # from 2.13: the original
            for _ in range(7):
                paths.append(snapshot.create(env.runner, inv, None, reason="rollback"))
        left = sorted(os.listdir(self.path("/var/backups/kaskad")))
        self.assertIn(os.path.basename(paths[0]), left)
        for p in paths[-5:]:
            self.assertIn(os.path.basename(p), left)
        for p in paths[1:3]:
            self.assertNotIn(os.path.basename(p), left)
        self.assertIn("not-ours", left)
        self.assertEqual(len(left), 7)
        items = snapshot.listing()
        self.assertEqual(items[0]["path"], "/var/backups/kaskad/" + os.path.basename(paths[-1]))
        self.assertEqual(items[-1]["command_version"], "2.13")

    def test_same_second_does_not_collide(self):
        env = Env(self, "pro_213").start()
        inv = scan.collect(env.runner)
        with mock.patch("kaskad.util.stamp", lambda: "20261004T120000Z"):
            a = snapshot.create(env.runner, inv, None)
            b = snapshot.create(env.runner, inv, None)
        self.assertNotEqual(a, b)
        self.assertTrue(os.path.isdir(a) and os.path.isdir(b))
        self.assertEqual([p for p, _m in snapshot.snapshots()], [a, b])         # creation order kept
        self.assertTrue(b.endswith("-pro-2.13-2"))

    def test_listing_json_has_no_contents(self):
        _env, _inv, _path = self.make()
        dump = json.dumps(snapshot.listing(), ensure_ascii=False)
        for s in SECRETS:
            self.assertNotIn(s, dump)
