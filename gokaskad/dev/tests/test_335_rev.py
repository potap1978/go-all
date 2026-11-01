"""3.3.5, the findings of the strict check REV-335 (k3/stand/logs/REV-335.md) that the other test files do not hold:
№4 — the words of an exit that fails with fewer than MIN_TRIES checks (SSH, ``gw exits`` and the panel), №9 — the order
«ids in subs.json before the pool is replaced» and the name of a lost key in the preview of a copy (``_exit_label``)."""

import os
import re
from unittest import mock

from kaskad import backup
from kaskad.gw import exits, exitsubs
from kaskad.i18n import use_lang
from tests.fakes_gw import write_pool
from tests.test_335_rename import RenameCase, b64, key
from tests.test_gw_exitsubs import IP_URL
from tests.test_backup import BackupCase
from tests.fakes_gw import VLESS_B

STATIC = os.path.join(os.path.dirname(__file__), "..", "lib", "kaskad", "panel", "static")


def row(fails, checks, silent=15):
    return {"usable": True, "state": "alive", "fails": fails, "dies_after": checks, "silent": silent,
            "detail": exits.word("detail.no_answer")}


class FewChecksTest(BackupCase):
    """REV-335 №4: «работает … (не прошло проверок: 2 из 3)» read as a contradiction — fewer than 4 checks and half of them
    failed: «проверяется», with when the rule decides."""

    def test_the_words(self):
        self.assertEqual(exits.state_text(row(2, 3)),
                         "проверяется: нет ответа 15 с (не прошло проверок: 2 из 3; решение — от 4 проверок)")
        self.assertEqual(exits.state_text(row(1, 2)),
                         "проверяется: нет ответа 15 с (не прошло проверок: 1 из 2; решение — от 4 проверок)")
        self.assertEqual(exits.state_text(row(1, 3)),           # less than half: it works
                         "работает, но нет ответа 15 с (не прошло проверок: 1 из 3 за 2 мин)")
        self.assertEqual(exits.state_text(row(2, 5)),
                         "работает, но нет ответа 15 с (не прошло проверок: 2 из 5 за 2 мин)")
        with use_lang("en"):
            self.assertEqual(exits.state_text(row(2, 3)),
                             "being checked: no answer for 15 s (failed checks: 2 of 3; decided from 4 checks)")

    def test_the_panel_says_the_same(self):
        with open(os.path.join(STATIC, "app.js"), encoding="utf-8") as f:
            js = f.read()
        m = re.search(r"const EXIT_MIN_CHECKS = (\d+);", js)
        self.assertIsNotNone(m)
        self.assertEqual(int(m.group(1)), exits.MIN_TRIES)       # one rule: the panel and kaskad.gw.exits
        self.assertIn('few ? "ex.silentFew" : "ex.silentCheck"', js)
        for lang in ("ru", "en"):
            with open(os.path.join(STATIC, "i18n-%s.js" % lang), encoding="utf-8") as f:
                cat = f.read()
            self.assertRegex(cat, r'"ex\.silentFew": ".*\{min\}.*"')
            self.assertNotIn("клиенты перейдут на другой выход", cat)     # not true of an exit pinned with «блок»


class IdsBeforeThePoolTest(RenameCase):
    """REV-335 №9(а): the map «ids» is in subs.json BEFORE the pool is replaced — the rebuild of the gateway inside
    --egress-replace names the renamed line by the kept id at once (no new tag, no second rebuild)."""

    def test_the_map_is_saved_before_the_pool(self):
        self.add(IP_URL, b64([key("10GB left"), VLESS_B]))
        eid = self.ids()[0]
        seen = []
        real = exitsubs.replace_pool

        def spy(runner, cur, pool, only=()):
            seen.append(dict(exitsubs.load().get("ids") or {}))
            return real(runner, cur, pool, only)

        self.feed.bodies[IP_URL] = b64([key("9GB left"), VLESS_B])
        with mock.patch.object(exitsubs, "replace_pool", spy):
            r = self.refresh()["results"][0]
        self.assertEqual(r["updated"], 1, r["text"])
        self.assertEqual(seen, [{exits.exit_id(key("9GB left")): eid}])
        self.assertEqual(self.ids()[0], eid)


class LabelOfALostKeyTest(BackupCase):
    """REV-335 №9(б): a key renamed by its subscription keeps its id — the preview of a copy names it by that id."""

    def test_the_kept_id_names_it(self):
        renamed = key("9GB left")
        kept = exits.exit_id(key("10GB left"))
        write_pool([renamed])
        doc = exitsubs.load()
        doc["ids"] = {exits.exit_id(renamed): kept}
        exitsubs.save(doc)
        self.assertEqual(backup._exit_label(renamed, {kept: "Мой выход"}, None), "Мой выход")
