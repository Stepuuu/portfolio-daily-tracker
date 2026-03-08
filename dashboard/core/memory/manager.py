"""
记忆管理器
负责用户记忆的存储、检索和更新
"""
import json
import uuid
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any

from .models import (
    UserMemory,
    UserProfile,
    TradingPreferences,
    TradingHistory,
    TradingLesson,
    UserGoals,
    MemoryEntry,
    ExperienceLevel,
    TradingStyle,
    RiskTolerance
)


class MemoryManager:
    """
    用户记忆管理器

    功能:
    1. 加载/保存用户记忆
    2. 更新用户画像
    3. 添加交易教训
    4. 从对话中提取记忆
    5. 生成 Agent 上下文
    """

    def __init__(self, data_file: str = "data/memory.json"):
        self.data_file = Path(data_file)
        self._memory: Optional[UserMemory] = None
        self._load()

    def _load(self):
        """从文件加载记忆"""
        if self.data_file.exists():
            try:
                with open(self.data_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._memory = UserMemory.from_dict(data)
            except Exception as e:
                print(f"加载记忆文件失败: {e}")
                self._memory = UserMemory()
        else:
            self._memory = UserMemory()

    def _save(self):
        """保存记忆到文件"""
        self.data_file.parent.mkdir(parents=True, exist_ok=True)

        self._memory.updated_at = datetime.now()

        with open(self.data_file, "w", encoding="utf-8") as f:
            json.dump(self._memory.to_dict(), f, ensure_ascii=False, indent=2)

    @property
    def memory(self) -> UserMemory:
        """获取用户记忆"""
        return self._memory

    # ==================== 用户画像 ====================

    def update_profile(self, **kwargs) -> UserProfile:
        """
        更新用户画像

        Args:
            name: 用户昵称
            experience_level: 经验等级
            trading_style: 交易风格
            risk_tolerance: 风险承受能力
            preferred_sectors: 偏好板块
            typical_position_size: 典型仓位
            holding_period: 持仓周期
            notes: 备注
        """
        profile = self._memory.profile

        if "name" in kwargs:
            profile.name = kwargs["name"]
        if "experience_level" in kwargs:
            try:
                profile.experience_level = ExperienceLevel(kwargs["experience_level"])
            except ValueError:
                print(f"[记忆] 无效的经验等级: {kwargs['experience_level']}")
        if "trading_style" in kwargs:
            try:
                profile.trading_style = TradingStyle(kwargs["trading_style"])
            except ValueError:
                print(f"[记忆] 无效的交易风格: {kwargs['trading_style']}")
        if "risk_tolerance" in kwargs:
            try:
                profile.risk_tolerance = RiskTolerance(kwargs["risk_tolerance"])
            except ValueError:
                print(f"[记忆] 无效的风险偏好: {kwargs['risk_tolerance']}")
        if "preferred_sectors" in kwargs:
            profile.preferred_sectors = kwargs["preferred_sectors"]
        if "typical_position_size" in kwargs:
            profile.typical_position_size = kwargs["typical_position_size"]
        if "holding_period" in kwargs:
            profile.holding_period = kwargs["holding_period"]
        if "notes" in kwargs:
            profile.notes = kwargs["notes"]

        self._save()
        return profile

    def add_preferred_sector(self, sector: str):
        """添加偏好板块"""
        if sector not in self._memory.profile.preferred_sectors:
            self._memory.profile.preferred_sectors.append(sector)
            self._save()

    def remove_preferred_sector(self, sector: str):
        """移除偏好板块"""
        if sector in self._memory.profile.preferred_sectors:
            self._memory.profile.preferred_sectors.remove(sector)
            self._save()

    # ==================== 交易偏好 ====================

    def update_preferences(self, **kwargs) -> TradingPreferences:
        """更新交易偏好"""
        prefs = self._memory.preferences

        if "stop_loss_habit" in kwargs:
            prefs.stop_loss_habit = kwargs["stop_loss_habit"]
        if "take_profit_style" in kwargs:
            prefs.take_profit_style = kwargs["take_profit_style"]
        if "market_hours_active" in kwargs:
            prefs.market_hours_active = kwargs["market_hours_active"]
        if "news_sensitivity" in kwargs:
            prefs.news_sensitivity = kwargs["news_sensitivity"]
        if "emotional_triggers" in kwargs:
            prefs.emotional_triggers = kwargs["emotional_triggers"]
        if "avoid_patterns" in kwargs:
            prefs.avoid_patterns = kwargs["avoid_patterns"]
        if "preferred_indicators" in kwargs:
            prefs.preferred_indicators = kwargs["preferred_indicators"]

        self._save()
        return prefs

    def add_emotional_trigger(self, trigger: str):
        """添加情绪触发点"""
        if trigger not in self._memory.preferences.emotional_triggers:
            self._memory.preferences.emotional_triggers.append(trigger)
            self._save()

    def remove_emotional_trigger(self, trigger: str):
        """移除情绪触发点"""
        if trigger in self._memory.preferences.emotional_triggers:
            self._memory.preferences.emotional_triggers.remove(trigger)
            self._save()

    def add_avoid_pattern(self, pattern: str):
        """添加要避免的模式"""
        if pattern not in self._memory.preferences.avoid_patterns:
            self._memory.preferences.avoid_patterns.append(pattern)
            self._save()

    # ==================== 交易历史 ====================

    def add_lesson(
        self,
        description: str,
        lesson_type: str,
        lesson: str = "",
        symbol: Optional[str] = None,
        date: Optional[str] = None
    ):
        """
        添加交易教训

        Args:
            description: 事件描述
            lesson_type: "success" 或 "failure"
            lesson: 学到的教训
            symbol: 相关股票代码
            date: 日期，默认今天
        """
        trading_lesson = TradingLesson(
            date=date or datetime.now().strftime("%Y-%m-%d"),
            description=description,
            type=lesson_type,
            symbol=symbol,
            lesson=lesson
        )

        self._memory.history.lessons.append(trading_lesson)

        # 更新成功/失败模式
        if lesson_type == "success" and lesson:
            if lesson not in self._memory.history.successful_patterns:
                self._memory.history.successful_patterns.append(lesson)
        elif lesson_type == "failure" and lesson:
            if lesson not in self._memory.history.failed_patterns:
                self._memory.history.failed_patterns.append(lesson)

        self._save()

    def remove_lesson(self, index: int):
        """移除指定索引的教训"""
        if 0 <= index < len(self._memory.history.lessons):
            self._memory.history.lessons.pop(index)
            self._save()

    def add_successful_pattern(self, pattern: str):
        """添加成功模式"""
        if pattern not in self._memory.history.successful_patterns:
            self._memory.history.successful_patterns.append(pattern)
            self._save()

    def add_failed_pattern(self, pattern: str):
        """添加失败模式"""
        if pattern not in self._memory.history.failed_patterns:
            self._memory.history.failed_patterns.append(pattern)
            self._save()

    # ==================== 用户目标 ====================

    def update_goals(self, **kwargs) -> UserGoals:
        """更新用户目标"""
        goals = self._memory.goals

        if "short_term" in kwargs:
            goals.short_term = kwargs["short_term"]
        if "long_term" in kwargs:
            goals.long_term = kwargs["long_term"]
        if "learning" in kwargs:
            goals.learning = kwargs["learning"]

        self._save()
        return goals

    def add_learning_goal(self, goal: str):
        """添加学习目标"""
        if goal not in self._memory.goals.learning:
            self._memory.goals.learning.append(goal)
            self._save()

    def add_short_term_goal(self, goal: str):
        """添加/更新短期目标"""
        self._memory.goals.short_term = goal
        self._save()

    def add_long_term_goal(self, goal: str):
        """添加/更新长期目标"""
        self._memory.goals.long_term = goal
        self._save()

    # ==================== 通用记忆条目 ====================

    def add_memory_entry(
        self,
        content: str,
        category: str,
        source: str = "extracted",
        confidence: float = 1.0
    ) -> MemoryEntry:
        """
        添加通用记忆条目

        Args:
            content: 记忆内容
            category: 类别 (profile/preference/history/goal)
            source: 来源 (user_input/extracted)
            confidence: 置信度
        """
        entry = MemoryEntry(
            id=str(uuid.uuid4()),
            content=content,
            category=category,
            source=source,
            confidence=confidence
        )

        self._memory.entries.append(entry)
        self._save()
        return entry

    def get_recent_entries(self, n: int = 10) -> List[MemoryEntry]:
        """获取最近的记忆条目"""
        return sorted(
            self._memory.entries,
            key=lambda x: x.created_at,
            reverse=True
        )[:n]

    def search_entries(self, keyword: str) -> List[MemoryEntry]:
        """搜索记忆条目"""
        return [
            e for e in self._memory.entries
            if keyword.lower() in e.content.lower()
        ]

    # ==================== 上下文生成 ====================

    def get_context_string(self) -> str:
        """生成给 Agent 的上下文字符串"""
        return self._memory.to_context_string()

    def get_full_memory(self) -> Dict[str, Any]:
        """获取完整记忆（用于 API）"""
        return self._memory.to_dict()

    # ==================== 记忆清理 ====================

    def clear_all(self):
        """清除所有记忆"""
        self._memory = UserMemory()
        self._save()

    def clear_history(self):
        """清除历史记录"""
        self._memory.history = TradingHistory()
        self._save()

    def clear_entries(self):
        """清除通用记忆条目"""
        self._memory.entries = []
        self._save()
