"""3.3.5.1, безопасность (SEC-s1-t11 Б-1, З-1): адрес и имя ключа приходят из чужой ссылки или подписки.

Б-1: хост и порт ключа шли в текст команды внутреннего bash (egress_key_ping, меню 17 → 3) — метасимволы оболочки в них
выполнялись бы от root. Теперь разборщик принимает только IP или домен (IDN — в punycode) и порт 1..65535 (иначе
bad_link: ключ не попадает в пул, у ключа пула — «?» вместо адреса), а bash передаёт хост и порт аргументами и ещё раз
проверяет их сам. З-1: управляющие символы (ESC, BEL, C0/C1) из имени ключа не уходят в терминал SSH-меню.

Каждая проверка «файл-метка не создан» на коде до правки падает: метка появляется."""

import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from kaskad.gw import exits
from tests.fakes_gw import INSTALL_SH, awg_conf
from tests.test_334_xray import Quiet, parser

UUID = "11111111-2222-3333-4444-555555555555"
TAIL = "?type=tcp&security=reality&pbk=8FXcm8haAt5zxGJFC6-71alFTj_LhAL5hbOx7QBE6lc&sid=80cd0a54&sni=www.microsoft.com"
CTRL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def function(name):
    """Функция install.sh целиком (от «name() {» до «}» в начале строки)."""
    with open(INSTALL_SH, encoding="utf-8") as f:
        src = f.read()
    m = re.search(r"^%s\(\) \{.*?^\}\n" % re.escape(name), src, re.S | re.M)
    if not m:                                            # однострочная: «name() { …; }»
        m = re.search(r"^%s\(\) \{.*\}\n" % re.escape(name), src, re.M)
    assert m, name
    return m.group(0)


def helpers():
    """Проверки адреса (их нет в коде до правки — тогда пусто)."""
    out = []
    for n in ("egress_host_ok", "egress_port_ok"):
        try:
            out.append(function(n))
        except AssertionError:
            pass
    return "\n".join(out)


def vless(host, port="443", name="k"):
    return "vless://%s@%s:%s%s#%s" % (UUID, host, port, TAIL, name)


class Mark:
    """Временный каталог; в нём — файл-метка, которую создала бы подстановка."""

    def __enter__(self):
        self.d = tempfile.mkdtemp(prefix="k3-cmdinj-")
        self.mark = os.path.join(self.d, "PWNED")
        return self

    def __exit__(self, *a):
        shutil.rmtree(self.d, ignore_errors=True)

    def hosts(self):
        """Хосты с метасимволами оболочки: каждый, выполнившись, создал бы метку."""
        m = self.mark
        return ["$(touch${IFS}%s)" % m, "`touch${IFS}%s`" % m, "a;touch${IFS}%s;" % m, "a|touch${IFS}%s" % m,
                "a&&touch${IFS}%s" % m, "a'$(touch${IFS}%s)'" % m, "a\ntouch %s" % m, "a touch %s" % m]

    def ports(self):
        m = self.mark
        return ["443;touch${IFS}%s" % m, "$(touch${IFS}%s)" % m, "443`touch${IFS}%s`" % m]


def bash(script, *args):
    return subprocess.run(["bash", "-c", script, "_"] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=60, universal_newlines=True)


# timeout(1) есть не везде (macOS): та же семантика без ограничения времени — команда своя, не сеть
SHIM = 'timeout() { shift; "$@"; }\n'


class ParserAddressTest(Quiet):
    """Разборщик: плохой адрес — bad_link, в xray и в list не идёт."""

    def test_bad_hosts_are_bad_link(self):
        p = parser()
        with Mark() as m:
            for h in m.hosts():
                line = vless(h).replace("\n", "%0a")
                self.assertEqual(p.vless_problem(line), "bad_link", h)
                self.assertIsNone(p.vless_outbound(line, "t"), h)
                self.assertEqual(p.vless_label(line)["host"], "?", h)
                good, skipped = p.extract_report(line)
                self.assertEqual(good, [], h)
                self.assertEqual([x["code"] for x in skipped], ["bad_link"], h)

    def test_bad_ports_are_bad_link(self):
        p = parser()
        with Mark() as m:
            for port in m.ports() + ["0", "65536", "-1", "44a", "４４３"]:
                line = vless("198.51.100.20", port)
                self.assertEqual(p.vless_problem(line), "bad_link", port)
                self.assertIsNone(p.vless_outbound(line, "t"), port)
                self.assertEqual(p.vless_label(line)["host"], "?", port)

    def test_good_addresses_stay(self):
        p = parser()
        for host, want in (("198.51.100.20", "198.51.100.20"), ("[2001:db8::1]", "2001:db8::1"),
                           ("Exit-1.Example.com", "exit-1.example.com"), ("пример.рф", "xn--e1afmkfd.xn--p1ai"),
                           ("srv_1.example.com", "srv_1.example.com")):
            line = vless(host, "8443")
            self.assertEqual(p.vless_problem(line), "", host)
            ob = p.vless_outbound(line, "t")
            self.assertEqual(ob["settings"]["vnext"][0]["address"], want, host)
            self.assertEqual(ob["settings"]["vnext"][0]["port"], 8443, host)
            self.assertEqual(p.vless_label(line)["host"], "%s:8443" % want, host)
        self.assertEqual(p.vless_outbound(vless("198.51.100.20").replace(":443?", "?"), "t")
                         ["settings"]["vnext"][0]["port"], 443)

    def awg_skip(self, p, conf):
        """extract_report of a .conf: (pool lines, codes of the skipped)."""
        good, skipped = p.extract_report(conf)
        return good, [x["code"] for x in skipped]

    def test_awg_endpoint_checked(self):
        """AmneziaWG с плохим Endpoint или Address — не молча: K3SKIP bad_link с именем ключа, не в пул."""
        p = parser()
        with Mark() as m:
            bad = ["%s:51820" % h for h in m.hosts() if "\n" not in h]
            bad += ["198.51.100.31:%s" % port for port in m.ports() + ["0", "70000"]]
            bad += ["[fe80::1%25eth0]:51820", "[fe80::1%eth0]:51820", "[fe80::1%\x1b]52;c;QQ==\x07]:51820"]
            for ep in bad:
                self.assertEqual(self.awg_skip(p, awg_conf(endpoint=ep)), ([], ["bad_link"]), ep)
            for a in ("$(touch${IFS}%s)" % m.mark, "10.8.1.5/32;x", "-c9", "fe80::2%eth0/64"):
                self.assertEqual(self.awg_skip(p, awg_conf(address=a)), ([], ["bad_addr"]), a)
            r = subprocess.run(["python3", p.__file__, "extract"], input=awg_conf(endpoint=bad[0]),
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=60)
            self.assertEqual(r.stdout, "")
            self.assertRegex(r.stderr, r"(?m)^K3SKIP\tbad_link\t\S")
            self.assertNotIn(m.mark, r.stderr)
        for ep in ("198.51.100.31:51820", "[2001:db8::7]:51820", "awg.example.com:443"):
            self.assertEqual(p.awg_from_conf(awg_conf(endpoint=ep))["peer"]["endpoint"], ep)
            good, codes = self.awg_skip(p, awg_conf(endpoint=ep))
            self.assertEqual((len(good), codes), (1, []), ep)

    def test_port_as_urllib_reads_it(self):
        """Без скобок порт — всё после первого «:», как у urllib: IPv6 без скобок и «хост:порт:порт» — не адрес."""
        p = parser()
        for hp in ("2001:db8::1:443", "198.51.100.1:443:8443", "198.51.100.1:"):
            line = "vless://%s@%s%s#k" % (UUID, hp, TAIL)
            want = "" if hp.endswith(":") else "bad_link"      # «host:» — порт по умолчанию, как у urllib
            self.assertEqual(p.vless_problem(line), want, hp)
            self.assertEqual(p.vless_outbound(line, "t") is None, bool(want), hp)

    def test_problem_error_is_marked(self):
        """Ошибка разбора не значит «нет причины»: ключ — bad_link, а не хороший."""
        p = parser()
        line = vless("198.51.100.20").replace("security=reality", "security=tls")
        with mock.patch.object(p, "_tls_check", side_effect=RuntimeError("x")):
            self.assertEqual(p.vless_problem(line), "bad_link")

    def test_awg_client_address(self):
        """Б-1: Address клиента — один адрес: IPv4 /8–/32, IPv6 /64–/128 (без префикса — /32, /128); иначе bad_addr."""
        p = parser()
        for a, want in (("10.8.1.5/32", "10.8.1.5/32"), ("10.8.1.5", "10.8.1.5/32"), ("10.8.0.2/24", "10.8.0.2/24"),
                        ("10.8.0.2/8", "10.8.0.2/8"), ("fd42::2/128", "fd42::2/128"), ("fd42::2/64", "fd42::2/64"),
                        ("FD42:0::2", "fd42::2/128")):
            self.assertEqual(p._awg_ifaddr(a), want, a)
            self.assertEqual(self.awg_skip(p, awg_conf(address=a))[1], [], a)
        for a in ("10.8.0.2/0", "10.8.0.2/1", "10.8.0.2/7", "0.0.0.0/32", "224.0.0.1/32", "fd42::2/48", "::/128",
                  "10.8.0.2/33", "10.8.0.2/24/1"):
            self.assertIsNone(p._awg_ifaddr(a), a)
            self.assertEqual(self.awg_skip(p, awg_conf(address=a)), ([], ["bad_addr"]), a)

    def test_leg_address_without_prefix_route(self):
        """Адрес ноги — noprefixroute (юнит и drop-in модуля ядра): /24 ключа не забирает сеть в основной таблице."""
        for fn in ("egress_leg_template", "egress_leg_set_mode"):
            body = function(fn)
            self.assertIn("address replace \\${ADDR} dev %i noprefixroute", body, fn)
            self.assertNotRegex(body, r"address replace \\\$\{ADDR\} dev %i\n", fn)

    def test_ipv6_zone_refused(self):
        """С-1: IPv6 с зоной «%…» (ip_address берёт любой хвост на 3.9+) — не адрес, в хвосте может быть ESC/OSC."""
        p = parser()
        for h in ("fe80::1%25eth0", "fe80::1%eth0", "fe80::1%25%1b]52;c;QQ==%07", "fe80::1%25$(id)"):
            line = vless("[%s]" % h)
            self.assertEqual(p.vless_problem(line), "bad_link", h)
            self.assertIsNone(p.vless_outbound(line, "t"), h)
            self.assertEqual(p.vless_label(line)["host"], "?", h)
        self.assertIsNone(p._host("fe80::1%eth0"))
        self.assertEqual(p._host("2001:0DB8:0:0::1"), "2001:db8::1")

    def test_pool_line_with_bad_address(self):
        """Ключ, попавший в пул до правки: list и awglist адреса не печатают, problems — bad_link, config — без него."""
        p = parser()
        with Mark() as m:
            bad = vless(m.hosts()[0])
            rec = p.awg_from_conf(awg_conf())
            rec["peer"]["endpoint"] = "%s:51820" % m.hosts()[2]
            awg = "awg://" + base64.urlsafe_b64encode(json.dumps(rec).encode()).decode().rstrip("=")
            rec["peer"]["endpoint"] = "198.51.100.31:51820"
            rec["address"] = "$(touch${IFS}%s)" % m.mark
            awg2 = "awg://" + base64.urlsafe_b64encode(json.dumps(rec).encode()).decode().rstrip("=")
            pool = "\n".join([bad, awg, vless("198.51.100.20"), awg2]) + "\n"
            out = self.run_parser(p, ["list"], pool)
            self.assertNotIn(m.mark, out)
            self.assertEqual([ln.split("\t")[1] for ln in out.splitlines()],
                             ["?", "?", "198.51.100.20:443", "198.51.100.31:51820"])
            # no leg for such keys: a leg of an older version is removed (egress_awg_sync — not in awglist)
            self.assertEqual(self.run_parser(p, ["awglist"], pool), "")
            self.assertEqual(self.run_parser(p, ["problems"], pool).split(),
                             ["1", "bad_link", "2", "bad_link", "4", "bad_addr"])
            cfg = json.loads(self.run_parser(p, ["config"], pool))
            self.assertNotIn(m.mark, json.dumps(cfg))
            self.assertEqual([o["tag"] for o in cfg["outbounds"] if o["tag"].startswith("out_")], ["out_3"])
            for idx in ("2", "4"):                       # no leg for such a key: awgconf refuses
                r = subprocess.run(["python3", p.__file__, "awgconf", idx], input=pool, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, universal_newlines=True, timeout=60)
                self.assertEqual((r.returncode, r.stdout), (3, ""), idx)

    def run_parser(self, p, args, stdin):
        r = subprocess.run(["python3", p.__file__] + args, input=stdin, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, universal_newlines=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout


class ParserNameTest(Quiet):
    """З-1: имя ключа в терминал — без управляющих символов."""

    NAMES = ["a%1b]52;c;SGVsbG8=%07b", "a%1b[2J%1b[Hb", "a%c2%9b31mb", "a%07%7f%00b", "a%1b]0;title%1b%5cb"]

    def test_list_has_no_control_bytes(self):
        p = parser()
        pool = "\n".join(vless("198.51.100.%d" % (20 + i), name=n) for i, n in enumerate(self.NAMES))
        rec = p.awg_from_conf(awg_conf(), "w\x1b]52;c;SGVsbG8=\x07x")
        pool += "\nawg://" + base64.urlsafe_b64encode(json.dumps(rec).encode()).decode().rstrip("=") + "\n"
        r = subprocess.run(["python3", p.__file__, "list"], input=pool, stdout=subprocess.PIPE, timeout=60,
                           universal_newlines=True)
        lines = r.stdout.splitlines()
        self.assertEqual(len(lines), len(self.NAMES) + 1)
        for ln in lines:
            self.assertEqual(len(ln.split("\t")), 4, repr(ln))
            self.assertIsNone(CTRL.search(ln.replace("\t", "")), repr(ln))
        self.assertTrue(lines[0].startswith("a ]52;c;SGVsbG8= b\t"), repr(lines[0]))

    def test_skip_name_has_no_control_bytes(self):
        p = parser()
        for n in self.NAMES:
            got = p._skip_name(vless("198.51.100.20", name=n))
            self.assertIsNone(CTRL.search(got), repr(got))

    def test_exit_name_has_no_control_bytes(self):
        n = exits.own_name({"name": "x\x1b]52;c;SGVsbG8=\x07y\x9b", "host": "198.51.100.20:443"})
        self.assertEqual(n, "x ]52;c;SGVsbG8= y")
        # «\» — echo -e терминала выполнил бы «\033]52…» из имени (%5C в ссылке)
        p = parser()
        lab = p.pool_label(vless("198.51.100.20", name="x%5C033]52;c;SGVsbG8=%5C007y"))
        n = exits.own_name({"name": lab["name"], "host": lab["host"]})
        self.assertNotIn("\\", n)
        self.assertEqual(n, "x 033]52;c;SGVsbG8= 007y")
        self.assertEqual(exits.term_text("a\\033[2J\x1b]0;t\x07b"), "a 033[2J ]0;t b")


class BashPingTest(unittest.TestCase):
    """egress_key_ping и awg_ping_ms: метасимволы в хосте и порте не выполняются."""

    def test_key_ping_runs_nothing(self):
        body = SHIM + helpers() + function("egress_key_ping")
        with Mark() as m:
            for h in m.hosts():
                r = bash(body + 'egress_key_ping "$1" "$2"', h, "9")
                self.assertFalse(os.path.exists(m.mark), "host %r ran: %s" % (h, r.stderr))
                self.assertEqual(r.stdout.strip(), "—", h)
            for port in m.ports():
                r = bash(body + 'egress_key_ping "$1" "$2"', "127.0.0.1", port)
                self.assertFalse(os.path.exists(m.mark), "port %r ran: %s" % (port, r.stderr))
                self.assertEqual(r.stdout.strip(), "—", port)

    def test_menu_chain_runs_nothing(self):
        """Путь меню 17 → 3 (_egr_measure): пул → list → хост:порт → egress_key_ping / awg_ping_ms."""
        p = parser()
        body = SHIM + helpers() + function("egress_key_ping") + function("awg_ping_ms") + r'''
ping() { echo "ping $*" >> "$LOG"; }
while IFS=$'\t' read -r name host sec net; do
    hp="$host"; p="${hp##*:}"; h="${hp%:*}"
    if [[ "$sec" == amnezia* ]]; then awg_ping_ms "$h"; else egress_key_ping "$h" "$p"; fi
done < <(python3 "$1" list < "$2")
'''
        # хост ссылки проходит urlparse (без «/», в нижнем регистре) — метка в текущем каталоге, по имени
        hosts = ["$(touch pwned)", "`touch pwned`", "a;touch pwned;", "a|touch pwned", "a&&touch pwned",
                 "a'$(touch pwned)'"]
        with Mark() as m:
            pool = os.path.join(m.d, "upstream.txt")
            rec = p.awg_from_conf(awg_conf())
            rec["peer"]["endpoint"] = "-c9:51820"
            awg = "awg://" + base64.urlsafe_b64encode(json.dumps(rec).encode()).decode().rstrip("=")
            with open(pool, "w") as f:
                f.write("\n".join([vless(h) for h in hosts] + [awg]) + "\n")
            log = os.path.join(m.d, "ping.log")
            r = subprocess.run(["bash", "-c", body, "_", p.__file__, pool], env=dict(os.environ, LOG=log), cwd=m.d,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=120)
            self.assertFalse(os.path.exists(os.path.join(m.d, "pwned")), r.stderr)
            self.assertFalse(os.path.exists(log), "ping got an option from a key")
            self.assertEqual(r.stdout.split(), ["—"] * (len(hosts) + 1))

    def test_host_port_checks(self):
        body = helpers()
        for h in ("198.51.100.20", "2001:db8::1", "exit-1.example.com", "xn--e1afmkfd.xn--p1ai", "a_b.example"):
            self.assertEqual(bash(body + 'egress_host_ok "$1"', h).returncode, 0, h)
        for h in ("", "?", "-c9", "a b", "a;b", "$(id)", "a`b`", "a|b", "a&b", "a'b", "a\nb", "a/b", "a" * 254):
            self.assertNotEqual(bash(body + 'egress_host_ok "$1"', h).returncode, 0, h)
        for port in ("1", "443", "65535", "08443"):
            self.assertEqual(bash(body + 'egress_port_ok "$1"', port).returncode, 0, port)
        for port in ("", "0", "65536", "99999", "-1", "443;x", "4a", "123456"):
            self.assertNotEqual(bash(body + 'egress_port_ok "$1"', port).returncode, 0, port)


@unittest.skipUnless(shutil.which("jq"), "jq")
class ExitsTableTest(unittest.TestCase):
    """З-1: экран выходов шлюза (k3_gw_exits_table) печатает имя и адрес ключа printf %s, без «\\» и управляющих байтов."""

    def test_no_terminal_sequences_from_a_key(self):
        name = "x\\033]52;c;SGVsbG8=\\007y\x1b]0;t\x07z\u009b31m"
        doc = {"ok": True, "exits": [{"id": "e-1", "index": 1, "name": name, "where": "VLESS · 198.51.100.37",
                                      "named": True, "kind": "vless", "host": "198.51.100.37:443", "usable": True,
                                      "enabled": True, "state": "alive", "text": "работает\\a"}]}
        script = 'source "$1" --lib >/dev/null 2>&1 || exit 97; K3_GW_OUT=$2; k3_gw_exits_table'
        r = subprocess.run(["bash", "-c", script, "_", INSTALL_SH, json.dumps(doc)], stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=60, env=dict(os.environ, LANG="en_US.UTF-8"))
        out = r.stdout.decode("utf-8", "replace")
        self.assertIn("198.51.100.37", out, r.stderr)
        plain = re.sub(r"\x1b\[[0-9;]*m", "", out)      # цвета самого меню
        self.assertIsNone(CTRL.search(plain.replace("\n", "")), repr(plain))
        self.assertNotIn("\\", plain)
        self.assertIn("1) x033]52;c;SGVsbG8=007y ]0;t z 31m (VLESS · 198.51.100.37) — работаетa", plain)


def _b64u(s):
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


def vmess(add="vm.example.com", port="443", ps="VM"):
    j = {"v": "2", "ps": ps, "add": add, "port": port, "id": UUID.replace("5555", "6666", 1), "aid": "0", "scy": "auto",
         "net": "ws", "type": "none", "host": "vm.example.com", "path": "/vm", "tls": "tls", "sni": "vm.example.com"}
    return "vmess://" + base64.b64encode(json.dumps(j).encode()).decode()


# Ключи схем этапа 1 (T11): хост, порт и имя — из чужой ссылки, как у VLESS и AmneziaWG (класс ошибки 3.3.5.1)
NEW = {
    "hy2": "hy2://hyAuth1@{h}:{p}/?sni=hy.example.com#{n}",
    "ss": "ss://" + _b64u("chacha20-ietf-poly1305:ssSecret1") + "@{h}:{p}#{n}",
    "trojan": "trojan://tjSecret1@{h}:{p}?security=tls&sni=tj.example.com&type=tcp#{n}",
}


def new_link(kind, host, port="8443", name="k"):
    if kind == "vmess":
        return vmess(host.strip("[]"), port, name)
    return NEW[kind].format(h=host, p=port, n=name)


KINDS = ("hy2", "ss", "trojan", "vmess")


class NewSchemesTest(Quiet):
    """3.3.5.1 на схемах этапа 1: hy2, ss, trojan, vmess (и tuic — не берётся): плохой адрес — bad_link, ни в xray, ни в
    list/endpoint (ping, проба потерь), имя — без управляющих байтов."""

    def check_bad(self, p, line, why):
        self.assertEqual(p.link_problem(line), "bad_link", why)
        self.assertIsNone(p.xray_outbound(line, "t"), why)
        self.assertEqual(p.pool_label(line)["host"], "?", why)
        self.assertIsNone(p.endpoint(line), why)
        good, skipped = p.extract_report(line)
        self.assertEqual((good, [x["code"] for x in skipped]), ([], ["bad_link"]), why)

    def test_bad_hosts_are_bad_link(self):
        p = parser()
        with Mark() as m:
            for kind in KINDS:
                for h in m.hosts():
                    line = new_link(kind, h.replace("\n", "%0a") if kind != "vmess" else h)
                    self.check_bad(p, line, (kind, h))
                    self.assertNotIn(m.mark, p._skip_name(line), (kind, h))
            for h in m.hosts():                 # endpoint of VLESS (T11: «endpoint» of the probe of losses) — checked too
                self.assertIsNone(p.endpoint(vless(h.replace("\n", "%0a"))), h)

    def test_bad_ports_are_bad_link(self):
        p = parser()
        with Mark() as m:
            for kind in KINDS:
                for port in m.ports() + ["0", "65536", "44a"]:
                    self.check_bad(p, new_link(kind, "198.51.100.20", port), (kind, port))

    def test_ipv6_zone_refused(self):
        """«%…» после IPv6 (ip_address берёт любой хвост на 3.9+): в _parts хост не раскодируется — «%25» тоже зона."""
        p = parser()
        for kind in KINDS:
            for h in ("[fe80::1%25eth0]", "[fe80::1%eth0]", "[fe80::1%25$(id)]", "[fe80::1%251b]52;c;QQ==%07]"):
                self.check_bad(p, new_link(kind, h), (kind, h))

    def test_good_addresses_stay(self):
        """Адрес как пишет ip_address (IPv6 — сжатый, в нижнем регистре), домен — в punycode; кто ключ (peer_key) —
        одинаково у разборщика и ядра (exits.peer_key)."""
        p = parser()
        for kind in KINDS:
            for host, want in (("198.51.100.20", "198.51.100.20"), ("[2001:0DB8:0::1]", "2001:db8::1"),
                               ("Exit-1.Example.com", "exit-1.example.com"), ("пример.рф", "xn--e1afmkfd.xn--p1ai")):
                line = new_link(kind, host)
                self.assertEqual(p.link_problem(line), "", (kind, host))
                self.assertEqual(p.endpoint(line), (want, 8443), (kind, host))
                self.assertIsNotNone(p.xray_outbound(line, "t"), (kind, host))
                self.assertEqual(p.peer_key(line), exits.peer_key(line, p), (kind, host))
        self.assertIsNone(exits._host("fe80::1%eth0"))
        self.assertEqual(exits._host("2001:0DB8:0:0::1"), "2001:db8::1")

    def test_names_have_no_control_bytes(self):
        p = parser()
        names = ParserNameTest.NAMES
        lines = [new_link(kind, "198.51.100.%d" % (20 + i), name=n) for kind in ("hy2", "ss", "trojan")
                 for i, n in enumerate(names)]
        lines += [vmess("198.51.100.9", "443", "v\x1b]52;c;SGVsbG8=\x07w\u009b31m")]
        for line in lines:
            got = p._skip_name(line)
            self.assertIsNone(CTRL.search(got), repr(got))
            self.assertNotIn("\\", got)
        r = subprocess.run(["python3", p.__file__, "list"], input="\n".join(lines) + "\n", stdout=subprocess.PIPE,
                           timeout=60, universal_newlines=True)
        out = r.stdout.splitlines()
        self.assertEqual(len(out), len(lines))
        for ln in out:
            self.assertEqual(len(ln.split("\t")), 4, repr(ln))
            self.assertIsNone(CTRL.search(ln.replace("\t", "")), repr(ln))

    def test_unsupported_scheme_says_its_name_only(self):
        """tuic:// и другие — «другие протоколы: TUIC»: ни хоста, ни имени ключа в K3SKIP."""
        p = parser()
        with Mark() as m:
            line = "tuic://%s:pw@$(touch${IFS}%s):443#n\x1b]0;t\x07" % (UUID, m.mark)
            r = subprocess.run(["python3", p.__file__, "extract"], input=line + "\n", stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, universal_newlines=True, timeout=60)
            self.assertEqual(r.stdout, "")
            self.assertIn("K3SKIP\tunsupported_scheme\tTUIC\n", r.stderr)
            self.assertNotIn(m.mark, r.stderr)
            self.assertIsNone(CTRL.search(r.stderr.replace("\t", "").replace("\n", "")), repr(r.stderr))

    def test_menu_chain_runs_nothing(self):
        """Меню 17 → 3 с ключами hy2/ss/trojan/vmess: list → хост:порт → egress_key_ping — ничего не выполняется."""
        p = parser()
        body = SHIM + helpers() + function("egress_key_ping") + function("awg_ping_ms") + r'''
while IFS=$'\t' read -r name host sec net; do
    hp="$host"; p="${hp##*:}"; h="${hp%:*}"
    if [[ "$sec" == amnezia* ]]; then awg_ping_ms "$h"; else egress_key_ping "$h" "$p"; fi
done < <(python3 "$1" list < "$2")
'''
        hosts = ["$(touch pwned)", "`touch pwned`", "a;touch pwned;", "a|touch pwned", "a&&touch pwned"]
        with Mark() as m:
            pool = os.path.join(m.d, "upstream.txt")
            with open(pool, "w") as f:
                f.write("\n".join(new_link(k, h) for k in KINDS for h in hosts) + "\n")
            r = subprocess.run(["bash", "-c", body, "_", p.__file__, pool], cwd=m.d, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, universal_newlines=True, timeout=120)
            self.assertFalse(os.path.exists(os.path.join(m.d, "pwned")), r.stderr)
            self.assertEqual(r.stdout.split(), ["—"] * (len(KINDS) * len(hosts)))

    def test_endpoint_second_line(self):
        """egress_key_endpoint (проба потерь: ss -tinO «dst …»): хост и порт из разборщика проверяются ещё раз."""
        p = parser()
        body = helpers() + function("egress_key_endpoint") + 'egress_key_endpoint "$1"'
        with Mark() as m:
            up = os.path.join(m.d, "upstream.txt")
            with open(up, "w") as f:
                f.write("\n".join([new_link("trojan", "$(touch${IFS}%s)" % m.mark), new_link("hy2", "198.51.100.5"),
                                   vless("198.51.100.6"), vless("$(touch${IFS}%s)" % m.mark)]) + "\n")
            stub = os.path.join(m.d, "stub.py")        # разборщик чужой версии: отдаёт хост как есть
            with open(stub, "w") as f:
                f.write("import sys\nprint('$(touch ${IFS}%s)\\t443\\ttrojan')\nprint('198.51.100.5\\t44;3\\thy2')\n"
                        "print('198.51.100.6\\t443\\tvless')\n" % m.mark)
            for py, want in ((p.__file__, ["", "198.51.100.5\t8443\thysteria2", "198.51.100.6\t443\tvless", ""]),
                             (stub, ["", "", "198.51.100.6\t443\tvless", ""])):
                d = os.path.join(m.d, "e-" + os.path.basename(py))
                os.mkdir(d)
                env = dict(os.environ, EGRESS_UP=up, EGRESS_PARSE_PY=py, EGRESS_DIR=d)
                got = [subprocess.run(["bash", "-c", body, "_", str(i)], env=env, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, universal_newlines=True, timeout=60).stdout.rstrip("\n")
                       for i in (1, 2, 3, 4)]
                self.assertEqual(got, want, py)
            self.assertFalse(os.path.exists(m.mark))


class PanelCodesTest(unittest.TestCase):
    """bad_link и bad_addr — коды, которые знают меню, ядро и подписки (слова RU/EN)."""

    def test_codes_known(self):
        from kaskad.gw import exitsubs, ops
        for code in ("bad_link", "bad_addr"):
            self.assertIn(code, ops.SKIP_CODES)
            self.assertIn(code, exitsubs.SKIP_CODES)
            self.assertIn(code, ops.VLESS_KEY_CODES)
        with open(INSTALL_SH, encoding="utf-8") as f:
            src = f.read()
        for key in ("EGRESS_KEY_SKIP_BAD_LINK", "EGRESS_SKIP_WHY_BAD_LINK", "EGRESS_W_VLESS_BAD_LINK",
                    "EGRESS_KEY_SKIP_BAD_ADDR", "EGRESS_SKIP_WHY_BAD_ADDR", "EGRESS_W_AWG_BAD_ADDR"):
            for lang in ("ru", "en"):
                self.assertEqual(len(re.findall(r"(?m)^M_%s_%s=" % (lang, key), src)), 1, (lang, key))
        for code in ("bad_link", "bad_addr"):
            self.assertIn("%s)" % code, function("egress_skip_report"))
            self.assertIn("%s)" % code, function("egress_awg_warn_text"))


if __name__ == "__main__":
    unittest.main()
