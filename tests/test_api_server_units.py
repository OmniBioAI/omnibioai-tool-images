"""Focused unit tests for API behavior without TestClient or host services.

Developer:
    Manish Kumar <manish@omnibioai.org>
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "api"))

import server  # noqa: E402


def collect(async_generator):
    """Run an async generator to completion and return its collected events."""
    async def run():
        return [event async for event in async_generator]

    return asyncio.run(run())


def test_health_returns_service_status():
    """health() returns {"status": "ok"} directly, without going through TestClient."""
    assert server.health() == {"status": "ok"}


def test_tool_info_reports_built_sif_metadata(tmp_path, monkeypatch):
    """_tool_info() reports full metadata (path, size in MB, status "built") for a tool with a SIF on disk."""
    sif = tmp_path / "demo_arm64.sif"
    sif.write_bytes(b"x" * (2 * 1024 * 1024 + 100))
    monkeypatch.setattr(server, "SIF_DIR", tmp_path)

    info = server._tool_info("demo")

    assert info == {
        "name": "demo",
        "category": "Other",
        "sif_path": str(sif),
        "sif_filename": "demo_arm64.sif",
        "sif_exists": True,
        "sif_size_mb": 2.0,
        "status": "built",
        "dockerfile": "Dockerfile.demo",
        "updated_at": info["updated_at"],
    }
    assert info["updated_at"]


@pytest.mark.parametrize("tool", ["missing_tool", "cellranger"])
def test_tool_info_reports_nonbuilt_statuses_without_sif(tmp_path, monkeypatch, tool):
    """A tool with no SIF reports status "missing", or "license" when it's in LICENSE_NEEDED."""
    monkeypatch.setattr(server, "SIF_DIR", tmp_path)

    info = server._tool_info(tool)

    expected_status = "license" if tool == "cellranger" else "missing"
    assert info["status"] == expected_status
    assert info["sif_exists"] is False
    assert info["sif_path"] is None
    assert info["sif_size_mb"] is None
    assert info["updated_at"] is None


def test_list_tools_is_sorted_and_skips_internal_tools(tmp_path, monkeypatch):
    """list_tools() sorts alphabetically and excludes internal tools like "template" and "obsolete"."""
    for name in ("zeta", "alpha", "template", "obsolete"):
        (tmp_path / f"Dockerfile.{name}").write_text(f"FROM alpine\nCMD ['{name}']\n")
    monkeypatch.setattr(server, "DOCKERFILES_DIR", tmp_path)
    monkeypatch.setattr(server, "SIF_DIR", tmp_path / "sif")

    tools = server.list_tools()

    assert [tool["name"] for tool in tools] == ["alpha", "zeta"]
    assert all(tool["dockerfile"] == f"Dockerfile.{tool['name']}" for tool in tools)


def test_get_dockerfile_reads_text_and_preserves_plain_content(tmp_path, monkeypatch):
    """get_dockerfile() returns a Dockerfile's exact text content unmodified."""
    dockerfiles = tmp_path / "dockerfiles"
    dockerfiles.mkdir()
    expected = "FROM python:3.12-slim\nCMD [\"tool\"]\n"
    (dockerfiles / "Dockerfile.demo").write_text(expected)
    monkeypatch.setattr(server, "DOCKERFILES_DIR", dockerfiles)

    assert server.get_dockerfile("demo") == expected


def test_get_dockerfile_missing_file_raises_http_404(tmp_path, monkeypatch):
    """get_dockerfile() raises HTTPException(404, "Dockerfile not found") for a missing tool."""
    monkeypatch.setattr(server, "DOCKERFILES_DIR", tmp_path)

    with pytest.raises(HTTPException) as exc:
        server.get_dockerfile("missing")

    assert exc.value.status_code == 404
    assert exc.value.detail == "Dockerfile not found"


def test_get_build_log_reads_log_and_reports_missing_log(tmp_path, monkeypatch):
    """get_build_log() returns log text when present and raises HTTPException(404) when absent."""
    (tmp_path / "demo.log").write_text("build output\n")
    monkeypatch.setattr(server, "BUILD_LOGS_DIR", tmp_path)
    assert server.get_build_log("demo") == "build output\n"

    with pytest.raises(HTTPException) as exc:
        server.get_build_log("missing")
    assert exc.value.status_code == 404


def test_build_endpoints_validate_tool_and_construct_sse_response(tmp_path, monkeypatch):
    """build_tool()/build_all() return an EventSourceResponse for a valid tool and raise HTTPException(404) for a missing one."""
    dockerfiles = tmp_path / "dockerfiles"
    dockerfiles.mkdir()
    (dockerfiles / "Dockerfile.demo").write_text("FROM alpine\n")
    monkeypatch.setattr(server, "DOCKERFILES_DIR", dockerfiles)

    response = server.build_tool("demo")
    assert response.__class__.__name__ == "EventSourceResponse"
    with pytest.raises(HTTPException) as exc:
        server.build_tool("missing")
    assert exc.value.status_code == 404

    all_response = server.build_all()
    assert all_response.__class__.__name__ == "EventSourceResponse"


def test_stream_build_passes_safe_subprocess_boundary_and_decodes_output(monkeypatch):
    """_stream_build() invokes create_subprocess_exec with the exact argv/pipes/cwd and decodes stdout lines, including non-UTF-8 bytes."""
    stdout = _AsyncLines([b"first\n", b"\xffsecond\n"])
    process = SimpleNamespace(returncode=0, stdout=stdout, wait=AsyncMock())
    create = AsyncMock(return_value=process)
    monkeypatch.setattr(server.asyncio, "create_subprocess_exec", create)

    events = collect(server._stream_build(["bash", "scripts/build_all.sh", "demo"]))

    create.assert_awaited_once_with(
        "bash", "scripts/build_all.sh", "demo",
        stdout=server.asyncio.subprocess.PIPE,
        stderr=server.asyncio.subprocess.STDOUT,
        cwd=str(server.BASE),
    )
    assert events[0]["data"] == "first"
    assert "second" in events[1]["data"]
    assert events[-1] == {"data": "\n[exit code: 0]", "event": "done"}
    process.wait.assert_awaited_once_with()


@pytest.mark.parametrize("returncode,event", [(1, "error"), (127, "error")])
def test_stream_build_emits_error_event_for_nonzero_exit(monkeypatch, returncode, event):
    """Any non-zero exit code yields a single final "error" event reporting that code."""
    process = SimpleNamespace(
        returncode=returncode,
        stdout=_AsyncLines([]),
        wait=AsyncMock(),
    )
    monkeypatch.setattr(server.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))

    events = collect(server._stream_build(["missing-command"]))

    assert events == [{"data": f"\n[exit code: {returncode}]", "event": event}]


class _AsyncLines:
    """Minimal async iterator over a fixed list of byte lines, standing in for a subprocess's stdout stream."""

    def __init__(self, lines):
        self.lines = iter(lines)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self.lines)
        except StopIteration as exc:
            raise StopAsyncIteration from exc
