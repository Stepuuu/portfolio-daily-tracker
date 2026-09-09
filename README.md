<div align="center">

# 📊 Portfolio Daily Tracker

**A comprehensive self-hosted investment portfolio tracking & AI trading assistant**

**全功能自托管投资组合追踪 & AI 交易助手**

[![Checks](https://github.com/Stepuuu/portfolio-daily-tracker/actions/workflows/checks.yml/badge.svg)](https://github.com/Stepuuu/portfolio-daily-tracker/actions/workflows/checks.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://python.org)
[![React 18](https://img.shields.io/badge/React-18-61dafb.svg)](https://reactjs.org)
[![ClawHub Skill](https://img.shields.io/badge/ClawHub-portfolio--daily--tracker-orange)](https://clawhub.ai)

**📖 Documentation / 文档**

[🇬🇧 English](README_EN.md) · [🇨🇳 中文](README_CN.md)

</div>

---

### Highlights / 亮点

| | Feature | 功能 |
|---|---------|------|
| 🌍 | Multi-market: A-shares, HK, US | 多市场：A 股、港股、美股 |
| 📈 | Sharpe, volatility, max drawdown | 夏普、波动率、最大回撤 |
| 🤖 | AI chat with GPT / Claude / DeepSeek | AI 对话助手 |
| 📊 | Transaction ledger and research journal | 交易账本与研究复盘 |
| 📉 | Strategy backtesting engine | 策略回测引擎 |
| 🔔 | Auto daily report → Feishu / Telegram | 每日自动推送日报 |
| 🦞 | OpenClaw agent skill on ClawHub | OpenClaw 技能已发布 |

### Try it with fictional data / 先体验演示数据

![Portfolio tracker with fictional multi-currency accounts](docs/images/tracker-demo.png)

Requires **Python 3.10+**, **Node.js 22.12+** and Bash 4.3+ for the local launcher.
After `make setup`, run `make demo` and open [the tracker](http://localhost:3000/tracker).
Demo mode is read-only and needs no API key. It never overwrites your holdings.

Record trades with fees and fractional shares, preview every balance change, and
confirm once. Import standard CSV with duplicate detection, reverse mistakes,
back up the ledger, and keep a thesis with a review date for each security.
Native CNY/HKD/USD cash, valuation guards and flow-adjusted drawdown make the
accounting assumptions visible. AI prepares proposals for your review.

**[Ledger guide / 账本使用说明](docs/LEDGER.md)** · **[Release notes](CHANGELOG.md)**

![Transaction preview using fictional data](docs/images/ledger-demo.png)
See [accounting conventions](docs/ACCOUNTING.md), [planned work](docs/ROADMAP.md),
[changes](CHANGELOG.md) and [deployment scope](SECURITY.md).

### Quick Start / 快速开始

```bash
git clone https://github.com/Stepuuu/portfolio-daily-tracker.git
cd portfolio-daily-tracker
python3 -m venv .venv
source .venv/bin/activate
make setup
make demo  # or make start for your own configured portfolio
# Open http://localhost:3000
```

Docker is optional. The commands above run directly on a host or inside an existing container.
For users with a Docker host:
```bash
docker compose up -d
```

**OpenClaw Skill:**
```bash
clawhub install portfolio-daily-tracker
```

See full documentation: [English](README_EN.md) · [中文](README_CN.md)

---

### Contributing / 贡献

We welcome issues, feature requests, and pull requests!
If you find a bug, have an idea, or want to improve the code, please open an [issue](https://github.com/Stepuuu/portfolio-daily-tracker/issues) or submit a [pull request](https://github.com/Stepuuu/portfolio-daily-tracker/pulls).

欢迎提交 Issue、功能建议和 Pull Request！
如果你发现了 Bug、有改进想法或希望贡献代码，请在 [Issues](https://github.com/Stepuuu/portfolio-daily-tracker/issues) 中反馈，或直接提交 [PR](https://github.com/Stepuuu/portfolio-daily-tracker/pulls)。

---

### Citation / 引用

If this project helps your research or workflow, please cite it:

如果本项目对你的研究或工作有帮助，欢迎引用：

```bibtex
@misc{portfolio-daily-tracker,
  author       = {Stepuuu},
  title        = {Portfolio Daily Tracker — Self-hosted Investment Portfolio Tracking \& AI Trading Assistant},
  year         = {2026},
  publisher    = {GitHub},
  howpublished = {\url{https://github.com/Stepuuu/portfolio-daily-tracker}},
}
```

---

<div align="center">
  <b>📊 Manage your investments with code · 用代码管理你的投资</b><br>
  <b>Powered by OpenClaw 🦞</b><br>
  <a href="https://github.com/Stepuuu/portfolio-daily-tracker">GitHub</a> ·
  <a href="https://clawhub.ai">ClawHub</a> ·
  MIT License
</div>
