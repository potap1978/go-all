"""Running system commands — the only place that touches subprocess.

Every function that executes a command takes a ``runner`` argument. Production passes
``SystemRunner()``; tests pass ``FakeRunner`` with handlers that emulate iptables, nft,
systemctl, ss and so on. Commands are argv lists, never shell strings: no ``eval``, no
``shell=True`` (rules from ``iptables -S`` are split with ``shlex``).
"""

import os
import shutil
import subprocess


class Result(object):
    __slots__ = ("argv", "rc", "out", "err")

    def __init__(self, argv, rc=0, out="", err=""):
        self.argv = list(argv)
        self.rc = rc
        self.out = out
        self.err = err

    @property
    def ok(self):
        return self.rc == 0

    def __repr__(self):
        return "Result(%r, rc=%d)" % (self.argv, self.rc)


class CommandError(Exception):
    def __init__(self, result):
        self.result = result
        msg = "%s: rc=%d %s" % (" ".join(result.argv), result.rc, (result.err or result.out).strip()[:400])
        super().__init__(msg)


class Runner(object):
    def run(self, argv, input=None, check=False, timeout=60, env=None):
        """``env`` — extra environment of this one command (a secret for ``openssl -pass env:…``: the environment of a
        process is readable by its owner only, its argv by everyone)."""
        raise NotImplementedError

    def which(self, name):
        raise NotImplementedError


class SystemRunner(Runner):
    """Real commands. LC_ALL=C so tool output is parseable (no localized messages)."""

    def __init__(self, env=None):
        self.env = dict(os.environ)
        self.env["LC_ALL"] = "C"
        # The notify socket of our unit (kaskad-panel: Type=notify) is the main process's alone: a command run from it
        # that speaks sd_notify would leave «Got notification message from PID …, but reception only permitted for
        # main PID» in the journal (T-M6 №9) — or, with NotifyAccess=all, could change the state of the unit.
        self.env.pop("NOTIFY_SOCKET", None)
        # nft, iptables, sysctl live in /usr/sbin; a PATH without it (cron, some ssh setups)
        # would turn them into rc 127. which() already looks there — run() must too.
        parts = [p for p in self.env.get("PATH", "/usr/bin:/bin").split(":") if p]
        for extra in ("/usr/local/sbin", "/usr/sbin", "/sbin"):
            if extra not in parts:
                parts.append(extra)
        self.env["PATH"] = ":".join(parts)
        if env:
            self.env.update(env)

    def run(self, argv, input=None, check=False, timeout=60, env=None):
        try:
            p = subprocess.run(list(argv), input=input, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               universal_newlines=True, timeout=timeout,
                               env=self.env if not env else dict(self.env, **env))
            res = Result(argv, p.returncode, p.stdout, p.stderr)
        except FileNotFoundError as e:
            res = Result(argv, 127, "", str(e))
        except subprocess.TimeoutExpired:
            res = Result(argv, 124, "", "timeout after %ss" % timeout)
        if check and not res.ok:
            raise CommandError(res)
        return res

    def which(self, name):
        return shutil.which(name, path=self.env.get("PATH", ""))


class FakeRunner(Runner):
    """Test double. ``on(prefix, handler)`` registers a handler for argv starting with ``prefix``.

    handler is a ``Result``-like tuple ``(rc, out, err)``, a string (stdout, rc 0) or a callable
    ``fn(argv, input) -> (rc, out, err)``. The first registered matching handler wins, so register
    specific prefixes before generic ones. Unmatched commands return rc 127 and are recorded in
    ``unmatched`` — tests assert it stays empty.
    """

    def __init__(self, binaries=None):
        self.calls = []          # list of (argv, input)
        self.envs = []           # (argv, env) of the calls that passed an extra environment
        self.unmatched = []
        self._handlers = []
        self.binaries = set(binaries or [])

    def on(self, prefix, handler):
        self._handlers.append((list(prefix), handler))
        return self

    def which(self, name):
        return ("/usr/sbin/" + name) if name in self.binaries else None

    def run(self, argv, input=None, check=False, timeout=60, env=None):
        argv = list(argv)
        self.calls.append((argv, input))
        if env:
            self.envs.append((argv, dict(env)))
        for prefix, h in self._handlers:
            if argv[:len(prefix)] == prefix:
                if callable(h):
                    rc, out, err = h(argv, input)
                elif isinstance(h, str):
                    rc, out, err = 0, h, ""
                else:
                    rc, out, err = h
                res = Result(argv, rc, out, err)
                break
        else:
            self.unmatched.append(argv)
            res = Result(argv, 127, "", "not mocked: %s" % " ".join(argv))
        if check and not res.ok:
            raise CommandError(res)
        return res

    def commands(self, prefix=None):
        """argv lists of recorded calls, optionally only those starting with prefix."""
        out = []
        for argv, _ in self.calls:
            if prefix is None or argv[:len(prefix)] == list(prefix):
                out.append(argv)
        return out


# ── heavy commands outside the caller's cgroup ─────────────────────────────
# kaskad-panel.service lives in MemoryMax (panel/cli.py). Its own Python is small, but what it RUNS was counted to it:
# ``gokaskad --egress-replace`` (a refresh of a subscription) runs ``kaskadctl gw apply`` (~66 MB of Python), and that
# runs ``nft`` with 35 602 networks of Russia — in the cgroup of the panel the OOM killer took the panel itself
# (T-M6-qs330b №3). One rule for every such command: ``heavy`` says which, ``ScopedRunner`` runs them in a transient
# ``systemd-run --scope`` (the scope is a unit of its own: its memory is not the panel's; ``--scope`` execs the command
# in place — stdin, stdout, the exit code and the environment stay the command's own).

HEAVY_BINARIES = ("apt-get", "dpkg", "make", "cmake", "certbot", "dkms",
                  "kaskadctl")          # any command of the core: a whole Python with the gateway (~66 MB)
# modes of gokaskad (install.sh) that rebuild the pool (its egress_rebuild runs ``kaskadctl gw apply`` and nft) or
# fetch and unpack a program; the others — light, in place (--egress-awg-heal: the legs are their own units anyway)
HEAVY_SCRIPT_MODES = ("--egress-add", "--egress-del", "--egress-replace", "--egress-ensure-xray")
NFT_HEAVY_INPUT = 64 * 1024             # ``nft -f -`` of a larger script (the networks of Russia: ~1 MB) — heavy


def _head(argv):
    """argv without a leading ``env [NAME=value…]`` -> (basename of the program, the rest)."""
    argv = list(argv)
    if argv and os.path.basename(argv[0]) == "env":
        argv = argv[1:]
        while argv and "=" in argv[0] and not argv[0].startswith("-"):
            argv = argv[1:]
    if not argv:
        return "", []
    return os.path.basename(argv[0]), argv[1:]


def heavy(argv, input=None):
    """Is ``argv`` a command that may take tens of MB (or build something) — to be run outside the caller's cgroup?

        apt-get, dpkg, make, cmake, certbot, dkms, kaskadctl     always
        gokaskad <HEAVY_SCRIPT_MODES>                            the pool rebuilt (kaskadctl gw apply + nft inside)
        nft -f <file> | nft -f - with a script > 64 KB           the networks of Russia, the boot file of the table
        nft list ruleset | nft [-j] list table …                 the whole table: gw_ru's elements
        xray -test | xray run -test                              xray reads its geo files
    """
    name, rest = _head(argv)
    if name in HEAVY_BINARIES:
        return True
    if name == "gokaskad":
        return bool(rest) and rest[0] in HEAVY_SCRIPT_MODES
    if name == "nft":
        if "-f" in rest:
            i = rest.index("-f")
            src = rest[i + 1] if i + 1 < len(rest) else "-"
            if src not in ("-", "/dev/stdin"):
                return True
            return len(input or "") > NFT_HEAVY_INPUT
        words = [w for w in rest if not w.startswith("-")]
        return words[:2] in (["list", "ruleset"], ["list", "table"])
    if name == "xray":
        return "-test" in rest
    return False


SCOPE_FAILED = ("Failed to start transient scope unit", "Failed to connect to bus", "Failed to create bus connection")


class ScopedRunner(Runner):
    """The ``inner`` runner, but a ``heavy`` command goes through ``systemd-run --scope`` — out of the cgroup (and its
    MemoryMax/CPUQuota) of the service that runs it. No systemd-run (a container, a node without systemd) or the scope
    not created (no D-Bus) — the command runs in place, as before. Only the panel wraps its runner (server.serve): the
    wizard's steps are units of their own (setup/jobs.py), the commands of a person in SSH are not in a limit."""

    def __init__(self, inner):
        self.inner = inner

    def which(self, name):
        return self.inner.which(name)

    def _run(self, argv, input, check, timeout, env):
        if env:
            return self.inner.run(argv, input=input, check=check, timeout=timeout, env=env)
        return self.inner.run(argv, input=input, check=check, timeout=timeout)

    def run(self, argv, input=None, check=False, timeout=60, env=None):
        argv = list(argv)
        if not (heavy(argv, input) and self.inner.which("systemd-run")):
            return self._run(argv, input, check, timeout, env)
        scoped = ["systemd-run", "--scope", "--quiet", "--collect", "--description=kaskad-panel job"] + argv
        res = self._run(scoped, input, False, timeout, env)
        if res.rc != 0 and any(m in (res.err or "") for m in SCOPE_FAILED):
            return self._run(argv, input, check, timeout, env)      # the scope was not made: the command never ran
        if check and not res.ok:
            raise CommandError(res)
        return res


_default = None


def default_runner():
    global _default
    if _default is None:
        _default = SystemRunner()
    return _default
