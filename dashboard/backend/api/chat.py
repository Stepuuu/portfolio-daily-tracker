"""
对话 API 路由
"""
import base64
import io
import json
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

try:
    from PIL import Image as PILImage
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

router = APIRouter()

# 对话历史保存目录
CONVERSATIONS_DIR = Path("data/conversations")
CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)

# 图片保存目录
IMAGES_DIR = Path("data/conversation_images")
IMAGES_DIR.mkdir(parents=True, exist_ok=True)


class ChatRequest(BaseModel):
    """对话请求"""
    message: str
    stream: bool = True
    extract_data: bool = False  # 是否提取结构化数据


class ChatResponse(BaseModel):
    """对话响应"""
    response: str
    suggestions: list = []
    risks: list = []
    sentiment: str = "neutral"
    memory_updates: list = []
    imported_positions: int = 0  # 导入的持仓数量


def get_service():
    """获取 Agent 服务实例"""
    from backend.main import get_agent_service
    service = get_agent_service()
    if service is None:
        raise HTTPException(status_code=503, detail="服务尚未初始化")
    return service


def _save_image_to_disk(image_b64: str, image_type: str, turn_id: str, index: int) -> str:
    """将图片保存到磁盘，返回相对路径"""
    ext = "png"
    if "jpeg" in image_type or "jpg" in image_type:
        ext = "jpg"
    elif "webp" in image_type:
        ext = "webp"
    
    filename = f"{turn_id}_{index}.{ext}"
    filepath = IMAGES_DIR / filename
    
    try:
        raw = base64.b64decode(image_b64)
        # 创建缩略图以节省磁盘空间
        if HAS_PIL:
            img = PILImage.open(io.BytesIO(raw))
            img.thumbnail((800, 800), PILImage.Resampling.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=75)
            filepath = filepath.with_suffix(".jpg")
            filepath.write_bytes(buf.getvalue())
        else:
            filepath.write_bytes(raw)
    except Exception as e:
        print(f"[图片保存] 保存失败: {e}")
        return ""
    
    return str(filepath)


def save_conversation_turn(
    user_message: str, 
    ai_response: str, 
    image_data: Any = None, 
    suggestions: List[Dict] = None, 
    risks: List[Dict] = None
):
    """保存对话记录到文件（含图片路径和建议风险）"""
    today = datetime.now().strftime("%Y-%m-%d")
    log_file = CONVERSATIONS_DIR / f"chat_{today}.jsonl"
    turn_id = datetime.now().strftime("%H%M%S%f")

    # 保存图片到磁盘，记录路径
    image_paths = []
    if isinstance(image_data, list) and image_data:
        for i, img in enumerate(image_data):
            if isinstance(img, dict) and "data" in img:
                path = _save_image_to_disk(img["data"], img.get("type", "image/png"), turn_id, i)
                if path:
                    image_paths.append(path)

    turn = {
        "timestamp": datetime.now().isoformat(),
        "user": user_message,
        "assistant": ai_response,
        "images_count": len(image_paths),
        "image_paths": image_paths,
        "suggestions": suggestions or [],
        "risks": risks or [],
    }

    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(turn, ensure_ascii=False) + "\n")


@router.post("/message")
async def chat_message(request: ChatRequest):
    """
    发送消息并获取回复

    - stream=True: 流式返回（SSE）
    - stream=False: 一次性返回
    - extract_data=True: 同时提取结构化数据
    """
    service = get_service()

    if request.extract_data:
        # 带结构化数据提取
        result = await service.chat_with_extraction(request.message)
        # 保存对话
        save_conversation_turn(request.message, result.get("response", ""))
        return ChatResponse(**result)

    elif request.stream:
        # 流式返回
        async def generate():
            full_response = ""
            async for chunk in service.chat(request.message, stream=True):
                full_response += chunk
                escaped = json.dumps(chunk, ensure_ascii=False)
                yield f"data: {escaped}\n\n"
            yield "data: [DONE]\n\n"
            save_conversation_turn(
                request.message, full_response,
                suggestions=service._recent_suggestions,
                risks=service._recent_risks
            )

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            }
        )

    else:
        # 一次性返回
        full_response = ""
        async for chunk in service.chat(request.message, stream=False):
            full_response += chunk
        # 保存对话
        save_conversation_turn(request.message, full_response)
        return {"response": full_response}


@router.post("/message-with-image")
async def chat_with_image(
    message: str = Form(""),
    images: list[UploadFile] = File(None),
    extract_data: str = Form("true")  # 改为 str 类型接收
):
    """
    带图片的对话

    支持上传多张持仓截图等图片，图片会发送给多模态 LLM 进行分析
    """
    service = get_service()

    # 转换 extract_data 为布尔值
    should_extract = extract_data.lower() in ("true", "1", "yes")

    print(f"[图片上传] message={message[:50]}..., extract_data={should_extract}")

    # 读取所有图片
    image_list = []
    if images:
        for image in images:
            if not image.filename:
                continue
            content = await image.read()
            image_data = base64.b64encode(content).decode("utf-8")
            image_type = image.content_type or "image/png"
            image_list.append({"data": image_data, "type": image_type})
            print(f"[图片上传] 收到图片: {image.filename}, {len(content)} 字节, 类型: {image_type}")

    # 调用带图片的对话
    if image_list:
        result = await service.chat_with_images(
            message=message,
            images=image_list,
            extract_data=should_extract
        )
        print(f"[图片上传] 导入持仓数: {result.get('imported_positions', 0)}")
    else:
        if should_extract:
            result = await service.chat_with_extraction(message)
        else:
            full_response = ""
            async for chunk in service.chat(message, stream=False):
                full_response += chunk
            result = {"response": full_response}

    # 保存对话（含图片与建议）
    save_conversation_turn(
        message, result.get("response", ""), image_list,
        suggestions=result.get("suggestions", []),
        risks=result.get("risks", []),
    )

    if should_extract:
        return ChatResponse(**result)
    else:
        return {"response": result.get("response", "")}


@router.get("/history")
async def get_chat_history():
    """获取对话历史"""
    service = get_service()

    if service.agent and service.agent.conversation:
        messages = service.agent.conversation.messages
        return {
            "history": [
                {
                    "role": msg.role.value if hasattr(msg.role, 'value') else str(msg.role),
                    "content": msg.content,
                    "timestamp": msg.timestamp.isoformat() if hasattr(msg, 'timestamp') else None
                }
                for msg in messages
            ]
        }

    return {"history": []}


@router.delete("/history")
async def clear_chat_history():
    """清空对话历史"""
    service = get_service()

    if service.agent:
        service.agent.start_conversation()  # 重新开始对话

    return {"message": "对话历史已清空"}


@router.get("/conversations")
async def list_conversations():
    """列出所有对话记录文件"""
    files = sorted(CONVERSATIONS_DIR.glob("chat_*.jsonl"), reverse=True)
    return {
        "conversations": [
            {
                "date": f.stem.replace("chat_", ""),
                "file": f.name,
                "size": f.stat().st_size
            }
            for f in files
        ]
    }


@router.get("/conversations/{date}")
async def get_conversation_by_date(date: str):
    """获取指定日期的对话记录"""
    log_file = CONVERSATIONS_DIR / f"chat_{date}.jsonl"

    if not log_file.exists():
        raise HTTPException(status_code=404, detail="对话记录不存在")

    turns = []
    with open(log_file, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                turns.append(json.loads(line))

    returns = {"date": date, "turns": turns}
    return returns

@router.get("/conversation-images/{image_path:path}")
async def serve_conversation_image(image_path: str):
    """提供历史对话中保存的图片"""
    from fastapi.responses import FileResponse
    full_path = Path(image_path)
    if not full_path.exists():
        raise HTTPException(status_code=404, detail="图片不存在")
    return FileResponse(str(full_path), media_type="image/jpeg")


@router.post("/conversations/{date}/load")
async def load_conversation(date: str):
    """加载历史对话到当前会话，同时恢复建议与风险"""
    log_file = CONVERSATIONS_DIR / f"chat_{date}.jsonl"

    if not log_file.exists():
        raise HTTPException(status_code=404, detail="对话记录不存在")

    service = get_service()
    if not service.agent:
        raise HTTPException(status_code=503, detail="Agent服务尚未初始化")

    service.agent.start_conversation()

    turns = []
    last_suggestions = []
    last_risks = []
    with open(log_file, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                turn = json.loads(line)
                turns.append(turn)
                
                from core.models import MessageRole
                service.agent.conversation.add_message(MessageRole.USER, turn.get("user", ""))
                service.agent.conversation.add_message(MessageRole.ASSISTANT, turn.get("assistant", ""))
                
                # 收集最后一轮的 suggestions / risks
                if turn.get("suggestions"):
                    last_suggestions = turn["suggestions"]
                if turn.get("risks"):
                    last_risks = turn["risks"]

    # 同时恢复到服务的状态中 
    service._recent_suggestions = last_suggestions
    service._recent_risks = last_risks

    return {
        "message": "对话已加载", 
        "turns": turns,
        "suggestions": last_suggestions,
        "risks": last_risks,
    }
