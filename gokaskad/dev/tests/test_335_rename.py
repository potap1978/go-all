"""3.3.5 №0: a subscription that renames a key («10GB left» → «9GB left») keeps its exit — the id, the routes of devices
and people, the name and the switch of the exit; another uuid or server is a new exit; ids of 3.3.4 stay as they were;
the same for AmneziaWG of goAmnezia (vpn://). Stand T-334 §21.4 (BACKLOG-3.3.5 №0)."""

import base64
import json
import os
import urllib.parse

from kaskad import backup, paths, state, util
from kaskad.gw import exits, exitsubs, model, ops
from tests.fakes_gw import VLESS_A, VLESS_B, write_pool
from tests.fakes_gw import _key as fake_key
from tests.test_backup import PW, BackupCase
from tests.test_gw_exits import conf_of, vpn_of
from tests.test_gw_exitsubs import GA_URL, IP_URL, SubsCase

BASE = ("vless://%s@198.51.100.40:%d?type=tcp&security=reality&pbk=8FXcm8haAt5zxGJFC6-71alFTj_LhAL5hbOx7QBE6lc"
        "&sid=80cd0a54&sni=www.microsoft.com&fp=chrome")
UUID = "aaaaaaaa-2222-3333-4444-555555555555"


def key(name, uuid=UUID, port=443, extra=""):
    return (BASE % (uuid, port)) + extra + "#" + urllib.parse.quote(name)


def b64(lines):
    return base64.b64encode("\n".join(lines).encode()).decode()


def other_client(conf, n=9):
    """The same server (its public key and endpoint), another client: another private key."""
    from tests.fakes_gw import _key
    return "\n".join("PrivateKey = %s" % _key(n) if ln.startswith("PrivateKey") else ln for ln in conf.splitlines()) + "\n"


class RenameCase(SubsCase):
    def setUp(self):
        super().setUp()
        self.scenario()                 # people, devices; the pool of the scenario is replaced below
        write_pool([])

    def route_all(self, eid, name="Мой выход", enabled=False):
        """A device, a person and the default route to the exit; its own name and switch."""
        with state.transaction() as st:
            g = st["gateway"]
            g["devices"][0]["route"] = {"via": "exit:" + eid, "fail": "block"}
            g["users"][1]["route"] = {"via": "exit:" + eid, "fail": "direct"}
            g["routes"]["default"] = {"via": "exit:" + eid, "fail": "block"}
            g["exits"][eid] = {"enabled": enabled, "name": name}

    def routes(self):
        g = state.load()["gateway"]
        return (g["devices"][0]["route"]["via"], g["users"][1]["route"]["via"], g["routes"]["default"]["via"],
                dict(g["exits"]))

    def ids(self):
        return [i for i, _ in exits.line_ids()]

    def names(self):
        return [e["name"] for e in exits.pool(self.w.runner)]


class VlessRenameTest(RenameCase):
    def test_the_name_changes_twice_the_exit_stays(self):
        self.add(IP_URL, b64([key("10GB left"), VLESS_B]))
        eid = self.ids()[0]
        self.assertEqual(eid, exits.exit_id(key("10GB left")))         # a new key: the id of its text, as before
        self.route_all(eid)
        before = self.routes()
        for name in ("9GB left", "8GB left"):
            self.feed.bodies[IP_URL] = b64([key(name), VLESS_B])
            r = self.refresh()["results"][0]
            self.assertEqual((r["added"], r["removed"], r["updated"]), (0, 0, 1), r["text"])
            self.assertIn("обновлено 1", r["text"])
            self.assertIn("маршруты устройств сохранены", r["text"])
            self.assertEqual(self.pool(), [key(name), VLESS_B])        # in its place: the same line number
            self.assertEqual(self.ids()[0], eid)                       # the id of the exit is the same
            self.assertEqual(self.names()[0], name)                    # the name is the new one
            self.assertEqual(self.routes(), before)                    # routes, name and switch untouched
            row = exitsubs.listing()[0]
            self.assertEqual((row["keys"], row["exits"]), (2, [eid, exits.exit_id(VLESS_B)]))
        self.assertIn("обновлено 1 (маршруты сохранены)", self.journal())
        # the exit the routes name is in the pool: no «выход удалён» anywhere
        self.assertIn(eid, set(e["id"] for e in exits.pool(self.w.runner)))
        # the same text again: nothing to do, the pool is not written
        n = len(self.w.script.replace_inputs)
        r = self.refresh()["results"][0]
        self.assertEqual(len(self.w.script.replace_inputs), n)
        self.assertIn("без изменений", r["text"])
        # the map keeps only what the pool needs (the name of the day, not every name it had)
        self.assertEqual(exitsubs.load()["ids"], {exits.exit_id(key("8GB left")): eid})

    def test_other_parameters_are_the_same_exit_too(self):
        self.add(IP_URL, key("Вена"))
        eid = self.ids()[0]
        self.feed.bodies[IP_URL] = key("Вена", extra="&flow=xtls-rprx-vision")
        r = self.refresh()["results"][0]
        self.assertEqual((r["updated"], self.ids()), (1, [eid]))

    def test_another_uuid_or_server_is_a_new_exit(self):
        self.add(IP_URL, key("10GB left"))
        eid = self.ids()[0]
        self.route_all(eid)
        for line in (key("9GB left", uuid="bbbbbbbb-2222-3333-4444-555555555555"), key("9GB left", port=8443)):
            self.feed.bodies[IP_URL] = line
            r = self.refresh()["results"][0]
            self.assertEqual((r["added"], r["removed"], r["updated"]), (1, 1, 0))
            self.assertEqual(self.ids(), [exits.exit_id(line)])
            self.assertNotIn("ids", exitsubs.load())
        self.assertEqual(self.routes()[0], "exit:" + eid)              # the routes are not moved to another key

    def test_a_key_removed_by_hand_does_not_come_back_under_another_name(self):
        self.add(IP_URL, b64([key("10GB left"), VLESS_B]))
        eid = self.ids()[0]
        self.feed.bodies[IP_URL] = b64([key("9GB left"), VLESS_B])
        self.refresh()
        ops.exit_del(self.w.runner, eid)                               # its line is «9GB left»: install.sh by the text
        self.assertEqual(self.pool(), [VLESS_B])
        self.assertEqual(exitsubs.listing()[0]["excluded"], 1)         # not folded yet: said already
        for name in ("8GB left", "7GB left"):
            self.feed.bodies[IP_URL] = b64([key(name), VLESS_B])
            r = self.refresh()["results"][0]
            self.assertEqual(self.pool(), [VLESS_B], name)
            self.assertEqual(exitsubs.listing()[0]["excluded"], 1)
        res = exitsubs.restore(self.w.runner, "1", fetcher=self.feed)  # «Вернуть исключённые» brings it back
        self.assertTrue(res["ok"])
        self.assertEqual(self.pool(), [VLESS_B, key("7GB left")])
        self.assertTrue(r["ok"])

    def test_a_manual_key_and_a_held_key_are_left_as_they_are(self):
        manual = key("руками")
        write_pool([manual])
        self.add(IP_URL, key("10GB left"))                             # the same key: not its own, not added
        self.feed.bodies[IP_URL] = key("9GB left")
        r = self.refresh()["results"][0]
        self.assertEqual((self.pool(), r["updated"]), ([manual], 0))
        self.assertEqual(self.ids(), [exits.exit_id(manual)])

    def test_the_pool_changed_meanwhile_keeps_the_id(self):
        self.add(IP_URL, b64([key("10GB left"), VLESS_B]))
        eid = self.ids()[0]
        self.feed.bodies[IP_URL] = b64([key("9GB left"), VLESS_B])
        self.w.script.replace_race = [[key("10GB left"), VLESS_B, VLESS_A]]
        self.refresh()
        self.assertEqual(self.pool(), [key("9GB left"), VLESS_B, VLESS_A])
        self.assertEqual(self.ids()[0], eid)

    def test_a_failed_rebuild_keeps_the_old_line_and_its_id(self):
        self.add(IP_URL, key("10GB left"))
        eid = self.ids()[0]
        self.feed.bodies[IP_URL] = key("9GB left")
        self.w.script.replace_rc = 1
        r = self.refresh()["results"][0]
        self.assertFalse(r["ok"])
        self.w.script.replace_rc = None
        self.assertEqual((self.pool(), self.ids()), ([key("10GB left")], [eid]))
        r = self.refresh()["results"][0]                               # the next refresh renames it
        self.assertEqual((r["updated"], self.ids()), (1, [eid]))


class UpgradeTest(RenameCase):
    def test_ids_of_3_3_4_stay(self):
        """subs.json of 3.3.4 (no «ids», no «idents»): every exit keeps the id of its text; the first rename of 3.3.5
        keeps that id."""
        lines = [key("10GB left"), VLESS_B, VLESS_A]
        write_pool(lines)
        old = [exits.exit_id(l) for l in lines]
        doc = {"schema": 1, "next": 2, "subs": [{"id": "s-abcdef", "n": 1, "url": IP_URL, "auto": True, "pin": "",
                                                 "keys": old[:2], "seen": old[:2], "present": old[:2],
                                                 "excluded": [], "added": "2026-10-14T00:00:00Z", "last_try": 1,
                                                 "last_ok": 1, "error": "", "count": 2, "full": 0}]}
        util.atomic_write(exitsubs.path(), json.dumps(doc), 0o600)
        self.route_all(old[0])
        self.assertEqual(self.ids(), old)
        self.assertEqual([e["id"] for e in exits.pool(self.w.runner)], old)
        self.feed.bodies[IP_URL] = b64(lines[:2])
        self.refresh()                                                  # nothing changed: the ids as they were
        self.assertEqual(self.ids(), old)
        self.feed.bodies[IP_URL] = b64([key("9GB left"), VLESS_B])
        r = self.refresh()["results"][0]
        self.assertEqual((r["updated"], self.ids()), (1, old))
        self.assertEqual(self.routes()[0], "exit:" + old[0])


class AwgRenameTest(RenameCase):
    def test_goamnezia_name_changes_twice_the_exit_stays(self):
        a, b = conf_of("2.0", 3), conf_of("3.1", 4)
        self.add(GA_URL, "\n".join([vpn_of(a, "Аня · 10GB"), vpn_of(b, "Аня · B")]))
        eid = self.ids()[0]
        self.route_all(eid)
        before = self.routes()
        for name in ("Аня · 9GB", "Аня · 8GB"):
            self.feed.bodies[GA_URL] = "\n".join([vpn_of(a, name), vpn_of(b, "Аня · B")])
            r = self.refresh()["results"][0]
            self.assertEqual((r["added"], r["removed"], r["updated"]), (0, 0, 1), r["text"])
            self.assertEqual(self.ids()[0], eid)
            self.assertEqual(self.names()[0], name)
            self.assertEqual(self.routes(), before)

    def test_another_client_of_the_same_server_is_a_new_exit(self):
        a = conf_of("2.0", 3)
        self.add(GA_URL, vpn_of(a, "Аня"))
        eid = self.ids()[0]
        self.feed.bodies[GA_URL] = vpn_of(other_client(a), "Аня")
        r = self.refresh()["results"][0]
        self.assertEqual((r["added"], r["removed"], r["updated"]), (1, 1, 0))
        self.assertNotEqual(self.ids()[0], eid)

    def test_another_address_of_the_same_key_is_the_same_exit(self):
        """3.3.5 №4: the identity of an AmneziaWG key is the server key + the client key, no address: the subscription
        now gives the key by domain instead of by IP — the same exit, its id and routes stay."""
        self.add(GA_URL, vpn_of(conf_of("2.0", 3), "Аня"))
        eid = self.ids()[0]
        self.route_all(eid)
        before = self.routes()
        self.feed.bodies[GA_URL] = vpn_of(conf_of("2.0", 3, endpoint="vpn.example.com:51820"), "Аня")
        r = self.refresh()["results"][0]
        self.assertEqual((r["added"], r["removed"], r["updated"]), (0, 0, 1))
        self.assertEqual(self.ids(), [eid])
        self.assertEqual(self.routes(), before)

    def test_another_server_is_a_new_exit(self):
        self.add(GA_URL, vpn_of(conf_of("2.0", 3), "Аня"))
        eid = self.ids()[0]
        other = conf_of("2.0", 3).replace(fake_key(103), fake_key(104))          # another server key, the same client
        self.feed.bodies[GA_URL] = vpn_of(other, "Аня")
        r = self.refresh()["results"][0]
        self.assertEqual((r["added"], r["removed"], r["updated"]), (1, 1, 0))
        self.assertNotEqual(self.ids()[0], eid)


class EnglishTest(RenameCase):
    def test_the_words_in_english(self):
        from kaskad.i18n import use_lang
        self.add(IP_URL, key("10GB left"))
        self.feed.bodies[IP_URL] = key("9GB left")
        with use_lang("en"):
            r = self.refresh()["results"][0]
        self.assertIn("updated 1 (a new name or parameters of the key)", r["text"])
        self.assertIn("the routes of the devices are kept", r["text"])


class BackupIdsTest(BackupCase):
    def test_the_copy_keeps_the_ids(self):
        self.old_node()
        renamed = key("9GB left")
        write_pool([VLESS_A, renamed])
        kept = exits.exit_id(key("10GB left"))
        doc = exitsubs.load()
        doc["subs"][0]["keys"] = doc["subs"][0]["seen"] = [kept]
        doc["ids"] = {exits.exit_id(renamed): kept}
        exitsubs.save(doc)
        self.ok("gw", "device", "route", self.did, "exit:" + kept)
        self.assertEqual([i for i, _ in exits.line_ids()][1], kept)
        text = backup.export(self.w.runner, PW)["text"]
        self.new_node()
        self.assertEqual([i for i, _ in exits.line_ids()], [])
        backup.apply(self.w.runner, backup.unseal(self.w.runner, text, PW))
        self.assertEqual(exitsubs.load()["ids"], {exits.exit_id(renamed): kept})
        self.assertEqual([i for i, _ in exits.line_ids()], [exits.exit_id(VLESS_A), kept])
        d = [x for x in model.devices(state.load()) if x["id"] == self.did][0]
        self.assertEqual(d["route"]["via"], "exit:" + kept)
        self.assertTrue(os.path.exists(paths.egress_up()))
