"""vendor/awgkeys.py and vendor/clients.json — the copies of goAmnezia pro: provenance, keys, every line's
parameters, .conf, vpn:// and QR chunks round trips."""

import os
import unittest

from kaskad import vendor
from kaskad.vendor import awgkeys

HERE = os.path.dirname(os.path.abspath(vendor.__file__))


def client_data(line, params):
    return {"params": params,
            "client": {"ip": "10.77.4.10", "privkey": awgkeys.genkey(), "psk": awgkeys.genpsk()},
            "server": {"pubkey": awgkeys.pubkey(awgkeys.genkey()), "endpoint_host": "203.0.113.10", "port": 51911},
            "dns": ["10.77.0.1"], "mtu": "1280"}


class VendorTest(unittest.TestCase):
    def test_provenance(self):
        with open(os.path.join(HERE, "awgkeys.py"), encoding="utf-8") as f:
            first = f.readline()
        self.assertTrue(first.startswith("# vendored from goAmnezia pro: lib/awgkeys.py @ bbcbad7"), first)
        self.assertEqual(vendor.SOURCES["awgkeys.py"][2], "bbcbad7")
        self.assertEqual(vendor.SOURCES["clients.json"][2], "a403b16")
        cat = vendor.clients()
        self.assertEqual(cat["lines"], ["1.0", "1.5", "2.0", "3.1"])
        self.assertIn("amneziavpn", [a["id"] for a in cat["apps"]])

    def test_x25519_rfc7748_vector(self):
        alice = bytes.fromhex("77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a")
        self.assertEqual(awgkeys.x25519_base(alice).hex(),
                         "8520f0098930a754748b7ddcb43ef75a0dbf3a0d26381af4eba4a98eaa9b4e6a")
        k = awgkeys.genkey()
        self.assertTrue(awgkeys.is_key(k) and awgkeys.is_key(awgkeys.pubkey(k)) and awgkeys.is_key(awgkeys.genpsk()))

    def test_every_line_round_trips_through_vpn_and_qr(self):
        for line in awgkeys.LINES:
            with self.subTest(line=line):
                params = awgkeys.gen_params(line)
                self.assertEqual(awgkeys.validate_params(line, params), [])
                self.assertEqual(awgkeys.detect_line(params), line)
                data = client_data(line, params)
                conf = awgkeys.render_conf(line, data)
                self.assertEqual(awgkeys.detect_line(awgkeys.params_from_conf(conf)), line)
                vpn = awgkeys.vpn_encode({"container": "amnezia-awg2", "line": line, "params": params,
                                          "conf_text": conf, "description": "Kaskad · тест", "hostName": "203.0.113.10",
                                          "port": 51911, "dns": ["10.77.0.1"], "subnet": "10.77.4.0/24"})
                self.assertTrue(vpn.startswith("vpn://"))
                back = awgkeys.vpn_decode(vpn)
                self.assertEqual(back["line"], line)
                self.assertEqual(back["container"], "amnezia-awg2")
                self.assertEqual(back["client"]["privkey"], data["client"]["privkey"])
                self.assertEqual(back["client"]["psk"], data["client"]["psk"])
                self.assertEqual(back["server"]["pubkey"], data["server"]["pubkey"])
                self.assertEqual((back["hostName"], back["port"]), ("203.0.113.10", 51911))
                self.assertEqual(back["dns"], ["10.77.0.1"])
                for k in awgkeys.AWG_IFACE_KEYS + awgkeys.AWG3_KEYS:
                    self.assertEqual(back["params"][k], params[k], k)
                self.assertEqual(back["conf_text"], conf)
                chunks = awgkeys.qr_chunks(vpn)
                self.assertEqual(awgkeys.qr_chunks_join(list(reversed(chunks))), vpn)

    def test_server_conf_keeps_3x_fields(self):
        params = awgkeys.gen_params("3.1")
        text = awgkeys.render_server_conf("3.1", {"params": params, "server": {
            "privkey": awgkeys.genkey(), "subnet": "10.77.4.0/24", "port": 51911}, "peers": []})
        for k in ("HeaderProtectionKey", "RandomTrailers", "DisableCookies", "RekeyAfterTime"):
            self.assertIn(k + " = ", text)
        self.assertIn("Address = 10.77.4.0/24", text)       # the adapter strips it before awg setconf
