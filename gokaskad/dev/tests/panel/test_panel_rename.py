"""T13 N5 (SPEC-stage1-rename §4.1): the routes of the renames — 200, 400, 404, 409 (a person — no override; others —
``confirm_duplicate`` and the repeat with ``allow_duplicate``), no ``rev`` needed and the ``rev`` unchanged, the version
of a route untouched by a name (two tabs: one renames, the other saves the route with the ``ver`` it read)."""

from kaskad import state
from kaskad.bot import aliases
from kaskad.gw import exitsubs, model, ops
from tests.panel.panelcase import PanelCase


def json_names(body):
    import json
    out = []

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if k in ("name", "label", "title") and isinstance(v, str):
                    out.append(v)
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(json.loads(body))
    return out


class RenameApiTest(PanelCase):
    def setUp(self):
        super().setUp()
        self.w.runner.on(["systemd-run"], lambda argv, inp: (0, "", ""))
        self.gateway()
        self.u2 = ops.user_add(self.w.runner, "Дача")["id"]
        self.did = ops.device_add(self.w.runner, self.uid, "Телефон", kind="phone", platform="android",
                                  access=["wg"])["device"]["id"]
        self.d2 = ops.device_add(self.w.runner, self.uid, "Ноутбук")["device"]["id"]
        self.collect()

    def rev(self):
        return self.ok("GET", "/api/gw/exits")["rev"]

    def test_person(self):
        rev = self.rev()
        doc = self.ok("PUT", "/api/gw/users/%s/name" % self.uid, {"name": "Мама Оля"})
        self.assertEqual((doc["id"], doc["old"], doc["name"], doc["what"]), (self.uid, "Мама", "Мама Оля", "user"))
        self.assertTrue(doc["note"])
        people = self.ok("GET", "/api/gw/people")
        self.assertIn("Мама Оля", [u["name"] for u in people["users"]])
        self.assertEqual(self.rev(), rev)                                  # a name is outside the version
        doc = self.err(409, "PUT", "/api/gw/users/%s/name" % self.uid, {"name": "дача", "allow_duplicate": True})
        self.assertEqual(doc["code"], "user_exists")
        doc = self.err(400, "PUT", "/api/gw/users/%s/name" % self.uid, {"name": "x" * 41})
        self.assertEqual(doc["data"]["reason"], "long")
        self.err(400, "PUT", "/api/gw/users/%s/name" % self.uid, {"name": ""})
        self.err(400, "PUT", "/api/gw/users/%s/name" % self.uid, {})
        self.err(400, "PUT", "/api/gw/users/%s/name" % self.uid, {"name": 5})
        self.assertEqual(self.err(404, "PUT", "/api/gw/users/u-00000000/name", {"name": "x"})["code"], "gone")

    def test_device_duplicate_then_anyway(self):
        d = model.get_device(state.load(), self.did)
        doc = self.err(409, "PUT", "/api/gw/devices/%s/name" % self.did, {"name": "ноутбук"})
        self.assertEqual((doc["code"], doc["data"]["need"], doc["data"]["duplicates"]),
                         ("confirm_duplicate", "confirm_duplicate", [self.d2]))
        doc = self.ok("PUT", "/api/gw/devices/%s/name" % self.did, {"name": "ноутбук", "allow_duplicate": True})
        self.assertEqual(doc["duplicates"], [self.d2])
        self.assertIn("второй туннель", doc["note"])
        d0 = d
        d = model.get_device(state.load(), self.did)
        self.assertEqual(d["name"], "ноутбук")
        self.assertEqual(d["login"], d0["login"])                    # REV-s1-t13 Л-2: the login whole, not a prefix

    def test_a_route_saved_in_another_tab_after_a_rename(self):
        """Two tabs: the first read the device (its ``ver``), the second renamed it — the route of the first is saved
        without 409: the ``ver`` of a route holds only the route."""
        view = self.ok("PUT", "/api/gw/devices/%s/route?dry=1" % self.did, {"via": "direct"})
        self.ok("PUT", "/api/gw/devices/%s/name" % self.did, {"name": "Телефон Оли"})
        self.ok("PUT", "/api/gw/devices/%s/route" % self.did, {"via": "direct", "ver": view["ver"]})
        d = model.get_device(state.load(), self.did)
        self.assertEqual((d["name"], d["route"]["via"]), ("Телефон Оли", "direct"))

    def test_exit_name_light_and_reset(self):
        rev = self.rev()
        doc = self.ok("PUT", "/api/gw/exits/%s" % self.vid, {"name": "Вена"})
        self.assertEqual((doc["name"], doc["shown"], doc["rev"]), ("Вена", "Вена", rev))
        self.assertEqual(doc["note"], "Сам ключ и настройки не меняются.")
        doc = self.ok("PUT", "/api/gw/exits/%s" % self.vid, {"name": ""})
        self.assertEqual(doc["name"], "")
        self.assertNotIn("name", state.load()["gateway"]["exits"].get(self.vid, {}))
        doc = self.err(400, "PUT", "/api/gw/exits/%s" % self.vid, {"name": "x" * 41})   # no silent cut any more
        self.assertEqual(doc["data"]["reason"], "long")

    def test_inbound_label(self):
        doc = self.ok("PUT", "/api/gw/inbounds/wg/label", {"label": "для роутеров"})
        self.assertTrue(doc["shown"].endswith(" — для роутеров"))
        rows = dict((r["kind"], r) for r in self.ok("GET", "/api/gw/inbounds")["inbounds"])
        self.assertEqual((rows["wg"]["label"], rows["wg"]["shown"]), ("для роутеров", doc["shown"]))
        self.ok("PUT", "/api/gw/inbounds/wg/label", {"label": ""})
        self.assertNotIn("label", model.inbound(state.load(), "wg"))
        self.assertEqual(self.err(404, "PUT", "/api/gw/inbounds/pptp/label", {"label": "x"})["code"], "gone")

    def test_sub_label_and_auto(self):
        exitsubs.save({"schema": 1, "next": 2, "subs": [{"id": "s-aaaaaa", "n": 1, "url": "https://sub.example.com/a",
                                                          "keys": [], "auto": True}]})
        doc = self.ok("PUT", "/api/gw/exit-subs/s-aaaaaa", {"label": "Моя"})
        self.assertEqual((doc["shown"], doc["name"]), ("Моя", "Моя"))
        subs = self.ok("GET", "/api/gw/exits")["subs"]
        self.assertEqual([(s["name"], s["label"], s["domain"]) for s in subs], [("Моя", "Моя", "sub.example.com")])
        doc = self.ok("PUT", "/api/gw/exit-subs/s-aaaaaa", {"auto": False})
        self.assertFalse(doc["sub"]["auto"])
        self.assertEqual(self.ok("PUT", "/api/gw/exit-subs/s-aaaaaa", {"label": ""})["shown"], "sub.example.com")
        self.err(400, "PUT", "/api/gw/exit-subs/s-aaaaaa", {})
        self.assertEqual(self.err(404, "PUT", "/api/gw/exit-subs/s-bbbbbb", {"label": "x"})["code"], "gone")

    def test_forward(self):
        f = state.load()["forwards"][0]
        path = "/api/forwards/%s/%d/name" % (f["proto"], f["port"])
        doc = self.ok("PUT", path, {"name": "Сайт"})
        self.assertEqual((doc["name"], doc["id"]), ("Сайт", "%s:%d" % (f["proto"], f["port"])))
        self.assertEqual([r["name"] for r in self.ok("GET", "/api/forwards")["forwards"]], ["Сайт"])
        self.assertEqual(self.ok("PUT", path, {"name": ""})["shown"], "%s :%d" % (f["proto"], f["port"]))
        self.err(400, "PUT", path, {"name": "a|b"})
        self.assertEqual(self.err(404, "PUT", "/api/forwards/udp/9/name", {"name": "x"})["code"], "gone")

    def test_forward_duplicate_then_anyway(self):
        """REV-s1-t13 Л-2: the panel passes ``allow_duplicate`` to the forwards too (409 — then «Всё равно сохранить»)."""
        st = state.load()
        f = st["forwards"][0]
        other = dict(f, port=f["port"] + 1 if f["port"] < 65535 else f["port"] - 1, name="Сайт")
        st["forwards"].append(other)
        state.save(st)
        path = "/api/forwards/%s/%d/name" % (f["proto"], f["port"])
        oid = "%s:%d" % (other["proto"], other["port"])
        doc = self.err(409, "PUT", path, {"name": "сайт"})            # the page asks by 409 + data.need (Kit.errKind)
        self.assertEqual((doc["data"]["need"], doc["data"]["duplicates"]), ("confirm_duplicate", [oid]))
        doc = self.ok("PUT", path, {"name": "сайт", "allow_duplicate": True})
        self.assertEqual((doc["name"], doc["duplicates"]), ("сайт", [oid]))
        self.assertEqual(sorted(r["name"] for r in self.ok("GET", "/api/forwards")["forwards"]), ["Сайт", "сайт"])

    def test_the_scheme_and_the_people_show_the_new_name(self):
        self.ok("PUT", "/api/gw/devices/%s/name" % self.did, {"name": "<b>Телефон Оли</b>"})
        self.ok("PUT", "/api/gw/users/%s/name" % self.uid, {"name": "Оля & Ко"})
        body = self.call("GET", "/api/scheme").body.decode("utf-8")
        self.assertIn("<b>Телефон Оли</b>", json_names(body))       # the data as it is: the page shows it as text
        self.assertIn("Оля & Ко", json_names(body))
        people = self.ok("GET", "/api/gw/people")
        self.assertIn("<b>Телефон Оли</b>", [d["name"] for d in people["devices"]])

    def test_create_refuses_long_names(self):
        self.err(400, "POST", "/api/gw/users", {"name": "x" * 41})
        self.err(400, "POST", "/api/gw/devices", {"user": self.uid, "name": "a|b"})
        self.ok("POST", "/api/gw/users", {"name": "y" * 40})


class NoServerRenameInPanelTest(PanelCase):
    def test_no_route(self):
        """Servers of the monitor — renamed in the panel once it has their section (§4.1); today CLI/SSH/bot only."""
        aliases.update("203.0.113.5", name="Вена")
        self.assertEqual(self.call("PUT", "/api/servers/203.0.113.5/name", {"name": "x"}).status, 404)
