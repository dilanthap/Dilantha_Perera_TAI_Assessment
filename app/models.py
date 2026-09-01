"""ORM models: Policy -> Scenario -> Response.

Three tables, one chain. A Policy is the uploaded source document; Scenarios are
generated from it; a Response is one employee's answer to one Scenario plus the
model's assessment of their reasoning.
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
