"""Card pages of extensions (feishu_workbench/pages.py): a module in a temporary directory, named in
TRACKER_EXTENSIONS, that defines workbench_pages()."""
import asyncio
import json
import logging
import sys
import time

from fastapi.testclient import TestClient
import pytest

from feishu_workbench import pages
from feishu_workbench.app import create_app
from feishu_workbench.cards import _escape
from feishu_workbench.portfolio import PortfolioAdapter
from feishu_workbench.service import ACTIONS, Workbench
from feishu_workbench.store import Rejected, Store


PRIVATE = 'synthetic-credential 1234567.89'
PREFIX = 'wbpage_'

NEWS = '''
from feishu_workbench.pages import Refused, button, markdown

SECRET = %r
CALLS = []


def news_open(request):
    CALLS.append(('open', request.actor, request.view))
    return 'news_list', {'items': ['第一条 <b>粗体</b>', '第二条'], 'subtitle': '第 1 页'}


def news_preview(request):
    return 'news_preview', {'note': request.fields.get('note', '')}


def news_confirm(request):
    CALLS.append(('confirm', request.view, dict(request.model)))
    return 'news_list', {'items': ['已保存']}


def news_fail(request):
    raise RuntimeError(SECRET)


def news_refuse(request):
    raise Refused('资讯已过期。')


def news_stray(request):
    return 'home', {'title': 'stray'}


def news_sneak(request):
    return 'news_sneak', {'items': []}


def news_sneak_view(model, context):
    return [button(context, 'daily.confirm', '确认入账')]


def news_list(model, context):
    return [markdown(item) for item in model['items']] + [
        button(context, 'news.open', '刷新'), button(context, 'news.preview', '保存', primary=True)]


def news_preview_view(model, context):
    return [markdown('备注：' + model['note']), button(context, 'news.confirm', '确认', primary=True),
            button(context, 'home', '返回主页')]


def card(text):
    return {'card': {'type': 'raw', 'data': {'schema': '2.0', 'body': {'elements': [
        {'tag': 'markdown', 'content': text}]}}}}


def news_page(click):
    CALLS.append(('page', click.actor, click.chat, click.message, dict(click.value)))
    if click.message != 'om_sent':
        raise Refused('资讯卡片已失效。')
    page = click.value.get('page')
    if page == 'boom':
        raise RuntimeError(SECRET)
    if page == 'huge':
        return card('x' * 40000)
    if page == 'odd':
        return {**card('odd'), 'extra': 1}
    if page == 'toast':
        return {'toast': {'type': 'info', 'content': '已是最后一页。'}}
    return card('第 %%s 页' %% page)


def workbench_pages():
    return [{'actions': {'news.open': news_open, 'news.preview': news_preview, 'news.confirm': news_confirm,
                         'news.fail': news_fail, 'news.refuse': news_refuse, 'news.stray': news_stray,
                         'news.sneak': news_sneak},
             'views': {'news_list': ('市场资讯', news_list), 'news_preview': ('确认保存', news_preview_view),
                       'news_sneak': ('资讯', news_sneak_view)},
             'external': {'news.page': news_page},
             'entry': ('市场资讯', 'news.open')}]
''' % PRIVATE


class Delivery:
    def __init__(self):
        self.cards = []

    def send(self, actor, chat, card, operation_id):
        self.cards.append(card)
        return {'message_id': 'om_' + operation_id.replace('_', ''), 'chat_id': chat or 'private_chat'}

    def patch(self, message, card):
        self.cards.append(card)


@pytest.fixture
def extensions(tmp_path, monkeypatch):
    """Write modules into a temporary directory on sys.path; ``load(name=source, ...)`` names them all."""
    directory = tmp_path / 'ext'
    directory.mkdir()
    monkeypatch.syspath_prepend(str(directory))

    def load(*missing, **modules):
        for name, source in modules.items():
            (directory / f'{name}.py').write_text(source)
        monkeypatch.setenv('TRACKER_EXTENSIONS', ','.join([*missing, *modules]))
        return pages.load()

    yield load
    for name in [name for name in sys.modules if name.startswith(PREFIX)]:
        del sys.modules[name]


@pytest.fixture
def make(tmp_path):
    project = tmp_path / 'project'
    holdings = project / 'portfolio' / 'holdings'
    holdings.mkdir(parents=True)
    (project / 'scripts').mkdir()
    (holdings / '2026-01-02.json').write_text(json.dumps({'date': '2026-01-02', 'groups': {'Example': {
        'cash': 1000, 'cash_balances': {'CNY': 1000}, 'fund': 50, 'positions': []}}}))
    config = {'allowed_users': ['owner'], 'account_id': 'qr', '_token': 'x' * 48,
              'state_dir': str(tmp_path / 'state')}

    def make(found=None):
        return config, Workbench(config, Store(tmp_path / 'state/cards.sqlite3'),
                                 PortfolioAdapter(project, tmp_path / 'state'), None, Delivery(), pages=found)
    return make


def step(controller):
    command = controller.store.claim()
    try:
        controller.execute(command)
    finally:
        controller.store.finish(command)


def open_home(controller):
    identifier = controller.store.open('owner', 'private_chat')
    step(controller)
    return identifier


def callback(session, action, **fields):
    return {'account_id': 'qr', 'event_type': 'card.action.trigger', 'event': {
        'event_id': 'event_example', 'operator': {'open_id': 'owner'},
        'context': {'open_message_id': session['message'], 'open_chat_id': session['chat']},
        'action': {'name': 'qrw_' + session['nonces'][action], 'form_value': fields},
    }}


def press(controller, identifier, action, **fields):
    session = controller.store.session(identifier)
    assert controller.accept(callback(session, action, **fields))['toast']['type'] == 'info'


def external(value, *, message='om_sent', chat='oc_example', actor='owner'):
    """A press on a card another process sent: a value of its own, no nonce."""
    return {'account_id': 'qr', 'event_type': 'card.action.trigger', 'event': {
        'event_id': 'event_external', 'operator': {'open_id': actor},
        'context': {'open_message_id': message, 'open_chat_id': chat},
        'action': {'tag': 'button', 'value': value},
    }}


def buttons(card):
    found = []

    def walk(value):
        if isinstance(value, dict):
            if value.get('tag') == 'button':
                found.append(value)
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(card)
    return found


def labels(card):
    return [button['text']['content'] for button in buttons(card)]


def work(controller, monkeypatch):
    """Run the worker until it has finished one command."""
    finish = controller.store.finish

    def done(command, error=''):
        finish(command, error)
        controller.stopping = True

    monkeypatch.setattr(controller.store, 'finish', done)
    real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, 'sleep', lambda seconds: real_sleep(0))
    try:
        asyncio.run(asyncio.wait_for(controller.worker(), timeout=8))
    finally:
        controller.stopping = False
        monkeypatch.setattr(controller.store, 'finish', finish)


def test_unset_variable_changes_nothing(make, monkeypatch, caplog):
    monkeypatch.delenv('TRACKER_EXTENSIONS', raising=False)

    def no_import(name, *args):
        raise AssertionError(name)

    monkeypatch.setattr(pages.importlib, 'import_module', no_import)
    with caplog.at_level(logging.INFO, logger='feishu_workbench.pages'):
        for found in (pages.load(), pages.load({'TRACKER_EXTENSIONS': ' , '}), pages.load({})):
            assert found.pages == [] and found.action_names() == () and found.entries == []
    monkeypatch.undo()
    assert caplog.records == []
    _, controller = make()
    assert controller.actions == ACTIONS
    open_home(controller)
    assert labels(controller.delivery.cards[-1]) == ['每日更新', '资产', '研究任务', '日报']
    # A card without a nonce is refused as before, whatever its value says.
    for action in ('news.page', 'home', 'daily.open'):
        with pytest.raises(Rejected, match='卡片上下文不完整'):
            controller.accept(external({'qr_workbench': 1, 'action': action}))


def test_module_without_pages_is_imported_once_and_its_backend_hooks_are_not_used(extensions, make):
    found = extensions(wbpage_backend_only='''
CALLED = []
def routers():
    CALLED.append('routers')
    raise RuntimeError('not here')
def background_tasks():
    CALLED.append('background_tasks')
    return []
def on_close_report(report):
    CALLED.append('on_close_report')
''')
    assert found.pages == [] and found.action_names() == ()
    assert sys.modules['wbpage_backend_only'].CALLED == []
    _, controller = make(found)
    assert controller.actions == ACTIONS


def test_failing_import_is_logged_without_its_message_and_the_rest_still_loads(extensions, caplog):
    with caplog.at_level(logging.INFO, logger='feishu_workbench.pages'):
        found = extensions('wbpage_missing', 'not-a-module',
                           # The traceback shows the raising line, so the secret is in a variable.
                           wbpage_broken=f'SECRET = {PRIVATE!r}\nraise RuntimeError(SECRET)\n',
                           wbpage_raising=f'SECRET = {PRIVATE!r}\ndef workbench_pages():\n    raise ValueError(SECRET)\n',
                           wbpage_news=NEWS)
    assert [page.module for page in found.pages] == ['wbpage_news']
    assert found.handles('news.open') and 'news_list' in found.views
    text = caplog.text
    assert 'wbpage_broken not loaded by the card service: import failed: RuntimeError' in text
    assert 'wbpage_missing not loaded by the card service: import failed: ModuleNotFoundError' in text
    assert "'not-a-module': not a Python module name" in text
    assert 'wbpage_raising: workbench pages refused: ValueError' in text
    assert 'wbpage_news: 1 workbench page(s)' in text
    assert PRIVATE not in text and '1234567.89' not in text


def test_page_action_is_accepted_and_its_view_rendered(extensions, make):
    _, controller = make(extensions(wbpage_news=NEWS))
    module = sys.modules['wbpage_news']
    identifier = open_home(controller)
    home = controller.delivery.cards[-1]
    assert labels(home) == ['每日更新', '资产', '研究任务', '日报', '市场资讯']
    [entry] = [button for button in buttons(home) if button['text']['content'] == '市场资讯']
    session = controller.store.session(identifier)
    assert entry['behaviors'][0]['value']['nonce'] == session['nonces']['news.open']

    press(controller, identifier, 'news.open')
    step(controller)
    assert module.CALLS == [('open', 'owner', 'home')]
    card = controller.delivery.cards[-1]
    assert card['header']['title']['content'] == '市场资讯'
    assert card['header']['subtitle']['content'] == '第 1 页'
    text = json.dumps(card, ensure_ascii=False)
    # Escaped as the built-in views escape text from outside.
    assert '<b>' not in text and json.dumps(_escape('第一条 <b>粗体</b>'), ensure_ascii=False) in text
    # The page's own buttons are bound to the new card's nonces.
    session = controller.store.session(identifier)
    assert session['view'] == 'news_list'
    assert [button['behaviors'][0]['value']['nonce'] for button in buttons(card)] == [
        session['nonces']['news.open'], session['nonces']['news.preview']]
    press(controller, identifier, 'news.open')
    step(controller)
    assert module.CALLS[-1] == ('open', 'owner', 'news_list')


@pytest.mark.parametrize('action, message', [
    ('news.fail', '操作未完成，请返回工作台核对状态。'),
    ('news.refuse', '资讯已过期。'),
    ('news.stray', pages.FAILED),
    # A page's card cannot carry a built-in action other than home.
    ('news.sneak', '操作未完成，请返回工作台核对状态。'),
])
def test_handler_that_raises_turns_into_the_refusal_card(extensions, make, monkeypatch, caplog, action, message):
    _, controller = make(extensions(wbpage_news=NEWS))
    identifier = open_home(controller)
    press(controller, identifier, action)
    with caplog.at_level(logging.WARNING):
        work(controller, monkeypatch)
    card = controller.delivery.cards[-1]
    assert controller.store.session(identifier)['view'] == 'error'
    assert message in json.dumps(card, ensure_ascii=False)
    assert controller.store.counts().get('failed') == 1
    assert PRIVATE not in caplog.text and PRIVATE not in json.dumps(controller.delivery.cards, ensure_ascii=False)
    if action == 'news.fail':
        assert 'RuntimeError' in caplog.text and 'news_fail' in caplog.text


def test_confirm_is_accepted_only_from_the_pages_own_preview(extensions, make):
    _, controller = make(extensions(wbpage_news=NEWS))
    module = sys.modules['wbpage_news']
    identifier = open_home(controller)
    # Every card of the session holds a nonce for every action; a confirm from the home card is refused.
    press(controller, identifier, 'news.confirm')
    with pytest.raises(Rejected, match='请先预览'):
        step(controller)
    assert not any(call[0] == 'confirm' for call in module.CALLS)

    press(controller, identifier, 'news.preview', note='第一条')
    step(controller)
    session = controller.store.session(identifier)
    assert session['view'] == 'news_preview'
    assert session['expires'] - time.time() <= 900
    assert labels(controller.delivery.cards[-1]) == ['确认', '返回主页']
    press(controller, identifier, 'news.confirm')
    step(controller)
    assert module.CALLS[-1] == ('confirm', 'news_preview', {'note': '第一条'})
    assert controller.store.session(identifier)['view'] == 'news_list'


def test_external_click_is_answered_and_builtin_actions_still_need_a_nonce(extensions, make, caplog):
    config, controller = make(extensions(wbpage_news=NEWS))
    module = sys.modules['wbpage_news']
    value = {'qr_workbench': 1, 'action': 'news.page', 'batch_id': 'batch_example', 'page': 2}
    answer = controller.accept(external(value))
    assert answer == {'card': {'type': 'raw', 'data': {'schema': '2.0', 'body': {'elements': [
        {'tag': 'markdown', 'content': '第 2 页'}]}}}}
    assert module.CALLS == [('page', 'owner', 'oc_example', 'om_sent', value)]
    assert controller.accept(external({**value, 'page': 'toast'})) == {
        'toast': {'type': 'info', 'content': '已是最后一页。'}}
    # The page decides whether the card is its own; its refusal is shown as is.
    with pytest.raises(Rejected, match='资讯卡片已失效'):
        controller.accept(external(value, message='om_other'))
    # Whatever the page answers, the press is checked first and the answer after.
    with pytest.raises(Rejected, match='操作权限'):
        controller.accept(external(value, actor='stranger'))
    with pytest.raises(Rejected, match='卡片数据无效'):
        controller.accept(external({**value, 'padding': 'x' * 5000}))
    with caplog.at_level(logging.WARNING, logger='feishu_workbench.pages'):
        for page in ('boom', 'huge', 'odd'):
            with pytest.raises(Rejected, match=pages.FAILED):
                controller.accept(external({**value, 'page': page}))
    assert 'news.page failed: RuntimeError' in caplog.text and PRIVATE not in caplog.text
    # Nothing was queued, and built-in actions are refused without a nonce as before.
    assert controller.store.counts() == {}
    for action in ('home', 'daily.open', 'daily.confirm', 'news.open'):
        with pytest.raises(Rejected, match='卡片上下文不完整'):
            controller.accept(external({'qr_workbench': 1, 'action': action}))
    with pytest.raises(Rejected, match='卡片上下文不完整'):
        controller.accept(external({'qr_workbench': 1, 'action': ['news.page']}))
    # The callback route gives Feishu the page's card, or the refusal as a toast.
    with TestClient(create_app(config, controller)) as client:
        headers = {'Authorization': 'Bearer ' + config['_token']}
        assert client.post('/bridge/event', headers=headers, json=external(value)).json() == answer
        refused = client.post('/bridge/event', headers=headers, json=external(value, message='om_other')).json()
        assert refused == {'toast': {'type': 'error', 'content': '资讯卡片已失效。'}}


PAGE = '''
def f(request):
    return {}
def v(model, context):
    return []
def workbench_pages():
    return [{'actions': {'other.ok': f}, 'views': {'other_ok': ('其他', v)}}, %s]
'''


@pytest.mark.parametrize('bad', [
    "{'actions': {'daily.open': f}}",                  # a built-in action
    "{'actions': {'daily.extra': f}}",                 # a built-in namespace
    "{'actions': {'assets.more': f}}",
    "{'external': {'research.page': f}}",
    "{'external': {'reports': f}}",                    # not a page action name (no namespace)
    "{'views': {'home': ('主页', v)}}",                 # a built-in view
    "{'views': {'error': ('错误', v)}}",
    "{'views': {'daily_extra': ('每日', v)}}",          # a built-in namespace
    "{'entry': ('主页', 'home')}",                      # an entry for an action not its own
    "{'actions': {'other.more': 'not callable'}}",
    "{'views': {'other_more': v}}",
    "{'routers': []}",                                 # not a key of a page
])
def test_shadowing_or_invalid_page_refuses_the_module(extensions, make, caplog, bad):
    with caplog.at_level(logging.ERROR, logger='feishu_workbench.pages'):
        found = extensions(wbpage_bad=PAGE % bad)
    assert found.pages == [] and found.views == {} and found.external == {}
    assert 'wbpage_bad: workbench pages refused:' in caplog.text
    _, controller = make(found)
    assert controller.actions == ACTIONS
    open_home(controller)
    assert labels(controller.delivery.cards[-1]) == ['每日更新', '资产', '研究任务', '日报']


def test_name_taken_by_an_earlier_module_refuses_the_later_one(extensions, caplog):
    later = NEWS.replace("'entry': ('市场资讯', 'news.open')", "'entry': None")
    with caplog.at_level(logging.ERROR, logger='feishu_workbench.pages'):
        found = extensions(wbpage_news=NEWS, wbpage_later=later)
    assert [page.module for page in found.pages] == ['wbpage_news']
    assert 'wbpage_later: workbench pages refused: actions: news.open is built in or taken by another page' in caplog.text
