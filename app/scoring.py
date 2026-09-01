"""Reasoning assessment — the differentiator.

Conventional compliance training asks "did they pick the right option?". That
tests recall and produces a training record that proves very little: an employee
can pass by pattern-matching on which answer looks strictest.

This module asks a different question: given what this person WROTE, do they
understand the policy well enough to apply it to a situation nobody has shown
them? That is what a regulator actually cares about, and it is only answerable
against free text.

The grading contract lives in prompts.SCORING_SYSTEM. This module's job is to
make the result safe to persist: the model is a language model, not a validated
scoring function, so every field it returns is treated as untrusted and coerced
into range before it reaches the database.
"""

from __future__ import annotations

from app import prompts
from app.llm import LLMError, complete_json
from app.models import Scenario

_MAX_TOKENS = 1024

# The three verdicts the UI knows how to render.
ALIGNED = "aligned"
PARTIALLY_ALIGNED = "partially aligned"
MISALIGNED = "misaligned"
VALID_VERDICTS = {ALIGNED, PARTIALLY_ALIGNED, MISALIGNED}

# Score thresholds used to derive a verdict when the model omits or mangles one.
_ALIGNED_FLOOR = 75
_PARTIAL_FLOOR = 40

NOT_ADDRESSED = "Not addressed by this policy"


def score_answer(scenario: Scenario, user_answer: str) -> dict:
    """Assess `user_answer` against `scenario` and its governing policy excerpt.

    Returns a dict with the keys Response needs: reasoning_score, verdict,
    feedback_text, cited_policy_section. Raises LLMError on failure.
    """
    answer = (user_answer or "").strip()
    if not answer:
        raise LLMError("Please write an answer before submitting.")

    payload = complete_json(
        system=prompts.SCORING_SYSTEM,
        user=prompts.scoring_user(
            scenario_text=scenario.prompt_text,
            policy_excerpt=scenario.relevant_policy_excerpt,
            user_answer=answer,
        ),
        max_tokens=_MAX_TOKENS,
        demo_key="scoring",
    )

    if not isinstance(payload, dict):
        raise LLMError(
            "The model returned an unexpected format for the assessment "
            f"(expected an object, got {type(payload).__name__}). Please try again."
        )

    score = _coerce_score(payload.get("reasoning_score"))
    verdict = _coerce_verdict(payload.get("verdict"), score)
    feedback = _coerce_text(payload.get("feedback_text"))
    citation = _coerce_text(payload.get("cited_policy_section"))

    if not feedback:
        raise LLMError(
            "The model returned an assessment with no feedback. Please try again."
        )

    if not citation:
        # A missing citation is a grounding failure, not a crash. Say so plainly
        # rather than silently presenting an uncited score as if it were sourced.
        citation = NOT_ADDRESSED

    return {
        "reasoning_score": score,
        "verdict": verdict,
        "feedback_text": feedback,
        "cited_policy_section": citation,
    }


def _coerce_score(value: object) -> int:
    """Clamp whatever the model returned into a 0-100 integer.

    Accepts ints, floats and numeric strings ("85", "85/100" -> 85). Anything
    uninterpretable falls back to 0, which reads as "not assessed" rather than
    silently crediting the answer.
    """
    if isinstance(value, bool):  # bool is an int subclass — reject explicitly
        return 0
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        cleaned = value.strip().split("/")[0].strip()
        try:
            number = float(cleaned)
        except ValueError:
            return 0
    else:
        return 0

    return max(0, min(100, int(round(number))))


def _coerce_verdict(value: object, score: int) -> str:
    """Normalise the verdict, deriving one from the score if it is unusable.

    Keeping verdict and score consistent matters: the results page colour-codes
    on verdict and displays the number, so a mismatch between them would look
    like a bug to the person being assessed.
    """
    if isinstance(value, str):
        normalised = value.strip().lower()
        if normalised in VALID_VERDICTS:
            return normalised
        # Tolerate near-misses like "partially-aligned" or "Partially Aligned."
        collapsed = normalised.replace("-", " ").replace("_", " ").rstrip(".")
        collapsed = " ".join(collapsed.split())
        if collapsed in VALID_VERDICTS:
            return collapsed

    if score >= _ALIGNED_FLOOR:
        return ALIGNED
    if score >= _PARTIAL_FLOOR:
        return PARTIALLY_ALIGNED
    return MISALIGNED


def _coerce_text(value: object) -> str:
    """Return a stripped string, or empty if the model sent something else."""
    return value.strip() if isinstance(value, str) else ""
