"""Thin wrapper around the Anthropic API.

Everything that talks to Claude goes through here, so there is exactly one place
that handles model choice, timeouts, error translation and JSON extraction.

Three things this module guarantees to its callers:
  - It never raises a raw SDK exception. Every failure surfaces as `LLMError`
    with a message that is safe to show a user.
  - It never returns malformed JSON. Either you get a parsed object, or LLMError.
  - It never requires a network call in DEMO_MODE.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

import anthropic
from dotenv import load_dotenv

from app import fixtures

load_dotenv()

# --------------------------------------------------------------------------
# Model selection
# --------------------------------------------------------------------------
#
# Haiku 4.5 is the small/fast tier: cheapest and lowest latency, which keeps the
# demo snappy (scenario generation is the slow call — 3-5 scenarios in one shot).
#
# >>> SWAP POINT <<<
# Scoring reasoning quality is the most nuanced call in the app. If feedback
# starts reading generic or missing subtle policy tensions, raise the tier here:
#     DEFAULT_MODEL = "claude-sonnet-5"   # better nuance, ~2x input cost
#     DEFAULT_MODEL = "claude-opus-5"     # best judgement, highest cost
# Nothing else in the codebase needs to change. If you split generation and
# scoring across tiers, give each module its own constant and pass `model=`
# through `complete_json`, which already accepts it.
DEFAULT_MODEL = "claude-haiku-4-5"

# Per-request timeout in seconds. Generation asks for several scenarios at once
# and is the slowest call; 30s is comfortable headroom at the Haiku tier.
REQUEST_TIMEOUT_SECONDS = 30.0

# The SDK retries connection errors, 429s and 5xx itself with backoff.
MAX_RETRIES = 1

DEMO_MODE = os.getenv("DEMO_MODE") == "1"


class LLMError(Exception):
    """Any failure reaching or understanding the model.

    Carries a user-facing message. Routes catch this and render error.html —
    a failed API call must never surface as a stack trace.
    """


class JSONParseError(LLMError):
    """The model replied, but not with usable JSON."""


_client: anthropic.Anthropic | None = None


def get_client() -> anthropic.Anthropic:
    """Build the client lazily.

    Lazy so that importing this module never fails on a missing API key — the
    app can still boot, serve pages, and show a clean error, rather than dying
    at import time with a traceback.
    """
    global _client
    if _client is None:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise LLMError(
                "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add "
                "your key, or set DEMO_MODE=1 to run with sample data and no key."
            )
        # NOTE: anthropic 1.x removed temperature/top_p/top_k from the SDK
        # signature — passing them raises TypeError. Haiku 4.5 additionally
        # rejects output_config.effort and predates adaptive thinking, so no
        # `thinking` or `output_config` is sent on these calls.
        _client = anthropic.Anthropic(
            api_key=api_key,
            timeout=REQUEST_TIMEOUT_SECONDS,
            max_retries=MAX_RETRIES,
        )
    return _client


# --------------------------------------------------------------------------
# JSON extraction
# --------------------------------------------------------------------------

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def _strip_fences(text: str) -> str:
    """Remove ```json ... ``` wrappers the model may add despite instructions."""
    return _FENCE_RE.sub("", text.strip()).strip()


def _find_json_span(text: str) -> str | None:
    """Return the first complete JSON array/object in `text`, or None.

    Scans for a balanced bracket span while respecting string literals and
    escapes, so a brace inside a quoted policy excerpt doesn't end the span
    early. This is what makes a prose preamble ("Here are the scenarios: [...]")
    recoverable instead of fatal.
    """
    start = None
    for i, ch in enumerate(text):
        if ch in "[{":
            start = i
            break
    if start is None:
        return None

    opener = text[start]
    closer = "]" if opener == "[" else "}"
    depth = 0
    in_string = False
    escaped = False

    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def extract_json(raw: str) -> Any:
    """Parse JSON out of a model response, tolerating fences and stray prose.

    Raises JSONParseError if nothing parseable is found.
    """
    if not raw or not raw.strip():
        raise JSONParseError("The model returned an empty response.")

    cleaned = _strip_fences(raw)

    # Fast path: the model followed instructions exactly.
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Recovery path: pull the first balanced JSON span out of surrounding text.
    span = _find_json_span(cleaned)
    if span is not None:
        try:
            return json.loads(span)
        except json.JSONDecodeError:
            pass

    preview = cleaned[:200].replace("\n", " ")
    raise JSONParseError(f"Could not parse JSON from the model response: {preview!r}")


# --------------------------------------------------------------------------
# API calls
# --------------------------------------------------------------------------


def complete_text(
    system: str,
    user: str,
    max_tokens: int = 4096,
    model: str = DEFAULT_MODEL,
) -> str:
    """One non-streaming call. Returns concatenated text blocks.

    Translates every SDK exception into LLMError with a message safe to display.
    """
    client = get_client()
    try:
        message = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
    except anthropic.AuthenticationError:
        raise LLMError(
            "Anthropic rejected the API key. Check ANTHROPIC_API_KEY in your .env file."
        ) from None
    except anthropic.RateLimitError:
        raise LLMError(
            "Rate limited by the Anthropic API. Wait a moment and try again."
        ) from None
    except anthropic.APITimeoutError:
        raise LLMError(
            f"The request to Claude timed out after {REQUEST_TIMEOUT_SECONDS:.0f}s. "
            "This is usually transient — try again."
        ) from None
    except anthropic.APIConnectionError:
        raise LLMError(
            "Could not reach the Anthropic API. Check your network connection."
        ) from None
    except anthropic.APIStatusError as exc:
        if exc.status_code >= 500:
            raise LLMError(
                f"Anthropic returned a server error ({exc.status_code}). Try again shortly."
            ) from None
        raise LLMError(f"Anthropic API error ({exc.status_code}): {exc.message}") from None

    if message.stop_reason == "max_tokens":
        raise LLMError(
            "The model's response was cut off before it finished. "
            "Try a shorter policy document."
        )

    text = "".join(block.text for block in message.content if block.type == "text")
    if not text.strip():
        raise LLMError("The model returned an empty response.")
    return text


# Appended to the original request on the single retry. Kept blunt on purpose —
# the failure mode being corrected is the model wrapping JSON in commentary.
_JSON_RETRY_NUDGE = """

IMPORTANT: Your previous response could not be parsed as JSON. Respond with raw
JSON ONLY. No markdown fences, no explanation before or after. Start your
response with the opening bracket or brace and end it with the closing one."""


def complete_json(
    system: str,
    user: str,
    max_tokens: int = 4096,
    model: str = DEFAULT_MODEL,
    demo_key: str | None = None,
) -> Any:
    """Call the model and return parsed JSON, retrying once on a parse failure.

    `demo_key` selects a canned fixture when DEMO_MODE=1, so the whole app runs
    end-to-end with no API key and no network.
    """
    if DEMO_MODE:
        if demo_key is None:
            raise LLMError("DEMO_MODE is on but this call has no fixture available.")
        return fixtures.get(demo_key)

    raw = complete_text(system, user, max_tokens=max_tokens, model=model)
    try:
        return extract_json(raw)
    except JSONParseError:
        pass  # fall through to one corrective retry

    raw = complete_text(
        system,
        user + _JSON_RETRY_NUDGE,
        max_tokens=max_tokens,
        model=model,
    )
    try:
        return extract_json(raw)
    except JSONParseError as exc:
        raise LLMError(
            "The model did not return valid JSON, even after a retry. "
            "This is usually transient — please try again. "
            f"(Details: {exc})"
        ) from None
