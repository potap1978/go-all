"""PPTP inbound (``pptp``) on accel-ppp (accel.py): TCP 1723 + GRE, MS-CHAPv2 with MPPE required (no
unencrypted PPTP). Off by default (the install wizard does not tick it): MS-CHAPv2 and MPPE are broken, PPTP
is for routers that can do nothing else — requirements, status and issuance say so.

Interfaces kgw-pptp*; one address per device in the pool "ppp" (shared with L2TP and SSTP). UFW: 1723/tcp
and the GRE protocol under ``kaskad-gw pptp``. Kernel: pptp, gre, ppp_mppe.

Credentials of its own: login ``<login>-pptp``, password ``dev.<id>.pptp.password`` (accel.py) — MS-CHAPv2
without TLS can be cracked from a captured handshake, so the device's shared password (L2TP, SSTP, IKEv2,
OpenConnect) never goes through PPTP. Issuance says so: a router set up with the shared login needs the new one.

Issuance: Windows — PowerShell (Add-VpnConnection -TunnelType Pptp); MikroTik — a RouterOS 7 script;
routers — where to click and what to type; Linux — NetworkManager-pptp. iOS/macOS (since 2016) and Android 12+
have no PPTP. Always the fields: server, login, password.
"""

from kaskad.gw import issue_os, registry
from kaskad.gw.inbounds import accel, native, tunnel
from kaskad.gw.inbounds.texts import lazy_texts
from kaskad.i18n import tr

# WEAK, OWN, PS_NOTE (with %s for the file): words of the moment, read as pptp.NAME
__getattr__ = lazy_texts(__name__, {"WEAK": "gw_in_pptp.weak", "OWN": "gw_in_pptp.own",
                                    "PS_NOTE": ("gw_in.ps_note", {"file": "%s"})})


class PptpInbound(accel.PppInbound):
    kind = "pptp"
    title = "PPTP"

    def own_ports(self, st):
        return [(1723, "tcp"), (None, "gre")]

    def warning(self, st=None):
        return tr("gw_in_pptp.weak")

    def reset_secrets(self, device_id):
        return [accel.pptp_key(device_id)]

    def requirements(self, runner):
        out = super(PptpInbound, self).requirements(runner)
        out["ports"].append({"proto": "gre", "port": None})
        return out

    def issue(self, st, sec, device, os=None):
        dev = tunnel.find_device(st, device)
        p = issue_os.platform_of(dev, os)
        lg, pw = accel.client(st, sec, dev, self.kind)
        host = tunnel.host(st)
        notes = [n for n in (issue_os.builtin_gone(p, self.kind), tr("gw_in_pptp.weak"), tr("gw_in_pptp.own")) if n]
        fields = tunnel.record("fields", tr("gw_in.rec.fields", title="PPTP"),
                               self.fields(host, lg, pw, [[tr("gw_in.label.encryption"), tr("gw_in_pptp.mppe")]]),
                               None, notes)
        recs = []
        if p == "windows":
            recs.append(tunnel.record("ps1", tr("gw_in.rec.ps1", title="PPTP"), native.windows_pptp("Kaskad PPTP", host),
                                      "kaskad-pptp.ps1", [tr("gw_in.ps_note", file="kaskad-pptp.ps1")]))
        elif p == "mikrotik":
            recs.append(self.routeros(st, dev, {"host": host, "port": 0, "login": lg, "password": pw,
                                                "dns": tunnel.dns(st), "device": dev.get("id")}))
        steps = issue_os.steps(p, self.kind) if p != "mikrotik" else []
        if steps:
            recs.append(tunnel.record("guide", tr("gw_in.rec.guide", title="PPTP"), "\n".join(steps), None, []))
        recs.append(fields)
        return tunnel.result(self.kind, recs)


registry.register(PptpInbound)
