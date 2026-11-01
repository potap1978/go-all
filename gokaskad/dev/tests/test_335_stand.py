"""Kaskad 3.3.5, the findings of the release stand (k3/stand/logs/T-335-release.md) — П2 is tests/test_335_hold.py:

    В1  «Вернуть исключённые» brings a key back with the id it had, though the subscription renamed it meanwhile
        («10GB left» → «9GB left») — the routes of devices and people on that id work again;
    П1  «Выходы» say the rule «упал» of 3.3.5 (half of the checks in 2 minutes), not «три неудачные проверки подряд»;
    С1  the check of the server sends to delete the extra key where it is deleted: 8 → 7 → F3, not 17 → 3;
    С2  the drop-in of a removed leg goes with daemon-reload; the egress is restarted after one when systemd asks for
        it — no «changed on disk … daemon-reload» in the menu;
    П4  the panel's answer of a deletion carries the words of the CLI and MCP: «Маршруты перешли на «…»»;
    Т2  README: VLESS is noticed in 1–1.5 minutes, AmneziaWG in about one."""

import json
import os
import re
import stat
import subprocess
import tempfile
import unittest
from unittest import mock

from kaskad import summary
from kaskad.gw import exits, exitsubs, ops
from kaskad.i18n import use_lang
from tests.fakes_gw import VLESS_B
from tests.test_335_rename import RenameCase, b64, key
from tests.test_gw_exitsubs import IP_URL

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "lib", "kaskad", "panel", "static")


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class RestoreKeepsTheIdTest(RenameCase):
    """В1: the key removed by hand, renamed by the subscription, brought back — under its old id."""

    def removed_then_renamed(self):
        self.add(IP_URL, b64([key("10GB left"), VLESS_B]))
        eid = self.ids()[0]
        self.route_all(eid)
        before = self.routes()
        ops.exit_del(self.w.runner, eid)
        self.feed.bodies[IP_URL] = b64([key("9GB left"), VLESS_B])
        self.refresh()
        self.assertEqual(self.pool(), [VLESS_B])                          # still out under another name
        return eid, before

    def test_it_comes_back_with_its_id_and_the_routes_work(self):
        eid, before = self.removed_then_renamed()
        res = exitsubs.restore(self.w.runner, "1", fetcher=self.feed)
        self.assertTrue(res["ok"], res)
        self.assertEqual(self.pool(), [VLESS_B, key("9GB left")])
        self.assertEqual(exits.pool_id(key("9GB left")), eid)             # not the id of the new text
        self.assertIn(eid, [e["id"] for e in exits.pool(self.w.runner)])
        self.assertEqual(self.routes()[:3], before[:3])                   # the routes name a live exit again
        self.assertNotIn("returning", exitsubs.load()["subs"][0])        # given back: nothing more to remember
        # renamed once more after it: the same exit, as any key of the subscription
        self.feed.bodies[IP_URL] = b64([key("8GB left"), VLESS_B])
        self.refresh()
        self.assertEqual(exits.pool_id(key("8GB left")), eid)

    def test_not_given_at_the_restore_it_comes_back_with_its_id_later(self):
        eid, _before = self.removed_then_renamed()
        self.feed.bodies[IP_URL] = b64([VLESS_B])                          # the subscription lacks it for a while
        exitsubs.restore(self.w.runner, "1", fetcher=self.feed)
        self.assertEqual(self.pool(), [VLESS_B])
        self.assertIn("returning", exitsubs.load()["subs"][0])
        self.feed.bodies[IP_URL] = b64([key("7GB left"), VLESS_B])
        self.refresh()
        self.assertEqual(exits.pool_id(key("7GB left")), eid)

    def test_removed_again_after_it_stays_out(self):
        eid, _before = self.removed_then_renamed()
        exitsubs.restore(self.w.runner, "1", fetcher=self.feed)
        ops.exit_del(self.w.runner, eid)
        self.feed.bodies[IP_URL] = b64([key("6GB left"), VLESS_B])
        self.refresh()
        self.assertEqual(self.pool(), [VLESS_B])
        self.assertEqual(exitsubs.listing()[0]["excluded"], 1)

    def test_a_record_survives_the_file(self):
        self.removed_then_renamed()
        sub = exitsubs.load()["subs"][0]
        self.assertEqual(len(sub["excluded_was"]), 1)
        self.assertTrue(all(re.match(r"^e-[0-9a-f]{10}$", v) for v in sub["excluded_was"].values()))


class WordsTest(unittest.TestCase):
    def test_the_rule_of_down_in_the_panel(self):
        """П1: the words of the rule of 3.3.5 in both languages; no «подряд», no «20–35»."""
        ru = re.search(r'"ex\.listText": "([^"]+)"', read("lib", "kaskad", "panel", "static", "i18n-ru.js")).group(1)
        en = re.search(r'"ex\.listText": "([^"]+)"', read("lib", "kaskad", "panel", "static", "i18n-en.js")).group(1)
        for words in ("за последние 2 минуты не удалась хотя бы половина проверок", "не меньше четырёх",
                      "AmneziaWG замечается примерно за минуту, VLESS — за 1–1,5 минуты"):
            self.assertIn(words, ru)
        for words in ("at least half of its checks in the last 2 minutes failed", "at least four checks",
                      "about a minute for AmneziaWG and in 1 to 1.5 minutes for VLESS"):
            self.assertIn(words, en)
        for old in ("подряд", "20–35", "in a row", "20 to 35"):
            self.assertNotIn(old, ru + en)
        self.assertIn("AmneziaWG замечается примерно за минуту, VLESS — за 1–1,5 минуты", read("README.md"))
        self.assertNotIn("после 2 неудачных проверок подряд (и активная", read("docs", "k3", "SPEC-wave2-gateway.md"))

    def test_the_check_sends_where_the_key_is_deleted(self):
        """С1: 8 → 7 → F3 (bot_menu → egress_menu → F3 egress_alt_leave), not 17 → 3 that has no deletion."""
        with use_lang("ru"):
            item, ru = summary.advice("exits_same")
        with use_lang("en"):
            _item, en = summary.advice("exits_same")
        self.assertEqual(item, 8)
        self.assertIn("8 → 7 → F3", ru)
        self.assertIn("8 → 7 → F3", en)
        self.assertNotIn("17 → 3", ru + en)
        sh = read("install.sh")
        self.assertRegex(sh, r"8\) bot_menu;;")
        self.assertRegex(sh, r"\n {12}7\) egress_menu; ")
        self.assertRegex(sh, r"\n {12}F3\|3\|d\|D\) egress_alt_leave; ")


def body(sh, name):
    m = re.search(r"(?ms)^%s\(\) \{\n(.*?)^\}\n" % re.escape(name), sh)
    return "%s() {\n%s}\n" % (name, m.group(1))


class DaemonReloadTest(unittest.TestCase):
    """С2: the real functions of install.sh with a fake systemctl that logs its calls."""

    def run_sh(self, script, need="no"):
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", d]))
        bin_ = os.path.join(d, "bin")
        os.mkdir(bin_)
        log = os.path.join(d, "calls")
        for name, text in (("systemctl", '#!/bin/sh\necho "systemctl $*" >> "%s"\n'
                                          '[ "$1" = show ] && echo "%s"\nexit 0\n' % (log, need)),
                           ("ip", "#!/bin/sh\nexit 1\n"), ("pkill", "#!/bin/sh\nexit 1\n")):
            p = os.path.join(bin_, name)
            with open(p, "w") as f:
                f.write(text)
            os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC)
        units = os.path.join(d, "units")
        os.mkdir(units)
        env = dict(os.environ, PATH=bin_ + ":" + os.environ["PATH"], K3_UNIT_DIR=units, AWG_DIR=d, U=units)
        r = subprocess.run(["bash", "-c", script], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        if not os.path.exists(log):
            return "", units
        with open(log) as f:
            return f.read(), units

    def test_the_drop_in_of_a_removed_leg_goes_with_a_reload(self):
        sh = read("install.sh")
        fns = re.search(r"^egress_leg_unit\(\) \{.*\}$", sh, re.M).group(0) + "\n"
        fns += body(sh, "egress_awg_leg_remove")
        calls, units = self.run_sh(fns + 'mkdir -p "$U/kaskad-leg@kawg6.service.d"; : > "$U/kaskad-leg@kawg6.service.d/kernel.conf"\n'
                                         'egress_awg_leg_remove kawg6\n')
        self.assertFalse(os.path.exists(os.path.join(units, "kaskad-leg@kawg6.service.d")))
        self.assertIn("systemctl daemon-reload", calls)
        calls, _units = self.run_sh(fns + "egress_awg_leg_remove kawg7\n")          # no drop-in: nothing to read again
        self.assertNotIn("daemon-reload", calls)

    def test_reload_only_when_systemd_asks(self):
        fn = body(read("install.sh"), "k3_reload_if_stale")
        calls, _u = self.run_sh(fn + "k3_reload_if_stale kaskad-egress\n", need="yes")
        self.assertIn("systemctl show -p NeedDaemonReload --value kaskad-egress", calls)
        self.assertIn("systemctl daemon-reload", calls)
        calls, _u = self.run_sh(fn + "k3_reload_if_stale kaskad-egress\n", need="no")
        self.assertNotIn("daemon-reload", calls)

    def test_the_egress_is_restarted_after_it(self):
        ws = body(read("install.sh"), "egress_write_service")
        self.assertRegex(ws, r'k3_reload_if_stale "\$\{EGRESS_SERVICE\}"\n\s+systemctl restart "\$\{EGRESS_SERVICE\}"')


class PanelDeleteWordsTest(unittest.TestCase):
    """П4: DELETE gw/exits/<id> answers twin, moved and text — the panel says them under «Ключ удалён»."""

    def call(self, res):
        from kaskad.panel import api
        req = mock.Mock()
        req.match.group.return_value = "e-0123456789"
        with mock.patch.object(api.ops, "exit_del", return_value=res), mock.patch.object(api, "_changed"), \
                mock.patch.object(api, "runner"):
            return json.loads(api.gw_exit_del(req).body.decode("utf-8"))

    def test_the_api(self):
        said = "Маршруты перешли на «Аня IP» — тот же ключ."
        out = self.call({"ok": True, "id": "e-0123456789", "left": 2, "warnings": [], "twin": "e-9876543210",
                         "moved": 1, "text": said})
        self.assertEqual((out["twin"], out["moved"], out["text"]), ("e-9876543210", 1, said))
        out = self.call({"ok": True, "id": "e-0123456789", "left": 2, "warnings": []})
        self.assertNotIn("text", out)
        self.assertNotIn("twin", out)

    def test_the_panel(self):
        js = read("lib", "kaskad", "panel", "static", "app.js")
        # этап 1 (T8): удаление ключа — Kit.confirmAction плитки; слова ядра — в тосте после записи
        self.assertIn('if (how === "ok" && okKey) toast(t(okKey, { name }), res && typeof res.text === "string" ? res.text : "", "success");', js)
        self.assertIn('onDone: exitActsDone("ex.deletedT", exitDelName(r), true, r.id)', js)
        self.assertIn('const said = o.okText && r && typeof r.text === "string" ? r.text : "";', js)
        self.assertIn('(said ? said + " " : "")', js)


if __name__ == "__main__":
    unittest.main()
