"""Drive-style document revisions."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class DocumentRevision(Base):
    __tablename__ = "document_revisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    revision_id: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True, nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    keep_forever: Mapped[bool] = mapped_column(Boolean, default=False)
    export_links_json: Mapped[str] = mapped_column(Text, default="{}")
    title: Mapped[str] = mapped_column(String, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    body_text: Mapped[str] = mapped_column(Text, default="\n")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
