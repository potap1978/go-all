"""T8 part 2 — the panel on the LIVE API of T7 (SPEC-stage1-exits §7.9): the normalizers of app.js and the names the part
«Выходы → Группы» reads, against the real answers of the server on the fake node (tests/panel/test_panel_policy.py).

The mock of part 1 had its own shapes (``state`` of the policy, ``country: {code, was}``, columns ``rtt_ms``); the server
answers ``health`` (``state`` keeps alive/dead of 3.3.5), ``country`` + ``country_expected``, the columns of
``metrics.history``. A drift of either side fails here, not on the stand. Skipped without node."""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from kaskad import snapshot, state
from kaskad.gw import exits, metrics, model
from tests import jsparts
from tests.panel.test_panel_policy import PolicyCase

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib", "kaskad", "panel", "static")


def read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


def run_node(data, script):
    js = read("app.js")
    parts = [jsparts.const(js, n) for n in ("known", "isObj", "firstOf", "arr", "num", "byName", "PSTATES")]
    parts += [jsparts.function(js, n) for n in ("normExits", "normExitPolicy", "exitStateName", "normPolicy", "normRoute",
                                                "normDevice", "normPeople")]
    src = ("const LOCALE = 'ru-RU';\n%s\nconst DATA = %s;\n%s\n"
           % ("\n".join(parts), json.dumps(data, ensure_ascii=False), script))
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(src)
        path = f.name
    try:
        res = subprocess.run(["node", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    finally:
        os.unlink(path)
    if res.returncode != 0:
        raise AssertionError(res.stderr.decode("utf-8", "replace")[-2000:])
    return json.loads(res.stdout.decode("utf-8").strip().splitlines()[-1])


def rule_scope(rows, policy):
    """The pieces of the form of a condition (pages/exits-groups.js) that check a rule as the core does: ``ruleErrors``
    and the period ``pNom`` (policy.p_nom); ``rows`` — the exits of the part ({id, kind, role}), ``policy`` — the doc."""
    src = read("pages/exits-groups.js")
    parts = [jsparts.function(src, n) for n in ("cond", "rule", "ruleErrors", "freqP", "observatoryOf", "obsLineOf", "observatoryS", "pNom",
                                              "groupRuleErrors", "expectedCountry", "validGroup")]
    parts += [jsparts.const(src, n) for n in ("RULE_TPL", "FREQ_P", "NUM_METRIC", "METRIC_UI", "holdS", "isLazy", "exitsOfG",
                                              "kindOf", "whenConds", "freqValid", "newTierValid")]
    return ("const t = (k) => k;\nconst ROWS = %s;\nconst rowOf = (id) => ROWS.find((r) => r.id === id) || null;\n"
            "const POLICY = %s;\nconst pol = () => (POLICY ? { policy: POLICY } : null);\nconst exitsKnown = () => true;\n%s\n"
            % (json.dumps(rows), json.dumps(policy), "\n".join(parts)))


@unittest.skipUnless(shutil.which("node"), "нет node")
class ObservatoryAsTheCoreTest(unittest.TestCase):
    def test_the_shared_xray_check_as_the_core(self):
        """REV-s1-t8m З-4: общая проверка xray (observatoryOf) — как policy.observatory_s ядра с pool: российская группа
        не задаёт её, выход без строки в пуле не считается; строка «её задаёт группа «G»» (obsLineOf) — только у
        зарубежной группы (у российской xray-выходы проверяются её частотой, policy.period)."""
        from kaskad.gw import policy as P
        mk = lambda gid, role, mode, ex: {"id": gid, "role": role, "checks": {"freq": {"mode": mode}}, "tiers": [{"exits": ex}]}
        rows = [{"id": "e-a", "kind": "vless", "role": "abroad"}, {"id": "e-r", "kind": "vless", "role": "ru"},
                {"id": "e-b", "kind": "vless", "role": "abroad"}]
        docs = {
            "ru": {"groups": [mk("main", "abroad", "often", ["e-a"]), mk("g-ru0001", "ru", "rare", ["e-r"])]},
            "stale": {"groups": [mk("main", "abroad", "rare", ["e-a"]), mk("g-st0001", "abroad", "often", ["e-gone"])]},
            "two": {"groups": [mk("main", "abroad", "rare", ["e-a"]), mk("g-5f1a2b", "abroad", "often", ["e-b"])]},
        }
        got = {}
        for name, doc in docs.items():
            got[name] = run_node({}, rule_scope(rows, doc) + """
console.log(JSON.stringify(POLICY.groups.map((g) => { const o = observatoryOf(g), l = obsLineOf(g); return [o && o.s, l && l.gid]; })));
""")
        pool = [{"id": r["id"], "kind": r["kind"], "role": r["role"]} for r in rows]
        for name, doc in docs.items():
            core = P.observatory_s(doc, pool)
            self.assertEqual([x[0] for x in got[name]], [core] * len(doc["groups"]), name)
        self.assertEqual([x[1] for x in got["ru"]], [None, None])           # российской группе строки нет
        self.assertEqual([x[1] for x in got["stale"]], [None, None])        # 120 с у «Основной» — своя, не «пропавший» выход
        self.assertEqual([x[1] for x in got["two"]], ["g-5f1a2b", None])    # «Редко» у «Основной» — общую задаёт другая


@unittest.skipUnless(shutil.which("node"), "нет node")
class LivePanelTest(PolicyCase):
    def test_exit_rows_of_the_live_server(self):
        """``health`` is the state of the policy; ``country`` and ``country_expected`` give «было X, стало Y»; the
        owner's standby is ``manual`` with the reason ``manual_standby``."""
        self.both_ok()
        snap = exits.load_snapshot()
        snap["exits"][self.vid].update(state="degraded", reasons=["rule:r-rtt"], geo={"loc": "DE"})
        exits.save_snapshot(snap)
        with state.transaction() as st:
            model.set_exit(st, self.vid, country={"expected": "AT", "accepted_at": 1, "notified": []})
        snapshot.forget()
        rev = self.rev()
        self.ok("POST", "/api/gw/exits/%s/manual" % self.aid, {"state": "standby", "rev": rev})
        data = {"exits": self.ok("GET", "/api/gw/exits"), "policy": self.ok("GET", "/api/gw/policy")}
        out = run_node(data, """
const ex = normExits(DATA.exits), pol = normPolicy(DATA.policy);
console.log(JSON.stringify({ rows: ex.rows.map((r) => ({ id: r.id, pstate: r.pstate, has: r.hasPolicy, country: r.country, manual: r.manual,
  reasons: r.reasons.map((x) => x.code), group: r.group, tier: r.tier })), rev: ex.rev, prev: pol.rev,
  groups: pol.policy.groups.map((g) => [g.id, g.label, g.tiers.map((t) => t.label)]) }));
""")
        rows = dict((r["id"], r) for r in out["rows"])
        self.assertEqual(rows[self.vid]["pstate"], "degraded")
        self.assertTrue(rows[self.vid]["has"])
        self.assertEqual(rows[self.vid]["country"], {"code": "DE", "was": "AT"})
        self.assertEqual(rows[self.vid]["reasons"], ["rule"])
        self.assertEqual((rows[self.aid]["manual"], rows[self.aid]["group"], rows[self.aid]["tier"]), ("standby", "main", "t1"))
        self.assertEqual(out["rev"], out["prev"])
        self.assertEqual(out["groups"][0][:2], ["main", "Основная"])

    def test_the_part_reads_the_columns_of_the_history(self):
        """The charts of the detail ask the columns ``metrics.history`` answers (rx + tx — the traffic)."""
        src = read("pages/exits-groups.js")
        asked = set(re.findall(r'mk\("([a-z_]+)"', src)) - {"traffic"}
        cols = set(metrics.history("e-0000000000")["cols"])
        self.assertTrue(asked, "графики не найдены")
        self.assertLessEqual(asked, cols)
        self.assertIn('col("rx")', src)
        self.assertLessEqual({"rx", "tx"}, cols)

    def test_the_reasons_the_part_knows(self):
        """Every reason the health of an exit gives and every word of a transition has a text in RU and EN (an unknown
        code shows nothing, so a missing text is a silent hole)."""
        src = read("pages/exits-groups.js")
        alias = dict(re.findall(r'(\w+): "(\w+)"', src[src.index("const WHY_ALIAS"):src.index("\n", src.index("const WHY_ALIAS"))]))
        reasons = {"dead", "disabled", "maintenance", "manual_standby", "quota", "country", "unstable", "rule"}
        words = {"dead", "disabled", "maintenance", "quota", "country", "sub_confirm", "manual", "unstable", "rule"}
        for lang in ("ru", "en"):
            txt = read("pages/exits-groups-i18n-%s.js" % lang)
            for c in reasons:
                self.assertIn('"ep.why.%s"' % alias.get(c, c), txt, (lang, c))
                self.assertIn('"ep.why.%s"' % alias.get(c, c), src, c)
            for c in words:
                self.assertIn('"ep.whyT.%s"' % alias.get(c, c), txt, (lang, c))


    def test_the_conditions_the_form_builds_pass_the_server(self):
        """SE §7.2: every template of «+ условие» (the country filled in), a rule the form edits (any of, samples, only
        by hand, chosen exits, «только уведомить») — the real ``PUT /api/gw/policy?dry=1`` takes them; a rule the form
        calls wrong (back on the bad side, the return faster than the leave) — the server refuses it too."""
        pol = self.ok("GET", "/api/gw/policy")
        out = run_node({"g": pol["policy"]["groups"][0], "ex": [self.vid]}, """
%s
const g = DATA.g;
// «Страна не та» (REV-s1-t8-b П-5): ожидаемая страна группы — сразу в уходе и в возврате, код вводится один раз
const ge = JSON.parse(JSON.stringify(g)); ge.checks.geo = Object.assign({}, ge.checks.geo, { expect: "AT" });
const ctry = RULE_TPL.country(ge);
g.checks.services = [{ id: "yt", name: "YouTube", url: "https://example.com/" }];
const tpl = Object.keys(RULE_TPL).map((k) => { const r = RULE_TPL[k](g); if (k === "country") { r.when.conds[0].value = "DE"; r.back.conds[0].value = "DE"; } return r; });
const any = rule("loss", ">", 10, "pct", "standby", [cond("loss", "<", 5, "pct")], 1, 2); any.when.match = "any"; any.when.conds.push(cond("jitter", ">", 50, "ms")); any.when.hold = { samples: 3 }; any.exits = DATA.ex;
const manual = rule("rtt", ">", 300, "ms", "remove", [], 2, 2); manual.back = { match: "all", conds: [], manual: true };
const push = rule("traffic", ">", 50, "gb_day", "push", [], 1, 1); push.back = null;
const good = tpl.concat([any, manual, push]);
const badSide = rule("rtt", ">", 300, "ms", "degraded", [cond("rtt", "<", 400, "ms")], 2, 5);
const badHold = rule("rtt", ">", 300, "ms", "degraded", [cond("rtt", "<", 200, "ms")], 10, 2);
console.log(JSON.stringify({ ctry: [ctry.when.conds[0].value, ctry.back.conds[0].value], good, errs: good.map((r) => Object.keys(ruleErrors(r, g))), bad: [badSide, badHold], badErrs: [badSide, badHold].map((r) => Object.keys(ruleErrors(r, g))), services: g.checks.services }));
""" % rule_scope([], None))
        self.assertEqual(out["ctry"], ["AT", "AT"])
        self.assertEqual(out["errs"], [[]] * len(out["good"]))
        self.assertTrue(all(out["badErrs"]))
        doc = pol["policy"]
        for r in out["good"]:
            grp = dict(doc["groups"][0], rules=[r])
            grp["checks"] = dict(grp["checks"], services=out["services"])
            res = self.call("PUT", "/api/gw/policy?dry=1", {"policy": dict(doc, groups=[grp])})
            self.assertEqual(res.status, 200, (r, res.body.decode("utf-8")[:400]))
        for r in out["bad"]:
            res = self.call("PUT", "/api/gw/policy?dry=1", {"policy": dict(doc, groups=[dict(doc["groups"][0], rules=[r])])})
            self.assertEqual(res.status, 400, r)


    def test_the_notify_card_on_the_live_server(self):
        """SE §7.5, §8.1 (REV-s1-t8-a Б-1): the card shows every kind the server knows (``model.NOTIFY_PUSH``) and
        «склеивать повторы» (``coalesce``), sends only the changed keys — the live ``PUT /api/notify`` takes it; a stale
        ``ver`` is 409; a kind the server does not know (``node_online`` of the first draft) is 400 — the panel never
        sends one. 409 with another key changed elsewhere — silent rebase, the same key — «Применить снова»."""
        js, kit = read("app.js"), read("kit.js")
        parts = [jsparts.const(js, n) for n in ("NOTIFY_KEY", "NOTIFY_KINDS", "nfKinds", "nfPick", "nfKeys", "nfCount")]
        parts += [jsparts.function(js, "nfBody")]
        parts += [jsparts.const(kit, n) for n in ("getAt", "same", "copy", "membersOf")]
        parts += [jsparts.function(kit, n) for n in ("setAt", "dirtyKeys", "reorder", "rebaseModel")]
        prelude = "const window = {};\n%s\nwindow.Kit = { dirtyKeys, rebaseModel };\nconst nf = {};\n" % "\n".join(parts)
        got = self.ok("GET", "/api/notify")
        out = run_node({"got": got}, prelude + """
nf.kinds = nfKinds(DATA.got); nf.base = nfPick(DATA.got, nf.kinds); nf.draft = JSON.parse(JSON.stringify(nf.base)); nf.ver = DATA.got.ver;
const clean = nfCount();
nf.draft.push.exit_dead = !nf.draft.push.exit_dead; nf.draft.push.coalesce = !nf.draft.push.coalesce;
console.log(JSON.stringify({ kinds: nf.kinds, keys: Object.keys(nf.base.push), clean, count: nfCount(), body: nfBody() }));
""")
        server = set(model.NOTIFY_PUSH)
        self.assertEqual(set(out["kinds"]), server - {"coalesce"})
        self.assertEqual(set(out["keys"]), server)
        self.assertEqual((out["clean"], out["count"]), (0, 2))
        body = out["body"]
        self.assertEqual(sorted(body["push"]), ["coalesce", "exit_dead"])
        self.assertNotIn("flap_summary", body)
        res = self.ok("PUT", "/api/notify", body)
        self.assertEqual(res["push"]["exit_dead"], body["push"]["exit_dead"])
        self.assertEqual(res["push"]["coalesce"], body["push"]["coalesce"])
        self.assertEqual(self.call("PUT", "/api/notify", body).status, 409)                      # the old ver
        self.assertEqual(self.call("PUT", "/api/notify", {"push": {"node_online": True}, "ver": res["ver"]}).status, 400)
        # 409: another tab changed another kind — the edit stays (silent); the same kind — the list «Применить снова»
        fresh = self.ok("PUT", "/api/notify", {"push": {"exit_quota": not res["push"]["exit_quota"]}, "ver": res["ver"]})
        out = run_node({"base": res, "fresh": fresh}, prelude + """
nf.kinds = nfKinds(DATA.base); nf.base = nfPick(DATA.base, nf.kinds); nf.ver = DATA.base.ver;
const fresh = nfPick(DATA.fresh, nf.kinds), flip = (o, k) => { o.push[k] = !o.push[k]; return o; };
const rb = (mine) => rebaseModel(nf.base, mine, fresh, dirtyKeys(nf.base, mine, nfKeys()));
const silent = rb(flip(JSON.parse(JSON.stringify(nf.base)), "group_restored"));
const list = rb(flip(flip(JSON.parse(JSON.stringify(nf.base)), "group_restored"), "exit_quota"));
nf.draft = silent.draft; nf.base = fresh; nf.ver = DATA.fresh.ver;
console.log(JSON.stringify({ silent: [silent.mode, silent.draft.push.group_restored, silent.draft.push.exit_quota], list: list.mode, body: nfBody() }));
""")
        self.assertEqual(out["silent"], ["silent", not res["push"]["group_restored"], fresh["push"]["exit_quota"]])
        self.assertEqual(out["list"], "list")
        self.assertEqual(out["body"]["push"], {"group_restored": not res["push"]["group_restored"]})
        again = self.ok("PUT", "/api/notify", out["body"])                                     # «Сохранить» ещё раз
        self.assertEqual(again["push"]["exit_quota"], fresh["push"]["exit_quota"])             # the other tab's edit kept

    def test_the_time_card_on_the_live_server(self):
        """UI-A1 «Настройки › Время»: the body of the card (``tmBody``: the zone and ``ver``) is what the live
        ``PUT /api/settings/time`` takes; the old ``ver`` is 409; after 409 the card re-reads (``timeLoad(true)``): the
        zone changed elsewhere — the list «Применить снова», and «Сохранить» again with the fresh ``ver`` passes."""
        js, kit = read("app.js"), read("kit.js")
        parts = [jsparts.const(js, n) for n in ("tzf", "tmCount", "tmBody")]          # isObj — из run_node
        parts += [jsparts.function(js, n) for n in ("tmTake", "timeLoad")]
        parts += [jsparts.const(kit, n) for n in ("getAt", "same", "copy", "membersOf")]
        parts += [jsparts.function(kit, n) for n in ("setAt", "dirtyKeys", "reorder", "rebaseModel")]
        prelude = ("const window = {};\nconst t = (k) => k;\nconst scheduleRefresh = () => {};\nlet ANSWER = null;\n"
                   "const API = { time: { get: () => Promise.resolve(ANSWER) } };\n%s\nwindow.Kit = { dirtyKeys, rebaseModel };\n"
                   % "\n".join(parts))
        got = self.ok("GET", "/api/settings/time")
        other = [z for z in got["zones"] if z != got["tz"]]
        self.assertTrue(len(other) >= 2, "в базе поясов узла меньше трёх поясов")
        mine, theirs = other[0], other[1]
        out = run_node({"got": got, "mine": mine}, prelude + """
ANSWER = DATA.got;
timeLoad().then(() => { tzf.draft.tz = DATA.mine; console.log(JSON.stringify({ count: tmCount(), body: tmBody() })); });
""")
        self.assertEqual((out["count"], out["body"]), (1, {"ver": got["ver"], "tz": mine}))
        res = self.ok("PUT", "/api/settings/time", out["body"])
        self.assertEqual(res["tz"], mine)
        self.assertEqual(self.call("PUT", "/api/settings/time", out["body"]).status, 409)                   # the old ver
        # another tab changes the zone while this one edits it — 409, re-read: the list, then «Сохранить» passes
        fresh = self.ok("PUT", "/api/settings/time", {"tz": theirs, "ver": res["ver"]})
        out = run_node({"base": res, "fresh": fresh, "mine": got["tz"]}, prelude + """
ANSWER = DATA.base;
timeLoad().then(() => { tzf.draft.tz = DATA.mine; ANSWER = DATA.fresh; return timeLoad(true); }).then((r) => {
  console.log(JSON.stringify({ mode: r.mode, list: tzf.list.map((e) => e.mine), draft: tzf.draft.tz, ver: tzf.ver, body: (tzf.draft.tz = tzf.list[0].mine, tmBody()) }));
});
""")
        self.assertEqual((out["mode"], out["list"], out["draft"], out["ver"]), ("list", [got["tz"]], theirs, fresh["ver"]))
        again = self.ok("PUT", "/api/settings/time", out["body"])                                            # «Применить снова» и «Сохранить»
        self.assertEqual(again["tz"], got["tz"])

    def test_impact_lines_of_the_last_exit(self):
        """REV-s1-t8-a С-1: the live dry run of «выключить» on the last working exit of «Основная» (block) — «Что
        изменится» says «1 устройство останется без интернета» (moves.by_group.main.last_block), not just «переедут»."""
        self.both_ok()
        on = self.pinned()
        other = self.aid if on == self.vid else self.vid
        self.ok("POST", "/api/gw/exits/%s/enabled" % other, {"on": False, "rev": self.rev()})
        dry = self.ok("POST", "/api/gw/exits/%s/enabled?dry=1" % on, {"on": False, "rev": self.rev()})
        kit = read("kit.js")
        dict_ru = read("kit-i18n-ru.js").split('window.KASKAD_I18N_ADD["kit"] = ', 1)[1].rsplit(";", 1)[0]
        out = run_node({"dry": dry}, """
const window = {}; %s
const DICT = %s;
const fill = (s, v) => String(s).replace(/\\{(\\w+)\\}/g, (m, k) => (v && v[k] !== undefined ? String(v[k]) : m));
const t = (k, v) => fill(DICT[k] || k, v);
const tn = (k, n, v) => { const f = String(DICT[k] || k).split("|"); const i = new Intl.PluralRules("ru").select(n); return fill(f[Math.min(i === "one" ? 0 : i === "few" ? 1 : 2, f.length - 1)], Object.assign({ n }, v)); };
window.Kit.init({ el: () => ({}), t, tn, icon: () => ({}), isPhone: () => false, U: {}, layers: { openDialog() {}, openSheet() {}, openMenu() {}, confirmDialog() {}, typedConfirm() {}, toast() {} } });
console.log(JSON.stringify(window.Kit.impactLines(DATA.dry, { group: () => "Основная" })));
""" % (kit, dict_ru))
        self.assertEqual(dry["moves"]["by_group"]["main"]["last_block"], 1, dry["moves"])
        self.assertIn("1 устройство останется без интернета", out)

    def test_delete_of_a_group_people_use(self):
        """REV-s1-t8-a С-2: a group with an exit, a person whose route is the group and the bot's proxy — the dialog
        learns what to ask from the live 409 «need» of a dry run without parameters, sends only those
        (``delParams``) — the dry run and the delete pass; the person's route goes to the chosen group."""
        src = read("pages/exits-groups.js")
        parts = [jsparts.const(src, "isNeed"), jsparts.function(src, "mergeNeed"), jsparts.function(src, "delParams")]
        gid = self.add_group()
        self.ok("POST", "/api/gw/exits/%s/move" % self.aid, {"group": gid, "rev": self.rev()})
        st = state.load()
        model.get_user(st, self.uid)["route"] = {"via": "group:" + gid, "fail": "block"}
        st["gateway"]["exit_policy"]["tg_proxy_group"] = gid
        state.save(st)
        snapshot.forget()
        need = self.err(409, "DELETE", "/api/gw/groups/%s?dry=1" % gid)
        self.assertEqual(need["code"], "need")
        out = run_node({"need": need["data"]}, "%s\nconsole.log(JSON.stringify({ q: delParams(DATA.need, { to: 'main', reassign: 'main' }), "
                       "m: mergeNeed({ exits: ['x'], uses: ['tg_proxy'] }, { refs: { users: ['u'] }, uses: ['subs', 'tg_proxy'] }) }));" % "\n".join(parts))
        q = out["q"]
        self.assertEqual(q, {"exits_to": "main", "reassign": "main", "tg_proxy_to": "main"})
        self.assertEqual(out["m"], {"exits": ["x"], "refs": {"users": ["u"]}, "uses": ["tg_proxy", "subs"]})
        qs = "&".join("%s=%s" % kv for kv in sorted(q.items()))
        dry = self.ok("DELETE", "/api/gw/groups/%s?dry=1&%s" % (gid, qs))
        self.assertEqual(dry["refs"]["users"], [self.uid])
        self.ok("DELETE", "/api/gw/groups/%s?rev=%d&%s" % (gid, self.rev(), qs))
        st = state.load()
        self.assertEqual(model.get_user(st, self.uid)["route"]["via"], "pool")
        self.assertEqual(st["gateway"]["exit_policy"]["tg_proxy_group"], "main")

    def test_the_form_checks_a_rule_as_the_core(self):
        """REV-s1-t8-b З-1, З-2: ``ruleErrors`` of the form gives the same places of errors as ``policy.validate`` of the
        core on a set of rules — the period of a measure as ``p_nom`` (AWG max(3P, 30), xray the observatory, «Лениво»
        15 s), «только вручную» with or without conditions of the return, the order of the checks (a bad hold stops the
        rest), the values, operators, services, exits. Panel only: an empty list of exits (asked in the form)."""
        import re as _re
        from kaskad.gw import policy as P, policy_ops as pops
        doc = self.ok("GET", "/api/gw/policy")["policy"]
        pool = pops.pool_rows()
        rows = [{"id": e["id"], "kind": e.get("kind"), "role": e.get("role", "abroad")} for e in pool]
        g0 = doc["groups"][0]
        C = lambda m, op, v, u: {"metric": m, "op": op, "value": v, "unit": u}
        def R(when, back, then="degraded", wh=None, bh=None, exits="all", manual=False, match="all"):
            r = {"id": "r-aaaa01", "name": "", "enabled": True, "exits": exits, "then": then, "push": True,
                 "when": {"match": match, "conds": when, "hold": wh if wh is not None else {"minutes": 1}}}
            if back is not None:
                r["back"] = {"match": "all", "conds": back, "manual": manual}
                if bh is not False:
                    r["back"]["hold"] = bh if bh is not None else {"minutes": 5}
            return r
        rtt, back = [C("rtt", ">", 300, "ms")], [C("rtt", "<", 200, "ms")]
        freq = lambda mode, **kw: dict(g0["checks"]["freq"], mode=mode, **kw)
        cases = [
            ("ok", R(rtt, back), None),
            ("З-1 awg 3P", R(rtt, back, wh={"samples": 3}, bh={"minutes": 2}), freq("normal")),
            ("З-1 xray only", R(rtt, back, wh={"samples": 3}, bh={"minutes": 2}, exits=[self.vid]), freq("normal")),
            ("З-1 awg only", R(rtt, back, wh={"samples": 3}, bh={"minutes": 2}, exits=[self.aid]), freq("normal")),
            ("often", R(rtt, back, wh={"samples": 4}, bh={"minutes": 1}), freq("often")),
            ("lazy 15", R(rtt, back, wh={"samples": 3}, bh={"minutes": 1}), freq("lazy")),
            ("lazy 15 slow", R(rtt, back, wh={"samples": 5}, bh={"minutes": 1}), freq("lazy")),
            ("custom 45", R(rtt, back, wh={"samples": 2}, bh={"minutes": 2}), freq("custom", every_s=45)),
            ("З-2 manual + conds", R(rtt, [C("rtt", "<", 400, "ms")], bh={"minutes": 0.5}, manual=True), None),
            ("manual + bad cond", R(rtt, [C("rtt", "<", -1, "ms")], manual=True), None),
            ("manual clean", R(rtt, [], bh=False, manual=True, then="remove"), None),
            ("push, no back", R(rtt, None, then="push"), None),
            ("standby, no back", R(rtt, None, then="standby"), None),
            ("remove, no hold", R(rtt, back, then="remove", bh=False), None),
            ("hold both", R(rtt, back, wh={"samples": 2, "minutes": 1}), None),
            ("back hold 0", R(rtt, [C("rtt", ">", 400, "ms")], bh={"samples": 0}), None),
            ("bad side", R(rtt, [C("rtt", "<", 400, "ms")]), None),
            ("x_norm vs ms", R(rtt, [C("rtt", "<", 2, "x_norm")]), None),
            ("country", R([C("country", "!=", "de", "iso")], [C("country", "==", "DE", "iso")]), None),
            ("loss 150", R([C("loss", ">", 150, "pct")], [C("loss", "<", 5, "pct")]), None),
            ("loss min_fails", R([dict(C("loss", ">", 10, "pct"), min_fails=20)], [C("loss", "<", 5, "pct")]), None),
            ("service unknown", R([dict(C("service", "==", False, "ok"), service="zz")], [dict(C("service", "==", True, "ok"), service="zz")]), None),
            ("six conds", R(rtt * 6, back, match="any"), None),
            ("op wrong", R([C("rtt", "==", 300, "ms")], back), None),
            ("unit wrong", R([C("loss", ">", 10, "ms")], [C("loss", "<", 5, "pct")]), None),
            ("exits foreign", R(rtt, back, exits=["e-0000000000"]), None),
            ("no conds", R([], back), None),
        ]
        def server_keys(rule, fr):
            grp = dict(g0, rules=[rule])
            if fr is not None:
                grp["checks"] = dict(g0["checks"], freq=fr)
            errs, _w = P.validate(dict(doc, groups=[grp] + doc["groups"][1:]), pool)
            out = set()
            for e in errs:
                f = e.get("field") or ""
                if e["code"] == "ep.err.conds_group":
                    out.add("rules")
                    continue
                self.assertTrue(f.startswith("groups[0].rules[0]"), (rule, e))
                f, c = f[len("groups[0].rules[0]") + 1:], e["code"]
                m = _re.match(r"(when|back)\.conds\[(\d+)\]", f)
                if c in ("ep.err.remove_back", "ep.err.back_hold"):
                    out.add("back.hold")
                elif c in ("ep.err.back_missing", "ep.err.back_side"):
                    out.add("back")
                elif c == "ep.err.rule_exits":
                    out.add("exits")
                elif m:
                    out.add("%s.%s" % m.groups())
                elif f in ("when.conds", "back.conds", "when", "back"):
                    out.add(f.split(".")[0])
                elif f.endswith(".hold"):
                    out.add(f)
                else:
                    out.add(f or c)
            return sorted(out)
        data = [{"rule": r, "freq": fr} for _n, r, fr in cases]
        got = run_node({"cases": data}, rule_scope(rows, doc) + """
console.log(JSON.stringify(DATA.cases.map((c) => { const g = JSON.parse(JSON.stringify(POLICY.groups[0])); g.rules = [c.rule];
  if (c.freq) g.checks.freq = c.freq; return Object.keys(Object.assign(ruleErrors(c.rule, g), groupRuleErrors(g))).sort(); })));
""")
        for (name, rule, fr), mine in zip(cases, got):
            self.assertEqual(mine, server_keys(rule, fr), name)
        # REV-s1-t8-2 П-3 (X14): шестая проверка ухода в группе — по 3 в двух условиях; ядро не примет, «Сохранить»
        # группы неактивна (validGroup), а не отказ сервера
        r1, r2 = R(rtt * 3, back), dict(R(rtt * 3, back), id="r-aaaa02")
        errs, _w = P.validate(dict(doc, groups=[dict(g0, rules=[r1, r2])] + doc["groups"][1:]), pool)
        self.assertIn("ep.err.conds_group", [e["code"] for e in errs])
        six = run_node({"rs": [r1, r2]}, rule_scope(rows, doc) + """
const g = JSON.parse(JSON.stringify(POLICY.groups[0])); g.rules = DATA.rs;
const five = JSON.parse(JSON.stringify(g)); five.rules[1].when.conds.pop();
console.log(JSON.stringify({ six: validGroup(g), five: validGroup(five), each: g.rules.map((r) => Object.keys(ruleErrors(r, g)).length) }));
""")
        self.assertEqual(six, {"six": False, "five": True, "each": [0, 0]})
        # «Только вручную» (З-2): the switch puts the conditions of the return aside — the core takes the rule; off — back
        src = read("pages/exits-groups.js")
        extra = "%s\n%s\nconst copy = (v) => JSON.parse(JSON.stringify(v));\n" % (jsparts.const(src, "BACK_KEPT"), jsparts.function(src, "setManual"))
        bad = R(rtt, [C("rtt", "<", 400, "ms")], bh={"minutes": 0.5})
        sw = run_node({"r": bad}, rule_scope(rows, doc) + extra + """
const r = DATA.r, g = JSON.parse(JSON.stringify(POLICY.groups[0])); g.rules = [r];
const before = Object.keys(ruleErrors(r, g)).sort();
setManual(r, true); const on = JSON.parse(JSON.stringify(r)), onErr = Object.keys(ruleErrors(r, g));
setManual(r, false);
console.log(JSON.stringify({ before, on, onErr, off: r.back }));
""")
        self.assertEqual(sw["before"], ["back", "back.hold"])
        self.assertEqual((sw["on"]["back"]["conds"], "hold" in sw["on"]["back"], sw["onErr"]), ([], False, []))
        self.assertEqual(server_keys(sw["on"], None), [])
        self.assertEqual(sw["off"]["conds"], bad["back"]["conds"])
        # the period of «З-1»: the form says the same as the core (awg: 3 × 30 = 90 s a measure)
        self.assertIn("back.hold", got[1])
        self.assertEqual(got[2], [])

    def test_the_quota_form_round_trip(self):
        """SE §7.3: the quota form sends the five settings with ``rev`` (and ``speed_skip``); the live server takes it,
        and the row read back gives the form the same quota (``quotaOf`` of the page) — no edit appears from nothing."""
        q = {"period": "month", "limit_gb": 60.5, "reset_day": 3, "warn_pct": 90, "action": "standby"}
        rev = self.rev()
        self.assertEqual(self.call("PUT", "/api/gw/exits/%s?dry=1" % self.vid, {"quota": q, "speed_skip": True}).status, 200)
        self.ok("PUT", "/api/gw/exits/%s" % self.vid, {"quota": q, "speed_skip": True, "rev": rev})
        data = {"exits": self.ok("GET", "/api/gw/exits"), "id": self.vid}
        src = read("pages/exits-groups.js")
        out = run_node(data, """
%s
const r = normExits(DATA.exits).rows.find((x) => x.id === DATA.id);
console.log(JSON.stringify({ q: quotaOf(r), skip: r.speedSkip }));
""" % jsparts.const(src, "quotaOf"))
        self.assertEqual(out, {"q": q, "skip": True})
        res = self.call("PUT", "/api/gw/exits/%s" % self.vid, {"quota": None, "rev": rev})      # the old rev
        self.assertEqual((res.status, json.loads(res.body)["code"]), (409, "policy_changed"))


    def test_the_limits_of_the_core_are_the_panels(self):
        """T7 круг 2: the panel's limits are the core's — tiers in a group (policy.MAX_TIERS), conditions, checks in a
        condition, the hold of «вернуть на основной» — so a form never builds what the server refuses."""
        from kaskad.gw import policy as P
        src = read("pages/exits-groups.js")
        self.assertIn("const MAX_TIERS = %d;" % P.MAX_TIERS, src)
        self.assertIn("g.rules.length >= %d" % P.MAX_RULES, src)
        self.assertIn("sd.conds.length < %d" % P.MAX_CONDS, src)
        self.assertIn("hold_min >= %d && g.return_to_primary.hold_min <= %d" % P.HOLD_MIN, src)


if __name__ == "__main__":
    unittest.main()
