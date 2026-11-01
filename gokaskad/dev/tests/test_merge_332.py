"""Сведение 3.3.2 (k3-332): обмен меню ↔ ядро — метки, а не слова языка (k3/handoff/I18N-3.3.2.md, ворота 2)."""
import argparse
import contextlib
import io
import os
import re
import unittest
from unittest import mock

from kaskad.i18n import use_lang
from kaskad.panel import userpage_cli

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CYR = re.compile("[А-Яа-яЁё]")


class PageCliMarksTest(unittest.TestCase):
    """kaskadctl page …: пометки страницы человека — «@K3:warn …» при K3_MARKS=1 (меню 17 → 2 зовёт с ним и
    показывает «! …»), без K3_MARKS — словом языка вызова."""

    def show(self, marks, lang):
        doc = {"path": "/p/x", "note": "N1", "qr_note": "N2"}
        out = io.StringIO()
        env = {"K3_MARKS": "1"} if marks else {}
        with mock.patch.dict(os.environ, env, clear=False), use_lang(lang), contextlib.redirect_stdout(out):
            if not marks:
                os.environ.pop("K3_MARKS", None)
            userpage_cli._show(argparse.Namespace(json=False), doc, "HEAD")
        return out.getvalue().splitlines()

    def test_marks_for_the_menu_words_for_a_person(self):
        self.assertEqual(self.show(True, "ru")[-2:], ["@K3:warn N1", "@K3:warn N2"])
        self.assertEqual(self.show(False, "en")[-2:], ["Note: N1", "Note: N2"])
        self.assertEqual(self.show(False, "ru")[-1], "Внимание: N2")


class MenuNoWordMatchTest(unittest.TestCase):
    """install.sh не разбирает слова-приставки ядра («Внимание:», «Ошибка:», строки aptlock) — только метки @K3."""

    def test_no_cyrillic_in_matching(self):
        with open(os.path.join(ROOT, "install.sh"), encoding="utf-8") as f:
            src = f.read()
        src = re.sub(r"# >>> K3_(I18N|PAYLOAD).*?# <<< K3_\1", "", src, flags=re.S)
        bad = []
        for n, line in enumerate(src.split("\n"), 1):
            if line.lstrip().startswith("#"):
                continue
            code = re.sub(r"\s#\s.*$", "", line)
            if not CYR.search(code):
                continue
            if re.search(r"\[\[[^]]*(==|!=|=~)[^]]*[А-Яа-яЁё]", code) \
                    or re.search(r"\b(sed|grep|awk)\b[^|]*'[^']*[А-Яа-яЁё]", code) \
                    or re.search(r'(\*"[^"]*[А-Яа-яЁё][^"]*"|"[^"]*[А-Яа-яЁё][^"]*"\*)', code):
                bad.append(line.strip()[:160])
        # журнал «PANEL: установлена из меню» — log_action после sed 's/^/  /' (не разбор): строка не совпадение
        bad = [b for b in bad if "log_action" not in b]
        self.assertEqual(bad, [])


class JournalLanguageTest(unittest.TestCase):
    """Журнал /var/log/kaskad.log — на языке сервера везде (решение координатора 2026-10-11): записи панели, мастера,
    страницы человека, MCP — через util.log_tr и каталог journal.json, не русские строки в коде."""

    def journal(self, saved, env, request, *calls):
        """Journal lines of ``calls`` with KASKAD_LANG=``saved`` in /etc/kaskad/config, ``env`` in the environment of
        the process (a job of the panel gets the language of its request there) and use_lang(``request``)."""
        import tempfile
        from kaskad import util
        got = []
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "etc", "kaskad"))
            if saved:
                with open(os.path.join(root, "etc", "kaskad", "config"), "w", encoding="utf-8") as f:
                    f.write('KASKAD_LANG="%s"\n' % saved)
            with mock.patch.object(util, "log_action", got.append), \
                    mock.patch.dict(os.environ, {"KASKAD_ROOT": root, "KASKAD_LANG": env}), use_lang(request):
                for key, kw in calls:
                    util.log_tr(key, **kw)
        return got

    def test_the_saved_language_not_the_request_nor_the_environment(self):
        login = ("journal.panel.login_password", {"ip": "198.51.100.9"})
        page = ("journal.page.on", {"name": "Mama", "id": "u-1"})
        # сервер en, задание панели запущено с KASKAD_LANG=ru (язык запроса) — журнал всё равно по-английски
        self.assertEqual(self.journal("en", "ru", "ru", login, page),
                         ["PANEL: login with the password (198.51.100.9)", "Personal page turned on: Mama (u-1)"])
        # сервер ru, запрос и окружение en — по-русски, байт в байт как в 3.3.1
        self.assertEqual(self.journal("ru", "en", "en", ("journal.page.on", {"name": "Мама", "id": "u-1"})),
                         ["Страница пользователя включена: Мама (u-1)"])
        # язык не сохранён — русский (как у установщика по умолчанию), не язык окружения
        self.assertEqual(self.journal(None, "en", "en", page), ["Страница пользователя включена: Mama (u-1)"])

    def test_no_russian_literal_in_journal_calls(self):
        bad = []
        for dp, _dn, fn in os.walk(os.path.join(ROOT, "lib", "kaskad")):
            if "catalog" in dp or "static" in dp:
                continue
            for n in fn:
                if not n.endswith(".py"):
                    continue
                path = os.path.join(dp, n)
                with open(path, encoding="utf-8") as f:
                    src = f.read()
                for m in re.finditer(r"log_action\(\s*f?([\"'])((?:(?!\1).)*)\1", src):
                    if CYR.search(m.group(2)):
                        bad.append("%s: %s" % (os.path.relpath(path, ROOT), m.group(2)[:80]))
        self.assertEqual(bad, [])


class NoOrphanKeysTest(unittest.TestCase):
    """Ворота 2: ключ каталога Python, который нигде не зовут, — сирота. Зовут ключ целиком («area.sub.key» в коде
    lib/kaskad, install.sh, веб) или по началу, к которому код дописывает хвост («bot.mon.gw_» + kind,
    _K + "copy." + where, "net.socks.reply.%d"): начало в кавычках с точкой внутри."""

    # REV-ns-a12 П-2: the words of the TOFU consent (gw_tofu.warn, warn_name) were orphans until T10 (round 2): the CLI
    # «gw exit-preview --tofu» says them now. A new orphan still fails.
    # П-1 REV-3315-fin: the words gw_addresses.state.* waited here until the screen «Адреса сервера» was built; the panel
    # says its own (adr.* of pages/addresses.js), the Python ones are gone — no known orphan now
    KNOWN = ()

    def test_every_python_catalog_key_is_called(self):
        self.assertEqual([k for k in self.orphans() if k not in self.KNOWN], [])

    def test_the_known_orphans_are_still_orphans(self):
        """A known orphan that got its caller leaves KNOWN (the list never hides a key)."""
        self.assertEqual(sorted(k for k in self.orphans() if k in self.KNOWN), sorted(self.KNOWN))

    def test_a_check_by_the_area_is_no_call(self):
        """REV-ns-a12 П-2: ``startswith("gw_host.")`` in hostmon.from_disk covered every key of gw_host."""
        got = self.orphans(["gw_host.totally.unused_key", "gw_host.find.cpu.warn"])
        self.assertEqual([k for k in got if k not in self.KNOWN], ["gw_host.totally.unused_key"])

    def orphans(self, extra=()):
        """The keys of the catalogs (and ``extra``) that nothing calls."""
        import glob
        import json
        keys = list(extra)
        for f in sorted(glob.glob(os.path.join(ROOT, "lib", "kaskad", "i18n", "catalog", "ru", "*.json"))):
            with open(f, encoding="utf-8") as fh:
                keys += list(json.load(fh))
        code = []
        for dp, _dn, fn in os.walk(os.path.join(ROOT, "lib", "kaskad")):
            if os.sep + "catalog" in dp or "__pycache__" in dp:
                continue
            for n in fn:
                if n.endswith((".py", ".js", ".html")):
                    with open(os.path.join(dp, n), encoding="utf-8") as fh:
                        code.append(fh.read())
        with open(os.path.join(ROOT, "install.sh"), encoding="utf-8") as fh:
            code.append(re.sub(r"# >>> K3_(I18N|PAYLOAD).*?# <<< K3_\1", "", fh.read(), flags=re.S))
        blob = "\n".join(code)
        # a check of a key is no call: ``key.startswith("gw_host.")`` would cover every key of the area (REV-ns-a12 П-2)
        blob = re.sub(r"""(startswith|endswith)\(\s*([\"'])[^\"']*\2""", r"\1(", blob)
        words = set(re.findall(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", blob))           # «rules.common.yes» — одно слово
        quoted = set(re.findall(r"[\"']([A-Za-z0-9_][A-Za-z0-9_.-]*)", blob))     # начало строки в кавычках

        def used(k):
            if k in words:
                return True
            for j in [0] + [i + 1 for i, c in enumerate(k) if c == "."]:
                rest = k[j:]
                if j and "." in rest and rest in quoted:                       # _K + "copy.agent"
                    return True
                for i, c in enumerate(rest):
                    if c in "._" and i > 0 and "." in rest[:i + 1] and rest[:i + 1] in quoted:
                        return True                                            # "bot.mon.gw_" + kind; not "gw_"
            return False

        return [k for k in keys if not used(k)]


class McpKeysLanguageTest(unittest.TestCase):
    """Ключи ИИ-агента (kaskadctl mcp key, панель «Ключи ИИ-агентов», мастер этап 5): тексты и ошибки — на языке
    вызова через каталог mcp.json; русский — прежний."""

    def test_texts_and_errors_follow_the_language(self):
        import tempfile
        from kaskad.errors import KaskadError
        from kaskad.mcp import keys
        pub = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl agent"
        with tempfile.TemporaryDirectory() as root, mock.patch.dict(os.environ, {"KASKAD_ROOT": root}):
            os.makedirs(os.path.join(root, "etc", "kaskad"))
            with use_lang("en"):
                with self.assertRaises(KaskadError) as cm:
                    keys.parse_public("-----BEGIN OPENSSH PRIVATE KEY-----")
                self.assertIsNone(CYR.search(str(cm.exception)), str(cm.exception))
                res = keys.add(pub, label="laptop", host="203.0.113.10")
                self.assertEqual(res["text"], "Agent key added: it can run only kaskad-mcp.")
                gone = keys.delete(res["fingerprint"])
                self.assertIsNone(CYR.search(gone["text"]))
                self.assertEqual(keys.host_guess(None), "<server-IP>")
            with use_lang("ru"):
                with self.assertRaises(KaskadError) as cm:
                    keys.parse_public("")
                self.assertEqual(str(cm.exception),
                                 "Вставьте публичный ключ одной строкой: ssh-ed25519 AAAA… (файл ~/.ssh/<имя>.pub).")


class TypographicApostropheTest(unittest.TestCase):
    """EN-тексты для человека — с типографским ’ (can’t, server’s): каталоги Python catalog/en/*.json и bash
    sh/*.en.sh. Апостроф ASCII между буквами — только в коде внутри текста (команда оболочки), в словах — нет."""

    def test_no_ascii_apostrophe_in_english_words(self):
        import glob
        import json
        bad = []
        for f in sorted(glob.glob(os.path.join(ROOT, "lib", "kaskad", "i18n", "catalog", "en", "*.json"))):
            with open(f, encoding="utf-8") as fh:
                for k, v in json.load(fh).items():
                    if re.search(r"[A-Za-z]'[A-Za-z]|[A-Za-z]s' ", v):
                        bad.append("%s: %s" % (k, v[:80]))
        for f in sorted(glob.glob(os.path.join(ROOT, "lib", "kaskad", "i18n", "sh", "*.en.sh"))):
            with open(f, encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("M_en_") and re.search(r"[A-Za-z]{2}'\\''(t|s|re|ve|ll|d|m)\b", line):
                        bad.append(line[:100])
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
