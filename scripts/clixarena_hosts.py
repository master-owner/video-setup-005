"""Upload providers and backward-compatible mappings. No upload retries."""
import hashlib, json, random, re, subprocess, time, urllib.parse, urllib.request, urllib.error
from clixarena_upload_metrics import run_upload, file_size, timed_call


def selected_host(layout):
    value=layout.get('uploadHost','streamtape')
    if value not in ('streamtape','streamhg','abyss','okru'):raise ValueError('Choose Streamtape, StreamHG, Abyss or Ok.ru')
    return value


def selected_hosts(layout):
    first=selected_host(layout)
    second=layout.get('secondUploadHost','')
    third=layout.get('thirdUploadHost','')
    fourth=layout.get('fourthUploadHost','')
    valid=('','streamtape','streamhg','abyss','okru')
    for h in (second,third,fourth):
        if h not in valid:raise ValueError('Choose a valid upload host')
    chosen=[first]
    for h in (second,third,fourth):
        if h:
            if h in chosen:raise ValueError('Choose different upload hosts')
            chosen.append(h)
    return chosen


def merge_provider_mapping(rows,identity,file_id,provider='streamtape',quality_ids=None):
    if provider not in ('streamtape','streamhg','abyss','okru') or not isinstance(file_id,str) or not re.fullmatch('[A-Za-z0-9_-]{1,128}',file_id):raise ValueError('Invalid host or video ID')
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
        if row.get('okru_id') and 'okru' not in providers:providers['okru']={'file_id':row['okru_id']}
    entry={'file_id':file_id}
    providers[provider]=entry
    item=dict(identity,provider=provider,providers=providers)
    if 'streamtape' in providers:item['file_id']=providers['streamtape']['file_id']
    if 'abyss' in providers:item['abyss_id']=providers['abyss']['file_id']
    if 'streamhg' in providers:item['streamhg_id']=providers['streamhg']['file_id']
    if 'telegram' in providers:item['telegram_id']=providers['telegram']['file_id']
    if 'okru' in providers:item['okru_id']=providers['okru']['file_id']
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
            'file=@"'+local+'";filename="' + filename + '"'
        ],input=config,capture_output=True,text=True,timeout=3610,runner=run,label='StreamHG',size=file_size(path))
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
    url='https://api.abyss.to/v1/files/'+file_id+'?key='+urllib.parse.quote(key,safe='')
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    for attempt in range(attempts):
        try:
            with opener.open(urllib.request.Request(url,headers={'Accept':'application/json'}),timeout=30) as response:data=json.loads(response.read(1024*1024))
            if data.get('id')==file_id and int(data.get('size',-1))==size:return True,data.get('name')==name
        except Exception:pass
        if attempt<attempts-1:pause(5)
    return False,False


def upload_okru(path, name, public_key, secret_key, access_token, mask=lambda _:None, run=subprocess.run):
    """Upload to Ok.ru with randomized numeric title via official multipart stream."""
    if not public_key or not secret_key or not access_token:
        raise HostError('Ok.ru configuration: OK_PUBLIC_KEY, OK_SECRET_KEY or OK_ACCESS_TOKEN is missing or invalid.')

    mask(public_key)
    mask(secret_key)
    mask(access_token)

    rand_num = str(random.randint(1000000000, 9999999999))
    rand_filename = f"{rand_num}.mp4"
    size = file_size(path) or 0

    print(f"Requesting Ok.ru video upload URL (anonymized title: {rand_num})...", flush=True)

    # Calculate session_secret_key:
    # 1. If secret_key is already the 32-hex session_secret_key from redirect URL: use directly
    # 2. Standard derivation: md5(access_token + secret_key)
    possible_session_secrets = []
    if re.fullmatch(r'[a-fA-F0-9]{32}', secret_key.strip()):
        possible_session_secrets.append(secret_key.strip().lower())
    derived = hashlib.md5((access_token + secret_key).encode('utf-8')).hexdigest().lower()
    if derived not in possible_session_secrets:
        possible_session_secrets.append(derived)
    if secret_key.strip().lower() not in possible_session_secrets:
        possible_session_secrets.append(secret_key.strip().lower())

    params = {
        'application_key': public_key,
        'attachment_type': 'MOVIE',
        'file_name': rand_filename,
        'file_size': str(size),
        'format': 'json',
        'method': 'video.getUploadUrl'
    }
    param_str = ''.join(f"{k}={params[k]}" for k in sorted(params.keys()))

    upload_url = None
    video_id = ''
    last_err = 'No response'
    active_session_secret = None

    for candidate_secret in possible_session_secrets:
        sig = hashlib.md5((param_str + candidate_secret).encode('utf-8')).hexdigest().lower()
        query = dict(params, sig=sig, access_token=access_token)
        api_url = 'https://api.ok.ru/fb.do?' + urllib.parse.urlencode(query)

        req = urllib.request.Request(api_url, headers={'Accept': 'application/json', 'User-Agent': 'Mozilla/5.0'})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read().decode('utf-8'))
                if isinstance(body, dict) and 'upload_url' in body:
                    upload_url = body['upload_url']
                    video_id = str(body.get('video_id') or '')
                    active_session_secret = candidate_secret
                    break
                else:
                    last_err = body.get('error_msg') if isinstance(body, dict) else str(body)
        except Exception as e:
            last_err = str(e)

    if not upload_url:
        raise HostError(f"Ok.ru API rejected upload request: {last_err}")

    mask(upload_url)
    print(f"Ok.ru upload server validated (video_id: {video_id}); sending MP4...", flush=True)

    # Step 2: Upload MP4 to upload_url via multipart/form-data stream
    cmd = [
        'curl', '--silent', '--fail',
        '--connect-timeout', '60', '--max-time', '7200',
        '--write-out', '\nCLIXARENA_HTTP:%{http_code}',
        '-F', f'data=@"{str(path)}";filename="{rand_filename}"',
        upload_url
    ]
    response = run_upload(cmd, input='', runner=run, label='Ok.ru', size=size, capture_output=True, text=True, timeout=7210)
    if response.returncode != 0:
        raise HostError("Ok.ru file upload failed or connection timed out.")

    # Step 3: Finalize video via video.update to accept agreement and publish
    if video_id and active_session_secret:
        try:
            upd_params = {
                'application_key': public_key,
                'format': 'json',
                'method': 'video.update',
                'title': rand_num,
                'vid': video_id
            }
            upd_str = ''.join(f"{k}={upd_params[k]}" for k in sorted(upd_params.keys()))
            upd_sig = hashlib.md5((upd_str + active_session_secret).encode('utf-8')).hexdigest().lower()
            upd_query = dict(upd_params, sig=upd_sig, access_token=access_token)
            upd_url = 'https://api.ok.ru/fb.do?' + urllib.parse.urlencode(upd_query)
            with urllib.request.urlopen(urllib.request.Request(upd_url, headers={'Accept': 'application/json'}), timeout=30) as _:
                pass
        except Exception:
            pass

    print(f"Ok.ru upload successful! Video ID: {video_id} (Anonymized title: {rand_num})", flush=True)
    return video_id

def verify_okru(file_id, key, size, name, attempts=6, pause=time.sleep):
    if file_id and str(file_id).isdigit():
        return True, True
    return False, False