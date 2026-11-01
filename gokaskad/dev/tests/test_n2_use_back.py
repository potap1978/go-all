"""Н-2, остаток (R-n2.md, А6): «Отменить импорт» возвращает SSTP и OpenConnect на сертификат домена.

Снимок сервера хранит ``use`` части ``site`` (формат части ``v`` 2, ``backup.SITE_V``); ``backup undo`` на том же
сервере, когда сертификат снова выдан (``_site_back``), включает их обратно, как ``site.ops._switch_back``; не вышло —
заметка своими словами. Старый снимок (часть без ``v``) читается как раньше: SSTP и OpenConnect не трогаются. Полезная
нагрузка остаётся ``v`` 1 — старый Kaskad читает копию и снимок новой версии."""

import json
import os
import shutil

from kaskad import backup, state
from kaskad.site import model as smodel
from kaskad.site import ops as site_ops
from tests import test_gw_vless_front as F
from tests.test_n2_undo_site import _UndoCase


class UseBackTest(_UndoCase):
    def setUp(self):
        super().setUp()
        for k in ("sstp", "ocserv"):
            site_ops.use(self.w.runner, k, True)
        self.assertEqual(smodel.peek(state.load())["use"], {"sstp": True, "ocserv": True})

    def use(self):
        return smodel.peek(state.load())["use"]

    def test_the_snapshot_holds_use_in_part_v2(self):
        path = backup.take_snapshot(self.w.runner)
        site = json.loads(backup.read_snapshot(path)["files"][backup.F_STATE])["site"]
        self.assertEqual((site["v"], site["use"]), (2, {"sstp": True, "ocserv": True}))
        self.assertEqual(backup.VERSION, 1)                          # an older Kaskad still reads it

    def test_undo_brings_sstp_and_openconnect_back(self):
        backup.apply(self.w.runner, self.other_copy())
        self.assertEqual(self.use(), {"sstp": False, "ocserv": False})   # the import: another domain, no certificate
        res = backup.undo(self.w.runner)
        self.assert_as_before(res)
        self.assertEqual(self.use(), {"sstp": True, "ocserv": True})
        self.assertFalse([n for n in res["notes"] if "SSTP" in n or "OpenConnect" in n], res["notes"])
        self.assertFalse([w for w in res["warnings"] + res["text"] if "выдайте" in w and "SSTP" in w])
        self.assertIn("BACKUP undo: SSTP снова на сертификате домена %s" % F.DOMAIN, self.journal())
        self.assertEqual(self.use(), {"sstp": True, "ocserv": True})
        res = backup.undo(self.w.runner)                             # the stand undid twice
        self.assertEqual(self.use(), {"sstp": True, "ocserv": True})

    def test_without_the_certificate_it_says_so(self):
        backup.apply(self.w.runner, self.other_copy())
        shutil.rmtree(smodel.live_dir(F.DOMAIN))
        res = backup.undo(self.w.runner)
        self.assertEqual(self.use(), {"sstp": False, "ocserv": False})
        notes = " | ".join(res["notes"])
        self.assertIn("SSTP до импорта работал на сертификате домена %s" % F.DOMAIN, notes)
        self.assertIn("OpenConnect до импорта работал на сертификате домена", notes)

    def test_an_old_snapshot_without_the_flag(self):
        """Migration: a snapshot of an older Kaskad (the part ``site`` without ``v``/``use``) — read and applied as
        before; SSTP and OpenConnect are left as they are, no note about them."""
        backup.apply(self.w.runner, self.other_copy())
        path = backup.last_snapshot()
        rel = backup.F_STATE
        with open(os.path.join(path, rel), encoding="utf-8") as f:
            part = json.load(f)
        part["site"].pop("v")
        part["site"].pop("use")
        text = json.dumps(part, ensure_ascii=False, indent=1) + "\n"
        with open(os.path.join(path, rel), "w", encoding="utf-8") as f:
            f.write(text)
        mpath = os.path.join(path, "manifest.json")
        with open(mpath, encoding="utf-8") as f:
            m = json.load(f)
        m["files"][rel] = {"sha256": backup._sha(text), "size": len(text.encode("utf-8"))}
        with open(mpath, "w", encoding="utf-8") as f:
            json.dump(m, f)
        # k3-select: files=!* — the snapshot of the run (a temp root), no file of the repository
        res = backup.undo(self.w.runner, path=path)
        self.assert_as_before(res)
        self.assertEqual(self.use(), {"sstp": False, "ocserv": False})
        self.assertFalse([n for n in res["notes"] if "SSTP" in n or "OpenConnect" in n], res["notes"])

    def test_a_copy_of_another_server_leaves_them(self):
        def edit(stj):
            stj["site"]["domain"] = F.DOMAIN
        backup.apply(self.w.runner, self.other_copy())
        res = backup.apply(self.w.runner, self.copy(edit))           # not same_node: never takes the cert on disk
        self.assertEqual(self.use(), {"sstp": False, "ocserv": False})
        self.assertFalse([n for n in res["notes"] if "SSTP" in n], res["notes"])


if __name__ == "__main__":
    import unittest
    unittest.main()
