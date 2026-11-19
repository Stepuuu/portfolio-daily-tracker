"""Market breadth and sector boards via akshare. OFF unless ``TRACKER_AGENT_BREADTH=1``.

* ``stock_market_activity_legu``                          涨跌家数 (乐咕乐股, scraped HTML)
* ``stock_board_industry_name_em`` / ``..._concept_...``  行业 / 概念板块涨跌幅 (东方财富)

Same contract as the news provider: lazy import, ``getattr`` guard, timeout,
TTL cache of successes, alias-checked columns, ``{"error", "source"}`` instead
of exceptions. The board lists page through many requests (30 to 60 s from
the maintainers' server), so they get a longer timeout than the 8 s default.
"""
from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Mapping, Optional

from providers.news.cn_news_provider import ERR_COLUMNS, AkshareBase, _Failure, _flag, _text, map_columns

BREADTH_FLAG = 'TRACKER_AGENT_BREADTH'

SRC_ACTIVITY = 'akshare.stock_market_activity_legu（乐咕乐股市场涨跌统计）'
SRC_INDUSTRY = 'akshare.stock_board_industry_name_em（东方财富行业板块）'
SRC_CONCEPT = 'akshare.stock_board_concept_name_em（东方财富概念板块）'

# item label in the two-column activity table -> key we expose
ACTIVITY_ITEMS = {
    '上涨': 'up', '下跌': 'down', '平盘': 'flat', '停牌': 'suspended',
    '涨停': 'limit_up', '真实涨停': 'limit_up_real', '跌停': 'limit_down', '真实跌停': 'limit_down_real',
}
BOARD_COLUMNS = {
    'name': ('板块名称', '名称', 'name'),
    'change_pct': ('涨跌幅', 'change_pct'),
    'turnover_pct': ('换手率',),
    'up_count': ('上涨家数',),
    'down_count': ('下跌家数',),
    'leader': ('领涨股票', '领涨股'),
    'leader_change_pct': ('领涨股票-涨跌幅', '领涨股票涨跌幅'),
}
ACTIVITY_COLUMNS = {'item': ('item', '项目'), 'value': ('value', '值', '数值')}


def _number(text: str) -> Optional[float]:
    match = re.fullmatch(r'\s*(-?\d+(?:\.\d+)?)\s*%?\s*', text or '')
    return float(match.group(1)) if match else None


class BreadthProvider(AkshareBase):
    name = 'akshare-breadth'

    def __init__(self, timeout: float = 8.0, board_timeout: float = 60.0, ttl: float = 120.0,
                 clock: Callable[[], float] = time.monotonic, enabled: Optional[bool] = None,
                 now: Optional[Callable[[], datetime]] = None):
        super().__init__(timeout, ttl, clock, now)
        self.board_timeout = board_timeout
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        return _flag(BREADTH_FLAG) if self._enabled is None else self._enabled

    def _off(self, source: str) -> Dict[str, Any]:
        return _Failure(error=f'市场宽度数据未启用（设置环境变量 {BREADTH_FLAG}=1）', source=source)

    async def market_activity(self) -> Dict[str, Any]:
        if not self.enabled:
            return self._off(SRC_ACTIVITY)
        return await self._cached('activity', '', SRC_ACTIVITY, self.ttl, 0, self._fetch_activity)

    async def boards(self, kind: str = 'industry', limit: int = 10, order: str = 'gainers') -> Dict[str, Any]:
        source = SRC_CONCEPT if kind == 'concept' else SRC_INDUSTRY
        if kind not in ('industry', 'concept'):
            return _Failure(error='kind 只能是 industry 或 concept', source=SRC_INDUSTRY)
        if order not in ('gainers', 'losers'):
            return _Failure(error='order 只能是 gainers 或 losers', source=source)
        if not self.enabled:
            return self._off(source)
        # no truncation inside the cache: sort the whole table first, then cut
        result = await self._cached('board', kind, source, self.ttl, 10 ** 6,
                                    lambda: self._fetch_boards(kind, source))
        if 'error' in result:
            return result
        rows = sorted((r for r in result['items'] if r['change_pct'] is not None),
                      key=lambda r: r['change_pct'], reverse=(order == 'gainers'))
        return {**result, 'items': rows[:max(int(limit), 0)], 'order': order, 'kind': kind}

    async def _fetch_activity(self) -> Dict[str, Any]:
        frame = await self._call('stock_market_activity_legu', SRC_ACTIVITY)
        if isinstance(frame, _Failure):
            return frame
        rows = self._frame_rows(frame, ACTIVITY_COLUMNS, ('item', 'value'), SRC_ACTIVITY)
        if isinstance(rows, _Failure):
            return rows
        table = {row['item'].replace(' ', ''): row['value'] for row in rows}
        counts: Dict[str, Optional[float]] = {}
        for label, key in ACTIVITY_ITEMS.items():
            value = _number(table.get(label, ''))
            counts[key] = int(value) if value is not None else None
        if counts.get('up') is None and counts.get('down') is None:
            return _Failure(error=ERR_COLUMNS, source=SRC_ACTIVITY)
        stamp = table.get('统计日期', '')
        date_part = self._day(stamp)
        return {'source': SRC_ACTIVITY, 'fetched_at': self._now().isoformat(timespec='seconds'),
                'data_date': date_part or None, 'stat_time': stamp[:19] or None,
                'counts': counts, 'activity_pct': _number(table.get('活跃度', ''))}

    async def _fetch_boards(self, kind: str, source: str) -> Dict[str, Any]:
        function = 'stock_board_concept_name_em' if kind == 'concept' else 'stock_board_industry_name_em'
        frame = await self._call(function, source, timeout=self.board_timeout)
        if isinstance(frame, _Failure):
            return frame
        rows = self._frame_rows(frame, BOARD_COLUMNS, ('name', 'change_pct'), source)
        if isinstance(rows, _Failure):
            return rows
        items: List[Dict[str, Any]] = []
        for row in rows:
            if not row['name']:
                continue
            items.append({'name': row['name'], 'change_pct': _number(row['change_pct']),
                          'turnover_pct': _number(row['turnover_pct']),
                          'up_count': _number(row['up_count']), 'down_count': _number(row['down_count']),
                          'leader': row['leader'], 'leader_change_pct': _number(row['leader_change_pct'])})
        fetched = self._now()
        # the board table carries no date of its own: it is a snapshot of the fetch moment
        return {'source': source, 'fetched_at': fetched.isoformat(timespec='seconds'),
                'data_date': fetched.date().isoformat(), 'data_date_basis': 'fetch_time', 'items': items}


class FakeBreadthProvider:
    """Scripted twin: ``script`` keys ``activity`` and ``boards`` (result, exception or callable)."""

    name = 'fake-breadth'

    def __init__(self, script: Optional[Mapping[str, Any]] = None):
        self.script = dict(script or {})
        self.calls: List[tuple] = []

    def _serve(self, kind: str, source: str, *args) -> Dict[str, Any]:
        self.calls.append((kind, *args))
        entry = self.script.get(kind)
        if entry is None:
            return {'error': '没有预置数据', 'source': source}
        if isinstance(entry, BaseException):
            raise entry
        return entry(*args) if callable(entry) else entry

    async def market_activity(self):
        return self._serve('activity', SRC_ACTIVITY)

    async def boards(self, kind: str = 'industry', limit: int = 10, order: str = 'gainers'):
        return self._serve('boards', SRC_CONCEPT if kind == 'concept' else SRC_INDUSTRY, kind, limit, order)


_provider: Any = None


def get_breadth_provider():
    global _provider
    if _provider is None:
        _provider = BreadthProvider()
    return _provider


def set_breadth_provider(provider: Any) -> None:
    global _provider
    _provider = provider
