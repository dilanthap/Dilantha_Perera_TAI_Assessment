"""Chunking, embedding and retrieval for the policy library.

WHY THIS MODULE EXISTS SEPARATELY FROM app/generation.py
==========================================================
app/generation.py argues, correctly, that a single few-page policy should NOT
be chunked and retrieved — the whole document fits in context, and chunking
would cost citation fidelity and cross-section reasoning for no real benefit.

That argument stops holding once the source material is a *library*: several
policies, or documents long enough that re-sending all of them on every
question is neither cheap nor reliable. That is the scale this module is for.
It is the "real chunked RAG for large policy libraries" item from the README's
roadmap, built out rather than left as a bullet point.

Three things this module is careful about, because they are exactly the
failure modes a naive chunk-and-embed implementation falls into:

  1. SECTION-AWARE CHUNKING. Splitting happens at heading boundaries first
     (falling back to paragraph-aligned size splitting only when a section is
     too long), so a chunk's heading and body stay attached. A citation that
     quotes a clause severed from the heading that qualifies it is worse than
     no citation.
  2. RETRIEVAL-CONFIDENCE SIGNAL. Every retrieved chunk carries its cosine
     similarity score, and the model is told explicitly that a low score means
     "the search didn't find a match", not "the policy is silent". Those two
     things look identical to a model unless something tells it otherwise —
     see LIBRARY_ANSWER_SYSTEM in app/prompts.py.
  3. DEMO_MODE PARITY. Chunking runs for real in demo mode (it's pure local
     text processing) so the library UI is genuinely demoable, but embedding
     and answering are network calls and are skipped in favour of a canned
     fixture, exactly like app/llm.py does for the quiz loop.
"""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass

import voyageai
from voyageai.error import (
    APIConnectionError,
    AuthenticationError,
    RateLimitError,
    Timeout,
    VoyageError,
)

from app import fixtures, prompts
from app.llm import DEMO_MODE, LLMError, complete_json
from app.models import LibraryChunk, LibraryDocument

# --------------------------------------------------------------------------
# Embedding model selection
# --------------------------------------------------------------------------
#
# voyage-law-2 is Voyage AI's domain-tuned model for legal / compliance /
# regulatory text — the closest fit for AI-usage policies, which read like
# short compliance documents rather than general prose.
#
# >>> SWAP POINT <<<
# For a library that mixes policy documents with general business material,
# the domain tuning stops being a clear win. Swap to a general-purpose model:
#     EMBEDDING_MODEL = "voyage-4"        # current general-purpose flagship
#     EMBEDDING_MODEL = "voyage-4-lite"   # cheaper, faster, still strong
EMBEDDING_MODEL = "voyage-law-2"

_client: voyageai.Client | None = None


def get_embedding_client() -> voyageai.Client:
    """Build the Voyage client lazily, mirroring app/llm.py's get_client()."""
    global _client
    if _client is None:
        api_key = os.getenv("VOYAGE_API_KEY")
        if not api_key:
            raise LLMError(
                "VOYAGE_API_KEY is not set. Copy .env.example to .env and add a "
                "Voyage AI key to use the policy library, or set DEMO_MODE=1 to "
                "try it with sample data and no key."
            )
        _client = voyageai.Client(api_key=api_key)
    return _client


def embed_texts(texts: list[str], input_type: str) -> list[list[float]]:
    """Embed `texts`. `input_type` is "document" when storing chunks, "query"
    when embedding a question — Voyage prepends a different instruction
    internally for each, which measurably improves retrieval quality.
    """
    client = get_embedding_client()
    try:
        result = client.embed(texts, model=EMBEDDING_MODEL, input_type=input_type)
    except AuthenticationError:
        raise LLMError(
            "Voyage AI rejected the API key. Check VOYAGE_API_KEY in your .env file."
        ) from None
    except RateLimitError:
        raise LLMError(
            "Rate limited by the Voyage AI API. Wait a moment and try again."
        ) from None
    except Timeout:
        raise LLMError(
            "The embedding request timed out. This is usually transient — try again."
        ) from None
    except APIConnectionError:
        raise LLMError(
            "Could not reach the Voyage AI API. Check your network connection."
        ) from None
    except VoyageError as exc:
        raise LLMError(f"Voyage AI error: {exc}") from None
    return result.embeddings


# --------------------------------------------------------------------------
# Chunking — section-aware, with a size-based fallback
# --------------------------------------------------------------------------

# Soft ceiling on chunk size. Small enough that a chunk reads as one coherent
# citation; large enough that it usually holds a whole numbered subsection.
# ~350-400 tokens at typical English density.
_MAX_CHUNK_CHARS = 1500

_SECTION_RE = re.compile(r"(?m)^##\s+(.+)$")
_SUBSECTION_RE = re.compile(r"(?m)^(\d+\.\d+)\s")


@dataclass
class Chunk:
    heading: str
    text: str


def chunk_document(raw_text: str) -> list[Chunk]:
    """Split a document into retrievable chunks, preferring section boundaries.

    Primary split is on `## Heading` lines, which is how the sample policy and
    most markdown-authored policies are structured. A section that is still
    too long after that gets sub-split on numbered subsections ("2.3 ..."),
    and anything left over falls back to paragraph-aligned size splitting.
    Whichever level a chunk stops at, its heading travels with it — that's the
    property that keeps citations self-contained.
    """
    text = raw_text.strip()
    matches = list(_SECTION_RE.finditer(text))

    if not matches:
        return _split_by_size(text, heading="")

    chunks: list[Chunk] = []

    # Anything before the first "## " heading — title, policy reference,
    # version, owner — is real content (often exactly what a "what version is
    # this policy" question needs) and must not be silently dropped just
    # because it precedes the first heading match.
    preamble = text[: matches[0].start()].strip()
    if preamble:
        chunks.append(Chunk(heading="(front matter)", text=preamble))

    for i, m in enumerate(matches):
        heading = m.group(1).strip()
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()

        if len(body) <= _MAX_CHUNK_CHARS:
            chunks.append(Chunk(heading=heading, text=body))
        else:
            chunks.extend(_split_by_subsection(heading, body))

    return chunks


def _split_by_subsection(heading: str, body: str) -> list[Chunk]:
    """Sub-split an over-long section on "N.M " numbered subsections."""
    sub_matches = list(_SUBSECTION_RE.finditer(body))
    if len(sub_matches) < 2:
        return _split_by_size(body, heading)

    chunks: list[Chunk] = []
    for i, m in enumerate(sub_matches):
        start = m.start()
        end = sub_matches[i + 1].start() if i + 1 < len(sub_matches) else len(body)
        sub_body = body[start:end].strip()
        if sub_body:
            chunks.append(Chunk(heading=heading, text=sub_body))
    return chunks


def _split_by_size(text: str, heading: str) -> list[Chunk]:
    """Last-resort split: pack whole paragraphs up to _MAX_CHUNK_CHARS.

    Never splits mid-paragraph, so a sentence is never torn across chunks.
    """
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[Chunk] = []
    buffer = ""

    for para in paragraphs:
        if buffer and len(buffer) + len(para) + 2 > _MAX_CHUNK_CHARS:
            chunks.append(Chunk(heading=heading, text=buffer.strip()))
            buffer = para
        else:
            buffer = f"{buffer}\n\n{para}" if buffer else para

    if buffer:
        chunks.append(Chunk(heading=heading, text=buffer.strip()))

    return chunks or [Chunk(heading=heading, text=text)]


# --------------------------------------------------------------------------
# Ingestion — chunk, embed, persist
# --------------------------------------------------------------------------


def ingest_document(db, title: str, raw_text: str) -> LibraryDocument:
    """Chunk `raw_text`, embed the chunks (unless DEMO_MODE), and persist both.

    Caller owns the transaction boundary in the rest of this app's routes, but
    ingestion commits itself: a failed embedding call after a partial chunk
    write should not leave the document half-persisted, and there is no
    multi-step flow here for a caller to compose with (unlike generate_scenarios,
    which returns unsaved rows for main.py to add alongside the Policy row).
    """
    document = LibraryDocument(title=title, raw_text=raw_text)
    db.add(document)
    db.flush()  # assigns document.id without committing

    chunks = chunk_document(raw_text)
    if not chunks:
        db.rollback()
        raise LLMError("Could not split this document into any sections.")

    if DEMO_MODE:
        vectors: list[list[float] | None] = [None] * len(chunks)
    else:
        vectors = embed_texts([c.text for c in chunks], input_type="document")

    for i, (chunk, vector) in enumerate(zip(chunks, vectors)):
        db.add(
            LibraryChunk(
                document_id=document.id,
                chunk_index=i,
                heading=(chunk.heading or "(preamble)")[:255],
                chunk_text=chunk.text,
                embedding=json.dumps(vector) if vector is not None else None,
            )
        )

    db.commit()
    db.refresh(document)
    return document


# --------------------------------------------------------------------------
# Retrieval
# --------------------------------------------------------------------------

TOP_K = 5

# Heuristic thresholds on cosine similarity, not a validated constant — tune
# against real query traffic the way _ALIGNED_FLOOR / _PARTIAL_FLOOR in
# app/scoring.py are tuned against real answers. For Voyage embeddings, a
# genuinely relevant chunk in the same domain typically lands ~0.4-0.7;
# below ~0.3 is usually noise.
_HIGH_CONFIDENCE_FLOOR = 0.5
_LOW_CONFIDENCE_FLOOR = 0.3


@dataclass
class RetrievedChunk:
    document_title: str
    heading: str
    text: str
    score: float


def retrieve_chunks(db, question: str, k: int = TOP_K) -> list[RetrievedChunk]:
    """Embed `question` and return the top-`k` chunks by cosine similarity.

    A plain linear scan over Python floats — no vector index. At library
    scale (a handful of documents, at most a few dozen chunks) this is fast
    enough, and it keeps the prototype to zero extra infrastructure. See the
    module docstring in app/database.py for the same reasoning applied to
    choosing SQLite over Postgres.
    """
    rows = (
        db.query(LibraryChunk)
        .filter(LibraryChunk.embedding.isnot(None))
        .all()
    )
    if not rows:
        raise LLMError(
            "The policy library has no embedded documents yet. Add one first — "
            "if you added a document while DEMO_MODE was on, it was chunked but "
            "not embedded; re-add it with a live Voyage AI key."
        )

    query_vector = embed_texts([question], input_type="query")[0]

    scored = [
        (_cosine_similarity(query_vector, json.loads(row.embedding)), row)
        for row in rows
    ]
    scored.sort(key=lambda pair: pair[0], reverse=True)

    return [
        RetrievedChunk(
            document_title=row.document.title,
            heading=row.heading,
            text=row.chunk_text,
            score=score,
        )
        for score, row in scored[:k]
    ]


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def confidence_label(top_score: float) -> str:
    """Map a top similarity score to a label the UI and prompt both use."""
    if top_score >= _HIGH_CONFIDENCE_FLOOR:
        return "high"
    if top_score >= _LOW_CONFIDENCE_FLOOR:
        return "low"
    return "none"


# --------------------------------------------------------------------------
# Answering
# --------------------------------------------------------------------------

_ANSWER_MAX_TOKENS = 1024


def answer_library_question(db, question: str) -> dict:
    """Retrieve, then ask the model to answer strictly from what was retrieved.

    Returns a dict with: answer, addressed, citations, retrieved, confidence,
    top_score. In DEMO_MODE this is a canned fixture and never touches the
    network — retrieval and generation are both live-API steps.
    """
    if DEMO_MODE:
        return fixtures.get("library_answer")

    retrieved = retrieve_chunks(db, question, k=TOP_K)
    payload = complete_json(
        system=prompts.LIBRARY_ANSWER_SYSTEM,
        user=prompts.library_answer_user(question, retrieved),
        max_tokens=_ANSWER_MAX_TOKENS,
    )
    return _coerce_library_answer(payload, retrieved)


def _coerce_library_answer(payload: object, retrieved: list[RetrievedChunk]) -> dict:
    """Validate the model's response, same discipline as app/scoring.py:
    every field is untrusted until coerced into a safe shape.
    """
    if not isinstance(payload, dict):
        raise LLMError(
            "The model returned an unexpected format for the library answer "
            f"(expected an object, got {type(payload).__name__}). Please try again."
        )

    answer = payload.get("answer")
    answer = answer.strip() if isinstance(answer, str) else ""
    if not answer:
        raise LLMError("The model returned an empty answer. Please try again.")

    addressed = bool(payload.get("addressed"))

    citations: list[dict[str, str]] = []
    raw_citations = payload.get("citations")
    if isinstance(raw_citations, list):
        for item in raw_citations:
            if not isinstance(item, dict):
                continue
            doc = item.get("document")
            excerpt = item.get("excerpt")
            if (
                isinstance(doc, str)
                and isinstance(excerpt, str)
                and doc.strip()
                and excerpt.strip()
            ):
                citations.append({"document": doc.strip(), "excerpt": excerpt.strip()})

    top_score = retrieved[0].score if retrieved else 0.0

    return {
        "answer": answer,
        "addressed": addressed,
        "citations": citations,
        "retrieved": retrieved,
        "confidence": confidence_label(top_score),
        "top_score": top_score,
    }
