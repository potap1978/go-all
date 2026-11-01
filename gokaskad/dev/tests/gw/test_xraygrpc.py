"""T5: AddUser through gRPC ``HandlerService/AlterInbound`` for the inbounds the CLI ``adu`` does not know (Hysteria2;
SPEC-stage1-exits §2.4, С5): the message byte for byte as T0's ``grpc_hy.py``; a curl without HTTP/2, another
``grpc-status``, a timeout — False (the caller restarts xray); the secret never in argv."""

import json
import os
import shutil
import stat
import tempfile
import unittest
import unittest.mock

from kaskad.gw import xraygrpc

FIX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fixtures", "gw",
                   "s1_t5_grpc_add_user.json")


def fixture():
    with open(FIX, encoding="utf-8") as f:
        return json.load(f)


def account(fx):
    return (fx["account_type"], xraygrpc.field(1, fx["auth"]))


class GrpcMessageTest(unittest.TestCase):
    def test_bytes_as_t0(self):
        fx = fixture()
        msg = xraygrpc.add_user_message(fx["tag"], fx["email"], account(fx))
        self.assertEqual(xraygrpc.frame(msg).hex(), fx["frame_hex"])

    def test_varint_long(self):
        self.assertEqual(xraygrpc.varint(300), b"\xac\x02")
        self.assertEqual(xraygrpc.field(1, "x" * 200)[:3], b"\x0a\xc8\x01")


FAKE = """#!/bin/sh
# a fake curl: argv -> $LOG, stdin -> $LOG.body; behaviour by $MODE
echo "$@" >> "%(log)s"
if [ "$1" = "-V" ]; then
  if [ "%(mode)s" = "nohttp2" ]; then echo "Features: AsynchDNS IPv6 SSL"; else echo "Features: AsynchDNS HTTP2 IPv6 SSL"; fi
  exit 0
fi
cat > "%(log)s.body"
case "%(mode)s" in
  ok)      echo "< grpc-status: 0" >&2 ;;
  err13)   echo "< grpc-status: 13" >&2; echo "< grpc-message: boom" >&2 ;;
  timeout) echo "curl: (28) Operation timed out" >&2; exit 28 ;;
  sleep)   exec sleep 30 ;;
esac
exit 0
"""


class GrpcCallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        xraygrpc._curl_ok.clear()
        self.addCleanup(xraygrpc._curl_ok.clear)

    def curl(self, mode):
        p = os.path.join(self.tmp, "curl-" + mode)
        self.log = p + ".log"
        with open(p, "w") as f:
            f.write(FAKE % {"log": self.log, "mode": mode})
        os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR)
        return p

    def add(self, mode, timeout=8):
        fx = fixture()
        return xraygrpc.add_user("127.0.0.1:10854", fx["tag"], fx["email"], account(fx), curl=self.curl(mode),
                                 timeout=timeout)

    def test_ok_and_secret_not_in_argv(self):
        self.assertTrue(self.add("ok"))
        with open(self.log) as f:
            argv = f.read()
        self.assertNotIn(fixture()["auth"], argv)
        self.assertIn("--http2-prior-knowledge", argv)
        self.assertIn("http://127.0.0.1:10854/xray.app.proxyman.command.HandlerService/AlterInbound", argv)
        with open(self.log + ".body", "rb") as f:
            self.assertEqual(f.read().hex(), fixture()["frame_hex"])

    def test_failures_mean_restart(self):
        self.assertFalse(self.add("err13"))
        xraygrpc._curl_ok.clear()
        self.assertFalse(self.add("nohttp2"))
        self.assertFalse(os.path.exists(self.log + ".body"))         # no call without HTTP/2
        xraygrpc._curl_ok.clear()
        self.assertFalse(self.add("timeout"))
        xraygrpc._curl_ok.clear()
        self.assertFalse(self.add("sleep", timeout=1))               # the process timeout of the call itself

    def test_no_curl(self):
        with unittest.mock.patch.object(xraygrpc.shutil, "which", return_value=None), \
                unittest.mock.patch.object(xraygrpc.os, "access", return_value=False):
            self.assertEqual(xraygrpc.call("127.0.0.1:1", b"x"), (False, "no curl"))


if __name__ == "__main__":
    unittest.main()
