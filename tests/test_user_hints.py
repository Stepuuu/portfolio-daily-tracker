"""Every page the instructions tell a person to open is a page the web app has, and ``make demo`` opens Today.

The pages are the routes ``dashboard/frontend/src/routes.tsx`` registers; the old routes it only redirects
(``/dashboard``, ``/portfolio``, ``/help``, ``/memory``, ``/settings/cash`` ...) must not be suggested any more.
Looked at: every ``localhost:<port>/<path>`` in the Makefile, the scripts, the READMEs and the docs. The
changelog, which records the redirects, is history and not checked.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROUTES = (ROOT / 'dashboard' / 'frontend' / 'src' / 'routes.tsx').read_text(encoding='utf-8')
PAGES = set(re.findall(r"\bpath: '(/[^']*)'", ROUTES))
REDIRECTED = set(re.findall(r"\bfrom: '(/[^']*)'", ROUTES))
LINK = re.compile(r"localhost:[^/\s]+(/[A-Za-z0-9/_-]*)?")
NOT_PAGES = ('/api', '/v1', '/docs', '/openapi')


def hinted(path):
    text = path.read_text(encoding='utf-8')
    return [(path.relative_to(ROOT), match.group(1) or '/') for match in LINK.finditer(text)]


def is_page(path):
    if path in PAGES:
        return True
    return any(re.fullmatch(re.sub(r':[a-z]+', '[a-z-]+', page), path) for page in PAGES if ':' in page)


def test_make_demo_opens_the_home_page():
    makefile = (ROOT / 'Makefile').read_text(encoding='utf-8')
    demo = makefile.split('\ndemo:', 1)[1].split('\n\n', 1)[0]
    [(_, opened)] = [(None, match.group(1) or '/') for match in LINK.finditer(demo)]
    assert opened == '/', f'make demo tells the user to open {opened}'


def test_every_page_a_hint_names_exists():
    assert {'/', '/setup', '/lab', '/tracker'} <= PAGES and {'/dashboard', '/portfolio', '/help'} <= REDIRECTED
    files = [ROOT / 'Makefile', ROOT / 'start.sh', ROOT / 'README.md', ROOT / 'README_CN.md', ROOT / 'CONTRIBUTING.md',
             *sorted((ROOT / 'docs').glob('*.md')), *sorted((ROOT / 'engine' / 'scripts').glob('*.py')),
             *sorted((ROOT / 'scripts').glob('*.py')), *sorted((ROOT / 'scripts').glob('*.sh'))]
    wrong = [f'{name}: {path}' for file in files if file.is_file() for name, path in hinted(file)
             if not path.startswith(NOT_PAGES) and (path in REDIRECTED or not is_page(path))]
    assert wrong == []
