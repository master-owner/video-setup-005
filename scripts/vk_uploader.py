"""
VK.com (VKontakte / VK Video) Direct Video Uploader Module
- Handles Direct Video Upload to VK.com via video.save and multipart HTTP POST upload.
- Supports Uploading to Private Community / Closed Group (group_id) or Personal Account.
- Privacy & Stealth:
    * Group uploads: Automatically uploads into Private/Closed Community.
    * Privacy: privacy_view=3 (Only Me) for personal uploads, wallpost=0 (No public wall post), repeat=0.
- Supports Environment Variables:
    1. VK_ACCESS_TOKEN (The OAuth token obtained with video, groups scope)
    2. Optional: VK_GROUP_ID (Private Community ID where videos will be stored)
"""

import os
import sys
import time
import json
import secrets
import string
import subprocess
import urllib.parse
import urllib.request
from typing import Dict, Any, Optional

import functools

# Force real-time log flushing
print = functools.partial(print, flush=True)
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass


USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
VK_API_VERSION = "5.199"


class VKUploader:
    def __init__(self, access_token: str = None, group_id: str = None):
        self.access_token = (access_token or os.getenv("VK_ACCESS_TOKEN") or "").strip()
        raw_group = (group_id or os.getenv("VK_GROUP_ID") or "").strip()
        # Clean group_id if user passes full URL or club prefix (e.g., https://vk.com/club123456 or club123456 -> 123456)
        if raw_group:
            clean_g = raw_group.rstrip("/").split("/")[-1]
            for prefix in ("club", "public", "event"):
                if clean_g.lower().startswith(prefix):
                    clean_g = clean_g[len(prefix):]
            self.group_id = clean_g
        else:
            self.group_id = ""

    @property
    def is_configured(self) -> bool:
        return bool(self.access_token)

    def _api_call(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Calls VK API v5.199 with standard parameters."""
        url = f"https://api.vk.com/method/{method}"
        data_params = {
            "access_token": self.access_token,
            "v": VK_API_VERSION,
            **params
        }
        encoded_data = urllib.parse.urlencode(data_params).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=encoded_data,
            headers={
                "User-Agent": USER_AGENT,
                "Content-Type": "application/x-www-form-urlencoded"
            }
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw_text = resp.read().decode("utf-8")
            data = json.loads(raw_text)
            if "error" in data:
                err = data["error"]
                err_code = err.get("error_code")
                err_msg = err.get("error_msg", "Unknown error")
                raise RuntimeError(f"VK API error {err_code}: {err_msg}")
            return data.get("response", data)

    def get_upload_server(self, name: str, description: str = "") -> Dict[str, Any]:
        """
        Calls video.save to get upload_url and video metadata.
        If group_id is set, target group_id is passed so the video is hosted inside the private community.
        """
        params: Dict[str, Any] = {
            "name": name,
            "description": description,
            "wallpost": 0,
            "repeat": 0
        }
        if self.group_id:
            try:
                params["group_id"] = int(self.group_id)
            except ValueError:
                params["group_id"] = self.group_id
        else:
            # Only Me privacy for personal account
            params["privacy_view"] = 3
            params["privacy_comment"] = 3

        return self._api_call("video.save", params)

    def upload_file_stream(self, upload_url: str, file_path: str) -> Dict[str, Any]:
        """
        Uploads video file to VK upload_url via Native High-Speed curl (Multi-Gigabit Throughput).
        - Uses HTTP/1.1 with TCP_NODELAY for minimum latency across transatlantic links.
        - Enforces a 30-second speed-watchdog: If average speed drops below 500 KB/s for 30s, aborts early to request a fresh, fast VK edge server.
        """
        file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
        filename = os.path.basename(file_path)
        print(f"[*] Dispatching Native High-Speed curl upload to VK ({file_size_mb:.1f} MB)...")
        start_t = time.time()

        cmd = [
            "curl", "-sS",
            "-H", "Expect:",
            "-H", f"User-Agent: {USER_AGENT}",
            "--http1.1",
            "--tcp-nodelay",
            "--connect-timeout", "20",
            "--speed-time", "30",
            "--speed-limit", "500000",
            "--max-time", "420",
            "-F", f"video_file=@{file_path};filename={filename};type=video/mp4",
            upload_url
        ]

        proc = subprocess.run(cmd, capture_output=True, text=True)
        elapsed = time.time() - start_t
        if proc.returncode != 0:
            err_msg = proc.stderr.strip() if proc.stderr else f"Exit code {proc.returncode}"
            raise RuntimeError(f"Native curl upload failed after {elapsed:.1f}s: {err_msg}")

        raw_res = proc.stdout.strip()
        speed_mbps = (file_size_mb / elapsed) if elapsed > 0 else 0
        print(f"[+] Native curl upload to VK completed in {elapsed:.1f}s ({speed_mbps:.2f} MB/s)!")

        try:
            return json.loads(raw_res)
        except Exception:
            return {"raw": raw_res}

    def upload_video(self, file_path: str, title: str, description: str = "", max_retries: int = 4) -> Dict[str, Any]:
        """
        Executes complete VK upload flow with Exponential Backoff Auto-Healing:
        1. Obfuscates upload title to random 25 alphanumeric chars to bypass automated text/keyword filters.
        2. Calls video.save to register upload slot and get upload_url.
        3. Streams video to upload_url with multi-attempt failover on network timeouts/drops.
        4. Returns standardized record with video_id, owner_id, player embed_url and direct links.
        """
        if not self.is_configured:
            raise RuntimeError("VK credentials (VK_ACCESS_TOKEN) are not configured.")

        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Video file not found: {file_path}")

        file_mb = os.path.getsize(file_path) / (1024 * 1024)
        print(f"[*] Preparing VK Video Upload ({file_mb:.1f} MB)...")

        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                # Stealth Title Obfuscation: 25 random characters, empty description
                obfuscated_title = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(25))
                if attempt == 1:
                    print("[*] Obfuscated VK video title with random 25-character hash.")
                else:
                    print(f"[*] [Retry {attempt}/{max_retries}] Requesting fresh VK upload slot...")

                save_res = self.get_upload_server(name=obfuscated_title, description="")
                upload_url = save_res.get("upload_url")
                video_id = save_res.get("video_id")
                owner_id = save_res.get("owner_id")
                access_key = save_res.get("access_key", "")

                # GitHub Actions secret masking so critical VK IDs never leak in logs
                if os.getenv("GITHUB_ACTIONS"):
                    for sensitive_val in (str(video_id), str(owner_id), str(access_key), f"{owner_id}_{video_id}"):
                        if sensitive_val and sensitive_val != "None":
                            print(f"::add-mask::{sensitive_val}")

                if not upload_url:
                    raise RuntimeError("Failed to obtain VK upload_url from video.save.")

                print(f"[*] VK Video Slot Acquired (Protected Slot)")
                print(f"[*] Streaming video file to VK upload endpoint (Attempt {attempt}/{max_retries})...")
                upload_res = self.upload_file_stream(upload_url, file_path)
                print(f"[+] VK Upload successfully completed!")

                # Resolved standard identifiers
                full_video_id = f"{owner_id}_{video_id}"
                embed_url = f"https://vk.com/video_ext.php?oid={owner_id}&id={video_id}&hash={access_key}"

                return {
                    "video_id": video_id,
                    "owner_id": owner_id,
                    "full_id": full_video_id,
                    "access_key": access_key,
                    "embed_url": embed_url,
                    "title": title,
                    "group_id": self.group_id if self.group_id else None,
                    "upload_response": upload_res
                }

            except Exception as err:
                last_error = err
                wait_time = 4 * (2 ** (attempt - 1))
                print(f"[!] VK Upload Attempt {attempt}/{max_retries} failed: {err}", file=sys.stderr)
                if attempt < max_retries:
                    print(f"[*] Waiting {wait_time}s before exponential backoff retry...")
                    time.sleep(wait_time)

        raise RuntimeError(f"All {max_retries} VK Video upload attempts failed. Last error: {last_error}")
