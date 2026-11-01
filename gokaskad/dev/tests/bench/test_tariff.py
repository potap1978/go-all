"""The core ``bench/tariff.py`` (k3/handoff/SPEC-node-score.md NS-D34, решение владельца 23.10) — offline, without the
rest of Kaskad (process/BLOCKS.md п. 6): only the port (the health rule «port») and the traffic (the price of the
schedule), their types and ranges, the source of every value, the fields of the former tariff read past silently,
the version and the code of a change, the args of a «new base» (NS-S17)."""

import os
import unittest

from kaskad.bench import spool
from kaskad.bench import tariff as T


def vals(**kw):
    return dict((f, {"v": v, "src": "user"}) for f, v in kw.items())


# a tariff stored before 23.10 (C1): the seven fields that are gone and the two that stay
OLD = {"v": 1, "at": 1792000000, "mid": "0" * 32, "vals": {
    "vcpu": {"v": 2, "src": "user"}, "ram_gb": {"v": 2, "src": "user"}, "disk_type": {"v": "nvme", "src": "user"},
    "price": {"v": 5.5, "src": "user"}, "currency": {"v": "EUR", "src": "user"},
    "hoster": {"v": "Aeza", "src": "user"}, "name": {"v": "MSK-1", "src": "catalog"},
    "port_mbit": {"v": 1000, "src": "user"}, "traffic_gb": {"v": 2000, "src": "user"}}}


class ValueTest(unittest.TestCase):
    def ok(self, field, x, want):
        v, e = T.parse_value(field, x)
        self.assertIsNone(e, (field, x, e))
        self.assertEqual(v, want)
        self.assertIs(type(v), type(want))

    def bad(self, field, x, code):
        v, e = T.parse_value(field, x)
        self.assertIsNone(v)
        self.assertEqual(e["code"], code, (field, x, e))
        self.assertEqual(e["msg"]["key"], "tariff.err." + code)

    def test_ranges_at_their_edges(self):
        self.ok("port_mbit", 1, 1)
        self.ok("port_mbit", "100000", 100000)
        self.ok("port_mbit", 2.0, 2)
        self.bad("port_mbit", 100001, "range")
        self.bad("port_mbit", 0, "range")
        self.bad("port_mbit", 1.5, "int")
        self.bad("port_mbit", "1,5", "int")
        self.ok("traffic_gb", 1000, 1000)
        self.ok("traffic_gb", "1,5", 1.5)
        self.ok("traffic_gb", 1.999, 2)
        self.ok("traffic_gb", 10000000, 10000000)
        self.ok("traffic_gb", " Unlimited ", "unlimited")
        self.bad("traffic_gb", 0.5, "range")
        self.bad("traffic_gb", 10000001, "range")
        self.bad("port_mbit", "unlimited", "number")                   # «unlimited» — only the traffic

    def test_not_numbers(self):
        for f in T.FIELDS:
            for x in (True, False, float("nan"), float("inf"), "1e3", "0x10", "-1", "1 000", "１０", [], {}, "1.2.3",
                      "9" * 30):
                self.bad(f, x, "number")

    def test_clear_and_only_two_fields(self):
        self.assertEqual(T.FIELDS, ("port_mbit", "traffic_gb"))
        self.assertEqual(T.parse_value("port_mbit", ""), (None, None))     # empty — «clear»
        self.assertEqual(T.parse_value("traffic_gb", "   "), (None, None))
        self.assertEqual(T.parse_value("port_mbit", None), (None, None))

    def test_a_field_of_the_former_tariff_is_unknown(self):
        """NS-D34: cores, memory, disk, price, currency, hoster, name — no longer typed in: ``unknown``, as any other."""
        for f, x in (("vcpu", 2), ("ram_gb", 2), ("disk_type", "nvme"), ("price", 5), ("currency", "EUR"),
                     ("hoster", "Aeza"), ("name", "MSK-1"), ("ports", 1)):
            self.bad(f, x, "unknown")
            new, errs = T.merge(vals(port_mbit=1000), {f: x})
            self.assertEqual([(e["field"], e["code"]) for e in errs], [(f, "unknown")])
            self.assertEqual(new, vals(port_mbit=1000))                 # nothing changes
            self.assertEqual([(e["field"], e["code"]) for e in T.merge({}, {}, [f])[1]], [(f, "unknown")])


class StoreTest(unittest.TestCase):
    def test_merge_sets_clears_and_refuses_whole(self):
        old = vals(traffic_gb=1000)
        new, errs = T.merge(old, {"port_mbit": "1000", "traffic_gb": None})
        self.assertEqual(errs, [])
        self.assertEqual(new, vals(port_mbit=1000))
        new, errs = T.merge(old, {"port_mbit": 0, "traffic_gb": 2000})
        self.assertEqual([e["field"] for e in errs], ["port_mbit"])
        self.assertEqual(new, old)                                  # one bad value — nothing changes
        self.assertEqual(T.merge(vals(port_mbit=1, traffic_gb=1), {}, ["all"]), ({}, []))
        self.assertEqual(T.merge(old, {}, ["nope"])[1][0]["code"], "unknown")
        self.assertEqual(T.merge(old, {"nope": 1})[1][0]["code"], "unknown")
        for evil in ("a\x1b[2Jb", "x" * 40, 5, "Port"):                 # a person's key is never said back as is
            self.assertEqual(T.merge(old, {evil: 1})[1][0]["field"], "?")
            self.assertEqual(T.merge(old, {}, [evil])[1][0]["field"], "?")
            self.assertEqual(T.load({"vals": {evil: {"v": 1, "src": "user"}}})[1], ["?"])

    def test_a_typed_value_is_always_the_user(self):
        new, _ = T.merge({}, {"port_mbit": 100})
        self.assertEqual(new["port_mbit"]["src"], "user")

    def test_load_checks_every_field_and_drops_only_the_bad(self):
        raw = {"v": 1, "at": 1792000000, "vals": {
            "port_mbit": {"v": "1000", "src": "user"}, "traffic_gb": {"v": 500, "src": "inventory"},
            "evil": {"v": 1, "src": "user"}}}
        doc, dropped = T.load(raw)
        self.assertEqual(doc["vals"], {})
        self.assertEqual(sorted(dropped), ["evil", "port_mbit", "traffic_gb"])
        self.assertEqual(doc["at"], 1792000000)
        doc, dropped = T.load({"vals": {"port_mbit": {"v": 500, "src": "catalog"},
                                        "traffic_gb": {"v": "Unlimited", "src": "user"}}})
        self.assertEqual((doc["vals"], dropped), ({"port_mbit": {"v": 500, "src": "catalog"}}, ["traffic_gb"]))
        self.assertEqual(T.load({"vals": vals(traffic_gb="unlimited")})[0]["vals"], vals(traffic_gb="unlimited"))
        for bad in (None, [], "x", {"vals": []}, {"vals": None}, {"at": True}):
            doc, _ = T.load(bad)
            self.assertEqual(doc["vals"], {})
            self.assertIsNone(doc["at"])
        self.assertEqual(T.load(None)[1], [])

    def test_the_fields_of_the_former_tariff_are_read_past_silently(self):
        """NS-D34: a tariff stored before 23.10 — its seven gone fields are not values and not «dropped» (even a broken
        one: it is not a field any more); the port and the traffic stay and reach the rule and the price."""
        doc, dropped = T.load(OLD)
        self.assertEqual(dropped, [])
        self.assertEqual(doc["vals"], vals(port_mbit=1000, traffic_gb=2000))
        self.assertEqual((T.port_mbit(doc), T.traffic_limit_gb(doc)), (1000, 2000))
        broken = {"vals": {"hoster": {"v": "a\x1bb", "src": "inventory"}, "price": "x",
                           "port_mbit": {"v": 500, "src": "user"}}}
        self.assertEqual(T.load(broken), ({"v": 1, "at": None, "vals": vals(port_mbit=500)}, []))
        self.assertEqual(T.load({"vals": {"vcpu": {"v": 2, "src": "user"}}}), ({"v": 1, "at": None, "vals": {}}, []))
        # the version counts only what stays: the panel's rev of a stored old tariff is the rev of port + traffic
        self.assertEqual(T.view(doc)["rev"], T.rev(vals(port_mbit=1000, traffic_gb=2000)))

    def test_rev_and_code(self):
        a, b = vals(port_mbit=100), vals(port_mbit=200)
        self.assertEqual(T.rev(a), T.rev(vals(port_mbit=100)))
        self.assertNotEqual(T.rev(a), T.rev(b))
        self.assertNotEqual(T.rev(a), T.rev({"port_mbit": {"v": 100, "src": "catalog"}}))
        c = T.code(a, b)
        self.assertRegex(c, r"^[0-9a-f]{10}$")
        self.assertEqual(c, T.code(vals(port_mbit=100), vals(port_mbit=200)))
        self.assertNotEqual(c, T.code(a, vals(port_mbit=300)))       # another change
        self.assertNotEqual(c, T.code(vals(port_mbit=50), b))         # the same change over other stored values
        self.assertNotEqual(T.code(a, b), T.rev(b))

    def test_new_base_only_for_the_port(self):
        self.assertIsNone(T.epoch_args({}, vals(traffic_gb=1000)))                 # the traffic — no new base
        self.assertIsNone(T.epoch_args(vals(port_mbit=1000), vals(port_mbit=1000, traffic_gb="unlimited")))
        a = T.epoch_args({}, vals(port_mbit=1000, traffic_gb=1000))
        self.assertEqual(a, {"port_mbit": 1000, "fields": 1})
        self.assertEqual(spool.args_strict(a, "t"), a)              # the queue file takes them as they are
        # REV-ns-c1 П-4 (решение координатора): a value cleared, or the same value from another source, is no new base
        self.assertIsNone(T.epoch_args(vals(port_mbit=1000), {}))
        self.assertIsNone(T.epoch_args(vals(port_mbit=1000), {"port_mbit": {"v": 1000, "src": "catalog"}}))
        self.assertEqual(T.epoch_args(vals(port_mbit=1000), vals(port_mbit=500)), {"port_mbit": 500, "fields": 1})
        self.assertEqual(T.changed(vals(traffic_gb=2), vals(traffic_gb=2, port_mbit=10)), ["port_mbit"])


class ViewTest(unittest.TestCase):
    def test_the_view_says_the_values_only(self):
        """No hint of the inventory, no «counted / of / complete» of the comparison with the tariff (C2 is gone)."""
        v = T.view(T.load(None)[0])
        self.assertEqual(set(v), {"v", "at", "fields", "rev"})
        self.assertEqual(v["fields"], {"port_mbit": {"v": None, "src": None}, "traffic_gb": {"v": None, "src": None}})
        doc = {"v": 1, "at": 1, "vals": vals(port_mbit=1000)}
        v = T.view(doc)
        self.assertEqual(v["fields"]["port_mbit"], {"v": 1000, "src": "user"})
        self.assertEqual(v["rev"], T.rev(doc["vals"]))
        self.assertEqual(v["at"], 1)

    def test_other_blocks_take_numbers(self):
        doc = {"vals": vals(port_mbit=500, traffic_gb="unlimited")}
        self.assertEqual(T.port_mbit(doc), 500)
        self.assertIsNone(T.traffic_limit_gb(doc))
        self.assertEqual(T.traffic_limit_gb({"vals": vals(traffic_gb=1000)}), 1000)
        self.assertIsNone(T.port_mbit({"vals": {"port_mbit": {"v": 1000, "src": "inventory"}}}))
        self.assertIsNone(T.port_mbit(None))
        self.assertIsNone(T.traffic_limit_gb(None))


class CoreTest(unittest.TestCase):
    def test_the_core_takes_nothing_of_kaskad(self):
        """process/BLOCKS.md п. 1–3: the standard library only — no gw/panel/site, no file, no clock."""
        import ast
        with open(os.path.join(os.path.dirname(T.__file__), "tariff.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        mods = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                mods.add("." * node.level + (node.module or ""))
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                self.assertNotEqual(node.func.id, "open")
        self.assertEqual(mods, {"hashlib", "json", "math", "re"})


if __name__ == "__main__":
    unittest.main()
