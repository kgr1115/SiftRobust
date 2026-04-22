"""Run the classifier comparison against ONE provider and upsert its row into
``evals/last_provider_comparison.json``.

Use this instead of ``pytest evals/test_provider_comparison.py`` when you only
need to refresh numbers for a single provider — for example, after topping up
credits on a single API, or after changing a provider's default model. One
pass on 40 fixtures takes ~30-90s depending on the provider's rate limits.

Usage (from the project root, inside the venv):
    python scripts/run_one_provider.py anthropic
    python scripts/run_one_provider.py openai
    python scripts/run_one_provider.py google
    python scripts/run_one_provider.py groq
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# Make sure ``src`` is importable when invoked as ``python scripts/run_one_provider.py``
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env", override=True)

from sift.classifier import _CLASSIFY_SCHEMA, _CLASSIFY_SYSTEM, _render_thread  # noqa: E402
from sift.fixtures import load_labeled_threads  # noqa: E402
from sift.llm import structured_call_full  # noqa: E402
from sift.models import Classification  # noqa: E402
from sift.providers import get_provider  # noqa: E402

OUTPUT_JSON = PROJECT_ROOT / "evals" / "last_provider_comparison.json"


def run(provider_name: str) -> dict:
    provider = get_provider(provider_name)
    model = provider.model
    in_rate, out_rate = provider.get_pricing(model)

    threads = load_labeled_threads()
    n = len(threads)
    correct = 0
    errors = 0
    input_tokens = 0
    output_tokens = 0
    total_latency_ms = 0.0
    by_truth: dict[str, int] = {}
    by_correct: dict[str, int] = {}

    t0 = time.perf_counter()
    for i, t in enumerate(threads, 1):
        truth = t.label.value
        by_truth[truth] = by_truth.get(truth, 0) + 1
        user = f"Classify the following email thread:\n\n---\n{_render_thread(t)}\n---"
        try:
            result = structured_call_full(
                system=_CLASSIFY_SYSTEM,
                user=user,
                tool_name="classify_thread",
                tool_description="Record the triage classification for an email thread.",
                input_schema=_CLASSIFY_SCHEMA,
                provider_name=provider_name,
                max_tokens=400,
                log_tag=f"compare_{provider_name}",
            )
            data = result.data or {}
            cls = Classification(thread_id=t.id, **data)
            if cls.category.value == truth:
                correct += 1
                by_correct[truth] = by_correct.get(truth, 0) + 1
            if result.usage:
                input_tokens += result.usage.input_tokens
                output_tokens += result.usage.output_tokens
                total_latency_ms += result.usage.latency_ms
        except Exception as e:  # noqa: BLE001
            errors += 1
            print(f"  [{i}/{n}] {t.id}: {type(e).__name__}: {str(e)[:120]}", flush=True)

        if i % 10 == 0:
            print(f"  [{i}/{n}] {provider_name}: so_far_correct={correct}", flush=True)

    wall_ms = (time.perf_counter() - t0) * 1000
    cost = input_tokens / 1_000_000 * in_rate + output_tokens / 1_000_000 * out_rate

    return {
        "provider": provider_name,
        "model": model,
        "accuracy": round(correct / n, 4) if n else 0.0,
        "errors": errors,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_latency_ms": round(total_latency_ms, 2),
        "wall_ms": round(wall_ms, 2),
        "estimated_cost_usd": round(cost, 6),
        "per_category_recall": {
            cat: round(by_correct.get(cat, 0) / by_truth[cat], 4)
            for cat in by_truth
        },
    }


def upsert(row: dict) -> None:
    rows: list[dict] = []
    if OUTPUT_JSON.exists():
        try:
            rows = json.loads(OUTPUT_JSON.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            rows = []
    # Replace existing row for this provider in place; otherwise append.
    updated: list[dict] = []
    replaced = False
    for r in rows:
        if r.get("provider") == row["provider"]:
            updated.append(row)
            replaced = True
        else:
            updated.append(r)
    if not replaced:
        updated.append(row)
    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(updated, indent=2), encoding="utf-8")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} <anthropic|openai|google|groq>", file=sys.stderr)
        return 2
    provider_name = argv[1]
    print(f"=== {provider_name} ===", flush=True)
    row = run(provider_name)
    upsert(row)
    print(
        f"{provider_name}: acc={row['accuracy']:.3f} errors={row['errors']} "
        f"tokens={row['input_tokens']}/{row['output_tokens']} "
        f"wall={row['wall_ms']:.0f}ms cost=${row['estimated_cost_usd']:.4f}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
