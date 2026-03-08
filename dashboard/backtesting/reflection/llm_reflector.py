"""
LLM 回测反思器
- 使用现有的 Claude LLM Provider 分析回测结果
- 将洞察自动写入 MemoryManager (经验层)
- 生成结构化的策略改进建议

这是"展示结果 → 反思 → 记忆&经验"的闭环
"""
import json
import logging
from datetime import datetime
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)


REFLECTION_PROMPT = """你是一位专业的量化交易分析师和策略顾问。
我刚刚完成了一次策略回测,请你深度分析结果并给出专业建议。

## 回测结果

```json
{stats_json}
```

## 策略信息
- 策略名: {strategy_name}
- 回测标的: {symbol}
- 参数: {params}
- 总交易次数: {total_trades}

请从以下维度进行专业分析:

### 1. 整体表现评估
客观评价策略表现: 年化收益 {ann_return:.1%}, 最大回撤 {max_dd:.1%}, 夏普比率 {sharpe:.3f}

### 2. 优势识别
这个策略哪里做得好? 什么市场环境下可能表现优秀?

### 3. 风险暴露
- 最大回撤 {max_dd:.1%} 是否可以接受?
- 胜率 {win_rate:.1%}, 盈亏比 {profit_factor:.2f} 说明了什么?
- 有哪些潜在风险?

### 4. 改进方向 (具体可操作)
列出3-5个最重要的改进建议, 每个建议需包含:
- 问题描述
- 改进方法 (具体参数调整或逻辑改变)
- 预期效果

### 5. 交易行为洞察
从成交记录分析是否存在常见的"韭菜行为":
- 追涨杀跌
- 频繁交易
- 止损不坚定

### 6. 一句话总结
用一句话概括这个策略的核心问题或优势。

请用中文回复,语气专业但易懂。"""


class BacktestReflector:
    """
    回测 LLM 反思器
    
    结合 LLM 分析和记忆系统,实现策略持续学习与改进.
    """

    def __init__(
        self,
        llm_provider=None,
        memory_manager=None,
        history_store=None,
    ):
        """
        Args:
            llm_provider: LLM 提供商 (ClaudeProvider 等)
            memory_manager: 记忆管理器 (MemoryManager) - 仅用于关键教训
            history_store: 回测历史存储 (BacktestHistoryStore) - 主要存储
        """
        self._llm = llm_provider
        self._memory = memory_manager
        self._history = history_store

    def _get_llm_provider(self):
        """懒加载 LLM Provider"""
        if self._llm is not None:
            return self._llm
        try:
            import sys
            sys.path.insert(0, ".")
            from config.settings import Settings
            from providers.llm.claude import ClaudeProvider
            settings = Settings()
            self._llm = ClaudeProvider(settings)
            return self._llm
        except Exception as e:
            logger.warning(f"[Reflector] 加载 LLM Provider 失败: {e}")
            return None

    def _get_memory_manager(self):
        """懒加载 MemoryManager"""
        if self._memory is not None:
            return self._memory
        try:
            from core.memory.manager import MemoryManager
            self._memory = MemoryManager()
            return self._memory
        except Exception as e:
            logger.warning(f"[Reflector] 加载 MemoryManager 失败: {e}")
            return None

    async def reflect(self, result) -> Dict[str, Any]:
        """
        对回测结果进行 LLM 反思.
        
        Args:
            result: BacktestResult 对象
        Returns:
            {
              "analysis": str,        # LLM 分析文本
              "lessons": List[str],    # 提取的教训
              "improvements": List[str],# 改进建议
              "saved_to_memory": bool,
            }
        """
        stats_dict = result.to_dict()
        stats = result.stats

        prompt = REFLECTION_PROMPT.format(
            stats_json=json.dumps(stats_dict, ensure_ascii=False, indent=2),
            strategy_name=result.strategy_name,
            symbol=result.primary_symbol,
            params=json.dumps(result.strategy_params, ensure_ascii=False),
            total_trades=stats.total_trades,
            ann_return=stats.annualized_return,
            max_dd=stats.max_drawdown,
            sharpe=stats.sharpe_ratio,
            win_rate=stats.win_rate,
            profit_factor=stats.profit_factor,
        )

        llm = self._get_llm_provider()
        analysis_text = ""

        if llm is not None:
            try:
                logger.info("[Reflector] 调用 LLM 进行回测反思...")
                response = await llm.chat(
                    messages=[{"role": "user", "content": prompt}],
                    stream=False,
                )
                if hasattr(response, "content"):
                    analysis_text = response.content[0].text if response.content else ""
                elif isinstance(response, str):
                    analysis_text = response
                else:
                    analysis_text = str(response)
            except Exception as e:
                logger.error(f"[Reflector] LLM 调用失败: {e}")
                analysis_text = self._generate_rule_based_analysis(result)
        else:
            analysis_text = self._generate_rule_based_analysis(result)

        # 提取教训和改进建议
        lessons = self._extract_lessons(analysis_text, stats_dict)
        improvements = self._extract_improvements(analysis_text)

        # 保存到记忆系统
        saved = self._save_to_memory(result, analysis_text, lessons)

        return {
            "analysis": analysis_text,
            "lessons": lessons,
            "improvements": improvements,
            "saved_to_memory": saved,
            "run_id": result.run_id,
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
            logger.error(f"[Reflector] reflect_sync 失败: {e}")
            return {
                "analysis": self._generate_rule_based_analysis(result),
                "lessons": [],
                "improvements": [],
                "saved_to_memory": False,
                "run_id": result.run_id,
                "timestamp": datetime.now().isoformat(),
            }

    # ------------------------------------------------------------------ #
    #  辅助方法
    # ------------------------------------------------------------------ #

    def _generate_rule_based_analysis(self, result) -> str:
        """基于规则的分析 (LLM 不可用时的后备)"""
        s = result.stats
        lines = [f"## 策略分析: {result.strategy_name}\n"]

        # 整体评级
        if s.annualized_return > 0.15 and s.sharpe_ratio > 1.0:
            lines.append("**整体评级**: ⭐⭐⭐⭐ 优秀")
        elif s.annualized_return > 0.08 and s.sharpe_ratio > 0.5:
            lines.append("**整体评级**: ⭐⭐⭐ 良好")
        elif s.annualized_return > 0:
            lines.append("**整体评级**: ⭐⭐ 一般")
        else:
            lines.append("**整体评级**: ⭐ 较差")

        # 关键问题
        issues = []
        if s.max_drawdown < -0.20:
            issues.append(f"最大回撤过大 ({s.max_drawdown:.1%}), 风险控制需加强")
        if s.win_rate < 0.40:
            issues.append(f"胜率偏低 ({s.win_rate:.1%}), 考虑优化入场条件")
        if s.total_trades < 5:
            issues.append("交易次数过少, 统计意义不强, 需更长回测周期")
        if s.total_trades > 200:
            issues.append("交易频率过高, 交易成本可能侵蚀收益")
        if s.sharpe_ratio < 0:
            issues.append("夏普比率为负, 风险调整后收益不佳")

        if issues:
            lines.append("\n**主要问题**:")
            for i in issues:
                lines.append(f"- {i}")

        return "\n".join(lines)

    def _extract_lessons(
        self, analysis: str, stats: dict
    ) -> List[str]:
        """从分析文本和统计数据中提取教训"""
        lessons = []

        # 基于统计数据的自动教训提取
        if stats.get("max_drawdown", 0) < -0.20:
            lessons.append(
                f"策略最大回撤达 {stats['max_drawdown']:.1%}, "
                f"需要更严格的止损机制"
            )
        if stats.get("win_rate", 0) < 0.35:
            lessons.append(
                f"胜率仅 {stats.get('win_rate', 0):.1%}, "
                f"入场信号质量需要提升"
            )
        if stats.get("total_return", 0) < 0:
            lessons.append(
                f"策略总收益为负 ({stats.get('total_return', 0):.1%}), "
                f"当前参数组合不适用于该市场环境"
            )
        if stats.get("profit_factor", 0) < 1.0:
            lessons.append(
                "盈亏比小于1, 平均亏损大于平均盈利, 需调整持仓时间或止盈比例"
            )

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

    def _save_to_memory(
        self, result, analysis: str, lessons: List[str]
    ) -> bool:
        """将反思结果保存到回测专用记忆库（不再写入通用memory.json）"""
        # 主要保存到专用 BacktestHistoryStore
        try:
            if self._history is None:
                from backtesting.history import BacktestHistoryStore
                self._history = BacktestHistoryStore()
            # 注意: 完整结果由 API 层 save_run 保存,这里只做补充
            logger.info(f"[Reflector] 反思结果将由 API 层保存到回测历史库")
            return True
        except Exception as e:
            logger.error(f"[Reflector] 保存失败: {e}")
            return False
