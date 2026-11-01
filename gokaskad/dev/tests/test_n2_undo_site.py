"""Н-2 (stand/logs/T-3379.md): «Отменить импорт» (``backup undo``) on the same server after a copy with ANOTHER domain
brings back the site, the certificate of the domain and VLESS on TLS as they were before the import — the certificate
is still on disk; before the fix the undo left the domain in mode «domain» with no active certificate and VLESS on
Reality («waiting for the domain certificate»), and said «issue a certificate», «VLESS ran in TLS mode» on the old
server — wrong for the same server. The failure rollback of an import (``exact``) is the same path.

A copy of another server (not ``same_node``) never takes a certificate that happens to lie on this disk: the node
does not switch by itself (SPEC-vless-tls-default, решение владельца 20.10)."""

import shutil
import types
import unittest
from unittest import mock

from kaskad import backup, state
from kaskad.gw import model
from kaskad.site import front
from kaskad.site import model as smodel
from kaskad.site import ops as site_ops
from tests import test_gw_vless_front as F
from tests import test_vless_mode as VM

OTHER = "other.example.net"


class _UndoCase(VM._Case):
    copy = VM.ImportCopyTest.copy                                     # export of this node, the site part edited

    def setUp(self):
        super().setUp()
        from tests.test_backup import FakeOpenssl
        self.w.runner.binaries.add("openssl")
        self.w.runner.on(["openssl"], FakeOpenssl(self.w.runner))
        for p in (mock.patch.object(backup, "ITER", 100000),
                  mock.patch.object(backup, "_here_ip", lambda runner: state.load()["gateway"].get("host") or "")):
            p.start()
            self.addCleanup(p.stop)
        site_ops.mode(self.w.runner, "site", yes=True)              # the node before the import: site + VLESS TLS
        self.assertEqual(self.site_mode(), "site")
        self.assertEqual(self.rec()["security"], "tls")
        self.before = self.link_fields()

    def other_copy(self, mode="domain"):
        def edit(stj):
            stj["site"]["domain"] = OTHER
            stj["site"]["mode"] = mode
        return self.copy(edit)

    def assert_as_before(self, res):
        s = smodel.peek(state.load())
        self.assertEqual((s["domain"], s["mode"], s["cert"]), (F.DOMAIN, "site", "letsencrypt"))
        self.assertIsNotNone(smodel.active(state.load()))            # the copy for the consumers is of the domain
        rec = self.rec()
        self.assertEqual((rec["security"], rec["port"]), ("tls", 443))
        self.assertTrue(front.xray_fronts())
        self.assertEqual(self.link_fields(), self.before)            # the links given out work again
        notes = " | ".join(res["notes"])
        self.assertNotIn("выпустите сертификат", notes)
        self.assertNotIn("VLESS на старом сервере", notes)
        self.assertNotIn("включите сайт", notes.lower())
        # the gateway part had said «VLESS: режим … — выдайте настройки заново» (Reality while the domain had no
        # certificate yet): the links came back — the line goes, else it is a false alarm
        self.assertFalse([w for w in res["warnings"] if "VLESS" in w], res["warnings"])
        self.assertFalse([t for t in res["text"] if "выдайте" in t and "VLESS" in t], res["text"])


class UndoBringsTheSiteBackTest(_UndoCase):
    def test_undo_after_a_copy_with_another_domain(self):
        backup.apply(self.w.runner, self.other_copy())
        s = smodel.peek(state.load())
        self.assertEqual((s["domain"], s["cert"]), (OTHER, ""))      # the import: the copy's domain, no certificate
        self.assertEqual(self.rec()["security"], "reality")
        res = backup.undo(self.w.runner)
        self.assert_as_before(res)
        # the renewal of the domain is ours again (autorenew, our hook) — the import had switched it off
        from kaskad.site import acme
        self.assertEqual(acme.hook_state(F.DOMAIN), "ours")
        self.assertIn("BACKUP undo: сертификат домена %s снова выдан" % F.DOMAIN, self.journal())

    def test_undo_of_a_node_with_the_site_off_keeps_vless_on_reality(self):
        """The node before the import had the certificate and the site off (VLESS on Reality, devices with its links):
        the certificate comes back, the site and TLS do not — the undo never switches a working node by itself."""
        site_ops.mode(self.w.runner, "domain", yes=True)
        self.assertEqual(self.rec()["security"], "reality")
        before = self.link_fields()
        backup.apply(self.w.runner, self.other_copy())
        res = backup.undo(self.w.runner)
        s = smodel.peek(state.load())
        self.assertEqual((s["domain"], s["mode"], s["cert"]), (F.DOMAIN, "domain", "letsencrypt"))
        self.assertEqual(self.rec()["security"], "reality")
        self.assertEqual(self.link_fields(), before)
        self.assertFalse(res["notes"], res["notes"])

    def test_undo_twice_keeps_it(self):
        """The stand undid twice (11:53 and 11:56): the second undo returns the same snapshot again."""
        backup.apply(self.w.runner, self.other_copy(mode="site"))
        backup.undo(self.w.runner)
        res = backup.undo(self.w.runner)
        self.assert_as_before(res)

    def test_the_rollback_of_a_failed_import_brings_the_site_back(self):
        """A part failing after «site» was applied — the rollback (``exact``) is a snapshot of this very server too."""
        doc = self.other_copy()
        boom = RuntimeError("boom")
        real = backup._APPLY["site"]

        def site_then_fail(ctx):
            real(ctx)
            raise boom
        with mock.patch.dict(backup._APPLY, {"site": site_then_fail}):
            with self.assertRaises(backup.KaskadError):
                backup.apply(self.w.runner, doc)
        s = smodel.peek(state.load())
        self.assertEqual((s["domain"], s["mode"], s["cert"]), (F.DOMAIN, "site", "letsencrypt"))
        self.assertEqual(self.rec()["security"], "tls")
        self.assertEqual(self.link_fields(), self.before)

    def test_without_the_certificate_on_disk_it_says_so(self):
        """The certificate gone from the disk meanwhile: the undo cannot bring TLS back — the note says to issue it
        (as before), nothing raises."""
        backup.apply(self.w.runner, self.other_copy())
        shutil.rmtree(smodel.live_dir(F.DOMAIN))
        res = backup.undo(self.w.runner)
        s = smodel.peek(state.load())
        self.assertEqual((s["domain"], s["cert"]), (F.DOMAIN, ""))
        self.assertEqual(self.rec()["security"], "reality")
        notes = " | ".join(res["notes"])
        self.assertIn("сертификата на этом сервере больше нет или он истёк", notes)   # the words of this server
        self.assertIn("VLESS до импорта работал в режиме TLS", notes)
        self.assertNotIn("A-запись", notes)
        self.assertNotIn("на старом сервере", notes)

    def test_an_expired_certificate_is_not_handed_out(self):
        backup.apply(self.w.runner, self.other_copy())
        self.clock = self.clock + 400 * 86400                         # past the end of the certificate on disk
        res = backup.undo(self.w.runner)
        s = smodel.peek(state.load())
        self.assertEqual((s["domain"], s["mode"], s["cert"]), (F.DOMAIN, "domain", ""))
        self.assertEqual(self.rec()["security"], "reality")
        self.assertIn("сертификата на этом сервере больше нет или он истёк", " | ".join(res["notes"]))

    def test_a_copy_of_another_server_never_takes_the_certificate_on_disk(self):
        """Not ``same_node``: a copy naming the domain whose certificate lies here (left by an earlier domain) — the
        domain is set without it, the site stays off and VLESS on Reality, the note asks for the certificate."""
        backup.apply(self.w.runner, self.other_copy())               # the node now on OTHER, DOMAIN's cert on disk
        self.assertEqual(self.rec()["security"], "reality")

        def edit(stj):
            stj["site"]["domain"] = F.DOMAIN
            stj["site"]["mode"] = "site"
        doc = self.copy(edit)
        res = backup.apply(self.w.runner, doc)
        s = smodel.peek(state.load())
        self.assertEqual((s["domain"], s["mode"], s["cert"]), (F.DOMAIN, "domain", ""))
        self.assertEqual(self.rec()["security"], "reality")
        self.assertTrue(any("сертификат" in n for n in res["notes"]), res["notes"])
        self.assertEqual(model.inbound(state.load(), "vless").get("security"), "reality")


class UndoHysteria2Test(_UndoCase):
    """Hysteria2 follows the certificate of the domain by itself (``consumers.cert_installed``): the undo hands the
    certificate out again — Hysteria2 is back on it, and «give its links again» is said only when that is a change
    against the moment before the import."""

    def setUp(self):
        super().setUp()
        from tests.gw.tunnel_fakes import FakeOpenssl as PkiOpenssl
        from tests.test_backup import FakeOpenssl as EncOpenssl
        enc, pki = EncOpenssl(self.w.runner), PkiOpenssl()
        # the copy's cipher and the own certificate of Hysteria2 — one openssl; the first handler that fits wins
        self.w.runner._handlers.insert(0, (["openssl"], lambda argv, inp: (enc if "enc" in argv else pki)(argv, inp)))

    def hy2(self):
        rc, _, err = self.run_json("gw", "inbound", "install", "hy2")
        self.assertEqual(rc, 0, err)
        rc, _, err = self.run_json("gw", "device", "add", "Мама", "Планшет", "--platform", "android", "--access", "hy2")
        self.assertEqual(rc, 0, err)

    def cert_mode(self):
        return (model.inbound(state.load(), "hy2") or {}).get("cert_mode")

    def test_back_on_the_certificate_of_the_domain_without_a_false_alarm(self):
        self.hy2()
        self.assertEqual(self.cert_mode(), "domain")
        backup.apply(self.w.runner, self.other_copy())
        self.assertEqual(self.cert_mode(), "self")
        res = backup.undo(self.w.runner)
        self.assertEqual(self.cert_mode(), "domain")
        self.assertFalse([w for w in res["warnings"] if "Hysteria2" in w], res["warnings"])

    def test_a_certificate_not_handed_out_before_the_import_says_the_change(self):
        """Before the import the domain was set again without its certificate (it stayed on disk) and Hysteria2 was on
        its own: the undo hands the certificate out — Hysteria2 moves to it, and that is said."""
        site_ops.mode(self.w.runner, "domain", yes=True)
        site_ops.clear(self.w.runner, yes=True)
        site_ops.set_domain(self.w.runner, F.DOMAIN)
        self.assertEqual(smodel.peek(state.load())["cert"], "")
        self.hy2()
        self.assertEqual(self.cert_mode(), "self")
        backup.apply(self.w.runner, self.other_copy())
        res = backup.undo(self.w.runner)
        self.assertEqual(smodel.peek(state.load())["cert"], "letsencrypt")
        self.assertEqual(self.cert_mode(), "domain")
        self.assertTrue([w for w in res["warnings"] if "Hysteria2 перешёл на сертификат домена" in w and "Планшет" in w],
                        res["warnings"])


class MovedWarningTest(unittest.TestCase):
    """«сменились настройки протоколов (…)» said again in place without the kind whose links came back."""

    def test_only_the_kind_that_came_back_goes(self):
        ctx = types.SimpleNamespace(warnings=["a"], moved=[("hy2", "Hysteria2: порт"), ("vless", "VLESS: режим")],
                                    moved_text=None)
        backup._moved_warning(ctx)
        self.assertEqual(ctx.warnings, ["a", backup.tr("backup.warn.moved", changes="Hysteria2: порт; VLESS: режим")])
        ctx.warnings.append("b")
        backup._moved_warning(ctx, back="vless")
        self.assertEqual(ctx.warnings, ["a", backup.tr("backup.warn.moved", changes="Hysteria2: порт"), "b"])
        backup._moved_warning(ctx, back="hy2")
        self.assertEqual(ctx.warnings, ["a", "b"])
        backup._moved_warning(ctx, back="vless")                     # nothing left: nothing said, nothing broken
        self.assertEqual((ctx.warnings, ctx.moved_text), (["a", "b"], None))
