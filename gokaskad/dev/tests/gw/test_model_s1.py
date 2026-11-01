"""The model of the gateway in stage 1 (SPEC-stage1-exits §3.1, §2.2; SPEC-exit-groups §2.5; T2): the route
``group:<gid>``, ``check_route``, every field of ``exit_pref``, ``set_exit`` with the new fields, the defaults of
``gateway.notify``."""

import copy
import os
import unittest

from kaskad import paths, state
from kaskad.errors import USAGE, KaskadError
from kaskad.gw import model, policy as P, rev
from kaskad.i18n import tr
from tests.helpers import RootCase

E1, R1 = "e-0000000001", "e-00000000a1"


def st_with_groups():
    st = state.empty()
    g = model.gw(st)
    g["exit_policy"] = P.default_doc("compat")
    g["exit_policy"]["groups"][0]["tiers"][0]["exits"] = [E1]
    g["exit_policy"]["groups"].append(P.default_group("g-123456", "abroad", "compat", name="Стриминг"))
    g["exit_policy"]["groups"].append(P.default_group("g-abcdef", "ru", "ru_home", name="Дом", exits=[R1]))
    u = model.add_user(st, "Мама")
    d = model.add_device(st, u["id"], "Телефон", access=("wg",))
    return st, u, d


class RouteTest(unittest.TestCase):
    def test_parse_route_group(self):
        self.assertEqual(model.parse_route("group:g-123456"), {"via": "group:g-123456", "fail": "block"})
        self.assertEqual(model.parse_route("group:main", "direct"), {"via": "pool", "fail": "direct"})   # 3.3.3-safe
        for bad in ("group:", "group:G-123456", "group:g-12345", "group:other", "group:main2", "grp:main"):
            with self.assertRaises(KaskadError, msg=bad):
                model.parse_route(bad)
        self.assertEqual(model.FAILS, ("block", "direct"))              # no new values of fail
        with self.assertRaises(KaskadError):
            model.parse_route("pool", "pool")
        self.assertIn("group:", tr("gw_model.err.bad_route", "en", value="x"))

    def test_route_texts_list_every_route(self):
        """The refusal of the model and the «no route» of the panel list every form parse_route takes, both languages
        (fix1: group:<gid> since T2); the CLI help of «gw route» too."""
        from kaskad.gw import cli
        forms = ("pool", "group:<", "exit:<", "direct", "block")
        for key in ("gw_model.err.bad_route", "panel.api.routeMissing"):
            for lang in ("ru", "en"):
                text = tr(key, lang, value="'x'")
                for form in forms:
                    self.assertIn(form, text, (key, lang, form))
        for form in forms:
            self.assertIn(form, cli.__doc__.split("gw route <via>", 1)[1].split("\n", 1)[0], form)

    def test_cli_help_of_the_route_says_every_form_as_the_refusal(self):
        """REV-s1-fix1 З1, П1: the «help» of «via» of the real parser of «gw route» lists every form the model takes
        (VIA_SIMPLE, GROUP_VIA, exit:), with the same «group:<…>»/«exit:<…>» as the refusal of the model."""
        import argparse
        import re
        from kaskad import i18n
        from kaskad.gw import cli

        def sub(parser, name):
            act = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
            return act.choices[name]

        def via_help(ap):
            route = sub(sub(ap, "gw"), "route")                  # «kaskadctl gw route <via>»
            return next(a.help for a in route._actions if a.dest == "via")
        forms = list(model.VIA_SIMPLE) + [model.GROUP_VIA, "exit:"]
        for lang in ("ru", "en"):
            with i18n.use_lang(lang):
                ap = argparse.ArgumentParser()
                cli.register(ap.add_subparsers())
                text = via_help(ap)
                err = tr("gw_model.err.bad_route", lang, value="'x'")
            for form in forms:
                self.assertIn(form, text, (lang, form))
            marks = re.compile(r"(?:group|exit):<[^>]*>")
            self.assertEqual(marks.findall(text), marks.findall(err), lang)
            self.assertEqual(len(marks.findall(text)), 2, lang)

    def test_group_of_route(self):
        self.assertEqual(model.group_of_route({"via": "pool"}), "main")
        self.assertEqual(model.group_of_route({"via": "group:g-123456"}), "g-123456")
        self.assertIsNone(model.group_of_route({"via": "exit:" + E1}))
        self.assertIsNone(model.group_of_route({"via": "direct"}))

    def test_route_to_a_missing_or_russian_group_is_broken(self):
        st, u, d = st_with_groups()
        d["route"] = {"via": "group:g-123456", "fail": "block"}
        self.assertEqual(model.route_of(st, d)["via"], "group:g-123456")
        d["route"] = {"via": "group:g-999999", "fail": "block"}           # deleted on an import, a hand edit
        u["route"] = {"via": "direct", "fail": "block"}
        r = model.route_of(st, d)
        self.assertEqual((r["via"], r["from"]), ("direct", "user"))
        d["route"] = {"via": "group:g-abcdef", "fail": "block"}           # a Russian group is never a route
        self.assertEqual(model.route_of(st, d)["from"], "user")
        st["gateway"].pop("exit_policy")                                  # no policy: only «main» exists
        d["route"] = {"via": "group:g-123456", "fail": "block"}
        self.assertEqual(model.route_of(st, d)["from"], "user")

    def test_check_route(self):
        st, u, d = st_with_groups()
        pool = {"abroad": [E1], "ru": [R1]}
        self.assertEqual(model.check_route(st, {"via": "group:g-123456"}, pool)["via"], "group:g-123456")
        self.assertEqual(model.check_route(st, "group:main", pool)["via"], "pool")
        self.assertEqual(model.check_route(st, {"via": "exit:" + E1, "fail": "direct"}, pool),
                         {"via": "exit:" + E1, "fail": "direct"})
        for route, key in (("group:g-abcdef", "gw_model.err.route_ru_group"), ("exit:" + R1, "gw_model.err.route_ru_exit"),
                           ("group:g-999999", "gw_model.err.route_no_group")):
            with self.assertRaises(KaskadError) as cm:
                model.check_route(st, route, pool)
            self.assertEqual(cm.exception.code, USAGE)
            want = tr(key, group=P.label(P.group(st["gateway"]["exit_policy"], "g-abcdef")) if "ru_group" in key
                      else "g-999999", exit=R1)
            self.assertEqual(str(cm.exception), want)
        # an exit of upstream-ru.txt outside any group: Russian by its file
        st["gateway"]["exit_policy"]["groups"][2]["tiers"][0]["exits"] = []
        with self.assertRaises(KaskadError):
            model.check_route(st, "exit:" + R1, pool)
        self.assertEqual(model.check_route(st, "direct", pool)["via"], "direct")


class RouteFilesTest(RootCase):
    def test_check_route_reads_the_files_of_the_pool(self):
        from kaskad.gw import exits
        line = "vless://aaaaaaaa-2222-3333-4444-555555555555@203.0.113.7:443?type=tcp&security=reality#Moscow"
        p = rev.ru_pool_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(line + "\n")
        eid = exits.exit_id(line)
        st = state.empty()
        with self.assertRaises(KaskadError):
            model.check_route(st, "exit:" + eid)
        self.assertEqual(model.check_route(st, "exit:e-0000000009")["via"], "exit:e-0000000009")


class ExitPrefTest(unittest.TestCase):
    def test_every_field(self):
        st = state.empty()
        self.assertEqual(model.exit_pref(st, E1), {"enabled": True, "name": "", "group": None, "manual": None,
                                                    "manual_why": None, "speed_skip": False, "country": None,
                                                    "quota": None})
        q = {"period": "month", "limit_gb": 500, "reset_day": 1, "warn_pct": 80, "action": "remove"}
        model.set_exit(st, E1, manual="standby", manual_why="sub_confirm", speed_skip=True,
                       country={"expected": "DE", "accepted_at": 1760000000, "notified": ["FI"]}, quota=q,
                       group="main")
        p = model.exit_pref(st, E1)
        self.assertEqual((p["manual"], p["manual_why"], p["speed_skip"], p["country"]["expected"], p["quota"], p["group"]),
                         ("standby", "sub_confirm", True, "DE", q, "main"))
        model.set_exit(st, E1, manual=None)                  # «Вернуть»: the reason goes with the state
        p = model.exit_pref(st, E1)
        self.assertEqual((p["manual"], p["manual_why"]), (None, None))
        model.set_exit(st, E1, quota={"limit_gb": 10})       # the defaults of a quota filled in
        self.assertEqual(model.exit_pref(st, E1)["quota"],
                         {"period": "month", "limit_gb": 10, "reset_day": 1, "warn_pct": 80, "action": "remove"})
        model.set_exit(st, E1, quota=None)
        self.assertIsNone(model.exit_pref(st, E1)["quota"])
        self.assertNotIn("quota", st["gateway"]["exits"][E1])          # nothing written for a default

    def test_set_exit_refuses_and_changes_nothing(self):
        st = state.empty()
        model.set_exit(st, E1, enabled=False)
        before = copy.deepcopy(st)
        for kw in ({"manual": "off"}, {"quota": {"limit_gb": 1, "action": "off"}}, {"speed_skip": "yes"},
                   {"manual_why": "sub_confirm"}, {"country": {"expected": "Deutschland"}}):
            with self.assertRaises(KaskadError) as cm:
                model.set_exit(st, E1, **kw)
            self.assertEqual(cm.exception.code, USAGE)
            self.assertTrue(cm.exception.data["errors"])
            self.assertEqual(st, before, kw)

    def test_old_callers_see_the_same(self):
        st = state.empty()
        model.set_exit(st, E1, enabled=False, name="Вена")
        self.assertEqual(st["gateway"]["exits"][E1], {"enabled": False, "name": "Вена"})
        self.assertFalse(model.exit_pref(st, E1)["enabled"])


class NotifyTest(unittest.TestCase):
    def test_defaults_of_the_pushes(self):
        n = model.notify(state.empty())
        self.assertEqual(n["push"], model.NOTIFY_PUSH)
        self.assertTrue(n["push"]["exit_recovered"])
        self.assertFalse(n["push"]["speedtest_budget"])
        self.assertTrue(n["flap_summary"])
        old = state.empty()
        old["gen"] = 1
        self.assertFalse(model.notify(old)["flap_summary"])
        self.assertNotIn("notify", model.gw(old))           # a read writes nothing
        self.assertNotIn("exit_policy", model.gw(old))      # gw() never fills the policy (§1.3)

    def test_exit_policy_default(self):
        self.assertEqual(model.exit_policy(state.empty())["groups"][0]["preset"], "compat")



# ── REV-s1-t2: the review of T2 ────────────────────────────────────────────

class RouteWriteTest(unittest.TestCase):
    """З1: a route is checked at the write (as 3.3.5 refused an unknown «via»)."""

    def test_every_writer_of_the_model_refuses_a_missing_group(self):
        st, u, d = st_with_groups()
        pool = {"abroad": [E1], "ru": [R1]}
        for call in (lambda: model.set_route(d, "group:g-999999", st=st, pool=pool),
                     lambda: model.set_route(u, "group:g-abcdef", "direct", st=st, pool=pool),
                     lambda: model.set_route(d, "exit:" + R1, st=st, pool=pool),
                     lambda: model.set_default_route(st, "group:g-999999", pool=pool),
                     lambda: model.add_user(st, "Папа", {"via": "group:g-999999", "fail": "block"}),
                     lambda: model.add_device(st, u["id"], "Ноутбук", route="group:g-999999")):
            before = copy.deepcopy(st)
            with self.assertRaises(KaskadError) as cm:
                call()
            self.assertEqual(cm.exception.code, USAGE)
            self.assertEqual(st, before)
        self.assertEqual(model.set_route(d, "group:g-123456", st=st, pool=pool)["via"], "group:g-123456")
        self.assertEqual(model.set_route(d, "group:main", st=st, pool=pool)["via"], "pool")
        self.assertEqual(model.set_default_route(st, "group:g-123456", pool=pool)["via"], "group:g-123456")
        self.assertEqual(model.add_user(st, "Папа", "group:g-123456")["route"]["via"], "group:g-123456")

    def test_every_writer_of_the_product_passes_the_state(self):
        import re
        root = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "lib")
        bad = []
        for base, _, files in os.walk(root):
            for f in files:
                if not f.endswith(".py"):
                    continue
                path = os.path.join(base, f)
                with open(path, encoding="utf-8") as fh:
                    for n, line in enumerate(fh, 1):
                        m = re.search(r"\bmodel\.set_route\((.*)$", line)
                        if m and ", None)" not in m.group(1) and "st=" not in m.group(1):
                            bad.append("%s:%d" % (os.path.relpath(path, root), n))
        self.assertEqual(bad, [])

    def test_the_russian_exit_is_named(self):
        st, u, d = st_with_groups()
        model.set_exit(st, R1, name="Роутер дома")
        with self.assertRaises(KaskadError) as cm:
            model.check_route(st, "exit:" + R1, {"abroad": [E1], "ru": [R1]})
        self.assertIn("Роутер дома", str(cm.exception))


class SetExitNewIdTest(unittest.TestCase):
    def test_a_refused_new_exit_writes_nothing(self):
        st = state.empty()
        model.gw(st)                                           # the defaults of the gateway are not the point
        before = copy.deepcopy(st)
        with self.assertRaises(KaskadError):
            model.set_exit(st, E1, manual="off")
        self.assertEqual(st, before)
        self.assertNotIn(E1, model.gw(st)["exits"])


if __name__ == "__main__":
    unittest.main()
