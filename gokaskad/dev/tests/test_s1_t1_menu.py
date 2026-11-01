"""Stage 1, T1: the SSH menu reads the exits of ``kaskadctl gw exits --json`` and live.json — an xray exit is now of the
kind "xray" with ``proto``: the menu names it «VLESS» as before and «devices without internet» counts it alive."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def install_sh():
    with open(os.path.join(ROOT, "install.sh"), encoding="utf-8") as f:
        return f.read()


class MenuKindTest(unittest.TestCase):
    def test_exit_kind_words(self):
        text = install_sh()
        start = text.index("k3_gw_exit_kind() {")
        fn = text[start:text.index("esac; }\n", start) + len("esac; }")]
        script = fn + "\nfor k in vless hysteria2 trojan 'awg 2.0' xray; do k3_gw_exit_kind \"$k\"; done\n"
        out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True).stdout.split("\n")
        self.assertEqual(out[:5], ["VLESS", "Hysteria2", "Trojan", "AmneziaWG 2.0", "xray"])

    @unittest.skipUnless(shutil.which("jq"), "jq is needed")
    def test_list_passes_the_protocol(self):
        text = install_sh()
        start = text.index("k3_jq '.exits[]? | [.id, .name, (.where // \"\"), .named, ")
        end = 'join("\\u001f")'          # 3.3.5.1: «…join(…)' | k3_term_filter)» — программа jq до своей «'»
        prog = text[start + len("k3_jq '"):text.index(end, start) + len(end)]
        doc = {"exits": [{"id": "e-1", "name": "Вена", "kind": "xray", "proto": "vless", "named": False},
                         {"id": "e-2", "name": "Ам", "kind": "awg", "version": "2.0", "named": False},
                         {"id": "e-3", "name": "Old", "kind": "vless", "named": False}]}
        out = subprocess.run(["jq", "-r", prog], input=json.dumps(doc), capture_output=True, text=True, check=True)
        kinds = [ln.split("\x1f")[4] for ln in out.stdout.strip().split("\n")]
        self.assertEqual(kinds, ["vless", "awg 2.0", "vless"])

    @unittest.skipUnless(shutil.which("jq"), "jq is needed")
    def test_devices_without_internet_count_an_xray_exit(self):
        text = install_sh()
        body = text[text.index("k3_gw_dead() {"):]
        body = body[:body.index("\n}\n")]
        prog = body[body.index("'") + 1:body.rindex("'")]
        st = {"gateway": {"enabled": True, "routes": {"default": {"via": "pool", "fail": "block"}}}}
        with tempfile.TemporaryDirectory() as d:
            sp = os.path.join(d, "state.json")
            with open(sp, "w") as f:
                json.dump(st, f)

            def dead(kind, alive):
                live = {"ts": 1000, "gateway": {"enabled": True},
                        "exits": {"e-1": {"kind": kind, "alive": alive, "enabled": True}}}
                r = subprocess.run(["jq", "-e", "--argjson", "now", "1000", "--slurpfile", "st", sp, prog],
                                   input=json.dumps(live), capture_output=True, text=True)
                return r.returncode == 0
            self.assertFalse(dead("xray", True))          # T1: a live xray exit — the devices have internet
            self.assertFalse(dead("vless", True))         # a live.json of 3.3.5
            self.assertTrue(dead("xray", False))


if __name__ == "__main__":
    unittest.main()
