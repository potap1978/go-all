"""Web panel of Kaskad 3 (docs/k3/SPEC-wave3.md §3, §5).

The server (``kaskad.panel.server``, WP-P1) imports every module of ``MODULES`` at start; each registers its HTTP
handlers with ``kaskad.panel.routes.route``. A module that is not written yet is skipped (ModuleNotFoundError of
that very module), a broken import inside an existing module is a real error.
"""

MODULES = (
    "kaskad.panel.api",        # WP-P1: overview, forwards, gateway, people, exits, appearance, events (SSE)
    "kaskad.panel.scheme",     # WP-P3: scheme graph, layout, apply
    "kaskad.panel.userpage",   # WP-U:  the page of a person /p/<token>
    "kaskad.rules.panel",      # WP-R2: rules profiles and lists
    "kaskad.site.panel",       # 4.0-B: the domain, its DNS and the certificate of Let's Encrypt
    "kaskad.setup.web",        # the quick setup: /api/setup/* under the secret path /setup-<secret>/
    "kaskad.panel.ux",         # 3.3.3 UX: the bot, the keys of the AI agent, the journal and restart of a protocol
    "kaskad.panel.host",       # A7: the light health of the node and its history (SPEC-node-score §3.8)
)
