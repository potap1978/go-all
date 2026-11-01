#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Юнит-тесты lib/awgkeys.py и lib/awg_params.sh (CONTRACT §7).

Запуск: python3 -m unittest tests/test_awgkeys.py  (или discover -s tests).
Сеть, Docker и root не нужны. Bash-часть берёт ${BASH5:-/opt/homebrew/bin/bash|bash}.
Сверка с эталонным декодером навыка telegram-api-egress-vless-awg пропускается,
если файла нет на машине (путь можно задать через GOAMNEZIA_REF_DECODER).
"""

import base64
import importlib.util
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import unittest
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LIB = os.path.join(ROOT, "lib")
AWGKEYS_PY = os.path.join(LIB, "awgkeys.py")
AWG_PARAMS_SH = os.path.join(LIB, "awg_params.sh")
LANG_RU = os.path.join(LIB, "lang", "ru.d", "awg_params.sh")
LANG_EN = os.path.join(LIB, "lang", "en.d", "awg_params.sh")


def bash_env(**extra):
    """Окружение для bash, у которого проверяется пустой stderr: без LANG и LC_* машины.

    CI, экспортирующий локаль, которой в образе нет (en_US.UTF-8 без пакета locales), получает от
    каждого bash «setlocale: cannot change locale» в stderr — и проверка «stderr пуст» краснела бы на
    машине, а не на продукте. На Linux — C.UTF-8 (есть в glibc всегда), на macOS локаль не нужна."""
    env = {k: v for k, v in os.environ.items() if k != "LANG" and not k.startswith("LC_")}
    if sys.platform.startswith("linux"):
        env["LANG"] = "C.UTF-8"
    env.update(extra)
    return env

# Эталонный декодер — parser Kaskad (egress_parse.py, PARSER_VER=6) из навыка серии telegram-api-egress-vless-awg:
# независимый разбор vpn:// и .conf, проверенный на ключах приложения Amnezia. Копия байт в байт лежит в
# tests/fixtures (TAIL-13: прежде тест искал его по пути навыка на машине владельца, и на Linux три проверки
# пропускались). GOAMNEZIA_REF_DECODER — свежая версия эталона вместо копии.
REF_DECODER = os.environ.get("GOAMNEZIA_REF_DECODER") or os.path.join(HERE, "fixtures", "egress_parse.py")


def _load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


awg = _load_module(AWGKEYS_PY, "awgkeys")


def _bash():
    b = os.environ.get("BASH5")
    if b:
        return b
    if os.path.exists("/opt/homebrew/bin/bash"):
        return "/opt/homebrew/bin/bash"
    return shutil.which("bash") or "bash"


def cli(*args, stdin=None):
    """Запуск awgkeys.py как подпроцесса: (код, stdout, stderr)."""
    proc = subprocess.run([sys.executable, AWGKEYS_PY] + list(args), input=stdin,
                          capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


# Ключи из RFC 7748 §6.1 — известные пары, чтобы тесты были воспроизводимы.
ALICE_PRIV_HEX = "77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a"
ALICE_PUB_HEX = "8520f0098930a754748b7ddcb43ef75a0dbf3a0d26381af4eba4a98eaa9b4e6a"
BOB_PRIV_HEX = "5dab087e624a8a4b79e17f8b83800ee66f3bb1292618b6fd1c2f8b27ff88e0eb"
BOB_PUB_HEX = "de9edb7d7b7dc1b4d35b61c2ece435373f8343c85b78674dadfc7e146f882b4f"
SHARED_HEX = "4a5d9d5ba4ce2de1728e3bf480350f25e07e21c947d19e3376f09b3c1e161742"


def b64(hexstr):
    return base64.b64encode(bytes.fromhex(hexstr)).decode()


CLIENT_PRIV = b64(ALICE_PRIV_HEX)
CLIENT_PUB = b64(ALICE_PUB_HEX)
SERVER_PRIV = b64(BOB_PRIV_HEX)
SERVER_PUB = b64(BOB_PUB_HEX)
PSK = b64("00" * 31 + "01")
HPK = b64("11" * 32)
# Универсальный I1 с постоянным ID транзакции 0x1a2b (KEENETIC-I1) — для сравнения текстов без случайности.
I1_SAMPLE = "<b 0x1a2b" + awg.I1_DNS_ANSWER + ">"

HOST = "203.0.113.10"
DOMAIN = "vpn.example.com"


def sample_params(line):
    """Детерминированные параметры каждой линейки (без случайности, для сравнения текстов)."""
    p = {k: "" for k in awg.PARAM_KEYS}
    p.update({"Jc": "4", "Jmin": "10", "Jmax": "50", "MTU": "1280", "PersistentKeepalive": "25"})
    if line == "3.1":
        p.update({"S1": "12", "S2": "12", "S3": "12", "S4": "12",
                  "H1": "1", "H2": "2", "H3": "3", "H4": "4",
                  "I1": I1_SAMPLE, "HeaderProtectionKey": HPK,
                  "RekeyAfterTime": "100-120", "RekeyTimeout": "3-7", "RejectAfterTime": "150-180",
                  "KeepaliveTimeout": "5-15", "MaxHandshakeAttempts": "15-20",
                  "RandomTrailers": "on", "DisableCookies": "on", "PersistentKeepalive": "25-35"})
    elif line == "2.0":
        p.update({"S1": "40", "S2": "70", "S3": "20", "S4": "30",
                  "H1": "1000000-1002000", "H2": "2000000-2003000",
                  "H3": "3000000-3004000", "H4": "4000000-4005000", "I1": I1_SAMPLE})
    else:
        p.update({"S1": "40", "S2": "70",
                  "H1": "1000001", "H2": "1000002", "H3": "1000003", "H4": "1000004"})
        if line == "1.5":
            p["I1"] = I1_SAMPLE
    return p


def conf_input(line, host=HOST, dns=("1.1.1.1", "1.0.0.1"), port=443):
    return {
        "params": sample_params(line),
        "client": {"ip": "10.8.2.5", "privkey": CLIENT_PRIV, "psk": PSK},
        "server": {"pubkey": SERVER_PUB, "endpoint_host": host, "port": port},
        "dns": list(dns),
        "mtu": "1280",
    }


def vpn_input(line, host=HOST, dns=("1.1.1.1", "1.0.0.1"), port=443, container="amnezia-awg2"):
    conf_text = awg.render_conf(line, conf_input(line, host, dns, port))
    return {
        "container": container,
        "line": line,
        "params": sample_params(line),
        "conf_text": conf_text,
        "description": "Анна — awg2",
        "hostName": host,
        "port": port,
        "dns": list(dns),
        "subnet": "10.8.2.0/24",
    }


def conf_lines(text):
    return [l for l in text.split("\n") if l.strip()]


def iface_keys(text):
    iface, _ = awg.parse_conf(text)
    return set(iface)


# ---------------------------------------------------------------------------
# X25519 и ключи
# ---------------------------------------------------------------------------

class TestX25519(unittest.TestCase):
    def test_rfc7748_alice_public(self):
        self.assertEqual(awg.x25519_base(bytes.fromhex(ALICE_PRIV_HEX)).hex(), ALICE_PUB_HEX)

    def test_rfc7748_bob_public(self):
        self.assertEqual(awg.x25519_base(bytes.fromhex(BOB_PRIV_HEX)).hex(), BOB_PUB_HEX)

    def test_rfc7748_shared_secret_both_sides(self):
        a = awg.x25519(bytes.fromhex(ALICE_PRIV_HEX), bytes.fromhex(BOB_PUB_HEX)).hex()
        b = awg.x25519(bytes.fromhex(BOB_PRIV_HEX), bytes.fromhex(ALICE_PUB_HEX)).hex()
        self.assertEqual(a, SHARED_HEX)
        self.assertEqual(b, SHARED_HEX)

    def test_rfc7748_section_5_2_vector(self):
        k = bytes.fromhex("a546e36bf0527c9d3b16154b82465edd62144c0ac1fc5a18506a2244ba449ac4")
        u = bytes.fromhex("e6db6867583030db3594c1a424b15f7c726624ec26b3353b10a903a6d0ab1c4c")
        self.assertEqual(awg.x25519(k, u).hex(),
                         "c3da55379de9c6908e94ea4df28d084f32eccf03491c71f754b4075577a28552")

    def test_pubkey_matches_rfc(self):
        self.assertEqual(awg.pubkey(CLIENT_PRIV), CLIENT_PUB)
        self.assertEqual(awg.pubkey(SERVER_PRIV), SERVER_PUB)

    def test_genkey_is_clamped_base64_32(self):
        for _ in range(20):
            key = awg.genkey()
            raw = base64.b64decode(key)
            self.assertEqual(len(key), 44)
            self.assertEqual(len(raw), 32)
            self.assertEqual(raw[0] & 7, 0)
            self.assertEqual(raw[31] & 128, 0)
            self.assertEqual(raw[31] & 64, 64)
            self.assertTrue(awg.is_key(awg.pubkey(key)))

    def test_genpsk(self):
        psk = awg.genpsk()
        self.assertEqual(len(base64.b64decode(psk)), 32)
        self.assertNotEqual(awg.genpsk(), psk)

    def test_bad_keys_rejected(self):
        for bad in ("", "abc", base64.b64encode(b"x" * 31).decode(), "not base64!!"):
            self.assertFalse(awg.is_key(bad))
            with self.assertRaises(awg.AwgError):
                awg.pubkey(bad)

    def test_cli_genkey_pubkey_genpsk(self):
        code, out, err = cli("genkey")
        self.assertEqual(code, 0, err)
        priv = out.strip()
        self.assertTrue(awg.is_key(priv))
        code, out, _ = cli("pubkey", priv)
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), awg.pubkey(priv))
        code, out, _ = cli("pubkey", "-", stdin=priv + "\n")
        self.assertEqual(out.strip(), awg.pubkey(priv))
        code, out, _ = cli("genpsk")
        self.assertEqual(code, 0)
        self.assertTrue(awg.is_key(out.strip()))
        code, _, err = cli("pubkey", "garbage")
        self.assertEqual(code, 2)
        self.assertIn("awgkeys", err)


# ---------------------------------------------------------------------------
# Генерация по линейкам
# ---------------------------------------------------------------------------

class TestGenParams(unittest.TestCase):
    ROUNDS = 40

    def _gen_many(self, line, preset="default"):
        return [awg.gen_params(line, preset) for _ in range(self.ROUNDS)]

    def test_all_keys_present_and_valid(self):
        for line in awg.LINES:
            for preset in awg.PRESETS:
                for p in self._gen_many(line, preset):
                    self.assertEqual(set(p), set(awg.PARAM_KEYS), (line, preset))
                    self.assertEqual(awg.validate_params(line, p), [], (line, preset, p))
                    self.assertEqual(p["MTU"], "1280")
                    self.assertIn(int(p["Jc"]), (4, 5, 6))
                    self.assertEqual((p["Jmin"], p["Jmax"]), ("10", "50"))

    def test_line_1_0(self):
        for p in self._gen_many("1.0"):
            self.assertTrue(all(p[k] == "" for k in awg.I_KEYS))
            self.assertEqual((p["S3"], p["S4"]), ("", ""))
            self.assertTrue(all(p[k] == "" for k in awg.AWG3_KEYS))
            s1, s2 = int(p["S1"]), int(p["S2"])
            self.assertTrue(15 <= s1 <= 150 and 15 <= s2 <= 150)
            self.assertNotEqual(s1 + 56, s2)
            hs = [int(p[h]) for h in awg.H_KEYS]
            self.assertEqual(len(set(hs)), 4)
            self.assertTrue(all(5 <= h < 2 ** 31 for h in hs))
            self.assertEqual(p["PersistentKeepalive"], "25")
            self.assertEqual(awg.detect_line(p), "1.0")
        # для 1.0 пресет не меняет ничего — I-пакетов там нет
        for preset in ("quic", "router"):
            for p in self._gen_many("1.0", preset):
                self.assertEqual(p["I1"], "")

    def test_line_1_5_presets(self):
        for p in self._gen_many("1.5"):
            # KEENETIC-I1: универсальный I1 — один тег <b>, ID транзакции 2 байта, дальше DNS-ответ icloud.com.
            self.assertRegex(p["I1"], awg.DEFAULT_I1_RE)
            self.assertTrue(p["I1"].startswith("<b 0x"))
            self.assertEqual((p["S3"], p["S4"]), ("", ""))
            self.assertTrue(all("-" not in p[h] for h in awg.H_KEYS))
            self.assertEqual(awg.detect_line(p), "1.5")
        for p in self._gen_many("1.5", "quic"):
            self.assertEqual(p["I1"], awg.QUIC_I1)
            self.assertTrue(p["I1"].startswith("<b 0xc"))
            self.assertEqual(awg.detect_line(p), "1.5")
        for p in self._gen_many("1.5", "router"):
            self.assertEqual(p["I1"], "")
            self.assertEqual(awg.detect_line(p), "1.0")  # router = 1.0 без I

    def test_line_2_0(self):
        for p in self._gen_many("2.0"):
            s = {k: int(p[k]) for k in awg.S_KEYS}
            self.assertTrue(15 <= s["S1"] <= 150 and 15 <= s["S2"] <= 150)
            # MTU-01: S4 новых 2.0 — не больше 40 (бюджет пути ≈1400: 1400 − 80 − 1280 = 40), раньше было 15–150;
            # S4-MARGIN (29.10): 12–24 — пакет не больше 1384, запас 16 байт.
            self.assertTrue(15 <= s["S3"] <= 64 and 12 <= s["S4"] <= 24)
            sizes = [148 + s["S1"], 92 + s["S2"], 64 + s["S3"], 32 + s["S4"]]
            self.assertEqual(len(set(sizes)), 4)
            spans = []
            for h in awg.H_KEYS:
                m = re.fullmatch(r"(\d+)-(\d+)", p[h])
                self.assertIsNotNone(m, p[h])
                a, b = int(m.group(1)), int(m.group(2))
                self.assertTrue(1000 <= b - a <= 5000)
                self.assertTrue(5 <= a and b < 2 ** 31)
                spans.append((a, b))
            for i in range(4):
                for j in range(i + 1, 4):
                    a1, b1 = spans[i]
                    a2, b2 = spans[j]
                    self.assertTrue(b1 < a2 or b2 < a1, spans)
            self.assertRegex(p["I1"], awg.DEFAULT_I1_RE)
            self.assertTrue(all(p[k] == "" for k in awg.AWG3_KEYS))
            self.assertEqual(awg.detect_line(p), "2.0")
        self.assertEqual(awg.gen_params("2.0", "quic")["I1"], awg.QUIC_I1)
        self.assertEqual(awg.gen_params("2.0", "router")["I1"], "")

    def test_line_3_1(self):
        for p in self._gen_many("3.1"):
            self.assertTrue(all(p[k] == "12" for k in awg.S_KEYS))
            self.assertEqual([p[h] for h in awg.H_KEYS], ["1", "2", "3", "4"])
            self.assertTrue(awg.is_key(p["HeaderProtectionKey"]))
            self.assertEqual(p["RekeyAfterTime"], "100-120")
            self.assertEqual(p["RekeyTimeout"], "3-7")
            self.assertEqual(p["RejectAfterTime"], "150-180")
            self.assertEqual(p["KeepaliveTimeout"], "5-15")
            self.assertEqual(p["MaxHandshakeAttempts"], "15-20")
            # 1.0.13 по живым проверкам 08.10: keepalive 25 (PARAM-07: NAT 30 с терял запись при 25-35) и добавка
            # 10-100 (PARAM-04: подтверждения TCP с хвостами раздувались) — отступления от установщика приложения.
            self.assertEqual(p["PersistentKeepalive"], "25")
            self.assertEqual((p["RandomTrailers"], p["DisableCookies"]), ("on", "on"))
            self.assertEqual(p["ContentPaddingAddition"], "10-100")
            self.assertRegex(p["I1"], awg.DEFAULT_I1_RE)
            self.assertEqual(awg.detect_line(p), "3.1")
        keys = {awg.gen_params("3.1")["HeaderProtectionKey"] for _ in range(5)}
        self.assertEqual(len(keys), 5)  # ключ свежий каждый раз
        self.assertEqual(awg.gen_params("3.1", "quic")["I1"], awg.QUIC_I1)
        self.assertEqual(awg.gen_params("3.1", "router")["I1"], "")

    def test_quic_i1_is_well_formed_1200_bytes(self):
        self.assertIsNone(awg._check_ipacket(awg.QUIC_I1))
        total = 0
        for m in re.finditer(r"<([a-z]+) ([^<>]+)>", awg.QUIC_I1):
            total += (len(m.group(2)) - 2) // 2 if m.group(1) == "b" else int(m.group(2))
        self.assertEqual(total, 1200)

    def test_default_i1_universal(self):
        """KEENETIC-I1: I1 по умолчанию 1.5, 2.0 и 3.1 — только тег <b> (Keenetic 5.1.x не принимает I1, который
        начинается с <r 2>); ID транзакции — 2 случайных байта, свои у каждой генерации; дальше — DNS-ответ icloud.com."""
        # Прежнее умолчание — дословно строка 1.0.11–1.0.12: по ней миграция узнаёт, что менять.
        self.assertEqual(awg.LEGACY_DEFAULT_I1,
                         "<r 2><b 0x858000010001000000000669636c6f756403636f6d0000010001c00c000100010000105a00044d583737>")
        for line in ("1.5", "2.0", "3.1"):
            ids = set()
            for p in self._gen_many(line):
                i1 = p["I1"]
                self.assertTrue(i1.startswith("<b 0x"), (line, i1))
                self.assertNotIn("<r", i1)
                self.assertRegex(i1, awg.DEFAULT_I1_RE)
                self.assertEqual(i1[len("<b 0x") + 4:], awg.I1_DNS_ANSWER + ">")
                self.assertEqual(awg._ipacket_scan(i1, "write"), (None, []))
                ids.add(i1[5:9])
            # 40 генераций с 65 536 вариантами ID: одинаковые все — вероятность ≈ 0.
            self.assertGreater(len(ids), 1, line)
            self.assertEqual(awg.gen_params(line, "router")["I1"], "")
            self.assertEqual(awg.gen_params(line, "quic")["I1"], awg.QUIC_I1)
        # Тот же пакет, что прежде, только ID постоянный: 2 + 42 байта = 44.
        self.assertEqual(len(bytes.fromhex(awg.default_i1()[5:-1])), 44)

    def test_i1_universal_migration(self):
        """KEENETIC-I1, миграция и подхват: новый I1 — только если I1 ровно прежнее умолчание (после normalize_params);
        свой I1 человека, пустой и уже универсальный — None; I2–I5 на решение не влияют."""
        legacy = {"I1": awg.LEGACY_DEFAULT_I1, "I2": "<b 0x01><r 4>"}
        new = awg.i1_universal(legacy)
        self.assertRegex(new, awg.DEFAULT_I1_RE)
        self.assertEqual(legacy["I2"], "<b 0x01><r 4>")          # набор не меняется на месте
        self.assertRegex(awg.i1_universal({"I1": "  " + awg.LEGACY_DEFAULT_I1 + "\n"}), awg.DEFAULT_I1_RE)
        for own in ("", None, I1_SAMPLE, "<r 2><b 0x0102>", awg.LEGACY_DEFAULT_I1.upper(),
                    awg.LEGACY_DEFAULT_I1 + "<r 4>", awg.QUIC_I1):
            self.assertIsNone(awg.i1_universal({"I1": own}), own)
        self.assertIsNone(awg.i1_universal({}))
        self.assertIsNone(awg.i1_universal({"I1": new}))        # повтор после замены — ничего
        # CLI: stdin — JSON params; есть замена — строка I1, нет — пусто; код 0 в обоих случаях.
        code, out, err = cli("i1-universal", stdin=json.dumps(legacy))
        self.assertEqual(code, 0, err)
        self.assertRegex(out.strip(), awg.DEFAULT_I1_RE)
        code, out, err = cli("i1-universal", stdin=json.dumps({"I1": I1_SAMPLE}))
        self.assertEqual((code, out), (0, ""), err)
        code, out, _ = cli("i1-universal", stdin="[]")
        self.assertEqual((code, out), (2, ""))

    def test_bad_line_or_preset(self):
        with self.assertRaises(awg.AwgError):
            awg.gen_params("4.0")
        with self.assertRaises(awg.AwgError):
            awg.gen_params("2.0", "stealth")

    def test_cli_gen_params(self):
        for line in awg.LINES:
            code, out, err = cli("gen-params", "--line", line, "--preset", "quic")
            self.assertEqual(code, 0, err)
            p = json.loads(out)
            self.assertEqual(set(p), set(awg.PARAM_KEYS))
            self.assertEqual(awg.validate_params(line, p), [])
        code, _, _ = cli("gen-params", "--line", "9.9")
        self.assertNotEqual(code, 0)


# ---------------------------------------------------------------------------
# Валидатор — каждое правило отдельно
# ---------------------------------------------------------------------------

class TestValidate(unittest.TestCase):
    def errs(self, line, **changes):
        p = sample_params(line)
        p.update(changes)
        return awg.validate_params(line, p)

    def assertRule(self, line, needle, **changes):
        errs = self.errs(line, **changes)
        self.assertTrue(any(needle in e for e in errs), "expected %r in %r" % (needle, errs))

    def test_samples_are_valid(self):
        for line in awg.LINES:
            self.assertEqual(awg.validate_params(line, sample_params(line)), [], line)

    def test_unknown_line(self):
        self.assertTrue(awg.validate_params("5.0", sample_params("1.0")))

    def test_required_keys(self):
        for k in ("Jc", "Jmin", "Jmax", "S1", "S2", "H1", "H2", "H3", "H4"):
            self.assertRule("1.0", "%s is required" % k, **{k: ""})

    def test_h_pairwise_distinct(self):
        self.assertRule("1.0", "H1 and H3 must differ", H3="1000001")

    def test_h_ranges_non_overlapping_2_0(self):
        self.assertRule("2.0", "H1 and H2 ranges overlap", H2="1001000-1005000")
        self.assertEqual(self.errs("2.0", H2="1002001-1004000"), [])  # соседние, но не пересекаются

    def test_h_range_form(self):
        self.assertRule("2.0", "H1 range start must not exceed its end", H1="5000-4000")
        self.assertRule("2.0", "H1 must be an integer or a range", H1="12-34-56")
        self.assertRule("1.0", "H1 must be within 1..4294967295", H1="0")
        self.assertRule("1.0", "H1 must be within 1..4294967295", H1="4294967296")

    def test_jmin_jmax(self):
        self.assertRule("1.0", "Jmin must be < Jmax", Jmin="50", Jmax="50")
        self.assertRule("1.0", "Jmin must be < Jmax", Jmin="60", Jmax="50")
        self.assertRule("1.0", "Jmax must be <= 1280", Jmax="1281")
        self.assertEqual(self.errs("1.0", Jmax="1280"), [])

    def test_jc_bounds(self):
        self.assertRule("1.0", "Jc must be within 1..128", Jc="0")
        self.assertRule("1.0", "Jc must be within 1..128", Jc="129")
        # Формат пускает и 128 (так он разбирает подхваченные конфиги — adopt), а то, что пишем мы, — нет: клиент на
        # программе поднимается только при Jc + число I ≤ 127 (PARAM-09, вживую 08.10 — «Protocol error»).
        self.assertEqual(self.errs("1.0", Jc="127"), [])
        p = sample_params("1.0")
        p["Jc"] = "128"
        self.assertEqual(awg.validate_params("1.0", p, "adopt"), [])
        self.assertRule("1.0", "Jc + number of I1-I5 = 128", Jc="128")
        self.assertRule("1.0", "Jc must be a non-negative integer", Jc="-1")

    def test_s_bounds(self):
        self.assertRule("1.0", "S1 must be <= 1132", S1="1133")
        self.assertRule("1.0", "S2 must be <= 1188", S2="1189")
        self.assertEqual(self.errs("1.0", S1="1132", S2="1000"), [])
        self.assertRule("2.0", "S3 must be <= 64", S3="65")
        self.assertRule("2.0", "S4 must be <= 1248", S4="1249")

    def test_s1_plus_56_not_s2(self):
        self.assertRule("1.0", "S1+56 must not equal S2", S1="40", S2="96")

    def test_packet_sizes_pairwise_distinct(self):
        self.assertRule("2.0", "64+S3 equals 32+S4", S3="20", S4="52")
        # 148+S1 == 32+S4: S1=15 → 163, S4=131 → 163
        self.assertRule("2.0", "148+S1 equals 32+S4", S1="15", S4="131")
        # 92+S2 == 64+S3: S2=20 → 112, S3=48 → 112
        self.assertRule("2.0", "92+S2 equals 64+S3", S2="20", S3="48")
        self.assertEqual(self.errs("2.0", S1="15", S2="20", S3="15", S4="15"), [])

    def test_header_protection_requires_s_ge_12(self):
        self.assertRule("3.1", "S3 must be >= 12 when HeaderProtectionKey is set", S3="11")
        self.assertRule("3.1", "HeaderProtectionKey must be base64 of 32 bytes", HeaderProtectionKey="nope")

    def test_line_1_0_forbids(self):
        self.assertRule("1.0", "line 1.0 forbids I1-I5", I1=I1_SAMPLE)
        self.assertRule("1.0", "line 1.0 forbids S3/S4", S3="20")
        self.assertRule("1.0", "line 1.0 forbids H ranges", H1="1000001-1002000")
        self.assertRule("1.0", "line 1.0 forbids 3.x fields", RekeyTimeout="3-7")
        self.assertRule("1.0", "line 1.0 forbids 3.x fields", RandomTrailers="on")
        # выключенный переключатель — не признак 3.x (как у приложения)
        self.assertEqual(self.errs("1.0", RandomTrailers="off"), [])

    def test_line_1_5_forbids(self):
        self.assertEqual(self.errs("1.5", I1=I1_SAMPLE), [])
        self.assertRule("1.5", "line 1.5 forbids S3/S4", S4="20")
        self.assertRule("1.5", "line 1.5 forbids H ranges", H2="2000000-2003000")
        self.assertRule("1.5", "line 1.5 forbids 3.x fields", HeaderProtectionKey=HPK)

    def test_line_2_0_forbids_3x(self):
        self.assertRule("2.0", "line 2.0 forbids 3.x fields", HeaderProtectionKey=HPK)
        self.assertRule("2.0", "line 2.0 forbids 3.x fields", DisableCookies="on")
        self.assertRule("2.0", "PersistentKeepalive range is allowed only for line 3.1", PersistentKeepalive="25-35")

    def test_line_3_1_allows_everything(self):
        p = sample_params("3.1")
        p["ContentPaddingAddition"] = "10-100"
        self.assertEqual(awg.validate_params("3.1", p), [])

    def test_i_packet_syntax(self):
        self.assertRule("1.5", "I1: invalid tag syntax", I1="junk")
        self.assertRule("1.5", "I1: <b> needs 0x", I1="<b 0xabc>")
        self.assertRule("1.5", "I1: <r> needs a positive byte count", I1="<r 0>")
        self.assertRule("1.5", "I1: packet is", I1="<r 1281>")
        # <c> (счётчик) знает только модуль ядра, amneziawg-go 3.1 — нет (KMOD-02). То, что пишем мы (по умолчанию), —
        # только общие теги; уже записанное при подхвате (adopt) — теги любой реализации.
        mixed = "<b 0xf6ab3267fa><c><b 0xf6ab><t><r 10>"
        self.assertRule("1.5", "I1: <c> is understood only by the kernel module", I1=mixed)
        p = sample_params("1.5")
        p["I1"] = mixed
        self.assertEqual(awg.validate_params("1.5", p, "adopt"), [])

    def test_3x_value_formats(self):
        self.assertRule("3.1", "RekeyAfterTime must be an integer or a range", RekeyAfterTime="fast")
        self.assertRule("3.1", "RandomTrailers must be on or off", RandomTrailers="maybe")
        # Диапазон MTU — договор продукта, он живёт в ga_valid_mtu (lib/common.sh)
        # и проверяется в tests/test_core_dryrun.sh. Здесь только формат.
        self.assertRule("3.1", "MTU must be an integer", MTU="fast")
        self.assertEqual(self.errs("3.1", MTU="100"), [])

    def test_cli_validate_exit_codes(self):
        good = json.dumps(sample_params("2.0"))
        code, out, err = cli("validate", "--line", "2.0", stdin=good)
        self.assertEqual((code, out), (0, ""), err)
        code, out, err = cli("validate", "--line", "1.0", stdin=good)
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("line 1.0 forbids", err)
        code, _, err = cli("validate", "--line", "2.0", stdin="not json")
        self.assertEqual(code, 2)
        # текст .conf тоже принимается
        conf = awg.render_conf("2.0", conf_input("2.0"))
        code, _, err = cli("validate", "--line", "2.0", stdin=conf)
        self.assertEqual(code, 0, err)


# ---------------------------------------------------------------------------
# detect-line
# ---------------------------------------------------------------------------

class TestDetectLine(unittest.TestCase):
    def test_rules(self):
        base = sample_params("1.0")
        self.assertEqual(awg.detect_line(base), "1.0")
        self.assertEqual(awg.detect_line(dict(base, I3="<r 10>")), "1.5")
        self.assertEqual(awg.detect_line(dict(base, S4="20")), "2.0")
        self.assertEqual(awg.detect_line(dict(base, H4="10-20")), "2.0")
        self.assertEqual(awg.detect_line(dict(base, S3="20", I1="<r 1>")), "2.0")
        for k in awg.AWG3_VALUE_KEYS:
            self.assertEqual(awg.detect_line(dict(base, **{k: "1"})), "3.1", k)
        self.assertEqual(awg.detect_line(dict(base, RandomTrailers="on")), "3.1")
        self.assertEqual(awg.detect_line(dict(base, DisableCookies="off")), "1.0")
        self.assertEqual(awg.detect_line({}), "1.0")

    def test_from_conf_texts(self):
        for line in awg.LINES:
            self.assertEqual(awg.detect_line(awg.params_from_conf(awg.render_conf(line, conf_input(line)))), line)

    def test_cli_json_and_conf(self):
        code, out, _ = cli("detect-line", stdin=json.dumps(sample_params("3.1")))
        self.assertEqual((code, out.strip()), (0, "3.1"))
        code, out, _ = cli("detect-line", stdin=awg.render_conf("2.0", conf_input("2.0")))
        self.assertEqual((code, out.strip()), (0, "2.0"))
        code, out, _ = cli("detect-line", stdin=awg.render_conf("1.5", conf_input("1.5")))
        self.assertEqual((code, out.strip()), (0, "1.5"))
        code, _, _ = cli("detect-line", stdin="   ")
        self.assertEqual(code, 2)


# ---------------------------------------------------------------------------
# .conf клиента
# ---------------------------------------------------------------------------

class TestConf(unittest.TestCase):
    def test_no_empty_values_and_layout(self):
        for line in awg.LINES:
            text = awg.render_conf(line, conf_input(line))
            self.assertTrue(text.startswith("[Interface]\n"))
            self.assertTrue(text.endswith("PersistentKeepalive = 25\n") or text.endswith("PersistentKeepalive = 25-35\n"))
            for l in conf_lines(text):
                if "=" in l:
                    self.assertTrue(l.split("=", 1)[1].strip(), "empty value in %r" % l)
            self.assertIn("Address = 10.8.2.5/32", text)
            self.assertIn("DNS = 1.1.1.1, 1.0.0.1", text)
            self.assertIn("PrivateKey = %s" % CLIENT_PRIV, text)
            self.assertIn("MTU = 1280", text)
            self.assertIn("PublicKey = %s" % SERVER_PUB, text)
            self.assertIn("PresharedKey = %s" % PSK, text)
            self.assertIn("AllowedIPs = 0.0.0.0/0, ::/0", text)
            self.assertIn("Endpoint = 203.0.113.10:443", text)
            self.assertEqual(text.count("[Peer]"), 1)

    def test_line_1_0_has_no_i_s34_3x(self):
        keys = iface_keys(awg.render_conf("1.0", conf_input("1.0")))
        self.assertFalse(keys & set(awg.I_KEYS))
        self.assertFalse(keys & {"S3", "S4"})
        self.assertFalse(keys & set(awg.AWG3_KEYS))
        self.assertTrue({"Jc", "Jmin", "Jmax", "S1", "S2", "H1", "H2", "H3", "H4"} <= keys)
        # даже если в params прислали лишнее — 1.0 их не печатает
        data = conf_input("1.0")
        data["params"].update({"I1": I1_SAMPLE, "S3": "20", "S4": "30", "HeaderProtectionKey": HPK,
                               "RandomTrailers": "on", "PersistentKeepalive": "25-35"})
        text = awg.render_conf("1.0", data)
        keys = iface_keys(text)
        self.assertFalse(keys & (set(awg.I_KEYS) | {"S3", "S4"} | set(awg.AWG3_KEYS)))
        self.assertIn("PersistentKeepalive = 25\n", text)  # диапазон схлопнут до нижней границы

    def test_line_1_5_prints_i(self):
        text = awg.render_conf("1.5", conf_input("1.5"))
        self.assertIn("I1 = %s" % I1_SAMPLE, text)
        keys = iface_keys(text)
        self.assertFalse(keys & {"S3", "S4"})
        self.assertFalse(keys & set(awg.AWG3_KEYS))

    def test_line_2_0_no_3x(self):
        text = awg.render_conf("2.0", conf_input("2.0"))
        keys = iface_keys(text)
        self.assertTrue({"S3", "S4", "I1"} <= keys)
        self.assertIn("H1 = 1000000-1002000", text)
        self.assertFalse(keys & set(awg.AWG3_KEYS))
        data = conf_input("2.0")
        data["params"]["HeaderProtectionKey"] = HPK
        self.assertNotIn("HeaderProtectionKey", awg.render_conf("2.0", data))

    def test_line_3_1_has_header_protection(self):
        text = awg.render_conf("3.1", conf_input("3.1"))
        self.assertIn("HeaderProtectionKey = %s" % HPK, text)
        for k in ("RekeyAfterTime = 100-120", "RekeyTimeout = 3-7", "RejectAfterTime = 150-180",
                  "KeepaliveTimeout = 5-15", "MaxHandshakeAttempts = 15-20",
                  "RandomTrailers = on", "DisableCookies = on", "PersistentKeepalive = 25-35"):
            self.assertIn(k, text)
        self.assertNotIn("ContentPaddingAddition", text)

    def test_order_matches_app_template(self):
        text = awg.render_conf("3.1", conf_input("3.1"))
        keys = [l.split(" = ")[0] for l in conf_lines(text) if " = " in l]
        expected = ["Address", "DNS", "PrivateKey", "MTU", "Jc", "Jmin", "Jmax", "S1", "S2", "S3", "S4",
                    "H1", "H2", "H3", "H4", "I1", "HeaderProtectionKey", "RekeyAfterTime", "RekeyTimeout",
                    "RejectAfterTime", "KeepaliveTimeout", "MaxHandshakeAttempts", "RandomTrailers",
                    "DisableCookies", "PublicKey", "PresharedKey", "AllowedIPs", "Endpoint", "PersistentKeepalive"]
        self.assertEqual(keys, expected)

    def test_roundtrip_params_from_conf(self):
        for line in awg.LINES:
            text = awg.render_conf(line, conf_input(line))
            self.assertEqual(awg.params_from_conf(text), awg.normalize_params(sample_params(line)))

    def test_options(self):
        data = conf_input("2.0", host=DOMAIN, dns=("9.9.9.9",), port=51820)
        data["client"]["psk"] = ""
        data["client"]["allowed_ips"] = "10.0.0.0/8"
        data["mtu"] = "1376"
        text = awg.render_conf("2.0", data)
        self.assertIn("Endpoint = vpn.example.com:51820", text)
        self.assertIn("DNS = 9.9.9.9", text)
        self.assertIn("MTU = 1376", text)
        self.assertNotIn("PresharedKey", text)
        self.assertIn("AllowedIPs = 10.0.0.0/8", text)
        data = conf_input("1.0", host="2001:db8::10")
        self.assertIn("Endpoint = [2001:db8::10]:443", awg.render_conf("1.0", data))
        data = conf_input("1.0")
        data["dns"] = []
        self.assertNotIn("DNS", awg.render_conf("1.0", data))

    def test_missing_fields(self):
        data = conf_input("1.0")
        del data["client"]["privkey"]
        with self.assertRaises(awg.AwgError):
            awg.render_conf("1.0", data)
        data = conf_input("1.0")
        data["server"]["endpoint_host"] = ""
        with self.assertRaises(awg.AwgError):
            awg.render_conf("1.0", data)

    def test_cli_conf(self):
        code, out, err = cli("conf", "--line", "2.0", stdin=json.dumps(conf_input("2.0")))
        self.assertEqual(code, 0, err)
        self.assertEqual(out, awg.render_conf("2.0", conf_input("2.0")))
        code, _, err = cli("conf", "--line", "2.0", stdin="{}")
        self.assertEqual(code, 2)


# ---------------------------------------------------------------------------
# Профиль mihomo (подписка mihomo.yaml, docs/SUBSCRIPTION.md)
# ---------------------------------------------------------------------------

def mihomo_awg(line, data=None, name="Анна · AmneziaWG"):
    proxy = dict(awg.mihomo_proxy(line, data or conf_input(line), name))
    return proxy, dict(proxy.get("amnezia-wg-option") or [])


class TestMihomo(unittest.TestCase):
    """mihomo.yaml подписки: по линии — поля, которые mihomo этой линии понимает (wiki.metacubex.one, wireguard.go,
    образец 3X-UI buildAmneziaWGProxy), из тех же данных, что .conf; version: 3 — только у 3.1."""

    def test_common_fields_of_every_line(self):
        for line in awg.LINES:
            proxy, opt = mihomo_awg(line)
            self.assertEqual(list(proxy)[:4], ["name", "type", "server", "port"], line)
            self.assertEqual((proxy["type"], proxy["server"], proxy["port"], proxy["ip"]),
                             ("wireguard", HOST, 443, "10.8.2.5"), line)
            self.assertEqual((proxy["private-key"], proxy["public-key"], proxy["pre-shared-key"]),
                             (CLIENT_PRIV, SERVER_PUB, PSK), line)
            self.assertEqual(proxy["allowed-ips"], ["0.0.0.0/0", "::/0"])
            self.assertIs(proxy["udp"], True)
            self.assertEqual((proxy["mtu"], proxy["persistent-keepalive"]), (1280, 25), line)
            self.assertIs(proxy["remote-dns-resolve"], True)
            self.assertEqual(proxy["dns"], ["1.1.1.1", "1.0.0.1"])
            # Целые — целыми (mihomo читает их как int), h — строками: у 2.0 там диапазоны
            for key in ("jc", "jmin", "jmax", "s1", "s2"):
                self.assertIsInstance(opt[key], int, (line, key))
            self.assertEqual((opt["jc"], opt["jmin"], opt["jmax"]), (4, 10, 50))
            for key in ("h1", "h2", "h3", "h4"):
                self.assertIsInstance(opt[key], str, (line, key))

    def test_line_specific_fields(self):
        _, o10 = mihomo_awg("1.0")
        self.assertFalse(set(o10) & {"version", "s3", "s4", "i1", "header-protection-key"}, o10)
        _, o15 = mihomo_awg("1.5")
        self.assertEqual(o15["i1"], I1_SAMPLE)
        self.assertFalse(set(o15) & {"version", "s3", "s4", "header-protection-key"}, o15)
        _, o20 = mihomo_awg("2.0")
        self.assertEqual((o20["s3"], o20["s4"], o20["h1"], o20["i1"]), (20, 30, "1000000-1002000", I1_SAMPLE))
        self.assertFalse(set(o20) & {"version", "header-protection-key", "rekey-after-time", "random-trailers"}, o20)
        p31, o31 = mihomo_awg("3.1")
        self.assertEqual(o31["version"], 3)
        self.assertEqual(list(o31)[0], "version")
        self.assertEqual((o31["s1"], o31["s4"], o31["h4"]), (12, 12, "4"))
        self.assertEqual(o31["header-protection-key"], HPK)
        self.assertEqual((o31["rekey-after-time"], o31["rekey-timeout"], o31["reject-after-time"],
                          o31["keepalive-timeout"], o31["max-handshake-attempts"]),
                         ("100-120", "3-7", "150-180", "5-15", "15-20"))
        self.assertIs(o31["random-trailers"], True)
        self.assertIs(o31["disable-cookies"], True)
        self.assertNotIn("content-padding-addition", o31)
        # Диапазона keepalive у mihomo нет: 3.1 «25-35» — нижней границей, целым
        self.assertEqual(p31["persistent-keepalive"], 25)
        # 3.x-поля в params чужой линии в профиль не попадают — как и в .conf (line_filter)
        data = conf_input("2.0")
        data["params"].update({"HeaderProtectionKey": HPK, "RandomTrailers": "on"})
        self.assertFalse(set(mihomo_awg("2.0", data)[1]) & {"version", "header-protection-key", "random-trailers"})
        data = conf_input("3.1")
        data["params"].update({"RandomTrailers": "off", "DisableCookies": ""})
        _, opt = mihomo_awg("3.1", data)
        self.assertNotIn("random-trailers", opt)
        self.assertNotIn("disable-cookies", opt)

    def test_same_key_as_the_conf_of_the_line(self):
        """Служба подписок берёт у моста .conf и строит прокси из него — результат тот же, что из исходных данных."""
        for line in awg.LINES:
            text = awg.render_conf(line, conf_input(line))
            back = awg.conf_client_data(text)
            self.assertEqual(awg.render_conf(line, back), text, line)
            self.assertEqual(awg.mihomo_proxy(line, back, "x"), awg.mihomo_proxy(line, conf_input(line), "x"), line)
        data = conf_input("2.0", host=DOMAIN, dns=("9.9.9.9",), port=51820)
        data["client"].update({"psk": "", "allowed_ips": "10.0.0.0/8"})
        data["mtu"] = "1360"
        back = awg.conf_client_data(awg.render_conf("2.0", data))
        proxy = dict(awg.mihomo_proxy("2.0", back, "x"))
        self.assertEqual((proxy["server"], proxy["port"], proxy["mtu"], proxy["allowed-ips"], proxy["dns"]),
                         (DOMAIN, 51820, 1360, ["10.0.0.0/8"], ["9.9.9.9"]))
        self.assertNotIn("pre-shared-key", proxy)
        with self.assertRaises(awg.AwgError):
            awg.conf_client_data("[Interface]\nPrivateKey = %s\n" % CLIENT_PRIV)

    def test_addresses_and_dns(self):
        proxy, _ = mihomo_awg("1.0", conf_input("1.0", host="2001:db8::10"))
        self.assertEqual(proxy["server"], "2001:db8::10")          # без скобок
        data = conf_input("2.0")
        data["client"]["ip"] = "10.8.2.5/32, fd00::5/128"
        proxy, _ = mihomo_awg("2.0", data)
        self.assertEqual((proxy["ip"], proxy["ipv6"]), ("10.8.2.5", "fd00::5"))
        # DNS не адресом (имя) mihomo отвергает целиком — такой DNS не отдаём вовсе
        proxy, _ = mihomo_awg("2.0", conf_input("2.0", dns=("dns.example.com",)))
        self.assertNotIn("dns", proxy)
        self.assertNotIn("remote-dns-resolve", proxy)
        proxy, _ = mihomo_awg("2.0", conf_input("2.0", dns=()))
        self.assertNotIn("dns", proxy)

    def test_refusals(self):
        for broken in ({"client": {"privkey": ""}}, {"server": {"pubkey": ""}}, {"server": {"endpoint_host": ""}},
                       {"server": {"port": 70000}}):
            data = conf_input("2.0")
            for part, value in broken.items():
                data[part].update(value)
            with self.assertRaises(awg.AwgError, msg=broken):
                awg.mihomo_proxy("2.0", data, "x")
        data = conf_input("2.0")
        data["params"]["Jc"] = "4x"
        with self.assertRaises(awg.AwgError):
            awg.mihomo_proxy("2.0", data, "x")
        with self.assertRaises(awg.AwgError):
            awg.mihomo_proxy("2.0", conf_input("2.0"), " ")
        with self.assertRaises(awg.AwgError):
            awg.mihomo_proxy("9.9", conf_input("2.0"), "x")
        one = awg.mihomo_proxy("2.0", conf_input("2.0"), "same")
        with self.assertRaises(awg.AwgError):
            awg.mihomo_profile([one, one])
        with self.assertRaises(awg.AwgError):
            awg.mihomo_profile([awg.mihomo_proxy("2.0", conf_input("2.0"), awg.MIHOMO_GROUP)])
        with self.assertRaises(awg.AwgError):
            awg.mihomo_profile([])
        # Прокси с именем группы «Auto» при нескольких линиях — тоже отказ: группа и прокси не должны совпадать
        with self.assertRaises(awg.AwgError):
            awg.mihomo_profile([awg.mihomo_proxy("3.1", conf_input("3.1"), awg.MIHOMO_AUTO), one])
        # …а у профиля из одной линии группы «Auto» нет, и такое имя прокси законно
        self.assertIn('  - name: "Auto"', awg.mihomo_profile([awg.mihomo_proxy("3.1", conf_input("3.1"), "Auto")]))

    def test_profile_auto_group(self):
        """Несколько линий — первой в группе выбора стоит «Auto»: fallback по линиям в порядке профиля с проверкой
        доступности (приложение выбирает само); одна линия — группа выбора без «Auto»."""
        names = ["Анна · AmneziaWG 3.1", "Анна · AmneziaWG 2.0"]
        proxies = [awg.mihomo_proxy(line, conf_input(line), n) for line, n in zip(("3.1", "2.0"), names)]
        lines = awg.mihomo_profile(proxies).split("\n")
        groups = lines[lines.index("proxy-groups:"):lines.index("rules:")]
        self.assertEqual(groups, [
            "proxy-groups:", '  - name: "goAmnezia"', "    type: select", "    proxies:",
            '      - "Auto"', '      - "Анна · AmneziaWG 3.1"', '      - "Анна · AmneziaWG 2.0"',
            '  - name: "Auto"', "    type: fallback", '    url: "http://1.1.1.1/cdn-cgi/trace"',
            "    interval: 300", "    proxies:", '      - "Анна · AmneziaWG 3.1"', '      - "Анна · AmneziaWG 2.0"'])
        self.assertEqual(lines[lines.index("rules:") + 1], '  - "MATCH,goAmnezia"')
        single = awg.mihomo_profile(proxies[:1]).split("\n")
        self.assertNotIn("    type: fallback", single)
        self.assertEqual(single[single.index("proxy-groups:"):single.index("rules:")], [
            "proxy-groups:", '  - name: "goAmnezia"', "    type: select", "    proxies:", '      - "Анна · AmneziaWG 3.1"'])

    def test_profile_layout_and_escaping(self):
        names = ['Анна · AmneziaWG 3.1', 'Q "x" \\ y: #1 [a] {b} & *c', "t\tn\nc1\x85ls bom﻿", "yes"]
        proxies = [awg.mihomo_proxy(line, conf_input(line), n) for line, n in zip(("3.1", "2.0", "1.5", "1.0"), names)]
        text = awg.mihomo_profile(proxies, title="Анна\x85\x01 «тест»")
        lines = text.split("\n")
        self.assertTrue(lines[0].startswith("# goAmnezia pro"))
        self.assertEqual(lines[1], "# Анна «тест»")             # управляющие из подписи выброшены
        for want in ("mixed-port: 7890", "allow-lan: false", "mode: rule", "proxies:", "proxy-groups:",
                     '  - name: "goAmnezia"', "    type: select", "rules:", '  - "MATCH,goAmnezia"'):
            self.assertIn(want, lines)
        self.assertIn('  - name: "Анна · AmneziaWG 3.1"', lines)
        self.assertIn('  - name: "Q \\"x\\" \\\\ y: #1 [a] {b} & *c"', lines)
        self.assertIn('  - name: "t\\tn\\nc1\\u0085ls\\u2028bom\\ufeff"', lines)
        self.assertIn('      - "yes"', lines)                   # «yes» — строка, а не да
        self.assertIn('      version: 3', lines)
        self.assertEqual(text.count("version: 3"), 1)
        # Ни одного сырого управляющего знака в тексте профиля
        self.assertFalse([c for c in text if c not in "\n" and awg._yaml_unsafe(ord(c))])
        # Настоящий разбор YAML — там, где есть чем (Psych у ruby; PyYAML в продукт не тянем): Python-эмиттер
        # пишет сам, и проверять его своими же глазами мало.
        ruby = shutil.which("ruby")
        if ruby and subprocess.run([ruby, "-e", 'require "yaml"; require "json"'], capture_output=True).returncode == 0:
            script = ('require "yaml"; require "json"; d = YAML.safe_load(STDIN.read); '
                      'puts JSON.generate([d["proxies"].map { |p| p["name"] }, d["proxy-groups"][0]["proxies"], '
                      'd["rules"], d["proxies"][0]["amnezia-wg-option"]["version"], d["proxies"][1]["port"], '
                      'd["proxy-groups"][1]])')
            proc = subprocess.run([ruby, "-e", script], input=text, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            got = json.loads(proc.stdout)
            self.assertEqual(got[0][:2], names[:2])
            self.assertEqual(got[0][3], "yes")
            self.assertEqual(got[1], ["Auto"] + got[0])
            self.assertEqual(got[2:5], [["MATCH,goAmnezia"], 3, 443])
            self.assertEqual(got[5], {"name": "Auto", "type": "fallback", "url": "http://1.1.1.1/cdn-cgi/trace",
                                      "interval": 300, "proxies": got[0]})

    def test_cli_mihomo(self):
        code, out, err = cli("mihomo", "--line", "3.1", "--name", "Анна", stdin=json.dumps(conf_input("3.1")))
        self.assertEqual(code, 0, err)
        self.assertEqual(out, awg.mihomo_profile([awg.mihomo_proxy("3.1", conf_input("3.1"), "Анна")]))
        code, out, err = cli("mihomo", "--line", "2.0", stdin=json.dumps(conf_input("2.0")))
        self.assertEqual(code, 0, err)
        self.assertIn('  - name: "AmneziaWG 2.0"', out.split("\n"))
        code, _, err = cli("mihomo", "--line", "2.0", stdin="{}")
        self.assertEqual(code, 2)
        self.assertIn("awgkeys: mihomo:", err)


# ---------------------------------------------------------------------------
# awg0.conf сервера
# ---------------------------------------------------------------------------

class TestServerConf(unittest.TestCase):
    def server_input(self, line):
        return {
            "params": sample_params(line),
            "server": {"privkey": SERVER_PRIV, "subnet": "10.8.2.0/24", "port": 443},
            "peers": [{"pubkey": CLIENT_PUB, "psk": PSK, "ip": "10.8.2.1"},
                      {"pubkey": SERVER_PUB, "psk": "", "ip": "10.8.2.2/32"}],
        }

    def test_layout(self):
        for line in awg.LINES:
            text = awg.render_server_conf(line, self.server_input(line))
            head = conf_lines(text)[:4]
            self.assertEqual(head, ["[Interface]", "PrivateKey = %s" % SERVER_PRIV,
                                    "Address = 10.8.2.0/24", "ListenPort = 443"])
            self.assertEqual(text.count("[Peer]"), 2)
            self.assertIn("PublicKey = %s\nPresharedKey = %s\nAllowedIPs = 10.8.2.1/32\n" % (CLIENT_PUB, PSK), text)
            self.assertIn("PublicKey = %s\nAllowedIPs = 10.8.2.2/32\n" % SERVER_PUB, text)
            self.assertNotIn("PersistentKeepalive", text)
            self.assertNotIn("Endpoint", text)
            self.assertNotIn("DNS", text)
            for l in conf_lines(text):
                if "=" in l:
                    self.assertTrue(l.split("=", 1)[1].strip(), l)

    def test_i_lines_are_commented(self):
        for line in ("1.5", "2.0", "3.1"):
            text = awg.render_server_conf(line, self.server_input(line))
            self.assertIn("# I1 = %s\n" % I1_SAMPLE, text)
            self.assertNotRegex(text, r"(?m)^I1 = ")
            # закомментированный I1 читается обратно, и линейка определяется верно
            self.assertEqual(awg.params_from_conf(text)["I1"], I1_SAMPLE)
            self.assertEqual(awg.detect_line(awg.params_from_conf(text)), line)
        text = awg.render_server_conf("1.0", self.server_input("1.0"))
        self.assertNotIn("I1", text)

    def test_line_filtering(self):
        data = self.server_input("1.0")
        data["params"].update({"I1": I1_SAMPLE, "S3": "20", "HeaderProtectionKey": HPK})
        text = awg.render_server_conf("1.0", data)
        self.assertNotIn("I1", text)
        self.assertNotIn("S3", text)
        self.assertNotIn("HeaderProtectionKey", text)
        text = awg.render_server_conf("3.1", self.server_input("3.1"))
        self.assertIn("HeaderProtectionKey = %s" % HPK, text)
        self.assertIn("DisableCookies = on", text)

    def test_mtu_line(self):
        """MTU профиля — строкой в [Interface] сервера сразу после ListenPort; пусто — строки нет (как у приложения).
        Жалоба 29.09.2026: MTU = 1360 из подхваченного конфига пропадал при первой пересборке, интерфейс шёл с 1420."""
        for line in awg.LINES:
            data = self.server_input(line)
            data["params"]["MTU"] = "1360"
            text = awg.render_server_conf(line, data)
            self.assertEqual(conf_lines(text)[4], "MTU = 1360")
            self.assertEqual(text.count("MTU = "), 1)
            self.assertEqual(awg.params_from_conf(text)["MTU"], "1360")  # читается обратно — подхват видит то же
            data["params"]["MTU"] = ""
            self.assertNotIn("MTU", awg.render_server_conf(line, data))
        data = self.server_input("2.0")
        data["params"]["MTU"] = "13x0"
        with self.assertRaises(awg.AwgError):
            awg.render_server_conf("2.0", data)

    def test_no_peers(self):
        data = self.server_input("2.0")
        data["peers"] = []
        text = awg.render_server_conf("2.0", data)
        self.assertNotIn("[Peer]", text)
        self.assertTrue(text.endswith("\n"))

    def test_bad_subnet(self):
        data = self.server_input("2.0")
        data["server"]["subnet"] = "10.8.2.0"
        with self.assertRaises(awg.AwgError):
            awg.render_server_conf("2.0", data)

    def test_cli_server_conf(self):
        code, out, err = cli("server-conf", "--line", "3.1", stdin=json.dumps(self.server_input("3.1")))
        self.assertEqual(code, 0, err)
        self.assertEqual(out, awg.render_server_conf("3.1", self.server_input("3.1")))


# ---------------------------------------------------------------------------
# vpn:// — кодек и структура приложения
# ---------------------------------------------------------------------------

class TestVpnCodec(unittest.TestCase):
    def test_qcompress_prefix_and_zlib(self):
        data = b'{"x":1}' * 50
        blob = awg.qcompress(data)
        self.assertEqual(int.from_bytes(blob[:4], "big"), len(data))
        self.assertEqual(blob[4:6], b"\x78\xda")  # zlib, уровень 8
        self.assertEqual(zlib.decompress(blob[4:]), data)
        self.assertEqual(awg.quncompress(blob), data)
        self.assertEqual(awg.quncompress(zlib.compress(data)), data)  # сырой zlib тоже принимаем
        with self.assertRaises(awg.AwgError):
            awg.quncompress(b"\x00\x00\x00\x05garbage")

    def test_encode_format(self):
        vpn = awg.vpn_encode(vpn_input("2.0"))
        self.assertTrue(vpn.startswith("vpn://"))
        payload = vpn[6:]
        self.assertRegex(payload, r"^[A-Za-z0-9_-]+$")  # base64url без «=»
        raw = awg.b64url_decode(payload)
        text = zlib.decompress(raw[4:]).decode("utf-8")
        self.assertEqual(int.from_bytes(raw[:4], "big"), len(text.encode("utf-8")))
        top = json.loads(text)
        self.assertEqual(text, awg._json_compact(top))  # компактно и с сортировкой ключей
        self.assertEqual(vpn, awg.vpn_encode(vpn_input("2.0")))  # детерминированно

    def test_app_structure(self):
        for line in awg.LINES:
            top = awg.build_vpn_json(vpn_input(line))
            self.assertEqual(set(top), {"containers", "defaultContainer", "description", "dns1", "dns2", "hostName"})
            self.assertEqual(top["defaultContainer"], "amnezia-awg2")
            self.assertEqual(top["hostName"], HOST)
            self.assertEqual((top["dns1"], top["dns2"]), ("1.1.1.1", "1.0.0.1"))
            self.assertEqual(top["description"], "Анна — awg2")
            self.assertEqual(len(top["containers"]), 1)
            cont = top["containers"][0]
            self.assertEqual(set(cont), {"container", "awg"})
            self.assertEqual(cont["container"], "amnezia-awg2")
            a = cont["awg"]
            self.assertEqual(a["port"], "443")  # строка: fromJson читает toString()
            self.assertEqual(a["transport_proto"], "udp")
            self.assertEqual((a["subnet_address"], a["subnet_cidr"]), ("10.8.2.0", "24"))
            self.assertEqual(a.get("protocol_version", ""), awg.LINE_TO_PROTOCOL_VERSION[line])
            self.assertTrue(all(k in a for k in awg.I_KEYS))  # I1–I5 всегда, как у приложения
            for k in awg.AWG3_KEYS + ("S3", "S4"):
                self.assertEqual(k in a, bool(sample_params(line)[k]), (line, k))
            self.assertNotIn("PersistentKeepalive", a)
            self.assertNotIn("MTU", a)
            last = json.loads(a["last_config"])
            self.assertEqual(last["port"], 443)  # int: клиенты читают toInt()/getInt()
            self.assertIsInstance(last["port"], int)
            self.assertEqual(last["hostName"], HOST)
            self.assertEqual(last["client_ip"], "10.8.2.5")
            self.assertEqual(last["client_priv_key"], CLIENT_PRIV)
            self.assertEqual(last["client_pub_key"], CLIENT_PUB)
            self.assertEqual(last["clientId"], CLIENT_PUB)
            self.assertEqual(last["server_pub_key"], SERVER_PUB)
            self.assertEqual(last["psk_key"], PSK)
            self.assertEqual(last["allowed_ips"], ["0.0.0.0/0", "::/0"])
            self.assertEqual(last["mtu"], "1280")
            self.assertEqual(last["persistent_keep_alive"], "25-35" if line == "3.1" else "25")
            self.assertIn("DNS = $PRIMARY_DNS, $SECONDARY_DNS", last["config"])
            for k in awg.AWG_IFACE_KEYS + awg.AWG3_KEYS:
                self.assertEqual(last.get(k, ""), sample_params(line)[k], (line, k))
            # то, по чему приложение узнаёт свой формат (checkConfigFormat)
            text = awg._json_compact(top)
            self.assertIn("containers", text)
            self.assertIn("hostName", text)
            self.assertNotIn("userName", text)
            self.assertNotIn("password", text)

    def test_roundtrip_all_lines(self):
        for line in awg.LINES:
            data = vpn_input(line)
            dec = awg.vpn_decode(awg.vpn_encode(data))
            self.assertEqual(dec["conf_text"], data["conf_text"], line)
            self.assertEqual(dec["params"], awg.normalize_params(data["params"]), line)
            self.assertEqual(dec["line"], line)
            self.assertEqual(dec["protocol_version"], awg.LINE_TO_PROTOCOL_VERSION[line])
            self.assertEqual(dec["container"], "amnezia-awg2")
            self.assertEqual((dec["hostName"], dec["port"]), (HOST, 443))
            self.assertEqual(dec["description"], data["description"])
            self.assertEqual(dec["dns"], ["1.1.1.1", "1.0.0.1"])
            self.assertEqual(dec["subnet"], "10.8.2.0/24")
            self.assertEqual(dec["client"], {"ip": "10.8.2.5", "privkey": CLIENT_PRIV, "pubkey": CLIENT_PUB, "psk": PSK})
            self.assertEqual(dec["server"], {"pubkey": SERVER_PUB, "endpoint_host": HOST, "port": 443})
            self.assertEqual(dec["raw"], awg.build_vpn_json(data))

    def test_roundtrip_single_dns_domain_and_no_dns(self):
        data = vpn_input("2.0", host=DOMAIN, dns=("9.9.9.9",), port=51820)
        dec = awg.vpn_decode(awg.vpn_encode(data))
        self.assertEqual(dec["conf_text"], data["conf_text"])
        self.assertEqual(dec["dns"], ["9.9.9.9"])
        self.assertEqual((dec["hostName"], dec["port"]), (DOMAIN, 51820))
        last = json.loads(dec["raw"]["containers"][0]["awg"]["last_config"])
        self.assertIn("DNS = $PRIMARY_DNS\n", last["config"])
        self.assertNotIn("$SECONDARY_DNS", last["config"])
        data = vpn_input("1.0", dns=())
        top = awg.build_vpn_json(data)
        self.assertNotIn("dns1", top)
        dec = awg.vpn_decode(awg.vpn_encode(data))
        self.assertEqual(dec["conf_text"], data["conf_text"])
        self.assertEqual(dec["dns"], [])

    def test_dns_mismatch_keeps_literal_text(self):
        data = vpn_input("2.0")
        data["dns"] = ["8.8.8.8", "8.8.4.4"]  # не совпадают с DNS в тексте → плейсхолдеров нет
        top = awg.build_vpn_json(data)
        self.assertNotIn("$PRIMARY_DNS", top["containers"][0]["awg"]["last_config"])
        self.assertEqual(top["dns1"], "8.8.8.8")
        self.assertEqual(awg.vpn_decode(awg.vpn_encode(data))["conf_text"], data["conf_text"])

    def test_defaults_from_conf_text(self):
        data = vpn_input("3.1")
        for k in ("line", "params", "hostName", "port", "dns", "container", "subnet", "description"):
            del data[k]
        top = awg.build_vpn_json(data)
        self.assertEqual(top["containers"][0]["awg"]["protocol_version"], "3.1")
        self.assertEqual(top["hostName"], HOST)
        self.assertEqual(top["dns1"], "1.1.1.1")
        self.assertNotIn("description", top)
        self.assertNotIn("subnet_address", top["containers"][0]["awg"])
        dec = awg.vpn_decode(awg.vpn_encode(data))
        self.assertEqual(dec["params"], awg.normalize_params(sample_params("3.1")))
        self.assertEqual(dec["port"], 443)

    def test_app_signs_the_line_we_issue(self):
        """AWG31-01 (жалоба 05.10: «ключ 3.1 в AmneziaVPN — всегда 2.0»): как приложение подписывает версию ключа.

        Сверено с amnezia-client 5.0.1.5–5.0.3.0 и веткой dev: awgProtocolConfig.cpp (awgVersionOf, hasAwg3Markers) и
        serverDescription.cpp (getProtocolName): подпись в списке серверов — по полям last_config, без них — по полям
        объекта контейнера; « (version 3.1)», « (version 2)», « (version 1.5)» или ничего. Поле protocol_version читает
        только экран настроек (isAwg2 / isAwg3). Поля — QJsonValue::toString(): число вместо строки было бы пустым.
        Вставленный текстом .conf (extractWireGuardConfig) несёт те же поля [Interface] в last_config."""
        v3_values = ("HeaderProtectionKey", "ContentPaddingAddition", "RekeyAfterTime", "RekeyTimeout",
                     "RejectAfterTime", "KeepaliveTimeout", "MaxHandshakeAttempts")

        def app_version(obj):
            s = lambda k: obj.get(k).strip() if isinstance(obj.get(k), str) else ""  # noqa: E731
            if any(s(k) for k in v3_values) or any(s(k) and s(k).lower() != "off" for k in ("RandomTrailers", "DisableCookies")):
                return "3.1"
            if s("S3") or s("S4") or any("-" in s(k) for k in awg.H_KEYS):
                return "2"
            return "1.5" if any(s(k) for k in awg.I_KEYS) else ""

        for line in awg.LINES:
            a = awg.build_vpn_json(vpn_input(line))["containers"][0]["awg"]
            last = json.loads(a["last_config"])
            want = awg.LINE_TO_PROTOCOL_VERSION[line]
            with self.subTest(line=line):
                self.assertEqual(app_version(last), want)                  # подпись в списке серверов
                self.assertEqual(app_version(a), want)                     # запасная — по объекту контейнера
                self.assertEqual(a.get("protocol_version", ""), want)      # экран настроек
                self.assertEqual(app_version(awg.parse_conf(last["config"])[0]), want)  # .conf, вставленный текстом

    def test_unknown_container_is_coerced(self):
        top = awg.build_vpn_json(vpn_input("2.0", container="amnezia-awg3"))
        self.assertEqual(top["containers"][0]["container"], "amnezia-awg2")
        top = awg.build_vpn_json(vpn_input("1.0", container="amnezia-awg"))
        self.assertEqual(top["defaultContainer"], "amnezia-awg")

    def test_decode_app_style_indented_json(self):
        # Приложение пишет внешний JSON с отступами (toJson() по умолчанию), last_config — компактно.
        top = awg.build_vpn_json(vpn_input("3.1"))
        text = json.dumps(top, indent=4, ensure_ascii=False)
        vpn = "vpn://" + awg.b64url_encode(awg.qcompress(text.encode("utf-8")))
        dec = awg.vpn_decode(vpn)
        self.assertEqual(dec["conf_text"], vpn_input("3.1")["conf_text"])
        self.assertEqual(dec["line"], "3.1")
        # и без префикса vpn://, и с пробелами по краям
        self.assertEqual(awg.vpn_decode("  " + vpn[6:] + "\n")["line"], "3.1")

    def test_decode_errors(self):
        with self.assertRaises(awg.AwgError):
            awg.vpn_decode("vpn://AAAA")
        # Ключ vpn:// из приложения Amnezia может нести контейнер amnezia-xray:
        # это не AmneziaWG, и декодер обязан отказать, а не выдать пустой конфиг.
        blob = awg.qcompress(b'{"containers":[{"container":"amnezia-xray","xray":{}}]}')
        with self.assertRaises(awg.AwgError):
            awg.vpn_decode("vpn://" + awg.b64url_encode(blob))
        with self.assertRaises(awg.AwgError):
            awg.build_vpn_json({"conf_text": "[Interface]\nPrivateKey = x\n"})

    def test_cli_encode_decode(self):
        data = vpn_input("2.0")
        code, out, err = cli("vpn-encode", stdin=json.dumps(data, ensure_ascii=False))
        self.assertEqual(code, 0, err)
        vpn = out.strip()
        self.assertEqual(vpn, awg.vpn_encode(data))
        code, out, err = cli("vpn-decode", vpn)
        self.assertEqual(code, 0, err)
        dec = json.loads(out)
        self.assertEqual(dec["conf_text"], data["conf_text"])
        self.assertEqual(dec["params"], awg.normalize_params(data["params"]))
        code, _, err = cli("vpn-decode", "vpn://nope")
        self.assertEqual(code, 2)
        self.assertIn("awgkeys: vpn-decode:", err)


@unittest.skipUnless(os.path.exists(REF_DECODER), "эталонный декодер egress_parse.py не найден")
class TestReferenceDecoder(unittest.TestCase):
    """Наш vpn-encode разбирается эталонным декодером Kaskad (проверенным на ключах приложения)
    в те же параметры и тот же текст .conf, что и наш .conf напрямую."""

    @classmethod
    def setUpClass(cls):
        cls.ref = _load_module(REF_DECODER, "egress_parse_ref")

    def test_vpn_matches_conf_for_all_lines(self):
        for line in awg.LINES:
            data = vpn_input(line)
            rec_vpn = self.ref.awg_from_vpn(awg.vpn_encode(data))
            rec_conf = self.ref.awg_from_conf(data["conf_text"], data["description"])
            self.assertEqual(rec_vpn, rec_conf, line)
            expected = {k: v for k, v in awg.normalize_params(data["params"]).items()
                        if k in awg.AWG_IFACE_KEYS and v}
            self.assertEqual(rec_vpn["params"], expected, line)
            self.assertEqual(rec_vpn["address"], "10.8.2.5/32")
            self.assertEqual(rec_vpn["private_key"], CLIENT_PRIV)
            self.assertEqual(rec_vpn["peer"]["public_key"], SERVER_PUB)
            self.assertEqual(rec_vpn["peer"]["preshared_key"], PSK)
            self.assertEqual(rec_vpn["peer"]["endpoint"], "%s:443" % HOST)
            self.assertEqual(rec_vpn["name"], data["description"])
            # у эталона своя шкала: «2.0» — всё, где есть S3/S4 или I-пакеты, остальное «1.5»
            self.assertEqual(self.ref.awg_version(rec_vpn), "1.5" if line == "1.0" else "2.0")

    def test_reference_sees_same_conf_text(self):
        data = vpn_input("2.0")
        vpn = awg.vpn_encode(data)
        raw = awg.b64url_decode(vpn[6:])
        top = json.loads(zlib.decompress(raw[4:]))
        conf = json.loads(top["containers"][0]["awg"]["last_config"])["config"]
        conf = conf.replace("$PRIMARY_DNS", top["dns1"]).replace("$SECONDARY_DNS", top["dns2"])
        self.assertEqual(conf, data["conf_text"])

    def test_reference_extract_finds_our_key(self):
        data = vpn_input("3.1")
        lines = self.ref.extract("some text\n" + awg.vpn_encode(data) + "\n")
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("awg://"))


# ---------------------------------------------------------------------------
# Чанки QR приложения
# ---------------------------------------------------------------------------

class TestQrChunks(unittest.TestCase):
    def test_chunk_format_and_join(self):
        vpn = awg.vpn_encode(vpn_input("3.1"))
        compressed = awg.b64url_decode(vpn[6:])
        chunks = awg.qr_chunks(vpn)
        expected_count = -(-len(compressed) // 850)
        self.assertEqual(len(chunks), expected_count)
        self.assertGreaterEqual(expected_count, 2)  # ключ 3.1 не влезает в один чанк
        joined = b""
        for i, chunk in enumerate(chunks):
            self.assertRegex(chunk, r"^[A-Za-z0-9_-]+$")
            raw = awg.b64url_decode(chunk)
            magic, count, idx, length = struct.unpack(">hBBI", raw[:8])
            self.assertEqual((magic, count, idx), (1984, expected_count, i))
            self.assertEqual(raw[:2], b"\x07\xc0")
            self.assertEqual(length, len(raw) - 8)
            self.assertLessEqual(length, 850)
            joined += raw[8:]
        self.assertEqual(joined, compressed)
        # сборка в другом порядке — как при сканировании — даёт исходную строку
        self.assertEqual(awg.qr_chunks_join(list(reversed(chunks))), vpn)
        # и приложение после сборки делает qUncompress → JSON
        self.assertEqual(json.loads(awg.quncompress(joined)), awg.build_vpn_json(vpn_input("3.1")))

    def test_exact_multiple_and_small(self):
        chunks = awg.qr_chunks_from_bytes(b"a" * 1700)
        self.assertEqual(len(chunks), 2)
        self.assertEqual(len(awg.qr_chunks_from_bytes(b"a" * 1701)), 3)
        self.assertEqual(len(awg.qr_chunks_from_bytes(b"a")), 1)
        with self.assertRaises(awg.AwgError):
            awg.qr_chunks_from_bytes(b"")
        with self.assertRaises(awg.AwgError):
            awg.qr_chunks_from_bytes(b"a" * (850 * 256))

    def test_join_errors(self):
        chunks = awg.qr_chunks_from_bytes(b"x" * 2000)
        with self.assertRaises(awg.AwgError):
            awg.qr_chunks_join(chunks[:-1])
        other = awg.qr_chunks_from_bytes(b"y" * 100)
        with self.assertRaises(awg.AwgError):
            awg.qr_chunks_join(chunks + other)
        with self.assertRaises(awg.AwgError):
            awg.qr_chunks_join(["AAAA"])

    def test_cli_qr_chunks(self):
        vpn = awg.vpn_encode(vpn_input("2.0"))
        code, out, err = cli("qr-chunks", vpn)
        self.assertEqual(code, 0, err)
        chunks = json.loads(out)
        self.assertIsInstance(chunks, list)
        self.assertEqual(chunks, awg.qr_chunks(vpn))
        self.assertEqual(awg.qr_chunks_join(chunks), vpn)

    def test_cli_qr_chunks_and_decode_read_the_link_from_stdin(self):
        # SR-17: в ссылке приватный ключ, а argv виден всем в /proc — ядро передаёт её через stdin («-»).
        vpn = awg.vpn_encode(vpn_input("2.0"))
        code, out, err = cli("qr-chunks", "-", stdin=vpn + "\n")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out), awg.qr_chunks(vpn))
        code, out, err = cli("vpn-decode", "-", stdin=vpn)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["client"]["privkey"], CLIENT_PRIV)


# ---------------------------------------------------------------------------
# CLI в целом
# ---------------------------------------------------------------------------

class TestClientKey(unittest.TestCase):
    """client-key: ключ клиента из того, что ему выдали (привязка старого ключа после подхвата)."""

    def test_from_vpn_link_inside_a_message(self):
        link = awg.vpn_encode(vpn_input("2.0"))
        # Ссылку вставляют из переписки вместе с подписью — ищем её в тексте.
        got = awg.client_key("Вот твой ключ, Анна:\n" + link + "\nспасибо")
        self.assertEqual(got["privkey"], CLIENT_PRIV)
        self.assertEqual(got["pubkey"], CLIENT_PUB)
        self.assertEqual(got["server_pubkey"], SERVER_PUB)
        self.assertEqual(got["psk"], PSK)
        self.assertEqual(got["ip"], "10.8.2.5")

    def test_from_conf_text(self):
        got = awg.client_key(awg.render_conf("1.5", conf_input("1.5")))
        self.assertEqual((got["privkey"], got["pubkey"], got["server_pubkey"]), (CLIENT_PRIV, CLIENT_PUB, SERVER_PUB))

    def test_public_key_is_computed_not_trusted(self):
        # Открытый ключ считается из приватного: подложенное поле ссылки ничего не решает.
        got = awg.client_key(awg.render_conf("2.0", conf_input("2.0")).replace("[Interface]", "[Interface]\nPublicKey = " + SERVER_PUB))
        self.assertEqual(got["pubkey"], CLIENT_PUB)

    def test_garbage_is_refused(self):
        for text in ("просто текст", "[Interface]\nAddress = 10.8.2.5/32\n", "[Interface]\nPrivateKey = abc\n"):
            with self.assertRaises(awg.AwgError):
                awg.client_key(text)

    def test_cli_reads_stdin(self):
        code, out, _ = cli("client-key", stdin=awg.render_conf("2.0", conf_input("2.0")))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["pubkey"], CLIENT_PUB)
        code, _, err = cli("client-key", stdin="nothing here")
        self.assertEqual(code, 2, err)

    def test_conf_saved_by_windows_notepad_with_a_bom(self):
        # TK-1: Блокнот Windows пишет BOM в начало файла — «﻿[Interface]» раньше не открывал секцию.
        conf = awg.render_conf("2.0", conf_input("2.0"))
        got = awg.client_key("﻿" + conf.replace("\n", "\r\n"))
        self.assertEqual((got["privkey"], got["server_pubkey"]), (CLIENT_PRIV, SERVER_PUB))
        iface, peers = awg.parse_conf("﻿" + conf)
        self.assertEqual(iface.get("PrivateKey"), CLIENT_PRIV)
        self.assertEqual(len(peers), 1)

    def test_vpn_link_wrapped_over_several_lines(self):
        # TK-7: ссылку из письма (перенос по 76 знаков) или окна мессенджера раньше разбирали по первой строке.
        link = awg.vpn_encode(vpn_input("2.0"))
        wrapped = "\n".join(link[i:i + 76] for i in range(0, len(link), 76))
        got = awg.client_key("Ключ Анны:\n" + wrapped + "\nHello\nспасибо")
        self.assertEqual(got["privkey"], CLIENT_PRIV)
        # Пробелы в конце кусков (почтовый клиент) — тоже.
        got = awg.client_key(" \n".join(link[i:i + 60] for i in range(0, len(link), 60)))
        self.assertEqual(got["pubkey"], CLIENT_PUB)
        # Обрезанная ссылка по-прежнему отказ, а не молчаливая «удача».
        with self.assertRaises(awg.AwgError):
            awg.client_key(link[:len(link) // 2] + "\n" + "AAAA")


class TestCli(unittest.TestCase):
    def test_unknown_subcommand_and_help(self):
        code, _, _ = cli("no-such-command")
        self.assertNotEqual(code, 0)
        code, out, _ = cli("--help")
        self.assertEqual(code, 0)
        for sub in ("gen-params", "validate", "genkey", "pubkey", "genpsk", "conf", "server-conf",
                    "vpn-encode", "vpn-decode", "qr-chunks", "detect-line"):
            self.assertIn(sub, out)

    def test_python_version_floor(self):
        self.assertGreaterEqual(sys.version_info[:2], (3, 8))


# ---------------------------------------------------------------------------
# Модуль ядра AmneziaWG, этап А (docs/KMOD_PLAN.md): KMOD-02 (I1–I5) и MTU-01 (S4 новых 2.0)
# ---------------------------------------------------------------------------

class TestKmodStageA(unittest.TestCase):
    """Теги I1–I5 и S4 ≤ 40 у новых профилей 2.0. Теги сверены по исходникам 08.10.2026: модуль ядра v3.1.20260906
    (src/junk.c) — b c t r rc rd; amneziawg-go v3.1.20260828 (device/obf.go) — b t r rc rd d ds dz. I-пакеты шлёт клиент,
    поэтому правила зависят не от движка сервера, а от того, кто пишет: write — мы (только общие теги), adopt — подхват
    уже работающих конфигов (теги любой реализации, тег не всех — предупреждение)."""

    def problem(self, value, mode="write"):
        p = awg._ipacket_problem(value, mode)
        return p[0] if p else None

    def test_dangerous_ipackets_rejected_in_both_modes(self):
        # Принимались до KMOD-02 (проверено 08.10): <rc -5> роняет модуль ядра (#237), <rc 99999> зацикливает
        # `awg show` (#228), <x 1> модулю незнаком.
        for mode in awg.IPACKET_MODES:
            self.assertEqual(self.problem("<rc -5>", mode), "size", mode)
            self.assertEqual(self.problem("<rc 99999>", mode), "too_big", mode)
            self.assertEqual(self.problem("<rc 1000><rd 281>", mode), "too_big", mode)
            self.assertEqual(self.problem("<x 1>", mode), "unknown", mode)
            self.assertEqual(self.problem("<r -1>", mode), "size", mode)
            self.assertEqual(self.problem("<rd 0>", mode), "size", mode)
            self.assertEqual(self.problem("<wt 10>", mode), "unknown", mode)
            self.assertEqual(self.problem("<t 5>", mode), "noarg", mode)
            self.assertIsNone(self.problem("<b 0x0102><rc 8><rd 4><t><r 10>", mode), mode)
            self.assertIsNone(self.problem(I1_SAMPLE, mode), mode)
            self.assertIsNone(self.problem(awg.QUIC_I1, mode), mode)
        self.assertEqual(awg.validate_params("1.5", dict(sample_params("1.5"), I1="<rc -5>")),
                         ["I1: <rc> needs a positive byte count"])
        self.assertEqual(awg.ipacket_warnings({"I1": "<c><rc -5>"}), [], "беда важнее предупреждения")

    def test_partial_tags_refused_when_we_write_warned_when_adopting(self):
        self.assertEqual(sorted(awg.IPACKET_TAGS_COMMON), ["b", "r", "rc", "rd", "t"])
        self.assertEqual(self.problem("<c><r 5>"), "kernel_only")
        self.assertIsNone(self.problem("<c><r 5>", "adopt"))
        for v, tag in (("<b 0x01><d><r 5>", "d"), ("<b 0x01><ds><r 5>", "ds"), ("<b 0x01><dz 2><r 5>", "dz")):
            self.assertEqual(self.problem(v), "program_only", v)
            self.assertIsNone(self.problem(v, "adopt"), v)
            self.assertEqual(awg.ipacket_warnings({"I2": v}), [("I2", "program_only", tag)], v)
        self.assertEqual(awg.ipacket_warnings({"I1": "<b 0x01><c><r 5><c>", "I3": "<b 0x02><r 4>"}),
                         [("I1", "kernel_only", "c")])
        self.assertEqual(self.problem("<dz -2>", "adopt"), "size")
        self.assertEqual(self.problem("<d 5>", "adopt"), "noarg")
        self.assertEqual(self.problem("<c 5>", "adopt"), "noarg")
        # Незнакомое правило — как write, а не «пропустить всё».
        self.assertEqual(self.problem("<c>", "bogus"), "kernel_only")
        p = dict(sample_params("2.0"), I2="<c><r 5>")
        self.assertEqual(awg.validate_params("2.0", p, "adopt"), [])
        self.assertIn("I2: <c> is understood only by the kernel module; clients on the other implementation reject it",
                      awg.validate_params("2.0", p))
        # Движок сервера на теги не влияет: --engine принимается для совместимости, <c> всё равно отказ.
        for engine in awg.ENGINES:
            code, _, err = cli("validate", "--line", "2.0", "--engine", engine, stdin=json.dumps(p))
            self.assertEqual(code, 2, engine)
            self.assertIn("I2: <c> is understood only by the kernel module", err, engine)

    def test_write_needs_b_first_adopt_warns(self):
        """KEENETIC-I1: KeeneticOS 5.1.x не принимает ключ, если I начинается не с <b> («invalid I1 value»). Мы пишем
        только пакеты с <b 0x…> в начале — в каждом из I1–I5; подхват — предупреждение, без отказа."""
        bad = "<r 2><b 0x0102>"
        for k in awg.I_KEYS:
            params = dict(sample_params("2.0"), **{k: bad})
            self.assertEqual(awg.ipacket_problems(params), [(k, "b_first", "r", "")], k)
            self.assertEqual(awg.ipacket_problems(params, "adopt"), [], k)
            self.assertEqual(awg.ipacket_warnings(params), [(k, "b_first", "r")], k)
            errs = awg.validate_params("2.0", params)
            self.assertIn("%s: the packet must start with <b 0x…>: KeeneticOS 5.1 rejects a packet that starts with <r>"
                          % k, errs)
            self.assertEqual(awg.validate_params("2.0", params, "adopt"), [], k)
            code, out, _ = cli("ipackets", stdin=json.dumps(params))
            self.assertEqual((code, out.splitlines()), (2, ["%s\tb_first\tr\t-" % k]), k)
            code, out, _ = cli("ipackets", "--mode", "adopt", stdin=json.dumps(params))
            self.assertEqual((code, out.splitlines()), (0, ["%s\tb_first\tr\t-" % k]), k)
        # Прежнее умолчание приложения Amnezia — тоже отказ при записи и предупреждение при подхвате.
        self.assertEqual(self.problem(awg.LEGACY_DEFAULT_I1), "b_first")
        self.assertIsNone(self.problem(awg.LEGACY_DEFAULT_I1, "adopt"))
        # Любой первый тег, кроме <b>; <r 2> в конце — можно (форум Keenetic, тема 29439).
        for v, tag in (("<t><b 0x01>", "t"), ("<rc 4><b 0x01>", "rc"), ("<rd 4>", "rd"), ("<r 10>", "r")):
            self.assertEqual(awg._ipacket_problem(v)[:2], ("b_first", tag), v)
        self.assertIsNone(self.problem("<b 0x0102><r 2>"))
        self.assertIsNone(self.problem("<b 0x01>"))
        # Беда серьёзнее (незнакомый тег, отрицательный размер, тег не всех реализаций) — первой.
        self.assertEqual(self.problem("<r 2><x 1>"), "unknown")
        self.assertEqual(self.problem("<r -2><b 0x01>"), "size")
        self.assertEqual(self.problem("<c><b 0x01>"), "kernel_only")
        self.assertEqual(awg.ipacket_warnings({"I1": "<c><b 0x01>"}), [("I1", "kernel_only", "c"), ("I1", "b_first", "c")])

    def test_size_counts_every_tag(self):
        self.assertIsNone(self.problem("<b 0x01><rc 1000><rd 279>"))
        self.assertEqual(self.problem("<rc 1000><rd 281>"), "too_big")
        self.assertEqual(awg._ipacket_problem("<rc 1000><rd 281>")[2], "1281")
        self.assertIsNone(self.problem("<c><t><r 1272>", "adopt"))
        self.assertEqual(self.problem("<c><t><r 1273>", "adopt"), "too_big")
        self.assertIsNone(self.problem("<d><ds><dz 4><r 1276>", "adopt"))
        self.assertEqual(self.problem("<dz 1281>", "adopt"), "too_big")

    def test_ipacket_problems_and_cli(self):
        params = dict(sample_params("2.0"), I1="<rc -5>", I2="<x 1>", I3="<b 0x01><r 10>")
        self.assertEqual(awg.ipacket_problems(params), [("I1", "size", "rc", "-5"), ("I2", "unknown", "x", "")])
        code, out, _ = cli("ipackets", stdin=json.dumps(params))
        self.assertEqual(code, 2)
        self.assertEqual(out.splitlines(), ["I1\tsize\trc\t-5", "I2\tunknown\tx\t-"])
        partial = dict(params, I1="", I2="<b 0x01><d><r 3>", I4="<b 0x02><c>")
        code, out, _ = cli("ipackets", stdin=json.dumps(partial))
        self.assertEqual((code, out.splitlines()), (2, ["I2\tprogram_only\td\t-", "I4\tkernel_only\tc\t-"]))
        code, out, _ = cli("ipackets", "--mode", "adopt", "--engine", "kernel", stdin=json.dumps(partial))
        self.assertEqual((code, out.splitlines()), (0, ["I2\tprogram_only\td\t-", "I4\tkernel_only\tc\t-"]))
        code, out, _ = cli("ipackets", "--mode", "adopt", stdin=json.dumps(dict(partial, I2="<b 0x01><r 3>", I4="")))
        self.assertEqual((code, out), (0, ""))
        code, _, err = cli("validate", "--line", "2.0", "--ipackets", "adopt", stdin=json.dumps(partial))
        self.assertEqual((code, err), (0, ""))

    def test_new_2_0_s4_fits_mobile_budget(self):
        # MTU-01: внешний пакет данных 2.0 = MTU клиента 1280 + 80 (IPv6) + S4 — в бюджет пути ≈1400 байт.
        # S4-MARGIN (29.10): S4 12–24 — пакет не больше 1384, запас 16 байт; за 4000 генераций — весь диапазон.
        seen = set()
        for _ in range(4000):
            p = awg.gen_params("2.0")
            s = {k: int(p[k]) for k in awg.S_KEYS}
            self.assertTrue(12 <= s["S4"] <= 24, s)
            seen.add(s["S4"])
            self.assertLessEqual(int(p["MTU"]) + 80 + s["S4"], 1384)
            self.assertEqual(len({148 + s["S1"], 92 + s["S2"], 64 + s["S3"], 32 + s["S4"]}), 4, s)
            self.assertEqual(awg.validate_params("2.0", p), [])
        self.assertEqual(seen, set(range(12, 25)), "генератор берёт S4 из всего диапазона 12–24")

    def test_3_1_keeps_s4_at_least_12_with_header_protection(self):
        for _ in range(300):
            p = awg.gen_params("3.1")
            self.assertTrue(awg.is_key(p["HeaderProtectionKey"]))
            self.assertTrue(all(int(p[k]) >= awg.S_MIN_WITH_HEADER_PROTECTION for k in awg.S_KEYS), p)
            self.assertEqual(awg.validate_params("3.1", p), [])
        p = dict(sample_params("3.1"), S4="11")
        self.assertIn("S4 must be >= 12 when HeaderProtectionKey is set", awg.validate_params("3.1", p))


class TestS4Fix(unittest.TestCase):
    """S4-FIX (MTU-01, решение владельца 08.10): риск пакета данных выданных профилей 2.0 с большим S4 — при стандартном
    MTU 1280 (кто сам поднял MTU для проводной сети, того не пилим), новый безопасный S4 и окно MTU, которое предупреждает
    только о том, что ломается наверняка. Пакет данных = MTU + 80 + S4; 3.1 сверх этого ничего не добавляет
    (ContentPaddingAddition и RandomTrailers — только до самого большого уже отправленного пакета, amneziawg-go
    device/send.go)."""

    def risk(self, **kw):
        return awg.packet_risk(dict(sample_params("2.0"), **kw))

    def test_risk_2_0_with_large_s4(self):
        # 168, 08.10: S4 = 142 → пакет 1482 по IPv4 (1502 по IPv6), скачивание на пути 1456 — 0,0 Мбит/с.
        self.assertEqual(self.risk(S4="142"), {"packet": 1502, "budget": 1400, "s4": 142, "safe_s4": 40, "mtu": 1280})
        self.assertEqual(self.risk(S4="41")["packet"], 1401)
        self.assertIsNone(self.risk(S4="40"))
        self.assertIsNone(self.risk(S4="15"))
        self.assertIsNone(self.risk(S4="0"))

    def test_risk_ignores_the_mtu_a_person_raised(self):
        # Риск — при стандартном MTU: MTU 1420 при S4 = 30 — не повод, при S4 = 142 — риск того же размера.
        self.assertIsNone(self.risk(S4="30", MTU="1420"))
        self.assertEqual(self.risk(S4="142", MTU="1420")["packet"], 1502)
        self.assertEqual(self.risk(S4="142", MTU="")["mtu"], 1280)

    def test_no_risk_for_3_1_app_profiles_and_lines_without_s4(self):
        self.assertIsNone(awg.packet_risk(sample_params("3.1")))                       # S = 12
        self.assertIsNone(awg.packet_risk(dict(sample_params("2.0"), S1="12", S2="12", S3="12", S4="12")))  # приложение
        for line in ("1.0", "1.5"):
            self.assertIsNone(awg.packet_risk(sample_params(line)), line)
        self.assertIsNone(awg.packet_risk({"S4": "abc"}))
        self.assertIsNone(awg.packet_risk({}))
        self.assertEqual(awg.S4_SAFE_MAX, 40)
        # S4-MARGIN (29.10): генератор и «Исправить S4» — 12–24 с запасом; ручной предел и порог риска — прежние 40.
        self.assertEqual(awg.S4_GEN_RANGE, (12, 24))
        self.assertLess(awg.S4_GEN_RANGE[1], awg.S4_SAFE_MAX)
        self.assertGreaterEqual(awg.S4_GEN_RANGE[0], awg.S_MIN_WITH_HEADER_PROTECTION)

    def test_pick_safe_s4_keeps_sizes_distinct(self):
        for s3 in ("0", "8", "30", "64", ""):
            p = dict(sample_params("2.0"), S1="40", S2="60", S3=s3, S4="142")
            seen = set()
            for _ in range(300):
                s4 = awg.pick_safe_s4(p)
                seen.add(s4)
                self.assertTrue(12 <= s4 <= 24, s4)
                q = dict(p, S4=str(s4))
                sizes = [148 + 40, 92 + 60, 32 + s4] + ([64 + int(s3)] if s3 else [])
                self.assertEqual(len(set(sizes)), len(sizes), (s3, s4))
                self.assertEqual(awg.validate_params("2.0", q), [], (s3, s4))
                self.assertIsNone(awg.packet_risk(q))
            if s3 in ("0", "8"):
                # PARAM-06: 32 + S4 + 16k = 64 + S3 — класс пакетов данных размером с cookie (S3 = 0 → S4 16, 8 → 24)
                self.assertNotIn(16 if s3 == "0" else 24, seen, s3)
            # S1 = 40 и S2 = 60 исключают 12 и 24, S3 — ещё одно: свободных 10 из 13, все выпадают за 300 раз.
            self.assertGreaterEqual(len(seen), 10, "S4 выбирается случайно из всех свободных, а не одним значением")
        # 3.1 с защитой заголовков: S ≥ 12 соблюдается сам (низ — 12).
        p = dict(sample_params("3.1"), S4="200")
        self.assertGreaterEqual(awg.pick_safe_s4(p), awg.S_MIN_WITH_HEADER_PROTECTION)

    def test_mtu_warnings_only_what_surely_breaks(self):
        # L-02а (живая проверка 08.10): путь от хостера часто меньше 1500 (у Aeza — 1456), и пакет больше 1440 на части
        # хостеров и сетей не проходит. 1320 при S4 ≤ 40 — совет для проводного, без слов; ниже 1280 MTU не принимается
        # вовсе (MTU_MIN: ядро отказывает раньше, L-02б) — предупреждать не о чем.
        p20 = dict(sample_params("2.0"), S4="40")
        self.assertEqual(awg.mtu_warnings(p20, 1280), [])
        self.assertEqual(awg.mtu_warnings(p20, 1320), [])                       # 1320 + 80 + 40 = 1440 — совет
        self.assertEqual(awg.mtu_warnings(p20, 1321), ["path"])                 # 1441
        self.assertEqual(awg.mtu_warnings(p20, 1330), ["path"])
        self.assertEqual(awg.mtu_warnings(p20, 1376), ["path"])                 # прежний совет: 1496 больше пути Aeza
        self.assertEqual(awg.mtu_warnings(sample_params("1.5"), 1360), [])     # S4 нет: 1440 ровно
        self.assertEqual(awg.mtu_warnings(sample_params("1.5"), 1361), ["path"])
        self.assertEqual(awg.mtu_warnings(sample_params("3.1"), 1348), [])     # 1348 + 80 + 12 = 1440
        self.assertEqual(awg.mtu_warnings(sample_params("3.1"), 1460), ["path"])   # L-02а: 0,6 Мбит/с к клиенту
        self.assertEqual(awg.packet_size(p20, 1320), 1440)
        self.assertEqual((awg.MTU_MIN, awg.MTU_WIRED, awg.PACKET_PATH), (1280, 1320, 1440))
        # Не выше стандарта за большой пакет отвечает S4 (риск и «Исправить S4»), окно MTU про него молчит.
        big = dict(sample_params("2.0"), S4="142")
        self.assertEqual(awg.mtu_warnings(big, 1280), [])
        self.assertEqual(awg.mtu_warnings(big, 1281), ["path"])

    @staticmethod
    def size_class_hit(p):
        """PARAM-06: какой класс пакетов данных (S4 + 32 + 16k) совпадает по размеру с рукопожатием — или None."""
        s = {k: int(p[k]) for k in ("S1", "S2", "S3", "S4") if str(p.get(k) or "").isdigit()}
        for k, base in (("S1", 148), ("S2", 92), ("S3", 64)):
            if k in s:
                d = base + s[k] - (s["S4"] + 32)
                if d >= 0 and d % 16 == 0:
                    return k, d // 16
        return None

    def test_param06_generator_never_matches_handshake_sizes(self):
        # PARAMS-AUDIT §PARAM-06: у 17,6 % профилей генератора до правки класс пакетов данных совпадал с рукопожатием.
        for _ in range(10000):
            p = awg.gen_params("2.0")
            self.assertIsNone(self.size_class_hit(p), {k: p[k] for k in awg.S_KEYS})
            self.assertTrue(12 <= int(p["S4"]) <= 24, p["S4"])          # S4-MARGIN: и на 10 000 — в 12–24
        # Регрессия: прежнее правило (только точные размеры) такие наборы пропускало.
        self.assertFalse(awg.s4_size_class_free(40, 70, 20, 28))    # 148 + 40 = 188 = 28 + 32 + 16·8
        self.assertFalse(awg.s4_size_class_free(None, 70, 20, 34))  # 92 + 70 = 162 = 34 + 32 + 16·6
        self.assertFalse(awg.s4_size_class_free(None, None, 20, 36))  # 64 + 20 = 84 = 36 + 32 + 16
        self.assertTrue(awg.s4_size_class_free(40, 70, 20, 30))

    def test_param06_fix_button_never_matches_handshake_sizes(self):
        import random
        rnd = random.Random(20261008)
        for _ in range(10000):
            p = dict(sample_params("2.0"), S1=str(rnd.randint(0, 150)), S2=str(rnd.randint(0, 150)),
                     S3=rnd.choice(["", str(rnd.randint(0, 64))]), S4=str(rnd.randint(41, 150)))
            q = dict(p, S4=str(awg.pick_safe_s4(p)))
            self.assertIsNone(self.size_class_hit(q), {k: q[k] for k in awg.S_KEYS})
            self.assertTrue(12 <= int(q["S4"]) <= 24)
            # Свободных в 12–24 всегда не меньше 10 (13 остатков по модулю 16, три условия PARAM-06 — минус три).
            s = {k: (int(p[k]) if p[k] else None) for k in ("S1", "S2", "S3")}
            free = [v for v in range(12, 25) if 32 + v not in {148 + (s["S1"] or 0), 92 + (s["S2"] or 0)}
                    | ({64 + s["S3"]} if s["S3"] is not None else set())
                    and awg.s4_size_class_free(s["S1"], s["S2"], s["S3"], v)]
            self.assertGreaterEqual(len(free), 10, s)

    def test_cli(self):
        code, out, _ = cli("packet-risk", stdin=json.dumps(dict(sample_params("2.0"), S4="142")))
        self.assertEqual((code, json.loads(out)["packet"]), (0, 1502))
        code, out, _ = cli("packet-risk", stdin=json.dumps(sample_params("3.1")))
        self.assertEqual((code, out.strip()), (0, "null"))
        code, out, _ = cli("fix-s4", stdin=json.dumps(dict(sample_params("2.0"), S4="142")))
        fix = json.loads(out)
        self.assertEqual(code, 0)
        self.assertTrue(12 <= int(fix["S4"]) <= 24)
        self.assertEqual((fix["packet"], fix["budget"]), (1360 + int(fix["S4"]), 1400))
        code, _, err = cli("fix-s4", stdin=json.dumps(sample_params("2.0")))
        self.assertEqual(code, 2)
        self.assertIn("no packet risk", err)
        code, out, _ = cli("mtu-check", "--mtu", "1500", stdin=json.dumps(dict(sample_params("2.0"), S4="40")))
        self.assertEqual((code, json.loads(out)), (0, {"mtu": 1500, "s4": 40, "packet": 1620, "warnings": ["path"]}))
        code, out, _ = cli("mtu-check", "--mtu", "1320", stdin=json.dumps(dict(sample_params("2.0"), S4="40")))
        self.assertEqual((code, json.loads(out)), (0, {"mtu": 1320, "s4": 40, "packet": 1440, "warnings": []}))

    def test_bash_core_repeats_the_same_rule(self):
        # status спрашивают бот и панель на каждом экране — ядро считает риск арифметикой bash, без python
        # (ga_amn_s4_risky, lib/amnezia.sh). Правило и числа обязаны совпадать с packet_risk здесь.
        with open(os.path.join(LIB, "amnezia.sh"), encoding="utf-8") as fh:
            src = fh.read()
        consts = dict(re.findall(r"^GA_AMN_(MTU_STANDARD|PACKET_OVERHEAD|PACKET_BUDGET|S4_SAFE_MAX)=(\d+)$", src, re.M))
        self.assertEqual({k: int(v) for k, v in consts.items()},
                         {"MTU_STANDARD": awg.MTU_STANDARD, "PACKET_OVERHEAD": awg.PACKET_OVERHEAD,
                          "PACKET_BUDGET": awg.PACKET_BUDGET, "S4_SAFE_MAX": awg.S4_SAFE_MAX})
        fn = re.search(r"^ga_amn_s4_risky\(\) \{.*?^\}", src, re.M | re.S).group(0)
        values = ["", "abc", "-1", "0", "12", "39", "40", "41", "42", "100", "142", "150", "0041", "1248", "9999999999"]
        script = "\n".join("GA_AMN_%s=%s" % kv for kv in consts.items()) + "\n" + fn + \
            '\nfor v in "$@"; do if ga_amn_s4_risky "$v"; then echo 1; else echo 0; fi; done'
        proc = subprocess.run([_bash(), "-c", script, "_"] + values, capture_output=True, text=True, env=bash_env())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        got = proc.stdout.split()
        want = ["1" if awg.packet_risk({"S4": v}) is not None else "0" for v in values]
        self.assertEqual(dict(zip(values, got)), dict(zip(values, want)))
        # Минимум задания MTU (L-02б) — одно число у ядра (ga_amn_mtu_settable) и здесь (MTU_MIN).
        self.assertEqual(re.findall(r"^GA_AMN_MTU_MIN=(\d+)$", src, re.M), [str(awg.MTU_MIN)])


# ---------------------------------------------------------------------------
# bash-обёртка и словари i18n
# ---------------------------------------------------------------------------

class TestLimits1013(unittest.TestCase):
    """Пределы того, что пишем мы (1.0.13, живые проверки 08.10, .claude/work/progress/PARAMS-AUDIT.md): тайминги 3.1 и
    ContentPaddingAddition — коридор (PARAM-03), всплеск мусора Jc × Jmax ≤ 16 КБ (PARAM-08), Jc + число I ≤ 127
    (PARAM-09), хвосты 3.1 без широких H при разных S (PARAM-05, #226). Набор целиком — validate --ipackets write
    (генератор); записываемый ключ — limit_problems с ключом: только то, что запись ломает или ухудшает."""

    def p31(self, **kw):
        p = sample_params("3.1")
        p.update(kw)
        return p

    def codes(self, problems):
        return [x[0] for x in problems]

    def test_generator_stays_within_limits(self):
        for line in awg.LINES:
            for preset in awg.PRESETS:
                for _ in range(40 if line == "3.1" else 300):
                    p = awg.gen_params(line, preset)
                    self.assertEqual(awg.limit_problems(line, p), [], (line, preset))
                    self.assertEqual(awg.validate_params(line, p), [], (line, preset))
                    # PARAM-08: генератор — не больше 300 байт мусора; PARAM-09: Jc + число I — не больше 7.
                    self.assertLessEqual(int(p["Jc"]) * int(p["Jmax"]), 300)
                    self.assertLessEqual(int(p["Jc"]) + sum(1 for k in awg.I_KEYS if p[k]), 7)

    def test_corridors_and_live_values(self):
        for key, (lo, hi) in awg.AWG3_CORRIDORS.items():
            self.assertEqual(awg.limit_problems("3.1", self.p31(**{key: "%d-%d" % (lo, hi)})), [], key)
            for bad in ("%d" % (hi + 1), "%d-%d" % (lo, hi + 1)) + (("%d" % (lo - 1),) if lo > 0 else ()):
                probs = awg.limit_problems("3.1", self.p31(**{key: bad}), key, awg.AWG3_GEN[key])
                self.assertTrue(probs and probs[0] == ("corridor", key, bad, str(lo), str(hi), awg.AWG3_GEN[key]),
                                (key, bad, probs))
        # Живые цифры 08.10: поток рукопожатий, профиль не поднимается, модуль держит 4464.
        for key, bad in (("RekeyTimeout", "0-7"), ("RekeyAfterTime", "9999999999"), ("ContentPaddingAddition", "70000")):
            self.assertEqual(awg.limit_problems("3.1", self.p31(**{key: bad}), key, "")[0][:3], ("corridor", key, bad))
        # Пусто — «убрать параметр»: значение WireGuard, оно в коридоре.
        self.assertEqual(awg.limit_problems("3.1", self.p31(RekeyTimeout=""), "RekeyTimeout", "3-7"), [])

    def test_corridors_keep_the_session_key_alive(self):
        # Любое сочетание значений из коридоров — и «старый ключ — новый сервер» — держит неравенство 1 из §1б разбора:
        # RekeyAfterTime.hi < RejectAfterTime.lo − KeepaliveTimeout.lo − RekeyTimeout.lo.
        c = awg.AWG3_CORRIDORS
        self.assertLess(c["RekeyAfterTime"][1],
                        c["RejectAfterTime"][0] - c["KeepaliveTimeout"][1] - c["RekeyTimeout"][1])
        self.assertLessEqual(c["RejectAfterTime"][1], 180)          # стоковый модуль держит ключ 180 с (#227)
        self.assertGreaterEqual(c["RekeyTimeout"][0], 1)
        self.assertGreaterEqual(c["KeepaliveTimeout"][0], 1)

    def test_timing_order_only_when_the_write_breaks_it(self):
        adopted = self.p31(RekeyAfterTime="130-140")    # подхвачено вне коридора; 150 − 5 − 3 = 142 > 140
        self.assertEqual(self.codes(awg.limit_problems("3.1", adopted)), ["corridor"])
        probs = awg.limit_problems("3.1", dict(adopted, KeepaliveTimeout="15"), "KeepaliveTimeout", "5-15")
        self.assertEqual(probs, [("timing_order", "KeepaliveTimeout", "15", "132", "140", "RekeyAfterTime = 130-140 (60–120)")])
        self.assertEqual(awg.limit_problems("3.1", dict(adopted, Jc="6"), "Jc", "4"), [])
        self.assertEqual(awg.limit_problems("3.1", dict(adopted, RekeyAfterTime="100-120"), "RekeyAfterTime", "130-140"), [])

    def test_adopt_one_cause_one_warning(self):
        # Проверяльщик 15.10 (REVIEW-CORE-1013, находка 7): весь набор (подхват) — тайминг вне коридора назван строкой
        # corridor, и связка timing_order о той же причине («Верните в коридор: RejectAfterTime = 100») второй раз не
        # выводится. С ключом (profile-set-param) — как прежде: первая строка — коридор этого ключа.
        for key, bad in (("RejectAfterTime", "100"), ("RekeyAfterTime", "200"), ("KeepaliveTimeout", "40")):
            probs = awg.limit_problems("3.1", self.p31(**{key: bad}))
            self.assertEqual([(x[0], x[1]) for x in probs], [("corridor", key)], (key, probs))
        both = awg.limit_problems("3.1", self.p31(RejectAfterTime="100", RekeyAfterTime="200"))
        self.assertEqual(self.codes(both), ["corridor", "corridor"])
        keyed = awg.limit_problems("3.1", self.p31(RejectAfterTime="100"), "RejectAfterTime", "150-180")
        self.assertEqual(keyed[0][:2], ("corridor", "RejectAfterTime"))

    def test_junk_burst(self):
        p = self.p31(Jc="122", Jmin="1200", Jmax="1280")
        self.assertEqual(awg.limit_problems("3.1", p, "Jc", "4"),
                         [("junk_burst", "Jc", "122", "122", "1280", "156160", "16384")])
        self.assertEqual(awg.limit_problems("3.1", dict(p, Jc="12"), "Jc", "4"), [])         # 15360
        self.assertEqual(self.codes(awg.limit_problems("3.1", dict(p, Jc="13"), "Jc", "4")), ["junk_burst"])
        # Подхваченный с большим всплеском: уменьшать можно и по шагу, увеличивать — нет.
        self.assertEqual(awg.limit_problems("3.1", dict(p, Jmax="1250"), "Jmax", "1280"), [])
        self.assertEqual(self.codes(awg.limit_problems("3.1", p, "Jmax", "1250")), ["junk_burst"])

    def test_jc_plus_ipackets(self):
        p = self.p31(Jc="127")                      # I1 задан
        self.assertEqual(awg.limit_problems("3.1", p, "Jc", "4"), [("jc_ipackets", "Jc", "127", "127", "1", "128", "127")])
        self.assertEqual(awg.limit_problems("3.1", dict(p, Jc="126"), "Jc", "4"), [])
        q = dict(p, Jc="126", I2="<r 4>")
        self.assertEqual(self.codes(awg.limit_problems("3.1", q, "I2", "")), ["jc_ipackets"])
        self.assertEqual(awg.limit_problems("3.1", dict(q, I1=""), "I1", I1_SAMPLE), [])
        self.assertEqual(self.codes(awg.limit_problems("1.0", dict(sample_params("1.0"), Jc="128"), "Jc", "4")),
                         ["jc_ipackets"])

    def test_trailers_loss_226(self):
        wide = self.p31(H1="5-268435460", H2="268435461-268440000", H3="268440001-268450000", H4="268450001-268460000")
        self.assertEqual(awg.limit_problems("3.1", wide), [])           # S все равны — потерь нет
        probs = awg.limit_problems("3.1", dict(wide, S1="40"), "S1", "12")
        self.assertEqual(probs, [("trailers_loss", "S1", "40", "H1", "S1", "12", "268435456", "16")])
        self.assertEqual(awg.limit_problems("3.1", dict(wide, S2="30"), "S2", "12"), [])  # H2 узкий (4540)
        self.assertEqual(awg.limit_problems("3.1", dict(wide, S1="40", RandomTrailers="off"), "S1", "12"), [])
        self.assertEqual(self.codes(awg.limit_problems("3.1", dict(wide, S1="40"), "RandomTrailers", "off")),
                         ["trailers_loss"])
        narrow = self.p31(H1="1000000-1002000", H2="2000000-2003000", H3="3000000-3004000", H4="4000000-4005000",
                          S1="40", S2="70", S3="20", S4="30")
        self.assertEqual(awg.limit_problems("3.1", narrow), [])         # узкие H генератора 2.0 — ≈10⁻⁶

    def test_validate_write_and_adopt(self):
        bad = self.p31(RekeyTimeout="0-7")
        self.assertEqual(awg.validate_params("3.1", bad, "adopt"), [])
        errs = awg.validate_params("3.1", bad)
        self.assertEqual(len(errs), 1)
        self.assertIn("RekeyTimeout must be within 3..10", errs[0])

    def test_cli_limits(self):
        p = self.p31(RekeyTimeout="0-7")
        code, out, err = cli("limits", "--line", "3.1", "--key", "RekeyTimeout",
                             stdin=json.dumps({"params": p, "old": "3-7"}))
        self.assertEqual(code, 2, err)
        self.assertEqual(out, "corridor\tRekeyTimeout\t0-7\t3\t10\t3-7\n")
        code, out, err = cli("limits", "--line", "3.1", stdin=json.dumps(p))
        self.assertEqual((code, out.splitlines()), (0, ["corridor\tRekeyTimeout\t0-7\t3\t10\t3-7"]), err)
        code, out, err = cli("limits", "--line", "3.1", "--key", "Jc",
                             stdin=json.dumps({"params": self.p31(), "old": "4"}))
        self.assertEqual((code, out), (0, ""), err)
        # timing_order без партнёров вне коридора — пустое поле печатается «-»: табуляция у bash схлопывается.
        code, out, _ = cli("limits", "--line", "3.1", "--key", "RekeyAfterTime",
                           stdin=json.dumps({"params": self.p31(RekeyAfterTime="200"), "old": "100-120"}))
        self.assertEqual(code, 2)
        self.assertIn("timing_order\tRekeyAfterTime\t200\t142\t200\t-", out.splitlines())


class TestBashWrapper(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bash = _bash()
        proc = subprocess.run([cls.bash, "-c", 'echo "${BASH_VERSINFO[0]}"'], capture_output=True, text=True)
        cls.bash_major = int(proc.stdout.strip() or 0)

    def run_bash(self, script, env=None):
        full_env = bash_env()
        full_env["AWGKEYS_PY"] = AWGKEYS_PY
        if env:
            full_env.update(env)
        proc = subprocess.run([self.bash, "-c", script], capture_output=True, text=True, env=full_env)
        return proc.returncode, proc.stdout, proc.stderr

    def test_bash_n(self):
        for f in (AWG_PARAMS_SH, LANG_RU, LANG_EN):
            proc = subprocess.run([self.bash, "-n", f], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_shellcheck(self):
        sc = shutil.which("shellcheck")
        if not sc:
            self.skipTest("shellcheck не установлен")
        proc = subprocess.run([sc, "-S", "warning", AWG_PARAMS_SH, LANG_RU, LANG_EN],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_i18n_parity_and_usage(self):
        if self.bash_major < 4:
            self.skipTest("нужен bash 4+ (BASH5)")
        keys = {}
        for lang, path in (("ru", LANG_RU), ("en", LANG_EN)):
            # путь к словарю передаём отдельным аргументом, чтобы не собирать команду из строк
            proc = subprocess.run([self.bash, "-c", 'declare -A I18N; source "$1"; for k in "${!I18N[@]}"; do '
                                   'printf "%s\\t%s\\n" "$k" "${I18N[$k]}"; done', "_", path],
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            keys[lang] = dict(l.split("\t", 1) for l in proc.stdout.strip().split("\n") if l)
            self.assertTrue(keys[lang])
            for k, v in keys[lang].items():
                self.assertRegex(k, r"^[a-z0-9_]+$")
                self.assertTrue(v.strip(), k)
        self.assertEqual(set(keys["ru"]), set(keys["en"]))
        # число %s в форматах совпадает, чтобы tf не расходился между языками
        for k in keys["ru"]:
            self.assertEqual(keys["ru"][k].count("%s"), keys["en"][k].count("%s"), k)
        with open(AWG_PARAMS_SH, encoding="utf-8") as fh:
            src = fh.read()
        used = set(re.findall(r"\$\((?:t|tf) ([a-z0-9_]+)", src))
        self.assertTrue(used)
        self.assertTrue(used <= set(keys["ru"]), used - set(keys["ru"]))
        self.assertEqual(used, set(keys["ru"]), "лишние или неиспользуемые ключи")

    def test_generate_and_validate(self):
        if self.bash_major < 4:
            self.skipTest("нужен bash 4+ (BASH5)")
        script = 'source "%s"; awg_params_generate "$1" "$2"' % AWG_PARAMS_SH
        for line in awg.LINES:
            for preset in awg.PRESETS:
                proc = subprocess.run([self.bash, "-c", script, "_", line, preset],
                                      capture_output=True, text=True, env=bash_env(AWGKEYS_PY=AWGKEYS_PY))
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(proc.stderr, "")  # правило subshell-capture: в stderr ничего лишнего
                p = json.loads(proc.stdout)
                self.assertEqual(awg.validate_params(line, p), [])
        good = json.dumps(sample_params("2.0"))
        script = 'source "%s"; awg_params_validate "$1" "$2"' % AWG_PARAMS_SH
        proc = subprocess.run([self.bash, "-c", script, "_", "2.0", good], capture_output=True, text=True,
                              env=dict(os.environ, AWGKEYS_PY=AWGKEYS_PY))
        self.assertEqual((proc.returncode, proc.stdout), (0, ""), proc.stderr)
        proc = subprocess.run([self.bash, "-c", script, "_", "1.0", good], capture_output=True, text=True,
                              env=dict(os.environ, AWGKEYS_PY=AWGKEYS_PY))
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout, "")
        self.assertIn("1.0", proc.stderr)
        self.assertIn("forbids", proc.stderr)
        proc = subprocess.run([self.bash, "-c", script, "_", "7.7", good], capture_output=True, text=True,
                              env=dict(os.environ, AWGKEYS_PY=AWGKEYS_PY))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("7.7", proc.stderr)
        proc = subprocess.run([self.bash, "-c", 'source "%s"; awg_params_generate 2.0 evil' % AWG_PARAMS_SH],
                              capture_output=True, text=True, env=dict(os.environ, AWGKEYS_PY=AWGKEYS_PY))
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout, "")

    def test_keys_and_detect(self):
        if self.bash_major < 4:
            self.skipTest("нужен bash 4+ (BASH5)")
        script = ('source "%s"; priv="$(awg_keys_genkey)" || exit 9; pub="$(awg_keys_pubkey "$priv")" || exit 8; '
                  'psk="$(awg_keys_genpsk)" || exit 7; printf "%%s\\n%%s\\n%%s\\n" "$priv" "$pub" "$psk"' % AWG_PARAMS_SH)
        code, out, err = self.run_bash(script)
        self.assertEqual(code, 0, err)
        priv, pub, psk = out.strip().split("\n")
        self.assertEqual(awg.pubkey(priv), pub)
        self.assertTrue(awg.is_key(psk))
        code, out, err = self.run_bash('source "%s"; awg_keys_pubkey "short"' % AWG_PARAMS_SH)
        self.assertEqual((code, out), (1, ""))
        self.assertTrue(err.strip())
        conf = awg.render_conf("2.0", conf_input("2.0"))
        proc = subprocess.run([self.bash, "-c", 'source "%s"; awg_line_detect' % AWG_PARAMS_SH], input=conf,
                              capture_output=True, text=True, env=dict(os.environ, AWGKEYS_PY=AWGKEYS_PY))
        self.assertEqual((proc.returncode, proc.stdout.strip()), (0, "2.0"), proc.stderr)

    def test_missing_helper_reported(self):
        code, out, err = self.run_bash('source "%s"; awg_keys_genkey' % AWG_PARAMS_SH,
                                       env={"AWGKEYS_PY": "/nonexistent/awgkeys.py"})
        self.assertEqual((code, out), (1, ""))
        self.assertIn("awgkeys.py", err)

    # Мини-шим i18n как в lib/i18n.sh goTelegram: словарь RU загружен, t/tf настоящие.
    I18N_SHIM = ('declare -A I18N; source "%s"; '
                 't() { printf "%%s\\n" "${I18N[$1]:-$1}"; }; '
                 'tf() { local f="${I18N[$1]:-$1}"; shift; printf "$f" "$@"; }; '
                 'source "%s"; ' % (LANG_RU, AWG_PARAMS_SH))

    def test_translated_messages(self):
        if self.bash_major < 4:
            self.skipTest("нужен bash 4+ (BASH5)")
        code, out, err = self.run_bash(self.I18N_SHIM + 'awg_params_generate 7.7')
        self.assertEqual((code, out), (1, ""))
        self.assertIn("неизвестная линейка AmneziaWG: «7.7»", err)
        code, out, err = self.run_bash(self.I18N_SHIM + 'awg_params_generate 2.0 evil')
        self.assertIn("неизвестный пресет маскировки: «evil»", err)
        proc = subprocess.run([self.bash, "-c", self.I18N_SHIM + 'awg_params_validate 1.0 "$1"', "_",
                               json.dumps(sample_params("3.1"))], capture_output=True, text=True,
                              env=dict(os.environ, AWGKEYS_PY=AWGKEYS_PY))
        self.assertEqual((proc.returncode, proc.stdout), (1, ""))
        self.assertIn("параметры линейки 1.0 не прошли проверку:", proc.stderr)
        self.assertIn("line 1.0 forbids", proc.stderr)
        code, out, err = self.run_bash(self.I18N_SHIM + 'awg_keys_pubkey nope')
        self.assertEqual((code, out), (1, ""))
        self.assertIn("приватный ключ должен быть base64", err)
        code, out, err = self.run_bash(self.I18N_SHIM + 'awg_keys_genpsk', env={"AWGKEYS_PY": "/nonexistent/x.py"})
        self.assertEqual((code, out), (1, ""))
        self.assertIn("не найден помощник awgkeys.py", err)
        # при успехе stderr пуст, stdout — только результат
        code, out, err = self.run_bash(self.I18N_SHIM + 'awg_params_generate 3.1 quic')
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(awg.validate_params("3.1", json.loads(out)), [])


if __name__ == "__main__":
    unittest.main()
