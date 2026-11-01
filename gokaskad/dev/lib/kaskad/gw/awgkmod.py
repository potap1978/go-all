"""AmneziaWG in the kernel: the module amneziawg-linux-kernel-module built by DKMS, with an automatic way back to
amneziawg-go (docs/k3/SPEC-wave3.md §6, research docs/k3/research/kmod.md).

What runs where
    inbounds awg1 (1.0/1.5), awg2 (2.0)    the interface of the module (``ip link add kgw-awgN type amneziawg``)
                                           when acceleration is on and the module is loaded for the running
                                           kernel; else amneziawg-go, as before (gw/inbounds/awg.py)
    inbound awg3 (3.1)                     the same, but only when the module in memory carries Kaskad's set of
                                           fixes (vendor/awg-kmod-31.patch, its mark SET_ID in
                                           /sys/module/amneziawg/version — kernel_31()): the tag alone hangs or
                                           panics a node on ``awg syncconf`` with HeaderProtectionKey under traffic
                                           (#253). A module without the set (a node of an older Kaskad, or the set
                                           did not build here) — 3.1 on amneziawg-go, as before
    legs of line 2.0 (kawgN)               the module, in install.sh (egress_awg_leg_mode, EGRESS_AWG_KMOD_LINES)
    legs of 3.1, 1.5 and 1.0               the module too, but only with the set in memory (leg_lines): the stock
                                           module as a client of 3.1 did not handshake with an amneziawg-go server on
                                           x86 (#222, Aeza 6.8, 07.10.2026), the module with the set did, and so did a
                                           leg of 1.5 with I1 (the full table of k3/stand/logs/bench-msk-vps.md: the
                                           module as the initiator sends I1–I5 before the initiation, send.c; tags
                                           MODULE_TAGS). Without the set — amneziawg-go, as before

Installation (``install``, ``kaskadctl gw accel on``): host checks (x86_64/aarch64, no container — LXC/OpenVZ
cannot load modules —, Secure Boot off: DKMS signs with its own key, the kernel would refuse it without
``mokutil --import`` at the console); apt ``dkms gcc make patch`` (+ ``gcc-NN`` the kernel was built with, from
/proc/version), then ``linux-headers-$(uname -r)`` and the headers metapackage of the running flavour (the one
whose ``linux-image-*`` metapackage pulls this kernel: generic, virtual, kvm, amd64, cloud-amd64 …) so that the
next kernel builds too; the source archive of the pinned tag from GitHub, checked against SHA256 (no other
archive is ever built); its src/ unpacked to /usr/src/amneziawg-<VERSION> with ``PACKAGE_VERSION`` = the tag and
``AUTOINSTALL="no"`` in dkms.conf (a failing DKMS build must never leave apt with half-configured kernel
packages — our own hook builds, with ``|| true``); vendor/awg-kmod-31.patch (the set of 3.1: #253 and community PRs,
after a ``--dry-run``; a set that does not apply, build or pass its self-test is taken off again — the module
without it, 3.1 on amneziawg-go, ``set_failed`` remembers why); vendor/awg-kmod-218.patch (upstream PR #218: the signature of
``setup_udp_tunnel_sock`` detected at compile time) applied ONLY when the headers declare the new
``struct sock *`` signature — Ubuntu 26.04 kernels 7.0.0-38+; ``dkms add/build/install -k $(uname -r)``,
``modprobe amneziawg``, the kernel hooks, the boot unit, the self-test, and only then the lines move.

    /etc/kernel/postinst.d/zz-kaskad-amneziawg      run-parts with the new kernel's version (the image) …
    /etc/kernel/header_postinst.d/zz-kaskad-amneziawg … and again when its headers come (whichever is last
                                                      builds); patch #218 when that kernel needs it; never fails
    kaskad-gw-awgkmod.service                         at boot, before the AWG inbounds and the legs:
                                                      ``kaskadctl gw accel boot`` — modprobe; no module for the
                                                      running kernel -> the inbounds' units on amneziawg-go and a
                                                      warning (``gw status``, ``gw accel status``)

Self-test (``self_test``): netns kaskad-kt0 and kaskad-kt1; an interface of the module is created in kt0 and
moved to kt1 (its UDP socket stays in kt0, on lo — the topology of the upstream netns test); the peer is a
second module interface or amneziawg-go in kt0; parameters of lines 1.5 and 2.0 (gen_params, as the inbounds
get them): ping through the tunnel + a handshake. With the set: its mark in the module in memory, then 3.1
module<->module, amneziawg-go client -> module server (the inbound) and module client -> amneziawg-go server
(the role of a leg — legs of 3.1 rely on it). Never a live #253 check (it hangs the
node). A failure of the base cases -> acceleration stays off; of the 3.1 ones -> the module without the set.

Update (``upgrade``, ``kaskadctl gw accel upgrade`` from k3_post_update in a transient unit): a node on the module
whose module lacks the set gets it — the whole ``install`` path (build, the lines on amneziawg-go for the reload
of the module, self-test, the lines back): progress in the journal, a failure — the module without the set or
amneziawg-go, with the reason. Once per kernel: a set that failed on this kernel is not tried again by itself.

Parameters (``conf_problems``): the module trusts what ``awg setconf`` gives it — Jmin > Jmax panics it (#254),
huge I1–I5 make ``awg show`` loop (#228), ``<rc -N>`` crashes it (#237). Before a config goes into the kernel it
must pass the rules of its line (vendor.awgkeys.validate_params: Jmin < Jmax, S1–S4, H, I1–I5 syntax and size) and
the module's own (module_rules: tags of I only b c t r rc rd with positive sizes, 3.1 — S4 >= 12); else
amneziawg-go. ``conf_warnings``: 3.1 with RandomTrailers, unequal S and ranges in H loses ≈7 % (#226) — on both
engines alike, so only a warning.

A live interface of the module takes only peers: ``awg syncconf`` gets a config without HeaderProtectionKey and
I1–I5 (sync_conf_text; #253, #140), a change of the masking (key, S, H, I, J) recreates the interface
(gw/inbounds/awg.py).

State /etc/kaskad/gw/awg-kmod.json (0600; install.sh greps ``"enabled": true``):
    enabled     acceleration on (set only after a passed self-test)
    kernels     kernels the module was built for by Kaskad      selftest  {"ok", "detail", "kernel", "at"}
    fallback    {"kernel", "reason", "at"}: no module for the running kernel at the last boot
    failed      {kind: {"kernel", "reason", "at"}}: an inbound that did not come up on the module — on
                amneziawg-go until another kernel or the next ``gw accel on``
    error       why the last ``gw accel on`` failed
    set_failed  {"kernel", "reason", "at"}: the set of 3.1 did not apply, build or pass its self-test — the module
                runs without it (3.1 on amneziawg-go); ``upgrade`` does not retry on this kernel, ``gw accel on`` does
"""

import collections.abc
import fcntl
import glob
import hashlib
import json
import os
import re
import shutil
import tarfile
import tempfile
import time

from .. import paths, state, util
from ..errors import ConflictError, KaskadError, UnsupportedError
from ..i18n import saved_lang, tr
from ..vendor import awgkeys
from . import exits
from . import sys as gsys

NAME = "amneziawg"
TAG = "v3.1.20260906"
VERSION = "3.1.20260906"                 # PACKAGE_VERSION in dkms.conf, /usr/src/amneziawg-<VERSION>
COMMIT = "4569c4c67f3a57414969260cafbbd04694fbaae0"
URL = "https://github.com/amnezia-vpn/amneziawg-linux-kernel-module/archive/refs/tags/%s.tar.gz" % TAG
# sha256 of the archive above, computed 2026-10-05 from the real download (two downloads, the same bytes;
# GitHub keeps tag archives stable). Another value -> another archive -> nothing is built.
SHA256 = "e32fa46f1b6f9e319c5261f0b0765c3a9261e40cb7c4616fd73500da961a2932"
TOP = "amneziawg-linux-kernel-module-" + VERSION         # the top directory inside the archive

# Every line the module may take; line 3.1 (the inbound awg3) only with the set (kernel_lines, kernel_kinds).
KERNEL_LINES = ("1.0", "1.5", "2.0", "3.1")
KERNEL_KINDS = ("awg1", "awg2", "awg3")
# The legs on the module (install.sh EGRESS_AWG_KMOD_LINES): what a module client is proven for against an
# amneziawg-go server. 2.0 — always; 3.1, 1.5 and 1.0 — only with the set (leg_lines, LEG_LINES_SET): on x86 (Aeza,
# 6.8.0-48, 07.10.2026) the stock module as a leg of 3.1 did not connect to an exit on amneziawg-go (#222), the
# module with the set did, and a leg of 1.5 with I1 too (transit 1.5: 340 -> 570 Мбит/с at 30 % spare).
LEG_LINES = ("2.0", "3.1", "1.5", "1.0")
LEG_LINES_SET = ("3.1", "1.5", "1.0")
AWG_KINDS = ("awg1", "awg2", "awg3")


class Words(collections.abc.Mapping):
    """{code: words} in the language of the moment of the read (tr at every lookup, never at import): a dict of texts
    other modules index (CORE_TEXT here, awgengine.TITLE)."""

    def __init__(self, prefix, codes):
        self._prefix, self._codes = prefix, tuple(codes)

    def __getitem__(self, code):
        if code not in self._codes:
            raise KeyError(code)
        return tr(self._prefix + code)

    def __iter__(self):
        return iter(self._codes)

    def __len__(self):
        return len(self._codes)


CORE_TEXT = Words("gw_awgkmod.core.", ("kernel", "userspace"))
# Texts other modules and tests read as attributes (LEG31_WHY, TRAILERS_LOSS_TEXT): a tr() in the language of the
# moment (module __getattr__ below) — a constant would freeze the language of the import.
_TEXTS = {"LEG31_WHY": "gw_awgkmod.leg31_why", "TRAILERS_LOSS_TEXT": "gw_awgkmod.trailers_loss"}


def __getattr__(name):
    if name in _TEXTS:
        return tr(_TEXTS[name])
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


def _fail(kind, key, cls=KaskadError, **kw):
    """An error for people with ``data.kind`` — why the module did not go here (``build``, ``headers``, ``selftest``):
    awgengine stores it as the code of the engine's record (never parsed out of the words)."""
    return cls(tr(key, **kw), data={"kind": kind})

PATCH_NAME = "awg-kmod-218.patch"
# The set of fixes for 3.1 (#253 of Kaskad + community PRs) and its mark in WIREGUARD_VERSION (src/version.h ->
# MODULE_VERSION -> /sys/module/amneziawg/version, modinfo). A new revision of the set gets a new SET_ID.
SET_NAME = "awg-kmod-31.patch"
SET_ID = "kaskad-k31.1"
MODULE_TAGS = ("b", "c", "t", "r", "rc", "rd")            # tags of I1–I5 the module knows (junk.c)
PATCHED_MARK = "__compat_setup_udp_tunnel_sock"          # in compat/compat.h once PR #218 is in
HOOK_NAME = "zz-kaskad-amneziawg"
BOOT_UNIT = "kaskad-gw-awgkmod.service"
APT_BASE = ("dkms", "gcc", "make", "patch")
NETNS = ("kaskad-kt0", "kaskad-kt1")
# (line, peer, who starts): "kernel" — the module interface pings first (it is the client), "peer" — the other side.
SELFTEST = (("1.5", "kernel"), ("2.0", "kernel"), ("1.5", "userspace"), ("2.0", "userspace"))
SELFTEST_31 = (("3.1", "kernel", "kernel"), ("3.1", "userspace", "peer"), ("3.1", "userspace", "kernel"),
               ("1.0", "userspace", "kernel"))   # + the leg of 1.0 (1.5: SELFTEST, the module client with I1)
SELFTEST_INFO = ()      # cases told, never fatal (the role of a leg was one until the set proved it on x86)
BUILD_TIMEOUT = 1800
APT_TIMEOUT = 1800
# The new signature of setup_udp_tunnel_sock / udp_tunnel_sock_release (struct sock *): Ubuntu 26.04 7.0.0-38+
# changed the first one, upstream 7.1.5 both. The kernel hook greps the same (POSIX ERE, _HOOK_RE).
_NEW_SIG = re.compile(r"\bsetup_udp_tunnel_sock\s*\(\s*struct\s+net\s*\*\s*\w*\s*,\s*struct\s+sock\s*\*"
                      r"|\budp_tunnel_sock_release\s*\(\s*struct\s+sock\s*\*")
_HOOK_RE = (r"setup_udp_tunnel_sock[[:space:]]*\([[:space:]]*struct[[:space:]]+net[[:space:]]*\*[[:space:]]*"
            r"[A-Za-z_0-9]*[[:space:]]*,[[:space:]]*struct[[:space:]]+sock[[:space:]]*\*"
            r"|udp_tunnel_sock_release[[:space:]]*\([[:space:]]*struct[[:space:]]+sock[[:space:]]*\*")
_RELEASE = re.compile(r"^\d+\.\d+(?:\.\d+)?(?:-\d+)?(?:\+[A-Za-z0-9.~]+)?-([a-z0-9][a-z0-9.+-]*)$")

_sleep = time.sleep                     # tests patch it


# ── paths ──────────────────────────────────────────────────────────────────

def state_path():
    return os.path.join(paths.gw_dir(), "awg-kmod.json")


def src_dir():
    return paths.P("/usr/src/%s-%s" % (NAME, VERSION))


def module_dir():
    return paths.P("/sys/module/" + NAME)


def hook_paths():
    return [paths.P("/etc/kernel/postinst.d/" + HOOK_NAME), paths.P("/etc/kernel/header_postinst.d/" + HOOK_NAME)]


def boot_unit_path():
    return os.path.join(paths.systemd_dir(), BOOT_UNIT)


def kernel_tree(kernel, sub="build"):
    return paths.P("/lib/modules/%s/%s" % (kernel, sub))


def patch_source():
    return os.path.join(os.path.dirname(os.path.abspath(awgkeys.__file__)), PATCH_NAME)


def set_source():
    return os.path.join(os.path.dirname(os.path.abspath(awgkeys.__file__)), SET_NAME)


def lock_path():
    return os.path.join(paths.gw_dir(), "awg-kmod.lock")


def uapi_sock(iface):
    return paths.P("/var/run/amneziawg/%s.sock" % iface)


# ── state ──────────────────────────────────────────────────────────────────

def load():
    raw = util.read_text(state_path())
    if raw is None:
        return {}
    try:
        doc = json.loads(raw)
    except ValueError:
        return {}
    return doc if isinstance(doc, dict) else {}


def save(doc):
    p = state_path()
    d = os.path.dirname(p)
    if not os.path.isdir(d):
        os.makedirs(d, 0o700)
    doc = dict(doc)
    doc.update(tag=TAG, version=VERSION, updated=util.now_iso())
    util.atomic_write(p, util.json_text(doc), 0o600)
    return doc


def enabled():
    return bool(load().get("enabled"))


def _why(res):
    """The line of a failed command that says why (stderr first), without secrets."""
    lines = [ln.strip() for ln in (res.err or "").splitlines() + (res.out or "").splitlines() if ln.strip()]
    pick = next((ln for ln in lines if re.search(r"error|fail|not found|cannot|unable|denied|invalid", ln, re.I)),
                lines[-1] if lines else None)
    return util.redact(pick[:300]) if pick else tr("gw_awgkmod.rc", rc=res.rc)


def _log(key, **kw):
    """A line of the journal: «AWG KMOD: …» in the language of the server (util.journal_lang)."""
    with util.journal_lang():
        util.log_action("AWG KMOD: %s" % tr(key, **kw))


# ── the module in the kernel ───────────────────────────────────────────────

def running_kernel(runner):
    return runner.run(["uname", "-r"]).out.strip()


def module_loaded():
    return os.path.isdir(module_dir())


def module_version():
    """MODULE_VERSION of the module in memory ("" — none loaded)."""
    return (util.read_text(os.path.join(module_dir(), "version")) or "").strip()


def set_loaded():
    """The module in memory carries Kaskad's set of 3.1 (its mark in the version). Files only."""
    return SET_ID in module_version()


def kernel_31():
    """May line 3.1 go onto the module: the module in memory carries the set; none in memory — the module Kaskad
    would load carries it, unless the set failed here (``set_failed``). Files only (the faces ask it)."""
    if module_loaded():
        return set_loaded()
    return not load().get("set_failed")


def kernel_lines(k31=None):
    k31 = kernel_31() if k31 is None else k31
    return tuple(ln for ln in KERNEL_LINES if k31 or ln != "3.1")


def leg_lines(k31=None):
    """Lines of the legs on the module: 2.0, and 3.1, 1.5, 1.0 with the set (kernel_31)."""
    k31 = kernel_31() if k31 is None else k31
    return tuple(ln for ln in LEG_LINES if k31 or ln not in LEG_LINES_SET)


def kernel_kinds(k31=None):
    k31 = kernel_31() if k31 is None else k31
    return tuple(k for k in KERNEL_KINDS if k31 or k != "awg3")


def load_module(runner):
    """(True, "") when the module is in the kernel (``modprobe amneziawg`` once if needed), else (False, why)."""
    if module_loaded():
        return True, ""
    res = runner.run(["modprobe", NAME])
    if module_loaded():
        return True, ""
    return False, (_why(res) if not res.ok else tr("gw_awgkmod.no_module_after_modprobe"))


def _failed_here(runner, doc, kind):
    f = (doc.get("failed") or {}).get(kind)
    return isinstance(f, dict) and f.get("kernel") == running_kernel(runner)


def kernel_for(runner, kind, conf_text=None):
    """Should the inbound ``kind`` run on the module now? Acceleration on, a kernel line, its config (when
    given) safe for the module, no failure of this inbound on this kernel, the module loaded (modprobe once).
    Runs nothing while acceleration is off. awg3 (3.1) — only on a module that carries the set."""
    if kind not in KERNEL_KINDS:
        return False
    doc = load()
    if not doc.get("enabled"):
        return False
    if conf_text is not None and conf_problems(conf_text):
        return False
    if _failed_here(runner, doc, kind):
        return False
    return load_module(runner)[0] and kind in kernel_kinds()


def mark_failed(runner, kind, why):
    """The inbound did not come up on the module: amneziawg-go for it until another kernel or ``gw accel on``."""
    doc = load()
    failed = dict(doc.get("failed") or {})
    kernel = running_kernel(runner)
    failed[kind] = {"kernel": kernel, "reason": util.redact(str(why))[:300], "at": util.now_iso()}
    doc["failed"] = failed
    save(doc)
    _log("gw_awgkmod.log.inbound_failed", kind=kind, kernel=kernel, why=why)


def _ipacket_problem(v):
    """A signature packet the module would refuse or crash on: a tag it does not know (amneziawg-go's <d>, <ds>,
    <dz>, a typo — the validator lets unknown tags through), a size that is not a positive number (``<rc -5>``
    crashes the tag, #237)."""
    for m in re.finditer(r"<([a-z]{1,3})(?: ([^<>]*))?>", v):
        name, arg = m.group(1), m.group(2)
        if name not in MODULE_TAGS:
            return tr("gw_awgkmod.tag_unknown", tag=name, known=", ".join("<%s>" % t for t in MODULE_TAGS))
        if name in ("r", "rc", "rd") and not (arg and arg.isdigit() and int(arg) > 0):
            return tr("gw_awgkmod.tag_size", tag=name, arg=arg or "")
    return None


def module_rules(line, params):
    """The module's own rules on top of the rules of the line: tags of I1–I5, and S4 >= 12 for 3.1 (the nonce of
    the header protection is the first 12 bytes of the S prefix; the module refuses less with EINVAL)."""
    p = awgkeys.normalize_params(params)
    out = []
    for k in awgkeys.I_KEYS:
        if p[k]:
            why = _ipacket_problem(p[k])
            if why:
                out.append("%s: %s" % (k, why))
    if line == "3.1":
        s4 = p["S4"]
        if not (s4.isdigit() and int(s4) >= awgkeys.S_MIN_WITH_HEADER_PROTECTION):
            out.append(tr("gw_awgkmod.s4_31", s4=s4 or tr("gw_awgkmod.s4_unset"),
                          min=awgkeys.S_MIN_WITH_HEADER_PROTECTION))
    return out


def conf_problems(text, leg=False, k31=None):
    """Why a config for ``awg setconf`` must not go into the module ([] — it may): its line must be one the module
    takes (3.1 — with the set, kernel_31; a leg — leg_lines), its parameters must pass the rules of the line (the
    module panics on Jmin > Jmax, #254) and the module's own (module_rules)."""
    try:
        params = awgkeys.params_from_conf(text or "")
    except (awgkeys.AwgError, ValueError, TypeError) as e:
        return [str(e)]
    line = awgkeys.detect_line(params)
    if leg and line not in leg_lines(k31):
        if line == "3.1":
            return [tr("gw_awgkmod.leg31_why")]
        if line in LEG_LINES_SET:
            return [tr("gw_awgkmod.leg_no_set", line=line, set=SET_ID)]
        return [tr("gw_awgkmod.line_on_go", line=line)]
    if line not in KERNEL_LINES:
        return [tr("gw_awgkmod.line_on_go", line=line)]
    if line not in kernel_lines(k31):
        return [tr("gw_awgkmod.line31_no_set", set=SET_ID)]
    return list(awgkeys.validate_params(line, params)) + module_rules(line, params)


def trailers_loss_risk(params):
    """The rule against #226: with RandomTrailers on, the receiver takes long data packets for handshakes when
    S1–S4 differ and H are wide ranges (≈7 % lost by both implementations). Safe: S1 = S2 = S3 = S4, or every H a
    single value. True — the key is in the zone of losses (a warning, not a refusal)."""
    p = awgkeys.normalize_params(params)
    tr = p["RandomTrailers"].lower()
    if not tr or tr in ("off", "0", "false", "no", "f"):
        return False
    if len(set(p[k] for k in awgkeys.S_KEYS)) == 1:
        return False
    return not all(p[h] and "-" not in p[h] for h in awgkeys.H_KEYS)


def conf_warnings(text):
    """What to tell about a config that may go anywhere: 3.1 with RandomTrailers, unequal S1–S4 and ranges in H
    loses ≈7 % of the packets on both engines (#226)."""
    try:
        params = awgkeys.params_from_conf(text or "")
    except (awgkeys.AwgError, ValueError, TypeError):
        return []
    return [tr("gw_awgkmod.trailers_loss")] if trailers_loss_risk(params) else []


def check_file(path):
    """``kaskadctl gw accel check FILE`` (install.sh, the legs): problems of the leg's config in FILE."""
    text = util.read_text(path)
    if text is None:
        return [tr("gw_awgkmod.no_file", path=path)]
    return conf_problems(text, leg=True)


def sync_conf_text(text):
    """The config ``awg syncconf`` gives a live interface of the module: only what syncconf may change there —
    PrivateKey, ListenPort, FwMark and the peers. Without HeaderProtectionKey (writing it under traffic hangs or
    panics an unpatched module, #253) and I1–I5 (#140), and without the rest of the masking: it never changes on a
    live interface (a change recreates it), and a key absent from syncconf stays as it is."""
    keep = ("privatekey", "listenport", "fwmark")
    out, section = [], ""
    for line in (text or "").splitlines():
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            section = s.lower()
        elif section == "[interface]" and s and not s.startswith("#"):
            if s.split("=", 1)[0].strip().lower() not in keep:
                continue
        out.append(line)
    return "\n".join(out) + "\n"


# ── the host ───────────────────────────────────────────────────────────────

def _container(runner):
    if runner.which("systemd-detect-virt"):
        res = runner.run(["systemd-detect-virt", "-c"])
        v = res.out.strip()
        return v if res.ok and v and v != "none" else ""
    if os.path.exists(paths.P("/proc/vz")) and not os.path.exists(paths.P("/proc/bc")):
        return "openvz"
    return (util.read_text(paths.P("/run/systemd/container")) or "").strip()


def _secure_boot(runner):
    if runner.which("mokutil"):
        res = runner.run(["mokutil", "--sb-state"])
        return "secureboot enabled" in ((res.out or "") + (res.err or "")).lower()
    for p in sorted(glob.glob(paths.P("/sys/firmware/efi/efivars/SecureBoot-*"))):
        try:
            with open(p, "rb") as f:
                data = f.read()
        except OSError:
            continue
        return len(data) >= 5 and data[4] == 1          # 4 bytes of attributes, then the value
    return False


def supported_host(runner):
    """{"ok", "why", "arch", "container", "secure_boot"}: can the module be built and loaded here."""
    out = {"ok": True, "why": "", "arch": "", "container": "", "secure_boot": False}
    arch = runner.run(["uname", "-m"]).out.strip()
    out["arch"] = arch
    if arch not in ("x86_64", "aarch64"):
        out.update(ok=False, why=tr("gw_awgkmod.host.arch", arch=arch or tr("gw_awgkmod.host.arch_unknown")))
        return out
    virt = _container(runner)
    if virt:
        out.update(ok=False, container=virt,
                   why=tr("gw_awgkmod.host.container", virt=virt))
        return out
    if _secure_boot(runner):
        out.update(ok=False, secure_boot=True,
                   why=tr("gw_awgkmod.host.secure_boot"))
    return out


def flavor(release):
    """«generic» of 6.8.0-50-generic, «cloud-amd64» of 6.1.0-26-cloud-amd64, «amd64» of 6.12.48+deb13-amd64."""
    m = _RELEASE.match(release or "")
    return m.group(1) if m else ""


def _deps(text):
    out = []
    for part in (text or "").split(","):
        for alt in part.split("|"):
            name = alt.strip().split(" ", 1)[0].split("(", 1)[0].strip()
            if name:
                out.append(name)
    return out


def headers_meta(runner, kernel):
    """The headers metapackage of the running kernel's flavour: the installed ``linux-image-*`` metapackage
    that pulls this kernel (or another kernel of its flavour: an upgrade not booted yet), with «image» -> «headers»
    — linux-headers-generic, -virtual, -kvm, -amd64, -cloud-amd64 … None when there is none."""
    res = runner.run(["dpkg-query", "-W", "-f=${Package}\t${Status}\t${Depends}\n", "linux-image-*"])
    metas = []
    for line in (res.out or "").splitlines():
        f = line.split("\t")
        if len(f) < 3 or not f[1].strip().endswith("install ok installed") or not f[0].startswith("linux-image-"):
            continue
        rest = f[0][len("linux-image-"):]
        if not rest or rest[0].isdigit() or rest.startswith(("unsigned-", "extra-")) or rest.endswith("-dbg"):
            continue
        metas.append((rest, _deps(f[2])))
    metas.sort()
    for rest, deps in metas:
        if "linux-image-" + kernel in deps:
            return "linux-headers-" + rest
    fl = flavor(kernel)
    if fl:
        for rest, deps in metas:
            if any(d.startswith("linux-image-") and flavor(d[len("linux-image-"):]) == fl for d in deps):
                return "linux-headers-" + rest
        for rest, _ in metas:
            if rest == fl:
                return "linux-headers-" + rest
    return None


def _record_pkgs(installed):
    """What apt just put on the node for the module (the compiler, dkms, the headers) -> packages.json: menu 15 names
    them for ``apt remove``. ping is not a build tool — not named."""
    from .inbounds import tunnel
    tunnel.record_packages([p for p in installed or [] if p != "iputils-ping"], "awg-kmod")


def kernel_gcc():
    """``gcc-NN`` the running kernel was built with (/proc/version): Kbuild calls exactly it (HWE kernels of
    Ubuntu use a newer gcc than the default one). None if unknown."""
    m = re.search(r"\bgcc-(\d{1,2})\b", util.read_text(paths.P("/proc/version")) or "")
    return "gcc-%s" % m.group(1) if m else None


# ── DKMS ───────────────────────────────────────────────────────────────────

def dkms_status(runner):
    """{"present", "added", "installed": [kernels], "built": [kernels], "foreign": [versions]} of our module."""
    out = {"present": bool(runner.which("dkms")), "added": False, "installed": [], "built": [], "foreign": []}
    if not out["present"]:
        return out
    res = runner.run(["dkms", "status", "-m", NAME])
    for line in (res.out or "").splitlines():
        head, sep, tail = line.rpartition(":")
        if not sep:
            continue
        what = (tail.strip().split() or [""])[0].rstrip(",")
        f = [x.strip() for x in head.replace("/", ",", 1).split(",")]
        if f[0] != NAME or len(f) < 2:
            continue
        if f[1] != VERSION:
            if f[1] not in out["foreign"]:
                out["foreign"].append(f[1])
            continue
        out["added"] = True
        if len(f) >= 3 and f[2]:
            if what == "installed" and f[2] not in out["installed"]:
                out["installed"].append(f[2])
            if what in ("built", "installed") and f[2] not in out["built"]:
                out["built"].append(f[2])
    return out


# Why a build failed, in words (23.10; 20.10: a server of 458 MB, «gcc-13: fatal error: Killed signal terminated
# program cc1», and the person saw «make: Leaving directory»). Servers below 2 GB are not supported (the owner, 23.10).
_OOM = re.compile(r"Killed signal terminated program|\bKilled\s*$|(?i:\bout of memory\b|memory exhausted|"
                  r"cannot allocate memory)", re.M)
_NOSPACE = re.compile(r"No space left on device", re.I)
_SNIPPET = re.compile(r"^\s*\d*\s*\|")       # gcc quotes the source: «  434 |   pr_err("out of memory")»
_ERR_LINE = re.compile(r"\berror:|\bERROR:")
_UNDEF_LINE = re.compile(r"undefined reference|undefined symbol|\bundefined!")    # not «undefined behavior»
_MAKE_ERR = re.compile(r"\*\*\*.*\bError \d+|\bError \d+\s*$")
ERRORS_MAX = 120                # the compiler's (or dkms's) words: with the path they stay within 300 characters
MEM_SUPPORTED_MB = 1800         # MemTotal of a «2 GB» server is 1.8-1.95 GiB: the kernel keeps its part
KEPT_LOG = "awg-kmod-set-make.log"


def _meminfo_mb(key):
    """A line of /proc/meminfo in MB, or None."""
    for line in (util.read_text(paths.P("/proc/meminfo")) or "").splitlines():
        k, _, v = line.partition(":")
        f = v.split()
        if k.strip() == key and f and f[0].isdigit():
            return int(f[0]) // 1024
    return None


def _no_memory(log):
    """The words of a build that ran out of memory: what is free now (the build is gone — what it had), and below
    2 GB — a server from 2 GB recommended; a bigger one — free some memory and try again."""
    avail = _meminfo_mb("MemAvailable")
    mem = (" (%s)" % tr("gw_awgkmod.why.free", mb=avail)) if avail is not None else ""
    total = _meminfo_mb("MemTotal")
    big = total is not None and total >= MEM_SUPPORTED_MB
    return tr("gw_awgkmod.why.oom_busy" if big else "gw_awgkmod.why.oom", mem=mem, log=log)


def _log_reason(text, log, made=None):
    """Why the build failed, from its make.log: out of memory, of disk, the first errors of the compiler or modpost,
    an undefined symbol, make's «Error N»; None when the log says nothing of it. The path of the log comes right after
    the short reason: the engine's record, the panel and the bot keep 300 characters. ``made`` — the make.log DKMS
    wrote when ``log`` is its copy (its directory is the build tree)."""
    said = "\n".join(ln for ln in text.splitlines() if not _SNIPPET.match(ln))
    if _OOM.search(said):
        return _no_memory(log)
    if _NOSPACE.search(said):
        return tr("gw_awgkmod.why.no_space", log=log)
    tree = (os.path.dirname(made or log) + "/", "/var/lib/dkms/%s/%s/build/" % (NAME, VERSION))
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for rx in (_ERR_LINE, _UNDEF_LINE, _MAKE_ERR):
        hits = [ln for ln in lines if rx.search(ln)]
        if hits:
            errors = "; ".join(hits[:2])
            for d in tree:                      # «/var/lib/dkms/…/build/socket.c:434» -> «socket.c:434»
                errors = errors.replace(d, "")
            return tr("gw_awgkmod.why.compile", errors=util.redact(errors)[:ERRORS_MAX], log=log)
    return None


def _kept(log, text):
    """The path the words name. With the set of 3.1 in the sources a failed build is followed by one without it in
    the same build tree (drop_set), which writes make.log anew: the log of this one is kept beside the state of the
    gateway, so the path in set_failed still leads to its errors (REV-kmod3 З2). Not kept — make.log itself."""
    if not set_in_src():
        return log
    keep = os.path.join(paths.gw_var_dir(), KEPT_LOG)
    try:
        util.atomic_write(keep, text, 0o600)
    except OSError:
        return log
    return keep


def _build_error(res):
    """Why ``dkms build`` failed, for people: the reason its make.log gives with the path of the log; a log that
    gives none — dkms's own words and the path; no log — dkms's words."""
    text = (res.out or "") + "\n" + (res.err or "")
    logs = [m.group(1) for m in re.finditer(r"(/\S*make\.log)", text)]
    logs.append(paths.P("/var/lib/dkms/%s/%s/build/make.log" % (NAME, VERSION)))
    for log in logs:
        if os.path.isfile(log):
            body = util.read_text(log) or ""
            shown = _kept(log, body)
            return (_log_reason(body, shown, log)
                    or tr("gw_awgkmod.why.dkms_log", why=_why(res)[:ERRORS_MAX], log=shown))
    return _why(res)


def new_signature(kernel):
    """True/False: the headers of ``kernel`` declare the new signature of setup_udp_tunnel_sock (Debian keeps
    the headers in -common: build/, then source/); None when there are no headers."""
    for sub in ("build", "source"):
        text = util.read_text(os.path.join(kernel_tree(kernel, sub), "include", "net", "udp_tunnel.h"))
        if text is not None:
            return bool(_NEW_SIG.search(text))
    return None


def patched(src=None):
    return PATCHED_MARK in (util.read_text(os.path.join(src or src_dir(), "compat", "compat.h")) or "")


def apply_patch(runner, src):
    """Patch #218 onto the sources (once). -> True if applied now."""
    if patched(src):
        return False
    res = runner.run(["patch", "-d", src, "-p2", "-N", "-s", "-i", os.path.join(src, PATCH_NAME)], timeout=60)
    if not res.ok or not patched(src):
        raise _fail("build", "gw_awgkmod.err.patch218", why=_why(res))
    return True


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(runner, work):
    """The archive of TAG into ``work``, checked against SHA256. -> its path."""
    if not re.match(r"^[0-9a-f]{64}$", SHA256 or ""):
        raise _fail("build", "gw_awgkmod.err.no_sha", tag=TAG)
    dst = os.path.join(work, "%s.tar.gz" % TOP)
    res = runner.run(["curl", "-fsSL", "--retry", "3", "--connect-timeout", "15", "--max-time", "300", "-o", dst,
                      URL], timeout=330)
    if not res.ok or not os.path.isfile(dst):
        raise _fail("build", "gw_awgkmod.err.download", tag=TAG, why=_why(res))
    got = _sha256(dst)
    if got != SHA256:
        raise _fail("build", "gw_awgkmod.err.sha", tag=TAG, got=got, want=SHA256)
    return dst


def unpack(archive, dst):
    """src/ of the archive -> ``dst`` (files 0644, directories 0755). Links, devices, odd paths: refused."""
    prefix = TOP + "/src/"
    n = 0
    with tarfile.open(archive, "r:gz") as tf:
        for m in tf.getmembers():
            if not m.name.startswith(prefix):
                continue
            rel = m.name[len(prefix):].rstrip("/")
            if not rel:
                continue
            parts = rel.split("/")
            if any(p in ("", ".", "..") for p in parts):
                raise _fail("build", "gw_awgkmod.err.tar_path", name=m.name)
            target = os.path.join(dst, *parts)
            if m.isdir():
                if not os.path.isdir(target):
                    os.makedirs(target, 0o755)
                continue
            if not m.isfile():
                raise _fail("build", "gw_awgkmod.err.tar_file", name=m.name)
            d = os.path.dirname(target)
            if not os.path.isdir(d):
                os.makedirs(d, 0o755)
            with open(target, "wb") as out:
                shutil.copyfileobj(tf.extractfile(m), out)
            os.chmod(target, 0o644)
            n += 1
    if not os.path.isfile(os.path.join(dst, "dkms.conf")):
        raise _fail("build", "gw_awgkmod.err.tar_conf")
    for dp, _, _ in os.walk(dst):
        os.chmod(dp, 0o755)
    return n


def fix_dkms_conf(src):
    """PACKAGE_VERSION = the tag, AUTOINSTALL="no" (only our hook builds — with ``|| true``), no REMAKE_INITRD
    (the module is not needed in the initramfs)."""
    p = os.path.join(src, "dkms.conf")
    out, seen = [], set()
    for line in (util.read_text(p) or "").splitlines():
        key = line.split("=", 1)[0].strip()
        if key == "PACKAGE_VERSION":
            line = 'PACKAGE_VERSION="%s"' % VERSION
        elif key == "AUTOINSTALL":
            line = 'AUTOINSTALL="no"'
        elif key == "REMAKE_INITRD":
            continue
        seen.add(key)
        out.append(line)
    if "PACKAGE_NAME" not in seen:
        out.insert(0, 'PACKAGE_NAME="%s"' % NAME)
    if "PACKAGE_VERSION" not in seen:
        out.append('PACKAGE_VERSION="%s"' % VERSION)
    if "AUTOINSTALL" not in seen:
        out.append('AUTOINSTALL="no"')
    util.atomic_write(p, "\n".join(out) + "\n", 0o644)


def _prepare_src(runner, say):
    """Fresh /usr/src/amneziawg-<VERSION> from the pinned archive (built beside it, then renamed in place)."""
    src = src_dir()
    parent = os.path.dirname(src)
    if not os.path.isdir(parent):
        os.makedirs(parent, 0o755)
    work = tempfile.mkdtemp(prefix=".kaskad-%s." % NAME, dir=parent)
    try:
        say(tr("gw_awgkmod.say.download", tag=TAG))
        archive = fetch(runner, work)
        new = os.path.join(work, "src")
        os.makedirs(new, 0o755)
        unpack(archive, new)
        fix_dkms_conf(new)
        if os.path.isdir(src):
            shutil.rmtree(src)
        os.rename(new, src)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return src


def set_in_src(src=None):
    """The sources carry the set of 3.1 (its mark in src/version.h)."""
    return SET_ID in (util.read_text(os.path.join(src or src_dir(), "version.h")) or "")


def ko_has_set(runner, kernel):
    """The module DKMS installed for ``kernel`` carries the set (``modinfo -k`` reads the .ko on disk)."""
    res = runner.run(["modinfo", "-k", kernel, "-F", "version", NAME])
    return res.ok and SET_ID in (res.out or "")


def apply_set(runner, src):
    """The set of 3.1 onto the sources, all or nothing: ``--dry-run`` first. -> True if applied now."""
    if set_in_src(src):
        return False
    p = os.path.join(src, SET_NAME)
    shutil.copyfile(set_source(), p)
    os.chmod(p, 0o644)
    res = runner.run(["patch", "-d", src, "-p2", "-N", "-s", "--dry-run", "-i", p], timeout=60)
    if not res.ok:
        raise _fail("build", "gw_awgkmod.err.set_dry", why=_why(res))
    res = runner.run(["patch", "-d", src, "-p2", "-N", "-s", "-i", p], timeout=60)
    if not res.ok or not set_in_src(src):
        raise _fail("build", "gw_awgkmod.err.set_apply", why=_why(res))
    return True


def revert_set(runner, src):
    """The set off the sources again (``patch -R``, after a ``--dry-run``). -> True if taken off now."""
    if not set_in_src(src):
        return False
    p = os.path.join(src, SET_NAME)
    if not os.path.isfile(p):
        shutil.copyfile(set_source(), p)
    for extra in (["--dry-run"], []):
        res = runner.run(["patch", "-d", src, "-p2", "-R", "-s"] + extra + ["-i", p], timeout=60)
        if not res.ok:
            raise _fail("build", "gw_awgkmod.err.set_revert_why", why=_why(res))
    if set_in_src(src):
        raise _fail("build", "gw_awgkmod.err.set_revert")
    return True


def note_set_failed(runner, kernel, why):
    doc = load()
    doc["set_failed"] = {"kernel": kernel, "reason": util.redact(str(why))[:300], "at": util.now_iso()}
    save(doc)
    _log("gw_awgkmod.log.set_failed", kernel=kernel, set=SET_ID, why=why)


def _dkms_build(runner, kernel, say):
    """dkms add (once), build and install for ``kernel``; KaskadError when it does not build."""
    if not dkms_status(runner)["added"]:
        res = runner.run(["dkms", "add", "-m", NAME, "-v", VERSION], timeout=300)
        if not res.ok and "already" not in ((res.out or "") + (res.err or "")):
            raise _fail("build", "gw_awgkmod.err.dkms_add", why=_why(res))
    say(tr("gw_awgkmod.say.build", kernel=kernel))
    res = runner.run(["dkms", "build", "-m", NAME, "-v", VERSION, "-k", kernel], timeout=BUILD_TIMEOUT)
    if not res.ok and "already built" not in ((res.out or "") + (res.err or "")):
        raise _fail("build", "gw_awgkmod.err.build", kernel=kernel, why=_build_error(res))
    res = runner.run(["dkms", "install", "-m", NAME, "-v", VERSION, "-k", kernel], timeout=600)
    if not res.ok and kernel not in dkms_status(runner)["installed"]:
        raise _fail("build", "gw_awgkmod.err.dkms_install", why=_why(res))


def _dkms_drop(runner, kernel):
    """The module of ``kernel`` out of DKMS (its .ko too): the next build is from the sources as they are now."""
    st = dkms_status(runner)
    if kernel in st["built"] or kernel in st["installed"]:
        runner.run(["dkms", "remove", "-m", NAME, "-v", VERSION, "-k", kernel], timeout=600)


def _rebuild(runner, kernel, say):
    _dkms_drop(runner, kernel)
    _dkms_build(runner, kernel, say)


def drop_set(runner, kernel, say, why):
    """The set did not build or failed its self-test: off the sources, remembered (set_failed), the module of
    ``kernel`` rebuilt without it. KaskadError when even that does not build."""
    say(tr("gw_awgkmod.say.set_dropped", why=why))
    revert_set(runner, src_dir())
    note_set_failed(runner, kernel, why)
    _rebuild(runner, kernel, say)


def _build(runner, kernel, say):
    """The module for ``kernel`` built and installed by DKMS, with the set of 3.1 when it applies and builds (a set
    that does not — off again, remembered, the module without it). -> {"built": bool — built now, "set": bool — the
    module of ``kernel`` carries the set}."""
    src = src_dir()
    st = dkms_status(runner)
    if not os.path.isfile(os.path.join(src, "dkms.conf")):
        if st["added"]:                            # registered, its sources gone: start clean
            runner.run(["dkms", "remove", "-m", NAME, "-v", VERSION, "--all"], timeout=300)
        _prepare_src(runner, say)
    for name, origin in ((PATCH_NAME, patch_source()), (SET_NAME, set_source())):
        shutil.copyfile(origin, os.path.join(src, name))           # the kernel hook needs them for new kernels
        os.chmod(os.path.join(src, name), 0o644)
    if new_signature(kernel):
        say(tr("gw_awgkmod.say.patch218", kernel=kernel))
        if apply_patch(runner, src):
            _log("gw_awgkmod.log.patch218", kernel=kernel)
    if not set_in_src(src):
        say(tr("gw_awgkmod.say.set_apply", set=SET_ID))
        try:
            apply_set(runner, src)
            _log("gw_awgkmod.log.set_applied", set=SET_ID)
        except KaskadError as e:
            note_set_failed(runner, kernel, e)
            say(tr("gw_awgkmod.say.set_not_applied", why=e))
    if set_in_src(src):
        if kernel in dkms_status(runner)["installed"] and ko_has_set(runner, kernel):
            say(tr("gw_awgkmod.say.built_set", version=VERSION, set=SET_ID, kernel=kernel))
            return {"built": False, "set": True}
        try:
            _rebuild(runner, kernel, say)
        except KaskadError as e:
            drop_set(runner, kernel, say, e)
            return {"built": True, "set": False}
        doc = load()
        if doc.pop("set_failed", None) is not None:
            save(doc)
        return {"built": True, "set": True}
    if kernel in dkms_status(runner)["installed"]:
        say(tr("gw_awgkmod.say.built", version=VERSION, kernel=kernel))
        return {"built": False, "set": False}
    _rebuild(runner, kernel, say)
    return {"built": True, "set": False}


def _kver(release):
    return tuple(int(x) for x in re.findall(r"\d+", release or ""))


def build_newer(runner, running, say):
    """Kernels installed after the running one, with their headers (an upgrade not booted yet): the module is
    built for them too, so the next boot finds it (the kernel hook ran before the module existed). Patch #218
    when such a kernel needs it. A failure is only reported. -> (built, failed)."""
    built, failed = [], []
    base = paths.P("/lib/modules")
    try:
        names = sorted(os.listdir(base), key=_kver)
    except OSError:
        return built, failed
    have = dkms_status(runner)["installed"]
    for k in names:
        # built before (maybe without the set of 3.1): left as it is — the update after the reboot into it puts
        # the set on (upgrade), and a module that does not build with the set there would be lost here
        if k == running or k in have or _kver(k) <= _kver(running) or not os.path.exists(kernel_tree(k)):
            continue
        say(tr("gw_awgkmod.say.build_newer", kernel=k))
        try:
            if new_signature(k) and apply_patch(runner, src_dir()):
                _log("gw_awgkmod.log.patch218", kernel=k)
            res = runner.run(["dkms", "build", "-m", NAME, "-v", VERSION, "-k", k], timeout=BUILD_TIMEOUT)
            if not res.ok and "already built" not in ((res.out or "") + (res.err or "")):
                raise KaskadError(_build_error(res))
            runner.run(["dkms", "install", "-m", NAME, "-v", VERSION, "-k", k], timeout=600)
        except KaskadError as e:
            _log("gw_awgkmod.log.newer_failed", kernel=k, why=e)
        (built if k in dkms_status(runner)["installed"] else failed).append(k)
    return built, failed


# ── hooks and the boot unit ────────────────────────────────────────────────

def hook_text():
    """sh for run-parts: build the module for the kernel in $1; patch #218 when that kernel needs it; the sources
    keep the set of 3.1 — when the module does not build with it there, the set comes off and it is built without
    (3.1 on amneziawg-go after the reboot). Exit 0 always."""
    v = {"name": NAME, "ver": VERSION, "src": src_dir(), "mods": paths.P("/lib/modules"), "log": paths.log(),
         "mark": PATCHED_MARK, "patch": PATCH_NAME, "re": _HOOK_RE, "set": SET_NAME, "setid": SET_ID}
    lang = saved_lang()                        # the lines go to the journal: the saved language of the server
    for k in ("patched", "no_set", "built", "not_built"):
        v["say_" + k] = tr("gw_awgkmod.hook." + k, lang, kernel="$ver")
    return """#!/bin/sh
# Kaskad 3: the AmneziaWG kernel module (DKMS %(name)s/%(ver)s, AUTOINSTALL="no") for a new kernel. run-parts
# calls it with the kernel's version when the kernel (postinst.d) and its headers (header_postinst.d) are
# installed; whichever comes last builds. It never fails the kernel's installation: a module that did not build
# means amneziawg-go after the reboot (kaskadctl gw accel status says why). Written by kaskadctl gw accel on.
ver="${1:-}"
src="%(src)s"
[ -n "$ver" ] && [ -d "$src" ] || exit 0
command -v dkms >/dev/null 2>&1 || exit 0
[ -e "%(mods)s/$ver/build" ] || exit 0
say() { { printf '[%%s] AWG KMOD: %%s\\n' "$(date '+%%Y-%%m-%%d %%H:%%M:%%S')" "$1" >> "%(log)s"; } 2>/dev/null; }
if ! grep -q %(mark)s "$src/compat/compat.h" 2>/dev/null && [ -f "$src/%(patch)s" ]; then
    for h in "%(mods)s/$ver/build/include/net/udp_tunnel.h" "%(mods)s/$ver/source/include/net/udp_tunnel.h"; do
        [ -f "$h" ] || continue
        if grep -Eq '%(re)s' "$h"; then
            if patch -d "$src" -p2 -N -s -i "$src/%(patch)s" >/dev/null 2>&1; then
                say "%(say_patched)s"
            fi
        fi
        break
    done
fi
dkms build -m %(name)s -v %(ver)s -k "$ver" >/dev/null 2>&1
dkms install -m %(name)s -v %(ver)s -k "$ver" >/dev/null 2>&1
if ! dkms status -m %(name)s -v %(ver)s -k "$ver" 2>/dev/null | grep -q installed \
    && grep -q '%(setid)s' "$src/version.h" 2>/dev/null && [ -f "$src/%(set)s" ] \
    && patch -d "$src" -p2 -R -s --dry-run -i "$src/%(set)s" >/dev/null 2>&1 \
    && patch -d "$src" -p2 -R -s -i "$src/%(set)s" >/dev/null 2>&1; then
    say "%(say_no_set)s"
    dkms remove -m %(name)s -v %(ver)s -k "$ver" >/dev/null 2>&1
    dkms build -m %(name)s -v %(ver)s -k "$ver" >/dev/null 2>&1
    dkms install -m %(name)s -v %(ver)s -k "$ver" >/dev/null 2>&1
fi
if dkms status -m %(name)s -v %(ver)s -k "$ver" 2>/dev/null | grep -q installed; then
    say "%(say_built)s"
else
    say "%(say_not_built)s"
fi
exit 0
""" % v


def boot_unit_text():
    return "\n".join([
        "[Unit]",
        "Description=Kaskad 3 gateway: AmneziaWG kernel module (load it, or put the lines on amneziawg-go)",
        "After=local-fs.target systemd-modules-load.service",
        "Before=kaskad-gw-awg1.service kaskad-gw-awg2.service kaskad-gw-awg3.service kaskad-egress.service",
        "ConditionPathExists=%s" % state_path(),
        "",
        "[Service]",
        "Type=oneshot",
        "RemainAfterExit=yes",
        "ExecStart=-%s gw accel boot --quiet" % paths.kaskadctl(),
        "",
        "[Install]",
        "WantedBy=multi-user.target",
        "",
    ])


def write_hooks():
    text = hook_text()
    for p in hook_paths():
        d = os.path.dirname(p)
        if not os.path.isdir(d):
            os.makedirs(d, 0o755)
        if util.read_text(p) != text:
            util.atomic_write(p, text, 0o755)
        os.chmod(p, 0o755)


def install_boot_unit(runner):
    if gsys.write_unit(BOOT_UNIT, boot_unit_text()):
        runner.run(["systemctl", "daemon-reload"])
    gsys.enable(runner, BOOT_UNIT, check=False)


# ── the self-test ──────────────────────────────────────────────────────────

def _test_conf(priv, port, params, peer_pub, psk, allowed, endpoint):
    p = awgkeys.normalize_params(params)
    out = ["[Interface]", "PrivateKey = %s" % priv, "ListenPort = %d" % port]
    # 3.1: with its own fields — the header protection key, the trailers, the timers (empty for the older lines)
    out += ["%s = %s" % (k, p[k]) for k in awgkeys.AWG_IFACE_KEYS + awgkeys.AWG3_KEYS if p[k]]
    out += ["", "[Peer]", "PublicKey = %s" % peer_pub, "PresharedKey = %s" % psk, "AllowedIPs = %s" % allowed,
            "Endpoint = %s" % endpoint]
    return "\n".join(out) + "\n"


def _netns_clean(runner):
    for ns in NETNS:
        res = runner.run(["ip", "netns", "pids", ns])
        pids = [p for p in (res.out or "").split() if p.isdigit()] if res.ok else []
        if pids:
            runner.run(["kill"] + pids)
    for ns in reversed(NETNS):
        runner.run(["ip", "netns", "del", ns])


def _case(runner, core, tmp, i, line, peer, starter="kernel"):
    """One pair: a module interface in kt1 (socket in kt0) against a module interface or amneziawg-go in kt0;
    ``starter`` "kernel" — the module interface pings first (it starts the handshake: the client), "peer" — the
    other side does. -> (ok, detail)."""
    kt0, kt1 = NETNS
    awg, go = os.path.join(core, "awg"), os.path.join(core, "amneziawg-go")
    a, b = "kkt%da" % i, "kkt%db" % i
    net = "10.199.%d" % i
    pa, pb = 51000 + 2 * i, 51001 + 2 * i
    params = awgkeys.gen_params(line)
    bad = awgkeys.validate_params(line, params)
    if bad:
        return False, tr("gw_awgkmod.st.params", line=line, why="; ".join(bad))
    ka, kb, psk = awgkeys.genkey(), awgkeys.genkey(), awgkeys.genpsk()
    ca, cb = os.path.join(tmp, a + ".conf"), os.path.join(tmp, b + ".conf")
    util.atomic_write(ca, _test_conf(ka, pa, params, awgkeys.pubkey(kb), psk, net + ".2/32", "127.0.0.1:%d" % pb), 0o600)
    util.atomic_write(cb, _test_conf(kb, pb, params, awgkeys.pubkey(ka), psk, net + ".1/32", "127.0.0.1:%d" % pa), 0o600)
    steps = [(["ip", "-n", kt0, "link", "add", "dev", a, "type", NAME], "gw_awgkmod.st.iface"),
             (["ip", "-n", kt0, "link", "set", "dev", a, "netns", kt1], "gw_awgkmod.st.netns"),
             (["ip", "netns", "exec", kt1, awg, "setconf", a, ca], "gw_awgkmod.st.setconf"),
             (["ip", "-n", kt1, "address", "add", net + ".1/24", "dev", a], "gw_awgkmod.st.addr"),
             (["ip", "-n", kt1, "link", "set", "dev", a, "up"], "gw_awgkmod.st.up")]
    if peer == "kernel":
        steps.append((["ip", "-n", kt0, "link", "add", "dev", b, "type", NAME], "gw_awgkmod.st.iface2"))
    else:
        steps.append((["ip", "netns", "exec", kt0, go, b], "gw_awgkmod.st.go"))
    for argv, what in steps:
        res = runner.run(argv, timeout=30)
        if not res.ok:
            return False, "%s: %s" % (tr(what), _why(res))
    if peer != "kernel":
        for _ in range(50):
            if os.path.exists(uapi_sock(b)):
                break
            _sleep(0.1)
        else:
            return False, tr("gw_awgkmod.st.go_sock", path=uapi_sock(b))
    for argv, what in ((["ip", "netns", "exec", kt0, awg, "setconf", b, cb], "gw_awgkmod.st.setconf2"),
                       (["ip", "-n", kt0, "address", "add", net + ".2/24", "dev", b], "gw_awgkmod.st.addr2"),
                       (["ip", "-n", kt0, "link", "set", "dev", b, "up"], "gw_awgkmod.st.up2")):
        res = runner.run(argv, timeout=30)
        if not res.ok:
            return False, "%s: %s" % (tr(what), _why(res))
    ns, dst = (kt1, net + ".2") if starter == "kernel" else (kt0, net + ".1")
    res = runner.run(["ip", "netns", "exec", ns, "ping", "-c", "3", "-i", "0.2", "-W", "2", dst], timeout=30)
    if not res.ok:
        return False, tr("gw_awgkmod.st.ping", why=_why(res))
    res = runner.run(["ip", "netns", "exec", kt1, awg, "show", a, "latest-handshakes"], timeout=30)
    ts = [f.split()[1] for f in (res.out or "").splitlines() if len(f.split()) >= 2]
    if not res.ok or not ts or not ts[0].isdigit() or int(ts[0]) <= 0:
        return False, tr("gw_awgkmod.st.no_handshake", iface=a)
    return True, tr("gw_awgkmod.st.ok")


def _title(line, peer, starter):
    if peer == "kernel":
        return tr("gw_awgkmod.title.kk", line=line)
    if starter == "kernel" and line in ("3.1", "1.0"):
        return tr("gw_awgkmod.title.kclient", line=line)
    if starter != "kernel":
        return tr("gw_awgkmod.title.goclient", line=line)
    return tr("gw_awgkmod.title.kgo", line=line)


def self_test(runner, core, say=None, k31=False):
    """Kernel <-> kernel and kernel <-> amneziawg-go with the parameters of lines 1.5 and 2.0 (handshake + ping);
    ``k31`` — the module carries the set: its mark in the module in memory, then 3.1 in every role and the module
    client of 1.0 (SELFTEST_31, the role of a leg included: legs of 3.1 and 1.x go onto the module with the set; the
    module client of 1.5 with I1 against amneziawg-go is a base case; SELFTEST_INFO — told, never fatal). -> {"ok", "detail", "cases": [{"line",
    "peer", "ok", "detail", "info"?}], "notes", "kernel", "at"}."""
    say = say or (lambda text: None)
    plan = [(line, peer, "kernel") for line, peer in SELFTEST]
    if k31:
        plan += [("set", "", "")] + list(SELFTEST_31)
    of_set = len(plan) - (len(SELFTEST_31) + 1 if k31 else 0)     # plan[of_set:] — the cases of the set
    cases = []
    tmp = tempfile.mkdtemp(prefix="kaskad-awgkmod-test.")
    try:
        os.chmod(tmp, 0o700)
        _netns_clean(runner)
        for argv in (["ip", "netns", "add", NETNS[0]], ["ip", "netns", "add", NETNS[1]],
                     ["ip", "-n", NETNS[0], "link", "set", "dev", "lo", "up"],
                     ["ip", "-n", NETNS[1], "link", "set", "dev", "lo", "up"]):
            res = runner.run(argv)
            if not res.ok:
                cases.append({"line": "", "peer": "", "ok": False, "detail": "netns: %s" % _why(res)})
                break
        else:
            for i, (line, peer, starter) in enumerate(plan, 1):
                info = (line, peer, starter) in SELFTEST_INFO
                if line == "set":
                    ver = module_version()
                    ok = SET_ID in ver
                    cases.append({"line": "set", "peer": "", "ok": ok, "set": True,
                                  "detail": tr("gw_awgkmod.st.set_mark", set=SET_ID) if ok else
                                  tr("gw_awgkmod.st.no_set_mark", set=SET_ID, version=ver or "—")})
                    if not ok:
                        break
                    continue
                title = _title(line, peer, starter)
                say(tr("gw_awgkmod.say.selftest", title=title))
                try:
                    ok, detail = _case(runner, core, tmp, i, line, peer, starter)
                finally:
                    runner.run(["ip", "-n", NETNS[1], "link", "del", "dev", "kkt%da" % i])
                    runner.run(["ip", "-n", NETNS[0], "link", "del", "dev", "kkt%db" % i])
                case = {"line": line, "peer": peer, "ok": ok, "detail": "%s: %s" % (title, detail)}
                if i > of_set:
                    case["set"] = True
                if info:
                    case["info"] = True
                cases.append(case)
                if not ok and not info:
                    break
    finally:
        _netns_clean(runner)
        shutil.rmtree(tmp, ignore_errors=True)
    need = [c for c in cases if not c.get("info")]
    ok = bool(cases) and all(c["ok"] for c in need) and len(need) == len(plan) - len(
        [x for x in plan if x in SELFTEST_INFO])
    bad = [c["detail"] for c in need if not c["ok"]]
    notes = [tr("gw_awgkmod.st.info", detail=c["detail"]) for c in cases if c.get("info") and not c["ok"]]
    detail = bad[0] if bad else tr("gw_awgkmod.st.passed",
                                   extra=tr("gw_awgkmod.st.passed_set", set=SET_ID) if k31 else "")
    return {"ok": ok, "detail": detail, "cases": cases, "notes": notes, "kernel": running_kernel(runner),
            "at": util.now_iso()}


def set_case_failed(res):
    """The self-test failed in a case of the set (its mark, 3.1, the leg of 1.0), the base lines passed."""
    bad = [c for c in res.get("cases") or [] if not c["ok"] and not c.get("info")]
    return bool(bad) and all(c.get("set") for c in bad)


# ── switching the lines ────────────────────────────────────────────────────

def _adapter(kind):
    from . import registry
    ad = registry.get(kind)
    return ad() if isinstance(ad, type) else ad


def switch_inbounds(runner, restart=True):
    """Units of the AWG inbounds to the mode of now (adapter.unit_texts decides); a running one whose unit
    changed restarts — the interface is recreated, one short reconnect. A module interface that does not come
    up -> mark_failed and amneziawg-go at once. -> [{"kind", "core", "was", "restarted"}]."""
    from .inbounds import tunnel
    out = []
    for kind in AWG_KINDS:
        ad = _adapter(kind)
        if ad is None or not tunnel.unit_installed(ad.unit):
            continue
        was = ad.unit_core()
        changed = tunnel.refresh_units(runner, ad.unit_texts(runner))
        restarted = False
        if changed and restart and tunnel.is_active(runner, ad.unit):
            res = runner.run(["systemctl", "restart", ad.unit], timeout=120)
            restarted = True
            tunnel.note_restart(kind, "engine")
            if not res.ok and ad.unit_core() == "kernel":
                mark_failed(runner, kind, _why(res))
                tunnel.refresh_units(runner, ad.unit_texts(runner))
                runner.run(["systemctl", "restart", ad.unit], timeout=120)
        out.append({"kind": kind, "core": ad.unit_core(), "was": was, "restarted": restarted})
    return out


def switch_legs(runner, timeout=120):
    """Legs of the pool to the mode of now: ``EGRESS_AWG_REMODE=1 gokaskad --egress-awg-heal`` recreates only the
    legs whose mode changed (their units kaskad-leg@kawgN), under the legs' lock of the gateway's watchdog.
    -> {"ran", "ok"}."""
    try:
        legs = [e for e in exits.pool(runner, roles=exits.ROLES) if e.get("kind") == "awg"]
    except KaskadError:
        return {"ran": False, "ok": False}
    if not legs:
        return {"ran": False, "ok": True}
    lock = exits._legs_lock()
    util.ensure_dir(os.path.dirname(lock), 0o700)      # /run/kaskad above it stays passable (nginx → the panel)
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        deadline = time.time() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.time() >= deadline:
                    return {"ran": False, "ok": False}
                _sleep(0.2)
        res = runner.run(["env", "EGRESS_AWG_REMODE=1", exits._script(runner), "--egress-awg-heal"],
                         timeout=exits.HEAL_TIMEOUT)
        return {"ran": True, "ok": res.ok}
    finally:
        os.close(fd)


# ── on / off / boot ────────────────────────────────────────────────────────

def _core(runner):
    from .inbounds import awg as awgin
    return awgin.install_core(runner)


class _Lock(object):
    """One build of the module at a time (``gw accel on``, the engine switch, the update in its transient unit)."""

    def __enter__(self):
        d = os.path.dirname(lock_path())
        if not os.path.isdir(d):
            os.makedirs(d, 0o700)
        self.fd = os.open(lock_path(), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(self.fd)
            raise KaskadError(tr("gw_awgkmod.err.busy"))
        return self

    def __exit__(self, *exc):
        os.close(self.fd)
        return False


def _reload(runner, say):
    """The module in memory is not the one just built (the set of 3.1 came or went): every line onto amneziawg-go
    for a moment (they keep working), the old module out of memory. -> True; False — it did not unload (an
    interface not of Kaskad holds it, or it is busy): the lines go back onto it, the new one comes at the reboot."""
    foreign = [i for i in kernel_ifaces(runner) if not i.startswith(("kgw-awg", "kawg"))]
    if foreign:
        return False
    say(tr("gw_awgkmod.say.reload"))
    doc = load()
    was = bool(doc.get("enabled"))
    doc["enabled"] = False
    save(doc)
    switch(runner, say)
    for i in [i for i in kernel_ifaces(runner) if i.startswith(("kgw-awg", "kawg"))]:
        runner.run(["ip", "link", "delete", "dev", i])
    if _unload(runner):
        return True
    if was:
        doc = load()
        doc["enabled"] = True
        save(doc)
        switch(runner, say)
    return False


def install(runner, progress=None, force=False):
    """``gw accel on``: build and load the module (with the set of 3.1, kernel_31), check it, then move the lines.
    KaskadError (for people) when it cannot; acceleration then stays off (everything on amneziawg-go) and the reason
    is stored. A line the module cannot take even with the set (a leg of 3.1 or 1.x) next to the lines it would take
    — mixing, one direction slower: ConflictError (code 3) before anything is installed, unless ``force`` (then a
    warning). The set that does not build or fails its self-test comes off — the module without it; a 3.1 inbound
    then is mixing too (the same refusal, after the build). Also the update of a module without the set: it is
    rebuilt and reloaded (_reload)."""
    from . import awgengine
    say = progress or (lambda text: None)
    mixed = awgengine.mix(runner, k31=True)
    if mixed and not force:
        raise ConflictError(tr("gw_awgkmod.err.mix", mix=" ".join(mixed)))
    host = supported_host(runner)
    if not host["ok"]:
        raise UnsupportedError(tr("gw_awgkmod.err.host", why=host["why"]), data={"kind": "host"})
    kernel = running_kernel(runner)
    if not kernel:
        raise KaskadError(tr("gw_awgkmod.err.no_kernel"))
    st = dkms_status(runner)
    if st["foreign"]:
        raise _fail("build", "gw_awgkmod.err.foreign_dkms", dkms=", ".join("%s/%s" % (NAME, v) for v in st["foreign"]))
    doc = load()
    if module_loaded() and kernel not in st["installed"] and not doc.get("kernels"):
        raise KaskadError(tr("gw_awgkmod.err.foreign_loaded"))
    tested, notes = None, []
    with _Lock():
        try:
            core = _core(runner)
            pkgs = list(APT_BASE)
            gcc = kernel_gcc()
            if gcc and gcc not in pkgs:
                pkgs.append(gcc)
            if not runner.which("ping"):
                pkgs.append("iputils-ping")
            say(tr("gw_awgkmod.say.packages", pkgs=", ".join(pkgs)))
            _record_pkgs(gsys.apt_install(runner, pkgs, timeout=APT_TIMEOUT))
            meta = headers_meta(runner, kernel)
            heads = ["linux-headers-%s" % kernel] + ([meta] if meta else [])
            say(tr("gw_awgkmod.say.headers", pkgs=", ".join(heads)))
            try:
                _record_pkgs(gsys.apt_install(runner, heads, timeout=APT_TIMEOUT))
            except KaskadError as e:
                raise _fail("headers", "gw_awgkmod.err.headers", kernel=kernel, why=e)
            if not os.path.exists(kernel_tree(kernel)):
                raise _fail("headers", "gw_awgkmod.err.no_tree", path=kernel_tree(kernel))
            k31 = _build(runner, kernel, say)["set"]
            while True:
                if not k31:
                    mixed = awgengine.mix(runner, k31=False)
                    if mixed and not force:
                        raise ConflictError(tr("gw_awgkmod.err.set_mix",
                                               why=(load().get("set_failed") or {}).get("reason") or "—",
                                               mix=" ".join(mixed)))
                if module_loaded() and set_loaded() != k31 and not _reload(runner, say):
                    if enabled():
                        return _old_module_stays(runner, say, kernel, mixed)
                    raise KaskadError(tr("gw_awgkmod.err.no_unload"))
                say(tr("gw_awgkmod.say.load"))
                ok, why = load_module(runner)
                if not ok:
                    raise _fail("build", "gw_awgkmod.err.not_loaded", why=why)
                write_hooks()
                install_boot_unit(runner)
                res = self_test(runner, core, say, k31=k31)
                tested = {k: res[k] for k in ("ok", "detail", "kernel", "at")}
                notes = res.get("notes") or []
                if res["ok"] or not k31 or not set_case_failed(res):
                    break
                drop_set(runner, kernel, say, tr("gw_awgkmod.selftest_why", detail=res["detail"]))
                k31 = False
            if not res["ok"]:
                runner.run(["modprobe", "-r", NAME])
                raise _fail("selftest", "gw_awgkmod.err.selftest", detail=res["detail"])
        except KaskadError as e:
            doc = load()
            was = bool(doc.get("enabled"))
            doc.update(enabled=False, error=util.redact(str(e))[:500])
            if tested:
                doc["selftest"] = tested
            save(doc)
            _log("gw_awgkmod.log.not_enabled", why=e)
            if was:                                 # «on» again failed: the lines that ran on the module go back now
                try:
                    switch(runner, say)
                except KaskadError:
                    pass
            raise
        newer, missed = build_newer(runner, kernel, say)
    doc = load()
    kernels = sorted(set((doc.get("kernels") or []) + [kernel] + newer), key=_kver)
    doc.update(enabled=True, error=None, fallback=None, failed={}, kernels=kernels, patched=patched(),
               selftest=tested, headers_meta=meta)
    save(doc)
    with util.journal_lang():
        _log("gw_awgkmod.log.enabled", version=VERSION, kernel=kernel,
             set=tr("gw_awgkmod.log.with_set", set=SET_ID) if k31 else tr("gw_awgkmod.log.without_set"))
    out = switch(runner, say)
    out["set"] = k31
    if mixed:
        out["warnings"].append(tr("gw_awgkmod.warn.forced", mix=" ".join(mixed)))
    if not k31:
        f = load().get("set_failed") or {}
        out["warnings"].append(tr("gw_awgkmod.warn.set_failed", why=f.get("reason") or "—"))
    out["warnings"] += notes
    for k in missed:
        out["warnings"].append(tr("gw_awgkmod.warn.newer_missed", kernel=k))
    return out


def _old_module_stays(runner, say, kernel, mixed):
    """_reload could not unload the module in memory: it stays (the lines are back on it), the module just built
    comes at the next boot."""
    _log("gw_awgkmod.log.old_stays", kernel=kernel)
    out = {"ok": True, "inbounds": [], "legs": None, "set": set_loaded(),
           "warnings": [tr("gw_awgkmod.warn.old_stays")]}
    if mixed:
        out["warnings"].append(tr("gw_awgkmod.warn.forced", mix=" ".join(mixed)))
    return out


def upgrade(runner, progress=None):
    """After an update of Kaskad (k3_post_update -> ``kaskadctl gw accel upgrade`` in a transient unit): a node on the
    module whose module in memory lacks the set of 3.1 gets it — ``install`` with its progress and its way back (the
    set that does not build or pass — the module without it; anything else — amneziawg-go and the reason in the
    engine's record). Nothing when acceleration is off, the module is not in memory (fallback), it carries the set,
    or the set already failed on this kernel. -> {"ran", "why"?, ...install}."""
    from . import awgengine
    doc = load()
    if not doc.get("enabled"):
        return {"ok": True, "ran": False, "why": tr("gw_awgkmod.up.off")}
    if not module_loaded():
        return {"ok": True, "ran": False, "why": tr("gw_awgkmod.up.not_loaded")}
    if set_loaded():
        return {"ok": True, "ran": False, "why": tr("gw_awgkmod.up.has_set", set=SET_ID)}
    kernel = running_kernel(runner)
    f = doc.get("set_failed") or {}
    if f.get("kernel") == kernel:
        return {"ok": True, "ran": False, "why": tr("gw_awgkmod.up.set_failed", why=f.get("reason"))}
    _log("gw_awgkmod.log.upgrade")
    force = awgengine.record(awgengine._load())["force"]
    try:
        out = install(runner, progress=progress, force=force)
    except KaskadError as e:
        awgengine.save_record(reason=util.redact(str(e))[:300], code=awgengine._code_of(e), pending=False)
        raise
    return dict(out, ran=True)


def switch(runner, say=None):
    """Both sides of the switch (after on / off): inbounds under the state lock, then the legs."""
    say = say or (lambda text: None)
    out = {"ok": True, "inbounds": [], "legs": None, "warnings": []}
    say(tr("gw_awgkmod.say.inbounds"))
    try:
        with state.locked():
            out["inbounds"] = switch_inbounds(runner)
    except KaskadError as e:
        out["warnings"].append(tr("gw_awgkmod.warn.inbounds_later", why=e))
    say(tr("gw_awgkmod.say.legs"))
    out["legs"] = switch_legs(runner)
    if out["legs"]["ran"] and not out["legs"]["ok"]:
        out["warnings"].append(tr("gw_awgkmod.warn.legs_partly"))
    return out


def disable(runner, progress=None):
    """The engine «программа» (``gw awg engine program``): every line back on amneziawg-go, the module unloaded — but
    built and kept in DKMS with its kernel hooks, so the way back to the module is a minute, not a build from GitHub
    (``gw accel off`` removes it all)."""
    say = progress or (lambda text: None)
    doc = load()
    doc.update(enabled=False, fallback=None)
    save(doc)
    res = switch(runner, say)
    for i in [i for i in kernel_ifaces(runner) if i.startswith(("kgw-awg", "kawg"))]:
        runner.run(["ip", "link", "delete", "dev", i])
    left = kernel_ifaces(runner)
    if module_loaded() and not left and not _unload(runner):
        res["warnings"].append(tr("gw_awgkmod.warn.not_unloaded"))
    _log("gw_awgkmod.log.disabled")
    return res


def kernel_ifaces(runner):
    """Names of the interfaces of the module now (``ip -o link show type amneziawg``)."""
    res = runner.run(["ip", "-o", "link", "show", "type", NAME])
    names = []
    for line in (res.out if res.ok else "").splitlines():
        parts = line.split(":", 2)
        if len(parts) >= 2:
            names.append(parts[1].strip().split("@", 1)[0])
    return names


def remove(runner, progress=None):
    """``gw accel off``: every line back on amneziawg-go first, then the module, its sources, hooks and the boot
    unit away. Packages (dkms, gcc, make, headers) stay."""
    say = progress or (lambda text: None)
    doc = load()
    doc["enabled"] = False
    save(doc)                                   # from here every decision is amneziawg-go
    res = switch(runner, say)
    ours = [i for i in kernel_ifaces(runner) if i.startswith(("kgw-awg", "kawg"))]
    for i in ours:                              # a leg or an inbound the switch could not move: closed, not lost
        runner.run(["ip", "link", "delete", "dev", i])
    left = kernel_ifaces(runner)
    if left:
        res["warnings"].append(tr("gw_awgkmod.warn.foreign_ifaces", ifaces=", ".join(left)))
    # выгрузить ДО dkms remove: после него modprobe -r не находит модуль («Module not found», стенд T1 2.1), а
    # только что удалённые интерфейсы отпускают модуль не сразу (стенд T-G1, F-1) — rmmod с паузами
    if module_loaded() and not left and not _unload(runner):
        res["warnings"].append(tr("gw_awgkmod.warn.not_unloaded"))
    if dkms_status(runner)["added"]:
        say(tr("gw_awgkmod.say.dkms_remove"))
        runner.run(["dkms", "remove", "-m", NAME, "-v", VERSION, "--all"], timeout=600)
    for p in hook_paths():
        try:
            os.unlink(p)
        except OSError:
            pass
    if os.path.exists(boot_unit_path()):
        gsys.remove_unit(runner, BOOT_UNIT)
    shutil.rmtree(src_dir(), ignore_errors=True)
    try:
        os.unlink(state_path())
    except OSError:
        pass
    _log("gw_awgkmod.log.removed")
    return res


def _fallback_reason(runner, kernel, why):
    if kernel not in dkms_status(runner)["installed"]:
        return tr("gw_awgkmod.fb.not_built", kernel=kernel)
    return tr("gw_awgkmod.fb.not_loaded", why=why)


def boot(runner):
    """At boot (kaskad-gw-awgkmod.service, before the inbounds and the legs): load the module; no module for the
    running kernel -> the inbounds' units on amneziawg-go (legs decide for themselves) and the reason stored."""
    doc = load()
    if not doc.get("enabled"):
        return {"ok": True, "enabled": False, "units": []}
    kernel = running_kernel(runner)
    ok, why = load_module(runner)
    with util.journal_lang():                  # stored in the file, written at boot: the language of the server
        reason = None if ok else _fallback_reason(runner, kernel, why)
    fb = None if ok else {"kernel": kernel, "reason": reason, "at": util.now_iso()}
    old = doc.get("fallback") or None
    if (old or {}).get("kernel") != (fb or {}).get("kernel") or bool(old) != bool(fb):
        doc["fallback"] = fb
        save(doc)
        if fb:
            _log("gw_awgkmod.log.fallback", kernel=kernel, why=fb["reason"])
        else:
            _log("gw_awgkmod.log.loaded", kernel=kernel)
    units = switch_inbounds(runner, restart=False)
    return {"ok": True, "enabled": True, "loaded": ok, "kernel": kernel, "fallback": fb, "units": units}


# ── status ─────────────────────────────────────────────────────────────────

def link_cores(runner):
    """{iface: "kernel"|"userspace"} of the AWG interfaces up now (``ip -d -o link show``)."""
    res = runner.run(["ip", "-d", "-o", "link", "show"])
    out = {}
    for line in (res.out if res.ok else "").splitlines():
        parts = line.split(":", 2)
        if len(parts) < 3:
            continue
        name = parts[1].strip().split("@", 1)[0]
        if re.match(r"^(kawg\d+|kgw-awg\d)$", name):
            out[name] = "kernel" if re.search(r"\b%s\b" % NAME, parts[2]) else "userspace"
    return out


def status(runner):
    doc = load()
    kernel = running_kernel(runner)
    st = dkms_status(runner)
    inbounds = []
    kinds = kernel_kinds()
    for kind in AWG_KINDS:
        ad = _adapter(kind)
        core = ad.unit_core() if ad is not None else None
        if core:
            inbounds.append({"kind": kind, "core": core, "text": CORE_TEXT[core], "kernel_line": kind in kinds})
    live = link_cores(runner) if (inbounds or (os.path.exists(paths.egress_up()) or os.path.exists(paths.egress_up_ru()))) else {}
    legs = []
    try:
        pool = [e for e in exits.pool(runner, roles=exits.ROLES) if e.get("kind") == "awg"]
    except KaskadError:
        pool = []
    for e in pool:
        core = live.get(e["iface"])
        legs.append({"iface": e["iface"], "line": e.get("version") or "", "core": core,
                     "text": CORE_TEXT[core] if core in CORE_TEXT else tr("gw_awgkmod.core.none")})
    out = {"ok": True, "enabled": bool(doc.get("enabled")), "tag": TAG, "version": VERSION, "kernel": kernel,
           "loaded": module_loaded(), "built": kernel in st["installed"], "kernels": st["installed"],
           "sources": os.path.isfile(os.path.join(src_dir(), "dkms.conf")), "patched": patched(),
           "selftest": doc.get("selftest"), "fallback": doc.get("fallback"), "failed": doc.get("failed") or {},
           "error": doc.get("error"), "inbounds": inbounds, "legs": legs, "kernel_lines": list(kernel_lines()),
           "leg_lines": list(leg_lines()), "host": None if doc.get("enabled") else supported_host(runner),
           "set": {"id": SET_ID, "sources": set_in_src(), "loaded": set_loaded(), "version": module_version() or None,
                   "failed": doc.get("set_failed") or None}}
    out["warnings"] = warnings(out)
    if out["enabled"]:
        from . import awgengine
        out["warnings"] += awgengine.mix_warning(awgengine.mix(runner, pool=pool))
    return out


def warnings(st):
    out = []
    if not st.get("enabled"):
        return out
    if not st.get("loaded"):
        fb = st.get("fallback") or {}
        out.append(tr("gw_awgkmod.warn.fallback", kernel=st.get("kernel"),
                      why=fb.get("reason") or tr("gw_awgkmod.warn.no_module")))
    from . import registry
    for kind, f in sorted((st.get("failed") or {}).items()):
        if isinstance(f, dict) and f.get("kernel") == st.get("kernel"):
            out.append(tr("gw_awgkmod.warn.inbound_failed", kind=registry.title(kind), why=f.get("reason")))
    return out


def inbound_warnings(runner, kind, core):
    """Warnings for ``gw status`` of an AWG inbound: acceleration on, yet this kernel-line inbound runs on
    amneziawg-go. Nothing is asked while acceleration is off."""
    if kind not in kernel_kinds() or core != "userspace":
        return []
    doc = load()
    if not doc.get("enabled"):
        return []
    kernel = running_kernel(runner)
    f = (doc.get("failed") or {}).get(kind)
    if isinstance(f, dict) and f.get("kernel") == kernel:
        return [tr("gw_awgkmod.warn.on_but_go", why=f.get("reason"))]
    if not module_loaded():
        fb = doc.get("fallback") or {}
        return [tr("gw_awgkmod.warn.fallback_short",
                   why=fb.get("reason") or tr("gw_awgkmod.warn.not_loaded_for", kernel=kernel))]
    return []


def lines(st):
    """Text of ``gw accel status`` for people."""
    ins = ", ".join("%s — %s" % (r["kind"], r["text"]) for r in st.get("inbounds") or [])
    legs = ", ".join("%s (%s) — %s" % (r["iface"], r["line"] or "?", r["text"]) for r in st.get("legs") or [])
    out = []
    if st.get("enabled"):
        if st.get("loaded"):
            out.append(tr("gw_awgkmod.ln.on", version=st["version"], kernel=st["kernel"]))
        else:
            fb = st.get("fallback") or {}
            out.append(tr("gw_awgkmod.ln.fallback",
                          why=fb.get("reason") or tr("gw_awgkmod.fb.not_loaded_for", kernel=st["kernel"])))
    else:
        out.append(tr("gw_awgkmod.ln.off"))
    if ins:
        out.append("  " + tr("gw_awgkmod.ln.inbounds", list=ins))
    if legs:
        out.append("  " + tr("gw_awgkmod.ln.legs", list=legs))
    kl = st.get("kernel_lines") or list(kernel_lines())
    out.append("  " + tr("gw_awgkmod.ln.lines", inbounds=", ".join(kl),
                         legs=", ".join(st.get("leg_lines") or LEG_LINES)))
    ss = st.get("set") or {}
    if ss.get("loaded"):
        out.append("  " + tr("gw_awgkmod.ln.set", set=ss.get("id")))
    elif ss.get("failed"):
        out.append("  " + tr("gw_awgkmod.ln.set_failed", set=ss.get("id"), why=ss["failed"].get("reason")))
    elif st.get("enabled") and st.get("loaded"):
        out.append("  " + tr("gw_awgkmod.ln.no_set", set=ss.get("id")))
    sel = st.get("selftest") or {}
    if sel:
        out.append("  " + tr("gw_awgkmod.ln.selftest_ok" if sel.get("ok") else "gw_awgkmod.ln.selftest_bad",
                             detail=sel.get("detail"), at=sel.get("at")))
    for w in st.get("warnings") or []:
        out.append("  ! %s" % w)
    if st.get("error") and not st.get("enabled"):
        out.append("  " + tr("gw_awgkmod.ln.error", why=st["error"]))
    host = st.get("host") or {}
    if host and not host.get("ok"):
        out.append("  " + tr("gw_awgkmod.ln.unavailable", why=host.get("why")))
    elif not st.get("enabled"):
        out.append("  " + tr("gw_awgkmod.ln.enable_hint"))
    return out


def _unload(runner, tries=6, pause=0.5):
    """rmmod amneziawg, a few times: interfaces just deleted hold the module for a moment. -> unloaded."""
    for i in range(tries):
        if not module_loaded():
            return True
        if runner.run(["rmmod", NAME]).ok:
            return True
        if i + 1 < tries:
            time.sleep(pause)
    return not module_loaded()
