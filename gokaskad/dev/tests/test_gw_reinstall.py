"""«Удалить → Установить» протокола (тестер T-G1, F-2): запись удалённого входа сохраняется, новая установка
возвращает прежний порт (если он свободен), параметры и ключ сервера — выданные устройствам настройки работают
снова. Не вышло (порт заняли, взяли другой) — результат называет, что сменилось и каким устройствам выдать заново.
«Удалить шлюз» (gw purge) забывает и сохранённые записи."""

import os
import random

from kaskad import errors, secrets, state
from kaskad.gw import model, ops, registry
from kaskad.gw.inbounds.base import DummyInbound
from tests.fakes_gw import FakeUfw
from tests.fakes_nft import F443, save_state
from tests.test_gw_integration import RealAdapterCase
from tests.test_gw_ops import CliGwCase


class AwgLike(DummyInbound):
    """What an AmneziaWG line keeps in its record: a random port (chosen by the core), parameters made once (new
    ones every time they are missing — like awgkeys.gen_params), the public key of the server key."""

    def __init__(self, kind="awg1"):
        super().__init__(kind=kind, random_port=True, default_port=0, ifaces=["kgw-%s" % kind])
        self._rnd = random.Random(7)

    def prepare(self, st, sec, runner=None):
        sec.setdefault_new("%s.server.private" % self.kind, lambda: "SERVERKEY-%d" % self._rnd.randrange(1 << 30))
        out = super().prepare(st, sec, runner)
        cfg = st["gateway"]["inbounds"][self.kind]
        if not cfg.get("params"):
            cfg["params"] = {"H1": str(self._rnd.randrange(5, 1 << 31))}
        cfg["pub"] = "PUB-" + sec["%s.server.private" % self.kind]
        return out


class ReinstallTest(CliGwCase):
    UFW = FakeUfw

    def setUp(self):
        super().setUp()
        self.ads = self.dummies()
        self.ads["awg1"] = AwgLike()
        registry.register(self.ads["awg1"])
        save_state([F443])
        self.ok("gw", "enable")
        self.ok("gw", "inbound", "install", "awg1")
        self.ok("gw", "user", "add", "Мама")
        _, doc, _ = self.run_json("gw", "device", "add", "Мама", "Телефон", "--access", "awg1")
        self.did = doc["device"]["id"]
        self.before = dict(model.inbound(state.load(), "awg1"))

    def test_the_same_port_parameters_and_key_come_back(self):
        port = self.before["port"]
        out, _ = self.ok("gw", "inbound", "uninstall", "awg1")
        self.assertIn("Порт и ключи сервера сохранены", out)
        st = state.load()
        self.assertIsNone(model.inbound(st, "awg1"))
        kept = model.kept_inbound(st, "awg1")
        self.assertEqual((kept["port"], kept["params"], kept["pub"]), (port, self.before["params"], self.before["pub"]))
        self.assertNotIn("enabled", kept)
        self.assertIn("awg1.server.private", secrets.load())                     # keys stay (they always did)
        self.assertIn("dev.%s.awg1.private" % self.did, secrets.load())
        plan = [r for r in ops.inbound_plan(self.w.runner, ["awg1"]) if r["kind"] == "awg1"][0]
        self.assertEqual((plan["kept"], plan["port"]["port"], plan["port"]["conflicts"]), (True, port, []))
        self.assertEqual((plan["needs_core"], plan["needs_key"], plan["build"]), (False, False, ""))
        # the plan passes the adapter's needs_key and build on (stand T-G2b, N-3 and N-6: the menu reads them)
        orig = self.ads["awg1"].requirements
        self.ads["awg1"].requirements = lambda runner: dict(orig(runner), supported=False, why="нет ядра",
                                                            needs_core=True, needs_key=True, build="сборка")
        try:
            plan = [r for r in ops.inbound_plan(self.w.runner, ["awg1"]) if r["kind"] == "awg1"][0]
        finally:
            self.ads["awg1"].requirements = orig
        self.assertEqual((plan["supported"], plan["needs_core"], plan["needs_key"], plan["build"]),
                         (False, True, True, "сборка"))
        rc, doc, err = self.run_json("gw", "inbound", "install", "awg1")
        self.assertEqual(rc, 0, err)
        rec = model.inbound(state.load(), "awg1")
        self.assertEqual((rec["port"], rec["params"], rec["pub"], rec["enabled"]),
                         (port, self.before["params"], self.before["pub"], True))
        self.assertIsNone(model.kept_inbound(state.load(), "awg1"))
        self.assertNotIn(model.KEPT, state.load()["gateway"])                    # nothing left behind
        prev = doc["previous"]
        self.assertEqual((prev["restored"], prev["changed"], prev["text"]), (True, [], ""))
        self.assertEqual([(d["id"], d["name"], d["user_name"]) for d in prev["devices"]], [(self.did, "Телефон", "Мама")])
        self.assertEqual(doc["notes"], [])
        self.assertNotIn("Внимание", err)
        self.assertIn("прежние настройки вернулись", self.log())
        self.ok("gw", "inbound", "uninstall", "awg1")
        out, err = self.ok("gw", "inbound", "install", "awg1")                    # the text of the command line
        self.assertIn("Прежние порт и ключи сервера вернулись: выданные устройствам настройки (1) снова работают", out)

    def test_a_port_taken_meanwhile_names_the_devices(self):
        port = self.before["port"]
        self.ok("gw", "inbound", "uninstall", "awg1")
        self.ok("fwd", "add", "--proto", "udp", "--port", str(port), "--to", "198.51.100.7", "--to-port", "1")
        plan = [r for r in ops.inbound_plan(self.w.runner, ["awg1"]) if r["kind"] == "awg1"][0]
        self.assertEqual((plan["port"]["port"], plan["port"]["conflicts"][0]["kind"]), (port, "forward"))
        self.assertTrue(plan["port"]["free"])
        rc, doc, err = self.run_json("gw", "inbound", "install", "awg1")
        self.assertEqual(rc, 0, err)
        new = model.inbound(state.load(), "awg1")["port"]
        self.assertNotEqual(new, port)
        self.assertEqual(model.inbound(state.load(), "awg1")["params"], self.before["params"])   # the rest is kept
        self.assertIn("прежний порт %d/udp занят" % port, doc["notes"][0])
        prev = doc["previous"]
        self.assertEqual((prev["restored"], prev["changed"]), (False, ["порт %d → %d" % (port, new)]))
        self.assertIn("Телефон (Мама)", prev["text"])
        self.assertIn("выданные раньше настройки не подключатся", prev["text"])
        self.assertIn("Внимание: %s" % prev["text"], err)                        # the menu shows stderr lines
        self.assertIn("сменились: порт %d → %d" % (port, new), self.log())

    def test_another_port_by_choice_or_new_keys(self):
        port = self.before["port"]
        self.ok("gw", "inbound", "uninstall", "awg1")
        rc, doc, _ = self.run_json("gw", "inbound", "install", "awg1", "--port", "31999")
        self.assertEqual(model.inbound(state.load(), "awg1")["port"], 31999)
        self.assertEqual(doc["previous"]["changed"], ["порт %d → 31999" % port])
        self.ok("gw", "inbound", "uninstall", "awg1")
        sec = secrets.load()
        sec.delete("awg1.server.private")                                    # lost (edited by hand): a new key
        sec.save()
        _, doc, _ = self.run_json("gw", "inbound", "install", "awg1")
        self.assertEqual(model.inbound(state.load(), "awg1")["port"], 31999)
        self.assertEqual(doc["previous"]["changed"], ["ключ сервера"])
        self.assertIn("Телефон", doc["previous"]["text"])

    def test_without_devices_nothing_to_give_again(self):
        self.ok("gw", "device", "del", self.did)
        self.ok("gw", "inbound", "uninstall", "awg1")
        _, doc, _ = self.run_json("gw", "inbound", "install", "awg1", "--port", "31998")
        self.assertEqual((doc["previous"]["devices"], doc["previous"]["text"]), ([], ""))

    def test_first_install_and_purge(self):
        _, doc, _ = self.run_json("gw", "inbound", "install", "wg")
        self.assertIsNone(doc["previous"])                                    # never removed: nothing to say
        self.ok("gw", "inbound", "uninstall", "awg1")
        self.assertIsNotNone(model.kept_inbound(state.load(), "awg1"))
        rc, doc, err = self.run_json("gw", "purge", "--yes")
        self.assertEqual(rc, 0, err)
        self.assertNotIn(model.KEPT, state.load()["gateway"])                 # «стереть всё»: the record goes too
        self.assertNotIn("awg1.server.private", secrets.load())
        self.ok("gw", "enable")
        _, doc, _ = self.run_json("gw", "inbound", "install", "awg1")
        self.assertIsNone(doc["previous"])

    def test_uninstall_twice_and_the_installed_record_wins(self):
        self.ok("gw", "inbound", "uninstall", "awg1")
        self.assertEqual(self.run_cli("gw", "inbound", "uninstall", "awg1")[0], errors.ERROR)
        self.ok("gw", "inbound", "install", "awg1", "--port", "31997")
        _, doc, _ = self.run_json("gw", "inbound", "install", "awg1")        # installed: a repeat changes nothing
        self.assertEqual((doc["record"]["port"], doc["previous"]), (31997, None))


class KeptModelTest(CliGwCase):
    def test_keep_forget(self):
        st = {}
        self.assertIsNone(model.kept_inbound(st, "wg"))
        model.keep_inbound(st, "wg", {"enabled": False, "port": 51999, "subnet": "10.77.1.0/24"})
        model.keep_inbound(st, "awg2", {"port": 41000})
        self.assertEqual(model.kept_inbound(st, "wg"), {"port": 51999, "subnet": "10.77.1.0/24"})
        model.forget_kept(st, "wg")
        self.assertEqual(list(st["gateway"][model.KEPT]), ["awg2"])
        model.forget_kept(st, "awg2")
        self.assertNotIn(model.KEPT, st["gateway"])
        model.keep_inbound(st, "wg", {"port": 1})
        model.forget_kept(st)
        self.assertNotIn(model.KEPT, st["gateway"])
        st["gateway"][model.KEPT] = "garbage"
        self.assertIsNone(model.kept_inbound(st, "wg"))
        self.assertEqual(ops._kept_port({"tcp_port": 0}, "tcp_port", False), 0)    # OpenVPN over TCP off stays off
        self.assertIsNone(ops._kept_port({"port": 0}, "port", True))
        self.assertIsNone(ops._kept_port({"port": True}, "port", True))


class RealWireGuardTest(RealAdapterCase):
    def test_the_issued_config_is_the_same_after_remove_and_install(self):
        save_state([F443])
        self.ok("gw", "enable")
        self.ok("gw", "inbound", "install", "wg", "--port", "51830")         # not the default: it must come back
        self.ok("gw", "user", "add", "Мама")
        _, doc, _ = self.run_json("gw", "device", "add", "Мама", "Ноутбук", "--platform", "linux", "--access", "wg")
        did = doc["device"]["id"]
        before = self.run_json("gw", "issue", did)[1]["items"][0]["body"]
        self.assertIn("Endpoint = 203.0.113.10:51830", before)
        self.ok("gw", "inbound", "uninstall", "wg")
        self.assertFalse(os.path.exists(self.path("/etc/wireguard/kgw-wg.conf")))   # the files go, the keys stay
        rc, doc, err = self.run_json("gw", "inbound", "install", "wg")
        self.assertEqual(rc, 0, err)
        self.assertTrue(doc["previous"]["restored"])
        self.assertEqual(self.run_json("gw", "issue", did)[1]["items"][0]["body"], before)
        self.assertIn("ListenPort = 51830", self.read("/etc/wireguard/kgw-wg.conf"))
