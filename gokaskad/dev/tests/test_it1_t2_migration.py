"""Круг 1 Т2, миграция (правило владельца 24.10: «старые данные → новая» и откат в каждом выпуске).

Из хранимого Т2 меняет одно: «Удалить историю активности» (№41) стирает строки dev_* в metrics.db и ставит метку
``meta.dev_wiped`` (таблица meta была и в 3.3.7.11, новых таблиц и столбцов нет). Удаление группы с ключами (8а)
формат state.json не меняет — только убирает группу и её ключи тем же писателем политики. Проверка — данными, которые
записал сам выпущенный код (дерево коммита 3.3.7.11 через ``git archive``, подпроцесс):

1. 3.3.7.11 → эта версия: активность, записанная 3.3.7.11, читается; «Удалить историю» стирает её в базе 3.3.7.11;
2. откат: 3.3.7.11 открывает базу с меткой dev_wiped без ошибки, стёртого не видит, новые окна пишет и читает.

Без git или без коммита — пропуск.
"""

import json
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

from tests import test_share_migration as share_mig

OLD = "70d9b3f0"                                    # 3.3.7.11 (dev = test)
DEV = "d-aaaa"

WRITE = r"""
import json, sys
from kaskad.gw import metrics
ts = json.loads(sys.argv[1])
w = metrics.open_writer()
try:
    for t in ts:
        w.write_dev({"ts": t, "rounds": 30, "secs": 300, "rows": {"d-aaaa": [300, 120, 50000, 6000]}})
finally:
    w.close()
print(json.dumps({"ok": True}))
"""
READ = r"""
import json, sys
from kaskad.gw import metrics
s = metrics.dev_summary(["d-aaaa"], now=int(sys.argv[1]))
d = s.get("devices", {}).get("d-aaaa") or {}
print(json.dumps({"activity": s.get("activity"), "kb": sum(x or 0 for x in d.get("kb") or [])}))
"""


class T2MigrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="k3-t2-mig-")
        with mock.patch.object(share_mig, "PREV", OLD):
            cls.tree = share_mig.old_tree(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        from kaskad.gw import metrics
        if not metrics.available():
            self.skipTest("no sqlite3")
        self.root = tempfile.mkdtemp(prefix="root-", dir=self.tmp)
        self.now = int(time.time())
        self.b = self.now // 300 * 300 - 3600
        p = mock.patch.dict(os.environ, {"KASKAD_ROOT": self.root})
        p.start()
        self.addCleanup(p.stop)

    def old(self, code, *args):
        return share_mig.run_old(self.tree, self.root, code, *args)

    def new_kb(self):
        from kaskad.gw import metrics
        d = metrics.dev_summary([DEV], now=self.now).get("devices", {}).get(DEV) or {}
        return sum(x or 0 for x in d.get("kb") or [])

    def test_activity_of_3_3_7_11_wiped_and_rolled_back(self):
        from kaskad.gw import metrics
        self.old(WRITE, json.dumps([self.b, self.b + 300, self.b + 600]))
        before = self.old(READ, str(self.now))
        self.assertTrue(before["activity"])
        self.assertGreater(before["kb"], 0)
        self.assertGreater(self.new_kb(), 0)                                  # новая версия читает окна 3.3.7.11
        got, why = metrics.wipe_dev(self.now)
        self.assertIsNone(why)
        self.assertGreaterEqual(got["rows"], 3)
        self.assertEqual(self.new_kb(), 0)
        # откат: 3.3.7.11 открывает базу с меткой dev_wiped, стёртого не видит, новое окно пишет и читает
        self.assertEqual(self.old(READ, str(self.now))["kb"], 0)
        self.old(WRITE, json.dumps([self.b + 1800]))
        self.assertGreater(self.old(READ, str(self.now))["kb"], 0)


if __name__ == "__main__":
    unittest.main()
