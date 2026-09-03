"""Canned responses for DEMO_MODE=1 — no API key, no network.

Why this exists: a reviewer should be able to clone the repo and see the whole
loop work in under a minute, before deciding whether to spend an API key on it.
It also let this app's UI, routing and persistence be tested independently of
model behaviour, which is the only way to tell a template bug apart from a
prompt bug.

The payloads below are shaped exactly like real model output and are grounded in
samples/northwind_ai_policy.md, so the demo reads coherently. Scoring fixtures
rotate so a walkthrough shows a range of verdicts rather than the same card
four times.

This module is NOT part of the live path. In normal operation nothing here is
imported into a response.
"""

from itertools import cycle
from typing import Any

# --------------------------------------------------------------------------
# Scenario generation fixture — grounded in the Northwind sample policy.
# --------------------------------------------------------------------------

_SCENARIOS: list[dict[str, str]] = [
    {
        "prompt_text": (
            "You are a research analyst with a client committee pack due tomorrow "
            "morning. A colleague forwards you a link to a new AI research "
            "summarisation tool that is dramatically better than anything on the "
            "Approved AI Tools Register, and offers a 14-day free trial. You plan "
            "to use it only on published market research — no client material at "
            "all — and to submit it for review after the deadline passes. "
            "What would you do, and why?"
        ),
        "relevant_policy_excerpt": (
            "2.3 Requests to add a tool to the Register must be submitted to the "
            "CISO's office for security and vendor review. Trialling an unapproved "
            "tool with Firm data before that review concludes is a breach of this "
            "policy, irrespective of whether the trial is described as a pilot, an "
            "evaluation, or a proof of concept."
        ),
    },
    {
        "prompt_text": (
            "You need to summarise a 60-page set of client filings and you are "
            "three hours from a deadline. You intend to use an approved, "
            "enterprise-provisioned AI tool that your team lead confirmed is on "
            "the Register and covered by the Firm's data-protection terms. You are "
            "about to paste the filings in as they are. What would you do, and why?"
        ),
        "relevant_policy_excerpt": (
            "3.2 The restriction in 3.1 applies regardless of the tool's stated "
            "data-retention or training policy, and regardless of whether the tool "
            "is on the Approved AI Tools Register. Approval of a tool addresses "
            "vendor security posture; it does not grant permission to disclose "
            "client data to that vendor."
        ),
    },
    {
        "prompt_text": (
            "You have used an approved AI tool to draft a market commentary "
            "section for a client report. The draft is good, and it cites three "
            "specific figures on sector performance along with the sources they "
            "came from. You are confident in the tool's track record and the "
            "report is running late. What would you do before this goes out, and why?"
        ),
        "relevant_policy_excerpt": (
            "5.2 Where AI-generated material contains factual assertions, figures, "
            "citations, or references to source documents, the responsible employee "
            "must verify each against the underlying source before relying on it."
        ),
    },
    {
        "prompt_text": (
            "Late on a Friday you realise that earlier in the week you pasted a "
            "spreadsheet extract into an AI tool to reformat it, and that the "
            "extract included a column of client account numbers you had not "
            "noticed. Nothing appears to have gone wrong, the chat has been "
            "deleted, and reporting it will be awkward. What would you do, and why?"
        ),
        "relevant_policy_excerpt": (
            "7.1 Any actual or suspected disclosure of client or confidential "
            "information to an AI tool must be reported to the CISO's office within "
            "24 hours of discovery. 7.2 Reports made promptly and in good faith "
            "under 7.1 will not by themselves result in disciplinary action. "
            "Failure to report a known incident will."
        ),
    },
]


# --------------------------------------------------------------------------
# Scoring fixtures — rotate to show a realistic spread of assessments.
# --------------------------------------------------------------------------

_SCORINGS: list[dict[str, Any]] = [
    {
        "reasoning_score": 88,
        "verdict": "aligned",
        "feedback_text": (
            "You correctly identified that the approval requirement in 2.3 attaches "
            "to the tool itself, not to the sensitivity of the data you intend to "
            "put in it — that is the distinction most people miss here, and you got "
            "it. You also weighed the real deadline pressure rather than pretending "
            "it away, and landed on a concrete step: use a Register tool now, submit "
            "the new one for review after. The one thing that would strengthen this "
            "is naming who you would notify, since 7.3 asks you to consult the "
            "CISO's office before proceeding rather than after."
        ),
        "cited_policy_section": (
            "2.3 Requests to add a tool to the Register must be submitted to the "
            "CISO's office for security and vendor review. Trialling an unapproved "
            "tool with Firm data before that review concludes is a breach of this "
            "policy, irrespective of whether the trial is described as a pilot, an "
            "evaluation, or a proof of concept."
        ),
    },
    {
        "reasoning_score": 58,
        "verdict": "partially aligned",
        "feedback_text": (
            "Your conclusion — don't paste the filings in as they are — is right, "
            "and you were right that client data is the issue. But your reasoning "
            "rests on the tool being risky, and that is not what the policy says. "
            "Section 3.2 is explicit that approval of a tool does not grant "
            "permission to disclose client data to it, so the restriction would "
            "still apply even if the tool were perfectly secure. Reasoning from "
            "vendor trust rather than from 3.2 will lead you wrong the next time a "
            "tool looks trustworthy. The step you are missing is 3.3: anonymise the "
            "filings first, then process them with the approved tool."
        ),
        "cited_policy_section": (
            "3.2 The restriction in 3.1 applies regardless of the tool's stated "
            "data-retention or training policy, and regardless of whether the tool "
            "is on the Approved AI Tools Register. Approval of a tool addresses "
            "vendor security posture; it does not grant permission to disclose "
            "client data to that vendor."
        ),
    },
    {
        "reasoning_score": 31,
        "verdict": "misaligned",
        "feedback_text": (
            "You recognised that the commentary needs some form of check before it "
            "goes out, which is the right instinct. But treating your own read-"
            "through as sufficient does not meet 5.2, which requires each figure and "
            "citation to be verified against the underlying source — not reviewed "
            "for plausibility. You also described the tool's track record as a "
            "reason to trust the numbers; 5.1 places accountability on you "
            "regardless, and states that 'the model produced it' is not a defence. "
            "Open the three sources and check the three figures."
        ),
        "cited_policy_section": (
            "5.2 Where AI-generated material contains factual assertions, figures, "
            "citations, or references to source documents, the responsible employee "
            "must verify each against the underlying source before relying on it."
        ),
    },
    {
        "reasoning_score": 79,
        "verdict": "aligned",
        "feedback_text": (
            "You reached the right conclusion and, importantly, you engaged with the "
            "reason people don't report these — the awkwardness — rather than "
            "skipping past it. You correctly read 7.2 as protecting a prompt "
            "good-faith report. Two gaps: you treated the deleted chat as reducing "
            "the obligation, but 7.1 is triggered by the disclosure itself, not by "
            "whether traces remain; and you did not note the 24-hour clock, which "
            "started when you discovered it, not when it happened."
        ),
        "cited_policy_section": (
            "7.1 Any actual or suspected disclosure of client or confidential "
            "information to an AI tool must be reported to the CISO's office within "
            "24 hours of discovery."
        ),
    },
]

_scoring_cycle = cycle(_SCORINGS)


# --------------------------------------------------------------------------
# Policy library answer fixture — shaped exactly like the dict
# app/rag.py:_coerce_library_answer returns, so library.html renders it
# identically whether it came from the model or from here. "retrieved" uses
# plain dicts rather than RetrievedChunk instances: Jinja2 attribute access
# (`r.document_title`) falls back to item lookup on a dict automatically, so
# the template needs no branch for demo vs. live data.
# --------------------------------------------------------------------------

_LIBRARY_ANSWER: dict[str, Any] = {
    "answer": (
        "Not as-is, even though the tool is on the Approved AI Tools Register. "
        "Register approval addresses the vendor's security posture — it does "
        "not grant permission to disclose client data to that vendor, and that "
        "restriction applies regardless of which tool you use (3.2). Before "
        "using an approved tool on the filings, remove or replace every "
        "identifying detail so the remaining text could not be attributed to a "
        "specific client; the anonymised version may then go through the "
        "approved tool (3.3)."
    ),
    "addressed": True,
    "citations": [
        {
            "document": "Northwind Financial Partners — Acceptable Use of Artificial Intelligence",
            "excerpt": (
                "3.2 The restriction in 3.1 applies regardless of the tool's "
                "stated data-retention or training policy, and regardless of "
                "whether the tool is on the Approved AI Tools Register. Approval "
                "of a tool addresses vendor security posture; it does not grant "
                "permission to disclose client data to that vendor."
            ),
        },
        {
            "document": "Northwind Financial Partners — Acceptable Use of Artificial Intelligence",
            "excerpt": (
                "3.3 Where an AI tool would be genuinely useful for a task "
                "involving client material, personnel must first remove or "
                "replace all identifying details so that the remaining text "
                "could not be attributed to a specific client. Anonymised "
                "material may then be processed using an approved tool."
            ),
        },
    ],
    "retrieved": [
        {
            "document_title": "Northwind Financial Partners — Acceptable Use of Artificial Intelligence",
            "heading": "3. Client and Confidential Data",
            "text": (
                "3.2 The restriction in 3.1 applies regardless of the tool's "
                "stated data-retention or training policy... 3.3 Where an AI "
                "tool would be genuinely useful for a task involving client "
                "material, personnel must first remove or replace all "
                "identifying details..."
            ),
            "score": 0.61,
        },
        {
            "document_title": "Northwind Financial Partners — Acceptable Use of Artificial Intelligence",
            "heading": "2. Approved Tools",
            "text": (
                "2.1 Personnel may use only AI tools listed on the Approved AI "
                "Tools Register... 2.2 Approved tools are provisioned through "
                "the Firm's enterprise accounts with single sign-on."
            ),
            "score": 0.34,
        },
    ],
    "confidence": "high",
    "top_score": 0.61,
}


def get(key: str) -> Any:
    """Return a canned payload for `key`, shaped like real model output."""
    if key == "scenarios":
        # Copy so a caller mutating the result can't corrupt later demo runs.
        return [dict(item) for item in _SCENARIOS]
    if key == "scoring":
        return dict(next(_scoring_cycle))
    if key == "library_answer":
        return dict(_LIBRARY_ANSWER)
    raise KeyError(f"No demo fixture registered for {key!r}")
