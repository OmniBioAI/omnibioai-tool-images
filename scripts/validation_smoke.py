"""Read-only assertions for contract-defined smoke outputs; never executes tools."""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Mapping

from scripts.validation_contracts import ContractError, sha256_file, validate_contract


class SmokeValidationError(ValueError):
    """Smoke evidence is missing, malformed, or disagrees with its contract."""


def validate_version_evidence(
    contract: Mapping[str, Any], repo_root: Path,
    *, stdout: str, stderr: str, returncode: int,
) -> dict[str, str]:
    """Require one tool-version observation and the existing exact parser.

    The observation parser detects conflicting banners; it does not replace
    or relax the strict expected-version parser. No command is executed here.
    """
    try:
        validate_contract(contract, repo_root)
        if contract["contract_status"] != "CONTRACT_READY":
            raise SmokeValidationError("blocked contract cannot pass version validation")
        if type(returncode) is not int or returncode != 0:
            raise SmokeValidationError("version command did not exit successfully")
        if not isinstance(stdout, str) or not isinstance(stderr, str):
            raise SmokeValidationError("version streams must be text")
        if len(stdout.encode()) + len(stderr.encode()) > 1024 * 1024:
            raise SmokeValidationError("version evidence exceeds 1 MiB limit")
        source = contract["version_output_source"]
        streams = {"stdout": stdout, "stderr": stderr}
        if source not in ("stdout", "stderr", "either"):
            raise SmokeValidationError("unknown version output source")
        names = list(streams) if source == "either" else [source]
        strict = contract["version_parser"]
        if not isinstance(strict, str) or not strict.startswith("regex:"):
            raise SmokeValidationError("unsupported strict version parser")
        strict_parser = re.compile(strict.removeprefix("regex:"))
        observation = contract["version_observation_parser"]
        if not isinstance(observation, str):
            raise SmokeValidationError("observation parser must be text")
        observation_parser = re.compile(observation)
        if strict_parser.groups != 1 or observation_parser.groups != 1:
            raise SmokeValidationError("version parsers must capture one value")
        observations = [
            (name, match.group(1)) for name in names
            for match in observation_parser.finditer(streams[name])
        ]
        if len(observations) != 1:
            raise SmokeValidationError("missing, duplicate, or conflicting version observations")
        name, observed = observations[0]
        if observed != contract["version_expected_value"]:
            raise SmokeValidationError("observed tool version mismatch")
        matches = list(strict_parser.finditer(streams[name]))
        if len(matches) != 1 or matches[0].group(1) != observed:
            raise SmokeValidationError("strict version parser rejected evidence")
        return {
            "tool_id": contract["tool_id"], "observed_version": observed,
            "evidence_stream": name,
            "contract_sha256": contract["validation_contract_sha256"],
        }
    except (OSError, KeyError, TypeError, ValueError, ContractError, re.error) as error:
        if isinstance(error, SmokeValidationError):
            raise
        raise SmokeValidationError(f"invalid version evidence: {error}") from error


def _path(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise SmokeValidationError("expected a nonempty relative path")
    root = root.resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise SmokeValidationError("path escapes verification root")
    return path


def _text(path: Path) -> str:
    if path.stat().st_size > 1024 * 1024:
        raise SmokeValidationError("smoke evidence exceeds 1 MiB limit")
    return path.read_text(encoding="utf-8")


def _shape(value: Any) -> tuple[int, int]:
    if not isinstance(value, list) or len(value) != 2:
        raise SmokeValidationError("invalid matrix shape")
    if any(type(n) is not int or not 0 < n <= 100000 for n in value):
        raise SmokeValidationError("invalid matrix dimensions")
    return value[0], value[1]


def _number(value: Any) -> float:
    if isinstance(value, bool):
        raise SmokeValidationError("boolean count is invalid")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise SmokeValidationError("counts must be finite and nonnegative")
    return number


def _entries(rows: list, shape: tuple[int, int]) -> dict[tuple[int, int], float]:
    result = {}
    for entry in rows:
        if len(entry) != 3:
            raise SmokeValidationError("matrix entry must have three fields")
        row, column, value = entry
        if type(row) is not int or type(column) is not int:
            raise SmokeValidationError("matrix coordinates must be integers")
        if not (1 <= row <= shape[0] and 1 <= column <= shape[1]):
            raise SmokeValidationError("matrix coordinate out of bounds")
        if (row, column) in result:
            raise SmokeValidationError("duplicate matrix coordinate")
        result[row, column] = _number(value)
    return result


def _matrix(path: Path) -> tuple[tuple[int, int], dict[tuple[int, int], float]]:
    lines = _text(path).splitlines()
    if not lines or lines[0] not in (
        "%%MatrixMarket matrix coordinate real general",
        "%%MatrixMarket matrix coordinate integer general",
    ):
        raise SmokeValidationError("unsupported MatrixMarket header")
    data = [line.split() for line in lines[1:] if line.strip() and not line.startswith("%")]
    if not data or len(data[0]) != 3:
        raise SmokeValidationError("missing MatrixMarket size line")
    row_count, column_count, nnz = map(int, data[0])
    shape = _shape([row_count, column_count])
    if nnz < 0 or len(data) - 1 != nnz:
        raise SmokeValidationError("MatrixMarket entry count mismatch")
    entries = []
    for fields in data[1:]:
        if len(fields) != 3:
            raise SmokeValidationError("matrix entry must have three fields")
        if " integer " in lines[0]:
            int(fields[2])  # Reject fractional values under an integer header.
        entries.append([int(fields[0]), int(fields[1]), fields[2]])
    return shape, _entries(entries, shape)


def validate_smoke_outputs(
    contract: Mapping[str, Any], repo_root: Path, workspace: Path,
    *, returncode: int, elapsed_seconds: float,
) -> dict[str, Any]:
    """Validate outputs of an already-executed smoke; not native/release proof.

    The future execution adapter must supply measured exit status/duration and
    an isolated workspace. This function does not run commands or write files.
    """
    try:
        validate_contract(contract, repo_root)
        if contract["contract_status"] != "CONTRACT_READY":
            raise SmokeValidationError("blocked contract cannot pass smoke validation")
        spec = contract["smoke_assertions"]
        if spec["kind"] != "matrix_market_gene_counts":
            raise SmokeValidationError("unsupported smoke assertion kind")
        timeout = spec["timeout_seconds"]
        if type(timeout) is not int or timeout <= 0:
            raise SmokeValidationError("invalid smoke timeout")
        if type(returncode) is not int or returncode != 0:
            raise SmokeValidationError("smoke command did not exit successfully")
        if isinstance(elapsed_seconds, bool) or not math.isfinite(elapsed_seconds) or not 0 <= elapsed_seconds <= timeout:
            raise SmokeValidationError("smoke duration is invalid or exceeds timeout")
        hashes = contract["fixture_sha256"]
        if set(hashes) != set(contract["smoke_inputs"]):
            raise SmokeValidationError("fixture hash set mismatch")
        for relative, digest in hashes.items():
            if sha256_file(_path(repo_root, relative)) != digest:
                raise SmokeValidationError("fixture SHA256 mismatch")
        expected_path = spec["expected_fixture"]
        if expected_path not in hashes:
            raise SmokeValidationError("expected results must be hash-bound")
        expected = json.loads(_text(_path(repo_root, expected_path)))
        shape = _shape(expected["shape"])
        wanted = _entries(expected["entries"], shape)
        tolerance = _number(expected["tolerance"])
        if not 0 < tolerance <= 0.0001:
            raise SmokeValidationError("unreviewed numerical tolerance")
        directory = spec["output_directory"]
        names = ["quants_mat.mtx", "quants_mat_rows.txt", "quants_mat_cols.txt"]
        paths = [f"{directory}/{name}" for name in names]
        if paths != contract["smoke_expected_outputs"]:
            raise SmokeValidationError("output declarations disagree with assertions")
        observed_shape, observed = _matrix(_path(workspace, paths[0]))
        if observed_shape != shape or observed.keys() != wanted.keys():
            raise SmokeValidationError("matrix shape or coordinates mismatch")
        if any(abs(observed[key] - value) > tolerance for key, value in wanted.items()):
            raise SmokeValidationError("gene counts mismatch")
        for path, field, count in zip(paths[1:], ("rows", "columns"), shape):
            labels = expected[field]
            if not isinstance(labels, list) or len(labels) != count or len(set(labels)) != count:
                raise SmokeValidationError("invalid expected labels")
            if any(not isinstance(label, str) or not label for label in labels):
                raise SmokeValidationError("invalid expected label")
            if _text(_path(workspace, path)).splitlines() != labels:
                raise SmokeValidationError("matrix labels mismatch")
        totals = expected["row_totals"]
        if len(totals) != shape[0]:
            raise SmokeValidationError("row total count mismatch")
        for row, total in enumerate(totals, 1):
            total = _number(total)
            for matrix in (wanted, observed):
                if abs(sum(value for (r, _), value in matrix.items() if r == row) - total) > tolerance:
                    raise SmokeValidationError("row total mismatch")
        return {
            "assertion_kind": spec["kind"], "shape": list(shape),
            "contract_sha256": contract["validation_contract_sha256"],
            "output_sha256": {path: sha256_file(_path(workspace, path)) for path in paths},
            "smoke_outputs_verified": True,
        }
    except (OSError, UnicodeError, KeyError, TypeError, ValueError, ContractError) as error:
        if isinstance(error, SmokeValidationError):
            raise
        raise SmokeValidationError(f"invalid smoke evidence: {error}") from error
