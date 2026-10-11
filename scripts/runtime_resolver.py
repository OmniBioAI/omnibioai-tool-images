#!/usr/bin/env python3
"""Resolve and stage the canonical OmniBioAI tool runtime for builds."""
from __future__ import annotations
import argparse, os, shutil, subprocess, tempfile
from pathlib import Path

DEFAULT_COMMIT = "34a80219335f5dbeedeb7f0e6a4439913d12e25f"
DEFAULT_REPOSITORY = "https://github.com/OmniBioAI/omnibioai-tool-runtime.git"

class RuntimeResolutionError(RuntimeError):
    pass

def run(*args: str, cwd: Path | None = None) -> str:
    p = subprocess.run(args, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode:
        raise RuntimeResolutionError(f"command failed: {' '.join(args)}: {p.stderr.strip()}")
    return p.stdout.strip()

def sha(path: Path) -> str:
    try:
        return run("git", "-C", str(path), "rev-parse", "HEAD")
    except RuntimeResolutionError as exc:
        raise RuntimeResolutionError(f"runtime checkout is not a Git repository: {path}") from exc

def validate(path: Path, expected: str) -> None:
    if not path.is_dir():
        raise RuntimeResolutionError(f"runtime checkout is missing: {path}")
    if run("git", "-C", str(path), "status", "--porcelain"):
        raise RuntimeResolutionError(f"runtime checkout is dirty: {path}")
    actual = sha(path)
    if actual != expected:
        raise RuntimeResolutionError(f"runtime SHA mismatch: expected {expected}, got {actual}")

def resolve(repo: Path, expected: str) -> tuple[Path, tempfile.TemporaryDirectory[str] | None]:
    override = os.environ.get("TOOL_RUNTIME_DIR")
    candidates = [Path(override).expanduser()] if override else [repo.parent / "omnibioai-tool-runtime"]
    for candidate in candidates:
        if candidate.is_dir():
            validate(candidate, expected)
            return candidate, None
    td = tempfile.TemporaryDirectory(prefix="omnibioai-tool-runtime-")
    target = Path(td.name) / "checkout"
    commit = os.environ.get("TOOL_RUNTIME_COMMIT", expected)
    run("git", "clone", "--no-checkout", DEFAULT_REPOSITORY, str(target))
    run("git", "-C", str(target), "checkout", "--detach", commit)
    validate(target, expected)
    return target, td

def stage(repo: Path, destination: Path, expected: str) -> Path:
    runtime, temporary = resolve(repo, expected)
    try:
        if destination.exists():
            raise RuntimeResolutionError(f"staging destination already exists: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(runtime, destination, ignore=shutil.ignore_patterns(".git"))
        validate(destination, expected) if (destination / ".git").exists() else None
        return destination
    finally:
        if temporary is not None:
            temporary.cleanup()

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    ap.add_argument("--commit", default=os.environ.get("TOOL_RUNTIME_COMMIT", DEFAULT_COMMIT))
    ap.add_argument("--stage", type=Path, required=True)
    args = ap.parse_args()
    stage(args.repo.resolve(), args.stage.resolve(), args.commit)
    print(args.commit)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
