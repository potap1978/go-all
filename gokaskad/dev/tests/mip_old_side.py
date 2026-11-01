"""PLAN-multi-ip Ш1–Ш2, the migration (the owner's rule 24.10): the side that runs in a tree of its own — the RELEASED
3.3.7.12 (``tests/test_mip_migration.py`` copies this file into the tree of 8582b624) or this version (the same file,
this tree). Modes (``MIP_MODE``; ``MIP_IO`` — the folder of the exchange; ``MIP_ROOT`` — the fake root to start from,
else the scenario of the gateway; ``MIP_OUT`` — the name of the answer):

* ``render`` — the gateway of the fake node applied from scratch (``ops._apply``) and what it made: the nft scripts it
  applied and the tables, xray.json, the files of the legs (kawg*), the addresses the open mode of the panel listens on
  (eth0 with a second address, a private second interface, Docker) — written as ``<MIP_OUT>.json``, the root as
  ``<MIP_OUT>-root``;
* ``mark`` (this version only) — the root with the new keys: an address added by hand (``gw addresses add``) and the
  cache of «Адреса сервера»; written as ``<MIP_OUT>-root``;
* ``mark34`` (this version only) — the root with the keys of Ш3/Ш4 set through their core (``gw/mip.py``): the
  inbounds take connections on the address for clients only (``addresses.in``), an xray exit and an AmneziaWG exit
  go out from the second address (``exits[id].src``) — applied, so kaskad.nft and xray.json hold the new rules;
* ``export`` — the state.json a copy of the node holds (``backup.collect``) as ``<MIP_OUT>.json``.

Only what 3.3.7.12 has is used in ``render`` and ``export``. Not a test of this tree: no ``test_`` in the name, every case
is skipped without ``MIP_MODE``."""

import json
import os
import re
import shutil
import unittest

from kaskad import paths, secrets, snapshot, state
from tests.gwcase import GwCase

MODE = os.environ.get("MIP_MODE")
IO = os.environ.get("MIP_IO", "")
ROOT_IN = os.environ.get("MIP_ROOT", "")
OUT = os.environ.get("MIP_OUT", "out")
R2_SRC = "85.143.0.30"                  # MIP_EXTRA=r2: the source of the default route, the last address of eth0


def save_root(dst):
    shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(paths.root(), dst, symlinks=True)


def load_root(src):
    root = paths.root()
    for n in os.listdir(root):
        p = os.path.join(root, n)
        if os.path.isdir(p) and not os.path.islink(p):
            shutil.rmtree(p)
        else:
            os.remove(p)
    shutil.copytree(src, root, symlinks=True, dirs_exist_ok=True)
    snapshot.forget()


class Side(GwCase):
    def start(self):
        if ROOT_IN:
            self.ads = self.dummies()
            load_root(ROOT_IN)
        else:
            self.scenario()
        self.w.ip.links["eth0"]["addrs"].append("203.0.113.77/32")         # a second address of the hoster
        self.w.ip.add_link("eth1", "ether", ["10.10.0.5/24"])              # its private network
        self.w.ip.add_link("docker0", "bridge", ["172.17.0.1/16"])
        if os.environ.get("MIP_EXTRA"):
            # the fake node lives on documentation addresses (203.0.113.x — not external for «Адреса сервера»): two
            # external ones for the runs of Ш3/Ш4
            self.w.ip.links["eth0"]["addrs"] += ["85.143.0.20/32", "85.143.0.30/32"]
        if os.environ.get("MIP_EXTRA") == "r2":
            self.route_from(R2_SRC)
        self.legs_conf()

    def route_from(self, src):
        """Р2 of T-mip-lab (REV-mip-s34 Б-1): the default route names ``src`` — not the first address of eth0 (the fake
        answers ``ip route get`` with the first one): every answer of ``ip [-4] route get`` without ``iif`` says ``src``."""
        ip = self.w.ip

        def answer(argv, input):
            rc, out, err = ip(argv, input)
            if "route" in argv and "get" in argv and "iif" not in argv and rc == 0:
                out = re.sub(r" src \S+", " src " + src, out)
            return rc, out, err
        self.w.runner._handlers.insert(0, (["ip"], answer))

    def legs_conf(self):
        """The config of each AmneziaWG leg as install.sh writes it (``Endpoint`` = the IP it checked): the SNAT of a
        leg with its own address (Ш4) reads it there. Both versions see the same files."""
        from kaskad.gw import exits
        for n, e in enumerate(exits.pool(self.w.runner, roles=exits.ROLES)):
            if e.get("kind") != "awg" or not e.get("iface"):
                continue
            conf = os.path.join(paths.egress_dir(), "awg", ".%s.conf" % e["iface"])
            if not os.path.exists(conf):
                os.makedirs(os.path.dirname(conf), exist_ok=True)
                with open(conf, "w", encoding="utf-8") as f:
                    f.write("[Interface]\nPrivateKey = x\n\n[Peer]\nPublicKey = y\nEndpoint = 198.51.100.%d:51820\n"
                            % (n + 7))

    def test_side(self):
        if not MODE:
            raise unittest.SkipTest("the side of tests/test_mip_migration.py")
        self.start()
        if MODE == "render":
            self.render()
        elif MODE == "mark":
            from kaskad.gw import addresses
            addresses.add(self.w.runner, "93.184.216.34")
            addresses.collect(self.w.runner)
        elif MODE == "mark34":
            self.mark34()
        elif MODE == "export":
            from kaskad import backup
            files = backup.collect(self.w.runner, backup.PARTS, state.load())
            self.put(json.loads(files[backup.F_STATE]))
        save_root(os.path.join(IO, OUT + "-root"))

    def mark34(self):
        from kaskad.gw import addresses, exits, mip
        addresses.collect(self.w.runner)
        mip.set_in(self.w.runner, ["85.143.0.20"], env={})
        pool = exits.pool(self.w.runner, roles=exits.ROLES)
        ids = [e["id"] for e in pool if e.get("kind") == "xray"][:1] + [e["id"] for e in pool if e.get("kind") == "awg"][:1]
        assert len(ids) == 2, pool
        mip.set_src(self.w.runner, ids, ip="85.143.0.30")

    def put(self, obj):
        with open(os.path.join(IO, OUT + ".json"), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, sort_keys=True, indent=1)

    def render(self):
        from kaskad.gw import ops, xray
        from kaskad.panel import conf
        r2 = None
        if os.environ.get("MIP_EXTRA") == "r2":
            r2 = {"cache": False, "src": self.w.runner.run(["ip", "-4", "route", "get", "1.1.1.1"]).out.split(" src ")[1]
                  .split()[0]}
            try:                                        # this version: the cache of «Адреса сервера» as the collector
                from kaskad.gw import addresses         # writes it (3.3.7.12 has none)
            except ImportError:
                addresses = None
            if addresses is not None:
                addresses.collect(self.w.runner)
                r2["cache"] = addresses.load_cache() is not None
        ops._apply(state.load(), secrets.load(), self.w.runner, probe=False)
        nft = self.w.nft
        tables = {}
        for fam, ts in (("ip", nft.tables), ("ip6", nft.tables6)):
            for name in ts:
                tables["%s %s" % (fam, name)] = json.loads(nft.json_table(name, fam))
        legs = {}
        for d, _, names in os.walk(paths.root()):
            for n in names:
                if "kawg" in n:
                    p = os.path.join(d, n)
                    with open(p, encoding="utf-8", errors="replace") as f:
                        legs[os.path.relpath(p, paths.root())] = f.read()
        xr = xray.config_path()
        doc = {"nft_applied": list(nft.applied), "nft_tables": tables,
               "xray": open(xr, encoding="utf-8").read() if os.path.exists(xr) else None, "legs": legs,
               "listen": conf.listen_addresses(self.w.runner, "203.0.113.10")}
        if r2 is not None:
            doc["r2"] = r2
        self.put(doc)


if __name__ == "__main__":
    unittest.main()
