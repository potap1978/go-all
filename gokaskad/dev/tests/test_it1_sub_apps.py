"""Список приложений подписки (решение координатора 28.10, вкладка «Подписка» окна «Выдать настройки»):
gw/install_apps.json ``subscription`` — платформа → id приложений, которые берут подписку. Основа — protocols.vless той
же платформы в том же порядке (первое — рекомендуемое), без DefaultVPN (vless:// без подписки) и без роутеров."""
import json
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAT = os.path.join(ROOT, "lib", "kaskad", "gw", "install_apps.json")
ROUTERS = ("keenetic", "openwrt", "asus", "mikrotik")


class SubscriptionAppsTest(unittest.TestCase):
    def setUp(self):
        with open(CAT, encoding="utf-8") as f:
            self.cat = json.load(f)
        self.sub = self.cat.get("subscription")

    def test_present_for_the_platforms_of_vless(self):
        self.assertIsInstance(self.sub, dict)
        vless = self.cat["protocols"]["vless"]
        want = [p for p in vless if p not in ROUTERS]
        self.assertEqual(list(self.sub), want)
        self.assertFalse(set(self.sub) & set(ROUTERS))

    def test_every_app_is_known_and_takes_vless_there(self):
        for p, ids in self.sub.items():
            self.assertTrue(ids, p)
            self.assertEqual(len(ids), len(set(ids)), p)
            for x in ids:
                self.assertIn(x, self.cat["apps"], (p, x))
                self.assertIn(x, self.cat["protocols"]["vless"][p], (p, x))

    def test_order_follows_vless_and_no_defaultvpn(self):
        for p, ids in self.sub.items():
            self.assertNotIn("defaultvpn", ids, p)
            self.assertEqual(ids, [x for x in self.cat["protocols"]["vless"][p] if x != "defaultvpn"], p)

    def test_defaultvpn_really_has_no_subscription(self):
        note = self.cat["apps"]["defaultvpn"]["note"]
        self.assertIn("без подписки", note["ios"]["ru"])                      # каталог: заметка iOS


if __name__ == "__main__":
    unittest.main()
