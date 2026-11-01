"""3.3.2, живая проверка T-M6-332 (k3/stand/logs/T-M6-332.md), доводка le:

№2  коды протоколов человеку: «Сводка» мастера («…, AmneziaWG 3.1, l2tp, VLESS» — названия отдаёт сервер, а не
    угадывает страница), бот «Выходы» («(awg 3.1)», «(vless)» — тест в tests/bot/test_gateway.py).
№15 «owner» → person / default: профиль правил «inherit» у человека — «same as default», у устройства — «same as
    the person» (RU «как у владельца» — как в 3.3.1).
№5  журнал пробросов на английском сервере — «ADD forward:», «DELETE forward:», «FLUSH all kaskad forwards» (RU — как
    в 2.x/3.3.1); SSH и bash-журнал — tests/test_i18n_terms_sh.py.
№16 подпись под видео-карточкой мастера: подсвечивается содержимое этапа (слева от видео), а не «settings».
№19 журнал «GW AWG engine: program → kernel» на английском — «userspace → kernel module».
№22 «Self-update -> …» — на языке сервера (tests/test_i18n_terms_sh.py)."""

import json
import os
import re
import unittest
from unittest import mock

from kaskad import forwards, i18n, state
from kaskad.gw import awgengine, model as gmodel
from kaskad.i18n import tr, use_lang
from kaskad.rules import cli as rcli
from kaskad.setup import steps
from tests.fakes_nft import World, save_state
from tests.helpers import RootCase
from tests.test_qs330_e import WizardCase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOG = os.path.join(ROOT, "lib", "kaskad", "i18n", "catalog")
CODE = re.compile(r"\b(awg[123]|wg|l2tp|ikev2|sstp|ocserv|socks5|pptp|openvpn|vless)\b")


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def catalog(lang):
    out = {}
    for name in sorted(os.listdir(os.path.join(CATALOG, lang))):
        if name.endswith(".json"):
            with open(os.path.join(CATALOG, lang, name), encoding="utf-8") as f:
                out.update(json.load(f))
    return out


class SummaryTitlesTest(WizardCase):
    def test_every_protocol_by_its_name(self):
        kinds = ["wg", "awg2", "awg3", "l2tp", "vless"]
        with mock.patch.object(gmodel, "enabled_kinds", lambda st: list(kinds)):
            out = steps.run("summary", {}, self.runner)
        d = out["data"]
        self.assertEqual(d["protocols"], kinds)              # the codes stay: the page tests /^awg/ by them
        self.assertEqual([d["titles"][k] for k in kinds],
                         ["WireGuard", "AmneziaWG 2.0", "AmneziaWG 3.1", "L2TP/IPsec", "VLESS"])
        for k in kinds:
            self.assertIsNone(CODE.fullmatch(d["titles"][k]), k)

    def test_the_page_takes_the_names_of_the_server(self):
        js = read("lib/kaskad/panel/static/setup/setup.js")
        self.assertIn('arr(d.protocols).map((k) => (d.titles || {})[k] || inboundTitle(k))', js)
        mock_js = read("lib/kaskad/panel/static/setup/dev-mock-setup.js")
        self.assertIn("titles: INSTALLED.reduce(", mock_js)    # снимки страницы — с теми же названиями


class InheritWordsTest(unittest.TestCase):
    def test_person_and_device(self):
        with use_lang("en"):
            self.assertEqual(rcli.profile_text("inherit", "user"), "same as default")
            self.assertEqual(rcli.profile_text("inherit", "device"), "same as the user")
            self.assertEqual(rcli.profile_text("inherit"), "same as the user")
            self.assertEqual(rcli.profile_text("ru_direct", "user"), "Russian sites direct")
            self.assertEqual(tr("rules.cli.profile_set", who=tr("rules.cli.who_user"), name="qsen",
                                profile=rcli.profile_text("inherit", "user")).count("owner"), 0)
            self.assertEqual(tr("bot.gw.btn_as_owner"), "Same as user")
            self.assertEqual(tr("bot.gw.route_as_owner"), "same as the user")
        with use_lang("ru"):                                   # RU — как в 3.3.1
            self.assertEqual(rcli.profile_text("inherit", "user"), "как у владельца")
            self.assertEqual(rcli.profile_text("inherit", "device"), "как у владельца")

    def test_owner_only_of_the_bot_or_the_server(self):
        # «owner» в английских текстах — владелец бота/сервера (админ), не человек устройства
        allow = {"bot.cli.h_send_test", "bot.lang.owner_only", "bot.menu.not_bound", "bot.menu.not_bound_toast",
                 "backup.row.bot", "gw_in.ufw.denied", "gw_in_ipsec.log.rotate", "gw_in_sstp.pppd_note",
                 "gw_gate.comment_owner", "setup.steps.botWaitStart"}
        en = catalog("en")
        self.assertEqual(sorted(k for k, v in en.items() if re.search(r"\b[Oo]wner", v) and k not in allow), [])
        web = read("lib/kaskad/panel/static/i18n-en.js") + read("lib/kaskad/panel/static/setup/setup-i18n-en.js") \
            + read("lib/kaskad/panel/static/graph-i18n-en.js") + read("lib/kaskad/panel/static/user-i18n-en.js")
        lines = [ln for ln in web.splitlines() if re.search(r":\s*\".*\b[Oo]wner", ln) and "bk.sBot" not in ln]
        self.assertEqual(lines, [])


class ForwardJournalTest(RootCase):
    def setUp(self):
        super().setUp()
        self.w = World()
        self._ssh = os.environ.pop("SSH_CONNECTION", None)
        self.addCleanup(lambda: os.environ.__setitem__("SSH_CONNECTION", self._ssh) if self._ssh is not None else None)

    def log(self):
        return self.read("/var/log/kaskad.log").rstrip().splitlines()

    def test_english_server(self):
        save_state()
        i18n.set_lang("en")
        forwards.add_forward("tcp", 443, "203.0.113.5", 443, runner=self.w.runner, name="Vienna")
        forwards.add_forward("udp", 51820, "198.51.100.7", 51820, runner=self.w.runner)
        forwards.delete_forward("tcp", 443, runner=self.w.runner)
        forwards.flush_forwards(runner=self.w.runner)
        log = self.log()
        self.assertTrue(log[0].endswith("] ADD forward: tcp :443 -> 203.0.113.5:443 (Vienna)"), log)
        self.assertTrue(log[1].endswith("] ADD forward: udp :51820 -> 198.51.100.7:51820"), log)
        self.assertTrue(log[2].endswith("] DELETE forward: tcp :443 -> 203.0.113.5:443"), log)
        self.assertTrue(log[3].endswith("] FLUSH all kaskad forwards"), log)
        self.assertFalse([ln for ln in log if re.search(r"\brules?\b", ln)], log)

    def test_russian_server_as_before(self):
        save_state()
        forwards.add_forward("tcp", 443, "203.0.113.5", 443, runner=self.w.runner, name="Вена")
        self.assertTrue(self.log()[-1].endswith("] ADD rule: tcp :443 -> 203.0.113.5:443 (Вена)"))


class EngineJournalTest(unittest.TestCase):
    def test_words(self):
        with use_lang("en"):
            self.assertEqual((awgengine.LOG_WORD["program"], awgengine.LOG_WORD["kernel"]), ("userspace", "kernel module"))
        with use_lang("ru"):                                   # RU — как в 3.3.1 (коды)
            self.assertEqual((awgengine.LOG_WORD["program"], awgengine.LOG_WORD["kernel"]), ("program", "kernel"))
        self.assertEqual(awgengine.LOG_WORD.get(None, None), None)
        src = read("lib/kaskad/gw/awgengine.py")
        self.assertIn('LOG_WORD.get(was, was), LOG_WORD.get(target, target)', src)
        self.assertNotIn('"GW AWG engine: %s → %s (%s)" % (was, target,', src)


class VideoTipTest(unittest.TestCase):
    def test_no_video_column_a_link_in_the_head(self):
        # 3.3.3 (UX-333): колонки «Видео к шагу» нет — в шапке этапа «▶ Видео — скоро» (ролика нет) или ссылка на ролик
        css = read("lib/kaskad/panel/static/setup/qs.css")
        self.assertNotIn('"main video"', css)
        self.assertNotIn(".qs-video", css)
        js = read("lib/kaskad/panel/static/setup/setup.js")
        self.assertIn("function videoLink(kind)", js)
        self.assertIn('"aria-disabled": "true", "data-k": "vid-soon"', js)
        self.assertIn('"vid.soon": "Video — coming soon"', read("lib/kaskad/panel/static/setup/setup-i18n-en.js"))
        self.assertIn('"vid.soon": "Видео — скоро"', read("lib/kaskad/panel/static/setup/setup-i18n-ru.js"))


if __name__ == "__main__":
    unittest.main()
