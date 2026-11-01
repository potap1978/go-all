"""SSTP inbound (``sstp``) on accel-ppp (accel.py): PPP inside TLS on its own TCP port — ``inbounds.sstp.port``,
4433 by default (443 only in the domain mode of wave 4). The certificate is the node's self-signed one
(kaskad.gw.tls: RSA 2048, CN and SAN = gateway.host, /etc/kaskad/gw/tls/), its SHA-256 fingerprint goes into
the issuance; a new address makes a new certificate (accel-ppp takes it on reload, sessions stay). After
``kaskadctl site use sstp on`` (docs/k3/SPEC-wave4.md §4.1) it is the domain's certificate of Let's Encrypt instead:
the issuance names the domain, no fingerprint, nothing to import (a renewal is an accel-ppp reload).

Interfaces kgw-sstp*; one address per device in the pool "ppp" (shared with L2TP and PPTP). UFW: the port
over TCP under ``kaskad-gw sstp``. Kernel: ppp_async (accel-ppp runs SSTP's PPP over a pty).

Issuance: Windows — PowerShell (the certificate into the computer's Root store, NoCertRevocationCheck — a
self-signed certificate has no revocation list — and Add-VpnConnection -TunnelType Sstp); MikroTik — a
RouterOS 7 script; Linux — NetworkManager-sstp or sstpc; Android — the app Open SSTP Client. iOS and macOS have
no SSTP. Always the fields (server:port, login, password, fingerprint) and the certificate as a file.

sstpc on Linux: the login and the password go into a pppd options file (``user``/``password``, installed 0600
into /etc/ppp) and ``sstpc … file /etc/ppp/kaskad-sstp.options`` reads it — ``--user``/``--password`` would put
the password into the argv of sstpc and pppd (``ps``) and into the shell history.
"""

from kaskad.gw import issue_os, registry, tls
from kaskad.gw.inbounds import accel, native, tunnel
from kaskad.gw.inbounds.base import InboundError
from kaskad.gw.inbounds.texts import lazy_texts
from kaskad.i18n import tr

OPTIONS_FILE = "kaskad-sstp.options"
OPTIONS_PATH = "/etc/ppp/" + OPTIONS_FILE
# NoCertRevocationCheck is a value of the SSTP service, there is none per connection (Windows 10/11)
# PS_NOTE, REVOCATION_NOTE, DOMAIN_NOTE (%s — file, domain): words of the moment, read as sstp.NAME
__getattr__ = lazy_texts(__name__, {"PS_NOTE": ("gw_in.ps_note", {"file": "%s"}),
                                    "REVOCATION_NOTE": "gw_in_sstp.revocation_note",
                                    "DOMAIN_NOTE": ("gw_in_sstp.domain_note", {"domain": "%s"})})


def _pppd_word(value):
    """A word of a pppd options file in double quotes (a backslash escapes the next character)."""
    return '"%s"' % str(value).replace("\\", "\\\\").replace('"', '\\"')


def options_text(login, password):
    """The pppd options file of sstpc: ``user`` and ``password`` (pppd hides the password in its logs)."""
    return "\n".join(["# Kaskad SSTP: login and password for pppd (sstpc ... file %s)" % OPTIONS_PATH,
                      "# Keep it root-only (chmod 600): the password is in it.",
                      "user %s" % _pppd_word(login),
                      "password %s" % _pppd_word(password), ""])


class SstpInbound(accel.PppInbound):
    kind = "sstp"
    title = "SSTP"

    def default_port(self):
        return accel.SSTP_PORT

    def port(self, st):
        return accel.sstp_port(st)

    def own_ports(self, st):
        return [(self.port(st), "tcp")]

    def issue(self, st, sec, device, os=None):
        dev = tunnel.find_device(st, device)
        p = issue_os.platform_of(dev, os)
        lg, pw = accel.client(st, sec, dev, self.kind)
        crt = tunnel.sget(sec, tls.CRT)
        if not crt:
            raise InboundError(tr("gw_in_sstp.err.no_cert"))
        from kaskad.site import model as smodel
        domain = smodel.use_for(st, self.kind)          # «kaskadctl site use sstp on»: Let's Encrypt, the domain
        host, port = domain or tunnel.host(st), self.port(st)
        server = tunnel.hostport(host, port)
        notes = [n for n in (issue_os.builtin_gone(p, self.kind),) if n]
        rows = [[tr("gw_in.label.server"), server], [tr("gw_in.label.login"), lg], [tr("gw_in.label.password"), pw]]
        if domain:
            notes.append(tr("gw_in_sstp.domain_note", domain=domain))
            cert = None
        else:
            rows.append([tr("gw_in_sstp.label.fingerprint"), tls.fingerprint(sec)])
            notes.append(tr("gw_in_sstp.self_signed"))
            cert = tunnel.record("conf", tr("gw_in_sstp.rec.cert"), crt, "kaskad-sstp.crt",
                                 [tr("gw_in_sstp.cert_trust")])
        fields = tunnel.record("fields", tr("gw_in.rec.fields", title="SSTP"), rows, None, notes)
        with_cert = [cert] if cert else []
        if p == "windows":
            ps_notes = [tr("gw_in.ps_note", file="kaskad-sstp.ps1")] + ([] if domain else
                                                                       [tr("gw_in_sstp.revocation_note")])
            ps_notes.append(tr("gw_in_sstp.port_hint", port=port))
            ps = tunnel.record("ps1", tr("gw_in.rec.ps1", title="SSTP"),
                               native.windows_sstp("Kaskad SSTP", server, None if domain else crt),
                               "kaskad-sstp.ps1", ps_notes)
            recs = [ps, fields]
        elif p == "mikrotik":
            rsc = self.routeros(st, dev, {"host": host, "port": port, "login": lg, "password": pw,
                                          "dns": tunnel.dns(st), "device": dev.get("id")})
            if domain:
                rsc["notes"].append(tr("gw_in_sstp.ros_no_verify_le"))
            else:
                rsc["notes"].append(tr("gw_in_sstp.ros_no_verify_self"))
            recs = [rsc, fields] + with_cert
        elif p == "linux":
            ca = "/etc/ssl/certs/ca-certificates.crt" if domain else "kaskad-sstp.crt"
            cmd = tunnel.record("guide", tr("gw_in.rec.linux_cmd", title="SSTP"), "\n".join([
                "sudo install -m 600 %s %s" % (OPTIONS_FILE, OPTIONS_PATH),
                "sudo sstpc --ca-cert %s %s file %s require-mschap-v2 noauth usepeerdns"
                % (ca, server, OPTIONS_PATH)]), None,
                [tr("gw_in_sstp.linux_pkg", file=OPTIONS_FILE), tr("gw_in_sstp.linux_route")])
            opts = tunnel.record("conf", tr("gw_in_sstp.rec.pppd"), options_text(lg, pw), OPTIONS_FILE,
                                 [tr("gw_in_sstp.pppd_note")])
            recs = [cmd, opts, tunnel.record("guide", "SSTP — NetworkManager", "\n".join(issue_os.steps(p, self.kind)),
                                             None, [])] + with_cert + [fields]
        elif p == "android":
            trust = "" if domain else tr("gw_in_sstp.android_trust")
            recs = [tunnel.record("guide", tr("gw_in_sstp.rec.android"),
                                  tr("gw_in_sstp.android", host=host, port=port, trust=trust), None, []),
                    fields] + with_cert
        else:
            recs = [fields] + with_cert
        return tunnel.result(self.kind, recs)


registry.register(SstpInbound)
