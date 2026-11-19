"""Export a RunResult as CSV or JSON.

CSV without ``part``: one UTF-8 file (with a BOM, so spreadsheet programs read
the Chinese text) holding five tables one after another, each under a title
row in brackets and separated by an empty row:

    [manifest]     key,value          settings, data hashes and versions
    [fills]        FILL_COLUMNS
    [round_trips]  TRIP_COLUMNS
    [unfilled]     UNFILLED_COLUMNS
    [equity]       EQUITY_COLUMNS     daily, with both benchmarks normalised to 1

With ``part`` (one of the names above) only that table, with no title row.
JSON without ``part`` is the whole RunResult; with ``part`` that piece. A run saved
by v3.2.1 or earlier exports as its legacy translation (history.legacy_result): the
manifest table then has legacy_saved_by, legacy_unavailable and the saved metrics.

Spreadsheet formulas: a spreadsheet program runs a cell that starts with =, +, -, @,
a tab or a carriage return as a formula, and names, tickers, strategy names and
reasons can come from the user's ledger or custom strategies. Every text cell that
starts with one of those characters, title rows included, is written with a leading
apostrophe ('=1+1); numbers are written as numbers (-12.5 stays numeric). A line
break inside a text cell is written as LF, so the cell is quoted and stays one cell.
JSON is not changed.
"""
from __future__ import annotations

import csv
import io
import json
import numbers
from typing import Any, Dict, List, Optional

FILL_COLUMNS = ("date", "symbol", "direction", "quantity", "price", "commission", "pnl", "reason", "fees",
                "end_mark", "blocked", "blocked_quantity", "blocked_zh")
TRIP_COLUMNS = ("symbol", "entry_date", "exit_date", "quantity", "entry_price", "exit_price", "pnl",
                "return_pct", "holding_days", "fills", "exit_reason", "exit_reason_zh", "end_mark",
                "blocked", "blocked_quantity", "blocked_zh")
UNFILLED_COLUMNS = ("signal_date", "symbol", "direction", "order_type", "quantity", "reason", "reason_zh",
                    "outcome")
EQUITY_COLUMNS = ("date", "total_value", "cash", "market_value", "net_value", "drawdown",
                  "same_stock", "index")
PARTS = ("manifest", "fills", "round_trips", "unfilled", "equity")


FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _text(text: str) -> str:
    """A text cell a spreadsheet will not run as a formula. Line breaks become LF: the
    writer quotes a cell holding its line terminator, but not a bare CR."""
    if text.startswith(FORMULA_PREFIXES):
        text = "'" + text
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _cell(value) -> Any:
    if isinstance(value, (dict, list, tuple)):
        return _text(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))
    if value is None:
        return ""
    if isinstance(value, numbers.Real):              # numbers (numpy ones too) stay numeric
        return value
    return _text(str(value))


def manifest_rows(result: Dict[str, Any]) -> List[List[Any]]:
    manifest = result.get("manifest") or {}
    spec = manifest.get("spec") or {}
    facts = result.get("facts") or {}
    period = spec.get("period") or {}
    strategy = spec.get("strategy") or {}
    rows = [
        ["run_id", result.get("run_id")],
        ["created_at", result.get("created_at")],
        ["ticker", spec.get("ticker")],
        ["name", facts.get("name")],
        ["market", spec.get("market")],
        ["currency", spec.get("currency")],
        ["strategy_kind", strategy.get("kind")],
        ["strategy_id", strategy.get("id")],
        ["strategy_name", strategy.get("name")],
        ["params", strategy.get("params")],
        ["period", period.get("preset")],
        ["start", period.get("start")],
        ["end", period.get("end")],
        ["first_date", facts.get("first_date")],
        ["last_date", facts.get("last_date")],
        ["capital", spec.get("capital")],
        ["adjust", spec.get("adjust")],
        ["warmup", spec.get("warmup")],
        ["commission_buy", spec.get("commission_buy")],
        ["commission_sell", spec.get("commission_sell")],
        ["min_commission", spec.get("min_commission")],
        ["slippage_pct", spec.get("slippage_pct")],
        ["lot_size", spec.get("lot_size")],
        ["benchmark", spec.get("benchmark")],
        ["engine_version", manifest.get("engine_version")],
        ["fee_schedule_version", manifest.get("fee_schedule_version")],
        ["data_sha256", manifest.get("data_sha256")],
        ["data_hash_version", manifest.get("data_hash_version")],
        ["strategy_dependencies_sha256", (spec.get("strategy_definition") or {}).get("dependencies_sha256")],
        ["legacy_fees", spec.get("legacy_fees")],
        ["headline_zh", result.get("headline_zh")],
    ]
    legacy = result.get("legacy")
    if isinstance(legacy, dict):
        # A run saved by v3.2.1 or earlier (history.legacy_result): what it does not have.
        rows.append(["legacy_saved_by", legacy.get("saved_by")])
        rows.append(["legacy_unavailable", [item.get("key") for item in legacy.get("unavailable") or []]])
        rows += [[f"legacy_saved_metrics.{key}", value] for key, value in (legacy.get("saved_metrics") or {}).items()]
    for coverage in manifest.get("coverage") or []:
        prefix = f"coverage.{coverage.get('ticker')}"
        for key in ("adjust", "source", "first", "last", "rows", "complete", "offline", "fetched_at", "sha256",
                    "features_sha256"):
            rows.append([f"{prefix}.{key}", coverage.get(key)])
    return rows


def equity_rows(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    benchmark = result.get("benchmark") or {}
    same = {p["date"]: p["value"] for p in ((benchmark.get("same_stock") or {}).get("curve") or [])}
    index = {p["date"]: p["value"] for p in ((benchmark.get("index") or {}).get("curve") or [])}
    return [{**row, "same_stock": same.get(row["date"]), "index": index.get(row["date"])}
            for row in result.get("equity") or []]


def _table(rows: List[Dict[str, Any]], columns) -> List[List[Any]]:
    return [[_cell(column) for column in columns]] + [[_cell(row.get(column)) for column in columns] for row in rows]


def tables(result: Dict[str, Any]) -> Dict[str, List[List[Any]]]:
    return {
        "manifest": [["key", "value"]] + [[_cell(key), _cell(value)] for key, value in manifest_rows(result)],
        "fills": _table(result.get("fills") or [], FILL_COLUMNS),
        "round_trips": _table(result.get("round_trips") or [], TRIP_COLUMNS),
        "unfilled": _table(result.get("unfilled") or [], UNFILLED_COLUMNS),
        "equity": _table(equity_rows(result), EQUITY_COLUMNS),
    }


def to_csv(result: Dict[str, Any], part: Optional[str] = None) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    all_tables = tables(result)
    if part is not None:
        writer.writerows(all_tables[part])
    else:
        for index, name in enumerate(PARTS):
            if index:
                writer.writerow([])
            writer.writerow([_cell(f"[{name}]")])
            writer.writerows(all_tables[name])
    return "﻿" + out.getvalue()


def to_json(result: Dict[str, Any], part: Optional[str] = None) -> str:
    if part == "equity":
        payload: Any = equity_rows(result)
    elif part == "manifest":
        payload = result.get("manifest")
    elif part is not None:
        payload = result.get(part)
    else:
        payload = result
    return json.dumps(payload, ensure_ascii=False, indent=1)
