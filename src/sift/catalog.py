"""Provider + model catalog.

Aggregates everything the settings UI needs to show the user when picking
a provider/model:

* The list of providers we support, their default model, and which env var
  holds their API key.
* Per-model pricing (input/output per-MTok), pulled directly from the
  provider classes' ``pricing`` dicts — single source of truth.
* Per-model accuracy from the most recent provider-comparison eval run
  (``evals/last_provider_comparison.json``), if one exists.

Nothing in here performs an LLM call — it's all static or file-read data —
so the settings endpoints stay cheap to call on every UI mount.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .config import PROJECT_ROOT
from .providers import (
    AnthropicProvider,
    GoogleProvider,
    GroqProvider,
    OpenAIProvider,
)
from .providers.base import LLMProvider

COMPARISON_JSON: Path = PROJECT_ROOT / "evals" / "last_provider_comparison.json"


@dataclass
class ModelEntry:
    """One provider/model row the UI can render."""

    provider: str
    model: str
    input_per_mtok: float
    output_per_mtok: float
    is_default: bool = False
    # Populated from the last comparison eval if that model was scored.
    accuracy: float | None = None
    per_category_recall: dict[str, float] | None = None
    eval_model: str | None = None  # Which exact model string the eval used.


@dataclass
class ProviderEntry:
    """All the info the UI needs to render one provider section."""

    name: str            # "anthropic" | "openai" | "google" | "groq"
    display_name: str    # "Anthropic", "OpenAI", "Google", "Groq"
    env_var: str         # "ANTHROPIC_API_KEY"
    default_model: str
    models: list[ModelEntry] = field(default_factory=list)


# Static per-provider metadata. The pricing + model list comes from the
# provider classes themselves so this stays in sync automatically.
_PROVIDER_SPECS: list[tuple[type[LLMProvider], str, str]] = [
    (AnthropicProvider, "Anthropic", "ANTHROPIC_API_KEY"),
    (OpenAIProvider, "OpenAI", "OPENAI_API_KEY"),
    (GoogleProvider, "Google", "GOOGLE_API_KEY"),
    (GroqProvider, "Groq", "GROQ_API_KEY"),
]


def _load_accuracy() -> dict[tuple[str, str], dict]:
    """Return {(provider, model): eval_row} from the last comparison run.

    Silently returns an empty dict if the file is missing or malformed —
    the UI just hides the accuracy column in that case.
    """
    if not COMPARISON_JSON.exists():
        return {}
    try:
        data = json.loads(COMPARISON_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, list):
        return {}
    out: dict[tuple[str, str], dict] = {}
    for row in data:
        if not isinstance(row, dict):
            continue
        provider = row.get("provider")
        model = row.get("model")
        if provider and model:
            out[(provider, model)] = row
    return out


def _best_accuracy_for_provider(
    accuracy_rows: dict[tuple[str, str], dict],
    provider: str,
) -> dict | None:
    """If the exact (provider, model) isn't in the eval, fall back to any row
    for the same provider. Useful for showing "claude-opus-4-6" an accuracy
    number when the eval only scored claude-sonnet-4-6 — the user at least
    sees the provider's demonstrated capability.
    """
    for (p, _m), row in accuracy_rows.items():
        if p == provider:
            return row
    return None


def build_catalog() -> list[ProviderEntry]:
    """Assemble the full catalog: providers -> models -> pricing + accuracy."""
    accuracy_rows = _load_accuracy()
    entries: list[ProviderEntry] = []
    for cls, display, env_var in _PROVIDER_SPECS:
        provider_name = cls.name or ""
        default_model = cls.default_model or ""
        models: list[ModelEntry] = []

        # Sort so the default model lands first — UIs typically want that.
        ordered = sorted(
            cls.pricing.items(),
            key=lambda item: (item[0] != default_model, item[0]),
        )
        for model, (inp, outp) in ordered:
            exact = accuracy_rows.get((provider_name, model))
            fallback = (
                exact
                if exact is not None
                else _best_accuracy_for_provider(accuracy_rows, provider_name)
            )
            acc = fallback.get("accuracy") if fallback else None
            per_cat = fallback.get("per_category_recall") if fallback else None
            eval_model = fallback.get("model") if fallback else None
            models.append(
                ModelEntry(
                    provider=provider_name,
                    model=model,
                    input_per_mtok=float(inp),
                    output_per_mtok=float(outp),
                    is_default=(model == default_model),
                    accuracy=float(acc) if acc is not None else None,
                    per_category_recall=dict(per_cat) if per_cat else None,
                    # Only mark "eval_model" when it differs from this row's model
                    # so the UI can flag "accuracy is from a sibling model".
                    eval_model=eval_model if eval_model and eval_model != model else None,
                )
            )
        entries.append(
            ProviderEntry(
                name=provider_name,
                display_name=display,
                env_var=env_var,
                default_model=default_model,
                models=models,
            )
        )
    return entries


def all_models() -> list[ModelEntry]:
    """Flat list of every (provider, model) pair — handy for the dropdown."""
    return [m for p in build_catalog() for m in p.models]
