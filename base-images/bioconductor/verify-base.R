#!/usr/bin/env Rscript

manifest <- read.csv("/usr/local/share/omnibioai/package-manifest.csv", stringsAsFactors = FALSE)
installed <- read.csv("/usr/local/share/omnibioai/installed-packages.csv", stringsAsFactors = FALSE)
expected <- manifest$package
missing <- setdiff(expected, installed$Package)
if (length(missing)) stop("manifest packages missing: ", paste(missing, collapse = ", "))
if (!identical(as.character(BiocManager::version()), "3.20")) stop("Bioconductor release mismatch")
if (!startsWith(as.character(getRversion()), "4.4")) stop("R major/minor mismatch")
for (pkg in expected) if (!requireNamespace(pkg, quietly = TRUE)) stop("cannot load ", pkg)
cat("manifest_consistency=PASS\n")
cat("R=", getRversion(), "\n", sep = "")
cat("Bioconductor=", BiocManager::version(), "\n", sep = "")
