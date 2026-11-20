# syntax=docker/dockerfile:1.7
FROM node:22-bookworm-slim AS web
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim AS runtime
ARG TERRAFORM_VERSION=1.17.2
ARG TARGETARCH=amd64
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TF_IN_AUTOMATION=1 \
    CHECKPOINT_DISABLE=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl unzip git \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL "https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_${TARGETARCH}.zip" -o /tmp/terraform.zip \
    && unzip -q /tmp/terraform.zip -d /usr/local/bin \
    && chmod +x /usr/local/bin/terraform \
    && rm /tmp/terraform.zip \
    && terraform version

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin app

WORKDIR /app/backend
COPY backend/requirements.txt backend/requirements-postgres.txt ./
RUN pip install -r requirements.txt -r requirements-postgres.txt

COPY backend/ /app/backend/
COPY --from=web /src/frontend/dist /app/frontend/dist
COPY deploy/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh \
    && mkdir -p /app/backend/data/artifacts /app/backend/data/workspaces /app/backend/data/plugin-cache \
    && chown -R app:app /app/backend/data /app/backend

ENV TERRAFORM_BINARY=/usr/local/bin/terraform \
    TERRAFORM_PLUGIN_CACHE_DIR=/app/backend/data/plugin-cache \
    ARTIFACT_ROOT=/app/backend/data/artifacts \
    WORKSPACE_ROOT=/app/backend/data/workspaces \
    PYTHONPATH=/app/backend

USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/system/health', timeout=3).status == 200 else 1)"

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
