"""Chat streaming endpoints — SSE for real-time LLM responses.
Thin router — prompt building and streaming delegated to ChatService."""
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from services.chat import chat_service

router = APIRouter(tags=["chat"])


class ChatStreamRequest(BaseModel):
    messages: list[dict]
    action: str | None = None
    platform: str | None = None
    tone: str | None = None
    length: str | None = None
    creativity: float | None = None


@router.post("/chat/stream")
async def chat_stream(request: ChatStreamRequest):
    """
    SSE endpoint for streaming chat responses.
    Client uses EventSource or fetch with ReadableStream.
    """
    system_prompt = chat_service.build_system_prompt(
        action=request.action,
        platform=request.platform,
        tone=request.tone,
        length=request.length,
        creativity=request.creativity,
    )

    temperature = 0.7 if not request.creativity else request.creativity

    def event_generator():
        try:
            yield from chat_service.stream_response(
                messages=request.messages,
                system_prompt=system_prompt,
                temperature=temperature,
            )
        except Exception as e:
            import json
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
