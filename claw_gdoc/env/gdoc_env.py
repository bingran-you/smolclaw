"""Gymnasium environment for Docs tool-use RL training."""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any

import httpx

try:
    import gymnasium as gym
    from gymnasium import spaces

    HAS_GYM = True
except ImportError:
    HAS_GYM = False

from claw_gdoc.models import reset_engine
from claw_gdoc.seed.generator import seed_database
from claw_gdoc.tasks import get_task, list_tasks


def _start_server(host: str, port: int, db_path: str):
    import uvicorn

    from claw_gdoc.server import create_app

    app = create_app(db_path=db_path, enable_mcp=False)
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    server.run()


if HAS_GYM:
    class GDocToolEnv(gym.Env):
        """Gymnasium environment for interacting with the Mock Docs API via tool calls."""

        metadata = {"render_modes": ["human"]}

        TOOLS = {
            "documents_get": ("GET", "/v1/documents/{documentId}"),
            "documents_create": ("POST", "/v1/documents"),
            "documents_batch_update": ("POST", "/v1/documents/{documentId}:batchUpdate"),
            "drive_files_list": ("GET", "/drive/v3/files"),
            "drive_files_get": ("GET", "/drive/v3/files/{fileId}"),
            "drive_files_export": ("GET", "/drive/v3/files/{fileId}/export"),
        }

        def __init__(
            self,
            task_name: str = "cap-docs-01",
            scenario: str | None = None,
            host: str = "127.0.0.1",
            port: int = 8103,
            db_path: str = "gym_gdoc.db",
            seed: int = 42,
            max_steps: int = 50,
            step_penalty: float = -0.01,
        ):
            super().__init__()

            self.task_name = task_name
            self.host = host
            self.port = port
            self.db_path = db_path
            self.seed_val = seed
            self.max_steps = max_steps
            self.step_penalty = step_penalty
            self.base_url = f"http://{host}:{port}"

            task = get_task(task_name)
            if not task:
                raise ValueError(f"Unknown task: {task_name}. Available: {list_tasks()}")
            self.task = task
            self.scenario = scenario or task.scenario

            self.action_space = spaces.Dict(
                {
                    "tool_name": spaces.Text(min_length=1, max_length=100),
                    "tool_args": spaces.Text(min_length=0, max_length=10000),
                }
            )
            self.observation_space = spaces.Dict(
                {
                    "goal": spaces.Text(min_length=0, max_length=10000),
                    "api_response": spaces.Text(min_length=0, max_length=100000),
                    "step": spaces.Discrete(max_steps + 1),
                }
            )

            self._server_thread = None
            self._step_count = 0
            self._client = None

        def reset(self, seed=None, options=None):
            if seed is not None:
                self.seed_val = seed

            reset_engine()
            if os.path.exists(self.db_path):
                os.unlink(self.db_path)

            seed_database(
                scenario=self.scenario,
                seed=self.seed_val,
                db_path=self.db_path,
            )

            if self._server_thread is None or not self._server_thread.is_alive():
                reset_engine()
                self._server_thread = threading.Thread(
                    target=_start_server,
                    args=(self.host, self.port, self.db_path),
                    daemon=True,
                )
                self._server_thread.start()
                self._wait_for_server()

            self._client = httpx.Client(base_url=self.base_url, timeout=30)
            self._step_count = 0
            self._client.post("/_admin/reset")

            obs = {
                "goal": self.task.instruction,
                "api_response": json.dumps({"status": "ready", "task": self.task_name}),
                "step": 0,
            }
            info = {"task_name": self.task_name, "scenario": self.scenario, "tools": list(self.TOOLS.keys())}
            return obs, info

        def step(self, action: dict[str, Any]):
            self._step_count += 1
            tool_name = action.get("tool_name", "")
            tool_args_raw = action.get("tool_args", "{}")

            if isinstance(tool_args_raw, str):
                try:
                    tool_args = json.loads(tool_args_raw)
                except json.JSONDecodeError:
                    tool_args = {}
            else:
                tool_args = tool_args_raw

            api_response = self._execute_tool(tool_name, tool_args)

            reward = self.step_penalty
            terminated = False
            truncated = self._step_count >= self.max_steps

            if truncated or self._should_evaluate(tool_name):
                state = self._client.get("/_admin/state").json()
                diff = self._client.get("/_admin/diff").json()
                log = self._client.get("/_admin/action_log").json().get("entries", [])
                task_reward, done = self.task.evaluate(state, diff, log)
                reward += task_reward
                terminated = done

            obs = {
                "goal": self.task.instruction,
                "api_response": json.dumps(api_response) if isinstance(api_response, dict) else str(api_response),
                "step": self._step_count,
            }
            info = {"tool_name": tool_name, "step": self._step_count}
            return obs, reward, terminated, truncated, info

        def _execute_tool(self, tool_name: str, args: dict) -> dict | str:
            if tool_name not in self.TOOLS:
                return {"error": f"Unknown tool: {tool_name}. Available: {list(self.TOOLS.keys())}"}

            method, path_template = self.TOOLS[tool_name]
            path = path_template
            for key in ["documentId", "fileId"]:
                token = "{" + key + "}"
                if token in path:
                    value = args.pop(key, None)
                    if value is None:
                        return {"error": f"Missing required path param: {key}"}
                    path = path.replace(token, str(value))

            try:
                if method == "GET":
                    resp = self._client.get(path, params=args)
                else:
                    resp = self._client.post(path, json=args)
                content_type = resp.headers.get("content-type", "")
                if "application/json" in content_type:
                    return resp.json()
                return resp.text
            except Exception as exc:
                return {"error": str(exc)}

        def _should_evaluate(self, tool_name: str) -> bool:
            return tool_name in {"documents_batch_update", "documents_create"}

        def _wait_for_server(self, timeout: float = 10.0):
            start = time.time()
            while time.time() - start < timeout:
                try:
                    resp = httpx.get(f"{self.base_url}/health", timeout=1)
                    if resp.status_code == 200:
                        return
                except Exception:
                    pass
                time.sleep(0.1)
            raise RuntimeError("Timed out waiting for GDoc server")

        def close(self):
            if self._client:
                self._client.close()

else:
    class GDocToolEnv:
        def __init__(self, *args, **kwargs):
            raise ImportError(
                "gymnasium is required for GDocToolEnv. Install with: pip install smolclaws[gym]"
            )
