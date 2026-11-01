"""T13 (SPEC-stage1-rename §3, §7): renames of people, devices, exits, inbounds, subscriptions of exits, forwards and
monitored servers — the light write without an apply, the ids and everything issued untouched; the checks of a new
name at the creation."""

import hashlib
import json
import os
import unittest
from unittest import mock

from kaskad import errors, forwards, names, paths, state, util
from kaskad.bot import aliases
from kaskad.errors import KaskadError
from kaskad.gw import exits, exitsubs, model, ops, rename, rev as R
from kaskad.gw import policy_ops as pops
from tests.fakes_nft import save_state
from tests.test_gw_ops import F443, CliGwCase


# what every apply writes anew whatever changed — the clock and the render's sequence number, not content — left out
# of the comparison BY NAME (F19b п.9). The old way («a control apply finds the volatile files») was flaky: two applies
# in one second, the third one in the next — counters.json «changed». The control apply now only proves the list.
VOLATILE_FIELDS = {
    os.path.join("etc", "kaskad", "net", "counters.json"): ("saved",),     # nft._write_files: {"saved": now_iso()}
    os.path.join("etc", "kaskad", "gw", "xray.decision"): ("seq",),        # xray: {"fp", "seq"}; fp still compared
}
VOLATILE_FILES = (os.path.join("var", "lib", "kaskad", "gw", "xray-seq"),)  # the number of the last render taken


def _files():
    """sha1 of every file of the (fake) node but the state, the journal and the run dir — the configs of the daemons,
    the pool, secrets.json, the layout of the scheme. The clock fields of VOLATILE_FIELDS do not count."""
    root = paths.P("/")
    out = {}
    for d, _subs, fs in os.walk(root):
        for f in fs:
            p = os.path.join(d, f)
            rel = os.path.relpath(p, root)
            if rel.endswith(("state.json", "kaskad.log")) or rel.startswith(("run" + os.sep, "var/run", "tmp")) \
                    or ".lock" in rel or rel.endswith(".tmp") or rel in VOLATILE_FILES:
                continue
            with open(p, "rb") as fh:
                data = fh.read()
            if rel in VOLATILE_FIELDS:
                doc = json.loads(data.decode("utf-8"))
                for k in VOLATILE_FIELDS[rel]:
                    doc.pop(k, None)
                data = json.dumps(doc, sort_keys=True).encode("utf-8")
            out[rel] = hashlib.sha1(data).hexdigest()
    return out


class RenameCase(CliGwCase):
    def setUp(self):
        super().setUp()
        self.st = self.enabled_world()
        for kind in ("wg", "awg2"):
            ops.inbound_install(self.w.runner, kind)
        self.uid = ops.user_add(self.w.runner, "Мама")["id"]
        self.u2 = ops.user_add(self.w.runner, "Дача")["id"]
        self.did = ops.device_add(self.w.runner, self.uid, "Телефон", access=("wg",))["device"]["id"]
        self.d2 = ops.device_add(self.w.runner, self.uid, "Ноутбук", access=("awg2",))["device"]["id"]
        self.d3 = ops.device_add(self.w.runner, self.u2, "Роутер", access=("wg",))["device"]["id"]

    def light(self):
        """No apply, no gw hold, no command: the patched ones fail the test if called."""
        def boom(*a, **kw):
            raise AssertionError("apply called by a rename")
        return [mock.patch.object(ops, "_apply", boom), mock.patch.object(ops, "apply_all", boom)]

    def renamed(self, *a, **kw):
        ps = self.light()
        for p in ps:
            p.start()
        calls = len(getattr(self.w.runner, "calls", []) or [])
        try:
            return rename.rename(self.w.runner, *a, **kw)
        finally:
            for p in ps:
                p.stop()
            self.assertEqual(len(getattr(self.w.runner, "calls", []) or []), calls)


class PeopleDevicesTest(RenameCase):
    def test_a_person_renamed_ids_logins_configs_untouched(self):
        before = _files()
        st0 = state.load()
        r = self.renamed("user", self.uid, "  Мама   Оля ", via="panel")
        self.assertEqual((r["id"], r["old"], r["name"], r["what"]), (self.uid, "Мама", "Мама Оля", "user"))
        self.assertIn("Логины устройств не меняются", r["note"])
        st = state.load()
        self.assertEqual(model.get_user(st, self.uid)["name"], "Мама Оля")
        self.assertEqual([d["login"] for d in model.devices(st)], [d["login"] for d in model.devices(st0)])
        self.assertEqual([d["id"] for d in model.devices(st)], [d["id"] for d in model.devices(st0)])
        self.assertEqual(R.rev_of(st), R.rev_of(st0))
        self.assertEqual(_files(), before)             # secrets.json, wg/awg/xray/nft, the pool — byte for byte
        self.assertIn("RENAME user %s: «Мама» -> «Мама Оля» (via=panel)" % self.uid, self.log())

    def test_a_person_duplicate_is_refused_case_insensitive(self):
        with self.assertRaises(pops.Refused) as c:
            self.renamed("user", self.uid, "дача")
        self.assertEqual((c.exception.key, c.exception.status, c.exception.code), ("user_exists", 409, errors.CONFLICT))
        self.assertEqual(c.exception.data["duplicates"], [self.u2])
        with self.assertRaises(pops.Refused):
            self.renamed("user", self.uid, "дача", allow_duplicate=True)      # no override for people
        self.renamed("user", self.uid, "мама")                                # itself is no duplicate
        self.assertEqual(model.get_user(state.load(), self.uid)["name"], "мама")

    def test_the_name_rule_refuses(self):
        for bad, why in (("", "empty"), ("x" * 41, "long"), ("a|b", "char"), ("a\nb", "char"), ("Вена\n", "char"),
                         ("‮абв", "char"), ("ㅤ", "invisible"), ("a", "private")):
            with self.assertRaises(pops.Refused, msg=repr(bad)) as c:
                self.renamed("device", self.did, bad)
            self.assertEqual((c.exception.key, c.exception.status), ("field", 400))
            self.assertEqual(c.exception.data["reason"], why, repr(bad))
        r = self.renamed("device", self.did, "x" * 40)
        self.assertEqual(len(r["name"]), 40)
        for ok in ("<b>x</b>", "-минус", "a\\b", "👨‍👩‍👧", "&\"'"):
            self.assertEqual(self.renamed("device", self.did, ok)["name"], ok)

    def test_device_duplicate_asks_and_passes_with_the_flag(self):
        with self.assertRaises(pops.Refused) as c:
            self.renamed("device", self.did, "НОУТБУК")
        self.assertEqual(c.exception.key, "confirm_duplicate")
        self.assertEqual(c.exception.data, {"need": "confirm_duplicate", "duplicates": [self.d2]})
        d0 = model.get_device(state.load(), self.did)
        r = self.renamed("device", self.did, "НОУТБУК", allow_duplicate=True)
        self.assertEqual(r["duplicates"], [self.d2])
        self.renamed("device", self.did, "Роутер")                         # another person's device: no duplicate
        d = model.get_device(state.load(), self.did)
        self.assertEqual(d["login"], d0["login"])                              # the login never follows the name
        # the whole record but the name — the same (login, id, user, access, ips, route, platform, kind …)
        self.assertEqual({k: v for k, v in d.items() if k != "name"}, {k: v for k, v in d0.items() if k != "name"})
        self.assertEqual(d["name"], "Роутер")

    def test_device_notes_by_the_accesses(self):
        self.assertIn("второй туннель", self.renamed("device", self.did, "Т2")["note"])
        st = state.load()
        d = model.get_device(st, self.did)
        d["access"] = ["socks5"]
        state.save(st)
        self.assertIn("Имя профиля в приложении", self.renamed("device", self.did, "Т3")["note"])
        st = state.load()
        model.get_device(st, self.did)["access"] = ["vless"]
        state.save(st)
        self.assertIn("при следующем обновлении подписки", self.renamed("device", self.did, "Т4")["note"])
        self.assertIn("при следующем обновлении подписки", self.renamed("user", self.uid, "Мама 2")["note"])

    def test_hy2_alone_says_the_subscription_too(self):
        """REV-3371 З-2: Hysteria2 goes into the subscription as VLESS does — a device and a person with Hysteria2 only
        get the hint of the subscription; SOCKS5 (no link in it) does not."""
        st = state.load()
        model.get_device(st, self.did)["access"] = ["hy2"]
        model.get_device(st, self.d2)["access"] = ["socks5"]
        state.save(st)
        self.assertIn("при следующем обновлении подписки", self.renamed("device", self.did, "Т5")["note"])
        self.assertNotIn("обновлении подписки", self.renamed("device", self.d2, "Т6")["note"])
        self.assertIn("при следующем обновлении подписки", self.renamed("user", self.uid, "Мама 3")["note"])
        self.assertNotIn("обновлении подписки", self.renamed("user", self.u2, "Дача 2")["note"])   # WireGuard only
        self.assertIn("next subscription update", rename.note("device", None, {"access": ["hy2"]}, "en"))

    def test_gone_and_a_reference_by_name(self):
        with self.assertRaises(pops.Refused) as c:
            self.renamed("user", "u-deadbeef", "x")
        self.assertEqual((c.exception.key, c.exception.status), ("gone", 404))
        self.assertEqual(self.renamed("device", "Ноутбук", "Ноут")["id"], self.d2)
        with self.assertRaises(KaskadError):
            self.renamed("what", "x", "y")

    def test_an_old_long_name_is_shown_a_new_long_one_refused(self):
        st = state.load()
        model.get_user(st, self.uid)["name"] = "д" * 64                       # made before stage 1 (limit 64)
        state.save(st)
        self.assertEqual(model.get_user(state.load(), self.uid)["name"], "д" * 64)
        with self.assertRaises(pops.Refused):
            self.renamed("user", self.uid, "д" * 41)


class ExitsInboundsTest(RenameCase):
    def test_an_exit_light_reset_and_rev(self):
        st0 = state.load()
        before = _files()
        r = self.renamed("exit", self.vid, "Вена")
        self.assertEqual((r["name"], r["shown"]), ("Вена", "Вена"))
        self.assertEqual(r["rev"], R.rev_of(st0) or r["rev"])
        self.assertEqual(_files(), before)
        r = self.renamed("exit", self.vid, "")
        self.assertEqual(r["name"], "")
        self.assertNotIn("name", state.load()["gateway"]["exits"].get(self.vid, {}))
        row = [e for e in exits.pool(None) if e["id"] == self.vid][0]
        self.assertEqual(r["shown"], exits.label(state.load(), row))
        with self.assertRaises(pops.Refused):
            self.renamed("exit", self.vid, "x" * 41)
        with self.assertRaises(pops.Refused) as c:
            self.renamed("exit", "e-0000000000", "x")
        self.assertEqual(c.exception.key, "gone")

    def test_exit_set_of_a_name_alone_goes_the_light_way(self):
        ps = self.light()
        for p in ps:
            p.start()
        try:
            out = pops.exit_set(self.w.runner, self.vid, name="Амстердам")
            out2 = ops.exit_set(self.w.runner, self.aid, name="Рига")
        finally:
            for p in ps:
                p.stop()
        self.assertEqual(out["pref"]["name"], "Амстердам")
        self.assertEqual(out2["pref"]["name"], "Рига")
        with self.assertRaises(pops.Refused):
            ops.exit_set(self.w.runner, self.aid, on=False, name="x" * 41)      # with the switch: the same check
        res = ops.exit_set(self.w.runner, self.aid, on=False, name="")           # the reset with the switch
        self.assertNotIn("name", state.load()["gateway"]["exits"][self.aid])
        self.assertFalse(res["pref"]["enabled"])

    def test_the_label_of_an_inbound_shown_kept_and_never_issued(self):
        issued = ops.issue(self.w.runner, self.did, inbound="wg")
        r = self.renamed("inbound", "wg", "для роутеров")
        self.assertEqual(r["shown"], "%s — для роутеров" % rename._inbound_title("wg"))
        self.assertEqual(rename.inbound_shown(state.load(), "wg", "en"), "%s — для роутеров" %
                         rename._inbound_title("wg"))
        again = ops.issue(self.w.runner, self.did, inbound="wg")
        self.assertEqual(json.dumps(issued, sort_keys=True, default=str), json.dumps(again, sort_keys=True,
                                                                                      default=str))
        row = [x for x in ops.inbound_list(self.w.runner, with_status=False) if x["kind"] == "wg"][0]
        self.assertEqual((row["label"], row["shown"]), ("для роутеров", r["shown"]))
        ops.inbound_uninstall(self.w.runner, "wg")
        self.assertEqual(rename.inbound_label(state.load(), "wg"), "для роутеров")   # in removed_inbounds
        ops.inbound_install(self.w.runner, "wg")
        self.assertEqual(model.inbound(state.load(), "wg").get("label"), "для роутеров")
        self.renamed("inbound", "wg", "")
        self.assertNotIn("label", model.inbound(state.load(), "wg"))
        self.assertEqual(rename.inbound_shown(state.load(), "wg"), rename._inbound_title("wg"))
        with self.assertRaises(pops.Refused) as c:
            self.renamed("inbound", "pptp", "x")
        self.assertEqual(c.exception.key, "gone")

    def test_inbound_label_duplicate_asks(self):
        self.renamed("inbound", "wg", "Дом")
        with self.assertRaises(pops.Refused) as c:
            self.renamed("inbound", "awg2", "дом")
        self.assertEqual(c.exception.data["duplicates"], ["wg"])
        self.renamed("inbound", "awg2", "дом", allow_duplicate=True)


class SubsTest(RenameCase):
    def setUp(self):
        super().setUp()
        exitsubs.save({"schema": 1, "next": 3, "subs": [
            {"id": "s-aaaaaa", "n": 1, "url": "https://sub.example.com/x", "keys": [self.vid], "auto": True},
            {"id": "s-bbbbbb", "n": 2, "url": "https://203.0.113.9/y", "keys": [], "auto": False}]})

    def test_label_reset_find_and_public(self):
        r = self.renamed("exitsub", "1", "Основная")
        self.assertEqual((r["id"], r["old"], r["shown"]), ("s-aaaaaa", "", "Основная"))
        rows = dict((x["id"], x) for x in exitsubs.listing())
        self.assertEqual((rows["s-aaaaaa"]["name"], rows["s-aaaaaa"]["label"], rows["s-aaaaaa"]["domain"]),
                         ("Основная", "Основная", "sub.example.com"))
        self.assertEqual(rename.find_sub(exitsubs.load(), "основная")["id"], "s-aaaaaa")
        self.assertEqual(exitsubs.owners()[self.vid]["name"], "Основная")
        self.assertEqual(self.renamed("exitsub", "s-aaaaaa", "")["shown"], "sub.example.com")
        self.assertEqual(self.renamed("exitsub", "2", "")["shown"], exitsubs.name_of({"n": 2}))
        self.assertIn("url", exitsubs.load()["subs"][0])                     # the address untouched

    def test_duplicate_and_ambiguous_label(self):
        self.renamed("exitsub", "1", "Вена")
        with self.assertRaises(pops.Refused):
            self.renamed("exitsub", "2", "вена")
        self.renamed("exitsub", "2", "вена", allow_duplicate=True)
        with self.assertRaises(KaskadError) as c:
            rename.find_sub(exitsubs.load(), "Вена")
        self.assertEqual(c.exception.code, errors.USAGE)
        with self.assertRaises(pops.Refused) as c:
            self.renamed("exitsub", "s-cccccc", "x")
        self.assertEqual(c.exception.key, "gone")

    def test_a_label_is_kept_through_a_save_of_the_subs(self):
        self.renamed("exitsub", "1", "Моя")
        doc = exitsubs.load()
        exitsubs.save(doc)                                                    # as a refresh writes it back
        self.assertEqual(exitsubs.load()["subs"][0]["label"], "Моя")

    def test_the_result_of_a_refresh_says_the_label_as_it_is(self):
        """N3: the result of 3.3.5 (``updated``) names the subscription by its label — «vpn Вена», not «Vpn вена»."""
        self.renamed("exitsub", "1", "vpn Вена")
        sub = exitsubs.load()["subs"][0]
        self.assertEqual(exitsubs._title(sub), "vpn Вена")
        r = exitsubs._result(sub, {"added": [], "removed": [], "seen": [1], "full": 0, "renamed": {"a": "b"}}, 0)
        self.assertEqual(r["updated"], 1)
        self.assertTrue(r["text"].startswith("vpn Вена: обновлено 1"), r["text"])
        self.assertEqual(exitsubs._title({"n": 2}), exitsubs.name_of({"n": 2}).capitalize())

    def test_busy_refresh(self):
        with exitsubs._locked():
            with mock.patch.object(rename, "SUB_LOCK_WAIT", 0):
                with self.assertRaises(errors.LockedError):
                    rename.rename(None, "exitsub", "1", "x")


class ForwardsServersTest(CliGwCase):
    def setUp(self):
        super().setUp()
        save_state([F443])

    def test_forward_rename_reset_and_duplicate(self):
        st0 = state.load()
        r = forwards.rename("tcp", 443, "Сайт")
        self.assertEqual((r["id"], r["name"], r["old"]), ("tcp:443", "Сайт", st0["forwards"][0].get("name") or ""))
        f = state.find_forward(state.load(), "tcp", 443)
        self.assertEqual((f["name"], f["to"], f["to_port"]), ("Сайт", st0["forwards"][0]["to"],
                                                              st0["forwards"][0]["to_port"]))
        self.assertEqual(forwards.rename("tcp", 443, "")["shown"], "tcp :443")
        with self.assertRaises(KaskadError) as c:
            forwards.rename("tcp", 443, "x" * 41)
        self.assertEqual(c.exception.code, errors.USAGE)
        with self.assertRaises(KaskadError) as c:
            forwards.rename("udp", 9, "x")
        self.assertTrue(c.exception.data.get("gone"))
        self.assertIn("RENAME forward tcp:443", self.log())

    def test_forward_duplicate(self):
        st = state.load()
        st["forwards"].append(dict(st["forwards"][0], port=8443, name="Сайт"))
        state.save(st)
        with self.assertRaises(errors.ConflictError) as c:
            forwards.rename("tcp", 443, "САЙТ")
        self.assertEqual(c.exception.data, {"need": "confirm_duplicate", "duplicates": ["tcp:8443"]})
        self.assertEqual(forwards.rename("tcp", 443, "САЙТ", allow_duplicate=True)["duplicates"], ["tcp:8443"])

    def test_server_rename_keeps_the_other_fields(self):
        aliases.update("203.0.113.5", name="Старое", note="заметка", country="NL", isp="Провайдер")
        r = aliases.rename("203.0.113.5", "Сервер в Вене")
        self.assertEqual((r["old"], r["name"]), ("Старое", "Сервер в Вене"))
        self.assertEqual(aliases.get("203.0.113.5"), {"name": "Сервер в Вене", "note": "заметка", "country": "NL",
                                                     "isp": "Провайдер"})
        aliases.update("203.0.113.6", name="Рига")
        with self.assertRaises(errors.ConflictError):
            aliases.rename("203.0.113.5", "рига")
        aliases.rename("203.0.113.5", "рига", allow_duplicate=True)
        for bad in ("a\\b", "a|b", "x" * 41, "a\tb"):
            with self.assertRaises(KaskadError, msg=bad) as c:
                aliases.rename("203.0.113.5", bad)
            self.assertEqual(c.exception.code, errors.USAGE)
        with self.assertRaises(KaskadError):
            aliases.rename("not-an-ip", "x")
        self.assertEqual(aliases.rename("203.0.113.5", "")["shown"], "203.0.113.5")


class CreationTest(CliGwCase):
    """§2 «Где проверка действует при создании»: a refusal, not the old silent cut or replacement."""

    def setUp(self):
        super().setUp()
        self.enabled_world()

    def test_people_devices_forwards(self):
        for bad in ("x" * 41, "a|b", "a\nb", ""):
            with self.assertRaises(KaskadError, msg=repr(bad)) as c:
                ops.user_add(self.w.runner, bad)
            self.assertEqual(c.exception.code, errors.USAGE)
        u = ops.user_add(self.w.runner, "Вена")
        with self.assertRaises(KaskadError) as c:
            ops.user_add(self.w.runner, "вена")                             # case-insensitive now
        self.assertEqual(c.exception.code, errors.USAGE)
        with self.assertRaises(KaskadError):
            ops.user_add(self.w.runner, "Вена‍")                       # a zero-width joiner is the same name
        for bad in ("x" * 41, "a|b"):
            with self.assertRaises(KaskadError):
                ops.device_add(self.w.runner, u["id"], bad, access=("wg",))
        self.assertEqual(ops.device_add(self.w.runner, u["id"], "y" * 40)["device"]["name"], "y" * 40)
        with self.assertRaises(KaskadError) as c:
            forwards.add_forward("tcp", 8443, "203.0.113.5", 443, name="x" * 41, runner=self.w.runner)
        self.assertEqual(c.exception.code, errors.USAGE)
        self.assertIsNone(state.find_forward(state.load(), "tcp", 8443))


if __name__ == "__main__":
    import unittest
    unittest.main()


class UserPageLabelTest(RenameCase):
    def test_the_card_of_p_shows_the_label_the_items_stay(self):
        from kaskad.panel import userpage
        u = model.get_user(state.load(), self.uid)
        before = userpage.build_keys(u, "t" * 43, self.w.runner)
        self.renamed("inbound", "wg", "<b>для роутеров</b>")
        after = userpage.build_keys(u, "t" * 43, self.w.runner)

        def cards(doc):
            import re                                          # the grant of a file is new on every read
            return [(dev["id"], ib["kind"], ib["title"],
                     re.sub(r"/file/[A-Za-z0-9_-]+/", "/file/G/", json.dumps(ib["items"], sort_keys=True, default=str)))
                    for dev in doc["devices"] for ib in dev["inbounds"]]
        b, a = cards(before), cards(after)
        self.assertEqual([x[3] for x in b], [x[3] for x in a])                 # what is issued — byte for byte
        wg = [x for x in a if x[1] == "wg"][0]
        self.assertTrue(wg[2].endswith(" — <b>для роутеров</b>"), wg[2])       # escaped by the page (html.escape)


class WizardDeviceNameTest(RenameCase):
    """REV-s1-t13 Л-1: the quick setup names the device «{кто} · {платформа}» itself (setup.js devName, no field for
    it). A person of 25 (iOS), 28 (Android, Windows), 40 letters or with emoji still gets the device: the «кто» part
    is cut with «…» to 40, the platform stays whole; the page and the server cut the same way (names.fit)."""
    WHO = {"25": "Бабушка Валентина Петрова", "28": "Бабушка Валентина Петровна И",
           "40": "Бабушка Валентина Петровна Иванова-Сидор", "emoji": "👨‍👩‍👧‍👦 Семья Ивановых и бабушка Валя 🏡"}
    PLATFORMS = ("ios", "android", "windows")

    def setUp(self):
        super().setUp()
        from kaskad.panel import auth
        from kaskad.setup import steps
        from tests.setup.test_setup_core import new_link, open_panel
        self.steps = steps
        open_panel()
        new_link()
        self.patches = [mock.patch.object(auth, "SCRYPT_N", 1 << 10),
                        mock.patch.object(steps, "online", lambda force=False: True)]
        for p in self.patches:
            p.start()
        self.assertEqual(steps.run("password", {"password": "Correct-Horse-9"}, self.w.runner)["state"], "ok")

    def tearDown(self):
        for p in self.patches:
            p.stop()
        super().tearDown()

    def test_fit_keeps_the_platform_and_the_limit(self):
        self.assertEqual(len(self.WHO["25"]), 25)
        self.assertEqual(len(self.WHO["28"]), 28)
        self.assertEqual(len(self.WHO["40"]), 40)
        self.assertEqual(names.fit("Мама", " · Android"), "Мама · Android")          # short — as is
        got = names.fit(self.WHO["40"], " · iPhone и iPad")
        self.assertEqual(got, "Бабушка Валентина Петро… · iPhone и iPad")
        self.assertEqual(len(got), names.MAX)
        # no joiner, variation selector, combining mark or space hangs on «…»
        for who in ("Ёлка" + "́" * 30, "Аня" + "‍" * 40, "x" * 22 + "  " + "y" * 30):
            got = names.fit(who, " · Windows")
            self.assertLessEqual(len(got), names.MAX)
            self.assertNotRegex(got, "[‍́ ]…")
            self.assertEqual(names.check(got, "device"), got)

    def test_wizard_person_step_creates_the_device(self):
        from kaskad.setup import product
        for key, who in self.WHO.items():
            for plat in self.PLATFORMS:
                with self.subTest(who=key, platform=plat):
                    title = product.platform_title(plat)
                    sent = "%s · %s" % (who, title)                        # what an old page (without the cut) sends
                    out = self.steps.run("person", {"user": who, "device": sent, "platform": plat}, self.w.runner)
                    self.assertEqual(out["state"], "ok", out)
                    d = model.get_device(state.load(), out["data"]["device"])
                    self.assertLessEqual(len(d["name"]), names.MAX)
                    self.assertTrue(d["name"].endswith(" · " + title), d["name"])
                    self.assertEqual(d["name"], names.fit(who, " · " + title))
                    if len(sent) > names.MAX:
                        self.assertIn("… · ", d["name"])
                    else:
                        self.assertEqual(d["name"], sent)
        # a person made before stage 1 with a name longer than 40 (read as it is) — the device is made too
        with state.transaction() as st:
            u = next(x for x in model.users(st) if x["id"] == self.uid)
            u["name"] = "Очень длинное старое имя человека до этапа один"
        out = self.steps.run("person", {"user": u["name"], "device": u["name"] + " · Android", "platform": "android"},
                             self.w.runner)
        self.assertEqual(out["state"], "ok", out)
        self.assertEqual(out["data"]["user"], self.uid)

    def test_page_cuts_the_same_way(self):
        from tests.test_332f_give import HAS_NODE, run_node
        if not HAS_NODE:
            self.skipTest("нет node")
        from kaskad.setup import product
        src = open(os.path.join(os.path.dirname(__file__), "..", "lib", "kaskad", "panel", "static", "setup",
                                "setup.js"), encoding="utf-8").read()
        start = src.index("  const NAME_MAX = 40;")
        end = src.index("\n  }\n", src.index("function devName(", start)) + 4
        # REV-s1-t13-2 Л-1а: the flag of England (a tag sequence, Cf) on the cut — the page must not leave a piece of the
        # tags (the server refuses it), and a spacing mark (Mc) stays as the server keeps it
        eng = "\U0001F3F4\U000E0067\U000E0062\U000E0065\U000E006E\U000E0067\U000E007F"
        edge = ["А" * n + eng + "Б" * 5 for n in range(22, 34)] + ["क" * 22 + "\u093e" + "ख" * 10]
        cases = [(who, product.platform_title(p)) for who in list(self.WHO.values()) + edge for p in self.PLATFORMS]
        js = ("const t = (k, o) => k === 'd.devName' ? o.who + ' · ' + o.p : k;\nconst pfTitle = (p) => p;\n"
              + src[start:end] + "\nconsole.log(JSON.stringify(%s.map(([w, p]) => devName(w, p))));\n"
              % json.dumps(cases, ensure_ascii=False))
        got = run_node(js)
        self.assertEqual(got, [names.fit(w, " · " + p) for w, p in cases])
        for name in got:                                  # what the page sends, the server takes (device_add → check)
            self.assertEqual(names.check(name, "device"), name)          # KaskadError — the device refused


class PeopleTextTest(unittest.TestCase):
    """REV-s1-t13-2 придирка 2: «Люди» мастера не повторяют имя человека и в урезанном имени устройства."""

    def test_cut_name_is_not_repeated(self):
        from tests.test_332f_give import HAS_NODE, run_node
        if not HAS_NODE:
            self.skipTest("нет node")
        src = open(os.path.join(os.path.dirname(__file__), "..", "lib", "kaskad", "panel", "static", "setup",
                                "setup.js"), encoding="utf-8").read()
        start = src.index("  function peopleText(")
        end = src.index("\n  }\n", start) + 4
        who = "Бабушка Валентина Петровна Иванова"
        cut = names.fit(who, " · iPhone и iPad")
        people = [{"name": who, "devices": [cut, who + " · Android", "Ноутбук"]},
                  {"name": "Бабушка Валя", "devices": ["Бабушка Валентина Пе… · Windows"]}]
        js = ("const t = (k, o) => k === 'd.devName' ? o.who + ' · ' + o.p : k;\nconst arr = (a) => a || [];\n"
              + src[start:end] + "\nconsole.log(JSON.stringify(%s.map(peopleText)));\n"
              % json.dumps(people, ensure_ascii=False))
        self.assertTrue(cut.endswith("… · iPhone и iPad"), cut)
        self.assertEqual(run_node(js), [who + ": iPhone и iPad, Android, Ноутбук",
                                        "Бабушка Валя: Бабушка Валентина Пе… · Windows"])


def _vless_world():
    from tests.test_gw_vless import VlessCase
    return VlessCase


class RealApplyGuardTest(_vless_world()):
    """REV-s1-t13 Л-2: «only labels change» with the REAL VLESS adapter (not dummies) and a real ``gw apply`` after
    the renames: every file of the node is the same as after a control apply without them, and the label of the
    protocol never reaches the issued link (the name of the key in the app — brand · protocol · device)."""
    LABEL = "для телефонов Оли"

    def _issue(self, did):
        from urllib.parse import unquote
        out = ops.issue(self.w.runner, did, inbound="vless")
        link = next(i["body"] for i in out["items"] if i["format"] == "link")
        return out, link.split("#")[0], unquote(link.split("#", 1)[1])

    def test_renames_change_no_file_after_a_real_apply_and_no_issued_link(self):
        from kaskad.gw import issue_os
        self.enabled_world()
        rc, doc, err = self.run_json("gw", "inbound", "install", "vless")
        self.assertEqual(rc, 0, err)
        self.ok("gw", "user", "add", "Мама")
        did = self.add_device("Телефон", "android")["id"]
        issued0, url0, name0 = self._issue(did)            # before the snapshots: issue logs its time (inbound-seen)
        self.assertEqual(name0, issue_os.app_label("VLESS", "Телефон"))
        ops.apply_all(self.w.runner)
        f0 = _files()
        with mock.patch.object(util, "now_iso", lambda: "2099-01-01T00:00:01Z"):    # another second for sure
            ops.apply_all(self.w.runner)                   # the control: a second apply changes nothing by itself
        f1 = _files()
        self.assertEqual(sorted(k for k in set(f0) | set(f1) if f0.get(k) != f1.get(k)), [])   # the list is whole
        login0 = model.get_device(state.load(), did)["login"]

        rename.rename(self.w.runner, "inbound", "vless", self.LABEL)
        rename.rename(self.w.runner, "user", "Мама", "Мама Оля")
        rename.rename(self.w.runner, "exit", self.vid, "Вена")
        rename.rename(self.w.runner, "device", did, "Телефон Оли")
        ops.apply_all(self.w.runner)                       # the real apply after the renames
        f2 = _files()
        changed = sorted(k for k in set(f1) | set(f2) if f1.get(k) != f2.get(k))
        self.assertEqual(changed, [])                      # xray.json, nft, secrets, the pool — as without renames
        self.assertEqual(model.get_device(state.load(), did)["login"], login0)
        root = paths.P("/")
        for d, _subs, fs in os.walk(root):                 # the label is in state.json and the journal only
            for f in fs:
                rel = os.path.relpath(os.path.join(d, f), root)
                if rel.endswith(("state.json", "kaskad.log")):
                    continue
                with open(os.path.join(d, f), "rb") as fh:
                    self.assertNotIn(self.LABEL.encode(), fh.read(), rel)
        issued2, url2, name2 = self._issue(did)
        self.assertEqual(url2, url0)                       # the key itself — the same
        self.assertEqual(name2, issue_os.app_label("VLESS", "Телефон Оли"))   # the device's name — after a re-issue
        self.assertNotIn(self.LABEL, json.dumps(issued2, ensure_ascii=False, default=str))
        rename.rename(self.w.runner, "device", did, "Телефон")
        issued3, _url3, _name3 = self._issue(did)          # the label is still set: the issue is byte for byte the first
        self.assertEqual(json.dumps(issued3, sort_keys=True, default=str), json.dumps(issued0, sort_keys=True,
                                                                                      default=str))
