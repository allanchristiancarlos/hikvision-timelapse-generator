FROM mcr.microsoft.com/devcontainers/python:1-3.11-bookworm

RUN find /etc/apt/sources.list.d -maxdepth 1 -type f -iname '*yarn*' -delete \
    && (sed -i '/dl.yarnpkg.com\/debian/d' /etc/apt/sources.list || true) \
    && find /etc/apt/sources.list.d -maxdepth 1 -type f -name '*.list' -exec sed -i '/dl.yarnpkg.com\/debian/d' {} \; \
    && apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspaces/hikvision-timelapse-generator
