# Portfolio Tracker Skill

## Description
Portfolio tracking agent skill — provides tools for querying portfolio snapshots,
updating daily holdings via natural language, and running the full snapshot pipeline.

## Tools Provided
1. **get_tracker_snapshot** — Read portfolio snapshot data (positions, P&L, leverage, quant metrics)
2. **update_holdings** — Update daily holdings from natural language ("卖了500股xxx", "现金变为5000")
3. **run_portfolio_pipeline** — Run full pipeline: price fetch → snapshot → report → push

## When to Use
- User asks about portfolio/holdings/positions
- User reports daily holding changes
- User wants to generate today's report

## Example Interactions
```
User: 今日持仓有变化吗？
Agent: [calls get_tracker_snapshot to show current holdings]

User: 卖了500股药明康德，现金变为-48万
Agent: [calls update_holdings → run_portfolio_pipeline]

User: 帮我生成今天的日报
Agent: [calls run_portfolio_pipeline]
```
