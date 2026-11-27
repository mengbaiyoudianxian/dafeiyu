import os
from pathlib import Path

from . import jm_source, pica_source


SITES = {'jm': jm_source, 'pica': pica_source}


def available(site):
    session = Path(os.environ.get("DAFEIYU_COMIC_SESSIONS", str(Path.home() / ".local/share/dafeiyu-comic-sessions"))) / site
    return not (session / 'login.json').exists()


def error_text(exc):
    if type(exc).__module__ in (jm_source.__name__, pica_source.__name__):
        return str(exc)[:160]
    return f'本次阅读未完成（{type(exc).__name__}）'
