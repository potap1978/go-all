import json
import os
import stat

from kaskad import state, errors
from kaskad.errors import StateError
from tests.helpers import RootCase, lock_held_elsewhere


class StateTest(RootCase):
    def test_missing_file_is_empty_legacy(self):
        st = state.load()
        self.assertEqual(st["engine"], "legacy")
        self.assertEqual(st["forwards"], [])
        self.assertFalse(state.exists())

    def test_roundtrip_mode_and_grep_friendly_engine(self):
        st = state.load()
        st["engine"] = "nft"
        state.upsert_forward(st, {"proto": "TCP", "port": "443", "to": "203.0.113.5", "to_port": 443})
        state.save(st)
        p = self.path("/etc/kaskad/state.json")
        self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)
        text = self.read("/etc/kaskad/state.json")
        self.assertIn('"engine": "nft"', text)          # install.sh greps exactly this
        st2 = state.load()
        self.assertEqual(st2["forwards"][0]["proto"], "tcp")
        self.assertEqual(st2["forwards"][0]["port"], 443)
        self.assertEqual(st2["forwards"][0]["origin"], "k3")

    def test_upsert_replaces_same_proto_port_only(self):
        st = state.empty()
        state.upsert_forward(st, {"proto": "tcp", "port": 443, "to": "203.0.113.5", "to_port": 443})
        state.upsert_forward(st, {"proto": "udp", "port": 443, "to": "203.0.113.6", "to_port": 51820})
        old = state.upsert_forward(st, {"proto": "tcp", "port": 443, "to": "203.0.113.7", "to_port": 8443})
        self.assertEqual(old["to"], "203.0.113.5")
        self.assertEqual(len(st["forwards"]), 2)
        self.assertEqual(state.find_forward(st, "tcp", 443)["to"], "203.0.113.7")
        self.assertIsNotNone(state.remove_forward(st, "udp", 443))
        self.assertIsNone(state.remove_forward(st, "udp", 443))

    def test_validation(self):
        bad = [
            {"proto": "icmp", "port": 1, "to": "203.0.113.5", "to_port": 1},
            {"proto": "tcp", "port": 0, "to": "203.0.113.5", "to_port": 1},
            {"proto": "tcp", "port": 70000, "to": "203.0.113.5", "to_port": 1},
            {"proto": "tcp", "port": 1, "to": "example.com", "to_port": 1},
            {"proto": "tcp", "port": 1, "to": "127.0.0.1", "to_port": 1},
            {"proto": "tcp", "port": 1, "to": "0.0.0.0", "to_port": 1},
            {"proto": "tcp", "port": 1, "to": "224.0.0.1", "to_port": 1},
            {"proto": "tcp", "port": 1, "to": "203.0.113.5", "to_port": "x"},
            {"proto": "tcp", "port": 1, "to": "203.0.113.5", "to_port": 1, "origin": "alien"},
        ]
        for f in bad:
            with self.assertRaises(StateError, msg=f):
                state.normalize_forward(f)
        ok = state.normalize_forward({"proto": "udp", "port": 51820, "to": "10.8.0.2", "to_port": 51820,
                                      "name": "AWG|Вена\nдом"})
        self.assertEqual(ok["name"], "AWG Вена дом")

    def test_corrupted_file_is_not_overwritten(self):
        self.write("/etc/kaskad/state.json", "{broken")
        with self.assertRaises(StateError):
            state.load()
        self.assertEqual(state.engine(), "legacy")
        self.assertEqual(self.read("/etc/kaskad/state.json"), "{broken")

    def test_unknown_schema_refused(self):
        self.write("/etc/kaskad/state.json", json.dumps({"schema": 99}))
        with self.assertRaises(StateError):
            state.load()

    def test_duplicate_forward_in_file_refused(self):
        f = {"proto": "tcp", "port": 443, "to": "203.0.113.5", "to_port": 443}
        self.write("/etc/kaskad/state.json", json.dumps({"schema": 1, "forwards": [f, f]}))
        with self.assertRaises(StateError):
            state.load()

    def test_added_is_not_invented_on_read(self):
        """A forward of an old version without «added»: two loads give the same record ("" — the time is unknown),
        not the time of each read; a forward written now gets its time once and keeps it."""
        from unittest import mock
        f = {"proto": "tcp", "port": 443, "to": "203.0.113.5", "to_port": 443}
        self.write("/etc/kaskad/state.json", json.dumps({"schema": 1, "forwards": [f]}))
        with mock.patch.object(state.util, "now_iso", side_effect=["2026-01-01T00:00:00Z", "2026-02-02T00:00:00Z"]):
            a, b = state.load(), state.load()
        self.assertEqual(a["forwards"], b["forwards"])
        self.assertEqual(a["forwards"][0]["added"], "")
        st = state.empty()
        state.upsert_forward(st, dict(f, port=8443))
        state.save(st)
        self.assertTrue(st["forwards"][0]["added"])
        self.assertEqual(state.load()["forwards"][0]["added"], st["forwards"][0]["added"])
        self.assertEqual(state.load()["forwards"], state.load()["forwards"])

    def test_transaction_saves_only_on_success(self):
        with state.transaction() as st:
            state.upsert_forward(st, {"proto": "tcp", "port": 80, "to": "203.0.113.5", "to_port": 80})
        self.assertEqual(len(state.load()["forwards"]), 1)
        try:
            with state.transaction() as st:
                state.remove_forward(st, "tcp", 80)
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        self.assertEqual(len(state.load()["forwards"]), 1)

    def test_lock_timeout(self):
        with lock_held_elsewhere():                  # another process holds it: «busy» after the wait, code 6
            with self.assertRaises(errors.LockedError) as cm:
                with state.locked(timeout=0.3):
                    pass
        self.assertNotIsInstance(cm.exception, errors.NestedLockError)
        self.assertEqual(cm.exception.code, errors.LOCKED)
        with state.locked():
            # flock locks are per open file description: a second open in the same thread never passes — told at
            # once as an error of the code (T1, SPEC-stage1-exits §3.1)
            with self.assertRaises(errors.NestedLockError):
                with state.locked(timeout=0.3):
                    pass
