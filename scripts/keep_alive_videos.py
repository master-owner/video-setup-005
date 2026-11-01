"""Ping all uploaded videos in mappings.json to prevent inactivity deletion on StreamHG & Abyss.
Runs automatically via GitHub Actions Cron every 10 days.
"""
import base64
import hashlib
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path


def decrypt_mappings(encrypted_doc: dict, password: str) -> list:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt = base64.b64decode(encrypted_doc["salt"])
    nonce = base64.b64decode(encrypted_doc["nonce"])
    ciphertext = base64.b64decode(encrypted_doc["ciphertext"])
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 600000, 32)
    aesgcm = AESGCM(key)
    plaintext = aesgcm.decrypt(nonce, ciphertext, b"CLIXARENA-ANIME-MAPPINGS")
    return json.loads(plaintext.decode("utf-8"))


def keep_alive_streamhg(filecode: str, api_key: str = None) -> bool:
    try:
        # 1. Direct Embed / File Info Ping
        info_url = f"https://streamhgapi.com/api/file/info?key={urllib.parse.quote(api_key or '', safe='')}&file_code={filecode}" if api_key else None
        urls = [f"https://streamhg.com/{filecode}"]
        if info_url:
            urls.insert(0, info_url)
            
        for url in urls:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
            with urllib.request.urlopen(req, timeout=15) as res:
                _ = res.read(1024)
        return True
    except Exception as e:
        print(f"Warning pinging StreamHG {filecode}: {e}", flush=True)
        return False


def keep_alive_abyss(slug: str, api_key: str = None) -> bool:
    try:
        # Player Ping
        urls = [f"https://player.abyssplayer.com/{slug}"]
        if api_key:
            urls.append(f"https://api.abyss.to/v1/files/{slug}?key={urllib.parse.quote(api_key, safe='')}")
        for url in urls:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
            with urllib.request.urlopen(req, timeout=15) as res:
                _ = res.read(1024)
        return True
    except Exception as e:
        print(f"Warning pinging Abyss {slug}: {e}", flush=True)
        return False


def main():
    print("Starting Video Keep-Alive Service to prevent inactivity deletion...", flush=True)
    mapping_path = Path("mappings.json")
    if not mapping_path.exists():
        print("No mappings.json found in repository. Skipping.", flush=True)
        return 0

    password = os.getenv("MAPPING_PASSWORD", "").strip()
    streamhg_key = os.getenv("STREAMHG_API_KEY", "").strip()
    abyss_key = os.getenv("ABYSS_API_KEY", "").strip()

    with open(mapping_path, "r", encoding="utf-8") as f:
        doc = json.load(f)

    # Check if doc is encrypted
    if isinstance(doc, dict) and "ciphertext" in doc:
        if not password:
            print("mappings.json is encrypted, but MAPPING_PASSWORD secret is missing. Cannot ping items.", flush=True)
            return 1
        rows = decrypt_mappings(doc, password)
    elif isinstance(doc, list):
        rows = doc
    elif isinstance(doc, dict):
        rows = list(doc.values())
    else:
        print("Unknown mappings.json format. Skipping.", flush=True)
        return 0

    print(f"Extracted {len(rows)} mapping records to keep alive.", flush=True)

    streamhg_codes = set()
    abyss_slugs = set()

    for item in rows:
        if not isinstance(item, dict):
            continue
        providers = item.get("providers", {})
        if isinstance(providers, dict):
            if "streamhg" in providers and providers["streamhg"].get("file_id"):
                streamhg_codes.add(providers["streamhg"]["file_id"])
            if "abyss" in providers and providers["abyss"].get("file_id"):
                abyss_slugs.add(providers["abyss"]["file_id"])
        
        # Backward compatibility fields
        if item.get("streamhg_id"):
            streamhg_codes.add(item["streamhg_id"])
        if item.get("abyss_id"):
            abyss_slugs.add(item["abyss_id"])
        if item.get("file_id") and item.get("provider") == "streamhg":
            streamhg_codes.add(item["file_id"])
        if item.get("file_id") and item.get("provider") == "abyss":
            abyss_slugs.add(item["file_id"])

    print(f"Found {len(streamhg_codes)} StreamHG videos and {len(abyss_slugs)} Abyss videos.", flush=True)

    for code in streamhg_codes:
        ok = keep_alive_streamhg(code, streamhg_key)
        print(f"StreamHG [{code}]: {'Active (Pinged)' if ok else 'Failed'}", flush=True)
        time.sleep(1)

    for slug in abyss_slugs:
        ok = keep_alive_abyss(slug, abyss_key)
        print(f"Abyss [{slug}]: {'Active (Pinged)' if ok else 'Failed'}", flush=True)
        time.sleep(1)

    print("All videos pinged successfully! Files remain active without expiring.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
