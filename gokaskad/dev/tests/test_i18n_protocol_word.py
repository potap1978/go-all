"""3.3.2, повторная критика T-332 (№11): в текстах для человека — «Protocol / Протокол», а не «Inbound / Вход».

Решение координатора («Решения после повторной критики», k3/handoff/I18N-3.3.2.md; глоссарий docs/k3/GLOSSARY-en.md):
мастер, панель («Схема» тоже), страница человека, бот, SSH-экраны — protocol; inbound / вход остаётся только в консоли
(kaskadctl gw … — справка и подсказки команд), журнале, проверке полей API и для ИИ-агента. Здесь — все три вида
каталогов (веб-словари, Python, bash) и русская пара тех же ключей; консольные области — явным списком с причиной."""

import glob
import json
import os
import re
import unittest

from tests.test_i18n_terms_sh import catalog as sh_catalog
from tests.test_i18n_web import dictionary

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAT = os.path.join(ROOT, "lib", "kaskad", "i18n", "catalog")

# слово inbound, не имя подстановки {inbound} и не часть команды/поля (inbound_option, inbounds.l2tp, gw inbound)
EN_WORD = re.compile(r"(?<![\w.{])[Ii]nbounds?(?![\w}]|\.\w)")
CMD = re.compile(r"gw inbound|inbound_option|inbounds\.\w")
RU_WORD = re.compile(r"(?<![А-Яа-яЁё\w])[Вв]ход(а|у|ом|е|ы|ов|ам|ами|ах)?(?![А-Яа-яЁё\w])")


def _py(lang):
    out = {}
    for f in sorted(glob.glob(os.path.join(CAT, lang, "*.json"))):
        with open(f, encoding="utf-8") as fh:
            out.update(json.load(fh))
    return out


PY_EN, PY_RU = _py("en"), _py("ru")

# Консоль, журнал, API, агент — inbound законен (по области ключа)
CONSOLE_PREFIX = {
    "gw_cli.": "kaskadctl gw — справка и вывод консоли, рядом команды gw inbound …",
    "panel.api.": "проверка полей API панели (поле access — список inbound)",
    "gw_gate.": "строка журнала/комментарий nftables",
    "gw_ifwatch.": "заголовок журнала сторожа интерфейса",
    "gw_hooks.": "ошибки хука проверки входа (журнал accel-ppp/ocserv)",
    "gw_addr.": "внутренняя ошибка распределения адресов",
    "gw_model.": "проверка аргумента (kind) — консоль и API",
    "gw_in.dummy.": "тестовый адаптер",
    "gw_in.restart_why.": "причина перезапуска для журнала",
    "mcp.": "описания для ИИ-агента",
    "gw_mtu.st.": "строки kaskadctl gw status (консоль; SSH 17 → 5 показывает вывод консоли)",
    "gw_awgkmod.ln.": "строки kaskadctl gw accel status (консоль)",
}
CONSOLE_KEYS = {
    "panel.scheme.traceInbound": "проверка поля inbound в API",
    "panel.scheme.unknownInbound": "проверка поля в API",
    "gw_ops.err.unknown_kind": "ошибка аргумента (USAGE)",
    "gw_ops.err.unknown_kind_of": "ошибка аргумента (USAGE)",
    "gw_ops.err.no_option": "имена параметров inbound_option (агент, kaskadctl)",
    "gw_ops.err.pki_kinds": "аргумент kaskadctl gw pki",
    "gw_ops.err.not_granted_hint": "подсказка с командой kaskadctl",
    "gw_ops.err.not_installed_hint": "подсказка с командой kaskadctl",
    "gw_ops.warn.gates": "ExecStartPre служб — для администратора системы",
    "gw_in.err.not_installed": "подсказка с командой kaskadctl gw inbound install",
    "gw_in_accel.why.build_now": "подсказка с командой kaskadctl gw inbound install",
    "gw_in_awg.err.line": "проверка аргумента линии (консоль, API)",
    "gw_in_l2tp.off_both": "путь в настройках inbounds.l2tp",
    "site_cli.domain.cleared": "kaskadctl site — консоль",
}
SH_CONSOLE = {"GWA_LOG_S4": "журнал", "GWA_LOG_MTU": "журнал"}


def _console(key):
    return key.startswith(tuple(CONSOLE_PREFIX)) or ".log." in key or key in CONSOLE_KEYS


def _word(text):
    return EN_WORD.search(CMD.sub("", text))


class ProtocolNotInboundTest(unittest.TestCase):
    def test_web(self):
        bad = []
        for name in ("i18n-en.js", "graph-i18n-en.js", "setup/setup-i18n-en.js", "user-i18n-en.js"):
            bad += ["%s:%s %s" % (name, k, v) for k, v in dictionary(name).items() if _word(v)]
        self.assertEqual(bad, [])

    def test_python_catalogs(self):
        self.assertEqual(sorted("%s: %s" % (k, v) for k, v in PY_EN.items() if _word(v) and not _console(k)), [])

    def test_ssh_and_installer(self):
        en = sh_catalog("en")
        self.assertEqual(sorted("%s: %s" % (k, v) for k, v in en.items() if _word(v) and k not in SH_CONSOLE), [])
        self.assertEqual(en["W_INB"], "protocol|protocols")
        self.assertEqual(sh_catalog("ru")["W_INB"], "протокол|протокола|протоколов")

    def test_console_keeps_inbound(self):
        # консоль и журнал не переименованы: команды и поля остаются как есть
        for k in ("gw_ops.err.not_installed_hint", "gw_in.err.not_installed", "gw_ops.log.inbound_install"):
            self.assertRegex(PY_EN[k], r"\binbound\b", k)
        self.assertIn("kaskadctl gw inbound install vless", PY_EN["gw_ops.err.vless_not_installed"])

    def test_wizard_and_panel_names(self):
        s_en, s_ru = dictionary("setup/setup-i18n-en.js"), dictionary("setup/setup-i18n-ru.js")
        self.assertEqual(s_en["d.input"], "Protocol for this device")
        self.assertEqual(s_ru["d.input"], "Протокол для этого устройства")
        self.assertEqual(s_en["mode.nodeD"], "Protocols for devices and exits abroad.")
        self.assertEqual(dictionary("i18n-en.js")["mtu.doneT"], "Protocol MTU changed")
        self.assertEqual(dictionary("graph-i18n-en.js")["graph.inbound"], "Protocol")
        self.assertEqual(sh_catalog("en")["GWD_ACC_HEAD"], "Device protocols")
        self.assertEqual(PY_EN["bot.gw.inbounds_head"], "<b>Protocols</b>")
        self.assertEqual(PY_RU["bot.gw.inbounds_head"], "<b>Протоколы</b>")

    def test_russian_pair_says_protocol_too(self):
        # где английский говорит «protocol» вместо inbound, русская пара не говорит «вход» (вход в панель — другое
        # слово и в эти ключи не попадает)
        bad = []
        for k, v in PY_EN.items():
            if re.search(r"\b[Pp]rotocols?\b", v) and not _console(k) and RU_WORD.search(PY_RU.get(k, "")):
                bad.append("%s: %s" % (k, PY_RU[k]))
        for name in ("graph-i18n-%s.js", "i18n-%s.js", "setup/setup-i18n-%s.js"):
            en, ru = dictionary(name % "en"), dictionary(name % "ru")
            for k, v in en.items():
                if re.search(r"\b[Pp]rotocols?\b", v) and RU_WORD.search(ru.get(k, "")):
                    bad.append("%s:%s %s" % (name % "ru", k, ru[k]))
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
