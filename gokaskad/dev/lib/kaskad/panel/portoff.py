"""When the open mode (the way in by the port) goes off by itself — 3.3.7.5, круг 3 (owner 20.10: people's links never
break silently; a working node is never changed silently).

Only on a new install, and only once all three hold:

  (a) ``pending`` — the first end of the wizard set it (``setup.steps.s_finish``; a wizard run again never does);
  (b) the admin came into the panel from outside THROUGH THE DOMAIN (``/api/session`` on the listener ``proxy`` with a
      session) — the proof that 443 opens from outside, not a check from the node itself;
  (c) no link of people with the port was ever given (``port_links``): ``userpage._link`` marks every link built on the
      port (page, subscription, QR, bot, menu), ``userpage.authorize`` every /p/ opened on the port, and the first start
      of this code on a node that already has links of people marks them (they were given on the port before 3.3.7.5).

Otherwise the port stays (the hint «можно выключить» of «Адрес панели» remains). Markers are empty files in the panel
directory: created or removed, never rewritten (several processes mark — the panel, the bot, kaskadctl)."""

import os

from .. import paths, util

PENDING = "port-off.pending"
LINKS = "port-links"
CHECKED = "port-links.checked"


def _path(name):
    return os.path.join(paths.panel_dir(), name)


def _touch(name):
    p = _path(name)
    if os.path.exists(p):
        return
    util.ensure_dir(paths.panel_dir(), 0o700)
    os.close(os.open(p, os.O_WRONLY | os.O_CREAT, 0o600))


def mark_pending():
    """(a) — the first end of the wizard."""
    try:
        _touch(PENDING)
    except OSError:
        pass


def pending():
    return os.path.exists(_path(PENDING))


def mark_port_link():
    """(c) — a link of people with the port was given or opened. Never raises (a link is shown anyway)."""
    try:
        _touch(LINKS)
    except OSError:
        pass


def port_link_given():
    return os.path.exists(_path(LINKS))


def check_old_links(has_tokens):
    """Once per node, at the start of the panel: links of people given before this code were all on the port."""
    if os.path.exists(_path(CHECKED)):
        return
    try:
        if has_tokens():
            _touch(LINKS)
        _touch(CHECKED)
    except OSError:
        pass


def _claim():
    """The pending flag taken by one caller only (two tabs at once switch the port off once)."""
    try:
        os.unlink(_path(PENDING))
        return True
    except OSError:
        return False


def on_domain_login(runner=None):
    """(b): called when an admin's session is seen on the listener ``proxy``. -> "off" (the port switched off),
    "kept" (a link with the port was given — the port stays), "" (nothing to decide yet)."""
    from . import conf
    if not pending():
        return ""
    cfg = conf.load()
    d = conf.pages_domain(cfg)
    if not d:
        return ""                       # people's links are not on the domain yet: wait (the flag stays)
    if not cfg["public"]["on"]:
        _claim()                        # REV-3375 З-7: the port is already off — decided; a port the admin turns on
        return ""                       # later is never switched off again by a sign-in through the domain
    if not _claim():
        return ""
    if port_link_given():
        util.log_tr("journal.panel.public_auto_kept", domain=d)
        return "kept"
    from .. import sysrun
    try:
        conf.set_public(runner or sysrun.default_runner(), False)
    except Exception as e:                            # noqa: BLE001 — the port stays; the admin can switch it off
        util.log_tr("journal.panel.public_auto_failed", error=util.redact(str(e))[:200])
        return ""
    util.log_tr("journal.panel.public_auto_off", domain=d)
    return "off"
