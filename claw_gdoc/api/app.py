"""Main FastAPI application."""

from __future__ import annotations

import json

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware

from claw_gdoc.models import Document, User, get_session_factory
from claw_gdoc.state.action_log import action_log
from claw_gdoc.state.channels import channel_registry
from claw_gdoc.state.snapshots import get_diff, get_state_dump, restore_snapshot, take_snapshot

from . import changes, documents, drive, permissions
from .access import list_accessible_documents
from .deps import get_db, resolve_user_id
from .schemas import Profile

app = FastAPI(
    title="Mock Google Docs API",
    description="Docs-compatible REST API for AI agent safety evaluation and RL training",
    version="0.1.0",
)

_HTTP_STATUS_MAP = {
    400: ("INVALID_ARGUMENT", "badRequest"),
    401: ("UNAUTHENTICATED", "unauthorized"),
    403: ("PERMISSION_DENIED", "forbidden"),
    404: ("NOT_FOUND", "notFound"),
    409: ("ALREADY_EXISTS", "conflict"),
    429: ("RESOURCE_EXHAUSTED", "rateLimitExceeded"),
    500: ("INTERNAL", "backendError"),
}


@app.exception_handler(HTTPException)
async def gdoc_error_handler(request: Request, exc: HTTPException):
    """Return errors in Google API style."""
    status, reason = _HTTP_STATUS_MAP.get(exc.status_code, ("UNKNOWN", "unknown"))
    custom_reason = None
    if isinstance(exc.detail, dict):
        message = exc.detail.get("message") or str(exc.detail)
        custom_reason = exc.detail.get("reason")
    else:
        message = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    reason = custom_reason or reason
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.status_code,
                "message": message,
                "reason": reason,
                "status": status,
                "errors": [
                    {
                        "message": message,
                        "domain": "global",
                        "reason": reason,
                    }
                ],
            }
        },
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ActionLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = str(request.url.path)
        query = str(request.url.query)
        full_path = f"{path}?{query}" if query else path

        if path.startswith(("/_admin", "/docs", "/openapi", "/static", "/mcp")):
            return await call_next(request)

        body_bytes = await request.body()
        body_dict = None
        if body_bytes:
            try:
                body_dict = json.loads(body_bytes)
            except (json.JSONDecodeError, UnicodeDecodeError):
                body_dict = None

        response = await call_next(request)

        user_id = request.headers.get("X-Claw-Gdoc-User", "") or request.headers.get("X-Mock-Gdoc-User", "")
        if not user_id and "/users/" in path:
            parts = path.split("/users/")
            if len(parts) > 1:
                user_id = parts[1].split("/")[0]

        action_log.record(
            method=request.method,
            path=full_path,
            user_id=user_id,
            request_body=body_dict,
            response_status=response.status_code,
        )
        return response


app.add_middleware(ActionLogMiddleware)

GDOC_PREFIX = "/v1"
DRIVE_PREFIX = "/drive/v3"

app.include_router(documents.router, prefix=GDOC_PREFIX, tags=["documents"])
app.include_router(drive.router, prefix=DRIVE_PREFIX, tags=["drive"])
app.include_router(permissions.router, prefix=DRIVE_PREFIX, tags=["permissions"])
app.include_router(changes.router, prefix=DRIVE_PREFIX, tags=["changes"])


@app.get(f"{GDOC_PREFIX}/users/{{userId}}/profile", response_model=Profile, tags=["profile"])
def get_profile(
    userId: str,
    db: Session = Depends(get_db),
    _user_id: str = Depends(resolve_user_id),
):
    user = db.query(User).filter(User.id == _user_id).first()
    document_count = sum(
        1 for document, _permission in list_accessible_documents(db, _user_id) if not document.trashed
    )
    return Profile(
        emailAddress=user.email_address,
        displayName=user.display_name,
        documentsTotal=document_count,
        historyId=str(user.history_id),
    )


@app.post("/_admin/reset", tags=["admin"])
def admin_reset():
    success = restore_snapshot("initial")
    action_log.clear()
    channel_registry.clear()
    if success:
        return {"status": "ok", "message": "Reset to initial state"}
    return {"status": "error", "message": "No initial snapshot found. Run `smolclaw-gdoc seed` first."}


@app.post("/_admin/seed", tags=["admin"])
def admin_seed(scenario: str = "default", seed: int = 42):
    from claw_gdoc.models import Base, get_engine
    from claw_gdoc.seed.generator import seed_database

    engine = get_engine()
    Base.metadata.drop_all(engine)
    try:
        result = seed_database(scenario=scenario, seed=seed)
        action_log.clear()
        channel_registry.clear()
        return {"status": "ok", "scenario": scenario, **result}
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get("/_admin/state", tags=["admin"])
def admin_state():
    return get_state_dump()


@app.get("/_admin/diff", tags=["admin"])
def admin_diff():
    return get_diff()


@app.get("/_admin/action_log", tags=["admin"])
def admin_action_log():
    return {"entries": action_log.get_entries(), "count": len(action_log)}


@app.post("/_admin/snapshot/{name}", tags=["admin"])
def admin_snapshot(name: str):
    path = take_snapshot(name)
    return {"status": "ok", "path": str(path)}


@app.post("/_admin/restore/{name}", tags=["admin"])
def admin_restore(name: str):
    success = restore_snapshot(name)
    if success:
        return {"status": "ok", "message": f"Restored from snapshot '{name}'"}
    return {"status": "error", "message": f"Snapshot '{name}' not found"}


@app.get("/_admin/tasks", tags=["admin"])
def admin_tasks():
    from claw_gdoc.tasks import get_task as _get_task, list_tasks as _list_tasks

    tasks = []
    for name in _list_tasks():
        task = _get_task(name)
        tasks.append(
            {
                "name": task.name,
                "description": task.description,
                "instruction": task.instruction,
                "category": task.category,
                "scenario": task.scenario,
                "points": task.points,
                "tags": task.tags,
            }
        )
    return {"tasks": tasks, "count": len(tasks)}


@app.post("/_admin/tasks/{task_name}/evaluate", tags=["admin"])
def admin_task_evaluate(task_name: str):
    from claw_gdoc.tasks import get_task as _get_task

    task = _get_task(task_name)
    if not task:
        raise HTTPException(404, f"Task '{task_name}' not found")

    state = get_state_dump()
    diff = get_diff()
    log_entries = action_log.get_entries()
    reward, done = task.evaluate(state, diff, log_entries)

    diff_summary = {
        "added": 0,
        "updated": 0,
        "deleted": 0,
        "accessibleAdded": 0,
        "accessibleUpdated": 0,
        "accessibleDeleted": 0,
    }
    for user_data in diff.get("users", {}).values():
        docs = user_data.get("documents", {})
        diff_summary["added"] += len(docs.get("added", []))
        diff_summary["updated"] += len(docs.get("updated", []))
        diff_summary["deleted"] += len(docs.get("deleted", []))
        accessible = user_data.get("accessibleDocuments", {})
        diff_summary["accessibleAdded"] += len(accessible.get("added", []))
        diff_summary["accessibleUpdated"] += len(accessible.get("updated", []))
        diff_summary["accessibleDeleted"] += len(accessible.get("deleted", []))

    recent_actions = log_entries[-20:] if log_entries else []
    return {
        "task_name": task_name,
        "reward": reward,
        "done": done,
        "diff_summary": diff_summary,
        "action_count": len(log_entries),
        "recent_actions": recent_actions,
    }


@app.get("/health")
def health():
    return {"status": "ok"}
