# sock_diag fixtures (B17, NS-D26)

- `tcp_info_members.txt` — the members of `struct tcp_info` (linux/uapi/linux/tcp.h) in order; the test computes the
  offsets from it and compares them with `gw/hostpeople.py`.
- `live-*.hex` — answers of a real kernel to `hostpeople.request` (the filter of ports included), read by
  `hostpeople.netlink_dump`. Taken on an hourly test server (Ubuntu 24.04) from loopback sockets of the capturing
  process itself on test ports 31057–31059: the kernel filter returned only them. Afterwards every address was
  rewritten to RFC 5737 / RFC 3849 documentation addresses (our side 192.0.2.1 / 2001:db8::1, the other side
  198.51.100.7 / 2001:db8::7, v4-mapped kept mapped) and the remote port to 61717. The first line: `seq`, `port`,
  the number of sockets, the bytes each sent at least, the kernel, the length of `tcp_info`. One datagram per line, hex.
