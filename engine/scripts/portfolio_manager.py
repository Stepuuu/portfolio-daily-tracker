#!/usr/bin/env python3
"""Portfolio manager: update daily holdings from CLI or agent.

Usage:
    # 查看当天持仓
    python3 portfolio_manager.py show [--date YYYY-MM-DD]

    # 更新持仓数量(正数加仓,负数减仓)
    python3 portfolio_manager.py update --group 进攻 --ticker SHA:603259 --quantity -500
    python3 portfolio_manager.py update --group 进攻 --name 药明康德 --quantity -500

    # 添加新持仓
    python3 portfolio_manager.py add --group 进攻 --name 新股票 --ticker SHA:600000 --quantity 1000 --cost-price 15.5

    # 删除持仓
    python3 portfolio_manager.py remove --group 进攻 --ticker SHA:600000

    # 更新基金/现金
    python3 portfolio_manager.py set-fund --group 进攻 --value 160000
    python3 portfolio_manager.py set-cash --group 进攻 --value -500000

    # 更新成本基数
    python3 portfolio_manager.py set-cost --group 进攻 --value 600000
"""

import json, os, sys, argparse, glob, copy
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PORTFOLIO_DIR = os.path.join(os.path.dirname(BASE_DIR), "portfolio")


def get_holdings_path(date_str):
    return os.path.join(PORTFOLIO_DIR, "holdings", f"{date_str}.json")


def load_or_create_today(date_str):
    """Load today's holdings, copying from previous day if needed."""
    path = get_holdings_path(date_str)
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)

    # Find previous
    holdings_dir = os.path.join(PORTFOLIO_DIR, "holdings")
    files = sorted(glob.glob(os.path.join(holdings_dir, "*.json")))
    for f in reversed(files):
        fname = os.path.basename(f).replace(".json", "")
        if fname < date_str:
            with open(f, "r") as fh:
                data = json.load(fh)
            data["date"] = date_str
            data["updated_at"] = datetime.now().isoformat()
            save_holdings(data, date_str)
            return data

    print(f"❌ 没有找到任何历史持仓文件。请先创建初始持仓。", file=sys.stderr)
    sys.exit(1)


def save_holdings(data, date_str):
    data["date"] = date_str
    data["updated_at"] = datetime.now().isoformat()
    path = get_holdings_path(date_str)
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def find_position(group_data, ticker=None, name=None):
    """Find position by ticker or name."""
    for i, pos in enumerate(group_data.get("positions", [])):
        if ticker and pos["ticker"] == ticker:
            return i, pos
        if name and pos["name"] == name:
            return i, pos
    return -1, None


def cmd_show(args):
    data = load_or_create_today(args.date)
    print(f"\n📊 持仓 {args.date}")
    print(f"{'='*60}")
    for gname, gdata in data.get("groups", {}).items():
        cost = gdata.get("cost_basis", 0)
        print(f"\n【{gname}】成本: ¥{cost/10000:.1f}万")
        print(f"{'  名称':<12s} {'代码':<16s} {'数量':>8s} {'成本价':>10s}")
        print(f"  {'-'*52}")
        for pos in gdata.get("positions", []):
            print(f"  {pos['name']:<10s} {pos['ticker']:<16s} {pos['quantity']:>8d} {pos['cost_price']:>10.3f}")
        fund = gdata.get("fund", 0)
        cash = gdata.get("cash", 0)
        if fund:
            print(f"  {'基金':<10s} {'':16s} {'':>8s} {fund/10000:>9.2f}万")
        print(f"  {'现金':<10s} {'':16s} {'':>8s} {cash/10000:>9.2f}万")
    print(f"\n更新时间: {data.get('updated_at', '?')}")


def cmd_update(args):
    data = load_or_create_today(args.date)
    group_data = data["groups"].get(args.group)
    if not group_data:
        print(f"❌ 组 '{args.group}' 不存在。可选: {list(data['groups'].keys())}", file=sys.stderr)
        sys.exit(1)

    idx, pos = find_position(group_data, ticker=args.ticker, name=args.name)
    if idx < 0:
        search = args.ticker or args.name
        print(f"❌ 在 {args.group} 中未找到 '{search}'", file=sys.stderr)
        sys.exit(1)

    old_qty = pos["quantity"]
    new_qty = old_qty + args.quantity
    if new_qty < 0:
        print(f"⚠️ 数量不能为负 (现有{old_qty}, 变动{args.quantity})", file=sys.stderr)
        sys.exit(1)

    if args.cost_price is not None:
        # 更新成本价(加权平均)
        if args.quantity > 0:
            total_cost = old_qty * pos["cost_price"] + args.quantity * args.cost_price
            pos["cost_price"] = round(total_cost / new_qty, 4) if new_qty > 0 else 0
        else:
            pos["cost_price"] = args.cost_price

    if new_qty == 0:
        group_data["positions"].pop(idx)
        print(f"🗑️ {pos['name']} 已清仓")
    else:
        pos["quantity"] = new_qty
        print(f"✅ {pos['name']}: {old_qty} → {new_qty} ({args.quantity:+d})")

    path = save_holdings(data, args.date)
    print(f"   保存到: {path}")


def cmd_add(args):
    data = load_or_create_today(args.date)
    group_data = data["groups"].get(args.group)
    if not group_data:
        print(f"❌ 组 '{args.group}' 不存在", file=sys.stderr)
        sys.exit(1)

    idx, existing = find_position(group_data, ticker=args.ticker)
    if existing:
        print(f"⚠️ {args.ticker} 已存在于 {args.group}，请用 update 命令修改", file=sys.stderr)
        sys.exit(1)

    new_pos = {
        "name": args.name,
        "ticker": args.ticker,
        "quantity": args.quantity,
        "cost_price": args.cost_price
    }
    group_data["positions"].append(new_pos)
    path = save_holdings(data, args.date)
    print(f"✅ 新增 {args.name} ({args.ticker}) {args.quantity}股 @{args.cost_price}")
    print(f"   保存到: {path}")


def cmd_remove(args):
    data = load_or_create_today(args.date)
    group_data = data["groups"].get(args.group)
    if not group_data:
        print(f"❌ 组 '{args.group}' 不存在", file=sys.stderr)
        sys.exit(1)

    idx, pos = find_position(group_data, ticker=args.ticker, name=args.name)
    if idx < 0:
        search = args.ticker or args.name
        print(f"❌ 未找到 '{search}'", file=sys.stderr)
        sys.exit(1)

    removed = group_data["positions"].pop(idx)
    path = save_holdings(data, args.date)
    print(f"🗑️ 已移除 {removed['name']} ({removed['ticker']})")
    print(f"   保存到: {path}")


def cmd_set_fund(args):
    data = load_or_create_today(args.date)
    group_data = data["groups"].get(args.group)
    if not group_data:
        print(f"❌ 组 '{args.group}' 不存在", file=sys.stderr)
        sys.exit(1)

    old = group_data.get("fund", 0)
    group_data["fund"] = args.value
    path = save_holdings(data, args.date)
    print(f"✅ {args.group} 基金: ¥{old/10000:.2f}万 → ¥{args.value/10000:.2f}万")
    print(f"   保存到: {path}")


def cmd_set_cash(args):
    data = load_or_create_today(args.date)
    group_data = data["groups"].get(args.group)
    if not group_data:
        print(f"❌ 组 '{args.group}' 不存在", file=sys.stderr)
        sys.exit(1)

    old = group_data.get("cash", 0)
    group_data["cash"] = args.value
    path = save_holdings(data, args.date)
    print(f"✅ {args.group} 现金: ¥{old/10000:.2f}万 → ¥{args.value/10000:.2f}万")
    print(f"   保存到: {path}")


def cmd_set_cost(args):
    data = load_or_create_today(args.date)
    group_data = data["groups"].get(args.group)
    if not group_data:
        print(f"❌ 组 '{args.group}' 不存在", file=sys.stderr)
        sys.exit(1)

    old = group_data.get("cost_basis", 0)
    group_data["cost_basis"] = args.value
    path = save_holdings(data, args.date)
    print(f"✅ {args.group} 成本基数: ¥{old/10000:.1f}万 → ¥{args.value/10000:.1f}万")
    print(f"   保存到: {path}")


def main():
    parser = argparse.ArgumentParser(description="Portfolio holdings manager")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"), help="Target date")
    sub = parser.add_subparsers(dest="command")

    # show
    sub.add_parser("show", help="显示持仓")

    # update
    p_up = sub.add_parser("update", help="更新持仓数量")
    p_up.add_argument("--group", required=True, help="组名(进攻/稳健)")
    p_up.add_argument("--ticker", help="股票代码(如 SHA:603259)")
    p_up.add_argument("--name", help="股票名称(如 药明康德)")
    p_up.add_argument("--quantity", type=int, required=True, help="数量变动(正=加仓,负=减仓)")
    p_up.add_argument("--cost-price", type=float, dest="cost_price", help="本次交易成本价")

    # add
    p_add = sub.add_parser("add", help="添加新持仓")
    p_add.add_argument("--group", required=True)
    p_add.add_argument("--name", required=True)
    p_add.add_argument("--ticker", required=True)
    p_add.add_argument("--quantity", type=int, required=True)
    p_add.add_argument("--cost-price", type=float, required=True, dest="cost_price")

    # remove
    p_rm = sub.add_parser("remove", help="删除持仓")
    p_rm.add_argument("--group", required=True)
    p_rm.add_argument("--ticker", help="股票代码")
    p_rm.add_argument("--name", help="股票名称")

    # set-fund
    p_fund = sub.add_parser("set-fund", help="设置基金值")
    p_fund.add_argument("--group", required=True)
    p_fund.add_argument("--value", type=float, required=True, help="基金市值(元)")

    # set-cash
    p_cash = sub.add_parser("set-cash", help="设置现金")
    p_cash.add_argument("--group", required=True)
    p_cash.add_argument("--value", type=float, required=True, help="现金(元)")

    # set-cost
    p_cost = sub.add_parser("set-cost", help="设置组成本基数")
    p_cost.add_argument("--group", required=True)
    p_cost.add_argument("--value", type=float, required=True, help="成本(元)")

    args = parser.parse_args()

    if args.command == "show":
        cmd_show(args)
    elif args.command == "update":
        if not args.ticker and not args.name:
            print("❌ 必须提供 --ticker 或 --name", file=sys.stderr)
            sys.exit(1)
        cmd_update(args)
    elif args.command == "add":
        cmd_add(args)
    elif args.command == "remove":
        if not args.ticker and not args.name:
            print("❌ 必须提供 --ticker 或 --name", file=sys.stderr)
            sys.exit(1)
        cmd_remove(args)
    elif args.command == "set-fund":
        cmd_set_fund(args)
    elif args.command == "set-cash":
        cmd_set_cash(args)
    elif args.command == "set-cost":
        cmd_set_cost(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
