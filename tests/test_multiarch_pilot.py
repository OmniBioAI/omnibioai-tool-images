from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.pilot_manifest import ARCHES, RUNNERS, load_and_validate, matrix, selected_matrix, validate


def test_exact_roster_architectures_and_matrix_bound():
    data = load_and_validate()
    jobs = matrix(data)
    assert len(data["tools"]) == 10
    assert len({tool["tool_id"] for tool in data["tools"]}) == 10
    assert tuple(data["architectures"]) == ARCHES
    assert len(jobs) == 10 * 2 == 20
    assert len({(job["tool_id"], job["platform"]) for job in jobs}) == 20
    assert {job["runner"] for job in jobs} == set(RUNNERS.values())
    assert all(job["dockerfile"] == f"dockerfiles/Dockerfile.{job['tool_id']}" for job in jobs)


def test_exact_tool_selection_keeps_both_native_architectures():
    jobs = selected_matrix(load_and_validate(), "muscle")
    assert len(jobs) == 2
    assert {job["tool_id"] for job in jobs} == {"muscle"}
    assert {job["platform"] for job in jobs} == set(ARCHES)
    assert {job["runner"] for job in jobs} == set(RUNNERS.values())


def test_tool_selection_cannot_expand_outside_validated_roster():
    with pytest.raises(ValueError, match="unknown or incomplete"):
        selected_matrix(load_and_validate(), "not-a-pilot-tool")


@pytest.mark.parametrize("arches", [[], ["linux/amd64"], ["linux/amd64", "linux/amd64"],
                                     ["linux/amd64", "linux/arm64", "linux/ppc64le"],
                                     ["amd64", "arm64"]])
def test_rejects_architecture_aliases_duplicates_and_expansion(arches):
    data = load_and_validate()
    data["architectures"] = ["linux/amd64", "linux/arm64"]
    data["tools"][0]["architectures"] = arches
    with pytest.raises(ValueError):
        validate(data)


@pytest.mark.parametrize("mutation", [
    lambda d: d["tools"].append(dict(d["tools"][0])),
    lambda d: d["tools"][0].pop("expected_executable"),
    lambda d: d["tools"][0].update(version_command=""),
    lambda d: d["tools"][0].update(stub_or_placeholder=True),
    lambda d: d["tools"][0].update(license_restricted=True),
    lambda d: d["tools"][0].update(tool_id="seqkit", dockerfile="dockerfiles/Dockerfile.seqkit"),
    lambda d: d["tools"][0].update(tool_id="not-a-tool"),
])
def test_rejects_roster_and_gate_mutations(mutation):
    data = load_and_validate()
    mutation(data)
    with pytest.raises(ValueError):
        validate(data)


def test_evidence_roster_is_authoritatively_audited_and_published():
    evidence = json.loads((ROOT / ".github/pilot/audit-evidence.json").read_text())
    selected = {t["tool_id"] for t in load_and_validate()["tools"]}
    assert selected <= set(evidence["multiarch_ready"])
    assert selected <= set(evidence["published_multiarch_ready"])
    assert len(evidence["multiarch_ready"]) == 860
    assert len(evidence["published_multiarch_ready"]) == 611


def test_no_publication_job_or_write_permission_exists():
    workflow = (ROOT / ".github/workflows/pilot-multiarch-sifs.yml").read_text()
    assert "packages: write" not in workflow
    assert "publish-verified" not in workflow
    assert "inputs.publish" in workflow
    assert 'PUBLISH: ${{ inputs.publish }}' in workflow
    assert '--publish "$PUBLISH"' in workflow
    for source in (ROOT / "scripts/pilot_runner.py", ROOT / ".github/workflows/pilot-multiarch-sifs.yml"):
        text = source.read_text().lower()
        assert "docker push" not in text and "oras push" not in text
        assert "--push" not in text
