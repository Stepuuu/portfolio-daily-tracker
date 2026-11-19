"""Trade plans. Mounted at ``/api/plans``.

``GET    ''``               ``{items: [plan], as_of}``, newest first; ``?status=draft|active|closed|cancelled|all``
                            and ``?ticker=SHA:600000`` filter; ``?market=1`` adds each plan's ``market``
                            (below; null for an ended plan) from one Today payload and one quote request
``POST   ''``               create a draft (201); body: the plan fields of ``core.plans`` (``ticker``,
                            ``thesis`` and ``invalidation`` required; ``account``, ``entry_low``,
                            ``entry_high``, ``stop_price``, ``target_price``, ``planned_weight_pct`` or
                            ``planned_quantity``, ``review_on``, ``direction`` = ``buy`` optional)
``GET    /{id}``            one plan in detail (below)
``PATCH  /{id}``            change fields; a draft may change all, an active plan all but ticker, account and
                            direction; ``null`` or ``""`` clears an optional field. ``if_version`` (the plan's
                            ``version`` the edit was made against) refuses the edit with 409 when the plan
                            has changed since, so a stale form never reverts a newer change
``POST   /{id}/activate``   draft -> active; creates the alert rules. Repeating it repairs them
``POST   /{id}/close``      active -> closed; body ``{reason}`` (required); disables the alert rules
``POST   /{id}/cancel``     draft or active -> cancelled; body ``{reason?}`` optional; disables the alert rules

Every plan, in the list and in detail, carries ``identity``: its listing as ``core.market.search.identity_key``
gives it (``HKG:700`` = ``HKG:00700``, ``NYSE:BRK-B`` = ``NYSE:BRK.B``), the key the alerts, watchlist and Today
payloads carry for matching holdings.

Every response except the list is the plan in detail: the plan, plus

``links``        the confirmed ledger buys and sells linked to it (``core.plans.links_for_plan``); null with
                 ``links_error`` (Chinese) when they cannot be read, never an empty list then
``alerts``       its own alert rules (``source = plan:<id>``), as ``/api/alerts/rules`` items without ``market``;
                 null with ``alerts_error`` when they cannot be read
``alert_notes``  Chinese notes where an expected alert is covered by another rule or missing
``market``       the plan against the Today payload (``core.plans.market_view``): the instrument's
                 ``name``, price, quote, position quantity and weight in the plan's scope, distance to stop
                 and target, weight against the planned weight; null with ``price_reason`` /
                 ``weight_reason`` when unknown

and ``max_loss`` converted to CNY at the current rate for HKD and USD plans, with the rate's
``fx_source``, ``fx_quote_time`` and ``fx_status``, and ``estimated`` / ``estimated_reason`` when the rate
is not verified (the list items too).

Request bodies are read with every JSON number taken from its text
(``Decimal``), never through a binary float, so ``1000000000000.00000001`` is
refused as above the maximum instead of rounding into range; malformed JSON,
``NaN`` and ``Infinity`` are a 422.

The user saves plans here directly; the agent can only propose one
(``/api/proposals``). Errors are ``{"detail": "<中文说明>"}`` with 404 (no such
plan; an ``{id}`` that is not 1-16 ASCII digits is no such plan), 409 (the status does not allow it, or another active plan covers the
ticker and account) or 422 (invalid input); a busy store is 503. In demo mode
the existing middleware rejects every write with 403; reads work and create
nothing: the desk store is read only when it exists, the ledger only through
a read-only connection (``core.plans.read_only_ledger``), so no file is
created, grown or changed.

Plain ``def`` routes: FastAPI runs them in a worker thread, so the quote
requests and SQLite calls do not block the event loop.
"""
import json
import logging
import re
from decimal import Decimal
from typing import Any, Mapping, Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.api.desk_errors import desk_call
from core import plans
from core.alerts import rules
from core.market.runtime import get_market_runtime
from core.market.search import identity_key
from core.market.today import moment

logger = logging.getLogger(__name__)
router = APIRouter()

NOT_FOUND = '交易计划不存在。请刷新列表。'
# A plan id in a path: ASCII digits only. ``str.isdigit`` and ``int`` also take
# '²', full-width and other scripts' digits, which are not plan numbers.
_ID = re.compile(r'[0-9]{1,16}')


NOT_JSON = '请求内容不是有效的 JSON。数字请直接写出，例如 {"stop_price": "9.5"}。'


def _constant(name):
    raise ValueError(f'{name} is not a number')


async def exact_json(request: Request) -> Any:
    """The JSON body with every number read exactly (``Decimal``); ``None`` when there is none.

    ``fastapi.Body`` parses JSON numbers into floats, and a float can round an
    out-of-range or over-precise number into an accepted one before the plan
    rules see it. Here the plan rules get the number as written.
    """
    raw = await request.body()
    if not raw.strip():
        return None
    try:
        return json.loads(raw, parse_float=Decimal, parse_constant=_constant)
    except (ValueError, RecursionError):   # JSONDecodeError and UnicodeDecodeError are ValueErrors
        raise HTTPException(422, NOT_JSON) from None


def attempt(operation):
    try:
        return desk_call(operation)
    except plans.PlanNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    except plans.PlanConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except (plans.PlanError, rules.AlertRuleError) as exc:
        raise HTTPException(422, str(exc)) from None


def plan_id(text: str) -> int:
    """A path id; anything that is not a plan number (1-16 ASCII digits, in range) is a plan that does not exist."""
    if not isinstance(text, str) or not _ID.fullmatch(text) or not 0 < int(text) <= plans.MAX_ID:
        raise plans.PlanNotFound(NOT_FOUND)
    return int(text)


def _ledger():
    """The ledger for the market block, opened read-only: a read never creates or changes ``ledger.sqlite3``."""
    try:
        return plans.read_only_ledger()
    except Exception as exc:   # a broken ledger must not hide the plan
        logger.warning('trade plan view could not open the ledger: %s', type(exc).__name__)
        return None


ALERTS_UNAVAILABLE = '提醒规则暂时无法读取（不可用），不代表没有提醒。'
LINKS_UNAVAILABLE = '关联成交暂时无法读取（不可用），不代表没有成交。'


def _alerts(plan: Mapping) -> dict:
    try:
        return {'alerts': plans.alert_rules(plan['id']), 'alerts_error': None, 'alert_notes': plans.alert_notes(plan)}
    except Exception as exc:
        logger.warning('trade plan %s alert rules could not be read: %s', plan['id'], type(exc).__name__)
        return {'alerts': None, 'alerts_error': ALERTS_UNAVAILABLE, 'alert_notes': []}


def detail(plan: Mapping) -> dict:
    """The plan with links, alerts and market. Only the plan itself can fail; the rest degrades to nulls."""
    try:
        runtime = get_market_runtime()
    except Exception as exc:   # the write has committed; the view only loses its market part
        logger.warning('trade plan view has no market runtime: %s', type(exc).__name__)
        runtime = None
    [plan] = plans.with_fx([plan], runtime)
    [plan] = plans.with_market([plan], runtime, _ledger())
    try:
        links, links_error = plans.links_for_plan(plan['id']), None
    except Exception as exc:
        logger.warning('trade plan %s links could not be read: %s', plan['id'], type(exc).__name__)
        links, links_error = None, LINKS_UNAVAILABLE
    return {**plan, 'identity': identity_key(plan.get('ticker')), 'links': links, 'links_error': links_error,
            **_alerts(plan)}


def _reason(payload: Any, required: bool) -> Optional[str]:
    if payload is None:
        if required:
            raise plans.PlanError('请写明关闭原因，例如“已到目标价卖出”或“跌破止损已离场”。')
        return None
    if not isinstance(payload, Mapping):
        raise plans.PlanError('请求内容必须是一个 JSON 对象，例如 {"reason": "已到目标价卖出"}。')
    extra = sorted(str(key) for key in payload if key != 'reason')
    if extra:
        raise plans.PlanError('不支持的字段：' + '、'.join(extra) + '。')
    return payload.get('reason')


def _flag(value: Optional[str]) -> bool:
    if value in (None, '', '0', 'false'):
        return False
    if value in ('1', 'true'):
        return True
    raise plans.PlanError('market 只能是 1 或 0。')


@router.get('')
def list_plans(status: Optional[str] = None, ticker: Optional[str] = None, market: Optional[str] = None):
    def run():
        with_market = _flag(market)
        items = plans.with_fx(plans.list_plans(status=status, ticker=ticker))
        runtime = get_market_runtime()
        if with_market:
            open_plans = [plan for plan in items if plan['status'] not in plans.ENDED]
            views = {plan['id']: plan['market'] for plan in plans.with_market(open_plans, runtime, _ledger())}
            items = [{**plan, 'market': views.get(plan['id'])} for plan in items]
        items = [{**plan, 'identity': identity_key(plan.get('ticker'))} for plan in items]
        return {'items': items, 'as_of': moment(runtime.clock.now())}
    return attempt(run)


@router.post('', status_code=201)
def create_plan(payload: Any = Depends(exact_json)):
    return attempt(lambda: detail(plans.create(payload, source='manual')))


@router.get('/{plan}')
def get_plan(plan: str):
    return attempt(lambda: detail(plans.get(plan_id(plan))))


@router.patch('/{plan}')
def update_plan(plan: str, changes: Any = Depends(exact_json)):
    def run():
        body, expected = changes, None
        if isinstance(changes, Mapping) and 'if_version' in changes:
            body = {key: value for key, value in changes.items() if key != 'if_version'}
            expected = changes['if_version']
        return detail(plans.update(plan_id(plan), body, expected_version=expected))
    return attempt(run)


@router.post('/{plan}/activate')
def activate_plan(plan: str):
    return attempt(lambda: detail(plans.activate(plan_id(plan))))


@router.post('/{plan}/close')
def close_plan(plan: str, payload: Any = Depends(exact_json)):
    return attempt(lambda: detail(plans.close(plan_id(plan), _reason(payload, required=True))))


@router.post('/{plan}/cancel')
def cancel_plan(plan: str, payload: Any = Depends(exact_json)):
    return attempt(lambda: detail(plans.cancel(plan_id(plan), _reason(payload, required=False))))
