"""Chat service — LLM streaming, prompt building, system personality."""
import json


class ChatService:
    @staticmethod
    def build_system_prompt(
        action: str | None = None,
        platform: str | None = None,
        tone: str | None = None,
        length: str | None = None,
        creativity: float | None = None,
    ) -> str:
        """Build system prompt from content generation parameters."""
        base = """You are StraughterG-OS, a creator intelligence AI. You help users generate viral content by analyzing trending outliers and applying proven engagement patterns.

Your strengths:
- Identifying viral hooks and emotional triggers
- Adapting tone across platforms (Twitter, LinkedIn, TikTok, newsletters)
- Using data-driven insights from outlier analysis
- Writing in the user's unique voice when a voice profile is active"""

        if action:
            base += f"\n\nCurrent action: {action.upper()}"

        if platform:
            platform_guides = {
                "twitter": "Format as a Twitter thread: 6-8 posts, each under 280 chars. Hook in post 1, CTA in last.",
                "linkedin": "Format as a LinkedIn post: 200-300 words, professional tone, line breaks for readability, end with a question.",
                "tiktok": "Format as a TikTok script: 60 seconds, hook in first 3 seconds, pattern interrupt at 15s, CTA at end.",
                "newsletter": "Format as a newsletter section: 400-500 words, Context → Insight → Application structure.",
                "carousel": "Format as an Instagram carousel: 8 slides, one insight per slide, CTA on slide 8.",
            }
            base += f"\n\n{platform_guides.get(platform, '')}"

        if tone:
            tone_map = {
                "professional": "Use professional, authoritative language.",
                "casual": "Use casual, conversational language.",
                "witty": "Use witty, clever language with humor.",
                "inspirational": "Use motivational, uplifting language.",
                "controversial": "Use bold, contrarian takes that challenge assumptions.",
            }
            base += f"\n\nTone: {tone_map.get(tone, tone)}"

        if length:
            length_map = {
                "short": "Keep it concise — under 150 words.",
                "medium": "Aim for 200-400 words.",
                "long": "Go deep — 500+ words with detailed examples.",
            }
            base += f"\n\n{length_map.get(length, '')}"

        return base

    @staticmethod
    def stream_response(messages: list[dict], system_prompt: str, temperature: float = 0.7):
        """Generate SSE events from LLM streaming response."""
        from idea_generation import _get_client
        client, model = _get_client()
        if client is None:
            yield {"data": json.dumps({"error": "LLM not configured. Set SGOS_LLM_API_KEY.", "done": True})}
            return

        full_messages = [{"role": "system", "content": system_prompt}]
        for msg in messages:
            full_messages.append({"role": msg.get("role", "user"), "content": msg.get("content", "")})

        stream = client.chat.completions.create(
            model=model,
            messages=full_messages,
            stream=True,
            temperature=temperature,
            max_tokens=2000,
        )

        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                content = chunk.choices[0].delta.content
                yield f"data: {json.dumps({'type': 'content', 'content': content})}\n\n"

        yield f"data: {json.dumps({'type': 'done'})}\n\n"


chat_service = ChatService()
