"""Manual Actions media pipeline. Never print remote responses or credentials."""
import base64
from clixarena_download_progress import DownloadProgress
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from clixarena_upload_metrics import run_upload, file_size, timed_stage, timed_call
import hashlib
import http.cookiejar
import http.client
from html.parser import HTMLParser
import ipaddress
import json
import os
from pathlib import Path
import re
import random
import shutil
import socket
import ssl
import urllib.error
import struct
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from clixarena_features import (settings, subtitle_asset, audio_selection, fetch_logo,
                                write_intro, video_dimensions, fitted_dimensions, filter_graph, encode_progress, logo_dimensions)
from clixarena_torrent import TorrentError, download_torrent
from clixarena_parallel import parallel_download
from clixarena_hosts import HostError,selected_host,selected_hosts,merge_provider_mapping,upload_streamhg,verify_streamhg,upload_abyss,verify_abyss,upload_telegram,verify_telegram,upload_telegram_multi

def download_source(url,target,limit,root):
    descriptor=json.loads(os.getenv('OVERLAY_LAYOUT','') or '{}').get('torrent')
    if descriptor is not None:
        if not isinstance(descriptor,dict):raise TorrentError('Invalid torrent source metadata.')
        return download_torrent(descriptor,target,limit,root,audio_job_request)
    for attempt in range(4):
        try:return download(url,target,limit)
        except ExpiredDownloadError:
            if attempt == 3:raise DownloadError('Replacement URL was rejected repeatedly; download stopped.') from None
            url=wait_for_download_url()


def wait_for_download_url():
    job=os.getenv('PORTAL_JOB_ID','')
    if not re.fullmatch('[a-f0-9]{32}',job):
        raise DownloadError('Download URL expired or access was denied. Submit a fresh URL in a new portal request.')
    request_id=os.urandom(16).hex()
    state={'job':job,'run_id':os.getenv('GITHUB_RUN_ID',''),'status':'waiting_url','request':request_id}
    audio_job_request(job,state,state_dir='download')
    print('Download URL expired or access was denied. Enter a fresh URL in Vercel within 20 minutes; this same run is waiting.',flush=True)
    deadline=time.monotonic()+1200
    while time.monotonic()<deadline:
        time.sleep(15)
        current=audio_job_request(job,state_dir='download')
        value=json.loads(base64.b64decode(current['content']))
        if value.get('job')!=job or value.get('run_id')!=state['run_id'] or value.get('request')!=request_id:continue
        if value.get('status')!='selected':continue
        rows=decrypt_mappings(value['encrypted'])
        row=rows[0]
        if row.get('job')!=job or row.get('run_id')!=state['run_id'] or row.get('request')!=request_id:
            raise DownloadError('Replacement URL belongs to another request.')
        url=row['url'];mask(url);validate_url(url)
        audio_job_request(job,dict(state,status='downloading'),current['sha'],state_dir='download')
        return url
    raise DownloadError('Fresh download URL was not supplied within 20 minutes.')


def mask(value):
    if not value:
        return
    escaped = value.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')
    if os.getenv('GITHUB_ACTIONS') == 'true':
        print('::add-mask::' + escaped, flush=True)


def validate_url(url):
    if any(ord(c) < 32 for c in url) or len(url) > 8192:
        raise ValueError('Invalid URL characters or length')
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError('Use a direct HTTPS URL without embedded credentials or fragments')
    if parsed.port not in (None, 443, 8443):
        raise ValueError('Only HTTPS ports 443 and 8443 are allowed')
    addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError('URL must resolve to a public Internet host')
    return url


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        mask(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), SafeRedirect())


class DownloadError(Exception):
    """Only fixed, credential-free messages may be used here."""



class ExpiredDownloadError(DownloadError):
    pass


class RetryableDownloadError(DownloadError):
    """Retry only downloads, never upload or workflow dispatch."""


class DriveConfirmation(HTMLParser):
    def __init__(self):
        super().__init__(); self.forms = []; self.current = None
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'form':
            self.current = {'action': attrs.get('action', ''), 'method': attrs.get('method', 'get').lower(), 'fields': {}}
        elif tag == 'input' and self.current is not None and attrs.get('name'):
            self.current['fields'][attrs['name']] = attrs.get('value', '')
    def handle_endtag(self, tag):
        if tag == 'form' and self.current is not None:
            self.forms.append(self.current); self.current = None


def resolve_video_source(url):
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qs(parsed.query)
    if parsed.hostname in ('drive.google.com', 'www.drive.google.com'):
        match = re.fullmatch(r'/file/d/([A-Za-z0-9_-]+)(?:/[^/]*)?/?', parsed.path)
        file_id = match.group(1) if match else query.get('id', [''])[0] if parsed.path in ('/open', '/uc') else ''
        if not re.fullmatch(r'[A-Za-z0-9_-]{5,200}', file_id):
            raise DownloadError('Use a Google Drive file share link, not a folder or document link.')
        params = {'export': 'download', 'id': file_id}
        resource_key = query.get('resourcekey', [''])[0]
        if resource_key:
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', resource_key):
                raise DownloadError('Invalid Google Drive resource key.')
            params['resourcekey'] = resource_key
        return 'https://drive.google.com/uc?' + urllib.parse.urlencode(params), file_id
    if parsed.hostname in ('www.dropbox.com', 'dropbox.com') and parsed.path.startswith(('/s/', '/scl/fi/')):
        params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        params = [(k, v) for k, v in params if k not in ('dl', 'raw')] + [('dl', '1')]
        return urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(params))), None
    return url, None


def open_video_response(opener, request, drive_id):
    for step in range(3):
        response = opener.open(request, timeout=60)
        if not drive_id or 'text/html' not in response.headers.get('Content-Type', '').lower():
            return response
        try:
            final_url = response.geturl()
            body = response.read(2 * 1024**2 + 1)
        finally:
            response.close()
        if len(body) > 2 * 1024**2:
            raise DownloadError('Google Drive returned an unexpectedly large confirmation page.')
        text = body.decode('utf-8', errors='replace')
        parser = DriveConfirmation(); parser.feed(text)
        confirmed = None
        for form in parser.forms:
            action = urllib.parse.urljoin(final_url, form['action'])
            parsed = urllib.parse.urlsplit(action)
            if (form['method'] == 'get' and parsed.scheme == 'https' and parsed.hostname in ('drive.google.com', 'drive.usercontent.google.com')
                    and parsed.path in ('/uc', '/download') and parsed.port in (None, 443) and not parsed.username and not parsed.password and not parsed.fragment):
                params = dict(urllib.parse.parse_qsl(parsed.query)); params.update(form['fields'])
                if params.get('id') != drive_id or params.get('export', 'download') != 'download' or not params.get('confirm'):
                    continue
                confirmed = urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(params)))
                break
        if confirmed is None:
            lower = text.lower()
            if any(x in lower for x in ('too many users', 'download quota', 'quota exceeded')):
                raise DownloadError('Google Drive download quota is exceeded. Wait or use another source.')
            raise DownloadError('Google Drive did not provide a downloadable file. Enable Anyone with the link and allow downloads; login, CAPTCHA and restricted files are unsupported.')
        validate_url(confirmed); mask(confirmed)
        request = urllib.request.Request(confirmed, headers={'User-Agent': 'Mozilla/5.0', 'Accept-Encoding': 'identity'})
    raise DownloadError('Google Drive download confirmation did not complete. Check sharing and download permissions.')


def download(url, target, limit):
    validate_url(url); mask(url)
    source, drive_id = resolve_video_source(url)
    if drive_id:
        print('Google Drive file link detected; resolving download', flush=True)
    connections=json.loads(os.getenv('OVERLAY_LAYOUT','') or '{}').get('downloadConnections',4)
    if type(connections) is not int or not 1<=connections<=16:
        raise DownloadError('Choose 1 to 16 download connections.')
    print('Download connections selected: '+str(connections),flush=True)
    parallel_source=source
    cookies=http.cookiejar.CookieJar()
    def parallel_opener():
        # Each worker owns its opener and cookie jar; never share mutable jars.
        import copy
        local=http.cookiejar.CookieJar()
        for cookie in cookies:local.set_cookie(copy.copy(cookie))
        return urllib.request.build_opener(urllib.request.ProxyHandler({}),SafeRedirect(),urllib.request.HTTPCookieProcessor(local))
    if drive_id and connections>1 and os.getenv('PARALLEL_DOWNLOAD','true').lower() not in ('0','false','off'):
        resolver=urllib.request.build_opener(urllib.request.ProxyHandler({}),SafeRedirect(),urllib.request.HTTPCookieProcessor(cookies))
        request=urllib.request.Request(source,headers={'User-Agent':'Mozilla/5.0','Accept-Encoding':'identity','Range':'bytes=0-0'})
        with open_video_response(resolver,request,drive_id) as response:
            parallel_source=response.geturl()
            if any(kind in response.headers.get('Content-Type','').lower() for kind in ('text/html','application/json')):
                raise DownloadError('Google Drive returned a page instead of downloadable media.')
            validate_url(parallel_source);mask(parallel_source)
        print('Google Drive download resolved; checking parallel support',flush=True)
    if parallel_download(parallel_source,target,limit,parallel_opener,validate_url,connections=connections,
            consume_full=lambda response:consume_video_response(response,target,limit)):
        return
    print('Using single-connection media download',flush=True)
    for attempt in range(3):
        cookies = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), SafeRedirect(), urllib.request.HTTPCookieProcessor(cookies)) if drive_id else OPENER
        try:
            _download_once(source, target, limit, opener, drive_id)
            return
        except RetryableDownloadError:
            target.unlink(missing_ok=True)
            if attempt == 2:
                raise
            print('Video download interrupted; retrying from the beginning (attempt ' + str(attempt + 2) + '/3)', flush=True)
            time.sleep(2 * (attempt + 1))
        except Exception:
            target.unlink(missing_ok=True)
            raise


def consume_video_response(response,target,limit):
    with target.open('wb') as output, DownloadProgress() as progress:
        content_type = response.headers.get('Content-Type', '').lower()
        if 'text/html' in content_type or 'application/json' in content_type:
            raise DownloadError('The server returned a webpage or API response instead of video. Obtain a fresh direct download link.')
        total = 0
        declared_size = response.headers.get('Content-Length', '')
        progress.total=int(declared_size) if declared_size.isdigit() and int(declared_size)>0 else None
        if declared_size.isdigit() and int(declared_size) > limit:
            raise DownloadError('Video exceeds the 12 GB download limit.')
        while block := response.read(1024 * 1024):
            if total == 0 and block.lstrip()[:32].lower().startswith((b'<!doctype html', b'<html')):
                raise DownloadError('The server returned an HTML page instead of video. Obtain a fresh direct download link.')
            total += len(block)
            if total > limit:
                raise DownloadError('Video exceeds the 12 GB download limit.')
            if shutil.disk_usage(target.parent).free < 2 * 1024**3:
                raise DownloadError('Runner disk space is below the 2 GB reserve.')
            output.write(block)
            progress.update(total)
        if declared_size.isdigit() and total != int(declared_size):
            raise RetryableDownloadError('Download is incomplete after three attempts: received size differs from the server Content-Length. The host may be truncating the file or reporting an incorrect size.')
        if not total:raise DownloadError('The server returned an empty file.')
        progress.finish()
    if not target.stat().st_size:
        raise DownloadError('The server returned an empty file.')
    print('Download complete; validating downloaded file', flush=True)


def _download_once(url, target, limit, opener=OPENER, drive_id=None):
    validate_url(url)
    mask(url)
    request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Accept': '*/*', 'Accept-Encoding': 'identity'})
    try:
        with open_video_response(opener, request, drive_id) as response:
            consume_video_response(response,target,limit)
    except urllib.error.HTTPError as error:
        hints = {401: 'Link requires authentication.', 403: 'Link expired, needs cookies, or the host blocked this runner.',
                 404: 'File was not found or the link expired.', 429: 'Host rate limit reached.'}
        kind = ExpiredDownloadError if error.code in (401,403,404,410) else RetryableDownloadError if error.code in (408, 429, 500, 502, 503, 504) else DownloadError
        raise kind('Download HTTP ' + str(int(error.code)) + '. ' + hints.get(error.code, 'Video host rejected the download request.')) from None
    except urllib.error.URLError as error:
        if isinstance(error.reason, ssl.SSLError):
            message = 'Download TLS certificate validation failed.'
        elif isinstance(error.reason, socket.gaierror):
            message = 'Download hostname could not be resolved.'
        elif isinstance(error.reason, (TimeoutError, socket.timeout)):
            message = 'Download connection timed out; the host or port may be unreachable from GitHub.'
        else:
            message = 'Download connection failed; the host or port may be unavailable or blocked from GitHub.'
        raise (DownloadError if isinstance(error.reason, (ssl.SSLError, socket.gaierror)) else RetryableDownloadError)(message) from None
    except (TimeoutError, socket.timeout):
        raise RetryableDownloadError('Download timed out while receiving video data.') from None
    except (http.client.IncompleteRead, ConnectionError):
        raise RetryableDownloadError('Video connection ended before the download completed.') from None


def command(args, timeout=60):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise ValueError('Media tool failed; verify input formats and available disk space')
    return result.stdout


def probe(path):
    try:
        result = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)], capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        raise DownloadError('Media validation timed out. The downloaded file may be damaged or unusually difficult to probe.') from None
    if result.returncode:
        raise DownloadError('Media validation failed: the downloaded file is incomplete, damaged, or not a supported media file. No raw tool output logged.')
    try:
        return json.loads(result.stdout)
    except (ValueError, TypeError):
        raise DownloadError('Media validation returned invalid metadata.') from None


def packet_video_duration(lines, start=0.0):
    import math
    end=None
    for line in lines:
        fields=dict(part.split('=',1) for part in line.strip().split('|') if '=' in part)
        try:
            pts=float(fields.get('pts_time','nan'))
            duration=float(fields.get('duration_time','0'))
        except ValueError:continue
        if not math.isfinite(pts):continue
        if not math.isfinite(duration) or duration<0:duration=0
        value=pts+duration
        end=value if end is None else max(end,value)
    if end is None or end<=start:raise DownloadError('Could not determine the actual video timeline; upload stopped.')
    return end-start


def actual_video_duration(path,info):
    import threading
    video=next((s for s in info['streams'] if s.get('codec_type')=='video'),None)
    if video is None:raise DownloadError('No video stream was found.')
    try:start=float(video.get('start_time',0))
    except (ValueError,TypeError):start=0.0
    print('Checking actual video timeline; source subtitle padding is excluded',flush=True)
    process=subprocess.Popen(['ffprobe','-v','error','-select_streams','v:0','-show_packets',
        '-show_entries','packet=pts_time,duration_time','-of','compact=p=0',str(path)],
        stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
    timer=threading.Timer(300,process.kill);timer.start()
    try:
        duration=packet_video_duration(process.stdout,start)
        if process.wait()!=0:raise DownloadError('Actual video timeline scan failed or timed out; upload stopped.')
        return duration
    finally:
        timer.cancel()
        if process.poll() is None:process.kill();process.wait()


def repository_file(root, value, suffix, limit):
    relative = Path(value)
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('Asset path must stay inside the repository')
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError('Repository asset is missing or outside checkout')
    if path.suffix.lower() != suffix or not 0 < path.stat().st_size <= limit:
        raise ValueError('Repository asset has invalid extension or size')
    return path


def output_filename(value):
    name = value.strip()
    if not name or name.startswith('.') or any(c in name for c in '/\\:"<>|?*') or any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise ValueError('Invalid output filename')
    if name.lower().endswith(('.mp4', '.mkv')):
        name = name[:-4]
    if not name.strip() or len(name.encode('utf-8')) > 180:
        raise ValueError('Output filename is empty or too long')
    return name + '.mp4'


def validate_sinhala_font(path, require_modern_iskoola=True):
    data = Path(path).read_bytes()
    count = struct.unpack_from('>H', data, 4)[0]
    tables = {}
    for index in range(count):
        tag, _, offset, size = struct.unpack_from('>4sIII', data, 12 + index * 16)
        if offset + size > len(data):
            raise ValueError('Truncated font')
        tables[tag] = offset
    for tag in (b'GSUB', b'GPOS'):
        base = tables[tag]
        scripts = base + struct.unpack_from('>H', data, base + 4)[0]
        total = struct.unpack_from('>H', data, scripts)[0]
        if not any(data[scripts + 2 + i * 6:scripts + 6 + i * 6] in (b'sinh', b'sin2') for i in range(total)):
            raise ValueError('Font lacks Sinhala shaping tables')
    base = tables[b'name']
    _, total, strings = struct.unpack_from('>HHH', data, base)
    versions = []
    for index in range(total):
        platform, _, _, name_id, length, offset = struct.unpack_from('>HHHHHH', data, base + 6 + index * 12)
        if name_id == 5:
            text = data[base + strings + offset:base + strings + offset + length].decode('utf-16-be' if platform in (0, 3) else 'latin1')
            match = re.search(r'Version\s+(\d+(?:\.\d+)?)', text, re.I)
            if match:
                versions.append(float(match[1]))
    if require_modern_iskoola and (not versions or max(versions) < 6):
        print('::error::Use a modern Iskoola Pota font (version 6 or later). The supplied Windows font is version 6.96; replace fonts/IskoolaPota.ttf. Older versions are not supported by this pipeline.')
        raise ValueError('Unsupported Iskoola Pota version')
    if require_modern_iskoola:
        print('Iskoola Pota font version: ' + str(max(versions)), flush=True)


def selected_font_file(root, value):
    if value.startswith('google:'):
        family = value[7:]
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 ._-]{0,120}', family):
            raise DownloadError('Invalid Google font family.')
        cache = Path(os.getenv('RUNNER_TEMP', tempfile.gettempdir())) / 'clixarena-google-fonts'
        cache.mkdir(parents=True, exist_ok=True)
        target = cache / (hashlib.sha256(family.encode()).hexdigest() + '.ttf')
        if not target.is_file():
            try:
                request = urllib.request.Request('https://fonts.googleapis.com/css?family=' + urllib.parse.quote_plus(family), headers={'User-Agent': 'Mozilla/4.0'})
                with urllib.request.urlopen(request, timeout=30) as response:
                    css = response.read(1024*1024).decode('utf-8')
                urls = re.findall(r'url\((https://fonts\.gstatic\.com/[^)\s]+)\)', css)
                if len(urls) != 1:
                    raise ValueError('Expected a full font')
                url = urllib.parse.urlsplit(urls[0])
                if url.hostname != 'fonts.gstatic.com' or url.port not in (None,443) or url.username or url.fragment:
                    raise ValueError('Invalid font host')
                with urllib.request.urlopen(urls[0], timeout=60) as response:
                    if urllib.parse.urlsplit(response.url).hostname != 'fonts.gstatic.com':
                        raise ValueError('Invalid font redirect')
                    data = response.read(30*1024**2+1)
                if len(data)>30*1024**2 or data[:4] not in (b'\x00\x01\x00\x00',b'OTTO'):
                    raise ValueError('Expected a TTF/OTF font')
                target.write_bytes(data)
            except Exception:
                raise DownloadError('Google font could not be downloaded as TTF/OTF. Choose another font or upload its TTF/OTF file.') from None
        return target
    system_fonts = {'system:noto-sinhala': '/usr/share/fonts/truetype/noto/NotoSansSinhala-Regular.ttf',
                    'system:dejavu-sans': '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
                    'system:dejavu-serif': '/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf'}
    if value in system_fonts:
        path = Path(system_fonts[value])
        if not path.is_file():
            raise DownloadError('Selected system font is unavailable; update the media-tools workflow step.')
        return path
    relative = Path(value)
    if relative.is_absolute() or '..' in relative.parts or not relative.parts or relative.parts[0] != 'fonts' or relative.suffix.lower() not in ('.ttf', '.otf'):
        raise DownloadError('Choose a TTF/OTF inside the repository fonts folder, or a listed system font.')
    return repository_file(root, value, relative.suffix.lower(), 30 * 1024**2)


def validate_selected_font(path, subtitle, language):
    family = command(['fc-scan', '--format', '%{family}', str(path)]).split(',')[0].strip()
    if not family or re.search(r'[,\\\x00-\x1f\x7f]', family):
        raise DownloadError('Selected font has an invalid family name.')
    charset = command(['fc-scan', '--format', '%{charset}', str(path)])
    ranges = []
    for item in charset.split():
        endpoints = item.split('-')
        if len(endpoints) in (1, 2) and all(re.fullmatch('[0-9a-fA-F]+', v) for v in endpoints):
            ranges.append((int(endpoints[0], 16), int(endpoints[-1], 16)))
    plain_text = re.sub(r'<[^>]*>|\{[^}]*\}', '', subtitle)
    required = {ord(c) for c in plain_text if ('\u0d80' <= c <= '\u0dff') or ('A' <= c <= 'Z') or ('a' <= c <= 'z')}
    if any(not any(start <= value <= end for start, end in ranges) for value in required):
        raise DownloadError('Selected font is missing characters used in this subtitle. Choose a font that supports the selected language.')
    if language == 'sin':
        try:
            validate_sinhala_font(path, require_modern_iskoola=family == 'Iskoola Pota')
        except (ValueError, KeyError, struct.error):
            raise DownloadError('Selected Sinhala font lacks supported shaping tables or has an unsupported Iskoola Pota version.') from None
    print('Selected subtitle font verified', flush=True)
    return family


def prepare_ass(family='Iskoola Pota'):
    command(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
             '-sub_charenc', 'UTF-8', '-i', 'subtitles.srt', 'subtitles.ass'])
    path = Path('subtitles.ass')
    lines = path.read_text(encoding='utf-8-sig').splitlines()
    fields = None
    styles = False
    for index, line in enumerate(lines):
        if line.startswith('['):
            styles = line == '[V4+ Styles]'
        if styles and line.startswith('Format:'):
            fields = [field.strip() for field in line.split(':', 1)[1].split(',')]
        if styles and fields and line.startswith('Style:'):
            values = line.split(':', 1)[1].strip().split(',')
            settings = {'Fontname': family, 'Fontsize': '22', 'Outline': '1',
                        'Shadow': '0', 'MarginV': '24', 'Spacing': '0', 'Encoding': '1',
                        'PrimaryColour': '&H00FFFFFF', 'SecondaryColour': '&H00FFFFFF',
                        'OutlineColour': '&H00000000', 'BackColour': '&H00000000'}
            for key, value in settings.items():
                values[fields.index(key)] = value
            lines[index] = 'Style: ' + ','.join(values)
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def api(endpoint, values):
    # Credentials go in a POST body, not a URL or command line.
    data = urllib.parse.urlencode(values).encode()
    req = urllib.request.Request('https://api.streamtape.com/' + endpoint, data=data)
    # No redirects: do not forward credentials to a different endpoint.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(req, timeout=60) as response:
        body = json.loads(response.read(1024 * 1024))
    if body.get('status') != 200 or not (isinstance(body.get('result'), dict) or body.get('result') is True):
        raise ValueError('Streamtape API rejected the request')
    return body['result']


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def upload(path, login, key):
    with timed_stage('Streamtape checksum preparation'):
        with path.open('rb') as file:
            digest = hashlib.file_digest(file, 'sha256').hexdigest()
    result = timed_call('Streamtape upload-server lookup',api,'file/ul', {'login': login, 'key': key, 'sha256': digest})
    url = result.get('url', '')
    validate_url(url)
    mask(url)
    # curl streams the multipart file. URL is supplied over stdin, never argv.
    escaped = url.replace('\\', '\\\\').replace('"', '\\"')
    config = 'url = "' + escaped + '"\n'
    response = run_upload(['curl', '--config', '-', '--silent', '--fail',
                               '--proto', '=https', '--connect-timeout', '30',
                               '--max-time', '3600', '--form', 'file1=@' + str(path)],
                              input=config, capture_output=True, text=True, timeout=3610,label='Streamtape',size=file_size(path))
    if response.returncode:
        raise ValueError('Upload failed or timed out; check account before rerunning')
    body = json.loads(response.stdout)
    if body.get('status') != 200:
        raise ValueError('Upload API reported failure')
    file_id = body.get('result', {}).get('id', '')
    if not isinstance(file_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', file_id):
        raise ValueError('Upload response has no valid file ID; check account before rerunning')
    info = timed_call('Streamtape uploaded-file verification',api,'file/info', {'login': login, 'key': key, 'file': file_id}).get(file_id, {})
    if info.get('status') != 200 or int(info.get('size', -1)) != path.stat().st_size:
        raise ValueError('Uploaded file could not be verified; check account before rerunning')
    return file_id


def verify_filename(file_id, output_name, login, key, attempts=6, pause=time.sleep):
    # Read-only polling tolerates delayed metadata; never retry upload or rename.
    for attempt in range(attempts):
        try:
            result = api('file/info', {'login': login, 'key': key, 'file': file_id})
            info = result.get(file_id, {}) if isinstance(result, dict) else {}
            if isinstance(info, dict) and info.get('status') == 200 and info.get('name') == output_name:
                return True
        except Exception:
            pass  # Response/error text may contain credentials. Never log it.
        if attempt + 1 < attempts:
            print('Waiting for Streamtape filename metadata to update', flush=True)
            pause(3)
    return False


def audio_encoding_args(streams, audio_map):
    audio = [s for s in streams if s.get('codec_type') == 'audio']
    if not audio:
        return []
    selected = audio[int(audio_map.split(':')[-1].rstrip('?'))]
    if selected.get('codec_name') == 'aac':
        print('AAC audio: copying without re-encoding', flush=True)
        return ['-c:a', 'copy']
    return ['-c:a', 'aac', '-b:a', '192k']


def soft_mp4_args(streams, audio_map, duration, crf, language='sin', options=None):
    video=next(s for s in streams if s.get('codec_type')=='video')
    args=['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-y',
          '-i','source.video','-sub_charenc','UTF-8','-i','sinhala.srt',
          '-map','0:v:0','-map',audio_map,'-map','1:s:0','-map_metadata','-1','-map_chapters','-1']
    if video.get('codec_name')=='h264':
        print('H.264 video: copying without re-encoding',flush=True)
        args+=['-c:v','copy']
    else:
        print('Source video is not H.264: converting video to H.264',flush=True)
        args+=['-c:v','libx264','-preset','veryfast','-crf','23','-pix_fmt','yuv420p']
    args+=audio_encoding_args(streams,audio_map)
    audio=[s for s in streams if s.get('codec_type')=='audio']
    if audio:
        selected=audio[int(audio_map.split(':')[-1].rstrip('?'))]
        code=selected.get('tags',{}).get('language','')
        if re.fullmatch('[a-z]{2,3}',code):args+=['-metadata:s:a:0','language='+code]
        args+=['-disposition:a:0','default']
    return args+['-c:s','mov_text','-metadata:s:s:0','language='+language,
                 '-metadata:s:s:0','title='+('Sinhala' if language=='sin' else 'English'),'-disposition:s:0','0',
                 '-t',str(duration),'-movflags','+faststart']


def transfer_mp4_args(streams, crf=23, preset='veryfast'):
    video = next(s for s in streams if s.get('codec_type') == 'video')
    args = ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y', '-i', 'transfer.video', '-map', '0:v:0', '-map', '0:a?', '-map_metadata', '-1', '-map_chapters', '-1']
    if video.get('codec_name') == 'h264':
        print('H.264 video: copying into MP4 without re-encoding', flush=True)
        args += ['-c:v', 'copy']
    else:
        print('Converting source video to H.264 for MP4 playback', flush=True)
        args += ['-c:v', 'libx264', '-preset', preset, '-crf', str(crf), '-pix_fmt', 'yuv420p']
    for index, audio in enumerate(s for s in streams if s.get('codec_type') == 'audio'):
        if audio.get('codec_name') == 'aac':
            args += [f'-c:a:{index}', 'copy']
        else:
            args += [f'-c:a:{index}', 'aac', f'-b:a:{index}', '384k' if int(audio.get('channels', 2)) > 2 else '192k']
        language = audio.get('tags', {}).get('language', '')
        if re.fullmatch('[a-z]{2,3}', language):
            args += [f'-metadata:s:a:{index}', 'language=' + language]
        args += [f'-disposition:a:{index}', 'default' if audio.get('disposition', {}).get('default') else '0']
    text_codecs = {'subrip', 'ass', 'ssa', 'mov_text', 'webvtt', 'text'}
    subtitles = [s for s in streams if s.get('codec_type') == 'subtitle']
    retained = [s for s in subtitles if s.get('codec_name') in text_codecs]
    for index, subtitle in enumerate(retained):
        args += ['-map', '0:' + str(subtitle['index']), f'-c:s:{index}', 'mov_text']
        language = subtitle.get('tags', {}).get('language', '')
        if re.fullmatch('[a-z]{2,3}', language):
            args += [f'-metadata:s:s:{index}', 'language=' + language]
    if len(retained) != len(subtitles):
        print('::warning::Image-based or unsupported source subtitles omitted because they cannot be converted to MP4 text subtitles.')
    return args + ['-movflags', '+faststart']


_AUDIO_STATE_CACHE = {}

def audio_job_request(job_id, payload=None, sha=None, state_dir='audio'):
    repo = os.getenv('GITHUB_REPOSITORY', '')
    token = os.getenv('MAPPING_GITHUB_TOKEN', '')
    ref = os.getenv('GITHUB_REF_NAME', '')
    if not re.fullmatch(r'[a-f0-9]{32}', job_id) or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo) or not token or not ref:
        raise ValueError('Audio selection portal configuration missing')
    if state_dir not in ('audio','torrent','download'):raise ValueError('Invalid selection state folder')
    endpoint = 'https://api.github.com/repos/' + repo + '/contents/.clixarena/' + state_dir + '/' + job_id + '.json'
    headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json', 'Content-Type': 'application/json'}
    cache_key = (endpoint, ref, hashlib.sha256(token.encode()).digest())
    cached = _AUDIO_STATE_CACHE.get(cache_key) if payload is None and state_dir == 'audio' else None
    if cached:
        headers['If-None-Match'] = cached[0]
    if payload is None:
        request = urllib.request.Request(endpoint + '?ref=' + urllib.parse.quote(ref, safe=''), headers=headers)
    else:
        body = {'message': 'Update audio selection state', 'branch': ref, 'content': base64.b64encode(json.dumps(payload).encode()).decode()}
        if sha:
            body['sha'] = sha
        request = urllib.request.Request(endpoint, data=json.dumps(body).encode(), headers=headers, method='PUT')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    for attempt in range(12):
        try:
            with opener.open(request, timeout=30) as response:
                document = json.loads(response.read(1024 * 1024))
                etag = getattr(response, 'headers', {}).get('ETag')
                if payload is None and state_dir == 'audio' and etag:
                    _AUDIO_STATE_CACHE[cache_key] = (etag, document)
                return document
        except urllib.error.HTTPError as error:
            if error.code == 304 and cached:
                error.close()
                return cached[1]
            if payload is None or error.code != 409 or attempt == 11:
                raise
            time.sleep(random.uniform(0.15, 1.0) * min(3, 1 + attempt // 4))
            current = audio_job_request(job_id,state_dir=state_dir)
            body['sha'] = current['sha']
            request = urllib.request.Request(endpoint, data=json.dumps(body).encode(), headers=headers, method='PUT')


def portal_audio_selection(streams, choice, pause=time.sleep, clock=time.monotonic, wait_seconds=600):
    tracks = [s for s in streams if s.get('codec_type') == 'audio']
    if choice != 'ask' or len(tracks) <= 1:
        return audio_selection(streams, 'auto' if choice == 'ask' else choice)
    job_id = os.getenv('PORTAL_JOB_ID', '')
    state = {'job': job_id, 'run_id': os.getenv('GITHUB_RUN_ID', ''), 'status': 'waiting_audio', 'tracks': []}
    for index, track in enumerate(tracks):
        language = track.get('tags', {}).get('language', 'und')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,20}', language):
            language = 'und'
        codec = track.get('codec_name', 'unknown')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,30}', codec):
            codec = 'unknown'
        state['tracks'].append({'index': index, 'language': language, 'channels': int(track.get('channels', 0)), 'codec': codec})
    print(f'Audio tracks found: {len(tracks)}', flush=True)
    for track in state['tracks']:
        print(f'Audio track {track["index"]}: language={track["language"]}, codec={track["codec"]}, channels={track["channels"]}', flush=True)
    try:
        current = audio_job_request(job_id)
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        current = None
    deadline = clock() + wait_seconds
    state['expires_at'] = int(time.time() + wait_seconds)
    print('Multiple audio tracks found. Waiting for audio track selection in the control page. Maximum wait: 10 minutes; encoding has not started.', flush=True)
    audio_job_request(job_id, state, current.get('sha') if current else None)
    last_report = clock()
    while True:
        remaining = deadline - clock()
        if remaining <= 0:
            break
        current = audio_job_request(job_id)
        if clock() >= deadline:
            break
        updated = json.loads(base64.b64decode(current['content']).decode())
        if updated.get('job') != job_id or updated.get('run_id') != state['run_id']:
            raise ValueError('Audio selection does not match this run')
        selected = updated.get('selected')
        if type(selected) is int and 0 <= selected < len(tracks) and updated.get('status') == 'selected':
            updated['status'] = 'encoding'
            audio_job_request(job_id, updated, current['sha'])
            print('Selected audio confirmed; resuming this run now. Preparing parallel encoding jobs.', flush=True)
            return audio_selection(streams, str(selected), list_tracks=False)
        if clock() - last_report >= 15:
            print(f'Waiting for audio selection in the control page; {max(0, int(deadline-clock()))} seconds remaining. Encoding has not started.', flush=True)
            last_report = clock()
        pause(min(2, max(0, deadline-clock())))
    state['status'] = 'expired'
    try:
        audio_job_request(job_id, state, current['sha'])
    except Exception:
        pass
    print('::error::Audio selection timed out after 10 minutes. No encode or upload was started.')
    raise ValueError('Audio selection timed out')


def external_subtitle_mapping(root=None):
    layout = json.loads(os.getenv('OVERLAY_LAYOUT', '') or '{}')
    asset = layout.get('externalSubtitle')
    if asset is None:
        return {}
    if os.getenv('SUBTITLE_METHOD', 'burn') != 'transfer' or not isinstance(asset, dict) or set(asset) != {'path', 'language', 'url'}:
        raise ValueError('Invalid external subtitle metadata')
    repo = os.getenv('GITHUB_REPOSITORY', '')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        raise ValueError('Invalid subtitle repository')
    url = asset['url']
    if not isinstance(url, str):
        raise ValueError('Invalid subtitle URL')
    match = re.fullmatch(r'https://raw\.githubusercontent\.com/' + re.escape(repo) + r'/([a-f0-9]{40})/(subtitles/jobs/[a-f0-9]{32}/sinhala\.srt)', url)
    if not match or match.group(2) != asset['path'] or asset['language'] not in ('sin', 'eng'):
        raise ValueError('Invalid subtitle URL or language')
    if root is not None:
        path = repository_file(root, asset['path'], '.srt', 1024**2)
        text = path.read_text(encoding='utf-8-sig')
        alphabet = r'[\u0d80-\u0dff]' if asset['language'] == 'sin' else r'[A-Za-z]'
        if not re.search(alphabet, text) or not re.search(r'^\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}', text, re.M) or '\x00' in text or '\ufffd' in text:
            raise ValueError('Invalid external SRT file')
    return {'subtitle_url': url, 'subtitle_language': asset['language'], 'subtitle_format': 'srt'}


def merge_mapping(rows, identity, file_id, provider='streamtape', quality_ids=None):
    return merge_provider_mapping(rows,identity,file_id,provider,quality_ids=quality_ids)


def mapping_password():
    password = os.getenv('MAPPING_PASSWORD', '')
    if not password:
        raise DownloadError('MAPPING_PASSWORD is missing. Set the same password in GitHub Actions secrets and Vercel environment variables.')
    mask(password)
    return password.encode('utf-8')


def encrypt_mappings(rows):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt, nonce = os.urandom(16), os.urandom(12)
    key = hashlib.pbkdf2_hmac('sha256', mapping_password(), salt, 600000, 32)
    encrypted = AESGCM(key).encrypt(nonce, json.dumps(rows, ensure_ascii=False).encode('utf-8'), b'CLIXARENA-mappings-v1')
    return {'version': 1, 'algorithm': 'AES-256-GCM', 'kdf': 'PBKDF2-SHA256', 'iterations': 600000,
            'salt': base64.b64encode(salt).decode(), 'nonce': base64.b64encode(nonce).decode(), 'ciphertext': base64.b64encode(encrypted).decode()}


def decrypt_mappings(document):
    if isinstance(document, list):
        return document  # Migrate the current plaintext file on its next update.
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if not isinstance(document, dict) or document.get('version') != 1 or document.get('algorithm') != 'AES-256-GCM' or document.get('kdf') != 'PBKDF2-SHA256' or document.get('iterations') != 600000:
        raise ValueError('Invalid encrypted mapping format')
    salt = base64.b64decode(document['salt'], validate=True)
    nonce = base64.b64decode(document['nonce'], validate=True)
    if len(salt) != 16 or len(nonce) != 12:
        raise ValueError('Invalid mapping encryption parameters')
    key = hashlib.pbkdf2_hmac('sha256', mapping_password(), salt, 600000, 32)
    plaintext = AESGCM(key).decrypt(nonce, base64.b64decode(document['ciphertext'], validate=True), b'CLIXARENA-mappings-v1')
    rows = json.loads(plaintext)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError('Invalid decrypted mapping')
    return rows


def save_mapping(file_id,provider='streamtape',quality_ids=None):
    identity = json.loads(os.getenv('OVERLAY_LAYOUT', '') or '{}').get('mapping')
    if identity is None:
        return
    kind = identity.get('type')
    key = ('mal_id' if 'mal_id' in identity else 'anilist_id') if kind == 'anime' else 'tmdb_id'
    if kind not in ('movie', 'tv', 'anime') or set(identity) not in ({'type', key, 'season', 'episode'}, {'type', key, 'season', 'episode', 'name'}):
        raise ValueError('Invalid mapping identity')
    if 'name' in identity and (not isinstance(identity['name'], str) or not identity['name'].strip() or len(identity['name'].encode('utf-8')) > 1500 or re.search(r'[\x00-\x1f\x7f]', identity['name'])):
        raise ValueError('Invalid mapping name')
    if any(type(identity[k]) is not int or not 0 <= identity[k] <= 2147483647 for k in (key, 'season', 'episode')) or identity[key] < 1:
        raise ValueError('Invalid mapping numbers')
    if kind == 'movie' and (identity['season'] or identity['episode']) or kind != 'movie' and identity['episode'] < 1:
        raise ValueError('Invalid mapping episode')
    identity = dict(identity, **external_subtitle_mapping())
    backup = Path(os.environ.get('GITHUB_WORKSPACE', '.')) / 'mapping-result.json'
    receipt_rows=decrypt_mappings(json.loads(backup.read_text(encoding='utf-8'))) if backup.is_file() else []
    backup.write_text(json.dumps(encrypt_mappings(merge_mapping(receipt_rows,identity,file_id,provider,quality_ids=quality_ids)), indent=2) + '\n', encoding='utf-8')
    repo = os.getenv('GITHUB_REPOSITORY', '')
    token = os.getenv('MAPPING_GITHUB_TOKEN', '')
    ref = os.getenv('GITHUB_REF_NAME', '')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo) or not token or not ref:
        raise ValueError('Mapping repository configuration missing')
    mask(token)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    endpoint = 'https://api.github.com/repos/' + repo + '/contents/mappings.json'
    headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json', 'Content-Type': 'application/json', 'X-GitHub-Api-Version': '2022-11-28'}
    for attempt in range(12):
        try:
            request = urllib.request.Request(endpoint + '?ref=' + urllib.parse.quote(ref, safe=''), headers=headers)
            try:
                with opener.open(request, timeout=60) as response:
                    current = json.loads(response.read(8 * 1024**2))
                document = json.loads(base64.b64decode(current['content']).decode('utf-8-sig'))
                rows = decrypt_mappings(document)
            except urllib.error.HTTPError as error:
                if error.code != 404:
                    raise
                current, rows = None, []
            merged = merge_mapping(rows, identity, file_id,provider,quality_ids=quality_ids)
            content = json.dumps(encrypt_mappings(merged), indent=2) + '\n'
            backup.write_text(content, encoding='utf-8')
            if rows == merged and current and not isinstance(document, list):
                return
            payload = {'message': 'Update CLIXARENA video mapping', 'branch': ref, 'content': base64.b64encode(content.encode()).decode()}
            if current:
                payload['sha'] = current['sha']
            request = urllib.request.Request(endpoint, data=json.dumps(payload).encode(), headers=headers, method='PUT')
            with opener.open(request, timeout=60) as response:
                response.read(1024 * 1024)
            print('Video mapping saved to mappings.json', flush=True)
            return
        except urllib.error.HTTPError as error:
            if error.code not in (409, 422) or attempt == 11:
                raise
            time.sleep(random.uniform(0.15, 1.0) * min(3, 1 + attempt // 4))
    raise ValueError('Mapping save could not be confirmed')


_UPLOAD_VERIFIER = ContextVar('upload_verifier', default=None)


def finish_provider_upload(provider,file_id,key,size,output_name):
    label={'abyss':'Abyss','streamhg':'StreamHG','telegram':'Telegram'}[provider]
    verify={'abyss':verify_abyss,'streamhg':verify_streamhg,'telegram':verify_telegram}[provider]
    verified,named=timed_call(label+' metadata verification',verify,file_id,key,size,output_name)
    if not verified:print('::warning::'+label+' upload accepted; metadata verification is pending. Check the host manager before uploading again.',flush=True)
    elif not named:print('::warning::'+label+' upload verified, but the stored filename differs. Check the host manager; do not upload again.',flush=True)
    print(label+' upload complete. Playback processing may still be pending.',flush=True)
    if os.getenv('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'],'a') as output:output.write('upload_verified='+str(verified).lower()+'\nupload_host='+provider+'\n')


def verify_accepted_upload(provider,file_id,key,size,output_name):
    submit=_UPLOAD_VERIFIER.get()
    if submit is None:
        return finish_provider_upload(provider,file_id,key,size,output_name)
    print({'abyss':'Abyss','streamhg':'StreamHG','telegram':'Telegram'}[provider]+' accepted; checking metadata alongside the next destination. File uploads remain sequential.',flush=True)
    submit(provider,finish_provider_upload,provider,file_id,key,size,output_name)


def complete_upload(path, output_name, values):
    providers=selected_hosts(json.loads(os.getenv('OVERLAY_LAYOUT','') or '{}'))
    if len(providers)==1:
        return complete_upload_one(path,output_name,values,providers[0])
    failures=[]
    pending=[]
    # Only read-only metadata checks overlap. Uploads and mapping mutations
    # stay in the user's selected order; all checks finish before job success.
    with ThreadPoolExecutor(max_workers=1) as executor:
        def submit(provider,fn,*args):pending.append((provider,executor.submit(fn,*args)))
        token=_UPLOAD_VERIFIER.set(submit)
        try:
            for index,provider in enumerate(providers,1):
                print('Upload destination '+str(index)+' of '+str(len(providers))+': '+provider,flush=True)
                try:
                    complete_upload_one(path,output_name,values,provider)
                except Exception as error:
                    failures.append(provider)
                    # Only our deliberately safe errors may reach the log.
                    if isinstance(error,DownloadError):print('::warning::'+str(error),flush=True)
                    print('::warning::'+provider+' upload or finalization could not be confirmed. Check that host before retrying; no automatic upload retry.',flush=True)
        finally:
            _UPLOAD_VERIFIER.reset(token)
        for provider,future in pending:
            try:future.result()
            except Exception:
                if provider not in failures:failures.append(provider)
                print('::warning::'+provider+' metadata check failed after upload acceptance. Check that host; do not upload again.',flush=True)
    if failures:
        raise DownloadError('Upload incomplete for: '+', '.join(failures)+'. Other accepted uploads and mapping receipts are preserved. Do not rerun the whole job blindly.')
    print('All selected upload destinations completed.',flush=True)


def complete_upload_one(path, output_name, values, provider):
    if provider=='abyss':
        print('Uploading video to Abyss',flush=True)
        try:file_id=upload_abyss(path,output_name,values['ABYSS_API_KEY'],mask)
        except Exception:raise DownloadError('Abyss upload failed or response was not verified. Check Abyss before uploading again; no automatic retry.') from None
        print('Abyss upload accepted',flush=True)
        mapping_saved=True
        try:timed_call('Abyss mapping save',save_mapping,file_id,'abyss')
        except Exception:
            mapping_saved=False
            print('::warning::Abyss upload accepted, but mapping save failed. Download the encrypted mapping-result.json artifact; do not upload again.',flush=True)
        if not mapping_saved:raise DownloadError('Abyss upload accepted, but mapping save failed. Recover the encrypted mapping receipt and check repository access; do not upload the video again.')
        verify_accepted_upload('abyss',file_id,values['ABYSS_API_KEY'],path.stat().st_size,output_name)
        return

    if provider=='telegram':
        print('Uploading video to Telegram (with auto multi-resolution transcoding)',flush=True)
        try:
            bot_token=values.get('TELEGRAM_BOT_TOKEN') or os.getenv('TELEGRAM_BOT_TOKEN','')
            chat_id=values.get('TELEGRAM_CHAT_ID') or os.getenv('TELEGRAM_CHAT_ID','')
            api_id=values.get('TELEGRAM_API_ID') or os.getenv('TELEGRAM_API_ID','')
            api_hash=values.get('TELEGRAM_API_HASH') or os.getenv('TELEGRAM_API_HASH','')
            layout_cfg = json.loads(os.getenv('OVERLAY_LAYOUT','') or '{}')
            telegram_mode = layout_cfg.get('telegramMode', 'single')
            file_id, quality_ids = upload_telegram_multi(path,output_name,bot_token,chat_id,api_id,api_hash,mask,mode=telegram_mode)
        except HostError as error:raise DownloadError(str(error)) from None
        except Exception as e:raise DownloadError('Telegram upload failed: ' + str(e)) from None
        print('Telegram upload accepted: ' + file_id,flush=True)
        mapping_saved=True
        try:timed_call('Telegram mapping save',save_mapping,file_id,'telegram',quality_ids)
        except Exception:
            mapping_saved=False
            print('::warning::Telegram upload accepted, but mapping save failed. Download the encrypted mapping-result.json artifact; do not upload again.',flush=True)
        if not mapping_saved:raise DownloadError('Telegram upload accepted, but mapping save failed. Recover the encrypted mapping receipt and check repository access; do not upload the video again.')
        verify_accepted_upload('telegram',file_id,bot_token,path.stat().st_size,output_name)
        return

    if provider=='streamhg':
        print('Uploading video to StreamHG',flush=True)
        try:file_id=upload_streamhg(path,output_name,values['STREAMHG_API_KEY'],mask,validate=validate_url)
        except HostError as error:raise DownloadError(str(error)) from None
        except Exception:raise DownloadError('StreamHG upload failed or response was not verified. Check StreamHG before uploading again; no automatic retry.') from None
        print('StreamHG upload accepted',flush=True)
        mapping_saved=True
        try:timed_call('StreamHG mapping save',save_mapping,file_id,'streamhg')
        except Exception:
            mapping_saved=False
            print('::warning::StreamHG upload accepted, but mapping save failed. Download the encrypted mapping-result.json artifact; do not upload again.',flush=True)
        if not mapping_saved:raise DownloadError('StreamHG upload accepted, but mapping save failed. Recover the encrypted mapping receipt and check repository access; do not upload the video again.')
        verify_accepted_upload('streamhg',file_id,values['STREAMHG_API_KEY'],path.stat().st_size,output_name)
        return

    print('Uploading video to Streamtape', flush=True)
    file_id = upload(path, values['STREAMTAPE_LOGIN'], values['STREAMTAPE_KEY'])
    try:
        timed_call('Streamtape mapping save',save_mapping,file_id)
    except Exception:
        raise DownloadError('Streamtape upload accepted, but mapping save failed. Recover the encrypted mapping receipt; do not upload again.') from None
    # Keep user names out of shell, FFmpeg paths and curl form syntax.
    renamed = api('file/rename', {'login': values['STREAMTAPE_LOGIN'],
                  'key': values['STREAMTAPE_KEY'], 'file': file_id, 'name': output_name})
    if renamed is not True:
        raise ValueError('Streamtape filename update failed')
    filename_verified = timed_call('Streamtape filename verification',verify_filename,file_id,output_name,values['STREAMTAPE_LOGIN'],values['STREAMTAPE_KEY'])
    if not filename_verified:
        print('::warning::Upload verified and rename accepted, but the final filename could not be confirmed. Check the filename in Streamtape file manager; do not rerun just for this warning.')
    # Do not construct, print, summarize or export a Streamtape video URL/ID.
    print('Upload verified. Find the video in your Streamtape file manager.', flush=True)
    if os.getenv('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as file:
            file.write('upload_verified=true\nrename_accepted=true\nfilename_verified=' + str(filename_verified).lower() + '\n')
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as file:
            file.write('### Upload verified\nRename request accepted. Final filename ' + ('verified' if filename_verified else 'not confirmed; check it in Streamtape file manager. Do not re-upload solely for this warning') + '.\nFind the video in your Streamtape file manager. Video URLs and file IDs are not included in logs, summaries or outputs. Playback processing may still be pending.\n')

def selected_runner_mode(layout,environment=None):
    # GitHub injects the workflow input's default "single" even when the UI
    # dispatch supplied "parallel" inside overlay_layout. UI selection wins.
    value=layout.get('runnerMode')
    if value is None or value=='':
        value=(os.getenv('RUNNER_MODE','') if environment is None else environment) or 'parallel'
    if not isinstance(value,str) or value.strip().lower() not in ('single','parallel'):
        raise ValueError('Choose single or parallel runner mode')
    return value.strip().lower()


def main():
    stage = 'preflight'
    print('CLIXARENA pipeline version 2026.10.20-parallel-preparation', flush=True)
    try:
        values = {name: os.getenv(name, '') for name in
                  ('VIDEO_URL', 'SUBTITLE_PATH', 'FONT_PATH', 'STREAMTAPE_LOGIN', 'STREAMTAPE_KEY','STREAMHG_API_KEY','ABYSS_API_KEY','TELEGRAM_BOT_TOKEN','TELEGRAM_CHAT_ID','TELEGRAM_API_ID','TELEGRAM_API_HASH')}
        if not values['TELEGRAM_BOT_TOKEN'] and os.getenv('TELEGRAM_BOT_TOKENS'):
            values['TELEGRAM_BOT_TOKEN'] = os.getenv('TELEGRAM_BOT_TOKENS').split(',')[0].strip()
        sample_only = os.getenv('SAMPLE_ONLY', 'false').lower() == 'true'
        method = os.getenv('SUBTITLE_METHOD', 'burn')
        if method not in ('burn', 'soft', 'transfer'):
            raise ValueError('Invalid subtitle method')
        if method != 'burn':
            os.environ['TITLE_ANIMATION'] = 'false'
        options = settings()
        layout_cfg = json.loads(os.getenv('OVERLAY_LAYOUT', '') or '{}')
        runner_mode = selected_runner_mode(layout_cfg)
        print('Runner mode: '+('parallel (up to four encoding workers)' if runner_mode=='parallel' else 'single'),flush=True)
        if runner_mode == 'parallel':
            os.environ['DISTRIBUTED_PREPARE'] = 'true'
        else:
            os.environ['DISTRIBUTED_PREPARE'] = 'false'
        if method=='soft':
            if os.getenv('DISTRIBUTED_PREPARE') == 'true':
                options.update(watermark_enabled=False,audio='ask')
                if os.getenv('CRF','auto') == 'auto': options['crf']=18
            else:
                options.update(height=None,watermark_enabled=False,audio='ask',crf=23,preset='veryfast')
        if json.loads(os.getenv('OVERLAY_LAYOUT', '') or '{}').get('mapping') and not sample_only:
            mapping_password()
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        burn_subtitles = method != 'burn' or options['subtitle_enabled']
        tmdb_token = os.getenv('TMDB_READ_TOKEN', '')
        mask(tmdb_token)
        for value in values.values():
            mask(value)
        required = ('VIDEO_URL', 'SUBTITLE_PATH', 'FONT_PATH') if sample_only else tuple(values)
        if method == 'burn' and not burn_subtitles:
            required = tuple(name for name in required if name not in ('SUBTITLE_PATH', 'FONT_PATH'))
        if method == 'soft':
            required = tuple(name for name in required if name != 'FONT_PATH')
        if method == 'transfer':
            required = ('VIDEO_URL', 'STREAMTAPE_LOGIN', 'STREAMTAPE_KEY')
        providers=selected_hosts(json.loads(os.getenv('OVERLAY_LAYOUT','') or '{}'))
        required=tuple(n for n in required if n not in ('STREAMTAPE_LOGIN','STREAMTAPE_KEY','STREAMHG_API_KEY','ABYSS_API_KEY','TELEGRAM_BOT_TOKEN','TELEGRAM_CHAT_ID','TELEGRAM_API_ID','TELEGRAM_API_HASH'))
        if not sample_only:
            for provider in providers:
                required+=('ABYSS_API_KEY',) if provider=='abyss' else ('STREAMHG_API_KEY',) if provider=='streamhg' else ('TELEGRAM_BOT_TOKEN','TELEGRAM_CHAT_ID') if provider=='telegram' else ('STREAMTAPE_LOGIN','STREAMTAPE_KEY')
        missing = [name for name in required if not values[name].strip()]
        if missing:
            print('::error::Missing required configuration: ' + ', '.join(missing) +
                  '. Set video/asset inputs and repository Actions secrets with these exact names.')
            return 1
        for tool in ('ffmpeg', 'ffprobe', 'fc-scan', 'curl'):
            if not shutil.which(tool):
                raise ValueError('Required media tool is missing')
        stage = 'output filename validation'
        output_name = output_filename(os.getenv('OUTPUT_NAME', 'CLIXARENA.mp4'))
        overlay_name = os.getenv('OVERLAY_NAME', '').strip() or output_name[:-4]
        if len(overlay_name.encode('utf-8')) > 500 or re.search(r'[\x00-\x1f\x7f]', overlay_name):
            raise ValueError('Invalid display title')
        if method == 'transfer':
            stage = 'optional external subtitle validation'
            external_subtitle_mapping(Path.cwd().resolve())
            stage = 'original video download'
            with tempfile.TemporaryDirectory(prefix='clixarena-transfer-') as directory:
                original = Path.cwd()
                os.chdir(directory)
                try:
                    print('Downloading video for MP4 upload; no added subtitles or overlays', flush=True)
                    path = Path('transfer.video')
                    download_source(values['VIDEO_URL'], path, 12 * 1024**3, original.resolve())
                    stage = 'original video validation'
                    info = probe(str(path))
                    if not any(s.get('codec_type') == 'video' for s in info.get('streams', [])):
                        raise ValueError('Source is not a video')
                    try:
                        duration = actual_video_duration('transfer.video', info)
                        if not 0 < duration < float('inf'):
                            raise ValueError()
                    except (KeyError, TypeError, ValueError):
                        raise DownloadError('Source video has no valid duration metadata.') from None
                    if shutil.disk_usage('.').free < path.stat().st_size * 2 + 2 * 1024**3:
                        raise DownloadError('Insufficient runner disk space to prepare MP4.')
                    if os.getenv('DISTRIBUTED_PREPARE')=='true' and next(t for t in info['streams'] if t.get('codec_type')=='video').get('codec_name')!='h264':
                        from clixarena_distributed import prepare
                        prepare(original,info['streams'],None,duration,options,method='transfer',source=str(path))
                        return 0
                    stage = 'MP4 preparation without added subtitles'
                    encode_progress(transfer_mp4_args(info['streams'], options['crf'], options['preset']), duration)
                    stage = 'output MP4 validation'
                    output = probe('clixarena.mp4')
                    if 'mp4' not in output.get('format', {}).get('format_name', '').split(',') or not any(s.get('codec_name') == 'h264' and s.get('codec_type') == 'video' for s in output['streams']) or any(s.get('codec_type') == 'audio' and s.get('codec_name') != 'aac' for s in output['streams']):
                        raise DownloadError('Prepared MP4 has unexpected container or codecs; upload stopped.')
                    if abs(duration - float(output['format']['duration'])) > 2:
                        raise DownloadError('Prepared MP4 duration differs from the source; upload stopped.')
                    stage = 'Streamtape MP4 upload and filename update'
                    complete_upload(Path('clixarena.mp4'), output_name, values)
                finally:
                    os.chdir(original)
            return 0
        stage = 'repository subtitle and font availability'
        root = Path.cwd().resolve()
        srt_path = subtitle_asset(root, values['SUBTITLE_PATH']) if burn_subtitles else None
        font_path = selected_font_file(root, values['FONT_PATH']) if method == 'burn' and burn_subtitles else None
        algerian_path = None
        if options['parts']['name']:
            algerian_path = repository_file(root, 'fonts/Algerian.ttf', '.ttf', 30 * 1024**2)
        with tempfile.TemporaryDirectory(prefix='clixarena-') as directory:
            original = Path.cwd()
            os.chdir(directory)
            try:
                stage = 'subtitle and font validation'
                language = options['subtitle_language']
                language_name = 'Sinhala' if language == 'sin' else 'English'
                if burn_subtitles:
                    print('Checking subtitle and font', flush=True)
                    shutil.copyfile(srt_path, 'sinhala.srt')
                    # Every uploaded name is normalized in the runner, without changing repository files.
                    shutil.copyfile('sinhala.srt', 'subtitles.srt')
                    print(language_name + ' subtitle loaded', flush=True)
                    subtitle = Path('sinhala.srt').read_text(encoding='utf-8-sig')
                    if not re.search(r'\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}', subtitle) or '\x00' in subtitle or '\ufffd' in subtitle or (language == 'sin' and not re.search('[\u0d80-\u0dff]', subtitle)) or (language == 'eng' and not re.search('[A-Za-z]', subtitle)):
                        raise DownloadError('SRT must contain valid timestamps and text in the selected subtitle language.')
                else:
                    print('Subtitle burn disabled; no SRT or subtitle font required', flush=True)
                if method == 'burn':
                    Path('fonts').mkdir()
                    if burn_subtitles:
                        local_font = Path('fonts/selected' + font_path.suffix.lower())
                        shutil.copyfile(font_path, local_font)
                        family = validate_selected_font(local_font, subtitle, language)
                    if options['parts']['message']:
                        if burn_subtitles and language == 'sin':
                            options['layout']['messageFont'] = family
                        else:
                            message_font = selected_font_file(original, 'system:noto-sinhala')
                            shutil.copyfile(message_font, 'fonts/message.ttf')
                            options['layout']['messageFont'] = validate_selected_font('fonts/message.ttf', options['layout']['message'], 'sin')
                    if algerian_path:
                        shutil.copyfile(algerian_path, 'fonts/algerian.ttf')
                        if 'Algerian' not in command(['fc-scan', '--format', '%{family}', 'fonts/algerian.ttf']).split(','):
                            print('::error::fonts/Algerian.ttf must be the Algerian font. No font substitution is used.')
                            raise ValueError('Invalid Algerian font')
                    if burn_subtitles:
                        prepare_ass(family)
                        shaping_test = subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'info',
                            '-f', 'lavfi', '-i', 'color=s=320x180:d=0.1', '-vf',
                            'ass=subtitles.ass:fontsdir=fonts:shaping=complex', '-frames:v', '1', '-f', 'null', '-'],
                            capture_output=True, text=True, timeout=60)
                        if shaping_test.returncode or not re.search(r'HarfBuzz.*\(COMPLEX\)', shaping_test.stderr):
                            print('::error::FFmpeg/libass complex HarfBuzz shaping is unavailable. Use a build with libass and HarfBuzz.')
                            raise ValueError('Complex shaping unavailable')
                        print('Subtitle rendering: HarfBuzz complex shaping enabled', flush=True)
                logo_size = None
                if options['parts']['logo']:
                    stage = 'English TMDB original logo'
                    fetch_logo(options['tmdb'], tmdb_token, download)
                    logo_info = probe('title-logo.png')['streams'][0]
                    if logo_info.get('pix_fmt') not in ('rgba', 'bgra', 'argb', 'abgr', 'yuva420p', 'pal8'):
                        print('::error::Selected English logo lacks an alpha channel. Choose different artwork before encoding.')
                        raise ValueError('Nontransparent artwork')
                    logo_size = (int(logo_info['width']), int(logo_info['height']))
                if options['parts']['name'] or options['parts']['message']:
                    write_intro(overlay_name, options['start'], options['duration'], options['parts'], options['layout'])
                stage = 'video download and validation'
                print('Downloading video', flush=True)
                download_source(values['VIDEO_URL'], Path('source.video'), 12 * 1024**3, root)
                source = probe('source.video')
                if not any(s['codec_type'] == 'video' for s in source['streams']):
                    raise DownloadError('Downloaded file contains no video stream.')
                if shutil.disk_usage('.').free < Path('source.video').stat().st_size * 2 + 2 * 1024**3:
                    raise DownloadError('Insufficient runner disk space for encoding: twice the source size plus a 2 GB reserve is required.')
                try:
                    source_duration = actual_video_duration('source.video', source)
                    print(f'Actual video duration: {source_duration:.3f}s (container metadata {float(source["format"]["duration"]):.3f}s)',flush=True)
                    if not 0 < source_duration < float('inf'):
                        raise ValueError()
                except (KeyError, TypeError, ValueError):
                    raise DownloadError('Downloaded video has no valid duration metadata; encoding cannot safely start.') from None
                expected_duration = min(180, source_duration) if sample_only else source_duration
                stage = 'audio and quality selection'
                audio_map = portal_audio_selection(source['streams'], options['audio'])
                video_stream = next(s for s in source['streams'] if s['codec_type'] == 'video')
                width, height = video_dimensions(video_stream, options['height'])
                options['content_size']=fitted_dimensions(video_stream,width,height)
                if method=='soft':
                    print('On/off MP4: original resolution, selected audio only, no watermark',flush=True)
                else:
                    print(f'Output {width} x {height}, H.264 CRF {options["crf"]}, preset {options["preset"]}', flush=True)
                if any(options['parts'].values()) and options['start'] >= expected_duration:
                    print('::warning::Title timestamp is outside this encode. It will not appear; choose a timestamp within the sample/full video duration.')
                logo_input_duration = 0
                if logo_size and options['start'] < expected_duration:
                    stage = 'logo display preparation'
                    display_width, display_height = logo_dimensions(width, height, logo_size, options['layout']['logoScale'])
                    command(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
                             '-i', 'title-logo.png', '-vf',
                             f'scale={display_width}:{display_height}:flags=lanczos,format=rgba',
                             '-frames:v', '1', 'title-logo-display.png'], timeout=120)
                    logo_size = (display_width, display_height)
                    logo_input_duration = min(options['duration'], expected_duration - options['start'])
                    print(f'Logo prepared once at {display_width} x {display_height}; animation input ends after its display window', flush=True)
                else:
                    logo_size = None
                if os.getenv('DISTRIBUTED_PREPARE')=='true':
                    from clixarena_distributed import prepare
                    prepare(original,source['streams'],audio_map,expected_duration,options,language,method,logo_size=logo_size)
                    return 0
                if os.getenv('GITHUB_OUTPUT'):
                    with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as out_f:
                        out_f.write('distributed=false\nmatrix=[]\n')
                if method == 'soft':
                    stage = 'MP4 with selected switchable subtitles'
                    args = soft_mp4_args(source['streams'], audio_map, expected_duration, options['crf'], language,options)
                    print('Preparing MP4 with one ' + language_name + ' subtitle track; removing all source subtitle tracks', flush=True)
                    encode_progress(args, expected_duration)
                else:
                    stage = 'encoding'
                    print('Encoding H.264/AAC'+(' with watermark' if options['watermark_enabled'] else '; watermark disabled') + (' and ' + language_name + ' subtitles' if burn_subtitles else '; subtitles disabled'), flush=True)
                    filters = filter_graph(width, height, options, logo_size)
                    inputs = ['-threads', '0', '-i', 'source.video']
                    if logo_size:
                        inputs += ['-itsoffset', str(options['start']), '-loop', '1', '-framerate', '30',
                                   '-t', str(logo_input_duration), '-i', 'title-logo-display.png']
                    encode_progress(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y'] + inputs +
                             ['-filter_complex_threads', '4', '-map', '[video]', '-map', audio_map, '-filter_complex', filters,
                             '-c:v', 'libx264', '-preset', options['preset'], '-crf', str(options['crf']),
                             '-pix_fmt', 'yuv420p'] + audio_encoding_args(source['streams'], audio_map) + [
                             '-t', str(expected_duration), '-movflags', '+faststart'], expected_duration)
                stage = 'output MP4 validation'
                output = probe('clixarena.mp4')
                streams = output['streams']
                if method=='burn':
                    encoded_video=next((s for s in streams if s.get('codec_type')=='video'),{})
                    if (encoded_video.get('width'),encoded_video.get('height'))!=(width,height):
                        raise DownloadError('Output frame dimensions differ from the selected resolution. Upload was not started.')
                if not any(s.get('codec_name') == 'h264' for s in streams) or any(s.get('codec_type') == 'audio' and s.get('codec_name') != 'aac' for s in streams):
                    raise DownloadError('Output validation failed: expected H.264 video and AAC audio. Upload was not started.')
                if abs(expected_duration - float(output['format']['duration'])) > 2:
                    raise DownloadError(f'Output validation failed: source duration {expected_duration:.3f}s; MP4 duration {float(output["format"]["duration"]):.3f}s; difference exceeds 2 seconds. Upload was not started.')
                if method == 'soft':
                    selected_audio_count=1 if any(s.get('codec_type')=='audio' for s in source['streams']) else 0
                    if sum(s.get('codec_type')=='audio' for s in streams)!=selected_audio_count:
                        raise DownloadError('Output must contain only the selected audio track. Upload was not started.')
                    subtitles = [s for s in streams if s.get('codec_type') == 'subtitle']
                    if len(subtitles) != 1 or subtitles[0].get('codec_name') != 'mov_text' or subtitles[0].get('tags', {}).get('language') != language:
                        raise ValueError('Output must contain only the selected subtitle track')
                    preview = original / 'preview'
                    preview.mkdir(exist_ok=True)
                    shutil.copyfile('sinhala.srt', preview / 'subtitle.srt')
                if sample_only:
                    stage = 'sample export'
                    preview = original / 'preview'
                    preview.mkdir(exist_ok=True)
                    shutil.copyfile('clixarena.mp4', preview / 'sample.mp4')
                    print('Sample ready: download CLIXARENA-sample from this run artifacts. No Streamtape upload performed.')
                    if os.getenv('GITHUB_STEP_SUMMARY'):
                        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as file:
                            file.write('### 3-minute sample ready\nDownload **CLIXARENA-sample** from this run artifacts. Inspect subtitles from 01:18 and watermark. No Streamtape upload performed.\n')
                    return 0
                stage = 'selected host upload and metadata verification'
                complete_upload(Path('clixarena.mp4'), output_name, values)
            finally:
                os.chdir(original)
    except (DownloadError,TorrentError) as error:
        print('::error::' + str(error))
        return 1
    except Exception:
        # Exception strings can contain signed URLs, credentials and response bodies.
        print('::error::Failed during ' + stage + '. Check inputs, secrets, formats, service availability and disk space. Raw errors suppressed to protect credentials.')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
