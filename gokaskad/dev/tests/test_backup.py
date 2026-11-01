"""kaskad.backup — a copy of the settings in one file: the cipher (a wrong password, a damaged or foreign file), the
preview, the import with its snapshot and the rollback, the address of the devices, secrets never in argv or the
journal (docs/k3/SPEC-subs-backup.md §2)."""

import base64
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
import unittest
from unittest import mock

from kaskad import backup, paths, secrets, state, sysrun, util
from kaskad.errors import ConflictError, KaskadError
from kaskad.gw import exits, exitsubs, model, ops, registry
from tests.fakes_gw import VLESS_A, VLESS_B, FakeUfw, install_parser, write_pool
from tests.fakes_nft import F443, save_state
from tests.test_gw_ops import CliGwCase
from tests.test_gw_reinstall import AwgLike

PW = "очень-секретно-42"
TOKEN = "123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
PAT = "ghp_" + "x" * 36


class FakeOpenssl(object):
    """``openssl enc -aes-256-cbc … -a -A [-d] -pass env:KASKAD_BACKUP_KEY``: a keyed stream (sha256 of the key and a
    counter) instead of AES — the container, the keys and the MAC of kaskad.backup are what is tested here; the key
    comes from the environment of the call only."""

    def __init__(self, runner):
        self.runner = runner

    def __call__(self, argv, inp):
        assert argv[-1] == "env:%s" % backup.KEY_ENV and argv[-2] == "-pass", argv
        key = bytes.fromhex(self.runner.envs[-1][1][backup.KEY_ENV])

        def stream(n):
            out, c = b"", 0
            while len(out) < n:
                out += hashlib.sha256(key + c.to_bytes(4, "big")).digest()
                c += 1
            return out[:n]
        if "-d" in argv:
            data = base64.b64decode(inp.strip())
            return 0, bytes(a ^ b for a, b in zip(data, stream(len(data)))).decode("ascii", "replace"), ""
        data = inp.encode("ascii")
        return 0, base64.b64encode(bytes(a ^ b for a, b in zip(data, stream(len(data))))).decode() + "\n", ""


class BackupCase(CliGwCase):
    UFW = FakeUfw

    def setUp(self):
        super().setUp()
        self.w.runner.binaries.add("openssl")
        self.w.runner.on(["openssl"], FakeOpenssl(self.w.runner))
        p = mock.patch.object(backup, "ITER", 100000)
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(backup, "_here_ip", lambda runner: self.here_ip)
        p.start()
        self.addCleanup(p.stop)
        self.here_ip = "93.184.216.34"

    def old_node(self):
        """The server the copy is taken on: engine nft with a forward, the gateway on with wg and awg1 (its keys and
        parameters), a person with a device, the pool, a subscription, the bot, the GitHub key, an agent's key."""
        self.ads = self.dummies()
        self.ads["awg1"] = AwgLike()
        registry.register(self.ads["awg1"])
        save_state([F443])
        install_parser()
        write_pool([VLESS_A, VLESS_B])
        self.ok("gw", "enable")
        self.ok("gw", "host", "93.184.216.34", "--force")        # the old server's address (not of this fake machine)
        self.ok("gw", "inbound", "install", "awg1")
        self.ok("gw", "inbound", "install", "wg")
        self.ok("gw", "user", "add", "Мама")
        _, doc, _ = self.run_json("gw", "device", "add", "Мама", "Телефон", "--access", "awg1,wg")
        self.did = doc["device"]["id"]
        exitsubs.save({"next": 2, "subs": [{"id": "s-abcdef", "n": 1, "url": "http://198.51.100.9/sub/SECRETPATH",
                                            "auto": True, "pin": "", "keys": [exits.exit_id(VLESS_B)],
                                            "seen": [exits.exit_id(VLESS_B)], "added": "2026-10-01T00:00:00Z",
                                            "last_ok": 1, "last_try": 1, "error": "", "count": 1, "full": 0}]})
        util.atomic_write(paths.config(), 'BOT_TOKEN="%s"\nBOT_CHAT_ID="42"\nGITHUB_PAT="%s"\nMENU_STYLE="inline"\n'
                          % (TOKEN, PAT), 0o600)
        util.atomic_write(paths.aliases(), "203.0.113.5=Вена|||\n", 0o600)
        self.before = {"gw": json.loads(json.dumps(state.load()["gateway"])), "sec": dict(secrets.load())}

    def new_node(self):
        """Wipe this world to a fresh node (nothing of the old one left): the copy is then imported here."""
        ops.purge(self.w.runner, users=True)
        save_state([])
        write_pool([])
        exitsubs.save({"next": 1, "subs": []})
        util.atomic_write(paths.config(), 'BOT_TOKEN=""\nMENU_STYLE="inline"\n', 0o600)
        os.unlink(paths.aliases())


class CipherTest(BackupCase):
    def setUp(self):
        super().setUp()
        save_state([F443])
        install_parser()
        write_pool([VLESS_A])
        util.atomic_write(paths.config(), 'BOT_TOKEN="%s"\nBOT_CHAT_ID="42"\n' % TOKEN, 0o600)

    def test_round_trip_and_nothing_readable_in_the_file(self):
        res = backup.export(self.w.runner, PW)
        text = res["text"]
        self.assertTrue(text.startswith("KASKAD-BACKUP 1\n"))
        for secret in (TOKEN, "11111111-2222", "vless://", "203.0.113.5"):
            self.assertNotIn(secret, text)
        doc = backup.unseal(self.w.runner, text, PW)
        self.assertEqual(doc["manifest"]["kind"], "kaskad-snapshot")
        self.assertIn(backup.F_POOL, doc["files"])
        self.assertIn(TOKEN, doc["files"][backup.F_CONFIG])
        self.assertEqual(json.loads(doc["files"][backup.F_STATE])["forwards"][0]["port"], 443)

    def test_the_key_goes_in_the_environment_never_in_argv(self):
        backup.export(self.w.runner, PW)
        calls = [a for a, _ in self.w.runner.calls if a[0] == "openssl"]
        self.assertTrue(calls)
        for argv in calls:
            self.assertFalse(any(PW in a or len(a) >= 64 and all(c in "0123456789abcdef" for c in a) for a in argv))
        self.assertTrue(all(backup.KEY_ENV in e for _, e in self.w.runner.envs))
        self.assertNotIn(PW, json.dumps(self.w.runner.envs))      # a key derived from it, not the password

    def test_wrong_password_and_damaged_file_are_told_apart(self):
        text = backup.export(self.w.runner, PW)["text"]
        with self.assertRaises(KaskadError) as c:
            backup.unseal(self.w.runner, text, "другой-пароль")
        self.assertEqual(str(c.exception), backup.WRONG_PASSWORD)
        lines = text.splitlines()
        body = lines[2]
        lines[2] = ("B" if body[0] != "B" else "C") + body[1:]
        with self.assertRaises(KaskadError) as c:
            backup.unseal(self.w.runner, "\n".join(lines), PW)
        self.assertEqual(str(c.exception), backup.DAMAGED)
        header = json.loads(text.splitlines()[1])
        header["parts"] = ["gateway"]                             # the header is signed too
        lines = text.splitlines()
        lines[1] = json.dumps(header, sort_keys=True, separators=(",", ":"))
        with self.assertRaises(KaskadError):
            backup.unseal(self.w.runner, "\n".join(lines), PW)

    def test_foreign_truncated_and_newer_files(self):
        text = backup.export(self.w.runner, PW)["text"]
        cases = [("просто текст", "не файл копии"), (text[:40], "обрезан"),
                 (json.dumps({"kind": "kaskad-awg-export", "v": 1}), "экспорт AmneziaWG"),
                 (text.replace("KASKAD-BACKUP 1", "KASKAD-BACKUP 2"), "более новой версией")]
        for t, words in cases:
            with self.assertRaises(KaskadError) as c:
                backup.unseal(self.w.runner, t, PW)
            self.assertIn(words, str(c.exception), t[:30])

    def test_short_password_is_refused(self):
        with self.assertRaises(KaskadError):
            backup.export(self.w.runner, "1234567")

    def test_a_file_of_an_unknown_name_is_refused(self):
        doc = backup.payload(self.w.runner, backup.PARTS)
        doc["files"]["files/etc/passwd"] = "root::0:0"
        doc["manifest"]["files"]["files/etc/passwd"] = {"sha256": backup._sha("root::0:0"), "size": 9}
        text = backup.seal(self.w.runner, doc, PW, backup.PARTS)
        with self.assertRaises(KaskadError) as c:
            backup.unseal(self.w.runner, text, PW)
        self.assertIn("чужой файл", str(c.exception))

    def test_parts_can_be_left_out(self):
        doc = backup.unseal(self.w.runner, backup.export(self.w.runner, PW, ["exits", "forwards"])["text"], PW)
        self.assertEqual(doc["manifest"]["parts"], ["exits", "forwards"])
        self.assertNotIn(backup.F_CONFIG, doc["files"])
        self.assertNotIn(backup.F_SECRETS, doc["files"])

    def test_cli_password_from_stdin_file_0600(self):
        import io
        import sys
        out = os.path.join(self.root, "copy.kbk")
        with mock.patch.object(sys, "stdin", io.StringIO(PW + "\n")):
            rc, doc, err = self.run_json("backup", "export", "--file", out)
        self.assertEqual(rc, 0, err)
        self.assertEqual(stat.S_IMODE(os.stat(out).st_mode), 0o600)
        self.assertNotIn("text", doc)
        with mock.patch.object(sys, "stdin", io.StringIO(PW + "\n")):
            rc, doc, err = self.run_json("backup", "import", out)
        self.assertEqual(rc, 0, err)
        self.assertFalse(doc["applied"])
        self.assertTrue(any(r["part"] == "exits" for r in doc["preview"]["parts"]))
        self.assertNotIn(TOKEN, json.dumps(doc) + err + self.log())


@unittest.skipUnless(shutil.which("openssl"), "нет openssl")
class RealOpensslTest(unittest.TestCase):
    """The real openssl of this machine (Debian/Ubuntu: 1.1.1 or 3.x): the flags and the format hold."""

    def test_round_trip(self):
        r = sysrun.SystemRunner()
        res = r.run(["openssl", "enc", "-help"])
        if "pbkdf2" not in (res.err + res.out):
            self.skipTest("openssl без -pbkdf2")
        key = os.urandom(32)
        plain = json.dumps({"a": "Привет", "b": "x" * 5000}, ensure_ascii=True)
        enc = backup._openssl(r, False, key, plain)
        self.assertNotIn("Привет", enc)
        self.assertEqual(backup._openssl(r, True, key, "".join(enc.split()) + "\n"), plain)


class ImportTest(BackupCase):
    def test_the_node_moves_with_its_keys_ports_and_people(self):
        self.old_node()
        text = backup.export(self.w.runner, PW)["text"]
        self.new_node()
        self.assertEqual(model.users(state.load()), [])
        doc = backup.unseal(self.w.runner, text, PW)
        pv = backup.preview(self.w.runner, doc)
        self.assertTrue(pv["ok"])
        acts = dict((r["part"], r["action"]) for r in pv["parts"])
        self.assertEqual((acts["gateway"], acts["exits"], acts["bot"]), ("add", "add", "add"))
        self.assertEqual(pv["address"]["changed"], False)
        res = backup.apply(self.w.runner, doc)
        self.assertTrue(res["backup"].startswith(paths.backups()))
        st = state.load()
        self.assertTrue(model.enabled(st))
        self.assertEqual([u["name"] for u in model.users(st)], ["Мама"])
        self.assertEqual([d["id"] for d in model.devices(st)], [self.did])
        rec = model.inbound(st, "awg1")
        old = self.before["gw"]["inbounds"]["awg1"]
        self.assertEqual((rec["port"], rec["params"], rec["pub"]), (old["port"], old["params"], old["pub"]))
        sec = secrets.load()
        for k in ("awg1.server.private", "dev.%s.awg1.private" % self.did):
            self.assertEqual(sec.get(k), self.before["sec"].get(k), k)
        self.assertEqual(exits.pool_lines(), [VLESS_A, VLESS_B])
        self.assertEqual([s["id"] for s in exitsubs.load()["subs"]], ["s-abcdef"])
        self.assertEqual([(f["proto"], f["port"]) for f in st["forwards"]], [("tcp", 443)])
        self.assertIn(TOKEN, util.read_text(paths.config()))
        self.assertIn("Вена", util.read_text(paths.aliases()))
        self.assertEqual(st["gateway"]["host"], "93.184.216.34")
        log = self.log()
        for secret in (TOKEN, PAT, "SECRETPATH", PW, "11111111-2222"):
            self.assertNotIn(secret, log + json.dumps(res, ensure_ascii=False))
        # the copy taken before: a snapshot of the migration format
        from kaskad.migrate import snapshot as msnap
        self.assertEqual(msnap.manifest(res["backup"])["reason"], "backup-import")

    def test_names_of_servers_come_clean(self):
        """SEC-3371 Б-1: the file of names of servers (2.x, read by ``echo -e``) from a copy — every field without «\\»,
        C0, C1, U+2028/2029; lines without an address dropped; good lines and their order kept."""
        from kaskad.bot import aliases
        self.old_node()
        util.atomic_write(paths.aliases(), "203.0.113.5=Вена|||\n198.51.100.7=Офис\\e]52;c;SGk=\\a\x1b]52;c;SGk=\x07"
                          "\u009b2J|за\u2028метка|DE|ISP\u009d\nмусор\nexample.com=x|||\n", 0o600)
        text = backup.export(self.w.runner, PW)["text"]
        self.new_node()
        backup.apply(self.w.runner, backup.unseal(self.w.runner, text, PW))
        got = util.read_text(paths.aliases())
        self.assertEqual(got, "203.0.113.5=Вена|||\n198.51.100.7=Офисe]52;c;SGk=a]52;c;SGk=2J|заметка|DE|ISP\n")
        self.assertEqual(aliases.get("198.51.100.7")["name"], "Офисe]52;c;SGk=a]52;c;SGk=2J")
        self.assertEqual(aliases.clean_file(""), "")

    def test_another_address_needs_new_settings(self):
        self.old_node()
        text = backup.export(self.w.runner, PW)["text"]
        self.new_node()
        self.here_ip = "151.101.1.69"
        doc = backup.unseal(self.w.runner, text, PW)
        pv = backup.preview(self.w.runner, doc)
        self.assertTrue(pv["address"]["changed"])
        self.assertTrue(any("выдайте им настройки заново" in w for w in pv["warnings"]))
        with mock.patch("kaskad.gw.net.detect_host", lambda runner: ("151.101.1.69", "route")):
            backup.apply(self.w.runner, doc)
        self.assertEqual(state.load()["gateway"]["host"], "151.101.1.69")

    def test_a_domain_needs_only_its_a_record(self):
        self.old_node()
        self.ok("gw", "host", "vpn.example.org")
        doc = backup.unseal(self.w.runner, backup.export(self.w.runner, PW)["text"], PW)
        pv = backup.preview(self.w.runner, doc)
        self.assertEqual(pv["address"]["kind"], "domain")
        self.assertTrue(any("A-запись" in w for w in pv["warnings"]))

    def test_a_failure_brings_the_node_back(self):
        self.old_node()
        text = backup.export(self.w.runner, PW)["text"]
        self.new_node()
        save_state([F443])                                   # «the new node» had its own forward
        write_pool([VLESS_A])
        doc = backup.unseal(self.w.runner, text, PW)

        def broken(runner):
            raise KaskadError("apt не поставил пакеты")
        self.ads["awg1"].install = broken
        with self.assertRaises(KaskadError) as c:
            backup.apply(self.w.runner, doc)
        self.assertIn("Перенос не удался на шаге «шлюз»", str(c.exception))
        self.assertIn("Вернул как было", str(c.exception))
        st = state.load()
        self.assertEqual(model.users(st), [])
        self.assertFalse(model.installed_kinds(st))
        self.assertEqual(exits.pool_lines(), [VLESS_A])
        self.assertNotIn(TOKEN, util.read_text(paths.config()))

    def test_undo_returns_the_settings_before_the_import(self):
        self.old_node()
        text = backup.export(self.w.runner, PW)["text"]
        self.new_node()
        write_pool([VLESS_B])
        backup.apply(self.w.runner, backup.unseal(self.w.runner, text, PW))
        self.assertEqual(len(model.users(state.load())), 1)
        res = backup.undo(self.w.runner)
        st = state.load()
        self.assertEqual(model.users(st), [])
        self.assertFalse(model.installed_kinds(st))
        self.assertEqual(exits.pool_lines(), [VLESS_B])
        self.assertNotIn(TOKEN, util.read_text(paths.config()))
        self.assertTrue(res["backup"])                       # undo took its own copy first

    def test_the_engine_of_awg_of_this_machine_stays(self):
        """T-s1fin (было до): ``gateway.awg_engine`` is of this machine (NODE_GW_KEYS) — the import never brings the
        copy's and keeps this one's; «Вернуть как было» too (before: ``null`` after both — «not asked: the program»)."""
        from kaskad.gw import awgengine
        self.old_node()
        awgengine.save_record(want="kernel", why="install:default")         # the machine of the copy: the module
        text = backup.export(self.w.runner, PW)["text"]
        self.new_node()
        awgengine.save_record(want="program", why="person:install")         # this one: the program, chosen by a person
        mine = state.load()["gateway"]["awg_engine"]
        backup.apply(self.w.runner, backup.unseal(self.w.runner, text, PW))
        self.assertEqual(len(model.users(state.load())), 1)                 # the copy came
        self.assertEqual(state.load()["gateway"].get("awg_engine"), mine)
        awgengine.save_record(want="kernel", why="cli")                     # switched after the import: what runs now
        mine = state.load()["gateway"]["awg_engine"]
        backup.undo(self.w.runner)
        self.assertEqual(model.users(state.load()), [])                     # the copy went
        self.assertEqual(state.load()["gateway"].get("awg_engine"), mine)   # not the snapshot's: the machine's now

    def test_the_rollback_keeps_the_engine_of_awg_too(self):
        from kaskad.gw import awgengine
        self.old_node()
        text = backup.export(self.w.runner, PW)["text"]
        self.new_node()
        awgengine.save_record(want="kernel", why="install:default")
        mine = state.load()["gateway"]["awg_engine"]

        def broken(runner):
            raise KaskadError("apt не поставил пакеты")
        self.ads["awg1"].install = broken
        with self.assertRaises(KaskadError):
            backup.apply(self.w.runner, backup.unseal(self.w.runner, text, PW))
        self.assertEqual(state.load()["gateway"].get("awg_engine"), mine)

    def _agent_key_moves(self):
        """An agent's key on the old node, none on the new one: the import adds it, a return takes it away."""
        from kaskad.mcp import keys
        from tests.test_333d_backend import ed25519
        line, fp = ed25519(7)
        keys.add(line, label="laptop")
        text = backup.export(self.w.runner, PW)["text"]
        keys.delete(fp, self.w.runner)
        return keys, fp, text

    def test_undo_says_the_agents_it_cut_off(self):
        """R-333 З2″: «Вернуть как было» removes the agent key the import added — that ends its sessions (once for all
        the keys) and the answer says so; unsure — other agents may be cut too."""
        self.old_node()
        keys, fp, text = self._agent_key_moves()
        self.new_node()
        backup.apply(self.w.runner, backup.unseal(self.w.runner, text, PW), agents=True)   # SEC-3372 З-3: ticked
        self.assertIn(fp, json.dumps(keys.listing()["keys"]))
        # R-333 П3‴: the question before the return knows the key goes (the panel and the console say so)
        self.assertEqual(backup.undo_keys_gone(), 1)
        self.assertEqual(backup.summary_doc(self.w.runner)["undo"]["agent_keys_gone"], 1)
        import contextlib
        import io
        import types
        from kaskad import cli_backup
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli_backup.cmd_undo(types.SimpleNamespace(yes=False, json=False))
        self.assertIn(backup.tr("cli_backup.undo.agents", n=1), buf.getvalue())
        calls = []
        with mock.patch("kaskad.mcp.sessions.end_for", lambda fps, runner: calls.append(list(fps)) or
                        {"ended": 2, "unsure": True}):
            res = backup.undo(self.w.runner)
        self.assertEqual(calls, [[fp]])
        self.assertEqual(res["agents_cut"], 2)                # the panel shows the result as a warning, not green
        self.assertNotIn(fp, json.dumps(keys.listing()["keys"]))
        note = backup.tr("backup.note.mcp_cut_all", n=1)
        self.assertIn(note, res["notes"])
        self.assertIn("• " + note, res["text"])

    def test_a_failure_rollback_says_the_agents_it_cut_off(self):
        self.old_node()
        keys, fp, text = self._agent_key_moves()
        self.new_node()
        doc = backup.unseal(self.w.runner, text, PW)

        def broken(runner):
            raise KaskadError("apt не поставил пакеты")
        self.ads["awg1"].install = broken
        with mock.patch("kaskad.mcp.sessions.end_for", lambda fps, runner: {"ended": 1, "unsure": False}):
            with self.assertRaises(KaskadError) as c:
                backup.apply(self.w.runner, doc, agents=True)
        self.assertNotIn(fp, json.dumps(keys.listing()["keys"]))
        self.assertIn("Вернул как было", str(c.exception))
        self.assertIn(backup.tr("backup.rollback.notes", notes=backup.tr("backup.note.mcp_cut", n=1)), str(c.exception))

    def test_undo_without_agent_sessions_says_only_the_keys(self):
        self.old_node()
        keys, fp, text = self._agent_key_moves()
        self.new_node()
        backup.apply(self.w.runner, backup.unseal(self.w.runner, text, PW), agents=True)   # SEC-3372 З-3: ticked
        with mock.patch("kaskad.mcp.sessions.end_for", lambda fps, runner: {"ended": 0, "unsure": False}):
            res = backup.undo(self.w.runner)
        self.assertIn(backup.tr("backup.note.mcp_removed", n=1), res["notes"])
        self.assertEqual(res["agents_cut"], 0)
        self.assertEqual(backup.undo_keys_gone(), 0)            # nothing of the agents left to take away

    def test_undo_keys_gone_without_agent_keys(self):
        """No agent key added by the import — the question before the return has nothing to warn about."""
        self.old_node()
        text = backup.export(self.w.runner, PW)["text"]
        self.new_node()
        backup.apply(self.w.runner, backup.unseal(self.w.runner, text, PW))
        self.assertEqual(backup.undo_keys_gone(), 0)
        self.assertEqual(backup.summary_doc(self.w.runner)["undo"]["agent_keys_gone"], 0)

    def test_legacy_engine_blocks_the_gateway(self):
        self.old_node()
        doc = backup.unseal(self.w.runner, backup.export(self.w.runner, PW)["text"], PW)
        st = state.load()
        st["engine"] = "legacy"
        state.save(st)
        pv = backup.preview(self.w.runner, doc)
        self.assertFalse(pv["ok"])
        with self.assertRaises(ConflictError):
            backup.apply(self.w.runner, doc)

    def test_an_ssh_port_forward_is_not_carried(self):
        save_state([dict(F443, port=22, proto="tcp")])
        install_parser()
        doc = backup.unseal(self.w.runner, backup.export(self.w.runner, PW, ["forwards"])["text"], PW)
        save_state([])
        with mock.patch("kaskad.conflicts.check", lambda proto, port, runner, **kw:
                        [{"kind": "ssh", "detail": "sshd"}] if port == 22 else []):
            res = backup.apply(self.w.runner, doc)
        self.assertEqual(state.load()["forwards"], [])
        self.assertTrue(any("SSH" in w for w in res["warnings"]))


class PolicyRevTest(BackupCase):
    """The version of the exit policy over a restore (SPEC-stage1-exits §3.1; REV-s1-t1 З-1, З-6): never the copy's own,
    max(this node's, the copy's) + 1; a copy without the gateway (exact) — the count goes on from a random number."""

    def set_rev(self, value):
        with state.transaction() as st:
            g = st.setdefault("gateway", {})
            if value is None:
                g.pop("policy_rev", None)
                g.pop("policy_sig", None)
            else:
                g["policy_rev"] = value
                g["policy_sig"] = "x"

    def test_restore_is_max_plus_one(self):
        from kaskad.gw import rev
        self.old_node()
        self.set_rev(3)
        text = backup.export(self.w.runner, PW)["text"]
        self.set_rev(7)                                      # this node went on after the copy
        backup.apply(self.w.runner, backup.unseal(self.w.runner, text, PW))
        st = state.load()
        self.assertEqual(st["gateway"]["policy_rev"], 8)    # max(7, 3) + 1: a form opened before gets 409
        self.assertEqual(st["gateway"]["policy_sig"], rev.fingerprint(st))
        self.set_rev(None)                                   # a copy of 3.3.5: no rev in it
        old = backup.export(self.w.runner, PW)["text"]
        self.set_rev(11)
        backup.apply(self.w.runner, backup.unseal(self.w.runner, old, PW))
        self.assertEqual(state.load()["gateway"]["policy_rev"], 12)

    def test_purge_and_an_exact_copy_without_the_gateway_start_from_a_random_number(self):
        from kaskad.gw import rev

        class Rng(object):
            def __init__(self, n):
                self.n = n

            def randint(self, a, b):
                assert (a, b) == (1, rev.BASE_MAX - 1)
                return self.n
        save_state([F443])                                   # a node without the gateway: its copy has none
        install_parser()
        bare = backup.export(self.w.runner, PW)["text"]
        self.old_node()
        self.set_rev(5)
        with mock.patch.object(rev.random, "SystemRandom", lambda: Rng(123456789)):
            backup.apply(self.w.runner, backup.unseal(self.w.runner, bare, PW), exact=True)
        st = state.load()
        self.assertFalse(st["gateway"].get("policy_rev"))
        self.assertEqual(rev.rev_of(st), 123456789)          # not 0: a tab of rev 1 of before never passes
        self.assertEqual(rev.note(st), 123456790)
        self.assertNotIn(rev.BASE_KEY, st)                   # the gateway counts by itself again
        # gw purge with the people itself
        state.save(st)
        with mock.patch.object(rev.random, "SystemRandom", lambda: Rng(987654321)):
            ops.purge(self.w.runner, users=True)
        st = state.load()
        self.assertEqual(st["gateway"], {})                  # the gateway stays empty (state.empty())
        self.assertEqual((st[rev.BASE_KEY], rev.rev_of(st)), (987654321, 987654321))


class PanelApiTest(BackupCase):
    def setUp(self):
        super().setUp()
        from kaskad.panel import api
        self.api = api
        p = mock.patch.object(api, "RUNNER", self.w.runner)
        p.start()
        self.addCleanup(p.stop)
        save_state([F443])
        install_parser()
        write_pool([VLESS_A])

    def call(self, fn, body=None, query=None, match=None):
        from kaskad.panel.routes import HttpError, Request
        req = Request("POST", "/", query=query or {}, body=json.dumps(body or {}).encode(), match=match)
        try:
            resp = fn(req)
        except HttpError as e:
            return e.status, {"error": e.text}
        return resp.status, json.loads(resp.body.decode())

    def test_export_preview_import(self):
        st, doc = self.call(self.api.backup_export, {"password": PW})
        self.assertEqual(st, 200)
        self.assertTrue(doc["name"].endswith(".kbk"))
        st, pv = self.call(self.api.backup_preview, {"file": doc["file"], "password": "неверный-пароль"})
        self.assertEqual((st, pv["error"]), (400, backup.WRONG_PASSWORD))
        st, pv = self.call(self.api.backup_preview, {"file": doc["file"], "password": PW})
        self.assertEqual(st, 200, pv)
        self.assertTrue(pv["preview"]["ok"])
        st, res = self.call(self.api.backup_import, {"file": doc["file"], "password": PW, "parts": ["exits"]})
        self.assertEqual(st, 200, res)
        self.assertEqual(res["parts"], ["exits"])
        self.assertNotIn(PW, self.log())

    def test_subscriptions_routes(self):
        import re
        feed = {"http://198.51.100.9/s": VLESS_B}
        with mock.patch.object(exitsubs.subfetch, "fetch_sub",
                               lambda url, pin=None, new_pin=False: (feed[url], None)):
            st, res = self.call(self.api.gw_exit_sub_add, {"url": "http://198.51.100.9/s", "auto": False})
            self.assertEqual(st, 200, res)
            sid = res["sub"]["id"]
            m = re.match(r"/api/gw/exit-subs/(?P<id>s-[0-9a-f]{6})/refresh", "/api/gw/exit-subs/%s/refresh" % sid)
            st, res = self.call(self.api.gw_exit_sub_refresh, match=m)
            self.assertEqual(st, 200, res)
            self.assertTrue(res["all_ok"])
        self.assertEqual(exits.pool_lines(), [VLESS_A, VLESS_B])
        m = re.match(r"(?P<id>s-[0-9a-f]{6})", sid)
        st, res = self.call(self.api.gw_exit_sub_del, match=m)
        self.assertEqual((st, res["removed"]), (200, 1))
        self.assertEqual(exits.pool_lines(), [VLESS_A])


class SetupWizardTest(BackupCase):
    """The quick setup (kaskad.setup.steps «backup» and «import-file» kind backup) takes and loads the copy in process,
    as the panel: the file comes back to the page, the password and the file stay out of setup.json and the journal."""

    def setUp(self):
        super().setUp()
        from kaskad.panel import auth, conf
        from kaskad.setup import link, model as smodel, steps
        self.steps, self.smodel = steps, smodel
        conf.save({"schema": 1, "public": {"on": True, "port": 41234, "bind": "93.184.216.34", "host": "93.184.216.34",
                                           "cert_host": "93.184.216.34"}})
        smodel.update(lambda d: link.issue(d))
        p = mock.patch.object(auth, "SCRYPT_N", 1 << 10)
        p.start()
        self.addCleanup(p.stop)
        self.assertEqual(steps.run("password", {"password": "Correct-Horse-9"}, self.w.runner)["state"], "ok")

    # T-M6-qs330 №4: «Открыть копию» применяла копию сразу — теперь открыть (предпросмотр) и перенести — два шага

    STATE_FILES = ("/etc/kaskad/state.json", "/etc/kaskad/secrets.json", "/etc/kaskad/egress/upstream.txt",
                   "/etc/kaskad/egress/subs.json", "/etc/kaskad/config", "/etc/kaskad/aliases",
                   "/etc/kaskad/panel/config.json", "/root/.ssh/authorized_keys")

    def files_now(self):
        out = {}
        for rel in self.STATE_FILES:
            p = paths.P(rel)
            out[rel] = util.read_text(p) if os.path.exists(p) else None
        return out

    def tickets(self):
        from kaskad.setup import jobs
        d = jobs.run_dir()
        return sorted(n for n in os.listdir(d) if n.startswith("backup-")) if os.path.isdir(d) else []

    VLESS_C = VLESS_B.replace("66666666-7777", "12121212-3434").replace("#Riga", "#Berlin")

    def copy_then_more(self):
        """A copy of the old node, then this node gets more: a person with a device, a forward, a key of the pool, the
        default route «напрямую» — what the import would lose."""
        self.old_node()
        text = self.steps.run("backup", {"password": PW}, self.w.runner)["data"]["file"]
        self.ok("gw", "user", "add", "Петя")
        self.ok("gw", "device", "add", "Петя", "Ноутбук", "--access", "wg")
        with state.locked():
            st = state.load()
            state.upsert_forward(st, {"proto": "tcp", "port": 8443, "to": "198.51.100.23", "to_port": 22})
            state.save(st)
        write_pool([VLESS_A, self.VLESS_C])
        subs = exitsubs.load()
        subs["subs"].append(dict(subs["subs"][0], id="s-123456", n=2, url="https://203.0.113.77/sub/SECRET2", keys=[],
                                 seen=[]))
        exitsubs.save(subs)
        self.ok("gw", "route", "direct")
        return text

    def test_export_then_import_on_a_fresh_node(self):
        self.old_node()
        out = self.steps.run("backup", {"password": PW}, self.w.runner)
        self.assertEqual(out["state"], "ok", out)
        text = out["data"]["file"]
        self.assertTrue(text.startswith("KASKAD-BACKUP 1\n"))
        self.assertTrue(out["data"]["name"].endswith(".kbk"))
        self.new_node()
        out = self.steps.run("backup-open", {"file": text, "password": "не тот пароль"}, self.w.runner)
        self.assertEqual((out["state"], out["error"]["code"], out["error"]["field"]), ("error", "password", "password"))
        self.assertEqual(out["text"], backup.WRONG_PASSWORD)
        self.assertEqual(self.tickets(), [])
        self.assertEqual(model.users(state.load()), [])
        out = self.steps.run("backup-open", {"file": text, "password": PW}, self.w.runner)
        self.assertEqual(out["state"], "ok", out)
        d = out["data"]
        self.assertTrue(d["empty"])                                  # a fresh node: nothing to lose, no «да» needed
        self.assertEqual((d["lost"], d["changed"], d["confirm"]), ([], [], False))
        self.assertEqual(model.users(state.load()), [])                # the preview changed nothing
        out = self.steps.run("import-file", {"kind": "backup", "ticket": d["ticket"]}, self.w.runner)
        self.assertIn(out["state"], ("ok", "partial"), out)
        self.assertTrue(out["data"]["backup"].startswith(paths.backups()))
        self.assertEqual([u["name"] for u in model.users(state.load())], ["Мама"])
        self.assertEqual(self.tickets(), [])                         # the decrypted copy is gone with the import
        kept = util.read_text(self.smodel.path()) + self.log()
        for secret in (PW, text[-40:], TOKEN):
            self.assertNotIn(secret, kept)

    def test_open_changes_nothing_and_names_what_is_lost(self):
        text = self.copy_then_more()
        before = self.files_now()
        out = self.steps.run("backup-open", {"file": text, "password": PW}, self.w.runner)
        self.assertEqual(out["state"], "ok", out)
        self.assertEqual(self.files_now(), before)                     # nothing of the node changed
        d = out["data"]
        self.assertFalse(d["empty"])
        self.assertTrue(d["confirm"])
        lost = dict((x["what"], x) for x in d["lost"])
        self.assertEqual(lost["users"]["n"], 1)
        self.assertIn("Петя", lost["users"]["text"])
        self.assertIn("Ноутбук (Петя)", lost["devices"]["text"])
        self.assertIn("tcp 8443 → 198.51.100.23:22", lost["forwards"]["text"])
        self.assertEqual(lost["keys"]["text"], "1 выход: Berlin")      # VLESS_A is in the copy too: not lost
        self.assertEqual(lost["subs"]["text"], "1 подписка выходов: подписка 203.0.113.77")
        changed = dict((x["what"], x["text"]) for x in d["changed"])
        self.assertIn("напрямую → через выходы", changed["route"])
        acts = dict((r["part"], r["action"]) for r in d["parts"])
        self.assertEqual(acts["gateway"], "replace")
        # the page gets names and counts — no key, no token, no address of a subscription, no password
        blob = json.dumps(out, ensure_ascii=False)
        for secret in (PW, TOKEN, PAT, "SECRETPATH", "SECRET2", "12121212", VLESS_A, VLESS_B):
            self.assertNotIn(secret, blob)
        # the decrypted copy waits in /run (0600), without the password and the file
        from kaskad.setup import jobs
        (name,) = self.tickets()
        p = os.path.join(jobs.run_dir(), name)
        self.assertEqual(os.stat(p).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(jobs.run_dir()).st_mode & 0o777, 0o700)
        raw = util.read_text(p)
        self.assertNotIn(PW, raw)
        self.assertNotIn(text[-60:].strip(), raw)
        # «Перенести» without «да» on a node with something to lose — refused, nothing changed
        out = self.steps.run("import-file", {"kind": "backup", "ticket": d["ticket"]}, self.w.runner)
        self.assertEqual(out["error"]["code"], "confirm")
        self.assertEqual(self.files_now(), before)
        out = self.steps.run("import-file", {"kind": "backup", "ticket": d["ticket"], "confirm": True}, self.w.runner)
        self.assertIn(out["state"], ("ok", "partial"), out)
        st = state.load()
        self.assertEqual([u["name"] for u in model.users(st)], ["Мама"])
        self.assertEqual([f["port"] for f in st["forwards"]], [443])
        self.assertEqual(self.tickets(), [])
        self.assertNotIn(PW, self.log() + util.read_text(self.smodel.path()))

    def test_a_change_after_the_preview_asks_to_look_again(self):
        text = self.copy_then_more()
        d = self.steps.run("backup-open", {"file": text, "password": PW}, self.w.runner)["data"]
        self.ok("gw", "user", "add", "Оля")                            # someone adds a person meanwhile
        out = self.steps.run("import-file", {"kind": "backup", "ticket": d["ticket"], "confirm": True}, self.w.runner)
        self.assertEqual(out["error"]["code"], "changed")
        self.assertIn("Оля", [u["name"] for u in model.users(state.load())])
        out = self.steps.run("backup-open", {"ticket": d["ticket"]}, self.w.runner)      # no password again
        self.assertIn("Оля", next(x for x in out["data"]["lost"] if x["what"] == "users")["text"])
        out = self.steps.run("import-file", {"kind": "backup", "ticket": d["ticket"], "confirm": True}, self.w.runner)
        self.assertIn(out["state"], ("ok", "partial"), out)
        self.assertEqual([u["name"] for u in model.users(state.load())], ["Мама"])

    def test_the_open_copy_expires_and_closes(self):
        text = self.copy_then_more()
        d = self.steps.run("backup-open", {"file": text, "password": PW}, self.w.runner)["data"]
        before = self.files_now()
        with mock.patch.object(self.steps, "_now", lambda: time.time() + self.steps.BACKUP_TTL + 5):
            out = self.steps.run("import-file", {"kind": "backup", "ticket": d["ticket"], "confirm": True},
                                 self.w.runner)
        self.assertEqual(out["error"]["code"], "expired")
        self.assertEqual(self.tickets(), [])
        self.assertEqual(self.files_now(), before)
        for bad in ("../../etc/passwd", "x" * 32, None):
            out = self.steps.run("backup-open", {"ticket": bad} if bad else {"ticket": "0" * 31}, self.w.runner)
            self.assertEqual(out["error"]["code"], "expired")
        self.steps.run("backup-open", {"file": text, "password": PW}, self.w.runner)
        self.assertEqual(len(self.tickets()), 1)
        self.assertEqual(self.steps.run("backup-open", {"drop": True}, self.w.runner)["state"], "ok")
        self.assertEqual(self.tickets(), [])

    def test_the_file_and_password_alone_never_apply(self):
        text = self.copy_then_more()
        before = self.files_now()
        out = self.steps.run("import-file", {"kind": "backup", "file": text, "password": PW}, self.w.runner)
        self.assertEqual(out["error"]["code"], "preview_first")
        self.assertEqual(self.files_now(), before)

    def test_the_page_reads_what_the_server_answers(self):
        """JS contract: «backupView» of setup.js reads only keys of the real answer of «backup-open»; the page sends
        the password only to «backup-open» and forgets it first; «Перенести» sends the ticket, never the file or the
        password; the mock answers in the same shape."""
        from tests import jsparts
        static = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib", "kaskad", "panel",
                              "static", "setup")
        with open(os.path.join(static, "setup.js"), encoding="utf-8") as f:
            js = f.read()
        text = self.copy_then_more()
        d = self.steps.run("backup-open", {"file": text, "password": PW}, self.w.runner)["data"]
        view = jsparts.function(js, "backupView")
        self.assertTrue(set(re.findall(r"\bb\.([a-zA-Z_]+)", view)) <= set(d), view)
        self.assertTrue(set(re.findall(r"\bp\.([a-zA-Z_]+)", view)) <= set(d["parts"][0]))
        self.assertTrue(set(re.findall(r"\bx\.([a-zA-Z_]+)", view)) <= set(d["lost"][0]) & set(d["changed"][0]))
        opener = jsparts.function(js, "openBackup")
        self.assertLess(opener.index('u.bakPw = ""'), opener.index('step("backup-open"'))
        self.assertIn('{ file: f.text, password: pw }', opener)
        go = jsparts.function(js, "applyBackup")
        self.assertIn('step("import-file", { kind: "backup", ticket: b.ticket, confirm: !!u.bakOk })', go)
        self.assertNotIn("password", go)
        self.assertNotRegex(js, r'kind: "backup", file:')                # the old one-step apply is gone
        if shutil.which("node"):
            script = (r"""globalThis.window = globalThis;
globalThis.location = new URL("http://127.0.0.1/setup/index.html?mock=1&lat=0&pw=1&here=1");
require(process.argv[1]);
const M = window.KASKAD_SETUP_MOCK;
const post = (p, b) => M.fetch("api/setup/" + p, {method: "POST", headers: {"X-Kaskad-Admin": "1"}, body: JSON.stringify(b)}).then((r) => r.json());
(async () => {
  const o = await post("step/backup-open", {file: "KASKAD-BACKUP demo", password: "demo-pass-1"});
  console.log(JSON.stringify({open: o}));
})().catch((e) => { console.error("FAIL", e); process.exit(1); });""")
            p = subprocess.run(["node", "-e", script, os.path.join(static, "dev-mock-setup.js")],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=60)
            self.assertEqual(p.returncode, 0, p.stderr)
            m = json.loads(p.stdout)
            self.assertEqual(sorted(m["open"]["data"]), sorted(d))
            self.assertEqual(sorted(m["open"]["data"]["lost"][0]), sorted(d["lost"][0]))
            self.assertEqual(sorted(m["open"]["data"]["parts"][0]), sorted(d["parts"][0]))

    def test_clean_server_only_when_the_node_is_empty(self):
        out = self.steps.run("scan", {}, self.w.runner)
        self.assertTrue(out["data"]["clean"], out["data"])
        self.assertEqual(out["data"]["here"]["items"], [])
        self.assertIn("Чистый сервер", out["text"])
        self.old_node()
        out = self.steps.run("scan", {}, self.w.runner)
        self.assertFalse(out["data"]["clean"])
        here = out["data"]["here"]
        self.assertFalse(here["empty"])
        whats = set(i["what"] for i in here["items"])
        self.assertTrue({"users", "devices", "inbounds", "keys", "subs", "forwards", "bot"} <= whats, whats)
        self.assertIn("1 пользователь", here["text"])
        self.assertIn("2 выхода", here["text"])
        self.assertTrue(out["text"].startswith("На сервере уже есть: "))
        for secret in (TOKEN, "SECRETPATH", VLESS_A):
            self.assertNotIn(secret, json.dumps(out, ensure_ascii=False))
        self.new_node()                                                # wiped again: clean again
        self.assertTrue(self.steps.run("scan", {}, self.w.runner)["data"]["clean"])

    def test_no_password_no_file(self):
        out = self.steps.run("backup", {}, self.w.runner)
        self.assertEqual((out["state"], out["error"]["field"]), ("error", "password"))
        out = self.steps.run("backup-open", {"password": PW}, self.w.runner)
        self.assertEqual(out["error"]["field"], "file")
        out = self.steps.run("backup-open", {"file": "KASKAD-BACKUP 1"}, self.w.runner)
        self.assertEqual(out["error"]["field"], "password")
