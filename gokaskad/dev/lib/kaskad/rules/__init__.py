"""Rules «Россия напрямую» of the gateway (docs/k3/SPEC-wave3.md §2, WP-R): the lists of RoscomVPN —
Russian networks and sites go out directly, ads are blocked, YouTube/Telegram/GitHub/Google Play and the
sites that cut Russian addresses always go through the exit.

    source    downloads (plain HTTPS from the node: GitHub release, then the jsDelivr mirror; sha256 checked;
              text lists as the last resort for the kernel ranges) — never shipped in the package
    dat       stdlib decoder of the protobuf lists of Xray (GeoIPList / GeoSiteList)
    store     /var/lib/kaskad/rules: versions, ``current`` + ``previous`` (last good), atomic switch
    lists     the groups of §2 (block / always through the exit / direct) and the merged IPv4 ranges
    apply     what the gateway renders from the store and the model: gw_ru, gw_rd, dnsmasq lines, xray
              rules and its asset files
    ops       update, profiles, switches, status — the code the CLI, the timer and the panel share
    timer     kaskad-gw-rules.timer + .service (daily, RandomizedDelaySec=2h, Persistent=true)
    cli       ``kaskadctl rules ...``
    panel     routes of the web panel (/api/rules ...)

The kernel path itself (sets gw_ru, gw_ru_dns, gw_force, gw_rd and the rules of gw_pre) lives in
``kaskad.gw.net``; dnsmasq's lines are written by ``kaskad.gw.dns``, xray's by ``kaskad.gw.xray`` — they call
``kaskad.rules.apply``.

Data: RoscomVPN by hydraponique (geosite — MIT; geoip — no licence file; the geoip data come from MaxMind
GeoLite2, DB-IP Lite (CC BY 4.0) and IPinfo). The fallback text list of runetfreedom is GPL-3.0.
"""


def attribution():
    """Where the lists come from and their licences — a line for people (status, the panel)."""
    from ..i18n import tr
    return tr("rules.attribution")
