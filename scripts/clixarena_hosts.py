"""Upload providers and backward-compatible mappings. No upload retries.
Configured solely for Abyss.to.
"""
import json, re, subprocess, time, urllib.parse, urllib.request, urllib.error
from clixarena_upload_metrics import run_upload, file_size


def selected_host(layout):
    value = layout.get('uploadHost', 'abyss')
    if value != 'abyss':
        raise ValueError('Abyss is the only supported upload host')
    return 'abyss'


def selected_hosts(layout):
    # Only Abyss is retained
    return ['abyss']


def merge_provider_mapping(rows, identity, file_id, provider='abyss', quality_ids=None):
    if provider != 'abyss' or not isinstance(file_id, str) or not re.fullmatch('[A-Za-z0-9_-]{1,128}', file_id):
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
            providers['abyss'] = {'file_id': row['abyss_id']}
        if row.get('file_id') and 'abyss' not in providers and row.get('provider') == 'abyss':
            providers['abyss'] = {'file_id': row['file_id']}

    entry = {'file_id': file_id}
    providers['abyss'] = entry
    item = dict(identity, provider='abyss', providers=providers, abyss_id=file_id, file_id=file_id)
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

    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            print(f'Abyss upload attempt {attempt} of {max_attempts}...', flush=True)
            response = run_upload(
                ['curl', '--config', '-', '--silent', '--fail', '--proto', '=https',
                 '--connect-timeout', '30', '--keepalive-time', '5', '--max-time', '3600',
                 '--speed-limit', '1024', '--speed-time', '30',
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
                raise ValueError(f'Abyss upload returned non-zero code {response.returncode}')

            body = json.loads(response.stdout)
            slug = body.get('slug') if isinstance(body, dict) else None
            if not isinstance(slug, str) or not re.fullmatch('[A-Za-z0-9_-]{1,128}', slug):
                raise ValueError('Abyss upload returned no valid video ID')

            return slug
        except Exception as error:
            print(f'::warning::Abyss upload attempt {attempt} failed: {error}', flush=True)
            if attempt < max_attempts:
                wait_s = attempt * 10
                print(f'Retrying Abyss upload in {wait_s}s without re-encoding...', flush=True)
                time.sleep(wait_s)
            else:
                raise ValueError('Abyss upload failed after all retry attempts. Check network and account.') from None


def verify_abyss(file_id, key, size, name, attempts=6, pause=time.sleep):
    url = 'https://api.abyss.to/v1/files/' + file_id + '?key=' + urllib.parse.quote(key, safe='')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    for attempt in range(attempts):
        try:
            with opener.open(urllib.request.Request(url, headers={'Accept': 'application/json'}), timeout=30) as response:
                data = json.loads(response.read(1024 * 1024))
            if data.get('id') == file_id and int(data.get('size', -1)) == size:
                return True, data.get('name') == name
        except Exception:
            pass
        if attempt < attempts - 1:
            pause(5)
    return False, False
