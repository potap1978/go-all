"""Test doubles of work package 1: a small STATEFUL nft emulator, plus iptables, systemctl,
sysctl, ss and uname — wired into one FakeRunner by ``World``. Work package 4 extended the nft
emulator for the gateway (sets, maps, elements, the gw_* rules, table ip6 kaskad6).

FakeNft understands exactly the statements kaskad/nft.py and kaskad/gw/net.py emit, and nothing else
(an unknown statement is a syntax error, so a renderer slip fails the tests):

    table ip|ip6 T / add table ip|ip6 T / delete table ip|ip6 T
    table ip T { counter C {\n packets N bytes N\n }  set S { type ipv4_addr\n flags interval\n
                 elements = { ... } }  map M { type ipv4_addr : mark\n elements = { a : 0x1 } }
                 chain C { [hook spec] rules... } }   (render_full; the one-line counter form too)
    add chain ip|ip6 T C [{ type X hook Y priority N; policy accept; }] / flush chain / delete chain
    add set ip T S { type ipv4_addr; flags interval; } / add map ip T M { type ipv4_addr : mark; }
    flush set|map / delete set|map / add element ip T S { ... } / delete element ip T S { ... }
    add counter ip T C / delete counter ip T C / add rule ip|ip6 T C <rule>

Command lines: ``nft --version``, ``nft -j list table ip|ip6 T``, ``nft -j list set ip|ip6 T S``, ``nft -j list tables``,
``nft [-c] -f -`` (stdin), ``nft [-c] -f FILE``, ``nft delete table ip|ip6 T``.

It keeps the kernel's transaction rules we rely on: a script is all-or-nothing; ``-c`` never
commits; ``add`` of an existing table/chain/counter/set is a no-op (a different hook spec on an
existing chain is EEXIST); a counter or set referenced by a rule cannot be deleted (EBUSY) — but a
``flush chain`` earlier in the same script drops the reference; a chain with rules cannot be deleted
(EBUSY — stricter than kernels >= 4.x, so renderers must flush first); a map element re-added with
another value is EEXIST (as nft_add_set_elem's element clash), a missing element on delete ENOENT;
prefixes need ``flags interval`` and must not overlap; table-block contents are expanded like nft
does (objects and chains first, then rules, so ``jump`` may point forward).

``nft -j list table`` prints what real nft 1.0.6 (Debian 12) and 1.0.9 (Ubuntu 24.04) print for
the wave-1 table (tests/fixtures/nft/real-*-list.json, test_nft_apply checks the fake against them):
metainfo, table, counters, chains, sets/maps, rules. For sets/maps and the gw rules there is no real
fixture yet (verify on the stand): sets carry ``elem`` with ``{"prefix": {"addr", "len"}}`` or plain
addresses, maps ``[[addr, mark], ...]``; gw rules carry match/mangle/tproxy/drop/masquerade statements.

Failure injection: ``fail_check`` / ``fail_apply`` (error text, or fn(script) -> text or None),
``after_apply`` (fn(fake) run after a committed ``nft -f``, to tamper with the result),
``json_ok = False`` (nft built without JSON).
"""

import collections
import copy
import ipaddress
import json
import os
import re
import shlex

from kaskad import paths, state
from kaskad.sysrun import FakeRunner

ENOENT = "Could not process rule: No such file or directory"
EEXIST = "Could not process rule: File exists"
EBUSY = "Could not process rule: Device or resource busy"
EOPNOTSUPP = "Could not process rule: Operation not supported"

_HOOK = re.compile(r"^type (filter|nat) hook (prerouting|input|forward|output|postrouting) "
                   r"priority (-?\d+); policy (accept|drop);$")
_OR = r"(?:or|\|)"
_AND = r"(?:and|&)"
_HEX = r"(0x[0-9a-f]+)"
_IP = r"(\d+\.\d+\.\d+\.\d+)"
_R_JUMP = re.compile(r'^fib daddr type local iifname != "lo" jump (\w+)$')
_R_DNAT = re.compile(r'^(tcp|udp) dport (\d+) ct mark set ct mark %s %s dnat to (\d+\.\d+\.\d+\.\d+):(\d+) '
                     r'comment "([^"]*)"$' % (_OR, _HEX))
_R_GUARD = re.compile(r"^ct mark %s %s != %s return$" % (_AND, _HEX, _HEX))
_R_COUNT = re.compile(r'^meta l4proto (tcp|udp) ct original proto-dst (\d+) ct status dnat ct mark set ct mark %s %s '
                      r'counter name "(\w+)" return$' % (_OR, _HEX))
_R_MASQ = re.compile(r"^ct status dnat ct mark %s %s == %s masquerade$" % (_AND, _HEX, _HEX))
FAMILIES = ("ip", "ip6")


class NftError(Exception):
    pass


def _port(s):
    n = int(s)
    if not 1 <= n <= 65535:
        raise NftError("Error: value out of range: %s" % s)
    return n


def _masked(mask, value):
    """``ct mark and MASK <op> VALUE`` as nft -j prints it: a prefix when MASK is a prefix mask
    (real nft 1.0.6/1.0.9 print ``{"prefix": {"addr": 1258291200, "len": 8}}`` for 0xff000000)."""
    mask, value = int(mask, 16), int(value, 16)
    n = bin(mask).count("1")
    if mask == ((1 << n) - 1) << (32 - n):
        return {"ct": {"key": "mark"}}, {"prefix": {"addr": value, "len": n}}
    return {"&": [{"ct": {"key": "mark"}}, mask]}, value


# ── the gateway's rules (kaskad/gw/net.py) ─────────────────────────────────

def _m(left, right, op="=="):
    return {"match": {"op": op, "left": left, "right": right}}


_SADDR = {"payload": {"protocol": "ip", "field": "saddr"}}
_DADDR = {"payload": {"protocol": "ip", "field": "daddr"}}
_CTMARK = {"ct": {"key": "mark"}}
_MMARK = {"meta": {"key": "mark"}}
_L4 = {"meta": {"key": "l4proto"}}
_IIF = {"meta": {"key": "iifname"}}
_EST = _m({"ct": {"key": "state"}}, ["established", "related"], "in")
_RET = {"return": None}


def _hx(s):
    return int(s, 16)


_GW_RULES = [
    (r"^ct direction reply ct mark %s %s != %s meta mark set meta mark %s %s$" % (_AND, _HEX, _HEX, _OR, _HEX),
     lambda g: ([_m({"ct": {"key": "direction"}}, "reply"),
                 _m({"&": [_CTMARK, _hx(g[0])]}, _hx(g[1]), "!="),
                 {"mangle": {"key": _MMARK, "value": {"|": [_MMARK, _hx(g[2])]}}}], [])),
    (r"^ip saddr != @(\w+) return$",
     lambda g: ([_m(_SADDR, "@" + g[0], "!="), _RET], [g[0]])),
    (r"^ip daddr %s meta l4proto \{ tcp, udp \} th dport 53 ct mark set ct mark %s %s return$" % (_IP, _OR, _HEX),
     lambda g: ([_m(_DADDR, g[0]), _m(_L4, {"set": ["tcp", "udp"]}),
                 _m({"payload": {"protocol": "th", "field": "dport"}}, 53),
                 {"mangle": {"key": _CTMARK, "value": {"|": [_CTMARK, _hx(g[1])]}}}, _RET], [])),
    (r"^ip daddr %s icmp type echo-request ct mark set ct mark %s %s return$" % (_IP, _OR, _HEX),
     lambda g: ([_m(_DADDR, g[0]), _m({"payload": {"protocol": "icmp", "field": "type"}}, "echo-request"),
                 {"mangle": {"key": _CTMARK, "value": {"|": [_CTMARK, _hx(g[1])]}}}, _RET], [])),
    # SEC-3372 З-1: the own ports of the gateway's dnsmasq closed to the clients past port 53
    (r"^ip daddr %s meta l4proto \{ tcp, udp \} th dport \{ (\d+), (\d+) \} drop$" % _IP,
     lambda g: ([_m(_DADDR, g[0]), _m(_L4, {"set": ["tcp", "udp"]}),
                 _m({"payload": {"protocol": "th", "field": "dport"}}, {"set": [int(g[1]), int(g[2])]}),
                 {"drop": None}], [])),
    (r"^ip daddr @(\w+) return$",
     lambda g: ([_m(_DADDR, "@" + g[0]), _RET], [g[0]])),
    (r"^fib daddr type local return$",
     lambda g: ([_m({"fib": {"result": "type", "flags": ["daddr"]}}, "local"), _RET], [])),
    (r"^ct state established,related ct mark %s %s == %s meta l4proto udp meta mark set %s tproxy to %s:(\d+) accept$"
     % (_AND, _HEX, _HEX, _HEX, _IP),
     lambda g: ([_EST, _m({"&": [_CTMARK, _hx(g[0])]}, _hx(g[1])), _m(_L4, "udp"),
                 {"mangle": {"key": _MMARK, "value": _hx(g[2])}},
                 {"tproxy": {"family": "ip", "addr": g[3], "port": _port(g[4])}}, {"accept": None}], [])),
    (r"^ct state established,related meta mark set ct mark %s %s return$" % (_AND, _HEX),
     lambda g: ([_EST, {"mangle": {"key": _MMARK, "value": {"&": [_CTMARK, _hx(g[0])]}}}, _RET], [])),
    (r"^meta mark set ip saddr map @(\w+)$",
     lambda g: ([{"mangle": {"key": _MMARK, "value": {"map": {"key": _SADDR, "data": "@" + g[0]}}}}], [g[0]])),
    (r"^meta mark 0 meta mark set %s$" % _HEX,
     lambda g: ([_m(_MMARK, 0), {"mangle": {"key": _MMARK, "value": _hx(g[0])}}], [])),
    # wave 3, Russia directly (kaskad/gw/net.py ru_rules): concatenated and plain gw_ru, and gw_ru_dns
    (r"^ip saddr @(\w+) ip daddr \. meta l4proto @(\w+) ip daddr != @(\w+) meta mark != %s meta mark set %s$"
     % (_HEX, _HEX),
     lambda g: ([_m(_SADDR, "@" + g[0]), _m({"concat": [_DADDR, _L4]}, "@" + g[1]), _m(_DADDR, "@" + g[2], "!="),
                 _m(_MMARK, _hx(g[3]), "!="), {"mangle": {"key": _MMARK, "value": _hx(g[4])}}], [g[0], g[1], g[2]])),
    (r"^ip saddr @(\w+) ip daddr @(\w+) ip daddr != @(\w+) meta mark != %s meta mark set %s$" % (_HEX, _HEX),
     lambda g: ([_m(_SADDR, "@" + g[0]), _m(_DADDR, "@" + g[1]), _m(_DADDR, "@" + g[2], "!="),
                 _m(_MMARK, _hx(g[3]), "!="), {"mangle": {"key": _MMARK, "value": _hx(g[4])}}], [g[0], g[1], g[2]])),
    # stage 1 (§2.3, Б1): no ``meta mark != 0x2ff`` — a device blocked by choice is never in gw_rd
    (r"^ip saddr @(\w+) ip daddr \. meta l4proto @(\w+) ip daddr != @(\w+) meta mark set %s$" % _HEX,
     lambda g: ([_m(_SADDR, "@" + g[0]), _m({"concat": [_DADDR, _L4]}, "@" + g[1]), _m(_DADDR, "@" + g[2], "!="),
                 {"mangle": {"key": _MMARK, "value": _hx(g[3])}}], [g[0], g[1], g[2]])),
    (r"^ip saddr @(\w+) ip daddr @(\w+) ip daddr != @(\w+) meta mark set %s$" % _HEX,
     lambda g: ([_m(_SADDR, "@" + g[0]), _m(_DADDR, "@" + g[1]), _m(_DADDR, "@" + g[2], "!="),
                 {"mangle": {"key": _MMARK, "value": _hx(g[3])}}], [g[0], g[1], g[2]])),
    # T15 Q2 (SPEC-groups §3): Russia through the Russian group — the mark of gw_rx (kaskad/gw/net.py rx_rules)
    (r"^ip daddr \. meta l4proto @(\w+) ip daddr != @(\w+) meta mark set ip saddr map @(\w+)$",
     lambda g: ([_m({"concat": [_DADDR, _L4]}, "@" + g[0]), _m(_DADDR, "@" + g[1], "!="),
                 {"mangle": {"key": _MMARK, "value": {"map": {"key": _SADDR, "data": "@" + g[2]}}}}],
                [g[0], g[1], g[2]])),
    (r"^ip daddr @(\w+) ip daddr != @(\w+) meta mark set ip saddr map @(\w+)$",
     lambda g: ([_m(_DADDR, "@" + g[0]), _m(_DADDR, "@" + g[1], "!="),
                 {"mangle": {"key": _MMARK, "value": {"map": {"key": _SADDR, "data": "@" + g[2]}}}}],
                [g[0], g[1], g[2]])),
    (r"^ip saddr @(\w+) fib daddr type local drop$",
     lambda g: ([_m(_SADDR, "@" + g[0]), _m({"fib": {"result": "type", "flags": ["daddr"]}}, "local"),
                 {"drop": None}], [g[0]])),
    (r"^meta mark %s meta mark set numgen random mod (\d+) map \{ ((?:\d+ : %s(?:, )?)+) \}$" % (_HEX, _HEX),
     lambda g: ([_m(_MMARK, _hx(g[0])),
                 {"mangle": {"key": _MMARK, "value": {"map": {
                     "key": {"numgen": {"mode": "random", "mod": int(g[1]), "offset": 0}},
                     "data": {"set": [[int(k), _hx(v)] for k, v in re.findall(r"(\d+) : (0x[0-9a-f]+)", g[2])]}}}}}],
                [])),
    (r"^meta mark %s meta mark set %s$" % (_HEX, _HEX),
     lambda g: ([_m(_MMARK, _hx(g[0])), {"mangle": {"key": _MMARK, "value": _hx(g[1])}}], [])),
    (r"^ip daddr %s tcp dport \{ ((?:\d+(?:, )?)+) \} meta skuid != 0 reject$" % _IP,
     lambda g: ([_m(_DADDR, g[0]),
                 _m({"payload": {"protocol": "tcp", "field": "dport"}}, {"set": [int(x) for x in g[1].split(", ")]}),
                 _m({"meta": {"key": "skuid"}}, 0, "!="), {"reject": None}], [])),
    # stage 1 (§5.2, REV-s1-t5 З3): the ports of xray root alone may open — the set gw_xports
    (r"^ip daddr %s tcp dport @(\w+) meta skuid != 0 reject$" % _IP,
     lambda g: ([_m(_DADDR, g[0]), _m({"payload": {"protocol": "tcp", "field": "dport"}}, "@" + g[1]),
                 _m({"meta": {"key": "skuid"}}, 0, "!="), {"reject": None}], [g[1]])),
    (r"^ip daddr @(\w+) meta mark != %s meta mark set %s$" % (_HEX, _HEX),
     lambda g: ([_m(_DADDR, "@" + g[0]), _m(_MMARK, _hx(g[1]), "!="),
                 {"mangle": {"key": _MMARK, "value": _hx(g[2])}}], [g[0]])),
    (r"^ct mark set meta mark %s %s$" % (_OR, _HEX),
     lambda g: ([{"mangle": {"key": _CTMARK, "value": {"|": [_MMARK, _hx(g[0])]}}}], [])),
    (r"^meta mark %s meta l4proto \{ tcp, udp \} tproxy to %s:(\d+)$" % (_HEX, _IP),
     lambda g: ([_m(_MMARK, _hx(g[0])), _m(_L4, {"set": ["tcp", "udp"]}),
                 {"tproxy": {"family": "ip", "addr": g[1], "port": _port(g[2])}}], [])),
    (r"^ip saddr @(\w+) meta mark %s drop$" % _HEX,
     lambda g: ([_m(_SADDR, "@" + g[0]), _m(_MMARK, _hx(g[1])), {"drop": None}], [g[0]])),
    (r'^ip saddr @(\w+) meta mark %s %s == %s oifname != "([\w*.-]+)" drop$' % (_AND, _HEX, _HEX),
     lambda g: ([_m(_SADDR, "@" + g[0]), _m({"&": [_MMARK, _hx(g[1])]}, _hx(g[2])),
                 _m({"meta": {"key": "oifname"}}, g[3], "!="), {"drop": None}], [g[0]])),
    (r"^ip saddr @(\w+) tcp flags syn tcp option maxseg size set rt mtu$",
     lambda g: ([_m(_SADDR, "@" + g[0]), _m({"payload": {"protocol": "tcp", "field": "flags"}}, "syn", "in"),
                 {"mangle": {"key": {"tcp option": {"name": "maxseg", "field": "size"}},
                             "value": {"rt": {"key": "mtu"}}}}], [g[0]])),
    (r'^iifname "([\w*.-]+)" ip daddr @(\w+) tcp flags syn tcp option maxseg size > (\d+) tcp option maxseg size set (\d+)$',
     lambda g: ([_m(_IIF, g[0]), _m(_DADDR, "@" + g[1]), _m({"payload": {"protocol": "tcp", "field": "flags"}}, "syn", "in"),
                 _m({"tcp option": {"name": "maxseg", "field": "size"}}, int(g[2]), ">"),
                 {"mangle": {"key": {"tcp option": {"name": "maxseg", "field": "size"}}, "value": int(g[3])}}], [g[1]])),
    # §6.1: MSS by the MTU of each interface, both ways (kaskad/gw/mtu.py mss_rules)
    (r'^(iifname|oifname) "([\w*.-]+)"((?: (?:iifname|oifname) != "[\w*.-]+")*) tcp flags syn '
     r'tcp option maxseg size > (\d+) tcp option maxseg size set (\d+)$',
     lambda g: ([_m({"meta": {"key": g[0]}}, g[1])]
                + [_m({"meta": {"key": k}}, n, "!=") for k, n in re.findall(r'(iifname|oifname) != "([\w*.-]+)"', g[2] or "")]
                + [_m({"payload": {"protocol": "tcp", "field": "flags"}}, "syn", "in"),
                   _m({"tcp option": {"name": "maxseg", "field": "size"}}, int(g[3]), ">"),
                   {"mangle": {"key": {"tcp option": {"name": "maxseg", "field": "size"}}, "value": int(g[4])}}], [])),
    (r'^ip saddr @(\w+) oifname != "lo" ip daddr != @(\w+) masquerade$',
     lambda g: ([_m(_SADDR, "@" + g[0]), _m({"meta": {"key": "oifname"}}, "lo", "!="),
                 _m(_DADDR, "@" + g[1], "!="), {"masquerade": None}], [g[0], g[1]])),
    # PLAN-multi-ip Ш3 (kaskad/gw/mip.py in_rule): the inbounds only on the chosen addresses — one rule of gw_pre
    (r'^iifname != "([\w*.-]+)" ct direction original fib daddr type local meta l4proto \. th dport \{ ((?:(?:tcp|udp) \. \d+(?:-\d+)?(?:, )?)+) \}'
     r' ip daddr != \{ ((?:\d+\.\d+\.\d+\.\d+(?:, )?)+) \} drop$',
     lambda g: ([_m(_IIF, g[0], "!="), _m({"ct": {"key": "direction"}}, "original"),
                 _m({"fib": {"result": "type", "flags": ["daddr"]}}, "local"),
                 _m({"concat": [_L4, {"payload": {"protocol": "th", "field": "dport"}}]},
                    {"set": [{"concat": [pr, ({"range": [int(lo), int(hi or lo)]} if hi else int(lo))]}
                             for pr, lo, hi in re.findall(r"(tcp|udp) \. (\d+)(?:-(\d+))?", g[1])]}),
                 _m(_DADDR, {"set": g[2].split(", ")}, "!="), {"drop": None}], [])),
    # 3.3.7.15 F11 (mip.pre6_rules): «Приём» by IPv6 — the one rule of gw_pre6 of ip6 kaskad6, with the chosen IPv6 or
    # without a list (no IPv6 takes connections); one address — a plain value in ``right``, as nft -j prints a set of
    # one element (REV-3315-b1 П-5: net._daddr6_of reads both)
    (r'^iifname != "lo" iifname != "([\w*.-]+)" ct direction original fib daddr type local meta l4proto \. th dport '
     r'\{ ((?:(?:tcp|udp) \. \d+(?:-\d+)?(?:, )?)+) \}(?: ip6 daddr != \{ ((?:[0-9a-f:]+(?:, )?)+) \})? drop$',
     lambda g: ([_m(_IIF, "lo", "!="), _m(_IIF, g[0], "!="), _m({"ct": {"key": "direction"}}, "original"),
                 _m({"fib": {"result": "type", "flags": ["daddr"]}}, "local"),
                 _m({"concat": [_L4, {"payload": {"protocol": "th", "field": "dport"}}]},
                    {"set": [{"concat": [pr, ({"range": [int(lo), int(hi or lo)]} if hi else int(lo))]}
                             for pr, lo, hi in re.findall(r"(tcp|udp) \. (\d+)(?:-(\d+))?", g[1])]})]
                + ([_m({"payload": {"protocol": "ip6", "field": "daddr"}},
                       g[2] if ", " not in g[2] else {"set": g[2].split(", ")}, "!=")]
                   if g[2] else []) + [{"drop": None}], [])),
    # Ш4 (mip.post_rules): SNAT of a leg by its Endpoint; Н4 — the clients from the source of the route
    (r"^ip daddr %s udp dport (\d+) snat to %s$" % (_IP, _IP),
     lambda g: ([_m(_DADDR, g[0]), _m({"payload": {"protocol": "udp", "field": "dport"}}, _port(g[1])),
                 {"snat": {"addr": g[2]}}], [])),
    (r'^ip saddr @(\w+) oifname "([\w*.-]+)" ip daddr != @(\w+) snat to %s$' % _IP,
     lambda g: ([_m(_SADDR, "@" + g[0]), _m({"meta": {"key": "oifname"}}, g[1]),
                 _m(_DADDR, "@" + g[2], "!="), {"snat": {"addr": g[3]}}], [g[0], g[2]])),
    (r'^iifname "([\w*.-]+)" drop$',
     lambda g: ([_m({"meta": {"key": "iifname"}}, g[0]), {"drop": None}], [])),
    # F8 (3.3.7.15): gw_fwd6 — IPv6 out of IPsec (IKEv2) is not forwarded
    (r"^meta ipsec exists drop$",
     lambda g: ([_m({"meta": {"key": "ipsec"}}, True), {"drop": None}], [])),
    (r"^ip saddr != @(\w+) drop$",
     lambda g: ([_m(_SADDR, "@" + g[0], "!="), {"drop": None}], [g[0]])),
    (r"^meta mark set %s$" % _HEX,
     lambda g: ([{"mangle": {"key": _MMARK, "value": _hx(g[0])}}], [])),
    (r"^ip saddr @(\w+) ct mark %s %s == %s drop$" % (_AND, _HEX, _HEX),
     lambda g: ([_m(_SADDR, "@" + g[0]), _m({"&": [_CTMARK, _hx(g[1])]}, _hx(g[2])), {"drop": None}], [g[0]])),
    (r'^ip saddr @(\w+) ct mark %s %s == %s oifname != "([\w*.-]+)" drop$' % (_AND, _HEX, _HEX),
     lambda g: ([_m(_SADDR, "@" + g[0]), _m({"&": [_CTMARK, _hx(g[1])]}, _hx(g[2])),
                 _m({"meta": {"key": "oifname"}}, g[3], "!="), {"drop": None}], [g[0]])),
    (r"^ip daddr %s drop$" % _IP,
     lambda g: ([_m(_DADDR, g[0]), {"drop": None}], [])),
    (r"^ip saddr @(\w+) ip daddr (\d+\.\d+\.\d+\.\d+)/(\d+) drop$",
     lambda g: ([_m(_SADDR, "@" + g[0]), _m(_DADDR, {"prefix": {"addr": g[1], "len": int(g[2])}}),
                 {"drop": None}], [g[0]])),
    (r'^meta mark %s %s == %s oifname != "([\w*.-]+)" drop$' % (_AND, _HEX, _HEX),
     lambda g: ([_m({"&": [_MMARK, _hx(g[0])]}, _hx(g[1])), _m({"meta": {"key": "oifname"}}, g[2], "!="),
                 {"drop": None}], [])),
    # fail-closed, anti-spoofing, isolation (2026-10-04): the forms kaskad/gw/net.py renders
    (r'^iifname "([\w*.-]+)" return$',
     lambda g: ([_m(_IIF, g[0]), _RET], [])),
    (r'^iifname "([\w*.-]+)" ip saddr != @(\w+) drop$',
     lambda g: ([_m(_IIF, g[0]), _m(_SADDR, "@" + g[1], "!="), {"drop": None}], [g[1]])),
    (r'^iifname "([\w*.-]+)" fib saddr \. iif oif missing drop$',
     lambda g: ([_m(_IIF, g[0]), _m({"fib": {"result": "oif", "flags": ["saddr", "iif"]}}, False),
                 {"drop": None}], [])),
    (r'^ip saddr %s/(\d+)((?: iifname != "[\w*.-]+")*)( meta ipsec missing)? drop$' % _IP,
     lambda g: ([_m(_SADDR, {"prefix": {"addr": g[0], "len": int(g[1])}})]
                + [_m(_IIF, n, "!=") for n in re.findall(r'"([\w*.-]+)"', g[2] or "")]
                + ([_m({"meta": {"key": "ipsec"}}, False)] if g[3] else []) + [{"drop": None}], [])),
    (r'^ip daddr @(\w+) iifname != "([\w*.-]+)" meta ipsec missing drop$',
     lambda g: ([_m(_DADDR, "@" + g[0]), _m(_IIF, g[1], "!="), _m({"meta": {"key": "ipsec"}}, False),
                 {"drop": None}], [g[0]])),
    (r"^fib daddr type local meta mark set %s ct mark set ct mark %s %s return$" % (_HEX, _OR, _HEX),
     lambda g: ([_m({"fib": {"result": "type", "flags": ["daddr"]}}, "local"),
                 {"mangle": {"key": _MMARK, "value": _hx(g[0])}},
                 {"mangle": {"key": _CTMARK, "value": {"|": [_CTMARK, _hx(g[1])]}}}, _RET], [])),
    (r"^ip saddr @(\w+) ip daddr (!= )?@(\w+) ct mark %s %s (==|!=) %s drop$" % (_AND, _HEX, _HEX),
     lambda g: ([_m(_SADDR, "@" + g[0]), _m(_DADDR, "@" + g[2], "!=" if g[1] else "=="),
                 _m({"&": [_CTMARK, _hx(g[3])]}, _hx(g[5]), g[4]), {"drop": None}], [g[0], g[2]])),
    (r"^ip saddr @(\w+) ip daddr @(\w+) drop$",
     lambda g: ([_m(_SADDR, "@" + g[0]), _m(_DADDR, "@" + g[1]), {"drop": None}], [g[0], g[1]])),
    (r"^meta skuid (\d+) ip daddr %s/(\d+) ct direction original meta l4proto \{ tcp, udp \} th dport != (\d+) drop$"
     % _IP,
     lambda g: ([_m({"meta": {"key": "skuid"}}, int(g[0])), _m(_DADDR, {"prefix": {"addr": g[1], "len": int(g[2])}}),
                 _m({"ct": {"key": "direction"}}, "original"), _m(_L4, {"set": ["tcp", "udp"]}),
                 _m({"payload": {"protocol": "th", "field": "dport"}}, _port(g[3]), "!="), {"drop": None}], [])),
    # F2: the clients' DNS to dnsmasq's own port (kaskad/gw/net.py dns_dnat_rules, chain gw_dns); 3.3.7.2: which
    # dnsmasq — by the mark of gw_route (0x201 -> kaskad-gw-dnsdirect), the mark back to 0 with the DNAT
    (r"^ip saddr @(\w+) ip daddr %s meta l4proto \{ tcp, udp \} th dport (\d+) meta mark set ip saddr map @(\w+)$"
     % _IP,
     lambda g: ([_m(_SADDR, "@" + g[0]), _m(_DADDR, g[1]), _m(_L4, {"set": ["tcp", "udp"]}),
                 _m({"payload": {"protocol": "th", "field": "dport"}}, _port(g[2])),
                 {"mangle": {"key": _MMARK, "value": {"map": {"key": _SADDR, "data": "@" + g[3]}}}}], [g[0], g[3]])),
    (r"^ip saddr @(\w+) ip daddr %s meta mark %s (tcp|udp) dport (\d+) meta mark set %s dnat to %s:(\d+)$"
     % (_IP, _HEX, _HEX, _IP),
     lambda g: ([_m(_SADDR, "@" + g[0]), _m(_DADDR, g[1]), _m(_MMARK, _hx(g[2])),
                 _m({"payload": {"protocol": g[3], "field": "dport"}}, _port(g[4])),
                 {"mangle": {"key": _MMARK, "value": _hx(g[5])}},
                 {"dnat": {"addr": g[6], "port": _port(g[7])}}], [g[0]])),
    (r"^ip saddr @(\w+) ip daddr %s (tcp|udp) dport (\d+) meta mark set %s dnat to %s:(\d+)$" % (_IP, _HEX, _IP),
     lambda g: ([_m(_SADDR, "@" + g[0]), _m(_DADDR, g[1]), _m({"payload": {"protocol": g[2], "field": "dport"}},
                                                                 _port(g[3])),
                 {"mangle": {"key": _MMARK, "value": _hx(g[4])}},
                 {"dnat": {"addr": g[5], "port": _port(g[6])}}], [g[0]])),
    (r"^ip saddr @(\w+) ip daddr %s (tcp|udp) dport (\d+) dnat to %s:(\d+)$" % (_IP, _IP),
     lambda g: ([_m(_SADDR, "@" + g[0]), _m(_DADDR, g[1]), _m({"payload": {"protocol": g[2], "field": "dport"}},
                                                                 _port(g[3])),
                 {"dnat": {"addr": g[4], "port": _port(g[5])}}], [g[0]])),
    (r"^meta skuid (\d+) ip6 daddr ::1 ct direction original meta l4proto \{ tcp, udp \} th dport != (\d+) drop$",
     lambda g: ([_m({"meta": {"key": "skuid"}}, int(g[0])), _m({"payload": {"protocol": "ip6", "field": "daddr"}}, "::1"),
                 _m({"ct": {"key": "direction"}}, "original"), _m(_L4, {"set": ["tcp", "udp"]}),
                 _m({"payload": {"protocol": "th", "field": "dport"}}, _port(g[1]), "!="), {"drop": None}], [])),
]
_GW_RULES = [(re.compile(rx), fn) for rx, fn in _GW_RULES]


def compile_rule(text):
    """Rule text -> (expr list as ``nft -j`` prints it, comment or None, refs dict)."""
    m = _R_JUMP.match(text)
    if m:
        return ([{"match": {"op": "==", "left": {"fib": {"result": "type", "flags": ["daddr"]}}, "right": "local"}},
                 {"match": {"op": "!=", "left": {"meta": {"key": "iifname"}}, "right": "lo"}},
                 {"jump": {"target": m.group(1)}}], None, {"jump": m.group(1)})
    m = _R_DNAT.match(text)
    if m:
        proto, port, mark, addr, to_port, comment = m.groups()
        return ([{"match": {"op": "==", "left": {"payload": {"protocol": proto, "field": "dport"}},
                            "right": _port(port)}},
                 {"mangle": {"key": {"ct": {"key": "mark"}},
                             "value": {"|": [{"ct": {"key": "mark"}}, int(mark, 16)]}}},
                 {"dnat": {"addr": addr, "port": _port(to_port)}}], comment, {})
    m = _R_GUARD.match(text)
    if m:
        left, right = _masked(*m.groups())
        return ([{"match": {"op": "!=", "left": left, "right": right}},
                 {"return": None}], None, {})
    m = _R_COUNT.match(text)
    if m:
        proto, port, mark, name = m.groups()
        return ([{"match": {"op": "==", "left": {"meta": {"key": "l4proto"}}, "right": proto}},
                 {"match": {"op": "==", "left": {"ct": {"key": "proto-dst", "dir": "original"}},
                            "right": _port(port)}},
                 {"match": {"op": "in", "left": {"ct": {"key": "status"}}, "right": "dnat"}},
                 {"mangle": {"key": {"ct": {"key": "mark"}},
                             "value": {"|": [{"ct": {"key": "mark"}}, int(mark, 16)]}}},
                 {"counter": name},
                 {"return": None}], None, {"counter": name})
    m = _R_MASQ.match(text)
    if m:
        left, right = _masked(*m.groups())
        return ([{"match": {"op": "in", "left": {"ct": {"key": "status"}}, "right": "dnat"}},
                 {"match": {"op": "==", "left": left, "right": right}},
                 {"masquerade": None}], None, {})
    for rx, fn in _GW_RULES:
        m = rx.match(text)
        if m:
            expr, sets = fn(m.groups())
            return expr, None, {"sets": sets}
    raise NftError("syntax error, unexpected rule: %s" % text)


def _depth(line):
    d = 0
    quoted = False
    for ch in line:
        if ch == '"':
            quoted = not quoted
        elif not quoted and ch == "{":
            d += 1
        elif not quoted and ch == "}":
            d -= 1
    return d


def _block(lines, i):
    """Lines of a ``{`` block opened on the previous line, up to its ``}``; returns (body, next index)."""
    depth = 1
    body = []
    while i < len(lines):
        line = lines[i]
        i += 1
        depth += _depth(line)
        if depth == 0:
            if line.strip() != "}":
                raise NftError("syntax error, unexpected %r" % line.strip())
            return body, i
        body.append(line)
    raise NftError("syntax error, unexpected end of file")


def _elements(text):
    """``a, b`` or ``a : 0x1, b : 0x2`` (inside braces) -> [(key, value or None)]."""
    out = []
    for part in [p.strip() for p in text.split(",") if p.strip()]:
        if ":" in part:
            k, v = [x.strip() for x in part.split(":", 1)]
            try:
                val = int(v, 16) if v.lower().startswith("0x") else int(v)
            except ValueError:
                raise NftError("syntax error, unexpected %r" % v)
            out.append((k, val))
        else:
            out.append((part, None))
    return out


class FakeNft(object):
    RELEASE = "Lester Gooch #5"

    def __init__(self, version="1.0.6"):
        self.version = version
        self.json_ok = True
        self.tables = collections.OrderedDict()          # family ip
        self.tables6 = collections.OrderedDict()         # family ip6
        self.fail_check = None
        self.fail_apply = None
        self.after_apply = None
        self.checked = []
        self.applied = []
        self.set_listings = []                           # ``nft -j list set``: which sets were read alone
        self._h = 0

    def _fam(self, family, work=None):
        if family not in FAMILIES:
            raise NftError("syntax error, unexpected family %r" % family)
        if work is not None:
            return work[family]
        return self.tables if family == "ip" else self.tables6

    # ── command line ────────────────────────────────────────────────────
    def __call__(self, argv, input):
        args = list(argv[1:])
        try:
            if args == ["--version"]:
                return 0, "nftables v%s (%s)\n" % (self.version, self.RELEASE), ""
            if args[:1] == ["-j"]:
                if not self.json_ok:
                    return 1, "", "Error: JSON support not compiled-in\n"
                if args[1:] == ["list", "tables"]:
                    return 0, self.json_tables(), ""
                if args[1:3] == ["list", "table"] and len(args) == 5 and args[3] in FAMILIES:
                    tables = self._fam(args[3])
                    if args[4] not in tables:
                        return 1, "", "Error: No such file or directory\nlist table %s %s\n^^^^^^^^^^^^^^^^^^^^^\n" % (
                            args[3], args[4])
                    return 0, self.json_table(args[4], args[3]), ""
                if args[1:3] == ["list", "set"] and len(args) == 6 and args[3] in FAMILIES:
                    t = self._fam(args[3]).get(args[4])
                    if t is None or args[5] not in t["sets"] or t["sets"][args[5]]["kind"] != "set":
                        # nft's «list set» refuses a map (REV-s1-q2 З-2, порча peek_kind)
                        return 1, "", "Error: No such file or directory\nlist set %s %s %s\n" % tuple(args[3:6])
                    self.set_listings.append(args[5])
                    return 0, self.json_set(args[4], args[5], args[3]), ""
                got = self.json_list(args[1:])          # F2 (R-perf): chains, counters of a table, one set/map
                if got is not None:
                    return got
            if args[:2] == ["delete", "table"] and len(args) == 4 and args[2] in FAMILIES:
                tables = self._fam(args[2])
                if args[3] not in tables:
                    return 1, "", "Error: %s\ndelete table %s %s\n^^^^^^^^^^^^^^^^^^^^^^^\n" % (ENOENT, args[2], args[3])
                del tables[args[3]]
                return 0, "", ""
            commit = True
            if args[:1] == ["-c"]:
                commit = False
                args = args[1:]
            if len(args) == 2 and args[0] == "-f":
                if args[1] == "-":
                    text = input or ""
                else:
                    try:
                        with open(args[1], encoding="utf-8") as f:
                            text = f.read()
                    except (OSError, IOError):
                        return 1, "", 'Error: Could not open file "%s": No such file or directory\n' % args[1]
                return self.run_script(text, commit)
        except NftError as e:
            return 1, "", "Error: %s\n" % e
        return 1, "", "Error: fake nft does not understand %r\n" % (argv,)

    def run_script(self, text, commit):
        (self.applied if commit else self.checked).append(text)
        fail = self.fail_apply if commit else self.fail_check
        if callable(fail):
            fail = fail(text)
        if fail:
            return 1, "", fail
        work = {"ip": copy.deepcopy(self.tables), "ip6": copy.deepcopy(self.tables6)}
        try:
            self._exec(work, text)
        except NftError as e:
            return 1, "", "/dev/stdin: Error: %s\n" % e
        if commit:
            self.tables = work["ip"]
            self.tables6 = work["ip6"]
            if self.after_apply:
                self.after_apply(self)
        return 0, "", ""

    # ── script interpreter ──────────────────────────────────────────────
    def _handle(self):
        self._h += 1
        return self._h

    def _table(self, work, family, name, create=False):
        tables = work[family]
        if name not in tables:
            if not create:
                raise NftError(ENOENT)
            tables[name] = {"handle": self._handle(), "counters": collections.OrderedDict(),
                            "chains": collections.OrderedDict(), "sets": collections.OrderedDict()}
        return tables[name]

    def _chain(self, t, name, hook):
        ch = t["chains"].get(name)
        if ch is not None:
            if hook is not None and ch["hook"] != hook:
                raise NftError(EEXIST)
            return ch
        if hook and hook["type"] == "nat" and hook["prio"] <= -200:
            raise NftError(EOPNOTSUPP)
        ch = {"handle": self._handle(), "hook": hook, "rules": []}
        t["chains"][name] = ch
        return ch

    def _counter(self, t, name, packets=0, nbytes=0):
        if name not in t["counters"]:
            t["counters"][name] = {"handle": self._handle(), "packets": packets, "bytes": nbytes}

    def _set(self, t, name, kind, spec):
        """spec: {"type", "map", "flags"}; existing with the same spec -> no-op, else EEXIST."""
        s = t["sets"].get(name)
        if s is not None:
            if (s["kind"], s["type"], s["map"], s["flags"]) != (kind, spec["type"], spec["map"], spec["flags"]):
                raise NftError(EEXIST)
            return s
        s = {"handle": self._handle(), "kind": kind, "type": spec["type"], "map": spec["map"],
             "flags": spec["flags"], "timeout": spec.get("timeout"), "auto_merge": spec.get("auto_merge", False),
             "elems": collections.OrderedDict()}
        t["sets"][name] = s
        return s

    @staticmethod
    def _set_spec(kind, text):
        """``type ipv4_addr; flags interval;`` / ``type ipv4_addr : mark;`` (also one item per line)."""
        items = [x.strip() for x in re.split(r"[;\n]", text) if x.strip()]
        spec = {"type": None, "map": None, "flags": [], "timeout": None, "auto_merge": False}
        for it in items:
            m = re.match(r"^type (ipv4_addr \. inet_proto|\w+)(?: : (\w+))?$", it)
            if m:
                spec["type"], spec["map"] = m.group(1), m.group(2)
                continue
            m = re.match(r"^flags (\w+(?:\s*,\s*\w+)*)$", it)
            if m:
                spec["flags"] = [f.strip() for f in m.group(1).split(",")]
                continue
            m = re.match(r"^timeout (\d+[smhd])$", it)
            if m:
                spec["timeout"] = m.group(1)
                continue
            if it == "auto-merge":
                spec["auto_merge"] = True
                continue
            if it.startswith("elements = "):
                continue
            raise NftError("syntax error, unexpected %r" % it)
        concat = spec["type"] == "ipv4_addr . inet_proto"
        if spec["type"] not in ("ipv4_addr", "mark", "ipv4_addr . inet_proto", "inet_service") or \
                (kind == "map") != (spec["map"] is not None) or (spec["map"] not in (None, "mark")) or \
                spec["flags"] not in ([], ["interval"], ["timeout"]) or (concat and spec["flags"] != ["interval"]) or \
                (spec["timeout"] is not None and spec["flags"] != ["timeout"]) or \
                (spec["auto_merge"] and spec["flags"] != ["interval"]) or (spec["type"] in ("mark", "inet_service") and spec["flags"]):
            raise NftError("syntax error in %s definition: %r" % (kind, text))
        return spec

    @staticmethod
    def _addr_part(s, key):
        """An address, a prefix (strict) or a range ``a-b`` of an ipv4_addr key -> canonical text."""
        if "/" in key or "-" in key:
            if "interval" not in s["flags"]:
                raise NftError("Set member cannot be prefix, missing interval flag")
            if "-" in key:
                a, b = [ipaddress.IPv4Address(x.strip()) for x in key.split("-", 1)]
                if int(b) < int(a):
                    raise NftError("Range has zero or negative size")
                return "%s-%s" % (a, b)
            return str(ipaddress.IPv4Network(key))           # strict by default
        return str(ipaddress.IPv4Address(key))

    def _key(self, s, key):
        try:
            if s["type"] == "mark":
                return "0x%x" % int(key, 0)
            if s["type"] == "inet_service":                    # a port (stage 1: gw_xports)
                if not re.match(r"^\d+$", key) or not 0 <= int(key) <= 65535:
                    raise NftError("syntax error, unexpected %r" % key)
                return str(int(key))
            if s["type"] == "ipv4_addr . inet_proto":
                left, sep, right = key.partition(" . ")
                m = re.match(r"^(\d+)(?:-(\d+))?$", right.strip())
                if not sep or not m:
                    raise NftError("syntax error, unexpected %r" % key)
                lo, hi = int(m.group(1)), int(m.group(2) if m.group(2) is not None else m.group(1))
                if not 0 <= lo <= hi <= 255:
                    raise NftError("Range has zero or negative size")
                return "%s . %s" % (self._addr_part(s, left.strip()), ("%d-%d" % (lo, hi)) if lo != hi else str(lo))
            return self._addr_part(s, key)
        except ValueError:
            raise NftError("syntax error, unexpected %r" % key)

    @staticmethod
    def _span(key):
        """(first, last) addresses of an ipv4 key part (address, prefix or range)."""
        if "-" in key:
            a, b = key.split("-", 1)
            return int(ipaddress.IPv4Address(a)), int(ipaddress.IPv4Address(b))
        n = ipaddress.IPv4Network(key)
        return int(n.network_address), int(n.broadcast_address)

    def _overlap(self, s, k1, k2):
        if s["type"] == "ipv4_addr . inet_proto":
            a1, p1 = k1.split(" . ")
            a2, p2 = k2.split(" . ")
            r1 = [int(x) for x in p1.split("-")] * (1 if "-" in p1 else 2)
            r2 = [int(x) for x in p2.split("-")] * (1 if "-" in p2 else 2)
            x1, y1 = self._span(a1)
            x2, y2 = self._span(a2)
            return x1 <= y2 and x2 <= y1 and r1[0] <= r2[1] and r2[0] <= r1[1]
        x1, y1 = self._span(k1)
        x2, y2 = self._span(k2)
        return x1 <= y2 and x2 <= y1

    def _add_elems(self, s, elems):
        for k, v in elems:
            key = self._key(s, k)
            if (s["kind"] == "map") != (v is not None):
                raise NftError("syntax error: element %r does not fit %s" % (k, s["kind"]))
            if key in s["elems"]:
                if s["elems"][key] != v:
                    raise NftError(EEXIST)
                continue
            if "interval" in s["flags"]:
                for other in s["elems"]:
                    if self._overlap(s, other, key):
                        raise NftError("Could not process rule: File exists (conflicting intervals specified)")
            s["elems"][key] = v

    def _del_elems(self, s, elems):
        for k, _ in elems:
            key = self._key(s, k)
            if key not in s["elems"]:
                raise NftError(ENOENT)
            del s["elems"][key]

    def _add_rule(self, t, ch, text):
        expr, comment, refs = compile_rule(text)
        if refs.get("counter") and refs["counter"] not in t["counters"]:
            raise NftError(ENOENT)
        if refs.get("jump") and refs["jump"] not in t["chains"]:
            raise NftError(ENOENT)
        for s in refs.get("sets") or []:
            if s not in t["sets"]:
                raise NftError(ENOENT)
        ch["rules"].append({"handle": self._handle(), "text": text, "expr": expr, "comment": comment, "refs": refs})

    @staticmethod
    def _hook(spec):
        m = _HOOK.match(spec.strip())
        if not m:
            raise NftError("syntax error, unexpected %r" % spec)
        return {"type": m.group(1), "hook": m.group(2), "prio": int(m.group(3)), "policy": m.group(4)}

    def _exec(self, work, text):
        lines = text.split("\n")
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            i += 1
            if not line or line.startswith("#"):
                continue
            m = re.match(r"^table (ip|ip6) (\w+) \{$", line)
            if m:
                body, i = _block(lines, i)
                self._table_block(work, m.group(1), m.group(2), body)
                continue
            self._cmd(work, line)

    def _table_block(self, work, family, name, body):
        t = self._table(work, family, name, create=True)
        counters, chains, sets = [], [], []
        j = 0
        while j < len(body):
            line = body[j].strip()
            j += 1
            if not line or line.startswith("#"):
                continue
            m = re.match(r"^counter (\w+) \{ packets (\d+) bytes (\d+) \}$", line)
            if m:
                counters.append((m.group(1), int(m.group(2)), int(m.group(3))))
                continue
            m = re.match(r"^counter (\w+) \{$", line)          # the multi-line form nft itself prints
            if m:
                cbody, j = _block(body, j)
                vals = [x.strip() for x in cbody if x.strip()]
                vm = re.match(r"^packets (\d+) bytes (\d+)$", vals[0]) if len(vals) == 1 else None
                if not vm:
                    raise NftError("syntax error in counter %s" % m.group(1))
                counters.append((m.group(1), int(vm.group(1)), int(vm.group(2))))
                continue
            m = re.match(r"^(set|map) (\w+) \{$", line)
            if m:
                sbody, j = _block(body, j)
                lines = [x.strip() for x in sbody if x.strip()]
                elems = []
                for x in lines:
                    em = re.match(r"^elements = \{ (.*) \}$", x)
                    if em:
                        elems = _elements(em.group(1))
                spec = self._set_spec(m.group(1), "\n".join(x for x in lines if not x.startswith("elements")))
                sets.append((m.group(1), m.group(2), spec, elems))
                continue
            m = re.match(r"^chain (\w+) \{$", line)
            if m:
                cbody, j = _block(body, j)
                hook, rules = None, []
                for cl in (x.strip() for x in cbody):
                    if not cl:
                        continue
                    if cl.startswith("type ") and hook is None and not rules:
                        hook = self._hook(cl)
                    else:
                        rules.append(cl)
                chains.append((m.group(1), hook, rules))
                continue
            raise NftError("syntax error, unexpected %r" % line)
        # nft expands a block: objects and chains first, then the rules
        for name_, packets, nbytes in counters:
            self._counter(t, name_, packets, nbytes)
        for kind, sname, spec, elems in sets:
            self._add_elems(self._set(t, sname, kind, spec), elems)
        for cname, hook, _ in chains:
            self._chain(t, cname, hook)
        for cname, _, rules in chains:
            for r in rules:
                self._add_rule(t, t["chains"][cname], r)

    def _cmd(self, work, line):
        F = r"(ip|ip6)"
        m = re.match(r"^(?:add )?table %s (\w+)$" % F, line)
        if m:
            self._table(work, m.group(1), m.group(2), create=True)
            return
        m = re.match(r"^delete table %s (\w+)$" % F, line)
        if m:
            if m.group(2) not in work[m.group(1)]:
                raise NftError(ENOENT)
            del work[m.group(1)][m.group(2)]
            return
        m = re.match(r"^add chain %s (\w+) (\w+)(?: \{ (.*) \})?$" % F, line)
        if m:
            t = self._table(work, m.group(1), m.group(2))
            self._chain(t, m.group(3), self._hook(m.group(4)) if m.group(4) else None)
            return
        m = re.match(r"^flush chain %s (\w+) (\w+)$" % F, line)
        if m:
            ch = self._table(work, m.group(1), m.group(2))["chains"].get(m.group(3))
            if ch is None:
                raise NftError(ENOENT)
            ch["rules"] = []
            return
        m = re.match(r"^delete chain %s (\w+) (\w+)$" % F, line)
        if m:
            t = self._table(work, m.group(1), m.group(2))
            ch = t["chains"].get(m.group(3))
            if ch is None:
                raise NftError(ENOENT)
            jumps = sum(1 for c in t["chains"].values() for r in c["rules"] if r["refs"].get("jump") == m.group(3))
            if ch["rules"] or jumps:
                raise NftError(EBUSY)
            del t["chains"][m.group(3)]
            return
        m = re.match(r"^add (set|map) ip (\w+) (\w+) \{ (.*) \}$", line)
        if m:
            t = self._table(work, "ip", m.group(2))
            self._set(t, m.group(3), m.group(1), self._set_spec(m.group(1), m.group(4)))
            return
        m = re.match(r"^(flush|delete) (set|map) ip (\w+) (\w+)$", line)
        if m:
            t = self._table(work, "ip", m.group(3))
            s = t["sets"].get(m.group(4))
            if s is None or s["kind"] != m.group(2):
                raise NftError(ENOENT)
            if m.group(1) == "flush":
                s["elems"] = collections.OrderedDict()
            else:
                if self._set_refs(t, m.group(4)):
                    raise NftError(EBUSY)
                del t["sets"][m.group(4)]
            return
        m = re.match(r"^(add|delete) element ip (\w+) (\w+) \{ (.*) \}$", line)
        if m:
            t = self._table(work, "ip", m.group(2))
            s = t["sets"].get(m.group(3))
            if s is None:
                raise NftError(ENOENT)
            elems = _elements(m.group(4))
            if m.group(1) == "add":
                self._add_elems(s, elems)
            else:
                self._del_elems(s, elems)
            return
        m = re.match(r"^add counter ip (\w+) (\w+)$", line)
        if m:
            self._counter(self._table(work, "ip", m.group(1)), m.group(2))
            return
        m = re.match(r"^delete counter ip (\w+) (\w+)$", line)
        if m:
            t = self._table(work, "ip", m.group(1))
            if m.group(2) not in t["counters"]:
                raise NftError(ENOENT)
            if self._refs(t, m.group(2)):
                raise NftError(EBUSY)
            del t["counters"][m.group(2)]
            return
        m = re.match(r"^add rule %s (\w+) (\w+) (.+)$" % F, line)
        if m:
            t = self._table(work, m.group(1), m.group(2))
            ch = t["chains"].get(m.group(3))
            if ch is None:
                raise NftError(ENOENT)
            self._add_rule(t, ch, m.group(4))
            return
        # a rule by its handle (КТ2 К2-4: the numgen rule of «по соединениям» between two full applies); replace keeps
        # the place and gives a new handle, insert goes right before the rule of the handle
        m = re.match(r"^(replace|insert|delete) rule %s (\w+) (\w+) handle (\d+)(?: (.+))?$" % F, line)
        if m:
            t = self._table(work, m.group(2), m.group(3))
            ch = t["chains"].get(m.group(4))
            if ch is None:
                raise NftError(ENOENT)
            at = [i for i, r in enumerate(ch["rules"]) if r["handle"] == int(m.group(5))]
            if not at or (m.group(1) == "delete") != (m.group(6) is None):
                raise NftError(ENOENT if not at else "syntax error, unexpected %r" % line)
            i = at[0]
            if m.group(1) == "delete":
                del ch["rules"][i]
                return
            self._add_rule(t, ch, m.group(6))
            new = ch["rules"].pop()
            if m.group(1) == "replace":
                ch["rules"][i] = new
            else:
                ch["rules"].insert(i, new)
            return
        raise NftError("syntax error, unexpected %r" % line)

    @staticmethod
    def _refs(t, counter):
        return sum(1 for ch in t["chains"].values() for r in ch["rules"] if r["refs"].get("counter") == counter)

    @staticmethod
    def _set_refs(t, name):
        return sum(1 for ch in t["chains"].values() for r in ch["rules"] if name in (r["refs"].get("sets") or []))

    # ── JSON output (libnftables schema, nft 1.0.x order) ──────────────
    def _meta(self):
        return {"metainfo": {"version": self.version, "release_name": self.RELEASE, "json_schema_version": 1}}

    def json_tables(self):
        items = [self._meta()]
        for family in FAMILIES:
            for name, t in self._fam(family).items():
                items.append({"table": {"family": family, "name": name, "handle": t["handle"]}})
        return json.dumps({"nftables": items})

    @staticmethod
    def _json_addr(k):
        if "-" in k:
            return {"range": k.split("-", 1)}
        if "/" in k:
            net = ipaddress.IPv4Network(k)
            return {"prefix": {"addr": str(net.network_address), "len": net.prefixlen}}
        return k

    @classmethod
    def _json_elem(cls, s):
        out = []
        for k, v in s["elems"].items():
            if s["kind"] == "map":
                out.append([k, v])
            elif s["type"] == "mark":
                out.append(int(k, 16))
            elif s["type"] == "inet_service":
                out.append(int(k))
            elif s["type"] == "ipv4_addr . inet_proto":
                a, p = k.split(" . ")
                proto = {"range": [int(x) for x in p.split("-")]} if "-" in p else int(p)
                out.append({"concat": [cls._json_addr(a), proto]})
            else:
                out.append(cls._json_addr(k))
        return out

    def _json_set_obj(self, family, table, sname, s):
        typ = s["type"].split(" . ") if " . " in s["type"] else s["type"]
        d = {"family": family, "name": sname, "table": table, "type": typ, "handle": s["handle"]}
        if s.get("timeout"):
            d["timeout"] = {"s": 1, "m": 60, "h": 3600, "d": 86400}[s["timeout"][-1]] * int(s["timeout"][:-1])
        if s.get("auto_merge"):
            d["auto-merge"] = True
        if s["map"]:
            d["map"] = s["map"]
        if s["flags"]:
            d["flags"] = list(s["flags"])
        if s["elems"]:
            d["elem"] = self._json_elem(s)
        return d

    def json_set(self, table, sname, family="ip"):
        """``nft -j list set``: metainfo and the one set (with its elements)."""
        s = self._fam(family)[table]["sets"][sname]
        return json.dumps({"nftables": [self._meta(), {s["kind"]: self._json_set_obj(family, table, sname, s)}]})

    def json_list(self, args):
        """The small listings of F2 (R-perf), in the objects ``list table`` prints: ``list chains [FAMILY]`` (every
        chain of every table, no rules), ``list chain FAMILY T NAME`` (one chain with its rules), ``list counters
        table FAMILY T``, ``list set|map FAMILY T NAME`` (a set
        listed as a map, or the other way round, is ENOENT — as nft's «list set» refuses a map). -> (rc, out, err),
        None for anything else."""
        enoent = (1, "", "Error: No such file or directory\nlist %s\n^^^^^^^^^^^^^^^^^^^^^\n" % " ".join(args[1:]))
        if args[:2] == ["list", "chains"] and len(args) <= 3 and (len(args) == 2 or args[2] in FAMILIES):
            items = [self._meta()]
            for family in ([args[2]] if len(args) == 3 else list(FAMILIES)):
                for name in self._fam(family):
                    items += [it for it in self._json_items(name, family) if "chain" in it]
            return 0, json.dumps({"nftables": items}), ""
        if args[:3] == ["list", "counters", "table"] and len(args) == 5 and args[3] in FAMILIES:
            if args[4] not in self._fam(args[3]):
                return enoent
            items = [self._meta()] + [it for it in self._json_items(args[4], args[3]) if "counter" in it]
            return 0, json.dumps({"nftables": items}), ""
        if args[:2] == ["list", "chain"] and len(args) == 5 and args[2] in FAMILIES:    # a chain with its rules
            if args[3] not in self._fam(args[2]) or args[4] not in self._fam(args[2])[args[3]]["chains"]:
                return enoent
            items = [self._meta()] + [it for it in self._json_items(args[3], args[2])
                                      if ("chain" in it and it["chain"].get("name") == args[4])
                                      or ("rule" in it and it["rule"].get("chain") == args[4])]
            return 0, json.dumps({"nftables": items}), ""
        if args[:1] == ["list"] and len(args) == 5 and args[1] in ("set", "map") and args[2] in FAMILIES:
            if args[3] not in self._fam(args[2]):
                return enoent
            items = [it for it in self._json_items(args[3], args[2])
                     if args[1] in it and it[args[1]].get("name") == args[4]]
            if not items:
                return enoent
            return 0, json.dumps({"nftables": [self._meta()] + items}), ""
        return None

    def json_table(self, name, family="ip"):
        return json.dumps({"nftables": self._json_items(name, family)})

    def _json_items(self, name, family="ip"):
        # the order of real nft 1.0.6 / 1.0.9 (tests/fixtures/nft/real-*-list.json):
        # metainfo, table, counters, chains, sets, rules
        t = self._fam(family)[name]
        items = [self._meta(), {"table": {"family": family, "name": name, "handle": t["handle"]}}]
        for cname, c in t["counters"].items():
            items.append({"counter": {"family": family, "name": cname, "table": name, "handle": c["handle"],
                                      "packets": c["packets"], "bytes": c["bytes"]}})
        for cname, ch in t["chains"].items():
            d = {"family": family, "table": name, "name": cname, "handle": ch["handle"]}
            if ch["hook"]:
                d.update({"type": ch["hook"]["type"], "hook": ch["hook"]["hook"], "prio": ch["hook"]["prio"],
                          "policy": ch["hook"]["policy"]})
            items.append({"chain": d})
        for sname, s in t["sets"].items():
            items.append({s["kind"]: self._json_set_obj(family, name, sname, s)})
        for cname, ch in t["chains"].items():
            for r in ch["rules"]:
                d = {"family": family, "table": name, "chain": cname, "handle": r["handle"]}
                if r["comment"]:
                    d["comment"] = r["comment"]
                d["expr"] = r["expr"]
                items.append({"rule": d})
        return items

    # ── helpers for tests ───────────────────────────────────────────────
    def table(self, name="kaskad", family="ip"):
        return self._fam(family).get(name)

    def forwards(self, name="kaskad"):
        out = {}
        t = self.tables.get(name)
        for r in (t["chains"].get("fwd_dnat", {"rules": []})["rules"] if t else []):
            m = _R_DNAT.match(r["text"])
            if m:
                out[(m.group(1), int(m.group(2)))] = (m.group(4), int(m.group(5)))
        return out

    def rules(self, chain, name="kaskad", family="ip"):
        return [r["text"] for r in self._fam(family)[name]["chains"][chain]["rules"]]

    def elements(self, sname, name="kaskad"):
        """Elements of a set (list of keys) or a map ({key: value})."""
        s = self.tables[name]["sets"][sname]
        return dict(s["elems"]) if s["kind"] == "map" else list(s["elems"])

    def counter(self, cname, name="kaskad"):
        c = self.tables[name]["counters"].get(cname)
        return None if c is None else {"packets": c["packets"], "bytes": c["bytes"]}

    def set_counter(self, cname, packets, nbytes, name="kaskad"):
        c = self.tables[name]["counters"][cname]
        c["packets"], c["bytes"] = packets, nbytes

    def add_foreign_chain(self, cname, name="kaskad", hook=None):
        """A chain the renderers do not know (``gw_*`` of wave 2, or a hand-made one), one accept rule."""
        t = self._table({"ip": self.tables, "ip6": self.tables6}, "ip", name, create=True)
        t["chains"][cname] = {"handle": self._handle(), "hook": hook,
                              "rules": [{"handle": self._handle(), "text": "accept", "expr": [{"accept": None}],
                                         "comment": None, "refs": {}}]}

    def add_foreign_counter(self, cname, name="kaskad", packets=0, nbytes=0):
        self._counter(self._table({"ip": self.tables, "ip6": self.tables6}, "ip", name, create=True),
                      cname, packets, nbytes)

    def set_prio(self, chain, prio, name="kaskad"):
        self.tables[name]["chains"][chain]["hook"]["prio"] = prio

    def drop_rule(self, chain, index, name="kaskad", family="ip"):
        del self._fam(family)[name]["chains"][chain]["rules"][index]

    def flush_ruleset(self):
        self.tables = collections.OrderedDict()
        self.tables6 = collections.OrderedDict()


# ── iptables ───────────────────────────────────────────────────────────────

BAD_RULE = "iptables: Bad rule (does a matching rule exist in that chain?).\n"
NO_CHAIN = "iptables: No chain/target/match by that name.\n"


def _render_rule(tokens):
    """Like real ``iptables -S`` 1.8.x: comments always in double quotes
    (``--comment "kaskad:compat"``, tests/fixtures/nft/real-compat-S.txt), other tokens quoted
    only when they contain spaces."""
    out = []
    prev = ""
    for t in tokens:
        out.append('"%s"' % t if (prev == "--comment" or " " in t or not t) else t)
        prev = t
    return " ".join(out)


class FakeIptables(object):
    BUILTIN = {"filter": ("INPUT", "FORWARD", "OUTPUT"),
               "nat": ("PREROUTING", "INPUT", "OUTPUT", "POSTROUTING")}

    def __init__(self, version="v1.8.9 (nf_tables)"):
        self.version = version
        self.chains = {t: collections.OrderedDict((c, []) for c in cs) for t, cs in self.BUILTIN.items()}
        self.policy = {}
        self.fail_insert = None
        self.calls = []

    def add(self, table, chain, rule):
        """Append a rule given as the tail of an ``iptables -S`` line."""
        self.chains[table].setdefault(chain, []).append(shlex.split(rule))

    def new_chain(self, table, chain):
        self.chains[table].setdefault(chain, [])

    def rules(self, table, chain):
        return [list(r) for r in self.chains[table].get(chain, [])]

    def __call__(self, argv, input):
        self.calls.append(list(argv))
        args = list(argv[1:])
        table = "filter"
        rest = []
        i = 0
        while i < len(args):
            a = args[i]
            if a == "-w":
                i += 2 if (i + 1 < len(args) and args[i + 1].isdigit()) else 1
                continue
            if a == "-t":
                table = args[i + 1]
                i += 2
                continue
            rest.append(a)
            i += 1
        if rest == ["-V"]:
            return 0, "iptables %s\n" % self.version, ""
        if not rest:
            return 2, "", "iptables: no command specified\n"
        chains = self.chains.setdefault(table, collections.OrderedDict())
        cmd = rest[0]
        if cmd == "-S":
            names = rest[1:2] or list(chains)
            out = []
            for c in names:
                if c not in chains:
                    return 1, "", NO_CHAIN
                if c in self.BUILTIN.get(table, ()):
                    out.append("-P %s %s" % (c, self.policy.get((table, c), "ACCEPT")))
                else:
                    out.append("-N %s" % c)
            for c in names:
                for r in chains[c]:
                    out.append("-A %s %s" % (c, _render_rule(r)))
            return 0, "\n".join(out) + "\n", ""
        chain = rest[1] if len(rest) > 1 else ""
        if chain not in chains:
            return 1, "", NO_CHAIN
        tail = rest[2:]
        if cmd == "-C":
            return (0, "", "") if tail in chains[chain] else (1, "", BAD_RULE)
        if cmd == "-I":
            pos = 1
            if tail and tail[0].isdigit():
                pos, tail = int(tail[0]), tail[1:]
            if self.fail_insert:
                return 4, "", self.fail_insert
            chains[chain].insert(pos - 1, tail)
            return 0, "", ""
        if cmd == "-A":
            chains[chain].append(tail)
            return 0, "", ""
        if cmd == "-D":
            if tail in chains[chain]:
                chains[chain].remove(tail)
                return 0, "", ""
            return 1, "", BAD_RULE
        return 2, "", "iptables: unknown command %s\n" % cmd


# ── systemctl / sysctl ─────────────────────────────────────────────────────

class FakeSystemctl(object):
    def __init__(self):
        self.units = {}            # name -> {"enabled": bool, "active": bool, "masked"?, "packaged"?}
        self.reloads = 0
        self.restarts = {}         # name -> how many times restarted
        self.fail = {}             # verb -> error text
        self.fail_units = {}       # unit -> error text of its start/restart (it stays inactive; F2: kaskad-dns)
        self.calls = []

    def _file(self, name):
        return os.path.join(paths.systemd_dir(), name)

    def _credential_missing(self, name):
        """The path of a ``LoadCredential=`` of the unit file that does not exist (None — all there)."""
        try:
            with open(self._file(name), encoding="utf-8") as f:
                lines = f.read().splitlines()
        except OSError:
            return None
        for ln in lines:
            if ln.startswith("LoadCredential=") and ":" in ln:
                path = ln.split(":", 1)[1].strip()
                if path.startswith("/") and not os.path.exists(path):
                    return path
        return None

    def __call__(self, argv, input):
        self.calls.append(list(argv))
        args = list(argv[1:])
        verb = args[0] if args else ""
        if verb in self.fail:
            return 1, "", self.fail[verb]
        names = [a for a in args[1:] if not a.startswith("-")]
        now = "--now" in args
        if verb == "daemon-reload":
            self.reloads += 1
            return 0, "", ""
        if verb == "is-active":
            u = self.units.get(names[0]) or {}
            if u.get("activating"):
                return 3, "activating\n", ""
            return (0, "active\n", "") if u.get("active") else (3, "inactive\n", "")
        if verb == "is-enabled":
            n = names[0]
            u = self.units.get(n) or {}
            if u.get("masked"):
                return 1, "masked\n", ""
            if u.get("enabled"):
                return 0, "enabled\n", ""
            if not os.path.exists(self._file(n)) and not u.get("packaged"):
                return 1, "", "Failed to get unit file state for %s: No such file or directory\n" % n
            if u.get("packaged"):
                return 1, "disabled\n", ""
            with open(self._file(n), encoding="utf-8") as f:
                if "[Install]" not in f.read():
                    return 0, "static\n", ""
            return 1, "disabled\n", ""
        if verb in ("start", "stop", "restart", "try-restart", "reload"):
            for n in names:
                u = self.units.get(n) or {}
                if not os.path.exists(self._file(n)) and not u.get("packaged"):
                    return 5, "", "Failed to %s %s: Unit %s not found.\n" % (verb, n, n)
                if u.get("masked") and verb in ("start", "restart"):
                    return 1, "", "Failed to %s %s: Unit %s is masked.\n" % (verb, n, n)
                if n in self.fail_units and verb in ("start", "restart", "try-restart"):
                    self.units.setdefault(n, {"enabled": False, "active": False})["active"] = False
                    return 1, "", self.fail_units[n]
                u = self.units.setdefault(n, {"enabled": False, "active": False})
                cred = self._credential_missing(n) if verb in ("start", "restart") or u.get("active") else None
                if cred and verb != "reload":
                    # as systemd: LoadCredential= of a file that is not there — the start fails (B7 круг 2, -sec С-1)
                    u["active"] = False
                    return 1, "", "Job for %s failed: Failed to set up credentials (%s).\n" % (n, cred)
                if verb in ("restart", "try-restart", "reload"):
                    if verb == "restart" or u.get("active"):
                        self.restarts[n] = self.restarts.get(n, 0) + 1
                        u["active"] = True
                else:
                    u["active"] = verb == "start"
            return 0, "", ""
        if verb in ("enable", "disable"):
            # as systemd: «Created symlink …» / «Removed …» for every link made or taken away, and a whole
            # daemon-reload after it unless --no-reload — EVEN when nothing changed (T-M6-qs330b №14)
            said = []
            for n in names:
                u0 = self.units.get(n) or {}
                if not os.path.exists(self._file(n)) and not u0.get("packaged"):
                    return 1, "", "Failed to %s unit: Unit file %s does not exist.\n" % (verb, n)
                u = self.units.setdefault(n, {"enabled": False, "active": False})
                if u["enabled"] != (verb == "enable"):
                    link = "/etc/systemd/system/multi-user.target.wants/%s" % n
                    said.append(("Created symlink %s → %s.\n" % (link, self._file(n))) if verb == "enable"
                                else "Removed \"%s\".\n" % link)
                u["enabled"] = verb == "enable"
                if now:
                    u["active"] = verb == "enable"
            if "--no-reload" not in args:
                self.reloads += 1
            return 0, "", "".join(said)
        if verb in ("mask", "unmask"):
            for n in names:
                u = self.units.setdefault(n, {"enabled": False, "active": False})
                u["masked"] = verb == "mask"
            return 0, "", ""
        if verb == "reset-failed":
            return 0, "", ""
        if verb == "kill":                 # ``systemctl kill [--signal=…] UNIT``: the unit stops (B6, REV-s1-b5 П1)
            for n in names:
                u = self.units.get(n)
                if u is not None and not u.get("unkillable"):
                    u["active"] = False
            return 0, "", ""
        if verb == "show" and "-p" in args and "--value" not in args:
            # ``systemctl show -p Id -p ActiveState … UNIT…``: a block «Key=Value» per unit, a blank line between them;
            # a unit may carry "result" / "status" (Result=, ExecMainStatus=) of how it last stopped
            props = [args[i + 1] for i, a in enumerate(args) if a == "-p" and i + 1 < len(args)]
            blocks = []
            for n in [x for x in names if x not in props]:
                u = self.units.get(n)
                known = u is not None or os.path.exists(self._file(n))
                active = bool((u or {}).get("active"))
                vals = {"Id": n, "LoadState": "loaded" if known else "not-found",
                        "ActiveState": "active" if active else ("failed" if (u or {}).get("result") else "inactive"),
                        "SubState": "running" if active else ((u or {}).get("sub") or "dead"),
                        "Result": (u or {}).get("result") or "success", "ExecMainStatus": str((u or {}).get("status", 0))}
                blocks.append("\n".join("%s=%s" % (p, vals.get(p, "")) for p in props))
            return 0, "\n\n".join(blocks) + "\n", ""
        if verb == "show" and "-p" in args and "--value" in args:
            # ``systemctl show -p ActiveEnterTimestampMonotonic --value UNIT``: µs of CLOCK_MONOTONIC
            prop = args[args.index("-p") + 1]
            u = self.units.get(names[-1]) or {}
            if prop == "ActiveEnterTimestampMonotonic":
                return 0, "%d\n" % int(u.get("since_mono", 0) * 1e6), ""
            if prop == "ActiveState":        # B7 (REV-s1-b6-sec Н-1): what ``gsys.unit_stopped`` asks
                if u.get("activating"):
                    return 0, "activating\n", ""
                return 0, ("active" if u.get("active") else ("failed" if u.get("result") else "inactive")) + "\n", ""
            if prop == "MainPID":            # F2: several units -> their values, a blank line between them
                pids = ["%d" % ((self.units.get(n) or {}).get("pid", 0) if (self.units.get(n) or {}).get("active")
                                else 0) for n in names if n != prop]
                return 0, "\n\n".join(pids) + "\n", ""
            return 0, "\n", ""
        return 1, "", "Unknown command verb %s.\n" % verb


class FakeSysctl(object):
    def __init__(self, ip_forward="1"):
        self.values = {"net.ipv4.ip_forward": ip_forward}
        self.fail_write = None
        self.writes = []

    def __call__(self, argv, input):
        args = list(argv[1:])
        if len(args) == 2 and args[0] == "-n":
            if args[1] not in self.values:
                return 255, "", "sysctl: cannot stat /proc/sys/%s: No such file or directory\n" % args[1].replace(".", "/")
            return 0, self.values[args[1]] + "\n", ""
        if len(args) == 2 and args[0] == "-w" and "=" in args[1]:
            if self.fail_write:
                return 255, "", self.fail_write
            k, v = args[1].split("=", 1)
            self.values[k] = v
            self.writes.append(args[1])
            return 0, "%s = %s\n" % (k, v), ""
        return 255, "", "sysctl: unsupported arguments\n"


# ── the whole server ───────────────────────────────────────────────────────

DEFAULT_BINARIES = ("nft", "iptables", "systemctl", "sysctl", "ss", "uname")

SS_SSHD = ('tcp   LISTEN 0      128          0.0.0.0:22        0.0.0.0:*    users:(("sshd",pid=812,fd=3))\n'
           'tcp   LISTEN 0      128             [::]:22           [::]:*    users:(("sshd",pid=812,fd=4))\n')


class World(object):
    """FakeRunner + every fake above. ``world.runner`` goes into the code under test."""

    def __init__(self, binaries=DEFAULT_BINARIES, ss="", uname="6.1.0-18-amd64", ip_forward="1"):
        self.nft = FakeNft()
        self.ipt = FakeIptables()
        self.sysd = FakeSystemctl()
        self.sysctl = FakeSysctl(ip_forward)
        self.ss = ss
        self.uname = uname
        self.runner = FakeRunner(binaries=binaries)
        self.runner.on(["nft"], self.nft)
        self.runner.on(["iptables"], self.ipt)
        self.runner.on(["systemctl"], self.sysd)
        self.runner.on(["sysctl"], self.sysctl)
        self.runner.on(["ss"], lambda argv, inp: (0, self.ss, ""))
        self.runner.on(["uname", "-r"], lambda argv, inp: (0, self.uname + "\n", ""))

    def nft_writes(self):
        """Committed (not -c) nft scripts/commands that change the ruleset."""
        return [a for a, _ in self.runner.calls
                if a[:1] == ["nft"] and (("-f" in a and "-c" not in a) or a[1:2] == ["delete"])]


# ── state and system files ─────────────────────────────────────────────────

F443 = {"proto": "tcp", "port": 443, "to": "203.0.113.5", "to_port": 443, "name": "Вена"}
F51820 = {"proto": "udp", "port": 51820, "to": "198.51.100.7", "to_port": 51820, "name": "Амстердам"}
F8443 = {"proto": "tcp", "port": 8443, "to": "198.51.100.7", "to_port": 443}


def make_state(forwards=(), engine="nft"):
    st = state.empty()
    st["engine"] = engine
    for f in forwards:
        state.upsert_forward(st, dict(f))
    return st


def save_state(forwards=(), engine="nft"):
    st = make_state(forwards, engine)
    state.save(st)
    return state.load()


OS_DEBIAN12 = ('PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"\nNAME="Debian GNU/Linux"\nVERSION_ID="12"\n'
               'VERSION="12 (bookworm)"\nVERSION_CODENAME=bookworm\nID=debian\n')


def write_os_release(case, text=OS_DEBIAN12):
    """Write /etc/os-release under the test root (case is a tests.helpers.RootCase)."""
    return case.write("/etc/os-release", text, 0o644)
