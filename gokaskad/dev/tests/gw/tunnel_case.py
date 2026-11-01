"""Common ground of the tunnel inbound tests: the core stand-ins, a fake server, a gateway state,
fixed keys and golden files.

Golden files live in tests/fixtures/gw/tunnel/golden/; paths inside them are production paths (the temporary
KASKAD_ROOT is cut out). ``KASKAD_GOLDEN_UPDATE=1`` rewrites them instead of comparing — review the diff.
"""

import base64
import copy
import hashlib
import os

from kaskad import paths, state
from tests.gw.tunnel_fakes import Node
from tests.gwstub import Stubbed
from tests.helpers import RootCase

GOLDEN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fixtures", "gw", "tunnel", "golden")
HOST = "203.0.113.10"
I1 = ("<r 2><b 0x858000010001000000000669636c6f756403636f6d0000010001"
      "c00c000100010000105a00044d583737>")


def slurp(path, binary=False):
    with open(path, "rb" if binary else "r", **({} if binary else {"encoding": "utf-8"})) as f:
        return f.read()


def key(seed):
    """A fixed X25519 private key (any 32 bytes are one) or PSK."""
    return base64.b64encode(hashlib.sha256(("kaskad-test-" + seed).encode()).digest()).decode()


PARAMS = {
    "1.5": {"Jc": "5", "Jmin": "10", "Jmax": "50", "S1": "45", "S2": "65", "H1": "1807207516",
            "H2": "1906024337", "H3": "1225636662", "H4": "501165934", "I1": I1,
            "PersistentKeepalive": "25", "MTU": "1280"},
    "2.0": {"Jc": "4", "Jmin": "10", "Jmax": "50", "S1": "106", "S2": "123", "S3": "18", "S4": "117",
            "H1": "1827480060-1827483990", "H2": "447982126-447984206", "H3": "717615779-717619267",
            "H4": "398175212-398180108", "I1": I1, "PersistentKeepalive": "25", "MTU": "1280"},
    "3.1": {"Jc": "6", "Jmin": "10", "Jmax": "50", "S1": "12", "S2": "12", "S3": "12", "S4": "12",
            "H1": "1", "H2": "2", "H3": "3", "H4": "4", "I1": I1, "HeaderProtectionKey": key("hpk"),
            "RekeyAfterTime": "100-120", "RekeyTimeout": "3-7", "RejectAfterTime": "150-180",
            "KeepaliveTimeout": "5-15", "MaxHandshakeAttempts": "15-20", "RandomTrailers": "on",
            "DisableCookies": "on", "PersistentKeepalive": "25-35", "MTU": "1280"},
}

USERS = [{"id": "u-1", "name": "Мама", "route": None, "enabled": True, "added": "2026-10-04T10:00:00Z"},
         {"id": "u-2", "name": "Гость", "route": {"via": "direct", "fail": "block"}, "enabled": True,
          "added": "2026-10-04T10:00:00Z"}]


def device(dev_id, name, platform, access, login=None, user="u-1", kind="phone", ips=None, **extra):
    d = {"id": dev_id, "user": user, "name": name, "kind": kind, "platform": platform, "route": None,
         "enabled": True, "added": "2026-10-04T10:00:00Z", "login": login or dev_id.replace("d-", "dev-"),
         "ips": dict(ips or {}), "access": list(access)}
    d.update(extra)
    return d


def gw_state(inbounds, devices, host=HOST, users=None):
    st = state.empty()
    st["engine"] = "nft"
    st["gateway"] = {"enabled": True, "host": host, "net": {"base": "10.77.0.0/16", "dns": "10.77.0.1"},
                     "inbounds": copy.deepcopy(inbounds), "users": copy.deepcopy(users or USERS),
                     "devices": copy.deepcopy(devices), "routes": {"default": {"via": "pool", "fail": "block"}},
                     "exits": {}}
    return st


class GwCase(RootCase):
    """Fresh core stand-ins and adapter modules (self.gw), a fake server (self.node), the secrets store
    (self.sec — the same dict the stand-in kaskad.secrets writes to)."""

    maxDiff = None

    def setUp(self):
        super().setUp()
        self.gw = Stubbed().__enter__()
        self.addCleanup(self.gw.__exit__, None, None, None)
        self.node = Node()
        self.runner = self.node.runner
        self.sec = self.gw.secrets.DATA

    def tearDown(self):
        self.assertEqual(self.runner.unmatched, [])
        super().tearDown()

    def adapter(self, kind):
        return self.gw.adapter(kind)

    def prod(self, text):
        """Production paths: cut the temporary KASKAD_ROOT out."""
        return text.replace(self.root, "")

    def golden(self, name, text):
        p = os.path.join(GOLDEN, name)
        text = self.prod(text)
        if os.environ.get("KASKAD_GOLDEN_UPDATE"):
            os.makedirs(GOLDEN, exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)
            return
        with open(p, encoding="utf-8") as f:
            self.assertEqual(text, f.read(), "golden %s differs (KASKAD_GOLDEN_UPDATE=1 rewrites it)" % name)

    def files(self, rendered):
        """render() output keyed by production path."""
        return {self.prod(p): (t, m) for p, (t, m) in rendered.items()}

    def log_text(self):
        p = paths.log()
        if not os.path.exists(p):
            return ""
        with open(p, encoding="utf-8") as f:
            return f.read()

    def assert_no_secrets(self, text, extra=()):
        """None of the secret values (keys, passwords, certificates' keys) occurs in ``text``."""
        for k, v in list(self.sec.items()) + [("extra", e) for e in extra]:
            if not isinstance(v, str) or len(v) < 8:
                continue
            if k.endswith(".crt"):
                continue                                  # certificates are public
            for piece in [v] + [ln for ln in v.splitlines() if len(ln) >= 16 and "-----" not in ln]:
                self.assertNotIn(piece, text, "secret %s leaked" % k)
