#!/usr/bin/env python3
"""
Post pending strikes using ego-browser, one at a time.
Marks them as posted in the database after successful post.
"""
import sys
import subprocess
import json
from database import get_connection
from datetime import datetime

def post_strike_with_ego(strike_id, tweet_url, draft):
    """Post a single strike using ego-browser. ALL interactions via CDP (trusted events)."""
    
    # Escape draft for JS template literal
    escaped_draft = draft.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("\n", "\\n")
    
    script = f'''
const task = await useOrCreateTaskSpace('post-strike-{strike_id}')

await openOrReuseTab('{tweet_url}', {{ wait: true, timeout: 30 }})
await cdp('Emulation.setDeviceMetricsOverride', {{ width: 1280, height: 800, deviceScaleFactor: 1, mobile: false }})
await wait(2)
await waitForNetworkIdle({{ timeout: 20 }})
await wait(10)

// Click Reply button via CDP mouse event (trusted)
const replyBtnRect = await js(String.raw`(() => {{
  const btn = Array.from(document.querySelectorAll('button'))
    .find(b => b.getAttribute('aria-label')?.includes('Replies. Reply'))
  if (btn) {{
    const rect = btn.getBoundingClientRect()
    return {{ x: Math.round(rect.x + rect.width/2), y: Math.round(rect.y + rect.height/2) }}
  }}
  return null
}})()`)\n
if (!replyBtnRect) {{
  cliLog('ERROR: Could not find Reply button')
  await completeTaskSpace(task.id, {{ keep: true }})
  process.exit(1)
}}
await cdp('Input.dispatchMouseEvent', {{ type: 'mouseMoved', x: replyBtnRect.x, y: replyBtnRect.y }})
await wait(0.2)
await cdp('Input.dispatchMouseEvent', {{ type: 'mousePressed', x: replyBtnRect.x, y: replyBtnRect.y, button: 'left', clickCount: 1 }})
await wait(0.1)
await cdp('Input.dispatchMouseEvent', {{ type: 'mouseReleased', x: replyBtnRect.x, y: replyBtnRect.y, button: 'left', clickCount: 1 }})
await wait(5)

// Focus editor via CDP click
const editorRect = await js(String.raw`(() => {{
  const elem = document.querySelector('[aria-label="Post text"], [aria-label="Post your reply"]')
  if (!elem) return null
  const rect = elem.getBoundingClientRect()
  return {{ x: Math.round(rect.x + 10), y: Math.round(rect.y + 10) }}
}})()`)\n
if (!editorRect) {{
  cliLog('ERROR: Could not find editor element')
  await completeTaskSpace(task.id, {{ keep: true }})
  process.exit(1)
}}
await cdp('Input.dispatchMouseEvent', {{ type: 'mouseMoved', x: editorRect.x, y: editorRect.y }})
await wait(0.1)
await cdp('Input.dispatchMouseEvent', {{ type: 'mousePressed', x: editorRect.x, y: editorRect.y, button: 'left', clickCount: 1 }})
await wait(0.05)
await cdp('Input.dispatchMouseEvent', {{ type: 'mouseReleased', x: editorRect.x, y: editorRect.y, button: 'left', clickCount: 1 }})
await wait(1)

// INSERT TEXT via CDP Input.insertText (trusted events)
await cdp('Input.insertText', {{ text: "{escaped_draft}" }})
await wait(3)

// Check if submit button is enabled
const btnInfo = await js(String.raw`(() => {{
  const btn = document.querySelector('button[data-testid="tweetButtonInline"]')
    || document.querySelector('button[data-testid="tweetButton"]')
  if (!btn) return {{ found: false }}
  const rect = btn.getBoundingClientRect()
  return {{ found: true, disabled: btn.disabled, x: Math.round(rect.x + rect.width/2), y: Math.round(rect.y + rect.height/2) }}
}})()`)\n
cliLog('Button: found=' + btnInfo.found + ' disabled=' + btnInfo.disabled)

if (!btnInfo.found) {{
  cliLog('ERROR: submit button not found')
  await completeTaskSpace(task.id, {{ keep: true }})
  process.exit(1)
}}

if (btnInfo.disabled) {{
  // Fallback: char-by-char CDP typing
  cliLog('WARN: button disabled, trying char-by-char CDP typing')
  await cdp('Input.dispatchKeyEvent', {{ type: 'keyDown', key: 'a', code: 'KeyA', windowsVirtualKeyCode: 65, modifiers: 4 }})
  await cdp('Input.dispatchKeyEvent', {{ type: 'keyUp', key: 'a', code: 'KeyA', windowsVirtualKeyCode: 65, modifiers: 4 }})
  await cdp('Input.dispatchKeyEvent', {{ type: 'keyDown', key: 'Backspace', code: 'Backspace', windowsVirtualKeyCode: 8 }})
  await cdp('Input.dispatchKeyEvent', {{ type: 'keyUp', key: 'Backspace', code: 'Backspace', windowsVirtualKeyCode: 8 }})
  await wait(0.5)
  const chars = "{escaped_draft}"
  for (let i = 0; i < chars.length; i++) {{
    await cdp('Input.dispatchKeyEvent', {{ type: 'keyDown', key: chars[i], text: chars[i], unmodifiedText: chars[i] }})
    await cdp('Input.dispatchKeyEvent', {{ type: 'char', text: chars[i] }})
    await cdp('Input.dispatchKeyEvent', {{ type: 'keyUp', key: chars[i], text: chars[i], unmodifiedText: chars[i] }})
    if (i % 15 === 0) await wait(0.05)
  }}
  await wait(3)
  const btn2 = await js(String.raw`(() => {{
    const btn = document.querySelector('button[data-testid="tweetButtonInline"]')
      || document.querySelector('button[data-testid="tweetButton"]')
    if (!btn) return {{ found: false, disabled: true }}
    const rect = btn.getBoundingClientRect()
    return {{ found: true, disabled: btn.disabled, x: Math.round(rect.x + rect.width/2), y: Math.round(rect.y + rect.height/2) }}
  }})()`)\n  btnInfo.found = btn2.found
  btnInfo.disabled = btn2.disabled
  btnInfo.x = btn2.x
  btnInfo.y = btn2.y
  if (btnInfo.disabled) {{
    cliLog('ERROR: button STILL disabled')
    await completeTaskSpace(task.id, {{ keep: true }})
    process.exit(1)
  }}
}}

// SUBMIT via CDP mouse click
cliLog('Submit: CDP click at ' + btnInfo.x + ',' + btnInfo.y)
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
}})()`)\n
if (sentCheck === 'CONFIRMED' || sentCheck === 'EMPTY') {{
  cliLog('SUCCESS')
}} else {{
  cliLog('FAILED: ' + sentCheck)
}}

await completeTaskSpace(task.id, {{ keep: false }})
'''
    
    try:
        result = subprocess.run(
            ['/Users/speed/.local/bin/ego-browser', 'nodejs'],
            input=script,
            capture_output=True,
            text=True,
            timeout=120
        )
        output = result.stdout + result.stderr
        return 'SUCCESS' in output, output
    except subprocess.TimeoutExpired:
        return False, "Timeout"
    except Exception as e:
        return False, str(e)

def main():
    conn = get_connection()
    
    # Get ONE pending strike, excluding any we've recently posted to (dedup by tweet URL)
    pending = conn.execute("""
        SELECT id, target_author, target_tweet_url, reply_draft
        FROM strikes
        WHERE status = 'pending'
        AND target_tweet_url NOT IN (
            SELECT target_tweet_url FROM strikes 
            WHERE status = 'posted' 
            AND posted_at > datetime('now', '-1 hour')
        )
        ORDER BY created_at DESC
        LIMIT 1
    """).fetchone()
    
    if not pending:
        print("No pending strikes to post")
        return
    
    strike_id, author, url, draft = pending
    
    print(f"Posting to @{author}: {url[:60]}...")
    print(f"Draft: {draft[:80]}...")
    
    # Mark as attempted
    conn.execute("""
        UPDATE strikes 
        SET last_attempt = datetime('now')
        WHERE id = ?
    """, (strike_id,))
    conn.commit()
    
    # Post
    success, output = post_strike_with_ego(strike_id, url, draft)
    
    if success:
        print(f"✅ Posted successfully!")
        conn.execute("""
            UPDATE strikes 
            SET status = 'posted', posted_at = datetime('now')
            WHERE id = ?
        """, (strike_id,))
    else:
        print(f"❌ Failed: {output[:200]}")
    
    conn.commit()

if __name__ == '__main__':
    main()
