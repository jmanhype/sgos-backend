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
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

from lib.h3_pipeline import build_h3_config_with_fallback

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
H3_POLL_SECONDS = int(os.environ.get("H3_POLL_SECONDS", "900"))
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


async def _send_flux3(brief: dict) -> bool:
    """Navigate ego-bridge to #gen-1 and send the Flux3 /t2v command."""
    inner = brief.get("brief", brief)
    pp = inner.get("production_prompts", {})
    flux3_cmd = pp.get("flux3")
    if not flux3_cmd:
        logger.error("_send_flux3: no flux3 prompt in brief production_prompts")
        return False
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
                return False
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
        return True
    except Exception as exc:
        logger.error(f"_send_flux3: failed: {exc}")
        return False


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


async def _poll_flux3(seen_urls: Optional[set] = None) -> Optional[str]:
    """Poll Discord for a NEW result.mp4 CDN url (the factory's own submission).

    - Polls every FLUX_POLL_INTERVAL (15s) for up to ~5 minutes.
    - Reads ALL video source urls (not just the first match).
    - Skips URLs already present before this submission (`seen_urls`).
    - Logs each attempt so Docker logs show exactly what's happening.
    Returns the new full signed CDN URL, or None on timeout.
    """
    seen = set(seen_urls or set())
    logger.info(
        f"_poll_flux3: polling every {FLUX_POLL_INTERVAL}s, "
        f"up to {FLUX_POLL_ATTEMPTS} attempts; baseline seen={len(seen)} urls"
    )
    async with httpx.AsyncClient(timeout=30) as client:
        for attempt in range(1, FLUX_POLL_ATTEMPTS + 1):
            try:
                r = await client.post(f"{EGO_BRIDGE_URL}/v1/evaluate", json={"js": _cdn_js()})
                raw = (r.json().get("result") or "").strip()
                urls = json.loads(raw) if raw.startswith("[") else []
                new = [u for u in urls if u not in seen]
                logger.info(
                    f"_poll_flux3: attempt {attempt}/{FLUX_POLL_ATTEMPTS} "
                    f"found {len(urls)} cdn urls, {len(new)} new"
                )
                if new:
                    # Return the newest (last) new full URL with its signed token.
                    full = new[-1]
                    logger.info(f"_poll_flux3: detected new result: {full}")
                    return full
            except Exception as exc:
                logger.warning(f"_poll_flux3: attempt {attempt} error: {exc}")
            await asyncio.sleep(FLUX_POLL_INTERVAL)
    logger.error(f"_poll_flux3: no new result after {FLUX_POLL_ATTEMPTS} attempts")
    return None


def _download(url: str, dest: Path) -> str:
    """Download (signed CDN url) to dest using a browser UA; returns dest path."""
    import subprocess
    dest.parent.mkdir(parents=True, exist_ok=True)
    ua = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36")
    r = subprocess.run(
        ["curl", "-sL", "-m", "120", "-A", ua, "-o", str(dest), url],
        capture_output=True, text=True, timeout=130,
    )
    if r.returncode != 0 or not dest.exists() or dest.stat().st_size == 0:
        raise RuntimeError(f"download failed for {url}: {r.stderr.strip()}")
    return str(dest)


async def run_production_cycle(config: dict) -> dict:
    """Run one full production: pick -> brief -> H3 + Flux3 -> poll -> download -> log.

    config keys: style_id/franchise/premise/niche overrides; output_dir.
    """
    pick = (
        await asyncio.to_thread(pick_next_production) if not config.get("style_id")
        else config
    )

    brief_resp = await _get_brief(pick)
    brief = brief_resp.get("brief") or {}
    out_dir = Path(config.get("output_dir", str(OUTPUT_DIR)))

    # Kick off H3 + Flux3 concurrently (return_exceptions so one failure
    # doesn't cancel the other).
    h3_task = asyncio.create_task(_submit_and_poll_h3(brief, out_dir))
    flux_task = asyncio.create_task(_send_and_poll_flux(brief, out_dir))

    results = await asyncio.gather(h3_task, flux_task, return_exceptions=True)
    h3_video = results[0] if not isinstance(results[0], Exception) else None
    flux3_video = results[1] if not isinstance(results[1], Exception) else None
    if isinstance(results[0], Exception):
        logger.error(f"run_production_cycle: H3 task raised: {results[0]}")
    if isinstance(results[1], Exception):
        logger.error(f"run_production_cycle: Flux3 task raised: {results[1]}")

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
    }
    _append_log(entry)

    return {"h3_video": h3_video, "flux3_video": flux3_video,
            "brief": brief_resp, "qc_status": qc_status}


# H3 status polling cadence over the bridge (submit -> status every 15s).
H3_POLL_INTERVAL = int(os.environ.get("H3_POLL_INTERVAL", "15"))


async def _submit_and_poll_h3(brief: dict, out_dir: Path) -> Optional[str]:
    """Submit an H3 job via the host-side h3-bridge and poll/download over HTTP.

    Uses H3_BRIDGE_URL (http://host.docker.internal:8041) instead of direct SSH
    — containers can't reach LAN IPs, so all H3 ops go through the bridge:
      POST /v1/h3/submit   {job_json}                 -> {session_name}
      GET  /v1/h3/status/{session}                     -> {status,progress,output_file,error}
      POST /v1/h3/retrieve {remote_filename,local_path}-> {local_path,size}
    """
    inner = brief.get("brief", brief)
    pp = inner.get("production_prompts", {})
    raw_job = pp.get("h3_multishot_json") or pp.get("h3_job_json")
    if not raw_job:
        logger.error("_submit_and_poll_h3: no h3_multishot_json or h3_job_json in brief")
        return None

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
        logger.error(f"_submit_and_poll_h3: no output_file after timeout for {session_name}")
        return None

    # 3) Retrieve via the bridge (HTTP download to local_path).
    dest = str(out_dir / os.path.basename(output_file))
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        async with httpx.AsyncClient(timeout=300, headers=api_headers) as client:
            r = await client.post(
                f"{H3_BRIDGE_URL}/v1/h3/retrieve",
                json={"remote_filename": output_file, "local_path": dest},
            )
            r.raise_for_status()
            data = r.json() or {}
        logger.info(f"_submit_and_poll_h3: bridge retrieve -> {data}")
        # Trust the bridge's local_path if returned, else our dest.
        return data.get("local_path") or dest
    except Exception as exc:
        logger.error(f"_submit_and_poll_h3: bridge retrieve failed: {exc}")
        return None


async def _send_and_poll_flux(brief: dict, out_dir: Path) -> Optional[str]:
    """Send the Flux3 command, then poll for + download our result.

    If the send raises or returns False, log exactly why and return None
    immediately (don't waste a 5-minute poll on a command that never sent).
    """
    try:
        sent = await _send_flux3(brief)
    except Exception as exc:
        logger.error(f"_send_and_poll_flux: _send_flux3 raised: {exc}")
        return None
    if not sent:
        logger.error("_send_and_poll_flux: send returned False (see _send_flux3 log)")
        return None

    # Baseline: URLs already on the channel right after our send. Our result
    # will be a NEW cdn.mp4 not present here, so we can attribute it to us.
    try:
        seen = await _collect_cdn_urls()
    except Exception as exc:
        logger.warning(f"_send_and_poll_flux: baseline collection failed: {exc}")
        seen = set()

    url = await _poll_flux3(seen)
    if not url:
        logger.error("_send_and_poll_flux: poll returned no NEW URL")
        return None
    try:
        dest = out_dir / f"flux3_{int(time.time())}.mp4"
        # Download via ego-bridge (host has fresh browser cookies for CDN auth)
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(
                f"{EGO_BRIDGE_URL}/v1/download",
                json={"url": url, "output_path": str(dest)},
            )
            r.raise_for_status()
            data = r.json()
            size = data.get("size", 0)
            if size < 1000:
                logger.error(f"_send_and_poll_flux: downloaded file too small ({size}b), likely expired URL")
                return None
        logger.info(f"_send_and_poll_flux: downloaded to {dest} ({size} bytes)")
        return str(dest)
    except Exception as exc:
        logger.error(f"_send_and_poll_flux: download failed: {exc}")
        return None


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
