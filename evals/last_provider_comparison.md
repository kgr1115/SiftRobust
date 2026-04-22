# Provider Comparison

_Regenerated 2026-04-22. 40 labeled fixtures._

| Provider | Model | Accuracy | Errors | Total cost | $/1k threads | Avg latency | In / Out tokens |
|----------|-------|---------:|-------:|-----------:|-------------:|------------:|----------------:|
| groq | `llama-3.3-70b-versatile` | 97.5% | 0 | $0.0299 | $0.747 | 5254 ms | 48,136 / 1,891 |
| anthropic | `claude-sonnet-4-6` | 95.0% | 0 | $0.3032 | $7.581 | 3109 ms | 73,317 / 5,552 |
| openai | `gpt-4o-mini` | 92.5% | 0 | $0.0103 | $0.257 | 1394 ms | 60,804 / 1,915 |
| google | `gemini-2.5-flash` | 92.5% | 0 | $0.0240 | $0.601 | 1019 ms | 58,679 / 2,576 |

## Per-category recall

| Provider | fyi | needs_reply | newsletter | trash | urgent |
|----------|----:|------------:|-----------:|------:|-------:|
| groq | 100% | 100% | 88% | 100% | 100% |
| anthropic | 100% | 80% | 100% | 100% | 100% |
| openai | 100% | 90% | 100% | 67% | 100% |
| google | 100% | 80% | 100% | 83% | 100% |

## Notes

- **Accuracy** is raw classification accuracy over all labeled fixtures.
- **Errors** are calls where the provider raised or returned an invalid payload (counted as a miss against the `fyi` fallback).
- **Cost** uses the per-MTok prices declared on each provider class (`pricing` attribute).
- **Avg latency** is wall-clock per-call time and includes network round-trip.
- Regenerated from `last_provider_comparison.json`; rerun `pytest evals/test_provider_comparison.py -v -s` or `python scripts/run_one_provider.py <name>` to refresh.
