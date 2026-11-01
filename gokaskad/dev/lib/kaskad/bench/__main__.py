"""``python3 -I -c "<runpy of kaskad.bench>" <command>`` — the entry of the unit of the server check (``worker``) and a
thin way to start, stop and look at a check before ``kaskadctl host bench`` (B7) exists. Prints JSON.

    worker --id <10 hex> --mode quick|full --by <who> [--sched <id>] [--retry-of <id>] [--force] [--precheck N]
           [--iperf] [--skip 8b,9,up,down] [--only <links of a repeat: down,up,crypto,net_bw,cpu>]
    start [--mode quick|full] [--install | --no-install] [--force] [--iperf] [--skip 8b,9,up,down]
                                           ``--iperf`` — test 8b: the person agreed to the line «the point of a
                                           provider sees the address of the server»; ``--skip`` — tests of the channel
                                           left out (``bench.channel.SKIP``)
    cancel <id>
    status <id>
    plan [--update] [--iperf]              the packages the question would offer (``bench.pkgs.plan``); ``--update`` —
                                           the person agreed to ``apt-get update`` when the lists of apt are empty;
                                           ``--iperf`` — the person chose test 8b: iperf3 is in the same question
"""

import argparse
import json
import sys

from .. import sysrun
from ..errors import KaskadError
from . import pkgs, run


def _skip(text):
    return tuple(x for x in text.split(",") if x)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="kaskad.bench")
    sub = ap.add_subparsers(dest="cmd")
    w = sub.add_parser("worker")
    w.add_argument("--id", required=True)
    w.add_argument("--mode", required=True, choices=run.MODES)
    w.add_argument("--by", required=True, choices=run.BY)
    w.add_argument("--sched")
    w.add_argument("--retry-of")
    w.add_argument("--force", action="store_true")
    w.add_argument("--precheck", type=int, default=run.PRECHECK_S)
    w.add_argument("--iperf", action="store_true")
    w.add_argument("--skip", type=_skip, default=())
    w.add_argument("--only", type=_skip, default=())
    s = sub.add_parser("start")
    s.add_argument("--mode", default="quick", choices=run.MODES)
    g = s.add_mutually_exclusive_group()
    g.add_argument("--install", dest="install", action="store_true", default=None)
    g.add_argument("--no-install", dest="install", action="store_false")
    s.add_argument("--force", action="store_true")
    s.add_argument("--iperf", action="store_true")
    s.add_argument("--skip", type=_skip, default=())
    for name in ("cancel", "status"):
        sub.add_parser(name).add_argument("id")
    pl = sub.add_parser("plan")
    pl.add_argument("--update", action="store_true")
    pl.add_argument("--iperf", action="store_true")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "worker":
            doc = run.worker(a.id, a.mode, a.by, sched_id=a.sched, retry_of=a.retry_of, force=a.force,
                             precheck=max(0, min(a.precheck, 300)), iperf=a.iperf, skip=a.skip, only=a.only)
            out = {"id": a.id, "status": doc["run"]["status"]}
        elif a.cmd == "start":
            out = run.start(a.mode, "cli", force=a.force, install=a.install, iperf=a.iperf, skip=a.skip)
        elif a.cmd == "cancel":
            out = {"id": a.id, "stopping": run.cancel(a.id)}
        elif a.cmd == "status":
            out = run.status(a.id)
        elif a.cmd == "plan":
            p = pkgs.plan(sysrun.default_runner(), update=a.update, by="cli", iperf=a.iperf)
            out = dict(p, question=pkgs.question(p))
        else:
            ap.print_help()
            return 0
    except KaskadError as e:
        sys.stderr.write("%s\n" % e)
        return 1
    sys.stdout.write(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
