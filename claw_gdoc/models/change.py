"""Drive-style change records for tracking per-user document mutations."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class ChangeRecord(Base):
    __tablename__ = "change_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    change_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id"), index=True, nullable=True)
    file_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    change_type: Mapped[str] = mapped_column(String, nullable=False)
    removed: Mapped[bool] = mapped_column(Boolean, default=False)
    file_payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
