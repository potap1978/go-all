"""L2TP inbound (``l2tp``): L2TP/IPsec by default — accel-ppp (accel.py) runs L2TP and PPP, strongSwan
(ipsec.py) the IPsec part: IKEv1 with the node's PSK, transport mode, UDP 1701 only. Plain L2TP (no IPsec)
only when ``inbounds.l2tp.plain`` is on — for routers that cannot do IPsec (Huawei, Xiaomi, ASUS); issuance
and status warn then. While plain is off, an xfrm policy (accel.py, l2tp-guard.sh) refuses UDP 1701 that
did not come through IPsec, so a client cannot fall back to plain L2TP.

State ``inbounds.l2tp = {"enabled", "ipsec": true, "plain": false, "subnet"}``. Ports: UDP 1701 — this
inbound's UFW rule; UDP 500, 4500 and ESP — strongSwan's, shared with IKEv2 under ``kaskad-gw ipsec``.
Interfaces kgw-l2tp*; one address per device in the pool "ppp" (shared with PPTP and SSTP).

Issuance: Windows — PowerShell (Add-VpnConnection -L2tpPsk; AssumeUDPEncapsulationContextOnSendRule=2 for
NAT); MikroTik — a RouterOS 7 script; iOS/macOS — the built-in L2TP client field by field (Apple still has it;
iOS 26 / macOS 26 dropped 3DES, SHA1 and DH 2, the node offers AES + SHA2 + DH 14); routers (Keenetic,
TP-Link, Huawei, Xiaomi, ASUS) — where to click and what to type; Linux — NetworkManager-l2tp; Android 12+ has
no L2TP (IKEv2 instead). Always the fields: server, PSK, login, password.
"""

from kaskad.gw import issue_os, registry
from kaskad.gw.inbounds import accel, ipsec, native, tunnel
from kaskad.gw.inbounds.base import InboundError
from kaskad.gw.inbounds.texts import lazy_texts
from kaskad.i18n import tr

# PLAIN_WARNING, PS_NOTE (with %s for the file): words of the moment, read as l2tp.NAME
__getattr__ = lazy_texts(__name__, {"PLAIN_WARNING": "gw_in_l2tp.plain_warning",
                                    "PS_NOTE": ("gw_in.ps_note", {"file": "%s"})})


class L2tpInbound(accel.PppInbound):
    kind = "l2tp"
    engine = "accel-ppp + strongSwan"
    title = "L2TP/IPsec"

    def defaults(self, st):
        return {"ipsec": True, "plain": False}

    def units(self):
        return [accel.UNIT, ipsec.UNIT]

    def unit_texts(self, runner):
        out = accel.unit_texts(runner)
        out.update(ipsec.unit_texts(runner))
        return out

    def modes(self, st):
        """(ipsec, plain)."""
        return accel.l2tp_modes(st)

    def requirements(self, runner):
        out = super(L2tpInbound, self).requirements(runner)
        out["packages"] = accel.build_packages() + list(ipsec.PACKAGES)
        out["modules"] = out["modules"] + list(ipsec.MODULES)
        out["ports"] = [{"proto": "udp", "port": 1701}, {"proto": "udp", "port": 500}, {"proto": "udp", "port": 4500}]
        if out["supported"]:
            missing, busy = ipsec.missing_modules(runner), ipsec.foreign_units(runner)
            if missing or busy:
                out["supported"] = False
                out["why"] = tr("gw_in.why.no_ipsec", modules=", ".join(missing)) if missing else \
                    tr("gw_in.why.ipsec_busy", units=", ".join(busy))
        return out

    def install(self, runner):
        accel.install(runner, self.kind)
        ipsec.install(runner, self.kind)

    def uninstall(self, runner):
        tunnel.close_ufw(runner, self.dir(), self.kind)
        accel.uninstall(runner, self.kind)
        ipsec.uninstall(runner, self.kind)
        tunnel.remove_tree(self.dir())

    def prepare(self, st, sec, runner=None):
        return accel.prepare(st, sec, runner) + ipsec.prepare(st, sec, runner)

    def render(self, st, sec):
        files = dict(accel.render(st, sec))
        files.update(ipsec.render(st, sec))
        return files

    def apply(self, st, sec, runner):
        self.cfg(st)
        a = accel.apply(st, sec, runner)
        i = ipsec.apply(st, sec, runner)
        tunnel.sync_ufw(runner, self.dir(), self.kind, [(1701, "udp")] if accel.wanted(st, self.kind) else [])
        warnings = list(a.get("warnings") or [])
        on, plain = self.modes(st)
        if tunnel.wanted(st, self.kind):
            if not on and not plain:
                warnings.append(tr("gw_in_l2tp.off_both"))
            elif plain:
                warnings.append(tr("gw_in_l2tp.plain_warning"))
        return {"action": {"accel": a["action"], "ipsec": i["action"]}, "changed": a["changed"] + i["changed"],
                "prepared": a["prepared"] + i["prepared"], "cut": a.get("cut", []) + i.get("cut", []),
                "warnings": warnings}

    def status(self, runner):
        out = accel.status(runner, self.kind)
        info = accel.info()
        out["units"] = self.units()
        out["ipsec"] = bool(info.get("l2tp_ipsec"))
        out["plain"] = bool(info.get("l2tp_plain"))
        if out["ipsec"]:
            s = ipsec.status(runner, self.kind)
            out["installed"] = out["installed"] and s["installed"]
            out["running"] = out["running"] and s["running"]
        if out["plain"]:
            out["warnings"] = [tr("gw_in_l2tp.plain_warning")]
        return out

    # ── issuance ──
    def issue(self, st, sec, device, os=None):
        dev = tunnel.find_device(st, device)
        p = issue_os.platform_of(dev, os)
        on, plain = self.modes(st)
        if not accel.wanted(st, self.kind):
            raise InboundError(tr("gw_in_l2tp.err.off"))
        lg, pw = accel.client(st, sec, dev, self.kind)
        host = tunnel.host(st)
        psk = tunnel.sget(sec, ipsec.PSK) if on else None
        if on and not psk:
            raise InboundError(tr("gw_in_l2tp.err.no_psk"))
        notes = []
        gone = issue_os.builtin_gone(p, self.kind)
        if gone:
            notes.append(gone)
        if plain:
            notes.append(tr("gw_in_l2tp.plain_warning"))
        if issue_os.plain_l2tp_only(p) and not plain:
            notes.append(issue_os.plain_off_note())          # where to switch it on — in the words of the reader
        if p in ("ios", "macos") and not on:
            notes.append(tr("gw_in_l2tp.apple_ipsec_off"))
        if on:
            extra = [[tr("gw_in.label.type"), tr("gw_in_l2tp.type_psk_or_plain" if plain else "gw_in_l2tp.type_psk")],
                     [tr("gw_in_l2tp.label.psk"), psk]]
        else:
            extra = [[tr("gw_in.label.type"), tr("gw_in_l2tp.type_plain")]]
        fields = tunnel.record("fields", tr("gw_in.rec.fields", title="L2TP"), self.fields(host, lg, pw, extra), None,
                               notes)
        recs = []
        if p == "windows" and on:
            recs.append(tunnel.record(
                "ps1", tr("gw_in.rec.ps1", title="L2TP/IPsec"), native.windows_l2tp("Kaskad L2TP", host, psk),
                "kaskad-l2tp.ps1", [tr("gw_in.ps_note", file="kaskad-l2tp.ps1"), tr("gw_in_l2tp.err809")]))
        elif p == "mikrotik":
            recs.append(self.routeros(st, dev, {"host": host, "port": 0, "login": lg, "password": pw, "psk": psk,
                                                "dns": tunnel.dns(st), "device": dev.get("id")}))
        steps = issue_os.steps(p, self.kind) if p != "mikrotik" else []
        if steps:
            recs.append(tunnel.record("guide", tr("gw_in.rec.guide", title="L2TP"), "\n".join(steps), None, []))
        recs.append(fields)
        return tunnel.result(self.kind, recs)


registry.register(L2tpInbound)
