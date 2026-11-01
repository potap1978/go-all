"""Stand-in of kaskad/gw/inbounds/base.py (WP4): the adapter interface of SPEC §3."""

from kaskad.errors import KaskadError


class InboundError(KaskadError):
    """An inbound cannot do what was asked; the message is for people (Russian)."""


class Inbound(object):
    kind = ""
    engine = ""
    title = ""

    def default_port(self):
        return 0

    def requirements(self, runner):
        raise NotImplementedError

    def install(self, runner):
        raise NotImplementedError

    def uninstall(self, runner):
        raise NotImplementedError

    def render(self, st, sec):
        raise NotImplementedError

    def apply(self, st, sec, runner):
        raise NotImplementedError

    def status(self, runner):
        raise NotImplementedError

    def issue(self, st, sec, device, os):
        raise NotImplementedError

    def ifaces(self):
        return []

    def secret_prefixes(self, device_id):
        return ["dev.%s.%s." % (device_id, self.kind)]

    def reset_secrets(self, device_id):
        return self.secret_prefixes(device_id)
