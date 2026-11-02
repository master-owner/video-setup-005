"""Upload providers and backward-compatible mappings.
Configured strictly for Abyss.to and Byse.sx (FileMoon).
Automatically computes embed_url for both hosts.
"""
import json, re, subprocess, time, urllib.parse, urllib.request, urllib.error
from clixarena_upload_metrics import run_upload, file_size

SUPPORTED_HOSTS = ('abyss', 'byse')

def get_embed_url(provider, file_id):
    if provider == 'abyss':
        return f"https://player.abyssplayer.com/{file_id}"
    elif provider in ('byse', 'filemoon'):
        return f"https://byse.sx/e/{file_id}"
    return None

def selected_host(layout):
    value = layout.get('uploadHost', 'abyss')
    if value in ('filemoon',):
        value = 'byse'
    if value not in SUPPORTED_HOSTS:
        value = 'abyss'
    return value

def selected_hosts(layout):
    first = selected_host(layout)
    hosts = [first]
    for key in ('secondUploadHost', 'thirdUploadHost', 'fourthUploadHost'):
        val = layout.get(key, '')
        if val in ('filemoon',):
            val = 'byse'
        if val in SUPPORTED_HOSTS and val not in hosts:
            hosts.append(val)
    return hosts

def merge_provider_mapping(rows, identity, file_id, provider='abyss', quality_ids=None):
    if provider in ('filemoon',):
        provider = 'byse'
    if provider not in SUPPORTED_HOSTS or not isinstance(file_id, str) or not re.fullmatch('[A-Za-z0-9_-]{1,128}', file_id):
        raise ValueError('Invalid host or video ID')
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
        if row.get('abyss_id') and 'abyss' not in providers:
            providers['abyss'] = {'file_id': row['abyss_id'], 'embed_url': get_embed_url('abyss', row['abyss_id'])}
        if row.get('byse_id') and 'byse' not in providers:
            providers['byse'] = {'file_id': row['byse_id'], 'embed_url': get_embed_url('byse', row['byse_id'])}
        if row.get('filemoon_id') and 'byse' not in providers:
            providers['byse'] = {'file_id': row['filemoon_id'], 'embed_url': get_embed_url('byse', row['filemoon_id'])}
        if row.get('file_id') and row.get('provider') in SUPPORTED_HOSTS and row.get('provider') not in providers:
            providers[row['provider']] = {'file_id': row['file_id'], 'embed_url': get_embed_url(row['provider'], row['file_id'])}

    embed_url = get_embed_url(provider, file_id)
    entry = {'file_id': file_id, 'embed_url': embed_url}
    if quality_ids:
        entry['quality_ids'] = quality_ids
    providers[provider] = entry
    
    item = dict(identity, provider=provider, providers=providers, file_id=file_id, embed_url=embed_url)
    if provider == 'abyss':
        item['abyss_id'] = file_id
        item['abyss_embed_url'] = embed_url
    elif provider == 'byse':
        item['byse_id'] = file_id
        item['filemoon_id'] = file_id
        item['byse_embed_url'] = embed_url
        item['filemoon_embed_url'] = embed_url
    return [row for row in rows if not matches(row)] + [item]

def episode_key(identity):
    return 'episode'

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None

class HostError(ValueError):
    """Only fixed text and numeric status codes may be exposed to job logs."""

def upload_abyss(path, name, key, mask=lambda _: None, run=subprocess.run):
    if not key or len(key) > 512 or any(ord(c) < 33 for c in key):
        raise ValueError('ABYSS_API_KEY is missing or invalid')
    if any(c in name for c in ('\r', '\n', '\x00')):
        raise ValueError('Invalid output name')
    url = 'https://up.abyss.to/' + urllib.parse.quote(key, safe='')
    mask(key)
    mask(url)
    escaped = url.replace('\\', '\\\\').replace('"', '\\"')
    filename = name.replace('\\', '\\\\').replace('"', '\\"')
    form = 'file=@' + str(path) + ';filename="' + filename + '"'

    response = run_upload(
        ['curl', '--config', '-', '--silent', '--fail', '--proto', '=https',
         '--connect-timeout', '30', '--keepalive-time', '15', '--max-time', '3600',
         '--speed-limit', '1024', '--speed-time', '60',
         '--http1.1',
         '--tcp-nodelay',
         '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
         '--form', form],
        input='url = "' + escaped + '"\n',
        capture_output=True,
        text=True,
        timeout=3610,
        runner=run,
        label='Abyss',
        size=file_size(path)
    )
    if response.returncode != 0:
        raise ValueError('Abyss upload failed or timed out. Check the account before uploading again.')

    try:
        body = json.loads(response.stdout)
    except (ValueError, TypeError):
        raise ValueError('Abyss upload response was not understood. Check account before uploading again.') from None

    slug = body.get('slug') if isinstance(body, dict) else None
    if not isinstance(slug, str) or not re.fullmatch('[A-Za-z0-9_-]{1,128}', slug):
        raise ValueError('Abyss upload returned no valid video ID')

    return slug

def verify_abyss(file_id, key, size, name, attempts=6, pause=time.sleep):
    url = 'https://api.abyss.to/v1/files/' + file_id + '?key=' + urllib.parse.quote(key, safe='')
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    for attempt in range(attempts):
        try:
            opener = urllib.request.build_opener(NoRedirect)
            with opener.open(req, timeout=30) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                if data.get('id') == file_id:
                    return True, True
        except Exception:
            pass
        if attempt < attempts - 1:
            pause(5)
    return False, False

def get_byse_upload_server(key):
    """Retrieve active upload server node from Byse API (api.byse.sx/upload/server)."""
    url = f"https://api.byse.sx/upload/server?key={urllib.parse.quote(key, safe='')}"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
    with urllib.request.urlopen(req, timeout=30) as response:
        data = json.loads(response.read().decode('utf-8'))
        if data.get('status') == 200 and data.get('result'):
            return data['result']
        raise ValueError(f"Failed to get Byse upload server: {data.get('msg', 'Unknown error')}")

def upload_byse(path, name, key, mask=lambda _: None, run=subprocess.run):
    if not key or len(key) > 512 or any(ord(c) < 33 for c in key):
        raise ValueError('BYSE_API_KEY is missing or invalid')
    if any(c in name for c in ('\r', '\n', '\x00')):
        raise ValueError('Invalid output name')
    
    mask(key)
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            print(f'Byse upload attempt {attempt} of {max_attempts}...', flush=True)
            upload_server = get_byse_upload_server(key)
            mask(upload_server)
            print(f'Byse upload node obtained: {upload_server}', flush=True)

            filename = name.replace('\\', '\\\\').replace('"', '\\"')
            form_file = 'file=@' + str(path) + ';filename="' + filename + '"'
            
            response = run_upload(
                ['curl', '--silent', '--fail', '--proto', '=https',
                 '--connect-timeout', '30', '--keepalive-time', '15', '--max-time', '3600',
                 '--speed-limit', '1024', '--speed-time', '60',
                 '--http1.1',
                 '--tcp-nodelay',
                 '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
                 '-F', f'key={key}',
                 '-F', form_file,
                 upload_server],
                input='',
                capture_output=True,
                text=True,
                timeout=3610,
                runner=run,
                label='Byse',
                size=file_size(path)
            )
            if response.returncode != 0:
                raise ValueError(f'Byse upload returned non-zero code {response.returncode}')
            
            body = json.loads(response.stdout)
            files = body.get('files', []) if isinstance(body, dict) else []
            if not files or not isinstance(files, list):
                raise ValueError(f"Byse upload returned unexpected body: {body.get('msg', 'No files in response')}")
            
            filecode = files[0].get('filecode')
            if not isinstance(filecode, str) or not re.fullmatch('[A-Za-z0-9_-]{1,128}', filecode):
                raise ValueError('Byse upload returned no valid video ID')
            
            return filecode
        except Exception as error:
            print(f'::warning::Byse upload attempt {attempt} failed: {error}', flush=True)
            if attempt < max_attempts:
                wait_s = attempt * 10
                print(f'Retrying Byse upload in {wait_s}s without re-encoding...', flush=True)
                time.sleep(wait_s)
            else:
                raise ValueError('Byse upload failed after all retry attempts. Check network and account.') from None

def verify_byse(file_id, key, size, name, attempts=6, pause=time.sleep):
    """Check Byse file status via API."""
    url = f"https://api.byse.sx/file/info?key={urllib.parse.quote(key, safe='')}&file_code={file_id}"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                if data.get('status') == 200:
                    result = data.get('result', [])
                    if result and len(result) > 0 and result[0].get('status') == 200:
                        return True, True
        except Exception:
            pass
        if attempt < attempts - 1:
            pause(5)
    return False, False
