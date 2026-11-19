"""Extensions named in TRACKER_EXTENSIONS add to the running application; a broken one never takes it down.

The extension modules are written into the test's temporary directory and found through ``sys.path``,
as an installed package would be. Most tests drive ``backend.extensions`` in this process with the
registries swapped for empty ones; the tests at the end import the real ``backend.main`` in a child
process, where loading happens exactly as at start-up.
"""
import asyncio
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

needs_backend = pytest.mark.skipif(sys.version_info < (3, 10), reason='backend.main needs Python 3.10+')
pytestmark = needs_backend

ROOT = Path(__file__).resolve().parents[1]
TUESDAY = '2026-11-17'

# Every capability, and a marker file written when the module is imported.
FULL = '''
from contextlib import asynccontextmanager
import os
from pathlib import Path

from fastapi import APIRouter
from core import agent_tools, notify
from core.tools import Tool

Path(os.environ['EXTENSION_MARKER']).write_text('imported')
EVENTS = []
router = APIRouter()


@router.get('/hello')
def hello():
    return {'hello': 'extension'}


@router.post('/echo')
def echo(body: dict):
    return body


def routers():
    return [(router, '/api/ext/sample', ['扩展示例']), (APIRouter(), '/api/ext/empty', [])]


@asynccontextmanager
async def task(app):
    EVENTS.append('start')
    yield
    EVENTS.append('stop')


def background_tasks():
    return [task]


@agent_tools.register
def radar_tools(runtime):
    async def radar() -> dict:
        return {'radar': 'read only'}
    return Tool(name='sample_radar', description='示例只读工具', parameters=[], function=radar)


def deliver(notification):
    EVENTS.append(('delivered', notification['kind']))


notify.register_deliverer(deliver)


def on_close_report(report):
    EVENTS.append(('report', report['trading_date'], report['replaces']))
'''

# A route of the same method and path as the application's own status route.
CLASH = '''
from fastapi import APIRouter
from core import agent_tools

router = APIRouter()


@router.get('/status')
def impostor():
    return {'impostor': True}


@router.get('/other')
def other():
    return {}


health = APIRouter()


@health.get('/health')
def impostor_health():
    return {'status': 'impostor'}


def routers():
    return [(router, '/api/app', []), (health, '', [])]


@agent_tools.register
def clash_tools(runtime):
    return None
'''


@pytest.fixture
def setup(tmp_path, monkeypatch):
    """Empty registries and extension state, a portfolio directory, and a folder on sys.path for modules."""
    from backend import extensions
    from core import agent_tools, notify
    agent_tools.discover()   # the application's own tools register into the real list, not into the copy
    monkeypatch.setattr(agent_tools, '_factories', agent_tools.registered())
    monkeypatch.setattr(notify, '_deliverers', [])
    monkeypatch.setattr(extensions, '_extensions', [])
    monkeypatch.setattr(extensions, '_configured', False)
    for name in ('TRACKER_DEMO_MODE', 'TRACKER_LAB_ONLY', 'TRACKER_EXTENSIONS'):
        monkeypatch.delenv(name, raising=False)
    portfolio = tmp_path / 'portfolio'
    portfolio.mkdir()
    monkeypatch.setenv('PORTFOLIO_DIR', str(portfolio))
    monkeypatch.setenv('EXTENSION_MARKER', str(tmp_path / 'imported'))
    modules = tmp_path / 'modules'
    modules.mkdir()
    monkeypatch.syspath_prepend(str(modules))
    written = []

    def write(source, name=None):
        name = name or f'pdt_extension_{uuid.uuid4().hex[:12]}'
        (modules / f'{name}.py').write_text(textwrap.dedent(source), encoding='utf-8')
        importlib.invalidate_caches()
        written.append(name)
        return name

    yield write
    for name in written:
        sys.modules.pop(name, None)


def load(monkeypatch, *names):
    from backend import extensions
    monkeypatch.setenv('TRACKER_EXTENSIONS', ','.join(names))
    return extensions.load()


def statuses():
    from backend import extensions
    return extensions.status()


# ── Not configured ──

@pytest.mark.parametrize('value', [None, '', ' , '])
def test_unset_or_empty_variable_loads_nothing(setup, monkeypatch, tmp_path, value):
    from backend import extensions, features
    name = setup(FULL)
    if value is not None:
        monkeypatch.setenv('TRACKER_EXTENSIONS', value)
    assert extensions.load() == []
    assert name not in sys.modules and not (tmp_path / 'imported').exists()
    assert extensions.status() is None, 'the app status keeps its three keys'
    assert extensions.background not in features.background_tasks()
    app = FastAPI()
    extensions.mount(app)
    assert [route.path for route in app.routes] == [route.path for route in FastAPI().routes]


def test_the_app_status_lists_extensions_only_when_some_are_named(setup, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)   # importing the application creates data directories in the working directory
    import backend.main as main
    client = TestClient(main.app, base_url='http://127.0.0.1')
    assert set(client.get('/api/app/status').json()) == {'mode', 'ledger', 'version'}
    load(monkeypatch, setup(FULL), 'pdt_no_such_extension')
    body = client.get('/api/app/status').json()
    assert [(item['status'], item.get('error')) for item in body['extensions']] == [
        ('loaded', None), ('failed', 'ModuleNotFoundError (pdt_no_such_extension)')]


# ── Every capability ──

def test_an_extension_adds_routes_tools_deliverers_tasks_and_a_report_listener(setup, monkeypatch, tmp_path):
    from backend import extensions, features
    from core import agent_tools, notify
    name = setup(FULL)
    [extension] = load(monkeypatch, name)
    module = sys.modules[name]
    assert extension.status == 'loaded'
    assert statuses() == [{'name': name, 'status': 'loaded', 'started': False, 'errors': [],
                           'provides': ['routers', 'background_tasks', 'agent_tools', 'deliverers',
                                        'on_close_report']}]

    # Routes, after the built-in ones.
    app = FastAPI()
    extensions.mount(app)
    client = TestClient(app)
    assert client.get('/api/ext/sample/hello').json() == {'hello': 'extension'}
    assert client.post('/api/ext/sample/echo', json={'a': 1}).json() == {'a': 1}
    assert '扩展示例' in json.dumps(app.openapi(), ensure_ascii=False)

    # Chat tools: in the registry that AgentService._init_agent builds from at start-up.
    tools = {tool.name: tool for tool in agent_tools.build_all(None)}
    assert asyncio.run(tools['sample_radar'].function()) == {'radar': 'read only'}

    # Deliverers: receive what is published.
    notify.publish('alert', 'info', '示例', 'body')
    assert notify.flush(5)
    assert ('delivered', 'alert') in module.EVENTS

    # Background tasks: started after the built-in ones, stopped at shutdown.
    assert extensions.background in features.background_tasks()
    assert features.background_tasks()[-1] is extensions.background

    async def lifetime():
        async with extensions.background(FastAPI()):
            assert module.EVENTS[-1] == 'start' and statuses()[0]['started'] is True
    asyncio.run(lifetime())
    assert module.EVENTS[-2:] == ['start', 'stop'] and statuses()[0]['started'] is False


def finish(portfolio, engine, day, text):
    """The files a finished pipeline leaves (see tests/test_close_reports.py)."""
    def sha(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()
    (portfolio / 'snapshots').mkdir(exist_ok=True)
    snapshot = portfolio / 'snapshots' / f'{day}.json'
    snapshot.write_text(json.dumps({'date': day, 'text': text}))
    (engine.parent / 'reports').mkdir(parents=True, exist_ok=True)
    report = engine.parent / 'reports' / f"portfolio-{day.replace('-', '')}.md"
    report.write_text(text, encoding='utf-8')
    (portfolio / 'pipeline-state').mkdir(exist_ok=True)
    (portfolio / 'pipeline-state' / f'{day}.json').write_text(json.dumps({
        'snapshot': True, 'history': True, 'report': True, 'snapshot_sha': sha(snapshot),
        'history_snapshot': sha(snapshot), 'report_snapshot': sha(snapshot), 'report_sha': sha(report)}))


async def until(condition, seconds=10):
    for _ in range(int(seconds / 0.02)):
        if condition():
            return True
        await asyncio.sleep(0.02)
    return False


def first_look_done(portfolio):
    """The watcher's first look has listed the finished reports (it then creates its record file)."""
    return (portfolio / 'desk-extensions.sqlite3').exists()


@pytest.fixture
def quick_watcher(tmp_path, monkeypatch):
    """The report watcher looks every 20 ms, at a private engine directory."""
    from core import close_reports
    from core.routines import close_snapshot
    engine = tmp_path / 'checkout' / 'engine' / 'scripts'
    monkeypatch.setattr(close_reports, 'POLL_SECONDS', 0.02)
    monkeypatch.setattr(close_snapshot, 'default_engine_dir', lambda: engine)
    return engine


def test_on_close_report_is_called_for_each_new_report_while_the_app_runs(setup, monkeypatch, tmp_path, quick_watcher):
    from backend import extensions
    name = setup(FULL)
    load(monkeypatch, name)
    module = sys.modules[name]
    portfolio = tmp_path / 'portfolio'

    async def lifetime():
        async with extensions.background(FastAPI()):
            assert await until(lambda: first_look_done(portfolio))   # nothing finished yet
            finish(portfolio, quick_watcher, TUESDAY, '# 日报\n')
            assert await until(lambda: ('report', TUESDAY, None) in module.EVENTS)
            finish(portfolio, quick_watcher, TUESDAY, '# 日报（补录后）\n')
            assert await until(lambda: len([event for event in module.EVENTS if event[0] == 'report']) == 2)
    asyncio.run(lifetime())
    reports = [event for event in module.EVENTS if event[0] == 'report']
    assert reports[1][2] is not None, 'the second report names the one it replaces'


def test_a_report_listener_that_raises_is_listed_and_breaks_nothing(setup, monkeypatch, tmp_path, quick_watcher,
                                                                      caplog):
    from backend import extensions
    monkeypatch.setenv('PDT_TEST_SECRET', 'secret-value')
    name = setup('''
        import os

        def on_close_report(report):
            raise ConnectionError('token=' + os.environ['PDT_TEST_SECRET'])
    ''')
    good = setup(FULL)
    load(monkeypatch, name, good)
    portfolio = tmp_path / 'portfolio'

    async def lifetime():
        async with extensions.background(FastAPI()):
            assert await until(lambda: first_look_done(portfolio))
            finish(portfolio, quick_watcher, TUESDAY, '# 日报\n')
            assert await until(lambda: statuses()[0]['errors'])
            assert await until(lambda: ('report', TUESDAY, None) in sys.modules[good].EVENTS)
    asyncio.run(lifetime())
    [error] = statuses()[0]['errors']
    assert {key: error[key] for key in ('stage', 'error', 'trading_date')} == {
        'stage': 'on_close_report', 'error': 'ConnectionError', 'trading_date': TUESDAY}
    assert statuses()[0]['status'] == 'loaded' and statuses()[1]['errors'] == []
    assert 'secret-value' not in caplog.text and 'ConnectionError' in caplog.text


# ── Failures ──

def test_a_module_that_fails_to_import_is_listed_and_leaves_nothing_registered(setup, monkeypatch, caplog):
    from core import agent_tools, notify
    monkeypatch.setenv('PDT_TEST_SECRET', 'secret-value')
    tools, deliverers = agent_tools.registered(), notify.registered_deliverers()
    broken = setup('''
        from core import agent_tools, notify

        @agent_tools.register
        def half_registered(runtime):
            return None

        notify.register_deliverer(lambda notification: None)
        import pdt_missing_dependency
    ''')
    raising = setup('import os\nraise RuntimeError("password=" + os.environ["PDT_TEST_SECRET"])\n')
    good = setup(FULL)
    loaded = load(monkeypatch, 'not-a-module', broken, raising, 'pdt_no_such_extension', good)
    assert [(item.status, item.stage, item.error) for item in loaded] == [
        ('failed', 'name', 'invalid module name'),
        ('failed', 'import', 'ModuleNotFoundError (pdt_missing_dependency)'),
        ('failed', 'import', 'RuntimeError'),
        ('failed', 'import', 'ModuleNotFoundError (pdt_no_such_extension)'),
        ('loaded', None, None)]
    module = sys.modules[good]
    assert agent_tools.registered() == tools + [module.radar_tools], 'the broken module registered nothing'
    assert notify.registered_deliverers() == deliverers + [module.deliver]
    assert [item['status'] for item in statuses()] == ['failed', 'failed', 'failed', 'failed', 'loaded']
    assert 'secret-value' not in caplog.text
    assert 'not loaded: import failed: RuntimeError' in caplog.text and 'raise RuntimeError' in caplog.text


ROUTER_START_UP = '''
from contextlib import asynccontextmanager
from fastapi import APIRouter
from core import agent_tools


@asynccontextmanager
async def broken(app):
    raise RuntimeError('the cache could not be opened')
    yield


def fails():
    raise RuntimeError('the cache could not be opened')


@agent_tools.register
def router_tools(runtime):
    return None


def routers():
    {make}
    router.get('/ping')(lambda: {{}})
    return [(router, '/api/ext/startup', [])]
'''


@pytest.mark.parametrize('make', [
    'router = APIRouter(lifespan=broken)',
    'router = APIRouter(on_startup=[fails])',
    'router = APIRouter(on_shutdown=[fails])',
    'router = APIRouter()\n    router.include_router(APIRouter(lifespan=broken), prefix="/inner")',
    'router = APIRouter()\n    router.include_router(APIRouter(on_startup=[fails]), prefix="/inner")',
])
def test_a_router_with_its_own_start_up_is_refused_and_the_app_still_starts(setup, monkeypatch, make):
    from backend import extensions
    from core import agent_tools
    before = agent_tools.registered()
    name, good = setup(ROUTER_START_UP.format(make=make)), setup(FULL)
    load(monkeypatch, name, good)
    assert statuses()[0] == {'name': name, 'status': 'failed', 'stage': 'router_lifespan',
                             'error': extensions.ROUTER_LIFESPAN}
    assert agent_tools.registered() == before + [sys.modules[good].radar_tools], 'its tool is removed again'
    app = FastAPI()
    app.get('/health')(lambda: {'status': 'healthy'})
    extensions.mount(app)
    with TestClient(app) as client:   # runs the application's start-up and shutdown
        assert client.get('/health').json() == {'status': 'healthy'}
        assert client.get('/api/ext/startup/ping').status_code == 404
        assert client.get('/api/ext/sample/hello').status_code == 200


def test_routers_that_include_plain_routers_are_not_refused(setup, monkeypatch):
    from backend import extensions
    name = setup('''
        from fastapi import APIRouter

        def routers():
            inner = APIRouter()
            inner.get('/leaf')(lambda: {'leaf': True})
            middle = APIRouter()
            middle.include_router(inner, prefix='/inner')
            outer = APIRouter()
            outer.include_router(middle, prefix='/middle')
            return [(outer, '/api/ext/nested', [])]
    ''')
    [extension] = load(monkeypatch, name)
    assert extension.status == 'loaded'
    app = FastAPI()
    extensions.mount(app)
    with TestClient(app) as client:
        assert client.get('/api/ext/nested/middle/inner/leaf').json() == {'leaf': True}


def test_a_start_up_the_check_does_not_recognise_still_never_reaches_the_application():
    """The second line: the gathered router's own lifespan and handlers are empty, whatever it includes."""
    from contextlib import asynccontextmanager
    from fastapi import APIRouter
    from backend import extensions

    @asynccontextmanager
    async def broken(app):
        raise RuntimeError('no')
        yield

    with_lifespan, with_handler = APIRouter(lifespan=broken), APIRouter(on_startup=[lambda: 1 / 0])
    with_lifespan.get('/ping')(lambda: {'ok': True})
    with_handler.get('/ping')(lambda: {'ok': True})
    [gathered] = extensions._gather([(with_lifespan, '/api/ext/one', []), (with_handler, '/api/ext/two', [])])
    app = FastAPI()
    app.include_router(gathered)
    with TestClient(app) as client:
        assert client.get('/api/ext/one/ping').json() == {'ok': True}
        assert client.get('/api/ext/two/ping').json() == {'ok': True}


RADAR = '''
from core import agent_tools, notify

DELIVERED = []


@agent_tools.register
def radar_tools(runtime):
    return None


def deliver(notification):
    DELIVERED.append(notification['kind'])


notify.register_deliverer(deliver)
'''

PUBLISHER = '''
import {radar}
from core import notify


def own_deliverer(notification):
    pass


notify.register_deliverer(own_deliverer)
raise RuntimeError('the publisher has no configuration')
'''


@pytest.mark.parametrize('publisher_first', [True, False])
def test_an_extension_that_imports_another_and_fails_leaves_the_other_whole(setup, monkeypatch, publisher_first):
    from core import agent_tools, notify
    radar = setup(RADAR)
    publisher = setup(PUBLISHER.format(radar=radar))
    order = [publisher, radar] if publisher_first else [radar, publisher]
    loaded = {item.name: item for item in load(monkeypatch, *order)}
    assert (loaded[publisher].status, loaded[publisher].stage) == ('failed', 'import')
    assert loaded[radar].status == 'loaded'
    assert {item['name']: item.get('provides') for item in statuses()}[radar] == ['agent_tools', 'deliverers']
    module = sys.modules[radar]
    assert notify.registered_deliverers() == [module.deliver], "the publisher's own deliverer is removed"
    assert module.radar_tools in agent_tools.registered()
    notify.publish('alert', 'info', '示例', 'body')
    assert notify.flush(5) and module.DELIVERED == ['alert']


def test_an_extension_that_fails_after_another_imported_it_takes_its_own_registrations_with_it(setup, monkeypatch):
    from core import agent_tools, notify
    radar = setup(RADAR + '\ndef background_tasks():\n    return ["not callable"]\n')
    importer = setup(f'import {radar}\n')
    loaded = load(monkeypatch, importer, radar)
    assert [(item.status, item.stage) for item in loaded] == [('loaded', None), ('failed', 'background_tasks')]
    assert notify.registered_deliverers() == []
    assert sys.modules[radar].radar_tools not in agent_tools.registered()


def test_an_extension_route_never_replaces_a_built_in_one(setup, monkeypatch):
    from backend import extensions
    name = setup(CLASH)
    load(monkeypatch, name)
    app = FastAPI()
    app.get('/api/app/status')(lambda: {'built_in': True})
    extensions.mount(app)
    client = TestClient(app)
    assert client.get('/api/app/status').json() == {'built_in': True}
    assert client.get('/api/app/other').status_code == 200, 'its other routes are added'


@pytest.mark.parametrize('source, stage', [
    ('def routers():\n    return [("not a router", "/api/ext/x", [])]\n', 'routers'),
    ('from fastapi import APIRouter\ndef routers():\n    return [(APIRouter(), "api/no-slash", [])]\n', 'routers'),
    ('def routers():\n    raise ValueError("no")\n', 'routers'),
    ('def background_tasks():\n    return ["not callable"]\n', 'background_tasks'),
    ('on_close_report = "not callable"\n', 'on_close_report'),
])
def test_unusable_definitions_fail_the_extension(setup, monkeypatch, source, stage):
    from core import agent_tools
    before = agent_tools.registered()
    name = setup('from core import agent_tools\nagent_tools.register(lambda runtime: None)\n' + source)
    [extension] = load(monkeypatch, name)
    assert (extension.status, extension.stage) == ('failed', stage)
    assert agent_tools.registered() == before


def test_background_tasks_that_fail_are_listed_and_the_others_run(setup, monkeypatch):
    from backend import extensions
    monkeypatch.setattr(extensions, 'START_SECONDS', 0.2)
    name = setup('''
        import asyncio
        from contextlib import asynccontextmanager

        EVENTS = []

        @asynccontextmanager
        async def fails_to_start(app):
            raise OSError('no')
            yield

        @asynccontextmanager
        async def hangs(app):
            await asyncio.sleep(3600)
            yield

        @asynccontextmanager
        async def fails_to_stop(app):
            EVENTS.append('started')
            yield
            raise RuntimeError('no')

        @asynccontextmanager
        async def works(app):
            EVENTS.append('works')
            yield
            EVENTS.append('works stopped')

        def background_tasks():
            return [fails_to_start, hangs, lambda app: 'not a context manager', fails_to_stop, works]
    ''')
    load(monkeypatch, name)

    async def lifetime():
        async with extensions.background(FastAPI()):
            assert sys.modules[name].EVENTS == ['started', 'works']
    asyncio.run(lifetime())
    assert sys.modules[name].EVENTS == ['started', 'works', 'works stopped']
    [item] = statuses()
    assert item['status'] == 'loaded' and item['started'] is False
    assert [(error['stage'], error['error']) for error in item['errors']] == [
        ('start', 'OSError'), ('start', 'TimeoutError'), ('start', 'AttributeError'), ('stop', 'RuntimeError')]


# ── Modes ──

def test_demo_mode_loads_no_extension(setup, monkeypatch, tmp_path):
    from backend import extensions, features
    monkeypatch.setenv('TRACKER_DEMO_MODE', '1')
    name = setup(FULL)
    loaded = load(monkeypatch, name, 'pdt_no_such_extension')
    assert [(item.status, item.error) for item in loaded] == [('skipped', 'demo'), ('skipped', 'demo')]
    assert name not in sys.modules and not (tmp_path / 'imported').exists()
    assert statuses() == [{'name': name, 'status': 'skipped', 'reason': 'demo'},
                          {'name': 'pdt_no_such_extension', 'status': 'skipped', 'reason': 'demo'}]
    assert extensions.background not in features.background_tasks()
    app = FastAPI()
    extensions.mount(app)
    assert TestClient(app).get('/api/ext/sample/hello').status_code == 404
    before = sorted(path.name for path in (tmp_path / 'portfolio').iterdir())

    async def lifetime():
        async with extensions.background(FastAPI()):
            await asyncio.sleep(0.1)
    asyncio.run(lifetime())
    assert sorted(path.name for path in (tmp_path / 'portfolio').iterdir()) == before, 'nothing is written'


def test_lab_only_mode_mounts_routers_but_starts_no_task(setup, monkeypatch, tmp_path):
    """The real lifespan in lab-only mode: routes answer, no background task, no report listener."""
    from backend import extensions
    monkeypatch.chdir(tmp_path)
    import backend.main as main
    monkeypatch.setenv('TRACKER_LAB_ONLY', '1')
    monkeypatch.setenv('TRACKER_LAB_DIR', str(tmp_path / 'lab'))
    name = setup(FULL)
    load(monkeypatch, name)
    app = FastAPI(lifespan=main.lifespan)
    extensions.mount(app)
    with TestClient(app) as client:
        assert client.get('/api/ext/sample/hello').json() == {'hello': 'extension'}
    assert sys.modules[name].EVENTS == [] and statuses()[0]['started'] is False
    assert not (tmp_path / 'portfolio' / 'desk-extensions.sqlite3').exists()


# ── The real application, started in a child process ──

CHILD = r'''
import json, sys
from fastapi.testclient import TestClient
import backend.main as main
from backend import features

client = TestClient(main.app, base_url="http://127.0.0.1")
out = {
    "status": client.get("/api/app/status").json(),
    "hello": client.get("/api/ext/sample/hello").status_code,
    "foreign_host": client.get("/api/ext/sample/hello", headers={"Host": "rebind.example"}).status_code,
    "echo": client.post("/api/ext/sample/echo", json={"a": 1}).status_code,
    "added": client.get("/api/app/other").status_code,
    "health": client.get("/health").json().get("status"),
    "paths": sorted(path for path in main.app.openapi()["paths"] if path.startswith("/api/ext")),
    "background": [factory.__module__ + "." + factory.__name__ for factory in features.background_tasks()],
}
from backend.services.agent_service import AgentService
from core.memory.manager import MemoryManager
from providers.market_data.mock_provider import MockMarketDataProvider
from providers.news.rss_news_provider import RSSNewsProvider
service = AgentService()
service.market_provider = MockMarketDataProvider()
service.news_provider = RSSNewsProvider()
service.memory_manager = MemoryManager(data_file="data/memory.json")
service._init_agent()
out["tools"] = sorted(service.agent.tool_executor.tools)
print("RESULT " + json.dumps(out, ensure_ascii=False))
'''


def child(tmp_path, **variables):
    modules = tmp_path / 'modules'
    work = tmp_path / 'work'
    work.mkdir(exist_ok=True)
    env = {key: value for key, value in os.environ.items()
           if key not in ('TRACKER_EXTENSIONS', 'TRACKER_DEMO_MODE', 'TRACKER_LAB_ONLY', 'TRACKER_ALLOWED_HOSTS')}
    env.update(PYTHONPATH=os.pathsep.join([str(ROOT / 'dashboard'), str(ROOT / 'engine' / 'scripts'), str(modules)]),
               PORTFOLIO_DIR=str(tmp_path / 'portfolio'), TRACKER_CONFIG_FILE=str(tmp_path / 'config.json'),
               TRACKER_BACKTEST_DIR=str(tmp_path / 'backtest'), TRACKER_LAB_DIR=str(tmp_path / 'lab'),
               TRACKER_QUOTE_SOURCE='fake', EXTENSION_MARKER=str(tmp_path / 'imported'), PYTHONWARNINGS='ignore',
               **variables)
    result = subprocess.run([sys.executable, '-c', CHILD], cwd=work, env=env, capture_output=True, text=True,
                            timeout=300)
    assert result.returncode == 0, result.stderr[-3000:]
    [line] = [line for line in result.stdout.splitlines() if line.startswith('RESULT ')]
    return json.loads(line[len('RESULT '):])


def test_the_real_application_loads_extensions_at_start_up(setup, tmp_path):
    name, clash = setup(FULL), setup(CLASH)
    out = child(tmp_path, TRACKER_EXTENSIONS=f'{name},{clash},pdt_no_such_extension')
    assert out['hello'] == 200 and out['echo'] == 200
    assert out['foreign_host'] == 400, 'the host allow-list covers extension routes'
    assert out['paths'] == ['/api/ext/sample/echo', '/api/ext/sample/hello']
    assert out['added'] == 200
    assert set(out['status']) == {'mode', 'ledger', 'version', 'extensions'}, 'the built-in status route answers'
    assert out['health'] == 'healthy', 'routes defined at the end of backend/main.py are built-in too'
    assert [(item['name'], item['status'], item.get('error')) for item in out['status']['extensions']] == [
        (name, 'loaded', None), (clash, 'loaded', None),
        ('pdt_no_such_extension', 'failed', 'ModuleNotFoundError (pdt_no_such_extension)')]
    assert out['background'][-1] == 'backend.extensions.background'
    assert 'sample_radar' in out['tools'] and 'propose_ledger_events' in out['tools']


def test_the_real_application_in_demo_mode_does_not_import_them(setup, tmp_path):
    name = setup(FULL)
    out = child(tmp_path, TRACKER_EXTENSIONS=name, TRACKER_DEMO_MODE='1')
    assert out['hello'] == 404 and out['paths'] == [] and 'sample_radar' not in out['tools']
    assert out['echo'] == 403, 'demo mode refuses every write first'
    assert out['status']['extensions'] == [{'name': name, 'status': 'skipped', 'reason': 'demo'}]
    assert not (tmp_path / 'imported').exists()


def test_the_real_application_without_the_variable_is_unchanged(setup, tmp_path):
    setup(FULL)
    out = child(tmp_path)
    assert set(out['status']) == {'mode', 'ledger', 'version'}
    assert out['hello'] == 404 and out['paths'] == [] and 'sample_radar' not in out['tools']
    assert 'backend.extensions.background' not in out['background']
    assert not (tmp_path / 'imported').exists()
