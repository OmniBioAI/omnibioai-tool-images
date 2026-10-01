"""Contracts for the version-preserving native MUSCLE ARM64 repair."""
from pathlib import Path
import json


ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "dockerfiles/Dockerfile.muscle"
MANIFEST = ROOT / ".github/pilot/manifest.json"
SOURCE_SHA256 = "2bba8b06e3ccabf6465fa26f459763b2029d7e7b9596881063e3aaba60d9e87d"


def muscle_manifest():
    data = json.loads(MANIFEST.read_text())
    return next(tool for tool in data["tools"] if tool["tool_id"] == "muscle")


def test_muscle_uses_pinned_debian_source_for_both_native_architectures():
    text = DOCKERFILE.read_text()
    assert "muscle_5.1.0.orig.tar.gz" in text
    assert SOURCE_SHA256 in text
    assert "sha256sum -c -" in text
    assert "apt-get install -y --no-install-recommends muscle" not in text
    assert "COPY --from=muscle-builder" in text


def test_arm64_portability_fix_keeps_pointer_assertions_and_algorithms_untouched():
    text = DOCKERFILE.read_text()
    assert "defined(__arm64__) || defined(__aarch64__)" in text
    assert "sizeof(void *)" not in text
    assert "myutils.cpp" not in text
    assert "myutils.h" in text


def test_muscle_v5_identity_version_and_alignment_contract_remain_exact():
    entry = muscle_manifest()
    assert entry["executable_type"] == "native_binary"
    assert entry["expected_executable"] == "muscle"
    assert entry["version_command"] == "muscle -version"
    assert entry["version_pattern"] == "muscle [0-9]"
    assert entry["smoke_command"] == "muscle -align pairs.fa -output aligned.fa; test -s aligned.fa; echo muscle-ok"
    assert "^muscle 5\\.2\\.linux64 " in DOCKERFILE.read_text()
    assert entry["architectures"] == ["linux/amd64", "linux/arm64"]
