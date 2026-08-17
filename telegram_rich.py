"""
Telegram Rich Message Builder — Uses sendMessage with HTML parse_mode.
Works on ALL Telegram clients (not just those supporting sendRichMessage).

Uses HTML formatting: bold, italic, blockquotes, inline links, pre blocks, code.
Docs: https://core.telegram.org/bots/api#html-style
"""
import json
from datetime import datetime, timezone


def build_brief_html(opps: list[dict], outliers: list[dict],
                     topics: list[dict], shifts: list[dict],
                     recommendation: dict) -> str:
    """
    Build morning brief using HTML for sendMessage.
    Works on every Telegram client version.
    """
    today = datetime.now(timezone.utc).strftime("%B %d, %Y")
    lines = []

    # Header
    lines.append(f"<b>☀️ CREATOR INTELLIGENCE BRIEF — {today}</b>")
    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("")

    # Top Opportunities
    if opps:
        lines.append("<b>🎯 TOP OPPORTUNITIES</b>")
        lines.append("")

        for i, opp in enumerate(opps[:5], 1):
            platform = (opp.get("platform") or "?").upper()
            subreddit = opp.get("subreddit") or "frontpage"
            title = opp.get("post_title") or opp.get("title") or "Untitled"
            z = opp.get("z_score", 0)
            score = opp.get("post_score", 0)
            comments = opp.get("comment_count", 0)
            url = opp.get("url", "")
            hook_type = opp.get("hook_type") or "—"
            date = (opp.get("created_at") or "")[:10]

            # Title with link
            if url:
                lines.append(f'🔥 <b>TOP #{i}</b> — <a href="{url}">{_esc(title[:70])}</a>')
            else:
                lines.append(f"🔥 <b>TOP #{i}</b> — {_esc(title[:70])}")

            lines.append(f"   📱 {platform}/{subreddit} | z={z:.1f} | ⬆️ {score:,} | 💬 {comments:,} | 📅 {date}")

            # Genome analysis as blockquote
            hook_text = opp.get("hook", "")
            emotional_arc = opp.get("emotional_arc", "")

            genome_parts = []
            if hook_type:
                genome_parts.append(f"Hook: {hook_type}")
            if hook_text:
                genome_parts.append(f"> {_esc(hook_text[:120])}")
            if emotional_arc:
                arc_display = str(emotional_arc).replace("[", "").replace("]", "").replace("'", "").replace('"', "")
                genome_parts.append(f"Arc: {arc_display[:80]}")

            if genome_parts:
                lines.append(f"<blockquote>{chr(10).join(genome_parts)}</blockquote>")

            lines.append("")

    # Viral Outliers
    if outliers:
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        lines.append("<b>📡 VIRAL OUTLIERS (24h)</b>")
        lines.append("")

        for post in outliers[:3]:
            z = post.get("z_score", 0)
            emoji = "🔥" if z >= 4 else "📈"
            title = (post.get("title") or "Untitled")[:70]
            platform = (post.get("platform") or "?").upper()
            url = post.get("url", "")

            if url:
                lines.append(f'{emoji} z={z:.1f} — <a href="{url}">{_esc(title)}</a>')
            else:
                lines.append(f"{emoji} z={z:.1f} — {_esc(title)}")

            lines.append(f"<blockquote>{platform} / {post.get('subreddit') or 'frontpage'} | ⬆️ {post.get('score', 0):,} | 💬 {post.get('comment_count', 0):,}</blockquote>")
            lines.append("")

    # Trending Keywords
    if topics:
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        lines.append("<b>🧬 TRENDING KEYWORDS (72h)</b>")
        lines.append("")

        for t in topics[:6]:
            lines.append(f"  • <b>{t['keyword']}</b> (×{t['mentions']}, weight: {t['score']})")

        lines.append("")

    # Meta Shifts
    if shifts:
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        lines.append("<b>📊 META SHIFTS (7d)</b>")
        lines.append("")

        up_shifts = [s for s in shifts if s["direction"] == "up"][:3]
        down_shifts = [s for s in shifts if s["direction"] == "down"][:3]

        if up_shifts:
            lines.append("  🟢 <b>Emerging:</b>")
            for s in up_shifts:
                lines.append(f'  ↑ <b>"{_esc(s["keyword"])}"</b> +{s["change_pct"]}%')

        if down_shifts:
            lines.append("  🔴 <b>Declining:</b>")
            for s in down_shifts:
                lines.append(f'  ↓ <b>"{_esc(s["keyword"])}"</b> -{s["change_pct"]}%')

        lines.append("")

    # Recommendation
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("")
    lines.append("<b>💡 RECOMMENDATION</b>")
    lines.append("")

    if recommendation.get("title"):
        lines.append(f'  Lead with: <b>"{_esc(recommendation["title"][:60])}"</b>')
        lines.append(f"  <i>{_esc(recommendation.get('angle', ''))}</i>")
    elif outliers:
        top = outliers[0]
        lines.append(f'  Lead with: <b>"{_esc((top.get("title") or "")[:60])}"</b>')
        lines.append(f"  <i>This hit z={top.get('z_score', 0):.1f} — add your take</i>")
    else:
        lines.append("  <i>No high-scoring posts. Next ingest will refresh.</i>")

    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"<i>📊 {len(opps)} opportunities | {len(outliers)} outliers | {len(topics)} trending</i>")
    lines.append("")
    lines.append("React: 👍 good | 👎 skip | 🔥 fire")
    lines.append("Commands: /brief | /evolve | /stats")

    return "\n".join(lines)


def _esc(text: str) -> str:
    """Escape HTML special characters for Telegram HTML mode."""
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))


# ─── Rich Message Markdown (sendRichMessage API, Bot API 10.1+) ──────────────

def build_rich_brief_markdown(opps: list[dict], outliers: list[dict],
                               topics: list[dict], shifts: list[dict],
                               recommendation: dict) -> str:
    """
    Build a rich morning brief using Markdown for sendRichMessage.
    Returns the Markdown string. Supports tables, headings, collapsible details.
    Requires Telegram client updated after June 11, 2026.
    """
    today = datetime.now(timezone.utc).strftime("%B %d, %Y")
    lines = []

    # Header
    lines.append("# ☀️ Creator Intelligence Brief")
    lines.append(f"**{today}**")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Top Opportunities Table
    if opps:
        lines.append("## 🎯 Top Opportunities")
        lines.append("")

        lines.append("| # | z-Score | Hook Type | Score | Comments | Date |")
        lines.append("|:--|:-------:|:----------|------:|--------:|:-----|")

        for i, opp in enumerate(opps[:5], 1):
            z = f"{opp.get('z_score', 0):.1f}"
            hook = opp.get("hook_type") or "—"
            score = f"{opp.get('post_score', 0):,}"
            comments = f"{opp.get('comment_count', 0):,}"
            date = (opp.get("created_at") or "")[:10]
            lines.append(f"| {i} | {z} | {hook} | {score} | {comments} | {date} |")

        lines.append("")

        # Each opportunity with expandable details
        for i, opp in enumerate(opps[:5], 1):
            platform = (opp.get("platform") or "?").upper()
            subreddit = opp.get("subreddit") or ""
            title = opp.get("post_title") or opp.get("title") or "Untitled"
            z = opp.get("z_score", 0)
            url = opp.get("url", "")

            if url:
                lines.append(f"**#{i}** [{title[:70]}]({url})")
            else:
                lines.append(f"**#{i}** {title[:70]}")

            lines.append(f"*{platform}/{subreddit} | z={z:.1f} | ⬆️ {opp.get('post_score', 0):,} | 💬 {opp.get('comment_count', 0):,}*")

            # Genome analysis in collapsible block
            hook_text = opp.get("hook", "")
            hook_type = opp.get("hook_type", "")
            emotional_arc = opp.get("emotional_arc", "")
            structural = opp.get("structural_pattern", "")

            if hook_text or hook_type or emotional_arc:
                lines.append("")
                lines.append("<details>")
                lines.append("<summary>🧬 Genome Analysis</summary>")
                lines.append("")
                if hook_type:
                    lines.append(f"**Hook Type:** {hook_type}")
                if hook_text:
                    lines.append(f"> {hook_text[:150]}")
                if emotional_arc:
                    lines.append(f"**Emotional Arc:** {emotional_arc[:100]}")
                if structural:
                    lines.append(f"**Pattern:** {structural[:100]}")
                lines.append("")
                lines.append("</details>")

            lines.append("")

    # Viral Outliers
    if outliers:
        lines.append("---")
        lines.append("")
        lines.append("## 📡 Viral Outliers (24h)")
        lines.append("")

        for post in outliers[:3]:
            z = post.get("z_score", 0)
            emoji = "🔥" if z >= 4 else "📈"
            title = (post.get("title") or "Untitled")[:70]
            platform = (post.get("platform") or "?").upper()
            url = post.get("url", "")

            if url:
                lines.append(f"- {emoji} **z={z:.1f}** — [{title}]({url})")
            else:
                lines.append(f"- {emoji} **z={z:.1f}** — {title}")

            lines.append(f"  > {platform} / {post.get('subreddit') or 'frontpage'} | ⬆️ {post.get('score', 0):,} | 💬 {post.get('comment_count', 0):,}")

        lines.append("")

    # Trending Keywords Table
    if topics:
        lines.append("---")
        lines.append("")
        lines.append("## 🧬 Trending Keywords (72h)")
        lines.append("")
        lines.append("| Keyword | Mentions | Weight |")
        lines.append("|:--------|--------:|-------:|")

        for t in topics[:6]:
            lines.append(f"| {t['keyword']} | {t['mentions']} | {t['score']} |")

        lines.append("")

    # Meta Shifts
    if shifts:
        lines.append("---")
        lines.append("")
        lines.append("## 📊 Meta Shifts (7d)")
        lines.append("")

        up_shifts = [s for s in shifts if s["direction"] == "up"][:3]
        down_shifts = [s for s in shifts if s["direction"] == "down"][:3]

        if up_shifts:
            lines.append("**🟢 Emerging:**")
            for s in up_shifts:
                lines.append(f"- ↑ **\"{s['keyword']}\"** +{s['change_pct']}%")

        if down_shifts:
            lines.append("**🔴 Declining:**")
            for s in down_shifts:
                lines.append(f"- ↓ **\"{s['keyword']}\"** -{s['change_pct']}%")

        lines.append("")

    # Recommendation
    lines.append("---")
    lines.append("")
    lines.append("## 💡 Recommendation")
    lines.append("")

    if recommendation.get("title"):
        lines.append(f"**Lead with:** \"{recommendation['title'][:60]}\"")
        lines.append(f"*{recommendation.get('angle', '')}*")
    elif outliers:
        top = outliers[0]
        lines.append(f"**Lead with:** \"{(top.get('title') or '')[:60]}\"")
        lines.append(f"*This hit z={top.get('z_score', 0):.1f} — add your take*")
    else:
        lines.append("*No high-scoring posts. Next ingest will refresh data.*")

    lines.append("")

    # Footer
    lines.append("---")
    lines.append(f"*📊 {len(opps)} opportunities | {len(outliers)} outliers | {len(topics)} trending*")
    lines.append("")
    lines.append("React: 👍 good | 👎 skip | 🔥 fire")

    return "\n".join(lines)
