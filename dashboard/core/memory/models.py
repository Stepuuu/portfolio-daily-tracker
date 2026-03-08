"""
用户记忆数据模型
"""
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
from datetime import datetime
from enum import Enum
import json


class ExperienceLevel(str, Enum):
    """交易经验等级"""
    BEGINNER = "beginner"      # 新手
    INTERMEDIATE = "intermediate"  # 中级
    EXPERT = "expert"          # 专家


class TradingStyle(str, Enum):
    """交易风格"""
    DAY = "day"           # 日内交易
    SWING = "swing"       # 波段交易
    POSITION = "position"  # 中线持股
    VALUE = "value"       # 价值投资


class RiskTolerance(str, Enum):
    """风险承受能力"""
    CONSERVATIVE = "conservative"  # 保守
    MODERATE = "moderate"          # 稳健
    AGGRESSIVE = "aggressive"      # 激进


@dataclass
class UserProfile:
    """用户画像"""
    name: str = ""
    experience_level: ExperienceLevel = ExperienceLevel.INTERMEDIATE
    trading_style: TradingStyle = TradingStyle.SWING
    risk_tolerance: RiskTolerance = RiskTolerance.MODERATE
    preferred_sectors: List[str] = field(default_factory=list)
    typical_position_size: str = "20%"
    holding_period: str = "1-2周"
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "experience_level": self.experience_level.value,
            "trading_style": self.trading_style.value,
            "risk_tolerance": self.risk_tolerance.value,
            "preferred_sectors": self.preferred_sectors,
            "typical_position_size": self.typical_position_size,
            "holding_period": self.holding_period,
            "notes": self.notes
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UserProfile":
        return cls(
            name=data.get("name", ""),
            experience_level=ExperienceLevel(data.get("experience_level", "intermediate")),
            trading_style=TradingStyle(data.get("trading_style", "swing")),
            risk_tolerance=RiskTolerance(data.get("risk_tolerance", "moderate")),
            preferred_sectors=data.get("preferred_sectors", []),
            typical_position_size=data.get("typical_position_size", "20%"),
            holding_period=data.get("holding_period", "1-2周"),
            notes=data.get("notes", "")
        )


@dataclass
class TradingPreferences:
    """交易偏好"""
    stop_loss_habit: str = "设置但不严格执行"
    take_profit_style: str = "分批止盈"
    market_hours_active: List[str] = field(default_factory=lambda: ["9:30-11:30", "13:00-15:00"])
    news_sensitivity: str = "medium"  # low/medium/high
    emotional_triggers: List[str] = field(default_factory=list)
    avoid_patterns: List[str] = field(default_factory=list)
    preferred_indicators: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stop_loss_habit": self.stop_loss_habit,
            "take_profit_style": self.take_profit_style,
            "market_hours_active": self.market_hours_active,
            "news_sensitivity": self.news_sensitivity,
            "emotional_triggers": self.emotional_triggers,
            "avoid_patterns": self.avoid_patterns,
            "preferred_indicators": self.preferred_indicators
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TradingPreferences":
        return cls(
            stop_loss_habit=data.get("stop_loss_habit", "设置但不严格执行"),
            take_profit_style=data.get("take_profit_style", "分批止盈"),
            market_hours_active=data.get("market_hours_active", ["9:30-11:30", "13:00-15:00"]),
            news_sensitivity=data.get("news_sensitivity", "medium"),
            emotional_triggers=data.get("emotional_triggers", []),
            avoid_patterns=data.get("avoid_patterns", []),
            preferred_indicators=data.get("preferred_indicators", [])
        )


@dataclass
class TradingLesson:
    """交易教训/经验"""
    date: str
    description: str
    type: str  # "success" or "failure"
    symbol: Optional[str] = None
    lesson: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "date": self.date,
            "description": self.description,
            "type": self.type,
            "symbol": self.symbol,
            "lesson": self.lesson
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TradingLesson":
        return cls(
            date=data.get("date", ""),
            description=data.get("description", ""),
            type=data.get("type", ""),
            symbol=data.get("symbol"),
            lesson=data.get("lesson", "")
        )


@dataclass
class TradingHistory:
    """交易历史记录"""
    successful_patterns: List[str] = field(default_factory=list)
    failed_patterns: List[str] = field(default_factory=list)
    lessons: List[TradingLesson] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "successful_patterns": self.successful_patterns,
            "failed_patterns": self.failed_patterns,
            "lessons": [l.to_dict() for l in self.lessons]
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TradingHistory":
        return cls(
            successful_patterns=data.get("successful_patterns", []),
            failed_patterns=data.get("failed_patterns", []),
            lessons=[TradingLesson.from_dict(l) for l in data.get("lessons", [])]
        )


@dataclass
class UserGoals:
    """用户目标"""
    short_term: str = ""
    long_term: str = ""
    learning: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "short_term": self.short_term,
            "long_term": self.long_term,
            "learning": self.learning
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UserGoals":
        return cls(
            short_term=data.get("short_term", ""),
            long_term=data.get("long_term", ""),
            learning=data.get("learning", [])
        )


@dataclass
class MemoryEntry:
    """单条记忆"""
    id: str
    content: str
    category: str  # profile/preference/history/goal
    source: str    # "user_input" or "extracted"
    created_at: datetime = field(default_factory=datetime.now)
    confidence: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "content": self.content,
            "category": self.category,
            "source": self.source,
            "created_at": self.created_at.isoformat(),
            "confidence": self.confidence
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemoryEntry":
        return cls(
            id=data.get("id", ""),
            content=data.get("content", ""),
            category=data.get("category", ""),
            source=data.get("source", ""),
            created_at=datetime.fromisoformat(data.get("created_at", datetime.now().isoformat())),
            confidence=data.get("confidence", 1.0)
        )


@dataclass
class UserMemory:
    """完整的用户记忆"""
    profile: UserProfile = field(default_factory=UserProfile)
    preferences: TradingPreferences = field(default_factory=TradingPreferences)
    history: TradingHistory = field(default_factory=TradingHistory)
    goals: UserGoals = field(default_factory=UserGoals)
    entries: List[MemoryEntry] = field(default_factory=list)
    updated_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "profile": self.profile.to_dict(),
            "preferences": self.preferences.to_dict(),
            "history": self.history.to_dict(),
            "goals": self.goals.to_dict(),
            "entries": [e.to_dict() for e in self.entries],
            "updated_at": self.updated_at.isoformat()
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UserMemory":
        return cls(
            profile=UserProfile.from_dict(data.get("profile", {})),
            preferences=TradingPreferences.from_dict(data.get("preferences", {})),
            history=TradingHistory.from_dict(data.get("history", {})),
            goals=UserGoals.from_dict(data.get("goals", {})),
            entries=[MemoryEntry.from_dict(e) for e in data.get("entries", [])],
            updated_at=datetime.fromisoformat(data.get("updated_at", datetime.now().isoformat()))
        )

    def to_context_string(self) -> str:
        """生成给 Agent 的上下文字符串"""
        parts = []

        # 用户画像
        if self.profile.name or self.profile.notes:
            parts.append("【用户画像】")
            if self.profile.name:
                parts.append(f"昵称: {self.profile.name}")
            parts.append(f"经验等级: {self._translate_experience(self.profile.experience_level)}")
            parts.append(f"交易风格: {self._translate_style(self.profile.trading_style)}")
            parts.append(f"风险偏好: {self._translate_risk(self.profile.risk_tolerance)}")
            if self.profile.preferred_sectors:
                parts.append(f"偏好板块: {', '.join(self.profile.preferred_sectors)}")
            parts.append(f"典型仓位: {self.profile.typical_position_size}")
            parts.append(f"持仓周期: {self.profile.holding_period}")
            if self.profile.notes:
                parts.append(f"备注: {self.profile.notes}")

        # 交易偏好
        if self.preferences.emotional_triggers or self.preferences.avoid_patterns:
            parts.append("\n【交易偏好】")
            parts.append(f"止损习惯: {self.preferences.stop_loss_habit}")
            parts.append(f"止盈方式: {self.preferences.take_profit_style}")
            if self.preferences.emotional_triggers:
                parts.append(f"情绪触发点: {', '.join(self.preferences.emotional_triggers)}")
            if self.preferences.avoid_patterns:
                parts.append(f"避免的模式: {', '.join(self.preferences.avoid_patterns)}")

        # 历史教训
        if self.history.lessons:
            parts.append("\n【历史教训】")
            for lesson in self.history.lessons[-5:]:  # 最近5条
                parts.append(f"- {lesson.date}: {lesson.description}")
                if lesson.lesson:
                    parts.append(f"  教训: {lesson.lesson}")

        # 目标
        if self.goals.short_term or self.goals.long_term:
            parts.append("\n【投资目标】")
            if self.goals.short_term:
                parts.append(f"短期: {self.goals.short_term}")
            if self.goals.long_term:
                parts.append(f"长期: {self.goals.long_term}")

        return "\n".join(parts) if parts else ""

    def _translate_experience(self, level: ExperienceLevel) -> str:
        mapping = {
            ExperienceLevel.BEGINNER: "新手（<1年）",
            ExperienceLevel.INTERMEDIATE: "中级（1-5年）",
            ExperienceLevel.EXPERT: "专家（>5年）"
        }
        return mapping.get(level, str(level.value))

    def _translate_style(self, style: TradingStyle) -> str:
        mapping = {
            TradingStyle.DAY: "日内交易",
            TradingStyle.SWING: "波段交易",
            TradingStyle.POSITION: "中线持股",
            TradingStyle.VALUE: "价值投资"
        }
        return mapping.get(style, str(style.value))

    def _translate_risk(self, risk: RiskTolerance) -> str:
        mapping = {
            RiskTolerance.CONSERVATIVE: "保守（追求稳定）",
            RiskTolerance.MODERATE: "稳健（平衡收益与风险）",
            RiskTolerance.AGGRESSIVE: "激进（追求高收益）"
        }
        return mapping.get(risk, str(risk.value))
