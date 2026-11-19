"""Reads never write: every GET route the app registers and every agent tool that is not a ``propose_*``
tool leave every file as it was.

Each is called against a portfolio directory whose ``ledger.sqlite3`` and ``desk.sqlite3`` are missing,
zero bytes long, or the populated demo (``create_demo --ledger``), in normal and in demo mode, and against
the demo with its ledger switched to WAL (closed cleanly, so no ``-wal`` / ``-shm`` file exists) and a
pending ledger proposal (so its page read, ``/api/ledger/proposals/{id}``, meets a real one). After
every call the files under the test's directory (the portfolio, the working directory, the configuration
file's place, the backtest and lab directories) must be the same list with the same bytes: no file
created, grown, changed or given a sidecar that remains.

The routes are enumerated from ``app.routes`` and the tools from the list the chat service builds
(``AgentService._init_agent``), so a new route or tool is swept without editing this file. Path and
query parameters are synthesised from their names; no route is skipped (``SKIPPED`` would name one, with
its reason). The network is switched off: a call that would fetch answers from its failure path.
"""
import asyncio
import hashlib
import inspect
import shutil
import socket
import sqlite3
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

needs_backend = pytest.mark.skipif(sys.version_info < (3, 10), reason='backend.main needs Python 3.10+')
pytestmark = needs_backend

DEMO_DAY = date(2026, 11, 17)

# Routes not called, with the reason. Empty: every GET route is called.
SKIPPED = {}

# Path parameters: every value is tried. Unknown names get "1".
PATH_VALUES = {
    'rule_id': ['1', '6'], 'kind': ['premarket', 'postmarket'], 'group_name': ['长期账户', 'x'],
    'account': ['长期账户', 'A1'], 'symbol': ['SHA:DEMO', '600519'], 'keyword': ['DEMO'],
    'proposal_id': ['1', 'x'], 'session_id': ['x'], 'date': ['2026-11-17'], 'image_path': ['x.png'],
    'plan': ['1', '2'], 'run_id': ['x'], 'identifier': ['x'], 'strategy_id': ['ma_cross', 'x'],
}
# Query parameters by name: required ones always, optional ones on a second call. Unknown names get "1".
QUERY_VALUES = {
    'date': '2026-11-17', 'as_of': '2026-11-17', 'ticker': 'SHA:DEMO', 'symbol': 'SHA:DEMO', 'tickers': 'SHA:DEMO',
    'q': 'DEMO', 'query': 'DEMO', 'keyword': 'DEMO', 'market': '1', 'quotes': '1', 'refresh': '1', 'limit': '5',
    'offset': '0', 'status': 'active', 'account': '长期账户', 'kind': 'postmarket', 'start': '2026-01-05',
    'end': '2026-11-13', 'start_date': '2026-01-05', 'end_date': '2026-11-13', 'adjust': 'qfq', 'format': 'json',
    'quantity': '100', 'price': '10', 'amount': '1000', 'side': 'buy', 'direction': 'buy', 'currency': 'CNY',
    'strategy_name': 'x', 'metric': 'sharpe_ratio', 'top_n': '3', 'order_by': 'run_date DESC', 'days': '5',
    'probe': '1', 'enabled': '1', 'source': 'manual', 'unread': '1', 'before': '0', 'after': '0',
}
# Tool arguments by name and type.
TOOL_VALUES = {
    'ticker': 'SHA:DEMO', 'symbol': 'SHA:DEMO', 'tickers': ['SHA:DEMO', 'NASDAQ:DEMO'], 'symbols': ['SHA:DEMO'],
    'date': '2026-11-17', 'kind': 'postmarket', 'keyword': 'DEMO', 'market': 'CN', 'account': 'A1',
    'status': 'active', 'start': '2026-01-05', 'end': '2026-11-13', 'trading_date': '2026-11-17',
}


def fingerprint(root):
    """Relative path -> (size, sha256) of every file under ``root``."""
    return {str(path.relative_to(root)): (path.stat().st_size, hashlib.sha256(path.read_bytes()).hexdigest())
            for path in sorted(Path(root).rglob('*')) if path.is_file()}


def difference(before, after):
    created = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(name for name in set(before) & set(after) if before[name] != after[name])
    return {key: value for key, value in (('created', created), ('removed', removed), ('changed', changed)) if value}


def _no_network(*args, **kwargs):
    raise OSError('the network is switched off in this test')


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(socket.socket, 'connect', _no_network)
    monkeypatch.setattr(socket.socket, 'connect_ex', _no_network)
    monkeypatch.setattr(socket, 'create_connection', _no_network)
    monkeypatch.setattr(socket, 'getaddrinfo', _no_network)


def _demo(directory):
    from create_demo import create_demo
    create_demo(directory, ledger=True, today=DEMO_DAY)


def _wal(directory):
    """The demo with a WAL ledger and one pending ledger proposal; returns that proposal's id as a path value."""
    from core.demo_seed import LONG_ACCOUNT
    from core.ledger import Ledger
    _demo(directory)
    db = sqlite3.connect(directory / 'ledger.sqlite3')
    assert db.execute('PRAGMA journal_mode=WAL').fetchone()[0] == 'wal'
    db.close()
    pending = Ledger(directory / 'ledger.sqlite3').propose([
        {'kind': 'deposit', 'date': '2026-11-16', 'account': LONG_ACCOUNT, 'currency': 'CNY', 'amount': '100',
         'source': 'test', 'external_id': 'read-sweep:pending'}])['id']
    for sidecar in ('-wal', '-shm'):     # the writer's own, gone once it closed cleanly
        assert not (directory / f'ledger.sqlite3{sidecar}').exists()
    return {'proposal_id': [pending]}


def _missing(directory):
    directory.mkdir(parents=True)


def _empty(directory):
    directory.mkdir(parents=True)
    (directory / 'ledger.sqlite3').write_bytes(b'')
    (directory / 'desk.sqlite3').write_bytes(b'')


STATES = {'missing': _missing, 'empty': _empty, 'demo': _demo, 'demo_wal': _wal}
VARIANTS = [('missing', False), ('missing', True), ('empty', False), ('empty', True), ('demo', False),
            ('demo', True), ('demo_wal', True)]


def _reset():
    from config import settings
    from core import ledger
    from core.market import search
    from core.market.runtime import set_market_runtime
    set_market_runtime(None)
    settings._config = None
    search.set_symbol_index(None)
    with ledger._snapshot_lock:
        ledger._snapshots.clear()


@pytest.fixture(params=VARIANTS, ids=[f"{state}-{'demo' if demo else 'normal'}" for state, demo in VARIANTS])
def world(request, tmp_path, monkeypatch, offline):
    state, demo = request.param
    portfolio = tmp_path / 'portfolio'
    extra = STATES[state](portfolio) or {}
    path_values = {name: PATH_VALUES.get(name, []) + extra.get(name, []) for name in {*PATH_VALUES, *extra}}
    work = tmp_path / 'work'
    work.mkdir()
    monkeypatch.chdir(work)                 # importing the application creates data directories here
    monkeypatch.setenv('PORTFOLIO_DIR', str(portfolio))
    monkeypatch.setenv('TRACKER_CONFIG_FILE', str(tmp_path / 'settings' / 'config.json'))
    monkeypatch.setenv('TRACKER_BACKTEST_DIR', str(tmp_path / 'backtest'))
    monkeypatch.setenv('TRACKER_LAB_DIR', str(tmp_path / 'lab'))
    monkeypatch.setenv('TRACKER_QUOTE_SOURCE', 'fake')
    monkeypatch.delenv('TRACKER_ALERTS_ENABLED', raising=False)
    monkeypatch.delenv('TRACKER_LAB_ONLY', raising=False)
    if demo:
        monkeypatch.setenv('TRACKER_DEMO_MODE', '1')
    else:
        monkeypatch.delenv('TRACKER_DEMO_MODE', raising=False)
    _reset()
    yield SimpleNamespace(root=tmp_path, portfolio=portfolio, state=state, demo=demo, path_values=path_values)
    _reset()


def _value(name, table):
    return table.get(name, '1')


def _get_routes(app):
    """``{path: [OpenAPI parameter, ...]}`` of every GET route the application registers.

    The API routes come from the application's own OpenAPI schema (FastAPI builds it from every route of
    every included router, whatever object it keeps them in; no route is hidden from it); the plain
    Starlette routes (the schema and the docs pages themselves) from ``app.routes``.
    """
    from fastapi.routing import APIRoute
    schema = app.openapi()
    routes = {path: operations['get'].get('parameters', []) for path, operations in schema['paths'].items()
              if 'get' in operations}
    for route in app.routes:
        if not isinstance(route, APIRoute) and 'GET' in (getattr(route, 'methods', None) or set()):
            routes.setdefault(route.path, [])
    return routes


def _calls(path, parameters, path_values=PATH_VALUES):
    """``(path, params)`` for one GET route: every path value, without and with the optional query parameters."""
    paths = [path]
    for parameter in parameters:
        if parameter.get('in') == 'path':
            name = parameter['name']
            paths = [item.replace('{' + name + '}', value) for item in paths for value in path_values.get(name, ['1'])]
    query = [parameter for parameter in parameters if parameter.get('in') == 'query']
    required = {parameter['name']: _value(parameter['name'], QUERY_VALUES) for parameter in query
                if parameter.get('required')}
    optional = {parameter['name']: _value(parameter['name'], QUERY_VALUES) for parameter in query
                if not parameter.get('required')}
    calls = [(item, dict(required)) for item in paths]
    if optional:
        calls += [(item, {**required, **optional}) for item in paths]
    return calls


def _tools():
    """The tools the chat service registers (``AgentService._init_agent``), on offline providers."""
    from backend.services.agent_service import AgentService
    from core.memory.manager import MemoryManager
    from providers.market_data.mock_provider import MockMarketDataProvider
    from providers.news.rss_news_provider import RSSNewsProvider
    service = AgentService()
    service.market_provider = MockMarketDataProvider()
    service.news_provider = RSSNewsProvider()
    # As _init_memory_manager, without installing it as the lessons' writer (a module-wide hook).
    service.memory_manager = MemoryManager(data_file='data/memory.json')
    service._init_agent()
    return service, service.agent.tool_executor.tools


def _arguments(tool):
    from core.tools import ToolParameterType
    arguments = {}
    for parameter in tool.parameters:
        if parameter.name in TOOL_VALUES:
            arguments[parameter.name] = TOOL_VALUES[parameter.name]
        elif parameter.type == ToolParameterType.ARRAY:
            arguments[parameter.name] = ['SHA:DEMO']
        elif parameter.type == ToolParameterType.NUMBER:
            arguments[parameter.name] = 5
        elif parameter.type == ToolParameterType.BOOLEAN:
            arguments[parameter.name] = False
        elif parameter.type == ToolParameterType.OBJECT:
            arguments[parameter.name] = {}
        else:
            arguments[parameter.name] = 'SHA:DEMO'
    return arguments


def test_every_read_leaves_every_file_as_it_was(world):
    from fastapi.testclient import TestClient
    import backend.main as main
    from core.lab_service import LabService

    main.app.state.lab_service = LabService(world.root / 'lab', demo=world.demo)
    client = TestClient(main.app, base_url='http://127.0.0.1', raise_server_exceptions=False)
    service, tools = _tools()
    before = fingerprint(world.root)
    writes, called = [], set()

    def check(what):
        nonlocal before
        after = fingerprint(world.root)
        if after != before:
            writes.append((what, difference(before, after)))
            before = after

    routes = _get_routes(main.app)
    assert len(routes) > 80, 'the application registers its GET routes'
    for route, parameters in routes.items():
        if route in SKIPPED:
            continue
        for path, params in _calls(route, parameters, world.path_values):
            client.get(path, params=params)
            called.add(route)
            check(f'GET {path} {params or ""}'.strip())
    assert called == set(routes) - set(SKIPPED)

    assert len(tools) > 15, 'the chat service registers its tools'
    swept = [name for name in tools if not name.startswith('propose_')]
    for name in swept:
        tool = tools[name]
        arguments = _arguments(tool)
        asyncio.run(service.agent.tool_executor.execute_tool(name, arguments))
        check(f'tool {name}({arguments})')
        if inspect.signature(tool.function).parameters:
            asyncio.run(service.agent.tool_executor.execute_tool(name, {}))
            check(f'tool {name}()')

    # The context every chat turn is given reads the ledger, the alerts and the watchlist too.
    asyncio.run(service._build_context())
    check('chat context')

    if writes:
        pytest.fail(f'{len(writes)} reads wrote:\n' + '\n'.join(f'{what[:160]}: {change}' for what, change in writes),
                    pytrace=False)
