"""
交易员 Agent 核心逻辑
"""
from typing import Optional, AsyncIterator, List
from datetime import datetime
import json
import asyncio

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
        self.max_tool_iterations = 5  # Includes the final model response
        self.max_tool_calls = 20
        self.tool_timeout_seconds = 30

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
        """Yield a reply after a bounded multi-round tool conversation.

        Streaming and buffered requests use the same loop. Only the registered
        tools can run, and cancellation propagates to the active model/tool call.
        """
        if not self.conversation:
            self.start_conversation()
        self.conversation.add_message(MessageRole.USER, user_message)
        messages = self._build_messages(context)
        tools = self.tool_executor.get_tool_schemas() if self.tool_executor else None
        transcript = []
        total_calls = 0
        terminal = ""

        for iteration in range(self.max_tool_iterations):
            response_text, tool_calls, stop_reason = "", [], None
            if stream:
                emitted_text = False
                async for chunk in self.llm.chat_stream(messages, tools=tools):
                    if isinstance(chunk, dict):
                        if "stop_reason" in chunk:
                            stop_reason = chunk["stop_reason"]
                        if "tool_calls" in chunk:
                            tool_calls = chunk["tool_calls"] or []
                    elif isinstance(chunk, str):
                        if chunk and not emitted_text:
                            if transcript:
                                yield "\n\n"
                            emitted_text = True
                        response_text += chunk
                        if len(response_text) > 100000:
                            raise RuntimeError("模型回复超过本轮长度限制")
                        yield chunk
                    else:
                        raise RuntimeError("模型流返回了无法识别的数据")
            else:
                response = await self.llm.chat(messages, tools=tools)
                response_text = response.content or ""
                tool_calls = response.tool_calls or []
                stop_reason = response.stop_reason or response.finish_reason
            if stop_reason in {"length", "max_tokens"}:
                raise RuntimeError("模型达到输出长度限制，本轮回复尚未完成")
            if response_text:
                transcript.append(response_text)
            if not tool_calls:
                if not response_text.strip():
                    raise RuntimeError("模型返回空回复，本轮未完成")
                if stop_reason in {"tool_use", "tool_calls"}:
                    raise RuntimeError("模型请求工具，但没有提供完整的工具调用")
                terminal = response_text
                break
            if not self.tool_executor:
                raise RuntimeError("当前对话未配置可执行工具")
            if not isinstance(tool_calls, list):
                raise RuntimeError("工具调用必须是列表")
            if iteration + 1 >= self.max_tool_iterations or total_calls + len(tool_calls) > self.max_tool_calls:
                terminal = "已达到本轮研究工具调用上限，尚未完成全部核查；请缩小问题范围后继续。"
                if stream:
                    yield ("\n\n" if transcript else "") + terminal
                transcript.append(terminal)
                break
            call_ids = set()
            assistant_content = [{"type": "text", "text": response_text}] if response_text else []
            for call in tool_calls:
                if (not isinstance(call, dict) or not isinstance(call.get("id"), str) or not call["id"]
                        or call["id"] in call_ids or not isinstance(call.get("name"), str)
                        or not isinstance(call.get("input", {}), dict)):
                    raise RuntimeError("模型返回了不完整或重复的工具调用")
                call_ids.add(call["id"])
                assistant_content.append({"type": "tool_use", "id": call["id"], "name": call["name"], "input": call.get("input", {})})
            messages.append({"role": "assistant", "content": assistant_content})
            results = []
            for call in tool_calls:
                total_calls += 1
                try:
                    result = await asyncio.wait_for(self.tool_executor.execute_tool(call["name"], call.get("input", {})),
                                                    timeout=self.tool_timeout_seconds)
                except asyncio.TimeoutError:
                    result = {"error": "工具调用超时；本轮未自动重试，请根据已获取的信息说明限制。"}
                except Exception:
                    result = {"error": "工具调用失败；请说明信息缺口，不能把失败当作成功。"}
                try:
                    serialized = json.dumps(result, ensure_ascii=False, allow_nan=False)
                    if len(serialized) > 64000:
                        result = {"error": "工具结果过大，请缩小查询范围。"}
                        serialized = json.dumps(result, ensure_ascii=False)
                except (TypeError, ValueError):
                    result = {"error": "工具没有返回有效的结构化数据。"}
                    serialized = json.dumps(result, ensure_ascii=False)
                results.append({"type": "tool_result", "tool_use_id": call["id"], "content": serialized,
                                "is_error": isinstance(result, dict) and bool(result.get("error"))})
            messages.append({"role": "user", "content": results})
        else:
            terminal = "已达到本轮研究工具调用上限，尚未完成全部核查。"
            transcript.append(terminal)
            if stream:
                yield terminal

        final_response = "\n\n".join(transcript) if stream else terminal
        self.conversation.add_message(MessageRole.ASSISTANT, final_response)
        if not stream:
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
