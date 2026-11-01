"""kaskad.snapshot: cache by file signature, generation, single-flight."""

import json
import os
import tempfile
import threading
import time
import unittest

from kaskad import snapshot


class ReadJsonTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = os.path.join(self.d, "a.json")
        snapshot.forget()

    def write(self, obj):
        with open(self.p, "w", encoding="utf-8") as f:
            json.dump(obj, f)

    def test_cached_until_the_file_changes(self):
        self.write({"n": 1})
        a = snapshot.read_json(self.p)
        self.assertIs(snapshot.read_json(self.p), a)                 # same parsed object: no re-read
        time.sleep(0.01)
        self.write({"n": 22})
        self.assertEqual(snapshot.read_json(self.p), {"n": 22})

    def test_missing_and_broken(self):
        self.assertEqual(snapshot.read_json(self.p, default={}), {})
        with open(self.p, "w") as f:
            f.write("{nope")
        self.assertIsNone(snapshot.read_json(self.p))

    def test_copy_and_bump(self):
        self.write({"n": [1]})
        c = snapshot.read_json(self.p, copy=True)
        c["n"].append(2)
        self.assertEqual(snapshot.read_json(self.p), {"n": [1]})
        g = snapshot.generation()
        self.assertEqual(snapshot.bump(), g + 1)


class SingleFlightTest(unittest.TestCase):
    def test_concurrent_same_key_runs_once(self):
        sf = snapshot.SingleFlight()
        calls = []
        gate = threading.Event()

        def work():
            calls.append(1)
            gate.wait(2)
            return 42

        out = []
        ts = [threading.Thread(target=lambda: out.append(sf.do("k", work))) for _ in range(5)]
        for t in ts:
            t.start()
        time.sleep(0.1)
        gate.set()
        for t in ts:
            t.join(3)
        self.assertEqual((len(calls), out), (1, [42] * 5))

    def test_exception_reaches_every_waiter(self):
        sf = snapshot.SingleFlight()

        def boom():
            time.sleep(0.05)
            raise ValueError("x")

        errs = []

        def run():
            try:
                sf.do("k", boom)
            except ValueError:
                errs.append(1)

        ts = [threading.Thread(target=run) for _ in range(3)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(3)
        self.assertEqual(len(errs), 3)


if __name__ == "__main__":
    unittest.main()
