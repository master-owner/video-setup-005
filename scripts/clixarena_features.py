"""Validated settings, English TMDB artwork, timed overlays and safe progress."""
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time
import urllib.parse
import urllib.request


def number(value, minimum, maximum, label):
    result = float(value)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise ValueError('Invalid ' + label)
    return result


def timestamp(value):
    if re.fullmatch(r'\d{1,3}:\d{2}:\d{2}(?:\.\d+)?', value):
        h, m, s = map(float, value.split(':'))
        if m >= 60 or s >= 60:
            raise ValueError('Timestamp minutes/seconds must be below 60')
        return number(h * 3600 + m * 60 + s, 0, 86400, 'timestamp')
    return number(value, 0, 86400, 'timestamp')


def settings():
    quality = os.getenv('QUALITY', '720p - Balanced')
    choices = {f'{label} - {tier}': (height, crf) for label,height in [('Original',None)]+[(str(h)+'p',h) for h in (360,480,720,1080,1440,2160)] for tier,crf in [('High',18),('Balanced',23)]}
    if quality not in choices:
        raise ValueError('Unsupported quality choice')
    height, crf = choices[quality]
    selected_crf = os.getenv('CRF', 'auto') or 'auto'
    if selected_crf != 'auto':
        if not re.fullmatch(r'0|[1-9]\d?', selected_crf) or int(selected_crf) > 51:
            raise ValueError('CRF must be auto or an integer from 0 to 51')
        crf = int(selected_crf)
    start = timestamp(os.getenv('OVERLAY_START', '5'))
    duration = number(os.getenv('OVERLAY_DURATION', '8'), 4, 60, 'overlay duration')
    active = os.getenv('TITLE_ANIMATION', str(bool(os.getenv('TMDB_URL')))).lower() == 'true'
    parts = {}
    for key in ('logo', 'name', 'message'):
        value = os.getenv('SHOW_' + key.upper(), 'true').lower()
        if value not in ('true', 'false'):
            raise ValueError('Invalid overlay selection')
        parts[key] = active and value == 'true'
    if parts['logo'] and not os.getenv('TMDB_URL', '').strip():
        raise ValueError('TMDB URL required for enabled logo')
    layout = json.loads(os.getenv('OVERLAY_LAYOUT', '') or '{}')
    watermark_enabled=layout.get('watermarkEnabled',True)
    if type(watermark_enabled) is not bool:
        raise ValueError('Invalid watermark enable option')
    subtitle_enabled = layout.get('subtitleEnabled', True)
    if type(subtitle_enabled) is not bool:
        raise ValueError('Invalid subtitle enable option')
    subtitle_language = layout.get('subtitleLanguage', 'sin')
    if subtitle_language not in ('sin', 'eng'):
        raise ValueError('Subtitle language must be Sinhala or English')
    logo_scale = number(layout.get('logoScale', 100), 25, 200, 'logo size')
    preset = layout.get('preset', 'veryfast')
    if preset not in ('fast', 'veryfast', 'superfast', 'ultrafast'):
        raise ValueError('Invalid encoder preset')
    positions = {}
    for key, y in [('logo', 410/1080*100), ('name', 510/1080*100), ('message', 600/1080*100)]:
        pos = layout.get('positions', {}).get(key, {'x': 50, 'y': y})
        positions[key] = {axis: number(pos[axis], 5, 95, 'overlay position') for axis in ('x', 'y')}
    message = layout.get('message', 'සිංහල උපසිරැසි සමග චිත්‍රපට/රූපවාහිනී කතාමාලා\nonline නැරඹීමට පිවිසෙන්න')
    if not isinstance(message, str) or len(message.encode('utf-8')) > 1500 or re.search(r'[\x00-\x09\x0b-\x1f\x7f]', message) or parts['message'] and not message.strip():
        raise ValueError('Invalid overlay message')
    styles = {}
    for key, default in [('name', 54), ('message', 36)]:
        style = layout.get('styles', {}).get(key, {})
        color = style.get('color', '#FFFFFF')
        if not isinstance(color, str) or not re.fullmatch(r'#[0-9A-Fa-f]{6}', color):
            raise ValueError('Invalid overlay text color')
        styles[key] = {'size': number(style.get('size', default), 8, 160, 'overlay font size'), 'color': color.upper()}
    return {'layout': {'positions': positions, 'message': message, 'styles': styles, 'logoScale': logo_scale}, 'parts': parts, 'preset': preset, 'height': height, 'crf': crf, 'start': start, 'duration': duration,
            'watermark_enabled':watermark_enabled, 'subtitle_enabled': subtitle_enabled, 'subtitle_language': subtitle_language, 'audio': os.getenv('AUDIO_TRACK', 'auto').strip(),
            'tmdb': os.getenv('TMDB_URL', '').strip()}


def subtitle_asset(root, value):
    if value != 'auto':
        from clixarena_video import repository_file
        return repository_file(root, value, '.srt', 10 * 1024**2)
    folder = (root / 'subtitles').resolve()
    if not folder.is_relative_to(root.resolve()):
        raise ValueError('Subtitle folder escapes repository')
    files = sorted(p for p in folder.glob('*') if p.is_file() and p.suffix.lower() == '.srt')
    if len(files) != 1:
        print('::error::For subtitle_path=auto, keep exactly one SRT in subtitles/. Upload any filename using Add file > Upload files, or specify its exact repository path.')
        raise ValueError('Ambiguous or missing subtitle')
    from clixarena_video import repository_file
    return repository_file(root, str(files[0].relative_to(root)), '.srt', 10 * 1024**2)


def audio_selection(streams, choice, list_tracks=True):
    tracks = [s for s in streams if s.get('codec_type') == 'audio']
    def details(track):
        language = track.get('tags', {}).get('language', 'und')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,20}', language): language = 'und'
        codec = track.get('codec_name', 'unknown')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,30}', codec): codec = 'unknown'
        return f'language={language}, codec={codec}, channels={int(track.get("channels", 0))}'
    if list_tracks:
        print(f'Audio tracks found: {len(tracks)}', flush=True)
        for i,track in enumerate(tracks): print(f'Audio track {i}: {details(track)}', flush=True)
    def selected(index):
        print(f'Selected audio: track {index}; {details(tracks[index])}', flush=True)
        return f'0:a:{index}'
    if choice == 'auto':
        if not tracks:
            print('No audio track found; continuing with video only', flush=True)
            return '0:a:0?'
        return selected(next((i for i,t in enumerate(tracks) if t.get('disposition',{}).get('default')),0))
    if re.fullmatch(r'\d+',choice):
        index=int(choice)
        if index<len(tracks):return selected(index)
    elif re.fullmatch(r'[a-z]{2,3}',choice):
        index=next((i for i,t in enumerate(tracks) if t.get('tags',{}).get('language')==choice),None)
        if index is not None:return selected(index)
    print('::error::Requested audio track is unavailable. Use one of the zero-based track numbers listed above, its language code (e.g. eng), or auto.',flush=True)
    raise ValueError('Audio selection unavailable')


def select_logo(logos):
    candidates = [item for item in logos if re.fullmatch(r'/[A-Za-z0-9]+\.png', item.get('file_path', ''))
                  and isinstance(item.get('width'), int) and isinstance(item.get('height'), int)
                  and item['width'] > 0 and item['height'] > 0]
    if not candidates:
        print('::error::TMDB has no usable PNG title logo for this title.')
        raise ValueError('English logo unavailable')
    return max(candidates, key=lambda i: (2 if i.get('iso_639_1') == 'en' else 1 if i.get('iso_639_1') is None else 0, i['width'] * i['height'], i.get('vote_average', 0)))


def fetch_logo(url, token, download):
    parsed = urllib.parse.urlsplit(url)
    match = re.fullmatch(r'/(movie|tv)/(\d+)(?:-[^/]*)?/?', parsed.path)
    if parsed.scheme != 'https' or parsed.hostname not in ('www.themoviedb.org', 'themoviedb.org') or parsed.username or parsed.password or parsed.port or not match:
        raise ValueError('Use a TMDB movie or TV series URL')
    if not token:
        print('::error::Set the TMDB_READ_TOKEN repository secret to your TMDB API Read Access Token.')
        raise ValueError('TMDB token missing')
    from clixarena_video import NoRedirect
    kind, identifier = match.groups()
    endpoint = f'https://api.themoviedb.org/3/{kind}/{identifier}/images'
    req = urllib.request.Request(endpoint, headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/json'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(req, timeout=60) as response:
        body = json.loads(response.read(4 * 1024**2))
    chosen = select_logo(body.get('logos', []))
    download('https://image.tmdb.org/t/p/original' + chosen['file_path'], Path('title-logo.png'), 40 * 1024**2)
    print(f'Title logo ({chosen.get("iso_639_1") or "unlabelled"}): original PNG, {chosen["width"]} x {chosen["height"]} (highest pixel count available)')


def ass_time(seconds):
    cs = round(seconds * 100)
    return f'{cs // 360000}:{cs // 6000 % 60:02}:{cs // 100 % 60:02}.{cs % 100:02}'


def ass_text(text):
    # User filenames cannot inject ASS override commands, line breaks or formatting.
    text = text.replace('\\', '＼').replace('{', '｛').replace('}', '｝')
    return re.sub(r' +', r'\\h\\h', text)


def write_intro(name, start, duration, parts=None, layout=None):
    parts = parts or {'name': True, 'message': True}
    positions = (layout or {}).get('positions', {})
    nx, ny = [positions.get('name', {'x':50, 'y':510/1080*100})[axis] * scale for axis, scale in [('x',19.2), ('y',10.8)]]
    mx, my = [positions.get('message', {'x':50, 'y':600/1080*100})[axis] * scale for axis, scale in [('x',19.2), ('y',10.8)]]
    styles = '''[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
ScaledBorderAndShadow: yes
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Name,Algerian,54,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,-1,0,0,100,100,0,0,1,0.5,0,5,90,90,0,1
Style: Message,Iskoola Pota,36,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1,0,5,90,90,0,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''
    message_font = (layout or {}).get('messageFont', 'Iskoola Pota')
    styles = styles.replace('Style: Message,Iskoola Pota,', 'Style: Message,' + message_font + ',')
    text_styles = (layout or {}).get('styles', {})
    for key, label, default in [('name', 'Name', 54), ('message', 'Message', 36)]:
        style = text_styles.get(key, {'size': default, 'color': '#FFFFFF'})
        color = style['color'].lstrip('#')
        ass_color = '&H00' + color[4:6] + color[2:4] + color[:2]
        rows = styles.splitlines()
        for index, row in enumerate(rows):
            if row.startswith('Style: ' + label + ','):
                fields = row.split(',')
                fields[2] = str(style['size'])
                fields[3] = fields[4] = ass_color
                rows[index] = ','.join(fields)
        styles = '\n'.join(rows) + '\n'
    # Scale entrance delays for short custom durations; the center composition matches preview.
    delay = min(1.1, duration / 6)
    message_delay = min(1.9, duration / 3)
    available_width = 1740
    # Conservative fit for long names, including Algerian capitals.
    size = min(text_styles.get('name', {}).get('size', 54), max(8, available_width / max(len(name) * 0.8, 1)))
    events = f'Dialogue: 0,{ass_time(start + delay)},{ass_time(start + duration)},Name,,0,0,0,,{{\\move({nx:.2f},{min(1050,ny+30):.2f},{nx:.2f},{ny:.2f},0,900)\\fad(900,1000)\\fs{size:.1f}}}{ass_text(name)}\n'
    if not parts['name']:
        events = ''
    message = r'\N'.join(ass_text(line) for line in (layout or {}).get('message', 'සිංහල උපසිරැසි සමග චිත්‍රපට/රූපවාහිනී කතාමාලා\nonline නැරඹීමට පිවිසෙන්න').split('\n'))
    if parts['message']:
        events += f'Dialogue: 0,{ass_time(start + message_delay)},{ass_time(start + duration)},Message,,0,0,0,,{{\\pos({mx:.2f},{my:.2f})\\fad(1000,1000)}}{message}\n'
    Path('intro.ass').write_text(styles + events, encoding='utf-8')


def video_dimensions(stream, height):
    width, source_height = int(stream['width']), int(stream['height'])
    # Apply display rotation and sample aspect ratio before creating a square-pixel canvas.
    rotation = next((int(side.get('rotation', 0)) for side in stream.get('side_data_list', []) if 'rotation' in side), 0)
    sar = stream.get('sample_aspect_ratio', '1:1')
    if re.fullmatch(r'\d+:\d+', sar) and int(sar.split(':')[1]):
        a, b = map(int, sar.split(':'))
        width = round(width * a / b)
    if rotation % 180:
        width, source_height = source_height, width
    if height is not None:
        canvas={360:(640,360),480:(854,480),720:(1280,720),1080:(1920,1080),1440:(2560,1440),2160:(3840,2160)}
        if height not in canvas:raise ValueError('Unsupported standard resolution')
        return canvas[height]
    return max(2,width//2*2),max(2,source_height//2*2)

def fitted_dimensions(stream,width,height):
    source_width,source_height=video_dimensions(stream,None)
    factor=min(width/source_width,height/source_height)
    return max(2,int(source_width*factor)//2*2),max(2,int(source_height*factor)//2*2)


def logo_dimensions(width, height, logo_size, scale=100):
    lw, lh = logo_size
    # Resize once from original pixels using the same final display bounds.
    factor = number(scale, 25, 200, "logo size") / 100
    ratio = min(1, width * 920 / 1920 * factor / lw, height * 180 / 1080 * factor / lh, width * 0.95 / lw, height * 0.95 / lh)
    return max(1, round(lw * ratio)), max(1, round(lh * ratio))


def filter_graph(width, height, options, logo_size=None):
    content_width,content_height=options.get('content_size',(width,height))
    base = f'[0:v:0]scale={content_width}:{content_height}:flags=bicubic,setsar=1,'
    if (content_width,content_height)!=(width,height):
        base += f'pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,'
    if options.get('subtitle_enabled', True):
        base += 'ass=subtitles.ass:fontsdir=fonts:shaping=complex,'
    if options.get('watermark_enabled',True):
        base += 'drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:text=CLIXARENA:fontsize=h/44:fontcolor=white@0.55:x=w/136:y=h-th-h/38'
    else:
        base=base.rstrip(',')
    text = any(options.get('parts', {'name': bool(logo_size), 'message': bool(logo_size)})[key] for key in ('name', 'message'))
    if not logo_size:
        return base + (',ass=intro.ass:fontsdir=fonts:shaping=complex' if text else '') + '[video]'
    start, duration = options['start'], options['duration']
    fade_in = min(1, duration / 4)
    fade_out = start + duration - 1
    lp = options.get('layout', {}).get('positions', {}).get('logo', {'x':50,'y':410/1080*100})
    graph = base + '[base];'
    graph += ('[1:v]format=rgba,'
              f'fade=t=in:st={start}:d={fade_in}:alpha=1,fade=t=out:st={fade_out}:d=1:alpha=1[logo];'
              f"[base][logo]overlay=x=W*{lp['x']/100}-w/2:y=H*{lp['y']/100}-h/2:enable='between(t,{start},{start+duration})':eof_action=pass:repeatlast=0[art];"
              + ('[art]ass=intro.ass:fontsdir=fonts:shaping=complex[video]' if text else '[art]null[video]'))
    return graph


def encode_progress(args, duration, timeout=16000, output="clixarena.mp4"):
    started = time.monotonic()
    last_report = 0
    with open('encode-private.log', 'w', encoding='utf-8') as errors:
        process = subprocess.Popen(args + ['-progress', 'pipe:1', '-nostats', str(output)],
                                   stdout=subprocess.PIPE, stderr=errors, text=True)
        # A watchdog bounds even an encoder that stops emitting progress.
        import threading
        timer = threading.Timer(timeout, process.kill)
        timer.start()
        try:
            for line in process.stdout:
                if not line.startswith('out_time_us='):
                    continue
                try:
                    seconds = max(0, int(line.partition('=')[2]) / 1000000)
                except ValueError:
                    continue
                elapsed = time.monotonic() - started
                if elapsed - last_report < 20:
                    continue
                last_report = elapsed
                percent = min(99.9, seconds / duration * 100)
                remaining = max(0, (duration - seconds) / seconds * elapsed) if seconds else 0
                print(f'Encoding {percent:.1f}% | elapsed {elapsed/60:.1f} min | estimated remaining {remaining/60:.1f} min', flush=True)
            if process.wait():
                print('::error::Encoder failed or exceeded its time limit. Private tool output is suppressed; check codec support, input integrity and disk space.')
                raise ValueError('Encoding failed')
            print('Encoding 100% complete', flush=True)
        finally:
            timer.cancel()
            if process.poll() is None:
                process.kill()
                process.wait()
            process.stdout.close()
