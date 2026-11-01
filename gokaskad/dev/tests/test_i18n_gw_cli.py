"""3.3.2 E1-cli: ``kaskadctl gw …`` (lib/kaskad/gw/cli.py) speaks the language of the context — its own lines, the
help of the parser, the refusals, the warnings (with K3_MARKS=1 the mark «@K3:warn», no word of a language); the
Russian stays byte for byte what it was. Texts that come ready from ops/exits/awgengine… are of their own modules."""

import argparse
import os
import re
import unittest
from unittest import mock

from kaskad import cli
from kaskad.gw import cli as gcli
from kaskad.i18n import check, tr, use_lang
from tests.test_gw_ops import CliGwCase

CYR = re.compile("[А-Яа-яЁё]")
CLI_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib", "kaskad", "gw", "cli.py")


def _subparsers(parser):
    for a in parser._actions:
        if isinstance(a, argparse._SubParsersAction):
            return a
    return None


def _helps(parser):
    out = [parser.format_help()]
    sub = _subparsers(parser)
    if sub is not None:
        for p in sub.choices.values():
            out.extend(_helps(p))
    return out


class CatalogTest(unittest.TestCase):
    def test_catalog_complete_and_no_russian_left_in_the_code(self):
        check.assert_catalog_complete(self, ["gw_cli"])
        check.assert_no_cyrillic(self, CLI_PY)


class HelpTest(unittest.TestCase):
    def test_help_of_the_whole_gw_tree_is_english(self):
        with use_lang("en"):
            gw = _subparsers(cli.build_parser()).choices["gw"]
            texts = _helps(gw)
            top = cli.build_parser().format_help()
        joined = "\n".join(texts)
        self.assertIsNone(CYR.search(joined), [t for t in texts if CYR.search(t)][:1])
        self.assertIn("gateway: inbounds, users, devices, exits", top)
        for words in ("COMMAND", "gateway status", "WHO", "TARGET", "take the port even if it is in use",
                      "collector: exit checks and sessions", "safe MTU: the server’s network interface",
                      "AmneziaWG engine: kernel module or userspace"):
            self.assertIn(words, joined)

    def test_help_russian_by_default(self):
        gw = _subparsers(cli.build_parser()).choices["gw"]
        joined = "\n".join(_helps(gw))
        for words in ("КОМАНДА", "состояние шлюза", "КТО", "КУДА", "взять порт, даже если он занят"):
            self.assertIn(words, joined)


class WordsTest(unittest.TestCase):
    def test_module_texts_follow_the_language(self):
        self.assertIn("для разового запуска — --once", gcli.COLLECT_HELP)
        self.assertTrue(gcli.KEY_PROMPT.startswith("Вставьте ключ выхода") and gcli.KEY_PROMPT.endswith(":\n"))
        with use_lang("en"):
            self.assertEqual(gcli.COLLECT_TTY_TEXT, "The collector runs endlessly (it is the kaskad-gw-collect "
                                                    "service), Ctrl-C stops it. For a single run — kaskadctl gw "
                                                    "collect --once\n")
            self.assertEqual(gcli.SUB_PROMPT, "Paste the subscription address (http(s)://…), press Enter, then "
                                              "Ctrl-D:\n")
            self.assertIn("--new-only", tr("gw_cli.inbound.new_only", kind="vless"))
        with self.assertRaises(AttributeError):
            gcli.NO_SUCH_TEXT

    def test_routes_exits_sites_and_parts(self):
        pool_direct = {"via": "pool", "fail": "direct"}
        e = {"name": "Vienna", "where": "VLESS · 198.51.100.31", "named": True, "text": "working", "enabled": False}
        with use_lang("en"):
            self.assertEqual(gcli._route_text(None), "same as user")
            self.assertEqual(gcli._route_text(None, gcli.USER_INHERIT), "as the server’s default")
            self.assertEqual(gcli._route_text(pool_direct), "pool, direct on failure")
            self.assertEqual(gcli.exit_line(e), "Vienna (VLESS · 198.51.100.31) — off for the gateway")
            self.assertEqual(gcli._check_text(None), "not checked yet")
            self.assertEqual(gcli._check_text({"ok": True}), "answers over TLS 1.3 (no HTTP/2)")
            self.assertEqual(gcli._check_text({"ok": False}), "not suitable: no answer")
            self.assertEqual(gcli._mask_word("own"), "the server’s own site")
            self.assertEqual(gcli._body_lines({"body": ["a", "b"]}), ["-- 1 of 2", "a", "-- 2 of 2", "b"])
            self.assertEqual(gcli._plan_line({"supported": False, "kind": "ocserv", "title": "OpenConnect",
                                              "why": ""}), "ocserv   OpenConnect: doesn’t work here — not supported")
        self.assertEqual(gcli._route_text(pool_direct), "pool, при отказе — напрямую")
        self.assertEqual(gcli.exit_line(e), "Vienna (VLESS · 198.51.100.31) — выключен для шлюза")
        self.assertEqual(gcli._body_lines({"body": ["a", "b"]})[0], "-- 1 из 2")


class CliEnTest(CliGwCase):
    def en(self, *argv):
        with use_lang("en"):
            return self.run_cli(*argv)

    def mine(self, *argv):
        """A command whose whole output is of gw/cli.py: no Cyrillic at all in English."""
        rc, out, err = self.en(*argv)
        self.assertIsNone(CYR.search(out + err), out + err)
        return rc, out, err

    def test_people_devices_and_routes(self):
        self.enabled_world(pool=False)
        self.ok("gw", "inbound", "install", "wg")
        rc, out, _ = self.mine("gw", "user", "add", "Mom")
        self.assertEqual(rc, 0)
        self.assertTrue(re.match(r"^User “Mom” added \(u-[0-9a-f]+\)\.\n$", out), out)
        rc, out, _ = self.mine("gw", "user", "list")
        self.assertIn("Mom                  devices 0, route: as the server’s default\n", out)
        rc, out, _ = self.mine("gw", "device", "add", "Mom", "Phone", "--access", "wg")
        self.assertIn("Device “Phone” added: d-", out)
        self.assertIn(", inbounds: wg. Settings: kaskadctl gw issue d-", out)
        rc, out, _ = self.mine("gw", "device", "list")
        self.assertIn(" inbounds: wg               addresses: 10.77.1.10  route: pool\n", out)
        self.assertEqual(self.mine("gw", "user", "route", "Mom", "direct")[1], "Route of “Mom”: direct.\n")
        rc, out, _ = self.mine("gw", "scheme")
        # a new node (gen 2): the default of the rules is all_exit (rf §3.6, D-019)
        self.assertIn("gateway: on; default: pool; server routing rules: all_exit\n", out)
        self.assertIn("Phone: inbounds wg → all_exit, direct (route: user, routing rules: default)\n", out)
        rc, out, _ = self.mine("gw", "device", "reset", "Phone")
        self.assertIn("Device “Phone”: new keys and passwords — wg. Its connections were cut.\n", out)
        self.assertEqual(self.mine("gw", "device", "disable", "Phone")[1], "Device “Phone” turned off.\n")
        self.assertEqual(self.mine("gw", "sessions")[1], "No sessions.\n")
        # Russian by default — as before
        rc, out, _ = self.run_cli("gw", "user", "add", "Мама")
        self.assertTrue(out.startswith("Пользователь «Мама» добавлен (u-"), out)
        self.assertEqual(self.run_cli("gw", "device", "enable", "Phone")[1], "Устройство «Phone» включено.\n")

    def test_gateway_status_check_probe_and_refusals(self):
        self.enabled_world(pool=False)
        self.ok("gw", "inbound", "install", "wg")
        rc, out, _ = self.en("gw", "status")
        self.assertEqual(rc, 0)
        for line in ("gateway: on\n", "network: 10.77.0.0/16, DNS 10.77.0.1, client address 203.0.113.10\n",
                     "users: 0, devices: 0\n", "inbounds: \n", "exits: 0, working 0\n", "xray: not running\n"):
            self.assertIn(line, out)
        self.assertIn(", port 51820/udp, sessions 0, devices 0\n", out)
        self.assertEqual(self.mine("gw", "check")[1], "The gateway is fine.\n")
        self.assertEqual(self.mine("gw", "probe")[1], "The gateway can be turned on here.\n")
        self.assertEqual(self.mine("gw", "route", "direct")[1], "Default route: direct.\n")
        self.assertEqual(self.mine("gw", "inbound", "disable", "wg")[1], "Inbound wg turned off.\n")
        self.assertEqual(self.mine("gw", "exit-sub", "list")[1],
                         "No subscriptions: kaskadctl gw exit-sub add (the address goes to stdin).\n")
        rc, out, err = self.mine("gw", "exit", "x")
        self.assertEqual((rc, err), (2, "Error: Name an action: --enable, --disable, --standby, --restore, --maintenance, --accept-country, "
                                     "a quota (--quota-gb, --no-quota) or --name.\n"))
        rc, out, err = self.mine("gw", "purge")
        self.assertEqual(err, "Error: The whole gateway will be removed: repeat with --yes.\n")
        rc, doc, _ = self.run_json("gw", "inbound", "install", "wg", "--new-only")
        self.assertEqual(doc["data"], {"installed": True, "kind": "wg"})
        with use_lang("en"):
            rc, doc, _ = self.run_json("gw", "inbound", "install", "wg", "--new-only")
        self.assertTrue(doc["error"].startswith("Inbound wg is already installed. To reinstall, run without --new-only"))
        self.assertEqual(self.mine("gw", "disable")[1], "Gateway turned off. Users, devices and inbounds are kept "
                                                        "(turn on: kaskadctl gw enable).\n")
        self.assertEqual(self.run_cli("gw", "check")[1], "Шлюз в порядке.\n")

    def test_warnings_in_the_language_or_as_marks(self):
        self.enabled_world(pool=False)
        self.ok("gw", "inbound", "install", "wg")

        def boom(st, sec, runner):
            from kaskad.gw.inbounds.base import InboundError
            raise InboundError("service did not stop")
        self.ads["wg"].apply = boom
        rc, out, err = self.en("gw", "disable")
        self.assertEqual(rc, 0)
        self.assertIn("Warning: ", err)
        self.assertIn("service did not stop", err)
        self.assertNotIn("Внимание", err)
        self.ok("gw", "enable")
        with mock.patch.dict(os.environ, {"K3_MARKS": "1"}):
            rc, out, err = self.run_cli("gw", "disable")
        self.assertEqual(rc, 0)
        warn = [l for l in err.splitlines() if "service did not stop" in l]
        self.assertTrue(warn and all(l.startswith("@K3:warn ") for l in warn), err)
        self.assertNotIn("Внимание", err)

    def test_mtu_s4_confirm_line(self):
        res = {"text": "S4", "done": False, "warnings": []}
        with mock.patch.object(gcli.ops, "mtu_s4", lambda runner, kind, yes=False: dict(res)), \
                mock.patch.object(gcli, "_r", lambda: None):
            self.assertEqual(self.mine("gw", "mtu", "s4", "awg2")[1], "S4\nConfirm: kaskadctl gw mtu s4 awg2 --yes\n")
            self.assertEqual(self.run_cli("gw", "mtu", "s4", "awg2")[1], "S4\nПодтвердить: kaskadctl gw mtu s4 awg2 --yes\n")
            with mock.patch.dict(os.environ, {"K3_MARKS": "1"}):
                self.assertEqual(self.en("gw", "mtu", "s4", "awg2")[1], "S4\n@K3:confirm kaskadctl gw mtu s4 awg2 --yes\n")


if __name__ == "__main__":
    unittest.main()
