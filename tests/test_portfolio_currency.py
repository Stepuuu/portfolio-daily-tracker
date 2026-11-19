"""Legacy portfolio amounts carry a currency and are never added across currencies. Fictional data."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from core.models import CASH_CURRENCY, Market, Portfolio, Position, Stock, currency_symbol


def position(symbol, market, quantity, cost, price):
    return Position(stock=Stock(symbol=symbol, name=f"Example {symbol}", market=market),
                    quantity=quantity, available_qty=quantity, cost_price=cost, current_price=price)


def a_share():
    return position("600000", Market.A_SHARE, 100, 10.0, 12.0)      # value 1200, profit +200


def hk_stock():
    return position("00001", Market.HK_STOCK, 200, 50.0, 40.0)      # value 8000, profit -2000


def us_stock():
    return position("EXM", Market.US_STOCK, 10, 100.0, 150.0)       # value 1500, profit +500


def test_each_market_has_one_currency():
    assert {m.value: m.currency for m in Market} == {"a_share": "CNY", "hk_stock": "HKD", "us_stock": "USD"}
    assert [p.currency for p in (a_share(), hk_stock(), us_stock())] == ["CNY", "HKD", "USD"]
    assert CASH_CURRENCY == "CNY"


def test_position_dict_names_its_currency():
    data = hk_stock().to_dict()
    assert data["currency"] == "HKD" and data["market"] == "hk_stock"
    assert data["market_value"] == 8000 and data["profit"] == -2000


def test_single_currency_portfolio_keeps_its_plain_totals():
    portfolio = Portfolio(positions=[a_share(), position("000001", Market.A_SHARE, 10, 20.0, 15.0)], cash=300.0)
    assert portfolio.currencies == ["CNY"] and portfolio.currency == "CNY" and portfolio.mixed_currency is False
    assert portfolio.total_market_value == 1350
    assert portfolio.total_profit == 150
    assert portfolio.total_assets == 1650
    assert portfolio.totals_by_currency == {"CNY": {
        "positions": 2, "market_value": 1350, "cost_value": 1200, "profit": 150,
        "profit_pct": 12.5, "cash": 300.0, "total_assets": 1650}}


def test_mixed_portfolio_is_totalled_per_currency_and_never_summed():
    portfolio = Portfolio(positions=[a_share(), hk_stock(), us_stock()], cash=500.0)
    assert portfolio.currencies == ["CNY", "HKD", "USD"]
    assert portfolio.mixed_currency is True and portfolio.currency is None
    totals = portfolio.totals_by_currency
    assert list(totals) == ["CNY", "HKD", "USD"]
    assert (totals["CNY"]["market_value"], totals["CNY"]["profit"], totals["CNY"]["cash"], totals["CNY"]["total_assets"]) == (1200, 200, 500.0, 1700)
    assert (totals["HKD"]["market_value"], totals["HKD"]["profit"], totals["HKD"]["cash"], totals["HKD"]["total_assets"]) == (8000, -2000, 0.0, 8000)
    assert (totals["USD"]["market_value"], totals["USD"]["profit"], totals["USD"]["cash"], totals["USD"]["total_assets"]) == (1500, 500, 0.0, 1500)
    assert totals["HKD"]["profit_pct"] == pytest.approx(-20.0) and totals["USD"]["profit_pct"] == pytest.approx(50.0)
    # No single figure exists; in particular not the cross-currency sum the old model returned.
    assert portfolio.total_market_value is None
    assert portfolio.total_assets is None
    assert portfolio.total_profit is None


def test_one_foreign_currency_without_cash_is_not_mixed():
    portfolio = Portfolio(positions=[us_stock()])
    assert portfolio.currencies == ["USD"] and portfolio.currency == "USD" and portfolio.mixed_currency is False
    assert portfolio.total_market_value == 1500 and portfolio.total_assets == 1500 and portfolio.total_profit == 500


def test_cash_is_cny_so_foreign_positions_plus_cash_are_mixed():
    portfolio = Portfolio(positions=[us_stock()], cash=100.0)
    assert portfolio.currencies == ["CNY", "USD"] and portfolio.mixed_currency is True
    assert portfolio.total_assets is None
    totals = portfolio.totals_by_currency
    assert totals["CNY"] == {"positions": 0, "market_value": 0, "cost_value": 0, "profit": 0,
                             "profit_pct": 0.0, "cash": 100.0, "total_assets": 100.0}
    assert totals["USD"]["cash"] == 0.0 and totals["USD"]["total_assets"] == 1500


def test_empty_portfolio_reports_zero_cny():
    portfolio = Portfolio()
    assert portfolio.currencies == ["CNY"] and portfolio.mixed_currency is False
    assert (portfolio.total_market_value, portfolio.total_assets, portfolio.total_profit) == (0, 0, 0)
    assert portfolio.totals_by_currency["CNY"]["positions"] == 0
    assert "(空仓)" in portfolio.to_summary()


def test_cny_summary_text_is_unchanged():
    summary = Portfolio(positions=[a_share()], cash=300.0).to_summary()
    assert summary.splitlines()[:5] == [
        "【持仓概览】", "总资产: ¥1,500.00", "持仓市值: ¥1,200.00", "可用现金: ¥300.00", "总盈亏: ¥+200.00"]
    assert "100股 @ ¥10.00 → ¥12.00 | 盈亏: +20.00% (¥+200.00)" in summary


def test_mixed_summary_uses_each_currency_symbol_and_no_combined_total():
    summary = Portfolio(positions=[a_share(), hk_stock(), us_stock()], cash=500.0).to_summary()
    assert "CNY: 持仓市值 ¥1,200.00 | 盈亏 ¥+200.00 (+20.00%) | 可用现金 ¥500.00 | 总资产 ¥1,700.00" in summary
    assert "HKD: 持仓市值 HK$8,000.00 | 盈亏 HK$-2,000.00 (-20.00%)" in summary
    assert "USD: 持仓市值 $1,500.00 | 盈亏 $+500.00 (+50.00%)" in summary
    assert "200股 @ HK$50.00 → HK$40.00 | 盈亏: -20.00% (HK$-2,000.00)" in summary
    assert "10股 @ $100.00 → $150.00 | 盈亏: +50.00% ($+500.00)" in summary
    for mixed_sum in ("10,700.00", "11,200.00", "-1,300.00"):   # 1200+8000+1500, +500 cash, 200-2000+500
        assert mixed_sum not in summary
    assert "¥8,000.00" not in summary and "¥1,500.00" not in summary


def test_unknown_currency_code_is_shown_as_the_code():
    assert currency_symbol("CNY") == "¥" and currency_symbol("HKD") == "HK$" and currency_symbol("USD") == "$"
    assert currency_symbol("EUR") == "EUR "


def service_with(portfolio):
    from backend.services.agent_service import AgentService
    service = AgentService()

    async def get_portfolio():
        return portfolio

    service.portfolio_provider = SimpleNamespace(get_portfolio=get_portfolio)
    return service


def test_service_response_carries_currency_and_per_currency_totals():
    data = asyncio.run(service_with(Portfolio(positions=[a_share(), hk_stock()], cash=500.0)).get_portfolio())
    assert [p["currency"] for p in data["positions"]] == ["CNY", "HKD"]
    assert [p["market"] for p in data["positions"]] == ["a_share", "hk_stock"]
    assert data["mixed_currency"] is True and data["currency"] is None and data["currencies"] == ["CNY", "HKD"]
    assert data["cash"] == 500.0 and data["cash_currency"] == "CNY"
    assert data["total_market_value"] is None and data["total_assets"] is None and data["total_profit"] is None
    assert data["totals_by_currency"]["HKD"]["market_value"] == 8000
    assert data["totals_by_currency"]["CNY"]["total_assets"] == 1700
    json.dumps(data)   # the API returns this as JSON


def test_service_response_for_one_currency_keeps_the_old_fields():
    data = asyncio.run(service_with(Portfolio(positions=[a_share()], cash=300.0)).get_portfolio())
    assert data["mixed_currency"] is False and data["currency"] == "CNY"
    assert (data["total_market_value"], data["total_assets"], data["total_profit"]) == (1200, 1500, 200)
    assert {"symbol", "name", "market", "quantity", "available_qty", "cost_price", "current_price",
            "profit", "profit_pct", "market_value"} <= set(data["positions"][0])


def summary_for(portfolio, monkeypatch):
    from backend.api import portfolio as portfolio_api
    import core.ledger
    monkeypatch.setattr(core.ledger, "active_ledger", lambda directory=None: None)
    monkeypatch.setattr(portfolio_api, "get_service", lambda: service_with(portfolio))
    return asyncio.run(portfolio_api.get_portfolio_summary())


def test_summary_endpoint_does_not_mix_currencies(monkeypatch):
    summary = summary_for(Portfolio(positions=[a_share(), hk_stock(), us_stock()], cash=500.0), monkeypatch)
    assert summary["mixed_currency"] is True
    assert summary["total_assets"] is None and summary["market_value"] is None
    assert summary["total_profit"] is None and summary["profit_pct"] is None
    assert set(summary["totals_by_currency"]) == {"CNY", "HKD", "USD"}
    assert summary["total_positions"] == 3 and summary["winners_count"] == 2 and summary["losers_count"] == 1
    assert summary["top_winner"]["symbol"] == "EXM" and summary["top_loser"]["symbol"] == "00001"


def test_summary_endpoint_single_currency_is_unchanged(monkeypatch):
    summary = summary_for(Portfolio(positions=[a_share()], cash=300.0), monkeypatch)
    assert summary["mixed_currency"] is False and summary["currency"] == "CNY"
    assert (summary["total_assets"], summary["market_value"], summary["total_profit"]) == (1500, 1200, 200)
    assert summary["profit_pct"] == pytest.approx(200 / 1300 * 100)


def test_agent_portfolio_tool_never_reads_the_legacy_portfolio(monkeypatch):
    """The agent's get_portfolio reads only the confirmed ledger (tests/test_agent_context.py), so the
    legacy portfolio's currency handling is checked through the summary endpoint above, not through it."""
    from core.market.runtime import set_market_runtime
    from core.tools import get_portfolio_tool
    from tests.fakes import FakeClock, ScriptedSource, fake_runtime
    import core.ledger
    # Changed in review round 3: the tool reads the ledger read-only (read_confirmed), not through active_ledger.
    monkeypatch.setattr(core.ledger, "read_confirmed", lambda directory=None, *, timeout: None)
    set_market_runtime(fake_runtime(FakeClock("2026-11-17 10:00"), ScriptedSource()))

    async def get_portfolio():
        raise AssertionError("the legacy portfolio must not be read")
    try:
        data = asyncio.run(get_portfolio_tool(SimpleNamespace(get_portfolio=get_portfolio)).function())
    finally:
        set_market_runtime(None)
    assert data["accounts"] == [] and data["source"] == "confirmed_ledger+live_quotes"
    json.dumps(data)
