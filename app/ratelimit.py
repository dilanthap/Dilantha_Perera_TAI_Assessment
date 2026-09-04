"""Rate limiting for the routes that call a paid API, via Upstash Redis.

Every route this guards makes a real, billed call to Anthropic or Voyage.
Deployed publicly with no limit, one script, one bad retry loop, or one bored
visitor can run up real charges with no ceiling — see the README's Vercel
deployment section for the full reasoning.

Upstash Redis, not an in-process counter: Vercel serverless functions are
stateless between invocations, so a plain in-memory counter resets on every
cold start (or is simply a different counter on a different concurrent
instance) and would not actually stop sustained abuse. Redis gives every
instance the same shared counter.

Gracefully disabled when Upstash isn't configured — local dev and any
deployment that hasn't set it up yet run with no rate limiting at all,
the same DEMO_MODE-style "missing config degrades, it doesn't crash"
pattern used everywhere else in this app (see app/llm.py, app/rag.py).
"""

from __future__ import annotations

import os

from fastapi import Request

# >>> SWAP POINT <<<
# 10 requests/minute/visitor is a starting guess sized for "a reviewer
# clicking through the demo a few times", not measured traffic. Tighten it
# if the deployment is getting hit harder than expected, loosen it if real
# visitors are legitimately bumping into it.
_LIMIT_MAX_REQUESTS = 10
_LIMIT_WINDOW_SECONDS = 60

_configured = bool(
    os.getenv("UPSTASH_REDIS_REST_URL") and os.getenv("UPSTASH_REDIS_REST_TOKEN")
)

_ratelimit = None
if _configured:
    from upstash_ratelimit import Ratelimit, SlidingWindow
    from upstash_redis import Redis

    _ratelimit = Ratelimit(
        redis=Redis.from_env(),
        limiter=SlidingWindow(max_requests=_LIMIT_MAX_REQUESTS, window=_LIMIT_WINDOW_SECONDS),
    )


class RateLimitExceeded(Exception):
    """Raised by `enforce` when a caller is over the limit.

    Caught by the exception handler registered in app/main.py, which renders
    the same error.html every other failure in this app renders — a rate
    limit hit is a normal, expected outcome here, not a crash.
    """

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def _client_ip(request: Request) -> str:
    """Best-effort real client IP behind Vercel's proxy.

    Vercel terminates TLS at the edge and forwards the original client IP in
    X-Forwarded-For (first entry in the list); request.client.host would
    otherwise resolve to Vercel's own edge, which is the same for every
    visitor and would make the whole limit one shared bucket.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def enforce(request: Request) -> None:
    """FastAPI dependency — add to any route that calls a paid API.

    Raises RateLimitExceeded (caught globally, rendered as error.html) when
    the caller is over the limit. Does nothing at all — not even a Redis
    call — when Upstash isn't configured.
    """
    if _ratelimit is None:
        return

    result = _ratelimit.limit(_client_ip(request))
    if not result.allowed:
        raise RateLimitExceeded(
            "Too many requests. This action is limited to "
            f"{_LIMIT_MAX_REQUESTS} per {_LIMIT_WINDOW_SECONDS} seconds per "
            "visitor, since each one calls a paid API. Wait a moment and try "
            "again."
        )


__all__ = ["enforce", "RateLimitExceeded"]
