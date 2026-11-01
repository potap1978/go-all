"""The guard of the roles (SPEC-stage1-exits §3.1 «Роль выхода»; stage 1, T1 — the frame, T15 adds to it): a Russian
exit (``role = ru``, the lines of upstream-ru.txt) never gets into gw_route by the pool, gw-all, gw-vless, the
observatory, the pool candidates of the foreign group, the lists of the exits (panel, CLI, MCP, bot) and the summary —
whatever the data. Until T15 the pool file of Russia is not read: here the pool of T15 is simulated (``mixed``) and
``exits.pool`` is replaced by one that gives the Russian rows only to those who ask ``roles``.

T15 adds: bot-bal of «Прокси для API Telegram» (the parser's config), the route choices of the panel, bot, SSH, CLI
and MCP, the labels of the scheme, assign of the groups (T2/T4)."""

import copy
import inspect
import os
import re
import unittest
from unittest import mock

from kaskad import secrets
from kaskad.gw import exits, model, ops, xray
from tests.fakes_gw import register_dummies
from tests.gwcase import GwCase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RU_IDS = ("e-abcdef0001", "e-abcdef0002")          # valid ids (model._EXIT_ID): a route «exit:<id>» on them is kept


def russian(rows, start):
    """Copies of ``rows`` as exits of upstream-ru.txt: other ids, role "ru", their tags «gr-…», lines after ``start``."""
    out = []
    for i, (e, rid) in enumerate(zip(rows, RU_IDS)):
        r = copy.deepcopy(e)
        idx = start + i + 1
        r.update(id=rid, index=idx, role="ru", tag=exits.tag(rid, "ru"), line="")
        if r["kind"] == "awg":
            r.update(mark=exits.AWG_MARK_BASE + idx, iface="kawg%d" % idx, table=exits.AWG_MARK_BASE + idx)
        else:
            r.update(mark=exits.VLESS_MARK_BASE + idx)
        if isinstance(r.get("outbound"), dict):
            r["outbound"]["tag"] = r["tag"]
        out.append(r)
    return out


def selects(selector, tag):
    return any(tag.startswith(s) for s in selector or [])


class RoleGuardTest(GwCase):
    def setUp(self):
        super().setUp()
        self.st = self.scenario(save=True)
        self.abroad = exits.pool(self.w.runner)
        self.ru = russian(self.abroad, len(self.abroad))
        self.mixed = self.abroad + self.ru
        self.alive = set(e["id"] for e in self.mixed)
        self.ru_marks = set(e["mark"] for e in self.ru)
        self.ru_tags = set(e["tag"] for e in self.ru)

    def fake_pool(self):
        mixed = self.mixed

        def pool(runner=None, parser=None, roles=("abroad",)):
            return [copy.deepcopy(e) for e in mixed if exits.role_of(e) in roles]
        return mock.patch.object(exits, "pool", pool)

    def test_pool_gives_the_foreign_exits_by_default(self):
        self.assertEqual(inspect.signature(exits.pool).parameters["roles"].default, ("abroad",))
        self.assertTrue(all(e["role"] == "abroad" for e in self.abroad))
        self.assertEqual(exits.pool(self.w.runner, roles=("ru",)), [])         # T15 reads upstream-ru.txt
        self.assertEqual([e["tag"] for e in self.ru], ["gr-" + i for i in RU_IDS])

    def test_pool_candidates_and_gw_route(self):
        st = self.st
        self.assertFalse(set(e["id"] for e in exits.usable(st, self.mixed, self.alive)) & set(RU_IDS))
        devices = [{"id": "d-%08x" % i, "route": None, "user": None} for i in range(64)]
        for d in devices:
            t = exits.target(st, d, self.mixed, self.alive)
            self.assertNotIn(t["exit"], RU_IDS)
            self.assertNotIn(t["mark"], self.ru_marks)
            n = exits.now_of(st, d, self.mixed, self.alive)
            self.assertFalse(set(n["exits"]) & set(RU_IDS))
        self.assertFalse(set(exits.marks(st, self.mixed, self.alive).values()) & self.ru_marks)

    def test_xray_balancers_and_observatory(self):
        cfg = xray.render(self.st, secrets.load(), self.mixed)
        bal = dict((b["tag"], b) for b in cfg["routing"].get("balancers") or [])
        for name, b in bal.items():
            for tag in self.ru_tags:
                self.assertFalse(selects(b["selector"], tag), "%s selects %s" % (name, tag))
        # T-q круг 3 (З-2, решение координатора): the observatory — the foreign exits only, a Russian xray exit too
        # (its own probe through probe-<id>); the balancers above — never
        sel = (cfg.get("observatory") or {}).get("subjectSelector")
        self.assertEqual(sel, ["gx-"])
        for tag in self.ru_tags:
            self.assertFalse(selects(sel, tag))
        # the only rule naming a Russian outbound is its own TPROXY inbound (T15: through its group) — and, stage 1
        # (SPEC-stage1-exits §1.3: «без влияния на маршрут»; §5.1, §2.7), the inbound of its probes: one rule ``px-``
        for r in cfg["routing"]["rules"]:
            if r.get("outboundTag") in self.ru_tags:
                eid = r["outboundTag"][3:]
                self.assertEqual(r["inboundTag"], [exits.tproxy_tag(eid), xray.probe_tag(eid)])

    def test_route_to_a_russian_exit_on_a_foreign_inbound(self):
        """REV-s1-t1 С-2: «exit:<ru>» on a SOCKS5 inbound of xray is unfit, as in the core (fail: block or direct); no rule
        names «gx-<ru>», and every tag a rule or a balancer names exists. The test checks the tags itself: ``xray -test``
        (v26.3.27) does NOT refuse a rule to a missing tag — such a rule only closes its connections at run time
        (REV-s1-t1-2 П-1)."""
        register_dummies(("socks5", {"proto": "tcp", "default_port": 1080, "ifaces": [],
                                     "xray_inbound": {"tag": "socks-in", "listen": "0.0.0.0", "port": 1080,
                                                      "protocol": "socks", "settings": {"auth": "password"}}}))
        st = self.st
        g = st["gateway"]
        g["inbounds"]["socks5"] = {"enabled": True, "port": 1080}
        for d in g["devices"]:
            d["access"].append("socks5")
        foreign = next(e for e in self.abroad if exits.is_xray(e))
        routes = [{"via": "exit:" + RU_IDS[0], "fail": "block"}, {"via": "exit:" + RU_IDS[1], "fail": "direct"},
                  {"via": "exit:" + foreign["id"], "fail": "block"}]
        for d, r in zip(g["devices"], routes):
            d["route"] = r
            self.assertEqual(model.route_of(st, d)["via"], r["via"])          # the id is valid: the route is kept
        dec = exits.decision(st, pool_exits=self.mixed, alive=self.alive)    # the same exits alive as the core below
        cfg = xray.render(st, secrets.load(), self.mixed, (), dec)
        outs = set(o["tag"] for o in cfg["outbounds"])
        outs |= set(x for x in ((cfg.get("metrics") or {}).get("tag"), (cfg.get("api") or {}).get("tag")) if x)
        bals = dict((b["tag"], b) for b in cfg["routing"].get("balancers") or [])
        for r in cfg["routing"]["rules"]:
            if "outboundTag" in r:
                self.assertIn(r["outboundTag"], outs, r)
            if "balancerTag" in r:
                self.assertIn(r["balancerTag"], bals, r)
        for b in bals.values():
            self.assertIn(b["fallbackTag"], outs, b)
        from tests import realbin
        real = realbin.xray_check(cfg)                       # the real xray -test, when it is here (tools/fetch_test_bins.sh)
        if real is not None:
            self.assertTrue(real[0], real[1][-1500:])
        names = set(r.get("outboundTag") for r in cfg["routing"]["rules"]) | set(bals)
        for rid in RU_IDS:
            self.assertNotIn("gx-" + rid, names)
            self.assertFalse(any(("gx-" + rid) in b["selector"] or ("gr-" + rid) in b["selector"] for b in bals.values()))
        users = dict((r["user"][0], r) for r in cfg["routing"]["rules"] if r.get("inboundTag") == ["socks-in"]
                     and r.get("user"))
        devs = g["devices"]
        got = [users[d["login"]] for d in devs[:3]]
        # the core: fail_mark of the route; stage 1 (§1.3, §2.2): the block of the last step in xray is ``fail-block``
        # (no restart), «block» stays the block by choice
        self.assertEqual(got[0].get("outboundTag"), xray.FAIL_BLOCK)
        self.assertEqual(got[1].get("outboundTag"), "direct")
        self.assertEqual(got[2].get("outboundTag"), foreign["tag"])          # the record's own tag
        # the core says the same: the Russian exit is not the device's exit, the mark is the route's fail
        for d in devs[:2]:
            t = exits.target(st, d, self.mixed, self.alive)
            self.assertIsNone(t["exit"])
            self.assertEqual(t["mark"], exits.fail_mark(model.route_of(st, d)))
            self.assertEqual(users[d["login"]].get("outboundTag"), t["xray"]["outboundTag"])     # one answer

    def test_lists_and_summary(self):
        from kaskad import summary
        from kaskad.panel import api
        with self.fake_pool():
            rows = ops.exits_view(self.w.runner, self.st)                  # CLI gw exits, MCP exits_list, the bot
            self.assertTrue(rows)
            self.assertFalse(set(r["id"] for r in rows) & set(RU_IDS))
            prow, err = api._exit_rows(self.st)                            # the panel's «Выходы»
            self.assertIsNone(err)
            self.assertFalse(set(r["id"] for r in prow) & set(RU_IDS))
            doc, srows = summary._exits(self.st, None)                     # «Проверка сервера»
            self.assertEqual(doc["total"], len(self.abroad))
            self.assertFalse(set(r["id"] for r in srows or []) & set(RU_IDS))


class NoTagMadeByHandTest(unittest.TestCase):
    """The xray outbound of an exit is made by ``exits.tag`` only: «gx-» + id anywhere else would put a Russian exit
    under gw-all and the observatory (their selector is the prefix «gx-»)."""

    # k3-select: dirs=lib,bin
    # k3-select: files=install.sh
    PATTERNS = (r"""["']gx-["']\s*\+""", r"""["']gx-%s["']""", r"""f["']gx-\{""", r"""["']gx-["']\s*\.\s*concat""",
                r"""["']gx-\{\}["']\s*\.format""")

    def files(self):
        """lib/, bin/ and install.sh — walked by hand (not git grep: the test runs outside a git copy too, REV П-1)."""
        yield os.path.join(ROOT, "install.sh")
        for top in ("lib", "bin"):
            for d, dirs, names in os.walk(os.path.join(ROOT, top)):
                dirs[:] = [x for x in dirs if x != "__pycache__"]
                for n in names:
                    if n.endswith((".py", ".js", ".sh", ".html")) or "." not in n:
                        yield os.path.join(d, n)

    def test_no_gx_concatenation(self):
        rx = re.compile("|".join(self.PATTERNS))
        found = []
        for path in self.files():
            try:
                with open(path, encoding="utf-8") as f:
                    for i, line in enumerate(f, 1):
                        if rx.search(line):
                            found.append("%s:%d: %s" % (os.path.relpath(path, ROOT), i, line.strip()))
            except (OSError, UnicodeDecodeError):
                continue
        self.assertEqual(found, [], "build the tag with exits.tag(eid, role):\n" + "\n".join(found))
        with open(os.path.join(ROOT, "lib", "kaskad", "gw", "exits.py"), encoding="utf-8") as f:
            self.assertTrue(re.search(r'_TAG_PREFIX = \{"abroad": "gx-", "ru": "gr-"\}', f.read()))


if __name__ == "__main__":
    unittest.main()
