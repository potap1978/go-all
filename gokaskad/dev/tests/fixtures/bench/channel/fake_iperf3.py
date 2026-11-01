"""A fake iperf3 client in real time for the tests of the cap of test 8b (SEC-ns-b3 З-1, З-2): from its start it
«moves» RATE bytes a second, prints the lines of iperf3 3.x (``-P 4 -i 0.25 -O 2 --forceflush``) as the time goes
and, when it is stopped (SIGTERM) or ends, writes how many bytes went by then to <dir>/passed. Its start moment goes
to <dir>/start (the tests count «the interfaces» by it). Never a network: it only prints.

    python3 -I fake_iperf3.py <rate, bytes a second> <dir> [<the unit word: MBytes | MiBytes …>
                              [<rate after> <lines at the first rate>]]

The last two (Н-1): a link that speeds up — the first lines at the first rate, then the second one.
"""

import os
import signal
import sys
import time

RATE = float(sys.argv[1])
DIR = sys.argv[2]
UNIT = sys.argv[3] if len(sys.argv) > 3 else "MBytes"
RATE2 = float(sys.argv[4]) if len(sys.argv) > 4 else RATE
AFTER = int(sys.argv[5]) if len(sys.argv) > 5 else 0
STEP = 0.25
OMIT = 8                                # 2 s of -O 2
T0 = time.time()


def put(name, text):
    tmp = os.path.join(DIR, name + ".tmp")
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, os.path.join(DIR, name))


def moved(e):
    """Bytes by ``e`` s since the start."""
    a = min(e, AFTER * STEP)
    return RATE * a + RATE2 * max(0.0, e - AFTER * STEP)


def passed():
    return moved(time.time() - T0)


def stop(*_):
    put("passed", repr(passed()))
    os._exit(0)


signal.signal(signal.SIGTERM, stop)
put("start", repr(T0))
out = sys.stdout
out.write("Connecting to host 203.0.113.5, port 5201\n")
for i, sid in enumerate((5, 7, 9, 11)):
    out.write("[%3d] local 192.0.2.10 port %d connected to 203.0.113.5 port 5201\n" % (sid, 43210 + i))
out.write("[ ID] Interval           Transfer     Bitrate         Retr  Cwnd\n")
out.flush()
for k in range(OMIT + 40):
    rate = RATE if k < AFTER else RATE2
    mib = rate * STEP / 1048576.0
    bits = rate * 8 / 1e9
    wait = T0 + STEP * (k + 1) - time.time()
    if wait > 0:
        time.sleep(wait)
    j = k if k < OMIT else k - OMIT
    a, b = j * STEP, (j + 1) * STEP
    tail = "       (omitted)" if k < OMIT else ""
    for sid in (5, 7, 9, 11):
        out.write("[%3d] %6.2f-%-6.2f sec  %6.1f %s  %5.2f Gbits/sec    0   3.00 MBytes%s\n"
                  % (sid, a, b, mib / 4, UNIT, bits / 4, tail))
    out.write("[SUM] %6.2f-%-6.2f sec  %6.1f %s  %5.2f Gbits/sec    0%s\n" % (a, b, mib, UNIT, bits, tail))
    out.write("- - - - - - - - - - - - - - - - - - - - - - - - -\n")
    out.flush()
put("passed", repr(passed()))
