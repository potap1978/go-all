"""3.3.7.2, круг 3 (REV-3372-2): «Вернуть как было» без предупреждения о ключах агентов «из копии» (З-1); сброс
conntrack DNS при полном ``gw apply`` (З-2); мастер говорит, где перенести ключи агентов позже (З-3); U+2028 и соседи
у ``tz.set_name`` и ``i18n.set_lang`` (П-1); зона IPv6 резолвера (П-2); IPv4 link-local резолвер (П-3)."""

import os
import subprocess
import sys
from unittest import mock

from kaskad import backup, i18n, paths, state, tz, util
from kaskad.gw import dns, net
from kaskad.gw import sys as gsys
from kaskad.setup import steps
from tests.fakes_gw import VLESS_A, install_parser, write_pool
from tests.fakes_nft import F443, save_state
from tests.gwcase import GwCase
from tests import test_3372_r2 as r2
from tests.test_backup import PW, BackupCase
from tests.test_gw_ops import CliGwCase


class UndoAgentWordsTest(BackupCase):
    """З-1: «Вернуть как было» возвращает собственные ключи узла — без «! Ключи ИИ-агентов из копии … добавятся»;
    обычный импорт с отметкой (kaskadctl --agents) — с ним."""

    _copy = r2.BackupAgentKeysTest._copy

    def test_undo_says_no_word_of_keys_of_the_copy(self):
        from kaskad.mcp import keys
        from tests.test_333d_backend import ed25519
        _, _, doc = self._copy()
        line, own = ed25519(9)
        keys.add(line, label="own")                              # the node's own agent, before the import
        backup.apply(self.w.runner, doc)                         # an import without the tick: own key stays
        res = backup.undo(self.w.runner)
        warn = backup.tr("backup.preview.mcp_on", n=1)
        self.assertNotIn("! %s" % warn, res["text"])
        self.assertFalse([t for t in res["text"] if "ИИ-агентов из копии" in t], res["text"])
        self.assertIn(own, str(keys.listing()["keys"]))          # the own key is there after the return

    def test_an_import_with_the_tick_says_it(self):
        _, _, doc = self._copy()
        res = backup.apply(self.w.runner, doc, agents=True)
        self.assertIn("! %s" % backup.tr("backup.preview.mcp_on", n=1), res["text"])

    def test_preview_of_a_return_offers_the_keys_on_without_the_warning(self):
        _, _, doc = self._copy()
        pv = backup.preview(self.w.runner, doc, exact=True)
        self.assertEqual(pv["agents"], {"keys": 1, "on": True})
        self.assertNotIn(backup.tr("backup.preview.mcp_on", n=1), pv["warnings"])
        self.assertEqual([r["action"] for r in pv["parts"] if r["part"] == "mcp"], ["add"])


class WizardAgentWordsTest(BackupCase):
    """З-3: в мастере отметки нет — строка части говорит, где перенести ключи позже (панель / меню 16 → 6 → 2),
    а не «отдельной отметкой»."""

    _copy = r2.BackupAgentKeysTest._copy

    def test_the_row_of_the_wizard(self):
        _, _, doc = self._copy()
        pv = backup.preview(self.w.runner, doc)
        view = steps._backup_view(pv, "t", 0)
        row = [r for r in view["parts"] if r["part"] == "mcp"][0]
        self.assertEqual(row["action"], "none")
        self.assertEqual(row["text"], backup.tr("setup.steps.backupAgentsLater", keys=1))
        self.assertIn("16 → 6 → 2", row["text"])
        self.assertNotIn("отметкой и только", row["text"])
        others = [r for r in view["parts"] if r["part"] != "mcp"]
        self.assertEqual([r["text"] for r in others], [r["text"] for r in pv["parts"] if r["part"] != "mcp"])

    def test_both_languages(self):
        for lang, word in (("ru", "Загрузить копию"), ("en", "Upload backup")):
            with i18n.use_lang(lang):
                t = backup.tr("setup.steps.backupAgentsLater", keys=2)
            self.assertIn(word, t)
            self.assertIn("16 → 6 → 2", t)
            self.assertIn("2", t)


class GwApplyFlushesDirectDnsTest(CliGwCase):
    """З-2: смена маршрута человеком идёт полным ``gw apply`` (update_map потом видит «same») — сам apply забывает
    потоки DNS к kaskad-gw-dnsdirect у тех, кто сейчас не «напрямую»; чужие (не из сетей шлюза) не трогает."""

    def setUp(self):
        super().setUp()
        save_state([F443])
        install_parser()
        write_pool([VLESS_A])
        self.ok("gw", "enable")
        dns.apply(state.load(), self.w.runner, via_pool=True, direct=True)
        self.assertTrue(os.path.exists(gsys.unit_path(dns.UNIT_DIRECT)))

    def test_gw_apply_sweeps_the_flows_of_devices_off_direct(self):
        ct = self.w.conntrack
        ct.dns_flows = {("10.77.1.10", "udp"): 2, ("10.77.1.10", "tcp"): 1, ("198.51.100.1", "udp"): 1}
        ct.calls[:] = []
        self.ok("gw", "apply")
        self.assertEqual(ct.dns_flows, {("198.51.100.1", "udp"): 1})
        self.assertIn(["-D", "-s", "10.77.1.10", "-p", "udp", "--reply-port-src", "5364"], ct.calls)

    def test_a_failing_conntrack_does_not_fail_the_apply(self):
        from kaskad.errors import KaskadError
        with mock.patch.object(net, "flush_direct_dns", side_effect=KaskadError("x")):
            self.ok("gw", "apply")


class ConfigWritersLineBreaksTest(BackupCase):
    """П-1: U+2028/U+2029/U+0085/U+001C в значении, записанном как bash (``save_config_val`` их не вырезает), — после
    ``tz.set_name`` и ``i18n.set_lang`` строка остаётся одной: ``source`` не выполняет хвост как команду."""

    def _check(self, write):
        cfg = paths.config()
        scratch = self.path("/tmp/pwned")
        for ch in (" ", " ", "\x85", "\x1c"):
            util.atomic_write(cfg, 'BOT_PROXY="x%stouch %s #"\nMENU_STYLE="inline"\n' % (ch, scratch), 0o600)
            write()
            text = util.read_text(cfg)
            self.assertIn('BOT_PROXY="x%stouch %s #"\n' % (ch, scratch), text, repr(ch))   # one line, as bash sees it
            subprocess.run(["bash", "-c", 'source "$1"', "_", cfg], capture_output=True, timeout=30)
            self.assertFalse(os.path.exists(scratch), repr(ch))

    def test_tz_set_name(self):
        self._check(lambda: tz.set_name("UTC"))
        self.assertIn('KASKAD_TZ="UTC"', util.read_text(paths.config()))

    def test_i18n_set_lang(self):
        self._check(lambda: i18n.set_lang("en"))
        self.assertIn('KASKAD_LANG="en"', util.read_text(paths.config()))


class ResolverAddressTest(GwCase):
    """П-2: зона IPv6 — только ``[A-Za-z0-9._-]``, иначе адрес отброшен (``%x@eth0`` не доходит до dnsmasq).
    П-3: IPv4 link-local (облачный резолвер 169.254.169.254) — рабочий upstream, не отбрасывается."""

    def test_a_zone_with_other_characters_drops_the_address(self):
        st = self.scenario(save=False)
        self.write("/etc/resolv.conf", "nameserver 2a00:1450::1%x@eth0\nnameserver 2a00:1450::2%a/b\n"
                   "nameserver 9.9.9.9\n", 0o644)
        self.assertEqual(dns.system_servers(st), ["9.9.9.9"])
        self.assertNotIn("@eth0", dns.direct_text_of(st, False))

    def test_a_plain_zone_is_kept_where_python_reads_it(self):
        st = self.scenario(save=False)
        self.write("/etc/resolv.conf", "nameserver 2a00:1450::1%eth0.10\nnameserver 9.9.9.9\n", 0o644)
        want = ["2a00:1450::1%eth0.10", "9.9.9.9"] if sys.version_info >= (3, 9) else ["9.9.9.9"]
        self.assertEqual(dns.system_servers(st), want)

    def test_ipv4_link_local_is_a_resolver_ipv6_link_local_is_not(self):
        st = self.scenario(save=False)
        self.write("/etc/resolv.conf", "nameserver fe80::1%eth0\nnameserver 169.254.169.254\n", 0o644)
        self.assertEqual(dns.system_servers(st), ["169.254.169.254"])
        self.assertIn("server=169.254.169.254\n", dns.direct_text_of(st, False))
