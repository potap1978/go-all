"""SEC-s1-q2 С-2: the TPROXY port of a Russian xray exit is bound to its ID, not to its line (``exits.ru_ports``).

The kernel (gw_rx -> the mark of the line -> «tproxy to <port>») is applied BEFORE xray (gw apply, the collector), and
an apply of xray may fail or wait. With ports by the line, a key removed above another one gave its port to the next
key while xray still ran the old config: the device of the neighbour went into the inbound of somebody else's home
router (опыт test_x2_vless_renumber). Now a port names one exit: the old xray sends the device where the new table
means, or nowhere."""

import json
from unittest import mock

from kaskad import secrets, state, util
from kaskad.gw import exits, net, xray
from kaskad.gw import policy as P
from tests.fakes_gw import write_ru_pool
from tests.gw.test_ru_target import HOME, RuWorld

V1 = ("vless://aaaaaaaa-1111-2222-3333-444444444444@203.0.113.41:443?type=tcp&security=reality"
      "&pbk=8FXcm8haAt5zxGJFC6-71alFTj_LhAL5hbOx7QBE6lc&sid=80cd0a54&sni=www.microsoft.com&fp=chrome#Mama")
V2 = ("vless://bbbbbbbb-1111-2222-3333-444444444444@203.0.113.42:443?type=tcp&security=reality"
      "&pbk=8FXcm8haAt5zxGJFC6-71alFTj_LhAL5hbOx7QBE6lc&sid=80cd0a54&sni=www.microsoft.com&fp=chrome#Sosed")
V3 = ("vless://cccccccc-1111-2222-3333-444444444444@203.0.113.43:443?type=tcp&security=reality"
      "&pbk=8FXcm8haAt5zxGJFC6-71alFTj_LhAL5hbOx7QBE6lc&sid=80cd0a54&sni=www.microsoft.com&fp=chrome#Third")
SOSED = "g-0d0e0f"
D1_IP = "10.77.1.10"


class RuPortsTest(RuWorld):
    def two_people(self):
        st = self.world()
        write_ru_pool([V1, V2])
        v1, v2 = [e["id"] for e in exits.pool(self.w.runner, roles=("ru",))]
        doc = st["gateway"]["exit_policy"]
        P.group(doc, HOME)["tiers"][0]["exits"] = [v1]
        g = P.default_group(SOSED, "ru", "ru_home", name="Сосед")
        g["tiers"][0]["exits"] = [v2]
        doc["groups"].append(g)
        st["gateway"]["users"][0]["ru_via"] = SOSED        # «Мама»'s devices play the neighbour here
        snap = exits.load_snapshot()
        for i in (v1, v2):
            snap.setdefault("exits", {})[i] = {"alive": True, "state": "ok", "reasons": []}
        exits.save_snapshot(snap)
        state.save(st)
        return state.load(), v1, v2

    def running(self, cfg):
        util.atomic_write(xray.config_path(), util.json_text(cfg), 0o600)

    def inbound_of(self, cfg):
        return dict((i["port"], i["tag"]) for i in cfg["inbounds"] if str(i.get("tag", "")).startswith("tproxy-"))

    def test_a_removed_key_above_never_hands_its_port_to_the_next(self):
        st, v1, v2 = self.two_people()
        old = xray.render(st, secrets.load(), self.pool(), (), self.dec(st))
        self.running(old)
        rule_of = dict((t, r.get("outboundTag")) for r in old["routing"]["rules"] for t in r.get("inboundTag") or [])
        write_ru_pool([V2])                                 # MAMA's key deleted: SOSED's is line 1 now
        doc = st["gateway"]["exit_policy"]
        P.group(doc, HOME)["tiers"][0]["exits"] = []
        state.save(st)
        st = state.load()
        p = net.plan(st, self.w.runner)                     # the table — before xray (gw apply, the collector)
        m = p["rx"][D1_IP]
        port = dict(p["vless"])[m]
        tag = self.inbound_of(old).get(port)
        self.assertEqual(tag, exits.tproxy_tag(v2))         # the OLD xray on that port: the neighbour's own exit
        self.assertEqual(rule_of.get(tag), exits.tag(v2, "ru"))
        new = xray.render(st, secrets.load(), self.pool(), (), self.dec(st))
        self.assertEqual(self.inbound_of(new).get(port), exits.tproxy_tag(v2))   # the new xray agrees

    def test_a_new_key_never_takes_a_port_xray_may_still_run(self):
        st, v1, v2 = self.two_people()
        self.running(xray.render(st, secrets.load(), self.pool(), (), self.dec(st)))
        ports = dict((e["id"], exits.tproxy_port(e)) for e in exits.pool(self.w.runner, roles=("ru",)))
        write_ru_pool([V3, V2])                             # MAMA's key out, a third one in her place (line 1)
        rows = exits.pool(self.w.runner, roles=("ru",))
        got = dict((e["id"], exits.tproxy_port(e)) for e in rows)
        v3 = rows[0]["id"]
        self.assertEqual(got[v2], ports[v2])
        self.assertNotIn(got[v3], ports.values())           # never the port of the key that left (xray may run it)
        # the file of the ports lost (its «next» at the port of v1): the xray config on the disk still has v1 there —
        # never given away
        util.atomic_write(exits._ru_ports_path(), json.dumps({"ports": {}, "next": ports[v1]}), 0o600)
        got2 = dict((e["id"], exits.tproxy_port(e)) for e in exits.pool(self.w.runner, roles=("ru",)))
        self.assertEqual(got2[v2], ports[v2])               # seeded from the config xray runs
        self.assertNotEqual(got2[v3], ports[v1])

    def test_a_freed_port_comes_back_only_round_the_range(self):
        st, v1, v2 = self.two_people()
        write_ru_pool([V2])
        exits.pool(self.w.runner, roles=("ru",))
        nxt = json.loads(util.read_text(exits._ru_ports_path()))["next"]
        self.assertGreater(nxt, exits.RU_PORT_FIRST + 1)            # the world's key and v1 were given ports before
        write_ru_pool([V2, V3])
        v3 = exits.pool(self.w.runner, roles=("ru",))[1]
        self.assertEqual(exits.tproxy_port(v3), nxt)                # the next one round the range, not a freed one
        doc = json.loads(util.read_text(exits._ru_ports_path()))
        self.assertEqual(sorted(doc["ports"]), sorted([v2, v3["id"]]))      # the id that left went from the file

    def test_a_lost_file_keeps_the_port_xray_runs(self):
        st, v1, v2 = self.two_people()
        self.running(xray.render(st, secrets.load(), self.pool(), (), self.dec(st)))
        p2 = exits.tproxy_port([e for e in exits.pool(self.w.runner, roles=("ru",)) if e["id"] == v2][0])
        write_ru_pool([V2])
        util.atomic_write(exits._ru_ports_path(), json.dumps({"ports": {}, "next": exits.RU_PORT_FIRST}), 0o600)
        self.assertNotIn(exits.RU_PORT_FIRST, self.inbound_of(xray._read_config(xray.config_path())))
        self.assertEqual(exits.tproxy_port(exits.pool(self.w.runner, roles=("ru",))[0]), p2)

    def test_stable_by_the_id_and_the_same_for_the_kernel_and_xray(self):
        st, v1, v2 = self.two_people()
        a = dict((e["id"], exits.tproxy_port(e)) for e in exits.pool(self.w.runner, roles=exits.ROLES)
                 if e["role"] == "ru")
        write_ru_pool([V2, V1])                             # the lines swapped: the ports stay with the ids
        rows = exits.pool(self.w.runner, roles=exits.ROLES)
        b = dict((e["id"], exits.tproxy_port(e)) for e in rows if e["role"] == "ru")
        self.assertEqual(a, b)
        p = net.plan(state.load(), self.w.runner)
        cfg = xray.render(state.load(), secrets.load(), rows, (), self.dec(state.load()))
        marks = dict((e["mark"], e["id"]) for e in rows if e["role"] == "ru")
        inb = self.inbound_of(cfg)
        for mark, port in p["vless"]:
            if mark in marks:
                self.assertEqual(inb.get(port), exits.tproxy_tag(marks[mark]))
        fb = dict((e["id"], exits.tproxy_port(e)) for e in exits.pool_fallback(roles=exits.ROLES) if e["role"] == "ru")
        self.assertEqual(fb, b)                             # without the parser — the same ports

    def test_foreign_ports_by_the_line_as_before(self):
        from tests.fakes_gw import write_pool
        self.world()
        write_pool(exits.pool_lines("abroad") + [V3])
        rows = [e for e in exits.pool(self.w.runner, roles=exits.ROLES) if exits.is_xray(e) and e["role"] == "abroad"]
        self.assertTrue(rows)
        for e in rows:
            self.assertEqual(exits.tproxy_port(e), exits.VLESS_PORT_BASE + e["index"])

    def test_no_port_left_the_exit_is_not_usable(self):
        st, v1, v2 = self.two_people()
        import os
        os.unlink(exits._ru_ports_path())
        with mock.patch.object(exits, "RU_PORT_LAST", exits.RU_PORT_FIRST):
            rows = exits.pool(self.w.runner, roles=("ru",))
        by = dict((e["id"], e) for e in rows)
        self.assertEqual(exits.tproxy_port(by[v1]), exits.RU_PORT_FIRST)
        self.assertEqual(by[v2]["kind"], "bad")
        self.assertIsNone(by[v2]["mark"])
        self.assertEqual(by[v2]["problem_code"], "problem.ru_too_far")

    def test_no_russian_xray_exit_no_file(self):
        import os
        self.world()
        exits.pool(self.w.runner, roles=exits.ROLES)       # an AWG leg and a VLESS key: the file is there
        self.assertTrue(os.path.exists(exits._ru_ports_path()))
        write_ru_pool([])
        os.unlink(exits._ru_ports_path())
        exits.pool(self.w.runner, roles=exits.ROLES)
        self.assertFalse(os.path.exists(exits._ru_ports_path()))
