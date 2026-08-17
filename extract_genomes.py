"""
Background genome extractor — processes outliers one at a time.
Safe to run as a cron job or background task.
"""
import json
import sys
import time
from pathlib import Path

# Ensure .env is loaded before any SGOS imports
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env", override=True)

from database import get_connection, get_outliers
from services.pipeline import create_pipeline_engine


def extract_genomes(limit: int = 10, hours: int = 72):
    """Process outliers one at a time, with progress logging."""
    outliers = get_outliers(platform="", hours=hours, limit=limit)
    print(f"Found {len(outliers)} outliers to process")

    if not outliers:
        return {"processed": 0, "success": 0, "failed": 0}

    engine = create_pipeline_engine()
    success = 0
    failed = 0

    for i, post in enumerate(outliers):
        post_id = post.get("id", "?")
        title = (post.get("title") or "?")[:60]
        print(f"\n[{i+1}/{len(outliers)}] Processing: {post_id} — {title}")
        start = time.time()

        try:
            # Process single outlier
            result = engine.process_outliers(outliers=[post], skip_existing=True)
            elapsed = time.time() - start

            genomes = result.get("genomes_extracted", 0)
            scored = result.get("scored", 0)
            opps = result.get("opportunities_generated", 0)

            if genomes > 0:
                print(f"  ✅ Genome extracted in {elapsed:.1f}s (scored={scored}, opportunities={opps})")
                success += 1
            else:
                print(f"  ⏭️  Skipped (already extracted or no viral signal)")
                success += 1

        except Exception as e:
            elapsed = time.time() - start
            print(f"  ❌ Failed after {elapsed:.1f}s: {e}")
            failed += 1

    return {"processed": len(outliers), "success": success, "failed": failed}


if __name__ == "__main__":
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    hours = int(sys.argv[2]) if len(sys.argv) > 2 else 72
    result = extract_genomes(limit=limit, hours=hours)
    print(f"\n{'='*50}")
    print(json.dumps(result, indent=2))
