"""3.3.7.10, the migration (the owner's rule 24.10): the side of a RELEASED version. ``tests/test_33710_migration.py``
copies this file into the tree of a commit of 3.3.7.8 / 3.3.7.9 and runs it there (``S3310_MODE``, ``S3310_IO``):

* ``make`` — the data of that version: a user «Мама» with two devices («Телефон»: VLESS, Hysteria2, AmneziaWG 2.0;
  «Ноутбук»: WireGuard, VLESS), the personal page and the subscription on, a key issued in the window; what that
  version says of them — the tokens, the addresses, the body of the subscription, the keys of the page, the status of
  the page and of the subscription, the users and devices of the panel; its state.json, secrets.json and the marks of
  the window (inbound-seen.json);
* ``read`` — the rollback: that version on the state.json, secrets.json, marks and live.json written by the new one (live.json
  with the open window of activity and the minute of metrics.db, 3.3.7.10): the tokens and the addresses, its own body
  of the subscription, the overview and the users of the panel — no error; what it says, for the new side to judge.

The world (``SubWorld``) is shared with the new side. Only what all three versions have is used. Not a test of this
tree: no ``test_`` in the name, every case is skipped without ``S3310_MODE``."""

import json
import os
import shutil
import unittest

from kaskad import paths, snapshot, state
from kaskad.gw import inlog, metrics, model, registry
from kaskad.gw.inbounds.base import DummyInbound
from kaskad.panel import api, userpage
from tests.test_gw_hy2 import Hy2Case

MODE = os.environ.get("S3310_MODE")
IO = os.environ.get("S3310_IO", "")
FILES = (("state.json", paths.state), ("secrets.json", paths.secrets_file),
         ("inbound-seen.json", inlog.seen_path))          # the marks of the window: «выдали», «порт закрыт?»


def put(name, obj):
    with open(os.path.join(IO, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, sort_keys=True)


def get(name, io=None):
    with open(os.path.join(io or IO, name), encoding="utf-8") as f:
        return json.load(f)


def need(mode):
    if MODE != mode:
        raise unittest.SkipTest("the old side of tests/test_33710_migration.py (%s)" % mode)


class ConfInbound(DummyInbound):
    """A dummy of WireGuard / AmneziaWG whose .conf has the form of the real one ([Interface] … [Peer]) — as
    tests/test_sub_awg.py has it (the old trees have no such file)."""

    def __init__(self, line=None, **kw):
        super().__init__(**kw)
        self._line = line

    def line(self, st):
        return self._line

    def issue(self, st, sec, device, os=None):
        rec = dict(super().issue(st, sec, device, os)["items"][0])
        rec["body"] = "[Interface]\n%s%s\n[Peer]\nPublicKey = SERVER-%s\n" % (
            rec["body"], "Jc = 4\n" if self._line else "", self.kind)
        return dict(rec, inbound=self.kind, items=[rec])


class SubWorld(Hy2Case):
    """The gateway with VLESS, Hysteria2, AmneziaWG 2.0 and WireGuard; «Мама» with «Телефон» and «Ноутбук»."""

    def dummies(self):
        out = {}
        for kind, kw in (("wg", {"title": "WireGuard", "ifaces": ["kgw-wg"]}),
                         ("awg2", {"title": "AmneziaWG 2.0", "line": "2.0", "default_port": 41023,
                                   "ifaces": ["kgw-awg2"]})):
            out[kind] = ConfInbound(kind=kind, **kw)
            registry.register(out[kind])
        return out

    def world(self):
        self.install()                                              # the gateway, hy2, the user «Мама»
        for kind in ("vless", "awg2", "wg"):
            rc, _, err = self.run_json("gw", "inbound", "install", kind)
            self.assertEqual(rc, 0, err)
        phone = self.add_device(name="Телефон", platform="android", access="vless,hy2,awg2")
        self.add_device(name="Ноутбук", platform="windows", access="wg,vless")
        uid = phone["user"]
        userpage.enable(uid)
        userpage.sub_enable(uid)
        return uid, phone["id"]

    def put_files(self, io, names=("state.json", "secrets.json", "inbound-seen.json")):
        """The data of the other side in place of this world's own (its fake node stays)."""
        for name, path in FILES + (("live.json", paths.live_json),):
            if name in names:
                os.makedirs(os.path.dirname(path()), exist_ok=True)
                shutil.copyfile(os.path.join(io, name), path())
        snapshot.forget()

    def facts(self, uid):
        """What this version says of the user — the same keys on both sides."""
        snapshot.forget()
        st = state.load()
        user = model.find_user(st, uid)
        page, sub = userpage._stored_tokens()[uid], userpage._stored_sub_tokens()[uid]
        keys = userpage.build_keys(user, page, self.w.runner)
        status, sub_status = userpage.status(uid), userpage.sub_status(uid)
        people = api._people_view(True)
        return {
            "uid": uid, "page": page, "sub": sub,
            "page_of": userpage.find_token(page), "sub_of": userpage.find_token(sub, sub=True),
            "page_as_sub": userpage.find_token(page, sub=True), "sub_as_page": userpage.find_token(sub),
            "url": userpage.url(uid), "sub_url": userpage.sub_url(uid),
            "status": [status["enabled"], status["path"], status["user"]],
            "sub_status": [sub_status["enabled"], sub_status["path"], sub_status["devices"], sub_status["user"]],
            "links": userpage.build_sub(user, self.w.runner)[1],
            "keys": [[d["id"], d["name"], d["enabled"],
                      [[i["kind"], [[it.get("format"), it.get("body")] for it in i["items"]]] for i in d["inbounds"]]]
                     for d in keys["devices"]],
            "users": sorted([u["id"], u["name"], u.get("enabled", True) is not False, u["page"], u["sub"]]
                           for u in people["users"]),
            "devices": sorted([d["id"], d["user"], d["name"], sorted(d.get("access") or []),
                               d.get("enabled", True) is not False] for d in people["devices"]),
            "schema": metrics.SCHEMA,
        }


class Make(SubWorld):
    def setUp(self):
        need("make")
        super().setUp()

    def test_make(self):
        uid, phone = self.world()
        rc, _, err = self.run_json("gw", "issue", phone, "--inbound", "awg2")      # the window: «выдали»
        self.assertEqual(rc, 0, err)
        put("made.json", self.facts(uid))
        for name, path in FILES:
            shutil.copyfile(path(), os.path.join(IO, name))


class Read(SubWorld):
    """The rollback: this version reads what the new one wrote."""

    def setUp(self):
        need("read")
        super().setUp()

    def test_read(self):
        self.world()                                                # the fake node; the data are the new side's
        self.put_files(IO, ("state.json", "secrets.json", "inbound-seen.json", "live.json"))
        uid = get("new.json")["uid"]
        out = self.facts(uid)
        doc, age = api._live_now()[:2]
        out["live_keys"] = sorted(doc)
        out["overview"] = sorted(api._with_live(api._overview_core(True), doc, age, True))
        put("read.json", out)
