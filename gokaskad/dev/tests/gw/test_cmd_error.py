"""tunnel.cmd_error: the reason of a failure, not the first line of noise (found on Ubuntu 26.04)."""

import unittest

from kaskad.gw.inbounds import tunnel
from kaskad.sysrun import Result


class CmdErrorTest(unittest.TestCase):
    def test_systemctl_enable_now_names_the_failed_job(self):
        res = Result(["systemctl"], 1, "", "Created symlink '/etc/systemd/system/multi-user.target.wants/kaskad-gw-ipsec.service'"
                     " -> '/etc/systemd/system/kaskad-gw-ipsec.service'.\n"
                     "Job for kaskad-gw-ipsec.service failed because the control process exited with error code.\n")
        self.assertTrue(tunnel.cmd_error(res).startswith("Job for kaskad-gw-ipsec.service failed"))

    def test_swanctl_skips_the_plugin_list_of_strongswan_6(self):
        res = Result(["swanctl"], 22, "loaded certificate 'CN=Kaskad IKEv2 CA'\n"
                         "loading connection 'kaskad-l2tp' failed: invalid value for: remote_ts, config discarded\n"
                         "loaded 1 of 2 connections, 1 failed to load, 0 unloaded\n",
                     "plugin 'test-vectors': failed to load - test_vectors_plugin_create not found\n"
                     "agent plugin requires CAP_SETUID/CAP_SETGID capability\n")
        self.assertEqual(tunnel.cmd_error(res),
                         "loading connection 'kaskad-l2tp' failed: invalid value for: remote_ts, config discarded")

    def test_plain_first_line_and_exit_code(self):
        self.assertEqual(tunnel.cmd_error(Result(["x"], 1, "", "something odd\nmore\n")), "something odd")
        self.assertEqual(tunnel.cmd_error(Result(["x"], 3, "", "")), "код 3")


if __name__ == "__main__":
    unittest.main()
