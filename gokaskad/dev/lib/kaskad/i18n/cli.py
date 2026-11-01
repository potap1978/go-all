"""kaskadctl lang — the language of the server (3.3.2).

    lang get [--json]          {"lang": "ru"|"en", "server": .., "env": KASKAD_LANG of the environment or null}
    lang set ru|en [--json]    kaskad.i18n.set_lang(); {"ok": true, "lang": .., "changed": bool}
The SSH menu, the panel (POST /api/lang) and the bot call the same set_lang().
"""

import os

from .. import errors, i18n
from ..i18n import tr


def _name(lang):
    return tr("lang.name." + lang)


def _get(args):
    from ..cli import emit
    lang = i18n.saved_lang()
    env = os.environ.get(i18n.ENV) or None
    lines = [tr("lang.now", name=_name(lang), code=lang)]
    if env and i18n.normalize(env) not in (None, lang):
        lines.append(tr("lang.env", env=env))
    emit(args, {"ok": True, "lang": lang, "server": lang, "env": env, "langs": list(i18n.LANGS)}, lines)
    return errors.OK


def _set(args):
    from ..cli import emit
    before = i18n.saved_lang()
    try:
        lang = i18n.set_lang(args.lang)
    except ValueError as e:
        raise errors.KaskadError(str(e), code=errors.USAGE)
    key = "lang.same" if lang == before else "lang.set"
    with i18n.use_lang(lang):
        text = tr(key, name=_name(lang), code=lang)
    emit(args, {"ok": True, "lang": lang, "changed": lang != before}, text)
    return errors.OK


def register(sub):
    p = sub.add_parser("lang", help=tr("lang.help"))
    s = p.add_subparsers(dest="lang_cmd")
    g = s.add_parser("get", help=tr("lang.help.get"))
    g.add_argument("--json", action="store_true", help=tr("lang.help.json"))
    g.set_defaults(func=_get)
    st = s.add_parser("set", help=tr("lang.help.set"))
    st.add_argument("lang", metavar="ru|en")
    st.add_argument("--json", action="store_true", help=tr("lang.help.json"))
    st.set_defaults(func=_set)
