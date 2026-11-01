"""Круг 1 Т1, миграция (правило владельца 24.10: «старые данные → новая» и откат в каждом выпуске).

Т1 не меняет ни одной таблицы metrics.db, ни state.json, ни истории проверок: балл 0–1000 (GET /api/host/synth), «Выходы ·
час» (GET /api/gw/exits/hour) и пороги здоровья только ЧИТАЮТ то, что пишет 3.3.7.11. Проверка — данными, которые
записал сам выпущенный код (дерево коммита 3.3.7.11 через ``git archive``, подпроцесс):

1. 3.3.7.11 → эта версия: проверки сервера и минуты выходов 3.3.7.11 читаются — балл звеньев и итог по проверкам,
   ряд выхода за час; чтение ничего не пишет (файлы корня до и после — байт в байт);
2. откат: после чтения этой версией 3.3.7.11 читает свои данные так же (история проверок, «от обычного»,
   история выхода) — те же ответы, что до обновления.

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

MAKE = r"""
import json, sys
from kaskad import score as S
from kaskad.bench import spool
from kaskad.gw import metrics
vals, inv, now, eid = json.loads(sys.argv[1]), json.loads(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
metrics.open_writer().close()
for n, d in enumerate(range(12, 0, -1)):
    v = dict(vals, net_up=430) if d <= 3 else dict(vals)
    h = {"id": "%010x" % (n + 1), "ts": now - d * 86400, "mode": "full", "by": "schedule", "status": "ok", "inv": inv}
    spool.put(spool.check_doc(h, S.with_mode(v, "full")))
w = metrics.open_writer()
try:
    for i in range(1, 40):
        w.write_minute((now // 60 - i) * 60, {eid: {"rx": 750000, "tx": 1000, "rtt_med": 40.0 + i, "loss": 0.0, "state": "ok"}})
    with w.conn:                                     # пробы узла за 14 дней — как пишет сборщик (host_ping_h1, host_h1)
        for i in range(1, 14 * 24 + 1):
            ts = (now // 3600 - i) * 3600
            for a in ("a", "b", "c", "*"):
                w.conn.execute("INSERT OR REPLACE INTO host_ping_h1 (anchor, ts, n, lost, runs, rtt_med, rtt_p90, jit, jit_n)"
                               " VALUES (?,?,?,?,?,?,?,?,?)", (a, ts, 1080, 0, 0, 30.0, 40.0, 1.5, 1000))
            w.conn.execute("INSERT OR REPLACE INTO host_h1 (ts, n, busy, busy_max, steal, warn_min, alarm_min)"
                           " VALUES (?,?,?,?,?,?,?)", (ts, 60, 10.0, 30.0, 0.2, 0, 0))
finally:
    w.close()
print(json.dumps({"ok": True}))
"""
READ = r"""
import json, sys
from kaskad.bench import history, hostscore as HS
from kaskad.gw import metrics
now, eid = int(sys.argv[1]), sys.argv[2]
runs = history.runs(now=now)["runs"]
doc = HS.node_doc(now)
links = dict((l["id"], l.get("rel")) for l in doc.get("links", []))
ex = metrics.exit_summary(now)
print(json.dumps({"runs": [r["id"] for r in runs], "links": links, "rel": doc.get("summary", {}).get("rel_total"),
                  "exits": sorted(ex.get("exits", {})) if isinstance(ex, dict) else None}))
"""
EID = "e-0123456789"


class T1MigrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="k3-t1-mig-")
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
        share_mig.run_old(self.tree, self.root, MAKE, json.dumps(share_mig.VALS), json.dumps(share_mig.INV), str(self.now), EID)
        from kaskad.bench import hostscore as HS
        HS.reset()
        self.addCleanup(HS.reset)
        p = mock.patch.dict(os.environ, {"KASKAD_ROOT": self.root})
        p.start()
        self.addCleanup(p.stop)

    def files(self):
        out = {}
        for d, _dirs, names in os.walk(self.root):
            for n in names:
                with open(os.path.join(d, n), "rb") as f:
                    out[os.path.join(d, n)] = f.read()
        return out

    def test_the_data_of_3_3_7_11_are_read_and_rolled_back(self):
        from kaskad.bench import hostscore as HS
        from kaskad.gw import metrics
        before_old = share_mig.run_old(self.tree, self.root, READ, str(self.now), EID)
        self.assertEqual(len(before_old["runs"]), 12)
        before = self.files()
        syn = HS.node_synth(self.now)
        self.assertEqual(len(syn["checks"]), 12)                               # все проверки 3.3.7.11 — в ряду итогов
        up = next(x for x in syn["links"] if x["id"] == "up")
        self.assertLess(up["fresh"], up["usual"])                             # отдача в 3 последних — хуже обычного
        self.assertIsNotNone(syn["fresh"]["total"])
        hour = metrics.exit_hour(self.now)
        self.assertTrue(hour["charts"])
        self.assertEqual(sum(1 for v in hour["exits"][EID]["rtt"] if v is not None), 39)
        self.assertEqual(self.files(), before)                               # чтение новой версией ничего не пишет
        after_old = share_mig.run_old(self.tree, self.root, READ, str(self.now), EID)
        self.assertEqual(after_old, before_old)                              # откат: 3.3.7.11 видит то же, что до


if __name__ == "__main__":
    unittest.main()
