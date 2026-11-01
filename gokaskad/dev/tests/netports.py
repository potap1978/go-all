"""A free TCP port for the tests that run in parallel (tools/k3_unit_par.py, REV-speed П2).

«bind(0) → close → use the number» takes a port of the ephemeral range: until the code under test binds it (xray, the
panel) or a test counts on «nobody listens there», any outgoing connection of a test running beside it may get the
same port. ``free_port()`` picks outside that range (Linux 32768–60999, macOS 49152–65535), at random from the OS
(random.seed of a test does not make two processes pick alike), and checks it binds now: only another test picking
the same number out of ten thousand at the same moment could take it.
"""

import random
import socket

LOW, HIGH = 20000, 29999
_rng = random.SystemRandom()


def free_port(host="127.0.0.1"):
    for _ in range(200):
        port = _rng.randint(LOW, HIGH)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind((host, port))
        except OSError:
            continue
        finally:
            s.close()
        return port
    raise RuntimeError("no free port in %d-%d" % (LOW, HIGH))
