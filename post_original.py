#!/usr/bin/env python3
"""
SGOS Original Poster — Auto-post tweets and threads to your X timeline.

Uses ego-browser CDP (Chrome DevTools Protocol) to compose and submit
original tweets/threads. Supports:
  - Single tweet posting
  - Multi-tweet thread posting (auto-chains replies to self)
  - Pipeline integration (auto-post highest-scoring approved drafts)

Architecture mirrors post_with_ego.py but targets the home timeline composer.

Usage:
  python3 post_original.py --tweet "Your tweet text here"
  python3 post_original.py --thread file.json       # JSON array of tweet strings
  python3 post_original.py --pipeline                # Auto-post top approved pipeline draft
  python3 post_original.py --pipeline --dry-run      # Preview what would be posted
"""
import argparse
import json
import os
import subprocess
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

SGOS_DIR = Path.home() / "sgos-backend"
if str(SGOS_DIR) not in sys.path:
    sys.path.insert(0, str(SGOS_DIR))

from database import get_connection

EGO_BROWSER = "/Users/speed/.local/bin/ego-browser"
TASK_SPACE_NAME = "sgos-post-original"

# Rate limiting: minimum minutes between posts
MIN_MINUTES_BETWEEN_POSTS = 30


def ensure_tables():
    """Create posted_originals table if missing."""
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS posted_originals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_type TEXT NOT NULL DEFAULT 'single',
            content TEXT NOT NULL,
            tweet_count INTEGER DEFAULT 1,
            tweet_ids TEXT,
            source_type TEXT,
            source_id TEXT,
            status TEXT DEFAULT 'pending',
            posted_at TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            error TEXT
        )
    """)
    conn.commit()
    conn.close()


def get_db():
    conn = sqlite3.connect(SGOS_DIR / "sgos.db")
    conn.row_factory = sqlite3.Row
    return conn


def check_rate_limit():
    """Check if we've posted recently. Returns (ok, minutes_since_last)."""
    conn = get_db()
    row = conn.execute("""
        SELECT posted_at FROM posted_originals
        WHERE status = 'posted'
        ORDER BY posted_at DESC
        LIMIT 1
    """).fetchone()
    conn.close()

    if not row:
        return True, 9999

    last = datetime.fromisoformat(row["posted_at"].replace("Z", "+00:00"))
    now = datetime.now(last.tzinfo) if last.tzinfo else datetime.now()
    minutes = (now - last).total_seconds() / 60
    return minutes >= MIN_MINUTES_BETWEEN_POSTS, minutes


def record_post(post_type, content, tweet_count, tweet_ids, source_type=None, source_id=None, status="posted", error=None):
    """Record a post attempt in DB."""
    conn = get_db()
    conn.execute("""
        INSERT INTO posted_originals (post_type, content, tweet_count, tweet_ids, source_type, source_id, status, posted_at, error)
        VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), ?)
    """, (post_type, content, tweet_count, json.dumps(tweet_ids) if tweet_ids else None,
          source_type, source_id, status, error))
    conn.commit()
    conn.close()


def escape_for_js(text):
    """Escape text for safe embedding in JS template literal."""
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("\n", "\\n").replace("${", "\\${")


def build_single_tweet_script(tweets, task_space_name):
    """Build ego-browser script for posting a single tweet."""
    escaped = escape_for_js(tweets[0])

    return f'''
const task = await useOrCreateTaskSpace('{task_space_name}')

// Navigate to home timeline
await openOrReuseTab('https://x.com/home', {{ wait: true, timeout: 30 }})
await cdp('Emulation.setDeviceMetricsOverride', {{ width: 1280, height: 900, deviceScaleFactor: 1, mobile: false }})
await wait(3)
await waitForNetworkIdle({{ timeout: 20 }})
await wait(5)

// Find and click the main composer
const composerRect = await js(String.raw`(() => {{
  // Try aria-label for the tweet composer
  const composer = document.querySelector('[aria-label="Post text"]')
    || document.querySelector('[data-testid="tweetTextarea_0"]')
    || document.querySelector('[role="textbox"][aria-label="Post your reply"]')
  if (composer) {{
    const rect = composer.getBoundingClientRect()
    return {{ x: Math.round(rect.x + 10), y: Math.round(rect.y + 10), found: true }}
  }}
  return {{ found: false }}
}})()`)

if (!composerRect.found) {{
  cliLog('ERROR: Could not find tweet composer')
  await completeTaskSpace(task.id, {{ keep: true }})
  process.exit(1)
}}

// Click the composer
await cdp('Input.dispatchMouseEvent', {{ type: 'mouseMoved', x: composerRect.x, y: composerRect.y }})
await wait(0.2)
await cdp('Input.dispatchMouseEvent', {{ type: 'mousePressed', x: composerRect.x, y: composerRect.y, button: 'left', clickCount: 1 }})
await wait(0.1)
await cdp('Input.dispatchMouseEvent', {{ type: 'mouseReleased', x: composerRect.x, y: composerRect.y, button: 'left', clickCount: 1 }})
await wait(2)

// Insert text via CDP
await cdp('Input.insertText', {{ text: "{escaped}" }})
await wait(3)

// Find and click the Post button
const btnInfo = await js(String.raw`(() => {{
  const btn = document.querySelector('button[data-testid="tweetButton"]')
    || document.querySelector('button[data-testid="tweetButtonInline"]')
  if (!btn) return {{ found: false }}
  const rect = btn.getBoundingClientRect()
  return {{ found: true, disabled: btn.disabled, x: Math.round(rect.x + rect.width/2), y: Math.round(rect.y + rect.height/2) }}
}})()`)

if (!btnInfo.found) {{
  cliLog('ERROR: Post button not found')
  await completeTaskSpace(task.id, {{ keep: true }})
  process.exit(1)
}}

if (btnInfo.disabled) {{
  // Fallback: char-by-char CDP typing to activate DraftJS
  cliLog('WARN: button disabled, trying char-by-char CDP typing')
  await cdp('Input.dispatchKeyEvent', {{ type: 'keyDown', key: 'a', code: 'KeyA', windowsVirtualKeyCode: 65, modifiers: 4 }})
  await cdp('Input.dispatchKeyEvent', {{ type: 'keyUp', key: 'a', code: 'KeyA', windowsVirtualKeyCode: 65, modifiers: 4 }})
  await cdp('Input.dispatchKeyEvent', {{ type: 'keyDown', key: 'Backspace', code: 'Backspace', windowsVirtualKeyCode: 8 }})
  await cdp('Input.dispatchKeyEvent', {{ type: 'keyUp', key: 'Backspace', code: 'Backspace', windowsVirtualKeyCode: 8 }})
  await wait(0.5)
  const chars = "{escaped}"
  for (let i = 0; i < chars.length; i++) {{
    await cdp('Input.dispatchKeyEvent', {{ type: 'keyDown', key: chars[i], text: chars[i], unmodifiedText: chars[i] }})
    await cdp('Input.dispatchKeyEvent', {{ type: 'char', text: chars[i] }})
    await cdp('Input.dispatchKeyEvent', {{ type: 'keyUp', key: chars[i], text: chars[i], unmodifiedText: chars[i] }})
    if (i % 15 === 0) await wait(0.05)
  }}
  await wait(3)
  const btn2 = await js(String.raw`(() => {{
    const btn = document.querySelector('button[data-testid="tweetButton"]')
      || document.querySelector('button[data-testid="tweetButtonInline"]')
    if (!btn) return {{ found: false, disabled: true }}
    const rect = btn.getBoundingClientRect()
    return {{ found: true, disabled: btn.disabled, x: Math.round(rect.x + rect.width/2), y: Math.round(rect.y + rect.height/2) }}
  }})()`)
  btnInfo.found = btn2.found
  btnInfo.disabled = btn2.disabled
  btnInfo.x = btn2.x
  btnInfo.y = btn2.y
  if (btnInfo.disabled) {{
    cliLog('ERROR: button STILL disabled after char-by-char')
    await completeTaskSpace(task.id, {{ keep: true }})
    process.exit(1)
  }}
}}

// SUBMIT
cliLog('Submitting tweet...')
await cdp('Input.dispatchMouseEvent', {{ type: 'mouseMoved', x: btnInfo.x, y: btnInfo.y }})
await wait(0.3)
await cdp('Input.dispatchMouseEvent', {{ type: 'mousePressed', x: btnInfo.x, y: btnInfo.y, button: 'left', clickCount: 1 }})
await wait(0.1)
await cdp('Input.dispatchMouseEvent', {{ type: 'mouseReleased', x: btnInfo.x, y: btnInfo.y, button: 'left', clickCount: 1 }})
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


def build_thread_script(tweets, task_space_name):
    """Build ego-browser script for posting a multi-tweet thread.

    Strategy: Post first tweet from home composer, then for each subsequent
    tweet, scroll to find your just-posted tweet, click Reply, type, submit.
    Uses wait(20) between tweets to respect rate limits and look natural.
    """
    escaped_tweets = [escape_for_js(t) for t in tweets]

    # Build the tweet posting segments
    segments = []

    # First tweet: post from home composer
    segments.append(f'''
// === TWEET 1/{len(tweets)} ===
const composer1 = await js(String.raw`(() => {{
  const c = document.querySelector('[aria-label="Post text"]')
    || document.querySelector('[data-testid="tweetTextarea_0"]')
  if (c) {{
    const rect = c.getBoundingClientRect()
    return {{ x: Math.round(rect.x + 10), y: Math.round(rect.y + 10), found: true }}
  }}
  return {{ found: false }}
}})()`)

if (!composer1.found) {{
  cliLog('ERROR: Could not find composer for tweet 1')
  await completeTaskSpace(task.id, {{ keep: true }})
  process.exit(1)
}}

await cdp('Input.dispatchMouseEvent', {{ type: 'mouseMoved', x: composer1.x, y: composer1.y }})
await wait(0.2)
await cdp('Input.dispatchMouseEvent', {{ type: 'mousePressed', x: composer1.x, y: composer1.y, button: 'left', clickCount: 1 }})
await wait(0.1)
await cdp('Input.dispatchMouseEvent', {{ type: 'mouseReleased', x: composer1.x, y: composer1.y, button: 'left', clickCount: 1 }})
await wait(2)

await cdp('Input.insertText', {{ text: "{escaped_tweets[0]}" }})
await wait(3)

// Click Post
let btn1 = await js(String.raw`(() => {{
  const btn = document.querySelector('button[data-testid="tweetButton"]')
  if (!btn) return {{ found: false }}
  const rect = btn.getBoundingClientRect()
  return {{ found: true, disabled: btn.disabled, x: Math.round(rect.x + rect.width/2), y: Math.round(rect.y + rect.height/2) }}
}})()`)

if (!btn1.found || btn1.disabled) {{
  cliLog('ERROR: Post button issue on tweet 1')
  await completeTaskSpace(task.id, {{ keep: true }})
  process.exit(1)
}}

await cdp('Input.dispatchMouseEvent', {{ type: 'mouseMoved', x: btn1.x, y: btn1.y }})
await wait(0.3)
await cdp('Input.dispatchMouseEvent', {{ type: 'mousePressed', x: btn1.x, y: btn1.y, button: 'left', clickCount: 1 }})
await wait(0.1)
await cdp('Input.dispatchMouseEvent', {{ type: 'mouseReleased', x: btn1.x, y: btn1.y, button: 'left', clickCount: 1 }})
await wait(10)

const check1 = await js(String.raw`(() => {{
  const body = document.body.innerText
  if (body.includes('Your post was sent')) return 'CONFIRMED'
  return 'CHECK'
}})()`)
cliLog('Tweet 1: ' + check1)
''')

    # Subsequent tweets: reply to self
    for i in range(1, len(tweets)):
        n = i + 1
        segments.append(f'''
// === TWEET {n}/{len(tweets)} ===
await wait(20)

// Scroll up to find our just-posted tweet
await js('window.scrollTo(0, 0)')
await wait(3)

// Find the Reply button on the first tweet in feed (should be ours)
const replyRect{n} = await js(String.raw`(() => {{
  const btn = Array.from(document.querySelectorAll('button'))
    .find(b => b.getAttribute('aria-label')?.includes('Replies. Reply')
              || b.getAttribute('aria-label')?.includes('Reply'))
  if (btn) {{
    const rect = btn.getBoundingClientRect()
    if (rect.y > 0 && rect.y < 800) {{
      return {{ x: Math.round(rect.x + rect.width/2), y: Math.round(rect.y + rect.height/2), found: true }}
    }}
  }}
  return {{ found: false }}
}})()`)

if (!replyRect{n}.found) {{
  cliLog('WARN: Could not find reply button for tweet {n}, trying scroll')
  await js('window.scrollBy(0, -300)')
  await wait(2)
  // Retry
  const retry{n} = await js(String.raw`(() => {{
    const btn = Array.from(document.querySelectorAll('button'))
      .find(b => b.getAttribute('aria-label')?.includes('Reply'))
    if (btn) {{
      const rect = btn.getBoundingClientRect()
      return {{ x: Math.round(rect.x + rect.width/2), y: Math.round(rect.y + rect.height/2), found: rect.y > 0 }}
    }}
    return {{ found: false }}
  }})()`)
  if (!retry{n}.found) {{
    cliLog('ERROR: Still no reply button for tweet {n}')
    process.exit(1)
  }}
  replyRect{n}.x = retry{n}.x
  replyRect{n}.y = retry{n}.y
}}

// Click Reply
await cdp('Input.dispatchMouseEvent', {{ type: 'mouseMoved', x: replyRect{n}.x, y: replyRect{n}.y }})
await wait(0.2)
await cdp('Input.dispatchMouseEvent', {{ type: 'mousePressed', x: replyRect{n}.x, y: replyRect{n}.y, button: 'left', clickCount: 1 }})
await wait(0.1)
await cdp('Input.dispatchMouseEvent', {{ type: 'mouseReleased', x: replyRect{n}.x, y: replyRect{n}.y, button: 'left', clickCount: 1 }})
await wait(5)

// Focus editor
const editorRect{n} = await js(String.raw`(() => {{
  const elem = document.querySelector('[aria-label="Post your reply"]')
    || document.querySelector('[role="textbox"][contenteditable="true"]')
  if (!elem) return {{ found: false }}
  const rect = elem.getBoundingClientRect()
  return {{ x: Math.round(rect.x + 10), y: Math.round(rect.y + 10), found: true }}
}})()`)

if (!editorRect{n}.found) {{
  cliLog('ERROR: No editor for tweet {n}')
  process.exit(1)
}}

await cdp('Input.dispatchMouseEvent', {{ type: 'mouseMoved', x: editorRect{n}.x, y: editorRect{n}.y }})
await wait(0.1)
await cdp('Input.dispatchMouseEvent', {{ type: 'mousePressed', x: editorRect{n}.x, y: editorRect{n}.y, button: 'left', clickCount: 1 }})
await wait(0.05)
await cdp('Input.dispatchMouseEvent', {{ type: 'mouseReleased', x: editorRect{n}.x, y: editorRect{n}.y, button: 'left', clickCount: 1 }})
await wait(1)

await cdp('Input.insertText', {{ text: "{escaped_tweets[i]}" }})
await wait(3)

// Click Post/Reply button
let btn{n} = await js(String.raw`(() => {{
  const btn = document.querySelector('button[data-testid="tweetButtonInline"]')
    || document.querySelector('button[data-testid="tweetButton"]')
  if (!btn) return {{ found: false }}
  const rect = btn.getBoundingClientRect()
  return {{ found: true, disabled: btn.disabled, x: Math.round(rect.x + rect.width/2), y: Math.round(rect.y + rect.height/2) }}
}})()`)

if (btn{n}.disabled) {{
  // Fallback char-by-char
  cliLog('WARN: tweet {n} button disabled, trying char-by-char')
  await cdp('Input.dispatchKeyEvent', {{ type: 'keyDown', key: 'a', code: 'KeyA', windowsVirtualKeyCode: 65, modifiers: 4 }})
  await cdp('Input.dispatchKeyEvent', {{ type: 'keyUp', key: 'a', code: 'KeyA', windowsVirtualKeyCode: 65, modifiers: 4 }})
  await cdp('Input.dispatchKeyEvent', {{ type: 'keyDown', key: 'Backspace', code: 'Backspace', windowsVirtualKeyCode: 8 }})
  await cdp('Input.dispatchKeyEvent', {{ type: 'keyUp', key: 'Backspace', code: 'Backspace', windowsVirtualKeyCode: 8 }})
  await wait(0.5)
  const chars{n} = "{escaped_tweets[i]}"
  for (let i = 0; i < chars{n}.length; i++) {{
    await cdp('Input.dispatchKeyEvent', {{ type: 'keyDown', key: chars{n}[i], text: chars{n}[i], unmodifiedText: chars{n}[i] }})
    await cdp('Input.dispatchKeyEvent', {{ type: 'char', text: chars{n}[i] }})
    await cdp('Input.dispatchKeyEvent', {{ type: 'keyUp', key: chars{n}[i], text: chars{n}[i], unmodifiedText: chars{n}[i] }})
    if (i % 15 === 0) await wait(0.05)
  }}
  await wait(3)
  btn{n} = await js(String.raw`(() => {{
    const btn = document.querySelector('button[data-testid="tweetButtonInline"]')
      || document.querySelector('button[data-testid="tweetButton"]')
    if (!btn) return {{ found: false, disabled: true }}
    const rect = btn.getBoundingClientRect()
    return {{ found: true, disabled: btn.disabled, x: Math.round(rect.x + rect.width/2), y: Math.round(rect.y + rect.height/2) }}
  }})()`)
  if (btn{n}.disabled) {{
    cliLog('ERROR: tweet {n} button still disabled')
    process.exit(1)
  }}
}}

await cdp('Input.dispatchMouseEvent', {{ type: 'mouseMoved', x: btn{n}.x, y: btn{n}.y }})
await wait(0.3)
await cdp('Input.dispatchMouseEvent', {{ type: 'mousePressed', x: btn{n}.x, y: btn{n}.y, button: 'left', clickCount: 1 }})
await wait(0.1)
await cdp('Input.dispatchMouseEvent', {{ type: 'mouseReleased', x: btn{n}.x, y: btn{n}.y, button: 'left', clickCount: 1 }})
await wait(8)

const check{n} = await js(String.raw`(() => {{
  const body = document.body.innerText
  if (body.includes('Your post was sent')) return 'CONFIRMED'
  return 'CHECK'
}})()`)
cliLog('Tweet {n}: ' + check{n})
''')

    # Navigation + all segments + cleanup
    nav = f'''
const task = await useOrCreateTaskSpace('{task_space_name}')

// Navigate to home timeline
await openOrReuseTab('https://x.com/home', {{ wait: true, timeout: 30 }})
await cdp('Emulation.setDeviceMetricsOverride', {{ width: 1280, height: 900, deviceScaleFactor: 1, mobile: false }})
await wait(3)
await waitForNetworkIdle({{ timeout: 20 }})
await wait(5)
'''

    cleanup = '''
cliLog('THREAD COMPLETE')
await completeTaskSpace(task.id, { keep: false })
'''

    return nav + "\n".join(segments) + cleanup


def run_ego_script(script, timeout_sec=300):
    """Execute an ego-browser script and return (success, output)."""
    try:
        result = subprocess.run(
            [EGO_BROWSER, "nodejs"],
            input=script,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
        output = result.stdout + result.stderr
        success = "SUCCESS" in output or "THREAD COMPLETE" in output
        return success, output
    except subprocess.TimeoutExpired:
        return False, f"Timeout after {timeout_sec}s"
    except Exception as e:
        return False, str(e)


def post_single_tweet(text, source_type=None, source_id=None):
    """Post a single tweet to the timeline."""
    print(f"📝 Posting tweet: {text[:80]}...")

    script = build_single_tweet_script([text], TASK_SPACE_NAME)
    success, output = run_ego_script(script, timeout_sec=120)

    if success:
        print("✅ Tweet posted successfully!")
        record_post("single", text, 1, None, source_type, source_id, "posted")
        return True
    else:
        print(f"❌ Failed: {output[:200]}")
        record_post("single", text, 1, None, source_type, source_id, "failed", output[:500])
        return False


def post_thread(tweets, source_type=None, source_id=None):
    """Post a multi-tweet thread."""
    if len(tweets) == 1:
        return post_single_tweet(tweets[0], source_type, source_id)

    print(f"🧵 Posting thread ({len(tweets)} tweets)...")
    for i, t in enumerate(tweets):
        print(f"  [{i+1}] {t[:80]}...")

    # Use unique task space per thread to avoid conflicts
    task_name = f"sgos-thread-{int(time.time())}"
    script = build_thread_script(tweets, task_name)

    # Thread timeout: base 120s + 30s per additional tweet
    timeout = 120 + (len(tweets) - 1) * 40
    success, output = run_ego_script(script, timeout_sec=timeout)

    if success:
        print(f"✅ Thread posted successfully ({len(tweets)} tweets)!")
        record_post("thread", json.dumps(tweets), len(tweets), None,
                     source_type, source_id, "posted")
        return True
    else:
        print(f"❌ Thread failed: {output[:300]}")
        record_post("thread", json.dumps(tweets), len(tweets), None,
                     source_type, source_id, "failed", output[:500])
        return False


def get_top_pipeline_draft():
    """Get the highest-scoring unposted pipeline thread draft."""
    conn = get_db()
    row = conn.execute("""
        SELECT id, title, content, hook, score, grounding_score, created_at
        FROM pipeline_opportunities
        WHERE variant_type = 'thread'
          AND grounding_score >= 80
          AND dismissed = 0
        ORDER BY grounding_score DESC, score DESC
        LIMIT 1
    """).fetchone()
    conn.close()

    if not row:
        return None

    # Check if already posted
    conn2 = get_db()
    posted = conn2.execute("""
        SELECT id FROM posted_originals
        WHERE source_type = 'pipeline' AND source_id = ?
    """, (str(row["id"]),)).fetchone()
    conn2.close()

    if posted:
        return None

    return dict(row)


def parse_thread_content(content):
    """Parse thread content into individual tweets. Same logic as daily-alpha."""
    import re

    # Try numbered format: "1/ text\n\n2/ text"
    tweets = re.split(r'\n\s*\n\s*(?:\*\*)?(\d+)/(?:\*\*)?\s*', content)

    if len(tweets) > 2:
        result = []
        first = tweets[0].strip()
        first = re.sub(r'^(?:\*\*)?1/(?:\*\*)?\s*', '', first).strip()
        if first:
            result.append(first)
        for i in range(1, len(tweets) - 1, 2):
            text = tweets[i + 1].strip()
            if text:
                result.append(text)
        return result

    # Fallback: split on double newlines
    parts = [p.strip() for p in content.split("\n\n") if p.strip()]
    cleaned = []
    for p in parts:
        cleaned.append(re.sub(r'^(?:\*\*)?\d+/(?:\*\*)?\s*', '', p).strip())
    return [c for c in cleaned if c]


def pipeline_auto_post(dry_run=False):
    """Auto-post the top pipeline thread draft."""
    draft = get_top_pipeline_draft()

    if not draft:
        print("No pipeline drafts ready to post.")
        return False

    tweets = parse_thread_content(draft["content"])

    # Filter out tweets that are too long
    valid_tweets = [t for t in tweets if len(t) <= 280]
    if len(valid_tweets) < len(tweets):
        print(f"⚠️  {len(tweets) - len(valid_tweets)} tweets exceeded 280 chars, trimmed")

    if not valid_tweets:
        print("❌ No valid tweets in draft")
        return False

    print(f"\n{'='*60}")
    print(f"📋 Pipeline Draft: {draft['title']}")
    print(f"📊 Score: {draft['score']:.0f} | Grounding: {draft['grounding_score']}%")
    print(f"🧵 {len(valid_tweets)} tweets")
    print(f"{'='*60}")
    for i, t in enumerate(valid_tweets):
        print(f"\n  [{i+1}] ({len(t)} chars) {t}")

    if dry_run:
        print(f"\n🔍 DRY RUN — would post {len(valid_tweets)} tweets")
        return True

    # Check rate limit
    ok, minutes = check_rate_limit()
    if not ok:
        print(f"⏳ Rate limited: last post was {minutes:.0f} min ago (need {MIN_MINUTES_BETWEEN_POSTS})")
        return False

    if len(valid_tweets) == 1:
        return post_single_tweet(valid_tweets[0], "pipeline", str(draft["id"]))
    else:
        return post_thread(valid_tweets, "pipeline", str(draft["id"]))


def main():
    parser = argparse.ArgumentParser(description="SGOS Original Poster")
    parser.add_argument("--tweet", type=str, help="Post a single tweet")
    parser.add_argument("--thread", type=str, help="Path to JSON file with tweet array")
    parser.add_argument("--thread-text", type=str, nargs="+", help="Thread tweets as arguments")
    parser.add_argument("--pipeline", action="store_true", help="Auto-post top pipeline draft")
    parser.add_argument("--dry-run", action="store_true", help="Preview without posting")
    parser.add_argument("--rate-check", action="store_true", help="Check rate limit status")
    args = parser.parse_args()

    ensure_tables()

    if args.rate_check:
        ok, minutes = check_rate_limit()
        print(f"Rate limit: {'OK' if ok else 'BLOCKED'} (last post {minutes:.0f} min ago)")
        return

    if args.tweet:
        if args.dry_run:
            print(f"DRY RUN — would post: {args.tweet}")
        else:
            post_single_tweet(args.tweet)

    elif args.thread:
        with open(args.thread) as f:
            tweets = json.load(f)
        if args.dry_run:
            for i, t in enumerate(tweets):
                print(f"[{i+1}] ({len(t)} chars) {t}")
        else:
            post_thread(tweets)

    elif args.thread_text:
        tweets = args.thread_text
        if args.dry_run:
            for i, t in enumerate(tweets):
                print(f"[{i+1}] ({len(t)} chars) {t}")
        else:
            post_thread(tweets)

    elif args.pipeline:
        pipeline_auto_post(dry_run=args.dry_run)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
