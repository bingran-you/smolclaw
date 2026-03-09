"""Shared dependencies for Calendar routes."""

from __future__ import annotations

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from claw_gcal.models import User, get_session_factory


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
    x_claw_gcal_user: str | None,
) -> str | None:
    if not x_claw_gcal_user:
        return None
    user = db.query(User).filter(
        (User.id == x_claw_gcal_user) | (User.email_address == x_claw_gcal_user)
    ).first()
    if user:
        return user.id
    return None


def resolve_user_id(
    userId: str,
    x_claw_gcal_user: str | None = Header(None),
    db: Session = Depends(get_db),
) -> str:
    """Resolve 'me' to actual user id.

    Priority: userId path param -> X-Claw-Gcal-User header -> first user in DB.
    """
    if userId != "me":
        user = db.query(User).filter(User.id == userId).first()
        if not user:
            raise HTTPException(404, f"User {userId!r} not found")
        return userId

    resolved = _resolve_header_user(db, x_claw_gcal_user)
    if resolved:
        return resolved

    user = db.query(User).first()
    if not user:
        raise HTTPException(404, "No users in database. Run `smolclaw-gcal seed` first.")
    return user.id


def resolve_actor_user_id(
    x_claw_gcal_user: str | None = Header(None),
    db: Session = Depends(get_db),
) -> str:
    """Resolve request actor for endpoints without userId path params.

    Priority: X-Claw-Gcal-User header -> first user in DB.
    """
    resolved = _resolve_header_user(db, x_claw_gcal_user)
    if resolved:
        return resolved

    user = db.query(User).first()
    if not user:
        raise HTTPException(404, "No users in database. Run `smolclaw-gcal seed` first.")
    return user.id
