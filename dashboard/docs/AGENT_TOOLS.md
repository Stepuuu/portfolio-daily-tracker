# Agent 工具系统 (Agent Tools System)

## 功能概述

交易助手支持 **函数调用/工具使用** (Function Calling/Tool Use)，Agent 可以在对话中主动调用工具获取实时数据，提供更准确、有数据支撑的建议。

## 已实现的工具

### 1. get_stock_quote
获取指定股票的实时行情数据

**用法示例**：
- "贵州茅台现在什么价格？"
- "帮我看看 603259 今天涨了多少"
- "阿里巴巴港股现在多少钱？"

**返回数据**：最新价、涨跌幅、成交量、今日开高低收

### 2. get_portfolio
获取用户当前的持仓信息

**用法示例**：
- "我现在持仓情况如何？"
- "帮我看看我的仓位"

**返回数据**：持仓列表（代码、名称、数量、成本价、现价、盈亏）、现金、总市值

### 3. get_market_indices
获取主要市场指数的实时行情

**用法示例**：
- "今天大盘怎么样？"
- "上证指数现在多少点？"

**返回数据**：上证指数、深证成指、创业板指、沪深300

### 4. compare_stocks
比较多只股票的行情表现

**用法示例**：
- "帮我对比一下茅台和五粮液"
- "比较一下 600519、000858"

**返回数据**：各股票的价格、涨跌幅、成交量

## 工作原理

### 1. 工具定义
在 `core/tools.py` 中定义可用工具，包含工具名称、描述、参数定义、执行函数。

### 2. LLM 集成
支持 Claude Tool Use 和 OpenAI Function Calling 两种模式：
- 工具定义自动转换为对应 API 的 schema 格式
- LLM 在对话中判断是否需要调用工具
- 支持最多 5 次迭代工具调用

### 3. 工具执行流程
```
用户提问 → LLM 判断需要工具 → 返回 tool_use →
执行工具函数 → 将结果返回 LLM → LLM 生成最终回复
```

### 4. 多轮迭代
Agent 可以在一次对话中连续调用多个工具：
1. 先调用 `get_portfolio` 了解持仓
2. 再调用 `get_stock_quote` 查询持仓股票当前价格
3. 最后基于实际数据给出建议

## 扩展新工具

### 步骤 1：在 `core/tools.py` 中定义工具

```python
def get_news_tool(news_provider) -> Tool:
    """获取财经新闻工具"""
    async def get_news(keyword: str, limit: int = 5) -> Dict:
        news_list = await news_provider.search_news(keyword, limit)
        return {"news": news_list}

    return Tool(
        name="get_news",
        description="获取与指定关键词相关的财经新闻",
        parameters=[
            ToolParameter(
                name="keyword",
                type=ToolParameterType.STRING,
                description="搜索关键词"
            ),
            ToolParameter(
                name="limit",
                type=ToolParameterType.NUMBER,
                description="返回数量",
                required=False
            )
        ],
        function=get_news
    )
```

### 步骤 2：在 Agent Service 中注册

在 `backend/services/agent_service.py` 的 `_init_agent()` 方法中注册工具：

```python
tools = [
    get_stock_quote_tool(self.market_provider),
    get_portfolio_tool(self.portfolio_provider),
    get_market_indices_tool(self.market_provider),
    compare_stocks_tool(self.market_provider),
    get_news_tool(self.news_provider),  # 新增
]
```

### 步骤 3：重启服务

```bash
./start.sh
```

Agent 会自动识别并使用新工具。

## 技术实现

### 核心文件

| 文件 | 用途 |
|------|------|
| `core/tools.py` | 工具定义（Tool、ToolParameter、ToolExecutor） |
| `agents/trader/agent.py` | Agent 工具调用循环 |
| `providers/llm/claude.py` | Claude Tool Use 集成 |
| `providers/llm/openai.py` | OpenAI Function Calling 集成 |
| `backend/services/agent_service.py` | 服务层工具注册 |

### Claude Tool Use 消息格式

```python
# LLM 返回工具调用
{"type": "tool_use", "id": "toolu_xxx", "name": "get_stock_quote", "input": {"symbol": "600519"}}

# 返回工具结果
{"type": "tool_result", "tool_use_id": "toolu_xxx", "content": "{\"price\": 1418.43, ...}"}
```

## 最佳实践

1. **单一职责**: 每个工具只做一件事
2. **清晰描述**: 让 LLM 准确理解工具用途
3. **参数验证**: 在工具函数中验证输入
4. **错误处理**: 返回友好的错误信息
5. **批量查询**: 优先使用 `compare_stocks` 等批量工具

## 已知限制

- 当前限制为 5 次迭代工具调用
- 流式模式下工具调用后会切换为非流式返回
- 复杂多步推理可能需要优化 Prompt
