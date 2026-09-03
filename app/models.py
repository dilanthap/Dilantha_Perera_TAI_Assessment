"""ORM models: Policy -> Scenario -> Response, plus the policy-library RAG tables.

Two independent chains, deliberately not sharing a table:

  - Policy -> Scenario -> Response is the quiz loop. A Policy's full text is
    sent to the model on every call — see app/generation.py for why that is
    the right choice at this scale.
  - LibraryDocument -> LibraryChunk is the policy-library extension. It exists
    for the opposite scale: a corpus too large to put in context on every
    question, answered instead from retrieved excerpts — see app/rag.py.
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Policy(Base):
    """An uploaded AI-usage policy document — the single source of truth."""

    __tablename__ = "policies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    scenarios: Mapped[list["Scenario"]] = relationship(
        back_populates="policy",
        cascade="all, delete-orphan",
        order_by="Scenario.id",
    )


class Scenario(Base):
    """A generated workplace dilemma, plus the policy excerpt it is grounded in.

    `relevant_policy_excerpt` is what makes scoring citable: it is verbatim text
    the model identified from the uploaded policy, carried forward into the
    scoring call so feedback can point at a real section instead of a vague rule.
    """

    __tablename__ = "scenarios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    policy_id: Mapped[int] = mapped_column(
        ForeignKey("policies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    relevant_policy_excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    policy: Mapped["Policy"] = relationship(back_populates="scenarios")
    responses: Mapped[list["Response"]] = relationship(
        back_populates="scenario",
        cascade="all, delete-orphan",
        order_by="Response.id",
    )


class Response(Base):
    """One employee answer and the graded assessment of its reasoning."""

    __tablename__ = "responses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scenario_id: Mapped[int] = mapped_column(
        ForeignKey("scenarios.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_answer: Mapped[str] = mapped_column(Text, nullable=False)

    # 0-100. Scores the QUALITY OF REASONING, not just whether the conclusion
    # happened to be right — see app/prompts.py SCORING_SYSTEM.
    reasoning_score: Mapped[int] = mapped_column(Integer, nullable=False)
    verdict: Mapped[str] = mapped_column(String(32), nullable=False)
    feedback_text: Mapped[str] = mapped_column(Text, nullable=False)
    cited_policy_section: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    scenario: Mapped["Scenario"] = relationship(back_populates="responses")


class LibraryDocument(Base):
    """One document in the policy library — the RAG extension's source unit.

    Unlike `Policy`, a LibraryDocument's raw_text is never sent to the model
    directly. It exists to be chunked and embedded; `chunks` is what retrieval
    actually searches.
    """

    __tablename__ = "library_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    chunks: Mapped[list["LibraryChunk"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="LibraryChunk.chunk_index",
    )


class LibraryChunk(Base):
    """One retrievable slice of a library document, plus its embedding.

    `embedding` is a JSON-encoded list of floats — SQLite has no native vector
    type, and the library is small enough (a handful of documents, at most a
    few dozen chunks) that a linear cosine-similarity scan in Python is fast
    enough; see `app/rag.py`. It is NULL when the document was ingested in
    DEMO_MODE, or if an embedding call ever failed after the chunk was written
    — retrieval skips chunks with no embedding rather than crashing on them.
    """

    __tablename__ = "library_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("library_documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    heading: Mapped[str] = mapped_column(String(255), nullable=False)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[str | None] = mapped_column(Text, nullable=True)

    document: Mapped["LibraryDocument"] = relationship(back_populates="chunks")
