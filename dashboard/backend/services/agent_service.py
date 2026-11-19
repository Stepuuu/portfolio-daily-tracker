"""
Agent 服务
整合 LLM、持仓、记忆等功能

Memory writes
-------------
The memory file (``data/memory.json``) is shown to the model in every later
conversation. What may reach it without the person's confirmation is
decided by who found the value and by its *shape*, never by its words or by
the category an extractor gave it:

* only the fixed rules on the user's own message (``MemoryExtractor``) write
  at once, and only a member of a closed set of choices defined in code
  (``_PROFILE_CHOICES``, ``MemoryExtractor.SECTORS``,
  ``MemoryExtractor.emotion_choices()``) that the user stated about
  themself: a first-person statement for the profile choices, and never
  from a quotation, a question, a hypothetical, a negation or a sentence
  about someone else (see ``core/memory/extractor.py``). Such a value can
  only be one of those fixed words, so it carries nothing of its own into a
  prompt;
* everything the model extracts is a proposal the person confirms: a
  profile choice or a sector is a ``profile`` proposal (the model may have
  read it from a report about another trader), every free text a ``lesson``
  proposal (``core/lessons.py``), whatever its wording and category.

Every automatic write of this service, and why::

    method (input)                   field                         value must be              written
    -------------------------------  ----------------------------  -------------------------  ----------------------
    _apply_memory                    profile.experience_level      an ExperienceLevel member  at once: a fixed choice
      (fixed rules on the user's     profile.trading_style         a TradingStyle member        the user stated about
      own message,                   profile.risk_tolerance        a RiskTolerance member       themself, found by code
      MemoryExtractor)               profile.preferred_sectors     one of SECTORS               in the user's own
                                     preferences.emotional_        one of emotion_choices()     words, not by the model
                                       triggers                      ("fear:慌")
    _apply_profile_update            experience_level,             the enum member            profile proposal;
      (model extraction,             trading_style, risk_tolerance                              anything else dropped
      ``user_profile``)              preferred_sectors             one of SECTORS, not one    profile proposal;
                                                                   the message rejects        others dropped
                                     notes                         free text                  lesson proposal
    _apply_memory_update             any category                  free text                  lesson proposal
      (model extraction,
      ``memory_updates``)

Nothing else is written automatically: no memory entry, no profile note,
no free-text trigger, sector or pattern. The person's own edits through
``/api/memory`` are theirs and are saved directly.

Demo mode (``TRACKER_DEMO_MODE=1``) writes nothing on any of these paths: the
memory manager refuses every save and the proposal store every proposal, each
with a fixed Chinese text (an extracted update then has ``status:
not_proposed`` and that text as ``error``), whether or not an HTTP middleware
stands in front.
"""
import sys
from pathlib import Path
from typing import Optional, Dict, Any, List, AsyncIterator, Sequence

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from config import get_config
from core.llm import LLMConfig
from core.models import AgentContext, Market, MessageRole, CASH_CURRENCY
from core.memory import MemoryManager, MemoryExtractor, LLMMemoryExtractor
from core.memory.models import ExperienceLevel, RiskTolerance, TradingStyle
from core import lessons  # registers the ``lesson`` proposal kind
from providers.llm import create_llm_provider, LLMProviderType
from providers.market_data import EastmoneyDirectProvider, GoogleFinanceProvider, AKShareProvider, MultiSourceProvider
from providers.news import RSSNewsProvider
from providers.portfolio.manual import ManualPortfolioProvider
from agents.trader import TraderAgent


# The profile fields that are written without confirmation, and the closed set each value must belong to.
_PROFILE_CHOICES = {"experience_level": ExperienceLevel, "trading_style": TradingStyle,
                    "risk_tolerance": RiskTolerance}
# MemoryExtractor's keys for them
_RULE_PROFILE_KEYS = {"experience": "experience_level", "style": "trading_style", "risk": "risk_tolerance"}
# why a model's preferred sector is not proposed: the user's message of the same turn turned it down
SECTOR_REJECTED = "你在这条消息里表示不考虑这个板块，所以没有把它作为偏好板块提出。"


def _choice(field: str, value: Any) -> Optional[str]:
    """``value`` when it is one of ``field``'s fixed choices, else ``None``."""
    choices = _PROFILE_CHOICES.get(field)
    if choices is None or not isinstance(value, str):
        return None
    return value if value in {member.value for member in choices} else None


# Seconds the chat context waits to read the ledger (a commit in progress, reading and replaying).
CONTEXT_LEDGER_SECONDS = 5.0

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
        self.news_provider = None
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

        # 初始化新闻 Provider
        self._init_news_provider()

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
            temperature=self.config.llm_config.get("temperature", 0.7),
            timeout=self.config.llm_config.get("timeout", 120),
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

        model_name = model_id
        for model in api_group.get("models", []):
            if model.get("id") == model_id:
                model_name = model.get("name", model_id)
                break

        provider_label = api_group.get("name", provider_type.value)
        setattr(self.llm_provider, "display_name", f"{provider_label} / {model_name}")

    def _init_market_provider(self):
        """初始化市场数据 Provider"""
        # 优先走东方财富直连单票接口（A股更稳），
        # 再回退到 Yahoo/AKShare。
        eastmoney_provider = EastmoneyDirectProvider()
        google_provider = GoogleFinanceProvider()
        akshare_provider = AKShareProvider()

        self.market_provider = MultiSourceProvider([
            eastmoney_provider,
            google_provider,
            akshare_provider
        ])

    def _init_portfolio_provider(self):
        """初始化持仓 Provider"""
        self.portfolio_provider = ManualPortfolioProvider(
            data_file="data/portfolio.json",
            market_provider=self.market_provider
        )

    def _init_news_provider(self):
        """初始化新闻 Provider"""
        self.news_provider = RSSNewsProvider()

    def _init_memory_manager(self):
        """初始化记忆管理器"""
        self.memory_manager = MemoryManager(data_file="data/memory.json")
        # A confirmed lesson proposal writes through this instance, which owns
        # the memory file's state (core/lessons.py).
        lessons.use_memory_manager(lambda: self.memory_manager)

    def _init_agent(self):
        """初始化 Agent"""
        # 创建工具
        from core.tools import (
            get_stock_quote_tool,
            get_portfolio_tool,
            get_market_indices_tool,
            get_market_news_tool,
            compare_stocks_tool,
            get_tracker_snapshot_tool,
            propose_ledger_tool,
            ToolExecutor
        )

        tools = [
            get_stock_quote_tool(self.market_provider),
            get_portfolio_tool(self.portfolio_provider),
            get_market_indices_tool(self.market_provider),
            get_market_news_tool(self.news_provider),
            compare_stocks_tool(self.market_provider),
            get_tracker_snapshot_tool(),
            propose_ledger_tool(),
        ]
        from core import agent_tools
        tools += agent_tools.build_all(self, exclude=[tool.name for tool in tools])

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
        self._apply_user_message(message)

        # 调用 Agent
        full_response = ""
        async for chunk in self.agent.chat(message, context, stream=stream):
            full_response += chunk
            yield chunk

        # Numbers this turn's data does not contain get a code-built footnote,
        # sent as one more chunk (in streaming, the final one).
        footnote = self._grounding_footnote(full_response, context, message)
        if footnote:
            full_response += footnote
            yield footnote

        # 从 AI 回复中提取结构化数据
        await self._extract_from_response(message, full_response)

    def _grounding_footnote(self, answer: str, context: Optional[AgentContext], user_message: str, *,
                            image: bool = False) -> str:
        """``"\\n\\n" + footnote`` for the numbers of ``answer`` found in no data of this turn, else ``""``.

        ``image``: an image turn (``chat_with_images``). It calls no tools, so
        only the context and the user's text are its data; the footnote says
        that numbers read from the image can only be checked against it (see
        "Image turns" in ``core/agent_tools/grounding.py``). Its reply is not
        stored yet, so the caller stores it with the footnote.

        The data is the value fields of the live context the model saw
        (``context.evidence``: prices, quantities, amounts and percentages the
        system computed, never the context's text, so its instructions,
        codes, ids, dates, counts and stored notes prove nothing) and the
        results of this turn's tool calls; numbers the user typed in this
        conversation are the user's own. The footnote is also added to the
        stored reply, so the history shows what the user saw.
        """
        from core.agent_tools import grounding
        try:
            conversation = getattr(self.agent, "conversation", None)
            messages = list(getattr(conversation, "messages", None) or [])
            user_text = "\n".join([m.content for m in messages
                                   if m.role == MessageRole.USER and isinstance(m.content, str)] + [user_message])
            evidence = list(getattr(context, "evidence", None) or []) if context is not None else []
            tool_results = [] if image else list(getattr(self.agent, "last_tool_results", None) or [])
            missing = grounding.ungrounded_numbers(answer, tool_results, context_numbers=evidence, user_text=user_text)
            note = grounding.footnote(missing, image=image)
        except Exception as exc:   # the check must never cost the user the answer
            print(f"[数字核对] 跳过: {type(exc).__name__}")
            return ""
        if not note:
            return ""
        addition = "\n\n" + note
        if not image and messages and messages[-1].role == MessageRole.ASSISTANT:
            messages[-1].content += addition
        return addition

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
        self._apply_user_message(message)

        # 调用 Agent
        full_response = ""
        async for chunk in self.agent.chat(message, context, stream=False):
            full_response += chunk
        full_response += self._grounding_footnote(full_response, context, message)

        extraction_result = await self.extract_chat_artifacts(message, full_response)
        return {
            "response": full_response,
            **extraction_result,
        }

    async def extract_chat_artifacts(
        self,
        user_message: str,
        ai_response: str
    ) -> Dict[str, Any]:
        """对已生成的回复做结构化提取，不重新生成回答。"""
        extraction = await self.llm_extractor.extract(user_message, ai_response)
        memory_updates = self._apply_extraction(extraction, user_message)

        self._recent_suggestions = extraction.get("suggestions", [])
        self._recent_risks = extraction.get("risks", [])

        return {
            "suggestions": self._recent_suggestions,
            "risks": self._recent_risks,
            "sentiment": extraction.get("sentiment", "neutral"),
            "memory_updates": memory_updates,
            "imported_positions": 0,
        }

    def _apply_extraction(self, extraction: Dict[str, Any], user_message: str = "") -> List[Dict[str, Any]]:
        """Apply a model extraction (see "Memory writes"); return its memory updates, each with its status.

        A free-text profile note is reported as one more update with
        ``source: "user_profile.notes"``. ``user_message`` is the turn's
        message: a sector it rejects is never proposed as a preferred one.
        """
        updates = [update for update in extraction.get("memory_updates") or [] if isinstance(update, dict)]
        for update in updates:
            self._apply_memory_update(update)
        user_profile = extraction.get("user_profile")
        if isinstance(user_profile, dict) and user_profile:
            updates += self._apply_profile_update(user_profile, MemoryExtractor().rejected_sectors(user_message))
        return updates

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

        # 构建系统提示
        from agents.trader import history
        from agents.trader.prompts import build_system_prompt
        context_str = context.to_context_string() if context else ""
        system_prompt = build_system_prompt(context_str)

        # 构建带图片的消息内容: the text is shortened like an oversized chat message (history.shorten)
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

        text = message if message else "请分析这些图片中的持仓信息"
        sent_text, cut = history.shorten(text, history.MAX_CHARS - len(system_prompt))
        shortened_note = history.shortened_note({"total": len(text), "omitted": cut} if cut else None)
        content.append({
            "type": "text",
            "text": sent_text
        })

        image_message = {
            "role": "user",
            "content": content
        }

        # 直接调用 LLM（绕过 Agent 以支持图片）
        messages = [
            {"role": "system", "content": system_prompt},
            image_message
        ]

        try:
            response = await self.llm_provider.chat(messages)
            full_response = response.content or ""
            # The same grounding footnote as a text answer, worded for an image turn.
            full_response += self._grounding_footnote(full_response, context, message, image=True)
            full_response += shortened_note

            # 保存到对话历史（含核对脚注，与用户看到的一致）
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
                # 使用 LLM 提取结构化数据；记忆只按 "Memory writes" 的规则写入或提出方案
                extraction = await self.llm_extractor.extract(message, full_response)
                memory_updates = self._apply_extraction(extraction, message)

                # Image recognition is advisory; balances require an explicit ledger preview.
                positions = extraction.get("positions", [])
                imported_count = 0
                full_response += "\n\n图片识别结果尚未入账。请在交易账本核对账户、日期、现金币种和数量后确认期初余额或交易。"

                # 更新最近的建议和风险
                self._recent_suggestions = extraction.get("suggestions", [])
                self._recent_risks = extraction.get("risks", [])

                return {
                    "response": full_response,
                    "suggestions": self._recent_suggestions,
                    "risks": self._recent_risks,
                    "sentiment": extraction.get("sentiment", "neutral"),
                    "memory_updates": memory_updates,
                    "imported_positions": imported_count,
                    "cash_updated": False,
                    "proposed_positions": positions
                }
            else:
                return {"response": full_response}

        except Exception as e:
            print(f"图片对话失败: {type(e).__name__}")
            import traceback
            traceback.print_exc()
            return {
                "response": "抱歉，处理图片时出错，没有完成这次对话。请稍后重试。",
                "suggestions": [],
                "risks": [],
                "sentiment": "neutral",
                "memory_updates": []
            }

    async def _build_context(self) -> AgentContext:
        """构建 Agent 上下文

        The portfolio part is the live context built from the same payload
        as ``GET /api/today`` (``core.agent_context``). Holdings come only
        from the confirmed ledger: with no ledger, or one that cannot be
        opened, the block says so and still gives the session, alerts and
        watchlist; no other holdings source (such as the legacy manual
        portfolio) is ever asked. Nothing here raises.
        """
        import asyncio
        from core.agent_context import build_live_context
        from core.ledger import READ_FAILED, LedgerUnavailable, read_confirmed
        from core.market.runtime import get_market_runtime

        runtime = get_market_runtime()
        ledger, ledger_error = None, None
        try:
            # Read-only (never creates, initialises or grows ledger.sqlite3) and bounded in time.
            ledger = await asyncio.to_thread(read_confirmed, timeout=CONTEXT_LEDGER_SECONDS)
        except LedgerUnavailable as exc:   # busy or damaged: its fixed reason
            print(f"[上下文] 账本无法读取: {type(exc).__name__}")
            ledger_error = exc.reason
        except Exception as exc:   # an unreadable ledger must not end the conversation
            print(f"[上下文] 账本无法读取: {type(exc).__name__}")
            ledger_error = READ_FAILED
        evidence = []
        try:
            live = await asyncio.to_thread(build_live_context, ledger, runtime, ledger_error=ledger_error)
            portfolio_summary, evidence = live.text, live.evidence
        except Exception as exc:
            print(f"[上下文] 实时数据无法生成: {type(exc).__name__}")
            portfolio_summary = "【实时持仓与行情】暂时无法生成，本轮没有可引用的实时数字。"

        # 获取用户记忆
        try:
            memory_context = self.memory_manager.get_context_string()
        except Exception as exc:
            print(f"[上下文] 记忆无法读取: {type(exc).__name__}")
            memory_context = ""

        # 组合上下文
        combined_context = ""
        if memory_context:
            combined_context += memory_context + "\n\n"
        combined_context += portfolio_summary

        context = AgentContext(
            conversation=self.agent.conversation,
            portfolio_summary=combined_context,
            current_time=runtime.clock.now(),
            session_phase=self._get_session_phase(runtime),
            evidence=evidence,
        )

        return context

    def _get_session_phase(self, runtime=None) -> str:
        """获取当前市场阶段（A 股），由交易日历 SessionClock 判断，含节假日。"""
        from core.agent_context import session_label
        return session_label(runtime)

    def _apply_user_message(self, message: str):
        """Apply what the fixed rules find in the user's own message; a failed save never ends the chat."""
        for memory in self.memory_extractor.extract_from_user_message(message):
            try:
                self._apply_memory(memory)
            except Exception as exc:
                print(f"[记忆更新] 未保存: {type(exc).__name__}")

    def _apply_memory(self, memory):
        """应用规则提取的记忆: only a value from a fixed set of choices is written (see "Memory writes")."""
        if memory.category == "profile" and memory.key in _RULE_PROFILE_KEYS:
            field = _RULE_PROFILE_KEYS[memory.key]
            value = _choice(field, memory.value)
            if value:
                self.memory_manager.update_profile(**{field: value})
        elif memory.category == "profile" and memory.key == "preferred_sector":
            if memory.value in MemoryExtractor.SECTORS:
                self.memory_manager.add_preferred_sector(memory.value)
        elif memory.category == "preference" and memory.key == "emotional_trigger":
            if memory.value in MemoryExtractor.emotion_choices():
                self.memory_manager.add_emotional_trigger(memory.value)

    def _apply_memory_update(self, update: Dict[str, Any]):
        """应用从 LLM 提取的记忆更新: free text, so always a ``lesson`` proposal (see "Memory writes").

        Neither the words nor the ``category`` the extractor gave it decide
        anything: nothing about it is stored until the person confirms the
        proposal. ``update`` is marked with ``status``
        (``awaiting_user_confirmation`` and ``proposal_id``, or
        ``not_proposed`` and ``error``).
        """
        content = update.get("content")
        if not isinstance(content, str) or not content.strip():
            return
        self._propose_lesson(update, content, lessons.lesson_type(content))

    def _propose_lesson(self, update: Dict[str, Any], content: str, lesson_type: str):
        """Store an extracted lesson as a proposal for the user to confirm."""
        from core import proposals
        try:
            proposal = lessons.propose({"description": content, "lesson": content, "type": lesson_type},
                                       source="agent:chat")
        except proposals.ProposalError as exc:
            update.update(status="not_proposed", error=str(exc))
            return
        except Exception as exc:   # a busy or broken store must not end the conversation
            print(f"[记忆更新] 教训方案未保存: {type(exc).__name__}")
            update.update(status="not_proposed", error="暂时无法保存这条教训方案。")
            return
        update.update(status="awaiting_user_confirmation", proposal_id=proposal["id"])

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

    def _apply_profile_update(self, user_profile: Dict[str, Any],
                              rejected: Sequence[str] = ()) -> List[Dict[str, Any]]:
        """应用从 LLM 提取的用户画像更新 (see "Memory writes"): nothing is written, everything is proposed.

        Each of the three fixed choices that is a member of its enum, and
        each sector that is one of ``MemoryExtractor.SECTORS``, becomes a
        ``profile`` proposal; anything else in them is dropped. A sector in
        ``rejected`` (the user turned it down this turn) is never proposed:
        it is ``not_proposed`` with ``SECTOR_REJECTED``. The free-text
        ``notes`` become a lesson proposal. Each is returned as an update with
        its status (``awaiting_user_confirmation`` and ``proposal_id``, or
        ``not_proposed`` and ``error``).
        """
        found = [(field, value) for field in _PROFILE_CHOICES
                 for value in [_choice(field, user_profile.get(field))] if value]
        sectors = user_profile.get("preferred_sectors")
        found += [("preferred_sector", sector) for sector in (sectors if isinstance(sectors, list) else [])
                  if isinstance(sector, str) and sector in MemoryExtractor.SECTORS]
        updates = []
        for field, value in found:
            update = {"category": "profile", "field": field, "value": value, "source": f"user_profile.{field}"}
            if field == "preferred_sector" and value in rejected:
                update.update(status="not_proposed", error=SECTOR_REJECTED)
            else:
                self._propose_profile(update)
            updates.append(update)

        notes = user_profile.get("notes")
        if not isinstance(notes, str) or not notes.strip() or notes.strip() == "null":
            return updates
        update = {"category": "profile", "content": notes.strip(), "source": "user_profile.notes"}
        self._propose_lesson(update, update["content"], lessons.lesson_type(notes))
        return updates + [update]

    def _propose_profile(self, update: Dict[str, Any]):
        """Store an extracted profile choice as a ``profile`` proposal; mark ``update`` with its status."""
        from core import proposals
        try:
            proposal = lessons.propose_profile(update["field"], update["value"], source="agent:chat")
        except proposals.ProposalError as exc:
            update.update(status="not_proposed", error=str(exc))
            return
        except Exception as exc:   # a busy or broken store must not end the conversation
            print(f"[记忆更新] 画像方案未保存: {type(exc).__name__}")
            update.update(status="not_proposed", error="暂时无法保存这项画像修改方案。")
            return
        update.update(status="awaiting_user_confirmation", proposal_id=proposal["id"], content=proposal["summary"])

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
        """获取持仓

        Amounts are in each position's own currency (``currency``) and are only added up
        per currency (``totals_by_currency``). ``total_market_value`` / ``total_assets`` /
        ``total_profit`` are numbers in ``currency`` while the portfolio is in one currency,
        and null with ``mixed_currency`` true when it holds several (see core.models.Portfolio).
        """
        portfolio = await self.portfolio_provider.get_portfolio()
        return {
            "positions": [
                {
                    "symbol": p.stock.symbol,
                    "name": p.stock.name,
                    "market": p.stock.market.value,
                    "currency": p.currency,
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
            "cash_currency": CASH_CURRENCY,
            "currency": portfolio.currency,
            "currencies": portfolio.currencies,
            "mixed_currency": portfolio.mixed_currency,
            "totals_by_currency": portfolio.totals_by_currency,
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

    def _normalize_market(self, market: str) -> Market:
        market_aliases = {
            "hk": "hk_stock",
            "us": "us_stock",
        }
        return Market(market_aliases.get(market, market))

    def _serialize_quote(self, quote) -> Dict[str, Any]:
        price, previous = quote.price, quote.prev_close
        # null without a price or a previous close (a missing value is 不可用, never 0)
        moved = price is not None and bool(previous)
        return {
            "symbol": quote.stock.symbol,
            "name": quote.stock.name,
            "price": quote.price,
            "open": quote.open,
            "high": quote.high,
            "low": quote.low,
            "prev_close": quote.prev_close,
            "volume": quote.volume,
            "amount": quote.amount,
            "turnover": quote.turnover,
            "pe": quote.pe,
            "change": price - previous if moved else None,
            "change_pct": (price - previous) / previous * 100 if moved else None,
            "timestamp": quote.timestamp.isoformat()
        }

    async def get_quote(self, symbol: str, market: str = "a_share") -> Optional[Dict]:
        """获取股票行情"""
        print(f"[Service] 获取行情: symbol={symbol}, market={market}")
        normalized_market = self._normalize_market(market)
        quote = await self.market_provider.get_quote(symbol, normalized_market)
        if quote:
            print(f"[Service] 成功获取: {quote.stock.name} - {quote.price}")
            return self._serialize_quote(quote)
        else:
            print(f"[Service] 未找到: {symbol}")
        return None

    async def get_quotes(self, symbols: List[str], market: str = "a_share") -> List[Dict[str, Any]]:
        """批量获取股票行情"""
        normalized_market = self._normalize_market(market)
        quotes = await self.market_provider.get_quotes(symbols, normalized_market)
        return [self._serialize_quote(quote) for quote in quotes]

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
