#!/usr/bin/env python3
"""Standalone Telegram Multi-Resolution Transcoder, Splitter & Mapping Updater."""
import os
import sys
import json
import re
import math
import shutil
import tempfile
import subprocess
from pathlib import Path

# Add scripts directory to path
sys.path.insert(0, str(Path(__file__).parent))

from clixarena_hosts import (
    HostError,
    upload_telegram_multi,
    merge_provider_mapping
)
from clixarena_video import (
    save_mapping,
    mask,
    output_filename
)

def run_standalone_telegram():
    video_url = os.getenv("VIDEO_URL", "").strip()
    output_name = output_filename(os.getenv("OUTPUT_NAME", "CLIXARENA.mp4"))
    media_type = os.getenv("MEDIA_TYPE", "movie").strip().lower()
    tmdb_id_str = os.getenv("TMDB_ID", "").strip()
    season_str = os.getenv("SEASON", "0").strip()
    episode_str = os.getenv("EPISODE", "0").strip()
    display_title = os.getenv("TITLE", "").strip() or output_name.removesuffix(".mp4")

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    api_id = os.getenv("TELEGRAM_API_ID", "").strip()
    api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()

    for secret in (bot_token, chat_id, api_hash, os.getenv("MAPPING_PASSWORD", "")):
        if secret:
            mask(secret)

    if not video_url:
        print("::error::VIDEO_URL is required.", flush=True)
        return 1

    if not bot_token or not chat_id:
        print("::error::TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required.", flush=True)
        return 1

    print(f"🎬 Starting dedicated Telegram multi-resolution pipeline for: {output_name}", flush=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        input_file = Path(tmpdir) / "source_video.mp4"
        print(f"📥 Downloading source video from: {video_url}", flush=True)

        cmd = [
            "curl", "-L", "--fail", "--retry", "3", "--retry-delay", "5",
            "--connect-timeout", "30", "-o", str(input_file), video_url
        ]
        res = subprocess.run(cmd)
        if res.returncode != 0 or not input_file.is_file() or input_file.stat().st_size == 0:
            print("::error::Failed to download source video.", flush=True)
            return 1

        size_mb = round(input_file.stat().st_size / (1024 * 1024), 2)
        print(f"✅ Source video downloaded successfully ({size_mb} MB).", flush=True)

        print("⚡ Initiating multi-resolution transcode and multi-part upload...", flush=True)
        primary_id, quality_ids = upload_telegram_multi(
            path=input_file,
            name=output_name,
            bot_token=bot_token,
            chat_id=chat_id,
            api_id=api_id,
            api_hash=api_hash,
            mask=mask
        )

        print(f"✅ All resolutions uploaded to Telegram! Primary ID: {primary_id}", flush=True)
        print(f"📊 Quality map: {json.dumps(quality_ids)}", flush=True)

        if tmdb_id_str and tmdb_id_str.isdigit():
            tmdb_id = int(tmdb_id_str)
            season = int(season_str) if season_str.isdigit() else 0
            episode = int(episode_str) if episode_str.isdigit() else 0

            identity = {
                "type": media_type,
                "tmdb_id": tmdb_id,
                "season": season,
                "episode": episode,
                "name": display_title
            }
            if media_type == "anime":
                identity = {
                    "type": "anime",
                    "mal_id": tmdb_id,
                    "season": season,
                    "episode": episode,
                    "name": display_title
                }

            os.environ["OVERLAY_LAYOUT"] = json.dumps({"mapping": identity})
            print(f"📝 Saving mapping receipt for {media_type} (ID: {tmdb_id})...", flush=True)
            try:
                save_mapping(primary_id, provider="telegram", quality_ids=quality_ids)
                print("✅ mappings.json successfully updated and committed to GitHub repository!", flush=True)
            except Exception as e:
                print(f"::warning::Telegram upload completed, but mapping update failed: {e}", flush=True)
                return 1

    return 0

if __name__ == "__main__":
    sys.exit(run_standalone_telegram())
