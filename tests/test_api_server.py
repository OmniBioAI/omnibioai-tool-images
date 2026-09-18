"""Tests for api/server.py — covers all endpoints and the _stream_build generator.

Developer:
    Manish Kumar <manish@omnibioai.org>
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import AsyncGenerator
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).parent.parent
# Add api/ directly (avoids collision with a system-installed 'api' package)
sys.path.insert(0, str(ROOT / "api"))

import server  # noqa: E402
from server import (  # noqa: E402
    app,
    _tool_info,
    _stream_build,
    BUILD_LOGS_DIR,
    CATEGORY_MAP,
    DOCKERFILES_DIR,
    LICENSE_NEEDED,
    SKIP_TOOLS,
    SIF_DIR,
)

client = TestClient(app)


# ── helpers: pick real files from disk ───────────────────────────────────────

def _first_real_tool() -> str:
    """Return an arbitrary tool name with a real Dockerfile that isn't skip-listed."""
    for p in sorted(DOCKERFILES_DIR.glob("Dockerfile.*")):
        tool = p.name.removeprefix("Dockerfile.")
        if tool not in SKIP_TOOLS:
            return tool
    raise RuntimeError("No Dockerfiles found in dockerfiles/")


def _first_built_tool() -> str | None:
    """Return the name of an arbitrary tool with a built SIF on disk, or None."""
    for p in sorted(SIF_DIR.glob("*_arm64.sif")):
        return p.name.replace("_arm64.sif", "")
    return None


def _first_log_tool() -> str | None:
    """Return the name of an arbitrary tool with a build log on disk, or None."""
    for p in sorted(BUILD_LOGS_DIR.glob("*.log")):
        return p.stem
    return None


REAL_TOOL  = _first_real_tool()
BUILT_TOOL = _first_built_tool()
LOG_TOOL   = _first_log_tool()


# ── async helpers for stream tests ───────────────────────────────────────────

async def _fake_stdout_lines(*lines: bytes) -> AsyncGenerator[bytes, None]:
    """Yield the given byte lines, standing in for a subprocess's stdout stream."""
    for line in lines:
        yield line


async def _fake_stream_ok(cmd: list[str]) -> AsyncGenerator[dict, None]:
    """Fake _stream_build that yields one progress line then a successful "done" event."""
    yield {"data": "Building..."}
    yield {"data": "[exit code: 0]", "event": "done"}


async def _fake_stream_err(cmd: list[str]) -> AsyncGenerator[dict, None]:
    """Fake _stream_build that yields one progress line then a failing "error" event."""
    yield {"data": "Error"}
    yield {"data": "[exit code: 1]", "event": "error"}


# ── 1. Health ─────────────────────────────────────────────────────────────────

class TestHealth:
    """Health-check endpoint response shape."""

    def test_health_returns_ok(self):
        """GET /health returns 200 with {"status": "ok"}."""
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


# ── 2. List tools ─────────────────────────────────────────────────────────────

class TestListTools:
    """GET /v1/tools: response shape, field presence, and status filtering."""

    def test_returns_non_empty_list(self):
        """GET /v1/tools returns a non-empty JSON list."""
        resp = client.get("/v1/tools")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) > 0

    def test_each_tool_has_required_fields(self):
        """Each tool entry includes name, category, sif_exists, sif_filename, status, and dockerfile."""
        resp = client.get("/v1/tools")
        assert resp.status_code == 200
        tool = resp.json()[0]
        for field in ("name", "category", "sif_exists", "sif_filename",
                      "status", "dockerfile"):
            assert field in tool, f"Missing field: {field}"

    def test_skip_tools_excluded(self):
        """Tools in SKIP_TOOLS never appear in the /v1/tools response."""
        resp = client.get("/v1/tools")
        names = {t["name"] for t in resp.json()}
        for skipped in SKIP_TOOLS:
            assert skipped not in names, f"SKIP_TOOL '{skipped}' appeared in /v1/tools"

    def test_status_values_are_valid(self):
        """Every tool's status is one of "built", "missing", or "license"."""
        resp = client.get("/v1/tools")
        statuses = {t["status"] for t in resp.json()}
        assert statuses <= {"built", "missing", "license"}, f"Unexpected statuses: {statuses}"

    def test_built_tool_has_size_and_timestamp(self):
        """A tool with status "built" has non-null sif_size_mb and updated_at."""
        resp = client.get("/v1/tools")
        built = [t for t in resp.json() if t["status"] == "built"]
        if not built:
            pytest.skip("No built SIF files on disk")
        for tool in built:
            assert tool["sif_size_mb"] is not None
            assert tool["updated_at"] is not None

    def test_missing_tool_has_null_path_and_size(self):
        """A tool with status "missing" has null sif_path and sif_size_mb."""
        resp = client.get("/v1/tools")
        missing = [t for t in resp.json() if t["status"] == "missing"]
        if not missing:
            pytest.skip("All tools appear to be built")
        for tool in missing:
            assert tool["sif_path"] is None
            assert tool["sif_size_mb"] is None


# ── 3. _tool_info helper ──────────────────────────────────────────────────────

class TestToolInfo:
    """_tool_info() status classification, category lookup, and field formatting."""

    def test_built_tool_status(self):
        """A tool with a SIF on disk reports status "built" with a populated path, size, and timestamp."""
        if not BUILT_TOOL:
            pytest.skip("No SIF files on disk")
        info = _tool_info(BUILT_TOOL)
        assert info["status"] == "built"
        assert info["sif_exists"] is True
        assert info["sif_size_mb"] is not None
        assert info["updated_at"] is not None
        assert info["sif_path"] is not None

    def test_missing_tool_status(self):
        """A tool with no SIF on disk reports status "missing" with null path, size, and timestamp."""
        info = _tool_info("__nonexistent_tool_xyz__")
        assert info["status"] == "missing"
        assert info["sif_exists"] is False
        assert info["sif_size_mb"] is None
        assert info["sif_path"] is None
        assert info["updated_at"] is None

    def test_license_tool_status(self):
        """A tool listed in LICENSE_NEEDED reports status "license"."""
        for tool in LICENSE_NEEDED:
            info = _tool_info(tool)
            assert info["status"] == "license"
            break

    def test_category_from_map(self):
        """A tool's category matches its entry in CATEGORY_MAP."""
        for tool, expected_cat in CATEGORY_MAP.items():
            info = _tool_info(tool)
            assert info["category"] == expected_cat, (
                f"{tool}: expected {expected_cat!r}, got {info['category']!r}"
            )
            break

    def test_category_other_for_unknown_tool(self):
        """A tool absent from CATEGORY_MAP reports category "Other"."""
        info = _tool_info("__no_such_tool__")
        assert info["category"] == "Other"

    def test_sif_filename_format(self):
        """sif_filename is "<tool>_arm64.sif"."""
        info = _tool_info(REAL_TOOL)
        assert info["sif_filename"] == f"{REAL_TOOL}_arm64.sif"

    def test_dockerfile_field_format(self):
        """dockerfile field is "Dockerfile.<tool>"."""
        info = _tool_info(REAL_TOOL)
        assert info["dockerfile"] == f"Dockerfile.{REAL_TOOL}"

    def test_name_field_matches_input(self):
        """The returned name matches the tool name passed in."""
        info = _tool_info(REAL_TOOL)
        assert info["name"] == REAL_TOOL


# ── 4. Dockerfile endpoint ────────────────────────────────────────────────────

class TestGetDockerfile:
    """GET /v1/tools/{tool}/dockerfile: content retrieval and 404 handling."""

    def test_existing_tool_returns_content(self):
        """GET .../dockerfile returns 200 with non-empty content for an existing tool."""
        resp = client.get(f"/v1/tools/{REAL_TOOL}/dockerfile")
        assert resp.status_code == 200
        assert len(resp.text) > 0

    def test_existing_tool_contains_from(self):
        """The returned Dockerfile content contains a FROM instruction."""
        resp = client.get(f"/v1/tools/{REAL_TOOL}/dockerfile")
        assert resp.status_code == 200
        assert "FROM" in resp.text

    def test_nonexistent_tool_returns_404(self):
        """GET .../dockerfile returns 404 for a tool with no Dockerfile."""
        resp = client.get("/v1/tools/__no_such_tool_xyz__/dockerfile")
        assert resp.status_code == 404

    def test_404_detail_message(self):
        """The 404 response body's detail message mentions "not found"."""
        resp = client.get("/v1/tools/__no_such_tool_xyz__/dockerfile")
        assert "not found" in resp.json()["detail"].lower()


# ── 5. Build log endpoint ─────────────────────────────────────────────────────

class TestGetBuildLog:
    """GET /v1/tools/{tool}/log: content retrieval and 404 handling."""

    def test_existing_log_returns_content(self):
        """GET .../log returns 200 when a build log exists for the tool."""
        if not LOG_TOOL:
            pytest.skip("No build logs on disk")
        resp = client.get(f"/v1/tools/{LOG_TOOL}/log")
        assert resp.status_code == 200

    def test_nonexistent_log_returns_404(self):
        """GET .../log returns 404 for a tool with no build log."""
        resp = client.get("/v1/tools/__no_such_tool_xyz__/log")
        assert resp.status_code == 404

    def test_404_detail_message(self):
        """The 404 response body's detail message mentions "not found"."""
        resp = client.get("/v1/tools/__no_such_tool_xyz__/log")
        assert "not found" in resp.json()["detail"].lower()


# ── 6. Build endpoints ────────────────────────────────────────────────────────

class TestBuildEndpoints:
    """POST /v1/build/{tool} and /v1/build-all: validation and streamed response."""

    def test_build_nonexistent_tool_returns_404(self):
        """POST /v1/build/{tool} returns 404 for a tool with no Dockerfile."""
        resp = client.post("/v1/build/__no_such_tool_xyz__")
        assert resp.status_code == 404

    def test_build_nonexistent_tool_detail(self):
        """The 404 response body's detail message mentions "not found"."""
        resp = client.post("/v1/build/__no_such_tool_xyz__")
        assert "not found" in resp.json()["detail"].lower()

    def test_build_existing_tool_returns_200(self):
        """POST /v1/build/{tool} returns 200 for an existing tool (build streaming mocked)."""
        with patch("server._stream_build", side_effect=_fake_stream_ok):
            resp = client.post(f"/v1/build/{REAL_TOOL}")
        assert resp.status_code == 200

    def test_build_all_returns_200(self):
        """POST /v1/build-all returns 200 (build streaming mocked)."""
        with patch("server._stream_build", side_effect=_fake_stream_ok):
            resp = client.post("/v1/build-all")
        assert resp.status_code == 200


# ── 7. _stream_build async generator ─────────────────────────────────────────

class TestStreamBuild:
    """_stream_build() event sequencing for successful and failing subprocess runs."""

    def _collect(self, coro_fn):
        """Run an async generator and return the collected events."""
        async def _inner():
            return [event async for event in coro_fn]
        return asyncio.run(_inner())

    def _make_proc(self, returncode: int, lines: list[bytes]):
        """Build a mock asyncio subprocess with the given return code and stdout lines."""
        async def _stdout_gen():
            for line in lines:
                yield line

        proc = AsyncMock()
        proc.returncode = returncode
        proc.stdout = _stdout_gen()
        proc.wait = AsyncMock()
        return proc

    def test_stream_success_yields_lines(self):
        """_stream_build yields a data event per stdout line."""
        proc = self._make_proc(0, [b"step 1\n", b"step 2\n"])
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            events = self._collect(_stream_build(["echo", "ok"]))
        data_lines = [e["data"] for e in events]
        assert any("step 1" in d for d in data_lines)
        assert any("step 2" in d for d in data_lines)

    def test_stream_success_final_event_is_done(self):
        """A zero exit code ends the stream with a "done" event reporting exit code 0."""
        proc = self._make_proc(0, [b"output\n"])
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            events = self._collect(_stream_build(["echo", "ok"]))
        assert events[-1].get("event") == "done"
        assert "exit code: 0" in events[-1]["data"]

    def test_stream_failure_final_event_is_error(self):
        """A non-zero exit code ends the stream with an "error" event reporting the exit code."""
        proc = self._make_proc(1, [b"error msg\n"])
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            events = self._collect(_stream_build(["false"]))
        assert events[-1].get("event") == "error"
        assert "exit code: 1" in events[-1]["data"]

    def test_stream_handles_non_utf8_bytes(self):
        """Non-UTF-8 stdout bytes are decoded (errors="replace") without raising."""
        proc = self._make_proc(0, [b"\xab\xcd binary output\n"])
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            events = self._collect(_stream_build(["cmd"]))
        # Should not raise; errors='replace' handles bad bytes
        assert any("data" in e for e in events)

    def test_stream_empty_stdout_still_yields_exit_event(self):
        """Empty stdout still yields a single, final "done" event."""
        proc = self._make_proc(0, [])
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            events = self._collect(_stream_build(["true"]))
        assert len(events) == 1
        assert events[0].get("event") == "done"
