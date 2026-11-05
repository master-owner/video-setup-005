FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

RUN apt-get update -qq && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    git \
    ffmpeg \
    fontconfig \
    fonts-dejavu-core \
    fonts-noto-core \
    python3 \
    python3-cryptography \
    python3-libtorrent \
    && curl -sL --connect-timeout 15 --max-time 90 https://github.com/YouROK/TorrServer/releases/download/MatriX.148/TorrServer-linux-amd64 -o /usr/local/bin/torrserver \
    && chmod +x /usr/local/bin/torrserver \
    && apt-get clean && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

WORKDIR /github/workspace
