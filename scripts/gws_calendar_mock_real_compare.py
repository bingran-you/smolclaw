#!/usr/bin/env python3
"""Compare real Calendar CLI behavior vs local mock API behavior command-by-command.

Outputs:
  - reports/gws_calendar_mock_real_compare_YYYYMMDD.json
  - reports/gws_calendar_mock_real_compare_YYYYMMDD.md
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from base64 import b64decode, b64encode
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from claw_gcal.models import reset_engine
from claw_gcal.seed.generator import seed_database
from claw_gcal.server import create_app

GWS_BIN = Path.home() / ".cargo" / "bin" / "gws"
REPORTS_DIR = ROOT / "reports"
MOCK_DB = ROOT / ".data" / "gws_calendar_compare.db"
COVERAGE_PATH = ROOT / "tests" / "fixtures" / "mock_coverage_gcal.json"
REAL_ACCOUNT = "dowhiz@deep-tutor.com"
RAW_HTTP_ENDPOINTS = {
    "calendar.calendars.clear",
    "calendar.events.move",
    "calendar.events.quickAdd",
}
GOOGLE_CALENDAR_BASE_URL = "https://www.googleapis.com/calendar/v3"
GWS_CONFIG_DIR = Path.home() / ".config" / "gws"
_REAL_ACCESS_TOKEN: str | None = None


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


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


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
    if isinstance(payload, dict):
        keys = set(payload.keys())
        if keys == {"status", "bytes", "mimeType", "saved_file"} and payload.get("status") == "success":
            return {}
    return payload


def _account_suffix(account: str) -> str:
    return b64encode(account.encode("utf-8")).decode("ascii").rstrip("=")


def _load_real_authorized_user_credentials() -> dict[str, Any]:
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "cryptography is required for raw HTTP parity checks. Install dev deps first."
        ) from exc

    key_path = GWS_CONFIG_DIR / ".encryption_key"
    if not key_path.exists():
        raise FileNotFoundError(f"Missing gws encryption key: {key_path}")

    suffix = _account_suffix(REAL_ACCOUNT)
    candidates = [
        GWS_CONFIG_DIR / f"credentials.{suffix}.enc",
        GWS_CONFIG_DIR / "credentials.enc",
    ]
    creds_path = next((path for path in candidates if path.exists()), None)
    if creds_path is None:
        raise FileNotFoundError(
            f"Missing encrypted gws credentials for {REAL_ACCOUNT}. Run `gws auth login` first."
        )

    key = b64decode(key_path.read_text())
    encrypted = creds_path.read_bytes()
    if len(encrypted) < 12:
        raise RuntimeError(f"Encrypted credential payload is too short: {creds_path}")

    payload = AESGCM(key).decrypt(encrypted[:12], encrypted[12:], None)
    creds = json.loads(payload)
    if not isinstance(creds, dict):
        raise RuntimeError(f"Unexpected credential payload in {creds_path}")
    return creds


def _real_access_token() -> str:
    global _REAL_ACCESS_TOKEN
    if _REAL_ACCESS_TOKEN:
        return _REAL_ACCESS_TOKEN

    creds = _load_real_authorized_user_credentials()
    form = urlencode(
        {
            "client_id": creds["client_id"],
            "client_secret": creds["client_secret"],
            "refresh_token": creds["refresh_token"],
            "grant_type": "refresh_token",
        }
    ).encode("utf-8")
    req = Request("https://oauth2.googleapis.com/token", data=form, method="POST")
    with urlopen(req) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    token = payload.get("access_token")
    if not token:
        raise RuntimeError("OAuth token response did not contain access_token")
    _REAL_ACCESS_TOKEN = str(token)
    return _REAL_ACCESS_TOKEN


def _normalize_http_success_status(status_code: int) -> int:
    return 200 if status_code == 204 else status_code


def _real_raw_http_call(
    method: str,
    path: str,
    path_param_names: list[str],
    params: dict[str, Any] | None,
    body: Any | None,
) -> CallResult:
    params = dict(params or {})
    resolved_path = path.lstrip("/")
    for p in path_param_names:
        resolved_path = resolved_path.replace("{" + p + "}", quote(str(params.get(p, "")), safe=""))

    query = {k: v for k, v in params.items() if k not in path_param_names and v is not None}
    url = f"{GOOGLE_CALENDAR_BASE_URL}/{resolved_path}"
    if query:
        url = f"{url}?{urlencode(query, doseq=True)}"

    headers = {
        "Authorization": f"Bearer {_real_access_token()}",
    }
    if body is None:
        data = b""
        headers["Content-Length"] = "0"
    else:
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = Request(url, data=data, method=method.upper(), headers=headers)
    try:
        with urlopen(req) as resp:
            raw_bytes = resp.read()
            raw_text = raw_bytes.decode("utf-8") if raw_bytes else ""
            payload = _extract_json(raw_text) if raw_text else None
            status_code = _normalize_http_success_status(resp.status)
    except HTTPError as exc:
        raw_text = exc.read().decode("utf-8")
        payload = _extract_json(raw_text)
        status_code = _normalize_http_success_status(exc.code)

    if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
        err = payload["error"]
        reason = err.get("reason")
        message = err.get("message")
        keys: list[str] = []
    elif status_code >= 400:
        reason = None
        message = raw_text or None
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


def _real_call(tokens: list[str], params: dict[str, Any] | None, body: Any | None) -> CallResult:
    cmd = [str(GWS_BIN), "calendar", *tokens]
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


def _real_call_for_endpoint(
    endpoint_id: str,
    tokens: list[str],
    method: str,
    path: str,
    path_param_names: list[str],
    params: dict[str, Any] | None,
    body: Any | None,
) -> CallResult:
    if endpoint_id in RAW_HTTP_ENDPOINTS:
        return _real_raw_http_call(method, path, path_param_names, params, body)
    return _real_call(tokens, params, body)


def _payload_signature(endpoint_id: str, payload: Any) -> dict[str, Any] | None:
    if endpoint_id == "calendar.calendars.clear":
        return {"empty": payload in (None, {}, [])}

    if endpoint_id in {"calendar.events.move", "calendar.events.quickAdd"} and isinstance(payload, dict):
        start = payload.get("start") if isinstance(payload.get("start"), dict) else {}
        end = payload.get("end") if isinstance(payload.get("end"), dict) else {}
        return {
            "status": payload.get("status"),
            "summary": payload.get("summary"),
            "start": start.get("dateTime") or start.get("date"),
            "end": end.get("dateTime") or end.get("date"),
        }

    return None


def _environment_limit_reason(endpoint_id: str, ctx: dict[str, Any]) -> str | None:
    if endpoint_id in {
        "calendar.calendarList.patch",
        "calendar.calendarList.update",
        "calendar.calendars.patch",
        "calendar.calendars.update",
    } and not ctx.get("has_writable_secondary", False):
        return "requires-writable-secondary"

    if endpoint_id == "calendar.calendars.delete" and not ctx.get("has_delete_secondary", False):
        return "requires-delete-secondary"

    if endpoint_id == "calendar.calendars.clear" and ctx.get("uses_primary_events", False):
        return "primary-clear-would-destroy-event-fixtures"

    if endpoint_id == "calendar.events.move" and not ctx.get("has_distinct_move_destination", False):
        return "requires-distinct-source-and-destination-calendars"

    return None


def _mock_call(
    client: TestClient,
    method: str,
    path: str,
    path_param_names: list[str],
    params: dict[str, Any] | None,
    body: Any | None,
) -> CallResult:
    params = dict(params or {})
    route = "/calendar/v3/" + path.lstrip("/")
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
        rc, out, err = _run([str(GWS_BIN), "calendar", *tokens, "--help"])
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


def _get_coverage_set() -> set[str]:
    data = json.loads(COVERAGE_PATH.read_text())
    return {e["id"] for e in data["endpoints"]}


def _pick_path_param(endpoint_id: str, name: str, ctx: dict[str, Any]) -> Any:
    if name == "calendarId":
        if endpoint_id.startswith("calendar.events."):
            return ctx["calendar_id_events"]
        if endpoint_id.startswith("calendar.acl."):
            return ctx["calendar_id_acl"]
        if endpoint_id == "calendar.calendars.delete":
            return ctx["calendar_id_delete"]
        if endpoint_id in {
            "calendar.calendars.patch",
            "calendar.calendars.update",
            "calendar.calendarList.patch",
            "calendar.calendarList.update",
        }:
            return ctx["calendar_id_writable"]
        if endpoint_id == "calendar.calendarList.get":
            return ctx["calendar_list_get_id"]
        if endpoint_id == "calendar.calendarList.delete":
            return ctx["calendar_list_delete_id"]
        if endpoint_id == "calendar.calendars.clear":
            return ctx["calendar_id_primary"]
        return ctx["calendar_id_read"]
    if name == "eventId":
        if endpoint_id == "calendar.events.delete":
            return ctx["event_id_delete"]
        if endpoint_id == "calendar.events.move":
            return ctx["event_id_move"]
        if endpoint_id in {"calendar.events.patch", "calendar.events.update"}:
            return ctx["event_id_patch"]
        return ctx["event_id_read"]
    if name == "ruleId":
        if endpoint_id == "calendar.acl.delete":
            return ctx["rule_id_delete"]
        return ctx["rule_id_read"]
    if name == "setting":
        return ctx.get("setting", "timezone")
    return ctx.get(name, f"{name}-value")


def _build_request(endpoint_id: str, method: str, path_params: list[str], ctx: dict[str, Any]) -> tuple[dict[str, Any], Any | None]:
    params: dict[str, Any] = {}
    body: Any | None = None

    for p in path_params:
        params[p] = _pick_path_param(endpoint_id, p, ctx)

    if endpoint_id == "calendar.events.list":
        params["maxResults"] = 5
    elif endpoint_id == "calendar.events.move":
        params["destination"] = ctx["move_destination_calendar_id"]
    elif endpoint_id == "calendar.events.quickAdd":
        params["text"] = "Lunch tomorrow noon"

    if method in {"POST", "PUT", "PATCH"}:
        if endpoint_id == "calendar.acl.insert":
            body = {"role": "reader", "scope": {"type": "user", "value": "parity-user@example.com"}}
        elif endpoint_id == "calendar.acl.patch":
            body = {"role": "writer"}
        elif endpoint_id == "calendar.acl.update":
            body = {
                "role": "reader",
                "scope": {"type": "user", "value": ctx.get("acl_update_scope_value", "parity-read@example.com")},
            }
        elif endpoint_id in {
            "calendar.acl.watch",
            "calendar.calendarList.watch",
            "calendar.events.watch",
            "calendar.settings.watch",
        }:
            body = {
                "id": f"watch-{endpoint_id.replace('.', '-')}-{ctx['suffix']}",
                "type": "web_hook",
                "address": "https://example.com/hook",
            }
        elif endpoint_id == "calendar.calendarList.insert":
            body = {"id": ctx["calendar_list_insert_id"]}
        elif endpoint_id in {"calendar.calendarList.patch", "calendar.calendarList.update"}:
            body = {"selected": False, "colorId": "4"}
        elif endpoint_id == "calendar.calendars.insert":
            body = {
                "summary": f"Compare Insert {ctx['suffix']}",
                "description": "compare",
                "timeZone": "UTC",
            }
        elif endpoint_id == "calendar.calendars.patch":
            body = {"summary": f"Compare Patch {ctx['suffix']}"}
        elif endpoint_id == "calendar.calendars.update":
            body = {
                "summary": f"Compare Update {ctx['suffix']}",
                "description": "compare-update",
                "timeZone": "UTC",
            }
        elif endpoint_id == "calendar.events.insert":
            body = ctx["event_body"]
        elif endpoint_id == "calendar.events.import":
            body = {**ctx["event_body"], "iCalUID": f"compare-{ctx['suffix']}@example.com"}
        elif endpoint_id == "calendar.events.patch":
            body = {"summary": f"Patched {ctx['suffix']}"}
        elif endpoint_id == "calendar.events.update":
            body = ctx["event_body_update"]
        elif endpoint_id == "calendar.freebusy.query":
            body = {
                "timeMin": ctx["time_min"],
                "timeMax": ctx["time_max"],
                "items": [{"id": "primary"}],
            }
        elif endpoint_id == "calendar.channels.stop":
            body = {
                "id": ctx.get("channel_id", "missing-channel"),
                "resourceId": ctx.get("channel_resource_id", "missing-resource"),
            }
    return params, body


def _seed_mock_and_client() -> TestClient:
    reset_engine()
    if MOCK_DB.exists():
        MOCK_DB.unlink()
    seed_database(scenario="default", seed=42, db_path=str(MOCK_DB))
    app = create_app(db_path=str(MOCK_DB), enable_mcp=False)
    return TestClient(app)


def _build_event_bodies(suffix: str) -> tuple[dict[str, Any], dict[str, Any], str, str]:
    start = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=1)
    end = start + timedelta(hours=1)
    event_body = {
        "summary": f"Parity Event {suffix}",
        "description": "Parity body",
        "location": "Virtual",
        "start": {"dateTime": _iso(start), "timeZone": "UTC"},
        "end": {"dateTime": _iso(end), "timeZone": "UTC"},
    }

    start2 = start + timedelta(days=1)
    end2 = start2 + timedelta(hours=2)
    event_body_update = {
        "summary": f"Parity Updated {suffix}",
        "description": "Parity updated",
        "location": "Room B",
        "start": {"dateTime": _iso(start2), "timeZone": "UTC"},
        "end": {"dateTime": _iso(end2), "timeZone": "UTC"},
    }

    return event_body, event_body_update, _iso(start), _iso(end2)


def _shared_compare_context() -> dict[str, Any]:
    suffix = datetime.now().strftime("%H%M%S")
    event_body, event_body_update, time_min, time_max = _build_event_bodies(suffix)
    return {
        "suffix": suffix,
        "setting": "timezone",
        "event_body": event_body,
        "event_body_update": event_body_update,
        "time_min": time_min,
        "time_max": time_max,
    }


def _collect_real_context(shared: dict[str, Any]) -> dict[str, Any]:
    ctx: dict[str, Any] = {
        **shared,
        "created_calendar_ids": [],
        "created_event_ids": [],
        "created_rule_ids": [],
    }

    def call(tokens: list[str], params: dict[str, Any] | None = None, body: Any | None = None) -> dict | None:
        res = _real_call(tokens, params, body)
        if isinstance(res.raw, dict):
            return res.raw
        return None

    def create_calendar(label: str) -> str | None:
        payload = call(
            ["calendars", "insert"],
            None,
            {
                "summary": f"Parity {label} {ctx['suffix']}",
                "description": "",
                "timeZone": "UTC",
            },
        )
        cal_id = payload.get("id") if isinstance(payload, dict) else None
        if cal_id:
            ctx["created_calendar_ids"].append(cal_id)
        return cal_id

    list_payload = call(["calendarList", "list"], {"maxResults": 250}) or {}
    items = list_payload.get("items", []) if isinstance(list_payload, dict) else []

    primary_id = next(
        (item.get("id") for item in items if isinstance(item, dict) and item.get("primary") and item.get("id")),
        "primary",
    )
    owned_secondary_ids = [
        item.get("id")
        for item in items
        if isinstance(item, dict)
        and item.get("id")
        and not item.get("primary")
        and item.get("accessRole") == "owner"
    ]

    writable_cal = create_calendar("Writable")
    if not writable_cal:
        writable_cal = owned_secondary_ids[0] if owned_secondary_ids else primary_id

    delete_cal = create_calendar("Delete")
    if not delete_cal:
        fallback = [cid for cid in owned_secondary_ids if cid != writable_cal]
        delete_cal = fallback[0] if fallback else writable_cal

    events_cal = create_calendar("Events")
    if not events_cal:
        fallback = [cid for cid in owned_secondary_ids if cid != delete_cal]
        events_cal = fallback[0] if fallback else primary_id

    if events_cal == delete_cal and primary_id != delete_cal:
        events_cal = primary_id

    move_destination = primary_id
    if move_destination == events_cal:
        if writable_cal != events_cal:
            move_destination = writable_cal
        else:
            fallback = [cid for cid in owned_secondary_ids if cid != events_cal]
            if fallback:
                move_destination = fallback[0]

    ctx["calendar_id_primary"] = primary_id
    ctx["calendar_id_read"] = primary_id
    ctx["calendar_id_writable"] = writable_cal
    ctx["calendar_id_delete"] = delete_cal
    ctx["calendar_id_events"] = events_cal
    ctx["calendar_id_acl"] = events_cal
    ctx["calendar_list_get_id"] = primary_id
    ctx["calendar_list_insert_id"] = "primary"  # Keep deterministic invalid case.
    ctx["calendar_list_delete_id"] = primary_id
    ctx["move_destination_calendar_id"] = move_destination
    ctx["has_writable_secondary"] = writable_cal != primary_id
    ctx["has_delete_secondary"] = delete_cal != primary_id
    ctx["uses_primary_events"] = events_cal == primary_id
    ctx["has_distinct_move_destination"] = move_destination != events_cal

    ev_read = call(["events", "insert"], {"calendarId": ctx["calendar_id_events"]}, ctx["event_body"])
    ctx["event_id_read"] = (ev_read or {}).get("id", "event-read-missing")
    if ctx["event_id_read"] != "event-read-missing":
        ctx["created_event_ids"].append(ctx["event_id_read"])

    ev_delete = call(["events", "insert"], {"calendarId": ctx["calendar_id_events"]}, ctx["event_body"])
    ctx["event_id_delete"] = (ev_delete or {}).get("id", ctx["event_id_read"])
    if ctx["event_id_delete"] and ctx["event_id_delete"] != ctx["event_id_read"]:
        ctx["created_event_ids"].append(ctx["event_id_delete"])

    ev_move = call(["events", "insert"], {"calendarId": ctx["calendar_id_events"]}, ctx["event_body"])
    ctx["event_id_move"] = (ev_move or {}).get("id", ctx["event_id_read"])
    if ctx["event_id_move"] and ctx["event_id_move"] not in ctx["created_event_ids"]:
        ctx["created_event_ids"].append(ctx["event_id_move"])

    ev_patch = call(["events", "insert"], {"calendarId": ctx["calendar_id_events"]}, ctx["event_body"])
    ctx["event_id_patch"] = (ev_patch or {}).get("id", ctx["event_id_read"])
    if ctx["event_id_patch"] and ctx["event_id_patch"] not in ctx["created_event_ids"]:
        ctx["created_event_ids"].append(ctx["event_id_patch"])

    # Ensure list(maxResults=5) returns a paginated shape in both real and mock.
    for _ in range(2):
        ev_extra = call(["events", "insert"], {"calendarId": ctx["calendar_id_events"]}, ctx["event_body"])
        extra_id = (ev_extra or {}).get("id")
        if extra_id and extra_id not in ctx["created_event_ids"]:
            ctx["created_event_ids"].append(extra_id)

    acl_read = call(["acl", "insert"], {"calendarId": ctx["calendar_id_acl"]}, {
        "role": "reader",
        "scope": {"type": "user", "value": f"parity-read-{ctx['suffix']}@example.com"},
    })
    ctx["rule_id_read"] = (acl_read or {}).get("id", "user:missing-read@example.com")
    if ctx["rule_id_read"] != "user:missing-read@example.com":
        ctx["created_rule_ids"].append(ctx["rule_id_read"])
    if isinstance(acl_read, dict):
        scope = acl_read.get("scope", {})
        if isinstance(scope, dict):
            ctx["acl_update_scope_value"] = scope.get("value", "parity-read@example.com")

    acl_delete = call(["acl", "insert"], {"calendarId": ctx["calendar_id_acl"]}, {
        "role": "reader",
        "scope": {"type": "user", "value": f"parity-delete-{ctx['suffix']}@example.com"},
    })
    ctx["rule_id_delete"] = (acl_delete or {}).get("id", ctx["rule_id_read"])
    if ctx["rule_id_delete"] and ctx["rule_id_delete"] not in ctx["created_rule_ids"]:
        ctx["created_rule_ids"].append(ctx["rule_id_delete"])

    watch = call(
        ["events", "watch"],
        {"calendarId": ctx["calendar_id_events"]},
        {
            "id": f"compare-stop-{ctx['suffix']}",
            "type": "web_hook",
            "address": "https://example.com/hook",
        },
    )
    if watch:
        ctx["channel_id"] = watch.get("id")
        ctx["channel_resource_id"] = watch.get("resourceId")

    return ctx


def _collect_mock_context(client: TestClient, shared: dict[str, Any]) -> dict[str, Any]:
    ctx: dict[str, Any] = {
        **shared,
        "created_calendar_ids": [],
        "created_event_ids": [],
        "created_rule_ids": [],
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

    def create_calendar(label: str) -> str | None:
        payload = call(
            "POST",
            "/calendar/v3/calendars",
            body={
                "summary": f"Mock Parity {label} {ctx['suffix']}",
                "description": "",
                "timeZone": "UTC",
            },
        )
        if isinstance(payload, dict) and not payload.get("error") and payload.get("id"):
            cal_id = payload["id"]
            ctx["created_calendar_ids"].append(cal_id)
            return cal_id
        return None

    list_payload = call("GET", "/calendar/v3/users/me/calendarList", params={"maxResults": 250}) or {}
    items = list_payload.get("items", []) if isinstance(list_payload, dict) else []
    primary_id = next(
        (item.get("id") for item in items if isinstance(item, dict) and item.get("primary") and item.get("id")),
        "primary",
    )
    owned_secondary_ids = [
        item.get("id")
        for item in items
        if isinstance(item, dict)
        and item.get("id")
        and not item.get("primary")
        and item.get("accessRole") == "owner"
    ]

    writable_cal = create_calendar("Writable")
    if not writable_cal:
        writable_cal = owned_secondary_ids[0] if owned_secondary_ids else primary_id

    delete_cal = create_calendar("Delete")
    if not delete_cal:
        fallback = [cid for cid in owned_secondary_ids if cid != writable_cal]
        delete_cal = fallback[0] if fallback else writable_cal

    events_cal = create_calendar("Events")
    if not events_cal:
        fallback = [cid for cid in owned_secondary_ids if cid != delete_cal]
        events_cal = fallback[0] if fallback else primary_id

    if events_cal == delete_cal and primary_id != delete_cal:
        events_cal = primary_id

    move_destination = primary_id
    if move_destination == events_cal:
        if writable_cal != events_cal:
            move_destination = writable_cal
        else:
            fallback = [cid for cid in owned_secondary_ids if cid != events_cal]
            if fallback:
                move_destination = fallback[0]

    ctx["calendar_id_primary"] = primary_id
    ctx["calendar_id_read"] = primary_id
    ctx["calendar_id_writable"] = writable_cal
    ctx["calendar_id_delete"] = delete_cal
    ctx["calendar_id_events"] = events_cal
    ctx["calendar_id_acl"] = events_cal
    ctx["calendar_list_get_id"] = primary_id
    ctx["calendar_list_insert_id"] = "primary"
    ctx["calendar_list_delete_id"] = primary_id
    ctx["move_destination_calendar_id"] = move_destination
    ctx["has_writable_secondary"] = writable_cal != primary_id
    ctx["has_delete_secondary"] = delete_cal != primary_id
    ctx["uses_primary_events"] = events_cal == primary_id
    ctx["has_distinct_move_destination"] = move_destination != events_cal

    ev_read = call("POST", f"/calendar/v3/calendars/{ctx['calendar_id_events']}/events", body=ctx["event_body"])
    ctx["event_id_read"] = (ev_read or {}).get("id", "event-read-missing")
    if ctx["event_id_read"] != "event-read-missing":
        ctx["created_event_ids"].append(ctx["event_id_read"])

    ev_delete = call("POST", f"/calendar/v3/calendars/{ctx['calendar_id_events']}/events", body=ctx["event_body"])
    ctx["event_id_delete"] = (ev_delete or {}).get("id", ctx["event_id_read"])
    if ctx["event_id_delete"] and ctx["event_id_delete"] != ctx["event_id_read"]:
        ctx["created_event_ids"].append(ctx["event_id_delete"])

    ev_move = call("POST", f"/calendar/v3/calendars/{ctx['calendar_id_events']}/events", body=ctx["event_body"])
    ctx["event_id_move"] = (ev_move or {}).get("id", ctx["event_id_read"])
    if ctx["event_id_move"] and ctx["event_id_move"] not in ctx["created_event_ids"]:
        ctx["created_event_ids"].append(ctx["event_id_move"])

    ev_patch = call("POST", f"/calendar/v3/calendars/{ctx['calendar_id_events']}/events", body=ctx["event_body"])
    ctx["event_id_patch"] = (ev_patch or {}).get("id", ctx["event_id_read"])
    if ctx["event_id_patch"] and ctx["event_id_patch"] not in ctx["created_event_ids"]:
        ctx["created_event_ids"].append(ctx["event_id_patch"])

    # Ensure list(maxResults=5) returns a paginated shape in both real and mock.
    for _ in range(2):
        ev_extra = call("POST", f"/calendar/v3/calendars/{ctx['calendar_id_events']}/events", body=ctx["event_body"])
        extra_id = (ev_extra or {}).get("id")
        if extra_id and extra_id not in ctx["created_event_ids"]:
            ctx["created_event_ids"].append(extra_id)

    acl_read = call("POST", f"/calendar/v3/calendars/{ctx['calendar_id_acl']}/acl", body={
        "role": "reader",
        "scope": {"type": "user", "value": f"parity-read-{ctx['suffix']}@example.com"},
    })
    ctx["rule_id_read"] = (acl_read or {}).get("id", "user:missing-read@example.com")
    if ctx["rule_id_read"] != "user:missing-read@example.com":
        ctx["created_rule_ids"].append(ctx["rule_id_read"])
    if isinstance(acl_read, dict):
        scope = acl_read.get("scope", {})
        if isinstance(scope, dict):
            ctx["acl_update_scope_value"] = scope.get("value", "parity-read@example.com")

    acl_delete = call("POST", f"/calendar/v3/calendars/{ctx['calendar_id_acl']}/acl", body={
        "role": "reader",
        "scope": {"type": "user", "value": f"parity-delete-{ctx['suffix']}@example.com"},
    })
    ctx["rule_id_delete"] = (acl_delete or {}).get("id", ctx["rule_id_read"])
    if ctx["rule_id_delete"] and ctx["rule_id_delete"] not in ctx["created_rule_ids"]:
        ctx["created_rule_ids"].append(ctx["rule_id_delete"])

    watch = call("POST", f"/calendar/v3/calendars/{ctx['calendar_id_events']}/events/watch", body={
        "id": f"compare-stop-{ctx['suffix']}",
        "type": "web_hook",
        "address": "https://example.com/hook",
    })
    if isinstance(watch, dict):
        ctx["channel_id"] = watch.get("id")
        ctx["channel_resource_id"] = watch.get("resourceId")

    return ctx


def _cleanup_real_context(ctx: dict[str, Any]) -> None:
    # Best-effort cleanup to avoid accumulating resources.
    def call(tokens: list[str], params: dict[str, Any] | None = None, body: Any | None = None):
        _real_call(tokens, params, body)

    if ctx.get("channel_id") and ctx.get("channel_resource_id"):
        call(["channels", "stop"], None, {
            "id": ctx["channel_id"],
            "resourceId": ctx["channel_resource_id"],
        })

    for rid in ctx.get("created_rule_ids", []):
        if rid:
            call(["acl", "delete"], {"calendarId": ctx.get("calendar_id_acl", "primary"), "ruleId": rid}, None)

    for eid in ctx.get("created_event_ids", []):
        if eid:
            call(["events", "delete"], {"calendarId": ctx.get("calendar_id_events", "primary"), "eventId": eid}, None)

    for cid in reversed(ctx.get("created_calendar_ids", [])):
        if cid and cid != "primary":
            call(["calendars", "delete"], {"calendarId": cid}, None)


def main() -> int:
    if not GWS_BIN.exists():
        raise SystemExit(f"gws not found: {GWS_BIN}")

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    day = _now_suffix()
    json_out = REPORTS_DIR / f"gws_calendar_mock_real_compare_{day}.json"
    md_out = REPORTS_DIR / f"gws_calendar_mock_real_compare_{day}.md"

    coverage_ids = _get_coverage_set()
    leaves = _discover_leaf_commands()
    endpoint_ids = ["calendar." + ".".join(tokens) for tokens in leaves]

    schema_map: dict[str, dict[str, Any]] = {eid: _schema_for_id(eid) for eid in endpoint_ids}

    client = _seed_mock_and_client()
    try:
        shared = _shared_compare_context()
        real_ctx = _collect_real_context(shared)
        mock_ctx = _collect_mock_context(client, shared)

        results: list[dict[str, Any]] = []
        for endpoint_id, tokens in zip(endpoint_ids, leaves):
            schema = schema_map.get(endpoint_id, {})
            method = schema.get("httpMethod", "GET")
            path = schema.get("path", "")
            if not path:
                continue

            environment_limited = _environment_limit_reason(endpoint_id, real_ctx)
            if environment_limited:
                results.append(
                    {
                        "id": endpoint_id,
                        "in_mock_coverage": endpoint_id in coverage_ids,
                        "method": method,
                        "path": path,
                        "real": {
                            "status": None,
                            "returncode": None,
                            "keys": [],
                            "error_reason": environment_limited,
                            "error_message": None,
                            "signature": None,
                        },
                        "mock": {
                            "status": None,
                            "keys": [],
                            "error_reason": environment_limited,
                            "error_message": None,
                            "signature": None,
                        },
                        "parity": {
                            "same_status": False,
                            "same_status_class": False,
                            "same_top_keys": False,
                            "same_signature": False,
                            "tenant_limited": False,
                            "environment_limited": True,
                            "excluded_from_scoring": True,
                        },
                    }
                )
                continue

            params_meta = schema.get("parameters", {})
            path_params = [
                k
                for k, v in params_meta.items()
                if isinstance(v, dict) and v.get("location") == "path"
            ]

            real_params, real_body = _build_request(endpoint_id, method, path_params, real_ctx)
            mock_params, mock_body = _build_request(endpoint_id, method, path_params, mock_ctx)

            real = _real_call_for_endpoint(
                endpoint_id,
                tokens,
                method,
                path,
                path_params,
                real_params,
                real_body,
            )
            mock = _mock_call(client, method, path, path_params, mock_params, mock_body)

            tenant_limited = (
                endpoint_id == "calendar.calendars.insert"
                and real.status == 403
                and real.error_reason == "quotaExceeded"
            )
            excluded_from_scoring = tenant_limited

            same_status = real.status == mock.status
            same_status_class = (real.status // 100) == (mock.status // 100)
            same_top_keys = real.keys == mock.keys
            real_signature = _payload_signature(endpoint_id, real.raw)
            mock_signature = _payload_signature(endpoint_id, mock.raw)
            same_signature = real_signature == mock_signature if (real_signature is not None or mock_signature is not None) else True

            results.append(
                {
                    "id": endpoint_id,
                    "in_mock_coverage": endpoint_id in coverage_ids,
                    "method": method,
                    "path": path,
                    "real": {
                        "status": real.status,
                        "returncode": real.returncode,
                        "keys": real.keys,
                        "error_reason": real.error_reason,
                        "error_message": real.error_message,
                        "signature": real_signature,
                    },
                    "mock": {
                        "status": mock.status,
                        "keys": mock.keys,
                        "error_reason": mock.error_reason,
                        "error_message": mock.error_message,
                        "signature": mock_signature,
                    },
                    "parity": {
                        "same_status": same_status,
                        "same_status_class": same_status_class,
                        "same_top_keys": same_top_keys,
                        "same_signature": same_signature,
                        "tenant_limited": tenant_limited,
                        "environment_limited": False,
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
        "same_status": sum(1 for r in results if r["parity"]["same_status"]),
        "same_status_class": sum(1 for r in results if r["parity"]["same_status_class"]),
        "same_top_keys": sum(1 for r in results if r["parity"]["same_top_keys"]),
        "same_signature": sum(1 for r in results if r["parity"]["same_signature"]),
        "exact_parity": sum(
            1
            for r in results
            if r["parity"]["same_status"]
            and r["parity"]["same_status_class"]
            and r["parity"]["same_top_keys"]
            and r["parity"]["same_signature"]
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
        and r["parity"]["same_signature"]
    )

    payload = {
        "summary": summary,
        "results": results,
    }
    json_out.write_text(json.dumps(payload, indent=2))

    missing = [r["id"] for r in results if not r["in_mock_coverage"]]
    status_class_mismatch = [
        r
        for r in results
        if not r["parity"]["same_status_class"] and r["real"]["status"] is not None and r["mock"]["status"] is not None
    ]
    excluded = [r for r in results if r["parity"]["excluded_from_scoring"]]
    key_mismatch_2xx = [
        r
        for r in results
        if (
            r["real"]["status"] is not None
            and r["mock"]["status"] is not None
            and
            r["real"]["status"] // 100 == 2
            and r["mock"]["status"] // 100 == 2
            and not r["parity"]["same_top_keys"]
        )
    ]
    signature_mismatch = [r for r in results if not r["parity"]["same_signature"]]

    lines: list[str] = []
    lines.append(f"# gws Calendar real vs mock comparison ({day})")
    lines.append("")
    lines.append("## Summary")
    for k in (
        "total_commands",
        "missing_in_mock_coverage",
        "same_status",
        "same_status_class",
        "same_top_keys",
        "same_signature",
        "exact_parity",
        "excluded_from_scoring",
        "scored_commands",
        "exact_parity_scored",
    ):
        lines.append(f"- {k}: {summary[k]}")
    lines.append("")

    lines.append(f"## Excluded from scoring ({len(excluded)})")
    for r in excluded:
        reason = "environment-limited" if r["parity"]["environment_limited"] else "tenant-limited"
        lines.append(
            f"- {r['id']}: {reason}, real={r['real']['status']} ({r['real']['error_reason']}), "
            f"mock={r['mock']['status']}"
        )
    lines.append("")

    lines.append(f"## Missing in mock coverage ({len(missing)})")
    for x in missing:
        lines.append(f"- {x}")
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

    lines.append("## Signature mismatches")
    for r in signature_mismatch:
        lines.append(
            f"- {r['id']}: real_signature={r['real']['signature']} | mock_signature={r['mock']['signature']}"
        )
    lines.append("")

    lines.append("## Command-by-command status")
    for r in results:
        tag = (
            "OK"
            if (
                r["parity"]["same_status"]
                and r["parity"]["same_top_keys"]
                and r["parity"]["same_signature"]
            )
            else "DIFF"
        )
        lines.append(f"- {r['id']}: real={r['real']['status']}, mock={r['mock']['status']} [{tag}]")
    lines.append("")

    md_out.write_text("\n".join(lines))

    print(json.dumps({"json": str(json_out), "md": str(md_out), "summary": summary}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
