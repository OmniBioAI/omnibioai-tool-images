#!/usr/bin/env python3
"""Validate the audit-backed ten-tool roster and emit only its 20 fixed jobs."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / ".github/pilot/manifest.json"
EVIDENCE = ROOT / ".github/pilot/audit-evidence.json"
ARCHES = ("linux/amd64", "linux/arm64")
RUNNERS = {"amd64": "ubuntu-24.04", "arm64": "ubuntu-24.04-arm"}
SOURCE_SHA256 = {
    "20260930-omnibioai-multiarch-ready-missing-from-ghcr.txt": "58aa666517d2ccf40b9bd4c234f6eb0526ce0c3cdacb217bf26d1683d859fde6",
    "20260930-omnibioai-multiarch-ready-present-in-ghcr.txt": "8df6d392571183304a9c494d9db86170ce4fa3bce6aec0287b3a74c4f6a969ff",
    "20260930-omnibioai-sif-ghcr-reconciliation.csv": "d4f07864d3d1dd64144583bab93a486c2a3ea0d7c4f64eb1c26acb3273233d3e",
}
REQUIRED = {
    "tool_id", "dockerfile", "version_command", "version_pattern",
    "smoke_command", "smoke_pattern", "expected_executable", "architectures",
    "executable_type", "runtime_executables", "license", "selection_reason",
}


def exact_arches(value):
    if not isinstance(value, list) or len(value) != 2 or set(value) != set(ARCHES):
        raise ValueError("architectures must be exactly two unique linux/amd64 and linux/arm64")


def validate(data: dict) -> dict:
    if not isinstance(data, dict) or data.get("schema_version") != 3:
        raise ValueError("unsupported manifest schema")
    exact_arches(data.get("architectures"))
    if data.get("sbom_policy") != "optional":
        raise ValueError("pilot SBOM policy must be explicit")
    tools = data.get("tools")
    if not isinstance(tools, list) or len(tools) != 10:
        raise ValueError("pilot manifest must contain exactly 10 tools")
    if any(not isinstance(t, dict) or REQUIRED - t.keys() for t in tools):
        raise ValueError("missing required tool fields")
    ids = [t["tool_id"] for t in tools]
    if any(not isinstance(t, str) or not re.fullmatch(r"[a-z0-9_]+", t) for t in ids):
        raise ValueError("invalid tool ID")
    if len(set(ids)) != 10:
        raise ValueError("duplicate pilot tool")
    evidence = json.loads(EVIDENCE.read_text())
    if evidence.get("schema_version") != 1 or evidence.get("source_files") != SOURCE_SHA256:
        raise ValueError("audit source identities do not match the saved reconciliation artifacts")
    ready, published = evidence["multiarch_ready"], evidence["published_multiarch_ready"]
    if (len(ready) != 860 or len(set(ready)) != 860 or len(published) != 611
            or len(set(published)) != 611 or not set(published) <= set(ready)):
        raise ValueError("invalid authoritative audit snapshot")
    for tool in tools:
        tool_id = tool["tool_id"]
        if not (ROOT / f"dockerfiles/Dockerfile.{tool_id}").is_file():
            raise ValueError(f"unknown tool: {tool_id}")
        exact_arches(tool["architectures"])
        if tool["dockerfile"] != f"dockerfiles/Dockerfile.{tool_id}" or not (ROOT / tool["dockerfile"]).is_file():
            raise ValueError(f"missing/mismatched Dockerfile for {tool_id}")
        if tool_id not in ready or tool_id not in published:
            raise ValueError(f"{tool_id} is not an audited published MULTIARCH_READY tool")
        evidence_row = evidence["records"].get(tool_id, {})
        if (evidence_row.get("audit_category") != "MULTIARCH_READY"
                or evidence_row.get("ghcr_present") != "YES"
                or evidence_row.get("dockerfile") != tool["dockerfile"]):
            raise ValueError(f"missing matching reconciliation evidence for {tool_id}")
        if tool.get("category") != "MULTIARCH_READY" or tool.get("published_in_ghcr") is not True:
            raise ValueError(f"manifest contradicts audit for {tool_id}")
        for flag in ("stub_or_placeholder", "architecture_dependent", "license_restricted"):
            if tool.get(flag) is not False:
                raise ValueError(f"disallowed/undeclared {flag}: {tool_id}")
        for field in REQUIRED - {"architectures", "runtime_executables"}:
            if not isinstance(tool[field], str) or not tool[field].strip():
                raise ValueError(f"empty/malformed {field}: {tool_id}")
        if tool["expected_executable"] != tool_id:
            raise ValueError(f"tool/executable identity mismatch: {tool_id}")
        if tool["executable_type"] not in ("native_binary", "interpreted"):
            raise ValueError(f"unsupported executable type: {tool_id}")
        runtimes = tool["runtime_executables"]
        if (not isinstance(runtimes, list)
                or any(not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9_.-]+", name) for name in runtimes)
                or len(set(runtimes)) != len(runtimes)):
            raise ValueError(f"invalid runtime executable: {tool_id}")
        if tool["executable_type"] == "interpreted" and (not runtimes or tool.get("launcher_interpreter") not in runtimes):
            raise ValueError(f"script needs a declared launcher interpreter and checked runtimes: {tool_id}")
        if tool["executable_type"] == "native_binary" and (runtimes or tool.get("launcher_interpreter") is not None):
            raise ValueError(f"native executable must not declare launcher interpreters: {tool_id}")
        dockerfile = (ROOT / tool["dockerfile"]).read_text()
        if re.search(r"--platform=|stub|placeholder", dockerfile, re.I):
            raise ValueError(f"excluded architecture/stub marker in Dockerfile: {tool_id}")
        for phase in ("version", "smoke"):
            command = tool[f"{phase}_command"]
            if ":latest" in command or chr(96) in command:
                raise ValueError(f"mutable/unsafe command: {tool_id}")
            if re.search(r"\|\||&&|\b(?:true|eval|exec|source|trap|set)\b|\$\(|\b(?:if|while|until|for)\b", command):
                raise ValueError(f"failure-suppressing command syntax: {tool_id}")
            re.compile(tool[f"{phase}_pattern"])
    return data


def load_and_validate(path: Path = MANIFEST) -> dict:
    return validate(json.loads(path.read_text()))


def matrix(data: dict) -> list[dict]:
    validate(data)
    entries = [
        {"tool_id": tool["tool_id"], "platform": arch, "arch": arch.split("/")[1],
         "runner": RUNNERS[arch.split("/")[1]], "dockerfile": tool["dockerfile"]}
        for tool in data["tools"] for arch in ARCHES
    ]
    if len(entries) != 20 or len({(item["tool_id"], item["platform"]) for item in entries}) != 20:
        raise ValueError("matrix must contain exactly 20 unique combinations")
    return entries


def get_tool(tool_id: str) -> dict:
    for tool in load_and_validate()["tools"]:
        if tool["tool_id"] == tool_id:
            return tool
    raise ValueError(f"unknown pilot tool: {tool_id}")


def manifest_digest() -> str:
    return hashlib.sha256(MANIFEST.read_bytes()).hexdigest()


def require_nonpublishing(publish: bool) -> None:
    if publish:
        raise ValueError("publication is disabled in this safety-repair workflow")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", action="store_true")
    parser.add_argument("--publish", choices=("false", "true"), default="false")
    parser.add_argument("--github-output")
    args = parser.parse_args()
    try:
        require_nonpublishing(args.publish == "true")
        payload = json.dumps(matrix(load_and_validate()), separators=(",", ":"))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"pilot validation failed: {exc}", file=sys.stderr)
        return 1
    if args.matrix:
        print(payload)
    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as output:
            output.write(f"matrix={payload}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
