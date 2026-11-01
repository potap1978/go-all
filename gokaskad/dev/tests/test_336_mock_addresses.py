"""Kaskad 3.3.6 (BACKLOG-3.3.6 п. 6): the mock data of the panel and the wizard (dev-mock*.js, setup/dev-*.js) hold no
real addresses — servers, keys, DNS answers, «сети России» of the mock are addresses for documentation (RFC 5737:
192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24; IPv6 — 2001:db8::/32) or private ones. The server .67 of the stand was
deleted by the owner (15.10): its address may belong to someone else now.

Allowed: the public resolvers the product's own texts name (8.8.8.8 in «например youtube.com или 8.8.8.8» — the mock
repeats the words of gw_trace) and the versions of the app «AmneziaVPN 4.8.12.9» (four numbers, not an address).
"""
# k3-select: dirs=lib,docs,tests,tools,install.sh
import glob
import ipaddress
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "lib", "kaskad", "panel", "static")
FILES = sorted(set(glob.glob(os.path.join(STATIC, "dev-*.js")) + glob.glob(os.path.join(STATIC, "setup", "dev-*.js"))))
V4 = re.compile(r"(?<![\w.])(\d{1,3}(?:\.\d{1,3}){3})(?![\w.]*\d)")
V4_DASH = re.compile(r"(?<![\w.-])(\d{1,3}(?:-\d{1,3}){3})(?=\.[a-zA-Z])")   # 45-146-88-121.sslip.io
V6 = re.compile(r"(?<![\w:])([0-9a-fA-F]{1,4}(?::[0-9a-fA-F]{0,4}){2,7})(?![\w:])")
RESOLVERS = {"1.1.1.1", "8.8.8.8", "9.9.9.9"}
VERSION = re.compile(r"AmneziaVPN\s+$")
# the servers of the stands (.67 deleted by the owner 15.10; 171.22.129.141 and 46.226.167.23 — the coordinator 16.10;
# X6 45.146.88.121, M6 89.208.113.53, 138.124.14.241 and 185.22.10.4 — REV-336-nits №6): nowhere in the code, its
# comments, the tests, the tools or the docs — their addresses may be someone's now. A test that needs a global address
# (ipcert.ops.check_ip refuses RFC 5737, the check of a subscription refuses inner ones) takes a public resolver 1.1.1.1
GONE_IP = ("94.228.166.67", "171.22.129.141", "46.226.167.23", "45.146.88.121", "89.208.113.53", "138.124.14.241",
           "185.22.10.4")
# the live servers of the stand — the owner's test1 (REV-336-2 З-2): a real address of a working server is no example
# either; the code, the tests and the docs take RFC 5737 ones or 45.10.20.x of the text rules
LIVE_IP = ("79.137.206.84",)
GONE_IP = GONE_IP + LIVE_IP
GONE = GONE_IP + tuple(a.replace(".", "-") for a in GONE_IP)
GONE_BYTES = tuple("bytes([%s])" % ", ".join(a.split(".")) for a in GONE_IP)        # the address in a CSR of a test


def real_addresses(text):
    """[address] of ``text`` that may be someone's real one: global, not a resolver, not a version of the app."""
    out = []
    for m in V4.finditer(text):
        try:
            a = ipaddress.ip_address(m.group(1))
        except ValueError:
            continue
        if not a.is_global or m.group(1) in RESOLVERS or VERSION.search(text[max(0, m.start() - 20):m.start()]):
            continue
        out.append(m.group(1))
    for m in V4_DASH.finditer(text):
        try:
            a = ipaddress.ip_address(m.group(1).replace("-", "."))
        except ValueError:
            continue
        if a.is_global and m.group(1).replace("-", ".") not in RESOLVERS:
            out.append(m.group(1))
    for m in V6.finditer(text):
        try:
            a = ipaddress.ip_address(m.group(1))
        except ValueError:
            continue
        if a.is_global:
            out.append(m.group(1))
    return out


class MockAddressesTest(unittest.TestCase):
    def test_files_found(self):
        names = [os.path.relpath(f, STATIC) for f in FILES]
        for want in ("dev-mock.js", "dev-mock-en.js", os.path.join("setup", "dev-mock-setup.js"),
                     os.path.join("setup", "dev-view-setup.js")):
            self.assertIn(want, names)

    def test_no_real_address_in_the_mocks(self):
        bad = []
        for f in FILES:
            with open(f, encoding="utf-8") as fh:
                for n, line in enumerate(fh, 1):
                    bad += ["%s:%d %s" % (os.path.relpath(f, ROOT), n, a) for a in real_addresses(line)]
        self.assertEqual(bad, [])

    def test_the_servers_that_are_gone_are_nowhere(self):
        """Every file of the tree, any type (.txt, .conf, .service, templates without an extension…): a binary one —
        with a NUL byte — is skipped; the addresses dotted, dashed (sslip.io) and as bytes of a test."""
        bad = []
        for base in ("lib", "docs", "tests", "tools", "install.sh"):
            top = os.path.join(ROOT, base)
            walk = [(ROOT, [], [base])] if os.path.isfile(top) else os.walk(top)
            for d, dirs, files in walk:
                dirs[:] = [x for x in dirs if x != "__pycache__" and not x.startswith(".")]
                for name in files:
                    p = os.path.join(d, name)
                    if os.path.abspath(p) == os.path.abspath(__file__):
                        continue
                    with open(p, "rb") as fh:
                        raw = fh.read()
                    if b"\0" in raw:
                        continue
                    text = raw.decode("utf-8", "replace")
                    bad += ["%s %s" % (os.path.relpath(p, ROOT), g) for g in GONE + GONE_BYTES if g in text]
        self.assertEqual(bad, [])

    def test_the_rule_catches_what_it_should(self):
        for s in ('var IP = "94.228.166.67";', 'where: "138.124.58.245:443"', 'aaaa: ["2606:4700:3030::6815:2001"]',
                  'host: "45-146-88-121.sslip.io"',
                  '"ozon.ru": ["185.73.193.68"]'):
            self.assertTrue(real_addresses(s), s)
        for s in ('var IP = "203.0.113.67";', '"198.51.100.45:443"', '"192.0.2.4"', '"2001:db8:5::1"', "10.77.1.20",
                  "Поставьте приложение AmneziaVPN 4.8.12.9 или новее", "например youtube.com или 8.8.8.8.",
                  "192.168.88.0/24", "127.0.0.1", "0.0.0.0", "в 12:30:45", "203-0-113-5.sslip.io", "2026-10-16-1.log"):
            self.assertEqual(real_addresses(s), [], s)


if __name__ == "__main__":
    unittest.main()
