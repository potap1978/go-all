"""3.3.5, REV-335-2 (the decisions of the coordinator 15.10, PLAN-3.4 «3.3.5, дубли AWG»): one key of AmneziaWG twice in a
pool of 3.3.4 — by IP and by domain. Kaskad removes nothing by itself: a refresh of the subscription matches the lines of
the pool by the key and the address first, then by the key (both copies keep their ids, Н1); removing one copy by hand
excludes that copy, not the key, while the other stays (Н2); the routes of the copy that goes move to the one that
stays (panel, SSH, CLI, MCP — install.sh egress_remove_key_now; a subscription that drops one copy too). The words of a
duplicate — one wording everywhere, «по двум адресам» only for two addresses (Н3, Н4); the wizard and the panel say
them (Н5). Н1 and Н2 — the reviewer's own scenarios (scratchpad revtests/test_rev335.py)."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from kaskad import state, util
from kaskad.errors import ConflictError
from kaskad.gw import exits, exitsubs, model, ops
from kaskad.i18n import use_lang
from tests import jsparts
from tests.fakes_gw import VLESS_A, scenario_state, write_pool
from tests.test_335_samekey import BY_DOMAIN, BY_IP, SameKeyCase
from tests.test_gw_exits import conf_of, vpn_of
from tests.test_gw_exitsubs import GA_URL

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib", "kaskad", "panel", "static")
OTHER_IP = "203.0.113.9:51820"


def read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


class TwoCopiesCase(SameKeyCase):
    """A pool of 3.3.4: one subscription owns both copies of one client — by IP and by domain."""

    def setUp(self):
        super().setUp()
        self.ip = self.line(conf_of("2.0", 3, endpoint=BY_IP), "Аня IP")
        self.dom = self.line(conf_of("2.0", 3, endpoint=BY_DOMAIN), "Аня домен")
        write_pool([self.ip, self.dom])
        self.old = [exits.exit_id(l) for l in (self.ip, self.dom)]
        doc = {"schema": 1, "next": 2, "subs": [{"id": "s-abcdef", "n": 1, "url": GA_URL, "auto": True, "pin": "",
                                                 "keys": self.old, "seen": self.old, "present": self.old,
                                                 "excluded": [], "added": "2026-10-14T00:00:00Z", "last_try": 1,
                                                 "last_ok": 1, "error": "", "count": 2, "full": 0}]}
        util.atomic_write(exitsubs.path(), json.dumps(doc), 0o600)

    def body(self, a, b, first="ip"):
        rows = [vpn_of(conf_of("2.0", 3, endpoint=BY_IP), a), vpn_of(conf_of("2.0", 3, endpoint=BY_DOMAIN), b)]
        return "\n".join(rows if first == "ip" else rows[::-1])

    def ids(self):
        return [i for i, _ in exits.line_ids()]

    def where(self):
        """{exit id: the address its line connects to now}."""
        return dict((i, exits.key_address(l)) for (i, _), l in zip(exits.line_ids(), exits.pool_lines()))


class RenameTest(TwoCopiesCase):
    def test_A_rename_of_both_legs_removes_none(self):
        """Н1 (the reviewer's scenario): the subscription renames both copies — nothing removed, both ids stay."""
        self.feed.bodies[GA_URL] = self.body("Аня IP · 9GB", "Аня домен · 9GB")
        r = self.refresh()["results"][0]
        self.assertEqual(r["removed"], 0, r["text"])
        self.assertEqual(self.ids(), self.old)
        self.assertEqual(r["updated"], 2, r["text"])

    def test_each_copy_keeps_its_own_address(self):
        """The key and the address first: the answer in another order — the IP copy still gets the IP line; then the
        key alone, when the address is new: the copy by IP now comes by another IP — the same exit."""
        self.feed.bodies[GA_URL] = self.body("Аня IP · 9GB", "Аня домен · 9GB", first="domain")
        r = self.refresh()["results"][0]
        self.assertEqual((r["added"], r["removed"], r["updated"]), (0, 0, 2), r["text"])
        self.assertEqual(self.where(), {self.old[0]: BY_IP, self.old[1]: BY_DOMAIN})
        self.feed.bodies[GA_URL] = "\n".join([vpn_of(conf_of("2.0", 3, endpoint=OTHER_IP), "Аня IP"),
                                              vpn_of(conf_of("2.0", 3, endpoint=BY_DOMAIN), "Аня домен")])
        r = self.refresh()["results"][0]
        self.assertEqual((r["added"], r["removed"]), (0, 0), r["text"])
        self.assertEqual(self.where(), {self.old[0]: OTHER_IP, self.old[1]: BY_DOMAIN})


class RemoveOneCopyTest(TwoCopiesCase):
    def test_B_extra_leg_removed_by_hand_then_rename_keeps_the_other(self):
        """Н2 (the reviewer's scenario): one copy removed as the warning says — the other stays after a renaming."""
        self.feed.bodies[GA_URL] = self.body("Аня IP", "Аня домен")
        self.refresh()                                   # idents written (3.3.5)
        ops.exit_del(self.w.runner, self.old[1])         # «удалите лишний»
        self.refresh()
        self.assertEqual(self.ids(), [self.old[0]])
        self.feed.bodies[GA_URL] = self.body("Аня IP · 9GB", "Аня домен · 9GB")
        r = self.refresh()["results"][0]
        self.assertEqual(self.ids(), [self.old[0]], r["text"])
        self.assertEqual(r["dups"], 0, r["text"])        # the copy removed by hand stays out silently, not a duplicate

    def test_only_the_copy_is_excluded_while_the_other_stays(self):
        """… and when the last copy goes too, the key itself is excluded: no renaming brings either back."""
        self.feed.bodies[GA_URL] = self.body("Аня IP", "Аня домен")
        self.refresh()
        sub = exitsubs.load()["subs"][0]
        ident, place = sub["idents"][self.old[1]], sub["places"][self.old[1]]
        ops.exit_del(self.w.runner, self.old[1])
        self.refresh()
        sub = exitsubs.load()["subs"][0]
        self.assertEqual(sub["excluded"], [self.old[1]])
        self.assertEqual(sub["excluded_idents"], [place])
        self.assertNotIn(ident, sub["excluded_idents"])
        ops.exit_del(self.w.runner, self.old[0])
        self.feed.bodies[GA_URL] = self.body("Аня IP · 9GB", "Аня домен · 9GB")
        r = self.refresh()["results"][0]
        self.assertEqual(self.ids(), [], r["text"])
        self.assertIn(ident, exitsubs.load()["subs"][0]["excluded_idents"])


class OneAnswerTest(SameKeyCase):
    def test_the_same_key_twice_in_one_answer_is_said(self):
        """Н9: by IP and by domain in one answer — one exit and «пропущено дублей: 1» with the name, not silence."""
        with use_lang("ru"):
            res = self.add(GA_URL, "\n".join([vpn_of(conf_of("2.0", 3, endpoint=BY_IP), "Аня"),
                                              vpn_of(conf_of("2.0", 3, endpoint=BY_DOMAIN), "Аня")]))
        r = res["result"]
        eid = exits.exit_id(exits.pool_lines()[0])
        self.assertEqual((r["added"], r["dups"], r["count"]), (1, 1, 2), r["text"])
        self.assertIn("Пропущено дублей: 1 — этот ключ уже есть как %s («Аня»), по другому адресу." % eid, r["text"])
        self.assertNotIn("резервной копии", r["text"])           # by IP and by domain is one server: no hint


class WordsTest(SameKeyCase):
    def refused(self, pool, text, lang="ru"):
        write_pool(pool)
        self.w.script.add_result = (4, "Новых ключей не добавлено (дубликаты или пусто).\n", "")
        with use_lang(lang), self.assertRaises(ConflictError) as c:
            ops.exit_add(self.w.runner, text)
        return str(c.exception)

    def test_written_differently_has_no_words_of_two_addresses(self):
        """Н3: VLESS under another name — «записан иначе», nothing about addresses or connections."""
        eid = exits.exit_id(VLESS_A)
        for lang, want in (("ru", "Второй раз не добавляю: этот ключ уже есть как %s («Vienna»), записан иначе."),
                           ("en", "Not adding it twice: this key is already there as %s (“Vienna”), written "
                                  "differently.")):
            self.assertEqual(self.refused([VLESS_A], VLESS_A.split("#")[0] + "#Other", lang), want % eid)
        conf = conf_of("2.0", 3)
        msg = self.refused([self.line(conf)], conf)                      # .conf of a key the pool has as vpn://
        self.assertTrue(msg.endswith(", записан иначе."), msg)
        self.assertNotIn("ног", msg)

    def test_by_ip_and_by_domain_says_one_connection(self):
        msg = self.refused([self.line(conf_of("2.0", 3, endpoint=BY_IP))],
                           vpn_of(conf_of("2.0", 3, endpoint=BY_DOMAIN), "Аня"))
        self.assertTrue(msg.endswith("по другому адресу. Работать будет только одно подключение с этим ключом."), msg)
        self.assertNotIn("резервной копии", msg)

    def test_two_ips_hint_a_server_restored_from_a_copy(self):
        """A known limit: a server restored from a backup of this one has the same keys — the reason says it."""
        msg = self.refused([self.line(conf_of("2.0", 3, endpoint=BY_IP))],
                           vpn_of(conf_of("2.0", 3, endpoint=OTHER_IP), "Аня"))
        self.assertIn("по другому адресу (если это другой сервер, восстановленный из той же резервной копии, у него те "
                      "же ключи — выдайте на нём новый ключ)", msg)
        en = self.refused([self.line(conf_of("2.0", 3, endpoint=BY_IP))],
                          vpn_of(conf_of("2.0", 3, endpoint=OTHER_IP), "Аня"), "en")
        self.assertIn("if it is another server restored from the same backup", en)

    def test_the_warning_of_the_pool_in_plain_words(self):
        write_pool([self.line(conf_of("2.0", 3, endpoint=BY_IP), "Аня"),
                    self.line(conf_of("2.0", 3, endpoint=BY_DOMAIN), "Аня")])
        pool = exits.pool(self.w.runner)
        with use_lang("ru"):
            ru = exits.same_peer_problems({}, pool)
        with use_lang("en"):
            en = exits.same_peer_problems({}, pool)
        self.assertEqual(ru, ["Ключ «Аня» добавлен дважды: по IP и по домену. Работать будет только один — удалите "
                              "лишний, маршруты перейдут на оставшийся."])
        self.assertEqual(en, ["Key “Аня” is added twice: by IP and by domain. Only one will work — delete the extra "
                              "one, the routes will move to the one that stays."])
        write_pool([self.line(conf_of("2.0", 3, endpoint=BY_IP), "Аня"),
                    self.line(conf_of("2.0", 3, endpoint=OTHER_IP), "Аня")])
        with use_lang("ru"):
            self.assertIn(": по двум адресам.", exits.same_peer_problems({}, exits.pool(self.w.runner))[0])


class HandoverTest(TwoCopiesCase):
    """The routes of the copy that goes move to the one that stays (the default, a person, a device; the fail kept)."""

    def setUp(self):
        super().setUp()
        st, _vid, _aid = scenario_state()
        write_pool([self.ip, self.dom])                  # scenario_state wrote its own pool
        g = st["gateway"]
        g["routes"]["default"] = {"via": "exit:" + self.old[1], "fail": "block"}
        g["users"][0]["route"] = {"via": "exit:" + self.old[1], "fail": "direct"}
        g["devices"][0]["route"] = {"via": "exit:" + self.old[1], "fail": "block"}
        g["devices"][2]["route"] = {"via": "exit:" + self.old[0], "fail": "direct"}
        state.save(st)

    def routes(self):
        g = model.peek(state.load())
        return (g["routes"]["default"], g["users"][0]["route"], g["devices"][0]["route"], g["devices"][2]["route"])

    def test_the_copy_that_goes_gives_its_routes(self):
        with use_lang("ru"):
            dry = ops.exit_handover(2, dry=True)
        self.assertEqual((dry["id"], dry["twin"], dry["routes"]), (self.old[1], self.old[0], 3))
        self.assertEqual(dry["text"], "Этот ключ есть в пуле ещё раз — «Аня IP»: маршруты перейдут на него.")
        self.assertEqual(self.routes()[0]["via"], "exit:" + self.old[1])          # dry: nothing moved
        self.assertEqual(ops.exit_handover(2, "0000000000")["twin"], None)      # not that line any more: nothing
        with use_lang("ru"):
            res = ops.exit_handover(2, exits.exit_id(self.dom)[2:12])
        self.assertEqual((res["routes"], res["text"]), (3, "Маршруты перешли на «Аня IP» — тот же ключ."))
        via = "exit:" + self.old[0]
        self.assertEqual([(r["via"], r["fail"]) for r in self.routes()],
                         [(via, "block"), (via, "direct"), (via, "block"), (via, "direct")])

    def test_one_copy_only_moves_nothing(self):
        write_pool([self.ip])
        res = ops.exit_handover(1)
        self.assertEqual((res["twin"], res["routes"], res["text"]), (None, 0, ""))

    def test_exit_del_tells_where_the_routes_went(self):
        """The panel, the bot, the CLI, MCP: the answer of the removal says it (install.sh moved them)."""
        with use_lang("ru"):
            res = ops.exit_del(self.w.runner, self.old[1])
        self.assertEqual((res["twin"], res["moved"]), (self.old[0], 3))
        self.assertEqual(res["text"], "Маршруты перешли на «Аня IP» — тот же ключ.")
        rows = ops.exits_view(self.w.runner)
        self.assertNotIn("twin", rows[0])                 # one copy left: nothing to say any more

    def test_the_rows_name_the_other_copy_both_ways(self):
        rows = ops.exits_view(self.w.runner)
        self.assertEqual([(r["id"], r.get("twin"), r.get("same_how")) for r in rows],
                         [(self.old[0], self.old[1], None), (self.old[1], self.old[0], "ip_domain")])

    def test_a_subscription_that_drops_one_copy_moves_its_routes(self):
        self.feed.bodies[GA_URL] = vpn_of(conf_of("2.0", 3, endpoint=BY_IP), "Аня IP")
        r = self.refresh()["results"][0]
        self.assertEqual((r["removed"], self.ids()), (1, [self.old[0]]), r["text"])
        self.assertEqual(set(x["via"] for x in self.routes()), {"exit:" + self.old[0]})

    def test_the_cli_says_it(self):
        with use_lang("ru"):
            rc, out, _ = self.run_cli("gw", "exit-handover", "2", "--dry")
        self.assertEqual((rc, out.strip()), (0, "Этот ключ есть в пуле ещё раз — «Аня IP»: маршруты перейдут на него."))
        with use_lang("ru"):
            rc, out, _ = self.run_cli("gw", "exit-del", self.old[1])
        self.assertEqual(rc, 0)
        self.assertIn("Маршруты перешли на «Аня IP» — тот же ключ.", out)


class InstallShTest(unittest.TestCase):
    """install.sh: every removal by hand hands the routes over first; the menu asks with the words of the core; the
    import that replaces the pool drops a key that is already in the list (REV-335-2)."""

    @classmethod
    def setUpClass(cls):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "install.sh"), encoding="utf-8") as f:
            cls.sh = f.read()

    def body(self, name):
        start = self.sh.index("\n%s() {" % name)
        return self.sh[start:self.sh.index("\n}\n", start)]

    def test_remove_hands_over_before_the_line_goes(self):
        b = self.body("egress_remove_key_now")
        self.assertLess(b.index('egress_handover "$((10#$idx))" "$hex"'), b.index("egress_pool_drop"))
        self.assertIn('k3ctl gw exit-handover "$1" ${2:+"$2"}', self.body("egress_handover"))

    def test_the_menu_asks_with_the_words_of_the_core(self):
        menu = self.body("egress_menu")
        self.assertLess(menu.index('s=$(egress_handover_ask "$sel")'), menu.index("tf EGRESS_DEL_ASK"))

    def test_import_drops_the_same_key(self):
        b = self.body("egress_import_now")
        self.assertIn('python3 "$EGRESS_PARSE_PY" peers', b)
        self.assertLess(b.index("EGRESS_IMPORT_SAME"), b.index('> "$EGRESS_UP"'))


class WizardTest(unittest.TestCase):
    def test_the_step_says_why_a_key_is_in_the_pool(self):
        """Н5: the wizard shows the reason of the core («этот ключ уже есть как …, по другому адресу»)."""
        from tests.test_qs330_e import WizardCase
        from kaskad.setup import steps

        class Progress(object):
            def __init__(self):
                self.rows = []

            def item(self, key, st, detail="", title=None):
                self.rows.append({"key": key, "state": st, "detail": detail})

        why = "этот ключ уже есть как e-56fccd0f9b («Аня»), по другому адресу"
        result = {"ok": True, "text": "Добавлен.", "added": [{"id": "e-1111111111"}],
                  "results": [{"id": "e-1111111111", "result": "added", "title": "VLESS", "where": "x"},
                              {"id": "e-2222222222", "result": "in_pool", "why": why, "title": "AmneziaWG 2.0",
                               "where": "vpn.example.com"}]}
        case = WizardCase("setUp")
        case.setUp()
        try:
            p = Progress()
            with mock.patch("kaskad.gw.ops.exit_preview", lambda r, t: {"items": []}), \
                    mock.patch("kaskad.gw.ops.exit_add", lambda r, t: result), \
                    mock.patch("kaskad.setup.steps._route_after_add", lambda c: None):
                out = steps.run("exits-add", {"text": VLESS_A}, case.runner, progress=p)
        finally:
            case.doCleanups()
            case.tearDown()
        self.assertTrue(out["ok"], out)
        rows = dict((r["key"], r) for r in p.rows)
        self.assertEqual((rows["k-e-2222222222"]["state"], rows["k-e-2222222222"]["detail"]), ("warn", why))


@unittest.skipUnless(shutil.which("node"), "нет node: функции app.js не запустить")
class PanelTest(TwoCopiesCase):
    """Н5: the panel says the same — ``same_how`` («по IP и по домену») and the other copy in the question of removal."""

    def node(self, script):
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
            f.write(script)
            path = f.name
        try:
            res = subprocess.run(["node", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        finally:
            os.unlink(path)
        self.assertEqual(res.returncode, 0, res.stderr.decode("utf-8", "replace")[-2000:])
        return json.loads(res.stdout.decode("utf-8").strip().splitlines()[-1])

    def test_the_rows_and_the_question_of_removal(self):
        from tests.test_panel_js_contract import app_scope
        rows = json.loads(json.dumps(ops.exits_view(self.w.runner)))
        js = read("app.js")
        scope = "\n".join([jsparts.const(js, "exitNameOf"), jsparts.const(js, "exitTwinFit"),
                           jsparts.function(js, "exitPinned"), jsparts.const(js, "exAbRows"), jsparts.function(js, "exitDelWarn")])
        people = {"users": [], "devices": [{"id": "d%d" % i, "name": "D%d" % i,             # REV-335-3: with routes only
                                            "route": {"via": "exit:" + e, "fail": "block"}} for i, e in enumerate(self.old)]}
        out = self.node(app_scope() + """
const DATA = %s;
const rows = app.normExits({ exits: DATA }).rows;
const t = (key, vars) => key + (vars ? " " + JSON.stringify(vars) : "");
const tn = (key, n, vars) => key + " " + n + (vars ? " " + JSON.stringify(vars) : "");
const R = { exits: { loaded: true, data: { rows: rows } }, people: { loaded: true, data: %s }, gw: { data: null } };
const exitOwn = (id) => /^e-[0-9a-f]{10}$/.test(String(id));
%s
console.log(JSON.stringify({ rows: rows.map((r) => [r.samePeer, r.sameHow, r.twin]),
                             warn: rows.map((r) => exitDelWarn(r)) }));
""" % (json.dumps(rows, ensure_ascii=False), json.dumps(people), scope))
        self.assertEqual(out["rows"], [["", "text", self.old[1]], [self.old[0], "ip_domain", self.old[0]]])
        self.assertIn('ex.delTwin {"name":"Аня домен"}', out["warn"][0])
        self.assertIn('ex.delTwin {"name":"Аня IP"}', out["warn"][1])
        self.assertIn('"ex.samePeer", { name: exitNameOf(r.samePeer), how: t(EXIT_TWICE[r.sameHow] || "ex.twice.text") })',
                      js)
        for name in ("i18n-ru.js", "i18n-en.js"):
            words = read(name)
            for key in ("ex.samePeer", "ex.twice.ip_domain", "ex.twice.address", "ex.twice.text", "ex.delTwin"):
                self.assertIn('"%s":' % key, words, (name, key))
        self.assertIn('"ex.samePeer": "Ключ «{name}» добавлен дважды: {how}. Работать будет только один — удалите '
                      'лишний, маршруты перейдут на оставшийся."', read("i18n-ru.js"))


if __name__ == "__main__":
    unittest.main()
