"""Kaskad 3.3.7.12 — what round 1 of the owner's videos adds to 3.3.7.11: «Пользователи» as a table with folding
columns and the text size, «Выдать настройки» in one window and the apps without triangles, «Протоколы» as rows with
a diagram and the port at install, the new «Обзор» with the server score 0–1000 (provisional in the first 12 h),
«Отклик»/«Дрожание» from the probes, Russian groups in the common card and deleted together with their keys, the same
state words in the CLI, the bot and the SSH menu: one version (3.3.7.12 newer than 3.3.7.11 for the update), and
«Что нового» of 3.3.7.12 for the admin, in six groups, tied to what the code says and does."""
import json
import os
import re
import subprocess
import unittest

import kaskad
from tests.test_3371_release import entry, text

ST = "lib/kaskad/panel/static/"


def _js(lang, name="i18n-%s.js"):
    return dict(re.findall(r'(?m)^\s*"([^"]+)":\s*"((?:[^"\\]|\\.)*)"', text(ST + name % lang)))


def _give(lang):
    """give-i18n-*.js keeps its keys bare and its texts in single quotes."""
    return dict(re.findall(r"(?m)^\s*([A-Za-z0-9_]+):\s*'((?:[^'\\]|\\.)*)'", text(ST + "give-i18n-%s.js" % lang)))


def _cat(lang, name):
    return json.loads(text("lib/kaskad/i18n/catalog/%s/%s.json" % (lang, name)))


def _flat(s):
    return re.sub(r"\s+", " ", s)


GROUPS = {"ru": ("Пользователи", "Выдать настройки", "Протоколы", "Обзор и проверка сервера",
                 "Выходы и российские группы", "Прочее"),
          "en": ("Users", "Issue settings", "Protocols", "Overview and server check", "Exits and Russian groups",
                 "Other")}


class Release33712Test(unittest.TestCase):
    def setUp(self):
        self.readme = text("README.md")
        self.e = entry(self.readme, "3.3.7.12")
        self.ru_raw, sep, self.en_raw = self.e.partition("*In English.*")
        self.assertTrue(sep, "no English part of 3.3.7.12")
        self.ru, self.en = _flat(self.ru_raw), _flat(self.en_raw)

    def test_one_version(self):
        # 3.3.7.13 is above it now (tests/test_33713_release.py): the version is its, the heading names 3.3.7.12 too
        self.assertTrue(kaskad.__version__.startswith("3.3.7."), kaskad.__version__)
        m = re.search(r'(?m)^KASKAD_VERSION="([^"]+)"', text("install.sh")[:4096])
        self.assertEqual(m.group(1), kaskad.__version__)
        self.assertRegex(self.readme, r"(?m)^## Kaskad 3 \(версии [^)]*, 3\.3\.7\.10, 3\.3\.7\.11, 3\.3\.7\.12[ ,]")
        self.assertLess(self.readme.index("**3.3.7.13 — "), self.readme.index("**3.3.7.12 — "))
        self.assertLess(self.readme.index("**3.3.7.12 — "), self.readme.index("**3.3.7.11 — "))

    def test_the_update_sees_it_newer(self):
        """``k3_ver_lt`` of install.sh (sort -V): 3.3.7.12 is newer than 3.3.7.11 and 3.3.7.9, older than 3.3.8."""
        fn = re.search(r"(?m)^k3_ver_lt\(\) \{.*\}$", text("install.sh")).group(0)
        for a, b, lt in (("3.3.7.11", "3.3.7.12", 0), ("3.3.7.12", "3.3.7.11", 1), ("3.3.7.12", "3.3.7.12", 1),
                         ("3.3.7.9", "3.3.7.12", 0), ("3.3.7.12", "3.3.8", 0), ("3.3.3", "3.3.7.12", 0)):
            rc = subprocess.run(["bash", "-c", fn + '\nk3_ver_lt "$1" "$2"', "_", a, b], capture_output=True).returncode
            self.assertEqual(rc, lt, (a, b))

    def test_six_groups_in_order(self):
        for lang, raw in (("ru", self.ru_raw), ("en", self.en_raw)):
            found = re.findall(r"(?m)^\*([^*\n]+)\*$", raw)
            self.assertEqual(tuple(found), GROUPS[lang], lang)

    def test_the_new_parts_tied_to_the_code(self):
        """What README names in 3.3.7.12 — the same words as the panel, the give window, the catalogs."""
        for lang, part, q in (("ru", self.ru, "«%s»"), ("en", self.en, '"%s"')):
            core, ovb = _js(lang), _js(lang, "pages/ovb-i18n-%s.js")
            dg, eg, give = _js(lang, "pages/diag-i18n-%s.js"), _js(lang, "pages/exits-groups-i18n-%s.js"), _give(lang)
            path = lambda *ks: " › ".join(core[k] for k in ks)                       # «Настройки › Шлюз»
            words = [
                # Пользователи
                core["nav.peopleShort"], core["pp.k1.cActDay"], core["pp.k1.cOnline"], core["pp.k1.cProto"],
                core["pp.k1.cRoute"], core["m.fOff"], core["lk.fs"], path("nav.settings", "st.look"),
                core["pp.act.graphT"], core["pp.act.r30d"],
                # Выдать настройки
                core["pp.issue"], core["qp.sub"], core["qp.static"], core["pp.platform"], core["qp.qr"],
                core["c.copy"], core["sb.btn"], give["s1"],
                # Протоколы
                core["nav.inbounds"], core["ib.colFor"], core["ib.ppRec"], core["ib.ppKept"], core["ib.ppNear"],
                core["ib.ppRnd"], core["ib.ppOwn"], core["ib.ppFree"], core["ib.installOn"].format(port=1195),
                # Обзор и проверка сервера
                core["nav.overview"], core["n2.ovSum"], ovb["ovb.v.ok"], ovb["ovb.v.warn"], ovb["ovb.v.bad"],
                ovb["ovb.v.off"], ovb["ovb.d30"], ovb["ovb.xnT"], ovb["ovb.score"], ovb["ovb.why"], ovb["ovb.dr.how"],
                ovb["ovb.prov"], core["n2.ovHealth"], dg["dg.sTitle"], path("nav.settings", "st.health"),
                path("nav.exits", "n2.gGroups"), path("nav.overview", "n2.gCheck"), core["nav.diagNow"],
                core["nav.diagHistory"], dg["dg.lnT"], dg["dg.why.probes"], dg["dg.why.never"], dg["dg.why.load"],
                dg["dg.ck.rtt"], dg["dg.ck.jit"],
                # Выходы и российские группы
                core["n2.exList"], core["ex.xt.cType"], core["ex.xt.cRtt"], core["ex.xt.cGroup"], core["ex.xt.cAct"],
                core["n2.exRu"], eg["ep.ruLinkT"], eg["ep.delGroup"], eg["ep.removeFrom"], eg["ep.perConn"],
                eg["ep.newTier"], eg["ep.rareT"], eg["ep.ru.factsT"],   # метка «РФ» в 3.3.7.13 — значок (K.domMark)
                eg["ep.night"], eg["ep.delGroupKeys"],
                # Прочее
                core["st.gwHost"], path("nav.settings", "st.gw"), core["site.title"], core["site.hostIpBtn"],
                core["site.chkT"], path("nav.settings", "st.storage"), core["sto.wipe"], core["find.ph"],
            ]
            renamed = {"Внутренние группы": "Российские группы", "Domestic groups": "Russian groups"}   # имя до 29.10
            for w in words:
                self.assertTrue(q % renamed.get(w, w) in part, (lang, w))
            # the state words: kaskadctl host health, the bot and the SSH menu say what the panel says
            host, hh = _cat(lang, "gw_host"), _js(lang, "pages/ovb-i18n-%s.js")
            for k in ("ok", "warn", "crit"):
                self.assertIn(q % host["gw_host.light." + k], part)
                self.assertEqual(host["gw_host.light." + k], hh["ovb.hh." + k], k)
            # «Числа замечаний больше нет»: the verdict of «Сводка» carries no number
            for k in ("ok", "warn", "bad", "off"):
                self.assertNotRegex(ovb["ovb.v." + k], r"\d|\{")
            # the score goes from 0 to 1000; «≈» for the links with no reference servers
            self.assertIn("1000", ovb["ovb.dr.howX"])
            self.assertTrue(ovb["ovb.provCap"].startswith("≈ — "))
            self.assertIn("«≈»" if lang == "ru" else '"≈"', part)
        self.assertIn("«%s»" % _give("ru")["recommended"], self.ru)
        self.assertIn("от 0 до 1000", self.ru)
        self.assertIn("from 0 to 1000", self.en)

    def test_numbers_and_commands(self):
        """12 h of probes for a provisional total, the score in the panel only, the commands that exist."""
        self.assertRegex(text("lib/kaskad/score.py"), r"(?m)^WINDOW_HOURS_MIN = 12\b")
        self.assertIn("Первые 12 часов", self.ru)
        self.assertIn("first 12 hours", self.en)
        lib = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib", "kaskad")
        for sub in ("bot", "mcp"):
            for d, _dirs, names in os.walk(os.path.join(lib, sub)):
                for n in names:
                    if n.endswith((".py", ".json")):
                        with open(os.path.join(d, n), encoding="utf-8") as f:
                            s = f.read()
                        self.assertNotRegex(s, r"score_abs|host/synth", os.path.join(sub, n))
        self.assertRegex(text("lib/kaskad/host_cli.py"), r'add_parser\("health"')
        pol = text("lib/kaskad/gw/cli_policy.py")
        self.assertIn('add_parser("group-del"', pol)
        self.assertIn('add_argument("--with-keys"', pol)
        self.assertIn('"with_keys"', text("lib/kaskad/mcp/actions.json"))
        self.assertIn('txt("Aa")', text(ST + "app.js"))                                  # «Aa» on a phone
        for part in (self.ru, self.en):
            for cmd in ("`kaskadctl host health`", "`kaskadctl gw group-del … --with-keys`", "`kaskadctl`"):
                self.assertIn(cmd, part)

    def test_russian_and_english(self):
        self.assertIsNone(re.search(r"[А-Яа-яЁё]", self.en))
        for part in (self.ru, self.en):
            self.assertNotRegex(part, r"\.ru\b")
            self.assertIsNone(re.search(r"\b(REV|SEC|DQA|D-\d+|Н-\d|З-\d|М-\d|С-\d|П-\d|Р-\d|Ш\d|В\d|А\d|Q\d|AC\d|T-\d+|"
                                        r"it1|Т\d|Л\d)\b", part))
            self.assertNotRegex(part, r"№\s?\d")                                          # no numbers of remarks
            self.assertNotRegex(part, r"\.(js|py|css|json|md)\b")                         # no file names
        self.assertIsNone(re.search(r"(?i)\bnodes?\b", self.en))
        self.assertNotRegex(self.ru, r"(?i)человек|\bлюд")
        self.assertNotRegex(self.en, r"(?i)\b(person|people)\b")


if __name__ == "__main__":
    unittest.main()
