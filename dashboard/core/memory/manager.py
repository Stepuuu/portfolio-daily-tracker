"""
记忆管理器
负责用户记忆的存储、检索和更新

Only what is saved counts, and there is one writer at a time. Every change
goes through ``_change()``, which holds the manager's lock from the start of
the change to the end of the save:

* the change is made on a deep copy of the memory (the *draft*), never on
  the objects the manager has handed out;
* the draft is saved; only when the file has been replaced does the draft
  become the manager's memory. When the save fails nothing changes, neither
  in memory nor in any object a caller holds, and the error propagates;
* a second caller (another thread: a confirmation in the web server's
  thread pool while the chat writes in the event loop) waits for the lock,
  so it can neither save the first caller's unsaved change nor lose its own
  change when the first caller's save fails.

A change that leaves the memory as it was is not saved. (A confirmed lesson
relies on all this: its idempotency check reads the in-memory lessons, which
never hold a lesson the file does not.) ``_save`` writes a temporary file
next to ``memory.json`` and renames it over the old one, so the file is
replaced whole or not at all.

A ``memory.json`` that cannot be read is never replaced before a copy of it
(``memory.json.unreadable-<time>``) was written and read back identical. The
copy is made when the file is loaded; when that fails it is tried again
before each save, and until it succeeds every save is refused with
``MemoryWriteRefused`` (a Chinese message saying why).

Demo mode (``TRACKER_DEMO_MODE=1``) never writes: every change is refused
with ``MemoryWriteRefused`` and no copy of an unreadable file is made.
"""
import copy
import dataclasses
import json
import os
import shutil
import tempfile
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any

from .models import (
    lesson_key,
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


DEMO_REFUSAL = "演示模式只读：助手的记忆不会写入。"
LESSON_MOVED = "这条教训的位置已经变了（可能在另一个页面删除过教训），没有删除任何内容。请刷新后再删除。"
UNREADABLE_REFUSAL = ("记忆文件 {name} 无法读取，而且没能另存一份核对无误的备份；为了不覆盖原文件，"
                      "记忆暂时不能保存。请检查磁盘空间和文件权限，或者把这个文件移走后再试。")


class MemoryWriteRefused(ValueError):
    """A save that must not happen (demo mode; an unreadable file without a verified copy). Chinese message."""


def _demo_mode() -> bool:
    return os.environ.get("TRACKER_DEMO_MODE") == "1"


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
        self._lock = threading.RLock()
        self._staged: Optional[UserMemory] = None     # the draft ``_save`` writes, set by ``_change``
        self._unreadable = False                      # the file on disk was unreadable and has no verified copy yet
        self._load()

    def _load(self):
        """从文件加载记忆

        A file that cannot be read is copied aside first
        (``memory.json.unreadable-<time>``, read back and compared), so the
        next save, which replaces ``memory.json``, never destroys what it
        held. Without a verified copy saving is refused (see the module
        docstring). Demo mode makes no copy (and never saves).
        """
        if self.data_file.exists():
            try:
                with open(self.data_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._memory = UserMemory.from_dict(data)
            except Exception as e:
                print(f"加载记忆文件失败: {type(e).__name__}")
                self._memory = UserMemory()
                self._unreadable = True
                if not _demo_mode():
                    self._unreadable = not self._keep_unreadable()
        else:
            self._memory = UserMemory()

    def _keep_unreadable(self) -> bool:
        """Copy the unreadable file aside and check the copy; True when an identical copy exists (or no file is left)."""
        try:
            original = self.data_file.read_bytes()
        except FileNotFoundError:
            return True                                   # nothing left that a save could destroy
        except OSError as exc:
            print(f"[记忆] 无法读取要另存的记忆文件: {type(exc).__name__}")
            return False
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        for attempt in range(100):
            target = self.data_file.with_name(f"{self.data_file.name}.unreadable-{stamp}"
                                              + (f"-{attempt}" if attempt else ""))
            if target.exists():
                continue
            try:
                shutil.copy2(self.data_file, target)
                if target.read_bytes() == original:
                    print(f"[记忆] 无法读取的记忆文件已另存为 {target.name}")
                    return True
                print("[记忆] 另存的记忆文件与原文件不一致，已删除这份副本")
            except OSError as exc:
                print(f"[记忆] 无法另存无法读取的记忆文件: {type(exc).__name__}")
            try:
                target.unlink()
            except OSError:
                pass
            return False
        return False

    def _save(self):
        """保存记忆到文件: encode the draft of ``_change`` (else the memory), write a temporary file,
        then rename it over ``data_file``."""
        memory = self._staged if self._staged is not None else self._memory
        self.data_file.parent.mkdir(parents=True, exist_ok=True)

        memory.updated_at = datetime.now()
        text = json.dumps(memory.to_dict(), ensure_ascii=False, indent=2)

        handle, temporary = tempfile.mkstemp(prefix=f".{self.data_file.name}.", suffix=".tmp",
                                             dir=str(self.data_file.parent))
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, self.data_file)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise

    @staticmethod
    def _replace_fields(target: UserMemory, state: UserMemory):
        for item in dataclasses.fields(UserMemory):
            setattr(target, item.name, getattr(state, item.name))

    @contextmanager
    def _change(self):
        """Yield a draft of the memory; save it and make it the memory, under the lock (see the module docstring).

        On any failure, of the block or of the save, the memory and every
        object handed out stay as they were. A draft equal to the memory is
        not saved.
        """
        with self._lock:
            if _demo_mode():
                raise MemoryWriteRefused(DEMO_REFUSAL)
            if self._unreadable:
                self._unreadable = not self._keep_unreadable()
                if self._unreadable:
                    raise MemoryWriteRefused(UNREADABLE_REFUSAL.format(name=self.data_file.name))
            draft = copy.deepcopy(self._memory)
            yield draft
            if draft == self._memory:
                return
            self._staged = draft
            try:
                self._save()
            finally:
                self._staged = None
            self._memory = draft

    @property
    def memory(self) -> UserMemory:
        """获取用户记忆 (a snapshot: a later change replaces it, it is never changed in place)"""
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
        with self._change() as memory:
            self._update_profile(memory.profile, kwargs)
        return copy.deepcopy(self._memory.profile)

    @staticmethod
    def _update_profile(profile: UserProfile, kwargs: Dict[str, Any]):
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

    def add_preferred_sector(self, sector: str):
        """添加偏好板块"""
        with self._change() as memory:
            if sector not in memory.profile.preferred_sectors:
                memory.profile.preferred_sectors.append(sector)

    def remove_preferred_sector(self, sector: str):
        """移除偏好板块"""
        with self._change() as memory:
            if sector in memory.profile.preferred_sectors:
                memory.profile.preferred_sectors.remove(sector)

    # ==================== 交易偏好 ====================

    def update_preferences(self, **kwargs) -> TradingPreferences:
        """更新交易偏好"""
        with self._change() as memory:
            self._update_preferences(memory.preferences, kwargs)
        return copy.deepcopy(self._memory.preferences)

    @staticmethod
    def _update_preferences(prefs: TradingPreferences, kwargs: Dict[str, Any]):
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

    def add_emotional_trigger(self, trigger: str):
        """添加情绪触发点"""
        with self._change() as memory:
            if trigger not in memory.preferences.emotional_triggers:
                memory.preferences.emotional_triggers.append(trigger)

    def remove_emotional_trigger(self, trigger: str):
        """移除情绪触发点"""
        with self._change() as memory:
            if trigger in memory.preferences.emotional_triggers:
                memory.preferences.emotional_triggers.remove(trigger)

    def add_avoid_pattern(self, pattern: str):
        """添加要避免的模式"""
        with self._change() as memory:
            if pattern not in memory.preferences.avoid_patterns:
                memory.preferences.avoid_patterns.append(pattern)

    # ==================== 交易历史 ====================

    def add_lesson(
        self,
        description: str,
        lesson_type: str,
        lesson: str = "",
        symbol: Optional[str] = None,
        date: Optional[str] = None,
        source_id: Optional[str] = None,
        unique_text: bool = False
    ) -> bool:
        """
        添加交易教训

        Args:
            description: 事件描述
            lesson_type: "success" 或 "failure"
            lesson: 学到的教训
            symbol: 相关股票代码
            date: 日期，默认今天
            source_id: 写入它的已确认方案的 id；同一个 id 只写一次
            unique_text: 记忆里已有同样文字（``text_key`` 相同）的教训时不再写入

        Returns:
            True when the lesson was added and saved, False when ``source_id``
            was already written or (``unique_text``) the same text is already
            there: nothing changes. Raises when the save fails; then nothing
            changes either, so a retry adds it. The checks and the write are
            one change under the lock.
        """
        trading_lesson = TradingLesson(
            date=date or datetime.now().strftime("%Y-%m-%d"),
            description=description,
            type=lesson_type,
            symbol=symbol,
            lesson=lesson,
            source_id=source_id
        )
        added = False
        with self._change() as memory:
            if source_id and any(item.source_id == source_id for item in memory.history.lessons):
                return False
            if unique_text and any(lesson_key(item) == lesson_key(trading_lesson) for item in memory.history.lessons):
                return False
            memory.history.lessons.append(trading_lesson)
            added = True

            # 更新成功/失败模式
            if lesson_type == "success" and lesson:
                if lesson not in memory.history.successful_patterns:
                    memory.history.successful_patterns.append(lesson)
            elif lesson_type == "failure" and lesson:
                if lesson not in memory.history.failed_patterns:
                    memory.history.failed_patterns.append(lesson)
        return added

    def remove_lesson(self, index: int, expected: Optional[Dict[str, Any]] = None):
        """移除指定索引的教训.

        ``expected`` is the lesson the caller saw at ``index`` (as ``get_full_memory`` lists it). When given,
        the lesson is removed only if that place still holds it: a list read before another tab removed an
        earlier lesson must not remove the one that moved into the place. Otherwise nothing changes and
        ``MemoryWriteRefused(LESSON_MOVED)`` says why.
        """
        with self._change() as memory:
            lessons = memory.history.lessons
            if expected is not None:
                named = TradingLesson.from_dict(expected).to_dict()
                if not (0 <= index < len(lessons)) or lessons[index].to_dict() != named:
                    raise MemoryWriteRefused(LESSON_MOVED)
            if 0 <= index < len(lessons):
                lessons.pop(index)

    def add_successful_pattern(self, pattern: str):
        """添加成功模式"""
        with self._change() as memory:
            if pattern not in memory.history.successful_patterns:
                memory.history.successful_patterns.append(pattern)

    def add_failed_pattern(self, pattern: str):
        """添加失败模式"""
        with self._change() as memory:
            if pattern not in memory.history.failed_patterns:
                memory.history.failed_patterns.append(pattern)

    # ==================== 用户目标 ====================

    def update_goals(self, **kwargs) -> UserGoals:
        """更新用户目标"""
        with self._change() as memory:
            if "short_term" in kwargs:
                memory.goals.short_term = kwargs["short_term"]
            if "long_term" in kwargs:
                memory.goals.long_term = kwargs["long_term"]
            if "learning" in kwargs:
                memory.goals.learning = kwargs["learning"]
        return copy.deepcopy(self._memory.goals)

    def add_learning_goal(self, goal: str):
        """添加学习目标"""
        with self._change() as memory:
            if goal not in memory.goals.learning:
                memory.goals.learning.append(goal)

    def add_short_term_goal(self, goal: str):
        """添加/更新短期目标"""
        with self._change() as memory:
            memory.goals.short_term = goal

    def add_long_term_goal(self, goal: str):
        """添加/更新长期目标"""
        with self._change() as memory:
            memory.goals.long_term = goal

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

        with self._change() as memory:
            memory.entries.append(entry)
        return copy.deepcopy(entry)

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
        with self._change() as memory:
            self._replace_fields(memory, UserMemory())

    def clear_history(self):
        """清除历史记录"""
        with self._change() as memory:
            memory.history = TradingHistory()

    def clear_entries(self):
        """清除通用记忆条目"""
        with self._change() as memory:
            memory.entries = []
