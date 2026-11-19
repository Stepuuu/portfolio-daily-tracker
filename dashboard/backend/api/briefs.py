"""Pre- and post-market briefs (``core.routines.briefs``). Mounted at ``/api/briefs``.

``GET /?date=YYYY-MM-DD``    both kinds for a date (default: the current A-share
                             trading day): ``{date, current_trading_date,
                             is_trading_day, previous_date, next_date,
                             latest_date, calendar, now, mode, scheduler_alive,
                             items: {premarket, postmarket}}``. ``calendar``
                             maps those dates to 'calendar' or 'weekday_only'
                             (the mainland holiday table does not know the
                             year: the day may be a holiday). Each item has
                             the stored ``brief`` (or null), the scheduler's
                             ``run`` row (``next_attempt_at``: when it is
                             retried, null when it is not), ``state`` /
                             ``state_text`` / ``reason`` (尚未生成 with the time,
                             生成中, 失败 with why and whether it is retried
                             ...), and ``can_run`` / ``run_refusal`` for a
                             manual run (refused in demo mode, while switched
                             off, without a live scheduler, on another day ...).
``GET /{kind}?date=``        one of them.

Generating is ``POST /api/routines/{kind}/run`` (the scheduler's manual run),
which the demo refuses like every write. Reading never creates the store.
Errors are 4xx with a message for the user; storage trouble is 503.
"""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Path, Query

from core.routines import briefs

router = APIRouter()
logger = logging.getLogger(__name__)

DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"
KIND_PATTERN = r"^(premarket|postmarket)$"


def _guard(operation):
    try:
        return operation()
    except briefs.BriefError as exc:
        raise HTTPException(422, str(exc)) from None
    except Exception as exc:
        logger.warning("briefs API failed: %s", type(exc).__name__)
        raise HTTPException(503, "暂时无法读取简报，请稍后重试。") from None


@router.get("")
def day(date: Optional[str] = Query(None, pattern=DATE_PATTERN)):
    return _guard(lambda: briefs.day_view(date))


@router.get("/{kind}")
def one(kind: str = Path(..., pattern=KIND_PATTERN), date: Optional[str] = Query(None, pattern=DATE_PATTERN)):
    def read():
        view = briefs.day_view(date)
        return {key: value for key, value in view.items() if key != "items"} | view["items"][kind]
    return _guard(read)
