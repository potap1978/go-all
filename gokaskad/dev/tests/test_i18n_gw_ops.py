"""3.3.2 E1-ops: the English of kaskad.gw.ops, gate, hooks, peek, tls (k3/handoff/I18N-3.3.2.md).

* no Cyrillic left in the strings for people of these files; the catalogs gw_ops, gw_gate, gw_hooks, gw_peek, gw_tls
  complete (ru == en, same placeholders);
* the port of an inbound: the tails of kaskad.conflicts are replaced by their NAME (conflicts.effect), in both languages;
* the output of install.sh --egress-*: the core runs it with K3_MARKS=1 and the language of the call, reads the marks
  ``@K3:egress_…`` (codes -> its own words) and never shows them; the old Russian output (no marks) still works;
* errors, offers' notes, options, the module texts (ops.DUP_KEY_TEXT…) in English; the journal and the drop-ins of
  the inbounds — in the language of the server, whatever the language of the request.
"""

import os
import re
import unittest
from unittest import mock

from kaskad import conflicts, i18n, state, util
from kaskad.errors import ConflictError, KaskadError
from kaskad.gw import exits, gate, hooks, ops, peek, tls
from kaskad.i18n import check, use_lang
from kaskad.sysrun import FakeRunner
from tests.fakes_gw import VLESS_B
from tests.test_gw_exitsubs import SubsCase
from tests.serverlang import server

GW = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib", "kaskad", "gw")
FILES = [os.path.join(GW, f + ".py") for f in ("ops", "gate", "hooks", "peek", "tls")]
AREAS = ["gw_ops", "gw_gate", "gw_hooks", "gw_peek", "gw_tls"]
CYR = re.compile("[А-Яа-яЁё]")


class CatalogTest(unittest.TestCase):
    def test_no_russian_in_the_code_and_full_catalogs(self):
        check.assert_no_cyrillic(self, *FILES)
        check.assert_catalog_complete(self, AREAS)


class InboundWordsTest(unittest.TestCase):
    """_inbound_words: the tails of a forward («проброс отрежет вход…») -> the words of an inbound, by the name of the
    tail (conflicts.effect), in the language of the call — not by the Russian words."""

    def words(self, lang):
        with use_lang(lang):
            cause = "sshd listens here"
            return (ops._inbound_words(cause + conflicts.effect("ssh")),
                    ops._inbound_words(cause + conflicts.effect("foreign_dnat")),
                    ops._inbound_words(cause + conflicts.effect("gateway")),
                    ops._inbound_words(cause + conflicts.effect("listener")))

    def test_both_languages(self):
        ssh, dnat, gw, listener = self.words("ru")
        self.assertEqual(ssh, "sshd listens here — протокол на этом порту отрежет SSH-доступ к серверу")
        self.assertEqual(dnat, "sshd listens here — протокол шлюза его перекроет")
        self.assertEqual(gw, "sshd listens here")
        self.assertEqual(listener, "sshd listens here")     # a forward's tail, no word of a protocol (R-333 С1)
        ssh, dnat, gw, listener = self.words("en")
        self.assertEqual(ssh, "sshd listens here — a protocol on this port would cut off SSH access to the server")
        self.assertEqual(dnat, "sshd listens here — the gateway protocol would override it")
        self.assertEqual(gw, "sshd listens here")
        for text in (ssh, dnat, gw, listener):
            self.assertNotRegex(text, CYR)
            self.assertNotIn("forward", text)


# what install.sh 3.3.2 (F, k3-332f) prints in --egress-add with K3_MARKS=1 — here in English
EN_AWG_CORE = [
    "Couldn’t prepare the AmneziaWG core (amneziawg-go + awg) for amd64.",
    "It needs bin/amneziawg-go-linux-amd64 and bin/awg-linux-amd64 from the anten-ka/kaskad-pro repository.",
    "The repository is private: without a GitHub key it returns 404. What to do (any of):",
    "1) a GitHub key — menu 8 → 6 (or the GH_TOKEN variable when starting gokaskad);",
    "2) a kaskad-pro clone with bin/ in /opt/kaskad-pro;",
    "3) put the files in place by hand: /etc/kaskad/egress/awg/amneziawg-go and /etc/kaskad/egress/awg/awg.",
    "@K3:egress_fail:awg_core",
    "AWG core unavailable — bringing up VLESS keys only (AWG skipped).",
    "@K3:egress_warn:awg_core_missing",
    "The pool has AWG keys only, and the AmneziaWG core didn’t load.",
    "@K3:egress_fail:awg_only",
    "Key not added — the pool is left as it was.",
    "@K3:egress_fail:rolled_back",
]


class ScriptMarksTest(unittest.TestCase):
    def test_the_error_is_said_by_the_codes_in_the_language_of_the_call(self):
        for lang in ("en", "ru"):
            with use_lang(lang):
                e = ops._script_error(EN_AWG_CORE, "x")
            self.assertNotIn("@K3", e)
            if lang == "en":
                self.assertNotRegex(e, CYR)
                self.assertIn("What to do (any of): 1) a GitHub key — menu 8 → 6", e)
                self.assertLess(e.index("What to do"), e.index("The pool has AmneziaWG keys only"))
            else:
                self.assertIn("Что сделать (любое из): 1) ключ GitHub — меню 8 → 6", e)
                self.assertIn("В пуле только AWG, но ядро AmneziaWG не загрузилось.", e)
            # rolled_back: the script's own line (add / replace differ) — it is in the language the core asked for
            self.assertTrue(e.endswith("Key not added — the pool is left as it was."), e)
            self.assertIn("/etc/kaskad/egress/awg/amneziawg-go", e)

    def test_xray_reason_comes_from_the_script_line(self):
        lines = ["[*] Rebuilding the pool…", "xray rejected the config: invalid shortId (sid).",
                 "@K3:egress_fail:xray_invalid", "Key not added — the pool is left as it was.",
                 "@K3:egress_fail:rolled_back"]
        with use_lang("en"):
            self.assertEqual(ops._script_error(lines, "x"),
                             "xray rejected the config: invalid shortId (sid). Key not added — the pool is left as it was.")
            self.assertEqual(ops._script_error(["@K3:egress_fail:xray_invalid"], "x"), "xray rejected the config.")
            self.assertEqual(ops._script_error(["@K3:egress_same_peer:1"], "dflt"), "dflt")

    def test_the_cause_as_a_code_in_the_words_of_the_core(self):
        # сведение 3.3.2: метки F «rolled_back:add|replace», «xray_invalid:field:<поле>[:reality]|none|other» — ядро
        # говорит своими словами на языке вызова; строка скрипта перед меткой (язык сервера) — только запасной путь
        ru_said = ["Конфиг xray невалиден: неверное поле shortId (sid).", "@K3:egress_fail:xray_invalid:field:shortid:reality",
                   "Пул не заменён — оставлен прежним.", "@K3:egress_fail:rolled_back:replace"]
        with use_lang("en"):
            e = ops._script_error(ru_said, "x")
            self.assertEqual(e, "xray rejected the config: wrong field shortId (sid) — check publicKey (pbk) and shortId "
                                "(sid) in the key: the VLESS server issues them. Pool not replaced — left as it was.")
            self.assertIsNone(re.search("[А-Яа-яЁё]", e))
            self.assertEqual(ops._script_error(["x", "@K3:egress_fail:xray_invalid:field:password",
                                                "y", "@K3:egress_fail:rolled_back:add"], "d"),
                             "xray rejected the config: wrong field publicKey (pbk). Key not added — the pool is left as it was.")
            self.assertEqual(ops._script_error(["@K3:egress_fail:xray_invalid:none"], "d"),
                             "xray rejected the config: xray gave no reason.")
            # other: the reason is xray's own — the script's line; nothing before the mark — the core's general words
            self.assertEqual(ops._script_error(["xray rejected the config: bad port.", "@K3:egress_fail:xray_invalid:other"], "d"),
                             "xray rejected the config: bad port.")
            self.assertEqual(ops._script_error(["@K3:egress_fail:xray_invalid:other"], "d"), "xray rejected the config.")
        with use_lang("ru"):
            self.assertEqual(ops._script_error(ru_said, "x"),
                             "Конфиг xray невалиден: неверное поле shortId (sid) — проверьте в ключе publicKey (pbk) и "
                             "shortId (sid): их выдаёт сервер VLESS. Пул не заменён — оставлен прежним.")
            self.assertEqual(ops._script_error(["@K3:egress_fail:rolled_back:add"], "d"), "Ключ не добавлен — пул оставлен прежним.")

    def test_warnings_limit_and_github_key_by_the_marks(self):
        lines = EN_AWG_CORE + ["⚠ Key #2: AmneziaWG 3.1 won’t connect — it needs the 3.1 core.",
                               "@K3:egress_warn:awg_key:2:need31", "Key limit reached: 100.", "@K3:egress_limit:100",
                               "@K3:egress_warn:something_new"]
        with use_lang("en"):
            w = ops._script_warnings(lines)
        self.assertEqual(w, ["AmneziaWG core unavailable — bringing up VLESS keys only (AmneziaWG skipped).",
                             "Key #2: AmneziaWG 3.1 won’t connect — it needs the 3.1 core. GitHub key: menu 8 → 6, then "
                             "open this screen again.", "Key limit reached: 100."])
        with use_lang("ru"):
            w = ops._script_warnings(lines)
        self.assertEqual(w[0], "AWG-ядро недоступно — поднимаю только VLESS-ключи (AWG пропущены).")
        self.assertEqual(w[-1], "Достигнут предел 100 ключей.")
        self.assertTrue(ops._pool_full(lines))
        self.assertFalse(ops._pool_full(["Key limit reached: 100.", "@K3:egress_fail:rolled_back"]))   # by the code only
        from kaskad.gw.inbounds import awg as awg_inbound
        with mock.patch.object(awg_inbound, "needs_key", lambda: True):
            self.assertTrue(ops._needs_github_key(EN_AWG_CORE))
            self.assertFalse(ops._needs_github_key(["GitHub said no", "@K3:egress_fail:parse"]))

    def test_old_russian_output_without_marks(self):
        lines = ["[*] Пересобираю пул…", "Конфиг xray невалиден:", "Failed: user id 1111 is bad",
                 "Достигнут предел 100 ключей.", "Ключ не добавлен — пул оставлен прежним."]
        self.assertEqual(ops._script_error(lines, "x"), "Достигнут предел 100 ключей. Ключ не добавлен — пул оставлен прежним.")
        self.assertTrue(ops._pool_full(lines))
        self.assertEqual(ops._script_warnings(lines), ["Достигнут предел 100 ключей."])

    def test_script_env(self):
        with use_lang("en"):
            self.assertEqual(ops.script_env(), {"K3_MARKS": "1", "KASKAD_LANG": "en"})
        with use_lang("ru"):
            self.assertEqual(ops.script_env()["KASKAD_LANG"], "ru")


class ExitAddMarksTest(SubsCase):
    """exit_add / exit_del run gokaskad with K3_MARKS=1 and the language of the request; the answer in that language."""

    def script_envs(self, mode):
        return [env for argv, env in self.w.runner.envs if argv[1:2] == [mode]]

    def test_add_in_english(self):
        self.w.script.add_result = (1, "\x1b[0;31m" + "\n".join(EN_AWG_CORE) + "\x1b[0m\n", "")
        with use_lang("en"), self.assertRaises(KaskadError) as c:
            ops.exit_add(self.w.runner, VLESS_B)
        self.assertEqual(self.script_envs("--egress-add")[-1], {"K3_MARKS": "1", "KASKAD_LANG": "en"})
        self.assertNotRegex(str(c.exception), CYR)
        self.assertNotIn("@K3", str(c.exception))
        self.w.script.add_result = (5, "Key limit reached: 100.\n@K3:egress_limit:100\n", "")
        with use_lang("en"), self.assertRaises(ConflictError) as c:
            ops.exit_add(self.w.runner, VLESS_B)
        self.assertEqual(str(c.exception), "Key limit reached: 100. Delete a key you don’t need and add again.")
        self.w.script.add_result = None
        with use_lang("en"):
            doc = ops.exit_add(self.w.runner, VLESS_B)
            self.assertEqual(doc["text"], "Key added.")
            with self.assertRaises(ConflictError) as c:
                ops.exit_add(self.w.runner, VLESS_B)
        self.assertEqual(str(c.exception), "This key is already in the pool.")
        self.assertIn("GW exit add:", self.read("/var/log/kaskad.log"))

    def test_del_in_english(self):
        self.w.script.add_result = None
        ops.exit_add(self.w.runner, VLESS_B)
        eid = exits.exit_id(VLESS_B)
        self.w.script.del_rc = 1
        with use_lang("en"), self.assertRaises(KaskadError) as c:
            ops.exit_del(self.w.runner, eid)
        self.assertEqual(str(c.exception), "Key not deleted (code 1).")
        self.assertEqual(self.script_envs("--egress-del")[-1]["K3_MARKS"], "1")
        with use_lang("en"), self.assertRaises(KaskadError) as c:
            ops.exit_del(self.w.runner, "e-0000000000")
        self.assertEqual(str(c.exception), "This exit is already gone — reload the page.")


class EnglishTextsTest(unittest.TestCase):
    def test_errors_options_offers_and_module_texts(self):
        with use_lang("en"):
            with self.assertRaises(KaskadError) as c:
                ops.adapter("bogus")
            self.assertTrue(str(c.exception).startswith("unknown inbound 'bogus'; available: "), str(c.exception))
            with self.assertRaises(KaskadError) as c:
                ops.key_text("  ")
            self.assertEqual(str(c.exception), "Paste a VPN key (vless, hy2, ss, trojan, vmess, vpn://) or the whole text of an AmneziaWG .conf file.")
            self.assertEqual(ops.DUP_KEY_TEXT, "This key is already in the pool.")
            self.assertEqual(ops.OPTIONS[("socks5", "udp")], "UDP over SOCKS5")
            self.assertEqual(dict(ops.OPTIONS.items())[("l2tp", "plain")],
                             "L2TP without encryption (for routers without IPsec)")
            doc = ops.offers("ios", st=state.empty())
            self.assertEqual(doc["notes"], ["AmneziaVPN is hidden in the Russian App Store — use AmneziaWG or DefaultVPN."])
            for p in ("keenetic", "tplink", "huawei", "androidtv", "mikrotik", "openwrt", "xiaomi"):
                for n in ops.offers(p, st=state.empty())["notes"]:
                    self.assertNotRegex(n, CYR)
            with self.assertRaises(KaskadError) as c:
                hooks._check("wg", "sideways", "dev1", "10.0.0.1")
            self.assertEqual(str(c.exception), "the hook action must be up or down, not 'sideways'")
            r = FakeRunner()
            with self.assertRaises(KaskadError) as c:
                peek._items(r, ["chains", "ip"])
            self.assertTrue(str(c.exception).startswith("couldn’t read the ip kaskad table (chains ip): "))
            self.assertEqual(ops._who(["a", "b"] + ["x"] * 11), "a, b, x, x, x, x, x, x, x, x and 3 more")
        with use_lang("ru"):
            self.assertEqual(ops.DUP_KEY_TEXT, "Такой ключ уже есть в пуле.")
            self.assertEqual(ops.OPTIONS[("socks5", "udp")], "UDP через SOCKS5")
            self.assertEqual(ops.offers("huawei", st=state.empty())["notes"], ["У этих роутеров обычно только L2TP и PPTP."])
        with self.assertRaises(AttributeError):
            ops.NO_SUCH_TEXT                                             # noqa: B018

    def test_tls_words(self):
        with use_lang("en"), self.assertRaises(Exception) as c:
            tls.ensure({}, {}, None, {})
        self.assertNotRegex(str(c.exception), CYR)


class ServerLanguageTest(unittest.TestCase):
    """The journal speaks the language of the server, not the one of the request; the drop-ins of the inbounds are
    neutral English whatever the language (T-67-332 №7: they are not rewritten on a change of the language)."""

    def test_gate_dropin(self):
        r = FakeRunner()
        with server("ru"), use_lang("ru"):
            ru = gate.text(r, "kgw-wg", "kaskad-gw-wg.service")
        with server("en"), use_lang("ru"):
            en = gate.text(r, "kgw-wg", "kaskad-gw-wg.service")
        self.assertEqual(ru, en)
        self.assertNotRegex(en, CYR)
        self.assertIn('echo "Kaskad: inbound kgw-wg start #$$n since boot, $$u s after it"', en)
        self.assertIn("# This file is written by kaskadctl.", en)
        self.assertEqual(en.count("'"), 4)                                  # sh -c '…' twice, not broken by the words
        self.assertNotIn("%", en)

    def test_journal(self):
        got = []
        with server("ru"), use_lang("en"), \
                mock.patch.object(util, "log_action", got.append):
            ops.forget_renumbered(FakeRunner(), [1], log=None)
        self.assertEqual(len(got), 1)
        self.assertIn("GW exits: строки пула 1 сменили выход", got[0])
        got = []
        with server("en"), use_lang("ru"):
            ops.forget_renumbered(FakeRunner(), [1], log=got.append)
        self.assertTrue(got[0].startswith("GW exits: pool lines 1 changed their exit"), got)


if __name__ == "__main__":
    unittest.main()
