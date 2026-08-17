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

logger = logging.getLogger(__name__)

# ─── Endpoints (override via env) ─────────────────────────────────────────────
VBL_BRIEF_URL = os.environ.get(
    "VBL_BRIEF_URL", "http://127.0.0.1:8002/v1/agent/brief"
)
H3_BASE_URL = os.environ.get("H3_BASE_URL", "http://127.0.0.1:8420")
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


async def _submit_h3(brief: dict) -> str:
    """Submit the H3 job (from h3_multishot_json, fallback h3_job_json).

    Converts VBL's multishot format (shots array) to wgp.py format
    (script with --- separators, model_type at top level).
    """
    # Unwrap: support both full response and inner brief dict
    inner = brief.get("brief", brief)
    pp = inner.get("production_prompts", {})
    job = pp.get("h3_multishot_json") or pp.get("h3_job_json")
    if not job:
        logger.error("_submit_h3: no h3_multishot_json or h3_job_json in brief")
        return ""

    # Convert VBL multishot format to wgp.py format
    wgp_job = _convert_to_wgp_format(job)
    logger.info(f"_submit_h3: converted job has keys: {list(wgp_job.keys())}")

    api_key = os.environ.get("SGOS_API_KEY", "")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                f"{H3_BASE_URL}/v1/h3/generate",
                json={"h3_job_json": wgp_job},
                headers=headers,
            )
            r.raise_for_status()
            data = r.json()
            session = data.get("session_name", "")
            if session:
                logger.info(f"_submit_h3: submitted session {session}")
            else:
                logger.error(f"_submit_h3: no session_name in response: {data}")
            return session
    except Exception as exc:
        logger.error(f"_submit_h3: failed to submit H3 job: {exc}")
        return ""


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
            # Find & focus the Discord message box, type, then press Enter.
            js_focus = (
                "(()=>{const e=[...document.querySelectorAll('div[contenteditable=true]')]"
                ".find(x=>(x.getAttribute('aria-label')||'').startsWith('Message'));"
                "if(e){e.focus(); return 'ok';} return 'no-input';})()"
            )
            r = await client.post(f"{EGO_BRIDGE_URL}/v1/evaluate", json={"js": js_focus})
            if r.json().get("result") != "ok":
                logger.error("_send_flux3: could not focus Discord input")
                return False
            await client.post(f"{EGO_BRIDGE_URL}/v1/type",
                              json={"selector": '[aria-label="Message"]', "text": flux3_cmd})
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


async def _poll_h3(session: str) -> Optional[str]:
    """Poll H3 status until complete/failed; returns output_file or None."""
    api_key = os.environ.get("SGOS_API_KEY", "")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    deadline = time.time() + H3_POLL_SECONDS
    async with httpx.AsyncClient(timeout=30) as client:
        while time.time() < deadline:
            try:
                r = await client.get(
                    f"{H3_BASE_URL}/v1/h3/status/{session}", headers=headers
                )
                data = r.json()
            except Exception as exc:
                logger.warning(f"_poll_h3: poll error for {session}: {exc}")
                await asyncio.sleep(15)
                continue
            status = data.get("status")
            if status == "complete":
                logger.info(f"_poll_h3: session {session} complete")
                return data.get("output_file")
            if status == "failed":
                logger.error(f"_poll_h3: session {session} failed: {data.get('error')}")
                return None
            await asyncio.sleep(15)
    logger.error(f"_poll_h3: session {session} timed out after {H3_POLL_SECONDS}s")
    return None


async def _poll_flux3() -> Optional[str]:
    """Poll Discord for a result.mp4 CDN url (signed) rendered by the Flux bot."""
    deadline = time.time() + FLUX_POLL_SECONDS
    js = (
        "(()=>{const u=[...document.querySelectorAll('video')]"
        ".map(x=>(x.currentSrc||x.src||'')).find(s=>s.includes('cdn.discordapp.com')&&s.includes('.mp4'));"
        "return u||'';})()"
    )
    async with httpx.AsyncClient(timeout=30) as client:
        while time.time() < deadline:
            try:
                r = await client.post(f"{EGO_BRIDGE_URL}/v1/evaluate", json={"js": js})
                url = (r.json().get("result") or "").strip()
                if url.startswith("http"):
                    return url
            except Exception:
                pass
            await asyncio.sleep(20)
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

    # Kick off H3 + Flux3 concurrently.
    h3_task = asyncio.create_task(_submit_and_poll_h3(brief, out_dir))
    flux_task = asyncio.create_task(_send_and_poll_flux(brief, out_dir))

    h3_video, flux3_video = await asyncio.gather(h3_task, flux_task)

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


async def _submit_and_poll_h3(brief: dict, out_dir: Path) -> Optional[str]:
    session = await _submit_h3(brief)
    if not session:
        logger.error("_submit_and_poll_h3: submit returned empty session")
        return None
    output = await _poll_h3(session)
    if not output:
        logger.error(f"_submit_and_poll_h3: poll returned no output for {session}")
        return None
    try:
        from lib.h3_pipeline import retrieve_h3_result
        dest = out_dir / os.path.basename(output)
        result = await asyncio.to_thread(retrieve_h3_result, output, str(dest))
        logger.info(f"_submit_and_poll_h3: retrieved to {dest}")
        return result
    except Exception as exc:
        logger.error(f"_submit_and_poll_h3: retrieve failed: {exc}")
        return None


async def _send_and_poll_flux(brief: dict, out_dir: Path) -> Optional[str]:
    if not await _send_flux3(brief):
        logger.error("_send_and_poll_flux: send failed")
        return None
    url = await _poll_flux3()
    if not url:
        logger.error("_send_and_poll_flux: poll returned no URL")
        return None
    try:
        dest = out_dir / f"flux3_{int(time.time())}.mp4"
        result = await asyncio.to_thread(_download, url, dest)
        logger.info(f"_send_and_poll_flux: downloaded to {dest}")
        return result
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
