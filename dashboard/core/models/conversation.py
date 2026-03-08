"""
对话与消息模型定义
"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any


class MessageRole(Enum):
    """消息角色"""
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class AlertLevel(Enum):
    """提醒级别"""
    INFO = "info"           # 信息
    WARNING = "warning"     # 警告
    CRITICAL = "critical"   # 紧急


@dataclass
class Message:
    """对话消息"""
    role: MessageRole
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role.value,
            "content": self.content,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata
        }

    def to_llm_format(self) -> Dict[str, str]:
        """转换为 LLM API 格式"""
        return {
            "role": self.role.value,
            "content": self.content
        }


@dataclass
class Conversation:
    """对话会话"""
    id: str
    messages: List[Message] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def add_message(self, role: MessageRole, content: str, **metadata) -> Message:
        """添加消息"""
        msg = Message(role=role, content=content, metadata=metadata)
        self.messages.append(msg)
        self.updated_at = datetime.now()
        return msg

    def get_messages_for_llm(self, include_system: bool = True) -> List[Dict[str, str]]:
        """获取用于 LLM 的消息列表"""
        return [
            m.to_llm_format() for m in self.messages
            if include_system or m.role != MessageRole.SYSTEM
        ]

    def get_recent_messages(self, n: int = 10) -> List[Message]:
        """获取最近 n 条消息"""
        return self.messages[-n:]


@dataclass
class Alert:
    """主动提醒"""
    level: AlertLevel
    title: str
    message: str
    timestamp: datetime = field(default_factory=datetime.now)
    data: Dict[str, Any] = field(default_factory=dict)
    acknowledged: bool = False

    def to_display(self) -> str:
        """格式化显示"""
        level_icons = {
            AlertLevel.INFO: "ℹ️ ",
            AlertLevel.WARNING: "⚠️ ",
            AlertLevel.CRITICAL: "🚨"
        }
        icon = level_icons.get(self.level, "")
        return f"{icon} [{self.title}] {self.message}"


@dataclass
class AgentContext:
    """Agent 上下文信息"""
    conversation: Conversation
    portfolio_summary: Optional[str] = None
    market_summary: Optional[str] = None
    recent_alerts: List[Alert] = field(default_factory=list)
    current_time: datetime = field(default_factory=datetime.now)
    session_phase: str = "unknown"  # pre_market, trading, post_market

    def to_context_string(self) -> str:
        """生成上下文字符串，供 Agent 使用"""
        parts = [f"【当前时间】{self.current_time.strftime('%Y-%m-%d %H:%M:%S')} ({self.session_phase})"]

        if self.portfolio_summary:
            parts.append(f"\n{self.portfolio_summary}")

        if self.market_summary:
            parts.append(f"\n【市场概况】\n{self.market_summary}")

        if self.recent_alerts:
            parts.append("\n【近期提醒】")
            for alert in self.recent_alerts[-5:]:
                parts.append(f"  - {alert.to_display()}")

        return "\n".join(parts)
