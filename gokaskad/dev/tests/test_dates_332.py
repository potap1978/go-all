"""3.3.2, finding №2 of the live check: dates in one format per language everywhere.

Was: the panel «01/09/2027» (en-US MM/DD — ambiguous), SSH «09.01.2027» in English, the wizard's link «Valid until
12.10 02:40». Now one rule in the three places that print a date for a person:
    core     kaskad.i18n.fmt_date / fmt_datetime (on the language of the context: use_lang / KASKAD_LANG)
    SSH      k3_date in install.sh (K3_LANG)
    web      kaskadDates in boot.js -> window.KaskadLang.date / dateTime / time (panel, «Схема», wizard)
    en «Jan 9, 2027», ru «09.01.2027»; a date and time drops the year within the current year: en «Oct 12, 02:40»,
    ru «12.10, 02:40», another year — «Jan 9, 2027, 02:40» / «09.01.2027, 02:40». 24 h.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from kaskad import i18n
from tests import jsparts

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "lib", "kaskad", "panel", "static")
INSTALL = os.path.join(ROOT, "install.sh")


def read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


class CoreTest(unittest.TestCase):
    def setUp(self):
        self.tz = os.environ.get("TZ")
        os.environ["TZ"] = "UTC"
        import time
        time.tzset()

    def tearDown(self):
        import time
        if self.tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self.tz
        time.tzset()

    def test_the_formats_of_the_finding(self):
        now = "2027-10-11T12:00:00Z"
        self.assertEqual(i18n.fmt_date("2027-01-09", "en"), "Jan 9, 2027")
        self.assertEqual(i18n.fmt_date("2027-01-09", "ru"), "09.01.2027")
        self.assertEqual(i18n.fmt_datetime("2027-10-12T02:40:00Z", "en", now=now), "Oct 12, 02:40")
        self.assertEqual(i18n.fmt_datetime("2027-10-12T02:40:00Z", "ru", now=now), "12.10, 02:40")
        self.assertEqual(i18n.fmt_datetime("2028-01-09T02:40:00Z", "en", now=now), "Jan 9, 2028, 02:40")
        self.assertEqual(i18n.fmt_datetime("2028-01-09T02:40:00Z", "ru", now=now), "09.01.2028, 02:40")

    def test_the_wizard_link_speaks_the_language(self):
        from kaskad.setup import cli as scli
        import calendar
        at = calendar.timegm((2027, 10, 12, 2, 40, 0))
        with i18n.use_lang("en"):
            self.assertRegex(scli._when(at), r"^Oct 12(, 2027)?, 02:40$")
        with i18n.use_lang("ru"):
            self.assertRegex(scli._when(at), r"^12\.10(\.2027)?, 02:40$")

    def test_the_site_certificate_line(self):
        from kaskad.site import cli as site_cli
        c = {"kind": "letsencrypt", "not_after": "2027-01-09T10:00:00Z", "days_left": 90, "issuer": "R11"}
        with i18n.use_lang("en"):
            self.assertIn("valid until Jan 9, 2027 (", site_cli.cert_line(c))
        with i18n.use_lang("ru"):
            self.assertIn("до 09.01.2027 (", site_cli.cert_line(c))

    def test_no_bare_day_month_formats_left_in_texts_for_people(self):
        """strftime «%d.%m» and «[:10]» dates inside tr() went through fmt_date/fmt_datetime (the journal and JSON keep
        the machine format)."""
        bad = []
        lib = os.path.join(ROOT, "lib", "kaskad")
        for base, _dirs, files in os.walk(lib):
            for n in files:
                if not n.endswith(".py") or "vendor" in base:
                    continue
                p = os.path.join(base, n)
                for no, line in enumerate(read(p).splitlines(), 1):
                    if re.search(r"%d\.%m", line) and "i18n" not in p:
                        bad.append("%s:%d" % (os.path.relpath(p, lib), no))
                    if re.search(r"\btrn?\(.*\b(until|date|when|at)=[^,()\s]*\[:1[06]\]", line):
                        bad.append("%s:%d" % (os.path.relpath(p, lib), no))
        self.assertEqual(bad, [])


def bash():
    return shutil.which("bash")


@unittest.skipUnless(bash(), "нет bash")
class ShellTest(unittest.TestCase):
    """k3_date of install.sh, cut out and run alone (any bash, GNU or BSD date)."""

    def run_sh(self, lang, calls, now=None):
        src = read(INSTALL)
        m = re.search(r"(?ms)^K3_MONTHS_EN=\(.*?^k3_date\(\) \{.*?^\}\n", src)
        self.assertIsNotNone(m, "k3_date")
        script = "K3_LANG=%s\n%s\n%s" % (lang, m.group(0), "\n".join('k3_date %s; echo' % c for c in calls))
        env = dict(os.environ, TZ="UTC", LANG="en_US.UTF-8", LC_ALL="en_US.UTF-8")
        if now is not None:
            env["K3_DATE_NOW"] = str(now)
        r = subprocess.run([bash(), "-c", script], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.decode("utf-8").splitlines()

    def test_formats(self):
        import calendar
        now = calendar.timegm((2027, 10, 11, 12, 0, 0))
        oct12 = calendar.timegm((2027, 10, 12, 2, 40, 0))
        jan9 = calendar.timegm((2028, 1, 9, 2, 40, 0))
        calls = ["2027-01-09", "2027-01-09 short", "%d dt" % oct12, "%d dt" % jan9, "2027-10-12T02:40:00Z dt",
                 "2027-01-09T10:00:00Z", "%d" % oct12, "''", "soon", "2027-01-09 dt"]
        self.assertEqual(self.run_sh("en", calls, now),
                         ["Jan 9, 2027", "Jan 9", "Oct 12, 02:40", "Jan 9, 2028, 02:40", "Oct 12, 02:40",
                          "Jan 9, 2027", "Oct 12, 2027", "", "soon", "Jan 9, 2027"])
        self.assertEqual(self.run_sh("ru", calls, now),
                         ["09.01.2027", "09.01", "12.10, 02:40", "09.01.2028, 02:40", "12.10, 02:40",
                          "09.01.2027", "12.10.2027", "", "soon", "09.01.2027"])

    def test_the_menu_uses_it(self):
        src = read(INSTALL)
        self.assertNotIn('reverse | join(".")', src)                   # «ДД.ММ.ГГГГ» by jq
        self.assertNotRegex(src, r"date [^\n]*'\+%d\.%m")              # «ДД.ММ ЧЧ:ММ» by date
        self.assertIn('when=$(k3_date "$exp" dt)', src)                # the wizard's link «Valid until …»
        self.assertIn("K3_SITE_END=$(k3_date \"$K3_SITE_END\")", src)


@unittest.skipUnless(shutil.which("node"), "нет node")
class WebTest(unittest.TestCase):
    def node(self, script):
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
            f.write(script)
            path = f.name
        try:
            r = subprocess.run(["node", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
                               env=dict(os.environ, TZ="UTC"))
        finally:
            os.unlink(path)
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace")[-2000:])
        return json.loads(r.stdout.decode("utf-8").strip().splitlines()[-1])

    def formats(self, lang, locale):
        boot = read(os.path.join(STATIC, "boot.js"))
        return self.node(jsparts.dates(boot, "D", lang, locale, "2027-10-11T12:00:00Z") + """
const oct12 = new Date(Date.UTC(2027, 9, 12, 2, 40)), jan9 = new Date(Date.UTC(2028, 0, 9, 2, 40));
console.log(JSON.stringify([D.date("2027-01-09T10:00:00Z"), D.dateTime(oct12), D.dateTime(jan9),
  D.dateTime(oct12, true), D.time(oct12), D.date(1799489400), D.date(null), D.date("soon"), D.date(0)]));
""")

    def test_english(self):
        self.assertEqual(self.formats("en", "en-US"),
                         ["Jan 9, 2027", "Oct 12, 02:40", "Jan 9, 2028, 02:40", "Oct 12, 2027, 02:40", "02:40",
                          "Jan 9, 2027", "", "", ""])

    def test_english_of_a_british_browser_is_still_not_ambiguous(self):
        got = self.formats("en", "en-GB")
        self.assertEqual(got[:3], ["9 Jan 2027", "12 Oct, 02:40", "9 Jan 2028, 02:40"])

    def test_russian(self):
        self.assertEqual(self.formats("ru", "ru-RU"),
                         ["09.01.2027", "12.10, 02:40", "09.01.2028, 02:40", "12.10.2027, 02:40", "02:40",
                          "09.01.2027", "", "", ""])

    def test_pages_take_the_one_function(self):
        """No date of the pages is formatted by hand or by toLocale*String (the default of en-US is 01/09/2027)."""
        for name in ("app.js", "setup/setup.js", "graph.js", "user.js"):
            js = read(os.path.join(STATIC, name))
            self.assertNotRegex(js, r"toLocale(Date|Time)?String\(", name)
            self.assertNotRegex(js, r"getMonth\(\) \+ 1", name)
        self.assertIn("DATES.dateTime(", read(os.path.join(STATIC, "app.js")))
        self.assertIn("DATES.date(", read(os.path.join(STATIC, "setup", "setup.js")))
        boot = read(os.path.join(STATIC, "boot.js"))
        self.assertIn("date: dates.date", boot)
        self.assertIn("dateTime: dates.dateTime", boot)


if __name__ == "__main__":
    unittest.main()
