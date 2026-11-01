"""Import from Kaskad free (all versions) and Kaskad PRO 2.x into Kaskad 3 (work package 2).

Modules (work package 2 creates them):

    kaskad.ipt                iptables -S / iptables-save parsing (tokenizer, rule objects)
    kaskad.migrate.scan       read-only inventory of the server  -> report dict
    kaskad.migrate.plan       report -> ordered list of actions (+ Russian preview text)
    kaskad.migrate.snapshot   backups in /var/backups/kaskad/<id>/ (+ manifest.json, pruning)
    kaskad.migrate.apply      snapshot -> switch -> verify -> auto-rollback
    kaskad.migrate.rollback   engine nft -> legacy (2.x-format iptables rules from state)
    kaskad.migrate.cli        ``kaskadctl migrate scan|plan|apply|rollback|backups``

Design and the classification rules: docs/k3/SPEC-wave1.md, «Переезд».
"""
