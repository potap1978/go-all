"""``kaskadctl gw activity <человек|устройство> [--range] [--json]`` (k3/handoff/SPEC-activity.md §7, §8.1): the totals
of the period, a line of 24 hours (7 / 30 days) a device, RU and EN, --json — the answer of the API (§5.2) with
``who``, the search by id / login / name, the history off, nothing of another person."""

import json
import re
import time

from kaskad import i18n, state
from kaskad.gw import metrics, model, policy_ops
from tests.test_gw_ops import CliGwCase

MB = 1024 * 1024


class CliActivityTest(CliGwCase):
    def setUp(self):
        super().setUp()
        self.enabled_world()
        self.ok("gw", "inbound", "install", "wg")
        self.ok("gw", "user", "add", "Аня")
        self.ok("gw", "user", "add", "Борис")
        self.ok("gw", "device", "add", "Аня", "iPhone", "--access", "wg")
        self.ok("gw", "device", "add", "Аня", "Ноутбук", "--access", "wg")
        self.ok("gw", "device", "add", "Борис", "Роутер", "--access", "wg")
        st = state.load()
        by = dict((d["name"], d) for d in model.devices(st))
        self.ph, self.nb, self.rt = by["iPhone"]["id"], by["Ноутбук"]["id"], by["Роутер"]["id"]
        self.uid = model.find_user(st, "Аня")["id"]
        b = int(time.time()) // 300 * 300
        w = metrics.open_writer()
        try:
            for k in range(12):
                rows = {self.ph: [300, 120, 50 * MB, 5 * MB], self.rt: [300, 300, 10 ** 9, 10 ** 9]}
                if k < 3:
                    rows[self.nb] = [300, 0, 1024, 1024]
                self.assertTrue(w.write_dev({"ts": b - 300 * (12 - k), "rounds": 30, "secs": 300, "rows": rows}))
        finally:
            w.close()

    def test_a_person_in_russian(self):
        out, _err = self.ok("gw", "activity", "Аня")
        lines = out.rstrip("\n").splitlines()
        head = lines[0]
        self.assertTrue(head.startswith("Аня · 2 устройства · за сутки "), head)
        self.assertIn("(↓ ", head)
        self.assertIn("на связи 1 ч", head)
        self.assertIn("трафик шёл 24 мин", head)
        rows = [l for l in lines[1:] if l.startswith("  ") and ("iPhone" in l or "Ноутбук" in l)]
        self.assertEqual(len(rows), 2, out)
        for r in rows:
            spark = re.search(r"[▁▂▃▄▅▆▇█·]{24}", r)
            self.assertIsNotNone(spark, r)
        self.assertIn("660 МБ", [l for l in rows if "iPhone" in l][0])
        self.assertNotIn("Роутер", out)                         # another person's device

    def test_english_and_a_device(self):
        with i18n.use_lang("en"):
            out, _err = self.ok("gw", "activity", self.ph, "--range", "7d")
        head = out.splitlines()[0]
        self.assertIn("iPhone · Аня · ", head)
        self.assertIn(" in 7 days (↓ ", head)
        self.assertIn("online 1 h", head)
        self.assertFalse(re.search("[а-яё]", re.sub("Аня", "", out), re.I), out)
        self.assertRegex(out, r"[▁▂▃▄▅▆▇█·]{7}")

    def test_json_is_the_answer_of_the_api(self):
        rc, doc, _err = self.run_json("gw", "activity", "Аня", "--range", "30d")
        self.assertEqual(rc, 0)
        self.assertEqual((doc["ok"], doc["activity"], doc["range"], doc["step"], doc["n"]), (True, True, "30d", 3600, 720))
        self.assertEqual(doc["who"], {"kind": "user", "id": self.uid, "name": "Аня"})
        self.assertEqual(sorted(d["id"] for d in doc["devices"]), sorted([self.ph, self.nb]))
        self.assertEqual(sum(doc["rx"]), 12 * 50 * MB + 3 * 1024)
        rc, doc, _err = self.run_json("gw", "activity", self.ph)
        self.assertEqual((doc["who"]["kind"], doc["step"], doc["n"]), ("device", 300, 288))
        self.assertNotIn("devices", doc)
        text = json.dumps(doc, ensure_ascii=False)
        self.assertNotIn("10.77.", text)

    def test_unknown_bad_range_and_off(self):
        rc, _out, err = self.run_cli("gw", "activity", "Никто")
        self.assertNotEqual(rc, 0)
        self.assertIn("Нет такого пользователя или устройства: Никто.", err)
        rc, _out, _err = self.run_cli("gw", "activity", "Аня", "--range", "90d")
        self.assertNotEqual(rc, 0)
        policy_ops.activity_set(False, want_ver=policy_ops.activity_get()["ver"])
        out, _err = self.ok("gw", "activity", "Аня")
        self.assertIn("История активности выключена", out)
        rc, doc, _err = self.run_json("gw", "activity", "Аня")
        self.assertEqual((doc["activity"], doc["why"]), (False, "off"))

    def test_the_search_by_login_and_names(self):
        st = state.load()
        login = model.find_device(st, self.ph).get("login")
        if login:
            rc, doc, _err = self.run_json("gw", "activity", login)
            self.assertEqual(doc["who"]["id"], self.ph)
        rc, doc, _err = self.run_json("gw", "activity", "ноутбук")      # a name, any case
        self.assertEqual(doc["who"]["id"], self.nb)
        rc, doc, _err = self.run_json("gw", "activity", self.uid)
        self.assertEqual(doc["who"]["kind"], "user")
