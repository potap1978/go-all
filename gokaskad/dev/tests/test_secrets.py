"""kaskad.secrets — /etc/kaskad/secrets.json."""

import json
import os
import stat

from kaskad import secrets
from kaskad.errors import StateError
from tests.helpers import RootCase


class SecretsTest(RootCase):
    def test_missing_file_is_empty(self):
        s = secrets.load()
        self.assertEqual(dict(s), {})
        self.assertIsNone(secrets.get("wg.server.private"))
        self.assertEqual(secrets.get("x", "d"), "d")
        self.assertFalse(s.save())                                    # nothing to write
        self.assertFalse(os.path.exists(self.path("/etc/kaskad/secrets.json")))

    def test_set_get_delete_and_mode(self):
        secrets.set("wg.server.private", "AAA=")
        secrets.set("dev.d-1a2b3c4d.password", "pw")
        p = self.path("/etc/kaskad/secrets.json")
        self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)
        doc = json.loads(self.read("/etc/kaskad/secrets.json"))
        self.assertEqual(doc, {"schema": 1, "values": {"dev.d-1a2b3c4d.password": "pw", "wg.server.private": "AAA="}})
        self.assertEqual(secrets.get("wg.server.private"), "AAA=")
        self.assertTrue(secrets.delete("wg.server.private"))
        self.assertFalse(secrets.delete("wg.server.private"))
        self.assertIsNone(secrets.get("wg.server.private"))

    def test_store(self):
        s = secrets.load()
        s.set("dev.d-1.wg.private", "k1")
        s.set("dev.d-1.password", "p1")
        s.set("dev.d-2.password", "p2")
        self.assertEqual(s.setdefault_new("l2tp.psk", lambda: "psk1"), "psk1")
        self.assertEqual(s.setdefault_new("l2tp.psk", lambda: "psk2"), "psk1")
        self.assertTrue(s.save())
        self.assertFalse(s.save())
        s = secrets.load()
        self.assertEqual(s.delete_prefix("dev.d-1."), 2)
        s.save()
        self.assertEqual(sorted(secrets.load()), ["dev.d-2.password", "l2tp.psk"])

    def test_bad_keys_and_broken_files(self):
        with self.assertRaises(StateError):
            secrets.set("../../etc/passwd", "x")
        with self.assertRaises(StateError):
            secrets.load().set("a b", "x")
        self.write("/etc/kaskad/secrets.json", "{broken")
        with self.assertRaises(StateError):
            secrets.load()
        self.write("/etc/kaskad/secrets.json", '{"schema": 2, "values": {}}')
        with self.assertRaises(StateError):
            secrets.load()

    def test_passwords(self):
        seen = set()
        for _ in range(200):
            pw = secrets.new_password()
            self.assertEqual(len(pw), 14)
            self.assertTrue(set(pw) <= set(secrets.ALPHABET))
            for look_alike in "0O1lI":
                self.assertNotIn(look_alike, pw)
            self.assertTrue(any(c.isdigit() for c in pw) and any(c.islower() for c in pw)
                            and any(c.isupper() for c in pw))
            seen.add(pw)
        self.assertEqual(len(seen), 200)
        self.assertEqual(len(secrets.new_password(4)), 8)               # never shorter than 8
        self.assertEqual(len(secrets.new_psk()), 24)
        self.assertRegex(secrets.new_token(4), r"^[0-9a-f]{8}$")
