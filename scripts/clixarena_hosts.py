"""Upload providers and backward-compatible mappings. No upload retries."""
import asyncio, json, math, os, random, re, subprocess, tempfile, time, urllib.parse, urllib.request, urllib.error
from pathlib import Path
from clixarena_upload_metrics import run_upload, file_size, timed_call

# Guarantee an active event loop in MainThread for Pyrogram 2.x
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

try:
    import pyrogram
    from pyrogram import Client, utils

    # Monkey-patch Pyrogram MIN_CHANNEL_ID bug for modern 64-bit Telegram channels (-1002...)
    utils.MIN_CHANNEL_ID = -1009999999999
    utils.MIN_CHAT_ID = -999999999999
    def _patch_peer_type(peer_id: int) -> str:
        s = str(peer_id)
        if not s.startswith('-'):
            return 'user'
        elif s.startswith('-100'):
            return 'channel'
        else:
            return 'chat'
    utils.get_peer_type = _patch_peer_type
except ImportError:
    Client = None


def selected_host(layout):
    value=layout.get('uploadHost','streamtape')
    if value not in ('streamtape','streamhg','abyss','telegram'):raise ValueError('Choose Streamtape, StreamHG, Abyss or Telegram')
    return value


def selected_hosts(layout):
    first=selected_host(layout)
    second=layout.get('secondUploadHost','')
    third=layout.get('thirdUploadHost','')
    fourth=layout.get('fourthUploadHost','')
    valid=('','streamtape','streamhg','abyss','telegram')
    for h in (second,third,fourth):
        if h not in valid:raise ValueError('Choose a valid upload host')
    chosen=[first]
    for h in (second,third,fourth):
        if h:
            if h in chosen:raise ValueError('Choose different upload hosts')
            chosen.append(h)
    return chosen


def merge_provider_mapping(rows,identity,file_id,provider='streamtape',quality_ids=None):
    if provider not in ('streamtape','streamhg','abyss','telegram') or not isinstance(file_id,str) or not re.fullmatch('[A-Za-z0-9_-]{1,128}',file_id):raise ValueError('Invalid host or video ID')
    if not isinstance(rows,list) or any(not isinstance(row,dict) for row in rows):raise ValueError('Invalid mappings')
    key=('mal_id' if 'mal_id' in identity else 'anilist_id') if identity['type']=='anime' else 'tmdb_id'
    matches=lambda row:all(row.get(k)==identity.get(k) for k in ('type',key,'season','episode'))
    providers={}
    for row in rows:
        if not matches(row):continue
        if isinstance(row.get('providers'),dict):providers.update(row['providers'])
        if row.get('file_id') and 'streamtape' not in providers:providers['streamtape']={'file_id':row['file_id']}
        if row.get('abyss_id') and 'abyss' not in providers:providers['abyss']={'file_id':row['abyss_id']}
        if row.get('streamhg_id') and 'streamhg' not in providers:providers['streamhg']={'file_id':row['streamhg_id']}
        if row.get('telegram_id') and 'telegram' not in providers:providers['telegram']={'file_id':row['telegram_id']}
    entry={'file_id':file_id}
    if provider=='telegram':
        if '-' in file_id:
            entry['parts']=[p for p in file_id.split('-') if p.isdigit()]
        if quality_ids and isinstance(quality_ids,dict):
            existing_qualities = dict(providers.get('telegram',{}).get('qualities',{}) if isinstance(providers.get('telegram'),dict) else {})
            entry['qualities']=existing_qualities
            for q,qid in quality_ids.items():
                q_ent={'file_id':qid}
                if '-' in qid:
                    q_ent['parts']=[p for p in qid.split('-') if p.isdigit()]
                entry['qualities'][q]=q_ent
    providers[provider]=entry
    item=dict(identity,provider=provider,providers=providers)
    if 'streamtape' in providers:item['file_id']=providers['streamtape']['file_id']
    if 'abyss' in providers:item['abyss_id']=providers['abyss']['file_id']
    if 'streamhg' in providers:item['streamhg_id']=providers['streamhg']['file_id']
    if 'telegram' in providers:item['telegram_id']=providers['telegram']['file_id']
    return [row for row in rows if not matches(row)]+[item]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


class HostError(ValueError):
    """Only fixed text and numeric status codes may be exposed to job logs."""


def _status(value):
    return str(value) if type(value) is int and 100<=value<=599 else value if isinstance(value,str) and re.fullmatch(r'[1-5][0-9]{2}',value) else 'unknown'


def streamhg_api(endpoint,key,params=None):
    if not key or len(key)>512 or any(ord(c)<33 for c in key):
        raise HostError('StreamHG configuration: STREAMHG_API_KEY is missing or invalid.')
    url='https://streamhgapi.com/api/'+endpoint+'?'+urllib.parse.urlencode(dict(params or {},key=key))
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    try:
        with opener.open(urllib.request.Request(url,headers={'Accept':'application/json','User-Agent':'Mozilla/5.0'}),timeout=30) as response:
            raw=response.read(1024*1024+1)
    except urllib.error.HTTPError as error:
        raise HostError('StreamHG API discovery/info: HTTP '+_status(error.code)+'. Check API key, account access or host blocking. No file upload was started by this request.') from None
    except Exception:
        raise HostError('StreamHG API discovery/info: connection or TLS failure. No file upload was started by this request.') from None
    if len(raw)>1024*1024:raise HostError('StreamHG API discovery/info: oversized response; upload not started.')
    try:body=json.loads(raw)
    except (ValueError,TypeError):raise HostError('StreamHG API discovery/info: expected JSON but received another format, possibly a protection page. Upload not started.') from None
    if not isinstance(body,dict) or _status(body.get('status'))!='200':
        code=_status(body.get('status')) if isinstance(body,dict) else 'unknown'
        raise HostError('StreamHG API discovery/info: rejected request (API status '+code+'). Check STREAMHG_API_KEY and account access.')
    return body


def upload_streamhg(path,name,key,mask=lambda _:None,run=subprocess.run,validate=None):
    if any(c in name for c in ('\r','\n','\x00')):raise HostError('StreamHG configuration: invalid output name.')
    mask(key)
    print('Requesting StreamHG upload server',flush=True)
    server=timed_call('StreamHG upload-server lookup',streamhg_api,'upload/server',key).get('result')
    if not isinstance(server,str):raise HostError('StreamHG server discovery: upload URL missing in response; upload not started.')
    try:
        parsed=urllib.parse.urlsplit(server)
        if (parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password
                or parsed.fragment or parsed.port not in (None,443) or not parsed.path.startswith('/upload/')):
            raise ValueError()
        if validate is None:
            import socket,ipaddress
            addresses=socket.getaddrinfo(parsed.hostname,443,type=socket.SOCK_STREAM)
            if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):raise ValueError()
        else:validate(server)
    except Exception:
        raise HostError('StreamHG server discovery: returned endpoint failed public HTTPS validation; upload not started.') from None
    mask(server)
    print('StreamHG upload server validated; sending MP4',flush=True)
    quote=lambda v:v.replace('\\','\\\\').replace('"','\\"').replace('\r','').replace('\n','')
    filename=quote(name);local=quote(str(path))
    config='url = "'+quote(server)+'"\nform-string = "'+quote('key='+key)+'"\n'
    config+='form-string = "'+quote('file_title='+name.removesuffix('.mp4'))+'"\n'
    try:
        response=run_upload(['curl','--config','-','--silent','--fail','--proto','=https',
            '--user-agent','Mozilla/5.0','--connect-timeout','30','--max-time','3600',
            '--write-out','\nCLIXARENA_HTTP:%{http_code}','--form',
            'file=@"'+local+'";filename="'+filename+'"'],input=config,capture_output=True,text=True,timeout=3610,runner=run,label='StreamHG',size=file_size(path))
    except subprocess.TimeoutExpired:
        raise HostError('StreamHG file upload: timed out. Upload state is uncertain; check the account before retrying.') from None
    except Exception:
        raise HostError('StreamHG file upload: uploader could not finish. Check curl installation and the account before retrying.') from None
    raw,marker,http=response.stdout.rpartition('\nCLIXARENA_HTTP:')
    if not marker:raw=response.stdout;http='unknown'
    http=_status(http.strip())
    if response.returncode:
        hints={6:'DNS lookup failed',7:'could not connect to upload server',22:'HTTP request rejected',
               28:'upload timed out',35:'TLS handshake failed',60:'TLS certificate verification failed',
               26:'local output file could not be read',52:'upload server returned no response',56:'connection interrupted'}
        hint=hints.get(response.returncode,'uploader failed')
        raise HostError('StreamHG file upload: '+hint+' (curl '+str(int(response.returncode))+', HTTP '+http+'). Check the account before retrying; upload is not repeated automatically.')
    try:body=json.loads(raw)
    except (ValueError,TypeError):
        raise HostError('StreamHG upload response: expected JSON (HTTP '+http+'). Upload may exist; check account before retrying.') from None
    if not isinstance(body,dict) or _status(body.get('status'))!='200':
        code=_status(body.get('status')) if isinstance(body,dict) else 'unknown'
        raise HostError('StreamHG upload response: rejected or unsupported response (API status '+code+', HTTP '+http+'). Check account quota and upload permissions before retrying.')
    files=body.get('files')
    if not isinstance(files,list) or len(files)!=1 or not isinstance(files[0],dict):
        raise HostError('StreamHG upload response: expected one file result. Upload may exist; check account before retrying.')
    if str(files[0].get('status','')).upper()!='OK':
        raise HostError('StreamHG upload response: file was not accepted. Check account quota, file limits and upload permissions before retrying.')
    code=files[0].get('filecode')
    if not isinstance(code,str) or not re.fullmatch('[A-Za-z0-9]{1,128}',code):
        raise HostError('StreamHG upload response: accepted result has no valid filecode. Check account before retrying; no mapping was written.')
    return code


def verify_streamhg(file_id,key,size,name,attempts=6,pause=time.sleep):
    # Host transcoding changes sizes; verify the returned file ID and title.
    for attempt in range(attempts):
        try:
            rows=streamhg_api('file/info',key,{'file_code':file_id}).get('result')
            if isinstance(rows,list):
                for row in rows:
                    if isinstance(row,dict) and row.get('file_code')==file_id and row.get('status')==200:
                        return True,row.get('file_title') in (name,name.removesuffix('.mp4'))
        except Exception:pass
        if attempt<attempts-1:pause(5)
    return False,False


def upload_abyss(path,name,key,mask=lambda _:None,run=subprocess.run):
    if not key or len(key)>512 or any(ord(c)<33 for c in key):raise ValueError('ABYSS_API_KEY is missing or invalid')
    if any(c in name for c in ('\r','\n','\x00')):raise ValueError('Invalid output name')
    url='https://up.abyss.to/'+urllib.parse.quote(key,safe='')
    mask(key);mask(url)
    escaped=url.replace('\\','\\\\').replace('"','\\"')
    filename=name.replace('\\','\\\\').replace('"','\\"')
    form='file=@'+str(path)+';filename="'+filename+'"'
    response=run_upload(['curl','--config','-','--silent','--fail','--proto','=https','--connect-timeout','30','--max-time','3600','--form',form],input='url = "'+escaped+'"\n',capture_output=True,text=True,timeout=3610,runner=run,label='Abyss',size=file_size(path))
    if response.returncode:raise ValueError('Abyss upload failed or timed out. Check the account before uploading again.')
    try:body=json.loads(response.stdout)
    except (ValueError,TypeError):raise ValueError('Abyss upload response was not understood. Check account before uploading again.') from None
    slug=body.get('slug') if isinstance(body,dict) else None
    if not isinstance(slug,str) or not re.fullmatch('[A-Za-z0-9_-]{1,128}',slug):raise ValueError('Abyss upload returned no valid video ID. Check account before uploading again.')
    return slug


def verify_abyss(file_id,key,size,name,attempts=6,pause=time.sleep):
    # Read-only retries never repeat multipart upload; transcoding may still be waiting.
    url='https://api.abyss.to/v1/files/'+file_id+'?key='+urllib.parse.quote(key,safe='')
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    for attempt in range(attempts):
        try:
            with opener.open(urllib.request.Request(url,headers={'Accept':'application/json'}),timeout=30) as response:data=json.loads(response.read(1024*1024))
            if data.get('id')==file_id and int(data.get('size',-1))==size:return True,data.get('name')==name
        except Exception:pass
        if attempt<attempts-1:pause(5)
    return False,False


def get_media_duration(path, run=subprocess.run):
    try:
        res = run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', str(path)], capture_output=True, text=True, timeout=30)
        return float(res.stdout.strip())
    except Exception:
        return 0.0


async def _pyrogram_send_video(path, name, bot_token, chat_str, api_id, api_hash, bot_label="Telegram"):
    """Native AsyncIO upload for Pyrogram without threadpool contention or event loop crashes."""
    for attempt in range(1, 4):
        try:
            last_log_time = 0
            start_upload_time = time.time()
            def upload_progress(current, total):
                nonlocal last_log_time
                now = time.time()
                if now - last_log_time >= 5 or current == total:
                    last_log_time = now
                    pct = (current / total) * 100 if total else 0
                    elapsed = now - start_upload_time
                    speed = (current / elapsed) / (1024 * 1024) if elapsed > 0 else 0
                    curr_mb = current / (1024 * 1024)
                    tot_mb = total / (1024 * 1024)
                    eta_sec = (total - current) / (current / elapsed) if current > 0 and elapsed > 0 else 0
                    eta_m, eta_s = divmod(int(eta_sec), 60)
                    print(f"[{bot_label}] Uploading [{name}]: {pct:5.1f}% | {curr_mb:6.1f}MB / {tot_mb:6.1f}MB | {speed:4.1f} MB/s | ETA {eta_m}m {eta_s:02d}s", flush=True)

            session_name = f"uploader_{random.randint(100000, 999999)}"
            async with Client(session_name, api_id=int(api_id), api_hash=str(api_hash), bot_token=str(bot_token), in_memory=True, workers=8) as app:
                msg = await app.send_video(
                    chat_id=int(chat_str),
                    video=str(path),
                    caption=name,
                    file_name=name,
                    supports_streaming=True,
                    progress=upload_progress
                )
                return str(msg.id)
        except Exception as e:
            if attempt >= 3:
                raise HostError(f"Telegram Pyrogram upload failed on [{bot_label}]: {e}. Check bot permissions in storage channel.") from None
            print(f"[{bot_label}] Pyrogram upload attempt {attempt} failed ({e}). Auto-retrying in 5 seconds...", flush=True)
            await asyncio.sleep(5)


def _curl_send_video(path, name, bot_token, chat_str, mask, run, size):
    url = 'https://api.telegram.org/bot' + bot_token + '/sendVideo'
    mask(url)
    quote = lambda v: v.replace('\\', '\\\\').replace('"', '\\"').replace('\r', '').replace('\n', '')
    filename = quote(name)
    local = quote(str(path))
    config = 'url = "' + quote(url) + '"\nform-string = "chat_id=' + quote(chat_str) + '"\nform-string = "supports_streaming=true"\nform-string = "caption=' + quote(name) + '"\n'
    for attempt in range(1, 4):
        try:
            response = run_upload([
                'curl', '--config', '-', '--silent', '--fail', '--proto', '=https',
                '--connect-timeout', '30', '--max-time', '3600',
                '--write-out', '\nCLIXARENA_HTTP:%{http_code}',
                '--form', 'video=@"' + local + '";filename="' + filename + '"'
            ], input=config, capture_output=True, text=True, timeout=3610, runner=run, label='Telegram', size=size)

            raw, marker, http = response.stdout.rpartition('\nCLIXARENA_HTTP:')
            if not marker:
                raw = response.stdout
                http = 'unknown'
            http = _status(http.strip())

            if response.returncode:
                raise HostError('Telegram file upload failed (curl ' + str(int(response.returncode)) + ', HTTP ' + http + '). Check bot permissions in channel.')
            try:
                body = json.loads(raw)
            except (ValueError, TypeError):
                raise HostError('Telegram upload response: expected JSON (HTTP ' + http + '). Check bot and channel access.') from None

            if not isinstance(body, dict) or not body.get('ok'):
                desc = body.get('description', 'Unknown rejection') if isinstance(body, dict) else 'unknown'
                raise HostError('Telegram upload rejected: ' + desc + ' (HTTP ' + http + ').')

            result = body.get('result', {})
            msg_id = result.get('message_id')
            if not msg_id or not isinstance(msg_id, int):
                raise HostError('Telegram upload response: accepted result has no valid message_id.')
            return str(msg_id)
        except Exception as e:
            if attempt >= 3:
                raise
            print(f"Telegram upload attempt {attempt} failed ({e}). Auto-retrying in 5 seconds...", flush=True)
            time.sleep(5)


def _upload_telegram_single(path, name, bot_token, chat_str, api_id, api_hash, mask, run, bot_label="Telegram"):
    size = file_size(path) or 0
    chat_str = str(chat_str).strip()
    if chat_str.startswith('-100'):
        pass
    elif chat_str.startswith('-'):
        chat_str = f'-100{chat_str.lstrip("-")}'
    elif chat_str.startswith('100'):
        chat_str = f'-{chat_str}'
    else:
        chat_str = f'-100{chat_str}'

    if size >= 50 * 1024 * 1024 and api_id and api_hash:
        if Client is None:
            raise HostError("Pyrogram is required for Telegram files >= 50MB. Verify pyrogram installation.")
        return asyncio.run(_pyrogram_send_video(path, name, bot_token, chat_str, api_id, api_hash, bot_label=bot_label))

    return _curl_send_video(path, name, bot_token, chat_str, mask, run, size)


def upload_telegram(path, name, bot_token, chat_id, api_id=None, api_hash=None, mask=lambda _:None, run=subprocess.run):
    # Parse single or multiple bot tokens (supports TELEGRAM_BOT_TOKENS or comma-separated tokens in bot_token)
    env_tokens = os.environ.get('TELEGRAM_BOT_TOKENS', '').strip()
    raw_tokens = env_tokens or str(bot_token or '').strip()
    bot_token_list = [t.strip() for t in re.split(r'[\s,;]+', raw_tokens) if t.strip() and re.fullmatch(r'\d{8,12}:[A-Za-z0-9_-]{30,45}', t.strip())]
    if not bot_token_list:
        if bot_token and re.fullmatch(r'\d{8,12}:[A-Za-z0-9_-]{30,45}', str(bot_token).strip()):
            bot_token_list = [str(bot_token).strip()]
        else:
            raise HostError('Telegram configuration: TELEGRAM_BOT_TOKEN is missing or invalid format.')

    chat_str = str(chat_id).strip()
    if not chat_str or not re.fullmatch(r'-?\d{5,20}', chat_str):
        raise HostError('Telegram configuration: TELEGRAM_CHAT_ID is missing or invalid format.')
    if any(c in name for c in ('\r', '\n', '\x00')):
        raise HostError('Telegram configuration: invalid output name.')

    for tok in bot_token_list:
        mask(tok)
    mask(chat_str)
    if api_hash:
        mask(str(api_hash))

    primary_token = bot_token_list[0]
    size = file_size(path) or 0
    part_limit = 1995 * 1024 * 1024  # 1995MB safe boundary below 2000MB Telegram limit

    if size > part_limit:
        num_parts = math.ceil(size / part_limit)
        print(f"Telegram file size is {round(size / (1024 * 1024), 1)}MB (>2000MB). Automatically splitting into {num_parts} parts.", flush=True)

        total_duration = get_media_duration(path, run=run)
        if total_duration <= 0:
            total_duration = 7200.0  # fallback assumption 2 hours if probe unreadable

        part_duration = total_duration / num_parts
        base_name = name.removesuffix('.mp4')

        with tempfile.TemporaryDirectory() as tmpdir:
            parts_info = []
            for i in range(num_parts):
                part_path = Path(tmpdir) / f"{base_name}.part{i+1}.mp4"
                start_sec = i * part_duration
                cmd = [
                    'ffmpeg', '-y', '-ss', str(start_sec), '-i', str(path),
                    '-t', str(part_duration), '-c', 'copy', '-map', '0',
                    '-movflags', '+faststart', str(part_path)
                ]
                print(f"Splitting part {i+1} of {num_parts} via stream-copy (starts at {round(start_sec, 1)}s)...", flush=True)
                res = run(cmd, capture_output=True)
                if res.returncode != 0:
                    raise HostError(f"FFmpeg split failed on part {i+1}.")

                part_name = f"{base_name} - Part {i+1}.mp4"
                parts_info.append((i, part_path, part_name))

            # MULTI-BOT CONCURRENT ASYNCIO UPLOADING (ZERO THREADPOOL CRASH)
            use_pyrogram = any((file_size(p[1]) or 0) >= 50 * 1024 * 1024 for p in parts_info) and api_id and api_hash
            if use_pyrogram:
                if Client is None:
                    raise HostError("Pyrogram is required for Telegram files >= 50MB. Verify pyrogram installation.")

                async def _upload_all_parts():
                    tasks = []
                    for i, (part_idx, part_path, part_name) in enumerate(parts_info):
                        assigned_tok = bot_token_list[i % len(bot_token_list)]
                        bot_label = f"Bot #{i % len(bot_token_list) + 1}" if len(bot_token_list) > 1 else "Telegram"
                        print(f"[{bot_label}] Starting upload of part {i+1}/{num_parts}...", flush=True)
                        tasks.append(_pyrogram_send_video(part_path, part_name, assigned_tok, chat_str, api_id, api_hash, bot_label=bot_label))
                    return await asyncio.gather(*tasks)

                print(f"Multi-Bot Speed Boost: Uploading {num_parts} parts concurrently across {min(num_parts, len(bot_token_list))} bots via AsyncIO...", flush=True)
                part_results = asyncio.run(_upload_all_parts())
            else:
                part_results = []
                for i, (part_idx, part_path, part_name) in enumerate(parts_info):
                    assigned_tok = bot_token_list[i % len(bot_token_list)]
                    bot_label = f"Bot #{i % len(bot_token_list) + 1}" if len(bot_token_list) > 1 else "Telegram"
                    print(f"[{bot_label}] Starting upload of part {i+1}/{num_parts}...", flush=True)
                    p_id = _curl_send_video(part_path, part_name, assigned_tok, chat_str, mask, run, file_size(part_path))
                    part_results.append(p_id)

        combined_id = "-".join(part_results)
        print(f"All {num_parts} parts successfully uploaded to Telegram: {combined_id}", flush=True)
        return combined_id

    return _upload_telegram_single(path, name, primary_token, chat_str, api_id, api_hash, mask, run)


def verify_telegram(file_id, key, size, name, attempts=6, pause=time.sleep):
    if isinstance(file_id, (str, int)):
        parts = str(file_id).split('-')
        if all(p.isdigit() for p in parts):
            return True, True
    return False, False


def get_video_dimensions(path, run=subprocess.run):
    try:
        res = run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height', '-of', 'csv=s=x:p=0', str(path)], capture_output=True, text=True, timeout=30)
        w, h = res.stdout.strip().split('x')
        return int(w), int(h)
    except Exception:
        return 1280, 720


def transcode_resolution(input_path, output_path, target_height, run=subprocess.run):
    vf = f"scale=-2:{target_height}"
    crf = "22" if target_height >= 720 else "23"
    duration = get_media_duration(input_path, run=run)
    extra_flags = []
    if duration > 0:
        # Budget total file for 1750MB (video + audio + container) to guarantee file stays well below Telegram 1950MB limit
        total_kbps = int((1750 * 8192) / duration)
        safe_video_kbps = max(250, total_kbps - 220)
        if target_height >= 1080:
            cap_kbps = min(2400, safe_video_kbps)
            crf = "23"
        elif target_height >= 720:
            cap_kbps = min(1400, safe_video_kbps)
            crf = "23"
        else:
            cap_kbps = min(750, safe_video_kbps)
            crf = "24"
        extra_flags = ['-maxrate', f'{cap_kbps}k', '-bufsize', f'{cap_kbps * 2}k']
    cmd = [
        'ffmpeg', '-y', '-threads', '0', '-progress', 'pipe:1', '-nostats',
        '-i', str(input_path),
        '-vf', vf, '-c:v', 'libx264', '-preset', 'superfast', '-crf', crf,
        *extra_flags, '-sws_flags', 'fast_bilinear',
        '-c:a', 'copy', '-movflags', '+faststart', str(output_path)
    ]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        last_log = 0
        t0 = time.time()
        speed = '1.0x'
        while True:
            line = proc.stdout.readline()
            if not line and proc.poll() is not None:
                break
            line = line.strip()
            if line.startswith('speed='):
                speed = line.split('=')[1].strip()
            elif line.startswith('out_time_ms='):
                try:
                    out_ms = int(line.split('=')[1].strip())
                    curr_sec = out_ms / 1_000_000.0
                    now = time.time()
                    if now - last_log >= 5 or (duration > 0 and curr_sec >= duration):
                        last_log = now
                        pct = min(100.0, (curr_sec / duration) * 100.0) if duration > 0 else 0
                        elapsed = now - t0
                        eta_sec = (duration - curr_sec) / (curr_sec / elapsed) if curr_sec > 0 and elapsed > 0 else 0
                        eta_m, eta_s = divmod(int(eta_sec), 60)
                        print(f"Transcoding {target_height}p: {pct:5.1f}% | speed={speed} | elapsed={int(elapsed)}s | ETA {eta_m}m {eta_s:02d}s", flush=True)
                except Exception:
                    pass
        ret = proc.wait()
        if ret != 0:
            stderr_out = proc.stderr.read()
            raise HostError(f"Transcoding to {target_height}p failed (code {ret}): {stderr_out[-300:]}")
    except Exception as e:
        if isinstance(e, HostError):
            raise
        raise HostError(f"Transcoding to {target_height}p failed: {e}")


def upload_telegram_multi(path, name, bot_token, chat_id, api_id=None, api_hash=None, mask=lambda _:None, run=subprocess.run, mode='single'):
    w, h = get_video_dimensions(path, run=run)
    base_name = name.removesuffix('.mp4')
    quality_ids = {}

    if mode == 'single':
        q_name = '1080p' if h >= 900 else ('720p' if h >= 600 else '480p')
        print(f"Uploading single quality ({q_name}) video to Telegram...", flush=True)
        res_id = upload_telegram(path, name, bot_token, chat_id, api_id, api_hash, mask, run=run)
        quality_ids[q_name] = res_id
        return res_id, quality_ids

    with tempfile.TemporaryDirectory() as tmpdir:
        if h >= 900:
            targets = [('1080p', None), ('720p', 720), ('480p', 480)]
        elif h >= 600:
            targets = [('720p', None), ('480p', 480)]
        else:
            targets = [('480p', None)]

        orig_size = file_size(path) or 0
        part_limit = 1950 * 1024 * 1024

        primary_id = None
        prev_720p_path = None
        for q_name, target_h in targets:
            need_transcode = (target_h is not None)  # Master quality is never down-sampled; splits via -c copy if > 2GB
            if not need_transcode:
                q_path = path
                q_file_name = f"{base_name} [{q_name}].mp4"
                if q_name == '720p':
                    prev_720p_path = path
            else:
                h_to_use = target_h if target_h is not None else min(h, 1080)
                q_path = Path(tmpdir) / f"{base_name}_{q_name}.mp4"

                source_video = path
                if q_name == '480p' and prev_720p_path and prev_720p_path.exists():
                    source_video = prev_720p_path
                    print(f"Cascading Speedup: Transcoding {base_name} 480p directly from 720p source...", flush=True)
                else:
                    msg = "Bitrate Guard Active: Transcoding" if target_h is None else "Transcoding"
                    print(f"{msg} {base_name} to {q_name} ({h_to_use}p)...", flush=True)

                transcode_resolution(source_video, q_path, h_to_use, run=run)
                if q_name == '720p':
                    prev_720p_path = q_path
                q_file_name = f"{base_name} [{q_name}].mp4"

            print(f"Uploading {q_name} version to Telegram...", flush=True)
            res_id = upload_telegram(q_path, q_file_name, bot_token, chat_id, api_id, api_hash, mask, run=run)
            quality_ids[q_name] = res_id
            if primary_id is None:
                primary_id = res_id

    return primary_id, quality_ids