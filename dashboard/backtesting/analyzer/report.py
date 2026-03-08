"""
报告生成器 - 生成 HTML / Markdown / JSON 格式的回测报告
灵感: qlib 的 AnalysisPositionReport + rqalpha 的 sys_analyser
"""
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Optional


class ReportGenerator:
    """
    回测报告生成器

    支持输出:
      - Markdown 文本报告
      - HTML 富文本报告
      - JSON 数据格式
    """

    def __init__(self, result):
        """
        Args:
            result: BacktestResult 对象
        """
        self.result = result
        self.stats = result.stats
        self.equity_df = result.equity_df

    # ------------------------------------------------------------------ #
    #  Markdown 报告
    # ------------------------------------------------------------------ #

    def to_markdown(self) -> str:
        """生成 Markdown 格式回测报告"""
        s = self.stats
        r = self.result

        lines = [
            f"# 回测报告: {r.strategy_name}",
            f"",
            f"> 运行时间: {r.run_date} | Run ID: {r.run_id}",
            f"",
            f"## 基本信息",
            f"| 项目 | 值 |",
            f"|:-----|:---|",
            f"| 策略名称 | {r.strategy_name} |",
            f"| 回测标的 | {r.primary_symbol} |",
            f"| 策略参数 | `{json.dumps(r.strategy_params, ensure_ascii=False)}` |",
            f"| 初始资金 | {r._broker.initial_cash:,.0f} 元 |",
            f"",
            f"## 收益指标",
            f"| 指标 | 数值 |",
            f"|:-----|-----:|",
            f"| 初始资金 | {r._broker.initial_cash:,.0f} 元 |",
            f"| 最终净值 | {s.final_value:,.0f} 元 |",
            f"| 总收益率 | {s.total_return:.2%} |",
            f"| 年化收益 | {s.annualized_return:.2%} |",
            f"| 基准收益 | {s.benchmark_return:.2%} |",
            f"| Alpha | {s.alpha:.2%} |",
            f"",
            f"## 风险指标",
            f"| 指标 | 数值 |",
            f"|:-----|-----:|",
            f"| 最大回撤 | {s.max_drawdown:.2%} |",
            f"| 最大回撤持续 | {s.max_drawdown_duration} 天 |",
            f"| 年化波动率 | {s.volatility:.2%} |",
            f"| VaR (95%) | {s.var_95:.2%} |",
            f"| 下行偏差 | {s.downside_deviation:.2%} |",
            f"",
            f"## 风险效率",
            f"| 指标 | 数值 |",
            f"|:-----|-----:|",
            f"| 夏普比率 | {s.sharpe_ratio:.4f} |",
            f"| 索提诺比率 | {s.sortino_ratio:.4f} |",
            f"| 卡玛比率 | {s.calmar_ratio:.4f} |",
            f"",
            f"## 交易统计",
            f"| 指标 | 数值 |",
            f"|:-----|-----:|",
            f"| 交易次数 | {s.total_trades} |",
            f"| 胜率 | {s.win_rate:.2%} |",
            f"| 盈亏比 | {s.profit_factor:.4f} |",
            f"| 平均盈利 | {s.avg_profit:.2f} 元 |",
            f"| 平均亏损 | {s.avg_loss:.2f} 元 |",
            f"| 最大单笔盈利 | {s.max_single_profit:.2f} 元 |",
            f"| 最大单笔亏损 | {s.max_single_loss:.2f} 元 |",
            f"",
        ]

        # 交易明细
        if r.trades:
            lines += [
                f"## 交易明细 (最近20笔)",
                f"| 日期 | 方向 | 数量 | 价格 | 盈亏 |",
                f"|:-----|:-----|-----:|-----:|-----:|",
            ]
            for trade in r.trades[-20:]:
                pnl_str = f"{trade.pnl:+.0f}" if trade.pnl != 0 else "-"
                lines.append(
                    f"| {trade.timestamp} | {trade.direction} | "
                    f"{trade.quantity:.0f} | {trade.price:.2f} | {pnl_str} |"
                )

        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    #  HTML 报告
    # ------------------------------------------------------------------ #

    def to_html(self) -> str:
        """生成 HTML 富文本报告 (内嵌 Chart.js 净值曲线)"""
        s = self.stats
        r = self.result
        equity_df = self.equity_df

        # 净值曲线数据
        if not equity_df.empty:
            dates = [str(d.date()) for d in equity_df.index]
            net_values = [round(v, 4) for v in equity_df["net_value"].tolist()]
            drawdowns = [round(v * 100, 4) for v in equity_df["drawdown"].tolist()]
        else:
            dates, net_values, drawdowns = [], [], []

        # 颜色: 盈利绿, 亏损红
        color = "#10b981" if s.total_return >= 0 else "#ef4444"
        return_str = f"+{s.total_return:.2%}" if s.total_return >= 0 else f"{s.total_return:.2%}"

        html = f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<title>回测报告 - {r.strategy_name}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #f8fafc; color: #1e293b; padding: 24px; }}
  .header {{ background: linear-gradient(135deg, #1e3a5f 0%, #0f172a 100%);
             color: white; padding: 24px 32px; border-radius: 12px; margin-bottom: 24px; }}
  .header h1 {{ font-size: 1.5rem; font-weight: 700; }}
  .header .subtitle {{ opacity: 0.7; font-size: 0.875rem; margin-top: 4px; }}
  .return {{ font-size: 2rem; font-weight: 800; color: {color}; margin-top: 8px; }}
  .card-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
               gap: 16px; margin-bottom: 24px; }}
  .card {{ background: white; border-radius: 10px; padding: 16px 20px;
           box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
  .card .label {{ font-size: 0.75rem; color: #64748b; text-transform: uppercase;
                  letter-spacing: 0.05em; }}
  .card .value {{ font-size: 1.25rem; font-weight: 700; margin-top: 4px; }}
  .chart-container {{ background: white; border-radius: 10px; padding: 20px;
                      box-shadow: 0 1px 3px rgba(0,0,0,0.08); margin-bottom: 24px; }}
  .chart-title {{ font-size: 0.875rem; font-weight: 600; color: #64748b;
                  margin-bottom: 16px; }}
  .section {{ background: white; border-radius: 10px; padding: 20px;
              box-shadow: 0 1px 3px rgba(0,0,0,0.08); margin-bottom: 16px; }}
  .section h3 {{ font-size: 0.875rem; font-weight: 700; color: #64748b;
                text-transform: uppercase; margin-bottom: 12px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.875rem; }}
  th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #f1f5f9; }}
  th {{ background: #f8fafc; color: #64748b; font-weight: 600; font-size: 0.75rem; }}
  .pos {{ color: #10b981; font-weight: 600; }}
  .neg {{ color: #ef4444; font-weight: 600; }}
</style>
</head>
<body>
<div class="header">
  <h1>📊 回测报告: {r.strategy_name}</h1>
  <div class="subtitle">标的: {r.primary_symbol} | {r.run_date} | Run: {r.run_id}</div>
  <div class="return">{return_str}</div>
</div>

<div class="card-grid">
  <div class="card">
    <div class="label">年化收益</div>
    <div class="value {'pos' if s.annualized_return >= 0 else 'neg'}">{s.annualized_return:.2%}</div>
  </div>
  <div class="card">
    <div class="label">夏普比率</div>
    <div class="value">{s.sharpe_ratio:.3f}</div>
  </div>
  <div class="card">
    <div class="label">最大回撤</div>
    <div class="value neg">{s.max_drawdown:.2%}</div>
  </div>
  <div class="card">
    <div class="label">卡玛比率</div>
    <div class="value">{s.calmar_ratio:.3f}</div>
  </div>
  <div class="card">
    <div class="label">交易次数</div>
    <div class="value">{s.total_trades}</div>
  </div>
  <div class="card">
    <div class="label">胜率</div>
    <div class="value">{s.win_rate:.1%}</div>
  </div>
  <div class="card">
    <div class="label">盈亏比</div>
    <div class="value">{s.profit_factor:.3f}</div>
  </div>
  <div class="card">
    <div class="label">年化波动率</div>
    <div class="value">{s.volatility:.2%}</div>
  </div>
</div>

<div class="chart-container">
  <div class="chart-title">净值曲线</div>
  <canvas id="equity-chart" height="80"></canvas>
</div>

<div class="chart-container">
  <div class="chart-title">回撤曲线</div>
  <canvas id="dd-chart" height="50"></canvas>
</div>

<script>
const dates = {json.dumps(dates)};
const netValues = {json.dumps(net_values)};
const drawdowns = {json.dumps(drawdowns)};

new Chart(document.getElementById('equity-chart'), {{
  type: 'line',
  data: {{
    labels: dates,
    datasets: [{{
      label: '策略净值', data: netValues,
      borderColor: '{color}', backgroundColor: '{color}22',
      borderWidth: 2, fill: true, pointRadius: 0, tension: 0.1,
    }}]
  }},
  options: {{ responsive: true, interaction: {{ mode: 'index', intersect: false }},
    scales: {{ x: {{ ticks: {{ maxTicksLimit: 12 }} }}, y: {{ beginAtZero: false }} }},
    plugins: {{ legend: {{ display: false }} }} }}
}});

new Chart(document.getElementById('dd-chart'), {{
  type: 'line',
  data: {{
    labels: dates,
    datasets: [{{
      label: '回撤%', data: drawdowns,
      borderColor: '#ef4444', backgroundColor: '#ef444422',
      borderWidth: 1.5, fill: true, pointRadius: 0,
    }}]
  }},
  options: {{ responsive: true, interaction: {{ mode: 'index', intersect: false }},
    scales: {{ x: {{ ticks: {{ maxTicksLimit: 12 }} }}, y: {{ max: 0 }} }},
    plugins: {{ legend: {{ display: false }} }} }}
}});
</script>
</body>
</html>"""
        return html

    # ------------------------------------------------------------------ #
    #  保存报告
    # ------------------------------------------------------------------ #

    def save(self, output_dir: str = "data/backtest_reports"):
        """保存报告到文件"""
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = f"{output_dir}/{self.result.primary_symbol}_{self.result.strategy_name}_{ts}"

        # HTML
        html_path = f"{base}.html"
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(self.to_html())

        # JSON
        json_path = f"{base}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(self.result.to_dict(), f, ensure_ascii=False, indent=2)

        return html_path, json_path
