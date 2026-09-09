#!/usr/bin/env python3
"""Generate fictional portfolio history offline, in a NEW directory only."""
import argparse
from datetime import date, timedelta
from pathlib import Path
from portfolio_accounting import atomic_json_dump
from portfolio_snapshot import calculate_snapshot


def create_demo(destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "holdings").mkdir()
    (destination / "snapshots").mkdir()
    atomic_json_dump({"groups": {"长期账户": {}, "研究账户": {}}}, destination / "config.json")
    snapshots = []
    for index in range(28):
        day = date(2026, 8, 3) + timedelta(days=index)
        if day.weekday() >= 5:
            continue
        holdings = {"date": day.isoformat(), "groups": {
            "长期账户": {"cost_basis": 81000, "cash": 20000, "fund": 0,
                "cash_balances": {"CNY": 20000, "USD": 1000, "HKD": 5000},
                "positions": [{"name": "示例宽基", "ticker": "SHA:DEMO", "quantity": 1000, "cost_price": 50}]},
            "研究账户": {"cost_basis": 29000, "cash": 10000, "fund": 5000,
                "positions": [{"name": "示例研究标的", "ticker": "NASDAQ:DEMO", "quantity": 20, "cost_price": 100}]},
        }}
        prices = {"SHA:DEMO": 50 + index * .4 - (6 if 9 <= index <= 15 else 0),
                  "NASDAQ:DEMO": 100 + index * .7}
        rates = {"CNY": 1, "USD": 7 + index * .002, "HKD": .9}
        snapshot = calculate_snapshot(holdings, prices, {"NASDAQ:DEMO": "USD"}, rates,
                                      snapshots[-1] if snapshots else None, snapshots)
        snapshot["demo"] = True
        snapshot["generated_at"] = day.isoformat() + "T16:00:00+08:00"
        atomic_json_dump(holdings, destination / "holdings" / f"{day}.json")
        atomic_json_dump(snapshot, destination / "snapshots" / f"{day}.json")
        snapshots.append(snapshot)
    return len(snapshots)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(f"Created {create_demo(args.output)} fictional snapshots in {args.output}")
