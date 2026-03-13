"""Document model."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


def generate_revision_id() -> str:
    """Return an opaque Docs-like revision identifier."""
    return f"AIxLkS{uuid4().hex}{uuid4().hex[:24]}"


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    body_text: Mapped[str] = mapped_column(Text, default="\n")
    text_style_spans_json: Mapped[str] = mapped_column(Text, default="[]")
    paragraph_style_json: Mapped[str] = mapped_column(Text, default="[]")
    named_ranges_json: Mapped[str] = mapped_column(Text, default="[]")
    named_styles_json: Mapped[str] = mapped_column(Text, default="{}")
    document_style_json: Mapped[str] = mapped_column(Text, default="{}")
    revision_id: Mapped[str] = mapped_column(String, default=generate_revision_id)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    trashed: Mapped[bool] = mapped_column(Boolean, default=False)

    user = relationship("User", back_populates="documents")
    permissions = relationship(
        "DocumentPermission",
        back_populates="document",
        cascade="all, delete-orphan",
    )
