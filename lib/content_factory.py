"""Autonomous content factory loop — pick, brief, produce (H3 + Flux3), log.

Wires together:
  - VBL brief API   (http://127.0.0.1:8002/v1/agent/brief)  -> prompts
  - H3 pipeline     (http://127.0.0.1:8420/v1/h3/*)          -> Wan2GP jobs on 3090
  - ego-bridge      (http://127.0.0.1:8040/v1/*)             -> Flux 3 Discord gen

Each cycle picks a least-recently-used (style, franchise) combo, gets a brief,
submits H3 + Flux3, polls both to completion, downloads results, and logs the
production to data/production_log.json.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

from lib.h3_pipeline import build_h3_config_with_fallback
from lib.production_store import (
    to_host_path,
    PRODUCTIONS_ROOT,
    check_disk_quota,
    compute_file_hash,
    get_production_path,
    register_production,
    write_meta_sidecar,
)

logger = logging.getLogger(__name__)

# ─── Endpoints (override via env) ─────────────────────────────────────────────
VBL_BRIEF_URL = os.environ.get(
    "VBL_BRIEF_URL", "http://127.0.0.1:8002/v1/agent/brief"
)
H3_BASE_URL = os.environ.get("H3_BASE_URL", "http://127.0.0.1:8420")
# Host-side h3-bridge (HTTP) used by the container — containers can't SSH to LAN.
H3_BRIDGE_URL = os.environ.get("H3_BRIDGE_URL", "http://host.docker.internal:8041")
EGO_BRIDGE_URL = os.environ.get("EGO_BRIDGE_URL", "http://127.0.0.1:8040")

# ─── Data assets ───────────────────────────────────────────────────────────────
_HOME = Path.home()
VISUAL_REGISTER = Path(
    os.environ.get(
        "VISUAL_REGISTER", str(_HOME / "viral-bench-local" / "data" / "visual_register.json")
    )
)
LOST_FUTURES_INDEX = Path(
    os.environ.get(
        "LOST_FUTURES_INDEX",
        str(_HOME / "gist-archive" / "gists-film-aesthetics" / "lost-futures"),
    )
)
LOG_PATH = Path(os.environ.get("PRODUCTION_LOG", "data/production_log.json"))

# Discord #gen-1 channel where the Flux 3 bot listens.
FLUX_GEN_CHANNEL = os.environ.get(
    "FLUX_GEN_CHANNEL",
    "https://discord.com/channels/1501633423859650610/1534279818450043162",
)

# Cap how long we wait for one production (H3 or Flux3) before giving up.
H3_POLL_SECONDS = int(os.environ.get("H3_POLL_SECONDS", "1800"))
# Disabled in favor of FLUX_POLL_INTERVAL/ATTEMPTS in _poll_flux3 above (kept
# for backward env compatibility of the overall poll budget, not used):
FLUX_POLL_SECONDS = int(os.environ.get("FLUX_POLL_SECONDS", "900"))

# Output directory for downloaded results (local path writable by this process).
OUTPUT_DIR = Path(os.environ.get("FACTORY_OUTPUT_DIR", "outputs/factory"))


# ─── 1. Picking ────────────────────────────────────────────────────────────────


def _load_visual_register() -> list[dict]:
    if not VISUAL_REGISTER.exists():
        return []
    try:
        data = json.loads(VISUAL_REGISTER.read_text())
        return data.get("styles") or []
    except (json.JSONDecodeError, OSError):
        return []


# Cache the visual register once at module level so the coherence gate (and the
# picker) don't re-read the file on every production. Container path lives at
# /app/vbl-data/visual_register.json via the VISUAL_REGISTER env var.
_STYLES_CACHE: Optional[list[dict]] = None


def _styles() -> list[dict]:
    global _STYLES_CACHE
    if _STYLES_CACHE is None:
        _STYLES_CACHE = _load_visual_register()
    return _STYLES_CACHE


def _get_style_guide(style_id: str) -> Optional[dict]:
    """Return the dialogue_guide for a style_id, or None if not found."""
    if not style_id:
        return None
    for s in _styles():
        if s.get("id") == style_id:
            dg = s.get("dialogue_guide")
            return dg if isinstance(dg, dict) else None
    return None


def _load_franchises() -> list[str]:
    """Franchise/aesthetic names from the lost-futures index.

    Each top-level dir is treated as a franchise group, and any markdown file
    inside contributes its stem. Leading `YYYY-MM-DD_` date prefixes are
    stripped so generated premises read naturally.

    Falls back to a built-in list from the SGFLIX Lost Futures index when
    the external gist-archive directory isn't available (e.g. inside Docker).
    """
    import re as _re
    if not LOST_FUTURES_INDEX.is_dir():
        # Built-in fallback: top franchises from sgflix-lost-futures-index
        return [
            "magnitude-kaiju", "proximity-kaiju", "kart-hell-95",
            "megacorp-office", "jurassic-live-pd", "fnaf-cctv",
            "arcade-demons", "block-mission", "curriculum-breach",
            "inner-voltage", "darkknight-sentai", "grove-street-stories",
            "aisle-13", "bone-kitchen", "cloud-gardener",
            "cosmonaut-9", "crimson-kabuki", "cul-de-sac-jutsu",
            "dissolve", "el-coyote", "jade-express",
            "iron-hands", "daughters-of-the-sun",
            "mario-kart-twisted-metal", "imperial-hr",
            "hawkins-911", "daily-bugle", "hbo-muppets",
            "kitchen-nightmares", "warhammer-40k",
            "pokemon-underground", "godzilla-iphone-pov",
            "bodycam-chaos", "ecohorror-puppet", "soviet-space-opera",
            "mall-slasher", "evidence-room-toy", "occult-school",
            "fitness-vhs", "ninja-hoa", "aquatic-puppet",
            "factory-mascot", "fashion-doll-recruitment",
            "used-car-robot", "wall-crawler-psa", "teen-counseling",
            "cursed-relic-fantasy", "tuner-racing", "toddler-mascot",
            "superteam-disaster",
        ]
    franchises: list[str] = []
    def clean(name: str) -> str:
        return _re.sub(r"^\d{4}-\d{2}-\d{2}_", "", name)
    def keep(name: str) -> bool:
        # Skip hidden gist-archive artifacts and empty names.
        return bool(name.strip()) and not name.startswith(".") and ".gist-meta" not in name
    for d in sorted(LOST_FUTURES_INDEX.iterdir()):
        if d.is_dir():
            c = clean(d.name)
            if keep(c):
                franchises.append(c)
        for f in sorted(d.glob("*.md")):
            c = clean(f.stem)
            if keep(c):
                franchises.append(c)
    # Dedup, keep order.
    seen: set[str] = set()
    out: list[str] = []
    for name in franchises:
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def _load_log() -> list[dict]:
    if not LOG_PATH.exists():
        return []
    try:
        data = json.loads(LOG_PATH.read_text())
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "productions" in data:
            return data["productions"]
        return []
    except (json.JSONDecodeError, OSError):
        return []


def _append_log(entry: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = _load_log()
    data.append(entry)
    LOG_PATH.write_text(json.dumps(data, indent=2))


def _recent_production_exists(style_id: str, franchise: str, premise: str, window_h: int = 24) -> Optional[dict]:
    """Return the most recent matching `productions` row inside `window_h`, or None.

    Queries the production store's SQLite table directly (best-effort; returns
    None on any error so dedup never blocks a production).
    """
    try:
        from database import get_connection
        conn = get_connection()
        row = conn.execute(
            "SELECT id, premise, generated_at FROM productions "
            "WHERE style_id = ? AND franchise = ? AND premise = ? "
            "AND generated_at >= datetime('now', ?) ORDER BY generated_at DESC LIMIT 1",
            (style_id, franchise, premise, f"-{window_h} hours"),
        ).fetchone()
        return dict(row) if row else None
    except Exception as exc:
        logger.warning(f"_recent_production_exists: {exc}")
        return None


def pick_next_production() -> dict:
    """Pick the least-recently-used (style, franchise) combo and return a premise.

    Returns {style_id, style_name, franchise, premise, niche, scenario}.
    """
    styles = _load_visual_register()
    franchises = _load_franchises()
    if not styles or not franchises:
        raise RuntimeError(
            "No styles/franchises available — check visual_register.json and "
            "the lost-futures index paths."
        )

    log = _load_log()
    # LRU: record of last-produced (style_id, franchise).
    last_used: dict[tuple[str, str], float] = {}
    for entry in log:
        key = (entry.get("style_id", ""), entry.get("franchise", ""))
        ts = entry.get("ts", 0) or 0
        if ts > last_used.get(key, 0):
            last_used[key] = ts

    best_key: Optional[tuple[str, str]] = None
    best_ts: Optional[float] = None
    for style in styles:
        sid = style.get("id", str(style))
        for franchise in franchises:
            ts = last_used.get((sid, franchise), 0.0)
            if best_ts is None or ts < best_ts:
                best_ts = ts
                best_key = (sid, franchise)

    if best_key is None:
        # Loop over a non-empty style/franchise set always sets best_key.
        raise RuntimeError("no style/franchise combo found")

    style = next((s for s in styles if s.get("id") == best_key[0]), {})
    style_name = style.get("name", best_key[0])
    franchise = best_key[1]

    # A fresh random variable makes each premise unique even for repeat combos.
    variable = random.choice([
        "a midnight diner", "a rooftop chase", "the last train home",
        "an abandoned arcade", "a neon-lit rainstorm", "a desert road at dusk",
        "a crowded market", "a frozen lake", "a burning library",
        "the morning after a storm",
    ])
    premise = f"{style_name} in the {franchise} world: {variable}"

    return {
        "style_id": best_key[0],
        "style_name": style_name,
        "franchise": franchise,
        "premise": premise,
        "niche": (style.get("niche") or "comedy"),
        "scenario": variable,
    }


# ─── 2. Production cycle ───────────────────────────────────────────────────────


async def _get_brief(pick: dict) -> dict:
    """Call VBL brief API for the picked style/premise."""
    payload = {
        "niche": pick["niche"],
        "goal": pick["premise"],
        "style_id": pick["style_id"],
        "max_rounds": 3,
    }
    print(f"[BRIEF] Sending goal={json.dumps(payload['goal'])[:120]!r} to {VBL_BRIEF_URL}", flush=True)
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(VBL_BRIEF_URL, json=payload)
        r.raise_for_status()
        return r.json()


def _convert_to_wgp_format(job: dict) -> dict:
    """Convert VBL's h3_multishot_json to wgp.py-compatible format.

    VBL format: {shots: [{prompt, ...}, ...], frames_per_shot, ...}
    wgp.py format: {model_type, prompt, script: "shot1\\n---\\nshot2", frames_per_shot, ...}
    """
    shots = job.get("shots")
    if not isinstance(shots, list) or not shots:
        # Already in single-shot or wgp format — just ensure model_type exists
        out = dict(job)
        out.setdefault("model_type", "minimax_h3_fl2va_pruned")
        out.setdefault("prompt", "factory-generated")
        out.setdefault("force_fps", "24")
        return out

    # Build script from shots with --- separators
    script_parts = []
    for shot in shots:
        prompt = shot.get("prompt", "")
        motion = shot.get("motion_prompt", "")
        combined = f"{prompt}, {motion}" if motion else prompt
        script_parts.append(combined)

    script = "\n---\n".join(script_parts)

    # Build wgp.py-compatible config
    out = {
        "model_type": job.get("model_type", "minimax_h3_fl2va_pruned"),
        "prompt": "multishot",
        "script": script,
        "width": job.get("width", 480),
        "height": job.get("height", 832),
        "frames_per_shot": job.get("frames_per_shot", 176),
        "num_inference_steps": job.get("num_inference_steps", 20),
        "guidance_scale": job.get("guidance_scale", 1.0),
        "embedded_guidance_scale": job.get("embedded_guidance_scale", 6.0),
        "force_fps": str(job.get("force_fps", "24")),
        "seed": job.get("seed", 42),
    }
    return out


async def _send_flux3(brief: dict) -> dict:
    """Navigate ego-bridge to #gen-1 and send the Flux3 /t2v command.

    Returns dict with 'sent' (bool) and optionally 'message_id' (str) of the
    sent message for targeted reply tracking.
    """
    inner = brief.get("brief", brief)
    pp = inner.get("production_prompts", {})
    flux3_cmd = pp.get("flux3")
    if not flux3_cmd:
        logger.error("_send_flux3: no flux3 prompt in brief production_prompts")
        return {"sent": False}
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            await client.post(f"{EGO_BRIDGE_URL}/v1/navigate",
                              json={"url": FLUX_GEN_CHANNEL, "wait": True, "timeout": 40})
            # Discord needs time to settle after navigation
            await asyncio.sleep(5)
            # Find & focus the Discord message box, type, then press Enter.
            js_focus = (
                "(()=>{const e=[...document.querySelectorAll('div[contenteditable=true]')]"
                ".find(x=>(x.getAttribute('aria-label')||'').startsWith('Message'));"
                "if(e){e.focus(); return 'ok';} return 'no-input';})()"
            )
            r = await client.post(f"{EGO_BRIDGE_URL}/v1/evaluate", json={"js": js_focus})
            rdata = r.json()
            result_val = rdata.get("result") or rdata.get("output", "")
            if "ok" not in str(result_val):
                logger.error(f"_send_flux3: could not focus Discord input: {rdata}")
                return {"sent": False}
            await client.post(f"{EGO_BRIDGE_URL}/v1/type",
                              json={"selector": 'div[contenteditable=true][aria-label^="Message"]', "text": flux3_cmd})
            await asyncio.sleep(1)
            enter_js = (
                "(()=>{const e=[...document.querySelectorAll('div[contenteditable=true]')]"
                ".find(x=>(x.getAttribute('aria-label')||'').startsWith('Message'));"
                "if(!e) return false; e.focus();"
                "const k=new KeyboardEvent('keydown',{key:'Enter',code:'Enter',keyCode:13,which:13,bubbles:true,cancelable:true});"
                "return e.dispatchEvent(k);})()"
            )
            await client.post(f"{EGO_BRIDGE_URL}/v1/evaluate", json={"js": enter_js})
            logger.info("_send_flux3: sent Flux3 command to Discord")

            # Wait for our message to appear, then extract its message ID
            await asyncio.sleep(3)
            message_id = None
            # Discord uses id="chat-messages-{channel_id}-{message_id}" format
            # Extract message IDs from elements whose id matches that pattern
            prompt_snippet = flux3_cmd[:60].replace("'", "\\'").replace("\n", " ")
            js_msg_id = (
                f"(()=>{{"
                f"const els=[...document.querySelectorAll('li[id],div[id]')];"
                f"const msgEls=els.filter(e=>e.id.match(/\\d{{17,20}}$/)&&e.textContent.includes('{prompt_snippet}'));"
                f"if(msgEls.length===0) return '';"
                f"const last=msgEls[msgEls.length-1];"
                f"const m=last.id.match(/(\\d{{17,20}})$/);"
                f"return m?m[1]:'';"
                f"}})()"
            )
            try:
                r2 = await client.post(f"{EGO_BRIDGE_URL}/v1/evaluate", json={"js": js_msg_id})
                mid = (r2.json().get("result") or "").strip()
                if mid and mid.isdigit():
                    message_id = mid
                    logger.info(f"_send_flux3: captured message_id={message_id}")
                else:
                    logger.warning(f"_send_flux3: could not extract message_id (got '{mid}')")
            except Exception as exc:
                logger.warning(f"_send_flux3: message_id extraction failed: {exc}")

            return {"sent": True, "message_id": message_id}
    except Exception as exc:
        logger.error(f"_send_flux3: failed: {exc}")
        return {"sent": False}


# Flux3 result polling: poll each 15s for up to ~5 minutes (20 attempts).
FLUX_POLL_INTERVAL = int(os.environ.get("FLUX_POLL_INTERVAL", "15"))
FLUX_POLL_ATTEMPTS = int(os.environ.get("FLUX_POLL_ATTEMPTS", "20"))


def _cdn_js() -> str:
    """JS to collect ALL Discord CDN mp4 sources from videos/sources in the page."""
    return (
        "(()=>{"
        "const urls=new Set();"
        "document.querySelectorAll('video source, video[src]').forEach(v=>{"
        "const s=(v.currentSrc||v.src||'').split('?')[0];"
        "if(s.includes('cdn.discordapp.com')&&s.includes('.mp4'))urls.add(s);"
        "});"
        "return JSON.stringify([...urls]);"
        "})()"
    )


async def _collect_cdn_urls() -> set[str]:
    """Return the current set of Discord CDN mp4 URLs visible in the page."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(f"{EGO_BRIDGE_URL}/v1/evaluate", json={"js": _cdn_js()})
            raw = (r.json().get("result") or "").strip()
            urls = json.loads(raw) if raw.startswith("[") else []
            return set(urls)
    except Exception as exc:
        logger.warning(f"_collect_cdn_urls: failed to read CDN urls: {exc}")
        return set()


def _reply_cdn_js(message_id: Optional[str] = None) -> str:
    """JS to collect CDN mp4 URLs from Discord.

    Primary (message_id set): look for result_A.mp4 / result_B.mp4 patterns
    in messages after our message_id, plus any reply-chain matches.
    Fallback (message_id None): scan all video URLs on page.
    """
    if message_id:
        # Strategy: find ALL mp4 URLs on page, but prioritize those in messages
        # that reference our message_id OR contain result_A/result_B filenames.
        return (
            f"(()=>{{"
            f"const targetId='{message_id}';"
            f"const allUrls=new Set();"
            f"const targetedUrls=new Set();"
            # Collect ALL video URLs on the page
            f"document.querySelectorAll('video source, video[src]').forEach(v=>{{"
            f"  const s=(v.currentSrc||v.src||'');"
            f"  if(s.includes('cdn.discordapp.com')&&s.includes('.mp4'))allUrls.add(s);"
            f"}});"
            # Find messages referencing our message_id or containing result_ pattern
            f"const allEls=[...document.querySelectorAll('li[id],div[id]')];"
            f"const msgEls=allEls.filter(e=>/\\d{{17,20}}$/.test(e.id));"
            f"msgEls.forEach(m=>{{"
            f"  const text=m.textContent||'';"
            f"  const hasRef=text.includes(targetId);"
            f"  const hasResult=/result_[AB]\\.mp4/.test(text)||/attachments\\/\\d+\\/\\d+\\//.test(text);"
            f"  if(hasRef||hasResult){{"
            f"    m.querySelectorAll('video source, video[src]').forEach(v=>{{"
            f"      const s=v.currentSrc||v.src||'';"
            f"      if(s.includes('cdn.discordapp.com')&&s.includes('.mp4'))targetedUrls.add(s);"
            f"    }});"
            f"  }}"
            f"}});"
            f"return JSON.stringify({{"
            f"  all:[...allUrls],"
            f"  targeted:[...targetedUrls],"
            f"  msgCount:msgEls.length,"
            f"  matchCount:targetedUrls.size"
            f"}});"
            f"}})()"
        )
    else:
        return _cdn_js()


async def _poll_flux3(
    seen_urls: Optional[set] = None,
    message_id: Optional[str] = None,
) -> Optional[str]:
    """Poll Discord for result.mp4 CDN url from Flux3 bot.

    Primary mode (message_id set): looks for result_A/result_B mp4 files in
    messages referencing our message_id, plus tracks new URLs vs baseline.
    Fallback mode (message_id None): scans all new video URLs vs baseline.

    Polls every FLUX_POLL_INTERVAL (15s) for up to ~5 minutes.
    Returns the full signed CDN URL, or None on timeout.
    """
    seen = set(seen_urls or set())
    mode = "targeted" if message_id else "baseline"
    print(f"[FLUX3] _poll_flux3: {mode} mode, message_id={message_id}, baseline_seen={len(seen)}", flush=True)
    logger.info(
        f"_poll_flux3: {mode} mode, polling every {FLUX_POLL_INTERVAL}s, "
        f"up to {FLUX_POLL_ATTEMPTS} attempts"
        + (f"; message_id={message_id}" if message_id else f"; baseline seen={len(seen)} urls")
    )
    async with httpx.AsyncClient(timeout=30) as client:
        for attempt in range(1, FLUX_POLL_ATTEMPTS + 1):
            try:
                js = _reply_cdn_js(message_id)
                r = await client.post(f"{EGO_BRIDGE_URL}/v1/evaluate", json={"js": js})
                raw = (r.json().get("result") or "").strip()
                print(f"[FLUX3] attempt {attempt}: raw response length={len(raw)}", flush=True)

                if message_id and raw.startswith("{"):
                    # Targeted mode: parse structured response
                    try:
                        data = json.loads(raw)
                        all_urls = data.get("all", [])
                        targeted = data.get("targeted", [])
                        msg_count = data.get("msgCount", 0)
                        match_count = data.get("matchCount", 0)
                        print(
                            f"[FLUX3] attempt {attempt}: msgs={msg_count}, "
                            f"targeted_matches={match_count}, all_videos={len(all_urls)}, "
                            f"targeted_urls={len(targeted)}",
                            flush=True,
                        )
                        logger.info(
                            f"_poll_flux3: attempt {attempt}/{FLUX_POLL_ATTEMPTS} "
                            f"msgs={msg_count} targeted={match_count} all={len(all_urls)}"
                        )
                        # Priority 1: targeted URLs (from reply/result_ messages)
                        new_targeted = [u for u in targeted if u not in seen]
                        if new_targeted:
                            full = new_targeted[-1]
                            print(f"[FLUX3] TARGETED RESULT: {full}", flush=True)
                            logger.info(f"_poll_flux3: targeted result: {full}")
                            return full
                        # Priority 2: any new URL not in baseline (catches result_A/B even without reply match)
                        new_all = [u for u in all_urls if u not in seen]
                        if new_all:
                            full = new_all[-1]
                            print(f"[FLUX3] NEW URL (not in baseline): {full}", flush=True)
                            logger.info(f"_poll_flux3: new URL vs baseline: {full}")
                            return full
                    except json.JSONDecodeError as e:
                        print(f"[FLUX3] JSON parse error: {e}, raw[:200]={raw[:200]}", flush=True)
                        logger.warning(f"_poll_flux3: could not parse targeted response: {raw[:100]}")
                else:
                    # Baseline fallback mode
                    urls = json.loads(raw) if raw.startswith("[") else []
                    new = [u for u in urls if u not in seen]
                    print(f"[FLUX3] baseline attempt {attempt}: total={len(urls)}, new={len(new)}", flush=True)
                    logger.info(
                        f"_poll_flux3: attempt {attempt}/{FLUX_POLL_ATTEMPTS} "
                        f"found {len(urls)} cdn urls, {len(new)} new"
                    )
                    if new:
                        full = new[-1]
                        print(f"[FLUX3] BASELINE RESULT: {full}", flush=True)
                        logger.info(f"_poll_flux3: baseline result: {full}")
                        return full
            except Exception as exc:
                print(f"[FLUX3] attempt {attempt} error: {exc}", flush=True)
                logger.warning(f"_poll_flux3: attempt {attempt} error: {exc}")
            await asyncio.sleep(FLUX_POLL_INTERVAL)

    # If targeted mode failed, try one final baseline sweep
    if message_id:
        print("[FLUX3] targeted mode exhausted, trying final baseline sweep", flush=True)
        logger.warning("_poll_flux3: targeted mode found nothing, trying baseline fallback")
        try:
            baseline_seen = await _collect_cdn_urls()
        except Exception:
            baseline_seen = set()
        await asyncio.sleep(FLUX_POLL_INTERVAL)
        try:
            r = await client.post(f"{EGO_BRIDGE_URL}/v1/evaluate", json={"js": _cdn_js()})
            raw = (r.json().get("result") or "").strip()
            urls = json.loads(raw) if raw.startswith("[") else []
            new = [u for u in urls if u not in baseline_seen]
            if new:
                print(f"[FLUX3] FINAL BASELINE FALLBACK: {new[-1]}", flush=True)
                logger.info(f"_poll_flux3: baseline fallback found result: {new[-1]}")
                return new[-1]
        except Exception:
            pass

    print(f"[FLUX3] NO RESULT after {FLUX_POLL_ATTEMPTS} attempts ({mode} mode)", flush=True)
    logger.error(f"_poll_flux3: no result after {FLUX_POLL_ATTEMPTS} attempts ({mode} mode)")
    return None


def _download(url: str, dest: Path) -> str:
    """Download (signed CDN url) to dest using a browser UA; returns dest path.

    Downloads to a `.part` temp file first, then renames atomically, so a
    partial/failed download never leaves a corrupt final file.
    """
    import subprocess
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    ua = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36")
    r = subprocess.run(
        ["curl", "-sL", "-m", "120", "-A", ua, "-o", str(part), url],
        capture_output=True, text=True, timeout=130,
    )
    if r.returncode != 0 or not part.exists() or part.stat().st_size == 0:
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass
        raise RuntimeError(f"download failed for {url}: {r.stderr.strip()}")
    os.replace(part, dest)  # atomic rename
    return str(dest)


async def _finalize_media(dest: Path, meta: dict) -> Optional[str]:
    """Hash, sidecar, register a downloaded media file with the production store.

    Returns the final path string on success, or None on any failure (logged).
    `meta` must carry engine/kind and identity fields used for registration.
    Uses print() alongside logger for daemon-thread visibility.
    """
    try:
        file_size = dest.stat().st_size
        file_hash = await asyncio.to_thread(compute_file_hash, dest)
        reg_meta = dict(meta)
        reg_meta.update({
            "file_path": str(dest),
            "file_size": file_size,
            "file_hash": file_hash,
        })
        # Sidecar written before registration; register is the durable commit.
        await asyncio.to_thread(write_meta_sidecar, dest, reg_meta)
        print(f"[STORE] Registering production: engine={reg_meta.get('engine')}, hash={file_hash[:12]}, size={file_size}", flush=True)

        # Ensure productions table exists in current DB (may be first run in container)
        from database import get_connection
        conn = get_connection()
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='productions'"
        ).fetchall()
        if not tables:
            print("[STORE] productions table missing — running init_db()", flush=True)
            from database import init_db
            init_db()

        production_id = await asyncio.to_thread(register_production, reg_meta)
        print(f"[STORE] Registered production {production_id}", flush=True)
        logger.info(f"_finalize_media: registered {production_id} size={file_size} hash={file_hash[:12]}")

        # Run QC review (non-blocking for the pipeline; failures don't affect file delivery)
        try:
            vid_meta = _extract_video_meta(dest)
            qc_result = await _qc_review_video(dest, vid_meta, production_id, metadata=reg_meta)
            score = qc_result.get("score")
            print(f"[QC] Final score for {production_id}: {score}/10", flush=True)
            # Persist reroll feedback on the sidecar + row so run_production_cycle can act on it.
            reg_meta.setdefault("qc_score", score)
            reg_meta.setdefault("reroll_recommended", qc_result.get("reroll_recommended", False))
            reg_meta.setdefault("prompt_improvements", qc_result.get("prompt_improvements") or [])
            reg_meta.setdefault("reroll_count", reg_meta.get("reroll_count", 0))
            await asyncio.to_thread(write_meta_sidecar, dest, reg_meta)
        except Exception as qc_exc:
            print(f"[QC] QC review failed (non-fatal): {qc_exc}", flush=True)
            logger.warning(f"_finalize_media: QC review failed: {qc_exc}")

        return str(dest)
    except Exception as exc:
        print(f"[STORE] Registration FAILED: {exc}", flush=True)
        logger.error(f"_finalize_media: store write failed: {exc}")
        return None


def _read_reroll_feedback(dest: Path) -> dict:
    """Best-effort read of reroll feedback from a media file's .meta.json sidecar.

    Returns {reroll_recommended: bool, prompt_improvements: list}.
    """
    try:
        sidecar = dest.with_suffix(dest.suffix + ".meta.json")
        if not sidecar.exists():
            return {"reroll_recommended": False, "prompt_improvements": []}
        data = json.loads(sidecar.read_text())
        return {
            "reroll_recommended": bool(data.get("reroll_recommended", False)),
            "prompt_improvements": data.get("prompt_improvements") or [],
        }
    except Exception as exc:
        logger.warning(f"_read_reroll_feedback: {exc}")
        return {"reroll_recommended": False, "prompt_improvements": []}


def _build_improved_premise(original: str, improvements: list) -> str:
    """Append each prompt improvement as a sentence to the original premise."""
    parts = [original] if original else []
    for imp in improvements:
        text = str(imp).strip()
        if not text:
            continue
        if not text.endswith("."):
            text += "."
        parts.append(text[0].upper() + text[1:])
    return " ".join(parts)


async def _collect_reroll_improvements(video_paths: list) -> list:
    """Read each produced video's .meta.json sidecar for reroll feedback.

    Returns a de-duplicated list of prompt_improvements if ANY produced video
    has reroll_recommended=True and a non-empty improvements list; else [].
    """
    seen: set = set()
    combined: list = []
    for vp in video_paths:
        if not vp:
            continue
        try:
            feedback = await asyncio.to_thread(_read_reroll_feedback, Path(vp))
        except Exception as exc:
            logger.warning(f"_collect_reroll_improvements: read failed for {vp}: {exc}")
            continue
        # Only consider this video's feedback if QC recommended a reroll.
        if not feedback.get("reroll_recommended"):
            continue
        for imp in feedback.get("prompt_improvements") or []:
            text = str(imp).strip()
            key = text.lower()
            if text and key not in seen:
                seen.add(key)
                combined.append(text)
    return combined


def _validate_prompt_coherence(
    prompt: str,
    style_id: str,
    engine: str,
    reroll_count: int = 0,
) -> str:
    """Pre-generation coherence gate: check drawn-out dialogue matches the scene.

    Uses the style's `dialogue_guide` (tone, examples, banned_phrases) from
    visual_register.json. NEVER strips dialogue — only APPENDS a coherence hint
    when banned phrases are detected or tone overlap is low.

    Returns the (possibly hint-appended) prompt string. On reroll (reroll_count>0)
    we skip appending because the improved premise/improvements already steer the
    dialogue (avoids double-annotating).
    """
    guide = _get_style_guide(style_id)
    if not guide:
        return prompt

    banned = [str(b).lower().replace("\u2019", "'").replace("\u2018", "'") for b in (guide.get("banned_phrases") or [])]
    tone = (guide.get("tone") or "").lower()
    examples = guide.get("examples") or []

    # Extract quoted dialogue lines (double-quoted, and single-quoted short),
    # normalizing curly apostrophes so "Y'all" still matches the guide.
    quoted = re.findall(r'"([^"]{2,})"', prompt) + re.findall(r"'([^']{2,})'", prompt)
    lines = [q.replace("\u2019", "'").replace("\u2018", "'") for q in quoted if len(q.strip()) > 1]
    n_lines = len(lines)

    # 1) Banned phrase check.
    banned_found = 0
    for line in (l.lower() for l in lines):
        for b in banned:
            if b and b in line:
                banned_found += 1
                break

    # 2) Tone overlap: does any dialogue token overlap the tone description?
    if tone:
        tone_words = set(re.findall(r"[a-z]{3,}", tone))
        overlap = 0
        for line in (l.lower() for l in lines):
            if set(re.findall(r"[a-z]{3,}", line)) & tone_words:
                overlap += 1
        tone_overlap_ratio = overlap / n_lines if n_lines else 1.0
    else:
        tone_overlap_ratio = 1.0

    low_coherence = (banned_found > 0) or (n_lines > 0 and tone_overlap_ratio < 0.5)

    coherence_hint_appended = False
    if low_coherence and reroll_count == 0 and lines and tone:
        example = examples[0] if examples else ""
        hint = (
            f"Dialogue should match the {tone} tone of this {style_id} scene."
        )
        if banned:
            hint += f" Avoid: {', '.join(banned)}."
        if example:
            hint += f" Example appropriate dialogue: {example}"
        end = " duration:"
        if end in prompt:
            head, _, tail = prompt.partition(end)
            prompt = f"{head.rstrip('.')}. {hint}.{end}{tail}"
        else:
            prompt = f"{prompt.rstrip('.')}. {hint}."
        coherence_hint_appended = True

    print(
        f"[COHERENCE] Style={style_id}, engine={engine}, dialogue_lines={n_lines}, "
        f"banned_found={banned_found}, coherence_hint_appended={coherence_hint_appended}",
        flush=True,
    )
    return prompt


def _apply_reroll_to_flux3(brief: dict, improvements: list) -> dict:
    """Post-process the flux3 /t2v prompt for a reroll so it ACTUALLY changes.

    Flux 3 CAN and SHOULD do dialogue/lip-sync — so we NEVER strip existing
    dialogue. The real improvement comes from a more specific premise, which
    makes VBL generate matching dialogue naturally next time. We only touch the
    visual description parts:
      1. KEEP all existing dialogue as-is.
      2. Append the prompt_improvements as visual/context additions.
    Returns a NEW brief (does not mutate the caller's dict) whose flux3 prompt
    is guaranteed to differ from the original.
    """
    pp = brief.get("production_prompts", {})
    flux3 = pp.get("flux3", "")
    if not flux3 or not improvements:
        return brief
    orig = flux3

    flux3_out = flux3
    # Append improvements as visual/context addition (before the /t2v params).
    # Dialogue-related improvements are kept as context so VBL writes better
    # matching dialogue; visual improvements sharpen the scene.
    additions = ", ".join(i.rstrip(".") for i in improvements if str(i).strip())
    if additions:
        if " duration:" in flux3_out:
            head, _, tail = flux3_out.partition(" duration:")
            flux3_out = f"{head.rstrip(',')}, {additions}. duration:{tail}"
        else:
            flux3_out = f"{flux3_out.rstrip()}. {additions}."

    if flux3_out == orig:
        flux3_out = f"{flux3_out.rstrip()}. Enhanced reroll: {', '.join(improvements)}."

    print("[REROLL_FIX] Kept all dialogue; appended improvements as visual/context additions; flux3 now differs from original", flush=True)

    new_pp = dict(pp)
    new_pp["flux3"] = flux3_out
    new_brief = dict(brief)
    new_brief["production_prompts"] = new_pp
    return new_brief


def _apply_reroll_to_h3(brief: dict, improvements: list) -> dict:
    """Ensure the H3 prompts reflect the reroll improvements.

    Appends the improvements to each shot's prompt (h3_multishot_json.shots[]),
    and to the single-shot h3_job_json.prompt, so the H3 output also changes.
    """
    pp = dict(brief.get("production_prompts", {}))
    add = ", ".join(i.rstrip(".") for i in improvements if str(i).strip())
    if not add:
        return brief

    ms = pp.get("h3_multishot_json")
    if isinstance(ms, dict) and isinstance(ms.get("shots"), list):
        new_shots = []
        for shot in ms["shots"]:
            s = dict(shot)
            base = s.get("prompt", "")
            s["prompt"] = f"{base}, {add}" if base else add
            new_shots.append(s)
        ms2 = dict(ms)
        ms2["shots"] = new_shots
        pp["h3_multishot_json"] = ms2

    job = pp.get("h3_job_json")
    if isinstance(job, dict):
        base = job.get("prompt", "")
        job2 = dict(job)
        job2["prompt"] = f"{base}, {add}" if base else add
        pp["h3_job_json"] = job2

    new_brief = dict(brief)
    new_brief["production_prompts"] = pp
    return new_brief


def _extract_video_meta(path: Path) -> dict:
    """Best-effort ffprobe of a media file -> {resolution, duration_s}.

    Never fails the caller: returns empty dict if ffprobe is missing/errors.
    """
    import json as _json
    import subprocess as _sp
    try:
        r = _sp.run(
            ["ffprobe", "-v", "error",
             "-show_entries", "stream=width,height:format=duration",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            return {}
        data = _json.loads(r.stdout or "{}")
        streams = data.get("streams") or []
        duration_raw = data.get("format", {}).get("duration")
        resolution = None
        for s in streams:
            w, h = s.get("width"), s.get("height")
            if w and h:
                resolution = f"{w}x{h}"
                break
        duration = None
        if duration_raw:
            try:
                duration = round(float(duration_raw), 2)
            except (TypeError, ValueError):
                duration = None
        return {"resolution": resolution, "duration_s": duration}
    except Exception:
        return {}


def _extract_qc_frames(video_path: Path, frames_dir: Path) -> list[Path]:
    """Extract 3 frames at 25%, 50%, 75% of video duration using ffmpeg.

    Returns list of frame paths. Empty list on any failure.
    """
    import subprocess as _sp
    meta = _extract_video_meta(video_path)
    duration = meta.get("duration_s")
    if not duration or duration <= 0:
        print(f"[QC] Cannot extract frames: duration={duration}", flush=True)
        return []

    frames_dir.mkdir(parents=True, exist_ok=True)
    timestamps = [duration * 0.25, duration * 0.50, duration * 0.75]
    frame_paths = []

    for i, ts in enumerate(timestamps):
        out = frames_dir / f"frame_{i}_{ts:.1f}s.jpg"
        try:
            r = _sp.run(
                ["ffmpeg", "-y", "-ss", f"{ts:.2f}", "-i", str(video_path),
                 "-frames:v", "1", "-q:v", "2", str(out)],
                capture_output=True, text=True, timeout=15,
            )
            if r.returncode == 0 and out.exists() and out.stat().st_size > 0:
                frame_paths.append(out)
            else:
                print(f"[QC] Frame extraction failed at {ts:.1f}s: {r.stderr[:100]}", flush=True)
        except Exception as e:
            print(f"[QC] Frame extraction error at {ts:.1f}s: {e}", flush=True)

    print(f"[QC] Extracted {len(frame_paths)}/3 frames from {video_path.name}", flush=True)
    return frame_paths


def _qc_local_check(video_path: Path, vid_meta: dict) -> dict:
    """Local QC checks that don't need an external API.

    Checks: file size > 1MB, duration > 3s, valid resolution.
    Returns {score: float, notes: str}.
    """
    checks = []
    score = 10.0

    # File size check
    try:
        size = video_path.stat().st_size
        if size < 1_000_000:
            checks.append(f"FAIL: file size {size} bytes < 1MB (possibly corrupt)")
            score -= 5.0
        else:
            checks.append(f"OK: file size {size:,} bytes")
    except OSError as e:
        checks.append(f"FAIL: cannot stat file: {e}")
        score -= 5.0

    # Duration check
    duration = vid_meta.get("duration_s")
    if duration is None or duration <= 3.0:
        checks.append(f"FAIL: duration {duration}s <= 3s (possibly empty/truncated)")
        score -= 3.0
    else:
        checks.append(f"OK: duration {duration}s")

    # Resolution check
    resolution = vid_meta.get("resolution")
    if not resolution:
        checks.append("FAIL: no resolution detected")
        score -= 2.0
    else:
        try:
            w, h = resolution.split("x")
            wi, hi = int(w), int(h)
            if wi < 100 or hi < 100:
                checks.append(f"FAIL: resolution {resolution} too small")
                score -= 2.0
            else:
                checks.append(f"OK: resolution {resolution}")
        except (ValueError, AttributeError):
            checks.append(f"WARN: unparseable resolution '{resolution}'")
            score -= 1.0

    score = max(0.0, score)
    notes = "; ".join(checks)
    return {"score": score, "notes": notes}


async def _qc_video_modelscope(video_path: Path, metadata: dict) -> Optional[dict]:
    """QC a video via ModelScope Qwen3.8-Max vision API.

    Re-encodes to ≤8MB at 320p, base64 encodes, sends structured QC prompt.
    Returns parsed {motion_quality, temporal_coherence, style_adherence,
    artifacts, overall_watchability, summary} or None on any failure.
    Never raises — all errors are logged and return None.
    """
    import base64
    import subprocess as _sp

    api_key = os.environ.get("MODELSCOPE_API_KEY", "")
    if not api_key:
        print("[QC-MS] MODELSCOPE_API_KEY not set — skipping ModelScope QC", flush=True)
        return None

    # Step 1: Re-encode to ≤8MB at 320p
    small_path = video_path.parent / f".qc_{video_path.stem}_small.mp4"
    print(f"[QC-MS] Re-encoding {video_path.name} to 320p for ModelScope...", flush=True)
    try:
        r = _sp.run(
            ["ffmpeg", "-y", "-i", str(video_path),
             "-vf", "scale=320:-2", "-c:v", "libx264", "-preset", "fast",
             "-crf", "30", "-c:a", "aac", "-b:a", "96k", str(small_path)],
            capture_output=True, text=True, timeout=60,
        )
        if r.returncode != 0 or not small_path.exists():
            print(f"[QC-MS] Re-encode failed: {r.stderr[:200]}", flush=True)
            return None
        small_size = small_path.stat().st_size
        print(f"[QC-MS] Re-encoded: {small_size:,} bytes ({small_size/1024/1024:.1f}MB)", flush=True)
        if small_size > 8_000_000:
            print(f"[QC-MS] Still too large ({small_size:,}b), trying CRF 35...", flush=True)
            r = _sp.run(
                ["ffmpeg", "-y", "-i", str(video_path),
                 "-vf", "scale=240:-2", "-c:v", "libx264", "-preset", "fast",
                 "-crf", "35", "-c:a", "aac", "-b:a", "64k", str(small_path)],
                capture_output=True, text=True, timeout=60,
            )
            if r.returncode != 0 or not small_path.exists() or small_path.stat().st_size > 8_000_000:
                print(f"[QC-MS] Could not reduce to ≤8MB — skipping ModelScope QC", flush=True)
                return None
            small_size = small_path.stat().st_size
            print(f"[QC-MS] Second pass: {small_size:,} bytes", flush=True)
    except Exception as e:
        print(f"[QC-MS] Re-encode error: {e}", flush=True)
        return None

    # Step 2: Base64 encode
    try:
        with open(small_path, "rb") as f:
            b64_video = base64.b64encode(f.read()).decode("ascii")
        print(f"[QC-MS] Base64 encoded: {len(b64_video):,} chars", flush=True)
    except Exception as e:
        print(f"[QC-MS] Base64 encode error: {e}", flush=True)
        return None
    finally:
        # Clean up temp file
        try:
            small_path.unlink(missing_ok=True)
        except OSError:
            pass

    # Step 3: Build QC prompt with context
    premise = metadata.get("premise", "unknown")
    style = metadata.get("style_id", "unknown")
    engine = metadata.get("engine", "unknown")
    qc_prompt = (
        f"You are a professional video quality reviewer. Analyze this AI-generated video.\n\n"
        f"INTENDED CONTENT:\n"
        f"- Engine: {engine}\n"
        f"- Style: {style}\n"
        f"- Premise: {premise}\n\n"
        f"Respond with ONLY valid JSON (no markdown fences):\n"
        f'{{\n'
        f'  "motion_quality": {{"score": 0-10, "notes": "smoothness, jitter, motion artifacts"}},\n'
        f'  "temporal_coherence": {{"score": 0-10, "notes": "scene consistency, morphing, teleportation"}},\n'
        f'  "style_adherence": {{"score": 0-10, "notes": "matches intended aesthetic/style"}},\n'
        f'  "artifacts": ["list of specific visual defects observed"],\n'
        f'  "overall_watchability": 0-10,\n'
        f'  "reroll_recommended": true/false,  // true only if a reroll is likely to materially improve quality\n'
        f'  "prompt_improvements": ["specific, actionable prompt changes to improve this video, one per element"],\n'
        f'  "summary": "one paragraph overall assessment"\n'
        f'}}'
    )

    # Step 4: Call ModelScope API
    payload = {
        "model": "Qwen-Ambassador/Qwen3.8-Max",
        "messages": [{
            "role": "user",
            "content": [
                {"type": "video_url", "video_url": {"url": f"data:video/mp4;base64,{b64_video}"}},
                {"type": "text", "text": qc_prompt},
            ],
        }],
        "max_tokens": 2048,
        "temperature": 0.3,
    }

    print(f"[QC-MS] Sending to ModelScope Qwen3.8-Max...", flush=True)
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(
                "https://api-inference.modelscope.ai/v1/chat/completions",
                json=payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )
            if resp.status_code != 200:
                print(f"[QC-MS] API returned {resp.status_code}: {resp.text[:200]}", flush=True)
                return None
            data = resp.json()
    except httpx.TimeoutException:
        print("[QC-MS] API timed out (300s)", flush=True)
        return None
    except Exception as e:
        print(f"[QC-MS] API request error: {e}", flush=True)
        return None

    # Step 5: Parse response
    content = ""
    try:
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        # Strip markdown fences if present
        import re
        content = re.sub(r'^```(?:json)?\s*', '', content.strip())
        content = re.sub(r'\s*```$', '', content.strip())
        result = json.loads(content)
        print(f"[QC-MS] Parsed QC result: watchability={result.get('overall_watchability')}", flush=True)
        return result
    except (json.JSONDecodeError, IndexError, KeyError) as e:
        print(f"[QC-MS] Failed to parse response: {e}", flush=True)
        print(f"[QC-MS] Raw content[:300]: {content[:300]}", flush=True)
        return None


async def _qc_review_video(
    video_path: Path,
    vid_meta: dict,
    production_id: str,
    metadata: Optional[dict] = None,
) -> dict:
    """Run QC review on a production video.

    Two-tier: local sanity checks first, then ModelScope vision QC if available.
    Updates the production record with qc_score and qc_notes.
    Returns {score, notes}.
    """
    metadata = metadata or {}
    print(f"[QC] Starting QC review for {production_id}: {video_path.name}", flush=True)

    # Tier 1: Local checks (always run)
    result = _qc_local_check(video_path, vid_meta)
    score = result["score"]
    notes = result["notes"]

    # Extract frames (validates video is readable + future manual review)
    frames_dir = video_path.parent / f".qc_{video_path.stem}"
    frames = await asyncio.to_thread(_extract_qc_frames, video_path, frames_dir)
    if frames:
        notes += f"; {len(frames)} QC frames extracted"

    # Tier 2: ModelScope vision QC (if API key available and local checks passed)
    ms_result = None
    if score >= 5.0:  # Only send to ModelScope if not obviously broken
        try:
            ms_result = await _qc_video_modelscope(video_path, metadata)
            if ms_result:
                ms_score = ms_result.get("overall_watchability", score)
                ms_summary = ms_result.get("summary", "")
                ms_artifacts = ms_result.get("artifacts", [])
                # Blend: use ModelScope score as primary, local as sanity floor
                score = max(ms_score, min(score, 5.0))  # MS score, but local failures cap at 5
                artifact_str = "; ".join(ms_artifacts[:5]) if ms_artifacts else "none noted"
                notes = (
                    f"ModelScope QC: {ms_score}/10 — {ms_summary}; "
                    f"Artifacts: {artifact_str}; "
                    f"Local: {result['notes']}"
                )
                print(f"[QC] ModelScope score: {ms_score}/10, blended: {score}/10", flush=True)
            else:
                print("[QC] ModelScope unavailable — using local QC only", flush=True)
        except Exception as ms_exc:
            print(f"[QC] ModelScope QC failed (non-fatal): {ms_exc}", flush=True)

    # Update DB with QC results
    try:
        from database import get_connection
        conn = get_connection()
        conn.execute(
            "UPDATE productions SET qc_score = ?, qc_notes = ? WHERE id = ?",
            (score, notes, production_id),
        )
        conn.commit()
        print(f"[QC] Updated {production_id}: score={score}", flush=True)
    except Exception as e:
        print(f"[QC] Failed to update DB for {production_id}: {e}", flush=True)
        logger.error(f"_qc_review_video: DB update failed: {e}")

    logger.info(f"_qc_review_video: {production_id} score={score}")
    return {
        "score": score,
        "notes": notes,
        "reroll_recommended": bool((ms_result or {}).get("reroll_recommended", False)),
        "prompt_improvements": (ms_result or {}).get("prompt_improvements") or [],
    }


def _validate_prompt_context(prompt: str, engine: str) -> list[str]:
    """Check if dialogue in a prompt is contextually appropriate for the visual scene.

    Returns a list of warning strings. Never modifies the prompt — the QC→reroll
    loop handles improvements via ModelScope feedback.
    """
    import re
    warnings: list[str] = []

    # Extract quoted dialogue (both single and double quotes)
    dialogue_matches = re.findall(r'"([^"]{3,})"|\'([^\']{3,})\'', prompt)
    dialogues = [m[0] or m[1] for m in dialogue_matches]

    # Extract <d> tag content for H3
    d_tag_matches = re.findall(r'<d>\[[^\]]*\]([^<]+)</d>', prompt)
    dialogues.extend(d_tag_matches)

    if not dialogues:
        return warnings  # No dialogue to check

    # Extract visual keywords from the non-dialogue parts of the prompt
    # Remove dialogue text and <d> tags to get the visual description
    visual_text = re.sub(r'<d>[^<]*</d>', '', prompt)
    for d in dialogues:
        visual_text = visual_text.replace(f'"{d}"', '').replace(f"'{d}'", "")
    visual_text = visual_text.lower()

    # Simple heuristic: extract nouns/keywords from visual description
    # and check if any dialogue word overlaps with the visual context
    visual_words = set(re.findall(r'[a-z]{3,}', visual_text))
    # Common visual categories that dialogue should relate to
    scene_indicators = {
        "robot", "mech", "kaiju", "godzilla", "monster", "dragon", "alien",
        "city", "tokyo", "street", "building", "tower", "bridge", "harbor",
        "car", "truck", "vehicle", "drive", "chase", "highway", "road",
        "school", "classroom", "graduation", "campus", "student",
        "kitchen", "cook", "food", "restaurant", "chef", "recipe",
        "fight", "battle", "punch", "kick", "sword", "weapon", "attack",
        "sunset", "sunrise", "night", "rain", "snow", "storm", "fire",
        "dance", "music", "sing", "concert", "stage", "perform",
        "hangar", "launch", "fly", "space", "ship", "rocket", "pilot",
        "beach", "ocean", "wave", "surf", "boat", "island",
        "forest", "tree", "mountain", "river", "field", "garden",
        "party", "celebration", "birthday", "wedding", "funeral",
        "hospital", "doctor", "nurse", "patient", "clinic",
        "barbershop", "salon", "haircut", "mirror",
    }
    # Find which scene indicators appear in the visual description
    # Use stem matching to handle plurals (streets→street, cars→car)
    active_scene = set()
    for word in visual_words:
        stem = word.rstrip("s") if word.endswith("s") and len(word) > 3 else word
        if stem in scene_indicators or word in scene_indicators:
            active_scene.add(stem if stem in scene_indicators else word)

    if not active_scene:
        # Can't determine scene context — skip contextual check
        return warnings

    # Check each dialogue line for relevance to the visual scene
    for dialogue in dialogues:
        dialogue_words = set(re.findall(r'[a-z]{3,}', dialogue.lower()))
        # Stem dialogue words too for matching
        dialogue_stems = set()
        for w in dialogue_words:
            stem = w.rstrip("s") if w.endswith("s") and len(w) > 3 else w
            dialogue_stems.add(stem)
        # Check overlap with visual words OR scene indicators
        overlap = dialogue_stems & (visual_words | active_scene | scene_indicators)
        has_relation = len(overlap) >= 1

        if not has_relation:
            visual_summary = ", ".join(sorted(active_scene)[:5])
            warnings.append(
                f'Dialogue may not match visual context: "{dialogue[:60]}" '
                f'in scene about {visual_summary}'
            )

    # For H3: also check <d> tag formatting
    if engine == "h3":
        bare_quotes = re.findall(r'"[^"]{5,}"', prompt)
        if bare_quotes and not d_tag_matches:
            warnings.append(
                f"H3 prompt has {len(bare_quotes)} quoted strings without <d> tags — "
                f"dialogue may not render as speech"
            )
        bad_d_tags = re.findall(r'<d>(?!\[)([^<]+)</d>', prompt)
        if bad_d_tags:
            warnings.append(
                f"H3 prompt has {len(bad_d_tags)} <d> tags missing [Language] prefix"
            )

    return warnings


def _validate_prompts(brief: dict) -> dict:
    """Validate all prompts in a brief before generation. WARN only, never modify.

    Logs warnings for contextual mismatches and formatting issues.
    The QC→reroll loop handles actual prompt improvements via ModelScope.
    Returns the brief unchanged.
    """
    pp = brief.get("production_prompts", {})
    if not pp:
        return brief

    # Validate Flux3 prompt
    flux3 = pp.get("flux3", "")
    if flux3:
        raw = flux3
        if flux3.startswith("/t2v"):
            import re
            m = re.match(r'^/t2v\s+(?:prompt:)?(.*)', flux3, re.DOTALL)
            if m:
                raw = m.group(1)
        warnings = _validate_prompt_context(raw, "flux3")
        for w in warnings:
            print(f"[PROMPT_WARN] Flux3: {w}", flush=True)
            logger.warning(f"_validate_prompts: Flux3: {w}")

    # Validate H3 prompts
    for key in ("h3_job_json", "h3_multishot_json"):
        h3_cfg = pp.get(key)
        if not h3_cfg or not isinstance(h3_cfg, dict):
            continue
        prompt = h3_cfg.get("prompt", "")
        if prompt:
            warnings = _validate_prompt_context(prompt, "h3")
            for w in warnings:
                print(f"[PROMPT_WARN] H3 {key}: {w}", flush=True)
                logger.warning(f"_validate_prompts: H3 {key}: {w}")
        # Check multishot shots
        for shot in h3_cfg.get("shots", []):
            shot_prompt = shot.get("prompt", "")
            if shot_prompt:
                warnings = _validate_prompt_context(shot_prompt, "h3")
                for w in warnings:
                    print(f"[PROMPT_WARN] H3 shot {shot.get('shot_index','?')}: {w}", flush=True)

    return brief


# ─── Factory Job Tracking ─────────────────────────────────────────────────────

def _log_job(
    engine: str, stage: str, status: str,
    session_id: str = "", production_id: str = "",
    outcome: str = "", parent_job_id: str = "",
    reroll_count: int = 0, started_at: str = "",
) -> str:
    """Insert or update a factory_jobs row. Returns the job id."""
    import uuid as _uuid
    from database import get_connection
    job_id = _uuid.uuid4().hex[:16]
    now = datetime.now(timezone.utc).isoformat()
    try:
        conn = get_connection()
        conn.execute(
            """INSERT INTO factory_jobs
               (id, production_id, session_id, engine, stage, status,
                started_at, created_at, finished_at, duration_s, outcome, reroll_count, parent_job_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (job_id, production_id, session_id, engine, stage, status,
             started_at or now, started_at or now,
             now if status in ("complete", "failed", "skipped") else None,
             None, outcome, reroll_count, parent_job_id),
        )
        conn.commit()
    except Exception as e:
        print(f"[JOB] Failed to log {engine}/{stage}: {e}", flush=True)
    return job_id


def _finish_job(job_id: str, status: str, outcome: str = "") -> None:
    """Mark a factory_jobs row as complete/failed with duration."""
    from database import get_connection
    now = datetime.now(timezone.utc).isoformat()
    try:
        conn = get_connection()
        # Calculate duration from started_at
        row = conn.execute("SELECT started_at FROM factory_jobs WHERE id = ?", (job_id,)).fetchone()
        duration = None
        if row and row["started_at"]:
            try:
                from datetime import datetime as _dt
                sa = _dt.fromisoformat(row["started_at"])
                fa = _dt.fromisoformat(now)
                duration = round((fa - sa).total_seconds(), 1)
            except Exception:
                pass
        conn.execute(
            "UPDATE factory_jobs SET status = ?, finished_at = ?, duration_s = ?, outcome = ? WHERE id = ?",
            (status, now, duration, outcome, job_id),
        )
        conn.commit()
    except Exception as e:
        print(f"[JOB] Failed to finish {job_id}: {e}", flush=True)


async def run_production_cycle(config: dict) -> dict:
    """Run one full production: pick -> brief -> H3 + Flux3 -> poll -> download -> log.

    config keys: style_id/franchise/premise/niche overrides; output_dir; reroll_count.

    Auto-reroll: if QC on the produced videos returns reroll_recommended=True with
    a non-empty prompt_improvements list AND this isn't already a reroll, the cycle
    re-runs once with an improved premise (capped at 1 reroll per production).
    """
    reroll_count = int(config.get("reroll_count", 0) or 0)
    session_id = config.get("session_id", "")
    production_id = config.get("production_id") or uuid.uuid4().hex[:16]
    parent_job_id = config.get("parent_job_id", "")
    print(f"[FACTORY] run_production_cycle starting (prod={production_id}, reroll={reroll_count})", flush=True)

    # Stage: brief
    brief_job = _log_job("factory", "brief", "running", session_id, production_id, reroll_count=reroll_count, parent_job_id=parent_job_id)
    pick = (
        await asyncio.to_thread(pick_next_production) if not config.get("style_id")
        else dict(config)
    )
    print(f"[FACTORY] pick complete: style={pick.get('style_id')}, franchise={pick.get('franchise')}", flush=True)

    brief_resp = await _get_brief(pick)
    brief = brief_resp.get("brief") or {}
    out_dir = Path(config.get("output_dir", str(OUTPUT_DIR)))
    _finish_job(brief_job, "complete", json.dumps({"style": pick.get("style_id"), "franchise": pick.get("franchise")}))

    # Pre-generation prompt validation (warn only, never modify)
    _validate_prompts(brief)

    # On a reroll, VBL may return the SAME flux3 prompt (stale dialogue) despite
    # the improved premise. Post-process the brief so the generated prompt
    # actually changes from the original (see option-2 root-cause fix).
    if reroll_count > 0:
        improvements = config.get("prompt_improvements") or []
        if improvements:
            brief = _apply_reroll_to_flux3(brief, improvements)
            brief = _apply_reroll_to_h3(brief, improvements)
            brief_resp = dict(brief_resp)
            brief_resp["brief"] = brief

    # Pre-generation dialogue-scene coherence gate (append-only, never strips).
    try:
        style_id = pick.get("style_id", "")
        pp = brief.get("production_prompts", {})
        new_pp = dict(pp)
        flux3 = pp.get("flux3", "")
        if flux3:
            flux3 = _validate_prompt_coherence(flux3, style_id, "flux3", reroll_count)
            new_pp["flux3"] = flux3
        for key in ("h3_multishot_json", "h3_job_json"):
            cfg = pp.get(key)
            if isinstance(cfg, dict):
                cfg2 = dict(cfg)
                if isinstance(cfg2.get("shots"), list):
                    cfg2["shots"] = [
                        {**shot, "prompt": _validate_prompt_coherence(
                            str(shot.get("prompt", "")), style_id, "h3", reroll_count)}
                        for shot in cfg2["shots"]
                    ]
                if "prompt" in cfg2 and isinstance(cfg2.get("prompt"), str):
                    cfg2["prompt"] = _validate_prompt_coherence(
                        cfg2["prompt"], style_id, "h3", reroll_count)
                new_pp[key] = cfg2
        brief = dict(brief)
        brief["production_prompts"] = new_pp
        brief_resp = dict(brief_resp)
        brief_resp["brief"] = brief
    except Exception as exc:
        print(f"[COHERENCE] gate failed (non-fatal): {exc}", flush=True)
        logger.warning(f"run_production_cycle: coherence gate failed: {exc}")

    # Dedup guard: warn (but don't block) if this exact production happened recently.
    try:
        dup = await asyncio.to_thread(
            _recent_production_exists, pick.get("style_id", ""),
            pick.get("franchise", ""), pick.get("premise", ""), 24,
        )
        if dup:
            logger.warning(
                f"run_production_cycle: duplicate of production "
                f"{dup.get('id', dup)} within 24h ({pick.get('premise')}); continuing anyway"
            )
    except Exception as exc:
        logger.warning(f"run_production_cycle: dedup check failed: {exc}")

    # Kick off H3 + Flux3 concurrently (return_exceptions so one failure
    # doesn't cancel the other). Pass the pick metadata for registration.
    provenance = {k: pick.get(k, "") for k in ("style_id", "franchise", "premise", "niche")}
    provenance["reroll_count"] = reroll_count
    provenance["production_id"] = production_id
    provenance["session_id"] = session_id

    h3_job = _log_job("h3", "submit", "running", session_id, production_id, reroll_count=reroll_count, parent_job_id=parent_job_id)
    flux_job = _log_job("flux3", "submit", "running", session_id, production_id, reroll_count=reroll_count, parent_job_id=parent_job_id)

    h3_task = asyncio.create_task(_submit_and_poll_h3(brief, out_dir, provenance))
    flux_task = asyncio.create_task(_send_and_poll_flux(brief, out_dir, provenance))

    results = await asyncio.gather(h3_task, flux_task, return_exceptions=True)
    h3_video = results[0] if not isinstance(results[0], Exception) else None
    flux3_video = results[1] if not isinstance(results[1], Exception) else None
    failure_reason = None
    if isinstance(results[0], Exception):
        failure_reason = str(results[0])
        _finish_job(h3_job, "failed", str(results[0])[:500])
        logger.error(f"run_production_cycle: H3 task raised: {results[0]}")
    else:
        _finish_job(h3_job, "complete" if h3_video else "skipped", h3_video or "no output")
    if isinstance(results[1], Exception):
        failure_reason = str(results[1]) or failure_reason
        _finish_job(flux_job, "failed", str(results[1])[:500])
        logger.error(f"run_production_cycle: Flux3 task raised: {results[1]}")
    else:
        _finish_job(flux_job, "complete" if flux3_video else "skipped", flux3_video or "no output")

    qc_status = "ok" if (h3_video or flux3_video) else "failed"
    entry = {
        "ts": time.time(),
        "iso": datetime.now(timezone.utc).isoformat(),
        "style_id": pick.get("style_id", ""),
        "franchise": pick.get("franchise", ""),
        "premise": pick.get("premise", ""),
        "niche": pick.get("niche", ""),
        "h3_video": h3_video,
        "flux3_video": flux3_video,
        "qc_status": qc_status,
        "failure_reason": failure_reason,
        "reroll_count": reroll_count,
    }
    # Primary path: production store. Fallback to the JSON log if it fails.
    try:
        register_production(entry)
    except Exception as exc:
        logger.error(f"run_production_cycle: store register failed, falling back to log: {exc}")
        _append_log(entry)

    result = {"h3_video": h3_video, "flux3_video": flux3_video,
              "brief": brief_resp, "qc_status": qc_status, "reroll_count": reroll_count}

    # ── Auto-reroll: use QC feedback (max 1 reroll per production) ─────────────
    if reroll_count >= 1:
        return result

    improvements = await _collect_reroll_improvements([h3_video, flux3_video])
    if improvements:
        improved = _build_improved_premise(pick.get("premise", ""), improvements)
        print(f"[REROLL] Attempting reroll with improved prompt: {improvements}", flush=True)
        logger.info(f"[REROLL] Attempting reroll with improved prompt: {improvements}")
        _log_job("factory", "reroll", "running", session_id, production_id, outcome=json.dumps(improvements)[:500], reroll_count=reroll_count, parent_job_id=parent_job_id)
        reroll_config = dict(config)
        reroll_config.update({
            "premise": improved,
            "reroll_count": 1,
            "prompt_improvements": improvements,
            "production_id": production_id,
            "session_id": session_id,
            "parent_job_id": brief_job,
        })
        reroll_result = await run_production_cycle(reroll_config)
        reroll_result["rerolled_from"] = pick.get("premise", "")
        reroll_result["reroll_improvements"] = improvements
        return reroll_result

    return result


# H3 status polling cadence over the bridge (submit -> status every 15s).
H3_POLL_INTERVAL = int(os.environ.get("H3_POLL_INTERVAL", "15"))


async def _submit_and_poll_h3(brief: dict, out_dir: Path, provenance: Optional[dict] = None) -> Optional[str]:
    """Submit an H3 job via the host-side h3-bridge and poll/download over HTTP.

    Uses H3_BRIDGE_URL (http://host.docker.internal:8041) instead of direct SSH
    — containers can't reach LAN IPs, so all H3 ops go through the bridge:
      POST /v1/h3/submit   {job_json}                 -> {session_name}
      GET  /v1/h3/status/{session}                     -> {status,progress,output_file,error}
      POST /v1/h3/retrieve {remote_filename,local_path}-> {local_path,size}

    On success the result is hashed, given a .meta.json sidecar, and registered
    with the production store (engine='h3') using `provenance` metadata.
    """
    provenance = provenance or {}
    print(f"[H3] _submit_and_poll_h3: starting H3 pipeline", flush=True)
    logger.info("_submit_and_poll_h3: starting H3 pipeline")
    inner = brief.get("brief", brief)
    pp = inner.get("production_prompts", {})
    raw_job = pp.get("h3_multishot_json") or pp.get("h3_job_json")
    if not raw_job:
        logger.error("_submit_and_poll_h3: no h3_multishot_json or h3_job_json in brief")
        return None
    logger.info(f"_submit_and_poll_h3: got job config with keys: {list(raw_job.keys()) if isinstance(raw_job, dict) else type(raw_job)}")

    # Build the complete WGP config (Ref2VA fallback, motion injection, etc.).
    try:
        wgp_job = _convert_to_wgp_format(
            build_h3_config_with_fallback(raw_job, keeper_metadata=None)
        )
    except Exception as exc:
        logger.error(f"_submit_and_poll_h3: config build failed: {exc}")
        return None

    api_headers = {}
    api_key = os.environ.get("SGOS_API_KEY", "")
    if api_key:
        api_headers["Authorization"] = f"Bearer {api_key}"

    # 1) Submit via the bridge.
    try:
        async with httpx.AsyncClient(timeout=120, headers=api_headers) as client:
            r = await client.post(
                f"{H3_BRIDGE_URL}/v1/h3/submit", json={"job_json": wgp_job}
            )
            r.raise_for_status()
            session_name = (r.json() or {}).get("session_name", "")
        logger.info(f"_submit_and_poll_h3: bridge submit -> {session_name}")
    except Exception as exc:
        logger.error(f"_submit_and_poll_h3: bridge submit failed: {exc}")
        return None
    if not session_name:
        logger.error("_submit_and_poll_h3: bridge submit returned empty session name")
        return None

    # 2) Poll status every H3_POLL_INTERVAL until complete/failed/timeout.
    deadline = time.time() + H3_POLL_SECONDS
    output_file: Optional[str] = None
    try:
        async with httpx.AsyncClient(timeout=30, headers=api_headers) as client:
            while time.time() < deadline:
                try:
                    rr = await client.get(f"{H3_BRIDGE_URL}/v1/h3/status/{session_name}")
                    rr.raise_for_status()
                    data = rr.json() or {}
                except Exception as exc:
                    logger.warning(f"_submit_and_poll_h3: status poll error for {session_name}: {exc}")
                    await asyncio.sleep(H3_POLL_INTERVAL)
                    continue
                status = data.get("status")
                progress = data.get("progress", 0)
                logger.info(
                    f"_submit_and_poll_h3: {session_name} status={status} progress={progress}"
                )
                if status == "complete":
                    output_file = data.get("output_file")
                    logger.info(f"_submit_and_poll_h3: {session_name} complete, output={output_file}")
                    break
                if status == "failed":
                    logger.error(
                        f"_submit_and_poll_h3: {session_name} failed: {data.get('error')}"
                    )
                    return None
                await asyncio.sleep(H3_POLL_INTERVAL)
    except Exception as exc:
        logger.error(f"_submit_and_poll_h3: status polling broke: {exc}")
        return None

    if not output_file:
        # The tmux session may have ended just after our last poll — the bridge
        # then reports `complete` with the latest output file. Do one final
        # status query before giving up so a just-finished job isn't lost.
        try:
            async with httpx.AsyncClient(timeout=30, headers=api_headers) as client:
                rr = await client.get(f"{H3_BRIDGE_URL}/v1/h3/status/{session_name}")
                final = (rr.json() or {}) if rr.status_code == 200 else {}
            if final.get("status") == "complete" and final.get("output_file"):
                output_file = final.get("output_file")
                logger.info(f"_submit_and_poll_h3: recovered output_file on final status: {output_file}")
        except Exception as exc:
            logger.warning(f"_submit_and_poll_h3: final status probe failed: {exc}")

    if not output_file:
        logger.error(f"_submit_and_poll_h3: no output_file after timeout for {session_name}")
        return None

    # 3) Retrieve via the bridge (HTTP download to local_path), atomically.
    render_started = time.time()
    # Quota guard (Qwen's store exposes min_free_gb; require ≥1GB free).
    if not await asyncio.to_thread(check_disk_quota, 1.0):
        logger.error("_submit_and_poll_h3: disk quota exceeded — skipping retrieve")
        return None

    # Standardized path from the production store; download to .part first.
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    seed = abs(hash(provenance.get("premise", ""))) % (2 ** 31)
    final_dest = get_production_path(
        date_str, provenance.get("franchise", ""), "h3", seed
    )
    final_dest.parent.mkdir(parents=True, exist_ok=True)
    part_dest = final_dest.with_suffix(final_dest.suffix + ".part")
    try:
        async with httpx.AsyncClient(timeout=300, headers=api_headers) as client:
            r = await client.post(
                f"{H3_BRIDGE_URL}/v1/h3/retrieve",
                json={"remote_filename": output_file, "local_path": to_host_path(part_dest)},
            )
            r.raise_for_status()
            data = r.json() or {}
        if not part_dest.exists() or part_dest.stat().st_size == 0:
            logger.error("_submit_and_poll_h3: retrieve produced empty/absent file")
            try:
                part_dest.unlink(missing_ok=True)
            except OSError:
                pass
            return None
        os.replace(part_dest, final_dest)  # atomic rename to final name
        render_duration_s = round(time.time() - render_started, 1)
        logger.info(
            f"_submit_and_poll_h3: bridge retrieve -> {final_dest} "
            f"(render {render_duration_s}s, {data.get('size', final_dest.stat().st_size)} bytes)"
        )
    except Exception as exc:
        logger.error(f"_submit_and_poll_h3: bridge retrieve failed: {exc}")
        try:
            part_dest.unlink(missing_ok=True)
        except OSError:
            pass
        return None

    # 4) Hash, sidecar, register.
    vid_meta = _extract_video_meta(final_dest)
    reg_meta = {
        "engine": "h3",
        "style_id": provenance.get("style_id", ""),
        "franchise": provenance.get("franchise", ""),
        "premise": provenance.get("premise", ""),
        "niche": provenance.get("niche", ""),
        "qc_status": "ok",
        "prompt": (raw_job or {}).get("prompt", ""),
        "render_duration_s": render_duration_s,
        "resolution": vid_meta.get("resolution"),
        "reroll_count": provenance.get("reroll_count", 0),
        "duration_s": vid_meta.get("duration_s"),
    }
    finalized = await _finalize_media(final_dest, reg_meta)
    if not finalized:
        logger.error("_submit_and_poll_h3: media finalize (hash/sidecar/register) failed")
        return str(final_dest)  # keep the file even if registration failed
    return finalized


async def _send_and_poll_flux(brief: dict, out_dir: Path, provenance: Optional[dict] = None) -> Optional[str]:
    """Send the Flux3 command, then poll for + download our result.

    If the send raises or returns False, log exactly why and return None
    immediately (don't waste a 5-minute poll on a command that never sent).
    On success the file is atomically placed, hashed, sidecar'd and registered
    with the production store (engine='flux3') using `provenance` metadata.
    """
    provenance = provenance or {}
    try:
        send_result = await _send_flux3(brief)
    except Exception as exc:
        logger.error(f"_send_and_poll_flux: _send_flux3 raised: {exc}")
        return None
    if not send_result.get("sent"):
        logger.error("_send_and_poll_flux: send returned False (see _send_flux3 log)")
        return None

    message_id = send_result.get("message_id")
    if message_id:
        logger.info(f"_send_and_poll_flux: using targeted tracking with message_id={message_id}")
    else:
        logger.info("_send_and_poll_flux: message_id unavailable, falling back to baseline mode")

    # Baseline: URLs already on the channel right after our send. Used as
    # fallback if message_id tracking fails.
    try:
        seen = await _collect_cdn_urls()
    except Exception as exc:
        logger.warning(f"_send_and_poll_flux: baseline collection failed: {exc}")
        seen = set()

    url = await _poll_flux3(seen, message_id=message_id)
    if not url:
        logger.error("_send_and_poll_flux: poll returned no NEW URL")
        return None

    # Quota guard BEFORE downloading (skip if disk is too full).
    if not await asyncio.to_thread(check_disk_quota, 1.0):
        logger.error("_send_and_poll_flux: disk quota exceeded — skipping download")
        return None

    # Standardized path; download to .part first, then atomic rename.
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    seed = abs(hash(provenance.get("premise", ""))) % (2 ** 31)
    final_dest = get_production_path(
        date_str, provenance.get("franchise", ""), "flux3", seed
    )
    final_dest.parent.mkdir(parents=True, exist_ok=True)
    part_dest = final_dest.with_suffix(final_dest.suffix + ".part")
    try:
        # Download via ego-bridge (host has fresh browser cookies for CDN auth).
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(
                f"{EGO_BRIDGE_URL}/v1/download",
                json={"url": url, "output_path": to_host_path(part_dest)},
            )
            r.raise_for_status()
            data = r.json()
            size = data.get("size", 0)
            if size < 1000 or not part_dest.exists() or part_dest.stat().st_size == 0:
                logger.error(f"_send_and_poll_flux: file too small ({size}b), likely expired URL")
                try:
                    part_dest.unlink(missing_ok=True)
                except OSError:
                    pass
                return None
        os.replace(part_dest, final_dest)  # atomic
        logger.info(f"_send_and_poll_flux: downloaded to {final_dest} ({size} bytes)")
    except Exception as exc:
        logger.error(f"_send_and_poll_flux: download failed: {exc}")
        try:
            part_dest.unlink(missing_ok=True)
        except OSError:
            pass
        return None

    # Extract the Flux3 prompt (the /t2v command) for registration metadata.
    prompt = ""
    _inner = brief.get("brief", brief)
    pp = _inner.get("production_prompts", {})
    cmd = pp.get("flux3") or ""
    if cmd.startswith("/t2v"):
        prompt = cmd[5:].strip()

    vid_meta = _extract_video_meta(final_dest)
    reg_meta = {
        "engine": "flux3",
        "style_id": provenance.get("style_id", ""),
        "franchise": provenance.get("franchise", ""),
        "premise": provenance.get("premise", ""),
        "niche": provenance.get("niche", ""),
        "qc_status": "ok",
        "prompt": prompt,
        "resolution": vid_meta.get("resolution"),
        "duration_s": vid_meta.get("duration_s"),
        "reroll_count": provenance.get("reroll_count", 0),
    }
    finalized = await _finalize_media(final_dest, reg_meta)
    if not finalized:
        logger.error("_send_and_poll_flux: media finalize (hash/sidecar/register) failed")
        return str(final_dest)  # keep the file even if registration failed
    return finalized


# ─── 3. Grind session (thread driver) ──────────────────────────────────────────


def run_grind_session(max_productions: int = 10, delay_between: int = 60,
                      progress: Optional[dict] = None) -> dict:
    """Loop pick → produce → log → wait. Returns summary dict.

    progress is an optional mutable dict the caller can poll for live status.
    """
    summary = {"produced": 0, "failed": 0, "results": [], "finished": time.time()}
    if progress is not None:
        progress.update({"state": "running", "current": None, "last": None})

    async def driver() -> dict:
        for i in range(max_productions):
            if progress is not None:
                progress["queue_position"] = max_productions - i - 1
            try:
                pick = await asyncio.to_thread(pick_next_production)
                if progress is not None:
                    progress["current"] = pick.get("premise")
                result = await run_production_cycle({**pick})
                summary["produced"] += 1
            except Exception as exc:
                summary["failed"] += 1
                summary["results"].append({"error": str(exc)})
            finally:
                if progress is not None:
                    progress["last"] = summary["results"][-1] if summary["results"] else None
            if i < max_productions - 1:
                await asyncio.sleep(delay_between)
        summary["finished"] = time.time()
        return summary

    summary.update(asyncio.run(driver()))
    if progress is not None:
        progress["state"] = "done"
    return summary
