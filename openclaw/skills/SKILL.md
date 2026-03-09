---
name: portfolio-daily-tracker
description: Track and report multi-group stock portfolios with daily snapshots, live Yahoo Finance prices, P&L analytics, and push notifications (Feishu/Telegram). Supports A-shares, HK, US markets. Use when asked about holdings, buy/sell/rebalance positions, generate daily portfolio reports, check drawdown or returns, update fund/cash balances, or run the full snapshot-report-push pipeline.
version: 1.1.0
---

# Portfolio Daily Tracker Skill

## 触发条件

当用户提到以下内容时使用此技能：
- 持仓变动（买入、卖出、加仓、减仓、调仓）
- 查询当前持仓、市值、盈亏
- 询问投资组合报告、投资表现
- 提到具体股票名称+数量变化
- 询问回撤、收益率、资产走势
- 基金/现金余额变更

## 系统概述

投资组合跟踪系统采用每日快照模式：
- **持仓文件**: `portfolio/holdings/YYYY-MM-DD.json` — 每天独立的持仓记录
- **快照文件**: `portfolio/snapshots/YYYY-MM-DD.json` — 每日计算后的完整数据（含价格、市值、盈亏）
- **配置文件**: `portfolio/config.json` — 组别定义、API配置
- **历史CSV**: `portfolio/history.csv` — 时序数据汇总

数据目录: `$PROJECT_ROOT/engine/portfolio/`（可通过环境变量 `PORTFOLIO_DIR` 覆盖）

## 投资组合分组

系统支持多个投资组合分组，每组有独立的成本基数和持仓。例如：
- **进攻组** (Growth): 高成长个股 + 科技股 + 基金 + 现金
- **稳健组** (Income): 蓝筹 + ETF + 债券 + 现金

注意：同一只股票可以同时出现在不同组中（分别持有不同数量），更新时需要指定组别。

## 持仓变更操作

当用户说"卖了500股某股"、"加了200股某股"等，执行以下步骤：

### 1. 解析用户意图
- 识别股票名称 → 映射到 ticker（参见下方 Ticker 格式表）
- 识别操作类型：买入/加仓(+quantity), 卖出/减仓(-quantity)
- 识别组别：如果用户未指定且股票存在于多个组中，询问"哪个组？"
- 识别数量和可选的成本价

### 2. 执行变更
```bash
cd $PROJECT_ROOT/engine
python3 scripts/portfolio_manager.py update <ticker> --qty <new_total> [--cost <price>] [--group <组名>]
```

常用命令：
```bash
# 查看当前持仓
python3 scripts/portfolio_manager.py show

# 更新持仓数量（设置为新的总量）
python3 scripts/portfolio_manager.py update SHA:603259 --qty 4000 --group Growth

# 添加新持仓
python3 scripts/portfolio_manager.py add NASDAQ:NVDA NVIDIA Growth --qty 10 --cost 120.0 --market us

# 删除持仓
python3 scripts/portfolio_manager.py remove NASDAQ:META --group Growth

# 显示分组信息
python3 scripts/portfolio_manager.py groups
```

## 基金/现金变更

当用户说"今天基金涨了500块"、"基金现在值16万了"、"现金变成-50万"等：

### 基金更新
```bash
# 设置某组基金市值（单位：元）
python3 scripts/portfolio_manager.py set-fund --group Growth --value 160000

# 设置另一组基金市值
python3 scripts/portfolio_manager.py set-fund --group Income --value 5000
```

### 现金更新
```bash
# 设置某组现金（可为负数，表示融资/杠杆）
python3 scripts/portfolio_manager.py set-cash --group Growth --value -500000

# 设置另一组现金
python3 scripts/portfolio_manager.py set-cash --group Income --value 3300
```

### 解析用户意图
- "基金值16万" / "基金现在值16万" → set-fund --value 160000
- "基金涨了500" → 先读取当前 fund 值，加500后 set-fund
- "基金赎回了1万" → 先读取当前 fund 值，减10000后 set-fund
- "转入5万" / "充值5万" → 先读取当前 cash 值，加50000后 set-cash
- "现金变成-48万" → set-cash --value -480000

### 3. 确认变更
变更后读取并展示当天持仓文件，确认更新正确。

## 查询操作

### 查看最新快照
```bash
# 查看最新快照
ls -t engine/portfolio/snapshots/*.json | head -1 | xargs cat | python3 -m json.tool
```

### 生成/查看报告
```bash
# 生成今日快照（需取价）
python3 engine/scripts/portfolio_snapshot.py --date $(date +%Y-%m-%d)

# 生成报告
python3 engine/scripts/portfolio_report.py --date $(date +%Y-%m-%d)
```

### 查看历史数据
```bash
# 查看历史CSV
cat engine/portfolio/history.csv

# 查看特定日期快照
cat engine/portfolio/snapshots/2026-03-08.json | python3 -m json.tool
```

## Ticker 格式表

| 市场 | 格式 | 示例 | Yahoo代码 |
|------|------|------|-----------|
| 上交所A股 | `SHA:XXXXXX` | `SHA:603259` | `603259.SS` |
| 深交所A股 | `SHE:XXXXXX` | `SHE:002050` | `002050.SZ` |
| 上交所ETF | `SHA:XXXXXX` | `SHA:513050` | `513050.SS` |
| 港股 | `HKG:XXXX` | `HKG:0700` | `0700.HK` |
| 纳斯达克 | `NASDAQ:XXXX` | `NASDAQ:GOOGL` | `GOOGL` |
| 纽交所 | `NYSE:XXXX` | `NYSE:BRK.B` | `BRK-B` |

> 用户通常用中文名或简称，需要你映射为 Ticker 格式。
> 如果不确定某只股票的 Ticker，先用 `portfolio_manager.py show` 查看已有持仓中的命名。

## 交易信号短语识别

用户可能使用的中文表达：
- "卖了/卖掉/清仓/减持" → 减少数量
- "买了/加了/加仓/建仓" → 增加数量
- "换仓/调仓 X→Y" → 卖出X + 买入Y
- "成本调了/成本变成" → 更新 cost_price
- "基金赎回了/基金买了" → 更新 fund 值
- "转入/充值" → 更新 cash

## 完整日报流水线

当用户要求生成日报时，运行完整管道：

```bash
cd $PROJECT_ROOT/engine

# 1. 获取最新价格并生成快照
python3 scripts/portfolio_snapshot.py --date $(date +%Y-%m-%d)

# 2. 生成 Markdown 报告
python3 scripts/portfolio_report.py --date $(date +%Y-%m-%d)

# 3.（可选）推送到飞书/Telegram
# 在 scripts/portfolio-daily.sh 中可用 openclaw message send 推送
```

## Tools Provided

1. **get_tracker_snapshot** — 获取投资组合快照，包含分组持仓、融资杠杆、量化指标
2. **update_holdings** — 更新每日持仓，接受自然语言描述
3. **run_portfolio_pipeline** — 运行完整管道：快照→报告→推送

## Example Interactions
```
User: 今日持仓有变化吗？
Agent: [calls get_tracker_snapshot to show current holdings]

User: 卖了500股某股票，现金变为-48万
Agent: [calls update_holdings with changes, then run_portfolio_pipeline]

User: 帮我生成今天的日报
Agent: [calls run_portfolio_pipeline]

User: Growth 组的基金现在值16万了
Agent: [runs: portfolio_manager.py set-fund --group Growth --value 160000]
```

## 注意事项

1. **每日文件不可变**: 修改当天的文件不影响历史文件
2. **如果今天没有文件**: 自动从最近一天复制
3. **同一股票跨组**: 需要指定组别才能准确更新
4. **基金和现金**: 使用 `set-fund` 和 `set-cash` 命令更新
5. **周末/节假日**: 可以修改持仓，但价格快照获取可能无变化
6. **融资/杠杆**: 现金为负值时自动计算融资额和杠杆比率
7. **回撤**: 从 history.csv 全量历史数据计算
