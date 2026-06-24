"""
Platform Formatters — Convert pipeline opportunities to platform-ready content.

Each formatter takes a ContentVariant and produces platform-specific output:
  - X/Twitter: Auto-numbered thread, 280-char split
  - LinkedIn: Professional formatting with line breaks
  - Newsletter: Markdown-ready snippet
  - Bluesky: 300-char split thread

SOLID:
  - Single Responsibility: Each formatter handles ONE platform.
  - Open/Closed: Add platforms by implementing format() — no changes to existing.
"""
import re
import textwrap
from dataclasses import dataclass


@dataclass
class FormattedPost:
    platform: str
    parts: list[str]  # For threads: one part per post. For single: one element.
    char_counts: list[int]
    total_chars: int
    warnings: list[str]  # e.g., "Part 3 exceeds 280 chars"


class PlatformFormatter:
    """Base class for platform-specific formatters."""
    platform: str = "generic"
    max_chars: int = 10000

    def format(self, content: str, title: str = "", hook: str = "") -> FormattedPost:
        raise NotImplementedError


class XThreadFormatter(PlatformFormatter):
    """
    Formats content as an X/Twitter thread.
    - Auto-numbers parts (1/N, 2/N, ...)
    - Splits at paragraph boundaries when possible
    - Falls back to sentence splitting mid-paragraph
    - Each part ≤ 280 characters (including numbering)
    """
    platform = "x_thread"
    max_chars = 280

    def format(self, content: str, title: str = "", hook: str = "") -> FormattedPost:
        # Clean markdown formatting for Twitter
        text = self._clean_for_twitter(content)

        # Split into logical chunks
        raw_parts = self._split_content(text)

        # Number them
        total = len(raw_parts)
        numbered = []
        for i, part in enumerate(raw_parts, 1):
            prefix = f"{i}/{total} " if total > 1 else ""
            numbered.append(prefix + part.strip())

        # Check char limits
        char_counts = [len(p) for p in numbered]
        warnings = []
        for i, count in enumerate(char_counts):
            if count > self.max_chars:
                warnings.append(f"Part {i+1} is {count} chars (limit {self.max_chars})")

        # Try to re-split overlong parts
        final_parts = []
        for i, part in enumerate(numbered):
            if len(part) > self.max_chars:
                sub_parts = self._split_overlong(part, self.max_chars - 8)  # room for re-numbering
                # Re-number after splitting
                total_adjusted = total + len(sub_parts) - 1
                for j, sp in enumerate(sub_parts):
                    num = i + j + 1
                    final_parts.append(f"{num}/{total_adjusted} {sp.strip()}")
            else:
                final_parts.append(part)

        # Re-number with correct total
        total_final = len(final_parts)
        if total_final > 1:
            final_numbered = []
            for i, part in enumerate(final_parts, 1):
                # Strip old numbering
                cleaned = re.sub(r'^\d+/\d+\s*', '', part)
                final_numbered.append(f"{i}/{total_final} {cleaned}")
            final_parts = final_numbered

        char_counts = [len(p) for p in final_parts]
        warnings = [f"Part {i+1}: {c} chars" + (" ⚠️" if c > self.max_chars else "")
                     for i, c in enumerate(char_counts) if c > self.max_chars * 0.9]

        return FormattedPost(
            platform="x_thread",
            parts=final_parts,
            char_counts=char_counts,
            total_chars=sum(char_counts),
            warnings=warnings,
        )

    @staticmethod
    def _clean_for_twitter(text: str) -> str:
        """Strip markdown formatting that doesn't work on Twitter."""
        text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)  # Bold
        text = re.sub(r'\*(.*?)\*', r'\1', text)      # Italic
        text = re.sub(r'__(.*?)__', r'\1', text)       # Bold underline
        text = re.sub(r'`(.*?)`', r'\1', text)         # Code
        text = re.sub(r'^#{1,6}\s*', '', text, flags=re.MULTILINE)  # Headers
        text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)  # Links → text
        return text.strip()

    @staticmethod
    def _split_content(text: str, max_chars: int = 265) -> list[str]:
        """Split content at paragraph boundaries, then sentences if needed."""
        paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]

        parts = []
        current = ""

        for para in paragraphs:
            if not para:
                continue

            # Skip bullet-only paragraphs — merge into current
            if len(current) + len(para) + 2 <= max_chars:
                current = f"{current}\n\n{para}".strip() if current else para
            else:
                if current:
                    parts.append(current)
                # If paragraph itself is too long, split by sentences
                if len(para) > max_chars:
                    sentence_parts = XThreadFormatter._split_by_sentences(para, max_chars)
                    parts.extend(sentence_parts[:-1])
                    current = sentence_parts[-1] if sentence_parts else ""
                else:
                    current = para

        if current:
            parts.append(current)

        return parts if parts else [text[:max_chars]]

    @staticmethod
    def _split_by_sentences(text: str, max_chars: int) -> list[str]:
        """Split text by sentences, keeping each under max_chars."""
        sentences = re.split(r'(?<=[.!?])\s+', text)
        parts = []
        current = ""

        for sent in sentences:
            if len(current) + len(sent) + 1 <= max_chars:
                current = f"{current} {sent}".strip() if current else sent
            else:
                if current:
                    parts.append(current)
                current = sent

        if current:
            parts.append(current)

        return parts if parts else [text]

    @staticmethod
    def _split_overlong(text: str, max_chars: int) -> list[str]:
        """Force-split overlong text at word boundaries."""
        words = text.split()
        parts = []
        current = ""

        for word in words:
            if len(current) + len(word) + 1 <= max_chars:
                current = f"{current} {word}".strip() if current else word
            else:
                if current:
                    parts.append(current)
                current = word

        if current:
            parts.append(current)

        return parts if parts else [text[:max_chars]]


class LinkedInFormatter(PlatformFormatter):
    """
    Formats content for LinkedIn.
    - Preserves paragraph structure
    - Adds professional spacing
    - Strips Twitter-specific formatting (1/N)
    - Keeps under 3000 chars (LinkedIn limit)
    """
    platform = "linkedin"
    max_chars = 3000

    def format(self, content: str, title: str = "", hook: str = "") -> FormattedPost:
        text = content.strip()

        # Remove thread numbering
        text = re.sub(r'^\d+/\d+\s*', '', text, flags=re.MULTILINE)

        # Ensure professional formatting
        if title and not text.startswith(title):
            text = f"{title}\n\n{text}"

        # Add hook as first line if strong enough
        if hook and not text.startswith(hook):
            text = f"{hook}\n\n{text}"

        # LinkedIn loves line breaks — ensure paragraphs have spacing
        text = re.sub(r'\n(?!\n)', '\n', text)  # Normalize single newlines
        text = re.sub(r'\n{3,}', '\n\n', text)  # Cap at double newlines

        # Trim to limit
        if len(text) > self.max_chars:
            text = text[:self.max_chars - 3].rsplit(' ', 1)[0] + "..."

        warnings = []
        if len(text) > self.max_chars * 0.9:
            warnings.append(f"Approaching LinkedIn limit ({len(text)}/{self.max_chars} chars)")

        return FormattedPost(
            platform="linkedin",
            parts=[text],
            char_counts=[len(text)],
            total_chars=len(text),
            warnings=warnings,
        )


class BlueskyFormatter(PlatformFormatter):
    """Formats for Bluesky (300 char limit per post)."""
    platform = "bluesky"
    max_chars = 300

    def format(self, content: str, title: str = "", hook: str = "") -> FormattedPost:
        # Use X formatter with different char limit
        x_formatter = XThreadFormatter()
        x_formatter.max_chars = 290  # Leave room for numbering
        result = x_formatter.format(content, title, hook)
        result.platform = "bluesky"
        return result


class NewsletterFormatter(PlatformFormatter):
    """Formats as a newsletter-ready markdown snippet."""
    platform = "newsletter"
    max_chars = 5000

    def format(self, content: str, title: str = "", hook: str = "") -> FormattedPost:
        parts = []

        if title:
            parts.append(f"## {title}\n")

        if hook:
            parts.append(f"> {hook}\n")

        # Keep markdown formatting intact
        parts.append(content.strip())

        text = "\n".join(parts)

        return FormattedPost(
            platform="newsletter",
            parts=[text],
            char_counts=[len(text)],
            total_chars=len(text),
            warnings=[],
        )


# ─── Formatter Registry ────────────────────────────────────────────────────

FORMATTERS: dict[str, PlatformFormatter] = {
    "x": XThreadFormatter(),
    "x_thread": XThreadFormatter(),
    "twitter": XThreadFormatter(),
    "linkedin": LinkedInFormatter(),
    "bluesky": BlueskyFormatter(),
    "newsletter": NewsletterFormatter(),
}


def format_for_platform(content: str, platform: str, title: str = "", hook: str = "") -> FormattedPost:
    """Format content for a specific platform. Falls back to generic if unknown."""
    formatter = FORMATTERS.get(platform.lower())
    if not formatter:
        # Generic fallback — just clean and return
        return FormattedPost(
            platform=platform,
            parts=[content],
            char_counts=[len(content)],
            total_chars=len(content),
            warnings=[f"Unknown platform '{platform}', returned as-is"],
        )
    return formatter.format(content, title, hook)


def format_opportunity(opp: dict, platform: str) -> FormattedPost:
    """Format a pipeline opportunity dict for a platform."""
    return format_for_platform(
        content=opp.get("content", ""),
        platform=platform,
        title=opp.get("title", ""),
        hook=opp.get("hook", ""),
    )
