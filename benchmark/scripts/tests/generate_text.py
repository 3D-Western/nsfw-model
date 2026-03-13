"""
Test VLMs for generating synthetic user text from 3D model render images.
Uses OpenRouter to access all providers with a single API key.

Usage:
    python tests/generate_text.py --images ./test_images --output ./results/haiku --model anthropic/claude-3-haiku --samples 5
    python tests/generate_text.py --images ./test_images --output ./results/4omini --model openai/gpt-4o-mini --samples 5
    python tests/generate_text.py --images ./test_images --output ./results/flash --model google/gemini-2.0-flash-exp --samples 5

Requires .env with: OPENROUTER_API_KEY=...
"""

import argparse
import asyncio
import base64
import json
import os
import time
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from openai import AsyncOpenAI


PROMPT_TEMPLATE = """
You are simulating a user submitting a 3D model for printing.
Look at these rendered images of a 3D model and write:
1. A natural user description similar to what someone would type when uploading
2. A title for the model
3. Any tags they might add

Be realistic. Users describe models informally.

Respond in JSON: {"title": "...", "description": "...", "tags": [...]}
"""

IMG_EXTS = {".png", ".jpg", ".jpeg", ".webp"}

# OpenRouter model IDs
SUGGESTED_MODELS = [
    "anthropic/claude-3-haiku",
    "anthropic/claude-sonnet-4",
    "openai/gpt-4o-mini",
    "openai/gpt-4o",
    "google/gemini-2.0-flash-001",
    "google/gemini-2.5-pro-preview",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def load_image_b64(path: Path) -> tuple[str, str]:
    ext = path.suffix.lower()
    media_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
    media_type = media_map.get(ext, "image/png")
    data = base64.standard_b64encode(path.read_bytes()).decode()
    return data, media_type


def get_sample_dirs(images_dir: Path, limit: int | None = None) -> list[Path]:
    dirs = sorted(d for d in images_dir.iterdir() if d.is_dir())
    return dirs[:limit] if limit else dirs


def get_images_for_sample(sample_dir: Path, max_images: int = 4) -> list[Path]:
    imgs = sorted(p for p in sample_dir.iterdir() if p.suffix.lower() in IMG_EXTS)
    return imgs[:max_images]


def parse_json_response(raw: str) -> dict | None:
    raw = raw.strip()
    if raw.startswith("```"):
        lines = [l for l in raw.split("\n") if not l.strip().startswith("```")]
        raw = "\n".join(lines)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# OpenRouter call (works for all providers)
# ---------------------------------------------------------------------------
async def call_openrouter(images: list[Path], model: str, sem: asyncio.Semaphore) -> dict:
    client = AsyncOpenAI(
        api_key=os.environ["OPENROUTER_API_KEY"],
        base_url="https://openrouter.ai/api/v1",
    )

    content = []
    for img_path in images:
        b64, media = load_image_b64(img_path)
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:{media};base64,{b64}"},
        })
    content.append({"type": "text", "text": PROMPT_TEMPLATE})

    async with sem:
        t0 = time.time()
        resp = await client.chat.completions.create(
            model=model,
            max_tokens=512,
            messages=[{"role": "user", "content": content}],
        )
        latency = time.time() - t0

    return {
        "raw": resp.choices[0].message.content,
        "usage": {
            "input_tokens": resp.usage.prompt_tokens if resp.usage else 0,
            "output_tokens": resp.usage.completion_tokens if resp.usage else 0,
            "latency_s": round(latency, 2),
        },
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def process_sample(sample_dir: Path, model: str, sem: asyncio.Semaphore, max_images: int) -> dict:
    sample_id = sample_dir.name
    images = get_images_for_sample(sample_dir, max_images)
    if not images:
        return {"sample_id": sample_id, "error": "no images found"}

    try:
        result = await call_openrouter(images, model, sem)
        parsed = parse_json_response(result["raw"])
        return {
            "sample_id": sample_id,
            "model": model,
            "parsed": parsed,
            "raw_response": result["raw"],
            "usage": result["usage"],
            "num_images": len(images),
            "error": None if parsed else "json_parse_failed",
        }
    except Exception as e:
        return {"sample_id": sample_id, "model": model, "error": str(e)}


async def run(args):
    images_dir = Path(args.images)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    sample_dirs = get_sample_dirs(images_dir, args.samples)
    if not sample_dirs:
        print(f"No sample directories found in {images_dir}")
        print(f"Expected structure: {images_dir}/<sample_id>/*.png")
        return

    sem = asyncio.Semaphore(args.concurrency)

    print(f"Testing {args.model} via OpenRouter on {len(sample_dirs)} samples ({args.max_images} imgs each)\n")

    tasks = [process_sample(d, args.model, sem, args.max_images) for d in sample_dirs]
    results = await asyncio.gather(*tasks)

    # Print results + collect stats
    total_in, total_out, total_lat, successes = 0, 0, 0.0, 0
    for r in results:
        sid = r["sample_id"]
        out_file = output_dir / f"{sid}.json"
        out_file.write_text(json.dumps(r, indent=2))

        if r.get("error"):
            print(f"  ✗ {sid}: {r['error']}")
        else:
            title = r["parsed"].get("title", "?") if r.get("parsed") else "?"
            print(f"  ✓ {sid}: \"{title}\"")
            successes += 1

        usage = r.get("usage", {})
        total_in += usage.get("input_tokens", 0)
        total_out += usage.get("output_tokens", 0)
        total_lat += usage.get("latency_s", 0)

    # Summary
    n = len(results)
    summary = {
        "model": args.model,
        "samples": n,
        "successes": successes,
        "failures": n - successes,
        "total_input_tokens": total_in,
        "total_output_tokens": total_out,
        "avg_latency_s": round(total_lat / max(n, 1), 2),
    }
    summary_path = output_dir / "_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2))

    print(f"\n{'='*50}")
    print(f"  {args.model}")
    print(f"  Success: {successes}/{n}")
    print(f"  Tokens:  {total_in} in / {total_out} out")
    print(f"  Avg latency: {summary['avg_latency_s']}s")
    print(f"  Results: {output_dir}")
    print(f"{'='*50}\n")


def main():
    parser = argparse.ArgumentParser(description="Test VLMs for synthetic text generation from 3D model renders")
    parser.add_argument("--images", required=True, help="Dir of rendered images (subdirs per sample)")
    parser.add_argument("--output", required=True, help="Output dir for results")
    parser.add_argument("--model", required=True, help="OpenRouter model ID e.g. anthropic/claude-3-haiku, openai/gpt-4o-mini, google/gemini-2.0-flash-exp")
    parser.add_argument("--samples", type=int, default=None, help="Limit samples")
    parser.add_argument("--max-images", type=int, default=4, help="Max images per sample")
    parser.add_argument("--concurrency", type=int, default=5, help="Max concurrent API calls")
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()