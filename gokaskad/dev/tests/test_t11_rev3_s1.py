"""Stage 1, T11 — the third round after the strict review REV-s1-t11-2 (SPEC-stage1-exits §9.2).

С2-2: the pool is «poisoned» neither by new lines nor by old ones. A line goes to a config with the parameters 3.3.5
did not pass (fm, extra, ech, pqv, VLESS Encryption) only once the real xray took it with them (vet, a subscription:
the form «ext» in xray-forms.json); every other line — in the form of 3.3.5. xray refuses the pool at its build — the
refused lines are built again in the form of 3.3.5 and asked again; refused in that form too — out of every config
with a reason and an event (xray_refused), the pool and the add are not rolled back (the parser's xray_heal; the
script before the bot's config, the gateway on «config refused»).
З-1: a key xray refused is told in its own words (xray_refused), not «no password, address or port».
З-2: xray did not answer in time — the key is not taken («could not check, try again»), alike in the script and in a
subscription (xray_unchecked).
П-1: the refused lines are found by halves — about log2(N) runs, not N.
П-5: the menu says the bot's proxy is off."""

import json
import os
import tempfile
import unittest
import urllib.parse
from unittest import mock

from kaskad import secrets
from kaskad.gw import exits, exitsubs, ops, xray
from tests import realbin
from tests.fakes_gw import VLESS_A, VLESS_B, write_pool
from tests.gw import test_tg_proxy as _tg
from tests.gwcase import GwCase
from tests.test_334_xray import Quiet, parser
from tests.test_335_rename import RenameCase
from tests.test_egress_parse_s1 import FIX, UUID
from tests.test_gw_exitsubs import IP_URL
from tests.test_t11_rev_s1 import b64

# the three of REV-s1-t11-2: the parser takes their shape, xray 26.3.27 refuses them
FM = "vless://%s@fm.example.com:443?security=tls&sni=fm.example.com&type=tcp&fm=%s#FmPool" % (
    UUID, urllib.parse.quote('{"tcp":[{"type":"bogus"}]}', safe=""))
EXTRA = "vless://%s@ex.example.com:443?security=tls&sni=ex.example.com&type=xhttp&extra=%s#ExtraPool" % (
    UUID, urllib.parse.quote('{"xmux":5}', safe=""))
ENC = ("vless://%s@enc.example.com:443?security=tls&sni=enc.example.com&type=tcp"
       "&encryption=mlkem768x25519plus.native.0rtt.1-2-3.%s#EncPool" % (UUID, "A" * 43))
# refused in the form of 3.3.5 too: a public key of REALITY that is no key
BAD = "vless://%s@bad.example.com:443?security=reality&sni=bad.example.com&type=tcp&pbk=zz&sid=ab#BadPbk" % UUID
GOOD = FIX["trojan_tls"]


def need_xray(test):
    if realbin.xray_path() is None:
        test.skipTest("no real xray (tools/fetch_test_bins.sh)")
    return realbin.xray_path()


def bot_cfg(p, lines, **kw):
    return p.build_config(lines, 10808, "http://cp.cloudflare.com/generate_204", "leastPing", "60s", 100, 10851, 0,
                          **kw)[0]


class FormsCase(Quiet):
    """The parser with its own xray-forms.json per test (the module is shared by the tests)."""

    def setUp(self):
        super().setUp()
        self.p = parser()
        d = tempfile.mkdtemp()
        self.forms = os.path.join(d, "xray-forms.json")
        patch = mock.patch.object(self.p, "_FORMS_FILE", self.forms)
        patch.start()
        self.addCleanup(patch.stop)
        self.p._forms_cache.clear()
        self.addCleanup(self.p._forms_cache.clear)


class PoolFormTest(FormsCase):
    def test_a_line_not_checked_by_xray_goes_as_on_335(self):
        """С2-2: a line of the pool before this parser — no fm, extra, VLESS Encryption in its outbound."""
        p = self.p
        for line in (FM, EXTRA, ENC):
            self.assertEqual(p.line_form(line), "legacy")
            ob = p.xray_outbound(line, "out_1", 0)
            self.assertEqual(ob["settings"]["vnext"][0]["users"][0]["encryption"], "none", line)
            self.assertNotIn("finalmask", ob["streamSettings"], line)
            self.assertNotIn("extra", ob["streamSettings"].get("xhttpSettings") or {}, line)
            self.assertEqual(p.pool_problem(line), "", line)

    def test_ext_only_for_a_line_xray_took(self):
        p = self.p
        p.forms_put({ENC: "ext"})
        self.assertEqual(p.line_form(ENC), "ext")
        self.assertEqual(p.line_form(ENC.replace("#EncPool", "#Renamed")), "ext")   # the name is not the key
        ob = p.xray_outbound(ENC, "out_1", 0)
        self.assertTrue(ob["settings"]["vnext"][0]["users"][0]["encryption"].startswith("mlkem768x25519plus."))
        self.assertEqual(oct(os.stat(self.forms).st_mode & 0o777), "0o600")



class ReviewReproTest(Quiet):
    def test_the_config_of_the_pool_passes_the_real_xray(self):
        """REV-s1-t11-2: «a valid trojan + the lines of the pool» — xray 26.3.27 took it (it refused it before)."""
        need_xray(self)
        cfg = bot_cfg(parser(), [GOOD, FM, EXTRA, ENC])
        self.assertEqual(len([o for o in cfg["outbounds"] if o["tag"].startswith("out_")]), 4)
        ok, text = realbin.xray_check(cfg)
        self.assertTrue(ok, text)


class HealTest(FormsCase):
    def test_refused_with_its_parameters_goes_as_on_335_refused_both_ways_goes_out(self):
        x = need_xray(self)
        p = self.p
        lines = [GOOD, FM, EXTRA, ENC, BAD]
        p.forms_put({FM: "ext", EXTRA: "ext", ENC: "ext", BAD: "ext"})
        with self.assertRaises(AssertionError):             # as the review saw it: xray refuses this pool
            ok, text = realbin.xray_check(bot_cfg(p, lines))
            self.assertTrue(ok, text)
        r = p.xray_heal(lines, x)
        self.assertEqual((r["legacy"], r["new"], r["refused"], r["unchecked"]), ([2, 3, 4], [5], [5], False))
        self.assertEqual([p.line_form(l) for l in lines], ["legacy", "legacy", "legacy", "legacy", "refused"])
        self.assertEqual(p.pool_problem(BAD), "xray_refused")
        self.assertEqual(p.link_problem(BAD), "xray_refused")
        cfg = bot_cfg(p, lines)
        self.assertEqual([o["tag"] for o in cfg["outbounds"] if o["tag"].startswith("out_")],
                         ["out_1", "out_2", "out_3", "out_4"])
        ok, text = realbin.xray_check(cfg)
        self.assertTrue(ok, text)
        r = p.xray_heal(lines, x)                           # nothing new: told once
        self.assertEqual((r["legacy"], r["new"], r["refused"]), ([], [], [5]))
        with self.assertRaises(SystemExit) as e:            # the bot's group of that line alone: no proxy, no rollback
            bot_cfg(p, lines, only={5})
        self.assertEqual(e.exception.code, 3)

    def test_another_xray_tries_a_refused_line_again(self):
        p = self.p
        d = tempfile.mkdtemp()
        fake = os.path.join(d, "xray")
        with open(fake, "w") as f:
            f.write("#!/bin/sh\nexit 1\n")
        os.chmod(fake, 0o755)
        p.forms_put({BAD: "refused"}, xray=fake)
        self.assertEqual(p.line_form(BAD), "refused")
        with open(fake, "a") as f:                          # another binary at that path
            f.write("# v2\n")
        self.assertEqual(p.line_form(BAD), "legacy")

    def test_no_answer_changes_nothing(self):
        """З-2: xray did not answer — nothing is moved or left out (the build asks it again)."""
        p = self.p
        p.forms_put({FM: "ext"})
        r = p.xray_heal([GOOD, FM], "/bin/xray", run=lambda path: None)
        self.assertTrue(r["unchecked"])
        self.assertEqual(p.line_form(FM), "ext")

    def test_an_xray_that_refuses_anything_leaves_no_key_out(self):
        """A broken xray (it refuses a config without keys too) is no fault of the keys: nothing is marked refused,
        a new key is «unchecked», not «refused»."""
        p = self.p
        p.forms_put({FM: "ext"})
        r = p.xray_heal([GOOD, FM], "/bin/xray", run=lambda path: False)
        self.assertEqual((r["new"], r["legacy"], r["unchecked"]), ([], [], True))
        self.assertEqual((p.line_form(GOOD), p.line_form(FM)), ("legacy", "ext"))
        self.assertEqual(p.xray_vet([GOOD], "/bin/xray", run=lambda path: False), {"refused": [], "unchecked": [1]})

    def test_halves_not_one_by_one(self):
        """П-1: 64 lines, one refused — about 2·log2(64) runs of xray, not 65."""
        p = self.p
        lines = ["trojan://pw%d@t%d.example.com:443?security=tls&sni=t%d.example.com#T%d" % (i, i, i, i)
                 for i in range(64)]
        bad = lines[40]
        runs = []

        def run(path):
            with open(path) as f:
                cfg = json.load(f)
            runs.append(1)
            return not any(o.get("settings", {}).get("servers", [{}])[0].get("password") == "pw40"
                           for o in cfg["outbounds"])
        self.assertEqual(p.xray_vet(lines, "/bin/xray", run=run), {"refused": [41], "unchecked": []})
        self.assertLessEqual(len(runs), 2 * 6 + 2)         # + one config without them (a broken xray?)
        self.assertEqual(p.line_form(bad), "legacy")
        self.assertEqual(p.line_form(lines[0]), "ext")


class UnvettedTest(FormsCase):
    """REV-s1-t11-3 З-1: a key taken while there was no xray goes as on 3.3.5 («unvetted») and is asked of the real
    xray at the heal of the next rebuild — taken in full: «ext» (VLESS Encryption on), told; refused: as an «ext»."""

    def test_no_xray_marks_unvetted_heal_takes_it_in_full(self):
        from tests.test_egress_parse_s1 import FIX
        xb = need_xray(self)
        p = self.p
        enc, plain = FIX["vless_enc"], VLESS_B
        self.assertEqual(p.xray_vet([enc, plain], ""), {"refused": [], "unchecked": []})
        self.assertEqual(p.line_form(enc), "unvetted")
        self.assertEqual(p.line_form(plain), "legacy")          # nothing more in its full form: not marked
        ob = p.xray_outbound(enc, "out_1", 0)
        self.assertEqual(ob["settings"]["vnext"][0]["users"][0]["encryption"], "none")      # as 3.3.5 till asked
        r = p.xray_heal([GOOD, enc, plain], xb)
        self.assertEqual((r["ext"], r["new"], r["legacy"]), ([2], [], []), r)
        self.assertEqual(p.line_form(enc), "ext")
        ob = p.xray_outbound(enc, "out_1", 0)
        self.assertTrue(ob["settings"]["vnext"][0]["users"][0]["encryption"].startswith("mlkem768x25519plus."))
        self.assertEqual(p.xray_heal([GOOD, enc, plain], xb)["ext"], [])                   # told once

    def test_an_unvetted_line_xray_refuses_in_full_goes_as_on_335(self):
        xb = need_xray(self)
        p = self.p
        p.xray_vet([FM, BAD], "")
        self.assertEqual((p.line_form(FM), p.line_form(BAD)), ("unvetted", "legacy"))
        p.forms_put({BAD: "unvetted"})
        r = p.xray_heal([GOOD, FM, BAD], xb)
        self.assertEqual((r["ext"], r["legacy"], r["new"]), ([], [2], [3]), r)
        self.assertEqual((p.line_form(FM), p.line_form(BAD)), ("legacy", "refused"))

    def test_a_line_xray_already_took_is_not_marked_again(self):
        p = self.p
        p.forms_put({FM: "ext"})
        p.xray_vet([FM], "")
        self.assertEqual(p.line_form(FM), "ext")


class VetTimeoutTest(FormsCase):
    def test_no_answer_is_no_refusal_and_no_yes(self):
        """З-2: xray did not answer — «unchecked»: not taken, not called a wrong link, not marked."""
        p = self.p
        r = p.xray_vet([GOOD, FM], "/bin/xray", run=lambda path: None)
        self.assertEqual(r, {"refused": [], "unchecked": [1, 2]})
        self.assertEqual(p.line_form(GOOD), "legacy")
        self.assertEqual(p.xray_refused([GOOD, FM], "/bin/xray", run=lambda path: None), [])


class SubscriptionTimeoutTest(RenameCase):
    def test_a_line_xray_did_not_answer_for_waits_for_the_next_refresh(self):
        """З-2: the subscription says «could not check» (xray_unchecked), never «the link is wrong»; not in the pool."""
        xb = exits.paths.egress_xray()
        os.makedirs(os.path.dirname(xb), exist_ok=True)
        with open(xb, "w") as f:
            f.write("#!/bin/sh\n")
        os.chmod(xb, 0o755)
        p = exitsubs._parser(self.w.runner)
        with mock.patch.object(p, "_xray_takes", lambda *a, **kw: None):
            self.add(IP_URL, b64([GOOD, VLESS_B]))
        self.assertEqual(self.pool(), [])
        codes = exitsubs.load()["subs"][0].get("skip_codes") or []
        self.assertIn("xray_unchecked", codes)
        self.assertNotIn("bad_link", codes)
        self.assertIn("при следующем обновлении", exitsubs.skip_why(codes))


class SubscriptionKeepsRefusedTest(RenameCase):
    """REV-s1-t11-3 З-2: a line of the pool xray refused in both forms («refused», xray_heal) stays in the pool on a
    refresh of its subscription — off, with its id and the routes on it (as С3, «no_tls»); a new such key is skipped."""

    def mark(self, lines):
        p = exitsubs._parser(self.w.runner)
        p.forms_put(dict((l, "refused") for l in lines), xray=exits.paths.egress_xray())
        p._forms_cache.clear()
        return p

    def fake_xray(self):
        xb = exits.paths.egress_xray()
        os.makedirs(os.path.dirname(xb), exist_ok=True)
        with open(xb, "w") as f:
            f.write("#!/bin/sh\nexit 0\n")
        os.chmod(xb, 0o755)

    def test_refresh_keeps_the_refused_line_its_id_and_routes(self):
        from tests.test_335_rename import key
        self.fake_xray()
        vena = key("Вена")
        self.add(IP_URL, b64([vena, VLESS_B]))
        eid = self.ids()[0]
        self.route_all(eid)
        before = self.routes()
        p = self.mark([vena])
        self.assertEqual(p.line_form(vena), "refused")
        r = self.refresh()["results"][0]
        self.assertEqual(self.pool(), [vena, VLESS_B], r["text"])
        self.assertEqual(self.ids()[0], eid)
        self.assertEqual((r["added"], r["removed"]), (0, 0), r["text"])
        self.assertEqual(self.routes(), before)
        # renamed by the subscription: the key is the same (the record has no «#name») — still kept, still the id
        self.feed.bodies[IP_URL] = b64([key("Вена 2"), VLESS_B])
        r = self.refresh()["results"][0]
        self.assertEqual(self.ids()[0], eid, r["text"])
        self.assertEqual(r["removed"], 0, r["text"])
        self.assertEqual(self.routes(), before)

    def test_a_new_refused_key_is_skipped_as_refused(self):
        from tests.test_335_rename import key
        self.fake_xray()
        self.add(IP_URL, b64([VLESS_B]))
        other = key("Новый", port=8443)
        self.mark([other])
        self.feed.bodies[IP_URL] = b64([other, VLESS_B])
        r = self.refresh()["results"][0]
        self.assertEqual(self.pool(), [VLESS_B], r["text"])
        self.assertIn("xray_refused", exitsubs.load()["subs"][0].get("skip_codes") or [])
        self.assertNotIn("insecure", exitsubs.load()["subs"][0].get("skip_codes") or [])


class SubscriptionWithoutXrayTest(RenameCase):
    def test_a_new_key_without_xray_is_unvetted(self):
        """REV-s1-t11-3 З-1: a subscription on a node with no xray of its own yet — its new keys go in, «unvetted» (the
        heal of the rebuild asks the real xray of them)."""
        from tests.test_egress_parse_s1 import FIX
        if os.path.lexists(exits.paths.egress_xray()):
            os.unlink(exits.paths.egress_xray())                       # no xray of its own yet
        self.add(IP_URL, b64([FIX["vless_enc"], VLESS_B]))
        self.assertEqual(len(self.pool()), 2)
        p = exitsubs._parser(self.w.runner)
        p._forms_cache.clear()
        self.assertEqual(p.line_form(self.pool()[0]), "unvetted")
        self.assertEqual(p.line_form(VLESS_B), "legacy")


class WordsTest(Quiet):
    def test_the_core_names_xray_s_refusal_in_its_own_words(self):
        """З-1: the panel — xray_refused and xray_unchecked of their own; bad_link no longer «no password» alone."""
        from kaskad.i18n import use_lang
        for lang in ("ru", "en"):
            with use_lang(lang):
                for code in ("xray_refused", "xray_unchecked"):
                    self.assertIn(code, ops.SKIP_CODES)
                    for kind in ("script_fail.skipped.", "script_warn.skipped."):
                        self.assertIsNotNone(ops._known("gw_ops." + kind + code, n=1), (lang, kind, code))
                    self.assertNotIn("pass", ops._known("gw_ops.script_fail.skipped." + code) or "")
                    self.assertNotIn("парол", ops._known("gw_ops.script_fail.skipped." + code) or "")
                    self.assertIn(code, exitsubs.SKIP_CODES)
                for code in ("xray_refused", "xray_legacy", "xray_ext"):
                    self.assertIsNotNone(ops._known("gw_ops.script_warn.vless_key." + code, n=3), (lang, code))
                got = ops._script_warnings(["⚠ x", "@K3:egress_warn:skipped:xray_refused:2"])
                self.assertEqual(len(got), 1)
                self.assertIn("xray", got[0])

    def test_the_advice_of_a_refused_key_is_a_new_link(self):
        """REV-s1-t11-3 З-3: the same link is refused again (its record lives a day, then vet refuses it in full) — the
        advice is a new link from whoever issued the key, as for a skipped one; never «add the link again»."""
        from kaskad.i18n import tr, use_lang
        here = os.path.dirname(INSTALL)
        for lang, want, bad in (("ru", "попросите у того, кто выдал ключ, новую ссылку", "добавьте ссылку заново"),
                                ("en", "ask whoever issued the key for a new link", "add the link again")):
            with use_lang(lang):
                for key, kw in (("gw_exits.problem.key_xray_refused", {"proto": "VLESS"}),
                                ("gw_ops.script_warn.vless_key.xray_refused", {"n": 3})):
                    txt = tr(key, **kw)
                    self.assertIn(want, txt, (lang, key))
                    self.assertNotIn(bad, txt, (lang, key))
            with open(os.path.join(here, "lib/kaskad/i18n/sh/egress.%s.sh" % lang), encoding="utf-8") as f:
                line = [l for l in f if l.startswith("M_%s_EGRESS_W_XRAY_REFUSED=" % lang)][0]
            self.assertIn(want, line)
            self.assertNotIn(bad, line)

    def test_a_vless_of_the_pool_xray_refused_is_told_so(self):
        p = parser()
        e = {}
        with mock.patch.object(p, "line_form", lambda l: "refused"):
            exits._key_problem(e, p, BAD, "unparsed")
        self.assertEqual(e["problem_code"], "problem.key_xray_refused")
        self.assertIn("problem.key_xray_refused", exits.DETAIL_CODES)


class RunnerCodesTest(GwCase):
    """REV-s1-t11-3 З-4 (M6, M11): what the runner says of ``xray -test`` — 124 (no answer in time) and 127 (did not
    run) are «could not tell», never a refusal, in the subscription (exitsubs._refused) and in the heal of the gateway
    (exits.heal); rc 1 is a refusal; the parser's own run — a timeout is «could not tell»."""

    def setUp(self):
        super().setUp()
        self.xb = exits.paths.egress_xray()
        os.makedirs(os.path.dirname(self.xb), exist_ok=True)
        with open(self.xb, "w") as f:
            f.write("#!/bin/sh\nexit 0\n")
        os.chmod(self.xb, 0o755)

    def rc(self, n):
        """``xray -test`` answers ``n`` for a config with a key in it; a config of no keys passes (a sound xray)."""
        def h(argv, inp):
            with open(argv[-1]) as f:
                keys = [o for o in json.load(f)["outbounds"] if o.get("tag") != "direct"]
            return (n if keys else 0), "", ""
        self.w.runner._handlers.insert(0, ([self.xb, "-test"], h))

    def test_the_subscription_never_takes_no_answer_for_a_refusal(self):
        for n, want in ((124, "xray_unchecked"), (127, "xray_unchecked"), (1, "xray_refused")):
            self.rc(n)
            got = exitsubs._refused(self.w.runner, [GOOD], [])
            self.assertEqual(got, {GOOD: want}, n)
            self.w.runner._handlers.pop(0)

    def test_the_heal_of_the_gateway_changes_nothing_without_an_answer(self):
        write_pool([GOOD, FM])
        p = exits.load_parser(self.w.runner)
        p.forms_put({FM: "ext"})
        p._forms_cache.clear()
        self.rc(124)
        r = exits.heal(self.w.runner, self.xb)
        self.assertTrue(r["unchecked"], r)
        self.assertEqual(p.line_form(FM), "ext")
        self.w.runner._handlers.pop(0)

    def test_the_parser_s_own_run_a_timeout_is_no_answer(self):
        p = parser()
        slow = os.path.join(os.path.dirname(self.xb), "slow-xray")
        with open(slow, "w") as f:
            f.write("#!/bin/sh\nsleep 5\n")
        os.chmod(slow, 0o755)
        self.assertIsNone(p._xray_takes([], slow, 0.3, None))


class FormsPruneTest(FormsCase):
    def test_a_line_out_of_the_pool_for_a_day_leaves_the_file_at_the_heal(self):
        """REV-s1-t11-3 З-4 (M7): the heal of the pool drops the records of the lines out of it for a day; a younger one
        (a key moved out an hour ago, coming back) stays."""
        import time
        xb = need_xray(self)
        p = self.p
        p.forms_put({FM: "refused"}, now=time.time() - 2 * 86400)
        p.forms_put({EXTRA: "ext"}, now=time.time() - 3600)
        p._forms_cache.clear()
        p.xray_heal([GOOD], xb)
        with open(self.forms) as f:
            keys = set(json.load(f)["lines"])
        self.assertNotIn(p._form_key(FM), keys)
        self.assertIn(p._form_key(EXTRA), keys)


class GatewayHealTest(GwCase):
    def test_a_line_xray_refuses_does_not_stop_the_gateway(self):
        """С2-2: the gateway's config refused for a line of the pool — the line goes as on 3.3.5, the config is applied
        (gw apply no longer stops at «config refused»)."""
        need_xray(self)
        if not realbin.in_fakes():
            self.skipTest("KASKAD_TEST_NO_REAL=1")
        st = self.scenario()
        write_pool([VLESS_A, VLESS_B, FM])
        p = exits.load_parser(self.w.runner)
        p.forms_put({FM: "ext"})
        self.with_xports()                     # B3: the table of stage 1 before xray (REV-s1-b1 З1)
        res = xray.apply(st, secrets.load(), self.w.runner)
        self.assertTrue(res["changed"])
        self.assertEqual(p.line_form(FM), "legacy")
        text = self.read("/etc/kaskad/gw/xray.json")
        self.assertNotIn("bogus", text)


class BashHealTest(unittest.TestCase):
    setUp = _tg.BashBotConfigTest.setUp
    run_sh = _tg.BashBotConfigTest.run_sh
    cfg = _tg.BashBotConfigTest.cfg

    def forms(self, d):
        p = parser()
        lines = dict((p._form_key(l), [f, 2000000000]) for l, f in d.items())
        with open(os.path.join(self.eg, "xray-forms.json"), "w") as f:
            json.dump({"v": 1, "lines": lines}, f)

    def test_the_pool_is_healed_before_the_bot_config(self):
        need_xray(self)
        with open(os.path.join(self.eg, "upstream.txt"), "w") as f:
            f.write("\n".join([GOOD, FM, BAD]) + "\n")
        self.forms({FM: "ext", BAD: "ext"})
        rc, out = self.run_sh("EGRESS_MARKS=1; K3_MARKS=1; egress_xray_heal; egress_bot_config; echo RC=$?\n"
                              "while IFS=$'\\t' read -r w c; do egress_awg_warn_text \"$w\" \"$c\"; echo; "
                              "done < <(egress_vless_warn_codes)")
        self.assertIn("RC=0", out)
        self.assertIn("@K3:egress_warn:vless_key:2:xray_legacy", out)
        self.assertIn("Ключ #3: xray этой версии его не принимает", out)
        cfg = self.cfg()
        self.assertEqual([o["tag"] for o in cfg["outbounds"] if o["tag"].startswith("out_")], ["out_1", "out_2"])
        with open(os.path.join(self.d, "log")) as f:
            log = f.read()
        self.assertIn("#3 «BadPbk» выключен", log)
        self.assertIn("#2 «FmPool» собран без новых параметров", log)

    def test_a_key_added_without_xray_is_asked_once_xray_is_there(self):
        """REV-s1-t11-3 З-1: a key added while the node has no xray of its own (a fresh node, a copy brought over) —
        «unvetted», as on 3.3.5; the heal of the rebuild with xray takes it in full and says so (screen, mark, log)."""
        from tests.test_egress_parse_s1 import FIX
        need_xray(self)
        x = os.path.join(self.eg, "xray")
        os.unlink(x)                                                    # no xray of its own yet
        src = os.path.join(self.d, "key.txt")
        with open(src, "w") as f:
            f.write(FIX["vless_enc"] + "\n")
        with open(os.path.join(self.eg, "upstream.txt"), "w") as f:
            f.write(GOOD + "\n")
        rc, out = self.run_sh('got=$(egress_collect_links "%s"); echo "N=$(printf "%%s\\n" "$got" | grep -c .)"' % src)
        self.assertIn("N=1", out)
        p = parser()
        with open(os.path.join(self.eg, "xray-forms.json")) as f:
            forms = json.load(f)["lines"]
        self.assertEqual(forms[p._form_key(FIX["vless_enc"])][0], "unvetted")
        os.symlink(realbin.xray_path(), x)                              # xray downloaded by the rebuild
        with open(os.path.join(self.eg, "upstream.txt"), "w") as f:
            f.write("\n".join([GOOD, FIX["vless_enc"]]) + "\n")
        rc, out = self.run_sh("EGRESS_MARKS=1; K3_MARKS=1; egress_xray_heal; egress_bot_config; echo RC=$?")
        self.assertIn("RC=0", out)
        self.assertIn("@K3:egress_warn:vless_key:2:xray_ext", out)
        self.assertIn("Ключ #2: добавлен, когда xray ещё не было; теперь xray его проверил", out)
        enc = [o for o in self.cfg()["outbounds"] if o["tag"] == "out_2"][0]
        self.assertTrue(enc["settings"]["vnext"][0]["users"][0]["encryption"].startswith("mlkem768x25519plus."))
        with open(os.path.join(self.d, "log")) as f:
            self.assertIn("проверен xray после его установки", f.read())
        rc, out = self.run_sh("EGRESS_MARKS=1; K3_MARKS=1; egress_xray_heal")
        self.assertNotIn("xray_ext", out)                               # told once

    STUBS = ("egress_ensure_xray() { return 0; }; egress_tofu_snap() { :; }; egress_awg_sync() { :; }; "
             "egress_write_service() { :; }; egress_set_bot_proxy() { :; }; egress_bot_off() { echo BOT_OFF; }; "
             "systemctl() { :; }; k3_gw_pool_changed() { :; }; _egr_sha256() { :; }; ")

    def test_the_rebuilds_heal_the_pool_themselves(self):
        """REV-s1-t11-3 З-4 (M2, M3): the heal is wired into the rebuild of the pool (egress_rebuild) and of the bot
        (egress_tg_rebuild_now) — not only called by hand: a line xray refuses in full goes as on 3.3.5, the config
        passes the real xray."""
        need_xray(self)
        for fn in ("egress_rebuild", "egress_tg_rebuild_now"):
            with open(os.path.join(self.eg, "upstream.txt"), "w") as f:
                f.write("\n".join([GOOD, FM]) + "\n")
            self.forms({FM: "ext"})
            rc, out = self.run_sh("EGRESS_MARKS=1; K3_MARKS=1; " + self.STUBS + fn + "; echo RC=$?")
            self.assertIn("RC=0", out, (fn, out))
            self.assertIn("@K3:egress_warn:vless_key:2:xray_legacy", out, fn)
            with open(os.path.join(self.eg, "xray-forms.json")) as f:
                self.assertEqual(json.load(f)["lines"][parser()._form_key(FM)][0], "legacy", fn)
            ob = [o for o in self.cfg()["outbounds"] if o["tag"] == "out_2"][0]
            self.assertNotIn("finalmask", ob.get("streamSettings") or {}, fn)

    def test_a_copy_brought_over_without_xray_is_unvetted(self):
        """REV-s1-b2 З4: egress_replace_now (a copy .kbk brought over, the rebuild a stub) on a node with no xray of its
        own — the new line «unvetted» (``vet --xray ""``), a line of the pool before gets no record (--keep)."""
        from tests.test_egress_parse_s1 import FIX
        x = os.path.join(self.eg, "xray")
        if os.path.lexists(x):
            os.unlink(x)                                                # no xray of its own yet
        with open(os.path.join(self.eg, "upstream.txt"), "w") as f:
            f.write(FM + "\n")                                          # a line of the pool before (its full form differs)
        new = os.path.join(self.d, "new.txt")
        with open(new, "w") as f:
            f.write(FM + "\n" + FIX["vless_enc"] + "\n")
        rc, out = self.run_sh(self.STUBS + "egress_rebuild() { echo REBUILT; return 0; }; "
                              'egress_replace_now "$(egress_pool_hash)" "$(cat "%s")"; echo RC=$?' % new)
        self.assertIn("RC=0", out)
        self.assertIn("REBUILT", out)
        p = parser()
        path = os.path.join(self.eg, "xray-forms.json")
        self.assertTrue(os.path.exists(path), "no vet without xray: the new line is not marked")
        with open(path) as f:
            forms = json.load(f)["lines"]
        self.assertEqual(forms[p._form_key(FIX["vless_enc"])][0], "unvetted")
        self.assertNotIn(p._form_key(FM), forms)

    def test_a_key_of_the_russian_pool_is_not_vetted_again(self):
        """REV-s1-t11-3 З-4 (M10): adding keys, the lines of upstream-ru.txt are the pool too (--keep): not asked of
        xray again — one this xray would not answer for stays."""
        x = os.path.join(self.eg, "xray")
        if os.path.lexists(x):
            os.unlink(x)
        with open(x, "w") as f:
            f.write("#!/bin/sh\nexit 1\n")                        # refuses anything: a new line is never taken
        os.chmod(x, 0o755)
        with open(os.path.join(self.eg, "upstream.txt"), "w") as f:
            f.write(VLESS_B + "\n")                                # the abroad pool has not this key
        with open(os.path.join(self.eg, "upstream-ru.txt"), "w") as f:
            f.write(GOOD + "\n")
        src = os.path.join(self.d, "key.txt")
        with open(src, "w") as f:
            f.write(GOOD + "\n")
        rc, out = self.run_sh('got=$(egress_collect_links "%s"); echo "N=$(printf "%%s\\n" "$got" | grep -c .)"' % src)
        self.assertIn("N=1", out)
        os.unlink(os.path.join(self.eg, "upstream-ru.txt"))
        rc, out = self.run_sh('got=$(egress_collect_links "%s"); echo "N=$(printf "%%s\\n" "$got" | grep -c .)"' % src)
        self.assertIn("N=0", out)                                  # not in a pool: asked, and left out

    def test_a_group_of_refused_keys_turns_the_proxy_off(self):
        need_xray(self)
        with open(os.path.join(self.eg, "upstream.txt"), "w") as f:
            f.write(BAD + "\n")
        self.forms({BAD: "ext"})
        rc, out = self.run_sh("EGRESS_MARKS=1; K3_MARKS=1; egress_xray_heal; egress_bot_config; echo RC=$?")
        self.assertIn("RC=3", out)
        self.assertIn("@K3:egress_warn:tg_group_empty", out)
        self.assertIn("xray этой версии не принимает", out)

    def test_skip_report_says_could_not_check(self):
        with open(os.path.join(self.eg, ".extract.skip"), "w") as f:
            f.write("K3SKIP\txray_unchecked\tSlow\nK3SKIP\txray_refused\tOdd\n")
        rc, out = self.run_sh("EGRESS_MARKS=1; K3_MARKS=1; egress_skip_report; echo \"WHY=$EGRESS_SKIP_WHY\"")
        self.assertIn("Пропущен ключ «Slow»: xray не ответил вовремя", out)
        self.assertIn("Пропущен ключ «Odd»: xray этой версии его не принимает", out)
        self.assertIn("@K3:egress_warn:skipped:xray_unchecked:1", out)
        self.assertIn("@K3:egress_warn:skipped:xray_refused:1", out)
        self.assertNotIn("не разобрана", out)

    def test_the_menu_says_the_proxy_is_off(self):
        """П-5: after egress_bot_off the menu has the line «the bot's proxy is off»; a proxy again — no line."""
        rc, out = self.run_sh("egress_bot_off >/dev/null 2>&1; [ -f \"$EGRESS_BOT_OFF_STAMP\" ] && echo STAMP; "
                              "t EGRESS_TG_PROXY_OFF")
        self.assertIn("STAMP", out)
        self.assertIn("Прокси бота выключен", out)
        with open(INSTALL) as f:
            text = f.read()
        # SPEC-tg-proxy Р6: метка «direct» (выбрано «Напрямую») — своя строка, пустая метка — эта (tests/gw/test_tg_direct.py)
        self.assertIn('elif [ -f "$EGRESS_BOT_OFF_STAMP" ]; then echo -e "  ${YELLOW}$(t EGRESS_TG_PROXY_OFF)', text)
        self.assertEqual(text.count('egress_set_bot_proxy "socks5h://127.0.0.1:${EGRESS_SOCKS_PORT}"; '
                                    'rm -f "$EGRESS_BOT_OFF_STAMP"'), 2)


INSTALL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "install.sh")
