"""Public torrent video selection and piece-verified download with Seedr.cc API cloud caching."""
import base64
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

class TorrentError(Exception):pass
VIDEO={'.mkv','.mp4','.webm','.avi','.mov','.m4v','.ts','.m2ts'}
FALLBACK_TRACKERS=[
    'udp://tracker.opentrackr.org:1337/announce',
    'udp://open.stealth.si:80/announce',
    'udp://tracker.torrent.eu.org:451/announce',
    'udp://explodie.org:6969/announce',
    'udp://tracker.openbittorrent.com:80/announce',
    'udp://opentracker.i2p.rocks:6969/announce',
    'udp://tracker.cyberia.is:6969/announce',
    'udp://open.demonii.com:1337/announce',
    'udp://p4p.arenabg.com:1337/announce',
    'udp://tracker.moeking.me:6969/announce',
    'udp://tracker.dler.org:6969/announce',
    'udp://tracker.srv00.com:6969/announce'
]

def public_trackers(values,resolver=socket.getaddrinfo):
    result=[]
    for value in list(values)[:30]+FALLBACK_TRACKERS:
        try:
            value=value.decode('utf-8') if isinstance(value,bytes) else str(value)
            url=urllib.parse.urlsplit(value)
            if url.scheme!='udp' or not url.hostname or url.username or url.password or url.query or url.fragment or url.path!='/announce' or any(ord(c)<33 for c in value):continue
            port=url.port
            if not port or not 1<=port<=65535:continue
            addresses=resolver(url.hostname,port,type=socket.SOCK_DGRAM)
            if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):continue
            if value not in result:result.append(value)
        except (ValueError,UnicodeError,OSError):continue
        if len(result)>=12:break
    return result

def discovery(session,handle,lt,counters):
    for alert in session.pop_alerts():
        name=type(alert).__name__
        if name=='tracker_reply_alert':counters['replies']+=1
        elif name=='tracker_error_alert':counters['errors']+=1
    try:nodes=str(max(0,int(session.status().dht_nodes)))
    except (AttributeError,TypeError,ValueError):nodes='unknown'
    return f"DHT nodes {nodes} | tracker replies {counters['replies']} | tracker errors {counters['errors']}"

def candidates(info):
    files=info.files();result=[]
    if files.num_files()>10000:raise TorrentError('Torrent contains too many files.')
    for index in range(files.num_files()):
        path=str(files.file_path(index));parts=path.replace('\\','/').split('/')
        if path.startswith(('/','\\')) or any(p in ('..','.') or ':' in p or '\x00' in p for p in parts):raise TorrentError('Torrent has unsafe file paths.')
        # Reject symlinks before any payload can be written.
        if int(files.file_flags(index))&8:raise TorrentError('Torrent symlinks are unsupported.')
        size=int(files.file_size(index));extension=Path(path).suffix.lower()
        if extension in VIDEO and size>0:
            name=' / '.join(parts[1:] or parts)
            name=''.join(c if ord(c)>=32 and ord(c)!=127 else ' ' for c in name)[:350]
            result.append({'index':index,'name':name,'bytes':size})
    if not result:raise TorrentError('Torrent contains no supported video files.')
    if len(result)>1000:raise TorrentError('Torrent contains too many video choices.')
    return result

def select_file(files,request,pause=time.sleep,clock=time.monotonic,wait_seconds=1200):
    if len(files)==1:return files[0]['index']
    job=os.getenv('PORTAL_JOB_ID','');run=os.getenv('GITHUB_RUN_ID','')
    state={'job':job,'run_id':run,'status':'waiting_file','files':files}
    try:current=request(job,state_dir='torrent')
    except urllib.error.HTTPError as error:
        if error.code!=404:raise
        current=None
    request(job,state,current.get('sha') if current else None,state_dir='torrent')
    print('Multiple torrent videos found. Select a file in the portal within 20 minutes; payload download is paused.',flush=True)
    deadline=clock()+wait_seconds
    while clock()<deadline:
        pause(min(30,max(0,deadline-clock())))
        current=request(job,state_dir='torrent');updated=json.loads(base64.b64decode(current['content']).decode())
        if updated.get('job')!=job or str(updated.get('run_id'))!=run:raise TorrentError('Torrent selection belongs to another run.')
        selected=updated.get('selected')
        if updated.get('status')=='selected' and type(selected) is int and any(f['index']==selected for f in files):
            updated['status']='downloading';request(job,updated,current['sha'],state_dir='torrent');return selected
    state['status']='expired'
    try:request(job,state,current['sha'],state_dir='torrent')
    except Exception:pass
    raise TorrentError('Torrent file selection expired after 20 minutes. No video was downloaded.')

def public_filter(lt):
    rules=lt.ip_filter()
    for cidr in ['0.0.0.0/8','10.0.0.0/8','100.64.0.0/10','127.0.0.0/8','169.254.0.0/16','172.16.0.0/12','192.168.0.0/16','192.0.0.0/24','198.18.0.0/15','224.0.0.0/3','::/128','::1/128','::ffff:0:0/96','fc00::/7','fe80::/10','ff00::/8']:
        net=ipaddress.ip_network(cidr);rules.add_rule(str(net.network_address),str(net.broadcast_address),1)
    return rules

def prepare(lt,descriptor,root):
    kind=descriptor.get('kind');job=os.getenv('PORTAL_JOB_ID','');trackers=[]
    if kind=='file':
        path=descriptor.get('path','')
        if not re.fullmatch(r'[a-f0-9]{32}',job) or path!=f'.clixarena/torrents/{job}.torrent':raise TorrentError('Invalid repository torrent path.')
        asset=root/path
        if asset.is_symlink() or root not in asset.resolve().parents or not asset.is_file() or asset.stat().st_size>1024*1024:raise TorrentError('Torrent file is missing, oversized or unsafe.')
        blob=asset.read_bytes();meta=lt.bdecode(blob)
        if not meta or not isinstance(meta.get(b'info'),dict) or meta[b'info'].get(b'private')==1 or b'pieces' not in meta[b'info']:raise TorrentError('Only public BitTorrent v1 torrents are supported.')
        trackers=[meta.get(b'announce',b'')]
        trackers += [value for tier in meta.get(b'announce-list',[]) if isinstance(tier,list) for value in tier]
        # Only validated public UDP announces are restored below. No HTTP/web seeds.
        for key in (b'announce',b'announce-list',b'url-list',b'httpseeds',b'nodes'):meta.pop(key,None)
        params=lt.load_torrent_buffer(lt.bencode(meta))
    elif kind=='magnet':
        value=descriptor.get('magnet','');query=urllib.parse.parse_qs(urllib.parse.urlsplit(value).query)
        xt=next((x for x in query.get('xt',[]) if re.fullmatch(r'urn:btih:(?:[a-fA-F0-9]{40}|[A-Za-z2-7]{32})',x)),None)
        if not value.startswith('magnet:?') or not xt:raise TorrentError('Use a valid public BitTorrent v1 magnet.')
        trackers=query.get('tr',[])
        params=lt.parse_magnet_uri('magnet:?xt='+xt)
    else:raise TorrentError('Invalid torrent source.')
    params.trackers=public_trackers(trackers);params.tracker_tiers=list(range(len(params.trackers)));params.url_seeds=[];params.http_seeds=[]
    params.flags &= ~lt.torrent_flags.auto_managed
    params.flags |= lt.torrent_flags.upload_mode
    params.flags &= ~lt.torrent_flags.paused
    return params

class SeedrClient:
    """Standalone client for official Seedr.cc REST API v1 using HTTP Basic Authentication."""
    BASE_REST = "https://www.seedr.cc/rest"

    def __init__(self, email="", password=""):
        self.email = (email or "").strip()
        self.password = (password or "").strip()
        if not self.email or not self.password:
            raise TorrentError("Seedr credentials (SEEDR_EMAIL and SEEDR_PASSWORD) are required.")

    def _auth_header(self):
        user_pass = f"{self.email}:{self.password}".encode("utf-8")
        b64 = base64.b64encode(user_pass).decode("utf-8")
        return f"Basic {b64}"

    def request(self, endpoint, method="GET", data=None, files=None):
        url = f"{self.BASE_REST}/{endpoint.lstrip('/')}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Authorization": self._auth_header(),
            "Accept": "application/json",
        }

        encoded_body = None
        if files:
            boundary = uuid.uuid4().hex
            headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
            parts = []
            for k, v in (data or {}).items():
                parts.append(f"--{boundary}".encode("utf-8"))
                parts.append(f'Content-Disposition: form-data; name="{k}"'.encode("utf-8"))
                parts.append(b"")
                parts.append(str(v).encode("utf-8"))
            for field_name, file_tuple in files.items():
                fname, fbytes = file_tuple
                parts.append(f"--{boundary}".encode("utf-8"))
                parts.append(f'Content-Disposition: form-data; name="{field_name}"; filename="{fname}"'.encode("utf-8"))
                parts.append(b"Content-Type: application/x-bittorrent")
                parts.append(b"")
                parts.append(fbytes)
            parts.append(f"--{boundary}--".encode("utf-8"))
            parts.append(b"")
            encoded_body = b"\r\n".join(parts)
        elif data is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            encoded_body = urllib.parse.urlencode(data).encode("utf-8")

        req = urllib.request.Request(url, data=encoded_body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                resp_bytes = resp.read()
                if not resp_bytes:
                    return {}
                return json.loads(resp_bytes.decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_text = ""
            try:
                err_text = e.read().decode("utf-8")
            except Exception:
                pass
            raise TorrentError(f"Seedr REST request '{endpoint}' failed (HTTP {e.code}: {e.reason}) - {err_text[:200]}")

    def add_magnet(self, magnet_link):
        return self.request("transfer/magnet", method="POST", data={"magnet": magnet_link})

    def add_torrent_file(self, torrent_bytes, filename="upload.torrent"):
        files = {"file": (filename, torrent_bytes)}
        return self.request("transfer/file", method="POST", files=files)

    def get_transfer(self, transfer_id):
        return self.request(f"transfer/{transfer_id}", method="GET")

    def delete_transfer(self, transfer_id):
        try:
            return self.request(f"transfer/{transfer_id}", method="DELETE")
        except Exception as e:
            print(f"::warning::Seedr cleanup failed for transfer {transfer_id}: {e}", flush=True)
            return None

    def list_folder(self, folder_id=""):
        endpoint = f"folder/{folder_id}" if folder_id else "folder"
        return self.request(endpoint, method="GET")

    def delete_folder(self, folder_id):
        try:
            return self.request(f"folder/{folder_id}", method="DELETE")
        except Exception as e:
            print(f"::warning::Seedr cleanup failed for folder {folder_id}: {e}", flush=True)
            return None

    def delete_file(self, file_id):
        try:
            return self.request(f"file/{file_id}", method="DELETE")
        except Exception as e:
            print(f"::warning::Seedr cleanup failed for file {file_id}: {e}", flush=True)
            return None

    def get_file_download_url(self, file_id):
        """Resolves the 302 redirect from /rest/file/{id} to get the direct CDN download URL."""
        class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        url = f"{self.BASE_REST}/file/{file_id}"
        opener = urllib.request.build_opener(NoRedirectHandler)
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Authorization": self._auth_header(),
        })
        try:
            resp = opener.open(req)
            # If server returned 200 directly with url or data
            return resp.geturl()
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308):
                location = e.headers.get("Location")
                if location:
                    return location
            raise TorrentError(f"Seedr failed to retrieve file download link (HTTP {e.code}: {e.reason})")

def download_via_seedr(descriptor, target, limit, root, request):
    """Downloads torrent or magnet using Seedr cloud caching and direct high-speed HTTP transfer."""
    email = os.getenv("SEEDR_EMAIL", "").strip()
    password = os.getenv("SEEDR_PASSWORD", "").strip()
    if not (email and password):
        return False

    kind = descriptor.get("kind")
    magnet = descriptor.get("magnet")
    torrent_bytes = None
    torrent_filename = "upload.torrent"

    if kind == "file":
        path = descriptor.get("path", "")
        job = os.getenv("PORTAL_JOB_ID", "")
        if not re.fullmatch(r"[a-f0-9]{32}", job) or path != f".clixarena/torrents/{job}.torrent":
            return False
        asset = root / path
        if asset.is_file() and not asset.is_symlink() and asset.stat().st_size <= 1024 * 1024:
            torrent_bytes = asset.read_bytes()
            torrent_filename = f"{job}.torrent"
        else:
            return False
    elif kind == "magnet" and magnet:
        pass
    else:
        return False

    print("Seedr credentials detected. Attempting high-speed cloud caching via Seedr REST API...", flush=True)
    try:
        client = SeedrClient(email=email, password=password)
    except Exception as e:
        print(f"::warning::Seedr init failed ({e}). Falling back to libtorrent direct P2P.", flush=True)
        return False

    transfer_id = None
    try:
        # Step 1: Add torrent magnet or file to Seedr
        if torrent_bytes:
            print("Uploading .torrent file to Seedr REST endpoint...", flush=True)
            res = client.add_torrent_file(torrent_bytes, filename=torrent_filename)
        else:
            print("Submitting magnet link to Seedr REST endpoint...", flush=True)
            res = client.add_magnet(magnet)

        if not res or not res.get("result", True):
            print(f"::warning::Seedr rejected transfer ({res}). Falling back to libtorrent.", flush=True)
            return False

        transfer = res.get("transfer") or {}
        transfer_id = transfer.get("id")
        title = transfer.get("title") or "Torrent"
        print(f"Torrent '{title}' queued on Seedr (Transfer ID: {transfer_id}). Waiting for cloud caching...", flush=True)

        # Step 2: Poll transfer status or folder until complete
        deadline = time.monotonic() + 900  # 15 minutes timeout
        completed_folder_id = None
        last_progress_log = 0

        while time.monotonic() < deadline:
            t_status = None
            if transfer_id:
                try:
                    t_data = client.get_transfer(transfer_id)
                    t_status = t_data.get("transfer") or {}
                except Exception:
                    t_status = None

            if t_status and t_status.get("status") in ("queued", "downloading"):
                now = time.monotonic()
                if now - last_progress_log >= 10:
                    prog = t_status.get("progress", 0)
                    seeds = t_status.get("seeds", 0)
                    print(f"Seedr cloud download {prog}% | seeders: {seeds}", flush=True)
                    last_progress_log = now
                time.sleep(3)
                continue

            if t_status and t_status.get("folder_id"):
                completed_folder_id = str(t_status.get("folder_id"))
                break

            # If transfer completed or finished, look in root folder
            root_contents = client.list_folder()
            folders = root_contents.get("folders", [])
            files = root_contents.get("files", [])

            # Check if any folder matches title or has video files
            matched_folder = None
            if title and title != "Torrent":
                matched_folder = next((f for f in folders if f.get("name") == title), None)
            if not matched_folder and folders:
                matched_folder = folders[-1]

            if matched_folder:
                completed_folder_id = str(matched_folder.get("id"))
                break

            # If single file in root folder
            v_root_files = [f for f in files if Path(f.get("name", "")).suffix.lower() in VIDEO and f.get("size", 0) > 0]
            if v_root_files:
                break

            time.sleep(3)

        # Step 3: Fetch file choices from folder or root
        completed_files = []
        if completed_folder_id:
            folder_data = client.list_folder(completed_folder_id)
            completed_files = [f for f in folder_data.get("files", []) if Path(f.get("name", "")).suffix.lower() in VIDEO and f.get("size", 0) > 0]
        else:
            root_contents = client.list_folder()
            completed_files = [f for f in root_contents.get("files", []) if Path(f.get("name", "")).suffix.lower() in VIDEO and f.get("size", 0) > 0]

        if not completed_files:
            print("::warning::Seedr transfer did not produce playable video within time limit. Falling back to libtorrent.", flush=True)
            if transfer_id:
                client.delete_transfer(transfer_id)
            return False

        # Step 4: Multi-video handling! If multiple video files exist, trigger selection in Portal UI
        file_choices = []
        for idx, f in enumerate(completed_files):
            file_choices.append({
                "index": idx,
                "name": f.get("name", f"file-{idx}"),
                "bytes": int(f.get("size", 0)),
                "raw_file": f,
            })

        if len(file_choices) == 1:
            selected_file = file_choices[0]
            print(f"Single video found in torrent: '{selected_file['name']}'", flush=True)
        else:
            print(f"Multiple videos found in torrent ({len(file_choices)} files). Waiting for user selection in portal...", flush=True)
            portal_choices = [{"index": c["index"], "name": c["name"], "bytes": c["bytes"]} for c in file_choices]
            sel_idx = select_file(portal_choices, request)
            selected_file = next(c for c in file_choices if c["index"] == sel_idx)
            print(f"User selected: '{selected_file['name']}'", flush=True)

        size = selected_file["bytes"]
        if size > limit:
            raise TorrentError("Selected video exceeds the 12 GB source limit.")
        if shutil.disk_usage(".").free < size * 3 + 2 * 1024**3:
            raise TorrentError("Not enough runner space for this video and processing. Select a smaller file.")

        raw = selected_file["raw_file"]
        file_id = raw.get("id")
        download_url = client.get_file_download_url(file_id)

        print(f"Downloading '{selected_file['name']}' ({size / (1024**2):.1f} MB) at high speed from Seedr CDN...", flush=True)

        # Step 5: Stream download directly to target with progress
        target_path = Path(target)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        req_dl = urllib.request.Request(download_url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "*/*",
        })
        with urllib.request.urlopen(req_dl, timeout=120) as resp, open(target_path, "wb") as out_f:
            copied = 0
            last_dl_log = time.monotonic()
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                out_f.write(chunk)
                copied += len(chunk)
                if time.monotonic() - last_dl_log >= 10:
                    print(f"Seedr CDN transfer {min(100.0, copied / size * 100):.1f}% ({copied / 1024**2:.1f} MB)", flush=True)
                    last_dl_log = time.monotonic()

        if target_path.stat().st_size != size:
            raise TorrentError("Downloaded Seedr video size mismatch.")

        print("Seedr high-speed download successfully completed!", flush=True)

        # Step 6: Cleanup from Seedr cloud storage to keep 2GB free storage available
        print("Cleaning up Seedr cloud storage for subsequent jobs...", flush=True)
        if completed_folder_id:
            client.delete_folder(completed_folder_id)
        else:
            client.delete_file(file_id)

        return True

    except Exception as e:
        print(f"::warning::Seedr download failed ({e}). Falling back to libtorrent direct P2P.", flush=True)
        return False


def start_runner_torrserver():
    """Starts TorrServer on localhost:8090 if binary is present and not already running."""
    import subprocess
    cmd = shutil.which('torrserver') or shutil.which('/usr/local/bin/torrserver') or shutil.which('/tmp/torrserver')
    if not cmd:
        for p in ['/usr/local/bin/torrserver', '/tmp/torrserver', 'torrserver']:
            if Path(p).is_file() and os.access(p, os.X_OK):
                cmd = p
                break
    if not cmd:
        return None

    # Check if already responding
    try:
        req = urllib.request.Request('http://127.0.0.1:8090/echo', headers={'User-Agent': 'ClixArena'})
        with urllib.request.urlopen(req, timeout=2) as resp:
            if resp.status == 200:
                return 'http://127.0.0.1:8090'
    except Exception:
        pass

    db_dir = Path('/tmp/torr_db')
    db_dir.mkdir(parents=True, exist_ok=True)
    log_file = Path('/tmp/torrserver.log')
    try:
        log_fd = open(log_file, 'w', encoding='utf-8')
        proc = subprocess.Popen(
            [cmd, '-p', '8090', '-d', str(db_dir)],
            stdout=log_fd,
            stderr=subprocess.STDOUT
        )
    except Exception as e:
        print(f"::warning::Failed to launch local TorrServer process ({e})", flush=True)
        return None

    # Wait for server to come online
    for _ in range(15):
        time.sleep(1)
        try:
            req = urllib.request.Request('http://127.0.0.1:8090/echo', headers={'User-Agent': 'ClixArena'})
            with urllib.request.urlopen(req, timeout=1) as resp:
                if resp.status == 200:
                    print("Local TorrServer engine active on port 8090", flush=True)
                    return 'http://127.0.0.1:8090'
        except Exception:
            continue

    return None


def download_via_torrserver(descriptor, target, limit, root, request):
    """Downloads torrent video using local TorrServer runner engine with direct HTTP streaming."""
    server = start_runner_torrserver()
    if not server:
        return False

    kind = descriptor.get('kind')
    magnet = descriptor.get('magnet')
    torrent_data_b64 = None

    if kind == 'file':
        path = descriptor.get('path', '')
        if not path:
            return False
        fpath = (root / path).resolve()
        if not fpath.is_file():
            return False
        with open(fpath, 'rb') as f:
            torrent_data_b64 = base64.b64encode(f.read()).decode('utf-8')
    elif kind == 'magnet':
        if not magnet:
            return False
    else:
        return False

    print("Submitting torrent to runner-local TorrServer...", flush=True)

    # 1. Add torrent to TorrServer
    payload = {
        'action': 'add',
        'link': magnet if kind == 'magnet' else None,
        'torrent': torrent_data_b64 if kind == 'file' else None,
        'title': 'ClixArena Video',
        'save_to_db': True
    }
    # remove None keys
    payload = {k: v for k, v in payload.items() if v is not None}

    req = urllib.request.Request(
        f'{server}/torrents',
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json', 'User-Agent': 'ClixArena'}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            tinfo = json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        print(f"::warning::Failed to add torrent to TorrServer ({e})", flush=True)
        return False

    torr_hash = tinfo.get('hash')
    if not torr_hash:
        print("::warning::TorrServer did not return a torrent hash", flush=True)
        return False

    print(f"TorrServer tracking hash: {torr_hash}. Waiting for metadata & video file list...", flush=True)

    # 2. Poll for file list
    deadline = time.monotonic() + 300
    files_list = []
    title = tinfo.get('title', 'Video')
    while time.monotonic() < deadline:
        time.sleep(2)
        try:
            req_get = urllib.request.Request(
                f'{server}/torrents',
                data=json.dumps({'action': 'get', 'hash': torr_hash}).encode('utf-8'),
                headers={'Content-Type': 'application/json', 'User-Agent': 'ClixArena'}
            )
            with urllib.request.urlopen(req_get, timeout=10) as r:
                curr = json.loads(r.read().decode('utf-8'))
                fl = curr.get('file_stats') or curr.get('files')
                if fl and len(fl) > 0:
                    files_list = fl
                    title = curr.get('title') or title
                    break
        except Exception:
            continue

    if not files_list:
        print("::warning::TorrServer metadata discovery timed out.", flush=True)
        return False

    # 3. Filter candidate video files
    video_exts = {'.mkv', '.mp4', '.webm', '.avi', '.mov', '.m4v', '.ts'}
    video_choices = []
    for f in files_list:
        idx = f.get('id')
        fname = f.get('path', '')
        size = f.get('length', 0)
        ext = Path(fname).suffix.lower()
        if ext in video_exts and size > 0:
            clean_name = Path(fname).name
            video_choices.append({'index': idx, 'name': clean_name, 'bytes': size, 'raw_file': f})

    if not video_choices:
        raise TorrentError("Torrent contains no supported video files.")

    # 4. Selection
    if len(video_choices) == 1:
        chosen = video_choices[0]
        print(f"Single video found in torrent: '{chosen['name']}'", flush=True)
    else:
        print(f"Multiple videos found in torrent ({len(video_choices)} files). Waiting for portal selection...", flush=True)
        portal_choices = [{'index': c['index'], 'name': c['name'], 'bytes': c['bytes']} for c in video_choices]
        sel_idx = select_file(portal_choices, request)
        chosen = next(c for c in video_choices if c['index'] == sel_idx)
        print(f"User selected: '{chosen['name']}'", flush=True)

    size = chosen['bytes']
    if size > limit:
        raise TorrentError("Selected video exceeds the 12 GB source limit.")
    if shutil.disk_usage(".").free < size * 2 + 2 * 1024**3:
        raise TorrentError("Not enough runner space for this video and processing. Select a smaller file.")

    file_id = chosen['index']
    # TorrServer direct streaming URL
    stream_url = f"{server}/stream/{urllib.parse.quote(chosen['name'])}?link={torr_hash}&index={file_id}&play"

    print(f"Streaming and downloading '{chosen['name']}' ({size / (1024**2):.1f} MB) from local TorrServer engine...", flush=True)

    target_path = Path(target)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    # 5. Stream data from TorrServer to target file with progress
    req_dl = urllib.request.Request(stream_url, headers={'User-Agent': 'ClixArena'})
    try:
        with urllib.request.urlopen(req_dl, timeout=300) as resp, open(target_path, 'wb') as out_f:
            copied = 0
            last_dl_log = time.monotonic()
            while True:
                chunk = resp.read(2 * 1024 * 1024)
                if not chunk:
                    break
                out_f.write(chunk)
                copied += len(chunk)
                if time.monotonic() - last_dl_log >= 10:
                    print(f"TorrServer download progress: {min(100.0, copied / size * 100):.1f}% ({copied / (1024**2):.1f} MB)", flush=True)
                    last_dl_log = time.monotonic()

        if target_path.stat().st_size != size:
            print("::warning::TorrServer downloaded file size mismatch. Retrying with libtorrent...", flush=True)
            return False

        print("TorrServer download successfully completed!", flush=True)

        # 6. Cleanup torrent from TorrServer memory/disk
        try:
            req_rem = urllib.request.Request(
                f'{server}/torrents',
                data=json.dumps({'action': 'rem', 'hash': torr_hash}).encode('utf-8'),
                headers={'Content-Type': 'application/json', 'User-Agent': 'ClixArena'}
            )
            urllib.request.urlopen(req_rem, timeout=5)
        except Exception:
            pass

        return True

    except Exception as e:
        print(f"::warning::TorrServer stream download failed ({e}). Falling back to libtorrent...", flush=True)
        return False

def download_torrent(descriptor,target,limit,root,request):
    # Try ultra-fast runner TorrServer engine first
    try:
        if download_via_torrserver(descriptor, target, limit, root, request):
            return target
    except Exception as e:
        print(f"::warning::TorrServer resolver exception: {e}. Continuing with fallbacks...", flush=True)

    # Try high-speed Seedr cloud caching first if credentials are configured
    try:
        if download_via_seedr(descriptor, target, limit, root, request):
            return target
    except Exception as e:
        print(f"::warning::Seedr resolver exception: {e}. Continuing with libtorrent...", flush=True)

    try:import libtorrent as lt
    except ImportError:raise TorrentError('libtorrent is missing. Update the Actions workflow and media tools.') from None
    folder=Path('.torrent-payload').resolve();folder.mkdir()
    session=lt.session({'listen_interfaces':'0.0.0.0:0','enable_dht':True,'dht_bootstrap_nodes':'dht.libtorrent.org:25401,router.bittorrent.com:6881,dht.transmissionbt.com:6881','use_dht_as_fallback':False,'apply_ip_filter_to_trackers':True,'enable_lsd':False,'enable_upnp':False,'enable_natpmp':False,'upload_rate_limit':1024,'connections_limit':80,'max_metadata_size':1024*1024})
    session.set_ip_filter(public_filter(lt));handle=None;counters={'replies':0,'errors':0}
    try:
        params=prepare(lt,descriptor,root);params.save_path=str(folder)
        if params.ti:
            choices=candidates(params.ti);params.file_priorities=[0]*params.ti.num_files()
        handle=session.add_torrent(params)
        print(f'Fetching torrent metadata; public UDP trackers {len(getattr(params,"trackers",[]))}; DHT bootstrap enabled',flush=True)
        deadline=time.monotonic()+600;last_metadata=0
        while not handle.has_metadata():
            if time.monotonic()>=deadline:raise TorrentError('Torrent metadata timed out after 10 minutes. Check public seeders.')
            if handle.status().errc.value():raise TorrentError('Torrent metadata failed. Raw peer errors were suppressed.')
            if time.monotonic()-last_metadata>=30:
                print('Finding metadata peers | '+discovery(session,handle,lt,counters),flush=True);last_metadata=time.monotonic()
            time.sleep(2)
        info=handle.torrent_file()
        if info.priv():raise TorrentError('Private torrents are unsupported.')
        choices=candidates(info);handle.prioritize_files([0]*info.num_files())
        # Keep every destination flat and generated, independent of untrusted names.
        for i in range(info.num_files()):handle.rename_file(i,f'file-{i:05d}'+Path(info.files().file_path(i)).suffix.lower()[:12])
        selected=select_file(choices,request)
        size=next(f['bytes'] for f in choices if f['index']==selected)
        if size>limit:raise TorrentError('Selected video exceeds the 12 GB source limit.')
        if shutil.disk_usage(folder).free<size*3+2*1024**3:raise TorrentError('Not enough runner space for this video and MP4 processing. Select a smaller file.')
        priorities=[0]*info.num_files();priorities[selected]=4;handle.prioritize_files(priorities)
        handle.unset_flags(lt.torrent_flags.upload_mode)
        handle.force_reannounce();handle.force_dht_announce()
        started=time.monotonic();last=0;last_bytes=0;last_progress=started
        print('Downloading only the selected torrent video; boundary pieces may include small parts of adjacent files.',flush=True)
        while True:
            status=handle.status();progress=handle.file_progress(lt.torrent_handle.piece_granularity)[selected];now=time.monotonic()
            if status.errc.value():raise TorrentError('Torrent download failed. Raw peer errors were suppressed.')
            if progress>last_bytes:last_bytes=progress;last_progress=now
            if now-last>=30:
                print(f'Torrent download {min(100,progress/size*100):.1f}% | {status.download_rate/1024**2:.2f} MiB/s | peers {status.num_peers} | '+discovery(session,handle,lt,counters),flush=True);last=now
                if status.num_peers==0 and now-started>=120:print('::warning::No connected peers yet. Check DHT nodes/tracker replies; zero may indicate blocked UDP/discovery, while working discovery with no peers may indicate no available seeders.',flush=True)
            if progress>=size:break
            if now-started>7200:raise TorrentError('Torrent download exceeded the 2-hour limit.')
            if now-last_progress>900:raise TorrentError('Torrent made no download progress for 15 minutes. Check public seeders before rerunning.')
            time.sleep(2)
        handle.pause();session.remove_torrent(handle);handle=None
        file=folder/f'file-{selected:05d}{Path(info.files().file_path(selected)).suffix.lower()[:12]}'
        # torrent_file may reflect renamed paths; the destination is still generated.
        if not file.is_file() or file.is_symlink() or file.stat().st_size!=size:raise TorrentError('Selected torrent video did not complete correctly.')
        shutil.move(str(file),str(target));print('Selected torrent video complete; piece hashes verified by libtorrent.',flush=True)
    finally:
        if handle is not None:handle.pause();session.remove_torrent(handle)
        session.pause()
        # session must release its disk worker before the directory is cleaned by the outer job.
