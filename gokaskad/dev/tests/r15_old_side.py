"""3.3.7.15 F11, the rollback (the owner's rule 24.10: «старые данные → новая» и откат в каждом выпуске): the side that
runs in a tree of its own — the RELEASED 3.3.7.13 / 3.3.7.14 (``tests/test_r15_in6_rollback.py`` copies this file into
the trees of bc541e29 and fc5ef683) or this version (the same file, this tree). ``R15_IO`` — the folder of the exchange,
``R15_OUT`` — the name of the answer, ``R15_RULE`` — the rule of gw_pre6 this version draws (the old trees put it in
the live table as this version left it before the rollback).

The fake node (the scenario of GwCase: wg, awg2, the forward of 443, VLESS + the leg kawg2) applied, then:

* ``null`` — ``in6`` not set: the ip6 kaskad6 part of ``render_full``, the script of ``apply_ip6`` (the repair of the
  watchdog), the ip6 lines of ``update_lines`` and the kaskad6 part of the boot file kaskad.nft;
* ``in6 = {"*": [2001:db8:10::5]}`` written into state.json the way this version writes it: ``model.validate``,
  ``gw apply``, the subscription of a user; IPv6 in ``ip kaskad``; a live gw_pre6 (as this version left it) after an
  apply (``update_lines``), in the boot file and after ``apply_ip6``; ``in6`` still in state.json after all of it.

Only what 3.3.7.13 has is used. Not a test of this tree: no ``test_`` in the name, skipped without ``R15_OUT``."""

import ipaddress
import json
import os
import re
import unittest
from unittest import mock

from kaskad import cli, nft, paths, state
from kaskad.gw import model, net
from tests import fakes_nft
from tests.fakes_nft import F443
from tests.gwcase import GwCase

IO = os.environ.get("R15_IO", "")
OUT = os.environ.get("R15_OUT", "")
RULE = os.environ.get("R15_RULE", "")
X6 = "2001:db8:10::5"
PRE6_RX = (r'^iifname != "lo" iifname != "([\w*.-]+)" ct direction original fib daddr type local meta l4proto \. th '
           r'dport \{ ((?:(?:tcp|udp) \. \d+(?:-\d+)?(?:, )?)+) \}(?: ip6 daddr != \{ ((?:[0-9a-f:]+(?:, )?)+) \})? '
           r'drop$')


def _teach_the_fake():
    """The fake nft of an old tree knows no rule of gw_pre6 (it takes only the rules it can parse): the one of this
    version is taught to it here, as tests/fakes_nft.py of this tree has it."""
    if any(rx.pattern == PRE6_RX for rx, _ in fakes_nft._GW_RULES):
        return
    m, iif, l4 = fakes_nft._m, fakes_nft._IIF, fakes_nft._L4

    def fn(g):
        ports = [{"concat": [pr, ({"range": [int(lo), int(hi or lo)]} if hi else int(lo))]}
                 for pr, lo, hi in re.findall(r"(tcp|udp) \. (\d+)(?:-(\d+))?", g[1])]
        return ([m(iif, "lo", "!="), m(iif, g[0], "!="), m({"ct": {"key": "direction"}}, "original"),
                 m({"fib": {"result": "type", "flags": ["daddr"]}}, "local"),
                 m({"concat": [l4, {"payload": {"protocol": "th", "field": "dport"}}]}, {"set": ports})]
                + ([m({"payload": {"protocol": "ip6", "field": "daddr"}}, {"set": g[2].split(", ")}, "!=")]
                   if g[2] else []) + [{"drop": None}], [])
    fakes_nft._GW_RULES.append((re.compile(PRE6_RX), fn))


def part6(text):
    i = (text or "").find("table ip6 kaskad6 {")
    return text[i:] if i >= 0 else ""


def _doc_addresses(case):
    """The fake node lives on documentation addresses (2001:db8::/32 is not global): this version takes them as the
    external IPv6 of the server, as tests/test_r15_addr6.doc6 does (3.3.7.13/14 have no ``is_public6``)."""
    from kaskad.gw import addresses
    if not hasattr(addresses, "is_public6"):
        return
    doc = ipaddress.IPv6Network("2001:db8::/32")
    p = mock.patch.object(addresses, "is_public6",
                          lambda ip: addresses._v6(ip) is not None and (addresses._v6(ip).is_global or
                                                                        addresses._v6(ip) in doc))
    p.start()
    case.addCleanup(p.stop)


class Side(GwCase):
    def pre6(self):
        t = self.w.nft.tables6.get("kaskad6")
        if not t or "gw_pre6" not in t["chains"]:
            return None
        return [r["text"] for r in t["chains"]["gw_pre6"]["rules"]]

    def apply(self):
        try:
            return cli.main(["gw", "apply"])
        except SystemExit as e:
            return e.code

    def texts(self):
        st = state.load()
        seen = []
        run = self.w.runner.run

        def rec(argv, input=None, **kw):
            seen.append(input)
            return run(argv, input=input, **kw)
        self.w.runner.run = rec
        try:
            net.apply_ip6(self.w.runner, st)
        finally:
            self.w.runner.run = run
        return {"boot6": part6(nft.render_full(st)), "apply6": [x for x in seen if x],
                "upd6": [x for x in net.update_lines(st, nft.read_live(self.w.runner)) if "ip6" in x],
                "file6": part6(open(paths.nft_file(), encoding="utf-8").read())}

    def sub(self):
        from kaskad.panel import userpage
        got = userpage.build_sub({"id": "u-00000001"}, self.w.runner)
        return got[1] if got else None

    def test_side(self):
        if not OUT:
            raise unittest.SkipTest("the side of tests/test_r15_in6_rollback.py")
        _teach_the_fake()
        _doc_addresses(self)
        self.scenario(forwards=[F443])
        self.w.leg(2)
        doc = {"apply0": self.apply()}
        doc["null"] = self.texts()
        doc["sub0"] = self.sub()
        with state.locked():
            st = state.load()
            st["gateway"].setdefault("addresses", {})["in6"] = {"*": [X6]}
            state.save(st)
        st = state.load()
        try:
            model.validate(st)
            doc["validate"] = ""
        except Exception as e:                          # noqa: BLE001 — said, not raised: the answer is the test
            doc["validate"] = "%s: %s" % (type(e).__name__, e)
        doc["apply1"] = self.apply()
        doc["sub1"] = self.sub()
        full = nft.render_full(state.load())
        doc["ip4_v6"] = "2001:db8" in full[:full.find("table ip6 kaskad6 {")] or any(
            "2001:db8" in r["text"] for t in self.w.nft.tables.values() for c in t["chains"].values() for r in c["rules"])
        doc["rule"] = (net.pre6_rules(state.load()) or [""])[0] if hasattr(net, "pre6_rules") else ""
        if self.pre6() is None and RULE:                # this version left it before the rollback
            self.w.nft.run_script("add chain ip6 kaskad6 gw_pre6 { type filter hook prerouting priority -150; policy "
                                  "accept; }\nadd rule ip6 kaskad6 gw_pre6 %s\n" % RULE, True)
        doc["pre6_live"] = self.pre6()
        doc["apply2"] = self.apply()
        doc["pre6_after_update"] = self.pre6()
        doc["pre6_in_file"] = "gw_pre6" in open(paths.nft_file(), encoding="utf-8").read()
        net.apply_ip6(self.w.runner, state.load())
        doc["pre6_after_apply_ip6"] = self.pre6()
        doc["ip6_problems"] = len(net.ip6_problems(state.load(), self.w.runner))
        doc["in6_kept"] = (state.load()["gateway"].get("addresses") or {}).get("in6")
        with open(os.path.join(IO, OUT + ".json"), "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, sort_keys=True, indent=1)


if __name__ == "__main__":
    unittest.main()
