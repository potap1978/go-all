"""Q6/Q7, the migration (the owner's rule 24.10): the side of a RELEASED version. ``tests/test_q67_migration.py``
copies this file into the tree of a commit of 3.3.7.8 / 3.3.7.9 and runs it there (``Q67_MODE``, ``Q67_IO``):

* ``make`` — what that version shows for the scenario «Дом» (a Russian group, ⚠РФ and the consent, «Россия
  напрямую»): A — nobody chose the group; B — «Мама» chose it (``rules.ops.set_ru_via`` of that version) and the group
  is on its last step. Saved: its state.json, ``live.no_exit`` of the decision ``DEC``, the people of the panel
  (``panel.api._people_view``) on the live.json ``live_doc`` and the pins ``PINS``, ``gw groups`` (``policy_ops.groups``);
* ``read`` — the rollback: that version reads the data written by the new one (state.json with ``ru_via`` written by
  ``kaskadctl rules ru-via``, live.json with ``no_exit_ru``) without an error.

Only what both versions have is used. Not a test of this tree: no ``test_`` in the name, every case is skipped
without ``Q67_MODE``."""

import json
import os
import time
import unittest

from kaskad import paths, snapshot, state
from kaskad.gw import live
from kaskad.gw import policy as P
from tests.gw.test_ru_target import D1, HOME, RuWorld

MODE = os.environ.get("Q67_MODE")
IO = os.environ.get("Q67_IO", "")
NOW = 1761300000


def dec(st, ru_steps):
    """The decision of a tick: «Основная» with every device fine, «Дом» with ``ru_steps`` ({device: step})."""
    pol = P.load(st)
    return {"policy": pol, "groups": {
        P.MAIN: {"steps": {"d-00000001": "exit", "d-00000002": "exit", "d-00000003": "exit"}, "last_since": None},
        HOME: {"steps": dict(ru_steps), "last_since": NOW - 600 if ru_steps else None}}}


def live_doc(no_exit, extra=None):
    """live.json as a collector writes it (the keys the panel reads)."""
    doc = {"v": live.V, "ts": time.time(), "seq": 1, "no_exit": no_exit, "events": [], "exits": {}, "devices": {},
           "policy": {"node_offline": False}}
    doc.update(extra or {})
    return doc


def pins(ru_ids):
    return {"version": 1, "saved": NOW, "groups": {
        HOME: {"pins": {D1: {"exit": ru_ids[0], "tier": "t1", "since": NOW, "why": "new"}}}}}


def write(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, sort_keys=True)
    snapshot.forget()


def put(name, obj):
    with open(os.path.join(IO, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, sort_keys=True)


def get(name):
    with open(os.path.join(IO, name), encoding="utf-8") as f:
        return json.load(f)


def need(mode):
    if MODE != mode:
        raise unittest.SkipTest("Q67_MODE=%s" % mode)


def people():
    from kaskad.panel import api
    snapshot.forget()
    return api._people_view(True)


class Make(RuWorld):
    def setUp(self):
        need("make")
        super().setUp()

    def test_a_nobody_chose_the_group(self):
        st = self.world(via=None)
        d = dec(st, {})
        write(paths.live_json(), live_doc(live.no_exit(d)))
        write(paths.pins_json(), pins(self.ru_ids))
        put("a_state.json", state.load())
        put("a_no_exit.json", live.no_exit(d))
        put("a_people.json", people())
        from kaskad.gw import policy_ops
        put("a_groups.json", policy_ops.groups())

    def test_b_the_group_chosen_and_down(self):
        self.world(via=None)
        from kaskad.rules import ops
        ops.set_ru_via(self.w.runner, "user", state.load()["gateway"]["users"][0]["id"], HOME)
        st = state.load()
        d = dec(st, {D1: "last_block"})
        write(paths.live_json(), live_doc(live.no_exit(d)))
        write(paths.pins_json(), pins(self.ru_ids))
        put("b_state.json", state.load())
        put("b_no_exit.json", live.no_exit(d))
        put("b_people.json", people())


class Read(RuWorld):
    def setUp(self):
        need("read")
        super().setUp()

    def test_the_data_of_the_new_version(self):
        self.world(via=None)
        state.save(get("new_state.json"))
        write(paths.live_json(), get("new_live.json"))
        write(paths.pins_json(), get("new_pins.json"))
        doc = people()
        from kaskad.gw import model, policy_ops
        from kaskad.rules import ops
        st = state.load()
        put("read.json", {"people": doc, "groups": policy_ops.groups(), "rules": ops.status(st),
                          "ru_via": dict((u["id"], model.ru_via_of(st, {"user": u["id"]})["via"])
                                         for u in st["gateway"]["users"])})
