"""Shared Jinja2Templates instance and custom filters.

app/main.py and app/library.py both render templates from the same directory
but are two separate FastAPI routers. Centralising the Jinja environment here
means a filter registered once — section_tag, below — works from both,
instead of the registration call being duplicated in two places and risking
drift.
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

PROJECT_ROOT = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=PROJECT_ROOT / "templates")

_SECTION_TAG_RE = re.compile(r"^(\d+(?:\.\d+)+)\b")


def section_tag(text: str) -> str | None:
    """Pull a leading numbered-clause reference ("3.2") off a policy excerpt,
    for display as a small citation tag next to the quoted text.

    Every excerpt in this app is verbatim policy text (see app/prompts.py's
    grounding rules), and this app's sample policies number their clauses
    ("3.2 The restriction in 3.1 applies..."). Surfacing that number as its
    own tag makes the citation read as a precise reference rather than an
    unattributed quote. Returns None when the excerpt doesn't start with one
    — e.g. the literal string "Not addressed by this policy" — so the
    template can simply omit the tag rather than show a wrong one.
    """
    if not isinstance(text, str):
        return None
    match = _SECTION_TAG_RE.match(text.strip())
    return match.group(1) if match else None


templates.env.filters["section_tag"] = section_tag


def render_error(request: Request, message: str, status_code: int = 400) -> HTMLResponse:
    """Render the shared error page. Was duplicated verbatim in app/main.py
    and app/library.py; centralised here so app/ratelimit.py's exception
    handler can reach it too without a third copy.
    """
    from app import llm  # deferred: avoids a circular import at module load

    return templates.TemplateResponse(
        request=request,
        name="error.html",
        context={"message": message, "demo_mode": llm.DEMO_MODE},
        status_code=status_code,
    )
