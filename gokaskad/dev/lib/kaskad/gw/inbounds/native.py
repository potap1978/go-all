"""Settings for the BUILT-IN VPN clients (work package 6): Windows PowerShell scripts (Add-VpnConnection),
the Apple .mobileconfig of IKEv2, MikroTik RouterOS scripts. Pure functions of their arguments: the adapters
pass the secrets in; nothing here logs or keeps them.

    windows_ikev2(name, server, ca_pem, retired=())   IKEv2 + EAP-MSCHAPv2, the CA into the computer's Root
                                               store (CAs of this node it replaced — ``retired`` thumbprints — out
                                               of it), Set-VpnConnectionIPsecConfiguration = our first proposal
    windows_sstp(name, server, cert_pem)       SSTP: the node's certificate into Root, NoCertRevocationCheck
                                               (a value of the SSTP service: Windows has no setting per
                                               connection — the issuance says it is for every SSTP connection)
    windows_l2tp(name, server, psk)            L2TP/IPsec with the PSK; AssumeUDPEncapsulationContextOnSendRule=2
    windows_pptp(name, server)                 PPTP, MS-CHAPv2 + MPPE required
    mobileconfig_ikev2(...)                    a .mobileconfig (plist XML) with the CA and the IKEv2 payload
    routeros_ppp(kind, data)                   /interface l2tp-client|sstp-client|pptp-client + the LAN routed
                                               through it (like the WireGuard script of wg.py)

Windows scripts are ASCII only (Windows PowerShell 5.1 reads a .ps1 without BOM in the ANSI code page — any
Cyrillic would come out broken) and carry no password of the device: Windows asks for the login and the
password at the first connection and remembers them (-RememberCredential). L2TP/IPsec needs the node's PSK
in the script (-L2tpPsk); it stays in a variable and is never printed.
"""

import base64
import plistlib
import re
import uuid

from kaskad.gw.inbounds import pki

RUN_HINT = "Run in Windows PowerShell as administrator: powershell -ExecutionPolicy Bypass -File .\\%s"


def ascii_only(text):
    """Scripts for Windows and RouterOS stay ASCII (their consoles garble anything else)."""
    return "".join(ch if 32 <= ord(ch) < 127 else "?" for ch in str(text))


def ps_str(value):
    """A single-quoted PowerShell string (only the quote itself is special there)."""
    return "'" + ascii_only(value).replace("'", "''") + "'"


def _ps_head(title, filename, name, server):
    return ["# Kaskad: %s" % ascii_only(title),
            "# " + RUN_HINT % filename,
            "$ErrorActionPreference = 'Stop'",
            "$name = %s" % ps_str(name),
            "$server = %s" % ps_str(server)]


def _ps_import(what, pem, filename):
    """The certificate into "Trusted Root Certification Authorities" of the computer."""
    return ["# %s -> Trusted Root Certification Authorities of this computer" % what,
            "$pem = @'",
            pem.strip(),
            "'@",
            "$file = Join-Path $env:TEMP %s" % ps_str(filename),
            "[IO.File]::WriteAllText($file, $pem)",
            "Import-Certificate -FilePath $file -CertStoreLocation Cert:\\LocalMachine\\Root | Out-Null",
            "Remove-Item $file"]


def _ps_add(tunnel_type, auth, extra=""):
    return ["# The connection for every user of this computer (an older one with this name is replaced)",
            "Remove-VpnConnection -AllUserConnection -Name $name -Force -ErrorAction SilentlyContinue",
            "Add-VpnConnection -AllUserConnection -Name $name -ServerAddress $server -TunnelType %s "
            "-AuthenticationMethod %s -EncryptionLevel Required -RememberCredential -Force%s"
            % (tunnel_type, auth, extra)]


def _ps_done():
    return ['Write-Host "Done: Settings > Network & Internet > VPN > $name > Connect (login and password of '
            'the device)."', ""]


_THUMB = re.compile(r"^[0-9A-F]{40}$")


def windows_ikev2(name, server, ca_pem, filename="kaskad-ikev2.ps1", retired=()):
    lines = _ps_head("IKEv2 for Windows 10/11 (built-in client)", filename, name, server)
    old = [t for t in retired if _THUMB.match(str(t))]
    if old:
        lines += ["# Earlier CAs of this node (replaced) out of Trusted Root Certification Authorities",
                  "foreach ($t in @(%s)) {" % ", ".join(ps_str(t) for t in old),
                  "    Remove-Item -Path ('Cert:\\LocalMachine\\Root\\' + $t) -ErrorAction SilentlyContinue",
                  "}"]
    lines += _ps_import("The CA of the node", ca_pem, "kaskad-ikev2-ca.cer")
    lines += _ps_add("Ikev2", "Eap")
    lines += ["# The server's first proposal (Windows alone offers DH group 2 and SHA-1)",
              "Set-VpnConnectionIPsecConfiguration -AllUserConnection -ConnectionName $name "
              "-AuthenticationTransformConstants GCMAES256 -CipherTransformConstants GCMAES256 "
              "-EncryptionMethod AES256 -IntegrityCheckMethod SHA256 -DHGroup Group14 -PfsGroup None -Force"]
    return "\n".join(lines + _ps_done())


def windows_sstp(name, server, cert_pem, filename="kaskad-sstp.ps1"):
    """``cert_pem`` None: the server has a certificate of a public CA (the domain of wave 4) — Windows trusts it as
    it is: nothing to import, the revocation check stays on."""
    lines = _ps_head("SSTP for Windows 10/11 (built-in client)", filename, name, server)
    if cert_pem:
        lines += _ps_import("The certificate of the node (self-signed)", cert_pem, "kaskad-sstp.cer")
        lines += ["# A self-signed certificate has no revocation list: Windows must not ask for one. The value",
                  "# belongs to the SSTP service (every SSTP connection of this computer): none exists per connection",
                  "New-ItemProperty -Path 'HKLM:\\SYSTEM\\CurrentControlSet\\Services\\SstpSvc\\Parameters' "
                  "-Name NoCertRevocationCheck -PropertyType DWord -Value 1 -Force | Out-Null"]
    lines += _ps_add("Sstp", "MSChapv2")
    return "\n".join(lines + _ps_done())


def windows_l2tp(name, server, psk, filename="kaskad-l2tp.ps1"):
    lines = _ps_head("L2TP/IPsec for Windows 10/11 (built-in client)", filename, name, server)
    lines += ["$psk = %s" % ps_str(psk)]
    lines += _ps_add("L2tp", "MSChapv2", " -L2tpPsk $psk")
    lines += ["# NAT between this computer and the server (error 809): IPsec over UDP 4500 on both sides.",
              "# Takes effect after a reboot; harmless otherwise.",
              "New-ItemProperty -Path 'HKLM:\\SYSTEM\\CurrentControlSet\\Services\\PolicyAgent' "
              "-Name AssumeUDPEncapsulationContextOnSendRule -PropertyType DWord -Value 2 -Force | Out-Null"]
    return "\n".join(lines + _ps_done())


def windows_pptp(name, server, filename="kaskad-pptp.ps1"):
    lines = _ps_head("PPTP for Windows (built-in client; weak encryption)", filename, name, server)
    lines += _ps_add("Pptp", "MSChapv2")
    return "\n".join(lines + _ps_done())


# ── Apple ──────────────────────────────────────────────────────────────────

def _uuid(*parts):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "kaskad:" + ":".join(str(p) for p in parts))).upper()


def mobileconfig_ikev2(name, host, login, password, ca_pem, ca_cn, ident):
    """A configuration profile for iOS/iPadOS/macOS: the CA (com.apple.security.root) and an IKEv2 VPN
    (EAP-MSCHAPv2 with the device's login and password, all traffic through the tunnel). Stable UUIDs per
    device and node: a new profile replaces the old one."""
    vpn = {
        "PayloadType": "com.apple.vpn.managed",
        "PayloadVersion": 1,
        "PayloadIdentifier": "net.kaskad.vpn.%s.ikev2" % ident,
        "PayloadUUID": _uuid(host, ident, "ikev2"),
        "PayloadDisplayName": name,
        "UserDefinedName": name,
        "VPNType": "IKEv2",
        "IKEv2": {
            "RemoteAddress": host,
            "RemoteIdentifier": host,
            "LocalIdentifier": login,
            "AuthenticationMethod": "None",
            "ExtendedAuthEnabled": 1,
            "AuthName": login,
            "AuthPassword": password,
            "ServerCertificateIssuerCommonName": ca_cn,
            "IKESecurityAssociationParameters": {"EncryptionAlgorithm": "AES-256", "IntegrityAlgorithm": "SHA2-256",
                                                 "DiffieHellmanGroup": 14, "LifeTimeInMinutes": 1440},
            "ChildSecurityAssociationParameters": {"EncryptionAlgorithm": "AES-256-GCM",
                                                   "IntegrityAlgorithm": "SHA2-256", "DiffieHellmanGroup": 14,
                                                   "LifeTimeInMinutes": 1440},
            "DeadPeerDetectionRate": "Medium",
            "DisableMOBIKE": 0,
            "DisableRedirect": 1,
            "EnablePFS": 0,
            "UseConfigurationAttributeInternalIPSubnet": 0,
            "OnDemandEnabled": 0,
        },
        "IPv4": {"OverridePrimary": 1},
    }
    ca = {
        "PayloadType": "com.apple.security.root",
        "PayloadVersion": 1,
        "PayloadIdentifier": "net.kaskad.vpn.%s.ca" % ident,
        "PayloadUUID": _uuid(host, ident, "ca"),
        "PayloadDisplayName": ca_cn,
        "PayloadCertificateFileName": "kaskad-ikev2-ca.cer",
        "PayloadContent": pki.pem_der(ca_pem),
    }
    profile = {
        "PayloadType": "Configuration",
        "PayloadVersion": 1,
        "PayloadIdentifier": "net.kaskad.vpn.%s" % ident,
        "PayloadUUID": _uuid(host, ident, "profile"),
        "PayloadDisplayName": name,
        "PayloadDescription": "IKEv2 VPN of the Kaskad gateway %s" % host,
        "PayloadOrganization": "Kaskad",
        "PayloadRemovalDisallowed": False,
        "PayloadContent": [ca, vpn],
    }
    return plistlib.dumps(profile, fmt=plistlib.FMT_XML, sort_keys=True).decode("utf-8")


def cer_base64(pem):
    """The certificate as one base64 line (for fields that take DER in base64)."""
    return base64.b64encode(pki.pem_der(pem)).decode("ascii")


# ── MikroTik ───────────────────────────────────────────────────────────────

def ros_str(value):
    """A RouterOS string: in double quotes, with \\, ", $ (variables) and ? (help in the terminal) escaped."""
    out = ascii_only(value)
    for ch in ("\\", '"', "$", "?"):
        out = out.replace(ch, "\\" + ch)
    return '"%s"' % out


ROS_CLIENTS = {"l2tp": "l2tp-client", "sstp": "sstp-client", "pptp": "pptp-client"}


def routeros_ppp(kind, data):
    """RouterOS 7: the PPP client interface ``kaskad-<kind>`` and the LAN routed through it (the router's
    own traffic, the tunnel itself too, stays on main). ``data``: host, port, login, password, dns, device,
    psk (L2TP/IPsec; None = plain L2TP)."""
    iface = "kaskad-%s" % kind
    c = 'comment="Kaskad"'
    add = ["/interface %s add name=%s connect-to=%s" % (ROS_CLIENTS[kind], iface, data["host"])]
    if kind == "sstp":
        add.append("port=%d" % data["port"])
    add.append("user=%s password=%s" % (ros_str(data["login"]), ros_str(data["password"])))
    if kind == "l2tp":
        if data.get("psk"):
            add.append("use-ipsec=yes ipsec-secret=%s" % ros_str(data["psk"]))
        else:
            add.append("use-ipsec=no")
    if kind == "sstp":
        add.append("authentication=mschap2 verify-server-certificate=no")
    else:
        add.append("allow=mschap2")
    add += ["profile=default-encryption add-default-route=no disabled=no", c]
    return "\n".join([
        "# Kaskad: %s client for MikroTik RouterOS 7, device %s" % (kind.upper(), ascii_only(data["device"])),
        "# Paste into New Terminal or run /import file-name=kaskad-%s.rsc" % kind,
        " ".join(add),
        "/ip firewall nat add chain=srcnat out-interface=%s action=masquerade %s" % (iface, c),
        "/ip firewall mangle add chain=forward out-interface=%s protocol=tcp tcp-flags=syn "
        "action=change-mss new-mss=clamp-to-pmtu passthrough=yes %s" % (iface, c),
        "/routing table add name=kaskad fib %s" % c,
        "/ip route add dst-address=0.0.0.0/0 gateway=%s routing-table=kaskad %s" % (iface, c),
        "/ip route add dst-address=%s/32 gateway=%s %s" % (data["dns"], iface, c),
        "# LAN through the tunnel: 192.168.88.0/24 is the default MikroTik LAN, put yours",
        "/routing rule add dst-address=192.168.88.0/24 action=lookup-only-in-table table=main %s" % c,
        "/routing rule add src-address=192.168.88.0/24 action=lookup-only-in-table table=kaskad %s" % c,
        "",
    ])
