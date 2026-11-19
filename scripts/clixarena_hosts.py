"""Upload providers and backward-compatible mappings.
Configured strictly for VK Video (VKontakte Private/Closed Group Uploads).
Automatically computes embed_url and records owner_id, video_id, and access_hash.
"""
import os, json, re, subprocess, time, urllib.parse, urllib.request, urllib.error
from clixarena_upload_metrics import run_upload, file_size
from vk_uploader import VKUploader

SUPPORTED_HOSTS = ('vk',)

def get_embed_url(provider, file_id, access_hash=""):
    if provider == 'vk':
        if '_' in str(file_id):
            oid, vid = str(file_id).split('_')
            return f"https://vkvideo.ru/video_ext.php?oid={oid}&id={vid}" + (f"&hash={access_hash}" if access_hash else "")
        return f"https://vkvideo.ru/video_ext.php?id={file_id}"
    return None

def selected_host(layout):
    return 'vk'

def selected_hosts(layout):
    return ['vk']

def merge_provider_mapping(rows, identity, file_id, provider='vk', access_hash="", owner_id="", video_id=""):
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError('Invalid mappings')
    
    key = ('mal_id' if 'mal_id' in identity else 'anilist_id') if identity.get('type') == 'anime' else 'tmdb_id'
    matches = lambda row: all(row.get(k) == identity.get(k) for k in ('type', key, 'season', episode_key(identity)))
    
    providers = {}
    for row in rows:
        if not matches(row):
            continue
        if isinstance(row.get('providers'), dict):
            providers.update(row['providers'])
        if row.get('vk_id') and 'vk' not in providers:
            providers['vk'] = {'file_id': row['vk_id'], 'embed_url': get_embed_url('vk', row['vk_id'])}

    embed_url = get_embed_url('vk', file_id, access_hash)
    entry = {
        'file_id': file_id,
        'full_id': file_id,
        'embed_url': embed_url
    }
    if owner_id:
        entry['owner_id'] = str(owner_id)
    if video_id:
        entry['video_id'] = str(video_id)
    if access_hash:
        entry['access_hash'] = str(access_hash)
        
    providers['vk'] = entry
    
    item = dict(identity, provider='vk', providers=providers, file_id=file_id, embed_url=embed_url)
    item['vk_id'] = file_id
    item['vk_embed_url'] = embed_url
    if access_hash:
        item['access_hash'] = access_hash
    if owner_id:
        item['owner_id'] = str(owner_id)
    if video_id:
        item['video_id'] = str(video_id)

    return [row for row in rows if not matches(row)] + [item]

def episode_key(identity):
    return 'episode'

def upload_vk(path, name, access_token=None, group_id=None, mask=lambda _: None):
    token = access_token or os.getenv("VK_ACCESS_TOKEN")
    if not token:
        raise ValueError('VK_ACCESS_TOKEN is missing or invalid')
    
    mask(token)
    gid = group_id or os.getenv("VK_GROUP_ID")
    uploader = VKUploader(access_token=token, group_id=gid)
    
    res = uploader.upload_video(file_path=str(path), title=name)
    full_id = res.get("full_id")
    access_key = res.get("access_key", "")
    owner_id = res.get("owner_id", "")
    video_id = res.get("video_id", "")
    
    return {
        "file_id": full_id,
        "full_id": full_id,
        "access_hash": access_key,
        "owner_id": owner_id,
        "video_id": video_id,
        "embed_url": res.get("embed_url")
    }

def verify_vk(file_id, access_token=None, attempts=5, pause=time.sleep):
    """Verify VK video status using video.get API."""
    token = access_token or os.getenv("VK_ACCESS_TOKEN")
    if not token:
        return True, True
    
    url = f"https://api.vk.com/method/video.get?videos={file_id}&access_token={token}&v=5.199"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                items = data.get("response", {}).get("items", [])
                if items and len(items) > 0:
                    return True, True
        except Exception:
            pass
        if attempt < attempts - 1:
            pause(3)
    return False, False
