"""
记忆提取器
从 AI 回复和用户消息中自动提取记忆信息
"""
import re
import json
import math
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Any, Optional, Sequence, Tuple
from dataclasses import dataclass


@dataclass
class ExtractedMemory:
    """提取的记忆"""
    category: str  # profile/preference/history/goal
    key: str       # 具体的字段
    value: Any     # 值
    confidence: float  # 置信度
    source_text: str   # 原文


@dataclass
class ExtractedSuggestion:
    """提取的建议"""
    type: str           # buy/sell/hold/reduce/add
    symbol: Optional[str]
    reason: str
    target_price: Optional[float]
    stop_loss: Optional[float]
    position_size: Optional[str]
    confidence: str     # high/medium/low
    raw_text: str


@dataclass
class ExtractedRisk:
    """提取的风险"""
    level: str          # high/medium/low
    type: str           # 满仓/重仓/追高/恐慌等
    description: str
    suggestion: str


@dataclass
class ExtractionResult:
    """提取结果"""
    memories: List[ExtractedMemory]
    suggestions: List[ExtractedSuggestion]
    risks: List[ExtractedRisk]
    sentiment: str  # bullish/bearish/neutral


# -- what the user says about themself ------------------------------------------
#
# The fixed rules write to memory at once (see "Memory writes" in
# backend/services/agent_service.py), so they read only what the user says
# about themself. The message is cut into clauses (at ，。！？；：、 and line
# ends) after every quoted span is removed (“…” 「…」 『…』 "…" ‘…’ 《…》: words
# quoted from someone else). A clause is skipped when it
#
# * asks something: ？ ? 吗 呢 么 嘛 是否 是不是 算不算 会不会 能不能 有没有
#   怎么 怎样 什么 为什么 如何 哪 多少;
# * is hypothetical or an example: 如果 假如 假设 要是 万一 倘若 若 的话 比如
#   例如 譬如 就像 (not 好比, which 偏好比较 contains);
# * speaks of someone or something else (THIRD_PERSON): 他 她 他们 别人 朋友
#   同事 大家 散户 这位 报告 文章 新闻 作者 交易员 ...
#
# A profile choice (experience, style, risk) needs more: the clause must
# start with the user as subject, 我 (after an opener such as 其实 / 说实话 /
# 我觉得), followed only by words from a short list (的风格, 的风险偏好, 比较,
# 很, 一般, 主要, 是, ...) and then the pattern. Anything else between (不, 并不,
# 从不, 们, 朋友, ...) is no match, so a negation, 我们 or 我朋友 sets nothing.
# Emotional triggers and sectors are fixed words too and may come from a
# clause without 我 (Chinese drops the subject: 最近很慌), but never from a
# skipped clause, and a trigger right after a negation (不慌, 不怕) is none.
# A sector the user turns down is never a preferred one: a clause is cut
# further at 但 / 而 / 只 / 不过 / 却 ..., and in each part a rejection (不喜欢,
# 不太喜欢, 不碰, 不买, 不考虑, 不感兴趣, 别买, 回避, 避开, 讨厌, 远离, 排除, 除了,
# ...) turns down the sectors after it (不喜欢银行股), or, when nothing follows
# it, the ones before it (银行股我不碰, 对传媒不感兴趣); those are rejected
# (``rejected_sectors``). A rejection of something else (不想追高) turns down no
# sector: 我喜欢科技股不想追高 prefers 科技, 我喜欢科技不喜欢银行 prefers 科技 only.

_QUOTED = re.compile(r'“[^”]*”|「[^」]*」|『[^』]*』|"[^"]*"|‘[^’]*’|《[^》]*》|\'[^\']*\'')
_CLAUSE = re.compile(r'[^，,。．.!！？?；;：:、\n\r]+[，,。．.!！？?；;：:、\n\r]?')
_QUESTION = re.compile(r'[？?]|吗|呢|么|嘛|是否|是不是|算不算|会不会|能不能|有没有|怎么|怎样|什么|为什么|如何|哪|多少')
_HYPOTHETICAL = re.compile(r'如果|假如|假设|要是|万一|倘若|若|的话|比如|例如|譬如|就像')
THIRD_PERSON = re.compile(
    r'他|她|它|别人|人家|有人|朋友|同事|家人|老婆|老公|大家|散户|机构|主力|游资|这位|那位|此人|作者|报告|研报|文章|新闻|'
    r'公告|帖子|博主|老师|分析师|交易员|专家|大V|媒体|网友|群里|书里|视频|主播')
_NEGATED = re.compile(r'(?:不|没|没有|别|无|非|未|毫不|并不|从不|从没)$')
_CONTRAST = re.compile(r'但是|可是|不过|然而|反而|而是|但|而|却|只')
_REJECT = re.compile(r'(?:不|没|没有|别|并不|从不|从没)(?:太|怎么|再|会|想|要|打算|愿意|大)?'
                     r'(?:喜欢|看好|碰|买|考虑|做|投|配|关注|感兴趣|兴趣|玩|追|爱|持有|看)'
                     r'|回避|避开|躲开|躲避|讨厌|厌恶|远离|排除|拒绝|除了')
# what may follow a rejection that ends its part (银行股我不碰了)
_NOTHING_AFTER = re.compile(r'[\s了的啊呀吧呢哦嘛啦，,。．.!！？?；;：:、]*')
_OPENER = r'(?:(?:其实|说实话|老实说|坦白说|坦白讲|但是|但|不过|而且|另外|所以|因为|总的来说|总之|反正|对了|嗯|哦)[\s，,]*)?'
_SUBJECT = (_OPENER + r'(?:我(?:觉得|认为|感觉|自认为|承认)[\s，,]*)?我(?:自己|本人)?'
            r'(?:的(?:交易|操作|投资)?(?:风格|风险偏好|性格))?')
_ADVERBS = (r'(?:一直|一向|向来|比较|很|挺|偏|偏向|倾向于|算是|属于|就是|是|现在|目前|一般|平时|主要|基本上|基本|'
            r'通常|个人|都|更|还算|算|也|只|非常|相当|特别|个|一个)*')


def _first_person(pattern: str) -> 're.Pattern':
    return re.compile(r'^\s*' + _SUBJECT + r'\s*' + _ADVERBS + r'\s*(?:' + pattern + ')')


def _clauses(message: str) -> List[str]:
    """The clauses of ``message`` that may say something about the user (see the comment above)."""
    text = _QUOTED.sub('，', message if isinstance(message, str) else '')
    return [clause.strip() for clause in _CLAUSE.findall(text)
            if clause.strip() and not _QUESTION.search(clause) and not _HYPOTHETICAL.search(clause)
            and not THIRD_PERSON.search(clause)]


class MemoryExtractor:
    """
    记忆提取器

    功能:
    1. 从用户消息中提取用户信息
    2. 从 AI 回复中提取建议和风险
    3. 分析情绪倾向
    """

    # 用户画像：只认用户对自己的第一人称陈述（见上面的说明）。每类按顺序取第一条匹配的。
    PROFILE_PATTERNS = {
        "experience": [
            (_first_person(r'(?:是|算)(?:个|一个)?(?:股市|炒股|投资)?(?:新手|小白)'), "beginner"),
            (_first_person(r'刚(?:开始)?(?:炒股|入市|做交易|接触股票)'), "beginner"),
            (_first_person(r'入市(?:还)?不久'), "beginner"),
            (_first_person(r'(?:是|算)(?:个|一个)?老股民'), "expert"),
            (_first_person(r'(?:炒股|做交易|投资)(?:已经|有|快|差不多)*(?:十几|十多|二十多|几十|'
                           r'[二三四五六七八九]?十|[六七八九]|[1-9]\d|[6-9])年'), "expert"),
            (_first_person(r'(?:炒股|做交易|投资)(?:已经|有|快|差不多)*(?:[一二两三四五]|[1-5])年'), "intermediate"),
            (_first_person(r'有' + r'(?:[一二两三四五]|[1-5])' + r'年(?:的)?(?:炒股|交易|投资)?经验'), "intermediate"),
        ],
        "style": [
            (_first_person(r'(?:做|是|玩)(?:个|一个)?(?:日内|T\+0|t\+0)'), "day"),
            (_first_person(r'(?:做|是|玩)(?:个|一个)?(?:波段|短线)'), "swing"),
            (_first_person(r'(?:做|是)(?:个|一个)?中线'), "position"),
            (_first_person(r'(?:做|是|坚持|搞)?(?:个|一个)?价值投资'), "value"),
            (_first_person(r'(?:习惯|喜欢)?长期持有'), "value"),
        ],
        "risk": [
            (_first_person(r'保守'), "conservative"),
            (_first_person(r'风险承受能力(?:比较|很|挺|偏|非常)?(?:低|弱)'), "conservative"),
            (_first_person(r'稳健|平衡|适中'), "moderate"),
            (_first_person(r'激进'), "aggressive"),
            (_first_person(r'风险承受能力(?:比较|很|挺|偏|非常)?(?:高|强)'), "aggressive"),
        ],
    }

    # 偏好板块：固定词表（自动写入记忆的板块只能是其中之一）
    SECTORS = (
        "科技", "新能源", "半导体", "芯片", "医药", "消费", "金融",
        "银行", "地产", "汽车", "军工", "农业", "白酒", "光伏",
        "锂电", "AI", "人工智能", "机器人", "传媒", "游戏"
    )

    # 情绪关键词
    EMOTION_PATTERNS = {
        "fear": ["慌", "怕", "担心", "焦虑", "不安", "恐慌", "割肉"],
        "greed": ["追", "满仓", "梭哈", "加仓", "错过", "后悔没买"],
        "confidence": ["看好", "牛市", "大涨", "翻倍"],
        "frustration": ["亏", "套", "跌", "难受", "心态崩"],
    }

    # 建议类型关键词
    SUGGESTION_PATTERNS = {
        "buy": ["买入", "建仓", "可以买", "买点", "入场"],
        "sell": ["卖出", "止盈", "离场", "出局", "走人"],
        "hold": ["持有", "观望", "等待", "不动"],
        "reduce": ["减仓", "减持", "卖一部分"],
        "add": ["加仓", "补仓", "加点"],
    }

    # 风险关键词
    RISK_PATTERNS = {
        "满仓": ["满仓", "全仓", "100%仓位", "没有现金"],
        "重仓": ["重仓", "仓位过重", "集中持仓"],
        "追高": ["追高", "追涨", "涨停追", "高位买入"],
        "恐慌": ["恐慌", "割肉", "不计成本卖出"],
        "频繁交易": ["频繁", "反复买卖", "来回操作"],
    }

    @classmethod
    def emotion_choices(cls) -> frozenset:
        """Every emotional-trigger value this extractor can produce: ``"<emotion>:<keyword>"``."""
        return frozenset(f"{emotion}:{keyword}" for emotion, keywords in cls.EMOTION_PATTERNS.items()
                         for keyword in keywords)

    def extract_from_user_message(self, message: str) -> List[ExtractedMemory]:
        """从用户消息中提取记忆: only what the user says about themself (see the comment above the class)."""
        memories = []
        clauses = _clauses(message)
        message = message if isinstance(message, str) else ''

        # 提取用户画像信息
        for category, patterns in self.PROFILE_PATTERNS.items():
            value = next((value for pattern, value in patterns for clause in clauses if pattern.search(clause)), None)
            if value:
                memories.append(ExtractedMemory(
                    category="profile",
                    key=category,
                    value=value,
                    confidence=0.8,
                    source_text=message[:100]
                ))

        # 提取情绪信息
        for emotion, keywords in self.EMOTION_PATTERNS.items():
            for keyword in keywords:
                if any(self._said(clause, keyword) for clause in clauses):
                    memories.append(ExtractedMemory(
                        category="preference",
                        key="emotional_trigger",
                        value=f"{emotion}:{keyword}",
                        confidence=0.7,
                        source_text=message[:100]
                    ))
                    break

        # 提取偏好板块（用户拒绝的板块不算，见上面的说明）
        sectors = self._sectors(clauses)[0]
        for sector in sectors:
            memories.append(ExtractedMemory(
                category="profile",
                key="preferred_sector",
                value=sector,
                confidence=0.6,
                source_text=message[:100]
            ))

        return memories

    def extract_from_ai_response(self, response: str) -> ExtractionResult:
        """从 AI 回复中提取结构化信息"""
        suggestions = self._extract_suggestions(response)
        risks = self._extract_risks(response)
        sentiment = self._analyze_sentiment(response)

        return ExtractionResult(
            memories=[],  # AI 回复一般不包含用户记忆
            suggestions=suggestions,
            risks=risks,
            sentiment=sentiment
        )

    @staticmethod
    def _said(clause: str, keyword: str) -> bool:
        """``keyword`` occurs in ``clause`` somewhere not right after a negation (不慌, 不怕亏, 没有担心)."""
        start = clause.find(keyword)
        while start != -1:
            if not _NEGATED.search(clause[max(0, start - 3):start]):
                return True
            start = clause.find(keyword, start + 1)
        return False

    def _extract_sectors(self, text: str) -> List[str]:
        """提取板块信息: the preferred sectors of ``text`` (none the text rejects)."""
        return self._sectors(_clauses(text))[0]

    def rejected_sectors(self, message: str) -> List[str]:
        """The sectors the user turns down in ``message`` (不喜欢银行, 远离地产, 银行股我不碰), in ``SECTORS`` order.

        A model's proposal never makes one of them a preferred sector.
        """
        return self._sectors(_clauses(message))[1]

    def _sectors(self, clauses: Sequence[str]) -> Tuple[List[str], List[str]]:
        """(preferred, rejected) sectors of ``clauses``; see the comment above the class."""
        preferred, rejected = [], []
        for part in (part for clause in clauses for part in _CONTRAST.split(clause) if part.strip()):
            turned_down = set()
            for rejection in _REJECT.finditer(part):
                after = part[rejection.end():]
                span = part[:rejection.start()] if _NOTHING_AFTER.fullmatch(after) else after
                turned_down.update(sector for sector in self.SECTORS if sector in span)
            for sector in self.SECTORS:
                if sector in part:
                    (rejected if sector in turned_down else preferred).append(sector)
        rejected = [sector for sector in self.SECTORS if sector in rejected]
        return [sector for sector in self.SECTORS if sector in preferred and sector not in rejected], rejected

    def _extract_suggestions(self, response: str) -> List[ExtractedSuggestion]:
        """提取建议"""
        suggestions = []

        for action_type, keywords in self.SUGGESTION_PATTERNS.items():
            for keyword in keywords:
                if keyword in response:
                    # 尝试提取更多细节
                    suggestion = ExtractedSuggestion(
                        type=action_type,
                        symbol=self._extract_stock_code(response),
                        reason=self._extract_reason(response, keyword),
                        target_price=self._extract_price(response, "目标"),
                        stop_loss=self._extract_price(response, "止损"),
                        position_size=self._extract_position_size(response),
                        confidence=self._determine_confidence(response),
                        raw_text=response[:200]
                    )
                    suggestions.append(suggestion)
                    break

        return suggestions

    def _extract_risks(self, response: str) -> List[ExtractedRisk]:
        """提取风险提示"""
        risks = []

        for risk_type, keywords in self.RISK_PATTERNS.items():
            for keyword in keywords:
                if keyword in response:
                    # 尝试提取风险级别和建议
                    level = "high" if any(w in response for w in ["严重", "高风险", "危险"]) else "medium"

                    risks.append(ExtractedRisk(
                        level=level,
                        type=risk_type,
                        description=self._extract_context(response, keyword, 50),
                        suggestion=self._extract_risk_suggestion(response, risk_type)
                    ))
                    break

        return risks

    def _analyze_sentiment(self, response: str) -> str:
        """分析情绪倾向"""
        bullish_words = ["看好", "上涨", "买入", "机会", "低估", "突破"]
        bearish_words = ["看空", "下跌", "卖出", "风险", "高估", "破位"]

        bullish_count = sum(1 for w in bullish_words if w in response)
        bearish_count = sum(1 for w in bearish_words if w in response)

        if bullish_count > bearish_count + 1:
            return "bullish"
        elif bearish_count > bullish_count + 1:
            return "bearish"
        else:
            return "neutral"

    def _extract_stock_code(self, text: str) -> Optional[str]:
        """提取股票代码"""
        # 匹配 6 位数字股票代码
        match = re.search(r'\b(\d{6})\b', text)
        if match:
            return match.group(1)
        return None

    def _extract_reason(self, text: str, keyword: str) -> str:
        """提取原因"""
        # 找到关键词附近的文本作为原因
        idx = text.find(keyword)
        if idx != -1:
            start = max(0, idx - 50)
            end = min(len(text), idx + 100)
            return text[start:end].strip()
        return ""

    def _extract_price(self, text: str, prefix: str) -> Optional[float]:
        """提取价格"""
        pattern = rf'{prefix}[价位]*[：:是]?\s*¥?(\d+\.?\d*)'
        match = re.search(pattern, text)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                pass
        return None

    def _extract_position_size(self, text: str) -> Optional[str]:
        """提取仓位建议"""
        patterns = [
            r'仓位[不超过]*(\d+%)',
            r'(\d+%)仓位',
            r'不超过(\d+%)',
        ]

        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(1)

        return None

    def _determine_confidence(self, text: str) -> str:
        """判断置信度"""
        high_words = ["强烈建议", "必须", "一定要", "务必"]
        low_words = ["可能", "或许", "不确定", "看情况"]

        if any(w in text for w in high_words):
            return "high"
        elif any(w in text for w in low_words):
            return "low"
        else:
            return "medium"

    def _extract_context(self, text: str, keyword: str, context_size: int = 50) -> str:
        """提取关键词上下文"""
        idx = text.find(keyword)
        if idx != -1:
            start = max(0, idx - context_size)
            end = min(len(text), idx + len(keyword) + context_size)
            return text[start:end].strip()
        return ""

    def _extract_risk_suggestion(self, text: str, risk_type: str) -> str:
        """提取风险应对建议"""
        suggestion_keywords = ["建议", "应该", "可以", "需要"]

        for keyword in suggestion_keywords:
            idx = text.find(keyword)
            if idx != -1:
                # 提取建议所在的句子
                end = text.find("。", idx)
                if end == -1:
                    end = min(len(text), idx + 100)
                return text[idx:end].strip()

        return ""


def _stated_cost_only(position: Any) -> Any:
    """A recognised position with ``cost_price`` kept only as a usable stated number, else None.

    A cost is never inferred (the prompt asks only for one the image or text states): 0, a negative
    or non-finite number, a boolean or anything that is not a number becomes null, never a guess.
    """
    if not isinstance(position, dict):
        return position
    cost = position.get("cost_price")
    usable = None
    if isinstance(cost, (int, float)) and not isinstance(cost, bool):
        usable = cost if math.isfinite(cost) and cost > 0 else None
    elif isinstance(cost, str):
        try:
            number = Decimal(cost.strip())
        except (InvalidOperation, ValueError):
            number = None
        usable = cost if number is not None and number.is_finite() and number > 0 else None
    return {**position, "cost_price": usable}


# 使用 LLM 进行更精确的提取
class LLMMemoryExtractor:
    """
    使用 LLM 进行记忆提取

    比规则提取更准确，但需要额外的 API 调用
    """

    EXTRACTION_PROMPT = """请仔细分析以下对话内容（可能包含对多张长截图或多个账户的持仓分析）。

对话内容:
用户: {user_message}
助手: {ai_response}

请以 JSON 格式返回提取的信息:
{{
    "user_profile": {{
        "experience_level": "beginner/intermediate/expert 或 null（只填用户本人明确说的关于自己的情况）",
        "trading_style": "day/swing/position/value 或 null（同上）",
        "risk_tolerance": "conservative/moderate/aggressive 或 null（同上）",
        "preferred_sectors": ["板块1", "板块2"] 或 [],
        "notes": "其他观察到的用户特点"
    }},
    "cash": 现金余额（浮点数）或 null,
    "positions": [
        {{
            "symbol": "股票代码（如 603259, 002050, 9988, GOOGL）",
            "name": "股票名称",
            "market": "a_share/hk_stock/us_stock",
            "quantity": 数量（整数）,
            "cost_price": 成本价（数字，只填截图或文字里明确写出的成本价；没有明确写出成本时填 null）
        }}
    ],
    "suggestions": [
        {{
            "type": "buy/sell/hold/reduce/add",
            "symbol": "股票代码或null",
            "reason": "原因",
            "target_price": 数字或null,
            "stop_loss": 数字或null,
            "position_size": "百分比或null",
            "confidence": "high/medium/low"
        }}
    ],
    "risks": [
        {{
            "level": "high/medium/low",
            "type": "风险类型",
            "description": "描述",
            "suggestion": "应对建议"
        }}
    ],
    "sentiment": "bullish/bearish/neutral",
    "memory_updates": [
        {{
            "category": "profile/preference/history/goal",
            "content": "要记住的内容",
            "confidence": 0.0-1.0
        }}
    ]
}}

注意：
0. user_profile 只描述用户本人：报告、新闻、截图或对话里提到的其他人（例如“这位资深交易员很激进”）不是用户，不要据此填写；用户在提问、假设或否定时也不要填写。这里的所有内容都只是给用户确认的建议，不会直接写入记忆。
1. **多图/多账户提取（最重要的规则）**：如果对话中分析了多张图片或多个账户（例如：账户1和账户2），请务必**提取所有图片、所有账户中的所有持仓**，并将它们统一合并放置到 `positions` 数组中。绝对不要遗漏任何一张图的数据！！
2. **cash 提取规则（重要）**：
   - 提取所有图片/账户的"可用资金"、"可用现金"、"余额"、"纯现金"字段并**求和**（例如账户1有1元，账户2没提，则结果为1）。
   - 如果没有提到现金信息，返回 null（不要猜测或填0）。
3. 股票代码格式：A股6位数字，港股4-5位数字，美股字母代码
4. market 判断：SHA/SHE开头或6位数字=a_share, HKG开头=hk_stock, NASDAQ/NYSE开头或字母代码=us_stock
5. **cost_price 规则（非常重要）**：
   - 只填截图或文字里明确写出的"成本价"、"成本均价"、"买入均价"、"摊薄成本"等字段的数值，原样照抄。
   - 没有明确写出成本时填 null。不要用现价、盈亏、盈亏比例或市值推算成本，也不要填 0 或现价：成本只能由用户在账本里确认。
   - 示例："示例股票 3600股，成本价 104.031，当前价 99.95" → cost_price = 104.031；
     "示例股票 3600股，当前价 99.95，盈亏 -14716元" → cost_price = null。
6. 只返回 JSON，不要其他内容
"""

    def __init__(self, llm_provider):
        self.llm = llm_provider

    async def extract(
        self,
        user_message: str,
        ai_response: str
    ) -> Dict[str, Any]:
        """使用 LLM 提取结构化信息"""
        prompt = self.EXTRACTION_PROMPT.format(
            user_message=user_message,
            ai_response=ai_response
        )

        try:
            response = await self.llm.chat([
                {"role": "user", "content": prompt}
            ])

            # 解析 JSON（处理可能的 markdown 代码块）
            content = response.content.strip()
            if content.startswith("```"):
                # 移除 markdown 代码块
                lines = content.split("\n")
                content = "\n".join(lines[1:-1])

            result = json.loads(content)
            if isinstance(result, dict) and isinstance(result.get("positions"), list):
                result["positions"] = [_stated_cost_only(position) for position in result["positions"]]
            return result

        except Exception as e:
            print(f"LLM 提取失败: {e}")
            return {
                "user_profile": {},
                "positions": [],
                "suggestions": [],
                "risks": [],
                "sentiment": "neutral",
                "memory_updates": []
            }
