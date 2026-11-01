"""T7 — the API of the exit policy (SPEC-stage1-exits §7.9, §3.1; SPEC-groups §8.5; §11.1 «policy_ops.py + API»): the
version ``rev`` of every write of the list, ``ver`` of the objects outside it, ``?dry=1`` with the same ``moves`` as the
write, 404 ``gone``, the three steps of the writes through the script of the pool, «занято» (code 6) from ANOTHER
process, the nested lock — 500, the fields of ``GET /api/gw/exits`` and ``GET /api/gw/people``."""

import json
import os
import subprocess
import sys
import time
from unittest import mock

from kaskad import errors, paths, snapshot, state, util
from kaskad.gw import exits, exitsubs, model, ops, policy as P, policy_ops as pops, rev as R
from tests.fakes_gw import VLESS_A, VLESS_B, awg_line, write_pool
from tests.panel.panelcase import PanelCase

_REAL_LOCKED = state.locked
VLESS_C = ("vless://00000007-2222-3333-4444-555555555555@198.51.100.8:443?type=tcp&security=reality"
           "&pbk=8FXcm8haAt5zxGJFC6-71alFTj_LhAL5hbOx7QBE6lc&sid=80cd0a54&sni=www.microsoft.com&fp=chrome#k7")


def _short_locked(timeout=15.0):
    """The wait of the state lock cut to 1 s (the substitution of the 15 s of §3.1 for the tests of «занято»)."""
    return _REAL_LOCKED(min(timeout, 1.0))


class Holder(object):
    """The state lock held by ANOTHER process (never this thread: that is the nested lock, §11.1 проверка 20, З-2)."""

    def __init__(self, path):
        self.path = path
        self.proc = None

    def __enter__(self):
        code = ("import fcntl, os, sys, time\nfd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)\n"
                "fcntl.flock(fd, fcntl.LOCK_EX)\nsys.stdout.write('held\\n'); sys.stdout.flush()\ntime.sleep(30)\n")
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.proc = subprocess.Popen([sys.executable, "-c", code, self.path], stdout=subprocess.PIPE)
        assert self.proc.stdout.readline().strip() == b"held"
        return self

    def __exit__(self, *a):
        self.proc.kill()                                # our own process, by its PID
        self.proc.wait()


class PolicyCase(PanelCase):
    def setUp(self):
        super().setUp()
        self.w.runner.on(["systemd-run"], lambda argv, inp: (0, "", ""))     # the rebuild of the bot's proxy (§2.9)
        self.gateway()
        self.did = ops.device_add(self.w.runner, self.uid, "Телефон", kind="phone", platform="android",
                                  access=["wg"])["device"]["id"]
        self.collect()

    def rev(self):
        return self.ok("GET", "/api/gw/exits")["rev"]

    def both_ok(self):
        """Both exits «ok» in the snapshot (xray of the fake node is not asked): a device has somewhere to move."""
        snap = exits.load_snapshot()
        for eid in (self.vid, self.aid):
            rec = snap["exits"].setdefault(eid, {})
            rec.update(state="ok", reasons=[], alive=True)
        exits.save_snapshot(snap)
        snapshot.forget()

    def policy(self):
        return self.ok("GET", "/api/gw/policy")

    def state_bytes(self):
        return self.read("/etc/kaskad/state.json")

    def pinned(self):
        """The exit the device is pinned to by the collector."""
        from kaskad.gw import pins as PN
        for g in (PN.read().get("groups") or {}).values():
            p = (g.get("pins") or {}).get(self.did)
            if p:
                return p["exit"]
        return None

    def log(self):
        try:
            return self.read("/var/log/kaskad.log")
        except OSError:
            return ""

    def add_group(self, name="Стриминг", role="abroad", **kw):
        return self.ok("POST", "/api/gw/groups", dict({"name": name, "role": role, "rev": self.rev()}, **kw))["id"]


class ReadTest(PolicyCase):
    def test_policy_view_labels_presets_forecast(self):
        doc = self.policy()
        self.assertIsInstance(doc["rev"], int)
        g = doc["policy"]["groups"][0]
        self.assertEqual((g["id"], g["name"], g["label"], g["preset"]), ("main", "", "Основная", "standard"))
        self.assertEqual(g["tiers"][0]["label"], "Уровень 1")
        # T15 Q3 (Б12): after Q3 the Russian role has its preset «Дом» — its fields as policy.preset_fields
        from kaskad.gw import policy as P
        self.assertEqual(list(doc["presets"]["ru"]), ["ru_home"])
        self.assertEqual(doc["presets"]["ru"]["ru_home"], P.preset_fields("ru_home", "ru"))
        self.assertEqual(doc["presets"]["ru"]["ru_home"]["checks"]["geo"]["expect"], "RU")
        self.assertIn("standard", doc["presets"]["abroad"])
        self.assertIn("main", doc["forecast"]["probe_mb_day"])
        self.assertGreater(doc["forecast"]["probe_mb_day"]["main"], 0)
        with mock.patch.dict(os.environ, {"KASKAD_LANG": "en"}):
            from kaskad.i18n import use_lang
            with use_lang("en"):
                self.assertEqual(pops.view()["policy"]["groups"][0]["label"], "Main")

    def test_exits_rows_and_rev_of_one_read(self):
        doc = self.ok("GET", "/api/gw/exits")
        self.assertEqual(doc["rev"], R.rev_of(state.load()))
        self.assertEqual(len(doc["default_route_ver"]), 12)
        row = [r for r in doc["exits"] if r["id"] == self.aid][0]
        for k in ("health", "reasons", "group", "tier", "devices", "rtt_ms", "loss_pct", "jitter_ms", "country", "speed",
                  "quota", "speed_skip", "probe_mb_day", "last_check_ts", "recovering", "off_until", "unstable",
                  "unstable_until", "manual_why", "role", "proto"):
            self.assertIn(k, row)
        self.assertEqual((row["group"], row["tier"], row["role"]), ("main", "t1", "abroad"))
        self.assertIn(row["health"], ("ok", "degraded", "standby", "off", "unknown"))
        self.assertIn(row["state"], ("alive", "dead", "unknown"))        # the words of 3.3.5 stay for the panel
        self.assertEqual(sum(r["devices"] for r in doc["exits"]), 1)
        # a write: the new rev at once (the view is cached by the signature of state.json and the pool)
        was = doc["rev"]
        self.ok("POST", "/api/gw/exits/%s/manual" % self.aid, {"state": "standby", "rev": was})
        self.assertEqual(self.rev(), was + 1)
        # a line written past Python: the next read counts it (the same read gives the rows and the rev)
        write_pool([VLESS_A, awg_line(2), VLESS_B])
        snapshot.forget()
        doc = self.ok("GET", "/api/gw/exits")
        self.assertEqual(doc["rev"], was + 2)
        self.assertIn(exits.exit_id(VLESS_B), [r["id"] for r in doc["exits"]])

    def test_people_net_and_route_ver(self):
        p = self.ok("GET", "/api/gw/people")
        dev = [d for d in p["devices"] if d["id"] == self.did][0]
        self.assertEqual(len(dev["route_ver"]), 12)
        self.assertEqual((dev["net"]["step"], dev["net"]["group"], dev["net"]["missing"]), ("exit", "main", False))
        u = [x for x in p["users"] if x["id"] == self.uid][0]
        self.assertEqual(u["net"]["step"], "exit")
        self.assertEqual(len(u["route_ver"]), 12)


class RevTest(PolicyCase):
    LIST = None

    def calls(self, gid, eid):
        """Every request of the list of §7.9 (body or ?rev=)."""
        return [("PUT", "/api/gw/policy", {"policy": self.policy()["policy"]}),
                ("POST", "/api/gw/policy/reset", {"preset": "compat"}),
                ("POST", "/api/gw/groups", {"name": "Новая", "role": "abroad"}),
                ("DELETE", "/api/gw/groups/%s" % gid, None),
                ("PUT", "/api/gw/exits/%s" % eid, {"quota": {"limit_gb": 5}}),
                ("PUT", "/api/gw/exits/%s" % eid, {"speed_skip": True}),
                ("POST", "/api/gw/exits/%s/manual" % eid, {"state": "standby"}),
                ("POST", "/api/gw/exits/%s/country" % eid, {"accept": True}),
                ("POST", "/api/gw/exits/%s/enabled" % eid, {"on": False}),
                ("POST", "/api/gw/exits/%s/move" % eid, {"group": gid}),
                ("DELETE", "/api/gw/exits/%s" % eid, None)]

    def test_every_write_of_the_list_needs_rev(self):
        gid = self.add_group()
        self.rev()
        before = self.state_bytes()
        for method, path, body in self.calls(gid, self.aid):
            doc = self.err(400, method, path, body)
            self.assertEqual(doc["code"], "rev_required", path)
            self.assertIn("обновите страницу", doc["error"])
        self.assertEqual(self.state_bytes(), before)
        self.assertEqual(self.w.script.calls and [c for c in self.w.script.calls if c[:1] == ["--egress-del"]], [])

    def test_the_others_need_no_rev(self):
        doc = self.ok("POST", "/api/gw/exits", {"key": VLESS_B})
        self.assertIsInstance(doc["rev"], int)
        r = self.call("PUT", "/api/rules/profile", {"target": "default", "profile": "all_exit"})
        self.assertNotEqual((r.json() or {}).get("code"), "rev_required")
        ver = self.ok("GET", "/api/gw/people")
        uver = [x for x in ver["users"] if x["id"] == self.uid][0]["route_ver"]
        self.ok("PUT", "/api/gw/users/%s/route" % self.uid, {"via": "pool", "ver": uver})
        rv = self.rev()
        self.ok("PUT", "/api/gw/exits/%s" % self.aid, {"name": "Амстердам"})                 # a name: no rev, no change
        self.assertEqual(self.rev(), rv)
        gid = self.add_group()
        rv = self.rev()
        doc = self.ok("PUT", "/api/gw/groups/%s" % gid, {"name": "Кино"})
        self.assertEqual((doc["name"], doc["label"], doc["old"]), ("Кино", "Кино", "Стриминг"))
        self.assertEqual(self.rev(), rv)

    def test_an_old_rev_is_409_and_nothing_written(self):
        gid = self.add_group()
        rev0 = self.rev()
        pol = self.policy()["policy"]
        doc = self.ok("POST", "/api/gw/exits/%s/move" % self.vid, {"group": gid, "rev": rev0})
        self.assertEqual(doc["rev"], rev0 + 1)
        before = self.state_bytes()
        doc = self.err(409, "PUT", "/api/gw/policy", {"policy": pol, "rev": rev0})
        self.assertEqual((doc["code"], doc["data"]["rev"]), ("policy_changed", rev0 + 1))
        for method, path, body in (("POST", "/api/gw/exits/%s/manual" % self.aid, {"state": "standby", "rev": rev0}),
                                   ("POST", "/api/gw/exits/%s/enabled" % self.aid, {"on": False, "rev": rev0}),
                                   ("DELETE", "/api/gw/groups/%s?rev=%d&exits_to=main" % (gid, rev0), None),
                                   ("DELETE", "/api/gw/exits/%s?rev=%d" % (self.aid, rev0), None)):
            self.assertEqual(self.err(409, method, path, body)["code"], "policy_changed", path)
        self.assertEqual(self.state_bytes(), before)
        self.assertEqual([c for c in self.w.script.calls if c[:1] == ["--egress-del"]], [])

    def test_put_policy_keeps_groups_names_and_the_speed_test(self):
        gid = self.add_group()
        doc = self.policy()
        pol = json.loads(json.dumps(doc["policy"]))
        # a moved exit — 400 use_move
        g0, g1 = pol["groups"][0], [g for g in pol["groups"] if g["id"] == gid][0]
        bad = json.loads(json.dumps(pol))
        b0, b1 = bad["groups"][0], [g for g in bad["groups"] if g["id"] == gid][0]
        b0["tiers"][0]["exits"].remove(self.aid)
        b1["tiers"][0]["exits"].append(self.aid)
        self.assertEqual(self.err(400, "PUT", "/api/gw/policy", {"policy": bad, "rev": doc["rev"]})["code"],
                         "use_move")
        # a group of the saved policy missing — 409 group_missing
        miss = json.loads(json.dumps(pol))
        miss["groups"] = [g for g in miss["groups"] if g["id"] != gid]
        self.assertEqual(self.err(409, "PUT", "/api/gw/policy", {"policy": miss, "rev": doc["rev"]})["code"],
                         "group_missing")
        # names of groups and probes.speed in the body are skipped; the rest is written, rev + 1
        g1["name"] = "Другое"
        g0["fail"] = "direct"
        pol["probes"]["speed"]["max_mb"] = 1
        sp = self.ok("GET", "/api/gw/probes/speed")
        res = self.ok("PUT", "/api/gw/policy", {"policy": pol, "rev": doc["rev"]})
        self.assertEqual(res["rev"], doc["rev"] + 1)
        now = self.policy()["policy"]
        self.assertEqual([g["name"] for g in now["groups"] if g["id"] == gid], ["Стриминг"])
        self.assertEqual(now["groups"][0]["fail"], "direct")
        self.assertEqual(self.ok("GET", "/api/gw/probes/speed"), sp)
        # a new group in the body — refused (POST /api/gw/groups)
        pol2 = self.policy()["policy"]
        pol2["groups"].append(dict(pol2["groups"][-1], id="g-abcdef"))
        self.assertEqual(self.err(400, "PUT", "/api/gw/policy", {"policy": pol2, "rev": self.rev()})["code"], "field")

    def test_reset_and_standard_keep_the_speed_test(self):
        sp = self.ok("GET", "/api/gw/probes/speed")
        self.ok("PUT", "/api/gw/probes/speed", {"max_mb": 5, "ver": sp["ver"]})
        sp = self.ok("GET", "/api/gw/probes/speed")
        doc = self.ok("POST", "/api/gw/policy/reset", {"preset": "compat", "rev": self.rev()})
        self.assertEqual(self.policy()["policy"]["groups"][0]["preset"], "compat")
        self.ok("POST", "/api/gw/policy/reset", {"preset": "standard", "group": "main", "rev": doc["rev"]})
        self.assertEqual(self.policy()["policy"]["groups"][0]["preset"], "standard")
        self.assertEqual(self.ok("GET", "/api/gw/probes/speed"), sp)
        self.assertEqual(self.err(400, "POST", "/api/gw/policy/reset", {"preset": "custom", "rev": self.rev()})["code"],
                         "field")


class DryTest(PolicyCase):
    def same_dry_and_real(self, method, path, body, check=None):
        """A dry run writes nothing (after a read the count is in the file already) and gives the moves of the write."""
        self.rev()
        before = self.state_bytes()
        sep = "&" if "?" in path else "?"
        dry = self.ok(method, path + sep + "dry=1", body)
        self.assertTrue(dry["dry"])
        self.assertEqual(dry["rev"], self.rev())
        self.assertEqual(self.state_bytes(), before, path)
        real = self.ok(method, path, body)
        self.assertEqual(real["moves"], dry["moves"], path)
        if check:
            check(dry, real)
        return dry, real

    def test_the_buttons_over_one_exit(self):
        self.both_ok()
        on = self.pinned()
        self.assertIn(on, (self.vid, self.aid))
        other = self.aid if on == self.vid else self.vid
        dry, real = self.same_dry_and_real("POST", "/api/gw/exits/%s/manual" % on, {"state": "standby",
                                                                                   "rev": self.rev()})
        self.assertEqual(real["moves"]["total"], 1)
        self.assertEqual(real["moves"]["devices"][0]["to"]["exit"], other)
        self.assertEqual(real["moves"]["by_group"]["main"]["by_tier"], {"t1": 1})
        self.assertEqual(real["state"], "standby")
        self.same_dry_and_real("POST", "/api/gw/exits/%s/manual" % on, {"state": "restore", "rev": self.rev()})
        self.same_dry_and_real("POST", "/api/gw/exits/%s/enabled" % on, {"on": False, "rev": self.rev()})
        self.same_dry_and_real("POST", "/api/gw/exits/%s/enabled" % on, {"on": True, "rev": self.rev()})
        gid = self.add_group()
        self.same_dry_and_real("POST", "/api/gw/exits/%s/move" % on, {"group": gid, "rev": self.rev()})
        self.same_dry_and_real("POST", "/api/gw/policy/reset", {"preset": "strict", "rev": self.rev()})

    def test_delete_of_a_key_and_of_a_group(self):
        self.both_ok()
        on = self.pinned()
        dry = self.ok("DELETE", "/api/gw/exits/%s?dry=1" % on)
        self.assertEqual((dry["dry"], dry["moves"]["total"]), (True, 1))
        self.assertEqual([c for c in self.w.script.calls if c[:1] == ["--egress-del"]], [])
        real = self.ok("DELETE", "/api/gw/exits/%s?rev=%d" % (on, dry["rev"]))
        self.assertEqual(real["moves"], dry["moves"])
        self.assertIsInstance(real["rev"], int)
        gid = self.add_group()
        left = [i for i, _ in exits.line_ids()][0]
        self.ok("POST", "/api/gw/exits/%s/move" % left, {"group": gid, "rev": self.rev()})
        self.same_dry_and_real("DELETE", "/api/gw/groups/%s?exits_to=main&rev=%d" % (gid, self.rev()), None)
        self.assertIsNone(P.group(P.load(state.load()), gid))


class GoneTest(PolicyCase):
    def test_one_exit_gone(self):
        rev0 = self.rev()
        gone = "e-0123456789"
        for method, path, body in (("POST", "/api/gw/exits/%s/manual" % gone, {"state": "standby", "rev": rev0}),
                                   ("POST", "/api/gw/exits/%s/manual?dry=1" % gone, {"state": "standby"}),
                                   ("POST", "/api/gw/exits/%s/move" % gone, {"group": "main", "rev": rev0}),
                                   ("POST", "/api/gw/exits/%s/move?dry=1" % gone, {"group": "main"}),
                                   ("POST", "/api/gw/exits/%s/enabled" % gone, {"on": True, "rev": rev0}),
                                   ("POST", "/api/gw/exits/%s/country" % gone, {"accept": True, "rev": rev0}),
                                   ("PUT", "/api/gw/exits/%s" % gone, {"speed_skip": True, "rev": rev0}),
                                   ("PUT", "/api/gw/exits/%s" % gone, {"name": "x"})):
            doc = self.err(404, method, path, body)
            self.assertEqual((doc["code"], doc["data"]["gone"], doc["data"]["exit"]), ("gone", True, gone), path)

    def test_a_group_gone_before_rev_and_the_repeat_of_a_key_after_it(self):
        gid = self.add_group()
        old = self.rev()
        self.ok("DELETE", "/api/gw/groups/%s?rev=%d" % (gid, old))
        doc = self.err(404, "DELETE", "/api/gw/groups/%s?rev=%d" % (gid, old))          # the repeat, the old rev
        self.assertEqual((doc["code"], doc["data"]["gone"], doc["data"]["group"]), ("gone", True, gid))
        self.assertEqual(self.err(404, "PUT", "/api/gw/groups/%s" % gid, {"name": "Икс"})["code"], "gone")
        # [A, B, A]: B removed — e-A-3 became e-A-2; with the old rev — 409, the repeat stays
        write_pool([VLESS_A, awg_line(2), VLESS_A])
        snapshot.forget()
        old = self.rev()
        self.ok("DELETE", "/api/gw/exits/%s?rev=%d" % (self.aid, old))
        doc = self.err(409, "DELETE", "/api/gw/exits/%s-3?rev=%d" % (self.vid, old))
        self.assertEqual(doc["code"], "policy_changed")
        self.assertEqual(exits.pool_lines(), [VLESS_A, VLESS_A])


class ExitFormTest(PolicyCase):
    def test_the_order_and_the_quota(self):
        rv = self.rev()
        url = "/api/gw/exits/%s" % self.aid
        self.assertEqual(self.err(400, "PUT", url, {"group": "main", "rev": rv})["code"], "use_move")
        self.assertEqual(self.err(400, "PUT", url, {"quota": {"limit_gb": 5}})["code"], "rev_required")
        before = model.exit_pref(state.load(), self.aid)
        doc = self.err(400, "PUT", url, {"quota": {"limit_gb": 5, "used_gb": 1}, "rev": rv})
        self.assertEqual((doc["code"], doc["data"]["field"]), ("field", "quota.used_gb"))
        self.assertEqual(model.exit_pref(state.load(), self.aid), before)
        dry = self.ok("PUT", url + "?dry=1", {"quota": {"limit_gb": 5, "action": "standby"}, "rev": rv})
        self.assertEqual(dry["rev"], rv)
        doc = self.ok("PUT", url, {"quota": {"limit_gb": 5, "action": "standby"}, "rev": rv})
        self.assertEqual((doc["rev"], doc["pref"]["quota"]["limit_gb"]), (rv + 1, 5))
        row = [r for r in self.ok("GET", "/api/gw/exits")["exits"] if r["id"] == self.aid][0]
        q = row["quota"]
        self.assertEqual((q["period"], q["limit_gb"], q["reset_day"], q["warn_pct"], q["action"]),
                         ("month", 5, 1, 80, "standby"))
        for k in ("used_gb", "pct", "reset", "paused_until"):
            self.assertIn(k, q)
        doc = self.ok("PUT", url, {"quota": None, "speed_skip": True, "rev": rv + 1})
        self.assertNotIn("quota", doc["pref"])
        self.assertTrue(doc["pref"]["speed_skip"])

    def test_names_of_exits_duplicates(self):
        self.ok("PUT", "/api/gw/exits/%s" % self.aid, {"name": "Вена-2"})
        doc = self.err(409, "PUT", "/api/gw/exits/%s" % self.vid, {"name": "вена-2"})
        self.assertEqual((doc["code"], doc["data"]["duplicates"]), ("confirm_duplicate", [self.aid]))
        self.ok("PUT", "/api/gw/exits/%s" % self.vid, {"name": "вена-2", "allow_duplicate": True})


class GroupsTest(PolicyCase):
    def test_add_rename_duplicates(self):
        rv = self.rev()
        doc = self.err(409, "POST", "/api/gw/groups", {"name": "main", "role": "abroad", "rev": rv})
        self.assertEqual((doc["code"], doc["data"]["duplicates"]), ("confirm_duplicate", ["main"]))
        self.assertEqual(self.err(400, "POST", "/api/gw/groups", {"name": "", "role": "abroad", "rev": rv})["code"],
                         "field")
        gid = self.ok("POST", "/api/gw/groups", {"name": "Стриминг", "role": "abroad", "rev": rv})["id"]
        self.assertRegex(gid, r"^g-[0-9a-f]{6}$")
        g = P.group(P.load(state.load()), gid)
        self.assertEqual((g["name"], g["role"], g["preset"]), ("Стриминг", "abroad", "standard"))
        for name in ("Main", "основная"):
            doc = self.err(409, "PUT", "/api/gw/groups/%s" % gid, {"name": name})
            self.assertEqual(doc["code"], "confirm_duplicate")
        doc = self.ok("PUT", "/api/gw/groups/%s" % gid, {"name": "Main", "allow_duplicate": True})
        self.assertEqual(doc["label"], "Main")
        doc = self.ok("PUT", "/api/gw/groups/main", {"name": ""})
        self.assertEqual((doc["name"], doc["label"]), ("", "Основная"))

    def test_delete_needs_its_parameters(self):
        self.assertEqual(self.err(400, "DELETE", "/api/gw/groups/main?rev=%d" % self.rev())["code"], "main")
        gid = self.add_group()
        self.ok("POST", "/api/gw/exits/%s/move" % self.aid, {"group": gid, "rev": self.rev()})
        st = state.load()
        model.get_user(st, self.uid)["route"] = {"via": "group:" + gid, "fail": "block"}
        st["gateway"]["exit_policy"]["tg_proxy_group"] = gid
        state.save(st)
        snapshot.forget()
        doc = self.err(409, "DELETE", "/api/gw/groups/%s?rev=%d" % (gid, self.rev()))
        self.assertEqual(doc["code"], "need")
        self.assertEqual(doc["data"]["exits"], [self.aid])
        self.assertEqual(doc["data"]["refs"]["users"], [self.uid])
        self.assertEqual(doc["data"]["uses"], ["tg_proxy"])
        ru = self.add_group("Дом", role="ru")
        doc = self.err(400, "DELETE", "/api/gw/groups/%s?rev=%d&exits_to=%s&reassign=direct&tg_proxy_to=main"
                       % (gid, self.rev(), ru))
        self.assertEqual(doc["code"], "field")
        # with everything: the exits to main, the person direct, the bot's proxy to main — rebuilt
        self.write("/etc/systemd/system/kaskad-egress.service", "[Unit]\n")
        doc = self.ok("DELETE", "/api/gw/groups/%s?rev=%d&exits_to=main&reassign=direct&tg_proxy_to=main"
                      % (gid, self.rev()))
        self.assertEqual((doc["uses"], doc["refs"]["users"]), (["tg_proxy"], [self.uid]))
        st = state.load()
        self.assertEqual(model.get_user(st, self.uid)["route"]["via"], "direct")
        pol = P.load(st)
        self.assertEqual((pol["tg_proxy_group"], P.group_of(pol, self.aid)["id"]), ("main", "main"))
        self.assertTrue([c for c in self.w.runner.calls if c[0][:1] == ["systemd-run"] and "--egress-tg-rebuild" in c[0]])


class VerTest(PolicyCase):
    def test_route_of_the_node(self):
        doc = self.ok("GET", "/api/gw/exits")
        ver = doc["default_route_ver"]
        self.assertEqual(self.err(400, "PUT", "/api/gw/route", {"via": "direct"})["code"], "ver_required")
        before = self.state_bytes()
        doc = self.err(409, "PUT", "/api/gw/route", {"via": "direct", "ver": "000000000000"})
        self.assertEqual((doc["code"], doc["data"]["ver"]), ("object_changed", ver))
        self.assertEqual(self.state_bytes(), before)
        dry = self.ok("PUT", "/api/gw/route?dry=1", {"via": "direct"})
        self.assertEqual((dry["dry"], dry["ver"], dry["moves"]["total"]), (True, ver, 1))
        self.assertEqual(self.state_bytes(), before)
        res = self.ok("PUT", "/api/gw/route", {"via": "direct", "ver": ver})
        self.assertEqual(res["moves"], dry["moves"])
        self.assertNotEqual(res["ver"], ver)
        self.assertEqual(self.ok("GET", "/api/gw/exits")["default_route_ver"], res["ver"])
        # the CLI (gw/ops, Python) writes without ver
        ops.default_route(self.w.runner, "pool", "block")
        self.assertNotEqual(self.ok("GET", "/api/gw/exits")["default_route_ver"], res["ver"])

    def test_route_of_a_device_and_a_person(self):
        p = self.ok("GET", "/api/gw/people")
        dver = [d for d in p["devices"] if d["id"] == self.did][0]["route_ver"]
        url = "/api/gw/devices/%s/route" % self.did
        self.assertEqual(self.err(400, "PUT", url, {"via": "block"})["code"], "ver_required")
        self.assertEqual(self.err(409, "PUT", url, {"via": "block", "ver": "x"})["code"], "object_changed")
        res = self.ok("PUT", url, {"via": "block", "ver": dver})
        self.assertEqual(res["device"]["route"]["via"], "block")
        self.assertEqual(self.err(409, "PUT", url, {"via": "pool", "ver": dver})["code"], "object_changed")
        ops.user_route(self.w.runner, self.uid, "direct")                 # CLI/bot without ver
        self.assertEqual(model.get_user(state.load(), self.uid)["route"]["via"], "direct")

    def test_speed_test_settings(self):
        sp = self.ok("GET", "/api/gw/probes/speed")
        self.assertEqual(sorted(sp), sorted(pops.SPEED_KEYS + ("ver", "ok")))
        self.assertEqual(self.err(400, "PUT", "/api/gw/probes/speed", {"max_mb": 5})["code"], "ver_required")
        self.assertEqual(self.err(400, "PUT", "/api/gw/probes/speed", {"nope": 1, "ver": sp["ver"]})["code"], "field")
        self.assertEqual(self.err(400, "PUT", "/api/gw/probes/speed", {"max_mb": 500, "ver": sp["ver"]})["code"], "field")
        rv = self.rev()
        res = self.ok("PUT", "/api/gw/probes/speed", {"max_mb": 5, "ver": sp["ver"]})
        self.assertEqual(res["max_mb"], 5)
        self.assertEqual(self.rev(), rv)                                     # outside the fingerprint
        self.assertEqual(self.err(409, "PUT", "/api/gw/probes/speed", {"max_mb": 6, "ver": sp["ver"]})["code"],
                         "object_changed")

    def test_notify(self):
        doc = self.ok("GET", "/api/notify")
        self.assertIn("flap_summary", doc)
        self.assertEqual(doc["push"]["speedtest_budget"], False)
        self.assertEqual(self.err(400, "PUT", "/api/notify", {"flap_summary": False})["code"], "ver_required")
        res = self.ok("PUT", "/api/notify", {"flap_summary": not doc["flap_summary"], "ver": doc["ver"]})
        self.assertNotEqual(res["ver"], doc["ver"])
        self.assertEqual(self.err(409, "PUT", "/api/notify", {"push": {"exit_dead": False}, "ver": doc["ver"]})["code"],
                         "object_changed")
        self.assertEqual(self.err(400, "PUT", "/api/notify", {"push": {"nope": True}, "ver": res["ver"]})["code"],
                         "field")

    def test_time(self):
        doc = self.ok("GET", "/api/settings/time")
        for k in ("tz", "os_tz", "now", "offset", "ntp", "zones", "ver"):
            self.assertIn(k, doc)
        self.assertEqual(self.err(400, "PUT", "/api/settings/time", {"tz": "Europe/Berlin"})["code"], "ver_required")
        self.assertEqual(self.err(409, "PUT", "/api/settings/time", {"tz": "Europe/Berlin", "ver": "x"})["code"],
                         "object_changed")
        self.assertEqual(self.err(400, "PUT", "/api/settings/time", {"tz": "../etc", "ver": doc["ver"]})["code"],
                         "field")

    def test_metrics_size(self):
        doc = self.ok("GET", "/api/gw/metrics")
        self.assertEqual((doc["max_mb"], doc["default"]), (64, 64))
        res = self.ok("PUT", "/api/gw/metrics", {"max_mb": 128, "ver": doc["ver"]})
        self.assertEqual(state.load()["gateway"]["metrics_max_mb"], 128)
        self.assertEqual(self.err(409, "PUT", "/api/gw/metrics", {"max_mb": 32, "ver": doc["ver"]})["code"],
                         "object_changed")
        self.assertEqual(self.err(400, "PUT", "/api/gw/metrics", {"max_mb": 1, "ver": res["ver"]})["code"], "field")


class LockTest(PolicyCase):
    def test_busy_is_409_code_6_for_a_write_and_a_dry_run(self):
        rv = self.rev()
        with mock.patch.object(state, "locked", _short_locked), Holder(paths.lock()):
            for method, path, body in (("POST", "/api/gw/exits/%s/manual" % self.aid, {"state": "standby", "rev": rv}),
                                       ("POST", "/api/gw/exits/%s/manual?dry=1" % self.aid, {"state": "standby"})):
                doc = self.err(409, method, path, body)
                self.assertEqual(doc["code"], errors.LOCKED, path)

    def test_a_nested_lock_is_500_and_a_line_in_the_journal(self):
        def nested(st=None, lang=None):
            with state.locked():
                return R.current(state.load())
        with mock.patch.object(pops, "view", nested):
            doc = self.err(500, "GET", "/api/gw/policy")
        self.assertEqual(doc["code"], errors.ERROR)
        self.assertIn("nested state lock", self.log())

    def test_step_3_busy_answers_rev_null_and_the_key_is_gone(self):
        holder = Holder(paths.lock())
        real = self.w.script.egress_del

        def busy_after(n, hexid):
            out = real(n, hexid)
            holder.__enter__()                          # another apply takes the lock once the line is gone
            return out
        self.w.script.egress_del = busy_after
        try:
            with mock.patch.object(state, "locked", _short_locked):
                doc = self.ok("DELETE", "/api/gw/exits/%s?rev=%d" % (self.aid, self.rev()))
        finally:
            holder.__exit__()
        self.assertIsNone(doc["rev"])
        self.assertNotIn(self.aid, [i for i, _ in exits.line_ids()])
        self.assertIn("не досчитана", self.log())

    def test_exit_add_holds_no_lock_while_the_script_runs(self):
        real = self.w.script.egress_add
        seen = []

        def add(text):
            with state.locked(timeout=0.5):             # the script's own gw apply: it takes the lock, never waits
                seen.append(True)
            return real(text)
        self.w.script.egress_add = add
        gid = self.add_group()
        t0 = time.time()
        doc = self.ok("POST", "/api/gw/exits", {"key": VLESS_B, "group": gid})
        self.assertLess(time.time() - t0, 5)
        self.assertEqual(seen, [True])
        eid = exits.exit_id(VLESS_B)
        pol = P.load(state.load())
        self.assertEqual(P.group_of(pol, eid)["id"], gid)                   # placed after the script, in its group
        self.assertEqual(doc["rev"], self.rev())
        self.collect()                                                       # the tick after it changes nothing
        self.assertEqual(self.rev(), doc["rev"])
        self.assertEqual(self.err(400, "POST", "/api/gw/exits", {"key": awg_line(5), "group": "g-000000"})["code"],
                         "field")


class SubsRevTest(PolicyCase):
    URL = "http://203.0.113.50:2096/sub/abcdef0123456789"

    def feed(self, body):
        def fetch(url, pin=None, new_pin=False):
            return body, pin
        return fetch

    def test_add_refresh_delete_carry_rev_and_a_busy_step_3_keeps_the_keys(self):
        import base64
        body = base64.b64encode(VLESS_B.encode()).decode()
        res = exitsubs.add(self.w.runner, self.URL, fetcher=self.feed(body))
        self.assertEqual(res["rev"], self.rev())
        holder = Holder(paths.lock())
        real = exitsubs.save

        def save_busy(doc):
            real(doc)
            if not holder.proc:
                holder.__enter__()
        with mock.patch.object(state, "locked", _short_locked), mock.patch.object(exitsubs, "save", save_busy):
            try:
                body2 = base64.b64encode(("%s\n%s" % (VLESS_B, VLESS_C)).encode()).decode()
                res = exitsubs.refresh(self.w.runner, res["sub"]["id"], fetcher=self.feed(body2))
            finally:
                holder.__exit__()
        self.assertIsNone(res["rev"])
        sub = exitsubs.load()["subs"][0]
        self.assertEqual(len(sub["keys"]), 2)                                 # recorded before the count
        self.assertIn(VLESS_C, exits.pool_lines())
        doc = self.ok("DELETE", "/api/gw/exit-subs/%s" % sub["id"])
        self.assertEqual(doc["rev"], self.rev())
        doc = self.err(404, "DELETE", "/api/gw/exit-subs/%s" % sub["id"])
        self.assertEqual((doc["code"], doc["data"]["gone"], doc["data"]["sub"]), ("gone", True, sub["id"]))


class TgRebuildTest(PolicyCase):
    def test_not_more_often_than_once_a_minute_and_never_lost(self):
        self.write("/etc/systemd/system/kaskad-egress.service", "[Unit]\n")
        self.assertEqual(pops.tg_rebuild(self.w.runner, now=1000.0), "now")
        self.assertEqual(pops.tg_rebuild(self.w.runner, now=1010.0), "later")
        argv = [c[0] for c in self.w.runner.calls if c[0][:1] == ["systemd-run"]]
        self.assertIn("--no-block", argv[0])
        self.assertIn("--on-active=51s", argv[1])
        self.assertEqual(pops.tg_rebuild(self.w.runner, now=1061.0), "now")
        os.unlink(self.path("/etc/systemd/system/kaskad-egress.service"))
        self.assertIsNone(pops.tg_rebuild(self.w.runner, now=2000.0))


class TofuButtonTest(PolicyCase):
    """«Закрепить» of the preview (§9.1; T7 — the API, the button — T8): the preview asks ``tofu`` only on request, the
    add pins the keys whose fingerprint is the one shown, before the script; another — ``bad_pin``, nothing added."""

    def test_pin_before_the_script_only_the_fingerprint_shown(self):
        from kaskad.gw import tofu
        sha = "ab" * 32
        asked, pinned = [], []

        def check(parser, line, by="panel", runner=None):
            asked.append(by)
            return {"status": "ask", "server": "198.51.100.21:8443", "sha256": sha} if line == VLESS_B else None

        def consent(parser, line, s, by, runner=None):
            pinned.append((line == VLESS_B, s, by))
            return {"status": "pinned"}
        with mock.patch.object(tofu, "check", check), mock.patch.object(tofu, "consent", consent):
            doc = self.err(409, "POST", "/api/gw/exits", {"key": VLESS_B, "pin_tofu": "cd" * 32})
            self.assertEqual(doc["data"]["code"], "bad_pin")
            self.assertEqual([c for c in self.w.script.calls if c[:1] == ["--egress-add"]], [])
            self.ok("POST", "/api/gw/exits", {"key": VLESS_B, "pin_tofu": sha.upper()})
        self.assertEqual(pinned, [(True, sha, "panel")])
        self.assertIn(exits.exit_id(VLESS_B), [i for i, _ in exits.line_ids()])
        with mock.patch.object(tofu, "check", check):
            self.ok("POST", "/api/gw/exits/preview", {"key": VLESS_B})
            self.assertEqual(asked, ["panel", "panel"])                      # the preview without «tofu» asks nothing
