# goVLESS — connection-quality fixes + seamless upgrades (rc1)

## Connection quality (fixes intermittent lag / unreachable VLESS)
- sniffing: destOverride=[http,tls], routeOnly=true on both inbounds — stops
  the server re-resolving client destinations; the old quic+fakedns config
  caused random stalls and silent dead connections in TUN-mode clients.
- TLS (Pro): rejectUnknownSni=true — server answers only for its real domain,
  behaving like a genuine vhost against active DPI probing.
- nginx (Pro): dropped 30-day HTML caching (masquerade fingerprint / stale).
- enable_bbr: drop-in now also tunes TCP buffers, fastopen, MTU probing and
  backlog — smoother throughput and fewer stalls on lossy routes.

## Seamless upgrades (no reinstall, no rescan)
- lib/migrate.sh: existing installs are upgraded in place on startup. The
  x-ui inbound is patched directly in SQLite — clients, UUIDs, subIds, emails,
  certs and panel creds all preserved → existing keys & subscription URLs keep
  working byte-for-byte.
- Restorable backup of config.json + x-ui.db under /opt/govless/backups/<ts>/
  before any change. Migrations gated by an integer GOVLESS_SCHEMA.
- Accurate, mode-aware post-upgrade message: server-only fixes report that
  nothing needs to change on devices; rescan/resubscribe notice is reserved
  for future link-changing migrations.
