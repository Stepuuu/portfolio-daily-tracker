"""
记忆提取器
从 AI 回复和用户消息中自动提取记忆信息
"""
import re
import json
from typing import Dict, List, Any, Optional
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


class MemoryExtractor:
    """
    记忆提取器

    功能:
    1. 从用户消息中提取用户信息
    2. 从 AI 回复中提取建议和风险
    3. 分析情绪倾向
    """

    # 用户信息关键词
    PROFILE_PATTERNS = {
        "experience": [
            (r"我是新手|刚开始炒股|入市不久", "beginner"),
            (r"炒股[几\d]+年|有[几\d]+年经验", "intermediate"),
            (r"老股民|资深|十几年", "expert"),
        ],
        "style": [
            (r"日内|T\+0|当天进出", "day"),
            (r"波段|短线|几天", "swing"),
            (r"中线|持股|几周|一个月", "position"),
            (r"价值投资|长期持有|几年", "value"),
        ],
        "risk": [
            (r"保守|稳健|不想亏|安全", "conservative"),
            (r"平衡|适中", "moderate"),
            (r"激进|高收益|不怕亏|赌一把", "aggressive"),
        ],
    }

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

    def extract_from_user_message(self, message: str) -> List[ExtractedMemory]:
        """从用户消息中提取记忆"""
        memories = []

        # 提取用户画像信息
        for category, patterns in self.PROFILE_PATTERNS.items():
            for pattern, value in patterns:
                if re.search(pattern, message):
                    memories.append(ExtractedMemory(
                        category="profile",
                        key=category,
                        value=value,
                        confidence=0.8,
                        source_text=message[:100]
                    ))
                    break

        # 提取情绪信息
        for emotion, keywords in self.EMOTION_PATTERNS.items():
            for keyword in keywords:
                if keyword in message:
                    memories.append(ExtractedMemory(
                        category="preference",
                        key="emotional_trigger",
                        value=f"{emotion}:{keyword}",
                        confidence=0.7,
                        source_text=message[:100]
                    ))
                    break

        # 提取偏好板块
        sectors = self._extract_sectors(message)
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

    def _extract_sectors(self, text: str) -> List[str]:
        """提取板块信息"""
        sectors = []
        sector_keywords = [
            "科技", "新能源", "半导体", "芯片", "医药", "消费", "金融",
            "银行", "地产", "汽车", "军工", "农业", "白酒", "光伏",
            "锂电", "AI", "人工智能", "机器人", "传媒", "游戏"
        ]

        for sector in sector_keywords:
            if sector in text:
                sectors.append(sector)

        return sectors

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
        "experience_level": "beginner/intermediate/expert 或 null",
        "trading_style": "day/swing/position/value 或 null",
        "risk_tolerance": "conservative/moderate/aggressive 或 null",
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
            "cost_price": 成本价（浮点数，从AI回复中提取具体价格，如果没有明确提到成本价就提取当前价格，不要填0）
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
1. **多图/多账户提取（最重要的规则）**：如果对话中分析了多张图片或多个账户（例如：账户1和账户2），请务必**提取所有图片、所有账户中的所有持仓**，并将它们统一合并放置到 `positions` 数组中。绝对不要遗漏任何一张图的数据！！
2. **cash 提取规则（重要）**：
   - 提取所有图片/账户的"可用资金"、"可用现金"、"余额"、"纯现金"字段并**求和**（例如账户1有1元，账户2没提，则结果为1）。
   - 如果没有提到现金信息，返回 null（不要猜测或填0）。
3. 股票代码格式：A股6位数字，港股4-5位数字，美股字母代码
4. market 判断：SHA/SHE开头或6位数字=a_share, HKG开头=hk_stock, NASDAQ/NYSE开头或字母代码=us_stock
5. **cost_price 提取规则（非常重要）**：
   - **第一优先级**：如果助手分析的是持仓截图，直接从助手回复中提取明确提到的"成本价"、"成本均价"、"买入均价"等字段的准确数值
   - **第二优先级**：如果没有明确的成本价字段，寻找"盈亏"和"当前价"信息，通过反推计算：
     * 如果盈利X元：cost_price = current_price - (profit / quantity)
     * 如果盈利X%：cost_price = current_price / (1 + profit_pct/100)
   - **第三优先级**：如果以上都没有，使用"市值 / 数量"作为估算
   - **禁止行为**：绝对不要填 0 或 null，不要随意猜测数值
   - **示例1**："药明康德 3600股，成本价 104.031，当前价 99.95" → cost_price = 104.031（精确提取）
   - **示例2**："药明康德 3600股，当前价 99.95，盈亏 -14716元" → cost_price = 99.95 + 14716/3600 ≈ 104.087（反推）
   - **示例3**："药明康德 3600股，市值35.3万" → cost_price = 353000/3600 ≈ 98.06（估算，不准确）
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
