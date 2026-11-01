"""B5 (SEC-s1-r2 З2): «Россия напрямую» or the consent ⚠РФ removed and xray refusing the new config — the old config
that sends Russia directly does not keep running: xray is stopped and its file goes (a reboot or ``gw check --repair``
never starts it again). A refusal that closes nothing keeps the old config as before."""

import json
import os

from kaskad import secrets, state
from kaskad.errors import KaskadError
from kaskad.gw import collect, exits, model, xray
from kaskad.i18n import tr
from kaskad.rules import apply as rapply
from kaskad.rules import ops as rops
from tests.fakes_gw import register_dummies
from tests.fakes_nft import F443, save_state
from tests.test_gw_ops import CliGwCase
from tests.test_rules_gw import SOCKS_IN, RulesGwCase

CONF = "/etc/kaskad/gw/xray.json"
REFUSED = "Failed to start: main: failed to load config files > invalid outbound\n"


def direct_users(cfg):
    """The users the config sends directly by the lists (read here, not by ``xray.ru_direct_paths``: red on the old code
    for its reason)."""
    return sorted(set(u for r in cfg["routing"]["rules"] if r.get("outboundTag") == "direct" and "ext:roscomvpn-"
                      in json.dumps(r) for u in r.get("user") or []))


class RuClosedCase(RulesGwCase):
    def socks_world(self):
        """«Мама» on ru_direct with the consent, her devices on SOCKS5 too; xray runs a config with the direct rules."""
        st = self.ru_state(save=False)
        register_dummies(("socks5", {"proto": "tcp", "default_port": 1080, "ifaces": [], "xray_inbound": SOCKS_IN}))
        g = st["gateway"]
        g["inbounds"]["socks5"] = {"enabled": True, "port": 1080}
        for d in g["devices"]:
            d["access"].append("socks5")
        state.save(st)
        self.with_xports()
        self.ok("gw", "apply")
        self.assertTrue(direct_users(json.loads(self.read(CONF))))
        self.assertTrue(xray.status(self.w.runner)["active"])
        return state.load()


class PathsTest(RuClosedCase):
    def test_only_the_direct_rules_of_the_lists(self):
        geo = "ext:%s:direct" % rapply.GEOIP_ASSET
        site = "ext:%s:ru" % rapply.GEOSITE_ASSET
        cfg = {"routing": {"rules": [
            {"inboundTag": ["socks-in"], "user": ["a", "b"], "ip": [geo], "outboundTag": "direct"},
            {"inboundTag": ["vless-in"], "user": ["c"], "domain": [site], "outboundTag": "direct"},
            {"inboundTag": ["socks-in"], "user": ["d"], "outboundTag": "direct"},             # a route «напрямую»
            {"inboundTag": ["socks-in"], "user": ["e"], "domain": [site], "outboundTag": "gx-1"},  # «через выход»
            {"inboundTag": ["socks-in"], "domain": [site], "outboundTag": "block"}]}}
        self.assertEqual(xray.ru_direct_paths(cfg), {("socks-in", "a"), ("socks-in", "b"), ("vless-in", "c")})
        self.assertEqual(xray.ru_direct_paths(None), set())
        self.assertEqual(xray.ru_direct_paths({"routing": {"rules": "x"}}), set())


class RefusedTest(RuClosedCase):
    def test_removing_ru_direct_with_a_refused_config_stops_xray(self):
        self.socks_world()
        self.w.xray.fail = REFUSED
        rc, out, err = self.run_cli("rules", "profile", "user", "Мама", "all_exit")
        self.assertEqual(rc, 0, err)
        self.assertIn("xray не принял конфиг шлюза", err)
        self.assertIn(tr("gw_ops.warn.xray_ru_closed", err="").strip(), err)
        self.assertNotIn("Работает прежний конфиг", err)
        st = state.load()
        self.assertEqual(st["gateway"]["users"][0]["rules"], "all_exit")             # saved
        self.assertNotIn("rf_consent", st["gateway"])                                # the consent went with it
        self.assertFalse(xray.status(self.w.runner)["active"])
        self.assertFalse(xray.status(self.w.runner)["enabled"])                      # a reboot does not start it
        self.assertFalse(os.path.exists(self.path(CONF)))                            # nor gw check --repair
        self.assertEqual(self.w.nft.elements("gw_rd"), [])
        # the collector on its tick: the new config is refused again — xray stays down, nothing directly
        collect.tick(self.w.runner)
        self.assertFalse(xray.status(self.w.runner)["active"])
        self.assertFalse(os.path.exists(self.path(CONF)))
        self.w.xray.fail = None                                                      # the cause fixed: gw apply
        self.ok("gw", "apply")
        cfg = json.loads(self.read(CONF))
        self.assertEqual(direct_users(cfg), [])
        self.assertTrue(xray.status(self.w.runner)["active"])

    def test_a_refusal_that_closes_nothing_keeps_the_old_config(self):
        self.socks_world()
        before = self.read(CONF)
        self.w.xray.fail = REFUSED
        rc, out, err = self.run_cli("rules", "block", "torrent", "on")
        self.assertEqual(rc, 0, err)
        self.assertIn("Работает прежний конфиг", err)
        self.assertTrue(xray.status(self.w.runner)["active"])
        self.assertEqual(self.read(CONF), before)

    def test_apply_closes_under_the_xray_lock_and_says_how_many(self):
        st = self.socks_world()
        sec = secrets.load()
        new = json.loads(json.dumps(st))
        new["gateway"].pop("rf_consent")                                          # a hand edit: no consent
        self.w.xray.fail = REFUSED
        with self.assertRaises(KaskadError) as e:
            xray.apply(new, sec, self.w.runner, exits.pool(self.w.runner))
        self.assertGreater(e.exception.data.get("ru_closed", 0), 0)
        self.assertFalse(xray.status(self.w.runner)["active"])
        self.assertFalse(os.path.exists(self.path(CONF)))
        with self.assertRaises(KaskadError) as e:                                 # nothing on the disk: nothing to close
            xray.apply(new, sec, self.w.runner, exits.pool(self.w.runner))
        self.assertNotIn("ru_closed", e.exception.data)

    def test_new_lists_into_the_gateway_say_it_too(self):
        self.socks_world()
        with state.transaction() as st:
            st["gateway"].pop("rf_consent")                                      # a hand edit: no consent
        self.w.xray.fail = REFUSED
        res = rops.apply_system(self.w.runner)
        self.assertIn(tr("gw_xray.warn.ru_closed"), res["warnings"])
        self.assertFalse(xray.status(self.w.runner)["active"])


class PinDefaultTest(CliGwCase):
    """SEC-s1-r2 П3: on a new node «всё через выход» is in the file — 3.3.5 put back by hand reads a missing default
    as «Россия напрямую» for everyone (``RULES_DEFAULTS``)."""

    @staticmethod
    def stored(st=None):
        raw = ((state.load() if st is None else st).get("gateway") or {}).get("rules")
        return (raw or {}).get("default") if isinstance(raw, dict) else None

    def test_a_new_node_writes_all_exit_at_enable_and_on_a_change(self):
        self.enabled_world(pool=False)
        self.assertTrue(state.born_new(state.load()))
        self.assertEqual(self.stored(), "all_exit")
        with state.transaction() as st:
            st["gateway"].pop("rules")                                           # as before B5
        self.ok("gw", "user", "add", "Мама")
        self.assertEqual(self.stored(), "all_exit")
        self.ok("rules", "default", "all_exit")
        self.assertEqual(self.stored(), "all_exit")

    def test_a_choice_stays_and_an_updated_node_gets_nothing(self):
        st = {"gen": 2, "gateway": {"rules": {"default": "ru_direct", "block_torrent": True}}}
        self.assertFalse(model.pin_default(st))
        self.assertEqual(st["gateway"]["rules"], {"default": "ru_direct", "block_torrent": True})
        st = {"gen": 2, "gateway": {"rules": {"default": "nonsense", "block_ads": False}}}
        self.assertTrue(model.pin_default(st))
        self.assertEqual(st["gateway"]["rules"], {"default": "all_exit", "block_ads": False})
        st = {"gen": 1, "gateway": {}}
        self.assertFalse(model.pin_default(st))
        self.assertEqual(model.rules_conf(st)["default"], "ru_direct")                    # 3.3.x as before
        save_state([F443])
        with state.transaction() as st:
            st["gen"] = 1
        self.ok("gw", "enable")
        self.assertIsNone(self.stored())


if __name__ == "__main__":
    import unittest
    unittest.main()
