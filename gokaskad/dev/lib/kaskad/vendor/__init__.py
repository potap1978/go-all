"""Code and data shared with goAmnezia pro (same owner), copied without changes.

    awgkeys.py    goAmnezia pro lib/awgkeys.py    @ bbcbad7   the first line names the source
    clients.json  goAmnezia pro lib/clients.json  @ a403b16   byte for byte (JSON has no comments)

awgkeys: AmneziaWG lines 1.0/1.5/2.0/3.1 — obfuscation parameters, X25519 keys, client and server
configs, ``vpn://`` links and QR chunks of the Amnezia app. clients.json: the catalog of client apps
(which app understands which line on which platform, how it imports a key).

To update: copy both files again from goAmnezia pro, keep the first line of awgkeys.py and the
commits above in step (``git -C "<goAmnezia Pro>" log -1 --format=%h -- lib/awgkeys.py``).
"""

import json
import os

SOURCES = {
    "awgkeys.py": ("goAmnezia pro", "lib/awgkeys.py", "bbcbad7"),
    "clients.json": ("goAmnezia pro", "lib/clients.json", "a403b16"),
}

_clients = None


def clients():
    """The app catalog (clients.json) as a dict, read once per process."""
    global _clients
    if _clients is None:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "clients.json"),
                  encoding="utf-8") as f:
            _clients = json.load(f)
    return _clients
