from pathlib import Path

import pytest

from scripts import runtime_resolver as resolver

EXPECTED = "34a80219335f5dbeedeb7f0e6a4439913d12e25f"
RUNTIME = Path(__file__).resolve().parents[2] / "omnibioai-tool-runtime"


def test_sibling_runtime_resolves_and_validates(monkeypatch):
    monkeypatch.delenv("TOOL_RUNTIME_DIR", raising=False)
    resolved, temporary = resolver.resolve(Path(__file__).resolve().parents[1], EXPECTED)
    assert resolved == RUNTIME
    assert temporary is None


def test_wrong_sha_fails_closed():
    with pytest.raises(resolver.RuntimeResolutionError, match="SHA mismatch"):
        resolver.validate(RUNTIME, "0" * 40)


def test_missing_runtime_fails_closed_without_network(monkeypatch, tmp_path):
    monkeypatch.setenv("TOOL_RUNTIME_DIR", str(tmp_path / "missing"))
    def fail_clone(*args, **kwargs):
        raise resolver.RuntimeResolutionError("network disabled in test")
    monkeypatch.setattr(resolver, "run", fail_clone)
    with pytest.raises(resolver.RuntimeResolutionError):
        resolver.resolve(Path(__file__).resolve().parents[1], EXPECTED)


def test_staging_excludes_git_metadata(monkeypatch, tmp_path):
    monkeypatch.setenv("TOOL_RUNTIME_DIR", str(RUNTIME))
    destination = tmp_path / "omnibioai-tool-runtime"
    resolver.stage(Path(__file__).resolve().parents[1], destination, EXPECTED)
    assert (destination / "pyproject.toml").is_file()
    assert not (destination / ".git").exists()
