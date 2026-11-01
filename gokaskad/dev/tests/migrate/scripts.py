"""Run the REAL rule-making shell functions of Kaskad 2.13 and of the free Kaskad v1.1 on the
iptables emulator.

The function bodies are kept verbatim in fixtures (extracted from install.sh v2.13 and from
anten-ka/kaskad v1.1): ``pro213_rule_functions.sh``, ``free_v11_apply.sh``. A tiny interpreter
runs their ``iptables ...`` lines with the variables substituted, honours
``if ! iptables -C ...; then ... fi``, skips other ``if`` blocks (rc / ufw checks) and emulates
``remove_rules_for_port`` (``iptables -S | grep label | eval "iptables -D ..."``).
"""

import os
import re

from tests.fakes_ipt import IptFail, ipt_tokenize
from tests.migrate.env import FIX


def _read(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


def function_body(text, name):
    m = re.search(r"^%s\(\) \{\n(.*?)^\}" % re.escape(name), text, re.S | re.M)
    if not m:
        raise AssertionError("no function %s" % name)
    return m.group(1).splitlines()


def _subst(line, variables):
    for k in sorted(variables, key=len, reverse=True):
        line = line.replace("${%s}" % k, variables[k]).replace("$%s" % k, variables[k])
    return line


def _clean(cmd):
    return cmd.replace("|| rc=1", "").replace("2>/dev/null", "").strip()


def _ipt(emu, line):
    toks = ipt_tokenize(_clean(line))
    assert toks[0] == "iptables", line
    rc, out, err = emu.h_iptables(toks, None)
    return rc, out, err


def remove_rules_for_port(emu, proto, in_port):
    """2.13 remove_rules_for_port: grep the label in -S output, eval "iptables -D <rule>"."""
    label = "kaskad:%s:%s" % (in_port, proto)
    for table, chain, need in (("nat", "PREROUTING", "DNAT"), ("filter", "INPUT", ""), ("filter", "FORWARD", "")):
        listing = emu.list_S(table, chain)
        for line in listing.splitlines():
            if not line.startswith("-A ") or label not in line or need not in line:
                continue
            toks = ["iptables"] + (["-t", "nat"] if table == "nat" else []) + ["-D"] + ipt_tokenize(line[3:])
            emu.h_iptables(toks, None)


def run_function(emu, lines, variables):
    skip_block = False
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("if ! iptables"):
            check = _subst(line[len("if ! "):].split(";")[0], variables)
            rc, _o, _e = _ipt(emu, check)
            skip_block = rc == 0
            continue
        if line.startswith("if ") or line.startswith("if["):
            skip_block = True
            continue
        if line == "fi":
            skip_block = False
            continue
        if skip_block:
            continue
        if line.startswith("remove_rules_for_port"):
            remove_rules_for_port(emu, variables["proto"], variables["in_port"])
            continue
        if line.startswith("iptables "):
            cmd = _subst(line, variables)
            rc, _o, err = _ipt(emu, cmd)
            if rc and "2>/dev/null" not in line:
                raise IptFail("%s -> %s" % (cmd, err))


def pro213_add(emu, proto, in_port, out_port, target, iface="eth0"):
    """apply_iptables_rules of Kaskad PRO 2.13."""
    body = function_body(_read("pro213_rule_functions.sh"), "apply_iptables_rules")
    run_function(emu, body, {"proto": proto, "in_port": str(in_port), "out_port": str(out_port),
                             "target_ip": target, "IFACE": iface, "name": "test"})


def free_add(emu, proto, in_port, out_port, target, iface="eth0"):
    """apply_iptables_rules of the free Kaskad v1.1 (v1.0 is the same with in_port == out_port)."""
    body = function_body(_read("free_v11_apply.sh"), "apply_iptables_rules")
    run_function(emu, body, {"PROTO": proto, "IN_PORT": str(in_port), "OUT_PORT": str(out_port),
                             "TARGET_IP": target, "IFACE": iface, "NAME": "test"})
