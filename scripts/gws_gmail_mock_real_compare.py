#!/usr/bin/env python3
"""Compare real Gmail CLI behavior vs local mock API behavior command-by-command.

Outputs:
  - reports/gws_gmail_mock_real_compare_YYYYMMDD.json
  - reports/gws_gmail_mock_real_compare_YYYYMMDD.md
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from claw_gmail.models import reset_engine
from claw_gmail.seed.generator import seed_database
from claw_gmail.server import create_app

GWS_BIN = Path.home() / ".cargo" / "bin" / "gws"
REPORTS_DIR = ROOT / "reports"
MOCK_DB = ROOT / ".data" / "gws_gmail_compare.db"
COVERAGE_PATH = ROOT / "tests" / "fixtures" / "mock_coverage.json"
REAL_ACCOUNT = "dowhiz@deep-tutor.com"


@dataclass
class CallResult:
    status: int
    returncode: int | None
    keys: list[str]
    error_reason: str | None
    error_message: str | None
    raw: Any


def _now_suffix() -> str:
    return datetime.now().strftime("%Y%m%d")


def _extract_json(text: str) -> Any:
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    obj_start = text.find("{")
    arr_start = text.find("[")
    starts = [x for x in (obj_start, arr_start) if x != -1]
    if not starts:
        return None
    start = min(starts)
    end_obj = text.rfind("}")
    end_arr = text.rfind("]")
    end = max(end_obj, end_arr)
    if end <= start:
        return None

    chunk = text[start : end + 1]
    try:
        return json.loads(chunk)
    except json.JSONDecodeError:
        return None


def _run(cmd: list[str], env: dict[str, str] | None = None) -> tuple[int, str, str]:
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _normalize_wrapper_payload(payload: Any) -> Any:
    # gws may wrap binary reads into a helper payload.
    if isinstance(payload, dict):
        keys = set(payload.keys())
        if keys == {"status", "bytes", "mimeType", "saved_file"} and payload.get("status") == "success":
            return {}
    return payload


def _real_call(tokens: list[str], params: dict[str, Any] | None, body: Any | None) -> CallResult:
    cmd = [str(GWS_BIN), "gmail", *tokens]
    if params:
        cmd.extend(["--params", json.dumps(params, separators=(",", ":"))])
    if body is not None:
        cmd.extend(["--json", json.dumps(body, separators=(",", ":"))])

    env = os.environ.copy()
    env["GOOGLE_WORKSPACE_CLI_ACCOUNT"] = REAL_ACCOUNT
    rc, stdout, stderr = _run(cmd, env=env)

    payload = _extract_json(stdout) or _extract_json(stderr)
    payload = _normalize_wrapper_payload(payload)

    if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
        err = payload["error"]
        status = int(err.get("code", 400))
        reason = err.get("reason")
        message = err.get("message")
        keys: list[str] = []
    else:
        status = 200 if rc == 0 else 500
        reason = None
        message = None if payload is not None else (stderr.strip() or stdout.strip() or None)
        keys = sorted(payload.keys()) if isinstance(payload, dict) else []

    return CallResult(
        status=status,
        returncode=rc,
        keys=keys,
        error_reason=reason,
        error_message=message,
        raw=payload,
    )


def _mock_call(
    client: TestClient,
    method: str,
    path: str,
    path_param_names: list[str],
    params: dict[str, Any] | None,
    body: Any | None,
) -> CallResult:
    params = dict(params or {})
    route = "/" + path.lstrip("/")

    for p in path_param_names:
        val = params.get(p, "")
        route = route.replace("{" + p + "}", quote(str(val), safe=""))

    query = {k: v for k, v in params.items() if k not in path_param_names and v is not None}
    if method.upper() in {"POST", "PUT", "PATCH"}:
        resp = client.request(method.upper(), route, params=query, json=body)
    else:
        resp = client.request(method.upper(), route, params=query)

    try:
        payload = resp.json()
    except Exception:
        payload = None

    status_code = 200 if resp.status_code == 204 else resp.status_code

    if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
        err = payload["error"]
        reason = err.get("reason")
        message = err.get("message")
        keys: list[str] = []
    elif resp.status_code >= 400:
        reason = None
        message = payload.get("detail") if isinstance(payload, dict) else resp.text
        keys = []
    else:
        reason = None
        message = None
        keys = sorted(payload.keys()) if isinstance(payload, dict) else []

    return CallResult(
        status=status_code,
        returncode=None,
        keys=keys,
        error_reason=reason,
        error_message=message,
        raw=payload,
    )


def _discover_leaf_commands() -> list[list[str]]:
    def help_text(tokens: list[str]) -> str:
        rc, out, err = _run([str(GWS_BIN), "gmail", *tokens, "--help"])
        _ = rc
        return out + "\n" + err

    def parse_subcommands(text: str) -> list[str]:
        lines = text.splitlines()
        subs: list[str] = []
        in_cmds = False
        for line in lines:
            if line.strip() == "Commands:":
                in_cmds = True
                continue
            if in_cmds:
                if line.startswith("Options:") or line.startswith("FLAGS:"):
                    break
                m = re.match(r"^\s{2}([a-zA-Z][a-zA-Z0-9-]*)\s{2,}", line)
                if not m:
                    continue
                name = m.group(1)
                if name == "help" or name.startswith("+"):
                    continue
                subs.append(name)
        return subs

    leaves: list[list[str]] = []
    stack: list[list[str]] = [[]]
    seen: set[tuple[str, ...]] = set()

    while stack:
        tokens = stack.pop()
        key = tuple(tokens)
        if key in seen:
            continue
        seen.add(key)

        subs = parse_subcommands(help_text(tokens))
        if subs:
            for s in subs:
                stack.append(tokens + [s])
        elif tokens:
            leaves.append(tokens)

    return sorted(leaves)


def _schema_for_id(endpoint_id: str) -> dict[str, Any]:
    rc, out, err = _run([str(GWS_BIN), "schema", endpoint_id])
    payload = _extract_json(out) or _extract_json(err)
    if rc != 0 or not isinstance(payload, dict):
        return {}
    return payload


def _get_coverage_map() -> dict[str, dict[str, Any]]:
    data = json.loads(COVERAGE_PATH.read_text())
    return {e["id"]: e for e in data["endpoints"]}


def _raw_rfc2822(subject: str, suffix: str, *, to_email: str = REAL_ACCOUNT) -> str:
    body = f"Parity body {suffix}"
    raw = (
        f"From: {REAL_ACCOUNT}\r\n"
        f"To: {to_email}\r\n"
        f"Subject: {subject} {suffix}\r\n"
        "MIME-Version: 1.0\r\n"
        "Content-Type: text/plain; charset=UTF-8\r\n"
        "\r\n"
        f"{body}\r\n"
    )
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def _find_attachment(payload: Any) -> tuple[str | None, str | None]:
    if not isinstance(payload, dict):
        return None, None

    msg_id = payload.get("id")
    root = payload.get("payload")

    def walk(part: Any) -> str | None:
        if not isinstance(part, dict):
            return None
        body = part.get("body")
        if isinstance(body, dict) and isinstance(body.get("attachmentId"), str):
            return body["attachmentId"]
        parts = part.get("parts")
        if isinstance(parts, list):
            for child in parts:
                found = walk(child)
                if found:
                    return found
        return None

    attachment_id = walk(root)
    if msg_id and attachment_id:
        return msg_id, attachment_id
    return None, None


def _seed_mock_and_client() -> TestClient:
    reset_engine()
    if MOCK_DB.exists():
        MOCK_DB.unlink()
    seed_database(scenario="default", seed=42, db_path=str(MOCK_DB))
    app = create_app(db_path=str(MOCK_DB), enable_mcp=False)
    return TestClient(app)


def _collect_real_context() -> dict[str, Any]:
    suffix = datetime.now().strftime("%H%M%S")

    ctx: dict[str, Any] = {
        "suffix": suffix,
        "user_id": "me",
        "raw_insert": _raw_rfc2822("Parity Insert", suffix),
        "raw_import": _raw_rfc2822("Parity Import", suffix),
        "raw_send": _raw_rfc2822("Parity Send", suffix),
        "raw_draft_create": _raw_rfc2822("Parity Draft Create", suffix),
        "raw_draft_update": _raw_rfc2822("Parity Draft Update", suffix),
        "watch_topic": "projects/project-1068555158553/topics/gmail-parity",
        "cse_identity_email": f"cse-{suffix}@example.com",
        "cse_patch_email": f"csepatch-{suffix}@example.com",
        "cse_keypair_id": "missing-keypair",
        "smime_id": "missing-smime",
        "created_message_ids": [],
        "created_label_ids": [],
        "created_draft_ids": [],
        "created_filter_ids": [],
        "created_send_as": [],
        "created_forwarding": [],
        "created_delegates": [],
    }

    def call(tokens: list[str], params: dict[str, Any] | None = None, body: Any | None = None) -> dict | None:
        res = _real_call(tokens, params, body)
        if isinstance(res.raw, dict):
            return res.raw
        return None

    def create_message(tag: str) -> tuple[str | None, str | None]:
        payload = call(
            ["users", "messages", "insert"],
            {"userId": "me"},
            {"raw": _raw_rfc2822(f"Parity {tag}", suffix)},
        )
        msg_id = payload.get("id") if isinstance(payload, dict) else None
        thread_id = payload.get("threadId") if isinstance(payload, dict) else None
        if msg_id:
            ctx["created_message_ids"].append(msg_id)
        return msg_id, thread_id

    def create_label(tag: str) -> str | None:
        payload = call(
            ["users", "labels", "create"],
            {"userId": "me"},
            {"name": f"Parity {tag} {suffix}"},
        )
        label_id = payload.get("id") if isinstance(payload, dict) else None
        if label_id:
            ctx["created_label_ids"].append(label_id)
        return label_id

    def create_draft(tag: str) -> str | None:
        payload = call(
            ["users", "drafts", "create"],
            {"userId": "me"},
            {"message": {"raw": _raw_rfc2822(f"Parity Draft {tag}", suffix)}},
        )
        draft_id = payload.get("id") if isinstance(payload, dict) else None
        if draft_id:
            ctx["created_draft_ids"].append(draft_id)
        return draft_id

    def create_filter(tag: str) -> str | None:
        payload = call(
            ["users", "settings", "filters", "create"],
            {"userId": "me"},
            {
                "criteria": {"subject": f"Parity-{tag}-{suffix}"},
                "action": {"addLabelIds": ["STARRED"]},
            },
        )
        filter_id = payload.get("id") if isinstance(payload, dict) else None
        if filter_id:
            ctx["created_filter_ids"].append(filter_id)
        return filter_id

    def create_send_as(email: str) -> str | None:
        payload = call(
            ["users", "settings", "sendAs", "create"],
            {"userId": "me"},
            {
                "sendAsEmail": email,
                "displayName": f"Parity {suffix}",
                "treatAsAlias": True,
            },
        )
        send_as = payload.get("sendAsEmail") if isinstance(payload, dict) else None
        if send_as:
            ctx["created_send_as"].append(send_as)
        return send_as

    def create_forwarding(email: str) -> str | None:
        payload = call(
            ["users", "settings", "forwardingAddresses", "create"],
            {"userId": "me"},
            {"forwardingEmail": email},
        )
        forwarding = payload.get("forwardingEmail") if isinstance(payload, dict) else None
        if forwarding:
            ctx["created_forwarding"].append(forwarding)
        return forwarding

    def create_delegate(email: str) -> str | None:
        payload = call(
            ["users", "settings", "delegates", "create"],
            {"userId": "me"},
            {"delegateEmail": email},
        )
        delegate = payload.get("delegateEmail") if isinstance(payload, dict) else None
        if delegate:
            ctx["created_delegates"].append(delegate)
        return delegate

    # Profile/history baseline.
    profile = call(["users", "getProfile"], {"userId": "me"}) or {}
    history_id = str(profile.get("historyId", "1"))
    try:
        history_start = max(1, int(history_id) - 1)
    except ValueError:
        history_start = 1
    ctx["history_start_id"] = str(history_start)

    # Message resources (dedicated ids because command order includes deletes early).
    msg_get, thread_get = create_message("MSG-GET")
    msg_delete, thread_delete = create_message("MSG-DELETE")
    msg_modify, thread_modify = create_message("MSG-MODIFY")
    msg_trash, thread_trash = create_message("MSG-TRASH")
    msg_untrash, thread_untrash = create_message("MSG-UNTRASH")
    batch_1, _ = create_message("MSG-BATCH-1")
    batch_2, _ = create_message("MSG-BATCH-2")

    if msg_untrash:
        call(["users", "messages", "trash"], {"userId": "me", "id": msg_untrash}, None)
    if thread_untrash:
        call(["users", "threads", "trash"], {"userId": "me", "id": thread_untrash}, None)

    ctx["message_id_get"] = msg_get or "missing-message-get"
    ctx["message_id_delete"] = msg_delete or ctx["message_id_get"]
    ctx["message_id_modify"] = msg_modify or ctx["message_id_get"]
    ctx["message_id_trash"] = msg_trash or ctx["message_id_get"]
    ctx["message_id_untrash"] = msg_untrash or ctx["message_id_get"]
    ctx["batch_modify_ids"] = [x for x in [batch_1, batch_2] if x] or [ctx["message_id_get"]]
    ctx["batch_delete_ids"] = [x for x in [batch_2] if x] or [ctx["message_id_delete"]]

    ctx["thread_id_get"] = thread_get or "missing-thread-get"
    ctx["thread_id_delete"] = thread_delete or ctx["thread_id_get"]
    ctx["thread_id_modify"] = thread_modify or ctx["thread_id_get"]
    ctx["thread_id_trash"] = thread_trash or ctx["thread_id_get"]
    ctx["thread_id_untrash"] = thread_untrash or ctx["thread_id_get"]

    # Attachment probe.
    ctx["attachment_message_id"] = ctx["message_id_get"]
    ctx["attachment_id"] = "missing-attachment"
    lst = call(["users", "messages", "list"], {"userId": "me", "maxResults": 20}) or {}
    for item in lst.get("messages", []):
        if not isinstance(item, dict) or not item.get("id"):
            continue
        full = call(["users", "messages", "get"], {"userId": "me", "id": item["id"], "format": "full"})
        msg_id, att_id = _find_attachment(full)
        if msg_id and att_id:
            ctx["attachment_message_id"] = msg_id
            ctx["attachment_id"] = att_id
            break

    # Labels.
    ctx["label_id_get"] = "INBOX"
    ctx["label_id_delete"] = create_label("LBL-DELETE") or "MISSING_LABEL_DELETE"
    patch_label = create_label("LBL-PATCH")
    update_label = create_label("LBL-UPDATE")
    ctx["label_id_patch"] = patch_label or ctx["label_id_get"]
    ctx["label_id_update"] = update_label or ctx["label_id_patch"]

    # Drafts.
    ctx["draft_id_delete"] = create_draft("DRAFT-DELETE") or "MISSING_DRAFT_DELETE"
    ctx["draft_id_get"] = create_draft("DRAFT-GET") or ctx["draft_id_delete"]
    ctx["draft_id_send"] = create_draft("DRAFT-SEND") or ctx["draft_id_get"]
    ctx["draft_id_update"] = create_draft("DRAFT-UPDATE") or ctx["draft_id_get"]

    # Filters.
    ctx["filter_id_delete"] = create_filter("FILTER-DELETE") or "MISSING_FILTER_DELETE"
    ctx["filter_id_get"] = create_filter("FILTER-GET") or ctx["filter_id_delete"]

    # SendAs.
    send_as_list = call(["users", "settings", "sendAs", "list"], {"userId": "me"}) or {}
    send_as_items = send_as_list.get("sendAs", []) if isinstance(send_as_list, dict) else []
    primary_send_as = next(
        (
            e.get("sendAsEmail")
            for e in send_as_items
            if isinstance(e, dict) and e.get("isPrimary") and e.get("sendAsEmail")
        ),
        REAL_ACCOUNT,
    )
    ctx["send_as_get_email"] = primary_send_as
    ctx["send_as_verify_email"] = primary_send_as

    ctx["send_as_create_email"] = f"create-{suffix}@deep-tutor.com"

    delete_alias = create_send_as(f"delete-{suffix}@deep-tutor.com")
    patch_alias = create_send_as(f"patch-{suffix}@deep-tutor.com")
    update_alias = create_send_as(f"update-{suffix}@deep-tutor.com")

    ctx["send_as_delete_email"] = delete_alias or primary_send_as
    ctx["send_as_patch_email"] = patch_alias or primary_send_as
    ctx["send_as_update_email"] = update_alias or primary_send_as
    ctx["smime_send_as_email"] = primary_send_as

    # Forwarding / delegates.
    ctx["forwarding_create_email"] = f"fwd-create-{suffix}@example.com"
    forwarding_precreated = create_forwarding(f"fwd-pre-{suffix}@example.com")
    ctx["forwarding_email_get"] = forwarding_precreated or f"missing-fwd-{suffix}@example.com"
    ctx["forwarding_email_delete"] = forwarding_precreated or ctx["forwarding_email_get"]

    ctx["delegate_create_email"] = f"delegate-create-{suffix}@example.com"
    delegate_precreated = create_delegate(f"delegate-pre-{suffix}@example.com")
    ctx["delegate_email_get"] = delegate_precreated or f"missing-delegate-{suffix}@example.com"
    ctx["delegate_email_delete"] = delegate_precreated or ctx["delegate_email_get"]

    return ctx


def _collect_mock_context(client: TestClient) -> dict[str, Any]:
    suffix = datetime.now().strftime("%H%M%S")

    ctx: dict[str, Any] = {
        "suffix": suffix,
        "user_id": "me",
        "raw_insert": _raw_rfc2822("Parity Insert", suffix),
        "raw_import": _raw_rfc2822("Parity Import", suffix),
        "raw_send": _raw_rfc2822("Parity Send", suffix),
        "raw_draft_create": _raw_rfc2822("Parity Draft Create", suffix),
        "raw_draft_update": _raw_rfc2822("Parity Draft Update", suffix),
        "watch_topic": "projects/project-1068555158553/topics/gmail-parity",
        "cse_identity_email": f"cse-{suffix}@example.com",
        "cse_patch_email": f"csepatch-{suffix}@example.com",
        "cse_keypair_id": "missing-keypair",
        "smime_id": "missing-smime",
    }

    def call(method: str, path: str, params: dict[str, Any] | None = None, body: Any | None = None) -> Any:
        if method in {"POST", "PUT", "PATCH"}:
            resp = client.request(method, path, params=params, json=body)
        else:
            resp = client.request(method, path, params=params)
        try:
            return resp.json()
        except Exception:
            return None

    def create_message(tag: str) -> tuple[str | None, str | None]:
        payload = call(
            "POST",
            "/gmail/v1/users/me/messages",
            body={"raw": _raw_rfc2822(f"Parity {tag}", suffix)},
        )
        if isinstance(payload, dict) and not payload.get("error"):
            return payload.get("id"), payload.get("threadId")
        return None, None

    def create_label(tag: str) -> str | None:
        payload = call(
            "POST",
            "/gmail/v1/users/me/labels",
            body={"name": f"Parity {tag} {suffix}"},
        )
        if isinstance(payload, dict) and not payload.get("error"):
            return payload.get("id")
        return None

    def create_draft(tag: str) -> str | None:
        payload = call(
            "POST",
            "/gmail/v1/users/me/drafts",
            body={"message": {"raw": _raw_rfc2822(f"Parity Draft {tag}", suffix)}},
        )
        if isinstance(payload, dict) and not payload.get("error"):
            return payload.get("id")
        return None

    def create_filter(tag: str) -> str | None:
        payload = call(
            "POST",
            "/gmail/v1/users/me/settings/filters",
            body={
                "criteria": {"subject": f"Parity-{tag}-{suffix}"},
                "action": {"addLabelIds": ["STARRED"]},
            },
        )
        if isinstance(payload, dict) and not payload.get("error"):
            return payload.get("id")
        return None

    def create_send_as(email: str) -> str | None:
        payload = call(
            "POST",
            "/gmail/v1/users/me/settings/sendAs",
            body={
                "sendAsEmail": email,
                "displayName": f"Parity {suffix}",
                "treatAsAlias": True,
            },
        )
        if isinstance(payload, dict) and not payload.get("error"):
            return payload.get("sendAsEmail")
        return None

    def create_forwarding(email: str) -> str | None:
        payload = call(
            "POST",
            "/gmail/v1/users/me/settings/forwardingAddresses",
            body={"forwardingEmail": email},
        )
        if isinstance(payload, dict) and not payload.get("error"):
            return payload.get("forwardingEmail")
        return None

    def create_delegate(email: str) -> str | None:
        payload = call(
            "POST",
            "/gmail/v1/users/me/settings/delegates",
            body={"delegateEmail": email},
        )
        if isinstance(payload, dict) and not payload.get("error"):
            return payload.get("delegateEmail")
        return None

    # Profile/history baseline.
    profile = call("GET", "/gmail/v1/users/me/profile") or {}
    history_id = str(profile.get("historyId", "1"))
    try:
        history_start = max(1, int(history_id) - 1)
    except ValueError:
        history_start = 1
    ctx["history_start_id"] = str(history_start)

    # Message resources.
    msg_get, thread_get = create_message("MSG-GET")
    msg_delete, thread_delete = create_message("MSG-DELETE")
    msg_modify, thread_modify = create_message("MSG-MODIFY")
    msg_trash, thread_trash = create_message("MSG-TRASH")
    msg_untrash, thread_untrash = create_message("MSG-UNTRASH")
    batch_1, _ = create_message("MSG-BATCH-1")
    batch_2, _ = create_message("MSG-BATCH-2")

    if msg_untrash:
        call("POST", f"/gmail/v1/users/me/messages/{msg_untrash}/trash")
    if thread_untrash:
        call("POST", f"/gmail/v1/users/me/threads/{thread_untrash}/trash")

    ctx["message_id_get"] = msg_get or "missing-message-get"
    ctx["message_id_delete"] = msg_delete or ctx["message_id_get"]
    ctx["message_id_modify"] = msg_modify or ctx["message_id_get"]
    ctx["message_id_trash"] = msg_trash or ctx["message_id_get"]
    ctx["message_id_untrash"] = msg_untrash or ctx["message_id_get"]
    ctx["batch_modify_ids"] = [x for x in [batch_1, batch_2] if x] or [ctx["message_id_get"]]
    ctx["batch_delete_ids"] = [x for x in [batch_2] if x] or [ctx["message_id_delete"]]

    ctx["thread_id_get"] = thread_get or "missing-thread-get"
    ctx["thread_id_delete"] = thread_delete or ctx["thread_id_get"]
    ctx["thread_id_modify"] = thread_modify or ctx["thread_id_get"]
    ctx["thread_id_trash"] = thread_trash or ctx["thread_id_get"]
    ctx["thread_id_untrash"] = thread_untrash or ctx["thread_id_get"]

    # Attachment probe.
    ctx["attachment_message_id"] = ctx["message_id_get"]
    ctx["attachment_id"] = "missing-attachment"
    lst = call("GET", "/gmail/v1/users/me/messages", params={"maxResults": 20}) or {}
    for item in lst.get("messages", []):
        if not isinstance(item, dict) or not item.get("id"):
            continue
        full = call("GET", f"/gmail/v1/users/me/messages/{item['id']}", params={"format": "full"})
        msg_id, att_id = _find_attachment(full)
        if msg_id and att_id:
            ctx["attachment_message_id"] = msg_id
            ctx["attachment_id"] = att_id
            break

    # Labels.
    ctx["label_id_get"] = "INBOX"
    ctx["label_id_delete"] = create_label("LBL-DELETE") or "MISSING_LABEL_DELETE"
    patch_label = create_label("LBL-PATCH")
    update_label = create_label("LBL-UPDATE")
    ctx["label_id_patch"] = patch_label or ctx["label_id_get"]
    ctx["label_id_update"] = update_label or ctx["label_id_patch"]

    # Drafts.
    ctx["draft_id_delete"] = create_draft("DRAFT-DELETE") or "MISSING_DRAFT_DELETE"
    ctx["draft_id_get"] = create_draft("DRAFT-GET") or ctx["draft_id_delete"]
    ctx["draft_id_send"] = create_draft("DRAFT-SEND") or ctx["draft_id_get"]
    ctx["draft_id_update"] = create_draft("DRAFT-UPDATE") or ctx["draft_id_get"]

    # Filters.
    ctx["filter_id_delete"] = create_filter("FILTER-DELETE") or "MISSING_FILTER_DELETE"
    ctx["filter_id_get"] = create_filter("FILTER-GET") or ctx["filter_id_delete"]

    # SendAs.
    send_as_list = call("GET", "/gmail/v1/users/me/settings/sendAs") or {}
    send_as_items = send_as_list.get("sendAs", []) if isinstance(send_as_list, dict) else []
    primary_send_as = next(
        (
            e.get("sendAsEmail")
            for e in send_as_items
            if isinstance(e, dict) and e.get("isPrimary") and e.get("sendAsEmail")
        ),
        "alex@nexusai.com",
    )
    ctx["send_as_get_email"] = primary_send_as
    ctx["send_as_verify_email"] = primary_send_as

    ctx["send_as_create_email"] = f"create-{suffix}@deep-tutor.com"

    delete_alias = create_send_as(f"delete-{suffix}@deep-tutor.com")
    patch_alias = create_send_as(f"patch-{suffix}@deep-tutor.com")
    update_alias = create_send_as(f"update-{suffix}@deep-tutor.com")

    ctx["send_as_delete_email"] = delete_alias or primary_send_as
    ctx["send_as_patch_email"] = patch_alias or primary_send_as
    ctx["send_as_update_email"] = update_alias or primary_send_as
    ctx["smime_send_as_email"] = primary_send_as

    # Forwarding / delegates.
    ctx["forwarding_create_email"] = f"fwd-create-{suffix}@example.com"
    forwarding_precreated = create_forwarding(f"fwd-pre-{suffix}@example.com")
    ctx["forwarding_email_get"] = forwarding_precreated or f"missing-fwd-{suffix}@example.com"
    ctx["forwarding_email_delete"] = forwarding_precreated or ctx["forwarding_email_get"]

    ctx["delegate_create_email"] = f"delegate-create-{suffix}@example.com"
    delegate_precreated = create_delegate(f"delegate-pre-{suffix}@example.com")
    ctx["delegate_email_get"] = delegate_precreated or f"missing-delegate-{suffix}@example.com"
    ctx["delegate_email_delete"] = delegate_precreated or ctx["delegate_email_get"]

    return ctx


def _cleanup_real_context(ctx: dict[str, Any]) -> None:
    def call(tokens: list[str], params: dict[str, Any] | None = None, body: Any | None = None):
        _real_call(tokens, params, body)

    for email in ctx.get("created_delegates", []):
        if email:
            call(["users", "settings", "delegates", "delete"], {"userId": "me", "delegateEmail": email}, None)

    for email in ctx.get("created_forwarding", []):
        if email:
            call(
                ["users", "settings", "forwardingAddresses", "delete"],
                {"userId": "me", "forwardingEmail": email},
                None,
            )

    for email in ctx.get("created_send_as", []):
        if email and email != REAL_ACCOUNT:
            call(["users", "settings", "sendAs", "delete"], {"userId": "me", "sendAsEmail": email}, None)

    for filter_id in ctx.get("created_filter_ids", []):
        if filter_id:
            call(["users", "settings", "filters", "delete"], {"userId": "me", "id": filter_id}, None)

    for draft_id in ctx.get("created_draft_ids", []):
        if draft_id:
            call(["users", "drafts", "delete"], {"userId": "me", "id": draft_id}, None)

    for label_id in ctx.get("created_label_ids", []):
        if label_id:
            call(["users", "labels", "delete"], {"userId": "me", "id": label_id}, None)

    for msg_id in ctx.get("created_message_ids", []):
        if msg_id:
            call(["users", "messages", "delete"], {"userId": "me", "id": msg_id}, None)


def _pick_path_param(endpoint_id: str, name: str, ctx: dict[str, Any]) -> Any:
    if name == "userId":
        return ctx.get("user_id", "me")

    if name == "messageId":
        return ctx.get("attachment_message_id", ctx.get("message_id_get", "missing-message"))

    if name == "id":
        id_map = {
            "gmail.users.messages.get": "message_id_get",
            "gmail.users.messages.delete": "message_id_delete",
            "gmail.users.messages.modify": "message_id_modify",
            "gmail.users.messages.trash": "message_id_trash",
            "gmail.users.messages.untrash": "message_id_untrash",
            "gmail.users.messages.attachments.get": "attachment_id",
            "gmail.users.labels.get": "label_id_get",
            "gmail.users.labels.delete": "label_id_delete",
            "gmail.users.labels.patch": "label_id_patch",
            "gmail.users.labels.update": "label_id_update",
            "gmail.users.drafts.get": "draft_id_get",
            "gmail.users.drafts.delete": "draft_id_delete",
            "gmail.users.drafts.update": "draft_id_update",
            "gmail.users.threads.get": "thread_id_get",
            "gmail.users.threads.delete": "thread_id_delete",
            "gmail.users.threads.modify": "thread_id_modify",
            "gmail.users.threads.trash": "thread_id_trash",
            "gmail.users.threads.untrash": "thread_id_untrash",
            "gmail.users.settings.filters.get": "filter_id_get",
            "gmail.users.settings.filters.delete": "filter_id_delete",
            "gmail.users.settings.sendAs.smimeInfo.get": "smime_id",
            "gmail.users.settings.sendAs.smimeInfo.delete": "smime_id",
            "gmail.users.settings.sendAs.smimeInfo.setDefault": "smime_id",
        }
        key = id_map.get(endpoint_id)
        if key:
            return ctx.get(key, "missing-id")
        return ctx.get("id", "missing-id")

    if name == "sendAsEmail":
        send_as_map = {
            "gmail.users.settings.sendAs.get": "send_as_get_email",
            "gmail.users.settings.sendAs.delete": "send_as_delete_email",
            "gmail.users.settings.sendAs.patch": "send_as_patch_email",
            "gmail.users.settings.sendAs.update": "send_as_update_email",
            "gmail.users.settings.sendAs.verify": "send_as_verify_email",
        }
        key = send_as_map.get(endpoint_id)
        if key:
            return ctx.get(key, REAL_ACCOUNT)
        return ctx.get("smime_send_as_email", REAL_ACCOUNT)

    if name == "forwardingEmail":
        if endpoint_id.endswith(".get"):
            return ctx.get("forwarding_email_get", "missing-forwarding@example.com")
        return ctx.get("forwarding_email_delete", "missing-forwarding@example.com")

    if name == "delegateEmail":
        if endpoint_id.endswith(".get"):
            return ctx.get("delegate_email_get", "missing-delegate@example.com")
        return ctx.get("delegate_email_delete", "missing-delegate@example.com")

    if name == "cseEmailAddress":
        return ctx.get("cse_identity_email", "missing-cse@example.com")

    if name == "emailAddress":
        return ctx.get("cse_patch_email", "missing-csepatch@example.com")

    if name == "keyPairId":
        return ctx.get("cse_keypair_id", "missing-keypair")

    return ctx.get(name, f"{name}-value")


def _build_request(
    endpoint_id: str,
    method: str,
    path_params: list[str],
    ctx: dict[str, Any],
) -> tuple[dict[str, Any], Any | None]:
    params: dict[str, Any] = {}
    body: Any | None = None

    for p in path_params:
        params[p] = _pick_path_param(endpoint_id, p, ctx)

    if endpoint_id in {"gmail.users.messages.list", "gmail.users.threads.list"}:
        params["maxResults"] = 5
    elif endpoint_id == "gmail.users.messages.get":
        params["format"] = "full"
    elif endpoint_id == "gmail.users.history.list":
        params["startHistoryId"] = ctx.get("history_start_id", "1")
        params["maxResults"] = 5

    if method in {"POST", "PUT", "PATCH"}:
        if endpoint_id == "gmail.users.messages.insert":
            body = {"raw": ctx["raw_insert"], "labelIds": ["INBOX"]}
        elif endpoint_id == "gmail.users.messages.import":
            body = {"raw": ctx["raw_import"]}
        elif endpoint_id == "gmail.users.messages.send":
            body = {"raw": ctx["raw_send"]}
        elif endpoint_id == "gmail.users.messages.modify":
            body = {"addLabelIds": ["STARRED"], "removeLabelIds": ["UNREAD"]}
        elif endpoint_id == "gmail.users.messages.batchModify":
            body = {
                "ids": ctx.get("batch_modify_ids", [ctx.get("message_id_get")]),
                "addLabelIds": ["STARRED"],
                "removeLabelIds": [],
            }
        elif endpoint_id == "gmail.users.messages.batchDelete":
            body = {
                "ids": ctx.get("batch_delete_ids", [ctx.get("message_id_delete")]),
            }
        elif endpoint_id == "gmail.users.threads.modify":
            body = {"addLabelIds": ["STARRED"], "removeLabelIds": []}
        elif endpoint_id == "gmail.users.labels.create":
            body = {"name": f"Parity Create {ctx['suffix']}"}
        elif endpoint_id == "gmail.users.labels.patch":
            body = {"name": f"Parity Patch {ctx['suffix']}"}
        elif endpoint_id == "gmail.users.labels.update":
            body = {
                "name": f"Parity Update {ctx['suffix']}",
                "messageListVisibility": "show",
                "labelListVisibility": "labelShow",
            }
        elif endpoint_id == "gmail.users.drafts.create":
            body = {"message": {"raw": ctx["raw_draft_create"]}}
        elif endpoint_id == "gmail.users.drafts.update":
            body = {
                "id": ctx.get("draft_id_update"),
                "message": {"raw": ctx["raw_draft_update"]},
            }
        elif endpoint_id == "gmail.users.drafts.send":
            body = {"id": ctx.get("draft_id_send")}
        elif endpoint_id == "gmail.users.watch":
            body = {
                "topicName": ctx.get("watch_topic"),
                "labelIds": ["INBOX"],
                "labelFilterBehavior": "include",
            }
        elif endpoint_id == "gmail.users.settings.sendAs.create":
            body = {
                "sendAsEmail": ctx.get("send_as_create_email"),
                "displayName": f"Parity Alias {ctx['suffix']}",
                "treatAsAlias": True,
            }
        elif endpoint_id in {
            "gmail.users.settings.sendAs.update",
            "gmail.users.settings.sendAs.patch",
        }:
            body = {"displayName": f"Parity Updated {ctx['suffix']}"}
        elif endpoint_id == "gmail.users.settings.filters.create":
            body = {
                "criteria": {"subject": f"Parity Filter {ctx['suffix']}"},
                "action": {"addLabelIds": ["STARRED"]},
            }
        elif endpoint_id == "gmail.users.settings.forwardingAddresses.create":
            body = {"forwardingEmail": ctx.get("forwarding_create_email")}
        elif endpoint_id == "gmail.users.settings.delegates.create":
            body = {"delegateEmail": ctx.get("delegate_create_email")}
        elif endpoint_id == "gmail.users.settings.updateVacation":
            body = {
                "enableAutoReply": False,
                "responseSubject": f"Parity {ctx['suffix']}",
                "responseBodyPlainText": "Parity auto-reply",
            }
        elif endpoint_id == "gmail.users.settings.updateAutoForwarding":
            body = {
                "enabled": False,
                "emailAddress": ctx.get("forwarding_email_get", ""),
                "disposition": "leaveInInbox",
            }
        elif endpoint_id == "gmail.users.settings.updateImap":
            body = {
                "enabled": True,
                "autoExpunge": True,
                "expungeBehavior": "archive",
            }
        elif endpoint_id == "gmail.users.settings.updatePop":
            body = {
                "accessWindow": "allMail",
                "disposition": "leaveInInbox",
            }
        elif endpoint_id == "gmail.users.settings.updateLanguage":
            body = {"displayLanguage": "en"}
        elif endpoint_id == "gmail.users.settings.cse.identities.create":
            body = {
                "emailAddress": ctx.get("cse_identity_email"),
                "displayName": f"CSE {ctx['suffix']}",
            }
        elif endpoint_id == "gmail.users.settings.cse.keypairs.create":
            body = {
                "emailAddress": ctx.get("cse_identity_email"),
                "enablementState": "enabled",
                "pem": "-----BEGIN CERTIFICATE-----\\nMIIB\\n-----END CERTIFICATE-----",
            }
        elif endpoint_id in {
            "gmail.users.settings.cse.keypairs.disable",
            "gmail.users.settings.cse.keypairs.enable",
            "gmail.users.settings.cse.keypairs.obliterate",
        }:
            body = {}
        elif endpoint_id == "gmail.users.settings.sendAs.smimeInfo.insert":
            body = {
                "pkcs12": "AQID",
                "encryptedKeyPassword": "password",
            }

    return params, body


def _is_auth_scope_limited(real: CallResult) -> bool:
    if real.status != 403:
        return False
    msg = (real.error_message or "").lower()
    reason = (real.error_reason or "").lower()
    return (
        "insufficient authentication scopes" in msg
        or "insufficient permissions" in msg
        or reason in {"insufficientpermissions", "forbidden"}
    )


def _is_tenant_limited(endpoint_id: str, real: CallResult) -> bool:
    if not endpoint_id.startswith("gmail.users.settings.") and endpoint_id != "gmail.users.watch":
        return False
    if real.status not in {400, 401, 403, 412, 429}:
        return False
    msg = (real.error_message or "").lower()
    reason = (real.error_reason or "").lower()
    if "delegate" in msg or "forward" in msg or "alias" in msg:
        return True
    if "topic" in msg or "pubsub" in msg:
        return True
    return reason in {"forbidden", "failedprecondition", "invalidargument", "badrequest"}


def main() -> int:
    if not GWS_BIN.exists():
        raise SystemExit(f"gws not found: {GWS_BIN}")

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    day = _now_suffix()
    json_out = REPORTS_DIR / f"gws_gmail_mock_real_compare_{day}.json"
    md_out = REPORTS_DIR / f"gws_gmail_mock_real_compare_{day}.md"

    coverage_map = _get_coverage_map()
    leaves = _discover_leaf_commands()
    endpoint_ids = ["gmail." + ".".join(tokens) for tokens in leaves]

    schema_map: dict[str, dict[str, Any]] = {eid: _schema_for_id(eid) for eid in endpoint_ids}

    client = _seed_mock_and_client()
    try:
        real_ctx = _collect_real_context()
        mock_ctx = _collect_mock_context(client)

        results: list[dict[str, Any]] = []
        for endpoint_id, tokens in zip(endpoint_ids, leaves):
            schema = schema_map.get(endpoint_id, {})
            method = schema.get("httpMethod", "GET")
            path = schema.get("path", "")
            if not path:
                continue

            params_meta = schema.get("parameters", {})
            path_params = [
                k
                for k, v in params_meta.items()
                if isinstance(v, dict) and v.get("location") == "path"
            ]

            real_params, real_body = _build_request(endpoint_id, method, path_params, real_ctx)
            mock_params, mock_body = _build_request(endpoint_id, method, path_params, mock_ctx)

            real = _real_call(tokens, real_params, real_body)
            mock = _mock_call(client, method, path, path_params, mock_params, mock_body)

            coverage = coverage_map.get(endpoint_id)
            in_coverage = coverage is not None
            implemented = bool(coverage.get("implemented")) if coverage else False
            skip_reason = coverage.get("skip_reason") if coverage else None

            blocked_by_real_cli = real.status == 411 and real.error_reason == "httpError"
            auth_scope_limited = _is_auth_scope_limited(real)
            tenant_limited = _is_tenant_limited(endpoint_id, real)
            mock_out_of_scope = in_coverage and not implemented

            excluded_from_scoring = (
                blocked_by_real_cli
                or auth_scope_limited
                or tenant_limited
                or mock_out_of_scope
            )

            same_status = real.status == mock.status
            same_status_class = (real.status // 100) == (mock.status // 100)
            same_top_keys = real.keys == mock.keys

            results.append(
                {
                    "id": endpoint_id,
                    "in_mock_coverage": in_coverage,
                    "mock_implemented": implemented,
                    "mock_skip_reason": skip_reason,
                    "method": method,
                    "path": path,
                    "real": {
                        "status": real.status,
                        "returncode": real.returncode,
                        "keys": real.keys,
                        "error_reason": real.error_reason,
                        "error_message": real.error_message,
                    },
                    "mock": {
                        "status": mock.status,
                        "keys": mock.keys,
                        "error_reason": mock.error_reason,
                        "error_message": mock.error_message,
                    },
                    "parity": {
                        "same_status": same_status,
                        "same_status_class": same_status_class,
                        "same_top_keys": same_top_keys,
                        "blocked_by_real_cli": blocked_by_real_cli,
                        "auth_scope_limited": auth_scope_limited,
                        "tenant_limited": tenant_limited,
                        "mock_out_of_scope": mock_out_of_scope,
                        "excluded_from_scoring": excluded_from_scoring,
                    },
                }
            )
    finally:
        try:
            _cleanup_real_context(locals().get("real_ctx", {}))
        except Exception:
            pass
        client.close()

    summary = {
        "total_commands": len(results),
        "missing_in_mock_coverage": sum(1 for r in results if not r["in_mock_coverage"]),
        "out_of_scope_in_mock_coverage": sum(
            1 for r in results if r["in_mock_coverage"] and not r["mock_implemented"]
        ),
        "same_status": sum(1 for r in results if r["parity"]["same_status"]),
        "same_status_class": sum(1 for r in results if r["parity"]["same_status_class"]),
        "same_top_keys": sum(1 for r in results if r["parity"]["same_top_keys"]),
        "exact_parity": sum(
            1
            for r in results
            if r["parity"]["same_status"]
            and r["parity"]["same_status_class"]
            and r["parity"]["same_top_keys"]
        ),
        "excluded_from_scoring": sum(1 for r in results if r["parity"]["excluded_from_scoring"]),
    }
    summary["scored_commands"] = summary["total_commands"] - summary["excluded_from_scoring"]
    summary["exact_parity_scored"] = sum(
        1
        for r in results
        if not r["parity"]["excluded_from_scoring"]
        and r["parity"]["same_status"]
        and r["parity"]["same_status_class"]
        and r["parity"]["same_top_keys"]
    )

    payload = {
        "summary": summary,
        "results": results,
    }
    json_out.write_text(json.dumps(payload, indent=2))

    missing = [r for r in results if not r["in_mock_coverage"]]
    out_of_scope = [r for r in results if r["in_mock_coverage"] and not r["mock_implemented"]]
    excluded = [r for r in results if r["parity"]["excluded_from_scoring"]]
    status_class_mismatch = [r for r in results if not r["parity"]["same_status_class"]]
    key_mismatch_2xx = [
        r
        for r in results
        if (
            r["real"]["status"] // 100 == 2
            and r["mock"]["status"] // 100 == 2
            and not r["parity"]["same_top_keys"]
        )
    ]

    lines: list[str] = []
    lines.append(f"# gws Gmail real vs mock comparison ({day})")
    lines.append("")
    lines.append("## Summary")
    for k in (
        "total_commands",
        "missing_in_mock_coverage",
        "out_of_scope_in_mock_coverage",
        "same_status",
        "same_status_class",
        "same_top_keys",
        "exact_parity",
        "excluded_from_scoring",
        "scored_commands",
        "exact_parity_scored",
    ):
        lines.append(f"- {k}: {summary[k]}")
    lines.append("")

    lines.append(f"## Excluded from scoring ({len(excluded)})")
    for r in excluded:
        if r["parity"]["blocked_by_real_cli"]:
            reason = "real-cli-transport-limit"
        elif r["parity"]["auth_scope_limited"]:
            reason = "auth-scope-limited"
        elif r["parity"]["tenant_limited"]:
            reason = "tenant-limited"
        elif r["parity"]["mock_out_of_scope"]:
            reason = "mock-out-of-scope"
        else:
            reason = "excluded"
        lines.append(
            f"- {r['id']}: {reason}, real={r['real']['status']} ({r['real']['error_reason']}), "
            f"mock={r['mock']['status']}"
        )
    lines.append("")

    lines.append(f"## Missing in mock coverage ({len(missing)})")
    for r in missing:
        lines.append(f"- {r['id']}")
    lines.append("")

    lines.append(f"## Out of scope in mock coverage ({len(out_of_scope)})")
    for r in out_of_scope:
        lines.append(f"- {r['id']}: {r['mock_skip_reason']}")
    lines.append("")

    lines.append("## Status-class mismatches")
    for r in status_class_mismatch:
        lines.append(
            f"- {r['id']}: real={r['real']['status']} ({r['real']['error_reason']}) | "
            f"mock={r['mock']['status']} ({r['mock']['error_reason']})"
        )
    lines.append("")

    lines.append("## 2xx key mismatches")
    for r in key_mismatch_2xx:
        lines.append(
            f"- {r['id']}: real_keys={r['real']['keys']} | mock_keys={r['mock']['keys']}"
        )
    lines.append("")

    lines.append("## Command-by-command status")
    for r in results:
        tag = "OK" if (r["parity"]["same_status"] and r["parity"]["same_top_keys"]) else "DIFF"
        lines.append(f"- {r['id']}: real={r['real']['status']}, mock={r['mock']['status']} [{tag}]")
    lines.append("")

    md_out.write_text("\n".join(lines))

    print(json.dumps({"json": str(json_out), "md": str(md_out), "summary": summary}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
