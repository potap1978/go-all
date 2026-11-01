"""3.3.2 E2: English of summary, state, nft, ipt, util — the same paths say it in the language of the context."""

import os
import re
import shutil
import tempfile
import unittest

from kaskad import ipt, netcheck, nft, state, summary, util
from kaskad.errors import StateError
from kaskad.i18n import use_lang

CYR = re.compile("[А-Яа-яЁё]")


class CoreEnglishTest(unittest.TestCase):
    def _err(self, fn, *a):
        with self.assertRaises(StateError) as c:
            fn(*a)
        return str(c.exception)

    def test_state_errors(self):
        with use_lang("en"):
            self.assertEqual(self._err(state.parse_port, "x"), "port must be a number 1–65535, not 'x'")
            self.assertEqual(self._err(state.parse_proto, "icmp"), "protocol must be tcp or udp, not 'icmp'")
            self.assertIn("can’t forward", self._err(state.parse_target_ip, "127.0.0.1"))
            msg = self._err(state.normalize_forward, {"proto": "tcp", "port": 0, "to": "1.2.3.4", "to_port": 1})
            self.assertTrue(msg.startswith("incoming port must be"), msg)
        with use_lang("ru"):
            self.assertEqual(self._err(state.parse_port, "x"), "порт должен быть числом 1–65535, а не 'x'")

    def test_util(self):
        with use_lang("en"):
            self.assertEqual([util.n_bytes(n) for n in (1, 2, 1372)], ["1 byte", "2 bytes", "1372 bytes"])
            self.assertEqual(util.human_bytes(5000), "4.9 KB")
            self.assertEqual(util.redact("vpn://AAAA x"), "vpn://<hidden> x")
        with use_lang("ru"):
            self.assertEqual([util.n_bytes(n) for n in (1, 2, 1360)], ["1 байт", "2 байта", "1360 байт"])
            self.assertEqual(util.human_bytes(5000), "4,9 КБ")   # 3.3.2: ru — десятичная запятая (i18n.fmt_bytes)
            self.assertEqual(util.redact("vpn://AAAA x"), "vpn://<скрыт> x")

    def test_nft_verify_and_ipt(self):
        with use_lang("en"):
            self.assertEqual(nft.verify({"forwards": []}, None), ["table ip kaskad is missing"])
            r = ipt.Rule("-A INPUT -s")
            self.assertEqual(r.qualifiers(), ["(not parsed: no value for -s)"])
        with use_lang("ru"):
            self.assertEqual(ipt.Rule("-A INPUT -s").error, "нет значения у -s")

    def test_summary_text(self):
        doc = {"version": "3.3.2", "engine": "nft", "forwards": 2,
               "gateway": {"setup": True, "enabled": True, "inbounds": [{"state": "run", "short": "VLESS"}]},
               "exits": {"alive": 1, "total": 2},
               "problems": [summary.problem("no_exits", "No exits", "rule")]}
        with use_lang("en"):
            lines = summary.text_lines(dict(doc, problems=[summary.problem("no_exits", "No exits", "rule")]))
            self.assertEqual(lines[1:4], ["Forwards: 2 (nftables)", "Gateway: on  ● VLESS", "Exits: 1 of 2 working"])
            self.assertIsNone(CYR.search("\n".join(lines)), lines)
        with use_lang("ru"):
            lines = summary.text_lines(doc)
            self.assertEqual(lines[1:4], ["Пробросы: 2 (nftables)", "Шлюз: включён  ● VLESS", "Выходы: 1 из 2 работают"])



class WatchReportLangTest(unittest.TestCase):
    """watch.json is written in the watchdog's language; a reader in another language does not show its words."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.old = os.environ.get("KASKAD_ROOT")
        os.environ["KASKAD_ROOT"] = self.root
        os.makedirs(os.path.join(self.root, "run", "kaskad"))

    def tearDown(self):
        if self.old is None:
            os.environ.pop("KASKAD_ROOT", None)
        else:
            os.environ["KASKAD_ROOT"] = self.old
        shutil.rmtree(self.root, ignore_errors=True)

    def test_language_of_the_report(self):
        with use_lang("ru"):
            netcheck.write_report([{"kind": "ip_forward", "detail": "выключен", "repaired": False}])
            probs, age = netcheck.read_report()
            self.assertEqual([p["detail"] for p in probs], ["выключен"])
        with use_lang("en"):
            probs, age = netcheck.read_report()
            self.assertIsNone(probs)
            self.assertIsNotNone(age)
            netcheck.write_report([{"kind": "ip_forward", "detail": "off", "repaired": False}])
            self.assertEqual([p["detail"] for p in netcheck.read_report()[0]], ["off"])

class CoreMarksTest(unittest.TestCase):
    """K3_MARKS=1 (the SSH menu reads the core): warnings, errors and waits carry «@K3:warn|error|wait», not a word."""

    def test_core_line(self):
        import io
        import sys
        from unittest import mock
        from kaskad import aptlock, cli
        from kaskad.errors import KaskadError
        with mock.patch.dict(os.environ, {"K3_MARKS": "1"}):
            self.assertEqual(util.core_line("warn", "x"), "@K3:warn x")
            err = io.StringIO()
            with mock.patch.object(sys, "stderr", err):
                aptlock._stderr("apt is busy")

                def boom(args):
                    raise KaskadError("broken")
                with mock.patch.object(cli, "build_parser") as bp:
                    bp.return_value.parse_args.return_value = mock.Mock(func=boom, json=False)
                    cli.main(["x"])
            self.assertEqual(err.getvalue().splitlines(), ["@K3:wait apt is busy", "@K3:error broken"])
        with mock.patch.dict(os.environ, {"K3_MARKS": ""}):
            self.assertIsNone(util.core_line("warn", "x"))
            err = io.StringIO()
            with mock.patch.object(sys, "stderr", err), use_lang("en"):
                aptlock._stderr("apt is busy")
            self.assertEqual(err.getvalue(), "apt is busy\n")


if __name__ == "__main__":
    unittest.main()
