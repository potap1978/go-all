"""What a certificate says about itself — read in pure Python (DER walk, no openssl), for the domain of wave 4
(docs/k3/SPEC-wave4.md §4.1, CERT):

    parse(pem)          -> {"names": ["vpn.example.com"], "not_before", "not_after": "2027-01-05T10:00:00Z",
                            "not_after_ts": 1799143200, "issuer": "R11", "staging": False, "sha256": "AB:CD:…"}
                           of the FIRST certificate in ``pem`` (fullchain.pem: the leaf); ValueError when it is no
                           certificate
    days_left(info)     whole days until not_after (negative — expired), by the clock ``_now``
    covers(info, name)  the certificate is for ``name`` (a SAN dNSName, a «*.» wildcard of one label, or the CN
                        when there is no SAN)
    doc(info, kind)     CERT of the contract: {"kind", "names", "not_after", "days_left", "staging", "issuer"}

Staging certificates of Let's Encrypt name their issuer «(STAGING) …» (and the older «Fake LE …»): browsers do not
trust them, and «живой — не перевыпускать» must not keep one when a real one is asked for.
"""

import calendar
import ipaddress
import time

from ..gw.inbounds import pki
from ..i18n import tr

_now = time.time

_OID_CN = bytes.fromhex("550403")              # 2.5.4.3 commonName
_OID_O = bytes.fromhex("55040a")               # 2.5.4.10 organizationName
_OID_SAN = bytes.fromhex("551d11")             # 2.5.29.17 subjectAltName


def _text(der, vp, vl):
    return der[vp:vp + vl].decode("utf-8", "replace")


def _attrs(der, vp, vl):
    """{oid bytes: value} of a Name (the first value of each type)."""
    out = {}
    for _, sp, sl in pki._items(der, vp, vp + vl):              # RDN (SET)
        for _, ap, al in pki._items(der, sp, sp + sl):          # AttributeTypeAndValue
            parts = pki._items(der, ap, ap + al)
            if len(parts) == 2:
                oid = der[parts[0][1]:parts[0][1] + parts[0][2]]
                out.setdefault(oid, _text(der, parts[1][1], parts[1][2]))
    return out


def _time(der, tag, vp, vl):
    """UTCTime (0x17, YYMMDDHHMMSSZ) or GeneralizedTime (0x18, YYYYMMDDHHMMSSZ) -> epoch seconds."""
    s = der[vp:vp + vl].decode("ascii")
    if not s.endswith("Z"):
        raise ValueError(tr("site.certinfo.time_no_z", value=repr(s)))
    s = s[:-1]
    if tag == 0x17:
        yy = int(s[:2])
        s = ("19" if yy >= 50 else "20") + s
    elif tag != 0x18:
        raise ValueError(tr("site.certinfo.time_type", tag="%#x" % tag))
    t = time.strptime(s[:14], "%Y%m%d%H%M%S")
    return calendar.timegm(t)


def _iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _san(der, tbs):
    names = []
    for tag, vp, vl in tbs[6:]:
        if tag != 0xA3:                                         # [3] extensions
            continue
        _, sp, sl = pki._tlv(der, vp)
        for _, ep, el in pki._items(der, sp, sp + sl):          # Extension
            parts = pki._items(der, ep, ep + el)
            if not parts or der[parts[0][1]:parts[0][1] + parts[0][2]] != _OID_SAN:
                continue
            _, op, ol = parts[-1]                               # extnValue OCTET STRING
            _, gp, gl = pki._tlv(der, op)                       # GeneralNames SEQUENCE
            for gtag, np, nl in pki._items(der, gp, gp + gl):
                if gtag == 0x82:                                # [2] dNSName
                    names.append(_text(der, np, nl).lower())
                elif gtag == 0x87 and nl in (4, 16):            # [7] iPAddress (the IP certificate, kaskad.ipcert)
                    names.append(str(ipaddress.ip_address(der[np:np + nl])))
    return names


def parse(pem):
    """The fields of the first certificate of ``pem`` (see the module doc). ValueError when it cannot be read."""
    if not isinstance(pem, str):
        raise ValueError(tr("site.certinfo.none"))
    try:
        der = pki.pem_der(pem)
        tbs = pki._tbs(der)
        issuer = _attrs(der, tbs[2][1], tbs[2][2])
        validity = pki._items(der, tbs[3][1], tbs[3][1] + tbs[3][2])
        subject = _attrs(der, tbs[4][1], tbs[4][2])
        nb = _time(der, *validity[0])
        na = _time(der, *validity[1])
        names = _san(der, tbs)
    except (IndexError, ValueError, UnicodeDecodeError, TypeError) as e:
        raise ValueError(tr("site.certinfo.unreadable", error=e))
    cn = subject.get(_OID_CN, "").lower()
    if not names and cn:
        names = [cn]
    iss_cn, iss_o = issuer.get(_OID_CN, ""), issuer.get(_OID_O, "")
    label = (iss_cn + " " + iss_o).upper()
    return {"names": names, "not_before": _iso(nb), "not_after": _iso(na), "not_after_ts": na,
            "issuer": iss_cn or iss_o, "staging": "STAGING" in label or "FAKE LE" in label,
            "sha256": pki.sha256_fingerprint(pem)}


def read(path):
    """parse() of a file; None when it is missing or unreadable."""
    try:
        with open(path, encoding="ascii", errors="replace") as f:
            return parse(f.read())
    except (OSError, ValueError):
        return None


def days_left(info):
    return int((info["not_after_ts"] - _now()) // 86400)


def expired(info):
    return info["not_after_ts"] <= _now()


def covers(info, name):
    name = str(name or "").lower().rstrip(".")
    for n in info.get("names") or []:
        if n == name:
            return True
        if n.startswith("*.") and "." in name and name.split(".", 1)[1] == n[2:]:
            return True
    return False


def doc(info, kind):
    """CERT of the contract §4.1."""
    return {"kind": kind, "names": list(info["names"]), "not_after": info["not_after"],
            "days_left": days_left(info), "staging": bool(info["staging"]), "issuer": info["issuer"]}
