#!/usr/bin/env bash
set -euo pipefail

test "$(id -u)" != 0
test "$(uname -m)" = "aarch64" || test "$(uname -m)" = "x86_64"
test -w "${TMPDIR:-/tmp}"
Rscript --vanilla -e '
  stopifnot(grepl("^4\\.4", paste(R.version$major, R.version$minor, sep=".")))
  stopifnot(as.character(BiocManager::version()) == "3.20")
  suppressPackageStartupMessages({
    library(SummarizedExperiment); library(SingleCellExperiment)
    library(GenomicRanges); library(Biostrings); library(data.table); library(jsonlite)
  })
  se <- SummarizedExperiment(matrix(1:4, nrow=2, dimnames=list(NULL, c("a", "b"))))
  stopifnot(nrow(se) == 2L, ncol(se) == 2L)
  cat("package_smoke=PASS\n")
  cat("tls=", tryCatch({
    con <- url("https://bioconductor.org/config.yaml"); x <- readLines(con, n=1); close(con); length(x) > 0
  }, error=function(e) FALSE), "\n", sep="")
  tf <- file.path(Sys.getenv("TMPDIR"), "omnibioai-smoke.txt"); writeLines("ok", tf); stopifnot(file.exists(tf))
'
