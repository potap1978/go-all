"""П8 (3.3.7.15, OBS-3315-1822): заметка «Скачать мои новые настройки» (F7) и «Выдать» в любом порядке со сменой
порта, авто-откатом и сменой сети шлюза. Путь настоящий: ``ops.issue_preview`` → ``ops.inbound_settings`` через
``pending.guard`` (администратор — Телефон в пуле WG, окно 2 мин) → ``pending.expire`` (авто-откат настоящим
``inbound_settings_undo``) → ``ops.issue`` («Выдать»).

Дыра (С5, С7, С8 — падают без правки): «Выдать» не гасил живую заметку F7, и применение той же смены в 15 мин снимало
пометку с устройства, у которого после «Выдать» старые настройки, — оно не подключится, «Ждут новых настроек» молчит.
"""

import json
import os
import time

from kaskad import state
from kaskad.gw import model, ops, pending, reissue
from tests.test_r15_net_base import NetCase

NEW = 51826


class PreviewThenGiveTest(NetCase):
    def setUp(self):
        super().setUp()
        r = self.w.runner
        self.laptop = self.did                          # «Ноутбук» NetCase
        self.phone = ops.device_add(r, self.uid, "Телефон", kind="phone", platform="android",
                                    access=["wg"])["device"]["id"]
        self.tv = ops.device_add(r, self.uid, "ТВ", kind="tv", platform="other", access=["wg"])["device"]["id"]
        self.pip = self.dev(self.phone)["ips"]["wg"]
        self.p0 = self.wgport()

    # ── helpers ──
    def who(self):
        return {"via": "panel", "ip": self.pip}         # панель из туннеля Телефона: окно подтверждения F7

    def wgport(self):
        return model.inbound(state.load(), "wg")["port"]

    def marks(self):
        return reissue.load().get("wg", {})

    def note(self):
        p = ops.preview_path()
        if not os.path.exists(p):
            return None
        with open(p, encoding="utf-8") as f:
            return json.load(f).get("wg")

    def f7(self, port=NEW):
        return ops.issue_preview(self.w.runner, None, "wg", {"port": port}, who=self.who())

    def put(self, port=NEW):
        return ops.inbound_settings(self.w.runner, "wg", port=port, yes=True, who=self.who())

    def expire(self, res):
        p = res["pending"]
        self.assertIsNotNone(p, "администратор в пуле WG: окно подтверждения")
        self.assertEqual(pending.expire(self.w.runner, p["token"], now=p["deadline"] + 1)["done"], "reverted")
        self.assertEqual(self.wgport(), self.p0)

    def give(self, dev=None):
        ops.issue(self.w.runner, dev or self.phone, inbound="wg", where="panel")

    def assert_hard(self, dev):
        m = self.marks().get(dev)
        self.assertIsNotNone(m, "нет пометки")
        self.assertEqual((m["what"], m["hard"]), (["port"], True))

    # ── порядок стенда R.5 и его варианты: так было и до П8 ──
    def test_s1_stand_order_names_the_phone(self):
        """F7 → применить → авто-откат → «Выдать» (51820) → повтор без нового F7: Телефон назван и жёсткий."""
        self.f7()
        r = self.put()
        self.assertNotIn("Телефон", r["text"])                         # первое применение: F7 засчитан
        self.assertIsNone(self.note())                                 # заметка одноразовая
        self.expire(r)
        self.assertIsNone(self.note())                                 # откат её не возвращает
        self.assert_hard(self.phone)                                   # у Телефона 51826, сервер снова 51820
        self.give()
        self.assertNotIn(self.phone, self.marks())
        r = self.put()
        self.assertIn("Телефон", r["text"])
        self.assert_hard(self.phone)

    def test_s2_a_new_preview_after_give_counts(self):
        """Перед повтором снова F7 на тот же порт (после «Выдать»): Телефон скачал 51826 — без пометки (наблюдение
        18:22 — так)."""
        self.f7()
        self.expire(self.put())
        self.give()
        self.f7()
        r = self.put()
        self.assertNotIn("Телефон", r["text"])
        self.assertNotIn(self.phone, self.marks())
        self.assert_hard(self.laptop)
        self.assert_hard(self.tv)

    def test_s3_give_inside_the_window(self):
        """«Выдать» в окне (51826) → откат: Телефон жёсткий; повтор — жёсткий и назван (П-2 A→B→A: безопасная
        сторона)."""
        self.f7()
        r = self.put()
        self.give()
        self.expire(r)
        self.assert_hard(self.phone)
        r = self.put()
        self.assertIn("Телефон", r["text"])
        self.assert_hard(self.phone)

    def test_s4_no_give(self):
        self.f7()
        self.expire(self.put())
        r = self.put()
        self.assertIn("Телефон", r["text"])
        self.assert_hard(self.phone)

    def test_s6_a_stale_note(self):
        self.f7()
        p = ops.preview_path()
        with open(p, encoding="utf-8") as f:
            doc = json.load(f)
        doc["wg"]["at"] -= ops.PREVIEW_TTL + 1
        with open(p, "w", encoding="utf-8") as f:
            json.dump(doc, f)
        r = self.put()
        self.assertIn("Телефон", r["text"])
        self.assert_hard(self.phone)

    # ── П8: «Выдать» гасит живую заметку своего устройства (без правки падают) ──
    def test_s5_give_after_preview_takes_the_note(self):
        """F7 51826, окно закрыли без «Применить» → «Выдать» Телефону (у него 51820) → применение 51826 в 15 мин:
        Телефон назван и жёсткий."""
        self.f7()
        self.give()
        self.assertIsNone(self.note())
        r = self.put()
        self.assertIn("Телефон", r["text"])
        self.assert_hard(self.phone)

    def test_s7_refused_apply_then_give(self):
        """Применение отказано 409 (ждёт другой вид) — заметку не тратит; «Выдать» её гасит; применение — пометка."""
        self.f7()
        pending.arm(self.w.runner, "awg2", "a-other", self.who(), time.time())
        with self.assertRaises(pending.Refused):
            self.put()
        self.assertIsNotNone(self.note())
        self.assertEqual(self.wgport(), self.p0)
        pending.disarm(self.w.runner)
        self.give()
        r = self.put()
        self.assertIn("Телефон", r["text"])
        self.assert_hard(self.phone)

    def test_s8_net_base_give_after_preview(self):
        """F6: F7 на новую сеть → «Выдать» (старая сеть) → смена сети: у Телефона жёсткая ``net``."""
        ops.issue_preview(self.w.runner, None, "wg", {"base": "10.78.0.0/16"}, who=self.who())
        self.give()
        res = ops.net_base(self.w.runner, "10.78.0.0/16", yes=True)
        m = self.marks().get(self.phone)
        self.assertIsNotNone(m)
        self.assertEqual((m["what"], m["hard"]), (["net"], True))
        self.assertIn(self.phone, [r["id"] for r in res["reissue"]["wg"]])

    def test_a_give_of_another_device_keeps_the_note(self):
        """«Выдать» Ноутбуку не трогает заметку Телефона: применение той же смены его по-прежнему засчитывает."""
        self.f7()
        self.give(self.laptop)
        self.assertEqual(self.note()["device"], self.phone)
        r = self.put()
        self.assertNotIn("Телефон", r["text"])
        self.assertNotIn(self.phone, self.marks())
        self.assert_hard(self.laptop)
