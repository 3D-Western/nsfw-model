"""
Cost estimation report for VLM text generation across the full benchmark dataset.

Usage:
    python cost_report.py
    python cost_report.py --samples 500 --images-per-sample 4
"""

import argparse
import json
from pathlib import Path


# Pricing per 1M tokens (verify current pricing before running)
MODELS = {
    "claude-sonnet-4-20250514": {
        "provider": "anthropic",
        "input_per_1m": 3.00,
        "output_per_1m": 15.00,
        "est_input_tokens_per_image": 1600,
        "est_text_prompt_tokens": 200,
        "est_output_tokens": 150,
        "notes": "Best quality, highest cost",
    },
    "claude-haiku-3-20240307": {
        "provider": "anthropic",
        "input_per_1m": 0.25,
        "output_per_1m": 1.25,
        "est_input_tokens_per_image": 1600,
        "est_text_prompt_tokens": 200,
        "est_output_tokens": 150,
        "notes": "Fast, cheap, good-enough quality for text gen",
    },
    "gpt-4o-2024-11-20": {
        "provider": "openai",
        "input_per_1m": 2.50,
        "output_per_1m": 10.00,
        "est_input_tokens_per_image": 850,
        "est_text_prompt_tokens": 200,
        "est_output_tokens": 150,
        "notes": "Strong multimodal, competitive pricing",
    },
    "gpt-4o-mini-2024-07-18": {
        "provider": "openai",
        "input_per_1m": 0.15,
        "output_per_1m": 0.60,
        "est_input_tokens_per_image": 850,
        "est_text_prompt_tokens": 200,
        "est_output_tokens": 150,
        "notes": "Cheapest option, may sacrifice quality",
    },
    "gemini-2.0-flash": {
        "provider": "google",
        "input_per_1m": 0.10,
        "output_per_1m": 0.40,
        "est_input_tokens_per_image": 258,
        "est_text_prompt_tokens": 200,
        "est_output_tokens": 150,
        "notes": "Extremely cheap, fast, worth testing quality",
    },
    "gemini-2.5-pro-preview-05-06": {
        "provider": "google",
        "input_per_1m": 1.25,
        "output_per_1m": 10.00,
        "est_input_tokens_per_image": 258,
        "est_text_prompt_tokens": 200,
        "est_output_tokens": 150,
        "notes": "High quality, moderate cost for Google",
    },
}


def estimate_cost(model_info: dict, num_samples: int, images_per_sample: int) -> dict:
    input_tokens_per_sample = (
        model_info["est_input_tokens_per_image"] * images_per_sample
        + model_info["est_text_prompt_tokens"]
    )
    output_tokens_per_sample = model_info["est_output_tokens"]

    total_input = input_tokens_per_sample * num_samples
    total_output = output_tokens_per_sample * num_samples

    input_cost = (total_input / 1_000_000) * model_info["input_per_1m"]
    output_cost = (total_output / 1_000_000) * model_info["output_per_1m"]
    total_cost = input_cost + output_cost

    return {
        "input_tokens_per_sample": input_tokens_per_sample,
        "output_tokens_per_sample": output_tokens_per_sample,
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "input_cost_usd": round(input_cost, 4),
        "output_cost_usd": round(output_cost, 4),
        "total_cost_usd": round(total_cost, 4),
    }


def main():
    parser = argparse.ArgumentParser(description="Estimate VLM costs for synthetic text generation")
    parser.add_argument("--samples", type=int, default=500, help="Number of samples in benchmark dataset")
    parser.add_argument("--images-per-sample", type=int, default=4, help="Images sent per VLM call")
    parser.add_argument("--output", default=None, help="Save report as JSON")
    args = parser.parse_args()

    n = args.samples
    ips = args.images_per_sample

    print(f"\n{'='*70}")
    print(f"  VLM COST REPORT — {n} samples, {ips} images/sample")
    print(f"{'='*70}\n")

    header = f"{'Model':<35} {'Input $':>10} {'Output $':>10} {'Total $':>10}"
    print(header)
    print("-" * len(header))

    report = {}
    for model_name, info in MODELS.items():
        costs = estimate_cost(info, n, ips)
        report[model_name] = {**costs, "provider": info["provider"], "notes": info["notes"]}
        print(f"{model_name:<35} {costs['input_cost_usd']:>10.4f} {costs['output_cost_usd']:>10.4f} {costs['total_cost_usd']:>10.4f}")

    print(f"\n{'--- Recommendation ---':^70}")
    cheapest = min(report.items(), key=lambda x: x[1]["total_cost_usd"])
    print(f"\nCheapest:  {cheapest[0]} @ ${cheapest[1]['total_cost_usd']:.4f}")

    # Tier breakdown
    print(f"\nBy tier:")
    budget = [(k, v) for k, v in report.items() if v["total_cost_usd"] < 1.0]
    mid = [(k, v) for k, v in report.items() if 1.0 <= v["total_cost_usd"] < 5.0]
    premium = [(k, v) for k, v in report.items() if v["total_cost_usd"] >= 5.0]

    if budget:
        print(f"  Budget  (<$1):  {', '.join(k for k, _ in budget)}")
    if mid:
        print(f"  Mid     ($1-5): {', '.join(k for k, _ in mid)}")
    if premium:
        print(f"  Premium (>$5):  {', '.join(k for k, _ in premium)}")

    print(f"\nSuggestion: Run `generate_text.py --samples 10` with 2-3 models,")
    print(f"spot-check output quality, then pick the cheapest one that's good enough.\n")

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2))
        print(f"Report saved to {out}")


if __name__ == "__main__":
    main()