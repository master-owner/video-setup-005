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
FALLBACK_TRACKERS=['udp://tracker.opentrackr.org:1337/announce','udp://open.stealth.si:80/announce','udp://tracker.torrent.eu.org:451/announce']

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
    """Lightweight standalone client for Seedr.cc REST and Extension OAuth APIs."""
    TOKEN_URL = "https://www.seedr.cc/oauth_test/token.php"
    RESOURCE_URL = "https://www.seedr.cc/oauth_test/resource.php"

    def __init__(self, email="", password="", token=""):
        self.email = (email or "").strip()
        self.password = (password or "").strip()
        self.token = (token or "").strip()

    def authenticate(self):
        if self.token:
            return self.token
        if not self.email or not self.password:
            raise TorrentError("Seedr credentials (SEEDR_EMAIL and SEEDR_PASSWORD or SEEDR_TOKEN) are required.")
        payload = urllib.parse.urlencode({
            "grant_type": "password",
            "client_id": "seedr_chrome",
            "type": "login",
            "username": self.email,
            "password": self.password,
        }).encode("utf-8")
        req = urllib.request.Request(self.TOKEN_URL, data=payload, headers={
            "User-Agent": "Mozilla/5.0",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                self.token = data.get("access_token") or ""
                if not self.token:
                    raise TorrentError("Seedr login failed: No access token received.")
                return self.token
        except urllib.error.HTTPError as e:
            try:
                err_body = json.loads(e.read().decode("utf-8"))
                desc = err_body.get("error_description") or err_body.get("error") or str(e)
            except Exception:
                desc = str(e)
            raise TorrentError(f"Seedr authentication error: {desc}")
        except Exception as e:
            raise TorrentError(f"Seedr connection error during login: {e}")

    def call_resource(self, func, data=None, files=None):
        if not self.token:
            self.authenticate()
        query = urllib.parse.urlencode({"access_token": self.token, "func": func})
        url = f"{self.RESOURCE_URL}?{query}"

        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        }

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
        else:
            encoded_body = urllib.parse.urlencode(data or {}).encode("utf-8") if data is not None else None

        req = urllib.request.Request(url, data=encoded_body, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                body = json.loads(e.read().decode("utf-8"))
                if body.get("error") == "expired_token" and self.email and self.password:
                    self.token = ""
                    self.authenticate()
                    return self.call_resource(func, data, files)
            except Exception:
                pass
            raise

    def list_contents(self, folder_id="0"):
        return self.call_resource("list_contents", {"content_type": "folder", "content_id": str(folder_id)})

    def add_torrent(self, magnet=None, torrent_file_bytes=None, torrent_filename="upload.torrent"):
        if torrent_file_bytes:
            files = {"torrent_file": (torrent_filename, torrent_file_bytes)}
            return self.call_resource("add_torrent", {"folder_id": "-1"}, files=files)
        return self.call_resource("add_torrent", {"torrent_magnet": magnet, "folder_id": "-1"})

    def fetch_file(self, folder_file_id):
        return self.call_resource("fetch_file", {"folder_file_id": str(folder_file_id)})

    def delete_item(self, item_type, item_id):
        try:
            payload = {"delete_arr": json.dumps([{"type": item_type, "id": str(item_id)}])}
            return self.call_resource("delete", payload)
        except Exception as e:
            print(f"::warning::Seedr cleanup failed for {item_type} {item_id}: {e}", flush=True)
            return None

def download_via_seedr(descriptor, target, limit, root, request):
    """Downloads torrent or magnet using Seedr cloud caching and direct high-speed HTTP transfer."""
    email = os.getenv("SEEDR_EMAIL", "").strip()
    password = os.getenv("SEEDR_PASSWORD", "").strip()
    token = os.getenv("SEEDR_TOKEN", "").strip()
    if not token and not (email and password):
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

    print("Seedr credentials detected. Attempting high-speed cloud caching via Seedr...", flush=True)
    client = SeedrClient(email=email, password=password, token=token)
    try:
        client.authenticate()
    except Exception as e:
        print(f"::warning::Seedr authentication failed ({e}). Falling back to libtorrent direct P2P.", flush=True)
        return False

    try:
        # Step 1: Add torrent magnet or file to Seedr
        if torrent_bytes:
            print("Uploading .torrent file to Seedr...", flush=True)
            add_res = client.add_torrent(torrent_file_bytes=torrent_bytes, torrent_filename=torrent_filename)
        else:
            add_res = client.add_torrent(magnet=magnet)

        user_torrent_id = add_res.get("user_torrent_id")
        title = add_res.get("title") or "Torrent"
        if not add_res.get("result", True):
            print(f"::warning::Seedr rejected torrent ({add_res}). Falling back to libtorrent.", flush=True)
            return False

        print(f"Torrent '{title}' queued on Seedr (ID: {user_torrent_id}). Waiting for cloud caching...", flush=True)

        # Step 2: Poll status until complete or timeout
        deadline = time.monotonic() + 900  # 15 minutes timeout
        completed_folder = None
        completed_files = None
        last_progress_log = 0

        while time.monotonic() < deadline:
            contents = client.list_contents("0")
            torrents = contents.get("torrents", [])
            folders = contents.get("folders", [])
            root_files = contents.get("files", [])

            active = None
            if user_torrent_id:
                active = next((t for t in torrents if str(t.get("id")) == str(user_torrent_id)), None)
            if not active and torrents:
                active = torrents[0]

            if active:
                now = time.monotonic()
                if now - last_progress_log >= 10:
                    prog = active.get("progress", "0")
                    rate = active.get("download_rate", 0) / (1024 * 1024)
                    print(f"Seedr cloud download {prog}% | {rate:.2f} MiB/s | seeders: {active.get('seeders', 0)}", flush=True)
                    last_progress_log = now
            else:
                # Active transfer is no longer in torrents queue; it should have landed in folders or files
                for fld in folders:
                    sub = client.list_contents(str(fld.get("id")))
                    v_files = [f for f in sub.get("files", []) if Path(f.get("name", "")).suffix.lower() in VIDEO and f.get("size", 0) > 0]
                    if v_files:
                        completed_folder = fld
                        completed_files = v_files
                        break

                if not completed_files and root_files:
                    v_files = [f for f in root_files if Path(f.get("name", "")).suffix.lower() in VIDEO and f.get("size", 0) > 0]
                    if v_files:
                        completed_files = v_files

                if completed_files:
                    break

            time.sleep(3)

        if not completed_files:
            print("::warning::Seedr transfer did not finish within time limit. Falling back to libtorrent.", flush=True)
            if user_torrent_id:
                client.delete_item("torrent", user_torrent_id)
            return False

        # Step 3: Multi-video handling! If multiple video files exist, trigger selection in Portal UI
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
        folder_file_id = raw.get("folder_file_id") or raw.get("id")
        file_info = client.fetch_file(folder_file_id)
        download_url = file_info.get("url")
        if not download_url:
            raise TorrentError("Seedr failed to generate direct download link.")

        print(f"Downloading '{selected_file['name']}' ({size / (1024**2):.1f} MB) at high speed from Seedr CDN...", flush=True)

        # Step 4: Stream download directly to target with progress
        target_path = Path(target)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        req_dl = urllib.request.Request(download_url, headers={
            "User-Agent": "Mozilla/5.0",
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

        # Step 5: Cleanup from Seedr cloud storage to keep 2GB free storage available
        print("Cleaning up Seedr cloud storage for subsequent jobs...", flush=True)
        if completed_folder:
            client.delete_item("folder", completed_folder.get("id"))
        else:
            client.delete_item("file", folder_file_id)

        return True

    except TorrentError:
        raise
    except Exception as e:
        print(f"::warning::Seedr download failed ({e}). Falling back to libtorrent direct P2P.", flush=True)
        return False

def download_torrent(descriptor,target,limit,root,request):
    # Try high-speed Seedr cloud caching first if credentials are configured
    try:
        if download_via_seedr(descriptor, target, limit, root, request):
            return target
    except TorrentError:
        raise
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
