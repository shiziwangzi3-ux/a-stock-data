from __future__ import annotations

import contextlib
import importlib
import io
import json
import os
import sys
import tempfile
import threading
import traceback
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_RUN_LOCK = threading.Lock()


def _run_pipeline() -> dict[str, Any]:
    """
    Run the existing repository scripts on demand.

    The scripts write latest.json and technical.json using relative paths,
    so each request gets an isolated temporary working directory.
    A process lock prevents concurrent requests from changing cwd at once.
    """
    with _RUN_LOCK:
        import update_data
        import technical_data

        # Refresh module-level NOW on every request, including warm instances.
        update_data = importlib.reload(update_data)
        technical_data = importlib.reload(technical_data)

        original_cwd = Path.cwd()
        stdout_buffer = io.StringIO()

        with tempfile.TemporaryDirectory(prefix="a_stock_live_", dir="/tmp") as tmp:
            workdir = Path(tmp)
            try:
                os.chdir(workdir)

                with contextlib.redirect_stdout(stdout_buffer):
                    update_data.main()
                    technical_data.main()

                latest_path = workdir / "latest.json"
                technical_path = workdir / "technical.json"

                if not latest_path.exists():
                    raise RuntimeError("latest.json was not generated")
                if not technical_path.exists():
                    raise RuntimeError("technical.json was not generated")

                latest = json.loads(latest_path.read_text(encoding="utf-8"))
                technical = json.loads(technical_path.read_text(encoding="utf-8"))

                return {
                    "ok": bool(
                        latest.get("allowTradeAnalysis")
                        and technical.get("allowTechnicalAnalysis")
                    ),
                    "latest": latest,
                    "technical": technical,
                }
            finally:
                os.chdir(original_cwd)


class handler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        try:
            payload = _run_pipeline()
            self._send_json(200, payload)
        except Exception as exc:
            self._send_json(
                502,
                {
                    "ok": False,
                    "error": str(exc),
                    "trace": traceback.format_exc(limit=6),
                },
            )
