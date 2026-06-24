"""Voice profile service — build, retrieve, and use writing style profiles."""
from database import get_connection
from voice_profile import (
    build_voice_profile,
    get_voice_profile,
    generate_voice_prompt,
    list_profiles,
    analyze_text,
)


class VoiceService:
    @staticmethod
    def build_from_author(name: str, description: str = "") -> dict | None:
        """Build a voice profile from all posts by a specific author."""
        conn = get_connection()
        rows = conn.execute(
            "SELECT content, title, platform FROM posts WHERE author=? AND LENGTH(content) > 30",
            (name,),
        ).fetchall()
        conn.close()

        if not rows:
            return None

        texts = [{"content": f"{r['title']} {r['content']}", "source": r["platform"]} for r in rows]
        return build_voice_profile(name, texts, description)

    @staticmethod
    def build_from_texts(name: str, texts: list[str]) -> dict:
        """Build a voice profile from manually provided text samples."""
        samples = [{"content": t, "source": "manual"} for t in texts]
        return build_voice_profile(name, samples, "Manual upload")

    @staticmethod
    def get(name: str) -> dict | None:
        """Get a stored voice profile."""
        return get_voice_profile(name)

    @staticmethod
    def get_prompt(name: str) -> dict | None:
        """Get the system prompt fragment for a voice profile."""
        profile = get_voice_profile(name)
        if not profile:
            return None
        prompt = generate_voice_prompt(profile)
        return {"name": name, "prompt": prompt}

    @staticmethod
    def list_all() -> list:
        """List all voice profiles."""
        return list_profiles()

    @staticmethod
    def analyze(text: str) -> dict:
        """Analyze a single text for style metrics (no storage)."""
        return analyze_text(text)


voice_service = VoiceService()
