from pathlib import Path


BASE = Path(__file__).parents[1] / "base-images" / "bioconductor"


def test_base_is_release_pinned_and_non_root():
    dockerfile = (BASE / "Dockerfile").read_text()
    assert "RELEASE_3_20" in dockerfile
    assert "USER omnibioai" in dockerfile
    assert ":latest" not in dockerfile.lower()


def test_manifest_contains_only_foundation_packages():
    rows = (BASE / "package-manifest.csv").read_text().splitlines()
    packages = {row.split(",", 1)[0] for row in rows[1:]}
    assert {"DESeq2", "clusterProfiler", "Seurat", "DSS", "MOFA2", "xcms", "MSnbase"}.isdisjoint(packages)
    assert {"BiocVersion", "GenomicRanges", "SummarizedExperiment", "SingleCellExperiment"} <= packages


def test_release_has_exact_tags_and_no_latest():
    workflow = (Path(__file__).parents[1] / ".github" / "workflows" / "release-bioconductor-base.yml").read_text()
    assert "type=raw,value=1.0.0" in workflow
    assert "type=raw,value=1.0" in workflow
    assert "type=raw,value=1" in workflow
    assert "latest=false" in workflow
    assert "platforms: linux/amd64,linux/arm64" in workflow
