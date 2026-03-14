#!/usr/bin/env python3
"""Compare mock Google Docs/Drive endpoints against the real gws CLI."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ACCOUNT = "dowhiz@deep-tutor.com"
MOCK_USER_ID = "user1"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@dataclass
class OperationResult:
    ok: bool
    transport: str
    method: str
    target: str
    status_code: int | None
    body: Any
    stdout: str
    stderr: str
    output_path: str | None = None
    output_preview: str | None = None


@dataclass
class CaseResult:
    endpoint_id: str
    real_cli: str
    mock_request: str
    real: OperationResult
    mock: OperationResult
    issues: list[str]


def _json_dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=True)


def _load_json_maybe(text: str) -> Any:
    stripped = text.strip()
    if not stripped:
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return stripped


def _shape_issues(real: Any, mock: Any, path: str = "$") -> list[str]:
    issues: list[str] = []
    if isinstance(real, dict) and isinstance(mock, dict):
        real_keys = set(real)
        mock_keys = set(mock)
        missing = sorted(real_keys - mock_keys)
        extra = sorted(mock_keys - real_keys)
        if missing:
            issues.append(f"{path}: mock missing keys {missing}")
        if extra:
            issues.append(f"{path}: mock has extra keys {extra}")
        for key in sorted(real_keys & mock_keys):
            issues.extend(_shape_issues(real[key], mock[key], f"{path}.{key}"))
        return issues

    if isinstance(real, list) and isinstance(mock, list):
        if not real and not mock:
            return issues
        if not real:
            return issues
        if not mock:
            issues.append(f"{path}: real list is non-empty but mock list is empty")
            return issues
        issues.extend(_shape_issues(real[0], mock[0], f"{path}[0]"))
        return issues

    if type(real) is not type(mock):
        issues.append(f"{path}: type mismatch real={type(real).__name__} mock={type(mock).__name__}")
    return issues


def _body_text(document: dict[str, Any]) -> str | None:
    try:
        return document["body"]["content"][1]["paragraph"]["elements"][0]["textRun"]["content"]
    except Exception:
        return None


def _summarize_output(path: Path) -> str:
    data = path.read_bytes()
    preview = data[:160]
    try:
        text = preview.decode("utf-8")
        text = text.replace("\n", "\\n")
        return f"{len(data)} bytes: {text}"
    except UnicodeDecodeError:
        return f"{len(data)} bytes (binary)"


class CompareRunner:
    def __init__(self, account: str, verbose: bool = False):
        self.account = account
        self.verbose = verbose
        self.tmpdir = Path(tempfile.mkdtemp(prefix="gws-gdoc-compare-"))
        self.db_path = self.tmpdir / "gdoc_compare.db"
        self.mock_port = self._free_port()
        self.mock_base = f"http://127.0.0.1:{self.mock_port}"
        self.mock_headers = {"X-Claw-Gdoc-User": self.account}
        self.mock_server: subprocess.Popen[str] | None = None
        self.client = httpx.Client(base_url=self.mock_base, timeout=30.0)
        self.real_env = os.environ.copy()
        self.real_env["GOOGLE_WORKSPACE_CLI_ACCOUNT"] = self.account
        self.profile: dict[str, Any] = {}
        self.ctx: dict[str, Any] = {
            "title_prefix": f"smolclaw-compare-{int(time.time())}",
        }

    def _log(self, message: str) -> None:
        if self.verbose:
            print(message, file=sys.stderr)

    @staticmethod
    def _free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    def close(self) -> None:
        self.client.close()
        if self.mock_server and self.mock_server.poll() is None:
            self.mock_server.terminate()
            try:
                self.mock_server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.mock_server.kill()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def setup(self) -> None:
        self.profile = self._fetch_real_profile()
        self._seed_mock()
        self._start_mock_server()

    def _fetch_real_profile(self) -> dict[str, Any]:
        result = self.run_gws(
            ["drive", "about", "get"],
            params={"fields": "user"},
        )
        if not result.ok or not isinstance(result.body, dict):
            raise RuntimeError(f"Unable to verify gws auth for {self.account}: {result.stdout or result.stderr}")
        return result.body.get("user", {})

    def _seed_mock(self) -> None:
        from claw_gdoc.models import User, get_session_factory, init_db, reset_engine
        from claw_gdoc.seed.generator import seed_database

        reset_engine()
        seed_database(scenario="default", seed=42, db_path=str(self.db_path), num_users=1)
        reset_engine()
        init_db(str(self.db_path))
        session = get_session_factory(str(self.db_path))()
        try:
            user = session.query(User).filter(User.id == MOCK_USER_ID).one()
            user.email_address = self.account
            user.display_name = self.profile.get("displayName") or self.account
            session.commit()
        finally:
            session.close()
            reset_engine()

    def _start_mock_server(self) -> None:
        cmd = [
            sys.executable,
            "-m",
            "claw_gdoc.cli",
            "--db",
            str(self.db_path),
            "serve",
            "--port",
            str(self.mock_port),
            "--no-mcp",
        ]
        self.mock_server = subprocess.Popen(
            cmd,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        deadline = time.time() + 20
        captured = []
        while time.time() < deadline:
            if self.mock_server.poll() is not None:
                output = self.mock_server.stdout.read() if self.mock_server.stdout else ""
                raise RuntimeError(f"Mock server exited early:\n{output}")
            try:
                response = self.client.get("/health")
                if response.status_code == 200:
                    return
            except Exception:
                pass
            if self.mock_server.stdout:
                line = self.mock_server.stdout.readline()
                if line:
                    captured.append(line)
            time.sleep(0.2)
        raise RuntimeError("Timed out waiting for mock gdoc server to start.\n" + "".join(captured))

    def run_gws(
        self,
        command: list[str],
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        output_name: str | None = None,
    ) -> OperationResult:
        argv = ["gws", *command]
        if params is not None:
            argv += ["--params", _json_dumps(params)]
        if json_body is not None:
            argv += ["--json", _json_dumps(json_body)]

        output_path: Path | None = None
        if output_name:
            output_path = self.tmpdir / output_name
            argv += ["--output", str(output_path)]

        proc = subprocess.run(
            argv,
            cwd=self.tmpdir,
            env=self.real_env,
            capture_output=True,
            text=True,
        )
        body = _load_json_maybe(proc.stdout)
        status_code = None
        if proc.returncode == 0:
            status_code = 200
        elif isinstance(body, dict):
            status_code = body.get("error", {}).get("code")

        output_preview = None
        if output_path and output_path.exists():
            output_preview = _summarize_output(output_path)

        return OperationResult(
            ok=proc.returncode == 0,
            transport="gws",
            method="CLI",
            target=" ".join(argv),
            status_code=status_code,
            body=body,
            stdout=proc.stdout,
            stderr=proc.stderr,
            output_path=str(output_path) if output_path and output_path.exists() else None,
            output_preview=output_preview,
        )

    def run_mock(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        expect_binary: bool = False,
    ) -> OperationResult:
        response = self.client.request(
            method,
            path,
            params=params,
            json=json_body,
            headers=self.mock_headers,
        )
        content_type = response.headers.get("content-type", "")
        if response.status_code == 204 or not response.content:
            body = None
        elif not expect_binary and "application/json" in content_type:
            body: Any = response.json()
        elif not expect_binary and content_type.startswith("text/"):
            body = response.text
        else:
            body = response.content
        preview = None
        if isinstance(body, bytes):
            preview = f"{len(body)} bytes (binary)" if expect_binary else _summarize_bytes(body)
        elif isinstance(body, str):
            preview = body[:160].replace("\n", "\\n")

        return OperationResult(
            ok=response.is_success,
            transport="mock-http",
            method=method.upper(),
            target=path,
            status_code=response.status_code,
            body=body,
            stdout=response.text if "text/" in content_type or "json" in content_type else "",
            stderr="",
            output_preview=preview,
        )

    def compare_case(
        self,
        endpoint_id: str,
        real_cli: str,
        mock_request: str,
        real: OperationResult,
        mock: OperationResult,
        *,
        semantic_checks: list[str] | None = None,
    ) -> CaseResult:
        issues: list[str] = []
        if real.ok != mock.ok:
            issues.append(f"success mismatch: real_ok={real.ok} mock_ok={mock.ok}")
        if real.status_code and mock.status_code and real.status_code != mock.status_code:
            issues.append(f"status mismatch: real={real.status_code} mock={mock.status_code}")
        if isinstance(real.body, dict) and isinstance(mock.body, dict):
            issues.extend(_shape_issues(real.body, mock.body))
        if semantic_checks:
            issues.extend(semantic_checks)
        return CaseResult(
            endpoint_id=endpoint_id,
            real_cli=real_cli,
            mock_request=mock_request,
            real=real,
            mock=mock,
            issues=issues,
        )

    def run(self) -> dict[str, Any]:
        success_cases = self._run_success_cases()
        behavior_cases = self._run_behavior_cases()
        all_cases = success_cases + behavior_cases
        summary = {
            "account": self.account,
            "mock_base_url": self.mock_base,
            "commands_verified": len(success_cases),
            "behavior_checks": len(behavior_cases),
            "diff_cases": len([case for case in all_cases if case.issues]),
            "cases": [self._case_to_dict(case) for case in all_cases],
        }
        return summary

    def _case_to_dict(self, case: CaseResult) -> dict[str, Any]:
        return {
            "endpoint_id": case.endpoint_id,
            "real_cli": case.real_cli,
            "mock_request": case.mock_request,
            "issues": case.issues,
            "real": {
                "ok": case.real.ok,
                "status_code": case.real.status_code,
                "output_preview": case.real.output_preview,
                "body": case.real.body,
            },
            "mock": {
                "ok": case.mock.ok,
                "status_code": case.mock.status_code,
                "output_preview": case.mock.output_preview,
                "body": case.mock.body if not isinstance(case.mock.body, bytes) else f"{len(case.mock.body)} bytes",
            },
        }

    def _run_success_cases(self) -> list[CaseResult]:
        prefix = self.ctx["title_prefix"]
        file_watch_id = str(uuid.uuid4())
        changes_watch_id = str(uuid.uuid4())
        channel_address = "https://example.com/webhook"

        start_real = self.run_gws(["drive", "changes", "getStartPageToken"])
        start_mock = self.run_mock("GET", "/drive/v3/changes/startPageToken")
        cases: list[CaseResult] = [
            self.compare_case(
                "drive.changes.getStartPageToken",
                "gws drive changes getStartPageToken",
                "GET /drive/v3/changes/startPageToken",
                start_real,
                start_mock,
            )
        ]
        self.ctx["real_start_token"] = start_real.body.get("startPageToken") if isinstance(start_real.body, dict) else None
        self.ctx["mock_start_token"] = start_mock.body.get("startPageToken") if isinstance(start_mock.body, dict) else None

        docs_create_body = {
            "title": f"{prefix}-docs-primary",
            "body": {"content": [{"startIndex": 1, "endIndex": 5}]},
        }
        real_docs_create = self.run_gws(["docs", "documents", "create"], json_body=docs_create_body)
        mock_docs_create = self.run_mock("POST", "/v1/documents", json_body=docs_create_body)
        self.ctx["real_doc_id"] = real_docs_create.body["documentId"]
        self.ctx["mock_doc_id"] = mock_docs_create.body["documentId"]
        cases.append(
            self.compare_case(
                "docs.documents.create",
                f"gws docs documents create --json '{_json_dumps(docs_create_body)}'",
                "POST /v1/documents",
                real_docs_create,
                mock_docs_create,
                semantic_checks=self._check_create_ignored_body(real_docs_create, mock_docs_create),
            )
        )

        get_params = {"documentId": self.ctx["real_doc_id"]}
        real_docs_get = self.run_gws(["docs", "documents", "get"], params=get_params)
        mock_docs_get = self.run_mock("GET", f"/v1/documents/{self.ctx['mock_doc_id']}")
        cases.append(
            self.compare_case(
                "docs.documents.get",
                f"gws docs documents get --params '{_json_dumps(get_params)}'",
                f"GET /v1/documents/{self.ctx['mock_doc_id']}",
                real_docs_get,
                mock_docs_get,
            )
        )

        batch_body = {"requests": [{"insertText": {"location": {"index": 1}, "text": "Intro: "}}]}
        real_batch = self.run_gws(
            ["docs", "documents", "batchUpdate"],
            params={"documentId": self.ctx["real_doc_id"]},
            json_body=batch_body,
        )
        mock_batch = self.run_mock(
            "POST",
            f"/v1/documents/{self.ctx['mock_doc_id']}:batchUpdate",
            json_body=batch_body,
        )
        cases.append(
            self.compare_case(
                "docs.documents.batchUpdate",
                f"gws docs documents batchUpdate --params '{_json_dumps({'documentId': self.ctx['real_doc_id']})}' --json '{_json_dumps(batch_body)}'",
                f"POST /v1/documents/{self.ctx['mock_doc_id']}:batchUpdate",
                real_batch,
                mock_batch,
            )
        )

        drive_create_body = {
            "name": f"{prefix}-drive-primary",
            "description": "Created by compare script",
            "mimeType": "application/vnd.google-apps.document",
        }
        real_drive_create = self.run_gws(["drive", "files", "create"], json_body=drive_create_body)
        mock_drive_create = self.run_mock("POST", "/drive/v3/files", json_body=drive_create_body)
        self.ctx["real_drive_file_id"] = real_drive_create.body["id"]
        self.ctx["mock_drive_file_id"] = mock_drive_create.body["id"]
        cases.append(
            self.compare_case(
                "drive.files.create",
                f"gws drive files create --json '{_json_dumps(drive_create_body)}'",
                "POST /drive/v3/files",
                real_drive_create,
                mock_drive_create,
            )
        )

        list_query = {
            "q": f"name = '{drive_create_body['name']}' and mimeType = 'application/vnd.google-apps.document'",
        }
        real_list = self.run_gws(["drive", "files", "list"], params=list_query)
        mock_list = self.run_mock("GET", "/drive/v3/files", params=list_query)
        cases.append(
            self.compare_case(
                "drive.files.list",
                f"gws drive files list --params '{_json_dumps(list_query)}'",
                "GET /drive/v3/files",
                real_list,
                mock_list,
            )
        )

        real_get = self.run_gws(
            ["drive", "files", "get"],
            params={"fileId": self.ctx["real_drive_file_id"]},
        )
        mock_get = self.run_mock("GET", f"/drive/v3/files/{self.ctx['mock_drive_file_id']}")
        cases.append(
            self.compare_case(
                "drive.files.get",
                f"gws drive files get --params '{_json_dumps({'fileId': self.ctx['real_drive_file_id']})}'",
                f"GET /drive/v3/files/{self.ctx['mock_drive_file_id']}",
                real_get,
                mock_get,
            )
        )

        copy_body = {"name": f"{prefix}-drive-copy"}
        real_copy = self.run_gws(
            ["drive", "files", "copy"],
            params={"fileId": self.ctx["real_drive_file_id"]},
            json_body=copy_body,
        )
        mock_copy = self.run_mock(
            "POST",
            f"/drive/v3/files/{self.ctx['mock_drive_file_id']}/copy",
            json_body=copy_body,
        )
        self.ctx["real_copy_file_id"] = real_copy.body["id"]
        self.ctx["mock_copy_file_id"] = mock_copy.body["id"]
        cases.append(
            self.compare_case(
                "drive.files.copy",
                f"gws drive files copy --params '{_json_dumps({'fileId': self.ctx['real_drive_file_id']})}' --json '{_json_dumps(copy_body)}'",
                f"POST /drive/v3/files/{self.ctx['mock_drive_file_id']}/copy",
                real_copy,
                mock_copy,
            )
        )

        update_body = {"name": f"{prefix}-drive-renamed", "description": "Updated by compare script", "trashed": True}
        real_update = self.run_gws(
            ["drive", "files", "update"],
            params={"fileId": self.ctx["real_drive_file_id"]},
            json_body=update_body,
        )
        mock_update = self.run_mock(
            "PATCH",
            f"/drive/v3/files/{self.ctx['mock_drive_file_id']}",
            json_body=update_body,
        )
        cases.append(
            self.compare_case(
                "drive.files.update",
                f"gws drive files update --params '{_json_dumps({'fileId': self.ctx['real_drive_file_id']})}' --json '{_json_dumps(update_body)}'",
                f"PATCH /drive/v3/files/{self.ctx['mock_drive_file_id']}",
                real_update,
                mock_update,
            )
        )

        real_export = self.run_gws(
            ["drive", "files", "export"],
            params={"fileId": self.ctx["real_doc_id"], "mimeType": "text/plain"},
            output_name="real-export.txt",
        )
        mock_export = self.run_mock(
            "GET",
            f"/drive/v3/files/{self.ctx['mock_doc_id']}/export",
            params={"mimeType": "text/plain"},
        )
        cases.append(
            self.compare_case(
                "drive.files.export",
                f"gws drive files export --params '{_json_dumps({'fileId': self.ctx['real_doc_id'], 'mimeType': 'text/plain'})}' --output real-export.txt",
                f"GET /drive/v3/files/{self.ctx['mock_doc_id']}/export?mimeType=text/plain",
                real_export,
                mock_export,
                semantic_checks=self._check_export_text(real_export, mock_export),
            )
        )

        watch_body = {"id": file_watch_id, "type": "web_hook", "address": channel_address, "token": "smolclaw-file"}
        real_file_watch = self.run_gws(
            ["drive", "files", "watch"],
            params={"fileId": self.ctx["real_doc_id"]},
            json_body=watch_body,
        )
        mock_file_watch = self.run_mock(
            "POST",
            f"/drive/v3/files/{self.ctx['mock_doc_id']}/watch",
            json_body=watch_body,
        )
        cases.append(
            self.compare_case(
                "drive.files.watch",
                f"gws drive files watch --params '{_json_dumps({'fileId': self.ctx['real_doc_id']})}' --json '{_json_dumps(watch_body)}'",
                f"POST /drive/v3/files/{self.ctx['mock_doc_id']}/watch",
                real_file_watch,
                mock_file_watch,
            )
        )
        self.ctx["real_file_watch"] = real_file_watch.body
        self.ctx["mock_file_watch"] = mock_file_watch.body

        real_revisions_list = self.run_gws(
            ["drive", "revisions", "list"],
            params={"fileId": self.ctx["real_doc_id"]},
        )
        mock_revisions_list = self.run_mock("GET", f"/drive/v3/files/{self.ctx['mock_doc_id']}/revisions")
        cases.append(
            self.compare_case(
                "drive.revisions.list",
                f"gws drive revisions list --params '{_json_dumps({'fileId': self.ctx['real_doc_id']})}'",
                f"GET /drive/v3/files/{self.ctx['mock_doc_id']}/revisions",
                real_revisions_list,
                mock_revisions_list,
            )
        )
        real_revision_id = real_revisions_list.body["revisions"][0]["id"]
        mock_revision_id = mock_revisions_list.body["revisions"][0]["id"]

        real_revision_get = self.run_gws(
            ["drive", "revisions", "get"],
            params={"fileId": self.ctx["real_doc_id"], "revisionId": real_revision_id},
        )
        mock_revision_get = self.run_mock(
            "GET",
            f"/drive/v3/files/{self.ctx['mock_doc_id']}/revisions/{mock_revision_id}",
        )
        cases.append(
            self.compare_case(
                "drive.revisions.get",
                f"gws drive revisions get --params '{_json_dumps({'fileId': self.ctx['real_doc_id'], 'revisionId': real_revision_id})}'",
                f"GET /drive/v3/files/{self.ctx['mock_doc_id']}/revisions/{mock_revision_id}",
                real_revision_get,
                mock_revision_get,
            )
        )

        real_permissions_list = self.run_gws(
            ["drive", "permissions", "list"],
            params={"fileId": self.ctx["real_doc_id"]},
        )
        mock_permissions_list = self.run_mock("GET", f"/drive/v3/files/{self.ctx['mock_doc_id']}/permissions")
        cases.append(
            self.compare_case(
                "drive.permissions.list",
                f"gws drive permissions list --params '{_json_dumps({'fileId': self.ctx['real_doc_id']})}'",
                f"GET /drive/v3/files/{self.ctx['mock_doc_id']}/permissions",
                real_permissions_list,
                mock_permissions_list,
            )
        )

        permission_body = {"type": "anyone", "role": "reader", "allowFileDiscovery": False}
        real_permission_create = self.run_gws(
            ["drive", "permissions", "create"],
            params={"fileId": self.ctx["real_doc_id"]},
            json_body=permission_body,
        )
        mock_permission_create = self.run_mock(
            "POST",
            f"/drive/v3/files/{self.ctx['mock_doc_id']}/permissions",
            json_body=permission_body,
        )
        cases.append(
            self.compare_case(
                "drive.permissions.create",
                f"gws drive permissions create --params '{_json_dumps({'fileId': self.ctx['real_doc_id']})}' --json '{_json_dumps(permission_body)}'",
                f"POST /drive/v3/files/{self.ctx['mock_doc_id']}/permissions",
                real_permission_create,
                mock_permission_create,
            )
        )
        self.ctx["real_permission_id"] = real_permission_create.body["id"]
        self.ctx["mock_permission_id"] = mock_permission_create.body["id"]

        real_permission_get = self.run_gws(
            ["drive", "permissions", "get"],
            params={"fileId": self.ctx["real_doc_id"], "permissionId": self.ctx["real_permission_id"]},
        )
        mock_permission_get = self.run_mock(
            "GET",
            f"/drive/v3/files/{self.ctx['mock_doc_id']}/permissions/{self.ctx['mock_permission_id']}",
        )
        cases.append(
            self.compare_case(
                "drive.permissions.get",
                f"gws drive permissions get --params '{_json_dumps({'fileId': self.ctx['real_doc_id'], 'permissionId': self.ctx['real_permission_id']})}'",
                f"GET /drive/v3/files/{self.ctx['mock_doc_id']}/permissions/{self.ctx['mock_permission_id']}",
                real_permission_get,
                mock_permission_get,
            )
        )

        permission_update_body = {"role": "commenter"}
        real_permission_update = self.run_gws(
            ["drive", "permissions", "update"],
            params={"fileId": self.ctx["real_doc_id"], "permissionId": self.ctx["real_permission_id"]},
            json_body=permission_update_body,
        )
        mock_permission_update = self.run_mock(
            "PATCH",
            f"/drive/v3/files/{self.ctx['mock_doc_id']}/permissions/{self.ctx['mock_permission_id']}",
            json_body=permission_update_body,
        )
        cases.append(
            self.compare_case(
                "drive.permissions.update",
                f"gws drive permissions update --params '{_json_dumps({'fileId': self.ctx['real_doc_id'], 'permissionId': self.ctx['real_permission_id']})}' --json '{_json_dumps(permission_update_body)}'",
                f"PATCH /drive/v3/files/{self.ctx['mock_doc_id']}/permissions/{self.ctx['mock_permission_id']}",
                real_permission_update,
                mock_permission_update,
            )
        )

        changes_watch_body = {
            "id": changes_watch_id,
            "type": "web_hook",
            "address": channel_address,
            "token": "smolclaw-changes",
        }
        real_changes_watch = self.run_gws(
            ["drive", "changes", "watch"],
            params={"pageToken": self.ctx["real_start_token"]},
            json_body=changes_watch_body,
        )
        mock_changes_watch = self.run_mock(
            "POST",
            "/drive/v3/changes/watch",
            params={"pageToken": self.ctx["mock_start_token"]},
            json_body=changes_watch_body,
        )
        cases.append(
            self.compare_case(
                "drive.changes.watch",
                f"gws drive changes watch --params '{_json_dumps({'pageToken': self.ctx['real_start_token']})}' --json '{_json_dumps(changes_watch_body)}'",
                f"POST /drive/v3/changes/watch?pageToken={self.ctx['mock_start_token']}",
                real_changes_watch,
                mock_changes_watch,
            )
        )
        self.ctx["real_changes_watch"] = real_changes_watch.body
        self.ctx["mock_changes_watch"] = mock_changes_watch.body

        real_changes_list = self.run_gws(
            ["drive", "changes", "list"],
            params={"pageToken": self.ctx["real_start_token"], "pageSize": 50},
        )
        mock_changes_list = self.run_mock(
            "GET",
            "/drive/v3/changes",
            params={"pageToken": self.ctx["mock_start_token"], "pageSize": 50},
        )
        cases.append(
            self.compare_case(
                "drive.changes.list",
                f"gws drive changes list --params '{_json_dumps({'pageToken': self.ctx['real_start_token'], 'pageSize': 50})}'",
                f"GET /drive/v3/changes?pageToken={self.ctx['mock_start_token']}&pageSize=50",
                real_changes_list,
                mock_changes_list,
                semantic_checks=self._check_changes_include_doc(real_changes_list, mock_changes_list),
            )
        )

        real_permission_delete = self.run_gws(
            ["drive", "permissions", "delete"],
            params={"fileId": self.ctx["real_doc_id"], "permissionId": self.ctx["real_permission_id"]},
        )
        mock_permission_delete = self.run_mock(
            "DELETE",
            f"/drive/v3/files/{self.ctx['mock_doc_id']}/permissions/{self.ctx['mock_permission_id']}",
        )
        cases.append(
            self.compare_case(
                "drive.permissions.delete",
                f"gws drive permissions delete --params '{_json_dumps({'fileId': self.ctx['real_doc_id'], 'permissionId': self.ctx['real_permission_id']})}'",
                f"DELETE /drive/v3/files/{self.ctx['mock_doc_id']}/permissions/{self.ctx['mock_permission_id']}",
                real_permission_delete,
                mock_permission_delete,
                semantic_checks=["Real gws wraps empty 204 responses as CLI success metadata; mock returns empty HTTP 204 body."],
            )
        )

        real_delete = self.run_gws(
            ["drive", "files", "delete"],
            params={"fileId": self.ctx["real_copy_file_id"]},
        )
        mock_delete = self.run_mock("DELETE", f"/drive/v3/files/{self.ctx['mock_copy_file_id']}")
        cases.append(
            self.compare_case(
                "drive.files.delete",
                f"gws drive files delete --params '{_json_dumps({'fileId': self.ctx['real_copy_file_id']})}'",
                f"DELETE /drive/v3/files/{self.ctx['mock_copy_file_id']}",
                real_delete,
                mock_delete,
                semantic_checks=["Real gws wraps empty 204 responses as CLI success metadata; mock returns empty HTTP 204 body."],
            )
        )

        real_stop = self.run_gws(
            ["drive", "channels", "stop"],
            json_body={"id": self.ctx["real_file_watch"]["id"], "resourceId": self.ctx["real_file_watch"]["resourceId"]},
        )
        mock_stop = self.run_mock(
            "POST",
            "/drive/v3/channels/stop",
            json_body={"id": self.ctx["mock_file_watch"]["id"], "resourceId": self.ctx["mock_file_watch"]["resourceId"]},
        )
        cases.append(
            self.compare_case(
                "drive.channels.stop",
                f"gws drive channels stop --json '{_json_dumps({'id': self.ctx['real_file_watch']['id'], 'resourceId': self.ctx['real_file_watch']['resourceId']})}'",
                "POST /drive/v3/channels/stop",
                real_stop,
                mock_stop,
                semantic_checks=["Real gws emits a success wrapper for empty channel stop responses; mock returns bare HTTP 204."],
            )
        )

        self._assert_endpoint_coverage(cases)
        return cases

    def _run_behavior_cases(self) -> list[CaseResult]:
        cases: list[CaseResult] = []

        invalid_revision_body = {
            "requests": [{"insertText": {"location": {"index": 1}, "text": "X"}}],
            "writeControl": {"requiredRevisionId": "999"},
        }
        real_invalid_revision = self.run_gws(
            ["docs", "documents", "batchUpdate"],
            params={"documentId": self.ctx["real_doc_id"]},
            json_body=invalid_revision_body,
        )
        mock_invalid_revision = self.run_mock(
            "POST",
            f"/v1/documents/{self.ctx['mock_doc_id']}:batchUpdate",
            json_body=invalid_revision_body,
        )
        cases.append(
            self.compare_case(
                "docs.documents.batchUpdate.failedPrecondition",
                f"gws docs documents batchUpdate --params '{_json_dumps({'documentId': self.ctx['real_doc_id']})}' --json '{_json_dumps(invalid_revision_body)}'",
                f"POST /v1/documents/{self.ctx['mock_doc_id']}:batchUpdate",
                real_invalid_revision,
                mock_invalid_revision,
            )
        )

        bad_page = {"pageToken": "oops"}
        real_bad_page = self.run_gws(["drive", "files", "list"], params=bad_page)
        mock_bad_page = self.run_mock("GET", "/drive/v3/files", params=bad_page)
        cases.append(
            self.compare_case(
                "drive.files.list.invalidPageToken",
                f"gws drive files list --params '{_json_dumps(bad_page)}'",
                "GET /drive/v3/files?pageToken=oops",
                real_bad_page,
                mock_bad_page,
            )
        )

        bad_export = {"fileId": self.ctx["real_doc_id"], "mimeType": "application/xml"}
        real_bad_export = self.run_gws(["drive", "files", "export"], params=bad_export, output_name="bad-export.bin")
        mock_bad_export = self.run_mock(
            "GET",
            f"/drive/v3/files/{self.ctx['mock_doc_id']}/export",
            params={"mimeType": "application/xml"},
        )
        cases.append(
            self.compare_case(
                "drive.files.export.invalidMimeType",
                f"gws drive files export --params '{_json_dumps(bad_export)}'",
                f"GET /drive/v3/files/{self.ctx['mock_doc_id']}/export?mimeType=application/xml",
                real_bad_export,
                mock_bad_export,
            )
        )

        missing_watch_body = {"id": "missing-page-token", "type": "web_hook", "address": "https://example.com/webhook"}
        real_missing_watch = self.run_gws(["drive", "changes", "watch"], json_body=missing_watch_body)
        mock_missing_watch = self.run_mock("POST", "/drive/v3/changes/watch", json_body=missing_watch_body)
        cases.append(
            self.compare_case(
                "drive.changes.watch.missingPageToken",
                f"gws drive changes watch --json '{_json_dumps(missing_watch_body)}'",
                "POST /drive/v3/changes/watch",
                real_missing_watch,
                mock_missing_watch,
            )
        )

        return cases

    def _assert_endpoint_coverage(self, cases: list[CaseResult]) -> None:
        spec = json.loads((ROOT / "tests" / "fixtures" / "gdoc_api_spec.json").read_text())
        implemented = {item["id"] for item in spec["endpoints"] if item["implemented"]}
        covered = {case.endpoint_id for case in cases}
        missing = sorted(implemented - covered)
        extra = sorted(covered - implemented)
        if missing or extra:
            raise RuntimeError(f"Endpoint coverage mismatch. Missing={missing}, extra={extra}")

    def _check_create_ignored_body(self, real: OperationResult, mock: OperationResult) -> list[str]:
        notes: list[str] = []
        real_text = _body_text(real.body) if isinstance(real.body, dict) else None
        mock_text = _body_text(mock.body) if isinstance(mock.body, dict) else None
        if real_text != "\n":
            notes.append(f"real create body was not blank newline: {real_text!r}")
        if mock_text != "\n":
            notes.append(f"mock create body was not blank newline: {mock_text!r}")
        return notes

    def _check_export_text(self, real: OperationResult, mock: OperationResult) -> list[str]:
        notes: list[str] = []
        real_text = ""
        if real.output_path:
            real_text = Path(real.output_path).read_text()
        mock_text = mock.body if isinstance(mock.body, str) else ""
        if "Intro:" not in real_text:
            notes.append("real export text/plain did not contain inserted text 'Intro:'")
        if "Intro:" not in mock_text:
            notes.append("mock export text/plain did not contain inserted text 'Intro:'")
        return notes

    def _check_changes_include_doc(self, real: OperationResult, mock: OperationResult) -> list[str]:
        notes: list[str] = []
        real_changes = real.body.get("changes", []) if isinstance(real.body, dict) else []
        mock_changes = mock.body.get("changes", []) if isinstance(mock.body, dict) else []
        if not any(change.get("fileId") == self.ctx["real_doc_id"] for change in real_changes):
            notes.append("real changes.list did not include the tracked docs document")
        if not any(change.get("fileId") == self.ctx["mock_doc_id"] for change in mock_changes):
            notes.append("mock changes.list did not include the tracked docs document")
        return notes


def _summarize_bytes(data: bytes) -> str:
    preview = data[:160]
    try:
        text = preview.decode("utf-8").replace("\n", "\\n")
        return f"{len(data)} bytes: {text}"
    except UnicodeDecodeError:
        return f"{len(data)} bytes (binary)"


def _print_summary(summary: dict[str, Any]) -> None:
    print(f"Account: {summary['account']}")
    print(f"Mock base: {summary['mock_base_url']}")
    print(f"Verified endpoint cases: {summary['commands_verified']}")
    print(f"Behavior checks: {summary['behavior_checks']}")
    print(f"Cases with differences: {summary['diff_cases']}")
    print()
    for case in summary["cases"]:
        status = "DIFF" if case["issues"] else "OK"
        print(f"[{status}] {case['endpoint_id']}")
        print(f"  real: {case['real_cli']}")
        print(f"  mock: {case['mock_request']}")
        if case["issues"]:
            for issue in case["issues"]:
                print(f"  - {issue}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", default=DEFAULT_ACCOUNT, help="gws account email to use")
    parser.add_argument("--json-out", default=None, help="Optional path to write the full JSON report")
    parser.add_argument("--verbose", action="store_true", help="Print setup progress to stderr")
    args = parser.parse_args()

    runner = CompareRunner(account=args.account, verbose=args.verbose)
    try:
        runner.setup()
        summary = runner.run()
    finally:
        runner.close()

    _print_summary(summary)
    if args.json_out:
        output_path = Path(args.json_out)
        output_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
        print()
        print(f"JSON report written to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
