"""CSV export: a text cell is never a spreadsheet formula; numbers stay numbers."""
import csv
import io

from backtesting.export import PARTS, to_csv

DANGEROUS = ("=", "+", "-", "@", "\t", "\r")


def result_with_hostile_text():
    return {
        "run_id": "r1", "created_at": "2024-03-01T10:00:00", "headline_zh": "-6.4% 的说明",
        "facts": {"name": "=1+1", "first_date": "2024-01-02", "last_date": "2024-01-31"},
        "manifest": {
            "spec": {"ticker": "@SUM(1)", "market": "CN", "currency": "CNY", "capital": 100000.0,
                     "strategy": {"kind": "strategy", "id": "custom:x", "name": "+SUM(1,2)",
                                  "params": {"note": "=cmd"}},
                     "period": {"preset": "custom", "start": "2024-01-02", "end": "2024-01-31"}},
            "coverage": [{"ticker": "-HYPERLINK(1)", "adjust": "qfq", "source": "\t=x", "rows": 20}],
            "data_sha256": "ab" * 32,
        },
        "fills": [{"date": "2024-01-03", "symbol": "=HYPERLINK(\"http://x\")", "direction": "sell",
                   "quantity": 100, "price": 10.5, "commission": 5.0, "pnl": -12.5, "reason": "\r=y",
                   "fees": {"stamp_duty": 0.5}}],
        "round_trips": [{"symbol": "@x", "entry_date": "2024-01-02", "exit_date": "2024-01-03",
                         "quantity": 100, "pnl": -12.5, "exit_reason_zh": "-止损", "end_mark": False}],
        "unfilled": [{"signal_date": "2024-01-04", "symbol": "+x", "direction": "buy", "reason_zh": "=资金"}],
        "equity": [{"date": "2024-01-02", "total_value": 99987.5, "net_value": 0.999875, "drawdown": -0.000125}],
        "benchmark": {"same_stock": {"curve": [{"date": "2024-01-02", "value": 1.0}]}, "index": None},
    }


def cells(text):
    return [cell for row in csv.reader(io.StringIO(text.lstrip("﻿"))) for cell in row]


def is_number(cell):
    try:
        float(cell)
        return True
    except ValueError:
        return False


def test_text_cells_that_start_like_a_formula_are_quoted_with_an_apostrophe():
    result = result_with_hostile_text()
    for text in [to_csv(result)] + [to_csv(result, part) for part in PARTS]:
        for cell in cells(text):
            assert not cell.startswith(DANGEROUS) or is_number(cell), repr(cell)
    everything = cells(to_csv(result))
    # A CR is written as LF so the writer quotes the cell (a bare CR would split the row).
    for expected in ("'=1+1", "'+SUM(1,2)", "'@SUM(1)", "'\t=x", "'\n=y",
                     "'=HYPERLINK(\"http://x\")", "'@x", "'-止损", "'+x", "'=资金", "'-6.4% 的说明"):
        assert expected in everything, expected


def test_numbers_stay_numeric():
    everything = cells(to_csv(result_with_hostile_text()))
    assert "-12.5" in everything and "-0.000125" in everything and "99987.5" in everything
