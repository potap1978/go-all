"""util.ensure_dir and util.atomic_write when another process makes the directory between the check and makedirs.

The flake of ``tests.bench.test_sched_retry_limits`` under ``-j4`` (мелочи после 3.3.7.12, п. 5): four processes plan a
repeat at once, the first of them makes /var/lib/kaskad/bench, another one had already seen it missing — its
``os.makedirs`` raised FileExistsError and the plan fell. The same race on a node: the timer service, the unit of a check
and the repeat touch the directory of the bench on a fresh node."""
import os
import shutil
import tempfile
import unittest
from unittest import mock

from kaskad import util


class DirRaceTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="k3-dir-race-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.d = os.path.join(self.root, "bench")

    def _late(self):
        """os.path.isdir that says «missing» once while another process has made the directory already."""
        real = os.path.isdir

        def isdir(p):
            if p == self.d and not getattr(isdir, "said", False):
                isdir.said = True
                os.makedirs(self.d, 0o700)                 # the other process wins the race right now
                return False
            return real(p)
        return mock.patch.object(os.path, "isdir", side_effect=isdir)

    def test_ensure_dir_when_another_process_made_it(self):
        with self._late():
            util.ensure_dir(self.d, 0o700)
        self.assertTrue(os.path.isdir(self.d))
        self.assertEqual(os.stat(self.d).st_mode & 0o777, 0o700)

    def test_atomic_write_when_another_process_made_the_directory(self):
        p = os.path.join(self.d, "sched-state.json")
        with self._late():
            util.atomic_write(p, "{}", 0o600)
        with open(p) as f:
            self.assertEqual(f.read(), "{}")

    def test_a_file_in_place_of_the_directory_still_fails(self):
        with open(self.d, "w") as f:
            f.write("x")
        with self.assertRaises(OSError):
            util.ensure_dir(self.d, 0o700)


if __name__ == "__main__":
    unittest.main()
