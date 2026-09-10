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
FROM node:24-bookworm-slim AS pixivflow-builder

ARG PIXIVFLOW_VERSION=2.10.21
RUN npm install --prefix /opt/pixivflow "pixivflow@${PIXIVFLOW_VERSION}" \
    && npm cache clean --force


FROM python:3.11-slim AS runtime-base

ARG APP_VERSION=dev
ARG GIT_SHA=dev
ARG BUILD_DATE=dev

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
# Bake build identity for /version + /health. Same shape scripts/build-release
# writes for PyInstaller bundles; the shared releasegraph workflow supplies the
# build-args (APP_VERSION/GIT_SHA). Defaults keep a local `docker build` on dev.
RUN printf 'RELEASE_VERSION = "%s"\nRELEASE_COMMIT = "%s"\nBUILD_DATE = "%s"\n' \
        "${APP_VERSION}" "${GIT_SHA}" "${BUILD_DATE}" > /app/_release_version.py
RUN mkdir -p logs data data/search_index \
    && chmod -R 755 logs data

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health').read()" || exit 1
CMD ["python", "-u", "run.py"]


# Fly's combined 512 MiB profile explicitly selects this stage.
FROM runtime-base AS runtime-pixivflow

# PixivFlow 的 ugoira（动图）转 GIF 在运行时 spawn python3 + ffmpeg；
# python:3.11-slim 自带 python3，只缺 ffmpeg，合一台必须装上，否则动图投递失败。
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY --from=pixivflow-builder /usr/local/bin/node /usr/local/bin/node
COPY --from=pixivflow-builder /opt/pixivflow /opt/pixivflow
RUN ln -s /opt/pixivflow/node_modules/.bin/pixivflow /usr/local/bin/pixivflow


# Default Docker/GHCR builds remain the Python-only TelePost image.
FROM runtime-base AS runtime
