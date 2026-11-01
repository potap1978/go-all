"""3.3.2 E2, group «cli/net»: kaskadctl's own words (cli, cli_net, cli_fwd, cli_update), forwards, units, conflicts,
netcheck and net.socks in English under ``use_lang("en")`` — and the Russian text as before by default."""

import argparse
import os
import re
import stat
import sys
from unittest import mock

from kaskad import cli, cli_update, compat, conflicts, errors, forwards, netcheck, nft, paths, sysrun, units
from kaskad.errors import KaskadError
from kaskad.i18n import use_lang
from kaskad.net import socks
from tests.fakes_nft import F443, F51820, SS_SSHD, World, save_state
from tests.helpers import RootCase
from tests.test_cli_fwd_net import CliCase

CYR = re.compile("[А-Яа-яЁё]")
MINE = ("net", "fwd", "update")


def _subparsers(parser):
    for a in parser._actions:
        if isinstance(a, argparse._SubParsersAction):
            return a
    return None


def _helps(parser):
    """The help text of the parser and of every parser under it."""
    out = [parser.format_help()]
    sub = _subparsers(parser)
    if sub is not None:
        for p in sub.choices.values():
            out.extend(_helps(p))
    return out


class HelpTest(RootCase):
    def test_help_of_my_groups_is_english(self):
        with use_lang("en"):
            parser = cli.build_parser()
            groups = _subparsers(parser).choices
            texts = _helps(groups["version"])
            for name in MINE:
                texts.extend(_helps(groups[name]))
            top = parser.format_help()
        for t in texts:
            self.assertIsNone(CYR.search(t), t)
        self.assertIn("forwarding engine (nftables)", top)
        self.assertIn("port forwards (nftables engine)", top)
        self.assertIn("update Kaskad (as menu 11)", top)
        self.assertIn("machine-readable output", texts[0])
        joined = "\n".join(texts)
        for words in ("COMMAND", "check the forwarding engine (watchdog)", "destination IPv4 address",
                      "is there a new version (changes nothing)"):
            self.assertIn(words, joined)

    def test_help_russian_by_default(self):
        groups = _subparsers(cli.build_parser()).choices
        self.assertIn("проверить движок (сторож)", "\n".join(_helps(groups["net"])))
        self.assertIn("КОМАНДА", groups["fwd"].format_help())


class CliEnTest(CliCase):
    def en(self, *argv):
        with use_lang("en"):
            rc, out, err = self.run_cli(*argv)
        self.assertIsNone(CYR.search(out + err), out + err)
        return rc, out, err

    def test_error_and_conflict(self):
        save_state([F443], engine="legacy")
        rc, out, err = self.en("fwd", "list")
        self.assertEqual((rc, out), (errors.UNSUPPORTED, ""))
        self.assertEqual(err, "Error: Forwards are run by the legacy forwarding engine (iptables); this command is for the "
                              "nftables engine.\n")
        save_state()
        self.w.ss = SS_SSHD
        rc, out, err = self.en("fwd", "add", "--proto", "tcp", "--port", "22", "--to", "203.0.113.5",
                               "--to-port", "22")
        self.assertEqual(rc, errors.CONFLICT)
        self.assertEqual(err, "Error: Port 22/tcp is in use: the SSH server listens here (sshd) — the forward would cut "
                              "off access to this server. To add it anyway — use --force.\n")
        # Russian by default — byte for byte as before
        rc, out, err = self.run_cli("fwd", "add", "--proto", "tcp", "--port", "22", "--to", "203.0.113.5",
                                    "--to-port", "22")
        self.assertEqual(err, "Ошибка: Порт 22/tcp занят: здесь слушает SSH-сервер (sshd) — проброс отрежет вход на "
                              "этот сервер. Добавить всё равно — с --force.\n")

    def test_fwd_text(self):
        save_state([dict(F443, name="Vienna")])                     # a name is data: «Вена» would stay Cyrillic
        rc, out, _ = self.en("fwd", "stats")
        self.assertEqual(rc, 0)
        self.assertIn("The kaskad table is not loaded — no counters", out)
        self.assertIn("Forward", out)
        self.assertIn("Traffic", out)
        self.assertEqual(self.en("fwd", "del", "--proto", "tcp", "--port", "443"), (0, "Forward deleted.\n", ""))
        rc, out, err = self.en("fwd", "del", "--proto", "tcp", "--port", "443")
        self.assertEqual(err, "Error: There is no forward tcp :443.\n")
        self.assertEqual(self.run_cli("fwd", "flush"), (0, "Удалено правил: 0.\n", ""))
        self.assertEqual(self.en("fwd", "flush"), (0, "Forwards deleted: 0.\n", ""))

    def test_net_status_and_check(self):
        self.applied()
        compat.ensure(self.w.runner)
        units.install(self.w.runner)
        rc, out, _ = self.en("net", "status")
        self.assertIn("engine: nftables", out)
        self.assertIn("kaskad table: present, forwards: 2, matches the settings", out)
        self.assertIn("kaskad-net-watch.timer: enabled, running", out)
        self.assertIn("kaskad-net-watch.service: by timer\n", out)
        self.assertEqual(self.en("net", "check"), (0, "Everything is fine.\n", ""))
        self.w.nft.flush_ruleset()
        rc, out, _ = self.en("net", "check")
        self.assertEqual(rc, 1)
        self.assertEqual(out, "Problems:\n  • there is no nftables table “kaskad” — forwards don’t work\n")
        rc, out, _ = self.en("net", "check", "--repair")
        self.assertIn(" — repaired", out)
        self.assertEqual(self.en("net", "save-counters"), (0, "Counters saved: 2.\n", ""))
        rc, out, _ = self.en("net", "teardown")
        self.assertEqual(out, "The nftables engine is removed: the kaskad table, the compat rule and the services are "
                              "deleted.\n")
        save_state(engine="legacy")
        self.assertEqual(self.en("net", "check"), (0, "The forwarding engine is iptables (legacy): nothing to check.\n",
                                                   ""))


V3 = '#!/bin/bash\nKASKAD_VERSION="3.0.1"\ncase "$1" in\n  *) : ;;\nesac\n'
FREE = "#!/bin/bash\n# Kaskad\nconfigure_custom_rule() { :; }\n"
T0 = 1790000000.0


class NetcheckEnTest(RootCase):
    def setUp(self):
        super().setUp()
        self.w = World()
        self.now = T0
        self._patch = mock.patch.object(netcheck, "_now", lambda: self.now)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        super().tearDown()

    def healthy(self):
        st = save_state((F443, F51820))
        nft.apply(st, self.w.runner)
        compat.ensure(self.w.runner)
        self.write("/opt/kaskad/bin/gokaskad", V3, 0o755)
        self.write("/usr/local/bin/gokaskad", V3, 0o755)

    def log(self):
        p = self.path("/var/log/kaskad.log")
        return self.read("/var/log/kaskad.log") if os.path.exists(p) else ""

    def test_details_and_journal_in_english(self):
        self.healthy()
        self.write("/usr/local/bin/gokaskad", FREE, 0o755)
        self.w.sysctl.values["net.ipv4.ip_forward"] = "0"
        with use_lang("en"):
            res = netcheck.check(self.w.runner, repair=False)
            details = [p["detail"] for p in res["problems"]]
            self.assertIn("net.ipv4.ip_forward = 0: the server doesn’t forward packets", details)
            self.assertIn("another version replaced the gokaskad command (free Kaskad)", details)
            res = netcheck.check(self.w.runner, repair=True)
        self.assertTrue(res["ok"], res)
        self.assertEqual(stat.S_IMODE(os.stat(self.path("/usr/local/bin/gokaskad")).st_mode), 0o755)
        log = self.log()
        self.assertIn("NET repair: net.ipv4.ip_forward was 0 — turned it on", log)
        self.assertIn("NET repair: another version replaced the gokaskad command — restored 3.x", log)
        self.assertIsNone(CYR.search(log), log)

    def test_repeats_one_and_many(self):
        self.healthy()
        for minutes, lang in ((0, "en"), (1, "en"), (61, "en"), (62, "en"), (63, "en"), (130, "en")):
            self.now = T0 + 60 * minutes
            compat.remove(self.w.runner)
            with use_lang(lang):
                netcheck.check(self.w.runner, repair=True)
        lines = [ln for ln in self.log().splitlines() if "compat rule" in ln]
        self.assertEqual(len(lines), 3, lines)
        self.assertTrue(lines[1].endswith("(1 more time since the last entry)"), lines[1])
        self.assertTrue(lines[2].endswith("(2 more times since the last entry)"), lines[2])

    def test_russian_as_before(self):
        self.healthy()
        os.unlink(self.path("/usr/local/bin/gokaskad"))
        res = netcheck.check(self.w.runner, repair=False)
        self.assertEqual([p["detail"] for p in res["problems"]], ["команду gokaskad подменила другая версия (команды нет)"])


class ConflictsTest(RootCase):
    def test_effects_ru_are_the_tails_others_cut(self):
        # site/ops.py, ipcert/http01.py and gw/ops.py cut these tails by their Russian words — still the same words
        tails = conflicts.effects()
        for t in (" — проброс отрежет вход на этот сервер", " — подключения к нему снаружи перестанут доходить",
                  " — проброс Kaskad его перекроет", " — проброс заберёт его клиентов"):
            self.assertIn(t, tails)
        with use_lang("en"):
            en = conflicts.effects()
        self.assertEqual(len(en), len(tails))
        for t in en:
            self.assertTrue(t.startswith(" — "), t)
            self.assertIsNone(CYR.search(t), t)

    def test_listener_and_message(self):
        w = World()
        w.ss = ('tcp   LISTEN 0      511          0.0.0.0:443        0.0.0.0:*    users:(("nginx",pid=9,fd=6))\n')
        with use_lang("en"):
            found = conflicts.check("tcp", 443, w.runner, env={}, st=save_state())
            text = conflicts.message("tcp", 443, found)
        self.assertEqual(found, [{"kind": "listener", "detail": "nginx listens on it — connections to it from outside "
                                                                "would stop getting through", "procs": ["nginx"],
                                  "pids": [9]}])
        self.assertTrue(text.startswith("Port 443/tcp is in use: nginx listens on it"), text)
        found = conflicts.check("tcp", 443, w.runner, env={}, st=save_state())
        self.assertEqual(found[0]["detail"], "его слушает nginx — подключения к нему снаружи перестанут доходить")


class SocksTest(RootCase):
    def test_errors_in_english(self):
        with use_lang("en"):
            for url, words in (("ftp://127.0.0.1:1", "the scheme ftp:// is not supported"),
                               ("socks5://u:p@127.0.0.1:1", "a proxy without a password is needed"),
                               ("socks5://:1080", "no proxy address")):
                with self.assertRaises(ValueError) as c:
                    socks.parse_proxy(url)
                self.assertIn(words, str(c.exception))
                self.assertIsNone(CYR.search(str(c.exception)))
            with self.assertRaises(socks.ProxyError) as c:
                socks.socks5_connect("127.0.0.1", 1, "example.com", 0)
            self.assertEqual(str(c.exception), "the destination port is outside 1–65535")
            with self.assertRaises(ValueError) as c:
                socks.connection("ftp", "example.com")
            self.assertEqual(str(c.exception), "the scheme 'ftp' is not supported")
        with self.assertRaises(ValueError) as c:
            socks.parse_proxy("ftp://127.0.0.1:1")
        self.assertEqual(str(c.exception), "BOT_PROXY: схема ftp:// не поддерживается — нужен socks5h://, socks5:// "
                                           "или http://")

    def test_reply_words(self):
        """A SOCKS5 refusal (REP 5) through a fake socket: the words of the code in the language of the call."""
        class Sock(object):
            def __init__(self):
                self.data = b"\x05\x00" + b"\x05\x05\x00\x01"

            def sendall(self, b):
                pass

            def recv(self, n):
                out, self.data = self.data[:n], self.data[n:]
                return out

            def close(self):
                pass
        for lang, words in (("en", "the SOCKS5 proxy didn’t connect to example.com:443: connection refused"),
                            ("ru", "прокси SOCKS5 не соединил с example.com:443: в соединении отказано")):
            with use_lang(lang), mock.patch.object(socks.socket, "create_connection", lambda *a, **kw: Sock()):
                with self.assertRaises(socks.ProxyError) as c:
                    socks.socks5_connect("127.0.0.1", 1080, "example.com", 443)
            self.assertEqual(str(c.exception), words)


class UpdateEnTest(RootCase):
    def setUp(self):
        super().setUp()
        self.runner = sysrun.FakeRunner()
        self._old = sysrun._default
        sysrun._default = self.runner

    def tearDown(self):
        sysrun._default = self._old
        super().tearDown()

    def test_old_command_and_texts(self):
        self.write("/usr/local/bin/gokaskad", '#!/bin/bash\nKASKAD_VERSION="2.13"\n', mode=0o755)
        with use_lang("en"):
            with self.assertRaises(KaskadError) as c:
                cli_update.script()
            self.assertEqual(c.exception.code, errors.UNSUPPORTED)
            self.assertIn("The gokaskad command here is Kaskad 2.13", str(c.exception))
            self.assertEqual(cli_update.branch_label({"branch": "k3", "auto": True}), "k3, chosen automatically")
        self.assertEqual(cli_update.branch_label({"branch": "k3", "auto": True}), "k3, выбрана сама")

    def test_check_text(self):
        self.write("/usr/local/bin/gokaskad", '#!/bin/bash\nKASKAD_VERSION="3.2.22"\n', mode=0o755)
        self.runner.on([paths.command(), "--update-check"],
                       '{"ok":true,"current":"3.2.22","latest":"3.2.23","branch":"dev","auto":true,"remembered":true,'
                       '"available":true,"same_file":false}\n')
        import io
        out = io.StringIO()
        with use_lang("en"), mock.patch.object(sys, "stdout", out):
            rc = cli.main(["update", "check"])
        self.assertEqual(rc, 0)
        self.assertEqual(out.getvalue(), "A new version is available: 3.2.23 (now 3.2.22, branch dev, chosen "
                                         "automatically). Update: kaskadctl update run or menu 11. Update branch: dev "
                                         "(test builds) — saved automatically.\n")
        # the script answers in the language of the request: its refusal («no network…») is its own text (stand T-334)
        self.assertIn(([paths.command(), "--update-check"], {"KASKAD_LANG": "en"}), self.runner.envs)

    def test_run_failure_in_english(self):
        self.write("/usr/local/bin/gokaskad", '#!/bin/bash\nKASKAD_VERSION="3.2.22"\n', mode=0o755)
        self.runner.on([paths.command(), "--self-update"],
                       (0, "[*] Загрузка обновления (ветка main)...\nНе удалось скачать. Репозиторий приватный.\n", ""))
        with use_lang("en"):
            with self.assertRaises(KaskadError) as c:
                cli_update.run(self.runner)
        self.assertEqual(str(c.exception), "Couldn’t download install.sh of the main branch: no network, or a GitHub "
                                           "key is needed (SSH menu gokaskad → 8 → 6). Nothing was changed.")


class ForwardsUnitsTest(RootCase):
    def test_messages(self):
        with use_lang("en"):
            with self.assertRaises(KaskadError) as c:
                forwards.require_nft({"engine": "legacy"})
            self.assertNotRegex(str(c.exception), CYR)
            runner = sysrun.FakeRunner()
            runner.on(["systemctl", "daemon-reload"], (1, "", ""))
            with self.assertRaises(KaskadError) as c:
                units._systemctl(runner, "daemon-reload")
            self.assertEqual(str(c.exception), "systemctl daemon-reload: code 1")
