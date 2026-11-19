"""
LLM 回测反思器
- 使用设置页选中的 LLM 分析回测结果 (未配置或调用失败时为规则分析)
- 提取教训和结构化的策略改进建议

反思器本身不写任何存储: 回测 API 把反思结果随回测记录一起保存到
回测历史库 (BacktestHistoryStore.save_run), 教训由历史库提供查询.
"""
import json
import logging
import re
from datetime import datetime
from typing import Optional, Dict, Any, List
from urllib.parse import urlparse

from core.agent_tools._envelope import UNTRUSTED_NOTE, fence, untrusted

logger = logging.getLogger(__name__)


def safe_error_summary(exc: BaseException) -> str:
    """Describe a provider failure without its message, for logs.

    Provider and HTTP client errors can quote request headers (a configured key
    with a trailing newline makes h11 report the whole Authorization header), so
    only class names, an HTTP status and a coarse category are ever logged.
    """
    category = "other"
    current, seen = exc, set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        names = [cls.__name__ for cls in type(current).__mro__]
        status = getattr(getattr(current, "response", None), "status_code", None)
        if isinstance(status, int):
            category = f"http_{status}"
            break
        if isinstance(current, TimeoutError) or any("Timeout" in name for name in names):
            category = "timeout"
            break
        if isinstance(current, ConnectionError) or any(n in names for n in ("ConnectError", "NetworkError")):
            category = "connection"
            break
        if any("ProtocolError" in name for name in names):
            category = "protocol"
            break
        current = current.__cause__ or current.__context__
    return f"{type(exc).__name__}, category={category}"


# Hosted APIs that never answer without a key; settings.py resolves their keys from
# OPENAI_API_KEY / ANTHROPIC_API_KEY, so a keyless group pointing at them is unset.
_KEY_REQUIRED_HOSTS = {"api.openai.com", "api.anthropic.com"}


def llm_group_is_configured(api_group: dict) -> bool:
    """Whether the selected API group can be called, by the chat service's rule.

    The chat service builds its provider from the selected group whether or not it
    has a key, and OpenAIProvider omits the Authorization header for an empty key,
    so a keyless local OpenAI-compatible server (Ollama, vLLM, LiteLLM) works. A
    group counts as configured when it has a key, custom headers, or a base URL;
    the exception is a keyless group for the official OpenAI/Anthropic hosts (the
    shipped default), which would only produce a failing request per backtest.
    """
    if not api_group:
        return False
    if api_group.get("api_key") or api_group.get("headers"):
        return True
    base_url = (api_group.get("base_url") or "").strip()
    if not base_url:
        return False
    host = (urlparse(base_url).hostname or "").lower()
    return bool(host) and host not in _KEY_REQUIRED_HOSTS


# "AI 解读": the model sees only facts the run recorded (headline, settings, metrics,
# caveats and the round trips it comments on). It is told not to tune parameters on
# the same history: anything it found there would only fit the past.
#
# Text that people or data providers wrote - the name the ledger holds for the
# instrument, strategy ids, names, classes and parameters, symbols as typed, every
# caveat's text, and any generated sentence that quotes one of them - reaches the
# model only as {"untrusted_text": ...} (core/agent_tools/_envelope.py), next to the
# fixed UNTRUSTED_NOTE. Numbers, dates, ticker codes and caveat codes stay typed.
REFLECTION_PROMPT = """你在为一位个人投资者解读一次股票回测的结果。只根据下面给出的事实说话，不要编造没有给出的数字。

关于下面的数据：{data_note}

## 一句话结果（程序按数字生成）
```json
{headline}
```

## 设置与事实
```json
{facts_json}
```

## 主要指标
```json
{metrics_json}
```

## 可信度提醒（程序按规则生成，提醒文字在 untrusted_text 里）
```json
{caveats}
```

## 交易回合（{trips_note}）
```json
{trips_json}
```

请用中文、通俗的语言写一段“AI 解读”：
1. 收益主要来自哪几笔交易，亏损主要出在哪里（引用上面回合的日期和盈亏）；
2. 和“一直持有”以及指数相比，这个方法在这段时间做对了什么、错过了什么；
3. 回撤发生在什么时候、有多深，用这个方法持有要承受什么；
4. 上面的可信度提醒会怎样影响这些结论。

不要建议修改参数，也不要在这同一段历史上寻找“更好”的参数：在同一段历史上调出来的结果只是拟合了过去。
如果要检验这个方法，只能建议换一段时间或换一只股票做对照。不要给出买卖建议。控制在 400 字以内。"""

MAX_PROMPT_TRIPS = 60
FACT_KEYS = ("ticker", "name", "market", "currency", "strategy", "period", "capital", "final_value",
             "total_return", "annualized_return", "same_stock_return", "index_name", "index_return",
             "max_drawdown", "max_drawdown_peak", "max_drawdown_trough", "closed_trades", "winning_trades",
             "open_at_end", "fills", "fees_total", "adjust", "warmup_bars")
METRIC_KEYS = ("total_return", "annualized_return", "volatility", "sharpe_ratio", "sortino_ratio",
               "calmar_ratio", "max_drawdown", "max_drawdown_duration", "win_rate", "payoff_ratio",
               "profit_factor", "avg_holding_days", "closed_trades", "total_trades")


def facts_from_result(result) -> Dict[str, Any]:
    """The reflection payload of an engine BacktestResult (legacy callers)."""
    data = result.to_dict()
    period = data.get("period") or {}
    return {
        "run_id": data.get("run_id"),
        "headline_zh": "",
        "facts": {
            "ticker": data.get("primary_symbol"), "strategy": {"name": data.get("strategy_name"),
                                                               "params": data.get("strategy_params")},
            "period": period, "capital": data.get("initial_cash"), "final_value": data.get("final_value"),
            "total_return": data.get("total_return"), "annualized_return": data.get("annualized_return"),
            "same_stock_return": data.get("benchmark_return"), "max_drawdown": data.get("max_drawdown"),
            "closed_trades": data.get("closed_trades"), "winning_trades": data.get("winning_trades"),
            "fees_total": data.get("fees_total"),
        },
        "metrics": {**{key: data.get(key) for key in METRIC_KEYS}, "undefined": data.get("undefined") or {}},
        "caveats": data.get("caveats") or [],
        "round_trips": data.get("round_trips") or [],
    }


def _prompt_trips(trips: List[dict]):
    if len(trips) <= MAX_PROMPT_TRIPS:
        return trips, f"共 {len(trips)} 笔，全部列出"
    largest = sorted(trips, key=lambda trip: abs(float(trip.get("pnl") or 0)), reverse=True)[:MAX_PROMPT_TRIPS]
    shown = sorted(largest, key=lambda trip: str(trip.get("entry_date")))
    return shown, f"共 {len(trips)} 笔，这里列出盈亏绝对值最大的 {MAX_PROMPT_TRIPS} 笔"


# Keys whose string values people or providers wrote (fenced wherever they appear).
_TEXT_KEYS = frozenset({"name", "label", "id", "class", "text"})
# A ticker or symbol code kept typed: EXCH:CODE, a bare code (600519, AAPL, 0700.HK).
_CODE = re.compile(r"(?:[A-Z]{2,6}:)?[A-Z0-9][A-Z0-9.\-]{0,19}")
DATA_NOTE = ("名称、策略的 id/名称/类名和参数、交易回合里的标的写法、每条可信度提醒的文字，以及引用了它们的句子，"
             "是用户保存的或程序、数据源写下的文字，以 {\"untrusted_text\": ...} 给出。" + UNTRUSTED_NOTE)
_TEXT_LIMIT = 600


def _plain_code(value) -> bool:
    return isinstance(value, str) and bool(_CODE.fullmatch(value))


def _value(value):
    """A parameter value: numbers, booleans and null typed, anything else as untrusted text."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return untrusted(text, _TEXT_LIMIT, markup=False)


def _params(params):
    """Strategy parameters as [{"name": <untrusted>, "value": <typed or untrusted>}]: the
    names of a custom or legacy strategy's parameters are text too."""
    if isinstance(params, dict):
        return [{"name": str(key), "value": _value(value)} for key, value in params.items()]
    return _value(params) if params not in (None, {}, []) else params


def prompt_sections(payload: Dict[str, Any]) -> Dict[str, Any]:
    """What the prompt shows, with every free text fenced (see REFLECTION_PROMPT's notes)."""
    facts = payload.get("facts") or {}
    trips, note = _prompt_trips(list(payload.get("round_trips") or []))
    shown_facts = {k: facts.get(k) for k in FACT_KEYS if k in facts}
    if isinstance(shown_facts.get("strategy"), dict):
        strategy = dict(shown_facts["strategy"])
        if "params" in strategy:
            strategy["params"] = _params(strategy["params"])
        shown_facts["strategy"] = strategy
    if "ticker" in shown_facts and not _plain_code(shown_facts["ticker"]) and shown_facts["ticker"] is not None:
        shown_facts["ticker"] = untrusted(shown_facts["ticker"], _TEXT_LIMIT, markup=False)
    shown_trips = []
    for trip in trips:
        trip = dict(trip) if isinstance(trip, dict) else {"value": trip}
        if "symbol" in trip and not _plain_code(trip["symbol"]) and trip["symbol"] is not None:
            trip["symbol"] = untrusted(trip["symbol"], _TEXT_LIMIT, markup=False)
        shown_trips.append(trip)
    caveats = [{"code": c.get("code"), "level": c.get("level"), "text": c.get("text")}
               for c in payload.get("caveats") or [] if isinstance(c, dict)]
    sections = {
        "facts": shown_facts,
        "metrics": payload.get("metrics") or {},
        "caveats": caveats,
        "round_trips": shown_trips,
        "headline": payload.get("headline_zh") or "（无）",
    }
    # First pass: strings under _TEXT_KEYS; second pass: any string quoting one of them.
    fenced = fence(sections, keys=_TEXT_KEYS, limit=_TEXT_LIMIT, markup=False)
    return {**fenced, "trips_note": note}


def build_prompt(payload: Dict[str, Any]) -> str:
    sections = prompt_sections(payload)

    def dumps(value, indent=2):
        return json.dumps(value, ensure_ascii=False, indent=indent, default=str)

    return REFLECTION_PROMPT.format(
        data_note=DATA_NOTE,
        headline=dumps(sections["headline"]),
        facts_json=dumps(sections["facts"]),
        metrics_json=dumps(sections["metrics"]),
        caveats=dumps(sections["caveats"] or "无"),
        trips_note=sections["trips_note"],
        trips_json=dumps(sections["round_trips"], indent=1),
    )


class BacktestReflector:
    """
    回测 LLM 反思器

    生成分析文本、教训和改进建议; 保存由调用方负责 (见模块说明).
    """

    def __init__(
        self,
        llm_provider=None,
        memory_manager=None,
        history_store=None,
        use_llm: bool = True,
    ):
        """
        Args:
            llm_provider: LLM 提供商 (ClaudeProvider 等); 不传则按设置页配置创建
            memory_manager, history_store: 仅为兼容旧调用保留, 不再使用 (反思器不写存储)
            use_llm: False 时只做规则分析 (演示模式: 不读设置、不联网)
        """
        self._llm = llm_provider
        self._use_llm = use_llm

    def _get_llm_provider(self):
        """懒加载 LLM Provider"""
        if not self._use_llm:
            return None
        if self._llm is not None:
            return self._llm
        try:
            from config.settings import get_config
            from core.llm.base import LLMConfig
            from providers.llm import LLMProviderType, create_llm_provider

            # 与对话服务一致: 使用设置页当前选中的 API 组和模型
            config = get_config()
            api_group = config.get_current_api_group() or {}
            custom_headers = api_group.get("headers")
            if not llm_group_is_configured(api_group):
                logger.info("[Reflector] 未配置 LLM, 使用规则分析")
                return None

            model_id = config.get_current_model()
            base_url = api_group.get("base_url") or ""
            explicit_type = api_group.get("provider_type", "")
            if explicit_type in ("claude", "anthropic"):
                provider_type = LLMProviderType.CLAUDE
            elif explicit_type == "openai":
                provider_type = LLMProviderType.OPENAI
            elif "anthropic" in base_url.lower() or "claude" in model_id.lower():
                provider_type = LLMProviderType.CLAUDE
            else:
                provider_type = LLMProviderType.OPENAI

            llm_config = LLMConfig(
                api_key=api_group.get("api_key", ""),
                base_url=base_url or None,
                model=model_id,
                temperature=config.llm_config.get("temperature", 0.7),
                timeout=config.llm_config.get("timeout", 120),
            )
            self._llm = create_llm_provider(provider_type, llm_config, custom_headers)
            return self._llm
        except Exception as e:
            logger.warning("[Reflector] 加载 LLM Provider 失败 (%s), 使用规则分析", safe_error_summary(e))
            return None

    async def reflect(self, result) -> Dict[str, Any]:
        """
        对回测结果进行反思 ("AI 解读").

        Args:
            result: BacktestResult 对象 (转换成 facts 后交给 reflect_facts)
        Returns: 见 reflect_facts.
        不写任何存储; 由调用方 (回测 API) 随回测记录保存.
        """
        return await self.reflect_facts(facts_from_result(result))

    async def reflect_facts(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        "AI 解读" of a finished run, from its facts only.

        Args:
            payload: {"run_id", "headline_zh", "facts", "metrics", "caveats", "round_trips"}
                     (service.RunResult has these keys)
        Returns:
            {
              "analysis": str,          # 解读文本
              "source": str,            # "llm" 或 "rules" (未配置/调用失败时的规则分析)
              "lessons": List[str],     # 从数字得出的事实性要点
              "improvements": List[str],
              "round_trips_seen": int,  # 提示词里给出的回合数
            }
        """
        prompt = build_prompt(payload)
        trips, _ = _prompt_trips(list(payload.get("round_trips") or []))

        llm = self._get_llm_provider()
        analysis_text = ""

        if llm is not None:
            try:
                logger.info("[Reflector] 调用 LLM 进行回测反思...")
                response = await llm.chat(
                    messages=[{"role": "user", "content": prompt}],
                    stream=False,
                )
                # LLMProvider.chat 返回 LLMResponse, 其 content 是字符串
                content = response.content if hasattr(response, "content") else response
                analysis_text = content.strip() if isinstance(content, str) else ""
                if not analysis_text:
                    logger.warning("[Reflector] LLM 未返回文本, 使用规则分析")
            except Exception as e:
                # Never log or return the provider's message: it can contain the key.
                logger.error("[Reflector] LLM 调用失败 (%s), 使用规则分析", safe_error_summary(e))
                analysis_text = ""

        source = "llm" if analysis_text else "rules"
        if not analysis_text:
            analysis_text = self._rule_based_analysis(payload)

        # 提取要点和 (LLM 自己写出的) 建议
        lessons = self._extract_lessons(analysis_text, {**(payload.get("facts") or {}),
                                                        **(payload.get("metrics") or {})})
        improvements = self._extract_improvements(analysis_text)

        return {
            "title": "AI 解读",
            "analysis": analysis_text,
            "source": source,
            "lessons": lessons,
            "improvements": improvements,
            "round_trips_seen": len(trips),
            "run_id": payload.get("run_id"),
            "timestamp": datetime.now().isoformat(),
        }

    def reflect_sync(self, result) -> Dict[str, Any]:
        """同步版本的 reflect (用于非 async 上下文)"""
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    future = pool.submit(asyncio.run, self.reflect(result))
                    return future.result()
            else:
                return loop.run_until_complete(self.reflect(result))
        except Exception as e:
            logger.error("[Reflector] reflect_sync 失败 (%s)", safe_error_summary(e))
            return {
                "title": "AI 解读",
                "analysis": self._generate_rule_based_analysis(result),
                "source": "rules",
                "lessons": [],
                "improvements": [],
                "run_id": result.run_id,
                "timestamp": datetime.now().isoformat(),
            }

    # ------------------------------------------------------------------ #
    #  辅助方法
    # ------------------------------------------------------------------ #

    def _generate_rule_based_analysis(self, result) -> str:
        """基于规则的分析 (LLM 不可用时的后备), 来自 BacktestResult"""
        return self._rule_based_analysis(facts_from_result(result))

    @staticmethod
    def _rule_based_analysis(payload: Dict[str, Any]) -> str:
        """基于规则的分析 (LLM 不可用时的后备): 只陈述数字, 不给调参建议."""
        facts = payload.get("facts") or {}
        metrics = payload.get("metrics") or {}
        undefined = metrics.get("undefined") or {}

        def number(key):
            """The metric (or fact) as a float; None when it has no value (null, NaN)."""
            value = metrics.get(key, facts.get(key))
            return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) \
                and value == value else None

        name = (facts.get("strategy") or {}).get("name") or "回测"
        lines = [f"## 策略分析: {name}\n", "*规则分析 (未使用 LLM)*\n"]
        if payload.get("headline_zh"):
            lines.append(payload["headline_zh"] + "\n")

        annual, sharpe = number("annualized_return"), number("sharpe_ratio")
        missing = [(key, label) for key, label in (("annualized_return", "年化收益"), ("sharpe_ratio", "夏普比率"))
                   if number(key) is None]
        if missing:
            # A statistic without a value is not rated as if it were 0.
            reasons = "；".join(f"{label}没有数值" + (f"（{undefined[key]}）" if undefined.get(key) else "")
                               for key, label in missing)
            lines.append(f"**整体评级**: 不可用 —— {reasons}，不按收益和风险评级")
        elif annual > 0.15 and sharpe > 1.0:
            lines.append("**整体评级**: ⭐⭐⭐⭐ 优秀")
        elif annual > 0.08 and sharpe > 0.5:
            lines.append("**整体评级**: ⭐⭐⭐ 良好")
        elif annual > 0:
            lines.append("**整体评级**: ⭐⭐ 一般")
        else:
            lines.append("**整体评级**: ⭐ 较差")

        issues = []
        drawdown = number("max_drawdown")
        closed = int(number("closed_trades") or 0)
        win_rate = number("win_rate")
        if drawdown is not None and drawdown < -0.20:
            issues.append(f"最大回撤 {drawdown:.1%}：用这个方法持有，要能承受这样的下跌")
        if closed and win_rate is not None and win_rate < 0.40:
            issues.append(f"完整交易的胜率 {win_rate:.1%}，多数交易是亏的")
        if closed < 10:
            issues.append(f"只有 {closed} 笔完整交易，交易统计的偶然性很大")
        if closed > 200:
            issues.append(f"完整交易 {closed} 笔，交易频繁，费用和滑点占收益的比重大")
        if sharpe is not None and sharpe < 0:
            issues.append("夏普比率为负：扣除无风险收益后，承担的波动没有换来回报")
        same = facts.get("same_stock_return")
        total = number("total_return")
        if isinstance(same, (int, float)) and same == same and total is not None and total < same:
            issues.append(f"总收益 {total:.1%} 低于同期一直持有的 {same:.1%}")

        if issues:
            lines.append("\n**主要事实**:")
            for i in issues:
                lines.append(f"- {i}")
        lines.append("\n这些结论来自同一段历史，没有在别的时间段检验过；不要据此在同一段历史上调参。")

        return "\n".join(lines)

    def _extract_lessons(
        self, analysis: str, stats: dict
    ) -> List[str]:
        """从统计数据得出事实性要点 (不含调参建议)"""
        lessons = []

        def number(key):
            value = stats.get(key)
            return float(value) if isinstance(value, (int, float)) and value == value else None

        drawdown, win_rate = number("max_drawdown"), number("win_rate")
        total, factor = number("total_return"), number("profit_factor")
        if drawdown is not None and drawdown < -0.20:
            lessons.append(f"最大回撤达 {drawdown:.1%}，持有期间要能承受这样的下跌")
        if win_rate is not None and win_rate < 0.35 and (number("closed_trades") or 0) > 0:
            lessons.append(f"完整交易的胜率只有 {win_rate:.1%}")
        if total is not None and total < 0:
            lessons.append(f"这段历史上总收益为负 ({total:.1%})")
        if factor is not None and factor < 1.0:
            lessons.append("利润因子小于 1：全部亏损多于全部盈利")

        return lessons

    def _extract_improvements(self, analysis: str) -> List[str]:
        """从 LLM 分析中提取改进建议"""
        improvements = []
        lines = analysis.split("\n")
        in_improvements = False

        for line in lines:
            line = line.strip()
            if "改进" in line or "建议" in line or "优化" in line:
                in_improvements = True
            if in_improvements and line.startswith(("- ", "* ", "•", "1.", "2.", "3.", "4.", "5.")):
                clean = line.lstrip("-*•0123456789. ")
                if clean:
                    improvements.append(clean)

        return improvements[:5]  # 最多5条
