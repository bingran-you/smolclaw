#!/usr/bin/env python3
"""Systematic parity check between mock gcal endpoints and real Google Calendar API.

Checks:
1) Every implemented mock CLI route has a mapped gws command (no mapping miss).
2) gws --dry-run method/path matches the expected Calendar REST endpoint family.
3) For each mapped operation, compare real vs mock status code and top-level response keys.

Auth:
- Uses GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE if present.
- Or builds a temporary credentials file from split env vars:
  GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE_CLIENT_ID
  GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE_CLIENT_SECRET
  GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE_REFRESH_TOKEN
  GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE_TYPE
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from claw_gcal.api.app import app
from claw_gcal.models import init_db, reset_engine
from claw_gcal.seed.generator import seed_database


GCAL_PREFIX = "/calendar/v3"
EXCLUDED_ROUTE_MAP = {
    ("GET", f"{GCAL_PREFIX}/users/me/profile"),
}


@dataclass(frozen=True)
class Operation:
    op_id: str
    route_method: str
    route_template: str
    gws_resource: str
    gws_method: str
    path_pattern: str
    sample_params: dict[str, Any] | None = None
    sample_body: dict[str, Any] | None = None


OPS: list[Operation] = [
    Operation(
        op_id="calendar.calendarList.list",
        route_method="GET",
        route_template=f"{GCAL_PREFIX}/users/{{userId}}/calendarList",
        gws_resource="calendarList",
        gws_method="list",
        path_pattern=rf"^{GCAL_PREFIX}/users/[^/]+/calendarList$",
        sample_params={"userId": "me"},
    ),
    Operation(
        op_id="calendar.calendarList.get",
        route_method="GET",
        route_template=f"{GCAL_PREFIX}/users/{{userId}}/calendarList/{{calendarId}}",
        gws_resource="calendarList",
        gws_method="get",
        path_pattern=rf"^{GCAL_PREFIX}/users/[^/]+/calendarList/[^/]+$",
        sample_params={"userId": "me", "calendarId": "primary"},
    ),
    Operation(
        op_id="calendar.calendars.get",
        route_method="GET",
        route_template=f"{GCAL_PREFIX}/calendars/{{calendarId}}",
        gws_resource="calendars",
        gws_method="get",
        path_pattern=rf"^{GCAL_PREFIX}/calendars/[^/]+$",
        sample_params={"calendarId": "primary"},
    ),
    Operation(
        op_id="calendar.calendars.insert",
        route_method="POST",
        route_template=f"{GCAL_PREFIX}/calendars",
        gws_resource="calendars",
        gws_method="insert",
        path_pattern=rf"^{GCAL_PREFIX}/calendars$",
        sample_body={"summary": "smolclaw parity sample"},
    ),
    Operation(
        op_id="calendar.calendars.delete",
        route_method="DELETE",
        route_template=f"{GCAL_PREFIX}/calendars/{{calendarId}}",
        gws_resource="calendars",
        gws_method="delete",
        path_pattern=rf"^{GCAL_PREFIX}/calendars/[^/]+$",
        sample_params={"calendarId": "{CALENDAR_ID}"},
    ),
    Operation(
        op_id="calendar.events.list",
        route_method="GET",
        route_template=f"{GCAL_PREFIX}/calendars/{{calendarId}}/events",
        gws_resource="events",
        gws_method="list",
        path_pattern=rf"^{GCAL_PREFIX}/calendars/[^/]+/events$",
        sample_params={"calendarId": "primary", "maxResults": 2},
    ),
    Operation(
        op_id="calendar.events.get",
        route_method="GET",
        route_template=f"{GCAL_PREFIX}/calendars/{{calendarId}}/events/{{eventId}}",
        gws_resource="events",
        gws_method="get",
        path_pattern=rf"^{GCAL_PREFIX}/calendars/[^/]+/events/[^/]+$",
        sample_params={"calendarId": "{CALENDAR_ID}", "eventId": "{EVENT_ID}"},
    ),
    Operation(
        op_id="calendar.events.insert",
        route_method="POST",
        route_template=f"{GCAL_PREFIX}/calendars/{{calendarId}}/events",
        gws_resource="events",
        gws_method="insert",
        path_pattern=rf"^{GCAL_PREFIX}/calendars/[^/]+/events$",
        sample_params={"calendarId": "{CALENDAR_ID}"},
        sample_body={
            "summary": "smolclaw parity sample",
            "description": "created by parity test",
            "start": {"dateTime": "2026-03-10T10:00:00Z"},
            "end": {"dateTime": "2026-03-10T11:00:00Z"},
        },
    ),
    Operation(
        op_id="calendar.events.patch",
        route_method="PATCH",
        route_template=f"{GCAL_PREFIX}/calendars/{{calendarId}}/events/{{eventId}}",
        gws_resource="events",
        gws_method="patch",
        path_pattern=rf"^{GCAL_PREFIX}/calendars/[^/]+/events/[^/]+$",
        sample_params={"calendarId": "{CALENDAR_ID}", "eventId": "{EVENT_ID}"},
        sample_body={"summary": "smolclaw parity sample patched"},
    ),
    Operation(
        op_id="calendar.events.delete",
        route_method="DELETE",
        route_template=f"{GCAL_PREFIX}/calendars/{{calendarId}}/events/{{eventId}}",
        gws_resource="events",
        gws_method="delete",
        path_pattern=rf"^{GCAL_PREFIX}/calendars/[^/]+/events/[^/]+$",
        sample_params={"calendarId": "{CALENDAR_ID}", "eventId": "{EVENT_ID}"},
    ),
]


def _resolve_credentials_file() -> tuple[str, Path | None]:
    existing = os.getenv("GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE")
    if existing:
        return existing, None

    cid = os.getenv("GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE_CLIENT_ID")
    csecret = os.getenv("GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE_CLIENT_SECRET")
    rtok = os.getenv("GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE_REFRESH_TOKEN")
    ctype = os.getenv("GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE_TYPE", "authorized_user")

    if not (cid and csecret and rtok):
        raise RuntimeError(
            "Missing credentials: set GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE or split credential env vars"
        )

    payload = {
        "client_id": cid,
        "client_secret": csecret,
        "refresh_token": rtok,
        "type": ctype,
    }

    fd, path = tempfile.mkstemp(prefix="gws-creds-", suffix=".json")
    os.close(fd)
    p = Path(path)
    p.write_text(json.dumps(payload), encoding="utf-8")
    return str(p), p


def _get_access_token(credentials_file: str) -> str:
    cred = json.loads(Path(credentials_file).read_text(encoding="utf-8"))
    r = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": cred["client_id"],
            "client_secret": cred["client_secret"],
            "refresh_token": cred["refresh_token"],
            "grant_type": "refresh_token",
        },
        timeout=20.0,
    )
    r.raise_for_status()
    data = r.json()
    token = data.get("access_token")
    if not token:
        raise RuntimeError("Token exchange failed: no access_token")
    return token


def _run_gws_dry_run(
    op: Operation,
    params: dict[str, Any] | None,
    body: dict[str, Any] | None,
    env: dict[str, str],
) -> dict[str, Any]:
    cmd = [
        "gws",
        "calendar",
        op.gws_resource,
        op.gws_method,
        "--dry-run",
    ]
    if params:
        cmd += ["--params", json.dumps(params, separators=(",", ":"))]
    if body:
        cmd += ["--json", json.dumps(body, separators=(",", ":"))]

    proc = subprocess.run(
        cmd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"dry-run failed for {op.op_id}: {proc.stderr.strip()}")

    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"dry-run produced non-JSON output for {op.op_id}: {proc.stdout!r}") from exc


def _parse_json_or_none(response: httpx.Response) -> Any:
    if not response.content:
        return None

    ctype = (response.headers.get("content-type") or "").lower()
    if "application/json" in ctype:
        return response.json()

    text = response.text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _request_real(dry: dict[str, Any], token: str) -> tuple[int, Any]:
    headers = {"Authorization": f"Bearer {token}"}
    method = dry["method"]
    url = dry["url"]
    params = dry.get("query_params") or None
    body = dry.get("body")

    with httpx.Client(timeout=30.0) as client:
        resp = client.request(method, url, params=params, json=body, headers=headers)
    return resp.status_code, _parse_json_or_none(resp)


def _request_mock(dry: dict[str, Any], client: TestClient) -> tuple[int, Any]:
    method = dry["method"]
    path = urlparse(dry["url"]).path
    params = dry.get("query_params") or None
    body = dry.get("body")

    resp = client.request(
        method,
        path,
        params=params,
        json=body,
        headers={"X-Claw-Gcal-User": "user1"},
    )
    return resp.status_code, resp.json() if resp.content else None


def _top_level_keys(payload: Any) -> list[str]:
    if isinstance(payload, dict):
        return sorted(payload.keys())
    return []


def _discover_mock_routes() -> set[tuple[str, str]]:
    routes: set[tuple[str, str]] = set()
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if not route.path.startswith(GCAL_PREFIX):
            continue

        for method in route.methods:
            if method in {"HEAD", "OPTIONS"}:
                continue
            tup = (method, route.path)
            if tup in EXCLUDED_ROUTE_MAP:
                continue
            routes.add(tup)
    return routes


def _manifest_routes() -> set[tuple[str, str]]:
    return {(op.route_method, op.route_template) for op in OPS}


def _validate_mapping_completeness() -> dict[str, Any]:
    discovered = _discover_mock_routes()
    manifest = _manifest_routes()

    unmapped = sorted(discovered - manifest)
    stale = sorted(manifest - discovered)

    return {
        "discovered_routes": sorted([{"method": m, "path": p} for m, p in discovered], key=lambda x: (x["path"], x["method"])),
        "manifest_routes": sorted([{"method": m, "path": p} for m, p in manifest], key=lambda x: (x["path"], x["method"])),
        "unmapped_routes": [{"method": m, "path": p} for m, p in unmapped],
        "stale_manifest_routes": [{"method": m, "path": p} for m, p in stale],
        "no_miss": not unmapped and not stale,
    }


def _resolve_placeholders(template: dict[str, Any] | None, values: dict[str, str]) -> dict[str, Any] | None:
    if template is None:
        return None

    def resolve(v: Any) -> Any:
        if isinstance(v, str):
            return values.get(v, v)
        if isinstance(v, dict):
            return {k: resolve(x) for k, x in v.items()}
        if isinstance(v, list):
            return [resolve(x) for x in v]
        return v

    return resolve(template)


def _mapping_inputs_for(op: Operation, ids: dict[str, str], suffix: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    base = {
        "{USER_ID}": "me",
        "{CALENDAR_ID}": ids.get("calendar_id", "primary"),
        "{EVENT_ID}": ids.get("event_id", "sample_event_id"),
        "{SUFFIX}": suffix,
    }

    params = _resolve_placeholders(op.sample_params, base)
    body = _resolve_placeholders(op.sample_body, base)

    return params, body


def _path_method_matches(op: Operation, dry: dict[str, Any]) -> tuple[bool, str]:
    method_ok = dry.get("method") == op.route_method
    path = urlparse(dry.get("url", "")).path
    path_ok = re.match(op.path_pattern, path) is not None

    if method_ok and path_ok:
        return True, ""

    msg = f"expected {op.route_method} {op.path_pattern}, got {dry.get('method')} {path}"
    return False, msg


def run(output: Path | None = None) -> dict[str, Any]:
    credentials_file, temp_creds_path = _resolve_credentials_file()
    account = os.getenv("GOOGLE_WORKSPACE_CLI_ACCOUNT", "")
    if not account:
        raise RuntimeError("GOOGLE_WORKSPACE_CLI_ACCOUNT is required")

    env = os.environ.copy()
    env["GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE"] = credentials_file
    env["GOOGLE_WORKSPACE_CLI_ACCOUNT"] = account

    db_tmp = tempfile.TemporaryDirectory(prefix="smolclaw-gcal-parity-")
    db_path = str(Path(db_tmp.name) / "gcal_parity.db")

    reset_engine()
    seed_database(scenario="default", seed=42, db_path=db_path)
    reset_engine()
    init_db(db_path)

    token = _get_access_token(credentials_file)

    suffix = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")

    ids_real = {"calendar_id": "primary", "event_id": "sample_event_id"}
    ids_mock = {"calendar_id": "primary", "event_id": "sample_event_id"}

    mapping_completeness = _validate_mapping_completeness()

    ordered_ops = [
        "calendar.calendarList.list",
        "calendar.calendarList.get",
        "calendar.calendars.get",
        "calendar.calendars.insert",
        "calendar.events.list",
        "calendar.events.insert",
        "calendar.events.get",
        "calendar.events.patch",
        "calendar.events.delete",
        "calendar.calendars.delete",
    ]
    op_index = {op.op_id: op for op in OPS}

    results: list[dict[str, Any]] = []

    with TestClient(app) as mock_client:
        for op_id in ordered_ops:
            op = op_index[op_id]

            # Build real/mock-specific inputs where IDs may differ.
            real_params, real_body = _mapping_inputs_for(op, ids_real, suffix)
            mock_params, mock_body = _mapping_inputs_for(op, ids_mock, suffix)

            # For create/patch payloads we embed suffix so there is no collision.
            if real_body and "summary" in real_body:
                real_body = {**real_body, "summary": f"{real_body['summary']}-{suffix}-real"}
            if mock_body and "summary" in mock_body:
                mock_body = {**mock_body, "summary": f"{mock_body['summary']}-{suffix}-mock"}

            dry_real = _run_gws_dry_run(op, real_params, real_body, env)
            dry_mock = _run_gws_dry_run(op, mock_params, mock_body, env)

            map_ok_real, map_msg_real = _path_method_matches(op, dry_real)
            map_ok_mock, map_msg_mock = _path_method_matches(op, dry_mock)
            map_ok = map_ok_real and map_ok_mock

            real_status, real_payload = _request_real(dry_real, token)
            mock_status, mock_payload = _request_mock(dry_mock, mock_client)

            real_keys = _top_level_keys(real_payload)
            mock_keys = _top_level_keys(mock_payload)

            status_ok = real_status == mock_status
            keys_ok = set(real_keys) == set(mock_keys)

            results.append(
                {
                    "op_id": op_id,
                    "real": {
                        "status": real_status,
                        "keys": real_keys,
                    },
                    "mock": {
                        "status": mock_status,
                        "keys": mock_keys,
                    },
                    "status_ok": status_ok,
                    "keys_ok": keys_ok,
                    "map_ok": map_ok,
                    "map_detail": {
                        "real": map_msg_real,
                        "mock": map_msg_mock,
                    },
                    "key_diff": {
                        "missing_in_mock": sorted(list(set(real_keys) - set(mock_keys))),
                        "extra_in_mock": sorted(list(set(mock_keys) - set(real_keys))),
                    },
                }
            )

            # Capture IDs for subsequent operations.
            if op_id == "calendar.calendars.insert":
                if isinstance(real_payload, dict) and real_payload.get("id"):
                    ids_real["calendar_id"] = str(real_payload["id"])
                if isinstance(mock_payload, dict) and mock_payload.get("id"):
                    ids_mock["calendar_id"] = str(mock_payload["id"])
            if op_id == "calendar.events.insert":
                if isinstance(real_payload, dict) and real_payload.get("id"):
                    ids_real["event_id"] = str(real_payload["id"])
                if isinstance(mock_payload, dict) and mock_payload.get("id"):
                    ids_mock["event_id"] = str(mock_payload["id"])

    summary = {
        "total": len(results),
        "status_ok": sum(1 for r in results if r["status_ok"]),
        "keys_ok": sum(1 for r in results if r["keys_ok"]),
        "map_ok": sum(1 for r in results if r["map_ok"]),
        "no_command_miss": mapping_completeness["no_miss"],
    }

    report = {
        "summary": summary,
        "mapping": mapping_completeness,
        "results": results,
        "context": {
            "account": account,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "mock_db": db_path,
        },
    }

    text = json.dumps(report, indent=2, ensure_ascii=False)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
    print(text)

    # Best-effort secret hygiene for temp credentials file.
    if temp_creds_path and temp_creds_path.exists():
        try:
            temp_creds_path.write_text("", encoding="utf-8")
            temp_creds_path.unlink(missing_ok=True)
        except OSError:
            pass

    db_tmp.cleanup()
    reset_engine()

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run gcal mock-vs-real parity checks")
    parser.add_argument("--output", type=Path, default=None, help="Optional JSON report path")
    args = parser.parse_args()

    try:
        report = run(output=args.output)
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1

    summary = report["summary"]
    all_ok = (
        summary["total"] == summary["status_ok"] == summary["keys_ok"] == summary["map_ok"]
        and summary["no_command_miss"]
    )
    return 0 if all_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
