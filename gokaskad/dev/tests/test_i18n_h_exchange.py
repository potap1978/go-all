"""Machine exchange menu ↔ core for menu 17 (3.3.2, H): the SSH menu does not match the words of the core
(k3/handoff/I18N-3.3.2.md, «Обмен меню ↔ ядро»): ``K3_MARKS=1`` — machine lines ``@K3:…``; refusals — ``data.kind``."""
import os
import unittest
from unittest import mock

from kaskad import errors
from kaskad.errors import KaskadError
from kaskad.gw import cli as gwcli
from kaskad.gw import ops
from kaskad.gw.inbounds import vless as vin
from kaskad.gw.inbounds.base import InboundError


class TraceVerdictMark(unittest.TestCase):
    RES = {"verdict": "block", "summary": "Заблокировано", "steps": [{"text": "шаг"}], "notes": []}

    def run_trace(self, env):
        got = {}
        args = mock.Mock(who="d-1", target="ya.ru", inbound=None)
        with mock.patch.dict(os.environ, env, clear=False), \
                mock.patch.object(gwcli.state, "load", return_value={}), \
                mock.patch("kaskad.gw.trace.trace", return_value=dict(self.RES)), \
                mock.patch.object(gwcli, "emit", side_effect=lambda a, doc, text: got.update(doc=doc, text=text)):
            if not env:
                os.environ.pop("K3_MARKS", None)
            self.assertEqual(gwcli.cmd_trace(args), errors.OK)
        return got

    def test_marks_first_line_is_the_verdict(self):
        got = self.run_trace({"K3_MARKS": "1"})
        self.assertEqual(got["text"][0], "@K3:verdict block")
        self.assertEqual(got["text"][1], "Итог: Заблокировано")

    def test_without_marks_no_machine_line(self):
        got = self.run_trace({})
        self.assertFalse(any(l.startswith("@K3:") for l in got["text"]))


class MaskCheckKind(unittest.TestCase):
    def test_failed_check_says_kind(self):
        with mock.patch.object(vin, "country_of", return_value={"code": ""}), \
                mock.patch.object(vin.tunnel, "inbound_cfg", return_value={}), \
                mock.patch.object(vin, "checked", return_value={"ok": False, "why": "TLSv1.2"}):
            with self.assertRaises(InboundError) as cm:
                vin.mask_fields({}, "badsite.example")
        self.assertEqual(cm.exception.data, {"kind": "mask_check"})

    def test_other_refusal_has_no_kind(self):
        with mock.patch.object(vin, "country_of", return_value={"code": ""}), \
                mock.patch.object(vin.tunnel, "inbound_cfg", return_value={}):
            with self.assertRaises(InboundError) as cm:
                vin.mask_fields({}, "not a host!")
        self.assertNotEqual(cm.exception.data.get("kind"), "mask_check")

    def test_ops_keeps_the_kind(self):
        def boom(*a, **k):
            raise InboundError("x", data={"kind": "mask_check"})
        with mock.patch.object(ops.state, "load", return_value={}), mock.patch.object(ops, "_vless_rec"), \
                mock.patch.object(ops, "adapter"), mock.patch.object(vin, "mask_fields", side_effect=boom):
            with self.assertRaises(KaskadError) as cm:
                ops._vless_mask(None, "badsite.example", True)      # under the lock of a transition: the inner part
        self.assertEqual((cm.exception.code, cm.exception.data), (errors.USAGE, {"kind": "mask_check"}))


if __name__ == "__main__":
    unittest.main()
