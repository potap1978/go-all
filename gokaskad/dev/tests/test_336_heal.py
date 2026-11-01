"""3.3.6, R-336-heal (находки REV-336-awgaddr-3: З-1, П-1, П-2).

З-1: нога AmneziaWG, закрытая из-за того, что имя Endpoint не разрешилось (awgconf, код 4), получает метку «ждёт DNS»
(.kawgN.nodns). Раз в минуту проба потерь бота зовёт gokaskad --egress-awg-revive. Он поднимает только такие ноги и
только без интерфейса, не чаще раза в 50 с, когда DNS снова отвечает. Это работает и на узле только с ботом: там нет
сторожа шлюза. Нога снова поднята — строка журнала, маршрут таблицы метки через ногу на месте, бот снова выходит через
неё. На шлюзе то же делает его сторож (egress_awg_heal → egress_awg_leg_up).
П-1: починка (egress_awg_heal) спрашивает имена одним ожиданием на проход. Старт юнита сразу после сборки
(ExecStartPre --egress-awg-conf) берёт её ответы (.awgdns.json моложе минуты), а не ждёт DNS заново по 2 с на ногу.
П-2: знаки-теги остаются только у трёх флагов-эмодзи: Англии, Шотландии и Уэльса.

Каждая проверка падает на коде до правки (9dbeb88): там нет кода 4 и метки, нет --egress-awg-revive, починка ждёт DNS
по 4 с на ногу, а «🏴 + 6 любых тегов + отмена» проходит как флаг."""

import json
import os
import re
import subprocess
import time
import unittest

from kaskad.gw import exits
from tests.fakes_gw import INSTALL_SH
from tests.test_334_xray import Quiet, parser
from tests.test_336_awgaddr import LEG_FUNCS, LegConfBase, fn, read

REVIVE_FUNCS = LEG_FUNCS + ("egress_awg_heal", "egress_awg_revive", "egress_awg_revive_now",
                            "egress_awg_nodns_waiting", "egress_awg_nodns_due", "egress_awg_teardown")
# systemd ног: is-active — по интерфейсу; restart — ExecStartPre (--egress-awg-conf) и интерфейс, как настоящий юнит
UNITS = r'''
systemctl() {
    echo "systemctl $*" >> "$LOG"
    local u i
    case "$1" in
        is-active)
            if [ "$2" = --quiet ]; then u="$3"; else u="$2"; fi
            i="${u#kaskad-leg@}"; i="${i%.service}"
            if grep -qx "$i" "$W/links"; then [ "$2" = --quiet ] || echo active; return 0; fi
            [ "$2" = --quiet ] || echo inactive; return 3 ;;
        restart)
            i="${2#kaskad-leg@}"; i="${i%.service}"
            egress_awg_conf_unit "$i" || return 1
            grep -qx "$i" "$W/links" || echo "$i" >> "$W/links" ;;
    esac
    return 0
}
'''


class ReviveTest(LegConfBase):
    """З-1 и П-1: нога ждёт DNS и оживает сама; рабочие ноги не трогаются; частота ограничена."""

    FUNCS = REVIVE_FUNCS

    def marker(self, n=1):
        return os.path.exists(os.path.join(self.w, "awg", ".kawg%d.nodns" % n))

    def asked(self):
        try:
            with open(os.path.join(self.w, "dns.json.asked")) as f:
                return f.read().split()
        except OSError:
            return []

    def forget_asked(self):
        try:
            os.unlink(os.path.join(self.w, "dns.json.asked"))
        except OSError:
            pass

    def old_stamp(self):
        f = os.path.join(self.w, "awg", ".revive")
        if os.path.exists(f):
            t = time.time() - 120
            os.utime(f, (t, t))

    def close_a(self):
        """Нога 1 — ключ A по имени; при сборке DNS не ответил: нога закрыта и ждёт DNS."""
        self.keys((1, "awg-a.example.net:51820"))
        self.dns({})
        rc, log = self.run_sh("egress_awg_sync")
        self.assertEqual(rc, 0)
        self.assertIsNone(self.conf())
        self.assertTrue(self.marker())
        self.assertTrue([ln for ln in log if "EGRESS_LOG_LEG_CLOSED_DNS kawg1" in ln], log)
        self.assertIn("257 257", self.rules())

    def test_parser_code_4_only_for_dns(self):
        """awgconf: имя не разрешилось — код 4; имя указывает на этот сервер — код 3; IP — конфиг."""
        env = dict(os.environ, PYTHONPATH=os.path.join(self.w, "site"), K3_FAKE_DNS=os.path.join(self.w, "dns.json"))

        def code(ep, dns):
            self.keys((1, ep)); self.dns(dns)
            with open(os.path.join(self.w, "up.txt")) as f:
                return subprocess.run(["python3", self.parse, "awgconf", "1"], stdin=f, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, env=env, timeout=60).returncode
        self.assertEqual(code("awg-a.example.net:51820", {}), 4)
        self.assertEqual(code("awg-a.example.net:51820", {"awg-a.example.net": ["127.0.0.1"]}), 3)
        self.assertEqual(code("awg-a.example.net:51820", {"awg-a.example.net": ["203.0.113.9"]}), 0)
        self.assertEqual(code("203.0.113.9:51820", {}), 0)
        self.assertEqual(code("127.0.0.1:51820", {}), 3)

    def test_marker_only_for_dns(self):
        """Имя указывает на этот сервер — нога закрыта до пересборки, метки «ждёт DNS» нет, строка журнала прежняя."""
        self.keys((1, "awg-a.example.net:51820"))
        self.dns({"awg-a.example.net": ["127.0.0.1"]})
        rc, log = self.run_sh("egress_awg_sync")
        self.assertIsNone(self.conf())
        self.assertFalse(self.marker())
        self.assertTrue([ln for ln in log if "EGRESS_LOG_LEG_CLOSED kawg1" in ln], log)
        self.assertFalse([ln for ln in log if "CLOSED_DNS" in ln], log)

    def test_dns_back_leg_up_again(self):
        """DNS вернулся — проход оживления поднимает ногу: конфиг с IP, юнит перезапущен, маршрут таблицы метки через
        ногу (бот снова выходит через неё), метки нет, в журнале «нога снова поднята». Старт юнита берёт ответы
        прохода: имя спрошено один раз."""
        self.close_a()
        self.dns({"awg-a.example.net": ["203.0.113.9"]})
        self.forget_asked()
        rc, log = self.run_sh("egress_awg_revive", UNITS)
        self.assertEqual(rc, 0)
        self.assertIn("Endpoint = 203.0.113.9:51820", self.conf() or "")
        self.assertFalse(self.marker())
        acts = self.acts(log)
        self.assertIn("systemctl restart kaskad-leg@kawg1.service", acts)
        self.assertIn("ip route replace default dev kawg1 table 257", acts)
        self.assertTrue([ln for ln in log if "EGRESS_LOG_LEG_REVIVED kawg1" in ln], log)
        self.assertEqual(self.asked(), ["awg-a.example.net"])
        self.assertIn("locked egress_awg_revive_now", log)

    def test_dns_still_dead_quiet_and_rate_limited(self):
        """DNS всё ещё молчит — нога закрыта, метка остаётся, журнал не повторяет «закрыта». Следующий проход раньше
        50 с ничего не делает (ни DNS, ни блокировки пула); позже — пробует снова."""
        self.close_a()
        self.forget_asked()
        rc, log = self.run_sh("egress_awg_revive", UNITS)
        self.assertEqual(rc, 0)
        self.assertTrue(self.marker())
        self.assertIsNone(self.conf())
        self.assertNotIn("systemctl restart kaskad-leg@kawg1.service", self.acts(log))
        self.assertFalse([ln for ln in log if ln.startswith("log ")], log)
        self.assertEqual(self.asked(), ["awg-a.example.net"])
        self.forget_asked()
        rc, log = self.run_sh("egress_awg_revive", UNITS)
        self.assertEqual(self.asked(), [])
        self.assertNotIn("locked egress_awg_revive_now", log)
        self.old_stamp()
        self.dns({"awg-a.example.net": ["203.0.113.9"]})
        rc, log = self.run_sh("egress_awg_revive", UNITS)
        self.assertIn("Endpoint = 203.0.113.9:51820", self.conf() or "")
        self.assertTrue([ln for ln in log if "EGRESS_LOG_LEG_REVIVED kawg1" in ln], log)

    def test_working_legs_untouched(self):
        """Нога 1 работает (IP), нога 2 ждёт DNS — оживление перезапускает только ногу 2."""
        self.keys((1, "198.51.100.31:51820"), (2, "awg-b.example.net:51820"))
        self.write("links", "kawg1\nkawg2\n")
        rc, log = self.run_sh("egress_awg_sync")
        self.assertIn("Endpoint = 198.51.100.31:51820", self.conf(1))
        self.assertTrue(self.marker(2))
        self.assertFalse(self.marker(1))
        self.dns({"awg-b.example.net": ["203.0.113.12"]})
        rc, log = self.run_sh("egress_awg_revive", UNITS)
        acts = self.acts(log)
        self.assertIn("systemctl restart kaskad-leg@kawg2.service", acts)
        self.assertFalse([a for a in acts if "kawg1" in a], acts)
        self.assertIn("Endpoint = 203.0.113.12:51820", self.conf(2))

    def test_nothing_waits_nothing_runs(self):
        """Меток нет — ни разборщика, ни DNS, ни блокировки пула."""
        self.keys((1, "awg-a.example.net:51820"))
        rc, log = self.run_sh("egress_awg_revive", UNITS)
        self.assertEqual(rc, 0)
        self.assertEqual(log, [])
        self.assertEqual(self.asked(), [])

    def test_marker_of_a_gone_key_goes(self):
        """Метка ноги, которой больше нет в пуле, — прочь при проходе; снятие ноги убирает и её метку."""
        self.keys((1, "198.51.100.31:51820"))
        self.write("links", "")
        os.makedirs(os.path.join(self.w, "awg"), exist_ok=True)
        open(os.path.join(self.w, "awg", ".kawg5.nodns"), "w").close()
        self.run_sh("egress_awg_revive", UNITS)
        self.assertFalse(self.marker(5))
        open(os.path.join(self.w, "awg", ".kawg1.nodns"), "w").close()
        self.run_sh("egress_awg_leg_remove kawg1 keep")
        self.assertFalse(self.marker(1))

    def test_heal_one_dns_wait(self):
        """П-1: у шлюза пропали все пять ног (их имена по-прежнему в конфигах с IP), DNS молчит. Починка ждёт DNS один
        раз (около 2 с), старт каждого юнита — без нового ожидания: ноги снова на прежних IP. До правки — 2 с на
        конфиг и ещё 2 с на старт юнита у каждой ноги."""
        n = 5
        self.keys(*[(i, "s%d.example.net:51820" % i) for i in range(1, n + 1)])
        self.dns({"s%d.example.net" % i: ["203.0.113.%d" % i] for i in range(1, n + 1)})
        self.write("links", "".join("kawg%d\n" % i for i in range(1, n + 1)))
        rc, log = self.run_sh("egress_awg_sync")
        for i in range(1, n + 1):
            self.assertIn("Endpoint = 203.0.113.%d:51820" % i, self.conf(i))
        self.write("links", "")
        self.stale_answers()
        # R-speed-2: та же починка при отвечающем DNS — сколько она идёт на этой машине сейчас (рядом прогоны с Chrome:
        # 2–7 с); граница ниже — на ожидание DNS сверх этого, а не на всё время (под нагрузкой было 9,0 с при 8,0)
        t0 = time.monotonic()
        rc, log = self.run_sh("egress_awg_heal", UNITS)
        base = time.monotonic() - t0
        self.assertEqual(rc, 0, log)
        self.write("links", "")
        self.stale_answers()
        self.dns({"s%d.example.net" % i: "sleep" for i in range(1, n + 1)})
        t0 = time.monotonic()
        rc, log = self.run_sh("egress_awg_heal", UNITS)
        took = time.monotonic() - t0
        self.assertEqual(rc, 0, log)
        self.assertLess(took - base, 2.0 + n * 1.2, (took, base))   # до правки — не меньше 4 с на ногу (здесь 20 с)
        with open(os.path.join(self.w, "links")) as f:
            self.assertEqual(sorted(f.read().split()), ["kawg%d" % i for i in range(1, n + 1)])
        for i in range(1, n + 1):
            self.assertIn("Endpoint = 203.0.113.%d:51820" % i, self.conf(i))

    def test_heal_revives_on_the_gateway(self):
        """Шлюз: та же нога, ждущая DNS, поднимается сторожем (egress_awg_heal) — с той же строкой журнала."""
        self.close_a()
        self.dns({"awg-a.example.net": ["203.0.113.9"]})
        rc, log = self.run_sh("egress_awg_heal", UNITS)
        self.assertEqual(rc, 0)
        self.assertFalse(self.marker())
        self.assertTrue([ln for ln in log if "EGRESS_LOG_LEG_REVIVED kawg1" in ln], log)

    def test_probe_calls_revive_every_minute(self):
        """Проба потерь: раз в минуту (60 / EGRESS_PROBE_EVERY проходов) — --egress-awg-revive, если есть метки; проверка
        меток — без python. Точка входа знает --egress-awg-revive."""
        names = ("_egr_count", "egress_xray_bin", "egress_active_index", "egress_probe_sample", "egress_tofu_waiting",
                 "egress_awg_nodns_waiting", "egress_probe_script")
        script = ('EGRESS_PROBE_EVERY=10; K3_OPT=/opt/kaskad; AWG_DIR="$1"\n' + "".join(fn(f) for f in names)
                  + "egress_probe_script\n")
        os.makedirs(os.path.join(self.w, "awg"), exist_ok=True)
        r = subprocess.run(["bash", "-c", script, "_", os.path.join(self.w, "awg")], stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, universal_newlines=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout
        self.assertIn("AWG_DIR=%s\n" % os.path.join(self.w, "awg"), out)
        self.assertIn("egress_awg_nodns_waiting () ", out)
        self.assertRegex(out, r'if \[ \$\(\(n % 6\)\) -eq 0 \] && egress_awg_nodns_waiting; then "\$K3_KICK" '
                              r'--egress-awg-revive >/dev/null 2>&1; fi')
        probe = os.path.join(self.w, "probe.sh")
        with open(probe, "w") as f:
            f.write(out)
        self.assertEqual(subprocess.run(["bash", "-n", probe]).returncode, 0)
        check = 'AWG_DIR="$1"\n' + fn("egress_awg_nodns_waiting") + "egress_awg_nodns_waiting\n"
        run = lambda: subprocess.run(["bash", "-c", check, "_", os.path.join(self.w, "awg")]).returncode
        self.assertEqual(run(), 1)
        open(os.path.join(self.w, "awg", ".kawg3.nodns"), "w").close()
        self.assertEqual(run(), 0)
        self.assertRegex(read(INSTALL_SH), r"(?m)^    --egress-awg-revive\) egress_awg_revive ;;")


class FlagTagsTest(Quiet):
    """П-2: теги — только у флагов Англии, Шотландии и Уэльса; «🏴 + 6 любых тегов + отмена» — чёрный флаг без букв."""

    @staticmethod
    def flag(code):
        return "\U0001f3f4" + "".join(chr(0xE0000 + ord(c)) for c in code) + "\U000e007f"

    def test_three_flags_only(self):
        from kaskad.mcp import server
        p = parser()
        real = [self.flag(c) for c in ("gbeng", "gbsct", "gbwls")]
        fake = "Exit " + self.flag("sendme") + self.flag("thekey") + self.flag("plzzzz")
        self.assertEqual(sum(1 for ch in fake if 0xE0000 <= ord(ch) <= 0xE007F), 21)    # 18 букв и 3 отмены
        for f in (p._term, exits.term_text, exits.no_tags, server.no_tags):
            self.assertEqual(f(fake), "Exit \U0001f3f4\U0001f3f4\U0001f3f4", f)
            self.assertEqual(f("".join(real)), "".join(real), f)                     # три настоящих флага подряд
            self.assertEqual(f("Лондон " + real[0] + " №1"), "Лондон " + real[0] + " №1", f)
            self.assertEqual(f(self.flag("usca")), "\U0001f3f4", f)                  # не эмодзи: штата США нет
            self.assertEqual(f(self.flag("gbeng")[:-1]), "\U0001f3f4", f)            # без отмены — не флаг
        src = read(INSTALL_SH)
        rule = re.search(r'_TERM_TAGS = re\.compile\("([^"]+)"\)', src).group(1)
        self.assertEqual(src.count('"%s"' % rule), 3)
        self.assertNotIn("{2,6}", rule)


if __name__ == "__main__":
    unittest.main()
