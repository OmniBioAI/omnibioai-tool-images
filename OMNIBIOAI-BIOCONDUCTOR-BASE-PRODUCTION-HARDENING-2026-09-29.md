# OmniBioAI Bioconductor Base Production Hardening — 2026-09-29

## Scope

Track R2 only. The authoritative location is `omnibioai-tool-images/base-images/bioconductor`. Discovery inputs were the supplied R discovery report, workload matrix, and package-frequency CSV; the 73 Bioconductor-family rows were analyzed directly. No runtime-R, plugin, workflow, SIF, service, CPU-base, native-base, or GPU files were changed.

## Compatibility and parent decision

Selected line: **R 4.4.2 / Bioconductor 3.20**. Official evidence: [Bioconductor release table](https://bioconductor.org/about/release-announcements/), [3.20 release](https://bioconductor.org/news/bioc_3_20_release/), and [3.19 release](https://bioconductor.org/news/bioc_3_19_release/). Both 3.19 and 3.20 are R 4.4 cohorts; R 4.3 belongs to 3.18. Thus 3.20 is canonical, the explicit 3.19 heavy row stays specialized, and the observed R 4.3 row is a compatibility exception.

Final recommended parent (Design B):

```text
bioconductor/bioconductor_docker@sha256:5053f59de1c1a79b5a05d820ba722bae62ded0c1042e547189aa0226f111ea7e
```

Design A was not selected because `omnibioai-runtime-r:1` is not release-verified or remotely available. No unpublished runtime-R reference was introduced. Re-evaluate inheritance only at the synchronization gate after R1 release verification. Design B avoids duplicating upstream R/Bioconductor/system-library maintenance and independently supports both target architectures.

## Workload analysis and dependency boundary

`base-images/bioconductor/workload-compatibility.csv` contains all 73 rows: 46 ordinary candidates, 1 observed R4.3 exception, and 26 heavy/specialized workloads. Heavy single-cell, epigenomics, proteomics, genome-resource, and mixed Python/CLI stacks remain downstream.

Included foundation packages:

- CRAN: `BiocManager`, `data.table` (8 workloads), `jsonlite` (3), `renv` lock tooling.
- Bioconductor: `BiocVersion`, `BiocGenerics`, `S4Vectors`, `IRanges`, `GenomeInfoDb`, `GenomicRanges`, `SummarizedExperiment`, `SingleCellExperiment`, `BiocParallel`, `Biostrings`.

These provide common S4, ranges, assay containers, sequences, and parallel infrastructure. `BiocFileCache` was excluded after its tidy/database dependency footprint proved disproportionate to observed reuse. Excluded downstream packages include `clusterProfiler`, `DESeq2`, `Seurat`, `DSS`, `MOFA2`, `xcms`, `MSnbase`, `ArchR`, `Signac`, `CATALYST`, annotation/data packages, Java/HDF5/NetCDF/geospatial stacks, external CLIs, Python, and GPU libraries. Popularity alone did not qualify a package.

System dependencies are limited to common compiler, CA/TLS/curl, XML, PNG/JPEG/TIFF, font, Git, and locale support. Java, Python, HDF5/NetCDF/geospatial, CLI, and GPU requirements remain specialized. No workload evidence justified adding them to the canonical foundation.

## Reproducibility

CRAN is the Posit snapshot `2024-11-01`; package installation uses source packages and only Depends/Imports/LinkingTo. Bioconductor is explicitly release `3.20`. The parent and Dockerfile frontend are digest-pinned. The image records resolved packages, session info, and Bioconductor version under `/usr/local/share/omnibioai/`; `verify-base.R` checks manifest consistency, R 4.4, Bioconductor 3.20, and loadability. Observed CRAN resolutions: BiocManager 1.30.25, data.table 1.16.2, jsonlite 1.8.9, renv 1.0.11. Downstream workload packages still require their own exact lock.

## Architecture and runtime evidence

ARM64 native Apple M4: build PASS; runtime PASS; `linux/arm64`; R 4.4.2/Bioc 3.20; image 1,663,205,364 bytes (~1.66 GB); `omnibioai`, UID 10001, `/workspace`; package/object smoke, TLS, and writable temp path PASS.

AMD64 Buildx: build PASS; controlled runtime smoke PASS under QEMU (explicitly not native AMD64 evidence); `linux/amd64`; R 4.4.2/Bioc 3.20; image 1,715,884,837 bytes (~1.72 GB); non-root, package/object smoke, TLS, and writable temp path PASS under emulation.

No package was silently dropped for ARM64. The final image sets `HOME=/home/omnibioai`, `TMPDIR=/tmp/omnibioai`, `R_LIBS_USER=/home/omnibioai/R/%v-library`, and runs non-root. R/Rscript and runtime writes were validated on both built images.

## OCI, release, vulnerability, and synchronization status

`.github/workflows/release-bioconductor-base.yml` is manual-only and publishes exactly `1.0.0`, `1.0`, and `1`; it sets `latest=false`, creates OCI metadata, enables SBOM/provenance, resolves the upstream digest, and verifies post-push manifests plus absence of `latest`. No publication, GHCR visibility change, or permission change was performed.

`trivy`, `syft`, `cosign`, and `hadolint` were unavailable: `VULNERABILITY_SCAN_BLOCKED_EXTERNAL_TOOLING`. SBOM/provenance generation is workflow-ready; release operator scanning remains required before publication.

The parallel `omnibioai-tool-runtime` repository remained clean. `git diff --check` passes. No reset, stash, clean, checkout, revert, force operation, migration, or SIF generation was used.

## Release gate

Parent architecture, compatibility rationale, dependency boundary, manifest consistency, ARM64 native build/runtime, AMD64 build/QEMU smoke, non-root, TLS, package smoke, OCI metadata, and release workflow: PASS. Post-push manifest verification is pending publication. Vulnerability evidence is blocked by external tooling. Publication authority is absent.

Files changed are listed by `git status --short`; all are new R2 files in this repository, and the concurrent runtime-R worktree is preserved.

OMNIBIOAI BIOCONDUCTOR BASE READY TO RELEASE
