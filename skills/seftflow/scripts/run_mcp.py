#!/usr/bin/env python
"""Thin launcher for the SeftFlow MCP server.

Runs `python -m productflow_backend.mcp_server` inside the backend's uv
environment so MCP clients (Codex CLI, Claude Desktop, Cursor) can drive
SeftFlow over stdio.

Usage:
    python skills/seftflow/scripts/run_mcp.py

Environment:
    Reuses the backend .env (DATABASE_URL, REDIS_URL, provider keys).
    Requires the `mcp` Python SDK: `uv add mcp` inside backend/.

Example prompt once connected:
    Create a new product "Summer T-shirt", write casual English copy,
    render a 1024x1024 hero image, then save the best result to the gallery.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"


def main() -> int:
    if not BACKEND_DIR.exists():
        print(f"backend/ not found at {BACKEND_DIR}", file=sys.stderr)
        return 1

    uv = shutil.which("uv")
    if uv:
        cmd = [uv, "run", "--directory", str(BACKEND_DIR), "python", "-m", "productflow_backend.mcp_server"]
    else:
        # Fallback: assume productflow_backend is importable on PYTHONPATH.
        env_src = str(BACKEND_DIR / "src")
        os.environ["PYTHONPATH"] = os.pathsep.join(
            filter(None, [env_src, os.environ.get("PYTHONPATH", "")])
        )
        cmd = [sys.executable, "-m", "productflow_backend.mcp_server"]

    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())