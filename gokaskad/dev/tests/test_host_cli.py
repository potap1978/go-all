"""A8 (SPEC-node-score §3.8, §4 A8): ``kaskadctl host health | set | probes-hint``, the advice of the summary for the
alarms ``host_*`` (item 22), the MCP tools ``host_health`` / ``host_set``. The SSH menu (item 22) — section 55 of
tools/k3_install_test.sh."""

import contextlib
import gc
import io
import json
import os
import warnings
from unittest import mock

from kaskad import cli, errors, host_cli, i18n, state, summary
from kaskad.gw import hostmon, hostping, hostprobe, model
from tests.gw.test_collect_host import HostWorld
from tests.helpers import RootCase, lock_held_elsewhere
from tests.test_mcp import ACTIONS, McpCase, TOOLS, call

V6_DEFAULT = ("00000000000000000000000000000000 00 00000000000000000000000000000000 00 "
              "fe800000000000000000000000000001 00000400 00000001 00000000 00000003 eth0\n")
V6_LOCAL_ONLY = ("fe800000000000000000000000000000 40 00000000000000000000000000000000 00 "
                 "00000000000000000000000000000000 00000100 00000001 00000000 00000001 eth0\n")


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = cli.main(list(argv))
        except SystemExit as e:
            rc = e.code
    return rc, out.getvalue(), err.getvalue()


def run_json(*argv):
    rc, out, err = run_cli(*(argv + ("--json",)))
    return rc, json.loads(out), err


class HealthTest(HostWorld):
    """``host health``: the source as ``hostprobe.read_health`` (live.json → the bot's file → its own snapshot)."""

    def setUp(self):
        super().setUp()
        p = mock.patch.object(host_cli, "time", mock.Mock(time=lambda: self.now))      # the clock of the world
        p.start()
        self.addCleanup(p.stop)

    def test_with_live_json_the_contract(self):
        for _ in range(3):
            self.round()
        rc, doc, _ = run_json("host", "health")
        self.assertEqual(rc, 0)
        self.assertEqual(set(doc), {"ok", "health", "live", "instant", "by", "hostmon"})
        self.assertEqual(set(doc["live"]), {"age", "fresh"})
        self.assertEqual((doc["ok"], doc["by"], doc["instant"], doc["live"]["fresh"]), (True, "collector", False, True))
        self.assertEqual(doc["health"]["checks"], self.health()["checks"])
        self.assertEqual(doc["hostmon"], model.hostmon(state.load()))
        rc, out, _ = run_cli("host", "health")
        self.assertEqual(rc, 0)
        lines = out.splitlines()
        self.assertTrue(lines[0].startswith("Состояние: "), out)
        self.assertIn("Проверки:", lines)
        self.assertTrue(any(l.startswith("  ✓ В норме: ") and "Место на диске" in l for l in lines), out)
        self.assertIn("Лёгкие пробы сети: выключены. Включить — kaskadctl host set --probes on", lines)

    def test_without_live_its_own_snapshot(self):
        with state.locked():
            st = state.load()
            st["gateway"]["enabled"] = False
            state.save(st)
        rc, doc, _ = run_json("host", "health")
        self.assertEqual((rc, doc["by"], doc["instant"], doc["live"]["fresh"]), (0, "panel", True, False))
        self.assertIn("disk", doc["health"]["checks"])
        self.assertNotIn("cpu", doc["health"]["checks"])
        _rc, out, _ = run_cli("host", "health")
        self.assertIn("  " + i18n.tr("gw_host.light.instant"), out.splitlines())

    def test_bot_on_bash_without_the_gateway_says_python_bot(self):
        self.write("/etc/kaskad/config", 'BOT_IMPL="bash"\n')
        with state.locked():
            st = state.load()
            st["gateway"]["enabled"] = False
            state.save(st)
        _rc, doc, _ = run_json("host", "health")
        self.assertEqual(doc["health"]["hint"], "bot_python")
        _rc, out, _ = run_cli("host", "health")
        self.assertIn("бот на Python: пункт 8 SSH-меню", out)

    def test_an_alarm_with_its_advice(self):
        self.disk = 99.0
        for _ in range(3):
            self.round()
        _rc, out, _ = run_cli("host", "health")
        lines = out.splitlines()
        i = next(i for i, l in enumerate(lines) if l.startswith("✗ "))
        self.assertIn("/", lines[i])
        self.assertEqual(lines[i + 1], "  " + i18n.tr("gw_host.advice.disk"))
        self.assertEqual(lines[0], "Состояние: " + i18n.tr("gw_host.light.crit"))

    def test_english_reader(self):
        for _ in range(3):
            self.round()
        with i18n.use_lang("en"):
            _rc, out, _ = run_cli("host", "health")
            _rc, doc, _ = run_json("host", "health")
        self.assertTrue(out.startswith("Status: "), out)
        self.assertIn("Checks:", out)
        self.assertNotRegex(out, "[а-яА-ЯёЁ]")
        self.assertNotRegex(json.dumps([c.get("why_text") for c in doc["health"]["checks"].values()],
                                       ensure_ascii=False), "[а-яА-ЯёЁ]")

    def test_probes_on_shows_the_points(self):
        self.hm(probes=True)
        for _ in range(3):
            self.round()
        _rc, out, _ = run_cli("host", "health")
        self.assertIn("Лёгкие пробы сети: включены, точки: " + ", ".join(hostping.DEFAULT_ANCHORS), out)

    def test_the_check_off_says_how_to_turn_it_on(self):
        """REV-ns-a8 З-2: ``enabled: false`` — the light «off» and how to turn the check on (the screen: its number)."""
        self.hm(enabled=False, probes=False)
        for _ in range(2):
            self.round()
        _rc, out, _ = run_cli("host", "health")
        lines = out.splitlines()
        self.assertEqual(lines[:2], ["Состояние: " + i18n.tr("gw_host.light.off"), "  Включить — kaskadctl host set --on"])
        _rc, out, _ = run_cli("host", "health", "--screen")
        self.assertEqual(out.splitlines()[1], "  Включить — 2 на этом экране")
        self.assertNotIn("kaskadctl", out)
        with i18n.use_lang("en"):
            _rc, out, _ = run_cli("host", "health", "--screen")
        self.assertEqual(out.splitlines()[1], "  To turn it on — 2 on this screen")

    def test_the_screen_points_to_its_number_not_to_a_command(self):
        """П-2: on the screen of item 22 «turn on the probes» is «2 on this screen»."""
        for _ in range(2):
            self.round()
        _rc, out, _ = run_cli("host", "health", "--screen")
        self.assertIn("Лёгкие пробы сети: выключены. Включить — 2 на этом экране", out.splitlines())
        self.assertNotIn("kaskadctl", out)

    def test_probes_on_without_the_collector_say_why_they_wait(self):
        """The line «Пробы идут, только когда работает шлюз» (the gateway off), «… движок nftables» (the old engine)."""
        self.hm(probes=True)
        with state.locked():
            st = state.load()
            st["gateway"]["enabled"] = False
            state.save(st)
        _rc, out, _ = run_cli("host", "health")
        self.assertIn("  Пробы идут, только когда работает шлюз (пункт 17 SSH-меню)", out.splitlines())
        with state.locked():
            st = state.load()
            st["engine"] = "legacy"
            st["gateway"]["enabled"] = True
            state.save(st)
        _rc, out, _ = run_cli("host", "health")
        self.assertIn("  Пробы идут, только когда работает шлюз, а ему нужен движок nftables (пункты 16 и 17 SSH-меню)",
                      out.splitlines())

    def test_waiting_for_history_is_one_line(self):
        """П-4: «ждёт истории» with its minutes is one line, not one per «N мин из M»."""
        self.round()
        _rc, doc, _ = run_json("host", "health")
        waits = set(c["why_text"] for c in doc["health"]["checks"].values()
                    if c.get("level") == "wait" and isinstance(c.get("why_msg"), dict)
                    and c["why_msg"].get("key") == "gw_host.why.wait")
        self.assertGreater(len(waits), 1, waits)                   # several «N мин из M» in the world
        _rc, out, _ = run_cli("host", "health")
        lines = [l for l in out.splitlines() if l.startswith("  … ")]
        self.assertEqual(len([l for l in lines if l.startswith("  … Ждёт истории: ")]), 1, out)
        self.assertNotIn("мин из", out)

    def test_a_read_error_is_code_1(self):
        with mock.patch.object(hostprobe, "read_health", side_effect=RuntimeError("boom")):
            rc, out, err = run_cli("host", "health")
        self.assertEqual((rc, out), (errors.ERROR, ""))
        self.assertIn("Не удалось прочитать здоровье сервера: boom", err)


class SetTest(RootCase):
    def setUp(self):
        super().setUp()
        st = state.empty()
        st["engine"] = "nft"
        st["gateway"] = {"enabled": True, "host": "1.2.3.4"}
        state.save(st)

    def hm(self):
        return state.load()["gateway"].get("hostmon")

    def test_bad_anchors_are_refused_with_words_and_nothing_is_written(self):
        six = ",".join(["1.1.1.1", "1.0.0.1", "8.8.8.8", "8.8.4.4", "9.9.9.9", "149.112.112.112"])
        for bad in ("10.0.0.1", "example.com", six, "127.0.0.1", "1.2.3.4", "100.64.0.1", "224.0.0.1", "ff02::1",
                    "::ffff:10.0.0.1", "64:ff9b::7f00:1", "fe80::1%eth0", "", "1.1.1.1,192.168.1.1"):
            before = self.read("/etc/kaskad/state.json")
            rc, out, err = run_cli("host", "set", "--probes", "on", "--anchors", bad)
            self.assertEqual((rc, out), (errors.USAGE, ""), bad)
            self.assertTrue(err.startswith("Ошибка: "), (bad, err))
            self.assertEqual(self.read("/etc/kaskad/state.json"), before, bad)
            if bad == six:
                self.assertIn("не больше 5 (указано 6)", err)
            elif bad == "1.2.3.4":
                self.assertIn("«1.2.3.4» — адрес этого сервера", err)
            elif bad:
                self.assertIn("не годится как точка проверки", err, bad)
        with i18n.use_lang("en"):
            _rc, _out, err = run_cli("host", "set", "--anchors", "10.0.0.1")
        self.assertIn("“10.0.0.1” can’t be a check point", err)

    def test_good_anchors_are_written(self):
        rc, doc, _ = run_json("host", "set", "--anchors", " 1.1.1.1, 2606:4700:4700::1111 ,1.1.1.1,::ffff:8.8.8.8")
        self.assertEqual(rc, 0)
        self.assertEqual(self.hm()["anchors"], ["1.1.1.1", "2606:4700:4700::1111", "8.8.8.8"])
        self.assertEqual(doc["hostmon"], model.hostmon(state.load()))
        self.assertEqual(doc["changed"], ["anchors"])
        _rc, out, _ = run_cli("host", "set", "--anchors", "default")
        self.assertNotIn("anchors", self.hm())
        self.assertIn("Точки проверки — по умолчанию: " + ", ".join(hostping.DEFAULT_ANCHORS) + ".", out)

    def test_the_answer_says_within_10_s(self):
        """REV-ns-a8 З-1: the honest promise — the collector within 10 s, the first measurements under a minute."""
        rc, out, _ = run_cli("host", "set", "--probes", "on")
        self.assertEqual(rc, 0)
        self.assertIs(self.hm()["probes"], True)
        self.assertIn("сборщик возьмёт их в течение 10 с, первые замеры — меньше чем через минуту", out)
        self.assertNotIn("начнутся в течение 10 с", out)
        _rc, out, _ = run_cli("host", "set", "--probes", "off")
        self.assertIs(self.hm()["probes"], False)
        self.assertIn("остановятся в течение 10 с", out)
        _rc, out, _ = run_cli("host", "set", "--off")
        self.assertIs(self.hm()["enabled"], False)
        self.assertIn("в течение 10 с", out)
        with i18n.use_lang("en"):
            _rc, out, _ = run_cli("host", "set", "--probes", "on", "--on")
        self.assertIn("within 10 s", out)
        self.assertIn("the first measurements come in under a minute", out)

    def test_the_check_off_is_the_reason_not_the_gateway(self):
        """REV-ns-a8 З-2: the gateway works, ``enabled: false`` — the probes wait for the check, not for the gateway."""
        run_cli("host", "set", "--off")
        rc, out, _ = run_cli("host", "set", "--probes", "on")
        self.assertEqual(rc, 0)
        self.assertIn("проверка здоровья сервера выключена — пробы начнутся, когда её включат: kaskadctl host set --on",
                      out)
        self.assertNotIn("шлюз", out)
        self.assertNotIn("в течение 10 с", out)
        _rc, out, _ = run_cli("host", "set", "--on")
        self.assertIn("Проверка здоровья сервера включена.", out)

    def test_the_old_engine_says_nftables(self):
        """П-3: on the old engine the gateway needs nftables first (item 16), not only item 17."""
        with state.locked():
            st = state.load()
            st["engine"] = "legacy"
            state.save(st)
        _rc, out, _ = run_cli("host", "set", "--probes", "on")
        self.assertIn("начнутся, когда сервер перейдёт на nftables (пункт 16 SSH-меню) и заработает шлюз (пункт 17)", out)

    def test_a_bad_value_is_said_back_without_control_signs_and_short(self):
        """REV-ns-a8-sec П-1: ESC/OSC/BEL and bidi signs of a bad value never reach the terminal; ≤ 64 signs of it."""
        evil = "\x1b[31mX\x1b]0;pwn\x07\u202e"
        for argv in (("--anchors", evil), ("--probes", evil), ("--push-level", evil), ("--anchors", "9" * 100000)):
            rc, out, err = run_cli("host", "set", *argv)
            self.assertEqual((rc, out), (errors.USAGE, ""), argv[0])
            for c in ("\x1b", "\x07", "\u202e"):
                self.assertNotIn(c, err, argv[0])
            self.assertLess(len(err), 400, argv[0])
        _rc, _out, err = run_cli("host", "set", "--anchors", "9" * 100000)
        self.assertIn("«" + "9" * 63 + "…»", err)
        self.assertEqual(host_cli._shown("\x1b[31mX"), "?[31mX")

    def test_an_address_of_an_interface_is_no_anchor(self):
        """REV-ns-a8-sec З-1: the addresses of the interfaces (``hostprobe.own_addrs`` of A5 круг 2) — the list of the
        collector's round — are refused at the write too; unreadable /proc — the state alone."""
        self.assertTrue(callable(getattr(hostprobe, "own_addrs", None)))      # after the merge with A5: no stub
        with mock.patch.object(hostprobe, "own_addrs", new=lambda mono=None: ("5.6.7.8", "2a01:4f8::5")):
            for a in ("5.6.7.8", "2a01:4f8::5"):
                rc, _out, err = run_cli("host", "set", "--anchors", "1.1.1.1," + a)
                self.assertEqual(rc, errors.USAGE, a)
                self.assertIn("«%s» — адрес этого сервера" % a, err)
            self.assertNotIn("hostmon", state.load()["gateway"])
        with mock.patch.object(hostprobe, "own_addrs", new=mock.Mock(side_effect=OSError("no /proc"))):
            rc, _out, _err = run_cli("host", "set", "--anchors", "5.6.7.8")
        self.assertEqual(rc, 0)

    def test_an_address_of_an_interface_read_from_proc_is_no_anchor(self):
        """REV-ns-a8-sec З-1 after the merge with A5: the real ``hostprobe.own_addrs`` (no stub) — the addresses of
        /proc/net/fib_trie and /proc/net/if_inet6 are refused by ``host set``."""
        self.write("/proc/net/fib_trie", "Local:\n  +-- 0.0.0.0/0 3 0 5\n     |-- 5.6.7.8\n        /32 host LOCAL\n")
        self.write("/proc/net/if_inet6", "2a0104f8000000000000000000000005 02 40 00 80 eth0\n")
        hostprobe.reset()
        self.addCleanup(hostprobe.reset)
        for a in ("5.6.7.8", "2a01:4f8::5"):
            rc, _out, err = run_cli("host", "set", "--anchors", "1.1.1.1," + a)
            self.assertEqual(rc, errors.USAGE, a)
            self.assertIn("«%s» — адрес этого сервера" % a, err)
        self.assertNotIn("hostmon", state.load()["gateway"])
        rc, _out, _err = run_cli("host", "set", "--anchors", "1.1.1.1")
        self.assertEqual(rc, 0)

    def test_an_address_of_the_name_of_the_node_is_no_anchor(self):
        """REV-ns-a8-sec З-1: ``gateway.host`` a name — its addresses (the node's resolver, ≤ 2 s) are no anchors."""
        with state.locked():
            st = state.load()
            st["gateway"]["host"] = "vpn.my.example.com"
            state.save(st)
        def gai(name, *a, **kw):
            self.assertEqual(name, "vpn.my.example.com")
            return [(2, 1, 6, "", ("5.6.7.8", 0)), (10, 1, 6, "", ("2a01:4f8::5%eth0", 0, 0, 0))]
        with mock.patch("socket.getaddrinfo", gai):
            for a in ("5.6.7.8", "2a01:4f8::5"):
                rc, _out, err = run_cli("host", "set", "--anchors", a)
                self.assertEqual(rc, errors.USAGE, a)
                self.assertIn("«%s» — адрес имени vpn.my.example.com" % a, err)
            rc, _out, _err = run_cli("host", "set", "--anchors", "default")
            self.assertEqual(rc, 0)
        import threading, time as _t
        gate = threading.Event()
        self.addCleanup(gate.set)
        with mock.patch("socket.getaddrinfo", lambda *a, **kw: gate.wait(5) and []), \
                mock.patch.object(host_cli, "RESOLVE_S", 0.2):
            t0 = _t.monotonic()
            rc, _out, _err = run_cli("host", "set", "--anchors", "5.6.7.8")
            self.assertLess(_t.monotonic() - t0, 3.0)                  # a hanging resolver does not hang host set
        self.assertEqual(rc, 0)
        self.assertEqual(self.hm()["anchors"], ["5.6.7.8"])

    def test_without_the_gateway_the_probes_wait_for_it(self):
        with state.locked():
            st = state.load()
            st["gateway"]["enabled"] = False
            state.save(st)
        _rc, out, _ = run_cli("host", "set", "--probes", "on")
        self.assertIn("начнутся, когда заработает шлюз (пункт 17 SSH-меню)", out)
        self.assertNotIn("в течение 10 с", out)

    def test_ipv6_point_without_an_ipv6_route_is_written_with_a_warning(self):
        self.write("/proc/net/ipv6_route", V6_LOCAL_ONLY)
        rc, doc, _ = run_json("host", "set", "--anchors", "1.1.1.1,2606:4700:4700::1111")
        self.assertEqual(rc, 0)
        self.assertEqual(self.hm()["anchors"], ["1.1.1.1", "2606:4700:4700::1111"])
        self.assertEqual(doc["warnings"], ["У сервера нет IPv6: точка 2606:4700:4700::1111 будет «не проверяется»"])
        self.write("/proc/net/ipv6_route", V6_LOCAL_ONLY + V6_DEFAULT)
        _rc, doc, _ = run_json("host", "set", "--anchors", "2606:4700:4700::1111")
        self.assertEqual(doc["warnings"], [])

    def test_push_level(self):
        rc, doc, _ = run_json("host", "set", "--push-level", "warn")
        self.assertEqual((rc, doc["push_level"]), (0, "warn"))
        self.assertEqual(model.notify(state.load())["host_level"], "warn")
        self.assertEqual(model.notify(state.load())["push"], model.NOTIFY_PUSH)       # the switches untouched
        rc, _out, err = run_cli("host", "set", "--push-level", "info")
        self.assertEqual(rc, errors.USAGE)
        self.assertIn("crit или warn", err)

    def test_usage_errors(self):
        for argv, needle in ((("--on", "--off"), "--on и --off вместе нельзя"), ((), "Укажите, что изменить"),
                             (("--probes", "yes"), "--probes — on или off")):
            rc, out, err = run_cli("host", "set", *argv)
            self.assertEqual((rc, out), (errors.USAGE, ""), argv)
            self.assertIn(needle, err)
        rc, doc, _ = run_json("host", "set", "--on", "--off")
        self.assertEqual((rc, doc["ok"], doc["code"]), (errors.USAGE, False, errors.USAGE))
        # T-3377 Н-4: the hint names every key of «host set» (the threshold ▲/▼ and the repeat of a check among them)
        import re
        with open(host_cli.__file__, encoding="utf-8") as f:
            keys = [k for k in re.findall(r'st\.add_argument\("(--[a-z-]+)"', f.read())
                    if k not in ("--code", "--yes", "--by-mcp")]            # how to confirm, not what to change
        self.assertIn("--dev-threshold", keys)
        for lang in ("ru", "en"):
            with i18n.use_lang(lang):
                hint = i18n.tr("host_cli.set.nothing")
            self.assertEqual([k for k in keys if k not in hint], [], lang)

    def test_same_value_writes_nothing(self):
        run_cli("host", "set", "--probes", "off")
        before = self.read("/etc/kaskad/state.json")
        rc, doc, _ = run_json("host", "set", "--probes", "off")
        self.assertEqual((rc, doc["changed"]), (0, []))
        self.assertEqual(self.read("/etc/kaskad/state.json"), before)

    def test_explicit_off_on_a_node_without_the_key_is_a_change(self):
        """«Не напоминать» (item 22 → 6): probes false written explicitly — the hint stops."""
        rc, doc, _ = run_json("host", "set", "--probes", "off")
        self.assertEqual((rc, doc["changed"]), (0, ["probes"]))
        self.assertIs(self.hm()["probes"], False)

    def test_locked(self):
        with lock_held_elsewhere():
            with mock.patch.object(state, "locked", lambda timeout=15.0, _l=state.locked: _l(0.2)):
                rc, _out, err = run_cli("host", "set", "--probes", "on")
        self.assertEqual(rc, errors.LOCKED)
        self.assertNotIn("hostmon", state.load()["gateway"])


class ProbesHintTest(RootCase):
    def setUp(self):
        super().setUp()
        with warnings.catch_warnings():       # sqlite connections left by the worlds of other tests: not on our stderr
            warnings.simplefilter("ignore", ResourceWarning)
            gc.collect()

    def world(self, gateway=True, engine="nft", hostmon_=None, gen=None):
        st = state.empty()
        st["engine"] = engine
        st["gateway"] = {"enabled": gateway}
        if hostmon_ is not None:
            st["gateway"]["hostmon"] = hostmon_
        if gen is not None:
            st["gen"] = gen
        state.save(st)

    def hint(self):
        rc, out, err = run_cli("host", "probes-hint")
        self.assertEqual((rc, err), (0, ""))
        return out.strip()

    def test_no_key_and_the_gateway_on_a_line_in_the_language_of_the_server(self):
        for gen in (2, 1):
            self.world(gen=gen)
            line = self.hint()
            self.assertTrue(line.startswith("Можно включить лёгкие пробы сети: "), line)
            self.assertIn(", ".join(hostping.DEFAULT_ANCHORS), line)
            self.assertIn("пункт 22 SSH-меню или kaskadctl host set --probes on", line)
            self.assertEqual(len(line.splitlines()), 1)
        i18n.set_lang("en")
        with i18n.use_lang("ru"):                       # the reader's language does not matter: the server's one
            self.assertTrue(self.hint().startswith("You can turn on light network probes: "))

    def test_nothing_when_the_key_is_set_or_no_collector(self):
        for kw in ({"hostmon_": {"probes": True}}, {"hostmon_": {"probes": False}}, {"gateway": False},
                   {"engine": "legacy"}, {"hostmon_": {"enabled": False}}):
            self.world(**kw)
            self.assertEqual(self.hint(), "", kw)
        rc, doc, _ = run_json("host", "probes-hint")
        self.assertEqual((rc, doc), (0, {"ok": True, "hint": None}))

    def test_a_broken_state_is_no_hint_and_code_0(self):
        self.write("/etc/kaskad/state.json", "{broken")
        self.assertEqual(self.hint(), "")


class SummaryAdviceTest(RootCase):
    """REV-ns-a5 С-1: an alarm ``host_*`` is not a forwarding problem (16) — item 22 and its own advice."""

    def test_every_kind_of_the_node_has_item_22_and_its_advice(self):
        for kind in hostmon.HOST_KINDS:
            item, text = summary.advice(kind)
            self.assertEqual(item, 22, kind)
            for lang in ("ru", "en"):
                self.assertIn("summary.advice." + kind, i18n.catalog(lang), (kind, lang))
            self.assertEqual(text, i18n.tr("summary.advice." + kind))
            self.assertNotIn("Сторож чинит пробросы", text)
        self.assertEqual(summary.advice("host_new_group"), (22, i18n.tr("summary.advice.host")))
        p = summary.problem("host_disk", "На диске / осталось 0,4 ГБ (99 % занято)", "live")
        self.assertEqual((p["item"], p["advice"]), (22, i18n.tr("summary.advice.host_disk")))
        self.assertEqual(summary.advice("weird")[0], 16)                    # the others as before
        self.assertEqual(summary.advice("gw_weird")[0], 17)


class SummaryLiveTest(HostWorld):
    def test_disk_alarm_in_the_summary_with_item_22(self):
        self.disk = 99.0
        for _ in range(3):
            self.round()
        rc, doc, _ = run_json("summary")
        self.assertEqual(rc, 0)
        p = [x for x in doc["problems"] if x["kind"] == "host_disk"]
        self.assertEqual(len(p), 1, doc["problems"])
        self.assertEqual((p[0]["item"], p[0]["source"]), (22, "live"))
        self.assertIn("22 «Нагрузка и сеть сервера»", p[0]["advice"])


class McpCatalogTest(McpCase):
    def test_tools(self):
        h, s = TOOLS["host_health"], TOOLS["host_set"]
        self.assertEqual((h["cmd"], h["readonly"], h["destructive"]), (["host", "health"], True, False))
        self.assertEqual((s["cmd"], s["readonly"], s["destructive"], s.get("open_world")),
                         (["host", "set"], False, False, True))
        self.assertEqual(set(s["params"]), {"on", "off", "probes", "anchors", "push_level", "bench_retry",
                                            "dev_threshold", "dev_spread", "code"})  # B14: SS §2.8 п. 5, NS-D25
        self.assertEqual(s["args"], ["--by-mcp"])           # REV-ns-b14 З-2: the checks — with the code of the server
        self.assertNotIn("ask_human", s)                    # the other settings — as in 3.3.7
        self.assertIn("host probes-hint", ACTIONS["hidden"])
        for name in ("host_health", "host_set"):
            for lang in ("ru", "en"):
                self.assertTrue(TOOLS[name]["description"][lang])

    def test_host_set_goes_with_the_flags_of_the_cli(self):
        sess = self.run_session([call(1, "host_set", {"probes": "on", "anchors": "1.1.1.1,8.8.8.8", "push_level": "warn",
                                                      "on": True})])
        argv = sess.result(1)["structuredContent"]["argv"]
        self.assertEqual(argv[:2], ["host", "set"])
        for a in ("--on", "--probes=on", "--anchors=1.1.1.1,8.8.8.8", "--push-level=warn", "--json"):
            self.assertIn(a, argv)
        sess = self.run_session([call(2, "host_set", {"anchors": "-x"})])
        self.assertTrue(sess.responses[2].get("error") or sess.result(2)["isError"])

    def test_host_health_through_mcp_is_the_cli_answer(self):
        """«Готово, когда»: ``kaskadctl host health --json`` and MCP ``host_health`` give the same."""
        root = os.path.join(self.tmp, "root")
        env = {"KASKAD_ROOT": root}
        with mock.patch.dict(os.environ, env):
            rc, doc, _ = run_json("host", "health")
        self.assertEqual(rc, 0)
        sess = self.run_session([call(1, "host_health")], KASKAD_MCP_CTL="", KASKAD_MCP_LANG="ru", timeout=120)
        r = sess.result(1)
        self.assertFalse(r["isError"], r)
        got = r["structuredContent"]
        for d in (doc, got):
            d["health"].pop("at", None)
            d["live"].pop("age", None)
        self.assertEqual(got, doc)
