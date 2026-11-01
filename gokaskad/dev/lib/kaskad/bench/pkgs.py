"""The packages of the server check: sysbench and fio (SPEC-node-score §6.2 B1; RESEARCH-node-score §1.4, §6.1 З-9).

* They are installed only by a MANUAL check (the panel, ``kaskadctl host bench``, MCP ``host_bench`` with its
  confirmation, the SSH menu) after the explicit question with numbers in the window of the price; a check by the
  schedule or an automatic repeat NEVER installs (``install`` refuses ``by`` = schedule | auto). Without them the
  tests 3, 4, 6, 7 are «не измерено: нет пакетов sysbench и fio» and the rest go.
* Only the names of ``NEED`` (no name from a person goes to apt), through ``gw.sys.apt_install`` — one install of
  Kaskad at a time, the services of the packages never started (policy-rc.d), a busy apt waited for.
* ``plan`` — what the question shows: the packages apt would add with their dependencies and the place they take on
  the disk after the install = ``Installed-Size`` (KiB) + ``Size`` (the .deb in the cache of apt, bytes) of each, by
  ``apt-get -s`` (a simulation: no lock, nothing changes) and ``apt-cache show`` (≈ 70 MB on Debian 12/13 and Ubuntu
  22.04/24.04 — the same as ``df`` before and after; the coordinator, 19.10, п. 4); apt does not know a package
  (Ubuntu without «universe») — ``available: false`` and the tests say so instead of failing. The package lists of
  the node are empty (a fresh image: no ``*_Packages`` in /var/lib/apt/lists) — apt knows nothing, which says
  nothing about the sources (REV-ns-b1 З-5): with ``update=True`` (a manual check whose person agreed to it — never
  the schedule) ``apt-get update`` runs once and the simulation again; without it ``available: null`` — the question
  offers «Установить» with no size (the install updates the lists itself, ``gw.sys.apt_install``). ``apt-get update``
  failed (no network, the sources do not answer) — ``available: false``, ``why: no_lists``: the reason is the lists,
  not the sources (REV-ns-b1-2 придирка 4).

    missing(runner, iperf=False)  the binaries of NEED (and iperf3 for 8b) not on the PATH -> [names]
    plan(runner, names, update=False, by="cli", iperf=False)
                                  -> {"missing", "available": True | False | None, "install": [packages],
                                  "upgrade": [packages already there apt would update — named in the question, not
                                  in the size], "kib": int | None (of the new ones),
                                  "why": None | "not_found" | "no_lists"}
    question(plan, lang)          -> {"text", "choices": [(1, text), (2, text)], "default": 1}
    install(runner, names, by)    apt-get install of ``names`` (⊆ NEED) -> [installed]; KaskadError on refusal/failure
"""

import os
import re

from .. import aptlock, paths
from ..errors import KaskadError
from ..i18n import tr

NEED = ("sysbench", "fio")
OPTIONAL = ("iperf3",)                 # test 8b (by the button): only when the person chose it, by the same answer
AUTO_BY = ("schedule", "auto")         # who never installs
# «Inst name (new …)» — a new package; «Inst name [old] (new …)» — an update of one already there (REV-ns-b1-3 П-3)
_INST = re.compile(r"^Inst ([a-z0-9][a-z0-9.+-]{0,63})(?::[a-z0-9]+)? (\[?)", re.M)
_SIZE = re.compile(r"^Installed-Size:\s*([0-9]+)\s*$", re.M)          # KiB
_DEB = re.compile(r"^Size:\s*([0-9]+)\s*$", re.M)                     # bytes of the .deb
_PKG = re.compile(r"^Package:\s*([a-z0-9][a-z0-9.+-]{0,63})\s*$", re.M)


def missing(runner, iperf=False):
    return [p for p in NEED + (OPTIONAL if iperf else ()) if not runner.which(p)]


def _names(names):
    names = list(names or ())
    bad = [n for n in names if n not in NEED + OPTIONAL]
    if bad:
        raise KaskadError(tr("bench_run.pkgs.bad", names=", ".join(repr(n) for n in bad)))
    return names


def lists_empty():
    """No package list of apt on the node (a fresh image before ``apt-get update``)."""
    try:
        names = os.listdir(paths.P("/var/lib/apt/lists"))
    except OSError:
        return True
    return not any("_Packages" in n for n in names)


def _sim(runner, names):
    return runner.run(["env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "-s", "-q", "install",
                       "--no-install-recommends"] + names, timeout=120)


def plan(runner, names=None, update=False, by="cli", iperf=False):
    """``iperf`` — the person chose test 8b: iperf3 is in the same question (REV-ns-b3 З-4)."""
    names = _names(missing(runner, iperf=iperf) if names is None else names)
    out = {"missing": names, "available": True, "install": [], "upgrade": [], "kib": 0, "why": None}
    if not names:
        return out
    if update and by in AUTO_BY:
        raise KaskadError(tr("bench_run.pkgs.never_auto"))
    sim = _sim(runner, names)
    if not sim.ok and lists_empty():
        if not update:
            out.update(available=None, kib=None)        # unknown: the lists are empty, not the sources
            return out
        upd = runner.run(["env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "update", "-q"] + aptlock.OPTS,
                         timeout=300)
        if not upd.ok and lists_empty():
            out.update(available=False, kib=None, why="no_lists")
            return out
        sim = _sim(runner, names)
    if not sim.ok:
        out.update(available=False, kib=None, why="not_found")
        return out
    found = _INST.findall(sim.out or "")
    out["install"] = sorted(set(n for n, old in found if not old))
    out["upgrade"] = sorted(set(n for n, old in found if old) - set(out["install"]))
    if out["install"]:
        show = runner.run(["apt-cache", "show", "--no-all-versions"] + out["install"], timeout=60)
        text = (show.out or "") if show.ok else ""
        out["kib"] = _kib(text, out["install"])
    return out


def _kib(text, names):
    """KiB on the disk of the packages ``names`` by the stanzas of ``apt-cache show`` (each of them once, the first
    stanza of a name — ``--no-all-versions``); a package without its two sizes — None (no number rather than a wrong
    one). An update of a package already there is not in ``names``: its size is not new place."""
    got = {}
    for st in re.split(r"\n\s*\n", text or ""):
        m = _PKG.search(st)
        if not m or m.group(1) in got:
            continue
        a, b = _SIZE.search(st), _DEB.search(st)
        got[m.group(1)] = (int(a.group(1)), int(b.group(1))) if a and b else None
    if any(got.get(n) is None for n in names):
        return None
    return sum(got[n][0] for n in names) + int(round(sum(got[n][1] for n in names) / 1024.0))


def _mb(kib):
    """MB as ``df`` and the hosters count them (10^6 bytes)."""
    return max(1, int(round(kib * 1024 / 1e6)))


def question(p, lang=None):
    """The question of the window of the price (numbers, never y/n): 1) install and check, 2) cancel."""
    pk = " ".join(p["missing"])
    rest = [n for n in p["missing"] if n not in OPTIONAL]          # the programs of the tests 1–7
    if p["available"] is False:
        if rest:
            key = "bench_run.pkgs.no_lists" if p.get("why") == "no_lists" else "bench_run.pkgs.unavailable"
        else:                                                      # iperf3 alone: the texts of 8b (REV З-4)
            key = "bench_run.pkgs.no_lists_iperf" if p.get("why") == "no_lists" else "bench_run.pkgs.unavailable_iperf"
        return {"text": tr(key, lang, pkgs=pk), "choices": [], "default": None}
    if p.get("kib"):
        one = tr("bench_run.pkgs.install_size", lang, pkgs=pk, n=len(p["install"]), mb=_mb(p["kib"]))
    else:
        one = tr("bench_run.pkgs.install", lang, pkgs=pk)
    if "iperf3" not in p["missing"]:
        text = tr("bench_run.pkgs.question", lang, pkgs=pk)
    elif rest:
        text = tr("bench_run.pkgs.question_iperf", lang, pkgs=" ".join(rest))
    else:
        text = tr("bench_run.pkgs.question_iperf_only", lang)
    if p.get("upgrade"):
        text += " " + tr("bench_run.pkgs.upgrade", lang, pkgs=" ".join(p["upgrade"]))
    return {"text": text, "choices": [(1, one), (2, tr("bench_run.pkgs.cancel", lang))], "default": 1}


def install(runner, names, by):
    """Install ``names`` (of NEED and OPTIONAL) for a manual check; the unit of the iperf3 server is masked — the
    check needs the client only, never a server open to all on 5201 (SEC П-5). -> the packages installed."""
    if by in AUTO_BY:
        raise KaskadError(tr("bench_run.pkgs.never_auto"))
    names = _names(names)
    if not names:
        return []
    from ..gw import sys as gsys            # the gateway's apt (policy-rc.d, the install lock); imported only here
    return gsys.apt_install(runner, names, mask=("iperf3.service",) if "iperf3" in names else ())
