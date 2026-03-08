"""
Agent 服务
整合 LLM、持仓、记忆等功能
"""
import sys
from pathlib import Path
from typing import Optional, Dict, Any, List, AsyncIterator
from datetime import datetime

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from config import get_config
from core.llm import LLMConfig
from core.models import AgentContext, Market
from core.memory import MemoryManager, MemoryExtractor, LLMMemoryExtractor
from providers.llm import create_llm_provider, LLMProviderType
from providers.market_data import GoogleFinanceProvider, AKShareProvider, MultiSourceProvider
from providers.portfolio.manual import ManualPortfolioProvider
from agents.trader import TraderAgent


class AgentService:
    """
    Agent 服务

    功能:
    1. 管理 LLM Provider
    2. 管理 Portfolio Provider
    3. 管理 Memory Manager
    4. 提供对话接口
    5. 提取结构化数据
    """

    def __init__(self):
        self.config = None
        self.llm_provider = None
        self.portfolio_provider = None
        self.market_provider = None
        self.memory_manager = None
        self.agent = None
        self.memory_extractor = None
        self.llm_extractor = None

        # 最近的建议和风险
        self._recent_suggestions: List[Dict] = []
        self._recent_risks: List[Dict] = []


    async def initialize(self):
        """初始化所有服务"""
        # 加载配置
        self.config = get_config()

        # 初始化 LLM Provider
        await self._init_llm()

        # 初始化市场数据 Provider
        self._init_market_provider()

        # 初始化持仓 Provider
        self._init_portfolio_provider()

        # 初始化记忆管理器
        self._init_memory_manager()

        # 初始化 Agent
        self._init_agent()

        # 初始化提取器
        self._init_extractors()

    async def reload_llm(self):
        """重新加载 LLM 配置（热重载，无需重启服务）"""
        print("[LLM热重载] 开始重新加载LLM配置...")
        
        # 重新加载配置文件（从磁盘重新读取）
        self.config = get_config()
        self.config.reload_from_disk()
        
        # 重新初始化 LLM Provider
        await self._init_llm()
        
        # 重新初始化 Agent（使用新的 LLM Provider）
        self._init_agent()
        
        # 重新初始化提取器
        self._init_extractors()
        
        print(f"[LLM热重载] 完成！当前API组: {self.config.get('current_api_group')}, 模型: {self.config.get_current_model()}")

    async def _init_llm(self):
        """初始化 LLM"""
        api_group = self.config.get_current_api_group()
        model_id = self.config.llm_config.get("current_model", "kimi-k2.5")

        llm_config = LLMConfig(
            api_key=api_group["api_key"],
            base_url=api_group["base_url"],
            model=model_id,
            temperature=self.config.llm_config.get("temperature", 0.7)
        )

        # 根据 API 组配置、base_url 或模型名称确定 provider 类型
        explicit_type = api_group.get("provider_type", "")
        base_url = api_group.get("base_url", "")

        if explicit_type == "claude" or explicit_type == "anthropic":
            provider_type = LLMProviderType.CLAUDE
        elif explicit_type == "openai":
            provider_type = LLMProviderType.OPENAI
        elif "anthropic" in base_url.lower():
            # DashScope 等 Anthropic 兼容接口
            provider_type = LLMProviderType.CLAUDE
        elif "claude" in model_id.lower():
            provider_type = LLMProviderType.CLAUDE
        else:
            provider_type = LLMProviderType.OPENAI

        custom_headers = api_group.get("headers")
        self.llm_provider = create_llm_provider(provider_type, llm_config, custom_headers)

    def _init_market_provider(self):
        """初始化市场数据 Provider"""
        # 使用 MultiSource Provider
        # 优先使用 Google Finance（Yahoo），失败则fallback到AKShare
        google_provider = GoogleFinanceProvider()
        akshare_provider = AKShareProvider()

        self.market_provider = MultiSourceProvider([
            google_provider,
            akshare_provider
        ])

    def _init_portfolio_provider(self):
        """初始化持仓 Provider"""
        self.portfolio_provider = ManualPortfolioProvider(
            data_file="data/portfolio.json",
            market_provider=self.market_provider
        )

    def _init_memory_manager(self):
        """初始化记忆管理器"""
        self.memory_manager = MemoryManager(data_file="data/memory.json")

    def _init_agent(self):
        """初始化 Agent"""
        # 创建工具
        from core.tools import (
            get_stock_quote_tool,
            get_portfolio_tool,
            get_market_indices_tool,
            compare_stocks_tool,
            get_tracker_snapshot_tool,
            update_holdings_tool,
            run_portfolio_pipeline_tool,
            ToolExecutor
        )

        tools = [
            get_stock_quote_tool(self.market_provider),
            get_portfolio_tool(self.portfolio_provider),
            get_market_indices_tool(self.market_provider),
            compare_stocks_tool(self.market_provider),
            get_tracker_snapshot_tool(),
            update_holdings_tool(),
            run_portfolio_pipeline_tool(),
        ]

        tool_executor = ToolExecutor(tools)

        # 创建 Agent（带工具支持）
        self.agent = TraderAgent(self.llm_provider, tool_executor=tool_executor)
        self.agent.start_conversation()

    def _init_extractors(self):
        """初始化提取器"""
        self.memory_extractor = MemoryExtractor()
        self.llm_extractor = LLMMemoryExtractor(self.llm_provider)

    # ==================== 对话接口 ====================

    async def chat(
        self,
        message: str,
        stream: bool = False
    ) -> AsyncIterator[str]:
        """
        与 Agent 对话

        Args:
            message: 用户消息
            stream: 是否流式返回

        Yields:
            AI 回复的文本块
        """
        # 构建上下文
        context = await self._build_context()

        # 从用户消息中提取记忆
        user_memories = self.memory_extractor.extract_from_user_message(message)
        for mem in user_memories:
            self._apply_memory(mem)

        # 调用 Agent
        full_response = ""
        async for chunk in self.agent.chat(message, context, stream=stream):
            full_response += chunk
            yield chunk

        # 从 AI 回复中提取结构化数据
        await self._extract_from_response(message, full_response)

    async def chat_with_extraction(
        self,
        message: str
    ) -> Dict[str, Any]:
        """
        对话并返回结构化数据

        Returns:
            {
                "response": "AI 回复文本",
                "suggestions": [...],
                "risks": [...],
                "sentiment": "...",
                "memory_updates": [...]
            }
        """
        # 构建上下文
        context = await self._build_context()

        # 从用户消息中提取记忆
        user_memories = self.memory_extractor.extract_from_user_message(message)
        for mem in user_memories:
            self._apply_memory(mem)

        # 调用 Agent
        full_response = ""
        async for chunk in self.agent.chat(message, context, stream=False):
            full_response += chunk

        # 使用 LLM 提取结构化数据
        extraction = await self.llm_extractor.extract(message, full_response)

        # 应用记忆更新
        for update in extraction.get("memory_updates", []):
            self._apply_memory_update(update)

        # 从用户画像提取中更新结构化数据
        user_profile = extraction.get("user_profile", {})
        if user_profile:
            self._apply_profile_update(user_profile)

        # 更新最近的建议和风险
        self._recent_suggestions = extraction.get("suggestions", [])
        self._recent_risks = extraction.get("risks", [])

        return {
            "response": full_response,
            "suggestions": self._recent_suggestions,
            "risks": self._recent_risks,
            "sentiment": extraction.get("sentiment", "neutral"),
            "memory_updates": extraction.get("memory_updates", [])
        }

    async def chat_with_images(
        self,
        message: str,
        images: list[Dict[str, str]],  # List of {"data": "base64", "type": "image/png"}
        extract_data: bool = True
    ) -> Dict[str, Any]:
        """
        带图片的对话

        Args:
            message: 用户消息
            images: 图片数据列表
            extract_data: 是否提取结构化数据

        Returns:
            对话结果
        """
        # 构建上下文
        context = await self._build_context()

        # 构建带图片的消息内容
        content = []
        for img in images:
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": img.get("type", "image/png"),
                    "data": img.get("data", "")
                }
            })
        
        content.append({
            "type": "text",
            "text": message if message else "请分析这些图片中的持仓信息"
        })

        image_message = {
            "role": "user",
            "content": content
        }

        # 构建系统提示
        from agents.trader.prompts import build_system_prompt
        context_str = context.to_context_string() if context else ""
        system_prompt = build_system_prompt(context_str)

        # 直接调用 LLM（绕过 Agent 以支持图片）
        messages = [
            {"role": "system", "content": system_prompt},
            image_message
        ]

        try:
            response = await self.llm_provider.chat(messages)
            full_response = response.content

            # 保存到对话历史
            from core.models import MessageRole
            self.agent.conversation.add_message(
                MessageRole.USER,
                f"{message} [附带 {len(images)} 张图片]"
            )
            self.agent.conversation.add_message(
                MessageRole.ASSISTANT,
                full_response
            )

            if extract_data:
                # 使用 LLM 提取结构化数据
                extraction = await self.llm_extractor.extract(message, full_response)

                # 调试：打印提取结果
                print(f"[提取调试] 提取到的数据: {extraction}")

                # 应用记忆更新
                for update in extraction.get("memory_updates", []):
                    self._apply_memory_update(update)

                # 从用户画像提取中更新结构化数据
                user_profile = extraction.get("user_profile", {})
                if user_profile:
                    self._apply_profile_update(user_profile)

                # 导入提取的持仓信息
                positions = extraction.get("positions", [])
                print(f"[持仓导入] 提取到 {len(positions)} 个持仓")
                imported_count = 0
                for pos in positions:
                    try:
                        print(f"[持仓导入] 正在导入: {pos}")
                        await self._import_position(pos)
                        imported_count += 1
                    except Exception as e:
                        print(f"[持仓导入] 导入失败: {pos}, 错误: {e}")
                        import traceback
                        traceback.print_exc()

                if imported_count > 0:
                    print(f"[持仓导入] 成功导入 {imported_count} 个持仓")

                # 导入现金余额
                cash = extraction.get("cash")
                if cash is not None and cash > 0:
                    try:
                        self.portfolio_provider.set_cash(cash)
                        print(f"[现金导入] 成功设置现金余额: ¥{cash:,.2f}")
                    except Exception as e:
                        print(f"[现金导入] 设置失败: {e}")

                # 更新最近的建议和风险
                self._recent_suggestions = extraction.get("suggestions", [])
                self._recent_risks = extraction.get("risks", [])

                return {
                    "response": full_response,
                    "suggestions": self._recent_suggestions,
                    "risks": self._recent_risks,
                    "sentiment": extraction.get("sentiment", "neutral"),
                    "memory_updates": extraction.get("memory_updates", []),
                    "imported_positions": imported_count,
                    "cash_updated": cash is not None
                }
            else:
                return {"response": full_response}

        except Exception as e:
            print(f"图片对话失败: {e}")
            import traceback
            traceback.print_exc()
            return {
                "response": f"抱歉，处理图片时出错: {str(e)}",
                "suggestions": [],
                "risks": [],
                "sentiment": "neutral",
                "memory_updates": []
            }

    async def _build_context(self) -> AgentContext:
        """构建 Agent 上下文"""
        # 获取持仓信息
        portfolio = await self.portfolio_provider.get_portfolio()
        portfolio_summary = portfolio.to_summary()

        # 获取用户记忆
        memory_context = self.memory_manager.get_context_string()

        # 组合上下文
        combined_context = ""
        if memory_context:
            combined_context += memory_context + "\n\n"
        combined_context += portfolio_summary

        # 确定市场阶段
        session_phase = self._get_session_phase()

        context = AgentContext(
            conversation=self.agent.conversation,
            portfolio_summary=combined_context,
            current_time=datetime.now(),
            session_phase=session_phase
        )

        return context

    def _get_session_phase(self) -> str:
        """获取当前市场阶段"""
        now = datetime.now()
        hour, minute = now.hour, now.minute
        time_value = hour * 60 + minute

        # A股交易时间
        if time_value < 9 * 60 + 15:
            return "盘前"
        elif time_value < 9 * 60 + 30:
            return "集合竞价"
        elif time_value < 11 * 60 + 30:
            return "盘中交易"
        elif time_value < 13 * 60:
            return "午间休市"
        elif time_value < 15 * 60:
            return "盘中交易"
        else:
            return "盘后"

    def _apply_memory(self, memory):
        """应用提取的记忆"""
        if memory.category == "profile":
            if memory.key == "experience":
                self.memory_manager.update_profile(experience_level=memory.value)
            elif memory.key == "style":
                self.memory_manager.update_profile(trading_style=memory.value)
            elif memory.key == "risk":
                self.memory_manager.update_profile(risk_tolerance=memory.value)
            elif memory.key == "preferred_sector":
                self.memory_manager.add_preferred_sector(memory.value)
        elif memory.category == "preference":
            if memory.key == "emotional_trigger":
                self.memory_manager.add_emotional_trigger(memory.value)

    def _apply_memory_update(self, update: Dict[str, Any]):
        """应用从 LLM 提取的记忆更新"""
        content = update.get("content", "")
        category = update.get("category", "profile")
        confidence = update.get("confidence", 0.8)

        if not content:
            return

        # 添加到通用记忆条目
        self.memory_manager.add_memory_entry(
            content=content,
            category=category,
            source="extracted",
            confidence=confidence
        )

        # 根据类别尝试更新结构化数据
        if category == "preference" and "情绪" in content:
            self.memory_manager.add_emotional_trigger(content)
        elif category == "history" and ("教训" in content or "失败" in content or "亏" in content):
            self.memory_manager.add_lesson(
                description=content,
                lesson_type="failure",
                lesson=content
            )
        elif category == "history" and ("成功" in content or "赚" in content):
            self.memory_manager.add_lesson(
                description=content,
                lesson_type="success",
                lesson=content
            )

    async def _import_position(self, pos: Dict[str, Any]):
        """导入单个持仓"""
        symbol = pos.get("symbol", "")
        name = pos.get("name", "")
        market = pos.get("market", "a_share")
        quantity = pos.get("quantity", 0)
        cost_price = pos.get("cost_price", 0)

        if not symbol or not name:
            return

        # 规范化股票代码
        symbol = str(symbol).strip().upper()

        # 处理不同格式的代码
        if symbol.startswith("SHA:"):
            symbol = symbol[4:]
            market = "a_share"
        elif symbol.startswith("SHE:"):
            symbol = symbol[4:]
            market = "a_share"
        elif symbol.startswith("HKG:"):
            symbol = symbol[4:]
            market = "hk_stock"
        elif symbol.startswith("NASDAQ:") or symbol.startswith("NYSE:"):
            symbol = symbol.split(":")[1]
            market = "us_stock"
            
        # 兼容处理
        if market == "hk":
            market = "hk_stock"
        elif market == "us":
            market = "us_stock"

        # 确保数量和价格为有效数字
        try:
            quantity = int(quantity) if quantity else 0
            cost_price = float(cost_price) if cost_price else 0
        except (ValueError, TypeError):
            quantity = 0
            cost_price = 0

        if quantity <= 0:
            return

        # 添加到持仓
        self.portfolio_provider.add_position(
            symbol=symbol,
            name=name,
            quantity=quantity,
            cost_price=cost_price,
            market=Market(market)
        )
        print(f"[持仓导入] {name}({symbol}) 数量:{quantity} 成本:{cost_price}")

    def _apply_profile_update(self, user_profile: Dict[str, Any]):
        """应用从 LLM 提取的用户画像更新"""
        updates = {}

        # 经验等级
        exp_level = user_profile.get("experience_level")
        if exp_level and exp_level != "null":
            updates["experience_level"] = exp_level

        # 交易风格
        style = user_profile.get("trading_style")
        if style and style != "null":
            updates["trading_style"] = style

        # 风险偏好
        risk = user_profile.get("risk_tolerance")
        if risk and risk != "null":
            updates["risk_tolerance"] = risk

        # 备注
        notes = user_profile.get("notes")
        if notes and notes != "null" and notes.strip():
            # 追加到现有备注
            current_notes = self.memory_manager.memory.profile.notes or ""
            if notes not in current_notes:
                new_notes = f"{current_notes}\n{notes}".strip() if current_notes else notes
                updates["notes"] = new_notes

        # 应用更新
        if updates:
            try:
                self.memory_manager.update_profile(**updates)
                print(f"[记忆更新] 自动更新用户画像: {updates}")
            except Exception as e:
                print(f"[记忆更新] 更新失败: {e}")

        # 偏好板块
        sectors = user_profile.get("preferred_sectors", [])
        for sector in sectors:
            if sector:
                self.memory_manager.add_preferred_sector(sector)
                print(f"[记忆更新] 添加偏好板块: {sector}")

    async def _extract_from_response(self, user_message: str, ai_response: str):
        """从回复中提取信息"""
        result = self.memory_extractor.extract_from_ai_response(ai_response)

        # 更新建议和风险
        self._recent_suggestions = [
            {
                "type": s.type,
                "symbol": s.symbol,
                "reason": s.reason,
                "target_price": s.target_price,
                "stop_loss": s.stop_loss,
                "position_size": s.position_size,
                "confidence": s.confidence
            }
            for s in result.suggestions
        ]

        self._recent_risks = [
            {
                "level": r.level,
                "type": r.type,
                "description": r.description,
                "suggestion": r.suggestion
            }
            for r in result.risks
        ]

    # ==================== 建议和风险 ====================

    def get_recent_suggestions(self) -> List[Dict]:
        """获取最近的建议"""
        return self._recent_suggestions

    def get_recent_risks(self) -> List[Dict]:
        """获取最近的风险"""
        return self._recent_risks

    # ==================== 持仓接口 ====================

    async def get_portfolio(self) -> Dict[str, Any]:
        """获取持仓"""
        portfolio = await self.portfolio_provider.get_portfolio()
        return {
            "positions": [
                {
                    "symbol": p.stock.symbol,
                    "name": p.stock.name,
                    "market": p.stock.market.value,
                    "quantity": p.quantity,
                    "available_qty": p.available_qty,
                    "cost_price": p.cost_price,
                    "current_price": p.current_price,
                    "profit": p.profit,
                    "profit_pct": p.profit_pct,
                    "market_value": p.market_value
                }
                for p in portfolio.positions
            ],
            "cash": portfolio.cash,
            "total_market_value": portfolio.total_market_value,
            "total_assets": portfolio.total_assets,
            "total_profit": portfolio.total_profit
        }

    async def add_position(
        self,
        symbol: str,
        name: str,
        quantity: int,
        cost_price: float,
        market: str = "a_share"
    ):
        """添加持仓"""
        self.portfolio_provider.add_position(
            symbol=symbol,
            name=name,
            quantity=quantity,
            cost_price=cost_price,
            market=Market(market)
        )

    async def remove_position(self, symbol: str):
        """删除持仓"""
        self.portfolio_provider.remove_position(symbol)

    async def update_position(self, symbol: str, quantity: int = None, cost_price: float = None):
        """更新持仓"""
        self.portfolio_provider.update_position(symbol, quantity=quantity, cost_price=cost_price)

    async def refresh_portfolio(self):
        """刷新持仓价格"""
        await self.portfolio_provider.refresh()

    # ==================== 行情接口 ====================

    async def get_quote(self, symbol: str, market: str = "a_share") -> Optional[Dict]:
        """获取股票行情"""
        print(f"[Service] 获取行情: symbol={symbol}, market={market}")
        quote = await self.market_provider.get_quote(symbol, Market(market))
        if quote:
            print(f"[Service] 成功获取: {quote.stock.name} - {quote.price}")
            return {
                "symbol": quote.stock.symbol,
                "name": quote.stock.name,
                "price": quote.price,
                "open": quote.open,
                "high": quote.high,
                "low": quote.low,
                "prev_close": quote.prev_close,
                "volume": quote.volume,
                "change": quote.price - quote.prev_close if quote.prev_close else 0,
                "change_pct": (quote.price - quote.prev_close) / quote.prev_close * 100 if quote.prev_close else 0
            }
        else:
            print(f"[Service] 未找到: {symbol}")
        return None

    # ==================== 记忆接口 ====================

    def get_memory(self) -> Dict[str, Any]:
        """获取用户记忆"""
        return self.memory_manager.get_full_memory()

    def update_profile(self, **kwargs):
        """更新用户画像"""
        self.memory_manager.update_profile(**kwargs)

    def add_lesson(self, description: str, lesson_type: str, lesson: str = "", symbol: str = None):
        """添加交易教训"""
        self.memory_manager.add_lesson(
            description=description,
            lesson_type=lesson_type,
            lesson=lesson,
            symbol=symbol
        )
