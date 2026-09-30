# OmniBioAI Bioconductor scientific base

Canonical target: `ghcr.io/omnibioai/omnibioai-base-bioconductor`.

This image is intentionally a foundation, not a universal R environment. It
uses the authoritative upstream `bioconductor/bioconductor_docker:RELEASE_3_20`
parent, pins the Bioconductor release to 3.20 / R 4.4, adds only common S4,
genomic-range, assay-container, sequence, parallel/cache infrastructure plus
`data.table`, `jsonlite`, and `renv`, and runs as `omnibioai` (UID 10001).

The package list is in `package-manifest.csv`. CRAN resolution uses the Posit
Package Manager snapshot dated 2024-11-01; Bioconductor resolution is pinned to
release 3.20. The build records the resolved package versions and session info
in `/usr/local/share/omnibioai/`. CI must resolve the upstream parent digest and
pass it as `UPSTREAM_IMAGE` for a release build.

Excluded downstream packages include `clusterProfiler`, `DESeq2`, `Seurat`,
`DSS`, `MOFA2`, `xcms`, `MSnbase`, `ArchR`, genome annotation/data packages,
Java/HDF5/NetCDF stacks, external genomics CLIs, Python, and GPU libraries.
These are workload-specific or materially heavy and remain specialized layers.

Local checks:

```bash
docker buildx build --platform linux/arm64 \
  -f base-images/bioconductor/Dockerfile \
  -t ghcr.io/omnibioai/omnibioai-base-bioconductor:1.0.0 .
docker run --rm --entrypoint /bin/bash IMAGE \
  /usr/local/share/omnibioai/smoke-test.sh
```
