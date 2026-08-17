#!/usr/bin/env python3
"""Test post with JS clicks — fixed cliLog scoping."""
import subprocess

EGO_BROWSER = "/Users/speed/.local/bin/ego-browser"

test_text = "Most founders think AI is just a fancy chatbot.\n\nWe built an AI Second Brain 30 days ago and it completely changed how we run our business."
escaped = test_text.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("\n", "\\n")

script = f'''
const task = await useOrCreateTaskSpace('test-post-v3')

await openOrReuseTab('https://x.com/home', {{ wait: true, timeout: 30 }})
await cdp('Emulation.setDeviceMetricsOverride', {{ width: 1280, height: 800, deviceScaleFactor: 1, mobile: false }})
await wait(2)
await waitForNetworkIdle({{ timeout: 20 }})
await wait(5)

// Click the composer using JS click
await js(String.raw`(() => {{
  const composer = document.querySelector('[role="textbox"][aria-label="Post text"]')
    || document.querySelector('[role="textbox"]')
  if (composer) {{
    composer.click()
    composer.focus()
  }}
}})()`)
await wait(1)

// INSERT TEXT via CDP Input.insertText
await cdp('Input.insertText', {{ text: "{escaped}" }})
await wait(3)

// Check editor content
const editorText = await js(String.raw`(() => {{
  const editor = document.querySelector('[role="textbox"]')
  return editor ? editor.innerText : 'NOT_FOUND'
}})()`)
cliLog('Editor: ' + editorText.substring(0, 80))

// Check button state
const btnState = await js(String.raw`(() => {{
  const btn = document.querySelector('button[data-testid="tweetButton"]')
    || document.querySelector('button[data-testid="tweetButtonInline"]')
  if (!btn) return 'NOT_FOUND'
  if (btn.disabled) return 'DISABLED'
  btn.click()
  return 'CLICKED'
}})()`)
cliLog('Button: ' + btnState)

if (btnState === 'DISABLED' || btnState === 'NOT_FOUND') {{
  cliLog('ERROR: ' + btnState)
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
  return 'STILL_HAS_TEXT: ' + texts.join(' | ').substring(0, 50)
}})()`)

cliLog('Verify: ' + sentCheck)

if (sentCheck === 'CONFIRMED' || sentCheck === 'EMPTY') {{
  cliLog('SUCCESS')
}} else {{
  cliLog('FAILED: ' + sentCheck)
  await completeTaskSpace(task.id, {{ keep: true }})
  process.exit(1)
}}

await completeTaskSpace(task.id, {{ keep: false }})
'''

print("Running...")
result = subprocess.run([EGO_BROWSER, 'nodejs'], input=script, capture_output=True, text=True, timeout=120)
output = result.stdout + result.stderr
print(output)
if 'SUCCESS' in output:
    print("\n✅ Posted!")
else:
    print("\n❌ Failed")
