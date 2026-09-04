"""FastAPI application: upload -> generate -> answer -> score -> results.

One loop, five routes. Every route that can touch the model catches LLMError and
renders a clean error page — a failed API call must never reach the user as a
stack trace.

The policy-library routes (retrieval-grounded Q&A across a document corpus)
live in app/library.py and are mounted below via include_router — a separate
module because it is a genuinely different mode (see app/rag.py), not a
variation on this one.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from app import llm
from app.database import get_db, init_db
from app.generation import generate_scenarios
from app.library import router as library_router
from app.models import Policy, Response as ResponseModel, Scenario
from app.ratelimit import RateLimitExceeded, enforce
from app.scoring import score_answer
from app.templating import render_error, templates
from app.text_utils import extract_upload_text, title_from_filename

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_POLICY_PATH = PROJECT_ROOT / "samples" / "northwind_ai_policy.md"

MAX_UPLOAD_BYTES = 1_000_000  # ~1MB; policies are pages, not books


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="AI Policy & Risk Simulator", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=PROJECT_ROOT / "static"), name="static")
app.include_router(library_router)


@app.exception_handler(RateLimitExceeded)
async def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded):
    """Registered once, on the top-level app — applies to library.py's
    routes too, since Starlette's exception handling wraps every included
    router, not just routes declared directly on `app`.
    """
    return _error(request, exc.message, status_code=429)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _error(request: Request, message: str, status_code: int = 400) -> HTMLResponse:
    """Render the friendly error page."""
    return render_error(request, message, status_code)


def _latest_responses(scenarios: list[Scenario]) -> dict[int, ResponseModel]:
    """Map scenario_id -> most recent Response.

    Every submission is kept rather than overwritten (an audit trail is the point
    for compliance clients), so the UI reads the latest one per scenario.
    """
    return {s.id: s.responses[-1] for s in scenarios if s.responses}


def _get_policy_or_none(db: Session, policy_id: int) -> Policy | None:
    return db.get(Policy, policy_id)


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
def upload_page(request: Request, sample: int = 0):
    """Upload form. `?sample=1` pre-fills the textarea with the bundled policy.

    Pre-filling server-side keeps the demo path working with zero JavaScript.
    """
    prefill_text = ""
    prefill_title = ""
    if sample:
        try:
            prefill_text = SAMPLE_POLICY_PATH.read_text(encoding="utf-8")
            prefill_title = "Northwind Financial Partners — Acceptable Use of AI"
        except OSError:
            prefill_text = ""

    return templates.TemplateResponse(
        request=request,
        name="upload.html",
        context={
            "prefill_text": prefill_text,
            "prefill_title": prefill_title,
            "demo_mode": llm.DEMO_MODE,
        },
    )


@app.post("/upload", dependencies=[Depends(enforce)])
async def upload_policy(
    request: Request,
    title: str = Form(""),
    policy_text: str = Form(""),
    policy_file: UploadFile | None = None,
    db: Session = Depends(get_db),
):
    """Store the policy, generate scenarios, and send the user into the quiz."""
    text = (policy_text or "").strip()
    fallback_title = "Untitled AI-Usage Policy"

    # An uploaded file wins over the textarea if both are supplied.
    if policy_file is not None and policy_file.filename:
        raw = await policy_file.read()
        if len(raw) > MAX_UPLOAD_BYTES:
            return _error(
                request,
                "That file is larger than 1MB. This tool is designed for policy "
                "documents of a few pages.",
            )
        try:
            text = extract_upload_text(policy_file, raw)
        except ValueError as exc:
            return _error(request, str(exc))
        fallback_title = title_from_filename(policy_file.filename, fallback_title)

    if not text:
        return _error(
            request,
            "No policy text was provided. Paste your policy into the box or "
            "upload a file — or load the sample policy to try the tool.",
        )

    if len(text) < 200:
        return _error(
            request,
            "That policy looks too short to generate meaningful scenarios from. "
            "Please provide the full policy document.",
        )

    clean_title = (title or "").strip() or fallback_title

    policy = Policy(title=clean_title[:255], raw_text=text)
    db.add(policy)
    db.commit()
    db.refresh(policy)

    try:
        scenarios = generate_scenarios(policy)
    except llm.LLMError as exc:
        # The policy row is kept — the document is still valid, only generation
        # failed, and keeping it means a retry doesn't lose the upload.
        return _error(request, str(exc), status_code=502)

    db.add_all(scenarios)
    db.commit()

    return RedirectResponse(url=f"/policy/{policy.id}/quiz?n=0", status_code=303)


@app.get("/policy/{policy_id}/quiz", response_class=HTMLResponse)
def quiz(request: Request, policy_id: int, n: int = 0, db: Session = Depends(get_db)):
    """Present one scenario at a time."""
    policy = _get_policy_or_none(db, policy_id)
    if policy is None:
        return _error(request, "That policy could not be found.", status_code=404)

    scenarios = policy.scenarios
    if not scenarios:
        return _error(
            request,
            "No scenarios exist for this policy. Please upload it again.",
            status_code=404,
        )

    if n < 0:
        n = 0
    if n >= len(scenarios):
        return RedirectResponse(url=f"/policy/{policy_id}/results", status_code=303)

    answered = _latest_responses(scenarios)

    return templates.TemplateResponse(
        request=request,
        name="quiz.html",
        context={
            "policy": policy,
            "scenario": scenarios[n],
            "index": n,
            "total": len(scenarios),
            "answered_ids": set(answered.keys()),
            "scenario_ids": [s.id for s in scenarios],
            "demo_mode": llm.DEMO_MODE,
        },
    )


@app.post("/policy/{policy_id}/answer", dependencies=[Depends(enforce)])
def submit_answer(
    request: Request,
    policy_id: int,
    scenario_id: int = Form(...),
    user_answer: str = Form(""),
    n: int = Form(0),
    db: Session = Depends(get_db),
):
    """Score one answer, persist it, and advance."""
    policy = _get_policy_or_none(db, policy_id)
    if policy is None:
        return _error(request, "That policy could not be found.", status_code=404)

    scenario = db.get(Scenario, scenario_id)
    if scenario is None or scenario.policy_id != policy_id:
        return _error(
            request, "That scenario could not be found.", status_code=404
        )

    answer = (user_answer or "").strip()
    if not answer:
        return _error(
            request,
            "Please write an answer explaining what you would do and why, then "
            "submit again.",
        )

    try:
        assessment = score_answer(scenario, answer)
    except llm.LLMError as exc:
        return _error(request, str(exc), status_code=502)

    db.add(
        ResponseModel(
            scenario_id=scenario.id,
            user_answer=answer,
            reasoning_score=assessment["reasoning_score"],
            verdict=assessment["verdict"],
            feedback_text=assessment["feedback_text"],
            cited_policy_section=assessment["cited_policy_section"],
        )
    )
    db.commit()

    return RedirectResponse(url=f"/policy/{policy_id}/quiz?n={n + 1}", status_code=303)


@app.post("/policy/{policy_id}/delete")
def delete_policy(request: Request, policy_id: int, db: Session = Depends(get_db)):
    """Remove a policy and everything generated from it.

    Cascades to its Scenarios and their Responses via the ORM relationships
    in models.py (cascade="all, delete-orphan"), the same pattern used for
    the library's delete_document in app/library.py.
    """
    policy = _get_policy_or_none(db, policy_id)
    if policy is None:
        return _error(request, "That policy could not be found.", status_code=404)

    db.delete(policy)
    db.commit()

    return RedirectResponse(url="/", status_code=303)


@app.get("/policy/{policy_id}/results", response_class=HTMLResponse)
def results(request: Request, policy_id: int, db: Session = Depends(get_db)):
    """Per-scenario assessment plus an overall reasoning score."""
    policy = _get_policy_or_none(db, policy_id)
    if policy is None:
        return _error(request, "That policy could not be found.", status_code=404)

    scenarios = policy.scenarios
    latest = _latest_responses(scenarios)

    rows = [(scenario, latest.get(scenario.id)) for scenario in scenarios]
    scored = [resp for _, resp in rows if resp is not None]

    overall = round(sum(r.reasoning_score for r in scored) / len(scored)) if scored else None

    if overall is None:
        overall_verdict = None
    elif overall >= 75:
        overall_verdict = "aligned"
    elif overall >= 40:
        overall_verdict = "partially aligned"
    else:
        overall_verdict = "misaligned"

    return templates.TemplateResponse(
        request=request,
        name="results.html",
        context={
            "policy": policy,
            "rows": rows,
            "overall": overall,
            "overall_verdict": overall_verdict,
            "answered_count": len(scored),
            "total": len(scenarios),
            "demo_mode": llm.DEMO_MODE,
        },
    )
