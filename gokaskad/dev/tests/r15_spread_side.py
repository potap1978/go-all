"""3.3.7.15 F12б, the rollback (the owner's rule 24.10: «прошлая версия читает новые»): the side that runs in a tree of
its own — the RELEASED 3.3.7.13 / 3.3.7.14 (``tests/test_r15_spread.py`` copies this file into the trees of bc541e29 and
fc5ef683) or this version (the same file, this tree). ``R15S_IO`` — the folder of the exchange, ``R15S_OUT`` — the name
of the answer ("new" — this version spreads; else the old one is given ``R15S_SRC``, the addresses this version wrote).

The fake node of tests/test_mip_s34.Node (A — the address of the route, B — the second one of eth0; VLESS_A + the leg
kawg2 whose config names the Endpoint), applied; then ``exits[id].src`` — written by «Разнести выходы» here, by hand
(the same field) in an old tree — and ``gw apply``: the SNAT of the leg in gw_post and ``sendThrough`` of the xray exit.

Only what 3.3.7.13 has is used. Not a test of this tree: no ``test_`` in the name, skipped without ``R15S_OUT``."""

import json
import os
import unittest

from kaskad import cli, state
from kaskad.gw import exits, model, xray
from tests.fakes_nft import F443
from tests.gwcase import GwCase
from tests.test_mip_s34 import A_IP, chain, leg_conf, two_addresses

IO = os.environ.get("R15S_IO", "")
OUT = os.environ.get("R15S_OUT", "")


class Side(GwCase):
    def run_cli(self, *argv):
        try:
            return cli.main(list(argv))
        except SystemExit as e:
            return e.code

    def test_side(self):
        if not OUT:
            raise unittest.SkipTest("the side of tests/test_r15_spread.py")
        two_addresses(self)
        self.scenario(forwards=[F443])
        self.w.leg(2)
        aid = [e for e in exits.pool(self.w.runner) if e.get("kind") == "awg"][0]
        leg_conf(aid["iface"])
        doc = {"host": self.run_cli("gw", "host", A_IP), "apply0": self.run_cli("gw", "apply")}
        from kaskad.gw import addresses
        addresses.collect(self.w.runner, net=False)     # the cache of «Адреса сервера» (the collector keeps it)
        if OUT == "new":
            from kaskad.gw import mip_spread
            mip_spread.spread_exits(self.w.runner, yes=True)
        else:
            with state.locked():
                st = state.load()
                for eid, ip in json.loads(os.environ.get("R15S_SRC") or "{}").items():
                    st["gateway"].setdefault("exits", {}).setdefault(eid, {})["src"] = ip
                state.save(st)
        try:
            model.validate(state.load())
            doc["validate"] = ""
        except Exception as e:                          # noqa: BLE001 — said, not raised: the answer is the test
            doc["validate"] = "%s: %s" % (type(e).__name__, e)
        doc["apply"] = self.run_cli("gw", "apply")
        doc["src"] = dict((eid, rec["src"]) for eid, rec in (state.load()["gateway"].get("exits") or {}).items()
                          if isinstance(rec, dict) and rec.get("src"))
        doc["snat"] = [r for r in chain("gw_post") if "snat to" in r]
        cfg = json.load(open(xray.config_path(), encoding="utf-8"))
        doc["send"] = dict((o.get("tag"), o["sendThrough"]) for o in cfg["outbounds"] if o.get("sendThrough"))
        with open(os.path.join(IO, OUT + ".json"), "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, sort_keys=True, indent=1)


if __name__ == "__main__":
    unittest.main()
