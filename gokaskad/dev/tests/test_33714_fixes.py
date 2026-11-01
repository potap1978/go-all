"""Kaskad 3.3.7.14 — the small fixes after 3.3.7.13 (k3/handoff/briefs/BRIEF-33714-small-2026-10-30.md). Each test
fails on bc541e29 (3.3.7.13)."""
import ipaddress
import json
import os
import shutil
import subprocess
import tempfile
import unittest

from unittest import mock

from kaskad.gw import cli_policy, exits, exitsubs
from kaskad.i18n import use_lang
from tests.test_3351_cmdinj import function
from tests.test_3371_release import text
from tests.test_336_awgaddr import EMPTY, DnsCase

# The table of a leg (256+N) holds only ours — «unreachable default metric 1000» and «default dev kawgN» — and,
# on a server with source routing for a second address, the admin's «default via …» (PLAN-multi-ip Л2).
OURS_AND_FOREIGN = ("default via 85.143.0.1 dev eth0 src 85.143.0.10 \n"
                    "default dev kawg1 scope link \n"
                    "unreachable default metric 1000 \n")
LEG_FAKE = r"""
ip() {
    echo "IP:$*" >> "$CALLS"
    case "$*" in
        "-4 route show table 257") printf '%s' "$ROUTES" ;;
        "-4 rule show") printf '%s' "$RULES" ;;
        "rule del "*) [ -e "$CALLS.del.$*" ] && return 2; : > "$CALLS.del.$*" ;;
    esac
    return 0
}
systemctl() { echo "SYSTEMCTL:$*" >> "$CALLS"; }
pkill() { return 1; }
"""


class LegTableTest(unittest.TestCase):
    """№5: the removal of an AmneziaWG key (``egress_awg_leg_remove``) and of Kaskad (``k3_leg_routes_remove``) took
    the table 256+N away with ``ip route flush`` — the admin's routes in it too. Now only ours goes."""

    def run_bash(self, call, routes, rules=""):
        d = tempfile.mkdtemp(prefix="k3-33714-leg-")
        self.addCleanup(shutil.rmtree, d, True)
        calls = os.path.join(d, "calls")
        src = "".join(function(n) for n in ("egress_awg_leg_remove", "k3_leg_routes_remove", "egress_awg_route_leg"))
        try:
            src += function("egress_leg_table_clear")
        except AssertionError:                                    # 3.3.7.13: no such function
            pass
        src += "egress_leg_unit() { echo \"kaskad-leg@$1.service\"; }\n" + LEG_FAKE + call + "\n"
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LC_ALL": "C.UTF-8", "CALLS": calls,
               "ROUTES": routes, "RULES": rules, "AWG_DIR": d, "K3_UNIT_DIR": d}
        subprocess.run(["bash", "-c", "set -o pipefail\n" + src], capture_output=True, text=True, timeout=60, env=env)
        with open(calls, encoding="utf-8") as f:
            return f.read().splitlines()

    def check(self, calls):
        self.assertFalse([c for c in calls if "flush" in c], calls)
        self.assertIn("IP:route del unreachable default metric 1000 table 257", calls)
        self.assertIn("IP:route del default dev kawg1 table 257", calls)
        self.assertFalse([c for c in calls if c.startswith("IP:route del default via")], calls)   # the admin's stays

    def test_a_key_removed_takes_only_its_own_routes(self):
        calls = self.run_bash("egress_awg_leg_remove kawg1", OURS_AND_FOREIGN)
        self.assertIn("IP:rule del fwmark 257 table 257", calls)
        self.check(calls)

    def test_kaskad_removed_takes_only_its_own_routes(self):
        rules = "0:\tfrom all lookup local\n1001:\tfrom all fwmark 0x101 lookup 257\n32766:\tfrom all lookup main\n"
        calls = self.run_bash("k3_leg_routes_remove", OURS_AND_FOREIGN, rules)
        self.assertIn("IP:rule del pref 1001 fwmark 0x101 lookup 257", calls)
        self.check(calls)

    def test_a_table_of_only_ours_is_empty_after(self):
        calls = self.run_bash("egress_awg_leg_remove kawg1", "unreachable default metric 1000 \n")
        self.assertEqual([c for c in calls if c.startswith("IP:route ")],
                         ["IP:route del unreachable default metric 1000 table 257"])

    def test_keep_touches_no_route(self):
        """``keep`` (the key gone, xray of the bot still marks): the table stays closed — not a route goes."""
        calls = self.run_bash("egress_awg_leg_remove kawg1 keep", "unreachable default metric 1000 \n")
        self.assertFalse([c for c in calls if c.startswith("IP:route del")], calls)


class GwGroupsOrderTest(unittest.TestCase):
    """№4: ``kaskadctl gw groups`` listed the exits in the order of the pool (VLESS above Hy2 while the manual tier had
    Hy2 first and the line above said «первый рабочий выход сверху»). Now — the order of the queue, as «Уровни» of the
    panel: a manual tier — its list with the place, an automatic one — the rows of the collector (row 1, row 2, then
    the ones not taking part), the ms the collector chooses by; exits of no group last."""

    POL = {"groups": [
        {"id": "main", "name": "", "role": "abroad", "fail": "block", "tiers": [
            {"id": "t1", "exits": ["hy2", "vless"], "method": "best_rtt", "order": "manual"},
            {"id": "t2", "exits": ["c", "d", "e"], "method": "best_rtt"}]},
        {"id": "g-0a0b0c", "name": "Дом", "role": "abroad", "fail": "block", "tiers": [
            {"id": "t1", "exits": ["f", "g"], "method": "best_rtt"}]}]}
    RANKS = {"main": {"t2": {"by": "rtt", "order": "auto", "nodata": False,
                             "rows": [[{"id": "d", "v": 120.4, "share": 1.0}], [{"id": "c", "v": 300, "share": 0.0}]],
                             "out": [{"id": "e", "why": "degraded"}]}},
             "g-0a0b0c": {"t1": {"by": "weights", "order": "manual", "rows": [[{"id": "g", "v": 1, "share": 1.0}]]}}}

    def rows(self):
        return [{"id": i, "name": i.upper(), "health": "ok", "devices": 0, "rtt_ms": 999}
                for i in ("vless", "c", "z", "d", "e", "hy2", "g", "f")]

    def exit_lines(self, ranks):
        doc = {"groups": [dict(g, label=g["name"] or "Основная", exits=[]) for g in self.POL["groups"]], "no_exit": 0}
        return [x for x in cli_policy.groups_lines(doc, self.rows(), self.POL, ranks) if x.startswith("  ")]

    def test_the_order_of_the_queue(self):
        lines = self.exit_lines(self.RANKS)
        self.assertEqual([x.split(" — ")[0].strip() for x in lines], ["HY2", "VLESS", "D", "C", "E", "F", "G", "Z"])
        self.assertIn("HY2 — Основная · Уровень 1 · место 1 · работает", lines[0])
        self.assertIn("VLESS — Основная · Уровень 1 · место 2 · ", lines[1])
        self.assertIn("D — Основная · Уровень 2 · ряд 1 · работает · 120 мс", lines[2])
        self.assertIn("C — Основная · Уровень 2 · ряд 2 · ", lines[3])
        self.assertNotIn("ряд", lines[4])                                  # not taking part: no row
        self.assertNotIn("ряд", lines[5])                                  # rows of another order: the saved list
        self.assertIn("без группы", lines[7])
        with use_lang("en"):
            lines = self.exit_lines(self.RANKS)
        self.assertIn("HY2 — Main · Level 1 · place 1 · ", lines[0])
        self.assertIn("D — Main · Level 2 · row 1 · ", lines[2])

    def test_no_live_the_saved_lists(self):
        lines = self.exit_lines({})
        self.assertEqual([x.split(" — ")[0].strip() for x in lines], ["HY2", "VLESS", "C", "D", "E", "F", "G", "Z"])
        self.assertIn("999 мс", lines[2])


NODE_IP = "85.143.0.10"
REALITY = "?type=tcp&security=reality&pbk=8FXcm8haAt5zxGJFC6-71alFTj_LhAL5hbOx7QBE6lc&sid=80cd0a54&sni=www.microsoft.com"


def vless(host, name):
    return "vless://11111111-2222-3333-4444-555555555555@%s:443%s&fp=chrome#%s" % (host, REALITY, name)


def hy2(host, name):
    return "hy2://hyAuthSecret1@%s:443/?sni=%s&obfs=salamander&obfs-password=obfsSecret1#%s" % (host, host, name)


class SelfNameTest(DnsCase):
    """№3: «ключ указывает на этот сервер» (self_ep) — AmneziaWG по имени Endpoint (DNS) отказывал, а VLESS и Hysteria2
    с доменом этого же сервера принимались (сверка только по IP). Теперь имя сервера xray-ключа при добавлении
    разрешается так же (одним ожиданием с именами AmneziaWG); ключ, который уже в пуле из подписки, остаётся (как С3)."""

    def setUp(self):
        super().setUp()
        node = dict(EMPTY, addrs=[("eth0", ipaddress.ip_address(NODE_IP))])
        m = mock.patch.object(self.p, "_node_now", return_value=node)
        m.start()
        self.addCleanup(m.stop)
        self.dns.update({"b.example.com": [NODE_IP], "lo.example.com": ["198.51.100.40", "127.0.0.1"],
                         "far.example.com": ["198.51.100.41"]})

    def test_the_domain_of_this_node_is_refused(self):
        keys = [vless("b.example.com", "Vb"), hy2("b.example.com", "Hb"), vless("lo.example.com", "Vlo"),
                vless("far.example.com", "Vfar"), hy2("far.example.com", "Hfar"), vless("nx.example.com", "Vnx")]
        good, skipped = self.p.extract_report("\n".join(keys))
        self.assertEqual([(x["code"], x["name"], x.get("dns")) for x in skipped],
                         [("self_ep", "Vb", True), ("self_ep", "Hb", True), ("self_ep", "Vlo", True)])
        self.assertEqual(len(good), 3)                     # another server, and a name not resolved now — taken
        self.assertEqual(sorted(set(self.asked)), ["b.example.com", "far.example.com", "lo.example.com", "nx.example.com"])

    def test_the_check_says_why(self):
        """Окно «Проверить ключ» (exits.preview): причина extract_report — «указывает на этот сервер»."""
        rows = exits.preview(self.p, "\n".join([vless("b.example.com", "Vb"), hy2("far.example.com", "Hfar")]))
        self.assertIn("этот сервер", rows[1]["problem"] if rows[0]["name"] == "Hfar" else rows[0]["problem"])
        self.assertEqual([r for r in rows if r["name"] == "Hfar" and r.get("problem")], [])

    def test_the_lists_never_ask(self):
        """Списки (config, link_problem) в DNS не ходят — как у AmneziaWG."""
        self.assertEqual(self.p.link_problem(vless("b.example.com", "Vb")), "")
        self.assertEqual(self.asked, [])

    def test_a_subscription_keeps_the_key_the_pool_has(self):
        held, skipped = [], []
        lines = exitsubs.keys_of("\n".join([vless("b.example.com", "Vb"), vless("far.example.com", "Vfar")]), self.p,
                                 skipped, held)
        self.assertEqual(len(lines), 1)
        self.assertEqual(held, [vless("b.example.com", "Vb")])
        self.assertEqual(skipped, [])
        # the pool has it not — one more skipped, said as «points to this server», not as a waited TLS server
        self.assertEqual(exitsubs._held_codes([], held), ["self_ep"])
        self.assertEqual(exitsubs._held_codes([vless("b.example.com", "Vb")], held), [])


class InListMenuTest(unittest.TestCase):
    """№2: SSH-меню 17 → 4 → 5 → «Несколько адресов» — неверный список (``abc``, ``1,2``) давал «Нет такого пункта:
    введите номер из списка» (общий отказ выбора пункта), хотя вводили адреса. Теперь список уходит в ядро, и отказ —
    его словами, как у ``kaskadctl gw addresses in`` («abc — не внешний IPv4-адрес»); только то, что ядро прочло бы как
    флаг, меню отвергает само — своим текстом, RU и EN."""

    def run_menu(self, keys):
        from tests.test_mip_s34 import MenuTest
        m = MenuTest("test_taking_connections_by_digits")
        m.STUBS = MenuTest.STUBS.replace('"addresses in")', '"addresses in")\n            echo "ARG:$3"\n'
                                         '            [[ "$3" =~ ^[0-9]{1,3}(\\.[0-9]{1,3}){3}(,[0-9]{1,3}(\\.[0-9]{1,3}){3})*$ ]] || '
                                         '{ K3_GW_RC=2; K3_GW_ERR="CORE:$3"; K3_GW_OUT="{}"; return 2; }')
        m.addCleanup = self.addCleanup
        return m.run_menu("k3_gw_in_menu", keys)

    @unittest.skipUnless(shutil.which("jq"), "нет jq")
    def test_a_wrong_list_is_told_by_the_core(self):
        out = self.run_menu(["4", "abc", "4", "1,2", "4", "-x", "0"])
        self.assertNotIn("BAD\n", out.replace("GWS_IN_BAD", ""))           # never «no such item» for a list
        self.assertIn("[ERROR] CORE:abc", out)
        self.assertIn("[ERROR] CORE:1,2", out)
        self.assertIn("[ERROR] GWS_IN_BAD:-x", out)
        self.assertNotIn("ARG:-x", out)                                     # a flag never goes to the core

    def test_the_words_ru_en(self):
        import re
        for lang, want in (("ru", "не внешний IPv4-адрес"), ("en", "isn’t an external IPv4 address")):
            m = re.search(r"(?m)^M_%s_GWS_IN_BAD='(%%s [^']+)'$" % lang, text("lib/kaskad/i18n/sh/gwstat.%s.sh" % lang))
            self.assertTrue(m and want in m.group(1), lang)


class HeadFindTest(unittest.TestCase):
    """№1, Н-5 стенда 3.3.7.13: «Пользователи» на 1100–1199 — поле поиска в шапке сжато до 40–62 px, а «⌘K»
    (``flex: none``) вылезал из него на «Онлайн». Теперь от 1100 поле — контейнер своей ширины, и пока в нём нет места
    для слова и «⌘K», оно значком, как ниже 1100 (вид прежний). Детектор вёрстки ловит такое правилом headbox —
    в Chrome проверено: на bc541e29 «kbd.kk-hfind__k 637…667 вне 593…633, на span.gx-chip» на 1100."""

    def test_the_narrow_field_is_an_icon(self):
        import re
        css = re.sub(r"/\*.*?\*/", "", text("lib/kaskad/panel/static/find.css"), flags=re.S)
        m = re.search(r"@media \(min-width: 1100px\) \{\s*\.gx-head \.kk-hfind \{ container: kk-hfind / inline-size; \}", css)
        self.assertTrue(m, "the field is a container from 1100")
        q = re.search(r"@container kk-hfind \(max-width: ([0-9.]+)px\) \{(.*?)\n\}", css, re.S)
        self.assertTrue(q)
        self.assertGreaterEqual(float(q.group(1)), 62)                  # «⌘K» 30 + значок 16 + два зазора 8
        self.assertRegex(q.group(2), r"\.kk-hfind__t, \.kk-hfind \.kk-hfind__k \{ display: none; \}")

    def test_the_detector_has_headbox(self):
        src = text("tools/k3_layout_check.mjs")
        self.assertIn("out.push(['headbox'", src)
        self.assertIn("//   headbox", src)


class JsonConstantsTest(unittest.TestCase):
    """REV-3313-final (было до): NaN и Infinity проходили в API панели (обычный json.loads) — ``limit_gb: Infinity``
    давал OverflowError в глубине политики. Теперь тело с ними — 400 «не JSON», как любое другое не-JSON."""

    def test_nan_and_infinity_are_no_json(self):
        from kaskad.panel.routes import HttpError, Request
        for body in (b'{"limit_gb": Infinity}', b'{"x": NaN}', b'{"x": [-Infinity]}'):
            with self.assertRaises(HttpError) as c:
                Request("PUT", "/api/gw/policy", body=body).json()
            self.assertEqual(c.exception.status, 400, body)
        self.assertEqual(Request("PUT", "/x", body=b'{"limit_gb": 1e3}').json(), {"limit_gb": 1000.0})

    def test_numbers_past_the_bound_are_no_json(self):
        """REV-33714 С-1: ``1e400`` Python reads as inf (parse_float, not parse_constant), ``1e308`` overflows in
        × 10, an integer of 400 digits — in float(). The same 400 «не JSON», the numbers of a real body pass."""
        from kaskad.panel.routes import HttpError, Request
        big = b"1" + b"0" * 400
        for body in (b'{"quota": {"limit_gb": 1e400}}', b'{"x": -1e999}', b'{"x": [1e308]}',
                     b'{"quota": {"limit_gb": ' + big + b'}}', b'{"x": -' + big + b'}', b'{"x": 1' + b"0" * 19 + b'}',
                     b'{"x": 1' + b"0" * 5000 + b'}'):
            with self.assertRaises(HttpError) as c:
                Request("PATCH", "/api/gw/exits/e-0123456789", body=body).json()
            self.assertEqual(c.exception.status, 400, body[:40])
        ok = b'{"a": 1e18, "b": -1000000000000000000, "c": 0.1, "d": 9007199254740991, "e": 1.5e-300}'
        self.assertEqual(Request("PUT", "/x", body=ok).json(),
                         {"a": 1e18, "b": -10 ** 18, "c": 0.1, "d": 9007199254740991, "e": 1.5e-300})


class HugeNumbersTest(unittest.TestCase):
    """REV-33714 С-1 as a class: every way in of a number (the API of the panel and the setup, MCP, stdin of
    ``setup step``), every check of the policy, every reader of a saved quota and policy (state.json, the read cache,
    a copy .kbk). Without the fix: OverflowError in validate_exit / validate / quota_due / the health step / the
    probes, ``Infinity`` written to the policy."""

    BAD_LIMITS = (float("inf"), float("-inf"), float("nan"), 1e308, 10 ** 400, -10 ** 400, 1e-320, 1e-7, 0.05,
                  2000000, 1000000.1, "50", True, None)

    def test_validate_exit_refuses_a_bad_limit_with_its_code(self):
        from kaskad.gw import policy as P
        for lim in self.BAD_LIMITS:
            errs = P.validate_exit({"quota": {"limit_gb": lim, "action": "push"}}, "ru")
            self.assertEqual([e["code"] for e in errs], ["ep.err.quota_limit"], repr(lim)[:30])
        self.assertIn("1000000", errs[0]["text"])
        for lim in (0.1, 99.9, 100, 1000000):
            self.assertEqual(P.validate_exit({"quota": {"limit_gb": lim, "action": "push"}}), [], lim)

    def test_validate_refuses_a_huge_number_of_a_rule_or_a_tier(self):
        from kaskad.gw import policy as P
        for v in (float("inf"), 1e308, 10 ** 400):
            doc = P.default_doc("standard")
            doc["groups"][0]["rules"][0]["when"]["conds"][0]["value"] = v
            errs, _w = P.validate(doc)
            self.assertIn("ep.err.cond_value", [e["code"] for e in errs], repr(v)[:30])
            doc = P.default_doc("standard")
            doc["groups"][0]["tiers"][0]["tolerance_ms"] = v
            doc["groups"][0]["checks"]["freq"] = {"mode": "custom", "every_s": v}
            doc["probes"] = {"speed": {"budget_day_mb": v}}
            codes = [e["code"] for e in P.validate(doc)[0]]
            for code in ("ep.err.tolerance", "ep.err.freq_every", "ep.err.speed_budget"):
                self.assertIn(code, codes, repr(v)[:30])
        self.assertEqual(P.freq_p({"checks": {"freq": {"mode": "custom", "every_s": float("inf")}}}), P.FREQ_P["often"])

    def test_a_saved_huge_number_is_read_finite(self):
        from kaskad import backup, snapshot, state, util
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        p = os.path.join(d, "state.json")
        doc = state.empty()
        doc["gateway"] = {"exit_pref": {"e-0123456789": {"quota": {"limit_gb": 333}}}, "x": [111.5, 222]}
        raw = util.json_text(doc).replace("333", "1" + "0" * 400).replace("111.5", "Infinity") \
            .replace("222", "-1e999")
        self.assertEqual(raw.count("Infinity") + raw.count("-1e999") + raw.count("0" * 400), 3)
        with open(p, "w") as f:
            f.write(raw)
        for st in (state.load(p), snapshot.read_json(p), backup._json(raw)):
            g = st["gateway"]
            self.assertEqual(g["exit_pref"]["e-0123456789"]["quota"]["limit_gb"], util.NUM_MAX)
            self.assertEqual(g["x"], [1e18, -1e18])
            self.assertIn('"limit_gb": 1000000000000000000', util.json_text(st))
        self.assertTrue(util.json_kept('{"x": NaN}')["x"] != util.json_kept('{"x": NaN}')["x"])

    def test_the_count_of_a_quota_lives_with_a_saved_huge_limit(self):
        from kaskad import tz
        from kaskad.gw import health, pins
        from kaskad.gw import policy as P
        from kaskad.gw.probe import run
        z = tz.Zone("Fixed/Moscow", types=[(3 * 3600, False)])
        t = 1900000000.0
        for lim in (10 ** 400, float("inf"), 1e-320, float("nan"), "5"):
            q = {"period": "month", "limit_gb": lim, "reset_day": 1, "warn_pct": 80, "action": "remove"}
            self.assertIsNone(P.quota_gb(q), repr(lim)[:30])
            d = pins.traffic_empty()
            pins.traffic_add(d, "e-a", 0, 0, t, q, z)
            pins.traffic_add(d, "e-a", 5 * 10 ** 9, 0, t + 60, q, z)
            self.assertEqual(pins.quota_due(d, "e-a", q, t + 60, z), [], repr(lim)[:30])
            self.assertFalse(run.quota_removed(None, {"quota": q}, d, "e-a", t + 60))
            h, reasons, evs = {}, {"quota"}, []

            class Ctx(object):
                def ev(self, *a, **kw):
                    evs.append((a, kw))
            o = {"quota": q, "traffic": {"used_gb": 5.0, "period": "2030-03-01"}}
            self.assertFalse(health._quota(h, reasons, o, "e-a", "main", Ctx()))
            self.assertNotIn("quota", reasons)
            self.assertEqual(evs, [])
        q = dict(q, limit_gb=4)                         # a real limit still counts
        self.assertEqual(P.quota_gb(q), 4.0)
        self.assertEqual(pins.quota_due(d, "e-a", q, t + 60, z), [80, 100])
        self.assertTrue(run.quota_removed(None, {"quota": q}, d, "e-a", t + 60))

    def test_setup_stdin_refuses_them(self):
        import io
        import types
        from kaskad.errors import KaskadError
        from kaskad.setup import cli as scli
        for body in ('{"x": 1e400}', '{"x": 1' + "0" * 400 + "}", '{"x": NaN}'):
            with mock.patch("sys.stdin", io.StringIO(body)), self.assertRaises(KaskadError):
                scli._body(types.SimpleNamespace(job=None, stdin=True))
        with mock.patch("sys.stdin", io.StringIO('{"x": 5}')):
            self.assertEqual(scli._body(types.SimpleNamespace(job=None, stdin=True)), {"x": 5})


def _mcp_case():
    from tests.test_mcp import McpCase
    return McpCase


class McpHugeNumbersTest(_mcp_case()):
    """REV-33714 С-1: MCP read ``1e400`` as inf and an integer of 400 digits as an int — now a parse error, as NaN."""

    def test_parse_error_and_the_server_lives(self):
        data = (b'{"jsonrpc":"2.0","id":2,"method":"ping","params":{"x":1e400}}\n'
                b'{"jsonrpc":"2.0","id":3,"method":"ping","params":{"x":1' + b"0" * 400 + b'}}\n' +
                b'{"jsonrpc":"2.0","id":4,"method":"ping"}\n')
        proc = subprocess.run([__import__("sys").executable, "-m", "kaskad.mcp"], input=data, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, env=self.env(), timeout=30)
        lines = [json.loads(ln) for ln in proc.stdout.decode().splitlines()]
        self.assertEqual([x.get("id") for x in lines], [None, None, 4])
        self.assertEqual([x["error"]["code"] for x in lines[:2]], [-32700, -32700])


class CopyHostTest(unittest.TestCase):
    """REV-mip-s12 (было до): ``gateway.host`` из копии не проходил net.parse_host — в настройки и на экран шло что
    угодно; SSH-меню печатало адрес через echo -e без k3_term_esc."""

    def test_the_host_of_a_copy_is_checked(self):
        from kaskad import backup
        self.assertEqual(backup._copy_host("93.184.216.34"), "93.184.216.34")
        self.assertEqual(backup._copy_host("VPN.Example.org."), "vpn.example.org")
        for bad in ("\x1b]0;x\x07", "10.0.0.1", "a b", "1.2.3", "", None):
            self.assertEqual(backup._copy_host(bad), "", repr(bad))

    def test_the_restore_takes_only_a_checked_host(self):
        from kaskad import backup
        src = text("lib/kaskad/backup.py")
        body = src[src.index("    was_on = bool(g_file.get(\"enabled\"))"):]
        self.assertIn('host, auto = _copy_host(g_file.get("host")), g_file.get("host_auto", True)', body)
        self.assertIn("old = _copy_host(g.get(\"host\"))", src)
        self.assertTrue(callable(backup._copy_host))

    def test_the_menu_escapes_the_host(self):
        body = function("k3_gw_host_menu")
        now = [ln for ln in body.splitlines() if "GWS_HOST_NOW" in ln]
        self.assertEqual(len(now), 1)
        self.assertIn("${ce:-", now[0])
        self.assertLess(body.index("ce=$(printf '%s' \"$cur\" | k3_term_esc)"), body.index("GWS_HOST_NOW"))


def _backup_case():
    from tests.test_backup import BackupCase
    return BackupCase


class CopyHostImportTest(_backup_case()):
    """REV-mip-s12: копия с испорченным ``gateway.host`` (управляющие байты) — после импорта адрес для клиентов не
    этот текст, а найденный заново (host_auto), в словах предпросмотра его тоже нет."""

    def test_a_bad_host_of_a_copy_never_comes(self):
        from kaskad import backup, state
        from tests.test_backup import PW
        self.old_node()
        st = state.load()
        st["gateway"]["host"], st["gateway"]["host_auto"] = "\x1b]0;x\x07evil", False
        state.save(st)
        doc = backup.unseal(self.w.runner, backup.export(self.w.runner, PW)["text"], PW)
        self.new_node()
        pv = backup.preview(self.w.runner, doc)
        self.assertNotIn("evil", json.dumps(pv, ensure_ascii=False))
        with mock.patch("kaskad.gw.net.detect_host", lambda runner: ("151.101.1.69", "route")):
            backup.apply(self.w.runner, doc)
        g = state.load()["gateway"]
        self.assertNotIn("evil", str(g.get("host")))
        self.assertTrue(g.get("host_auto"))


if __name__ == "__main__":
    unittest.main()
