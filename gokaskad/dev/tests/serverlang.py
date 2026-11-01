"""The language of the SERVER in a test (3.3.2): KASKAD_LANG in the environment (screens of kaskadctl, units of the
node) and the one SAVED in /etc/kaskad/config (i18n.saved_lang: the journal — never the environment, a job of the
panel runs with the language of its request there)."""
import contextlib
import os
from unittest import mock

from kaskad import i18n


@contextlib.contextmanager
def server(lang):
    with mock.patch.dict(os.environ, {"KASKAD_LANG": lang}), mock.patch.object(i18n, "_config_lang", lambda: lang):
        yield
