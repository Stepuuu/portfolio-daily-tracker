"""Daily routines (the close snapshot, the pre- and post-market briefs). Mounted at ``/api/routines``.

``GET /status``          every kind: today's run, next due time, last success,
                         the runs of earlier days that still have an attempt
                         to come (``outstanding``), and whether the background
                         scheduler is running; ``settings`` holds the briefs'
                         switches and times (``core.routines.briefs.settings``)
                         and the close snapshot's (``close_snapshot``: its
                         switch, fixed time and environment variable).
``GET /runs``            run rows, newest trading day first (``kind``, ``limit``).
``POST /{kind}/run``     run today's ``kind`` now, also after the last automatic
                         retry. Accepted (202) when the scheduler runs; 409 with
                         the reason otherwise. Demo mode rejects it (403) in
                         ``backend.main``.

Errors are 4xx with a message for the user; storage trouble is 503, never 500.
"""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Path, Query

from core.routines import load_kinds, scheduler

router = APIRouter()
logger = logging.getLogger(__name__)

KIND_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"


def _guard(operation):
    try:
        return operation()
    except HTTPException:
        raise
    except scheduler.UnknownRoutine:
        raise HTTPException(404, "没有这个例行任务。") from None
    except scheduler.RunRefused as exc:
        raise HTTPException(409, str(exc)) from None
    except Exception as exc:
        logger.warning("routines API failed: %s", type(exc).__name__)
        raise HTTPException(503, "暂时无法读取或保存例行任务记录，请稍后重试。") from None


def _require_known(kind: str) -> None:
    load_kinds()
    if kind not in {routine.kind for routine in scheduler.registered()}:
        raise HTTPException(404, "没有这个例行任务。")


def _snapshot_settings() -> dict:
    """The close snapshot's switch as the routine reads it (``TRACKER_CLOSE_SNAPSHOT=0`` turns it off)."""
    import os
    from core.routines import close_snapshot
    off = os.environ.get(close_snapshot.DISABLE_ENV, "").strip().lower() in {"0", "off", "false", "no"}
    return {"enabled": not off, "time": f"{close_snapshot.DUE_TIME:%H:%M}", "env": {"enabled": close_snapshot.DISABLE_ENV}}


@router.get("/status")
def status():
    def report():
        from core.routines import briefs
        return {**scheduler.status_report(), "settings": {**briefs.settings(), "close_snapshot": _snapshot_settings()}}
    return _guard(report)


@router.get("/runs")
def runs(kind: Optional[str] = Query(None, pattern=KIND_PATTERN),
         limit: int = Query(20, ge=1, le=scheduler.MAX_LIMIT)):
    if kind is not None:
        _require_known(kind)
    return _guard(lambda: {"items": scheduler.runs(kind, limit)})


@router.post("/{kind}/run", status_code=202)
def run_now(kind: str = Path(..., pattern=KIND_PATTERN)):
    _require_known(kind)
    running = scheduler.current()
    if running is None or not running.alive():
        raise HTTPException(409, "后台调度器未运行：例行任务只在后端运行期间执行，研究模式（TRACKER_LAB_ONLY=1）不运行。")
    row = _guard(lambda: running.request_run(kind))
    return {"accepted": True, "run": row, "message": "已安排运行，稍后刷新状态查看结果。"}
