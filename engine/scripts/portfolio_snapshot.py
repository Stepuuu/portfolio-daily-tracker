#!/usr/bin/env python3
"""Portfolio snapshot engine: fetch prices → calculate metrics → save daily snapshot.

Usage:
    python3 portfolio_snapshot.py [--date YYYY-MM-DD] [--dry-run]
"""

import json, os, sys, argparse, glob, copy
from datetime import datetime, timedelta
import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PORTFOLIO_DIR = os.path.join(os.path.dirname(BASE_DIR), "portfolio")
CONFIG_PATH = os.path.join(PORTFOLIO_DIR, "config.json")


def load_config():
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)


def load_holdings(date_str, config):
    """Load holdings for a date. If missing, copy from previous day."""
    path = os.path.join(PORTFOLIO_DIR, "holdings", f"{date_str}.json")
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)

    # Find most recent previous holdings
    holdings_dir = os.path.join(PORTFOLIO_DIR, "holdings")
    files = sorted(glob.glob(os.path.join(holdings_dir, "*.json")))
    prev_file = None
    for f in reversed(files):
        fname = os.path.basename(f).replace(".json", "")
        if fname < date_str:
            prev_file = f
            break

    if prev_file:
        with open(prev_file, "r") as f:
            data = json.load(f)
        data["date"] = date_str
        data["updated_at"] = datetime.now().isoformat()
        # Save as today's file
        with open(path, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"  从 {os.path.basename(prev_file)} 复制持仓到 {date_str}")
        return data
    else:
        print(f"  ❌ 没有找到任何持仓文件", file=sys.stderr)
        return None


def load_previous_snapshot(date_str):
    """Load the most recent snapshot before given date."""
    snap_dir = os.path.join(PORTFOLIO_DIR, "snapshots")
    files = sorted(glob.glob(os.path.join(snap_dir, "*.json")))
    for f in reversed(files):
        fname = os.path.basename(f).replace(".json", "")
        if fname < date_str:
            with open(f, "r") as fh:
                return json.load(fh)
    return None


def fetch_prices(holdings, config):
    """Fetch current prices for all tickers via Yahoo Finance."""
    proxy = config.get("proxy", "")
    proxies = {"http": proxy, "https": proxy} if proxy else None
    headers = {"User-Agent": "Mozilla/5.0"}
    ticker_map = config.get("ticker_map", {})
    yahoo_base = config.get("yahoo_base", "https://query1.finance.yahoo.com/v8/finance/chart")

    # Collect all unique tickers
    all_tickers = set()
    for group in holdings.get("groups", {}).values():
        for pos in group.get("positions", []):
            all_tickers.add(pos["ticker"])

    prices = {}  # ticker -> price in local currency
    currencies = {}  # ticker -> currency

    for ticker in all_tickers:
        yahoo_sym = ticker_map.get(ticker)
        if not yahoo_sym:
            # Auto-convert: SHA:603259 -> 603259.SS, SHE:002050 -> 002050.SZ, etc.
            exchange, code = ticker.split(":")
            if exchange == "SHA":
                yahoo_sym = f"{code}.SS"
            elif exchange == "SHE":
                yahoo_sym = f"{code}.SZ"
            elif exchange == "HKG":
                yahoo_sym = f"{code}.HK"
            else:
                yahoo_sym = code

        try:
            r = requests.get(
                f"{yahoo_base}/{yahoo_sym}",
                headers=headers, proxies=proxies, timeout=15,
                params={"interval": "1d", "range": "1d"}
            )
            data = r.json()
            meta = data["chart"]["result"][0]["meta"]
            prices[ticker] = meta["regularMarketPrice"]
            currencies[ticker] = meta.get("currency", "CNY")
        except Exception as e:
            print(f"  ⚠️ 获取 {ticker} ({yahoo_sym}) 价格失败: {e}", file=sys.stderr)
            prices[ticker] = 0
            currencies[ticker] = "CNY"

    return prices, currencies


def fetch_fx_rates(config):
    """Fetch HKD/CNY and USD/CNY exchange rates."""
    proxy = config.get("proxy", "")
    proxies = {"http": proxy, "https": proxy} if proxy else None
    headers = {"User-Agent": "Mozilla/5.0"}
    yahoo_base = config.get("yahoo_base", "https://query1.finance.yahoo.com/v8/finance/chart")
    fx_tickers = config.get("fx_tickers", {"HKD_CNY": "HKDCNY=X", "USD_CNY": "USDCNY=X"})

    rates = {"CNY": 1.0}  # default
    for key, yahoo_sym in fx_tickers.items():
        try:
            r = requests.get(
                f"{yahoo_base}/{yahoo_sym}",
                headers=headers, proxies=proxies, timeout=15,
                params={"interval": "1d", "range": "1d"}
            )
            data = r.json()
            rate = data["chart"]["result"][0]["meta"]["regularMarketPrice"]
            # key = "HKD_CNY" -> currency = "HKD"
            currency = key.split("_")[0]
            rates[currency] = rate
        except Exception as e:
            print(f"  ⚠️ 获取汇率 {key} 失败: {e}", file=sys.stderr)
            if "HKD" in key:
                rates["HKD"] = 0.88
            elif "USD" in key:
                rates["USD"] = 6.90

    return rates


def calculate_snapshot(holdings, prices, currencies, fx_rates, prev_snapshot, all_snapshots, history_values=None):
    """Calculate full portfolio snapshot with all metrics."""
    date_str = holdings["date"]
    currency_map_cfg = {
        "SHA": "CNY", "SHE": "CNY", "HKG": "HKD", "NASDAQ": "USD", "NYSE": "USD"
    }

    snapshot = {
        "date": date_str,
        "generated_at": datetime.now().isoformat(),
        "fx_rates": fx_rates,
        "groups": {},
        "summary": {}
    }

    grand_total_value = 0
    grand_total_cost = 0

    for group_name, group_data in holdings.get("groups", {}).items():
        positions_out = []
        group_positions_value = 0

        for pos in group_data.get("positions", []):
            ticker = pos["ticker"]
            exchange = ticker.split(":")[0]
            currency = currencies.get(ticker, currency_map_cfg.get(exchange, "CNY"))
            fx = fx_rates.get(currency, 1.0)
            current_price = prices.get(ticker, 0)

            market_value_local = current_price * pos["quantity"]
            market_value_cny = market_value_local * fx
            cost_value_cny = pos["cost_price"] * pos["quantity"] * fx
            profit_cny = market_value_cny - cost_value_cny
            profit_pct = (profit_cny / cost_value_cny * 100) if cost_value_cny != 0 else 0

            positions_out.append({
                "name": pos["name"],
                "ticker": ticker,
                "quantity": pos["quantity"],
                "cost_price": pos["cost_price"],
                "current_price": current_price,
                "currency": currency,
                "fx_rate": fx,
                "market_value_cny": round(market_value_cny, 2),
                "cost_value_cny": round(cost_value_cny, 2),
                "profit_cny": round(profit_cny, 2),
                "profit_pct": round(profit_pct, 2),
                "weight_in_group": 0  # calculated later
            })
            group_positions_value += market_value_cny

        fund = group_data.get("fund", 0)
        cash = group_data.get("cash", 0)
        group_total = group_positions_value + fund + cash
        cost_basis = group_data.get("cost_basis", 0)
        group_profit = group_total - cost_basis
        group_return_pct = (group_profit / cost_basis * 100) if cost_basis != 0 else 0

        # Calculate weights
        for p in positions_out:
            p["weight_in_group"] = round(p["market_value_cny"] / group_total * 100, 1) if group_total != 0 else 0

        snapshot["groups"][group_name] = {
            "cost_basis": cost_basis,
            "positions": positions_out,
            "fund": fund,
            "cash": cash,
            "positions_value": round(group_positions_value, 2),
            "total_value": round(group_total, 2),
            "profit": round(group_profit, 2),
            "return_pct": round(group_return_pct, 2)
        }

        grand_total_value += group_total
        grand_total_cost += cost_basis

    # Summary
    grand_profit = grand_total_value - grand_total_cost
    grand_return_pct = (grand_profit / grand_total_cost * 100) if grand_total_cost != 0 else 0

    # Daily change (vs previous snapshot)
    prev_value = prev_snapshot["summary"]["total_value"] if prev_snapshot else grand_total_value
    daily_change = grand_total_value - prev_value
    daily_change_pct = (daily_change / prev_value * 100) if prev_value != 0 else 0

    # Max drawdown from full history (history.csv + snapshot files)
    all_values = []
    if history_values:
        all_values = [v for _, v in history_values if _ < date_str]
    # Also include snapshot file values as fallback
    for s in all_snapshots:
        if s.get("date", "") < date_str and "summary" in s:
            v = s["summary"]["total_value"]
            if v not in all_values:
                all_values.append(v)
    all_values.append(grand_total_value)
    max_value = max(all_values) if all_values else grand_total_value
    drawdown = (grand_total_value - max_value) / max_value * 100 if max_value != 0 else 0

    # Monthly stats — always use last day of previous month from history for accuracy
    month_first_day = date_str[:8] + "01"  # e.g. "2026-03-01"
    prev_month_values = [(d, v) for d, v in (history_values or []) if d < month_first_day]
    if prev_month_values:
        month_start_value = prev_month_values[-1][1]  # last trading day of previous month
    else:
        # Fallback: use first snapshot of the month's prev_total_value, or prev_value
        month_snapshots = [s for s in all_snapshots if s["date"].startswith(date_str[:7])]
        if month_snapshots:
            month_start_value = month_snapshots[0]["summary"].get("prev_total_value", grand_total_value)
        else:
            month_start_value = prev_value
    month_change = grand_total_value - month_start_value
    month_return_pct = (month_change / month_start_value * 100) if month_start_value != 0 else 0

    # Quantitative metrics from history
    daily_returns = []
    hist_vals = [(d, v) for d, v in (history_values or []) if d <= date_str]
    for i in range(1, len(hist_vals)):
        prev_v = hist_vals[i - 1][1]
        curr_v = hist_vals[i][1]
        if prev_v > 0:
            daily_returns.append((curr_v - prev_v) / prev_v)

    # Sharpe ratio (annualized, risk-free rate ~2%)
    import math
    sharpe_ratio = 0.0
    volatility_annual = 0.0
    win_rate = 0.0
    avg_win = 0.0
    avg_loss = 0.0
    profit_loss_ratio = 0.0
    if len(daily_returns) >= 5:
        mean_r = sum(daily_returns) / len(daily_returns)
        std_r = math.sqrt(sum((r - mean_r) ** 2 for r in daily_returns) / len(daily_returns))
        risk_free_daily = 0.02 / 252
        sharpe_ratio = round(((mean_r - risk_free_daily) / std_r * math.sqrt(252)) if std_r > 0 else 0, 2)
        volatility_annual = round(std_r * math.sqrt(252) * 100, 2)
        wins = [r for r in daily_returns if r > 0]
        losses = [r for r in daily_returns if r < 0]
        win_rate = round(len(wins) / len(daily_returns) * 100, 1)
        avg_win = round(sum(wins) / len(wins) * 100, 3) if wins else 0
        avg_loss = round(sum(losses) / len(losses) * 100, 3) if losses else 0
        profit_loss_ratio = round(abs(avg_win / avg_loss), 2) if avg_loss != 0 else 0

    snapshot["summary"] = {
        "total_value": round(grand_total_value, 2),
        "total_cost": grand_total_cost,
        "total_profit": round(grand_profit, 2),
        "total_return_pct": round(grand_return_pct, 2),
        "prev_total_value": round(prev_value, 2),
        "daily_change": round(daily_change, 2),
        "daily_change_pct": round(daily_change_pct, 2),
        "max_value": round(max_value, 2),
        "max_drawdown_pct": round(drawdown, 2),
        "month_start_value": round(month_start_value, 2),
        "month_change": round(month_change, 2),
        "month_return_pct": round(month_return_pct, 2),
        # Quantitative metrics
        "sharpe_ratio": sharpe_ratio,
        "volatility_annual": volatility_annual,
        "win_rate": win_rate,
        "avg_win_pct": avg_win,
        "avg_loss_pct": avg_loss,
        "profit_loss_ratio": profit_loss_ratio,
        "trading_days": len(daily_returns),
    }

    return snapshot


def save_snapshot(snapshot):
    """Save snapshot to file."""
    path = os.path.join(PORTFOLIO_DIR, "snapshots", f"{snapshot['date']}.json")
    with open(path, "w") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)
    print(f"  ✅ 快照已保存: {path}")
    return path


def sync_to_qr(snapshot, config):
    """Update QR trading-assistant portfolio.json for backward compatibility."""
    qr_path = config.get("qr_portfolio_path", "")
    if not qr_path or not os.path.exists(os.path.dirname(qr_path)):
        return

    # Merge positions across groups
    merged = {}
    for group_name, group_data in snapshot["groups"].items():
        for pos in group_data["positions"]:
            symbol = pos["ticker"].split(":")[1]
            exchange = pos["ticker"].split(":")[0]
            market_map = {"SHA": "a_share", "SHE": "a_share", "HKG": "hk_stock", "NASDAQ": "us_stock", "NYSE": "us_stock"}
            if symbol in merged:
                merged[symbol]["quantity"] += pos["quantity"]
            else:
                merged[symbol] = {
                    "symbol": symbol,
                    "name": pos["name"],
                    "market": market_map.get(exchange, "a_share"),
                    "quantity": pos["quantity"],
                    "available_qty": pos["quantity"],
                    "cost_price": pos["cost_price"],
                    "current_price": pos.get("current_price", 0),
                    "side": "long"
                }

    total_cash = sum(g.get("cash", 0) + g.get("fund", 0) for g in snapshot["groups"].values())

    qr_data = {
        "positions": list(merged.values()),
        "cash": total_cash,
        "updated_at": snapshot["generated_at"]
    }
    with open(qr_path, "w") as f:
        json.dump(qr_data, f, ensure_ascii=False, indent=2)
    print(f"  ✅ QR portfolio.json 已同步")


def load_all_snapshots():
    """Load all historical snapshots for drawdown/monthly calc."""
    snap_dir = os.path.join(PORTFOLIO_DIR, "snapshots")
    snapshots = []
    for f in sorted(glob.glob(os.path.join(snap_dir, "*.json"))):
        try:
            with open(f, "r") as fh:
                snapshots.append(json.load(fh))
        except:
            pass
    return snapshots


def load_all_history_values():
    """Load all historical total_value from history.csv for accurate peak/drawdown."""
    csv_path = os.path.join(PORTFOLIO_DIR, "history.csv")
    values = []  # list of (date, total_value)
    if os.path.exists(csv_path):
        with open(csv_path, "r") as f:
            header = f.readline().strip().split(",")
            date_idx = header.index("date") if "date" in header else 0
            val_idx = header.index("total_value") if "total_value" in header else 1
            for line in f:
                parts = line.strip().split(",")
                if len(parts) > val_idx:
                    try:
                        values.append((parts[date_idx], float(parts[val_idx])))
                    except ValueError:
                        pass
    return values


def main():
    parser = argparse.ArgumentParser(description="Generate portfolio daily snapshot")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"), help="Date (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true", help="Print without saving")
    args = parser.parse_args()

    date_str = args.date
    print(f"📊 生成 {date_str} 资产快照...")

    config = load_config()
    holdings = load_holdings(date_str, config)
    if not holdings:
        sys.exit(1)

    print("  获取实时价格...")
    prices, currencies = fetch_prices(holdings, config)
    fx_rates = fetch_fx_rates(config)
    print(f"  汇率: USD/CNY={fx_rates.get('USD', '?')}, HKD/CNY={fx_rates.get('HKD', '?')}")

    prev_snapshot = load_previous_snapshot(date_str)
    all_snapshots = load_all_snapshots()
    history_values = load_all_history_values()

    snapshot = calculate_snapshot(holdings, prices, currencies, fx_rates, prev_snapshot, all_snapshots, history_values)

    if args.dry_run:
        print(json.dumps(snapshot, ensure_ascii=False, indent=2))
    else:
        save_snapshot(snapshot)
        sync_to_qr(snapshot, config)

        # Append to history CSV
        history_path = os.path.join(PORTFOLIO_DIR, "history.csv")
        write_header = not os.path.exists(history_path)
        with open(history_path, "a") as f:
            if write_header:
                f.write("date,total_value,total_cost,total_profit,return_pct,daily_change,daily_change_pct,max_drawdown_pct\n")
            s = snapshot["summary"]
            f.write(f"{date_str},{s['total_value']},{s['total_cost']},{s['total_profit']},{s['total_return_pct']},{s['daily_change']},{s['daily_change_pct']},{s['max_drawdown_pct']}\n")

    # Print summary
    s = snapshot["summary"]
    print(f"\n{'='*50}")
    print(f"  总资产: ¥{s['total_value']/10000:.2f}万  成本: ¥{s['total_cost']/10000:.1f}万")
    print(f"  总盈亏: ¥{s['total_profit']/10000:.2f}万 ({s['total_return_pct']:+.2f}%)")
    print(f"  日变动: ¥{s['daily_change']/10000:.2f}万 ({s['daily_change_pct']:+.2f}%)")
    print(f"  月收益: {s['month_return_pct']:+.2f}%  最大回撤: {s['max_drawdown_pct']:.2f}%")
    for gname, gdata in snapshot["groups"].items():
        print(f"  [{gname}] ¥{gdata['total_value']/10000:.2f}万 ({gdata['return_pct']:+.2f}%)")
    print(f"{'='*50}")
    return snapshot


if __name__ == "__main__":
    main()
