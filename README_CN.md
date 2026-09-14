<div align="center">

# 📊 投资组合日报追踪器

**自托管投资组合记录与 Agent 股票研究工作台**

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://python.org)
[![React 18](https://img.shields.io/badge/React-18-61dafb.svg)](https://reactjs.org)
[![ClawHub Skill](https://img.shields.io/badge/ClawHub-portfolio--daily--tracker-orange)](https://clawhub.ai)

[🇬🇧 English](README_EN.md) &nbsp;·&nbsp; [🔬 研究指南](docs/RESEARCH_CN.md) &nbsp;·&nbsp; [飞书工作台](docs/FEISHU_WORKBENCH.md) &nbsp;·&nbsp; [📖 完整使用指南 (CN)](docs/FULL_GUIDE_CN.md)

</div>

---

### News / Versions

| 版本 | 日期 | 更新摘要 |
|---|---|---|
| [v3.2.1](https://github.com/Stepuuu/portfolio-daily-tracker/releases/tag/v3.2.1) | 2026-09-14 | 飞书每日清单新增资金转入、转出及本金核对；现金与本金联动，最终余额对账避免重复计算。 |
| [v3.2.0](https://github.com/Stepuuu/portfolio-daily-tracker/releases/tag/v3.2.0) | 2026-09-12 | 新增可复查的手动与 Agent 股票研究、持久化任务、模型接入及 CLI/HTTP/MCP 扩展；[飞书卡片工作台](docs/FEISHU_WORKBENCH.md)经配置后支持无变动一键更新、多账户变动清单统一确认、资产查看与研究任务。 |
| [v3.1.0](https://github.com/Stepuuu/portfolio-daily-tracker/releases/tag/v3.1.0) | 2026-09-09 | 新增需确认的交易账本与研究日志，支持 CSV 去重，修复多币种核算并提供离线演示。 |
| [V3 · AI 助手](CHANGELOG.md#v3--ai-trading-assistant) | 2026-03-08† | 整合投资组合追踪、AI 对话、React 面板、策略回测与定时日报。 |
| [V2 · 自托管引擎](CHANGELOG.md#v2--self-hosted-portfolio-engine) | 2026-03-08† | 转为 Python 自托管，使用本地 JSON/CSV，支持多市场行情、风险指标与自动日报。 |
| [V1 · Google Sheets](CHANGELOG.md#v1--google-sheets) | 2025-09-25† | 基于 Google Sheets 与 Apps Script，实现每日快照、行情更新、资产图表与月度盈亏记录。 |

† 历史里程碑：V1 日期取自最早的指南更新记录；V2 与 V3 首次收录于同一次仓库提交。这些日期不代表正式 Release 发布日。

[完整更新记录](CHANGELOG.md) · [GitHub Releases](https://github.com/Stepuuu/portfolio-daily-tracker/releases) · [后续计划](docs/ROADMAP.md)

### ✨ 功能特性

| 功能 | 说明 |
|------|------|
| 🌍 **多市场支持** | A 股 (SHA/SHE)、港股 (HKG)、美股 (NASDAQ/NYSE) 统一管理 |
| 💱 **自动汇率** | 实时 HKD/CNY、USD/CNY，所有资产折算人民币 |
| 📈 **量化指标** | 夏普比率、年化波动率、最大回撤、胜率、盈亏比 |
| 💰 **融资杠杆** | 负现金自动识别融资额，计算杠杆比例 |
| 🤖 **AI 对话助手** | 自然语言问持仓、盈亏、风险，支持 GPT/Claude/DeepSeek |
| 🔬 **股票研究工作台** | CSV/缓存/行情导入，手动与 Agent 实验、基线比较、净值曲线、任务恢复与导出 |
| 📊 **Web 面板** | React + TailwindCSS，5 KPI · 双饼图 · 净值曲线 · 月度收益 |
| 📉 **策略回测** | 内置双均线/RSI/自定义策略回测引擎 |
| 🔔 **每日推送** | 收盘后自动通知 → 确认变化 → 推送飞书/Telegram 日报 |
| ⏰ **自动调度** | Python 调度器（无需 cron），18:00 通知，19:00 兜底管道 |
| 🦞 **OpenClaw 技能** | 已发布到 [ClawHub](https://clawhub.ai)，安装命令：`clawhub install portfolio-daily-tracker` |

### 🔬 从问题到可复查的实验

研究工作台提供动量、均值回归和波动率三个股票日线学习模板，按时间划分训练、验证与测试，剔除跨分段边界的标签，并展示基线误差、成本假设和净值曲线。先用合成数据熟悉流程，再导入自己的 CSV、已有缓存或明确下载的市场数据。合成结果不代表真实市场证据，当前不包含期权曲面、多腿期权回测或实盘交易。

Agent 模式可以在预算内规划实验、读取验证集反馈、有限迭代、冻结方案后评价最终测试结果。连接支持 OpenAI 兼容 API、Anthropic API 及已有官方 Codex/Claude Code 登录；登录检查不保证模型或订阅额度可用。任务、事件与阶段保存在本机，支持取消、重试、定时触发和 JSON 导出。CLI、HTTP、MCP 与 Python 注册表可复用和扩展研究能力。

远程模型会收到研究问题、数据元信息和计算证据等上下文；本地存储不等于全程离线。导出**不会自动脱敏**，分享前请检查。方法假设、命令行示例与故障处理见 [研究指南](docs/RESEARCH_CN.md)，开发接口见 [扩展指南](docs/RESEARCH_EXTENSIONS.md)。

### 📁 项目结构

```
portfolio-daily-tracker/
├── engine/                      # 🔧 投资组合快照引擎
│   ├── scripts/
│   │   ├── portfolio_snapshot.py      # 核心：获取行情 → 计算 → 保存
│   │   ├── portfolio_daily_update.py  # 每日：克隆 → 解析变更 → 管道
│   │   ├── portfolio_report.py        # Markdown 报告生成
│   │   ├── portfolio_manager.py       # 持仓管理 CLI
│   │   ├── portfolio_scheduler.py     # Python 调度器（替代 cron）
│   │   ├── portfolio-cron.sh          # Cron 入口脚本
│   │   └── portfolio-daily.sh         # 传统每日脚本
│   └── portfolio/
│       ├── config.example.json        # 配置模板
│       └── holdings/example.json      # 持仓格式示例
│
├── dashboard/                   # 📱 完整交易助手 Web 应用
│   ├── main.py                        # 应用入口
│   ├── config.example.json            # 配置模板（复制为 config.json）
│   ├── config/                        # 配置模块
│   ├── core/                          # LLM、记忆、模型、工具
│   ├── providers/                     # LLM 和行情数据提供者
│   ├── agents/                        # AI 交易 Agent
│   ├── backtesting/                   # 策略回测引擎
│   ├── ui/                            # CLI 终端模式
│   ├── tools/                         # 浏览器 MCP 截图工具
│   ├── docs/                          # Agent 工具文档
│   ├── backend/                       # FastAPI REST API
│   │   ├── api/                       # 路由处理器（对话/行情/记忆/回测等）
│   │   ├── services/                  # AgentService
│   │   └── main.py                    # API 入口
│   └── frontend/                      # React + TailwindCSS
│       └── src/
│           ├── pages/                 # 面板/持仓/行情/记忆/回测 等页面
│           ├── components/            # ChatPanel/Layout/Sidebar 等组件
│           └── services/              # API 客户端层
│
├── openclaw/                    # 🦞 OpenClaw Agent 集成
│   ├── tools/portfolio_tools.py       # Agent 工具定义
│   ├── skills/SKILL.md                # 完整技能指南（中文）
│   └── examples/config.example.json
│
├── v1-google-sheets/            # 📋 V1：Google Sheets 版本（归档）
├── docker-compose.yml
├── scripts/research.py          # 研究任务 JSON CLI
├── Makefile                     # make setup / make lab / make demo / make start
├── start.sh                     # 开发启动脚本
├── stop.sh                      # 停止所有服务
└── README.md
```

### 🚀 快速开始

本地启动需要 **Python 3.10+、Node.js 22.12+ 和 Bash 4.3+**，无需 Docker，可直接在主机或已有容器内运行。

#### 一键安装（推荐）

```bash
git clone https://github.com/Stepuuu/portfolio-daily-tracker.git
cd portfolio-daily-tracker

python3 -m venv .venv
source .venv/bin/activate
make setup    # 安装依赖、复制配置模板
make lab      # 研究模式：后端 127.0.0.1:8000，前端 :3000
# 浏览器访问 http://localhost:3000/lab
```

手动研究不需要 LLM 密钥或账户配置。`make demo` 提供隔离的虚构组合与合成研究数据，组合保持只读。配置自己的组合和对话模型后使用 `make start`；同样可以访问研究工作台。命令行连接已运行的后端，例如 `python scripts/research.py datasets`，完整示例见 [研究指南](docs/RESEARCH_CN.md)。

交易入账需要预览和确认，AI 与截图识别不会自动入账。期初迁移、冲销与备份见 [账本指南](docs/LEDGER.md)，多币种现金与收益口径见 [核算说明](docs/ACCOUNTING.md)。

#### 方式 1：仅引擎（投资组合追踪器）

最简部署 — 只需 Python + `requests`：

```bash
cd engine
pip install requests

cp portfolio/config.example.json portfolio/config.json
# 编辑 config.json：设置 proxy、分组名、成本基础

cp portfolio/holdings/example.json portfolio/holdings/$(date +%Y-%m-%d).json
# 编辑为你的实际持仓

python3 scripts/portfolio_snapshot.py    # 生成快照
python3 scripts/portfolio_report.py      # 生成报告
```

#### 方式 2：完整交易助手

```bash
cd dashboard
pip install -r requirements.txt

cp config.example.json config.json
# 编辑 config.json：添加 AI API Key（OpenAI / Claude / DeepSeek）

# 启动后端
python3 -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 &

# 启动前端
cd frontend && npm install && npm run dev
# 浏览器访问 http://localhost:3000
```

或使用启动脚本：
```bash
./start.sh          # 同时启动后端 + 前端
./start.sh backend  # 仅启动后端
./start.sh engine   # 运行一次快照引擎
./stop.sh           # 停止所有服务
```

#### 方式 3：Docker Compose 一键启动

```bash
docker compose up -d
# 前端: http://localhost:3000
# API 文档: http://localhost:8000/docs
```

#### 方式 4：CLI 终端模式

```bash
cd dashboard && python3 main.py
# 支持 /portfolio, /add, /import, /refresh, /models 等命令
```

### 🦞 OpenClaw 技能（ClawHub）

本项目已作为 [OpenClaw](https://openclaw.ai) Agent 技能发布到 **ClawHub**。

**安装：**
```bash
clawhub install portfolio-daily-tracker
```

该技能使 AI Agent 能通过自然语言管理你的投资组合 — 买卖持仓、生成日报、更新基金/现金余额、运行完整的快照→报告→推送管道。详见 [openclaw/skills/SKILL.md](openclaw/skills/SKILL.md)。

### 🏗 系统架构

```
┌──────────────────────────────────────────────────────┐
│                    数据来源                           │
│  Yahoo Finance  ·  AKShare  ·  holdings/*.json        │
└─────────────────────┬────────────────────────────────┘
                      ▼
┌──────────────────────────────────────────────────────┐
│          portfolio_snapshot.py（~450 行）              │
│  fetch_prices() → fetch_fx() → calculate() →          │
│  quant_metrics() → save_snapshot() → sync()           │
└────────────────┬────────────────┬────────────────────┘
                 ▼                ▼
        ┌────────────┐   ┌─────────────────────────────┐
        │ 快照 JSON  │   │  FastAPI 后端（端口 8000）    │
        │ + CSV      │   │  /api/chat /api/market       │
        └────────────┘   │  /api/memory /api/backtest   │
                         └──────────────┬──────────────┘
                                        ▼
                         ┌─────────────────────────────┐
                         │  React 前端（端口 3000）      │
                         │  对话·持仓·行情·记忆·回测     │
                         └─────────────────────────────┘
                                        ▲
                         ┌─────────────────────────────┐
                         │  OpenClaw 🦞 AI Agent        │
                         │  飞书 / Telegram 推送         │
                         └─────────────────────────────┘
```

### 📅 每日交互流程

```
18:00  ─ 调度器触发
       1. 克隆昨日持仓 → 今日 .json
       2. 飞书推送："今日持仓有变化吗？"

       ← 用户回复："卖了100股茅台，进攻现金变为-5万"
         或者："未变化"

       3. Agent 解析自然语言 → 更新持仓 JSON
       4. 获取实时行情（Yahoo Finance）
       5. 计算快照（盈亏 / 回撤 / 量化指标）
       6. 生成 Markdown 日报
       7. 推送飞书/Telegram 日报
       8. 同步 Web Dashboard

19:00  ─ 兜底：如用户未响应则自动运行
```

**启动调度器：**
```bash
nohup python engine/scripts/portfolio_scheduler.py &
# 默认：18:00 通知，19:00 兜底管道
```

### 🤖 完整 AI 交易助手

本项目提供的不只是持仓追踪器 — 而是一个**完整的多页面 AI 交易助手**。`docker compose up` 后即可获得配备 8 个功能页面的 React Web 应用，后端由 FastAPI + LLM 驱动。

```
┌──────────────────────────────────────────────────────────────────────┐
│  💬 对话  │ 💼 持仓 │ 📊 追踪 │ 📈 行情 │ 🧠 记忆 │ 📉 回测         │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌─────────────────────────────────┐  ┌─────────────────────────┐   │
│  │  💬 AI 交易助手                 │  │  💼 实时持仓              │   │
│  │                                 │  │  进攻   ¥62.4万  +2.3%   │   │
│  │  你：今天持仓分析一下           │  │  稳健   ¥76.8万  -0.8%   │   │
│  │  🤖：茅台今日 +2.3%，进攻组     │  ├─────────────────────────┤   │
│  │  主要贡献；平安压力较大，        │  │  💡 操作建议              │   │
│  │  建议关注杠杆是否过高...        │  │  · 平安杠杆偏高，考虑减仓 │   │
│  │                                 │  │  · 茅台突破前高，可持有   │   │
│  │  你：帮我算一下夏普比率         │  └─────────────────────────┘   │
│  │  🤖：当前夏普 1.23，波动率      │                                  │
│  │  54.2%，处于合理区间...         │                                  │
│  └─────────────────────────────────┘                                  │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

#### 各页面详细功能

**💬 Dashboard（AI 对话）**
- 流式对话，支持 GPT-4o / Claude / DeepSeek 任意切换
- 上下文感知：每次对话自动携带最新持仓快照给 LLM
- 实时持仓侧边栏 — 仓位、盈亏、今日涨跌实时刷新
- AI 智能操作建议面板（可操作的具体建议，非泛泛分析）
- 对话历史记录与搜索

**💼 Portfolio（持仓管理）**
- 内联表单添加 / 编辑 / 删除持仓
- 多账户分组（进攻 / 稳健 或自定义任意分组）
- 负现金自动识别为融资，显示杠杆徽章
- 成本基础按组追踪，支持导出 JSON

**📊 Tracker（投资组合追踪）**
- 5 KPI 卡：总资产 · 总盈亏 · 今日盈亏 · 本月盈亏 · 最大回撤
- 分组双饼图展示持仓权重分布
- 资产净值曲线 vs 成本基线（交互式面积图）
- 月度盈亏可折叠柱状图
- 完整量化指标：夏普 · 波动率 · 胜率 · 盈亏比 · 平均盈亏

**📈 Market（实时行情）**
- 跨市场行情搜索：A 股 / 港股 / 美股
- 自选股监控列表，实时涨跌展示
- 价格预警设置（突破阈值自动通知）
- AKShare（沪深港）+ Yahoo Finance（美股）双数据源

**🧠 Memory（智能记忆）**
- 持久化用户画像：投资风格、风险偏好、策略备注
- 自动从对话中提炼关键决策（LLM 驱动摘要）
- 交易日记：带日期戳的交易备注
- 策略库：保存并回溯自己的交易规则与理由

**📉 Backtest（策略回测）**
- 内置策略：双均线交叉、RSI 均值回归、SMA 穿越
- 支持自定义策略插件
- 可配置：标的代码、时间范围、初始资金、手续费、滑点
- 结果指标：总收益率、年化收益、夏普比率、最大回撤、交易日志
- **AI 策略反思**：LLM 分析策略成败原因并给出改进建议
- 净值曲线图 + 基准对比

**⚙️ Settings（设置）**
- 运行时切换 LLM 提供商和模型（无需重启）
- 配置 API Key、代理地址、数据提供商
- 飞书 / Telegram Webhook 配置

| 页面 | 路由 | 后端接口 |
|------|------|----------|
| 对话 | `/` | `POST /api/chat` |
| 持仓 | `/portfolio` | `GET/POST /api/portfolio` |
| 追踪 | `/tracker` | `GET /api/portfolio/tracker` |
| 行情 | `/market` | `GET /api/market/quote` |
| 记忆 | `/memory` | `GET/POST /api/memory` |
| 回测 | `/backtest` | `POST /api/backtest/run` |
| 设置 | `/settings` | `GET/POST /api/settings` |

### 📝 配置说明

`engine/portfolio/config.json`：
```json
{
  "groups": {
    "进攻": { "cost_basis": 600000 },
    "稳健": { "cost_basis": 400000 }
  },
  "proxy": "http://127.0.0.1:7890",
  "feishu_chat_id": "your_feishu_chat_id",
  "ticker_map": {
    "贵州茅台": "SHA:600519",
    "中国平安": "SHA:601318",
    "腾讯控股": "HKG:0700",
    "苹果":     "NASDAQ:AAPL"
  }
}
```

**持仓格式**（`holdings/YYYY-MM-DD.json`）：
```json
{
  "date": "2026-03-06",
  "groups": {
    "进攻": {
      "cost_basis": 600000,
      "positions": [
        { "name": "贵州茅台", "ticker": "SHA:600519", "quantity": 100,  "cost_price": 1580.00 },
        { "name": "腾讯控股", "ticker": "HKG:0700",   "quantity": 500,  "cost_price": 398.60 }
      ],
      "fund": 50000,
      "cash": -85000
    },
    "稳健": {
      "cost_basis": 400000,
      "positions": [
        { "name": "中国平安", "ticker": "SHA:601318", "quantity": 2000, "cost_price": 42.50 }
      ],
      "fund": 80000,
      "cash": 130000
    }
  }
}
```

### Ticker 格式

| 市场 | 格式 | 示例 |
|------|------|------|
| A 股（沪） | `SHA:XXXXXX` | `SHA:600519` |
| A 股（深） | `SHE:XXXXXX` | `SHE:000858` |
| 港股 | `HKG:XXXX` | `HKG:0700` |
| 美股 | `NASDAQ:XX` / `NYSE:XX` | `NASDAQ:AAPL` |

### 🔄 自然语言更新

| 指令模式 | 示例 | 说明 |
|---------|------|------|
| 未变化 | `持仓未变化` | 保持原样，直接生成快照 |
| 现金变更 | `进攻现金变为-5万` | 更新指定组现金 |
| 基金变更 | `基金变为8万` | 更新基金金额 |
| 卖出 | `卖了100股茅台` | 减少持仓数量 |
| 买入 | `买了200股中国平安` | 增加已有持仓数量 |
| 买入新股 | `买了500股万润新能 代码是SHA:688275` | 自动新增新持仓 |
| 直接设置 | `腾讯控股 800股` | 直接设定数量 |
| 清仓 | `清仓茅台` / `万润新能清了` | 移除整个持仓 |
| 新增 | `新增 苹果 ticker:NASDAQ:AAPL 数量:50 成本:175.5` | 添加新股票 |
| 成本调整 | `进攻成本调整为620000` | 修改组成本基础 |

**以下为旧版 CLI 解析规则，启用账本后不再用于写入持仓。账本要求显式价格、数量和原币金额，不会反推成交价。**

多条变更用逗号 `,` 或分号 `；` 分隔。数字支持 `万` 后缀；对现金/基金字段，像 `-44.273`、`15.635` 这类省略 `万` 的写法也会自动按“万”解析。若新增持仓未填写成本价，系统会尝试根据同一条消息里的现金/基金变动反推单股成本。

### 📋 V1：Google Sheets 版本

V1 基于 Google Sheets + Apps Script，适合不想自托管的用户：
- `v1-google-sheets/code.gs` — Apps Script 源码
- `v1-google-sheets/` — 历史 Google Sheets 脚本与指南
- `v1-google-sheets/User_Guide_CN.md` — 中文使用指南

### 🆚 版本对比

| 维度 | V1（Google Sheets） | 当前自托管版 |
|------|---------------------|-------------|
| 数据存储 | Google Drive | 本地 SQLite/JSON/CSV |
| 行情来源 | Google Finance + 新浪 | Yahoo Finance + AKShare |
| 多市场 | 部分支持 | ✅ A 股 + 港股 + 美股 |
| 量化指标 | ❌ | ✅ 夏普 / 波动率 / 回撤 |
| AI 对话 | ❌ | ✅ GPT / Claude / DeepSeek |
| 策略回测 | ❌ | ✅ 自定义策略引擎 |
| Web 面板 | Google Sheets 图表 | ✅ React + TailwindCSS |
| 消息推送 | ❌ | ✅ 飞书 / Telegram / Slack |
| 数据隐私 | 数据在 Google | 本地存储；远程模型接收请求上下文 |
| Docker | ❌ | ✅ 一键部署 |

### 📄 许可证

MIT — 详见 [LICENSE](LICENSE)

---

<div align="center">
  <b>📊 用代码管理你的投资 · OpenClaw 🦞 驱动</b><br>
  <a href="https://github.com/Stepuuu/portfolio-daily-tracker">GitHub</a> ·
  <a href="https://clawhub.ai">ClawHub 技能</a> ·
  MIT License
</div>
