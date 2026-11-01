"""IKEv2 inbound (``ikev2``) on strongSwan (ipsec.py): the server proves itself with a certificate of the node's
own CA (RSA 2048; SAN and identity = gateway.host; EKU serverAuth + ikeIntermediate for Windows and Apple),
devices log in with EAP-MSCHAPv2 — the device's login and its one password. Addresses come from strongSwan's
pool 10.77.8.10-254 (``dynamic``): updown → ``kaskadctl gw hook ikev2 up|down <EAP identity> <vip>`` puts the
address into gw_route. DNS of the gateway. No interface (xfrm policies).

Proposals (ipsec.py) that Windows 10/11, macOS/iOS and Android 12+ accept: aes256-sha256-modp2048 first,
aes256-sha1-modp1024 for Windows out of the box. UFW: UDP 500, 4500 and ESP — strongSwan's, shared with
L2TP/IPsec under ``kaskad-gw ipsec``.

Issuance: iOS/macOS — a .mobileconfig (the CA + the IKEv2 payload with login and password); Windows — PowerShell
(the CA into the computer's Root store, Add-VpnConnection -TunnelType Ikev2 -AuthenticationMethod Eap,
Set-VpnConnectionIPsecConfiguration = the first proposal); Android 12+ — the built-in client field by field and
the CA as a file; Linux — charon-cmd / NetworkManager-strongswan. Always the fields and the CA file.

The CA the devices trust is constrained to the node's address (ipsec.py: nameConstraints, a CN per node); a CA
of an earlier build is not — the issuance says so and ``kaskadctl gw pki rotate ikev2`` (``rotate_pki``)
replaces it.
"""

from kaskad.gw import issue_os, registry
from kaskad.gw.inbounds import ipsec, native, tunnel
from kaskad.gw.inbounds.base import Inbound, InboundError
from kaskad.gw.inbounds.texts import lazy_texts
from kaskad.i18n import tr

NAME = "Kaskad IKEv2"
# PS_NOTE (%s — the file), LEGACY_NOTE: words of the moment, read as ikev2.NAME
__getattr__ = lazy_texts(__name__, {"PS_NOTE": ("gw_in.ps_note", {"file": "%s"}),
                                    "LEGACY_NOTE": "gw_in_ikev2.legacy_note"})


def _ca_where(p):
    """«Проверка сервера» of the fields: where this platform's issuance carries the CA. Windows gets no .crt — the
    script trusts the CA itself (stand T-G2b, N-5); iOS and macOS — the profile carries it."""
    if p == "windows":
        return tr("gw_in_ikev2.ca_where.windows")
    if p in ("ios", "macos"):
        return tr("gw_in_ikev2.ca_where.apple")
    return tr("gw_in_ikev2.ca_where.file")


class Ikev2Inbound(Inbound):
    kind = "ikev2"
    engine = "strongSwan"
    title = "IKEv2"
    dynamic = True

    def dir(self):
        return ipsec.own_dir()

    def ifaces(self):
        return []

    def addr_kinds(self, st=None):
        return []

    def default_port(self):
        return 0

    def defaults(self, st):
        return {"cert": "self"}

    def units(self):
        return [ipsec.UNIT]

    def unit_texts(self, runner):
        return ipsec.unit_texts(runner)

    def cfg(self, st):
        return tunnel.require_cfg(st, self.kind, self.title)

    def requirements(self, runner):
        missing, busy = ipsec.missing_modules(runner), ipsec.foreign_units(runner)
        why = ""
        if missing:
            why = tr("gw_in.why.no_ipsec", modules=", ".join(missing))
        elif busy:
            why = tr("gw_in.why.ipsec_busy", units=", ".join(busy))
        return {"kind": self.kind, "engine": self.engine, "packages": list(ipsec.PACKAGES),
                "modules": list(ipsec.MODULES), "ports": [{"proto": "udp", "port": 500}, {"proto": "udp", "port": 4500}],
                "supported": not why, "why": why}

    def install(self, runner):
        ipsec.install(runner, self.kind)

    def uninstall(self, runner):
        ipsec.uninstall(runner, self.kind)

    def prepare(self, st, sec, runner=None):
        return ipsec.prepare(st, sec, runner)

    def render(self, st, sec):
        return ipsec.render(st, sec)

    def apply(self, st, sec, runner):
        self.cfg(st)
        return ipsec.apply(st, sec, runner)

    def status(self, runner):
        out = ipsec.status(runner, self.kind)
        out["port"] = 500 if out["installed"] else None
        return out

    def reset_secrets(self, device_id):
        """IKEv2 logs in with the device's shared password (EAP-MSCHAPv2 inside IKEv2)."""
        return [tunnel.shared_password_key(device_id)]

    def rotate_pki(self, st, sec, runner):
        """A new CA (constrained to gateway.host) and server certificate: every IKEv2 device needs new settings."""
        return ipsec.rotate(st, sec, runner)

    # ── issuance ──
    def issue(self, st, sec, device, os=None):
        dev = tunnel.find_device(st, device)
        p = issue_os.platform_of(dev, os)
        found = [x for x in ipsec.eap_users(st, sec) if x[0].get("id") == dev.get("id")]
        if not ipsec.ikev2_on(st) or not found:
            raise InboundError(tr("gw_in.err.not_user_pw", device=tunnel.device_name(dev), title="IKEv2"))
        _, lg, pw = found[0]
        ca = tunnel.sget(sec, "ikev2.ca.crt")
        if not ca or not tunnel.sget(sec, "ikev2.server.crt"):
            raise InboundError(tr("gw_in_ikev2.err.no_host") if not tunnel.gateway(st).get("host") else
                               tr("gw_in_ikev2.err.no_certs"))
        host = tunnel.host(st)
        gone = issue_os.builtin_gone(p, self.kind)
        legacy = ipsec.ca_legacy(sec)
        scope = [] if legacy else [tr("gw_in_ikev2.ca_scope", host=host)]
        fields = tunnel.record("fields", tr("gw_in.rec.fields", title="IKEv2"),
                               [[tr("gw_in.label.server"), host], [tr("gw_in_ikev2.label.remote_id"), host],
                                [tr("gw_in_ikev2.label.login_eap"), lg], [tr("gw_in.label.password"), pw],
                                [tr("gw_in_ikev2.label.server_check"), _ca_where(p)]],
                               None, ([gone] if gone else []) + ([tr("gw_in_ikev2.legacy_note")] if legacy else []))
        ca_rec = tunnel.record("conf", tr("gw_in_ikev2.rec.ca"), ca, "kaskad-ikev2-ca.crt",
                               [tr("gw_in_ikev2.ca_purpose")] + scope)
        if p in ("ios", "macos"):
            how = tr("gw_in_ikev2.how.ios" if p == "ios" else "gw_in_ikev2.how.macos")
            mc = native.mobileconfig_ikev2(NAME, host, lg, pw, ca, ipsec.ca_cn_of(ca), dev.get("id"))
            recs = [tunnel.record("mobileconfig", tr("gw_in_ikev2.rec.mobileconfig"), mc, "kaskad-ikev2.mobileconfig",
                                  [how, tr("gw_in_ikev2.mobileconfig_note")]),
                    fields, ca_rec]
        elif p == "windows":
            retired = (tunnel.inbound_cfg(st, self.kind) or {}).get("retired_ca") or []
            recs = [tunnel.record("ps1", tr("gw_in.rec.ps1", title="IKEv2"),
                                  native.windows_ikev2(NAME, host, ca, retired=retired),
                                  "kaskad-ikev2.ps1", [tr("gw_in.ps_note", file="kaskad-ikev2.ps1"),
                                                       tr("gw_in_ikev2.ps_trust")] + scope), fields]
        elif p == "android":
            recs = [tunnel.record("guide", tr("gw_in_ikev2.rec.android"),
                                  "\n".join(issue_os.steps(p, self.kind)), None, []), ca_rec, fields]
        elif p == "linux":
            cmd = "sudo charon-cmd --host %s --remote-identity %s --identity %s --cert kaskad-ikev2-ca.crt " \
                  "--profile ikev2-eap" % (host, host, lg)
            recs = [tunnel.record("guide", tr("gw_in.rec.linux_cmd", title="IKEv2"), cmd, None,
                                  [tr("gw_in_ikev2.linux_note")]),
                    tunnel.record("guide", "IKEv2 — NetworkManager", "\n".join(issue_os.steps(p, self.kind)), None, []),
                    ca_rec, fields]
        else:
            recs = [fields, ca_rec]
        return tunnel.result(self.kind, recs)


registry.register(Ikev2Inbound)
