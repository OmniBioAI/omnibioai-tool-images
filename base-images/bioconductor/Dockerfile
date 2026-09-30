# syntax=docker/dockerfile:1.7
# OmniBioAI canonical Bioconductor foundation.
# The upstream release tag is deliberate: it supplies the supported R/Bioc
# pair and its maintained system dependency baseline. CI resolves and records
# the immutable digest before release; no floating `latest` tag is used.
ARG UPSTREAM_IMAGE=bioconductor/bioconductor_docker@sha256:5053f59de1c1a79b5a05d820ba722bae62ded0c1042e547189aa0226f111ea7e
FROM ${UPSTREAM_IMAGE}

ARG TARGETARCH
LABEL org.opencontainers.image.title="OmniBioAI Bioconductor scientific base" \
      org.opencontainers.image.description="Pinned Bioconductor 3.20 / R 4.4 scientific foundation" \
      org.opencontainers.image.vendor="OmniBioAI" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.source="https://github.com/OmniBioAI/omnibioai-tool-images" \
      org.opencontainers.image.base.name="bioconductor/bioconductor_docker:RELEASE_3_20" \
      org.omnibioai.bioconductor="3.20" \
      org.omnibioai.r="4.4"

USER root

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       build-essential \
       ca-certificates \
       curl \
       git \
       libcurl4-openssl-dev \
       libfontconfig1-dev \
       libfreetype6-dev \
       libjpeg-dev \
       libpng-dev \
       libssl-dev \
       libtiff5-dev \
       libxml2-dev \
       locales \
    && locale-gen C.UTF-8 \
    && rm -rf /var/lib/apt/lists/*

COPY base-images/bioconductor/install-base.R /usr/local/share/omnibioai/install-base.R
COPY base-images/bioconductor/package-manifest.csv /usr/local/share/omnibioai/package-manifest.csv
RUN Rscript /usr/local/share/omnibioai/install-base.R \
    && rm -f /usr/local/share/omnibioai/install-base.R

RUN useradd --create-home --uid 10001 --shell /bin/bash omnibioai \
    && install -d -o omnibioai -g omnibioai -m 0700 /home/omnibioai/.cache \
    && install -d -o omnibioai -g omnibioai -m 1777 /tmp/omnibioai \
    && install -d -o omnibioai -g omnibioai /workspace
COPY base-images/bioconductor/verify-base.R /usr/local/share/omnibioai/verify-base.R
COPY base-images/bioconductor/smoke-test.sh /usr/local/share/omnibioai/smoke-test.sh
# Re-run after copying the verifier so the image build fails on manifest drift.
RUN Rscript /usr/local/share/omnibioai/verify-base.R

ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    HOME=/home/omnibioai \
    TMPDIR=/tmp/omnibioai \
    R_LIBS_USER=/home/omnibioai/R/%v-library

USER omnibioai
WORKDIR /workspace

CMD ["R"]
