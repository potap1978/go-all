"""kaskad.i18n (3.3.2): tr/trn, get_lang order, use_lang in threads and asyncio, catalog checks, the payload."""

import asyncio
import json
import os
import shutil
import tempfile
import threading
import unittest
from unittest import mock

from kaskad import i18n
from kaskad.i18n import check
from tests.helpers import RootCase, zone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _write_catalog(d, lang, area, doc):
    os.makedirs(os.path.join(d, lang), exist_ok=True)
    with open(os.path.join(d, lang, area + ".json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)


class CatalogCase(RootCase):
    """A temporary catalog dir with area «t» and KASKAD_LANG removed from the environment."""

    RU = {"t.kl": "{key} на {lang}", "t.hello": "Привет, {name}", "t.only_ru": "Только по-русски", "t.braces": "{{x}} = {x}",
          "t.exits": "{n} выход|{n} выхода|{n} выходов", "t.peers": "один из {total}|{n} из {total}|{n} из {total}"}
    EN = {"t.kl": "{key} in {lang}", "t.hello": "Hello, {name}", "t.braces": "{{x}} = {x}",
          "t.exits": "{n} exit|{n} exits", "t.peers": "one of {total}|{n} of {total}"}

    def setUp(self):
        super().setUp()
        self.cat = tempfile.mkdtemp(prefix="kaskad-i18n-")
        _write_catalog(self.cat, "ru", "t", self.RU)
        _write_catalog(self.cat, "en", "t", self.EN)
        self._p = mock.patch.object(i18n, "CATALOG_DIR", self.cat)
        self._p.start()
        self._env = mock.patch.dict(os.environ)
        self._env.start()
        os.environ.pop("KASKAD_LANG", None)
        i18n.reload()

    def tearDown(self):
        self._env.stop()
        self._p.stop()
        i18n.reload()
        shutil.rmtree(self.cat, ignore_errors=True)
        super().tearDown()


def lax():
    """The production way of tr(): an unfilled «{name}» stays as is, silently (tests run with K3_TR_STRICT=1)."""
    return mock.patch.dict(os.environ, {i18n.STRICT_ENV: "0"})


def strict(log):
    """The strict mode with a journal of its own: the run's $K3_TR_STRICT_LOG gets nothing from the deliberate misses."""
    return mock.patch.dict(os.environ, {i18n.STRICT_ENV: "1", i18n.STRICT_LOG_ENV: log})


class TrTest(CatalogCase):
    def test_strict_unfilled(self):
        """П3-а: K3_TR_STRICT=1 — an unfilled «{name}» raises (tr and trn); filled, escaped «{{x}}», a plain text — no;
        production — the text as is and one journal line per key."""
        log = os.path.join(self.cat, "strict.log")
        with strict(log):
            for call in (lambda: i18n.tr("t.hello", "en"), lambda: i18n.tr("t.hello", "en", other=1),
                         lambda: i18n.trn("t.peers", 2, "en"), lambda: i18n.trs("t.hello", "ru")):
                with self.assertRaises(i18n.Unfilled):
                    call()
            self.assertEqual(i18n.tr("t.hello", "en", name="A"), "Hello, A")
            self.assertEqual(i18n.trn("t.peers", 2, "en", total=5), "2 of 5")
            self.assertEqual(i18n.tr("t.braces", "en", x=1), "{x} = 1")
            self.assertEqual(i18n.tr("t.only_ru", "en"), "Только по-русски")
            self.assertEqual(i18n.tr("t.nope", "en"), "t.nope")
            try:                                     # REV-ns-p3a З-1: swallowed by «except Exception» — still in the file
                i18n.tr("t.hello", "en", other="secret-value")
            except Exception:
                pass
        with open(log, encoding="utf-8") as f:
            lines = f.read().splitlines()
        self.assertEqual(lines, ["t.hello: {name}", "t.hello: {name}", "t.peers: {total}", "t.hello: {name}",
                                 "t.hello: {name}"])
        with mock.patch.dict(os.environ, {i18n.STRICT_ENV: "1"}):
            os.environ.pop(i18n.STRICT_LOG_ENV, None)                             # no file named — only the raise
            self.assertRaises(i18n.Unfilled, i18n.tr, "t.hello", "en")
        self.assertEqual(i18n.strict_env().get(i18n.STRICT_ENV), os.environ.get(i18n.STRICT_ENV))
        i18n._unfilled_said.discard("t.hello")
        with lax(), mock.patch("syslog.syslog") as log:
            self.assertEqual(i18n.tr("t.hello", "en"), "Hello, {name}")
            self.assertEqual(i18n.tr("t.hello", "ru"), "Привет, {name}")
        self.assertEqual(log.call_count, 1)                                    # one line per key, not per call
        self.assertIn("t.hello", log.call_args[0][1])

    def test_tr_and_fallbacks(self):
        self.assertEqual(i18n.tr("t.hello", "ru", name="Мир"), "Привет, Мир")
        self.assertEqual(i18n.tr("t.hello", "en", name="World"), "Hello, World")
        self.assertEqual(i18n.tr("t.only_ru", "en"), "Только по-русски")          # en -> ru
        self.assertEqual(i18n.tr("t.nope", "en"), "t.nope")                       # -> the key
        with lax():                                                              # production: silent, as is
            self.assertEqual(i18n.tr("t.hello", "en"), "Hello, {name}")          # no kw: as is
            self.assertEqual(i18n.tr("t.braces", "en"), "{{x}} = {x}")            # no kw: no format at all
            self.assertEqual(i18n.tr("t.hello", "en", other=1), "Hello, {name}")  # a missing value stays visible
        self.assertEqual(i18n.tr("t.braces", "en", x=1), "{x} = 1")
        self.assertEqual(i18n.tr("t.hello", "en-GB", name="A"), "Hello, A")
        self.assertEqual(i18n.tr("t.hello", "de", name="A"), "Hello, A")         # not Russian -> English

    def test_placeholders_key_and_lang(self):
        self.assertEqual(i18n.tr("t.kl", "en", key="K", lang="L"), "K in L")
        self.assertEqual(i18n.tr("t.kl", key="K", lang="L"), "K на L")              # {lang} is a placeholder here
        self.assertEqual(i18n.tr("t.hello", lang="en", name="A"), "Hello, A")      # no {lang}: the language
        self.assertEqual(i18n.trn("t.exits", 2, lang="en"), "2 exits")
        with self.assertRaises(TypeError):
            i18n.tr(_key="t.hello")                                                 # positional-only

    def test_trs_retr(self):
        import copy
        import pickle
        said = i18n.trs("t.hello", "ru", name="Мир")
        self.assertEqual((said, json.dumps([said], ensure_ascii=False)), ("Привет, Мир", '["Привет, Мир"]'))
        self.assertEqual(i18n.retr(said, "en"), "Hello, Мир")
        self.assertEqual(i18n.retr([said, "plain"], "en"), ["Hello, Мир", "plain"])
        with lax():
            nested = i18n.trs("t.hello", "ru", name=i18n.trs("t.exits", "ru"))
            self.assertTrue(i18n.retr(nested, "en").startswith("Hello, {n} exit"))
        for c in (copy.deepcopy(said), pickle.loads(pickle.dumps(said))):
            self.assertEqual((c, i18n.retr(c, "en")), ("Привет, Мир", "Hello, Мир"))
        with i18n.use_lang("en"):
            self.assertEqual(i18n.retr(said), "Hello, Мир")

    def test_trn(self):
        ns = (1, 2, 5, 11, 21, 22, 25, 111, 12, 14, 0)
        self.assertEqual([i18n.trn("t.exits", n, "ru") for n in ns],
                         ["1 выход", "2 выхода", "5 выходов", "11 выходов", "21 выход", "22 выхода", "25 выходов",
                          "111 выходов", "12 выходов", "14 выходов", "0 выходов"])
        self.assertEqual([i18n.trn("t.exits", n, "en") for n in (0, 1, 2)], ["0 exits", "1 exit", "2 exits"])
        self.assertEqual(i18n.trn("t.peers", 1, "ru", total=3), "один из 3")
        self.assertEqual(i18n.trn("t.peers", 2, "en", total=3), "2 of 3")
        self.assertEqual(i18n.trn("t.nope", 2, "en"), "t.nope")
        with i18n.use_lang("en"):
            self.assertEqual(i18n.trn("t.exits", 1), "1 exit")

    def test_get_lang_order(self):
        self.assertEqual(i18n.get_lang(), "ru")                                   # nothing set
        self.write("/etc/kaskad/config", 'BOT_TOKEN="x"\nKASKAD_LANG="en"\n')
        self.assertEqual(i18n.get_lang(), "en")                                   # config
        os.environ["KASKAD_LANG"] = "ru"
        self.assertEqual(i18n.get_lang(), "ru")                                   # env over config
        with i18n.use_lang("en"):
            self.assertEqual(i18n.get_lang(), "en")                               # context over env
            self.assertEqual(i18n.get_lang("ru-RU"), "ru")                        # explicit over all
            self.assertEqual(i18n.tr("t.hello", name="X"), "Hello, X")
        self.assertEqual(i18n.get_lang(), "ru")
        os.environ["KASKAD_LANG"] = "xx"                                          # junk: next source
        self.assertEqual(i18n.get_lang(), "en")
        os.environ["KASKAD_LANG"] = "ru"
        with i18n.use_lang("en"):                                                 # the server: env -> config
            self.assertEqual((i18n.server_lang(), i18n.saved_lang()), ("ru", "en"))
        os.environ.pop("KASKAD_LANG")
        self.assertEqual(i18n.server_lang(), "en")

    def test_config_change_is_seen(self):
        self.write("/etc/kaskad/config", "KASKAD_LANG=en\n")
        self.assertEqual(i18n.get_lang(), "en")
        p = self.path("/etc/kaskad/config")
        self.write("/etc/kaskad/config", "KASKAD_LANG='ru'   \n# KASKAD_LANG=en\n")
        os.utime(p, ns=(1, 1))
        self.assertEqual(i18n.get_lang(), "ru")

    def test_use_lang_threads(self):
        seen = {}
        with i18n.use_lang("en"):
            plain = threading.Thread(target=lambda: seen.setdefault("plain", i18n.get_lang()))
            bound = threading.Thread(target=i18n.bind_lang(lambda: seen.setdefault("bound", i18n.get_lang())))
            plain.start(); bound.start(); plain.join(); bound.join()

        def other():
            with i18n.use_lang("en"):
                seen["other"] = i18n.get_lang()
        t = threading.Thread(target=other)
        t.start(); t.join()
        self.assertEqual(seen, {"plain": "ru", "bound": "en", "other": "en"})
        self.assertEqual(i18n.get_lang(), "ru")                                   # no leak into this thread

    def test_use_lang_asyncio(self):
        async def one(lang):
            with i18n.use_lang(lang):
                await asyncio.sleep(0.01)
                return i18n.get_lang()

        async def both():
            return await asyncio.gather(one("en"), one("ru"), one("en"))
        self.assertEqual(asyncio.run(both()), ["en", "ru", "en"])

    def test_child_env(self):
        with i18n.use_lang("en"):
            self.assertEqual(i18n.lang_env({"A": "1"}), {"A": "1", "KASKAD_LANG": "en"})
            self.assertEqual(i18n.setenv_args(), ["--setenv=KASKAD_LANG=en"])
        self.assertEqual(i18n.setenv_args(), ["--setenv=KASKAD_LANG=ru"])

    def test_broken_catalog_file_is_skipped(self):
        with open(os.path.join(self.cat, "en", "z.json"), "w") as f:
            f.write("{broken")
        i18n.reload()
        self.assertEqual(i18n.tr("t.hello", "en", name="A"), "Hello, A")
        self.assertTrue(any("z/" in p or "z.json" in p for p in check.catalog_problems(catalog_dir=self.cat)))


class CheckTest(CatalogCase):
    def test_complete_catalog_passes(self):
        RU = dict(self.RU)
        RU.pop("t.only_ru")
        _write_catalog(self.cat, "ru", "t", RU)
        self.assertEqual(check.catalog_problems(catalog_dir=self.cat), [])

    def test_problems_found(self):
        _write_catalog(self.cat, "ru", "u", {"u.a": "А {x}", "u.b": "Б", "t.dup": "в чужой области",
                                             "u.c": "{n} шт|{n} шт|{n} шт", "u.d": "{n} шт|{n} шт|{n} шт",
                                             "u.e": "ru|en"})
        _write_catalog(self.cat, "en", "u", {"u.a": "A {y}", "u.extra": "Extra", "u.b": "Б",
                                             "u.c": "{n} pc", "u.d": "one|{n} pcs", "u.e": "ru|en"})
        probs = "\n".join(check.catalog_problems(catalog_dir=self.cat))
        for want in ("t.only_ru — нет в en", "u.a — подстановки", "u.extra — нет в ru", "t.dup — ключ не",
                     "u.b — кириллица", "u.c — формы числа"):
            self.assertIn(want, probs)
        self.assertNotIn("u.d", probs)
        self.assertNotIn("u.e —", probs)                                              # the same literal «|»

    def test_cyrillic_strings(self):
        d = tempfile.mkdtemp(prefix="kaskad-cyr-")
        self.addCleanup(shutil.rmtree, d, True)
        src = (
            '"""Модуль: докстринг не считается."""\n'
            '# комментарий не считается\n'
            'from kaskad.i18n import tr\n'
            'def f(x):\n'
            '    """Докстринг функции."""\n'
            '    a = tr("gw.key", name="ok")\n'
            '    b = "Видно человеку"\n'
            '    c = f"Тоже видно {x}"\n'
            '    d = "работает"  # i18n: skip — шаблон вывода русскоязычной программы\n'
            '    e = ("многострочная "\n'
            '         "строка")\n'
            '    return a, b, c, d, e\n')
        with open(os.path.join(d, "m.py"), "w", encoding="utf-8") as f:
            f.write(src)
        found = check.cyrillic_strings(d)
        self.assertEqual([x.split(":")[1] for x in found], ["7", "8", "10"], found)


class RealCatalogsTest(unittest.TestCase):
    """Every area of the shipped catalogs: complete ru == en, same placeholders, no Russian in English."""

    def test_shipped_catalogs_complete(self):
        check.assert_catalog_complete(self)

    def test_a_shipped_area_answers(self):
        i18n.reload()
        self.assertEqual(i18n.tr("journal.setup.finished", "en"), "SETUP: quick setup finished")
        self.assertEqual(i18n.tr("journal.setup.finished", "ru"), "SETUP: быстрая настройка завершена")

    def test_payload_ships_catalogs(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("build_payload", os.path.join(ROOT, "tools", "build_payload.py"))
        bp = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bp)
        names = [n for n, _ in bp.collect(os.path.join(ROOT, "lib"))]
        for lang in i18n.LANGS:
            self.assertIn("kaskad/i18n/catalog/%s/journal.json" % lang, names)
        self.assertIn("kaskad/i18n/__init__.py", names)


class JobLangTest(RootCase):
    """A step of the wizard started from an English request runs in English (KASKAD_LANG of the unit/process)."""

    def test_systemd_run_gets_lang(self):
        from kaskad.setup import jobs
        calls = []

        class R(object):
            def which(self, n):
                return "/usr/bin/" + n

            def run(self, argv, timeout=None):
                calls.append(argv)
                return mock.Mock(ok=True, rc=0, out="", err="")
        with mock.patch.dict(os.environ, {"INVOCATION_ID": "x"}), i18n.use_lang("en"):
            jobs._launch("abc", "domain", R())
        self.assertIn("--setenv=KASKAD_LANG=en", calls[0])
        self.assertLess(calls[0].index("--setenv=KASKAD_LANG=en"), calls[0].index("setup"))

    def test_detached_process_gets_lang(self):
        from kaskad.setup import jobs
        with mock.patch.dict(os.environ), mock.patch.object(jobs.subprocess, "Popen") as po, i18n.use_lang("en"):
            os.environ.pop("INVOCATION_ID", None)
            jobs._launch("abc", "domain", None)
        self.assertEqual(po.call_args[1]["env"]["KASKAD_LANG"], "en")


class SetLangTest(CatalogCase):
    """kaskad.i18n.set_lang / kaskadctl lang: one language of the server in /etc/kaskad/config."""

    def setUp(self):
        super().setUp()
        self._real = mock.patch.object(i18n, "CATALOG_DIR", i18n.CATALOG_DIR)   # lang.json of the shipped catalogs
        self._p.stop()
        i18n.reload()

    def tearDown(self):
        self._p.start()
        super().tearDown()

    def test_set_lang_keeps_other_lines(self):
        self.write("/etc/kaskad/config", 'BOT_TOKEN="1:x"\nKASKAD_LANG="ru"\n# KASKAD_LANG=xx — note\nexport KASKAD_LANG=ru\n')
        self.assertEqual(i18n.set_lang(" EN "), "en")
        self.assertEqual(self.read("/etc/kaskad/config"),
                         'BOT_TOKEN="1:x"\n# KASKAD_LANG=xx — note\nKASKAD_LANG="en"\n')
        self.assertEqual(oct(os.stat(self.path("/etc/kaskad/config")).st_mode & 0o777), "0o600")
        self.assertEqual((i18n.server_lang(), i18n.saved_lang(), i18n.get_lang()), ("en", "en", "en"))
        self.assertEqual(i18n.set_lang("ru"), "ru")
        self.assertEqual(i18n.get_lang(), "ru")

    def test_set_lang_no_config_yet(self):
        self.assertEqual(i18n.set_lang("en"), "en")
        self.assertEqual(self.read("/etc/kaskad/config"), 'KASKAD_LANG="en"\n')

    def test_set_lang_rejects(self):
        for bad in ("de", "english", "", None, "ru-RU"):
            with self.assertRaises(ValueError):
                i18n.set_lang(bad)
        with i18n.use_lang("en"):
            with self.assertRaises(ValueError) as cm:
                i18n.set_lang("de")
        self.assertIn("ru or en", str(cm.exception))
        self.assertFalse(os.path.exists(self.path("/etc/kaskad/config")))

    def test_set_lang_updates_own_env(self):
        os.environ["KASKAD_LANG"] = "ru"
        i18n.set_lang("en")
        self.assertEqual(os.environ["KASKAD_LANG"], "en")

    def _ctl(self, *argv):
        import contextlib
        import io
        from kaskad import cli
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def test_kaskadctl_lang(self):
        rc, out, _ = self._ctl("lang", "get", "--json")
        self.assertEqual((rc, json.loads(out)["lang"]), (0, "ru"))
        rc, out, _ = self._ctl("lang", "set", "en")
        self.assertEqual(rc, 0)
        self.assertIn("Server language: English (en)", out)
        rc, out, _ = self._ctl("lang", "set", "en", "--json")
        self.assertEqual(json.loads(out), {"ok": True, "lang": "en", "changed": False})
        rc, out, _ = self._ctl("lang", "get")
        self.assertIn("Server language: English", out)
        rc, out, err = self._ctl("lang", "set", "de")
        self.assertEqual(rc, 2)
        self.assertIn("de", err)
        rc, out, _ = self._ctl("lang", "set", "ru")
        self.assertIn("Язык сервера: русский (ru)", out)


class FmtTest(unittest.TestCase):
    """fmt_*: one way to write numbers, sizes and dates in bot, CLI, menu and panel."""

    def test_num(self):
        self.assertEqual((i18n.fmt_num(33467, "ru"), i18n.fmt_num(33467, "en")), ("33\u202f467", "33,467"))
        self.assertEqual((i18n.fmt_num(1234.5, "ru", digits=1), i18n.fmt_num(1234.5, "en", digits=1)),
                         ("1\u202f234,5", "1,234.5"))
        self.assertEqual((i18n.fmt_num(7, "ru"), i18n.fmt_num(-1000000, "en")), ("7", "-1,000,000"))
        self.assertEqual((i18n.fmt_num(None), i18n.fmt_num("x")), ("", "x"))
        with i18n.use_lang("en"):
            self.assertEqual(i18n.fmt_num(1000), "1,000")

    def test_bytes(self):
        self.assertEqual([i18n.fmt_bytes(n, "ru") for n in (0, 512, 5017, 1536, 3 * 1024 ** 3)],
                         ["0 Б", "512 Б", "4,9 КБ", "1,5 КБ", "3,0 ГБ"])
        self.assertEqual([i18n.fmt_bytes(n, "en") for n in (512, 5017, 2 * 1024 ** 4, None)],
                         ["512 B", "4.9 KB", "2.0 TB", "0 B"])
        with i18n.use_lang("en"):
            self.assertEqual(i18n.fmt_bytes(1536), "1.5 KB")

    def test_dates(self):
        import datetime
        with zone("UTC"):
            for x in ("2026-12-30", "2026-12-30T10:00:00Z", "20261230T100000Z", datetime.date(2026, 12, 30),
                      "2026-12-30T10:00:00.123456+00:00", "2026-12-30 10:00:00"):
                self.assertEqual((i18n.fmt_date(x, "ru"), i18n.fmt_date(x, "en")), ("30.12.2026", "Dec 30, 2026"), x)
            now = "2027-03-01T12:00:00Z"
            self.assertEqual(i18n.fmt_datetime("2026-01-05T07:08:09Z", "ru", now=now), "05.01.2026, 07:08")
            self.assertEqual(i18n.fmt_datetime("2026-01-05T07:08:09Z", "en", seconds=True, now=now),
                             "Jan 5, 2026, 07:08:09")
            self.assertEqual(i18n.fmt_datetime(0, "en"), "Jan 1, 1970, 00:00")
            self.assertEqual(i18n.fmt_datetime("2026-01-05", "ru"), "05.01.2026")
        with zone("Europe/Moscow"):
            self.assertEqual(i18n.fmt_datetime("2026-12-30T22:00:00Z", "ru", now=0), "31.12.2026, 01:00")   # server clock
        self.assertEqual((i18n.fmt_date(None), i18n.fmt_date("soon"), i18n.fmt_datetime("")), ("", "soon", ""))

    def test_dates_one_rule(self):
        """Finding №2 of the live check (3.3.2): one format per language everywhere — en «Jan 9, 2027» (never the
        ambiguous 01/09/2027), ru «09.01.2027»; a date and time drops the year within the current year."""
        import datetime
        with zone("UTC"):
            now = datetime.datetime(2027, 10, 11, 12, 0)
            jan9 = "2027-01-09T02:40:00Z"
            self.assertEqual((i18n.fmt_date(jan9, "en"), i18n.fmt_date(jan9, "ru")), ("Jan 9, 2027", "09.01.2027"))
            self.assertEqual((i18n.fmt_date(jan9, "en", short=True), i18n.fmt_date(jan9, "ru", short=True)),
                             ("Jan 9", "09.01"))
            self.assertEqual(i18n.fmt_datetime("2027-10-12T02:40:00Z", "en", now=now), "Oct 12, 02:40")
            self.assertEqual(i18n.fmt_datetime("2027-10-12T02:40:00Z", "ru", now=now), "12.10, 02:40")
            self.assertEqual(i18n.fmt_datetime("2028-01-09T02:40:00Z", "en", now=now), "Jan 9, 2028, 02:40")
            self.assertEqual(i18n.fmt_datetime("2028-01-09T02:40:00Z", "ru", now=now), "09.01.2028, 02:40")
            self.assertEqual(i18n.fmt_datetime("2027-10-12T02:40:00Z", "en", now=now, year=True), "Oct 12, 2027, 02:40")
            with i18n.use_lang("en"):                  # the language of the context (a panel request, a bot update)
                self.assertEqual(i18n.fmt_date(jan9), "Jan 9, 2027")
            with i18n.use_lang("ru"):
                self.assertEqual(i18n.fmt_datetime(jan9, now=now), "09.01, 02:40")
        with zone("Europe/Moscow"):
            t = "2027-01-09T22:30:00Z"
            self.assertEqual(i18n.fmt_datetime(t, "ru", year=True), "10.01.2027, 01:30")     # the server's clock
            self.assertEqual(i18n.fmt_datetime(t, "ru", year=True, utc=True), "09.01.2027, 22:30")
            self.assertEqual(i18n.fmt_date(t, "en", utc=True), "Jan 9, 2027")


class PayloadGateTest(unittest.TestCase):
    """tools/build_payload.py refuses incomplete catalogs (build and --check)."""

    def test_incomplete_catalog_refused(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("build_payload", os.path.join(ROOT, "tools", "build_payload.py"))
        bp = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bp)
        self.assertEqual(bp.i18n_problems(os.path.join(ROOT, "lib")), [])
        lib = tempfile.mkdtemp(prefix="kaskad-lib-")
        self.addCleanup(shutil.rmtree, lib, True)
        cat = os.path.join(lib, "kaskad", "i18n", "catalog")
        _write_catalog(cat, "ru", "x", {"x.a": "А", "x.b": "Б {n}"})
        _write_catalog(cat, "en", "x", {"x.a": "A"})
        self.assertEqual(bp.i18n_problems(lib), ["x: x.b — нет в en"])
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(bp.check(os.path.join(ROOT, "install.sh"), lib), 1)
        self.assertIn("x.b — нет в en", out.getvalue())


if __name__ == "__main__":
    unittest.main()
