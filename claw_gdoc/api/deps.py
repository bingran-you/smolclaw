"""Shared dependencies for API routes."""

from __future__ import annotations

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from claw_gdoc.models import User, get_session_factory


def get_db() -> Session:
    """Yield a DB session."""
    session_factory = get_session_factory()
    db = session_factory()
    try:
        yield db
    finally:
        db.close()


def _resolve_header_user(
    db: Session,
    x_claw_gdoc_user: str | None,
    x_mock_gdoc_user: str | None,
) -> str | None:
    candidate = x_claw_gdoc_user or x_mock_gdoc_user
    if candidate:
        user = db.query(User).filter(
            (User.id == candidate) | (User.email_address == candidate)
        ).first()
        if user:
            return user.id
    return None


def resolve_user_id(
    userId: str,
    x_claw_gdoc_user: str | None = Header(None),
    x_mock_gdoc_user: str | None = Header(None),
    db: Session = Depends(get_db),
) -> str:
    """Resolve 'me' to the actual user ID."""
    if userId != "me":
        user = db.query(User).filter(User.id == userId).first()
        if not user:
            raise HTTPException(404, f"User {userId!r} not found")
        return userId

    resolved = _resolve_header_user(db, x_claw_gdoc_user, x_mock_gdoc_user)
    if resolved:
        return resolved

    user = db.query(User).first()
    if not user:
        raise HTTPException(404, "No users in database. Run `smolclaw-gdoc seed` first.")
    return user.id


def resolve_actor_user_id(
    x_claw_gdoc_user: str | None = Header(None),
    x_mock_gdoc_user: str | None = Header(None),
    db: Session = Depends(get_db),
) -> str:
    """Resolve actor for endpoints without userId path params."""
    resolved = _resolve_header_user(db, x_claw_gdoc_user, x_mock_gdoc_user)
    if resolved:
        return resolved

    user = db.query(User).first()
    if not user:
        raise HTTPException(404, "No users in database. Run `smolclaw-gdoc seed` first.")
    return user.id
