# OmniBioAI — Tool Images UI and API
# Purpose: Build the OmniBioAI tool-image catalog UI and API container.
# Author: Manish Kumar <manish@omnibioai.org>
# ── Stage 1: Build React UI ────────────────────────────────────────────────────
FROM --platform=$BUILDPLATFORM node:20-bookworm-slim AS ui-builder
WORKDIR /ui
COPY frontend/tool-images-ui/package*.json ./
RUN npm ci
COPY frontend/tool-images-ui/ ./
RUN npm run build

# ── Stage 2: Python API + nginx ───────────────────────────────────────────────
FROM python:3.12-slim-bookworm AS backend
LABEL org.opencontainers.image.source=https://github.com/man4ish/omnibioai

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    nginx && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY api/ ./api/

# Python dependencies
RUN pip install --no-cache-dir uvicorn fastapi sse-starlette

COPY --from=ui-builder /ui/dist /usr/share/nginx/html

RUN printf 'server {\n\
    listen 5179;\n\
    root /usr/share/nginx/html;\n\
    index index.html;\n\
    location /_svc/toolimages/ { alias /usr/share/nginx/html/; try_files $uri $uri/ /_svc/toolimages/index.html; }\n\
    location / { try_files $uri $uri/ /index.html; }\n\
    location /v1/ { proxy_pass http://127.0.0.1:8097; proxy_set_header Host $host; }\n\
    location /health { proxy_pass http://127.0.0.1:8097; }\n\
    location /docs   { proxy_pass http://127.0.0.1:8097; }\n\
}\n' > /etc/nginx/conf.d/tool-images.conf && \
    rm -f /etc/nginx/sites-enabled/default /etc/nginx/sites-available/default

# Runtime environment
ENV PYTHONUNBUFFERED=1
EXPOSE 8097 5179

# Entrypoint and default command
CMD ["bash", "-c", "service nginx start && python -m uvicorn api.server:app --host 0.0.0.0 --port 8097"]
