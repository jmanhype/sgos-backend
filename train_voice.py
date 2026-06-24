#!/usr/bin/env python3
"""
Train the StraughterG voice profile from the content system skill.
Seeds voice_samples and voice_profiles from SKILL.md + example hooks.
"""
import os
import sys
sys.path.insert(0, os.path.dirname(__file__))

from voice_profile import init_voice_tables, build_voice_profile, analyze_text
from database import get_connection

SKILL_PATH = os.path.expanduser("~/StraughterG-os/skills/straughterg-content/SKILL.md")

# Example posts/hooks extracted from the skill
SAMPLE_POSTS = [
    "Agents do not fail all at once.\nThey drift.\nThen they hallucinate state.\nThen they call the wrong tool with confidence.\nThen everyone calls it \"reasoning failure.\"\nIt was actually a systems failure.",
    "Most agent benchmarks are measuring task completion.\nThey are not measuring survival.\nThat distinction matters because real agents do not run inside clean benchmark boxes.\nThey run through messy tool calls, stale memory, ambiguous goals, and partial failures.",
    "Patch success is not agent survival.\nA model can pass the final test and still be useless as a long-running engineer.\nThe question is not 'can the agent solve the task?' The question is 'does it still know what it is doing after 300 actions?'",
    "People keep treating agent orchestration like a prompt problem. It is a distributed systems problem.",
    "A 1M token context window does not fix bad state management. It just gives the agent more room to get lost.",
    "Most 'autonomous agents' are just while loops with anxiety.",
    "Elixir/OTP is underrated for agents because agents are not functions. They are long-running processes that fail weirdly.",
    "The BEAM already solved problems AI agents are rediscovering badly.",
    "AI video is not a prompt problem anymore. It is a production pipeline problem.",
    "The winning AI video stack will look less like Midjourney and more like a factory floor.",
    "In VAOS, every claim should be treated like a process. It needs supervision, evidence, attack surfaces, and failure handling.",
    "Most agent frameworks track messages. I care about claims, evidence, attacks, and uncertainty.",
    "prompt engineering got us demos. distributed systems gets us agents that don't collapse after 400 tool calls.",
    "everyone is benchmarking patch success. i care whether the agent survives the trajectory.",
    "the missing metric is recovery. passing once matters less than whether the agent can repair its own bad state.",
    "this is why tool use needs a ledger. without one, the agent has no durable memory of what it actually proved.",
    "context length helps, but it does not solve state discipline. a bigger room does not make you less lost.",
    "not a chatbot — a system",
    "agents need supervision, not vibes",
    "prompting is the interface; orchestration is the system",
    "epistemics is the survival layer",
    "benchmark success is not production survival",
    "the agent has to know what it does not know",
    "long context is not state management",
    "hallucination is often a missing ledger problem",
    "the BEAM was built for this kind of chaos",
    "if it cannot recover, it is not autonomous",
    "demos are easy; durable trajectories are hard",
    "the hard part is not the model call",
    "every claim needs evidence",
    "every tool call should leave a trail",
    "agents fail like distributed systems fail: partially, weirdly, and late",
    "LLM orchestration is not a prompt-engineering problem. It is a distributed systems problem.",
    "Agents fail because people treat them like chatbots with tools instead of long-running, stateful, adversarial, partially informed systems.",
    "AI video is not one model. It is a pipeline with failure points.",
    "The uncomfortable truth about agent frameworks: they optimize for the demo, not the deployment.",
]


def train():
    """Train the StraughterG voice profile."""
    print("🎙️  Training StraughterG voice profile...")
    init_voice_tables()

    conn = get_connection()

    # Clear old samples for this profile
    conn.execute("DELETE FROM voice_samples WHERE profile_name=?", ("straughterg",))

    # Insert all samples
    count = 0
    for post in SAMPLE_POSTS:
        analysis = analyze_text(post)
        conn.execute("""
            INSERT INTO voice_samples (profile_name, source, content, word_count, char_count)
            VALUES (?, ?, ?, ?, ?)
        """, ("straughterg", "skill_examples", post, analysis["word_count"], analysis["char_count"]))
        count += 1

    conn.commit()
    print(f"  ✅ Ingested {count} sample posts")

    # Build the profile from all samples
    rows = conn.execute(
        "SELECT content, source FROM voice_samples WHERE profile_name=?",
        ("straughterg",)
    ).fetchall()
    texts = [{"content": r["content"], "source": r["source"]} for r in rows]

    profile = build_voice_profile(
        "straughterg",
        texts,
        description="Straughter Guthrie — AI Distillation Architect, systems engineer, agent infrastructure builder. Direct, technical, implementation-first voice."
    )
    conn.close()

    print(f"  ✅ Profile trained: {profile.get('sample_count', 0)} samples")
    print(f"     Avg word count: {profile.get('avg_word_count', 0):.0f}")
    print(f"     Avg sentence length: {profile.get('avg_sentence_length', 0):.1f}")
    print(f"     Vocabulary richness: {profile.get('vocabulary_richness', 0):.3f}")

    # Store the full SKILL.md as a voice reference for the idea generator
    if os.path.exists(SKILL_PATH):
        with open(SKILL_PATH) as f:
            skill_text = f.read()

        conn = get_connection()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS voice_references (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_name TEXT NOT NULL,
                source TEXT,
                content TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        existing = conn.execute(
            "SELECT id FROM voice_references WHERE profile_name=? AND source=?",
            ("straughterg", "skill_md")
        ).fetchone()
        if existing:
            conn.execute("UPDATE voice_references SET content=? WHERE id=?", (skill_text, existing["id"]))
        else:
            conn.execute(
                "INSERT INTO voice_references (profile_name, source, content) VALUES (?, ?, ?)",
                ("straughterg", "skill_md", skill_text)
            )
        conn.commit()
        conn.close()
        print(f"  ✅ Stored SKILL.md ({len(skill_text)} chars) as voice reference")

    print("\n🎯 Voice profile ready. Use voice='straughterg' in /ideas/generate")
    return profile


if __name__ == "__main__":
    train()
