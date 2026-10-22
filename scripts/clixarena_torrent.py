"""Public torrent video selection and piece-verified download. No raw alerts/URLs."""
import base64
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import socket
import time
import urllib.error
import urllib.parse

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

def download_torrent(descriptor,target,limit,root,request):
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

