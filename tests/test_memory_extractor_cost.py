"""The extractor never infers a cost price: only a cost stated explicitly in the image or the text is returned.

The model's extraction prompt asks for the cost only where the screenshot or the text states it (成本价, 成本均价,
买入均价 ...) and for null otherwise: never the current price, never a back-calculation from the profit or its
percentage (反推), never market value / quantity, never 0. Whatever the model answers, a position without a
usable stated cost comes back with ``cost_price`` null. Fictional positions; the model is scripted.
"""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.memory.extractor import LLMMemoryExtractor

PROMPT = LLMMemoryExtractor.EXTRACTION_PROMPT
ABSENT = object()


def test_the_prompt_asks_for_a_stated_cost_or_null_and_never_derives_one():
    for derivation in ('反推', 'profit / quantity', 'profit_pct', '市值 / 数量', '估算', '提取当前价格',
                       '不要填 0 或 null', '104.087', '98.06'):
        assert derivation not in PROMPT, derivation
    assert '没有明确写出成本时填 null' in PROMPT
    assert '不要用现价、盈亏、盈亏比例或市值推算成本' in PROMPT


def extracted(answer):
    llm = SimpleNamespace(chat=AsyncMock(return_value=SimpleNamespace(content=json.dumps(answer, ensure_ascii=False))))
    return asyncio.run(LLMMemoryExtractor(llm).extract('看看这张截图', '示例股票 100 股，现价 10.5，浮动盈亏 +50 元'))


def position(cost=ABSENT):
    item = {'symbol': '600001', 'name': '示例股票', 'market': 'a_share', 'quantity': 100}
    if cost is not ABSENT:
        item['cost_price'] = cost
    return item


@pytest.mark.parametrize('cost', [ABSENT, None, 0, 0.0, '0', '', '未知', -10.5, float('nan'), True, [10]])
def test_a_position_without_a_stated_cost_has_no_cost(cost):
    [item] = extracted({'positions': [position(cost)]})['positions']   # NaN reaches the parser as NaN
    assert item['cost_price'] is None


@pytest.mark.parametrize('cost', [10.5, '10.031', 104])
def test_a_stated_cost_is_kept_as_given(cost):
    [item] = extracted({'positions': [position(cost)]})['positions']
    assert item['cost_price'] == cost
