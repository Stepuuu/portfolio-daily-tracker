"""Release gate 4 (cr_g4d PART 1), the run service, caveats and headline: offline,
with synthetic bars.

* service.py:445 - a method with no indicator needs no warm-up.
* analyzer/caveats.py:129 - no few_trades (or in_sample) caveat on 一直持有.
* broker/fees.py:127 - the HK ETF stamp-duty note only where it can apply.
* service.py:796 - the demo never opens a bar cache, an explicit index included.
* summary.py:227 - "no drawdown" only when the drawdown is exactly zero.
"""
import sys

import pytest

import backtesting.presets as real_presets
from backtesting.analyzer.caveats import fee_limit_caveats
from backtesting.broker.simulated import BrokerConfig
from backtesting.service import BacktestService, TaskRegistry, parse_spec
from backtesting.summary import headline
from tests.backtesting.test_service import (  # noqa: F401  (svc, frames and loader are fixtures)
    TODAY, FakeLoader, codes_of, frames, loader, run, svc, wave)
from tests.test_backtest_demo_isolation import client, demo, wait_run  # noqa: F401  (fixtures)


def service_on(frames_by_ticker, tmp_path, monkeypatch):
    monkeypatch.setenv("TRACKER_BACKTEST_DIR", str(tmp_path / "bt"))
    monkeypatch.delenv("TRACKER_DEMO_MODE", raising=False)
    monkeypatch.setitem(sys.modules, "backtesting.presets", real_presets)
    fake = FakeLoader(frames_by_ticker)
    return BacktestService(strategy_resolver=lambda name: pytest.fail(f"no strategy {name}"),
                           loader_factory=lambda plan: fake, today=lambda: TODAY, registry=TaskRegistry())


def start(service, **spec):
    task = service.start(service.plan(parse_spec({"ticker": "600519", **spec})))
    assert task.done_event.wait(30)
    assert task.status == "completed", task.error
    return task.result


# ------------------------------------------------------------------ #
#  service.py:445 - 一直持有 needs no warm-up
# ------------------------------------------------------------------ #

def test_buy_and_hold_on_a_recent_listing_runs_on_every_bar(tmp_path, monkeypatch):
    listed = wave(start="2026-09-21", end="2026-11-16")              # about 40 sessions
    service = service_on({"SHA:600519": listed, "IDX:000300": wave(base=3800.0, seed=2)}, tmp_path, monkeypatch)
    result = start(service, preset="buy_and_hold", period="1y")
    facts = result["facts"]
    assert facts["warmup_bars"] == 0 and facts["warmup_auto"] is True
    assert facts["first_date"] == "2026-09-21"
    assert facts["total_return"] is not None and facts["total_return"] != 0
    assert facts["same_stock_return"] is not None
    assert "warmup_short" not in codes_of(result)


def test_a_method_with_an_indicator_keeps_its_automatic_warmup(tmp_path, monkeypatch):
    service = service_on({"SHA:600519": wave(), "IDX:000300": wave(base=3800.0, seed=2)}, tmp_path, monkeypatch)
    result = start(service, preset="ma_trend", period="1y")
    assert result["facts"]["warmup_bars"] == 60


# ------------------------------------------------------------------ #
#  analyzer/caveats.py:129 - caveats 一直持有 cannot act on
# ------------------------------------------------------------------ #

def test_buy_and_hold_gets_no_few_trades_or_in_sample_caveat(tmp_path, monkeypatch):
    service = service_on({"SHA:600519": wave(), "IDX:000300": wave(base=3800.0, seed=2)}, tmp_path, monkeypatch)
    codes = codes_of(start(service, preset="buy_and_hold", period="1y"))
    assert "few_trades" not in codes and "in_sample" not in codes
    # A method with parameters still gets both (it trades a few times on this history).
    trend = codes_of(start(service, preset="ma_trend", period="1y"))
    assert "in_sample" in trend


def test_a_python_strategy_with_few_trades_still_gets_the_caveat(svc):
    codes = codes_of(run(svc, preset=None, strategy="buy_once").result)
    assert codes["few_trades"]["level"] == "warning" and "in_sample" in codes


# ------------------------------------------------------------------ #
#  broker/fees.py:127 - HK ETF stamp-duty relief
# ------------------------------------------------------------------ #

@pytest.mark.parametrize("symbol", ["HKG:00700", "0700.HK", "HKG:00005"])
def test_an_hk_stock_gets_no_etf_stamp_duty_warning(symbol):
    found = fee_limit_caveats([symbol], BrokerConfig())
    assert len(found) == 1 and found[0]["code"] == "fee_not_modelled"
    assert found[0]["level"] == "info" and "ETF" not in found[0]["text"] and "印花税" not in found[0]["text"]
    assert "中央结算" in found[0]["text"]


@pytest.mark.parametrize("symbol", ["HKG:02800", "2828.HK", "HKG:03033", "HKG:07226", "HKG:82800", "HKG:09834"])
def test_an_hk_etf_code_gets_the_stamp_duty_warning(symbol):
    found = fee_limit_caveats([symbol], BrokerConfig())
    assert len(found) == 1 and found[0]["level"] == "warning"
    assert "ETF" in found[0]["text"] and "印花税" in found[0]["text"]


def test_an_hk_run_on_tencent_has_only_the_info_note(svc):
    codes = codes_of(run(svc, ticker="0700.HK").result)
    assert codes["fee_not_modelled"]["level"] == "info" and "ETF" not in codes["fee_not_modelled"]["text"]


# ------------------------------------------------------------------ #
#  service.py:796 - the demo with an explicit index
# ------------------------------------------------------------------ #

def test_a_demo_run_with_an_explicit_index_writes_nothing(client, demo):
    started = client.post("/api/backtest/runs", json={"ticker": "SHA:DEMO", "strategy": "sma_cross",
                                                      "benchmark": "IDX:000300"})
    assert started.status_code == 202, started.text
    done = wait_run(client, started.json()["run_id"])
    assert done["status"] == "completed", done["error"]
    result = done["result"]
    assert result["benchmark"]["index"] is None
    assert "演示模式没有指数行情" in codes_of(result)["index_unavailable"]["text"]
    assert not (demo["portfolio"] / "backtest_demo").exists()
    assert not (demo["private"] / "backtesting.db").exists()


# ------------------------------------------------------------------ #
#  summary.py:227 - a small drawdown is still a drawdown
# ------------------------------------------------------------------ #

BASE = {"first_date": "2024-01-02", "last_date": "2024-12-31", "total_return": 0.05}


@pytest.mark.parametrize("drawdown,shown", [(-0.000248, "−0.02%"), (-0.0000041, "−0.0004%"),
                                            (-0.0004, "−0.04%"), (-0.0123, "−1.2%")])
def test_a_measured_drawdown_is_shown_however_small(drawdown, shown):
    text, _ = headline({**BASE, "max_drawdown": drawdown})
    assert "没有回撤" not in text and f"最大回撤 {shown}" in text


def test_only_a_zero_drawdown_is_no_drawdown():
    text, _ = headline({**BASE, "max_drawdown": 0.0})
    assert "期间净值没有回撤" in text
    tiny, _ = headline({**BASE, "max_drawdown": -1e-12})
    assert "没有回撤" not in tiny and "不到 0.0001%" in tiny
