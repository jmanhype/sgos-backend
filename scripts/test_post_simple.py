#!/usr/bin/env python3
"""
Simple test: post one tweet using the exact pattern from post_with_ego.py
"""
import subprocess
import json

EGO_BROWSER = "/Users/speed/.local/bin/ego-browser"

# Test tweet with line breaks
test_text = "Most founders think AI is just a fancy chatbot.\n\nWe built an AI Second Brain 30 days ago and it completely changed how we run our business."
escaped = test_text.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("\n", "\\n")

script = f'''
const task = await useOrCreateTaskSpace('test-post-simple')

await openOrReuseTab('https://x.com/home', {{ wait: true, timeout: 30 }})
await cdp('Emulation.setDeviceMetricsOverride', {{ width: 1280, height: 800, deviceScaleFactor: 1, mobile: false }})
await wait(2)
await waitForNetworkIdle({{ timeout: 20 }})
await wait(5)

// Click the composer area via CDP (trusted click)
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
cliLog('Composer found at ' + composerRect.x + ',' + composerRect.y)
await cdp('Input.dispatchMouseEvent', {{ type: 'mouseMoved', x: composerRect.x, y: composerRect.y }})
await wait(0.1)
await cdp('Input.dispatchMouseEvent', {{ type: 'mousePressed', x: composerRect.x, y: composerRect.y, button: 'left', clickCount: 1 }})
await wait(0.05)
await cdp('Input.dispatchMouseEvent', {{ type: 'mouseReleased', x: composerRect.x, y: composerRect.y, button: 'left', clickCount: 1 }})
await wait(1)

// INSERT TEXT via CDP Input.insertText (trusted events)
await cdp('Input.insertText', {{ text: "{escaped}" }})
await wait(3)

// Check if submit button is enabled
const btnInfo = await js(String.raw`(() => {{
  const btn = document.querySelector('button[data-testid="tweetButtonInline"]')
    || document.querySelector('button[data-testid="tweetButton"]')
  if (!btn) return {{ found: false }}
  const rect = btn.getBoundingClientRect()
  return {{ found: true, disabled: btn.disabled, x: Math.round(rect.x + rect.width/2), y: Math.round(rect.y + rect.height/2) }}
}})()`)

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
  const chars = "{escaped}"
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
  }})()`)
  btnInfo.found = btn2.found
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
}})()`)

if (sentCheck === 'CONFIRMED' || sentCheck === 'EMPTY') {{
  cliLog('SUCCESS')
}} else {{
  cliLog('FAILED: ' + sentCheck)
  await completeTaskSpace(task.id, {{ keep: true }})
  process.exit(1)
}}

await completeTaskSpace(task.id, {{ keep: false }})
'''

print("Running ego-browser test...")
result = subprocess.run(
    [EGO_BROWSER, 'nodejs'],
    input=script,
    capture_output=True,
    text=True,
    timeout=120
)

print("STDOUT:")
print(result.stdout)
print("\nSTDERR:")
print(result.stderr)
print(f"\nExit code: {result.returncode}")

if 'SUCCESS' in result.stdout:
    print("\n✅ Tweet posted successfully!")
else:
    print("\n❌ Tweet failed")
