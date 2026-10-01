#!/usr/bin/env python3
"""Run scientific gates inside the target OCI container or native SIF runtime."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys


def fixtures(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    sequence = "ATG" + "GCT" * 800 + "TAA"
    (directory / "reference.fa").write_text(">chr1\n" + sequence + "\n")
    (directory / "reads.fastq").write_text("@read1\n" + sequence[:100] + "\n+\n" + "I" * 100 + "\n")
    (directory / "reads.sam").write_text(
        "@HD\tVN:1.6\tSO:unknown\n@SQ\tSN:chr1\tLN:" + str(len(sequence))
        + "\nread1\t0\tchr1\t1\t60\t100M\t*\t0\t0\t" + sequence[:100]
        + "\t" + "I" * 100 + "\n"
    )
    (directory / "variants.vcf").write_text(
        "##fileformat=VCFv4.2\n##contig=<ID=chr1,length=3000>\n"
        "##FORMAT=<ID=GT,Number=1,Type=String,Description=\"Genotype\">\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
        "chr1\t10\t.\tA\tG\t60\tPASS\t.\tGT\t0/1\n"
    )
    (directory / "a.bed").write_text("chr1\t0\t10\n")
    (directory / "b.bed").write_text("chr1\t5\t12\n")
    (directory / "pairs.fa").write_text(
        ">a\nACGTACGTACGT\n>b\nACGTACGTTCGT\n>c\nACGTTCGTACGT\n"
    )
    qc = directory / "sample_fastqc"
    qc.mkdir()
    (qc / "fastqc_data.txt").write_text(
        "##FastQC\t0.12.1\n>>Basic Statistics\tpass\n"
        "#Measure\tValue\nFilename\treads.fastq\nTotal Sequences\t1\n"
        "Sequence length\t100\n%GC\t50\n>>END_MODULE\n"
    )


def run(command: str, cwd: Path, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/bin/bash", "-euo", "pipefail", "-c", command],
        cwd=cwd, text=True, capture_output=True, timeout=timeout,
    )


def require_text(value, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"missing/empty/non-string {label}")
    return value.strip()


def validate_native_file(report, arch: str) -> None:
    # Inspect file's description, never architecture words in the filename.
    description = require_text(report, "file evidence").partition(": ")[2]
    expected = {"amd64": "x86-64", "arm64": "ARM aarch64"}.get(arch)
    if not expected or "ELF 64-bit" not in description or expected not in description:
        raise ValueError(f"native executable architecture mismatch: {report}")


def validate_command_evidence(gate, entry: dict, phase: str) -> None:
    if not isinstance(gate, dict) or gate.get("status") != "PASS":
        raise ValueError(f"missing/failed {phase} gate")
    if type(gate.get("returncode")) is not int or gate["returncode"] != 0:
        raise ValueError(f"{phase} returncode must be integer zero")
    output = require_text(gate.get("output"), f"{phase} output")
    if gate.get("command") != entry[f"{phase}_command"]:
        raise ValueError(f"{phase} command does not match manifest")
    if not re.search(entry[f"{phase}_pattern"], output, re.MULTILINE):
        raise ValueError(f"{phase} output does not match manifest pattern")


def command_evidence(result, entry: dict, phase: str) -> dict:
    # Tools such as BWA and Prodigal legitimately emit versions on stderr.
    # Preserve both streams and normalize only outer whitespace and separation.
    output = "\n".join(stream.strip() for stream in (result.stdout, result.stderr) if stream.strip())
    gate = {"status": "PASS", "command": entry[f"{phase}_command"],
            "returncode": result.returncode, "stdout": result.stdout,
            "stderr": result.stderr, "output": output}
    try:
        validate_command_evidence(gate, entry, phase)
    except ValueError as exc:
        raise ValueError(f"{phase} check failed (returncode={result.returncode}): {exc}; output={output[-3000:]}") from exc
    return gate


def validate_executable_evidence(gate, entry: dict, arch: str) -> None:
    kind = entry.get("executable_type")
    if kind not in ("native_binary", "interpreted"):
        raise ValueError("unknown executable type")
    if not isinstance(gate, dict) or gate.get("status") != "PASS":
        raise ValueError("missing/failed executable gate")
    if gate.get("executable_type") != kind or gate.get("name") != entry["expected_executable"]:
        raise ValueError("executable identity/type differs from manifest")
    for key in ("path", "resolved_path", "file"):
        require_text(gate.get(key), f"executable {key}")
    if gate.get("invocable") is not True:
        raise ValueError("launcher/executable is not invocable")
    if kind == "native_binary":
        validate_native_file(gate["file"], arch)
        return
    if not re.search(r"\b(script|text)\b", gate["file"].partition(": ")[2], re.I):
        raise ValueError("interpreted launcher is not identified as script/text")
    runtimes = gate.get("interpreters")
    declared = entry.get("runtime_executables")
    launcher_interpreter = entry.get("launcher_interpreter")
    if (not isinstance(declared, list) or not declared or launcher_interpreter not in declared
            or not isinstance(runtimes, dict) or set(runtimes) != set(declared)):
        raise ValueError("missing/mismatched declared interpreter evidence")
    for name in declared:
        runtime = runtimes[name]
        if not isinstance(runtime, dict) or runtime.get("status") != "PASS" or runtime.get("invocable") is not True:
            raise ValueError(f"missing/failed interpreter: {name}")
        require_text(runtime.get("path"), f"{name} path")
        require_text(runtime.get("resolved_path"), f"{name} resolved path")
        validate_native_file(runtime.get("file"), arch)
    if (gate.get("launcher_interpreter") != launcher_interpreter
            or gate.get("shebang_interpreter_path") != runtimes[launcher_interpreter]["resolved_path"]
            or not require_text(gate.get("shebang"), "launcher shebang").startswith("#!")):
        raise ValueError("launcher shebang is not bound to the declared interpreter")


def validate_probe_result(result, entry: dict, arch: str, phase: str) -> None:
    if not isinstance(result, dict) or result.get("verification_status") != "PASS" or result.get("phase") != phase:
        raise ValueError("missing/failed or wrong-phase scientific probe")
    architecture = result.get("architecture")
    allowed = {"amd64": ("x86_64",), "arm64": ("aarch64", "arm64")}.get(arch, ())
    if (not isinstance(architecture, dict) or architecture.get("status") != "PASS"
            or architecture.get("target") != arch or architecture.get("uname") not in allowed):
        raise ValueError("runtime architecture mismatch")
    validate_executable_evidence(result.get("executable"), entry, arch)
    for check_name in ("version", "smoke"):
        validate_command_evidence(result.get(check_name), entry, check_name)


def resolve_shebang(exe: str) -> tuple[str, str]:
    with open(exe, "rb") as launcher:
        shebang = launcher.readline(4096).decode("utf-8").strip()
    if not shebang.startswith("#!"):
        raise ValueError("interpreted launcher has no shebang")
    parts = shlex.split(shebang[2:])
    if not parts or not parts[0].startswith("/"):
        raise ValueError("unsupported launcher shebang")
    interpreter = parts[0]
    if Path(interpreter).name == "env":
        # Support the simple env form; reject assignments/options rather than guessing.
        if len(parts) != 2 or parts[1].startswith("-") or "=" in parts[1]:
            raise ValueError("unsupported env shebang")
        interpreter = shutil.which(parts[1])
    if not interpreter or not os.access(interpreter, os.X_OK):
        raise ValueError("launcher interpreter is missing/not executable")
    return shebang, os.path.realpath(interpreter)


def check(entry: dict, arch: str, workspace: Path, phase: str) -> dict:
    if entry.get("executable_type") not in ("native_binary", "interpreted"):
        raise ValueError("unknown executable type")
    uname = subprocess.run(["uname", "-m"], check=True, text=True, capture_output=True).stdout.strip()
    allowed = {"amd64": {"x86_64"}, "arm64": {"aarch64", "arm64"}}[arch]
    if uname not in allowed:
        raise RuntimeError(f"runtime architecture mismatch: target={arch}, uname={uname}")
    exe = shutil.which(entry["expected_executable"])
    if not exe or not os.access(exe, os.X_OK):
        raise RuntimeError("intended executable missing or not executable")
    file_result = subprocess.run(["file", "-L", exe], check=True, text=True, capture_output=True)
    executable_report = file_result.stdout.strip()
    executable = {"status": "PASS", "name": entry["expected_executable"],
                  "executable_type": entry["executable_type"], "path": exe,
                  "resolved_path": os.path.realpath(exe), "file": executable_report, "invocable": True}
    runtime_reports = {}
    for runtime in entry["runtime_executables"]:
        path = shutil.which(runtime)
        if not path or not os.access(path, os.X_OK):
            raise RuntimeError(f"required script interpreter missing: {runtime}")
        report = subprocess.run(["file", "-L", path], check=True, text=True, capture_output=True).stdout.strip()
        validate_native_file(report, arch)
        runtime_reports[runtime] = {"status": "PASS", "path": path,
                                    "resolved_path": os.path.realpath(path), "file": report, "invocable": True}
    executable["interpreters"] = runtime_reports
    if entry["executable_type"] == "interpreted":
        shebang, interpreter_path = resolve_shebang(exe)
        executable.update(shebang=shebang, shebang_interpreter_path=interpreter_path,
                          launcher_interpreter=entry["launcher_interpreter"])
    validate_executable_evidence(executable, entry, arch)
    version = run(entry["version_command"], workspace)
    version_gate = command_evidence(version, entry, "version")
    smoke = run(entry["smoke_command"], workspace, timeout=240)
    smoke_gate = command_evidence(smoke, entry, "smoke")
    result = {
        "verification_status": "PASS",
        "architecture": {"status": "PASS", "uname": uname, "target": arch},
        "executable": executable,
        "version": version_gate,
        "smoke": smoke_gate,
        "phase": phase,
    }
    validate_probe_result(result, entry, arch, phase)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--entry", required=True, type=Path)
    parser.add_argument("--arch", required=True, choices=("amd64", "arm64"))
    parser.add_argument("--phase", required=True, choices=("oci", "sif"))
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        entry = json.loads(args.entry.read_text())
        result = check(entry, args.arch, args.workspace, args.phase)
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        print(json.dumps(result, sort_keys=True))
    except (OSError, ValueError, subprocess.SubprocessError, RuntimeError, KeyError) as exc:
        print(f"{args.phase} scientific verification failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
