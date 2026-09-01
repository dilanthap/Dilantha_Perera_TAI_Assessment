"""Scenario generation: policy document in, grounded scenarios out.

ARCHITECTURAL NOTE — there is deliberately no vector store here.
=================================================================
The obvious instinct for "answer questions about a document" is to chunk it,
embed the chunks, and retrieve the top-k per query. This app does not, and that
is a considered choice rather than a shortcut.

An AI-usage policy is a few pages — the Northwind sample is roughly 1,100 tokens.
The entire document fits in the model's context window many times over, so we
send all of it on every call. That buys three things chunked retrieval would
cost us:

  1. Completeness. Scenario generation needs to see the WHOLE policy to pick
     provisions that interact and to spread scenarios across different sections.
     Top-k retrieval optimises for local relevance and would systematically miss
     cross-section tensions — exactly the non-obvious cases worth training on.
  2. Fidelity of citation. The excerpt the model returns is verbatim from the
     real document, not from a chunk boundary that may have severed a clause
     from the heading that qualifies it.
  3. Honesty about "the policy doesn't cover this". A retrieval miss and a
     genuine gap in the policy look identical to the model. With the full text
     in context, silence is real silence.

Where this stops working: a client with a library of long policies, or
enterprise handbooks running to hundreds of pages. At that point the right move
is chunked retrieval with section-aware splitting so citations stay intact —
see the README's "What I'd do with more time".
"""

from __future__ import annotations

from app import prompts
from app.llm import LLMError, complete_json
from app.models import Policy, Scenario

# Generous enough for 5 scenarios with substantial policy excerpts.
_MAX_TOKENS = 4096

# Guardrails on what we accept back from the model.
_MIN_SCENARIOS = 1
_MAX_SCENARIOS = 5
_MIN_PROMPT_CHARS = 40
_MIN_EXCERPT_CHARS = 20


def generate_scenarios(policy: Policy) -> list[Scenario]:
    """Generate grounded scenarios for `policy`.

    Returns unsaved Scenario objects — the caller owns the transaction.
    Raises LLMError if the model fails or returns nothing usable.
    """
    if not policy.raw_text or not policy.raw_text.strip():
        raise LLMError("This policy document appears to be empty.")

    # The full policy text goes into the prompt. See the module docstring.
    payload = complete_json(
        system=prompts.SCENARIO_SYSTEM,
        user=prompts.scenario_user(policy.raw_text),
        max_tokens=_MAX_TOKENS,
        demo_key="scenarios",
    )

    if not isinstance(payload, list):
        raise LLMError(
            "The model returned an unexpected format for scenarios "
            f"(expected a list, got {type(payload).__name__}). Please try again."
        )

    scenarios: list[Scenario] = []
    for item in payload:
        parsed = _coerce_scenario(item)
        if parsed is None:
            continue  # drop malformed entries rather than failing the whole batch
        prompt_text, excerpt = parsed
        scenarios.append(
            Scenario(
                policy_id=policy.id,
                prompt_text=prompt_text,
                relevant_policy_excerpt=excerpt,
            )
        )
        if len(scenarios) >= _MAX_SCENARIOS:
            break

    if len(scenarios) < _MIN_SCENARIOS:
        raise LLMError(
            "The model did not return any usable scenarios for this document. "
            "This can happen if the uploaded text is not actually a policy — "
            "check the document and try again."
        )

    return scenarios


def _coerce_scenario(item: object) -> tuple[str, str] | None:
    """Validate one entry. Returns (prompt_text, excerpt) or None if unusable."""
    if not isinstance(item, dict):
        return None

    prompt_text = item.get("prompt_text")
    excerpt = item.get("relevant_policy_excerpt")

    if not isinstance(prompt_text, str) or not isinstance(excerpt, str):
        return None

    prompt_text = prompt_text.strip()
    excerpt = excerpt.strip()

    if len(prompt_text) < _MIN_PROMPT_CHARS or len(excerpt) < _MIN_EXCERPT_CHARS:
        return None

    return prompt_text, excerpt
