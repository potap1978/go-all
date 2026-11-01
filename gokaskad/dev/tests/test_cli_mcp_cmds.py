"""Commands of kaskadctl the MCP server needs (docs/k3/SPEC-mcp.md §3): ``gw exit-add`` / ``exit-preview`` (the key on
stdin, never argv), ``gw exit-del``, ``gw scheme``, ``gw issue --png`` and ``update check | run``."""

import base64
import io
import json
import os
import stat
import sys
import tempfile
from unittest import mock

from kaskad import cli_update, errors, paths, qr, sysrun
from kaskad.gw import exits, ops
from tests.fakes_gw import VLESS_A, VLESS_B
from tests.helpers import RootCase
from tests.test_gw_ops import CliGwCase


class ExitKeysCliTest(CliGwCase):
    def stdin(self, text):
        return mock.patch.object(sys, "stdin", io.StringIO(text))

    def script_calls(self, mode):
        return [(argv, inp) for argv, inp in self.w.runner.calls if argv[1:2] == [mode]]

    def test_add_reads_the_key_from_stdin_only(self):
        self.enabled_world()
        with self.stdin(VLESS_B + "\n"):
            rc, doc, err = self.run_json("gw", "exit-add")
        self.assertEqual(rc, 0, err)
        eid = exits.exit_id(VLESS_B)
        self.assertEqual([a["id"] for a in doc["added"]], [eid])
        (argv, inp), = self.script_calls("--egress-add")
        self.assertEqual(argv, [exits._script(None), "--egress-add"])
        self.assertEqual(inp.strip(), VLESS_B)
        secret = VLESS_B.split("@")[0][len("vless://"):]
        self.assertNotIn(secret, json.dumps(doc) + err + self.log())
        with self.stdin(VLESS_B + "\n"):                                        # the same key again: conflict
            rc, doc, _ = self.run_json("gw", "exit-add")
        self.assertEqual((rc, doc["ok"], doc["code"]), (errors.CONFLICT, False, errors.CONFLICT))

    def test_public_only_hands_the_script_text_not_a_link(self):
        from kaskad.gw import subfetch
        self.enabled_world()
        with self.stdin("https://sub.example/s\n"), \
                mock.patch.object(subfetch, "fetch", lambda url, *a, **kw: VLESS_B + "\nhttp://127.0.0.1/x\n"):
            rc, doc, err = self.run_json("gw", "exit-add", "--public-only")
        self.assertEqual(rc, 0, err)
        (argv, inp), = self.script_calls("--egress-add")
        self.assertEqual(inp.strip(), VLESS_B)                                   # ни одной ссылки скрипту
        with self.stdin("http://user:pw@127.0.0.1:8080/sub\n"):
            rc, doc, _ = self.run_json("gw", "exit-add", "--public-only")
        self.assertEqual(rc, errors.USAGE)
        self.assertEqual(len(self.script_calls("--egress-add")), 1)

    def test_empty_stdin_is_refused_and_runs_nothing(self):
        self.enabled_world()
        with self.stdin("  \n"):
            rc, doc, _ = self.run_json("gw", "exit-add")
        self.assertEqual(rc, errors.USAGE)
        self.assertEqual(self.script_calls("--egress-add"), [])

    def test_preview_changes_nothing(self):
        self.enabled_world()
        before = exits.pool_lines()
        with self.stdin(VLESS_A + "\n" + VLESS_B + "\n"):
            rc, doc, err = self.run_json("gw", "exit-preview")
        self.assertEqual(rc, 0, err)
        self.assertEqual([x["in_pool"] for x in doc["items"]], [True, False])
        self.assertEqual(exits.pool_lines(), before)
        self.assertEqual(self.script_calls("--egress-add"), [])
        self.assertNotIn("11111111-2222", json.dumps(doc))

    def test_delete(self):
        self.enabled_world()
        rc, doc, err = self.run_json("gw", "exit-del", self.vid)
        self.assertEqual(rc, 0, err)
        self.assertEqual(doc["id"], self.vid)
        self.assertNotIn(self.vid, [i for i, _ in exits.line_ids()])
        rc, doc, _ = self.run_json("gw", "exit-del", self.vid)                  # gone already
        self.assertEqual((rc, doc["data"].get("gone")), (errors.CONFLICT, True))


class SchemeCliTest(CliGwCase):
    def test_people_devices_routes_without_secrets(self):
        self.enabled_world()
        self.ok("gw", "inbound", "install", "wg")
        self.ok("gw", "user", "add", "Мама")
        self.ok("gw", "device", "add", "Мама", "Телефон", "--access", "wg")
        self.ok("gw", "user", "route", "Мама", "exit:" + self.vid)
        rc, doc, err = self.run_json("gw", "scheme")
        self.assertEqual(rc, 0, err)
        self.assertEqual(doc["default_route"]["via"], "pool")
        (u,) = doc["people"]
        self.assertEqual(u["name"], "Мама")
        (d,) = u["devices"]
        self.assertEqual((d["name"], d["access"]), ("Телефон", ["wg"]))
        self.assertEqual((d["route"]["via"], d["route"]["from"]), ("exit:" + self.vid, "user"))
        self.assertIn(d["rules"]["profile"], ("ru_direct", "all_exit"))
        self.assertEqual([r["kind"] for r in doc["inbounds"]], ["wg"])
        self.assertIn(self.vid, [e["id"] for e in doc["exits"]])
        v = next(e for e in doc["exits"] if e["id"] == self.vid)     # the resource of the MCP scheme (REV-s1-t1 З-4)
        self.assertEqual((v["kind"], v["proto"], v["role"]), ("xray", "vless", "abroad"))
        self.assertNotIn("DEVKEY", json.dumps(doc))
        out, _ = self.ok("gw", "scheme")
        self.assertIn("Телефон", out)


FAKE_QRENCODE = """#!%s
import hashlib, sys
data = sys.stdin.buffer.read()
if b"BROKEN" in data:
    sys.stderr.write("Failed to encode the input data\\n")
    sys.exit(1)
sys.stdout.buffer.write(b"\\x89PNG\\r\\n\\x1a\\n" + hashlib.sha256(data).hexdigest().encode())
"""


class QrPngTest(RootCase):
    def fake(self):
        d = tempfile.mkdtemp(dir=self.root)
        exe = os.path.join(d, "qrencode")
        with open(exe, "w") as f:
            f.write(FAKE_QRENCODE % sys.executable)
        os.chmod(exe, os.stat(exe).st_mode | stat.S_IEXEC)
        return exe

    def test_records_qr_and_link_get_pngs(self):
        exe = self.fake()
        res = {"items": [{"format": "qr", "body": ["vpn://AAA", "vpn://BBB"]}, {"format": "link", "body": "vless://x"},
                         {"format": "conf", "body": "[Interface]"}, {"format": "qr", "body": "BROKEN"}]}
        with mock.patch.object(qr, "qrencode_path", lambda: exe):
            qr.add_pngs(res)
        a, b, c, d = res["items"]
        self.assertEqual(len(a["png_b64"]), 2)
        self.assertTrue(base64.b64decode(a["png_b64"][0]).startswith(b"\x89PNG"))
        self.assertEqual(len(b["png_b64"]), 1)
        self.assertNotIn("png_b64", c)
        self.assertIn("qrencode не нарисовал", d["png_error"])
        with mock.patch.object(qr, "qrencode_path", lambda: None):
            res = qr.add_pngs({"items": [{"format": "qr", "body": "vpn://A"}]})
        self.assertIn("qrencode", res["items"][0]["png_error"])
        self.assertNotIn("png_b64", res["items"][0])

    def test_payload_goes_on_stdin(self):
        seen = []

        class P(object):
            returncode, stdout, stderr = 0, b"\x89PNG..", b""

        def run(argv, input=None, **kw):
            seen.append((argv, input))
            return P()
        with mock.patch.object(qr, "qrencode_path", lambda: "/usr/bin/qrencode"), \
                mock.patch.object(qr.subprocess, "run", run):
            qr.png("vpn://SECRET")
        (argv, inp), = seen
        self.assertNotIn("vpn://SECRET", " ".join(argv))
        self.assertEqual(inp, b"vpn://SECRET")


class IssuePngCliTest(CliGwCase):
    def test_flag_adds_pictures(self):
        self.enabled_world(pool=False)
        res = {"ok": True, "name": "Т", "kind": "awg2", "os": "ios", "howto": [], "available": ["awg2"],
               "items": [{"format": "qr", "title": "QR", "body": ["vpn://A"]}]}
        calls = []

        def add(r):
            calls.append(r)
            r["items"][0]["png_b64"] = ["eA=="]
            return r
        with mock.patch.object(ops, "issue", lambda *a, **kw: json.loads(json.dumps(res))), \
                mock.patch.object(qr, "add_pngs", add):
            rc, doc, _ = self.run_json("gw", "issue", "d-00000001", "--png")
            self.assertEqual(doc["items"][0]["png_b64"], ["eA=="])
            rc, doc, _ = self.run_json("gw", "issue", "d-00000001")
            self.assertNotIn("png_b64", doc["items"][0])
        self.assertEqual(len(calls), 1)


class UpdateCliTest(RootCase):
    def setUp(self):
        super().setUp()
        self.runner = sysrun.FakeRunner()
        self._old = sysrun._default
        sysrun._default = self.runner
        self.write("/usr/local/bin/gokaskad", '#!/bin/bash\nKASKAD_VERSION="3.2.22"\n', mode=0o755)

    def tearDown(self):
        sysrun._default = self._old
        super().tearDown()

    def run_json(self, *argv):
        from kaskad import cli
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "stdout", out), mock.patch.object(sys, "stderr", err):
            rc = cli.main(list(argv) + ["--json"])
        return rc, json.loads(out.getvalue())

    def test_check(self):
        self.runner.on([paths.command(), "--update-check"],
                       '{"ok":true,"current":"3.2.22","latest":"3.2.23","branch":"test","available":true,'
                       '"same_file":false}\n')
        rc, doc = self.run_json("update", "check")
        self.assertEqual((rc, doc["available"], doc["latest"]), (0, True, "3.2.23"))
        self.runner._handlers = []
        self.runner.on([paths.command(), "--update-check"], (1, '{"ok":false,"reason":"fetch","text":"нет сети"}\n', ""))
        rc, doc = self.run_json("update", "check")
        self.assertEqual((rc, doc["ok"], doc["error"]), (errors.ERROR, False, "нет сети"))

    def test_check_auto_branch_dev_is_said(self):
        """3.2.23: the branch chosen by itself (main → test → dev) and written — in the answer and in the text."""
        self.runner.on([paths.command(), "--update-check"],
                       '{"ok":true,"current":"3.2.22","latest":"3.2.23","branch":"dev","auto":true,"remembered":true,'
                       '"available":true,"same_file":false}\n')
        from kaskad import cli
        out = io.StringIO()
        with mock.patch.object(sys, "stdout", out):
            rc = cli.main(["update", "check"])
        self.assertEqual(rc, 0)
        self.assertIn("ветка dev, выбрана сама", out.getvalue())
        self.assertIn("Ветка обновлений: dev (тестовые сборки) — записана автоматически", out.getvalue())
        rc, doc = self.run_json("update", "check")
        self.assertEqual((doc["branch"], doc["auto"], doc["remembered"]), ("dev", True, True))

    def test_run_from_k3_notes_the_branch(self):
        def upd(argv, inp):
            self.write("/usr/local/bin/gokaskad", '#!/bin/bash\nKASKAD_VERSION="3.2.23"\n', mode=0o755)
            return 0, ("[OK] Обновлено до v3.2.23 (ветка k3).\n"
                       "Ветка обновлений: k3 (тестовые сборки) — записана автоматически.\nНажмите Enter..."), ""
        self.runner.on([paths.command(), "--self-update"], upd)
        rc, doc = self.run_json("update", "run")
        self.assertEqual((rc, doc["updated"]), (0, True))
        self.assertTrue(any(n.startswith("Ветка обновлений: k3") for n in doc["notes"]), doc["notes"])

    def test_run_from_dev_notes_the_branch(self):
        """3.3.4: install.sh пишет «dev» — строка о записанной ветке та же, разбор её узнаёт (и старую «k3» выше)."""
        def upd(argv, inp):
            self.write("/usr/local/bin/gokaskad", '#!/bin/bash\nKASKAD_VERSION="3.3.4"\n', mode=0o755)
            return 0, ("[OK] Обновлено до v3.3.4 (ветка dev).\n"
                       "Ветка обновлений: dev (тестовые сборки) — записана автоматически.\nНажмите Enter..."), ""
        self.runner.on([paths.command(), "--self-update"], upd)
        rc, doc = self.run_json("update", "run")
        self.assertEqual((rc, doc["updated"]), (0, True))
        self.assertTrue(any(n.startswith("Ветка обновлений: dev") for n in doc["notes"]), doc["notes"])

    def test_run_updated(self):
        def upd(argv, inp):
            self.write("/usr/local/bin/gokaskad", '#!/bin/bash\nKASKAD_VERSION="3.2.23"\n', mode=0o755)
            return 0, "\x1b[0;32m[OK] Обновлено до v3.2.23 (ветка test).\x1b[0m\nПерезапущено: bot\nНажмите Enter...", ""
        self.runner.on([paths.command(), "--self-update"], upd)
        rc, doc = self.run_json("update", "run")
        self.assertEqual((rc, doc["updated"], doc["from"], doc["to"]), (0, True, "3.2.22", "3.2.23"))
        self.assertIn("Обновлено до v3.2.23", doc["text"])

    def test_run_nothing_and_rollback(self):
        self.runner.on([paths.command(), "--self-update"],
                       "\x1b[0;32mУ вас уже последняя версия ветки test: v3.2.22.\x1b[0m\nНажмите Enter...")
        rc, doc = self.run_json("update", "run")
        self.assertEqual((rc, doc["updated"]), (0, False))
        self.runner._handlers = []
        self.runner.on([paths.command(), "--self-update"], "Бот не поднялся на новой версии — откат.\n")
        rc, doc = self.run_json("update", "run")
        self.assertEqual((rc, doc["code"]), (errors.ROLLED_BACK, errors.ROLLED_BACK))

    def test_failure_says_why(self):
        self.runner.on([paths.command(), "--self-update"],
                       (0, "[*] Загрузка обновления (ветка main)...\nНе удалось скачать. Репозиторий приватный.\n"
                           "[ERROR] Не удалось обновить.\nНажмите Enter...", ""))
        rc, doc = self.run_json("update", "run")
        self.assertEqual(rc, errors.ERROR)
        self.assertIn("Не удалось скачать install.sh ветки main", doc["error"])
        self.assertIn("8 → 6", doc["error"])
        self.assertEqual(cli_update.file_version(paths.command()), "3.2.22")


class UpdateMarksTest(UpdateCliTest):
    """3.3.2: gokaskad --self-update runs with K3_MARKS=1 and the result is told by its lines «@K3:…», never by its
    words (any language); a gokaskad without marks — the Russian parse of before (UpdateCliTest)."""

    def answer(self, out, new=None, rc=0):
        def upd(argv, inp):
            if new:
                self.write("/usr/local/bin/gokaskad", '#!/bin/bash\nKASKAD_VERSION="%s"\n' % new, mode=0o755)
            return rc, out, "Press Enter..."
        self.runner._handlers = []
        self.runner.on([paths.command(), "--self-update"], upd)

    def test_marks_are_asked_for(self):
        self.answer("@K3:update_latest:3.2.22:test\n")
        self.run_json("update", "run")
        self.assertIn(([paths.command(), "--self-update"], {"K3_MARKS": "1"}), self.runner.envs)

    def test_done_in_english(self):
        from kaskad.i18n import use_lang
        self.answer("[OK] Updated to v3.3.2 (branch k3).\n@K3:update_done:3.3.2:k3\n@K3:update_branch_learned:k3\n"
                    "@K3:update_restarted:bot:monitor\n@K3:update_warn:core_not_ready\n", new="3.3.2")
        with use_lang("en"):
            rc, doc = self.run_json("update", "run")
        self.assertEqual((rc, doc["updated"], doc["from"], doc["to"], doc["partial"]), (0, True, "3.2.22", "3.3.2", True))
        self.assertTrue(doc["text"].startswith("Updated to v3.3.2 (branch k3). Restarted: bot, monitor."), doc["text"])
        self.assertIn("didn’t get ready", doc["warnings"][0])
        self.assertIn("saved automatically", doc["notes"][0])

    def test_latest_nothing_rollback_fetch_error(self):
        self.answer("Some words\n@K3:update_latest:3.2.22:test\n")
        rc, doc = self.run_json("update", "run")
        self.assertEqual((rc, doc["updated"], doc["text"]), (0, False, "У вас уже последняя версия ветки test: v3.2.22."))
        self.answer("@K3:update_nothing:3.2.20:main\n")
        rc, doc = self.run_json("update", "run")
        self.assertEqual((rc, doc["updated"]), (0, False))
        self.assertIn("v3.2.20", doc["text"])
        self.answer("@K3:update_rolled_back:bot\n")
        rc, doc = self.run_json("update", "run")
        self.assertEqual((rc, doc["code"]), (errors.ROLLED_BACK, errors.ROLLED_BACK))
        self.answer("@K3:update_fetch_failed:k3\n[ERROR] Could not update.\n@K3:update_error:fetch:k3\n")
        rc, doc = self.run_json("update", "run")
        self.assertEqual((rc, doc["data"]["reason"]), (errors.ERROR, "fetch"))
        self.assertIn("ветки k3", doc["error"])
        self.answer("[ERROR] The file of branch x is not Kaskad.\n@K3:update_error:not_kaskad:x\n")
        rc, doc = self.run_json("update", "run")
        self.assertEqual((rc, doc["error"], doc["data"]["reason"]),
                         (errors.ERROR, "The file of branch x is not Kaskad.", "not_kaskad"))
        self.assertEqual(cli_update.file_version(paths.command()), "3.2.22")

class InstallNewOnlyTest(CliGwCase):
    def test_new_only_refuses_a_reinstall(self):
        self.enabled_world(pool=False)
        rc, doc, err = self.run_json("gw", "inbound", "install", "wg", "--new-only")
        self.assertEqual(rc, 0, err)
        n = len([c for c, _ in self.w.runner.calls])
        rc, doc, _ = self.run_json("gw", "inbound", "install", "wg", "--new-only")
        self.assertEqual((rc, doc["code"], doc["data"]["installed"]), (errors.CONFLICT, errors.CONFLICT, True))
        self.assertIn("Переустановка — без --new-only", doc["error"])        # агенту MCP заменит на inbound_reinstall
        self.assertEqual(len([c for c, _ in self.w.runner.calls]), n)          # nothing ran
        rc, doc, err = self.run_json("gw", "inbound", "install", "wg")         # a reinstall without the flag
        self.assertEqual(rc, 0, err)


ED = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHYQVexUVwrWE3ClSz+u3fw9zsSdIB/hQ6bgD7OtwSt8"
ED2 = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIDJ9d2nBfuBMfY6m6VVm0r6S3pqf3CUqvMsDVpIpmdRf"
ED_FP = "SHA256:oqSYAGlvmpT6i6Ppmw12El4pvf1mwwM8IvTl54P89Ok"


class AgentKeysTest(RootCase):
    """kaskadctl mcp key add|list|del: ключ ИИ-агента в authorized_keys — только с принудительной командой kaskad-mcp."""

    def run_json(self, *argv, stdin=""):
        from kaskad import cli
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "stdout", out), mock.patch.object(sys, "stderr", err), \
                mock.patch.object(sys, "stdin", io.StringIO(stdin)):
            rc = cli.main(list(argv) + ["--json"])
        return rc, json.loads(out.getvalue())

    def keys_file(self):
        return self.read("/root/.ssh/authorized_keys")

    def test_add_list_del_only_our_lines(self):
        owner = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOwner owner@pc\n# заметка владельца\n"
        self.write("/root/.ssh/authorized_keys", owner)
        rc, doc = self.run_json("mcp", "key", "add", "--label", "claude", "--host", "203.0.113.10", stdin=ED + " me@pc\n")
        self.assertEqual(rc, 0, doc)
        self.assertEqual(doc["fingerprint"], ED_FP)                               # как у ssh-keygen -l
        self.assertIn("root@203.0.113.10 kaskad-mcp", doc["connect"]["claude_code"])
        text = self.keys_file()
        self.assertTrue(text.startswith(owner))
        line = text.splitlines()[-1]
        self.assertEqual(line, 'restrict,command="/usr/local/bin/kaskad-mcp",no-pty,no-port-forwarding,'
                               'no-agent-forwarding,no-X11-forwarding,no-user-rc %s kaskad-mcp-agent:claude' % ED)
        self.assertEqual(oct(os.stat(self.path("/root/.ssh/authorized_keys")).st_mode & 0o777), "0o600")
        rc, doc = self.run_json("mcp", "key", "list")
        self.assertEqual([(k["fingerprint"], k["label"]) for k in doc["keys"]], [(ED_FP, "claude")])
        rc, doc = self.run_json("mcp", "key", "add", stdin=ED + "\n")               # тот же ключ ещё раз
        self.assertEqual(rc, errors.CONFLICT)
        # строка с нашей меткой, но чужими опциями — не наша: её не показывают и не удаляют
        with open(self.path("/root/.ssh/authorized_keys"), "a") as f:
            f.write("%s kaskad-mcp-agent:fake\n" % ED2)
        rc, doc = self.run_json("mcp", "key", "list")
        self.assertEqual(len(doc["keys"]), 1)
        rc, doc = self.run_json("mcp", "key", "del", ED_FP)
        self.assertEqual((rc, doc["removed"]), (0, 1))
        self.assertEqual(self.keys_file(), owner + "%s kaskad-mcp-agent:fake\n" % ED2)
        rc, doc = self.run_json("mcp", "key", "del", ED_FP)
        self.assertEqual(rc, errors.ERROR)
        rc, doc = self.run_json("mcp", "key", "del", "not-a-fingerprint")
        self.assertEqual(rc, errors.USAGE)

    def test_refusals(self):
        self.write("/root/.ssh/authorized_keys", ED + " root-login\n")
        bad = ["", "мусор", "ssh-ed25519 AAAA", "ssh-ed25519 !!!!", ED + "\n" + ED2,
               'command="/bin/sh" ' + ED2, 'restrict,command="/usr/local/bin/kaskad-mcp" ' + ED2,
               "from=\"1.2.3.4\" " + ED2, "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXk\n-----END OPENSSH PRIVATE KEY-----",
               "ssh-dss AAAAB3NzaC1kc3MAAACBAP", "ssh-rsa AAAAC3NzaC1lZDI1NTE5AAAAIHYQVexUVwrWE3ClSz+u3fw9zsSdIB/hQ6bgD7OtwSt8",
               "x" * 20000]
        for text in bad:
            rc, doc = self.run_json("mcp", "key", "add", stdin=text)
            self.assertEqual(rc, errors.USAGE, (text[:40], doc))
        rc, doc = self.run_json("mcp", "key", "add", stdin=ED)                      # уже полный вход root
        self.assertEqual(rc, errors.CONFLICT)
        self.assertIn("полный вход root", doc["error"])
        rc, doc = self.run_json("mcp", "key", "add", stdin='command="/bin/sh -i" ' + ED2)
        self.assertIn("свои опции", doc["error"])
        rc, doc = self.run_json("mcp", "key", "add", "--label", "bad label!", stdin=ED2)
        self.assertEqual(rc, errors.USAGE)
        self.assertEqual(self.keys_file(), ED + " root-login\n")

    def test_short_rsa_is_refused(self):
        import struct as st
        blob = st.pack(">I", 7) + b"ssh-rsa" + st.pack(">I", 3) + b"\x01\x00\x01" + st.pack(">I", 129) + b"\x00" + b"\xff" * 128
        rc, doc = self.run_json("mcp", "key", "add", stdin="ssh-rsa " + base64.b64encode(blob).decode())
        self.assertEqual(rc, errors.USAGE)
        self.assertIn("2048", doc["error"])


class SubscriptionFetchTest(RootCase):
    """gw exit-add --public-only: ссылка подписки от агента скачивается безопасно (стенд T-M6, F1)."""

    RESOLVE = {"evil.example": ["10.0.0.5"], "good.example": ["8.8.8.8"], "meta.example": ["169.254.169.254"],
               "mixed.example": ["8.8.8.8", "127.0.0.1"], "httpbin.example": ["8.8.4.4"]}

    def resolve(self, host):
        if host not in self.RESOLVE:
            raise OSError("no such host")
        return self.RESOLVE[host]

    def test_internal_and_tricky_links_are_refused(self):
        from kaskad.gw import subfetch
        for url in ("https://127.0.0.1/sub", "http://[::1]:8080/s", "https://evil.example/s", "https://meta.example/x",
                    "http://user:pw@127.0.0.1:8080/sub", "http://user:pw@good.example/sub", "http://good.example@127.0.0.1/",
                    "https://mixed.example/s", "http://[::ffff:127.0.0.1]/", "http://2130706433/", "http://0.0.0.0/",
                    "ftp://good.example/s", "file:///etc/passwd", "http://%31%32%37.0.0.1/", "http://100.100.100.200/"):
            with self.assertRaises(errors.KaskadError, msg=url) as cm:
                subfetch.check_url(url, resolve=self.resolve)
            self.assertEqual(cm.exception.code, errors.USAGE, url)

    def test_connects_to_the_checked_address_and_checks_each_redirect(self):
        from kaskad.gw import subfetch
        seen = []

        def opener(scheme, host, port, path, ip):
            seen.append((scheme, host, port, path, ip))
            if host == "httpbin.example":
                return 302, "http://127.0.0.1:8080/sub", b""
            return 200, None, b"vless://x@8.8.8.8:443\nhttps://evil.example/next\n"
        with self.assertRaises(errors.KaskadError):
            subfetch.fetch("https://httpbin.example/redirect-to?url=x", resolve=self.resolve, opener=opener)
        self.assertEqual(seen, [("https", "httpbin.example", 443, "/redirect-to?url=x", "8.8.4.4")])
        text = subfetch.expand("vless://a@8.8.8.8:443\nhttps://good.example/sub", resolve=self.resolve, opener=opener)
        self.assertEqual(text, "vless://a@8.8.8.8:443\nvless://x@8.8.8.8:443\n")     # ссылка из подписки — выброшена
        self.assertEqual(seen[-1][4], "8.8.8.8")                                    # соединение — с проверенным адресом

    def test_endless_redirects_and_big_answers(self):
        from kaskad.gw import subfetch
        with self.assertRaises(errors.KaskadError):
            subfetch.fetch("https://good.example/a", resolve=self.resolve,
                           opener=lambda *a: (302, "https://good.example/a", b""))
        with self.assertRaises(errors.KaskadError):
            subfetch.fetch("https://good.example/a", resolve=self.resolve,
                           opener=lambda *a: (200, None, b"x" * (subfetch.MAX_BYTES + 1)))


class ForwardNewOnlyTest(CliGwCase):
    def test_new_only_refuses_a_replacement(self):
        from tests.fakes_nft import save_state
        save_state([])
        rc, doc, err = self.run_json("fwd", "add", "--proto", "tcp", "--port", "8443", "--to", "10.0.0.2",
                                     "--to-port", "443", "--new-only")
        self.assertEqual(rc, 0, err)
        rc, doc, _ = self.run_json("fwd", "add", "--proto", "tcp", "--port", "8443", "--to", "10.0.0.9",
                                   "--to-port", "443", "--new-only")
        self.assertEqual((rc, doc["data"]["exists"]["to"]), (errors.CONFLICT, "10.0.0.2"))
        rc, doc, err = self.run_json("fwd", "add", "--proto", "tcp", "--port", "8443", "--to", "10.0.0.9",
                                     "--to-port", "443")                       # без флага — замена, как было
        self.assertEqual((rc, doc["replaced"]["to"]), (0, "10.0.0.2"))


class NoExitsProblemTest(RootCase):
    """gw status / gw check: «нет интернета у устройств» — то же правило, что красная строка меню 17 (T-M6, F3)."""

    def st(self, route=None, enabled=True):
        return {"gateway": {"enabled": enabled, "users": [], "devices": [],
                            "routes": {"default": route or {"via": "pool", "fail": "block"}}}}

    def test_rule(self):
        dead = [{"id": "e-0123456789", "usable": False}, {"id": "e-9876543210", "usable": False}]
        alive = [{"id": "e-0123456789", "usable": True}]
        p = ops.no_exits_problem(self.st(), dead, live_age=5)
        self.assertEqual(p["kind"], "no_exits")
        self.assertIn("Нет интернета у устройств", p["detail"])
        self.assertIsNotNone(ops.no_exits_problem(self.st(), [], live_age=5))                  # пул пуст
        self.assertIsNone(ops.no_exits_problem(self.st(), alive, live_age=5))
        self.assertIsNone(ops.no_exits_problem(self.st({"via": "pool", "fail": "direct"}), dead, live_age=5))
        self.assertIsNone(ops.no_exits_problem(self.st({"via": "direct", "fail": "block"}), dead, live_age=5))
        self.assertIsNone(ops.no_exits_problem(self.st(enabled=False), dead, live_age=5))
        self.assertIsNone(ops.no_exits_problem(self.st(), dead, live_age=None))               # сборщик молчит
        one = self.st({"via": "exit:e-9876543210", "fail": "block"})
        # этап 1 (SPEC-stage1-exits §1.3 отличие 4, §2.2; REV-s1-t4 С1): «exit:E» с неработающим E идёт через другие
        # годные выходы его группы — живой выход той же группы держит интернет, тревоги нет (в 3.3.5 — была)
        self.assertIsNone(ops.no_exits_problem(one, alive + dead[1:], live_age=5))
        self.assertIsNotNone(ops.no_exits_problem(one, dead, live_age=5))                    # в группе ни одного
        self.assertIsNone(ops.no_exits_problem(one, [{"id": "e-9876543210", "usable": True}], live_age=5))
        grp = self.st({"via": "group:g-abc123", "fail": "block"})                           # своя группа: её выходы
        grp["gateway"]["exit_policy"] = {"groups": [
            {"id": "main", "role": "abroad", "tiers": [{"id": "t1", "exits": ["e-0123456789"]}]},
            {"id": "g-abc123", "role": "abroad", "tiers": [{"id": "t1", "exits": ["e-9876543210"]}]}]}
        self.assertIsNotNone(ops.no_exits_problem(grp, alive + dead[1:], live_age=5))       # жив выход не той группы
        self.assertIsNone(ops.no_exits_problem(grp, [{"id": "e-9876543210", "usable": True}], live_age=5))

    def test_menu_rule_and_core_rule_agree(self):
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "install.sh"),
                  encoding="utf-8") as f:
            text = f.read()
        body = text[text.index("k3_gw_dead() {"):]
        body = body[:body.index("\n}\n")]
        for part in ('($r.fail // "block") != "direct"', '$via == "pool" or ($via | startswith("exit:"))',
                     "<= 60"):
            self.assertIn(part, body)
