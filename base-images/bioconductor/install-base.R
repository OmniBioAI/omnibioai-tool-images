#!/usr/bin/env Rscript

options(repos = c(CRAN = "https://packagemanager.posit.co/cran/2024-11-01"))
options(pkgType = "source")

cran_packages <- c("BiocManager", "data.table", "jsonlite", "renv")
bioc_packages <- c(
  "BiocVersion", "BiocGenerics", "S4Vectors", "IRanges", "GenomeInfoDb",
  "GenomicRanges", "SummarizedExperiment", "SingleCellExperiment",
  "BiocParallel", "Biostrings"
)

# Install only Depends/Imports/LinkingTo. Suggested packages belong to the
# downstream workload and must not silently expand this foundation.
install.packages(cran_packages, dependencies = NA, quiet = FALSE)
stopifnot(requireNamespace("BiocManager", quietly = TRUE))
BiocManager::install(
  bioc_packages,
  version = "3.20",
  ask = FALSE,
  update = FALSE,
  Ncpus = max(1L, parallel::detectCores(logical = TRUE) - 1L)
)

ip <- as.data.frame(installed.packages(noCache = TRUE), stringsAsFactors = FALSE)
selected <- ip[ip$Package %in% c(cran_packages, bioc_packages),
              c("Package", "Version", "LibPath"), drop = FALSE]
selected <- selected[order(selected$Package), , drop = FALSE]
write.csv(selected, "/usr/local/share/omnibioai/installed-packages.csv", row.names = FALSE)
writeLines(capture.output(sessionInfo()), "/usr/local/share/omnibioai/session-info.txt")
writeLines(as.character(BiocManager::version()), "/usr/local/share/omnibioai/bioconductor-version.txt")
