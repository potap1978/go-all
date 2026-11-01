"""3.3.2, критика T-332 (исполнитель P): термины глоссария (docs/k3/GLOSSARY-en.md, «Решения по терминам после
критики») в английских каталогах Python lib/kaskad/i18n/catalog/en/*.json и предпросмотр копии настроек без
внутренних кодов протоколов. Каждая проверка — с явным белым списком исключений и причиной."""

import glob
import json
import os
import re
import unittest

from kaskad import backup
from kaskad.gw import issue_os, ops
from kaskad.i18n import use_lang

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib", "kaskad", "i18n", "catalog")


def _catalog(lang):
    out = {}
    for f in sorted(glob.glob(os.path.join(ROOT, lang, "*.json"))):
        with open(f, encoding="utf-8") as fh:
            out.update(json.load(fh))
    return out


EN = _catalog("en")
RU = _catalog("ru")

# Строки с подписями сторонних приложений в кавычках правит координатор целиком (общий бриф): термины в них не трогаем.
COORDINATOR = {
}


def _hits(pattern, allow=(), flags=0):
    rx = re.compile(pattern, flags)
    return sorted(k for k, v in EN.items() if rx.search(v) and k not in allow and k not in COORDINATOR)


class TermsTest(unittest.TestCase):
    def test_server_not_node(self):
        allow = {
            "setup.steps.modeBad": "“node” — значение режима в API мастера (node|classic), в кавычках, не слово о машине",
        }
        self.assertEqual(_hits(r"\bnodes?\b", allow, re.I), [])

    def test_forwards_are_not_rules(self):
        fwd = [k for k in EN if k.startswith(("bot.fwd.", "cli.fwd.")) or k == "bot.menu.btn_rules"]
        words = dict((k, re.sub(r"\{[a-z_]+\}", "", EN[k])) for k in fwd)      # {rule} — имя подстановки, не слово
        self.assertEqual([k for k in fwd if re.search(r"\brules?\b", words[k], re.I)], [])
        self.assertEqual(EN["bot.menu.btn_rules"], "📋 Forwards")
        self.assertEqual(EN["cli.fwd.deleted"], "Forward deleted.")

    def test_rules_are_routing_rules(self):
        # «Rules» без уточнения не используем: в начале фразы — только «Routing rules»
        self.assertEqual(_hits(r"(?<![Rr]outing )\bRules\b"), [])
        for k in ("rules.cli.title", "rules.cli.help.rules", "panel.scheme.rulesSoon", "setup.steps.rulesSaved",
                  "gw_cli.status.rules", "panel.scheme.pathText"):
            self.assertRegex(EN[k], r"[Rr]outing rules", k)

    def test_userspace_not_program(self):
        allow = {
            "gw_awgengine.err.target": "program — значение аргумента CLI (kernel|program), рядом пояснено userspace",
            "gw_cli.help.awg_engine": "program — значение аргумента CLI (kernel|program), рядом пояснено userspace",
            "panel.api.badEngine": "program — значение поля API «engine»",
        }
        self.assertEqual(_hits(r"amneziawg-go program|\bthe program\b(?! —)|[Ss]witch to (?:the )?program|as the "
                               r"amneziawg-go|\bprogram \(userspace|\band the program\b", allow), [])
        for k in allow:
            if k != "panel.api.badEngine":
                self.assertIn("program (userspace", EN[k], k)
        self.assertEqual(EN["bot.gw.btn_to_program"], "🐢 Switch to userspace")
        self.assertEqual(EN["setup.steps.moduleProgram"], "userspace")

    def test_access_from_the_internet(self):
        allow = {
            "migrate.stay.masq_kaskad": "правило iptables MASQUERADE (выход в интернет), не доступ к панели",
            "migrate.stay.masq_many": "правило iptables MASQUERADE (выход в интернет), не доступ к панели",
            "migrate.stay.masq_one": "правило iptables MASQUERADE (выход в интернет), не доступ к панели",
        }
        self.assertEqual(_hits(r"[Ii]nternet access|[Pp]ublic mode", allow), [])
        self.assertIn("“Access from the internet”", EN["panel.userpage.noteClosed"])
        self.assertIn("“Web panel”", EN["panel.userpage.noteClosed"])

    def test_core_not_engine(self):
        # engine — только выбор режима (модуль/userspace) и движок пробросов; бинари — AmneziaWG core
        self.assertEqual(_hits(r"no AmneziaWG engine|AWG core|gateway core"), [])

    def test_network_interface_not_card(self):
        self.assertEqual(_hits(r"network card|narrow|\bcard\b"), [])

    def test_client_address(self):
        self.assertEqual(_hits(r"address for (?:clients|devices)|server address for devices", flags=re.I), [])

    # «человек» = тот, кто выбирает движок или ведёт ИИ-агента (администратор узла), не пользователь шлюза
    # (24.10, «Пользователи вместо людей»; тот же список — people в tools/k3_text_exceptions.tsv); «other people’s
    # sites» — люди вообще
    OPERATOR = {"gw_awgengine.why.person.bot", "gw_awgengine.why.person.install", "gw_awgengine.why.person.menu",
                "gw_awgengine.why.person.menu_update", "gw_awgengine.why.person.panel", "gw_awgengine.why.person.wizard",
                "gw_cli.help.program", "gw_in_vless.need_consent", "gw_in_vless.consent.a.body", "host_cli.set.preview",
                "host_cli.set.retry_heavy_mcp", "host_sched.err.heavy_mcp", "tariff.err.code_mismatch",
                "tariff.err.code_needed"}

    def test_user_not_person(self):
        # владелец 24.10: «заменим везде человек на пользователя, а у пользователя могут быть устройства» — было
        # test_person_not_user (глоссарий 3.3.2: person / people)
        self.assertEqual(_hits(r"\b[Pp]ersons?\b|(?<!\{)\b[Pp]eople\b|Person page", self.OPERATOR), [])   # {people} — заполнитель
        for k in [k for k in EN if k.startswith(("gw_cli.user.", "journal.page."))] + ["gw_cli.help.users",
                                                                                        "rules.cli.who_user"]:
            self.assertNotRegex(EN[k], r"\b[Pp]ersons?\b|\b[Pp]eople\b", k)
        self.assertEqual(sorted(k for k in self.OPERATOR if not re.search(r"\b[Pp]erson|\b[Pp]eople", EN[k])), [],
                         "исключение больше не нужно — уберите ключ из OPERATOR")

    def test_exit_key_is_deleted(self):
        keys = [k for k in EN if k.startswith(("gw_ops.exit.", "gw_cli.exit.")) or k in (
            "gw_cli.help.exit_del", "setup.steps.exitRemoved", "summary.advice.exits_same")]
        self.assertEqual([k for k in keys if re.search(r"\b[Rr]emov", EN[k])], [])

    def test_en_us_and_typography(self):
        # «Needs attention» — уровень crit словами панели (ovb.hh.crit; владелец 28.10 «статусы одними словами везде»),
        # не обращение «Attention» (оно — «Please note», глоссарий)
        self.assertEqual(_hits(r"neighbou?r|licence|\bgrey\b|colour|cancelled|behaviour|Attention",
                               allow=("gw_host.light.crit",), flags=re.I), [])
        self.assertEqual(_hits(r"\.\.\."), [])
        self.assertEqual(_hits(r"\}ms\b|\dms\b"), [])
        self.assertEqual(_hits(r"[A-Za-z]'[a-z]"), [], "апостроф в EN — типографский ’")

    def test_bot_buttons_and_wording(self):
        self.assertEqual(EN["bot.menu.page_next"], "Next ➡️")
        self.assertEqual(EN["bot.menu.page_prev"], "⬅️ Back")
        self.assertNotIn("did not", EN["bot.fwd.probe_none"])
        self.assertNotIn("carried out", EN["bot.menu.stale_message"])
        self.assertNotIn("turned on on", EN["site_cli.mode.on"])

    def test_one_word_for_the_placeholder_page(self):
        # critic2 №19: одно имя — placeholder page / страница-заглушка (бот, CLI, веб, SSH)
        self.assertEqual(_hits(r"stub page|simple page|simple stub|simple placeholder", flags=re.I), [])
        for k in ("bot.site.simple_page", "site_cli.mode.simple_page"):
            self.assertEqual(EN[k], "placeholder page", k)
            self.assertEqual(RU[k], "страница-заглушка", k)
        self.assertEqual(sorted(k for k, v in RU.items() if re.search(r"[Пп]рост(ая|ую) страниц|поставлена заглушка", v)),
                         [])

    def test_update_kaskad_and_engines(self):
        # critic2 №12, 13: «Update Kaskad», engine — только AmneziaWG engine или forwarding engine
        self.assertEqual(_hits(r"[Uu]pdate the program"), [])
        self.assertIn("update Kaskad", EN["summary.advice.command"])
        self.assertIn("обновите Kaskad", RU["summary.advice.command"])
        left = []
        for k, v in EN.items():
            for m in re.finditer(r"\b[Ee]ngines?\b", v):
                before = v[:m.start()]
                if not re.search(r"(AmneziaWG|[Ff]orwarding|nftables|iptables|awg) $", before) and \
                        not re.match(r"[”\"]", v[m.end():]) and "{engine}" not in v[m.start() - 1:m.end() + 1]:
                    left.append(k)
        allow = {
            "gw_awgengine.log.reconcile": "журнал: «GW AWG engine» — AmneziaWG",
            "panel.api.badEngine": "“engine” — имя поля API",
        }
        self.assertEqual(sorted(set(left) - set(allow)), [])

    def test_small_wording(self):  # critic2 №15–17, 23
        self.assertNotIn("crashed out of memory", EN["gw_awgengine.benefit.memory"] + EN["gw_awgengine.pays.memory"])
        self.assertIn("“node” (protocol translator)", EN["setup.steps.modeBad"])
        self.assertIn("match the mode to the server’s site", EN["gw_cli.help.sync"])
        self.assertTrue(EN["gw_issue.ros.paste_menu"].count("If it doesn’t copy in full (small window):"))
        self.assertEqual(_hits(r"user \(a person\)"), [])      # «for a user» — снова норма (24.10, user вместо person)

    def test_platforms(self):
        self.assertEqual(EN["bot.platform.ios"], "iPhone / iPad")
        self.assertEqual(EN["setup.product.platformIos"], "iPhone / iPad")
        self.assertEqual(_hits(r"iPhone (?:and|or) iPad\b(?! →)(?: \(iOS\))?$"), [])

    def test_domain_and_site_label(self):
        for k in ("backup.note.cert", "backup.note.site_mode"):
            self.assertIn("“Domain and site”", EN[k], k)
            self.assertIn("«Домен и сайт»", RU[k], k)
            self.assertNotIn("«Домен»", RU[k], k)


class OneSourceOfPlatformNotesTest(unittest.TestCase):
    def test_offer_notes_are_the_issue_notes(self):
        self.assertEqual([k for k in EN if k.startswith("gw_ops.offers.note.")], [])
        self.assertEqual([k for k in RU if k.startswith("gw_ops.offers.note.")], [])
        for lang in ("en", "ru"):
            with use_lang(lang):
                for p in list(issue_os.NOTES) + ["windows", "other"]:
                    self.assertEqual(ops.offer_notes(p), list(issue_os.NOTES.get(p, ())), (lang, p))
        with use_lang("en"):
            self.assertIn("the router won’t work", ops.offer_notes("tplink")[0])


class BackupPreviewNamesTest(unittest.TestCase):
    """№12: предпросмотр копии называет протоколы (AmneziaWG 2.0, WireGuard, L2TP/IPsec), а не коды awg2, wg, l2tp."""

    F = {"present": True, "users": 2, "devices": 3, "inbounds": ["awg2", "l2tp", "wg"]}
    H = {"present": True, "users": 1, "devices": 1, "inbounds": ["awg3", "wg"]}
    CODES = re.compile(r"\b(?:awg[123]|wg|l2tp|ikev2|sstp|ocserv|socks5|pptp|openvpn|vless)\b")

    def test_row_names_protocols(self):
        for lang in ("en", "ru"):
            with use_lang(lang):
                text = backup._row("gateway", self.F, self.H)["text"]
            self.assertIn("AmneziaWG 2.0, L2TP/IPsec, WireGuard", text, lang)
            self.assertIn("AmneziaWG 3.1, WireGuard", text, lang)
            self.assertNotRegex(text, self.CODES, lang)

    def test_row_without_protocols(self):
        with use_lang("en"):
            text = backup._row("gateway", dict(self.F, inbounds=[]), {"users": 0, "devices": 0, "inbounds": []})["text"]
        self.assertTrue(text.endswith("protocols: none"), text)


if __name__ == "__main__":
    unittest.main()
