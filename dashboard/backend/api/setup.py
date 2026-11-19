"""The first-run setup wizard (plan EN-5). Mounted at ``/api/setup``.

``GET  /status``             the checklist (``core.setup_checks``): ``{mode, items: [{id, label, state,
                             detail, action}], setup_complete, completed_at, dismissed, dismissed_at,
                             ledger_ready}``. ``?quotes=0`` skips the quote probe (the item says so).
``POST /complete``           set ``setup_complete``; ``POST /dismiss`` set ``dismissed`` (Today then
                             never sends the person to /setup). Both return the flags.
``GET  /llm``                presets and the saved model choice; the key is never returned, only
                             ``key: {configured, source: 'config'|'env'|null, env}`` (``core.llm_setup``).
``POST /llm``                save ``{preset, model, base_url?, api_key?, supports_vision?}`` through the
                             existing settings file. An empty ``api_key`` keeps the saved key.
``POST /llm/test``           one bounded request to the saved model: ``{ok, code, message, model,
                             latency_ms?, status?}``; ``message`` is one of fixed Chinese texts.
``GET  /legacy``             the migration draft of a v3.2 legacy file (``?source=holdings|portfolio
                             &date=YYYY-MM-DD``; ``core.legacy_migration.draft``). 404 when there is none.
                             ``sources[].pending`` says whether a source still has something to migrate.
``POST /legacy/proposals``   ``{source, date, token, fills, skip, account_name}``: an ordinary ledger
                             proposal (the shape of ``POST /api/ledger/proposals``), bound to the file's
                             content; it is reviewed and confirmed like any other. An account already in
                             the ledger is never booked again, and a portfolio.json migrated once is
                             refused (422, ``ALREADY_MIGRATED``) whatever account name is asked for.

Reads never create or change a file. Writes are refused in demo mode (403: the
application's middleware answers first, these routes check again) and when
the request comes from a page of another site (403: an ``Origin`` header whose
host is not one the API answers for). Beyond that the guard is the one every
settings route has: ``backend.trusted_hosts`` (loopback names only). Bodies
are read by hand and answered with fixed Chinese messages, so no error ever
echoes what was sent; the API key appears in no response, log line or URL.
"""
import logging
from typing import Any, Optional
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Query, Request

from backend.api.desk_errors import desk_call
from backend.trusted_hosts import allowed_hosts, normalize_host
from core import legacy_migration, llm_setup, setup_checks
from core.ledger import Conflict, LedgerError

logger = logging.getLogger(__name__)
router = APIRouter()

DEMO_REFUSED = '演示模式只读：不能保存设置。'
LAB_REFUSED = '研究模式（TRACKER_LAB_ONLY=1）不使用账本。'


def _writable(request: Request) -> None:
    if setup_checks.mode() == 'demo':
        raise HTTPException(403, DEMO_REFUSED)
    origin = request.headers.get('origin')
    if origin is None:
        return
    hosts = allowed_hosts()
    if '*' in hosts:
        return
    try:
        netloc = urlsplit(origin).netloc
    except ValueError:
        netloc = ''
    host = normalize_host(netloc) if netloc else None
    if not host or not any(host == pattern or (pattern.startswith('*.') and host.endswith(pattern[1:]))
                           for pattern in hosts):
        raise HTTPException(403, '只接受本机页面发来的设置请求。')


async def _body(request: Request) -> Any:
    try:
        return await request.json()
    except ValueError:
        raise HTTPException(422, '请求内容不是有效的 JSON。') from None


# -- checklist and flags --------------------------------------------------------------

@router.get('/status')
def status(quotes: bool = True):
    # A plain ``def``: the ledger, the desk store and the quote probe block.
    return desk_call(lambda: setup_checks.status(probe_quotes=quotes))


@router.post('/complete')
def complete(request: Request):
    _writable(request)
    return desk_call(lambda: setup_checks.mark('completed'))


@router.post('/dismiss')
def dismiss(request: Request):
    _writable(request)
    return desk_call(lambda: setup_checks.mark('dismissed'))


# -- the chat model -----------------------------------------------------------------------

@router.get('/llm')
def llm_settings():
    return llm_setup.view()


@router.post('/llm')
async def save_llm(request: Request):
    _writable(request)
    body = await _body(request)
    try:
        parsed = llm_setup.parse_request(body)
        current = llm_setup.save(parsed)
    except llm_setup.SetupError as exc:
        raise HTTPException(422, str(exc)) from None
    except OSError as exc:
        logger.warning('model settings were not saved: %s', type(exc).__name__)
        raise HTTPException(503, '无法写入配置文件（没有权限或磁盘已满）。') from None
    reloaded = await llm_setup.reload_running_service()
    return {'saved': True, 'current': current, 'reloaded': reloaded}


@router.post('/llm/test')
async def test_llm(request: Request):
    _writable(request)
    return await llm_setup.test_connection()


# -- legacy holdings ------------------------------------------------------------------------

def _legacy(operation):
    if setup_checks.mode() == 'lab':
        raise HTTPException(409, LAB_REFUSED)
    try:
        return desk_call(operation)
    except legacy_migration.LegacyStale as exc:
        raise HTTPException(409, str(exc)) from None
    except legacy_migration.LegacyError as exc:
        raise HTTPException(422, str(exc)) from None
    except Conflict as exc:
        raise HTTPException(409, str(exc)) from None
    except LedgerError as exc:
        raise HTTPException(422, str(exc)) from None


@router.get('/legacy')
def legacy_draft(source: Optional[str] = Query(None, pattern=r'^(holdings|portfolio)$'),
                 date: Optional[str] = Query(None, pattern=r'^\d{4}-\d{2}-\d{2}$')):
    if setup_checks.mode() != 'lab' and not legacy_migration.find():
        raise HTTPException(404, '没有发现旧版持仓文件。')
    return _legacy(lambda: legacy_migration.draft(source, date))


@router.post('/legacy/proposals')
async def legacy_proposal(request: Request):
    _writable(request)
    body = await _body(request)
    if not isinstance(body, dict):
        raise HTTPException(422, '请求内容必须是 JSON 对象。')
    fills, skip = body.get('fills') or {}, body.get('skip') or []
    if not isinstance(fills, dict) or not isinstance(skip, list) or len(fills) > 5000 or len(skip) > 1000:
        raise HTTPException(422, '请求内容无效。')

    def run():
        from backend.api.ledger import get_ledger, with_plan_check
        proposal = legacy_migration.propose(body.get('source'), body.get('date'), body.get('token'), fills, skip,
                                            body.get('account_name'))
        return with_plan_check(proposal, get_ledger())
    return _legacy(run)
