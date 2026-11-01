"""Kaskad 3.3.7 — the release: one version, and «Что нового» of 3.3.7 for people, tied to what the code says.

- The heading of Kaskad 3 ends with «… и 3.3.7» (the version itself — tests/test_336_release.py, the test of the
  heading and of install.sh = kaskad.__version__).
- The entry of 3.3.7 stands above 3.3.6, in Russian and in English, in plain words — no codes of the tasks (REV-…,
  З-1, T7, R4, A5, КТ1, D-136…), no «.ru» domains in examples, no Cyrillic in the English part.
- What it names is in the code: the presets of the checks, the states of an exit, the tab «Группы», the menu item 22,
  the CLI of the probes, the bot button «💻 Система», the consent tick of ⚠РФ, the new schemes of the keys; and what is
  said as «следующим выпуском» is not there yet (no «Настройки → Время»; the Russian group came in 3.3.7.10).
"""
import json
import os
import re
import unittest

import kaskad

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(ROOT, "lib", "kaskad")


def text(*name):
    with open(os.path.join(ROOT, *name), encoding="utf-8") as f:
        return f.read()


def entry(readme, version):
    """The entry «**<version> — …**» of «Что нового» up to the next version's entry."""
    i = readme.index("**%s — " % version)
    m = re.compile(r"(?m)^\*\*\d+(?:\.\d+){2,3} — ").search(readme, i + 1)
    return readme[i:m.start() if m else len(readme)]


def js_dict(*name):
    """«"key": "value"» pairs of a panel dictionary (i18n-*.js)."""
    return dict(re.findall(r'^\s*"([\w.]+)":\s*"((?:[^"\\]|\\.)*)"', text(*name), re.M))


class Release337Test(unittest.TestCase):
    def setUp(self):
        self.readme = text("README.md")
        self.e = entry(self.readme, "3.3.7")
        self.ru, sep, self.en = self.e.partition("*In English.*")
        self.assertTrue(sep, "no English part of 3.3.7")
        self.ru, self.en = re.sub(r"\s+", " ", self.ru), re.sub(r"\s+", " ", self.en)    # the words, not the lines

    def test_version_and_order(self):
        # 3.3.7.1 is above it now (tests/test_3371_release.py): the version is its, the heading names both
        self.assertTrue(kaskad.__version__ == "3.3.7" or kaskad.__version__.startswith("3.3.7."), kaskad.__version__)
        self.assertRegex(self.readme,
                         r"(?m)^## Kaskad 3 \(версии [^)]*, 3\.3\.6, 3\.3\.7(, 3\.3\.7\.\d+)* и 3\.3\.7\.\d+\)")
        self.assertLess(self.readme.index("**3.3.7 — "), self.readme.index("**3.3.6 — "))

    def test_russian_and_english(self):
        for words in ("Внутри — всё из 3.3.6", "Проверка выходов", "«годен», «ухудшен», «в запасе» или «снят»",
                      "порог возврата", "квоту трафика", "графики", "Hysteria2, Shadowsocks, Trojan и VMess",
                      "Выходы → Группы", "«Экономно», «Стандарт» и «Строго»", "Что изменится", "по соединениям",
                      "⚠РФ", "«Куда пойдёт трафик?»", "Скрипт не используется на", "светофор «Сервер»", "💻 Система",
                      "пункт 22 SSH-меню «Нагрузка и сеть сервера»", "kaskadctl host set --probes on",
                      "kaskadctl host health", "Что важно при обновлении", "Следующим выпуском"):
            self.assertIn(words, self.ru)
        for words in ("Includes everything of 3.3.6", "Exit checks", "“healthy”, “degraded”, “standby” or “out”",
                      "return threshold", "traffic quota", "charts", "Hysteria2, Shadowsocks, Trojan and VMess",
                      "Exits → Groups", "“Economy”, “Standard” and “Strict”", "What will change", "Per connection",
                      "⚠RU", "Where will the", "The script is not used in the Russian Federation", "“Server” light",
                      "💻 System", "Server load & network", "kaskadctl host set --probes on", "kaskadctl host health",
                      "What matters when updating", "In the next release"):
            self.assertIn(words, self.en)
        self.assertIsNone(re.search(r"[А-Яа-яЁё]", self.en), "Cyrillic in the English part")

    def test_plain_words(self):
        for code in (r"\bREV-", r"\b[ЗПСНТМВАБ]-?\d+\b", r"\b[TRDAB]-?\d+\b", r"\bКТ\d", r"\bF\d\b", r"\bNS-",
                     r"\bBACKLOG", r"\bR-33", r"\bSPEC", r"<!--", r"§"):
            self.assertIsNone(re.search(code, self.e), code)
        self.assertIsNone(re.search(r"\b[\w-]+\.ru\b", self.e), "a .ru domain in the entry")
        for line in self.e.splitlines():
            self.assertLessEqual(len(line), 118, line)

    def test_what_it_names_is_in_the_code(self):
        from kaskad.gw import policy
        self.assertLessEqual({"compat", "standard", "strict", "economy"}, set(policy.PRESETS["abroad"]))
        ep_ru = js_dict("lib", "kaskad", "panel", "static", "pages", "exits-groups-i18n-ru.js")
        ep_en = js_dict("lib", "kaskad", "panel", "static", "pages", "exits-groups-i18n-en.js")
        # UI-A2 (UX-аудит 20.10, «Статусы»): одно слово на состояние, как в «Выходах» — «годен» / «снят» стали
        # «работает» / «не работает» (EN — глоссарий: working / down); README 3.3.7 — история того выпуска
        for k, ru, en in (("ep.preset.economy", "Экономно", "Economy"), ("ep.preset.standard", "Стандарт", "Standard"),
                          ("ep.preset.strict", "Строго", "Strict"), ("ep.st.ok", "работает", "working"),
                          ("ep.st.degraded", "ухудшен", "degraded"), ("ep.st.standby", "в запасе", "standby"),
                          ("ep.st.off", "не работает", "down"), ("ep.perConn", "По соединениям", "Per connection")):
            self.assertEqual((ep_ru.get(k), ep_en.get(k)), (ru, en), k)
        self.assertIn("«Россия напрямую»", ep_ru["ep.roleRuD"])         # the Russian group: включается с 3.3.7.10 (Q6)
        ui_ru = js_dict("lib", "kaskad", "panel", "static", "i18n-ru.js")
        ui_en = js_dict("lib", "kaskad", "panel", "static", "i18n-en.js")
        self.assertEqual((ui_ru["ex.tabGroups"], ui_en["ex.tabGroups"]), ("Группы", "Groups"))
        self.assertEqual((ui_ru["ov.host2"], ui_en["ov.host2"]), ("Сервер", "Server"))
        self.assertEqual(ui_ru["rf.consent"], "Скрипт не используется на территории РФ")
        self.assertEqual(ui_en["rf.consent"], "The script is not used in the Russian Federation")
        self.assertFalse([k for k in ui_ru if k.startswith("tz.")], "«Настройки → Время» is said to come next")
        kit = js_dict("lib", "kaskad", "panel", "static", "kit-i18n-en.js")
        self.assertEqual(kit["kit.what"], "What will change")
        self.assertIn("M_ru_MM_I22='Нагрузка и сеть сервера'", text("lib", "kaskad", "i18n", "sh", "menu.ru.sh"))
        self.assertIn("M_en_MM_I22='Server load & network'", text("lib", "kaskad", "i18n", "sh", "menu.en.sh"))
        host = json.loads(text("lib", "kaskad", "i18n", "catalog", "ru", "gw_host.json"))
        self.assertIn("пункт 22 SSH-меню или kaskadctl host set --probes on", host["gw_host.event.probes_hint"])
        actions = text("lib", "kaskad", "mcp", "actions.json")
        self.assertIn('"host_health"', actions)
        self.assertIn('"rules_consent"', actions)
        from kaskad.gw import exits
        for link, proto in (("hysteria2://p@a.example.com:443", "hysteria2"), ("ss://x@a.example.com:443", "shadowsocks"),
                            ("trojan://p@a.example.com:443", "trojan"), ("vmess://e30=", "vmess")):
            self.assertEqual(exits.link_proto(link), proto, link)


    def flat(self, part):
        return re.sub(r"\s+", " ", part)

    def test_the_numbers_are_the_code_s(self):
        """README-сверка 3.3.7, п. 5: числа «Что нового» — из кода, а не на память."""
        from kaskad.gw import hostping, policy
        ru, en = self.flat(self.ru), self.flat(self.en)
        mins = policy.UNSTABLE_S // 60
        self.assertEqual(policy.UNSTABLE_S % 60, 0)
        self.assertIn("(%d падения за %d минут) уходит в запас на %d минут" % (policy.FLAP_FALLS, mins, mins), ru)
        self.assertIn("(%d failures in %d minutes) goes to standby for %d minutes" % (policy.FLAP_FALLS, mins, mins), en)
        # «нестабильный — в запас» — только в наборах, где flap_standby (не «как раньше»)
        flap = sorted(k for k in policy.PRESETS["abroad"]
                      if policy.default_group("main", "abroad", k)["flap_standby"])
        self.assertEqual(flap, ["economy", "standard", "strict"])
        self.assertIn("В наборах «Экономно», «Стандарт» и «Строго» нестабильный выход", ru)
        self.assertIn("In the “Economy”, “Standard” and “Strict” sets an unstable exit", en)
        hold = set(policy.default_group("main", "abroad", k)["return_to_primary"]["hold_min"] for k in flap)
        self.assertEqual(hold, {10})
        self.assertIn("снова годен %d минут подряд" % hold.pop(), ru)
        self.assertIn("healthy for 10 minutes in a row", en)
        n = len(hostping.DEFAULT_ANCHORS)
        words = {3: ("трём", "three")}[n]
        self.assertIn("раз в ≈ %d с по %d пакета ping к %s точкам" % (hostping.PERIOD, hostping.COUNT, words[0]), ru)
        self.assertIn("every ≈ %d s, %d ping packets to %s points" % (hostping.PERIOD, hostping.COUNT, words[1]), en)

    def test_the_words_are_the_panel_s(self):
        """README-сверка 3.3.7, п. 5: плашка, способы выбора на уровне и действия квоты — тексты панели и ядра."""
        from kaskad.gw import policy
        ep_ru = js_dict("lib", "kaskad", "panel", "static", "pages", "exits-groups-i18n-ru.js")
        ep_en = js_dict("lib", "kaskad", "panel", "static", "pages", "exits-groups-i18n-en.js")
        ru, en = self.flat(self.ru), self.flat(self.en)
        self.assertTrue(ep_ru["ep.hint"].endswith("Включить «Стандарт»?"), ep_ru["ep.hint"])
        self.assertTrue(ep_en["ep.hint"].endswith("Turn on “Standard”?"), ep_en["ep.hint"])
        self.assertIn("плашкой «Включить «Стандарт»?»", ru)
        self.assertIn("the banner “Turn on “Standard”?”", en)
        self.assertEqual(policy.METHODS, ("even", "best_rtt", "weights", "capacity"))
        lo = lambda d: [d["ep.method." + m][0].lower() + d["ep.method." + m][1:] for m in policy.METHODS]  # noqa: E731
        self.assertIn("поровну, лучший отклик, по весам или по скорости", ru)
        self.assertEqual(lo(ep_ru), ["поровну", "лучший отклик", "по весам", "по скорости"])
        self.assertIn("evenly, best response, by weight or by speed", en)
        self.assertEqual(lo(ep_en), ["evenly", "best response", "by weight", "by speed"])
        self.assertEqual(policy.QUOTA_ACTIONS, ("remove", "standby", "push"))       # снят, в запас, только сообщить
        self.assertIn("выход снимается, уходит в запас или Kaskad только сообщает", ru)
        self.assertIn("the exit is taken out, goes to standby, or Kaskad only notifies", en)

    def test_what_the_review_of_the_readme_asked(self):
        """README-сверка 3.3.7, п. 1–3, 6–8 и итоговая строгая (группы на телефоне)."""
        ru, en = self.flat(self.ru), self.flat(self.en)
        self.assertNotIn("условия и квоты", ru)                    # квота — в подробностях выхода
        self.assertNotIn("conditions and quotas", en)
        self.assertNotIn("один раз подскажет", ru)
        self.assertIn("подскажет в итоге обновления", ru)
        self.assertNotIn("tells you once", en)
        self.assertIn("the summary of the update tells you", en)
        self.assertNotIn("переименование всего", ru)
        self.assertIn("переименование людей и устройств (выходы, группы и уровни переименовываются уже сейчас)", ru)
        self.assertIn("renaming people and devices (exits, groups and levels can be renamed already)", en)
        self.assertIn("бот присылает сообщение о ⚠РФ", ru)
        self.assertIn("the bot sends a message about ⚠RU", en)
        self.assertNotIn("расходуют меньше трафика", ru)
        self.assertNotIn("use less traffic", en)
        self.assertIsNone(re.search(r"\d+\s*(?:МБ|MB)", self.e), "a number of megabytes without a measurement")
        self.assertIn("Вкладка «Группы», наборы, условия и плашка «Включить «Стандарт»?» пока только на компьютере", ru)
        self.assertIn("on a computer only for now", en)
        self.assertIn("группы выходов на телефоне", ru)
        self.assertIn("exit groups on a phone", en)
        # что названо «уже сейчас» — есть: переименование выхода, группы и уровня в панели
        ep_ru = js_dict("lib", "kaskad", "panel", "static", "pages", "exits-groups-i18n-ru.js")
        self.assertEqual((ep_ru["ep.groupName"], ep_ru["ep.tierRename"]), ("Имя группы", "Переименовать уровень"))


if __name__ == "__main__":
    unittest.main()
