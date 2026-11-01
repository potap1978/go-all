"""The unit of probes of the exits (docs/k3/SPEC-stage1-exits §5.3–§5.7; stage 1, T6).

    fetch     — ``safe_get``: one HTTPS GET through an exit (a socket with the mark of an AmneziaWG leg, SOCKS5 to the
                ``probe-<id>`` inbound of an xray exit, or the node's own path), the name resolved through the same exit
                (DoH to 1.1.1.1), SSRF guards, limits; the limiter of «Проверить ссылку»
    speed     — the speed test of one exit
    services  — the check of a service of a group (``checks.services``)
    geo       — the country of the IP of an exit (``cdn-cgi/trace``), the signs of a broken measure
    run       — the scheduler: what is due, budgets, ``flock``, the atomic ``probe-results.json``
    unit      — ``kaskad-gw-probe.service`` / ``.timer`` / ``@.service``: ``ensure``, ``remove``, a manual run

The unit only reads the policy, the snapshot of the states (``exits.json``) and ``traffic.json``; it writes
``probe-results.json`` (the collector takes the new ``seq`` into ``metrics.db`` and its ``Obs``) and its own
``probe-state.json`` (what is due when, budgets, the pending country).
"""
