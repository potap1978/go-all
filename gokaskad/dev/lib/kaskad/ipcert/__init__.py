"""The certificate of Let's Encrypt for the IP address of the node (docs/k3/SPEC-setup.md §1): the browser opens the
quick setup and the panel by ``https://<IP>:<port>/`` without a warning.

Let's Encrypt gives IP certificates only with the profile ``shortlived`` (160 h, ≈ 6.7 days) and checks them by
``http-01`` on TCP/80 (or tls-alpn-01; never DNS). certbot learned IP addresses in 5.3 (webroot in 5.4); Debian 12
has 2.1, Debian 13 and Ubuntu 26.04 — 4.0, Ubuntu 24.04 — 2.9: none of them can. So Kaskad has its own minimal ACME
client (RFC 8555): the standard library for HTTP and JSON, the ``openssl`` command (a dependency of ca-certificates,
on every Debian and Ubuntu) for keys and signatures; the CSR is built here.

    crypto    keys, JWS signatures, JWK, the CSR with an iPAddress SAN (openssl + DER written by hand)
    acme      the protocol: directory, nonce, account, order with a profile, http-01, finalize, download
    http01    who answers Let's Encrypt on :80 — our own short-lived listener, or nginx of the site (webroot)
    store     /etc/kaskad/tls/ip: the certificate, its key and meta.json; what the panel serves
    ops       issue / renew when due / status / remove; the timer kaskad-ipcert.timer (every 12 h)
    cli       ``kaskadctl ipcert …``
"""
