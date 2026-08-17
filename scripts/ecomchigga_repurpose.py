#!/usr/bin/env python3
"""
Multi-account repurpose pipeline — scrapes long-form tweets and threads
from target accounts, rewrites them in StraughterG's voice, and posts
as threads via ego-browser CDP.

Uses:
- bird CLI to scrape (read-only, fast)
- Aliyun LLM to rewrite as threads
- ego-browser CDP to post (matches post_with_ego.py pattern)
"""
import json
import re
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional
import urllib.request
import urllib.parse
from urllib.parse import quote_plus

# Load .env for Telegram config
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env", override=True)
except ImportError:
    pass

BIRD_BIN = "bird"
TARGET_HANDLES = [
    "ecomchigga",      # AI automation, SaaS, practical
    "coreyganim",      # AI tools, workflows, no-BS
    "levelsio",        # Pieter Levels - indie hacker, AI tools
    "shpigford",       # Josh Pigford - SaaS, automation
    "swyx",            # Shawn Wang - AI/ML, developer tools
    "karpathy",        # Andrej Karpathy - AI/ML, technical but practical
    "lexfridman",      # Lex Fridman - AI, research
]
MAX_TWEETS_TO_SCRAPE = 15
EGO_BROWSER = "/Users/speed/.local/bin/ego-browser"

# Grounding search endpoints
SEARXNG_URL = "http://localhost:4004"
FIRECRAWL_URL = "http://localhost:3005/v1"
GROUNDING_TIMEOUT = 15

# Rate limiting
MIN_MINUTES_BETWEEN_POSTS = 45
SECONDS_BETWEEN_THREAD_REPLIES = 20


def send_alert(message: str):
    """Send Telegram alert on failure."""
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    
    if not bot_token or not chat_id:
        print(f"⚠️  Alert not sent (no Telegram config): {message[:100]}")
        return
    
    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": chat_id,
            "text": f"🚨 *Repurpose Pipeline Alert*\n\n{message}",
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }).encode()
        
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(f"📱 Alert sent to Telegram")
    except Exception as e:
        print(f"⚠️  Failed to send alert: {e}")

# Rate limiting
MIN_MINUTES_BETWEEN_POSTS = 45
SECONDS_BETWEEN_THREAD_REPLIES = 20


def run_bird(args: list[str], timeout: int = 30) -> tuple[int, str, str]:
    """Run bird CLI command and return (returncode, stdout, stderr)."""
    cmd = [BIRD_BIN] + args
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return 1, "", f"Command timed out after {timeout}s"
    except FileNotFoundError:
        return 1, "", f"bird binary not found at {BIRD_BIN}"


def scrape_recent_tweets(handle: str, count: int = MAX_TWEETS_TO_SCRAPE) -> list[dict]:
    """Scrape recent tweets from a handle using bird."""
    print(f"📡 Scraping {count} recent tweets from @{handle}...")
    
    rc, stdout, stderr = run_bird([
        "user-tweets", handle,
        "--count", str(count),
        "--json"
    ], timeout=60)
    
    if rc != 0:
        print(f"❌ bird failed: {stderr}")
        return []
    
    # bird outputs warnings to stdout before JSON — find the array
    start = stdout.find("[")
    if start == -1:
        print(f"❌ No JSON array found in output")
        return []
    
    try:
        tweets = json.loads(stdout[start:])
        print(f"✅ Scraped {len(tweets)} tweets")
        return tweets
    except json.JSONDecodeError as e:
        print(f"❌ Failed to parse JSON: {e}")
        return []


def fetch_full_thread(first_tweet_url: str) -> list[dict]:
    """Fetch the complete thread using bird thread command."""
    rc, stdout, stderr = run_bird([
        "thread", first_tweet_url,
        "--json"
    ], timeout=60)
    
    if rc != 0:
        print(f"  ⚠️ Failed to fetch full thread: {stderr}")
        return []
    
    # Find JSON array in output
    start = stdout.find("[")
    if start == -1:
        return []
    
    try:
        tweets = json.loads(stdout[start:])
        # Filter to only author's tweets (exclude replies from others)
        author_tweets = [t for t in tweets if t.get("isReply", False) == False or 
                        t.get("author", {}).get("username") == tweets[0].get("author", {}).get("username")]
        return author_tweets
    except (json.JSONDecodeError, KeyError):
        return []


def group_threads(tweets: list[dict]) -> list[dict]:
    """Group tweets by conversationId to detect threads. Returns list of thread objects."""
    convs: dict[str, list[dict]] = {}
    for t in tweets:
        cid = t.get("conversationId", t["id"])
        if cid not in convs:
            convs[cid] = []
        convs[cid].append(t)
    
    threads = []
    for cid, tws in convs.items():
        # Sort by creation time
        tws.sort(key=lambda x: x["createdAt"])
        first = tws[0]
        
        # If this looks like a thread (multiple tweets), fetch the FULL thread
        if len(tws) > 1:
            print(f"  🔍 Detected thread with {len(tws)} tweets, fetching full thread...")
            first_url = f"https://x.com/{first.get('author', {}).get('username', 'user')}/status/{first['id']}"
            full_thread = fetch_full_thread(first_url)
            if len(full_thread) > len(tws):
                print(f"  ✅ Fetched {len(full_thread)} tweets (was {len(tws)})")
                tws = full_thread
                first = tws[0]
            else:
                print(f"  ℹ️ Kept {len(tws)} tweets (full fetch returned {len(full_thread)})")
        
        total_chars = sum(len(t["text"]) for t in tws)
        has_media = any(t.get("media") for t in tws)
        
        threads.append({
            "id": first["id"],
            "conversation_id": cid,
            "tweets": tws,
            "tweet_count": len(tws),
            "total_chars": total_chars,
            "first_text": first["text"],
            "source_handle": first.get("source_handle", "unknown"),
            "author": first.get("author", {}).get("username", "unknown"),
            "likes": first.get("likeCount", 0),
            "retweets": first.get("retweetCount", 0),
            "created_at": first["createdAt"],
            "has_media": has_media,
            "is_thread": len(tws) > 1,
            "is_long_single": len(first["text"]) > 500,
        })
    
    # Sort by date (newest first)
    threads.sort(key=lambda x: x["created_at"], reverse=True)
    return threads


def filter_quality_threads(threads: list[dict]) -> list[dict]:
    """Filter to substantive threads and long-form posts. Skip RTs and short junk."""
    filtered = []
    for th in threads:
        first = th["first_text"]
        
        # Skip retweets
        if first.strip().startswith("RT @"):
            continue
        
        # Skip very short tweets (under 200 chars and not a thread)
        if th["total_chars"] < 200 and not th["is_thread"]:
            continue
        
        # Skip replies (start with @ and are short)
        if first.startswith("@") and th["total_chars"] < 300:
            continue
        
        # Prioritize: threads > long singles > medium singles
        if th["is_thread"]:
            th["priority"] = 3  # threads first
        elif th["is_long_single"]:
            th["priority"] = 2  # long posts second
        elif th["total_chars"] > 200:
            th["priority"] = 1  # decent singles last
        else:
            continue
        
        # Boost by engagement
        th["priority"] += min(th["likes"] / 50, 2)  # up to +2 for viral posts
        
        filtered.append(th)
    
    # Sort by priority (highest first)
    filtered.sort(key=lambda x: x["priority"], reverse=True)
    
    print(f"🎯 Filtered to {len(filtered)} quality threads/posts")
    for th in filtered:
        kind = "🧵 THREAD" if th["is_thread"] else "📝 LONG POST" if th["is_long_single"] else "📝 POST"
        print(f"   {kind} ({th['tweet_count']} tweets, {th['total_chars']} chars, ❤️{th['likes']}) from @{th['source_handle']}")
    
    return filtered


def get_voice_profile() -> tuple[str, str]:
    """Return StraughterG's voice style."""
    style_guide = """Direct, conversational hot-takes. No-BS tone.
- Open with a contrarian hook or surprising fact
- Use short sentences. Mix in fragments for rhythm.
- Concrete numbers and specifics beat vague claims
- No corporate speak, no AI slop (delve, tapestry, leverage, unlock, harness)
- No em dashes. Use periods or semicolons instead.
- Sound like you're explaining to a friend, not lecturing
- Numbered points work well for threads (1/, 2/, etc.)
- End threads with a punch line, CTA, or engagement bait"""
    return "straughterg", style_guide


def _firecrawl_search(query: str, limit: int = 3) -> list[dict]:
    """Search via local Firecrawl for web context."""
    try:
        req = urllib.request.Request(
            f"{FIRECRAWL_URL}/search",
            data=json.dumps({"query": query, "limit": limit}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=GROUNDING_TIMEOUT) as resp:
            data = json.loads(resp.read())
            if data.get("success") and data.get("data"):
                return [
                    {
                        "title": item.get("title", ""),
                        "url": item.get("url", ""),
                        "snippet": item.get("description", "")[:300],
                        "source": "firecrawl",
                    }
                    for item in data["data"][:limit]
                ]
    except Exception as e:
        print(f"  [warn] Firecrawl search failed: {e}")
    return []


def _searxng_search(query: str, limit: int = 3) -> list[dict]:
    """Search via local SearXNG meta-search engine."""
    try:
        url = f"{SEARXNG_URL}/search?q={quote_plus(query)}&format=json&engines=google,duckduckgo"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=GROUNDING_TIMEOUT) as resp:
            data = json.loads(resp.read())
            results = data.get("results", [])
            return [
                {
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "snippet": item.get("content", "")[:300],
                    "source": "searxng",
                }
                for item in results[:limit]
            ]
    except Exception as e:
        print(f"  [warn] SearXNG search failed: {e}")
    return []


def enrich_grounding(text: str) -> str:
    """Fetch broader web context to ground the repurposed content.
    
    Uses entity extraction to build targeted queries instead of random word extraction.
    Extracts:
    - Tool/product names (Claude, Zapier, Notion, etc.)
    - Technical concepts (AI Second Brain, knowledge management, etc.)
    - Specific claims (numbers, timeframes, prices)
    """
    
    # Known tool/product names to extract
    known_tools = {
        'claude', 'chatgpt', 'gpt-4', 'gpt-3', 'zapier', 'notion', 'obsidian',
        'logseq', 'roam', 'github', 'slack', 'discord', 'cursor', 'copilot',
        'gemini', 'llama', 'mistral', 'anthropic', 'openai', 'cowork', 'sift'
    }
    
    # Extract tool names
    found_tools = []
    text_lower = text.lower()
    for tool in known_tools:
        if tool in text_lower:
            found_tools.append(tool.capitalize())
    
    # Extract technical concepts (2-3 word phrases)
    concept_patterns = [
        r'AI [A-Z][a-z]+ [A-Z][a-z]+',  # AI Second Brain, AI Knowledge Base
        r'[A-Z][a-z]+ [A-Z][a-z]+ [A-Z][a-z]+',  # Three-word concepts
        r'[A-Z][a-z]+ [A-Z][a-z]+',  # Two-word concepts
    ]
    
    concepts = []
    for pattern in concept_patterns:
        matches = re.findall(pattern, text)
        concepts.extend(matches[:3])  # Limit per pattern
    
    # Extract numbers and claims
    number_patterns = [
        r'\$\d+[,\d]*',  # Prices: $20, $3,500
        r'\d+ (minutes|hours|days|weeks|months)',  # Timeframes
        r'\d+x',  # Multipliers: 3.2x, 9x
        r'\d+\.\d+%',  # Percentages: 99.9%
    ]
    
    numbers = []
    for pattern in number_patterns:
        matches = re.findall(pattern, text)
        numbers.extend(matches[:2])
    
    # Build targeted queries
    queries = []
    
    # Query 1: Tool-based (if we found tools)
    if found_tools:
        tool_query = " ".join(found_tools[:2]) + " automation workflow"
        queries.append(tool_query)
    
    # Query 2: Concept-based (if we found concepts)
    if concepts:
        concept_query = concepts[0] + " best practices"
        queries.append(concept_query)
    
    # Query 3: Problem-solution based
    # Extract the main problem being solved
    problem_keywords = ['knowledge management', 'second brain', 'automation', 'workflow', 'productivity']
    for keyword in problem_keywords:
        if keyword in text_lower:
            queries.append(f"{keyword} tools comparison 2024")
            break
    
    # Fallback: use first 3 sentences
    if not queries:
        sentences = text.split('.')[:3]
        queries.append(' '.join(sentences)[:100])
    
    print(f"  🔍 Grounding: {len(queries)} targeted queries")
    for i, q in enumerate(queries[:3], 1):
        print(f"    {i}. '{q}'")
    
    # Execute queries
    all_results = []
    for query in queries[:3]:  # Limit to 3 queries
        fc_results = _firecrawl_search(query, limit=2)
        sx_results = _searxng_search(query, limit=2)
        all_results.extend(fc_results + sx_results)
    
    # Deduplicate by URL, filter out X/Twitter
    seen_urls = set()
    merged = []
    for item in all_results:
        url = item.get("url", "")
        # Skip X/Twitter pages
        if "/status/" not in url and ("x.com" in url or "twitter.com" in url):
            continue
        # Normalize URL
        norm_url = re.sub(r'https?://', '', url).rstrip('/')
        if norm_url and norm_url not in seen_urls:
            seen_urls.add(norm_url)
            merged.append(item)
    
    if merged:
        print(f"  ✅ Found {len(merged)} grounding sources")
        return json.dumps(merged[:5])  # Top 5
    else:
        print(f"  ⚠️ No grounding sources found")
        return ""


def score_candidate(candidate: dict) -> int:
    """Score a candidate based on quality criteria.
    
    Scoring (0-400):
    - Character count: 100 points max (300-380 chars per tweet, aim 340-380)
    - Format compliance: 100 points max (bullet lists, ALL CAPS, code paths)
    - Hook strength: 100 points max (contrarian, surprising, specific)
    - Specificity: 100 points max (numbers, tools, timeframes, examples)
    """
    import re as _re
    
    if candidate.get("format") == "long":
        text = candidate.get("text", "")
        score = 0
        if 2000 <= len(text) <= 4000:
            score += 100
        elif 1000 <= len(text) <= 5000:
            score += 50
        if _re.search(r'^#+\s', text, _re.MULTILINE):
            score += 50
        if _re.search(r'^\s*[-*]\s', text, _re.MULTILINE):
            score += 50
        return score
    
    tweets = candidate.get("tweets", [])
    if not tweets:
        return 0
    
    score = 0
    full_text = "\n".join(tweets)
    
    # Character count (100 pts)
    char_score = 0
    for tweet in tweets:
        cc = len(tweet)
        if 340 <= cc <= 380:
            char_score += 20
        elif 300 <= cc <= 380:
            char_score += 15
        elif 280 <= cc <= 400:
            char_score += 5
    score += min(100, int(char_score / len(tweets) * 5))
    
    # Format compliance (100 pts)
    fmt = 0
    if _re.search(r'^\s*-\w', full_text, _re.MULTILINE):
        fmt += 30
    if _re.search(r'\b(NEVER|NOT|MUST|DO NOT)\b', full_text):
        fmt += 30
    if _re.search(r'\b\w+/\w*/?|\b\w+\.md\b', full_text):
        fmt += 20
    if '"' in full_text or "'" in full_text:
        fmt += 20
    score += min(100, fmt)
    
    # Hook strength (100 pts)
    hook = 0
    first = tweets[0]
    if _re.search(r'most people|everyone thinks|nobody realizes|99% of', first, _re.IGNORECASE):
        hook += 40
    if _re.search(r'\d+', first):
        hook += 30
    first_sent = first.split('.')[0]
    if len(first_sent) < 100:
        hook += 30
    score += min(100, hook)
    
    # Specificity (100 pts)
    spec = 0
    if _re.search(r'\$\d+|\d+ (minutes|hours|days|weeks|months)|\d+\.\d+%', full_text):
        spec += 30
    if _re.search(r'\b(Claude|Zapier|Notion|Cowork|GitHub|Slack)\b', full_text):
        spec += 30
    if _re.search(r'\d+ (month|week|day|year)s? ago', full_text, _re.IGNORECASE):
        spec += 20
    if _re.search(r'\$\d+[,\d]*|saved \d+ hours|\d+x engagement', full_text):
        spec += 20
    score += min(100, spec)
    
    return score


def generate_single_candidate(prompt: str, temperature: float, api_url: str, api_key: str, model: str) -> Optional[dict]:
    """Generate a single candidate with the given temperature."""
    import re as _re
    
    try:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": "You are a thread writer for X/Twitter. Output ONLY the thread tweets, one per line. No commentary."},
                {"role": "user", "content": prompt}
            ],
            "temperature": temperature,
            "max_tokens": 2000
        }
        
        if os.environ.get("SGOS_LLM_ENABLE_THINKING", "").lower() == "true":
            payload["enable_thinking"] = True
        
        req = urllib.request.Request(
            f"{api_url}/chat/completions",
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}"
            },
            method="POST"
        )
        
        with urllib.request.urlopen(req, timeout=300) as response:
            result = json.loads(response.read())
            raw_output = result["choices"][0]["message"]["content"].strip()
        
        raw_output = raw_output.replace("**", "").replace("*", "")
        
        if raw_output.upper().startswith("LONG:"):
            text = raw_output[5:].strip()
            text = _re.sub(r'^#+\s*', '', text, flags=_re.MULTILINE)
            text = _re.sub(r'https?://\S+', '', text)
            text = _re.sub(r'Grab the full guide here:?', '', text)
            text = _re.sub(r'Check out the full \w+ here:?', '', text)
            text = _re.sub(r'\s+', ' ', text).strip()
            if len(text) > 5000:
                text = text[:4997] + "..."
            return {"format": "long", "text": text, "char_count": len(text)}
        
        elif raw_output.upper().startswith("THREAD:"):
            content = raw_output[7:].strip()
        else:
            if "---" in raw_output:
                content = raw_output
            else:
                lines = [l.strip() for l in raw_output.split("\n") if l.strip()]
                if len(lines) >= 2 and all(len(l) <= 280 for l in lines):
                    content = raw_output
                else:
                    return {"format": "long", "text": raw_output, "char_count": len(raw_output)}
        
        if "---" in content:
            raw_tweets = content.split("---")
        else:
            raw_tweets = _re.split(r'\n\s*\n\s*\n', content)
        
        tweets = []
        for raw_tweet in raw_tweets:
            tweet = raw_tweet.strip()
            tweet = tweet.replace("**", "").replace("*", "")
            tweet = _re.sub(r'^[\d]+[\.\)]\s*', '', tweet)
            tweet = _re.sub(r'^Tweet\s*\d+[:\.\)]?\s*', '', tweet, flags=_re.IGNORECASE)
            tweet = _re.sub(r'^\d+/\d+\s*', '', tweet)
            tweet = _re.sub(r'https?://\S+', '', tweet)
            tweet = _re.sub(r'Grab the full guide here:?', '', tweet)
            tweet = tweet.strip()
            if tweet:
                tweets.append(tweet)
        
        if not tweets:
            return None
        
        return {"format": "thread", "tweets": tweets, "char_counts": [len(t) for t in tweets]}
    
    except Exception as e:
        print(f"  ⚠️  Candidate generation failed (temp={temperature}): {e}")
        return None


def repurpose_as_thread(thread_data: dict, voice_name: str, voice_prompt: str) -> Optional[dict]:
    """Rewrite a long post or thread as EITHER a thread OR a long-form post.
    
    Returns dict with:
      - format: "thread" or "long"
      - tweets: list[str] (for threads)
      - text: str (for long posts)
    """
    
    # Build the full original text
    if thread_data["is_thread"]:
        original_text = "\n\n---\n\n".join(t["text"] for t in thread_data["tweets"])
    else:
        original_text = thread_data["first_text"]
    
    source = thread_data["source_handle"]
    
    # Ground with real-world context from SearXNG + Firecrawl
    print(f"🌐 Fetching grounding context for @{source} content...")
    grounding = enrich_grounding(original_text)
    
    grounding_section = ""
    if grounding:
        try:
            sources = json.loads(grounding)
            grounding_lines = []
            for s in sources:
                grounding_lines.append(f"- {s.get('title', 'Untitled')}: {s.get('snippet', '')} (source: {s.get('source', 'web')})")
            grounding_section = f"""

GROUNDING CONTEXT (real-world facts to enrich your content):
{chr(10).join(grounding_lines)}

Use these facts to add specific details, stats, or context that make the content feel more informed and credible. Do NOT contradict the original content."""
        except json.JSONDecodeError:
            pass
    
    # Get API credentials
    api_url = os.environ.get("SGOS_LLM_BASE_URL")
    api_key = os.environ.get("SGOS_LLM_API_KEY")
    model = os.environ.get("SGOS_LLM_MODEL", "qwen-latest-series-invite-beta-v34")
    
    if not api_url or not api_key:
        print("❌ SGOS_LLM_BASE_URL or SGOS_LLM_API_KEY not set")
        return None
    
    prompt = f"""You are rewriting a long-form post/thread from @{source} into content for @{voice_name}'s X/Twitter.
The account has X Premium, so you can post EITHER a thread (multiple tweets 280-380 chars each) OR a single long-form post (up to 5000 chars).

CRITICAL RULES:
- PRESERVE the original topic and ALL core insights. Do NOT change the subject matter.
- Only change the VOICE and STYLE of delivery.
- NO CTA links. No "grab it here" or "link in bio" nonsense.

Voice style guide (how to write, NOT what to write about):
{voice_prompt}

Original content (by @{source}, {thread_data['total_chars']} chars):
{original_text}
{grounding_section}

CHOOSE THE BEST FORMAT:
- Use THREAD if: the content is a list, step-by-step, or has clear numbered sections that work as standalone tweets
- Use LONG POST if: the content is an essay, rant, personal story, or argument that flows better as one piece

FORMAT: Start your response with either "THREAD:" or "LONG:" on its own line, then the content.

THREAD format: One tweet per line after "THREAD:". Each tweet MUST be 300-380 characters — this is critical, count them! Aim for the HIGHER end (340-380). Pack detail into each tweet. Use \\n\\n (double newline) WITHIN each tweet to create line breaks. Example:
THREAD:
Hook line here that sets up the topic.

-Bullet point with detail
-Another bullet point
-Third bullet point

Closing line that ties it together.
---
Second tweet here that introduces a concept.

raw/ = what this folder does
wiki/ = what this folder does
outputs/ = what this folder does
schema.md = what this file does

This division of labor is why it works so well.
---
Third tweet here with a rule.

You NEVER do X manually.

That's the AI's job. You curate sources. It summarizes, files, and cross-references.

Division of labor is what makes this system work so well.
---
Fourth tweet with operations.

Three operations run everything:

- Ingest: add sources to the wiki
- Query: ask the wiki anything
- Lint: audit the wiki for contradictions and stale info
---
Fifth tweet with a warning.

Do NOT skip the monthly audit.

Failure to audit your system turns 1 wrong fact into 90 wrong answers 3 months later.

You must run the Lint command monthly to keep the system clean and prune outdated facts.
---
Sixth tweet with examples.

Setup time: 45 minutes
Cost: $20/month Claude subscription

Once you build it for yourself, ask it things like:

"What are our biggest missed opportunities?"
"What should our top 3 priorities be in Q3?"
"What can we do to immediately increase revenue?"

You'll get insights you never would've found on your own.
---
Seventh tweet wrapping up.

This is the 80/20 version that gets you a working system in under an hour.

Most people overcomplicate this. They try to build the perfect knowledge base before starting.

Just start. Let the AI organize. You focus on feeding it good sources.

Use --- on its own line to separate tweets. Each tweet MUST have at least 2 line breaks inside it.

LONG format: Use DOUBLE LINE BREAKS (\\n\\n) between sections. Structure:
  1. Hook (income claim or bold statement)
  2. [blank line]
  3. Bullet list of offers/tiers (single line breaks between items)
  4. [blank line]
  5. Social proof statement
  6. [blank line]
  7. Setup line ending with colon
  8. [blank line]
  9. Numbered list: 1), 2), 3) format - each point is a FULL PARAGRAPH with specific details
  10. [blank line between each numbered point]
  11. Section break: "Two things that make this work:"
  12. [blank line]
  13. Another numbered list for key takeaways
  14. [blank line]
  15. Closing line

Rules for both:
1. No AI slop words (delve, tapestry, leverage, unlock, harness)
2. No em dashes. Use periods or semicolons.
3. Sound like a real person who does this stuff, not a guru
4. Keep the SAME information density as the original
5. Open with the most surprising or contrarian point
6. End with a punch line or principle statement (no CTA links)
7. NEVER include any URLs, links, or @mentions from the original content. Write as if those don't exist.

FORMATTING RULES (from Corey Ganim analysis - this is how high-performing threads are structured):
8. Use bullet lists for scannable content: -item format, not "item 1", "item 2"
9. Use code-style formatting for technical terms: raw/, wiki/, outputs/, CLAUDE.md, schema.md
10. Include quoted examples: "What are our biggest missed opportunities?" in quotes
11. Use ALL CAPS for emphasis on rules: NEVER, NOT, MUST, DO NOT
12. Pack 3-4 points per tweet, not just one idea per tweet
13. Each tweet should be 300-380 characters (count them!). Aim for 340-380. If under 300, add more detail. If over 380, split.
14. Keep the casual voice with intentional imperfections (like "since since" in the original)
15. Use specific numbers: "$20 a month", "45 minutes", "99.9% margin", not "cheap", "quick", "high"
16. Name specific tools: Claude, Cowork, Zapier, Notion, not "AI tools", "automation software"
17. Give real examples with dollar amounts: "A $3,500 process optimization", "saved 20 hours a month"
18. Use concrete timeframes: match the EXACT timeframe from the source. If source says "1 month", write "1 month" — never convert to "30 days" or approximate
19. Show before/after or comparisons: "Before: 500 views. After: 3,900 views"
20. Keep the exact pricing, tool names, and numbers from the original. Don't abstract them.
21. Contrarian hooks get 3.2x engagement - lead with the most surprising claim
22. Each tweet in a thread must stand alone as a complete thought
23. Use short sentences for emphasis, but pack detail into the tweet overall"""

    # Generate 3 candidates with different temperatures
    print("🎲 Generating 3 candidates with different temperatures...")
    candidates = []
    temperatures = [0.5, 0.7, 0.9]
    
    for temp in temperatures:
        print(f"  Generating candidate (temp={temp})...")
        candidate = generate_single_candidate(prompt, temp, api_url, api_key, model)
        if candidate:
            score = score_candidate(candidate)
            candidate["score"] = score
            candidate["temperature"] = temp
            candidates.append(candidate)
            print(f"    ✓ Score: {score}/400")
        else:
            print(f"    ✗ Failed")
    
    if not candidates:
        print("❌ All candidates failed to generate")
        return None
    
    # Pick the best candidate
    best = max(candidates, key=lambda c: c["score"])
    print(f"🏆 Selected candidate with score {best['score']}/400 (temp={best['temperature']})")
    
    # For backward compat, re-parse the best candidate's raw output through the same pipeline
    # Actually, generate_single_candidate already parsed it, so just return best
    return best
    
    # --- DEAD CODE BELOW (old single-candidate path) ---
    try:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": "You are a thread writer for X/Twitter. Output ONLY the thread tweets, one per line. No commentary."},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.7,
            "max_tokens": 2000
        }
        
        if os.environ.get("SGOS_LLM_ENABLE_THINKING", "").lower() == "true":
            payload["enable_thinking"] = True
        
        req = urllib.request.Request(
            f"{api_url}/chat/completions",
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}"
            },
            method="POST"
        )
        
        with urllib.request.urlopen(req, timeout=300) as response:
            result = json.loads(response.read())
            raw_output = result["choices"][0]["message"]["content"].strip()
        
        # Parse format
        import re
        raw_output = raw_output.replace("**", "").replace("*", "")
        
        if raw_output.upper().startswith("LONG:"):
            # Long-form post
            text = raw_output[5:].strip()
            # Strip markdown headers
            text = re.sub(r'^#+\s*', '', text, flags=re.MULTILINE)
            # Strip URLs and link phrases
            text = re.sub(r'https?://\S+', '', text)
            text = re.sub(r'Grab the full guide here:?', '', text)
            text = re.sub(r'Check out the full \w+ here:?', '', text)
            text = re.sub(r'\s+', ' ', text).strip()
            if len(text) > 5000:
                text = text[:4997] + "..."
            return {"format": "long", "text": text, "char_count": len(text)}
        
        elif raw_output.upper().startswith("THREAD:"):
            # Thread format — split on --- delimiter first, fallback to double newlines
            content = raw_output[7:].strip()
        else:
            # Default: try to detect format
            if "---" in raw_output:
                content = raw_output
            else:
                lines = [l.strip() for l in raw_output.split("\n") if l.strip()]
                if len(lines) >= 2 and all(len(l) <= 280 for l in lines):
                    content = raw_output
                else:
                    return {"format": "long", "text": raw_output, "char_count": len(raw_output)}
        
        # Parse thread tweets — split on --- delimiter first, then fallback to double newlines
        if "---" in content:
            raw_tweets = content.split("---")
        else:
            # Fallback: split on double newlines, group consecutive non-empty lines
            raw_tweets = re.split(r'\n\s*\n\s*\n', content)
        
        tweets = []
        for raw_tweet in raw_tweets:
            tweet = raw_tweet.strip()
            tweet = tweet.replace("**", "").replace("*", "")
            # Remove numbering prefixes at the start
            tweet = re.sub(r'^[\d]+[\.\\)]\s*', '', tweet)
            tweet = re.sub(r'^Tweet\s*\d+[:\.\)]?\s*', '', tweet, flags=re.IGNORECASE)
            tweet = re.sub(r'^\d+/\d+\s*', '', tweet)
            # Strip URLs
            tweet = re.sub(r'https?://\S+', '', tweet)
            tweet = re.sub(r'Grab the full guide here:?', '', tweet)
            tweet = tweet.strip()
            if tweet:
                tweets.append(tweet)
        
        # Validate thread — Premium allows longer tweets, cap at 500
        for i, tweet in enumerate(tweets):
            if len(tweet) > 500:
                tweets[i] = tweet[:497].rsplit(" ", 1)[0] + "..."
        
        if len(tweets) < 2:
            # Not enough for a thread, make it a long post instead
            combined = "\n\n".join(tweets) if tweets else raw_output
            return {"format": "long", "text": combined, "char_count": len(combined)}
        
        return {"format": "thread", "tweets": tweets, "tweet_count": len(tweets)}
        
    except Exception as e:
        print(f"❌ LLM call failed: {e}")
        return None


# ─── Grounding Verification (Simple Claim-Checking) ──────────────────────

def verify_grounding(repuposed_text, original_text, grounding_context=""):
    """Verify that repurposed content is grounded in the original source.
    
    Simple approach: Extract specific claims (numbers, dates, tools) from generated content,
    check if they appear in the source. Flag anything that doesn't match.
    
    Returns (grounding_score, unsupported_claims, verified_bool).
    """
    # Combine source material
    source_material = f"{original_text}\n{grounding_context}".lower()
    repuposed_lower = repuposed_text.lower()
    
    unsupported_claims = []
    total_claims = 0
    supported_claims = 0
    
    # Pattern 1: Specific numbers (dates, counts, prices)
    number_patterns = [
        r'\b(\d{4})\b',  # Years
        r'\$(\d+)',      # Prices
        r'\b(\d+)\s*(?:minutes?|hours?|days?|weeks?|months?|years?)\b',  # Durations
        r'\b(\d+)%\b',   # Percentages
    ]
    
    for pattern in number_patterns:
        matches = re.findall(pattern, repuposed_lower)
        for match in matches:
            total_claims += 1
            # Check if this number appears in source
            if str(match) in source_material:
                supported_claims += 1
            else:
                unsupported_claims.append(f"Number '{match}' not found in source")
    
    # Pattern 2: Specific tools/platforms
    tool_patterns = [
        r'\b(notion|obsidian|evernote|roam|logseq|bear|craft|mem|reflect)\b',
        r'\b(chatgpt|claude|gpt-4|gpt-3\.5|gemini|llama|mistral|perplexity)\b',
        r'\b(google|microsoft|apple|meta|openai|anthropic|cursor)\b',
        r'\b(vscode|sublime|vim|neovim|emacs)\b',
    ]
    
    for pattern in tool_patterns:
        matches = re.findall(pattern, repuposed_lower)
        for match in matches:
            total_claims += 1
            if match in source_material:
                supported_claims += 1
            else:
                unsupported_claims.append(f"Tool '{match}' not mentioned in source")
    
    # Pattern 3: Specific dates/times
    date_patterns = [
        r'\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b',  # MM/DD/YYYY
        r'\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2}(?:st|nd|rd|th)?\b',
    ]
    
    for pattern in date_patterns:
        matches = re.findall(pattern, repuposed_lower)
        for match in matches:
            total_claims += 1
            if match in source_material:
                supported_claims += 1
            else:
                unsupported_claims.append(f"Date '{match}' not found in source")
    
    # Calculate score
    if total_claims == 0:
        # No specific claims to verify, assume good
        score = 90
    else:
        score = int((supported_claims / total_claims) * 100)
    
    # Verify if score >= 70 and no unsupported claims
    verified = score >= 70 and len(unsupported_claims) == 0
    
    return score, unsupported_claims, verified


def escape_for_js(text: str) -> str:
    """Escape text for use inside a JS string literal."""
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("\n", "\\n")


def escape_for_html(text: str) -> str:
    """Escape text for use inside HTML/innerHTML."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("\n", "<br>")


def post_thread(tweets: list[str]) -> bool:
    """Post a thread using ego-browser CDP. Posts first tweet, then replies to self."""
    print(f"📤 Posting thread ({len(tweets)} tweets) via ego-browser...")
    for i, t in enumerate(tweets):
        print(f"   [{i+1}] ({len(t)} chars) {t[:80]}...")
    
    # Post the first tweet from the home composer
    first_html = escape_for_html(tweets[0])
    first_text_json = json.dumps(tweets[0])
    ts1 = int(time.time())
    
    script = f'''
const task = await useOrCreateTaskSpace('repurpose-t1-{ts1}')

await openOrReuseTab('https://x.com/home', {{ wait: true, timeout: 30 }})
await cdp('Emulation.setDeviceMetricsOverride', {{ width: 1280, height: 800, deviceScaleFactor: 1, mobile: false }})
await wait(2)
await waitForNetworkIdle({{ timeout: 20 }})
await wait(5)

// Click the composer area
const composerRect = await js(String.raw`(() => {{
  const elem = document.querySelector('[role="textbox"][aria-label="Post text"]')
    || document.querySelector('[role="textbox"]')
  if (elem) {{
    const rect = elem.getBoundingClientRect()
    return {{ x: Math.round(rect.x + 10), y: Math.round(rect.y + 10) }}
  }}
  return null
}})()`)

if (!composerRect) {{
  cliLog('ERROR: Could not find composer')
  await completeTaskSpace(task.id, {{ keep: true }})
  process.exit(1)
}}
await cdp('Input.dispatchMouseEvent', {{ type: 'mouseMoved', x: composerRect.x, y: composerRect.y }})
await wait(0.1)
await cdp('Input.dispatchMouseEvent', {{ type: 'mousePressed', x: composerRect.x, y: composerRect.y, button: 'left', clickCount: 1 }})
await wait(0.05)
await cdp('Input.dispatchMouseEvent', {{ type: 'mouseReleased', x: composerRect.x, y: composerRect.y, button: 'left', clickCount: 1 }})
await wait(1)

// Type first tweet — char-by-char dispatchKeyEvent (DraftJS compatible)
const firstText = {first_text_json}
for (let i = 0; i < firstText.length; i++) {{
  const char = firstText[i]
  if (char === '\\n') {{
    // Enter key for line break
    await cdp('Input.dispatchKeyEvent', {{ type: 'rawKeyDown', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13 }})
    await cdp('Input.dispatchKeyEvent', {{ type: 'char', key: 'Enter', code: 'Enter', text: '\\r', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13 }})
    await cdp('Input.dispatchKeyEvent', {{ type: 'keyUp', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13 }})
    await wait(0.05)
  }} else {{
    // Regular character
    await cdp('Input.dispatchKeyEvent', {{ type: 'rawKeyDown', key: char, code: 'Key' + char.toUpperCase(), windowsVirtualKeyCode: char.charCodeAt(0), nativeVirtualKeyCode: char.charCodeAt(0) }})
    await cdp('Input.dispatchKeyEvent', {{ type: 'char', key: char, code: 'Key' + char.toUpperCase(), text: char, windowsVirtualKeyCode: char.charCodeAt(0), nativeVirtualKeyCode: char.charCodeAt(0) }})
    await cdp('Input.dispatchKeyEvent', {{ type: 'keyUp', key: char, code: 'Key' + char.toUpperCase(), windowsVirtualKeyCode: char.charCodeAt(0), nativeVirtualKeyCode: char.charCodeAt(0) }})
    await wait(0.02)
  }}
}}
await wait(3)

// Click Post button using JS click
const btnState = await js(String.raw`(() => {{
  const btn = document.querySelector('button[data-testid="tweetButton"]')
    || document.querySelector('button[data-testid="tweetButtonInline"]')
  if (!btn) return 'NOT_FOUND'
  if (btn.disabled) return 'DISABLED'
  btn.click()
  return 'CLICKED'
}})()`)\ncliLog('Button: ' + btnState)

if (btnState === 'DISABLED' || btnState === 'NOT_FOUND') {{
  cliLog('ERROR: submit button ' + btnState)
  await completeTaskSpace(task.id, {{ keep: true }})
  process.exit(1)
}}

await wait(8)

// Verify first tweet posted
const sentCheck = await js(String.raw`(() => {{
  const body = document.body.innerText
  if (body.includes('Your post was sent')) return 'CONFIRMED'
  const editors = document.querySelectorAll('[role="textbox"]')
  const texts = Array.from(editors).map(e => e.innerText.trim())
  if (texts.length === 0 || texts.every(t => t.length === 0)) return 'EMPTY'
  return 'STILL_HAS_TEXT'
}})()`)

if (sentCheck === 'CONFIRMED' || sentCheck === 'EMPTY') {{
  cliLog('TWEET1_SUCCESS')
}} else {{
  cliLog('TWEET1_FAILED: ' + sentCheck)
  await completeTaskSpace(task.id, {{ keep: true }})
  process.exit(1)
}}

await completeTaskSpace(task.id, {{ keep: false }})
'''
    
    try:
        result = subprocess.run(
            [EGO_BROWSER, 'nodejs'],
            input=script,
            capture_output=True,
            text=True,
            timeout=120
        )
        output = result.stdout + result.stderr
        if 'TWEET1_SUCCESS' not in output:
            print(f"❌ First tweet failed: {output[:200]}")
            return False
        print("✅ Tweet 1 posted")
        posted_count = 1
    except subprocess.TimeoutExpired:
        print("❌ ego-browser timed out on tweet 1")
        return False
    except FileNotFoundError:
        print(f"❌ ego-browser not found at {EGO_BROWSER}")
        return False
    
    # Get the tweet ID from our profile (most recent tweet)
    time.sleep(5)  # Let X index it
    
    # Try bird first
    first_tweet_id = None
    try:
        result = subprocess.run(
            [BIRD_BIN, 'user-tweets', 'StraughterG', '--json', '-n', '1'],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode == 0:
            tweets_data = json.loads(result.stdout) if result.stdout.strip().startswith('[') else json.loads(result.stdout.split('\n')[-1].strip() if result.stdout.strip() else '[]')
            if isinstance(tweets_data, list) and tweets_data:
                first_tweet_id = tweets_data[0]['id']
    except Exception:
        pass
    
    # Fallback: use ego-browser to scrape our profile page
    if not first_tweet_id:
        print("⚠️ bird failed, using ego-browser to get tweet ID...")
        try:
            id_ts = int(time.time())
            id_script = f'''
const task = await useOrCreateTaskSpace('repurpose-getid-{id_ts}')
await openOrReuseTab('https://x.com/StraughterG', {{ wait: true, timeout: 30 }})
await cdp('Emulation.setDeviceMetricsOverride', {{ width: 1280, height: 800, deviceScaleFactor: 1, mobile: false }})
await wait(3)
await waitForNetworkIdle({{ timeout: 15 }})
await wait(2)
const tweetLink = await js(String.raw`(() => {{
  const links = document.querySelectorAll('a[href*="/StraughterG/status/"]')
  for (const link of links) {{
    const match = link.getAttribute('href').match(/\\/status\\/(\\d+)/)
    if (match) return match[1]
  }}
  return null
}})()`)
cliLog('TWEET_ID:' + (tweetLink || 'NONE'))
await completeTaskSpace(task.id, {{ keep: false }})
'''
            id_result = subprocess.run(
                [EGO_BROWSER, 'nodejs'],
                input=id_script,
                capture_output=True,
                text=True,
                timeout=120
            )
            for line in (id_result.stdout + id_result.stderr).split('\n'):
                if 'TWEET_ID:' in line:
                    tid = line.split('TWEET_ID:')[1].strip()
                    if tid and tid != 'NONE':
                        first_tweet_id = tid
                        break
        except Exception as e:
            print(f"⚠️ ego-browser ID fetch failed: {e}")
    
    if not first_tweet_id:
        print("❌ Could not get tweet ID from either source, skipping replies")
        return posted_count >= len(tweets)
    
    print(f"✅ Got tweet ID: {first_tweet_id}")
    
    # Post remaining tweets as replies — navigate to tweet 1's detail page, then reply inline
    for i, tweet_text in enumerate(tweets[1:], start=2):
        print(f"⏳ Waiting {SECONDS_BETWEEN_THREAD_REPLIES}s before tweet {i}...")
        time.sleep(SECONDS_BETWEEN_THREAD_REPLIES)
        
        escaped = escape_for_js(tweet_text)
        reply_text_json = json.dumps(tweet_text)
        
        reply_script = f'''
const task = await useOrCreateTaskSpace('repurpose-r{i}-{int(time.time())}')

// Navigate to the first tweet's detail page — reply composer is built-in here
await openOrReuseTab('https://x.com/StraughterG/status/{first_tweet_id}', {{ wait: true, timeout: 30 }})
await cdp('Emulation.setDeviceMetricsOverride', {{ width: 1280, height: 800, deviceScaleFactor: 1, mobile: false }})
await wait(3)
await waitForNetworkIdle({{ timeout: 15 }})
await wait(3)

// Click the reply textbox (always present on detail page)
const editorRect = await js(String.raw`(() => {{
  const elem = document.querySelector('[role="textbox"][aria-label="Post text"]')
    || document.querySelector('[role="textbox"]')
  if (!elem) return null
  const rect = elem.getBoundingClientRect()
  return {{ x: Math.round(rect.x + 10), y: Math.round(rect.y + 10) }}
}})()`)

if (!editorRect) {{
  cliLog('ERROR: No reply textbox on detail page')
  await completeTaskSpace(task.id, {{ keep: true }})
  process.exit(1)
}}

// Focus via CDP click
await cdp('Input.dispatchMouseEvent', {{ type: 'mouseMoved', x: editorRect.x, y: editorRect.y }})
await wait(0.1)
await cdp('Input.dispatchMouseEvent', {{ type: 'mousePressed', x: editorRect.x, y: editorRect.y, button: 'left', clickCount: 1 }})
await wait(0.05)
await cdp('Input.dispatchMouseEvent', {{ type: 'mouseReleased', x: editorRect.x, y: editorRect.y, button: 'left', clickCount: 1 }})
await wait(1)

// Type reply — split on newlines, use Enter key for line breaks (DraftJS compatible)
const replyText = {reply_text_json}
const replyLines = replyText.split('\\n')
for (let li = 0; li < replyLines.length; li++) {{
  if (replyLines[li]) {{
    await cdp('Input.insertText', {{ text: replyLines[li] }})
  }}
  if (li < replyLines.length - 1) {{
    await cdp('Input.dispatchKeyEvent', {{ type: 'rawKeyDown', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13 }})
    await cdp('Input.dispatchKeyEvent', {{ type: 'char', key: 'Enter', code: 'Enter', text: '\\r', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13 }})
    await cdp('Input.dispatchKeyEvent', {{ type: 'keyUp', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13 }})
    await wait(0.05)
  }}
}}
await wait(3)

// Click Reply button via JS
const btnResult = await js(String.raw`(() => {{
  const btn = document.querySelector('button[data-testid="tweetButtonInline"]')
    || document.querySelector('button[data-testid="tweetButton"]')
  if (!btn) return 'NO_BUTTON'
  if (btn.disabled) return 'BUTTON_DISABLED'
  btn.click()
  return 'CLICKED'
}})()`)
cliLog('Submit: ' + btnResult)

if (btnResult !== 'CLICKED') {{
  cliLog('ERROR: submit failed: ' + btnResult)
  await completeTaskSpace(task.id, {{ keep: true }})
  process.exit(1)
}}
await wait(8)

const sentCheck = await js(String.raw`(() => {{
  const body = document.body.innerText
  if (body.includes('Your post was sent')) return 'CONFIRMED'
  const editors = document.querySelectorAll('[role="textbox"]')
  const texts = Array.from(editors).map(e => e.innerText.trim())
  if (texts.length === 0 || texts.every(t => t.length === 0)) return 'EMPTY'
  return 'STILL_HAS_TEXT'
}})()`)

if (sentCheck === 'CONFIRMED' || sentCheck === 'EMPTY') {{
  cliLog('REPLY_SUCCESS')
}} else {{
  cliLog('REPLY_FAILED: ' + sentCheck)
}}

await completeTaskSpace(task.id, {{ keep: false }})
'''
        try:
            result = subprocess.run(
                [EGO_BROWSER, 'nodejs'],
                input=reply_script,
                capture_output=True,
                text=True,
                timeout=60  # Reduced from 120s — fail fast
            )
            output = result.stdout + result.stderr
            if 'REPLY_SUCCESS' in output:
                print(f"✅ Tweet {i}/{len(tweets)} posted as reply")
                posted_count += 1
                # Refresh session every 2 replies to prevent CDP staleness
                if i % 2 == 0:
                    print(f"🔄 Refreshing CDP session after tweet {i}...")
                    time.sleep(5)  # Brief pause for session cleanup
            else:
                print(f"❌ Tweet {i} failed: {output[:300]}")
                # Retry once on failure
                print(f"🔄 Retrying tweet {i}...")
                time.sleep(10)
                retry_result = subprocess.run(
                    [EGO_BROWSER, 'nodejs'],
                    input=reply_script,
                    capture_output=True,
                    text=True,
                    timeout=60
                )
                if 'REPLY_SUCCESS' in (retry_result.stdout + retry_result.stderr):
                    print(f"✅ Tweet {i}/{len(tweets)} posted as reply (retry)")
                    posted_count += 1
                else:
                    print(f"❌ Tweet {i} failed after retry, aborting thread")
                    break
        except subprocess.TimeoutExpired:
            print(f"❌ ego-browser timed out on tweet {i}/{len(tweets)} (>60s)")
            if posted_count == 0:
                send_alert(f"CDP timeout on first tweet. ego-browser session may be stale.")
            else:
                send_alert(f"Posted {posted_count}/{len(tweets)} tweets before CDP timeout. Thread incomplete.")
            break
        except Exception as e:
            print(f"❌ Tweet {i} error: {e}")
            if posted_count == 0:
                send_alert(f"Posting failed on first tweet: {str(e)[:100]}")
            else:
                send_alert(f"Posted {posted_count}/{len(tweets)} tweets before error. Thread incomplete.")
            break
    
    return posted_count >= len(tweets)  # Only success if ALL tweets posted


def post_long_form(text: str) -> bool:
    """Post a long-form Premium post using ego-browser CDP."""
    print(f"📤 Posting long-form ({len(text)} chars) via ego-browser...")
    print(f"   {text[:120]}...")
    
    escaped = escape_for_js(text)
    text_json = json.dumps(text)
    
    # X Premium long posts use the same composer but with expanded text
    script = f'''
const task = await useOrCreateTaskSpace('repurpose-long-{int(time.time())}')

await openOrReuseTab('https://x.com/home', {{ wait: true, timeout: 30 }})
await cdp('Emulation.setDeviceMetricsOverride', {{ width: 1280, height: 800, deviceScaleFactor: 1, mobile: false }})
await wait(2)
await waitForNetworkIdle({{ timeout: 20 }})
await wait(5)

// Click the composer area
const composerRect = await js(String.raw`(() => {{
  const elem = document.querySelector('[role="textbox"][aria-label="Post text"]')
    || document.querySelector('[role="textbox"]')
  if (elem) {{
    const rect = elem.getBoundingClientRect()
    return {{ x: Math.round(rect.x + 10), y: Math.round(rect.y + 10) }}
  }}
  return null
}})()`)

if (!composerRect) {{
  cliLog('ERROR: Could not find composer')
  await completeTaskSpace(task.id, {{ keep: true }})
  process.exit(1)
}}
await cdp('Input.dispatchMouseEvent', {{ type: 'mouseMoved', x: composerRect.x, y: composerRect.y }})
await wait(0.1)
await cdp('Input.dispatchMouseEvent', {{ type: 'mousePressed', x: composerRect.x, y: composerRect.y, button: 'left', clickCount: 1 }})
await wait(0.05)
await cdp('Input.dispatchMouseEvent', {{ type: 'mouseReleased', x: composerRect.x, y: composerRect.y, button: 'left', clickCount: 1 }})
await wait(1)

// Type long-form post — split on newlines, use Enter key for line breaks (DraftJS compatible)
const longText = {text_json}
const longLines = longText.split('\\n')
for (let li = 0; li < longLines.length; li++) {{
  if (longLines[li]) {{
    await cdp('Input.insertText', {{ text: longLines[li] }})
  }}
  if (li < longLines.length - 1) {{
    await cdp('Input.dispatchKeyEvent', {{ type: 'rawKeyDown', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13 }})
    await cdp('Input.dispatchKeyEvent', {{ type: 'char', key: 'Enter', code: 'Enter', text: '\\r', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13 }})
    await cdp('Input.dispatchKeyEvent', {{ type: 'keyUp', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13 }})
    await wait(0.05)
  }}
}}
await wait(3)

// Click Post button using JS click
const btnState = await js(String.raw`(() => {{
  const btn = document.querySelector('button[data-testid="tweetButton"]')
    || document.querySelector('button[data-testid="tweetButtonInline"]')
  if (!btn) return 'NOT_FOUND'
  if (btn.disabled) return 'DISABLED'
  btn.click()
  return 'CLICKED'
}})()`)\ncliLog('Button: ' + btnState)

if (btnState === 'DISABLED' || btnState === 'NOT_FOUND') {{
  cliLog('ERROR: submit button ' + btnState)
  await completeTaskSpace(task.id, {{ keep: true }})
  process.exit(1)
}}

await wait(8)

// Verify
const sentCheck = await js(String.raw`(() => {{
  const body = document.body.innerText
  if (body.includes('Your post was sent')) return 'CONFIRMED'
  const editors = document.querySelectorAll('[role="textbox"]')
  const texts = Array.from(editors).map(e => e.innerText.trim())
  if (texts.length === 0 || texts.every(t => t.length === 0)) return 'EMPTY'
  return 'STILL_HAS_TEXT'
}})()`)

if (sentCheck === 'CONFIRMED' || sentCheck === 'EMPTY') {{
  cliLog('SUCCESS')
}} else {{
  cliLog('FAILED: ' + sentCheck)
}}

await completeTaskSpace(task.id, {{ keep: false }})
'''
    
    try:
        result = subprocess.run(
            [EGO_BROWSER, 'nodejs'],
            input=script,
            capture_output=True,
            text=True,
            timeout=120
        )
        output = result.stdout + result.stderr
        if 'SUCCESS' in output:
            print("✅ Long-form post published")
            return True
        else:
            print(f"❌ Failed to post: {output[:200]}")
            return False
    except subprocess.TimeoutExpired:
        print("❌ ego-browser timed out")
        return False
    except FileNotFoundError:
        print(f"❌ ego-browser not found at {EGO_BROWSER}")
        return False


def post_reply(tweet_id: str, text: str) -> bool:
    """Post a reply to a specific tweet using ego-browser CDP."""
    print(f"📤 Posting reply to tweet {tweet_id} ({len(text)} chars)...")
    
    escaped = escape_for_js(text)
    reply_text_json = json.dumps(text)
    ts = int(time.time())
    
    script = f'''
const task = await useOrCreateTaskSpace('repurpose-reply-{ts}')

await openOrReuseTab('https://x.com/StraughterG/status/{tweet_id}', {{ wait: true, timeout: 30 }})
await cdp('Emulation.setDeviceMetricsOverride', {{ width: 1280, height: 800, deviceScaleFactor: 1, mobile: false }})
await wait(3)
await waitForNetworkIdle({{ timeout: 15 }})
await wait(3)

// Click the reply textbox (always present on detail page)
const editorRect = await js(String.raw`(() => {{
  const elem = document.querySelector('[role="textbox"][aria-label="Post text"]')
    || document.querySelector('[role="textbox"]')
  if (!elem) return null
  const rect = elem.getBoundingClientRect()
  return {{ x: Math.round(rect.x + 10), y: Math.round(rect.y + 10) }}
}})()`)

if (!editorRect) {{
  cliLog('ERROR: No reply textbox on detail page')
  await completeTaskSpace(task.id, {{ keep: false }})
  process.exit(1)
}}

await cdp('Input.dispatchMouseEvent', {{ type: 'mouseMoved', x: editorRect.x, y: editorRect.y }})
await wait(0.1)
await cdp('Input.dispatchMouseEvent', {{ type: 'mousePressed', x: editorRect.x, y: editorRect.y, button: 'left', clickCount: 1 }})
await wait(0.05)
await cdp('Input.dispatchMouseEvent', {{ type: 'mouseReleased', x: editorRect.x, y: editorRect.y, button: 'left', clickCount: 1 }})
await wait(1)

const replyText = {reply_text_json}
const replyLines = replyText.split('\\n')
for (let li = 0; li < replyLines.length; li++) {{
  if (replyLines[li]) {{
    await cdp('Input.insertText', {{ text: replyLines[li] }})
  }}
  if (li < replyLines.length - 1) {{
    await cdp('Input.dispatchKeyEvent', {{ type: 'rawKeyDown', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13 }})
    await cdp('Input.dispatchKeyEvent', {{ type: 'char', key: 'Enter', code: 'Enter', text: '\\r', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13 }})
    await cdp('Input.dispatchKeyEvent', {{ type: 'keyUp', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13 }})
    await wait(0.05)
  }}
}}
await wait(3)

const btnResult = await js(String.raw`(() => {{
  const btn = document.querySelector('button[data-testid="tweetButtonInline"]')
    || document.querySelector('button[data-testid="tweetButton"]')
  if (!btn) return 'NO_BUTTON'
  if (btn.disabled) return 'BUTTON_DISABLED'
  btn.click()
  return 'CLICKED'
}})()`)

if (btnResult !== 'CLICKED') {{
  cliLog('ERROR: submit failed: ' + btnResult)
  await completeTaskSpace(task.id, {{ keep: false }})
  process.exit(1)
}}
await wait(8)

const sentCheck = await js(String.raw`(() => {{
  const body = document.body.innerText
  if (body.includes('Your post was sent')) return 'CONFIRMED'
  const editors = document.querySelectorAll('[role="textbox"]')
  const texts = Array.from(editors).map(e => e.innerText.trim())
  if (texts.length === 0 || texts.every(t => t.length === 0)) return 'EMPTY'
  return 'STILL_HAS_TEXT'
}})()`)

if (sentCheck === 'CONFIRMED' || sentCheck === 'EMPTY') {{
  cliLog('REPLY_SUCCESS')
}} else {{
  cliLog('REPLY_FAILED: ' + sentCheck)
}}

await completeTaskSpace(task.id, {{ keep: false }})
'''
    
    try:
        result = subprocess.run(
            [EGO_BROWSER, 'nodejs'],
            input=script,
            capture_output=True,
            text=True,
            timeout=120
        )
        output = result.stdout + result.stderr
        if 'REPLY_SUCCESS' in output:
            print("✅ Reply posted")
            return True
        else:
            print(f"❌ Reply failed: {output[:300]}")
            return False
    except subprocess.TimeoutExpired:
        print("❌ ego-browser timed out")
        return False
    except FileNotFoundError:
        print(f"❌ ego-browser not found at {EGO_BROWSER}")
        return False


def get_last_tweet_id() -> Optional[str]:
    """Get the ID of the last tweet posted by our account."""
    try:
        result = subprocess.run(
            [BIRD_BIN, "profile", "StraughterG", "--json"],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            tweets = data.get("tweets", [])
            if tweets:
                return tweets[0].get("id")
    except Exception as e:
        print(f"⚠️ Could not get last tweet ID: {e}")
    return None


# ─── Deduplication ────────────────────────────────────────────────────────

DEDUP_FILE = Path(__file__).parent.parent / "data" / "repurpose_posted.json"

def load_posted_history() -> dict:
    """Load the history of posted source tweets."""
    if DEDUP_FILE.exists():
        try:
            with open(DEDUP_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️ Could not load posted history: {e}")
    return {"posted": []}

def save_posted_history(history: dict):
    """Save the history of posted source tweets."""
    DEDUP_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(DEDUP_FILE, 'w') as f:
            json.dump(history, f, indent=2)
    except Exception as e:
        print(f"⚠️ Could not save posted history: {e}")

def is_already_posted(history: dict, source_handle: str, source_tweet_id: str) -> bool:
    """Check if a source tweet has already been posted."""
    for entry in history.get("posted", []):
        if entry.get("source_handle") == source_handle and entry.get("source_tweet_id") == source_tweet_id:
            return True
    return False

def record_posted(source_handle: str, source_tweet_id: str, tweet_count: int):
    """Record that a source tweet has been posted."""
    history = load_posted_history()
    history["posted"].append({
        "source_handle": source_handle,
        "source_tweet_id": source_tweet_id,
        "posted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tweet_count": tweet_count
    })
    save_posted_history(history)
    print(f"📝 Recorded in dedup history: @{source_handle} tweet {source_tweet_id}")

def check_rate_limit() -> tuple[bool, float]:
    """Check rate limit file. Returns (ok, minutes_since_last)."""
    rate_file = Path("/tmp/sgos-rate-limit.txt")
    if not rate_file.exists():
        return True, 9999
    
    try:
        content = rate_file.read_text().strip()
        last_ts = float(content)
        minutes = (time.time() - last_ts) / 60
        return minutes >= MIN_MINUTES_BETWEEN_POSTS, minutes
    except:
        return True, 9999


def update_rate_limit():
    """Update rate limit timestamp."""
    Path("/tmp/sgos-rate-limit.txt").write_text(str(time.time()))


def main():
    print("=" * 60)
    print("🔄 Multi-Account Thread Repurpose Pipeline")
    print("=" * 60)
    print(f"Target accounts: {', '.join('@' + h for h in TARGET_HANDLES)}")
    
    # Check rate limit
    ok, mins = check_rate_limit()
    if not ok:
        print(f"⏳ Rate limited: posted {mins:.0f} min ago (need {MIN_MINUTES_BETWEEN_POSTS} min gap)")
        return 1
    print(f"⏱️ Rate limit: OK (last post {mins:.0f} min ago)")
    
    # Step 1: Scrape from all target accounts
    all_tweets = []
    for handle in TARGET_HANDLES:
        tweets = scrape_recent_tweets(handle)
        if tweets:
            for t in tweets:
                t["source_handle"] = handle
            all_tweets.extend(tweets)
    
    if not all_tweets:
        print("❌ No tweets scraped from any account, exiting")
        send_alert("No tweets scraped from any target account. Check bird CLI auth.")
        return 1
    
    print(f"\n📊 Total tweets scraped: {len(all_tweets)}")
    
    # Step 2: Group into threads and filter
    threads = group_threads(all_tweets)
    quality = filter_quality_threads(threads)
    
    if not quality:
        print("❌ No quality threads found, exiting")
        send_alert("No quality threads found after filtering. May need to expand target accounts or lower quality threshold.")
        return 1
    
    # Load dedup history
    history = load_posted_history()
    print(f"📚 Loaded {len(history.get('posted', []))} previously posted tweets from history")
    
    # Filter out already-posted content
    original_count = len(quality)
    quality = [
        t for t in quality
        if not is_already_posted(history, t['source_handle'], t['id'])
    ]
    
    if not quality:
        print(f"❌ All {original_count} quality threads already posted, exiting")
        return 1
    
    skipped = original_count - len(quality)
    if skipped > 0:
        print(f"⏭️  Skipped {skipped} already-posted threads, {len(quality)} remaining")
    
    # Step 3: Load voice profile
    voice_name, voice_prompt = get_voice_profile()
    
    # Step 4: Pick the top thread (highest priority) and repurpose
    # Take top 1 for posting, show all as dry run
    top = quality[0]
    kind = "🧵 THREAD" if top["is_thread"] else "📝 LONG POST"
    print(f"\n{'='*60}")
    print(f"🏆 TOP PICK: {kind} from @{top['source_handle']}")
    print(f"   {top['tweet_count']} tweets, {top['total_chars']} chars, ❤️{top['likes']}")
    print(f"   > {top['first_text'][:120].replace(chr(10), ' ')}...")
    print(f"{'='*60}")
    
    print(f"\n📝 Repurposing as {'thread' if top['is_thread'] else 'long-form or thread'}...")
    result = repurpose_as_thread(top, voice_name, voice_prompt)
    
    if not result:
        print("❌ Failed to generate content")
        send_alert(f"LLM generation failed for thread from @{top['source_handle']}. Check Aliyun API.")
        return 1
    
    # Build the full repuposed text for verification
    if result["format"] == "thread":
        repuposed_text = "\n\n".join(result["tweets"])
    else:
        repuposed_text = result["text"]
    
    # Build the original text
    original_text = top.get("first_text", "")
    if top["is_thread"]:
        original_text = "\n\n".join(t.get("text", "") for t in top.get("tweets", []))
    
    # Verify grounding
    print(f"\n🔍 Verifying grounding...")
    score, unsupported, verified = verify_grounding(
        repuposed_text, 
        original_text,
        top.get("grounding_context", "")
    )
    
    print(f"   Grounding score: {score}/100")
    if unsupported:
        print(f"   ⚠️  {len(unsupported)} unsupported claims:")
        for claim in unsupported[:3]:
            print(f"      - {claim[:80]}")
    
    if not verified:
        print(f"   ❌ Grounding verification FAILED (score < 70 or unsupported claims)")
        if "--force" not in sys.argv:
            print(f"   Use --force to post anyway")
            return 1
        else:
            print(f"   --force flag set, proceeding anyway")
    else:
        print(f"   ✅ Grounding verified")
    
    # Display result based on format
    if result["format"] == "thread":
        print(f"\n✨ Generated THREAD ({len(result['tweets'])} tweets):")
        for i, t in enumerate(result['tweets'], 1):
            print(f"   [{i}] ({len(t)} chars) {t[:100]}")
    else:
        print(f"\n✨ Generated LONG POST ({result['char_count']} chars):")
        print(f"   {result['text'][:200]}...")
    
    # Step 5: Post or dry-run
    if "--post" in sys.argv:
        print(f"\n{'='*60}")
        if result["format"] == "thread":
            print(f"📤 POSTING THREAD ({len(result['tweets'])} tweets)...")
            success = post_thread(result['tweets'])
        else:
            print(f"📤 POSTING LONG FORM ({result['char_count']} chars)...")
            success = post_long_form(result['text'])
            
        if success:
            update_rate_limit()
            # Record in dedup history
            source_tweet_id = top.get('id')
            if source_tweet_id:
                record_posted(top['source_handle'], source_tweet_id, len(result.get('tweets', [result.get('text', '')])))
            print(f"\n✅ Posted successfully!")
            send_alert(f"✅ Successfully posted {len(result.get('tweets', 1))} tweets from @{top['source_handle']}")
        else:
            print(f"\n❌ Posting failed")
            send_alert(f"❌ Posting failed for thread from @{top['source_handle']}. Check ego-browser and CDP session.")
        return 0 if success else 1
        
    elif "--test-reply" in sys.argv:
        print(f"\n{'='*60}")
        print("🧪 TEST MODE: Posting reply to last tweet...")
        last_id = get_last_tweet_id()
        if not last_id:
            print("❌ Could not find last tweet")
            return 1
        success = post_reply(last_id, "🧪 Thread reply test — verifying the reply mechanism works. Ignore this.")
        return 0 if success else 1
        
    elif "--test-long" in sys.argv:
        print(f"\n{'='*60}")
        print("🧪 TEST MODE: Posting long-form Premium post...")
        test_text = """🧪 Long-form post test — verifying X Premium posting works.

    This is a test of the long-form posting path. If you see this, the system successfully:
    1. Navigated to x.com/home
    2. Found the composer
    3. Typed this long content
    4. Clicked Post
    5. Verified it published

    All systems operational. Ignore this test post."""
        success = post_long_form(test_text)
        return 0 if success else 1
    else:
        print(f"\n💡 Dry run complete. Use --post to post this {result['format']}.")
        
        # Show what the next picks would be
        if len(quality) > 1:
            print(f"\n📋 Next picks:")
            for th in quality[1:4]:
                kind = "🧵 THREAD" if th["is_thread"] else "📝 LONG POST" if th["is_long_single"] else "📝 POST"
                print(f"   {kind} from @{th['source_handle']} ({th['total_chars']} chars, ❤️{th['likes']})")
        
        return 0


if __name__ == "__main__":
    sys.exit(main())
