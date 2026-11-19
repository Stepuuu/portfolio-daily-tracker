"""The desk store as one file, for a backup. Mounted at ``/api/desk``. Read-only.

``GET /backup.sqlite3`` streams a copy of ``PORTFOLIO_DIR/desk.sqlite3`` made with
SQLite's backup API (``desk_store.backup``): one consistent state, also while the
alert monitor or a routine writes. It holds what the ledger backup
(``/api/ledger/backup.sqlite3``) does not: alert rules and their fires, trade
plans, the watchlist, the assistant's proposals (including the lessons and
profile changes the person rejected), briefs, routine runs, import sessions and
column mappings, account fee profiles, notifications and the setup flags. Not in
it: the assistant's memory, the model settings and keys, research and backtest
data.

A read like any other: allowed in the read-only demo, answered only for the
hosts the application serves (``trusted_hosts``), never creates the store (404
without one). A busy or unusable store is a 503 (``desk_errors``).
"""
from pathlib import Path
import tempfile

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from backend.api.desk_errors import desk_call
from core import desk_store

router = APIRouter()

NO_DESK_STORE = '还没有桌面数据：提醒、计划、自选、费率等第一次保存后才会生成这个文件。'


@router.get('/backup.sqlite3')
def backup():
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory) / 'desk.sqlite3'
        if not desk_call(lambda: desk_store.backup(target)):
            raise HTTPException(404, NO_DESK_STORE)
        content = target.read_bytes()
    return Response(content, media_type='application/vnd.sqlite3',
                    headers={'Content-Disposition': 'attachment; filename="desk-backup.sqlite3"'})
