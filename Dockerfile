# syntax=docker/dockerfile:1.7

# Build Python wheels once, then keep compilers and headers out of production.
FROM python:3.11-slim AS python-builder

WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt


# The combined Fly profile needs Node and PixivFlow, but not npm at runtime.
FROM node:20-bookworm-slim AS pixivflow-builder

ARG PIXIVFLOW_VERSION=2.7.0
RUN npm install --prefix /opt/pixivflow "pixivflow@${PIXIVFLOW_VERSION}" \
    && npm cache clean --force


FROM python:3.11-slim AS runtime-base

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai \
    HTTP_PROXY="" \
    HTTPS_PROXY=""

RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
COPY --from=python-builder /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels

COPY . .
RUN mkdir -p logs data data/search_index \
    && chmod -R 755 logs data

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health').read()" || exit 1
CMD ["python", "-u", "run.py"]


# Fly's combined 512 MiB profile explicitly selects this stage.
FROM runtime-base AS runtime-pixivflow

COPY --from=pixivflow-builder /usr/local/bin/node /usr/local/bin/node
COPY --from=pixivflow-builder /opt/pixivflow /opt/pixivflow
RUN ln -s /opt/pixivflow/node_modules/.bin/pixivflow /usr/local/bin/pixivflow


# Default Docker/GHCR builds remain the Python-only TelePost image.
FROM runtime-base AS runtime
