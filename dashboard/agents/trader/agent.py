"""
交易员 Agent 核心逻辑
"""
from typing import Optional, AsyncIterator, List
from datetime import datetime
import json

from core.llm.base import LLMProvider
from core.models import (
    Conversation,
    MessageRole,
    AgentContext,
    Portfolio
)
from core.tools import ToolExecutor
from .prompts import build_system_prompt


class TraderAgent:
    """交易员 Agent"""

    def __init__(self, llm_provider: LLMProvider, tool_executor: Optional[ToolExecutor] = None):
        self.llm = llm_provider
        self.conversation: Optional[Conversation] = None
        self.tool_executor = tool_executor
        self.max_tool_iterations = 5  # 防止无限循环

    def start_conversation(self, conversation_id: str = "default") -> Conversation:
        """开始新对话"""
        self.conversation = Conversation(id=conversation_id)
        return self.conversation

    def load_conversation(self, conversation: Conversation):
        """加载已有对话"""
        self.conversation = conversation

    async def chat(
        self,
        user_message: str,
        context: Optional[AgentContext] = None,
        stream: bool = False
    ):
        """
        与 Agent 对话（支持工具调用）

        Args:
            user_message: 用户消息
            context: 上下文信息（持仓、市场等）
            stream: 是否流式返回

        Returns:
            如果 stream=False，返回完整回复字符串
            如果 stream=True，返回 async generator
        """
        if not self.conversation:
            self.start_conversation()

        # 添加用户消息
        self.conversation.add_message(MessageRole.USER, user_message)

        # 构建消息列表
        messages = self._build_messages(context)

        # 如果有工具，添加工具定义
        tools = None
        if self.tool_executor:
            tools = self.tool_executor.get_tool_schemas()

        # 工具调用循环
        iteration = 0
        final_response = ""

        while iteration < self.max_tool_iterations:
            iteration += 1

            # 调用 LLM
            if stream and iteration == 1:
                # 流式模式：只在第一次迭代流式返回
                response_text = ""
                stop_reason = None
                tool_calls = []

                async for chunk in self.llm.chat_stream(messages, tools=tools):
                    if isinstance(chunk, dict):
                        # 处理流式响应中的元数据
                        if "stop_reason" in chunk:
                            stop_reason = chunk["stop_reason"]
                        if "tool_calls" in chunk:
                            tool_calls = chunk["tool_calls"]
                    else:
                        response_text += chunk
                        yield chunk

                # 检查是否需要调用工具
                if stop_reason == "tool_use" and tool_calls:
                    # 暂停流式输出，执行工具
                    assistant_content = []
                    if response_text:
                        assistant_content.append({"type": "text", "text": response_text})

                    for tool_call in tool_calls:
                        assistant_content.append({
                            "type": "tool_use",
                            "id": tool_call["id"],
                            "name": tool_call["name"],
                            "input": tool_call["input"]
                        })

                    messages.append({
                        "role": "assistant",
                        "content": assistant_content
                    })

                    # 执行所有工具调用
                    tool_results = []
                    for tool_call in tool_calls:
                        tool_name = tool_call["name"]
                        tool_input = tool_call["input"]

                        print(f"[Tool] 调用工具: {tool_name}({tool_input})")
                        result = await self.tool_executor.execute_tool(tool_name, tool_input)
                        print(f"[Tool] 工具结果: {result}")

                        tool_results.append({
                            "role": "user",  # Claude expects tool results in a user message
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": tool_call["id"],
                                    "content": json.dumps(result, ensure_ascii=False)
                                }
                            ]
                        })

                    # 添加工具结果到消息
                    messages.extend(tool_results)

                    # 继续非流式模式获取最终回复
                    response = await self.llm.chat(messages, tools=tools)
                    final_text = response.content
                    yield f"\n\n{final_text}"

                    final_response = response_text + "\n\n" + final_text
                    break
                else:
                    final_response = response_text
                    break

            else:
                # 非流式模式或后续迭代
                response = await self.llm.chat(messages, tools=tools)
                response_text = response.content

                # 检查是否需要调用工具
                if hasattr(response, 'stop_reason') and response.stop_reason == "tool_use":
                    # 有工具调用
                    tool_calls = getattr(response, 'tool_calls', [])

                    if not tool_calls:
                        # 没有工具调用，直接返回
                        final_response = response_text
                        break

                    # 添加助手消息（包含工具调用）
                    assistant_content = []
                    if response_text:
                        assistant_content.append({"type": "text", "text": response_text})

                    for tool_call in tool_calls:
                        assistant_content.append({
                            "type": "tool_use",
                            "id": tool_call.get("id"),
                            "name": tool_call.get("name"),
                            "input": tool_call.get("input", {})
                        })

                    messages.append({
                        "role": "assistant",
                        "content": assistant_content
                    })

                    # 执行所有工具调用
                    tool_results = []
                    for tool_call in tool_calls:
                        tool_name = tool_call.get("name")
                        tool_input = tool_call.get("input", {})
                        tool_id = tool_call.get("id")

                        print(f"[Tool] 调用工具: {tool_name}({tool_input})")
                        result = await self.tool_executor.execute_tool(tool_name, tool_input)
                        print(f"[Tool] 工具结果: {result}")

                        tool_results.append({
                            "role": "user",  # Claude expects tool results in a user message
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": tool_id,
                                    "content": json.dumps(result, ensure_ascii=False)
                                }
                            ]
                        })

                    # 添加工具结果到消息
                    messages.extend(tool_results)

                    # 继续下一次迭代
                    continue

                else:
                    # 没有工具调用，正常回复
                    final_response = response_text
                    break

        # 保存助手回复
        self.conversation.add_message(MessageRole.ASSISTANT, final_response)

        if not stream or iteration > 1:
            yield final_response

    def _build_messages(self, context: Optional[AgentContext] = None) -> list:
        """构建发送给 LLM 的消息列表"""
        # 构建 system prompt
        context_str = ""
        if context:
            context_str = context.to_context_string()

        system_prompt = build_system_prompt(context_str)

        # 构建消息列表
        messages = [
            {"role": "system", "content": system_prompt}
        ]

        # 添加历史消息
        for msg in self.conversation.messages:
            if msg.role != MessageRole.SYSTEM:
                messages.append(msg.to_llm_format())

        return messages

    def get_conversation_history(self, n: int = 10) -> list:
        """获取最近 n 条对话历史"""
        if not self.conversation:
            return []

        recent = self.conversation.get_recent_messages(n)
        return [
            {
                "role": msg.role.value,
                "content": msg.content,
                "timestamp": msg.timestamp.strftime("%Y-%m-%d %H:%M:%S")
            }
            for msg in recent
        ]

    def clear_conversation(self):
        """清空对话历史"""
        if self.conversation:
            self.conversation.messages.clear()
