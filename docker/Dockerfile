FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

RUN apt-get update -qq && apt-get install -y --no-install-recommends     ca-certificates     curl     git     ffmpeg     fontconfig     fonts-dejavu-core     fonts-noto-core     python3     python3-pip     python3-cryptography     python3-libtorrent     && pip install --no-cache-dir --break-system-packages pyrogram tgcrypto     && apt-get clean && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

WORKDIR /github/workspace
